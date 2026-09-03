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
        msg["Subject"] = Header(f"宠物乐园 · {purpose_label}验证码", "utf-8")
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

    def _player_summary(self, group_id: str, qq: str, pet_index: int = 0) -> dict:
        key = self.store.make_key(group_id, qq)
        player = self.store._data["players"].get(key)
        if not player:
            raise web.HTTPNotFound(text="未找到该宠物")
        pending = self.store.get_pet_custom_reviews(group_id, qq, status="pending")
        rejected = self.store.get_pet_custom_reviews(group_id, qq, status="rejected")
        # 只返回最近一条拒绝原因
        last_rejected = sorted(rejected, key=lambda x: x.get("created_at", 0), reverse=True)[:1]
        rp = self._resolve_player_pet(player, pet_index)
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
            "cooldowns": self._cooldown_list(player, pet_index),
            "skills": list(rp.get("skills", [])),
            "artifact": rp.get("artifact"),
            "artifact_names": list(data.ARTIFACTS.keys()),
            "skill_names": list(data.SKILLS.keys()),
            "custom_pending": pending,
            "custom_rejected": last_rejected,
            "custom_remaining": {
                "image": self.store.remaining_custom_changes(player, "image"),
                "species_name": self.store.remaining_custom_changes(player, "species_name"),
            },
            "auto_cultivation": dict(rp.get("auto_cultivation", {})),
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
        app.router.add_post("/api/portal/auto_cultivation", self._api_auto_cultivation)
        app.router.add_post("/api/portal/custom_redeem", self._api_custom_redeem)
        app.router.add_post("/api/portal/custom_submit", self._api_custom_submit)
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
        return web.json_response({
            "ok": True,
            "account": {
                "id": account["id"],
                "qq": account["qq"],
                "email_masked": self._mask_email(account.get("email") or ""),
            },
            "bound_pets": bound,
        })

    async def _api_bind_query(self, request: web.Request) -> web.Response:
        """查询指定群+用户ID 下的宠物列表，供绑定前选择。"""
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
            return web.json_response({"ok": False, "msg": "该群聊与用户 ID 下不存在宠物"})
        existing = self.store.account_for_pet(group_id, qq)
        if existing:
            # 已绑定则直接返回当前绑定信息
            return web.json_response({"ok": False, "already_bound": True, "msg": "该宠物已被绑定"})
        pets = player.get("pets", [])
        if not pets:
            return web.json_response({"ok": False, "msg": "该用户还没有宠物"})
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
        return web.json_response({"ok": True, "pets": pet_list})

    async def _api_bind_auto(self, request: web.Request) -> web.Response:
        """根据登录账号的绑定 QQ，自动列出各群名下宠物及其绑定情况（含群 ID + 用户 ID）。

        匹配规则：玩家槽位 (group_id, user_id) 属于该账号，当且仅当
        - user_id == 账号绑定 QQ；或 user_id 经 qq_bindings 绑定到该 QQ（平台 openid → QQ号）。
        返回每个名下宠物的绑定状态：none=未绑定 / me=已绑到本账号 / other=被其它网页账号绑定（可强要回）。
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
            for bp in acc.get("bound_pets", []):
                bound_map[(str(bp.get("group")), str(bp.get("qq")))] = acc_id
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
            pets = player.get("pets", [])
            if not pets:
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
            if pet_list:
                entry = {
                    "qq": uid,
                    "pet_count": len(pet_list),
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

    async def _api_auto_cultivation(self, request: web.Request) -> web.Response:
        self._check_csrf(request)
        self._require_session(request)
        body = await request.json()
        group_id = str(body.get("group_id", "")).strip()
        qq = str(body.get("qq", "")).strip()
        enabled = bool(body.get("enabled"))
        if not group_id or not qq:
            return web.json_response({"ok": False, "msg": "缺少群号或用户 ID"})
        owner = self.store.account_for_pet(group_id, qq)
        sess = self._current_session(request)
        if owner != sess.get("aid"):
            raise web.HTTPForbidden(text="你没有绑定该宠物")
        key = self.store.make_key(group_id, qq)
        player = self.store._data["players"].get(key)
        if not player:
            return web.json_response({"ok": False, "msg": "未找到该宠物"})
        pet_index = int(body.get("pet_index", 0))
        pet = self._resolve_player_pet(player, pet_index)
        ascended = pet and data.STAGES.index(pet.get("stage", "")) >= data.STAGES.index("飞升")
        if not pet or (not pet.get("custom") and not ascended):
            return web.json_response({"ok": False, "msg": "自动修炼仅限定制宠物或飞升宠物"})
        ac = pet.setdefault("auto_cultivation", {
            "enabled": False,
            "started_at": 0,
            "total_sessions": 0,
            "total_exp": 0,
            "last_run_at": 0,
        })
        ac["enabled"] = enabled
        if enabled:
            ac["started_at"] = int(time.time())
        await self.store.save()
        return web.json_response({
            "ok": True,
            "msg": "已开启自动修炼" if enabled else "已关闭自动修炼",
            "auto_cultivation": dict(ac),
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
                        "🎉 **全服贺电！宠物乐园迎来全新混沌定制大师！** 🎉\n\n"
                        f"👑 尊贵的训练家 **{nickname}**（QQ：{show_qq}）\n"
                        f"为心爱的 **{pet_nick}** 解锁了【混沌定制】权限！\n\n"
                        f"✨ **{pet_nick}** 已褪去凡躯，化身为独一无二的 **{species}**，\n"
                        "品质晋升为【混沌】，傲视群宠，闪耀全服！\n\n"
                        "💎 这是实力与热爱的象征，让我们共同祝贺这位大师登上宠物乐园的巅峰！\n"
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
            new_filename = f"{secrets.token_hex(8)}{ext}"
            path = self.store.custom_image_path(new_filename)
            path.write_bytes(file_data)
            changes["image"] = new_filename
        review, err = self.store.create_custom_review(sess["aid"], group_id, qq, changes)
        if err:
            return web.json_response({"ok": False, "msg": err})
        await self.store.save()
        return web.json_response({
            "ok": True,
            "msg": "已提交审核，预计 3 个工作日内处理完毕",
            "review": review,
        })

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
            return web.json_response({"ok": True, "reply": f"宠物乐园处理出错：{e}", "image_md": None})
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
                text = gw._evolve(player)
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
        success = "兑换成功" in str(text)
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
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>宠物乐园 · 玩家中心</title>
<link rel="stylesheet" href="/webstatic/element-plus.min.css">
<style>
  :root{
    --bg:#f4f6fb; --card:#fff; --line:#e6e9f2; --text:#1f2534; --muted:#8a93a8;
    --brand:#6366f1; --brand2:#a855f7; --grad:linear-gradient(135deg,#6366f1,#a855f7);
  }
  *{margin:0;padding:0;box-sizing:border-box}
  body{
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
    background:var(--bg); color:var(--text); min-height:100vh;
  }
  [v-cloak]{display:none}
  #app{min-height:100vh}
  .layout{display:flex;min-height:100vh}
  .sidebar{width:264px;background:#fff;border-right:1px solid var(--line);display:flex;flex-direction:column;padding:22px 16px 18px;position:fixed;left:0;top:0;height:100vh;overflow-y:auto;z-index:20}
  .sidebar::-webkit-scrollbar{width:6px}
  .sidebar::-webkit-scrollbar-thumb{background:#d6dae6;border-radius:3px}
  .side-brand{font-size:16px;font-weight:800;padding:2px 8px 16px;border-bottom:1px solid var(--line);margin-bottom:14px;display:flex;align-items:center;gap:9px}
  .side-brand::before{content:'';width:10px;height:10px;border-radius:3px;background:var(--grad);flex:0 0 auto}
  .side-sec{font-size:11.5px;color:var(--muted);font-weight:700;letter-spacing:1.2px;margin:4px 8px 9px}
  .side-pets{display:flex;flex-direction:column;gap:8px}
  .pet-chip{display:flex;align-items:center;gap:10px;padding:9px 10px;border:1px solid var(--line);border-radius:14px;cursor:pointer;transition:.16s;background:#fff}
  .pet-chip:hover{border-color:#c4c9ff;box-shadow:0 3px 12px rgba(99,102,241,.12)}
  .pet-chip.active{border-color:transparent;background:linear-gradient(135deg,#eef0ff,#f6efff);box-shadow:inset 0 0 0 1.5px #8a8ef5}
  .pet-chip img{width:40px;height:40px;border-radius:11px;object-fit:cover;background:#eef1f8;flex:0 0 auto}
  .pet-chip .name{font-size:13.5px;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .pet-chip .sub{font-size:11.5px;color:var(--muted);margin-top:2px}
  .pet-chip .info{min-width:0}
  .side-btns{display:flex;flex-direction:column;gap:9px;margin-top:12px}
  .side-btns .el-button{width:100%;margin:0}
  .side-tip{color:var(--muted);margin:9px 4px 0;font-size:12px;line-height:1.6}
  .side-foot{margin-top:auto;padding-top:14px;border-top:1px solid var(--line);display:flex;flex-direction:column;gap:9px}
  .side-user{font-size:12.5px;color:var(--muted);font-weight:600;padding:0 2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .side-foot-btns{display:flex;gap:8px}
  .side-foot-btns .el-button{flex:1;margin:0}
  .content{flex:1;min-width:0;padding:26px 34px 40px;margin-left:264px}
  .content-inner{max-width:960px;margin:0 auto}
  .page-title{font-size:21px;font-weight:800;margin-bottom:18px}

  .card{background:#fff;border:1px solid var(--line);border-radius:18px;padding:22px;box-shadow:0 2px 10px rgba(30,40,80,.04)}
  .sec-title{font-size:16px;font-weight:800;margin:26px 0 12px;display:flex;align-items:center;gap:9px}
  .sec-title::before{content:'';width:4px;height:16px;border-radius:2px;background:var(--grad)}

  .pet-hero{display:flex;gap:22px;align-items:flex-start;flex-wrap:wrap}
  .pet-img{width:148px;height:148px;border-radius:20px;object-fit:cover;background:#eef1f8;border:1px solid var(--line);flex:0 0 auto}
  .pet-head{flex:1;min-width:220px}
  .pet-name{font-size:22px;font-weight:800;display:flex;align-items:baseline;gap:9px;flex-wrap:wrap}
  .pet-name .lv{font-size:14px;color:var(--muted);font-weight:700}
  .pet-meta{color:var(--muted);font-size:13.5px;margin-top:5px}
  .pet-tags{display:flex;gap:6px;flex-wrap:wrap;margin-top:9px}
  .pet-bars{margin-top:13px;display:flex;flex-direction:column;gap:8px;max-width:420px}
  .bar-row{display:flex;align-items:center;gap:10px;font-size:12.5px}
  .bar-row .bl{width:70px;color:var(--muted);font-weight:600;flex:0 0 auto}
  .bar-row .el-progress{flex:1}
  .bar-row .bv{width:120px;text-align:right;color:var(--muted);font-variant-numeric:tabular-nums;flex:0 0 auto}
  .pet-badges{display:flex;gap:8px;flex-wrap:wrap;margin-top:13px}

  .stat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:12px;margin-top:16px}
  .stat{background:#f8f9fd;border:1px solid var(--line);border-radius:14px;padding:13px 15px}
  .stat .label{font-size:12px;color:var(--muted);font-weight:600}
  .stat .value{font-size:17px;font-weight:800;margin-top:4px;font-variant-numeric:tabular-nums}

  .wallet{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px}
  .coin{background:#fff;border:1px solid var(--line);border-radius:16px;padding:16px 18px;transition:.16s}
  .coin:hover{transform:translateY(-2px);box-shadow:0 8px 22px rgba(30,40,80,.08)}
  .coin .label{font-size:12.5px;color:var(--muted);font-weight:600}
  .coin .value{font-size:21px;font-weight:800;margin-top:5px;background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent;font-variant-numeric:tabular-nums}

  .grow-row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  .grow-group{display:flex;gap:8px;align-items:center}
  .muted{color:var(--muted);font-size:12.5px;line-height:1.7}

  .cd-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
  .cd{border:1px solid var(--line);border-radius:13px;padding:11px 13px;background:#fff}
  .cd .cd-name{font-size:13px;font-weight:700}
  .cd .cd-time{font-size:12.5px;margin-top:4px;font-weight:700;font-variant-numeric:tabular-nums}
  .cd.ready .cd-time{color:#16a34a}
  .cd.busy .cd-time{color:#d97706}
  .cd.ready{border-color:#bbf0cd;background:#f4fdf7}

  .bag{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px}
  .item{border:1px solid var(--line);border-radius:14px;padding:14px 15px;background:#fff;position:relative;transition:.16s}
  .item:hover{box-shadow:0 6px 18px rgba(30,40,80,.08)}
  .item-name{font-size:14px;font-weight:700;padding-right:44px}
  .item .count{font-size:12px;color:var(--muted);margin-top:3px;display:block}
  .info-link{color:var(--brand);cursor:pointer;font-weight:600}
  .info-link:hover{text-decoration:underline}
  .item .use-row{display:flex;gap:8px;margin-top:11px;align-items:center}
  .item .use-row .el-input-number{width:100px;flex:0 0 auto}
  .item .use-row .el-button{flex:1;margin:0}
  .item .item-tag{position:absolute;top:12px;right:12px}

  .empty-tip{color:var(--muted);font-size:13.5px;padding:22px 0;text-align:center}

  .redeem-row{display:flex;gap:10px}
  .redeem-row .el-input{flex:1}
  .redeem-result{margin-top:12px;padding:12px 14px;border-radius:12px;background:#f6f8ff;border:1px solid #dfe4ff;font-size:13px;white-space:pre-wrap;line-height:1.7}

  .custom-box{margin-top:16px;padding:18px;background:linear-gradient(135deg,#f4f0ff,#eef5ff);border-radius:16px;border:1px solid #e2e0f7}
  .custom-badge{color:var(--brand2);font-size:14px;font-weight:700;margin-bottom:6px}
  .custom-remaining{font-size:12px;color:var(--muted);margin-bottom:12px}

  .pet-source{margin-top:18px;text-align:center;color:#b0b8ca;font-size:12px;word-break:break-all}

  .upload-zone{border:1.5px dashed #c9cede;border-radius:14px;padding:18px;text-align:center;cursor:pointer;transition:.16s;background:#fafbfe}
  .upload-zone:hover{border-color:var(--brand);background:#f5f6ff}
  .upload-plus{font-size:26px;color:#aab1c5;line-height:1}
  .upload-text{font-size:13px;font-weight:700;margin-top:5px}
  .upload-hint{font-size:12px;color:var(--muted);margin-top:3px}
  .fld{display:block;font-size:12.5px;font-weight:700;color:#3c455c;margin:12px 0 7px}

  .crop-wrap{display:flex;justify-content:center;margin:10px 0}
  #cropCanvas{max-width:100%;height:auto;border-radius:12px;border:1px solid var(--line);cursor:grab;touch-action:none}
  .crop-zoom{display:flex;align-items:center;gap:12px;margin:12px 0 4px}
  .crop-zoom .el-slider{flex:1}
  .crop-preview{margin:10px 0;text-align:center}
  .crop-preview img{max-width:150px;max-height:150px;border-radius:14px;border:1px solid var(--line)}

  .result-pre{white-space:pre-wrap;line-height:1.8;font-size:14px}

  @media(max-width:760px){
    .layout{flex-direction:column}
    .sidebar{width:100%;position:static;height:auto;border-right:none;border-bottom:1px solid var(--line);padding:14px 14px 12px}
    .side-brand{padding-bottom:10px;margin-bottom:10px}
    .side-pets{flex-direction:row;overflow-x:auto;padding-bottom:4px;-webkit-overflow-scrolling:touch}
    .pet-chip{flex:0 0 auto;min-width:150px}
    .side-btns{flex-direction:row}
    .side-btns .el-button{width:auto;flex:1}
    .side-tip{display:none}
    .side-foot{margin-top:12px;padding-top:10px;flex-direction:row;align-items:center;justify-content:space-between}
    .side-foot-btns{flex:0 0 auto}
    .content{padding:18px 12px 32px;margin-left:0}
    .page-title{font-size:18px;margin-bottom:14px}
    .card{padding:16px;border-radius:15px}
    .pet-hero{flex-direction:column;align-items:center;text-align:center}
    .pet-name,.pet-tags,.pet-badges{justify-content:center}
    .pet-img{width:120px;height:120px}
    .pet-bars{max-width:none;width:100%}
    .bar-row .bl{width:56px}
    .bar-row .bv{width:96px;font-size:11.5px}
    .stat-grid{grid-template-columns:repeat(2,1fr)}
    .wallet{grid-template-columns:repeat(2,1fr)}
    .cd-grid{grid-template-columns:repeat(2,1fr)}
    .bag{grid-template-columns:1fr}
    .grow-row{gap:8px}
    .redeem-row{flex-direction:column}
    .el-dialog{--el-dialog-width:calc(100vw - 28px) !important;width:calc(100vw - 28px) !important;max-width:calc(100vw - 28px)}
    .el-message-box{max-width:calc(100vw - 28px)}
    .el-message{max-width:calc(100vw - 24px)}
  }
</style>
</head>
<body>
<div id="app" v-cloak>
<div class="layout">
  <aside class="sidebar">
    <div class="side-brand">宠物乐园 · 玩家中心</div>
    <div class="side-sec">我的宠物</div>
    <div class="side-pets">
      <div v-for="(p,i) in pets" :key="p.group_id + ':' + p.qq" class="pet-chip"
           :class="{active: current && current.group_id===p.group_id && current.qq===p.qq}" @click="loadPet(p)">
        <img :src="p.image_url || blankImg" alt="">
        <div class="info">
          <div class="name">{{ p.nickname }}</div>
          <div class="sub">Lv{{ p.level }} · {{ p.quality }}</div>
        </div>
      </div>
      <span v-if="!pets.length" class="muted" style="padding:0 8px">暂无绑定宠物</span>
    </div>
    <div class="side-btns">
      <el-button type="primary" plain round @click="openBind()">＋ 绑定新宠物</el-button>
      <el-button type="success" round @click="goChat">💬 宠物对话</el-button>
      <el-button type="warning" round @click="goFeedback">📣 问题反馈</el-button>
    </div>
    <p class="side-tip">绑定后可在不同群号 / 用户ID 之间切换查看宠物。</p>
    <div class="side-foot">
      <div class="side-user" v-if="account">QQ {{ account.qq }}</div>
      <div class="side-foot-btns">
        <el-button size="small" round @click="openPwd">修改密码</el-button>
        <el-button size="small" round @click="logout">退出登录</el-button>
      </div>
    </div>
  </aside>

  <section class="content">
    <div class="content-inner">
      <div class="page-title">宠物档案</div>

      <div v-if="!current" class="card empty-tip">请先在左侧绑定并选择宠物</div>
      <div v-else-if="petLoading" class="card" v-loading="petLoading" style="min-height:220px"></div>
      <template v-else-if="data">
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
                  <el-progress :percentage="pct(pet.ascended ? pet.xianyuan : pet.exp, pet.exp_to_next)" :stroke-width="10" :show-text="false" color="#8b5cf6"></el-progress>
                  <span class="bv">{{ fmt(pet.ascended ? (pet.xianyuan||0) : (pet.exp||0)) }} / {{ fmt(pet.exp_to_next||0) }}</span>
                </div>
                <div class="bar-row">
                  <span class="bl">❤️ 生命</span>
                  <el-progress :percentage="pct(pet.hp, pet.hp_max)" :stroke-width="10" :show-text="false" color="#f43f5e"></el-progress>
                  <span class="bv">{{ fmt(pet.hp||0) }} / {{ fmt(pet.hp_max||0) }}</span>
                </div>
                <div class="bar-row">
                  <span class="bl">⚡ 精力</span>
                  <el-progress :percentage="pct(pet.energy, pet.energy_max)" :stroke-width="10" :show-text="false" color="#f59e0b"></el-progress>
                  <span class="bv">{{ fmt(pet.energy||0) }} / {{ fmt(pet.energy_max||0) }}</span>
                </div>
              </div>
              <div class="pet-badges">
                <el-tag type="danger" effect="plain" round>⚔️ 战力 {{ fmt(pet.battle_power) }}</el-tag>
                <el-tag type="warning" effect="plain" round>😊 心情 {{ fmt(pet.mood||0) }}</el-tag>
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
            <div class="custom-badge">✨ 定制权限已解锁（混沌品质）</div>
            <div class="custom-remaining">本月剩余次数：图片 {{ data.custom_remaining.image }} 次 / 名称 {{ data.custom_remaining.species_name }} 次</div>
            <div style="display:flex;align-items:center;gap:12px;margin:12px 0;flex-wrap:wrap">
              <el-button type="primary" round @click="openCustomEdit">修改形象 / 名称</el-button>
              <div style="display:flex;align-items:center;gap:8px;background:#fff;padding:8px 14px;border-radius:999px;border:1px solid #e2e0f7">
                <span style="font-size:13px;color:#5b657d">自动修炼</span>
                <el-switch
                  v-model="data.auto_cultivation.enabled"
                  :loading="autoCultivating"
                  inline-prompt
                  active-text="开"
                  inactive-text="关"
                  @change="toggleAutoCultivation"
                />
              </div>
            </div>
            <div v-if="data.auto_cultivation.enabled" class="custom-remaining" style="color:#0c7a45">
              🧘 自动修炼运行中 · 累计 {{ data.auto_cultivation.total_sessions || 0 }} 次 · 经验 +{{ data.auto_cultivation.total_exp || 0 }}
            </div>
            <el-alert v-for="(r,i) in data.custom_pending || []" :key="'p'+i" type="success" :closable="false" style="margin-top:10px"
              :title="'已提交审核，预计 3 个工作日内完成。' + (r.new.species_name ? '名称：'+r.new.species_name+' ' : '') + (r.new.image ? '图片' : '')"></el-alert>
            <el-alert v-for="(r,i) in data.custom_rejected || []" :key="'r'+i" type="error" :closable="false" style="margin-top:10px"
              :title="'审核未通过：' + (r.reason || '未说明原因')"></el-alert>
          </div>
        </div>
        <div v-else class="card empty-tip">该账号下暂无宠物</div>

        <div class="sec-title">我的财产</div>
        <div class="wallet">
          <div class="coin"><div class="label">🪙 金币</div><div class="value">{{ fmt(data.coin) }}</div></div>
          <div class="coin"><div class="label">✨ 积分</div><div class="value">{{ fmt(data.jifen) }}</div></div>
          <div class="coin"><div class="label">💎 钻石</div><div class="value">{{ fmt(data.diamond) }}</div></div>
          <div class="coin"><div class="label">🔮 深渊结晶</div><div class="value">{{ fmt(data.abyss && data.abyss.crystal || 0) }}</div></div>
        </div>

        <div class="sec-title">宠物养成</div>
        <div class="card">
          <div class="grow-row">
            <el-button type="primary" round :loading="acting==='auto_level'" @click="petAction('auto_level')">⚡ 一键升级</el-button>
            <span class="grow-group">
              <el-input-number v-model="levelTimes" :min="1" :max="9999" size="default"></el-input-number>
              <el-button round :loading="acting==='level'" @click="petAction('level')">⬆ 升级</el-button>
            </span>
            <el-button type="warning" round :loading="acting==='evolve'" @click="petAction('evolve')">🌟 宠物进化</el-button>
          </div>
          <p class="muted" style="margin-top:9px">效果与群聊指令一致；升级消耗经验与精力，进化需『进化神石』。</p>
        </div>

        <div class="sec-title">活动冷却</div>
        <div class="cd-grid" v-if="cooldowns.length">
          <div v-for="c in cooldowns" :key="c.name" class="cd" :class="cdRemaining(c) > 0 ? 'busy' : 'ready'">
            <div class="cd-name">{{ c.name }}</div>
            <div class="cd-time">{{ cdRemaining(c) > 0 ? fmtCd(cdRemaining(c)) : '可用' }}</div>
          </div>
        </div>
        <div v-else class="card empty-tip">暂无活动</div>

        <div class="sec-title">背包</div>
        <p class="muted" style="margin:-4px 0 10px">道具可直接使用（支持数量），神器可佩戴、秘技书可参悟，效果与群聊指令一致。</p>
        <div class="bag" v-if="bagItems.length">
          <div v-for="it in bagItems" :key="it.name" class="item">
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
        <div v-else class="card empty-tip">背包空空如也</div>

        <div class="sec-title">卡密兑换</div>
        <div class="card">
          <div class="redeem-row">
            <el-input v-model="redeemCode" placeholder="输入卡密，可兑换金币 / 积分 / 钻石 / 道具" clearable @keyup.enter="redeem"></el-input>
            <el-button type="primary" round :loading="redeeming" @click="redeem">兑换</el-button>
          </div>
          <div v-if="redeemResult" class="redeem-result">{{ redeemResult }}</div>
        </div>

        <div class="pet-source">群号：{{ data.group_id }} ｜ 用户ID：{{ data.qq }}</div>
      </template>
    </div>
  </section>
</div>

<!-- 绑定新宠物 -->
<el-dialog v-model="bind.show" title="＋ 绑定新宠物" width="460px" align-center>
  <div v-if="auto.loading" class="muted" style="padding:4px 2px 8px">🔍 正在自动识别（按登录 QQ {{ auto.qq || '...' }}）…</div>
  <div v-else-if="auto.list && auto.list.length" style="margin-bottom:10px">
    <div style="color:#8f97ab;font-size:12px;margin:0 0 6px">✅ 已按登录 QQ（{{ auto.qq }}）自动列出名下宠物及绑定情况：未绑定点「选择」、被其它账号绑定的可「强要回」。</div>
    <div style="max-height:230px;overflow:auto;border:1px solid rgba(255,255,255,.08);border-radius:8px">
      <div v-for="grp in auto.list" :key="grp.group_id" style="padding:8px;border-bottom:1px solid rgba(255,255,255,.06)">
        <div style="font-size:12px;color:#cfd6e4;margin-bottom:2px"><b>群 ID</b> <code>{{ grp.group_id }}</code></div>
        <div v-for="pl in grp.players" :key="grp.group_id + '|' + pl.qq" style="padding:6px 0 6px 10px">
          <div style="font-size:12px;color:#aeb6c9">用户ID <code>{{ pl.qq }}</code>　共 {{ pl.pet_count }} 只
            <span v-if="pl.bound==='other'" style="color:#f56c6c">　⚠️ 已被其它账号绑定<span v-if="pl.bound_qq">（{{ pl.bound_qq }}）</span>，可强要回</span>
            <span v-else-if="pl.bound==='me'" style="color:#67c23a">　✅ 已绑定到本账号</span>
          </div>
          <div v-for="pt in pl.pets" :key="pt.index" style="display:flex;align-items:center;gap:8px;margin:3px 0 3px 8px">
            <span style="flex:1;font-size:12px;color:#e6e9f0">{{ pt.nickname }}　{{ pt.species }}　{{ pt.quality }}　Lv{{ pt.level }}　{{ pt.stage }}</span>
            <el-button v-if="pl.bound==='other'" size="small" round plain type="danger" @click="reclaimBind(grp.group_id, pl.qq, pt.index)">强要回</el-button>
            <el-button v-else-if="pl.bound==='me'" size="small" round plain disabled>已绑定</el-button>
            <el-button v-else size="small" round plain type="primary" @click="pickAuto(grp.group_id, pl.qq, pt.index)">选择</el-button>
          </div>
        </div>
      </div>
    </div>
  </div>
  <div v-else-if="!auto.loading" class="muted" style="padding:4px 2px 8px">未找到可通过登录 QQ（{{ auto.qq || '未绑定QQ' }}）自动匹配的宠物，请在下方手动输入群号与用户 ID。</div>
  <el-form label-position="top" @submit.prevent="doBind">
    <el-form-item label="群号">
      <el-input v-model="bind.group" placeholder="宠物所在的 QQ 群号" clearable></el-input>
    </el-form-item>
    <el-form-item label="绑定用户ID">
      <el-input v-model="bind.qq" placeholder="你在该群使用宠物乐园的用户 ID" clearable @keyup.enter="doBind"></el-input>
    </el-form-item>
  </el-form>
  <p class="muted">输入群号和用户 ID 后先查询宠物列表，再选择要绑定的宠物。</p>
  <el-form v-if="bind.pets && bind.pets.length && !bind.querying" label-position="top">
    <el-form-item label="选择要绑定的宠物">
      <el-select v-model="bind.petIndex" style="width:100%">
        <el-option v-for="pt in bind.pets" :key="pt.index" :label="pt.nickname + '  ' + pt.species + '  ' + pt.quality + ' Lv' + pt.level + '  ' + pt.stage" :value="pt.index"></el-option>
      </el-select>
    </el-form-item>
  </el-form>
  <template #footer>
    <el-button round @click="bind.show=false">取消</el-button>
    <el-button v-if="bind.pets && bind.pets.length" type="primary" round :loading="bind.loading" @click="doBind">绑定</el-button>
    <el-button v-else type="primary" round :loading="bind.querying" @click="doBindQuery">查询宠物</el-button>
  </template>
</el-dialog>

<!-- 修改密码 -->
<el-dialog v-model="pwd.show" title="🔒 修改密码" width="420px" align-center>
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
<el-dialog v-model="custom.editShow" title="✨ 修改宠物形象" width="480px" align-center>
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
const { createApp, reactive, ref, computed, onMounted, nextTick } = Vue;
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
    const current = ref(null);
    const data = ref(null);
    const pet = computed(()=> data.value ? data.value.pet : null);
    const petLoading = ref(false);
    const now = ref(Math.floor(Date.now()/1000));
    setInterval(()=>{ now.value = Math.floor(Date.now()/1000); }, 1000);

    const levelTimes = ref(1);
    const acting = ref('');
    const usingItem = ref('');
    const redeemCode = ref('');
    const redeeming = ref(false);
    const redeemResult = ref('');
    const bagItems = ref([]);
    const cooldowns = ref([]);
    const autoCultivating = ref(false);

    const bind = reactive({show:false, group:'', qq:'', loading:false, querying:false, pets:null, petIndex:0});
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
      const me = await api('/api/portal/me');
      if(!me || !me.ok){ location.href = '/'; return; }
      account.value = me.account;
      pets.value = me.bound_pets || [];
      if(location.hash === '#feedback'){
        location.href = '/feedback';
        return;
      }
      if(pets.value.length) await loadPet(pets.value[0]);
    }

    async function loadPet(p){
      current.value = p;
      petLoading.value = true;
      try{
        const d = await api(`/api/portal/pet?group_id=${encodeURIComponent(p.group_id)}&qq=${encodeURIComponent(p.qq)}&pet_index=${p.pet_index||0}`);
        if(!d || !d.ok){ ElMessage.error((d && d.msg) || '宠物数据加载失败'); data.value = null; return; }
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
      } finally { petLoading.value = false; }
    }

    async function refreshAll(){
      const me = await api('/api/portal/me');
      if(me && me.ok){ account.value = me.account; pets.value = me.bound_pets || []; }
      if(current.value) await loadPet(current.value);
    }

    async function toggleAutoCultivation(enabled){
      if(!data.value) return;
      autoCultivating.value = true;
      try{
        const p = current.value;
        const r = await api('/api/portal/auto_cultivation','POST',{
          group_id: p.group_id,
          qq: p.qq,
          pet_index: p.pet_index || 0,
          enabled: Boolean(enabled),
        });
        if(r && r.ok){
          ElMessage.success(r.msg || '设置成功');
          if(data.value) data.value.auto_cultivation = r.auto_cultivation || data.value.auto_cultivation;
        } else {
          ElMessage.error((r && r.msg) || '设置失败');
          // 回滚开关状态：重新加载宠物数据
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
      bind.show = true; bind.pets = null; bind.petIndex = 0;
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
      bind.group = g; bind.qq = q; bind.pets = null; bind.petIndex = idx || 0;
      await doBindQuery();
    }
    async function reclaimBind(g, q, idx){
      if(!g || !q){ return; }
      try{ await ElMessageBox.confirm('确定要强行要回该宠物的绑定权吗？这会把该宠物的网页绑定权改到你的账号。','强要确认',{type:'warning'}); }
      catch(e){ return; }
      const r = await api('/api/portal/bind/reclaim','POST',{group_id:g, qq:q, pet_index:idx});
      if(r && r.ok){ ElMessage.success(r.msg || '已强行要回绑定权'); await init(); await doBindAuto(); }
      else { ElMessage.error((r && r.msg) || '强要失败'); }
    }
    async function doBindQuery(){
      const g = bind.group.trim(), q = bind.qq.trim();
      if(!g || !q){ ElMessage.warning('群号和用户 ID 不能为空'); return; }
      bind.querying = true; bind.pets = null; bind.petIndex = 0;
      try{
        const r = await api('/api/portal/bind/query','POST',{group_id:g, qq:q});
        if(r && r.ok){
          bind.pets = r.pets || [];
          if(bind.pets.length) bind.petIndex = bind.pets[0].index;
          else ElMessage.warning('该玩家暂无宠物');
        } else { ElMessage.error((r && r.msg) || '查询失败'); }
      } finally { bind.querying = false; }
    }
    async function doBind(){
      const g = bind.group.trim(), q = bind.qq.trim();
      if(!g || !q){ ElMessage.warning('群号和用户 ID 不能为空'); return; }
      bind.loading = true;
      try{
        const r = await api('/api/portal/bind','POST',{group_id:g, qq:q, pet_index:bind.petIndex});
        if(r && r.ok){ ElMessage.success(r.msg || '绑定成功'); bind.show=false; bind.group=''; bind.qq=''; bind.pets=null; bind.petIndex=0; await init(); }
        else { ElMessage.error((r && r.msg) || '绑定失败'); }
      } finally { bind.loading = false; }
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
      levelTimes, acting, usingItem, redeemCode, redeeming, redeemResult, bagItems, cooldowns, autoCultivating,
      bind, pwd, custom, crop,
      auto,
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
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>问题反馈 · 宠物乐园</title>
<link rel="stylesheet" href="/webstatic/element-plus.min.css">
<style>
  :root{
    --bg:#f4f6fb; --card:#fff; --line:#e6e9f2; --text:#1f2534; --muted:#8a93a8;
    --brand:#6366f1; --brand2:#a855f7; --grad:linear-gradient(135deg,#6366f1,#a855f7);
  }
  *{margin:0;padding:0;box-sizing:border-box}
  body{
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
    background:var(--bg); color:var(--text); min-height:100vh;
  }
  [v-cloak]{display:none}
  .topbar{position:sticky;top:0;z-index:30;background:rgba(255,255,255,.92);backdrop-filter:blur(8px);border-bottom:1px solid var(--line)}
  .topbar-inner{max-width:1200px;margin:0 auto;display:flex;align-items:center;gap:14px;padding:14px 20px}
  .back-link{display:inline-flex;align-items:center;gap:6px;color:var(--muted);font-size:13.5px;font-weight:600;cursor:pointer;text-decoration:none;transition:.16s;padding:6px 10px;border-radius:10px}
  .back-link:hover{color:var(--brand);background:#f2f3ff}
  .topbar-title{font-size:16px;font-weight:800}
  .wrap{max-width:1200px;margin:0 auto;padding:26px 20px 60px;display:grid;grid-template-columns:minmax(340px,5fr) minmax(380px,6fr);gap:22px;align-items:start}
  .card{background:#fff;border:1px solid var(--line);border-radius:18px;padding:24px;box-shadow:0 2px 10px rgba(30,40,80,.04)}
  .card-title{font-size:16px;font-weight:800;margin-bottom:6px}
  .card-desc{color:var(--muted);font-size:13px;line-height:1.7;margin-bottom:14px}
  .muted{color:var(--muted);font-size:12.5px;line-height:1.7}
  .fld{display:block;font-size:12.5px;font-weight:700;color:#3c455c;margin:12px 0 7px}
  .upload-zone{border:1.5px dashed #c9cede;border-radius:14px;padding:18px;text-align:center;cursor:pointer;transition:.16s;background:#fafbfe}
  .upload-zone:hover{border-color:var(--brand);background:#f5f6ff}
  .upload-plus{font-size:26px;color:#aab1c5;line-height:1}
  .upload-text{font-size:13px;font-weight:700;margin-top:5px}
  .upload-hint{font-size:12px;color:var(--muted);margin-top:3px}
  .fb-img-preview{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
  .fb-thumb{position:relative}
  .fb-thumb img{width:72px;height:72px;object-fit:cover;border-radius:10px;border:1px solid var(--line)}
  .fb-thumb .rm{position:absolute;top:-6px;right:-6px;width:20px;height:20px;border:none;border-radius:50%;background:#ef4444;color:#fff;font-size:12px;line-height:20px;cursor:pointer;padding:0}
  .submit-row{display:flex;justify-content:flex-end;margin-top:18px}
  .list-head{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:4px}
  .list-count{color:var(--muted);font-size:12.5px;font-weight:600}
  .fb-rows{display:flex;flex-direction:column;margin-top:10px}
  .fb-row{display:flex;align-items:center;gap:12px;padding:14px 4px;border-bottom:1px solid var(--line)}
  .fb-row:last-child{border-bottom:none}
  .fb-row-main{flex:1;min-width:0}
  .fb-row-top{display:flex;align-items:center;gap:8px;margin-bottom:5px}
  .fb-row-date{color:var(--muted);font-size:12px;font-variant-numeric:tabular-nums}
  .fb-row-text{font-size:13.5px;line-height:1.5;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#3c455c}
  .fb-row-btns{display:flex;gap:8px;flex:0 0 auto}
  .fb-row-btns .el-button{margin:0}
  .empty-tip{color:var(--muted);font-size:13.5px;padding:30px 0;text-align:center}
  .pager{display:flex;justify-content:center;margin-top:16px}
  .dt-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:12px}
  .dt-date{margin-left:auto;color:var(--muted);font-size:12.5px}
  .dt-body{white-space:pre-wrap;line-height:1.7;font-size:13.5px;background:#f8f9fd;border:1px solid var(--line);border-radius:12px;padding:13px 15px;max-height:300px;overflow-y:auto;word-break:break-word}
  .dt-meta{color:var(--muted);font-size:12.5px;margin-top:10px;line-height:1.8}
  .dt-imgs{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
  .dt-reply{margin-top:12px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:12px;padding:11px 14px;color:#166534;white-space:pre-wrap;line-height:1.7;font-size:13.5px}
  .dt-reply .rt{font-weight:800;margin-bottom:4px;font-size:12.5px}
  @media(max-width:900px){
    .wrap{grid-template-columns:1fr}
  }
  @media(max-width:640px){
    .wrap{padding:18px 12px 44px;gap:16px}
    .card{padding:17px;border-radius:15px}
    .fb-row{flex-direction:column;align-items:stretch;gap:9px}
    .fb-row-btns{justify-content:flex-end}
    .el-dialog{--el-dialog-width:calc(100vw - 28px) !important;width:calc(100vw - 28px) !important;max-width:calc(100vw - 28px)}
    .el-message-box{max-width:calc(100vw - 28px)}
    .el-message{max-width:calc(100vw - 24px)}
  }
</style>
</head>
<body>
<div id="app" v-cloak>
  <header class="topbar">
    <div class="topbar-inner">
      <a class="back-link" href="/portal">← 返回玩家中心</a>
      <div class="topbar-title">问题反馈</div>
    </div>
  </header>

  <main class="wrap">
    <div class="card">
      <div class="card-title">提交反馈</div>
      <p class="card-desc">遇到 Bug 或有好的想法都可以在这里告诉我们，管理员处理后可在下方记录中查看回复。</p>
      <el-tabs v-model="form.kind">
        <el-tab-pane label="🐞 反馈 Bug" name="bug"></el-tab-pane>
        <el-tab-pane label="💡 提出建议" name="suggestion"></el-tab-pane>
      </el-tabs>
      <label class="fld" style="margin-top:2px">{{ form.kind==='bug' ? '问题描述' : '建议内容' }}</label>
      <el-input v-model="form.content" type="textarea" :rows="4" maxlength="2000" show-word-limit
        :placeholder="form.kind==='bug' ? '请详细描述遇到的问题：操作了什么、预期结果、实际结果…' : '说说你希望增加或改进的功能…'"></el-input>
      <template v-if="form.kind==='bug'">
        <label class="fld">发生时间</label>
        <el-date-picker v-model="form.time" type="datetime" placeholder="选择问题发生的时间" style="width:100%" format="YYYY-MM-DD HH:mm" value-format="YYYY-MM-DD HH:mm"></el-date-picker>
        <div style="display:flex;gap:8px;margin-top:12px">
          <el-input v-model="form.group" placeholder="对应的 QQ 群号"></el-input>
          <el-input v-model="form.user" placeholder="对应的用户 ID"></el-input>
        </div>
      </template>
      <label class="fld">图片截图（可选，最多 3 张）</label>
      <input ref="fileInput" type="file" accept="image/*" multiple style="display:none" @change="pickImages">
      <div class="upload-zone" @click="$refs.fileInput.click()">
        <div class="upload-plus">＋</div>
        <div class="upload-text">点击选择图片</div>
        <div class="upload-hint">支持 jpg / png / gif / webp，单张不超过 5MB</div>
      </div>
      <div class="fb-img-preview" v-if="form.files.length">
        <div v-for="(f,i) in form.files" :key="i" class="fb-thumb">
          <img :src="f.url"><button class="rm" @click="form.files.splice(i,1)">×</button>
        </div>
      </div>
      <div class="submit-row">
        <el-button type="primary" round :loading="form.submitting" @click="submitFeedback">提交反馈</el-button>
      </div>
    </div>

    <div class="card">
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
        <div class="rt">💬 管理员回复{{ detail.item.replied_at ? '（' + fmtDate(detail.item.replied_at) + '）' : '' }}</div>{{ detail.item.reply }}
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
        data.sort((a,b)=>(a.created_at||0)-(b.created_at||0));
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

    return {form, list, listLoading, page, pageSize, detail, deleting, pageList,
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
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>宠物对话 · 宠物乐园</title>
<link rel="stylesheet" href="/webstatic/element-plus.min.css">
<style>
  :root{
    --bg:#eef1f7; --line:#e2e6f0; --text:#1f2534; --muted:#8a93a8;
    --brand:#6366f1; --bubble-me:#4f9bff; --bubble-bot:#ffffff;
  }
  *{margin:0;padding:0;box-sizing:border-box}
  html,body{height:100%}
  body{
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
    background:var(--bg); color:var(--text);
  }
  [v-cloak]{display:none}
  .frame{width:100%;height:100vh;height:100dvh;display:flex;flex-direction:column;background:#f3f5fa}
  .topbar{flex:0 0 auto;background:rgba(255,255,255,.95);backdrop-filter:blur(8px);border-bottom:1px solid var(--line)}
  .topbar-inner{display:flex;align-items:center;gap:12px;padding:11px 16px}
  .back-link{display:inline-flex;align-items:center;gap:6px;color:var(--muted);font-size:13px;font-weight:600;cursor:pointer;text-decoration:none;transition:.16s;padding:6px 9px;border-radius:10px;white-space:nowrap}
  .back-link:hover{color:var(--brand);background:#f2f3ff}
  .top-title{min-width:0}
  .top-title .t{font-size:15px;font-weight:800;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .top-title .s{font-size:11.5px;color:var(--muted);margin-top:1px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .top-right{margin-left:auto;display:flex;align-items:center;gap:8px}
  .chat-body{flex:1 1 auto;overflow-y:auto;padding:18px 16px 8px;scroll-behavior:smooth}
  .day-tip{text-align:center;margin:6px 0 14px}
  .day-tip span{display:inline-block;background:#dde2ee;color:#7a8299;font-size:11.5px;border-radius:9px;padding:3px 10px}
  .msg{display:flex;gap:9px;margin-bottom:16px;align-items:flex-start}
  .msg.me{flex-direction:row-reverse}
  .avatar{flex:0 0 38px;width:38px;height:38px;border-radius:50%;overflow:hidden;background:#fff;border:1px solid var(--line);display:flex;align-items:center;justify-content:center;font-size:19px}
  .avatar img{width:100%;height:100%;object-fit:cover}
  .msg-col{max-width:74%;min-width:0;display:flex;flex-direction:column}
  .msg.me .msg-col{align-items:flex-end}
  .msg-name{font-size:11.5px;color:#98a0b4;margin:0 2px 4px}
  .bubble{position:relative;background:var(--bubble-bot);border-radius:4px 14px 14px 14px;padding:10px 13px;font-size:14px;line-height:1.65;word-break:break-word;box-shadow:0 1px 3px rgba(30,40,80,.07);overflow-x:auto}
  .msg.me .bubble{background:var(--bubble-me);color:#fff;border-radius:14px 4px 14px 14px}
  .bubble.pending{color:var(--muted)}
  .bubble b{font-weight:800}
  .bubble .h{display:block;font-weight:800;font-size:14.5px;margin:2px 0}
  .bubble img{max-width:100%;border-radius:10px;display:block;margin:4px 0}
  .bubble table{border-collapse:collapse;margin:6px 0;font-size:12.5px;width:max-content;max-width:100%}
  .bubble th,.bubble td{border:1px solid #e4e8f2;padding:4px 8px;text-align:center;white-space:nowrap}
  .bubble th{background:#f4f6fc;font-weight:800}
  .bubble tr:nth-child(even) td{background:#fafbfe}
  .msg.me .bubble table th,.msg.me .bubble td{border-color:rgba(255,255,255,.4)}
  .bubble .quote{display:block;border-left:3px solid #c9cede;background:#f6f8fc;color:#5b6478;border-radius:0 8px 8px 0;padding:5px 10px;margin:5px 0;font-size:13px}
  .bubble hr{border:none;border-top:1px solid #e4e8f2;margin:8px 0}
  .typing{display:inline-flex;gap:4px;align-items:center;padding:4px 2px}
  .typing i{width:6px;height:6px;border-radius:50%;background:#b6bdd0;animation:blink 1.2s infinite}
  .typing i:nth-child(2){animation-delay:.2s}.typing i:nth-child(3){animation-delay:.4s}
  @keyframes blink{0%,80%,100%{opacity:.25}40%{opacity:1}}
  .chips{flex:0 0 auto;display:flex;gap:8px;overflow-x:auto;padding:8px 16px;scrollbar-width:none}
  .chips::-webkit-scrollbar{display:none}
  .chip{flex:0 0 auto;background:#fff;border:1px solid var(--line);border-radius:999px;padding:6px 13px;font-size:12.5px;color:#3c455c;cursor:pointer;transition:.15s;user-select:none;white-space:nowrap}
  .chip:hover{border-color:var(--brand);color:var(--brand);background:#f5f6ff}
  .inputbar{flex:0 0 auto;display:flex;gap:10px;align-items:flex-end;padding:10px 16px 14px;background:rgba(255,255,255,.95);border-top:1px solid var(--line)}
  .inputbar textarea{flex:1;resize:none;border:1px solid var(--line);border-radius:14px;background:#fff;padding:10px 13px;font-size:14px;line-height:1.5;font-family:inherit;outline:none;max-height:110px;transition:border-color .15s}
  .inputbar textarea:focus{border-color:var(--brand)}
  .send-btn{flex:0 0 auto;border:none;border-radius:14px;background:var(--bubble-me);color:#fff;font-size:14px;font-weight:700;padding:10px 22px;cursor:pointer;transition:.15s}
  .send-btn:disabled{background:#c2cbde;cursor:not-allowed}
  .empty-wrap{display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;gap:12px;color:var(--muted)}
  .empty-wrap .e{font-size:42px}
  @media(max-width:640px){
    .msg-col{max-width:82%}
    .chat-body{padding:14px 10px 6px}
    .inputbar{padding:8px 10px 12px}
    .chips{padding:8px 10px}
  }
</style>
</head>
<body>
<div id="app" v-cloak>
  <div class="frame">
    <header class="topbar">
      <div class="topbar-inner">
        <a class="back-link" href="/portal">← 玩家中心</a>
        <div class="top-title" v-if="current">
          <div class="t">{{ current.nickname }} 的小窝</div>
          <div class="s">群 {{ current.group_id }} · ID {{ current.qq }}</div>
        </div>
        <div class="top-title" v-else><div class="t">宠物对话</div></div>
        <div class="top-right">
          <el-select v-if="pets.length > 1" v-model="petIdx" size="small" style="width:150px" @change="switchPet">
            <el-option v-for="(p,i) in pets" :key="i" :label="p.nickname + '（群' + p.group_id + '）'" :value="i"></el-option>
          </el-select>
          <el-button size="small" round @click="clearHistory" v-if="msgs.length">清空记录</el-button>
        </div>
      </div>
    </header>

    <template v-if="current">
      <main class="chat-body" ref="bodyEl">
        <div class="day-tip"><span>与『{{ current.nickname }}』的对话 · 模拟群 {{ current.group_id }}</span></div>
        <div v-for="(m,i) in msgs" :key="i" class="msg" :class="{me: m.role==='me'}">
          <div class="avatar">
            <img v-if="m.role==='me' && userAvatar" :src="userAvatar">
            <img v-else-if="m.role==='bot' && current.image_url" :src="current.image_url">
            <span v-else>{{ m.role==='me' ? '🙂' : '🐾' }}</span>
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
        <span class="chip" v-for="c in quickCmds" :key="c" @click="sendText(c)">{{ c }}</span>
      </div>

      <div class="inputbar">
        <textarea ref="inputEl" v-model="draft" rows="1" maxlength="200"
          placeholder="像在 QQ 群里一样发消息，例如：签到、我的宠物、帮我升级…"
          @keydown.enter.exact.prevent="sendText()"
          @input="autoGrow"></textarea>
        <button class="send-btn" :disabled="sending || !draft.trim()" @click="sendText()">发送</button>
      </div>
    </template>

    <div class="empty-wrap" v-else-if="loaded">
      <div class="e">🐣</div>
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
    const quickCmds = ['签到','我的宠物','宠物状态','查看背包','宠物升级','宠物菜单','宠物排行','自动修炼状态'];

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
      sendText, switchPet, clearHistory, autoGrow};
  }
}).use(ElementPlus, {locale: ElementPlusLocaleZhCn}).mount('#app');
</script>
</body>
</html>
"""


_HOME_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN" class="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>宠物乐园 · 全服数据中心</title>
<link rel="stylesheet" href="/webstatic/element-plus.min.css">
<link rel="stylesheet" href="/webstatic/element-plus-dark.css">
<style>
  :root{
    --bg:#0b1020; --card:rgba(255,255,255,.04); --line:rgba(255,255,255,.08);
    --text:#e8ecf8; --muted:#8b93b0; --brand:#6366f1; --brand2:#a855f7;
  }
  *{margin:0;padding:0;box-sizing:border-box}
  html.dark{--el-bg-color:#141a33;--el-bg-color-overlay:#141a33}
  body{
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
    background:var(--bg); color:var(--text); min-height:100vh; overflow-x:hidden;
  }
  [v-cloak]{display:none}
  #particles{position:fixed;inset:0;z-index:0;pointer-events:none}
  .glow{position:fixed;border-radius:50%;filter:blur(120px);opacity:.35;pointer-events:none;z-index:0;transition:transform .6s cubic-bezier(.22,1,.36,1)}
  .glow.a{width:560px;height:560px;background:#4338ca;top:-180px;left:-120px;animation:drift 18s ease-in-out infinite alternate}
  .glow.b{width:480px;height:480px;background:#7e22ce;top:22%;right:-160px;animation:drift 22s ease-in-out infinite alternate-reverse}
  .glow.c{width:420px;height:420px;background:#0e7490;bottom:-140px;left:32%;animation:drift 26s ease-in-out infinite alternate}
  @keyframes drift{from{transform:translate(0,0)}to{transform:translate(60px,40px)}}
  .grid-bg{position:fixed;inset:0;z-index:0;pointer-events:none;
    background-image:linear-gradient(rgba(255,255,255,.03) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.03) 1px,transparent 1px);
    background-size:44px 44px;
    mask-image:radial-gradient(ellipse 90% 60% at 50% 0%,#000 40%,transparent 100%);
  }
  .wrap{position:relative;z-index:1;max-width:1180px;margin:0 auto;padding:0 24px}

  nav{display:flex;align-items:center;justify-content:space-between;padding:22px 0}
  .brand{display:flex;align-items:center;gap:10px;font-weight:800;font-size:18px;letter-spacing:.5px}
  .brand .dot{width:12px;height:12px;border-radius:4px;background:linear-gradient(135deg,var(--brand),var(--brand2));box-shadow:0 0 16px rgba(129,90,247,.8)}
  .nav-btns{display:flex;gap:10px;align-items:center}
  .user-chip{font-size:13px;color:#b6f2d4;background:rgba(52,211,153,.12);border:1px solid rgba(52,211,153,.3);border-radius:999px;padding:7px 14px;font-weight:600}
  .btn-grad{background:linear-gradient(135deg,var(--brand),var(--brand2)) !important;border:none !important;color:#fff !important;box-shadow:0 4px 18px rgba(120,80,240,.4)}
  .btn-grad:hover{transform:translateY(-1px);box-shadow:0 8px 26px rgba(120,80,240,.55)}

  .hero{text-align:center;padding:72px 0 40px}
  .hero .tag,.hero h1,.hero p,.hero .cta{opacity:0;animation:rise .9s cubic-bezier(.22,1,.36,1) forwards}
  .hero h1{animation-delay:.12s}
  .hero p{animation-delay:.24s}
  .hero .cta{animation-delay:.36s}
  @keyframes rise{from{opacity:0;transform:translateY(26px)}to{opacity:1;transform:translateY(0)}}
  .hero .tag{display:inline-block;font-size:12px;letter-spacing:2px;color:#b6bdf7;border:1px solid rgba(120,110,250,.4);border-radius:999px;padding:6px 16px;background:rgba(90,80,220,.12);margin-bottom:22px}
  .hero h1{font-size:56px;font-weight:900;line-height:1.15;letter-spacing:1px;
    background:linear-gradient(120deg,#fff 10%,#c7bfff 35%,#8f7bf7 55%,#c7bfff 75%,#fff 95%);background-size:200% auto;-webkit-background-clip:text;background-clip:text;color:transparent;animation:rise .9s cubic-bezier(.22,1,.36,1) .12s forwards,shine 7s linear 1.1s infinite}
  @keyframes shine{to{background-position:-200% center}}
  .hero p{color:var(--muted);font-size:16px;margin-top:16px;line-height:1.8}
  .hero .cta{margin-top:30px;display:flex;gap:14px;justify-content:center}
  .hero .cta .el-button{padding:22px 32px;font-size:15px;border-radius:12px}

  .stats{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:46px 0 10px}
  .stat-card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:26px 20px;text-align:center;backdrop-filter:blur(8px);position:relative;overflow:hidden;transition:transform .25s,border-color .25s,box-shadow .25s}
  .stat-card:hover{transform:translateY(-4px);border-color:rgba(140,110,255,.45);box-shadow:0 14px 36px rgba(80,60,200,.28)}
  .stat-card::before{content:"";position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,transparent,rgba(140,110,255,.7),transparent)}
  .stat-card .num{font-size:38px;font-weight:900;background:linear-gradient(120deg,#fff,#b9aefe);-webkit-background-clip:text;background-clip:text;color:transparent;font-variant-numeric:tabular-nums}
  .stat-card .lbl{color:var(--muted);font-size:13px;margin-top:8px;letter-spacing:1px}

  .section{margin:64px 0}
  .section-head{display:flex;align-items:baseline;gap:14px;margin-bottom:22px}
  .section-head h2{font-size:24px;font-weight:800}
  .section-head span{color:var(--muted);font-size:13px}
  .boards{display:grid;grid-template-columns:1fr 1fr;gap:18px}
  .board{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:22px;backdrop-filter:blur(8px)}
  .board.full{grid-column:1/-1}
  .board h3{font-size:16px;font-weight:800;display:flex;align-items:center;gap:8px;margin-bottom:4px}
  .board .sub{color:var(--muted);font-size:12px;margin-bottom:14px}
  .board .el-table{--el-table-bg-color:transparent;--el-table-tr-bg-color:transparent;--el-table-header-bg-color:transparent;
    --el-table-border-color:rgba(255,255,255,.07);--el-table-row-hover-bg-color:rgba(255,255,255,.045);
    --el-table-header-text-color:#8b93b0;--el-table-text-color:#e8ecf8;font-size:14px}
  .board .el-table::before{display:none}
  .rk{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:8px;font-size:13px;font-weight:800;background:rgba(255,255,255,.06);color:var(--muted)}
  .rk.g1{background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#442c00}
  .rk.g2{background:linear-gradient(135deg,#e5e7eb,#9ca3af);color:#26292f}
  .rk.g3{background:linear-gradient(135deg,#f6ad7b,#c2703d);color:#3d1e05}
  .pw{font-weight:800;color:#ffd479;font-variant-numeric:tabular-nums}
  .mb{font-weight:700;color:#8ce3c2;font-variant-numeric:tabular-nums}
  .q{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;background:rgba(140,110,255,.15);color:#c3b8ff;border:1px solid rgba(140,110,255,.25)}

  .links{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px}
  .link-card{display:flex;align-items:center;gap:16px;background:var(--card);border:1px solid var(--line);border-radius:18px;padding:22px;text-decoration:none;color:var(--text);backdrop-filter:blur(8px);transition:.2s;cursor:pointer}
  .link-card:hover{transform:translateY(-2px);border-color:rgba(140,110,255,.45);box-shadow:0 12px 32px rgba(80,60,200,.25)}
  .link-card .ic{width:52px;height:52px;border-radius:14px;display:flex;align-items:center;justify-content:center;font-size:26px;background:linear-gradient(135deg,rgba(99,102,241,.25),rgba(168,85,247,.25));border:1px solid rgba(140,110,255,.3);flex-shrink:0}
  .link-card .t{font-size:16px;font-weight:800}
  .link-card .d{color:var(--muted);font-size:13px;margin-top:5px;line-height:1.6}
  .link-card .go{margin-left:auto;flex-shrink:0;color:#a5b0ff;font-size:13px;font-weight:700;white-space:nowrap}
  @media(max-width:900px){.links{grid-template-columns:1fr}}

  footer{color:var(--muted);font-size:13px;text-align:center;padding:50px 0 36px;border-top:1px solid var(--line);margin-top:70px}
  footer a{color:#a5b0ff;text-decoration:none}

  .reveal{opacity:0;transform:translateY(34px);transition:opacity .8s cubic-bezier(.22,1,.36,1),transform .8s cubic-bezier(.22,1,.36,1)}
  .reveal.in{opacity:1;transform:none}
  @media(prefers-reduced-motion:reduce){
    .reveal{opacity:1;transform:none;transition:none}
    .hero .tag,.hero h1,.hero p,.hero .cta{opacity:1;animation:none}
  }

  .auth-dialog{--el-dialog-border-radius:20px}
  .auth-dialog .el-dialog__header{padding-bottom:2px}
  .auth-hint{color:var(--muted);font-size:13px;margin-bottom:18px}
  .auth-switch{color:var(--muted);font-size:13px;text-align:center;margin-top:14px}
  .auth-switch a{color:#a5b0ff;cursor:pointer}

  @media(max-width:900px){
    .stats{grid-template-columns:repeat(2,1fr)}
    .boards{grid-template-columns:1fr}
    .hero h1{font-size:38px}
  }
  @media(max-width:600px){
    .wrap{padding:0 16px}
    nav{padding:16px 0}
    .brand{font-size:16px}
    .nav-btns{gap:6px}
    .nav-btns .el-button{padding:8px 14px}
    .user-chip{display:none}
    .hero{padding:44px 0 26px}
    .hero h1{font-size:30px}
    .hero p{font-size:14px}
    .hero .cta{flex-wrap:wrap}
    .hero .cta .el-button{padding:18px 22px;font-size:14px}
    .stats{gap:10px;margin:30px 0 6px}
    .stat-card{padding:18px 12px;border-radius:14px}
    .stat-card .num{font-size:27px}
    .section{margin:44px 0}
    .section-head{flex-direction:column;gap:4px}
    .section-head h2{font-size:20px}
    .board{padding:14px;border-radius:14px}
    .board .el-table{font-size:13px}
    .link-card{padding:16px}
    .link-card .go{display:none}
    footer{padding:36px 0 26px;line-height:2}
    .el-dialog{--el-dialog-width:calc(100vw - 28px) !important;width:calc(100vw - 28px) !important;max-width:calc(100vw - 28px)}
    .el-message{max-width:calc(100vw - 24px)}
  }
</style>
</head>
<body>
<div class="glow a"></div><div class="glow b"></div><div class="glow c"></div>
<div class="grid-bg"></div>
<canvas id="particles"></canvas>
<div id="app" v-cloak>
<div class="wrap">
  <nav>
    <div class="brand"><span class="dot"></span>宠物乐园</div>
    <div class="nav-btns" v-if="loggedIn">
      <span class="user-chip">✅ 已登录{{ userQQ ? ' · ' + userQQ : '' }}</span>
      <el-button class="btn-grad" round @click="goPortal">仪表盘</el-button>
      <el-button round plain @click="logout">退出登录</el-button>
    </div>
    <div class="nav-btns" v-else>
      <el-button round plain @click="openAuth('login')">登录</el-button>
      <el-button class="btn-grad" round @click="openAuth('register')">注册</el-button>
    </div>
  </nav>

  <div class="hero">
    <div class="tag">QQ 群宠物养成 · 全服数据中心</div>
    <h1>砸蛋抽宠 · 养成对战<br>飞升渡劫 · 摸金探险</h1>
    <p>跨群神榜实时竞技，副本、姻缘、天赋觉醒、深渊秘境……<br>登录玩家中心，随时随地管理你的专属宠物。</p>
    <div class="cta" v-if="loggedIn">
      <el-button class="btn-grad" size="large" round @click="goPortal">进入仪表盘</el-button>
      <el-button size="large" round plain @click="logout">退出登录</el-button>
    </div>
    <div class="cta" v-else>
      <el-button class="btn-grad" size="large" round @click="openAuth('register')">立即加入</el-button>
      <el-button size="large" round plain @click="openAuth('login')">进入玩家中心</el-button>
    </div>
    <div class="cta" v-if="appVer.ok">
      <el-button size="large" round plain @click="downloadApp">📱 下载安卓 App（{{ appVer.version_name }}）</el-button>
    </div>
  </div>

  <div class="stats reveal">
    <div class="stat-card"><div class="num">{{ fmt(disp.players) }}</div><div class="lbl">全服玩家</div></div>
    <div class="stat-card"><div class="num">{{ fmt(disp.auth_groups) }}</div><div class="lbl">授权群聊</div></div>
    <div class="stat-card"><div class="num">{{ fmt(disp.pets) }}</div><div class="lbl">在册宠物</div></div>
    <div class="stat-card"><div class="num">{{ fmt(disp.tomb_players) }}</div><div class="lbl">摸金玩家</div></div>
  </div>

  <div class="section reveal">
    <div class="section-head"><h2>🏅 宠物神榜</h2><span>全服跨群战力排行 · 前三每日可领神榜奖励</span></div>
    <div class="boards">
      <div class="board full">
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
  </div>

  <div class="section reveal">
    <div class="section-head"><h2>🏺 摸金风云榜</h2><span>地宫探险 · 冥币为王</span></div>
    <div class="boards">
      <div class="board full">
        <h3>💰 摸金排行（全服）</h3>
        <div class="sub">按永久冥币总量排序</div>
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
        <h3>🔥 今日摸金神榜</h3>
        <div class="sub">{{ todaySub }}</div>
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
        <h3>🌙 昨日摸金神榜</h3>
        <div class="sub">{{ ystSub }}</div>
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
    <div class="section-head"><h2>🚀 加入我们</h2><span>进群开玩 · 充值直达</span></div>
    <div class="links">
      <a class="link-card" href="https://qm.qq.com/q/S6ql07Q72m" target="_blank" rel="noopener">
        <div class="ic">💬</div>
        <div>
          <div class="t">小飞机器人官方群</div>
          <div class="d">官方 QQ 群：547205828 · 点击一键加群，交流攻略、领取福利</div>
        </div>
        <div class="go">加入群聊 →</div>
      </a>
      <a class="link-card" href="https://pay.ldxp.cn/shop/2P5XIVMD" target="_blank" rel="noopener">
        <div class="ic">💎</div>
        <div>
          <div class="t">充值入口</div>
          <div class="d">金币 / 积分 / 钻石卡密自助购买，兑换即时到账</div>
        </div>
        <div class="go">前往充值 →</div>
      </a>
      <a class="link-card" @click="goFeedback">
        <div class="ic">📣</div>
        <div>
          <div class="t">问题反馈</div>
          <div class="d">遇到 Bug 或有好建议？登录后即可提交，管理员处理后回复可查</div>
        </div>
        <div class="go">去反馈 →</div>
      </a>
    </div>
  </div>

  <footer>宠物乐园 · 数据每 30 秒更新 · <a href="/portal">玩家中心</a> · <a href="https://qm.qq.com/q/S6ql07Q72m" target="_blank" rel="noopener">官方群 547205828</a> · <a href="https://pay.ldxp.cn/shop/2P5XIVMD" target="_blank" rel="noopener">充值入口</a></footer>
</div>

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
    const disp = reactive({players:0, auth_groups:0, pets:0, tomb_players:0});
    const appVer = reactive({ok:false, version_name:'', url:''});
    const petRank = ref([]);
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
    const fmtPower = bp => bp >= 10000 ? (bp/10000).toFixed(2) + '万' : fmt(bp);

    function animate(key, target){
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
      try{
        const r = await (await fetch('/api/portal/home')).json();
        if(!r.ok) return;
        animate('players', r.stats.players);
        animate('auth_groups', r.stats.auth_groups);
        animate('pets', r.stats.pets);
        animate('tomb_players', r.stats.tomb_players);
        petRank.value = r.pet_rank || [];
        tombRank.value = r.tomb_rank || [];
        tombToday.value = r.tomb_today || [];
        tombYst.value = r.tomb_yesterday || [];
        todaySub.value = `统计 ${r.date_today} 00:00 至今获得冥币，每日 0 点重置`;
        ystSub.value = `统计 ${r.date_yesterday} 全天 · 前三名可领取随机宠物经验奖励`;
      }catch(e){
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

    function initParticles(){
      const cv = document.getElementById('particles');
      if(!cv) return;
      const ctx = cv.getContext('2d');
      let W = 0, H = 0, dots = [];
      const mouse = {x:-9999, y:-9999};
      function resize(){
        W = cv.width = innerWidth; H = cv.height = innerHeight;
        const n = Math.min(110, Math.round(W * H / 16000));
        dots = Array.from({length:n}, () => ({
          x: Math.random()*W, y: Math.random()*H,
          vx: (Math.random()-.5)*.35, vy: (Math.random()-.5)*.35,
          r: Math.random()*1.6 + .6
        }));
      }
      resize();
      addEventListener('resize', resize);
      addEventListener('pointermove', e => { mouse.x = e.clientX; mouse.y = e.clientY; });
      addEventListener('pointerleave', () => { mouse.x = -9999; mouse.y = -9999; });
      const LINK = 130, MOUSE = 170;
      function frame(){
        ctx.clearRect(0, 0, W, H);
        for(const d of dots){
          d.x += d.vx; d.y += d.vy;
          if(d.x < -20) d.x = W + 20; else if(d.x > W + 20) d.x = -20;
          if(d.y < -20) d.y = H + 20; else if(d.y > H + 20) d.y = -20;
          ctx.beginPath();
          ctx.arc(d.x, d.y, d.r, 0, Math.PI * 2);
          ctx.fillStyle = 'rgba(165,150,255,.55)';
          ctx.fill();
        }
        for(let i = 0; i < dots.length; i++){
          const a = dots[i];
          for(let j = i + 1; j < dots.length; j++){
            const b = dots[j];
            const dx = a.x - b.x, dy = a.y - b.y;
            const dist = Math.hypot(dx, dy);
            if(dist < LINK){
              ctx.beginPath();
              ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
              ctx.strokeStyle = `rgba(140,120,255,${(1 - dist / LINK) * .22})`;
              ctx.lineWidth = 1;
              ctx.stroke();
            }
          }
          const md = Math.hypot(a.x - mouse.x, a.y - mouse.y);
          if(md < MOUSE){
            ctx.beginPath();
            ctx.moveTo(a.x, a.y); ctx.lineTo(mouse.x, mouse.y);
            ctx.strokeStyle = `rgba(190,170,255,${(1 - md / MOUSE) * .3})`;
            ctx.lineWidth = 1;
            ctx.stroke();
          }
        }
        requestAnimationFrame(frame);
      }
      if(!matchMedia('(prefers-reduced-motion: reduce)').matches) requestAnimationFrame(frame);
    }

    function initMotion(){
      const io = new IntersectionObserver(es => {
        es.forEach(e => { if(e.isIntersecting){ e.target.classList.add('in'); io.unobserve(e.target); } });
      }, {threshold: .12});
      document.querySelectorAll('.reveal').forEach(el => io.observe(el));
      const glows = document.querySelectorAll('.glow');
      addEventListener('pointermove', e => {
        const rx = e.clientX / innerWidth - .5, ry = e.clientY / innerHeight - .5;
        glows.forEach((g, i) => {
          const k = (i + 1) * 14;
          g.style.transform = `translate(${rx * k}px, ${ry * k}px)`;
        });
      });
    }

    onMounted(()=>{
      checkAuth();
      loadHome();
      loadAppVer();
      setInterval(loadHome, 30000);
      initParticles();
      initMotion();
    });

    return {loggedIn, userQQ, loading, disp, appVer, petRank, tombRank, tombToday, tombYst, todaySub, ystSub,
            auth, authTitle, authHint, fmt, fmtPower, openAuth, goFeedback, goPortal, downloadApp, logout, submitAuth, sendCode};
  }
}).use(ElementPlus, {locale: ElementPlusLocaleZhCn}).mount('#app');
</script>
</body>
</html>
"""
