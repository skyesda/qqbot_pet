"""宠物乐园专属管理网站。

在 AstrBot 进程内启动一个独立端口的 aiohttp 网站，提供：
- 账号密码登录（默认 admin / 2468080asd，可在插件配置修改）；
- 查看 / 增删改查 插件数据库（玩家 players、群设置 groups、卡密 cards）；
- 批量生成卡密（金币 / 积分 / 钻石）。

依赖 aiohttp（AstrBot 自带）。启动失败不会影响插件主体功能。
"""

from __future__ import annotations

import copy
import json
import secrets
import time
from typing import Any

from astrbot.api import logger

from .portal import PlayerPortal
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
        app.router.add_post("/api/celebrate/broadcast", self._api_celebrate_broadcast)

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
        return self._json({"ok": True, "data": self.store._data.get(table, {})})

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
            elif card_type == "auto_cultivation":
                codes = self.store.create_auto_cultivation_cards(
                    count=int(body.get("count", 1)),
                    prefix=body.get("prefix", ""),
                )
            elif auth_days > 0:
                codes = self.store.create_auth_cards(
                    days=auth_days,
                    count=int(body.get("count", 1)),
                    prefix=body.get("prefix", ""),
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
        reviews = list(self.store.custom_reviews().values())
        if status:
            reviews = [r for r in reviews if r.get("status") == status]
        data = sorted(reviews, key=lambda x: x.get("created_at", 0), reverse=True)
        return self._json({"ok": True, "data": data})

    async def _api_custom_review_approve(self, request):
        self._require(request)
        body = await request.json()
        rid = str(body.get("id", "")).strip()
        ok, msg = self.store.apply_custom_review(rid)
        if ok:
            await self.store.save()
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
        return self._json({"ok": True, "data": data})

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
        return self._json({"ok": True, "data": data})

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
                "grand": {
                    "item": str((r.get("grand") or {}).get("item") or ""),
                    "count": max(1, int((r.get("grand") or {}).get("count") or 1)),
                },
                "normal": {
                    "item": str((r.get("normal") or {}).get("item") or ""),
                    "count": max(1, int((r.get("normal") or {}).get("count") or 1)),
                },
                "normal_winners": max(0, int(r.get("normal_winners") or 0)),
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
        gacha["cmd"] = str(gcfg.get("cmd") or gacha.get("cmd") or "生辰抽奖")
        gacha["menu_cmd"] = str(gcfg.get("menu_cmd") or gacha.get("menu_cmd") or "生辰活动")
        gacha["rounds"] = rounds
        # 奖池瓜分
        pcfg = cfg.get("pool") or {}
        cel.setdefault("pool", {})
        pool = cel["pool"]
        pool["enabled"] = bool(pcfg.get("enabled", pool.get("enabled", True)))
        pool["cmd"] = str(pcfg.get("cmd") or pool.get("cmd") or "生辰瓜分")
        pool["cooldown_min"] = max(1, int(pcfg.get("cooldown_min") or pool.get("cooldown_min") or 30))
        old_cur = pool.get("currencies") or {}
        new_cur = {}
        for name, c in (pcfg.get("currencies") or {}).items():
            if not name:
                continue
            new_cur[name] = {
                "total": max(0, int(c.get("total") or 0)),
                "min": max(0, int(c.get("min") or 0)),
                "max": max(0, int(c.get("max") or 0)),
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
                }
            )
        return self._json({"ok": True, "data": data})

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
        bound = acc.get("bound_pets", [])
        acc["bound_pets"] = [
            bp
            for bp in bound
            if not (bp.get("group") == group and bp.get("qq") == qq)
        ]
        await self.store.save()
        return self._json({"ok": True})


LOGIN_HTML = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>宠物乐园 · 管理登录</title>
<style>
body{margin:0;font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;background:#f6f8fd;color:#141a2a;display:flex;min-height:100vh;align-items:center;justify-content:center;-webkit-font-smoothing:antialiased}
body::before{content:'';position:fixed;top:-260px;left:-160px;width:640px;height:640px;border-radius:50%;background:radial-gradient(closest-side,rgba(47,107,255,.14),transparent);pointer-events:none}
body::after{content:'';position:fixed;bottom:-280px;right:-180px;width:720px;height:720px;border-radius:50%;background:radial-gradient(closest-side,rgba(147,51,234,.10),transparent);pointer-events:none}
.box{background:#fff;padding:36px 32px;border-radius:22px;width:340px;border:1px solid #e8ecf6;box-shadow:0 24px 64px -12px rgba(47,107,255,.18),0 2px 6px rgba(20,26,42,.06);position:relative;overflow:hidden;z-index:1}
.box::before{content:'';position:absolute;top:0;left:0;right:0;height:4px;background:linear-gradient(120deg,#2f6bff,#6d4aff,#9333ea)}
h1{font-size:19px;font-weight:800;margin:0 0 22px;text-align:center;letter-spacing:-.2px}
input{width:100%;box-sizing:border-box;padding:12px 15px;margin:7px 0;border-radius:12px;border:1.5px solid transparent;background:#f7f9fd;color:#141a2a;font-size:14px;outline:none;transition:border-color .2s,box-shadow .2s,background .2s}
input:focus{background:#fff;border-color:#2f6bff;box-shadow:0 0 0 4px rgba(47,107,255,.12)}
button{width:100%;padding:12px;margin-top:14px;border:0;border-radius:12px;background:linear-gradient(120deg,#2f6bff,#6d4aff,#9333ea);color:#fff;font-size:15px;font-weight:700;cursor:pointer;box-shadow:0 8px 20px -6px rgba(47,107,255,.5);transition:transform .15s,box-shadow .2s}
button:hover{transform:translateY(-1px);box-shadow:0 12px 26px -6px rgba(47,107,255,.55)}
.err{color:#e5484d;text-align:center;min-height:18px;font-size:13px}
</style></head><body>
<form class="box" method="post" action="/login">
<h1>宠物乐园 · 管理后台</h1>
<div class="err"><!--ERR--></div>
<input name="user" placeholder="账号" autocomplete="username">
<input name="password" type="password" placeholder="密码" autocomplete="current-password">
<button type="submit">登录</button>
</form></body></html>"""



DASHBOARD_HTML = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>宠物乐园 · 管理后台</title>
<style>
*{box-sizing:border-box}
body{margin:0;font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;background:#f6f8fd;color:#141a2a;-webkit-font-smoothing:antialiased}
header{background:rgba(255,255,255,.85);backdrop-filter:blur(10px);padding:14px 24px;display:flex;align-items:center;gap:16px;position:sticky;top:0;z-index:10;border-bottom:1px solid #e8ecf6}
header h1{font-size:16px;font-weight:800;margin:0;flex:1;letter-spacing:-.2px}
header h1::before{content:'';display:inline-block;width:10px;height:10px;border-radius:3px;background:linear-gradient(120deg,#2f6bff,#9333ea);margin-right:9px}
header a{color:#8a93a8;text-decoration:none;font-size:13px;font-weight:600;padding:7px 14px;border-radius:999px;border:1px solid #e8ecf6;background:#fff;transition:color .2s,border-color .2s}
header a:hover{color:#2f6bff;border-color:#2f6bff}
.tabs{display:flex;gap:6px;padding:16px 24px 0;flex-wrap:wrap}
.tabs button{padding:9px 18px;border:1px solid transparent;border-radius:999px;background:transparent;color:#8a93a8;cursor:pointer;font-size:13.5px;font-weight:600;transition:background .2s,color .2s}
.tabs button:hover{background:#eef3ff;color:#2f6bff}
.tabs button.active{background:linear-gradient(120deg,#2f6bff,#6d4aff);color:#fff;box-shadow:0 6px 16px -6px rgba(47,107,255,.5)}
main{padding:18px 24px 28px}
.cards-stat{display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap}
.stat{background:#fff;border:1px solid #e8ecf6;border-radius:14px;padding:14px 20px;min-width:130px;box-shadow:0 1px 2px rgba(20,26,42,.04)}
.stat .n{font-size:23px;font-weight:800;background:linear-gradient(120deg,#2f6bff,#9333ea);-webkit-background-clip:text;background-clip:text;color:transparent;font-variant-numeric:tabular-nums}
.stat .l{font-size:12px;color:#8a93a8;margin-top:2px;font-weight:500}
.bar{margin-bottom:14px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
input,select{padding:9px 13px;border-radius:10px;border:1.5px solid #e8ecf6;background:#fff;color:#141a2a;font-size:13px;outline:none;transition:border-color .2s,box-shadow .2s}
input:focus,select:focus{border-color:#2f6bff;box-shadow:0 0 0 3px rgba(47,107,255,.12)}
input::placeholder{color:#aab3c7}
label.fld{display:block;margin:10px 0 4px;font-size:13px;color:#5b657d;font-weight:600}
button.act{padding:9px 16px;border:0;border-radius:10px;background:linear-gradient(120deg,#2f6bff,#6d4aff);color:#fff;cursor:pointer;font-size:13px;font-weight:600;box-shadow:0 4px 12px -4px rgba(47,107,255,.45);transition:transform .15s,box-shadow .2s,filter .15s}
button.act:hover{transform:translateY(-1px);filter:saturate(1.08)}
button.del{background:linear-gradient(120deg,#ef4444,#dc2626);box-shadow:0 4px 12px -4px rgba(220,38,38,.4)}
button.del:hover{filter:brightness(1.05)}
.tag{display:inline-block;font-size:11px;background:#eef3ff;color:#2f6bff;border:1px solid rgba(47,107,255,.25);border-radius:999px;padding:2px 9px;margin:1px;font-weight:600}
button.ghost{background:#fff;color:#5b657d;border:1.5px solid #d8dfef;box-shadow:none}
button.ghost:hover{color:#2f6bff;border-color:#2f6bff;background:#eef3ff;transform:none}
table{width:100%;border-collapse:separate;border-spacing:0;font-size:13px;background:#fff;border:1px solid #e8ecf6;border-radius:14px;overflow:hidden;box-shadow:0 1px 2px rgba(20,26,42,.04)}
th,td{padding:12px 14px;border-bottom:1px solid #eef1f8;text-align:left}
tr:last-child td{border-bottom:0}
th{color:#8a93a8;background:#fafbfe;font-weight:700;font-size:12px;letter-spacing:.3px}
tr:hover td{background:#f7f9fd}
td.k{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;color:#6d4aff;word-break:break-all;max-width:240px}
.num{font-variant-numeric:tabular-nums}
.coin{color:#d97706;font-weight:700}.jifen{color:#059669;font-weight:700}.diamond{color:#0891b2;font-weight:700}
.tag{padding:2px 10px;border-radius:999px;font-size:12px;white-space:nowrap}
.used{background:#feeef0;color:#c53a3f;border-color:rgba(229,72,77,.25)}.unused{background:#e9f8f0;color:#0c7a45;border-color:rgba(15,157,88,.25)}
.on{background:#eef3ff;color:#2f6bff;border-color:rgba(47,107,255,.25)}.off{background:#f1f3f9;color:#8a93a8;border-color:#e8ecf6}
.modal{position:fixed;inset:0;background:rgba(20,26,42,.4);backdrop-filter:blur(6px);display:none;align-items:center;justify-content:center;z-index:20}
.modal .card{background:#fff;padding:26px;border-radius:20px;width:min(720px,96vw);max-height:90vh;overflow:auto;border:1px solid #e8ecf6;box-shadow:0 32px 80px -12px rgba(20,26,42,.3)}
.modal h3{margin:0 0 6px;font-weight:800;letter-spacing:-.2px}
.content-ellipsis{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;text-overflow:ellipsis;line-height:1.5;max-width:340px}
.fb-detail-content{white-space:pre-wrap;line-height:1.7;color:#1f2937;font-size:15px;word-break:break-word}
.fb-detail-meta{color:#64748b;font-size:13px;margin-top:10px}
.fb-detail-images{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}
.fb-detail-images img{width:120px;height:120px;object-fit:cover;border-radius:12px;border:1px solid #e2e8f0;cursor:pointer;transition:transform .2s}
.fb-detail-images img:hover{transform:scale(1.03)}
.row{display:flex;gap:10px;flex-wrap:wrap}
.row>div{flex:1;min-width:120px}
.row input{width:100%}
textarea{width:100%;height:240px;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px;border-radius:12px;border:1.5px solid #e8ecf6;background:#f7f9fd;color:#141a2a;padding:12px;outline:none}
textarea:focus{border-color:#2f6bff;box-shadow:0 0 0 3px rgba(47,107,255,.12);background:#fff}
.muted{color:#8a93a8;font-size:12px}
.adv{margin-top:12px}
.adv summary{cursor:pointer;color:#5b657d;font-size:13px;font-weight:600}
.chk{display:flex;align-items:center;gap:8px;margin:10px 0}
.chk input{width:auto}
.sec{margin:18px 0 8px;font-weight:800;font-size:13px;color:#5b657d;border-bottom:1px solid #e8ecf6;padding-bottom:6px;letter-spacing:.3px}
.bagrow{margin:6px 0}
.empty{padding:32px;text-align:center;color:#8a93a8;background:#fff;border:1px dashed #d8dfef;border-radius:14px}
</style></head><body>
<header><h1>宠物乐园 · 管理后台</h1><a href="/logout">退出登录</a></header>
<div class="tabs">
<button data-t="players" class="active" onclick="tab('players')">玩家</button>
<button data-t="groups" onclick="tab('groups')">群设置</button>
<button data-t="cards" onclick="tab('cards')">卡密</button>
<button data-t="events" onclick="tab('events')">活动</button>
<button data-t="lottery" onclick="tab('lottery')">口令抽奖</button>
<button data-t="portal_accounts" onclick="tab('portal_accounts')">网页账号</button>
<button data-t="custom_reviews" onclick="tab('custom_reviews')">定制审核</button>
<button data-t="custom_pets" onclick="tab('custom_pets')">定制管理</button>
<button data-t="feedbacks" onclick="tab('feedbacks')">玩家反馈</button>
<button data-t="app_release" onclick="tab('app_release')">App 发布</button>
<button data-t="zhongyuan" onclick="tab('zhongyuan')">中元活动</button>
<button data-t="push" onclick="tab('push')">群推送</button>
<button data-t="celebrate" onclick="tab('celebrate')">生辰盛典</button>
</div>
<main>
<div id="cardgen" style="display:none">
<div class="cards-stat" id="cardstats"></div>
<div class="muted" style="margin-bottom:6px">套餐卡密：填了哪几项就加哪几项，可任意组合。空或 0 表示不含该项。<b>选择「宠物定制卡」将生成可解锁宠物定制权限的卡密。</b></div>
<div class="bar">
<select id="card_type" onchange="cardTypeChange()" style="width:130px">
 <option value="">货币/道具卡</option>
 <option value="custom_pet">宠物定制卡</option>
 <option value="auto_cultivation">自动修炼卡</option>
</select>
<input id="amt_coin" type="number" placeholder="金币面额" style="width:120px">
<input id="amt_jifen" type="number" placeholder="积分面额" style="width:120px">
<input id="amt_diamond" type="number" placeholder="钻石面额" style="width:120px">
<select id="amt_item" style="width:140px"></select>
<input id="amt_item_count" type="number" placeholder="数量" value="1" style="width:80px">
<input id="amt_authdays" type="number" placeholder="授权天数(群授权卡)" style="width:160px">
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
<input id="q" placeholder="搜索…" oninput="render()" style="flex:1;min-width:160px">
<button class="ghost act" onclick="load()">刷新</button>
<span class="muted" id="count"></span>
</div>
<div id="extrawrap"></div>
<div id="tablewrap"></div>
</main>
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
<script>
let cur='players', cache={}, editKey=null, editSnapshot=null, META={};
let paCache=[];
let LOTTERY=null;

async function loadPortalAccounts(){
 const r=await (await fetch('/api/portal_accounts')).json();
 paCache=r.data||[];
 renderPortalAccounts();
}
function renderPortalAccounts(){
 const q=(document.getElementById('q').value||'').toLowerCase();
 let rows='';
 for(const a of paCache){
  if(q && !a.id.toLowerCase().includes(q) && !String(a.qq).toLowerCase().includes(q)) continue;
  rows+=`<tr>
   <td class="k">${esc(a.id)}</td>
   <td class="num">${esc(a.qq||'')}</td>
   <td class="num">${(a.bound_pets||[]).length}</td>
   <td class="muted">${fdate(a.last_login)}</td>
   <td class="muted">${fdate(a.created_at)}</td>
   <td style="white-space:nowrap"><button class="act" onclick='paDetail(${tj(a.id)})'>查看</button> <button class="act" onclick='paResetPwd(${tj(a.id)})'>重置密码</button> <button class="act del" onclick='paDelete(${tj(a.id)})'>删除</button></td>
  </tr>`;
 }
 document.getElementById('count').textContent='共 '+paCache.length+' 个账号';
 document.getElementById('extrawrap').innerHTML='';
 document.getElementById('tablewrap').innerHTML = rows
   ? `<table><thead><tr><th>ID</th><th>QQ</th><th>绑定宠物</th><th>最后登录</th><th>创建时间</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table>`
   : `<div class="empty">暂无网页账号</div>`;
}
function paDetail(aid){
 const a=paCache.find(x=>x.id===aid); if(!a) return;
 let pets='';
 for(const p of (a.bound_pets||[])){
  pets+=`<div class="row" style="align-items:center;margin:6px 0;padding:8px;border:1px solid #e8ecf6;border-radius:8px">
   <div style="flex:1"><div class="muted">群号 / 用户ID</div>${esc(p.group||'')} / ${esc(p.qq||'')}</div>
   <div style="flex:1"><div class="muted">宠物</div>${esc(p.nickname||'未命名')} · ${esc(p.species||'未知')}</div>
   <div><button class="act del" onclick='paUnbind(${tj(aid)},${tj(p.group)},${tj(p.qq)})'>解绑</button></div>
  </div>`;
 }
 if(!pets) pets='<div class="muted">未绑定任何宠物</div>';
 g('patitle').textContent='账号详情：'+esc(a.qq||a.id);
 g('pabody').innerHTML=`
  <div class="row"><div><label class="fld">ID</label><input readonly value="${esc(a.id)}"></div><div><label class="fld">QQ</label><input readonly value="${esc(a.qq||'')}"></div></div>
  <div class="sec">已绑定宠物</div>${pets}`;
 g('pamodal').style.display='flex';
}
function closePaModal(){ g('pamodal').style.display='none'; }
async function paResetPwd(aid){
 const pwd=prompt('请输入新密码（至少6位）：'); if(!pwd) return;
 if(pwd.length<6){ alert('密码长度至少 6 位'); return; }
 const r=await api('/api/portal_accounts/reset_password',{account_id:aid,new_password:pwd});
 alert(r.ok?(r.msg||'重置成功'):(r.msg||'重置失败'));
}
async function paDelete(aid){ if(!confirm('确认删除账号 '+aid+'？绑定关系也会清空。')) return; await api('/api/portal_accounts/delete',{account_id:aid}); loadPortalAccounts(); }
function crImgUrl(img){ if(!img) return ''; if(img.startsWith('http') || img.startsWith('/')) return esc(img); return '/custom_images/'+esc(img); }
function crImgBox(img,label){
 if(!img) return '';
 return `<div><div class="muted">${label}</div><div style="width:160px;height:160px;overflow:auto;border-radius:8px;border:1px solid #e8ecf6"><img src="${crImgUrl(img)}" style="width:512px;height:512px;object-fit:contain;display:block"></div></div>`;
}
async function paUnbind(aid,group,qq){ if(!confirm(`确认解绑 ${group} / ${qq}？`)) return; await api('/api/portal_accounts/unbind',{account_id:aid,group, qq}); loadPortalAccounts(); paDetail(aid); }

let crCache=[], crStatus='pending';
async function loadCustomReviews(status='pending'){
 crStatus=status;
 const r=await api('/api/custom_reviews',{status});
 crCache=r.data||[];
 renderCustomReviews();
}
function renderCustomReviews(){
 const q=(document.getElementById('q').value||'').toLowerCase();
 let rows='';
 for(const r of crCache){
  if(q && !r.id.toLowerCase().includes(q) && !String(r.qq).toLowerCase().includes(q) && !String(r.group).toLowerCase().includes(q)) continue;
  const oldImg=r.old.image||'';
  const newImg=r.new.image||'';
  const oldName=esc(r.old.species_name||'');
  const newName=esc(r.new.species_name||'');
  rows+=`<tr>
   <td class="k">${esc(r.id)}</td>
   <td class="num">${esc(r.qq||'')}</td>
   <td class="num">${esc(r.group||'')}</td>
   <td>${r.new.species_name?`<div class="muted">旧：${oldName}</div><div>新：${newName}</div>`:'—'}</td>
   <td>${r.new.image||oldImg?`<div style="display:flex;gap:8px">${crImgBox(oldImg,'旧')}${crImgBox(r.new.image,'新')}</div>`:'—'}</td>
   <td class="muted">${fdate(r.created_at)}</td>
   <td>${r.status==='pending'?`<button class="act" onclick='crApprove(${tj(r.id)})'>通过</button> <button class="act del" onclick='crReject(${tj(r.id)})'>拒绝</button>`:`<span class="tag ${r.status==='approved'?'on':'off'}">${r.status==='approved'?'已通过':'已拒绝'}</span><div class="muted">${esc(r.reason||'')}</div>`}</td>
  </tr>`;
 }
 document.getElementById('count').textContent='共 '+crCache.length+' 条';
 document.getElementById('extrawrap').innerHTML=`
  <div class="bar" style="margin-bottom:8px">
   <button class="act ${crStatus==='pending'?'':'ghost'}" onclick="loadCustomReviews('pending')">待审核</button>
   <button class="act ${crStatus==='approved'?'':'ghost'}" onclick="loadCustomReviews('approved')">已通过</button>
   <button class="act ${crStatus==='rejected'?'':'ghost'}" onclick="loadCustomReviews('rejected')">已拒绝</button>
   <button class="act ${crStatus===''?'':'ghost'}" onclick="loadCustomReviews('')">全部</button>
  </div>`;
 document.getElementById('tablewrap').innerHTML = rows
   ? `<table><thead><tr><th>ID</th><th>QQ</th><th>群号</th><th>名称</th><th>图片</th><th>提交时间</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table>`
   : `<div class="empty">暂无审核记录</div>`;
}
async function crApprove(id){ if(!confirm('确认通过该定制申请？')) return; const r=await api('/api/custom_reviews/approve',{id}); alert(r.ok?(r.msg||'已通过'):(r.msg||'操作失败')); loadCustomReviews(crStatus); }
async function crReject(id){ const reason=prompt('请输入拒绝原因：'); if(!reason) return; const r=await api('/api/custom_reviews/reject',{id,reason}); alert(r.ok?(r.msg||'已拒绝'):(r.msg||'操作失败')); loadCustomReviews(crStatus); }

let cpCache=[];
async function loadCustomPets(){
 const r=await api('/api/custom_pets',{});
 cpCache=r.data||[];
 renderCustomPets();
}
function renderCustomPets(){
 const q=(document.getElementById('q').value||'').toLowerCase();
 let rows='';
 for(const p of cpCache){
  if(q && !String(p.group).toLowerCase().includes(q) && !String(p.qq).toLowerCase().includes(q) && !String(p.account_qq).toLowerCase().includes(q) && !String(p.nickname).toLowerCase().includes(q)) continue;
  const img=p.custom_image?`<img src="/custom_images/${esc(p.custom_image)}" style="width:64px;height:64px;object-fit:cover;border-radius:8px;border:1px solid #e8ecf6">`:'—';
  const tags=(p.tags||[]).map(t=>`<span class="tag">${esc(t)}</span>`).join(' ');
  rows+=`<tr>
   <td class="num">${esc(p.group)}</td>
   <td class="num">${esc(p.qq)}</td>
   <td class="num">${esc(p.account_qq)}</td>
   <td>${esc(p.nickname)}</td>
   <td>${esc(p.custom_species_name||p.species)}</td>
   <td>${esc(p.quality)}</td>
   <td>${tags}</td>
   <td>${img}</td>
   <td style="white-space:nowrap"><button class="act del" onclick='cpCancel(${tj(p.group)},${tj(p.qq)})'>取消定制</button></td>
  </tr>`;
 }
 document.getElementById('count').textContent='共 '+cpCache.length+' 个';
 document.getElementById('extrawrap').innerHTML='';
 document.getElementById('tablewrap').innerHTML = rows
   ? `<table><thead><tr><th>群号</th><th>用户ID</th><th>账号QQ</th><th>宠物昵称</th><th>种类名称</th><th>品质</th><th>标签</th><th>定制图</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table>`
   : `<div class="empty">暂无已解锁定制的宠物</div>`;
}
function fsize(n){n=Number(n)||0;if(n<1024)return n+' B';if(n<1048576)return (n/1024).toFixed(1)+' KB';return (n/1048576).toFixed(2)+' MB';}
async function loadAppRelease(){
 document.getElementById('count').textContent='';
 document.getElementById('extrawrap').innerHTML='';
 let rel={};
 try{ const r=await api('/api/app_release/info',{}); rel=r.data||{}; }catch(e){}
 const cur=rel.filename?`当前线上版本：<b>${esc(rel.version_name||'')}</b>（versionCode ${esc(rel.version_code||0)}），文件 ${esc(rel.filename)}（${fsize(rel.size)}），发布于 ${fdate(rel.updated_at)}`:'当前尚未发布任何版本。';
 document.getElementById('tablewrap').innerHTML=`
 <div style="max-width:640px">
  <div class="muted" style="margin-bottom:14px;line-height:1.7">${cur}</div>
  <div style="background:#fff;border:1px solid #e8ecf6;border-radius:14px;padding:20px">
   <h3 style="margin:0 0 14px">发布新版本</h3>
   <div style="display:flex;flex-direction:column;gap:12px">
    <label>版本号 versionCode（必须比当前大的整数）<input id="ar_code" type="number" placeholder="如 2" style="width:100%;margin-top:5px;padding:9px 12px;border:1px solid #d8dfef;border-radius:9px" value="${esc((rel.version_code||0)+1)}"></label>
    <label>版本名 versionName（展示给用户，如 1.0.1）<input id="ar_name" placeholder="如 1.0.1" style="width:100%;margin-top:5px;padding:9px 12px;border:1px solid #d8dfef;border-radius:9px"></label>
    <label>更新说明（可选，多行）<textarea id="ar_log" rows="4" placeholder="本次更新内容…" style="width:100%;margin-top:5px;padding:9px 12px;border:1px solid #d8dfef;border-radius:9px;resize:vertical"></textarea></label>
    <label>APK 文件（不选则仅更新版本信息）<input id="ar_apk" type="file" accept=".apk,application/vnd.android.package-archive" style="margin-top:5px"></label>
    <div><button class="act" onclick="uploadApp()">发布</button> <span class="muted" id="ar_msg"></span></div>
   </div>
  </div>
 </div>`;
}
async function uploadApp(){
 const code=document.getElementById('ar_code').value.trim();
 const name=document.getElementById('ar_name').value.trim();
 const log=document.getElementById('ar_log').value.trim();
 const f=document.getElementById('ar_apk').files[0];
 const msg=document.getElementById('ar_msg');
 if(!code||Number(code)<=0){msg.textContent='请填写正确的版本号';return;}
 if(!name){msg.textContent='请填写版本名';return;}
 const fd=new FormData();
 fd.append('version_code',code); fd.append('version_name',name); fd.append('changelog',log);
 if(f) fd.append('apk',f,f.name);
 msg.textContent='上传中…';
 try{
  const r=await (await fetch('/api/app_release/upload',{method:'POST',body:fd})).json();
  msg.textContent=r.msg||(r.ok?'发布成功':'发布失败');
  if(r.ok) setTimeout(loadAppRelease,800);
 }catch(e){ msg.textContent='上传失败：'+e; }
}
let fbCache=[], fbStatus='pending';
async function loadFeedbacks(status){
 if(status===undefined) status=fbStatus;
 fbStatus=status;
 const r=await api('/api/feedbacks',{status});
 fbCache=r.data||[];
 renderFeedbacks();
}
function renderFeedbacks(){
 const q=(document.getElementById('q').value||'').toLowerCase();
 let rows='';
 for(const f of fbCache){
  if(q && !String(f.qq).toLowerCase().includes(q) && !String(f.group).toLowerCase().includes(q) && !String(f.user_id).toLowerCase().includes(q) && !String(f.content).toLowerCase().includes(q)) continue;
  const imgs=(f.images||[]).map(im=>`<a href="/feedback_images/${esc(im)}" target="_blank"><img src="/feedback_images/${esc(im)}" style="width:64px;height:64px;object-fit:cover;border-radius:8px;border:1px solid #e8ecf6"></a>`).join(' ')||'—';
  const meta=f.kind==='bug'?`<div class="muted">发生时间：${esc(f.occur_time||'—')}</div><div class="muted">群号：${esc(f.group||'—')} · 用户ID：${esc(f.user_id||'—')}</div>`:'';
  rows+=`<tr>
   <td><span class="tag ${f.kind==='bug'?'off':'on'}" onclick='fbDetail(${tj(f.id)})' style="cursor:pointer" title="查看详情">${f.kind==='bug'?'Bug':'建议'}</span></td>
   <td class="num">${esc(f.qq||'')}</td>
   <td style="max-width:340px"><div class="content-ellipsis" title="${esc(f.content).replace(/"/g,'&quot;')}">${esc(f.content)}</div>${meta}</td>
   <td>${imgs}</td>
   <td class="muted">${fdate(f.created_at)}</td>
   <td>${f.status==='pending'?`<span class="tag off">待处理</span>`:`<span class="tag on">已回复</span><div class="muted" style="max-width:220px;white-space:pre-wrap">${esc(f.reply||'')}</div><div class="muted">${fdate(f.replied_at)}</div>`}</td>
   <td style="white-space:nowrap"><button class="act" onclick='fbDetail(${tj(f.id)})'>详情</button> <button class="act" onclick='fbReply(${tj(f.id)})'>${f.status==='pending'?'回复':'修改回复'}</button> <button class="act del" onclick='fbDelete(${tj(f.id)})'>删除</button></td>
  </tr>`;
 }
 document.getElementById('count').textContent='共 '+fbCache.length+' 条';
 document.getElementById('extrawrap').innerHTML=`
  <div class="bar" style="margin-bottom:8px">
   <button class="act ${fbStatus==='pending'?'':'ghost'}" onclick="loadFeedbacks('pending')">待处理</button>
   <button class="act ${fbStatus==='resolved'?'':'ghost'}" onclick="loadFeedbacks('resolved')">已回复</button>
   <button class="act ${fbStatus===''?'':'ghost'}" onclick="loadFeedbacks('')">全部</button>
  </div>`;
 document.getElementById('tablewrap').innerHTML = rows
   ? `<table><thead><tr><th>类型</th><th>账号QQ</th><th>内容</th><th>截图</th><th>提交时间</th><th>处理状态</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table>`
   : `<div class="empty">暂无反馈</div>`;
}
async function fbReply(id){
 const cur=fbCache.find(x=>x.id===id)||{};
 const reply=prompt('回复内容（用户将在「我的反馈」中看到）：', cur.reply||'');
 if(!reply) return;
 const r=await api('/api/feedbacks/reply',{id,reply});
 alert(r.ok?(r.msg||'已回复'):(r.msg||'操作失败'));
 loadFeedbacks(fbStatus);
}
async function fbDelete(id){ if(!confirm('确认删除该条反馈？')) return; const r=await api('/api/feedbacks/delete',{id}); alert(r.ok?'已删除':(r.msg||'操作失败')); loadFeedbacks(fbStatus); }
function fbDetail(id){
 const f=fbCache.find(x=>x.id===id)||{};
 const meta=f.kind==='bug'
  ? `<div class="fb-detail-meta">发生时间：${esc(f.occur_time||'—')}　|　群号：${esc(f.group||'—')}　|　用户ID：${esc(f.user_id||'—')}</div>`
  : `<div class="fb-detail-meta">群号：${esc(f.group||'—')}　|　用户ID：${esc(f.user_id||'—')}</div>`;
 const imgs=(f.images||[]).map(im=>`<a href="/feedback_images/${esc(im)}" target="_blank"><img src="/feedback_images/${esc(im)}"></a>`).join('')||'';
 const reply=f.reply?`<div style="margin-top:18px;padding:14px;background:#f1f5f9;border-radius:12px"><div class="muted" style="font-weight:700;margin-bottom:6px">管理员回复（${fdate(f.replied_at)}）</div><div style="white-space:pre-wrap">${esc(f.reply)}</div></div>`:'';
 g('fbtitle').textContent=(f.kind==='bug'?'🐛 Bug 反馈':'💡 玩家建议')+' 详情';
 g('fbbody').innerHTML=`
  <div class="fb-detail-meta">提交账号：${esc(f.qq||'—')}　|　提交时间：${fdate(f.created_at)}</div>
  ${meta}
  <div class="fb-detail-content" style="margin-top:14px">${esc(f.content)}</div>
  ${imgs?`<div class="fb-detail-images">${imgs}</div>`:''}
  ${reply}
 `;
 g('fbmodal').style.display='flex';
}
function closeFbModal(){ g('fbmodal').style.display='none'; }
g('fbmodal').addEventListener('click',e=>{ if(e.target===g('fbmodal')) closeFbModal(); });

async function cpCancel(group,qq){ if(!confirm('确认取消该宠物的定制权限？将移除定制图和自定义名称。')) return; const r=await api('/api/custom_pets/cancel',{group,qq}); alert(r.ok?(r.msg||'已取消'):(r.msg||'操作失败')); loadCustomPets(); }

const PET_FIELDS=[
 ['nickname','昵称','text'],['species','种类','sel','species'],
 ['quality','品质','sel','qualities'],['element','元素','sel','elements'],
 ['gender','性别','sel','genders'],['stage','阶段','sel','stages'],
 ['level','等级','num'],['exp','经验','num'],
 ['hp','生命','num'],['hp_max','生命上限','num'],
 ['atk','攻击','num'],['def','防御','num'],['intel','智力','num'],
 ['mood','心情(1-5)','num'],['energy','精力','num'],['energy_max','精力上限','num'],
 ['status','状态','sel','statuses'],['love_state','姻缘','sel','love_states'],
 ['love_target','伴侣键(群+QQ)','text'],['favor','好感度','num'],
 ['artifact','神器','sel','artifacts','无'],['talent','天赋','sel','talents','无'],
];
const PET_DEF={nickname:'宝宝',species:'幼龙',quality:'普通',element:'金',gender:'男',stage:'幼年期',level:1,exp:0,hp:800,hp_max:800,atk:50,def:40,intel:30,mood:5,energy:100,energy_max:100,status:'正常',love_state:'单身',love_target:null,favor:0,artifact:null,talent:null,custom:false,skills:[],ascended:false,frozen_until:0};
async function loadMeta(){try{const r=await api('/api/meta',{});META=r.data||{};}catch(e){META={};} const am=g('amt_item'); if(am) am.innerHTML=optHtml(META.items||[],'', '道具名（可选）');}
function escA(s){return esc(s).replace(/"/g,'&quot;');}
function optHtml(list,val,empty){let h='';const L=(list||[]).map(String);if(empty!==undefined)h+=`<option value="">${esc(empty)}</option>`;for(const o of L)h+=`<option ${String(o)===String(val)?'selected':''}>${esc(o)}</option>`;if(val!==undefined&&val!==null&&val!==''&&!L.includes(String(val)))h+=`<option selected>${esc(val)}</option>`;return h;}
function tab(t){
 cur=t;
 document.querySelectorAll('.tabs button').forEach(b=>b.classList.toggle('active',b.dataset.t===t));
 document.getElementById('cardgen').style.display=(t==='cards')?'block':'none';
 const addBtn=document.getElementById('addBtn'); if(addBtn) addBtn.style.display=(t==='portal_accounts'||t==='custom_reviews'||t==='custom_pets'||t==='feedbacks'||t==='app_release'||t==='lottery'||t==='zhongyuan'||t==='push'||t==='celebrate')?'none':'';
 const bar=document.querySelector('main>.bar'); if(bar) bar.style.display=(t==='app_release'||t==='lottery'||t==='zhongyuan'||t==='push'||t==='celebrate')?'none':'';
 if(t==='portal_accounts') loadPortalAccounts();
 else if(t==='custom_reviews') loadCustomReviews();
 else if(t==='custom_pets') loadCustomPets();
 else if(t==='feedbacks') loadFeedbacks();
 else if(t==='app_release') loadAppRelease();
 else if(t==='lottery') loadLottery();
 else if(t==='zhongyuan') loadZhongyuan();
 else if(t==='push') loadPush();
 else if(t==='celebrate') loadCelebrate();
 else load();
}
async function api(p,b){const r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});return r.json();}
async function load(){ if(cur==='portal_accounts') return loadPortalAccounts(); if(cur==='custom_reviews') return loadCustomReviews(); if(cur==='custom_pets') return loadCustomPets(); if(cur==='feedbacks') return loadFeedbacks(); if(cur==='lottery') return loadLottery(); if(cur==='zhongyuan') return loadZhongyuan(); if(cur==='push') return loadPush(); if(cur==='celebrate') return loadCelebrate(); const r=await api('/api/list',{table:cur});cache=r.data||{};render();}
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function tj(k){return JSON.stringify(k);}
function fdate(ts){if(!ts)return '—';const d=new Date(ts*1000);return d.toLocaleString('zh-CN',{hour12:false});}
function match(k,v){const q=(document.getElementById('q').value||'').toLowerCase();if(!q)return true;return k.toLowerCase().includes(q)||JSON.stringify(v).toLowerCase().includes(q);}
function render(){
 if(cur==='players')renderPlayers();
 else if(cur==='groups')renderGroups();
 else if(cur==='events')renderEvents();
 else if(cur==='portal_accounts')renderPortalAccounts();
 else if(cur==='custom_reviews')renderCustomReviews();
 else if(cur==='custom_pets')renderCustomPets();
 else if(cur==='feedbacks')renderFeedbacks();
 else if(cur==='zhongyuan'){ /* 由 renderZhongyuan 自绘 */ }
 else if(cur==='push'){ /* 由 renderPush 自绘 */ }
 else if(cur==='celebrate'){ /* 由 renderCelebrate 自绘 */ }
 else renderCards();
}
// ---- 口令抽奖（管理表单；奖品从全部货币 + 全部道具中选择，全群共享）----
async function loadLottery(){
 const r=await api('/api/lottery/state',{});
 LOTTERY=r.data||null;
 renderLottery();
}
function renderLottery(){
 document.getElementById('count').textContent='';
 document.getElementById('extrawrap').innerHTML='';
 const l=LOTTERY||{};
 const prize=l.prize||{};
 const kind=prize.kind||'currency';
 const count=Number(prize.count)||1;
 const curName=(kind==='currency')?(prize.name||'金币'):(prize.name||'');
 const winners=(l.winners||[]).map(esc).join('、')||'（未开奖）';
 const entriesN=l.entries?Object.keys(l.entries).length:0;
 document.getElementById('tablewrap').innerHTML=`
 <div style="max-width:880px">
  <div style="background:#fff;border:1px solid #e8ecf6;border-radius:14px;padding:22px">
   <h3 style="margin:0 0 16px">口令抽奖 <span class="muted" style="font-weight:400">（玩家输入口令参与；到点自动开奖并全群播报）</span></h3>
   <div class="row">
    <label class="fld">启用 <input id="lt_enabled" type="checkbox" ${l.enabled?'checked':''}></label>
    <label class="fld">开奖方式 <select id="lt_mode"><option value="lottery" ${l.mode==='lottery'?'selected':''}>随机抽取</option><option value="claim" ${l.mode==='claim'?'selected':''}>先到先得</option></select></label>
   </div>
   <label class="fld">口令 <input id="lt_password" placeholder="如：一起发财" value="${esc(l.password||'')}"></label>
   <div class="row">
    <label class="fld">份数 / 中奖人数 <input id="lt_quantity" type="number" value="${Number(l.quantity)||10}" style="width:140px"></label>
    <label class="fld">开奖时间 <input id="lt_draw_at" type="datetime-local" value="${eventTsToLocal(l.draw_at)}"></label>
   </div>
   <div class="sec">奖品（全部货币 / 全部道具，二选一）</div>
   <div class="row">
    <label class="fld">类型 <select id="lt_kind" onchange="ltFillName()"><option value="currency" ${kind==='currency'?'selected':''}>货币</option><option value="item" ${kind==='item'?'selected':''}>道具</option></select></label>
    <label class="fld">名称 <select id="lt_name"></select></label>
    <label class="fld">数量 <input id="lt_count" type="number" value="${count}" style="width:110px"></label>
   </div>
   <div class="sec">全群播报文本</div>
   <div class="muted" style="margin:-4px 0 8px">支持占位符：<code>{{password}}</code> <code>{{prize}}</code> <code>{{count}}</code> <code>{{total}}</code> <code>{{mode}}</code> <code>{{winners}}</code>（留空用默认模板）</div>
   <textarea id="lt_broadcast" rows="4" placeholder="留空使用默认开奖公告" style="width:100%;padding:10px 12px;border:1px solid #d8dfef;border-radius:9px;resize:vertical">${esc(l.broadcast_text||'')}</textarea>
   <label class="fld" style="margin-top:12px"><input id="lt_reset" type="checkbox"> 重置为全新抽奖（清空已有报名与结果）</label>
   <div style="margin-top:16px;display:flex;gap:10px">
    <button class="act" onclick="saveLottery()">保存</button>
    <button class="act del" onclick="lotteryDraw()">立即开奖</button>
    <button class="act ghost" onclick="loadLottery()">刷新</button>
   </div>
   <div class="muted" id="lt_msg" style="margin-top:10px"></div>
  </div>
  <div style="margin-top:12px;padding:14px;background:#fff;border:1px solid #e8ecf6;border-radius:12px">
   <span class="muted">报名人数：</span><b>${entriesN}</b>
   &nbsp;&nbsp;<span class="muted">状态：</span><b>${l.drawn?'已开奖':'进行中'}</b>
   &nbsp;&nbsp;<span class="muted">中奖名单：</span><span>${winners}</span>
  </div>
 </div>`;
 ltFillName();
}
function ltFillName(){
 const kind=(g('lt_kind').value||'currency');
 const list=kind==='currency'?(META.currencies||['金币','积分','钻石']):(META.items||[]);
 const cur=((LOTTERY||{}).prize||{}).name||'';
 g('lt_name').innerHTML=optHtml(list, cur, kind==='currency'?'': '道具名称');
}
async function saveLottery(){
 const kind=g('lt_kind').value;
 const cfg={
  enabled:g('lt_enabled').checked,
  mode:g('lt_mode').value,
  password:g('lt_password').value.trim(),
  quantity:Number(g('lt_quantity').value)||0,
  draw_at:eventLocalToTs(g('lt_draw_at').value)||0,
  prize:{kind, name:g('lt_name').value, count:Number(g('lt_count').value)||1},
  broadcast_text:g('lt_broadcast').value.trim(),
 };
 if(!cfg.password){alert('请填写口令');return;}
 if(!cfg.quantity){alert('请填写份数 / 中奖人数');return;}
 const r=await api('/api/lottery/save',{cfg, reset:g('lt_reset').checked});
 g('lt_msg').textContent=r.ok?'✅ 已保存':'❌ 保存失败：'+(r.msg||'');
 if(r.ok) loadLottery();
}
async function lotteryDraw(){
 if(!confirm('确认立即开奖？')) return;
 const r=await api('/api/lottery/draw',{});
 alert(r.ok?(r.msg||'✅ 已开奖'):(r.msg||'开奖失败'));
 loadLottery();
}
// ---- 中元活动（独立模块配置，保存即时生效）----
const ZY_FIELDS=[
 {k:'enabled',label:'活动总开关',t:'bool'},
 {k:'start_at',label:'开始时间(0=不限)',t:'ts'},
 {k:'end_at',label:'结束时间(0=不限)',t:'ts'},
 {k:'open_hour',label:'每日开放小时(含)',t:'num'},
 {k:'close_hour',label:'每日关闭小时(不含)',t:'num'},
 {k:'trigger_interval_min',label:'解密触发间隔(分)',t:'num'},
 {k:'dungeon_limit_min',label:'单场解密时限(分)',t:'num'},
 {k:'bind_open_hours_before',label:'绑定提前开启(时)',t:'num'},
 {k:'bind_close_hours_before',label:'绑定截止(时)',t:'num'},
 {k:'redeem_window_hours',label:'兑换窗口(时)',t:'num'},
 {k:'max_draw_per_day',label:'每人每日被抽上限(0=不限)',t:'num'},
 {k:'max_dungeon_per_day',label:'副本每日开本上限(0=不限)',t:'num'},
 {k:'puzzle_count',label:'每场题数(协作)',t:'num'},
 {k:'answer_cooldown_sec',label:'答对后冷却(秒)',t:'num'},
 {k:'individual_fail_wrong',label:'个人答错出局次数',t:'num'},
 {k:'pull_min_pct',label:'拉入人数下限%',t:'num'},
 {k:'pull_max_pct',label:'拉入人数上限%',t:'num'},
 {k:'gongde_clear',label:'通关基础功德',t:'num'},
 {k:'perfect_reward_mult',label:'完美奖励倍数',t:'num'},
 {k:'lantern_daily_limit',label:'放河灯每日次数',t:'num'},
 {k:'incense_daily_limit',label:'供灯/焚香每日次数',t:'num'},
 {k:'quiz_daily_limit',label:'问答每日次数',t:'num'},
 {k:'lantern_cooldown_min',label:'放河灯冷却(分)',t:'num'},
 {k:'incense_cooldown_min',label:'供灯/焚香冷却(分)',t:'num'},
 {k:'quiz_timeout_sec',label:'问答超时(秒)',t:'num'},
 {k:'gongde_lantern_min',label:'放河灯功德下限',t:'num'},
 {k:'gongde_lantern_max',label:'放河灯功德上限',t:'num'},
 {k:'gongde_incense_min',label:'供灯/焚香功德下限',t:'num'},
 {k:'gongde_incense_max',label:'供灯/焚香功德上限',t:'num'},
 {k:'gongde_quiz_min',label:'问答功德下限',t:'num'},
 {k:'gongde_quiz_max',label:'问答功德上限',t:'num'},
 {k:'gongde_sign',label:'签到功德',t:'num'},
 {k:'yin_penalty_min',label:'阴气缠身时长(分)',t:'num'},
 {k:'yin_clear_cost',label:'解除阴气消耗',t:'num'},
 {k:'deepseek_enabled',label:'启用 DeepSeek',t:'bool'},
 {k:'deepseek_model',label:'DeepSeek 模型',t:'txt'},
 {k:'deepseek_base_url',label:'接口地址',t:'txt'},
 {k:'deepseek_api_key',label:'API Key',t:'pwd'},
 {k:'deepseek_temperature',label:'温度',t:'num',step:'0.1'},
 {k:'deepseek_max_tokens',label:'最大 tokens',t:'num'},
 {k:'deepseek_timeout',label:'超时(秒)',t:'num'},
];
let ZY_CFG=null;
async function loadZhongyuan(){
 const r=await api('/api/zhongyuan/config',{});
 ZY_CFG=(r&&r.ok)?r.data:null;
 renderZhongyuan();
}
function renderZhongyuan(){
 document.getElementById('count').textContent='';
 document.getElementById('extrawrap').innerHTML='';
 const c=ZY_CFG||{};
 let rows='';
 for(const f of ZY_FIELDS){
  const v=c[f.k];
  let inp;
  if(f.t==='bool') inp=`<input id="zy_${f.k}" type="checkbox" ${v?'checked':''}>`;
  else if(f.t==='ts') inp=`<input id="zy_${f.k}" type="datetime-local" value="${eventTsToLocal(v||0)}">`;
  else if(f.t==='pwd') inp=`<input id="zy_${f.k}" type="password" autocomplete="off" placeholder="${v?'已设置（留空不修改）':'未设置'}" value="">`;
  else if(f.t==='num') inp=`<input id="zy_${f.k}" type="number" step="${f.step||'1'}" value="${(v===undefined||v===null)?'':v}">`;
  else inp=`<input id="zy_${f.k}" value="${esc(v==null?'':v)}">`;
  rows+=`<label class="fld">${f.label} ${inp}</label>`;
 }
 const tiers=JSON.stringify(c.tiers||[],null,2);
 const miles=JSON.stringify(c.milestones||[],null,2);
 document.getElementById('tablewrap').innerHTML=`
 <div style="max-width:960px">
  <div style="background:#fff;border:1px solid #e8ecf6;border-radius:14px;padding:22px">
   <h3 style="margin:0 0 16px">🕯️ 中元节活动 <span class="muted" style="font-weight:400">（独立模块配置，保存即时生效）</span></h3>
   <div class="sec">总控 / 时间 / 抽人 / 解密 / 功德</div>
   <div class="row">${rows}</div>
   <div class="sec">段位（前 20 名功德奖励，JSON：name / min / max / gongde）</div>
   <textarea id="zy_tiers" rows="5" style="width:100%;padding:10px 12px;border:1px solid #d8dfef;border-radius:9px;resize:vertical;font-family:monospace">${esc(tiers)}</textarea>
   <div class="sec">群里程碑（累计功德达标，JSON：threshold / gongde）</div>
   <textarea id="zy_milestones" rows="5" style="width:100%;padding:10px 12px;border:1px solid #d8dfef;border-radius:9px;resize:vertical;font-family:monospace">${esc(miles)}</textarea>
   <div style="margin-top:16px;display:flex;gap:10px;flex-wrap:wrap">
    <button class="act" onclick="saveZhongyuan()">保存配置</button>
    <button class="act ghost" onclick="loadZhongyuan()">刷新</button>
    <button class="act ghost" onclick="testDeepSeek()">测试 DeepSeek 连接</button>
    <button class="act ghost" onclick="testZhongyuanBroadcast()">全群通报测试</button>
    <button class="act ghost" onclick="testZhongyuanStart()">测试活动开始全群播放</button>
    <button class="act ghost" onclick="testZhongyuanEnd()">测试活动结束全群播放</button>
    <button class="act ghost" onclick="viewZhongyuanData()">查看中元所有数据</button>
    <button class="act del" onclick="clearZhongyuanData()">清空中元玩家数据</button>
   </div>
   <div class="muted" id="zy_msg" style="margin-top:10px"></div>
   <div class="muted" id="zy_test_msg" style="margin-top:6px"></div>
   <div class="muted" id="zy_data_msg" style="margin-top:6px"></div>
   <textarea id="zy_data_box" rows="16" style="display:none;width:100%;margin-top:8px;padding:10px 12px;border:1px solid #d8dfef;border-radius:9px;font-family:monospace;font-size:12px;white-space:pre" readonly></textarea>
  </div>
 </div>`;
}
async function saveZhongyuan(){
 const cfg={};
 for(const f of ZY_FIELDS){
  const el=g('zy_'+f.k);
  if(f.t==='bool') cfg[f.k]=el.checked;
  else if(f.t==='pwd'){const v=el.value.trim(); if(v!=='') cfg[f.k]=v;} // 留空 = 不修改
  else if(f.t==='ts') cfg[f.k]=eventLocalToTs(el.value)||0;
  else if(f.t==='num'){const raw=el.value;cfg[f.k]=(raw===''?(ZY_CFG&&ZY_CFG[f.k]!==undefined?ZY_CFG[f.k]:0):parseFloat(raw));}
  else cfg[f.k]=el.value.trim();
 }
 try{cfg.tiers=JSON.parse(g('zy_tiers').value);}catch(e){alert('段位 JSON 解析失败：'+e.message);return;}
 try{cfg.milestones=JSON.parse(g('zy_milestones').value);}catch(e){alert('里程碑 JSON 解析失败：'+e.message);return;}
 const r=await api('/api/zhongyuan/config/save',{config:cfg});
 const msg=g('zy_msg');
 if(!r){msg.textContent='❌ 保存失败：无响应';return;}
 msg.textContent=r.ok?('✅ 已保存'+(r.bad&&r.bad.length?'（跳过：'+r.bad.join(', ')+'）':'')):('❌ 保存失败：'+(r.msg||'未知错误'));
 if(r.ok) loadZhongyuan();
}
// ---- 自定义文本群推送（手动 / 定时一次性 / 定时循环）----
let PUSH_DATA=null;
async function loadPush(){
 const r=await api('/api/push/state',{});
 PUSH_DATA=(r&&r.ok)?r.data:{jobs:[]};
 renderPush();
}
function pushModeChange(){
 const el=g('push_mode'); const m=el?el.value:'once';
 const once=g('push_once_row'), rec=g('push_recur_row');
 if(once) once.style.display=(m==='once')?'':'none';
 if(rec) rec.style.display=(m==='recurring')?'':'none';
}
function renderPush(){
 document.getElementById('count').textContent='';
 document.getElementById('extrawrap').innerHTML='';
 const jobs=(PUSH_DATA&&PUSH_DATA.jobs)||[];
 let rows='';
 for(const j of jobs){
  const lr=j.last_result||{};
  let lrs='（未推送）';
  if(lr.ts){
   lrs=`${fdate(lr.ts)} · 目标${lr.targets??'—'}/成功${lr.sent??'—'}/失败${lr.failed??'—'}`+(lr.error?(' · '+esc(lr.error)):'');
  }
  let when='';
  if(j.mode==='once') when=j.done?('已完成 · '+fdate(j.target_ts)):('到点 '+fdate(j.target_ts));
  else when=('下次 '+fdate(j.next_run)+' · 每'+j.interval_min+'分钟');
  const stCol=j.enabled?'<b style="color:#17a05e">启用</b>':'<span class="muted">停用</span>';
  const modeCol=j.mode==='once'?'一次性':'循环';
  const statusBtn=j.enabled?'停用':'启用';
  rows+=`<tr>
   <td class="muted">${esc(j.id.slice(-8))}</td>
   <td>${modeCol}</td>
   <td style="max-width:200px;overflow-wrap:anywhere">${esc(j.text)}</td>
   <td>${when}</td>
   <td class="muted" style="font-size:12px">${lrs}</td>
   <td>${stCol}</td>
   <td>
    <button class="act ghost" onclick="pushToggle('${escA(j.id)}',${!j.enabled})">${statusBtn}</button>
    <button class="act ghost" onclick="pushFire('${escA(j.id)}')">触发</button>
    <button class="act del" onclick="pushDelete('${escA(j.id)}')">删除</button>
   </td></tr>`;
 }
 const tableHtml=rows?`<table><thead><tr><th>ID</th><th>模式</th><th>文案</th><th>排程</th><th>最近结果</th><th>状态</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table>`:'<div class="empty">暂无定时任务</div>';
 document.getElementById('tablewrap').innerHTML=`
 <div style="max-width:960px">
  <div style="background:#fff;border:1px solid #e8ecf6;border-radius:14px;padding:22px">
   <h3 style="margin:0 0 16px">📣 自定义文本群推送 <span class="muted" style="font-weight:400">（推送到所有已授权且开启宠物乐园玩法的群）</span></h3>
   <div class="row">
    <label class="fld">模式 <select id="push_mode" onchange="pushModeChange()">
      <option value="once">指定时间发送（一次性）</option>
      <option value="recurring">定时循环（每隔 N 分钟）</option>
    </select></label>
    <label class="fld">任务名称 <input id="push_name" placeholder="可选" style="width:180px"></label>
   </div>
   <label class="fld">推送文案 <textarea id="push_text" rows="4" placeholder="推送给所有群的内容，支持 Markdown：用空行/列表分段，勿用单个换行" style="width:100%;padding:10px 12px;border:1px solid #d8dfef;border-radius:9px;resize:vertical"></textarea></label>
   <div class="row">
    <label class="fld" id="push_once_row">指定时间 <input id="push_at" type="datetime-local"></label>
    <label class="fld" id="push_recur_row" style="display:none">间隔(分钟) <input id="push_interval" type="number" value="30" style="width:100px"></label>
   </div>
   <div style="margin-top:16px;display:flex;gap:10px;flex-wrap:wrap">
    <button class="act" onclick="pushManual()">立即推送到所有授权群</button>
    <button class="act" onclick="pushSave()">新建定时任务</button>
    <button class="act ghost" onclick="loadPush()">刷新</button>
   </div>
   <div class="muted" id="push_msg" style="margin-top:10px"></div>
  </div>
  <div style="margin-top:12px;background:#fff;border:1px solid #e8ecf6;border-radius:14px;padding:10px 14px">
   <div class="sec">定时任务列表</div>
   ${tableHtml}
  </div>
 </div>`;
 pushModeChange();
}
async function pushManual(){
 const text=g('push_text').value.trim();
 if(!text){alert('请填写推送文案'); return;}
 const r=await api('/api/push/manual',{text});
 const m=g('push_msg'); if(m) m.textContent=(r.ok?'✅ ':'❌ ')+(r.msg||'');
}
async function pushSave(){
 const mode=g('push_mode').value;
 const text=g('push_text').value.trim();
 const name=g('push_name').value.trim();
 if(!text){alert('请填写推送文案'); return;}
 const body={mode,text,name};
 if(mode==='once'){
  body.target_ts=eventLocalToTs(g('push_at').value)||0;
  if(!body.target_ts){alert('请选择指定时间'); return;}
 } else {
  body.interval_min=Number(g('push_interval').value)||0;
  if(!body.interval_min){alert('请填写间隔分钟'); return;}
 }
 const r=await api('/api/push/save',body);
 const m=g('push_msg'); if(m) m.textContent=(r.ok?'✅ ':'❌ ')+(r.msg||'');
 if(r.ok) loadPush();
}
async function pushToggle(id,en){
 const r=await api('/api/push/toggle',{id,enabled:en});
 const m=g('push_msg'); if(m) m.textContent=(r.ok?'✅ ':'❌ ')+(r.msg||'');
 if(r.ok) loadPush();
}
async function pushFire(id){
 if(!confirm('确认立即触发推送到所有授权群？')) return;
 const r=await api('/api/push/fire',{id});
 alert(r.ok?(r.msg||'已触发'):(r.msg||'触发失败'));
 if(r.ok) loadPush();
}
async function pushDelete(id){
 if(!confirm('确认删除该定时任务？')) return;
 const r=await api('/api/push/delete',{id});
 const m=g('push_msg'); if(m) m.textContent=(r.ok?'✅ ':'❌ ')+(r.msg||'');
 if(r.ok) loadPush();
}
// --------------------------- 生辰盛典（每日定时开奖箱 + 奖池瓜分）后台 ---------------------------
let CELEBRATE=null;
async function loadCelebrate(){
 const r=await api('/api/celebrate/state',{});
 CELEBRATE=(r&&r.ok)?r.data:{};
 if(!CELEBRATE.gacha) CELEBRATE.gacha={cmd:'生辰抽奖',menu_cmd:'生辰活动',rounds:[]};
 if(!CELEBRATE.pool) CELEBRATE.pool={cmd:'生辰瓜分',cooldown_min:30,currencies:{}};
 renderCelebrate();
}
function ceRoundDrawAt(timeStr){
 if(!timeStr) return 0;
 const s=Number((CELEBRATE&&CELEBRATE.start_at)||0);
 const base=s?new Date(s*1000):new Date();
 const p=String(timeStr).split(':');
 base.setHours(Number(p[0])||0, Number(p[1])||0, 0, 0);
 return Math.floor(base.getTime()/1000);
}
function ceRoundRow(r,i){
 r=r||{};
 const g0=(r.grand||{}), n0=(r.normal||{});
 const drawn=r.drawn?`<span style="color:#17a05e">✓已开奖</span>`:'<span class="muted">未开奖</span>';
 return `<tr>
  <td class="muted">${drawn}</td>
  <td><input type="time" value="${esc(r.time||'')}" id="ce_rt${i}" style="width:105px"></td>
  <td><input value="${esc(g0.item||'')}" id="ce_rg${i}" placeholder="大奖名" style="width:115px"></td>
  <td><input type="number" min="1" value="${g0.count||1}" id="ce_rgc${i}" style="width:64px"></td>
  <td><input value="${esc(n0.item||'')}" id="ce_rn${i}" placeholder="普通奖" style="width:115px"></td>
  <td><input type="number" min="1" value="${n0.count||1}" id="ce_rnc${i}" style="width:64px"></td>
  <td><input type="number" min="0" value="${r.normal_winners||0}" id="ce_rnw${i}" style="width:64px"></td>
  <td><button class="act del" onclick="ceDelRound(${i})">删</button></td>
 </tr>`;
}
function renderCelebrate(){
 document.getElementById('count').textContent='';
 document.getElementById('extrawrap').innerHTML='';
 const c=CELEBRATE||{};
 const ga=c.gacha||{}, po=c.pool||{};
 const rounds=ga.rounds||[];
 let rrows='';
 for(let i=0;i<rounds.length;i++) rrows+=ceRoundRow(rounds[i],i);
 const cur=po.currencies||{};
 let curRows='';
 for(const nm of ['积分','金币','钻石']){
  const cc=cur[nm]||{};
  curRows+=`<tr>
   <td><input value="${esc(nm)}" style="width:80px" readonly></td>
   <td><input type="number" min="0" value="${cc.total||0}" id="ce_ct_${nm}" style="width:110px"></td>
   <td><input type="number" min="0" value="${cc.min||0}" id="ce_cmin_${nm}" style="width:90px"></td>
   <td><input type="number" min="0" value="${cc.max||0}" id="ce_cmax_${nm}" style="width:90px"></td></tr>`;
 }
 const st=c.start_at||0, en=c.end_at||0;
 document.getElementById('tablewrap').innerHTML=`
 <div style="max-width:1000px">
  <div style="background:#fff;border:1px solid #e8ecf6;border-radius:14px;padding:22px">
   <h3 style="margin:0 0 16px">🎂 ${esc(c.name||'生辰盛典')} 后台配置 <span class="muted" style="font-weight:400">（每日多次定时开奖箱 + 奖池瓜分）</span></h3>
   <div class="row">
    <label class="fld">启用 <input type="checkbox" id="ce_on" ${c.enabled?'checked':''}></label>
    <label class="fld">名称 <input id="ce_name" value="${esc(c.name||'生辰盛典')}" style="width:150px"></label>
   </div>
   <div class="row">
    <label class="fld">开始 <input id="ce_start" type="datetime-local" value="${eventTsToLocal(st)}"></label>
    <label class="fld">结束 <input id="ce_end" type="datetime-local" value="${eventTsToLocal(en)}"></label>
   </div>
   <label class="fld">开启公告 <textarea id="ce_ann" rows="2" style="width:100%;padding:10px 12px;border:1px solid #d8dfef;border-radius:9px">${esc(c.announce||'')}</textarea></label>
   <label class="fld" style="margin-top:10px">结束公告 <textarea id="ce_ann_end" rows="2" style="width:100%;padding:10px 12px;border:1px solid #d8dfef;border-radius:9px">${esc(c.announce_end||'')}</textarea></label>
   <div class="sec" style="margin-top:16px">📣 每小时推送「如何参与」（盛典窗口内循环提醒）</div>
   <div class="row">
    <label class="fld" style="width:auto">间隔(小时) <input type="number" min="0" value="${c.howto_interval_h||0}" id="ce_how_ih" style="width:90px"></label>
    <label class="fld" style="flex:1;min-width:280px">文案 <textarea id="ce_how" rows="3" style="width:100%;padding:10px 12px;border:1px solid #d8dfef;border-radius:9px">${esc(c.howto||'')}</textarea></label>
   </div>
   <div style="color:#9aa3b8;font-size:12px">填 1 表示开奖期间每 1 小时全群推一次此文案；填 0 关闭。</div>
   <div class="sec" style="margin-top:18px">🎯 抽奖开奖箱（每场到点自动抽 1 位大奖 + N 位普通奖）</div>
   <div class="row">
    <label class="fld">指令 <input id="ce_gcmd" value="${esc(ga.cmd||'生辰抽奖')}" style="width:150px"></label>
    <label class="fld">菜单 <input id="ce_gmenu" value="${esc(ga.menu_cmd||'生辰活动')}" style="width:150px"></label>
    <button class="act ghost" onclick="ceAddRound()">+ 加一场（共 ${rounds.length} 场）</button>
   </div>
   ${rounds.length?`<table><thead><tr><th>状态</th><th>时间</th><th>大奖名</th><th>数</th><th>普通奖</th><th>数</th><th>人数</th><th></th></tr></thead><tbody>${rrows}</tbody></table>`:'<div class="empty">尚未配置开奖场次，点击上方「加一场」。</div>'}
   <div style="color:#9aa3b8;font-size:12px;margin-top:6px">时间填 HH:MM，开奖日期取「开始」时间的当天；20:00 那场放「宠物定制卡」作为最大奖。</div>
   <div class="sec" style="margin-top:20px">💰 奖池瓜分（${esc(po.cmd||'生辰瓜分')}）</div>
   <div class="row">
    <label class="fld">启用瓜分 <input type="checkbox" id="ce_pon" ${po.enabled===false?'':'checked'}></label>
    <label class="fld">指令 <input id="ce_pcmd" value="${esc(po.cmd||'生辰瓜分')}" style="width:150px"></label>
    <label class="fld">冷却(分钟) <input type="number" min="1" value="${po.cooldown_min||30}" id="ce_pcd" style="width:90px"></label>
   </div>
   <table><thead><tr><th>货币</th><th>总量</th><th>单次最少</th><th>单次最多</th></tr></thead><tbody>${curRows||'<tr><td colspan="4" class="muted">未配置</td></tr>'}</tbody></table>
   <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:16px">
    <button class="act" onclick="saveCelebrate()">保存配置</button>
    <button class="act ghost" onclick="resetPool()">重置奖池剩余</button>
    <button class="act ghost" onclick="broadcastAnnounce('start')">广播开启公告</button>
    <button class="act ghost" onclick="broadcastAnnounce('end')">广播结束公告</button>
    <button class="act ghost" onclick="loadCelebrate()">刷新</button>
   </div>
   <div class="muted" id="ce_msg" style="margin-top:10px"></div>
  </div>
 </div>`;
}
function ceAddRound(){ const c=CELEBRATE||{}; c.gacha=c.gacha||{cmd:'生辰抽奖',menu_cmd:'生辰活动',rounds:[]}; c.gacha.rounds.push({time:'',draw_at:0,grand:{item:'',count:1},normal:{item:'',count:1},normal_winners:0}); renderCelebrate(); }
function ceDelRound(i){ const c=CELEBRATE||{}; if(c.gacha&&c.gacha.rounds){ c.gacha.rounds.splice(i,1); renderCelebrate(); } }
async function saveCelebrate(){
 const c=CELEBRATE||{};
 const body={celebrate:{
  enabled: g('ce_on')?g('ce_on').checked:false,
  name: g('ce_name').value,
  start_at: eventLocalToTs(g('ce_start').value)||0,
  end_at: eventLocalToTs(g('ce_end').value)||0,
  announce: g('ce_ann').value,
  announce_end: g('ce_ann_end').value,
  howto: g('ce_how').value,
  howto_interval_h: Number(g('ce_how_ih').value)||0,
  gacha:{enabled:true, cmd:g('ce_gcmd').value||'生辰抽奖', menu_cmd:g('ce_gmenu').value||'生辰活动', rounds:[]},
  pool:{enabled:g('ce_pon')?g('ce_pon').checked:true, cmd:g('ce_pcmd').value||'生辰瓜分', cooldown_min:Number(g('ce_pcd').value)||30, currencies:{}}
 }};
 const nRounds=((c.gacha||{}).rounds||[]).length;
 for(let i=0;i<nRounds;i++){
  const tEl=document.getElementById('ce_rt'+i); if(!tEl) continue;
  const time=tEl.value;
  body.celebrate.gacha.rounds.push({time,
   draw_at: ceRoundDrawAt(time),
   grand:{item:g('ce_rg'+i).value, count:Number(g('ce_rgc'+i).value)||1},
   normal:{item:g('ce_rn'+i).value, count:Number(g('ce_rnc'+i).value)||1},
   normal_winners:Number(g('ce_rnw'+i).value)||0});
 }
 for(const nm of ['积分','金币','钻石']){
  const tEl=document.getElementById('ce_ct_'+nm); if(!tEl) continue;
  body.celebrate.pool.currencies[nm]={total:Number(tEl.value)||0, min:Number(g('ce_cmin_'+nm).value)||0, max:Number(g('ce_cmax_'+nm).value)||0};
 }
 const r=await api('/api/celebrate/save',body);
 const m=g('ce_msg'); if(m) m.textContent=(r.ok?'✅ ':'❌ ')+(r.msg||'');
 if(r.ok) loadCelebrate();
}
async function resetPool(){
 if(!confirm('确认将奖池剩余重置回配置总额？')) return;
 const r=await api('/api/celebrate/reset_pool',{});
 const m=g('ce_msg'); if(m) m.textContent=(r.ok?'✅ ':'❌ ')+(r.msg||'');
}
async function broadcastAnnounce(which){
 if(!confirm(which==='start'?'确认向所有已授权群真实广播「开启公告」？':'确认向所有已授权群真实广播「结束公告」？')) return;
 const r=await api('/api/celebrate/broadcast',{which});
 alert(r.ok?(r.msg||'已广播'):(r.msg||'广播失败'));
}
async function testDeepSeek(){
 const msg=g('zy_test_msg'); if(msg) msg.textContent='⏳ 正在测试连接…';
 const r=await api('/api/zhongyuan/test_deepseek',{});
 if(msg) msg.textContent=r?(r.ok?'✅ '+r.msg:'❌ '+r.msg):'❌ 测试失败：无响应';
}
async function testZhongyuanBroadcast(){
 if(!confirm('将向所有已注册群真实推送一条测试消息，确认继续？')) return;
 const msg=g('zy_test_msg'); if(msg) msg.textContent='⏳ 正在广播…';
 const r=await api('/api/zhongyuan/test_broadcast',{});
 if(msg) msg.textContent=r?(r.ok?'✅ '+r.msg:'❌ '+r.msg):'❌ 广播失败：无响应';
}

async function testZhongyuanStart(){
 if(!confirm('将向所有已注册群真实推送「活动开始」通报（不更改活动状态），确认继续？')) return;
 const msg=g('zy_test_msg'); if(msg) msg.textContent='⏳ 正在推送「活动开始」通报…';
 const r=await api('/api/zhongyuan/test_start',{});
 if(msg) msg.textContent=r?(r.ok?'✅ '+r.msg:'❌ '+r.msg):'❌ 推送失败：无响应';
}
async function testZhongyuanEnd(){
 if(!confirm('将向所有已注册群真实推送「活动结束」通报（不结算、不更改状态），确认继续？')) return;
 const msg=g('zy_test_msg'); if(msg) msg.textContent='⏳ 正在推送「活动结束」通报…';
 const r=await api('/api/zhongyuan/test_end',{});
 if(msg) msg.textContent=r?(r.ok?'✅ '+r.msg:'❌ '+r.msg):'❌ 推送失败：无响应';
}
async function viewZhongyuanData(){
 const msg=g('zy_data_msg'); const box=g('zy_data_box');
 if(msg) msg.textContent='⏳ 正在读取中元数据…';
 const r=await api('/api/zhongyuan/data',{});
 if(!r){ if(msg) msg.textContent='❌ 读取失败：无响应'; return; }
 if(!r.ok){ if(msg) msg.textContent='❌ '+(r.msg||'读取失败'); return; }
 if(msg) msg.textContent='✅ 已读取：玩家 '+r.stats.players+' · 群 '+r.stats.groups+' · 进行中副本 '+r.stats.sessions;
 if(box){ box.value=JSON.stringify(r.data,null,2); box.style.display='block'; }
}
async function clearZhongyuanData(){
 if(!confirm('⚠️ 将清空中元所有玩家数据（玩家 / 群 / 副本 + 活动 ID 从 1 重新分配），配置保留。此操作不可撤销，确认继续？')) return;
 if(!confirm('再次确认：真的要删除当前中元全部玩家数据吗？')) return;
 const msg=g('zy_test_msg'); if(msg) msg.textContent='⏳ 正在清空…';
 const r=await api('/api/zhongyuan/clear_data',{});
 if(msg) msg.textContent=r?(r.ok?'✅ '+r.msg:'❌ '+r.msg):'❌ 清空失败：无响应';
 if(r&&r.ok) viewZhongyuanData();
}

function shell(head,rows,cols){
 document.getElementById('count').textContent='共 '+Object.keys(cache).length+' 条';
 document.getElementById('extrawrap').innerHTML='';
 document.getElementById('tablewrap').innerHTML = rows
   ? `<table><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>`
   : `<div class="empty">暂无数据</div>`;
}
function petEditInfo(v){
 // 多宠物系统：宠物统一存储在 v.pets 列表，返回要编辑的 (列表, active_pet 索引)；无宠物返回 (-1, null)
 const pets=Array.isArray(v.pets)?v.pets:null;
 if(!pets||!pets.length)return {idx:-1,pets:null};
 let idx=v.active_pet;
 if(!Number.isInteger(idx)||idx<0||idx>=pets.length)idx=0;
 return {idx:idx,pets:pets};
}
function renderPlayers(){
 let rows='';
 for(const k of Object.keys(cache)){const v=cache[k];if(!match(k,v))continue;
  const pi=petEditInfo(v);
  const pet=(pi.idx>=0&&pi.pets[pi.idx])?`${esc(pi.pets[pi.idx].name||pi.pets[pi.idx].species||'宠物')}`:'—';
  const lv=(pi.idx>=0&&pi.pets[pi.idx])?('Lv'+(pi.pets[pi.idx].level||1)):'—';
  rows+=`<tr>
   <td>${esc(v.group||'')}</td><td class="num">${esc(v.qq||'')}</td>
   <td>${pet}</td><td class="num">${lv}</td>
   <td class="num coin">${v.coin||0}</td><td class="num jifen">${v.jifen||0}</td><td class="num diamond">${v.diamond||0}</td>
   <td style="white-space:nowrap"><button class="act" onclick='editRow(${tj(k)})'>编辑</button> <button class="act del" onclick='delRow(${tj(k)})'>删除</button></td></tr>`;}
 shell('<th>群号</th><th>QQ号</th><th>宠物</th><th>等级</th><th>金币</th><th>积分</th><th>钻石</th><th>操作</th>',rows);
}
function renderGroups(){
 let rows='';
 for(const k of Object.keys(cache)){const v=cache[k];if(!match(k,v))continue;
  const sect=v.sect||{};
  rows+=`<tr><td class="k">${esc(k)}</td>
   <td><span class="tag ${v.enabled?'on':'off'}">${v.enabled?'已开启':'已关闭'}</span></td>
   <td><span class="tag ${v.cross?'on':'off'}">${v.cross?'允许':'禁止'}</span></td>
   <td>${esc(sect.name||'—')}</td>
   <td class="num">${sect.level||1}</td>
   <td class="num">${sect.points||0}</td>
   <td class="num">${v.sign_count||0}</td>
   <td style="white-space:nowrap"><button class="act" onclick='editRow(${tj(k)})'>编辑</button> <button class="act del" onclick='delRow(${tj(k)})'>删除</button></td></tr>`;}
 shell('<th>群号</th><th>宠物乐园</th><th>跨群挑战</th><th>宗门名</th><th>宗门等级</th><th>宗门积分</th><th>今日签到数</th><th>操作</th>',rows);
}
const CUR_CLS={'金币':'coin','积分':'jifen','钻石':'diamond'};
function cardRewards(v){
 if(v.rewards&&typeof v.rewards==='object')return v.rewards;
 if(v.currency&&v.amount)return {[v.currency]:v.amount};
 return {};
}
function cardItems(v){
 if(v.items&&typeof v.items==='object')return v.items;
 return {};
}
function rewardsHtml(r){
 const parts=[];for(const c of ['金币','积分','钻石'])if(r[c])parts.push(`<span class="${CUR_CLS[c]}">${c} +${r[c]}</span>`);
 return parts.length?parts.join(' ＋ '):'';
}
function itemsHtml(items){
 const parts=[];for(const [name,cnt] of Object.entries(items||{}))if(cnt>0)parts.push(`<span class="muted">📦 ${name} ×${cnt}</span>`);
 return parts.join(' ＋ ');
}
function packageHtml(v){
 const r=rewardsHtml(cardRewards(v));
 const i=itemsHtml(cardItems(v));
 const parts=[];if(r)parts.push(r);if(i)parts.push(i);
 return parts.length?parts.join(' ＋ '):'<span class="muted">—</span>';
}
function cardContentHtml(v){
 const acDays=+(v.auto_cultivation_days||0);
 if(acDays>0)return `<span class="diamond">🧘 自动修炼 ${acDays} 天</span>`;
 const days=+(v.auth_days||0);
 if(days>0)return `<span class="diamond">🔐 群授权 ${days} 天</span>`;
 return packageHtml(v);
}
function renderCards(){
 let total=0,used=0;
 let rows='';
 for(const k of Object.keys(cache)){const v=cache[k];total++;if(v.used)used++;if(!match(k,v))continue;
  rows+=`<tr><td><input type="checkbox" class="cardchk" value="${esc(k)}"></td><td class="k">${esc(k)}</td>
   <td>${cardContentHtml(v)}</td>
   <td><span class="tag ${v.used?'used':'unused'}">${v.used?'已使用':'未使用'}</span></td>
   <td class="muted">${v.used_by?esc(v.used_by.replace(String.fromCharCode(31),' / ')):'—'}</td>
   <td class="muted">${fdate(v.created_at)}</td>
   <td style="white-space:nowrap"><button class="act" onclick='editRow(${tj(k)})'>编辑</button> <button class="act del" onclick='delRow(${tj(k)})'>删除</button></td></tr>`;}
 document.getElementById('cardstats').innerHTML=`<div class="stat"><div class="n">${total}</div><div class="l">卡密总数</div></div><div class="stat"><div class="n">${total-used}</div><div class="l">未使用</div></div><div class="stat"><div class="n">${used}</div><div class="l">已使用</div></div>`;
 shell('<th><input type="checkbox" onclick="cardsToggleAll(this)"></th><th>卡密</th><th>套餐内容</th><th>状态</th><th>使用者</th><th>创建时间</th><th>操作</th>',rows);
}
function cardsToggleAll(box){document.querySelectorAll('.cardchk').forEach(c=>c.checked=box.checked);}
async function cardsDeleteSelected(){
 if(cur!=='cards'){alert('请先切换到卡密页');return;}
 const keys=[...document.querySelectorAll('.cardchk:checked')].map(c=>c.value);
 if(!keys.length){alert('请先勾选要删除的卡密');return;}
 if(!confirm('确认删除选中的 '+keys.length+' 个卡密？'))return;
 const r=await api('/api/cards/batch_delete',{keys});
 alert(r.ok?('已删除 '+r.deleted+' 个卡密'):(r.msg||'删除失败'));
 load();
}
async function cardsDeleteUsed(){
 if(cur!=='cards'){alert('请先切换到卡密页');return;}
 if(!confirm('确认删除全部已使用的卡密？'))return;
 const r=await api('/api/cards/batch_delete',{mode:'used'});
 alert(r.ok?('已删除 '+r.deleted+' 个已使用卡密'):(r.msg||'删除失败'));
 load();
}
function eventDate(ts){
 if(!ts)return '—';
 return new Date(ts*1000).toLocaleString('zh-CN',{hour12:false});
}
function renderEvents(){
 let rows='';
 for(const k of Object.keys(cache)){const v=cache[k];if(!match(k,v))continue;
  const now=Math.floor(Date.now()/1000);
  const active=!!v.enabled && v.start_at<=now && now<=v.end_at;
  rows+=`<tr><td class="k">${esc(k)}</td>
   <td>${esc(v.name||'—')}</td>
   <td><span class="tag ${active?'on':'off'}">${active?'生效中':(v.enabled?'未生效':'已禁用')}</span></td>
   <td>${esc(v.token||'—')}</td>
   <td class="muted">${eventDate(v.start_at)}</td>
   <td class="muted">${eventDate(v.end_at)}</td>
   <td class="num">${Object.keys(v.actions||{}).length} / ${Object.keys(v.shop||{}).length} / ${((v.gacha||{}).pool||[]).length}</td>
   <td style="white-space:nowrap"><button class="act" onclick='editRow(${tj(k)})'>编辑</button> <button class="act del" onclick='delRow(${tj(k)})'>删除</button></td></tr>`;}
 shell('<th>ID</th><th>名称</th><th>状态</th><th>代币</th><th>开始</th><th>结束</th><th>玩法/商店/奖品</th><th>操作</th>',rows);
 document.getElementById('extrawrap').innerHTML=`
  <div class="sec" style="margin-top:14px">Boss 管理</div>
  <div class="bar" style="align-items:flex-end">
   <input id="boss_respawn_id" placeholder="活动ID" style="width:220px">
   <button class="act" onclick="bossRespawn()">立即复活该活动 Boss 并全服播报</button>
  </div>
  <div id="boss_respawn_msg" class="muted"></div>`;
}
function fieldHtml(){
 if(cur==='players')return `
  <div class="sec">基础</div>
  <div class="row"><div><label class="fld">金币</label><input id="f_coin" type="number"></div>
  <div><label class="fld">积分</label><input id="f_jifen" type="number"></div>
  <div><label class="fld">钻石</label><input id="f_diamond" type="number"></div></div>
  <div class="row"><div><label class="fld">胜场</label><input id="f_st_win" type="number"></div>
  <div><label class="fld">探索次数</label><input id="f_st_exp" type="number"></div></div>
  <div class="sec">宠物</div><div id="petbox"></div>
  <div class="sec">背包</div><div id="bagbox"></div>
  <button class="act ghost" type="button" onclick="bagAdd()" style="margin-top:6px">＋ 添加物品</button>`;
 if(cur==='groups')return `
  <div class="sec">基础设置</div>
  <div class="chk"><input id="f_enabled" type="checkbox"><label for="f_enabled">开启宠物乐园</label></div>
  <div class="chk"><input id="f_cross" type="checkbox"><label for="f_cross">允许跨群挑战</label></div>
  <div class="chk"><input id="f_sect_enabled" type="checkbox"><label for="f_sect_enabled">参加宗门战</label></div>
  <div class="sec">宗门信息</div>
  <div class="row">
   <div style="flex:2"><label class="fld">宗门名</label><input id="f_sect_name" placeholder="宗门展示名"></div>
   <div><label class="fld">宗门等级</label><input id="f_sect_level" type="number" placeholder="1"></div>
  </div>
  <div class="row">
   <div><label class="fld">宗门积分</label><input id="f_sect_points" type="number" placeholder="0"></div>
   <div><label class="fld">本赛季积分</label><input id="f_sect_season_points" type="number" placeholder="0"></div>
   <div><label class="fld">宗门经验</label><input id="f_sect_exp" type="number" placeholder="0"></div>
  </div>
  <div class="row">
   <div><label class="fld">宗主QQ</label><input id="f_sect_master" placeholder="用户ID"></div>
   <div style="flex:2"><label class="fld">副宗主QQ（逗号分隔）</label><input id="f_sect_deputies" placeholder="a,b,c"></div>
  </div>
  <div><label class="fld">宗门公告</label><input id="f_sect_notice" placeholder="宗门公告"></div>`;
 if(cur==='events')return `
  <div class="muted">ID 保存后不可修改；活动时间选择本地日期，后台自动转时间戳。</div>
  <div class="row">
   <div style="flex:2"><label class="fld">活动名称</label><input id="f_name" placeholder="秋收冬藏"></div>
   <div><label class="fld">主题</label><input id="f_theme" placeholder="autumn"></div>
  </div>
  <div class="row">
   <div><label class="fld">菜单指令</label><input id="f_menu_cmd" placeholder="秋冬活动"></div>
   <div><label class="fld">代币名</label><input id="f_token" placeholder="银杏叶"></div>
  </div>
  <div class="row">
   <div><label class="fld">副本列表指令</label><input id="f_dungeon_list_cmd" placeholder="活动副本"></div>
   <div><label class="fld">进入副本指令</label><input id="f_dungeon_enter_cmd" placeholder="进入活动副本"></div>
  </div>
  <div class="chk"><input id="f_enabled" type="checkbox"><label for="f_enabled">启用</label></div>
  <div class="row">
   <div><label class="fld">开始时间</label><input id="f_start_at" type="datetime-local"></div>
   <div><label class="fld">结束时间</label><input id="f_end_at" type="datetime-local"></div>
  </div>
  <div class="sec">活动玩法</div>
  <div id="event_actions"></div>
  <button class="act ghost" type="button" onclick="eventAddAction()" style="margin-top:6px">＋ 添加玩法</button>
  <div class="sec">活动道具（自定义）</div>
  <div id="event_items"></div>
  <button class="act ghost" type="button" onclick="eventAddItem()" style="margin-top:6px">＋ 添加活动道具</button>
  <div class="sec">活动商店</div>
  <div id="event_shop"></div>
  <button class="act ghost" type="button" onclick="eventAddShop()" style="margin-top:6px">＋ 添加商品</button>
  <div class="sec">活动抽奖</div>
  <div class="chk"><input id="f_gacha_enabled" type="checkbox"><label for="f_gacha_enabled">启用抽奖</label></div>
  <div class="row">
   <div><label class="fld">抽奖指令</label><input id="f_gacha_cmd" placeholder="秋冬抽奖"></div>
   <div><label class="fld">每日次数</label><input id="f_gacha_limit" type="number" value="5"></div>
  </div>
  <div class="row"><div style="flex:1"><label class="fld">抽奖价格（如：贝壳 15）</label><input id="f_gacha_cost" placeholder="银杏叶 15"></div></div>
  <div class="sec" style="margin-top:10px">保底设置</div>
  <div class="chk"><input id="f_pity_enabled" type="checkbox"><label for="f_pity_enabled">启用大奖保底</label></div>
  <div class="row">
   <div style="flex:1"><label class="fld">大奖保底次数</label><input id="f_pity_big_threshold" type="number" value="1000" placeholder="如 1000"></div>
   <div style="flex:2"><label class="fld">大奖物品名（须与奖池奖品一致）</label><input id="f_pity_big_item" placeholder="如：史诗卡"></div>
  </div>
  <div class="row">
   <div style="flex:1"><label class="fld">小奖保底次数</label><input id="f_pity_small_threshold" type="number" value="500" placeholder="如 500"></div>
   <div style="flex:2"><label class="fld">小奖物品名（须与奖池奖品一致）</label><input id="f_pity_small_item" placeholder="如：金币"></div>
  </div>
  <div id="event_gacha_pool"></div>
  <button class="act ghost" type="button" onclick="eventAddGacha()" style="margin-top:6px">＋ 添加奖品</button>
  <div class="sec">活动副本</div>
  <div id="event_dungeons"></div>
  <button class="act ghost" type="button" onclick="eventAddDungeon()" style="margin-top:6px">＋ 添加副本</button>
  <div class="sec">世界 Boss</div>
  <div class="chk"><input id="f_boss_enabled" type="checkbox"><label for="f_boss_enabled">启用世界 Boss</label></div>
  <div class="row">
   <div style="flex:1"><label class="fld">挑战指令</label><input id="f_boss_cmd" placeholder="秋冬Boss"></div>
   <div style="flex:1"><label class="fld">Boss名称</label><input id="f_boss_name" placeholder="丰收巨灵"></div>
   <div style="flex:1"><label class="fld">血量</label><input id="f_boss_hp" type="number" value="100000"></div>
  </div>
  <div class="row">
   <div style="flex:1"><label class="fld">等级要求</label><input id="f_boss_level" type="number" value="1"></div>
   <div style="flex:1"><label class="fld">宠物精力</label><input id="f_boss_energy" type="number" value="20"></div>
   <div style="flex:1"><label class="fld">冷却(秒)</label><input id="f_boss_cooldown" type="number" value="600"></div>
   <div style="flex:1"><label class="fld">每日次数</label><input id="f_boss_limit" type="number" value="5"></div>
  </div>
  <div class="row">
   <div style="flex:1"><label class="fld">伤害系数</label><input id="f_boss_factor" type="number" step="0.01" value="0.1"></div>
   <div style="flex:1"><label class="fld">每次代币</label><input id="f_boss_token_hit" type="number" value="5"></div>
   <div style="flex:1"><label class="fld">复活秒数</label><input id="f_boss_respawn" type="number" value="3600"></div>
   <div style="flex:1"><label class="fld">Boss攻击</label><input id="f_boss_damage" type="number" value="100" placeholder="每次反击宠物的基础伤害"></div>
  </div>
  <div class="chk" style="margin-top:8px"><input id="f_boss_random_damage" type="checkbox"><label for="f_boss_random_damage">玩家伤害随机（不跟宠物战力挂钩）</label></div>
  <div class="row">
   <div style="flex:1"><label class="fld">随机最小伤害</label><input id="f_boss_random_min" type="number" value="1"></div>
   <div style="flex:1"><label class="fld">随机最大伤害</label><input id="f_boss_random_max" type="number" value="10000"></div>
  </div>
  <div class="sec" style="margin-top:10px">击杀奖励（每条奖励都会发放，可设置随机数量）</div>
  <div id="event_boss_rewards"></div>
  <button class="act ghost" type="button" onclick="eventAddBossReward()" style="margin-top:6px">＋ 添加击杀奖励</button>
  <div class="muted" style="margin-top:10px">高级用户仍可在下方「高级编辑」中直接修改 JSON。表单保存时会覆盖表单内容到 JSON。</div>`;
 return `
  <div class="muted">套餐面额（空或 0 表示不含该项，可任意组合）；或填「授权天数」改为群授权卡。</div>
  <div class="row"><div><label class="fld">金币</label><input id="f_r_coin" type="number"></div>
  <div><label class="fld">积分</label><input id="f_r_jifen" type="number"></div>
  <div><label class="fld">钻石</label><input id="f_r_diamond" type="number"></div>
  <div><label class="fld">授权天数(群授权卡)</label><input id="f_authdays" type="number"></div></div>
  <div class="chk"><input id="f_used" type="checkbox"><label for="f_used">已使用</label></div>`;
}
function buildPetForm(pet){
 const has=!!pet&&typeof pet==='object';const p=has?pet:{};
 let h=`<div class="chk"><input id="f_haspet" type="checkbox" ${has?'checked':''}><label for="f_haspet">拥有宠物（取消勾选并保存＝删除宠物；勾选无宠物者＝按默认值新建）</label></div><div class="row">`;
 for(const f of PET_FIELDS){const k=f[0],l=f[1],t=f[2],opt=f[3],empty=f[4];const val=p[k];let inp;
  if(t==='num')inp=`<input id="fp_${k}" type="number" value="${val!==undefined&&val!==null?escA(val):''}">`;
  else if(t==='sel')inp=`<select id="fp_${k}">${optHtml(META[opt],val,empty)}</select>`;
  else inp=`<input id="fp_${k}" value="${val!==undefined&&val!==null?escA(val):''}">`;
  h+=`<div style="min-width:115px;flex:1"><label class="fld">${l}</label>${inp}</div>`;}
 h+=`</div><div class="chk"><input id="fp_custom" type="checkbox" ${p.custom?'checked':''}><label for="fp_custom">定制宠物</label></div>`;
 const sk=p.skills||[];h+=`<label class="fld">秘技（按住 Ctrl/Cmd 多选）</label><select id="fp_skills" multiple style="height:96px;width:100%">`;
 for(const s of (META.skills||[]))h+=`<option ${sk.includes(s)?'selected':''}>${esc(s)}</option>`;
 for(const s of sk)if(!(META.skills||[]).includes(s))h+=`<option selected>${esc(s)}</option>`;
 h+=`</select>`;return h;
}
function bagRow(name,cnt){return `<div class="bagrow row" style="align-items:flex-end">
 <div style="flex:3"><input class="bagname" list="itemlist" value="${escA(name||'')}" placeholder="物品名"></div>
 <div style="flex:1"><input class="bagcnt" type="number" value="${cnt!==undefined&&cnt!==null?escA(cnt):1}" placeholder="数量"></div>
 <div style="flex:0"><button class="act del" type="button" onclick="this.closest('.bagrow').remove()">×</button></div></div>`;}
function buildBag(bag){bag=(bag&&typeof bag==='object')?bag:{};let h='';for(const n of Object.keys(bag))h+=bagRow(n,bag[n]);return h||'<div class="muted" id="bagempty">（空）</div>';}
function bagAdd(){const box=g('bagbox');const e=g('bagempty');if(e)e.remove();box.insertAdjacentHTML('beforeend',bagRow('',1));}
function fillFields(v){
 if(cur==='players'){
  g('f_coin').value=v.coin||0;g('f_jifen').value=v.jifen||0;g('f_diamond').value=v.diamond||0;
  const st=v.stats||{};g('f_st_win').value=st.battle_win||0;g('f_st_exp').value=st.explore||0;
  g('itemlist').innerHTML=(META.items||[]).map(i=>`<option value="${escA(i)}">`).join('');
  const pi=petEditInfo(v);
  g('petbox').innerHTML=buildPetForm(pi.idx>=0?pi.pets[pi.idx]:null);
  g('bagbox').innerHTML=buildBag(v.bag);
 }
 else if(cur==='groups'){
  g('f_enabled').checked=!!v.enabled;g('f_cross').checked=!!v.cross;
  const sect=v.sect||{};
  g('f_sect_enabled').checked=sect.enabled!==false;
  g('f_sect_name').value=sect.name||'';
  g('f_sect_level').value=sect.level||1;
  g('f_sect_points').value=sect.points||0;
  g('f_sect_season_points').value=sect.season_points||0;
  g('f_sect_exp').value=sect.exp||0;
  g('f_sect_master').value=sect.master_qq||'';
  g('f_sect_deputies').value=(sect.deputy_qqs||[]).join(',');
  g('f_sect_notice').value=sect.notice||'';
 }
 else if(cur==='events'){
  g('f_name').value=v.name||'';
  g('f_theme').value=v.theme||'';
  g('f_menu_cmd').value=v.menu_cmd||'';
  g('f_token').value=v.token||'';
  g('f_dungeon_list_cmd').value=v.dungeon_list_cmd||'';
  g('f_dungeon_enter_cmd').value=v.dungeon_enter_cmd||'';
  g('f_enabled').checked=!!v.enabled;
  g('f_start_at').value=eventTsToLocal(v.start_at||0);
  g('f_end_at').value=eventTsToLocal(v.end_at||0);
  const gc=v.gacha||{};
  g('f_gacha_enabled').checked=!!gc.enabled;
  g('f_gacha_cmd').value=gc.cmd||'';
  g('f_gacha_limit').value=gc.daily_limit!==undefined?gc.daily_limit:5;
  g('f_gacha_cost').value=eventCostToString(gc.cost||{});
  const pity=gc.pity||{};
  g('f_pity_enabled').checked=!!pity.enabled;
  const pityItems=(pity.items||[]);
  const big=pityItems.find(x=>x.name==='大奖保底')||{};
  const small=pityItems.find(x=>x.name==='小奖保底')||{};
  g('f_pity_big_threshold').value=big.threshold!==undefined?big.threshold:1000;
  g('f_pity_big_item').value=big.reward_item||'';
  g('f_pity_small_threshold').value=small.threshold!==undefined?small.threshold:500;
  g('f_pity_small_item').value=small.reward_item||'';
  eventRenderActions(v.actions||{});
  eventRenderItems(v.event_items||{});
  eventRenderShop(v.shop||{}, v.event_items||{});
  eventRenderGacha(gc.pool||[]);
  eventRenderDungeons(v.dungeons||{});
  const bs=v.boss||{};
  g('f_boss_enabled').checked=!!bs.enabled;
  g('f_boss_cmd').value=bs.cmd||'';
  g('f_boss_name').value=bs.name||'';
  g('f_boss_hp').value=bs.hp!==undefined?bs.hp:100000;
  g('f_boss_level').value=bs.level_req!==undefined?bs.level_req:1;
  g('f_boss_energy').value=bs.energy!==undefined?bs.energy:20;
  g('f_boss_cooldown').value=bs.cooldown!==undefined?bs.cooldown:600;
  g('f_boss_limit').value=bs.daily_limit!==undefined?bs.daily_limit:5;
  g('f_boss_factor').value=bs.damage_factor!==undefined?bs.damage_factor:0.1;
  g('f_boss_token_hit').value=bs.token_per_hit!==undefined?bs.token_per_hit:5;
  g('f_boss_respawn').value=bs.respawn_seconds!==undefined?bs.respawn_seconds:3600;
  g('f_boss_damage').value=bs.boss_damage!==undefined?bs.boss_damage:100;
  g('f_boss_random_damage').checked=!!bs.random_damage;
  g('f_boss_random_min').value=bs.random_damage_min!==undefined?bs.random_damage_min:1;
  g('f_boss_random_max').value=bs.random_damage_max!==undefined?bs.random_damage_max:10000;
  eventRenderBossRewards(bs.kill_rewards||[]);
 }
 else{const r=cardRewards(v);g('f_r_coin').value=r['金币']||'';g('f_r_jifen').value=r['积分']||'';g('f_r_diamond').value=r['钻石']||'';g('f_authdays').value=v.auth_days||'';g('f_used').checked=!!v.used;}
}
function applyFields(v){
 if(cur==='players'){
  v.coin=+g('f_coin').value||0;v.jifen=+g('f_jifen').value||0;v.diamond=+g('f_diamond').value||0;
  v.stats=v.stats||{};v.stats.battle_win=+g('f_st_win').value||0;v.stats.explore=+g('f_st_exp').value||0;
  const pi=petEditInfo(v);
  const haspet=g('f_haspet')&&g('f_haspet').checked;
  if(haspet){
   // 多宠物系统：宠物统一存于 v.pets[active_pet]，不写顶层 pet（运行时引用不落盘）
   const pets=pi.pets||(Array.isArray(v.pets)?v.pets:[]);
   let pet=(pi.idx>=0&&pets[pi.idx]&&typeof pets[pi.idx]==='object')?pets[pi.idx]:{};
   for(const f of PET_FIELDS){const k=f[0],t=f[2];const el=g('fp_'+k);if(!el)continue;
    if(t==='num'){if(el.value!=='')pet[k]=+el.value;}else{pet[k]=el.value;}}
   pet.custom=g('fp_custom').checked;
   pet.skills=Array.from(g('fp_skills').selectedOptions).map(o=>o.value);
   if(pet.artifact==='')pet.artifact=null;
   if(pet.talent==='')pet.talent=null;
   if(pet.love_target==='')pet.love_target=null;
   for(const k of Object.keys(PET_DEF))if(pet[k]===undefined)pet[k]=PET_DEF[k];
   if(!pet.created_at)pet.created_at=Math.floor(Date.now()/1000);
   if(!pet.last_energy_ts)pet.last_energy_ts=Math.floor(Date.now()/1000);
   if(pi.idx>=0){pets[pi.idx]=pet;}else{pets.push(pet);v.active_pet=pets.length-1;}
   v.pets=pets;
  }else if(pi.idx>=0){
   // 取消勾选＝删除当前活跃宠物
   pi.pets.splice(pi.idx,1);
   v.pets=pi.pets;
   v.active_pet=v.pets.length?Math.min(v.active_pet,v.pets.length-1):-1;
  }
  delete v.pet;  // 清理可能存在的幻影顶层 pet 字段
  const bag={};document.querySelectorAll('#bagbox .bagrow').forEach(r=>{const n=r.querySelector('.bagname').value.trim();const c=+r.querySelector('.bagcnt').value||0;if(n&&c>0)bag[n]=c;});
  v.bag=bag;
 }
 else if(cur==='groups'){
  v.enabled=g('f_enabled').checked;v.cross=g('f_cross').checked;
  v.sect=v.sect||{};
  const sect=v.sect;
  sect.enabled=g('f_sect_enabled').checked;
  sect.name=g('f_sect_name').value.trim();
  sect.level=+g('f_sect_level').value||1;
  sect.points=+g('f_sect_points').value||0;
  sect.season_points=+g('f_sect_season_points').value||0;
  sect.exp=+g('f_sect_exp').value||0;
  sect.master_qq=g('f_sect_master').value.trim();
  const dep=g('f_sect_deputies').value.trim();
  sect.deputy_qqs=dep?dep.split(',').map(s=>s.trim()).filter(Boolean):[];
  sect.notice=g('f_sect_notice').value.trim();
 }
 else if(cur==='events'){
  v.name=g('f_name').value.trim();
  v.theme=g('f_theme').value.trim();
  v.menu_cmd=g('f_menu_cmd').value.trim();
  v.token=g('f_token').value.trim();
  v.dungeon_list_cmd=g('f_dungeon_list_cmd').value.trim()||'活动副本';
  v.dungeon_enter_cmd=g('f_dungeon_enter_cmd').value.trim()||'进入活动副本';
  v.enabled=g('f_enabled').checked;
  const now=Math.floor(Date.now()/1000);
  v.start_at=eventLocalToTs(g('f_start_at').value)||now;
  v.end_at=eventLocalToTs(g('f_end_at').value)||(now+30*86400);
  v.actions=eventCollectActions();
  v.event_items=eventCollectItems();
  const rawShop=eventCollectShop();
  v.shop={};
  v.event_items=v.event_items||{};
  for(const [name,it] of Object.entries(rawShop)){
   const shopEff=it.effect||{};
   const existing=v.event_items[name]||{};
   const dedicatedEff=existing.effect||{};
   // 商店效果与独立活动道具效果同一概念：若两者不同，优先以商店编辑为准；否则保留独立区域的数据
   let finalEff=dedicatedEff;
   if(Object.keys(shopEff).length>0 && JSON.stringify(shopEff)!==JSON.stringify(dedicatedEff)){
    finalEff=shopEff;
   }
   v.shop[name]={cost:it.cost, stock:it.stock, desc:it.desc, effect:finalEff, reward:{item:name,count:1}};
   v.event_items[name]={
    category:existing.category||'道具',
    usable:Object.keys(finalEff).length>0?true:(existing.usable||false),
    desc:it.desc||existing.desc||'',
    effect:finalEff
   };
  }
  v.gacha={
   enabled:g('f_gacha_enabled').checked,
   cmd:g('f_gacha_cmd').value.trim()||'抽奖',
   daily_limit:+g('f_gacha_limit').value||0,
   cost:eventCostFromString(g('f_gacha_cost').value),
   pity:{
    enabled:g('f_pity_enabled').checked,
    items:[]
   },
   pool:eventCollectGacha()
  };
  if(g('f_pity_enabled').checked){
   const bigThreshold=+g('f_pity_big_threshold').value||0;
   const bigItem=g('f_pity_big_item').value.trim();
   if(bigThreshold>0 && bigItem){
    v.gacha.pity.items.push({name:'大奖保底',threshold:bigThreshold,reward_item:bigItem});
   }
   const smallThreshold=+g('f_pity_small_threshold').value||0;
   const smallItem=g('f_pity_small_item').value.trim();
   if(smallThreshold>0 && smallItem){
    v.gacha.pity.items.push({name:'小奖保底',threshold:smallThreshold,reward_item:smallItem});
   }
  }
  v.dungeons=eventCollectDungeons();
  v.boss={
   enabled:g('f_boss_enabled').checked,
   cmd:g('f_boss_cmd').value.trim()||'活动Boss',
   name:g('f_boss_name').value.trim()||'活动Boss',
   hp:+g('f_boss_hp').value||100000,
   level_req:+g('f_boss_level').value||1,
   energy:+g('f_boss_energy').value||0,
   cooldown:+g('f_boss_cooldown').value||600,
   daily_limit:+g('f_boss_limit').value||0,
   damage_factor:+g('f_boss_factor').value||0.1,
   token_per_hit:+g('f_boss_token_hit').value||0,
   respawn_seconds:+g('f_boss_respawn').value||3600,
   boss_damage:+g('f_boss_damage').value||100,
   random_damage:!!g('f_boss_random_damage').checked,
   random_damage_min:+g('f_boss_random_min').value||1,
   random_damage_max:+g('f_boss_random_max').value||10000,
   kill_rewards:eventCollectBossRewards()
  };
 }
 else{const ad=+g('f_authdays').value||0;if(ad>0){v.auth_days=ad;delete v.rewards;delete v.currency;delete v.amount;}else{const r={};const c=+g('f_r_coin').value||0,j=+g('f_r_jifen').value||0,d=+g('f_r_diamond').value||0;if(c>0)r['金币']=c;if(j>0)r['积分']=j;if(d>0)r['钻石']=d;v.rewards=r;delete v.currency;delete v.amount;delete v.auth_days;}v.used=g('f_used').checked;}
 return v;
}
function g(id){return document.getElementById(id);}
async function editRow(k){
 // 编辑前先拉取最新数据，并记录原始快照供保存时做乐观锁校验
 const r=await api('/api/list',{table:cur});cache=r.data||{};render();
 if(!(k in cache)){alert('该记录已不存在，列表已刷新');return;}
 editSnapshot=JSON.parse(JSON.stringify(cache[k]));
 openModal(k,JSON.parse(JSON.stringify(cache[k])));
}
function addRow(){editSnapshot=null;openModal('',{});}
function keyLabel(){return cur==='players'?'玩家键（群号\\x1fQQ号）':cur==='groups'?'群号':cur==='events'?'活动ID':'卡密码';}
function openModal(k,v){
 editKey=k;
 g('mtitle').textContent=k?'编辑记录':'新增记录';
 g('mfields').innerHTML=(k?'':`<label class="fld">${keyLabel()}</label><input id="newkey" style="width:100%">`)+fieldHtml();
 g('msub').textContent=k?k:'';
 if(cur==='events' && !k){
  const now=Math.floor(Date.now()/1000);
  v={
   id:'',
   name:'秋收冬藏',
   enabled:true,
   start_at:now,
   end_at:now+30*86400,
   token:'银杏叶',
   theme:'autumn',
   menu_cmd:'秋冬活动',
   dungeon_list_cmd:'活动副本',
   dungeon_enter_cmd:'进入活动副本',
   actions:{
    '拾穗':{energy:10,cooldown:600,daily_limit:5,rewards:{银杏叶:{min:3,max:8,chance:1}},msg:'🌾 你在田埂拾到 {银杏叶} 片银杏叶！'},
    '晒秋':{energy:15,cooldown:900,daily_limit:3,rewards:{银杏叶:{min:5,max:12,chance:1},经验:{min:50,max:120,chance:0.3}},msg:'🍁 晒秋收获 {银杏叶} 片银杏叶！'}
   },
   event_items:{
    '桂花酿':{category:'药品',usable:true,desc:'暖心润体，恢复 200 点精力并回满心情。',effect:{heal_energy:200,mood:5}},
    '暖手炉':{category:'装饰',usable:false,desc:'秋冬活动限定装饰道具，可佩戴在宠物身上（收藏用）。',effect:{}},
    '丰收斗笠':{category:'道具',usable:true,desc:'戴上后永久增加 20 点攻击。',effect:{add_atk:20}}
   },
   shop:{
    '桂花酿':{cost:{银杏叶:20},stock:{per_player:5},reward:{item:'桂花酿',count:1},desc:'恢复 200 精力并回满心情'},
    '丰收斗笠':{cost:{银杏叶:80},stock:{per_player:1},reward:{effect:{add_atk:20}},desc:'永久攻击 +20'}
   },
   gacha:{enabled:true,cmd:'秋冬抽奖',cost:{银杏叶:15},daily_limit:5,
    pity:{enabled:true,items:[
     {name:'大奖保底',threshold:1000,reward_item:'史诗卡'},
     {name:'小奖保底',threshold:500,reward_item:'金币'}
    ]},
    pool:[
    {weight:38,reward:{银杏叶:5},msg:'安慰奖，拾得 5 片银杏叶'},
    {weight:20,reward:{item:'桂花酿',count:1},msg:'来杯桂花酿～'},
    {weight:12,reward:{金币:500}},
    {weight:8,reward:{item:'普通碎片',count:3},msg:'普通碎片×3'},
    {weight:7,reward:{item:'精品碎片',count:2},msg:'精品碎片×2'},
    {weight:6,reward:{item:'稀有碎片',count:1},msg:'稀有碎片×1'},
    {weight:5,reward:{effect:{add_hp_max:50}}},
    {weight:2,reward:{item:'传说碎片',count:1},msg:'传说碎片×1（稀）'},
    {weight:1,reward:{item:'史诗卡',count:1},msg:'🎉 大奖！史诗卡'}
   ]},
   dungeons:{
    '珊瑚洞穴':{monster:'巨蟹守卫',level_req:10,energy:15,cooldown:600,power:1500,exp:315,jifen:180,token_reward:10,reward:{item:'桂花酿',count:1}},
    '沉船海湾':{monster:'幽灵船长',level_req:30,energy:25,cooldown:900,power:5000,exp:875,jifen:340,token_reward:25,reward:{item:'史诗卡',count:1}}
   },
   boss:{enabled:true,cmd:'秋冬Boss',name:'丰收巨灵',hp:100000,level_req:20,energy:30,cooldown:1800,daily_limit:3,damage_factor:0.1,token_per_hit:20,respawn_seconds:3600,boss_damage:200,random_damage:true,random_damage_min:1,random_damage_max:10000,kill_rewards:[
    {weight:48,reward:{银杏叶:100,银杏叶_max:200},msg:'满仓银杏叶'},
    {weight:30,reward:{item:'桂花酿',count:1,count_max:3}},
    {weight:15,reward:{effect:{add_atk:50}}},
    {weight:4,reward:{金币:1000,金币_max:5000}},
    {weight:2,reward:{item:'传说碎片',count:1,count_max:3},msg:'传说碎片×1~3'},
    {weight:1,reward:{item:'混沌卡',count:1},msg:'🎉 混沌品质卡！'}
   ]}
  };
 }
 fillFields(v);
 g('mval').value=JSON.stringify(v,null,2);
 g('modal').style.display='flex';
}
async function saveRow(){
 let key=editKey;
 if(!key){const nk=g('newkey');key=nk?nk.value.trim():'';if(!key){alert('请填写键');return;}}
 let base;try{base=JSON.parse(g('mval').value||'{}');}catch(e){alert('高级 JSON 格式错误: '+e);return;}
 const v=applyFields(base);
 const payload={table:cur,key:key,value:v};
 if(editSnapshot!==null)payload.base=editSnapshot;
 const r=await api('/api/upsert',payload);
 if(!r.ok){alert(r.msg||'保存失败');load();return;}
 closeModal();load();
}
function closeModal(){g('modal').style.display='none';editKey=null;editSnapshot=null;}
async function delRow(k){if(!confirm('确认删除 '+k+' ?'))return;await api('/api/delete',{table:cur,key:k});load();}
async function genCards(){
 const cardType=g('card_type').value;
 let payload;
 if(cardType==='custom_pet'){
  payload={card_type:'custom_pet',count:+g('cnt').value,prefix:g('pre').value};
 }else if(cardType==='auto_cultivation'){
  payload={card_type:'auto_cultivation',count:+g('cnt').value,prefix:g('pre').value};
 }else{
  const authdays=+g('amt_authdays').value||0;
  if(authdays>0){
   payload={auth_days:authdays,count:+g('cnt').value,prefix:g('pre').value};
  }else{
   const rewards={};const c=+g('amt_coin').value||0,j=+g('amt_jifen').value||0,d=+g('amt_diamond').value||0;
   if(c>0)rewards['金币']=c;if(j>0)rewards['积分']=j;if(d>0)rewards['钻石']=d;
   const itemName=g('amt_item').value.trim();
   const itemCount=+g('amt_item_count').value||0;
   const items={};
   if(itemName&&itemCount>0)items[itemName]=itemCount;
   if(!Object.keys(rewards).length&&!Object.keys(items).length){alert('请填写金币/积分/钻石面额，或选择道具及数量，或填写授权天数生成群授权卡');return;}
   payload={rewards:rewards,items:items,count:+g('cnt').value,prefix:g('pre').value};
  }
 }
 const r=await api('/api/cards/generate',payload);
 if(!r.ok){alert(r.msg||'生成失败');return;}
 g('genout').innerHTML='✅ 已生成 '+r.codes.length+' 张：<br>'+r.codes.map(esc).join('<br>');
 load();
}
function cardTypeChange(){
 const t=g('card_type').value;
 const hideRewards=(t==='custom_pet'||t==='auto_cultivation');
 ['amt_coin','amt_jifen','amt_diamond','amt_item','amt_item_count','amt_authdays'].forEach(id=>{const el=g(id);if(el)el.style.display=hideRewards?'none':'';});
}
function exportUnused(){
 const lines=[];for(const k of Object.keys(cache)){const v=cache[k];if(v.used)continue;let pkg;if(+(v.auto_cultivation_days||0)>0){pkg='自动修炼'+v.auto_cultivation_days+'天';}else if(+(v.auth_days||0)>0){pkg='群授权'+v.auth_days+'天';}else{const r=cardRewards(v);const items=cardItems(v);const parts=[];for(const c of ['金币','积分','钻石'])if(r[c])parts.push(c+'+'+r[c]);for(const [name,cnt] of Object.entries(items||{}))if(cnt>0)parts.push(name+'×'+cnt);pkg=parts.join('/')||'空卡';}lines.push(`${k}\\t${pkg}`);}
 if(!lines.length){alert('没有未使用的卡密');return;}
 const blob=new Blob([lines.join('\\n')],{type:'text/plain'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='unused_cards.txt';a.click();
}

// ---- 活动编辑器辅助函数 ----
function eventTsToLocal(ts){
 if(!ts) return '';
 const d=new Date(ts*1000);
 d.setMinutes(d.getMinutes()-d.getTimezoneOffset());
 return d.toISOString().slice(0,16);
}
function eventLocalToTs(s){
 if(!s) return 0;
 return Math.floor(new Date(s).getTime()/1000);
}
function eventCostToString(cost){
 if(!cost || typeof cost!=='object') return '';
 return Object.entries(cost).map(([k,v])=>k+' '+v).join(' / ');
}
function eventCostFromString(s){
 const out={};
 if(!s) return out;
 for(const part of s.split('/')){
  const m=part.trim().match(/^(.+?)\\s+(\\d+)$/);
  if(m) out[m[1].trim()]=+m[2];
 }
 return out;
}
function eventRewardHtml(reward){
 reward=reward||{};
 let type='item';
 if(reward.effect!==undefined) type='effect';
 else if(reward.item!==undefined) type='item';
 else {
  const k=Object.keys(reward).find(x=>x!=='msg' && !x.endsWith('_max'));
  if(k && META.currencies && META.currencies.includes(k)) type='currency';
  else if(k) type='token';
 }
 let detail='';
 if(type==='item'){
  detail=`<div class="row"><div style="flex:2"><label>物品名</label><input class="ev-r-item" list="ev-item-datalist" value="${escA(reward.item||'')}" placeholder="输入或选择道具名"></div><div style="flex:1"><label>最小数量</label><input class="ev-r-count" type="number" value="${reward.count!==undefined?reward.count:1}"></div><div style="flex:1"><label>最大数量</label><input class="ev-r-count-max" type="number" value="${reward.count_max!==undefined?reward.count_max:''}" placeholder="固定"></div></div>`;
 } else if(type==='effect'){
  const eff=reward.effect||{};
  const k=Object.keys(eff)[0]||'add_atk';
  const v=Object.values(eff)[0]||0;
  detail=`<div class="row"><div style="flex:2"><label>效果键</label><select class="ev-r-effk">${['add_atk','add_def','add_intel','add_hp_max','add_energy_max','mood','heal_hp','heal_energy','add_exp'].map(o=>`<option ${o===k?'selected':''}>${o}</option>`).join('')}</select></div><div style="flex:1"><label>数值</label><input class="ev-r-effv" type="number" value="${v}"></div></div>`;
 } else if(type==='currency'){
  const k=Object.keys(reward).find(x=>META.currencies.includes(x))||'金币';
  const v=reward[k]||0;
  const vmax=reward[k+'_max'];
  detail=`<div class="row"><div style="flex:2"><label>货币</label><select class="ev-r-cur">${META.currencies.map(o=>`<option ${o===k?'selected':''}>${o}</option>`).join('')}</select></div><div style="flex:1"><label>最小值</label><input class="ev-r-curv" type="number" value="${v}"></div><div style="flex:1"><label>最大值</label><input class="ev-r-curv-max" type="number" value="${vmax!==undefined?vmax:''}" placeholder="固定"></div></div>`;
 } else if(type==='token'){
  const k=Object.keys(reward).find(x=>!META.currencies.includes(x)&&x!=='msg'&&!x.endsWith('_max'))||'';
  const v=reward[k]||0;
  const vmax=reward[k+'_max'];
  detail=`<div class="row"><div style="flex:2"><label>代币名</label><input class="ev-r-tok" value="${escA(k)}"></div><div style="flex:1"><label>最小值</label><input class="ev-r-tokv" type="number" value="${v}"></div><div style="flex:1"><label>最大值</label><input class="ev-r-tokv-max" type="number" value="${vmax!==undefined?vmax:''}" placeholder="固定"></div></div>`;
 }
 return `<div class="ev-reward" data-type="${type}"><div class="row"><div style="flex:1"><label>奖励类型</label><select class="ev-r-type" onchange="eventRewardTypeChange(this)">${[['item','物品'],['effect','属性'],['currency','货币'],['token','活动代币']].map(([t,l])=>`<option value="${t}" ${t===type?'selected':''}>${l}</option>`).join('')}</select></div></div><div class="ev-r-detail">${detail}</div></div>`;
}
function eventRewardTypeChange(sel){
 const box=sel.closest('.ev-reward');
 const type=sel.value;
 box.dataset.type=type;
 let detail='';
 if(type==='item') detail=`<div class="row"><div style="flex:2"><label>物品名</label><input class="ev-r-item" list="ev-item-datalist" value="" placeholder="输入或选择道具名"></div><div style="flex:1"><label>最小数量</label><input class="ev-r-count" type="number" value="1"></div><div style="flex:1"><label>最大数量</label><input class="ev-r-count-max" type="number" value="" placeholder="固定"></div></div>`;
 else if(type==='effect') detail=`<div class="row"><div style="flex:2"><label>效果键</label><select class="ev-r-effk">${['add_atk','add_def','add_intel','add_hp_max','add_energy_max','mood','heal_hp','heal_energy','add_exp'].map(o=>`<option>${o}</option>`).join('')}</select></div><div style="flex:1"><label>数值</label><input class="ev-r-effv" type="number" value="0"></div></div>`;
 else if(type==='currency') detail=`<div class="row"><div style="flex:2"><label>货币</label><select class="ev-r-cur">${(META.currencies||['金币','积分','钻石']).map(o=>`<option>${o}</option>`).join('')}</select></div><div style="flex:1"><label>最小值</label><input class="ev-r-curv" type="number" value="0"></div><div style="flex:1"><label>最大值</label><input class="ev-r-curv-max" type="number" value="" placeholder="固定"></div></div>`;
 else if(type==='token') detail=`<div class="row"><div style="flex:2"><label>代币名</label><input class="ev-r-tok" value=""></div><div style="flex:1"><label>最小值</label><input class="ev-r-tokv" type="number" value="0"></div><div style="flex:1"><label>最大值</label><input class="ev-r-tokv-max" type="number" value="" placeholder="固定"></div></div>`;
 box.querySelector('.ev-r-detail').innerHTML=detail;
}
function eventCollectReward(box){
 const type=box.dataset.type || box.querySelector('.ev-r-type').value;
 if(type==='item'){
  const name=box.querySelector('.ev-r-item').value.trim();
  const count=+box.querySelector('.ev-r-count').value||1;
  const countMax=+box.querySelector('.ev-r-count-max').value||0;
  if(!name) return null;
  const out={item:name,count:count};
  if(countMax>count) out.count_max=countMax;
  return out;
 } else if(type==='effect'){
  const k=box.querySelector('.ev-r-effk').value;
  const v=+box.querySelector('.ev-r-effv').value||0;
  return {effect:{[k]:v}};
 } else if(type==='currency'){
  const k=box.querySelector('.ev-r-cur').value;
  const min=+box.querySelector('.ev-r-curv').value||0;
  const max=+box.querySelector('.ev-r-curv-max').value||0;
  const out={[k]:min};
  if(max>min) out[k+'_max']=max;
  return out;
 } else if(type==='token'){
  const k=box.querySelector('.ev-r-tok').value.trim();
  const min=+box.querySelector('.ev-r-tokv').value||0;
  const max=+box.querySelector('.ev-r-tokv-max').value||0;
  if(!k) return null;
  const out={[k]:min};
  if(max>min) out[k+'_max']=max;
  return out;
 }
 return null;
}

// actions
function eventActionHtml(name,conf){
 conf=conf||{};
 return `<div class="event-card" style="border:1px solid #e8ecf6;padding:10px;margin:8px 0;border-radius:8px">
  <div class="row">
   <div style="flex:2"><label>玩法指令</label><input class="ev-a-name" value="${escA(name)}"></div>
   <div style="flex:1"><label>宠物精力</label><input class="ev-a-energy" type="number" value="${conf.energy!==undefined?conf.energy:10}"></div>
   <div style="flex:1"><label>冷却(秒)</label><input class="ev-a-cooldown" type="number" value="${conf.cooldown!==undefined?conf.cooldown:600}"></div>
   <div style="flex:1"><label>每日次数</label><input class="ev-a-limit" type="number" value="${conf.daily_limit!==undefined?conf.daily_limit:5}" placeholder="空=不限"></div>
  </div>
  <div style="margin-top:6px"><label>结果文案（可用 {代币名} 占位）</label><input class="ev-a-msg" style="width:100%" value="${escA(conf.msg||'')}"></div>
  <div class="sec" style="margin-top:10px">随机奖励</div>
  <div class="ev-a-rewards"></div>
  <button class="act ghost" type="button" onclick="eventAddReward(this.closest('.event-card').querySelector('.ev-a-rewards'))" style="margin-top:6px">＋ 奖励</button>
  <button class="act del" type="button" onclick="this.closest('.event-card').remove()" style="margin-top:6px">删除玩法</button>
 </div>`;
}
function eventAddAction(){
 const box=g('event_actions');
 const div=document.createElement('div');
 div.innerHTML=eventActionHtml('',{});
 const card=div.firstElementChild;
 box.appendChild(card);
 eventAddReward(card.querySelector('.ev-a-rewards'));
}
function eventRenderActions(actions){
 const box=g('event_actions'); box.innerHTML='';
 for(const [name,conf] of Object.entries(actions||{})){
  const div=document.createElement('div');
  div.innerHTML=eventActionHtml(name,conf);
  const card=div.firstElementChild;
  box.appendChild(card);
  eventRenderRewards(card.querySelector('.ev-a-rewards'),conf.rewards||{});
 }
}
function eventCollectActions(){
 const out={};
 document.querySelectorAll('#event_actions .event-card').forEach(card=>{
  const name=card.querySelector('.ev-a-name').value.trim();
  if(!name) return;
  const limit=card.querySelector('.ev-a-limit').value;
  out[name]={
   energy:+card.querySelector('.ev-a-energy').value||0,
   cooldown:+card.querySelector('.ev-a-cooldown').value||0,
   daily_limit:limit===''?null:+limit,
   msg:card.querySelector('.ev-a-msg').value,
   rewards:eventCollectRewards(card.querySelector('.ev-a-rewards'))
  };
 });
 return out;
}
function eventAddReward(container){
 const div=document.createElement('div');
 div.innerHTML=`<div class="reward-row row" style="align-items:flex-end;margin:6px 0;border:1px dashed #334155;padding:8px;border-radius:6px">
   <div style="flex:2"><label>奖励名</label><input class="ev-r-name" value="" placeholder="贝壳 / 经验 / 物品名"></div>
   <div style="flex:1"><label>最小值</label><input class="ev-r-min" type="number" value="0"></div>
   <div style="flex:1"><label>最大值</label><input class="ev-r-max" type="number" value="0"></div>
   <div style="flex:1"><label>概率</label><input class="ev-r-chance" type="number" step="0.1" value="1"></div>
   <div style="flex:0"><button class="act del" type="button" onclick="this.closest('.reward-row').remove()">×</button></div>
  </div>`;
 container.appendChild(div.firstElementChild);
}
function eventRenderRewards(container,rewards){
 container.innerHTML='';
 for(const [name,cfg] of Object.entries(rewards||{})){
  const div=document.createElement('div');
  div.innerHTML=`<div class="reward-row row" style="align-items:flex-end;margin:6px 0;border:1px dashed #334155;padding:8px;border-radius:6px">
   <div style="flex:2"><label>奖励名</label><input class="ev-r-name" value="${escA(name)}" placeholder="贝壳 / 经验 / 物品名"></div>
   <div style="flex:1"><label>最小值</label><input class="ev-r-min" type="number" value="${cfg.min!==undefined?cfg.min:0}"></div>
   <div style="flex:1"><label>最大值</label><input class="ev-r-max" type="number" value="${cfg.max!==undefined?cfg.max:0}"></div>
   <div style="flex:1"><label>概率</label><input class="ev-r-chance" type="number" step="0.1" value="${cfg.chance!==undefined?cfg.chance:1}"></div>
   <div style="flex:0"><button class="act del" type="button" onclick="this.closest('.reward-row').remove()">×</button></div>
  </div>`;
  container.appendChild(div.firstElementChild);
 }
}
function eventCollectRewards(container){
 const out={};
 container.querySelectorAll('.reward-row').forEach(row=>{
  const name=row.querySelector('.ev-r-name').value.trim();
  if(!name) return;
  out[name]={
   min:+row.querySelector('.ev-r-min').value||0,
   max:+row.querySelector('.ev-r-max').value||0,
   chance:+row.querySelector('.ev-r-chance').value||1
  };
 });
 return out;
}

// shop
function eventShopHtml(name,it){
 it=it||{};
 return `<div class="event-card" style="border:1px solid #e8ecf6;padding:10px;margin:8px 0;border-radius:8px">
  <div class="row">
   <div style="flex:2"><label>商品名</label><input class="ev-s-name" value="${escA(name)}"></div>
   <div style="flex:2"><label>价格（如：贝壳 20 / 金币 100）</label><input class="ev-s-cost" value="${escA(eventCostToString(it.cost||{}))}"></div>
   <div style="flex:1"><label>每人限购</label><input class="ev-s-per" type="number" value="${it.stock&&it.stock.per_player!==undefined?it.stock.per_player:''}" placeholder="空=不限"></div>
   <div style="flex:1"><label>全局库存</label><input class="ev-s-global" type="number" value="${it.stock&&it.stock.global!==undefined?it.stock.global:''}" placeholder="空=不限"></div>
  </div>
  <div style="margin-top:6px"><label>描述</label><input class="ev-s-desc" style="width:100%" value="${escA(it.desc||'')}"></div>
  <div class="sec" style="margin-top:10px">道具使用效果（购买后获得该道具）</div>
  <div class="ev-s-effect">${eventItemEffectHtml(it.effect||{})}</div>
  <button class="act ghost" type="button" onclick="eventAddEffRow(this)" style="margin-top:6px">＋ 添加效果</button>
  <button class="act del" type="button" onclick="this.closest('.event-card').remove()" style="margin-top:6px">删除商品</button>
 </div>`;
}
function eventAddShop(){
 const box=g('event_shop');
 const div=document.createElement('div');
 div.innerHTML=eventShopHtml('',{});
 box.appendChild(div.firstElementChild);
}
function eventRenderShop(shop,event_items){
 event_items=event_items||{};
 const box=g('event_shop'); box.innerHTML='';
 for(const [name,it] of Object.entries(shop||{})){
  // 使用效果统一存到 event_items，shop 自身可能没 effect；这里优先回显 event_items
  let effect=it.effect||{};
  if(!Object.keys(effect).length){
   const ei=event_items[name];
   if(ei && ei.effect && typeof ei.effect==='object') effect=ei.effect;
  }
  if(!Object.keys(effect).length && it.reward && it.reward.effect && typeof it.reward.effect==='object'){
   effect=it.reward.effect;
  }
  const div=document.createElement('div');
  div.innerHTML=eventShopHtml(name,{...it,effect:effect});
  box.appendChild(div.firstElementChild);
 }
}
function eventCollectShop(){
 const out={};
 document.querySelectorAll('#event_shop .event-card').forEach(card=>{
  const name=card.querySelector('.ev-s-name').value.trim();
  if(!name) return;
  const effect={};
  card.querySelectorAll('.ev-s-effect .ev-eff-row').forEach(row=>{
   const k=row.querySelector('.ev-eff-k').value;
   const v=+row.querySelector('.ev-eff-v').value||0;
   if(k) effect[k]=v;
  });
  const it={cost:eventCostFromString(card.querySelector('.ev-s-cost').value), desc:card.querySelector('.ev-s-desc').value, effect:effect, reward:{item:name,count:1}};
  const per=card.querySelector('.ev-s-per').value;
  const glob=card.querySelector('.ev-s-global').value;
  it.stock={};
  if(per!=='') it.stock.per_player=+per;
  if(glob!=='') it.stock.global=+glob;
  out[name]=it;
 });
 return out;
}


async function bossRespawn(){
 const eid=g('boss_respawn_id').value.trim();
 const box=g('boss_respawn_msg');
 if(!eid){box.textContent='请输入活动ID';return;}
 const r=await api('/api/boss_respawn',{event_id:eid});
 box.textContent=r.ok?(r.msg||'操作成功'):(r.msg||'操作失败');
 if(r.ok) load();
}

// gacha
function eventGachaHtml(entry){
 entry=entry||{};
 return `<div class="event-card" style="border:1px solid #e8ecf6;padding:10px;margin:8px 0;border-radius:8px">
  <div class="row">
   <div style="flex:1"><label>权重</label><input class="ev-g-weight" type="number" value="${entry.weight!==undefined?entry.weight:1}"></div>
   <div style="flex:3"><label>提示文案（可选）</label><input class="ev-g-msg" value="${escA(entry.msg||'')}" placeholder="例如：恭喜获得大奖！"></div>
  </div>
  <div class="sec" style="margin-top:10px">奖品内容</div>
  <div class="ev-g-reward">${eventRewardHtml(entry.reward||{})}</div>
  <button class="act del" type="button" onclick="this.closest('.event-card').remove()" style="margin-top:6px">删除奖品</button>
 </div>`;
}
function eventAddGacha(){
 const box=g('event_gacha_pool');
 const div=document.createElement('div');
 div.innerHTML=eventGachaHtml({});
 box.appendChild(div.firstElementChild);
}
function eventRenderGacha(pool){
 const box=g('event_gacha_pool'); box.innerHTML='';
 for(const entry of (pool||[])){
  const div=document.createElement('div');
  div.innerHTML=eventGachaHtml(entry);
  box.appendChild(div.firstElementChild);
 }
}
function eventCollectGacha(){
 const out=[];
 document.querySelectorAll('#event_gacha_pool .event-card').forEach(card=>{
  const rw=eventCollectReward(card.querySelector('.ev-g-reward .ev-reward'));
  if(!rw) return;
  out.push({weight:+card.querySelector('.ev-g-weight').value||1, msg:card.querySelector('.ev-g-msg').value, reward:rw});
 });
 return out;
}

// dungeons
function eventDungeonHtml(name,conf){
 conf=conf||{};
 return `<div class="event-card" style="border:1px solid #e8ecf6;padding:10px;margin:8px 0;border-radius:8px">
  <div class="row">
   <div style="flex:2"><label>副本名称</label><input class="ev-d-name" value="${escA(name)}"></div>
   <div style="flex:2"><label>怪物名</label><input class="ev-d-monster" value="${escA(conf.monster||'')}"/></div>
   <div style="flex:1"><label>等级要求</label><input class="ev-d-level" type="number" value="${conf.level_req!==undefined?conf.level_req:1}"></div>
  </div>
  <div class="row">
   <div style="flex:1"><label>宠物精力</label><input class="ev-d-energy" type="number" value="${conf.energy!==undefined?conf.energy:10}"></div>
   <div style="flex:1"><label>冷却(秒)</label><input class="ev-d-cooldown" type="number" value="${conf.cooldown!==undefined?conf.cooldown:600}"></div>
   <div style="flex:1"><label>每日次数</label><input class="ev-d-limit" type="number" value="${conf.daily_limit!==undefined?conf.daily_limit:''}" placeholder="空=不限"></div>
   <div style="flex:1"><label>怪物战力</label><input class="ev-d-power" type="number" value="${conf.power!==undefined?conf.power:1000}"></div>
  </div>
  <div class="row">
   <div style="flex:1"><label title="推荐≈(100+等级×80)×0.35，避免一次副本连升数级">经验</label><input class="ev-d-exp" type="number" value="${conf.exp!==undefined?conf.exp:0}"></div>
   <div style="flex:1"><label title="推荐≈100+等级×8">积分</label><input class="ev-d-jifen" type="number" value="${conf.jifen!==undefined?conf.jifen:0}"></div>
   <div style="flex:1"><label>代币奖励</label><input class="ev-d-token" type="number" value="${conf.token_reward!==undefined?conf.token_reward:0}"></div>
  </div>
  <div class="sec" style="margin-top:10px">通关额外奖励（可选）</div>
  <div class="ev-d-reward">${eventRewardHtml(conf.reward||{})}</div>
  <button class="act del" type="button" onclick="this.closest('.event-card').remove()" style="margin-top:6px">删除副本</button>
 </div>`;
}
function eventAddDungeon(){
 const box=g('event_dungeons');
 const div=document.createElement('div');
 div.innerHTML=eventDungeonHtml('',{});
 box.appendChild(div.firstElementChild);
}
function eventRenderDungeons(dungeons){
 const box=g('event_dungeons'); box.innerHTML='';
 for(const [name,conf] of Object.entries(dungeons||{})){
  const div=document.createElement('div');
  div.innerHTML=eventDungeonHtml(name,conf);
  box.appendChild(div.firstElementChild);
 }
}
function eventCollectDungeons(){
 const out={};
 document.querySelectorAll('#event_dungeons .event-card').forEach(card=>{
  const name=card.querySelector('.ev-d-name').value.trim();
  if(!name) return;
  const limit=card.querySelector('.ev-d-limit').value;
  out[name]={
   monster:card.querySelector('.ev-d-monster').value.trim()||'怪物',
   level_req:+card.querySelector('.ev-d-level').value||1,
   energy:+card.querySelector('.ev-d-energy').value||0,
   cooldown:+card.querySelector('.ev-d-cooldown').value||600,
   power:+card.querySelector('.ev-d-power').value||0,
   exp:+card.querySelector('.ev-d-exp').value||0,
   jifen:+card.querySelector('.ev-d-jifen').value||0,
   token_reward:+card.querySelector('.ev-d-token').value||0,
   reward:eventCollectReward(card.querySelector('.ev-d-reward .ev-reward'))
  };
  if(limit!=='') out[name].daily_limit=+limit;
 });
 return out;
}

// event items
const EVENT_ITEM_EFFECT_KEYS=['add_atk','add_def','add_intel','add_hp_max','add_energy_max','mood','heal_hp','heal_energy','add_exp'];
function eventItemEffectRowHtml(k,v){
 return `<div class="ev-eff-row row" style="align-items:flex-end;margin:4px 0">
  <div style="flex:2"><label>效果键</label><select class="ev-eff-k">${EVENT_ITEM_EFFECT_KEYS.map(o=>`<option ${o===k?'selected':''}>${o}</option>`).join('')}</select></div>
  <div style="flex:1"><label>数值</label><input class="ev-eff-v" type="number" value="${v!==undefined?v:0}"></div>
  <div style="flex:0"><button class="act del" type="button" onclick="this.closest('.ev-eff-row').remove()">×</button></div>
 </div>`;
}
function eventItemEffectHtml(effect){
 effect=effect||{};
 // 兼容旧版 {effect:{heal_energy:200}} 包裹格式
 if(effect.effect && typeof effect.effect==='object') effect=effect.effect;
 const keys=Object.keys(effect);
 if(keys.length===0) return '';
 return keys.map(k=>eventItemEffectRowHtml(k,effect[k])).join('');
}
function eventAddEffRow(btn){
 const box=btn.previousElementSibling;
 if(!box) return;
 const div=document.createElement('div');
 div.innerHTML=eventItemEffectRowHtml('heal_energy',0);
 box.appendChild(div.firstElementChild);
}
function eventItemHtml(name,conf){
 conf=conf||{};
 return `<div class="event-card" style="border:1px solid #e8ecf6;padding:10px;margin:8px 0;border-radius:8px">
  <div class="row">
   <div style="flex:2"><label>道具名</label><input class="ev-i-name" value="${escA(name)}" placeholder="夏日冰饮"></div>
   <div style="flex:1"><label>分类</label><select class="ev-i-cat">${['药品','道具','装饰','材料'].map(o=>`<option ${o===(conf.category||'道具')?'selected':''}>${o}</option>`).join('')}</select></div>
   <div style="flex:0"><div class="chk" style="margin-top:20px"><input class="ev-i-usable" type="checkbox" ${conf.usable?'checked':''}><label>可使用</label></div></div>
  </div>
  <div style="margin-top:6px"><label>描述</label><input class="ev-i-desc" style="width:100%" value="${escA(conf.desc||'')}"></div>
  <div class="sec" style="margin-top:10px">使用效果</div>
  <div class="ev-i-effect">${eventItemEffectHtml(conf.effect)}</div>
  <button class="act ghost" type="button" onclick="eventAddEffRow(this)" style="margin-top:6px">＋ 添加效果</button>
  <button class="act del" type="button" onclick="this.closest('.event-card').remove();updateEventItemDatalist();" style="margin-top:6px">删除道具</button>
 </div>`;
}
function eventAddItem(){
 const box=g('event_items');
 const div=document.createElement('div');
 div.innerHTML=eventItemHtml('',{});
 box.appendChild(div.firstElementChild);
 updateEventItemDatalist();
}
function eventRenderItems(items){
 const box=g('event_items'); box.innerHTML='';
 for(const [name,conf] of Object.entries(items||{})){
  // 兼容旧版包裹格式
  if(conf.effect && conf.effect.effect && typeof conf.effect.effect==='object') conf.effect=conf.effect.effect;
  const div=document.createElement('div');
  div.innerHTML=eventItemHtml(name,conf);
  box.appendChild(div.firstElementChild);
 }
 updateEventItemDatalist();
}
function eventCollectItems(){
 const out={};
 document.querySelectorAll('#event_items .event-card').forEach(card=>{
  const name=card.querySelector('.ev-i-name').value.trim();
  if(!name) return;
  const effect={};
  card.querySelectorAll('.ev-i-effect .ev-eff-row').forEach(row=>{
   const k=row.querySelector('.ev-eff-k').value;
   const v=+row.querySelector('.ev-eff-v').value||0;
   if(k) effect[k]=v;
  });
  out[name]={
   category:card.querySelector('.ev-i-cat').value,
   usable:!!card.querySelector('.ev-i-usable').checked,
   desc:card.querySelector('.ev-i-desc').value,
   effect:effect
  };
 });
 return out;
}
function updateEventItemDatalist(){
 let dl=g('ev-item-datalist');
 if(!dl){
  dl=document.createElement('datalist');
  dl.id='ev-item-datalist';
  document.body.appendChild(dl);
 }
 const names=new Set(META.items||[]);
 document.querySelectorAll('#event_items .ev-i-name').forEach(el=>{const v=el.value.trim();if(v)names.add(v);});
 dl.innerHTML=Array.from(names).map(i=>`<option value="${escA(i)}">`).join('');
}

// boss
function eventBossRewardHtml(entry){
 entry=entry||{};
 return `<div class="event-card" style="border:1px solid #e8ecf6;padding:10px;margin:8px 0;border-radius:8px">
  <div class="row">
   <div style="flex:1"><label>分配权重（越高越优先给高伤害）</label><input class="ev-b-weight" type="number" value="${entry.weight!==undefined?entry.weight:1}"></div>
   <div style="flex:3"><label>提示文案（可选）</label><input class="ev-b-msg" value="${escA(entry.msg||'')}" placeholder="例如：恭喜获得大奖！"></div>
  </div>
  <div class="sec" style="margin-top:10px">奖励内容（设置最小/最大数量即可随机）</div>
  <div class="ev-b-reward">${eventRewardHtml(entry.reward||{})}</div>
  <button class="act del" type="button" onclick="this.closest('.event-card').remove()" style="margin-top:6px">删除奖励</button>
 </div>`;
}
function eventAddBossReward(){
 const box=g('event_boss_rewards');
 const div=document.createElement('div');
 div.innerHTML=eventBossRewardHtml({});
 box.appendChild(div.firstElementChild);
}
function eventRenderBossRewards(rewards){
 const box=g('event_boss_rewards'); box.innerHTML='';
 for(const entry of (rewards||[])){
  const div=document.createElement('div');
  div.innerHTML=eventBossRewardHtml(entry);
  box.appendChild(div.firstElementChild);
 }
}
function eventCollectBossRewards(){
 const out=[];
 document.querySelectorAll('#event_boss_rewards .event-card').forEach(card=>{
  const rw=eventCollectReward(card.querySelector('.ev-b-reward .ev-reward'));
  if(!rw) return;
  out.push({weight:+card.querySelector('.ev-b-weight').value||1, msg:card.querySelector('.ev-b-msg').value, reward:rw});
 });
 return out;
}

(async()=>{await loadMeta();await load();})();

</script></body></html>"""
