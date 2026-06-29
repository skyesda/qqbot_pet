"""宠物乐园 —— AstrBot 群聊养成 / 对战插件。

参考某 QQ 群"宠物联盟"玩法复刻：砸蛋抽宠、宠物商城、属性克制对战、繁殖姻缘、
进化飞升渡劫、天赋觉醒、炼丹、神器/秘技、副本、剧情任务、跨群挑战、排行神榜等。

指令均为无前缀中文指令（与参考一致），通过监听全部消息后自行解析路由。
"""

from __future__ import annotations

import random
import time
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api import message_components as Comp
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .petpark import data, pet as petmod
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
    "查看说明",
    # 管理员：增减货币
    "加金币",
    "减金币",
    "加积分",
    "减积分",
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
        self.rank_reward_jifen = max(0, int(self.config.get("rank_reward_jifen", 50000)))
        # 签到积分/金币随机范围（可在配置面板调整）
        self.sign_jifen_min = max(0, int(self.config.get("sign_jifen_min", 1000)))
        self.sign_jifen_max = max(self.sign_jifen_min, int(self.config.get("sign_jifen_max", 12000)))
        self.sign_coin_min = max(0, int(self.config.get("sign_coin_min", 50)))
        self.sign_coin_max = max(self.sign_coin_min, int(self.config.get("sign_coin_max", 200)))
        # 连续签到每天的额外金币（额外金币 = 连续天数 × 该值，封顶 7 天）
        self.sign_streak_bonus = max(0, int(self.config.get("sign_streak_bonus", 100)))
        # 精力恢复速度为全局常量，按配置覆盖
        data.ENERGY_REGEN_PER_MIN = max(1, int(self.config.get("energy_regen_per_min", data.ENERGY_REGEN_PER_MIN)))
        # 专属管理网站（卡密生成 + 数据增删改查）
        self._web = None
        if bool(self.config.get("web_enabled", True)):
            self._start_web_admin()

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
        try:
            reply = self.dispatch(event, qq, group_id, text)
        except Exception as e:  # 保证插件不因单条消息崩溃
            logger.exception("[petpark] 处理指令出错")
            reply = f"宠物乐园处理出错：{e}"
        if reply is None:
            return
        await self.store.save()
        event.stop_event()
        # 群聊里 @ 触发者，便于多人同时游玩时分辨各自的消息；私聊不 @。
        if self._is_group(group_id):
            # QQ 官方机器人(qq_official)适配器会忽略 At 组件，故同时以纯文本
            # 形式前置 @昵称，确保任何平台都能看出这条消息@的是谁。
            name = self._sender_name(event) or qq
            head = Comp.Plain(f"@{name}\n")
            at = self._safe_at(qq)
            chain = ([at] if at else []) + [head, Comp.Plain(reply)]
            yield event.chain_result(chain)
        else:
            yield event.plain_result(reply)

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
    def dispatch(self, event, qq, group_id, text) -> str | None:
        tokens = text.split()
        cmd = tokens[0]
        # 非本插件指令直接放行，避免为每条普通聊天创建玩家/群档案
        if cmd not in KNOWN_COMMANDS and text not in data.DAILY_ACTIONS:
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

        # ---- 管理员：增减指定用户金币 / 积分 ----
        if cmd in ("加金币", "减金币", "加积分", "减积分"):
            return self._admin_adjust(event, group_id, cmd, tokens)

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

        # ---- 每日签到 ----
        if cmd == "签到":
            return self._sign_in(player, group_id)

        # ---- 卡密兑换 ----
        if cmd in ("兑换", "卡密兑换"):
            return self._redeem(player, group_id, qq, tokens)

        # ---- 获取宠物 ----
        if cmd == "砸蛋":
            return self._smash_egg(player)
        if cmd == "购买宠物":
            return self._buy_pet(player, tokens)

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
            return self._daily(player, text)

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
            return self._rank(group_id, local=True)
        if cmd == "宠物神榜":
            return self._rank(group_id, local=False)
        if cmd == "领取神榜奖励":
            return self._claim_rank_reward(player, group_id)

        # ---- 副本 ----
        if cmd == "宠物副本":
            return self._dungeon_list()
        if cmd == "进入副本":
            return self._enter_dungeon(player, tokens)

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

        return None

    # =====================================================================
    # 帮助 / 信息查询
    # =====================================================================
    def _handle_info(self, cmd: str, tokens: list[str]) -> str | None:
        if cmd in ("宠物菜单", "宠物指令", "宠物帮助"):
            return self._menu_text()
        if cmd == "宠物种类":
            names = " · ".join(data.SPECIES_NAMES)
            return f"## 📖 宠物种类（共 {len(data.SPECIES_NAMES)} 种）\n{names}"
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
            for table in (data.ITEMS, data.ELIXIRS, data.ARTIFACTS, data.SKILLS, data.TALENTS):
                if name in table:
                    return f"## 📘 {name}\n{table[name]['desc']}"
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

    def _cooldown_block(self, player: dict, key: str, label: str) -> str | None:
        """若该行为仍在冷却中，返回提示文本；否则返回 None。"""
        remain = self.store.cooldown_remaining(player, key)
        if remain > 0:
            return f"⏳ **{label}** 冷却中，还需 `{self._fmt_duration(remain)}`。"
        return None

    def _my_info(self, player: dict, group_id: str) -> str:
        gid = group_id if group_id and group_id != "private" else "私聊"
        return "\n".join(
            [
                "## 📇 我的信息",
                "━━━━━━━━━━━━━━",
                f"🆔 **QQ号**　`{player['qq']}`",
                f"👥 **群号**　`{gid}`",
                f"🪙 **金币**　{player.get('coin', 0)}",
                f"💎 **积分**　{player.get('jifen', 0)}",
                f"💠 **钻石**　{player.get('diamond', 0)}",
            ]
        )

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

    def _admin_adjust(
        self, event, group_id: str, cmd: str, tokens: list[str]
    ) -> str:
        if not self._is_admin(event):
            return "仅管理员可增减用户金币/积分。"
        currency = "金币" if "金币" in cmd else "积分"
        sign = 1 if cmd.startswith("加") else -1
        # 目标 ID 不一定是纯数字（QQ 官方机器人/频道为 openid 字符串），
        # 仅要求最后给出的数量是整数。
        if len(tokens) < 3 or not tokens[2].lstrip("-").isdigit():
            return f"用法：{cmd} QQ号/ID 数量"
        target = tokens[1]
        amount = int(tokens[2])
        if amount <= 0:
            return f"用法：{cmd} QQ号/ID 数量（数量需为正整数）"
        tp, err = self._find_target(group_id, target)
        if err:
            return err
        before = self.store.get_currency(tp, currency)
        self.store.add_currency(tp, currency, sign * amount)
        after = self.store.get_currency(tp, currency)
        verb = "增加" if sign > 0 else "减少"
        icon = "🪙" if currency == "金币" else "💎"
        return (
            f"## ⚙️ 管理操作\n"
            f"已为用户 `{target}` {verb}{icon}**{currency} {amount}**\n"
            f"> {currency}：{before} → **{after}**"
        )

    def _menu_text(self) -> str:
        return "\n".join(
            [
                "# 🐾 宠物乐园 · 指令菜单",
                "",
                "**🐣 入门**",
                "砸蛋 · 购买宠物 · 我的宠物 · 宠物状态 · 宠物改名 · 宠物变性 · 赠送宠物 用户ID · 放生宠物 · 宠物侦查 用户ID",
                "",
                "**🛒 商城 / 背包**",
                "宠物商城 · 道具商城 · 宠物市场 · 查看背包 · 购买 物品 数量 · 使用 物品 · 出售 物品 数量 · 丢弃 物品 数量 · 转让 用户ID 物品 数量 · 清空背包",
                "",
                "**🍖 喂养 / 日常**（各 10~20 分钟冷却）",
                "喂食 物品 · " + " · ".join(data.DAILY_ACTIONS),
                "",
                "**📈 成长**",
                "一键升级宠物 · 宠物升级 [次数] · 宠物进化 · 宠物飞升 · 宠物渡劫 · 幻境寻宝 · 宠物神仙劫",
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
                "**🏰 副本 / 任务**（副本 10 分钟冷却）",
                "宠物副本 · 进入副本 名称 · 宠物剧情任务 · 领取任务 名称 · 提交任务 名称 · 我的剧情任务 · 取消剧情任务",
                "",
                "**💕 姻缘**",
                "宠物追求 用户ID · 同意追求 用户ID · 宠物求婚 用户ID · 同意求婚 用户ID · 宠物分手 · 宠物离婚 · 宠物恋情",
                "",
                "**📇 个人**",
                "我的信息（查看 QQ号/群号/金币/积分/钻石） · 签到（每日领积分金币） · 兑换 卡密（卡密充值金币/积分/钻石）",
                "",
                "**📖 图鉴查询**",
                "宠物种类 · 属性 · 状态 · 神器 · 秘技 · 仙丹 · 天赋 · 查看说明 名称",
                "",
                "**⚙️ 管理员**",
                "开启/关闭宠物乐园 · 开启/关闭宠物跨群 · 加金币 QQ 数量 · 减金币 QQ 数量 · 加积分 QQ 数量 · 减积分 QQ 数量",
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
                "聚灵丹",
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
        lines.append("> 品质可选：" + " / ".join(data.QUALITIES) + "（默认普通，高品质加价）")
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
        price = data.PET_MARKET[species]
        # 高品质加价
        mult = 1 + data.QUALITIES.index(quality) * 0.5
        cost = int(price * mult)
        if self.store.get_currency(player, "积分") < cost:
            return f"购买『{species}』（{quality}）需 {cost} 积分，积分不足。"
        self.store.add_currency(player, "积分", -cost)
        player["pet"] = petmod.new_pet(species, quality)
        return f"✅ **购买成功！**花费 {cost} 积分获得 【{quality}】品质的 **{species}**。"

    # =====================================================================
    # 宠物查看 / 管理
    # =====================================================================
    @staticmethod
    def _need_pet(player: dict) -> dict | None:
        return player.get("pet")

    @staticmethod
    def _busy_reason(p: dict) -> str | None:
        """宠物当前是否无法被操作（死亡 / 假死惊魂）。可操作返回 None。"""
        if petmod.is_dead(p):
            return "宠物已死亡，请先复活（宠物复活 / 九转还魂丹）。"
        if petmod.is_frozen(p):
            return f"宠物假死/惊魂中，约 {petmod.frozen_remain_min(p)} 分钟后才能操作。"
        return None

    def _my_pet(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物，发送『砸蛋』或『宠物市场』获取一只吧！"
        return petmod.render_pet(p)

    def _inspect(self, group_id: str, tokens: list[str]) -> str:
        target = self._arg(tokens, 1)
        if not target:
            return "用法：宠物侦查 用户ID"
        tp, err = self._find_target(group_id, target)
        if err:
            return err
        if not tp.get("pet"):
            return "对方还没有宠物。"
        return petmod.render_pet(tp["pet"])

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
        if not it or not it.get("usable"):
            return f"『{name}』不能直接使用。"
        count = self._parse_count(tokens, 2)
        if not self.store.has_item(player, name, count):
            return f"背包里『{name}』数量不足。"
        eff = it["effect"]
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
        msgs = []
        for _ in range(count):
            msgs.append(self._apply_effect(p, eff, name))
        self.store.remove_item(player, name, count)
        petmod.refresh_energy(p)
        return f"使用『{name}』x{count}：\n" + "\n".join(msgs[-1:])

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
    def _daily(self, player: dict, action: str) -> str:
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
            return f"💕 约会愉快，好感度 +{gain}，当前 {p['favor']}。"
        if action in ("修炼", "双修"):
            base = random.randint(80, 200) + p["level"] * 10
            exp = base * (2 if action == "双修" else 1)
            petmod.add_exp(p, exp)
            return (
                f"🧘 {action}完成，经验 +{exp}，当前经验 {p['exp']}。"
                + self._auto_level_note(p)
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
            self.store.add_item(player, "万能宝石", 2)
            return (
                f"🧭 探险偶遇神器图纸（{art}），并获得万能宝石 x2，可用于『打造神器』！"
            )
        if kind == "秘技":
            skill = random.choice(data.SKILL_NAMES)
            return f"🧭 探险参悟到秘技线索（{skill}），可前往『参悟秘技 {skill}』！"
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
        n = petmod.auto_level_up(p)
        if n == 0:
            return f"未能升级（经验或精力不足）。当前 Lv{p['level']}/{petmod.level_cap(p)}。"
        return f"⬆ 一键升级 +{n} 级！当前 Lv{p['level']}/{petmod.level_cap(p)}，剩余精力 {p['energy']}。"

    def _manual_level(self, player: dict, tokens: list[str]) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        times = self._parse_count(tokens, 1)
        petmod.refresh_energy(p)
        n, note = petmod.level_up(p, times)
        if n == 0:
            return f"升级失败：{note}"
        suffix = f"（{note}）" if note else ""
        return f"⬆ 升级 +{n} 级！当前 Lv{p['level']}/{petmod.level_cap(p)}{suffix}。"

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
        self.store.add_currency(player, "积分", -cost["jifen"])
        self.store.remove_item(player, cost["material"], cost["material_count"])
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
        s = data.SKILLS[name]
        if p["level"] < s["level_req"]:
            return f"参悟『{name}』需要等级 Lv{s['level_req']}。"
        if p["intel"] < s["intel_req"]:
            return f"参悟『{name}』需要智力 {s['intel_req']}（当前 {p['intel']}）。"
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
            dead_txt = ""
        else:
            attacker["hp"] = max(0, attacker["hp"] - loss)
            dead_txt = ""
            if attacker["hp"] <= 0:
                attacker["status"] = "死亡"
                dead_txt = "，你的宠物力竭身亡！"
        return (
            f"⚔ 战斗失败！你的『{attacker['nickname']}』(战力{ap}) 不敌 "
            f"『{defender['nickname']}』(战力{dp}){dead_txt or '，受了点伤'}。"
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

    def _rank(self, group_id: str, local: bool) -> str:
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
        entries = entries[: self.rank_size]
        if not entries:
            return "暂无宠物上榜。"
        title = "## 🏆 宠物排行（本群）" if local else "## 🏅 宠物神榜（全服）"
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = [title, "━━━━━━━━━━━━━━"]
        for i, (q, pet, bp) in enumerate(entries, 1):
            rk = medals.get(i, f"`{i}.`")
            lines.append(
                f"{rk} **{pet['nickname']}**（{pet['quality']}/{pet['stage']}）\n"
                f"　　💥 战力 `{bp}`　·　`{q}`"
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
        reward = self.rank_reward_jifen
        self.store.add_currency(player, "积分", reward)
        return f"🎁 神榜强者奖励到账，积分 +{reward}！"

    # =====================================================================
    # 副本 / 剧情任务
    # =====================================================================
    def _dungeon_list(self) -> str:
        lines = [
            "## 🏰 宠物副本",
            "> 进入方式：`进入副本 副本名称`（冷却 10 分钟）",
            "",
        ]
        for n, d in data.DUNGEONS.items():
            lines.append(
                f"- **{n}** `Lv{d['level_req']}`　🗡{d['monster']}（战力 {d['power']}）\n"
                f"　　耗 {d['energy']} 精力 · 产出 经验 {d['exp']} / 积分 {d['jifen']}"
            )
        lines.append("\n> 战力 ≥ 怪物战力即可通关；经验满后自动升级。")
        return "\n".join(lines)

    def _auto_level_note(self, p: dict) -> str:
        """经验满则自动一键升级，返回提示文本（无升级则空串）。"""
        if not petmod.exp_enough_to_level(p):
            return ""
        gained = petmod.auto_level_up(p)
        if gained <= 0:
            return ""
        return (
            f"\n⬆ **自动升级 +{gained} 级！**当前 "
            f"Lv{p['level']}/{petmod.level_cap(p)}（剩余精力 {p['energy']}）"
        )

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
            time.localtime(time.time() + data.DUNGEON_COOLDOWN * 60),
        )
        nick = p["nickname"]
        head = f"## ⚔ {nick} VS {monster}"
        if win:
            petmod.add_exp(p, d["exp"])
            self.store.add_currency(player, "积分", d["jifen"])
            drop = ""
            if random.random() < 0.2:
                self.store.add_item(player, "万能宝石", 1)
                drop = "\n●掉落道具：万能宝石 ×1"
            desc = f"您的{nick}在{name}遇见{monster}，激战{monster}结果**大胜**！"
            body = (
                "┏-★---副☆本---★-┓\n"
                f"●本次耗时：{minutes}分钟\n"
                f"●怪物战力：{power}\n"
                f"●获得经验：{d['exp']}\n"
                f"●获得积分：{d['jifen']}{drop}\n"
                f"●下次时间：{next_time}\n"
                "┗-★---信☆息---★-┛"
            )
            return f"{head}\n{desc}\n{body}{self._auto_level_note(p)}"
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
            tp.setdefault("pending", {})["marry"] = player["qq"]
            return f"💍 已向 {target} 求婚，等待对方『同意求婚 {player['qq']}』。"
        if cmd == "同意求婚":
            pend = player.get("pending", {}).get("marry")
            if pend != target:
                return "没有来自该 QQ 的求婚请求。"
            if not self.store.remove_item(tp, "永恒钻戒"):
                return "对方没有『永恒钻戒』，求婚失效。"
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
