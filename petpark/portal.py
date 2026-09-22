"""玩家门户：注册 / 登录 / 绑定宠物后，按群聊+用户ID查看宠物、背包、财产信息。

安全设计：
- 密码使用 PBKDF2-HMAC-SHA256 + 随机 salt 存储
- 会话采用 HMAC-SHA256 签名 Cookie，HttpOnly + SameSite=Strict
- POST 接口校验 CSRF token
- 登录/注册/绑定接口有简单的 IP+QQ 级速率限制
- 使用道具/卡密兑换等写操作复用主插件的群聊指令实现，效果与群聊一致
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
import smtplib
import time
from email.header import Header
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from aiohttp import web
from astrbot.api import logger

from . import data, images
from .pet import battle_power

_COOKIE_NAME = "pp_portal"
_CSRF_HEADER = "X-CSRF-Token"
_LOGIN_COOLDOWN = 900  # 15 分钟
_LOGIN_MAX_ATTEMPTS = 5

# 邮箱验证码发送端配置
_EMAIL_SMTP = {
    "enabled": True,
    "smtp_host": "smtp.qq.com",
    "smtp_port": 465,
    "use_ssl": True,
    "username": "1808344406@qq.com",
    "auth_code": "carwvuyvfjntfbeg",
    "from_email": "1808344406@qq.com",
}
_EMAIL_CODE_TTL = 60  # 验证码有效期（秒）
_EMAIL_SEND_INTERVAL = 60  # 同一邮箱重发间隔（秒）
_EMAIL_CODE_MAX_TRIES = 5
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_EMAIL_PURPOSE_LABEL = {"register": "注册", "login": "登录", "bind": "绑定邮箱", "chpwd": "修改密码"}


class PlayerPortal:
    def __init__(self, store, broadcast_callback=None, command_gateway=None):
        self.store = store
        self.broadcast_callback = broadcast_callback
        self.command_gateway = command_gateway
        self._attempts: dict[str, dict] = {}
        self._email_codes: dict[str, dict] = {}
        self._email_send_at: dict[str, float] = {}

    @staticmethod
    def _normalize_custom_image(file_data: bytes, ext: str) -> tuple[bytes, str]:
        """上传定制图规范化：QQ 端不兼容 webp —— 动态 webp 转动态 GIF（保留动画），
        静态 webp 转 PNG；转换失败回退原样。其余格式原样返回。"""
        if ext != ".webp":
            return file_data, ext
        try:
            import io
            from PIL import Image
            img = Image.open(io.BytesIO(file_data))
            if getattr(img, "is_animated", False):
                frames, durations = [], []
                for i in range(getattr(img, "n_frames", 1)):
                    img.seek(i)
                    durations.append(int(img.info.get("duration") or 80))
                    frames.append(img.convert("RGBA").convert("P", palette=Image.ADAPTIVE))
                out = io.BytesIO()
                try:
                    frames[0].save(out, format="GIF", save_all=True,
                                   append_images=frames[1:], duration=durations, loop=0)
                except TypeError:
                    out = io.BytesIO()
                    avg = max(1, sum(durations) // len(durations))
                    frames[0].save(out, format="GIF", save_all=True,
                                   append_images=frames[1:], duration=avg, loop=0)
                return out.getvalue(), ".gif"
            out = io.BytesIO()
            img.save(out, format="PNG")
            return out.getvalue(), ".png"
        except Exception:
            return file_data, ext

    # --------------------------- 工具：密码与会话 ---------------------------
    @staticmethod
    def _hash_password(password: str, salt: str) -> str:
        """PBKDF2-HMAC-SHA256，10 万次迭代。"""
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000
        )
        return dk.hex()

    @staticmethod
    def _make_salt() -> str:
        return secrets.token_hex(16)

    def _sign(self, payload: dict) -> str:
        secret = self.store.portal_secret().encode("utf-8")
        body = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).decode("ascii").rstrip("=")
        sig = hmac.new(secret, body.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
        return f"{body}.{sig}"

    def _unsign(self, token: str) -> Optional[dict]:
        if not token or "." not in token:
            return None
        body, sig = token.split(".", 1)
        expected = hmac.new(
            self.store.portal_secret().encode("utf-8"), body.encode("utf-8"), hashlib.sha256
        ).hexdigest()[:32]
        if not secrets.compare_digest(sig, expected):
            return None
        try:
            pad = 4 - len(body) % 4
            if pad != 4:
                body += "=" * pad
            return json.loads(base64.urlsafe_b64decode(body.encode("utf-8")))
        except Exception:
            return None

    def _set_session(self, response: web.Response, account_id: str, csrf: str) -> None:
        max_age = 7 * 86400  # 7 天
        payload = {"aid": account_id, "csrf": csrf, "exp": int(time.time()) + max_age}
        response.set_cookie(
            _COOKIE_NAME,
            self._sign(payload),
            max_age=max_age,
            httponly=True,
            samesite="Strict",
            secure=False,  # 若站点走 HTTPS，建议改为 True
        )

    def _clear_session(self, response: web.Response) -> None:
        response.del_cookie(_COOKIE_NAME)

    def _current_session(self, request: web.Request) -> Optional[dict]:
        token = request.cookies.get(_COOKIE_NAME)
        sess = self._unsign(token) if token else None
        if not sess or sess.get("exp", 0) < int(time.time()):
            return None
        return sess

    def _require_session(self, request: web.Request) -> dict:
        sess = self._current_session(request)
        if not sess:
            raise web.HTTPUnauthorized(text="未登录")
        account = self.store.get_account(sess.get("aid"))
        if not account:
            raise web.HTTPUnauthorized(text="账号不存在")
        return sess

    def _check_csrf(self, request: web.Request) -> None:
        sess = self._current_session(request)
        if not sess:
            raise web.HTTPForbidden(text="CSRF 校验失败")
        token = request.headers.get(_CSRF_HEADER, "")
        if not secrets.compare_digest(token, sess.get("csrf", "")):
            raise web.HTTPForbidden(text="CSRF 校验失败")

    def _check_rate(self, key: str) -> tuple[bool, str]:
        now = int(time.time())
        rec = self._attempts.get(key, {"count": 0, "reset": now})
        if rec["reset"] < now:
            rec = {"count": 0, "reset": now + _LOGIN_COOLDOWN}
        if rec["count"] >= _LOGIN_MAX_ATTEMPTS:
            remain = max(1, (rec["reset"] - now) // 60)
            return False, f"尝试次数过多，请 {remain} 分钟后再试"
        rec["count"] += 1
        self._attempts[key] = rec
        return True, ""

    def _reset_rate(self, key: str) -> None:
        self._attempts.pop(key, None)

    # --------------------------- 工具：邮箱验证码 ---------------------------
    @staticmethod
    def _normalize_email(email: str) -> str:
        return str(email or "").strip().lower()

    def _send_email_sync(self, to_email: str, code: str, purpose_label: str) -> None:
        cfg = _EMAIL_SMTP
        msg = MIMEText(
            f"您的{purpose_label}验证码为：{code}\n"
            f"验证码 {_EMAIL_CODE_TTL} 秒内有效，请尽快完成验证。\n"
            "若非本人操作，请忽略本邮件。",
            "plain",
            "utf-8",
        )
        msg["Subject"] = Header(f"灵契仙途 · {purpose_label}验证码", "utf-8")
        msg["From"] = cfg["from_email"]
        msg["To"] = to_email
        if cfg.get("use_ssl"):
            server = smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"], timeout=15)
        else:
            server = smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=15)
            server.starttls()
        try:
            server.login(cfg["username"], cfg["auth_code"])
            server.sendmail(cfg["from_email"], [to_email], msg.as_string())
        finally:
            try:
                server.quit()
            except Exception:
                pass

    def _verify_email_code(self, purpose: str, email: str, code: str) -> tuple[bool, str]:
        email = self._normalize_email(email)
        code = str(code or "").strip()
        if not code:
            return False, "请输入邮箱验证码"
        key = f"{purpose}:{email}"
        rec = self._email_codes.get(key)
        now = int(time.time())
        if not rec or rec.get("exp", 0) < now:
            self._email_codes.pop(key, None)
            return False, "验证码已过期，请重新获取"
        if rec.get("tries", 0) >= _EMAIL_CODE_MAX_TRIES:
            self._email_codes.pop(key, None)
            return False, "验证码错误次数过多，请重新获取"
        if not secrets.compare_digest(str(rec.get("code", "")), code):
            rec["tries"] = rec.get("tries", 0) + 1
            return False, "验证码错误"
        self._email_codes.pop(key, None)
        return True, ""

    @staticmethod
    def _mask_email(email: str) -> str:
        email = str(email or "")
        if "@" not in email:
            return email
        name, domain = email.split("@", 1)
        if len(name) <= 2:
            masked = name[:1] + "***"
        else:
            masked = name[:2] + "***" + name[-1:]
        return f"{masked}@{domain}"

    async def _send_code_to(self, email: str, purpose: str) -> tuple[bool, str]:
        """发送验证码到指定邮箱（含重发间隔限制）。"""
        now = time.time()
        last = self._email_send_at.get(email, 0)
        if now - last < _EMAIL_SEND_INTERVAL:
            remain = int(_EMAIL_SEND_INTERVAL - (now - last))
            return False, f"发送过于频繁，请 {remain} 秒后再试"
        code = f"{secrets.randbelow(1000000):06d}"
        try:
            await asyncio.to_thread(
                self._send_email_sync, email, code, _EMAIL_PURPOSE_LABEL[purpose]
            )
        except Exception as e:
            logger.warning(f"[petpark] 邮件发送失败 {email}: {e}")
            return False, "邮件发送失败，请稍后再试"
        self._email_send_at[email] = now
        self._email_codes[f"{purpose}:{email}"] = {
            "code": code,
            "exp": int(now) + _EMAIL_CODE_TTL,
            "tries": 0,
        }
        return True, "验证码已发送，请查收邮箱"

    async def _api_send_email_code(self, request: web.Request) -> web.Response:
        if not _EMAIL_SMTP.get("enabled"):
            return web.json_response({"ok": False, "msg": "邮箱验证功能未启用"})
        body = await request.json()
        email = self._normalize_email(body.get("email", ""))
        purpose = str(body.get("purpose", "")).strip()
        if purpose not in ("register", "login", "bind"):
            return web.json_response({"ok": False, "msg": "参数不正确"})
        if not _EMAIL_RE.match(email):
            return web.json_response({"ok": False, "msg": "邮箱格式不正确"})
        if purpose in ("register", "bind"):
            if self.store.get_account_by_email(email):
                return web.json_response({"ok": False, "msg": "该邮箱已被注册"})
        else:  # login
            if not self.store.get_account_by_email(email):
                return web.json_response({"ok": False, "msg": "该邮箱未绑定任何账号"})
        ok, msg = await self._send_code_to(email, purpose)
        return web.json_response({"ok": ok, "msg": msg, "ttl": _EMAIL_CODE_TTL})

    async def _api_send_chpwd_code(self, request: web.Request) -> web.Response:
        """向当前登录账号的绑定邮箱发送修改密码验证码。"""
        self._check_csrf(request)
        sess = self._require_session(request)
        if not _EMAIL_SMTP.get("enabled"):
            return web.json_response({"ok": False, "msg": "邮箱验证功能未启用"})
        account = self.store.get_account(sess.get("aid"))
        if not account:
            return web.json_response({"ok": False, "msg": "账号不存在"})
        email = self._normalize_email(account.get("email", ""))
        if not email:
            return web.json_response({"ok": False, "msg": "该账号尚未绑定邮箱，请重新登录完成绑定"})
        ok, msg = await self._send_code_to(email, "chpwd")
        return web.json_response({
            "ok": ok,
            "msg": msg,
            "email": self._mask_email(email),
            "ttl": _EMAIL_CODE_TTL,
        })

    # --------------------------- 工具：数据格式化 ---------------------------
    @staticmethod
    def _resolve_player_pet(player: dict | None, pet_index: int = 0) -> dict:
        """多宠物系统：从 pets[pet_index] 解析指定宠物，兼容旧单宠物。"""
        if not player:
            return {}
        pets = player.get("pets", [])
        if pets and 0 <= pet_index < len(pets):
            return pets[pet_index]
        # Fallback: 运行时引用 / 旧单宠物数据
        return player.get("pet") or {}

    def _format_pet(self, player: dict, group_id: str, qq: str, pet_index: int = 0) -> dict:
        pet = (self._resolve_player_pet(player, pet_index) or {}).copy()
        if not pet:
            return {"exists": False}
        species = pet.get("species")
        level = pet.get("level", 1)
        custom_image = pet.get("custom_image")
        if custom_image:
            pet["image_url"] = f"/custom_images/{custom_image}"
        else:
            pet["image_url"] = images.pet_image_url(species)
        pet["battle_power"] = battle_power(pet)
        if data.STAGES.index(pet.get("stage", "")) >= data.STAGES.index("飞升"):
            pet["ascended"] = True
            pet["xianyuan"] = pet.get("xianyuan", 0)
            pet["exp_to_next"] = data.ascend_xianyuan_to_next(level)
        else:
            pet["ascended"] = False
            pet["xianyuan"] = 0
            pet["exp_to_next"] = data.exp_to_next(level)
        pet["element_cn"] = pet.get("element", "未知")
        pet["quality"] = pet.get("quality", "普通")
        pet["stage"] = pet.get("stage", "幼年期")
        pet["custom"] = bool(pet.get("custom"))
        pet["custom_species_name"] = pet.get("custom_species_name")
        pet["tags"] = pet.get("tags", [])
        # 隐藏内部对象，避免前端误用
        pet.pop("skills", None)
        pet.pop("rune", None)
        return {"exists": True, **pet}

    def _slot_role_summary(self, player: dict, group_id: str = "", qq: str = "") -> dict:
        """槽位摘要：修士(adventure) + 坐骑(mounts)，供绑定与门户展示共用。"""
        adv = player.get("adventure") or {}
        adventure = None
        if adv.get("name"):
            try:
                from .adventure.power import compute_unified_power, power_breakdown
                from .adventure.combat import hero_sheet, roll_hp
                from .adventure.portal_loadout import loadout_summary
                from .adventure import content as advc
                s = hero_sheet(adv, player)
                _realm = int(adv.get("realm") or 0)
                _st, _stmax = int(adv.get("stamina", 100) or 100), int(adv.get("stamina_max", 100) or 100)
                heaven = adv.get("heaven", 0)
                heaven_meta = advc.HEAVENS[int(heaven or 0)] if 0 <= int(heaven or 0) < len(advc.HEAVENS) else None
                # 神通：adv.tactics 列表 + 效果描述（tactic_effect）
                _tacs = adv.get("tactics", []) or []
                tactics = [(t, advc.tactic_effect(t)) for t in _tacs]
                # 道侣：结契灵宠（第 0 只宠物）的姻缘状态/好感/对象
                pets = player.get("pets") or []
                _p0 = pets[0] if pets else {}
                partner = {
                    "married": _p0.get("love_state") == "已婚",
                    "love_state": _p0.get("love_state") or "单身",
                    "love_target": _p0.get("love_target") or "",
                    "favor": int(_p0.get("favor", 0) or 0),
                    "pet_name": _p0.get("nickname") or "",
                }
                # 战力构成（与修士图 card.html 同源 power_breakdown）
                bd = power_breakdown(player, player.get("qq", "")) or {}
                pb = {
                    "hero": int(bd.get("hero", 0) or 0),
                    "pet_name": bd.get("pet_name") or "引路灵蝶",
                    "pet_contrib": int(bd.get("pet_contrib", 0) or 0),
                    "pet_part": int(bd.get("pet_part", 0) or 0),
                    "pet_power": int(bd.get("pet_power", 0) or 0),
                    "mount_contrib": int(bd.get("mount_contrib", 0) or 0),
                    "mount_part": int(bd.get("mount_part", 0) or 0),
                    "mount_power": int(bd.get("mount_power", 0) or 0),
                    "pet_ratio": float(bd.get("pet_ratio") or 0),  # 0 是合法值（Lv1），不能回退默认
                    "pet_ratio_max": float(bd.get("pet_ratio_max") or 0.15),
                    "mount_ratio": float(bd.get("mount_ratio", 0.10) or 0.10),
                    "partner": float(bd.get("partner", 1.0) or 1.0),
                    "heaven_margin": float(bd.get("heaven_margin", 1.0) or 1.0),
                    "base": float(bd.get("base", 0) or 0),
                    "total": int(bd.get("total", 0) or 0),
                }
                # 当前气血：惰性回算（存活每分钟回复上限1%，陨落静养30分钟自愈30%）
                import time as _time
                cur_hp = roll_hp(adv, player, _time.time())
                if not isinstance(cur_hp, int) or cur_hp <= 0:
                    cur_hp = s.get("hp", 0)
                adventure = {
                    **loadout_summary(adv),
                    "name": adv.get("name"),
                    "profession": adv.get("profession"),
                    "gender": adv.get("gender", "男"),
                    "realm": advc.REALMS[_realm] if 0 <= _realm < len(advc.REALMS) else "",
                    "realm_idx": _realm,
                    "level": adv.get("level", 1),
                    "heaven_idx": int(heaven or 0),
                    "heaven": (heaven_meta or {}).get("name") or f"{heaven}阶",
                    "spirit_root": adv.get("spirit_root"),
                    "element": advc.element_line(advc.hero_element(adv)) if hasattr(advc, "hero_element") else "",
                    "tactics": tactics,
                    "stamina": _st,
                    "stamina_max": _stmax,
                    "stamina_buff_until": int(adv.get("stamina_buff_until", 0) or 0),
                    "insight": int(adv.get("insight", 0) or 0),
                    "wudao": int(s.get("wudao", adv.get("wudao", 0)) or 0),
                    "gengu": int(s.get("gengu", adv.get("gengu", 0)) or 0),
                    "power": compute_unified_power(player, player.get("qq", "")),
                    "hp": cur_hp,
                    "hp_max": s.get("hp", 0),
                    "hp_dead": adv.get("hp_dead") is True,
                    "atk": s.get("atk", 0),
                    "defense": s.get("def", 0),
                    "speed": s.get("speed", 0),
                    "heaven_multiplier": (heaven_meta or {}).get("enemy", 1.0),
                    "partner": partner,
                    "breakdown": pb,
                }
            except Exception as e:
                logger.exception(f"[petpark] 修士档案汇总异常：{e}")
                adventure = {"name": adv.get("name"), "profession": adv.get("profession"),
                             "level": adv.get("level", 1)}
        mounts = []
        for mname, inst in (player.get("mounts") or {}).items():
            cfg = data.MOUNTS.get(mname) or {}
            entry = {
                "name": mname,
                "level": inst.get("level", 1),
                "power": inst.get("power", cfg.get("base_power", 0)),
                "custom": bool(inst.get("custom")),
                "custom_spec": bool(inst.get("custom_spec")),
                "custom_image": inst.get("custom_image"),
                "stars": inst.get("stars", cfg.get("stars", 0)),
                "remaining": self.store.remaining_custom_changes(player, "mount_image"),
            }
            mounts.append(entry)
        mc = {"slots": self.store.mount_custom_slots(player), "pending": [], "rejected": [], "img_pending": []}
        if group_id and qq:
            mreviews = self.store.get_custom_reviews(group_id, qq, kind="mount")
            mc["pending"] = [r.get("mount_name") for r in mreviews if r.get("status") == "pending"]
            mc["img_pending"] = [
                r.get("mount_name") for r in self.store.get_custom_reviews(
                    group_id, qq, kind="mount_image", status="pending")
            ]
            rej = [r for r in mreviews if r.get("status") == "rejected"]
            if rej:
                last = rej[-1]
                mc["rejected"] = [{"name": last.get("mount_name"), "reason": last.get("reason") or ""}]
        return {"adventure": adventure, "mounts": mounts, "mount_custom": mc}

    def _player_summary(self, group_id: str, qq: str, pet_index: int = 0) -> dict:
        key = self.store.make_key(group_id, qq)
        player = self.store._data["players"].get(key)
        if not player:
            raise web.HTTPNotFound(text="未找到该角色")
        pending = self.store.get_pet_custom_reviews(group_id, qq, status="pending")
        rejected = self.store.get_pet_custom_reviews(group_id, qq, status="rejected")
        # 只返回最近一条拒绝原因
        last_rejected = sorted(rejected, key=lambda x: x.get("created_at", 0), reverse=True)[:1]
        rp = self._resolve_player_pet(player, pet_index)
        role = self._slot_role_summary(player, group_id, qq)
        return {
            "group_id": group_id,
            "qq": qq,
            "pet_index": pet_index,
            "coin": player.get("coin", 0),
            "jifen": player.get("jifen", 0),
            "diamond": player.get("diamond", 0),
            "bag": dict(player.get("bag", {})),
            "abyss": dict(self.store.abyss_state(player)),
            "stats": dict(player.get("stats", {})),
            "pet": self._format_pet(player, group_id, qq, pet_index),
            "adventure": role["adventure"],
            "mounts": role["mounts"],
            "mount_custom": role["mount_custom"],
            "cooldowns": self._cooldown_list(player, pet_index),
            "skills": list(rp.get("skills", [])) if rp else [],
            "artifact": rp.get("artifact") if rp else None,
            "artifact_names": list(data.ARTIFACTS.keys()),
            "skill_names": list(data.SKILLS.keys()),
            "custom_pending": pending,
            "custom_rejected": last_rejected,
            "custom_remaining": {
                "image": self.store.remaining_custom_changes(player, "image") if rp else 0,
                "species_name": self.store.remaining_custom_changes(player, "species_name") if rp else 0,
            },
            # 自动助手：宠物级配置 + 玩家级剩余次数（门户开关与群内指令共用同一份数据）
            "assistant": self._assistant_summary(player, rp),
        }

    def _cooldown_list(self, player: dict, pet_index: int = 0) -> list:
        """汇总玩家所有活动冷却：日常活动 + 固定玩法 + 限时活动。"""
        now = int(time.time())
        entries = []
        married = (self._resolve_player_pet(player, pet_index) or {}).get("love_state") == "已婚"
        for action in data.DAILY_ACTIONS:
            if action == "双修" and not married:
                continue
            if action == "修炼" and married:
                continue
            entries.append({
                "name": action,
                "remaining": self.store.cooldown_remaining(player, f"日常:{action}"),
            })
        fixed = [
            ("砸蛋", "砸蛋"),
            ("副本", "副本"),
            ("fantasy_treasure", "幻境寻宝"),
            ("ascend_dungeon", "挑战神仙"),
            ("深渊秘境", "深渊秘境"),
        ]
        for key, label in fixed:
            entries.append({
                "name": label,
                "remaining": self.store.cooldown_remaining(player, key),
            })
        known = {f"日常:{a}" for a in data.DAILY_ACTIONS} | {k for k, _ in fixed}
        for key, end in (player.get("cooldowns") or {}).items():
            if key in known:
                continue
            remaining = max(0, int(end) - now)
            if remaining <= 0:
                continue
            label = key.split(":")[-1] if ":" in key else key
            entries.append({"name": label, "remaining": remaining})
        return entries

    def _owned_player(self, sess: dict, group_id: str, qq: str) -> dict:
        if not group_id or not qq:
            raise web.HTTPBadRequest(text="参数不完整")
        owner = self.store.account_for_pet(group_id, qq)
        if owner != sess.get("aid"):
            raise web.HTTPForbidden(text="你没有绑定该宠物")
        key = self.store.make_key(group_id, qq)
        player = self.store._data["players"].get(key)
        if not player:
            raise web.HTTPNotFound(text="未找到该宠物")
        return player

    # --------------------------- 路由 ---------------------------
    def setup(self, app: web.Application) -> None:
        app.router.add_get("/", self._home_page)
        app.router.add_get("/agreement", self._agreement_page)
        app.router.add_get("/api/portal/home", self._api_home)
        app.router.add_get("/portal", self._portal_page)
        app.router.add_post("/api/portal/register", self._api_register)
        app.router.add_post("/api/portal/login", self._api_login)
        app.router.add_post("/api/portal/send_email_code", self._api_send_email_code)
        app.router.add_post("/api/portal/login_email", self._api_login_email)
        app.router.add_post("/api/portal/bind_email", self._api_bind_email)
        app.router.add_post("/api/portal/logout", self._api_logout)
        app.router.add_get("/api/portal/me", self._api_me)
        app.router.add_post("/api/portal/bind/query", self._api_bind_query)
        app.router.add_post("/api/portal/bind/auto", self._api_bind_auto)
        app.router.add_post("/api/portal/bind/reclaim", self._api_bind_reclaim)
        app.router.add_post("/api/portal/bind", self._api_bind)
        app.router.add_get("/api/portal/pet", self._api_pet)
        app.router.add_post("/api/portal/assistant", self._api_assistant)
        app.router.add_post("/api/portal/custom_redeem", self._api_custom_redeem)
        app.router.add_post("/api/portal/custom_submit", self._api_custom_submit)
        app.router.add_post("/api/portal/mount_custom_redeem", self._api_mount_custom_redeem)
        app.router.add_post("/api/portal/mount_custom_submit", self._api_mount_custom_submit)
        app.router.add_post("/api/portal/mount_image_submit", self._api_mount_image_submit)
        app.router.add_post("/api/portal/use_item", self._api_use_item)
        app.router.add_get("/api/portal/item_info", self._api_item_info)
        app.router.add_post("/api/portal/redeem", self._api_redeem)
        app.router.add_post("/api/portal/change_password", self._api_change_password)
        app.router.add_post("/api/portal/send_chpwd_code", self._api_send_chpwd_code)
        app.router.add_post("/api/portal/pet_action", self._api_pet_action)
        app.router.add_get("/feedback", self._feedback_page)
        app.router.add_post("/api/portal/feedback", self._api_feedback_submit)
        app.router.add_get("/api/portal/feedback", self._api_feedback_list)
        app.router.add_post("/api/portal/feedback/delete", self._api_feedback_delete)
        app.router.add_get("/chat", self._chat_page)
        app.router.add_post("/api/portal/chat", self._api_chat_send)
        app.router.add_get("/api/app/version", self._api_app_version)
        app.router.add_get("/app_download/latest.apk", self._app_download)
        app.router.add_static(
            "/feedback_images",
            path=self.store.feedback_images_dir,
            name="feedback_images",
        )
        app.router.add_static(
            "/custom_images",
            path=self.store.custom_images_dir,
            name="custom_images",
        )
        app.router.add_static(
            "/webstatic",
            path=Path(__file__).parent / "webstatic",
            name="webstatic",
        )
        app.router.add_static(
            "/cultivator_assets", path=Path(__file__).parent / "assets" / "cultivator",
            name="cultivator_assets", show_index=False,
        )

    async def _portal_page(self, request: web.Request) -> web.Response:
        sess = self._current_session(request)
        if not sess:
            raise web.HTTPFound("/")
        csrf = sess.get("csrf")
        html = _PORTAL_HTML.replace("{{CSRF_TOKEN}}", csrf)
        response = web.Response(text=html, content_type="text/html")
        # 刷新 Cookie 过期时间
        self._set_session(response, sess["aid"], csrf)
        return response

    async def _feedback_page(self, request: web.Request) -> web.Response:
        sess = self._current_session(request)
        if not sess:
            raise web.HTTPFound("/")
        csrf = sess.get("csrf")
        html = _FEEDBACK_HTML.replace("{{CSRF_TOKEN}}", csrf)
        response = web.Response(text=html, content_type="text/html")
        self._set_session(response, sess["aid"], csrf)
        return response

    async def _home_page(self, request: web.Request) -> web.Response:
        sess = self._current_session(request)
        html = _HOME_HTML.replace("{{CSRF_TOKEN}}", sess.get("csrf", "") if sess else "")
        return web.Response(text=html, content_type="text/html")

    async def _agreement_page(self, request: web.Request) -> web.Response:
        return web.Response(text=_AGREEMENT_HTML, content_type="text/html")

    @staticmethod
    def _mask_qq(qq: str) -> str:
        q = str(qq or "")
        if len(q) <= 5:
            return q
        return f"{q[:3]}****{q[-2:]}"

    async def _api_home(self, request: web.Request) -> web.Response:
        """首页公开统计：玩家/授权群/各大榜单，30 秒缓存。"""
        now = time.time()
        cache = getattr(self, "_home_cache", None)
        if cache and now - cache[0] < 30:
            return web.json_response(cache[1])
        players = self.store.all_players()
        pet_entries = []
        for pl in players.values():
            pet = pl.get("pet")
            if not pet:
                continue
            pet_entries.append({
                "nickname": str(pet.get("nickname", "")),
                "level": pet.get("level", 1),
                "stage": pet.get("stage", ""),
                "quality": pet.get("quality", ""),
                "power": int(battle_power(pet)),
            })
        pet_entries.sort(key=lambda x: x["power"], reverse=True)
        # 仙途战力榜：修士综合战力（修士+灵宠+坐骑+道侣，与群内「仙途战力榜」同口径）
        cultivators = []
        compute_unified_power = None
        advc = None
        try:
            from .adventure.power import compute_unified_power
            from .adventure import content as advc
        except Exception:
            compute_unified_power = None
        if compute_unified_power is not None:
            for pl in players.values():
                adv = pl.get("adventure") or {}
                if not adv.get("name"):
                    continue
                try:
                    power = int(compute_unified_power(pl, pl.get("qq", "")))
                except Exception:
                    continue
                realm_idx = int(adv.get("realm") or 0)
                realm = (advc.REALMS[realm_idx] if advc and 0 <= realm_idx < len(advc.REALMS) else "")
                cultivators.append({
                    "name": str(adv.get("name", "")),
                    "level": int(adv.get("level", 1) or 1),
                    "profession": adv.get("profession") or "",
                    "realm": realm,
                    "power": power,
                })
            cultivators.sort(key=lambda x: x["power"], reverse=True)
        groups = self.store._data.get("groups", {})
        auth_groups = sum(
            1 for g in groups.values()
            if int(g.get("auth_until", 0) or 0) > int(now)
        )
        tomb = self.store._data.get("tomb_players", {})
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        tomb_rank, tomb_today, tomb_yesterday = [], [], []
        for qq, st in tomb.items():
            masked = self._mask_qq(qq)
            mingbi = int(st.get("mingbi", 0) or 0)
            if mingbi > 0:
                tomb_rank.append({"qq": masked, "value": mingbi})
            gains = st.get("daily_gains", {}) or {}
            g_today = int(gains.get(today, 0) or 0)
            if g_today > 0:
                tomb_today.append({"qq": masked, "value": g_today})
            g_yst = int(gains.get(yesterday, 0) or 0)
            if g_yst > 0:
                tomb_yesterday.append({"qq": masked, "value": g_yst})
        for lst in (tomb_rank, tomb_today, tomb_yesterday):
            lst.sort(key=lambda x: x["value"], reverse=True)
        payload = {
            "ok": True,
            "stats": {
                "players": len(players),
                "auth_groups": auth_groups,
                "pets": len(pet_entries),
                "tomb_players": len(tomb),
            },
            "pet_rank": pet_entries[:10],
            "cultivator_rank": cultivators[:10],
            "tomb_rank": tomb_rank[:10],
            "tomb_today": tomb_today[:10],
            "tomb_yesterday": tomb_yesterday[:10],
            "date_today": today,
            "date_yesterday": yesterday,
        }
        self._home_cache = (now, payload)
        return web.json_response(payload)

    async def _api_register(self, request: web.Request) -> web.Response:
        body = await request.json()
        qq = str(body.get("qq", "")).strip()
        password = str(body.get("password", ""))
        email = self._normalize_email(body.get("email", ""))
        code = str(body.get("code", "")).strip()
        ip = request.remote or "unknown"
        ok, msg = self._check_rate(f"{ip}:{qq}")
        if not ok:
            return web.json_response({"ok": False, "msg": msg})
        if not qq.isdigit() or len(qq) < 5 or len(qq) > 12:
            return web.json_response({"ok": False, "msg": "QQ 号格式不正确"})
        if len(password) < 6:
            return web.json_response({"ok": False, "msg": "密码长度至少 6 位"})
        if not _EMAIL_RE.match(email):
            return web.json_response({"ok": False, "msg": "邮箱格式不正确"})
        if self.store.get_account_by_qq(qq):
            return web.json_response({"ok": False, "msg": "该 QQ 号已注册"})
        if self.store.get_account_by_email(email):
            return web.json_response({"ok": False, "msg": "该邮箱已被注册"})
        ok, msg = self._verify_email_code("register", email, code)
        if not ok:
            return web.json_response({"ok": False, "msg": msg})
        salt = self._make_salt()
        phash = self._hash_password(password, salt)
        account = self.store.create_account(qq, phash, salt, email=email)
        await self.store.save()
        self._reset_rate(f"{ip}:{qq}")
        return web.json_response({"ok": True, "msg": "注册成功", "account_id": account["id"]})

    async def _api_login(self, request: web.Request) -> web.Response:
        body = await request.json()
        qq = str(body.get("qq", "")).strip()
        password = str(body.get("password", ""))
        ip = request.remote or "unknown"
        ok, msg = self._check_rate(f"{ip}:{qq}")
        if not ok:
            return web.json_response({"ok": False, "msg": msg})
        account = self.store.get_account_by_qq(qq)
        if not account:
            return web.json_response({"ok": False, "msg": "账号或密码错误"})
        if account["password_hash"] != self._hash_password(password, account["salt"]):
            return web.json_response({"ok": False, "msg": "账号或密码错误"})
        self._reset_rate(f"{ip}:{qq}")
        if not account.get("email"):
            return web.json_response({
                "ok": False,
                "need_bind_email": True,
                "msg": "该账号尚未绑定邮箱，请先绑定邮箱后再登录",
            })
        account["last_login"] = int(time.time())
        await self.store.save()
        self._reset_rate(f"{ip}:{qq}")
        csrf = secrets.token_urlsafe(24)
        resp = web.json_response({"ok": True, "msg": "登录成功"})
        self._set_session(resp, account["id"], csrf)
        return resp

    async def _api_login_email(self, request: web.Request) -> web.Response:
        body = await request.json()
        email = self._normalize_email(body.get("email", ""))
        code = str(body.get("code", "")).strip()
        ip = request.remote or "unknown"
        ok, msg = self._check_rate(f"{ip}:email:{email}")
        if not ok:
            return web.json_response({"ok": False, "msg": msg})
        if not _EMAIL_RE.match(email):
            return web.json_response({"ok": False, "msg": "邮箱格式不正确"})
        account = self.store.get_account_by_email(email)
        if not account:
            return web.json_response({"ok": False, "msg": "该邮箱未绑定任何账号"})
        ok, msg = self._verify_email_code("login", email, code)
        if not ok:
            return web.json_response({"ok": False, "msg": msg})
        account["last_login"] = int(time.time())
        await self.store.save()
        self._reset_rate(f"{ip}:email:{email}")
        csrf = secrets.token_urlsafe(24)
        resp = web.json_response({"ok": True, "msg": "登录成功"})
        self._set_session(resp, account["id"], csrf)
        return resp

    async def _api_bind_email(self, request: web.Request) -> web.Response:
        """老账号首次登录时强制绑定邮箱：校验 QQ+密码后绑定并直接登录。"""
        body = await request.json()
        qq = str(body.get("qq", "")).strip()
        password = str(body.get("password", ""))
        email = self._normalize_email(body.get("email", ""))
        code = str(body.get("code", "")).strip()
        ip = request.remote or "unknown"
        ok, msg = self._check_rate(f"{ip}:{qq}")
        if not ok:
            return web.json_response({"ok": False, "msg": msg})
        account = self.store.get_account_by_qq(qq)
        if not account:
            return web.json_response({"ok": False, "msg": "账号或密码错误"})
        if account["password_hash"] != self._hash_password(password, account["salt"]):
            return web.json_response({"ok": False, "msg": "账号或密码错误"})
        if account.get("email"):
            return web.json_response({"ok": False, "msg": "该账号已绑定邮箱，请直接登录"})
        if not _EMAIL_RE.match(email):
            return web.json_response({"ok": False, "msg": "邮箱格式不正确"})
        if self.store.get_account_by_email(email):
            return web.json_response({"ok": False, "msg": "该邮箱已被其他账号绑定"})
        ok, msg = self._verify_email_code("bind", email, code)
        if not ok:
            return web.json_response({"ok": False, "msg": msg})
        account["email"] = email
        account["last_login"] = int(time.time())
        await self.store.save()
        self._reset_rate(f"{ip}:{qq}")
        csrf = secrets.token_urlsafe(24)
        resp = web.json_response({"ok": True, "msg": "绑定成功，已登录"})
        self._set_session(resp, account["id"], csrf)
        return resp

    async def _api_logout(self, request: web.Request) -> web.Response:
        self._check_csrf(request)
        resp = web.json_response({"ok": True})
        self._clear_session(resp)
        return resp

    async def _api_me(self, request: web.Request) -> web.Response:
        sess = self._require_session(request)
        account = self.store.get_account(sess["aid"])
        if not account:
            raise web.HTTPUnauthorized(text="账号不存在")
        bound = []
        for bp in account.get("bound_pets", []):
            key = self.store.make_key(bp.get("group", ""), bp.get("qq", ""))
            player = self.store._data["players"].get(key)
            pet = self._resolve_player_pet(player, bp.get("pet_index", 0)) if player else None
            bound.append({
                "group_id": bp.get("group"),
                "qq": bp.get("qq"),
                "pet_index": bp.get("pet_index", 0),
                "nickname": pet.get("nickname") if pet else bp.get("nickname", "未命名"),
                "species": pet.get("species") if pet else bp.get("species", "未知"),
                "level": pet.get("level", 1) if pet else 1,
                "quality": pet.get("quality", "普通") if pet else "普通",
                "image_url": images.pet_image_url(pet.get("species")) if pet else None,
            })
        slots = []
        for slot in self.store.bound_slots_of(account):
            key = self.store.make_key(slot["group"], slot["qq"])
            player = self.store._data["players"].get(key)
            if not player:
                continue
            role = self._slot_role_summary(player, slot["group"], slot["qq"])
            slot_pets = [bp for bp in bound if bp["group_id"] == slot["group"] and bp["qq"] == slot["qq"]]
            slots.append({
                "group_id": slot["group"],
                "qq": slot["qq"],
                "pets": slot_pets,
                "adventure": role["adventure"],
                "mounts": role["mounts"],
                "pet_count": len(player.get("pets", []) or []),
                "mount_count": len(role["mounts"]),
            })
        return web.json_response({
            "ok": True,
            "account": {
                "id": account["id"],
                "qq": account["qq"],
                "email_masked": self._mask_email(account.get("email") or ""),
            },
            "bound_pets": bound,
            "slots": slots,
        })

    async def _api_bind_query(self, request: web.Request) -> web.Response:
        """查询指定群+用户ID 槽位（修士/宠物/坐骑）概览，供绑定前确认。"""
        self._check_csrf(request)
        self._require_session(request)
        body = await request.json()
        group_id = str(body.get("group_id", "")).strip()
        qq = str(body.get("qq", "")).strip()
        if not group_id or not qq:
            return web.json_response({"ok": False, "msg": "群号和用户 ID 不能为空"})
        key = self.store.make_key(group_id, qq)
        player = self.store._data["players"].get(key)
        if not player:
            return web.json_response({"ok": False, "msg": "该群聊与用户 ID 下不存在角色数据（修士/宠物/坐骑）"})
        existing = self.store.account_for_slot(group_id, qq)
        if existing:
            # 已绑定则直接返回当前绑定信息
            return web.json_response({"ok": False, "already_bound": True, "msg": "该角色已被绑定"})
        pets = player.get("pets", []) or []
        pet_list = []
        for i, pt in enumerate(pets):
            pet_list.append({
                "index": i,
                "nickname": pt.get("nickname", "未命名"),
                "species": pt.get("species", "未知"),
                "quality": pt.get("quality", "普通"),
                "level": pt.get("level", 1),
                "stage": pt.get("stage", "幼年期"),
                "element": pt.get("element", "未知"),
            })
        role = self._slot_role_summary(player, group_id, qq)
        return web.json_response({
            "ok": True,
            "pets": pet_list,
            "has_adventure": role["adventure"] is not None,
            "adventure": role["adventure"],
            "mounts": role["mounts"],
        })

    async def _api_bind_auto(self, request: web.Request) -> web.Response:
        """根据登录账号的绑定 QQ，自动列出各群名下角色槽位（修士/宠物/坐骑）及其绑定情况。

        匹配规则：玩家槽位 (group_id, user_id) 属于该账号，当且仅当
        - user_id == 账号绑定 QQ；或 user_id 经 qq_bindings 绑定到该 QQ（平台 openid → QQ号）。
        返回每个名下槽位的绑定状态：none=未绑定 / me=已绑到本账号 / other=被其它网页账号绑定（可强要回）。
        """
        self._check_csrf(request)
        sess = self._require_session(request)
        account = self.store.get_account(sess["aid"])
        if not account:
            raise web.HTTPUnauthorized(text="账号不存在")
        account_qq = str(account.get("qq", "")).strip()
        if not account_qq:
            return web.json_response({"ok": True, "qq": "", "groups": []})
        # 找出绑定到该账号 QQ 的平台用户ID（一个 QQ 至多绑一个 openid），并加自身 QQ 作为兜底。
        cand_pids = {account_qq}
        for pid, q in self.store.qq_bindings().items():
            if str(q) == account_qq:
                cand_pids.add(str(pid))
        # 汇总 (群, 用户ID) -> 已绑定账号ID，用于判断绑定状态与归属。
        bound_map: dict[tuple[str, str], str] = {}
        for acc_id, acc in self.store.accounts().items():
            for slot in self.store.bound_slots_of(acc):
                bound_map[(str(slot.get("group")), str(slot.get("qq")))] = acc_id
        # 记录被其它账号绑定的账号 QQ，便于展示。
        acc_qq_map = {a_id: str(a.get("qq", "")) for a_id, a in self.store.accounts().items()}
        players = self.store._data.get("players", {})
        groups: dict[str, list[dict]] = {}
        for key, player in players.items():
            sep = "\x1f"
            if sep not in key or not isinstance(player, dict):
                continue
            gid, uid = key.split(sep, 1)
            uid = str(uid)
            if uid not in cand_pids:
                continue
            pets = player.get("pets", []) or []
            adv = player.get("adventure") or {}
            mounts = player.get("mounts") or {}
            if not pets and not adv.get("name") and not mounts:
                continue
            gid = str(gid)
            owner_id = bound_map.get((gid, uid))
            if owner_id is None:
                bound = "none"
            elif owner_id == sess["aid"]:
                bound = "me"
            else:
                bound = "other"
            pet_list = []
            for i, pt in enumerate(pets):
                if not isinstance(pt, dict):
                    continue
                pet_list.append({
                    "index": i,
                    "nickname": pt.get("nickname", "未命名"),
                    "species": pt.get("species", "未知"),
                    "quality": pt.get("quality", "普通"),
                    "level": pt.get("level", 1),
                    "stage": pt.get("stage", "幼年期"),
                    "element": pt.get("element", "未知"),
                })
            role = self._slot_role_summary(player, gid, uid)
            entry = {
                "qq": uid,
                "pet_count": len(pet_list),
                "mount_count": len(mounts),
                "has_adventure": role["adventure"] is not None,
                "adventure_name": (role["adventure"] or {}).get("name"),
                "bound": bound,
                "pets": pet_list,
            }
            if bound == "other":
                entry["bound_qq"] = self._mask_qq(acc_qq_map.get(owner_id, ""))
            groups.setdefault(gid, []).append(entry)
        ordered = [{"group_id": g, "players": ps} for g, ps in groups.items()]
        return web.json_response({"ok": True, "qq": account_qq, "groups": ordered})

    async def _api_bind_reclaim(self, request: web.Request) -> web.Response:
        """宠物所有方（其 QQ 绑定了该槽位）强行要回绑定权。"""
        self._check_csrf(request)
        sess = self._require_session(request)
        body = await request.json()
        group_id = str(body.get("group_id", "")).strip()
        qq = str(body.get("qq", "")).strip()
        pet_index = int(body.get("pet_index", 0))
        if not group_id or not qq:
            return web.json_response({"ok": False, "msg": "群号和用户 ID 不能为空"})
        ok, msg = self.store.reclaim_pet_binding(sess["aid"], group_id, qq, pet_index)
        if ok:
            await self.store.save()
        return web.json_response({"ok": ok, "msg": msg})

    async def _api_bind(self, request: web.Request) -> web.Response:
        self._check_csrf(request)
        sess = self._require_session(request)
        body = await request.json()
        group_id = str(body.get("group_id", "")).strip()
        qq = str(body.get("qq", "")).strip()
        pet_index = int(body.get("pet_index", 0))
        if not group_id or not qq:
            return web.json_response({"ok": False, "msg": "群号和用户 ID 不能为空"})
        success, msg2 = self.store.bind_pet_to_account(sess["aid"], group_id, qq, pet_index)
        if success:
            await self.store.save()
        return web.json_response({"ok": success, "msg": msg2})

    async def _api_pet(self, request: web.Request) -> web.Response:
        self._require_session(request)
        group_id = request.query.get("group_id", "").strip()
        qq = request.query.get("qq", "").strip()
        pet_index = int(request.query.get("pet_index", "0"))
        if not group_id or not qq:
            raise web.HTTPBadRequest(text="缺少群号或用户 ID")
        # 验证当前账号确实绑定了该宠物
        owner = self.store.account_for_pet(group_id, qq)
        sess = self._current_session(request)
        if owner != sess.get("aid"):
            raise web.HTTPForbidden(text="你没有绑定该宠物")
        return web.json_response({"ok": True, **self._player_summary(group_id, qq, pet_index)})

    def _assistant_summary(self, player: dict, pet: dict | None) -> dict:
        return {
            **((pet.get("assistant") or {}) if pet else {}),
            "quota": self.store.assistant_quota(player),
            "free": self.store.assistant_free_active(),
            "max_tasks": data.ASSISTANT_MAX_TASKS,
            "options": [{"key": key, "description": description, "axis": axis}
                        for key, _label, description, axis in data.ASSISTANT_TASKS],
        }

    async def _api_assistant(self, request: web.Request) -> web.Response:
        self._check_csrf(request)
        sess = self._require_session(request)
        body = await request.json()
        if not isinstance(body, dict):
            return web.json_response({"ok": False, "msg": "无效的助手设置"})
        group_id = str(body.get("group_id", "")).strip()
        qq = str(body.get("qq", "")).strip()
        if not group_id or not qq:
            return web.json_response({"ok": False, "msg": "缺少群号或用户 ID"})
        owner = self.store.account_for_pet(group_id, qq)
        if owner != sess.get("aid"):
            raise web.HTTPForbidden(text="你没有绑定该宠物")
        key = self.store.make_key(group_id, qq)
        player = self.store._data["players"].get(key)
        if not player:
            return web.json_response({"ok": False, "msg": "未找到该宠物"})
        pet_index = body.get("pet_index", 0)
        if (type(pet_index) is not int or pet_index < 0
                or pet_index >= (len(player.get("pets") or []) or 1)):
            return web.json_response({"ok": False, "msg": "宠物已变更，请刷新后重新选择"})
        pet = self._resolve_player_pet(player, pet_index)
        if not pet:
            return web.json_response({"ok": False, "msg": "未找到该宠物"})
        a = dict(pet.get("assistant") or {
            "enabled": False,
            "tasks": [],
            "total_runs": 0,
            "last_run_at": 0,
            "log": [],
        })
        if "tasks" not in body and "enabled" not in body:
            return web.json_response({"ok": False, "msg": "请选择活动或设置助手开关"})
        if "enabled" in body and type(body["enabled"]) is not bool:
            return web.json_response({"ok": False, "msg": "无效的助手开关"})
        tasks = body.get("tasks", [t for t in (a.get("tasks") or []) if t in data.ASSISTANT_TASK_BY_KEY])
        if (not isinstance(tasks, list) or len(tasks) > data.ASSISTANT_MAX_TASKS
                or any(not isinstance(t, str) or t not in data.ASSISTANT_TASK_BY_KEY for t in tasks)
                or len(set(tasks)) != len(tasks)):
            return web.json_response({"ok": False, "msg": f"请选择最多 {data.ASSISTANT_MAX_TASKS} 个不同的有效活动"})
        enabled = body.get("enabled", bool(a.get("enabled") and tasks))
        if enabled and not tasks:
            return web.json_response({
                "ok": False,
                "msg": "请先点击「选择活动」保存要代跑的活动，再开启助手",
            })
        # 修改活动和关闭助手不消耗次数；主动开启仍遵守额度及免费窗口。
        available = self.store.assistant_active(player) or self.store.assistant_free_active()
        if body.get("enabled") is True and not available:
            return web.json_response({"ok": False, "msg": "自动助手次数不足，请先兑换自动助手卡"})
        a["tasks"] = list(tasks)
        enabled = enabled and available
        a["enabled"] = bool(enabled and tasks)
        saved = pet.setdefault("assistant", a)
        saved.update(tasks=a["tasks"], enabled=a["enabled"])
        await self.store.save()
        return web.json_response({
            "ok": True,
            "msg": (("活动已保存，助手继续运行" if a["enabled"] else "活动已保存，助手当前已关闭")
                    if "tasks" in body else ("已开启自动助手" if a["enabled"] else "已关闭自动助手")),
            "assistant": self._assistant_summary(player, pet),
        })

    async def _api_custom_redeem(self, request: web.Request) -> web.Response:
        try:
            self._check_csrf(request)
            sess = self._require_session(request)
            account = self.store.get_account(sess.get("aid", ""))
            body = await request.json()
            group_id = str(body.get("group_id", "")).strip()
            qq = str(body.get("qq", "")).strip()
            pet_index = int(body.get("pet_index", 0))
            code = str(body.get("code", "")).strip()
            nickname = str(body.get("nickname", "")).strip() or "神秘训练家"
            show_qq = str(body.get("show_qq", "")).strip() or (account.get("qq") if account else sess.get("aid", ""))
            logger.info(f"[petpark] 收到定制解锁请求 group={group_id} qq={qq} code={code}")
            if not group_id or not qq or not code:
                return web.json_response({"ok": False, "msg": "参数不完整"})
            logger.info(f"[petpark] 定制解锁：参数校验通过，owner={sess.get('aid')}")
            owner = self.store.account_for_pet(group_id, qq)
            if owner != sess.get("aid"):
                logger.warning(f"[petpark] 定制解锁：无权操作，owner={owner} aid={sess.get('aid')}")
                raise web.HTTPForbidden(text="你没有绑定该宠物")
            key = self.store.make_key(group_id, qq)
            player = self.store._data["players"].get(key)
            if not player:
                logger.warning(f"[petpark] 定制解锁：未找到玩家 {key}")
                return web.json_response({"ok": False, "msg": "未找到该宠物"})
            logger.info(f"[petpark] 定制解锁：找到玩家，准备兑换卡密")
            pet, err = self.store.redeem_custom_card(code, player, sess.get("aid"), pet_index)
            if err:
                logger.warning(f"[petpark] 定制解锁：卡密兑换失败 {err}")
                return web.json_response({"ok": False, "msg": err})
            logger.info(f"[petpark] 定制解锁：卡密兑换成功，宠物={pet.get('nickname')}")
            await self.store.save()
            logger.info("[petpark] 定制解锁：数据已保存")
            # 全授权群通报（异步后台执行，不阻塞 HTTP 响应）
            broadcast_submitted = False
            logger.info(f"[petpark] 定制解锁：broadcast_callback 是否配置={bool(self.broadcast_callback)}")
            if self.broadcast_callback:
                try:
                    pet_nick = pet.get("nickname", "宠物") if pet else "宠物"
                    species = pet.get("custom_species_name") or pet.get("species", "神秘生物") if pet else "神秘生物"
                    text = (
                        "🎉 **全服贺电！灵契仙途迎来全新混沌定制大师！** 🎉\n\n"
                        f"👑 尊贵的训练家 **{nickname}**（QQ：{show_qq}）\n"
                        f"为心爱的 **{pet_nick}** 解锁了【混沌定制】权限！\n\n"
                        f"✨ **{pet_nick}** 已褪去凡躯，化身为独一无二的 **{species}**，\n"
                        "品质晋升为【混沌】，傲视群宠，闪耀全服！\n\n"
                        "💎 这是实力与热爱的象征，让我们共同祝贺这位大师登上灵契仙途的巅峰！\n"
                        "🚀 各位训练家也快去努力，打造属于自己的专属传奇宠物吧！"
                    )
                    logger.info(f"[petpark] 准备发送定制解锁全服广播，训练家：{nickname}，宠物：{pet_nick}")
                    task = self.broadcast_callback(text)
                    logger.info(f"[petpark] broadcast_callback 返回任务={task}")
                    if task:
                        def _log_broadcast(t):
                            try:
                                result = t.result()
                                logger.info(f"[petpark] 定制解锁全服广播结果：{result}")
                            except Exception as e:
                                logger.exception(f"[petpark] 定制解锁广播任务异常：{e}")
                        task.add_done_callback(_log_broadcast)
                        broadcast_submitted = True
                        logger.info("[petpark] 定制解锁全服广播已提交后台执行")
                    else:
                        logger.warning("[petpark] broadcast_callback 未返回广播任务")
                except Exception as e:
                    logger.exception(f"[petpark] 定制解锁广播失败：{e}")
            else:
                logger.warning("[petpark] 未配置 broadcast_callback，无法发送定制解锁广播")
            resp = {
                "ok": True,
                "msg": "定制权限已解锁" + ("，全服祝贺已发送" if broadcast_submitted else ""),
                "pet": self._format_pet(player, group_id, qq, pet_index),
                "broadcast_submitted": broadcast_submitted,
            }
            logger.info(f"[petpark] 定制解锁：返回响应 {resp.get('msg')}")
            return web.json_response(resp)
        except Exception as e:
            logger.exception(f"[petpark] 定制解锁接口未捕获异常：{e}")
            return web.json_response({"ok": False, "msg": f"服务器内部错误：{e}"})

    async def _api_custom_submit(self, request: web.Request) -> web.Response:
        self._check_csrf(request)
        sess = self._require_session(request)
        reader = await request.multipart()
        fields: dict[str, str] = {}
        file_data: Optional[bytes] = None
        filename: Optional[str] = None
        async for part in reader:
            if part.filename:
                file_data = await part.read()
                filename = part.filename
            else:
                fields[part.name] = await part.text()
        group_id = str(fields.get("group_id", "")).strip()
        qq = str(fields.get("qq", "")).strip()
        species_name = str(fields.get("species_name", "")).strip()
        if not group_id or not qq:
            return web.json_response({"ok": False, "msg": "参数不完整"})
        owner = self.store.account_for_pet(group_id, qq)
        if owner != sess.get("aid"):
            raise web.HTTPForbidden(text="你没有绑定该宠物")
        key = self.store.make_key(group_id, qq)
        player = self.store._data["players"].get(key)
        if not player:
            return web.json_response({"ok": False, "msg": "未找到该宠物"})
        pet_index = int(fields.get("pet_index", 0))
        pet = self._resolve_player_pet(player, pet_index)
        if not pet or not pet.get("custom"):
            return web.json_response({"ok": False, "msg": "该宠物未解锁定制权限"})
        changes: dict[str, str] = {}
        current_name = pet.get("custom_species_name") or pet.get("species") or ""
        if species_name and species_name != current_name:
            changes["species_name"] = species_name
        if file_data:
            ext = Path(filename).suffix.lower() if filename else ".jpg"
            if ext not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
                return web.json_response({"ok": False, "msg": "仅支持 jpg/png/gif/webp 图片"})
            file_data, ext = self._normalize_custom_image(file_data, ext)
            new_filename = f"{secrets.token_hex(8)}{ext}"
            path = self.store.custom_image_path(new_filename)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(file_data)
            except OSError as e:
                logger.exception(f"[petpark] 宠物定制图写盘失败 {path}: {e}")
                return web.json_response({"ok": False, "msg": f"图片保存失败：{e}"})
            if not path.exists():
                logger.error(f"[petpark] 宠物定制图写盘后不存在 {path} (dir={self.store.custom_images_dir})")
                return web.json_response({"ok": False, "msg": "图片保存失败，请重试"})
            logger.info(f"[petpark] 宠物定制图已落盘 {path} size={len(file_data)}")
            changes["image"] = new_filename
        review, err = self.store.create_custom_review(sess["aid"], group_id, qq, changes)
        if not review:
            return web.json_response({"ok": False, "msg": err or "提交失败，请稍后再试"})
        await self.store.save()
        return web.json_response({
            "ok": True,
            "msg": "已提交审核，预计 3 个工作日内处理完毕",
            "review": review,
        })

    async def _api_mount_custom_redeem(self, request: web.Request) -> web.Response:
        """坐骑定制卡兑换：获得 1 次「新建定制坐骑」资格。"""
        try:
            self._check_csrf(request)
            sess = self._require_session(request)
            body = await request.json()
            group_id = str(body.get("group_id", "")).strip()
            qq = str(body.get("qq", "")).strip()
            code = str(body.get("code", "")).strip()
            if not group_id or not qq or not code:
                return web.json_response({"ok": False, "msg": "参数不完整"})
            owner = self.store.account_for_slot(group_id, qq)
            if owner != sess.get("aid"):
                raise web.HTTPForbidden(text="你没有绑定该角色")
            key = self.store.make_key(group_id, qq)
            player = self.store._data["players"].get(key)
            if not player:
                return web.json_response({"ok": False, "msg": "未找到该角色"})
            ok, msg = self.store.redeem_mount_custom_card(code, player, sess.get("aid"))
            if not ok:
                return web.json_response({"ok": False, "msg": msg})
            await self.store.save()
            role = self._slot_role_summary(player, group_id, qq)
            return web.json_response({
                "ok": True,
                "msg": msg,
                "mount_custom": role["mount_custom"],
            })
        except Exception as e:
            logger.exception(f"[petpark] 坐骑定制卡兑换异常：{e}")
            return web.json_response({"ok": False, "msg": f"服务器内部错误：{e}"})

    async def _api_mount_custom_submit(self, request: web.Request) -> web.Response:
        """提交新建定制坐骑审核：定制名 + 外观图，通过后创建初始战力 30 万的定制坐骑。"""
        try:
            self._check_csrf(request)
            sess = self._require_session(request)
            reader = await request.multipart()
            fields: dict[str, str] = {}
            file_data: Optional[bytes] = None
            filename: Optional[str] = None
            async for part in reader:
                if part.filename:
                    file_data = await part.read()
                    filename = part.filename
                else:
                    fields[part.name] = await part.text()
            group_id = str(fields.get("group_id", "")).strip()
            qq = str(fields.get("qq", "")).strip()
            mname = str(fields.get("name", "")).strip()
            nickname = str(fields.get("nickname", "")).strip()
            show_qq = str(fields.get("show_qq", "")).strip()
            if not group_id or not qq:
                return web.json_response({"ok": False, "msg": "参数不完整"})
            owner = self.store.account_for_slot(group_id, qq)
            if owner != sess.get("aid"):
                raise web.HTTPForbidden(text="你没有绑定该角色")
            key = self.store.make_key(group_id, qq)
            player = self.store._data["players"].get(key)
            if not player:
                return web.json_response({"ok": False, "msg": "未找到该角色"})
            if not (1 <= len(mname) <= 8):
                return web.json_response({"ok": False, "msg": "定制坐骑名称需 1~8 个字"})
            if not file_data:
                return web.json_response({"ok": False, "msg": "请上传定制坐骑外观图片"})
            ext = Path(filename).suffix.lower() if filename else ".jpg"
            if ext not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
                return web.json_response({"ok": False, "msg": "仅支持 jpg/png/gif/webp 图片"})
            if len(file_data) > 5 * 1024 * 1024:
                return web.json_response({"ok": False, "msg": "图片不能超过 5MB"})
            file_data, ext = self._normalize_custom_image(file_data, ext)
            new_filename = f"{secrets.token_hex(8)}{ext}"
            path = self.store.custom_image_path(new_filename)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(file_data)
            except OSError as e:
                logger.exception(f"[petpark] 坐骑定制图写盘失败 {path}: {e}")
                return web.json_response({"ok": False, "msg": f"图片保存失败：{e}"})
            if not path.exists():
                logger.error(f"[petpark] 坐骑定制图写盘后不存在 {path} (dir={self.store.custom_images_dir})")
                return web.json_response({"ok": False, "msg": "图片保存失败，请重试"})
            logger.info(f"[petpark] 坐骑定制图已落盘 {path} size={len(file_data)}")
            changes = {"name": mname, "image": new_filename}
            if nickname:
                changes["nickname"] = nickname
            if show_qq:
                changes["show_qq"] = show_qq
            review, err = self.store.create_custom_review(
                sess.get("aid"), group_id, qq, changes,
                kind="mount", mount_name=mname)
            if not review:
                # 仅在真正失败（review 未创建）时回收图片文件；成功路径 err 恒为空
                try:
                    if path.exists():
                        path.unlink()
                except OSError:
                    pass
                return web.json_response({"ok": False, "msg": err or "提交失败，请稍后再试"})
            await self.store.save()
            role = self._slot_role_summary(player, group_id, qq)
            return web.json_response({
                "ok": True,
                "msg": "定制坐骑已提交审核，预计 3 个工作日内处理完毕",
                "review": review,
                "mount_custom": role["mount_custom"],
            })
        except Exception as e:
            logger.exception(f"[petpark] 定制坐骑提交异常：{e}")
            return web.json_response({"ok": False, "msg": f"服务器内部错误：{e}"})

    async def _api_mount_image_submit(self, request: web.Request) -> web.Response:
        """更换已有定制坐骑的外观图：上传新图 → 审核（每月 3 次，通过后生效）。"""
        try:
            self._check_csrf(request)
            sess = self._require_session(request)
            reader = await request.multipart()
            fields: dict[str, str] = {}
            file_data: Optional[bytes] = None
            filename: Optional[str] = None
            async for part in reader:
                if part.filename:
                    file_data = await part.read()
                    filename = part.filename
                else:
                    fields[part.name] = await part.text()
            group_id = str(fields.get("group_id", "")).strip()
            qq = str(fields.get("qq", "")).strip()
            mname = str(fields.get("name", "")).strip()
            if not group_id or not qq or not mname:
                return web.json_response({"ok": False, "msg": "参数不完整"})
            owner = self.store.account_for_slot(group_id, qq)
            if owner != sess.get("aid"):
                raise web.HTTPForbidden(text="你没有绑定该角色")
            key = self.store.make_key(group_id, qq)
            player = self.store._data["players"].get(key)
            if not player:
                return web.json_response({"ok": False, "msg": "未找到该角色"})
            inst = (player.get("mounts") or {}).get(mname)
            if not inst or not inst.get("custom"):
                return web.json_response({"ok": False, "msg": "未找到该定制坐骑"})
            if not file_data:
                return web.json_response({"ok": False, "msg": "请上传新的外观图片"})
            ext = Path(filename).suffix.lower() if filename else ".jpg"
            if ext not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
                return web.json_response({"ok": False, "msg": "仅支持 jpg/png/gif/webp 图片"})
            if len(file_data) > 5 * 1024 * 1024:
                return web.json_response({"ok": False, "msg": "图片不能超过 5MB"})
            file_data, ext = self._normalize_custom_image(file_data, ext)
            new_filename = f"{secrets.token_hex(8)}{ext}"
            path = self.store.custom_image_path(new_filename)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(file_data)
            except OSError as e:
                logger.exception(f"[petpark] 坐骑换装图写盘失败 {path}: {e}")
                return web.json_response({"ok": False, "msg": f"图片保存失败：{e}"})
            if not path.exists():
                logger.error(f"[petpark] 坐骑换装图写盘后不存在 {path} (dir={self.store.custom_images_dir})")
                return web.json_response({"ok": False, "msg": "图片保存失败，请重试"})
            logger.info(f"[petpark] 坐骑换装图已落盘 {path} size={len(file_data)} mount={mname}")
            changes = {"name": mname, "image": new_filename}
            review, err = self.store.create_custom_review(
                sess.get("aid"), group_id, qq, changes,
                kind="mount_image", mount_name=mname)
            if not review:
                try:
                    if path.exists():
                        path.unlink()
                except OSError:
                    pass
                return web.json_response({"ok": False, "msg": err or "提交失败，请稍后再试"})
            await self.store.save()
            role = self._slot_role_summary(player, group_id, qq)
            return web.json_response({
                "ok": True,
                "msg": "坐骑外观更换已提交审核（每月可更换 3 次），预计 3 个工作日内处理完毕",
                "review": review,
                "mount_custom": role["mount_custom"],
                "role": {"mounts": role["mounts"], "mount_custom": role["mount_custom"]},
            })
        except Exception as e:
            logger.exception(f"[petpark] 坐骑换装提交异常：{e}")
            return web.json_response({"ok": False, "msg": f"服务器内部错误：{e}"})

    # --------------------------- 玩家反馈 ---------------------------
    _FEEDBACK_IMG_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    _FEEDBACK_IMG_MAX = 5 * 1024 * 1024

    async def _api_feedback_submit(self, request: web.Request) -> web.Response:
        self._check_csrf(request)
        sess = self._require_session(request)
        account = self.store.get_account(sess["aid"])
        ok_rate, why = self._check_rate(f"feedback:{sess.get('aid')}")
        if not ok_rate:
            return web.json_response({"ok": False, "msg": why})
        reader = await request.multipart()
        fields: dict[str, str] = {}
        images: list[str] = []
        async for part in reader:
            if part.filename:
                if len(images) >= 3:
                    continue
                ext = Path(part.filename).suffix.lower()
                if ext not in self._FEEDBACK_IMG_EXTS:
                    return web.json_response({"ok": False, "msg": "仅支持 jpg/png/gif/webp 图片"})
                blob = await part.read()
                if len(blob) > self._FEEDBACK_IMG_MAX:
                    return web.json_response({"ok": False, "msg": "单张图片不能超过 5MB"})
                fname = f"{secrets.token_hex(8)}{ext}"
                self.store.feedback_image_path(fname).write_bytes(blob)
                images.append(fname)
            else:
                fields[part.name] = await part.text()
        kind = fields.get("kind", "bug")
        if kind not in ("bug", "suggestion"):
            kind = "bug"
        content = str(fields.get("content", "")).strip()
        occur_time = str(fields.get("occur_time", "")).strip()
        group_id = str(fields.get("group_id", "")).strip()
        user_id = str(fields.get("user_id", "")).strip()
        if not content:
            return web.json_response({"ok": False, "msg": "请填写问题描述"})
        if len(content) > 2000:
            return web.json_response({"ok": False, "msg": "描述请控制在 2000 字以内"})
        if kind == "bug":
            if not occur_time:
                return web.json_response({"ok": False, "msg": "请填写发生时间"})
            if not group_id:
                return web.json_response({"ok": False, "msg": "请填写对应的 QQ 群号"})
            if not user_id:
                return web.json_response({"ok": False, "msg": "请填写对应的用户 ID"})
        fb = self.store.create_feedback(
            sess["aid"],
            account.get("qq", "") if account else "",
            kind,
            content,
            occur_time=occur_time,
            group_id=group_id,
            user_id=user_id,
            images=images,
        )
        await self.store.save()
        return web.json_response({"ok": True, "msg": "反馈已提交，管理员处理后可在「我的反馈」中查看回复", "feedback": fb})

    async def _api_feedback_list(self, request: web.Request) -> web.Response:
        sess = self._require_session(request)
        return web.json_response({"ok": True, "data": self.store.account_feedbacks(sess["aid"])})

    async def _api_feedback_delete(self, request: web.Request) -> web.Response:
        self._check_csrf(request)
        sess = self._require_session(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        fid = str(body.get("id", "")).strip()
        fb = self.store.feedbacks().get(fid)
        if not fb or fb.get("account_id") != sess.get("aid"):
            return web.json_response({"ok": False, "msg": "反馈记录不存在"})
        if fb.get("status") == "resolved":
            return web.json_response({"ok": False, "msg": "该反馈已由管理员处理，无法删除"})
        self.store.delete_feedback(fid)
        await self.store.save()
        return web.json_response({"ok": True, "msg": "反馈已删除"})

    # --------------------------- 宠物对话 ---------------------------
    async def _chat_page(self, request: web.Request) -> web.Response:
        sess = self._current_session(request)
        if not sess:
            raise web.HTTPFound("/")
        csrf = sess.get("csrf")
        html = _CHAT_HTML.replace("{{CSRF_TOKEN}}", csrf)
        response = web.Response(text=html, content_type="text/html")
        self._set_session(response, sess["aid"], csrf)
        return response

    async def _api_chat_send(self, request: web.Request) -> web.Response:
        self._check_csrf(request)
        sess = self._require_session(request)
        gw = self.command_gateway
        if gw is None or not hasattr(gw, "web_dispatch"):
            return web.json_response({"ok": False, "msg": "对话服务未就绪，请重载插件后重试"})
        try:
            body = await request.json()
        except Exception:
            body = {}
        group_id = str(body.get("group_id", "")).strip()
        qq = str(body.get("qq", "")).strip()
        text = str(body.get("text", "")).strip()
        if not group_id or not qq:
            return web.json_response({"ok": False, "msg": "参数不完整"})
        if not text:
            return web.json_response({"ok": False, "msg": "消息不能为空"})
        if len(text) > 200:
            return web.json_response({"ok": False, "msg": "消息太长啦，请控制在 200 字以内"})
        owner = self.store.account_for_pet(group_id, qq)
        if owner != sess.get("aid"):
            raise web.HTTPForbidden(text="你没有绑定该宠物")
        try:
            reply = await gw.web_dispatch(qq, group_id, text)
        except Exception as e:
            logger.exception("[petpark] 网页对话执行出错")
            return web.json_response({"ok": True, "reply": f"灵契仙途处理出错：{e}", "image_md": None})
        image_md = None
        if isinstance(reply, tuple):
            reply, image_md = reply
        if reply is None:
            reply = "😶 没有听懂这条指令…发送「宠物菜单」可以查看全部可用指令哦。"
        return web.json_response({"ok": True, "reply": reply, "image_md": image_md})

    # --------------------------- 安卓 App 版本 / 下载 ---------------------------
    async def _api_app_version(self, request: web.Request) -> web.Response:
        rel = self.store.app_release()
        if not rel.get("filename"):
            return web.json_response({"ok": False, "msg": "暂无发布版本"})
        return web.json_response({
            "ok": True,
            "version_code": int(rel.get("version_code", 0) or 0),
            "version_name": str(rel.get("version_name", "")),
            "changelog": str(rel.get("changelog", "")),
            "url": "/app_download/latest.apk",
            "updated_at": rel.get("updated_at"),
        })

    async def _app_download(self, request: web.Request) -> web.StreamResponse:
        rel = self.store.app_release()
        filename = str(rel.get("filename", "") or "")
        path = (self.store.app_release_dir / filename) if filename else None
        if not filename or not path.exists():
            raise web.HTTPNotFound(text="暂无发布版本")
        return web.FileResponse(
            path,
            headers={
                "Content-Type": "application/vnd.android.package-archive",
                "Content-Disposition": 'attachment; filename="petpark.apk"',
            },
        )

    # --------------------------- 道具使用 / 卡密兑换 / 改密 ---------------------------
    async def _api_use_item(self, request: web.Request) -> web.Response:
        self._check_csrf(request)
        sess = self._require_session(request)
        if self.command_gateway is None:
            return web.json_response({"ok": False, "msg": "功能暂不可用，请重载插件后重试"})
        body = await request.json()
        group_id = str(body.get("group_id", "")).strip()
        qq = str(body.get("qq", "")).strip()
        pet_index = int(body.get("pet_index", 0))
        name = str(body.get("name", "")).strip()
        try:
            count = max(1, min(9999, int(body.get("count", 1))))
        except (TypeError, ValueError):
            count = 1
        if not name:
            return web.json_response({"ok": False, "msg": "请选择要使用的道具"})
        player = self._owned_player(sess, group_id, qq)
        bag = player.get("bag", {})
        if bag.get(name, 0) <= 0:
            return web.json_response({"ok": False, "msg": f"背包里没有『{name}』"})
        try:
            gw = self.command_gateway
            if name in data.ARTIFACTS:
                # 神器：等同群聊「佩戴神器 名称」
                text = gw._equip_artifact(player, ["佩戴神器", name])
            else:
                # 普通道具 / 秘技书：等同群聊「使用 名称 数量」
                text = gw._use_item(player, ["使用", name, str(count)])
        except Exception as e:
            logger.exception("[petpark] 门户使用道具失败")
            return web.json_response({"ok": False, "msg": f"使用失败：{e}"})
        await self.store.save()
        text = str(text)
        failed_markers = ("没有", "不足", "不能", "无法", "无需", "用法：", "需要", "已学会", "还活着", "已佩戴该")
        success = not any(m in text for m in failed_markers) or "成功" in text
        return web.json_response({
            "ok": success,
            "msg": text,
            "summary": self._player_summary(group_id, qq, pet_index),
        })

    async def _api_item_info(self, request: web.Request) -> web.Response:
        self._require_session(request)
        if self.command_gateway is None:
            return web.json_response({"ok": False, "msg": "功能暂不可用，请重载插件后重试"})
        name = str(request.query.get("name", "")).strip()
        if not name:
            return web.json_response({"ok": False, "msg": "请指定物品名称"})
        try:
            # 等同群聊「查看说明 物品名」
            text = self.command_gateway._handle_info("查看说明", ["查看说明", name])
        except Exception as e:
            logger.exception("[petpark] 门户查看物品说明失败")
            return web.json_response({"ok": False, "msg": f"查询失败：{e}"})
        text = str(text or "")
        ok = bool(text) and "未找到" not in text
        return web.json_response({"ok": ok, "msg": text or f"❓ 未找到『{name}』的说明。"})

    async def _api_pet_action(self, request: web.Request) -> web.Response:
        self._check_csrf(request)
        sess = self._require_session(request)
        if self.command_gateway is None:
            return web.json_response({"ok": False, "msg": "功能暂不可用，请重载插件后重试"})
        body = await request.json()
        group_id = str(body.get("group_id", "")).strip()
        qq = str(body.get("qq", "")).strip()
        pet_index = int(body.get("pet_index", 0))
        action = str(body.get("action", "")).strip()
        try:
            times = max(1, min(9999, int(body.get("times", 1))))
        except (TypeError, ValueError):
            times = 1
        player = self._owned_player(sess, group_id, qq)
        gw = self.command_gateway
        try:
            if action == "auto_level":
                # 等同群聊「一键升级宠物」
                text = gw._auto_level(player)
            elif action == "level":
                # 等同群聊「宠物升级 次数」
                text = gw._manual_level(player, ["宠物升级", str(times)])
            elif action == "evolve":
                # 等同群聊「宠物进化」
                text = gw._evolve(player, render_image=False)
            else:
                return web.json_response({"ok": False, "msg": "未知操作"})
        except Exception as e:
            logger.exception("[petpark] 门户宠物操作失败")
            return web.json_response({"ok": False, "msg": f"操作失败：{e}"})
        await self.store.save()
        text = str(text)
        failed_markers = ("没有", "不足", "不能", "无法", "失败", "未能", "正在", "无需")
        success = not any(m in text for m in failed_markers)
        return web.json_response({
            "ok": success,
            "msg": text,
            "summary": self._player_summary(group_id, qq, pet_index),
        })

    async def _api_redeem(self, request: web.Request) -> web.Response:
        self._check_csrf(request)
        sess = self._require_session(request)
        if self.command_gateway is None:
            return web.json_response({"ok": False, "msg": "功能暂不可用，请重载插件后重试"})
        body = await request.json()
        group_id = str(body.get("group_id", "")).strip()
        qq = str(body.get("qq", "")).strip()
        pet_index = int(body.get("pet_index", 0))
        code = str(body.get("code", "")).strip()
        if not code:
            return web.json_response({"ok": False, "msg": "请输入卡密"})
        ok_rate, why = self._check_rate(f"redeem:{sess.get('aid')}")
        if not ok_rate:
            return web.json_response({"ok": False, "msg": why})
        player = self._owned_player(sess, group_id, qq)
        try:
            # 等同群聊「兑换 卡密」
            text = self.command_gateway._redeem(player, group_id, qq, ["兑换", code])
        except Exception as e:
            logger.exception("[petpark] 门户卡密兑换失败")
            return web.json_response({"ok": False, "msg": f"兑换失败：{e}"})
        await self.store.save()
        # 不能靠「兑换成功」四个字判定：自动助手卡的成功文案是
        # 「🧘 自动助手卡使用成功」，会被误判成失败。改为看失败前缀。
        success = not str(text).lstrip().startswith(("❌", "⚠️"))
        if success:
            self._reset_rate(f"redeem:{sess.get('aid')}")
        return web.json_response({
            "ok": success,
            "msg": str(text),
            "summary": self._player_summary(group_id, qq, pet_index),
        })

    async def _api_change_password(self, request: web.Request) -> web.Response:
        self._check_csrf(request)
        sess = self._require_session(request)
        body = await request.json()
        code = str(body.get("code", "")).strip()
        new_password = str(body.get("new_password", ""))
        if len(new_password) < 6:
            return web.json_response({"ok": False, "msg": "新密码至少 6 位"})
        account = self.store.get_account(sess.get("aid"))
        if not account:
            return web.json_response({"ok": False, "msg": "账号不存在"})
        ok_rate, why = self._check_rate(f"chpwd:{sess.get('aid')}")
        if not ok_rate:
            return web.json_response({"ok": False, "msg": why})
        email = self._normalize_email(account.get("email", ""))
        if not email:
            return web.json_response({"ok": False, "msg": "该账号尚未绑定邮箱，请重新登录完成绑定"})
        ok_code, msg = self._verify_email_code("chpwd", email, code)
        if not ok_code:
            return web.json_response({"ok": False, "msg": msg})
        salt = self._make_salt()
        account["salt"] = salt
        account["password_hash"] = self._hash_password(new_password, salt)
        await self.store.save()
        self._reset_rate(f"chpwd:{sess.get('aid')}")
        return web.json_response({"ok": True, "msg": "密码修改成功"})


# --------------------------- 前端页面 ---------------------------
# 明亮现代风格：浅色背景 + 白色卡片 + 品牌渐变
_PORTAL_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<script src="/webstatic/device-ui.js?v=20260922-1"></script>
<title>灵契仙途 · 玩家中心</title>
<link rel="stylesheet" href="/webstatic/element-plus.min.css">
<link rel="stylesheet" href="/webstatic/portal.css?v=20260922-4">
<link rel="stylesheet" href="/webstatic/mobile.css?v=20260922-1">
</head>
<body class="portal-page">
<div id="app" v-cloak>
<div class="layout">
  <aside class="sidebar" :class="{expanded:roleMenu}" aria-label="角色与账号">
    <a class="side-brand" href="/"><span class="seal" aria-hidden="true">契</span><span>灵契仙途<small>玩家中心</small></span></a>
    <button class="role-toggle" @click="roleMenu=!roleMenu" :aria-expanded="roleMenu" aria-controls="role-picker" aria-label="切换角色与账号"><span class="role-toggle-label">{{ data && data.adventure ? data.adventure.name : '切换角色' }}</span><span aria-hidden="true">⌄</span></button>
    <div id="role-picker" class="role-picker">
    <div class="side-sec">我的角色</div>
    <div class="side-pets">
      <button type="button" v-for="s in slots" :key="s.group_id + ':' + s.qq" class="pet-chip"
           :class="{active: currentSlot && currentSlot.group_id===s.group_id && currentSlot.qq===s.qq}"
           @click="switchSlot(s);roleMenu=false" :aria-pressed="!!(currentSlot && currentSlot.group_id===s.group_id && currentSlot.qq===s.qq)">
        <span class="role-symbol" aria-hidden="true">修</span>
        <div class="info">
          <div class="name">{{ slotLabel(s) }}</div>
          <div class="sub">{{ slotSub(s) }}</div>
        </div>
      </button>
      <span v-if="!slots.length" class="muted" style="padding:0 8px">暂无绑定角色（修士/宠物/坐骑）</span>
    </div>
    <div class="side-btns">
      <el-button type="primary" plain round @click="openBind()">＋ 绑定角色</el-button>
      <el-button type="success" round @click="goChat"> 网页游玩</el-button>
      <el-button type="warning" round @click="goFeedback"> 问题反馈</el-button>
    </div>
    <p class="side-tip">绑定群号+用户ID 一次，其下修士/宠物/坐骑即可统一管理。</p>
    </div><div class="side-foot">
      <div class="side-user" v-if="account">QQ {{ account.qq }}</div>
      <div class="side-foot-btns">
        <el-button size="small" round @click="openPwd">修改密码</el-button>
        <el-button size="small" round @click="logout">退出登录</el-button>
      </div>
    </div>
  </aside>

  <section class="content">
    <div class="content-inner">
      <header class="portal-header">
        <div><a href="/" class="home-link">官网首页</a><h1>我的仙途</h1><p>{{ account ? '修士、灵宠与坐骑，都在这一处。' : '正在读取你的角色…' }}</p></div>
        <a class="play-link" href="/chat">网页游玩 ↗</a>
      </header>
      <nav class="panel-nav" aria-label="玩家中心功能">
        <button v-for="tab in panelTabs" :key="tab.key" @click="selectPanel(tab.key)" :class="{active:activePanel===tab.key}" :aria-current="activePanel===tab.key ? 'page' : undefined"><svg class="mobile-tab-icon" viewBox="0 0 24 24" aria-hidden="true"><path :d="tab.icon"></path></svg><span>{{ tab.label }}</span></button>
      </nav>
      <div v-if="loadError" class="card load-error" role="alert"><h2>暂时无法读取角色</h2><p>{{ loadError }}</p><el-button @click="current ? loadPet(current) : init()">重新加载</el-button></div>
      <div v-else-if="initialLoading || petLoading" class="card loading-state" role="status"><span class="loading-dot"></span>正在读取角色档案…</div>
      <div v-else-if="!current" class="card welcome-state"><h2>从绑定你的角色开始</h2><p>填写所在群的群号与用户 ID，即可查看修士、灵宠和坐骑。</p><el-button type="primary" @click="openBind()">绑定已有角色</el-button><a href="/">返回官网查看入门指引</a></div>
      <template v-else-if="data">
        <div class="role-context"><span>群 {{ data.group_id }} <span class="context-divider">/</span> 用户 {{ data.qq }}</span><el-button text @click="loadPet(current)">刷新档案</el-button></div>
        <section v-show="activePanel==='overview'" aria-label="角色总览">
      <template v-if="data && data.adventure">
        <div class="sec-title"> 我的修士</div>
        <div class="card cultivator-card" style="margin-bottom:12px">
          <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
            <div style="flex:1;min-width:240px">
              <div style="font-size:18px;font-weight:800">
                 {{ data.adventure.name }}
                <span style="font-size:12px;font-weight:500;color:var(--brand2);margin-left:6px">{{ data.adventure.profession }} · {{ data.adventure.gender || '男' }}</span>
              </div>
              <div style="font-size:12.5px;color:var(--muted);margin:6px 0 8px">
                {{ data.adventure.realm }} · Lv{{ data.adventure.level }} · {{ data.adventure.heaven }} ·
                灵根 {{ data.adventure.spirit_root || '无' }}{{ data.adventure.element ? ' · '+data.adventure.element : '' }}
              </div>
              <div v-if="data.adventure.portrait_url" class="cultivator-loadout" aria-label="修士立绘与六件装备">
                <div class="equipment-column" v-for="side in [0,1]" :key="side" :class="side===0 ? 'equipment-left' : 'equipment-right'">
                  <article class="equipment-slot" v-for="gear in (data.adventure.equipment || []).slice(side*3,side*3+3)" :key="gear.slot">
                    <img :src="gear.image_url" :alt="gear.name" loading="lazy" decoding="async" width="96" height="96">
                    <div class="equipment-name">{{ gear.name }}</div>
                    <div class="equipment-level">Lv{{ gear.level }} <span>{{ gear.attribute }}</span></div>
                    <div class="equipment-affix">{{ gear.affix }}</div>
                  </article>
                </div>
                <figure class="cultivator-portrait"><img :src="data.adventure.portrait_url" :alt="data.adventure.name + '的修士立绘'" decoding="async"><figcaption>{{ data.adventure.profession }} · {{ data.adventure.gender || '男' }}</figcaption></figure>
              </div>
              <div style="display:flex;gap:8px;flex-wrap:wrap;font-size:12.5px;margin-bottom:8px">
                <el-tag type="danger" effect="plain" round> 总战力 {{ fmt(data.adventure.power) }}</el-tag>
                <span> 气血 {{ fmt(data.adventure.hp) }}/{{ data.adventure.hp_max }}</span>
                  <span v-if="data.adventure.hp_dead" style="color:#c0392b"> 陨落</span>
                <el-tag effect="plain" round> 攻击 {{ fmt(data.adventure.atk) }}</el-tag>
                <el-tag effect="plain" round> 防御 {{ fmt(data.adventure.defense) }}</el-tag>
                <el-tag effect="plain" round> 速度 {{ fmt(data.adventure.speed) }}</el-tag>
                <el-tag type="warning" effect="plain" round>悟性 {{ data.adventure.wudao }} · 根骨 {{ data.adventure.gengu }}</el-tag>
              </div>
              <div style="margin-bottom:10px;font-size:12.5px;color:#50604a">
                 道侣：<b>{{ data.adventure.partner.love_state }}</b>{{ data.adventure.partner.married ? ' · 已婚于『'+data.adventure.partner.pet_name+'』' : ' · 结契灵宠『'+data.adventure.partner.pet_name+'』' }}
                <span style="margin-left:10px"> 剩余悟性点 <b>{{ data.adventure.insight }}</b></span>
              </div>
              <div style="display:flex;align-items:center;gap:10px;font-size:12.5px">
                <span style="flex:0 0 50px">体力</span>
                <el-progress :percentage="pct(data.adventure.stamina, data.adventure.stamina_max)" :stroke-width="10" :show-text="false" color="#286356" style="flex:1"></el-progress>
                <span style="flex:0 0 auto">{{ data.adventure.stamina }} / {{ data.adventure.stamina_max }}</span>
              </div>
              <div v-if="data.adventure.tactics && data.adventure.tactics.length" style="margin-top:8px;font-size:12.5px;color:var(--muted)">
                 已悟神通：<span v-for="(t,i) in data.adventure.tactics" :key="i"><b>{{ t[0] }}</b><span v-if="t[1]">（{{ t[1] }}）</span><span v-if="i < data.adventure.tactics.length-1">、 </span></span>
              </div>
              <details class="power-detail"><summary>查看战力构成</summary><div>
                <b>战力构成</b>：本体 {{ fmt(data.adventure.breakdown.hero) }} ＋
                灵宠「{{ data.adventure.breakdown.pet_name }}」{{ fmt(data.adventure.breakdown.pet_power) }} × {{ (data.adventure.breakdown.pet_ratio*100).toFixed(1) }}%
                （随修士等级，满级 {{ (data.adventure.breakdown.pet_ratio_max*100).toFixed(0) }}%）＝ {{ fmt(data.adventure.breakdown.pet_contrib) }} ＋
                坐骑 {{ fmt(data.adventure.breakdown.mount_power) }} × {{ Math.round(data.adventure.breakdown.mount_ratio*100) }}%
                ＝ {{ fmt(data.adventure.breakdown.mount_contrib) }}
                × 道侣 ×{{ data.adventure.breakdown.partner.toFixed(2) }} ·
                洞天 ×{{ data.adventure.breakdown.heaven_margin.toFixed(2) }}
                → <b>{{ fmt(data.adventure.breakdown.total) }}</b>
              </div></details>
            </div>
          </div>
        </div>
      </template>


          <div v-if="!data.adventure" class="card overview-welcome"><h2>{{ pet && pet.exists ? pet.nickname : '角色档案' }}</h2><p class="muted">{{ pet && pet.exists ? '灵宠已结契，前往灵宠页查看属性、养成与自动助手。' : '当前角色尚未创建修士。可在群内查看玩法指引。' }}</p><el-button v-if="pet && pet.exists" @click="activePanel='pet'">查看灵宠</el-button></div>
      <div class="sec-title">修行资源</div>
        <div class="wallet">
          <div class="coin"><div class="label"> 灵石</div><div class="value">{{ fmt(data.coin) }}</div></div>
          <div class="coin"><div class="label"> 玄晶</div><div class="value">{{ fmt(data.jifen) }}</div></div>
          <div class="coin"><div class="label"> 天晶</div><div class="value">{{ fmt(data.diamond) }}</div></div>
          <div class="coin"><div class="label"> 深渊结晶</div><div class="value">{{ fmt(data.abyss && data.abyss.crystal || 0) }}</div></div>
        </div>

        <div class="sec-title">活动冷却</div>
        <div class="cd-grid" v-if="cooldowns.length">
          <div v-for="c in cooldowns" :key="c.name" class="cd" :class="cdRemaining(c) > 0 ? 'busy' : 'ready'">
            <div class="cd-name">{{ c.name }}</div>
            <div class="cd-time">{{ cdRemaining(c) > 0 ? fmtCd(cdRemaining(c)) : '可用' }}</div>
          </div>
        </div>
        <div v-else class="card empty-tip">暂无活动</div>


        </section>
        <section v-show="activePanel==='pet'" aria-label="灵宠档案"><h2 class="sec-title first-title">我的灵宠</h2>
    <div v-if="slotPets.length" class="pet-selector" aria-label="当前修士的灵宠">
      <button type="button" v-for="(p,i) in slotPets" :key="'pet'+i" class="pet-option"
           :class="{active: current && current.group_id===p.group_id && current.qq===p.qq && (current.pet_index||0)===(p.pet_index||0)}"
           @click="loadPet(p)" :aria-pressed="!!(current && (current.pet_index||0)===(p.pet_index||0))">
        <img :src="p.image_url || blankImg" alt="">
        <div class="info">
          <div class="name">{{ p.nickname }}</div>
          <div class="sub">Lv{{ p.level }} · {{ p.quality }}</div>
        </div>
      </button>
    </div>

        <div class="card" v-if="pet && pet.exists">
          <div class="pet-hero">
            <img class="pet-img" :src="pet.image_url || blankImg" :alt="pet.custom_species_name || pet.species || '宠物'">
            <div class="pet-head">
              <div class="pet-name">{{ pet.nickname || '未命名' }} <span class="lv">Lv{{ pet.level }}</span></div>
              <div class="pet-meta">{{ pet.custom_species_name || pet.species || '未知' }} · {{ pet.quality }} · {{ pet.stage }} · {{ pet.element_cn }}</div>
              <div class="pet-tags" v-if="pet.tags && pet.tags.length">
                <el-tag v-for="t in pet.tags" :key="t" size="small" effect="light" round>{{ t }}</el-tag>
              </div>
              <div class="pet-bars">
                <div class="bar-row">
                  <span class="bl">{{ pet.ascended ? '仙元' : '经验' }}</span>
                  <el-progress :percentage="pct(pet.ascended ? pet.xianyuan : pet.exp, pet.exp_to_next)" :stroke-width="10" :show-text="false" color="#8b7350"></el-progress>
                  <span class="bv">{{ fmt(pet.ascended ? (pet.xianyuan||0) : (pet.exp||0)) }} / {{ fmt(pet.exp_to_next||0) }}</span>
                </div>
                <div class="bar-row">
                  <span class="bl"> 生命</span>
                  <el-progress :percentage="pct(pet.hp, pet.hp_max)" :stroke-width="10" :show-text="false" color="#a54132"></el-progress>
                  <span class="bv">{{ fmt(pet.hp||0) }} / {{ fmt(pet.hp_max||0) }}</span>
                </div>
                <div class="bar-row">
                  <span class="bl"> 精力</span>
                  <el-progress :percentage="pct(pet.energy, pet.energy_max)" :stroke-width="10" :show-text="false" color="#a48148"></el-progress>
                  <span class="bv">{{ fmt(pet.energy||0) }} / {{ fmt(pet.energy_max||0) }}</span>
                </div>
              </div>
              <div class="pet-badges">
                <el-tag type="danger" effect="plain" round> 战力 {{ fmt(pet.battle_power) }}</el-tag>
                <el-tag type="warning" effect="plain" round> 心情 {{ fmt(pet.mood||0) }}</el-tag>
                <el-tag v-if="pet.ascended" type="success" effect="plain" round>余 {{ fmt(pet.exp||0) }} 经验</el-tag>
              </div>
            </div>
          </div>
          <div class="stat-grid">
            <div class="stat"><div class="label">攻击</div><div class="value">{{ fmt(pet.atk||0) }}</div></div>
            <div class="stat"><div class="label">防御</div><div class="value">{{ fmt(pet.def||0) }}</div></div>
            <div class="stat"><div class="label">智力</div><div class="value">{{ fmt(pet.intel||0) }}</div></div>
            <div class="stat"><div class="label">经验</div><div class="value">{{ fmt(pet.exp||0) }}/{{ fmt(pet.exp_to_next||0) }}</div></div>
            <div class="stat"><div class="label">性别</div><div class="value">{{ pet.gender || '?' }}</div></div>
            <div class="stat"><div class="label">姻缘</div><div class="value">{{ pet.love_state || '单身' }}</div></div>
          </div>

        <div class="sec-title">宠物养成</div>
        <div class="growth-actions">
          <div class="grow-row">
            <el-button type="primary" round :loading="acting==='auto_level'" @click="petAction('auto_level')"> 一键升级</el-button>
            <span class="grow-group">
              <el-input-number v-model="levelTimes" :min="1" :max="9999" size="default"></el-input-number>
              <el-button round :loading="acting==='level'" @click="petAction('level')">⬆ 升级</el-button>
            </span>
            <el-button type="warning" round :loading="acting==='evolve'" @click="petAction('evolve')"> 宠物进化</el-button>
          </div>
          <p class="muted" style="margin-top:9px">效果与群聊指令一致；升级消耗经验与精力，进化需『进化神石』。</p>
        </div>

          <div class="custom-box" v-if="!pet.custom">
            <el-input v-model="custom.code" placeholder="定制卡密" clearable></el-input>
            <p class="muted" style="margin:8px 0 0">输入宠物定制卡密，解锁后该宠物可修改形象和种类名称，品质将晋升为混沌。</p>
            <div class="fld">全群祝贺信息</div>
            <div style="display:flex;gap:8px">
              <el-input v-model="custom.nickname" placeholder="你的 QQ 昵称"></el-input>
              <el-input v-model="custom.showQQ" placeholder="显示 QQ 号"></el-input>
            </div>
            <p class="muted" style="margin:8px 0 10px">填写昵称和 QQ 号用于解锁后向所有授权群发送祝贺，让全服见证你的专属宠物！</p>
            <el-button type="primary" round :loading="custom.redeeming" @click="redeemCustom">解锁定制</el-button>
          </div>
          <div class="custom-box" v-else>
            <div class="custom-badge"> 定制权限已解锁（混沌品质）</div>
            <div class="custom-remaining">本月剩余次数：图片 {{ data.custom_remaining.image }} 次 / 名称 {{ data.custom_remaining.species_name }} 次</div>
            <div style="display:flex;align-items:center;gap:12px;margin:12px 0;flex-wrap:wrap">
              <el-button type="primary" round @click="openCustomEdit">修改形象 / 名称</el-button>
            </div>
            <el-alert v-for="(r,i) in data.custom_pending || []" :key="'p'+i" type="success" :closable="false" style="margin-top:10px"
              :title="'已提交审核，预计 3 个工作日内完成。' + (r.new.species_name ? '名称：'+r.new.species_name+' ' : '') + (r.new.image ? '图片' : '')"></el-alert>
            <el-alert v-for="(r,i) in data.custom_rejected || []" :key="'r'+i" type="error" :closable="false" style="margin-top:10px"
              :title="'审核未通过：' + (r.reason || '未说明原因')"></el-alert>
          </div>
          <!-- 自动助手：门禁只看该玩家有无剩余执行次数，与宠物是否定制无关 -->
          <div class="custom-box">
            <div class="custom-badge"> 自动助手</div>
            <div class="custom-remaining">
              剩余执行次数 <b>{{ data.assistant.quota || 0 }}</b> 次 · 该宠物累计代跑 {{ data.assistant.total_runs || 0 }} 次
            </div>
            <div class="custom-remaining">
              代跑任务：{{ (data.assistant.tasks && data.assistant.tasks.length) ? data.assistant.tasks.join('、') : '尚未选择活动' }}
            </div>
            <div style="display:flex;align-items:center;gap:12px;margin:12px 0;flex-wrap:wrap">
              <div style="display:flex;align-items:center;gap:8px;background:#fff;padding:8px 14px;border-radius:999px;border:1px solid #d8d7c9">
                <span style="font-size:13px;color:#50604a">自动助手</span>
                <el-switch
                  v-model="data.assistant.enabled"
                  :loading="autoCultivating"
                  :disabled="assistantEdit.saving || assistantEdit.open"
                  inline-prompt
                  active-text="开"
                  inactive-text="关"
                  @change="toggleAutoCultivation"
                />
              </div>
              <el-button :disabled="autoCultivating || assistantEdit.saving" @click="openAssistantEditor">{{ data.assistant.tasks && data.assistant.tasks.length ? '修改活动' : '选择活动' }}</el-button>
              <span class="muted" style="font-size:12.5px">{{ data.assistant.free ? '限时免费中，执行活动不扣次数' : '每成功执行 1 个任务扣 1 次，次数用尽自动停机' }}</span>
            </div>
            <div v-if="assistantEdit.open" class="assistant-editor" aria-label="选择自动助手活动">
              <div class="assistant-selection"><strong>已选 {{ assistantEdit.tasks.length }} / {{ data.assistant.max_tasks }} 项</strong><el-button text :disabled="assistantEdit.saving || !assistantEdit.tasks.length" @click="assistantEdit.tasks=[]">清空重选</el-button></div>
              <p class="muted">选择要代跑的活动，再次点击可取消。保存后对当前灵宠生效；清空并保存会停止助手。</p>
              <div class="assistant-options" role="group" aria-label="可选活动">
                <button v-for="option in data.assistant.options" :key="option.key" type="button"
                  :aria-pressed="assistantEdit.tasks.includes(option.key)"
                  :disabled="assistantEdit.saving || (!assistantEdit.tasks.includes(option.key) && assistantEdit.tasks.length>=data.assistant.max_tasks)"
                  @click="selectAssistantTask(option.key)">
                  <strong>{{ option.key }}</strong><span v-if="option.description!==option.key">{{ option.description }}</span>
                </button>
              </div>
              <div class="assistant-editor-actions"><el-button :disabled="assistantEdit.saving" @click="assistantEdit.open=false">取消</el-button><el-button type="primary" :loading="assistantEdit.saving" @click="saveAssistantTasks">保存活动</el-button></div>
            </div>
            <div v-if="data.assistant.enabled" class="custom-remaining assistant-log" style="color:#0c7a45">
               运行中 · 最近：{{ (data.assistant.log && data.assistant.log.length) ? data.assistant.log[data.assistant.log.length-1] : '暂无记录（等待下一次代跑）' }}
            </div>
          </div>
        </div>
        <div v-else class="card empty-tip">该角色暂无灵宠。可在群内领养，或切换到「总览」「坐骑」查看已有角色。</div>

</section>
        <section v-show="activePanel==='mounts'" aria-label="坐骑管理">
        <template v-if="data && data.mounts && data.mounts.length">
        <div class="sec-title"> 我的坐骑</div>
        <div class="card">
          <div class="muted" style="font-size:12px;margin-bottom:8px">含官方与玩家定制的全部坐骑</div>
          <div v-for="m in data.mounts" :key="m.name"
               style="display:flex;align-items:center;gap:12px;padding:10px 12px;border:1px solid var(--line);border-radius:12px;margin-bottom:8px">
            <div style="flex:1;min-width:0">
              <div style="font-size:14px;font-weight:700">{{ m.name }}
                <el-tag v-if="m.custom_spec" type="danger" size="small" effect="light" round style="margin-left:6px">⭐ 玩家定制</el-tag>
              </div>
              <div class="muted" style="font-size:12px;margin-top:2px">{{ m.stars }} 星 · Lv{{ m.level }} · 战力 {{ fmt(m.power) }}</div>
            </div>
          </div>
        </div>
      </template>

<div v-if="!(data.mounts && data.mounts.length)" class="card empty-tip">暂无坐骑。可在群内获取坐骑，或使用定制资格申请专属坐骑。</div>      <template v-if="data && data.mount_custom">
        <div class="card" style="margin-bottom:12px">
          <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
            <span style="font-size:14px;font-weight:700"> 定制坐骑</span>
            <el-tag v-if="(data.mount_custom.slots||0) > 0" type="success" size="small" effect="light" round>
              可用定制资格 ×{{ data.mount_custom.slots }}</el-tag>
            <el-tag v-else type="info" size="small" effect="plain" round>无定制资格</el-tag>
            <span style="flex:1"></span>
            <el-button type="primary" round size="small" @click="openMountNew()">＋ 新建定制坐骑</el-button>
            <el-button round size="small" @click="openMountImage()"> 更换坐骑外观</el-button>
          </div>
          <p class="muted" style="font-size:12px;margin:8px 0 0">定制坐骑 = 自定义名字 + 专属外观图，初始战力 <b>30 万</b>（Lv.1 起可升级，属性不可自定义）。每张「坐骑定制卡」可新建 1 只；外观图经后台人工审核后生效，并全服祝贺广播。</p>
          <el-alert v-for="(pn,i) in (data.mount_custom.pending||[])" :key="'mp'+i" type="warning" :closable="false" style="margin-top:10px"
            :title="'『'+pn+'』外观已提交审核，预计 3 个工作日内处理完毕'"></el-alert>
          <el-alert v-for="(pn,i) in (data.mount_custom.img_pending||[])" :key="'mip'+i" type="warning" :closable="false" style="margin-top:10px"
            :title="'『'+pn+'』外观更换审核中，通过后生效'"></el-alert>
          <el-alert v-if="(data.mount_custom.rejected||[]).length" type="error" :closable="false" style="margin-top:10px"
            :title="'上次定制被驳回：『'+(data.mount_custom.rejected[0].name||'')+'』' + (data.mount_custom.rejected[0].reason||'')"></el-alert>
        </div>
      </template>

</section>
        <section v-show="activePanel==='bag'" aria-label="背包道具">        <div class="sec-title">背包</div>
        <p class="muted" style="margin:-4px 0 10px">道具可直接使用（支持数量），神器可佩戴、秘技书可参悟，效果与群聊指令一致。</p>
        <div class="bag-toolbar"><el-input v-model="bagQuery" placeholder="搜索道具、神器或秘技" aria-label="搜索背包" clearable></el-input><span class="muted">共 {{ filteredBag.length }} 项</span></div>
        <div class="bag" v-if="filteredBag.length">
          <div v-for="it in visibleBag" :key="it.name" class="item">
            <el-tag v-if="it.kind==='art'" class="item-tag" type="danger" size="small" effect="dark" round>神器</el-tag>
            <el-tag v-else-if="it.kind==='skill'" class="item-tag" type="primary" size="small" effect="dark" round>秘技</el-tag>
            <div class="item-name">{{ it.name }}</div>
            <span class="count">持有 ×{{ it.count }} · <a class="info-link" @click="showItemInfo(it)">查看说明</a></span>
            <div class="use-row">
              <el-input-number v-if="it.kind==='item'" v-model="it.qty" :min="1" :max="it.count" size="small"></el-input-number>
              <el-button type="primary" plain size="small" round :loading="usingItem===it.name" @click="useItem(it)">
                {{ it.kind==='art' ? '佩戴' : (it.kind==='skill' ? '参悟' : '使用') }}
              </el-button>
            </div>
          </div>
        </div>
        <div v-else class="card empty-tip">{{ bagQuery ? '未找到匹配的道具，试试其他名称。' : '背包暂无道具。可在群内历练获取，或通过卡密兑换。' }}</div>

        <div class="bag-load-more" v-if="filteredBag.length" ref="bagEnd">
          <span role="status">已显示 {{ visibleBag.length }} / {{ filteredBag.length }} 项{{ bagHasMore ? ' · 向下滚动继续查看' : ' · 已全部显示' }}</span>
          <el-button v-if="bagHasMore" text @click="loadMoreBag">加载更多</el-button>
        </div></section>
        <section v-show="activePanel==='redeem'" aria-label="卡密兑换">        <div class="sec-title">卡密兑换</div>
        <div class="card">
          <div class="redeem-row">
            <el-input v-model="redeemCode" placeholder="输入卡密，可兑换灵石 / 玄晶 / 天晶 / 道具" clearable @keyup.enter="redeem"></el-input>
            <el-button type="primary" round :loading="redeeming" @click="redeem">兑换</el-button>
          </div>
          <div v-if="redeemResult" class="redeem-result">{{ redeemResult }}</div>
        </div>

<p class="muted redeem-note">兑换将用于当前选中的角色。宠物定制请前往「灵宠」，坐骑定制请前往「坐骑」。</p></section>
      </template>
      <footer class="portal-footer"><span>灵契仙途</span><div><a href="/feedback">问题反馈</a><a href="/agreement">用户协议</a><a href="/">官网首页</a></div></footer>
    </div>
  </section>
</div>

<!-- 绑定角色（槽位） -->
<el-dialog v-model="bind.show" title="绑定角色" width="480px" align-center>
  <div v-if="auto.loading" class="muted" style="padding:4px 2px 8px"> 正在自动识别（按登录 QQ {{ auto.qq || '...' }}）…</div>
  <div v-else-if="auto.list && auto.list.length" style="margin-bottom:10px">
    <div style="color:#6b766c;font-size:12px;margin:0 0 6px"> 已按登录 QQ（{{ auto.qq }}）自动列出名下角色：未绑定点「选择」、被其它账号绑定的可「强要回」。每个（群号+用户ID）只需绑定一次。</div>
    <div style="max-height:240px;overflow:auto;border:1px solid rgba(255,255,255,.08);border-radius:8px">
      <div v-for="grp in auto.list" :key="grp.group_id" style="padding:8px;border-bottom:1px solid rgba(255,255,255,.06)">
        <div style="font-size:12px;color:#50604a;margin-bottom:2px"><b>群 ID</b> <code>{{ grp.group_id }}</code></div>
        <div v-for="pl in grp.players" :key="grp.group_id + '|' + pl.qq" style="padding:6px 0 6px 10px">
          <div style="display:flex;align-items:center;gap:8px;font-size:12px;color:#233b36">
            <span style="flex:1;min-width:0">用户ID <code>{{ pl.qq }}</code>
              <span style="color:#6b766c">{{ pl.has_adventure ? '修士' + (pl.adventure_name ? '·' + pl.adventure_name : '') + ' ' : '' }}{{ pl.pet_count ? '宠物'+pl.pet_count+'只 ' : '' }}{{ pl.mount_count ? '坐骑'+pl.mount_count+'只' : '' }}</span>
            </span>
            <el-button v-if="pl.bound==='other'" size="small" round plain type="danger" @click="reclaimBind(grp.group_id, pl.qq, 0)">强要回</el-button>
            <el-button v-else-if="pl.bound==='me'" size="small" round plain disabled>已绑定</el-button>
            <el-button v-else size="small" round plain type="primary" @click="pickAuto(grp.group_id, pl.qq, 0)">选择</el-button>
          </div>
        </div>
      </div>
    </div>
  </div>
  <div v-else-if="!auto.loading" class="muted" style="padding:4px 2px 8px">未找到可通过登录 QQ（{{ auto.qq || '未绑定QQ' }}）自动匹配的角色，请在下方手动输入群号与用户 ID。</div>
  <el-form label-position="top" @submit.prevent="doBindQuery">
    <el-form-item label="群号">
      <el-input v-model="bind.group" placeholder="角色所在的 QQ 群号" clearable></el-input>
    </el-form-item>
    <el-form-item label="用户ID / QQ">
      <el-input v-model="bind.qq" placeholder="你在该群使用灵契仙途的用户 ID" clearable @keyup.enter="doBindQuery"></el-input>
    </el-form-item>
  </el-form>
  <div v-if="bind.info && !bind.querying" style="padding:10px 12px;border-radius:10px;border:1px solid rgba(103,194,58,.35);background:rgba(103,194,58,.08);font-size:13px;color:#286356">
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      <span>将绑定：{{ bind.info.has_adventure ? '修士' + (bind.info.adventure.name ? '『' + bind.info.adventure.name + '』' : '') + ' ' : '' }}{{ (bind.info.pets||[]).length ? '宠物 ' + (bind.info.pets||[]).length + ' 只 ' : '' }}{{ (bind.info.mounts||[]).length ? '坐骑 ' + (bind.info.mounts||[]).length + ' 只' : '' }}</span>
    </div>
  </div>
  <div v-if="bind.error" style="padding:8px 10px;border-radius:8px;background:rgba(245,108,108,.12);border:1px solid rgba(245,108,108,.3);color:#a54132;font-size:12.5px;margin-bottom:4px">{{ bind.error }}</div>
  <template #footer>
    <el-button round @click="bind.show=false">取消</el-button>
    <el-button type="primary" round :loading="bind.querying" @click="doBindQuery">查询角色</el-button>
    <el-button v-if="bind.info" type="success" round :loading="bind.loading" @click="doBind">绑定该角色</el-button>
  </template>
</el-dialog>

<!-- 新建定制坐骑 -->
<el-dialog v-model="mountC.dialog" title=" 新建定制坐骑" width="500px" align-center>
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
    <span style="font-size:13.5px">可用定制资格</span>
    <el-tag v-if="(data.mount_custom && data.mount_custom.slots) > 0" type="success" round>×{{ data.mount_custom.slots }}</el-tag>
    <el-tag v-else type="info" round>无</el-tag>
    <span style="flex:1"></span>
    <span style="font-size:12px;color:var(--muted)">初始战力 300,000 · Lv.1</span>
  </div>
  <div v-if="!((data.mount_custom && data.mount_custom.slots) > 0)" style="padding:12px;border-radius:10px;border:1px solid #d8d7c9;background:#eef0e5;margin-bottom:12px">
    <div style="font-size:13px;margin-bottom:8px">还没有定制资格，输入「坐骑定制卡」卡密兑换（每张可新建 1 只）：</div>
    <div style="display:flex;gap:8px">
      <el-input v-model="mountC.code" placeholder="坐骑定制卡密" clearable @keyup.enter="doMountRedeem"></el-input>
      <el-button type="primary" round :loading="mountC.redeeming" @click="doMountRedeem">兑换资格</el-button>
    </div>
  </div>
  <el-form v-if="(data.mount_custom && data.mount_custom.slots) > 0" label-position="top">
    <el-form-item label="定制坐骑名称（1~8 字，不可与官方坐骑重名）">
      <el-input v-model="mountC.name" maxlength="8" placeholder="例如：混沌·青龙" clearable></el-input>
    </el-form-item>
    <el-form-item label="专属外观图（JPG / PNG / GIF / WebP，≤5MB；群聊入场与坐骑卡片均显示）">
      <div class="upload-zone" @click="pickMountImage">
        <div class="upload-plus">＋</div>
        <div class="upload-text">{{ mountC.file ? '已选择：' + mountC.file.name : '点击上传外观图' }}</div>
        <div class="upload-hint">建议正方形或透明底；人工审核通过后生效</div>
      </div>
      <input ref="mountFileInput" type="file" accept=".jpg,.jpeg,.png,.gif,.webp,image/*" style="display:none" @change="onMountFile">
      <div v-if="mountC.preview" class="crop-preview"><img :src="mountC.preview" alt="定制坐骑预览"></div>
    </el-form-item>
    <el-form-item label="全群祝贺信息（审核通过后将向所有授权群发送祝贺，可不填）">
      <div style="display:flex;gap:8px">
        <el-input v-model="mountC.nickname" maxlength="32" placeholder="你的 QQ 昵称（用于广播）" clearable></el-input>
        <el-input v-model="mountC.showQQ" maxlength="32" placeholder="显示 QQ 号" clearable></el-input>
      </div>
    </el-form-item>
  </el-form>
  <template #footer>
    <el-button round @click="mountC.dialog=false">关闭</el-button>
    <el-button v-if="(data.mount_custom && data.mount_custom.slots) > 0" type="primary" round :disabled="!mountC.name || !mountC.file" :loading="mountC.submitting" @click="doMountSubmit">提交审核</el-button>
  </template>
</el-dialog>

<!-- 更换定制坐骑外观 -->
<el-dialog v-model="mountI.dialog" title=" 更换坐骑外观" width="500px" align-center>
  <div style="padding:10px 12px;border-radius:10px;border:1px solid #d8d7c9;background:#eef0e5;font-size:13px;margin-bottom:12px">
    每只定制坐骑<b>每月可更换 3 次外观</b>；新图经人工审核通过后生效，旧图自动替换。
  </div>
  <div v-if="!customMountList.length" style="padding:14px;color:var(--muted);font-size:13px">当前角色还没有定制坐骑。先『新建定制坐骑』吧。</div>
  <el-form v-else label-position="top">
    <el-form-item label="选择定制坐骑">
      <el-select v-model="mountI.name" placeholder="选择要更换外观的定制坐骑" style="width:100%">
        <el-option v-for="m in customMountList" :key="m.name" :value="m.name" :label="m.name + '（本月剩余 ' + m.remaining + ' 次）'" :disabled="m.remaining <= 0 || isMountImgPending(m.name)"></el-option>
      </el-select>
    </el-form-item>
    <el-form-item label="新外观图（JPG / PNG / GIF / WebP，≤5MB）">
      <div class="upload-zone" @click="pickMountImgFile">
        <div class="upload-plus">＋</div>
        <div class="upload-text">{{ mountI.file ? '已选择：' + mountI.file.name : '点击上传新外观图' }}</div>
        <div class="upload-hint">建议正方形或透明底；动态图（GIF/WebP）会保留动画</div>
      </div>
      <input ref="mountImgFileInput" type="file" accept=".jpg,.jpeg,.png,.gif,.webp,image/*" style="display:none" @change="onMountImgFile">
      <div v-if="mountI.preview" class="crop-preview"><img :src="mountI.preview" alt="新外观预览"></div>
    </el-form-item>
  </el-form>
  <template #footer>
    <el-button round @click="mountI.dialog=false">关闭</el-button>
    <el-button v-if="customMountList.length" type="primary" round :disabled="!mountI.name || !mountI.file" :loading="mountI.submitting" @click="doMountImgSubmit">提交审核</el-button>
  </template>
</el-dialog>

<!-- 修改密码 -->
<el-dialog v-model="pwd.show" title=" 修改密码" width="420px" align-center>
  <p class="muted" style="margin:-6px 0 10px">验证码将发送至绑定邮箱{{ account && account.email_masked ? '：' + account.email_masked : '' }}</p>
  <el-form label-position="top" @submit.prevent="changePwd">
    <el-form-item label="邮箱验证码">
      <div style="display:flex;gap:10px;width:100%">
        <el-input v-model="pwd.code" placeholder="6 位验证码" maxlength="6" style="flex:1"></el-input>
        <el-button round plain :disabled="pwd.countdown>0" :loading="pwd.sending" @click="sendPwdCode" style="white-space:nowrap">{{ pwd.countdown>0 ? pwd.countdown + 's' : '获取验证码' }}</el-button>
      </div>
    </el-form-item>
    <el-form-item label="新密码">
      <el-input v-model="pwd.n1" type="password" show-password placeholder="至少 6 位"></el-input>
    </el-form-item>
    <el-form-item label="确认新密码">
      <el-input v-model="pwd.n2" type="password" show-password placeholder="再次输入新密码" @keyup.enter="changePwd"></el-input>
    </el-form-item>
  </el-form>
  <template #footer>
    <el-button round @click="pwd.show=false">取消</el-button>
    <el-button type="primary" round :loading="pwd.loading" @click="changePwd">确认修改</el-button>
  </template>
</el-dialog>

<!-- 修改宠物形象 -->
<el-dialog v-model="custom.editShow" title=" 修改宠物形象" width="480px" align-center>
  <p class="muted" style="margin:-6px 0 4px">定制专属形象与种类名称，审核通过后生效。</p>
  <label class="fld">种类名称（显示名称）</label>
  <el-input v-model="custom.species" placeholder="例如：灭世魔龙" clearable></el-input>
  <label class="fld">宠物图片</label>
  <input ref="customFileInput" type="file" accept="image/*" style="display:none" @change="pickCustomImage">
  <div class="upload-zone" @click="$refs.customFileInput.click()">
    <div class="upload-plus">＋</div>
    <div class="upload-text">点击选择图片</div>
    <div class="upload-hint">支持 jpg / png / gif / webp，将裁剪为 512×512</div>
  </div>
  <div class="crop-preview" v-if="custom.previewUrl"><img :src="custom.previewUrl" alt="预览"></div>
  <p class="muted" style="margin-top:8px">每月图片和名称各限 3 次，提交后需管理员审核，预计 3 个工作日内完成。审核期间无法再次提交。</p>
  <template #footer>
    <el-button round @click="custom.editShow=false">取消</el-button>
    <el-button type="primary" round :loading="custom.submitting" @click="submitCustom">提交审核</el-button>
  </template>
</el-dialog>

<!-- 裁剪图片 -->
<el-dialog v-model="crop.show" title="裁剪图片" width="580px" align-center top="4vh" :close-on-click-modal="false">
  <div class="crop-wrap">
    <canvas id="cropCanvas" width="512" height="512"
      @mousedown="cropDown" @mousemove="cropMove" @mouseup="cropUp" @mouseleave="cropUp"
      @touchstart.prevent="cropTouchStart" @touchmove.prevent="cropTouchMove" @touchend="cropUp"></canvas>
  </div>
  <div class="crop-zoom">
    <span class="muted">缩小</span>
    <el-slider v-model="crop.zoom" :min="100" :max="300" @input="applyZoom"></el-slider>
    <span class="muted">放大</span>
  </div>
  <p class="muted">拖动图片调整位置，滑动缩放，最终输出 512×512。</p>
  <template #footer>
    <el-button round @click="crop.show=false">取消</el-button>
    <el-button type="primary" round @click="saveCrop">保存裁剪</el-button>
  </template>
</el-dialog>
</div>

<script src="/webstatic/vue.global.prod.js"></script>
<script src="/webstatic/element-plus.full.min.js"></script>
<script src="/webstatic/element-plus-zh-cn.min.js"></script>
<script>
const CSRF_TOKEN = '{{CSRF_TOKEN}}';
const { createApp, reactive, ref, computed, onMounted, onUnmounted, watch, nextTick } = Vue;
const { ElMessage, ElMessageBox } = ElementPlus;

const BLANK_IMG = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';

async function api(path, method='GET', body=null){
  const opts = {method, headers:{'X-CSRF-Token':CSRF_TOKEN}};
  if(body){ opts.headers['Content-Type']='application/json'; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts);
  if(r.status === 401 || r.status === 403){ location.href = '/'; return null; }
  return r.json().catch(()=>null);
}

function stripMd(s){ return String(s).replace(/^#+\s*/gm,'').replace(/\*\*/g,'').replace(/`/g,'').replace(/━+/g,'').replace(/\n{3,}/g,'\n\n').trim(); }

function showResult(r, fallback){
  if(!r){ ElMessage.error(fallback || '操作失败'); return; }
  const text = stripMd(r.msg || (r.ok ? '操作成功' : fallback || '操作失败'));
  if(text.length > 64 || text.includes('\n')){
    ElMessageBox.alert(`<div class="result-pre">${text.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])).replace(/\n/g,'<br>')}</div>`,
      r.ok ? '操作成功' : '操作失败', {dangerouslyUseHTMLString:true, confirmButtonText:'知道了', type: r.ok ? 'success' : 'warning'});
  } else {
    r.ok ? ElMessage.success(text) : ElMessage.error(text);
  }
}

createApp({
  setup(){
    const account = ref(null);
    const pets = ref([]);
    const slots = ref([]);
    const current = ref(null);
    const currentSlot = ref(null);
    const data = ref(null);
    const pet = computed(()=> data.value ? data.value.pet : null);
    const slotPets = computed(()=> {
      const c = currentSlot.value;
      if(!c) return [];
      return (pets.value||[]).filter(p=>p.group_id===c.group_id && p.qq===c.qq);
    });
    function slotLabel(s){
      if(s.adventure && s.adventure.name) return '修士 · ' + s.adventure.name;
      return '角色 · ' + s.qq;
    }
    function slotSub(s){
      const parts=[];
      if(s.adventure && s.adventure.name) parts.push((s.adventure.realm||'') + ' Lv' + (s.adventure.level||1));
      parts.push('群 ' + s.group_id);
      return parts.join(' · ');
    }
    const petLoading = ref(false);
    const initialLoading=ref(true), loadError=ref(''), roleMenu=ref(false), activePanel=ref('overview');
    const panelTabs=[
      {key:'overview',label:'总览',icon:'M3 11 12 3l9 8M5 10v11h14V10M9 21v-7h6v7'},
      {key:'pet',label:'灵宠',icon:'M8 12c-4 4-5 8 0 8l4-1 4 1c5 0 4-4 0-8-2-2-6-2-8 0ZM4 6v3M9 3v4M15 3v4M20 6v3'},
      {key:'mounts',label:'坐骑',icon:'M4 20 7 8l6-5 6 4v5l-5-1-2 9M7 12h7M15 7h1M7 3v5'},
      {key:'bag',label:'背包',icon:'M5 7h14l2 14H3L5 7ZM8 7V5a4 4 0 0 1 8 0v2M8 12h8v5H8Z'},
      {key:'redeem',label:'兑换',icon:'M3 8h18v4H3ZM5 12v9h14v-9M12 8v13M12 8C3 8 5 1 9 3l3 5ZM12 8c9 0 7-7 3-5l-3 5Z'}
    ];
    function selectPanel(key){
      activePanel.value=key;roleMenu.value=false;
      if(document.documentElement.dataset.ui==='mobile')nextTick(()=>window.scrollTo({top:0,behavior:'instant'}));
    }
    const bagQuery=ref(''),bagLimit=ref(10),bagEnd=ref(null);
    const filteredBag=computed(()=>bagItems.value.filter(it=>it.name.toLocaleLowerCase().includes(bagQuery.value.trim().toLocaleLowerCase())));
    const visibleBag=computed(()=>filteredBag.value.slice(0,bagLimit.value));
    const bagHasMore=computed(()=>visibleBag.value.length<filteredBag.value.length);
    let petRequest=0;
    const now = ref(Math.floor(Date.now()/1000));
    setInterval(()=>{ now.value = Math.floor(Date.now()/1000); }, 1000);

    const levelTimes = ref(1);
    const acting = ref('');
    const usingItem = ref('');
    const redeemCode = ref('');
    const redeeming = ref(false);
    const redeemResult = ref('');
    const bagItems = ref([]);
    function loadMoreBag(){
      if(activePanel.value!=='bag' || petLoading.value || !bagHasMore.value)return;
      bagLimit.value=Math.min(bagLimit.value+10,filteredBag.value.length);
    }
    const bagObserver=typeof IntersectionObserver==='undefined' ? null : new IntersectionObserver(entries=>{
      if(entries.some(entry=>entry.target===bagEnd.value && entry.isIntersecting))loadMoreBag();
    }, {rootMargin:'0px 0px 180px 0px'});
    watch(bagQuery,()=>{bagLimit.value=10;});
    watch([bagEnd,activePanel,petLoading,()=>visibleBag.value.length,()=>filteredBag.value.length],()=>{
      bagObserver?.disconnect();
      if(bagEnd.value && activePanel.value==='bag' && !petLoading.value && bagHasMore.value){
        bagObserver?.observe(bagEnd.value);
      }
    }, {flush:'post'});
    onUnmounted(()=>bagObserver?.disconnect());
    const cooldowns = ref([]);
    const autoCultivating = ref(false);
    const assistantEdit=reactive({open:false,tasks:[],saving:false});

    const bind = reactive({show:false, group:'', qq:'', loading:false, querying:false, info:null, error:'', petIndex:0});
    const auto = reactive({loading:false, list:null, qq:''});
    const pwd = reactive({show:false, code:'', n1:'', n2:'', loading:false, sending:false, countdown:0});
    let pwdCdTimer = null;
    const custom = reactive({code:'', nickname:'', showQQ:'', redeeming:false,
      editShow:false, species:'', blob:null, previewUrl:'', submitting:false});
    const crop = reactive({show:false, zoom:100});
    const cropState = {img:null, scale:1, base:1, x:0, y:0, dragging:false, lastX:0, lastY:0};

    const fmt = n => Number(n||0).toLocaleString('zh-CN');
    const pct = (v,m) => { m = Number(m)||0; if(m<=0) return 0; return Math.max(0, Math.min(100, Math.round(Number(v||0)/m*100))); };
    const fmtDate = ts => new Date((ts||0)*1000).toLocaleString('zh-CN',{hour12:false});
    function fmtCd(sec){
      sec = Math.max(0, Math.floor(sec));
      if(sec >= 3600) return `${Math.floor(sec/3600)}时${Math.floor(sec%3600/60)}分`;
      if(sec >= 60) return `${Math.floor(sec/60)}分${sec%60}秒`;
      return `${sec}秒`;
    }
    const cdRemaining = c => (c.ready_at || 0) - now.value;

    async function init(){
      initialLoading.value=true;loadError.value='';
      try{
      const me = await api('/api/portal/me');
      if(!me || !me.ok){ location.href = '/'; return; }
      account.value = me.account;
      pets.value = me.bound_pets || [];
      slots.value = me.slots || [];
      if(location.hash === '#feedback'){
        location.href = '/feedback';
        return;
      }
      const first = slots.value[0];
      if(!first){ currentSlot.value = null; data.value = null; return; }
      if(first.pets && first.pets.length){
        await loadPet(first.pets[0]);
      } else {
        await loadPet({group_id:first.group_id, qq:first.qq, pet_index:0});
      }
      }catch(error){loadError.value='连接未完成，请检查网络后重试。';}
      finally{initialLoading.value=false;}
    }

    async function loadPet(p){
      const requestId=++petRequest;
      assistantEdit.open=false;
      const roleChanged=!current.value || current.value.group_id!==p.group_id || current.value.qq!==p.qq;
      if(roleChanged){bagLimit.value=10;bagQuery.value='';}
      loadError.value='';
      current.value = p;
      currentSlot.value = {group_id: p.group_id, qq: p.qq};
      petLoading.value = true;
      try{
        const d = await api(`/api/portal/pet?group_id=${encodeURIComponent(p.group_id)}&qq=${encodeURIComponent(p.qq)}&pet_index=${p.pet_index||0}`);
        if(requestId!==petRequest)return;
        if(!d || !d.ok){ loadError.value=(d && d.msg)||'角色数据加载失败，请重试。'; data.value=null; return; }
        data.value = d;
        redeemResult.value = '';
        const artSet = new Set(d.artifact_names || []);
        const skillSet = new Set(d.skill_names || []);
        bagItems.value = Object.entries(d.bag || {}).map(([name,count])=>({
          name, count,
          kind: artSet.has(name) ? 'art' : (skillSet.has(name) ? 'skill' : 'item'),
          qty: 1,
        }));
        const base = Math.floor(Date.now()/1000);
        cooldowns.value = (d.cooldowns || []).map(c=>({name:c.name, ready_at: base + (c.remaining||0)}));
      }catch(error){if(requestId===petRequest)loadError.value='连接未完成，请检查网络后重试。';}
      finally {if(requestId===petRequest)petLoading.value=false;}
    }

    function switchSlot(s){
      if(!s) return;
      if(s.pets && s.pets.length){ loadPet(s.pets[0]); }
      else { loadPet({group_id:s.group_id, qq:s.qq, pet_index:0}); }
    }

    async function refreshAll(){
      const me = await api('/api/portal/me');
      if(me && me.ok){ account.value = me.account; pets.value = me.bound_pets || []; slots.value = me.slots || []; }
      if(current.value) await loadPet(current.value);
    }

    function openAssistantEditor(){
      const assistant=data.value?.assistant;
      if(!assistant || assistantEdit.saving)return;
      const options=new Set((assistant.options||[]).map(option=>option.key));
      assistantEdit.tasks=(assistant.tasks||[]).filter(key=>options.has(key)).slice(0,assistant.max_tasks);
      assistantEdit.open=true;
    }
    function selectAssistantTask(key){
      if(assistantEdit.saving)return;
      const index=assistantEdit.tasks.indexOf(key);
      if(index>=0)assistantEdit.tasks.splice(index,1);
      else if(assistantEdit.tasks.length<data.value.assistant.max_tasks)assistantEdit.tasks.push(key);
    }
    async function saveAssistantTasks(){
      if(!current.value || assistantEdit.saving)return;
      const p=current.value,requestId=petRequest;
      assistantEdit.saving=true;
      try{
        const r=await api('/api/portal/assistant','POST',{
          group_id:p.group_id,qq:p.qq,pet_index:p.pet_index||0,tasks:[...assistantEdit.tasks],
        });
        if(requestId!==petRequest)return;
        if(r && r.ok){
          data.value.assistant=r.assistant;
          assistantEdit.open=false;
          ElMessage.success(r.msg||'活动已保存');
        }else ElMessage.error(r?.msg||'保存失败，请重试');
      }catch(error){if(requestId===petRequest)ElMessage.error('连接未完成，未确认保存，请刷新档案后重试');}
      finally{assistantEdit.saving=false;}
    }
    async function toggleAutoCultivation(enabled){
      if(!data.value) return;
      const requestId=petRequest;
      const p = current.value;
      autoCultivating.value = true;
      try{
        const r = await api('/api/portal/assistant','POST',{
          group_id: p.group_id,
          qq: p.qq,
          pet_index: p.pet_index || 0,
          enabled: Boolean(enabled),
        });
        if(requestId!==petRequest)return;
        if(r && r.ok){
          ElMessage.success(r.msg || '设置成功');
          if(data.value) data.value.assistant = r.assistant || data.value.assistant;
        } else {
          ElMessage.error((r && r.msg) || '设置失败');
          // 回滚开关状态：重新加载宠物数据
          await loadPet(p);
        }
      } catch(error){
        if(requestId===petRequest){
          ElMessage.error('连接未完成，请检查助手状态后重试');
          await loadPet(p);
        }
      } finally { autoCultivating.value = false; }
    }

    async function logout(){
      await api('/api/portal/logout','POST');
      ElMessage.success('已退出登录');
      setTimeout(()=>{ location.href = '/'; }, 400);
    }

    // ---- 绑定 ----
    async function openBind(){
      bind.show = true; bind.info = null; bind.error=''; bind.petIndex = 0;
      await doBindAuto();
    }
    async function doBindAuto(){
      auto.loading = true; auto.list = null; auto.qq = '';
      try{
        const r = await api('/api/portal/bind/auto','POST',{});
        if(r && r.ok){ auto.list = r.groups || []; auto.qq = r.qq || ''; }
        else { ElMessage.error((r && r.msg) || '自动识别失败'); auto.list = []; }
      } finally { auto.loading = false; }
    }
    async function pickAuto(g, q, idx){
      bind.group = g; bind.qq = q; bind.info = null; bind.error=''; bind.petIndex = idx || 0;
      await doBind();
    }
    async function reclaimBind(g, q, idx){
      if(!g || !q){ return; }
      try{ await ElMessageBox.confirm('确定要强行要回该角色的绑定权吗？该 (群号+用户ID) 下的修士/宠物/坐骑将改绑到你的账号。','强要确认',{type:'warning'}); }
      catch(e){ return; }
      const r = await api('/api/portal/bind/reclaim','POST',{group_id:g, qq:q, pet_index:idx||0});
      if(r && r.ok){ ElMessage.success(r.msg || '已强行要回绑定权'); await init(); await doBindAuto(); }
      else { ElMessage.error((r && r.msg) || '强要失败'); }
    }
    async function doBindQuery(){
      const g = bind.group.trim(), q = bind.qq.trim();
      if(!g || !q){ ElMessage.warning('群号和用户 ID 不能为空'); return; }
      bind.querying = true; bind.info = null; bind.error=''; bind.petIndex = 0;
      try{
        const r = await api('/api/portal/bind/query','POST',{group_id:g, qq:q});
        if(r && r.ok){ bind.info = r; }
        else if(r && r.already_bound){ bind.error = r.msg || '该角色已被绑定'; }
        else { bind.error = (r && r.msg) || '查询失败'; }
      } finally { bind.querying = false; }
    }
    async function doBind(){
      const g = bind.group.trim(), q = bind.qq.trim();
      if(!g || !q){ ElMessage.warning('群号和用户 ID 不能为空'); return; }
      bind.loading = true;
      try{
        const r = await api('/api/portal/bind','POST',{group_id:g, qq:q, pet_index:bind.petIndex||0});
        if(r && r.ok){ ElMessage.success(r.msg || '绑定成功'); bind.show=false; bind.group=''; bind.qq=''; bind.info=null; bind.error=''; bind.petIndex=0; await init(); }
        else { ElMessage.error((r && r.msg) || '绑定失败'); }
      } finally { bind.loading = false; }
    }

    // ---- 坐骑外观定制 ----
    const mountFileInput = ref(null);
    const mountC = reactive({dialog:false, name:'', nickname:'', showQQ:'', code:'', redeeming:false, file:null, preview:'', submitting:false});
    function openMountNew(){ mountC.name=''; mountC.nickname=''; mountC.showQQ=''; mountC.code=''; mountC.file=null; mountC.preview=''; mountC.dialog = true; }
    function currentSlotId(){ return (data.value && {group_id:data.value.group_id, qq:data.value.qq}) || (currentSlot.value || {}); }
    function pickMountImage(){ if(!mountFileInput.value) return; mountFileInput.value.click(); }
    function onMountFile(e){
      const f = e.target.files && e.target.files[0];
      if(!f) return;
      if(!/\.(jpe?g|png|gif|webp)$/i.test(f.name)){ ElMessage.error('仅支持 jpg/png/gif/webp 图片'); e.target.value=''; return; }
      if(f.size > 5*1024*1024){ ElMessage.error('图片不能超过 5MB'); e.target.value=''; return; }
      mountC.file = f;
      mountC.preview = URL.createObjectURL(f);
    }
    async function doMountRedeem(){
      const id = currentSlotId();
      if(!id.group_id || !id.qq){ ElMessage.warning('请先绑定并选择角色'); return; }
      if(!mountC.code.trim()){ ElMessage.warning('请输入坐骑定制卡密'); return; }
      mountC.redeeming = true;
      try{
        const r = await api('/api/portal/mount_custom_redeem','POST',{group_id:id.group_id, qq:id.qq, code:mountC.code.trim()});
        if(r && r.ok){ ElMessage.success(r.msg || '兑换成功'); if(r.mount_custom) data.value.mount_custom = r.mount_custom; mountC.code=''; }
        else { ElMessage.error((r && r.msg) || '兑换失败'); }
      } finally { mountC.redeeming = false; }
    }
    async function doMountSubmit(){
      const id = currentSlotId();
      const name = mountC.name.trim();
      if(!id.group_id || !id.qq){ ElMessage.warning('请先绑定并选择角色'); return; }
      if(!name){ ElMessage.warning('请填写定制坐骑名称'); return; }
      if(name.length > 8){ ElMessage.warning('名称最多 8 个字'); return; }
      if(!mountC.file){ ElMessage.warning('请上传外观图片'); return; }
      mountC.submitting = true;
      try{
        const fd = new FormData();
        fd.append('group_id', id.group_id); fd.append('qq', id.qq);
        fd.append('name', name); fd.append('image', mountC.file);
        if(mountC.nickname){ fd.append('nickname', mountC.nickname); }
        if(mountC.showQQ){ fd.append('show_qq', mountC.showQQ); }
        const resp = await fetch('/api/portal/mount_custom_submit', {method:'POST', headers:{'X-CSRF-Token':CSRF_TOKEN}, body:fd});
        if(resp.status === 401 || resp.status === 403){ location.href = '/'; return null; }
        const r = await resp.json().catch(()=>null);
        if(r && r.ok){ ElMessage.success(r.msg || '已提交审核'); if(r.mount_custom) data.value.mount_custom = r.mount_custom; mountC.name=''; mountC.nickname=''; mountC.showQQ=''; mountC.file=null; mountC.preview=''; }
        else { ElMessage.error((r && r.msg) || '提交失败'); }
      } finally { mountC.submitting = false; }
    }

    // ---- 更换定制坐骑外观（每月 3 次，走审核） ----
    const mountImgFileInput = ref(null);
    const mountI = reactive({dialog:false, name:'', file:null, preview:'', submitting:false});
    const customMountList = computed(() => ((data.value && data.value.mounts) || []).filter(m => m.custom));
    function isMountImgPending(n){ return ((data.value && data.value.mount_custom && data.value.mount_custom.img_pending) || []).includes(n); }
    function openMountImage(){ mountI.name=''; mountI.file=null; mountI.preview=''; mountI.dialog = true; }
    function pickMountImgFile(){ if(!mountImgFileInput.value) return; mountImgFileInput.value.click(); }
    function onMountImgFile(e){
      const f = e.target.files && e.target.files[0];
      if(!f) return;
      if(!/\.(jpe?g|png|gif|webp)$/i.test(f.name)){ ElMessage.error('仅支持 jpg/png/gif/webp 图片'); e.target.value=''; return; }
      if(f.size > 5*1024*1024){ ElMessage.error('图片不能超过 5MB'); e.target.value=''; return; }
      mountI.file = f;
      mountI.preview = URL.createObjectURL(f);
    }
    async function doMountImgSubmit(){
      const id = currentSlotId();
      if(!id.group_id || !id.qq){ ElMessage.warning('请先绑定并选择角色'); return; }
      if(!mountI.name){ ElMessage.warning('请选择要更换外观的坐骑'); return; }
      if(!mountI.file){ ElMessage.warning('请上传新外观图片'); return; }
      mountI.submitting = true;
      try{
        const fd = new FormData();
        fd.append('group_id', id.group_id); fd.append('qq', id.qq);
        fd.append('name', mountI.name); fd.append('image', mountI.file);
        const resp = await fetch('/api/portal/mount_image_submit', {method:'POST', headers:{'X-CSRF-Token':CSRF_TOKEN}, body:fd});
        if(resp.status === 401 || resp.status === 403){ location.href = '/'; return null; }
        const r = await resp.json().catch(()=>null);
        if(r && r.ok){ ElMessage.success(r.msg || '已提交审核'); if(r.mount_custom) data.value.mount_custom = r.mount_custom; if(r.role){ data.value.mounts = r.role.mounts; } mountI.name=''; mountI.file=null; mountI.preview=''; }
        else { ElMessage.error((r && r.msg) || '提交失败'); }
      } finally { mountI.submitting = false; }
    }

    // ---- 修改密码 ----
    function openPwd(){ pwd.code=''; pwd.n1=''; pwd.n2=''; pwd.show=true; }
    async function sendPwdCode(){
      pwd.sending = true;
      try{
        const r = await api('/api/portal/send_chpwd_code','POST',{});
        if(r && r.ok){
          ElMessage.success((r.msg || '验证码已发送') + (r.email ? '（' + r.email + '）' : ''));
          pwd.countdown = 60;
          if(pwdCdTimer) clearInterval(pwdCdTimer);
          pwdCdTimer = setInterval(() => {
            pwd.countdown -= 1;
            if(pwd.countdown <= 0){ clearInterval(pwdCdTimer); pwdCdTimer = null; pwd.countdown = 0; }
          }, 1000);
        } else { ElMessage.error((r && r.msg) || '发送失败'); }
      } finally { pwd.sending = false; }
    }
    async function changePwd(){
      if(!pwd.code){ ElMessage.warning('请先获取并填写邮箱验证码'); return; }
      if(pwd.n1.length < 6){ ElMessage.warning('新密码至少 6 位'); return; }
      if(pwd.n1 !== pwd.n2){ ElMessage.warning('两次输入的新密码不一致'); return; }
      pwd.loading = true;
      try{
        const r = await api('/api/portal/change_password','POST',{code:pwd.code, new_password:pwd.n1});
        if(r && r.ok){ ElMessage.success('密码修改成功'); pwd.show = false; }
        else { ElMessage.error((r && r.msg) || '修改失败'); }
      } finally { pwd.loading = false; }
    }

    // ---- 养成 / 道具 / 兑换 ----
    async function petAction(action){
      if(!data.value) return;
      const tips = {
        auto_level: '确定要一键升级吗？将自动消耗经验与精力升到可达最高等级。',
        level: `确定要升级 ${Math.max(1, levelTimes.value||1)} 次吗？将消耗相应经验与精力。`,
        evolve: '确定要进行宠物进化吗？需消耗『进化神石』。'
      };
      try{
        await ElMessageBox.confirm(tips[action] || '确定要执行该操作吗？', '操作确认', {confirmButtonText:'确定', cancelButtonText:'取消', type:'warning'});
      }catch(e){ return; }
      acting.value = action;
      try{
        const r = await api('/api/portal/pet_action','POST',{group_id:data.value.group_id, qq:data.value.qq, pet_index:data.value.pet_index||0, action, times:Math.max(1, levelTimes.value||1)});
        showResult(r, '操作失败');
        if(r && r.ok) await refreshAll();
      } finally { acting.value = ''; }
    }

    async function showItemInfo(it){
      const r = await api('/api/portal/item_info?name=' + encodeURIComponent(it.name));
      if(!r){ ElMessage.error('查询失败'); return; }
      if(!r.ok){ ElMessage.warning(stripMd(r.msg || '未找到说明')); return; }
      let text = stripMd(r.msg);
      const lines = text.split('\n');
      if(lines.length > 1 && lines[0].includes(it.name)) text = lines.slice(1).join('\n').trim();
      ElMessageBox.alert(`<div class="result-pre">${text.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])).replace(/\n/g,'<br>')}</div>`,
        `📘 ${it.name}`, {dangerouslyUseHTMLString:true, confirmButtonText:'知道了'});
    }

    async function useItem(it){
      if(!data.value) return;
      const n = it.kind==='item' ? Math.max(1, it.qty||1) : 1;
      const verb = it.kind==='art' ? `佩戴神器「${it.name}」` : (it.kind==='skill' ? `参悟秘技书「${it.name}」` : `使用「${it.name}」×${n}`);
      try{
        await ElMessageBox.confirm(`确定要${verb}吗？`, '操作确认', {confirmButtonText:'确定', cancelButtonText:'取消', type:'warning'});
      }catch(e){ return; }
      usingItem.value = it.name;
      try{
        const r = await api('/api/portal/use_item','POST',{group_id:data.value.group_id, qq:data.value.qq, pet_index:data.value.pet_index||0, name:it.name, count: it.kind==='item' ? Math.max(1, it.qty||1) : 1});
        showResult(r, '使用失败');
        if(r && r.ok) await refreshAll();
      } finally { usingItem.value = ''; }
    }

    async function redeem(){
      const code = redeemCode.value.trim();
      if(!code){ ElMessage.warning('请输入卡密'); return; }
      redeeming.value = true;
      try{
        const r = await api('/api/portal/redeem','POST',{group_id:data.value.group_id, qq:data.value.qq, pet_index:data.value.pet_index||0, code});
        if(r && r.msg) redeemResult.value = stripMd(r.msg);
        if(r && r.ok){ ElMessage.success('兑换成功'); redeemCode.value=''; await refreshAll(); }
        else { ElMessage.error(stripMd((r && r.msg) || '兑换失败')); }
      } finally { redeeming.value = false; }
    }

    // ---- 反馈 / 对话 ----
    function goFeedback(){ location.href = '/feedback'; }
    function goChat(){ location.href = '/chat'; }

    // ---- 定制 ----
    async function redeemCustom(){
      const code = custom.code.trim();
      if(!code){ ElMessage.warning('请输入定制卡密'); return; }
      if(!custom.nickname.trim() || !custom.showQQ.trim()){ ElMessage.warning('请填写昵称和 QQ 号，用于全群祝贺'); return; }
      custom.redeeming = true;
      try{
        const r = await api('/api/portal/custom_redeem','POST',{group_id:data.value.group_id, qq:data.value.qq, pet_index:data.value.pet_index||0, code, nickname:custom.nickname.trim(), show_qq:custom.showQQ.trim()});
        if(r && r.ok){ ElMessage.success(r.msg || '解锁成功'); custom.code=''; await loadPet(current.value); }
        else { ElMessage.error((r && r.msg) || '解锁失败'); }
      } finally { custom.redeeming = false; }
    }
    function openCustomEdit(){
      custom.species = (pet.value && (pet.value.custom_species_name || pet.value.species)) || '';
      custom.blob = null; custom.previewUrl = '';
      custom.editShow = true;
    }
    async function submitCustom(){
      if(!current.value){ ElMessage.warning('请先选择宠物'); return; }
      const species = custom.species.trim();
      if(!species && !custom.blob){ ElMessage.warning('请至少修改名称或上传图片'); return; }
      custom.submitting = true;
      try{
        const fd = new FormData();
        fd.append('group_id', current.value.group_id);
        fd.append('qq', current.value.qq);
        fd.append('pet_index', current.value.pet_index || 0);
        if(species) fd.append('species_name', species);
        if(custom.blob) fd.append('image', custom.blob, 'custom.jpg');
        const r = await fetch('/api/portal/custom_submit', {method:'POST', headers:{'X-CSRF-Token':CSRF_TOKEN}, body:fd});
        const d = await r.json().catch(()=>null);
        if(d && d.ok){ ElMessage.success(d.msg || '已提交审核'); custom.editShow = false; custom.blob=null; custom.previewUrl=''; await loadPet(current.value); }
        else { ElMessage.error((d && d.msg) || '提交失败'); }
      } finally { custom.submitting = false; }
    }

    // ---- 裁剪 ----
    function drawCrop(){
      const canvas = document.getElementById('cropCanvas');
      if(!canvas || !cropState.img) return;
      const ctx = canvas.getContext('2d');
      ctx.clearRect(0,0,512,512);
      ctx.drawImage(cropState.img, cropState.x, cropState.y, cropState.img.naturalWidth*cropState.scale, cropState.img.naturalHeight*cropState.scale);
    }
    function pickCustomImage(e){
      const f = e.target.files[0];
      e.target.value = '';
      if(!f) return;
      const img = new Image();
      img.onload = ()=>{
        cropState.img = img;
        cropState.base = Math.max(512/img.naturalWidth, 512/img.naturalHeight);
        cropState.scale = cropState.base;
        cropState.x = (512 - img.naturalWidth*cropState.scale)/2;
        cropState.y = (512 - img.naturalHeight*cropState.scale)/2;
        crop.zoom = 100;
        crop.show = true;
        nextTick(()=>drawCrop());
      };
      img.src = URL.createObjectURL(f);
    }
    function applyZoom(v){
      if(!cropState.img) return;
      const oldScale = cropState.scale;
      cropState.scale = cropState.base * (v/100);
      cropState.x = 256 - (256 - cropState.x) * (cropState.scale/oldScale);
      cropState.y = 256 - (256 - cropState.y) * (cropState.scale/oldScale);
      drawCrop();
    }
    function canvasXY(e){
      const canvas = document.getElementById('cropCanvas');
      const r = canvas.getBoundingClientRect();
      const cx = e.touches ? e.touches[0].clientX : e.clientX;
      const cy = e.touches ? e.touches[0].clientY : e.clientY;
      return [(cx - r.left) * (512/r.width), (cy - r.top) * (512/r.height)];
    }
    function cropDown(e){ const [x,y]=canvasXY(e); cropState.dragging=true; cropState.lastX=x; cropState.lastY=y; }
    function cropMove(e){
      if(!cropState.dragging) return;
      const [x,y]=canvasXY(e);
      cropState.x += x - cropState.lastX; cropState.y += y - cropState.lastY;
      cropState.lastX=x; cropState.lastY=y;
      drawCrop();
    }
    function cropUp(){ cropState.dragging=false; }
    function cropTouchStart(e){ cropDown(e); }
    function cropTouchMove(e){ cropMove(e); }
    function saveCrop(){
      const canvas = document.getElementById('cropCanvas');
      if(!canvas || !cropState.img){ crop.show=false; return; }
      canvas.toBlob(blob=>{
        custom.blob = blob;
        custom.previewUrl = URL.createObjectURL(blob);
        crop.show = false;
        ElMessage.success('裁剪完成，可提交审核');
      }, 'image/jpeg', 0.92);
    }

    onMounted(init);

    return {account, pets, current, data, pet, petLoading, blankImg:BLANK_IMG,
      initialLoading,loadError,roleMenu,activePanel,panelTabs,selectPanel,bagQuery,bagEnd,bagHasMore,loadMoreBag,filteredBag,visibleBag,init,
      levelTimes, acting, usingItem, redeemCode, redeeming, redeemResult, bagItems, cooldowns, autoCultivating,
      assistantEdit,openAssistantEditor,selectAssistantTask,saveAssistantTasks,
      bind, pwd, custom, crop,
      auto,
      slots, currentSlot, slotPets, slotLabel, slotSub, switchSlot,
      mountFileInput, mountC, openMountNew, pickMountImage, onMountFile, doMountRedeem, doMountSubmit,
      mountImgFileInput, mountI, customMountList, isMountImgPending, openMountImage, pickMountImgFile, onMountImgFile, doMountImgSubmit,
      fmt, pct, fmtDate, fmtCd, cdRemaining,
      loadPet, logout, openBind, doBindAuto, pickAuto, reclaimBind, doBindQuery, doBind, openPwd, changePwd, sendPwdCode, petAction, useItem, showItemInfo, redeem,
      goFeedback, goChat,
      redeemCustom, openCustomEdit, submitCustom, toggleAutoCultivation,
      pickCustomImage, applyZoom, cropDown, cropMove, cropUp, cropTouchStart, cropTouchMove, saveCrop};
  }
}).use(ElementPlus, {locale: ElementPlusLocaleZhCn}).mount('#app');
</script>
</body>
</html>
"""


_FEEDBACK_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<script src="/webstatic/device-ui.js?v=20260922-1"></script>
<meta name="theme-color" content="#112f2d">
<title>问题反馈 · 灵契仙途</title>
<link rel="stylesheet" href="/webstatic/element-plus.min.css">
<link rel="stylesheet" href="/webstatic/feedback.css?v=20260922-1">
<link rel="stylesheet" href="/webstatic/support.css?v=20260922-1">
<link rel="stylesheet" href="/webstatic/mobile.css?v=20260922-1">
</head>
<body class="feedback-page">
<div id="app" v-cloak>
  <header class="topbar">
    <div class="topbar-inner">
      <a class="back-link" href="/portal">← 返回玩家中心</a>
      <div class="topbar-title">问题反馈</div>
    </div>
  </header>

  <section class="support-intro"><div><span class="support-kicker">灵契仙途 · 玩家反馈</span><h1>你说，我们听。</h1><p>记录遇到的问题，也留下让仙途更好的想法。</p></div><a href="/chat">返回仙途游玩 ↗</a></section>
  <nav class="feedback-tabs" aria-label="反馈功能"><button @click="feedbackPanel='submit'" :aria-pressed="feedbackPanel==='submit'">提交反馈</button><button @click="feedbackPanel='records'" :aria-pressed="feedbackPanel==='records'">我的记录 <span>{{ list.length }}</span></button></nav>
  <main class="wrap" :data-panel="feedbackPanel">
    <div class="card feedback-submit">
      <div class="card-title">提交反馈</div>
      <p class="card-desc">遇到 Bug 或有好的想法都可以在这里告诉我们，管理员处理后可在「我的反馈记录」查看回复。</p>
      <el-tabs v-model="form.kind">
        <el-tab-pane label="反馈问题" name="bug"></el-tab-pane>
        <el-tab-pane label="提出建议" name="suggestion"></el-tab-pane>
      </el-tabs>
      <label class="fld" style="margin-top:2px">{{ form.kind==='bug' ? '问题描述' : '建议内容' }}</label>
      <el-input v-model="form.content" aria-label="反馈内容" type="textarea" :rows="4" maxlength="2000" show-word-limit
        :placeholder="form.kind==='bug' ? '请详细描述遇到的问题：操作了什么、预期结果、实际结果…' : '说说你希望增加或改进的功能…'"></el-input>
      <template v-if="form.kind==='bug'">
        <label class="fld">发生时间</label>
        <el-date-picker v-model="form.time" aria-label="发生时间" type="datetime" placeholder="选择问题发生的时间" style="width:100%" format="YYYY-MM-DD HH:mm" value-format="YYYY-MM-DD HH:mm"></el-date-picker>
        <div style="display:flex;gap:8px;margin-top:12px">
          <el-input v-model="form.group" placeholder="对应的 QQ 群号"></el-input>
          <el-input v-model="form.user" placeholder="对应的用户 ID"></el-input>
        </div>
      </template>
      <label class="fld">图片截图（可选，最多 3 张）</label>
      <input ref="fileInput" type="file" accept="image/*" multiple style="display:none" @change="pickImages">
      <button type="button" class="upload-zone" @click="$refs.fileInput.click()">
        <div class="upload-plus">＋</div>
        <div class="upload-text">点击选择图片</div>
        <div class="upload-hint">支持 jpg / png / gif / webp，单张不超过 5MB</div>
      </button>
      <div class="fb-img-preview" v-if="form.files.length">
        <div v-for="(f,i) in form.files" :key="i" class="fb-thumb">
          <img :src="f.url"><button class="rm" @click="form.files.splice(i,1)">×</button>
        </div>
      </div>
      <div class="submit-row">
        <el-button type="primary" round :loading="form.submitting" @click="submitFeedback">提交反馈</el-button>
      </div>
    </div>

    <div class="card feedback-records">
      <div class="list-head">
        <div class="card-title" style="margin-bottom:0">我的反馈记录</div>
        <span class="list-count" v-if="list.length">共 {{ list.length }} 条</span>
      </div>
      <div v-loading="listLoading">
        <div v-if="!list.length && !listLoading" class="empty-tip">还没有提交过反馈</div>
        <div class="fb-rows" v-else>
          <div v-for="f in pageList" :key="f.id" class="fb-row">
            <div class="fb-row-main">
              <div class="fb-row-top">
                <el-tag :type="f.kind==='bug' ? 'danger' : 'primary'" size="small" round>{{ f.kind==='bug' ? 'Bug' : '建议' }}</el-tag>
                <el-tag :type="f.status==='resolved' ? 'success' : 'warning'" size="small" round>{{ f.status==='resolved' ? '已回复' : '处理中' }}</el-tag>
                <span class="fb-row-date">{{ fmtDate(f.created_at) }}</span>
              </div>
              <div class="fb-row-text">{{ f.content }}</div>
            </div>
            <div class="fb-row-btns">
              <el-button size="small" round @click="openDetail(f)">详情</el-button>
              <el-button v-if="f.status!=='resolved'" size="small" type="danger" plain round
                :loading="deleting===f.id" @click="removeFeedback(f)">删除</el-button>
            </div>
          </div>
        </div>
        <div class="pager" v-if="list.length > pageSize">
          <el-pagination layout="prev, pager, next" background small
            :total="list.length" :page-size="pageSize" v-model:current-page="page"></el-pagination>
        </div>
      </div>
    </div>
  </main>

  <!-- 反馈详情 -->
  <el-dialog v-model="detail.show" title="反馈详情" width="560px" align-center top="6vh">
    <template v-if="detail.item">
      <div class="dt-head">
        <el-tag :type="detail.item.kind==='bug' ? 'danger' : 'primary'" size="small" round>{{ detail.item.kind==='bug' ? 'Bug' : '建议' }}</el-tag>
        <el-tag :type="detail.item.status==='resolved' ? 'success' : 'warning'" size="small" round>{{ detail.item.status==='resolved' ? '已回复' : '处理中' }}</el-tag>
        <span class="dt-date">提交于 {{ fmtDate(detail.item.created_at) }}</span>
      </div>
      <div class="dt-body">{{ detail.item.content }}</div>
      <div v-if="detail.item.kind==='bug'" class="dt-meta">
        发生时间：{{ detail.item.occur_time || '—' }}<br>
        QQ 群号：{{ detail.item.group || '—' }} · 用户 ID：{{ detail.item.user_id || '—' }}
      </div>
      <div class="dt-imgs" v-if="(detail.item.images||[]).length">
        <el-image v-for="im in detail.item.images" :key="im" :src="'/feedback_images/'+im"
          :preview-src-list="(detail.item.images||[]).map(x=>'/feedback_images/'+x)" fit="cover" preview-teleported
          style="width:76px;height:76px;border-radius:10px;border:1px solid var(--line)"></el-image>
      </div>
      <div v-if="detail.item.reply" class="dt-reply">
        <div class="rt">管理员回复{{ detail.item.replied_at ? '（' + fmtDate(detail.item.replied_at) + '）' : '' }}</div>{{ detail.item.reply }}
      </div>
    </template>
    <template #footer>
      <el-button v-if="detail.item && detail.item.status!=='resolved'" type="danger" plain round
        :loading="deleting===(detail.item && detail.item.id)" @click="removeFeedback(detail.item)">删除该反馈</el-button>
      <el-button round @click="detail.show=false">关闭</el-button>
    </template>
  </el-dialog>
</div>

<script src="/webstatic/vue.global.prod.js"></script>
<script src="/webstatic/element-plus.full.min.js"></script>
<script src="/webstatic/element-plus-zh-cn.min.js"></script>
<script>
const CSRF_TOKEN = '{{CSRF_TOKEN}}';
const { createApp, reactive, ref, computed, onMounted } = Vue;
const { ElMessage, ElMessageBox } = ElementPlus;

async function api(path, method='GET', body=null){
  const opts = {method, headers:{'X-CSRF-Token':CSRF_TOKEN}};
  if(body){ opts.headers['Content-Type']='application/json'; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts);
  if(r.status === 401 || r.status === 403){ location.href = '/'; return null; }
  return r.json().catch(()=>null);
}

createApp({
  setup(){
    const form = reactive({kind:'bug', content:'', time:'', group:'', user:'', files:[], submitting:false});
    const feedbackPanel=ref('submit');
    const list = ref([]);
    const listLoading = ref(false);
    const page = ref(1);
    const pageSize = 10;
    const detail = reactive({show:false, item:null});
    const deleting = ref('');

    const pageList = computed(()=> list.value.slice((page.value-1)*pageSize, page.value*pageSize));
    const fmtDate = ts => new Date((ts||0)*1000).toLocaleString('zh-CN',{hour12:false});

    async function loadList(){
      listLoading.value = true;
      try{
        const r = await api('/api/portal/feedback');
        const data = (r && r.data) || [];
        data.sort((a,b)=>(b.created_at||0)-(a.created_at||0));
        list.value = data;
        const maxPage = Math.max(1, Math.ceil(data.length/pageSize));
        if(page.value > maxPage) page.value = maxPage;
      } finally { listLoading.value = false; }
    }

    function pickImages(e){
      for(const f of e.target.files){
        if(form.files.length >= 3){ ElMessage.warning('最多上传 3 张图片'); break; }
        if(f.size > 5*1024*1024){ ElMessage.warning(`图片「${f.name}」超过 5MB，已跳过`); continue; }
        form.files.push({file:f, url:URL.createObjectURL(f)});
      }
      e.target.value = '';
    }

    async function submitFeedback(){
      const content = form.content.trim();
      if(!content){ ElMessage.warning(form.kind==='bug' ? '请填写问题描述' : '请填写建议内容'); return; }
      const fd = new FormData();
      fd.append('kind', form.kind);
      fd.append('content', content);
      if(form.kind==='bug'){
        if(!form.time){ ElMessage.warning('请填写发生时间'); return; }
        if(!form.group.trim()){ ElMessage.warning('请填写对应的 QQ 群号'); return; }
        if(!form.user.trim()){ ElMessage.warning('请填写对应的用户 ID'); return; }
        fd.append('occur_time', form.time);
        fd.append('group_id', form.group.trim());
        fd.append('user_id', form.user.trim());
      }
      form.files.forEach((f,i)=>fd.append(`image${i}`, f.file, f.file.name));
      form.submitting = true;
      try{
        const r = await fetch('/api/portal/feedback', {method:'POST', headers:{'X-CSRF-Token':CSRF_TOKEN}, body:fd}).then(x=>x.json()).catch(()=>null);
        if(r && r.ok){
          ElMessage.success(r.msg || '反馈已提交');
          form.content=''; form.files=[]; form.time=''; form.group=''; form.user='';
          await loadList();
          page.value=1; feedbackPanel.value='records';
        } else {
          ElMessage.error((r && r.msg) || '提交失败，请稍后重试');
        }
      } finally { form.submitting = false; }
    }

    function openDetail(f){ detail.item = f; detail.show = true; }

    async function removeFeedback(f){
      if(!f) return;
      try{
        await ElMessageBox.confirm('删除后无法恢复，确定要删除这条反馈吗？', '删除确认',
          {confirmButtonText:'删除', cancelButtonText:'取消', type:'warning'});
      }catch(e){ return; }
      deleting.value = f.id;
      try{
        const r = await api('/api/portal/feedback/delete','POST',{id:f.id});
        if(r && r.ok){
          ElMessage.success(r.msg || '反馈已删除');
          if(detail.item && detail.item.id === f.id) detail.show = false;
          await loadList();
        } else {
          ElMessage.error((r && r.msg) || '删除失败');
        }
      } finally { deleting.value = ''; }
    }

    onMounted(loadList);

    return {feedbackPanel, form, list, listLoading, page, pageSize, detail, deleting, pageList,
      fmtDate, pickImages, submitFeedback, openDetail, removeFeedback};
  }
}).use(ElementPlus, {locale: ElementPlusLocaleZhCn}).mount('#app');
</script>
</body>
</html>
"""


_CHAT_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<script src="/webstatic/device-ui.js?v=20260922-1"></script>
<meta name="theme-color" content="#112f2d">
<title>灵契仙途 · 修行对话</title>
<link rel="stylesheet" href="/webstatic/element-plus.min.css">
<link rel="stylesheet" href="/webstatic/chat.css?v=20260922-1">
<link rel="stylesheet" href="/webstatic/support.css?v=20260922-1">
<link rel="stylesheet" href="/webstatic/mobile.css?v=20260922-1">
</head>
<body class="chat-page">
<div id="app" v-cloak>
  <div class="frame">
    <header class="topbar">
      <div class="topbar-inner">
        <a class="back-link" href="/portal">← 玩家中心</a>
        <div class="top-title" v-if="current">
          <div class="t">{{ current.nickname }} · 仙途游玩</div>
          <div class="s">群 {{ current.group_id }} · ID {{ current.qq }}</div>
        </div>
        <div class="top-title" v-else><div class="t">仙途游玩</div></div>
        <div class="top-right">
          <el-select v-if="pets.length > 1" v-model="petIdx" aria-label="切换游玩角色" :disabled="sending" size="small" style="width:150px" @change="switchPet">
            <el-option v-for="(p,i) in pets" :key="i" :label="p.nickname + '（群' + p.group_id + '）'" :value="i"></el-option>
          </el-select>
          <el-button size="small" round @click="clearHistory" v-if="msgs.length">清空记录</el-button>
        </div>
      </div>
    </header>

    <template v-if="current">
      <main class="chat-body" ref="bodyEl" aria-label="对话记录">
        <div v-if="!msgs.length" class="chat-welcome"><span class="chat-seal" aria-hidden="true">契</span><h1>与{{ current.nickname }}，再赴仙途。</h1><p>发送游戏指令继续历练，也可以从下方常用指令开始。</p><span>例如：我的修士 · 仙途地图 · 我的宠物</span></div>
        <div class="day-tip"><span>与『{{ current.nickname }}』的对话 · 模拟群 {{ current.group_id }}</span></div>
        <div v-for="(m,i) in msgs" :key="i" class="msg" :class="{me: m.role==='me'}">
          <div class="avatar">
            <img v-if="m.role==='me' && userAvatar" :src="userAvatar">
            <img v-else-if="m.role==='bot' && current.image_url" :src="current.image_url">
            <span v-else>{{ m.role==='me' ? '我' : '契' }}</span>
          </div>
          <div class="msg-col">
            <div class="msg-name">{{ m.role==='me' ? myName : current.nickname }}</div>
            <div class="bubble" :class="{pending: m.pending}">
              <div v-if="m.pending" class="typing"><i></i><i></i><i></i></div>
              <span v-else v-html="m.html"></span>
            </div>
          </div>
        </div>
      </main>

      <div class="chips">
        <button type="button" class="chip" v-for="c in quickCmds" :key="c" :disabled="sending" @click="sendText(c)">{{ c }}</button>
      </div>

      <div class="inputbar">
        <textarea ref="inputEl" v-model="draft" aria-label="输入游戏指令" rows="1" maxlength="200"
          placeholder="输入指令，如：签到"
          @keydown.enter.exact="onChatEnter"
          @input="autoGrow"></textarea>
        <button class="send-btn" :disabled="sending || !draft.trim()" @click="sendText()">发送</button>
      </div>
    </template>

    <div class="empty-wrap" v-else-if="loaded">
      <div class="chat-seal" aria-hidden="true">契</div>
      <div>还没有绑定宠物，先去玩家中心绑定一只吧</div>
      <el-button type="primary" round @click="location.href='/portal'">前往绑定</el-button>
    </div>
  </div>
</div>

<script src="/webstatic/vue.global.prod.js"></script>
<script src="/webstatic/element-plus.full.min.js"></script>
<script src="/webstatic/element-plus-zh-cn.min.js"></script>
<script>
const CSRF_TOKEN = '{{CSRF_TOKEN}}';
const { createApp, ref, computed, onMounted, nextTick } = Vue;
const { ElMessage, ElMessageBox } = ElementPlus;

async function api(path, method='GET', body=null){
  const opts = {method, headers:{'X-CSRF-Token':CSRF_TOKEN}};
  if(body){ opts.headers['Content-Type']='application/json'; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts);
  if(r.status === 401 || r.status === 403){ location.href = '/'; return null; }
  return r.json().catch(()=>null);
}

function escapeHtml(s){
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// 轻量 Markdown 渲染：表格 / 标题 / 引用 / 加粗 / 行内代码 / 图片 / 分隔线
function inlineMd(s){
  let t = escapeHtml(s);
  t = t.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
  t = t.replace(/`([^`]+)`/g, '<b>$1</b>');
  return t;
}
// 图片地址修正：站内图片（定制形象/地图等）改为同源路径，避免 https 页面下的混合内容被浏览器拦截
function fixImgUrl(u){
  const m = String(u).match(/\/(custom_images|feedback_images)\/.+$/);
  return m ? m[0] : u;
}
function isTableRow(l){ return /^\|.*\|\s*$/.test(l.trim()); }
function isTableSep(l){ return /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/.test(l.trim()); }
function splitCells(l){
  let t = l.trim();
  if(t.startsWith('|')) t = t.slice(1);
  if(t.endsWith('|')) t = t.slice(0,-1);
  return t.split('|').map(c=>c.trim());
}
function renderReply(text, imageMd){
  let imgs = '';
  const collect = src => { for(const m of String(src||'').matchAll(/!\[[^\]]*\]\(([^)\s]+)[^)]*\)/g)) imgs += '<img src="' + escapeHtml(fixImgUrl(m[1])) + '">'; };
  collect(imageMd);
  let t = String(text||'').replace(/!\[[^\]]*\]\(([^)\s]+)[^)]*\)/g, (mm,u)=>{ imgs += '<img src="' + escapeHtml(fixImgUrl(u)) + '">'; return ''; });
  const lines = t.trim().split(/\r?\n/);
  const out = [];
  let i = 0;
  while(i < lines.length){
    const line = lines[i];
    // 表格：表头行 + 分隔行 + 若干数据行
    if(isTableRow(line) && i+1 < lines.length && isTableSep(lines[i+1])){
      const head = splitCells(line);
      let rows = [];
      i += 2;
      while(i < lines.length && isTableRow(lines[i]) && !isTableSep(lines[i])){
        rows.push(splitCells(lines[i])); i++;
      }
      let html = '<table><thead><tr>' + head.map(c=>'<th>'+inlineMd(c)+'</th>').join('') + '</tr></thead><tbody>';
      for(const r of rows) html += '<tr>' + r.map(c=>'<td>'+inlineMd(c)+'</td>').join('') + '</tr>';
      out.push(html + '</tbody></table>');
      continue;
    }
    const trimmed = line.trim();
    if(!trimmed){ out.push('<br>'); i++; continue; }
    if(/^-{3,}$/.test(trimmed)){ out.push('<hr>'); i++; continue; }
    let m = trimmed.match(/^#{1,4}\s*(.+)$/);
    if(m){ out.push('<span class="h">'+inlineMd(m[1])+'</span>'); i++; continue; }
    m = trimmed.match(/^&gt;\s?(.*)$/) || trimmed.match(/^>\s?(.*)$/);
    if(m){ out.push('<span class="quote">'+inlineMd(m[1])+'</span>'); i++; continue; }
    out.push(inlineMd(line) + '<br>');
    i++;
  }
  let body = out.join('');
  body = body.replace(/(<br>)+$/,'').replace(/(<\/table>|<\/span>|<hr>)<br>/g, '$1');
  return imgs + body;
}

createApp({
  setup(){
    const pets = ref([]);
    const petIdx = ref(0);
    const account = ref(null);
    const loaded = ref(false);
    const msgs = ref([]);
    const draft = ref('');
    const sending = ref(false);
    const bodyEl = ref(null);
    const inputEl = ref(null);
    const quickCmds = ['灵契仙途','创建角色','选择职业 剑修','结契灵宠 九尾狐','我的洞天','洞天突破','仙途毕业','踏入仙途 体修','踏入仙途 灵修','踏入仙途 魔修','我的修士','修士修炼','仙途地图','历练 1','修士装备','世界首领','战斗详情','我的宠物'];

    const current = computed(()=> pets.value[petIdx.value] || null);
    const myName = computed(()=> account.value ? 'QQ ' + account.value.qq : '我');
    const userAvatar = computed(()=>{
      const q = account.value && account.value.qq;
      return q && /^\d{5,}$/.test(q) ? 'https://q1.qlogo.cn/g?b=qq&nk=' + q + '&s=100' : '';
    });

    const histKey = () => current.value ? 'petchat:' + current.value.group_id + ':' + current.value.qq : '';

    function loadHistory(){
      msgs.value = [];
      try{
        const raw = localStorage.getItem(histKey());
        if(raw) msgs.value = JSON.parse(raw).slice(-200);
      }catch(e){}
      scrollBottom();
    }
    function saveHistory(){
      try{ localStorage.setItem(histKey(), JSON.stringify(msgs.value.filter(m=>!m.pending).slice(-200))); }catch(e){}
    }
    function clearHistory(){
      ElMessageBox.confirm('清空当前宠物的对话记录？', '提示', {confirmButtonText:'清空', cancelButtonText:'取消', type:'warning'})
        .then(()=>{ msgs.value = []; saveHistory(); }).catch(()=>{});
    }

    function scrollBottom(){
      nextTick(()=>{ if(bodyEl.value) bodyEl.value.scrollTop = bodyEl.value.scrollHeight; });
    }
    function autoGrow(e){
      const el = e.target; el.style.height = 'auto'; el.style.height = Math.min(110, el.scrollHeight) + 'px';
    }

    function onChatEnter(event){
      if(event.isComposing || event.keyCode===229)return;
      if(document.documentElement.dataset.ui==='mobile')return;
      event.preventDefault();sendText();
    }
    function switchPet(){ loadHistory(); }

    async function sendText(preset){
      const text = (preset !== undefined ? preset : draft.value).trim();
      if(!text || sending.value || !current.value) return;
      if(preset === undefined) draft.value = '';
      if(inputEl.value){ inputEl.value.style.height = 'auto'; }
      msgs.value.push({role:'me', html: escapeHtml(text).replace(/\n/g,'<br>')});
      const pending = {role:'bot', pending:true, html:''};
      msgs.value.push(pending);
      scrollBottom();
      sending.value = true;
      try{
        const r = await api('/api/portal/chat','POST',{group_id: current.value.group_id, qq: current.value.qq, text});
        pending.pending = false;
        if(r && r.ok){
          pending.html = renderReply(r.reply, r.image_md);
        } else {
          pending.html = escapeHtml((r && r.msg) || '发送失败，请稍后重试');
        }
      } catch(e){
        pending.pending = false;
        pending.html = '网络异常，请稍后重试';
      } finally {
        sending.value = false;
        saveHistory();
        scrollBottom();
      }
    }

    onMounted(async ()=>{
      const me = await api('/api/portal/me');
      if(!me || !me.ok){ location.href = '/'; return; }
      account.value = me.account;
      pets.value = me.bound_pets || [];
      loaded.value = true;
      if(pets.value.length) loadHistory();
    });

    return {pets, petIdx, account, loaded, msgs, draft, sending, bodyEl, inputEl,
      quickCmds, current, myName, userAvatar, location,
      sendText, onChatEnter, switchPet, clearHistory, autoGrow};
  }
}).use(ElementPlus, {locale: ElementPlusLocaleZhCn}).mount('#app');
</script>
</body>
</html>
"""


_AGREEMENT_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>用户协议 · 灵契仙途</title>
<meta name="description" content="灵契仙途用户协议：账号与绑定、玩法与虚拟资源、行为规范、数据与隐私、未成年人保护、免责声明、协议变更与生效解释。">
<meta name="theme-color" content="#112f2d">
<link rel="stylesheet" href="/webstatic/home.css?v=20260908">
<style>
  .agree-mast { min-height:360px; }
  .agree-mast .landscape { height:100%; }
  .agree-head { position:relative; z-index:2; padding:58px 0 78px; max-width:900px; }
  .agree-head .eyebrow { margin-bottom:20px; }
  .agree-head h1 { font-size:50px; letter-spacing:4px; color:#f4eedf; margin:0 0 18px; }
  .agree-head .intro { font-size:15px; color:#c6cfc0; margin:0 0 26px; line-height:1.9; }
  .agree-head .meta { display:flex; flex-wrap:wrap; gap:12px 28px; font-size:12px; color:#a9b09b; border-top:1px solid #f4eedf26; padding-top:16px; }
  .agree-main { padding:18px 0 90px; }
  .agree-sec { margin:54px 0; }
  .agree-sec .section-head { margin-bottom:22px; }
  .clause { max-width:880px; color:#3c4a44; font-size:15px; line-height:2; }
  .clause p { margin:0 0 14px; }
  .clause strong { color:#233b36; font-weight:600; }
  .clause ul { margin:0 0 14px; padding:0; list-style:none; }
  .clause li { position:relative; padding-left:20px; margin:7px 0; }
  .clause li:before { content:"·"; position:absolute; left:2px; top:0; color:#a54132; }
  .clause .tail { margin-top:22px; padding-top:16px; border-top:1px dashed #d8d7c9; font-size:12px; color:#8b7350; }
  @media (max-width:760px) {
    .agree-head { padding:42px 0 54px; }
    .agree-head h1 { font-size:34px; letter-spacing:2px; }
    .agree-sec { margin:44px 0; }
  }
</style>
</head>
<body>
<a class="skip-link" href="#main">跳到正文</a>
<header class="masthead agree-mast">
  <img class="landscape" src="/webstatic/home/hero-mountains.webp" alt="" width="1672" height="941">
  <div class="wrap">
    <nav aria-label="主导航">
      <a class="brand" href="/" aria-label="灵契仙途首页"><span class="seal" aria-hidden="true">契</span>灵契仙途</a>
      <div class="nav-links"><a href="/">返回首页</a><a href="/portal">玩家中心</a><a href="/chat">网页游玩</a><a href="https://qm.qq.com/q/S6ql07Q72m" target="_blank" rel="noopener">官方群 547205828</a></div>
    </nav>
    <div class="agree-head">
      <div class="eyebrow">灵契仙途 / 用户协议</div>
      <h1>仙途有规，进门即约。</h1>
      <p class="intro">使用本游戏服务前，请您仔细阅读本协议。您在任一授权群内开始游戏，即视为已阅读并同意本协议全部条款。</p>
      <div class="meta"><span>生效日期：2026 年 9 月 22 日</span><span>协议版本：v1</span><span>如有疑问：官方群 547205828</span></div>
    </div>
  </div>
</header>
<main class="wrap agree-main" id="main">

  <section class="section agree-sec">
    <div class="section-kicker">壹 · 总则</div>
    <div class="section-head"><h2>仙途有规，进门即约。</h2><span>适用主体与服务范围</span></div>
    <div class="clause">
      <p>灵契仙途（下称「本游戏」）是一款运行于 QQ 群聊环境、由小飞机器人提供服务的文字修仙游戏。本协议是您与本游戏运营方（下称「运营方」）就使用本游戏服务所订立的约定。</p>
      <p>您在任一授权群内发送第一条游戏指令、或通过玩家中心完成绑定，即视为已阅读并同意本协议。若您不同意本协议任一条款，请停止使用本游戏服务。</p>
    </div>
  </section>

  <section class="section agree-sec">
    <div class="section-kicker">贰 · 账号与绑定</div>
    <div class="section-head"><h2>一名一档，认群不认人。</h2><span>身份对应 · 玩家中心绑定</span></div>
    <div class="clause">
      <p>本游戏以 QQ 群内身份参与，无需另行注册。您的游戏数据与您的群号、用户 ID（QQ 号或平台账号标识）一一对应，换群游玩时数据各自独立。</p>
      <p>您可在玩家中心（bot.flyyye.cn）绑定群号与用户 ID，用于跨端查看角色、管理修士、灵宠与坐骑。绑定仅用于身份对应与数据展示，请您妥善保管账号信息，勿将账号交由他人使用；因账号转借或泄露造成的后果由您自行承担。</p>
    </div>
  </section>

  <section class="section agree-sec">
    <div class="section-kicker">叁 · 玩法与虚拟资源</div>
    <div class="section-head"><h2>虚拟之物，只在局中作数。</h2><span>货币无现金价值 · 卡密即时到账</span></div>
    <div class="clause">
      <p>本游戏包含剑修、体修、灵修、魔修四职业修炼，灵宠结契、坐骑养成、道侣结缘、宗门建设、洞天修炼、秘境历练、摸金探宝、深渊抉择等玩法。</p>
      <p>游戏中的灵石、玄晶、天晶等均为虚拟货币，装备、丹药、卡牌等均为虚拟物品，仅限在本游戏内使用，不具任何现实货币价值，亦不可反向兑换为现金。</p>
      <p>您可通过卡密兑换的方式获取部分虚拟资源，兑换即时到账。请妥善保管卡密，卡密一经使用即告失效；因卡密泄露、转售造成的损失由您自行承担。</p>
    </div>
  </section>

  <section class="section agree-sec">
    <div class="section-kicker">肆 · 行为规范</div>
    <div class="section-head"><h2>群中修行，也讲规矩。</h2><span>违规处置：撤回 / 封号 / 清档 / 移群</span></div>
    <div class="clause">
      <p>为维护群内环境，请您在游戏过程中遵守法律法规与公序良俗，不发布辱骂、刷屏、引战、造谣等干扰他人游玩的内容，不利用本游戏从事任何违法违规活动。</p>
      <p>禁止通过多开小号、利用漏洞、篡改数据等方式获取不正当利益；禁止转让、交易账号以规避正常游戏规则。</p>
      <p>凡利用游戏漏洞进行违规刷取资源（包括但不限于货币、道具、修为、次数等）的，一经查实，处置包括但不限于：封号、清空数据、回溯违规用户数据等。因回溯导致的虚拟资源变动，不予另行补偿。一切处罚的解释权归开发者所有。</p>
      <p>对违规行为的处置包括但不限于：撤回消息、本群封禁（可设天数或永久）、清空游戏数据、移出群聊。情节严重的，开发者保留进一步追责的权利；游戏内大管理员依运营安排行使相应管理权限。</p>
    </div>
  </section>

  <section class="section agree-sec">
    <div class="section-kicker">伍 · 数据与隐私</div>
    <div class="section-head"><h2>存档在侧，私事不传。</h2><span>服务器存档 · 可申请清空</span></div>
    <div class="clause">
      <p>您的游戏存档保存在服务器上，仅用于本游戏的数据运算与展示，不会用于其他用途。</p>
      <p>除法律法规要求或经您本人授权外，运营方不会向第三方提供您的个人信息与游戏数据。</p>
      <p>您可通过运营方渠道申请清空您在本群内的游戏数据；数据一经清空不可恢复，请在操作前确认。为排查问题、改善服务，游戏过程中产生的操作记录仅在内部留存，不作他用。</p>
    </div>
  </section>

  <section class="section agree-sec">
    <div class="section-kicker">陆 · 未成年人保护</div>
    <div class="section-head"><h2>年少入道，宜有陪护。</h2><span>监护人陪同 · 理性游玩</span></div>
    <div class="clause">
      <p>若您为未成年人，请在监护人陪同下阅读本协议，并在征得监护人同意后使用本游戏服务。</p>
      <p>运营方提示未成年人合理安排游戏时间，理性消费，避免沉迷网络，影响学业与健康。</p>
    </div>
  </section>

  <section class="section agree-sec">
    <div class="section-kicker">柒 · 免责声明</div>
    <div class="section-head"><h2>天有不测，尽力而为。</h2><span>维护暂停 · 第三方渠道</span></div>
    <div class="clause">
      <p>因系统维护、升级或不可抗力（包括但不限于网络故障、服务器故障、第三方平台限制）导致服务暂停、中断或数据异常的，运营方将尽力恢复，但不因此承担额外赔偿责任。</p>
      <p>经第三方渠道（如充值平台、卡密售卖渠道）产生的交易行为，由您与该渠道自行协商解决。</p>
      <p>本游戏为文字修仙游戏，游戏内设定、剧情与真实世界无关，请您理性看待。</p>
    </div>
  </section>

  <section class="section agree-sec">
    <div class="section-kicker">捌 · 协议变更</div>
    <div class="section-head"><h2>条款有改，公示为准。</h2><span>官网与群公告公示</span></div>
    <div class="clause">
      <p>运营方可能根据实际情况对本协议进行修订。修订后的协议将通过官网或群公告进行公示。</p>
      <p>您在协议变更后继续使用本游戏服务的，视为已接受修订后的条款；如不同意，请停止使用本游戏服务。</p>
    </div>
  </section>

  <section class="section agree-sec">
    <div class="section-kicker">玖 · 生效与解释</div>
    <div class="section-head"><h2>今日立约，自今而始。</h2><span>生效日期 · 解释权归运营方</span></div>
    <div class="clause">
      <p>本协议自 2026 年 9 月 22 日起生效。</p>
      <p>本协议的解释权归开发者所有。开发者可依据本协议对相关条款进行解释与适用，并依据游戏运营情况对本协议进行调整。对协议条款如有疑问，可加入官方群 547205828 咨询。</p>
      <p class="tail">灵契仙途 · 与灵宠结契，共赴仙途。愿诸君守约而行，同登彼岸。</p>
    </div>
  </section>

</main>
<footer>
  <div>灵契仙途 · 与灵宠结契，共赴仙途。<br>本页为《用户协议》，游戏内相关约定以本页为准。</div>
  <div><a href="/">返回首页</a> &nbsp; / &nbsp; <a href="/portal">玩家中心</a> &nbsp; / &nbsp; <a href="/chat">网页游玩</a> &nbsp; / &nbsp; <a href="https://qm.qq.com/q/S6ql07Q72m" target="_blank" rel="noopener">官方群 547205828</a></div>
</footer>
</body>
</html>
"""


_HOME_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<script src="/webstatic/device-ui.js?v=20260922-1"></script>
<title>灵契仙途 · 与灵宠结契，共赴仙途</title>
<meta name="description" content="灵契仙途 QQ 群聊修仙游戏：剑修、体修、灵修、魔修四职业，灵宠结契、洞天突破、宗门秘境、坐骑养成。玩家中心支持绑定角色、查看修士与灵宠、坐骑外观定制。">
<meta name="theme-color" content="#112f2d">
<link rel="preload" as="image" href="/webstatic/home/hero-mountains.webp">
<link rel="stylesheet" href="/webstatic/element-plus.min.css">

<link rel="stylesheet" href="/webstatic/home.css?v=20260908">
<link rel="stylesheet" href="/webstatic/mobile.css?v=20260922-1">
</head>
<body class="home-page">
<a class="skip-link" href="#explore">跳到玩法介绍</a>
<noscript><p class="no-script">灵契仙途 · 请启用 JavaScript 查看游戏首页。<a href="https://qm.qq.com/q/S6ql07Q72m">加入官方群 547205828</a></p></noscript>
<div id="app" v-cloak>
<header class="masthead">
  <img class="landscape" src="/webstatic/home/hero-mountains.webp" alt="" fetchpriority="high" width="1672" height="941">
<div class="wrap">
  <nav aria-label="主导航">
    <a class="brand" href="/" aria-label="灵契仙途首页"><span class="seal" aria-hidden="true">契</span>灵契仙途</a>
    <div class="nav-links"><a href="#explore">仙途万象</a><a href="#professions">四道同修</a><a href="#start">初入仙途</a><a href="#rankings">风云榜</a></div>
    <div class="nav-btns" v-if="loggedIn">
      <span class="user-chip">{{ userQQ }}</span><el-button @click="goPortal">我的角色</el-button><el-button @click="logout">退出</el-button>
    </div>
    <div class="nav-btns" v-else><el-button @click="openAuth('login')">登录</el-button><el-button class="btn-grad" @click="openAuth('register')">注册</el-button></div>
  </nav>
  <div class="hero">
    <div class="hero-copy">
      <div class="eyebrow">灵契仙途 / QQ 群聊修仙游戏</div>
      <h1>一念入仙途。<br><span>山海有灵契。</span></h1>
      <p class="hero-intro">择一道修行，结一世灵契。<br>带上你的灵宠，与群友一起闯秘境、建宗门。<br>从无名修士，到属于你的仙途传说。</p>
      <div class="cta">
        <el-button class="btn-grad" size="large" @click="loggedIn ? goPortal() : openAuth('login')">{{ loggedIn ? '回到我的角色' : '进入玩家中心' }} &nbsp; ↗</el-button>
        <a class="secondary" href="https://qm.qq.com/q/S6ql07Q72m" target="_blank" rel="noopener">加入官方群 &nbsp; →</a>
      </div>
      <button v-if="appVer.ok" class="app-link" @click="downloadApp">安卓客户端下载 · {{ appVer.version_name }} ↗</button>
    </div>
    <div class="hero-poem" aria-hidden="true">携灵宠作伴<br>向山海而行</div>
    <a class="hero-note" href="#explore">向下展开仙途长卷 &nbsp; ↓</a>
  </div>
  <div class="stats reveal">
    <div class="stat-card"><div class="num">{{ hasData ? fmt(disp.players) : '—' }}</div><div class="lbl">全服玩家</div></div>
    <div class="stat-card"><div class="num">{{ hasData ? fmt(disp.auth_groups) : '—' }}</div><div class="lbl">授权群聊</div></div>
    <div class="stat-card"><div class="num">{{ hasData ? fmt(disp.pets) : '—' }}</div><div class="lbl">在册宠物</div></div>
    <div class="stat-card"><div class="num">{{ hasData ? fmt(disp.tomb_players) : '—' }}</div><div class="lbl">摸金玩家</div></div>
  </div>

  <div class="data-state" v-if="homeError" role="status">{{ homeError }}<button @click="loadHome" :disabled="loading">重新加载</button></div>
</div>
</header>
<main class="wrap" id="explore">
  <nav class="mobile-shortcuts" aria-label="手机快捷入口"><a href="#professions"><b aria-hidden="true">修</b>四道同修</a><a href="#start"><b aria-hidden="true">启</b>入门指引</a><a href="#rankings"><b aria-hidden="true">榜</b>风云榜</a><a href="/chat"><b aria-hidden="true">游</b>网页游玩</a></nav>
  <div class="update-strip"><strong>仙途新事</strong><p>修士、灵宠、坐骑，一处查看。坐骑外观定制现已开放。</p><a class="text-link" href="#player-center">查看玩家中心新功能 ↗</a></div>

  <section class="section" id="professions" aria-labelledby="profession-title">
    <div class="section-kicker">修行 · 各有其道</div>
    <div class="section-head"><h2 id="profession-title">四条路，同赴仙途。</h2><span>剑意、坚躯、灵法、血煞 · 点击了解职业</span></div>
    <div class="profession-grid">
      <button v-for="role in professions" :key="role.key" class="profession-card" :aria-pressed="selectedRole.key===role.key" @click="selectedRole=role" :aria-label="'了解'+role.name">
        <img :src="'/webstatic/home/'+role.key+'.webp'" :alt="role.name+'职业立绘'" width="512" height="768" loading="lazy">
        <span class="profession-label"><b>{{ role.name }}</b><span>{{ role.tag }}</span></span>
      </button>
    </div>
    <div class="profession-detail" aria-live="polite"><p><strong>{{ selectedRole.name }}</strong> {{ selectedRole.desc }}</p><button class="command" @click="copyCommand('选择职业 '+selectedRole.name)">选择职业 {{ selectedRole.name }} &nbsp; ⧉</button></div>
  </section>

  <section class="section" aria-labelledby="world-title">
    <div class="section-kicker">历练 · 不止一种日常</div>
    <div class="section-head"><h2 id="world-title">这方天地，等你来闯。</h2><span>修炼有进境，同行有故人</span></div>
    <div class="world-grid">
      <div class="world-art"><img src="/webstatic/home/world.webp" alt="仙途地图：从青山宗门到云海仙境" width="800" height="1200" loading="lazy"><div class="world-caption"><small>二十方山海 · 普通 / 困难</small><h3>越过眼前山，<br>还有万重境。</h3><p>历练地图、组队秘境、世界首领、深渊挑战。</p><button class="command" @click="copyCommand('仙途地图')">仙途地图 &nbsp; ⧉</button></div></div>
      <div>
        <article class="feature-row"><span class="feature-no">01</span><div><h3>修士与灵宠，并肩而战</h3><p>修炼破境、觉醒神通、锻造六件装备。灵宠可选择攻击、守护或辅助专长；坐骑与道侣，也会成为你修行路上的助力。</p><button class="command" @click="copyCommand('我的修士')">我的修士 &nbsp; ⧉</button></div></article>
        <article class="feature-row"><span class="feature-no">02</span><div><h3>一方洞天，一步一重关</h3><p>从初识到仙门，逐层挑战洞天试炼。与灵宠共同成长，完成仙途毕业目标，记下这一程的修行。</p><button class="command" @click="copyCommand('我的洞天')">我的洞天 &nbsp; ⧉</button></div></article>
        <article class="feature-row"><span class="feature-no">03</span><div><h3>与群友开宗立派</h3><p>创建或加入宗门，一起做任务、外出探索、镇守宗门。捐献资源、积累贡献，再去宗门兑换所需。</p><button class="command" @click="copyCommand('宗门帮助')">宗门帮助 &nbsp; ⧉</button></div></article>
      </div>
    </div>
  </section>

  <section class="section portal-band" id="player-center" aria-labelledby="portal-title">
    <div><div class="eyebrow">玩家中心 · 近期更新</div><h2 id="portal-title">你的修行，<br>打开就看得见。</h2><p>群里继续游历，网页随时查看。<br>绑定所在群的角色，修士、灵宠与坐骑都有自己的位置。</p><div class="cta"><el-button class="btn-grad" @click="loggedIn ? goPortal() : openAuth('login')">{{ loggedIn ? '查看我的角色' : '登录并绑定角色' }} &nbsp; ↗</el-button><a class="secondary" href="/chat">网页游玩 →</a></div></div>
    <div class="portal-features">
      <div class="portal-feature"><span>01</span><div><b>修士档案与角色绑定</b><p>查看职业、境界、洞天、灵根、神通与战力构成；没有灵宠，也能绑定修士。</p></div></div>
      <div class="portal-feature"><span>02</span><div><b>灵宠、坐骑与背包</b><p>查看养成状态、坐骑信息和资产，使用背包道具、兑换卡密。</p></div></div>
      <div class="portal-feature"><span>03</span><div><b>把喜欢的形象，带进仙途</b><p>灵宠定制与坐骑外观定制。解锁对应资格后上传图片、提交申请，审核通过后使用。</p></div></div>
    </div>
  </section>

  <section class="section" id="start" aria-labelledby="start-title">
    <div class="section-kicker">初见 · 从这三步开始</div>
    <div class="section-head"><h2 id="start-title">第一步，在群里发一句话。</h2><span>点击指令复制，回到已开通游戏的 QQ 群发送</span></div>
    <div class="guide-grid">
      <article class="guide-step"><span class="step-index">壹 / 创建角色</span><h3>先有一个自己的道号</h3><p>加入官方群或已开通游戏的群，发送指令创建修士。</p><button class="command" @click="copyCommand('创建角色')">创建角色 &nbsp; ⧉</button></article>
      <article class="guide-step"><span class="step-index">贰 / 选择职业</span><h3>选你喜欢的修行之道</h3><p>剑修、体修、灵修、魔修任选其一，体验各自的战斗方式。</p><button class="command" @click="copyCommand('选择职业 '+selectedRole.name)">选择职业 {{ selectedRole.name }} &nbsp; ⧉</button></article>
      <article class="guide-step"><span class="step-index">叁 / 结契灵宠</span><h3>这一程，有伙伴同行</h3><p>九尾狐、卡比兽、七夕青鸟，选一只初始伙伴，再出发历练。</p><button class="command" @click="copyCommand('结契灵宠 九尾狐')">结契灵宠 九尾狐 &nbsp; ⧉</button></article>
    </div>
    <p class="guide-foot">已在群里玩过？直接登录玩家中心绑定已有角色。若群内提示需要绑定 QQ，请先发送「绑定教程」。完整菜单发送「灵契仙途」。</p>
  </section>

  <section class="section" aria-labelledby="library-title">
    <div class="section-head"><h2 id="library-title">修行之外，也有江湖。</h2><span>展开查看玩法与常用指令</span></div>
    <div class="play-library">
      <details v-for="group in playGroups" :key="group.title"><summary><b>{{ group.title }}</b><span>{{ group.subtitle }}</span></summary><div class="library-body"><p>{{ group.desc }}</p><div class="command-list"><button class="command" v-for="cmd in group.commands" :key="cmd" @click="copyCommand(cmd)">{{ cmd }} &nbsp; ⧉</button></div></div></details>
    </div>
  </section>

  <section class="section rank-section" id="rankings">
    <div class="section-kicker">问道 · 修士登顶</div>
    <div class="section-head"><h2>仙途战力榜</h2><span>按修士综合战力排序（修士 + 灵宠 + 坐骑 + 道侣）</span></div>
    <p v-if="homeError" class="data-state" role="status">{{ homeError }}</p>
    <div class="boards">
      <div class="board full">
        <ol class="mobile-rank-list" aria-label="修士排行"><li v-for="(row,index) in cultRank" :key="index"><span class="rank-position">{{ index+1 }}</span><div class="rank-person"><b>{{ row.name }}</b><small>{{ row.realm + ' · ' + row.profession }} · Lv{{ row.level }}</small></div><strong class="rank-power">{{ fmtPower(row.power) }}</strong></li><li v-if="!cultRank.length" class="rank-empty">{{ loading ? '正在读取榜单…' : '暂无上榜数据' }}</li></ol>
        <el-table :data="cultRank" v-loading="loading" element-loading-background="transparent" empty-text="暂无修士上榜">
          <el-table-column label="排名" width="80">
            <template #default="s"><span class="rk" :class="s.$index<3 ? 'g'+(s.$index+1) : ''">{{ s.$index+1 }}</span></template>
          </el-table-column>
          <el-table-column prop="name" label="道号" min-width="140" show-overflow-tooltip></el-table-column>
          <el-table-column label="等级" width="90">
            <template #default="s">Lv{{ s.row.level }}</template>
          </el-table-column>
          <el-table-column prop="realm" label="境界" width="110" show-overflow-tooltip></el-table-column>
          <el-table-column prop="profession" label="职业" width="110"></el-table-column>
          <el-table-column label="战力" align="right" min-width="110">
            <template #default="s"><span class="pw">{{ fmtPower(s.row.power) }}</span></template>
          </el-table-column>
        </el-table>
      </div>
    </div>
  </section>

  <section class="section rank-section">
    <div class="section-kicker">风云 · 群雄留名</div>
    <div class="section-head"><h2>灵宠战力榜</h2><span>按灵宠自身战力排序 · 修士综合战力见上方「仙途战力榜」</span></div>
    <p v-if="homeError" class="data-state" role="status">{{ homeError }}</p>
    <div class="boards">
      <div class="board full">
        <ol class="mobile-rank-list" aria-label="灵宠排行"><li v-for="(row,index) in petRank" :key="index"><span class="rank-position">{{ index+1 }}</span><div class="rank-person"><b>{{ row.nickname }}</b><small>{{ row.stage + ' · ' + row.quality }} · Lv{{ row.level }}</small></div><strong class="rank-power">{{ fmtPower(row.power) }}</strong></li><li v-if="!petRank.length" class="rank-empty">{{ loading ? '正在读取榜单…' : '暂无上榜数据' }}</li></ol>
        <el-table :data="petRank" v-loading="loading" element-loading-background="transparent" empty-text="暂无宠物上榜">
          <el-table-column label="排名" width="80">
            <template #default="s"><span class="rk" :class="s.$index<3 ? 'g'+(s.$index+1) : ''">{{ s.$index+1 }}</span></template>
          </el-table-column>
          <el-table-column prop="nickname" label="昵称" min-width="140" show-overflow-tooltip></el-table-column>
          <el-table-column label="等级" width="90">
            <template #default="s">Lv{{ s.row.level }}</template>
          </el-table-column>
          <el-table-column prop="stage" label="阶段" width="110"></el-table-column>
          <el-table-column label="级别" width="120">
            <template #default="s"><span class="q">{{ s.row.quality }}</span></template>
          </el-table-column>
          <el-table-column label="战力" align="right" min-width="110">
            <template #default="s"><span class="pw">{{ fmtPower(s.row.power) }}</span></template>
          </el-table-column>
        </el-table>
      </div>
    </div>
  </section>

  <div class="section reveal">
    <div class="section-head"><h2>摸金风云榜</h2><span>地宫探险 · 记录每一笔收获</span></div>
    <div class="boards">
      <div class="board full">
        <h3>摸金排行 · 全服</h3>
        <div class="sub">按永久冥币总量排序</div>
        <ol class="mobile-rank-list" aria-label="摸金排行"><li v-for="(row,index) in tombRank" :key="index"><span class="rank-position">{{ index+1 }}</span><div class="rank-person"><b>{{ row.qq }}</b><small>{{ '累计冥币' }}</small></div><strong class="rank-power">{{ fmt(row.value) }}</strong></li><li v-if="!tombRank.length" class="rank-empty">{{ loading ? '正在读取榜单…' : '暂无上榜数据' }}</li></ol>
        <el-table :data="tombRank" empty-text="暂无上榜数据">
          <el-table-column label="排名" width="80">
            <template #default="s"><span class="rk" :class="s.$index<3 ? 'g'+(s.$index+1) : ''">{{ s.$index+1 }}</span></template>
          </el-table-column>
          <el-table-column prop="qq" label="用户" min-width="140"></el-table-column>
          <el-table-column label="冥币" align="right" min-width="110">
            <template #default="s"><span class="mb">{{ fmt(s.row.value) }}</span></template>
          </el-table-column>
        </el-table>
      </div>
      <div class="board">
        <h3>今日摸金榜</h3>
        <div class="sub">{{ todaySub }}</div>
        <ol class="mobile-rank-list" aria-label="摸金排行"><li v-for="(row,index) in tombToday" :key="index"><span class="rank-position">{{ index+1 }}</span><div class="rank-person"><b>{{ row.qq }}</b><small>{{ '今日获得' }}</small></div><strong class="rank-power">{{ fmt(row.value) }}</strong></li><li v-if="!tombToday.length" class="rank-empty">{{ loading ? '正在读取榜单…' : '暂无上榜数据' }}</li></ol>
        <el-table :data="tombToday" empty-text="暂无上榜数据">
          <el-table-column label="排名" width="70">
            <template #default="s"><span class="rk" :class="s.$index<3 ? 'g'+(s.$index+1) : ''">{{ s.$index+1 }}</span></template>
          </el-table-column>
          <el-table-column prop="qq" label="用户" min-width="120"></el-table-column>
          <el-table-column label="今日获得" align="right" min-width="100">
            <template #default="s"><span class="mb">{{ fmt(s.row.value) }}</span></template>
          </el-table-column>
        </el-table>
      </div>
      <div class="board">
        <h3>昨日摸金榜</h3>
        <div class="sub">{{ ystSub }}</div>
        <ol class="mobile-rank-list" aria-label="摸金排行"><li v-for="(row,index) in tombYst" :key="index"><span class="rank-position">{{ index+1 }}</span><div class="rank-person"><b>{{ row.qq }}</b><small>{{ '昨日获得' }}</small></div><strong class="rank-power">{{ fmt(row.value) }}</strong></li><li v-if="!tombYst.length" class="rank-empty">{{ loading ? '正在读取榜单…' : '暂无上榜数据' }}</li></ol>
        <el-table :data="tombYst" empty-text="暂无上榜数据">
          <el-table-column label="排名" width="70">
            <template #default="s"><span class="rk" :class="s.$index<3 ? 'g'+(s.$index+1) : ''">{{ s.$index+1 }}</span></template>
          </el-table-column>
          <el-table-column prop="qq" label="用户" min-width="120"></el-table-column>
          <el-table-column label="昨日获得" align="right" min-width="100">
            <template #default="s"><span class="mb">{{ fmt(s.row.value) }}</span></template>
          </el-table-column>
        </el-table>
      </div>
    </div>
  </div>

  <div class="section reveal">
    <div class="section-head"><h2>江湖不远，群里见。</h2><span>一起玩，也一起把仙途变得更好</span></div>
    <div class="links">
      <a class="link-card" href="https://qm.qq.com/q/S6ql07Q72m" target="_blank" rel="noopener">
        <div class="ic" aria-hidden="true">01</div>
        <div>
          <div class="t">小飞机器人 · 官方群</div>
          <div class="d">官方 QQ 群：547205828 · 点击一键加群，交流攻略、领取福利</div>
        </div>
        <div class="go">↗</div>
      </a>
      <a class="link-card" href="https://pay.ldxp.cn/shop/2P5XIVMD" target="_blank" rel="noopener">
        <div class="ic" aria-hidden="true">02</div>
        <div>
          <div class="t">充值入口</div>
          <div class="d">灵石 / 玄晶 / 天晶卡密自助购买，兑换即时到账</div>
        </div>
        <div class="go">↗</div>
      </a>
      <button type="button" class="link-card" @click="goFeedback">
        <div class="ic" aria-hidden="true">03</div>
        <div>
          <div class="t">问题反馈</div>
          <div class="d">提交遇到的问题或玩法建议，登录后可查看管理员回复。</div>
        </div>
        <div class="go">↗</div>
      </button>
    </div>
  </div>

  <footer><div>灵契仙途 · 与灵宠结契，共赴仙途。<br>榜单每 30 秒刷新；不同玩法按各自规则统计。</div><div><a href="/portal">玩家中心</a> &nbsp; / &nbsp; <a href="/chat">网页游玩</a> &nbsp; / &nbsp; <a href="https://qm.qq.com/q/S6ql07Q72m" target="_blank" rel="noopener">官方群 547205828</a> &nbsp; / &nbsp; <a href="/agreement" style="display:inline-block;border:1px solid #a68d5e;border-radius:2px;padding:0 9px;line-height:21px;color:#7d6a45">用户协议</a></div></footer>
</main>

<nav class="mobile-home-dock" aria-label="手机玩家入口" v-show="!auth.show"><a href="#start">新手指引</a><el-button @click="loggedIn ? goPortal() : openAuth('login')">{{ loggedIn ? '回到我的角色' : '进入玩家中心' }} &nbsp; ↗</el-button></nav>
<el-dialog v-model="auth.show" :title="authTitle" width="400px" class="auth-dialog" align-center>
  <div class="auth-hint">{{ authHint }}</div>
  <el-tabs v-if="auth.mode==='login'" v-model="auth.tab">
    <el-tab-pane label="密码登录" name="pwd"></el-tab-pane>
    <el-tab-pane label="邮箱验证码登录" name="email"></el-tab-pane>
  </el-tabs>
  <el-form label-position="top" @submit.prevent="submitAuth">
    <template v-if="auth.mode==='register' || (auth.mode==='login' && auth.tab==='pwd')">
      <el-form-item label="QQ 号">
        <el-input v-model="auth.qq" placeholder="请输入 QQ 号" size="large" autocomplete="username" clearable></el-input>
      </el-form-item>
      <el-form-item label="密码">
        <el-input v-model="auth.pwd" type="password" :placeholder="auth.mode==='register' ? '至少 6 位' : '请输入密码'" size="large" show-password @keyup.enter="submitAuth"></el-input>
      </el-form-item>
      <el-form-item v-if="auth.mode==='register'" label="确认密码">
        <el-input v-model="auth.pwd2" type="password" placeholder="再次输入密码" size="large" show-password></el-input>
      </el-form-item>
    </template>
    <template v-if="auth.mode!=='login' || auth.tab==='email'">
      <el-form-item label="邮箱">
        <el-input v-model="auth.email" placeholder="请输入邮箱地址" size="large" autocomplete="email" clearable></el-input>
      </el-form-item>
      <el-form-item label="邮箱验证码">
        <div style="display:flex;gap:10px;width:100%">
          <el-input v-model="auth.code" placeholder="6 位验证码" size="large" maxlength="6" @keyup.enter="submitAuth" style="flex:1"></el-input>
          <el-button size="large" round plain :disabled="auth.countdown>0" :loading="auth.sending" @click="sendCode" style="white-space:nowrap">{{ auth.countdown>0 ? auth.countdown + 's' : '获取验证码' }}</el-button>
        </div>
      </el-form-item>
    </template>
  </el-form>
  <el-button class="btn-grad" size="large" round style="width:100%" :loading="auth.loading" @click="submitAuth">{{ auth.mode==='register' ? '注 册' : (auth.mode==='bind' ? '绑定并登录' : '登 录') }}</el-button>
  <div class="auth-switch" v-if="auth.mode==='register'">已有账号？<a @click="openAuth('login')">直接登录</a></div>
  <div class="auth-switch" v-else-if="auth.mode==='login'">还没有账号？<a @click="openAuth('register')">立即注册</a></div>
  <div class="auth-switch" v-else>绑错账号？<a @click="openAuth('login')">返回登录</a></div>
</el-dialog>
</div>

<script src="/webstatic/vue.global.prod.js"></script>
<script src="/webstatic/element-plus.full.min.js"></script>
<script src="/webstatic/element-plus-zh-cn.min.js"></script>
<script>
const CSRF = "{{CSRF_TOKEN}}";
const { createApp, reactive, ref, onMounted } = Vue;
const { ElMessage } = ElementPlus;

createApp({
  setup(){
    const loggedIn = ref(false);
    const userQQ = ref('');
    const loading = ref(true);
    const hasData = ref(false);
    const homeError = ref('');
    const professions = [
      {key:'sword',name:'剑修',tag:'剑意爆发 · 破甲斩敌',desc:'每三回合蓄起剑意，以爆发与破甲直面强敌。'},
      {key:'body',name:'体修',tag:'护盾反击 · 护卫队友',desc:'以坚躯护卫队友，用护盾承伤，在受击中反击。'},
      {key:'spirit',name:'灵修',tag:'疗愈净化 · 灵法相助',desc:'队友受伤时施以治疗，无需治疗时以灵法进攻。'},
      {key:'demon',name:'魔修',tag:'以血催煞 · 攻击汲生',desc:'以血催煞，在攻击中汲取生命，走另一条修行路。'}
    ];
    const selectedRole = ref(professions[0]);
    const playGroups = [
      {title:'灵宠养成',subtitle:'砸蛋收集 / 进化飞升 / 天赋与炼丹',desc:'从获得第一只灵宠开始，升级、进化、飞升与渡劫；还可学习秘技、打造神器，挑战宠物副本与剧情任务。',commands:['砸蛋','我的宠物','宠物市场','灵宠觉醒','宠物副本','宠物剧情任务']},
      {title:'仙途历练',subtitle:'装备锻造 / 组队秘境 / 世界首领 / 深渊',desc:'修炼积累修为，锻造并进阶装备，选择历练与挑战。洞天试炼和仙途毕业，也在等你完成。',commands:['修士修炼','修士装备','锻造 灵剑','仙途地图','世界首领','我的洞天','仙途毕业']},
      {title:'坐骑与情缘',subtitle:'坐骑养成 / 外观定制 / 道侣双修',desc:'带上坐骑踏入仙途，与另一位修士结为道侣。玩家中心可查看坐骑，并在解锁资格后申请专属外观。',commands:['我的坐骑','道侣情缘','道侣双修']},
      {title:'宗门与家园',subtitle:'开宗立派 / 宗门任务 / 家园经营',desc:'与群友经营宗门、完成任务与探索；闲下来，也可以回到自己的家园。',commands:['宗门帮助','查看宗门','宗门任务','宗门兑换','家园']},
      {title:'摸金与棋局',subtitle:'地宫探险 / 扫雷 / 群聊棋类对弈',desc:'下地宫摸金，或与群友摆一局。象棋、围棋、五子棋、军棋和斗兽棋都有各自的玩法。发送完整菜单查看开局方式。',commands:['摸金介绍','灵契仙途']},
      {title:'每日与排行',subtitle:'签到 / 商城背包 / 本群与全服战力榜',desc:'签到领取日常奖励，查看背包与商城。群内仙途战力榜展示综合战力，官网下方保留灵宠自身战力和摸金榜。',commands:['签到','查看背包','宠物商城','仙途战力榜','仙途战力榜全服']}
    ];
    async function copyCommand(command){
      try { await navigator.clipboard.writeText(command); ElMessage.success('已复制「'+command+'」，请到游戏群发送'); }
      catch(e) { ElMessage.info({message:'请在游戏群发送：'+command,duration:6000}); }
    }
    const disp = reactive({players:0, auth_groups:0, pets:0, tomb_players:0});
    const appVer = reactive({ok:false, version_name:'', url:''});
    const petRank = ref([]);
    const cultRank = ref([]);
    const tombRank = ref([]);
    const tombToday = ref([]);
    const tombYst = ref([]);
    const todaySub = ref('统计今日 00:00 至今获得冥币');
    const ystSub = ref('前三名可领取随机宠物经验奖励');
    const auth = reactive({show:false, mode:'login', tab:'pwd', qq:'', pwd:'', pwd2:'', email:'', code:'', loading:false, sending:false, countdown:0, hint:''});
    let cdTimer = null;
    const authTitle = Vue.computed(() => auth.mode==='register' ? '注册' : (auth.mode==='bind' ? '绑定邮箱' : '登录'));
    const authHint = Vue.computed(() => auth.hint || (
      auth.mode==='register' ? '使用 QQ 号创建账号，需邮箱验证后方可注册' :
      auth.mode==='bind' ? '该账号尚未绑定邮箱，绑定后才能登录' :
      (auth.tab==='email' ? '使用已绑定的邮箱接收验证码登录' : '使用注册时的 QQ 号登录玩家中心')));

    const fmt = n => Number(n||0).toLocaleString('zh-CN');
    const fmtPower = bp => bp >= 1e12 ? (bp/1e12).toFixed(2) + '万亿' : bp >= 1e8 ? (bp/1e8).toFixed(2) + '亿' : bp >= 10000 ? (bp/10000).toFixed(2) + '万' : fmt(bp);

    function animate(key, target){
      if(matchMedia('(prefers-reduced-motion: reduce)').matches){ disp[key] = target; return; }
      const from = disp[key] || 0;
      if(from === target){ disp[key] = target; return; }
      const start = performance.now(), dur = 1200;
      function tick(t){
        const p = Math.min(1, (t - start) / dur);
        const eased = 1 - Math.pow(1 - p, 3);
        disp[key] = Math.round(from + (target - from) * eased);
        if(p < 1) requestAnimationFrame(tick);
      }
      requestAnimationFrame(tick);
    }

    async function post(path, data){
      const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
      return r.json();
    }

    async function checkAuth(){
      try{
        const r = await fetch('/api/portal/me');
        if(!r.ok) return;
        const d = await r.json();
        if(!d || !d.ok) return;
        loggedIn.value = true;
        userQQ.value = String(d.qq || (d.account && d.account.qq) || '');
      }catch(e){}
    }

    async function loadHome(){
      loading.value = true;
      try{
        const response = await fetch('/api/portal/home');
        if(!response.ok) throw new Error('home unavailable');
        const r = await response.json();
        if(!r.ok || !r.stats) throw new Error('invalid home data');
        hasData.value = true;
        homeError.value = '';
        animate('players', r.stats.players);
        animate('auth_groups', r.stats.auth_groups);
        animate('pets', r.stats.pets);
        animate('tomb_players', r.stats.tomb_players);
        petRank.value = r.pet_rank || [];
        cultRank.value = r.cultivator_rank || [];
        tombRank.value = r.tomb_rank || [];
        tombToday.value = r.tomb_today || [];
        tombYst.value = r.tomb_yesterday || [];
        todaySub.value = `统计 ${r.date_today} 00:00 至今获得冥币，每日 0 点重置`;
        ystSub.value = `统计 ${r.date_yesterday} 全天 · 前三名可领取随机宠物经验奖励`;
      }catch(e){
        homeError.value = hasData.value ? '数据更新暂时中断，当前显示上次获取结果。' : '榜单暂时未能加载，请稍后重试。';
      }finally{ loading.value = false; }
    }

    function openAuth(mode){
      auth.mode = mode; auth.tab = 'pwd'; auth.qq = ''; auth.pwd = ''; auth.pwd2 = '';
      auth.email = ''; auth.code = ''; auth.hint = '';
      auth.show = true;
    }

    function startCountdown(sec){
      auth.countdown = sec;
      if(cdTimer) clearInterval(cdTimer);
      cdTimer = setInterval(() => {
        auth.countdown -= 1;
        if(auth.countdown <= 0){ clearInterval(cdTimer); cdTimer = null; auth.countdown = 0; }
      }, 1000);
    }

    async function sendCode(){
      const email = auth.email.trim();
      if(!email){ ElMessage.warning('请先输入邮箱地址'); return; }
      const purpose = auth.mode==='register' ? 'register' : (auth.mode==='bind' ? 'bind' : 'login');
      auth.sending = true;
      try{
        const r = await post('/api/portal/send_email_code', {email, purpose});
        if(r.ok){ ElMessage.success(r.msg || '验证码已发送'); startCountdown(60); }
        else ElMessage.error(r.msg || '发送失败');
      }catch(e){ ElMessage.error('网络异常，请稍后再试'); }
      finally{ auth.sending = false; }
    }

    function goFeedback(){
      if(loggedIn.value){ location.href = '/feedback'; }
      else { openAuth('login'); auth.hint = '登录后即可提交问题反馈'; }
    }

    function goPortal(){ location.href = '/portal'; }

    async function loadAppVer(){
      try{
        const r = await (await fetch('/api/app/version')).json();
        if(r && r.ok && r.url){
          appVer.ok = true;
          appVer.version_name = r.version_name || '';
          appVer.url = r.url;
        }
      }catch(e){}
    }
    function downloadApp(){ if(appVer.url) location.href = appVer.url; }

    async function logout(){
      try{ await fetch('/api/portal/logout', {method:'POST', headers:{'X-CSRF-Token': CSRF}}); }catch(e){}
      ElMessage.success('已退出登录');
      setTimeout(()=>location.reload(), 500);
    }

    async function submitAuth(){
      const qq = auth.qq.trim(), pwd = auth.pwd;
      const email = auth.email.trim(), code = auth.code.trim();
      auth.loading = true;
      try{
        if(auth.mode === 'register'){
          if(!qq || !pwd){ ElMessage.warning('请填写 QQ 号和密码'); return; }
          if(pwd.length < 6){ ElMessage.warning('密码至少 6 位'); return; }
          if(pwd !== auth.pwd2){ ElMessage.warning('两次输入的密码不一致'); return; }
          if(!email || !code){ ElMessage.warning('请填写邮箱并获取验证码'); return; }
          const r = await post('/api/portal/register', {qq, password: pwd, email, code});
          if(!r.ok){ ElMessage.error(r.msg || '注册失败'); return; }
          ElMessage.success('注册成功，正在登录…');
          const r2 = await post('/api/portal/login', {qq, password: pwd});
          if(r2.ok){ location.href = '/portal'; return; }
          openAuth('login');
          ElMessage.info('注册成功，请登录');
          return;
        }
        if(auth.mode === 'bind'){
          if(!email || !code){ ElMessage.warning('请填写邮箱并获取验证码'); return; }
          const r = await post('/api/portal/bind_email', {qq, password: pwd, email, code});
          if(!r.ok){ ElMessage.error(r.msg || '绑定失败'); return; }
          ElMessage.success('绑定成功，正在进入玩家中心…'); location.href = '/portal';
          return;
        }
        if(auth.tab === 'email'){
          if(!email || !code){ ElMessage.warning('请填写邮箱并获取验证码'); return; }
          const r = await post('/api/portal/login_email', {email, code});
          if(!r.ok){ ElMessage.error(r.msg || '登录失败'); return; }
          ElMessage.success('登录成功，正在进入玩家中心…'); location.href = '/portal';
          return;
        }
        if(!qq || !pwd){ ElMessage.warning('请填写 QQ 号和密码'); return; }
        const r = await post('/api/portal/login', {qq, password: pwd});
        if(r.ok){ ElMessage.success('登录成功，正在进入玩家中心…'); location.href = '/portal'; return; }
        if(r.need_bind_email){
          auth.mode = 'bind'; auth.email = ''; auth.code = ''; auth.hint = '';
          ElMessage.warning(r.msg || '请先绑定邮箱');
          return;
        }
        ElMessage.error(r.msg || '操作失败');
      }catch(e){ ElMessage.error('网络异常，请稍后再试'); }
      finally{ auth.loading = false; }
    }

    onMounted(()=>{
      checkAuth();
      loadHome();
      loadAppVer();
      setInterval(loadHome, 30000);
    });

    return {professions, selectedRole, playGroups, copyCommand, hasData, homeError, loadHome, loggedIn, userQQ, loading, disp, appVer, petRank, cultRank, tombRank, tombToday, tombYst, todaySub, ystSub,
            auth, authTitle, authHint, fmt, fmtPower, openAuth, goFeedback, goPortal, downloadApp, logout, submitAuth, sendCode};
  }
}).use(ElementPlus, {locale: ElementPlusLocaleZhCn}).mount('#app');
</script>
</body>
</html>
"""
