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
import secrets
import time
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


class PlayerPortal:
    def __init__(self, store, broadcast_callback=None, command_gateway=None):
        self.store = store
        self.broadcast_callback = broadcast_callback
        self.command_gateway = command_gateway
        self._attempts: dict[str, dict] = {}

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

    # --------------------------- 工具：数据格式化 ---------------------------
    def _format_pet(self, player: dict, group_id: str, qq: str) -> dict:
        pet = (player.get("pet") or {}).copy()
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

    def _player_summary(self, group_id: str, qq: str) -> dict:
        key = self.store.make_key(group_id, qq)
        player = self.store._data["players"].get(key)
        if not player:
            raise web.HTTPNotFound(text="未找到该宠物")
        pending = self.store.get_pet_custom_reviews(group_id, qq, status="pending")
        rejected = self.store.get_pet_custom_reviews(group_id, qq, status="rejected")
        # 只返回最近一条拒绝原因
        last_rejected = sorted(rejected, key=lambda x: x.get("created_at", 0), reverse=True)[:1]
        return {
            "group_id": group_id,
            "qq": qq,
            "coin": player.get("coin", 0),
            "jifen": player.get("jifen", 0),
            "diamond": player.get("diamond", 0),
            "bag": dict(player.get("bag", {})),
            "abyss": dict(self.store.abyss_state(player)),
            "stats": dict(player.get("stats", {})),
            "pet": self._format_pet(player, group_id, qq),
            "cooldowns": self._cooldown_list(player),
            "skills": list((player.get("pet") or {}).get("skills", [])),
            "artifact": (player.get("pet") or {}).get("artifact"),
            "artifact_names": list(data.ARTIFACTS.keys()),
            "skill_names": list(data.SKILLS.keys()),
            "custom_pending": pending,
            "custom_rejected": last_rejected,
            "custom_remaining": {
                "image": self.store.remaining_custom_changes(player, "image"),
                "species_name": self.store.remaining_custom_changes(player, "species_name"),
            },
        }

    def _cooldown_list(self, player: dict) -> list:
        """汇总玩家所有活动冷却：日常活动 + 固定玩法 + 限时活动。"""
        now = int(time.time())
        entries = []
        for action in data.DAILY_ACTIONS:
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
        app.router.add_get("/portal", self._portal_page)
        app.router.add_post("/api/portal/register", self._api_register)
        app.router.add_post("/api/portal/login", self._api_login)
        app.router.add_post("/api/portal/logout", self._api_logout)
        app.router.add_get("/api/portal/me", self._api_me)
        app.router.add_post("/api/portal/bind", self._api_bind)
        app.router.add_get("/api/portal/pet", self._api_pet)
        app.router.add_post("/api/portal/custom_redeem", self._api_custom_redeem)
        app.router.add_post("/api/portal/custom_submit", self._api_custom_submit)
        app.router.add_post("/api/portal/use_item", self._api_use_item)
        app.router.add_post("/api/portal/redeem", self._api_redeem)
        app.router.add_post("/api/portal/change_password", self._api_change_password)
        app.router.add_static(
            "/custom_images",
            path=self.store.custom_images_dir,
            name="custom_images",
        )

    async def _portal_page(self, request: web.Request) -> web.Response:
        sess = self._current_session(request)
        csrf = sess.get("csrf") if sess else secrets.token_urlsafe(24)
        html = _PORTAL_HTML.replace("{{CSRF_TOKEN}}", csrf)
        response = web.Response(text=html, content_type="text/html")
        if sess:
            # 刷新 Cookie 过期时间
            self._set_session(response, sess["aid"], csrf)
        return response

    async def _api_register(self, request: web.Request) -> web.Response:
        body = await request.json()
        qq = str(body.get("qq", "")).strip()
        password = str(body.get("password", ""))
        ip = request.remote or "unknown"
        ok, msg = self._check_rate(f"{ip}:{qq}")
        if not ok:
            return web.json_response({"ok": False, "msg": msg})
        if not qq.isdigit() or len(qq) < 5 or len(qq) > 12:
            return web.json_response({"ok": False, "msg": "QQ 号格式不正确"})
        if len(password) < 6:
            return web.json_response({"ok": False, "msg": "密码长度至少 6 位"})
        if self.store.get_account_by_qq(qq):
            return web.json_response({"ok": False, "msg": "该 QQ 号已注册"})
        salt = self._make_salt()
        phash = self._hash_password(password, salt)
        account = self.store.create_account(qq, phash, salt)
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
        account["last_login"] = int(time.time())
        await self.store.save()
        self._reset_rate(f"{ip}:{qq}")
        csrf = secrets.token_urlsafe(24)
        resp = web.json_response({"ok": True, "msg": "登录成功"})
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
            pet = player.get("pet") if player else None
            bound.append({
                "group_id": bp.get("group"),
                "qq": bp.get("qq"),
                "nickname": pet.get("nickname") if pet else bp.get("nickname", "未命名"),
                "species": pet.get("species") if pet else bp.get("species", "未知"),
                "level": pet.get("level", 1) if pet else 1,
                "quality": pet.get("quality", "普通") if pet else "普通",
                "image_url": images.pet_image_url(pet.get("species")) if pet else None,
            })
        return web.json_response({
            "ok": True,
            "account": {"id": account["id"], "qq": account["qq"]},
            "bound_pets": bound,
        })

    async def _api_bind(self, request: web.Request) -> web.Response:
        self._check_csrf(request)
        sess = self._require_session(request)
        body = await request.json()
        group_id = str(body.get("group_id", "")).strip()
        qq = str(body.get("qq", "")).strip()
        if not group_id or not qq:
            return web.json_response({"ok": False, "msg": "群号和用户 ID 不能为空"})
        ip = request.remote or "unknown"
        ok, msg = self._check_rate(f"{ip}:bind:{qq}")
        if not ok:
            return web.json_response({"ok": False, "msg": msg})
        success, msg2 = self.store.bind_pet_to_account(sess["aid"], group_id, qq)
        if success:
            await self.store.save()
        self._reset_rate(f"{ip}:bind:{qq}") if success else None
        return web.json_response({"ok": success, "msg": msg2})

    async def _api_pet(self, request: web.Request) -> web.Response:
        self._require_session(request)
        group_id = request.query.get("group_id", "").strip()
        qq = request.query.get("qq", "").strip()
        if not group_id or not qq:
            raise web.HTTPBadRequest(text="缺少群号或用户 ID")
        # 验证当前账号确实绑定了该宠物
        owner = self.store.account_for_pet(group_id, qq)
        sess = self._current_session(request)
        if owner != sess.get("aid"):
            raise web.HTTPForbidden(text="你没有绑定该宠物")
        return web.json_response({"ok": True, **self._player_summary(group_id, qq)})

    async def _api_custom_redeem(self, request: web.Request) -> web.Response:
        try:
            self._check_csrf(request)
            sess = self._require_session(request)
            account = self.store.get_account(sess.get("aid", ""))
            body = await request.json()
            group_id = str(body.get("group_id", "")).strip()
            qq = str(body.get("qq", "")).strip()
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
            pet, err = self.store.redeem_custom_card(code, player, sess.get("aid"))
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
                "pet": self._format_pet(player, group_id, qq),
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
        pet = player.get("pet")
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

    # --------------------------- 道具使用 / 卡密兑换 / 改密 ---------------------------
    async def _api_use_item(self, request: web.Request) -> web.Response:
        self._check_csrf(request)
        sess = self._require_session(request)
        if self.command_gateway is None:
            return web.json_response({"ok": False, "msg": "功能暂不可用，请重载插件后重试"})
        body = await request.json()
        group_id = str(body.get("group_id", "")).strip()
        qq = str(body.get("qq", "")).strip()
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
            "summary": self._player_summary(group_id, qq),
        })

    async def _api_redeem(self, request: web.Request) -> web.Response:
        self._check_csrf(request)
        sess = self._require_session(request)
        if self.command_gateway is None:
            return web.json_response({"ok": False, "msg": "功能暂不可用，请重载插件后重试"})
        body = await request.json()
        group_id = str(body.get("group_id", "")).strip()
        qq = str(body.get("qq", "")).strip()
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
            "summary": self._player_summary(group_id, qq),
        })

    async def _api_change_password(self, request: web.Request) -> web.Response:
        self._check_csrf(request)
        sess = self._require_session(request)
        body = await request.json()
        old_password = str(body.get("old_password", ""))
        new_password = str(body.get("new_password", ""))
        if len(new_password) < 6:
            return web.json_response({"ok": False, "msg": "新密码至少 6 位"})
        account = self.store.get_account(sess.get("aid"))
        if not account:
            return web.json_response({"ok": False, "msg": "账号不存在"})
        ok_rate, why = self._check_rate(f"chpwd:{sess.get('aid')}")
        if not ok_rate:
            return web.json_response({"ok": False, "msg": why})
        expected = self._hash_password(old_password, account.get("salt", ""))
        if not secrets.compare_digest(expected, account.get("password_hash", "")):
            return web.json_response({"ok": False, "msg": "旧密码不正确"})
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
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>宠物乐园 · 玩家中心</title>
<style>
:root{
  --brand:#2f6bff;
  --brand-2:#7c3aed;
  --brand-3:#06b6d4;
  --grad:linear-gradient(120deg,#2f6bff 0%,#6d4aff 55%,#9333ea 100%);
  --brand-soft:#eef3ff;
  --bg:#f6f8fd;
  --card:#ffffff;
  --line:#e8ecf6;
  --line-strong:#d8dfef;
  --text:#141a2a;
  --muted:#8a93a8;
  --danger:#e5484d;
  --danger-soft:#feeef0;
  --ok:#0f9d58;
  --ok-soft:#e9f8f0;
  --shadow-sm:0 1px 2px rgba(20,26,42,.05);
  --shadow:0 1px 2px rgba(20,26,42,.04),0 12px 32px -8px rgba(20,26,42,.10);
  --shadow-lg:0 24px 64px -12px rgba(47,107,255,.18),0 2px 6px rgba(20,26,42,.06);
}
*{box-sizing:border-box}
html,body{height:100%;margin:0;background:var(--bg);color:var(--text);font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;overflow-x:hidden;overflow-y:auto;-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
body::before{content:'';position:fixed;top:-260px;left:-160px;width:640px;height:640px;border-radius:50%;background:radial-gradient(closest-side,rgba(47,107,255,.14),transparent);pointer-events:none;z-index:0}
body::after{content:'';position:fixed;bottom:-280px;right:-180px;width:720px;height:720px;border-radius:50%;background:radial-gradient(closest-side,rgba(147,51,234,.10),transparent);pointer-events:none;z-index:0}
#app{display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;padding:28px 20px;position:relative;z-index:1}

/* 主容器 */
.console{width:100%;max-width:440px;transition:max-width .35s cubic-bezier(.4,0,.2,1)}
.console.wide{max-width:min(1120px,100%)}
.brand{display:flex;align-items:center;justify-content:center;gap:11px;font-size:18px;font-weight:800;letter-spacing:.3px;margin-bottom:20px;color:var(--text)}
.screen-wrap{background:var(--card);border:1px solid var(--line);border-radius:24px;box-shadow:var(--shadow-lg);overflow:hidden;position:relative}
.screen-wrap::before{content:'';position:absolute;top:0;left:0;right:0;height:4px;background:var(--grad);z-index:5}
.screen{position:relative;border-radius:24px;min-height:340px;overflow:hidden auto;padding:32px 30px 30px}
.console.wide .screen{min-height:min(70vh,620px);max-height:min(820px,calc(92vh - 96px));padding:30px}

/* 通用排版 */
h1,h2,h3{margin:0 0 12px;font-weight:800;letter-spacing:-.2px}
h1{font-size:26px}
h2{font-size:19px}
h3{font-size:14px;color:var(--muted);text-transform:none;display:flex;align-items:center;gap:8px;margin:22px 0 10px;font-weight:700;letter-spacing:.4px}
h3::before{content:'';width:4px;height:14px;border-radius:4px;background:var(--grad)}
.muted{color:var(--muted);font-size:13px;line-height:1.6}

/* 表单 */
.form{display:flex;flex-direction:column;gap:14px;animation:fadeIn .45s cubic-bezier(.16,1,.3,1) both;max-width:360px;margin:8px auto 0}
label{font-size:13px;font-weight:600;color:#3c455c}
input,button,select{font-family:inherit;border:none;outline:none;border-radius:12px}
input,select{background:#f7f9fd;border:1.5px solid transparent;color:var(--text);padding:13px 16px;font-size:15px;transition:border-color .2s,box-shadow .2s,background .2s}
input:hover{background:#f2f5fc}
input::placeholder{color:#aab3c7}
input:focus,select:focus{background:#fff;border-color:var(--brand);box-shadow:0 0 0 4px rgba(47,107,255,.12)}
button{cursor:pointer;background:var(--grad);color:#fff;font-weight:700;padding:13px 20px;font-size:15px;letter-spacing:.3px;transition:transform .15s,box-shadow .2s,filter .15s;box-shadow:0 8px 20px -6px rgba(47,107,255,.5)}
button:hover{transform:translateY(-1px);box-shadow:0 12px 26px -6px rgba(47,107,255,.55);filter:saturate(1.08)}
button:active{transform:translateY(0);box-shadow:0 4px 12px -4px rgba(47,107,255,.5)}
button.ghost{background:#fff;color:#3c455c;border:1.5px solid var(--line-strong);box-shadow:var(--shadow-sm);font-weight:600}
button.ghost:hover{border-color:var(--brand);color:var(--brand);background:var(--brand-soft);box-shadow:var(--shadow-sm)}
button:disabled{opacity:.45;cursor:not-allowed;box-shadow:none;transform:none}
.links{display:flex;justify-content:space-between;margin-top:6px}
.links a{color:var(--brand);text-decoration:none;font-size:13px;font-weight:600}
.links a:hover{text-decoration:underline}

/* 消息 */
.msg{padding:11px 16px;border-radius:12px;font-size:14px;font-weight:500;margin-bottom:12px;animation:slideDown .3s cubic-bezier(.16,1,.3,1);display:flex;align-items:center;gap:8px}
.msg::before{font-size:15px}
.msg.err{background:var(--danger-soft);color:#c53a3f;border:1px solid rgba(229,72,77,.18)}
.msg.err::before{content:'⚠️'}
.msg.ok{background:var(--ok-soft);color:#0c7a45;border:1px solid rgba(15,157,88,.18)}
.msg.ok::before{content:'✅'}

/* 仪表盘 */
.dashboard{display:none;animation:fadeIn .45s cubic-bezier(.16,1,.3,1) both}
.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;flex-wrap:wrap;gap:10px}
.topbar h2{margin:0}
.account{font-size:13px;color:var(--muted);display:flex;align-items:center;gap:10px;background:#f7f9fd;border:1px solid var(--line);border-radius:999px;padding:5px 6px 5px 14px}
.account button{border-radius:999px}
.pet-selector{display:grid;grid-template-columns:repeat(auto-fill,minmax(168px,1fr));gap:12px;margin-bottom:18px}
.pet-chip{background:#fff;border:1.5px solid var(--line);border-radius:16px;padding:11px 13px;cursor:pointer;transition:border-color .2s,box-shadow .25s,transform .2s;display:flex;align-items:center;gap:11px;min-width:0;position:relative}
.pet-chip:hover{border-color:var(--line-strong);box-shadow:var(--shadow);transform:translateY(-2px)}
.pet-chip.active{border-color:var(--brand);background:linear-gradient(180deg,var(--brand-soft),#fff);box-shadow:0 8px 20px -8px rgba(47,107,255,.35)}
.pet-chip img{width:42px;height:42px;border-radius:12px;object-fit:cover;background:var(--bg);flex:0 0 auto;border:1px solid var(--line)}
.pet-chip .info{line-height:1.35;min-width:0}
.pet-chip .name{font-size:14px;font-weight:700;color:var(--text);word-break:break-all;overflow-wrap:anywhere}
.pet-chip .sub{font-size:11.5px;color:var(--muted);word-break:break-all}

/* 宠物卡片 */
.pet-card{display:flex;flex-direction:column;align-items:center;background:#fff;border:1px solid var(--line);border-radius:20px;padding:0 18px 22px;margin-bottom:16px;position:relative;overflow:hidden;box-shadow:var(--shadow-sm)}
.pet-card::before{content:'';width:calc(100% + 36px);height:110px;margin:0 -18px;background:var(--grad);opacity:.92;flex:0 0 auto}
.pet-card::after{content:'';position:absolute;top:0;left:0;right:0;height:110px;background:radial-gradient(circle at 80% -30%,rgba(255,255,255,.35),transparent 60%),radial-gradient(circle at 12% 130%,rgba(255,255,255,.22),transparent 55%);pointer-events:none}
.pet-img{width:150px;height:150px;border-radius:20px;object-fit:cover;background:#fff;border:5px solid #fff;box-shadow:0 16px 36px -10px rgba(20,26,42,.28);margin-top:-64px;position:relative;z-index:2;animation:popIn .45s cubic-bezier(.16,1,.3,1) both}
.pet-title{margin-top:14px;text-align:center;position:relative;z-index:2}
.pet-title .name{font-size:23px;font-weight:800;letter-spacing:-.3px}
.pet-title .meta{font-size:13px;color:var(--muted);margin-top:5px}
.pet-tags{display:flex;gap:6px;flex-wrap:wrap;justify-content:center;margin-top:9px}
.pet-tag{font-size:11px;font-weight:600;background:var(--brand-soft);color:var(--brand);border:1px solid rgba(47,107,255,.2);border-radius:999px;padding:3px 11px}
.pet-resource{font-size:12px;color:var(--muted);margin-top:9px}
.badges{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap;justify-content:center;position:relative;z-index:2}
.badge{font-size:12.5px;font-weight:600;background:#f7f9fd;padding:6px 14px;border-radius:999px;border:1px solid var(--line);color:#3c455c}

/* 属性网格 */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(112px,1fr));gap:10px;margin-bottom:6px}
.stat{background:#fff;border:1px solid var(--line);border-radius:14px;padding:13px 15px;transition:box-shadow .2s,transform .2s}
.stat:hover{box-shadow:var(--shadow);transform:translateY(-1px)}
.stat .label{font-size:12px;color:var(--muted);font-weight:500}
.stat .value{font-size:19px;font-weight:800;margin-top:3px;font-variant-numeric:tabular-nums}

/* 财产 */
.wallet{display:grid;grid-template-columns:repeat(auto-fit,minmax(100px,1fr));gap:10px;margin-bottom:6px}
.coin{text-align:center;background:#fff;border:1px solid var(--line);border-radius:14px;padding:15px 6px;transition:box-shadow .2s,transform .2s}
.coin:hover{box-shadow:var(--shadow);transform:translateY(-1px)}
.coin .label{font-size:11.5px;color:var(--muted);font-weight:500}
.coin .value{font-size:18px;font-weight:800;background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent;margin-top:5px;word-break:break-all;font-variant-numeric:tabular-nums}

/* 背包 */
.bag{display:grid;grid-template-columns:repeat(auto-fill,minmax(98px,1fr));gap:9px;max-height:290px;overflow-y:auto;padding:2px 4px 2px 2px}
.bag::-webkit-scrollbar{width:6px}
.bag::-webkit-scrollbar-thumb{background:var(--line-strong);border-radius:3px}
.bag{grid-template-columns:repeat(auto-fill,minmax(150px,1fr));max-height:420px}
.item{background:#fff;border:1px solid var(--line);border-radius:14px;padding:12px 12px 11px;text-align:left;font-size:13px;color:#3c455c;transition:box-shadow .2s,border-color .2s,transform .2s;display:flex;flex-direction:column;gap:8px}
.item:hover{border-color:var(--line-strong);box-shadow:var(--shadow);transform:translateY(-1px)}
.item .item-name{font-weight:700;color:var(--text);word-break:break-all;line-height:1.4}
.item .count{color:var(--brand);font-weight:800;font-variant-numeric:tabular-nums;font-size:12px}
.item .use-row{display:flex;gap:6px;margin-top:auto}
.item .use-row input{flex:1;min-width:0;width:52px;padding:6px 8px;font-size:13px;border-radius:9px;text-align:center}
.item .use-row button{flex:0 0 auto;padding:6px 13px;font-size:12.5px;border-radius:9px;box-shadow:none}
.item .item-tag{align-self:flex-start;font-size:10.5px;font-weight:700;border-radius:999px;padding:2px 9px;background:var(--brand-soft);color:var(--brand);border:1px solid rgba(47,107,255,.18)}
.item .item-tag.art{background:#fef3e2;color:#c2660a;border-color:rgba(194,102,10,.22)}
.item .item-tag.skill{background:#f2ecff;color:#7c3aed;border-color:rgba(124,58,237,.2)}

/* 冷却 */
.cd-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(128px,1fr));gap:9px}
.cd{background:#fff;border:1px solid var(--line);border-radius:13px;padding:11px 13px;transition:box-shadow .2s}
.cd:hover{box-shadow:var(--shadow-sm)}
.cd .cd-name{font-size:12.5px;color:var(--muted);font-weight:600}
.cd .cd-time{font-size:15px;font-weight:800;margin-top:4px;font-variant-numeric:tabular-nums}
.cd.ready .cd-time{color:var(--ok)}
.cd.busy .cd-time{color:#c2660a}

/* 卡密兑换 */
.redeem-box{margin-top:2px;padding:18px;background:linear-gradient(135deg,#eef7ff,#f4f0ff);border:1px solid #e0e6f7;border-radius:16px}
.redeem-box .bind-row input{background:#fff}
.redeem-result{margin-top:12px;font-size:13px;line-height:1.8;white-space:pre-wrap;background:#fff;border:1px solid var(--line);border-radius:12px;padding:12px 14px;display:none}
.redeem-result.show{display:block}
.empty{text-align:center;color:var(--muted);padding:34px 0;font-size:14px}

/* 绑定表单 */
.bind-box{margin-top:14px;padding:18px;background:#f7f9fd;border-radius:16px;border:1.5px dashed var(--line-strong)}
.bind-box h3{margin:0 0 10px}
.bind-row{display:flex;flex-wrap:wrap;gap:8px}
.bind-row input{flex:1 1 120px;min-width:0;background:#fff}
.bind-row button{flex:0 0 auto}
.bind-help{margin-top:9px;font-size:12px;color:var(--muted);line-height:1.7}
.bind-open{text-align:center;margin-top:20px;padding-top:18px;border-top:1px solid var(--line)}

/* 弹窗 */
.modal{position:fixed;inset:0;background:rgba(20,26,42,.4);backdrop-filter:blur(6px);display:none;align-items:center;justify-content:center;z-index:50;padding:20px}
.modal.show{display:flex}
.modal .sheet{background:#fff;border:1px solid var(--line);border-radius:22px;padding:26px;width:min(440px,100%);box-shadow:0 32px 80px -12px rgba(20,26,42,.3);animation:popIn .3s cubic-bezier(.16,1,.3,1) both}
.modal .sheet h3{color:var(--text);font-size:17px;margin:0 0 16px}
.modal .sheet h3::before{display:none}
.modal .sheet .bind-row{margin-bottom:10px}
.modal .sheet .actions{display:flex;gap:10px;justify-content:flex-end;margin-top:18px}
/* 定制弹窗 */
.custom-sheet{width:min(480px,100%)}
.modal-head{text-align:center;margin-bottom:18px}
.modal-icon{font-size:38px;line-height:1;margin-bottom:8px}
.modal-head h3{margin:0 0 6px;justify-content:center}
.modal-sub{font-size:13px;color:var(--muted);margin:0}
.custom-sheet label{display:block;margin:12px 0 6px}
.custom-sheet input[type="text"]{width:100%}
.upload-zone{border:2px dashed var(--line-strong);border-radius:16px;padding:26px;text-align:center;cursor:pointer;transition:border-color .2s,background .2s;background:#f7f9fd;margin:12px 0}
.upload-zone:hover{border-color:var(--brand);background:var(--brand-soft)}
.upload-plus{font-size:30px;color:var(--brand);line-height:1;margin-bottom:6px;font-weight:300}
.upload-text{font-size:15px;font-weight:600;color:var(--text);margin-bottom:4px}
.upload-hint{font-size:12px;color:var(--muted)}
.crop-preview{margin-bottom:12px;text-align:center}
.crop-preview img{max-width:160px;max-height:160px;border-radius:16px;border:1px solid var(--line);box-shadow:var(--shadow)}
.pet-source{margin-top:16px;text-align:center;color:#b0b8ca;font-size:12px;word-break:break-all}
.custom-box{margin-top:14px;padding:18px;background:linear-gradient(135deg,#f4f0ff,#eef5ff);border-radius:16px;border:1px solid #e2e0f7}
.custom-badge{color:var(--brand-2);font-size:14px;font-weight:700;margin-bottom:8px}
.custom-remaining{font-size:12px;color:var(--muted);margin-bottom:12px}
input[type="file"]{padding:10px;background:#fff;border:1px solid var(--line);color:var(--text);border-radius:12px;width:100%}
.sec{font-size:13px;font-weight:700;color:#3c455c;margin-bottom:8px}

/* 全屏应用布局 */
body.appmode #app{padding:0;align-items:stretch;justify-content:flex-start}
body.appmode::before,body.appmode::after{display:none}
body.appmode .console{max-width:none}
body.appmode .brand{display:none}
body.appmode .screen-wrap{border:none;border-radius:0;box-shadow:none;background:transparent;overflow:visible}
body.appmode .screen-wrap::before{display:none}
body.appmode .screen{border-radius:0;padding:0;min-height:100vh;max-height:none;overflow:visible}
.layout{display:flex;min-height:100vh;background:var(--bg)}
.sidebar{width:264px;background:#fff;border-right:1px solid var(--line);display:flex;flex-direction:column;padding:22px 16px 18px;position:fixed;left:0;top:0;height:100vh;overflow-y:auto;z-index:20}
.sidebar::-webkit-scrollbar{width:6px}
.sidebar::-webkit-scrollbar-thumb{background:var(--line-strong);border-radius:3px}
.side-brand{font-size:16px;font-weight:800;letter-spacing:-.2px;padding:2px 8px 16px;border-bottom:1px solid var(--line);margin-bottom:14px;display:flex;align-items:center;gap:9px}
.side-brand::before{content:'';width:10px;height:10px;border-radius:3px;background:var(--grad);flex:0 0 auto}
.side-sec{font-size:11.5px;color:var(--muted);font-weight:700;letter-spacing:1.2px;margin:4px 8px 9px}
.side-pets{display:flex;flex-direction:column;gap:8px}
.side-pets .pet-chip{border-radius:14px}
.side-bind{margin-top:12px;width:100%;padding:11px 14px;font-size:13.5px;border-radius:12px}
.side-foot{margin-top:auto;padding-top:14px;border-top:1px solid var(--line);display:flex;flex-direction:column;gap:9px}
.side-foot .side-user{padding:0 2px}
.side-foot-btns{display:flex;gap:8px}
.side-foot-btns button{flex:1;padding:8px 10px;font-size:12.5px;border-radius:10px}
.side-user{font-size:12.5px;color:var(--muted);font-weight:600;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.content{flex:1;min-width:0;padding:26px 34px 40px;overflow-x:hidden;margin-left:264px}
.content-inner{max-width:960px;margin:0 auto}
.content .topbar{margin-bottom:20px}
@media(max-width:760px){
  .layout{flex-direction:column}
  .sidebar{width:100%;flex:none;position:static;height:auto;border-right:none;border-bottom:1px solid var(--line)}
  .side-foot{margin-top:14px}
  .content{padding:20px 16px 32px;margin-left:0}
}

/* 动画 */
@keyframes fadeIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
@keyframes slideDown{from{opacity:0;transform:translateY(-8px)}to{opacity:1;transform:translateY(0)}}
@keyframes popIn{0%{transform:scale(.94);opacity:0}100%{transform:scale(1);opacity:1}}

@media(max-width:460px){
  #app{padding:14px 10px}
  .screen{min-height:360px;padding:24px 18px}
  .pet-img{width:124px;height:124px;margin-top:-56px}
  .wallet{grid-template-columns:repeat(2,1fr)}
  .brand{font-size:16px}
}
</style>
</head>
<body>
<div id="app">
  <div class="console">
    <div class="brand">宠物乐园 · 玩家中心</div>
    <div class="screen-wrap">
      <div id="screen" class="screen on">
        <noscript>请启用 JavaScript 以使用玩家中心。</noscript>
      </div>
    </div>
  </div>
  <div class="modal" id="bindModal">
    <div class="sheet">
      <h3>＋ 绑定新宠物</h3>
      <div class="bind-row">
        <input id="bindGroup" type="text" placeholder="群号">
        <input id="bindQQ" type="text" inputmode="numeric" placeholder="绑定用户ID">
      </div>
      <p class="bind-help">输入你在群内使用宠物乐园的群号和用户 ID，即可查看该群宠物。</p>
      <div class="actions">
        <button class="ghost" onclick="closeBindModal()">取消</button>
        <button id="bindBtn">绑定</button>
      </div>
    </div>
  </div>
  <div class="modal" id="pwdModal">
    <div class="sheet">
      <h3>🔒 修改密码</h3>
      <div class="bind-row"><input id="pwdOld" type="password" placeholder="旧密码" style="flex:1 1 100%"></div>
      <div class="bind-row"><input id="pwdNew" type="password" placeholder="新密码（至少 6 位）" style="flex:1 1 100%"></div>
      <div class="bind-row"><input id="pwdNew2" type="password" placeholder="确认新密码" style="flex:1 1 100%"></div>
      <div class="actions">
        <button class="ghost" onclick="closePwdModal()">取消</button>
        <button id="pwdBtn">确认修改</button>
      </div>
    </div>
  </div>
  <div class="modal" id="customModal">
    <div class="sheet custom-sheet">
      <div class="modal-head">
        <div class="modal-icon">✨</div>
        <h3>修改宠物形象</h3>
        <p class="modal-sub">定制专属形象与种类名称，审核通过后生效</p>
      </div>
      <label class="fld">种类名称（显示名称）</label>
      <input id="customSpeciesName" type="text" placeholder="例如：灭世魔龙">
      <label class="fld">宠物图片</label>
      <input id="customImage" type="file" accept="image/*" style="display:none">
      <div class="upload-zone" id="pickImageBtn">
        <div class="upload-plus">+</div>
        <div class="upload-text">点击选择图片</div>
        <div class="upload-hint">支持 jpg / png / gif / webp，将裁剪为 512×512</div>
      </div>
      <div id="cropPreview" class="crop-preview"></div>
      <p class="bind-help">每月图片和名称各限 3 次，提交后需管理员审核，预计 3 个工作日内完成。审核期间无法再次提交。</p>
      <div class="actions">
        <button class="ghost" onclick="closeCustomModal()">取消</button>
        <button id="submitCustomBtn">提交审核</button>
      </div>
    </div>
  </div>
  <div class="modal" id="cropModal">
    <div class="sheet" style="width:min(560px,100%)">
      <h3>裁剪图片</h3>
      <div style="display:flex;justify-content:center;margin:10px 0">
        <canvas id="cropCanvas" width="512" height="512" style="max-width:100%;height:auto;border-radius:12px;border:1px solid rgba(255,176,0,.2);cursor:grab"></canvas>
      </div>
      <div style="display:flex;align-items:center;gap:10px;margin:10px 0">
        <span class="muted">缩小</span>
        <input id="cropZoom" type="range" min="100" max="300" value="100" style="flex:1">
        <span class="muted">放大</span>
      </div>
      <p class="muted">拖动图片调整位置，滑动缩放，最终输出 512×512。</p>
      <div class="actions">
        <button class="ghost" onclick="closeCropModal()">取消</button>
        <button id="saveCropBtn">保存裁剪</button>
      </div>
    </div>
  </div>
</div>
<script>
const CSRF_TOKEN = '{{CSRF_TOKEN}}';
const screen = document.getElementById('screen');

async function api(path, method='GET', body=null){
  const opts = {method, headers:{'X-CSRF-Token':CSRF_TOKEN}};
  if(body){opts.headers['Content-Type']='application/json'; opts.body=JSON.stringify(body)}
  const r = await fetch(path, opts);
  if(r.status === 401 || r.status === 403){
    return {unauthorized:true};
  }
  return r.json().catch(()=>null);
}

function msg(text, type='err'){
  const d = document.createElement('div');
  d.className = `msg ${type}`;
  d.textContent = text;
  const host = document.querySelector('.content-inner') || screen;
  host.prepend(d);
  setTimeout(()=>d.remove(), 4000);
}

function viewLogin(){
  document.body.classList.remove('appmode');
  document.querySelector('.console').classList.remove('wide');
  screen.innerHTML = `
    <div style="text-align:center;margin-top:6px">
      <h1>欢迎回来 👋</h1>
      <p class="muted" style="margin-top:-4px">登录后随时随地查看你的宠物状态</p>
    </div>
    <form class="form" id="loginForm">
      <label>QQ 号</label>
      <input name="qq" type="text" inputmode="numeric" placeholder="10001" required>
      <label>密码</label>
      <input name="password" type="password" placeholder="●●●●●●" required>
      <button type="submit">登录</button>
      <div class="links"><a href="#" id="toRegister">注册账号</a><a href="#" id="toBind">先绑定宠物</a></div>
    </form>`;
  document.getElementById('loginForm').onsubmit = async e=>{
    e.preventDefault();
    const f = e.target;
    const r = await api('/api/portal/login','POST',{qq:f.qq.value, password:f.password.value});
    if(r && r.ok){ msg('登录成功','ok'); location.reload(); }
    else { msg((r&&r.msg)||'登录失败'); }
  };
  document.getElementById('toRegister').onclick = e=>{e.preventDefault(); viewRegister()};
  document.getElementById('toBind').onclick = e=>{e.preventDefault(); msg('请先登录或注册后再绑定宠物')};
}

function viewRegister(){
  document.body.classList.remove('appmode');
  document.querySelector('.console').classList.remove('wide');
  screen.innerHTML = `
    <div style="text-align:center;margin-top:6px">
      <h1>创建账号</h1>
      <p class="muted" style="margin-top:-4px">注册后即可绑定并管理你的宠物</p>
    </div>
    <form class="form" id="regForm">
      <label>QQ 号</label>
      <input name="qq" type="text" inputmode="numeric" placeholder="10001" required>
      <label>密码</label>
      <input name="password" type="password" placeholder="至少 6 位" required>
      <button type="submit">注册</button>
      <div class="links"><a href="#" id="toLogin">已有账号？登录</a></div>
    </form>`;
  document.getElementById('regForm').onsubmit = async e=>{
    e.preventDefault();
    const f = e.target;
    const r = await api('/api/portal/register','POST',{qq:f.qq.value, password:f.password.value});
    if(r && r.ok){ msg('注册成功，请登录','ok'); viewLogin(); }
    else { msg((r&&r.msg)||'注册失败'); }
  };
  document.getElementById('toLogin').onclick = e=>{e.preventDefault(); viewLogin()};
}

let state = {account:null, pets:[], current:null, data:null};

async function initDashboard(){
  const me = await api('/api/portal/me');
  if(!me || !me.ok){ viewLogin(); return; }
  state.account = me.account;
  state.pets = me.bound_pets || [];
  renderDashboard();
  if(state.pets.length) await loadPet(state.pets[0]);
}

function renderDashboard(){
  document.body.classList.add('appmode');
  document.querySelector('.console').classList.add('wide');
  const chips = state.pets.map((p,i)=>`
    <div class="pet-chip ${state.current && state.current.group_id===p.group_id && state.current.qq===p.qq?'active':''}" data-idx="${i}">
      <img src="${p.image_url || 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'}" alt="">
      <div class="info"><div class="name">${esc(p.nickname)}</div><div class="sub">Lv${p.level} · ${esc(p.quality)}</div></div>
    </div>`).join('');
  screen.innerHTML = `
    <div class="layout">
      <aside class="sidebar">
        <div class="side-brand">宠物乐园 · 玩家中心</div>
        <div class="side-sec">我的宠物</div>
        <div class="side-pets">${chips || '<span class="muted" style="padding:0 8px">暂无绑定宠物</span>'}</div>
        <button id="openBindBtn" class="side-bind">＋ 绑定新宠物</button>
        <p class="muted" style="margin:9px 4px 0;font-size:12px">绑定后可在不同群号 / 用户ID 之间切换查看宠物。</p>
        <div class="side-foot">
          <div class="side-user">QQ ${esc(state.account.qq)}</div>
          <div class="side-foot-btns">
            <button class="ghost" id="pwdOpenBtn">修改密码</button>
            <button class="ghost" id="logoutBtn">退出登录</button>
          </div>
        </div>
      </aside>
      <section class="content">
        <div class="content-inner">
          <div class="topbar">
            <div><h2>宠物档案</h2></div>
          </div>
          <div id="main"></div>
        </div>
      </section>
    </div>`;
  document.querySelectorAll('.pet-chip').forEach(c=>c.onclick=()=>loadPet(state.pets[+c.dataset.idx]));
  document.getElementById('logoutBtn').onclick = async ()=>{ await api('/api/portal/logout','POST'); viewLogin(); };
  document.getElementById('openBindBtn').onclick = openBindModal;
  document.getElementById('pwdOpenBtn').onclick = openPwdModal;
}

function openBindModal(){ document.getElementById('bindModal').classList.add('show'); }
function closeBindModal(){ document.getElementById('bindModal').classList.remove('show'); }

function openPwdModal(){
  ['pwdOld','pwdNew','pwdNew2'].forEach(id=>document.getElementById(id).value='');
  document.getElementById('pwdModal').classList.add('show');
}
function closePwdModal(){ document.getElementById('pwdModal').classList.remove('show'); }
document.getElementById('pwdModal').onclick = e=>{ if(e.target.id==='pwdModal') closePwdModal(); };
document.getElementById('pwdBtn').onclick = async ()=>{
  const oldPwd = document.getElementById('pwdOld').value;
  const newPwd = document.getElementById('pwdNew').value;
  const newPwd2 = document.getElementById('pwdNew2').value;
  if(!oldPwd || !newPwd){ msg('请填写旧密码和新密码'); return; }
  if(newPwd.length < 6){ msg('新密码至少 6 位'); return; }
  if(newPwd !== newPwd2){ msg('两次输入的新密码不一致'); return; }
  const r = await api('/api/portal/change_password','POST',{old_password:oldPwd, new_password:newPwd});
  if(r && r.ok){ msg('密码修改成功','ok'); closePwdModal(); }
  else { msg((r&&r.msg)||'修改失败'); }
};

document.getElementById('bindBtn').onclick = async ()=>{
  const g = document.getElementById('bindGroup').value.trim();
  const q = document.getElementById('bindQQ').value.trim();
  if(!g || !q){ msg('群号和用户 ID 不能为空'); return; }
  const r = await api('/api/portal/bind','POST',{group_id:g, qq:q});
  if(r && r.ok){ msg(r.msg,'ok'); closeBindModal(); document.getElementById('bindGroup').value=''; document.getElementById('bindQQ').value=''; await initDashboard(); }
  else { msg((r&&r.msg)||'绑定失败'); }
};
document.getElementById('bindModal').onclick = e=>{ if(e.target.id==='bindModal') closeBindModal(); };

function openCustomModal(){ document.getElementById('customModal').classList.add('show'); }
function closeCustomModal(){ document.getElementById('customModal').classList.remove('show'); }

let cropState = {img:null, scale:1, x:0, y:0, dragging:false, lastX:0, lastY:0, blob:null};
const cropCanvas = document.getElementById('cropCanvas');
const cropCtx = cropCanvas.getContext('2d');

function drawCrop(){
  if(!cropState.img) return;
  const W = 512, H = 512;
  cropCtx.clearRect(0,0,W,H);
  const img = cropState.img;
  const drawW = img.naturalWidth * cropState.scale;
  const drawH = img.naturalHeight * cropState.scale;
  cropCtx.drawImage(img, cropState.x, cropState.y, drawW, drawH);
}

function resetCrop(){
  cropState.img = null; cropState.scale = 1; cropState.x = 0; cropState.y = 0; cropState.blob = null;
  document.getElementById('customImage').value = '';
  document.getElementById('cropPreview').textContent = '';
}

function openCropper(file){
  const url = URL.createObjectURL(file);
  const img = new Image();
  img.onload = ()=>{
    cropState.img = img;
    // 初始缩放让图片短边填满 512
    const scale = Math.max(512 / img.naturalWidth, 512 / img.naturalHeight);
    cropState.scale = scale;
    cropState.x = (512 - img.naturalWidth * scale) / 2;
    cropState.y = (512 - img.naturalHeight * scale) / 2;
    document.getElementById('cropZoom').value = 100;
    drawCrop();
    document.getElementById('cropModal').classList.add('show');
  };
  img.src = url;
}

function closeCropModal(){ document.getElementById('cropModal').classList.remove('show'); }

document.getElementById('pickImageBtn').onclick = ()=> document.getElementById('customImage').click();
document.getElementById('customImage').onchange = e=>{
  const f = e.target.files[0];
  if(f) openCropper(f);
};
document.getElementById('cropZoom').oninput = e=>{
  if(!cropState.img) return;
  const oldScale = cropState.scale;
  const ratio = (+e.target.value) / 100;
  const base = Math.max(512 / cropState.img.naturalWidth, 512 / cropState.img.naturalHeight);
  cropState.scale = base * ratio;
  // 以画布中心为锚点缩放
  const cx = 256, cy = 256;
  cropState.x = cx - (cx - cropState.x) * (cropState.scale / oldScale);
  cropState.y = cy - (cy - cropState.y) * (cropState.scale / oldScale);
  drawCrop();
};
function cropStartDrag(ex,ey){
  cropState.dragging = true;
  cropState.lastX = ex; cropState.lastY = ey;
  cropCanvas.style.cursor = 'grabbing';
}
function cropMoveDrag(ex,ey){
  if(!cropState.dragging) return;
  cropState.x += ex - cropState.lastX;
  cropState.y += ey - cropState.lastY;
  cropState.lastX = ex; cropState.lastY = ey;
  drawCrop();
}
function cropEndDrag(){ cropState.dragging = false; cropCanvas.style.cursor = 'grab'; }
cropCanvas.addEventListener('mousedown', e=>cropStartDrag(e.offsetX * (512 / cropCanvas.clientWidth), e.offsetY * (512 / cropCanvas.clientHeight)));
cropCanvas.addEventListener('mousemove', e=>cropMoveDrag(e.offsetX * (512 / cropCanvas.clientWidth), e.offsetY * (512 / cropCanvas.clientHeight)));
cropCanvas.addEventListener('mouseup', cropEndDrag);
cropCanvas.addEventListener('mouseleave', cropEndDrag);
cropCanvas.addEventListener('touchstart', e=>{ const t=e.touches[0]; const r=cropCanvas.getBoundingClientRect(); cropStartDrag((t.clientX-r.left)*(512/r.width),(t.clientY-r.top)*(512/r.height)); },{passive:false});
cropCanvas.addEventListener('touchmove', e=>{ e.preventDefault(); const t=e.touches[0]; const r=cropCanvas.getBoundingClientRect(); cropMoveDrag((t.clientX-r.left)*(512/r.width),(t.clientY-r.top)*(512/r.height)); },{passive:false});
cropCanvas.addEventListener('touchend', cropEndDrag);

document.getElementById('saveCropBtn').onclick = ()=>{
  cropCanvas.toBlob(blob=>{
    cropState.blob = blob;
    const url = URL.createObjectURL(blob);
    document.getElementById('cropPreview').innerHTML = `<img src="${url}" alt="预览">`;
    closeCropModal();
  }, 'image/jpeg', 0.92);
};
document.getElementById('cropModal').onclick = e=>{ if(e.target.id==='cropModal') closeCropModal(); };

document.getElementById('submitCustomBtn').onclick = async ()=>{
  if(!state.current){ msg('请先选择宠物'); return; }
  const species = document.getElementById('customSpeciesName').value.trim();
  const file = cropState.blob;
  if(!species && !file){ msg('请至少修改名称或上传图片'); return; }
  const btn = document.getElementById('submitCustomBtn');
  btn.disabled = true; btn.textContent = '提交中…';
  try {
    const fd = new FormData();
    fd.append('group_id', state.current.group_id);
    fd.append('qq', state.current.qq);
    if(species) fd.append('species_name', species);
    if(file) fd.append('image', file, 'custom.jpg');
    const r = await fetch('/api/portal/custom_submit', {
      method:'POST',
      headers:{'X-CSRF-Token':CSRF_TOKEN},
      body:fd
    });
    const data = await r.json().catch(()=>null);
    if(data && data.ok){ msg(data.msg,'ok'); closeCustomModal(); resetCrop(); await loadPet(state.current); }
    else { msg((data&&data.msg)||'提交失败'); }
  } finally {
    btn.disabled = false; btn.textContent = '提交审核';
  }
};
document.getElementById('customModal').onclick = e=>{ if(e.target.id==='customModal') closeCustomModal(); };

async function loadPet(petMeta){
  state.current = petMeta;
  renderDashboard();
  const main = document.getElementById('main');
  main.innerHTML = '<div class="empty">加载中…</div>';
  const d = await api(`/api/portal/pet?group_id=${encodeURIComponent(petMeta.group_id)}&qq=${encodeURIComponent(petMeta.qq)}`);
  if(!d || !d.ok){ main.innerHTML='<div class="empty">加载失败</div>'; return; }
  state.data = d;
  renderPet(main, d);
}

function renderCustom(d){
  const pet = d.pet;
  if(!pet.exists) return '';
  const pending = (d.custom_pending || []).map(r=>`<div class="msg ok">已提交审核，预计 3 个工作日内完成。${r.new.species_name?'名称：'+esc(r.new.species_name):''} ${r.new.image?'图片':''}</div>`).join('');
  const rejected = (d.custom_rejected || []).map(r=>`<div class="msg err">审核未通过：${esc(r.reason||'未说明原因')}</div>`).join('');
  if(!pet.custom){
    return `<div class="custom-box">
      <div class="bind-row">
        <input id="customCode" type="text" placeholder="定制卡密">
      </div>
      <p class="muted" style="margin:8px 0 0">输入宠物定制卡密，解锁后该宠物可修改形象和种类名称，品质将晋升为混沌。</p>
      <div class="sec" style="margin-top:12px">全群祝贺信息</div>
      <div class="bind-row">
        <input id="customNickname" type="text" placeholder="你的 QQ 昵称" style="flex:1">
        <input id="customShowQQ" type="text" inputmode="numeric" placeholder="显示 QQ 号" style="flex:1">
      </div>
      <p class="bind-help">填写昵称和 QQ 号用于解锁后向所有授权群发送祝贺，让全服见证你的专属宠物！</p>
      <button id="redeemCustomBtn" style="margin-top:8px">解锁定制</button>
    </div>`;
  }
  return `<div class="custom-box">
      <div class="custom-badge">✨ 定制权限已解锁（混沌品质）</div>
      <div class="custom-remaining">本月剩余次数：图片 ${d.custom_remaining.image} 次 / 名称 ${d.custom_remaining.species_name} 次</div>
      <button id="editCustomBtn">修改形象 / 名称</button>
      ${pending}${rejected}
    </div>`;
}

function renderPet(container, d){
  const pet = d.pet;
  const petHtml = pet.exists ? `
    <div class="pet-card">
      <img class="pet-img" src="${pet.image_url || 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'}" alt="${esc(pet.custom_species_name || pet.species || '宠物')}">
      <div class="pet-title">
        <div class="name">${esc(pet.nickname||'未命名')} <span style="font-size:14px;color:var(--muted)">Lv${pet.level}</span></div>
        <div class="meta">${esc(pet.custom_species_name || pet.species || '未知')} · ${esc(pet.quality)} · ${esc(pet.stage)} · ${esc(pet.element_cn)}</div>
        ${pet.tags && pet.tags.length ? `<div class="pet-tags">${pet.tags.map(t=>`<span class="pet-tag">${esc(t)}</span>`).join('')}</div>` : ''}
        <div class="pet-resource">${pet.ascended ? `仙元 ${fmt(pet.xianyuan||0)} / ${fmt(pet.exp_to_next||0)}（余 ${fmt(pet.exp||0)} 经验）` : `经验 ${fmt(pet.exp||0)} / ${fmt(pet.exp_to_next||0)}`}</div>
      </div>
      <div class="badges">
        <span class="badge">⚔️ 战力 ${fmt(pet.battle_power)}</span>
        <span class="badge">❤️ 生命 ${fmt(pet.hp||0)}/${fmt(pet.hp_max||0)}</span>
        <span class="badge">⚡ 精力 ${fmt(pet.energy||0)}/${fmt(pet.energy_max||0)}</span>
        <span class="badge">😊 心情 ${fmt(pet.mood||0)}</span>
      </div>
    </div>
    <div class="grid">
      <div class="stat"><div class="label">攻击</div><div class="value">${fmt(pet.atk||0)}</div></div>
      <div class="stat"><div class="label">防御</div><div class="value">${fmt(pet.def||0)}</div></div>
      <div class="stat"><div class="label">智力</div><div class="value">${fmt(pet.intel||0)}</div></div>
      <div class="stat"><div class="label">经验</div><div class="value">${fmt(pet.exp||0)}/${fmt(pet.exp_to_next||0)}</div></div>
      <div class="stat"><div class="label">性别</div><div class="value">${esc(pet.gender||'?')}</div></div>
      <div class="stat"><div class="label">姻缘</div><div class="value">${esc(pet.love_state||'单身')}</div></div>
    </div>`
    : '<div class="empty">该账号下暂无宠物</div>';

  const artSet = new Set(d.artifact_names || []);
  const skillSet = new Set(d.skill_names || []);
  const bag = d.bag && Object.keys(d.bag).length ?
    Object.entries(d.bag).map(([k,v])=>{
      const isArt = artSet.has(k), isSkill = skillSet.has(k);
      const tag = isArt ? '<span class="item-tag art">神器</span>' : (isSkill ? '<span class="item-tag skill">秘技</span>' : '');
      const btnLabel = isArt ? '佩戴' : (isSkill ? '参悟' : '使用');
      const qty = (isArt || isSkill) ? '' : `<input type="number" min="1" max="${v}" value="1" data-qty="${escAttr(k)}">`;
      return `<div class="item">${tag}<div class="item-name">${esc(k)}</div><span class="count">持有 ×${v}</span>
        <div class="use-row">${qty}<button data-use="${escAttr(k)}">${btnLabel}</button></div></div>`;
    }).join('')
    : '<div class="empty">背包空空如也</div>';

  const cds = (d.cooldowns || []).map(c=>{
    const readyAt = Math.floor(Date.now()/1000) + (c.remaining||0);
    return `<div class="cd ${c.remaining>0?'busy':'ready'}" data-ready="${readyAt}"><div class="cd-name">${esc(c.name)}</div><div class="cd-time">${c.remaining>0?fmtCd(c.remaining):'可用'}</div></div>`;
  }).join('') || '<div class="empty">暂无活动</div>';

  container.innerHTML = petHtml + renderCustom(d) + `
    <h3>我的财产</h3>
    <div class="wallet">
      <div class="coin"><div class="label">🪙 金币</div><div class="value">${fmt(d.coin)}</div></div>
      <div class="coin"><div class="label">✨ 积分</div><div class="value">${fmt(d.jifen)}</div></div>
      <div class="coin"><div class="label">💎 钻石</div><div class="value">${fmt(d.diamond)}</div></div>
      <div class="coin"><div class="label">🔮 深渊结晶</div><div class="value">${fmt(d.abyss.crystal||0)}</div></div>
    </div>
    <h3>活动冷却</h3>
    <div class="cd-grid">${cds}</div>
    <h3>背包</h3>
    <p class="muted" style="margin:-4px 0 10px">道具可直接使用（支持数量），神器可佩戴、秘技书可参悟，效果与群聊指令一致。</p>
    <div class="bag">${bag}</div>
    <h3>卡密兑换</h3>
    <div class="redeem-box">
      <div class="bind-row">
        <input id="redeemCode" type="text" placeholder="输入卡密，可兑换金币 / 积分 / 钻石 / 道具">
        <button id="redeemBtn">兑换</button>
      </div>
      <div id="redeemResult" class="redeem-result"></div>
    </div>
    <div class="pet-source">群号：${esc(d.group_id)} &nbsp;|&nbsp; 用户ID：${esc(d.qq)}</div>`;

  container.querySelectorAll('button[data-use]').forEach(btn=>{
    btn.onclick = async ()=>{
      const name = btn.dataset.use;
      const qtyInput = container.querySelector(`input[data-qty="${CSS.escape(name)}"]`);
      const count = qtyInput ? Math.max(1, parseInt(qtyInput.value)||1) : 1;
      btn.disabled = true;
      try{
        const r = await api('/api/portal/use_item','POST',{group_id:d.group_id, qq:d.qq, name, count});
        if(r && r.msg) msg(stripMd(r.msg), r.ok?'ok':'err');
        else msg('使用失败');
        if(r && r.ok) await refreshAll();
      } finally { btn.disabled = false; }
    };
  });
  const redeemBtn2 = document.getElementById('redeemBtn');
  if(redeemBtn2){
    redeemBtn2.onclick = async ()=>{
      const code = document.getElementById('redeemCode').value.trim();
      if(!code){ msg('请输入卡密'); return; }
      redeemBtn2.disabled = true;
      try{
        const r = await api('/api/portal/redeem','POST',{group_id:d.group_id, qq:d.qq, code});
        const box = document.getElementById('redeemResult');
        if(r && r.msg){ box.textContent = stripMd(r.msg); box.classList.add('show'); }
        if(r && r.ok){ msg('兑换成功','ok'); await refreshAll(); }
        else if(r){ msg(stripMd(r.msg||'兑换失败')); }
      } finally { redeemBtn2.disabled = false; }
    };
  }
  startCdTicker();
  const redeemBtn = document.getElementById('redeemCustomBtn');
  if(redeemBtn){
    redeemBtn.onclick = async ()=>{
      const code = document.getElementById('customCode').value.trim();
      const nickname = document.getElementById('customNickname').value.trim();
      const showQQ = document.getElementById('customShowQQ').value.trim();
      if(!code){ msg('请输入定制卡密'); return; }
      if(!nickname || !showQQ){ msg('请填写昵称和 QQ 号，用于全群祝贺'); return; }
      const r = await api('/api/portal/custom_redeem','POST',{group_id:d.group_id, qq:d.qq, code, nickname, show_qq:showQQ});
      if(r && r.ok){ msg(r.msg,'ok'); await loadPet(state.current); }
      else { msg((r&&r.msg)||'解锁失败'); }
    };
  }
  const editBtn = document.getElementById('editCustomBtn');
  if(editBtn){
    editBtn.onclick = ()=>{
      resetCrop();
      document.getElementById('customSpeciesName').value = d.pet.custom_species_name || d.pet.species || '';
      document.getElementById('customModal').classList.add('show');
    };
  }
}

function esc(s){ return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function escAttr(s){ return esc(s); }
function fmt(n){ return Number(n).toLocaleString('zh-CN'); }
function stripMd(s){ return String(s).replace(/^#+\s*/gm,'').replace(/\*\*/g,'').replace(/`/g,'').replace(/━+/g,'').replace(/\n{3,}/g,'\n\n').trim(); }
function fmtCd(sec){
  sec = Math.max(0, Math.floor(sec));
  if(sec >= 3600) return `${Math.floor(sec/3600)}时${Math.floor(sec%3600/60)}分`;
  if(sec >= 60) return `${Math.floor(sec/60)}分${sec%60}秒`;
  return `${sec}秒`;
}
let cdTimer = null;
function startCdTicker(){
  if(cdTimer) clearInterval(cdTimer);
  cdTimer = setInterval(()=>{
    document.querySelectorAll('.cd[data-ready]').forEach(el=>{
      const remaining = (+el.dataset.ready) - Math.floor(Date.now()/1000);
      const t = el.querySelector('.cd-time');
      if(remaining > 0){ el.classList.add('busy'); el.classList.remove('ready'); t.textContent = fmtCd(remaining); }
      else { el.classList.remove('busy'); el.classList.add('ready'); t.textContent = '可用'; }
    });
  }, 1000);
}
async function refreshAll(){
  // 全局刷新：重新拉取账号绑定列表与当前宠物数据
  const me = await api('/api/portal/me');
  if(me && me.ok){ state.account = me.account; state.pets = me.bound_pets || []; }
  if(state.current) await loadPet(state.current);
}

initDashboard();
</script>
</body>
</html>
"""
