"""宠物乐园 · 宠物联盟 —— AstrBot 群聊养成 / 对战插件。

参考某 QQ 群"宠物联盟"玩法复刻：砸蛋抽宠、宠物商城、属性克制对战、繁殖姻缘、
进化飞升渡劫、天赋觉醒、炼丹、神器/秘技、副本、剧情任务、跨群挑战、排行神榜等。

指令均为无前缀中文指令（与参考一致），通过监听全部消息后自行解析路由。
"""

from __future__ import annotations

import random
import time
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

import astrbot.api.message_components as Comp

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
    "开启宠物联盟",
    "关闭宠物联盟",
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
    "查看说明",
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
    "宠物乐园 · 宠物联盟：群聊宠物养成与对战玩法（砸蛋/商城/对战/进化/姻缘/天赋/炼丹/副本）。",
    "1.0.0",
    "https://github.com/AstrBotDevs/AstrBot",
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
            default_enabled=bool(self.config.get("default_enabled", True)),
            default_cross=bool(self.config.get("default_cross_group", True)),
        )
        # 管理员 QQ 列表（白名单），统一转成字符串便于比较
        self.admins = {str(a).strip() for a in self.config.get("admins", []) if str(a).strip()}
        # 对战精力消耗、排行名额、神榜奖励等可调参数
        self.attack_energy = max(0, int(self.config.get("attack_energy", data.ATTACK_ENERGY)))
        self.rank_size = max(1, int(self.config.get("rank_size", 10)))
        self.rank_reward_jifen = max(0, int(self.config.get("rank_reward_jifen", 50000)))
        # 精力恢复速度为全局常量，按配置覆盖
        data.ENERGY_REGEN_PER_MIN = max(1, int(self.config.get("energy_regen_per_min", data.ENERGY_REGEN_PER_MIN)))

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
        yield event.plain_result(reply)

    async def terminate(self):
        await self.store.save()

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
    def _at_target(event: AstrMessageEvent) -> str | None:
        try:
            for seg in event.get_messages():
                if isinstance(seg, Comp.At):
                    return str(seg.qq)
        except Exception:
            pass
        return None

    def _target_qq(
        self, event: AstrMessageEvent, tokens: list[str], idx: int
    ) -> str | None:
        """优先取 @ 对象，否则取 tokens[idx] 中的纯数字 QQ。"""
        at = self._at_target(event)
        if at:
            return at
        if idx < len(tokens) and tokens[idx].isdigit():
            return tokens[idx]
        return None

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
        if cmd in ("开启宠物联盟", "关闭宠物联盟"):
            if not self._is_admin(event):
                return "仅管理员可开关宠物联盟。"
            group["enabled"] = cmd.startswith("开启")
            return f"本群宠物联盟已{'开启' if group['enabled'] else '关闭'}。"
        if cmd in ("开启宠物跨群", "关闭宠物跨群"):
            if not self._is_admin(event):
                return "仅管理员可开关跨群功能。"
            group["cross"] = cmd.startswith("开启")
            return f"本群宠物跨群功能已{'开启' if group['cross'] else '关闭'}。"

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

        player = self.store.get_player(qq)
        if group_id and group_id != "private":
            player["group"] = group_id

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
            return self._transfer_item(event, player, tokens)

        # ---- 以下指令大多需要拥有宠物 ----
        if cmd in ("我的宠物", "查看宠物", "宠物图"):
            return self._my_pet(player)
        if cmd == "宠物侦查":
            return self._inspect(event, tokens)
        if cmd == "赠送宠物":
            return self._gift_pet(event, player, tokens)
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
            return self._use_elixir(event, player, tokens)

        # ---- 天赋触发指令 ----
        if cmd == "治愈":
            return self._talent_heal(event, player, tokens)
        if cmd == "复活":
            return self._talent_revive(event, player, tokens)
        if cmd == "精力转移":
            return self._energy_transfer(event, player, tokens)

        # ---- 对战 / 排行 ----
        if cmd == "宠物攻击":
            return self._attack(event, player, group_id, tokens)
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
        love = self._handle_love(event, player, group_id, cmd, tokens)
        if love is not None:
            return love

        return None

    # =====================================================================
    # 帮助 / 信息查询
    # =====================================================================
    def _handle_info(self, cmd: str, tokens: list[str]) -> str | None:
        if cmd == "宠物种类":
            names = "、".join(data.SPECIES_NAMES)
            return f"【宠物种类】共 {len(data.SPECIES_NAMES)} 种：\n{names}"
        if cmd == "属性":
            lines = ["【属性克制】PK 时克制方额外 +50% 战力"]
            lines.append("金→木→土→水→火→金")
            lines.append("风→雷→冰→风")
            lines.append("光→暗→光")
            return "\n".join(lines)
        if cmd == "神器":
            lines = ["【神器一览】（武器加成）"]
            for n, v in data.ARTIFACTS.items():
                lines.append(f"{n}：需 Lv{v['level_req']}，{v['desc']}")
            return "\n".join(lines)
        if cmd == "秘技":
            lines = ["【秘技一览】（秘技加成）"]
            for n, v in data.SKILLS.items():
                lines.append(
                    f"{n}：需 Lv{v['level_req']}/智力{v['intel_req']}，{v['desc']}"
                )
            return "\n".join(lines)
        if cmd == "仙丹":
            lines = ["【仙丹一览】"]
            for n, v in data.ELIXIRS.items():
                lines.append(f"{n}：{v['desc']}")
            return "\n".join(lines)
        if cmd == "天赋":
            lines = ["【天赋一览】每只宠物只能拥有 1 种天赋，可重复觉醒"]
            for n, v in data.TALENTS.items():
                tag = "（需定制）" if v["need_custom"] else ""
                lines.append(f"{n}{tag}：{v['desc']}")
            return "\n".join(lines)
        if cmd == "状态":
            return (
                "【宠物状态】"
                + "/".join(data.STATUSES)
                + "\n异常状态需喂食对应药品恢复，例如『喂食 解毒剂』可解除中毒。"
            )
        if cmd == "查看说明":
            if len(tokens) < 2:
                return "用法：查看说明 物品名称（例如：查看说明 九转还魂丹）"
            name = tokens[1]
            if name in data.ITEMS:
                return f"【{name}】{data.ITEMS[name]['desc']}"
            if name in data.ELIXIRS:
                return f"【{name}】{data.ELIXIRS[name]['desc']}"
            if name in data.ARTIFACTS:
                return f"【{name}】{data.ARTIFACTS[name]['desc']}"
            if name in data.SKILLS:
                return f"【{name}】{data.SKILLS[name]['desc']}"
            if name in data.TALENTS:
                return f"【{name}】{data.TALENTS[name]['desc']}"
            return f"未找到『{name}』的说明。"
        return None

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
            title = "🛒 宠物商城（发送：购买 物品名 数量）"
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
                "普通经验书",
                "五色药",
            ]
            title = "🏪 道具商城（发送：购买 物品名 数量）"
        lines = [title, "=" * 16]
        for n in wanted:
            it = data.ITEMS[n]
            lines.append(f"{n} —— {it['price']} {it['currency']}")
        return "\n".join(lines)

    def _pet_market_text(self) -> str:
        lines = ["🐾 宠物专域 / 宠物市场（发送：购买宠物 宠物名 [品质]）", "=" * 16]
        for n, p in data.PET_MARKET.items():
            lines.append(f"{n}（{p} 积分）")
        lines.append("=" * 16)
        lines.append("品质可选：" + "、".join(data.QUALITIES) + "（默认普通）")
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
        species = random.choice(data.SPECIES_NAMES)
        quality = self._roll_quality()
        player["pet"] = petmod.new_pet(species, quality)
        return f"💥 砸蛋成功！获得【{quality}】品质的『{species}』！\n发送『我的宠物』查看详情。"

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
        return f"购买成功！花费 {cost} 积分获得【{quality}】品质的『{species}』。"

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
        head = f"积分：{player['jifen']}  金币：{player['coin']}\n" + "-" * 16 + "\n"
        return head + petmod.render_pet(p)

    def _inspect(self, event, tokens: list[str]) -> str:
        target = self._target_qq(event, tokens, 1)
        if not target:
            return "用法：宠物侦查 QQ号"
        tp = self.store.get_player(target, create=False)
        if not tp or not tp.get("pet"):
            return "对方还没有宠物。"
        return petmod.render_pet(tp["pet"])

    def _gift_pet(self, event, player: dict, tokens: list[str]) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物可赠送。"
        target = self._target_qq(event, tokens, 1)
        if not target:
            return "用法：赠送宠物 QQ号"
        if target == player["qq"]:
            return "不能赠送给自己。"
        tp = self.store.get_player(target)
        if tp.get("pet"):
            return "对方已经有宠物了，无法接收。"
        tp["pet"] = p
        player["pet"] = None
        return f"已将『{p['nickname']}』赠送给 {target}。"

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

    def _transfer_item(self, event, player: dict, tokens: list[str]) -> str:
        # 转让 QQ 物品 数量
        target = self._target_qq(event, tokens, 1)
        if not target or len(tokens) < 3:
            return "用法：转让 QQ 物品 数量"
        name = tokens[2]
        count = self._parse_count(tokens, 3)
        if not self.store.has_item(player, name, count):
            return f"背包里『{name}』数量不足。"
        self.store.remove_item(player, name, count)
        self.store.add_item(self.store.get_player(target), name, count)
        return f"已转让 {name} x{count} 给 {target}。"

    def _bag_text(self, player: dict) -> str:
        bag = player.get("bag", {})
        head = f"💼 背包  |  积分：{player['jifen']}  金币：{player['coin']}"
        if not bag:
            return head + "\n（空空如也）"
        lines = [head, "-" * 16]
        for n, c in bag.items():
            lines.append(f"{n} x{c}")
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
        p["energy"] -= conf["energy"]

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
            return f"🧘 {action}完成，经验 +{exp}，当前经验 {p['exp']}。"
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

    def _use_elixir(self, event, player: dict, tokens: list[str]) -> str:
        # 使用仙丹 仙丹名 QQ号 数量
        if len(tokens) < 3:
            return "用法：使用仙丹 仙丹名 QQ号 [数量]"
        name = tokens[1]
        if name not in data.ELIXIRS:
            return f"没有名为『{name}』的仙丹。"
        target = self._target_qq(event, tokens, 2)
        if not target:
            return "请指定目标 QQ 号。"
        count = self._parse_count(tokens, 3)
        if not self.store.has_item(player, name, count):
            return f"背包里『{name}』数量不足。"
        tp = self.store.get_player(target, create=False)
        if not tp or not tp.get("pet"):
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
        return f"对 {target} 的宠物使用『{name}』x{count}：{msg}"

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
    def _talent_heal(self, event, player: dict, tokens: list[str]) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if p.get("talent") != "妙手回春":
            return "需要觉醒『妙手回春』天赋才能治愈他人宠物。"
        target = self._target_qq(event, tokens, 1)
        if not target:
            return "用法：治愈 QQ"
        tp = self.store.get_player(target, create=False)
        if not tp or not tp.get("pet"):
            return "目标没有宠物。"
        tp["pet"]["hp"] = tp["pet"]["hp_max"]
        return f"🌿 已治愈 {target} 的宠物，血量回满。"

    def _talent_revive(self, event, player: dict, tokens: list[str]) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if p.get("talent") != "起死回生":
            return "需要觉醒『起死回生』天赋才能复活他人宠物。"
        target = self._target_qq(event, tokens, 1)
        if not target:
            return "用法：复活 QQ"
        tp = self.store.get_player(target, create=False)
        if not tp or not tp.get("pet"):
            return "目标没有宠物。"
        tpet = tp["pet"]
        tpet["status"] = "正常"
        tpet["hp"] = tpet["hp_max"]
        return f"💫 已复活 {target} 的宠物。"

    def _energy_transfer(self, event, player: dict, tokens: list[str]) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if p.get("talent") != "精力转移":
            return "需要觉醒『精力转移』天赋（需定制宠物）才能转移精力。"
        target = self._target_qq(event, tokens, 1)
        amount = self._parse_count(tokens, 2)
        if not target or len(tokens) < 3:
            return "用法：精力转移 QQ 精力值"
        if p["energy"] < amount:
            return "自身精力不足。"
        tp = self.store.get_player(target, create=False)
        if not tp or not tp.get("pet"):
            return "目标没有宠物。"
        p["energy"] -= amount
        tpet = tp["pet"]
        tpet["energy"] = min(tpet["energy_max"], tpet["energy"] + amount)
        return f"🔋 已向 {target} 的宠物转移 {amount} 点精力。"

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

    def _attack(self, event, player: dict, group_id: str, tokens: list[str]) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if petmod.is_dead(p):
            return "你的宠物已死亡，请先复活。"
        if petmod.is_frozen(p):
            return f"宠物假死/惊魂中，约 {petmod.frozen_remain_min(p)} 分钟后才能战斗。"
        target = self._target_qq(event, tokens, 1)
        if not target:
            return "用法：宠物攻击 QQ号"
        if target == player["qq"]:
            return "不能攻击自己。"
        tp = self.store.get_player(target, create=False)
        if not tp or not tp.get("pet"):
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
            return "本群未开启宠物跨群功能。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        # 跨群挑战宠物 [群号 QQ]，或随机
        target_player = None
        if len(tokens) >= 3 and tokens[2].isdigit():
            target_player = self.store.get_player(tokens[2], create=False)
        if not target_player:
            candidates = [
                pl
                for q, pl in self.store.all_players().items()
                if pl.get("pet") and q != player["qq"] and not petmod.is_dead(pl["pet"])
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
        entries = []
        for q, pl in self.store.all_players().items():
            pet = pl.get("pet")
            if not pet:
                continue
            # 本群排行只统计最近在本群游玩的玩家
            if local and str(pl.get("group", "")) != str(group_id):
                continue
            entries.append((q, pet, petmod.battle_power(pet)))
        entries.sort(key=lambda x: x[2], reverse=True)
        entries = entries[: self.rank_size]
        if not entries:
            return "暂无宠物上榜。"
        title = "🏆 宠物排行（本群口径）" if local else "🏅 宠物神榜（全服）"
        lines = [title, "=" * 16]
        for i, (q, pet, bp) in enumerate(entries, 1):
            lines.append(
                f"{i}. {pet['nickname']}（{pet['quality']}/{pet['stage']}）战力 {bp} —— {q}"
            )
        return "\n".join(lines)

    def _claim_rank_reward(self, player: dict, group_id: str) -> str:
        entries = []
        for q, pl in self.store.all_players().items():
            if pl.get("pet"):
                entries.append((q, petmod.battle_power(pl["pet"])))
        entries.sort(key=lambda x: x[1], reverse=True)
        top = [q for q, _ in entries[:3]]
        if player["qq"] not in top:
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
        lines = ["🏰 宠物副本（发送：进入副本 副本名称）", "=" * 16]
        for n, d in data.DUNGEONS.items():
            lines.append(
                f"{n}：需 Lv{d['level_req']}，耗 {d['energy']} 精力，产出 经验{d['exp']}/积分{d['jifen']}"
            )
        return "\n".join(lines)

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
        petmod.refresh_energy(p)
        if p["energy"] < d["energy"]:
            return f"精力不足（需 {d['energy']}）。"
        p["energy"] -= d["energy"]
        petmod.add_exp(p, d["exp"])
        self.store.add_currency(player, "积分", d["jifen"])
        bonus = ""
        if random.random() < 0.2:
            self.store.add_item(player, "万能宝石", 1)
            bonus = "，并掉落『万能宝石』x1"
        return f"🏰 通关副本『{name}』！经验 +{d['exp']}，积分 +{d['jifen']}{bonus}。"

    def _quest_list(self) -> str:
        lines = [
            "📜 可领取剧情任务（发送：领取任务 任务名 / 提交任务 任务名）",
            "=" * 16,
        ]
        for n, q in data.QUESTS.items():
            need = "、".join(f"{k}x{v}" for k, v in q["need"].items())
            rwd = "、".join(f"{k}{v}" for k, v in q["reward"].items())
            lines.append(f"{n}：目标 {need} → 奖励 {rwd}")
        return "\n".join(lines)

    def _my_quests(self, player: dict) -> str:
        qs = player.get("quests", {})
        if not qs:
            return "你还没有领取剧情任务，发送『宠物剧情任务』查看。"
        lines = ["📜 我的剧情任务"]
        stats = player.get("stats", {})
        for n, base in qs.items():
            need = data.QUESTS.get(n, {}).get("need", {})
            base = base if isinstance(base, dict) else {}
            prog = "、".join(
                f"{k} {max(0, stats.get(k, 0) - base.get(k, 0))}/{v}"
                for k, v in need.items()
            )
            lines.append(f"{n}：{prog}")
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
    def _handle_love(self, event, player, group_id, cmd, tokens) -> str | None:
        if cmd == "宠物恋情":
            return self._love_status(player)
        if cmd == "宠物分手":
            return self._breakup(player)
        if cmd == "宠物离婚":
            return self._divorce(player)
        if cmd in ("宠物追求", "同意追求", "宠物求婚", "同意求婚"):
            target = self._target_qq(event, tokens, 1)
            if not target:
                return f"用法：{cmd} QQ号"
            return self._love_action(player, target, cmd)
        return None

    def _love_status(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if p["love_state"] == "单身":
            return f"『{p['nickname']}』当前单身。"
        return f"『{p['nickname']}』{p['love_state']}中，伴侣 {p['love_target']}，好感度 {p['favor']}。"

    def _love_action(self, player: dict, target: str, cmd: str) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if target == player["qq"]:
            return "不能对自己的宠物执行该操作。"
        tp = self.store.get_player(target, create=False)
        if not tp or not tp.get("pet"):
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
            proposer = self.store.get_player(target)
            if not self.store.remove_item(proposer, "永恒钻戒"):
                return "对方没有『永恒钻戒』，求婚失效。"
            p["love_state"] = tpet["love_state"] = "已婚"
            player.get("pending", {}).pop("marry", None)
            return "🎉 喜结连理！双方宠物已婚，约会获得双倍好感度。"
        return "未知姻缘操作。"

    def _breakup(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if p["love_state"] != "恋爱":
            return "当前不在恋爱状态。"
        partner = p.get("love_target")
        self._reset_love(p)
        if partner:
            tp = self.store.get_player(partner, create=False)
            if tp and tp.get("pet"):
                self._reset_love(tp["pet"])
        return "💔 已分手。"

    def _divorce(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if p["love_state"] != "已婚":
            return "当前未婚。"
        partner = p.get("love_target")
        self._reset_love(p)
        if partner:
            tp = self.store.get_player(partner, create=False)
            if tp and tp.get("pet"):
                self._reset_love(tp["pet"])
        return "🕊 已离婚，缘尽于此。"

    @staticmethod
    def _reset_love(pet: dict) -> None:
        pet["love_state"] = "单身"
        pet["love_target"] = None
        pet["favor"] = 0
