"""宠物乐园 —— AstrBot 群聊养成 / 对战插件。

参考某 QQ 群"宠物联盟"玩法复刻：砸蛋抽宠、宠物商城、属性克制对战、繁殖姻缘、
进化飞升渡劫、天赋觉醒、炼丹、神器/秘技、副本、剧情任务、跨群挑战、排行神榜等。

指令均为无前缀中文指令（与参考一致），通过监听全部消息后自行解析路由。
"""

from __future__ import annotations

import asyncio
import random
import re
import time
import urllib.parse
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from astrbot.api import AstrBotConfig, logger
from astrbot.api import message_components as Comp
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register

from .petpark import data, images, pet as petmod
from .petpark.ai_router import AIRouter
from .petpark.store import PetStore

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:  # pragma: no cover - 兼容旧版本

    def get_astrbot_data_path() -> str:
        return "data"


PLUGIN_NAME = "astrbot_plugin_petpark"

# 统一进程时区为北京时间（Asia/Shanghai），使所有 time.localtime() 显示正确。
# 仅 Unix 支持 tzset；服务器为 Linux，本地开发（Windows）忽略即可。
import os as _os
_os.environ.setdefault("TZ", "Asia/Shanghai")
try:
    time.tzset()
except AttributeError:
    pass  # Windows 无 tzset，开发环境不强制

# 扫雷紧凑指令：扫a1b2 / 插旗a1 / 旗a1 / 开始扫雷2
_MS_COMPACT_RE = re.compile(r"^(扫|插旗|旗)((?:[a-zA-Z]\d{1,2})+)$")
_MS_START_RE = re.compile(r"^开始扫雷([1-4])$")
_MS_COORD_RE = re.compile(r"[a-zA-Z]\d{1,2}")

# QQ 官方群消息中 @成员 的文本形式：<@openid> 或 <@!openid>
_MENTION_RE = re.compile(r"<@!?([0-9A-Za-z_\-]+)>")

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
    "管理菜单",
    "我的信息",
    "个人信息",
    "绑定QQ",
    "验证码",
    "换绑QQ",
    "解绑QQ",
    "签到",
    "宗门签到",
    "宗门介绍",
    "宗门信息",
    "宗门名单",
    "宗门报名",
    "宗门战况",
    "宗门对阵",
    "宗门赛况",
    "宗门战报",
    "宗门历史",
    "宗门倒计时",
    "宗门排行",
    "宗门商店",
    "宗门升级",
    "宗门兑换",
    "宗门确认",
    "宗门踢出",
    "宗门公告",
    "宗门改名",
    "加油",
    "宗门任命副宗主",
    "宗门撤销副宗主",
    "宗门重选宗主",
    "宗门选举",
    "兑换",
    "卡密兑换",
    "修炼卡",
    "我要氪金",
    "查看说明",
    # 群授权
    "授权",
    "授权状态",
    "授权本群",
    # 群管理（禁言 / 全体禁言，仅群主/管理员或插件管理员可用）
    "禁言",
    "解除禁言",
    "全体禁言",
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
    "锁定宠物",
    "解锁宠物",
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
    "经验换仙元",
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
    # 自动修炼
    "自动修炼",
    "开启自动修炼",
    "关闭自动修炼",
    "自动修炼状态",
    "修炼状态",
    # 对战 / 排行
    "宠物攻击",
    "跨群挑战宠物",
    "宠物排行",
    "宠物神榜",
    "领取神榜奖励",
    # 副本 / 剧情
    "宠物副本",
    "进入副本",
    "飞升副本",
    "挑战神仙",
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
    # 摸金
    "摸金",
    "摸金介绍",
    "摸",
    "摸金商店",
    "摸店",
    "购买摸金道具",
    "摸买",
    "我的摸金",
    "摸我",
    "进入摸金",
    "摸进",
    "摸金移动",
    "上",
    "下",
    "左",
    "右",
    "1",
    "2",
    "3",
    "摸金探索",
    "摸看",
    "摸金开箱",
    "开箱",
    "摸金使用",
    "摸用",
    "摸装",
    "摸带",
    "摸存",
    "摸包",
    "摸金撤离",
    "摸撤",
    "摸金状态",
    "摸态",
    "放弃摸金",
    "摸弃",
    "战斗",
    "祭拜",
    "逃跑",
    "跳过",
    # 摸金排行 / 神榜
    "摸金排行",
    "摸排",
    "今日摸金神榜",
    "摸金神榜",
    "摸榜",
    "昨日摸金神榜",
    "领取摸金奖励",
    "摸金领奖",
    "摸领",
    # 摸金经验兑换
    "摸金兑换",
    "摸兑",
    # 摸金双排
    "摸金组队",
    "摸金准备",
    "摸金取消组队",
    "摸金队伍",
    "摸金救援",
    "摸金捡取",
    "摸金传送",
    # 扫雷
    "扫雷",
    "扫雷介绍",
    "扫雷帮助",
    "扫雷游戏",
    "开始扫雷",
    "扫",
    "插旗",
    "旗",
    "扫雷地图",
    "扫雷状态",
    "放弃扫雷",
    "扫雷排行",
    "扫雷兑换",
    # 银行
    "宠物银行",
    "银行信息",
    "银行存款",
    "银行取款",
    "银行贷款",
    "银行还款",
    # 重生
    "重生",
    "购买重生宝石",
    "确认重生",
    "祭奠",
    # 多宠物
    "切换宠物",
    "宠物列表",
    "查看所有宠物",
    "宠物信息",
}

# 网页端宠物对话不支持的指令：不可逆操作、获取/转移宠物与资产、群管理/授权类
WEB_BLOCKED_COMMANDS = {
    # 不可逆 / 资产转移
    "放生宠物",
    "赠送宠物",
    "宠物变性",
    "清空背包",
    "丢弃",
    "转让",
    "赠送金币",
    "赠送积分",
    "赠送钻石",
    # 获取宠物（应在群内进行）
    "砸蛋",
    "购买宠物",
    # 群管理 / 授权 / 广播类
    "开启宠物乐园",
    "关闭宠物乐园",
    "开启宠物跨群",
    "关闭宠物跨群",
    "加金币",
    "减金币",
    "加积分",
    "减积分",
    "加钻石",
    "减钻石",
    "任命小管理",
    "任命小管理员",
    "撤销小管理",
    "撤销小管理员",
    "小管理列表",
    "我的管理额度",
    "授权",
    "授权本群",
    # 宠物家园
    "家园",
    "家园介绍",
    "家园教程",
    "建造",
    "升级",
    "家园升级",
    "家园收取",
    "家园建筑",
    "拜访家园",
    "家园拜访",
    "派遣",
    "召回",
    "派遣状态",
    "顺手牵羊",
    "偷菜",
    "家园排行",
    "家园总排行",
    "商人购买",
    "拆除",
    # 银行
    "银行信息",
    "银行存款",
    "银行取款",
    "银行贷款",
    "银行还款",
    # 重生（不可逆操作）
    "重生",
    "购买重生宝石",
    "确认重生",
    "祭奠",
}


class _WebEvent:
    """网页端宠物对话的伪事件：仅提供 dispatch 所需的最小接口。"""

    role = ""

    def __init__(self, qq: str):
        self._qq = qq

    def get_sender_id(self) -> str:
        return self._qq

    def get_group_id(self) -> str:
        return ""

    def get_sender_name(self) -> str:
        return ""


@register(
    PLUGIN_NAME,
    "Devin",
    "宠物乐园：群聊宠物养成与对战玩法（砸蛋/商城/对战/进化/姻缘/天赋/炼丹/副本）。",
    "1.0.0",
    "https://github.com/skyesda/qqbot_pet",
)
class PetParkPlugin(Star):
    # 类级引用，用于插件重载时取消旧的后台任务，防止多实例并行运行
    _auto_cultivation_task_ref: Any = None
    _sect_war_task_ref: Any = None
    _sect_daily_reset_task_ref: Any = None
    _bank_interest_task_ref: Any = None
    _group_auto_approve_task_ref: Any = None
    _sect_war_lock = asyncio.Lock()  # 宗门战操作用锁，防止并发重入

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
        # 群管理（禁言/自动审批进群/新人推送/退群推送）——全部由插件实现
        self.mute_enabled = bool(self.config.get("mute_enabled", True))
        self.auto_approve = bool(self.config.get("auto_approve", True))
        self.welcome_push = bool(self.config.get("welcome_push", True))
        self.leave_push = bool(self.config.get("leave_push", True))
        self.welcome_template = str(self.config.get("welcome_template", "") or "") or (
            "## 👋 欢迎新成员\n欢迎 @{{member}} 加入本群！"
        )
        self.leave_template = str(self.config.get("leave_template", "") or "") or (
            "## 👋 成员退群\n成员 @{{member}} 已离开本群。"
        )
        # 群昵称缓存：{group_id: {member_openid: 群昵称}}
        self._nick_cache: dict[str, dict[str, str]] = {}
        # 专属管理网站（卡密生成 + 数据增删改查）
        self._web = None
        # 全服广播任务引用，防止被 GC
        self._broadcast_tasks: set = set()
        # 宠物摸金当局运行时状态（持久化到 store，插件重载后自动恢复）
        self._tomb_sessions: dict[str, dict] = self.store.load_tomb_sessions()
        # 扫雷当局运行时状态（内存中，不持久化，按 QQ 一人一局）
        self._ms_sessions: dict[str, dict] = {}
        # 群消息滚动缓存：群ID\x1f发送者 → deque[(message_id, 时间戳)]，供撤回指令用
        self._group_msg_log: dict[str, deque] = {}
        # 摸金双排组队状态（持久化到 store，插件重载后自动恢复）
        self._tomb_coop_teams, self._tomb_coop_index = self.store.load_tomb_coops()
        # QQ 绑定待验证码（内存中，platform_id -> {code, qq, expires_at, sent_at}）
        self._pending_qq_bind: dict[str, dict] = {}
        # AI 意图路由：自然语言 → 标准指令（使用 AstrBot 当前启用的 LLM Provider）
        self._ai_router = AIRouter(
            context,
            enabled=bool(self.config.get("ai_router_enabled", True)),
            timeout=float(self.config.get("ai_router_timeout", 20)),
            provider_id=str(self.config.get("ai_router_provider_id", "")),
        )
        if bool(self.config.get("web_enabled", True)):
            self._start_web_admin()
        self._patch_qqofficial_message_extensions()
        # 启动后台自动修炼循环；重载插件时先取消旧任务，避免并发
        try:
            if (
                PetParkPlugin._auto_cultivation_task_ref
                and not PetParkPlugin._auto_cultivation_task_ref.done()
            ):
                PetParkPlugin._auto_cultivation_task_ref.cancel()
        except Exception:
            pass
        PetParkPlugin._auto_cultivation_task_ref = asyncio.create_task(
            self._auto_cultivation_loop()
        )
        # 启动宗门战后台任务；重载插件时先取消旧任务
        try:
            if (
                PetParkPlugin._sect_war_task_ref
                and not PetParkPlugin._sect_war_task_ref.done()
            ):
                PetParkPlugin._sect_war_task_ref.cancel()
        except Exception:
            pass
        PetParkPlugin._sect_war_task_ref = asyncio.create_task(
            self._background_sect_war()
        )
        try:
            if (
                PetParkPlugin._sect_daily_reset_task_ref
                and not PetParkPlugin._sect_daily_reset_task_ref.done()
            ):
                PetParkPlugin._sect_daily_reset_task_ref.cancel()
        except Exception:
            pass
        PetParkPlugin._sect_daily_reset_task_ref = asyncio.create_task(
            self._background_sect_daily_reset()
        )
        try:
            if (
                PetParkPlugin._sect_forced_refresh_task_ref
                and not PetParkPlugin._sect_forced_refresh_task_ref.done()
            ):
                PetParkPlugin._sect_forced_refresh_task_ref.cancel()
        except Exception:
            pass
        PetParkPlugin._sect_forced_refresh_task_ref = asyncio.create_task(
            self._background_sect_forced_refresh()
        )
        # 银行周利息后台任务
        try:
            if (
                PetParkPlugin._bank_interest_task_ref
                and not PetParkPlugin._bank_interest_task_ref.done()
            ):
                PetParkPlugin._bank_interest_task_ref.cancel()
        except Exception:
            pass
        PetParkPlugin._bank_interest_task_ref = asyncio.create_task(
            self._bank_interest_loop()
        )
        # 群自动审批后台任务（每 30 秒拉取并审批入群申请）
        try:
            if (
                PetParkPlugin._group_auto_approve_task_ref
                and not PetParkPlugin._group_auto_approve_task_ref.done()
            ):
                PetParkPlugin._group_auto_approve_task_ref.cancel()
        except Exception:
            pass
        PetParkPlugin._group_auto_approve_task_ref = asyncio.create_task(
            self._group_auto_approve_loop()
        )

    # 类级别后台任务引用，避免重载后并发
    _sect_war_task_ref = None
    _sect_daily_reset_task_ref = None
    _sect_forced_refresh_task_ref = None
    _bank_interest_task_ref = None
    _sect_forced_refresh_task_ref = None

    # =====================================================================
    # 银行周利息后台循环
    # =====================================================================
    BANK_INTEREST_CHECK_SEC = 120  # 每 2 分钟检查一次

    async def _bank_interest_loop(self) -> None:
        """后台循环：每周一 00:00（北京时间）自动计算所有银行账户的周利息。"""
        while True:
            try:
                self._bank_interest_tick()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[petpark] 银行利息循环异常")
            try:
                await asyncio.sleep(self.BANK_INTEREST_CHECK_SEC)
            except asyncio.CancelledError:
                break

    def _bank_interest_tick(self) -> None:
        """单次利息检查：如果是周一且本周尚未计息，则为所有账户计息并保存。"""
        now = self._bj_localtime()
        weekday = now.tm_wday  # 0=周一
        if weekday != 0:
            return
        week_key = time.strftime("%Y-W%W")
        bank_players = self.store._data.get("bank_players", {})
        any_changed = False
        for qq, bk in list(bank_players.items()):
            if not isinstance(bk, dict):
                continue
            if bk.get("last_interest_week") == week_key:
                continue
            # 存款利息
            dep_coin = bk.get("deposit_coin", 0)
            dep_jifen = bk.get("deposit_jifen", 0)
            if dep_coin > 0:
                interest = max(1, int(dep_coin * data.BANK_INTEREST_WEEKLY))
                bk["deposit_coin"] += interest
                bk["total_interest_earned"] = bk.get("total_interest_earned", 0) + interest
            if dep_jifen > 0:
                interest = max(1, int(dep_jifen * data.BANK_INTEREST_WEEKLY))
                bk["deposit_jifen"] += interest
                bk["total_interest_earned"] = bk.get("total_interest_earned", 0) + interest
            # 贷款利息（复利）
            loan_coin = bk.get("loan_coin", 0)
            loan_jifen = bk.get("loan_jifen", 0)
            if loan_coin > 0:
                interest = max(1, int(loan_coin * data.BANK_INTEREST_WEEKLY))
                bk["loan_coin"] += interest
                bk["total_interest_paid"] = bk.get("total_interest_paid", 0) + interest
            if loan_jifen > 0:
                interest = max(1, int(loan_jifen * data.BANK_INTEREST_WEEKLY))
                bk["loan_jifen"] += interest
                bk["total_interest_paid"] = bk.get("total_interest_paid", 0) + interest
            bk["last_interest_week"] = week_key
            any_changed = True
        if any_changed:
            async def _save():
                await self.store.save()
            asyncio.ensure_future(_save())

    async def _background_sect_forced_refresh(self) -> None:
        """每分钟刷新一次所有群的强制出战名单。"""
        while True:
            await asyncio.sleep(60)
            try:
                self._sect_refresh_forced_all()
            except Exception as e:
                logger.exception(f"[petpark] 宗门强制出战刷新异常：{e}")

    async def _background_sect_daily_reset(self) -> None:
        """每天 00:00 执行宗门每日重置：赛季初始化、重选宗主、计算强制出战。"""
        while True:
            await asyncio.sleep(60)
            now = self._bj_localtime()
            if now.tm_hour == 0 and now.tm_min == 0:
                try:
                    self._sect_ensure_season()
                    for gid in list(self.store._data.get("groups", {}).keys()):
                        self._sect_reset_daily(gid)
                    await self.store.save()
                except Exception as e:
                    logger.exception(f"[petpark] 宗门每日重置异常：{e}")
                await asyncio.sleep(120)

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
            command_gateway=self,
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
    # 自动修炼挂机
    # =====================================================================
    AUTO_CULTIVATION_INTERVAL = 30  # 秒

    async def _auto_cultivation_loop(self) -> None:
        """后台循环：定期为开启自动挂机的定制宠物执行修炼/双修。"""
        while True:
            try:
                await self._auto_cultivation_tick()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[petpark] 自动修炼循环异常")
            try:
                await asyncio.sleep(self.AUTO_CULTIVATION_INTERVAL)
            except asyncio.CancelledError:
                break

    async def _auto_cultivation_tick(self) -> None:
        """单次扫描并执行所有符合条件的自动修炼/幻境寻宝（每个宠物独立修炼）。"""
        now = int(time.time())
        any_changed = False
        for key, player in list(self.store.all_players().items()):
            pets = player.get("pets", [])
            if not pets:
                continue
            for i, p in enumerate(pets):
                if not p:
                    continue
                # 每只宠物独立的自动修炼状态
                ac = p.get("auto_cultivation")
                if not ac or not ac.get("enabled"):
                    continue
                if not self.store.auto_cultivation_active(player, p):
                    # 权限失效时自动关闭该宠物的挂机
                    ac["enabled"] = False
                    any_changed = True
                    continue
                # 宠物异常时跳过，等恢复后再继续
                if self._busy_reason(p):
                    continue
                if self._pet_is_ascended(p):
                    # 飞升后自动切换为幻境寻宝
                    if self.store.cooldown_remaining(player, "fantasy_treasure") > 0:
                        continue
                    petmod.refresh_energy(p)
                    energy_cost = data.ASCEND_TREASURE.get("energy", 60)
                    if p["energy"] < energy_cost:
                        continue
                    p["energy"] -= energy_cost
                    self.store.set_cooldown(
                        player, "fantasy_treasure", random.randint(*data.ASCEND_TREASURE["cooldown"])
                    )
                    xianyuan = random.randint(*data.ascend_treasure_xianyuan(p["level"]))
                    petmod.add_xianyuan(p, xianyuan)
                    if random.random() < data.ASCEND_TREASURE.get("jifen_chance", 0.5):
                        jifen = random.randint(*data.ASCEND_TREASURE.get("jifen", (500, 3000)))
                        self.store.add_currency(player, "积分", jifen)
                    self._inc_stat(player, "ascended_fantasy_treasure")
                    ac["total_sessions"] = ac.get("total_sessions", 0) + 1
                    ac["total_exp"] = ac.get("total_exp", 0) + xianyuan
                    ac["last_run_at"] = now
                    any_changed = True
                else:
                    # 未飞升：优先双修，否则修炼
                    action = "双修" if p.get("love_state") == "已婚" else "修炼"
                    if self.store.cooldown_remaining(player, f"日常:{action}") > 0:
                        continue
                    petmod.refresh_energy(p)
                    conf = data.DAILY_ACTIONS[action]
                    if p["energy"] < conf["energy"]:
                        continue
                    # 执行修炼/双修
                    p["energy"] -= conf["energy"]
                    self.store.set_cooldown(
                        player, f"日常:{action}", random.randint(*data.DAILY_COOLDOWN_RANGE)
                    )
                    base = random.randint(50, 120) + p["level"] * 15
                    exp = base * (2 if action == "双修" else 1)
                    petmod.add_exp(p, exp)
                    if action == "双修":
                        self._inc_stat(player, "shuangxiu")
                    # 自动升级（不发送消息，静默处理）
                    petmod.auto_level_up(p)
                    # 更新统计
                    ac["total_sessions"] = ac.get("total_sessions", 0) + 1
                    ac["total_exp"] = ac.get("total_exp", 0) + exp
                    ac["last_run_at"] = now
                any_changed = True
        if any_changed:
            await self.store.save()

    def _auto_cultivation_toggle(self, player: dict, enable: bool) -> str:
        """开启或关闭当前宠物的自动修炼（每只宠物独立修炼状态）。"""
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物，发送『砸蛋』获取一只。"
        if not self.store.auto_cultivation_active(player, p):
            return (
                "你的宠物尚未获得自动修炼权限。\n"
                "定制宠物永久享有该权限；非定制宠物请使用『修炼卡 卡密』激活。"
            )
        ac = p.setdefault("auto_cultivation", {
            "enabled": False,
            "started_at": 0,
            "total_sessions": 0,
            "total_exp": 0,
            "last_run_at": 0,
        })
        ascended = self._pet_is_ascended(p)
        if enable:
            if ac.get("enabled"):
                return "你的宠物已经在自动修炼中，发送『自动修炼状态』查看进度。"
            ac["enabled"] = True
            ac["started_at"] = int(time.time())
            if ascended:
                return (
                    f"✅ 已开启『{p['nickname']}』的自动幻境寻宝！\n"
                    f"后台会自动在满足条件时探索幻境获取仙元，精力/冷却不足时自动等待。\n"
                    f"发送『关闭自动修炼』停止，发送『自动修炼状态』查看进度。"
                )
            return (
                f"✅ 已开启『{p['nickname']}』的自动修炼！\n"
                f"后台会自动在满足条件时进行修炼，已婚优先双修，精力/冷却不足时自动等待。\n"
                f"发送『关闭自动修炼』停止，发送『自动修炼状态』查看进度。"
            )
        else:
            if not ac.get("enabled"):
                return "你的宠物当前没有开启自动修炼。"
            ac["enabled"] = False
            unit = "仙元" if ascended else "经验"
            return f"⏹ 已关闭『{p['nickname']}』的自动修炼。累计挂机 {ac.get('total_sessions', 0)} 次，共获得 {ac.get('total_exp', 0)} {unit}。"

    def _auto_cultivation_status(self, player: dict) -> str:
        """查看当前宠物自动修炼状态（每只宠物独立）。"""
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物。"
        ac = p.get("auto_cultivation", {})
        if not ac:
            return "该宠物尚未开启过自动修炼。"
        status = "🟢 运行中" if ac.get("enabled") else "🔴 已停止"
        started = ac.get("started_at", 0)
        started_txt = time.strftime("%Y/%m/%d %H:%M:%S", time.localtime(started)) if started else "—"
        last = ac.get("last_run_at", 0)
        last_txt = time.strftime("%Y/%m/%d %H:%M:%S", time.localtime(last)) if last else "—"
        ascended = self._pet_is_ascended(p)
        if ascended:
            mode = "自动幻境寻宝（仙元）"
            cd_key = "fantasy_treasure"
            stat_label = "累计仙元"
        else:
            action = "双修" if p.get("love_state") == "已婚" else "修炼"
            mode = f"优先 {action}"
            cd_key = f"日常:{action}"
            stat_label = "累计经验"
        remain_cd = self.store.cooldown_remaining(player, cd_key)
        cd_txt = self._fmt_duration(remain_cd) if remain_cd > 0 else "已就绪"
        petmod.refresh_energy(p)
        if p.get("custom"):
            perm_txt = "永久（定制宠物）"
        else:
            pac = player.get("auto_cultivation", {})
            until = int(pac.get("card_until", 0) or 0)
            if until > int(time.time()):
                perm_txt = self._fmt_remain(until)
            else:
                perm_txt = "已到期"
        return (
            f"## 🧘 自动修炼状态\n"
            f"状态：{status}\n"
            f"宠物：{p['nickname']}\n"
            f"权限：{perm_txt}\n"
            f"模式：{mode}\n"
            f"精力：{p['energy']}/{p['energy_max']}\n"
            f"下次可行动：{cd_txt}\n"
            f"累计挂机：{ac.get('total_sessions', 0)} 次\n"
            f"{stat_label}：{ac.get('total_exp', 0)}\n"
            f"开启时间：{started_txt}\n"
            f"最后执行：{last_txt}"
        )

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
                [("💎 我要氪金", "我要氪金")],
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

    def _ms_menu_keyboard(self) -> dict:
        return self._build_qq_keyboard(
            [
                [("🧨 开始扫雷", "开始扫雷"), ("🗺 扫雷地图", "扫雷地图")],
                [("🏆 扫雷排行", "扫雷排行"), ("🎁 扫雷兑换", "扫雷兑换")],
                [("📖 扫雷介绍", "扫雷介绍"), ("🏳 放弃扫雷", "放弃扫雷")],
            ]
        )

    def _keyboard_for_cmd(self, text: str) -> dict | None:
        """根据用户发送的指令决定要不要附带快捷按钮。"""
        if text in {"宠物菜单", "宠物指令", "宠物帮助", "管理菜单"}:
            return self._main_menu_keyboard()
        if text in {"扫雷", "扫雷介绍", "扫雷帮助", "扫雷游戏", "扫雷地图", "扫雷状态"} or text.startswith("开始扫雷"):
            return self._ms_menu_keyboard()
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
            mid = str(
                getattr(getattr(event, "message_obj", None), "message_id", "") or ""
            )
            if mid:
                self._group_msg_log.setdefault(
                    f"{group_id}\x1f{qq}", deque(maxlen=30)
                ).append((mid, time.time()))
        try:
            if re.match(r"^(撤回消息|撤回)(\s|<@|$)", text):
                reply = await self._cmd_recall_member(event, qq, group_id, text)
            elif re.match(r"^(禁言|解除禁言|全体禁言)(\s|<@|$)", text):
                reply = await self._cmd_mute(event, qq, group_id, text)
            else:
                reply = self.dispatch(event, qq, group_id, text)
        except Exception as e:  # 保证插件不因单条消息崩溃
            logger.exception("[petpark] 处理指令出错")
            reply = f"宠物乐园处理出错：{e}"
        # AI 意图路由兜底：精确指令未命中时，尝试把自然语言翻译为标准指令
        effective_text = text
        if reply is None and text != "加油":
            routed = None
            try:
                allowed = (
                    KNOWN_COMMANDS
                    | set(data.DAILY_ACTIONS)
                    | self._active_event_commands()
                )
                routed = await self._ai_router.route(text, allowed)
            except Exception:
                logger.exception("[petpark] AI 意图路由出错")
            if routed and routed != text:
                effective_text = routed
                try:
                    reply = self.dispatch(event, qq, group_id, routed)
                except Exception as e:
                    logger.exception("[petpark] AI 路由指令执行出错")
                    reply = f"宠物乐园处理出错：{e}"
                if isinstance(reply, str):
                    reply = f"🤖 已识别：{routed}\n{reply}"
                elif isinstance(reply, tuple):
                    reply = (f"🤖 已识别：{routed}\n{reply[0]}", reply[1])
        # 部分指令返回 (文本, Markdown图片串) 二元组，把图片以 Markdown 语法内嵌到文本最前，
        # 与宠物图片发送方式保持一致（![alt #width #height](url)）。
        image_md = None
        if isinstance(reply, tuple):
            reply, image_md = reply
        if reply is None:
            return
        await self.store.save()
        event.stop_event()
        if image_md:
            reply = f"{image_md}\n{reply}"
        # 在合适的地方附加 QQ 官方消息按钮，方便用户快捷发送指令
        keyboard = self._keyboard_for_cmd(effective_text)
        # 群聊里 @ 触发者，便于多人同时游玩时分辨各自的消息；私聊不 @。
        if self._is_group(group_id):
            # QQ 官方机器人(qq_official)适配器会忽略 At 组件，故同时以纯文本
            # 形式前置 @昵称，确保任何平台都能看出这条消息@的是谁。
            # 群昵称优先级：消息事件自带的 author.username（QQ 群消息 payload 直接给）
            # -> 成员详情接口 -> 绑定的QQ号 -> openid
            name = self._sender_name(event)
            if not name or name == qq:
                nick = await self._member_nick(group_id, qq)
                name = nick or self.store.get_bound_qq(qq) or qq
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

    # =====================================================================
    # 网页端宠物对话入口（由玩家中心调用）
    # =====================================================================
    WEB_BLOCKED_TIP = "🚫 该指令仅支持在 QQ 群内使用哦，请回群里发送。"

    async def web_dispatch(self, qq: str, group_id: str, text: str):
        """以绑定的用户/群身份执行一条指令，返回与 dispatch 相同的结果。

        屏蔽不适合网页端的指令（放生/砸蛋/赠送/管理与授权类等），
        未命中精确指令时走 AI 意图路由（同样排除被屏蔽指令）。
        """
        text = (text or "").strip()
        if not text:
            return None
        tokens = text.split()
        if tokens[0] in WEB_BLOCKED_COMMANDS or text in WEB_BLOCKED_COMMANDS:
            return self.WEB_BLOCKED_TIP
        event = _WebEvent(qq)
        try:
            reply = self.dispatch(event, qq, group_id, text)
        except Exception as e:
            logger.exception("[petpark] 网页端指令执行出错")
            return f"宠物乐园处理出错：{e}"
        if reply is None:
            routed = None
            try:
                allowed = (
                    KNOWN_COMMANDS
                    | set(data.DAILY_ACTIONS)
                    | self._active_event_commands()
                ) - WEB_BLOCKED_COMMANDS
                routed = await self._ai_router.route(text, allowed)
            except Exception:
                logger.exception("[petpark] 网页端 AI 意图路由出错")
            if routed and routed != text:
                if routed.split()[0] in WEB_BLOCKED_COMMANDS:
                    return self.WEB_BLOCKED_TIP
                try:
                    reply = self.dispatch(event, qq, group_id, routed)
                except Exception as e:
                    logger.exception("[petpark] 网页端 AI 路由指令执行出错")
                    reply = f"宠物乐园处理出错：{e}"
                if isinstance(reply, str):
                    reply = f"🤖 已识别：{routed}\n{reply}"
                elif isinstance(reply, tuple):
                    reply = (f"🤖 已识别：{routed}\n{reply[0]}", reply[1])
        if reply is not None:
            await self.store.save()
        return reply

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
        """按用户ID查本群玩家（数据按群隔离）。返回 (player, 错误提示)。
        qq 可为平台用户ID或已绑定的QQ号（自动解析）。"""
        if not qq:
            return None, None
        qq = self._resolve_user_token(qq)
        tp = self.store.get_player(qq, group_id, create=False)
        if not tp:
            return None, f"❌ 用户 `{qq}` 在本群不存在（对方需先在本群参与宠物乐园）。"
        return tp, None

    def _resolve_user_token(self, token: str) -> str:
        """把用户输入的标识解析为平台用户ID：若为已绑定的QQ号则返回对应平台ID，否则原样返回。"""
        if not token:
            return token
        pid = self.store.find_platform_id_by_qq(token)
        return pid if pid else token

    @staticmethod
    def _track_activity(player: dict) -> None:
        """记录玩家每日活跃度，用于转让免税判定。连续 7 天活跃即享免税。"""
        today = time.strftime("%Y-%m-%d")
        last = player.get("last_active_date", "")
        if last == today:
            return  # 今天已记录
        yesterday = time.strftime("%Y-%m-%d", time.localtime(int(time.time()) - 86400))
        streak = player.get("active_streak", 0)
        if last == yesterday:
            streak += 1
        else:
            streak = 1  # 断签，重置
        player["active_streak"] = streak
        player["last_active_date"] = today

    @staticmethod
    def _check_transfer_limit(
        sender: dict, target: dict, group_id: str, count: int,
        transfer_type: str = "item",
    ) -> tuple[str | None, float]:
        """检查转让/赠送限制，返回 (错误提示, 税率)。

        transfer_type: "coin" / "jifen" / "diamond" / "item" / "pet"
        规则：
        1. 每日所有转让合计 ≤ TRANSFER_DAILY_MAX_OPS 次
        2. 货币类单次 ≤ TRANSFER_PER_TX_MAX
        3. 道具类单次 ≤ 10 个
        4. 货币税 20%，道具税 10%
        5. 7天内向同一人转让 ≥ TRANSFER_WEEKLY_SAME_LIMIT 次 → 双倍税
        """
        qq1 = str(sender.get("qq", ""))
        qq2 = str(target.get("qq", ""))
        if qq1 == qq2:
            return "不能转让/赠送给自己。", 0.0

        # ---- 每日总次数检查 ----
        today = time.strftime("%Y-%m-%d")
        sender.setdefault("_tx_daily", {})
        if sender["_tx_daily"].get("date") != today:
            sender["_tx_daily"] = {"date": today, "count": 0}
        if sender["_tx_daily"]["count"] >= data.TRANSFER_DAILY_MAX_OPS:
            return (
                f"今日转让/赠送次数已达上限（{data.TRANSFER_DAILY_MAX_OPS}次/天），明天再来。",
                0.0,
            )

        # ---- 单次数量检查 ----
        if transfer_type in ("coin", "jifen", "diamond"):
            if count > data.TRANSFER_PER_TX_MAX:
                return (
                    f"单次{transfer_type}转让不能超过 {data.TRANSFER_PER_TX_MAX}。",
                    0.0,
                )
        elif transfer_type == "item" and count > 10:
            return "每次道具转让不能超过 10 个。", 0.0

        # ---- 税率计算 ----
        tax_map = {
            "coin": data.TRANSFER_TAX_COIN,
            "jifen": data.TRANSFER_TAX_JIFEN,
            "diamond": data.TRANSFER_TAX_DIAMOND,
            "item": data.TRANSFER_TAX_ITEM,
            "pet": 0.0,
        }
        base_tax = tax_map.get(transfer_type, data.TRANSFER_TAX_ITEM)

        # 活跃用户判定：最近 7 天每天都有游玩（使用任意指令）
        is_active = sender.get("active_streak", 0) >= 7

        # 检查 7 天内向同一人的转让次数
        now = int(time.time())
        week_ago = now - 7 * 86400
        sender.setdefault("_tx_history", {})
        target_key = f"{group_id}:{qq2}"
        history = sender["_tx_history"].setdefault(target_key, [])
        history[:] = [ts for ts in history if ts > week_ago]
        same_user_count = len(history)
        frequent_same = same_user_count >= data.TRANSFER_WEEKLY_SAME_LIMIT

        # 道具 ≤3 个免税（但高频同用户仍收税）
        if transfer_type == "item" and count <= 3 and not frequent_same:
            tax_rate = 0.0
        elif is_active:
            # 活跃用户免税，但高频同用户转让仍收基础税
            if frequent_same:
                tax_rate = base_tax
            else:
                tax_rate = 0.0
        else:
            # 非活跃用户：基础税，高频同用户双倍税
            if frequent_same:
                tax_rate = round(base_tax * data.TRANSFER_DOUBLE_TAX_MULT, 4)
            else:
                tax_rate = base_tax

        # ---- 更新计数 ----
        sender["_tx_daily"]["count"] += 1
        history.append(now)

        return None, tax_rate

    # =====================================================================
    # 宠物银行
    # =====================================================================
    def _bank_block_check(self, player: dict, action: str = "") -> str | None:
        """检查玩家是否被银行冻结或有未还贷款限制。返回 None 表示放行。
        逾期7天以上自动强制还款（从账户余额抵扣）。"""
        bk = self.store.bank_state(player)
        if not bk:
            return None
        # 逾期强制还款：从账户余额自动抵扣
        force_repaid = False
        for cur, key_due, key_loan in [
            ("金币", "loan_coin_due", "loan_coin"),
            ("积分", "loan_jifen_due", "loan_jifen"),
        ]:
            loan = bk.get(key_loan, 0)
            due_str = bk.get(key_due, "")
            if loan > 0 and due_str:
                try:
                    due_dt = time.strptime(due_str, "%Y-%m-%d")
                    due_ts = int(time.mktime(due_dt))
                    days_overdue = max(0, (int(time.time()) - due_ts) // 86400)
                except ValueError:
                    days_overdue = 0
                if days_overdue >= data.BANK_OVERDUE_FREEZE_DAYS:
                    # 强制还款：能扣多少扣多少
                    balance = self.store.get_currency(player, cur)
                    if balance > 0:
                        deduct = min(balance, loan)
                        self.store.add_currency(player, cur, -deduct)
                        bk[key_loan] -= deduct
                        bk["total_repaid"] = bk.get("total_repaid", 0) + deduct
                        force_repaid = True
                        # 如果还清了
                        if bk[key_loan] <= 0:
                            bk[key_loan] = 0
                            bk.pop(key_due, None)
                            key_cd = "loan_coin_repaid_at" if cur == "金币" else "loan_jifen_repaid_at"
                            bk[key_cd] = int(time.time())
                            # 逾期罚分
                            old_credit = bk.get("credit_score", data.BANK_CREDIT_INITIAL)
                            penalty = random.randint(*data.BANK_CREDIT_OVERDUE)
                            bk["credit_score"] = max(0, old_credit + penalty)
        # 重新检查是否还被冻结
        frozen_msgs = []
        for cur, key_due in [("金币", "loan_coin_due"), ("积分", "loan_jifen_due")]:
            key_loan = "loan_coin" if cur == "金币" else "loan_jifen"
            loan = bk.get(key_loan, 0)
            due_str = bk.get(key_due, "")
            if loan > 0 and due_str:
                try:
                    due_dt = time.strptime(due_str, "%Y-%m-%d")
                    due_ts = int(time.mktime(due_dt))
                    days_overdue = max(0, (int(time.time()) - due_ts) // 86400)
                    if days_overdue >= data.BANK_OVERDUE_FREEZE_DAYS:
                        frozen_msgs.append(
                            f"• {cur}贷款 {loan:,}（逾期 {days_overdue} 天）"
                        )
                except ValueError:
                    pass
        if frozen_msgs:
            msg = "🚫 **银行账户已被冻结！**\n" + "\n".join(frozen_msgs)
            if force_repaid:
                msg += "\n\n⚠️ 已自动从余额强制抵扣部分欠款，仍不足还清。"
            msg += (
                "\n\n请立即还款以解冻账户：\n"
                "`银行还款 金币 数量` 或 `银行还款 积分 数量`\n"
                "还清所有贷款后自动解冻。"
            )
            return msg
        if force_repaid:
            return None  # 强制还款后已解冻
        # 有未还贷款时，阻止转让/赠送操作
        if action in ("transfer", "gift"):
            debts = []
            if bk.get("loan_coin", 0) > 0:
                debts.append(f"金币贷款 {bk['loan_coin']:,}")
            if bk.get("loan_jifen", 0) > 0:
                debts.append(f"积分贷款 {bk['loan_jifen']:,}")
            if debts:
                return (
                    "🚫 **有未还贷款，无法进行转让/赠送操作！**\n"
                    + "、".join(debts)
                    + "\n请先还清贷款：`银行还款 金币/积分 数量`"
                )
        return None

    def _bank_info(self, player: dict) -> str:
        """查看银行账户信息。"""
        bk = self.store.bank_state(player)
        if not bk:
            return "银行系统暂不可用。"
        credit = bk.get("credit_score", data.BANK_CREDIT_INITIAL)
        limit = data.bank_loan_limit(credit)
        now_ts = int(time.time())
        # 计算逾期/剩余天数
        def _loan_status(key_loan, key_due, key_dur):
            loan = bk.get(key_loan, 0)
            due_str = bk.get(key_due, "")
            dur = bk.get(key_dur, data.BANK_LOAN_DEFAULT_DAYS)
            if loan > 0 and due_str:
                try:
                    due_dt = time.strptime(due_str, "%Y-%m-%d")
                    due_ts = int(time.mktime(due_dt))
                    days = (now_ts - due_ts) // 86400
                    return loan, due_str, dur, days
                except ValueError:
                    pass
            return loan, "", dur, 0
        lc, lc_due, lc_dur, lc_days = _loan_status("loan_coin", "loan_coin_due", "loan_coin_dur")
        lj, lj_due, lj_dur, lj_days = _loan_status("loan_jifen", "loan_jifen_due", "loan_jifen_dur")
        # 信用评级
        if credit >= 800:
            grade = "🌟 极好"
        elif credit >= 650:
            grade = "👍 良好"
        elif credit >= 500:
            grade = "👤 普通"
        elif credit >= 300:
            grade = "⚠️ 较差"
        else:
            grade = "💀 极差"
        lines = [
            "## 🏦 宠物银行",
            "━━━━━━━━━━━━━━",
        ]
        # 存款
        dep_coin = bk.get("deposit_coin", 0)
        dep_jifen = bk.get("deposit_jifen", 0)
        lines.append(f"💰 **存款**")
        if dep_coin > 0:
            lines.append(f"　金币：{dep_coin:,}")
        if dep_jifen > 0:
            lines.append(f"　积分：{dep_jifen:,}")
        if dep_coin == 0 and dep_jifen == 0:
            lines.append("　（无存款）")
        # 贷款
        lines.append(f"📋 **贷款**（额度：{limit:,}）")
        if lc > 0:
            if lc_days >= 0:
                od_str = f" ⚠️ 已逾期{lc_days}天"
            else:
                od_str = f" ⏳ 还剩{-lc_days}天"
            lines.append(f"　金币：{lc:,} | {lc_dur}天期 | 到期 {lc_due}{od_str}")
        else:
            lines.append(f"　金币：无贷款")
        if lj > 0:
            if lj_days >= 0:
                od_str = f" ⚠️ 已逾期{lj_days}天"
            else:
                od_str = f" ⏳ 还剩{-lj_days}天"
            lines.append(f"　积分：{lj:,} | {lj_dur}天期 | 到期 {lj_due}{od_str}")
        else:
            lines.append(f"　积分：无贷款")
        # 信用
        lines.append(f"⭐ **信用分**：{credit}（{grade}）")
        # 利息统计
        earned = bk.get("total_interest_earned", 0)
        paid = bk.get("total_interest_paid", 0)
        if earned > 0 or paid > 0:
            lines.append(f"📊 **累计利息**：收入 {earned:,} | 支出 {paid:,}")
        lines.append(f"📅 **周利率**：1%（每周一自动计息）")
        # 逾期警告
        max_od = max(lc_days, lj_days)
        if max_od >= data.BANK_OVERDUE_FREEZE_DAYS:
            lines.append("> 🚫 **账户已冻结！请立即还款！**")
        elif max_od > 0:
            remain = data.BANK_OVERDUE_FREEZE_DAYS - max_od
            lines.append(f"> ⚠️ 贷款已逾期{max_od}天，{remain}天后将冻结账户！")
        lines.append("━━━━━━━━━━━━━━")
        lines.append("`银行存款 金币/积分 数量` | `银行取款 金币/积分 数量`")
        lines.append("`银行贷款 金币/积分 数量` | `银行还款 金币/积分 数量`")
        return "\n".join(lines)

    def _bank_deposit(self, player: dict, tokens: list[str]) -> str:
        """存款：银行存款 金币/积分 数量"""
        currency = self._arg(tokens, 1)
        if currency not in ("金币", "积分"):
            return "用法：银行存款 金币/积分 数量\n例如：银行存款 金币 10000"
        count_str = self._arg(tokens, 2)
        if not count_str or not count_str.isdigit():
            return "请输入有效的存款数量（正整数）。"
        count = int(count_str)
        if count <= 0:
            return "存款数量必须大于 0。"
        have = self.store.get_currency(player, currency)
        if have < count:
            return f"你的{currency}不足（需要 {count:,}，当前 {have:,}）。"
        bk = self.store.bank_state(player)
        key = "deposit_coin" if currency == "金币" else "deposit_jifen"
        self.store.add_currency(player, currency, -count)
        bk[key] = bk.get(key, 0) + count
        return f"🏦 已存入 {currency} ×{count:,}。当前存款 {currency} {bk[key]:,}。\n> 周利率 1%，每周一自动计息。"

    def _bank_withdraw(self, player: dict, tokens: list[str]) -> str:
        """取款：银行取款 金币/积分 数量"""
        currency = self._arg(tokens, 1)
        if currency not in ("金币", "积分"):
            return "用法：银行取款 金币/积分 数量\n例如：银行取款 金币 5000"
        count_str = self._arg(tokens, 2)
        if not count_str or not count_str.isdigit():
            return "请输入有效的取款数量（正整数）。"
        count = int(count_str)
        if count <= 0:
            return "取款数量必须大于 0。"
        bk = self.store.bank_state(player)
        key = "deposit_coin" if currency == "金币" else "deposit_jifen"
        have = bk.get(key, 0)
        if have < count:
            return f"银行存款不足（需要 {count:,}，存款余额 {have:,}）。"
        bk[key] -= count
        self.store.add_currency(player, currency, count)
        return f"🏦 已取出 {currency} ×{count:,}。当前存款 {currency} {bk[key]:,}。"

    def _bank_loan(self, player: dict, tokens: list[str]) -> str:
        """贷款：银行贷款 金币/积分 数量 [7/14/30天]"""
        currency = self._arg(tokens, 1)
        if currency not in ("金币", "积分"):
            return (
                "用法：银行贷款 金币/积分 数量 [天数]\n"
                "例如：银行贷款 金币 50000 7\n"
                "可选期限：7天 / 14天 / 30天（默认7天）"
            )
        count_str = self._arg(tokens, 2)
        if not count_str or not count_str.isdigit():
            return "请输入有效的贷款数量（正整数）。"
        count = int(count_str)
        if count <= 0:
            return "贷款数量必须大于 0。"
        # 贷款期限
        dur_str = self._arg(tokens, 3)
        if dur_str and dur_str.isdigit():
            dur = int(dur_str)
        else:
            dur = data.BANK_LOAN_DEFAULT_DAYS
        if dur not in data.BANK_LOAN_DURATIONS:
            return f"贷款期限仅支持：{' / '.join(data.BANK_LOAN_DURATIONS.values())}"
        bk = self.store.bank_state(player)
        key_loan = "loan_coin" if currency == "金币" else "loan_jifen"
        key_due = "loan_coin_due" if currency == "金币" else "loan_jifen_due"
        key_dur = "loan_coin_dur" if currency == "金币" else "loan_jifen_dur"
        # 最低贷款金额
        if count < data.BANK_LOAN_MIN_AMOUNT:
            return f"单次贷款最低 {data.BANK_LOAN_MIN_AMOUNT:,} {currency}。"
        # 不能重复贷款
        if bk.get(key_loan, 0) > 0:
            return f"你已有未还清的{currency}贷款（余额 {bk[key_loan]:,}），请先还清再贷。"
        # 还清后冷却检查
        key_cd = "loan_coin_repaid_at" if currency == "金币" else "loan_jifen_repaid_at"
        last_repaid = bk.get(key_cd, 0)
        if last_repaid:
            elapsed = int(time.time()) - last_repaid
            if elapsed < data.BANK_LOAN_COOLDOWN_AFTER_REPAY:
                remain = data.BANK_LOAN_COOLDOWN_AFTER_REPAY - elapsed
                hours = remain // 3600
                mins = (remain % 3600) // 60
                return f"你刚还清{currency}贷款，需等待 {hours}时{mins}分后才能再次贷款。"
        # 检查额度
        credit = bk.get("credit_score", data.BANK_CREDIT_INITIAL)
        limit = data.bank_loan_limit(credit)
        if count > limit:
            return f"贷款金额超过你的额度上限（{limit:,}），当前信用分 {credit}。"
        # 放款
        today = time.strftime("%Y-%m-%d")
        due_ts = int(time.time()) + dur * 86400
        due_date = time.strftime("%Y-%m-%d", time.localtime(due_ts))
        bk[key_loan] = count
        bk[key_due] = due_date
        bk[key_dur] = dur
        bk["total_borrowed"] = bk.get("total_borrowed", 0) + count
        self.store.add_currency(player, currency, count)
        weeks = dur // 7
        est_interest = int(count * data.BANK_INTEREST_WEEKLY * weeks)
        return (
            f"🏦 已发放{currency}贷款 ×{count:,}。\n"
            f"> 📅 期限 {dur} 天（到期日 {due_date}）\n"
            f"> 📊 预估利息 ~{est_interest:,}（周利率 1%，每周一计息，复利）\n"
            f"> ⚠️ 到期后有 7 天宽限期，逾期更久将冻结账户。\n"
            f"> ⚠️ 有未还贷款期间，无法赠送宠物、转让物品和货币。"
        )

    def _bank_repay(self, player: dict, tokens: list[str]) -> str:
        """还款：银行还款 金币/积分 数量"""
        currency = self._arg(tokens, 1)
        if currency not in ("金币", "积分"):
            return "用法：银行还款 金币/积分 数量（或 全部）\n例如：银行还款 金币 5000"
        count_str = self._arg(tokens, 2)
        bk = self.store.bank_state(player)
        key_loan = "loan_coin" if currency == "金币" else "loan_jifen"
        key_due = "loan_coin_due" if currency == "金币" else "loan_jifen_due"
        loan = bk.get(key_loan, 0)
        if loan <= 0:
            return f"你没有{currency}贷款需要还。"
        if count_str == "全部":
            count = loan
        elif count_str and count_str.isdigit():
            count = min(int(count_str), loan)
        else:
            return "请输入有效的还款数量（正整数）或『全部』。"
        if count <= 0:
            return "还款数量必须大于 0。"
        have = self.store.get_currency(player, currency)
        if have < count:
            return f"你的{currency}不足（需要 {count:,}，当前 {have:,}）。"
        # 执行还款
        self.store.add_currency(player, currency, -count)
        bk[key_loan] -= count
        bk["total_repaid"] = bk.get("total_repaid", 0) + count
        # 判断是否还清
        if bk[key_loan] <= 0:
            bk[key_loan] = 0
            # 记录还清时间（冷却用）
            key_cd = "loan_coin_repaid_at" if currency == "金币" else "loan_jifen_repaid_at"
            bk[key_cd] = int(time.time())
            # 信用分调整（按贷款金额比例缩放，防刷小额贷款涨分）
            due_str = bk.pop(key_due, "")
            old_credit = bk.get("credit_score", data.BANK_CREDIT_INITIAL)
            # 贷款额相对于基础额度的比例，至少 1/100（小额贷款信用收益极低）
            loan_ratio = max(0.01, min(1.0, count / data.BANK_LOAN_MIN_AMOUNT))
            if due_str:
                try:
                    due_dt = time.strptime(due_str, "%Y-%m-%d")
                    due_ts = int(time.mktime(due_dt))
                    days_overdue = max(0, (int(time.time()) - due_ts) // 86400)
                except ValueError:
                    days_overdue = 0
                if days_overdue >= data.BANK_OVERDUE_FREEZE_DAYS:
                    # 逾期冻结后还清
                    change = random.randint(*data.BANK_CREDIT_OVERDUE)
                elif days_overdue > 0:
                    # 逾期但未冻结
                    change = data.BANK_CREDIT_REPAY_LATE
                else:
                    # 按时还款：按贷款比例缩放
                    base_change = random.randint(*data.BANK_CREDIT_REPAY_ON_TIME)
                    change = max(1, int(base_change * loan_ratio))
            else:
                base_change = random.randint(*data.BANK_CREDIT_REPAY_ON_TIME)
                change = max(1, int(base_change * loan_ratio))
            bk["credit_score"] = max(0, old_credit + change)
            if change > 0:
                credit_msg = f"\n> ⭐ 按时还款！信用分 +{change}（当前 {bk['credit_score']}）"
            elif change < 0:
                credit_msg = f"\n> ⚠️ 逾期还款，信用分 {change}（当前 {bk['credit_score']}）"
            else:
                credit_msg = f"\n> 信用分不变（当前 {bk['credit_score']}）"
            cd_hours = data.BANK_LOAN_COOLDOWN_AFTER_REPAY // 3600
            credit_msg += f"\n> ⏳ 该币种 {cd_hours} 小时内无法再次贷款。"
            return f"✅ 已还清{currency}贷款 ×{count:,}！账户已恢复正常。" + credit_msg
        else:
            return f"🏦 已偿还{currency}贷款 ×{count:,}，剩余贷款 {bk[key_loan]:,}。"

    # =====================================================================
    # 宠物重生
    # =====================================================================
    def _rebirth_prep_reminder(self, p: dict, before_level: int) -> str:
        """如果本次升级跨过 800 级，返回重生准备期提示。"""
        after = p.get("level", 1)
        stage = p.get("stage", "")
        if stage != "渡劫":
            return ""
        if before_level < data.REBIRTH_PREP_LEVEL and after >= data.REBIRTH_PREP_LEVEL:
            return (
                "\n\n🔔 **重生准备期！**\n"
                "宠物已达 Lv800，进入重生准备阶段：\n"
                "⛔ 背包物品已被锁定，无法出售/转让/丢弃\n"
                "💡 继续升级到 Lv999 即可进行重生！\n"
                "> 发送 `重生` 查看完整说明。"
            )
        if before_level < data.REBIRTH_MAX_LEVEL and after >= data.REBIRTH_MAX_LEVEL:
            return (
                "\n\n🎉 **已达 Lv999！可以重生！**\n"
                "> 发送 `购买重生宝石` 购买重生宝石\n"
                "> 发送 `祭奠 积分/钻石 数量` 提升倍率\n"
                "> 发送 `确认重生` 执行重生"
            )
        return ""

    def _rebirth_prep_block(self, player: dict) -> str | None:
        """Lv800+ 重生准备期：禁止出售/转让/丢弃。返回 None 表示放行。"""
        p = player.get("pet")
        if not p:
            return None
        level = p.get("level", 1)
        stage = p.get("stage", "")
        # 仅渡劫阶段 800+ 触发
        if stage != "渡劫" or level < data.REBIRTH_PREP_LEVEL:
            return None
        return (
            "⛔ **重生准备期（Lv800+）**\n"
            "宠物已进入重生准备阶段，背包物品已被锁定，\n"
            "无法出售、转让或丢弃物品，直到完成重生。\n"
            f"> 当前 Lv{level}/999，达到 Lv999 即可重生。\n"
            "> 发送 `重生` 查看重生详情。"
        )

    def _rebirth_info(self, player: dict) -> str:
        """查看重生状态和说明。"""
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物，发送『砸蛋』获取一只。"
        level = p.get("level", 1)
        stage = p.get("stage", "")
        cap = petmod.level_cap(p)
        if stage != "渡劫":
            return (
                "## 🔄 宠物重生\n\n"
                "宠物需达到 **渡劫 Lv999** 才能进行重生。\n\n"
                f"当前：{stage} Lv{level}/{cap}\n\n"
                "**重生效果：**\n"
                "- 宠物阶段回到 **幼年期 Lv1**\n"
                "- 攻击/防御/智力/血量 **随机 2~10 倍**暴击\n"
                "- 精力上限重置为 100\n"
                "- 背包全部清空（品质卡和定制卡保留）\n\n"
                "**重生宝石：** 1 万钻石 + 10 万积分\n"
                "**祭奠：** 消耗积分/钻石提升高倍率概率\n"
                "> 发送 `购买重生宝石` 提前准备宝石。"
            )
        sacrifice_pts = p.get("rebirth_sacrifice", 0)
        has_gem = p.get("rebirth_gem", False)
        if level >= data.REBIRTH_MAX_LEVEL:
            status = "✅ **已达 Lv999，可以重生！**"
        elif level >= data.REBIRTH_PREP_LEVEL:
            remain = data.REBIRTH_MAX_LEVEL - level
            status = f"⏳ **重生准备期** — 还需 {remain} 级到达 Lv999"
        else:
            remain = data.REBIRTH_PREP_LEVEL - level
            status = f"还需 {remain} 级进入准备期（Lv800）"
        # 倍率概率展示（含祭奠加成）
        pts = min(sacrifice_pts, data.REBIRTH_SACRIFICE_MAX_POINTS)
        n = len(data.REBIRTH_MULTIPLIER_TABLE)
        adjusted = []
        for i, (mult, w) in enumerate(data.REBIRTH_MULTIPLIER_TABLE):
            # 最高 3 档倍率享受祭奠加成（与 rebirth_roll_multiplier 一致）
            if i >= n - 3 and pts > 0:
                w_adj = w + data.REBIRTH_SACRIFICE_WEIGHT_PER_POINT * pts
            else:
                w_adj = w
            adjusted.append((mult, w_adj))
        total_adj = sum(w_adj for _, w_adj in adjusted)
        lines = [
            "## 🔄 宠物重生",
            f"> {status}",
            f"> 当前：{stage} Lv{level}/{cap}",
            "",
            "**重生倍率概率：**",
        ]
        for mult, w_adj in adjusted:
            pct = w_adj / total_adj * 100
            bar = "█" * int(pct / 2)
            lines.append(f"　{mult}×：{pct:.1f}% {bar}")
        if sacrifice_pts > 0:
            lines.append(f"> 🔥 祭奠点数：{sacrifice_pts}（高倍率权重已提升）")
        lines.append("")
        lines.append("**消耗：**")
        gem_status = "✅ 已拥有" if has_gem else "❌ 未购买"
        lines.append(f"- 重生宝石：{gem_status}")
        lines.append(f"- 购买宝石：`购买重生宝石`（1万钻石 + 10万积分）")
        lines.append("")
        lines.append("**祭奠（提升高倍率概率）：**")
        lines.append(f"- `祭奠 积分 数量`（最低 {data.REBIRTH_SACRIFICE_MIN_JIFEN:,}）")
        lines.append(f"- `祭奠 钻石 数量`（最低 {data.REBIRTH_SACRIFICE_MIN_DIAMOND:,}）")
        lines.append(f"- 每 {data.REBIRTH_SACRIFICE_PER_POINT_JIFEN:,} 积分 / {data.REBIRTH_SACRIFICE_PER_POINT_DIAMOND:,} 钻石 = 1 祭奠点")
        lines.append(f"- 最多 {data.REBIRTH_SACRIFICE_MAX_POINTS} 点")
        lines.append("")
        lines.append("**重生后：**")
        lines.append("- 阶段 → 幼年期 Lv1，精力 → 100")
        lines.append("- 属性 × 随机倍率（2~10×）")
        lines.append("- 背包清空（品质卡/定制卡保留）")
        lines.append("- 神器/秘技脱落回背包（需重新达标）")
        lines.append("- 天赋消失，需重新天赋觉醒")
        if level >= data.REBIRTH_MAX_LEVEL and has_gem:
            lines.append("")
            lines.append("> ⚠️ 发送 `确认重生` 执行重生（不可逆！）")
        return "\n".join(lines)

    def _rebirth_buy_gem(self, player: dict) -> str:
        """购买重生宝石：1万钻石 + 10万积分。"""
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物。"
        if p.get("rebirth_gem"):
            return "你已经拥有重生宝石了，可直接 `确认重生`。"
        diamond = self.store.get_currency(player, "钻石")
        jifen = self.store.get_currency(player, "积分")
        need_d = data.REBIRTH_GEM_COST_DIAMOND
        need_j = data.REBIRTH_GEM_COST_JIFEN
        if diamond < need_d:
            return f"钻石不足（需要 {need_d:,}，当前 {diamond:,}）。"
        if jifen < need_j:
            return f"积分不足（需要 {need_j:,}，当前 {jifen:,}）。"
        self.store.add_currency(player, "钻石", -need_d)
        self.store.add_currency(player, "积分", -need_j)
        p["rebirth_gem"] = True
        return (
            "💎 **重生宝石** 购买成功！\n"
            f"> 消耗：钻石 {need_d:,} + 积分 {need_j:,}\n"
            "> 宠物达到渡劫 Lv999 后发送 `确认重生` 即可。\n"
            "> 💡 发送 `祭奠 积分/钻石 数量` 可提升高倍率概率。"
        )

    def _rebirth_sacrifice(self, player: dict, tokens: list[str]) -> str:
        """祭奠：消耗积分或钻石提升重生倍率权重。"""
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物。"
        if p.get("stage", "") != "渡劫":
            return "只有渡劫阶段（Lv800+）的宠物才能祭奠。"
        currency = self._arg(tokens, 1)
        if currency not in ("积分", "钻石"):
            return (
                "用法：祭奠 积分/钻石 数量\n"
                f"例如：祭奠 积分 {data.REBIRTH_SACRIFICE_MIN_JIFEN:,}\n"
                f"　　　祭奠 钻石 {data.REBIRTH_SACRIFICE_MIN_DIAMOND:,}"
            )
        count_str = self._arg(tokens, 2)
        if not count_str or not count_str.isdigit():
            return "请输入有效的数量。"
        count = int(count_str)
        min_required = data.REBIRTH_SACRIFICE_MIN_JIFEN if currency == "积分" else data.REBIRTH_SACRIFICE_MIN_DIAMOND
        if count < min_required:
            return f"祭奠{currency}最低 {min_required:,}。"
        have = self.store.get_currency(player, currency)
        if have < count:
            return f"{currency}不足（需要 {count:,}，当前 {have:,}）。"
        per_point = data.REBIRTH_SACRIFICE_PER_POINT_JIFEN if currency == "积分" else data.REBIRTH_SACRIFICE_PER_POINT_DIAMOND
        pts = count // per_point
        if pts <= 0:
            return f"数量不足，每 {per_point:,} {currency} = 1 祭奠点。"
        current_pts = p.get("rebirth_sacrifice", 0)
        max_pts = data.REBIRTH_SACRIFICE_MAX_POINTS
        if current_pts >= max_pts:
            return f"祭奠点数已达上限（{max_pts}点），无需继续祭奠。"
        actual_pts = min(pts, max_pts - current_pts)
        actual_cost = actual_pts * per_point
        self.store.add_currency(player, currency, -actual_cost)
        p["rebirth_sacrifice"] = current_pts + actual_pts
        new_total = p["rebirth_sacrifice"]
        return (
            f"🔥 **祭奠完成！**\n"
            f"> 消耗：{currency} {actual_cost:,}\n"
            f"> 获得：{actual_pts} 祭奠点\n"
            f"> 累计祭奠：{new_total}/{max_pts} 点\n"
            f"> 高倍率（8×/9×/10×）权重已提升！\n"
            "> 💡 发送 `重生` 查看更新后的概率分布。"
        )

    def _rebirth_confirm(self, player: dict) -> str:
        """确认重生：二次确认后执行。"""
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物。"
        if p.get("stage", "") != "渡劫":
            return "只有渡劫阶段的宠物才能重生。"
        if p["level"] < data.REBIRTH_MAX_LEVEL:
            return (
                f"宠物等级不足，需达到渡劫 Lv999 才能重生。\n"
                f"当前：Lv{p['level']}/999"
            )
        if not p.get("rebirth_gem"):
            return "你还没有重生宝石，请先 `购买重生宝石`（1万钻石 + 10万积分）。"
        # 收集一生回顾（在修改宠物数据之前）
        review_text = self._rebirth_review(player, p)
        # 计算最终倍率
        sacrifice_pts = p.get("rebirth_sacrifice", 0)
        multiplier = data.rebirth_roll_multiplier(sacrifice_pts)
        # 执行重生
        old_atk = p.get("atk", 0)
        old_def = p.get("def", 0)
        old_intel = p.get("intel", 0)
        old_hp = p.get("hp_max", 0)
        # 属性暴击
        p["atk"] = max(1, int(old_atk * multiplier))
        p["def"] = max(1, int(old_def * multiplier))
        p["intel"] = max(1, int(old_intel * multiplier))
        p["hp_max"] = max(1, int(old_hp * multiplier))
        p["hp"] = p["hp_max"]
        # 重置阶段/等级/精力
        p["stage"] = data.STAGES[0]  # 幼年期
        p["level"] = 1
        p["exp"] = 0
        p["xianyuan"] = 0
        p["ascended"] = False
        p["energy"] = 100
        p["energy_max"] = 100
        # 神器/秘技脱落回背包
        dropped = []
        if p.get("artifact"):
            dropped.append(p["artifact"])
            self.store.add_item(player, p["artifact"], 1)
            p["artifact"] = None
        for sk in list(p.get("skills", [])):
            dropped.append(sk)
            self.store.add_item(player, sk, 1)
        p["skills"] = []
        # 天赋消失：重生后回到未觉醒状态，需重新天赋觉醒
        p["talent"] = None
        # 清空背包，只保留品质卡和定制卡
        bag = player.get("bag", {})
        kept = {}
        cleared_count = 0
        for item_name, count in list(bag.items()):
            if item_name in data.REBIRTH_KEEP_ITEMS or "卡" in item_name and any(
                q in item_name for q in ["史诗", "圣灵", "洪荒", "创世", "混沌", "定制"]
            ):
                kept[item_name] = count
            else:
                cleared_count += count
        player["bag"] = kept
        # 清除重生相关标记
        p.pop("rebirth_gem", None)
        p.pop("rebirth_sacrifice", None)
        # 重生奖励：永久 +1 宠物席位
        old_slots = player.get("pet_slots", data.PET_SLOTS_DEFAULT)
        slot_msg = ""
        if old_slots < data.PET_SLOTS_MAX:
            player["pet_slots"] = old_slots + 1
            slot_msg = f"🎁 **重生福利**：宠物席位 +1（{old_slots}→{old_slots+1}）"
        else:
            slot_msg = f"宠物席位已达上限 {data.PET_SLOTS_MAX}，本次未增加。"
        # 伤害统计
        def _summarize(old, new, label):
            return f"　{label}：{old:,} → {new:,}（×{multiplier}）"
        lines = [
            "## 🔄 重生完成！",
            f"🎉 宠物成功重生，获得 **×{multiplier}** 属性暴击！",
        ]
        # 插入一生回顾与领悟仪式
        lines.append(review_text)
        lines.append("**属性变化：**")
        lines.append(_summarize(old_atk, p["atk"], "攻击"))
        lines.append(_summarize(old_def, p["def"], "防御"))
        lines.append(_summarize(old_intel, p["intel"], "智力"))
        lines.append(_summarize(old_hp, p["hp_max"], "生命"))
        lines.append("")
        lines.append(f"**重置：** 阶段→幼年期 Lv1 | 精力→100")
        if dropped:
            lines.append(f"**脱落：** {'、'.join(dropped)}（已入背包）")
        if cleared_count > 0:
            lines.append(f"**清空：** 背包物品 ×{cleared_count}（品质卡/定制卡已保留）")
        lines.append("")
        lines.append(slot_msg)
        lines.append("> 🐣 宠物获得新生，重新踏上成长之路！")
        # 全群播报重生祝贺（优先用绑定QQ号，否则用用户ID）
        pid = str(player.get("qq", ""))
        bound_qq = self.store.get_bound_qq(pid)
        who = f"QQ `{bound_qq}`" if bound_qq else f"`{pid}`"
        nickname = p.get("nickname", "?")
        self._broadcast_to_authorized_groups(
            f"## 🎉 恭贺重生！\n"
            f"玩家 {who} 的『{nickname}』渡劫圆满，重获新生！\n"
            f"获得 **×{multiplier}** 属性暴击，荣耀加身！"
        )
        return "\n".join(lines)

    def _rebirth_review(self, player: dict, pet: dict) -> str:
        """收集宠物一生数据，生成临终回顾与领悟仪式。"""
        stats = player.get("stats", {})
        nickname = pet.get("nickname", "?")
        species = pet.get("species", "?")
        level = pet.get("level", 1)
        stage = pet.get("stage", "?")

        # ---- 一生数据 ----
        battles = stats.get("battle_win", 0) + stats.get("ascended_battle_win", 0)
        dungeons = stats.get("ascended_dungeon_clear", 0)
        explores = stats.get("explore", 0)
        abyss_runs = stats.get("ascended_abyss", 0)
        forge = stats.get("forge_artifact", 0) or 0
        shuangxiu = stats.get("shuangxiu", 0) or 0
        treasure = stats.get("ascended_fantasy_treasure", 0) or 0
        calamity = stats.get("ascended_immortal_calamity", 0) or 0

        skills_count = len(pet.get("skills", []))
        artifact = pet.get("artifact")
        talent = pet.get("talent") or "未觉醒"
        favor = pet.get("favor", 0)
        love_target = pet.get("love_target")
        love_state = pet.get("love_state", "单身")
        created_at = pet.get("created_at", 0)
        pet_age_days = max(1, (int(__import__("time").time()) - created_at) // 86400) if created_at else "?"

        # ---- 数据卡片 ----
        lines = ["", "━━━━━━━━━━━━━━━━", "📜 **回望「{0}」的一生**".format(nickname), ""]
        life_parts = []
        if battles > 0:
            life_parts.append("⚔ 征战 **{0}** 场胜利".format(battles))
        if dungeons > 0:
            life_parts.append("🏔 通关 **{0}** 次神仙副本".format(dungeons))
        if abyss_runs > 0:
            life_parts.append("🌀 深入深渊 **{0}** 次".format(abyss_runs))
        if explores > 0:
            life_parts.append("🧭 探险 **{0}** 次".format(explores))
        if shuangxiu > 0:
            life_parts.append("💕 双修 **{0}** 次".format(shuangxiu))
        if treasure > 0:
            life_parts.append("✨ 幻境寻宝 **{0}** 次".format(treasure))
        if calamity > 0:
            life_parts.append("⚡ 渡过神仙劫 **{0}** 次".format(calamity))
        if forge > 0:
            life_parts.append("🔨 打造神器 **{0}** 把".format(forge))
        if skills_count > 0:
            life_parts.append("📜 参悟 **{0}** 门秘技".format(skills_count))
        if artifact:
            life_parts.append("🗡 佩戴过神器「{0}」".format(artifact))
        if talent and talent != "未觉醒":
            life_parts.append("🎯 天赋「{0}」".format(talent))

        if not life_parts:
            life_parts.append("🌱 平凡而安静的一生")

        lines.append("　" + "  ·  ".join(life_parts))

        if love_state != "单身" and favor > 0:
            heart = "💑" if love_state == "已婚" else "💕"
            lines.append("{0} 与伴侣相伴，好感 **{1:,}**".format(heart, favor))
        if isinstance(pet_age_days, int):
            lines.append("📅 陪伴了你 **{0}** 天".format(pet_age_days))
        lines.append("　品质 **{0}** · **{1}** Lv{2}".format(pet.get("quality", "?"), stage, level))

        # ---- 领悟方向判定 ----
        scores = {}
        scores["⚔ 战神之道"] = battles
        scores["🏔 探索之道"] = dungeons + explores + abyss_runs
        scores["💑 情缘之道"] = favor // 100 + shuangxiu * 10
        scores["💪 精进之道"] = treasure + calamity + forge + skills_count * 5
        # 取最高的
        best = max(scores, key=lambda k: scores[k]) if any(scores.values()) else "🌟 平凡之道"
        best_val = scores.get(best, 0)

        # ---- 领悟文案 ----
        wisdom = {
            "⚔ 战神之道": (
                "真正的强大，不是从未倒下，\n"
                "　而是每一次倒下后，依然选择站起来。\n"
                "　胜败皆过往，唯有勇气长存。"
            ),
            "🏔 探索之道": (
                "世界之大，穷尽一生也走不完。\n"
                "　但路上的风景、遇见的对手、发现的秘境，\n"
                "　已经把你变成了不一样的自己。"
            ),
            "💑 情缘之道": (
                "爱不是占有，而是彼此照亮。\n"
                "　即使轮回转世、记忆消散，\n"
                "　那份温暖的羁绊已在灵魂中留下印记。"
            ),
            "💪 精进之道": (
                "日复一日的修炼、锻造、参悟……\n"
                "　外人看来枯燥乏味，\n"
                "　但你知道——每一步都算数。"
            ),
            "🌟 平凡之道": (
                "不是每一段生命都要轰轰烈烈。\n"
                "　安静地来，安静地走，\n"
                "　存在本身，就已经是意义。"
            ),
        }
        wisdom_text = wisdom.get(best, wisdom["🌟 平凡之道"])

        lines.append("")
        lines.append(
            "💡 在生命最后一刻，**{0}** 领悟了——".format(nickname)
        )
        lines.append("")
        lines.append("　**「{0}」**".format(best))
        lines.append("")
        for wline in wisdom_text.split("\n"):
            lines.append("　> {0}".format(wline.strip()))
        lines.append("")
        lines.append("🌅 {0} 闭上了眼睛...".format(nickname))
        lines.append("　「谢谢你陪我走过这一段路。」")
        lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _inc_stat(player: dict, key: str, n: int = 1) -> None:
        """增加玩家统计计数（如剧情任务进度）。"""
        player.setdefault("stats", {})[key] = player["stats"].get(key, 0) + n

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
    # 群主/管理员撤回群成员消息（QQ 官方 v2 API）
    # =====================================================================
    async def _cmd_recall_member(
        self, event, qq: str, group_id: str, text: str
    ) -> str | None:
        """「撤回 @成员 [数量]」：撤回被 @ 成员最近发送的 N 条消息。

        仅群主/管理员（含插件管理员白名单）可用；机器人需被设为群管理员
        才有权撤回成员消息；仅能撤回 2 分钟内且机器人接收到过的消息。

        Args:
            event: 消息事件。
            qq: 发送者 openid。
            group_id: 群 openid。
            text: 去除@机器人后的消息文本。

        Returns:
            回复文本；若判定不是撤回指令则返回 None 走正常路由。
        """
        body = re.sub(r"^(撤回消息|撤回)", "", text, count=1).strip()
        # 解析目标成员：优先事件里的 @ 提及，其次文本中的 <@openid>
        targets: list[str] = []
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        for m in getattr(raw, "mentions", None) or []:
            mid = str(getattr(m, "id", "") or "")
            if mid and not getattr(m, "is_you", False) and mid not in targets:
                targets.append(mid)
        for mid in _MENTION_RE.findall(body):
            if mid not in targets:
                targets.append(mid)
        rest = _MENTION_RE.sub(" ", body).split()
        if not targets:
            if body:
                return None  # 普通聊天里出现「撤回」二字，不当作指令
            return (
                "## 🗑️ 撤回成员消息\n"
                "用法：`撤回 @成员 [数量]`（数量默认 1，最多 10）\n"
                "仅群主/管理员可用；只能撤回该成员最近 2 分钟内、且机器人"
                "接收到过的消息（机器人需为群管理员）。"
            )
        if not self._is_group(group_id):
            return "🚫 该指令仅支持在群聊内使用。"
        api = getattr(getattr(event, "bot", None), "api", None)
        if api is None:
            return "❌ 当前平台不支持撤回操作（需 QQ 官方机器人）。"
        try:
            from botpy.http import Route
        except Exception:
            return "❌ 当前平台不支持撤回操作（需 QQ 官方机器人）。"
        # 数量：取剩余 token 中第一个数字，默认 1，上限 10
        count = 1
        for tok in rest:
            if tok.isdigit():
                count = max(1, min(10, int(tok)))
                break
        # 权限：插件管理员白名单直接放行，否则查询群成员角色
        if qq not in self.admins:
            try:
                info = await api._http.request(
                    Route(
                        "GET",
                        "/v2/groups/{group_openid}/members/{member_openid}",
                        group_openid=group_id,
                        member_openid=qq,
                    )
                )
                role = str((info or {}).get("member_role", "member"))
            except Exception as e:
                logger.warning(f"[petpark] 查询群成员角色失败：{e}")
                return "❌ 无法校验你的群身份（查询群成员接口失败），已拒绝撤回。"
            if role not in ("owner", "admin"):
                return "❌ 仅群主或管理员可以撤回成员消息。"
        target = targets[0]
        key = f"{group_id}\x1f{target}"
        log = self._group_msg_log.get(key) or deque()
        now = time.time()
        cand = [mid for mid, ts in reversed(log) if now - ts <= 120]
        if not cand:
            return (
                "❌ 没有找到该成员最近可撤回的消息。\n"
                "只能撤回 2 分钟内、且机器人接收到过（如 @机器人）的消息。"
            )
        ok, fail = 0, 0
        for mid in cand[:count]:
            try:
                await api._http.request(
                    Route(
                        "DELETE",
                        "/v2/groups/{group_openid}/messages/{message_id}",
                        group_openid=group_id,
                        message_id=mid,
                    )
                )
                ok += 1
            except Exception as e:
                fail += 1
                logger.warning(f"[petpark] 撤回消息 {mid} 失败：{e}")
            self._group_msg_log[key] = deque(
                (p for p in log if p[0] != mid), maxlen=30
            )
            log = self._group_msg_log[key]
        result = f"## 🗑️ 撤回成员消息\n已撤回该成员 **{ok}** 条消息"
        if fail:
            result += (
                f"，另有 {fail} 条撤回失败\n"
                "（可能超过 2 分钟，或机器人未被设置为群管理员）"
            )
        result += "。"
        logger.info(
            f"[petpark] 群 {group_id} 内 {qq} 撤回成员 {target} 消息 "
            f"成功{ok}条/失败{fail}条"
        )
        return result

    # =====================================================================
    # 群管理：禁言 / 全体禁言查询 / 自动审批进群 / 新人进群推送 / 退群推送
    # （QQ 官方 v2 API，全部由插件直接实现，框架只提供原始 bot 客户端）
    # =====================================================================
    def _get_bot(self):
        """返回当前机器人的 bot 客户端（含 .api 直连 QQ 群 API）；无框架能力返回 None。"""
        get_bot = getattr(self.context, "get_bot", None)
        if callable(get_bot):
            try:
                return get_bot()
            except Exception:
                return None
        return None

    async def _member_nick(self, group_id: str, member: str) -> str:
        """查询成员群昵称（带缓存）；失败返回空串。

        缓存值为 (昵称, 时间戳)：昵称非空则长期有效；为空（接口无权限/失败）
        则 10 分钟内不重试，避免每条消息都打一次无效 API。
        """
        member = str(member or "")
        if not member or not group_id:
            return ""
        cache = self._nick_cache.setdefault(str(group_id), {})
        hit = cache.get(member)
        if isinstance(hit, tuple):
            nick, ts = hit
            if nick or (time.time() - ts) < 600:
                return nick
        bot = self._get_bot()
        api = getattr(bot, "api", None) if bot else None
        if api is None:
            return ""
        try:
            from botpy.http import Route
        except Exception:
            return ""
        try:
            info = await api._http.request(
                Route(
                    "GET",
                    "/v2/groups/{group_openid}/members/{member_openid}",
                    group_openid=str(group_id),
                    member_openid=member,
                )
            )
            nick = str((info or {}).get("username", "") or "")
        except Exception:
            nick = ""
        cache[member] = (nick, time.time())
        return nick

    async def _send_group_text(self, group_id: str, text: str) -> None:
        """主动向群推送纯文本（Markdown 优先，失败回退纯文本）。"""
        if not group_id or not text:
            return
        bot = self._get_bot()
        if bot is None:
            return
        try:
            await bot.send_group(str(group_id), text, markdown=True)
        except Exception:
            try:
                await bot.send_group(str(group_id), text, markdown=False)
            except Exception:
                logger.warning(f"[petpark] 群 {group_id} 主动推送失败")

    async def _cmd_mute(self, event, qq: str, group_id: str, text: str) -> str | None:
        """「禁言 @成员 [时长]」「解除禁言 @成员」「全体禁言」。

        仅群主/管理员（含插件管理员白名单）可用；禁言指令作用于已授权群。
        """
        if not self.mute_enabled:
            return "❌ 群管理指令（禁言/全体禁言）已在插件配置中关闭。"
        if not self._is_group(group_id):
            return "🚫 该指令仅支持在群聊内使用。"
        api = getattr(getattr(event, "bot", None), "api", None)
        if api is None:
            api = getattr(self._get_bot(), "api", None)
        if api is None:
            return "❌ 当前平台不支持群管理操作（需 QQ 官方机器人）。"
        try:
            from botpy.http import Route
        except Exception:
            return "❌ 当前平台不支持群管理操作（需 QQ 官方机器人）。"
        # 授权范围：仅「已授权宠物乐园」的群可用（关闭宠物乐园后群管理指令一并禁用）
        try:
            if not self.store.get_group(group_id).get("enabled", False):
                return "❌ 本群未开启宠物乐园，无法使用群管理指令。"
        except Exception:
            pass
        # 权限：插件管理员白名单直接放行，否则查询群成员角色
        if qq not in self.admins:
            try:
                info = await api._http.request(
                    Route(
                        "GET",
                        "/v2/groups/{group_openid}/members/{member_openid}",
                        group_openid=group_id,
                        member_openid=qq,
                    )
                )
                role = str((info or {}).get("member_role", "member"))
            except Exception as e:
                logger.warning(f"[petpark] 查询群成员角色失败：{e}")
                return (
                    "❌ 无法校验你的群身份：查询群成员接口失败。\n"
                    "> 可能原因：机器人未在 QQ 开放平台开通「查询群成员信息」接口权限。"
                )
            if role not in ("owner", "admin"):
                return "❌ 仅群主或管理员可以使用群管理指令。"

        # 解析被 @ 的目标成员
        body = re.sub(r"^(禁言|解除禁言|全体禁言)", "", text, count=1).strip()
        targets: list[str] = []
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        for m in getattr(raw, "mentions", None) or []:
            mid = str(getattr(m, "id", "") or "")
            if mid and not getattr(m, "is_you", False) and mid not in targets:
                targets.append(mid)
        for mid in _MENTION_RE.findall(body):
            if mid not in targets:
                targets.append(mid)

        if text.startswith("全体禁言"):
            return await self._cmd_mute_all(api, group_id)
        if text.startswith("解除禁言"):
            if not targets:
                return "## 🔇 解除禁言\n用法：`解除禁言 @成员`（仅群主/管理员可用）。"
            return await self._set_member_mute(api, group_id, targets[0], None)
        # 禁言
        if not targets:
            return (
                "## 🔇 禁言成员\n"
                "用法：`禁言 @成员 [时长]`（时长如 `10分钟` / `1小时` / `1天`，默认 10 分钟）\n"
                "仅群主/管理员可用；机器人需为群管理员。"
            )
        seconds = self._parse_mute_seconds(_MENTION_RE.sub(" ", body).split())
        return await self._set_member_mute(api, group_id, targets[0], seconds)

    @staticmethod
    def _parse_mute_seconds(tokens: list[str]) -> int:
        """解析时长 token（如 10分钟 / 1小时 / 2天 / 30 / 300），返回秒；默认 600（10 分钟）。"""
        seconds = 0
        for tok in tokens:
            m = re.match(r"^(\d+)\s*(秒|分钟|小时|天)?$", tok)
            if not m:
                continue
            n = int(m.group(1))
            unit = m.group(2) or "分钟"
            seconds = n if unit == "秒" else n * 60 if unit == "分钟" else n * 3600 if unit == "小时" else n * 86400
            if seconds > 0:
                break
        seconds = seconds or 600
        return max(1, min(30 * 86400, seconds))  # 最长 30 天

    MUTE_COMPENSATE_SEC = 60  # QQ 客户端把剩余禁言时长向下取整（会“少显示1分钟”），实际多设 1 分钟补偿

    async def _set_member_mute(self, api, group_id: str, target: str, seconds: int | None) -> str:
        """禁言（seconds>0）或解除禁言（seconds=None）指定成员。

        官方接口要求 mute_expire_at 为 RFC3339 字符串（北京时间），
        op=del 时传空字符串表示立即解除。
        实际设置的时长 = seconds + 1 分钟补偿（封顶 30 天），回复仍按申请时长显示。
        """
        try:
            from botpy.http import Route
        except Exception:
            return "❌ 当前平台不支持群管理操作。"
        route = Route(
            "POST",
            "/v2/groups/{group_openid}/restrict_chat_setting",
            group_openid=group_id,
        )
        if seconds:
            total = min(seconds + self.MUTE_COMPENSATE_SEC, 30 * 86400)
            expire_dt = datetime.fromtimestamp(
                int(time.time()) + total, tz=ZoneInfo("Asia/Shanghai")
            )
            expire = expire_dt.isoformat()
            # 优先 op=add；若目标已在禁言名单中则回退 op=update
            last_err = ""
            for op in ("add", "update"):
                try:
                    await api._http.request(
                        route,
                        json={
                            "members": [
                                {
                                    "op": op,
                                    "member_openid": target,
                                    "mute_expire_at": expire,
                                }
                            ]
                        },
                    )
                    break
                except Exception as e:
                    last_err = str(e)
                    if op == "add":
                        continue
                    logger.warning(f"[petpark] 禁言成员 {target} 失败：{e}")
                    return (
                        "❌ 禁言操作失败（机器人需为群管理员，且只能禁言普通成员，"
                        f"不能禁言群主/管理员/机器人）。\n> 接口返回：{last_err}"
                    )
            return (
                f"## 🔇 群管理\n已禁言成员 **{target}** {seconds // 60} 分钟，"
                f"至 {expire_dt.strftime('%H:%M:%S')} 自动解除。"
            )
        try:
            await api._http.request(
                route,
                json={
                    "members": [
                        {"op": "del", "member_openid": target, "mute_expire_at": ""}
                    ]
                },
            )
            return f"## 🔇 群管理\n已解除对成员 **{target}** 的禁言。"
        except Exception as e:
            logger.warning(f"[petpark] 解除禁言 {target} 失败：{e}")
            return f"❌ 解除禁言失败（机器人需为群管理员）。\n> 接口返回：{e}"

    async def _cmd_mute_all(self, api, group_id: str) -> str:
        """查询并汇报全员禁言状态（QQ 官方仅提供查询接口，无法由机器人代开/关）。"""
        try:
            from botpy.http import Route
        except Exception:
            return "❌ 当前平台不支持查询全员禁言。"
        try:
            info = await api._http.request(
                Route(
                    "GET",
                    "/v2/groups/{group_openid}/restrict_chat_setting",
                    group_openid=group_id,
                )
            )
        except Exception as e:
            logger.warning(f"[petpark] 查询全员禁言状态失败：{e}")
            return "❌ 查询全员禁言状态失败（机器人需为群管理员）。"
        mode = str(((info or {}).get("global_rule") or {}).get("mode", "none"))
        label = {"none": "未开启", "always": "全体禁言中（始终禁言）", "schedule": "定时禁言中"}.get(
            mode, mode or "未知"
        )
        return (
            "## 🔇 全体禁言状态\n"
            f"当前状态：**{label}**\n"
            "QQ 官方机器人接口仅支持查询，需群主在 QQ 客户端手动开启/关闭全体禁言。"
        )

    async def on_group_member_add(self, data: dict) -> None:
        """群成员加入事件：按配置推送欢迎语（@群昵称）。"""
        if not self.welcome_push:
            return
        gid = str(data.get("group_openid", "") or "")
        member = str(data.get("member_openid", "") or "")
        if not gid or not member:
            return
        nick = (await self._member_nick(gid, member)) or member
        text = (self.welcome_template or "").replace("{{member}}", nick)
        if text:
            await self._send_group_text(gid, text)

    async def on_group_member_remove(self, data: dict) -> None:
        """群成员退出事件：按配置推送退群语（@群昵称）。"""
        if not self.leave_push:
            return
        gid = str(data.get("group_openid", "") or "")
        member = str(data.get("member_openid", "") or "")
        if not gid or not member:
            return
        nick = (await self._member_nick(gid, member)) or member
        text = (self.leave_template or "").replace("{{member}}", nick)
        if text:
            await self._send_group_text(gid, text)

    GROUP_AUTO_APPROVE_SEC = 30  # 每 30 秒拉取一次入群申请
    _BOT_ADMIN_CHECK_SEC = 600  # 每 10 分钟复核一次机器人群身份

    async def _admin_groups(self, api) -> list[str]:
        """返回机器人是群主/管理员的群（带缓存）。

        通过 bot_state 接口确认；非管理员的群调用入群接口必报 11703，
        提前排除可避免每 30 秒刷一次错误日志。
        """
        now = time.time()
        cached = getattr(self, "_admin_groups_cache", None)
        if cached and now - cached[0] < self._BOT_ADMIN_CHECK_SEC:
            return cached[1]
        get_groups = getattr(self.context, "get_known_groups", None)
        groups: list[str] = []
        if callable(get_groups):
            try:
                groups = [str(g) for g in (get_groups() or [])]
            except Exception:
                groups = []
        admin_groups: list[str] = []
        try:
            from botpy.http import Route
        except Exception:
            return []
        for gid in groups:
            try:
                st = await api._http.request(
                    Route(
                        "GET",
                        "/v2/groups/{group_openid}/bot_state",
                        group_openid=gid,
                    )
                )
                if str((st or {}).get("member_role", "")) in ("owner", "admin"):
                    admin_groups.append(gid)
            except Exception:
                continue
        self._admin_groups_cache = (now, admin_groups)
        return admin_groups

    async def _group_auto_approve_loop(self) -> None:
        """后台循环：定期自动审批已接入群的入群申请（可配置关闭）。"""
        while True:
            try:
                if self.auto_approve:
                    await self._auto_approve_tick()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[petpark] 自动审批进群循环异常")
            try:
                await asyncio.sleep(self.GROUP_AUTO_APPROVE_SEC)
            except asyncio.CancelledError:
                break

    async def _auto_approve_tick(self) -> None:
        """单次自动审批：仅在机器人是管理员的群拉取并逐个通过入群申请。"""
        bot = self._get_bot()
        api = getattr(bot, "api", None) if bot else None
        if api is None or not getattr(bot, "is_ready", lambda: False)():
            return
        groups = await self._admin_groups(api)
        if not groups:
            return
        try:
            from botpy.http import Route
        except Exception:
            return
        for gid in groups:
            try:
                info = await api._http.request(
                    Route(
                        "GET",
                        "/v2/groups/{group_openid}/join_request_list",
                        group_openid=gid,
                    ),
                    params={"cursor": "", "limit": 10},
                )
            except Exception:
                continue
            for req in ((info or {}).get("list") or []):
                member = str(req.get("member_openid", "") or "")
                rid = str(req.get("join_request_id", "") or "")
                if not member:
                    continue
                # 入群申请里带 username（群昵称），顺手缓存供 @昵称 使用
                uname = str(req.get("username", "") or "")
                if uname:
                    self._nick_cache.setdefault(gid, {})[member] = (
                        uname,
                        time.time(),
                    )
                try:
                    await api._http.request(
                        Route(
                            "POST",
                            "/v2/groups/{group_openid}/approval_join_request/{member_openid}",
                            group_openid=gid,
                            member_openid=member,
                        ),
                        json={"op": "approve", "join_request_id": rid},
                    )
                    logger.info(f"[petpark] 已自动审批通过群 {gid} 的入群申请 {member}")
                except Exception as e:
                    logger.warning(f"[petpark] 自动审批群 {gid} 成员 {member} 失败：{e}")

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
        # 扫雷紧凑指令归一化：扫a1b2 → 扫 a1b2；插旗a1 → 插旗 a1；开始扫雷2 → 开始扫雷 2
        m = _MS_COMPACT_RE.match(cmd)
        if m:
            tokens = [m.group(1), m.group(2)] + tokens[1:]
            cmd = tokens[0]
        else:
            m = _MS_START_RE.match(cmd)
            if m:
                tokens = ["开始扫雷", m.group(1)] + tokens[1:]
                cmd = "开始扫雷"
        # 非本插件指令直接放行，避免为每条普通聊天创建玩家/群档案
        event_cmds = self._active_event_commands()
        # 家园指令白名单直通（绕过可能的模块缓存问题）
        _HS_CMDS = {"家园", "家园介绍", "家园教程", "建造", "升级", "家园升级", "家园收取",
                     "家园建筑", "拜访家园", "家园拜访", "派遣", "召回", "派遣状态",
                     "顺手牵羊", "偷菜", "家园排行", "家园总排行", "商人购买", "拆除"}
        # 银行指令白名单直通
        _BANK_CMDS = {"宠物银行", "银行信息", "银行存款", "银行取款", "银行贷款", "银行还款"}
        _REBIRTH_CMDS = {"重生", "购买重生宝石", "确认重生", "祭奠"}
        _PET_CMDS = {"切换宠物", "宠物列表", "查看所有宠物", "宠物信息"}
        if (
            cmd not in KNOWN_COMMANDS
            and cmd not in _HS_CMDS
            and cmd not in _BANK_CMDS
            and cmd not in _REBIRTH_CMDS
            and cmd not in _PET_CMDS
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
        self._track_activity(player)

        # 银行逾期冻结检查（放行查看/还款类指令）
        _bank_allow = {"银行信息", "银行还款", "宠物菜单", "宠物指令", "宠物帮助",
                        "管理菜单", "我的信息", "个人信息", "签到", "兑换", "卡密兑换",
                        "授权状态", "授权", "查看说明", "银行信息",
                        "重生", "购买重生宝石", "确认重生", "祭奠",
                        "宠物列表", "查看所有宠物", "宠物信息", "切换宠物",
                        "绑定QQ", "验证码", "换绑QQ", "解绑QQ"}
        if cmd not in _bank_allow:
            bank_block = self._bank_block_check(player)
            if bank_block:
                return bank_block

        # 重生准备期（Lv800+）：禁止出售/转让/丢弃背包物品
        _rebirth_block_cmds = {"出售", "转让", "丢弃"}
        if cmd in _rebirth_block_cmds:
            rb_block = self._rebirth_prep_block(player)
            if rb_block:
                return rb_block

        # ---- 我的信息（唯一展示 ID / 群 / 金币 / 积分 的地方）----
        if cmd in ("我的信息", "个人信息"):
            return self._my_info(player, group_id)

        # ---- QQ 绑定（邮箱验证码）----
        if cmd == "绑定QQ":
            return self._bind_qq(player, tokens)
        if cmd == "验证码":
            return self._verify_qq_code(player, tokens)
        if cmd == "换绑QQ":
            return self._bind_qq(player, tokens, rebind=True)
        if cmd == "解绑QQ":
            return self._unbind_qq(player)

        # ---- 邀请 ----
        if cmd == "受邀":
            return self._accept_invite(player, group_id, tokens)
        if cmd == "我的邀请情况":
            return self._my_invites(player)

        # ---- 每日签到 ----
        if cmd == "签到":
            return self._sign_in(player, group_id)

        # ---- 宗门战 ----
        if cmd == "宗门介绍":
            return self._sect_intro(group_id)
        if cmd == "宗门签到":
            return self._sect_sign(player, group_id)
        if cmd == "宗门信息":
            return self._sect_info(player, group_id)
        if cmd == "宗门名单":
            return self._sect_list(group_id)
        if cmd == "宗门报名":
            return self._sect_enroll(player, group_id)
        if cmd == "宗门战况":
            return self._sect_status(group_id)
        if cmd == "宗门对阵":
            return self._sect_matchup(group_id)
        if cmd == "宗门赛况":
            return self._sect_live(group_id)
        if cmd == "宗门战报":
            return self._sect_battle_report(group_id)
        if cmd == "宗门历史":
            return self._sect_history(group_id)
        if cmd == "宗门倒计时":
            return self._sect_countdown(group_id)
        if cmd == "宗门排行":
            return self._sect_rank(group_id)
        if cmd == "宗门商店":
            return self._sect_shop(group_id)
        if cmd == "宗门升级":
            return self._sect_level_up(player, group_id)
        if cmd == "宗门兑换":
            return self._sect_buy(player, group_id, tokens)
        if cmd == "宗门确认":
            return self._sect_confirm_list(player, group_id)
        if cmd == "宗门踢出":
            return self._sect_kick_enroll(player, group_id, tokens)
        if cmd == "宗门公告":
            return self._sect_set_notice(player, group_id, tokens)
        if cmd == "宗门改名":
            return self._sect_rename(player, group_id, tokens)
        if cmd == "宗门任命副宗主":
            return self._sect_appoint_deputy(player, group_id, tokens)
        if cmd == "宗门撤销副宗主":
            return self._sect_revoke_deputy(player, group_id, tokens)
        if cmd == "宗门重选宗主":
            return self._sect_re_elect(player, group_id)
        if cmd == "宗门选举":
            return self._sect_manual_elect(player, group_id)
        if cmd == "加油":
            return self._sect_cheer(player, group_id)

        # ---- 卡密兑换 ----
        if cmd in ("兑换", "卡密兑换"):
            return self._redeem(player, group_id, qq, tokens)
        if cmd == "修炼卡":
            return self._redeem_auto_cultivation_card(player, group_id, qq, tokens)
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
        if cmd in ("我的宠物", "宠物图"):
            return self._my_pet(player)
        if cmd == "查看宠物":
            if len(tokens) > 1:
                return self._inspect(group_id, tokens)
            return self._my_pet(player)
        if cmd == "宠物侦查":
            return self._inspect(group_id, tokens)
        if cmd == "赠送宠物":
            return self._gift_pet(player, group_id, tokens)
        if cmd in ("锁定宠物", "宠物锁定"):
            return self._lock_pet(player, True)
        if cmd in ("解锁宠物", "宠物解锁"):
            return self._lock_pet(player, False)
        if cmd == "宠物改名":
            return self._rename(player, tokens)
        if cmd == "宠物变性":
            return self._change_gender(player)
        if cmd == "宠物复活":
            return self._revive_self(player)
        if cmd == "宠物状态":
            return self._status_text(player)
        if cmd == "喂食":
            return self._feed(player, group_id, tokens)

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
        if cmd == "经验换仙元":
            return self._exp_to_xianyuan(player)

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

        # ---- 自动修炼 ----
        if cmd in ("自动修炼", "开启自动修炼"):
            return self._auto_cultivation_toggle(player, True)
        if cmd == "关闭自动修炼":
            return self._auto_cultivation_toggle(player, False)
        if cmd in ("自动修炼状态", "修炼状态"):
            return self._auto_cultivation_status(player)

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
        if cmd == "飞升副本":
            return self._ascend_dungeon_list()
        if cmd == "挑战神仙":
            return self._enter_ascend_dungeon(player, tokens)
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
            return self._quest_list(player)
        if cmd == "我的剧情任务":
            return self._my_quests(player)
        if cmd == "取消剧情任务":
            player["quests"] = {}
            return "已取消所有已领取的剧情任务。"
        if cmd == "提交任务" or cmd == "领取任务":
            return self._handle_quest(player, tokens, cmd)

        # ---- 宠物摸金 ----
        if cmd in ("摸金", "摸金介绍", "摸"):
            return self._tomb_intro()
        if cmd in ("摸金商店", "摸店"):
            return self._tomb_shop()
        if cmd in ("购买摸金道具", "摸买"):
            return self._tomb_buy(player, tokens)
        if cmd in ("我的摸金", "摸我"):
            return self._tomb_status_outside(player)
        if cmd == "摸包":
            return self._tomb_pack(player)
        # 摸金双排组队
        if cmd == "摸金组队":
            return self._tomb_team_invite(player, group_id, tokens)
        if cmd == "摸金准备":
            return self._tomb_team_ready(player)
        if cmd == "摸金取消组队":
            return self._tomb_team_cancel(player)
        if cmd == "摸金队伍":
            return self._tomb_team_status(player)
        if cmd in ("进入摸金", "摸进"):
            return self._tomb_enter(player, tokens)
        if cmd == "摸金移动":
            return self._tomb_move(player, tokens)
        if cmd in ("上", "下", "左", "右"):
            return self._tomb_move(player, ["摸金移动", cmd])
        if cmd in ("1", "2", "3"):
            return self._tomb_pick_card(player, cmd)
        if cmd in ("摸金探索", "摸看"):
            return self._tomb_explore(player)
        if cmd in ("摸金开箱", "开箱"):
            return self._tomb_open_chest(player)
        if cmd in ("摸金使用", "摸用"):
            return self._tomb_use_item(player, tokens)
        if cmd == "摸装":
            return self._tomb_equip(player, tokens)
        if cmd == "摸带":
            return self._tomb_move_item(player, tokens, "to_equip")
        if cmd == "摸存":
            return self._tomb_move_item(player, tokens, "to_storage")
        if cmd in ("摸金撤离", "摸撤"):
            return self._tomb_evacuate(player)
        if cmd in ("摸金状态", "摸态"):
            return self._tomb_status(player)
        if cmd in ("放弃摸金", "摸弃"):
            return self._tomb_forfeit(player)
        if cmd == "战斗":
            return self._tomb_battle_cmd(player)
        if cmd == "祭拜":
            return self._tomb_altar_cmd(player)
        if cmd == "逃跑":
            return self._tomb_flee(player)
        if cmd == "跳过":
            return self._tomb_skip(player)
        # 摸金双排互动
        if cmd == "摸金救援":
            return self._tomb_rescue(player)
        if cmd == "摸金捡取":
            return self._tomb_loot(player)
        if cmd == "摸金传送":
            return self._tomb_transfer(player, tokens)
        # 摸金排行 / 神榜
        if cmd in ("摸金排行", "摸排"):
            return self._tomb_rank(player, group_id)
        if cmd in ("今日摸金神榜", "摸金神榜", "摸榜"):
            return self._tomb_daily_rank(player)
        if cmd == "昨日摸金神榜":
            return self._tomb_yesterday_daily_rank(player)
        if cmd in ("领取摸金奖励", "摸金领奖", "摸领"):
            return self._tomb_claim_daily_reward(player, group_id)
        # 摸金经验兑换
        if cmd in ("摸金兑换", "摸兑"):
            return self._tomb_redeem_exp(player, tokens)

        # ---- 宠物扫雷 ----
        if cmd in ("扫雷", "扫雷介绍", "扫雷帮助", "扫雷游戏"):
            return self._ms_intro()
        if cmd == "开始扫雷":
            return self._ms_start(player, group_id, tokens)
        if cmd == "扫":
            return self._ms_sweep(player, tokens)
        if cmd in ("插旗", "旗"):
            return self._ms_flag(player, tokens)
        if cmd in ("扫雷地图", "扫雷状态"):
            return self._ms_status(player)
        if cmd == "放弃扫雷":
            return self._ms_forfeit(player)
        if cmd == "扫雷排行":
            return self._ms_rank(player)
        if cmd == "扫雷兑换":
            return self._ms_redeem_exp(player, tokens)

        # ---- 宠物家园 ----
        if cmd in ("家园",):
            return self._homestead_menu(player, group_id)
        if cmd in ("家园介绍", "家园教程"):
            return self._homestead_tutorial()
        if cmd == "建造":
            return self._homestead_build(player, tokens)
        if cmd in ("升级", "家园升级"):
            return self._homestead_upgrade(player, tokens)
        if cmd == "家园收取":
            return self._homestead_collect(player)
        if cmd == "家园建筑":
            return self._homestead_buildings(player)
        if cmd in ("拜访家园", "家园拜访"):
            return self._homestead_visit(player, group_id, tokens)
        if cmd == "派遣":
            return self._homestead_dispatch(player, tokens)
        if cmd == "召回":
            return self._homestead_recall(player, tokens)
        if cmd == "派遣状态":
            return self._homestead_dispatch_status(player)
        if cmd in ("顺手牵羊", "偷菜"):
            return self._homestead_steal(player, group_id, tokens)
        if cmd in ("家园排行",):
            return self._homestead_rank(player)
        if cmd in ("家园总排行",):
            return self._homestead_total_rank(player)
        if cmd == "商人购买":
            return self._homestead_merchant_buy(player, tokens)
        if cmd == "拆除":
            return self._homestead_demolish(player, tokens)

        # ---- 宠物银行 ----
        if cmd in ("银行信息", "宠物银行"):
            return self._bank_info(player)
        if cmd == "银行存款":
            return self._bank_deposit(player, tokens)
        if cmd == "银行取款":
            return self._bank_withdraw(player, tokens)
        if cmd == "银行贷款":
            return self._bank_loan(player, tokens)
        if cmd == "银行还款":
            return self._bank_repay(player, tokens)

        # ---- 宠物重生 ----
        if cmd in ("重生",):
            return self._rebirth_info(player)
        if cmd == "购买重生宝石":
            return self._rebirth_buy_gem(player)
        if cmd == "祭奠":
            return self._rebirth_sacrifice(player, tokens)
        if cmd == "确认重生":
            return self._rebirth_confirm(player)

        # ---- 多宠物 ----
        if cmd in ("宠物列表", "查看所有宠物"):
            return self._pet_list(player)
        if cmd == "切换宠物":
            return self._pet_switch(player, tokens)
        if cmd == "宠物信息":
            return self._pet_info(player, tokens)
        if cmd == "放生宠物":
            return self._release(player)

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
    def _broadcast_to_authorized_groups(self, text: str):
        """向所有已授权且记录过 UMO 的群主动推送一条文本消息，返回可等待的任务。"""
        try:
            logger.info(f"[petpark] 提交全服广播任务，文本长度 {len(text)} 字符")
            task = asyncio.get_running_loop().create_task(self._do_broadcast(text))
            self._broadcast_tasks.add(task)
            task.add_done_callback(self._broadcast_tasks.discard)
            return task
        except RuntimeError:
            logger.warning("[petpark] 提交广播任务时未找到运行中的事件循环")
            return None

    async def _do_broadcast(self, text: str) -> dict:
        from astrbot.api.event import MessageChain

        groups = self.store._data.get("groups", {})
        all_gids = list(groups.keys())
        authorized = [
            gid for gid in all_gids
            if self._is_group_authorized(gid) and groups[gid].get("enabled", True)
        ]
        with_umo = [gid for gid in authorized if groups[gid].get("umo")]
        targets = [(gid, groups[gid].get("umo")) for gid in with_umo]

        result = {
            "total_groups": len(all_gids),
            "authorized_groups": len(authorized),
            "umo_ready_groups": len(with_umo),
            "targets": len(targets),
            "sent": 0,
            "failed": 0,
            "errors": [],
        }

        if not targets:
            logger.warning(
                f"[petpark] 没有可广播的授权群（总群 {len(all_gids)}，授权 {len(authorized)}，有 UMO {len(with_umo)}）"
            )
            return result

        logger.info(f"[petpark] 开始向 {len(targets)} 个授权群广播消息")
        for gid, umo in targets:
            try:
                logger.info(f"[petpark] 正在向群 {gid} 广播消息")
                await self.context.send_message(
                    umo, MessageChain().message(text).use_markdown(True)
                )
                result["sent"] += 1
                logger.info(f"[petpark] 已向群 {gid} 广播消息成功")
            except Exception as e:
                logger.warning(f"[petpark] 向群 {gid} Markdown 广播失败：{e}，尝试纯文本降级")
                try:
                    await self.context.send_message(
                        umo, MessageChain().message(text)
                    )
                    result["sent"] += 1
                    logger.info(f"[petpark] 已向群 {gid} 纯文本广播成功")
                except Exception as e2:
                    result["failed"] += 1
                    result["errors"].append(f"{gid}: {e2}")
                    logger.exception(f"[petpark] 向群 {gid} 主动推送失败")

        logger.info(
            f"[petpark] 全服广播完成：目标 {result['targets']}，成功 {result['sent']}，失败 {result['failed']}"
        )
        return result

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
        if boss.get("random_damage"):
            dmg_min = max(1, int(boss.get("random_damage_min", 1)))
            dmg_max = max(dmg_min, int(boss.get("random_damage_max", 10000)))
            player_damage = random.randint(dmg_min, dmg_max)
        else:
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

    def _event_buy(
        self,
        player: dict,
        eid: str,
        cfg: dict,
        item_name: str,
        count: int = 1,
    ) -> str | None:
        shop = cfg.get("shop", {})
        if item_name not in shop:
            return None
        it = shop[item_name]
        token = cfg.get("token", "代币")
        today = time.strftime("%Y-%m-%d")
        self.store.reset_event_daily(player, eid, today)
        count = max(1, int(count))
        cost = it.get("cost", {})
        total_cost = {cur: amt * count for cur, amt in cost.items()}
        for cur, amt in total_cost.items():
            if cur == token:
                if self.store.get_event_token(player, eid, token) < amt:
                    return f"购买 {count} 个『{item_name}』需要 {amt} {token}，余额不足。"
            else:
                if self.store.get_currency(player, cur) < amt:
                    return f"购买 {count} 个『{item_name}』需要 {amt} {cur}，余额不足。"
        per_player = it.get("stock", {}).get("per_player")
        if per_player is not None:
            already = self.store.event_shop_bought(player, eid, item_name)
            if already + count > per_player:
                remain = per_player - already
                if remain <= 0:
                    return f"『{item_name}』每人限购 {per_player} 个。"
                return f"『{item_name}』每人限购 {per_player} 个，你还剩 {remain} 个可买。"
        global_stock = it.get("stock", {}).get("global")
        if global_stock is not None:
            sold = cfg.setdefault("_sold", {}).get(item_name, 0)
            if sold + count > global_stock:
                remain = global_stock - sold
                if remain <= 0:
                    return f"『{item_name}』已售罄。"
                return f"『{item_name}』全球库存剩余 {remain} 个，无法购买 {count} 个。"
        for cur, amt in total_cost.items():
            if cur == token:
                self.store.add_event_token(player, eid, token, -amt)
            else:
                self.store.add_currency(player, cur, -amt)
        self.store.inc_event_shop_bought(player, eid, item_name, count)
        if global_stock is not None:
            cfg["_sold"][item_name] = cfg["_sold"].get(item_name, 0) + count
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
        # 批量购买时按购买数量缩放奖励
        scaled_reward = dict(reward)
        if "count" in scaled_reward:
            scaled_reward["count"] = scaled_reward["count"] * count
        else:
            scaled_reward["count"] = count
        return self._grant_event_reward(
            player, eid, cfg, scaled_reward, prefix=f"购买『{item_name}』x{count} 成功"
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
        entry, is_pity = self._event_gacha_draw_one(player, eid, cfg)
        msg = entry.get("msg", "🎰 抽奖结果")
        pity_text = "\n🎁 **本次为保底奖励！**" if is_pity else ""
        return self._grant_event_reward(player, eid, cfg, entry.get("reward", {}), prefix=msg) + pity_text

    def _event_gacha_draw_one(
        self, player: dict, eid: str, cfg: dict
    ) -> tuple[dict, bool]:
        """单次活动抽奖抽取，返回 (奖池项, 是否保底)。"""
        gacha = cfg.get("gacha", {})
        pool = gacha.get("pool", [])
        pity_cfg = gacha.get("pity", {})
        pity_items = pity_cfg.get("items", []) if pity_cfg.get("enabled") else []

        # 所有保底计数 +1
        for pi in pity_items:
            self.store.inc_event_pity(player, eid, pi.get("name", "保底"))

        # 检查是否触发保底（按配置顺序优先）
        triggered = None
        for pi in pity_items:
            count = self.store.get_event_pity(player, eid, pi.get("name", "保底"))
            if count >= pi.get("threshold", 0) and pi.get("threshold", 0) > 0:
                triggered = pi
                break

        if triggered:
            target_item = triggered.get("reward_item", "")
            for entry in pool:
                if entry.get("reward", {}).get("item") == target_item:
                    self.store.reset_event_pity(player, eid, triggered.get("name", "保底"))
                    return entry, True

        # 正常随机
        weights = [entry.get("weight", 1) for entry in pool]
        entry = random.choices(pool, weights=weights, k=1)[0]

        # 抽到保底物品则清零对应保底计数
        reward_item = entry.get("reward", {}).get("item", "")
        for pi in pity_items:
            if pi.get("reward_item") == reward_item:
                self.store.reset_event_pity(player, eid, pi.get("name", "保底"))

        return entry, False

    def _event_gacha_multi(
        self, player: dict, eid: str, cfg: dict, times: int = 10
    ) -> str:
        """活动抽奖 N 连抽：消耗 times 倍单次价格（是否折扣由活动配置 cost 决定），结果以 Markdown 表格展示。"""
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
        # 十连消耗 = 单次价格 × (times - 1)，即 10 抽只收 9 抽的价格
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
        lines = [
            f"## 🎰 {cmd}{times}连抽结果",
            f"消耗：{' / '.join(f'{v} {k}' for k, v in multi_cost.items())}",
            "",
            "| 序号 | 奖品 |",
            "|---:|:---|",
        ]
        pity_hits = 0
        for i in range(times):
            entry, is_pity = self._event_gacha_draw_one(player, eid, cfg)
            if is_pity:
                pity_hits += 1
            msg = entry.get("msg", "🎰 奖励")
            reward_txt = self._grant_event_reward(
                player, eid, cfg, entry.get("reward", {}), prefix=msg
            )
            cell = reward_txt.replace("\n", "<br>")
            if is_pity:
                cell += " <span style='color:#ff6b6b'>[保底]</span>"
            lines.append(f"| {i + 1} | {cell} |")
        if pity_hits > 0:
            lines.append(f"\n🎁 本次十连触发 {pity_hits} 次保底奖励。")
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
        if cmd == "管理菜单":
            return self._admin_menu_text()
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
            if name in data.SECT_SHOP:
                cfg = data.SECT_SHOP[name]
                cost_type = cfg.get("cost_type", "sect_points")
                if cost_type == "sect_points":
                    price = f"{cfg['points']} 宗门积分（宗主/副宗主可兑换）"
                else:
                    price = f"{cfg['contribution']} 宗门贡献（个人资产，任何人可兑换）"
                reward = ""
                if "item" in cfg:
                    reward = f"获得 {cfg['item']} ×{cfg.get('count', 1)}"
                elif "currency" in cfg:
                    reward = f"获得 {cfg['currency']} +{cfg['amount']}"
                return (
                    f"## 📘 宗门商店·{name}\n"
                    f"{cfg.get('desc', '')}\n\n"
                    f"> 价格：{price}\n"
                    f"> 兑换后：{reward}"
                )
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
            f"🆔 **用户ID**　`{player['qq']}`",
            f"📱 **绑定QQ**　{self._bound_qq_text(player)}",
            f"👥 **群号**　`{gid}`",
            f"🪙 **金币**　{player.get('coin', 0)}",
            f"💎 **积分**　{player.get('jifen', 0)}",
            f"💠 **钻石**　{player.get('diamond', 0)}",
            f"🌀 **深渊结晶**　{self.store.get_abyss_crystal(player)}",
        ]
        streak = player.get("active_streak", 0)
        tax_status = "🟢 转让免税" if streak >= 7 else f"🏷️ 活跃 {streak}/7 天"
        lines.append(f"📅 **活跃**　{tax_status}")
        active = self.store.active_events()
        if active:
            lines.append("━━━━━━━━━━━━━━")
            lines.append("**🎉 活动代币**")
            for eid, cfg in active.items():
                token = cfg.get("token", "代币")
                bal = self.store.get_event_token(player, eid, token)
                lines.append(f"• {cfg.get('name', eid)} {token}：{bal}")
        return "\n".join(lines)

    # =====================================================================
    # QQ 绑定（邮箱验证码 · 跨群通用 · 可用QQ号代替用户ID指定他人）
    # =====================================================================
    def _bound_qq_text(self, player: dict) -> str:
        qq = self.store.get_bound_qq(player.get("qq", ""))
        if qq:
            return f"`{qq}`（✅已绑定）"
        return "未绑定（发送「绑定QQ QQ号」绑定）"

    def _bind_qq(self, player: dict, tokens: list[str], rebind: bool = False) -> str:
        pid = str(player.get("qq", ""))
        cmd_name = "换绑QQ" if rebind else "绑定QQ"
        if len(tokens) < 2:
            return (
                f"用法：{cmd_name} QQ号\n"
                f"● 绑定后向该QQ邮箱发送验证码验证\n"
                f"● 跨群通用，其他群无需重复绑定\n"
                f"● 绑定后可用QQ号代替用户ID指定他人（转让/赠送/PK/拜访等）"
            )
        qq_num = str(tokens[1]).strip()
        if not (qq_num.isdigit() and 5 <= len(qq_num) <= 11):
            return "❌ QQ号格式不正确（应为5~11位纯数字）。"
        current = self.store.get_bound_qq(pid)
        if current and not rebind:
            return f"你已绑定QQ `{current}`，如需更换请发送「换绑QQ 新QQ号」。"
        if current == qq_num and rebind:
            return f"你已绑定该QQ号 `{qq_num}`，无需换绑。"
        other = self.store.find_platform_id_by_qq(qq_num)
        if other and other != pid:
            return f"❌ QQ号 `{qq_num}` 已被其他用户绑定。"
        cfg = self.store.email_config()
        if not cfg.get("smtp_host") or not cfg.get("auth_code"):
            return "❌ 邮箱服务未配置，请联系管理员。"
        # 冷却防刷
        now = int(time.time())
        pend = self._pending_qq_bind.get(pid)
        if pend and now - pend.get("sent_at", 0) < 60:
            remain = 60 - (now - pend.get("sent_at", 0))
            return f"请求过于频繁，请 {remain} 秒后再试。"
        code = "".join(random.choices("0123456789", k=6))
        self._pending_qq_bind[pid] = {
            "code": code,
            "qq": qq_num,
            "expires_at": now + 300,
            "sent_at": now,
        }
        asyncio.create_task(self._send_bind_email_async(qq_num, code))
        return (
            f"📧 验证码已发送至 `{qq_num}@qq.com`\n"
            f"● 请在 **5分钟** 内发送「验证码 123456」完成{cmd_name}\n"
            f"● 验证码仅对本账号有效，请勿泄露给他人"
        )

    async def _send_bind_email_async(self, qq_num: str, code: str) -> None:
        try:
            await asyncio.to_thread(self._smtp_send, f"{qq_num}@qq.com", code)
        except Exception:
            logger.exception("[petpark] 绑定验证码邮件发送失败")

    def _smtp_send(self, to_email: str, code: str) -> bool:
        """同步发送QQ绑定验证码邮件。"""
        import smtplib
        from email.mime.text import MIMEText
        from email.header import Header
        from email.utils import formataddr
        cfg = self.store.email_config()
        host = cfg.get("smtp_host", "smtp.qq.com")
        port = int(cfg.get("smtp_port", 465))
        use_ssl = cfg.get("use_ssl", True)
        username = cfg.get("username", "")
        auth_code = cfg.get("auth_code", "")
        from_email = cfg.get("from_email", username) or username
        sender_name = cfg.get("sender_name", "宠物乐园")
        subject = cfg.get("subject", "[宠物乐园] QQ绑定验证码")
        body_tpl = cfg.get(
            "body_template",
            "您正在绑定QQ号，验证码为：{code}\n有效期 {minutes} 分钟，请勿泄露给他人。",
        )
        body = body_tpl.replace("{code}", code).replace("{minutes}", "5")
        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["From"] = formataddr((sender_name, from_email))
            msg["To"] = to_email
            msg["Subject"] = Header(subject, "utf-8")
            if use_ssl:
                server = smtplib.SMTP_SSL(host, port, timeout=15)
            else:
                server = smtplib.SMTP(host, port, timeout=15)
            try:
                if username and auth_code:
                    server.login(username, auth_code)
                server.sendmail(from_email, [to_email], msg.as_string())
            finally:
                server.quit()
            logger.info(f"[petpark] 绑定验证码邮件发送成功: {to_email}")
            return True
        except Exception as e:
            logger.error(f"[petpark] 绑定验证码邮件发送失败: {e}")
            return False

    def _verify_qq_code(self, player: dict, tokens: list[str]) -> str:
        pid = str(player.get("qq", ""))
        if len(tokens) < 2:
            return "用法：验证码 123456"
        code = str(tokens[1]).strip()
        pend = self._pending_qq_bind.get(pid)
        if not pend:
            return "❌ 你还没有请求验证码，请先发送「绑定QQ QQ号」。"
        if int(time.time()) > pend.get("expires_at", 0):
            self._pending_qq_bind.pop(pid, None)
            return "❌ 验证码已过期，请重新发送「绑定QQ QQ号」。"
        if pend.get("code", "") != code:
            return "❌ 验证码错误。"
        qq_num = pend.get("qq", "")
        self.store.set_qq_binding(pid, qq_num)
        self._pending_qq_bind.pop(pid, None)
        return (
            f"✅ 绑定成功！\n"
            f"● 用户ID `{pid}` ↔ QQ号 `{qq_num}`\n"
            f"● 跨群通用，其他群无需重复绑定\n"
            f"● 现在可以用QQ号代替用户ID指定他人（转让/赠送/PK/拜访等）"
        )

    def _unbind_qq(self, player: dict) -> str:
        pid = str(player.get("qq", ""))
        if not self.store.get_bound_qq(pid):
            return "你还没有绑定QQ号。"
        self.store.unbind_qq(pid)
        return "✅ 已解除QQ绑定。"

    def _accept_invite(self, player: dict, group_id: str, tokens: list[str]) -> str:
        """被邀请用户发送『受邀 用户ID』，双方均在本群时发放邀请奖励。"""
        if len(tokens) < 2:
            return "⚠️ 用法：`受邀 用户ID`（例如：受邀 7FC131A00B...）"
        inviter_qq = str(tokens[1]).strip()
        inviter_qq = self._resolve_user_token(inviter_qq)
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
        # 自动修炼卡走专用兑换
        card = self.store.cards().get(code.upper())
        if card and int(card.get("auto_cultivation_days", 0) or 0) > 0:
            return self._redeem_auto_cultivation_card(player, group_id, qq, tokens)
        rewards, items, err = self.store.redeem_card(code, player, used_by)
        if rewards is None and items is None:
            return f"❌ 兑换失败：{err}"
        lines = [
            "## 🎉 兑换成功",
            "━━━━━━━━━━━━━━",
            f"🎟 **卡密**　`{code.upper()}`",
        ]
        for cur, amt in (rewards or {}).items():
            lines.append(f"✅ **获得**　{cur} +{amt}")
            lines.append(f"💼 **当前{cur}**　{self.store.get_currency(player, cur)}")
        for name, cnt in (items or {}).items():
            lines.append(f"📦 **获得道具**　{name} ×{cnt}")
        lines.append("━━━━━━━━━━━━━━")
        return "\n".join(lines)

    def _redeem_auto_cultivation_card(
        self, player: dict, group_id: str, qq: str, tokens: list[str]
    ) -> str:
        if len(tokens) < 2 or not tokens[1].strip():
            return "⚠️ 用法：`修炼卡 卡密`（例如：修炼卡 ABCD23XY...）"
        code = tokens[1].strip()
        used_by = self.store.make_key(group_id, qq)
        days, err = self.store.redeem_auto_cultivation_card(code, player, used_by)
        if days is None:
            return f"❌ 使用失败：{err}"
        until = player["auto_cultivation"]["card_until"]
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(until))
        return (
            f"## 🧘 自动修炼卡使用成功\n"
            f"━━━━━━━━━━━━━━\n"
            f"获得 {days} 天自动修炼权限\n"
            f"到期时间：{when}\n"
            f"发送『开启自动修炼』即可开始挂机"
        )

    def _pay_link(self) -> str:
        return (
            "## 💎 宠物乐园 · 充值中心\n"
            "━━━━━━━━━━━━━━\n"
            "🛒 **商店链接**：https://pay.ldxp.cn/shop/2P5XIVMD\n\n"
            "📌 **购买后请复制卡密，然后在本群发送**：\n"
            "```\n兑换 你的卡密\n```\n"
            "例如：`兑换 ABCD1234EFGH`\n\n"
            "卡密可兑换金币、积分、钻石或系统道具，具体以商品说明为准。"
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
            event_lines.append("【限时活动】")
            for cfg in active.values():
                menu = cfg.get("menu_cmd", "活动菜单")
                event_lines.append(f"- {cfg.get('name', '活动')}：发送 `{menu}` 查看")
            event_lines.append("")
        return "\n".join(
            [
                "## 宠物乐园 · 指令菜单",
                "",
                "【入门】",
                "- 砸蛋 · 购买宠物 · 我的宠物 · 宠物状态",
                "- 宠物改名 · 宠物变性 · 赠送宠物 QQ · 放生宠物",
                "- 锁定宠物 · 解锁宠物（锁定后无法放生/赠送，防误操作）",
                "- 宠物侦查 用户ID",
                "",
                "【多宠物】💡 默认2席位 | 最多10 | 重生+1席位 | 宠物席位卡+1",
                "- 宠物列表 · 查看所有宠物（查看所有宠物概要）",
                "- 切换宠物 序号（切换到指定宠物）",
                "- 宠物信息 序号（查看指定宠物详情）",
                "- 放生宠物（放生当前宠物，最后一只不可放生）",
                "- 赠送宠物 QQ（赠送当前宠物）",
                "",
                "【商城 / 背包】",
                "- 宠物商城 · 道具商城 · 宠物市场",
                "- 查看背包 · 购买 物品 数量 · 使用 物品",
                "- 出售 物品 数量 · 丢弃 物品 数量",
                "- 转让 用户ID 物品 数量 · 清空背包",
                "- 查看说明 物品名",
                "",
                "【喂养 / 日常】（各 10~20 分钟冷却）",
                "- 喂食 物品 · " + " · ".join(data.DAILY_ACTIONS),
                "",
            ]
            + event_lines
            + [
                "【成长】",
                "- 一键升级宠物 · 宠物升级 次数 · 宠物进化",
                "- 宠物飞升 · 宠物渡劫 · 幻境寻宝 · 宠物神仙劫",
                "- 合成卡 目标卡名",
                "> 每突破 60 级赠史诗卡；10 张低品质卡可合成为高一级卡",
                "",
                "【神器 / 秘技】",
                "- 打造神器 名称 · 佩戴神器 名称 · 卸下神器",
                "- 参悟秘技 名称 · 遗忘秘技",
                "",
                "【天赋 / 炼丹】",
                "- 宠物觉醒 · 制作天赋符 · 使用天赋符 天赋",
                "- 炼丹 · 使用仙丹 名称 用户ID 数量",
                "- 治愈 用户ID · 复活 用户ID · 精力转移 用户ID 值",
                "",
                "【对战 / 排行】",
                "- 宠物攻击 用户ID · 跨群挑战宠物 群号 用户ID",
                "- 宠物排行（本群）· 宠物神榜（全服）· 领取神榜奖励",
                "",
                "【副本 / 任务】（副本 15 分钟冷却）",
                "- 宠物副本 · 进入副本 名称",
                "- 深渊秘境 · 深渊介绍 · 深渊商店 · 深渊祝福",
                "- 宠物剧情任务 · 领取任务 名称 · 提交任务 名称",
                "- 我的剧情任务 · 取消剧情任务",
                "",
                "【宠物摸金】（独立财富系统）",
                "- 摸金 · 摸金商店 · 购买摸金道具 名称",
                "- 我的摸金 · 进入摸金 难度(1~4)",
                "- 摸金移动 方向 · 摸金探索 · 摸金开箱",
                "- 摸金使用 名称 · 摸金撤离 · 放弃摸金",
                "- 摸金排行 · 今日摸金神榜 · 昨日摸金神榜",
                "- 领取摸金奖励 · 摸金兑换",
                "- 摸金组队 用户ID · 摸金准备 · 摸金队伍 · 摸金取消组队（双排）",
                "- 摸金救援 · 摸金捡取 · 摸金传送（双排互动）",
                "",
                "【宠物扫雷】（全服积分排行）",
                "- 扫雷介绍 · 开始扫雷 难度(1~4)",
                "- 扫 坐标（支持多扫，如：扫a1b2）· 插旗 坐标",
                "- 扫雷地图 · 放弃扫雷 · 扫雷排行 · 扫雷兑换",
                "",
                "【宠物家园】（放置建造 · 离线产出）",
                "> 在家园中建造建筑，随时间自动累积金币和积分，离线也产。",
                "- 家园 · 建造 建筑名 · 升级 建筑名",
                "- 派遣 建筑名 · 召回 建筑名 · 派遣状态",
                "- 家园收取 · 家园建筑 · 商人购买 编号",
                "- 拆除 建筑名（返还20%费用）",
                "- 拜访家园 QQ · 顺手牵羊 QQ（偷菜）",
                "- 家园排行 · 家园总排行",
                "> 🏗️ 7种建筑：金币矿/积分工坊/聚宝盆/经验泉/仓库/哨塔/祈福坛",
                "> 🐾 宠物派遣：驻扎建筑提升产量，等级品质越高加成越多",
                "> 💀 偷菜：拼成功率偷别人未收资源，建哨塔可防御",
                "> 🧳 流浪商人：收取时概率出现，可买加速券/护院符/双倍券",
                "",
                "【宠物银行】（存款生息 · 信用贷款）",
                "> 在银行存钱赚利息，信用好可低息贷款，逾期将冻结账户！",
                "- 宠物银行 · 银行信息",
                "- 银行存款 金币/积分 数量",
                "- 银行取款 金币/积分 数量",
                "- 银行贷款 金币/积分 数量 [7/14/30天]",
                "- 银行还款 金币/积分 数量（或 全部）",
                "> 💰 活期存款：周利率 1%，随时存取，每周一自动计息",
                "> 📋 信用贷款：初始额度 10 万，信用分越高额度越大",
                "> ⭐ 信用分：初始 500，按时还款 +10~30，逾期扣分",
                "> 🚫 逾期超 7 天：冻结所有游戏功能，还清自动解冻",
                "> ⚠️ 有贷款期间：无法赠送宠物、转让物品和货币",
                "",
                "【宠物重生】（涅槃新生 · 属性暴击）",
                "> 渡劫 Lv800 进入准备期，Lv999 可重生。",
                "- 重生 · 购买重生宝石 · 祭奠 积分/钻石 数量",
                "- 确认重生（需重生宝石 + Lv999）",
                "> 💎 重生宝石：1万钻石 + 10万积分",
                "> 🔥 祭奠：消耗积分/钻石提升高倍率概率",
                "> 🎲 属性暴击：2~10×随机（2×最高概率）",
                "> ⛔ 准备期（Lv800+）：禁止出售/转让/丢弃物品",
                "> 📦 重生后保留：品质卡、定制卡（其余清空）",
                "",
                "【姻缘】",
                "- 宠物追求 用户ID · 同意追求 用户ID",
                "- 宠物求婚 用户ID · 同意求婚 用户ID",
                "- 宠物分手 · 宠物离婚 · 宠物恋情",
                "",
                "【个人】",
                "- 我的信息 · 签到 · 我要氪金",
                "- 兑换 卡密 · 赠送金币/积分/钻石 用户ID 数量",
                "- 我的邀请情况 · 受邀 用户ID",
                "- 绑定QQ QQ号 · 验证码 123456 · 换绑QQ · 解绑QQ",
                "> 绑定QQ后可用QQ号代替用户ID指定他人，跨群通用",
                "",
                "【图鉴】",
                "- 宠物种类 · 属性 · 状态 · 神器 · 秘技 · 仙丹 · 天赋",
                "- 查看说明 名称",
                "",
                "> 指令均无需前缀，直接发送即可。需指定对方时直接填 用户ID。",
                "> 管理员指令请发送 `管理菜单` 查看。",
            ]
        )

    def _admin_menu_text(self) -> str:
        return "\n".join(
            [
                "## 宠物乐园 · 管理菜单",
                "",
                "【群开关】",
                "- 开启宠物乐园 · 关闭宠物乐园",
                "- 开启宠物跨群 · 关闭宠物跨群",
                "",
                "【货币管理】（大/小管理员）",
                "- 加金币 用户ID 数量 · 减金币 用户ID 数量",
                "- 加积分 用户ID 数量 · 减积分 用户ID 数量",
                "- 加钻石 用户ID 数量 · 减钻石 用户ID 数量",
                "> 小管理员仅可增减金币/积分，加币有每日额度上限",
                "",
                "【小管理员】",
                "- 任命小管理 用户ID · 撤销小管理 用户ID",
                "- 小管理列表（大管理员查看全服）",
                "- 我的管理额度（小管理员查看今日额度）",
                "",
                "【群授权】",
                "- 授权状态（查看本群授权状态）",
                "- 授权 卡密（用授权卡激活/续期本群）",
                "- 授权本群 天数（大管理员直接续期）",
                "",
                "【群管理】（群主/群管理员）",
                "- 禁言 @成员 时长（如 10分钟 / 1小时 / 1天，默认 10 分钟）",
                "- 解除禁言 @成员",
                "- 全体禁言（查询当前全员禁言状态）",
                "> 需机器人被设为群管理员；新成员入群/退群会自动推送通知",
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
        slots = player.get("pet_slots", data.PET_SLOTS_DEFAULT)
        if len(player.get("pets", [])) >= slots:
            return (
                f"宠物席位已满（{len(player['pets'])}/{slots}），无法获取新宠物。\n"
                "请先 `放生宠物` 或使用 `宠物席位卡` 扩容。"
            )
        cd = self._cooldown_block(player, "砸蛋", "砸蛋")
        if cd:
            return cd
        species = random.choice(data.SPECIES_NAMES)
        quality = self._roll_quality()
        new_p = petmod.new_pet(species, quality)
        if not self._add_pet(player, new_p):
            return "添加宠物失败，席位异常。"
        self.store.set_cooldown(player, "砸蛋", data.EGG_COOLDOWN)
        return (
            f"💥 **砸蛋成功！**\n获得 【{quality}】品质的 **{species}**！\n"
            "> 发送 `我的宠物` 查看详情。"
        )

    def _buy_pet(self, player: dict, tokens: list[str]) -> str:
        slots = player.get("pet_slots", data.PET_SLOTS_DEFAULT)
        if len(player.get("pets", [])) >= slots:
            return (
                f"宠物席位已满（{len(player['pets'])}/{slots}），无法获取新宠物。\n"
                "请先 `放生宠物` 或使用 `宠物席位卡` 扩容。"
            )
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
        new_p = petmod.new_pet(species, quality)
        if not self._add_pet(player, new_p):
            self.store.add_currency(player, "积分", cost)  # 退款
            return "添加宠物失败，席位异常，已退款。"
        return f"✅ **购买成功！** 花费 {cost} 积分获得 【{quality}】品质的 **{species}**。"

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

    # ------------------------------------------------------------------
    # 多宠物系统：辅助方法
    # ------------------------------------------------------------------
    def _add_pet(self, player: dict, new_pet: dict) -> bool:
        """添加宠物到玩家宠物列表，自动切换。成功返回 True。"""
        player.setdefault("pets", [])
        slots = player.get("pet_slots", data.PET_SLOTS_DEFAULT)
        if len(player["pets"]) >= slots:
            return False
        new_pet.setdefault("pet_id", str(int(time.time())) + "_" + __import__("secrets").token_hex(4))
        player["pets"].append(new_pet)
        player["active_pet"] = len(player["pets"]) - 1
        player["pet"] = new_pet  # 实时引用
        return True

    def _remove_pet(self, player: dict, index: int) -> dict | None:
        """移除指定索引的宠物，返回被移除的宠物或 None。"""
        pets = player.get("pets", [])
        if index < 0 or index >= len(pets):
            return None
        removed = pets.pop(index)
        if not pets:
            player["active_pet"] = -1
            player["pet"] = None
        else:
            if player["active_pet"] >= len(pets):
                player["active_pet"] = len(pets) - 1
            player["pet"] = pets[player["active_pet"]]
        return removed

    def _switch_pet(self, player: dict, index: int) -> bool:
        """切换活跃宠物到指定索引。"""
        pets = player.get("pets", [])
        if index < 0 or index >= len(pets):
            return False
        player["active_pet"] = index
        player["pet"] = pets[index]
        return True

    def _custom_image_md(self, pet: dict) -> str | None:
        """如果宠物有已审核通过的定制图，返回可在 QQ Markdown 里使用的图片语法串。"""
        filename = pet.get("custom_image")
        if not filename:
            return None
        host = str(self.config.get("web_host", "103.38.83.146"))
        if host in ("0.0.0.0", "127.0.0.1", "localhost"):
            host = "103.38.83.146"
        port = int(self.config.get("web_port", 7799))
        url = f"http://{host}:{port}/custom_images/{urllib.parse.quote(filename)}"
        return f"![定制形象 #{images._IMG_DISPLAY} #{images._IMG_DISPLAY}]({url})"

    def _my_pet(self, player: dict):
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物，发送『砸蛋』或『宠物市场』获取一只吧！"
        image_md = self._custom_image_md(p) or images.pet_image_md(p.get("species"))
        return petmod.render_pet(p), image_md

    def _inspect(self, group_id: str, tokens: list[str]):
        target = self._arg(tokens, 1)
        if not target:
            return "用法：宠物侦查 用户ID"
        tp, err = self._find_target(group_id, target)
        if err:
            return err
        if not tp.get("pet") and not tp.get("pets"):
            return "对方还没有宠物。"
        pet = tp.get("pet") or (tp.get("pets", [{}])[0] if tp.get("pets") else None)
        if not pet:
            return "对方还没有宠物。"
        image_md = self._custom_image_md(pet) or images.pet_image_md(pet.get("species"))
        return petmod.render_pet(pet), image_md

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
        if p.get("locked"):
            return "🔒 宠物已锁定，无法赠送。如需赠送请先发送『解锁宠物』。"
        # 检查对方宠物席位
        tp_slots = tp.get("pet_slots", data.PET_SLOTS_DEFAULT)
        if len(tp.get("pets", [])) >= tp_slots:
            return f"对方宠物席位已满（{len(tp['pets'])}/{tp_slots}），无法接收。"
        # 银行：有未还贷款无法赠送
        bank_block = self._bank_block_check(player, "gift")
        if bank_block:
            return bank_block
        # 转让限制检查
        limit_err, _ = self._check_transfer_limit(player, tp, group_id, 1, "pet")
        if limit_err:
            return limit_err
        removed = self._remove_pet(player, player.get("active_pet", 0))
        if not removed:
            return "移除宠物失败。"
        if not self._add_pet(tp, removed):
            self._add_pet(player, removed)  # 回滚
            return "对方接收失败（席位异常），已退还。"
        return f"🎁 已将『{removed['nickname']}』赠送给 `{target}`。"

    def _release(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if p.get("locked"):
            return f"🔒 『{p['nickname']}』已锁定，无法放生。如需放生请先发送『解锁宠物』。"
        if len(player.get("pets", [])) <= 1:
            return "⚠️ 这是你最后一只宠物，不能放生。请先获取新宠物再放生。"
        idx = player.get("active_pet", 0)
        removed = self._remove_pet(player, idx)
        if not removed:
            return "放生失败。"
        return f"已放生『{removed['nickname']}』，江湖再见。"

    def _lock_pet(self, player: dict, lock: bool) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if lock:
            if p.get("locked"):
                return f"🔒 『{p['nickname']}』已处于锁定状态。"
            p["locked"] = True
            return f"🔒 已锁定『{p['nickname']}』，锁定期间无法放生或赠送，发送『解锁宠物』可解除。"
        if not p.get("locked"):
            return f"『{p['nickname']}』当前未锁定。"
        p["locked"] = False
        return f"🔓 已解锁『{p['nickname']}』，现在可以放生或赠送了。"

    # ------------------------------------------------------------------
    # 多宠物系统：指令处理器
    # ------------------------------------------------------------------
    def _pet_list(self, player: dict) -> str:
        """查看所有宠物概要（MD 表格）。"""
        pets = player.get("pets", [])
        if not pets:
            return "你还没有宠物，发送『砸蛋』或『宠物市场』获取一只吧！"
        slots = player.get("pet_slots", data.PET_SLOTS_DEFAULT)
        active_idx = player.get("active_pet", 0)
        lines = [
            f"## 🐾 宠物列表（{len(pets)}/{slots} 席位）",
            "",
            "| # | 昵称 | 种类 | 品质 | 阶段 | 等级 | 战力 | 备注 |",
            "|:--:|:--:|:--:|:--:|:--:|:--:|--:|:--|",
        ]
        for i, pet in enumerate(pets):
            petmod.refresh_energy(pet)
            bp = petmod.battle_power(pet)
            notes = []
            if i == active_idx:
                notes.append("👈当前")
            if pet.get("locked"):
                notes.append("🔒")
            note_str = " ".join(notes) if notes else "—"
            lines.append(
                f"| {i+1} | {pet.get('nickname','?')} | {pet.get('species','?')} | "
                f"{pet.get('quality','?')} | {pet.get('stage','?')} | "
                f"Lv{pet.get('level',1)} | {bp:,} | {note_str} |"
            )
        lines.append("")
        lines.append("> 发送 `切换宠物 序号` 切换活跃宠物")
        lines.append("> 发送 `宠物信息 序号` 查看详细信息")
        return "\n".join(lines)

    def _pet_switch(self, player: dict, tokens: list[str]) -> str:
        """切换活跃宠物。"""
        pets = player.get("pets", [])
        if not pets:
            return "你还没有宠物。"
        if len(tokens) < 2:
            # 无参数：展示列表让用户选择
            return self._pet_list(player)
        try:
            num = int(tokens[1])
        except ValueError:
            return "用法：切换宠物 序号（例如：切换宠物 1）"
        idx = num - 1  # 用户输入 1-based，内部 0-based
        if idx < 0 or idx >= len(pets):
            return f"无效序号，请输入 1~{len(pets)}。"
        old = pets[player.get("active_pet", 0)] if player.get("active_pet", 0) < len(pets) else None
        old_name = old.get("nickname", "?") if old else "?"
        if not self._switch_pet(player, idx):
            return "切换失败。"
        new_pet = pets[idx]
        return f"✅ 已切换宠物：**{old_name}** → **{new_pet.get('nickname', '?')}**（{new_pet.get('species','?')} Lv{new_pet.get('level',1)}）"

    def _pet_info(self, player: dict, tokens: list[str]) -> str:
        """查看指定宠物的完整信息。"""
        pets = player.get("pets", [])
        if not pets:
            return "你还没有宠物。"
        if len(tokens) < 2:
            # 默认显示当前活跃宠物
            p = self._need_pet(player)
            if not p:
                return "你还没有宠物。"
        else:
            try:
                num = int(tokens[1])
            except ValueError:
                return "用法：宠物信息 序号（例如：宠物信息 1）"
            idx = num - 1
            if idx < 0 or idx >= len(pets):
                return f"无效序号，请输入 1~{len(pets)}。"
            p = pets[idx]
        image_md = self._custom_image_md(p) or images.pet_image_md(p.get("species"))
        return petmod.render_pet(p), image_md

    def _pet_release(self, player: dict, tokens: list[str]) -> str:
        """放生指定序号的宠物。"""
        pets = player.get("pets", [])
        if not pets:
            return "你还没有宠物。"
        if len(tokens) < 2:
            return "用法：放生宠物 序号（例如：放生宠物 2）\n> 发送 `宠物列表` 查看所有宠物及序号。"
        try:
            num = int(tokens[1])
        except ValueError:
            return "用法：放生宠物 序号（例如：放生宠物 2）"
        idx = num - 1
        if idx < 0 or idx >= len(pets):
            return f"无效序号，请输入 1~{len(pets)}。"
        if len(pets) <= 1:
            return "⚠️ 这是你最后一只宠物，不能放生。请先获取新宠物再放生。"
        pet = pets[idx]
        if pet.get("locked"):
            return f"🔒 『{pet.get('nickname', '?')}』已锁定，无法放生。请先发送『解锁宠物』。"
        name = pet.get("nickname", "?")
        removed = self._remove_pet(player, idx)
        if not removed:
            return "放生失败。"
        return f"已放生『{name}』，江湖再见。"

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
            return f"改名成功！消耗『改名卡』x1、精力 10 点，现在它叫『{name}』。"
        p["nickname"] = name
        return f"改名成功！现在它叫『{name}』。"

    def _change_gender(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if not self.store.remove_item(player, "变性药水"):
            return "需要『变性药水』才能变性（可在宠物商城购买）。"
        p["gender"] = "女" if p["gender"] == "男" else "男"
        return f"变性成功！消耗『变性药水』x1，『{p['nickname']}』现在是{p['gender']}生了。"

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
        return f"『{p['nickname']}』已满血复活！消耗『九转还魂丹』x1。"

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

    def _feed(self, player: dict, group_id: str, tokens: list[str]) -> str:
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
            extra = ""
            if p.get("love_target"):
                tp = self.store.get_player(p["love_target"], group_id, create=False)
                if tp and tp.get("pet"):
                    tp["pet"]["favor"] = min(
                        data.FAVOR_MAX, tp["pet"]["favor"] + 50
                    )
                    extra = f"\n伴侣 {p['love_target']} 的好感度也 +50。"
            return f"喂食相思豆，好感度 +50，当前 {p['favor']}。" + extra
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
        count = self._parse_count(tokens, 2)
        # 优先检查活动商店
        for eid, cfg in self.store.active_events().items():
            if name in cfg.get("shop", {}):
                return self._event_buy(player, eid, cfg, name, count) or "购买失败。"
        if name not in data.ITEMS:
            return f"商城没有『{name}』。发送『宠物商城』或『道具商城』查看。"
        it = data.ITEMS[name]
        if it["price"] <= 0:
            return f"『{name}』无法直接购买。"
        cost = it["price"] * count
        if self.store.get_currency(player, it["currency"]) < cost:
            return f"购买 {count} 个『{name}』需 {cost} {it['currency']}，余额不足。"
        self.store.add_currency(player, it["currency"], -cost)
        self.store.add_item(player, name, count)
        return f"购买成功：{name} x{count}，花费 {cost} {it['currency']}。"

    def _use_item(self, player: dict, tokens: list[str]) -> str:
        if len(tokens) < 2:
            return "用法：使用 物品 数量"
        name = tokens[1]
        # 宠物席位卡：玩家级别效果，无需有宠物即可使用
        it_check = data.ITEMS.get(name)
        if it_check and it_check.get("effect", {}).get("add_pet_slot"):
            if not self.store.has_item(player, name):
                return f"背包里没有『{name}』。"
            count = self._parse_count(tokens, 2)
            if not self.store.has_item(player, name, count):
                return f"背包里『{name}』数量不足。"
            slots = player.get("pet_slots", data.PET_SLOTS_DEFAULT)
            total_add = count * it_check["effect"]["add_pet_slot"]
            if slots >= data.PET_SLOTS_MAX:
                return f"宠物席位已达上限 {data.PET_SLOTS_MAX}，无法继续使用。"
            new_slots = min(data.PET_SLOTS_MAX, slots + total_add)
            actual_add = new_slots - slots
            player["pet_slots"] = new_slots
            self.store.remove_item(player, name, count)
            return f"✅ 使用『{name}』x{count}：宠物席位 +{actual_add}！当前席位上限：{player['pet_slots']}。"
        p = self._need_pet(player)
        if not p:
            return "你没有宠物，无法使用物品。"
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
                return f"📜 参悟成功！消耗秘技书『{name}』x1，习得秘技『{name}』，战力 +{s['power']}。"
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
        # 品质提升卡：每次 1 张，史诗及以上无法使用
        if "upgrade_quality" in eff:
            target = eff["upgrade_quality"]
            ok, msg = petmod.upgrade_quality(p, target)
            if not ok:
                return msg
            self.store.remove_item(player, name, 1)
            return f"使用『{name}』x1：{msg}"
        # 神秘宝箱：随机开出金币/积分/道具
        if "mystery_box" in eff:
            self.store.remove_item(player, name, count)
            results = []
            for _ in range(count):
                roll = random.random()
                if roll < 0.30:
                    amt = random.randint(500, 2000)
                    self.store.add_currency(player, "金币", amt)
                    results.append(f"金币 +{amt}")
                elif roll < 0.60:
                    amt = random.randint(100, 500)
                    self.store.add_currency(player, "积分", amt)
                    results.append(f"积分 +{amt}")
                elif roll < 0.80:
                    self.store.add_item(player, "普通经验书", 1)
                    results.append("普通经验书 ×1")
                elif roll < 0.95:
                    self.store.add_item(player, "进化神石", 1)
                    results.append("进化神石 ×1")
                else:
                    self.store.add_item(player, "史诗卡", 1)
                    results.append("史诗卡 ×1")
            return f"使用『{name}』x{count}：\n" + "\n".join(f"  {r}" for r in results)
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
        # 丹药类（药品/仙丹）禁止售卖
        if it.get("category") in ("药品", "仙丹"):
            return f"『{name}』属于丹药类物品，无法出售。"
        gain = int(it["price"] * 0.2) * count
        self.store.remove_item(player, name, count)
        self.store.add_currency(player, it["currency"], gain)
        return f"出售 {name} x{count}，获得 {gain} {it['currency']}（20% 回收价）。"

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
        # 银行：有未还贷款无法转让
        bank_block = self._bank_block_check(player, "transfer")
        if bank_block:
            return bank_block
        limit_err, tax_rate = self._check_transfer_limit(player, tp, group_id, count, "item")
        if limit_err:
            return limit_err
        if not self.store.has_item(player, name, count):
            return f"背包里『{name}』数量不足。"
        tax_count = max(1, int(count * tax_rate)) if tax_rate > 0 else 0
        receive_count = count - tax_count
        self.store.remove_item(player, name, count)
        self.store.add_item(tp, name, receive_count)
        if tax_rate == 0:
            if count <= 3:
                tax_info = "（🆓 少量免税）"
            else:
                tax_info = "（🟢 活跃免税）"
        elif tax_rate > data.TRANSFER_TAX_ITEM:
            tax_info = f"（税 {tax_count} 个，{tax_rate:.0%} ⚠️ 高频同用户）"
        else:
            tax_info = f"（税 {tax_count} 个，{tax_rate:.0%}）"
        return f"📦 已转让 {name} ×{receive_count} 给 `{target}`{tax_info}。"

    def _gift_currency(
        self, player: dict, group_id: str, cmd: str, tokens: list[str]
    ) -> str:
        # 赠送金币/积分/钻石 用户ID 数量
        currency = cmd.replace("赠送", "")
        tx_type = {"金币": "coin", "积分": "jifen", "钻石": "diamond"}.get(currency, "coin")
        if len(tokens) < 3:
            return f"用法：{cmd} 用户ID 数量（单次上限 {data.TRANSFER_PER_TX_MAX}）"
        target = self._arg(tokens, 1)
        if not target:
            return f"用法：{cmd} 用户ID 数量"
        if not tokens[2].isdigit():
            return "数量必须为正整数。"
        count = max(1, int(tokens[2]))
        tp, err = self._find_target(group_id, target)
        if err:
            return err
        # 银行：有未还贷款无法赠送货币
        bank_block = self._bank_block_check(player, "gift")
        if bank_block:
            return bank_block
        limit_err, tax_rate = self._check_transfer_limit(player, tp, group_id, count, tx_type)
        if limit_err:
            return limit_err
        have = self.store.get_currency(player, currency)
        if have < count:
            return f"你的{currency}不足（需要 {count}，当前 {have}）。"
        tax_amount = int(count * tax_rate) if tax_rate > 0 else 0
        receive_amount = count - tax_amount
        self.store.add_currency(player, currency, -count)
        if receive_amount > 0:
            self.store.add_currency(tp, currency, receive_amount)
        if tax_rate == 0:
            tax_info = "（🟢 活跃免税）"
        elif tax_rate > data.TRANSFER_TAX_COIN:
            tax_info = f"（税 {tax_amount}，{tax_rate:.0%} ⚠️ 高频同用户）"
        else:
            tax_info = f"（税 {tax_amount}，{tax_rate:.0%}）"
        return f"💰 已向 `{target}` 赠送 {currency} ×{receive_amount}{tax_info}。"

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
        if action in ("修炼", "双修") and self._pet_is_ascended(p):
            return "宠物已飞升，凡间的『修炼/双修』已无法带来增益，请通过『幻境寻宝』『宠物神仙劫』获取仙元。"
        if action == "双修" and p.get("love_state") != "已婚":
            return "『双修』需与伴侣结为夫妻才行，先通过『宠物求婚 / 同意求婚』结婚吧（单身/恋爱中可用『修炼』）。"
        if action == "修炼" and p.get("love_state") == "已婚":
            return "你已结婚，解锁了更高效的『双修』，请使用『双修』代替『修炼』。"
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
            base = random.randint(50, 120) + p["level"] * 15
            exp = base * (2 if action == "双修" else 1)
            petmod.add_exp(p, exp)
            if action == "双修":
                self._inc_stat(player, "shuangxiu")
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
        prep_msg = self._rebirth_prep_reminder(p, before)
        return (
            f"⬆ 一键升级 +{n} 级！当前 Lv{p['level']}/{petmod.level_cap(p)}，剩余精力 {p['energy']}。"
            + reward + prep_msg
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
        prep_msg = self._rebirth_prep_reminder(p, before)
        return (
            f"⬆ 升级 +{n} 级！当前 Lv{p['level']}/{petmod.level_cap(p)}。{suffix}"
            + reward + prep_msg
        )

    def _evolve(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        if not self.store.has_item(player, "进化神石", 1):
            return (
                "背包里没有『进化神石』，无法进化。\n"
                "> 可在商城购买（7200 积分），"
                "或通过剧情任务『探索秘境』获得。"
            )
        before = list(p.get("skills", [])) + (
            [p["artifact"]] if p.get("artifact") else []
        )
        ok, msg = petmod.evolve(p)
        if ok:
            self.store.remove_item(player, "进化神石", 1)
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
        cd = self._cooldown_block(player, "渡劫", "宠物渡劫")
        if cd:
            return cd
        ok, msg = petmod.tribulation(p)
        if not ok and msg.startswith("💥"):
            self.store.set_cooldown(player, "渡劫", data.TRIBULATION_FAIL_COOLDOWN)
            msg += f"\n‣ 天劫余威未散，{data.TRIBULATION_FAIL_COOLDOWN // 60} 分钟后才可再次渡劫。"
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
        cd = self._cooldown_block(player, "fantasy_treasure", "幻境寻宝")
        if cd:
            return cd
        if p["energy"] < data.ASCEND_TREASURE["energy"]:
            return f"精力不足（需 {data.ASCEND_TREASURE['energy']}）。"
        p["energy"] -= data.ASCEND_TREASURE["energy"]
        x = random.randint(*data.ascend_treasure_xianyuan(p["level"]))
        jifen_text = ""
        if random.random() < data.ASCEND_TREASURE.get("jifen_chance", 1.0):
            j = random.randint(*data.ASCEND_TREASURE["jifen"])
            self.store.add_currency(player, "积分", j)
            jifen_text = f"积分 +{j}，"
        petmod.add_xianyuan(p, x)
        self._inc_stat(player, "ascended_fantasy_treasure")
        cooldown = random.randint(*data.ASCEND_TREASURE["cooldown"])
        self.store.set_cooldown(player, "fantasy_treasure", cooldown)
        return f"✨ 幻境寻宝：{jifen_text}仙元 +{x}！下次可探索时间：{self._fmt_duration(cooldown)}后。"

    def _immortal_calamity(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        if data.STAGES.index(p["stage"]) < data.STAGES.index("飞升"):
            return "宠物飞升后才能挑战神仙劫。"
        cd = self._cooldown_block(player, "immortal_calamity", "宠物神仙劫")
        if cd:
            return cd
        if p["energy"] < 50:
            return "精力不足（需 50）。"
        p["energy"] -= 50
        self.store.set_cooldown(
            player, "immortal_calamity", random.randint(*data.DAILY_COOLDOWN_RANGE)
        )
        if random.random() < 0.5:
            x = random.randint(*data.ascend_treasure_xianyuan(p["level"]))
            petmod.add_xianyuan(p, x)
            self._inc_stat(player, "ascended_immortal_calamity")
            return f"⚡ 神仙劫渡过，仙元 +{x}！"
        p["hp"] = max(1, p["hp"] // 2)
        return "⚡ 神仙劫失败，宠物身受重伤，恢复后再来。"

    def _exp_to_xianyuan(self, player: dict) -> str:
        """飞升后宠物可手动把当前经验余额按 10万:1 兑换成仙元。"""
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        if data.STAGES.index(p["stage"]) < data.STAGES.index("飞升"):
            return "只有宠物飞升后才能把经验兑换成仙元。"
        exp = p.get("exp", 0)
        rate = data.ASCEND_XIANYUAN_PER_EXP
        gain = exp // rate
        if gain <= 0:
            need = rate - (exp % rate)
            return f"当前经验 {exp} 不足以兑换 1 仙元，还差 {need} 经验。"
        p["exp"] = exp % rate
        p["xianyuan"] = p.get("xianyuan", 0) + gain
        return f"🌟 兑换成功！消耗 {gain * rate} 经验，获得 {gain} 仙元。当前仙元 {p['xianyuan']}，剩余经验 {p['exp']}。"

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
        self._inc_stat(player, "forge_artifact")
        return (
            f"⚒ 打造成功！消耗 {cost['jifen']} 积分、"
            f"『{cost['material']}』x{cost['material_count']}、"
            f"『{cost.get('blueprint', '神器图纸')}』x{cost.get('blueprint_count', 1)}，"
            f"『{name}』已放入背包，可『佩戴神器 {name}』。"
        )

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
    @staticmethod
    def _pet_is_ascended(p: dict) -> bool:
        return data.STAGES.index(p["stage"]) >= data.STAGES.index("飞升")

    def _charge_cost(self, player: dict, p: dict, cost: dict, action: str) -> tuple[str | None, str]:
        """按消耗表检查并扣除（jifen/exp/xianyuan/energy）。

        返回 (不足时的提示 | None, 消耗描述文本)。
        """
        parts = []
        if cost.get("jifen"):
            parts.append(f"{cost['jifen']} 积分")
        if cost.get("exp"):
            parts.append(f"{cost['exp']} 经验")
        if cost.get("xianyuan"):
            parts.append(f"{cost['xianyuan']} 仙元")
        if cost.get("energy"):
            parts.append(f"{cost['energy']} 精力")
        cost_text = "、".join(parts)
        if cost.get("jifen") and self.store.get_currency(player, "积分") < cost["jifen"]:
            return f"{action}需要 {cost_text}。", cost_text
        if cost.get("exp") and p["exp"] < cost["exp"]:
            return f"{action}需要 {cost_text}。", cost_text
        if cost.get("xianyuan") and p.get("xianyuan", 0) < cost["xianyuan"]:
            return f"{action}需要 {cost_text}（当前仙元 {p.get('xianyuan', 0)}）。", cost_text
        if cost.get("energy") and p["energy"] < cost["energy"]:
            return f"{action}需要 {cost_text}。", cost_text
        if cost.get("jifen"):
            self.store.add_currency(player, "积分", -cost["jifen"])
        if cost.get("exp"):
            p["exp"] -= cost["exp"]
        if cost.get("xianyuan"):
            p["xianyuan"] = p.get("xianyuan", 0) - cost["xianyuan"]
        if cost.get("energy"):
            p["energy"] -= cost["energy"]
        return None, cost_text

    def _awaken(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        cd = self._cooldown_block(player, "觉醒", "宠物觉醒")
        if cd:
            return cd
        # 飞升后觉醒改用仙元，避免经验/积分在飞升后大幅贬值
        is_ascended = self._pet_is_ascended(p)
        c = data.ASCEND_AWAKEN_COST if is_ascended else data.AWAKEN_COST
        if data.STAGES.index(p["stage"]) < data.STAGES.index(c["stage"]):
            return f"觉醒要求宠物达到【{c['stage']}】阶段。"
        if p["level"] < c["level"]:
            return f"觉醒要求等级 Lv{c['level']}。"
        if p["energy"] < c["energy"]:
            return f"觉醒要求 {c['energy']} 点精力。"
        if is_ascended:
            if p.get("xianyuan", 0) < c["xianyuan"]:
                return f"觉醒要求 {c['xianyuan']} 仙元（当前 {p.get('xianyuan', 0)}）。"
            p["xianyuan"] -= c["xianyuan"]
            cost_text = f"{c['xianyuan']} 仙元"
        else:
            if p["exp"] < c["exp"]:
                return f"觉醒要求 {c['exp']} 经验（当前 {p['exp']}）。"
            if self.store.get_currency(player, "积分") < c["jifen"]:
                return f"觉醒要求 {c['jifen']} 积分。"
            p["exp"] -= c["exp"]
            self.store.add_currency(player, "积分", -c["jifen"])
            cost_text = f"{c['exp']} 经验、{c['jifen']} 积分"
        p["energy"] -= c["energy"]
        self.store.set_cooldown(
            player, "觉醒", random.randint(*data.CRAFT_COOLDOWN_RANGE)
        )
        # 可觉醒天赋（非定制宠物不能觉醒"需定制"的天赋）
        pool = [
            n
            for n, v in data.TALENTS.items()
            if p.get("custom") or not v["need_custom"]
        ]
        old = p.get("talent")
        p["talent"] = random.choice(pool)
        cover = f"（覆盖原天赋 {old}）" if old else ""
        return (
            f"🌟 觉醒成功！消耗 {cost_text}、{c['energy']} 点精力，"
            f"获得天赋【{p['talent']}】{cover}\n{data.TALENTS[p['talent']]['desc']}"
        )

    def _make_rune(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        if not p.get("talent"):
            return "宠物还没有觉醒天赋，无法制符。"
        cd = self._cooldown_block(player, "制符", "制作天赋符")
        if cd:
            return cd
        c = (
            data.ASCEND_TALENT_RUNE_MAKE_COST
            if self._pet_is_ascended(p)
            else data.TALENT_RUNE_MAKE_COST
        )
        err, cost_text = self._charge_cost(player, p, c, "制符")
        if err:
            return err
        self.store.set_cooldown(
            player, "制符", random.randint(*data.CRAFT_COOLDOWN_RANGE)
        )
        rune = f"{p['talent']}符"
        self.store.add_item(player, rune, 1)
        return (
            f"🪬 制符成功！消耗 {cost_text}，"
            f"获得『{rune}』，可『使用天赋符 {p['talent']}』赋予其它宠物该天赋。"
        )

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
        cd = self._cooldown_block(player, "使用天赋符", "使用天赋符")
        if cd:
            return cd
        c = (
            data.ASCEND_TALENT_RUNE_USE_COST
            if self._pet_is_ascended(p)
            else data.TALENT_RUNE_USE_COST
        )
        err, cost_text = self._charge_cost(player, p, c, "使用天赋符")
        if err:
            return err
        self.store.set_cooldown(
            player, "使用天赋符", random.randint(*data.CRAFT_COOLDOWN_RANGE)
        )
        self.store.remove_item(player, rune)
        old = p.get("talent")
        p["talent"] = talent
        cover = f"（覆盖原天赋 {old}）" if old else ""
        return (
            f"🪬 使用天赋符成功！消耗 {cost_text}"
            f"与『{rune}』x1，宠物获得天赋【{talent}】{cover}。"
        )

    def _refine_elixir(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        if p.get("talent") != "绝影丹心":
            return "需要觉醒『绝影丹心』天赋的宠物才能炼丹。"
        cd = self._cooldown_block(player, "炼丹", "炼丹")
        if cd:
            return cd
        c = (
            data.ASCEND_ELIXIR_CRAFT_COST
            if self._pet_is_ascended(p)
            else data.ELIXIR_CRAFT_COST
        )
        err, cost_text = self._charge_cost(player, p, c, "炼丹")
        if err:
            return err
        self.store.set_cooldown(
            player, "炼丹", random.randint(*data.CRAFT_COOLDOWN_RANGE)
        )
        elixir = random.choice(data.ELIXIR_NAMES)
        self.store.add_item(player, elixir, 1)
        return (
            f"⚗ 炼丹成功！消耗 {cost_text}，"
            f"提炼出『{elixir}』x1！\n{data.ELIXIRS[elixir]['desc']}"
        )

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
        exp = random.randint(150, 600) + attacker["level"] * 3
        # 七星化海：额外经验
        if attacker.get("talent") == "七星化海":
            exp = int(exp * (1 + random.uniform(0.1, 0.3)))
        petmod.add_exp(attacker, exp)
        ap_player.setdefault("stats", {})["battle_win"] = (
            ap_player["stats"].get("battle_win", 0) + 1
        )
        if petmod._is_ascended(attacker):
            self._inc_stat(ap_player, "ascended_battle_win")
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
        cd = self._cooldown_block(player, "对战", "宠物攻击")
        if cd:
            return cd
        petmod.refresh_energy(p)
        if p["energy"] < self.attack_energy:
            return f"发起攻击需要 {self.attack_energy} 点精力（当前 {p['energy']}）。"
        p["energy"] -= self.attack_energy
        self.store.set_cooldown(
            player, "对战", random.randint(*data.BATTLE_COOLDOWN_RANGE)
        )
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
        cd = self._cooldown_block(player, "对战", "跨群挑战宠物")
        if cd:
            return cd
        petmod.refresh_energy(p)
        if p["energy"] < self.attack_energy:
            return f"发起挑战需要 {self.attack_energy} 点精力（当前 {p['energy']}）。"
        p["energy"] -= self.attack_energy
        self.store.set_cooldown(
            player, "对战", random.randint(*data.BATTLE_COOLDOWN_RANGE)
        )
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
        prep_msg = self._rebirth_prep_reminder(p, before)
        return (
            f"\n⬆ **自动升级 +{gained} 级！** 当前 "
            f"Lv{p['level']}/{petmod.level_cap(p)}（剩余精力 {p['energy']}）"
        ) + self._grant_level60_reward(player, p, before) + prep_msg

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
            if petmod._is_ascended(p):
                self._inc_stat(player, "ascended_dungeon_clear")
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
    # 飞升副本：挑战神仙
    # =====================================================================
    def _ascend_dungeon_list(self) -> str:
        lines = [
            "## 🏔 飞升副本",
            "> 飞升后解锁，挑战各路神仙获取仙元。",
            "> 消耗 **30** 精力，冷却 **20 分钟**。",
            "",
        ]
        for lv in sorted(data.ASCEND_DUNGEONS.keys()):
            d = data.ASCEND_DUNGEONS[lv]
            low, high = d["xianyuan"]
            lines.append(
                f"- **{d['name']}** `Lv{lv}`　战力 {d['power']}\n"
                f"　　仙元 {low}~{high}　积分 {d['jifen']}"
            )
        lines.append("\n> 使用 `挑战神仙 等级` 进入对应副本（如 `挑战神仙 120`）。")
        return "\n".join(lines)

    def _enter_ascend_dungeon(self, player: dict, tokens: list[str]) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        if data.STAGES.index(p["stage"]) < data.STAGES.index("飞升"):
            return "只有宠物飞升后才能挑战神仙。"
        if len(tokens) < 2:
            return "用法：挑战神仙 等级（如：挑战神仙 120）"
        try:
            level = int(tokens[1])
        except ValueError:
            return "请输入正确的等级数字（120/130/.../220）。"
        if level not in data.ASCEND_DUNGEONS:
            return "飞升副本等级为 120~220，每 10 级一档。"
        d = data.ASCEND_DUNGEONS[level]
        if p["level"] < d["level_req"]:
            return f"挑战『{d['name']}』需要宠物达到 Lv{d['level_req']}。"
        cd = self._cooldown_block(player, "ascend_dungeon", "挑战神仙")
        if cd:
            return cd
        petmod.refresh_energy(p)
        if p["energy"] < d["energy"]:
            return f"精力不足（需 {d['energy']}，当前 {p['energy']}）。"
        p["energy"] -= d["energy"]
        self.store.set_cooldown(player, "ascend_dungeon", data.ASCEND_DUNGEON_COOLDOWN)
        return self._ascend_dungeon_battle(player, p, level, d)

    def _ascend_dungeon_battle(self, player: dict, p: dict, level: int, d: dict) -> str:
        monster = d["name"]
        power = d["power"]
        my_power = petmod.battle_power(p)
        # 战力 ±10% 浮动后比拼神仙战力
        roll = int(my_power * random.uniform(0.9, 1.1))
        win = roll >= power
        nick = p["nickname"]
        head = f"## ⚔ {nick} VS {monster}"
        next_time = time.strftime(
            "%Y/%m/%d %H:%M:%S",
            time.localtime(time.time() + data.ASCEND_DUNGEON_COOLDOWN),
        )
        if win:
            xianyuan_gain = random.randint(*d["xianyuan"])
            jifen_gain = d["jifen"]
            petmod.add_xianyuan(p, xianyuan_gain)
            self.store.add_currency(player, "积分", jifen_gain)
            self._inc_stat(player, "ascended_dungeon_clear")
            drop_text = ""
            drop = d.get("drop")
            if drop and random.random() < drop["chance"]:
                self.store.add_item(player, drop["item"], drop.get("count", 1))
                drop_text = f"\n●掉落道具：{drop['item']} ×{drop.get('count', 1)}"
            body = (
                "┏-★---飞☆升---★-┓\n"
                f"●神仙战力：{power}\n"
                f"●你的战力：{roll}\n"
                f"●获得仙元：{xianyuan_gain}\n"
                f"●获得积分：{jifen_gain}{drop_text}\n"
                f"●下次时间：{next_time}\n"
                "┗-★---信☆息---★-┛"
            )
            return f"{head}\n✨ 你的『{nick}』击败『{monster}』，获得仙缘！\n{body}{self._auto_level_note(player, p)}"
        # 失败惩罚：损失一半血量
        p["hp"] = max(1, p["hp"] // 2)
        body = (
            "┏-★---飞☆升---★-┓\n"
            f"●神仙战力：{power}\n"
            f"●你的战力：{roll}\n"
            "●战败，宠物身受重伤，无仙元奖励。\n"
            f"●宠物血量：{p['hp']}/{p['hp_max']}\n"
            f"●下次时间：{next_time}\n"
            "┗-★---信☆息---★-┛"
        )
        return f"{head}\n💥 你的『{nick}』不敌『{monster}』，请恢复后再战。\n{body}"

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
        if petmod._is_ascended(p):
            self._inc_stat(player, "ascended_abyss")

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
            "深渊秘境是一个**低门槛、高频次、看运气** 的副本玩法。\n"
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

    @staticmethod
    def _quest_req_met(player: dict, quest: dict) -> bool:
        """检查玩家是否满足任务的领取/提交前提。"""
        p = player.get("pet")
        if not p:
            return False
        req = quest.get("req", {})
        stage = req.get("stage")
        if stage and data.STAGES.index(p.get("stage", "")) < data.STAGES.index(stage):
            return False
        level = req.get("level")
        if level and p.get("level", 0) < level:
            return False
        return True

    @staticmethod
    def _quest_req_text(quest: dict) -> str:
        """把任务前提转成可读文本。"""
        req = quest.get("req", {})
        parts = []
        if "stage" in req:
            parts.append(f"阶段≥{req['stage']}")
        if "level" in req:
            parts.append(f"等级≥Lv{req['level']}")
        return "、".join(parts) if parts else "无"

    @staticmethod
    def _quest_reward_text(reward: dict) -> str:
        """把奖励字典转成可读文本。"""
        parts = []
        if "jifen" in reward:
            parts.append(f"积分+{reward['jifen']}")
        if "exp" in reward:
            parts.append(f"经验+{reward['exp']}")
        if "xianyuan" in reward:
            parts.append(f"仙元+{reward['xianyuan']}")
        if "item" in reward:
            count = reward.get("item_count", 1)
            parts.append(f"{reward['item']}×{count}")
        return "、".join(parts) if parts else "无"

    def _quest_list(self, player: dict) -> str:
        lines = [
            "## 📜 可领取剧情任务",
            "> `领取任务 任务名` 领取，完成后 `提交任务 任务名`",
            "",
        ]
        for n, q in data.QUESTS.items():
            locked = not self._quest_req_met(player, q)
            need = "、".join(f"{k}×{v}" for k, v in q["need"].items()) or "直接领取"
            rwd = self._quest_reward_text(q.get("reward", {}))
            req = self._quest_req_text(q)
            lock_mark = "🔒 " if locked else ""
            lines.append(
                f"- **{lock_mark}{n}**\n"
                f"　　🔒 前提：{req}　🎯 {need}　🎁 {rwd}"
            )
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
            ) or "已完成前置，可直接提交"
            lines.append(f"- **{n}**：{prog}")
        return "\n".join(lines)

    def _handle_quest(self, player: dict, tokens: list[str], cmd: str) -> str:
        if len(tokens) < 2:
            return f"用法：{cmd} 任务名称"
        name = tokens[1]
        if name not in data.QUESTS:
            return f"没有名为『{name}』的剧情任务。"
        quest = data.QUESTS[name]
        need = quest["need"]
        stats = player.get("stats", {})
        if cmd == "领取任务":
            if not self._quest_req_met(player, quest):
                req = self._quest_req_text(quest)
                return f"❌ 你尚未满足领取条件：{req}。"
            if name in player.get("quests", {}):
                return f"『{name}』已在进行中。"
            # 记录领取时的进度快照，任务进度从领取时刻起算
            player.setdefault("quests", {})[name] = {k: stats.get(k, 0) for k in need}
            return f"已领取剧情任务『{name}』。"
        # 提交任务
        if name not in player.get("quests", {}):
            return f"你尚未领取『{name}』。"
        if not self._quest_req_met(player, quest):
            req = self._quest_req_text(quest)
            return f"❌ 你尚未满足提交条件：{req}。"
        base = player["quests"][name]
        base = base if isinstance(base, dict) else {}
        if any(stats.get(k, 0) - base.get(k, 0) < v for k, v in need.items()):
            return "任务目标尚未完成。"
        reward = quest["reward"]
        for k, v in reward.items():
            if k == "jifen":
                self.store.add_currency(player, "积分", v)
            elif k == "exp" and player.get("pet"):
                petmod.add_exp(player["pet"], v)
            elif k == "xianyuan" and player.get("pet"):
                petmod.add_xianyuan(player["pet"], v)
            elif k == "item":
                self.store.add_item(player, v, reward.get("item_count", 1))
        player["quests"].pop(name, None)
        return f"✅ 提交『{name}』成功！获得奖励：{self._quest_reward_text(reward)}。"

    # =====================================================================
    # 宠物摸金（独立财富系统）
    # =====================================================================
    def _tomb_key(self, group_id: str, qq: str) -> str:
        return self.store.make_key(group_id, qq)

    def _tomb_persist(self) -> None:
        """持久化当前所有活跃的摸金 session 和双排队伍（插件重载后恢复）。"""
        self.store.save_tomb_runstate(
            self._tomb_sessions, self._tomb_coop_teams, self._tomb_coop_index
        )

    def _tomb_in_raid(self, player: dict) -> bool:
        """检查玩家是否正在进行活跃的摸金探险（单人或已开局的 coop）。"""
        key = self._tomb_key(player.get("group", ""), player.get("qq", ""))
        if key in self._tomb_sessions:
            return True
        coop = self._tomb_get_coop(player)
        return coop is not None and coop.get("active", False)

    # ---- 命运卡牌 ----
    def _tomb_draw_cards(self) -> list[str]:
        """随机抽取 3 张不同的命运卡牌。"""
        return random.sample(list(data.TOMB_DESTINY_CARDS.keys()), 3)

    def _get_card_effect(self, session: dict, key: str, default=1.0):
        """读取当前命运卡牌的效果值。session 为单人 session 或 coop 共享部分。"""
        card_name = session.get("destiny_card", "")
        if not card_name:
            return default
        return data.TOMB_DESTINY_CARDS.get(card_name, {}).get("effects", {}).get(key, default)

    def _tomb_apply_entry_card_effects(self, session: dict, card_name: str):
        """开局即时生效的命运卡牌效果（起始冥币/HP/时间/撤离要求等）。"""
        eff = data.TOMB_DESTINY_CARDS[card_name]["effects"]
        if "start_mingbi" in eff:
            session["mingbi"] = session.get("mingbi", 0) + eff["start_mingbi"]
        if "start_hp_mod" in eff:
            session["hp"] = max(1, session["hp"] + eff["start_hp_mod"])
            session["hp_max"] = max(1, session["hp_max"] + eff["start_hp_mod"])
        if "time_mult" in eff:
            new_limit = int(session["time_limit"] * eff["time_mult"])
            session["time_limit"] = new_limit
            session["deadline"] = session["started_at"] + new_limit
        if "required_mult" in eff:
            session["required"] = int(session["required"] * eff["required_mult"])

    # ---- 双排辅助 ----
    _TOMB_PLAYER_KEYS = frozenset({
        "player_pos", "prev_pos", "visited", "hp", "hp_max",
        "escapes", "weapon", "weapon_attack", "inventory",
        "buffs", "pending", "status", "stunned", "mingbi",
    })
    _TOMB_SHARED_KEYS = frozenset({
        "started_at", "difficulty", "map", "required", "time_limit",
        "deadline", "image", "leader", "teammate", "active", "group_id",
    })

    def _tomb_get_coop(self, player: dict) -> dict | None:
        """返回该玩家所在的双排队伍 session，不在队伍中返回 None。"""
        key = self._tomb_key(player.get("group", ""), player.get("qq", ""))
        coop_key = self._tomb_coop_index.get(key)
        if coop_key and coop_key in self._tomb_coop_teams:
            return self._tomb_coop_teams[coop_key]
        return None

    def _tomb_is_in_coop(self, player: dict) -> bool:
        return self._tomb_get_coop(player) is not None

    def _tomb_prepare(self, player: dict) -> tuple[dict | None, bool]:
        """为当前玩家准备虚拟 session。返回 (session, is_coop)。双排时构造虚拟单人 session。"""
        import copy
        qq = str(player.get("qq", ""))
        coop = self._tomb_get_coop(player)
        if coop and coop.get("active"):
            virtual = {}
            for k in self._TOMB_SHARED_KEYS:
                if k in coop:
                    virtual[k] = coop[k] if k != "map" else dict(coop["map"])
            pdata = coop.get("players", {}).get(qq, {})
            for k in self._TOMB_PLAYER_KEYS:
                if k in pdata:
                    val = pdata[k]
                    virtual[k] = copy.deepcopy(val) if isinstance(val, (dict, set, list)) else val
            # 命运卡牌共享字段 + 玩家私有字段
            for extra_k in ("destiny_card", "destiny_choices"):
                if extra_k in coop:
                    virtual[extra_k] = coop[extra_k]
            if "_auto_revive_used" in pdata:
                virtual["_auto_revive_used"] = pdata["_auto_revive_used"]
            virtual["_coop_parent"] = coop
            virtual["_is_coop"] = True
            virtual["_coop_self_qq"] = qq
            return virtual, True
        key = self._tomb_key(player.get("group", ""), player.get("qq", ""))
        session = self._tomb_sessions.get(key)
        return session, False

    def _tomb_commit(self, player: dict, virtual: dict, is_coop: bool) -> None:
        """将虚拟 session 中的玩家私有字段写回双排父 session，并持久化。"""
        if is_coop:
            coop = virtual.get("_coop_parent")
            if coop:
                # 防御：若 coop 已被结算并移出索引，避免继续写回已分离的 dict
                coop_key = self._tomb_key(coop.get("group_id", ""), coop.get("leader", ""))
                if coop_key in self._tomb_coop_teams and self._tomb_coop_teams[coop_key] is coop:
                    qq = str(player.get("qq", ""))
                    if qq in coop.get("players", {}):
                        pdata = coop["players"][qq]
                        import copy
                        for k in self._TOMB_PLAYER_KEYS:
                            if k in virtual:
                                val = virtual[k]
                                pdata[k] = copy.deepcopy(val) if isinstance(val, (dict, set, list)) else val
                        # 命运卡牌私有字段同步回玩家数据
                        for extra_k in ("_auto_revive_used",):
                            if extra_k in virtual:
                                pdata[extra_k] = virtual[extra_k]
                        # 同步可能被 _tomb_refresh_map 更新的共享字段
                        if "image" in virtual:
                            coop["image"] = virtual["image"]
        # 每次提交后都持久化，确保插件重载后摸金进度不丢失
        self._tomb_persist()

    def _tomb_teammate_qq(self, player: dict, coop: dict = None) -> str:
        """返回该玩家在双排队伍中的队友 QQ，不在双排返回空字符串。"""
        if coop is None:
            coop = self._tomb_get_coop(player)
        if not coop:
            return ""
        qq = str(player.get("qq", ""))
        return coop.get("teammate") if qq == coop.get("leader") else coop.get("leader", "")

    # ---- 双排辅助结束 ----

    def _tomb_pending_battle(self, session: dict) -> bool:
        pending = session.get("pending")
        return bool(pending and pending.get("type") in ("M", "B"))

    # --------------------------- 双排组队 ---------------------------
    def _tomb_team_invite(self, player: dict, group_id: str, tokens: list[str]) -> str:
        """摸金组队 用户ID —— 邀请队友组建双排队伍。"""
        if self._tomb_session_exists(player):
            return "你已在摸金中，请先结束当前探险（摸撤/摸弃）。"
        target_qq = self._arg(tokens, 1)
        if not target_qq:
            return "用法：摸金组队 用户ID"
        my_qq = str(player.get("qq", ""))
        if target_qq == my_qq:
            return "不能邀请自己组队。"
        tp, err = self._find_target(group_id, target_qq)
        if err:
            return err
        # 清理指向已不存在队伍的过期索引，避免邀请被旧数据阻塞
        for qq in (my_qq, target_qq):
            old_key = self._tomb_coop_index.get(self._tomb_key(group_id, qq))
            if old_key and old_key not in self._tomb_coop_teams:
                self._tomb_coop_index.pop(self._tomb_key(group_id, qq), None)
        if self._tomb_is_in_coop(player):
            return "你已经有队伍了，发送「摸金取消组队」退出当前队伍。"
        if self._tomb_is_in_coop(tp):
            return f"用户 {target_qq} 已经在另一个队伍中了。"
        if self._tomb_session_exists(tp):
            return f"用户 {target_qq} 正在摸金中，无法组队。"
        coop_key = self._tomb_key(group_id, my_qq)
        if coop_key in self._tomb_coop_teams:
            return "你已经创建了一个队伍，等待对方回应。"
        self._tomb_coop_teams[coop_key] = {
            "leader": my_qq,
            "teammate": target_qq,
            "ready": set(),
            "active": False,
            "group_id": str(group_id),
        }
        self._tomb_coop_index[self._tomb_key(group_id, my_qq)] = coop_key
        self._tomb_coop_index[self._tomb_key(group_id, target_qq)] = coop_key
        self._tomb_persist()
        return (
            f"## 摸金组队\n"
            f"队长 {my_qq} 邀请 {target_qq} 组队摸金！\n\n"
            f"双方发送「摸金准备」确认，队长发送「摸进 难度」开始。"
        )

    def _tomb_team_ready(self, player: dict) -> str:
        """摸金准备 —— 确认准备就绪。"""
        coop = self._tomb_get_coop(player)
        if not coop:
            return "你不在任何队伍中，发送「摸金组队 用户ID」创建队伍。"
        if coop.get("active"):
            return "队伍已经在摸金中了。"
        qq = str(player.get("qq", ""))
        coop.setdefault("ready", set()).add(qq)
        ready_list = "、".join(coop["ready"])
        if len(coop["ready"]) >= 2:
            return (
                f"## 全队就绪\n"
                f"队长 {coop['leader']} 可以发送「摸进 难度」开始探险！\n"
                f"已就绪：{ready_list}"
            )
        return f"你已就绪！等待队友准备...\n已就绪：{ready_list}"

    def _tomb_team_cancel(self, player: dict) -> str:
        """摸金取消组队 —— 离开或解散队伍。"""
        coop = self._tomb_get_coop(player)
        if not coop:
            return "你不在任何队伍中。"
        if coop.get("active"):
            return "探险已经开始，不能取消组队。发送「摸弃」放弃探险。"
        gid = coop.get("group_id", "")
        for pqq in (coop.get("leader", ""), coop.get("teammate", "")):
            self._tomb_coop_index.pop(self._tomb_key(gid, pqq), None)
        coop_key = self._tomb_key(gid, coop.get("leader", ""))
        self._tomb_coop_teams.pop(coop_key, None)
        self._tomb_persist()
        return f"## 队伍已解散\n{player.get('qq', '')} 取消了组队。"

    def _tomb_team_status(self, player: dict) -> str:
        """摸金队伍 —— 查看当前队伍状态。"""
        coop = self._tomb_get_coop(player)
        if not coop:
            return "你不在任何队伍中。发送「摸金组队 用户ID」创建队伍。"
        ready_set = coop.get("ready", set())
        state = "探险中" if coop.get("active") else "等待开始"
        return (
            f"## 摸金队伍\n"
            f"状态：{state}\n"
            f"队长：{coop.get('leader', '?')}\n"
            f"队友：{coop.get('teammate', '?')}\n"
            f"已就绪：{'、'.join(ready_set) if ready_set else '无'}"
        )

    def _tomb_team_validate_leader(self, player: dict, action: str = "") -> str | None:
        """验证玩家是否为队长，返回 None 表示通过，否则返回错误信息。"""
        coop = self._tomb_get_coop(player)
        if not coop:
            return None
        if str(player.get("qq", "")) != coop.get("leader", ""):
            return f"只有队长 {coop['leader']} 可以{action or '执行此操作'}。"
        return None
    # --------------------------- 双排组队结束 ---------------------------

    def _tomb_intro(self) -> str:
        diffs = data.TOMB_DIFFICULTIES
        return (
            "## 宠物摸金\n"
            "在独立墓穴中探索、战斗、开箱，并在时限内把指定数量的「冥币」带到出口撤离。\n\n"
            "【核心规则】\n"
            "- 拥有独立血量与战力，不影响宠物本体\n"
            "- 摸金等级无上限，每次成功或失败都获得经验\n"
            f"- 简单/普通免费进入；困难需1张棺椁令；噩梦需2张\n"
            f"- 棺椁令可在商店购买，每张 {data.TOMB_EXTRA_TOKEN_COST} 冥币\n"
            f"- 每局结束后冷却 {data.TOMB_COOLDOWN // 60} 分钟\n"
            "- 阵亡时，装备背包中的武器和道具全部掉落\n\n"
            "【难度要求】\n"
            f"- 简单：Lv{diffs[1]['tomb_level_req']}\n"
            f"- 普通：Lv{diffs[2]['tomb_level_req']}\n"
            f"- 困难：Lv{diffs[3]['tomb_level_req']}，需1张棺椁令\n"
            f"- 噩梦：Lv{diffs[4]['tomb_level_req']}，需2张棺椁令\n\n"
            "【常用指令】\n"
            "准备：摸店  摸买  摸带  摸装  摸包\n"
            "进入：摸进 难度\n"
            "移动：上 / 下 / 左 / 右  或  摸看\n"
            "交互：开箱  战斗  祭拜  逃跑  跳过  摸用\n"
            "状态：摸态  摸撤  摸弃\n\n"
            "【双排模式】\n"
            "- 摸金组队 用户ID — 邀请队友组队\n"
            "- 摸金准备 — 确认准备\n"
            "- 摸金队伍 — 查看队伍状态\n"
            "- 摸金取消组队 — 解散队伍\n"
            "- 摸金救援 — 救援倒地队友（3格内）\n"
            "- 摸金捡取 — 捡取倒地队友物品\n"
            "- 摸金传送 用户ID 物品/冥币 数量 — 传送物品"
        )

    def _tomb_shop(self) -> str:
        lines = ["## 摸金商店", "仅消耗冥币，与主背包完全隔离。", ""]
        lines.append("【武器】决定摸金战力，有耐久，阵亡全部掉落")
        for name, info in data.TOMB_WEAPONS.items():
            lines.append(f"- {name}：{info['price']} 冥币　攻击+{info['attack']}　耐久{info['durability']}")
        lines.append("")
        lines.append("【道具】")
        for name, info in data.TOMB_ITEMS.items():
            lines.append(f"- {name}：{info['price']} 冥币　{info['desc']}")
        lines.append("")
        lines.append("> 购买：「摸买 道具名 [数量]」\n"
                     "> 道具默认存入储物柜，需「摸带 道具名」带入装备背包后才能在局内使用\n"
                     "> 武器需「摸带 武器名」放入装备背包，再「摸装 武器名」装备")
        return "\n".join(lines)

    def _tomb_buy(self, player: dict, tokens: list[str]) -> str:
        if self._tomb_in_raid(player):
            return "摸金过程中不能购买东西，请先结束本局（摸撤/摸弃）。"
        if len(tokens) < 2:
            return "用法：摸买 道具名 [数量]"
        name = tokens[1]
        count = self._parse_count(tokens, 2) if len(tokens) > 2 else 1
        if name in data.TOMB_WEAPONS:
            # 武器按名称唯一存储（有耐久），不支持批量购买
            count = 1
            price = data.TOMB_WEAPONS[name]["price"]
            total = price * count
            if self.store.get_tomb_mingbi(player) < total:
                return f"冥币不足（需 {total}，当前 {self.store.get_tomb_mingbi(player)}）。"
            self.store.add_tomb_mingbi(player, -total)
            self.store.add_tomb_weapon(player, name, "storage")
            return f"🗡 已购买『{name}』，消耗 {total} 冥币（存入储物柜）。发送 `摸带 {name}` 带入装备背包，再 `摸装 {name}` 装备。"
        if name not in data.TOMB_ITEMS:
            return f"摸金商店没有『{name}』。"
        price = data.TOMB_ITEMS[name]["price"]
        total = price * count
        if self.store.get_tomb_mingbi(player) < total:
            return f"冥币不足（需 {total}，当前 {self.store.get_tomb_mingbi(player)}）。"
        self.store.add_tomb_mingbi(player, -total)
        if data.TOMB_ITEMS[name].get("effect") == "main_bag_item":
            self.store.add_item(player, name, count)
            return f"🏺 已购买『{name}』×{count}，消耗 {total} 冥币（已进入主背包）。"
        self.store.add_tomb_item(player, name, count, "storage")
        return f"🏺 已购买『{name}』×{count}，消耗 {total} 冥币（存入储物柜）。发送 `摸带 {name}` 带入装备背包。"

    def _tomb_status_outside(self, player: dict) -> str:
        st = self.store.tomb_state(player)
        stats = st.get("stats", {})
        token = data.TOMB_EXTRA_TOKEN
        storage = st.get("storage_items", {})
        equip = st.get("equip_items", {})
        token_count = storage.get(token, 0) + equip.get(token, 0)
        level = st.get("level", 1)
        exp = st.get("exp", 0)
        need = data.tomb_exp_to_next(level)
        equipped = st.get("equipped_weapon", "")
        weapons = st.get("weapons", {})
        wep_text = (
            f"{equipped}（攻击+{data.TOMB_WEAPONS[equipped]['attack']}）"
            if equipped and equipped in data.TOMB_WEAPONS
            else "徒手"
        )
        weapons_text = "、".join(
            f"{k}(耐久{w.get('durability')})" for k, w in weapons.items()
        ) or "无"
        storage_text = "、".join(f"{k}×{v}" for k, v in storage.items() if v > 0) or "空"
        equip_text = "、".join(f"{k}×{v}" for k, v in equip.items() if v > 0) or "空"
        cooldown_ts = st.get("cooldown", 0)
        now = int(time.time())
        if cooldown_ts > now:
            remain = cooldown_ts - now
            m, s = divmod(remain, 60)
            cd_text = f"冷却中，{m}分{s:02d}秒后可再次进入"
        else:
            cd_text = "可进入"
        pending_exp = st.get("pending_pet_exp", 0)
        pending_line = (
            f"待兑换宠物经验：{pending_exp}（发送「摸金兑换」领取）\n"
            if pending_exp > 0
            else ""
        )
        return (
            "## 我的摸金\n"
            f"等级：  Lv{level}（{exp}/{need}）\n"
            f"战力：  {data.tomb_player_attack(level, data.TOMB_WEAPONS.get(equipped, {}).get('attack', 0) if equipped else 0)}\n"
            f"状态：  {cd_text}\n"
            f"冥币：  {st.get('mingbi', 0)}\n\n"
            "【装备】\n"
            f"当前武器：{wep_text}\n"
            f"拥有武器：{weapons_text}\n\n"
            "【背包】\n"
            f"装备背包（带入墓中，失败掉落）：{equip_text}\n"
            f"储物柜（安全保管）：{storage_text}\n\n"
            "【道具】\n"
            f"棺椁令：{token_count} 张（困难需1张，噩梦需2张）\n\n"
            "【统计】\n"
            f"总次数：{stats.get('raids', 0)}  成功：{stats.get('success', 0)}  失败：{stats.get('fail', 0)}\n"
            f"历史带出冥币：{stats.get('total_mingbi', 0)}\n"
            f"{pending_line}"
        )

    def _tomb_format_card_choices(self, choices: list[str], is_coop: bool = False) -> str:
        """格式化命运卡牌选择消息。"""
        lines = ["## 🎴 命运抉择"]
        if is_coop:
            lines.append("队长请选择一张命运卡牌（发送 1/2/3）：")
        else:
            lines.append("请选择一张命运卡牌（发送 1/2/3）：")
        lines.append("")
        for i, name in enumerate(choices, 1):
            card = data.TOMB_DESTINY_CARDS[name]
            lines.append(f"{i}. 【{name}】{card['desc']}")
        return "\n".join(lines)

    def _tomb_enter(self, player: dict, tokens: list[str]) -> tuple[str, str | None]:
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物，发送『砸蛋』获取一只。", None
        busy = self._busy_reason(p)
        if busy:
            return busy, None
        group_id = player.get("group", "")
        my_qq = str(player.get("qq", ""))

        # 检查双排
        coop = self._tomb_get_coop(player)
        is_coop = coop is not None
        if is_coop:
            err = self._tomb_team_validate_leader(player, "开始探险")
            if err:
                return err, None
            if len(coop.get("ready", set())) < 2:
                return "双方都发送「摸金准备」后才能开始。", None
            teammate_qq = self._tomb_teammate_qq(player, coop)
            tp = self.store.get_player(teammate_qq, group_id, create=False)
            if not tp:
                return "队友数据异常，请重新组队。", None
            tp_pet = self._need_pet(tp)
            if not tp_pet:
                return "队友还没有宠物，无法一起摸金。", None
            if self._busy_reason(tp_pet):
                return "队友宠物状态异常（可能已死亡或假死），无法进入。", None
            if not self._tomb_coop_pass_checks(tp, teammate_qq, group_id):
                return "队友不满足入场条件，无法进入。", None
            key = self._tomb_key(group_id, my_qq)
        else:
            key = self._tomb_key(group_id, my_qq)
            if key in self._tomb_sessions:
                return "你已经在一次摸金探险中，发送『摸态』查看。", None

        # 难度解析
        name_to_diff = {cfg["name"]: d for d, cfg in data.TOMB_DIFFICULTIES.items()}
        difficulty = 1
        if len(tokens) > 1:
            raw = tokens[1]
            if raw in name_to_diff:
                difficulty = name_to_diff[raw]
            else:
                try:
                    difficulty = int(raw)
                except ValueError:
                    return "用法：摸进 难度(简单/普通/困难/噩梦 或 1~4)", None
        if difficulty not in data.TOMB_DIFFICULTIES:
            return "难度只能是 1~4。", None
        cfg = data.TOMB_DIFFICULTIES[difficulty]

        # 验证主玩家入场条件
        st = self.store.tomb_state(player)
        err = self._tomb_validate_entry(player, st, p, difficulty, cfg)
        if err:
            return err, None

        # 双排：验证队友入场条件，并消耗资源
        if is_coop:
            teammate_qq = self._tomb_teammate_qq(player, coop)
            tp_st = self.store.tomb_state(tp)
            err = self._tomb_validate_entry(tp, tp_st, tp_pet, difficulty, cfg)
            if err:
                return f"队友：{err}", None
            self._tomb_consume_entry(tp, tp_st, tp_pet, difficulty, cfg)

        # 消耗主玩家资源
        self._tomb_consume_entry(player, st, p, difficulty, cfg)

        # 生成地图（双排缩放）
        cells = self._tomb_generate_map(difficulty, coop=is_coop)
        entrance_pos = {"x": 1, "y": 1}
        for ey, row in enumerate(cells):
            for ex, c in enumerate(row):
                if c == "E":
                    entrance_pos = {"x": ex, "y": ey}
                    break
            if entrance_pos != {"x": 1, "y": 1}:
                break

        def _build_player_ps(qq: str, player_st: dict) -> dict:
            equipped = player_st.get("equipped_weapon", "")
            weapons = player_st.get("weapons", {})
            weapon_attack = 0
            if equipped and equipped in weapons and weapons[equipped].get("location") == "equip" and equipped in data.TOMB_WEAPONS:
                weapon_attack = data.TOMB_WEAPONS[equipped]["attack"]
            return {
                "player_pos": dict(entrance_pos),
                "prev_pos": dict(entrance_pos),
                "visited": {(entrance_pos["x"], entrance_pos["y"])},
                "hp": data.TOMB_MAX_HP,
                "hp_max": data.TOMB_MAX_HP,
                "escapes": data.TOMB_ESCAPES_PER_RAID,
                "weapon": equipped,
                "weapon_attack": weapon_attack,
                "inventory": dict(player_st.get("equip_items", {})),
                "buffs": {},
                "pending": None,
                "status": "active",
                "stunned": 0,
                "mingbi": 0,
            }

        now = int(time.time())
        if is_coop:
            teammate_qq = self._tomb_teammate_qq(player, coop)
            leader_st = st
            teammate_st = self.store.tomb_state(tp)
            shared = {
                "started_at": now,
                "difficulty": difficulty,
                "map": {"w": len(cells[0]), "h": len(cells), "cells": cells},
                "required": int(cfg["required"] * data.TOMB_COOP_REQUIRED_MULT),
                "time_limit": cfg["time"],
                "deadline": now + cfg["time"],
                "leader": my_qq,
                "teammate": teammate_qq,
                "active": True,
                "group_id": str(group_id),
                "players": {
                    my_qq: _build_player_ps(my_qq, leader_st),
                    teammate_qq: _build_player_ps(teammate_qq, teammate_st),
                },
            }
            filename = self._tomb_draw_map(shared)
            shared["image"] = filename
            self._tomb_coop_teams[key] = shared
            self._tomb_persist()
            leader_st.setdefault("stats", {})["raids"] = leader_st["stats"].get("raids", 0) + 1
            teammate_st.setdefault("stats", {})["raids"] = teammate_st["stats"].get("raids", 0) + 1
            # 用虚拟 session 生成玩家视角地图
            virtual, _ = self._tomb_prepare(player)
            image_md = self._tomb_player_map_md(virtual)
            power = data.tomb_player_attack(st.get("level", 1), 0)
            wep_text = "徒步" if is_coop else "徒手"
            text = (
                f"## 进入【{cfg['name']}】（双排）\n"
                f"队长 {my_qq} · 队友 {teammate_qq}\n"
                f"需带回 **{shared['required']}** 冥币（合并计算）\n"
                f"起点：({entrance_pos['x']},{entrance_pos['y']})\n"
                f"> 上/下/左/右 移动　摸看 探索　摸态 状态"
            )
            # 命运卡牌选择
            choices = self._tomb_draw_cards()
            shared["destiny_choices"] = choices
            shared["destiny_card"] = ""
            shared["_entry_text"] = text
            shared["_entry_image_md"] = image_md
            for pqq in (my_qq, teammate_qq):
                shared["players"][pqq]["status"] = "pick_card"
            return self._tomb_format_card_choices(choices, is_coop=True), None

        # 单人模式
        equipped = st.get("equipped_weapon", "")
        weapons = st.get("weapons", {})
        weapon_attack = 0
        if equipped and equipped in weapons and weapons[equipped].get("location") == "equip" and equipped in data.TOMB_WEAPONS:
            weapon_attack = data.TOMB_WEAPONS[equipped]["attack"]
        session = {
            "started_at": now,
            "difficulty": difficulty,
            "map": {"w": len(cells[0]), "h": len(cells), "cells": cells},
            "player_pos": entrance_pos,
            "prev_pos": dict(entrance_pos),
            "visited": {(entrance_pos["x"], entrance_pos["y"])},
            "hp": data.TOMB_MAX_HP,
            "hp_max": data.TOMB_MAX_HP,
            "escapes": data.TOMB_ESCAPES_PER_RAID,
            "weapon": equipped,
            "weapon_attack": weapon_attack,
            "required": cfg["required"],
            "time_limit": cfg["time"],
            "deadline": now + cfg["time"],
            "mingbi": 0,
            "inventory": dict(st.get("equip_items", {})),
            "buffs": {},
            "pending": None,
            "status": "exploring",
            "stunned": 0,
        }
        filename = self._tomb_draw_map(session)
        session["image"] = filename
        self._tomb_sessions[key] = session
        self._tomb_persist()
        st.setdefault("stats", {})["raids"] = st["stats"].get("raids", 0) + 1
        image_md = self._tomb_player_map_md(session)
        ex, ey = entrance_pos["x"], entrance_pos["y"]
        power = data.tomb_player_attack(st.get("level", 1), weapon_attack)
        wep_text = f"{equipped}(攻+{weapon_attack})" if equipped else "徒手"
        text = (
            f"## 进入【{cfg['name']}】\n"
            f"摸金HP：{session['hp']}/{session['hp_max']}　战力：{power}　武器：{wep_text}\n"
            f"逃跑次数：{session['escapes']}　需带回 **{cfg['required']}** 冥币并撤离\n"
            f"起点：({ex},{ey})　出口：见地图红菱标记\n"
            f"图例：红菱=出口　金箱=宝箱　白骷髅=怪物　紫刺=陷阱　蓝珠=祭坛　黄圆=金币　绿雾=毒雾　紫环=传送　青滴=生命泉　红骷髅=BOSS\n"
            f"操作：上/下/左/右　摸看　摸态\n"
            f"> 你当前在 ({ex},{ey})，剩余时间 {cfg['time'] // 60}:00"
        )
        # 命运卡牌选择
        choices = self._tomb_draw_cards()
        session["destiny_choices"] = choices
        session["destiny_card"] = ""
        session["_entry_text"] = text
        session["_entry_image_md"] = image_md
        session["status"] = "pick_card"
        return self._tomb_format_card_choices(choices, is_coop=False), None

    def _tomb_pick_card(self, player: dict, choice: str) -> str | tuple[str, str | None]:
        """处理命运卡牌选择（1/2/3）。"""
        session, is_coop = self._tomb_prepare(player)
        if not session:
            return "你没有进行中的摸金探险。"
        if session.get("status") != "pick_card":
            return "当前不需要选择卡牌。"
        # 双排：只有队长能选卡
        coop = self._tomb_get_coop(player) if is_coop else None
        if is_coop and coop and str(player.get("qq", "")) != coop.get("leader", ""):
            return "只有队长可以选择命运卡牌。"
        # 从共享或 solo session 读取 choices
        src = coop if coop else session
        choices = src.get("destiny_choices", [])
        try:
            idx = int(choice) - 1
        except ValueError:
            return "请输入 1/2/3 选择卡牌。"
        if idx < 0 or idx >= len(choices):
            return "请输入 1/2/3 选择卡牌。"

        card_name = choices[idx]
        # 应用卡牌（操作 session 以影响 prepare 返回的视图）
        session["destiny_card"] = card_name
        session["destiny_choices"] = []
        self._tomb_apply_entry_card_effects(session, card_name)
        # 恢复状态 + 清理临时字段
        if is_coop and coop:
            coop["destiny_card"] = card_name
            coop["destiny_choices"] = []
            for pqq in coop.get("players", {}):
                coop["players"][pqq]["status"] = "active"
            # 同步 card effects 对 time/required/hp 的修改回 coop
            for k in ("time_limit", "deadline", "required", "hp", "hp_max", "mingbi"):
                if k in session:
                    # 这些 effects 可能改了共享值，从 session 写回 coop
                    if k in self._TOMB_SHARED_KEYS:
                        coop[k] = session[k]
            # 同步 status，避免 _tomb_commit 把 pick_card 覆盖回队长数据
            session["status"] = "active"
            # 命运卡牌的个人即时效果也同步给队友
            eff = data.TOMB_DESTINY_CARDS[card_name]["effects"]
            my_qq = str(player.get("qq", ""))
            for pqq, pd in coop.get("players", {}).items():
                if pqq == my_qq:
                    continue
                if "start_mingbi" in eff:
                    pd["mingbi"] = pd.get("mingbi", 0) + eff["start_mingbi"]
                if "start_hp_mod" in eff:
                    pd["hp"] = max(1, pd.get("hp", data.TOMB_MAX_HP) + eff["start_hp_mod"])
                    pd["hp_max"] = max(1, pd.get("hp_max", data.TOMB_MAX_HP) + eff["start_hp_mod"])
            self._tomb_commit(player, session, is_coop)
            entry_text = coop.pop("_entry_text", "## 进入摸金（双排）")
            entry_image = coop.pop("_entry_image_md", None)
        else:
            session["destiny_choices"] = []
            session["status"] = "exploring"
            entry_text = session.pop("_entry_text", "## 进入摸金")
            entry_image = session.pop("_entry_image_md", None)

        return (
            f"🎴 你选择了【{card_name}】\n{data.TOMB_DESTINY_CARDS[card_name]['desc']}\n\n{entry_text}",
            entry_image,
        )

    def _tomb_validate_entry(self, player: dict, st: dict, p: dict, difficulty: int, cfg: dict) -> str | None:
        """验证玩家是否满足进入摸金的条件，返回 None 表示通过，否则返回错误信息。"""
        tomb_level = st.get("level", 1)
        if tomb_level < cfg["tomb_level_req"]:
            return (
                f"【{cfg['name']}】需要摸金等级 Lv{cfg['tomb_level_req']}，"
                f"你当前 Lv{tomb_level}。"
            )
        now = int(time.time())
        cooldown_ts = st.get("cooldown", 0)
        if cooldown_ts > now:
            remain = cooldown_ts - now
            m, s = divmod(remain, 60)
            return f"冷却中，还需 {m}分{s:02d}秒 才能再次进入摸金。"
        tokens_needed = cfg.get("entry_tokens", 0)
        if tokens_needed > 0:
            token_count = (st.get("storage_items", {}).get(data.TOMB_EXTRA_TOKEN, 0)
                           + st.get("equip_items", {}).get(data.TOMB_EXTRA_TOKEN, 0))
            if token_count < tokens_needed:
                return (
                    f"【{cfg['name']}】需要 {tokens_needed} 张『{data.TOMB_EXTRA_TOKEN}』，"
                    f"当前只有 {token_count} 张。"
                )
        petmod.refresh_energy(p)
        if p["energy"] < cfg["energy"]:
            return f"精力不足（需 {cfg['energy']}，当前 {p['energy']}）。"
        return None

    def _tomb_consume_entry(self, player: dict, st: dict, p: dict, difficulty: int, cfg: dict) -> None:
        """消耗入场资源（令牌、精力），不重复校验。"""
        tokens_needed = cfg.get("entry_tokens", 0)
        if tokens_needed > 0:
            self.store.consume_tomb_token(player, tokens_needed)
        p["energy"] -= cfg["energy"]

    def _tomb_coop_pass_checks(self, player: dict, qq: str, group_id: str) -> bool:
        """快速检查队友是否满足基本条件（不消耗资源），用于双排入场前验证。"""
        key = self._tomb_key(group_id, qq)
        if key in self._tomb_sessions:
            return False
        return True

    def _tomb_move(self, player: dict, tokens: list[str]) -> tuple[str, str] | str:
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物。"
        session, is_coop = self._tomb_prepare(player)
        if not session:
            return "你没有进行中的摸金探险，发送『摸进 难度』开始。"
        if session.get("status") in ("downed",):
            return "你已倒地，无法移动。等待队友「摸金救援」。"
        if session.get("status") == "pick_card":
            return "请先选择命运卡牌（发送 1/2/3）。"
        settle = self._tomb_check_timeout(player, p, session)
        if settle:
            return settle
        if self._tomb_pending_battle(session):
            return "👹 你遭遇了怪物或BOSS，必须先『战斗』或『逃跑』才能离开！"
        if session.get("stunned", 0) > 0:
            session["stunned"] -= 1
            self._tomb_commit(player, session, is_coop)
            return f"😵 你仍处于眩晕中，本回合无法移动（还剩 {session['stunned']} 回合）。"
        if len(tokens) < 2:
            return "用法：上/下/左/右"
        direction = tokens[1]
        dxdy = {"上": (0, -1), "下": (0, 1), "左": (-1, 0), "右": (1, 0)}
        if direction not in dxdy:
            return "方向只能是：上、下、左、右。"
        dx, dy = dxdy[direction]
        x = session["player_pos"]["x"] + dx
        y = session["player_pos"]["y"] + dy
        cells = session["map"]["cells"]
        if y < 0 or y >= session["map"]["h"] or x < 0 or x >= session["map"]["w"]:
            return "🧱 前方是墓穴边界，无法通行。"
        if cells[y][x] == "#":
            return "🧱 前方是墙壁，无法通行。"
        session["prev_pos"] = dict(session["player_pos"])
        session["player_pos"]["x"] = x
        session["player_pos"]["y"] = y
        session["visited"].add((x, y))
        session["pending"] = None
        # 使用引路香效果
        avoid = session["buffs"].pop("avoid_monster", False)
        cell = cells[y][x]
        event_text = self._tomb_encounter(player, p, session, cell, avoid)
        # 提交并检查会话是否仍然存在
        self._tomb_commit(player, session, is_coop)
        if not self._tomb_session_exists(player):
            return event_text
        remain = self._tomb_time_left(session)
        surroundings = self._tomb_format_surroundings(session)
        image_md = self._tomb_player_map_md(session)
        text = (
            f"你向 **{direction}** 移动到了 ({x},{y})。\n"
            f"{event_text}\n"
            f"{surroundings}\n"
            f"> 摸金HP {session['hp']}/{session['hp_max']}　背负 {session['mingbi']} / {session['required']} 冥币　剩余时间 {remain}"
        )
        return text, image_md

    def _tomb_session_exists(self, player: dict) -> bool:
        """检查玩家是否仍有存活的摸金会话（单人/双排通用）。"""
        key = self._tomb_key(player.get("group", ""), player.get("qq", ""))
        if key in self._tomb_sessions:
            return True
        coop = self._tomb_get_coop(player)
        return coop is not None and coop.get("active", False)

    def _tomb_explore(self, player: dict) -> tuple[str, str] | str:
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物。"
        session, is_coop = self._tomb_prepare(player)
        if not session:
            return "你没有进行中的摸金探险。"
        if session.get("status") in ("downed", "pick_card"):
            return "当前状态无法执行此操作。"
        settle = self._tomb_check_timeout(player, p, session)
        if settle:
            return settle
        surroundings = self._tomb_format_surroundings(session, radius=1)
        remain = self._tomb_time_left(session)
        pos = session["player_pos"]
        image_md = self._tomb_player_map_md(session)
        text = (
            f"## 摸看\n"
            f"你当前在 ({pos['x']},{pos['y']})。\n"
            f"{surroundings}\n"
            f"> 摸金HP {session['hp']}/{session['hp_max']}　背负 {session['mingbi']} / {session['required']} 冥币　剩余时间 {remain}"
        )
        return text, image_md

    def _tomb_open_chest(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物。"
        session, is_coop = self._tomb_prepare(player)
        if not session:
            return "你没有进行中的摸金探险。"
        if session.get("status") in ("downed", "pick_card"):
            self._tomb_commit(player, session, is_coop)
            return "当前状态无法执行此操作。"
        settle = self._tomb_check_timeout(player, p, session)
        if settle:
            return settle
        pending = session.get("pending")
        if pending and pending.get("type") == "C":
            x, y = pending["x"], pending["y"]
        else:
            x, y = session["player_pos"]["x"], session["player_pos"]["y"]
        cells = session["map"]["cells"]
        if cells[y][x] != "C":
            session["pending"] = None
            self._tomb_commit(player, session, is_coop)
            return "当前位置没有宝箱。"
        cfg = data.TOMB_DIFFICULTIES[session["difficulty"]]
        base_min, base_max = cfg["chest_mingbi"]
        gain = random.randint(base_min, base_max)
        if session["buffs"].pop("chest_bonus", False):
            gain = int(gain * 1.3)
        # 命运卡牌效果
        chest_mult = self._get_card_effect(session, "chest_mingbi_mult", 1.0)
        chest_bonus = self._get_card_effect(session, "chest_mingbi_bonus", 0)
        gain = int(gain * chest_mult) + int(chest_bonus)
        session["mingbi"] += gain
        cells[y][x] = "."
        self._tomb_refresh_map(session)
        session["pending"] = None
        extra = ""
        if random.random() < 0.2:
            item = random.choice([n for n in data.TOMB_ITEMS if data.TOMB_ITEMS[n].get("effect") not in ("token", "main_bag_item")])
            session.setdefault("inventory", {}).setdefault(item, 0)
            session["inventory"][item] += 1
            extra = f"，额外获得『{item}』×1"
        self._tomb_commit(player, session, is_coop)
        remain = self._tomb_time_left(session)
        return (
            f"🎁 开启宝箱，获得 **{gain}** 冥币{extra}。\n"
            f"> 摸金HP {session['hp']}/{session['hp_max']}　背负 {session['mingbi']} / {session['required']} 冥币　剩余时间 {remain}"
        )

    def _tomb_use_item(self, player: dict, tokens: list[str]) -> str:
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物。"
        session, is_coop = self._tomb_prepare(player)
        if not session:
            return "你没有进行中的摸金探险。"
        if session.get("status") in ("downed", "pick_card"):
            self._tomb_commit(player, session, is_coop)
            return "当前状态无法执行此操作。"
        settle = self._tomb_check_timeout(player, p, session)
        if settle:
            return settle
        if len(tokens) < 2:
            return "用法：摸用 道具名"
        name = tokens[1]
        if name not in data.TOMB_ITEMS:
            return f"没有『{name}』这种摸金道具。"
        inv = session.get("inventory", {})
        if inv.get(name, 0) <= 0:
            st = self.store.tomb_state(player)
            if st.get("storage_items", {}).get(name, 0) > 0:
                return f"『{name}』在储物柜中，局内无法直接使用。请先结束本局，用『摸带 {name}』带入装备背包。"
            return f"你的摸金背包中没有『{name}』。"
        effect = data.TOMB_ITEMS[name]["effect"]
        if effect in ("revive_tomb",):
            return "💊 还魂丹会在摸金HP归0时自动触发，无需手动使用。"
        if effect in ("token", "main_bag_item"):
            return f"『{name}』不能在摸金中使用。"
        # 消耗道具
        inv[name] -= 1
        if inv[name] <= 0:
            inv.pop(name, None)

        result = f"已使用『{name}』。"
        if effect == "heal_tomb":
            heal = data.TOMB_ITEMS[name].get("amount", 30)
            heal_mult = self._get_card_effect(session, "heal_item_mult", 1.0)
            heal = max(1, int(heal * heal_mult))
            session["hp"] = min(session["hp_max"], session["hp"] + heal)
            result = f"💊 使用『{name}』，摸金HP +{heal}（{session['hp']}/{session['hp_max']}）。"
        elif effect == "heal_tomb_pct":
            heal = max(1, int(session["hp_max"] * data.TOMB_ITEMS[name].get("amount", 0.3)))
            heal_mult = self._get_card_effect(session, "heal_item_mult", 1.0)
            heal = max(1, int(heal * heal_mult))
            session["hp"] = min(session["hp_max"], session["hp"] + heal)
            result = f"💊 使用『{name}』，摸金HP +{heal}（{session['hp']}/{session['hp_max']}）。"
        elif effect == "avoid_monster":
            session["buffs"]["avoid_monster"] = True
            result = f"🕯 使用『{name}』，下一次移动不会触发怪物。"
        elif effect == "auto_win":
            session["buffs"]["auto_win"] = True
            result = f"📌 使用『{name}』，下一场战斗锁定必胜。"
        elif effect == "chest_bonus":
            session["buffs"]["chest_bonus"] = True
            result = f"⛏ 使用『{name}』，下一次开箱冥币 +30%。"
        elif effect == "revive":
            session["buffs"]["revive"] = True
            result = f"🧧 使用『{name}』，摸金HP归0时自动复活到1并强制撤离。"

        self._tomb_commit(player, session, is_coop)
        return result

    def _tomb_evacuate(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物。"
        session, is_coop = self._tomb_prepare(player)
        if not session:
            return "你没有进行中的摸金探险。"
        if session.get("status") in ("downed", "pick_card"):
            return "当前状态无法执行此操作。"
        settle = self._tomb_check_timeout(player, p, session)
        if settle:
            return settle
        if self._tomb_pending_battle(session):
            return "👹 你还有未处理的怪物或BOSS，必须先『战斗』或『逃跑』才能撤离！"
        x, y = session["player_pos"]["x"], session["player_pos"]["y"]
        cells = session["map"]["cells"]
        if cells[y][x] != "E" and cells[y][x] != "X":
            return "❌ 只有回到起点或到达出口才能撤离。"
        # 双排：合并双方冥币判断
        if is_coop:
            coop = session.get("_coop_parent")
            if coop:
                total_mb = sum(pd.get("mingbi", 0) for pd in coop["players"].values())
                if total_mb < session["required"]:
                    return (
                        f"❌ 队伍冥币不足（当前合计 {total_mb} / {session['required']}），"
                        f"无法撤离。继续探索吧！"
                    )
                return self._tomb_settle_coop(player, p, coop, "success")
        if session["mingbi"] < session["required"]:
            return (
                f"❌ 你还未凑够冥币（当前 {session['mingbi']} / {session['required']}），"
                f"无法撤离。继续探索吧！"
            )
        return self._tomb_settle(player, p, session, "success")

    def _tomb_status(self, player: dict) -> tuple[str, str] | str:
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物。"
        session, is_coop = self._tomb_prepare(player)
        if not session:
            return self._tomb_status_outside(player)
        settle = self._tomb_check_timeout(player, p, session)
        if settle:
            return settle
        cfg = data.TOMB_DIFFICULTIES[session["difficulty"]]
        image_md = self._tomb_player_map_md(session)
        remain = self._tomb_time_left(session)
        card_line = ""
        if session.get("destiny_card"):
            card_line = f"● 命运卡牌：【{session['destiny_card']}】\n"
        pending = session.get("pending")
        pending_text = ""
        if pending:
            pmap = {"C": "宝箱待开（开箱/跳过）", "M": "怪物待战（战斗/逃跑）", "S": "祭坛待祭拜（祭拜/跳过）"}
            pending_text = f"\n● 当前：{pmap.get(pending['type'], '')}"

        if is_coop:
            coop = session.get("_coop_parent")
            my_qq = str(player.get("qq", ""))
            lines = [f"## 🏺 摸态 · {cfg['name']}（双排）", card_line.rstrip()]
            total_mingbi = 0
            for qq, pd in coop.get("players", {}).items():
                total_mingbi += pd.get("mingbi", 0)
                pos = pd.get("player_pos", {})
                inv = pd.get("inventory", {})
                inv_text = "、".join(f"{k}×{v}" for k, v in inv.items() if v > 0) or "空"
                wep = pd.get("weapon", "")
                wep_text = f"{wep}(攻+{pd.get('weapon_attack', 0)})" if wep else "徒手"
                pl = self.store.get_player(qq, coop.get("group_id", player.get("group", "")), create=False)
                level = self.store.get_tomb_level(pl) if pl else 1
                power = data.tomb_player_attack(level, pd.get("weapon_attack", 0))
                label = "我" if qq == my_qq else "队友"
                lines.append(
                    f"● {label} `{qq}`：HP {pd.get('hp', 0)}/{pd.get('hp_max', 0)}　"
                    f"战力 {power}（{wep_text}）\n"
                    f"　位置：({pos.get('x', 0)},{pos.get('y', 0)})　"
                    f"逃跑 {pd.get('escapes', 0)}/{data.TOMB_ESCAPES_PER_RAID}　"
                    f"眩晕 {pd.get('stunned', 0)}\n"
                    f"　冥币 {pd.get('mingbi', 0)}　背包 {inv_text}"
                )
            lines.append(f"● 合计冥币：{total_mingbi} / {session['required']}　剩余时间：{remain}{pending_text}")
            return "\n".join(lines), image_md

        pos = session["player_pos"]
        inv = session.get("inventory", {})
        inv_text = "、".join(f"{k}×{v}" for k, v in inv.items() if v > 0) or "空"
        wep = session.get("weapon", "")
        wep_text = f"{wep}(攻+{session.get('weapon_attack', 0)})" if wep else "徒手"
        level = self.store.get_tomb_level(player)
        power = data.tomb_player_attack(level, session.get("weapon_attack", 0))
        text = (
            f"## 🏺 摸态 · {cfg['name']}\n"
            f"{card_line}"
            f"● 位置：({pos['x']},{pos['y']})\n"
            f"● 摸金HP：{session['hp']}/{session['hp_max']}\n"
            f"● 战力：{power}　武器：{wep_text}\n"
            f"● 逃跑次数：{session.get('escapes', 0)}/{data.TOMB_ESCAPES_PER_RAID}\n"
            f"● 背负冥币：{session['mingbi']} / {session['required']}\n"
            f"● 摸金背包：{inv_text}\n"
            f"● 剩余时间：{remain}　眩晕：{session.get('stunned', 0)}"
            f"{pending_text}"
        )
        return text, image_md

    def _tomb_forfeit(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物。"
        session, is_coop = self._tomb_prepare(player)
        if not session:
            return "你没有进行中的摸金探险。"
        settle = self._tomb_check_timeout(player, p, session)
        if settle:
            return settle
        if is_coop:
            coop = session.get("_coop_parent")
            if coop:
                return self._tomb_settle_coop(player, p, coop, "forfeit")
        return self._tomb_settle(player, p, session, "forfeit")

    # ---- 摸金结算 ----
    def _tomb_settle(self, player: dict, p: dict, session: dict, reason: str) -> str:
        st = self.store.tomb_state(player)
        stats = st.setdefault("stats", {})
        key = self._tomb_key(player.get("group", ""), player.get("qq", ""))
        # 先移除 session 并持久化，再结算（避免重载后残留）
        self._tomb_sessions.pop(key, None)
        self._tomb_persist()
        cfg = data.TOMB_DIFFICULTIES[session["difficulty"]]
        st["cooldown"] = int(time.time()) + data.TOMB_COOLDOWN

        # 摸金经验结算：成功 / 失败（超时、死亡、放弃、复活均算失败）
        old_level = self.store.get_tomb_level(player)
        is_success = reason == "success"
        xp = data.TOMB_XP_REWARD["success" if is_success else "failure"].get(session["difficulty"], 0)
        new_level, new_exp = self.store.add_tomb_exp(player, xp)
        level_up_text = ""
        if new_level > old_level:
            level_up_text = f"　🆙 摸金等级提升至 Lv{new_level}！"
        xp_text = f"摸金经验 +{xp}{level_up_text}"

        # 宠物经验改为暂存，用户发送『摸金兑换』后统一发放到当前群宠物
        exp_range = data.TOMB_SUCCESS_EXP_RANGE.get(session["difficulty"], (500, 2000))
        base_pet_exp = random.randint(*exp_range)
        stored_pet_exp = base_pet_exp if is_success else (base_pet_exp // 10)
        if stored_pet_exp > 0:
            self.store.add_tomb_pending_pet_exp(player, stored_pet_exp)
        pet_exp_text = f"宠物经验 +{stored_pet_exp}（已暂存，发送『摸金兑换』可发放到当前群宠物）" if stored_pet_exp > 0 else ""

        # 撤离失败（阵亡/超时）：装备背包全部掉落；其它结果把剩余道具写回装备背包
        if reason in ("death", "timeout"):
            self.store.clear_tomb_loadout(player)
        else:
            self.store.writeback_tomb_equip(player, session.get("inventory", {}))

        if reason == "success":
            gained = session["mingbi"]
            self.store.add_tomb_mingbi(player, gained)
            stats["success"] = stats.get("success", 0) + 1
            stats["total_mingbi"] = stats.get("total_mingbi", 0) + gained
            return (
                f"🏆 撤离成功！带出 **{gained}** 冥币，已永久到账。\n"
                f"● {xp_text}\n"
                f"● {pet_exp_text}\n"
                f"● 累计成功 {stats['success']} 次，总带出冥币 {stats['total_mingbi']}"
            )
        if reason == "timeout":
            stats["fail"] = stats.get("fail", 0) + 1
            return (
                f"⏰ 墓穴坍塌，撤离失败！\n"
                f"● {xp_text}\n"
                f"● {pet_exp_text}\n"
                f"● 本局冥币全部损失\n"
                f"● 装备背包全部掉落！储物柜不受影响"
            )
        if reason == "death":
            kept = int(session["mingbi"] * 0.2)
            self.store.add_tomb_mingbi(player, kept)
            stats["fail"] = stats.get("fail", 0) + 1
            return (
                f"💀 摸金角色阵亡，撤离失败！\n"
                f"● {xp_text}\n"
                f"● {pet_exp_text}\n"
                f"● 装备背包全部掉落！储物柜不受影响\n"
                f"● 仅保留 {kept} 冥币"
            )
        if reason == "revive":
            kept = int(session["mingbi"] * 0.5)
            self.store.add_tomb_mingbi(player, kept)
            stats["fail"] = stats.get("fail", 0) + 1
            return (
                f"🧧 招魂幡触发，你在濒死之际被强行送出墓穴！\n"
                f"● {xp_text}\n"
                f"● {pet_exp_text}\n"
                f"● 保留 {kept} 冥币\n"
                f"● 带入的武器和道具已带回"
            )
        # forfeit
        kept = int(session["mingbi"] * 0.5)
        self.store.add_tomb_mingbi(player, kept)
        return (
            f"🏃 你已放弃本次摸金，仅保留 {kept} 冥币。\n"
            f"● {xp_text}\n"
            f"● {pet_exp_text}\n"
            f"● 损失 {session['mingbi'] - kept} 冥币\n"
            f"● 带入的武器和道具已带回"
        )

    def _tomb_settle_coop(self, player: dict, p: dict, coop: dict, reason: str) -> str:
        """双排结算：双方各自结算经验/宠物经验/冥币/装备，清理队伍。"""
        group_id = coop.get("group_id", player.get("group", ""))
        cfg = data.TOMB_DIFFICULTIES[coop["difficulty"]]
        result_parts = []
        total_mingbi = 0

        for qq in (coop["leader"], coop["teammate"]):
            pl = self.store.get_player(qq, group_id, create=False)
            if not pl:
                continue
            st = self.store.tomb_state(pl)
            stats = st.setdefault("stats", {})
            st["cooldown"] = int(time.time()) + data.TOMB_COOLDOWN
            pdata = coop["players"].get(qq, {})
            gained = pdata.get("mingbi", 0)

            old_level = self.store.get_tomb_level(pl)
            is_success = reason == "success"
            xp = data.TOMB_XP_REWARD["success" if is_success else "failure"].get(coop["difficulty"], 0)
            self.store.add_tomb_exp(pl, xp)

            exp_range = data.TOMB_SUCCESS_EXP_RANGE.get(coop["difficulty"], (500, 2000))
            base_pe = random.randint(*exp_range)
            stored_pe = base_pe if is_success else (base_pe // 10)
            if stored_pe > 0:
                self.store.add_tomb_pending_pet_exp(pl, stored_pe)

            if reason in ("death", "timeout"):
                self.store.clear_tomb_loadout(pl)
            else:
                self.store.writeback_tomb_equip(pl, pdata.get("inventory", {}))

            if reason == "success":
                self.store.add_tomb_mingbi(pl, gained)
                stats["success"] = stats.get("success", 0) + 1
                stats["total_mingbi"] = stats.get("total_mingbi", 0) + gained
                total_mingbi += gained
            elif reason == "forfeit":
                kept = int(gained * 0.5)
                self.store.add_tomb_mingbi(pl, kept)
                stats["fail"] = stats.get("fail", 0) + 1
                total_mingbi += kept
            elif reason == "revive":
                kept = int(gained * 0.5)
                self.store.add_tomb_mingbi(pl, kept)
                stats["fail"] = stats.get("fail", 0) + 1
                total_mingbi += kept
            elif reason == "death":
                kept = int(gained * 0.2)
                self.store.add_tomb_mingbi(pl, kept)
                stats["fail"] = stats.get("fail", 0) + 1
                total_mingbi += kept
            elif reason == "timeout":
                stats["fail"] = stats.get("fail", 0) + 1
                # 超时不保留冥币，与单人超时结算一致
            else:
                kept = int(gained * 0.2)
                self.store.add_tomb_mingbi(pl, kept)
                stats["fail"] = stats.get("fail", 0) + 1
                total_mingbi += kept

        # 清理双排数据
        for qq in (coop["leader"], coop["teammate"]):
            idx_key = self._tomb_key(group_id, qq)
            self._tomb_coop_index.pop(idx_key, None)
        coop_key = self._tomb_key(group_id, coop["leader"])
        self._tomb_coop_teams.pop(coop_key, None)
        self._tomb_persist()

        if reason == "success":
            return f"## 组队撤离成功！\n共带出 {total_mingbi} 冥币。"
        elif reason == "death":
            return "## 全队覆灭...\n装备背包全部掉落。"
        elif reason == "forfeit":
            return f"## 队伍放弃探险\n共保留 {total_mingbi} 冥币。"
        elif reason == "revive":
            return f"## 招魂幡复活撤离\n共保留 {total_mingbi} 冥币。"
        elif reason == "timeout":
            return "## 时间耗尽！\n全队撤离失败，装备背包全部掉落。"
        return f"## 结算完成"

    def _tomb_settle_coop_player(self, player: dict, qq: str, group_id: str, cfg: dict, pdata: dict, reason: str) -> None:
        """结算单个双排玩家。"""
        pl = self.store.get_player(qq, group_id, create=False)
        if not pl:
            return
        st = self.store.tomb_state(pl)
        stats = st.setdefault("stats", {})
        st["cooldown"] = int(time.time()) + data.TOMB_COOLDOWN
        is_success = reason == "success"
        xp = data.TOMB_XP_REWARD["success" if is_success else "failure"].get(cfg.get("difficulty", 1) if isinstance(cfg, dict) else 1, 0)
        self.store.add_tomb_exp(pl, xp)
        exp_range = data.TOMB_SUCCESS_EXP_RANGE.get(1, (500, 2000))
        base_pe = random.randint(*exp_range)
        stored_pe = base_pe if is_success else (base_pe // 2)
        if stored_pe > 0:
            self.store.add_tomb_pending_pet_exp(pl, stored_pe)
        gained = pdata.get("mingbi", 0)
        if reason == "success":
            self.store.add_tomb_mingbi(pl, gained)
            stats["success"] = stats.get("success", 0) + 1
            stats["total_mingbi"] = stats.get("total_mingbi", 0) + gained
        elif reason == "forfeit":
            self.store.add_tomb_mingbi(pl, int(gained * 0.5))
            stats["fail"] = stats.get("fail", 0) + 1
        else:
            stats["fail"] = stats.get("fail", 0) + 1
        if reason in ("death", "timeout"):
            self.store.clear_tomb_loadout(pl)
        else:
            self.store.writeback_tomb_equip(pl, pdata.get("inventory", {}))

    def _tomb_cleanup_coop(self, coop: dict) -> None:
        """清理双排 session 和索引。"""
        gid = coop.get("group_id", "")
        for qq in (coop.get("leader", ""), coop.get("teammate", "")):
            self._tomb_coop_index.pop(self._tomb_key(gid, qq), None)
        coop_key = self._tomb_key(gid, coop.get("leader", ""))
        self._tomb_coop_teams.pop(coop_key, None)
        self._tomb_persist()

    def _tomb_check_timeout(self, player: dict, p: dict, session: dict) -> str | None:
        if int(time.time()) >= session.get("deadline", 0):
            if session.get("_is_coop"):
                coop = session.get("_coop_parent")
                if coop:
                    return self._tomb_settle_coop(player, p, coop, "timeout")
            return self._tomb_settle(player, p, session, "timeout")
        return None

    def _tomb_time_left(self, session: dict) -> str:
        remain = max(0, session.get("deadline", 0) - int(time.time()))
        m, s = divmod(remain, 60)
        return f"{m:02d}:{s:02d}"

    # ---- 摸金事件触发 ----
    def _tomb_encounter(
        self, player: dict, p: dict, session: dict, cell: str, avoid_monster: bool = False
    ) -> str:
        x, y = session["player_pos"]["x"], session["player_pos"]["y"]
        cells = session["map"]["cells"]
        if cell == "X":
            return "🚪 你到达了出口，发送『摸撤』可结算离开。"
        if cell == "E":
            return ""
        if cell == "T":
            cells[y][x] = "."
            self._tomb_refresh_map(session)
            return self._tomb_trap_event(player, p, session)
        if cell == "C":
            session["pending"] = {"type": "C", "x": x, "y": y}
            return "🎁 发现宝箱！发送『开箱』打开，或『跳过』离开。"
        if cell == "M":
            if avoid_monster:
                return "🕯 引路香生效，你悄悄绕过怪物，怪物仍在原地。"
            enc_mult = self._get_card_effect(session, "monster_encounter_mult", 1.0)
            if enc_mult < 1.0 and random.random() > enc_mult:
                cells[y][x] = "."
                self._tomb_refresh_map(session)
                return "👹 前方有怪物痕迹…但似乎已经离开了（命运卡牌）。"
            session["pending"] = {"type": "M", "x": x, "y": y}
            return f"👹 遭遇怪物！发送『战斗』迎战，或『逃跑』（剩余 {session.get('escapes', 0)} 次）。"
        if cell == "S":
            session["pending"] = {"type": "S", "x": x, "y": y}
            return "🌀 发现祭坛！发送『祭拜』互动，或『跳过』离开。"
        if cell == "$":
            cfg = data.TOMB_DIFFICULTIES[session["difficulty"]]
            lo, hi = cfg.get("chest_mingbi", (10, 40))
            gain = random.randint(max(5, lo // 3), max(15, hi // 2))
            gold_mult = self._get_card_effect(session, "gold_mult", 1.0)
            gain = max(1, int(gain * gold_mult))
            session["mingbi"] += gain
            cells[y][x] = "."
            self._tomb_refresh_map(session)
            return f"💰 踩到散落的冥币，获得 {gain} 冥币！"
        if cell == "G":
            if self._get_card_effect(session, "gas_immune", False):
                return "☠️ 毒雾弥漫…但你免疫毒雾（命运卡牌），安然通过。"
            gas_mult = self._get_card_effect(session, "gas_damage_mult", 1.0)
            dmg = max(1, int(session["hp_max"] * 0.10 * gas_mult))
            session["hp"] = max(0, session["hp"] - dmg)
            death = self._tomb_after_damage(player, p, session)
            if death:
                return f"☠️ 踏入毒雾区，摸金HP -{dmg}。\n{death}"
            return f"☠️ 踏入毒雾区，摸金HP -{dmg}（毒雾不散，下次踩到仍有效果）。"
        if cell == "P":
            if self._get_card_effect(session, "portal_blocked", False):
                return "🌀 传送门闪烁着…但被命运之力封印，无法使用。"
            floors = [(fx, fy) for fy in range(len(cells)) for fx in range(len(cells[0]))
                      if cells[fy][fx] in (".", "E") and (fx, fy) != (x, y)]
            if floors:
                tx, ty = random.choice(floors)
                cells[y][x] = "."
                self._tomb_refresh_map(session)
                session["prev_pos"] = dict(session["player_pos"])
                session["player_pos"] = {"x": tx, "y": ty}
                session["visited"].add((tx, ty))
                return f"🌀 传送门将你吸入，传送到了 ({tx},{ty})！"
            return "🌀 传送门闪烁了一下…但似乎已经失效。"
        if cell == "H":
            if self._get_card_effect(session, "spring_blocked", False):
                return "💚 生命泉…但泉水已干涸（命运卡牌），无法回复。"
            spring_mult = self._get_card_effect(session, "spring_heal_mult", 1.0)
            heal = max(1, int(session["hp_max"] * 0.30 * spring_mult))
            session["hp"] = min(session["hp_max"], session["hp"] + heal)
            cells[y][x] = "."
            self._tomb_refresh_map(session)
            return f"💚 生命泉涌出，摸金HP +{heal}（{session['hp']}/{session['hp_max']}）。"
        if cell == "B":
            if avoid_monster:
                return "🕯 引路香生效，你悄悄绕过BOSS，BOSS仍在原地。"
            session["pending"] = {"type": "B", "x": x, "y": y}
            return f"👹 遭遇 BOSS！发送『战斗』迎战，或『逃跑』（剩余 {session.get('escapes', 0)} 次）。"
        return "四周一片死寂。"

    def _tomb_trap_event(self, player: dict, p: dict, session: dict) -> str:
        outcomes = [o for o, _ in data.TOMB_TRAP_OUTCOMES]
        weights = [w for _, w in data.TOMB_TRAP_OUTCOMES]
        # 命运卡牌：陷阱避开率加成
        dodge_bonus = self._get_card_effect(session, "trap_dodge_bonus", 0.0)
        if dodge_bonus > 0:
            # 增加 avoid 的权重
            total_w = sum(weights)
            bonus_w = int(total_w * dodge_bonus)
            weights[0] += bonus_w
        outcome = random.choices(outcomes, weights=weights, k=1)[0]
        trap_dmg_mult = self._get_card_effect(session, "trap_damage_mult", 1.0)
        if outcome == "avoid":
            return "🪤 你险险避开了一个陷阱。"
        if outcome == "light":
            dmg = max(1, int(15 * trap_dmg_mult))
            session["hp"] = max(0, session["hp"] - dmg)
            death = self._tomb_after_damage(player, p, session)
            if death:
                return f"☠️ 触发陷阱！摸金HP -{dmg}。\n{death}"
            return f"☠️ 触发陷阱！摸金HP -{dmg}。"
        # heavy
        dmg = max(1, int(30 * trap_dmg_mult))
        session["hp"] = max(0, session["hp"] - dmg)
        session["stunned"] = session.get("stunned", 0) + 1
        death = self._tomb_after_damage(player, p, session)
        if death:
            return f"☠️ 重伤陷阱！摸金HP -{dmg}，眩晕1回合。\n{death}"
        return f"☠️ 重伤陷阱！摸金HP -{dmg}，眩晕1回合。"

    def _tomb_after_damage(self, player: dict, p: dict, session: dict) -> str | None:
        """摸金HP归0时的复活/死亡处理，返回提示文本（None 表示未阵亡）。
        双排模式：先检查复活道具，若无则进入倒地状态。双方倒地则全队结算死亡。"""
        if session.get("hp", 0) > 0:
            return None
        inv = session.get("inventory", {})
        if inv.get("还魂丹", 0) > 0:
            inv["还魂丹"] -= 1
            if inv["还魂丹"] <= 0:
                inv.pop("还魂丹")
            session["hp"] = 50
            return "💊 还魂丹自动触发，摸金HP恢复到50！"
        if session.get("buffs", {}).get("revive"):
            session["buffs"].pop("revive")
            session["hp"] = 1
            # 双排招魂幡：不立即结算，保持存活
            if session.get("_is_coop"):
                return "🪦 招魂幡触发，摸金HP恢复到1！"
            return self._tomb_settle(player, p, session, "revive")
        # 命运卡牌：涅槃（自动复活1次）
        if self._get_card_effect(session, "auto_revive", False):
            if not session.get("_auto_revive_used"):
                session["_auto_revive_used"] = True
                revive_hp = int(self._get_card_effect(session, "revive_hp", 40))
                session["hp"] = revive_hp
                return f"🔥 命运卡牌【涅槃】触发！摸金HP恢复到 {revive_hp}（本局仅1次）。"
        # 双排倒地逻辑
        if session.get("_is_coop"):
            session["status"] = "downed"
            session["hp"] = 0
            coop = session.get("_coop_parent")
            if coop:
                all_downed = all(pd.get("status") == "downed" for pd in coop["players"].values())
                if all_downed:
                    return self._tomb_settle_coop(player, p, coop, "death")
            return "💀 你已倒地！等待队友「摸金救援」救援。"
        return self._tomb_settle(player, p, session, "death")

    def _tomb_altar_event(self, player: dict, p: dict, session: dict) -> str:
        roll = random.random()
        if roll < 0.4:
            heal = max(1, int(session["hp_max"] * 0.2))
            session["hp"] = min(session["hp_max"], session["hp"] + heal)
            return f"✨ 祭坛赐福：摸金HP +{heal}（{session['hp']}/{session['hp_max']}）。"
        if roll < 0.7:
            gain = random.randint(10, 30)
            session["mingbi"] += gain
            return f"✨ 祭坛涌出冥币：+{gain}。"
        if roll < 0.9:
            return self._tomb_battle(player, p, session, summoned=True)
        item = random.choice([n for n in data.TOMB_ITEMS if data.TOMB_ITEMS[n].get("effect") not in ("token", "main_bag_item")])
        session.setdefault("inventory", {}).setdefault(item, 0)
        session["inventory"][item] += 1
        return f"✨ 祭坛中藏着『{item}』×1。"

    def _tomb_battle(
        self, player: dict, p: dict, session: dict, summoned: bool = False, forced_win: bool = False, is_boss: bool = False
    ) -> str:
        cfg = data.TOMB_DIFFICULTIES[session["difficulty"]]
        cells = session["map"]["cells"]
        x, y = session["player_pos"]["x"], session["player_pos"]["y"]

        # 武器耐久 -1（命运卡牌可能加倍消耗）
        weapon = session.get("weapon", "")
        broke_text = ""
        if weapon:
            dura_mult = self._get_card_effect(session, "weapon_durability_mult", 1.0)
            dura_loss = max(1, int(dura_mult))
            for _ in range(dura_loss):
                remaining = self.store.decrement_tomb_weapon(player, weapon)
                if remaining is not None and remaining == 0:
                    session["weapon"] = ""
                    session["weapon_attack"] = 0
                    broke_text = f"　⚠️『{weapon}』耐久耗尽破碎！"
                    break

        if forced_win or session["buffs"].pop("auto_win", False):
            if not summoned and cells[y][x] in ("M", "B"):
                cells[y][x] = "."
                self._tomb_refresh_map(session)
            gain_min, gain_max = cfg["monster_mingbi"]
            if is_boss:
                gain_min = int(gain_min * data.TOMB_BOSS_MINGBI_MULT)
                gain_max = int(gain_max * data.TOMB_BOSS_MINGBI_MULT)
            gain = random.randint(gain_min, gain_max)
            session["mingbi"] += gain
            label = "BOSS" if is_boss else ""
            return f"⚔️ 镇尸钉锁定必胜！击败{label}获得 {gain} 冥币。{broke_text}"

        level = self.store.get_tomb_level(player)
        weapon_atk = session.get("weapon_attack", 0)
        # 命运卡牌：武器攻击倍率
        weapon_mult = self._get_card_effect(session, "weapon_attack_mult", 1.0)
        weapon_atk = int(weapon_atk * weapon_mult)
        my_power = data.tomb_player_attack(level, weapon_atk)
        # 命运卡牌：玩家攻击倍率
        player_atk_mult = self._get_card_effect(session, "player_attack_mult", 1.0)
        my_power = int(my_power * player_atk_mult)
        b = data.TOMB_BATTLE

        # 闪避：免伤撤退，怪物仍在原地（不清空格子）
        if random.random() < b["dodge_chance"]:
            return f"💨 你灵巧闪避，全身而退，怪物仍在原地。{broke_text}"

        # 正式交战后才清空怪物/BOSS 格
        if not summoned and cells[y][x] in ("M", "B"):
            cells[y][x] = "."
            self._tomb_refresh_map(session)

        # 命运卡牌：随机范围扩展
        luck_mult = self._get_card_effect(session, "luck_range_mult", 1.0)
        pl_low = 1.0 - (1.0 - b["player_luck"][0]) * luck_mult
        pl_high = 1.0 + (b["player_luck"][1] - 1.0) * luck_mult
        ml_low = 1.0 - (1.0 - b["monster_luck"][0]) * luck_mult
        ml_high = 1.0 + (b["monster_luck"][1] - 1.0) * luck_mult
        player_score = my_power * random.uniform(pl_low, pl_high)
        base_monster_power = cfg["monster_power"]
        if is_boss:
            base_monster_power = int(base_monster_power * data.TOMB_BOSS_POWER_MULT)
            # 命运卡牌：Boss 攻击倍率
            boss_atk_mult = self._get_card_effect(session, "boss_attack_mult", 1.0)
            base_monster_power = int(base_monster_power * boss_atk_mult)
        # 命运卡牌：怪物攻击/血量倍率
        mon_atk_mult = self._get_card_effect(session, "monster_attack_mult", 1.0)
        mon_hp_mult = self._get_card_effect(session, "monster_hp_mult", 1.0)
        monster_score = int(base_monster_power * mon_atk_mult * mon_hp_mult * random.uniform(ml_low, ml_high))
        events = []
        if random.random() < b["miss_chance"]:
            player_score *= b["miss_mult"]
            events.append("失手")
        crit_bonus = self._get_card_effect(session, "crit_chance_bonus", 0.0)
        if random.random() < (b["crit_chance"] + crit_bonus):
            player_score *= b["crit_mult"]
            events.append("暴击")
        player_score = int(player_score)
        monster_score = int(monster_score)
        event_text = ("（" + "、".join(events) + "）") if events else ""
        gain_min, gain_max = cfg["monster_mingbi"]
        if is_boss:
            gain_min = int(gain_min * data.TOMB_BOSS_MINGBI_MULT)
            gain_max = int(gain_max * data.TOMB_BOSS_MINGBI_MULT)

        if player_score >= monster_score:
            ratio = (player_score - monster_score) / max(1, monster_score)
            gain = int(random.randint(gain_min, gain_max) * (1 + min(1.0, ratio)))
            # 命运卡牌：战斗冥币归零
            if self._get_card_effect(session, "combat_mingbi_zero", False):
                gain = 0
            session["mingbi"] += gain
            boss_extra = ""
            if is_boss and random.random() < data.TOMB_BOSS_DROP_CHANCE:
                boss_items = [n for n in data.TOMB_ITEMS if data.TOMB_ITEMS[n].get("effect") not in ("token", "main_bag_item")]
                if boss_items:
                    item = random.choice(boss_items)
                    session.setdefault("inventory", {}).setdefault(item, 0)
                    session["inventory"][item] += 1
                    boss_extra = f"　掉落『{item}』×1！"
            if player_score >= monster_score * 1.5:
                hp_loss, tier = 5, "大胜"
            else:
                hp_loss, tier = 10, "小胜"
            # 命运卡牌：战后扣血/回血
            hp_loss += int(self._get_card_effect(session, "post_battle_hp_loss", 0))
            hp_loss = max(0, hp_loss - int(self._get_card_effect(session, "post_battle_hp_heal", 0)))
            label = "BOSS！" if is_boss else ""
            session["hp"] = max(0, session["hp"] - hp_loss)
            death = self._tomb_after_damage(player, p, session)
            if death:
                return f"⚔️ {tier}击败{label}获得 {gain} 冥币，摸金HP -{hp_loss}。{event_text}{broke_text}{boss_extra}\n{death}"
            return (
                f"⚔️ {tier}击败{label}获得 {gain} 冥币，摸金HP -{hp_loss}。{event_text}{broke_text}{boss_extra}\n"
                f"> 摸金HP {session['hp']}/{session['hp_max']}"
            )
        # 失败
        if player_score <= monster_score * 0.5:
            hp_loss, tier = 60, "碾压败"
        else:
            hp_loss, tier = 40, "惨败"
        label = "BOSS，" if is_boss else ""
        session["hp"] = max(0, session["hp"] - hp_loss)
        death = self._tomb_after_damage(player, p, session)
        if death:
            return f"⚔️ 败给{label}摸金HP -{hp_loss}。{event_text}{broke_text}\n{death}"
        return (
            f"⚔️ 败给{label}摸金HP -{hp_loss}。{event_text}{broke_text}\n"
            f"> 摸金HP {session['hp']}/{session['hp_max']}"
        )

    # ---- 摸金交互指令 ----
    def _tomb_battle_cmd(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物。"
        session, is_coop = self._tomb_prepare(player)
        if not session:
            return "你没有进行中的摸金探险。"
        if session.get("status") in ("downed", "pick_card"):
            self._tomb_commit(player, session, is_coop)
            return "当前状态无法执行此操作。"
        settle = self._tomb_check_timeout(player, p, session)
        if settle:
            return settle
        pending = session.get("pending")
        if not pending or pending.get("type") not in ("M", "B"):
            return "这里没有要战斗的怪物（移动到怪物格或BOSS格才会遭遇）。"
        x, y = pending["x"], pending["y"]
        cells = session["map"]["cells"]
        if cells[y][x] != pending["type"]:
            session["pending"] = None
            self._tomb_commit(player, session, is_coop)
            return "该目标已被处理。"
        is_boss = pending.get("type") == "B"
        result = self._tomb_battle(player, p, session, summoned=False, forced_win=False, is_boss=is_boss)
        if self._tomb_session_exists(player):
            # 闪避时怪物格仍保留，必须保持 pending；只有怪物被真正清理后才清空
            if cells[y][x] not in ("M", "B"):
                session["pending"] = None
        self._tomb_commit(player, session, is_coop)
        return result

    def _tomb_altar_cmd(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物。"
        session, is_coop = self._tomb_prepare(player)
        if not session:
            return "你没有进行中的摸金探险。"
        if session.get("status") in ("downed", "pick_card"):
            self._tomb_commit(player, session, is_coop)
            return "当前状态无法执行此操作。"
        settle = self._tomb_check_timeout(player, p, session)
        if settle:
            return settle
        pending = session.get("pending")
        if not pending or pending.get("type") != "S":
            return "这里没有祭坛（移动到祭坛格才会发现）。"
        if self._get_card_effect(session, "altar_blocked", False):
            return "🌀 祭坛被命运之力封印，无法祭拜。"
        x, y = pending["x"], pending["y"]
        cells = session["map"]["cells"]
        if cells[y][x] != "S":
            session["pending"] = None
            self._tomb_commit(player, session, is_coop)
            return "该祭坛已被处理。"
        cells[y][x] = "."
        self._tomb_refresh_map(session)
        session["pending"] = None
        self._tomb_commit(player, session, is_coop)
        result = self._tomb_altar_event(player, p, session)
        # 祭坛事件可能触发战斗/死亡并结算，若 session 仍存在则把收益写回
        if self._tomb_session_exists(player):
            self._tomb_commit(player, session, is_coop)
        return result

    def _tomb_flee(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物。"
        session, is_coop = self._tomb_prepare(player)
        if not session:
            return "你没有进行中的摸金探险。"
        if session.get("status") in ("downed", "pick_card"):
            self._tomb_commit(player, session, is_coop)
            return "当前状态无法执行此操作。"
        settle = self._tomb_check_timeout(player, p, session)
        if settle:
            return settle
        pending = session.get("pending")
        if not pending or pending.get("type") not in ("M", "B"):
            return "这里没有可以逃跑的怪物或BOSS。"
        if pending.get("type") == "B":
            return "👹 BOSS 战无法逃跑，必须战斗！"
        if session.get("escapes", 0) <= 0 and not self._get_card_effect(session, "escape_guaranteed", False):
            return "🏃 逃跑次数已用完，只能战斗或使用道具。"
        x, y = pending["x"], pending["y"]
        cells = session["map"]["cells"]
        if cells[y][x] != pending["type"]:
            session["pending"] = None
            self._tomb_commit(player, session, is_coop)
            return "该目标已被处理。"
        if not self._get_card_effect(session, "escape_guaranteed", False):
            session["escapes"] -= 1
        cells[y][x] = "."
        self._tomb_refresh_map(session)
        session["pending"] = None
        session["player_pos"] = dict(session.get("prev_pos", session["player_pos"]))
        self._tomb_commit(player, session, is_coop)
        label = "BOSS" if pending.get("type") == "B" else "怪物"
        return (
            f"🏃 你成功从{label}战中逃脱，退回上一格，目标已消失在墓道中。"
            f"剩余逃跑次数 {session['escapes']}/{data.TOMB_ESCAPES_PER_RAID}。"
        )

    def _tomb_skip(self, player: dict) -> str:
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物。"
        session, is_coop = self._tomb_prepare(player)
        if not session:
            return "你没有进行中的摸金探险。"
        if session.get("status") in ("downed", "pick_card"):
            self._tomb_commit(player, session, is_coop)
            return "当前状态无法执行此操作。"
        settle = self._tomb_check_timeout(player, p, session)
        if settle:
            return settle
        if not session.get("pending"):
            return "当前没有待交互的对象。"
        pending = session["pending"]
        x, y = pending["x"], pending["y"]
        ptype = pending.get("type", "")
        if ptype in ("M", "B"):
            return "👹 怪物和 BOSS 无法跳过，请发送『战斗』或『逃跑』。"
        cells = session["map"]["cells"]
        if cells[y][x] != ptype:
            session["pending"] = None
            self._tomb_commit(player, session, is_coop)
            return "该目标已被处理。"
        cells[y][x] = "."
        self._tomb_refresh_map(session)
        session["pending"] = None
        self._tomb_commit(player, session, is_coop)
        return "你选择离开，目标已消失在墓道中。可继续 上/下/左/右 移动。"

    # --------------------------- 双排互动 ---------------------------
    def _tomb_rescue(self, player: dict) -> str:
        """摸金救援 —— 3 格内救援倒地队友。"""
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物。"
        coop = self._tomb_get_coop(player)
        if not coop or not coop.get("active"):
            return "你不在组队摸金中。"
        if int(time.time()) >= coop.get("deadline", 0):
            return self._tomb_settle_coop(player, p, coop, "timeout")
        qq = str(player.get("qq", ""))
        tqq = self._tomb_teammate_qq(player, coop)
        if not tqq:
            return "无法找到队友。"
        mydata = coop["players"].get(qq, {})
        tpdata = coop["players"].get(tqq, {})
        if mydata.get("status") == "downed":
            return "你已倒地，无法救援队友。"
        if tpdata.get("status") != "downed":
            return "队友没有倒地，无需救援。"
        mx, my_ = mydata["player_pos"]["x"], mydata["player_pos"]["y"]
        tx, ty = tpdata["player_pos"]["x"], tpdata["player_pos"]["y"]
        dist = abs(mx - tx) + abs(my_ - ty)
        if dist > data.TOMB_COOP_RANGE:
            return f"队友离你太远了（距离 {dist} 格，需在 {data.TOMB_COOP_RANGE} 格内）。"
        cost = max(1, int(mydata["hp_max"] * data.TOMB_COOP_RESCUE_HP_COST))
        mydata["hp"] = max(1, mydata["hp"] - cost)
        revive_hp = max(1, int(tpdata["hp_max"] * data.TOMB_COOP_RESCUE_REVIVE_HP))
        tpdata["hp"] = revive_hp
        tpdata["status"] = "active"
        return (
            f"## 救援成功\n"
            f"你将 {tqq} 从绝境中救起！（HP恢复至 {revive_hp}）\n"
            f"你消耗了 {cost} HP（剩余 {mydata['hp']}）。"
        )

    def _tomb_loot(self, player: dict) -> str:
        """摸金捡取 —— 3 格内捡取倒地队友的全部物品和冥币。"""
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物。"
        coop = self._tomb_get_coop(player)
        if not coop or not coop.get("active"):
            return "你不在组队摸金中。"
        if int(time.time()) >= coop.get("deadline", 0):
            return self._tomb_settle_coop(player, p, coop, "timeout")
        qq = str(player.get("qq", ""))
        tqq = self._tomb_teammate_qq(player, coop)
        mydata = coop["players"].get(qq, {})
        tpdata = coop["players"].get(tqq, {})
        if mydata.get("status") == "downed":
            return "你已倒地，无法捡取。"
        if tpdata.get("status") != "downed":
            return "队友未倒地，无需捡取。"
        mx, my_ = mydata["player_pos"]["x"], mydata["player_pos"]["y"]
        tx, ty = tpdata["player_pos"]["x"], tpdata["player_pos"]["y"]
        if abs(mx - tx) + abs(my_ - ty) > data.TOMB_COOP_RANGE:
            return f"队友离你太远了。"
        transferred = []
        t_inv = tpdata.get("inventory", {})
        my_inv = mydata.setdefault("inventory", {})
        for item, count in list(t_inv.items()):
            if count > 0:
                my_inv[item] = my_inv.get(item, 0) + count
                transferred.append(f"{item}×{count}")
        tpdata["inventory"] = {}
        t_mb = tpdata.get("mingbi", 0)
        mydata["mingbi"] = mydata.get("mingbi", 0) + t_mb
        tpdata["mingbi"] = 0
        if t_mb > 0:
            transferred.append(f"冥币×{t_mb}")
        if not transferred:
            return "队友身上没有可捡取的物品。"
        return f"## 捡取完成\n已将队友的 {'、'.join(transferred)} 转移到你的背包。"

    def _tomb_transfer(self, player: dict, tokens: list[str]) -> str:
        """摸金传送 用户ID 物品/冥币 数量 —— 3 格内传送物品或冥币给队友。"""
        p = self._need_pet(player)
        if not p:
            return "你还没有宠物。"
        coop = self._tomb_get_coop(player)
        if not coop or not coop.get("active"):
            return "你不在组队摸金中。"
        if int(time.time()) >= coop.get("deadline", 0):
            return self._tomb_settle_coop(player, p, coop, "timeout")
        qq = str(player.get("qq", ""))
        tqq = self._tomb_teammate_qq(player, coop)
        if len(tokens) < 3:
            return "用法：摸金传送 用户ID 物品名/冥币 数量"
        target_qq = tokens[1]
        item_name = tokens[2]
        count = self._parse_count(tokens, 3) if len(tokens) > 3 else 1
        if target_qq != tqq:
            return "只能传送给自己的队友。"
        mydata = coop["players"].get(qq, {})
        tpdata = coop["players"].get(tqq, {})
        if mydata.get("status") == "downed":
            return "你已倒地，无法传送。"
        if tpdata.get("status") == "downed":
            return "队友已倒地，无法接收。可先「摸金救援」。"
        mx, my_ = mydata["player_pos"]["x"], mydata["player_pos"]["y"]
        tx, ty = tpdata["player_pos"]["x"], tpdata["player_pos"]["y"]
        if abs(mx - tx) + abs(my_ - ty) > data.TOMB_COOP_RANGE:
            return f"队友离你太远了（距离 {abs(mx - tx) + abs(my_ - ty)}，需在 {data.TOMB_COOP_RANGE} 格内）。"
        if item_name == "冥币":
            if mydata.get("mingbi", 0) < count:
                return f"你的冥币不足（当前 {mydata.get('mingbi', 0)}）。"
            mydata["mingbi"] -= count
            tpdata["mingbi"] = tpdata.get("mingbi", 0) + count
            return f"## 传送完成\n向 {target_qq} 传送 **冥币×{count}**。"
        my_inv = mydata.get("inventory", {})
        if my_inv.get(item_name, 0) < count:
            return f"你的背包中没有足够的「{item_name}」。"
        my_inv[item_name] -= count
        if my_inv[item_name] <= 0:
            my_inv.pop(item_name, None)
        tp_inv = tpdata.setdefault("inventory", {})
        tp_inv[item_name] = tp_inv.get(item_name, 0) + count
        return f"## 传送完成\n向 {target_qq} 传送 **{item_name}×{count}**。"
    # --------------------------- 双排互动结束 ---------------------------

    def _tomb_equip(self, player: dict, tokens: list[str]) -> str:
        if self._tomb_in_raid(player):
            return "摸金过程中不能调整装备，请先结束本局（摸撤/摸弃）。"
        if len(tokens) < 2:
            return "用法：摸装 武器名"
        name = tokens[1]
        if name not in data.TOMB_WEAPONS:
            return "没有这种武器。"
        weapons = self.store.get_tomb_weapons(player)
        if name not in weapons:
            return f"你还没有『{name}』，先发送 `摸店` 购买。"
        if weapons[name].get("location") != "equip":
            return f"『{name}』在储物柜里，先发送 `摸带 {name}` 放入装备背包。"
        self.store.equip_tomb_weapon(player, name)
        atk = data.TOMB_WEAPONS[name]["attack"]
        key = self._tomb_key(player.get("group", ""), player.get("qq", ""))
        session = self._tomb_sessions.get(key)
        if session:
            session["weapon"] = name
            session["weapon_attack"] = atk
        return f"🗡 已装备『{name}』（攻击+{atk}，耐久{weapons[name].get('durability')}）。"

    def _tomb_move_item(self, player: dict, tokens: list[str], direction: str) -> str:
        """direction: 'to_equip' = 储物柜→装备背包；'to_storage' = 装备背包→储物柜。"""
        if self._tomb_in_raid(player):
            return "摸金过程中不能调整背包，请先结束本局（摸撤/摸弃）。"
        if len(tokens) < 2:
            return "用法：摸带 道具名 [数量]  或  摸存 道具名 [数量]"
        name = tokens[1]
        count = self._parse_count(tokens, 2) if len(tokens) > 2 else 1
        if name in data.TOMB_WEAPONS:
            if direction == "to_equip":
                if not self.store.move_tomb_weapon(player, name, "equip"):
                    return f"储物柜没有武器『{name}』。"
                return f"🗡 『{name}』已放入装备背包（带入摸金）。"
            if not self.store.move_tomb_weapon(player, name, "storage"):
                return f"装备背包没有武器『{name}』。"
            return f"🗡 『{name}』已放回储物柜（安全保管）。"
        if name not in data.TOMB_ITEMS:
            return f"没有『{name}』这种物品。"
        if direction == "to_equip":
            if not self.store.move_tomb_item(player, name, count, "storage", "equip"):
                return f"储物柜中『{name}』不足。"
            return f"🎒 已把『{name}』×{count} 放入装备背包（带入摸金）。"
        if not self.store.move_tomb_item(player, name, count, "equip", "storage"):
            return f"装备背包中『{name}』不足。"
        return f"🗄 已把『{name}』×{count} 放回储物柜（安全保管）。"

    def _tomb_pack(self, player: dict) -> str:
        st = self.store.tomb_state(player)
        storage = st.get("storage_items", {})
        equip = st.get("equip_items", {})
        weapons = st.get("weapons", {})
        equip_text = "、".join(f"{k}×{v}" for k, v in equip.items() if v > 0) or "空"
        storage_text = "、".join(f"{k}×{v}" for k, v in storage.items() if v > 0) or "空"
        equip_weps = "、".join(
            f"{k}(耐久{w.get('durability')})" for k, w in weapons.items() if w.get("location") == "equip"
        ) or "无"
        storage_weps = "、".join(
            f"{k}(耐久{w.get('durability')})" for k, w in weapons.items() if w.get("location") != "equip"
        ) or "无"
        return (
            "## 摸金背包\n\n"
            "【装备背包】带入墓中，失败掉落\n"
            f"道具：{equip_text}\n"
            f"武器：{equip_weps}\n\n"
            "【储物柜】安全保管\n"
            f"道具：{storage_text}\n"
            f"武器：{storage_weps}\n\n"
            "操作：\n"
            "- 带入：摸带 道具名 数量\n"
            "- 取出：摸存 道具名 数量"
        )

    # ---- 摸金排行 / 神榜 ----
    @staticmethod
    def _tomb_display_qq(qq: str) -> str:
        """摸金排行统一显示用户 ID（QQ）。"""
        return str(qq or "未知").replace("|", "丨")

    def _tomb_rank(self, player: dict, group_id: str) -> str:
        """摸金财富全服排行（按永久冥币）。"""
        entries = []
        for qq, st in self.store._data.get("tomb_players", {}).items():
            mingbi = st.get("mingbi", 0)
            if mingbi > 0:
                entries.append((str(qq), self._tomb_display_qq(qq), mingbi))
        entries.sort(key=lambda x: x[2], reverse=True)
        if not entries:
            return "暂无玩家登上摸金排行。"
        lines = ["## 🏺 摸金排行（全服）"]
        my_qq = str(player.get("qq", ""))
        my_st = self.store._data.get("tomb_players", {}).get(my_qq, {})
        my_mingbi = my_st.get("mingbi", 0)
        if my_mingbi > 0:
            my_rank = 1 + sum(1 for _, _, m in entries if m > my_mingbi)
            lines.append(f"> 我的排名：**{my_rank}**　·　我的冥币：**{my_mingbi}**")
        else:
            lines.append(f"> 我的冥币：**{my_mingbi}**（还未获得冥币）")
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines.append("")
        lines.append("| 排名 | 用户ID | 冥币 |")
        lines.append("|:--:|:--:|--:|")
        for i, (_, qq_text, mingbi) in enumerate(entries[: self.rank_size], 1):
            rk = medals.get(i, str(i))
            lines.append(f"| {rk} | {qq_text} | {mingbi} |")
        return "\n".join(lines)

    def _tomb_daily_rank(self, player: dict) -> str:
        """今日摸金神榜（按今日获得冥币）。"""
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        entries = []
        for qq, st in self.store._data.get("tomb_players", {}).items():
            gain = st.get("daily_gains", {}).get(today, 0)
            if gain > 0:
                entries.append((str(qq), self._tomb_display_qq(qq), gain))
        entries.sort(key=lambda x: x[2], reverse=True)
        if not entries:
            return "今日还没有玩家在摸金中获得冥币。"
        lines = [
            "## 🔥 今日摸金神榜",
            f"> 统计 {today} 00:00 至今全服摸金获得冥币情况，每日 0 点清空。",
        ]
        my_qq = str(player.get("qq", ""))
        my_st = self.store._data.get("tomb_players", {}).get(my_qq, {})
        my_gain = my_st.get("daily_gains", {}).get(today, 0)
        if my_gain > 0:
            my_rank = 1 + sum(1 for _, _, g in entries if g > my_gain)
            lines.append(f"> 我的排名：**{my_rank}**　·　今日获得：**{my_gain}** 冥币")
        else:
            lines.append(f"> 我今日获得：**{my_gain}** 冥币")
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines.append("")
        lines.append("| 排名 | 用户ID | 今日获得冥币 |")
        lines.append("|:--:|:--:|--:|")
        for i, (_, qq_text, gain) in enumerate(entries[: self.rank_size], 1):
            rk = medals.get(i, str(i))
            lines.append(f"| {rk} | {qq_text} | {gain} |")
        lines.append("")
        lines.append(
            "> 🎁 前三名可于次日 0 点后发送『领取摸金奖励』领取随机宠物经验（5000~50000）。"
        )
        return "\n".join(lines)

    def _tomb_yesterday_daily_rank(self, player: dict) -> str:
        """昨日摸金神榜（按昨日获得冥币）。"""
        from datetime import datetime, timedelta
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        entries = []
        for qq, st in self.store._data.get("tomb_players", {}).items():
            gain = st.get("daily_gains", {}).get(yesterday, 0)
            if gain > 0:
                entries.append((str(qq), self._tomb_display_qq(qq), gain))
        entries.sort(key=lambda x: x[2], reverse=True)
        if not entries:
            return f"昨日（{yesterday}）没有玩家在摸金中获得冥币。"
        lines = [
            "## 昨日摸金神榜",
            f"> 统计 {yesterday} 全服摸金获得冥币情况。",
        ]
        my_qq = str(player.get("qq", ""))
        my_st = self.store._data.get("tomb_players", {}).get(my_qq, {})
        my_gain = my_st.get("daily_gains", {}).get(yesterday, 0)
        if my_gain > 0:
            my_rank = 1 + sum(1 for _, _, g in entries if g > my_gain)
            lines.append(f"> 我的排名：**{my_rank}**　·　昨日获得：**{my_gain}** 冥币")
        else:
            lines.append(f"> 我昨日获得：**{my_gain}** 冥币")
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines.append("")
        lines.append("| 排名 | 用户ID | 昨日获得冥币 |")
        lines.append("|:--:|:--:|--:|")
        for i, (_, qq_text, gain) in enumerate(entries[: self.rank_size], 1):
            rk = medals.get(i, str(i))
            lines.append(f"| {rk} | {qq_text} | {gain} |")
        lines.append("")
        lines.append(
            "> 前三名可发送『领取摸金奖励』领取随机宠物经验（5000~50000）。"
        )
        return "\n".join(lines)

    def _tomb_claim_daily_reward(self, player: dict, group_id: str) -> str:
        """领取昨日今日摸金神榜前三奖励（宠物主经验，发给当前群宠物）。"""
        p = player.get("pet")
        if not p:
            return "你还没有宠物，无法领取经验奖励。"
        ok, exp, msg = self.store.claim_tomb_daily_reward(
            player, str(group_id), str(player.get("qq", ""))
        )
        if not ok:
            return msg
        petmod.add_exp(p, exp)
        level_note = self._auto_level_note(player, p)
        return (
            f"🎁 昨日摸金神榜强者奖励到账！宠物经验 +{exp}。{level_note}"
        )

    def _tomb_redeem_exp(self, player: dict, tokens: list[str]) -> str:
        """把暂存的摸金宠物经验兑换到当前群宠物。

        用法：
        - 摸金兑换                 一键兑换全部
        - 摸金兑换 全部            一键兑换全部
        - 摸金兑换 10000           只兑换 10000 点
        """
        pending = self.store.get_tomb_pending_pet_exp(player)
        if pending <= 0:
            return "你没有待兑换的摸金宠物经验。"
        p = player.get("pet")
        if not p:
            return "你没有宠物，无法兑换经验。"

        # 解析数量
        amount_str = tokens[1] if len(tokens) > 1 else ""
        if amount_str and amount_str not in ("全部", "all"):
            try:
                amount = int(amount_str)
            except ValueError:
                return "用法：摸金兑换 [数量/全部]，数量请填写整数。"
            if amount <= 0:
                return "兑换数量必须大于 0。"
            if amount > pending:
                return f"待兑换经验只有 {pending} 点，不足 {amount} 点。"
        else:
            amount = pending

        actual = self.store.consume_tomb_pending_pet_exp(player, amount)
        petmod.add_exp(p, actual)
        remain = self.store.get_tomb_pending_pet_exp(player)
        note = f"，还剩余 {remain} 点" if remain > 0 else "，已全部兑换"
        return f"🎁 摸金经验兑换成功！当前群宠物 +{actual} 经验{note}。{self._auto_level_note(player, p)}"

    # ---- 地图生成与绘图 ----
    @staticmethod
    def _tomb_reachable(cells: list[list[str]], sx: int, sy: int) -> set[tuple[int, int]]:
        h, w = len(cells), len(cells[0])
        visited = set()
        dq = deque([(sx, sy)])
        visited.add((sx, sy))
        while dq:
            x, y = dq.popleft()
            for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in visited and cells[ny][nx] != "#":
                    visited.add((nx, ny))
                    dq.append((nx, ny))
        return visited

    @staticmethod
    def _tomb_bfs_dist(
        cells: list[list[str]], sx: int, sy: int, ex: int, ey: int
    ) -> int | None:
        h, w = len(cells), len(cells[0])
        visited = {(sx, sy): 0}
        dq = deque([(sx, sy)])
        while dq:
            x, y = dq.popleft()
            if (x, y) == (ex, ey):
                return visited[(x, y)]
            for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in visited and cells[ny][nx] != "#":
                    visited[(nx, ny)] = visited[(x, y)] + 1
                    dq.append((nx, ny))
        return None

    @staticmethod
    def _tomb_bfs_distances(
        cells: list[list[str]], sx: int, sy: int
    ) -> dict[tuple[int, int], int]:
        """返回从 (sx,sy) 到所有可达格子的最短距离。"""
        h, w = len(cells), len(cells[0])
        dist = {(sx, sy): 0}
        dq = deque([(sx, sy)])
        while dq:
            x, y = dq.popleft()
            for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in dist and cells[ny][nx] != "#":
                    dist[(nx, ny)] = dist[(x, y)] + 1
                    dq.append((nx, ny))
        return dist

    def _tomb_generate_map(self, difficulty: int, coop: bool = False) -> list[list[str]]:
        """生成复杂迷宫：墙为 #，通路为 .，随机入口 E，最远出口 X，并带少量环路。
        coop=True 时按 TOMB_COOP_MULT 倍率缩放怪物/宝箱/陷阱等数量。"""
        base_cfg = data.TOMB_DIFFICULTIES[difficulty]
        # coop 模式下缩放实体数量
        mult = data.TOMB_COOP_MULT if coop else 1.0
        cfg = dict(base_cfg)
        for key in ("monsters", "chests", "traps", "altars", "gold_piles", "gas_zones", "portals", "springs", "bosses"):
            if key in cfg:
                cfg[key] = max(1, int(cfg[key] * mult))
        if coop:
            cfg["required"] = int(cfg["required"] * data.TOMB_COOP_REQUIRED_MULT)
        w, h = cfg["size"]
        # 迷宫算法需要奇数尺寸；配置里已保持奇数，这里做保险处理
        if w % 2 == 0:
            w += 1
        if h % 2 == 0:
            h += 1

        cells = [["#" for _ in range(w)] for _ in range(h)]
        stack = [(1, 1)]
        cells[1][1] = "."
        visited = {(1, 1)}
        directions = [(0, -2), (0, 2), (-2, 0), (2, 0)]

        while stack:
            x, y = stack[-1]
            found = False
            for dx, dy in random.sample(directions, len(directions)):
                nx, ny = x + dx, y + dy
                if 0 < nx < w - 1 and 0 < ny < h - 1 and (nx, ny) not in visited:
                    cells[ny][nx] = "."
                    cells[y + dy // 2][x + dx // 2] = "."
                    visited.add((nx, ny))
                    stack.append((nx, ny))
                    found = True
                    break
            if not found:
                stack.pop()

        floors = [(x, y) for y in range(1, h - 1) for x in range(1, w - 1) if cells[y][x] == "."]
        if not floors:
            cells[h - 2][w - 2] = "X"
            return cells

        # 随机选择入口，并把出口设在迷宫最远端
        entrance = random.choice(floors)
        dist_map = self._tomb_bfs_distances(cells, entrance[0], entrance[1])
        exit_pos = max(floors, key=lambda pos: dist_map.get(pos, 0))
        ex, ey = entrance
        cells[ey][ex] = "E"
        ex, ey = exit_pos
        cells[ey][ex] = "X"

        # 增加少量环路，让迷宫更复杂（打通一些分隔通道的墙）
        loops = difficulty * 2
        candidates = []
        for y in range(2, h - 2):
            for x in range(2, w - 2):
                if cells[y][x] != "#":
                    continue
                # 只打通左右或上下两侧都是通道的墙
                lr = cells[y][x - 1] != "#" and cells[y][x + 1] != "#"
                ud = cells[y - 1][x] != "#" and cells[y + 1][x] != "#"
                if lr or ud:
                    candidates.append((x, y))
        if candidates:
            for wx, wy in random.sample(candidates, min(loops, len(candidates))):
                cells[wy][wx] = "."

        available = [pos for pos in floors if pos not in (entrance, exit_pos)]
        need = (
            cfg["monsters"] + cfg["chests"] + cfg["traps"] + cfg["altars"]
            + cfg.get("gold_piles", 0) + cfg.get("gas_zones", 0)
            + cfg.get("portals", 0) + cfg.get("springs", 0) + cfg.get("bosses", 0)
        )
        if len(available) < need:
            need = len(available)
        chosen = random.sample(available, need)
        i = 0
        for _ in range(cfg["monsters"]):
            if i < len(chosen):
                cells[chosen[i][1]][chosen[i][0]] = "M"
                i += 1
        for _ in range(cfg["chests"]):
            if i < len(chosen):
                cells[chosen[i][1]][chosen[i][0]] = "C"
                i += 1
        for _ in range(cfg["traps"]):
            if i < len(chosen):
                cells[chosen[i][1]][chosen[i][0]] = "T"
                i += 1
        for _ in range(cfg["altars"]):
            if i < len(chosen):
                cells[chosen[i][1]][chosen[i][0]] = "S"
                i += 1
        for _ in range(cfg.get("gold_piles", 0)):
            if i < len(chosen):
                cells[chosen[i][1]][chosen[i][0]] = "$"
                i += 1
        for _ in range(cfg.get("gas_zones", 0)):
            if i < len(chosen):
                cells[chosen[i][1]][chosen[i][0]] = "G"
                i += 1
        for _ in range(cfg.get("portals", 0)):
            if i < len(chosen):
                cells[chosen[i][1]][chosen[i][0]] = "P"
                i += 1
        for _ in range(cfg.get("springs", 0)):
            if i < len(chosen):
                cells[chosen[i][1]][chosen[i][0]] = "H"
                i += 1
        for _ in range(cfg.get("bosses", 0)):
            if i < len(chosen):
                cells[chosen[i][1]][chosen[i][0]] = "B"
                i += 1
        return cells

    def _tomb_draw_map(self, session: dict) -> str:
        """绘制迷宫式地图，墙面带立体感，特殊格子使用更易辨识的图标。"""
        cfg = data.TOMB_DIFFICULTIES[session["difficulty"]]
        cells = session["map"]["cells"]
        h, w = len(cells), len(cells[0])
        cell = data.TOMB_CELL_SIZE
        pad = data.TOMB_PADDING
        header_h = 44
        footer_h = 64
        img_w = w * cell + pad * 2
        img_h = h * cell + pad * 2 + header_h + footer_h

        img = Image.new("RGB", (img_w, img_h), data.TOMB_COLORS["bg"])
        draw = ImageDraw.Draw(img)

        # 优先加载中文字体；服务器若未安装会显示方框，需要安装 fonts-wqy-zenhei 等
        font = None
        small_font = None
        for font_path in (
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansMonoCJK-Regular.ttc",
            "/usr/share/fonts/opentype/source-han-sans/SourceHanSansCN-Regular.otf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/System/Library/Fonts/PingFang.ttc",
        ):
            try:
                font = ImageFont.truetype(font_path, 18)
                small_font = ImageFont.truetype(font_path, 14)
                break
            except Exception:
                continue

        # 顶部标题（全中文，避免英文）
        mode_label = "（双排）" if session.get("_is_coop") or "players" in session else ""
        title = f"摸金地图{mode_label}  难度{session['difficulty']}  {cfg['name']}"
        if font:
            draw.text((pad, 12), title, fill=data.TOMB_COLORS["text"], font=font)
        else:
            draw.text((pad, 12), title, fill=data.TOMB_COLORS["text"])

        ox, oy = pad, pad + header_h

        for y in range(h):
            for x in range(w):
                cx = ox + x * cell
                cy = oy + y * cell
                c = cells[y][x]

                if c == "#":
                    # 石墙块：带高光与阴影，呈现立体墓穴墙壁
                    draw.rectangle([cx, cy, cx + cell - 1, cy + cell - 1], fill=data.TOMB_COLORS["wall"])
                    draw.line([(cx, cy), (cx + cell - 1, cy)], fill=(95, 90, 90), width=2)
                    draw.line([(cx, cy), (cx, cy + cell - 1)], fill=(95, 90, 90), width=2)
                    draw.line([(cx + cell - 1, cy), (cx + cell - 1, cy + cell - 1)], fill=(35, 32, 32), width=2)
                    draw.line([(cx, cy + cell - 1), (cx + cell - 1, cy + cell - 1)], fill=(35, 32, 32), width=2)
                else:
                    # 地板：深色砖块，略微区分奇偶格
                    base = data.TOMB_COLORS["floor"]
                    alt = (34, 31, 31)
                    draw.rectangle([cx, cy, cx + cell - 1, cy + cell - 1], fill=alt if (x + y) % 2 else base)
                    # 细边框让通道更清晰
                    draw.rectangle([cx, cy, cx + cell - 1, cy + cell - 1], outline=(42, 39, 39), width=1)

                cx_c = cx + cell // 2
                cy_c = cy + cell // 2
                r = cell // 3

                if c == "X":
                    diamond = [(cx_c, cy_c - r), (cx_c + r, cy_c), (cx_c, cy_c + r), (cx_c - r, cy_c)]
                    draw.polygon(diamond, fill=data.TOMB_COLORS["exit"], outline=(255, 200, 200), width=2)
                    draw.line([(cx_c, cy_c - r), (cx_c, cy_c + r)], fill=(150, 50, 50), width=2)
                    draw.line([(cx_c - r, cy_c), (cx_c + r, cy_c)], fill=(150, 50, 50), width=2)
                elif c == "M":
                    # 白骷髅头 + 红眼窝，辨识度高
                    rh = int(r * 0.7)
                    draw.ellipse([cx_c - r, cy_c - rh, cx_c + r, cy_c + rh], fill=(235, 235, 235))
                    draw.ellipse([cx_c - r // 2, cy_c - rh // 3, cx_c - r // 4, cy_c], fill=(160, 40, 40))
                    draw.ellipse([cx_c + r // 4, cy_c - rh // 3, cx_c + r // 2, cy_c], fill=(160, 40, 40))
                    draw.rectangle([cx_c - r // 2, cy_c + rh // 4, cx_c + r // 2, cy_c + rh], fill=data.TOMB_COLORS["monster"])
                elif c == "C":
                    box = cell // 3
                    draw.rounded_rectangle(
                        [cx_c - box, cy_c - box // 2, cx_c + box, cy_c + box // 2],
                        radius=4,
                        fill=data.TOMB_COLORS["chest"],
                        outline=(255, 230, 150),
                        width=2,
                    )
                    draw.rectangle([cx_c - box // 4, cy_c - box // 3, cx_c + box // 4, cy_c], fill=(100, 75, 25))
                elif c == "T":
                    # 八角尖刺陷阱
                    pts = [
                        (cx_c, cy_c - r),
                        (cx_c + r * 0.4, cy_c - r * 0.4),
                        (cx_c + r, cy_c),
                        (cx_c + r * 0.4, cy_c + r * 0.4),
                        (cx_c, cy_c + r),
                        (cx_c - r * 0.4, cy_c + r * 0.4),
                        (cx_c - r, cy_c),
                        (cx_c - r * 0.4, cy_c - r * 0.4),
                    ]
                    draw.polygon(pts, fill=data.TOMB_COLORS["trap"], outline=(255, 200, 255), width=1)
                elif c == "S":
                    # 蓝色祭坛宝珠
                    draw.ellipse([cx_c - r, cy_c - r, cx_c + r, cy_c + r], fill=data.TOMB_COLORS["altar"], outline=(200, 230, 255), width=2)
                    flame = [
                        (cx_c, cy_c - r),
                        (cx_c + r // 2, cy_c),
                        (cx_c, cy_c + r // 3),
                        (cx_c - r // 2, cy_c),
                    ]
                    draw.polygon(flame, fill=(220, 240, 255))
                elif c == "$":
                    # 金币堆：金色圆盘 + $ 符号
                    draw.ellipse([cx_c - r, cy_c - r, cx_c + r, cy_c + r], fill=data.TOMB_COLORS["gold"], outline=(255, 240, 100), width=2)
                    if small_font:
                        draw.text((cx_c - cell // 6, cy_c - cell // 6), "$", fill=(120, 90, 0), font=small_font)
                elif c == "G":
                    # 毒雾：半透明绿色漩涡
                    g_r = int(r * 0.9)
                    draw.ellipse([cx_c - g_r, cy_c - g_r, cx_c + g_r, cy_c + g_r], fill=data.TOMB_COLORS["gas"], outline=(180, 255, 150), width=1)
                    draw.arc([cx_c - g_r // 2, cy_c - g_r // 2, cx_c + g_r // 2, cy_c + g_r // 2], 0, 270, fill=(40, 100, 40), width=2)
                elif c == "P":
                    # 传送门：紫色漩涡环
                    p_r = int(r * 0.85)
                    draw.ellipse([cx_c - p_r, cy_c - p_r, cx_c + p_r, cy_c + p_r], fill=None, outline=data.TOMB_COLORS["portal"], width=3)
                    draw.ellipse([cx_c - p_r // 2, cy_c - p_r // 2, cx_c + p_r // 2, cy_c + p_r // 2], fill=None, outline=(220, 160, 255), width=2)
                    draw.ellipse([cx_c - 3, cy_c - 3, cx_c + 3, cy_c + 3], fill=(220, 160, 255))
                elif c == "H":
                    # 生命泉：青色水滴
                    h_r = int(r * 0.8)
                    draw.ellipse([cx_c - h_r, cy_c - h_r, cx_c + h_r, cy_c + h_r], fill=data.TOMB_COLORS["spring"], outline=(180, 255, 255), width=2)
                    draw.ellipse([cx_c - h_r // 3, cy_c - h_r // 2, cx_c + h_r // 3, cy_c + h_r // 3], fill=(255, 255, 255))
                elif c == "B":
                    # Boss：红色大骷髅 + 交叉骨
                    b_r = int(r * 0.95)
                    draw.ellipse([cx_c - b_r, cy_c - b_r, cx_c + b_r, cy_c + b_r], fill=(40, 10, 10), outline=data.TOMB_COLORS["boss"], width=3)
                    bs = b_r // 2
                    draw.line([(cx_c - bs, cy_c - bs), (cx_c + bs, cy_c + bs)], fill=data.TOMB_COLORS["boss"], width=3)
                    draw.line([(cx_c + bs, cy_c - bs), (cx_c - bs, cy_c + bs)], fill=data.TOMB_COLORS["boss"], width=3)
                    # 头顶皇冠标记
                    crown_y = cy_c - b_r - 3
                    draw.polygon([(cx_c - 5, crown_y), (cx_c + 5, crown_y), (cx_c + 3, crown_y - 5), (cx_c - 3, crown_y - 5)], fill=(255, 200, 50))

        # 底部图例（拆为三行，避免小图宽度溢出）
        base_y = oy + h * cell + 8
        line_spacing = 16
        legend_lines = [
            "红菱=出口  金箱=宝箱  白骷髅=怪物  紫刺=陷阱",
            "蓝珠=祭坛  黄圆=金币  绿雾=毒雾  紫环=传送",
            f"青滴=生命泉  {'红骷髅=BOSS  ' if cfg.get('bosses', 0) > 0 else ''}需带回 {session.get('required', cfg['required'])} 冥币",
        ]
        for i, line in enumerate(legend_lines):
            if small_font:
                draw.text((pad, base_y + i * line_spacing), line, fill=(170, 170, 170), font=small_font)
            else:
                draw.text((pad, base_y + i * line_spacing), line, fill=(170, 170, 170))

        filename = f"tomb_{uuid.uuid4().hex}.png"
        path = self.store.custom_images_dir / filename
        img.save(path, "PNG")
        return filename

    def _tomb_image_url(self, filename: str) -> str:
        """返回图片的完整 HTTP URL。"""
        host = str(self.config.get("web_host", "103.38.83.146"))
        if host in ("0.0.0.0", "127.0.0.1", "localhost"):
            host = "103.38.83.146"
        port = int(self.config.get("web_port", 7799))
        return f"http://{host}:{port}/custom_images/{urllib.parse.quote(filename)}"

    def _tomb_image_md(self, filename: str) -> str:
        """返回与宠物图片一致的 Markdown 图片语法，带尺寸标记，确保手机端可渲染。"""
        url = self._tomb_image_url(filename)
        return f"![摸金地图 #{images._IMG_DISPLAY} #{images._IMG_DISPLAY}]({url})"

    def _tomb_player_map_md(self, session: dict) -> str:
        """生成带迷雾的玩家视角地图，返回 Markdown 图片串。"""
        return self._tomb_image_md(self._save_player_map(session))

    def _save_player_map(self, session: dict) -> str:
        """生成带迷雾的玩家视角地图文件，返回文件名。"""
        base_path = self.store.custom_images_dir / session["image"]
        try:
            img = Image.open(base_path).convert("RGB")
        except Exception:
            return session["image"]

        cells = session["map"]["cells"]
        h, w = len(cells), len(cells[0])
        cell = data.TOMB_CELL_SIZE
        pad = data.TOMB_PADDING
        header_h = 44
        ox, oy = pad, pad + header_h
        px, py = session["player_pos"]["x"], session["player_pos"]["y"]
        cx = ox + px * cell + cell // 2
        cy = oy + py * cell + cell // 2

        # 命运卡牌：无迷雾
        if self._get_card_effect(session, "no_fog", False):
            fog = img  # 全图可见，无需迷雾
        else:
            # 命运卡牌：视野加成
            vision_bonus = int(self._get_card_effect(session, "vision_bonus", 0))
            inner_r = (2 + vision_bonus) * cell
            outer_r = inner_r + cell
            # 全黑底图
            fog_img = Image.new("RGB", img.size, (5, 5, 5))
            # 视野遮罩：内圈清晰，外圈微亮，之外全黑
            mask = Image.new("L", img.size, 0)
            draw_mask = ImageDraw.Draw(mask)
            draw_mask.ellipse(
                [cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r],
                fill=90,
            )
            draw_mask.ellipse(
                [cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r],
                fill=255,
            )
            fog_img.paste(img, (0, 0), mask)

            # 顶部标题栏和底部图例不受迷雾遮挡，始终可见
            header_region = img.crop((0, 0, img.width, oy))
            fog_img.paste(header_region, (0, 0))
            footer_top = oy + h * cell
            footer_region = img.crop((0, footer_top, img.width, img.height))
            fog_img.paste(footer_region, (0, footer_top))
            fog = fog_img

        # 玩家定位标记（青色）
        draw = ImageDraw.Draw(fog)
        r = cell // 3
        draw.ellipse([cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2], outline=data.TOMB_COOP_SELF_COLOR, width=3)
        draw.ellipse([cx - r // 2, cy - r // 2, cx + r // 2, cy + r // 2], fill=data.TOMB_COOP_SELF_COLOR)

        # 双排：绘制队友标记
        coop = session.get("_coop_parent")
        if coop and session.get("_is_coop"):
            tqq = coop.get("teammate") if session.get("_coop_self_qq") == coop.get("leader") else coop.get("leader")
            # fallback: session may have _coop_qq from prepare
            tqq = tqq or (session.get("_coop_qq", ""))
            # try getting teammate from the other player in players dict
            for pq, pd in coop.get("players", {}).items():
                if pq != session.get("_coop_self_qq", session.get("_coop_qq", "")):
                    tpd = pd
                    tpx, tpy = tpd["player_pos"]["x"], tpd["player_pos"]["y"]
                    tcx = ox + tpx * cell + cell // 2
                    tcy = oy + tpy * cell + cell // 2
                    if tpd.get("status") == "downed":
                        # 倒地队友：红色骷髅（穿透迷雾）
                        tr = cell // 3
                        draw.ellipse([tcx - tr - 3, tcy - tr - 3, tcx + tr + 3, tcy + tr + 3],
                                    fill=(180, 20, 20), outline=(255, 80, 80), width=3)
                        draw.line([(tcx - tr, tcy - tr), (tcx + tr, tcy + tr)], fill=(255, 255, 255), width=2)
                        draw.line([(tcx + tr, tcy - tr), (tcx - tr, tcy + tr)], fill=(255, 255, 255), width=2)
                    else:
                        # 正常队友：黄色标记
                        tr = cell // 3
                        draw.ellipse([tcx - tr - 2, tcy - tr - 2, tcx + tr + 2, tcy + tr + 2],
                                    outline=data.TOMB_COOP_TEAMMATE_COLOR, width=3)
                        draw.ellipse([tcx - tr // 2, tcy - tr // 2, tcx + tr // 2, tcy + tr // 2],
                                    fill=data.TOMB_COOP_TEAMMATE_COLOR)
                    break

        # 命运卡牌：Boss 位置穿透迷雾可见
        if self._get_card_effect(session, "boss_visible", False):
            for by in range(h):
                for bx in range(w):
                    if cells[by][bx] == "B":
                        bcx = ox + bx * cell + cell // 2
                        bcy = oy + by * cell + cell // 2
                        tr = cell // 4
                        draw.ellipse(
                            [bcx - tr, bcy - tr, bcx + tr, bcy + tr],
                            fill=(255, 60, 60), outline=(255, 200, 200), width=2,
                        )

        filename = f"tomb_p_{uuid.uuid4().hex}.png"
        path = self.store.custom_images_dir / filename
        fog.save(path, "PNG")
        return filename

    def _tomb_refresh_map(self, session: dict) -> None:
        """重新绘制基础地图（用于宝箱/怪物/陷阱/祭坛被交互后从地图上消失）。"""
        session["image"] = self._tomb_draw_map(session)

    def _tomb_format_surroundings(self, session: dict, radius: int = 1) -> str:
        cells = session["map"]["cells"]
        h, w = len(cells), len(cells[0])
        px, py = session["player_pos"]["x"], session["player_pos"]["y"]
        names = {"X": "出口", "M": "怪物", "C": "宝箱", "T": "陷阱", "S": "祭坛", "$": "金币堆", "G": "毒雾", "P": "传送门", "H": "生命泉", "B": "BOSS", ".": "空地", "#": "墙壁"}
        found = []
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = px + dx, py + dy
                if 0 <= nx < w and 0 <= ny < h:
                    c = cells[ny][nx]
                    if c != "." and c != "#" and c != "E":
                        dir_name = {
                            (0, -1): "北", (0, 1): "南", (-1, 0): "西", (1, 0): "东",
                            (-1, -1): "西北", (1, -1): "东北", (-1, 1): "西南", (1, 1): "东南",
                        }.get((dx, dy), f"({dx},{dy})")
                        found.append(f"{dir_name}边有**{names.get(c, c)}**")
        if not found:
            return "四周看起来空荡荡的。"
        return "、".join(found) + "。"

    # =====================================================================
    # 宠物扫雷
    # =====================================================================
    @staticmethod
    def _ms_intro() -> str:
        lines = [
            "## 🧨 宠物扫雷",
            "> 翻开所有安全格即胜利，踩雷或超时即失败。",
            "> 奖励宠物经验（暂存），发送「扫雷兑换」发放到当前群宠物。",
            "",
            "**难度一览**：",
        ]
        for lv, cfg in data.MS_DIFFICULTIES.items():
            w, h = cfg["size"]
            lines.append(
                f"- 难度{lv} {cfg['name']}：{w}×{h} 格 · {cfg['mines']} 雷 · "
                f"限时 {cfg['time'] // 60} 分钟 · 积分 +{cfg['score']}"
            )
        lines += [
            "",
            "**指令**：",
            "- 开始扫雷 难度 （如：开始扫雷2，默认难度1）",
            "- 扫 坐标 （支持多扫，如：扫a1b2 或 扫 a1 b2）",
            "- 插旗 坐标 （标记/取消标记疑似雷，如：插旗a1）",
            "- 扫雷地图 · 放弃扫雷 · 扫雷排行 · 扫雷兑换",
        ]
        return "\n".join(lines)

    @staticmethod
    def _ms_parse_coords(tokens: list[str]) -> list[tuple[int, int]]:
        """把 a1b2 / a1 b2 等形式解析为 (x, y) 列表（0 基）。"""
        raw = "".join(tokens[1:])
        coords = []
        for m in _MS_COORD_RE.findall(raw.lower()):
            x = ord(m[0]) - ord("a")
            y = int(m[1:]) - 1
            coords.append((x, y))
        return coords

    @staticmethod
    def _ms_coord_name(x: int, y: int) -> str:
        return f"{chr(ord('a') + x)}{y + 1}"

    @staticmethod
    def _ms_col_name(x: int) -> str:
        return chr(ord("a") + x)

    @staticmethod
    def _ms_neighbors(x: int, y: int, w: int, h: int):
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    yield nx, ny

    def _ms_place_mines(self, session: dict, safe: tuple[int, int]) -> None:
        """首次翻格后布雷：首格及其八邻不放雷，保证开局不炸。"""
        w, h = session["w"], session["h"]
        excluded = {safe} | set(self._ms_neighbors(safe[0], safe[1], w, h))
        candidates = [
            (x, y) for y in range(h) for x in range(w) if (x, y) not in excluded
        ]
        count = min(session["mines_total"], len(candidates))
        session["mines"] = set(random.sample(candidates, count))
        session["mines_total"] = count
        numbers = {}
        for y in range(h):
            for x in range(w):
                if (x, y) in session["mines"]:
                    continue
                numbers[(x, y)] = sum(
                    1 for n in self._ms_neighbors(x, y, w, h) if n in session["mines"]
                )
        session["numbers"] = numbers

    def _ms_open_cell(self, session: dict, x: int, y: int) -> None:
        """翻开一格，0 则洪水式展开。"""
        opened = session["opened"]
        stack = [(x, y)]
        while stack:
            cx, cy = stack.pop()
            if (cx, cy) in opened or (cx, cy) in session["mines"]:
                continue
            opened.add((cx, cy))
            session["flags"].discard((cx, cy))
            if session["numbers"].get((cx, cy), 0) == 0:
                for n in self._ms_neighbors(cx, cy, session["w"], session["h"]):
                    if n not in opened:
                        stack.append(n)

    @staticmethod
    def _ms_load_fonts(size: int, small: int):
        for font_path in (
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/System/Library/Fonts/PingFang.ttc",
        ):
            try:
                return (
                    ImageFont.truetype(font_path, size),
                    ImageFont.truetype(font_path, small),
                )
            except Exception:
                continue
        return None, None

    def _ms_draw_board(self, session: dict, reveal: bool = False, boom: tuple[int, int] | None = None) -> str:
        """绘制扫雷棋盘，返回图片文件名。

        参考样式：顶部显示剩余雷数与难度；每个未翻开格子显示坐标；
        右侧与底部绘制行列标尺（数字 / 字母）。
        """
        cfg = data.MS_DIFFICULTIES[session["difficulty"]]
        w, h = session["w"], session["h"]
        cell = data.MS_CELL_SIZE
        pad = data.MS_PADDING
        label_m = 28  # 右侧/底部标尺宽度
        header_h = 56
        font, small_font = self._ms_load_fonts(20, 12)

        remain = max(0, session["deadline"] - int(time.time()))
        flags_left = session["mines_total"] - len(session["flags"])

        img_w = w * cell + pad * 2 + label_m
        img_h = h * cell + pad * 2 + header_h + label_m
        img = Image.new("RGB", (img_w, img_h), data.MS_COLORS["bg"])
        draw = ImageDraw.Draw(img)

        # ---- 顶部标题栏 ----
        # 左侧：炸弹图标 + 剩余雷数
        bomb_cx, bomb_cy = pad + 16, header_h // 2
        r = 10
        draw.ellipse(
            [bomb_cx - r, bomb_cy - r, bomb_cx + r, bomb_cy + r],
            fill=(40, 40, 40),
            outline=(20, 20, 20),
            width=2,
        )
        # 引信
        draw.arc(
            [bomb_cx - 4, bomb_cy - r - 7, bomb_cx + 6, bomb_cy - r + 2],
            start=200,
            end=340,
            fill=(180, 60, 60),
            width=2,
        )
        # 火花
        draw.polygon(
            [(bomb_cx + 5, bomb_cy - r - 5), (bomb_cx + 9, bomb_cy - r - 10), (bomb_cx + 7, bomb_cy - r - 4)],
            fill=(255, 140, 0),
        )

        count_x = bomb_cx + r + 10
        count_text = str(max(0, flags_left))
        if font:
            draw.text(
                (count_x, bomb_cy - 12),
                count_text,
                fill=data.MS_COLORS["text"],
                font=font,
            )

        # 右侧：难度
        diff_text = f"难度：{cfg['name']}"
        if font:
            tw = draw.textlength(diff_text, font=font)
            draw.text(
                (img_w - pad - label_m - tw, bomb_cy - 12),
                diff_text,
                fill=data.MS_COLORS["text"],
                font=font,
            )

        # ---- 棋盘原点 ----
        ox = pad
        oy = pad + header_h

        # ---- 绘制格子 ----
        for y in range(h):
            for x in range(w):
                cx = ox + x * cell
                cy = oy + y * cell
                pos = (x, y)
                is_open = pos in session["opened"]
                is_mine = session["mines"] is not None and pos in session["mines"]
                show_mine = reveal and is_mine

                if is_open or show_mine:
                    base = data.MS_COLORS["open"] if (x + y) % 2 else data.MS_COLORS["open_alt"]
                    if boom and pos == boom:
                        base = data.MS_COLORS["boom"]
                    draw.rectangle([cx, cy, cx + cell - 1, cy + cell - 1], fill=base)
                    draw.rectangle([cx, cy, cx + cell - 1, cy + cell - 1], outline=data.MS_COLORS["grid"], width=1)
                    if show_mine:
                        self._ms_draw_mine(draw, cx, cy, cell)
                    elif is_open:
                        num = session["numbers"].get(pos, 0)
                        if num > 0 and font:
                            color = data.MS_NUMBER_COLORS.get(num, data.MS_COLORS["text"])
                            tw = draw.textlength(str(num), font=font)
                            draw.text(
                                (cx + (cell - tw) / 2, cy + cell / 2 - 11),
                                str(num),
                                fill=color,
                                font=font,
                            )
                else:
                    base = data.MS_COLORS["closed"] if (x + y) % 2 else data.MS_COLORS["closed_alt"]
                    draw.rectangle([cx, cy, cx + cell - 1, cy + cell - 1], fill=base)
                    draw.rectangle([cx, cy, cx + cell - 1, cy + cell - 1], outline=(80, 105, 80), width=1)
                    if pos in session["flags"]:
                        self._ms_draw_flag(draw, cx, cy, cell)
                    elif small_font:
                        label = self._ms_coord_name(x, y)
                        tw = draw.textlength(label, font=small_font)
                        draw.text(
                            (cx + (cell - tw) / 2, cy + cell / 2 - 7),
                            label,
                            fill=data.MS_COLORS["coord"],
                            font=small_font,
                        )

        # ---- 右侧行号标尺 ----
        ruler_font = small_font or font
        if ruler_font:
            for y in range(h):
                cy = oy + y * cell + cell // 2 - 7
                label = str(y + 1)
                tw = draw.textlength(label, font=ruler_font)
                rx = ox + w * cell + (label_m - int(tw)) // 2
                draw.text((rx, cy), label, fill=data.MS_COLORS["text"], font=ruler_font)

        # ---- 底部列字母标尺 ----
        if ruler_font:
            for x in range(w):
                cx = ox + x * cell + cell // 2
                label = self._ms_col_name(x)
                tw = draw.textlength(label, font=ruler_font)
                bx = cx - int(tw) // 2
                by = oy + h * cell + (label_m - 14) // 2
                draw.text((bx, by), label, fill=data.MS_COLORS["text"], font=ruler_font)

        filename = f"ms_{uuid.uuid4().hex}.png"
        img.save(self.store.custom_images_dir / filename, "PNG")
        return filename

    def _ms_draw_mine(self, draw: ImageDraw.Draw, cx: int, cy: int, cell: int) -> None:
        """在格子内绘制地雷。"""
        r = cell // 4
        mx, my = cx + cell // 2, cy + cell // 2
        draw.ellipse([mx - r, my - r, mx + r, my + r], fill=data.MS_COLORS["mine"])
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0), (-1, -1), (1, 1), (-1, 1), (1, -1)):
            draw.line(
                [(mx, my), (mx + dx * (r + 5), my + dy * (r + 5))],
                fill=data.MS_COLORS["mine"],
                width=2,
            )

    def _ms_draw_flag(self, draw: ImageDraw.Draw, cx: int, cy: int, cell: int) -> None:
        """在格子内绘制旗帜。"""
        fx, fy = cx + cell // 2, cy + cell // 2
        draw.line([(fx - 2, fy - 10), (fx - 2, fy + 11)], fill=(90, 60, 30), width=3)
        draw.polygon(
            [(fx - 2, fy - 10), (fx + 12, fy - 5), (fx - 2, fy)],
            fill=data.MS_COLORS["flag"],
        )

    def _ms_col_name(self, x: int) -> str:
        return chr(ord("a") + x)

    def _ms_board_md(self, session: dict, reveal: bool = False, boom: tuple[int, int] | None = None) -> str:
        url = self._tomb_image_url(self._ms_draw_board(session, reveal=reveal, boom=boom))
        return f"![扫雷棋盘 #{images._IMG_DISPLAY} #{images._IMG_DISPLAY}]({url})"

    def _ms_status_line(self, session: dict) -> str:
        cfg = data.MS_DIFFICULTIES[session["difficulty"]]
        remain = max(0, session["deadline"] - int(time.time()))
        total_safe = session["w"] * session["h"] - session["mines_total"]
        return (
            f"> 难度{session['difficulty']} {cfg['name']}　"
            f"进度 {len(session['opened'])}/{total_safe}　"
            f"插旗 {len(session['flags'])}/{session['mines_total']}　"
            f"剩余时间 {remain // 60}:{remain % 60:02d}"
        )

    def _ms_start(self, player: dict, group_id: str, tokens: list[str]):
        qq = str(player.get("qq", ""))
        session = self._ms_sessions.get(qq)
        if session:
            expired = self._ms_check_timeout(player, session)
            if expired:
                return expired
            return (
                f"你已有进行中的扫雷（发送「放弃扫雷」可结束）。\n{self._ms_status_line(session)}",
                self._ms_board_md(session),
            )
        diff = 1
        if len(tokens) > 1:
            try:
                diff = int(tokens[1])
            except ValueError:
                return "用法：开始扫雷 难度（1-4），如：开始扫雷2"
        if diff not in data.MS_DIFFICULTIES:
            return "难度只有 1-4：1简单 / 2普通 / 3困难 / 4地狱。"
        cfg = data.MS_DIFFICULTIES[diff]
        w, h = cfg["size"]
        now = int(time.time())
        session = {
            "qq": qq,
            "group_id": group_id,
            "difficulty": diff,
            "w": w,
            "h": h,
            "mines_total": cfg["mines"],
            "mines": None,
            "numbers": {},
            "opened": set(),
            "flags": set(),
            "started_at": now,
            "deadline": now + cfg["time"],
        }
        self._ms_sessions[qq] = session
        return (
            f"🧨 扫雷开始！难度{diff} {cfg['name']}：{w}×{h} 格，{cfg['mines']} 颗雷，"
            f"限时 {cfg['time'] // 60} 分钟。\n"
            f"发送「扫 坐标」翻格（支持多扫，如：扫a1b2），「插旗 坐标」标雷。\n"
            f"{self._ms_status_line(session)}",
            self._ms_board_md(session),
        )

    def _ms_check_timeout(self, player: dict, session: dict) -> str | None:
        """超时则结算失败并返回结算文本，否则返回 None。"""
        if int(time.time()) >= session["deadline"]:
            return self._ms_settle(player, session, "timeout")
        return None

    def _ms_need_session(self, player: dict) -> dict | str:
        session = self._ms_sessions.get(str(player.get("qq", "")))
        if not session:
            return "你没有进行中的扫雷，发送「开始扫雷 难度」开始游戏。"
        return session

    def _ms_sweep(self, player: dict, tokens: list[str]):
        session = self._ms_need_session(player)
        if isinstance(session, str):
            return session
        expired = self._ms_check_timeout(player, session)
        if expired:
            return expired
        coords = self._ms_parse_coords(tokens)
        if not coords:
            return "用法：扫 坐标（支持多扫，如：扫a1b2 或 扫 a1 b2）。"
        w, h = session["w"], session["h"]
        results = []
        for x, y in coords:
            name = self._ms_coord_name(x, y)
            if not (0 <= x < w and 0 <= y < h):
                results.append(f"{name} 超出棋盘")
                continue
            if (x, y) in session["flags"]:
                results.append(f"{name} 已插旗，请先取消")
                continue
            if (x, y) in session["opened"]:
                results.append(f"{name} 已翻开")
                continue
            if session["mines"] is None:
                self._ms_place_mines(session, (x, y))
            if (x, y) in session["mines"]:
                session["opened"].add((x, y))
                return self._ms_settle(player, session, "boom", boom=(x, y))
            self._ms_open_cell(session, x, y)
            results.append(f"{name} 安全")
        total_safe = w * h - session["mines_total"]
        if session["mines"] is not None and len(session["opened"]) >= total_safe:
            return self._ms_settle(player, session, "win")
        note = "、".join(results) if results else "无有效操作"
        return (
            f"🔍 {note}\n{self._ms_status_line(session)}",
            self._ms_board_md(session),
        )

    def _ms_flag(self, player: dict, tokens: list[str]):
        session = self._ms_need_session(player)
        if isinstance(session, str):
            return session
        expired = self._ms_check_timeout(player, session)
        if expired:
            return expired
        coords = self._ms_parse_coords(tokens)
        if not coords:
            return "用法：插旗 坐标（如：插旗a1，再次插旗可取消）。"
        w, h = session["w"], session["h"]
        results = []
        for x, y in coords:
            name = self._ms_coord_name(x, y)
            if not (0 <= x < w and 0 <= y < h):
                results.append(f"{name} 超出棋盘")
                continue
            if (x, y) in session["opened"]:
                results.append(f"{name} 已翻开")
                continue
            if (x, y) in session["flags"]:
                session["flags"].discard((x, y))
                results.append(f"{name} 已取消旗")
            else:
                session["flags"].add((x, y))
                results.append(f"{name} 已插旗")
        return (
            f"🚩 {'、'.join(results)}\n{self._ms_status_line(session)}",
            self._ms_board_md(session),
        )

    def _ms_status(self, player: dict):
        session = self._ms_need_session(player)
        if isinstance(session, str):
            return session
        expired = self._ms_check_timeout(player, session)
        if expired:
            return expired
        return (
            f"🗺 当前扫雷棋盘\n{self._ms_status_line(session)}",
            self._ms_board_md(session),
        )

    def _ms_forfeit(self, player: dict):
        session = self._ms_need_session(player)
        if isinstance(session, str):
            return session
        return self._ms_settle(player, session, "forfeit")

    def _ms_settle(self, player: dict, session: dict, reason: str, boom: tuple[int, int] | None = None):
        """扫雷结算：win / boom / timeout / forfeit。"""
        qq = str(player.get("qq", ""))
        cfg = data.MS_DIFFICULTIES[session["difficulty"]]
        st = self.store.ms_state(qq)
        st["plays"] = st.get("plays", 0) + 1
        total_safe = session["w"] * session["h"] - session["mines_total"]
        opened_ratio = len(session["opened"]) / max(1, total_safe)
        self._ms_sessions.pop(qq, None)

        if reason == "win":
            elapsed = int(time.time()) - session["started_at"]
            st["wins"] = st.get("wins", 0) + 1
            st["score"] = st.get("score", 0) + cfg["score"]
            best = st.setdefault("best_time", {})
            key = str(session["difficulty"])
            if key not in best or elapsed < best[key]:
                best[key] = elapsed
            exp = random.randint(*cfg["exp"])
            self.store.add_ms_pending_pet_exp(qq, exp)
            if session["mines"] is None:
                session["mines"] = set()
            return (
                f"🎉 扫雷成功！难度{session['difficulty']} {cfg['name']}，"
                f"用时 {elapsed // 60}:{elapsed % 60:02d}。\n"
                f"● 扫雷积分 +{cfg['score']}（累计 {st['score']}）\n"
                f"● 宠物经验 +{exp}（已暂存，发送「扫雷兑换」可发放到当前群宠物）",
                self._ms_board_md(session, reveal=True),
            )

        # 失败：按进度给安慰经验
        exp = int(cfg["exp"][0] * opened_ratio * data.MS_FAIL_EXP_RATIO)
        exp_text = ""
        if exp > 0:
            self.store.add_ms_pending_pet_exp(qq, exp)
            exp_text = f"\n● 宠物经验 +{exp}（已暂存，发送「扫雷兑换」可发放到当前群宠物）"
        if session["mines"] is None:
            session["mines"] = set()
        if reason == "boom":
            return (
                f"💥 踩到地雷（{self._ms_coord_name(*boom)}），扫雷失败！{exp_text}",
                self._ms_board_md(session, reveal=True, boom=boom),
            )
        if reason == "timeout":
            return (
                f"⏰ 倒计时结束，扫雷失败！{exp_text}",
                self._ms_board_md(session, reveal=True),
            )
        return f"🏳 已放弃本局扫雷。{exp_text.lstrip()}" if exp_text else "🏳 已放弃本局扫雷。"

    def _ms_rank(self, player: dict) -> str:
        """扫雷全服排行（按累计积分），使用 Markdown 表格展示。"""
        entries = []
        for qq, st in self.store.all_ms_players().items():
            score = st.get("score", 0)
            if score > 0:
                entries.append((qq, score, st.get("wins", 0), st.get("plays", 0)))
        entries.sort(key=lambda x: x[1], reverse=True)
        if not entries:
            return "暂无玩家登上扫雷排行。"
        lines = ["## 🧨 扫雷排行（全服）"]
        my_qq = str(player.get("qq", ""))
        my_rank = next((i for i, e in enumerate(entries, start=1) if e[0] == my_qq), None)
        my_st = self.store.ms_state(my_qq)
        if my_rank:
            lines.append(f"> 我的排名：**{my_rank}**　·　我的积分：**{my_st.get('score', 0)}**")
        else:
            lines.append("> 你还没有扫雷积分，赢一局即可上榜。")
        lines.append("")
        lines.append("| 排名 | 用户 | 积分 | 胜场 | 总局数 |")
        lines.append("|:--:|:--:|--:|--:|--:|")
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        for i, (qq, score, wins, plays) in enumerate(entries[:10], start=1):
            rk = medals.get(i, str(i))
            display = self._tomb_display_qq(qq).replace("|", "丨")
            lines.append(
                f"| {rk} | {display} | {score} | {wins} | {plays} |"
            )
        if my_rank and my_rank > 10:
            display = self._tomb_display_qq(my_qq).replace("|", "丨")
            lines.append(
                f"| {my_rank} | {display} | {my_st.get('score', 0)} | "
                f"{my_st.get('wins', 0)} | {my_st.get('plays', 0)} |"
            )
        return "\n".join(lines)

    def _ms_redeem_exp(self, player: dict, tokens: list[str]) -> str:
        """把暂存的扫雷宠物经验兑换到当前群宠物。

        用法：
        - 扫雷兑换                 一键兑换全部
        - 扫雷兑换 全部            一键兑换全部
        - 扫雷兑换 10000           只兑换 10000 点
        """
        qq = str(player.get("qq", ""))
        pending = self.store.get_ms_pending_pet_exp(qq)
        if pending <= 0:
            return "你没有待兑换的扫雷宠物经验。"
        p = player.get("pet")
        if not p:
            return "你没有宠物，无法兑换经验。"
        amount_str = tokens[1] if len(tokens) > 1 else ""
        if amount_str and amount_str not in ("全部", "all"):
            try:
                amount = int(amount_str)
            except ValueError:
                return "用法：扫雷兑换 [数量/全部]，数量请填写整数。"
            if amount <= 0:
                return "兑换数量必须大于 0。"
            if amount > pending:
                return f"待兑换经验只有 {pending} 点，不足 {amount} 点。"
        else:
            amount = pending
        actual = self.store.consume_ms_pending_pet_exp(qq, amount)
        petmod.add_exp(p, actual)
        remain = self.store.get_ms_pending_pet_exp(qq)
        note = f"，还剩余 {remain} 点" if remain > 0 else "，已全部兑换"
        return f"🎁 扫雷经验兑换成功！当前群宠物 +{actual} 经验{note}。{self._auto_level_note(player, p)}"

    # =====================================================================
    # 宗门战 / 跨群联赛
    # =====================================================================
    _BJ_TZ = ZoneInfo("Asia/Shanghai")

    def _bj_localtime(self, secs: int | None = None) -> time.struct_time:
        """返回北京时间的 struct_time。"""
        if secs is None:
            return datetime.now(self._BJ_TZ).timetuple()
        return datetime.fromtimestamp(secs, self._BJ_TZ).timetuple()

    def _bj_today(self) -> str:
        """返回北京时间今天日期 YYYY-MM-DD。"""
        return datetime.now(self._BJ_TZ).strftime("%Y-%m-%d")

    def _bj_timestamp(self, year: int, month: int, day: int,
                      hour: int, minute: int, second: int = 0) -> int:
        """把北京时间日期分量转成 epoch 秒。"""
        dt = datetime(year, month, day, hour, minute, second, tzinfo=self._BJ_TZ)
        return int(dt.timestamp())

    def _sect_ensure_season(self) -> dict:
        """确保宗门战赛季状态字典存在（不再做每周清零重置）。"""
        return self.store._data.setdefault(
            "sect_season", self.store._default_sect_season()
        )

    def _sect_ensure_today(self, sect: dict) -> None:
        """确保 sect['today'] 是今天的数据。"""
        today = self._bj_today()
        if sect.get("today", {}).get("date") != today:
            sect["today"] = {
                "date": today,
                "enroll": [],
                "forced": [],
                "confirmed": [],
                "signed": [],
                "war": None,
            }
        else:
            sect["today"].setdefault("war", None)

    def _sect_inc_active(self, player: dict, score: int) -> None:
        """增加玩家活跃度。"""
        psect = player.setdefault("sect", self.store._default_player_sect())
        psect["active_score"] = psect.get("active_score", 0) + score
        psect["last_active_at"] = int(time.time())

    def _sect_is_master_or_deputy(self, player: dict, group_id: str) -> bool:
        """判断玩家是否为当前群宗主或副宗主。"""
        group = self.store.get_group(group_id)
        sect = group.get("sect", {})
        qq = str(player.get("qq", ""))
        if qq == sect.get("master_qq", ""):
            return True
        if qq in sect.get("deputy_qqs", []):
            return True
        return False

    def _sect_elect_master(self, group_id: str) -> dict | None:
        """选举宗主：战力最高者优先；同战力时活跃度更高者优先。"""
        now = int(time.time())
        week_ago = now - 7 * 86400
        candidates = []
        for pl in self.store.players_in_group(group_id).values():
            pet = pl.get("pet")
            if not pet or petmod.is_dead(pet):
                continue
            psect = pl.setdefault("sect", self.store._default_player_sect())
            # 初始无活跃数据时也允许参选
            candidates.append({
                "qq": pl["qq"],
                "player": pl,
                "bp": petmod.battle_power(pet),
                "active": psect.get("active_score", 0),
            })
        if not candidates:
            return None
        # 先按战力，再按活跃度
        candidates.sort(key=lambda x: (x["bp"], x["active"]), reverse=True)
        return candidates[0]["player"]

    def _sect_generate_name(self) -> str:
        """生成全服唯一的宗门名：宗门 + 6 位随机字符。"""
        chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        existing = set()
        for g in self.store._data.get("groups", {}).values():
            sect = g.get("sect", {})
            if sect.get("name"):
                existing.add(sect["name"])
        while True:
            suffix = "".join(random.choices(chars, k=6))
            name = f"宗门{suffix}"
            if name not in existing:
                return name

    def _sect_ensure_name(self, group_id: str) -> str:
        """确保本群有宗门名；没有则自动生成唯一名称。"""
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        if sect.get("name"):
            return sect["name"]
        name = self._sect_generate_name()
        sect["name"] = name
        return name

    def _sect_ensure_master(self, group_id: str) -> str | None:
        """确保本群已有宗主和宗门名；没有则立即选举并返回宗主 QQ。"""
        self._sect_ensure_name(group_id)
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        if sect.get("master_qq"):
            return sect["master_qq"]
        master = self._sect_elect_master(group_id)
        if master:
            sect["master_qq"] = master["qq"]
            return master["qq"]
        return None

    def _sect_manual_elect(self, player: dict, group_id: str) -> str:
        """手动触发宗主选举。"""
        if not self._sect_is_master_or_deputy(player, group_id):
            return "仅宗主或副宗主可手动触发宗主选举。"
        master = self._sect_elect_master(group_id)
        if not master:
            return "本群暂无符合条件的宗主候选人（需有存活宠物）。"
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        old = sect.get("master_qq", "")
        sect["master_qq"] = master["qq"]
        return (
            f"🏯 宗主选举完成\n"
            f"● 原宗主：`{old or '无'}`\n"
            f"● 新宗主：`{master['qq']}`（战力优先，活跃次之）"
        )

    def _sect_today_forced(self, group_id: str) -> list[dict]:
        """计算本群今日强制出战前三。"""
        pets = []
        for pl in self.store.players_in_group(group_id).values():
            pet = pl.get("pet")
            if not pet or petmod.is_dead(pet):
                continue
            pets.append({
                "qq": pl["qq"],
                "pet_name": pet["nickname"],
                "nickname": pet["nickname"],
                "bp": petmod.battle_power(pet),
                "element": pet.get("element", ""),
            })
        pets.sort(key=lambda x: x["bp"], reverse=True)
        return pets[:data.SECT_FORCED_COUNT]

    def _sect_refresh_forced_for_group(self, group_id: str) -> None:
        """重新计算本群强制出战前三，并同步调整报名列表。"""
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        self._sect_ensure_today(sect)
        old_forced = {e["qq"] for e in sect["today"]["forced"]}
        new_forced = self._sect_today_forced(group_id)
        new_forced_q = {e["qq"] for e in new_forced}

        # 从报名列表中移除新进入强制前三的玩家
        enroll = [e for e in sect["today"]["enroll"] if e["qq"] not in new_forced_q]

        # 对跌出强制的玩家，若报名未满则加回报名列表末尾
        dropped = old_forced - new_forced_q
        for qq in dropped:
            if len(enroll) >= data.SECT_ENROLL_COUNT:
                break
            pl = self.store.get_player(qq, group_id, create=False)
            if not pl:
                continue
            pet = pl.get("pet")
            if not pet or petmod.is_dead(pet):
                continue
            enroll.append({
                "qq": qq,
                "nickname": qq,
                "pet_name": pet["nickname"],
                "bp": petmod.battle_power(pet),
                "element": pet.get("element", ""),
                "enrolled_at": int(time.time()),
            })

        sect["today"]["forced"] = new_forced
        sect["today"]["enroll"] = enroll

    def _sect_refresh_forced_all(self) -> None:
        """刷新所有群的强制出战名单。"""
        for gid in list(self.store._data.get("groups", {}).keys()):
            try:
                self._sect_refresh_forced_for_group(gid)
            except Exception:
                logger.exception(f"[petpark] 刷新宗门强制出战失败：{gid}")

    def _sect_reset_daily(self, group_id: str) -> None:
        """每天重置宗门今日数据并重新选举宗主、计算强制出战。"""
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        self._sect_ensure_today(sect)
        # 选举宗主
        master = self._sect_elect_master(group_id)
        if master:
            sect["master_qq"] = master["qq"]
        # 计算强制出战
        sect["today"]["forced"] = self._sect_today_forced(group_id)
        # 宗门名默认用群号
        if not sect.get("name"):
            sect["name"] = str(group_id)

    def _sect_auto_confirm(self, group_id: str) -> None:
        """如果宗主未确认，自动从报名中取战力前 7 组成确认名单；并始终刷新为最新战力。"""
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        self._sect_ensure_today(sect)
        if not sect["today"]["confirmed"]:
            forced = sect["today"]["forced"]
            enroll = sorted(sect["today"]["enroll"], key=lambda x: x["bp"], reverse=True)
            selected = enroll[:data.SECT_ENROLL_COUNT]
            confirmed = []
            for f in forced:
                confirmed.append({**f, "kind": "强制"})
            for e in selected:
                confirmed.append({**e, "kind": "报名"})
            sect["today"]["confirmed"] = confirmed
        # 自动补位：确认人数不足最少参战人数时，从本群存活宠物中按战力补足
        if len(sect["today"]["confirmed"]) < data.SECT_MIN_BATTLE_MEMBERS:
            confirmed_qs = {c["qq"] for c in sect["today"]["confirmed"]}
            fillers = []
            for pl in self.store.players_in_group(group_id).values():
                qq = pl.get("qq")
                if not qq or qq in confirmed_qs:
                    continue
                pet = pl.get("pet")
                if not pet or petmod.is_dead(pet):
                    continue
                fillers.append({
                    "qq": qq,
                    "nickname": qq,
                    "pet_name": pet["nickname"],
                    "bp": petmod.battle_power(pet),
                    "element": pet.get("element", ""),
                    "kind": "补位",
                })
            fillers.sort(key=lambda x: x["bp"], reverse=True)
            need = data.SECT_MIN_BATTLE_MEMBERS - len(sect["today"]["confirmed"])
            sect["today"]["confirmed"].extend(fillers[:need])
        # 始终用当前宠物数据刷新出战名单战力，避免报名后战力变化导致偏差
        for c in sect["today"]["confirmed"]:
            pl = self.store.get_player(c["qq"], group_id, create=False)
            if pl and pl.get("pet") and not petmod.is_dead(pl["pet"]):
                c["bp"] = petmod.battle_power(pl["pet"])
                c["element"] = pl["pet"].get("element", "")
                c["pet_name"] = pl["pet"].get("nickname", c.get("pet_name", "-"))

    def _sect_enroll(self, player: dict, group_id: str) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        now = self._bj_localtime()
        if now.tm_hour > data.SECT_ENROLL_DEADLINE_HOUR or (
            now.tm_hour == data.SECT_ENROLL_DEADLINE_HOUR
            and now.tm_min >= data.SECT_ENROLL_DEADLINE_MIN
        ):
            return "今日报名已截止（20:30 截止），请明天再来。"

        self._sect_ensure_season()
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        self._sect_ensure_today(sect)

        forced_qqs = {x["qq"] for x in sect["today"]["forced"]}
        if player["qq"] in forced_qqs:
            return "你已在今日强制出战名单中，无需报名。"

        enroll = [e for e in sect["today"]["enroll"] if e["qq"] != player["qq"]]
        if len(enroll) >= data.SECT_ENROLL_COUNT:
            return f"今日报名名额已满（{data.SECT_ENROLL_COUNT}/{data.SECT_ENROLL_COUNT}），请明天再来。"

        enroll.append({
            "qq": player["qq"],
            "nickname": player["qq"],
            "pet_name": p["nickname"],
            "bp": petmod.battle_power(p),
            "element": p.get("element", ""),
            "enrolled_at": int(time.time()),
        })
        sect["today"]["enroll"] = enroll
        self._sect_inc_active(player, 1)
        return (
            f"## 🏯 宗门报名成功\n"
            f"● 你的『{p['nickname']}』已加入报名队列（{len(enroll)}/{data.SECT_ENROLL_COUNT}）\n"
            f"● 宗主将在 20:30 前确认最终出战名单。"
        )

    def _sect_confirm_list(self, player: dict, group_id: str) -> str:
        if not self._sect_is_master_or_deputy(player, group_id):
            return "仅宗主或副宗主可确认出战名单。"
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        self._sect_ensure_today(sect)
        now = self._bj_localtime()
        if now.tm_hour > data.SECT_ENROLL_DEADLINE_HOUR or (
            now.tm_hour == data.SECT_ENROLL_DEADLINE_HOUR
            and now.tm_min >= data.SECT_ENROLL_DEADLINE_MIN
        ):
            return "名单已锁定（20:30 后不可修改）。"

        self._sect_auto_confirm(group_id)
        return self._sect_format_confirmed(sect["today"]["confirmed"])

    def _sect_kick_enroll(self, player: dict, group_id: str, tokens: list[str]) -> str:
        if not self._sect_is_master_or_deputy(player, group_id):
            return "仅宗主或副宗主可管理报名列表。"
        target = self._arg(tokens, 1)
        if not target:
            return "用法：宗门踢出 用户ID"
        target = self._resolve_user_token(target)
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        self._sect_ensure_today(sect)
        enroll = sect["today"]["enroll"]
        new_enroll = [e for e in enroll if e["qq"] != target]
        if len(new_enroll) == len(enroll):
            return f"用户 `{target}` 不在今日报名列表中。"
        sect["today"]["enroll"] = new_enroll
        # 如果已确认，也移除
        sect["today"]["confirmed"] = [
            c for c in sect["today"]["confirmed"] if c["qq"] != target
        ]
        return f"已将 `{target}` 从今日报名列表中踢出。"

    def _sect_set_notice(self, player: dict, group_id: str, tokens: list[str]) -> str:
        if not self._sect_is_master_or_deputy(player, group_id):
            return "仅宗主或副宗主可设置公告。"
        notice = " ".join(tokens[1:])
        if not notice:
            return "用法：宗门公告 内容"
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        sect["notice"] = notice[:200]
        return f"🏯 宗门公告已更新：\n> {sect['notice']}"

    def _sect_rename(self, player: dict, group_id: str, tokens: list[str]) -> str:
        if not self._sect_is_master_or_deputy(player, group_id):
            return "仅宗主或副宗主可修改宗门名。"
        name = " ".join(tokens[1:]).strip()
        if not name:
            return "用法：宗门改名 新宗门名"
        if len(name) > 20:
            return "宗门名不能超过 20 个字。"
        # 检查全服唯一
        for gid, g in self.store._data.get("groups", {}).items():
            if gid == group_id:
                continue
            sect = g.get("sect", {})
            if sect.get("name") == name:
                return f"宗门名『{name}』已被其他宗门使用，请换一个。"
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        old = sect.get("name", group_id)
        sect["name"] = name
        return f"🏯 宗门名已由『{old}』改为『{name}』"

    def _sect_appoint_deputy(self, player: dict, group_id: str, tokens: list[str]) -> str:
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        if player["qq"] != sect.get("master_qq", ""):
            return "仅宗主可任命副宗主。"
        target = self._arg(tokens, 1)
        if not target:
            return "用法：宗门任命副宗主 用户ID"
        target = self._resolve_user_token(target)
        if target == player["qq"]:
            return "不能任命自己。"
        tp = self.store.get_player(target, group_id, create=False)
        if not tp:
            return f"用户 `{target}` 不在本群。"
        deputies = sect.setdefault("deputy_qqs", [])
        if target in deputies:
            return "该用户已是副宗主。"
        if len(deputies) >= data.SECT_MAX_DEPUTIES:
            return f"副宗主最多 {data.SECT_MAX_DEPUTIES} 人。"
        deputies.append(target)
        return f"已任命 `{target}` 为副宗主。"

    def _sect_revoke_deputy(self, player: dict, group_id: str, tokens: list[str]) -> str:
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        if player["qq"] != sect.get("master_qq", ""):
            return "仅宗主可撤销副宗主。"
        target = self._arg(tokens, 1)
        if not target:
            return "用法：宗门撤销副宗主 用户ID"
        target = self._resolve_user_token(target)
        deputies = sect.setdefault("deputy_qqs", [])
        if target not in deputies:
            return "该用户不是副宗主。"
        deputies.remove(target)
        return f"已撤销 `{target}` 的副宗主职位。"

    def _sect_re_elect(self, player: dict, group_id: str) -> str:
        if not self._sect_is_master_or_deputy(player, group_id):
            return "仅宗主或副宗主可发起重选。"
        master = self._sect_elect_master(group_id)
        if not master:
            return "本群暂无符合条件的宗主候选人（需有存活宠物且近 7 天活跃）。"
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        old = sect.get("master_qq", "")
        sect["master_qq"] = master["qq"]
        return (
            f"🏯 宗主重选完成\n"
            f"● 原宗主：`{old or '无'}`\n"
            f"● 新宗主：`{master['qq']}`（战力优先，活跃次之）"
        )

    def _sect_sign(self, player: dict, group_id: str) -> str:
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        self._sect_ensure_today(sect)
        if player["qq"] in sect["today"]["signed"]:
            return "你今天已经宗门签到过了。"
        sect["today"]["signed"].append(player["qq"])
        self._sect_give_points(group_id, data.SECT_SIGN_POINTS)
        self._sect_inc_active(player, 1)
        # 个人宗门贡献
        psect = player.setdefault("sect", self.store._default_player_sect())
        psect["contribution"] = psect.get("contribution", 0) + data.SECT_CONTRIBUTION_SIGN
        psect["total_contribution"] = psect.get("total_contribution", 0) + data.SECT_CONTRIBUTION_SIGN
        psect["season_contribution"] = psect.get("season_contribution", 0) + data.SECT_CONTRIBUTION_SIGN
        return (
            f"🏯 宗门签到成功！\n"
            f"● 宗门积分 +{data.SECT_SIGN_POINTS}\n"
            f"● 个人宗门贡献 +{data.SECT_CONTRIBUTION_SIGN}"
        )

    def _sect_give_points(self, group_id: str, points: int) -> None:
        """增加宗门积分：points 为可用积分，total_points 为累计积分并用于升级。"""
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        sect["points"] = sect.get("points", 0) + points
        sect["total_points"] = sect.get("total_points", 0) + points
        sect["season_points"] = sect.get("season_points", 0) + points
        sect["exp"] = sect.get("exp", 0) + points
        self._sect_check_level_up(sect)

    def _sect_check_level_up(self, sect: dict) -> int:
        """根据累计 total_points 检查并提升宗门等级，返回实际提升的级数。"""
        gained = 0
        while True:
            lvl = sect.get("level", 1)
            need = data.SECT_LEVEL_EXP.get(lvl + 1)
            if need is None or sect.get("total_points", 0) < need:
                break
            sect["level"] = lvl + 1
            gained += 1
        return gained

    def _sect_level_up(self, player: dict, group_id: str) -> str:
        """手动触发宗门升级检查（基于累计宗门积分）。"""
        group = self.store.get_group(group_id)
        sect = group.get("sect")
        if not sect:
            return "本群尚未建立宗门。"
        if not self._sect_is_master_or_deputy(player, group_id):
            return "只有宗主或副宗主可以使用宗门升级指令。"
        old_level = sect.get("level", 1)
        gained = self._sect_check_level_up(sect)
        new_level = sect.get("level", 1)
        if gained > 0:
            return (
                f"🏯 **宗门升级成功！**\n"
                f"由 Lv{old_level} 提升至 **Lv{new_level}**。\n"
                f"> 累计宗门积分：{sect.get('total_points', 0)}"
            )
        lvl = new_level
        need = data.SECT_LEVEL_EXP.get(lvl + 1)
        if need is None:
            return f"🏯 宗门已达最高等级 Lv{lvl}，无法继续升级。"
        progress = sect.get("total_points", 0)
        remain = need - progress
        return (
            f"🏯 宗门升级条件不足（当前 Lv{lvl}）。\n"
            f"> 累计积分 {progress}/{need}，还需 {remain} 点宗门积分可升至 Lv{lvl + 1}。"
        )

    def _sect_format_confirmed(self, confirmed: list[dict]) -> str:
        lines = [f"## 🏯 已确认出战名单（{len(confirmed)}/{data.SECT_TEAM_SIZE}）"]
        lines.append("| 序号 | 用户ID | 宠物 | 战力 | 类型 |")
        lines.append("|---|---|---|---|---|")
        for i, e in enumerate(confirmed, 1):
            lines.append(
                f"| {i} | `{e['qq']}` | {e.get('pet_name', e.get('nickname', '-'))} | "
                f"{self._fmt_power(e['bp'])} | {e.get('kind', '-')} |"
            )
        return "\n".join(lines)

    def _sect_get_rank(self, group_id: str) -> int:
        """返回本群在当前宗门排行榜中的名次。"""
        groups = self.store._data.get("groups", {})
        entries = []
        for gid, g in groups.items():
            if not g.get("cross"):
                continue
            sect = g.get("sect", {})
            if not sect.get("enabled", True):
                continue
            entries.append((gid, (sect.get("level", 1), sect.get("total_points", 0))))
        entries.sort(key=lambda x: x[1], reverse=True)
        for i, (gid, _) in enumerate(entries, 1):
            if gid == group_id:
                return i
        return 0

    def _sect_info(self, player: dict, group_id: str) -> str:
        self._sect_ensure_master(group_id)
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        self._sect_ensure_today(sect)
        rank = self._sect_get_rank(group_id)
        psect = player.setdefault("sect", self.store._default_player_sect())
        signed = player["qq"] in sect["today"]["signed"]
        forced_qqs = {e["qq"] for e in sect["today"]["forced"]}
        enroll_qqs = {e["qq"] for e in sect["today"]["enroll"]}
        confirmed = sect["today"]["confirmed"]
        if player["qq"] in forced_qqs:
            my_status = "强制出战"
        elif player["qq"] in enroll_qqs:
            my_status = "已报名"
        elif any(e["qq"] == player["qq"] for e in confirmed):
            my_status = "已确认出战"
        else:
            my_status = "未报名"

        lines = [
            "## 🏯 宗门信息",
            "| 项目 | 数值 |",
            "|---|---|",
            f"| 当前宗门 | {sect.get('name', group_id)} |",
            f"| 全服排名 | 第 {rank} 名 |",
            f"| 宗门等级 | Lv{sect.get('level', 1)} |",
            f"| 累计宗门积分 | {sect.get('total_points', 0)} |",
            f"| 当前可用积分 | {sect.get('points', 0)} |",
            f"| 本赛季积分 | {sect.get('season_points', 0)} |",
            f"| 胜/平/败 | {sect.get('win',0)} / {sect.get('draw',0)} / {sect.get('lose',0)} |",
            f"| 宗主 | `{sect.get('master_qq','未选举')}` |",
            f"| 副宗主 | {', '.join(f'`{q}`' for q in sect.get('deputy_qqs',[])) or '无'} |",
            f"| 公告 | {sect.get('notice','暂无公告')} |",
            f"| 今日签到 | {'已签到 ✅' if signed else '未签到 ❌'} |",
            f"| 今日出战状态 | {my_status} |",
            f"| 我的宗门贡献 | {psect.get('contribution', 0)} |",
            f"| 累计宗门贡献 | {psect.get('total_contribution', 0)} |",
        ]
        return "\n".join(lines)

    def _sect_list(self, group_id: str) -> str:
        self._sect_ensure_master(group_id)
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        self._sect_ensure_today(sect)
        forced = sect["today"]["forced"]
        enroll = sect["today"]["enroll"]
        confirmed = sect["today"]["confirmed"]

        lines = [
            "## 🏯 今日宗门出战名单",
            f"**宗主**：`{sect.get('master_qq','未选举')}`",
            f"**副宗主**：{', '.join(f'`{q}`' for q in sect.get('deputy_qqs',[])) or '无'}",
            f"**宗门公告**：{sect.get('notice','暂无公告')}",
            "",
        ]
        lines.append(f"### 强制出战（{len(forced)}/{data.SECT_FORCED_COUNT}）")
        lines.append("| 序号 | 用户ID | 宠物 | 战力 | 状态 |")
        lines.append("|---|---|---|---|---|")
        for i, e in enumerate(forced, 1):
            lines.append(
                f"| {i} | `{e['qq']}` | {e.get('pet_name', e.get('nickname','-'))} | "
                f"{self._fmt_power(e['bp'])} | 强制 |"
            )

        lines.append(f"\n### 报名出战（{len(enroll)}/{data.SECT_ENROLL_COUNT}）")
        lines.append("| 序号 | 用户ID | 宠物 | 战力 | 报名时间 |")
        lines.append("|---|---|---|---|---|")
        for i, e in enumerate(enroll, 1):
            t = time.strftime("%H:%M", self._bj_localtime(e.get("enrolled_at", 0)))
            lines.append(
                f"| {i} | `{e['qq']}` | {e.get('pet_name', e.get('nickname','-'))} | "
                f"{self._fmt_power(e['bp'])} | {t} |"
            )

        lines.append(f"\n### 已确认出战（{len(confirmed)}/{data.SECT_TEAM_SIZE}）")
        lines.append("| 序号 | 用户ID | 宠物 | 战力 | 类型 |")
        lines.append("|---|---|---|---|---|")
        for i, e in enumerate(confirmed, 1):
            lines.append(
                f"| {i} | `{e['qq']}` | {e.get('pet_name', e.get('nickname','-'))} | "
                f"{self._fmt_power(e['bp'])} | {e.get('kind','-')} |"
            )
        return "\n".join(lines)

    def _sect_status(self, group_id: str) -> str:
        self._sect_ensure_master(group_id)
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        self._sect_ensure_today(sect)
        now_ts = int(time.time())
        now = self._bj_localtime()
        match_ts = self._bj_timestamp(
            now.tm_year, now.tm_mon, now.tm_mday,
            data.SECT_WAR_MATCH_HOUR, data.SECT_WAR_MATCH_MIN
        )
        start_ts = self._bj_timestamp(
            now.tm_year, now.tm_mon, now.tm_mday,
            data.SECT_WAR_START_HOUR, data.SECT_WAR_START_MIN
        )
        end_ts = self._bj_timestamp(
            now.tm_year, now.tm_mon, now.tm_mday, 21, 10
        )

        if now_ts < match_ts:
            phase = "报名中"
        elif now_ts < start_ts:
            phase = "已匹配（公示期）"
        elif now_ts < end_ts:
            phase = "战斗中"
        else:
            phase = "已结束"

        war = sect["today"].get("war")

        lines = ["## 🏯 宗门战况"]
        lines.append("| 项目 | 状态 |")
        lines.append("|---|---|")
        lines.append(f"| 当前阶段 | {phase} |")
        lines.append(f"| 匹配时间 | {data.SECT_WAR_MATCH_HOUR:02d}:{data.SECT_WAR_MATCH_MIN:02d} |")
        lines.append(f"| 开战时间 | {data.SECT_WAR_START_HOUR:02d}:{data.SECT_WAR_START_MIN:02d} |")
        lines.append(f"| 参战模式 | 全群参与 |")
        if war and war.get("base_power"):
            lines.append(f"| 初始战力 | {self._fmt_power(war['base_power'])}（随机分配） |")
        lines.append(f"| 当前宗主 | `{sect.get('master_qq','未选举')}` |")

        if war:
            opp = war.get("opponent_name") or war.get("opponent") or ""
            if war.get("phase") == "bye":
                lines.append("| 对手 | 🌀 今日轮空 |")
            else:
                lines.append(f"| 对手 | {opp} |")
                if war.get("phase") == "battling":
                    my_total = war.get("base_power", 0) + war.get("cheer_bonus", 0)
                    lines.append(f"| 当前回合 | 第 {war.get('round', 1)} / {data.SECT_WAR_ROUNDS} 回合 |")
                    lines.append(f"| 当前总战力 | {self._fmt_power(my_total)}（初始 {self._fmt_power(war.get('base_power', 0))} + 加油 {self._fmt_power(war.get('cheer_bonus', 0))}，{war.get('cheer_count', 0)} 次） |")
                    lines.append(f"| 当前比分 | 本宗 {war.get('my_wins', 0)} : {war.get('opp_wins', 0)} 敌方 |")
                    lines.append("| 提示 | 群内所有人发送『加油』为本宗加战力 |")
                elif war.get("phase") == "ended":
                    w = war.get("winner", "")
                    if w == "draw":
                        rtxt = "🤝 平局"
                    elif w == group_id:
                        rtxt = "🏆 胜利"
                    else:
                        rtxt = "❌ 失败"
                    lines.append(f"| 最终结果 | {rtxt} |")
                    lines.append(f"| 最终比分 | 本宗 {war.get('my_wins', 0)} : {war.get('opp_wins', 0)} 敌方 |")
        else:
            lines.append("| 对手 | 尚未匹配 |")

        return "\n".join(lines)

    def _sect_countdown(self, group_id: str) -> str:
        now = self._bj_localtime()
        now_ts = int(time.time())
        match_ts = self._bj_timestamp(
            now.tm_year, now.tm_mon, now.tm_mday,
            data.SECT_WAR_MATCH_HOUR, data.SECT_WAR_MATCH_MIN
        )
        start_ts = self._bj_timestamp(
            now.tm_year, now.tm_mon, now.tm_mday,
            data.SECT_WAR_START_HOUR, data.SECT_WAR_START_MIN
        )
        end_ts = self._bj_timestamp(
            now.tm_year, now.tm_mon, now.tm_mday, 21, 10
        )
        if now_ts < match_ts:
            remain = match_ts - now_ts
            return (
                f"## ⏳ 宗门战倒计时\n"
                f"距离今日 {data.SECT_WAR_MATCH_HOUR:02d}:{data.SECT_WAR_MATCH_MIN:02d} 匹配还有：`{self._fmt_duration(remain)}`"
            )
        if now_ts < start_ts:
            remain = start_ts - now_ts
            return (
                f"## ⏳ 宗门战倒计时\n"
                f"已匹配对手，距离 {data.SECT_WAR_START_HOUR:02d}:{data.SECT_WAR_START_MIN:02d} 开战还有：`{self._fmt_duration(remain)}`"
            )
        if now_ts < end_ts:
            return "## ⏳ 宗门战倒计时\n⚔️ 宗门战正在进行中，发送『加油』为本宗助威！"
        return "## ⏳ 宗门战倒计时\n今日宗门战已结束，下一场将于明日 20:30 匹配。"

    def _sect_matchup(self, group_id: str) -> str:
        """对阵表：新版全群参与模式。"""
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        self._sect_ensure_today(sect)
        war = sect["today"].get("war")
        if not war:
            return "今日尚未匹配对手，20:30 匹配后可查看对阵表。"
        if war.get("phase") == "bye":
            return "🌀 本群今日轮空，无对阵。"

        my_name = sect.get("name", group_id)
        opp_gid = war.get("opponent", "")
        opp_name = war.get("opponent_name", opp_gid)
        my_base = war.get("base_power", 0)

        lines = ["## 🏯 今日宗门对阵", f"**{my_name}** vs **{opp_name}**\n"]
        lines.append(f"本宗初始战力：{self._fmt_power(my_base)}（全群参与，加油决定胜负）\n")

        rounds = war.get("rounds", [])
        if rounds:
            lines.append("### 回合结果")
            lines.append("| 回合 | 本宗战力（初始+加油） | 敌方战力（初始+加油） | 结果 |")
            lines.append("|---|---|---|---|")
            for r in rounds:
                res = "✅ 胜" if r["winner"] == "me" else "❌ 败"
                lines.append(
                    f"| 第{r['round']}回合 | {self._fmt_power(r['my_power'])}"
                    f"（{self._fmt_power(r['my_base'])}+{self._fmt_power(r['my_cheer'])}） | "
                    f"{self._fmt_power(r['opp_power'])}"
                    f"（{self._fmt_power(r['opp_base'])}+{self._fmt_power(r['opp_cheer'])}） | {res} |"
                )
        else:
            lines.append("> 尚未开战，20:40 开战后可查看回合结果。")
        return "\n".join(lines)

    def _sect_live(self, group_id: str) -> str:
        """实时赛况：当前回合、比分、已完成回合明细。"""
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        self._sect_ensure_today(sect)
        war = sect["today"].get("war")
        if not war:
            return "今日尚未匹配对手，20:30 匹配后开战。"
        if war.get("phase") == "bye":
            return "🌀 本群今日轮空。"
        rounds = war.get("rounds", [])
        lines = ["## ⚔️ 宗门赛况"]
        if war.get("phase") == "battling":
            lines.append(f"当前回合：第 {war.get('round', 1)} / {data.SECT_WAR_ROUNDS} 回合（进行中）")
            lines.append(f"本回合加油战力：+{self._fmt_power(war.get('cheer_bonus', 0))}（{war.get('cheer_count', 0)} 次）")
        elif war.get("phase") == "matched":
            lines.append("⚔️ 已匹配，等待 20:40 开战。")
        else:
            lines.append("⚔️ 今日宗门战已结束。")
        lines.append(f"当前比分：本宗 {war.get('my_wins', 0)} : {war.get('opp_wins', 0)} 敌方")
        if rounds:
            lines.append("\n| 回合 | 本宗战力 | 敌方战力 | 结果 |")
            lines.append("|---|---|---|---|")
            for r in rounds:
                res = "✅" if r["winner"] == "me" else "❌"
                lines.append(
                    f"| {r['round']} | {self._fmt_power(r['my_power'])} | "
                    f"{self._fmt_power(r['opp_power'])} | {res} |"
                )
        return "\n".join(lines)

    def _sect_battle_report(self, group_id: str) -> str:
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        history = sect.get("history", [])
        if not history:
            return "暂无宗门战报。"
        last = history[-1]
        lines = [
            "## 📜 宗门战报",
            f"**比赛时间**：{time.strftime('%Y-%m-%d %H:%M', self._bj_localtime(last.get('time',0)))}",
            f"**对阵**：{last.get('my_name', group_id)} vs {last.get('opponent_name', '-')}",
            f"**结果**：{last.get('result_text', '-')}（{last.get('my_wins',0)} : {last.get('opp_wins',0)}）",
            f"**获得宗门积分**：+{last.get('points',0)}",
            "",
            "| 排名 | 玩家 | 宠物 | 战力 | 贡献 |",
            "|---|---|---|---|---|",
        ]
        for i, c in enumerate(last.get("top_contributors", []), 1):
            lines.append(
                f"| {i} | `{c['qq']}` | {c.get('pet_name','-')} | "
                f"{self._fmt_power(c.get('bp',0))} | {c.get('contrib',0)} |"
            )
        return "\n".join(lines)

    def _sect_history(self, group_id: str) -> str:
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        history = sect.get("history", [])
        if not history:
            return "本赛季暂无宗门战记录。"
        lines = ["## 📚 宗门战历史"]
        lines.append("| 日期 | 对手 | 结果 | 比分 | 积分 |")
        lines.append("|---|---|---|---|---|")
        for h in history[-20:]:
            date = time.strftime("%m-%d", self._bj_localtime(h.get("time", 0)))
            opp = h.get("opponent_name", h.get("opponent", "-"))
            result = h.get("result_text", "-")
            score = f"{h.get('my_wins',0)}:{h.get('opp_wins',0)}"
            points = h.get("points", 0)
            lines.append(f"| {date} | {opp} | {result} | {score} | +{points} |")
        return "\n".join(lines)

    def _sect_rank(self, group_id: str) -> str:
        """宗门排行榜：先按等级，同等级按历史累计宗门积分；显示前 10 + 当前宗门排名。"""
        groups = self.store._data.get("groups", {})
        entries = []
        for gid, g in groups.items():
            if not g.get("cross"):
                continue
            sect = g.get("sect", {})
            if not sect.get("enabled", True):
                continue
            # 排序键：(等级, 历史累计积分)
            level = sect.get("level", 1)
            total = sect.get("total_points", 0)
            entries.append((gid, sect, (level, total)))
        # 先按等级降序，再按累计积分降序
        entries.sort(key=lambda x: x[2], reverse=True)

        # 计算当前宗门排名
        my_rank = None
        for i, (gid, _, _) in enumerate(entries, 1):
            if gid == group_id:
                my_rank = i
                break

        lines = ["## 🏯 宗门排行榜"]
        lines.append("| 排名 | 宗门 | 等级 | 积分 | 胜/平/败 | 宗主 |")
        lines.append("|---|---|---|---|---|---|")
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        for i, (gid, sect, _) in enumerate(entries[:10], 1):
            name = sect.get("name", gid)
            lines.append(
                f"| {medals.get(i, i)} | {name} | Lv{sect.get('level',1)} | "
                f"{sect.get('total_points',0)} | "
                f"{sect.get('win',0)}/{sect.get('draw',0)}/{sect.get('lose',0)} | "
                f"`{sect.get('master_qq','-')}` |"
            )

        if my_rank is not None:
            my_sect = self.store.get_group(group_id).get("sect", {})
            my_name = my_sect.get("name", group_id)
            lines.append(
                f"\n> 本宗 `{my_name}` 当前排名：**第 {my_rank} 名** "
                f"（Lv{my_sect.get('level',1)} / {my_sect.get('total_points',0)} 积分）"
            )
        return "\n".join(lines)

    def _sect_intro(self, group_id: str) -> str:
        """宗门战玩法详细介绍。"""
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        master = sect.get("master_qq", "未选举")
        lines = [
            "## 🏯 宗门战玩法介绍",
            "",
            "### 一、什么是宗门？",
            "- 一个 QQ 群就是一个宗门。",
            "- 宗门之间每天进行跨群联赛，争夺排名与奖励。",
            f"- 本群当前宗主：`{master}`",
            "",
            "### 二、宗主与副宗主",
            "- **宗主**：本群当前宠物战力最高者自动担任（战力优先）。",
            "- **副宗主**：由宗主任命，最多 3 人，可协助管理报名名单。",
            "- **权限**：宗主/副宗主可确认出战名单、踢出报名者、设置公告、修改宗门名、花费宗门积分兑换商店商品。",
            "",
            "### 三、每日时间安排",
            "| 时间 | 事件 |",
            "|---|---|",
            "| 00:00 | 重置每日数据、选举宗主、计算强制出战前三 |",
            "| 00:00 ~ 20:30 | 普通成员报名出战（限 7 人） |",
            "| 20:30 | 系统自动确认名单、匹配对手，向本群广播对手 |",
            "| 20:30 ~ 20:40 | 公示期，可发送 `宗门对阵` 查看双方阵容 |",
            "| 20:40 | 第 1 回合开始，向本群广播开战，可开始『加油』 |",
            "| 20:50 | 第 1 回合结束，广播回合战况、扣血、清空加油值 |",
            "| 21:00 | 第 2 回合结束，广播回合战况、扣血、清空加油值 |",
            "| 21:10 | 第 3 回合结束（决赛），广播最终战报、发放奖励 |",
            "",
            "### 四、出战与战斗机制",
            f"- 每群共 **{data.SECT_TEAM_SIZE}** 人出战：",
            f"  - **{data.SECT_FORCED_COUNT} 人强制出战**：本群战力前三，无需报名。",
            f"  - **{data.SECT_ENROLL_COUNT} 人报名出战**：普通成员发送 `宗门报名` 参与，宗主可踢出不合格者。",
            f"- **三回合制**：共 {data.SECT_WAR_ROUNDS} 回合，每回合 {data.SECT_WAR_ROUND_MINUTES} 分钟。回合战力 = 宗门基础战力 + 本回合加油值，高者赢得该回合；三回合后胜场多者获胜。",
            "- **基础战力**：本宗所有参战宠物当前战力之和，并按血量折算（血量越低战力越低）。",
            f"- **加油**：战斗中群内发送『加油』可为本宗当前回合随机加战力 +{data.SECT_CHEER_MIN}~{data.SECT_CHEER_MAX}，冷却 {data.SECT_CHEER_CD_MIN}~{data.SECT_CHEER_CD_MAX} 秒。仅出战名单内成员有效；回合结束后清空，下回合重新计算。",
            f"- **扣血**：每回合结束对所有参战宠物随机扣 {data.SECT_WAR_HP_LOSS_MIN_PCT}~{data.SECT_WAR_HP_LOSS_MAX_PCT}% 最大血量（不致死），请及时补充宠物血量。",
            "",
            "### 五、宗门积分与升级",
            "| 来源 | 宗门积分（累计/可用） | 宗门贡献（个人资产） |",
            "|---|---|---|",
            f"| 宗门签到 | +{data.SECT_SIGN_POINTS} | +{data.SECT_CONTRIBUTION_SIGN} |",
            f"| 宗门战参战 | 按胜负增加 | +{data.SECT_CONTRIBUTION_BATTLE_LOSE} |",
            f"| 宗门战胜利方 | 按胜负增加 | 额外 +{data.SECT_CONTRIBUTION_BATTLE} |",
            "- **宗门积分**：分为『累计积分』（用于升级、排行榜）和『可用积分』（宗门商店消耗）。",
            "- **宗门升级**：累计宗门积分达到阈值后自动升级，消耗可用积分不影响等级。",
            "- **宗门贡献**：个人资产，任何人都可以在宗门商店兑换专属商品。",
            "",
            "### 六、宗门商店",
            "- 发送 `宗门商店` 查看可兑换商品。",
            "- 宗门积分商品：仅宗主/副宗主可兑换。",
            "- 宗门贡献商品：任何人可用自己的贡献兑换。",
            "- 发送 `宗门兑换 商品名` 进行兑换。",
            "",
            "### 七、完整指令列表",
            "| 指令 | 说明 | 权限 |",
            "|---|---|---|",
            "| `宗门介绍` | 查看本介绍 | 所有人 |",
            "| `宗门信息` | 查看宗门基础信息 | 所有人 |",
            "| `宗门名单` | 查看今日强制/报名/确认名单 | 所有人 |",
            "| `宗门战况` | 查看当前阶段、对手、倒计时 | 所有人 |",
            "| `宗门对阵` | 查看今日对阵表 | 所有人 |",
            "| `宗门赛况` | 查看实时/已结束轮次 | 所有人 |",
            "| `宗门战报` | 查看上一场详细战报 | 所有人 |",
            "| `宗门历史` | 查看本赛季历史记录 | 所有人 |",
            "| `宗门倒计时` | 显示距离开战时间 | 所有人 |",
            "| `宗门排行` | 全服宗门排行榜 | 所有人 |",
            "| `宗门签到` | 每日签到，宗门积分/贡献 + | 所有人 |",
            "| `宗门报名` | 报名今日出战 | 所有人 |",
            "| `加油` | 战斗中为本宗加战力 | 出战名单成员 |",
            "| `宗门商店` | 查看宗门商店 | 所有人 |",
            "| `宗门兑换 商品名` | 花费宗门积分/贡献兑换 | 见商品说明 |",
            "| `宗门确认` | 确认最终出战名单 | 宗主/副宗主 |",
            "| `宗门踢出 QQ` | 踢出报名者 | 宗主/副宗主 |",
            "| `宗门公告 内容` | 设置宗门公告 | 宗主/副宗主 |",
            "| `宗门改名 名称` | 修改宗门名 | 宗主/副宗主 |",
            "| `宗门任命副宗主 QQ` | 任命副宗主 | 宗主 |",
            "| `宗门撤销副宗主 QQ` | 撤销副宗主 | 宗主 |",
            "| `宗门重选宗主` | 立即重新选举宗主 | 宗主/副宗主/管理员 |",
            "| `宗门选举` | 手动触发宗主选举 | 宗主/副宗主 |",
            "",
            "### 八、新手建议流程",
            "1. 发送 `宗门信息` 查看本群宗门状态。",
            "2. 发送 `宗门签到` 积累宗门积分与个人贡献。",
            "3. 如果你是战力前三，会自动强制出战；否则发送 `宗门报名`。",
            "4. 20:30 后发送 `宗门对阵` 查看双方阵容与对手。",
            "5. 20:40 开战后群内发送 `加油` 为本宗加战力，共 3 回合。",
            "6. 21:10 结束后发送 `宗门战报` 查看战果与奖励，并及时给参战宠物补血。",
        ]
        return "\n".join(lines)

    def _sect_shop(self, group_id: str) -> str:
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        points = sect.get("points", 0)
        lines = [f"## 🏯 宗门商店（宗门积分：{points}）", ""]
        lines.append("| 商品 | 价格 | 类型 | 说明 |")
        lines.append("|---|---|---|---|")
        for name, cfg in data.SECT_SHOP.items():
            kind = "道具" if "item" in cfg else "货币"
            cost_type = cfg.get("cost_type", "sect_points")
            if cost_type == "sect_points":
                price = f"{cfg['points']} 宗门积分"
                note = "宗主/副宗主可兑换"
            else:
                price = f"{cfg['contribution']} 宗门贡献"
                note = "个人资产，任何人可兑换"
            lines.append(f"| {name} | {price} | {kind} | {note} |")
        lines.append("\n> 用法：`宗门兑换 商品名`")
        return "\n".join(lines)

    def _sect_buy(self, player: dict, group_id: str, tokens: list[str]) -> str:
        if len(tokens) < 2:
            return "用法：宗门兑换 商品名"
        name = tokens[1]
        if name not in data.SECT_SHOP:
            return f"商品『{name}』不存在。"
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        cfg = data.SECT_SHOP[name]
        cost_type = cfg.get("cost_type", "sect_points")

        if cost_type == "sect_points":
            # 宗门积分：全宗共享，仅宗主/副宗主可花
            if not self._sect_is_master_or_deputy(player, group_id):
                return "仅宗主或副宗主可花费宗门积分。"
            need = cfg.get("points", 0)
            if sect.get("points", 0) < need:
                return f"宗门积分不足（需要 {need}，当前 {sect['points']}）。"
            sect["points"] = sect.get("points", 0) - need
        elif cost_type == "contribution":
            # 宗门贡献：个人资产，任何人可花
            psect = player.setdefault("sect", self.store._default_player_sect())
            need = cfg.get("contribution", 0)
            if psect.get("contribution", 0) < need:
                return f"个人宗门贡献不足（需要 {need}，当前 {psect['contribution']}）。"
            psect["contribution"] = psect.get("contribution", 0) - need
        else:
            return "商品配置异常。"

        if "item" in cfg:
            self.store.add_item(player, cfg["item"], cfg.get("count", 1))
            return f"🏯 宗门兑换成功，获得 {cfg['item']} ×{cfg.get('count',1)}。"
        if "currency" in cfg:
            self.store.add_currency(player, cfg["currency"], cfg["amount"])
            return f"🏯 宗门兑换成功，获得 {cfg['currency']} +{cfg['amount']}。"
        return "兑换异常。"

    def _sect_calc_power(self, group_id: str) -> int:
        """计算本群已确认出战队伍的总战力。"""
        group = self.store.get_group(group_id)
        sect = group.setdefault("sect", self.store._default_group_sect())
        confirmed = sect.get("today", {}).get("confirmed", [])
        return sum(x["bp"] for x in confirmed)

    # ---------------------------------------------------------------------
    # 宗门战：匹配、战斗、结算、广播
    # ---------------------------------------------------------------------
    def _sect_match_making(self) -> list[tuple[str, str]]:
        """新版宗门战：所有开启宗门且启用跨群的群随机配对（全群参与，无需确认名单）。"""
        groups = self.store._data.get("groups", {})
        candidates = []
        for gid, g in groups.items():
            if not g.get("enabled") or not g.get("cross"):
                continue
            sect = g.get("sect", {})
            if not sect.get("enabled", True):
                continue
            candidates.append(gid)
        # 随机打乱后相邻配对；奇数时最弱的宗门轮空
        import random as _random
        _random.shuffle(candidates)
        matches = []
        for i in range(0, len(candidates) - 1, 2):
            matches.append((candidates[i], candidates[i + 1]))
        return matches

    def _sect_pet_effective_power(self, pet: dict) -> int:
        """宠物当前有效战力：基础战力 × (当前血量/最大血量)。血量越低战力越低。"""
        bp = petmod.battle_power(pet)
        hp_max = pet.get("hp_max", 0) or 0
        if hp_max <= 0:
            return 0
        ratio = max(0, pet.get("hp", 0)) / hp_max
        return int(bp * ratio)

    def _sect_group_base_power(self, group_id: str) -> tuple[int, list[dict]]:
        """新版宗门战：返回随机初始战力（≤5万），不再依赖宠物属性。"""
        group = self.store.get_group(group_id)
        war = group.get("sect", {}).get("today", {}).get("war")
        if war and war.get("base_power"):
            return war["base_power"], []
        # 未分配时返回 0（实际由 _sect_war_start 分配）
        return 0, []

    def _sect_war_deduct_hp(self, group_id: str) -> list[tuple]:
        """回合结束：对本群所有参战宠物随机扣血（不致死，floor=1）。返回扣血明细。"""
        group = self.store.get_group(group_id)
        sect = group.get("sect", {})
        confirmed = sect.get("today", {}).get("confirmed", [])
        losses = []
        for e in confirmed:
            qq = e["qq"]
            pl = self.store.get_player(qq, group_id, create=False)
            if not pl:
                continue
            pet = pl.get("pet")
            if not pet or petmod.is_dead(pet):
                continue
            hp_max = pet.get("hp_max", 1) or 1
            pct = random.randint(data.SECT_WAR_HP_LOSS_MIN_PCT, data.SECT_WAR_HP_LOSS_MAX_PCT)
            loss = max(1, int(hp_max * pct / 100))
            pet["hp"] = max(1, pet.get("hp", 0) - loss)
            losses.append((pet.get("nickname", "-"), loss, pet["hp"], hp_max))
        return losses

    def _sect_init_war(self, gid: str, opp_gid: str) -> None:
        """为本群初始化今日宗门战状态（匹配阶段，新版全群参与）。"""
        group = self.store.get_group(gid)
        sect = group.setdefault("sect", self.store._default_group_sect())
        self._sect_ensure_today(sect)
        opp_name = self.store.get_group(opp_gid).get("sect", {}).get("name", opp_gid)
        sect["today"]["war"] = {
            "opponent": opp_gid,
            "opponent_name": opp_name,
            "phase": "matched",
            "round": 0,
            "base_power": 0,         # 开战时随机分配
            "cheer_bonus": 0,
            "cheer_count": 0,
            "rounds": [],
            "my_wins": 0,
            "opp_wins": 0,
            "winner": "",
            "matched_at": int(time.time()),
        }

    async def _send_to_group(self, group_id: str, text: str) -> bool:
        """向单个群主动推送一条消息（定向广播）。"""
        from astrbot.api.event import MessageChain
        group = self.store.get_group(group_id)
        umo = group.get("umo")
        if not umo:
            logger.warning(f"[petpark] 定向广播跳过：群 {group_id} 未记录 umo（群内尚无消息触发过）")
            return False
        if not self._is_group_authorized(group_id):
            logger.warning(f"[petpark] 定向广播跳过：群 {group_id} 未授权或授权已过期")
            return False
        if not group.get("enabled", True):
            logger.info(f"[petpark] 定向广播跳过：群 {group_id} 已关闭宠物乐园")
            return False
        try:
            await self.context.send_message(
                umo, MessageChain().message(text).use_markdown(True)
            )
            logger.info(f"[petpark] 已向群 {group_id} 定向推送成功")
            return True
        except Exception as e:
            logger.warning(f"[petpark] 向群 {group_id} 定向 Markdown 推送失败：{e}，尝试纯文本降级")
            try:
                await self.context.send_message(umo, MessageChain().message(text))
                logger.info(f"[petpark] 已向群 {group_id} 定向推送成功（纯文本降级）")
                return True
            except Exception:
                logger.exception(f"[petpark] 向群 {group_id} 定向推送失败")
                return False

    async def _sect_broadcast_matchup(self, g1: str, g2: str) -> None:
        n1 = self.store.get_group(g1).get("sect", {}).get("name", g1)
        n2 = self.store.get_group(g2).get("sect", {}).get("name", g2)
        for gid, my_name, opp_name in (
            (g1, n1, n2),
            (g2, n2, n1),
        ):
            text = (
                f"## ⚔️ 宗门战匹配结果\n"
                f"本宗 **{my_name}** 今日对手：**{opp_name}**\n"
                f"> {data.SECT_WAR_START_HOUR:02d}:{data.SECT_WAR_START_MIN:02d} 正式开战，全群成员发送『加油』"
                f"可为本宗加战力（+{data.SECT_CHEER_MIN}~{data.SECT_CHEER_MAX}，冷却"
                f"{data.SECT_CHEER_CD_MIN}~{data.SECT_CHEER_CD_MAX}s）。\n"
                f"> 初始战力随机分配，谁加油多谁获胜！"
            )
            await self._send_to_group(gid, text)

    async def _sect_war_bye(self, matched: set) -> None:
        """轮空：本可参战但未匹配到对手的宗门，给少量积分并写入历史、定向广播。"""
        for gid, g in self.store._data.get("groups", {}).items():
            if not g.get("enabled") or not g.get("cross"):
                continue
            sect = g.get("sect", {})
            if not sect.get("enabled", True):
                continue
            if gid in matched:
                continue
            self._sect_give_points(gid, data.SECT_BYE_POINTS)
            self._sect_ensure_today(sect)
            sect["today"]["war"] = {
                "opponent": "", "opponent_name": "",
                "phase": "bye", "round": 0, "base_power": 0, "cheer_bonus": 0, "cheer_count": 0,
                "rounds": [], "my_wins": 0, "opp_wins": 0, "winner": "bye",
            }
            sect.setdefault("history", []).append({
                "time": int(time.time()),
                "opponent": "", "opponent_name": "轮空",
                "my_name": sect.get("name", gid),
                "result_text": "🌀 轮空",
                "my_wins": 0, "opp_wins": 0,
                "points": data.SECT_BYE_POINTS,
                "top_contributors": [],
            })
            await self._send_to_group(
                gid,
                f"## 🌀 宗门战轮空\n本群今日未匹配到对手，获得轮空积分 +{data.SECT_BYE_POINTS}。",
            )

    async def _sect_war_match(self) -> None:
        """20:30 自动确认名单、匹配、写入双方 war 状态、定向广播对阵、处理轮空。"""
        async with PetParkPlugin._sect_war_lock:
            await self._sect_war_match_locked()

    async def _sect_war_match_locked(self) -> None:
        """_sect_war_match 的锁内实现（新版：全群参与，无需确认名单）。"""
        self._sect_ensure_season()
        season = self._sect_ensure_season()
        today = self._bj_today()
        # 清理今日旧记录（重试安全）
        season["matches"] = [m for m in season.get("matches", []) if m.get("date") != today]
        matches = self._sect_match_making()
        # 去重：避免并发导致的同配对重复追加
        existing = set()
        for m in season.get("matches", []):
            if m.get("date") == today:
                existing.add(tuple(sorted([m["group_a"], m["group_b"]])))
        # 二次去重：用全局 seen 防止同群出现在多个配对中
        seen_gids: set = set()
        matched: set = set()
        for g1, g2 in matches:
            if g1 in seen_gids or g2 in seen_gids:
                logger.warning(f"[petpark] 跳过宗门战配对（群已参战）: {g1} vs {g2}")
                continue
            pair = tuple(sorted([g1, g2]))
            if pair in existing:
                logger.warning(f"[petpark] 跳过重复宗门战配对: {g1} vs {g2}")
                continue
            existing.add(pair)
            seen_gids.add(g1)
            seen_gids.add(g2)
            matched.add(g1)
            matched.add(g2)
            self._sect_init_war(g1, g2)
            self._sect_init_war(g2, g1)
            season.setdefault("matches", []).append({
                "date": today,
                "time": int(time.time()),
                "group_a": g1,
                "group_b": g2,
                "a_wins": 0,
                "b_wins": 0,
                "winner": "",
            })
            await self._sect_broadcast_matchup(g1, g2)
        await self._sect_war_bye(matched)
        await self.store.save()

    async def _sect_war_start(self) -> None:
        """20:40 第1回合开始：进入 battling，清空加油值，定向广播开战。"""
        async with PetParkPlugin._sect_war_lock:
            await self._sect_war_start_locked()

    async def _sect_war_start_locked(self) -> None:
        today = self._bj_today()
        season = self._sect_ensure_season()
        broadcasted: set = set()  # 去重：避免同群重复推送开战消息
        for m in season.get("matches", []):
            if m.get("date") != today:
                continue
            for gid in (m["group_a"], m["group_b"]):
                if gid in broadcasted:
                    continue
                group = self.store.get_group(gid)
                war = group.get("sect", {}).get("today", {}).get("war")
                if not war or war.get("phase") != "matched":
                    continue
                # 幂等：如果已经进入 battling 阶段则跳过（重复触发防护）
                broadcasted.add(gid)
                # 为新版宗门战分配随机初始战力
                if war.get("base_power") is None:
                    import random as _random
                    war["base_power"] = _random.randint(10000, data.SECT_WAR_BASE_POWER_MAX)
                    opp_gid = war.get("opponent", "")
                    if opp_gid:
                        opp_group = self.store.get_group(opp_gid)
                        opp_war = opp_group.get("sect", {}).get("today", {}).get("war")
                        if opp_war and opp_war.get("base_power") is None:
                            opp_war["base_power"] = _random.randint(10000, data.SECT_WAR_BASE_POWER_MAX)
                war["phase"] = "battling"
                war["round"] = 1
                war["cheer_bonus"] = 0
                war["cheer_count"] = 0
                opp = war.get("opponent_name", "")
                my_base = war.get("base_power", 0)
                await self._send_to_group(
                    gid,
                    f"## ⚔️ 宗门战正式开战！\n"
                    f"本宗 vs **{opp}**\n"
                    f"● 本宗初始战力：{self._fmt_power(my_base)}\n"
                    f"> 第 1 回合开始！群内所有人发送『加油』为本宗加战力。"
                    f"每 {data.SECT_WAR_ROUND_MINUTES} 分钟一个回合，共 {data.SECT_WAR_ROUNDS} 回合，"
                    f"21:10 结束。",
                )
        await self.store.save()

    async def _sect_war_round_end(self, round_num: int, final: bool) -> None:
        """20:50/21:00/21:10 回合结束：结算本回合、扣血、清加油值、定向广播；决赛结算奖励。"""
        async with PetParkPlugin._sect_war_lock:
            await self._sect_war_round_end_locked(round_num, final)

    async def _sect_war_round_end_locked(self, round_num: int, final: bool) -> None:
        today = self._bj_today()
        season = self._sect_ensure_season()
        for m in season.get("matches", []):
            if m.get("date") != today:
                continue
            await self._sect_war_process_round(m["group_a"], m["group_b"], round_num, final, m)
        await self.store.save()

    async def _sect_war_process_round(
        self, g1: str, g2: str, round_num: int, final: bool, m: dict
    ) -> None:
        war1 = self.store.get_group(g1).get("sect", {}).get("today", {}).get("war")
        war2 = self.store.get_group(g2).get("sect", {}).get("today", {}).get("war")
        if not war1 or not war2 or war1.get("phase") != "battling":
            return
        # 幂等：本回合已结算过则跳过（防止重启后在同一分钟内重复触发）
        if any(r.get("round") == round_num for r in war1.get("rounds", [])):
            return
        base1 = war1.get("base_power", 0)
        base2 = war2.get("base_power", 0)
        cheer1 = war1.get("cheer_bonus", 0)
        cheer2 = war2.get("cheer_bonus", 0)
        power1 = base1 + cheer1
        power2 = base2 + cheer2
        if power1 > power2:
            winner = 1
        elif power2 > power1:
            winner = 2
        else:
            winner = random.choice([1, 2])

        # 写入双方回合记录（各自视角）
        for war, me_won, my_base, my_cheer, my_power, opp_base, opp_cheer, opp_power in (
            (war1, winner == 1, base1, cheer1, power1, base2, cheer2, power2),
            (war2, winner == 2, base2, cheer2, power2, base1, cheer1, power1),
        ):
            war["my_wins"] = war.get("my_wins", 0) + (1 if me_won else 0)
            war["opp_wins"] = war.get("opp_wins", 0) + (0 if me_won else 1)
            war["rounds"].append({
                "round": round_num,
                "my_base": my_base,
                "opp_base": opp_base,
                "my_cheer": my_cheer,
                "opp_cheer": opp_cheer,
                "my_power": my_power,
                "opp_power": opp_power,
                "winner": "me" if me_won else "opp",
            })
            # 清空加油值，下回合重新计算
            war["cheer_bonus"] = 0
            war["cheer_count"] = 0

        m["a_wins"] = war1.get("my_wins", 0)
        m["b_wins"] = war1.get("opp_wins", 0)

        if final:
            self._sect_war_settle(g1, g2, war1, war2)
            m["winner"] = war1.get("winner", "")
            await self._sect_broadcast_final(g1, g2, war1, war2)
        else:
            war1["round"] = round_num + 1
            war2["round"] = round_num + 1
            await self._sect_broadcast_round(g1, g2, war1, war2, round_num)

    async def _sect_broadcast_round(
        self, g1: str, g2: str, war1: dict, war2: dict, round_num: int
    ) -> None:
        n1 = self.store.get_group(g1).get("sect", {}).get("name", g1)
        n2 = self.store.get_group(g2).get("sect", {}).get("name", g2)
        for gid, war, my_name, opp_name in (
            (g1, war1, n1, n2),
            (g2, war2, n2, n1),
        ):
            last = war["rounds"][-1]
            rtxt = "✅ 本回合胜" if last["winner"] == "me" else "❌ 本回合败"
            text = (
                f"## ⚔️ 宗门战 · 第{round_num}回合结束\n"
                f"**{my_name}** vs **{opp_name}**\n"
                f"● 本宗战力：{self._fmt_power(last['my_power'])}"
                f"（初始 {self._fmt_power(last['my_base'])} + 加油 {self._fmt_power(last['my_cheer'])}）\n"
                f"● 敌方战力：{self._fmt_power(last['opp_power'])}"
                f"（初始 {self._fmt_power(last['opp_base'])} + 加油 {self._fmt_power(last['opp_cheer'])}）\n"
                f"● 本回合结果：{rtxt}\n"
                f"● 当前比分：本宗 {war['my_wins']} : {war['opp_wins']} 敌方\n"
                f"> 下回合继续加油！"
            )
            await self._send_to_group(gid, text)

    async def _sect_broadcast_final(
        self, g1: str, g2: str, war1: dict, war2: dict
    ) -> None:
        n1 = self.store.get_group(g1).get("sect", {}).get("name", g1)
        n2 = self.store.get_group(g2).get("sect", {}).get("name", g2)
        winner = war1.get("winner", "")
        for gid, war, my_name, opp_name in (
            (g1, war1, n1, n2),
            (g2, war2, n2, n1),
        ):
            if winner == "draw":
                rtxt = "🤝 平局"
            elif winner == gid:
                rtxt = "🏆 胜利"
            else:
                rtxt = "❌ 失败"
            round_lines = "\n".join(
                f"第{r['round']}回合：本宗 {self._fmt_power(r['my_power'])} vs "
                f"敌方 {self._fmt_power(r['opp_power'])} -> "
                f"{'✅' if r['winner'] == 'me' else '❌'}"
                for r in war["rounds"]
            )
            text = (
                f"## 🏆 宗门战 · 最终战报\n"
                f"**{my_name}** vs **{opp_name}**\n"
                f"● 三回合比分：本宗 {war['my_wins']} : {war['opp_wins']} 敌方\n"
                f"● 最终结果：**{rtxt}**\n\n"
                f"{round_lines}\n\n"
                f"> 奖励已发放，发送『宗门战报』查看详情。"
            )
            await self._send_to_group(gid, text)

    def _sect_war_kill_all_pets(self, group_id: str) -> None:
        """宗门战失败：将该群所有玩家的出战宠物血量归0（宠物死亡）。"""
        for pkey, pl in self.store._data.get("players", {}).items():
            if pl.get("group") != group_id:
                continue
            # 处理多宠物：所有宠物血量归0
            pets = pl.get("pets", [])
            for pet in pets:
                if pet and not petmod.is_dead(pet):
                    pet["hp"] = 0
            # 兼容：如果只有单宠物引用
            p = pl.get("pet")
            if p and isinstance(p, dict):
                p["hp"] = 0

    def _sect_war_settle(self, g1: str, g2: str, war1: dict, war2: dict) -> None:
        """决赛结算：宗门积分、胜负记录、个人奖励/贡献、写入历史。"""
        w1 = war1.get("my_wins", 0)
        w2 = war1.get("opp_wins", 0)
        if w1 > w2:
            winner = g1
        elif w2 > w1:
            winner = g2
        else:
            winner = "draw"
        war1["phase"] = "ended"
        war2["phase"] = "ended"
        war1["winner"] = winner
        war2["winner"] = winner

        # 宗门积分
        if winner == "draw":
            self._sect_give_points(g1, data.SECT_DRAW_POINTS)
            self._sect_give_points(g2, data.SECT_DRAW_POINTS)
            points_a = points_b = data.SECT_DRAW_POINTS
        else:
            self._sect_give_points(winner, data.SECT_WIN_POINTS)
            loser = g2 if winner == g1 else g1
            self._sect_give_points(loser, data.SECT_LOSE_POINTS)
            points_a = data.SECT_WIN_POINTS if winner == g1 else data.SECT_LOSE_POINTS
            points_b = data.SECT_WIN_POINTS if winner == g2 else data.SECT_LOSE_POINTS
        # 宗门胜负记录
        for gid, is_a in ((g1, True), (g2, False)):
            group = self.store.get_group(gid)
            sect = group.setdefault("sect", self.store._default_group_sect())
            sect["battles"] = sect.get("battles", 0) + 1
            if winner == "draw":
                sect["draw"] = sect.get("draw", 0) + 1
            elif (winner == g1 and is_a) or (winner == g2 and not is_a):
                sect["win"] = sect.get("win", 0) + 1
            else:
                sect["lose"] = sect.get("lose", 0) + 1

        # 新版：参与者获得完整奖励，未参与者获得保底（宗门贡献+统计）
        contributors: dict[str, list[dict]] = {g1: [], g2: []}
        for gid in (g1, g2):
            is_guild_winner = (winner == gid) if winner != "draw" else False
            is_draw = winner == "draw"
            # 获取本方的 war 对象来读取 cheer_players
            g_war = war1 if gid == g1 else war2
            cheer_players = g_war.get("cheer_players", {}) if g_war else {}
            for pkey, pl in self.store._data.get("players", {}).items():
                if pl.get("group") != gid:
                    continue
                qq = str(pl.get("qq", ""))
                pet = pl.get("pet")
                participated = qq in cheer_players
                contrib = data.SECT_CONTRIBUTION_BATTLE_LOSE
                if is_guild_winner:
                    contrib += data.SECT_CONTRIBUTION_BATTLE
                psect = pl.setdefault("sect", self.store._default_player_sect())
                psect["contribution"] = psect.get("contribution", 0) + contrib
                psect["total_contribution"] = psect.get("total_contribution", 0) + contrib
                psect["season_contribution"] = psect.get("season_contribution", 0) + contrib
                psect["battles"] = psect.get("battles", 0) + 1
                if is_guild_winner:
                    psect["wins"] = psect.get("wins", 0) + 1
                psect["last_battle"] = int(time.time())
                self._sect_inc_active(pl, 5 if is_guild_winner else 2)
                # 参与者：完整奖励（积分+金币）；未参与者：仅保底（宗门贡献+统计）
                if participated:
                    if is_guild_winner:
                        self.store.add_currency(pl, "积分", data.SECT_WIN_JIFEN)
                        self.store.add_currency(pl, "金币", data.SECT_WIN_COIN)
                    elif is_draw:
                        self.store.add_currency(pl, "积分", data.SECT_DRAW_JIFEN)
                        self.store.add_currency(pl, "金币", data.SECT_DRAW_COIN)
                    else:
                        self.store.add_currency(pl, "积分", data.SECT_LOSE_JIFEN)
                        self.store.add_currency(pl, "金币", data.SECT_LOSE_COIN)
                contributors[gid].append({
                    "qq": qq,
                    "pet_name": pet.get("nickname", "-") if pet else "-",
                    "bp": 0,
                    "contrib": contrib,
                })

        # 失败方全群宠物死亡
        if winner != "draw":
            loser = g2 if winner == g1 else g1
            self._sect_war_kill_all_pets(loser)

        result = {
            "date": self._bj_today(),
            "time": int(time.time()),
            "group_a": g1,
            "group_b": g2,
            "a_wins": w1,
            "b_wins": w2,
            "winner": winner,
        }
        self._sect_record_history(result, contributors, points_a, points_b)

    def _sect_record_history(
        self,
        result: dict,
        contributors: dict[str, list[dict]],
        points_a: int,
        points_b: int,
    ) -> None:
        g1, g2 = result["group_a"], result["group_b"]
        winner = result["winner"]
        for gid, is_a in ((g1, True), (g2, False)):
            group = self.store.get_group(gid)
            sect = group.setdefault("sect", self.store._default_group_sect())
            my_wins = result["a_wins"] if is_a else result["b_wins"]
            opp_wins = result["b_wins"] if is_a else result["a_wins"]
            opp_gid = g2 if is_a else g1
            my_name = sect.get("name", gid)
            opp_name = self.store.get_group(opp_gid).get("sect", {}).get("name", opp_gid)
            if winner == "draw":
                rtxt = "🤝 平局"
            elif (winner == g1 and is_a) or (winner == g2 and not is_a):
                rtxt = "🏆 胜利"
            else:
                rtxt = "❌ 失败"
            top = sorted(contributors.get(gid, []), key=lambda x: x["contrib"], reverse=True)[:5]
            hist = {
                "time": result["time"],
                "opponent": opp_gid,
                "opponent_name": opp_name,
                "my_name": my_name,
                "result_text": rtxt,
                "my_wins": my_wins,
                "opp_wins": opp_wins,
                "points": points_a if is_a else points_b,
                "top_contributors": top,
            }
            sect.setdefault("history", []).append(hist)

    def _sect_cheer(self, player: dict, group_id: str) -> str | None:
        """加油：全群任何人可为本宗增加战力，并通知敌方我方当前战力。"""
        group = self.store.get_group(group_id)
        sect = group.get("sect", {})
        war = sect.get("today", {}).get("war")
        if not war or war.get("phase") != "battling":
            return None  # 非战斗中，当作普通聊天不回复
        cd = self.store.cooldown_remaining(player, "sect:cheer")
        if cd > 0:
            return f"加油冷却中，还需 {cd} 秒。"
        bonus = random.randint(data.SECT_CHEER_MIN, data.SECT_CHEER_MAX)
        war["cheer_bonus"] = war.get("cheer_bonus", 0) + bonus
        war["cheer_count"] = war.get("cheer_count", 0) + 1
        # 记录参与加油的玩家（用于结算时区分参与/未参与奖励）
        qq = str(player.get("qq", ""))
        war.setdefault("cheer_players", {})[qq] = True
        cd_sec = random.randint(data.SECT_CHEER_CD_MIN, data.SECT_CHEER_CD_MAX)
        self.store.set_cooldown(player, "sect:cheer", cd_sec)
        opp = war.get("opponent_name", "")
        my_base = war.get("base_power", 0)
        my_total = my_base + war["cheer_bonus"]
        my_name = sect.get("name", group_id)
        # 通知敌方：我方当前总战力（后台异步发送，不阻塞回复）
        opp_gid = war.get("opponent", "")
        if opp_gid:
            try:
                asyncio.ensure_future(self._send_to_group(
                    opp_gid,
                    f"## 📣 敌情通报\n"
                    f"**{my_name}** 刚刚加油！当前总战力：**{self._fmt_power(my_total)}**\n"
                    f"> 快发送『加油』迎头赶上！",
                ))
            except Exception:
                pass
        return (
            f"📣 加油成功！为本宗增加战力 **+{self._fmt_power(bonus)}**\n"
            f"● 本宗当前总战力：{self._fmt_power(my_total)}"
            f"（初始 {self._fmt_power(my_base)} + 加油 {self._fmt_power(war['cheer_bonus'])}，{war['cheer_count']} 次）\n"
            f"● 对手：{opp}　● 冷却 {cd_sec} 秒\n"
            f"> 第{war.get('round', 1)}回合进行中，继续加油！"
        )

    async def _background_sect_war(self) -> None:
        """后台定时任务：宗门战时间轴。
        20:30 匹配 ｜ 20:40 开战 ｜ 20:50/21:00/21:10 回合结算（21:10 为决赛）。"""
        # fired 持久化到 store，避免插件重载/重启后同一分钟内重复触发
        fired = self.store._data.setdefault("sect_war_fired", {})
        while True:
            await asyncio.sleep(20)
            now = self._bj_localtime()
            h, m = now.tm_hour, now.tm_min
            today = self._bj_today()
            # 清理非今日的过期 fired 记录
            for k in list(fired.keys()):
                if not k.startswith(today):
                    del fired[k]
            try:
                if h == data.SECT_WAR_MATCH_HOUR and m == data.SECT_WAR_MATCH_MIN:
                    key = f"{today}_match"
                    if fired.get(key) != 1:
                        fired[key] = 1
                        logger.info(f"[petpark] 宗门战匹配触发 {today} {h:02d}:{m:02d}")
                        await self._sect_war_match()
                elif h == data.SECT_WAR_START_HOUR and m == data.SECT_WAR_START_MIN:
                    key = f"{today}_start"
                    if fired.get(key) != 1:
                        fired[key] = 1
                        logger.info(f"[petpark] 宗门战开战触发 {today} {h:02d}:{m:02d}")
                        await self._sect_war_start()
                elif h == 20 and m == 50:
                    key = f"{today}_r1"
                    if fired.get(key) != 1:
                        fired[key] = 1
                        logger.info(f"[petpark] 宗门战第1回合结算 {today} {h:02d}:{m:02d}")
                        await self._sect_war_round_end(1, False)
                elif h == 21 and m == 0:
                    key = f"{today}_r2"
                    if fired.get(key) != 1:
                        fired[key] = 1
                        logger.info(f"[petpark] 宗门战第2回合结算 {today} {h:02d}:{m:02d}")
                        await self._sect_war_round_end(2, False)
                elif h == 21 and m == 10:
                    key = f"{today}_r3"
                    if fired.get(key) != 1:
                        fired[key] = 1
                        logger.info(f"[petpark] 宗门战第3回合结算 {today} {h:02d}:{m:02d}")
                        await self._sect_war_round_end(3, True)
            except Exception:
                logger.exception("[petpark] 宗门战时间轴异常")

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
            f"💕 『{p['nickname']}』**{p['love_state']}** 中\n"
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
            return f"💍 已向 {target} 求婚，消耗『永恒钻戒』x1，等待对方『同意求婚 {player['qq']}』。"
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

    # =====================================================================
    # 宠物家园（放置建造 · 纯金币升级 · 宠物派遣 · 偷菜护院 · 流浪商人 · 排行）
    # =====================================================================
    def _homestead_tutorial(self) -> str:
        """家园介绍 / 家园教程 —— 新手指南。"""
        return (
            "## 🏡 宠物家园 · 玩法教程\n"
            "\n"
            "> 建造建筑 → 随时间自动累积金币/积分 → 收取升级 → 更多产出\n"
            "\n"
            "### 🚀 快速入门\n"
            "1. 发送「**建造 金币矿**」建第一座建筑（500金）\n"
            "2. 等待一段时间，发送「**家园收取**」收获金币\n"
            "3. 金币够了发送「**升级 金币矿**」提升产量\n"
            "4. 家园经验攒够自动升级，解锁更多建筑位\n"
            "\n"
            "### 🏗️ 7 种建筑（建筑位有限，需取舍）\n"
            "💰 **金币矿** — 纯金币产出（500金建造）\n"
            "🏭 **积分工坊** — 纯积分产出（500金建造）\n"
            "🏛️ **聚宝盆** — 金币+积分双产，效率60%（1000金建造）\n"
            "🌿 **经验泉** — 宠物经验产出，需Lv60（2000金建造）\n"
            "📦 **仓库** — 离线累积上限+2h/级（800金建造）\n"
            "🏹 **哨塔** — 防御偷菜，+25防御/级（1200金建造）\n"
            "🕯️ **祈福坛** — 好事件概率↑（1500金建造）\n"
            "\n"
            "### 🐾 宠物派遣\n"
            "发送「**派遣 建筑名**」让宠物驻扎建筑，产量倍率：\n"
            "`1.0 + 等级×0.006 + 品质×0.04 + 属性匹配0.10`\n"
            "> 例：Lv100混沌金→金币矿 = ×2.06 产量！\n"
            "> 派遣每小时耗2精力，发送「**召回 建筑名**」取回\n"
            "\n"
            "### 💀 偷菜玩法\n"
            "发送「**顺手牵羊 QQ**」偷别人未收资源\n"
            "成功率 = 你的宠物Lv / (你的Lv + 对方防御 + 50)\n"
            "成功偷10%~30%　失败赔50金　每日5次\n"
            "> 建哨塔+派宠物守家提升防御，或买护院符免疫12h\n"
            "\n"
            "### 🧳 流浪商人\n"
            "收取时10%概率出现（祈福坛可提升）\n"
            "可买：加速券/护院符/双倍券/进化神石/史诗卡等\n"
            "> 发送「**商人购买 编号**」购买，「0」跳过\n"
            "\n"
            "### 📊 排行\n"
            "「**家园排行**」本周产出Top10，前三奖励金币\n"
            "「**家园总排行**」累计产出Top10\n"
            "\n"
            "> 发送「**家园**」查看你的家园状态。"
        )

    def _homestead_menu(self, player: dict, group_id: str) -> str:
        """家园 —— 查看家园总览。"""
        hs = self.store.homestead_state(player)
        level = hs["level"]
        slots = data.homestead_slots(level)
        buildings = hs.get("buildings", {})
        dispatch = hs.get("dispatch", {})
        wh_level = buildings.get("仓库", {}).get("level", 0) if "仓库" in buildings else 0
        max_acc = data.homestead_max_accumulate(wh_level)
        defense = data.homestead_defense(hs)
        shield = "🛡️ 护院符生效中" if hs.get("shield_until", 0) > int(time.time()) else ""
        lines = [
            f"## 🏡 我的家园（Lv{level}）",
            f"🔧 建筑位：{len(buildings)}/{slots}　✨ 经验：{hs['exp']}/{data.homestead_exp_to_next(level)}",
            f"🛡️ 防御力：{defense}　📦 离线上限：{max_acc // 3600}h{'　' + shield if shield else ''}",
            "",
        ]
        total_coin = 0
        total_jifen = 0
        total_exp = 0
        now = int(time.time())
        if not buildings:
            lines.append("🏜️ 家园空空如也，发送「**建造 建筑名**」开始建设。")
        else:
            for name, b in buildings.items():
                cfg = data.HOMESTEAD_BUILDINGS.get(name, {})
                icon = cfg.get("icon", "🏠")
                lv = b.get("level", 1)
                prod = data.homestead_production(name, lv)
                acc_limit = max_acc if not cfg.get("warehouse") else max_acc
                elapsed = min(now - b.get("last_collect", now), acc_limit)
                hours = elapsed / 3600
                acc_coin = int(prod.get("coin", 0) * hours)
                acc_jifen = int(prod.get("jifen", 0) * hours)
                acc_exp = int(prod.get("exp", 0) * hours) if "exp" in prod else 0
                # 宠物派遣加成预览
                disp_info = dispatch.get(name, {})
                if disp_info:
                    disp_mult = data.homestead_dispatch_multiplier(disp_info, name)
                    acc_coin = int(acc_coin * disp_mult)
                    acc_jifen = int(acc_jifen * disp_mult)
                    acc_exp = int(acc_exp * disp_mult)
                total_coin += acc_coin
                total_jifen += acc_jifen
                total_exp += acc_exp
                parts = [f"{icon} **{name}** Lv{lv}"]
                income_parts = []
                if acc_coin:
                    income_parts.append(f"💰 {acc_coin}")
                if acc_jifen:
                    income_parts.append(f"💎 {acc_jifen}")
                if acc_exp:
                    income_parts.append(f"📖 {acc_exp}exp")
                if income_parts:
                    parts.append("待收：" + " · ".join(income_parts))
                # 派遣状态
                if disp_info:
                    disp_qq = disp_info.get("qq", "")
                    mult_str = f"×{data.homestead_dispatch_multiplier(disp_info, name)}"
                    parts.append(f"🐾 {disp_qq}({mult_str})")
                else:
                    next_cost = data.homestead_upgrade_cost(lv, cfg.get("build_cost", 500))
                    parts.append(f"⬆️{next_cost}金")
                lines.append("　".join(parts))
        lines.append("")
        summary_parts = []
        if total_coin:
            summary_parts.append(f"💰 {total_coin} 金币")
        if total_jifen:
            summary_parts.append(f"💎 {total_jifen} 积分")
        if total_exp:
            summary_parts.append(f"📖 {total_exp} 经验")
        if summary_parts:
            lines.append(f"📦 待收取总计：{' · '.join(summary_parts)}")
        # 可建造提示
        available = self._homestead_available(player)
        if available:
            lines.append(f"🏗️ 可建造：{' · '.join(available)}")
        # 排行
        weekly = hs.get("weekly_coin", 0)
        total_life = hs.get("total_coin_earned", 0)
        if weekly or total_life:
            lines.append(f"📊 本周产出 {weekly} 金 · 累计产出 {total_life} 金")
        lines.append("")
        lines.append("> **家园收取** 收获 · **建造/升级 建筑名** · **派遣/召回 建筑名**")
        lines.append("> **拜访家园 QQ** 串门 · **顺手牵羊 QQ** 偷菜 · **家园排行**")
        return "\n".join(lines)

    def _homestead_available(self, player: dict) -> list[str]:
        """返回当前可建造的建筑名称列表。"""
        hs = self.store.homestead_state(player)
        built = set(hs.get("buildings", {}).keys())
        p = player.get("pet") or {}
        pet_level = p.get("level", 1)
        available = []
        for name, cfg in data.HOMESTEAD_BUILDINGS.items():
            if name in built:
                continue
            req_lv = cfg.get("unlock_pet_level", 0)
            if req_lv and pet_level < req_lv:
                continue
            available.append(f"{cfg.get('icon','')}{name}({cfg['build_cost']}金)")
        return available

    def _homestead_build(self, player: dict, tokens: list[str]) -> str:
        """建造 建筑名 —— 在家园中建造新建筑。"""
        hs = self.store.homestead_state(player)
        if len(tokens) < 2:
            available = self._homestead_available(player)
            if available:
                return f"用法：建造 建筑名\n可选：{' · '.join(available)}"
            return "用法：建造 建筑名"
        name = tokens[1]
        cfg = data.HOMESTEAD_BUILDINGS.get(name)
        if not cfg:
            return f"没有『{name}』这种建筑。可选：{' · '.join(data.HOMESTEAD_BUILDINGS)}"
        if name in hs.get("buildings", {}):
            return f"你已经建造过{name}了，发送「升级 {name}」升级。"
        slots = data.homestead_slots(hs["level"])
        if len(hs.get("buildings", {})) >= slots:
            return f"建筑位已满（{slots}个）！升级家园可解锁更多位置。"
        p = player.get("pet") or {}
        req_lv = cfg.get("unlock_pet_level", 0)
        if req_lv and p.get("level", 1) < req_lv:
            return f"🔒 建造{cfg.get('icon','')}**{name}**需要宠物 Lv{req_lv}，当前 Lv{p.get('level', 1)}。"
        cost = cfg["build_cost"]
        if player.get("coin", 0) < cost:
            return f"金币不足，建造{name}需要 **{cost}** 金币，当前仅有 {player['coin']}。"
        player["coin"] -= cost
        now = int(time.time())
        hs["buildings"][name] = {"level": 1, "last_collect": now}
        hs["exp"] = hs.get("exp", 0) + 10
        levelup = self._homestead_check_levelup(hs)
        icon = cfg.get("icon", "")
        lines = [
            f"🏗️ 成功建造 {icon}**{name}** Lv1！消耗 {cost} 金币。",
            f"● 产量：{self._homestead_prod_text(name, 1)}",
        ]
        if levelup:
            lines.append(f"● {levelup}")
        return "\n".join(lines)

    def _homestead_upgrade(self, player: dict, tokens: list[str]) -> str:
        """升级 建筑名 —— 升级家园建筑。"""
        hs = self.store.homestead_state(player)
        if len(tokens) < 2:
            built = list(hs.get("buildings", {}).keys())
            if built:
                tips = " · ".join(f"{b}(Lv{hs['buildings'][b]['level']})" for b in built)
                return f"用法：升级 建筑名\n当前：{tips}"
            return "你还没有任何建筑，发送「建造 建筑名」。"
        name = tokens[1]
        if name not in hs.get("buildings", {}):
            return f"还没有建造{name}，发送「**建造 {name}**」。"
        cfg = data.HOMESTEAD_BUILDINGS.get(name, {})
        b = hs["buildings"][name]
        current_lv = b["level"]
        cost = data.homestead_upgrade_cost(current_lv, cfg.get("build_cost", 500))
        if player.get("coin", 0) < cost:
            return f"金币不足，升级{name}到 Lv{current_lv + 1} 需要 **{cost}** 金币，当前仅有 {player['coin']}。"
        player["coin"] -= cost
        b["level"] += 1
        new_lv = b["level"]
        hs["exp"] = hs.get("exp", 0) + new_lv
        levelup = self._homestead_check_levelup(hs)
        icon = cfg.get("icon", "")
        lines = [
            f"⬆️ {icon}**{name}** Lv{current_lv} → **Lv{new_lv}**！消耗 {cost} 金币。",
            f"● 产量：{self._homestead_prod_text(name, new_lv)}",
        ]
        if levelup:
            lines.append(f"● {levelup}")
        return "\n".join(lines)

    def _homestead_collect(self, player: dict) -> str:
        """家园收取 —— 一键收获所有建筑产出 + 随机事件 + 流浪商人。"""
        hs = self.store.homestead_state(player)
        buildings = hs.get("buildings", {})
        if not buildings:
            return "🏜️ 家园空空如也，发送「建造 建筑名」开始建设。"
        cd = self.store.cooldown_remaining(player, "homestead:collect")
        if cd > 0:
            m, s = divmod(cd, 60)
            return f"⏳ 家园收取冷却中，请 {m} 分 {s} 秒后再来。"
        now = int(time.time())
        wh_level = buildings.get("仓库", {}).get("level", 0) if "仓库" in buildings else 0
        max_acc = data.homestead_max_accumulate(wh_level)
        double_next = hs.get("next_collect_bonus", 0.0) > 0.99  # 双倍券处理
        total_coin = 0
        total_jifen = 0
        total_exp = 0
        lines = ["## 📦 家园收取", ""]
        for name, b in buildings.items():
            cfg = data.HOMESTEAD_BUILDINGS.get(name, {})
            icon = cfg.get("icon", "🏠")
            lv = b.get("level", 1)
            elapsed = min(now - b.get("last_collect", now), max_acc)
            if elapsed < 60 and not double_next:
                lines.append(f"{icon} {name}：刚收过，暂无产出")
                continue
            hours = max(elapsed, 60) / 3600  # 至少1分钟
            prod = data.homestead_production(name, lv)
            coin = int(prod.get("coin", 0) * hours)
            jifen = int(prod.get("jifen", 0) * hours)
            exp = int(prod.get("exp", 0) * hours) if "exp" in prod else 0
            # 派遣加成
            disp_info = hs.get("dispatch", {}).get(name, {})
            if disp_info:
                disp_mult = data.homestead_dispatch_multiplier(disp_info, name)
                coin = int(coin * disp_mult)
                jifen = int(jifen * disp_mult)
                exp = int(exp * disp_mult)
                # 扣除派遣精力（定位具体派遣的宠物）
                energy_cost = int(data.HOMESTEAD_DISPATCH_ENERGY_PER_HOUR * hours)
                if disp_info.get("qq") == str(player.get("qq", "")):
                    pet_idx = disp_info.get("pet_index", player.get("active_pet", -1))
                    pets = player.get("pets", [])
                    if 0 <= pet_idx < len(pets):
                        pets[pet_idx]["energy"] = max(0, pets[pet_idx].get("energy", 100) - energy_cost)
            h, m = divmod(int(elapsed // 60), 60)
            time_str = f"{h}时{m}分" if h > 0 else f"{m}分钟"
            parts = []
            if coin:
                parts.append(f"💰 +{coin}")
                total_coin += coin
            if jifen:
                parts.append(f"💎 +{jifen}")
                total_jifen += jifen
            if exp:
                parts.append(f"📖 +{exp}exp")
                total_exp += exp
            disp_tag = f" [🐾×{data.homestead_dispatch_multiplier(disp_info, name):.1f}]" if disp_info else ""
            if parts:
                lines.append(f"{icon} {name} Lv{lv}{disp_tag}（{time_str}）：{' · '.join(parts)}")
            else:
                lines.append(f"{icon} {name} Lv{lv}（{time_str}）：无产出")
            b["last_collect"] = now
        if total_coin == 0 and total_jifen == 0 and total_exp == 0 and not double_next:
            return "⏳ 建筑刚刚收过，稍等片刻再来。"
        # 双倍券
        if double_next:
            total_coin *= 2
            total_jifen *= 2
            total_exp *= 2
            hs["next_collect_bonus"] = 0.0
        # 随机事件（用祈福坛加权）
        event = data.homestead_roll_event(hs)
        mult = event.get("mult", 1.0)
        next_bonus = hs.get("next_collect_bonus", 0.0)
        if next_bonus > 0:
            mult += next_bonus
            hs["next_collect_bonus"] = 0.0
            lines.append(f"📈 上次暴风雨补偿：+{int(next_bonus * 100)}%！")
        # 地脉涌动
        extra_all = event.get("extra_all", 0)
        if extra_all > 1:
            mult *= extra_all
        if mult != 1.0:
            total_coin = int(total_coin * mult)
            total_jifen = int(total_jifen * mult)
            total_exp = int(total_exp * mult)
        # 宠物帮忙事件
        pet_bonus = event.get("pet_bonus", 0)
        pet_bonus_text = ""
        if pet_bonus:
            p = player.get("pet") or {}
            pet_lv = p.get("level", 1)
            bonus_coin = int(total_coin * pet_bonus * pet_lv / 100)
            bonus_jifen = int(total_jifen * pet_bonus * pet_lv / 100)
            total_coin += bonus_coin
            total_jifen += bonus_jifen
            pet_bonus_text = f"金币+{bonus_coin} 积分+{bonus_jifen}"
        # 幸运日额外金币
        extra_coin_range = event.get("extra_coin")
        extra_text = ""
        if extra_coin_range:
            extra = random.randint(*extra_coin_range)
            total_coin += extra
            extra_text = f"+{extra}"
        # 下次加成
        next_bonus_set = event.get("next_bonus", 0)
        if next_bonus_set:
            hs["next_collect_bonus"] = next_bonus_set
        # 事件文本
        event_text = event.get("text", "")
        if event_text:
            event_text = event_text.replace("{bonus}", pet_bonus_text or extra_text)
            lines.append("")
            lines.append(f"{event.get('emoji', '')} {event_text}")
        # 流浪商人
        merchant = event.get("merchant")
        if merchant:
            hs["merchant_pending"] = self._homestead_gen_merchant()
            lines.append("")
            lines.append("🧳 **流浪商人**带来了货物！发送「**商人购买 编号**」购买：")
            for i, item in enumerate(hs["merchant_pending"], 1):
                price_type = "金币" if item["price_type"] == "coin" else "积分"
                lines.append(f"　{i}. {item['name']} — {item['price']} {price_type}（{item['desc']}）")
            lines.append("　发送「**商人购买 0**」不买。")
        # 发放
        if total_coin:
            player["coin"] = player.get("coin", 0) + total_coin
        if total_jifen:
            player["jifen"] = player.get("jifen", 0) + total_jifen
        if total_exp:
            p = player.get("pet")
            if p:
                p["exp"] = p.get("exp", 0) + total_exp
                self._auto_level(player)
        # 家园经验 + 排行统计
        hs["exp"] = hs.get("exp", 0) + 5
        levelup = self._homestead_check_levelup(hs)
        self._homestead_update_weekly(hs)
        hs["total_coin_earned"] = hs.get("total_coin_earned", 0) + total_coin
        summary = []
        if total_coin:
            summary.append(f"💰 {total_coin} 金币")
        if total_jifen:
            summary.append(f"💎 {total_jifen} 积分")
        if total_exp:
            summary.append(f"📖 {total_exp} 经验")
        lines.append("")
        lines.append(f"✅ 收取完成！{' · '.join(summary)}")
        if levelup:
            lines.append(levelup)
        self.store.set_cooldown(player, "homestead:collect", 300)
        return "\n".join(lines)

    def _homestead_demolish(self, player: dict, tokens: list[str]) -> str:
        """拆除 建筑名 —— 拆除家园建筑，返还 20% 建造+升级费用。"""
        hs = self.store.homestead_state(player)
        buildings = hs.get("buildings", {})
        if len(tokens) < 2:
            built = list(buildings.keys())
            if built:
                tips = " · ".join(f"{b}(Lv{buildings[b]['level']})" for b in built)
                return f"用法：拆除 建筑名\n当前建筑：{tips}\n⚠️ 拆除仅返还 **20%** 费用！"
            return "你还没有任何建筑。"
        name = tokens[1]
        if name not in buildings:
            return f"没有找到建筑『{name}』。当前建筑：{' · '.join(buildings)}"
        cfg = data.HOMESTEAD_BUILDINGS.get(name, {})
        b = buildings[name]
        current_lv = b["level"]
        # 计算累计投入：建造费 + 每级升级费
        total_cost = cfg.get("build_cost", 500)
        for lv in range(1, current_lv):
            total_cost += data.homestead_upgrade_cost(lv, cfg.get("build_cost", 500))
        refund = int(total_cost * 0.2)
        player["coin"] = player.get("coin", 0) + refund
        # 清理派遣
        dispatch = hs.get("dispatch", {})
        if name in dispatch:
            dispatch.pop(name)
        # 拆除建筑
        buildings.pop(name)
        icon = cfg.get("icon", "")
        return (
            f"🔨 已拆除 {icon}**{name}** Lv{current_lv}。\n"
            f"● 累计投入 {total_cost} 金币，返还 **{refund}** 金币（20%）\n"
            f"● 建筑位已释放（{len(buildings)}/{data.homestead_slots(hs['level'])}）"
        )

    def _homestead_buildings(self, player: dict) -> str:
        """家园建筑 —— 查看全部建筑图鉴和详细信息。"""
        hs = self.store.homestead_state(player)
        built = hs.get("buildings", {})
        dispatch = hs.get("dispatch", {})
        wh_level = built.get("仓库", {}).get("level", 0) if "仓库" in built else 0
        max_acc = data.homestead_max_accumulate(wh_level)
        defense = data.homestead_defense(hs)
        p = player.get("pet") or {}
        pet_level = p.get("level", 1)
        lines = ["## 🏗️ 家园建筑图鉴", ""]
        for name, cfg in data.HOMESTEAD_BUILDINGS.items():
            icon = cfg.get("icon", "🏠")
            disp_info = dispatch.get(name, {})
            if name in built:
                b = built[name]
                lv = b["level"]
                prod_text = self._homestead_prod_text(name, lv)
                next_cost = data.homestead_upgrade_cost(lv, cfg.get("build_cost", 500))
                lines.append(f"{icon} **{name}** Lv{lv}（已建造）")
                lines.append(f"　产量：{prod_text}")
                if disp_info:
                    mult = data.homestead_dispatch_multiplier(disp_info, name)
                    lines.append(f"　🐾 派遣：{disp_info.get('qq','?')} ×{mult}")
                lines.append(f"　⬆️ 升级 Lv{lv + 1} 需 {next_cost} 金币")
            else:
                req_lv = cfg.get("unlock_pet_level", 0)
                if req_lv and pet_level < req_lv:
                    lines.append(f"{icon} **{name}**（🔒 需宠物 Lv{req_lv}）")
                else:
                    lines.append(f"{icon} **{name}**（可建造 · {cfg['build_cost']} 金币）")
                lines.append(f"　{cfg['desc']}")
                lines.append(f"　Lv1 表现：{self._homestead_prod_text(name, 1)}")
            lines.append("")
        lines.append(f"🏡 Lv{hs['level']} · 建筑位 {len(built)}/{data.homestead_slots(hs['level'])} · 防御 {defense} · 离线上限 {max_acc // 3600}h")
        return "\n".join(lines)

    def _homestead_visit(self, player: dict, group_id: str, tokens: list[str]) -> str:
        """拜访家园 / 家园拜访 —— 拜访好友家园。"""
        hs = self.store.homestead_state(player)
        today = time.strftime("%Y-%m-%d")
        if hs.get("visit_date") != today:
            hs["visit_date"] = today
            hs["visit_today"] = 0
        if hs["visit_today"] >= data.HOMESTEAD_VISIT_MAX_PER_DAY:
            return f"今日拜访次数已用完（{data.HOMESTEAD_VISIT_MAX_PER_DAY}/天），明天再来。"
        if len(tokens) < 2:
            return f"用法：拜访家园 用户ID（今日剩余 {data.HOMESTEAD_VISIT_MAX_PER_DAY - hs['visit_today']} 次）"
        target_qq = tokens[1]
        tp = self.store.get_player(target_qq, group_id, create=False)
        if not tp:
            return f"本群不存在用户 {target_qq}。"
        ths = self.store.homestead_state(tp)
        tbuildings = ths.get("buildings", {})
        tdispatch = ths.get("dispatch", {})
        hs["visit_today"] += 1
        player["coin"] = player.get("coin", 0) + data.HOMESTEAD_VISIT_REWARD_COIN
        tp["coin"] = tp.get("coin", 0) + data.HOMESTEAD_VISITED_REWARD_COIN
        tdefense = data.homestead_defense(ths)
        lines = [
            f"## 🏡 拜访 {target_qq} 的家园",
            f"🏡 Lv{ths['level']} · {len(tbuildings)}/{data.homestead_slots(ths['level'])} 建筑位 · 🛡️ 防御 {tdefense}",
            "",
        ]
        if not tbuildings:
            lines.append("🏜️ 一片荒芜...")
        else:
            for name, b in tbuildings.items():
                cfg = data.HOMESTEAD_BUILDINGS.get(name, {})
                icon = cfg.get("icon", "🏠")
                lv = b.get("level", 1)
                prod_text = self._homestead_prod_text(name, lv)
                disp_tag = ""
                if name in tdispatch:
                    disp_tag = f" [🐾{tdispatch[name].get('qq','?')}]"
                lines.append(f"{icon} **{name}** Lv{lv}{disp_tag}　{prod_text}")
        lines.append("")
        lines.append(f"🤝 拜访成功！你 +{data.HOMESTEAD_VISIT_REWARD_COIN} 金，对方 +{data.HOMESTEAD_VISITED_REWARD_COIN} 金。")
        remain = data.HOMESTEAD_VISIT_MAX_PER_DAY - hs["visit_today"]
        lines.append(f"📅 剩余拜访 {remain} 次 · 💀 也可「顺手牵羊 {target_qq}」偷菜！")
        return "\n".join(lines)

    # =====================================================================
    # 宠物派遣
    # =====================================================================
    def _homestead_dispatch(self, player: dict, tokens: list[str]) -> str:
        """派遣 建筑名 [宠物序号] —— 将宠物派遣到建筑上，提升产量。不指定序号默认当前出战宠物。"""
        hs = self.store.homestead_state(player)
        pets = player.get("pets", [])
        if not pets:
            return "你还没有宠物，无法派遣。"
        # 解析参数：派遣 建筑名 [宠物序号]
        if len(tokens) < 2:
            built = list(hs.get("buildings", {}).keys())
            if built:
                return f"用法：派遣 建筑名 [宠物序号]\n可选建筑：{' · '.join(built)}\n不指定序号默认派遣当前出战宠物"
            return "你还没有建筑，先发送「建造 建筑名」。"
        name = tokens[1]
        if name not in hs.get("buildings", {}):
            return f"你还没有建造{name}。"
        # 确定要派遣的宠物
        pet_index = player.get("active_pet", -1)
        if len(tokens) >= 3:
            try:
                idx = int(tokens[2]) - 1  # 用户输入从1开始
                if idx < 0 or idx >= len(pets):
                    return f"宠物序号无效，你共有 {len(pets)} 只宠物（输入 1~{len(pets)}）。"
                pet_index = idx
            except ValueError:
                return f"宠物序号无效，请输入数字（1~{len(pets)}）。"
        if pet_index < 0 or pet_index >= len(pets):
            return "当前没有出战宠物，请先切换宠物。"
        p = pets[pet_index]
        if p.get("energy", 0) < data.HOMESTEAD_DISPATCH_MIN_ENERGY:
            return f"『{p.get('nickname', '?')}』精力不足（需 ≥{data.HOMESTEAD_DISPATCH_MIN_ENERGY}，当前 {p.get('energy', 0)}）。"
        # 检查该宠物是否已派遣到其他建筑
        dispatch = hs.get("dispatch", {})
        my_qq = str(player.get("qq", ""))
        for bname, dp in dispatch.items():
            if dp.get("qq") == my_qq and dp.get("pet_index") == pet_index:
                return f"『{p.get('nickname', '?')}』已派遣到{bname}，先发送「**召回 {bname}**」。"
        cfg = data.HOMESTEAD_BUILDINGS.get(name, {})
        mult = data.homestead_dispatch_multiplier(p, name)
        element_tag = ""
        if cfg.get("prefer_element") == p.get("element", ""):
            element_tag = "（属性匹配 +10%）"
        dispatch[name] = {
            "qq": my_qq,
            "pet_index": pet_index,
            "level": p.get("level", 1),
            "quality": p.get("quality", "普通"),
            "element": p.get("element", ""),
            "since": int(time.time()),
        }
        icon = cfg.get("icon", "")
        return (
            f"🐾 『{p.get('nickname', '?')}』已派遣到 {icon}**{name}**！\n"
            f"● 产量倍率：×**{mult}**{element_tag}\n"
            f"● 每小时消耗 {data.HOMESTEAD_DISPATCH_ENERGY_PER_HOUR} 点精力\n"
            f"● 发送「**召回 {name}**」召回宠物"
        )

    def _homestead_recall(self, player: dict, tokens: list[str]) -> str:
        """召回 建筑名 —— 从建筑上召回宠物。"""
        hs = self.store.homestead_state(player)
        dispatch = hs.get("dispatch", {})
        my_qq = str(player.get("qq", ""))
        if len(tokens) < 2:
            my_dispatch = [b for b, dp in dispatch.items() if dp.get("qq") == my_qq]
            if my_dispatch:
                return f"用法：召回 建筑名\n当前派遣：{' · '.join(my_dispatch)}"
            return "你的宠物当前没有派遣到任何建筑。"
        name = tokens[1]
        if name not in dispatch:
            return f"{name}上没有派遣宠物。"
        if dispatch[name].get("qq") != my_qq:
            return "这不是你的宠物，你只能召回自己的宠物。"
        dispatch.pop(name)
        return f"🐾 已从{name}召回宠物。"

    def _homestead_dispatch_status(self, player: dict) -> str:
        """派遣状态 —— 查看当前所有建筑的派遣情况。"""
        hs = self.store.homestead_state(player)
        dispatch = hs.get("dispatch", {})
        if not dispatch:
            return "当前没有派遣任何宠物。发送「派遣 建筑名」派遣。"
        lines = ["## 🐾 派遣状态", ""]
        for name, dp in dispatch.items():
            cfg = data.HOMESTEAD_BUILDINGS.get(name, {})
            icon = cfg.get("icon", "🏠")
            mult = data.homestead_dispatch_multiplier(dp, name)
            elapsed = int(time.time()) - dp.get("since", int(time.time()))
            h, m = divmod(elapsed // 60, 60)
            time_str = f"{h}时{m}分" if h > 0 else f"{m}分钟"
            # 尝试获取派遣宠物的昵称
            pet_name = f"Lv{dp.get('level',1)} {dp.get('quality','')}"
            owner_qq = dp.get("qq", "?")
            if owner_qq != "?":
                owner_pl = self.store.get_player(owner_qq, player.get("group", ""), create=False)
                if owner_pl:
                    pet_idx = dp.get("pet_index", -1)
                    pets = owner_pl.get("pets", [])
                    if 0 <= pet_idx < len(pets):
                        pn = pets[pet_idx].get("nickname", "")
                        if pn:
                            pet_name = f"『{pn}』{pet_name}"
            lines.append(f"{icon} **{name}** ← {owner_qq} {pet_name}")
            lines.append(f"　倍率 ×{mult} · 已派遣 {time_str}")
        return "\n".join(lines)

    # =====================================================================
    # 偷菜系统
    # =====================================================================
    def _homestead_steal(self, player: dict, group_id: str, tokens: list[str]) -> str:
        """顺手牵羊 / 偷菜 QQ —— 尝试偷取目标家园的未收资源。"""
        hs = self.store.homestead_state(player)
        today = time.strftime("%Y-%m-%d")
        if hs.get("steal_date") != today:
            hs["steal_date"] = today
            hs["steal_today"] = 0
        if hs["steal_today"] >= data.HOMESTEAD_STEAL_MAX_PER_DAY:
            return f"今日偷菜次数已用完（{data.HOMESTEAD_STEAL_MAX_PER_DAY}/天），明天再来。"
        if len(tokens) < 2:
            return f"用法：顺手牵羊 用户ID（今日剩余 {data.HOMESTEAD_STEAL_MAX_PER_DAY - hs['steal_today']} 次）"
        target_qq = tokens[1]
        if target_qq == str(player.get("qq", "")):
            return "不能偷自己的家园！"
        tp = self.store.get_player(target_qq, group_id, create=False)
        if not tp:
            return f"本群不存在用户 {target_qq}。"
        ths = self.store.homestead_state(tp)
        # 检查护院符
        if ths.get("shield_until", 0) > int(time.time()):
            remain = ths["shield_until"] - int(time.time())
            h, m = divmod(remain // 60, 60)
            return f"🛡️ 该家园有护院符保护（剩余 {h}时{m}分），无法偷取。"
        # 检查冷却
        steal_targets = hs.get("steal_targets", {})
        last_steal = steal_targets.get(target_qq, 0)
        if int(time.time()) - last_steal < data.HOMESTEAD_STEAL_COOLDOWN_SAME:
            remain = data.HOMESTEAD_STEAL_COOLDOWN_SAME - (int(time.time()) - last_steal)
            m = remain // 60
            return f"刚偷过 {target_qq} 的家园，请 {m} 分钟后再来。"
        # 检查目标被偷次数
        if ths.get("be_stolen_date") != today:
            ths["be_stolen_date"] = today
            ths["be_stolen_today"] = 0
        if ths.get("be_stolen_today", 0) >= data.HOMESTEAD_MAX_BE_STOLEN_PER_DAY:
            return f"目标今日已被偷 {data.HOMESTEAD_MAX_BE_STOLEN_PER_DAY} 次，无法再偷。"
        # 检查目标是否有未收资源
        tbuildings = ths.get("buildings", {})
        if not tbuildings:
            return f"目标家园一片荒芜，没什么可偷的。"
        now = int(time.time())
        t_coin = 0
        t_jifen = 0
        wh_level = tbuildings.get("仓库", {}).get("level", 0) if "仓库" in tbuildings else 0
        t_max_acc = data.homestead_max_accumulate(wh_level)
        for name, b in tbuildings.items():
            elapsed = min(now - b.get("last_collect", now), t_max_acc)
            if elapsed < 300:
                continue
            prod = data.homestead_production(name, b.get("level", 1))
            t_coin += int(prod.get("coin", 0) * elapsed / 3600)
            t_jifen += int(prod.get("jifen", 0) * elapsed / 3600)
        if t_coin == 0 and t_jifen == 0:
            return "目标家园暂时没什么可偷的（资源刚被收走）。"
        # 计算成功率
        attacker_pet = player.get("pet") or {}
        attacker_lv = attacker_pet.get("level", 1)
        target_defense = data.homestead_defense(ths)
        success_rate = data.homestead_steal_success_rate(attacker_lv, target_defense)
        hs["steal_today"] += 1
        steal_targets[target_qq] = int(time.time())
        hs["steal_targets"] = steal_targets
        if random.random() < success_rate:
            ratio = random.uniform(data.HOMESTEAD_STEAL_RATIO_MIN, data.HOMESTEAD_STEAL_RATIO_MAX)
            stolen_coin = int(t_coin * ratio)
            stolen_jifen = int(t_jifen * ratio)
            player["coin"] = player.get("coin", 0) + stolen_coin
            player["jifen"] = player.get("jifen", 0) + stolen_jifen
            tp["coin"] = max(0, tp.get("coin", 0) - stolen_coin // 2)  # 目标损失一半
            tp["jifen"] = max(0, tp.get("jifen", 0) - stolen_jifen // 2)
            ths["be_stolen_today"] = ths.get("be_stolen_today", 0) + 1
            return (
                f"💀 **偷菜成功！**（成功率 {success_rate:.0%}）\n"
                f"● 偷得 {target_qq} 的 💰{stolen_coin} 金币 + 💎{stolen_jifen} 积分\n"
                f"● 目标防御力：{target_defense}　今日剩余偷取：{data.HOMESTEAD_STEAL_MAX_PER_DAY - hs['steal_today']} 次"
            )
        else:
            player["coin"] = max(0, player.get("coin", 0) - data.HOMESTEAD_STEAL_FAIL_PENALTY)
            tp["coin"] = tp.get("coin", 0) + data.HOMESTEAD_STEAL_FAIL_PENALTY
            return (
                f"🚨 **偷菜被抓！**（成功率 {success_rate:.0%}）\n"
                f"● 被 {target_qq} 的哨塔发现了！赔偿 {data.HOMESTEAD_STEAL_FAIL_PENALTY} 金币\n"
                f"● 目标防御力：{target_defense}"
            )

    # =====================================================================
    # 流浪商人
    # =====================================================================
    def _homestead_gen_merchant(self) -> list[dict]:
        """生成流浪商人货架（随机 3 件）。"""
        items = random.sample(data.HOMESTEAD_MERCHANT_ITEMS, min(3, len(data.HOMESTEAD_MERCHANT_ITEMS)))
        return items

    def _homestead_merchant_buy(self, player: dict, tokens: list[str]) -> str:
        """商人购买 编号 —— 从流浪商人处购买物品（0=不买）。"""
        hs = self.store.homestead_state(player)
        merchant = hs.get("merchant_pending")
        if not merchant:
            return "当前没有商人来访。收取时有一定概率遇到流浪商人。"
        if len(tokens) < 2:
            lines = ["## 🧳 流浪商人", ""]
            for i, item in enumerate(merchant, 1):
                price_type = "金币" if item["price_type"] == "coin" else "积分"
                lines.append(f"{i}. {item['name']} — {item['price']} {price_type}（{item['desc']}）")
            lines.append("")
            lines.append("发送「**商人购买 编号**」购买，「**商人购买 0**」不买。")
            return "\n".join(lines)
        try:
            idx = int(tokens[1])
        except ValueError:
            return "编号请输入数字。"
        if idx == 0:
            hs["merchant_pending"] = None
            return "🧳 商人离开了。下次再来吧！"
        if idx < 1 or idx > len(merchant):
            return f"编号 1~{len(merchant)}，或 0 不买。"
        item = merchant[idx - 1]
        price_type = item["price_type"]
        price = item["price"]
        currency = "金币" if price_type == "coin" else "积分"
        wallet = player.get("coin" if price_type == "coin" else "jifen", 0)
        if wallet < price:
            return f"{currency}不足（需 {price}，当前 {wallet}）。"
        if price_type == "coin":
            player["coin"] -= price
        else:
            player["jifen"] -= price
        hs["merchant_pending"] = None
        # 处理效果类物品
        effect = item.get("effect")
        if effect == "speed_2h":
            # 建筑加速：所有建筑 last_collect 往前推 2 小时
            for b in hs.get("buildings", {}).values():
                b["last_collect"] = max(b.get("last_collect", 0), int(time.time())) - 7200
            return f"⚡ 使用**建筑加速券**！所有建筑累积时间 +2 小时，快去收取！"
        if effect == "shield_12h":
            hs["shield_until"] = int(time.time()) + 43200
            return "🛡️ 使用**护院符**！12 小时内免疫偷菜。"
        if effect == "double_next":
            hs["next_collect_bonus"] = 1.0
            return "✨ 使用**双倍券**！下次收取产量翻倍。"
        # 普通物品
        item_name = item.get("item", "")
        item_count = item.get("item_count", 1)
        if item_name:
            bag = player.setdefault("bag", {})
            bag[item_name] = bag.get(item_name, 0) + item_count
        return f"🧳 购买成功！获得 **{item['name']}**，花费 {price} {currency}。发送「查看背包」查看。"

    # =====================================================================
    # 家园排行
    # =====================================================================
    def _homestead_rank(self, player: dict) -> str:
        """家园排行 —— 本周金币产出排行。"""
        all_players = self.store._data.get("homestead_players", {})
        entries = []
        for qq, hs in all_players.items():
            self._homestead_update_weekly(hs)
            weekly = hs.get("weekly_coin", 0)
            level = hs.get("level", 1)
            if weekly > 0:
                entries.append({"qq": qq, "weekly": weekly, "level": level, "total": hs.get("total_coin_earned", 0)})
        entries.sort(key=lambda x: x["weekly"], reverse=True)
        top = entries[:data.HOMESTEAD_RANK_SIZE]
        lines = ["## 🏆 家园排行（本周金币产出）", ""]
        my_qq = str(player.get("qq", ""))
        for i, e in enumerate(top):
            medal = {0: "🥇", 1: "🥈", 2: "🥉"}.get(i, f"{i + 1}.")
            lines.append(f"{medal} {e['qq']} — 💰 {e['weekly']} 金（Lv{e['level']}）")
        # 我的排名
        my_weekly = hs.get("weekly_coin", 0)
        my_rank = next((i + 1 for i, e in enumerate(entries) if e["qq"] == my_qq), None)
        lines.append("")
        if my_rank:
            lines.append(f"📊 你的排名：第 {my_rank} 名（💰 {my_weekly} 金）")
        else:
            lines.append(f"📊 你本周暂无产出。快去建造家园！")
        # 奖励预告
        lines.append(f"🏅 周榜前 3 奖励：🥇{data.HOMESTEAD_RANK_REWARD_COIN[1]} 🥈{data.HOMESTEAD_RANK_REWARD_COIN[2]} 🥉{data.HOMESTEAD_RANK_REWARD_COIN[3]} 金币")
        return "\n".join(lines)

    def _homestead_total_rank(self, player: dict) -> str:
        """家园总排行 —— 累计金币产出排行。"""
        all_players = self.store._data.get("homestead_players", {})
        entries = []
        for qq, hs in all_players.items():
            total = hs.get("total_coin_earned", 0)
            level = hs.get("level", 1)
            if total > 0:
                entries.append({"qq": qq, "total": total, "level": level})
        entries.sort(key=lambda x: x["total"], reverse=True)
        top = entries[:data.HOMESTEAD_RANK_SIZE]
        lines = ["## 🏆 家园总排行（累计金币产出）", ""]
        for i, e in enumerate(top):
            medal = {0: "🥇", 1: "🥈", 2: "🥉"}.get(i, f"{i + 1}.")
            lines.append(f"{medal} {e['qq']} — 💰 {e['total']} 金（Lv{e['level']}）")
        my_qq = str(player.get("qq", ""))
        my_total = hs.get("total_coin_earned", 0)
        my_rank = next((i + 1 for i, e in enumerate(entries) if e["qq"] == my_qq), None)
        lines.append("")
        if my_rank:
            lines.append(f"📊 你的排名：第 {my_rank} 名（💰 {my_total} 金）")
        return "\n".join(lines)

    # ---- 家园辅助 ----
    def _homestead_prod_text(self, name: str, level: int) -> str:
        """格式化建筑产量文本。"""
        prod = data.homestead_production(name, level)
        parts = []
        if prod.get("coin"):
            parts.append(f"💰 {prod['coin']}/时")
        if prod.get("jifen"):
            parts.append(f"💎 {prod['jifen']}/时")
        if prod.get("exp"):
            parts.append(f"📖 {prod['exp']}/时")
        return " · ".join(parts) if parts else "—"

    def _homestead_update_weekly(self, hs: dict) -> None:
        """更新本周统计（周一重置）。"""
        today = time.strftime("%Y-%m-%d")
        import datetime as _dt
        weekday = _dt.datetime.now().weekday()
        week_key = f"{today}_{weekday}"
        if hs.get("weekly_date") != today and weekday == 0:
            # 周一重置
            if hs.get("weekly_date", "")[:10] != today[:10]:
                hs["weekly_coin"] = 0
        hs["weekly_date"] = today

    def _homestead_check_levelup(self, hs: dict) -> str:
        """检查并处理家园升级。"""
        level = hs["level"]
        exp = hs["exp"]
        need = data.homestead_exp_to_next(level)
        if exp >= need:
            hs["level"] += 1
            hs["exp"] = exp - need
            new_level = hs["level"]
            new_slots = data.homestead_slots(new_level)
            return f"🎉 **家园升级！** Lv{level} → Lv{new_level}（建筑位 {new_slots} 个）"
        return ""
