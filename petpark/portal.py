"""玩家门户：注册 / 登录 / 绑定宠物后，按群聊+用户ID查看宠物、背包、财产信息。

安全设计：
- 密码使用 PBKDF2-HMAC-SHA256 + 随机 salt 存储
- 会话采用 HMAC-SHA256 签名 Cookie，HttpOnly + SameSite=Strict
- POST 接口校验 CSRF token
- 登录/注册/绑定接口有简单的 IP+QQ 级速率限制
- 当前只读，不提供任何修改数据的能力
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
    def __init__(self, store, broadcast_callback=None):
        self.store = store
        self.broadcast_callback = broadcast_callback
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
            "custom_pending": pending,
            "custom_rejected": last_rejected,
            "custom_remaining": {
                "image": self.store.remaining_custom_changes(player, "image"),
                "species_name": self.store.remaining_custom_changes(player, "species_name"),
            },
        }

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


# --------------------------- 前端页面 ---------------------------
# 复古掌机风格：深色机身 + 琥珀色 LCD 屏幕 + 像素字体
_PORTAL_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>宠物乐园 · 玩家中心</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&family=ZCOOL+KuaiLe&display=swap" rel="stylesheet">
<style>
:root{
  --case:#0d0d0d;
  --case-light:#1a1a1a;
  --lcd:#1c1b00;
  --lcd-on:#2a2100;
  --amber:#ffb000;
  --amber-dim:#b87d00;
  --amber-glow:#ffcc4d;
  --screen:#c4b14a;
  --screen-dim:#9e8f2e;
  --danger:#ff4d4d;
  --ok:#3ddc84;
  --text:#f2f2f2;
  --muted:#888;
}
*{box-sizing:border-box}
html,body{height:100%;margin:0;background:radial-gradient(circle at 50% 30%,#1a1505 0%,#0d0d0d 70%);color:var(--text);font-family:'ZCOOL KuaiLe','Microsoft YaHei',sans-serif;overflow-x:hidden;overflow-y:auto}
#app{display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}

/* 掌机外壳 */
.console{width:100%;max-width:480px;background:linear-gradient(145deg,#181818,#0d0d0d);border-radius:36px;padding:28px 22px 34px;box-shadow:0 30px 80px rgba(0,0,0,.7),inset 0 1px 0 rgba(255,255,255,.06),0 0 0 4px #111;position:relative;transition:max-width .3s ease,padding .3s ease}
.console.wide{max-width:min(1100px,100% - 40px)}
.console.wide .screen{min-height:min(70vh,600px);padding:22px}
.console::before{content:'';position:absolute;inset:10px;border-radius:28px;border:1px solid rgba(255,255,255,.04);pointer-events:none}
.brand{text-align:center;font-family:'Press Start 2P',cursive;font-size:12px;color:var(--amber-dim);letter-spacing:2px;margin-bottom:14px;text-shadow:0 0 8px rgba(255,176,0,.25)}

/* LCD 屏幕 */
.screen-wrap{background:#050505;border-radius:18px;padding:18px 16px 22px;box-shadow:inset 0 0 18px rgba(0,0,0,.9),0 1px 0 rgba(255,255,255,.04)}
.screen{position:relative;background:var(--lcd);border-radius:10px;min-height:320px;overflow:hidden auto;box-shadow:inset 0 0 40px rgba(0,0,0,.6);padding:18px}
.console.wide .screen{min-height:0;max-height:min(760px,calc(90vh - 120px))}
.screen::after{content:'';position:absolute;inset:0;background:repeating-linear-gradient(0deg,rgba(0,0,0,.18) 0 1px,transparent 1px 3px);pointer-events:none;z-index:10}
.screen.on{background:var(--lcd-on)}

/* 通用排版 */
h1,h2,h3{margin:0 0 12px;font-weight:400}
h1{font-size:22px;color:var(--amber);text-shadow:0 0 10px rgba(255,176,0,.4)}
h2{font-size:18px;color:var(--amber-glow)}
h3{font-size:15px;color:var(--amber-dim)}
.muted{color:var(--muted);font-size:13px}

/* 表单 */
.form{display:flex;flex-direction:column;gap:12px;animation:fadeIn .6s ease both}
label{font-size:13px;color:var(--amber-dim)}
input,button,select{font-family:inherit;border:none;outline:none;border-radius:8px}
input,select{background:rgba(0,0,0,.45);border:1px solid rgba(255,176,0,.2);color:var(--text);padding:12px 14px;font-size:15px;transition:.2s}
input:focus,select:focus{border-color:var(--amber);box-shadow:0 0 10px rgba(255,176,0,.15)}
button{cursor:pointer;background:var(--amber);color:#1a1200;font-weight:700;padding:12px 16px;font-size:15px;transition:.15s;box-shadow:0 4px 0 var(--amber-dim)}
button:active{transform:translateY(3px);box-shadow:0 1px 0 var(--amber-dim)}
button.ghost{background:transparent;color:var(--amber);border:1px solid var(--amber-dim);box-shadow:none}
button.ghost:active{transform:none}
button:disabled{opacity:.5;cursor:not-allowed;box-shadow:none;transform:none}
.links{display:flex;justify-content:space-between;margin-top:8px}
.links a{color:var(--amber);text-decoration:none;font-size:13px}
.links a:hover{text-decoration:underline}

/* 消息 */
.msg{padding:10px 12px;border-radius:8px;font-size:14px;margin-bottom:10px;animation:slideDown .3s ease}
.msg.err{background:rgba(255,77,77,.12);color:var(--danger);border:1px solid rgba(255,77,77,.25)}
.msg.ok{background:rgba(61,220,132,.12);color:var(--ok);border:1px solid rgba(61,220,132,.25)}

/* 仪表盘 */
.dashboard{display:none;animation:fadeIn .7s ease both}
.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;flex-wrap:wrap;gap:8px}
.account{font-size:13px;color:var(--muted)}
.pet-selector{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin-bottom:14px}
.pet-chip{background:rgba(0,0,0,.4);border:1px solid rgba(255,176,0,.15);border-radius:12px;padding:8px 12px;cursor:pointer;transition:.2s;display:flex;align-items:center;gap:8px;min-width:0}
.pet-chip:hover,.pet-chip.active{border-color:var(--amber);background:rgba(255,176,0,.08)}
.pet-chip img{width:36px;height:36px;border-radius:50%;object-fit:cover;background:#000;flex:0 0 auto}
.pet-chip .info{line-height:1.2;min-width:0}
.pet-chip .name{font-size:14px;color:var(--amber-glow);word-break:break-all;overflow-wrap:anywhere}
.pet-chip .sub{font-size:11px;color:var(--muted);word-break:break-all}

/* 宠物卡片 */
.pet-card{display:flex;flex-direction:column;align-items:center;background:rgba(255,176,0,.05);border:1px solid rgba(255,176,0,.2);border-radius:16px;padding:16px;margin-bottom:14px;position:relative;overflow:hidden}
.pet-card::before{content:'';position:absolute;top:-40px;right:-40px;width:120px;height:120px;background:radial-gradient(circle,rgba(255,176,0,.12),transparent 70%);pointer-events:none}
.pet-img{width:160px;height:160px;border-radius:14px;object-fit:cover;background:#000;border:2px solid var(--amber-dim);box-shadow:0 0 20px rgba(255,176,0,.15);animation:popIn .5s ease both}
.pet-title{margin-top:12px;text-align:center}
.pet-title .name{font-size:22px;color:var(--amber-glow)}
.pet-title .meta{font-size:13px;color:var(--muted);margin-top:4px}
.pet-tags{display:flex;gap:6px;flex-wrap:wrap;justify-content:center;margin-top:6px}
.pet-tag{font-size:11px;background:linear-gradient(135deg,rgba(255,176,0,.25),rgba(255,120,0,.15));color:var(--amber);border:1px solid rgba(255,176,0,.35);border-radius:999px;padding:2px 8px}
.badges{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap;justify-content:center}
.badge{font-size:12px;background:rgba(0,0,0,.45);padding:4px 10px;border-radius:20px;border:1px solid rgba(255,176,0,.2);color:var(--amber)}

/* 属性网格 */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:10px;margin-bottom:14px}
.stat{background:rgba(0,0,0,.35);border:1px solid rgba(255,176,0,.1);border-radius:10px;padding:10px 12px}
.stat .label{font-size:12px;color:var(--muted)}
.stat .value{font-size:18px;color:var(--amber-glow);margin-top:2px}

/* 财产 */
.wallet{display:grid;grid-template-columns:repeat(auto-fit,minmax(90px,1fr));gap:10px;margin-bottom:14px}
.coin{text-align:center;background:rgba(0,0,0,.35);border:1px solid rgba(255,176,0,.1);border-radius:10px;padding:12px 6px}
.coin .label{font-size:11px;color:var(--muted)}
.coin .value{font-size:16px;color:var(--amber-glow);margin-top:4px;word-break:break-all}

/* 背包 */
.bag{display:grid;grid-template-columns:repeat(auto-fill,minmax(95px,1fr));gap:8px;max-height:280px;overflow-y:auto;padding-right:4px}
.item{background:rgba(0,0,0,.35);border:1px solid rgba(255,176,0,.1);border-radius:10px;padding:10px 6px;text-align:center;font-size:13px;color:var(--text)}
.item .count{display:block;margin-top:4px;color:var(--amber);font-weight:700}
.empty{text-align:center;color:var(--muted);padding:30px 0;font-size:14px}

/* 绑定表单 */
.bind-box{margin-top:14px;padding:14px;background:rgba(0,0,0,.3);border-radius:12px;border:1px dashed rgba(255,176,0,.25)}
.bind-box h3{margin-bottom:10px}
.bind-row{display:flex;flex-wrap:wrap;gap:8px}
.bind-row input{flex:1 1 120px;min-width:0}
.bind-row button{flex:0 0 auto}
.bind-help{margin-top:8px;font-size:12px;color:var(--muted);line-height:1.5}
.bind-open{text-align:center;margin-top:14px}

/* 弹窗 */
.modal{position:fixed;inset:0;background:rgba(0,0,0,.75);display:none;align-items:center;justify-content:center;z-index:50;padding:20px}
.modal.show{display:flex}
.modal .sheet{background:linear-gradient(145deg,#181818,#0d0d0d);border:1px solid rgba(255,176,0,.25);border-radius:18px;padding:22px;width:min(440px,100%);box-shadow:0 24px 70px rgba(0,0,0,.85)}
.modal .sheet h3{color:var(--amber);margin:0 0 14px}
.modal .sheet .bind-row{margin-bottom:10px}
.modal .sheet .actions{display:flex;gap:10px;justify-content:flex-end;margin-top:14px}
/* 定制弹窗美化 */
.custom-sheet{width:min(480px,100%)}
.modal-head{text-align:center;margin-bottom:18px}
.modal-icon{font-size:36px;line-height:1;margin-bottom:6px}
.modal-head h3{margin:0 0 6px}
.modal-sub{font-size:13px;color:var(--muted);margin:0}
.upload-zone{border:2px dashed rgba(255,176,0,.35);border-radius:14px;padding:22px;text-align:center;cursor:pointer;transition:.2s;background:rgba(255,176,0,.05);margin-bottom:12px}
.upload-zone:hover{border-color:rgba(255,176,0,.65);background:rgba(255,176,0,.1)}
.upload-plus{font-size:32px;color:var(--amber);line-height:1;margin-bottom:4px}
.upload-text{font-size:15px;color:var(--text);margin-bottom:4px}
.upload-hint{font-size:12px;color:var(--muted)}
.crop-preview{margin-bottom:12px;text-align:center}
.crop-preview img{max-width:160px;max-height:160px;border-radius:10px;border:1px solid rgba(255,176,0,.25)}
.pet-source{margin-top:12px;text-align:center;color:var(--muted);font-size:12px;word-break:break-all}
.custom-box{margin-top:14px;padding:14px;background:rgba(0,0,0,.3);border-radius:12px;border:1px dashed rgba(255,176,0,.25)}
.custom-badge{color:var(--amber-glow);font-size:14px;margin-bottom:8px}
.custom-remaining{font-size:12px;color:var(--muted);margin-bottom:10px}
input[type="file"]{padding:10px;background:rgba(0,0,0,.45);border:1px solid rgba(255,176,0,.2);color:var(--text);border-radius:8px;width:100%}

/* 动画 */
@keyframes fadeIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
@keyframes slideDown{from{opacity:0;transform:translateY(-8px)}to{opacity:1;transform:translateY(0)}}
@keyframes popIn{0%{transform:scale(.85);opacity:0}80%{transform:scale(1.03)}100%{transform:scale(1);opacity:1}}

@media(max-width:420px){
  .console{border-radius:24px;padding:20px 16px 26px}
  .screen{min-height:360px}
  .pet-img{width:130px;height:130px}
  .wallet{grid-template-columns:repeat(2,1fr)}
}
</style>
</head>
<body>
<div id="app">
  <div class="console">
    <div class="brand">◈ PET-BOY ADVANCE ◈</div>
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
  screen.prepend(d);
  setTimeout(()=>d.remove(), 4000);
}

function viewLogin(){
  document.querySelector('.console').classList.remove('wide');
  screen.innerHTML = `
    <h1>玩家中心</h1>
    <p class="muted">绑定你的 QQ 宠物，随时随地查看状态</p>
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
  document.querySelector('.console').classList.remove('wide');
  screen.innerHTML = `
    <h1>注册账号</h1>
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
  document.querySelector('.console').classList.add('wide');
  const chips = state.pets.map((p,i)=>`
    <div class="pet-chip ${state.current && state.current.group_id===p.group_id && state.current.qq===p.qq?'active':''}" data-idx="${i}">
      <img src="${p.image_url || 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'}" alt="">
      <div class="info"><div class="name">${esc(p.nickname)}</div><div class="sub">Lv${p.level} · ${esc(p.quality)}</div></div>
    </div>`).join('');
  screen.innerHTML = `
    <div class="dashboard" style="display:block">
      <div class="topbar">
        <div><h2>宠物档案</h2></div>
        <div class="account">QQ: ${esc(state.account.qq)} <button class="ghost" id="logoutBtn" style="padding:6px 10px;font-size:12px">退出</button></div>
      </div>
      <div class="pet-selector">${chips || '<span class="muted">暂无绑定宠物</span>'}</div>
      <div id="main"></div>
      <div class="bind-open">
        <button id="openBindBtn" style="padding:12px 22px">＋ 绑定新宠物</button>
        <p class="muted" style="margin:8px 0 0">绑定后可在不同群号 / 用户ID 之间切换查看宠物。</p>
      </div>
    </div>`;
  document.querySelectorAll('.pet-chip').forEach(c=>c.onclick=()=>loadPet(state.pets[+c.dataset.idx]));
  document.getElementById('logoutBtn').onclick = async ()=>{ await api('/api/portal/logout','POST'); viewLogin(); };
  document.getElementById('openBindBtn').onclick = openBindModal;
}

function openBindModal(){ document.getElementById('bindModal').classList.add('show'); }
function closeBindModal(){ document.getElementById('bindModal').classList.remove('show'); }

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
      </div>
      <div class="badges">
        <span class="badge">战力 ${fmt(pet.battle_power)}</span>
        <span class="badge">生命 ${fmt(pet.hp||0)}/${fmt(pet.hp_max||0)}</span>
        <span class="badge">精力 ${fmt(pet.energy||0)}/${fmt(pet.energy_max||0)}</span>
        <span class="badge">心情 ${fmt(pet.mood||0)}★</span>
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

  const bag = d.bag && Object.keys(d.bag).length ?
    Object.entries(d.bag).map(([k,v])=>`<div class="item">${esc(k)}<span class="count">×${v}</span></div>`).join('')
    : '<div class="empty">背包空空如也</div>';

  container.innerHTML = petHtml + renderCustom(d) + `
    <h3>我的财产</h3>
    <div class="wallet">
      <div class="coin"><div class="label">金币</div><div class="value">${fmt(d.coin)}</div></div>
      <div class="coin"><div class="label">积分</div><div class="value">${fmt(d.jifen)}</div></div>
      <div class="coin"><div class="label">钻石</div><div class="value">${fmt(d.diamond)}</div></div>
      <div class="coin"><div class="label">深渊结晶</div><div class="value">${fmt(d.abyss.crystal||0)}</div></div>
    </div>
    <h3>背包</h3>
    <div class="bag">${bag}</div>
    <div class="pet-source">群号：${esc(d.group_id)} &nbsp;|&nbsp; 用户ID：${esc(d.qq)}</div>`;
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
function fmt(n){ return Number(n).toLocaleString('zh-CN'); }

initDashboard();
</script>
</body>
</html>
"""
