"""宠物乐园 —— AstrBot 群聊养成 / 对战插件。

参考某 QQ 群"宠物联盟"玩法复刻：砸蛋抽宠、宠物商城、属性克制对战、繁殖姻缘、
进化飞升渡劫、天赋觉醒、炼丹、神器/秘技、副本、剧情任务、跨群挑战、排行神榜等。

指令均为无前缀中文指令（与参考一致），通过监听全部消息后自行解析路由。
"""

from __future__ import annotations

import asyncio
import random
import time
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api import message_components as Comp
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register

from .petpark import data, images, pet as petmod
from .petpark.store import PetStore

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:  # pragma: no cover - 兼容旧版本

    def get_astrbot_data_path() -> str:
        return "data"


PLUGIN_NAME = "astrbot_plugin_petpark"

# 本插件识别的指令首词（日常活动为整句匹配，见 data.DAILY_ACTIONS）。
KNOWN_COMMANDS = {
    # 管理
    "开启宠物乐园",
    "关闭宠物乐园",
    "开启宠物跨群",
    "关闭宠物跨群",
    # 信息查询
    "宠物种类",
    "属性",
    "神器",
    "秘技",
    "仙丹",
    "天赋",
    "状态",
    "宠物菜单",
    "宠物指令",
    "宠物帮助",
    "我的信息",
    "个人信息",
    "签到",
    "兑换",
    "卡密兑换",
    "我要氪金",
    "查看说明",
    # 群授权
    "授权",
    "授权状态",
    "授权本群",
    # 管理员：增减货币
    "加金币",
    "减金币",
    "加积分",
    "减积分",
    "加钻石",
    "减钻石",
    # 小管理员（分群授权）
    "任命小管理",
    "任命小管理员",
    "撤销小管理",
    "撤销小管理员",
    "小管理列表",
    "我的管理额度",
    # 商城
    "宠物商城",
    "道具商城",
    "积分商城",
    "宠物市场",
    "宠物专域",
    # 获取宠物
    "砸蛋",
    "购买宠物",
    # 背包 / 物品
    "查看背包",
    "背包图",
    "清空背包",
    "购买",
    "使用",
    "出售",
    "丢弃",
    "转让",
    # 宠物管理
    "我的宠物",
    "查看宠物",
    "宠物图",
    "宠物侦查",
    "赠送宠物",
    "放生宠物",
    "宠物改名",
    "宠物变性",
    "宠物复活",
    "宠物状态",
    "喂食",
    # 成长
    "一键升级宠物",
    "宠物升级",
    "宠物进化",
    "宠物飞升",
    "宠物渡劫",
    "幻境寻宝",
    "宠物神仙劫",
    "合成卡",
    "合成品质卡",
    "卡合成",
    "赠送金币",
    "赠送积分",
    "赠送钻石",
    # 神器 / 秘技
    "打造神器",
    "佩戴神器",
    "卸下神器",
    "参悟秘技",
    "遗忘秘技",
    # 天赋 / 炼丹
    "宠物觉醒",
    "制作天赋符",
    "使用天赋符",
    "炼丹",
    "提炼仙丹",
    "使用仙丹",
    "治愈",
    "复活",
    "精力转移",
    # 对战 / 排行
    "宠物攻击",
    "跨群挑战宠物",
    "宠物排行",
    "宠物神榜",
    "领取神榜奖励",
    # 副本 / 剧情
    "宠物副本",
    "进入副本",
    "深渊秘境",
    "深渊介绍",
    "深渊商店",
    "深渊购买",
    "深渊祝福",
    "宠物剧情任务",
    "我的剧情任务",
    "取消剧情任务",
    "提交任务",
    "领取任务",
    # 婚恋
    "宠物恋情",
    "宠物分手",
    "宠物离婚",
    "宠物追求",
    "同意追求",
    "宠物求婚",
    "同意求婚",
    # Boss
    "Boss伤害排行",
    "Boss历史奖品",
    # 邀请
    "受邀",
    "我的邀请情况",
}


@register(
    PLUGIN_NAME,
    "Devin",
    "宠物乐园：群聊宠物养成与对战玩法（砸蛋/商城/对战/进化/姻缘/天赋/炼丹/副本）。",
    "1.0.0",
    "https://github.com/skyesda/qqbot_pet",
)
class PetParkPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        data_dir = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        self.store = PetStore(
            data_dir / "petpark.json",
            start_coin=int(self.config.get("start_coin", 1000)),
            start_jifen=int(self.config.get("start_jifen", 0)),
            start_diamond=int(self.config.get("start_diamond", 0)),
            default_enabled=bool(self.config.get("default_enabled", True)),
            default_cross=bool(self.config.get("default_cross_group", True)),
        )
        # 管理员 QQ 列表（白名单），统一转成字符串便于比较
        self.admins = {str(a).strip() for a in self.config.get("admins", []) if str(a).strip()}
        # 对战精力消耗、排行名额、神榜奖励等可调参数
        self.attack_energy = max(0, int(self.config.get("attack_energy", data.ATTACK_ENERGY)))
        self.rank_size = max(1, int(self.config.get("rank_size", 10)))
        # 小管理员每日「增加」额度上限（金币、积分各自独立；减少不限）
        self.subadmin_daily_add_limit = max(
            0, int(self.config.get("subadmin_daily_add_limit", 100000))
        )
        # 神榜前三每日可领取的随机钻石区间
        self.rank_reward_diamond_min = max(
            0, int(self.config.get("rank_reward_diamond_min", 10))
        )
        self.rank_reward_diamond_max = max(
            self.rank_reward_diamond_min,
            int(self.config.get("rank_reward_diamond_max", 50)),
        )
        # 签到积分/金币随机范围（可在配置面板调整）
        self.sign_jifen_min = max(0, int(self.config.get("sign_jifen_min", 1000)))
        self.sign_jifen_max = max(self.sign_jifen_min, int(self.config.get("sign_jifen_max", 12000)))
        self.sign_coin_min = max(0, int(self.config.get("sign_coin_min", 50)))
        self.sign_coin_max = max(self.sign_coin_min, int(self.config.get("sign_coin_max", 200)))
        # 连续签到每天的额外金币（额外金币 = 连续天数 × 该值，封顶 7 天）
        self.sign_streak_bonus = max(0, int(self.config.get("sign_streak_bonus", 100)))
        # 邀请成功奖励（可在 Aster 插件配置面板调整）
        self.invite_coin = max(0, int(self.config.get("invite_coin", 500)))
        self.invite_jifen = max(0, int(self.config.get("invite_jifen", 5000)))
        self.invite_diamond = max(0, int(self.config.get("invite_diamond", 50)))
        # 精力恢复速度为全局常量，按配置覆盖
        data.ENERGY_REGEN_PER_MIN = max(1, int(self.config.get("energy_regen_per_min", data.ENERGY_REGEN_PER_MIN)))
        # 专属管理网站（卡密生成 + 数据增删改查）
        self._web = None
        # 全服广播任务引用，防止被 GC
        self._broadcast_tasks: set = set()
        if bool(self.config.get("web_enabled", True)):
            self._start_web_admin()
        self._patch_qqofficial_message_extensions()

    def _start_web_admin(self) -> None:
        """在当前事件循环中后台启动管理网站；失败不影响插件主体。"""
        import asyncio

        try:
            from .petpark.webadmin import WebAdmin
        except Exception:  # 兼容不同导入路径
            try:
                from petpark.webadmin import WebAdmin
            except Exception:
                logger.exception("[petpark] 管理网站模块导入失败")
                return
        self._web = WebAdmin(
            self.store,
            host=str(self.config.get("web_host", "0.0.0.0")),
            port=int(self.config.get("web_port", 7799)),
            user=str(self.config.get("web_user", "admin")),
            password=str(self.config.get("web_pass", "2468080asd")),
            broadcast_callback=self._broadcast_to_authorized_groups,
        )

        async def _boot():
            try:
                await self._web.start()
            except Exception:
                logger.exception("[petpark] 管理网站启动失败（端口被占用或权限不足？）")

        try:
            asyncio.get_event_loop().create_task(_boot())
        except RuntimeError:
            logger.warning("[petpark] 无运行中的事件循环，管理网站未启动")

    def _patch_qqofficial_message_extensions(self) -> None:
        """为 QQ 官方适配器补齐 Markdown 主动推送与消息按钮支持。

        AstrBot 事件响应路径会自动把 use_markdown_=True 的消息按 msg_type=2 发送，
        但 `context.send_message` -> `send_by_session` 的群聊主动推送目前只发 `content`，
        导致原生 Markdown（##、**、表格）以纯文本形式显示；同时 MessageChain 没有官方
        消息按钮组件。这里在插件加载时打运行时补丁。
        """
        try:
            from astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter import (
                QQOfficialPlatformAdapter,
            )
            from astrbot.core.platform.sources.qqofficial.qqofficial_message_event import (
                QQOfficialMessageEvent,
            )
            from astrbot.api.platform import MessageType
            from botpy.types.message import KeyboardPayload, MarkdownPayload
        except Exception:
            logger.debug("[petpark] QQ 官方适配器未加载，跳过消息扩展补丁")
            return

        def _keyboard_from_chain(chain):
            kb = getattr(chain, "qq_keyboard", None)
            if kb:
                try:
                    return KeyboardPayload(content=kb)
                except Exception:
                    pass
            return None

        # 让 MessageChain.derive 复制 qq_keyboard，否则 AstrBot 响应管道拆分消息链时会丢失按钮
        _orig_derive = MessageChain.derive

        def _patched_derive(self, chain=None):
            new = _orig_derive(self, chain if chain is not None else [])
            if hasattr(self, "qq_keyboard"):
                new.qq_keyboard = self.qq_keyboard
            return new

        MessageChain.derive = _patched_derive

        # ---------- 1. 主动推送路径 ----------
        _orig_send_by_session = QQOfficialPlatformAdapter._send_by_session_common

        async def _patched_send_by_session(self, session, message_chain):
            use_md = getattr(message_chain, "use_markdown_", None)
            scene = getattr(self, "_session_scene", {})
            is_group = (
                session.message_type == MessageType.GROUP_MESSAGE
                and scene.get(session.session_id) == "group"
            )
            if is_group and use_md is not False:
                try:
                    parsed = await QQOfficialMessageEvent._parse_to_qqofficial(
                        message_chain
                    )
                except Exception:
                    parsed = None
                if parsed:
                    (
                        plain_text,
                        image_base64,
                        image_path,
                        record_file_path,
                        video_file_source,
                        file_source,
                        _,
                    ) = parsed
                    has_media = (
                        image_base64
                        or image_path
                        or record_file_path
                        or video_file_source
                        or file_source
                    )
                    if plain_text and not has_media:
                        msg_id = self._session_last_message_id.get(session.session_id)
                        allow = getattr(self, "_allow_group_proactive_send", False)
                        if msg_id or allow:
                            payload = {
                                "markdown": MarkdownPayload(content=plain_text),
                                "msg_type": 2,
                                "msg_seq": random.randint(1, 10000),
                            }
                            if msg_id and not allow:
                                payload["msg_id"] = msg_id
                            kb = _keyboard_from_chain(message_chain)
                            if kb:
                                payload["keyboard"] = kb
                            try:
                                await self.client.api.post_group_message(
                                    group_openid=session.session_id, **payload
                                )
                                return
                            except Exception as e:
                                logger.warning(
                                    f"[petpark] QQ 官方主动 Markdown 推送失败，将走原接口: {e}"
                                )
            return await _orig_send_by_session(self, session, message_chain)

        QQOfficialPlatformAdapter._send_by_session_common = _patched_send_by_session

        # ---------- 2. 事件响应路径 ----------
        _orig_fallback = QQOfficialMessageEvent._send_with_markdown_fallback

        async def _patched_fallback(self, send_func, payload, plain_text, stream=None):
            send_buffer = getattr(self, "send_buffer", None)
            if send_buffer and payload.get("markdown"):
                kb = _keyboard_from_chain(send_buffer)
                if kb:
                    payload["keyboard"] = kb

            def _wrap(sf):
                async def wrapper(p):
                    if not p.get("markdown") and "keyboard" in p:
                        p.pop("keyboard", None)
                    return await sf(p)

                return wrapper

            return await _orig_fallback(self, _wrap(send_func), payload, plain_text, stream)

        QQOfficialMessageEvent._send_with_markdown_fallback = _patched_fallback
        logger.info("[petpark] 已打补丁：QQ 官方消息支持 Markdown 与消息按钮")

    @staticmethod
    def _build_qq_keyboard(rows: list[list[tuple[str, str]]]) -> dict:
        """构造 QQ 官方机器人的 InlineKeyboard 数据字典。

        rows: 每一行是 (显示文字, 点击后发送的文本) 元组列表。
        """
        out_rows: list[dict] = []
        for r, row in enumerate(rows):
            buttons: list[dict] = []
            for c, (label, data) in enumerate(row):
                buttons.append(
                    {
                        "id": f"btn_{r}_{c}",
                        "render_data": {
                            "label": label,
                            "visited_label": label,
                            "style": 0,
                        },
                        "action": {
                            "type": 2,
                            "permission": {
                                "type": 2,
                                "specify_role_ids": [],
                                "specify_user_ids": [],
                            },
                            "click_limit": 100,
                            "data": data,
                            "at_bot_show_channel_list": False,
                        },
                    }
                )
            out_rows.append({"buttons": buttons})
        return {"rows": out_rows}

    def _main_menu_keyboard(self) -> dict:
        return self._build_qq_keyboard(
            [
                [("🥚 砸蛋", "砸蛋"), ("🐾 我的宠物", "我的宠物")],
                [("💼 查看背包", "查看背包"), ("⚔️ 宠物攻击", "宠物攻击")],
                [("📜 宠物菜单", "宠物菜单"), ("🎁 每日签到", "签到")],
            ]
        )

    def _event_menu_keyboard(self, cfg: dict) -> dict:
        rows: list[list[tuple[str, str]]] = []
        actions = list(cfg.get("actions", {}).keys())
        if actions:
            rows.append([(a, a) for a in actions[:4]])
        shop = cfg.get("shop", {})
        if shop:
            first = next(iter(shop))
            rows.append([("🛒 活动商店", f"购买 {first}")])
        gacha = cfg.get("gacha", {})
        if gacha.get("enabled"):
            cmd = gacha.get("cmd", "抽奖")
            rows.append([(f"🎰 {cmd}", cmd)])
        boss = cfg.get("boss", {})
        if boss.get("enabled"):
            cmd = boss.get("cmd", "活动Boss")
            rows.append([(f"👹 {cmd}", cmd)])
        dungeon_cmd = cfg.get("dungeon_list_cmd", "活动副本")
        rows.append([(f"🗺️ {dungeon_cmd}", dungeon_cmd)])
        return self._build_qq_keyboard(rows)

    def _keyboard_for_cmd(self, text: str) -> dict | None:
        """根据用户发送的指令决定要不要附带快捷按钮。"""
        if text in {"宠物菜单", "宠物指令", "宠物帮助"}:
            return self._main_menu_keyboard()
        for cfg in self.store.active_events().values():
            if text == cfg.get("menu_cmd"):
                return self._event_menu_keyboard(cfg)
        return None

    # =====================================================================
    # 消息入口：监听全部消息，解析无前缀中文指令
    # =====================================================================
    @filter.event_message_type(filter.EventMessageType.ALL, priority=1)
    async def on_message(self, event: AstrMessageEvent):
        text = (event.message_str or "").strip()
        if not text:
            return
        qq = str(event.get_sender_id())
        group_id = self._group_id(event)
        # 记录群聊统一消息来源，便于 Boss 击杀/复活时向授权群主动推送
        if self._is_group(group_id):
            umo = getattr(event, "unified_msg_origin", None)
            if umo:
                self.store.get_group(group_id)["umo"] = umo
        try:
            reply = self.dispatch(event, qq, group_id, text)
        except Exception as e:  # 保证插件不因单条消息崩溃
            logger.exception("[petpark] 处理指令出错")
            reply = f"宠物乐园处理出错：{e}"
        # 部分指令返回 (文本, Markdown图片串) 二元组，把宠物图片随消息一起展示。
        image_md = None
        if isinstance(reply, tuple):
            reply, image_md = reply
        if reply is None:
            return
        await self.store.save()
        event.stop_event()
        # QQ 官方机器人(qq_official)一条消息要么是原生 Markdown(msg_type=2，渲染
        # ## / **)，要么是富媒体图片(msg_type=7，文本只当纯文本)——带 Image 组件就会
        # 丢掉 markdown。所以这里不再附加 Image 组件，而是把宠物图片以 Markdown 图片
        # 语法内嵌到文本最前(images.pet_image_md)，整条消息仍是纯文本走 msg_type=2，
        # 让图片与渲染后的文本同处一条消息。QQ 服务端会按 URL(jsDelivr CDN)拉取图片。
        if image_md:
            reply = f"{image_md}\n{reply}"
        # 在合适的地方附加 QQ 官方消息按钮，方便用户快捷发送指令
        keyboard = self._keyboard_for_cmd(text)
        # 群聊里 @ 触发者，便于多人同时游玩时分辨各自的消息；私聊不 @。
        if self._is_group(group_id):
            # QQ 官方机器人(qq_official)适配器会忽略 At 组件，故同时以纯文本
            # 形式前置 @昵称，确保任何平台都能看出这条消息@的是谁。
            name = self._sender_name(event) or qq
            head = Comp.Plain(f"@{name}\n")
            at = self._safe_at(qq)
            chain = ([at] if at else []) + [head, Comp.Plain(reply)]
            res = event.chain_result(chain)
            if keyboard:
                res.qq_keyboard = keyboard
            yield res
        else:
            res = event.plain_result(reply)
            if keyboard:
                res.qq_keyboard = keyboard
            yield res

    @staticmethod
    def _is_group(group_id: str) -> bool:
        return bool(group_id) and group_id != "private"

    @staticmethod
    def _sender_name(event) -> str:
        for attr in ("get_sender_name",):
            fn = getattr(event, attr, None)
            if callable(fn):
                try:
                    return str(fn() or "")
                except Exception:
                    return ""
        return ""

    @staticmethod
    def _safe_at(qq: str):
        """构造 At 组件（支持的平台会渲染为真正的@）；失败则返回 None。"""
        try:
            return Comp.At(qq=qq)
        except Exception:
            return None

    async def terminate(self):
        await self.store.save()
        if self._web is not None:
            await self._web.stop()

    # =====================================================================
    # 工具函数
    # =====================================================================
    @staticmethod
    def _group_id(event: AstrMessageEvent) -> str:
        gid = ""
        try:
            gid = event.get_group_id() or ""
        except Exception:
            gid = getattr(getattr(event, "message_obj", None), "group_id", "") or ""
        return str(gid) or "private"

    @staticmethod
    def _arg(tokens: list[str], idx: int) -> str | None:
        """取 tokens[idx] 作为目标用户ID（兼容纯数字 QQ 与平台 openid 字符串）。"""
        if idx < len(tokens):
            tok = tokens[idx].strip()
            if tok:
                return tok
        return None

    def _find_target(
        self, group_id: str, qq: str | None
    ) -> tuple[dict | None, str | None]:
        """按用户ID查本群玩家（数据按群隔离）。返回 (player, 错误提示)。"""
        if not qq:
            return None, None
        tp = self.store.get_player(qq, group_id, create=False)
        if not tp:
            return None, f"❌ 用户 `{qq}` 在本群不存在（对方需先在本群参与宠物乐园）。"
        return tp, None

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        # 配置里的管理员白名单优先
        if str(event.get_sender_id()) in self.admins:
            return True
        for attr in ("is_admin",):
            fn = getattr(event, attr, None)
            if callable(fn):
                try:
                    return bool(fn())
                except Exception:
                    pass
        return str(getattr(event, "role", "")).lower() in ("admin", "owner")

    # =====================================================================
    # 路由
    # =====================================================================
    def _active_event_commands(self) -> set[str]:
        """返回当前生效活动的所有菜单/动作/抽奖/奖池列表/副本/Boss 指令。"""
        cmds = set()
        for cfg in self.store.active_events().values():
            if cfg.get("menu_cmd"):
                cmds.add(cfg["menu_cmd"])
            cmds.update(cfg.get("actions", {}).keys())
            gacha = cfg.get("gacha", {})
            if gacha.get("enabled"):
                gcmd = gacha.get("cmd", "抽奖")
                cmds.add(gcmd)
                cmds.add(f"{gcmd}列表")
                cmds.add(f"{gcmd}十连")
                cmds.add(f"{gcmd}10连")
                if gcmd.endswith("抽奖"):
                    cmds.add(gcmd[:-2] + "十连抽")
            if cfg.get("dungeons"):
                cmds.add(cfg.get("dungeon_list_cmd", "活动副本"))
                cmds.add(cfg.get("dungeon_enter_cmd", "进入活动副本"))
            boss = cfg.get("boss", {})
            if boss.get("enabled"):
                bcmd = boss.get("cmd", "活动Boss")
                cmds.add(bcmd)
                cmds.add(f"{bcmd}伤害排行")
                cmds.add(f"{bcmd}奖池")
        return cmds

    def dispatch(self, event, qq, group_id, text):
        """处理一条指令。返回 None / 文本字符串 / (文本, 图片路径) 二元组。"""
        tokens = text.split()
        cmd = tokens[0]
        # 非本插件指令直接放行，避免为每条普通聊天创建玩家/群档案
        event_cmds = self._active_event_commands()
        if (
            cmd not in KNOWN_COMMANDS
            and text not in data.DAILY_ACTIONS
            and cmd not in event_cmds
            and text not in event_cmds
        ):
            return None
        group = self.store.get_group(group_id)

        # ---- 管理开关（管理员） ----
        if cmd in ("开启宠物乐园", "关闭宠物乐园"):
            if not self._is_admin(event):
                return "❌ 仅管理员可开关宠物乐园。"
            group["enabled"] = cmd.startswith("开启")
            state = "已开启 ✅" if group["enabled"] else "已关闭 🚫"
            return f"## 🐾 宠物乐园\n本群宠物乐园**{state}**。"
        if cmd in ("开启宠物跨群", "关闭宠物跨群"):
            if not self._is_admin(event):
                return "❌ 仅管理员可开关跨群功能。"
            group["cross"] = cmd.startswith("开启")
            state = "已开启 ✅" if group["cross"] else "已关闭 🚫"
            return f"## 🌐 跨群挑战\n本群跨群功能**{state}**。"

        # ---- 管理员：增减指定用户金币 / 积分 / 钻石 ----
        if cmd in ("加金币", "减金币", "加积分", "减积分", "加钻石", "减钻石"):
            return self._admin_adjust(event, qq, group_id, cmd, tokens)

        # ---- 大管理员：任命 / 撤销 / 查看 小管理员 ----
        if cmd in ("任命小管理", "任命小管理员", "撤销小管理", "撤销小管理员"):
            return self._manage_subadmin(event, group_id, cmd, tokens)
        if cmd == "小管理列表":
            return self._list_subadmins(event)
        if cmd == "我的管理额度":
            return self._my_admin_quota(event, qq, group_id)

        # ---- 群授权（状态查询 / 卡密授权 / 大管理员直授）----
        if cmd == "授权状态":
            return self._auth_status(group_id)
        if cmd == "授权":
            return self._redeem_auth_card(event, group_id, qq, tokens)
        if cmd == "授权本群":
            return self._grant_auth(event, group_id, tokens)

        # ---- 群授权校验：严格模式，所有群聊都需有效授权才能使用 ----
        if self._is_group(group_id) and not self._is_group_authorized(group_id):
            return self._auth_blocked_text()

        # 群未开启则不响应任何宠物指令
        if not group.get("enabled", True):
            return None

        # ---- 查看类型 / 说明（信息查询，无需有宠物）----
        info = self._handle_info(cmd, tokens)
        if info is not None:
            return info

        # ---- 商城（无需宠物）----
        if cmd == "宠物商城":
            return self._shop_text("宠物商城")
        if cmd == "道具商城" or cmd == "积分商城":
            return self._shop_text("道具商城")
        if cmd in ("宠物市场", "宠物专域"):
            return self._pet_market_text()

        player = self.store.get_player(qq, group_id)
        player["group"] = group_id

        # ---- 我的信息（唯一展示 ID / 群 / 金币 / 积分 的地方）----
        if cmd in ("我的信息", "个人信息"):
            return self._my_info(player, group_id)

        # ---- 邀请 ----
        if cmd == "受邀":
            return self._accept_invite(player, group_id, tokens)
        if cmd == "我的邀请情况":
            return self._my_invites(player)

        # ---- 每日签到 ----
        if cmd == "签到":
            return self._sign_in(player, group_id)

        # ---- 卡密兑换 ----
        if cmd in ("兑换", "卡密兑换"):
            return self._redeem(player, group_id, qq, tokens)
        if cmd == "我要氪金":
            return self._pay_link()

        # ---- 获取宠物 ----
        if cmd == "砸蛋":
            return self._smash_egg(player)
        if cmd == "购买宠物":
            return self._buy_pet(player, tokens)
        if cmd in ("合成卡", "合成品质卡", "卡合成"):
            return self._compose_quality_card(player, tokens)
        if cmd in ("赠送金币", "赠送积分", "赠送钻石"):
            return self._gift_currency(player, group_id, cmd, tokens)

        # ---- 背包 / 商城购买 / 物品 ----
        if cmd in ("查看背包", "背包图"):
            return self._bag_text(player)
        if cmd == "清空背包":
            player["bag"] = {}
            return "背包已清空。"
        if cmd == "购买":
            return self._buy_item(player, tokens)
        if cmd == "使用":
            return self._use_item(player, tokens)
        if cmd in ("出售",):
            return self._sell_item(player, tokens)
        if cmd in ("丢弃",):
            return self._drop_item(player, tokens)
        if cmd == "转让":
            return self._transfer_item(player, group_id, tokens)

        # ---- 以下指令大多需要拥有宠物 ----
        if cmd in ("我的宠物", "查看宠物", "宠物图"):
            return self._my_pet(player)
        if cmd == "宠物侦查":
            return self._inspect(group_id, tokens)
        if cmd == "赠送宠物":
            return self._gift_pet(player, group_id, tokens)
        if cmd == "放生宠物":
            return self._release(player)
        if cmd == "宠物改名":
            return self._rename(player, tokens)
        if cmd == "宠物变性":
            return self._change_gender(player)
        if cmd == "宠物复活":
            return self._revive_self(player)
        if cmd == "宠物状态":
            return self._status_text(player)
        if cmd == "喂食":
            return self._feed(player, tokens)

        # ---- 日常活动 ----
        if text in data.DAILY_ACTIONS:
            return self._daily(player, group_id, text)

        # ---- 成长 ----
        if cmd == "一键升级宠物":
            return self._auto_level(player)
        if cmd == "宠物升级":
            return self._manual_level(player, tokens)
        if cmd == "宠物进化":
            return self._evolve(player)
        if cmd == "宠物飞升":
            return self._ascend(player)
        if cmd == "宠物渡劫":
            return self._tribulation(player)

        # ---- 飞升后玩法 ----
        if cmd == "幻境寻宝":
            return self._fantasy_treasure(player)
        if cmd == "宠物神仙劫":
            return self._immortal_calamity(player)

        # ---- 神器 / 秘技 ----
        if cmd == "打造神器":
            return self._forge_artifact(player, tokens)
        if cmd == "佩戴神器":
            return self._equip_artifact(player, tokens)
        if cmd == "卸下神器":
            return self._unequip_artifact(player)
        if cmd == "参悟秘技":
            return self._learn_skill(player, tokens)
        if cmd == "遗忘秘技":
            return self._forget_skill(player)

        # ---- 天赋 / 炼丹 ----
        if cmd == "宠物觉醒":
            return self._awaken(player)
        if cmd == "制作天赋符":
            return self._make_rune(player)
        if cmd == "使用天赋符":
            return self._use_rune(player, tokens)
        if cmd in ("炼丹", "提炼仙丹"):
            return self._refine_elixir(player)
        if cmd == "使用仙丹":
            return self._use_elixir(player, group_id, tokens)

        # ---- 天赋触发指令 ----
        if cmd == "治愈":
            return self._talent_heal(player, group_id, tokens)
        if cmd == "复活":
            return self._talent_revive(player, group_id, tokens)
        if cmd == "精力转移":
            return self._energy_transfer(player, group_id, tokens)

        # ---- 对战 / 排行 ----
        if cmd == "宠物攻击":
            return self._attack(player, group_id, tokens)
        if cmd == "跨群挑战宠物":
            return self._cross_attack(player, group, tokens)
        if cmd == "宠物排行":
            return self._rank(player, group_id, local=True)
        if cmd == "宠物神榜":
            return self._rank(player, group_id, local=False)
        if cmd == "领取神榜奖励":
            return self._claim_rank_reward(player, group_id)

        # ---- 副本 ----
        if cmd == "宠物副本":
            return self._dungeon_list()
        if cmd == "进入副本":
            return self._enter_dungeon(player, tokens)
        if cmd == "深渊秘境":
            return self._abyss_dungeon(player)
        if cmd == "深渊介绍":
            return self._abyss_intro()
        if cmd == "深渊商店":
            return self._abyss_shop(player)
        if cmd == "深渊购买":
            return self._abyss_buy(player, tokens)
        if cmd == "深渊祝福":
            return self._abyss_blessing(player, tokens)

        # ---- 剧情任务 ----
        if cmd == "宠物剧情任务":
            return self._quest_list()
        if cmd == "我的剧情任务":
            return self._my_quests(player)
        if cmd == "取消剧情任务":
            player["quests"] = {}
            return "已取消所有已领取的剧情任务。"
        if cmd == "提交任务" or cmd == "领取任务":
            return self._handle_quest(player, tokens, cmd)

        # ---- 婚恋 ----
        love = self._handle_love(player, group_id, cmd, tokens)
        if love is not None:
            return love

        # ---- 限时活动 ----
        event_reply = self._handle_event(player, group_id, cmd, text, tokens)
        if event_reply is not None:
            return event_reply

        # ---- Boss 全服排行 / 个人 Boss 奖品历史 ----
        if cmd == "Boss伤害排行":
            return self._event_boss_ranking_all(group_id)
        if cmd == "Boss历史奖品":
            return self._my_boss_rewards(player)

        return None

    # =====================================================================
    # 限时活动
    # =====================================================================
    def _handle_event(
        self, player: dict, group_id: str, cmd: str, text: str, tokens: list[str]
    ) -> str | None:
        for eid, cfg in self.store.active_events().items():
            if cfg.get("menu_cmd") and cmd == cfg["menu_cmd"]:
                return self._event_menu(cfg)
            if text in cfg.get("actions", {}):
                return self._event_action(player, eid, cfg, text)
            gacha = cfg.get("gacha", {})
            gcmd = gacha.get("cmd", "抽奖")
            if gacha.get("enabled"):
                if cmd == gcmd:
                    return self._event_gacha(player, eid, cfg)
                if cmd == f"{gcmd}列表":
                    return self._event_gacha_list(cfg)
                ten_cmds = {f"{gcmd}十连", f"{gcmd}10连"}
                if gcmd.endswith("抽奖"):
                    ten_cmds.add(gcmd[:-2] + "十连抽")
                if cmd in ten_cmds:
                    return self._event_gacha_multi(player, eid, cfg, times=10)
            dungeons = cfg.get("dungeons", {})
            list_cmd = cfg.get("dungeon_list_cmd", "活动副本")
            enter_cmd = cfg.get("dungeon_enter_cmd", "进入活动副本")
            if dungeons:
                if cmd == list_cmd:
                    return self._event_dungeon_list(cfg)
                if cmd == enter_cmd and len(tokens) >= 2:
                    return self._event_enter_dungeon(player, eid, cfg, tokens[1])
            boss = cfg.get("boss", {})
            bcmd = boss.get("cmd", "活动Boss")
            if boss.get("enabled"):
                if cmd == bcmd:
                    return self._event_boss_challenge(player, group_id, eid, cfg)
                if cmd == f"{bcmd}伤害排行":
                    return self._event_boss_ranking(cfg)
                if cmd == f"{bcmd}奖池":
                    return self._event_boss_pool(cfg)
        if cmd == "活动副本":
            return "当前没有开启的活动副本。"
        return None

    def _event_menu(self, cfg: dict) -> str:
        token = cfg.get("token", "代币")
        lines = [f"## 🎉 {cfg.get('name', '限时活动')}", f"> 活动代币：{token}", ""]
        actions = cfg.get("actions", {})
        if actions:
            lines.append("**活动玩法**")
            for action, aconf in actions.items():
                energy = aconf.get("energy", 0)
                limit = aconf.get("daily_limit")
                limit_txt = f"每日限 {limit} 次" if limit else "不限次数"
                lines.append(f"• `{action}` — 消耗宠物精力 {energy}，{limit_txt}")
            lines.append("")
        shop = cfg.get("shop", {})
        if shop:
            lines.append("**活动商店**（发送 `购买 物品名`）")
            for name, it in shop.items():
                cost = " / ".join(f"{v} {k}" for k, v in it.get("cost", {}).items())
                lines.append(f"• `{name}` — {cost} — {it.get('desc', '')}")
            lines.append("")
        gacha = cfg.get("gacha", {})
        if gacha.get("enabled"):
            cost = gacha.get("cost", {})
            single_cost = " / ".join(f"{v} {k}" for k, v in cost.items())
            ten_cost = " / ".join(f"{v * 9} {k}" for k, v in cost.items())
            gcmd = gacha.get("cmd", "抽奖")
            ten_cmd = gcmd[:-2] + "十连抽" if gcmd.endswith("抽奖") else f"{gcmd}十连"
            lines.append(
                f"**活动抽奖**：`{gcmd}` 单次 {single_cost} · "
                f"`{ten_cmd}` 十连 {ten_cost} · 每日限 {gacha.get('daily_limit', '∞')} 次"
            )
            lines.append(f"> 发送 `{gcmd}列表` 查看奖池与概率")
            lines.append("")
        dungeons = cfg.get("dungeons", {})
        if dungeons:
            list_cmd = cfg.get("dungeon_list_cmd", "活动副本")
            enter_cmd = cfg.get("dungeon_enter_cmd", "进入活动副本")
            lines.append("**活动副本**（发送 `{enter_cmd} 名称`）".format(enter_cmd=enter_cmd))
            for name, d in dungeons.items():
                energy = d.get("energy", 0)
                req = d.get("level_req", 1)
                lines.append(f"• `{name}` — Lv{req} · 耗 {energy} 精力 · 战力 {d.get('power', 0)}")
            lines.append(f"> 发送 `{list_cmd}` 查看完整副本列表")
            lines.append("")
        boss = cfg.get("boss", {})
        if boss.get("enabled"):
            bcmd = boss.get("cmd", "活动Boss")
            state = self._event_boss_state(cfg)
            hp_text = f"{state['hp']}/{state['max_hp']}"
            lines.append(f"**世界 Boss**：`{boss.get('name', '活动Boss')}` 血量 {hp_text}")
            lines.append("**Boss 指令**")
            lines.append(f"• `{bcmd}` — 挑战 Boss（消耗宠物精力 {boss.get('energy', 0)}）")
            lines.append(f"• `{bcmd}伤害排行` — 查看全服伤害排行")
            lines.append(f"• `{bcmd}奖池` — 查看击杀奖励池")
            lines.append("")
        return "\n".join(lines)

    def _event_action(self, player: dict, eid: str, cfg: dict, action: str) -> str:
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物，发送『砸蛋』获取一只。"
        conf = cfg["actions"][action]
        token = cfg.get("token", "代币")
        today = time.strftime("%Y-%m-%d")
        self.store.reset_event_daily(player, eid, today)
        limit = conf.get("daily_limit")
        if limit and self.store.event_daily_count(player, eid, action) >= limit:
            return f"今日『{action}』次数已用完。"
        petmod.refresh_energy(p)
        energy = conf.get("energy", 0)
        if p["energy"] < energy:
            return f"宠物精力不足（需 {energy}，当前 {p['energy']}/{p['energy_max']}）。"
        cd_key = f"event:{eid}:{action}"
        cd = self._cooldown_block(player, cd_key, action)
        if cd:
            return cd
        if energy:
            p["energy"] -= energy
        cooldown = conf.get("cooldown", 0)
        if cooldown:
            self.store.set_cooldown(player, cd_key, cooldown)
        self.store.inc_event_daily(player, eid, action)
        reward_texts = {}
        for reward_name, reward_cfg in conf.get("rewards", {}).items():
            if random.random() > reward_cfg.get("chance", 1.0):
                continue
            amount = random.randint(reward_cfg.get("min", 0), reward_cfg.get("max", 0))
            if amount <= 0:
                continue
            if reward_name == token:
                self.store.add_event_token(player, eid, token, amount)
                reward_texts[token] = reward_texts.get(token, 0) + amount
            elif reward_name in self.store.CURRENCY_KEYS:
                self.store.add_currency(player, reward_name, amount)
                reward_texts[reward_name] = reward_texts.get(reward_name, 0) + amount
            elif reward_name == "经验":
                petmod.add_exp(p, amount)
                reward_texts["经验"] = reward_texts.get("经验", 0) + amount
            else:
                self.store.add_item(player, reward_name, amount)
                reward_texts[reward_name] = reward_texts.get(reward_name, 0) + amount
        msg_template = conf.get("msg", f"完成『{action}』！")
        for k, v in reward_texts.items():
            msg_template = msg_template.replace(f"{{{k}}}", str(v))
        lines = [msg_template]
        fmt = self._format_event_rewards(reward_texts)
        if fmt:
            lines.append(fmt)
        return "\n".join(lines)

    def _event_gacha_list(self, cfg: dict) -> str:
        """显示活动抽奖奖池列表及中奖概率。"""
        gacha = cfg.get("gacha", {})
        pool = gacha.get("pool", [])
        if not pool:
            return "当前活动抽奖奖池为空。"
        total = sum(entry.get("weight", 1) for entry in pool)
        if total <= 0:
            return "当前活动抽奖奖池权重配置有误。"
        token = cfg.get("token", "代币")
        lines = [
            f"## 🎰 {cfg.get('name', '活动')}·抽奖奖池",
            f"> 总权重：{total}，每次消耗 {gacha.get('cmd', '抽奖')}",
            "",
        ]
        for i, entry in enumerate(pool, 1):
            weight = entry.get("weight", 1)
            pct = weight / total * 100
            reward = entry.get("reward", {})
            reward_txt = self._format_event_reward(reward, token)
            msg = entry.get("msg", "")
            lines.append(
                f"{i}. **{reward_txt}** — {pct:.1f}%（权重 {weight}）{msg and ' — ' + msg or ''}"
            )
        return "\n".join(lines)

    def _event_dungeon_list(self, cfg: dict) -> str:
        """显示活动副本列表。"""
        dungeons = cfg.get("dungeons", {})
        token = cfg.get("token", "代币")
        lines = [f"## 🏰 {cfg.get('name', '活动')}·活动副本", f"> 活动代币：{token}", ""]
        if not dungeons:
            return "当前活动暂无副本。"
        for name, d in dungeons.items():
            energy = d.get("energy", 0)
            req = d.get("level_req", 1)
            power = d.get("power", 0)
            drop = self._format_event_reward(d.get("reward", {}), token)
            lines.append(
                f"- **{name}** `Lv{req}`　🗡{d.get('monster', '怪物')}（战力 {power}）\n"
                f"　　耗 {energy} 精力 · 通关奖励 {drop}"
            )
        lines.append(
            f"\n> 进入方式：`{cfg.get('dungeon_enter_cmd', '进入活动副本')} 副本名称`"
        )
        return "\n".join(lines)

    def _event_enter_dungeon(
        self, player: dict, eid: str, cfg: dict, name: str
    ) -> str:
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物，发送『砸蛋』获取一只。"
        dungeons = cfg.get("dungeons", {})
        if name not in dungeons:
            return f"活动副本中没有『{name}』。"
        d = dungeons[name]
        token = cfg.get("token", "代币")
        today = time.strftime("%Y-%m-%d")
        self.store.reset_event_daily(player, eid, today)
        limit = d.get("daily_limit")
        action_key = f"dungeon:{name}"
        if limit and self.store.event_daily_count(player, eid, action_key) >= limit:
            return f"今日『{name}』次数已用完。"
        if p["level"] < d.get("level_req", 1):
            return f"进入『{name}』需要宠物等级 Lv{d.get('level_req', 1)}。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        cd_key = f"event:{eid}:dungeon:{name}"
        cd = self._cooldown_block(player, cd_key, name)
        if cd:
            return cd
        petmod.refresh_energy(p)
        energy = d.get("energy", 0)
        if p["energy"] < energy:
            return f"宠物精力不足（需 {energy}，当前 {p['energy']}/{p['energy_max']}）。"
        p["energy"] -= energy
        self.store.set_cooldown(player, cd_key, d.get("cooldown", 600))
        self.store.inc_event_daily(player, eid, action_key)
        return self._event_dungeon_battle(player, eid, cfg, p, name, d)

    def _event_dungeon_battle(
        self, player: dict, eid: str, cfg: dict, p: dict, name: str, d: dict
    ) -> str:
        token = cfg.get("token", "代币")
        monster = d.get("monster", "怪物")
        power = d.get("power", 0)
        my_power = petmod.battle_power(p)
        roll = int(my_power * random.uniform(0.9, 1.1))
        win = roll >= power
        nick = p["nickname"]
        head = f"## ⚔ {nick} VS {monster}"
        if win:
            exp_gain = d.get("exp", 0)
            jifen_gain = d.get("jifen", 0)
            token_gain = d.get("token_reward", 0)
            if exp_gain:
                petmod.add_exp(p, exp_gain)
            if jifen_gain:
                self.store.add_currency(player, "积分", jifen_gain)
            if token_gain:
                self.store.add_event_token(player, eid, token, token_gain)
            reward_lines = []
            if exp_gain:
                reward_lines.append(f"经验 +{exp_gain}")
            if jifen_gain:
                reward_lines.append(f"积分 +{jifen_gain}")
            if token_gain:
                reward_lines.append(f"{token} +{token_gain}")
            item_reward = d.get("reward", {})
            if item_reward:
                extra = self._grant_event_reward(player, eid, cfg, item_reward)
                if extra:
                    for line in extra.split("\n"):
                        if line:
                            reward_lines.append(line)
            drop = "\n● " + "\n● ".join(reward_lines) if reward_lines else ""
            desc = f"您的{nick}在{name}遇见{monster}，激战之后**大胜**！"
            body = (
                "┏-★---副☆本---★-┓\n"
                f"●怪物战力：{power}\n"
                f"●本次战力：{roll}\n"
                f"●通关奖励：{drop}\n"
                "┗-★---信☆息---★-┛"
            )
            return f"{head}\n{desc}\n{body}{self._auto_level_note(player, p)}"
        desc = f"您的{nick}在{name}遇见{monster}，力战之后**惨败**！"
        body = (
            "┏-★---副☆本---★-┓\n"
            f"●怪物战力：{power}\n"
            f"●本次战力：{roll}\n"
            "●战败没有奖励！\n"
            "┗-★---信☆息---★-┛"
        )
        return f"{head}\n{desc}\n{body}"

    # --------------------------- Boss 全服广播 -----------------------------
    def _broadcast_to_authorized_groups(self, text: str) -> None:
        """向所有已授权且记录过 UMO 的群主动推送一条文本消息。"""
        try:
            task = asyncio.get_running_loop().create_task(self._do_broadcast(text))
            self._broadcast_tasks.add(task)
            task.add_done_callback(self._broadcast_tasks.discard)
        except RuntimeError:
            pass

    async def _do_broadcast(self, text: str) -> None:
        from astrbot.api.event import MessageChain

        groups = self.store._data.get("groups", {})
        targets = [
            (gid, g.get("umo"))
            for gid, g in groups.items()
            if self._is_group_authorized(gid) and g.get("umo")
        ]
        if not targets:
            logger.info("[petpark] 没有可广播的授权群（UMO 未记录）")
            return
        logger.info(f"[petpark] 开始向 {len(targets)} 个授权群广播 Boss 消息")
        for gid, umo in targets:
            try:
                await self.context.send_message(
                    umo, MessageChain().message(text).use_markdown(True)
                )
                logger.info(f"[petpark] 已向群 {gid} 广播 Boss 消息")
            except Exception:
                logger.exception(f"[petpark] 向群 {gid} 主动推送失败")

    def _event_boss_state(self, cfg: dict) -> dict:
        """初始化/返回活动 Boss 的共享状态。编辑活动时若只改非血量字段，应保持当前血量。"""
        boss = cfg.setdefault("boss", {})
        state = cfg.setdefault("_boss_state", {})
        max_hp = int(boss.get("hp", 10000))
        if not state:
            state["max_hp"] = max_hp
            state["hp"] = max_hp
            state["respawn_until"] = 0
            state["damage_rank"] = {}
            state["respawn_notified"] = False
            return state
        # 血量上限变化时按比例缩放当前血量，而不是直接回满
        old_max = state.get("max_hp")
        if old_max != max_hp:
            old_hp = state.get("hp", old_max or max_hp)
            if old_max:
                state["hp"] = max(1, int(old_hp * max_hp / old_max))
            else:
                state["hp"] = max_hp
            state["max_hp"] = max_hp
        state.setdefault("respawn_until", 0)
        state.setdefault("damage_rank", {})
        state.setdefault("respawn_notified", False)
        # Boss 复活时向所有授权群推送
        now = int(time.time())
        respawn_until = state.get("respawn_until", 0)
        if respawn_until and now >= respawn_until and not state.get("respawn_notified"):
            state["respawn_notified"] = True
            bname = boss.get("name", "活动Boss")
            self._broadcast_to_authorized_groups(
                f"## 👹 世界 Boss {bname} 已复活！\n"
                f"血量 {state['hp']}/{state['max_hp']}，发送 `{boss.get('cmd', '活动Boss')}` 即可挑战。"
            )
        return state

    def _event_boss_challenge(
        self, player: dict, group_id: str, eid: str, cfg: dict
    ) -> str:
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物，发送『砸蛋』获取一只。"
        boss = cfg.get("boss", {})
        token = cfg.get("token", "代币")
        today = time.strftime("%Y-%m-%d")
        self.store.reset_event_daily(player, eid, today)
        cmd = boss.get("cmd", "活动Boss")
        limit = boss.get("daily_limit")
        if limit and self.store.event_daily_count(player, eid, cmd) >= limit:
            return f"今日『{cmd}』挑战次数已用完。"
        if p["level"] < boss.get("level_req", 1):
            return f"挑战活动 Boss 需要宠物等级 Lv{boss.get('level_req', 1)}。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        cd_key = f"event:{eid}:boss"
        cd = self._cooldown_block(player, cd_key, cmd)
        if cd:
            return cd
        state = self._event_boss_state(cfg)
        now = int(time.time())
        if state.get("respawn_until", 0) > now:
            remain = state["respawn_until"] - now
            return (
                f"『{boss.get('name', '活动Boss')}』正在复活，"
                f"还需 `{self._fmt_duration(remain)}`。"
            )
        petmod.refresh_energy(p)
        energy = boss.get("energy", 0)
        if p["energy"] < energy:
            return f"宠物精力不足（需 {energy}，当前 {p['energy']}/{p['energy_max']}）。"
        p["energy"] -= energy
        self.store.set_cooldown(player, cd_key, boss.get("cooldown", 600))
        self.store.inc_event_daily(player, eid, cmd)
        return self._event_boss_battle(player, group_id, eid, cfg, p, state)

    def _event_boss_battle(
        self, player: dict, group_id: str, eid: str, cfg: dict, p: dict, state: dict
    ) -> str:
        boss = cfg.get("boss", {})
        token = cfg.get("token", "代币")
        bname = boss.get("name", "活动Boss")
        factor = float(boss.get("damage_factor", 0.1))
        base_player_damage = int(
            petmod.battle_power(p) * random.uniform(factor * 0.8, factor * 1.2)
        )
        player_damage = max(1, base_player_damage)
        # Boss 反击：对宠物造成伤害
        boss_base_damage = int(boss.get("boss_damage", 100))
        boss_damage = max(1, int(boss_base_damage * random.uniform(0.8, 1.2)))
        nick = p["nickname"]
        # 先结算 Boss 对宠物的伤害
        p["hp"] = max(0, p["hp"] - boss_damage)
        if petmod.is_dead(p):
            p["status"] = "死亡"
            return (
                f"## 👹 {nick} 挑战 {bname}\n"
                f"● {bname} 发起攻击，造成 **{boss_damage}** 伤害！\n"
                f"● 『{nick}』不幸阵亡，挑战失败。\n"
                f"> 发送『宠物复活』或使用『九转还魂丹』复活后再来挑战。"
            )
        store_key = self.store.make_key(group_id, player.get("qq", ""))
        state["damage_rank"][store_key] = (
            state["damage_rank"].get(store_key, 0) + player_damage
        )
        old_hp = state["hp"]
        state["hp"] = max(0, old_hp - player_damage)
        token_per_hit = boss.get("token_per_hit", 0)
        hit_reward = ""
        if token_per_hit:
            self.store.add_event_token(player, eid, token, token_per_hit)
            hit_reward = f"，{token} +{token_per_hit}"
        lines = [
            f"## 👹 {nick} 挑战 {bname}",
            f"● {bname} 造成 **{boss_damage}** 伤害，『{nick}』剩余 HP {p['hp']}/{p['hp_max']}",
            f"● 『{nick}』反击造成 **{player_damage}** 伤害",
            f"● Boss 剩余血量：**{state['hp']}/{state['max_hp']}**{hit_reward}",
        ]
        if state["hp"] <= 0 < old_hp:
            lines.append("\n🏆 **Boss 被击杀！** 奖励已按伤害比例分配给所有参与者。")
            reward_table = self._distribute_boss_kill_rewards(eid, cfg, state, bname)
            lines.append(reward_table)
            respawn = boss.get("respawn_seconds", 3600)
            state["respawn_until"] = int(time.time()) + int(respawn)
            state["hp"] = state["max_hp"]
            state["damage_rank"] = {}
            state["respawn_notified"] = False
            lines.append(f"\n⏳ {bname} 已阵亡，{self._fmt_duration(respawn)} 后复活。")
            self._broadcast_to_authorized_groups(
                f"## 🏆 世界 Boss {bname} 被击杀！\n"
                f"{bname} 已被击败，{self._fmt_duration(respawn)} 后复活。\n"
                f"{reward_table}"
            )
        return "\n".join(lines)

    @staticmethod
    def _roll_value(value: int, max_value: int | None) -> int:
        """在 value 与 max_value 之间随机取值；max_value 无效时返回 value。"""
        value = int(value or 0)
        if max_value is None:
            return value
        max_value = int(max_value)
        if max_value > value:
            return random.randint(value, max_value)
        return value

    @staticmethod
    def _allocate_by_damage(total: int, ratios: list[float]) -> list[int]:
        """按伤害比例分配 total，尽量保证每人都有产出。"""
        n = len(ratios)
        if n == 0 or total <= 0:
            return [0] * n
        # 按比例下取整
        bases = [int(total * r) for r in ratios]
        # 若 total 足够，确保每人至少 1
        if total >= n:
            for i in range(n):
                if bases[i] < 1:
                    bases[i] = 1
        # 不能超过 total
        if sum(bases) > total:
            # 按伤害比例重新归一化
            bases = [max(0, int(total * r)) for r in ratios]
        remainder = total - sum(bases)
        if remainder > 0:
            # 按小数部分从大到小补余数
            fracs = sorted(
                ((i, total * ratios[i] - bases[i]) for i in range(n)),
                key=lambda x: x[1],
                reverse=True,
            )
            for i in range(min(remainder, n)):
                bases[fracs[i][0]] += 1
        return bases

    def _distribute_boss_kill_rewards(
        self, eid: str, cfg: dict, state: dict, bname: str
    ) -> str:
        """按本轮伤害比例发放 Boss 击杀奖励，并记录到各人历史。"""
        boss = cfg.get("boss", {})
        token = cfg.get("token", "代币")
        kill_rewards = boss.get("kill_rewards", [])
        rank = sorted(
            state["damage_rank"].items(), key=lambda x: x[1], reverse=True
        )
        if not rank or not kill_rewards:
            return ""
        total_damage = sum(d for _, d in rank)
        if total_damage <= 0:
            return ""
        ratios = [d / total_damage for _, d in rank]
        all_players = self.store.all_players()
        granted: dict[str, list[str]] = {sk: [] for sk, _ in rank}
        # 按分配权重从高到低依次发放，高权重奖励优先分给高伤害玩家
        sorted_rewards = sorted(
            kill_rewards, key=lambda x: x.get("weight", 1), reverse=True
        )
        for entry in sorted_rewards:
            reward = entry.get("reward", {})
            if not reward:
                continue
            # 物品
            if "item" in reward:
                total = self._roll_value(
                    reward.get("count", 1), reward.get("count_max")
                )
                amounts = self._allocate_by_damage(total, ratios)
                for (sk, _), amt in zip(rank, amounts):
                    if amt <= 0:
                        continue
                    target = all_players.get(sk)
                    if not target:
                        continue
                    partial = dict(reward)
                    partial["count"] = amt
                    self._grant_event_reward(target, eid, cfg, partial)
                    granted[sk].append(f"{reward['item']} x{amt}")
            # 货币
            for cur in self.store.CURRENCY_KEYS:
                if cur not in reward:
                    continue
                total = self._roll_value(reward[cur], reward.get(f"{cur}_max"))
                amounts = self._allocate_by_damage(total, ratios)
                for (sk, _), amt in zip(rank, amounts):
                    if amt <= 0:
                        continue
                    target = all_players.get(sk)
                    if not target:
                        continue
                    partial = dict(reward)
                    partial[cur] = amt
                    self._grant_event_reward(target, eid, cfg, partial)
                    granted[sk].append(f"{cur} +{amt}")
            # 活动代币
            if token in reward:
                total = self._roll_value(reward[token], reward.get(f"{token}_max"))
                amounts = self._allocate_by_damage(total, ratios)
                for (sk, _), amt in zip(rank, amounts):
                    if amt <= 0:
                        continue
                    target = all_players.get(sk)
                    if not target:
                        continue
                    partial = dict(reward)
                    partial[token] = amt
                    self._grant_event_reward(target, eid, cfg, partial)
                    granted[sk].append(f"{token} +{amt}")
        # 记录到个人历史
        now = int(time.time())
        for sk, items in granted.items():
            if not items:
                continue
            target = all_players.get(sk)
            if not target:
                continue
            hist = (
                self.store.player_event_state(target, eid)
                .setdefault("boss_history", [])
            )
            hist.append({"time": now, "boss": bname, "rewards": items})
        # 生成前三名摘要
        top3 = []
        for sk, dmg in rank[:3]:
            target = all_players.get(sk)
            nick = "未知"
            if target and target.get("pet"):
                nick = target["pet"].get("nickname", "未知")
            qq = sk.split("\x1f")[-1] if "\x1f" in sk else sk
            got = "、".join(granted.get(sk, [])) or "无"
            top3.append(f"| {len(top3)+1} | `{qq}` {nick} | {dmg} | {got} |")
        return (
            "**本轮回伤害榜与奖励分配**\n"
            "| 排名 | 玩家 | 伤害 | 获得奖励 |\n"
            "|---|---|---|---|\n"
            + "\n".join(top3)
        )

    def _event_boss_ranking(self, cfg: dict) -> str:
        """显示单个活动 Boss 的当前伤害排行。"""
        state = cfg.get("_boss_state", {})
        rank = sorted(
            state.get("damage_rank", {}).items(), key=lambda x: x[1], reverse=True
        )
        bname = cfg.get("boss", {}).get("name", "活动Boss")
        lines = [f"## 👹 {cfg.get('name','活动')}·{bname} 伤害排行", ""]
        if not rank:
            lines.append("> 本轮暂无挑战记录。")
            return "\n".join(lines)
        lines.append("| 排名 | 玩家 | 宠物 | 伤害 |")
        lines.append("|---|---|---|---|")
        all_players = self.store.all_players()
        for i, (sk, dmg) in enumerate(rank, 1):
            target = all_players.get(sk)
            qq = sk.split("\x1f")[-1] if "\x1f" in sk else sk
            pet_name = "-"
            if target and target.get("pet"):
                pet_name = target["pet"].get("nickname", "-")
            lines.append(f"| {i} | `{qq}` | {pet_name} | {dmg} |")
        return "\n".join(lines)

    def _event_boss_ranking_all(self, group_id: str) -> str:
        """显示当前所有活动 Boss 的伤害排行。"""
        active = self.store.active_events()
        if not active:
            return "当前没有开启的活动。"
        parts = []
        for eid, cfg in active.items():
            if not cfg.get("boss", {}).get("enabled"):
                continue
            parts.append(self._event_boss_ranking(cfg))
        if not parts:
            return "当前没有开启的世界 Boss。"
        return "\n\n".join(parts)

    def _my_boss_rewards(self, player: dict) -> str:
        """查看玩家在所有活动中获得过的 Boss 奖励历史。"""
        event_state = player.get("event_state", {})
        lines = ["## 🎁 我的 Boss 奖励历史", ""]
        has_any = False
        for eid, st in event_state.items():
            hist = st.get("boss_history", [])
            if not hist:
                continue
            cfg = self.store.events().get(eid, {})
            bname = cfg.get("boss", {}).get("name", "活动Boss")
            lines.append(f"**{cfg.get('name', eid)} · {bname}**")
            for entry in hist[-5:]:
                has_any = True
                t = time.strftime(
                    "%Y/%m/%d %H:%M", time.localtime(entry.get("time", 0))
                )
                rewards = "、".join(entry.get("rewards", []))
                lines.append(f"- {t}：{rewards}")
            lines.append("")
        if not has_any:
            lines.append("> 你还没有获得过 Boss 击杀奖励。多参与世界 Boss 挑战吧！")
        return "\n".join(lines)

    def _event_boss_pool(self, cfg: dict) -> str:
        """显示活动 Boss 的击杀奖励池。"""
        boss = cfg.get("boss", {})
        bname = boss.get("name", "活动Boss")
        token = cfg.get("token", "代币")
        kill_rewards = boss.get("kill_rewards", [])
        lines = [f"## 🎁 {cfg.get('name','活动')}·{bname} 奖池", ""]
        if not kill_rewards:
            lines.append("> 当前 Boss 没有设置击杀奖励。")
            return "\n".join(lines)
        lines.append("| 优先级 | 奖励内容 | 提示 |")
        lines.append("|---|---|---|")
        for entry in sorted(
            kill_rewards, key=lambda x: x.get("weight", 1), reverse=True
        ):
            reward_txt = self._format_event_reward(entry.get("reward", {}), token)
            msg = entry.get("msg", "")
            lines.append(f"| {entry.get('weight', 1)} | {reward_txt} | {msg} |")
        lines.append("")
        lines.append("> 击杀后按权重优先分配给伤害排行高的玩家，同时按伤害比例确保参与者都有奖励。")
        return "\n".join(lines)

    def _event_item_def(self, name: str) -> dict | None:
        """从当前生效活动中查找自定义道具定义，并兼容旧版 effect 被多包一层的情况。"""
        for cfg in self.store.active_events().values():
            item = cfg.get("event_items", {}).get(name)
            if item:
                # 旧版网页编辑器把使用效果存成了 {effect:{heal_energy:200}}，这里自动拆包
                eff = item.get("effect", {})
                if (
                    isinstance(eff, dict)
                    and "effect" in eff
                    and isinstance(eff["effect"], dict)
                ):
                    item["effect"] = eff["effect"]
                return item
        return None

    def _event_buy(self, player: dict, eid: str, cfg: dict, item_name: str) -> str | None:
        shop = cfg.get("shop", {})
        if item_name not in shop:
            return None
        it = shop[item_name]
        token = cfg.get("token", "代币")
        today = time.strftime("%Y-%m-%d")
        self.store.reset_event_daily(player, eid, today)
        cost = it.get("cost", {})
        for cur, amt in cost.items():
            if cur == token:
                if self.store.get_event_token(player, eid, token) < amt:
                    return f"购买『{item_name}』需要 {amt} {token}，余额不足。"
            else:
                if self.store.get_currency(player, cur) < amt:
                    return f"购买『{item_name}』需要 {amt} {cur}，余额不足。"
        per_player = it.get("stock", {}).get("per_player")
        if per_player and self.store.event_shop_bought(player, eid, item_name) >= per_player:
            return f"『{item_name}』每人限购 {per_player} 个。"
        global_stock = it.get("stock", {}).get("global")
        if global_stock is not None:
            sold = cfg.setdefault("_sold", {}).get(item_name, 0)
            if sold >= global_stock:
                return f"『{item_name}』已售罄。"
        for cur, amt in cost.items():
            if cur == token:
                self.store.add_event_token(player, eid, token, -amt)
            else:
                self.store.add_currency(player, cur, -amt)
        self.store.inc_event_shop_bought(player, eid, item_name)
        if global_stock is not None:
            cfg["_sold"][item_name] = cfg["_sold"].get(item_name, 0) + 1
        reward = it.get("reward") or {"item": item_name, "count": 1}
        # 兼容旧版/误配置：如果商店奖励写的是效果，则视为该道具的使用效果，购买时只给道具
        if "effect" in reward and "item" not in reward:
            effect = reward["effect"]
            if isinstance(effect, dict):
                cfg.setdefault("event_items", {})[item_name] = {
                    "category": "道具",
                    "usable": True,
                    "desc": it.get("desc", ""),
                    "effect": effect,
                }
            reward = {"item": item_name, "count": 1}
        return self._grant_event_reward(
            player, eid, cfg, reward, prefix=f"购买『{item_name}』成功"
        )

    def _event_gacha(self, player: dict, eid: str, cfg: dict) -> str:
        gacha = cfg.get("gacha", {})
        token = cfg.get("token", "代币")
        today = time.strftime("%Y-%m-%d")
        self.store.reset_event_daily(player, eid, today)
        cmd = gacha.get("cmd", "抽奖")
        limit = gacha.get("daily_limit")
        if limit and self.store.event_daily_count(player, eid, cmd) >= limit:
            return f"今日『{cmd}』次数已用完。"
        pool = gacha.get("pool", [])
        if not pool:
            return "奖池为空。"
        cost = gacha.get("cost", {})
        for cur, amt in cost.items():
            if cur == token:
                if self.store.get_event_token(player, eid, token) < amt:
                    return f"抽奖需要 {amt} {token}，余额不足。"
            else:
                if self.store.get_currency(player, cur) < amt:
                    return f"抽奖需要 {amt} {cur}，余额不足。"
        for cur, amt in cost.items():
            if cur == token:
                self.store.add_event_token(player, eid, token, -amt)
            else:
                self.store.add_currency(player, cur, -amt)
        self.store.inc_event_daily(player, eid, cmd)
        weights = [entry.get("weight", 1) for entry in pool]
        entry = random.choices(pool, weights=weights, k=1)[0]
        msg = entry.get("msg", "🎰 抽奖结果")
        return self._grant_event_reward(player, eid, cfg, entry.get("reward", {}), prefix=msg)

    def _event_gacha_multi(
        self, player: dict, eid: str, cfg: dict, times: int = 10
    ) -> str:
        """活动抽奖 N 连抽：消耗 (times-1) 倍单次价格，结果以 Markdown 表格展示。"""
        gacha = cfg.get("gacha", {})
        token = cfg.get("token", "代币")
        today = time.strftime("%Y-%m-%d")
        self.store.reset_event_daily(player, eid, today)
        cmd = gacha.get("cmd", "抽奖")
        limit = gacha.get("daily_limit")
        current = self.store.event_daily_count(player, eid, cmd)
        if limit and current + times > limit:
            return (
                f"今日『{cmd}』剩余次数不足进行 {times} 连抽（剩余 {limit - current} 次）。"
            )
        pool = gacha.get("pool", [])
        if not pool:
            return "奖池为空。"
        cost = gacha.get("cost", {})
        multi_cost = {cur: amt * (times - 1) for cur, amt in cost.items()}
        for cur, amt in multi_cost.items():
            if cur == token:
                if self.store.get_event_token(player, eid, token) < amt:
                    return f"{times}连抽需要 {amt} {token}，余额不足。"
            else:
                if self.store.get_currency(player, cur) < amt:
                    return f"{times}连抽需要 {amt} {cur}，余额不足。"
        for cur, amt in multi_cost.items():
            if cur == token:
                self.store.add_event_token(player, eid, token, -amt)
            else:
                self.store.add_currency(player, cur, -amt)
        for _ in range(times):
            self.store.inc_event_daily(player, eid, cmd)
        weights = [entry.get("weight", 1) for entry in pool]
        entries = random.choices(pool, weights=weights, k=times)
        cost_txt = " / ".join(f"{v} {k}" for k, v in multi_cost.items())
        lines = [
            f"## 🎰 {cmd}{times}连抽结果",
            f"消耗：{cost_txt}",
            "",
            "| 序号 | 奖品 |",
            "|---:|:---|",
        ]
        for i, entry in enumerate(entries, 1):
            msg = entry.get("msg", "🎰 奖励")
            reward_txt = self._grant_event_reward(
                player, eid, cfg, entry.get("reward", {}), prefix=msg
            )
            cell = reward_txt.replace("\n", "<br>")
            lines.append(f"| {i} | {cell} |")
        return "\n".join(lines)

    def _grant_event_reward(self, player: dict, eid: str, cfg: dict, reward: dict, prefix: str = "") -> str:
        token = cfg.get("token", "代币")
        p = self._need_pet(player)
        lines = [prefix] if prefix else []
        if "item" in reward:
            count = reward.get("count", 1)
            count_max = reward.get("count_max")
            if count_max is not None and count_max > count:
                count = random.randint(count, count_max)
            self.store.add_item(player, reward["item"], count)
            lines.append(f"获得 {reward['item']} x{count}")
        if "effect" in reward and p:
            eff_msg = self._apply_effect(p, reward["effect"], reward.get("item", "奖励"))
            lines.append(eff_msg)
        for cur in self.store.CURRENCY_KEYS:
            if cur in reward:
                amt = reward[cur]
                amt_max = reward.get(f"{cur}_max")
                if amt_max is not None and amt_max > amt:
                    amt = random.randint(amt, amt_max)
                self.store.add_currency(player, cur, amt)
                lines.append(f"{cur} +{amt}")
        if token in reward:
            amt = reward[token]
            amt_max = reward.get(f"{token}_max")
            if amt_max is not None and amt_max > amt:
                amt = random.randint(amt, amt_max)
            self.store.add_event_token(player, eid, token, amt)
            lines.append(f"{token} +{amt}")
        return "\n".join(lines)

    def _format_event_reward(self, reward: dict, token: str = "代币") -> str:
        """把单个奖励对象格式化为人类可读文本（支持随机范围）。"""
        parts = []
        if "item" in reward:
            count = reward.get("count", 1)
            count_max = reward.get("count_max")
            range_txt = f"{count}~{count_max}" if count_max and count_max > count else str(count)
            parts.append(f"{reward['item']} x{range_txt}")
        if "effect" in reward:
            eff = reward["effect"]
            for k, v in eff.items():
                parts.append(f"效果 {k}:{v}")
        for cur in self.store.CURRENCY_KEYS:
            if cur in reward:
                v = reward[cur]
                v_max = reward.get(f"{cur}_max")
                range_txt = f"{v}~{v_max}" if v_max and v_max > v else str(v)
                parts.append(f"{cur} +{range_txt}")
        if token in reward:
            v = reward[token]
            v_max = reward.get(f"{token}_max")
            range_txt = f"{v}~{v_max}" if v_max and v_max > v else str(v)
            parts.append(f"{token} +{range_txt}")
        return "、".join(parts) if parts else "无奖励"

    def _format_event_rewards(self, reward_texts: dict) -> str:
        parts = []
        for k, v in reward_texts.items():
            parts.append(f"{k} +{v}")
        return "、".join(parts) if parts else ""

    # =====================================================================
    # 帮助 / 信息查询
    # =====================================================================
    def _handle_info(self, cmd: str, tokens: list[str]) -> str | None:
        if cmd in ("宠物菜单", "宠物指令", "宠物帮助"):
            return self._menu_text()
        if cmd == "宠物种类":
            name = self._arg(tokens, 1)
            if name and name in data.SPECIES:
                element = data.SPECIES[name]
                text = (
                    f"## 📖 {name}\n"
                    f"● **默认属性**：{element}\n"
                    f"● 可通过『砸蛋』抽取，部分种类可在『宠物市场』购买"
                )
                return text, images.pet_image_md(name)
            names = " · ".join(data.SPECIES_NAMES)
            return (
                f"## 📖 宠物种类（共 {len(data.SPECIES_NAMES)} 种）\n{names}\n\n"
                f"> 发送 `宠物种类 名称` 可查看单个种类及其图片"
            )
        if cmd == "属性":
            return (
                "## 🌀 属性克制\n"
                "> PK 时克制方额外 **+50%** 战力\n\n"
                "- 金 → 木 → 土 → 水 → 火 → 金\n"
                "- 风 → 雷 → 冰 → 风\n"
                "- 光 → 暗 → 光"
            )
        if cmd == "神器":
            lines = ["## 🗡️ 神器一览", "> 佩戴提供武器战力加成", ""]
            for n, v in data.ARTIFACTS.items():
                lines.append(f"- **{n}**（需 Lv{v['level_req']}）：{v['desc']}")
            return "\n".join(lines)
        if cmd == "秘技":
            lines = ["## 📜 秘技一览", "> 参悟后提供秘技战力加成", ""]
            for n, v in data.SKILLS.items():
                lines.append(
                    f"- **{n}**（需 Lv{v['level_req']}/智力{v['intel_req']}）：{v['desc']}"
                )
            return "\n".join(lines)
        if cmd == "仙丹":
            lines = ["## 💊 仙丹一览", ""]
            for n, v in data.ELIXIRS.items():
                lines.append(f"- **{n}**：{v['desc']}")
            return "\n".join(lines)
        if cmd == "天赋":
            lines = ["## ✨ 天赋一览", "> 每只宠物只能拥有 1 种天赋，可重复觉醒", ""]
            for n, v in data.TALENTS.items():
                tag = "（需定制）" if v["need_custom"] else ""
                lines.append(f"- **{n}**{tag}：{v['desc']}")
            return "\n".join(lines)
        if cmd == "状态":
            return (
                "## 🩺 宠物状态\n"
                + " / ".join(data.STATUSES)
                + "\n\n> 异常状态需喂食对应药品恢复，例如 `喂食 解毒剂` 可解除中毒。"
            )
        if cmd == "查看说明":
            if len(tokens) < 2:
                return "⚠️ 用法：`查看说明 物品名称`（例如：查看说明 九转还魂丹）"
            name = tokens[1]
            if name in data.ITEMS:
                info = data.ITEMS[name]
                lines = [f"## 📘 {name}", info.get("desc", "")]
                eff = info.get("effect")
                if eff:
                    lines.append(f"\n> 效果：{self._format_effect(eff)}")
                lines.append(
                    f"\n> 分类：{info.get('category', '未分类')} | "
                    f"价格：{info.get('price', 0)} {info.get('currency', '')} | "
                    f"{'可使用 ✅' if info.get('usable') else '不可使用 ❌'}"
                )
                return "\n".join(lines)
            if name in data.ELIXIRS:
                info = data.ELIXIRS[name]
                return (
                    f"## 📘 {name}\n"
                    f"{info.get('desc', '')}\n\n"
                    f"> 效果：{self._format_effect(info.get('effect', {}))}"
                )
            if name in data.ARTIFACTS:
                info = data.ARTIFACTS[name]
                return f"## 📘 {name}\n{info.get('desc', '')}\n\n> 等级要求：Lv{info.get('level_req')}"
            if name in data.SKILLS:
                info = data.SKILLS[name]
                return f"## 📘 {name}\n{info.get('desc', '')}"
            if name in data.TALENTS:
                info = data.TALENTS[name]
                tag = "（需定制宠物）" if info.get("need_custom") else ""
                return f"## 📘 {name}{tag}\n{info.get('desc', '')}"
            # 活动自定义道具
            event_item = self._event_item_def(name)
            if event_item:
                effect = event_item.get("effect", {})
                eff_txt = self._format_effect(effect)
                return (
                    f"## 📘 {name}\n"
                    f"{event_item.get('desc', '')}\n\n"
                    f"> 分类：{event_item.get('category', '道具')} | "
                    f"{'可使用 ✅' if event_item.get('usable') else '不可使用 ❌'} | "
                    f"效果：{eff_txt}"
                )
            return f"❓ 未找到『{name}』的说明。"
        return None

    @staticmethod
    def _fmt_duration(sec: int) -> str:
        m, s = divmod(int(sec), 60)
        if m and s:
            return f"{m}分{s}秒"
        if m:
            return f"{m}分钟"
        return f"{s}秒"

    @staticmethod
    def _format_effect(eff: dict) -> str:
        """把 effect 字典格式化为人类可读文本。"""
        if not eff:
            return "无"
        parts = []
        for k, v in eff.items():
            if k == "heal_hp":
                parts.append(f"恢复 {v} 血量")
            elif k == "heal_energy":
                parts.append(f"恢复 {v} 精力")
            elif k == "add_exp":
                parts.append(f"经验 +{v}")
            elif k == "add_hp_max":
                parts.append(f"生命上限 +{v}")
            elif k == "add_energy_max":
                parts.append(f"精力上限 +{v}")
            elif k == "add_atk":
                parts.append(f"攻击 +{v}")
            elif k == "add_def":
                parts.append(f"防御 +{v}")
            elif k == "add_intel":
                parts.append(f"智力 +{v}")
            elif k == "mood":
                parts.append(f"心情设为 {v} 星")
            elif k == "cure":
                parts.append(f"解除『{v}』状态")
            elif k == "revive":
                parts.append("复活并回满血量")
            elif k == "force_evolve":
                parts.append("强制进化")
            elif k == "upgrade_quality":
                parts.append(f"品质提升为【{v}】")
            elif k == "clear_abyss_corruption":
                parts.append(f"清除 {v} 点深渊侵蚀")
            elif k == "freeze_hours":
                parts.append(f"假死 {v} 小时")
            elif k == "cure_all":
                parts.append("解除所有限制和异常")
            elif k == "kill":
                parts.append("立即死亡")
            else:
                parts.append(f"{k}:{v}")
        return "、".join(parts) if parts else "无"

    def _cooldown_block(self, player: dict, key: str, label: str) -> str | None:
        """若该行为仍在冷却中，返回提示文本；否则返回 None。"""
        remain = self.store.cooldown_remaining(player, key)
        if remain > 0:
            return f"⏳ **{label}** 冷却中，还需 `{self._fmt_duration(remain)}`。"
        return None

    def _my_info(self, player: dict, group_id: str) -> str:
        gid = group_id if group_id and group_id != "private" else "私聊"
        lines = [
            "## 📇 我的信息",
            "━━━━━━━━━━━━━━",
            f"🆔 **QQ号**　`{player['qq']}`",
            f"👥 **群号**　`{gid}`",
            f"🪙 **金币**　{player.get('coin', 0)}",
            f"💎 **积分**　{player.get('jifen', 0)}",
            f"💠 **钻石**　{player.get('diamond', 0)}",
            f"🌀 **深渊结晶**　{self.store.get_abyss_crystal(player)}",
        ]
        active = self.store.active_events()
        if active:
            lines.append("━━━━━━━━━━━━━━")
            lines.append("**🎉 活动代币**")
            for eid, cfg in active.items():
                token = cfg.get("token", "代币")
                bal = self.store.get_event_token(player, eid, token)
                lines.append(f"• {cfg.get('name', eid)} {token}：{bal}")
        return "\n".join(lines)

    def _accept_invite(self, player: dict, group_id: str, tokens: list[str]) -> str:
        """被邀请用户发送『受邀 用户ID』，双方均在本群时发放邀请奖励。"""
        if len(tokens) < 2:
            return "⚠️ 用法：`受邀 用户ID`（例如：受邀 7FC131A00B...）"
        inviter_qq = str(tokens[1]).strip()
        invitee_qq = str(player.get("qq", ""))
        if not inviter_qq:
            return "❌ 邀请人ID不能为空。"
        if inviter_qq == invitee_qq:
            return "❌ 不能邀请自己哦。"
        inviter = self.store.get_player(inviter_qq, group_id, create=False)
        if not inviter:
            return f"❌ 用户 `{inviter_qq}` 不在本群或未注册。"
        # 杜绝互相邀请/反向接受：自己已经邀请过对方，就不能再变成对方的被邀请人
        if self.store.is_already_invited_by(player, inviter_qq):
            return "❌ 你已经邀请过该用户，不能反向接受邀请。"
        if self.store.invited_by(player):
            return "❌ 你已经接受过他人邀请，无法重复接受。"
        if self.store.is_already_invited_by(inviter, invitee_qq):
            return f"❌ 用户 `{inviter_qq}` 已经邀请过你啦。"
        # 记录邀请关系并发放奖励
        self.store.record_invite(inviter, player)
        rewards = [
            ("金币", self.invite_coin),
            ("积分", self.invite_jifen),
            ("钻石", self.invite_diamond),
        ]
        for p in (inviter, player):
            for currency, amount in rewards:
                self.store.add_currency(p, currency, amount)
        reward_text = "、".join(
            [f"{c} +{a}" for c, a in rewards]
        )
        return (
            f"## 🎉 邀请成功\n"
            f"你已成功接受 `{inviter_qq}` 的邀请！\n"
            f"双方各获得：**{reward_text}**\n"
            f"> 发送 `我的邀请情况` 可查看自己邀请的好友列表。"
        )

    def _my_invites(self, player: dict) -> str:
        """以 Markdown 表格展示当前玩家邀请的所有用户。"""
        users = self.store.get_invited_users(player)
        if not users:
            return (
                "## 📋 我的邀请情况\n"
                "你还没有成功邀请过好友。\n"
                "> 让好友发送 `受邀 你的用户ID`，双方即可领取奖励！"
            )
        lines = [
            "## 📋 我的邀请情况",
            f"累计邀请：**{len(users)}** 人",
            "",
            "| 序号 | 用户ID | 邀请时间 |",
            "|---:|---|---|",
        ]
        for i, entry in enumerate(users, 1):
            qq = entry.get("qq", "")
            at = entry.get("at", 0)
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(at)) if at else "-"
            lines.append(f"| {i} | `{qq}` | {ts} |")
        return "\n".join(lines)

    def _sign_in(self, player: dict, group_id: str) -> str:
        today = time.strftime("%Y-%m-%d")
        if player.get("sign_last") == today:
            return (
                "📅 今天已经签到过啦，明天再来吧～\n"
                f"> 累计签到 {player.get('sign_total', 0)} 天"
                f"，连续 {player.get('sign_streak', 0)} 天"
            )
        # 连续签到：昨天签过则 +1，否则连续天数重置为 1
        yesterday = time.strftime(
            "%Y-%m-%d", time.localtime(time.time() - 86400)
        )
        streak = player.get("sign_streak", 0)
        streak = streak + 1 if player.get("sign_last") == yesterday else 1
        total = player.get("sign_total", 0) + 1
        player["sign_last"] = today
        player["sign_streak"] = streak
        player["sign_total"] = total

        order = self.store.next_sign_order(group_id, today)
        jifen = random.randint(self.sign_jifen_min, self.sign_jifen_max)
        coin = random.randint(self.sign_coin_min, self.sign_coin_max)
        extra = min(streak, 7) * self.sign_streak_bonus
        self.store.add_currency(player, "积分", jifen)
        self.store.add_currency(player, "金币", coin + extra)

        title, need, nxt = data.sign_title(total)
        now = time.strftime("%Y/%m/%d %H:%M:%S")
        lines = [
            "签到成功！",
            "==================",
            f"●今日是第：{order}位签到的！",
            f"●获得积分：{jifen}",
            f"●获得金币：{coin}",
            f"●累计签到：{total}天",
            f"●额外金币：{extra}",
            f"●当前称号：{title}",
        ]
        if need and nxt:
            lines.append(f"Tips：再签到{need}天即可成为{nxt}了哦！")
        else:
            lines.append("Tips：你已是最高称号，恭喜成为宠园传说！")
        lines.append(now)
        return "\n".join(lines)

    def _redeem(self, player: dict, group_id: str, qq: str, tokens: list[str]) -> str:
        if len(tokens) < 2:
            return "⚠️ 用法：`兑换 卡密`（例如：兑换 ABCD23XY...）"
        code = tokens[1].strip()
        used_by = self.store.make_key(group_id, qq)
        rewards, err = self.store.redeem_card(code, player, used_by)
        if rewards is None:
            return f"❌ 兑换失败：{err}"
        lines = [
            "## 🎉 兑换成功",
            "━━━━━━━━━━━━━━",
            f"🎟 **卡密**　`{code.upper()}`",
        ]
        for cur, amt in rewards.items():
            lines.append(f"✅ **获得**　{cur} +{amt}")
        lines.append("━━━━━━━━━━━━━━")
        for cur in rewards:
            lines.append(f"💼 **当前{cur}**　{self.store.get_currency(player, cur)}")
        return "\n".join(lines)

    def _pay_link(self) -> str:
        return (
            "## 💎 宠物乐园 · 充值中心\n"
            "━━━━━━━━━━━━━━\n"
            "🛒 **商店链接**：https://pay.ldxp.cn/shop/2P5XIVMD\n\n"
            "📌 **购买后请复制卡密，然后在本群发送**：\n"
            "```\n兑换 你的卡密\n```\n"
            "例如：`兑换 ABCD1234EFGH`\n\n"
            "卡密可兑换金币、积分、钻石等，具体以商品说明为准。"
        )

    def _admin_adjust(
        self, event, qq: str, group_id: str, cmd: str, tokens: list[str]
    ) -> str:
        if "钻石" in cmd:
            currency = "钻石"
        elif "金币" in cmd:
            currency = "金币"
        else:
            currency = "积分"
        sign = 1 if cmd.startswith("加") else -1
        is_super = self._is_admin(event)
        is_sub = self._is_subadmin(group_id, qq)
        if not (is_super or is_sub):
            return "❌ 仅管理员可增减用户金币/积分/钻石。"
        # 目标 ID 不一定是纯数字（QQ 官方机器人/频道为 openid 字符串），
        # 仅要求最后给出的数量是整数。
        if len(tokens) < 3 or not tokens[2].lstrip("-").isdigit():
            return f"用法：{cmd} QQ号/ID 数量"
        target = tokens[1]
        amount = int(tokens[2])
        if amount <= 0:
            return f"用法：{cmd} QQ号/ID 数量（数量需为正整数）"
        # 小管理员：仅限本群、仅金币/积分、加币有每日额度、减币不限
        if not is_super:
            if currency == "钻石":
                return "❌ 小管理员无权增减钻石（仅大管理员可操作钻石）。"
            if sign > 0:
                actor = self.store.get_player(qq, group_id)
                quota = self._subadmin_quota(actor)
                key = "coin" if currency == "金币" else "jifen"
                used = quota.get(key, 0)
                limit = self.subadmin_daily_add_limit
                if used + amount > limit:
                    remain = max(0, limit - used)
                    return (
                        f"❌ 小管理员每日增加{currency}上限 {limit}，今日已增加 {used}，"
                        f"剩余 {remain}，本次 {amount} 超出额度。"
                    )
        tp, err = self._find_target(group_id, target)
        if err:
            return err
        before = self.store.get_currency(tp, currency)
        self.store.add_currency(tp, currency, sign * amount)
        after = self.store.get_currency(tp, currency)
        # 记录小管理员当日已用加币额度
        if not is_super and sign > 0:
            actor = self.store.get_player(qq, group_id)
            quota = self._subadmin_quota(actor)
            key = "coin" if currency == "金币" else "jifen"
            quota[key] = quota.get(key, 0) + amount
        verb = "增加" if sign > 0 else "减少"
        icon = "🪙" if currency == "金币" else ("💠" if currency == "钻石" else "💎")
        extra = ""
        if not is_super and sign > 0:
            key = "coin" if currency == "金币" else "jifen"
            used = self._subadmin_quota(self.store.get_player(qq, group_id)).get(key, 0)
            extra = (
                f"\n> 🛡️ 小管理今日{currency}已增加 {used}/"
                f"{self.subadmin_daily_add_limit}"
            )
        return (
            f"## ⚙️ 管理操作\n"
            f"已为用户 `{target}` {verb}{icon}**{currency} {amount}**\n"
            f"> {currency}：{before} → **{after}**{extra}"
        )

    # --------------------------- 小管理员 ---------------------------
    def _is_subadmin(self, group_id: str, qq: str) -> bool:
        # 小管理员身份随本群授权有效而有效：授权失效则自动失去权限
        if not self._is_group_authorized(group_id):
            return False
        group = self.store.get_group(group_id)
        return str(qq) in [str(x) for x in group.get("subadmins", [])]

    def _subadmin_quota(self, player: dict) -> dict:
        """取玩家当日小管理加币额度记录（跨天自动清零）。返回可变 dict。"""
        today = time.strftime("%Y-%m-%d")
        quota = player.get("subadmin_quota")
        if not isinstance(quota, dict) or quota.get("day") != today:
            quota = {"day": today, "coin": 0, "jifen": 0}
            player["subadmin_quota"] = quota
        return quota

    def _manage_subadmin(
        self, event, group_id: str, cmd: str, tokens: list[str]
    ) -> str:
        if not self._is_admin(event):
            return "❌ 仅大管理员可任命/撤销小管理员。"
        if len(tokens) < 2 or not tokens[1].strip():
            return f"用法：{cmd} QQ号/ID"
        target = tokens[1].strip()
        group = self.store.get_group(group_id)
        subs = [str(x) for x in group.get("subadmins", [])]
        appoint = cmd.startswith("任命")
        if appoint:
            if target in subs:
                return f"用户 `{target}` 已经是本群小管理员。"
            subs.append(target)
            group["subadmins"] = subs
            return (
                f"## 🛡️ 小管理员任命\n已任命 `{target}` 为本群小管理员。\n"
                f"> 权限：本群内『加金币/减金币/加积分/减积分』（不可操作钻石）；"
                f"每日增加金币、积分各上限 {self.subadmin_daily_add_limit}，减少不限。"
            )
        else:
            if target not in subs:
                return f"用户 `{target}` 不是本群小管理员。"
            subs.remove(target)
            group["subadmins"] = subs
            return f"## 🛡️ 小管理员撤销\n已撤销 `{target}` 的本群小管理员权限。"

    def _list_subadmins(self, event) -> str:
        if not self._is_admin(event):
            return "❌ 仅大管理员可查看小管理员列表。"
        groups = self.store._data.get("groups", {})
        lines = ["## 🛡️ 小管理员一览（全服）", "━━━━━━━━━━━━━━"]
        found = False
        for gid, g in groups.items():
            subs = [str(x) for x in g.get("subadmins", [])]
            if not subs:
                continue
            found = True
            lines.append(f"**群 `{gid}`**")
            for u in subs:
                lines.append(f"　• `{u}`")
        if not found:
            return "目前没有任何群任命了小管理员。"
        return "\n".join(lines)

    def _my_admin_quota(self, event, qq: str, group_id: str) -> str:
        is_super = self._is_admin(event)
        is_sub = self._is_subadmin(group_id, qq)
        if is_super:
            return (
                "## 🛡️ 管理额度\n你是**大管理员**，增减金币/积分/钻石均无每日上限。"
            )
        if not is_sub:
            return "你不是本群管理员。"
        actor = self.store.get_player(qq, group_id)
        quota = self._subadmin_quota(actor)
        limit = self.subadmin_daily_add_limit
        coin_used = quota.get("coin", 0)
        jifen_used = quota.get("jifen", 0)
        return (
            "## 🛡️ 我的管理额度（本群 · 今日）\n"
            "━━━━━━━━━━━━━━\n"
            f"🪙 **金币**　已增加 {coin_used} / {limit}　·　剩余 **{max(0, limit - coin_used)}**\n"
            f"💎 **积分**　已增加 {jifen_used} / {limit}　·　剩余 **{max(0, limit - jifen_used)}**\n"
            "> 减少金币/积分不受限；不可增减钻石。额度每日 0 点自动重置。"
        )

    # --------------------------- 群授权 ---------------------------
    def _is_group_authorized(self, group_id: str) -> bool:
        if not self._is_group(group_id):
            return True  # 私聊不受群授权限制
        group = self.store.get_group(group_id)
        return int(group.get("auth_until", 0) or 0) > int(time.time())

    def _extend_group_auth(self, group_id: str, days: int) -> int:
        """延长群授权 days 天（未过期则在原到期时间上叠加，已过期从现在起算）。
        若此前已过期/从未授权，则清空旧的小管理员（其身份随上次授权结束而消失）。"""
        group = self.store.get_group(group_id)
        now = int(time.time())
        cur = int(group.get("auth_until", 0) or 0)
        if cur <= now:
            base = now
            group["subadmins"] = []
        else:
            base = cur
        group["auth_until"] = base + int(days) * 86400
        return group["auth_until"]

    @staticmethod
    def _fmt_remain(until: int) -> str:
        remain = int(until) - int(time.time())
        if remain <= 0:
            return "已到期"
        days, rem = divmod(remain, 86400)
        hours, rem = divmod(rem, 3600)
        mins = rem // 60
        if days > 0:
            return f"{days} 天 {hours} 小时"
        if hours > 0:
            return f"{hours} 小时 {mins} 分钟"
        return f"{mins} 分钟"

    def _auth_status(self, group_id: str) -> str:
        if not self._is_group(group_id):
            return "私聊不受群授权限制。"
        group = self.store.get_group(group_id)
        until = int(group.get("auth_until", 0) or 0)
        if until <= 0:
            return (
                "## 🔐 本群授权状态\n状态：**未授权** ❌\n"
                "> 宠物乐园需授权后使用。请发送『授权 卡密』激活，或联系管理员。"
            )
        ok = until > int(time.time())
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(until))
        return (
            "## 🔐 本群授权状态\n"
            f"状态：{'**有效** ✅' if ok else '**已过期** ❌'}\n"
            f"到期时间：{when}\n"
            f"剩余：**{self._fmt_remain(until)}**"
            + ("" if ok else "\n> 请发送『授权 卡密』续期。")
        )

    def _redeem_auth_card(self, event, group_id: str, qq: str, tokens: list[str]) -> str:
        if not self._is_group(group_id):
            return "授权卡只能在群聊内兑换。"
        if len(tokens) < 2 or not tokens[1].strip():
            return "用法：授权 卡密"
        code = tokens[1].strip()
        used_by = self.store.make_key(group_id, qq)
        days, err = self.store.redeem_auth_card(code, used_by)
        if days is None:
            return f"❌ 授权失败：{err}"
        until = self._extend_group_auth(group_id, days)
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(until))
        # 非大管理员激活本群者，自动升级为本群小管理员（随本群授权失效而失效）
        promoted = ""
        if not self._is_admin(event):
            group = self.store.get_group(group_id)
            subs = [str(x) for x in group.get("subadmins", [])]
            if str(qq) not in subs:
                subs.append(str(qq))
                group["subadmins"] = subs
            promoted = (
                f"\n> 🛡️ 你已成为**本群小管理员**（可加减本群金币/积分，"
                f"每日加币各上限 {self.subadmin_daily_add_limit}）；该身份随本群授权失效而消失。"
            )
        return (
            "## 🔓 群授权成功\n"
            f"本群授权 **+{days} 天**！\n到期时间：{when}\n"
            f"剩余：**{self._fmt_remain(until)}**" + promoted
        )

    def _grant_auth(self, event, group_id: str, tokens: list[str]) -> str:
        if not self._is_admin(event):
            return "❌ 仅大管理员可直接授权本群。"
        if not self._is_group(group_id):
            return "请在群聊内使用本指令。"
        if len(tokens) < 2 or not tokens[1].lstrip("-").isdigit():
            return "用法：授权本群 天数（正数延长，负数缩短）"
        days = int(tokens[1])
        if days == 0:
            return "用法：授权本群 天数（不能为 0）"
        until = self._extend_group_auth(group_id, days)
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(until))
        verb = "延长" if days > 0 else "缩短"
        return (
            "## 🔐 大管理员授权\n"
            f"已为本群{verb} **{abs(days)} 天**。\n到期时间：{when}\n"
            f"剩余：**{self._fmt_remain(until)}**"
        )

    @staticmethod
    def _auth_blocked_text() -> str:
        return (
            "## 🔒 宠物乐园未授权\n"
            "本群授权未激活或已到期，暂时无法使用宠物乐园。\n"
            "> 发送『授权 卡密』激活，或『授权状态』查看；管理员可联系作者获取授权卡。"
        )

    def _menu_text(self) -> str:
        event_lines = []
        active = self.store.active_events()
        if active:
            event_lines.append("")
            event_lines.append("**🎉 限时活动**")
            for cfg in active.values():
                menu = cfg.get("menu_cmd", "活动菜单")
                event_lines.append(f"{cfg.get('name', '活动')}：发送 `{menu}` 查看")
            event_lines.append("")
        return "\n".join(
            [
                "# 🐾 宠物乐园 · 指令菜单",
                "",
                "**🐣 入门**",
                "砸蛋 · 购买宠物 · 我的宠物 · 宠物状态 · 宠物改名 · 宠物变性 · 赠送宠物 用户ID · 放生宠物 · 宠物侦查 用户ID",
                "",
                "**🛒 商城 / 背包**",
                "宠物商城 · 道具商城 · 宠物市场 · 查看背包 · 购买 物品 数量 · 使用 物品 · 出售 物品 数量 · 丢弃 物品 数量 · 转让 用户ID 物品 数量 · 清空背包 · 查看说明 物品名",
                "",
                "**🍖 喂养 / 日常**（各 10~20 分钟冷却）",
                "喂食 物品 · " + " · ".join(data.DAILY_ACTIONS),
                "",
            ]
            + event_lines
            + [
                "**📈 成长**",
                "一键升级宠物 · 宠物升级 [次数] · 宠物进化 · 宠物飞升 · 宠物渡劫 · 幻境寻宝 · 宠物神仙劫 · 合成卡 目标卡名",
                "> 宠物每突破 60 级赠『史诗卡』，`使用 史诗卡` 可将品质升为史诗；10 张低一级品质卡可 `合成卡` 为高一级卡（如 10 史诗卡 → 1 圣灵卡）。",
                "",
                "**🗡️ 神器 / 秘技**",
                "打造神器 名称 · 佩戴神器 名称 · 卸下神器 · 参悟秘技 名称 · 遗忘秘技",
                "",
                "**✨ 天赋 / 炼丹**",
                "宠物觉醒 · 制作天赋符 · 使用天赋符 天赋 · 炼丹 · 使用仙丹 名称 用户ID 数量 · 治愈 用户ID · 复活 用户ID · 精力转移 用户ID 值",
                "",
                "**⚔️ 对战 / 排行**",
                "宠物攻击 用户ID · 跨群挑战宠物 [群号 用户ID] · 宠物排行（本群） · 宠物神榜（全服） · 领取神榜奖励",
                "",
                "**🏰 副本 / 任务**（副本 15 分钟冷却）",
                "宠物副本 · 进入副本 名称 · 深渊秘境 · 深渊介绍 · 深渊商店 · 深渊祝福 · 宠物剧情任务 · 领取任务 名称 · 提交任务 名称 · 我的剧情任务 · 取消剧情任务",
                "",
                "**💕 姻缘**",
                "宠物追求 用户ID · 同意追求 用户ID · 宠物求婚 用户ID · 同意求婚 用户ID · 宠物分手 · 宠物离婚 · 宠物恋情",
                "",
                "**📇 个人**",
                "我的信息（查看 QQ号/群号/金币/积分/钻石/活动代币） · 签到（每日领积分金币） · 我要氪金（获取充值链接与卡密使用方法） · 兑换 卡密（卡密充值金币/积分/钻石） · 赠送金币 用户ID 数量 · 赠送积分 用户ID 数量 · 赠送钻石 用户ID 数量 · 我的邀请情况 · 受邀 用户ID",
                "",
                "📖 图鉴查询",
                "宠物种类（加名称看单个种类及图片，如 宠物种类 皮卡丘） · 属性 · 状态 · 神器 · 秘技 · 仙丹 · 天赋 · 查看说明 名称",
                "",
                "**⚙️ 管理员**",
                "开启/关闭宠物乐园 · 开启/关闭宠物跨群 · 加金币 QQ 数量 · 减金币 QQ 数量 · 加积分 QQ 数量 · 减积分 QQ 数量 · 加钻石 QQ 数量 · 减钻石 QQ 数量",
                "任命小管理 QQ · 撤销小管理 QQ · 小管理列表（大管理员查看全服）· 我的管理额度（小管理员查看今日额度）",
                "授权状态（查看本群授权）· 授权 卡密（用授权卡激活/续期本群）· 授权本群 天数（大管理员直接授权）",
                "",
                "> 💡 指令均无需前缀，直接发送即可。\n"
                "> 👤 需指定对方时请**直接填用户ID/QQ号**（不支持 @）；所有数据按群独立，神榜为全服排行。",
            ]
        )

    # =====================================================================
    # 商城
    # =====================================================================
    def _shop_text(self, which: str) -> str:
        if which == "宠物商城":
            wanted = [
                "红药水",
                "蓝药水",
                "九转还魂丹",
                "变性药水",
                "永恒钻戒",
                "改名卡",
            ]
            title = "## 🛒 宠物商城"
        else:
            wanted = [
                "永恒钻戒",
                "三明治",
                "大补丸",
                "镇定剂",
                "疏筋丸",
                "清醒剂",
                "解毒剂",
                "九转还魂丹",
                "进化神石",
                "万能宝石",
                "小精力瓶",
                "中精力瓶",
                "大精力瓶",
                "普通经验书",
                "五色药",
                "净化药水",
                "智力宝符",
                "智力仙符",
                "智力神符",
                "精力宝符",
                "精力仙符",
                "精力神符",
                "攻击宝符",
                "攻击仙符",
                "攻击神符",
                "防御宝符",
                "防御仙符",
                "防御神符",
                "生命宝符",
                "生命仙符",
                "生命神符",
            ]
            title = "## 🏪 道具商城"
        lines = [title, "> 购买方式：`购买 物品名 数量`", ""]
        for n in wanted:
            it = data.ITEMS[n]
            lines.append(f"- **{n}** — {it['price']} {it['currency']}")
        return "\n".join(lines)

    def _pet_market_text(self) -> str:
        lines = [
            "## 🐾 宠物市场 / 宠物专域",
            "> 购买方式：`购买宠物 宠物名 [品质]`",
            "",
        ]
        for n, p in data.PET_MARKET.items():
            lines.append(f"- **{n}** — {p} 积分")
        lines.append("")
        lines.append("> 品质可选：" + " / ".join([q for q in data.QUALITIES if q not in data.PET_MARKET_BANNED_QUALITIES]) + "（默认普通，高品质加价；圣灵/洪荒/创世/混沌为活动/定制限定，不可在市场购买）")
        return "\n".join(lines)

    # =====================================================================
    # 获取宠物
    # =====================================================================
    @staticmethod
    def _roll_quality() -> str:
        names = list(data.QUALITY_WEIGHT.keys())
        weights = list(data.QUALITY_WEIGHT.values())
        return random.choices(names, weights=weights, k=1)[0]

    def _smash_egg(self, player: dict) -> str:
        if player.get("pet"):
            return "你已经有宠物啦！如需更换请先『放生宠物』。"
        cd = self._cooldown_block(player, "砸蛋", "砸蛋")
        if cd:
            return cd
        species = random.choice(data.SPECIES_NAMES)
        quality = self._roll_quality()
        player["pet"] = petmod.new_pet(species, quality)
        self.store.set_cooldown(player, "砸蛋", data.EGG_COOLDOWN)
        return (
            f"💥 **砸蛋成功！**\n获得 【{quality}】品质的 **{species}**！\n"
            "> 发送 `我的宠物` 查看详情。"
        )

    def _buy_pet(self, player: dict, tokens: list[str]) -> str:
        if player.get("pet"):
            return "你已经有宠物啦！如需更换请先『放生宠物』。"
        if len(tokens) < 2:
            return "用法：购买宠物 宠物名 [品质]"
        species = tokens[1]
        if species not in data.PET_MARKET:
            return f"宠物市场没有『{species}』，发送『宠物市场』查看在售宠物。"
        quality = (
            tokens[2] if len(tokens) > 2 and tokens[2] in data.QUALITIES else "普通"
        )
        if quality in data.PET_MARKET_BANNED_QUALITIES:
            return f"【{quality}】为活动/定制限定品质，无法通过宠物市场购买。"
        price = data.PET_MARKET[species]
        # 高品质加价
        mult = 1 + data.QUALITIES.index(quality) * 0.5
        cost = int(price * mult)
        if self.store.get_currency(player, "积分") < cost:
            return f"购买『{species}』（{quality}）需 {cost} 积分，积分不足。"
        self.store.add_currency(player, "积分", -cost)
        player["pet"] = petmod.new_pet(species, quality)
        return f"✅ **购买成功！**花费 {cost} 积分获得 【{quality}】品质的 **{species}**。"

    def _compose_quality_card(self, player: dict, tokens: list[str]) -> str:
        """品质卡合成：10 张低一级卡合成 1 张高一级卡。"""
        if len(tokens) < 2:
            available = "、".join(data.QUALITY_CARD_UPGRADE.keys()) or "暂无可合成卡片"
            return (
                "用法：`合成卡 目标卡名`（例如：`合成卡 圣灵卡`）\n"
                f"当前可合成：{available}\n"
                "规则：10 张低一级品质卡可合成 1 张高一级品质卡。"
            )
        target = tokens[1]
        if target not in data.QUALITY_CARD_UPGRADE:
            available = "、".join(data.QUALITY_CARD_UPGRADE.keys()) or "暂无可合成卡片"
            return f"『{target}』无法通过合成获得。当前可合成：{available}。"
        src_card, need = data.QUALITY_CARD_UPGRADE[target]
        have = player.get("bag", {}).get(src_card, 0)
        if have < need:
            return f"合成 1 张【{target}】需要 {src_card} ×{need}，你当前只有 {have} 张。"
        self.store.remove_item(player, src_card, need)
        self.store.add_item(player, target, 1)
        return (
            f"✅ **合成成功！**\n"
            f"消耗 {src_card} ×{need}，获得 **{target}** ×1。"
        )

    # =====================================================================
    # 宠物查看 / 管理
    # =====================================================================
    @staticmethod
    def _need_pet(player: dict) -> dict | None:
        return player.get("pet")

    @staticmethod
    def _busy_reason(p: dict) -> str | None:
        """宠物当前是否无法被操作（死亡 / 假死惊魂 / 心情 1 星）。可操作返回 None。"""
        if petmod.is_dead(p):
            return "宠物已死亡，请先复活（宠物复活 / 九转还魂丹）。"
        if petmod.is_frozen(p):
            return f"宠物假死/惊魂中，约 {petmod.frozen_remain_min(p)} 分钟后才能操作。"
        if p.get("mood", 5) <= 1:
            return "宠物心情低落（1颗星），无法参加活动，请先恢复心情（玩耍 / 喂食 / 使用道具）。"
        return None

    def _my_pet(self, player: dict):
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物，发送『砸蛋』或『宠物市场』获取一只吧！"
        return petmod.render_pet(p), images.pet_image_md(p.get("species"))

    def _inspect(self, group_id: str, tokens: list[str]):
        target = self._arg(tokens, 1)
        if not target:
            return "用法：宠物侦查 用户ID"
        tp, err = self._find_target(group_id, target)
        if err:
            return err
        if not tp.get("pet"):
            return "对方还没有宠物。"
        pet = tp["pet"]
        return petmod.render_pet(pet), images.pet_image_md(pet.get("species"))

    def _gift_pet(self, player: dict, group_id: str, tokens: list[str]) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物可赠送。"
        target = self._arg(tokens, 1)
        if not target:
            return "用法：赠送宠物 用户ID"
        if target == player["qq"]:
            return "不能赠送给自己。"
        tp, err = self._find_target(group_id, target)
        if err:
            return err
        if tp.get("pet"):
            return "对方已经有宠物了，无法接收。"
        tp["pet"] = p
        player["pet"] = None
        return f"🎁 已将『{p['nickname']}』赠送给 `{target}`。"

    def _release(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        player["pet"] = None
        return f"已放生『{p['nickname']}』，江湖再见。"

    def _rename(self, player: dict, tokens: list[str]) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if len(tokens) < 2:
            return "用法：宠物改名 昵称"
        name = tokens[1]
        first = p["nickname"] == p["species"]
        if not first:
            if not self.store.has_item(player, "改名卡"):
                return "改名需要『改名卡』（首次改名免费）。"
            if p["energy"] < 10:
                return "改名需要 10 点精力。"
            self.store.remove_item(player, "改名卡")
            p["energy"] -= 10
        p["nickname"] = name
        return f"改名成功！现在它叫『{name}』。"

    def _change_gender(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if not self.store.remove_item(player, "变性药水"):
            return "需要『变性药水』才能变性（可在宠物商城购买）。"
        p["gender"] = "女" if p["gender"] == "男" else "男"
        return f"变性成功！『{p['nickname']}』现在是{p['gender']}生了。"

    def _revive_self(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if not petmod.is_dead(p):
            return "宠物还活着，无需复活。"
        if not self.store.remove_item(player, "九转还魂丹"):
            return "复活需要『九转还魂丹』（可在商城购买）。"
        p["status"] = "正常"
        p["hp"] = p["hp_max"]
        p["mood"] = max(1, p["mood"])
        return f"『{p['nickname']}』已满血复活！"

    def _status_text(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        s = p["status"]
        if s == "正常":
            return f"『{p['nickname']}』当前状态：正常。"
        cure = data.STATUS_CURE_ITEM.get(s)
        tip = f"，可『喂食 {cure}』解除" if cure else ""
        if s == "死亡":
            tip = "，可使用『九转还魂丹』复活（发送：宠物复活）"
        return f"『{p['nickname']}』当前状态：{s}{tip}。"

    def _feed(self, player: dict, tokens: list[str]) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if len(tokens) < 2:
            return "用法：喂食 物品"
        name = tokens[1]
        # 相思豆喂给恋爱中的宠物
        if name == "相思豆":
            if p["love_state"] != "恋爱":
                return "只有恋爱中的宠物喂相思豆才能增加好感度。"
            if not self.store.remove_item(player, "相思豆"):
                return "你没有『相思豆』。"
            p["favor"] = min(data.FAVOR_MAX, p["favor"] + 50)
            return f"喂食相思豆，好感度 +50，当前 {p['favor']}。"
        return self._use_item(player, ["使用", name, "1"])

    # =====================================================================
    # 物品：购买 / 使用 / 出售 / 丢弃 / 转让
    # =====================================================================
    @staticmethod
    def _parse_count(tokens: list[str], idx: int) -> int:
        if idx < len(tokens) and tokens[idx].isdigit():
            return max(1, int(tokens[idx]))
        return 1

    def _buy_item(self, player: dict, tokens: list[str]) -> str:
        if len(tokens) < 2:
            return "用法：购买 物品名 [数量]"
        name = tokens[1]
        # 优先检查活动商店
        for eid, cfg in self.store.active_events().items():
            if name in cfg.get("shop", {}):
                return self._event_buy(player, eid, cfg, name) or "购买失败。"
        if name not in data.ITEMS:
            return f"商城没有『{name}』。发送『宠物商城』或『道具商城』查看。"
        it = data.ITEMS[name]
        if it["price"] <= 0:
            return f"『{name}』无法直接购买。"
        count = self._parse_count(tokens, 2)
        cost = it["price"] * count
        if self.store.get_currency(player, it["currency"]) < cost:
            return f"购买 {count} 个『{name}』需 {cost} {it['currency']}，余额不足。"
        self.store.add_currency(player, it["currency"], -cost)
        self.store.add_item(player, name, count)
        return f"购买成功：{name} x{count}，花费 {cost} {it['currency']}。"

    def _use_item(self, player: dict, tokens: list[str]) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物，无法使用物品。"
        if len(tokens) < 2:
            return "用法：使用 物品 数量"
        name = tokens[1]
        if not self.store.has_item(player, name):
            return f"背包里没有『{name}』。"
        it = data.ITEMS.get(name)
        if not it:
            it = self._event_item_def(name)
        if not it or not it.get("usable"):
            # 背包里的秘技书（如进化/渡劫脱落的秘技），使用后学习并消耗
            if name in data.SKILLS:
                if name in p.get("skills", []):
                    return "已学会该秘技。"
                s = data.SKILLS[name]
                if p["level"] < s["level_req"]:
                    return f"参悟『{name}』需要等级 Lv{s['level_req']}。"
                if p["intel"] < s["intel_req"]:
                    return f"参悟『{name}』需要智力 {s['intel_req']}。"
                p.setdefault("skills", []).append(name)
                self.store.remove_item(player, name, 1)
                petmod.refresh_energy(p)
                return f"📜 参悟成功！习得秘技『{name}』，战力 +{s['power']}。"
            return f"『{name}』不能直接使用。"
        count = self._parse_count(tokens, 2)
        if not self.store.has_item(player, name, count):
            return f"背包里『{name}』数量不足。"
        eff = it.get("effect", {})
        # 深渊净化药水：先处理再消耗
        if "clear_abyss_corruption" in eff:
            cleared = self.store.clear_abyss_corruption(
                player, eff["clear_abyss_corruption"] * count
            )
            self.store.remove_item(player, name, count)
            return (
                f"使用『{name}』x{count}：清除 {cleared} 点深渊侵蚀，"
                f"当前侵蚀 {self.store.get_abyss_corruption(player)} 点。"
            )
        # 条件性物品：条件不满足则不消耗
        if eff.get("revive") and not petmod.is_dead(p):
            return "宠物还活着，无需复活。"
        if "cure" in eff and p.get("status") != eff["cure"]:
            return f"宠物当前不是『{eff['cure']}』状态，无需使用『{name}』。"
        # 进化神石：每次只用 1 颗，且需可进化；脱落的神器/秘技放回背包
        if eff.get("force_evolve"):
            before = list(p.get("skills", [])) + (
                [p["artifact"]] if p.get("artifact") else []
            )
            ok, msg = petmod.evolve(p, force=True)
            if not ok:
                return msg
            for itx in before:
                self.store.add_item(player, itx, 1)
            self.store.remove_item(player, name, 1)
            return f"使用『{name}』：{msg}"
        # 品质提升卡：每次 1 张，史诗及以上无法使用
        if "upgrade_quality" in eff:
            target = eff["upgrade_quality"]
            ok, msg = petmod.upgrade_quality(p, target)
            if not ok:
                return msg
            self.store.remove_item(player, name, 1)
            return f"使用『{name}』：{msg}"
        msgs = []
        scaled: dict[str, Any] = {}
        for k, v in eff.items():
            if isinstance(v, bool):
                scaled[k] = v
            elif isinstance(v, (int, float)):
                scaled[k] = v * count
            else:
                scaled[k] = v
        msg = self._apply_effect(p, scaled, name)
        self.store.remove_item(player, name, count)
        petmod.refresh_energy(p)
        return f"使用『{name}』x{count}：\n{msg}"

    def _apply_effect(self, p: dict, eff: dict, name: str) -> str:
        if "heal_hp" in eff:
            p["hp"] = min(p["hp_max"], p["hp"] + eff["heal_hp"])
            return f"恢复 {eff['heal_hp']} 血量，当前 {p['hp']}/{p['hp_max']}。"
        if "heal_energy" in eff:
            p["energy"] = min(p["energy_max"], p["energy"] + eff["heal_energy"])
            return f"恢复 {eff['heal_energy']} 精力，当前 {p['energy']}/{p['energy_max']}。"
        if "add_exp" in eff:
            petmod.add_exp(p, eff["add_exp"])
            return f"经验 +{eff['add_exp']}，当前 {p['exp']}。"
        if "add_hp_max" in eff:
            p["hp_max"] += eff["add_hp_max"]
            p["hp"] = p["hp_max"]
            return f"血量上限 +{eff['add_hp_max']} 并回满，当前上限 {p['hp_max']}。"
        if "add_energy_max" in eff:
            p["energy_max"] = p.get("energy_max", 100) + eff["add_energy_max"]
            p["energy"] = p["energy_max"]
            return f"精力上限 +{eff['add_energy_max']} 并回满，当前上限 {p['energy_max']}。"
        if "add_atk" in eff:
            p["atk"] += eff["add_atk"]
            return f"攻击 +{eff['add_atk']}，当前攻击 {p['atk']}。"
        if "add_def" in eff:
            p["def"] += eff["add_def"]
            return f"防御 +{eff['add_def']}，当前防御 {p['def']}。"
        if "add_intel" in eff:
            p["intel"] += eff["add_intel"]
            return f"智力 +{eff['add_intel']}，当前智力 {p['intel']}。"
        if "mood" in eff:
            p["mood"] = max(1, min(5, eff["mood"]))
            return f"心情已恢复到 {p['mood']} 颗星！"
        if "cure" in eff:
            if p["status"] == eff["cure"]:
                p["status"] = "正常"
                return f"已解除『{eff['cure']}』状态。"
            return f"宠物当前不是『{eff['cure']}』状态，无效果。"
        if eff.get("revive"):
            p["status"] = "正常"
            p["hp"] = p["hp_max"]
            return "复活成功并回满血量！"
        return f"使用了『{name}』。"

    def _sell_item(self, player: dict, tokens: list[str]) -> str:
        if len(tokens) < 2:
            return "用法：出售 物品 数量"
        name = tokens[1]
        count = self._parse_count(tokens, 2)
        if not self.store.has_item(player, name, count):
            return f"背包里『{name}』数量不足。"
        it = data.ITEMS.get(name, {"price": 100, "currency": "积分"})
        gain = int(it["price"] * 0.5) * count
        self.store.remove_item(player, name, count)
        self.store.add_currency(player, it["currency"], gain)
        return f"出售 {name} x{count}，获得 {gain} {it['currency']}。"

    def _drop_item(self, player: dict, tokens: list[str]) -> str:
        if len(tokens) < 2:
            return "用法：丢弃 物品 数量"
        name = tokens[1]
        count = self._parse_count(tokens, 2)
        if not self.store.remove_item(player, name, count):
            return f"背包里『{name}』数量不足。"
        return f"已丢弃 {name} x{count}。"

    def _transfer_item(self, player: dict, group_id: str, tokens: list[str]) -> str:
        # 转让 用户ID 物品 数量
        target = self._arg(tokens, 1)
        if not target or len(tokens) < 3:
            return "用法：转让 用户ID 物品 数量"
        tp, err = self._find_target(group_id, target)
        if err:
            return err
        name = tokens[2]
        count = self._parse_count(tokens, 3)
        if not self.store.has_item(player, name, count):
            return f"背包里『{name}』数量不足。"
        self.store.remove_item(player, name, count)
        self.store.add_item(tp, name, count)
        return f"📦 已转让 {name} ×{count} 给 `{target}`。"

    def _gift_currency(
        self, player: dict, group_id: str, cmd: str, tokens: list[str]
    ) -> str:
        # 赠送金币 用户ID 数量
        currency = cmd.replace("赠送", "")
        if len(tokens) < 3:
            return f"用法：{cmd} 用户ID 数量"
        target = self._arg(tokens, 1)
        if not target:
            return f"用法：{cmd} 用户ID 数量"
        if not tokens[2].isdigit():
            return "数量必须为正整数。"
        count = max(1, int(tokens[2]))
        if str(target) == str(player.get("qq", "")):
            return "不能赠送给自己。"
        tp, err = self._find_target(group_id, target)
        if err:
            return err
        have = self.store.get_currency(player, currency)
        if have < count:
            return f"你的{currency}不足（需要 {count}，当前 {have}）。"
        self.store.add_currency(player, currency, -count)
        self.store.add_currency(tp, currency, count)
        return f"💰 已向 `{target}` 赠送 {currency} ×{count}。"

    def _bag_text(self, player: dict) -> str:
        bag = player.get("bag", {})
        head = "## 💼 我的背包"
        if not bag:
            return head + "\n> （空空如也，去商城逆选购吧）"
        lines = [head, "━━━━━━━━━━━━━━"]
        for n, c in bag.items():
            lines.append(f"• **{n}** ×{c}")
        return "\n".join(lines)

    # =====================================================================
    # 日常活动
    # =====================================================================
    def _daily(self, player: dict, group_id: str, action: str) -> str:
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物，发送『砸蛋』获取一只。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        petmod.refresh_energy(p)
        conf = data.DAILY_ACTIONS[action]
        if p["energy"] < conf["energy"]:
            return f"精力不足（需 {conf['energy']}，当前 {p['energy']}）。"
        if action == "冥想" and not p.get("custom"):
            return "『冥想』需要定制宠物才行。"
        if action == "双修" and p.get("love_state") != "已婚":
            return "『双修』需与伴侣结为夫妻才行，先通过『宠物求婚 / 同意求婚』结婚吧（单身/恋爱中可用『修炼』）。"
        cd = self._cooldown_block(player, f"日常:{action}", action)
        if cd:
            return cd
        p["energy"] -= conf["energy"]
        self.store.set_cooldown(
            player, f"日常:{action}", random.randint(*data.DAILY_COOLDOWN_RANGE)
        )

        if action == "约会":
            gain = 5 + p["level"] // 5
            if p["love_state"] == "已婚":
                gain *= 2
            p["favor"] = min(data.FAVOR_MAX, p["favor"] + gain)
            extra = ""
            if p.get("love_target") and p["love_state"] in ("恋爱", "已婚"):
                tp = self.store.get_player(p["love_target"], group_id, create=False)
                if tp and tp.get("pet"):
                    tp["pet"]["favor"] = min(
                        data.FAVOR_MAX, tp["pet"]["favor"] + gain
                    )
                    extra = f"\n💕 伴侣 `{p['love_target']}` 的好感度也 +{gain}。"
            return f"💕 约会愉快，好感度 +{gain}，当前 {p['favor']}。" + extra
        if action in ("修炼", "双修"):
            base = random.randint(80, 200) + p["level"] * 25
            exp = base * (2 if action == "双修" else 1)
            petmod.add_exp(p, exp)
            return (
                f"🧘 {action}完成，经验 +{exp}，当前经验 {p['exp']}。"
                + self._auto_level_note(player, p)
            )
        if action == "打工":
            gain = random.randint(200, 600) + p["level"] * 5
            self.store.add_currency(player, "积分", gain)
            return f"💰 打工辛苦了，积分 +{gain}，当前 {player['jifen']}。"
        if action == "闭关":
            p["hp"] = p["hp_max"]
            return "🛡 闭关完成，血量已回满。"
        if action == "学习":
            gain = random.randint(3, 10)
            p["intel"] += gain
            return f"📖 学习完成，智力 +{gain}，当前 {p['intel']}。"
        if action == "玩耍":
            p["mood"] = 5
            return "🎈 玩耍开心，心情恢复到 ★★★★★。"
        if action == "洗髓":
            if p["intel"] <= 20:
                return "智力过低，无法洗髓。"
            conv = random.randint(10, max(11, p["intel"] // 3))
            p["intel"] -= conv
            if random.random() < 0.5:
                p["atk"] += conv * 2
                return f"🌀 洗髓：智力 -{conv}，攻击 +{conv * 2}。"
            p["def"] += conv * 2
            return f"🌀 洗髓：智力 -{conv}，防御 +{conv * 2}。"
        if action == "探险":
            return self._explore(player, p)
        if action == "冥想":
            attr = random.choice(["atk", "def", "intel", "hp_max"])
            gain = random.randint(50, 200)
            p[attr] += gain
            label = {
                "atk": "攻击",
                "def": "防御",
                "intel": "智力",
                "hp_max": "生命上限",
            }[attr]
            return f"🧠 冥想：永久{label} +{gain}。"
        return "完成。"

    def _explore(self, player: dict, p: dict) -> str:
        player.setdefault("stats", {})["explore"] = (
            player["stats"].get("explore", 0) + 1
        )
        lucky = p.get("talent") == "鸿运当头"
        roll = random.random()
        if not lucky and roll < 0.25:
            return "🌫 探险归来，这次什么也没找到……"
        kind = random.choices(
            ["积分", "经验", "道具", "材料", "图纸", "神器", "秘技"],
            weights=[30, 25, 18, 10, 7, 5, 5],
            k=1,
        )[0]
        if kind == "积分":
            g = random.randint(500, 3000)
            self.store.add_currency(player, "积分", g)
            return f"🧭 探险发现宝箱，积分 +{g}！"
        if kind == "经验":
            g = random.randint(500, 4000)
            petmod.add_exp(p, g)
            return f"🧭 探险顿悟，经验 +{g}！"
        if kind == "道具":
            item = random.choice(["红药水", "蓝药水", "三明治", "相思豆", "万能宝石"])
            self.store.add_item(player, item, 1)
            return f"🧭 探险拾得『{item}』x1！"
        if kind == "材料":
            self.store.add_item(player, "万能宝石", 1)
            return "🧭 探险采得材料『万能宝石』x1！"
        if kind == "图纸":
            self.store.add_item(player, "进化神石", 1)
            return "🧭 探险获得机缘『进化神石』x1！"
        if kind == "神器":
            art = random.choice(data.ARTIFACT_NAMES)
            self.store.add_item(player, "神器图纸", 1)
            self.store.add_item(player, "万能宝石", 2)
            return (
                f"🧭 探险偶遇神器图纸（{art}），获得『神器图纸』x1 和『万能宝石』x2，"
                f"可用于『打造神器 {art}』！"
            )
        if kind == "秘技":
            skill = random.choice(data.SKILL_NAMES)
            self.store.add_item(player, skill, 1)
            return (
                f"🧭 探险参悟到秘技线索（{skill}），获得『{skill}』x1，"
                f"可『使用 {skill}』或发送『参悟秘技 {skill}』学习！"
            )
        return "🧭 探险归来。"

    # =====================================================================
    # 成长
    # =====================================================================
    def _auto_level(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        petmod.refresh_energy(p)
        before = p.get("level", 1)
        n = petmod.auto_level_up(p)
        if n == 0:
            return f"未能升级（经验或精力不足）。当前 Lv{p['level']}/{petmod.level_cap(p)}。"
        reward = self._grant_level60_reward(player, p, before)
        return (
            f"⬆ 一键升级 +{n} 级！当前 Lv{p['level']}/{petmod.level_cap(p)}，剩余精力 {p['energy']}。"
            + reward
        )

    def _manual_level(self, player: dict, tokens: list[str]) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        times = self._parse_count(tokens, 1)
        petmod.refresh_energy(p)
        before = p.get("level", 1)
        n, note = petmod.level_up(p, times)
        if n == 0:
            return f"升级失败：{note}"
        suffix = f"（{note}）" if note else ""
        reward = self._grant_level60_reward(player, p, before)
        return (
            f"⬆ 升级 +{n} 级！当前 Lv{p['level']}/{petmod.level_cap(p)}{suffix}。"
            + reward
        )

    def _evolve(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        before = list(p.get("skills", [])) + (
            [p["artifact"]] if p.get("artifact") else []
        )
        ok, msg = petmod.evolve(p)
        if ok:
            for it in before:
                self.store.add_item(player, it, 1)
        return msg

    def _ascend(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        ok, msg = petmod.ascend(p)
        return msg

    def _tribulation(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        ok, msg = petmod.tribulation(p)
        return msg

    def _fantasy_treasure(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        if data.STAGES.index(p["stage"]) < data.STAGES.index("飞升"):
            return "宠物飞升后才能『幻境寻宝』。"
        if p["energy"] < data.ASCEND_TREASURE["energy"]:
            return f"精力不足（需 {data.ASCEND_TREASURE['energy']}）。"
        p["energy"] -= data.ASCEND_TREASURE["energy"]
        j = random.randint(*data.ASCEND_TREASURE["jifen"])
        e = random.randint(*data.ASCEND_TREASURE["exp"])
        self.store.add_currency(player, "积分", j)
        petmod.add_exp(p, e)
        return f"✨ 幻境寻宝：积分 +{j}，经验 +{e}！"

    def _immortal_calamity(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        if data.STAGES.index(p["stage"]) < data.STAGES.index("飞升"):
            return "宠物飞升后才能挑战神仙劫。"
        if p["energy"] < 50:
            return "精力不足（需 50）。"
        p["energy"] -= 50
        if random.random() < 0.5:
            e = random.randint(50000, 200000)
            petmod.add_exp(p, e)
            return f"⚡ 神仙劫渡过，经验 +{e}！"
        p["hp"] = max(1, p["hp"] // 2)
        return "⚡ 神仙劫失败，宠物身受重伤，恢复后再来。"

    # =====================================================================
    # 神器 / 秘技
    # =====================================================================
    def _forge_artifact(self, player: dict, tokens: list[str]) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if len(tokens) < 2:
            return "用法：打造神器 神器名称（可用『神器』查看）"
        name = tokens[1]
        if name not in data.ARTIFACTS:
            return f"没有名为『{name}』的神器。"
        cost = data.ARTIFACT_FORGE_COST
        if self.store.get_currency(player, "积分") < cost["jifen"]:
            return f"打造『{name}』需 {cost['jifen']} 积分。"
        if not self.store.has_item(player, cost["material"], cost["material_count"]):
            return f"打造需要材料『{cost['material']}』x{cost['material_count']}。"
        if not self.store.has_item(
            player, cost.get("blueprint", "神器图纸"), cost.get("blueprint_count", 1)
        ):
            return (
                f"打造需要『{cost.get('blueprint', '神器图纸')}』"
                f"x{cost.get('blueprint_count', 1)}。"
            )
        self.store.add_currency(player, "积分", -cost["jifen"])
        self.store.remove_item(player, cost["material"], cost["material_count"])
        self.store.remove_item(
            player, cost.get("blueprint", "神器图纸"), cost.get("blueprint_count", 1)
        )
        self.store.add_item(player, name, 1)
        return f"⚒ 打造成功！『{name}』已放入背包，可『佩戴神器 {name}』。"

    def _equip_artifact(self, player: dict, tokens: list[str]) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if len(tokens) < 2:
            return "用法：佩戴神器 神器名称"
        name = tokens[1]
        if name not in data.ARTIFACTS:
            return f"没有名为『{name}』的神器。"
        if not self.store.has_item(player, name):
            return f"背包里没有『{name}』，请先打造或获取。"
        req = data.ARTIFACTS[name]["level_req"]
        if p["level"] < req and data.STAGES.index(p["stage"]) < data.STAGES.index(
            "飞升"
        ):
            return f"佩戴『{name}』需要等级达到 Lv{req}。"
        if p.get("artifact"):
            self.store.add_item(player, p["artifact"], 1)
        self.store.remove_item(player, name)
        p["artifact"] = name
        return f"🗡 已佩戴神器『{name}』，战力 +{data.ARTIFACTS[name]['power']}！"

    def _unequip_artifact(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if not p.get("artifact"):
            return "当前未佩戴神器。"
        name = p["artifact"]
        self.store.add_item(player, name, 1)
        p["artifact"] = None
        return f"已卸下『{name}』并放回背包。"

    def _learn_skill(self, player: dict, tokens: list[str]) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        if len(tokens) < 2:
            return "用法：参悟秘技 秘技名称（可用『秘技』查看）"
        name = tokens[1]
        if name not in data.SKILLS:
            return f"没有名为『{name}』的秘技。"
        if name in p.get("skills", []):
            return "已学会该秘技。"
        if not self.store.has_item(player, name, 1):
            return f"背包里没有秘技书『{name}』，请通过探险等途径获取。"
        s = data.SKILLS[name]
        if p["level"] < s["level_req"]:
            return f"参悟『{name}』需要等级 Lv{s['level_req']}。"
        if p["intel"] < s["intel_req"]:
            return f"参悟『{name}』需要智力 {s['intel_req']}（当前 {p['intel']}）。"
        self.store.remove_item(player, name, 1)
        p.setdefault("skills", []).append(name)
        return f"📜 参悟成功！习得秘技『{name}』，战力 +{s['power']}。"

    def _forget_skill(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if not p.get("skills"):
            return "宠物没有秘技可遗忘。"
        forgotten = p["skills"]
        p["skills"] = []
        return f"已遗忘秘技：{'、'.join(forgotten)}。"

    # =====================================================================
    # 天赋 / 炼丹
    # =====================================================================
    def _awaken(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        c = data.AWAKEN_COST
        if data.STAGES.index(p["stage"]) < data.STAGES.index(c["stage"]):
            return f"觉醒要求宠物达到【{c['stage']}】阶段。"
        if p["level"] < c["level"]:
            return f"觉醒要求等级 Lv{c['level']}。"
        if p["exp"] < c["exp"]:
            return f"觉醒要求 {c['exp']} 经验（当前 {p['exp']}）。"
        if self.store.get_currency(player, "积分") < c["jifen"]:
            return f"觉醒要求 {c['jifen']} 积分。"
        if p["energy"] < c["energy"]:
            return f"觉醒要求 {c['energy']} 点精力。"
        p["exp"] -= c["exp"]
        self.store.add_currency(player, "积分", -c["jifen"])
        p["energy"] -= c["energy"]
        # 可觉醒天赋（非定制宠物不能觉醒"需定制"的天赋）
        pool = [
            n
            for n, v in data.TALENTS.items()
            if p.get("custom") or not v["need_custom"]
        ]
        old = p.get("talent")
        p["talent"] = random.choice(pool)
        cover = f"（覆盖原天赋 {old}）" if old else ""
        return f"🌟 觉醒成功！获得天赋【{p['talent']}】{cover}\n{data.TALENTS[p['talent']]['desc']}"

    def _make_rune(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        if not p.get("talent"):
            return "宠物还没有觉醒天赋，无法制符。"
        c = data.TALENT_RUNE_MAKE_COST
        if (
            self.store.get_currency(player, "积分") < c["jifen"]
            or p["exp"] < c["exp"]
            or p["energy"] < c["energy"]
        ):
            return f"制符需要 {c['jifen']} 积分、{c['exp']} 经验、{c['energy']} 精力。"
        self.store.add_currency(player, "积分", -c["jifen"])
        p["exp"] -= c["exp"]
        p["energy"] -= c["energy"]
        rune = f"{p['talent']}符"
        self.store.add_item(player, rune, 1)
        return f"🪬 制符成功！获得『{rune}』，可『使用天赋符 {p['talent']}』赋予其它宠物该天赋。"

    def _use_rune(self, player: dict, tokens: list[str]) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        if len(tokens) < 2:
            return "用法：使用天赋符 名称（例如：使用天赋符 绝影丹心）"
        talent = tokens[1].rstrip("符")
        rune = f"{talent}符"
        if not self.store.has_item(player, rune):
            return f"背包里没有『{rune}』。"
        if talent not in data.TALENTS:
            return f"未知天赋『{talent}』。"
        c = data.TALENT_RUNE_USE_COST
        if (
            self.store.get_currency(player, "积分") < c["jifen"]
            or p["exp"] < c["exp"]
            or p["energy"] < c["energy"]
        ):
            return f"使用天赋符需要 {c['jifen']} 积分、{c['exp']} 经验、{c['energy']} 精力。"
        self.store.add_currency(player, "积分", -c["jifen"])
        p["exp"] -= c["exp"]
        p["energy"] -= c["energy"]
        self.store.remove_item(player, rune)
        old = p.get("talent")
        p["talent"] = talent
        cover = f"（覆盖原天赋 {old}）" if old else ""
        return f"🪬 使用天赋符成功，宠物获得天赋【{talent}】{cover}。"

    def _refine_elixir(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        if p.get("talent") != "绝影丹心":
            return "需要觉醒『绝影丹心』天赋的宠物才能炼丹。"
        c = data.ELIXIR_CRAFT_COST
        if (
            p["exp"] < c["exp"]
            or self.store.get_currency(player, "积分") < c["jifen"]
            or p["energy"] < c["energy"]
        ):
            return f"炼丹需要 {c['exp']} 经验、{c['jifen']} 积分、{c['energy']} 精力。"
        p["exp"] -= c["exp"]
        self.store.add_currency(player, "积分", -c["jifen"])
        p["energy"] -= c["energy"]
        elixir = random.choice(data.ELIXIR_NAMES)
        self.store.add_item(player, elixir, 1)
        return f"⚗ 炼丹成功，提炼出『{elixir}』x1！\n{data.ELIXIRS[elixir]['desc']}"

    def _use_elixir(self, player: dict, group_id: str, tokens: list[str]) -> str:
        # 使用仙丹 仙丹名 用户ID 数量
        if len(tokens) < 3:
            return "用法：使用仙丹 仙丹名 用户ID [数量]"
        name = tokens[1]
        if name not in data.ELIXIRS:
            return f"没有名为『{name}』的仙丹。"
        target = self._arg(tokens, 2)
        count = self._parse_count(tokens, 3)
        if not self.store.has_item(player, name, count):
            return f"背包里『{name}』数量不足。"
        tp, err = self._find_target(group_id, target)
        if err:
            return err
        if not tp.get("pet"):
            return "目标没有宠物。"
        tpet = tp["pet"]
        if tpet.get("talent") == "不死之体" and data.ELIXIRS[name]["effect"].get(
            "kill"
        ):
            return "对方宠物拥有『不死之体』，死亡丹无效。"
        msg = ""
        for _ in range(count):
            msg = self._apply_elixir(tpet, name)
        self.store.remove_item(player, name, count)
        return f"对 `{target}` 的宠物使用『{name}』×{count}：{msg}"

    def _apply_elixir(self, p: dict, name: str) -> str:
        eff = data.ELIXIRS[name]["effect"]
        if "freeze_hours" in eff:
            p["frozen_until"] = int(time.time()) + eff["freeze_hours"] * 3600
            return f"进入假死/惊魂，{eff['freeze_hours']} 小时内无法操作。"
        if eff.get("cure_all"):
            p["status"] = "正常"
            p["frozen_until"] = 0
            return "解除了一切限制与异常。"
        if eff.get("kill"):
            p["status"] = "死亡"
            p["hp"] = 0
            p["mood"] = 1
            return "宠物立即死亡。"
        if eff.get("forget_skill"):
            p["skills"] = []
            return "遗忘了全部秘技。"
        for k, label in (
            ("atk", "攻击"),
            ("def", "防御"),
            ("hp_max", "生命上限"),
            ("exp", "经验"),
        ):
            if k in eff:
                p[k] = max(0, p.get(k, 0) + eff[k])
                if k == "hp_max":
                    p["hp"] = min(p["hp"], p["hp_max"])
                sign = "+" if eff[k] >= 0 else ""
                return f"{label} {sign}{eff[k]}。"
        return "已使用。"

    # =====================================================================
    # 天赋触发指令：治愈 / 复活 / 精力转移
    # =====================================================================
    def _talent_heal(self, player: dict, group_id: str, tokens: list[str]) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if p.get("talent") != "妙手回春":
            return "需要觉醒『妙手回春』天赋才能治愈他人宠物。"
        target = self._arg(tokens, 1)
        if not target:
            return "用法：治愈 用户ID"
        tp, err = self._find_target(group_id, target)
        if err:
            return err
        if not tp.get("pet"):
            return "目标没有宠物。"
        tp["pet"]["hp"] = tp["pet"]["hp_max"]
        return f"🌿 已治愈 `{target}` 的宠物，血量回满。"

    def _talent_revive(self, player: dict, group_id: str, tokens: list[str]) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if p.get("talent") != "起死回生":
            return "需要觉醒『起死回生』天赋才能复活他人宠物。"
        target = self._arg(tokens, 1)
        if not target:
            return "用法：复活 用户ID"
        tp, err = self._find_target(group_id, target)
        if err:
            return err
        if not tp.get("pet"):
            return "目标没有宠物。"
        tpet = tp["pet"]
        tpet["status"] = "正常"
        tpet["hp"] = tpet["hp_max"]
        return f"💫 已复活 `{target}` 的宠物。"

    def _energy_transfer(
        self, player: dict, group_id: str, tokens: list[str]
    ) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if p.get("talent") != "精力转移":
            return "需要觉醒『精力转移』天赋（需定制宠物）才能转移精力。"
        target = self._arg(tokens, 1)
        amount = self._parse_count(tokens, 2)
        if not target or len(tokens) < 3:
            return "用法：精力转移 用户ID 精力值"
        if p["energy"] < amount:
            return "自身精力不足。"
        tp, err = self._find_target(group_id, target)
        if err:
            return err
        if not tp.get("pet"):
            return "目标没有宠物。"
        p["energy"] -= amount
        tpet = tp["pet"]
        tpet["energy"] = min(tpet["energy_max"], tpet["energy"] + amount)
        return f"🔋 已向 `{target}` 的宠物转移 {amount} 点精力。"

    # =====================================================================
    # 对战 / 排行
    # =====================================================================
    def _battle(
        self, attacker: dict, defender: dict, ap_player: dict, dp_player: dict
    ) -> str:
        ap = petmod.effective_power_vs(attacker, defender)
        dp = petmod.effective_power_vs(defender, attacker)
        # 神隐遁术：被高战力攻击有概率逃脱
        if defender.get("talent") == "神隐遁术" and ap > dp and random.random() < 0.5:
            return f"对方宠物『{defender['nickname']}』触发【神隐遁术】，逃之夭夭！"
        # 蝶逆轮回：满血弱者秒杀强者
        if (
            attacker.get("talent") == "蝶逆轮回"
            and attacker["hp"] >= attacker["hp_max"]
            and ap < dp
            and random.random() < 0.3
        ):
            attacker["hp"] = 1
            return self._battle_win(
                attacker, defender, ap_player, dp_player, flawless=True
            )
        if ap >= dp:
            return self._battle_win(attacker, defender, ap_player, dp_player)
        # 攻击方失败：扣血，可能因此死亡；『不死之体』则至少保留 1 点血
        loss = attacker["hp_max"] // 3
        if attacker.get("talent") == "不死之体":
            attacker["hp"] = max(1, attacker["hp"] - loss)
            dead_txt = f"，HP -{loss}"
        else:
            attacker["hp"] = max(0, attacker["hp"] - loss)
            if attacker["hp"] <= 0:
                attacker["status"] = "死亡"
                attacker["mood"] = max(1, attacker.get("mood", 5) - 1)
                dead_txt = (
                    f"，HP -{loss}，你的宠物力竭身亡！"
                    f"心情降至 {attacker['mood']} 颗星。"
                )
            else:
                dead_txt = f"，HP -{loss}，受了点伤。"
        return (
            f"⚔ 战斗失败！你的『{attacker['nickname']}』(战力{ap}) 不敌 "
            f"『{defender['nickname']}』(战力{dp}){dead_txt}"
        )

    def _battle_win(
        self, attacker, defender, ap_player, dp_player, flawless=False
    ) -> str:
        # 不死之体
        killed = False
        if defender.get("talent") != "不死之体":
            defender["hp"] = max(0, defender["hp"] - defender["hp_max"] // 2)
            if defender["hp"] <= 0:
                defender["status"] = "死亡"
                defender["mood"] = max(1, defender.get("mood", 5) - 1)
                killed = True
        exp = random.randint(300, 1500) + attacker["level"] * 5
        # 七星化海：额外经验
        if attacker.get("talent") == "七星化海":
            exp = int(exp * (1 + random.uniform(0.1, 0.3)))
        petmod.add_exp(attacker, exp)
        ap_player.setdefault("stats", {})["battle_win"] = (
            ap_player["stats"].get("battle_win", 0) + 1
        )
        steal = ""
        # 妙手摘星：击杀偷物
        if (
            killed
            and attacker.get("talent") == "妙手摘星"
            and dp_player.get("bag")
            and random.random() < 0.4
        ):
            item = random.choice(list(dp_player["bag"].keys()))
            self.store.remove_item(dp_player, item, 1)
            self.store.add_item(ap_player, item, 1)
            steal = f"，并偷得对方『{item}』x1"
        head = "💥 触发【蝶逆轮回】，一滴血秒杀对手！\n" if flawless else "⚔ 战斗胜利！"
        kill_txt = "（对方宠物已死亡）" if killed else ""
        return f"{head}经验 +{exp}{steal}{kill_txt}。"

    def _attack(self, player: dict, group_id: str, tokens: list[str]) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if petmod.is_dead(p):
            return "你的宠物已死亡，请先复活。"
        if petmod.is_frozen(p):
            return f"宠物假死/惊魂中，约 {petmod.frozen_remain_min(p)} 分钟后才能战斗。"
        target = self._arg(tokens, 1)
        if not target:
            return "用法：宠物攻击 用户ID"
        if target == player["qq"]:
            return "不能攻击自己。"
        tp, err = self._find_target(group_id, target)
        if err:
            return err
        if not tp.get("pet"):
            return "对方没有宠物。"
        tpet = tp["pet"]
        if petmod.is_dead(tpet):
            return "对方宠物已死亡。"
        petmod.refresh_energy(p)
        if p["energy"] < self.attack_energy:
            return f"发起攻击需要 {self.attack_energy} 点精力（当前 {p['energy']}）。"
        p["energy"] -= self.attack_energy
        return self._battle(p, tpet, player, tp)

    def _cross_attack(self, player: dict, group: dict, tokens: list[str]) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if not group.get("cross", True):
            return "⚠️ 本群未开启宠物跨群功能。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        # 跨群挑战宠物 [群号 用户ID]，或随机
        target_player = None
        if len(tokens) >= 3:
            target_player = self.store.get_player(
                tokens[2], tokens[1], create=False
            )
            if not target_player:
                return f"❌ 群 `{tokens[1]}` 内用户 `{tokens[2]}` 不存在。"
        if not target_player:
            self_key = self.store.make_key(player.get("group", ""), player["qq"])
            candidates = [
                pl
                for k, pl in self.store.all_players().items()
                if pl.get("pet") and k != self_key and not petmod.is_dead(pl["pet"])
            ]
            if not candidates:
                return "暂时找不到可挑战的跨群宠物。"
            target_player = random.choice(candidates)
        if not target_player or not target_player.get("pet"):
            return "目标没有宠物。"
        petmod.refresh_energy(p)
        if p["energy"] < self.attack_energy:
            return f"发起挑战需要 {self.attack_energy} 点精力（当前 {p['energy']}）。"
        p["energy"] -= self.attack_energy
        return self._battle(p, target_player["pet"], player, target_player)

    @staticmethod
    def _fmt_power(bp: int) -> str:
        """战力显示：≥1万用『X.XX万』，否则原值。"""
        if bp >= 10000:
            return f"{bp / 10000:.2f}万"
        return str(bp)

    def _rank(self, player: dict, group_id: str, local: bool) -> str:
        # 本群排行只统计本群玩家；神榜为全服（跨群）。
        source = (
            self.store.players_in_group(group_id)
            if local
            else self.store.all_players()
        )
        entries = []
        for pl in source.values():
            pet = pl.get("pet")
            if not pet:
                continue
            entries.append((pl.get("qq", "?"), pet, petmod.battle_power(pet)))
        entries.sort(key=lambda x: x[2], reverse=True)
        if not entries:
            return "暂无宠物上榜。"
        title = "## 🏆 宠物排行（本群）" if local else "## 🏅 宠物神榜（全服）"
        lines = [title]
        # 我的排名/战力：按全量排序算真实名次，即使未进前 N 也显示。
        my_pet = player.get("pet")
        if my_pet:
            my_bp = petmod.battle_power(my_pet)
            my_rank = 1 + sum(1 for _, _, bp in entries if bp > my_bp)
            lines.append(
                f"> 我的排名：**{my_rank}**　·　我的战力：**{self._fmt_power(my_bp)}**"
            )
        top = entries[: self.rank_size]
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines.append("")
        lines.append("| 排名 | 昵称 | 等级 | 阶段 | 级别 | 战力 |")
        lines.append("|:--:|:--:|:--:|:--:|:--:|--:|")
        for i, (q, pet, bp) in enumerate(top, 1):
            rk = medals.get(i, str(i))
            # 昵称里若含 | 会破坏表格列，替换为视觉相近的全角竖线。
            nick = str(pet.get("nickname", "")).replace("|", "丨")
            lines.append(
                f"| {rk} | {nick} | Lv{pet['level']} | "
                f"{pet['stage']} | {pet['quality']} | {self._fmt_power(bp)} |"
            )
        if not local:
            lines.append(
                f"\n> 🎁 神榜前三每日可『领取神榜奖励』，随机钻石 💠 "
                f"{self.rank_reward_diamond_min}~{self.rank_reward_diamond_max}。"
            )
        return "\n".join(lines)

    def _claim_rank_reward(self, player: dict, group_id: str) -> str:
        # 神榜为全服跨群排行，以「群ID+用户ID」为唯一身份。
        entries = []
        for k, pl in self.store.all_players().items():
            if pl.get("pet"):
                entries.append((k, petmod.battle_power(pl["pet"])))
        entries.sort(key=lambda x: x[1], reverse=True)
        top = [k for k, _ in entries[:3]]
        self_key = self.store.make_key(player.get("group", group_id), player["qq"])
        if self_key not in top:
            return "只有神榜前三名可领取奖励。"
        today = time.strftime("%Y-%m-%d")
        if player.get("rank_reward_day") == today:
            return "今天已领取过神榜奖励。"
        player["rank_reward_day"] = today
        reward = random.randint(
            self.rank_reward_diamond_min, self.rank_reward_diamond_max
        )
        self.store.add_currency(player, "钻石", reward)
        return f"🎁 神榜强者奖励到账，钻石 💠 +{reward}！"

    # =====================================================================
    # 副本 / 剧情任务
    # =====================================================================
    def _dungeon_list(self) -> str:
        lines = [
            "## 🏰 宠物副本",
            "> 进入方式：`进入副本 副本名称`（冷却 15 分钟）",
            "",
        ]
        for n, d in data.DUNGEONS.items():
            lines.append(
                f"- **{n}** `Lv{d['level_req']}`　🗡{d['monster']}（战力 {d['power']}）\n"
                f"　　耗 {d['energy']} 精力 · 产出 经验约 {d['exp']}（±20%） / 积分约 {d['jifen']}（±20%）"
            )
        lines.append("\n> 战力 ≥ 怪物战力即可通关；经验满后自动升级。")
        return "\n".join(lines)

    def _auto_level_note(self, player: dict, p: dict) -> str:
        """经验满则自动一键升级，返回提示文本（无升级则空串）。"""
        if not petmod.exp_enough_to_level(p):
            return ""
        before = p.get("level", 1)
        gained = petmod.auto_level_up(p)
        if gained <= 0:
            return ""
        return (
            f"\n⬆ **自动升级 +{gained} 级！**当前 "
            f"Lv{p['level']}/{petmod.level_cap(p)}（剩余精力 {p['energy']}）"
        ) + self._grant_level60_reward(player, p, before)

    def _grant_level60_reward(self, player: dict, p: dict, before_level: int) -> str:
        """宠物本次升级若跨过 60 级倍数，赠送 1 张『史诗卡』放入背包。返回提示文本。"""
        after_level = p.get("level", 1)
        if before_level // 60 < after_level // 60:
            self.store.add_item(player, "史诗卡", 1)
            return "\n🎁 宠物等级突破 **60 级**，获得 **史诗卡** ×1（背包查看，可使用提升品质至史诗）！"
        return ""

    def _enter_dungeon(self, player: dict, tokens: list[str]) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        if len(tokens) < 2:
            return "用法：进入副本 副本名称"
        name = tokens[1]
        if name not in data.DUNGEONS:
            return f"没有名为『{name}』的副本。"
        d = data.DUNGEONS[name]
        if p["level"] < d["level_req"]:
            return f"进入『{name}』需要等级 Lv{d['level_req']}。"
        cd = self._cooldown_block(player, "副本", "进入副本")
        if cd:
            return cd
        petmod.refresh_energy(p)
        if p["energy"] < d["energy"]:
            return f"精力不足（需 {d['energy']}）。"
        p["energy"] -= d["energy"]
        self.store.set_cooldown(player, "副本", data.DUNGEON_COOLDOWN)
        return self._dungeon_battle(player, p, name, d)

    def _dungeon_battle(self, player: dict, p: dict, name: str, d: dict) -> str:
        monster = d["monster"]
        power = d["power"]
        my_power = petmod.battle_power(p)
        # 战力 ±10% 浮动后比拼怪物战力
        roll = int(my_power * random.uniform(0.9, 1.1))
        win = roll >= power
        minutes = random.randint(0, 5)
        next_time = time.strftime(
            "%Y/%m/%d %H:%M:%S",
            time.localtime(time.time() + data.DUNGEON_COOLDOWN),
        )
        nick = p["nickname"]
        head = f"## ⚔ {nick} VS {monster}"
        if win:
            # 实际获得数值在配置值基础 ±20% 浮动，避免每次完全相同
            exp_gain = max(1, int(d["exp"] * random.uniform(0.8, 1.2)))
            jifen_gain = max(1, int(d["jifen"] * random.uniform(0.8, 1.2)))
            petmod.add_exp(p, exp_gain)
            self.store.add_currency(player, "积分", jifen_gain)
            drop = ""
            if random.random() < 0.2:
                self.store.add_item(player, "万能宝石", 1)
                drop = "\n●掉落道具：万能宝石 ×1"
            desc = f"您的{nick}在{name}遇见{monster}，激战{monster}结果**大胜**！"
            body = (
                "┏-★---副☆本---★-┓\n"
                f"●本次耗时：{minutes}分钟\n"
                f"●怪物战力：{power}\n"
                f"●获得经验：{exp_gain}\n"
                f"●获得积分：{jifen_gain}{drop}\n"
                f"●下次时间：{next_time}\n"
                "┗-★---信☆息---★-┛"
            )
            return f"{head}\n{desc}\n{body}{self._auto_level_note(player, p)}"
        desc = f"您的{nick}在{name}遇见{monster}，力战{monster}结果**惨败**！"
        body = (
            "┏-★---副☆本---★-┓\n"
            f"●本次耗时：{minutes}分钟\n"
            f"●怪物战力：{power}\n"
            "●战败没有经验奖励！\n"
            f"●下次时间：{next_time}\n"
            "┗-★---信☆息---★-┛"
        )
        return f"{head}\n{desc}\n{body}"

    # =====================================================================
    # 深渊秘境
    # =====================================================================
    def _abyss_dungeon(self, player: dict) -> str:
        """深渊秘境：高频、高运气、无次数上限，但越打侵蚀越高。"""
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物，发送『砸蛋』获取一只。"
        if p["level"] < data.ABYSS_LEVEL_REQ:
            return f"深渊秘境需要宠物达到 Lv{data.ABYSS_LEVEL_REQ}，当前 Lv{p['level']}。"
        busy = self._busy_reason(p)
        if busy:
            return busy

        # 刷新侵蚀（每日清零 + 自然衰减）
        self.store.refresh_abyss(player)
        corruption = self.store.get_abyss_corruption(player)
        pity = self.store.get_abyss_pity(player)

        # 动态成本与冷却
        energy_cost = min(data.ABYSS_MAX_ENERGY, data.ABYSS_BASE_ENERGY + corruption * 3)
        cooldown = min(data.ABYSS_MAX_COOLDOWN, data.ABYSS_BASE_COOLDOWN + corruption * 60)

        petmod.refresh_energy(p)
        if p["energy"] < energy_cost:
            return (
                f"精力不足，进入深渊秘境需要 {energy_cost} 点精力"
                f"（当前 {p['energy']}/{p['energy_max']}）。"
            )
        cd = self._cooldown_block(player, "深渊秘境", "深渊秘境")
        if cd:
            return cd

        # 扣除并记录
        p["energy"] -= energy_cost
        self.store.set_cooldown(player, "深渊秘境", cooldown)

        # 读取祝福与一次性 Buff
        blessing = self.store.get_abyss_blessing(player)
        buffs = self.store.get_abyss_buffs(player)
        no_corruption = blessing == "侵蚀压制" or buffs.get("no_corruption", 0) > 0
        if no_corruption:
            if buffs.get("no_corruption", 0) > 0:
                self.store.consume_abyss_buff(player, "no_corruption")
            # 侵蚀压制祝福在结算时统一清除
        else:
            self.store.add_abyss_corruption(player, 1)
        corruption_after = self.store.get_abyss_corruption(player)

        # 经验加成倍率（祝福）
        exp_bonus = 1.2 if blessing == "幸运之星" else 1.0
        # 大奖概率加成（祝福）
        blessing_jackpot_bonus = 10 if blessing == "怜悯加速" else 0

        # 抽取事件：怜悯值 + 祝福会提高大奖概率
        events = list(data.ABYSS_EVENTS)
        weights = [e.get("weight", 1) for e in events]
        pity_bonus = pity * 0.5
        for i, e in enumerate(events):
            if e["id"] in ("blessing", "lord"):
                weights[i] += pity_bonus + blessing_jackpot_bonus
        event = random.choices(events, weights=weights, k=1)[0]

        exp_to_next = data.exp_to_next(p["level"])
        corruption_factor = max(0.1, 1.0 - corruption * 0.04)
        base_power = petmod.battle_power(p)
        nick = p["nickname"]

        # 玩家战力浮动（侵蚀越高，下限越低）
        low = max(0.3, 0.8 - corruption * 0.02)
        roll = int(base_power * random.uniform(low, 1.8))

        lines = [
            f"## 🌀 深渊秘境 · 第 {corruption_after} 层侵蚀",
            f"当前侵蚀：**{corruption}** → **{corruption_after}** 点",
            f"经验收益倍率：**{int(corruption_factor * 100)}%**",
        ]
        if blessing:
            lines.append(f"✨ 本次祝福：**{blessing}**")
        if pity:
            lines.append(f"深渊怜悯：**{pity}**（大奖概率提升）")
        lines.append("")

        reward_lines: list[str] = []

        def _add_exp(mult: float) -> int:
            amt = max(1, int(exp_to_next * mult * corruption_factor * exp_bonus))
            petmod.add_exp(p, amt)
            return amt

        def _hurt(dmg_pct: float) -> bool:
            dmg = max(1, int(p["hp_max"] * dmg_pct))
            p["hp"] = max(0, p["hp"] - dmg)
            # 深渊回春石：死亡时自动复活
            if petmod.is_dead(p) and self.store.consume_abyss_buff(player, "revive"):
                p["status"] = "正常"
                p["hp"] = max(1, int(p["hp_max"] * 0.3))
                reward_lines.append(
                    f"💎 深渊回春石触发，『{nick}』从死亡边缘复活！HP 恢复至 {p['hp']}/{p['hp_max']}。"
                )
                return False
            if petmod.is_dead(p):
                p["status"] = "死亡"
                p["mood"] = max(1, p.get("mood", 5) - 1)
                reward_lines.append(f"💀 『{nick}』伤势过重不幸阵亡，心情降至 {p['mood']} 星。")
                return True
            reward_lines.append(f"❤️‍🩹 『{nick}』受到深渊伤害，HP -{dmg}（{p['hp']}/{p['hp_max']}）。")
            return False

        def _random_status() -> None:
            status = random.choice(["中毒", "沉眠", "麻痹", "亢奋", "虚弱", "肌饿"])
            p["status"] = status
            reward_lines.append(f"☠️ 深渊诅咒降临，宠物陷入『{status}』状态。")

        # ---------- 事件分支 ----------
        if event["id"] == "guard":
            monster_power = int(base_power * event.get("power_mult", 0.5) * (1 + corruption * 0.06))
            lines.append(f"🗡️ 你遭遇了 **{event['name']}**！")
            lines.append(f"● 你的战力：{roll}　VS　守卫战力：{monster_power}")
            if roll >= monster_power:
                exp = _add_exp(event["exp_mult"])
                jifen = 50 + p["level"] * 2
                crystal = random.randint(*event.get("crystal", (1, 3)))
                self.store.add_currency(player, "积分", jifen)
                self.store.add_abyss_crystal(player, crystal)
                reward_lines.extend(
                    [
                        f"经验 +{exp}",
                        f"积分 +{jifen}",
                        f"深渊结晶 +{crystal}",
                    ]
                )
                lines.append(f"✅ 激战之后，你击退了深渊守卫！")
            else:
                exp = _add_exp(event["exp_mult"] * 0.1)
                died = _hurt(0.3)
                reward_lines.append(f"经验 +{exp}（战败保底）")
                if not died:
                    lines.append(f"❌ 守卫太过强大，『{nick}』只能狼狈撤退。")

        elif event["id"] == "chest":
            lines.append(f"🎁 你发现了一个古老的 **{event['name']}**！")
            if random.random() < event.get("mimic_chance", 0.2):
                monster_power = int(base_power * event.get("power_mult", 0.35) * (1 + corruption * 0.06))
                lines.append(f"⚠️ 宝箱突然张开血盆大口，原来是宝箱怪！")
                lines.append(f"● 你的战力：{roll}　VS　宝箱怪战力：{monster_power}")
                if roll >= monster_power:
                    exp = _add_exp(event["mimic_exp_mult"])
                    crystal = random.randint(2, 4)
                    self.store.add_abyss_crystal(player, crystal)
                    reward_lines.extend([f"经验 +{exp}", f"深渊结晶 +{crystal}"])
                    lines.append(f"✅ 你险胜宝箱怪，收获了额外战利品！")
                else:
                    _hurt(0.2)
                    lines.append(f"❌ 宝箱怪狠狠咬了你一口，空手而逃。")
            else:
                exp = _add_exp(event["exp_mult"])
                crystal = random.randint(*event.get("crystal", (1, 2)))
                self.store.add_abyss_crystal(player, crystal)
                reward_lines.extend([f"经验 +{exp}", f"深渊结晶 +{crystal}"])
                lines.append(f"✅ 你小心翼翼地打开宝箱， safely 拿走了里面的东西。")

        elif event["id"] == "turbulence":
            lines.append(f"⚡ 你踏入了 **{event['name']}**，周围空间开始扭曲……")
            outcome = random.choice(["低语", "暗影", "能量", "异象"])
            if outcome == "低语":
                cleared = self.store.clear_abyss_corruption(player, 2)
                heal = max(1, int(p["hp_max"] * 0.2))
                p["hp"] = min(p["hp_max"], p["hp"] + heal)
                reward_lines.append(f"🌟 深渊的低语抚慰了你：侵蚀 -{cleared}，HP +{heal}。")
            elif outcome == "暗影":
                self.store.add_abyss_corruption(player, 1)
                _random_status()
                lines[-1] = lines[-1].replace("……", "，阴影正在侵蚀你的宠物！")
            elif outcome == "能量":
                exp = _add_exp(0.1)
                reward_lines.append(f"经验 +{exp}（乱流中捕捉到一丝能量）")
            else:  # 异象
                jifen = 20 + p["level"]
                self.store.add_currency(player, "积分", jifen)
                reward_lines.append(f"积分 +{jifen}（你看到了无法理解的景象）")

        elif event["id"] == "altar":
            lines.append(f"🌀 一座 **{event['name']}** 挡在面前，上面刻着献祭符文。")
            safe_mult = event.get("exp_mult_safe", 0.2)
            sac_mult = event.get("exp_mult_sacrifice", 0.6)
            sac_pct = event.get("hp_sacrifice_pct", 0.15)
            if p["hp"] > p["hp_max"] * 0.3:
                p["hp"] = max(1, int(p["hp"] - p["hp_max"] * sac_pct))
                exp = _add_exp(sac_mult)
                reward_lines.append(f"你献祭了 {int(sac_pct * 100)}% HP，深渊回报了你 {exp} 经验。")
                lines.append(f"✅ 宠物忍痛完成献祭，获得了丰厚回报。")
            else:
                exp = _add_exp(safe_mult)
                reward_lines.append(f"经验 +{exp}（状态不佳，只取了保底祝福）")
                lines.append(f"⚠️ 宠物状态不佳，你不敢献祭，只取走了保底祝福。")

        elif event["id"] == "blessing":
            lines.append(f"✨ **{event['name']}** 降临！深渊意志向你微笑。")
            exp = _add_exp(event["exp_mult"])
            crystal = random.randint(*event.get("crystal", (2, 4)))
            jifen = 100 + p["level"] * 3
            p["hp"] = p["hp_max"]
            self.store.add_currency(player, "积分", jifen)
            self.store.add_abyss_crystal(player, crystal)
            self.store.reset_abyss_pity(player)
            reward_lines.extend(
                [
                    f"经验 +{exp}",
                    f"积分 +{jifen}",
                    f"深渊结晶 +{crystal}",
                    "血量已回满",
                ]
            )
            lines.append(f"🎉 这是深渊罕见的恩赐！")

        elif event["id"] == "lord":
            monster_power = int(base_power * event.get("power_mult", 1.2) * (1 + corruption * 0.06))
            lines.append(f"👹 **{event['name']}** 降临！恐怖的威压笼罩四周。")
            lines.append(f"● 你的战力：{roll}　VS　领主战力：{monster_power}")
            if roll >= monster_power:
                exp = _add_exp(event["exp_mult"])
                crystal = random.randint(*event.get("crystal", (3, 5)))
                self.store.add_abyss_crystal(player, crystal)
                reward_lines.extend([f"经验 +{exp}", f"深渊结晶 +{crystal}"])
                if random.random() < 0.3:
                    self.store.add_item(player, "万能宝石", 1)
                    reward_lines.append("万能宝石 ×1")
                self.store.reset_abyss_pity(player)
                lines.append(f"🏆 你击败了深渊领主，名扬秘境！")
            else:
                died = _hurt(0.5)
                lines.append(f"❌ 领主的力量碾压了你，『{nick}』惨败。")

        # 怜悯值结算：只有大奖事件会清零
        if event["id"] not in ("blessing", "lord"):
            self.store.add_abyss_pity(player, 1)

        # 组装奖励展示
        if reward_lines:
            lines.append("")
            lines.append("**本次收获**")
            lines.extend(f"• {r}" for r in reward_lines)

        # 下次成本提示
        next_corruption = self.store.get_abyss_corruption(player)
        next_energy = min(data.ABYSS_MAX_ENERGY, data.ABYSS_BASE_ENERGY + next_corruption * 3)
        next_cd = min(data.ABYSS_MAX_COOLDOWN, data.ABYSS_BASE_COOLDOWN + next_corruption * 60)
        lines.append("")
        lines.append(
            f"> 下次挑战：消耗 {next_energy} 精力，冷却 {self._fmt_duration(next_cd)}"
        )
        if next_corruption >= 15:
            lines.append("⚠️ 侵蚀已非常高，建议先休息或使用『净化药水』清理。")

        # 精力回收祝福：返还 50% 精力
        if blessing == "精力回收":
            refund = max(1, energy_cost // 2)
            p["energy"] = min(p["energy_max"], p["energy"] + refund)
            lines.append(f"♻️ 『精力回收』生效，返还 {refund} 点精力。")

        # 一次性祝福使用完毕
        if blessing:
            self.store.clear_abyss_blessing(player)

        return "\n".join(lines) + self._auto_level_note(player, p)

    def _abyss_intro(self) -> str:
        """返回深渊秘境的简洁玩法介绍。"""
        return (
            "## 🌀 深渊秘境 · 玩法简介\n"
            "\n"
            "深渊秘境是一个**低门槛、高频次、看运气**的副本玩法。\n"
            "\n"
            "**基础规则**\n"
            "- 宠物达到 **Lv20** 即可进入\n"
            "- 每次消耗 **20 精力**，冷却 **5 分钟**\n"
            "- **无次数限制**，但越刷越亏\n"
            "\n"
            "**核心机制：深渊侵蚀**\n"
            "- 每次进入都会 +1 点侵蚀\n"
            "- 侵蚀越高：经验收益越低、怪物越强、你越容易获得负面状态\n"
            "- 侵蚀每 20 分钟自然 -1，每日 0 点清零\n"
            "- 道具商城可用 **5000 积分** 购买『净化药水』，清除 5 点侵蚀\n"
            "- 深渊商店可用 **5 结晶** 购买『净化药水』\n"
            "\n"
            "**深渊结晶用途**\n"
            "- `深渊商店` — 用结晶购买净化药水、深渊护符、深渊回春石\n"
            "- `深渊祝福` — 购买一次性祝福（幸运之星/侵蚀压制/怜悯加速/精力回收）\n"
            "\n"
            "**可能遇到的事件**\n"
            "- 🗡️ 深渊守卫：普通战斗\n"
            "- 🎁 深渊宝箱：开箱，有概率是宝箱怪\n"
            "- ⚡ 深渊乱流：随机增益/减益/受伤/清侵蚀\n"
            "- 🌀 古老祭坛：献祭 HP 换高经验，或安全保底\n"
            "- ✨ 深渊赐福：大奖，回血 + 大量经验\n"
            "- 👹 深渊领主：Boss 战，最高奖励\n"
            "\n"
            "**相关指令**\n"
            "- `深渊秘境` — 进入副本\n"
            "- `深渊商店` — 结晶商店\n"
            "- `深渊购买 商品名` — 购买商品\n"
            "- `深渊祝福` / `深渊祝福 祝福名` — 购买/查看祝福\n"
            "- `深渊介绍` — 查看本介绍\n"
            "- `使用 净化药水` — 清除侵蚀\n"
            "- `我的信息` — 查看深渊结晶数量\n"
            "\n"
            "> 💡 小贴士：前几次收益最高，侵蚀高了建议先休息、净化，或买祝福压制。"
        )

    def _abyss_shop(self, player: dict) -> str:
        """深渊结晶商店。"""
        crystal = self.store.get_abyss_crystal(player)
        lines = [
            "## 🏪 深渊商店",
            f"> 当前拥有深渊结晶：**{crystal}**",
            "",
            "**一次性道具**（购买后自动生效/入包）",
            "| 商品 | 结晶 | 说明 |",
            "|---|---|---|",
        ]
        for name, info in data.ABYSS_SHOP.items():
            lines.append(f"| {name} | {info['cost']} | {info['desc']} |")
        lines.append("")
        lines.append("**战前祝福**（购买后下一次挑战生效）")
        lines.append("| 祝福 | 结晶 | 说明 |")
        lines.append("|---|---|---|")
        for name, info in data.ABYSS_BLESSINGS.items():
            lines.append(f"| {name} | {info['cost']} | {info['desc']} |")
        lines.append("")
        lines.append("> 购买方式：`深渊购买 商品名` · `深渊祝福 祝福名`"
        )
        return "\n".join(lines)

    def _abyss_buy(self, player: dict, tokens: list[str]) -> str:
        """用深渊结晶购买深渊商店商品。"""
        if len(tokens) < 2:
            return "⚠️ 用法：`深渊购买 商品名`（例如：深渊购买 净化药水）"
        name = tokens[1]
        if name not in data.ABYSS_SHOP:
            return f"❌ 深渊商店没有『{name}』。发送 `深渊商店` 查看列表。"
        info = data.ABYSS_SHOP[name]
        cost = info["cost"]
        crystal = self.store.get_abyss_crystal(player)
        if crystal < cost:
            return f"❌ 深渊结晶不足（需要 {cost}，当前 {crystal}）。"
        self.store.add_abyss_crystal(player, -cost)
        if info["type"] == "item":
            self.store.add_item(player, info["give"], 1)
            return (
                f"✅ 花费 {cost} 深渊结晶购买『{name}』，"
                f"已放入背包。剩余结晶 {self.store.get_abyss_crystal(player)}。"
            )
        if info["type"] == "buff":
            self.store.add_abyss_buff(player, info["buff"], 1)
            return (
                f"✅ 花费 {cost} 深渊结晶购买『{name}』，"
                f"下一次挑战自动生效。剩余结晶 {self.store.get_abyss_crystal(player)}。"
            )
        return "❌ 商品类型异常，购买失败。"

    def _abyss_blessing(self, player: dict, tokens: list[str]) -> str:
        """购买/查看深渊祝福。"""
        crystal = self.store.get_abyss_crystal(player)
        active = self.store.get_abyss_blessing(player)
        if len(tokens) < 2:
            lines = [
                "## ✨ 深渊祝福",
                f"> 当前结晶：**{crystal}**",
            ]
            if active:
                lines.append(f"> 已购买祝福：**{active}**（下次挑战生效）")
            else:
                lines.append("> 当前无祝福，购买后下一次挑战生效")
            lines.append("")
            lines.append("| 祝福 | 结晶 | 说明 |")
            lines.append("|---|---|---|")
            for name, info in data.ABYSS_BLESSINGS.items():
                lines.append(f"| {name} | {info['cost']} | {info['desc']} |")
            lines.append("")
            lines.append("> 用法：`深渊祝福 祝福名`")
            return "\n".join(lines)
        name = tokens[1]
        if name not in data.ABYSS_BLESSINGS:
            return f"❌ 没有『{name}』祝福。发送 `深渊祝福` 查看列表。"
        info = data.ABYSS_BLESSINGS[name]
        cost = info["cost"]
        if crystal < cost:
            return f"❌ 深渊结晶不足（需要 {cost}，当前 {crystal}）。"
        if active and active != name:
            return (
                f"⚠️ 你已拥有祝福『{active}』，本次挑战会先消耗它。"
                f"如需更换，请先发送一次 `深渊秘境` 消耗掉当前祝福。"
            )
        self.store.add_abyss_crystal(player, -cost)
        self.store.set_abyss_blessing(player, name)
        return (
            f"✅ 花费 {cost} 深渊结晶购买祝福『{name}』，"
            f"下一次 `深渊秘境` 自动生效。剩余结晶 {self.store.get_abyss_crystal(player)}。"
        )

    def _quest_list(self) -> str:
        lines = [
            "## 📜 可领取剧情任务",
            "> `领取任务 任务名` 领取，完成后 `提交任务 任务名`",
            "",
        ]
        for n, q in data.QUESTS.items():
            need = "、".join(f"{k}×{v}" for k, v in q["need"].items())
            rwd = "、".join(f"{k}{v}" for k, v in q["reward"].items())
            lines.append(f"- **{n}**\n　　🎯 {need}　🎁 {rwd}")
        return "\n".join(lines)

    def _my_quests(self, player: dict) -> str:
        qs = player.get("quests", {})
        if not qs:
            return "📜 你还没有领取剧情任务，发送『宠物剧情任务』查看。"
        lines = ["## 📜 我的剧情任务", "━━━━━━━━━━━━━━"]
        stats = player.get("stats", {})
        for n, base in qs.items():
            need = data.QUESTS.get(n, {}).get("need", {})
            base = base if isinstance(base, dict) else {}
            prog = "、".join(
                f"{k} **{max(0, stats.get(k, 0) - base.get(k, 0))}**/{v}"
                for k, v in need.items()
            )
            lines.append(f"- **{n}**：{prog}")
        return "\n".join(lines)

    def _handle_quest(self, player: dict, tokens: list[str], cmd: str) -> str:
        if len(tokens) < 2:
            return f"用法：{cmd} 任务名称"
        name = tokens[1]
        if name not in data.QUESTS:
            return f"没有名为『{name}』的剧情任务。"
        need = data.QUESTS[name]["need"]
        stats = player.get("stats", {})
        if cmd == "领取任务":
            if name in player.get("quests", {}):
                return f"『{name}』已在进行中。"
            # 记录领取时的进度快照，任务进度从领取时刻起算
            player.setdefault("quests", {})[name] = {k: stats.get(k, 0) for k in need}
            return f"已领取剧情任务『{name}』。"
        # 提交任务
        if name not in player.get("quests", {}):
            return f"你尚未领取『{name}』。"
        base = player["quests"][name]
        base = base if isinstance(base, dict) else {}
        if any(stats.get(k, 0) - base.get(k, 0) < v for k, v in need.items()):
            return "任务目标尚未完成。"
        reward = data.QUESTS[name]["reward"]
        for k, v in reward.items():
            if k == "jifen":
                self.store.add_currency(player, "积分", v)
            elif k == "exp" and player.get("pet"):
                petmod.add_exp(player["pet"], v)
            elif k == "item":
                self.store.add_item(player, v, 1)
        player["quests"].pop(name, None)
        rwd = "、".join(f"{k}{v}" for k, v in reward.items())
        return f"✅ 提交『{name}』成功！获得奖励：{rwd}。"

    # =====================================================================
    # 婚恋
    # =====================================================================
    def _handle_love(self, player, group_id, cmd, tokens) -> str | None:
        if cmd == "宠物恋情":
            return self._love_status(player)
        if cmd == "宠物分手":
            return self._breakup(player, group_id)
        if cmd == "宠物离婚":
            return self._divorce(player, group_id)
        if cmd in ("宠物追求", "同意追求", "宠物求婚", "同意求婚"):
            target = self._arg(tokens, 1)
            if not target:
                return f"用法：{cmd} 用户ID"
            return self._love_action(player, group_id, target, cmd)
        return None

    def _love_status(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if p["love_state"] == "单身":
            return f"💔 『{p['nickname']}』当前**单身**。"
        return (
            f"💕 『{p['nickname']}』**{p['love_state']}**中\n"
            f"> 伴侣：`{p['love_target']}`　好感度：{p['favor']}"
        )

    def _love_action(
        self, player: dict, group_id: str, target: str, cmd: str
    ) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if target == player["qq"]:
            return "不能对自己的宠物执行该操作。"
        tp, err = self._find_target(group_id, target)
        if err:
            return err
        if not tp.get("pet"):
            return "对方没有宠物。"
        tpet = tp["pet"]

        if cmd == "宠物追求":
            if p["love_state"] != "单身" or tpet["love_state"] != "单身":
                return "只有双方都单身才能追求。"
            if p["gender"] == tpet["gender"]:
                return "只有异性宠物才能互相追求。"
            tp.setdefault("pending", {})["pursue"] = player["qq"]
            return f"💌 已向 {target} 的宠物发起追求，等待对方『同意追求 {player['qq']}』。"
        if cmd == "同意追求":
            pend = player.get("pending", {}).get("pursue")
            if pend != target:
                return "没有来自该 QQ 的追求请求。"
            p["love_state"] = tpet["love_state"] = "恋爱"
            p["love_target"], tpet["love_target"] = target, player["qq"]
            p["favor"] = tpet["favor"] = data.LOVE_INIT_FAVOR
            player.get("pending", {}).pop("pursue", None)
            return f"💕 追求成功！双方进入恋爱状态，初始好感度 {data.LOVE_INIT_FAVOR}。"
        if cmd == "宠物求婚":
            if p["love_state"] != "恋爱" or p["love_target"] != target:
                return "只能向恋爱中的伴侣求婚。"
            if p["favor"] < data.FAVOR_MARRY_REQUIRE:
                return f"好感度需达到 {data.FAVOR_MARRY_REQUIRE} 才能求婚（当前 {p['favor']}）。"
            if not self.store.has_item(player, "永恒钻戒"):
                return "求婚需要消耗『永恒钻戒』。"
            if not self.store.remove_item(player, "永恒钻戒"):
                return "你没有『永恒钻戒』。"
            tp.setdefault("pending", {})["marry"] = player["qq"]
            return f"💍 已向 {target} 求婚，等待对方『同意求婚 {player['qq']}』。"
        if cmd == "同意求婚":
            pend = player.get("pending", {}).get("marry")
            if pend != target:
                return "没有来自该 QQ 的求婚请求。"
            p["love_state"] = tpet["love_state"] = "已婚"
            player.get("pending", {}).pop("marry", None)
            return "🎉 喜结连理！双方宠物已婚，约会获得双倍好感度。"
        return "未知姻缘操作。"

    def _breakup(self, player: dict, group_id: str) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if p["love_state"] != "恋爱":
            return "当前不在恋爱状态。"
        partner = p.get("love_target")
        self._reset_love(p)
        if partner:
            tp = self.store.get_player(partner, group_id, create=False)
            if tp and tp.get("pet"):
                self._reset_love(tp["pet"])
        return "💔 已分手。"

    def _divorce(self, player: dict, group_id: str) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if p["love_state"] != "已婚":
            return "当前未婚。"
        partner = p.get("love_target")
        self._reset_love(p)
        if partner:
            tp = self.store.get_player(partner, group_id, create=False)
            if tp and tp.get("pet"):
                self._reset_love(tp["pet"])
        return "🕊 已离婚，缘尽于此。"

    @staticmethod
    def _reset_love(pet: dict) -> None:
        pet["love_state"] = "单身"
        pet["love_target"] = None
        pet["favor"] = 0
