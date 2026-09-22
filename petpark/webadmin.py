"""灵契仙途专属管理网站。

在 AstrBot 进程内启动一个独立端口的 aiohttp 网站，提供：
- 账号密码登录（默认 admin / 2468080asd，可在插件配置修改）；
- 查看 / 增删改查 插件数据库（玩家 players、群设置 groups、卡密 cards）；
- 批量生成卡密（灵石 / 玄晶 / 天晶）。

依赖 aiohttp（AstrBot 自带）。启动失败不会影响插件主体功能。
"""

from __future__ import annotations

import copy
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger

from .portal import PlayerPortal
from .admin_paging import paginate
from .zhongyuan.config import ACTIVITY_NAME

COOKIE = "pp_session"
TABLES = ("players", "groups", "cards", "events")


class WebAdmin:
    def __init__(
        self,
        store,
        host: str,
        port: int,
        user: str,
        password: str,
        broadcast_callback=None,
        command_gateway=None,
        zhongyuan=None,
        silk_dir=None,
    ):
        self.store = store
        self.host = host
        self.port = int(port)
        self.user = user
        self.password = password
        self._broadcast_callback = broadcast_callback
        self._command_gateway = command_gateway
        self.zhongyuan = zhongyuan  # 中元活动引擎（可为 None = 模块未加载）
        self._tokens: set[str] = set()
        self._runner = None
        # 点歌 silk 临时目录：供 QQ 外部拉取语音文件；文件名走白名单，仅临时存在。
        self.silk_dir = Path(silk_dir) if silk_dir else None
        if self.silk_dir is not None:
            self.silk_dir.mkdir(parents=True, exist_ok=True)

    # --------------------------- 生命周期 ---------------------------
    async def start(self) -> None:
        from aiohttp import web

        app = web.Application(client_max_size=200 * 1024 * 1024)
        app.router.add_get("/admin", self._index)
        app.router.add_get("/login", self._login_page)
        app.router.add_post("/login", self._login_submit)
        app.router.add_get("/logout", self._logout)
        app.router.add_post("/api/list", self._api_list)
        app.router.add_post("/api/meta", self._api_meta)
        app.router.add_post("/api/upsert", self._api_upsert)
        app.router.add_post("/api/delete", self._api_delete)
        app.router.add_post("/api/cards/generate", self._api_gen_cards)
        app.router.add_post("/api/cards/batch_delete", self._api_cards_batch_delete)
        app.router.add_post("/api/boss_respawn", self._api_boss_respawn)
        app.router.add_get("/api/portal_accounts", self._api_portal_accounts)
        app.router.add_post("/api/portal_accounts/reset_password", self._api_portal_reset_password)
        app.router.add_post("/api/portal_accounts/delete", self._api_portal_delete_account)
        app.router.add_post("/api/portal_accounts/unbind", self._api_portal_unbind)
        app.router.add_post("/api/custom_reviews", self._api_custom_reviews)
        app.router.add_post("/api/custom_reviews/approve", self._api_custom_review_approve)
        app.router.add_post("/api/custom_reviews/reject", self._api_custom_review_reject)
        app.router.add_post("/api/custom_pets", self._api_custom_pets)
        app.router.add_post("/api/custom_pets/cancel", self._api_custom_pet_cancel)
        app.router.add_post("/api/custom_mounts", self._api_custom_mounts)
        app.router.add_post("/api/custom_mounts/set_image", self._api_custom_mount_set_image)
        app.router.add_post("/api/feedbacks", self._api_feedbacks)
        app.router.add_post("/api/feedbacks/reply", self._api_feedback_reply)
        app.router.add_post("/api/feedbacks/delete", self._api_feedback_delete)
        app.router.add_post("/api/app_release/info", self._api_app_release_info)
        app.router.add_post("/api/app_release/upload", self._api_app_release_upload)
        app.router.add_post("/api/lottery/state", self._api_lottery_state)
        app.router.add_post("/api/lottery/save", self._api_lottery_save)
        app.router.add_post("/api/lottery/draw", self._api_lottery_draw)
        app.router.add_post("/api/zhongyuan/config", self._api_zhongyuan_config)
        app.router.add_post("/api/zhongyuan/config/save", self._api_zhongyuan_config_save)
        app.router.add_post("/api/zhongyuan/test_deepseek", self._api_zhongyuan_test_deepseek)
        app.router.add_post("/api/zhongyuan/test_broadcast", self._api_zhongyuan_test_broadcast)
        app.router.add_post("/api/zhongyuan/test_start", self._api_zhongyuan_test_start)
        app.router.add_post("/api/zhongyuan/test_end", self._api_zhongyuan_test_end)
        app.router.add_post("/api/zhongyuan/data", self._api_zhongyuan_data)
        app.router.add_post("/api/zhongyuan/clear_data", self._api_zhongyuan_clear_data)
        app.router.add_post("/api/push/state", self._api_push_state)
        app.router.add_post("/api/push/manual", self._api_push_manual)
        app.router.add_post("/api/push/save", self._api_push_save)
        app.router.add_post("/api/push/toggle", self._api_push_toggle)
        app.router.add_post("/api/push/fire", self._api_push_fire)
        app.router.add_post("/api/push/delete", self._api_push_delete)
        app.router.add_post("/api/celebrate/state", self._api_celebrate_state)
        app.router.add_post("/api/celebrate/save", self._api_celebrate_save)
        app.router.add_post("/api/celebrate/reset_pool", self._api_celebrate_reset_pool)
        app.router.add_post("/api/celebrate/reset_stock", self._api_celebrate_reset_stock)
        app.router.add_post("/api/celebrate/broadcast", self._api_celebrate_broadcast)
        app.router.add_post("/api/assistant_free/state", self._api_assistant_free_state)
        app.router.add_post("/api/assistant_free/save", self._api_assistant_free_save)
        app.router.add_post("/api/audit/query", self._api_audit_query)
        app.router.add_post("/api/audit/flags", self._api_audit_flags)
        app.router.add_post("/api/audit/scan", self._api_audit_scan)
        app.router.add_post("/api/audit/flag/update", self._api_audit_flag_update)
        app.router.add_post("/api/audit/verify", self._api_audit_verify)
        app.router.add_get("/api/song_silk/{name}", self._api_song_silk)
        # 后台审核图片：以进程内 file read 直接返回二进制，绕开 /custom_images 静态路由
        # （framework 容器化运行后 host 与容器文件视图可能不一致，需 in-process 提供）
        app.router.add_get("/api/admin/image", self._api_admin_image)

        portal = PlayerPortal(
            self.store,
            broadcast_callback=self._broadcast_callback,
            command_gateway=self._command_gateway,
        )
        portal.setup(app)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        self._runner = runner
        logger.info(
            f"[petpark] 管理网站已启动: http://{self.host}:{self.port} "
            f"(账号 {self.user})"
        )

    # ---- 点歌 silk 临时文件下载（供 QQ 拉取；无鉴权，仅服务白名单临时目录）----
    async def _api_admin_image(self, request):
        """后台审核页图片：进程内读取 store.custom_images_dir 文件并直接返回二进制。
        避开 /custom_images 静态路由（容器化/挂载视图差异下不可靠）。
        """
        self._require(request)
        from aiohttp import web
        fn = str(request.query.get("file", "")).strip()
        if not fn:
            return web.Response(status=404)
        # 拒绝路径穿越
        if "/" in fn or ".." in fn:
            return web.Response(status=400, text="bad filename")
        try:
            p = self.store.custom_image_path(fn)
            if not p.exists():
                # 占位：返回一张 SVG 提示「图片缺失」，前端 <img onerror> 也会兜底
                svg = (
                    '<svg xmlns="http://www.w3.org/2000/svg" width="420" height="240">'
                    '<rect width="100%" height="100%" fill="repeating-linear-gradient(45deg,#efece1,#efece1 12px,#f7f5eb 12px,#f7f5eb 24px)"/>'
                    '<text x="50%" y="46%" font-size="16" text-anchor="middle" fill="#4f5c50">📂 图片缺失</text>'
                    '<text x="50%" y="64%" font-size="11" text-anchor="middle" fill="#6b766c">该定制图文件可能已被清理；建议让玩家重新提交。</text>'
                    '</svg>')
                return web.Response(body=svg, content_type="image/svg+xml")
            data = p.read_bytes()
        except OSError:
            return web.Response(status=404)
        ext = p.suffix.lower()
        mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".gif": "image/gif",
                ".webp": "image/webp"}.get(ext, "application/octet-stream")
        return web.Response(body=data, content_type=mime,
                            headers={"Cache-Control": "no-cache"})

    async def _api_song_silk(self, request) -> Any:
        from aiohttp import web

        if self.silk_dir is None:
            return web.Response(status=404)
        name = request.match_info.get("name", "")
        # 文件名白名单：32 位十六进制 + .silk，杜绝路径穿越
        if not re.fullmatch(r"[0-9a-f]{32}\.silk", name):
            return web.Response(status=404)
        self._purge_expired_silk()
        path = self.silk_dir / name
        if not path.is_file():
            return web.Response(status=404)
        if time.time() - path.stat().st_mtime > 300:
            try:
                path.unlink()
            except Exception:
                pass
            return web.Response(status=404)
        return web.FileResponse(
            path, headers={"Content-Type": "application/octet-stream"}
        )

    def _purge_expired_silk(self) -> None:
        """清理 silk 目录里超过 5 分钟未访问的临时文件。"""
        if self.silk_dir is None:
            return
        cutoff = time.time() - 300
        try:
            for f in self.silk_dir.glob("*.silk"):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                except Exception:
                    pass
        except Exception:
            pass

    async def stop(self) -> None:
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            except Exception:
                pass
            self._runner = None

    # --------------------------- 鉴权 ---------------------------
    def _authed(self, request) -> bool:
        return request.cookies.get(COOKIE) in self._tokens

    def _require(self, request):
        from aiohttp import web

        if not self._authed(request):
            raise web.HTTPFound("/login")

    async def _login_page(self, request):
        from aiohttp import web

        return web.Response(text=LOGIN_HTML, content_type="text/html")

    async def _login_submit(self, request):
        from aiohttp import web

        data = await request.post()
        if (
            data.get("user") == self.user
            and data.get("password") == self.password
        ):
            token = secrets.token_hex(16)
            self._tokens.add(token)
            resp = web.HTTPFound("/admin")
            resp.set_cookie(COOKIE, token, httponly=True, max_age=86400)
            return resp
        return web.Response(
            text=LOGIN_HTML.replace("<!--ERR-->", "账号或密码错误"),
            content_type="text/html",
        )

    async def _logout(self, request):
        from aiohttp import web

        tok = request.cookies.get(COOKIE)
        self._tokens.discard(tok)
        resp = web.HTTPFound("/login")
        resp.del_cookie(COOKIE)
        return resp

    async def _index(self, request):
        from aiohttp import web

        self._require(request)
        return web.Response(text=DASHBOARD_HTML, content_type="text/html")

    # --------------------------- API ---------------------------
    @staticmethod
    def _table(name: str) -> str:
        if name not in TABLES:
            from aiohttp import web

            raise web.HTTPBadRequest(text="未知数据表")
        return name

    def _json(self, payload: Any):
        from aiohttp import web

        return web.json_response(payload, dumps=lambda o: json.dumps(o, ensure_ascii=False))

    async def _api_list(self, request):
        self._require(request)
        body = await request.json()
        table = self._table(body.get("table", ""))
        records = self.store._data.get(table, {})
        # Fetch just one fresh record for optimistic-lock editing.
        if "key" in body:
            key = str(body["key"])
            return self._json({"ok": True, "data": {key: records[key]} if key in records else {}})
        # Export intentionally spans every page, independent of current search.
        if table == "cards" and body.get("export") == "unused":
            return self._json({"ok": True, "data": {k: v for k, v in records.items() if not v.get("used")}})
        result = paginate(records, body)
        if table == "cards":
            used = sum(bool(v.get("used")) for v in records.values())
            result["stats"] = {"total": len(records), "used": used}
        return self._json(result)

    async def _api_meta(self, request):
        """返回各类枚举值，供前端编辑表单渲染下拉框。"""
        self._require(request)
        from . import data

        return self._json(
            {
                "ok": True,
                "data": {
                    "species": list(data.SPECIES.keys()),
                    "qualities": list(data.QUALITIES),
                    "elements": list(data.ELEMENTS),
                    "genders": ["男", "女"],
                    "stages": list(data.STAGES),
                    "statuses": list(data.STATUSES),
                    "love_states": list(data.LOVE_STATES),
                    "artifacts": list(data.ARTIFACTS.keys()),
                    "talents": list(data.TALENTS.keys()),
                    "skills": list(data.SKILLS.keys()),
                    "items": list(data.ITEMS.keys()),
                    "currencies": ["金币", "积分", "钻石"],
                },
            }
        )

    @staticmethod
    def _merge_edits(base: dict, value: dict, existing: dict) -> dict:
        """3 路合并：把 base→value 的管理员编辑应用到最新 existing 上。

        宠物/玩家数据会随玩家实时变化（精力、经验、冷却、血量等），
        若整条指纹比对或直接替换，后台一保存就判定"数据已更新"而失败，
        且旧快照整条覆盖还会把玩家最新进度冲掉（回溯）。
        因此改为：管理员未改动的字段保留 existing 的实时值，
        管理员显式修改的字段以 value 为准，嵌套对象递归合并。
        """
        result: dict = {}
        for k in set(base) | set(value) | set(existing):
            if k in base and k not in value:
                continue  # 管理员删除了该字段
            if k not in base:
                result[k] = value[k] if k in value else existing[k]  # 新增字段/防御保留
                continue
            b, v = base[k], value[k]
            if k not in existing:
                result[k] = v
            elif v == b:
                result[k] = existing[k]  # 未修改 → 保留实时数据
            elif isinstance(b, dict) and isinstance(v, dict) \
                    and isinstance(existing[k], dict):
                result[k] = WebAdmin._merge_edits(b, v, existing[k])
            elif isinstance(b, list) and isinstance(v, list) \
                    and isinstance(existing[k], list) and len(v) == len(b):
                # 长度相同的列表（如多宠物 pets）：逐元素合并，
                # 未改动的元素保留实时值，避免编辑单个宠物时覆盖其他宠物进度
                e_list = existing[k]
                merged_list = []
                for i, vi in enumerate(v):
                    bi = b[i]
                    if vi == bi:
                        merged_list.append(e_list[i] if i < len(e_list) else vi)
                    elif isinstance(bi, dict) and isinstance(vi, dict) \
                            and i < len(e_list) and isinstance(e_list[i], dict):
                        merged_list.append(WebAdmin._merge_edits(bi, vi, e_list[i]))
                    else:
                        merged_list.append(vi)
                result[k] = merged_list
            else:
                result[k] = v  # 显式修改 → 管理员意图优先
        return result

    async def _api_upsert(self, request):
        self._require(request)
        body = await request.json()
        table = self._table(body.get("table", ""))
        key = str(body.get("key", "")).strip()
        value = body.get("value")
        if not key:
            return self._json({"ok": False, "msg": "键不能为空"})
        if not isinstance(value, dict):
            return self._json({"ok": False, "msg": "记录内容必须是 JSON 对象"})
        existing = self.store._data.get(table, {}).get(key)
        # 3 路合并：编辑已有记录时，把管理员改动合并到最新实时数据上，
        # 既保证后台修改能保存，又避免旧快照整条覆盖玩家最新进度（回溯）。
        if existing is not None:
            base = body.get("base")
            if isinstance(base, dict):
                value = self._merge_edits(base, value, existing)
        # 保存活动时保留运行时 Boss 状态，避免后台编辑把当前血量/伤害排行清空
        if table == "events":
            if isinstance(existing, dict) and "_boss_state" in existing \
                    and "_boss_state" not in value:
                value["_boss_state"] = existing["_boss_state"]
        self.store._data.setdefault(table, {})[key] = value
        await self.store.save()
        logger.info(
            f"[petpark][webadmin] upsert {table}/{key} "
            f"({'更新' if existing is not None else '新增'}) by {request.remote}"
        )
        return self._json({"ok": True})

    async def _api_delete(self, request):
        self._require(request)
        body = await request.json()
        table = self._table(body.get("table", ""))
        key = str(body.get("key", ""))
        removed = self.store._data.get(table, {}).pop(key, None)
        await self.store.save()
        if removed is not None:
            logger.info(f"[petpark][webadmin] delete {table}/{key} by {request.remote}")
        return self._json({"ok": True})

    async def _api_cards_batch_delete(self, request):
        """批量删除卡密：keys 指定卡密列表，或 mode=used 删除全部已使用卡密。"""
        self._require(request)
        body = await request.json()
        cards = self.store._data.setdefault("cards", {})
        mode = str(body.get("mode", "")).strip()
        if mode == "used":
            keys = [k for k, v in cards.items() if isinstance(v, dict) and v.get("used")]
        elif mode == "assistant":
            # 一键清理全部自动助手卡（后台改版后清旧卡用）
            keys = [k for k, v in cards.items()
                    if isinstance(v, dict) and "assistant_quota" in v]
        else:
            req_keys = body.get("keys")
            if not isinstance(req_keys, list) or not req_keys:
                return self._json({"ok": False, "msg": "请选择要删除的卡密"})
            keys = [str(k) for k in req_keys if str(k) in cards]
        if not keys:
            return self._json({"ok": False, "msg": "没有可删除的卡密"})
        for k in keys:
            cards.pop(k, None)
        await self.store.save()
        logger.info(
            f"[petpark][webadmin] batch_delete cards ×{len(keys)} "
            f"(mode={mode or 'keys'}) by {request.remote}"
        )
        return self._json({"ok": True, "deleted": len(keys)})

    async def _api_gen_cards(self, request):
        self._require(request)
        body = await request.json()
        auth_days = int(body.get("auth_days", 0) or 0)
        card_type = str(body.get("card_type", "")).strip()
        try:
            if card_type == "custom_pet":
                codes = self.store.create_custom_cards(
                    count=int(body.get("count", 1)),
                    prefix=body.get("prefix", ""),
                )
            elif card_type == "mount_custom":
                codes = self.store.create_mount_custom_cards(
                    count=int(body.get("count", 1)),
                    prefix=body.get("prefix", ""),
                )
            elif card_type == "assistant":
                codes = self.store.create_assistant_cards(
                    count=int(body.get("count", 1)),
                    prefix=body.get("prefix", ""),
                )
            elif auth_days > 0:
                st = str(body.get("server_type", "official")).strip()
                codes = self.store.create_auth_cards(
                    days=auth_days,
                    count=int(body.get("count", 1)),
                    prefix=body.get("prefix", ""),
                    server_type=st,
                )
            else:
                rewards = body.get("rewards")
                if not isinstance(rewards, dict):
                    # 兼容旧版单一货币入参
                    rewards = {body.get("currency", ""): body.get("amount", 0)}
                items = body.get("items")
                if not isinstance(items, dict):
                    items = None
                codes = self.store.create_combo_cards(
                    rewards=rewards,
                    items=items,
                    count=int(body.get("count", 1)),
                    prefix=body.get("prefix", ""),
                )
        except (ValueError, TypeError) as e:
            return self._json({"ok": False, "msg": str(e)})
        await self.store.save()
        return self._json({"ok": True, "codes": codes})

    async def _api_custom_reviews(self, request):
        self._require(request)
        body = await request.json()
        status = body.get("status", "")
        kind = body.get("kind", "")
        reviews = list(self.store.custom_reviews().values())
        if status:
            reviews = [r for r in reviews if r.get("status") == status]
        if kind:
            reviews = [r for r in reviews if r.get("kind", "pet") == kind]
        data = sorted(reviews, key=lambda x: x.get("created_at", 0), reverse=True)
        return self._json(paginate(data, body))

    async def _api_custom_review_approve(self, request):
        self._require(request)
        body = await request.json()
        rid = str(body.get("id", "")).strip()
        ok, msg = self.store.apply_custom_review(rid)
        if ok:
            await self.store.save()
            # 坐骑审核通过 → 全服贺电（与宠物定制同款，QQ 名/ QQ 号可由玩家在申请时填入）
            try:
                review = self.store.custom_reviews().get(rid) or {}
                if review.get("kind") == "mount" and self._broadcast_callback:
                    player = self.store._data["players"].get(
                        self.store.make_key(review.get("group", ""), review.get("qq", "")))
                    pet = (player.get("pets") or [{}])[0] if player else None
                    nick = review.get("nickname") or (
                        (self.store.get_account(review.get("account_id", "")) or {}).get("qq", "")
                        if review.get("account_id") else "")
                    show_qq = review.get("show_qq") or (
                        (self.store.get_account(review.get("account_id", "")) or {}).get("qq", "")
                        if review.get("account_id") else "")
                    mount_name = review.get("mount_name") or review.get("new", {}).get("name", "")
                    text = (
                        "🎉 **全服贺电！灵契仙途迎来全新定制坐骑大师！** 🎉\n\n"
                        f"👑 尊贵的训练家 **{nick or '神秘训练家'}**（QQ：{show_qq or '—'}）\n"
                        f"创造了一只独一无二的新坐骑 **{mount_name or '神秘坐骑'}**，初始战力 30 万！\n\n"
                        f"✨ **{mount_name or '神秘坐骑'}** 拥有专属外观图与战斗加成，"
                        "（属性不可自定义——实力与热爱的象征）。\n\n"
                        "🚀 各位训练家也快去努力，打造属于自己的专属传奇坐骑吧！"
                    )
                    task = self._broadcast_callback(text)
                    if task:
                        task.add_done_callback(
                            lambda t: logger.info(f"[petpark] 定制坐骑全服广播结果：{t.result()}"))
                    logger.info("[petpark] 定制坐骑全服广播已提交后台执行")
            except Exception:
                logger.exception("[petpark] 定制坐骑广播异常")
        return self._json({"ok": ok, "msg": msg})

    async def _api_custom_review_reject(self, request):
        self._require(request)
        body = await request.json()
        rid = str(body.get("id", "")).strip()
        reason = str(body.get("reason", "")).strip()
        if not reason:
            return self._json({"ok": False, "msg": "请填写拒绝原因"})
        ok, msg = self.store.reject_custom_review(rid, reason)
        if ok:
            await self.store.save()
        return self._json({"ok": ok, "msg": msg})

    async def _api_feedbacks(self, request):
        self._require(request)
        body = await request.json()
        status = body.get("status", "")
        data = list(self.store.feedbacks().values())
        if status:
            data = [f for f in data if f.get("status") == status]
        data.sort(key=lambda x: x.get("created_at", 0), reverse=True)
        return self._json(paginate(data, body))

    async def _api_feedback_reply(self, request):
        self._require(request)
        body = await request.json()
        fid = str(body.get("id", "")).strip()
        reply = str(body.get("reply", "")).strip()
        if not reply:
            return self._json({"ok": False, "msg": "请填写回复内容"})
        ok, msg = self.store.reply_feedback(fid, reply)
        if ok:
            await self.store.save()
        return self._json({"ok": ok, "msg": msg})

    async def _api_feedback_delete(self, request):
        self._require(request)
        body = await request.json()
        fid = str(body.get("id", "")).strip()
        ok = self.store.delete_feedback(fid)
        if ok:
            await self.store.save()
        return self._json({"ok": ok, "msg": "已删除" if ok else "反馈记录不存在"})

    async def _api_app_release_info(self, request):
        self._require(request)
        rel = self.store.app_release()
        return self._json({"ok": True, "data": {
            "version_code": rel.get("version_code", 0),
            "version_name": rel.get("version_name", ""),
            "changelog": rel.get("changelog", ""),
            "filename": rel.get("filename", ""),
            "size": rel.get("size", 0),
            "updated_at": rel.get("updated_at"),
        }})

    async def _api_app_release_upload(self, request):
        self._require(request)
        reader = await request.multipart()
        version_code = 0
        version_name = ""
        changelog = ""
        apk_bytes = b""
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "version_code":
                version_code = int((await part.text()).strip() or 0)
            elif part.name == "version_name":
                version_name = (await part.text()).strip()
            elif part.name == "changelog":
                changelog = (await part.text()).strip()
            elif part.name == "apk":
                apk_bytes = await part.read(decode=False)
        if version_code <= 0 or not version_name:
            return self._json({"ok": False, "msg": "请填写版本号（version_code 为正整数）和版本名"})
        rel = self.store.app_release()
        if apk_bytes:
            if apk_bytes[:2] != b"PK":
                return self._json({"ok": False, "msg": "文件不是有效的 APK"})
            filename = f"petpark_{version_code}.apk"
            (self.store.app_release_dir / filename).write_bytes(apk_bytes)
            old = rel.get("filename", "")
            if old and old != filename:
                try:
                    (self.store.app_release_dir / old).unlink(missing_ok=True)
                except OSError:
                    pass
            rel["filename"] = filename
            rel["size"] = len(apk_bytes)
        elif not rel.get("filename"):
            return self._json({"ok": False, "msg": "请选择 APK 文件"})
        rel["version_code"] = version_code
        rel["version_name"] = version_name
        rel["changelog"] = changelog
        rel["updated_at"] = int(time.time())
        await self.store.save()
        return self._json({"ok": True, "msg": "发布成功"})

    async def _api_custom_pets(self, request):
        self._require(request)
        body = await request.json()
        accounts = self.store.accounts()
        account_map = {}
        for aid, acc in accounts.items():
            for bp in acc.get("bound_pets", []):
                account_map[self.store.make_key(bp.get("group", ""), bp.get("qq", ""))] = acc.get("qq", aid)
        data = []
        for key, player in self.store._data.get("players", {}).items():
            pet = player.get("pet")
            if not pet or not pet.get("custom"):
                continue
            group, qq = key.split("\x1f", 1)
            data.append({
                "group": group,
                "qq": qq,
                "account_qq": account_map.get(key, "—"),
                "nickname": pet.get("nickname", "未命名"),
                "species": pet.get("species", "未知"),
                "custom_species_name": pet.get("custom_species_name"),
                "custom_image": pet.get("custom_image"),
                "quality": pet.get("quality", "普通"),
                "tags": pet.get("tags", []),
            })
        data.sort(key=lambda x: x["group"])
        return self._json(paginate(data, body))

    async def _api_custom_mounts(self, request):
        """列出所有玩家的定制坐骑（供「定制管理」页维护外观图）。"""
        self._require(request)
        body = await request.json()
        accounts = self.store.accounts()
        account_map = {}
        for aid, acc in accounts.items():
            for bp in acc.get("bound_pets", []):
                account_map[self.store.make_key(bp.get("group", ""), bp.get("qq", ""))] = acc.get("qq", aid)
        data = []
        for key, player in self.store._data.get("players", {}).items():
            mounts = player.get("mounts") or {}
            for name, inst in mounts.items():
                if not (inst or {}).get("custom"):
                    continue
                group, qq = key.split("\x1f", 1)
                data.append({
                    "group": group,
                    "qq": qq,
                    "account_qq": account_map.get(key, "—"),
                    "name": name,
                    "custom_image": inst.get("custom_image") or "",
                    "power": int(inst.get("power", 0) or 0),
                    "level": int(inst.get("level", 1) or 1),
                    "stars": int(inst.get("stars", 0) or 0),
                    "plate": inst.get("plate", ""),
                    "reward_min": int(inst.get("reward_min", 0) or 0),
                    "reward_max": int(inst.get("reward_max", 0) or 0),
                })
        data.sort(key=lambda x: (x["group"], x["qq"]))
        return self._json(paginate(data, body))

    _CUSTOM_MOUNT_IMG_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

    async def _api_custom_mount_set_image(self, request):
        """后台直接更换/补救定制坐骑外观图（如历史丢图）：上传文件 → 落盘 → 更新实例。"""
        self._require(request)
        reader = await request.multipart()
        group = qq = mname = ""
        file_data = b""
        filename = ""
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "image":
                file_data = await part.read(decode=False)
                filename = part.filename or ""
            elif part.name == "group":
                group = (await part.text()).strip()
            elif part.name == "qq":
                qq = (await part.text()).strip()
            elif part.name == "name":
                mname = (await part.text()).strip()
        if not group or not qq or not mname:
            return self._json({"ok": False, "msg": "参数不完整"})
        player = self.store._data.get("players", {}).get(self.store.make_key(group, qq))
        if not player:
            return self._json({"ok": False, "msg": "未找到该角色"})
        inst = (player.get("mounts") or {}).get(mname)
        if not inst or not inst.get("custom"):
            return self._json({"ok": False, "msg": "未找到该定制坐骑"})
        if not file_data:
            return self._json({"ok": False, "msg": "请选择图片文件"})
        if len(file_data) > 5 * 1024 * 1024:
            return self._json({"ok": False, "msg": "图片不能超过 5MB"})
        ext = Path(filename).suffix.lower() if filename else ".jpg"
        if ext not in self._CUSTOM_MOUNT_IMG_EXTS:
            return self._json({"ok": False, "msg": "仅支持 jpg/png/gif/webp 图片"})
        # webp 规范化（QQ 端不兼容 webp）：动态转 GIF / 静态转 PNG，与玩家上传同一逻辑
        file_data, ext = PlayerPortal._normalize_custom_image(file_data, ext)
        new_filename = f"{secrets.token_hex(8)}{ext}"
        path = self.store.custom_image_path(new_filename)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(file_data)
        except OSError as e:
            logger.exception(f"[petpark] 后台坐骑换图写盘失败 {path}: {e}")
            return self._json({"ok": False, "msg": f"图片保存失败：{e}"})
        if not path.exists():
            logger.error(f"[petpark] 后台坐骑换图写盘后不存在 {path}")
            return self._json({"ok": False, "msg": "图片保存失败，请重试"})
        old_img = str(inst.get("custom_image") or "")
        inst["custom_image"] = new_filename
        await self.store.save()
        # 回收旧外观图（与回收临时图同理，避免孤儿文件）
        if old_img and old_img != new_filename:
            try:
                old_p = self.store.custom_image_path(old_img)
                if old_p.exists():
                    old_p.unlink()
            except OSError:
                pass
        logger.info(f"[petpark] 后台已更换定制坐骑外观 group={group} qq={qq} mount={mname} img={new_filename}")
        return self._json({"ok": True, "msg": "外观图已更新", "image": new_filename})

    async def _api_custom_pet_cancel(self, request):
        self._require(request)
        body = await request.json()
        group = str(body.get("group", "")).strip()
        qq = str(body.get("qq", "")).strip()
        if not group or not qq:
            return self._json({"ok": False, "msg": "参数不完整"})
        player = self.store._data.get("players", {}).get(self.store.make_key(group, qq))
        if not player:
            return self._json({"ok": False, "msg": "未找到玩家"})
        pet = player.get("pet")
        if not pet or not pet.get("custom"):
            return self._json({"ok": False, "msg": "该宠物未开启定制"})
        pet["custom"] = False
        pet.pop("custom_image", None)
        pet.pop("custom_species_name", None)
        self.store.remove_pet_tag(pet, "定制")
        await self.store.save()
        return self._json({"ok": True, "msg": "已取消该宠物的定制权限"})

    async def _api_boss_respawn(self, request):
        """管理后台：立即复活指定活动的 Boss，并向所有授权群播报。

        仅在 Boss 已阵亡（处于复活倒计时中）时允许立即复活；Boss 还活着时不能强制复活。
        """
        self._require(request)
        body = await request.json()
        eid = str(body.get("event_id", "")).strip()
        if not eid:
            return self._json({"ok": False, "msg": "请填写活动ID"})
        cfg = self.store.events().get(eid)
        if not cfg:
            return self._json({"ok": False, "msg": f"活动 {eid} 不存在"})
        boss = cfg.get("boss", {})
        if not boss.get("enabled"):
            return self._json({"ok": False, "msg": "该活动未启用 Boss"})

        state = cfg.get("_boss_state", {})
        now = int(time.time())
        # 判断 Boss 是否还活着：有血且不在复活倒计时中
        if (
            state
            and state.get("hp", 0) > 0
            and state.get("respawn_until", 0) <= now
        ):
            return self._json(
                {"ok": False, "msg": "Boss 还活着，无需复活。请等待它被击杀后再操作。"}
            )

        max_hp = int(boss.get("hp", 10000))
        cfg["_boss_state"] = {
            "max_hp": max_hp,
            "hp": max_hp,
            "respawn_until": 0,
            "damage_rank": {},
            "respawn_notified": False,
        }
        await self.store.save()
        bname = boss.get("name", "活动Boss")
        cmd = boss.get("cmd", "活动Boss")
        text = (
            f"## 👹 世界 Boss {bname} 已复活！\n"
            f"血量 {max_hp}/{max_hp}，发送 `{cmd}` 即可挑战。"
        )
        if self._broadcast_callback:
            try:
                self._broadcast_callback(text)
            except Exception:
                logger.exception("[petpark] 后台复活 Boss 广播失败")
        return self._json({"ok": True, "msg": f"Boss {bname} 已复活并全服播报"})

    async def _api_lottery_state(self, request):
        """口令抽奖当前状态（配置 + 报名 + 开奖结果）。"""
        self._require(request)
        return self._json({"ok": True, "data": self.store.lottery()})

    async def _api_lottery_save(self, request):
        """保存口令抽奖配置。默认保留运行时状态（报名 entries / 开奖结果）；reset=true 清空重来。"""
        self._require(request)
        body = await request.json()
        cfg = body.get("cfg")
        if not isinstance(cfg, dict):
            return self._json({"ok": False, "msg": "配置必须是对象"})
        existing = self.store.lottery()
        existing = existing if isinstance(existing, dict) else {}
        if body.get("reset"):
            # 重置为全新抽奖：清空报名与开奖结果，并生成新一轮编号
            cfg["entries"] = {}
            cfg["winners"] = []
            cfg["drawn"] = False
            cfg["drawn_at"] = 0
            cfg["created_at"] = int(time.time())
        else:
            # 编辑配置时不要冲掉已报名用户与开奖结果；全新配置用安全的默认值
            cfg.setdefault("entries", existing.get("entries", {}))
            cfg.setdefault("winners", existing.get("winners", []))
            cfg.setdefault("drawn", bool(existing.get("drawn", False)))
            cfg.setdefault("drawn_at", existing.get("drawn_at", 0))
            cfg.setdefault("created_at", existing.get("created_at", 0) or int(time.time()))
        self.store.set_lottery(cfg)
        await self.store.save()
        logger.info(f"[petpark][webadmin] 口令抽奖配置保存 by {request.remote}")
        return self._json({"ok": True})

    async def _api_lottery_draw(self, request):
        """管理后台「立即开奖」：由插件执行抽取 + 全群播报（幂等）。"""
        self._require(request)
        gw = self._command_gateway
        if gw is None or not hasattr(gw, "lottery_force_draw"):
            return self._json({"ok": False, "msg": "开奖由插件执行，当前网关不可用"})
        try:
            msg = await gw.lottery_force_draw()
        except Exception as e:
            logger.exception("[petpark] 后台手动开奖失败")
            return self._json({"ok": False, "msg": f"开奖失败：{e}"})
        return self._json({"ok": True, "msg": msg})

    # --------------------------- 自定义文本群推送 ---------------------------
    def _push_state(self) -> dict:
        return self.store._data.setdefault("custom_push", {"jobs": []})

    async def _api_push_state(self, request):
        self._require(request)
        return self._json({"ok": True, "data": self._push_state()})

    async def _api_push_manual(self, request):
        self._require(request)
        body = await request.json()
        text = str(body.get("text") or "").strip()
        if not text:
            return self._json({"ok": False, "msg": "文案不能为空"})
        if not self._broadcast_callback:
            return self._json({"ok": False, "msg": "广播接口不可用"})
        task = self._broadcast_callback(text)
        result = {}
        if task is not None:
            try:
                result = await task
            except Exception as e:
                logger.exception("[petpark] 后台手动群推送失败")
                return self._json({"ok": False, "msg": f"推送异常：{e}"})
        return self._json({
            "ok": True,
            "msg": f"已推送：目标 {result.get('targets', 0)} 群，成功 {result.get('sent', 0)}，失败 {result.get('failed', 0)}",
            "result": result,
        })

    async def _api_push_save(self, request):
        self._require(request)
        body = await request.json()
        mode = str(body.get("mode") or "").strip()
        text = str(body.get("text") or "").strip()
        name = str(body.get("name") or "").strip()
        interval_min = int(body.get("interval_min") or 0)
        target_ts = int(body.get("target_ts") or 0)
        jid = str(body.get("id") or "").strip()
        if mode not in ("once", "recurring"):
            return self._json({"ok": False, "msg": "模式必须是 once 或 recurring"})
        if not text:
            return self._json({"ok": False, "msg": "文案不能为空"})
        if mode == "recurring" and interval_min <= 0:
            return self._json({"ok": False, "msg": "循环间隔需大于 0 分钟"})
        if mode == "once" and target_ts <= int(time.time()):
            return self._json({"ok": False, "msg": "指定时间需是未来时间"})
        state = self._push_state()
        jobs = state.get("jobs")
        if not isinstance(jobs, list):
            state["jobs"] = []
            jobs = state["jobs"]
        now = int(time.time())
        if jid:
            job = next((j for j in jobs if j.get("id") == jid), None)
            if job is None:
                return self._json({"ok": False, "msg": "任务不存在"})
        else:
            job = {
                "id": "push_" + secrets.token_hex(6),
                "name": "未命名任务",
                "mode": mode,
                "text": text,
                "enabled": True,
                "created_at": now,
                "done": False,
                "last_result": None,
            }
            jobs.append(job)
        job["name"] = name or job.get("name") or "未命名任务"
        job["mode"] = mode
        job["text"] = text
        job["created_at"] = job.get("created_at") or now
        job["enabled"] = True
        if mode == "recurring":
            job["interval_min"] = max(1, interval_min)
            if not job.get("next_run"):
                job["next_run"] = now  # 创建即开始：首轮很快触发
        else:
            job["target_ts"] = target_ts
            job["done"] = False
        await self.store.save()
        logger.info(f"[petpark][webadmin] 群推送任务保存 by {request.remote}")
        return self._json({"ok": True, "msg": f"已保存任务「{job['name']}」"})

    async def _api_push_toggle(self, request):
        self._require(request)
        body = await request.json()
        jid = str(body.get("id") or "").strip()
        job = next((j for j in self._push_state().get("jobs", []) if j.get("id") == jid), None)
        if job is None:
            return self._json({"ok": False, "msg": "任务不存在"})
        job["enabled"] = bool(body.get("enabled", not job.get("enabled")))
        await self.store.save()
        return self._json({"ok": True, "msg": "已启用" if job["enabled"] else "已停用"})

    async def _api_push_fire(self, request):
        self._require(request)
        body = await request.json()
        jid = str(body.get("id") or "").strip()
        job = next((j for j in self._push_state().get("jobs", []) if j.get("id") == jid), None)
        if job is None:
            return self._json({"ok": False, "msg": "任务不存在"})
        text = str(job.get("text") or "").strip()
        if not text:
            return self._json({"ok": False, "msg": "文案为空"})
        if not self._broadcast_callback:
            return self._json({"ok": False, "msg": "广播接口不可用"})
        task = self._broadcast_callback(text)
        result = {}
        if task is not None:
            try:
                result = await task
            except Exception as e:
                logger.exception("[petpark] 后台手动触发推送失败")
                return self._json({"ok": False, "msg": f"推送异常：{e}"})
        job["last_result"] = {
            "ts": int(time.time()),
            "sent": int(result.get("sent", 0)),
            "failed": int(result.get("failed", 0)),
            "targets": int(result.get("targets", 0)),
            "error": ("; ".join(result.get("errors", [])[:5]) if result.get("errors") else None),
        }
        await self.store.save()
        return self._json({
            "ok": True,
            "msg": f"已触发：目标 {result.get('targets', 0)} 群，成功 {result.get('sent', 0)}，失败 {result.get('failed', 0)}",
        })

    async def _api_push_delete(self, request):
        self._require(request)
        body = await request.json()
        jid = str(body.get("id") or "").strip()
        state = self._push_state()
        jobs = state.get("jobs")
        if not isinstance(jobs, list):
            state["jobs"] = []
            jobs = state["jobs"]
        new = [j for j in jobs if j.get("id") != jid]
        if len(new) == len(jobs):
            return self._json({"ok": False, "msg": "任务不存在"})
        state["jobs"] = new
        await self.store.save()
        return self._json({"ok": True, "msg": "已删除任务"})

    # --------------------------- 生辰盛典（每日定时开奖箱 + 奖池瓜分） ---------------------------
    def _celebrate_state(self) -> dict:
        return self.store._data.setdefault("celebrate", {})

    async def _api_celebrate_state(self, request):
        self._require(request)
        return self._json({"ok": True, "data": self._celebrate_state()})

    # ------------------- 限时免费使用自动助手（全局窗口） -------------------
    def _assistant_free_state(self) -> dict:
        return self.store._data.setdefault(
            "assistant_free", {"enabled": False, "start_at": 0, "end_at": 0}
        )

    async def _api_assistant_free_state(self, request):
        self._require(request)
        cfg = self._assistant_free_state()
        return self._json({
            "ok": True,
            "data": dict(cfg),
            "active": self.store.assistant_free_active(),
            "now": int(time.time()),
        })

    async def _api_assistant_free_save(self, request):
        self._require(request)
        body = await request.json()
        cfg = body.get("assistant_free") or {}
        st = self._assistant_free_state()
        st["enabled"] = bool(cfg.get("enabled", st.get("enabled")))
        st["start_at"] = max(0, int(cfg.get("start_at") or 0))
        st["end_at"] = max(0, int(cfg.get("end_at") or 0))
        await self.store.save()
        logger.info(
            f"[petpark][webadmin] 限时免费助手窗口已保存 {st} by {request.remote}"
        )
        active = self.store.assistant_free_active()
        return self._json({
            "ok": True,
            "msg": f"已保存（当前{'生效中' if active else '未生效'}）",
            "active": active,
        })

    # ------------------- 数据追溯（审计流水 + 异常检测） -------------------
    async def _api_audit_query(self, request):
        self._require(request)
        body = await request.json()
        log = self.store._data.get("audit_log") or []
        # 筛选：action / group / pid / 时间范围 / 关键字
        action = str(body.get("action") or "")
        group = str(body.get("group") or "")
        pid = str(body.get("pid") or "")
        kw = str(body.get("kw") or "").strip().lower()
        ts_from = int(body.get("ts_from") or 0)
        ts_to = int(body.get("ts_to") or 0)
        rows = []
        for rec in reversed(log):  # 倒序：最新在前
            if action and rec.get("action") != action:
                continue
            if group and rec.get("group") != group:
                continue
            if pid and str(rec.get("pid") or "") != pid:
                continue
            ts = int(rec.get("ts") or 0)
            if ts_from and ts < ts_from:
                continue
            if ts_to and ts > ts_to:
                continue
            if kw:
                hay = " ".join(str(v) for v in rec.values() if not isinstance(v, (dict, list))).lower()
                if kw not in hay:
                    continue
            rows.append(rec)
        result = paginate(rows, {"page": body.get("page", 1)})
        result["items"] = result.pop("data")
        result["now"] = int(time.time())
        return self._json(result)

    async def _api_audit_flags(self, request):
        self._require(request)
        body = await request.json()
        status = str(body.get("status") or "")
        flags = self.store._data.get("audit_flags") or {}
        items = []
        for fid, f in flags.items():
            if status and f.get("status") != status:
                continue
            items.append({"flag_id": fid, **f})
        items.sort(key=lambda x: x.get("ts", 0), reverse=True)
        result = paginate(items, body)
        result["items"] = result.pop("data")
        result["open_count"] = sum(f.get("status") == "open" for f in items)
        return self._json(result)

    async def _api_audit_scan(self, request):
        self._require(request)
        result = self.store.audit_scan()
        await self.store.save()
        logger.info(
            f"[petpark][webadmin] 全量异常扫描完成 {result} by {request.remote}"
        )
        return self._json({"ok": True, **result})

    async def _api_audit_flag_update(self, request):
        self._require(request)
        body = await request.json()
        flag_id = str(body.get("flag_id") or "")
        status = str(body.get("status") or "")
        if status not in ("open", "handled", "false_positive"):
            return self._json({"ok": False, "msg": "状态须为 open/handled/false_positive"})
        flags = self.store._data.setdefault("audit_flags", {})
        if flag_id not in flags:
            return self._json({"ok": False, "msg": "标记不存在"})
        flags[flag_id]["status"] = status
        await self.store.save()
        logger.info(
            f"[petpark][webadmin] 异常标记 {flag_id} -> {status} by {request.remote}"
        )
        return self._json({"ok": True, "flag": flags[flag_id]})

    async def _api_audit_verify(self, request):
        self._require(request)
        result = self.store.audit_verify()
        return self._json({"ok": True, **result})

    async def _api_celebrate_save(self, request):
        self._require(request)
        body = await request.json()
        cfg = body.get("celebrate") or {}
        cel = self._celebrate_state()
        cel["enabled"] = bool(cfg.get("enabled", cel.get("enabled")))
        cel["name"] = str(cfg.get("name") or cel.get("name") or "生辰盛典")
        cel["start_at"] = int(cfg.get("start_at") or 0)
        cel["end_at"] = int(cfg.get("end_at") or 0)
        cel["announce"] = str(cfg.get("announce") or "")
        cel["announce_end"] = str(cfg.get("announce_end") or "")
        cel["announced_start"] = bool(cfg.get("announced_start", cel.get("announced_start")))
        cel["announced_end"] = bool(cfg.get("announced_end", cel.get("announced_end")))
        cel["howto"] = str(cfg.get("howto") or "")
        cel["howto_interval_h"] = max(0, int(cfg.get("howto_interval_h") or 0))
        # 抽奖场次：按 draw_at 匹配旧场次，保留已开奖/参与者等运行时状态
        gcfg = cfg.get("gacha") or {}
        old_rounds = (cel.get("gacha") or {}).get("rounds")
        old_rounds = old_rounds if isinstance(old_rounds, list) else []
        rounds = []
        for r in (gcfg.get("rounds") or []):
            da = int(r.get("draw_at") or 0)
            entry = {
                "time": str(r.get("time") or ""),
                "draw_at": da,
                "drawn": False,
                "participants": {},
                "result": None,
            }
            old = next((o for o in old_rounds if int(o.get("draw_at") or 0) == da), None)
            if old is not None:
                entry["drawn"] = bool(old.get("drawn"))
                entry["participants"] = old.get("participants") or {}
                entry["result"] = old.get("result")
            rounds.append(entry)
        cel.setdefault("gacha", {})
        gacha = cel["gacha"]
        gacha["enabled"] = bool(gcfg.get("enabled", gacha.get("enabled", True)))
        gacha["cmd"] = str(gcfg.get("cmd") or gacha.get("cmd") or "生日抽奖")
        gacha["menu_cmd"] = str(gcfg.get("menu_cmd") or gacha.get("menu_cmd") or "生辰活动")
        # 中奖率 + 压轴大奖（定制卡，仅最后一轮）
        gacha["win_rate"] = min(1.0, max(0.0, float(gcfg.get("win_rate") or gacha.get("win_rate") or 0.8)))
        gacha["grand_item"] = str(gcfg.get("grand_item") or gacha.get("grand_item") or "宠物定制卡")
        gacha["grand_count"] = max(1, int(gcfg.get("grand_count") or gacha.get("grand_count") or 1))
        gacha["grand_used"] = bool(gcfg.get("grand_used", gacha.get("grand_used")))
        # 动态库存：配置总量 + 保留已有剩余
        def _cnt(sc):
            if isinstance(sc, (int, float)):
                return max(0, int(sc))
            return max(0, int((sc or {}).get("count") or 0))
        new_stock = {}
        for nm, sc in (gcfg.get("stock") or {}).items():
            if not nm:
                continue
            new_stock[nm] = _cnt(sc)
        gacha["stock"] = new_stock
        old_remain = gacha.get("stock_remain") or {}
        new_remain = {}
        for nm, total in new_stock.items():
            new_remain[nm] = int(old_remain.get(nm, total)) if nm in old_remain else total
        gacha["stock_remain"] = new_remain
        gacha["rounds"] = rounds
        # 奖池瓜分
        pcfg = cfg.get("pool") or {}
        cel.setdefault("pool", {})
        pool = cel["pool"]
        pool["enabled"] = bool(pcfg.get("enabled", pool.get("enabled", True)))
        pool["cmd"] = str(pcfg.get("cmd") or pool.get("cmd") or "生日快乐")
        pool["start_time"] = str(pcfg.get("start_time") or pool.get("start_time") or "07:00")
        pool["cooldown_min"] = max(1, int(pcfg.get("cooldown_min") or pool.get("cooldown_min") or 15))
        pool["cooldown_max"] = max(pool["cooldown_min"], int(pcfg.get("cooldown_max") or pool.get("cooldown_max") or 30))
        # 等额固定额度 + 无冷却开关（后台可配；见 main._celebrate_pool）
        pool["per_grab"] = max(0, int(pcfg.get("per_grab") or pool.get("per_grab") or 1000))
        # 按币种单独覆盖瓜分额度（缺省走 scalar per_grab）；钻石默认 100 在 main 里兜底
        pool["per_grab_by_cur"] = dict(pcfg.get("per_grab_by_cur") or pool.get("per_grab_by_cur") or {})
        pool["no_cd"] = bool(pcfg.get("no_cd", pool.get("no_cd", True)))
        old_cur = pool.get("currencies") or {}
        new_cur = {}
        for name, c in (pcfg.get("currencies") or {}).items():
            if not name:
                continue
            new_cur[name] = {
                "total": max(0, int(c.get("total") or 0)),
                # 递减动态：瓜分不再配 min/max，改为每次按剩余比例动态抽取（见 main._celebrate_pool）
            }
        pool["currencies"] = new_cur
        # 池余额：已有币保留，新增币补齐至 total，删除的币清除
        remain = cel.setdefault("pool_remain", {})
        for name in list(remain.keys()):
            if name not in new_cur:
                remain.pop(name, None)
        for name, c in new_cur.items():
            remain[name] = int(remain.get(name, c["total"])) if name in remain else c["total"]
        await self.store.save()
        logger.info(f"[petpark][webadmin] 生辰盛典配置保存 by {request.remote}")
        return self._json({"ok": True, "msg": "已保存生辰盛典配置"})

    async def _api_celebrate_reset_pool(self, request):
        self._require(request)
        cel = self._celebrate_state()
        cur = (cel.get("pool") or {}).get("currencies") or {}
        remain = cel.setdefault("pool_remain", {})
        for name, c in cur.items():
            remain[name] = int(c.get("total") or 0)
        await self.store.save()
        return self._json({"ok": True, "msg": "奖池剩余已重置回配置总额"})

    async def _api_celebrate_reset_stock(self, request):
        self._require(request)
        cel = self._celebrate_state()
        gacha = cel.setdefault("gacha", {})
        stock = gacha.get("stock") or {}
        gacha["stock_remain"] = {nm: max(0, int(c)) for nm, c in stock.items()}
        gacha["grand_used"] = False   # 同时恢复可再发定制卡压轴大奖
        await self.store.save()
        return self._json({"ok": True, "msg": "抽奖库存剩余已重置回配置总量"})

    async def _api_celebrate_broadcast(self, request):
        self._require(request)
        body = await request.json()
        which = str(body.get("which") or "start")
        cel = self._celebrate_state()
        text = str(cel.get("announce") or "") if which == "start" else str(cel.get("announce_end") or "")
        if not text:
            return self._json({"ok": False, "msg": "公告文案为空"})
        if not self._broadcast_callback:
            return self._json({"ok": False, "msg": "广播接口不可用"})
        task = self._broadcast_callback(text)
        result = {}
        if task is not None:
            try:
                result = await task
            except Exception as e:
                logger.exception("[petpark] 后台生辰盛典广播失败")
                return self._json({"ok": False, "msg": f"广播异常：{e}"})
        return self._json({
            "ok": True,
            "msg": f"已广播：目标 {result.get('targets', 0)} 群，成功 {result.get('sent', 0)}，失败 {result.get('failed', 0)}",
        })

    async def _api_zhongyuan_config(self, request):
        """中元活动：读取完整配置（供后台「中元活动」页渲染）。API Key 脱敏返回。"""
        self._require(request)
        zy = self.zhongyuan
        if zy is None:
            return self._json({"ok": False, "msg": "中元活动模块未加载"})
        cfg = dict(zy.cfg)
        key = str(cfg.get("deepseek_api_key") or "")
        if key:
            cfg["deepseek_api_key"] = (key[:3] + "••••" + key[-4:]) if len(key) > 8 else "••••"
        return self._json({"ok": True, "data": cfg})

    async def _api_zhongyuan_config_save(self, request):
        """中元活动：保存配置（类型由引擎按当前值强制转换，list/dict 字段解析 JSON）。"""
        self._require(request)
        zy = self.zhongyuan
        if zy is None:
            return self._json({"ok": False, "msg": "中元活动模块未加载"})
        try:
            body = await request.json()
        except Exception:
            return self._json({"ok": False, "msg": "请求体必须是 JSON"})
        updates = body.get("config")
        if not isinstance(updates, dict):
            return self._json({"ok": False, "msg": "配置必须是对象"})
        ok, bad = zy.apply_config(updates)
        await zy.save()
        logger.info(f"[petpark][webadmin] 中元活动配置保存 by {request.remote}（成功 {ok}，跳过 {len(bad)}）")
        return self._json({"ok": True, "changed": ok, "bad": bad})

    async def _api_zhongyuan_test_deepseek(self, request):
        """中元活动：测试 DeepSeek 模型连接是否正常。"""
        self._require(request)
        zy = self.zhongyuan
        if zy is None:
            return self._json({"ok": False, "msg": "中元活动模块未加载"})
        ok, note, cost = await zy._deepseek.ping()
        model = str(zy.cfg.get("deepseek_model", "") or "")
        base = str(zy.cfg.get("deepseek_base_url", "") or "")
        msg = (
            ("✅ " if ok else "❌ ") + note
            + (f"（耗时 {cost:.2f}s，模型 {model or '未配置'}，接口 {base or '默认'}）")
        )
        logger.info(f"[petpark][webadmin] DeepSeek 连接测试 by {request.remote}：{msg}")
        return self._json({"ok": ok, "msg": msg, "cost": round(cost, 2)})

    async def _api_zhongyuan_test_broadcast(self, request):
        """中元活动：向所有已注册群推送一条全群通报测试消息。"""
        self._require(request)
        zy = self.zhongyuan
        if zy is None:
            return self._json({"ok": False, "msg": "中元活动模块未加载"})
        text = (
            "## 🕯️ 中元全群通报测试\n"
            "本消息由中元活动引擎向所有已注册群主动推送。若你看到此消息，说明中元全群通报链路正常。\n"
            "> 此为测试通报，可忽略。"
        )
        try:
            await zy._push_all_groups(text)
        except Exception as e:
            logger.exception("[petpark] 中元全群通报测试异常")
            return self._json({"ok": False, "msg": f"异常: {e}"})
        return self._json({"ok": True, "msg": "已向所有已注册群发起中元全群通报"})

    async def _api_zhongyuan_test_start(self, request):
        """中元活动：测试「活动开始」全群通报（仅推送，不更改活动状态）。"""
        self._require(request)
        zy = self.zhongyuan
        if zy is None:
            return self._json({"ok": False, "msg": "中元活动模块未加载"})
        text = (
            "## 🕯️ 中元活动开启\n"
            f"{ACTIVITY_NAME}\n"
            "—— 勾连阴阳两界的思念，一阴一阳，同场并陈 ——\n\n"
            "🕳️ **阴面 · 幽影饲育馆**（协作解密 · 每日 8:00–22:00）\n"
            "旧年江南一座饲育馆，馆主以「点灵续命」邪术抽离灵宠魂魄，封进青灯符箓；反噬之夜整馆被阴气吞没，只剩一部残破《规矩簿》与游荡不去的灵宠残魂。每逢中元，它便锁定一名驯宠师，强行勾你入馆——\n"
            "> 「解不开这馆里的规矩，你与你的宠物，就都留下吧。」\n\n"
            "🕯️ **阳面 · 青灯寄思**（文化温情 · 全天开放）\n"
            "放河灯、敬祖先、知中元，用一盏青灯照见思念归途。\n"
            "> 中元点灯，不为驱鬼，只为照见思念归途。\n\n"
            "💰 **全场唯一货币「功德」**：既是排行榜积分，也是唯一奖励。\n\n"
            "━━━━━━━━━━\n"
            "📜 **参与方式**\n"
            "发送「**相约中元**」领取你的活动 ID，踏入阴阳两界。\n"
            "> ⚠️ 未「相约中元」领取 ID 者，其余活动指令一律无效。\n"
            "发送「**中元活动**」查看完整玩法。"
        )
        try:
            await zy._push_all_groups(text)
        except Exception as e:
            logger.exception("[petpark] 中元「活动开始」通报测试异常")
            return self._json({"ok": False, "msg": f"异常: {e}"})
        return self._json({"ok": True, "msg": "已向所有已注册群发起「活动开始」全群通报（测试，未更改活动状态）"})

    async def _api_zhongyuan_test_end(self, request):
        """中元活动：测试「活动结束」全群通报（仅推送，不结算、不更改活动状态）。"""
        self._require(request)
        zy = self.zhongyuan
        if zy is None:
            return self._json({"ok": False, "msg": "中元活动模块未加载"})
        text = (
            "## 🕯️ 中元活动落幕\n"
            f"{ACTIVITY_NAME}\n"
            "已落下帷幕。\n"
            "阴阳门缓缓合拢，青灯渐次熄灭，思念长河终将收束。\n\n"
            "🏮 段位功德已结算完毕，功德榜就此定格，奖励已入「功德商店」。\n"
            "> 中元点灯，不为驱鬼，只为照见思念归途。这一程的思念，愿已送达故人。\n\n"
            "━━━━━━━━━━\n"
            "📜 **兑换提醒**\n"
            "段位 / 里程碑功德已入账，发送「**功德商店**」查看并兑换你的奖励。\n"
            "> ⏳ 兑换窗口 48 小时，逾期未兑换将作废。"
        )
        try:
            await zy._push_all_groups(text)
        except Exception as e:
            logger.exception("[petpark] 中元「活动结束」通报测试异常")
            return self._json({"ok": False, "msg": f"异常: {e}"})
        return self._json({"ok": True, "msg": "已向所有已注册群发起「活动结束」全群通报（测试，未结算、未更改活动状态）"})

    async def _api_zhongyuan_data(self, request):
        """中元活动：查看全部数据（config / meta / players / groups / sessions）。API Key 脱敏。"""
        self._require(request)
        zy = self.zhongyuan
        if zy is None:
            return self._json({"ok": False, "msg": "中元活动模块未加载"})
        data = copy.deepcopy(zy._data)
        cfg = data.get("config") or {}
        key = str(cfg.get("deepseek_api_key") or "")
        if key:
            cfg["deepseek_api_key"] = (key[:3] + "••••" + key[-4:]) if len(key) > 8 else "••••"
            data["config"] = cfg
        stats = {
            "players": len(data.get("players", {})),
            "groups": len(data.get("groups", {})),
            "sessions": len(data.get("sessions", {})),
        }
        return self._json({"ok": True, "data": data, "stats": stats})

    async def _api_zhongyuan_clear_data(self, request):
        """中元活动：清空全部玩家数据（players / groups / sessions + 活动 ID 序号）。保留 config。"""
        self._require(request)
        zy = self.zhongyuan
        if zy is None:
            return self._json({"ok": False, "msg": "中元活动模块未加载"})
        zy.reset_data()
        await zy.save()
        logger.info(f"[petpark][webadmin] 中元活动数据已清空 by {request.remote}")
        return self._json({"ok": True, "msg": "已清空中元所有玩家数据（配置与代码保留，活动 ID 从 1 重新分配）"})

    # --------------------------- 网页账号管理 ---------------------------
    async def _api_portal_accounts(self, request):
        self._require(request)
        accounts = self.store.accounts()
        data = []
        for aid, acc in accounts.items():
            data.append(
                {
                    "id": aid,
                    "qq": acc.get("qq"),
                    "created_at": acc.get("created_at"),
                    "last_login": acc.get("last_login"),
                    "bound_pets": acc.get("bound_pets", []),
                    "bound_slots": self.store.bound_slots_of(acc),
                }
            )
        return self._json(paginate(data, request.query))

    async def _api_portal_reset_password(self, request):
        self._require(request)
        body = await request.json()
        aid = str(body.get("account_id", "")).strip()
        new_pwd = str(body.get("new_password", ""))
        if len(new_pwd) < 6:
            return self._json({"ok": False, "msg": "密码长度至少 6 位"})
        acc = self.store.get_account(aid)
        if not acc:
            return self._json({"ok": False, "msg": "账号不存在"})
        salt = PlayerPortal._make_salt()
        acc["password_hash"] = PlayerPortal._hash_password(new_pwd, salt)
        acc["salt"] = salt
        await self.store.save()
        return self._json({"ok": True, "msg": "密码已重置"})

    async def _api_portal_delete_account(self, request):
        self._require(request)
        body = await request.json()
        aid = str(body.get("account_id", "")).strip()
        accounts = self.store.accounts()
        if aid in accounts:
            del accounts[aid]
            await self.store.save()
        return self._json({"ok": True})

    async def _api_portal_unbind(self, request):
        self._require(request)
        body = await request.json()
        aid = str(body.get("account_id", "")).strip()
        group = str(body.get("group", "")).strip()
        qq = str(body.get("qq", "")).strip()
        acc = self.store.get_account(aid)
        if not acc:
            return self._json({"ok": False, "msg": "账号不存在"})
        self.store.unbind_slot(aid, group, qq)
        await self.store.save()
        return self._json({"ok": True})


LOGIN_HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#112f2d">
<title>管理登录 · 灵契仙途</title>
<link rel="stylesheet" href="/webstatic/admin-login.css?v=20260922">
</head><body>
<main class="login-layout">
 <section class="landscape" aria-label="灵契仙途">
  <a href="/" class="brand"><span class="seal" aria-hidden="true">契</span>灵契仙途</a>
  <div class="landscape-copy"><h1>山海有灵，<br>仙途有序。</h1><p>灵契仙途运营管理</p></div>
  <div class="landscape-footer"><span>与灵宠结契，共赴仙途。</span><a href="/">返回官网</a></div>
 </section>
 <section class="login-panel" aria-labelledby="login-title">
  <div class="login-intro"><span class="panel-marker" aria-hidden="true"></span><span>管理后台</span></div>
  <form method="post" action="/login">
   <h2 id="login-title">登录管理账号</h2>
   <p class="intro">管理玩家、处理反馈，照看这一方仙途。</p>
   <div class="error" role="alert"><!--ERR--></div>
   <label for="admin-user">账号</label>
   <input id="admin-user" name="user" placeholder="输入管理账号" autocomplete="username" required autocapitalize="none" spellcheck="false">
   <label for="admin-password">密码</label>
   <input id="admin-password" name="password" type="password" placeholder="输入登录密码" autocomplete="current-password" required>
   <button type="submit">登录后台</button>
   <p class="login-help">此入口供管理员使用。<a href="/portal">前往玩家中心</a></p>
  </form>
  <footer><span>灵契仙途</span><a href="/">官网首页</a></footer>
 </section>
</main>
</body></html>
"""

DASHBOARD_HTML = r"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>灵契仙途 · 管理后台</title>
<link rel="stylesheet" href="/webstatic/admin.css?v=20260922"></head><body class="admin-page">
<a class="skip-link" href="#workspace">跳到管理内容</a>
<aside class="sidebar" id="sidebar">
 <a class="admin-brand" href="/"><span class="seal" aria-hidden="true">契</span><span>灵契仙途<small>运营管理</small></span></a>
 <nav class="tabs" aria-label="管理导航"><div class="nav-group"><p>日常管理</p><button data-t="players" onclick="tab('players')" class="active" aria-current="page">玩家档案</button><button data-t="groups" onclick="tab('groups')">群设置</button><button data-t="cards" onclick="tab('cards')">卡密管理</button><button data-t="portal_accounts" onclick="tab('portal_accounts')">网页账号</button></div><div class="nav-group"><p>玩家服务</p><button data-t="custom_reviews" onclick="tab('custom_reviews')">定制审核</button><button data-t="custom_pets" onclick="tab('custom_pets')">定制管理</button><button data-t="feedbacks" onclick="tab('feedbacks')">玩家反馈</button></div><div class="nav-group"><p>活动运营</p><button data-t="events" onclick="tab('events')">活动配置</button><button data-t="lottery" onclick="tab('lottery')">口令抽奖</button><button data-t="push" onclick="tab('push')">群推送</button><button data-t="celebrate" onclick="tab('celebrate')">生辰盛典</button><button data-t="zhongyuan" onclick="tab('zhongyuan')">中元活动</button><button data-t="assistant_free" onclick="tab('assistant_free')">免费助手</button></div><div class="nav-group"><p>系统维护</p><button data-t="audit" onclick="tab('audit')">数据追溯</button><button data-t="app_release" onclick="tab('app_release')">App 发布</button></div></nav>
 <div class="sidebar-footer"><a href="/" target="_blank" rel="noopener">查看官网</a><a href="/logout">退出登录</a></div>
</aside>
<div class="workspace" id="workspace">
<header class="workspace-header"><button class="menu-button" aria-label="展开管理导航" aria-expanded="false" aria-controls="sidebar" onclick="toggleNavigation()">☰</button><span>管理后台 <span class="breadcrumb">/</span> <span id="section-label">玩家档案</span></span><span class="connection" id="load-state" role="status">正在加载</span></header>
<main>
<div class="page-heading"><div><h1 id="page-title">玩家档案</h1><p id="page-description">查看玩家与灵宠资料，管理游戏资产。</p></div><span class="page-size-note" id="page-size-note">每页 10 条</span></div>
<div class="request-error" id="request-error" role="alert" hidden></div>
<div id="cardgen" style="display:none">
<div class="cards-stat" id="cardstats"></div>
<div class="muted" style="margin-bottom:6px">套餐卡密：填了哪几项就加哪几项，可任意组合。空或 0 表示不含该项。<b>选择「宠物定制卡」将生成可解锁宠物定制权限的卡密。</b></div>
<div class="bar">
<select id="card_type" onchange="cardTypeChange()" style="width:130px">
 <option value="">货币/道具卡</option>
 <option value="custom_pet">宠物定制卡</option>
 <option value="mount_custom">坐骑定制卡</option>
 <option value="assistant">自动助手卡（500 次）</option>
</select>
<input id="amt_coin" type="number" placeholder="灵石面额" style="width:120px">
<input id="amt_jifen" type="number" placeholder="玄晶面额" style="width:120px">
<input id="amt_diamond" type="number" placeholder="天晶面额" style="width:120px">
<select id="amt_item" style="width:140px"></select>
<input id="amt_item_count" type="number" placeholder="数量" value="1" style="width:80px">
<input id="amt_authdays" type="number" placeholder="授权天数(群授权卡)" style="width:160px">
<select id="amt_server_type" title="群授权卡服类型" style="width:90px">
 <option value="official">官方服</option>
 <option value="infinite">无限服</option>
</select>
<input id="cnt" type="number" placeholder="数量" value="10" style="width:80px">
<input id="pre" placeholder="前缀(可选,如VIP)" style="width:130px">
<button class="act" onclick="genCards()">批量生成</button>
<button class="ghost act" onclick="exportUnused()">导出未用卡密</button>
<button class="act del" onclick="cardsDeleteSelected()">删除选中</button>
<button class="act del" onclick="cardsDeleteUsed()">删除已使用</button>
</div>
<div id="genout" class="muted" style="margin-bottom:8px"></div>
</div>
<div class="bar">
<button class="act" id="addBtn" onclick="addRow()">＋ 新增</button>
<input id="q" placeholder="搜索群号、QQ、名称或记录内容" aria-label="搜索当前列表" oninput="scheduleSearch()" style="flex:1;min-width:160px">
<button class="ghost act" onclick="load()">刷新</button>
<span class="muted" id="count"></span>
</div>
<div id="extrawrap"></div>
<div id="tablewrap" aria-live="polite"></div><div id="pager"></div>
</main></div>
<div class="modal" id="modal"><div class="card">
<h3 id="mtitle">编辑</h3>
<div class="muted" id="msub"></div>
<datalist id="itemlist"></datalist>
<div id="mfields"></div>
<details class="adv"><summary>高级编辑（原始 JSON）</summary>
<textarea id="mval"></textarea></details>
<div style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end">
<button class="act ghost" onclick="closeModal()">取消</button>
<button class="act" onclick="saveRow()">保存</button>
</div></div></div>
<div class="modal" id="pamodal"><div class="card">
<h3 id="patitle">网页账号详情</h3>
<div id="pabody"></div>
<div style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end">
<button class="act ghost" onclick="closePaModal()">关闭</button>
</div>
</div></div>
<div class="modal" id="fbmodal"><div class="card" style="width:min(760px,96vw)">
<h3 id="fbtitle">反馈详情</h3>
<div id="fbbody"></div>
<div style="margin-top:18px;display:flex;gap:10px;justify-content:flex-end">
<button class="act ghost" onclick="closeFbModal()">关闭</button>
</div>
</div></div>
<script src="/webstatic/admin.js?v=20260922" defer></script></body></html>"""
