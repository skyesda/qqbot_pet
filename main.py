"""宠物乐园 —— AstrBot 群聊养成 / 对战插件。

参考某 QQ 群"宠物联盟"玩法复刻：砸蛋抽宠、宠物商城、属性克制对战、繁殖姻缘、
进化飞升渡劫、天赋觉醒、炼丹、神器/秘技、副本、剧情任务、跨群挑战、排行神榜等。

指令均为无前缀中文指令（与参考一致），通过监听全部消息后自行解析路由。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import random
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
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

from .petpark import card_theme, data, images, pet as petmod
from .petpark.ai_router import AIRouter
from .petpark.store import PetStore
from .petpark.boardgames import BoardGames, COMMANDS as BOARD_COMMANDS

# 中元节活动（独立模块）。缺失/损坏时降级为关闭，不影响宠物乐园主程序。
try:
    from .petpark.zhongyuan import COMMANDS as _ZY_COMMANDS, ZhongyuanActivity
except Exception as _zy_err:  # pragma: no cover
    _ZY_COMMANDS: set[str] = set()
    ZhongyuanActivity = None
    logger.warning("[petpark] 中元活动模块加载失败，已自动关闭：%s", _zy_err)

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

# 强制绑定QQ模式下，未绑定用户仍可使用的指令（绑定相关 + 菜单/帮助）
_BIND_ALWAYS_ALLOWED = {
    "绑定QQ", "验证码", "换绑QQ", "解绑QQ", "绑定教程",
    "宠物乐园", "查看说明",
}

# 本插件识别的指令首词（日常活动为整句匹配，见 data.DAILY_ACTIONS）。
KNOWN_COMMANDS = {
    *BOARD_COMMANDS,
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
    "宠物乐园",
    "管理菜单",
    "官方网站",
    "我的信息",
    "个人信息",
    "我的奖品",
    "口令抽奖",
    "绑定QQ",
    "验证码",
    "换绑QQ",
    "解绑QQ",
    "绑定教程",
    "签到",
    "兑换",
    "卡密兑换",
    "修炼卡",
    "我要氪金",
    "查看说明",
    # 群授权
    "授权",
    "授权状态",
    "授权本群",
    # 群绑定（跨机器人数据互通）
    "绑定群",
    "解绑群",
    "群映射",
    "群ID",
    # 群管理（禁言 / 全体禁言 / 踢人，仅群主/管理员或插件管理员可用）
    "禁言",
    "解除禁言",
    "全体禁言",
    "踢出",
    "移除成员",
    "踢人",
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
    "钻石商城",
    "秘技商城",
    "神器商城",
    "宠物市场",
    "宠物专域",
    # 获取宠物
    "砸蛋",
    "砸蛋十连",
    "十连砸蛋",
    "购买宠物",
    "购买市场",
    "购买品质卡",
    "购买变种卡",
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
    "炼化宠物",
    "锁定宠物",
    "解锁宠物",
    "宠物改名",
    "宠物变性",
    "宠物复活",
    "宠物状态",
    "喂食",
    # 成长
    "一键升级宠物",
    "开启自动升级",
    "关闭自动升级",
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
    "碎片转卡",
    "碎片合成",
    "碎片兑换",
    "一键合成品质碎片",
    "一键碎片兑换",
    "一键合成品质卡",
    "一键卡合成",
    "批量碎片转卡",
    "批量卡合成",
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
    # 坐骑系统
    "坐骑系统",
    "开启坐骑系统",
    "关闭坐骑系统",
    "开启坐骑入场提示",
    "关闭坐骑入场提示",
    "开启坐骑离场提示",
    "关闭坐骑离场提示",
    "坐骑列表",
    "我的坐骑",
    "骑乘坐骑",
    "赠送坐骑",
    "丢弃坐骑",
    "坐骑市场",
    "购买坐骑",
    "坐骑图鉴",
    "定制坐骑",
    "坐骑升级",
    "升级坐骑",
    # 点歌（QQ官方语音）
    "点歌",
    "下一页",
    "上一页",
    "选歌",
}

# 合并中元活动指令（由独立模块动态提供，避免在 KNOWN_COMMANDS 手写两份清单）
KNOWN_COMMANDS |= _ZY_COMMANDS

# 生辰盛典（独立庆典活动）：生日抽奖 / 生日快乐 / 活动菜单
KNOWN_COMMANDS |= {"生辰活动", "生日抽奖", "生日快乐"}

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
    "砸蛋十连",
    "十连砸蛋",
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
    "设为无限服",
    "设为官方服",
    "绑定群",
    "解绑群",
    "群映射",
    "群ID",
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

# 中元活动为群聊玩法，网页端一并屏蔽
WEB_BLOCKED_COMMANDS |= _ZY_COMMANDS


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
    # 后台任务引用均为实例属性（self.*），统一由 terminate() 逐个取消。
    # 不用类属性：热重载（importlib.reload 重建类）后类级引用会丢成 None，
    # 导致 __init__ 的取消判断拿不到旧任务 → 旧任务泄漏，与新任务并存重复触发。
    _BG_TASK_REFS = (
        "_board_clock_task_ref",
        "_auto_cultivation_task_ref",
        "_bank_interest_task_ref",
        "_group_auto_approve_task_ref",
        "_lottery_task_ref",
        "_custom_push_task_ref",
        "_celebrate_task_ref",
        "_mount_task_ref",
    )

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
        # 坐骑 GIF 资产同步到 custom_images_dir（经 /custom_images 静态路由动画展示）
        self._sync_mount_gifs()
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
        # 强制绑定QQ：开启后未绑定用户禁止游玩宠物乐园（安全阀，可在后台关闭）
        self.require_qq_bind = bool(self.config.get("require_qq_bind", True))
        self.welcome_template = str(self.config.get("welcome_template", "") or "") or (
            "## 👋 欢迎新成员\n欢迎 @{{member}} 加入本群！"
        )
        self.leave_template = str(self.config.get("leave_template", "") or "") or (
            "## 👋 成员退群\n成员 @{{member}} 已离开本群。"
        )
        # ===== 点歌（QQ官方语音）=====
        self.song_enabled = bool(self.config.get("song_enabled", True))
        self.song_max_results = max(1, int(self.config.get("song_max_results", 50)))
        self.song_page_size = max(1, int(self.config.get("song_page_size", 10)))
        self.alapi_token = str(self.config.get("alapi_token") or "").strip()
        if not self.alapi_token:
            # SkyeBot 运行时配置不合并 schema 默认值：新字段在已保存配置中缺失时兜底用默认密钥
            self.alapi_token = self.SONG_DEFAULT_ALAPI_TOKEN
            logger.warning("[petpark] 点歌 alapi_token 未配置，使用默认密钥")
        self.silk_encoder_path = str(self.config.get("silk_encoder_path", "") or "").strip()
        self.silk_url_base = str(self.config.get("silk_url_base") or "").strip().rstrip("/")
        if not self.silk_url_base:
            # 同 alapi_token：运行时配置不合并 schema 默认值，兜底用公网 webadmin 地址
            self.silk_url_base = self.SONG_DEFAULT_SILK_URL_BASE
            logger.warning("[petpark] 点歌 silk_url_base 未配置，使用默认公网地址")
        # 点歌会话：{group_id: {"keyword", "songs", "page", "ts"}}（15 分钟过期）
        self._song_sessions: dict[str, dict] = {}
        # silk 临时目录：webadmin 从这里对外提供 QQ 可拉取的 silk 文件
        self.song_silk_dir = data_dir / "song_silk"
        self.song_silk_dir.mkdir(parents=True, exist_ok=True)
        if not self.silk_encoder_path:
            _silk_bin = Path(__file__).parent / "bin" / "silk_v3_encoder"
            self.silk_encoder_path = str(_silk_bin) if _silk_bin.exists() else ""
        # 群昵称缓存：{group_id: {member_openid: 群昵称}}
        self._nick_cache: dict[str, dict[str, str]] = {}
        # 群成员角色缓存：{group_id: {member_openid: (角色, 时间戳)}}
        self._role_cache: dict[str, dict[str, tuple]] = {}
        # 成员详情接口是否可用：非白名单机器人访问会返回 11253，识别后不再重试
        self._member_api_ok = True
        # 专属管理网站（卡密生成 + 数据增删改查）
        self._web = None
        # 全服广播任务引用，防止被 GC
        self._broadcast_tasks: set = set()
        # 宠物摸金当局运行时状态（持久化到 store，插件重载后自动恢复）
        self._tomb_sessions: dict[str, dict] = self.store.load_tomb_sessions()
        # 扫雷当局运行时状态（内存中，不持久化，按 QQ 一人一局）
        self._ms_sessions: dict[str, dict] = {}
        self._board_games = BoardGames(
            data_dir / "boardgames.json", self.store.custom_images_dir,
            self._tomb_image_url, self._display_uid, self._find_board_target,
        )
        self._board_clock_task_ref = asyncio.create_task(self._board_clock_loop())
        # 群消息滚动缓存：群ID\x1f发送者 → deque[(message_id, 时间戳)]，供撤回指令用
        self._group_msg_log: dict[str, deque] = {}
        # 摸金双排组队状态（持久化到 store，插件重载后自动恢复）
        self._tomb_coop_teams, self._tomb_coop_index = self.store.load_tomb_coops()
        # QQ 绑定待验证码（内存中，platform_id -> {code, qq, expires_at, sent_at}）
        self._pending_qq_bind: dict[str, dict] = {}
        # 群绑定待确认（内存中，token -> {"group": 规范群 openid, "expires": 时间戳}）
        self._pending_group_bind: dict[str, dict] = {}
        # AI 意图路由：自然语言 → 标准指令（使用 AstrBot 当前启用的 LLM Provider）
        self._ai_router = AIRouter(
            context,
            enabled=bool(self.config.get("ai_router_enabled", True)),
            timeout=float(self.config.get("ai_router_timeout", 20)),
            provider_id=str(self.config.get("ai_router_provider_id", "")),
        )
        # 中元节活动（独立模块：独立数据 zhongyuan.json、独立开关、独立后台循环）
        self.zhongyuan = None
        self._zy_commands: set[str] = set()
        if ZhongyuanActivity is not None:
            try:
                self.zhongyuan = ZhongyuanActivity(self, data_dir)
                self._zy_commands = self.zhongyuan.commands()
            except Exception:
                logger.exception("[petpark] 中元活动初始化失败")
                self.zhongyuan = None
        if bool(self.config.get("web_enabled", True)):
            self._start_web_admin()
        self._patch_qqofficial_message_extensions()
        # 启动后台循环：任务引用存到 self（实例属性）。重载插件时 terminate()
        # 会先取消旧实例的全部后台任务再走到这里，避免旧任务和新任务并存、重复触发。
        self._auto_cultivation_task_ref = asyncio.create_task(self._auto_cultivation_loop())
        self._bank_interest_task_ref = asyncio.create_task(self._bank_interest_loop())
        self._group_auto_approve_task_ref = asyncio.create_task(self._group_auto_approve_loop())
        self._lottery_task_ref = asyncio.create_task(self._lottery_loop())
        self._custom_push_task_ref = asyncio.create_task(self._custom_push_loop())
        self._celebrate_task_ref = asyncio.create_task(self._celebrate_loop())
        self._mount_task_ref = asyncio.create_task(self._mount_loop())
        if self.zhongyuan is not None:
            self.zhongyuan.start()

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
            zhongyuan=getattr(self, "zhongyuan", None),
            silk_dir=self.song_silk_dir,
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
                    # 自动升级（不发送消息，静默处理；玩家可关闭）
                    if player.get("auto_level", True):
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
                [("📜 宠物乐园", "宠物乐园"), ("🎁 每日签到", "签到")],
                [("💎 我要氪金", "我要氪金")],
                [("🌐 官方网站", "官方网站")],
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
        if text in {"宠物乐园", "管理菜单"}:
            return self._main_menu_keyboard()
        if text.split() and text.split()[0] in BOARD_COMMANDS:
            board_kind = "军棋" if "军棋" in text else "象棋" if "象棋" in text else "五子棋"
            return self._build_qq_keyboard([
                [("⚫ 五子棋", "五子棋"), ("🎴 中国象棋", "中国象棋"), ("🚩 军棋", "军棋")],
                [(f"{board_kind}·简单", f"{board_kind}单人 1"), (f"{board_kind}·普通", f"{board_kind}单人 2")],
                [(f"{board_kind}·困难", f"{board_kind}单人 3"), (f"{board_kind}·地狱", f"{board_kind}单人 4")],
                [("查看棋局", "棋局"), ("接受邀请", "接受棋局")],
                [("玩法帮助", "棋类帮助"), ("棋局统计", "棋局统计")],
            ])
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
        # @提及统一替换为对方用户ID：所有「用户ID/QQ号」参数位都支持直接 @ 对方
        # （dispatch 内也会再做一次同样处理，此处提前替换是为让 AI 意图路由
        #  拿到干净文本；对禁言/撤回指令无影响——它们解析的是 raw.mentions）
        text = _MENTION_RE.sub(lambda m: f" {m.group(1)} ", text).strip()
        # 兼容 QQ 官方指令面板：点击面板项时客户端会自动补 `/` 前缀（如 `/签到`），
        # 剥掉开头的斜杠使 `/签到` 等价于 `签到`，面板指令才能被识别；同时兼容用户手输的 `/指令 参数`。
        text = text.lstrip("/")
        if not text:
            return
        qq = str(event.get_sender_id())
        raw_group_id = self._group_id(event)
        # 跨机器人数据互通：把本机器人视角的群 openid 解析为规范群 ID，
        # 使授权/群设置等按同一逻辑群共享（绑定群后生效）。
        group_id = self.store.resolve_group(raw_group_id)
        # 记录群聊统一消息来源，便于 Boss 击杀/复活时向授权群主动推送
        if self._is_group(group_id):
            # 趁成员还在时缓存群昵称：退群/进群推送要 @昵称，但成员离群后
            # 成员详情接口常常取不到 username，只能靠消息时代提前缓存兜底。
            # 同时缓存原始 openid 与解析后群 ID 两种键，兼配绑定群场景。
            sender_nick = self._sender_name(event)
            if sender_nick:
                now = time.time()
                self._nick_cache.setdefault(raw_group_id, {})[qq] = (sender_nick, now)
                self._nick_cache.setdefault(group_id, {})[qq] = (sender_nick, now)
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
            elif re.match(r"^(踢出|移除成员|踢人)(\s|<@|$)", text):
                reply = await self._cmd_kick(event, qq, group_id, text)
            else:
                reply = self.dispatch(event, qq, group_id, text)
        except Exception as e:  # 保证插件不因单条消息崩溃
            logger.exception("[petpark] 处理指令出错")
            reply = f"宠物乐园处理出错：{e}"
        # AI 意图路由兜底：精确指令未命中时，尝试把自然语言翻译为标准指令
        effective_text = text
        if reply is None:
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
        # 坐骑自动入场：任何群消息都可能触发（拥有坐骑即骑乘登场，入场发一次奖励）。
        if self._is_group(group_id):
            try:
                await self._mount_enter_tick(qq, group_id)
            except Exception:
                logger.exception("[petpark] 坐骑入场检测出错")

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
            # 真正的艾特：QQ 官方消息内嵌 <qqbot-at-user id="openid" />，客户端
            # 会渲染为可点击的@成员（旧 <@openid> 已弃用；Comp.At 组件在 QQ 官方
            # 适配器下被忽略）。用 \n\n 分隔，避免 Markdown 吞掉单个换行。
            reply = f'<qqbot-at-user id="{qq}" />\n\n{reply}'
            res = event.plain_result(reply)
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
        """取发送者显示名（退群/进群推送 @昵称 用）。

        优先框架标准 get_sender_name()；再探测常见属性/嵌套容器，尽可能是组名片或昵称。
        拿不到时返回空串（调用方忽略，绝不抛异常）。
        """
        getter = getattr(event, "get_sender_name", None)
        if callable(getter):
            try:
                v = getter()
                if v:
                    return str(v)
            except Exception:
                pass
        # 探测 event / event.message / event.message_obj 上的常见名字字段
        for container in (event, getattr(event, "message", None), getattr(event, "message_obj", None)):
            if container is None:
                continue
            if isinstance(container, dict):
                for attr in ("sender_name", "nickname", "member_name", "username"):
                    v = container.get(attr)
                    if v:
                        return str(v)
                continue
            for attr in ("sender_name", "nickname", "member_name"):
                v = getattr(container, attr, None)
                if callable(v):
                    try:
                        v = v()
                    except Exception:
                        continue
                if v:
                    return str(v)
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
        # 先取消全部后台循环：重载/停机时若不清理，旧任务会带着旧 store 一直跑，
        # 与新实例并存导致重复触发（如某广播被对全群广播两遍）。
        for name in self._BG_TASK_REFS:
            task = getattr(self, name, None)
            if task is not None and not task.done():
                try:
                    task.cancel()
                except Exception:
                    pass
        # 中元活动独立模块：取消其后台循环并落盘
        if self.zhongyuan is not None:
            try:
                await self.zhongyuan.terminate()
            except Exception:
                logger.exception("[petpark] 中元活动终止出错")
        # 给被取消的任务处理 CancelledError 的机会，再落盘
        await asyncio.sleep(0)
        await self.store.save()
        if self._web is not None:
            await self._web.stop()

    async def _board_clock_loop(self):
        while True:
            try:
                await asyncio.sleep(5)
                self._board_games.expire()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[petpark] 棋局超时结算失败")

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
            return None, f"❌ 用户 `{self._display_uid(qq)}` 在本群不存在（对方需先在本群参与宠物乐园）。"
        return tp, None

    def _find_board_target(self, group_id: str, token: str):
        """Chess identities and invitations span all groups."""
        user = self._resolve_user_token(token)
        target = next((p for p in self.store.all_players().values() if str(p.get("qq", "")) == user), None)
        if target is None:
            return None, "找不到该玩家，请对方先在任意群参与宠物乐园。"
        return target, None

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

    def _check_transfer_limit(
        self, sender: dict, target: dict, group_id: str, count: int,
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
        6. 大管理员（admins 白名单）豁免以上所有限制与税费
        7. 无限服：以上所有限制与税费全部去除（不收税、不限次数、不限单次数量）
        """
        # 大管理员豁免：不限次数、不交税、不计次数（普通玩家不受影响）
        sender_id = str(sender.get("qq", ""))
        if (
            sender_id in self.admins
            or str(self.store.get_bound_qq(sender_id)) in self.admins
        ):
            return None, 0.0
        qq1 = str(sender.get("qq", ""))
        qq2 = str(target.get("qq", ""))
        if qq1 == qq2:
            return "不能转让/赠送给自己。", 0.0
        # 无限服：转让限制与税费全部去除（不走每日次数、单次数量、税率逻辑）
        if self._group_is_infinite(group_id):
            return None, 0.0

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
        # 清空背包，只保留长期养成投入（品质卡/定制卡/宠物卡/品质碎片/自动修炼卡）
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
        # 重生后清空剧情任务记录（已完成 + 进行中），任务可重新完成
        quest_cleared = len(player.get("quest_done", [])) + len(player.get("quests", {}))
        player.pop("quest_done", None)
        player.pop("quests", None)
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
        if quest_cleared:
            lines.append(f"**任务重置：** 剧情任务 ×{quest_cleared} 已清空，重生后可重新完成")
        if dropped:
            lines.append(f"**脱落：** {'、'.join(dropped)}（已入背包）")
        if cleared_count > 0:
            lines.append(f"**清空：** 背包物品 ×{cleared_count}（品质卡/定制卡/宠物卡/碎片/自动修炼卡已保留）")
        lines.append("")
        lines.append(slot_msg)
        lines.append("> 🐣 宠物获得新生，重新踏上成长之路！")
        # 全群播报重生祝贺（优先用绑定QQ号，否则用用户ID）
        pid = str(player.get("qq", ""))
        bound_qq = self.store.get_bound_qq(pid)
        who = f"QQ `{bound_qq}`" if bound_qq else f"`{pid}`"
        nickname = p.get("nickname", "?")
        # 无限服：退出跨群共享层，重生不公布到其它群（本群玩家仍会收到上面完整结果）
        if not self._group_is_infinite(str(player.get("group", ""))):
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
        """大管理员 = 仅配置白名单「admins」中的身份（super admin）。

        绝不因 QQ 官方群的群主/群管理员（author.member_role）等群身份授予超级管理权限，
        否则群里任一被设为管理员的人都能无上限铸造钻石/金币（并间接无限加币），属权限漏洞。
        QQ 群主/群管理的「群管理」操作（如撤回消息）走 _is_group_staff，与本判定无关。
        """
        sender_id = str(event.get_sender_id())
        if sender_id in self.admins:
            return True
        # 白名单内可能是绑定QQ号，用绑定QQ兜底反查（与 _check_transfer_limit 同一口径）
        return str(self.store.get_bound_qq(sender_id)) in self.admins

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
        ok, why = await self._is_group_staff(event, qq, group_id, api)
        if not ok:
            return why
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
        if not self._member_api_ok:
            return ""
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
        except Exception as e:
            # 非白名单机器人访问成员详情接口报 11253（应用无接口访问权限），
            # 识别后置为不可用并停止重试，避免每来一个新成员都刷一条错误日志。
            if "11253" in str(e) or "40012010" in str(e):
                self._member_api_ok = False
            nick = ""
        cache[member] = (nick, time.time())
        return nick

    async def _member_role(self, api, group_id: str, member: str) -> str:
        """查询成员在群内的身份角色（owner/admin/member），带缓存。

        这是「真正的群主/管理员/群友身份识别」的统一入口：优先返回 QQ 官方
        成员详情接口的 member_role；接口不可用或失败时返回空串（调用方据此
        拒绝或放行）。缓存规则同 _member_nick：非空长期有效，空值 10 分钟不重试。
        """
        member = str(member or "")
        group_id = str(group_id or "")
        if not member or not group_id or api is None:
            return ""
        cache = self._role_cache.setdefault(group_id, {})
        hit = cache.get(member)
        if isinstance(hit, tuple):
            role, ts = hit
            if role or (time.time() - ts) < 600:
                return role
        if not self._member_api_ok:
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
                    group_openid=group_id,
                    member_openid=member,
                )
            )
            role = str((info or {}).get("member_role", "") or "")
        except Exception as e:
            if "11253" in str(e) or "40012010" in str(e):
                self._member_api_ok = False
            role = ""
        cache[member] = (role, time.time())
        return role

    async def _is_group_staff(self, event, qq: str, group_id: str, api) -> tuple[bool, str]:
        """判断发送者是否为群主/管理员（含插件管理员白名单）。

        返回 (是否通过, 未通过原因文本)。通过优先级：插件管理员白名单 >
        群消息事件自带的 member_role（owner/admin）> 成员详情接口（兜底）。

        事件身份（event.sender_role）来自群消息 payload 的 author.member_role，
        零额外 API 请求；旧客户端/非群消息场景无该字段时，回退查成员详情接口。
        """
        if str(qq) in self.admins:
            return True, ""
        # 群消息事件已携带身份（框架透传 author.member_role）
        role = str(getattr(event, "sender_role", "") or "") or ""
        if role in ("owner", "admin", "member"):
            return role in ("owner", "admin"), ""
        if api is None:
            return False, "❌ 当前平台不支持群管理操作（需 QQ 官方机器人）。"
        role = await self._member_role(api, group_id, qq)
        if role in ("owner", "admin"):
            return True, ""
        if role == "":
            return (
                False,
                "❌ 无法校验你的群身份：查询群成员接口失败。\n"
                "> 可能原因：机器人未在 QQ 开放平台开通「查询群成员信息」接口权限。",
            )
        return False, "❌ 仅群主或管理员可以执行该操作。"

    def _display_uid(self, pid: str) -> str:
        """展示用户：优先已绑定QQ号，未绑定则返回平台用户ID(openid)。"""
        return self.store.get_bound_qq(pid) or str(pid)

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
        ok, why = await self._is_group_staff(event, qq, group_id, api)
        if not ok:
            return why

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
                f"## 🔇 群管理\n已禁言成员 **{self._display_uid(target)}** {seconds // 60} 分钟，"
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
            return f"## 🔇 群管理\n已解除对成员 **{self._display_uid(target)}** 的禁言。"
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

    async def _cmd_kick(self, event, qq: str, group_id: str, text: str) -> str | None:
        """「踢出 @成员」：把被 @ 的成员移出本群（群主/管理员可用）。

        走 QQ 官方批量移除成员接口 POST /v2/groups/{group_openid}/batch_remove_members；
        仅群主/管理员（含插件管理员白名单）可用；机器人需为群管理员且目标为普通成员。
        """
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
        try:
            if not self.store.get_group(group_id).get("enabled", False):
                return "❌ 本群未开启宠物乐园，无法使用群管理指令。"
        except Exception:
            pass
        # 解析被 @ 的目标成员
        body = re.sub(r"^(踢出|移除成员|踢人)", "", text, count=1).strip()
        targets: list[str] = []
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        for m in getattr(raw, "mentions", None) or []:
            mid = str(getattr(m, "id", "") or "")
            if mid and not getattr(m, "is_you", False) and mid not in targets:
                targets.append(mid)
        for mid in _MENTION_RE.findall(body):
            if mid not in targets:
                targets.append(mid)
        if not targets:
            return (
                "## 👢 踢出成员\n"
                "用法：`踢出 @成员`（可一次 @ 多名，最多 20 名）\n"
                "仅群主/管理员可用；机器人需为群管理员，且只能移出普通成员。"
            )
        # 权限：插件管理员白名单直接放行，否则查询群成员角色
        ok, why = await self._is_group_staff(event, qq, group_id, api)
        if not ok:
            return why
        targets = targets[:20]
        try:
            resp = await api._http.request(
                Route(
                    "POST",
                    "/v2/groups/{group_openid}/batch_remove_members",
                    group_openid=group_id,
                ),
                json={
                    "member_openids": targets,
                    "add_to_member_blacklist": False,
                },
            )
        except Exception as e:
            logger.warning(f"[petpark] 踢出成员 {targets} 失败：{e}")
            err = str(e)
            if "11253" in err or "40012010" in err:
                return (
                    "❌ 踢出失败：当前机器人（appid）尚未开通「群成员批量移除」能力。\n"
                    "> 请到 QQ 开放平台为该机器人的「群管理」权限点申请开通，审核通过后即可使用。"
                )
            return (
                "❌ 踢出失败（机器人需为群管理员，且只能移出普通成员，"
                f"不能移出群主/管理员/机器人）。\n> 接口返回：{err}"
            )
        fail = ((resp or {}).get("add_to_member_blacklist_fail_openids") or [])
        shown = "、".join(self._display_uid(t) for t in targets)
        result = f"## 👢 群管理\n已移出成员 **{shown}**"
        if fail:
            result += f"\n（其中 {len(fail)} 名加入黑名单失败，不影响移出）"
        result += "。"
        logger.info(f"[petpark] 群 {group_id} 内 {qq} 踢出成员 {targets}")
        return result

    async def on_group_member_add(self, data: dict) -> None:
        """群成员加入事件：按配置推送欢迎语（@群昵称）。"""
        if not self.welcome_push:
            return
        gid = str(data.get("group_openid", "") or "")
        member = str(data.get("member_openid", "") or "")
        if not gid or not member:
            return
        # 群昵称优先级：事件自带 username（个别平台有）-> 缓存 -> 已绑定QQ号 -> openid
        nick = (
            str(data.get("username", "") or "")
            or (await self._member_nick(gid, member))
            or self.store.get_bound_qq(member)
            or member
        )
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
        # 群昵称优先级：事件自带 username（个别平台有）-> 缓存 -> 已绑定QQ号 -> openid
        nick = (
            str(data.get("username", "") or "")
            or (await self._member_nick(gid, member))
            or self.store.get_bound_qq(member)
            or member
        )
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
        # 口令抽奖：口令本身即参与指令（全群共享，全局唯一口令）。在 KNOWN_COMMANDS
        # 过滤之前匹配，输入完整口令即可登记报名。
        lottery = self.store.lottery()
        if lottery and lottery.get("enabled") and not lottery.get("drawn") \
                and text.strip() == str(lottery.get("password", "")):
            return self._register_lottery_claim(qq, group_id, lottery)
        # @提及统一替换为对方用户ID：所有「用户ID/QQ号」参数位
        # （赠送/转让/PK/拜访/加金币/任命小管理等）都支持直接 @ 对方。
        # 替换时两侧补空格，兼容「赠送<@!xx>100」这类@与文字粘连的写法。
        text = _MENTION_RE.sub(lambda m: f" {m.group(1)} ", text)
        # 兼容 QQ 官方指令面板：点击面板项客户端会自动补 `/` 前缀（如 `/签到`），
        # 剥掉开头的斜杠使 `/签到` 等价于 `签到`。
        text = text.lstrip("/")
        tokens = text.split()
        cmd = tokens[0]
        board_start = re.fullmatch(r"(开始(?:五子棋|中国象棋|象棋|军棋)|(?:五子棋|中国象棋|象棋|军棋)单人)([1-4])", cmd)
        if board_start:
            tokens = [board_start.group(1), board_start.group(2)] + tokens[1:]
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

        # ---- 点歌（QQ官方语音）：搜索 / 翻页 / 按序号选歌（后台异步处理）----
        if cmd in ("点歌", "下一页", "上一页", "选歌"):
            return self._song_dispatch(qq, group_id, cmd, tokens)

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
        if cmd in ("设为无限服", "设为官方服"):
            return self._set_server_type(event, group_id, cmd)

        # ---- 群绑定（跨机器人数据互通）----
        if cmd == "绑定群":
            return self._cmd_group_bind(event, group_id, qq, tokens)
        if cmd == "解绑群":
            return self._cmd_group_unbind(event, group_id, qq)
        if cmd in ("群映射", "群ID"):
            return self._cmd_group_map(event, group_id)

        # ---- 群授权校验：严格模式，所有群聊都需有效授权才能使用 ----
        if self._is_group(group_id) and not self._is_group_authorized(group_id):
            return self._auth_blocked_text()

        # 群未开启则不响应任何宠物指令
        if not group.get("enabled", True):
            return None

        # ---- 强制绑定QQ：未绑定用户禁止游玩（含中元），仅放行绑定相关与菜单/帮助 ----
        # 管理员（配置白名单 / 群主群管）不拦截，避免把运营者锁死在管理功能之外
        if not self._is_admin(event):
            bb = self._qq_bind_block(qq, cmd)
            if bb:
                return bb

        # ---- 中元活动（独立模块）：已授权且宠物乐园开启的群路由给活动引擎 ----
        if self.zhongyuan is not None and cmd in self._zy_commands:
            return self.zhongyuan.dispatch(event, qq, group_id, text)

        # ---- 「阴气缠身」锁定：中元副本失败后禁止一切宠物指令，仅可参与中元活动 ----
        if self.zhongyuan is not None:
            block = self.zhongyuan.yin_lock_block(qq, group_id)
            if block:
                return block

        # ---- 查看类型 / 说明（信息查询，无需有宠物）----
        info = self._handle_info(cmd, tokens)
        if info is not None:
            return info

        # ---- 商城（无需宠物）----
        if cmd == "宠物商城":
            return self._shop_text("宠物商城")
        if cmd in ("道具商城", "积分商城"):
            return self._shop_text("道具商城")
        if cmd == "钻石商城":
            return self._shop_text("钻石商城")
        if cmd == "秘技商城":
            return self._shop_text("秘技商城")
        if cmd == "神器商城":
            return self._shop_text("神器商城")
        if cmd in ("宠物市场", "宠物专域"):
            return self._pet_market_text()

        player = self.store.get_player(qq, group_id)
        player["group"] = group_id
        self._track_activity(player)

        # 银行逾期冻结检查（放行查看/还款类指令）
        _bank_allow = {"银行信息", "银行还款", "宠物乐园",
                        "管理菜单", "官方网站", "我的信息", "个人信息", "签到", "兑换", "卡密兑换",
                        "授权状态", "授权", "设为无限服", "设为官方服", "查看说明", "银行信息",
                        "重生", "购买重生宝石", "确认重生", "祭奠",
                        "宠物列表", "查看所有宠物", "宠物信息", "切换宠物",
                        "坐骑系统", "坐骑市场", "坐骑图鉴", "我的坐骑", "坐骑列表",
                        "绑定QQ", "验证码", "换绑QQ", "解绑QQ", "绑定教程",
                        "我的奖品", "口令抽奖",
                        "生辰活动", "生日抽奖", "生日快乐"}
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

        # ---- 坐骑系统 ----
        if cmd == "坐骑系统":
            return self._mount_help()
        if cmd in ("开启坐骑系统", "关闭坐骑系统"):
            return self._mount_group_toggle(group_id, cmd, event)
        if cmd in ("开启坐骑入场提示", "关闭坐骑入场提示"):
            return self._mount_notify(player, "enter", cmd)
        if cmd in ("开启坐骑离场提示", "关闭坐骑离场提示"):
            return self._mount_notify(player, "leave", cmd)
        if cmd == "坐骑列表":
            return self._mount_list(player)
        if cmd == "我的坐骑":
            return self._my_mounts(player)
        if cmd == "骑乘坐骑":
            return self._mount_ride(player, group_id, tokens)
        if cmd == "赠送坐骑":
            return self._gift_mount(player, group_id, tokens)
        if cmd == "丢弃坐骑":
            return self._mount_discard(player, tokens)
        if cmd == "坐骑市场":
            return self._mount_market_text()
        if cmd == "购买坐骑":
            return self._buy_mount(player, tokens)
        if cmd == "坐骑图鉴":
            return self._mount_codex(player, tokens)
        if cmd == "定制坐骑":
            return self._mount_custom(player)
        if cmd in ("坐骑升级", "升级坐骑"):
            return self._mount_upgrade(player, tokens)

        # ---- 我的信息（唯一展示 ID / 群 / 金币 / 积分 的地方）----
        if cmd in ("我的信息", "个人信息"):
            return self._my_info(player, group_id, event)

        # ---- 口令抽奖 / 我的奖品（全群共享，以用户 id 为主键）----
        if cmd == "口令抽奖":
            return self._handle_lottery_status()
        if cmd == "我的奖品":
            return self._handle_my_prizes(player, group_id, tokens)

        # ---- QQ 绑定（邮箱验证码 / 大管理员代绑）----
        if cmd in ("绑定QQ", "换绑QQ"):
            # 大管理员代绑：绑定QQ @目标 目标QQ号 / 换绑QQ 用户ID 目标QQ号（免邮箱验证）
            if len(tokens) >= 3:
                return self._admin_qq_bind(event, group_id, cmd, tokens)
            return self._bind_qq(player, tokens, rebind=(cmd == "换绑QQ"))
        if cmd == "验证码":
            return self._verify_qq_code(player, tokens)
        if cmd == "解绑QQ":
            return self._unbind_qq(player)
        if cmd == "绑定教程":
            return self._bind_tutorial()

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
        if cmd == "修炼卡":
            return self._redeem_auto_cultivation_card(player, group_id, qq, tokens)
        if cmd == "我要氪金":
            return self._pay_link()

        # ---- 获取宠物 ----
        if cmd == "砸蛋":
            return self._smash_egg(player)
        if cmd in ("砸蛋十连", "十连砸蛋"):
            return self._smash_ten(player)
        if cmd in ("购买市场", "购买品质卡", "购买变种卡", "购买宠物"):
            return self._buy_market_item(player, tokens)
        if cmd in ("合成卡", "合成品质卡", "卡合成"):
            return self._compose_quality_card(player, tokens)
        if cmd in ("碎片转卡", "碎片合成", "碎片兑换"):
            return self._exchange_fragment(player, tokens)
        if cmd in ("一键合成品质碎片", "一键碎片兑换", "批量碎片转卡"):
            return self._batch_exchange_fragments(player)
        if cmd in ("一键合成品质卡", "一键卡合成", "批量卡合成"):
            return self._batch_compose_cards(player)
        if cmd in ("赠送金币", "赠送积分", "赠送钻石"):
            return self._gift_currency(player, group_id, cmd, tokens)

        # ---- 背包 / 商城购买 / 物品 ----
        if cmd in ("查看背包", "背包图"):
            md = self._render_bag_image(player)
            if md:
                return ("我的背包", md)
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
            if len(tokens) > 1:
                # 宠物复活 @他人 / 用户ID → 复活指定玩家宠物（需『起死回生』天赋，@提及自动转为ID）
                return self._talent_revive(player, group_id, tokens)
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
        if cmd in ("开启自动升级", "关闭自动升级"):
            return self._toggle_auto_level(player, cmd == "开启自动升级")
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
            if len(tokens) >= 2:
                name = tokens[1]
                if name not in player.get("quests", {}):
                    return f"你尚未领取『{name}』。"
                player["quests"].pop(name, None)
                return f"已取消剧情任务『{name}』。"
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
        if cmd in BOARD_COMMANDS:
            return self._board_games.handle(group_id, str(player.get("qq", qq)), tokens)

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
        if cmd == "炼化宠物":
            return self._refine_pet(player, tokens)

        # ---- 婚恋 ----
        love = self._handle_love(player, group_id, cmd, tokens)
        if love is not None:
            return love

        # ---- 限时活动 ----
        event_reply = self._handle_event(player, group_id, cmd, text, tokens)
        if event_reply is not None:
            return event_reply

        # ---- 生辰盛典（独立庆典活动：抽奖 + 奖池瓜分）----
        cel_reply = self._handle_celebrate(player, group_id, qq, cmd, tokens)
        if cel_reply is not None:
            return cel_reply

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

    # =====================================================================
    # 生辰盛典：每日多次定时开奖箱（大奖） + 奖池瓜分（货币，30 分钟冷却）
    # =====================================================================
    def _handle_celebrate(self, player, group_id, qq, cmd, tokens):
        """生辰盛典指令分发入口：`生辰活动`（菜单）/ `生辰抽奖`（报名开奖箱）/ `生辰瓜分`（瓜分奖池）。"""
        cel = self.store._data.get("celebrate")
        if not cel:
            return None
        gacha = cel.get("gacha", {})
        pool = cel.get("pool", {})
        cmds = {gacha.get("cmd"), gacha.get("menu_cmd"), pool.get("cmd")}
        cmds.discard(None)
        if cmd not in cmds:
            return None
        if not cel.get("enabled"):
            return "🎂 **生辰盛典** 尚未开启，敬请期待！"
        if self._group_is_infinite(group_id):
            return "⚠️ 本群为无限服，不参与官方服全局生辰盛典。"
        now = int(time.time())
        start = int(cel.get("start_at") or 0)
        end = int(cel.get("end_at") or 0)
        if not (start <= now <= end):
            if now < start:
                return "🎂 **生辰盛典** 尚未开启，敬请期待！"
            return "🎂 **生辰盛典** 已收官，感谢参与！"
        if cmd == gacha.get("menu_cmd"):
            return self._celebrate_menu(cel, qq)
        if cmd == gacha.get("cmd"):
            return self._celebrate_gacha(cel, qq, group_id)
        if cmd == pool.get("cmd"):
            return self._celebrate_pool(cel, player, qq)
        return None

    @staticmethod
    def _ga_norm(cel: dict) -> dict:
        """校验/补全抽奖(gacha)配置：兼容旧数据，缺字段时回填默认（幂等）。"""
        ga = cel.setdefault("gacha", {})
        ga.setdefault("enabled", True)
        ga.setdefault("cmd", "生日抽奖")
        ga.setdefault("menu_cmd", "生辰活动")
        ga.setdefault("win_rate", 0.8)
        ga.setdefault("grand_item", "宠物定制卡")
        ga.setdefault("grand_count", 1)
        ga.setdefault("grand_used", False)
        ga.setdefault("per_win_min", 2)
        ga.setdefault("per_win_max", 10)
        ga.setdefault("stock", {"洪荒卡": 1, "变种卡": 5, "史诗卡": 10, "自动修炼卡": 94})
        ga.setdefault("stock_remain", dict(ga["stock"]))
        ga.setdefault("rounds", [])
        return ga

    def _celebrate_menu(self, cel: dict, qq: str) -> str:
        gacha = self._ga_norm(cel)
        pool = cel.get("pool", {})
        lines = [
            f"## 🎂 {cel.get('name', '生辰盛典')}",
            f"> `{gacha.get('cmd')}` 报名下一轮开奖箱 ｜ `{pool.get('cmd')}` 瓜分货币池",
            "",
            f"**今日开奖场次**（每轮约{int(float(gacha.get('win_rate') or 0.8) * 100)}%中奖，奖品从库存动态抽取）",
        ]
        rounds = gacha.get("rounds") or []
        last_i = len(rounds)
        grand_item = gacha.get("grand_item")
        grand_count = int(gacha.get("grand_count") or 1)
        if not rounds:
            lines.append("- （后台尚未配置开奖场次）")
        else:
            for i, r in enumerate(rounds, 1):
                status = "🔔已开奖" if r.get("drawn") else "🕓未开奖"
                mine = "　✅已报名" if qq in (r.get("participants") or {}) else ""
                big = f"　🌟{grand_item}" if (i == last_i and grand_item) else ""
                lines.append(f"- **第{i}场** `{r.get('time') or '?'}`　{status}{big}{mine}")
            lines.append("")
            lines.append("**🎁 动态库存**")
            stock = gacha.get("stock") or {}
            remain = gacha.get("stock_remain") or {}
            if stock:
                for nm, total in stock.items():
                    left = int(remain.get(nm, total))
                    lines.append(f"- {nm}×{total}（剩余 {left}）")
            else:
                lines.append("- （后台尚未配置库存）")
            if grand_item and not gacha.get("grand_used"):
                lines.append(f"- 🌟 {grand_item}×{grand_count} —— 仅最后一轮保发")
        if pool.get("enabled"):
            cur = pool.get("currencies") or {}
            remain = cel.get("pool_remain") or {}
            lines.append("")
            lines.append(f"**奖池瓜分**（每日 **{pool.get('start_time') or '07:00'}** 开启，每15~30分钟可瓜一次，每次瓜取剩余的一个随机比例，越瓜越少）")
            if cur:
                for name in cur:
                    lines.append(f"- {name} 剩余 **{int(remain.get(name, 0)):,}**")
            else:
                lines.append("- （后台尚未配置奖池）")
        lines.append("")
        lines.append("> 报名后到点自动开奖：约{0}%中奖者从剩余库存动态抽奖，每人随机得{1}~{2}份；每人每轮仅一次，最后一轮清空剩余库存并保发定制卡大奖。".format(int(float(gacha.get('win_rate') or 0.8) * 100), max(1, int(gacha.get('per_win_min', 2))), max(1, int(gacha.get('per_win_max', 10)))))
        return "\n".join(lines)

    def _celebrate_gacha(self, cel: dict, qq: str, group_id: str) -> str:
        """报名进入下一场开奖箱（每轮每人限一次）。"""
        gacha = self._ga_norm(cel)
        rounds = gacha.get("rounds") or []
        if not rounds:
            return "🎂 后台还未配置开奖场次。"
        last_i = len(rounds)
        grand_item = gacha.get("grand_item")
        target_i, target = None, None
        for i, r in enumerate(rounds, 1):
            if not r.get("drawn"):
                target_i, target = i, r
                break
        if target is None:
            return "🎂 今日开奖已全部结束，欢迎关注下一场狂欢！"
        is_last = target_i == last_i
        big = f"\n> 🌟 本场压轴大奖：**{grand_item}**（仅此一场）" if (is_last and grand_item) else ""
        parts = target.setdefault("participants", {})
        if qq in parts:
            return (f"🎂 你已报名 **第{target_i}场**（`{target.get('time') or '?'}`）开奖箱。\n"
                    f"> 约80%中奖，开奖后见分晓～{big}")
        parts[qq] = group_id
        return (f"🎂 **报名成功！** 你已进入 **第{target_i}场**（`{target.get('time') or '?'}`）开奖箱。\n"
                f"> 约80%中奖，奖品将从剩余库存随机抽取。{big}\n"
                f"> 开奖后将在群内公布中奖名单，记得留意公告哦～")

    def _pool_start_ts(self, cel: dict) -> int:
        """瓜分池开启时间（事件当日 HH:MM）的时间戳；基于 start_at 同一天计算。"""
        pool = cel.get("pool") or {}
        hm = str(pool.get("start_time") or "07:00")
        st = int(cel.get("start_at") or 0)
        if not st:
            return 0
        p = hm.split(":")
        hh = int(p[0]) if p and p[0].isdigit() else 0
        mm = int(p[1]) if len(p) > 1 and p[1].isdigit() else 0
        ts = st + hh * 3600 + mm * 60
        return max(ts, st)   # 池子不能早于活动开启

    def _celebrate_pool(self, cel: dict, player: dict, qq: str) -> str:
        """瓜分货币奖池：每日 HH:MM 开启，默认无冷却；每次按「等额固定若干」抽取（积分/金币各 per_grab，钻石默认 100 可 per_grab_by_cur 覆盖）。"""
        pool = cel.get("pool", {})
        if not pool.get("enabled"):
            return "🎂 奖池瓜分暂未开启。"
        cur = pool.get("currencies")
        if not isinstance(cur, dict) or not cur:
            return "🎂 后台还未配置奖池。"
        now = int(time.time())
        # 池子开启时间门槛（例如 07:00）
        pst = self._pool_start_ts(cel)
        if pst and now < pst:
            hm = pool.get("start_time") or "07:00"
            return f"🎂 瓜分奖池将于 **{hm}** 开启，敬请期待！"
        carnival = self._in_carnival(cel, now)
        # 无冷却开关（后台 pool 可配，默认开启）：开启时每次瓜分不设冷却，可连续点击
        no_cd = bool(pool.get("no_cd", True))
        ppl = cel.setdefault("players", {}).setdefault(qq, {})
        if not no_cd:
            if carnival:
                cd_sec = int(cel.get("carnival_cooldown_sec") or self.CARNIVAL_COOLDOWN_SEC)
            else:
                cd_min = max(1, int(pool.get("cooldown_min") or 15))
                cd_max = max(cd_min, int(pool.get("cooldown_max") or 30))
                cd_sec = random.randint(cd_min, cd_max) * 60   # 冷却 15~30 分钟随机
            next_ok = int(ppl.get("pool_next") or 0)
            if next_ok and now < next_ok:
                rem = next_ok - now
                if carnival:
                    return f"⏳ 奖池冷却中，剩 {rem} 秒后可再次瓜分。"
                return f"⏳ 奖池仍在冷却，剩 **约{max(1, (rem + 59) // 60)}分钟** 后可再次瓜分。"
        # 等额固定额度：每次从剩余奖池抽「固定若干」而非随机比例。积分/金币各取 per_grab；
        # 钻石默认 100（可用 pool.per_grab_by_cur 按币种单独覆盖）。
        per_grab = max(0, int(pool.get("per_grab") or 1000))
        per_by_cur = {"钻石": 100}
        per_by_cur.update(pool.get("per_grab_by_cur") or {})
        remain = cel.setdefault("pool_remain", {})
        gained = []
        for name in cur:
            r = int(remain.get(name, 0))
            if r <= 0:
                continue
            amt = max(0, int(per_by_cur.get(name, per_grab)))
            share = min(amt, r)   # 等额若干，最多取到该币剩余
            if share <= 0:
                continue
            remain[name] = r - share
            self.store.add_currency(player, name, share)
            gained.append((name, share))
        if not gained:
            return "🎂 奖池已被瓜分完毕！"
        # 狂欢时刻参与者：整个最后 1 小时窗口内至少瓜过一次的人，用于收官平分
        if carnival:
            (cel.setdefault("carnival", {}).setdefault("participants", {}))[qq] = player.get("group") or ""
        ppl["pool_ts"] = now
        ppl["pool_next"] = now if no_cd else now + cd_sec
        lines = ["## 🎂 瓜分成功！你获得："]
        for name, share in gained:
            lines.append(f"- {name} × **{share:,}**")
        remain_lines = [f"{n} {int(remain.get(n, 0)):,}" for n in cur if int(remain.get(n, 0)) > 0]
        lines.append(f"> 剩余：**{', '.join(remain_lines) or '已空'}**")
        return "\n".join(lines)

    def _in_carnival(self, cel: dict, now: int | None = None) -> bool:
        """是否处于「狂欢时刻」总活动最后 1 小时窗口内。"""
        if now is None:
            now = int(time.time())
        end = int(cel.get("end_at") or 0)
        if not end:
            return False
        car_sec = int(cel.get("carnival_window_sec") or self.CARNIVAL_WINDOW_SEC)
        return end - car_sec <= now <= end

    def _pool_multiply(self, cel: dict) -> int:
        """把当前剩余奖池按随机 10~100 倍放大（仅对仍有余量的币种），返回倍率。"""
        lo = int(cel.get("carnival_mult_lo") or self.CARNIVAL_DOUBLE_LO)
        hi = int(cel.get("carnival_mult_hi") or self.CARNIVAL_DOUBLE_HI)
        mult = random.randint(lo, hi)
        cur = (cel.get("pool") or {}).get("currencies") or {}
        remain = cel.setdefault("pool_remain", {})
        for name in cur:
            r = int(remain.get(name, 0))
            if r > 0:
                remain[name] = r * mult
        return mult

    def _distribute_remaining(self, cel: dict) -> str:
        """结束前 5 分钟：把剩余奖池平分给所有参与过狂欢瓜分的玩家（余数随机补发）。"""
        cur = (cel.get("pool") or {}).get("currencies") or {}
        remain = cel.setdefault("pool_remain", {})
        parts = (cel.get("carnival") or {}).get("participants") or {}
        ids = list(parts.keys())
        if not ids:
            return "🎉 **狂欢时刻收官**：本轮无人参与瓜分，剩余奖池无人领取。"
        lines = [f"🎉 **狂欢时刻收官**！剩余奖池平分给 **{len(ids)}** 位狂欢玩家："]
        for name in cur:
            r = int(remain.get(name, 0))
            if r <= 0:
                continue
            n = len(ids)
            each, extra = divmod(r, n)
            extra_ids = set(random.sample(ids, extra)) if extra else set()
            for uid in ids:
                amt = each + (1 if uid in extra_ids else 0)
                if amt <= 0:
                    continue
                self.store.add_currency(
                    self.store.get_player(uid, parts[uid]), name, amt
                )
            remain[name] = 0
            lines.append(
                f"• **{name}** 剩余 {r:,}：每人 {each:,} 份"
                + ("，余数随机补发若干活跃玩家" if extra else "（平均瓜分）")
            )
        return "\n".join(lines)

    async def _celebrate_carnival(self, cel: dict, now: int) -> bool:
        """狂欢时刻调度：开场公告+冷却10s+首次翻倍、每10分钟翻倍共3次、结束前5分钟平分。返回是否有变动。"""
        end = int(cel.get("end_at") or 0)
        if not end:
            return False
        car_sec = int(cel.get("carnival_window_sec") or self.CARNIVAL_WINDOW_SEC)
        car_start = end - car_sec
        if not (car_start <= now <= end):
            return False
        carn = cel.setdefault("carnival", {})
        # end_at 变更（后台重新配置新一场活动）时，自动清空狂欢时刻的现场状态
        if carn.get("for_end") != end:
            carn = cel["carnival"] = {
                "for_end": end, "announced": False, "double_step": 0,
                "last_double_ts": 0, "participants": {}, "distributed": False,
            }
        changed = False
        # 开场：公告 + 冷却 10s + 第①次翻倍（与窗口开启同一时刻）
        if not carn.get("announced"):
            mult = self._pool_multiply(cel)
            carn["announced"] = True
            carn["double_step"] = 1
            carn["last_double_ts"] = now
            cd = int(cel.get("carnival_cooldown_sec") or self.CARNIVAL_COOLDOWN_SEC)
            await self._fire_celebrate_broadcast(
                cel,
                f"🎉 **狂欢时刻**！最后 {car_sec // 60} 分钟，奖池瓜分冷却降至 **{cd} 秒**，抢到就是赚到！\n"
                f"🎁 奖池翻倍 ×{mult}，速来瓜分！",
            )
            changed = True
        # 第②③次翻倍：每 10 分钟一次，共 3 次
        else:
            done = int(carn.get("double_step") or 1)
            gap = int(cel.get("carnival_double_gap_sec") or self.CARNIVAL_DOUBLE_GAP_SEC)
            maxx = int(cel.get("carnival_double_max") or self.CARNIVAL_DOUBLE_MAX)
            if done < maxx and now - int(carn.get("last_double_ts") or 0) >= gap:
                mult = self._pool_multiply(cel)
                carn["double_step"] = done + 1
                carn["last_double_ts"] = now
                await self._fire_celebrate_broadcast(
                    cel, f"🎁 **奖池再次翻倍 ×{mult}**！第 {done + 1}/{maxx} 次，冲！"
                )
                changed = True
        # 结束前 5 分钟：平分剩余
        dist_sec = int(cel.get("carnival_final_dist_sec") or self.CARNIVAL_FINAL_DIST_SEC)
        if now >= end - dist_sec and not carn.get("distributed"):
            carn["distributed"] = True
            await self._fire_celebrate_broadcast(cel, self._distribute_remaining(cel))
            changed = True
        return changed

    # --------------------------- 生辰盛典后台循环（开奖箱/公告） ----------------------------
    CELEBRATE_CHECK_SEC = 20
    # 瓜分「递减动态」：每次瓜取剩余池子的一个随机比例（下限/上限），池子越瓜越少。
    POOL_DECAY_LO = 0.03
    POOL_DECAY_HI = 0.12
    # 「狂欢时刻」：总活动最后 1 小时，冷却 10s；每 10 分钟奖池翻倍 10~100 倍，共 3 次；
    # 结束前 5 分钟把剩余奖池平分给参与过狂欢瓜分的玩家。均可被 cel 配置覆盖。
    CARNIVAL_WINDOW_SEC = 3600
    CARNIVAL_COOLDOWN_SEC = 10
    CARNIVAL_DOUBLE_GAP_SEC = 600
    CARNIVAL_DOUBLE_MAX = 3
    CARNIVAL_DOUBLE_LO = 10
    CARNIVAL_DOUBLE_HI = 100
    CARNIVAL_FINAL_DIST_SEC = 300

    async def _celebrate_loop(self) -> None:
        while True:
            try:
                await self._celebrate_tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[petpark] 生辰盛典循环异常")
            try:
                await asyncio.sleep(self.CELEBRATE_CHECK_SEC)
            except asyncio.CancelledError:
                raise

    async def _celebrate_tick(self) -> None:
        cel = self.store._data.get("celebrate")
        if not cel or not cel.get("enabled"):
            return
        now = int(time.time())
        start = int(cel.get("start_at") or 0)
        end = int(cel.get("end_at") or 0)
        changed = False
        # 开启 / 结束公告（各一次）
        if start and start <= now and not cel.get("announced_start"):
            await self._fire_celebrate_broadcast(cel, cel.get("announce") or "🎂 生辰盛典开启！")
            cel["announced_start"] = True
            changed = True
        if end and end <= now and not cel.get("announced_end"):
            await self._fire_celebrate_broadcast(cel, cel.get("announce_end") or "🎂 生辰盛典收官")
            cel["announced_end"] = True
            changed = True
        # 每小时推送「如何参与」（窗口内循环，`howto_interval_h` 小时/次，0=关闭）
        how_h = int(cel.get("howto_interval_h") or 0)
        if how_h and start <= now <= end:
            howlast = int(cel.get("howto_last_ts") or 0)
            how_int = how_h * 3600
            if now - howlast >= how_int:
                await self._fire_celebrate_broadcast(cel, cel.get("howto") or "🎂 如何参与【生辰盛典】")
                cel["howto_last_ts"] = now
                changed = True
        # 开奖箱：仅在活动窗口内触发已到点的场次
        if start <= now <= end:
            rounds = (cel.get("gacha") or {}).get("rounds")
            if isinstance(rounds, list):
                for i, r in enumerate(rounds, 1):
                    if r.get("drawn"):
                        continue
                    da = int(r.get("draw_at") or 0)
                    if da and now >= da:
                        await self._celebrate_draw_round(cel, i, r)
                        changed = True
            # 狂欢时刻：最后 1 小时 冷却10s + 每10分钟翻倍(共3次) + 结束前5分钟平分剩余
            if await self._celebrate_carnival(cel, now):
                changed = True
        if changed:
            await self.store.save()

    async def _fire_celebrate_broadcast(self, cel: dict, text: str) -> None:
        task = self._broadcast_to_authorized_groups(text)
        if task is not None:
            try:
                await task
            except Exception:
                logger.exception("[petpark] 生辰盛典广播失败")

    async def _celebrate_draw_round(self, cel: dict, idx: int, rnd: dict) -> None:
        """对某一开奖场次执行抽奖：每轮约 win_rate 中奖，从共享库存按剩余份数加权动态抽取，每人随机得 per_win_min~per_win_max 份；最后一轮清空剩余库存并保发定制卡大奖。"""
        gacha = self._ga_norm(cel)
        stock_cfg = gacha.get("stock") or {}
        remain = gacha.setdefault("stock_remain", dict(stock_cfg))
        win_rate = float(gacha.get("win_rate") or 0.8)
        grand_item = gacha.get("grand_item")
        grand_count = max(1, int(gacha.get("grand_count") or 1))
        rounds = gacha.get("rounds") or []
        is_last = (idx == len(rounds))
        parts = rnd.get("participants") or {}
        rnd["drawn"] = True
        text = f"## 🎂 生辰盛典 **第{idx}场**开奖（`{rnd.get('time') or '?'}`）\n"
        if not parts:
            rnd["result"] = {"participants": 0, "note": "本轮无有效参与者"}
            text += "> 本轮无有效参与者，本轮流流，下轮再会～"
            rnd["participants"] = {}
            await self._fire_celebrate_broadcast(cel, text)
            return
        openids = list(parts.keys())

        def _left(it):
            return int(remain.get(it, stock_cfg[it]))

        avail_total = sum(_left(it) for it in stock_cfg if _left(it) > 0)
        # 每名中奖者随机派发数量（加大：默认 2~10，可在 gacha 配置里用 per_win_min/per_win_max 调整）
        per_win_min = max(1, int(gacha.get("per_win_min", 2)))
        per_win_max = max(per_win_min, int(gacha.get("per_win_max", 10)))
        # 中奖人数：参与×win_rate（至少1），上限=参与人数/剩余库存（保证每名中奖者至少能拿到1件）
        want = max(1, int(len(openids) * win_rate))
        n_winners = min(want, len(openids), avail_total)
        result = {"participants": len(openids), "winners": [], "grand": None}
        if n_winners > 0:
            chosen = random.sample(openids, n_winners)
            chunk = [f"- 🎁 **中奖**（约{int(win_rate * 100)}%，剩余库存动态抽取，每人随机×{per_win_min}~{per_win_max}）："]
            for w in chosen:
                items = [it for it in stock_cfg if _left(it) > 0]
                if not items:
                    break
                weights = [_left(it) for it in items]
                item = random.choices(items, weights=weights)[0]
                qty = random.randint(per_win_min, per_win_max)
                if qty > _left(item):
                    qty = _left(item)
                if qty <= 0:
                    continue
                gid = parts[w]
                self.store.add_item(self.store.get_player(w, gid), item, qty)
                remain[item] = _left(item) - qty
                result["winners"].append({"openid": w, "item": item, "count": qty})
                chunk.append(f"　• {self._display_uid(w)} → {item}×{qty}")
            text += "\n".join(chunk) + "\n"
            # 最后一场：无论如何把剩余库存全部随机派发给当轮参与者，确保奖池清空
            if is_last and any(_left(it) > 0 for it in stock_cfg):
                flush = {}
                for it in list(stock_cfg.keys()):
                    cnt = _left(it)
                    if cnt <= 0:
                        continue
                    for w in random.choices(openids, k=cnt):
                        gid = parts[w]
                        self.store.add_item(self.store.get_player(w, gid), it, 1)
                        b = flush.setdefault(w, {})
                        b[it] = b.get(it, 0) + 1
                    remain[it] = 0
                if flush:
                    lines = ["- 🌊 **收官清空剩余库存**："]
                    for w, m in flush.items():
                        s = "、".join(f"{it}×{c}" for it, c in m.items())
                        lines.append(f"　• {self._display_uid(w)} → {s}")
                    text += "\n".join(lines) + "\n"
        else:
            text += "> 本轮库存已发光，无库存奖品。\n"
        # 最后一轮：保发定制卡大奖（仅一次）
        if is_last and grand_item and not gacha.get("grand_used") and openids:
            g = random.choice(openids)
            self.store.add_item(self.store.get_player(g, parts[g]), grand_item, grand_count)
            gacha["grand_used"] = True
            result["grand"] = {"openid": g, "item": grand_item, "count": grand_count}
            text += f"- 🌟 **压轴大奖**：{grand_item}×{grand_count} → 恭喜 **{self._display_uid(g)}**\n"
        if result["winners"] or result["grand"]:
            text += f"> 共 **{len(openids)}** 人参与，恭喜中奖者，奖品已到账！"
        else:
            text += f"> 本轮共 **{len(openids)}** 人参与，无库存派发，下轮再会～"
        rnd["result"] = result
        rnd["participants"] = {}
        await self._fire_celebrate_broadcast(cel, text)

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
            drop = "\n> " + "\n> ".join(reward_lines) if reward_lines else ""
            desc = f"您的{nick}在{name}遇见{monster}，激战之后**大胜**！"
            body = (
                f"> 👹 怪物战力 **{power}** · 我方发挥 **{roll}**\n"
                f"> 🎁 通关奖励：{drop}"
            )
            return f"{head}\n{desc}\n{body}{self._auto_level_note(player, p)}"
        desc = f"您的{nick}在{name}遇见{monster}，力战之后**惨败**！"
        body = (
            f"> 👹 怪物战力 **{power}** · 我方发挥 **{roll}**\n"
            "> 💔 战败没有奖励！"
        )
        return f"{head}\n{desc}\n{body}"

    # =====================================================================
    # 点歌系统（QQ官方语音）
    #   ALAPI 搜索/播放 + 本地 mp3→silk 转码 + botpy 群语音发送
    # =====================================================================
    SONG_SESSION_TTL = 15 * 60  # 会话过期：秒
    SONG_DEFAULT_ALAPI_TOKEN = "oq7yomxswpvx1k3lcitguvcdzztc0i"  # schema 默认；运行时配置未合并默认值时的兜底
    SONG_DEFAULT_SILK_URL_BASE = "http://103.38.83.146:7799"  # 同上：webadmin 公网地址，供 QQ 拉取 silk

    # ---- 会话 ----
    def _song_session(self, group_id: str) -> dict | None:
        """取有效点歌会话；过期或缺失返回 None。"""
        s = self._song_sessions.get(str(group_id))
        if not s:
            return None
        if time.time() - s.get("ts", 0) > self.SONG_SESSION_TTL:
            self._song_sessions.pop(str(group_id), None)
            return None
        return s

    def _song_dispatch(self, qq, group_id, cmd, tokens):
        """点歌同步入口：搜索/选歌交给后台任务，翻页即时返回。"""
        if not self.song_enabled:
            return "❌ 点歌功能当前已关闭。"
        if cmd == "点歌":
            keyword = " ".join(tokens[1:]).strip()
            if not keyword:
                return ("## 🎵 点歌\n"
                        "发送「点歌 歌名」搜索歌曲；「下一页 / 上一页」翻页；\n"
                        "「选歌 序号」用官方语音发送。")
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return "⚠️ 点歌暂时不可用。"
            loop.create_task(self._song_search_async(group_id, keyword))
            return "⏳ 正在搜索，请稍候…"
        if cmd in ("下一页", "上一页"):
            delta = 1 if cmd == "下一页" else -1
            return self._song_page(group_id, delta)
        if cmd == "选歌":
            if len(tokens) >= 2 and tokens[1].isdigit():
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    return "⚠️ 点歌暂时不可用。"
                loop.create_task(self._song_select_async(group_id, int(tokens[1])))
                return "⏳ 正在制作语音，请稍候…"
            return "❌ 用法：选歌 <序号>（序号见点歌列表左侧数字）。"
        return None

    # ---- API（同步，跑在 executor 线程）----
    def _song_api_get_json(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _song_api_search(self, keyword: str) -> dict:
        u = ("https://v2.alapi.cn/api/music/search?token=%s&keyword=%s&limit=%d"
             % (self.alapi_token, urllib.parse.quote(keyword), self.song_max_results))
        return self._song_api_get_json(u)

    def _song_play_url(self, song_id) -> str:
        u = ("https://v2.alapi.cn/api/music/url?token=%s&id=%s"
             % (self.alapi_token, urllib.parse.quote(str(song_id))))
        d = self._song_api_get_json(u)
        return str((d.get("data") or {}).get("url") or "").strip()

    @staticmethod
    def _song_dur(ms) -> str:
        try:
            s = max(0, int(ms) // 1000)
        except (TypeError, ValueError):
            return "--:--"
        m, ss = divmod(s, 60)
        return f"{m:02d}:{ss:02d}"

    # ---- 渲染 ----
    def _song_render(self, group_id: str) -> str:
        s = self._song_session(group_id)
        if not s:
            return "❌ 请先发送「点歌 歌名」搜索。"
        keyword = s["keyword"]
        songs = s["songs"]
        size = self.song_page_size
        total = len(songs)
        pages = max(1, (total + size - 1) // size)
        page = max(1, min(pages, s["page"]))
        start = (page - 1) * size
        chunk = songs[start:start + size]
        lines = []
        for idx, song in enumerate(chunk, start=start + 1):
            name = str(song.get("name", ""))
            artists = " / ".join(str(a.get("name", "")) for a in song.get("artists", []) if a.get("name"))
            lines.append(f"- **{idx}** {name} - {artists} ({self._song_dur(song.get('duration', 0))})")
        body = "\n".join(lines)
        return (f"## 🎵 点歌搜索『{keyword}』 第 {page}/{pages} 页\n{body}\n\n"
                "发送「选歌 序号」用官方语音；「下一页 / 上一页」翻页。")

    def _song_page(self, group_id: str, delta: int) -> str:
        """本地翻一页并即时返回文本（无网络请求）。"""
        s = self._song_session(group_id)
        if not s:
            return "❌ 请先发送「点歌 歌名」搜索后再翻页。"
        size = self.song_page_size
        total = len(s["songs"])
        pages = max(1, (total + size - 1) // size)
        pos = max(1, min(pages, s["page"] + delta))
        if pos == s["page"]:
            return ("✅ 已经是边界了。\n\n" if (delta > 0 and pos == pages) or (delta < 0 and pos == 1)
                    else "") + self._song_render(group_id)
        s["page"] = pos
        s["ts"] = time.time()
        return self._song_render(group_id)

    # ---- 发送 ----
    async def _song_send(self, group_id: str, text: str, markdown: bool = True) -> None:
        bot = self.context.get_bot()
        if bot is None:
            logger.warning("[petpark] 点歌文本发送失败：get_bot() 为空")
            return
        try:
            await bot.send_group(group_openid=str(group_id), text=text, markdown=markdown)
        except Exception as e:
            logger.warning(f"[petpark] 点歌文本发送失败：{e}")
            try:
                await bot.send_group(group_openid=str(group_id), text=text, markdown=False)
            except Exception as e2:
                logger.warning(f"[petpark] 点歌纯文本降级失败：{e2}")

    async def _song_send_voice(self, group_id: str, silk_url: str) -> None:
        bot = self.context.get_bot()
        if bot is None:
            raise RuntimeError("bot_client 为空")
        gid = str(group_id)
        # file_type=3 = 语音(silk)；QQ 会主动拉取 url 并返回 media 信息
        media = await bot.api.post_group_file(group_openid=gid, file_type=3, url=silk_url)
        if isinstance(media, dict):
            file_info = media.get("file_info")
        else:
            file_info = getattr(media, "file_info", None)
        if not file_info:
            raise RuntimeError("post_group_file 未返回 file_info")
        await bot.api.post_group_message(group_openid=gid, msg_type=7, media={"file_info": file_info})

    # ---- 后台任务 ----
    async def _song_search_async(self, group_id: str, keyword: str) -> None:
        try:
            data = await asyncio.to_thread(self._song_api_search, keyword)
            code = data.get("code") if isinstance(data, dict) else None
            if code is not None and code != 200:  # ALAPI 错误（token 缺失/无效/频率限制等）
                msg = str(data.get("msg") or code or "未知错误")
                logger.warning(f"[petpark] 点歌 ALAPI 搜索错误 code={code} msg={msg} "
                               f"token={'已配置' if self.alapi_token else '为空'}")
                await self._song_send(group_id, f"❌ 点歌查询失败（{msg}），请稍后再试或联系管理员检查 alapi_token。")
                return
            songs = (data.get("data") or {}).get("songs") or []
            if not songs:
                await self._song_send(group_id, f"❌ 没找到「{keyword}」相关歌曲，换个关键词试试。")
                return
            self._song_sessions[str(group_id)] = {
                "keyword": keyword, "songs": songs, "page": 1, "ts": time.time(),
            }
            await self._song_send(group_id, self._song_render(group_id))
        except Exception as e:
            logger.warning(f"[petpark] 点歌搜索失败：{e}")
            await self._song_send(group_id, "❌ 搜索失败，请稍后再试。")

    async def _song_select_async(self, group_id: str, seq: int) -> None:
        s = self._song_session(group_id)
        if not s:
            await self._song_send(group_id, "❌ 请先发送「点歌 歌名」搜索后再选歌。")
            return
        songs = s["songs"]
        if seq < 1 or seq > len(songs):
            await self._song_send(group_id, f"❌ 序号超出范围（1–{len(songs)}）。")
            return
        song = songs[seq - 1]
        name = str(song.get("name", ""))
        artists = " / ".join(str(a.get("name", "")) for a in song.get("artists", []) if a.get("name"))
        by = f" - {artists}" if artists else ""
        try:
            play_url = await asyncio.to_thread(self._song_play_url, song.get("id"))
            if not play_url:
                await self._song_send(group_id, f"❌《{name}》暂无试听资源（可能是 VIP 曲目），换一首吧。")
                return
            silk_url = await self._song_make_silk(play_url)
            if not silk_url:
                await self._song_send(group_id, "❌ 语音生成失败，请稍后再试。")
                return
            await self._song_send_voice(group_id, silk_url)
            await self._song_send(group_id, f"🎵《{name}{by}》已用官方语音发出，请听～")
        except Exception as e:
            logger.warning(f"[petpark] 点歌选歌/发语音失败：{e}")
            await self._song_send(group_id, f"❌ 发送语音失败：{e}")

    async def _song_make_silk(self, play_url: str) -> str | None:
        """下载 mp3 → 转码 silk v3 → 放入临时目录，返回 QQ 可拉取的公网 URL。

        优先用 graiax-silkcoder 在内存中转码（自带 ffmpeg，无需外部二进制）；
        库不可用时回退到外部二进制（需配置 silk_encoder_path）。
        silk 文件保留给 webadmin 对外供 QQ 拉取（由 TTL 清理）。
        """
        name = uuid.uuid4().hex
        silk = self.song_silk_dir / f"{name}.silk"
        try:
            # 1) 下载 mp3（读入内存）
            req = urllib.request.Request(play_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                mp3_bytes = resp.read()
            if not mp3_bytes:
                raise RuntimeError("mp3 下载为空")
            # 2) mp3 → silk v3
            silk_bytes = None
            try:
                import graiax.silkcoder as _sc  # 延迟导入，未安装时优雅降级
                silk_bytes = _sc.encode(mp3_bytes, codec=_sc.Codec.ffmpeg, tencent=True,
                                        audio_format="mp3", ffmpeg_para=["-ar", "24000"])
            except ImportError:
                silk_bytes = None
            if not silk_bytes:
                silk_bytes = self._song_encode_binary(mp3_bytes)
                if not silk_bytes:
                    raise RuntimeError("silk 转码失败（graiax-silkcoder 不可用且无外部编码器）")
            silk.write_bytes(silk_bytes)
            if not self.silk_url_base:
                raise RuntimeError("未配置 silk_url_base")
            return f"{self.silk_url_base}/api/song_silk/{name}.silk"
        except Exception as e:
            logger.warning(f"[petpark] 点歌制作 silk 失败：{e}")
            return None

    def _song_encode_binary(self, mp3_bytes: bytes) -> bytes | None:
        """graiax-silkcoder 不可用时的兜底：ffmpeg→pcm→silk_v3_encoder（二进制需另行提供）。"""
        ff = "ffmpeg"
        enc = self.silk_encoder_path
        if not enc:
            return None
        name = uuid.uuid4().hex
        mp3 = self.song_silk_dir / f"{name}.mp3"
        pcm = self.song_silk_dir / f"{name}.pcm"
        wav = self.song_silk_dir / f"{name}.wav"
        silk = self.song_silk_dir / f"{name}.silk"
        try:
            mp3.write_bytes(mp3_bytes)
            r = subprocess.run([ff, "-y", "-i", str(mp3), "-ar", "24000", "-ac", "1",
                                "-f", "s16le", str(pcm)],
                               timeout=120, check=False, capture_output=True)
            if r.returncode != 0 or not pcm.exists() or pcm.stat().st_size == 0:
                raise RuntimeError("ffmpeg 转 mp3→pcm 失败")
            r = subprocess.run([enc, str(pcm), str(silk), "24000"],
                               timeout=120, check=False, capture_output=True)
            if r.returncode != 0 or not silk.exists() or silk.stat().st_size == 0:
                r = subprocess.run([ff, "-y", "-i", str(mp3), "-ar", "24000", "-ac", "1", str(wav)],
                                   timeout=120, check=False, capture_output=True)
                if r.returncode == 0 and wav.exists() and wav.stat().st_size > 0:
                    r = subprocess.run([enc, str(wav), str(silk), "24000"],
                                       timeout=120, check=False, capture_output=True)
                if r.returncode != 0 or not silk.exists() or silk.stat().st_size == 0:
                    raise RuntimeError("外部 silk_v3_encoder 编码失败")
            return silk.read_bytes()
        except Exception as e:
            logger.warning(f"[petpark] 点歌二进制兜底转码失败：{e}")
            return None
        finally:
            for f in (mp3, pcm, wav, silk):
                try:
                    if f.exists():
                        f.unlink()
                except Exception:
                    pass

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
                f"- {bname} 发起攻击，造成 **{boss_damage}** 伤害！\n"
                f"- 『{nick}』不幸阵亡，挑战失败。\n"
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
            f"- {bname} 造成 **{boss_damage}** 伤害，『{nick}』剩余 HP {p['hp']}/{p['hp_max']}",
            f"- 『{nick}』反击造成 **{player_damage}** 伤害",
            f"- Boss 剩余血量：**{state['hp']}/{state['max_hp']}**{hit_reward}",
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
            top3.append(f"| {len(top3)+1} | `{self._display_uid(qq)}` {nick} | {dmg} | {got} |")
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
            (
                item
                for item in state.get("damage_rank", {}).items()
                if not self._group_is_infinite(str(item[0]).split("\x1f")[0])
            ),
            key=lambda x: x[1],
            reverse=True,
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
            lines.append(f"| {i} | `{self._display_uid(qq)}` | {pet_name} | {dmg} |")
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
    # 口令抽奖 / 我的奖品（全群共享，以用户 id 为主键）
    # =====================================================================
    LOTTERY_CHECK_SEC = 30  # 后台每 30 秒检查一次是否到点开奖
    DEFAULT_LOTTERY_BROADCAST = (
        "## 🎉 口令抽奖开奖！\n"
        "口令「{{password}}」开奖啦～共 {{count}} 份「{{prize}}」\n"
        "中奖名单：{{winners}}\n"
        "（中奖者请到所在群发送「我的奖品」查看并兑换到想要的群）"
    )

    async def _lottery_loop(self) -> None:
        """后台循环：到「开奖时间」自动开奖并全群播报（口令抽奖，全局唯一）。"""
        while True:
            try:
                lottery = self.store.lottery()
                if lottery and lottery.get("enabled") and not lottery.get("drawn"):
                    if int(time.time()) >= int(lottery.get("draw_at", 0)):
                        await self._do_lottery_draw()
                await asyncio.sleep(self.LOTTERY_CHECK_SEC)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[petpark] 口令抽奖后台循环出错")
                await asyncio.sleep(self.LOTTERY_CHECK_SEC)

    # --------------------------- 自定义文本群推送（后台） ----------------------------
    CUSTOM_PUSH_CHECK_SEC = 15   # 每隔 15 秒检查一次是否到点

    async def _custom_push_loop(self) -> None:
        """后台循环：扫描 custom_push.jobs，到点向所有授权且开启宠物乐园的群推送。"""
        while True:
            try:
                await self._custom_push_tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[petpark] 群推送循环异常")
            try:
                await asyncio.sleep(self.CUSTOM_PUSH_CHECK_SEC)
            except asyncio.CancelledError:
                raise

    async def _custom_push_tick(self) -> None:
        store = self.store._data.setdefault("custom_push", {})
        jobs = store.get("jobs")
        if not isinstance(jobs, list):
            store["jobs"] = []
            jobs = store["jobs"]
        now = int(time.time())
        changed = False
        for job in jobs:
            if not job.get("enabled"):
                continue
            if job.get("mode") == "once":
                target = int(job.get("target_ts") or 0)
                if target and now >= target:
                    await self._fire_push_job(job)
                    job["enabled"] = False
                    job["done"] = True
                    changed = True
            elif job.get("mode") == "recurring":
                nxt = int(job.get("next_run") or 0)
                interval_min = max(1, int(job.get("interval_min") or 30))
                if now >= nxt:
                    await self._fire_push_job(job)
                    job["next_run"] = int(time.time()) + interval_min * 60
                    changed = True
        if changed:
            await self.store.save()

    async def _fire_push_job(self, job: dict) -> None:
        """向所有授权且开启宠物乐园玩法的群推送一次任务文案，并记录最近结果。"""
        text = str(job.get("text") or "").strip()
        if not text:
            job["last_result"] = {"ts": int(time.time()), "sent": 0, "failed": 0,
                                  "targets": 0, "error": "文案为空"}
            return
        result = {"sent": 0, "failed": 0, "targets": 0, "errors": []}
        task = self._broadcast_to_authorized_groups(text)
        if task is not None:
            try:
                result = await task
            except Exception as e:
                result = {"sent": 0, "failed": 0, "targets": 0, "errors": [str(e)]}
        job["last_result"] = {
            "ts": int(time.time()),
            "sent": int(result.get("sent", 0)),
            "failed": int(result.get("failed", 0)),
            "targets": int(result.get("targets", 0)),
            "error": ("; ".join(result.get("errors", [])[:5]) if result.get("errors") else None),
        }

    def _register_lottery_claim(self, qq: str, group_id: str, lottery: dict) -> str:
        """玩家输入口令即登记参与（按 openid 去重，全群共享）。"""
        g = self.store.get_group(group_id)
        if not g.get("enabled", True):
            return "本群未开启宠物乐园，暂时无法参与口令抽奖。"
        if self._group_is_infinite(group_id):
            return "⚠️ 本群为无限服，不参与官方服全局口令抽奖。"
        if self._is_group(group_id) and not self._is_group_authorized(group_id):
            return self._auth_blocked_text()
        qq = str(qq)
        now = int(time.time())
        start = int(lottery.get("start_at") or 0)
        draw = int(lottery.get("draw_at") or 0)
        if lottery.get("drawn"):
            return "本期口令抽奖已开奖。"
        if now < start:
            return "口令抽奖尚未开始，请等待。"
        if now >= draw:
            return "口令抽奖已到开奖时间，等待开奖…"
        entries = lottery.setdefault("entries", {})
        if qq in entries:
            return "你已参与本次口令抽奖，请耐心等待开奖～"
        entries[qq] = {"group": str(group_id), "time": now}
        qty = int(lottery.get("quantity", 0))
        prize_text = PetStore.prize_display_text(lottery.get("prize", {}))
        mode = "随机抽取" if lottery.get("mode") == "lottery" else "先到先得"
        return (
            f"## 🔐 口令正确！\n你已成功参与本次口令抽奖。\n"
            f"奖品：{prize_text}（共 {qty} 份 · {mode}）\n"
            f"开奖时间：{self._fmt_lottery_time(draw)}\n"
            f"开奖后在群里发「我的奖品」即可查看 / 兑换到想要的群～"
        )

    def _handle_lottery_status(self) -> str:
        """口令抽奖公开状态（所有人可见）。口令本身不泄露。"""
        lottery = self.store.lottery()
        if not lottery or not lottery.get("enabled"):
            return "当前没有进行中的口令抽奖。"
        if lottery.get("drawn"):
            return f"最近一期口令抽奖已开奖（{len(lottery.get('winners', []))} 份）。发「我的奖品」查看你是否中奖。"
        now = int(time.time())
        draw = int(lottery.get("draw_at") or 0)
        entries = lottery.get("entries", {})
        qty = int(lottery.get("quantity") or 0)
        prize_text = PetStore.prize_display_text(lottery.get("prize", {}))
        mode = "随机抽取" if lottery.get("mode") == "lottery" else "先到先得"
        if now >= draw:
            return "口令抽奖已到开奖时间，正在开奖…"
        return (
            f"## 🔐 口令抽奖进行中\n"
            f"输入口令参与，抽取 {qty} 份「{prize_text}」\n"
            f"开奖方式：{mode} ｜ 开奖时间：{self._fmt_lottery_time(draw)}\n"
            f"当前已有 {len(entries)} 人参与"
        )

    def _handle_my_prizes(self, player: dict, group_id: str, tokens: list[str]) -> str:
        """我的奖品：列出本人全局奖品背包（unclaimed/claimed），支持「我的奖品 兑换 <序号> [群id]」。"""
        qq = str(player["qq"])
        wallet = self.store.prize_wallet()
        w = PetStore.wallet_for(wallet, qq)
        if len(tokens) >= 2 and str(tokens[1]) == "兑换":
            return self._redeem_prize(qq, tokens, w, group_id)
        unclaimed = w["unclaimed"]
        claimed = w["claimed"]
        if not unclaimed and not claimed:
            return "🎁 你暂无奖品。参与口令抽奖、中奖后奖品会出现在这里。"
        lines = ["## 🎁 我的奖品"]
        if unclaimed:
            lines.append(f"**未兑换（{len(unclaimed)}）**")
            for i, p in enumerate(unclaimed, 1):
                lines.append(
                    f"{i}. {p.get('text', '')} ｜ 口令「{p.get('lottery', '')}」"
                    f"（{self._fmt_lottery_time(p.get('won_at'))}）"
                )
            lines.append("> 领取：`我的奖品 兑换 <序号>`（到当前群），或 `我的奖品 兑换 <序号> <群id>`")
        if claimed:
            lines.append(f"**已兑换（{len(claimed)}）**")
            for p in claimed:
                lines.append(
                    f"- {p.get('text', '')} ｜ 已发放到群 {p.get('claimed_group', '')}"
                    f"（{self._fmt_lottery_time(p.get('claimed_at'))}）"
                )
        return "\n".join(lines)

    def _redeem_prize(self, qq: str, tokens: list[str], w: dict, current_group: str) -> str:
        if len(tokens) < 3:
            return "用法：`我的奖品 兑换 <序号>`（兑换到当前群），或 `我的奖品 兑换 <序号> <群id>`。"
        try:
            idx = int(tokens[2])
        except ValueError:
            return "❌ 序号需为数字。"
        unclaimed = w["unclaimed"]
        if idx < 1 or idx > len(unclaimed):
            return "❌ 没有该序号的可兑换奖品。"
        target_group = current_group
        if len(tokens) >= 4 and tokens[3].strip():
            target_group = str(tokens[3]).strip()
            if target_group != current_group and target_group not in self.store._data.get("groups", {}):
                return "❌ 未识别的群，无法兑换到该群。"
        prize_entry = unclaimed[idx - 1]
        prize = prize_entry.get("prize", {})
        grant = self._grant_prize_to_group(qq, target_group, prize)
        if grant is not None:
            return grant
        now = int(time.time())
        self.store.move_unclaimed_to_claimed(
            self.store.prize_wallet(), qq, str(prize_entry.get("id", "")), target_group, now
        )
        text = prize_entry.get("text", "") or PetStore.prize_display_text(prize)
        return f"✅ 兑换成功！「{text}」已发放到群 {target_group} 你的名下，发送「我的信息」查看。"

    def _grant_prize_to_group(self, qq: str, group_id: str, prize: dict) -> str | None:
        """把奖品发放到指定群的玩家记录。成功返回 None，失败返回错误文案。奖品只含货币/道具。"""
        if not isinstance(prize, dict):
            return "❌ 奖品数据异常，请联系管理员。"
        kind = prize.get("kind")
        name = str(prize.get("name", "") or "")
        count = int(prize.get("count", 1) or 1)
        if not name:
            return "❌ 奖品数据异常（缺少名称），请联系管理员。"
        player = self.store.get_player(qq, group_id)  # 目标群若无档案则自动创建
        if kind == "currency":
            if name not in self.store.CURRENCY_KEYS:
                return f"❌ 货币「{name}」不存在，请联系管理员。"
            self.store.add_currency(player, name, count)
            return None
        if kind == "item":
            if name not in data.ITEMS:
                return f"❌ 道具「{name}」不存在，请联系管理员。"
            self.store.add_item(player, name, count)
            return None
        return "❌ 奖品类型不识别，请联系管理员。"

    async def _do_lottery_draw(self, force: bool = False) -> str:
        """开奖：抽取/排序选出中奖者 → 记入全局奖品背包 → 全群播报。返回结果描述。"""
        lottery = self.store.lottery()
        if not lottery:
            return "当前没有口令抽奖。"
        if lottery.get("drawn"):
            return "本期口令抽奖已开奖。"
        now = int(time.time())
        if not force and now < int(lottery.get("draw_at") or 0):
            return "还没到开奖时间。"
        entries = lottery.setdefault("entries", {})
        quantity = max(0, int(lottery.get("quantity") or 0))
        mode = lottery.get("mode", "lottery")
        openids = list(entries.keys())
        if mode == "claim":
            ordered = sorted(openids, key=lambda oid: entries[oid].get("time", 0))
        else:
            ordered = list(openids)
            random.shuffle(ordered)
        winners = ordered[:quantity]
        prize = lottery.get("prize", {})
        wallet = self.store.prize_wallet()
        created_at = int(lottery.get("created_at") or now)
        for oid in winners:
            self.store.add_prize(wallet, oid, {
                "id": f"{created_at}_{oid}",
                "lottery": str(lottery.get("password", "口令抽奖")),
                "prize": prize,
                "text": PetStore.prize_display_text(prize),
                "won_at": now,
            })
        lottery["drawn"] = True
        lottery["drawn_at"] = now
        lottery["winners"] = winners
        # 解析中奖者展示名用于播报：优先已绑定QQ号，未绑定则回退平台用户ID(openid)
        names = [self._display_uid(oid) for oid in winners]
        text = self._build_lottery_broadcast(
            lottery, names, len(winners), len(entries)
        )
        await self.store.save()
        self._broadcast_to_authorized_groups(text)
        return f"本期口令抽奖已开奖：共 {len(winners)} 人中奖，已全群播报。"

    def _build_lottery_broadcast(self, lottery: dict, winner_names: list[str],
                                 count: int, total: int) -> str:
        """按管理员自定义播报文本（带占位符）或默认模板生成全群通报内容。"""
        tpl = str(lottery.get("broadcast_text") or "").strip() or self.DEFAULT_LOTTERY_BROADCAST
        mode = "随机抽取" if lottery.get("mode") == "lottery" else "先到先得"
        prize_text = PetStore.prize_display_text(lottery.get("prize", {}))
        return (
            tpl.replace("{{password}}", str(lottery.get("password", "")))
            .replace("{{count}}", str(count))
            .replace("{{total}}", str(total))
            .replace("{{prize}}", prize_text)
            .replace("{{mode}}", mode)
            .replace("{{winners}}", "、".join(winner_names) if winner_names else "（无人中奖）")
        )

    @staticmethod
    def _fmt_lottery_time(ts) -> str:
        ts = int(ts or 0)
        if ts <= 0:
            return "—"
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))

    async def lottery_force_draw(self) -> str:
        """管理后台「立即开奖」入口（幂等：已开奖则返回提示）。"""
        return await self._do_lottery_draw(force=True)

    # =====================================================================
    # 帮助 / 信息查询
    # =====================================================================
    def _handle_info(self, cmd: str, tokens: list[str]) -> str | tuple | None:
        if cmd == "宠物乐园":
            md = self._render_menu_image()
            if md:
                return ("宠物乐园 · 指令菜单", md)
            # 渲染万一失败：给一句提示，不提供文字版菜单
            return "菜单图片暂时生成失败，请稍后重试。"
        if cmd == "管理菜单":
            return self._admin_menu_text()
        if cmd == "官方网站":
            return self._official_site_text()
        if cmd == "宠物种类":
            name = self._arg(tokens, 1)
            if name and name in data.SPECIES:
                element = data.SPECIES[name]
                text = (
                    f"## 📖 {name}\n"
                    f"- **默认属性**：{element}\n"
                    f"- 可通过『砸蛋』抽取，或用品质卡在『宠物市场』召唤"
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

    def _cooldown_block(self, player: dict, key: str, label: str, scope: str = "pet") -> str | None:
        """若该行为仍在冷却中，返回提示文本；否则返回 None。

        scope="pet"（默认）表示宠物级冷却(切换宠物各自独立)；scope="player" 表示玩家级冷却(跨宠物共享)。
        """
        remain = (
            self.store.player_cooldown_remaining(player, key)
            if scope == "player"
            else self.store.cooldown_remaining(player, key)
        )
        if remain > 0:
            return f"⏳ **{label}** 冷却中，还需 `{self._fmt_duration(remain)}`。"
        return None

    def _my_info(self, player: dict, group_id: str, event=None) -> str:
        gid = group_id if group_id and group_id != "private" else "私聊"
        lines = [
            "## 📇 我的信息",
            "━━━━━━━━━━━━━━",
            f"🆔 **用户ID**　`{player['qq']}`",
            f"📱 **绑定QQ**　{self._bound_qq_text(player)}",
            *(["> ⚠️ 未绑定QQ将无法游玩宠物乐园，请先绑定（发送「绑定QQ 你的QQ号」）"]
              if self.require_qq_bind and not self.store.get_bound_qq(player.get("qq", "")) else []),
            f"👥 **群号**　`{gid}`",
            f"🌐 **所处分服**　{self._server_label(group_id)}",
            f"👤 **群身份**　{'—' if gid == '私聊' else self._role_label_text(event)}",
            f"🪙 **金币**　{self._short_num(player.get('coin', 0))}",
            f"💎 **积分**　{self._short_num(player.get('jifen', 0))}",
            f"💠 **钻石**　{self._short_num(player.get('diamond', 0))}",
            f"🌀 **深渊结晶**　{self._short_num(self.store.get_abyss_crystal(player))}",
        ]
        streak = player.get("active_streak", 0)
        if self._group_is_infinite(group_id):
            tax_status = "🟢 转让免税 · 无限服（无限制）"
        else:
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
    @staticmethod
    def _role_label_text(event) -> str:
        """当前群身份中文标签：优先群消息事件携带的 member_role。

        与 _is_group_staff 一致——先取事件身份（零额外 API），
        事件未携带时显示「未知」而非臆断。
        """
        role = str(getattr(event, "sender_role", "") or "") if event is not None else ""
        return {"owner": "群主", "admin": "管理员", "member": "群成员"}.get(role, "未知")

    def _bound_qq_text(self, player: dict) -> str:
        qq = self.store.get_bound_qq(player.get("qq", ""))
        if qq:
            return f"`{qq}`（✅已绑定）"
        return "未绑定（发送「绑定QQ QQ号」绑定）"

    def _qq_bind_block(self, qq: str, cmd: str) -> str | None:
        """强制绑定QQ拦截：未绑定用户返回拦截文案，其余返回 None（放行）。"""
        if not self.require_qq_bind:
            return None
        if self.store.get_bound_qq(qq):
            return None
        if cmd in _BIND_ALWAYS_ALLOWED:
            return None
        return (
            "🔒 绑定QQ后才能游玩宠物乐园\n"
            "你还没绑定 QQ号，请先完成绑定：\n"
            "- 发送「绑定QQ 你的QQ号」（纯数字，如 `绑定QQ 123456789`）\n"
            "- 系统会向该QQ的 QQ 邮箱发送 6 位验证码\n"
            "- 收到后发送「验证码 123456」即绑定成功\n\n"
            "> 绑定一次，跨群通用；完整步骤发送「绑定教程」"
        )

    def _bind_tutorial(self) -> str:
        """绑定QQ完整教程（QQ Markdown：多行用 \\n\\n 分隔，避免单换行被吞）。"""
        return (
            "## 📱 绑定QQ教程\n"
            "绑定后你将以**真实QQ号**作为宠物乐园身份，跨群通用，一次绑定全群生效。\n\n"
            "**为什么要绑定**\n"
            "- 宠物乐园已开启「强制绑定QQ」，未绑定无法游玩\n"
            "- 绑定后可用QQ号或「@对方」代替用户ID，赠送/转让/PK/拜访更方便\n\n"
            "**绑定步骤**\n"
            "1. 发送「绑定QQ 你的QQ号」（纯数字5~11位，如 `绑定QQ 123456789`）\n"
            "2. 系统向该QQ的 **QQ邮箱** 发送 6 位验证码\n"
            "3. 打开邮箱查看验证码，发送「验证码 123456」即绑定成功\n\n"
            "**绑定后**\n"
            "- 跨群通用，其它群无需重复绑定\n"
            "- 换绑：发送「换绑QQ 新QQ号」；解除：发送「解绑QQ」\n\n"
            "**收不到验证码？**\n"
            "- 请**大管理员**发送「绑定QQ @对方 你的QQ号」代你绑定（免邮箱验证）\n\n"
            "> 若提示「邮箱服务未配置」，请联系管理员开通邮箱验证。"
        )

    def _bind_qq(self, player: dict, tokens: list[str], rebind: bool = False) -> str:
        pid = str(player.get("qq", ""))
        cmd_name = "换绑QQ" if rebind else "绑定QQ"
        if len(tokens) < 2:
            return (
                f"用法：{cmd_name} QQ号\n"
                f"- 绑定后向该QQ邮箱发送验证码验证\n"
                f"- 跨群通用，其他群无需重复绑定\n"
                f"- 绑定后可用QQ号代替用户ID指定他人（转让/赠送/PK/拜访等）\n"
                f"- 也可直接 @ 对方代替输入用户ID"
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
            f"- 请在 **5分钟** 内发送「验证码 123456」完成{cmd_name}\n"
            f"- 验证码仅对本账号有效，请勿泄露给他人"
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
            f"- 用户ID `{pid}` ↔ QQ号 `{qq_num}`\n"
            f"- 跨群通用，其他群无需重复绑定\n"
            f"- 现在可以用QQ号代替用户ID指定他人（转让/赠送/PK/拜访等）"
        )

    def _unbind_qq(self, player: dict) -> str:
        pid = str(player.get("qq", ""))
        if not self.store.get_bound_qq(pid):
            return "你还没有绑定QQ号。"
        self.store.unbind_qq(pid)
        return "✅ 已解除QQ绑定。"

    def _admin_qq_bind(self, event, group_id: str, cmd: str, tokens: list[str]) -> str:
        """大管理员代用户绑定QQ：`绑定QQ @目标 目标QQ号` / `绑定QQ 用户ID 目标QQ号`。
        免邮箱验证（管理员权限直接设置），跨群通用。"""
        if not self._is_admin(event):
            return "❌ 仅大管理员可代用户绑定QQ。"
        target = str(tokens[1]).strip()
        qq_num = str(tokens[2]).strip()
        if not target:
            return "❌ 无法解析目标用户，请使用 @ 对方或填写对方用户ID。"
        if not (qq_num.isdigit() and 5 <= len(qq_num) <= 11):
            return "❌ QQ号格式不正确（应为5~11位纯数字）。"
        # 目标为平台openid（@提及/用户ID）直接绑定；若目标为已绑定QQ号则解析为对应平台ID
        pid = self._resolve_user_token(target)
        other = self.store.find_platform_id_by_qq(qq_num)
        if other and other != pid:
            return f"❌ QQ号 `{qq_num}` 已被其他用户绑定。"
        self.store.set_qq_binding(pid, qq_num)
        return (
            f"🛠️ 管理员已代绑定成功\n"
            f"- 用户ID `{pid}` ↔ QQ号 `{qq_num}`\n"
            f"- 跨群通用，对方无需再自行绑定"
        )

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
            return f"❌ 用户 `{self._display_uid(inviter_qq)}` 不在本群或未注册。"
        # 杜绝互相邀请/反向接受：自己已经邀请过对方，就不能再变成对方的被邀请人
        if self.store.is_already_invited_by(player, inviter_qq):
            return "❌ 你已经邀请过该用户，不能反向接受邀请。"
        if self.store.invited_by(player):
            return "❌ 你已经接受过他人邀请，无法重复接受。"
        if self.store.is_already_invited_by(inviter, invitee_qq):
            return f"❌ 用户 `{self._display_uid(inviter_qq)}` 已经邀请过你啦。"
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
            f"你已成功接受 `{self._display_uid(inviter_qq)}` 的邀请！\n"
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
            "| 序号 | 用户 | 邀请时间 |",
            "|---:|---|---|",
        ]
        for i, entry in enumerate(users, 1):
            qq = entry.get("qq", "")
            at = entry.get("at", 0)
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(at)) if at else "-"
            lines.append(f"| {i} | `{self._display_uid(qq)}` | {ts} |")
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
        now = time.strftime("%Y/%m/%d %H:%M")
        lines = [
            "## ✅ 签到成功",
            f"> 🎖️ 今日第 **{order}** 位签到 · {now}",
            "",
            f"- 🪙 金币 **+{coin}**（连续签到额外 +{extra}）",
            f"- 🎯 积分 **+{jifen}**",
            f"- 📅 累计签到 **{total}** 天 · 连续 **{streak}** 天",
            f"- 🏅 当前称号：**{title}**",
        ]
        if need and nxt:
            lines.append(f"> 💡 再签到 {need} 天即可成为「{nxt}」哦！")
        else:
            lines.append("> 🏆 你已是最高称号，恭喜成为宠园传说！")
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
        # 单次加币上限 1000亿（金/积/钻统一，含大、小管理员；减币不限）
        if sign > 0 and amount > 100_000_000_000:
            return f"❌ 单次增加{currency}上限 1000亿，本次 {amount} 超出。"
        # 小管理员：仅限本群、仅金币/积分、加币有每日额度、减币不限
        # 无限服：小管理员加币/积分无每日上限（仍不得增减钻石）
        no_sub_limit = self._group_is_infinite(group_id)
        if not is_super:
            if currency == "钻石":
                return "❌ 小管理员无权增减钻石（仅大管理员可操作钻石）。"
            if sign > 0 and not no_sub_limit:
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
        # 记录小管理员当日已用加币额度（无限服无上限，跳过记录）
        if not is_super and sign > 0 and not no_sub_limit:
            actor = self.store.get_player(qq, group_id)
            quota = self._subadmin_quota(actor)
            key = "coin" if currency == "金币" else "jifen"
            quota[key] = quota.get(key, 0) + amount
        verb = "增加" if sign > 0 else "减少"
        icon = "🪙" if currency == "金币" else ("💠" if currency == "钻石" else "💎")
        extra = ""
        if not is_super and sign > 0:
            if no_sub_limit:
                extra = (
                    f"\n> 🛡️ 小管理{currency}无每日上限（无限服）"
                )
            else:
                key = "coin" if currency == "金币" else "jifen"
                used = self._subadmin_quota(self.store.get_player(qq, group_id)).get(key, 0)
                extra = (
                    f"\n> 🛡️ 小管理今日{currency}已增加 {used}/"
                    f"{self.subadmin_daily_add_limit}"
                )
        return (
            f"## ⚙️ 管理操作\n"
            f"已为用户 `{self._display_uid(target)}` {verb}{icon}**{currency} {self._short_num(amount)}**\n"
            f"> {currency}：{self._short_num(before)} → **{self._short_num(after)}**{extra}"
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
                return f"用户 `{self._display_uid(target)}` 已经是本群小管理员。"
            subs.append(target)
            group["subadmins"] = subs
            if self._group_is_infinite(group_id):
                limit_line = "加金币/积分**无每日上限**（无限服）。"
            else:
                limit_line = f"每日增加金币、积分各上限 {self.subadmin_daily_add_limit}，减少不限。"
            return (
                f"## 🛡️ 小管理员任命\n已任命 `{self._display_uid(target)}` 为本群小管理员。\n"
                f"> 权限：本群内『加金币/减金币/加积分/减积分』（不可操作钻石）；"
                f"{limit_line}"
            )
        else:
            if target not in subs:
                return f"用户 `{self._display_uid(target)}` 不是本群小管理员。"
            subs.remove(target)
            group["subadmins"] = subs
            return f"## 🛡️ 小管理员撤销\n已撤销 `{self._display_uid(target)}` 的本群小管理员权限。"

    def _list_subadmins(self, event) -> str:
        if not self._is_admin(event):
            return "❌ 仅大管理员可查看小管理员列表。"
        groups = self.store._data.get("groups", {})
        lines = [
            "## 🛡️ 小管理员一览（全服）",
            "| 群 | 服类型 | 小管理员 |",
            "|---|---|---|",
        ]
        found = False
        for gid, g in groups.items():
            subs = [str(x) for x in g.get("subadmins", [])]
            if not subs:
                continue
            found = True
            st = self._server_label(str(gid))
            users = "<br>".join(f"`{u}`" for u in subs)
            lines.append(f"| `{gid}` | {st} | {users} |")
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
        if self._group_is_infinite(group_id):
            return (
                "## 🛡️ 我的管理额度（本群 · 今日）\n"
                "━━━━━━━━━━━━━━\n"
                "♂️ 本群为 **无限服**：**无每日上限**\n"
                "> 加金币/积分不限量、减金币/积分不限；不可增减钻石。"
            )
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
    def _group_is_infinite(self, group_id: str) -> bool:
        """该群是否为无限服（跨群挑战/宠物神榜对其关闭，小管理员加币/积分无上限）。"""
        if not self._is_group(group_id):
            return False  # 私聊不属于任何服
        group = self.store.get_group(self.store.resolve_group(group_id))
        return str(group.get("server_type", "official")) == "infinite"

    def _server_label(self, group_id: str) -> str:
        """群/私聊当前所处分服的中文标签：无限服 / 官方服 / 私聊（不属于服）。"""
        if not self._is_group(group_id):
            return "私聊（不属任何服）"
        if self._group_is_infinite(group_id):
            return "无限服 🌐"
        return "官方服"

    def _parse_server_type(self, token: str | None) -> str | None:
        """把『官方服/无限服』词解析成 server_type 值；无效或空返回 None（表示不变更）。"""
        if not token:
            return None
        t = str(token).strip()
        if t in ("无限服", "infinite"):
            return "infinite"
        if t in ("官方服", "official"):
            return "official"
        return None

    def _infinite_group_ids(self) -> set[str]:
        """全服所有无限服群的规范群 openid 集合，用于从跨群共享层剔除。"""
        out: set[str] = set()
        for gid, g in self.store._data.get("groups", {}).items():
            if str(g.get("server_type", "official")) == "infinite":
                out.add(self.store.resolve_group(str(gid)))
        return out

    def _infinite_member_qqs(self) -> set[str]:
        """所有「在无限服群有玩家档案」的 openid 集合，用于从按-qq共享的排行中剔除。"""
        inf = self._infinite_group_ids()
        out: set[str] = set()
        for pl in self.store.all_players().values():
            if self.store.resolve_group(str(pl.get("group", ""))) in inf:
                out.add(str(pl.get("qq", "")))
        return out

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
            f"当前服：**{self._server_label(group_id)}**\n"
            f"到期时间：{when}\n"
            f"剩余：**{self._fmt_remain(until)}**"
            + ("" if ok else "\n> 请发送『授权 卡密』续期；切换服类型建议用『授权本群』。")
        )

    def _redeem_auth_card(self, event, group_id: str, qq: str, tokens: list[str]) -> str:
        if not self._is_group(group_id):
            return "授权卡只能在群聊内兑换。"
        if len(tokens) < 2 or not tokens[1].strip():
            return "用法：授权 卡密"
        code = tokens[1].strip()
        used_by = self.store.make_key(group_id, qq)
        days, server_type, err = self.store.redeem_auth_card(code, used_by)
        if days is None:
            return f"❌ 授权失败：{err}"
        group = self.store.get_group(group_id)
        self._apply_server_type(group_id, server_type)
        until = self._extend_group_auth(group_id, days)
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(until))
        # 非大管理员激活本群者，自动升级为本群小管理员（随本群授权失效而失效）
        promoted = ""
        if not self._is_admin(event):
            subs = [str(x) for x in group.get("subadmins", [])]
            if str(qq) not in subs:
                subs.append(str(qq))
                group["subadmins"] = subs
            if server_type == "infinite":
                limit_txt = "无限服：加积分/金币无每日上限"
            else:
                limit_txt = f"每日加币积分各上限 {self.subadmin_daily_add_limit}"
            promoted = (
                f"\n> 🛡️ 你已成为**本群小管理员**（可加减本群金币/积分，{limit_txt}）；"
                f"该身份随本群授权失效而消失。"
            )
        st_label = "无限服（跨群/神榜关闭）" if server_type == "infinite" else "官方服"
        return (
            "## 🔓 群授权成功\n"
            f"本群授权 **+{days} 天**！服类型：**{st_label}**。\n到期时间：{when}\n"
            f"剩余：**{self._fmt_remain(until)}**" + promoted
        )

    def _grant_auth(self, event, group_id: str, tokens: list[str]) -> str:
        if not self._is_admin(event):
            return "❌ 仅大管理员可直接授权本群。"
        if not self._is_group(group_id):
            return "请在群聊内使用本指令。"
        if len(tokens) < 2 or not tokens[1].lstrip("-").isdigit():
            return "用法：授权本群 天数 [官方服|无限服]（天数正数延长，负数缩短；服类型省略则维持现状）"
        days = int(tokens[1])
        if days == 0:
            return "用法：授权本群 天数（不能为 0）"
        server_type = self._parse_server_type(self._arg(tokens, 2))
        group = self.store.get_group(group_id)
        if server_type:
            self._apply_server_type(group_id, server_type)
        until = self._extend_group_auth(group_id, days)
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(until))
        verb = "延长" if days > 0 else "缩短"
        st_label = "无限服（跨群/神榜关闭，小管理员加币积分无上限）" if group["server_type"] == "infinite" else "官方服"
        return (
            "## 🔐 大管理员授权\n"
            f"已为本群{verb} **{abs(days)} 天**。\n到期时间：{when}\n"
            f"剩余：**{self._fmt_remain(until)}**\n当前服：**{st_label}**"
        )

    def _apply_server_type(self, group_id: str, st: str) -> None:
        """切换群服类型，并做家园数据迁移（官方↔无限）。"""
        group = self.store.get_group(group_id)
        old = str(group.get("server_type", "official"))
        if old == st:
            return
        group["server_type"] = st
        if st == "infinite":
            self.store._migrate_homestead_to_group(group_id)
        else:
            self.store._migrate_homestead_from_group(group_id)

    def _set_server_type(self, event, group_id: str, cmd: str) -> str:
        """设为无限服 / 设为官方服（仅大管理员）。"""
        if not self._is_admin(event):
            return "❌ 仅大管理员可设置服类型。"
        if not self._is_group(group_id):
            return "请在群聊内使用本指令。"
        st = "infinite" if cmd.startswith("设为无限服") else "official"
        self._apply_server_type(group_id, st)
        if st == "infinite":
            return (
                "## 🌐 已设为无限服\n"
                "本群已退出跨群共享层：宠物神榜/跨群挑战对群关闭，数据完全群独立；"
                "群内小管理员加积分/金币**无每日上限**。"
            )
        return (
            "## 🌐 已设为官方服\n"
            "本群已回到跨群共享层：参与宠物神榜、可/可被跨群挑战；"
            "小管理员加币积分按每日上限执行。"
        )

    # --------------------------- 群绑定（跨机器人互通） ---------------------------
    def _cmd_group_bind(self, event, group_id: str, qq: str, tokens: list[str]) -> str:
        """把两个机器人在同一物理群的不同 openid 绑定为同一逻辑群。

        用法：
          - 「绑定群」：在本群发起绑定，生成一次性令牌；再用另一机器人在本群发送「绑定群 令牌」。
          - 「绑定群 令牌」：用另一机器人兑换令牌，把本机器人视角的群 openid 映射到规范群。
        """
        if not self._is_admin(event):
            return "❌ 仅大管理员可绑定群。"
        if not self._is_group(group_id):
            return "请在群聊内使用本指令。"
        raw = self._group_id(event) or group_id  # 本机器人视角的原始 openid（映射表键）
        if len(tokens) >= 2 and tokens[1].strip():
            token = tokens[1].strip()
            pend = self._pending_group_bind.get(token)
            if not pend:
                return "❌ 绑定令牌无效，请先在另一机器人所在本群发送「绑定群」获取令牌。"
            if int(time.time()) > int(pend.get("expires", 0)):
                self._pending_group_bind.pop(token, None)
                return "❌ 绑定令牌已过期，请重新发起「绑定群」。"
            canonical = str(pend["group"])
            if raw == canonical:
                self._pending_group_bind.pop(token, None)
                return "ℹ️ 该令牌即本群发起，无需绑定；请改用**另一个机器人**在本群发送「绑定群 令牌」。"
            if self.store.resolve_group(raw) == canonical:
                self._pending_group_bind.pop(token, None)
                return f"ℹ️ 本机器人视角群 `{raw}` 已绑定到规范群 `{canonical}`，无需重复绑定。"
            self.store.set_group_map(raw, canonical)
            self._pending_group_bind.pop(token, None)
            return (
                "## 🔗 群绑定成功\n"
                f"已把本机器人视角群 `{raw}` 映射到规范群 `{canonical}`。\n"
                "> 两机器人在本群的**授权、群设置、跨群**等数据现已互通。"
            )
        token = uuid.uuid4().hex[:6]
        self._pending_group_bind[token] = {
            "group": self.store.resolve_group(raw),
            "expires": int(time.time()) + 300,
        }
        return (
            "## 🔗 群绑定已发起\n"
            f"令牌：`{token}`（5 分钟内有效）\n"
            f"> 请用**另一个机器人**在本群发送：`绑定群 {token}` 完成绑定。"
        )

    def _cmd_group_unbind(self, event, group_id: str, qq: str) -> str:
        if not self._is_admin(event):
            return "❌ 仅大管理员可解绑群。"
        if not self._is_group(group_id):
            return "请在群聊内使用本指令。"
        raw = self._group_id(event) or group_id
        if self.store.unset_group_map(raw):
            return f"## 🔓 群解绑成功\n已解除本机器人视角群 `{raw}` 的映射，恢复为独立群。"
        return "ℹ️ 本机器人视角群当前没有绑定映射（无需解绑）。"

    def _cmd_group_map(self, event, group_id: str) -> str:
        if not self._is_group(group_id):
            return "请在群聊内使用本指令。"
        raw = self._group_id(event) or group_id
        canonical = self.store.resolve_group(raw)
        mapping = self.store.group_map()
        if raw == canonical:
            children = [k for k, v in mapping.items() if v == raw]
            extra = (
                f"> 已绑定到本群的其他机器人视角群：\n> `{'`、`'.join(children)}`"
                if children
                else "> 当前没有其他机器人视角群绑定到本群。"
            )
        else:
            extra = f"> 本机器人视角群已绑定到规范群 `{canonical}`。"
        return (
            "## 🔗 群映射信息\n"
            f"本机器人视角群 openid：`{raw}`\n"
            f"规范群 openid：`{canonical}`\n{extra}"
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
                "## 🐾 宠物乐园 · 指令菜单",
                "> 指令**无需前缀**，直接发送即可；需指定对方时填 **用户ID** 或直接 **@对方**",
                "",
                "**【入门】**",
                "- 砸蛋 · 宠物市场（品质卡/变种卡）· 我的宠物 · 宠物状态",
                "- 宠物改名 · 宠物变性 · 赠送宠物 QQ · 放生宠物",
                "- 锁定宠物 · 解锁宠物（锁定后无法放生/赠送，防误操作）",
                "- 宠物侦查 用户ID",
                "",
                "**【多宠物】**",
                "> 💡 默认 2 席位 ｜ 最多 10 ｜ 重生 +1 ｜ 宠物席位卡 +1",
                "- 宠物列表 · 查看所有宠物（查看所有宠物概要）",
                "- 切换宠物 序号（切换到指定宠物）",
                "- 宠物信息 序号（查看指定宠物详情）",
                "- 放生宠物（放生当前宠物，最后一只不可放生）",
                "- 赠送宠物 QQ（赠送当前宠物）",
                "- 炼化宠物（消耗 1000 积分，将宠物化作对应品质的卡/碎片，20% 出卡 80% 出碎片 3-8 个；`炼化宠物 宠物卡` 可炼化神秘宠物卡）",
                "",
                "**【商城 / 背包】**",
                "- 宠物商城（总览）· 道具商城 · 钻石商城",
                "- 秘技商城 · 神器商城 · 宠物市场",
                "- 查看背包 · 购买 物品 数量 · 使用 物品",
                "- 出售 物品 数量 · 丢弃 物品 数量",
                "- 转让 用户ID 物品 数量 · 清空背包",
                "- 查看说明 物品名",
                "",
                "**【喂养 / 日常】**",
                "> ⏳ 各 10~20 分钟冷却",
                "- 喂食 物品 · " + " · ".join(data.DAILY_ACTIONS),
                "",
            ]
            + event_lines
            + [
                "**【成长】**",
                "- 一键升级宠物 · 宠物升级 次数 · 宠物进化",
                "- 开启自动升级 · 关闭自动升级（经验满自动升级开关，默认开启）",
                "- 宠物飞升 · 宠物渡劫 · 幻境寻宝 · 宠物神仙劫",
                "- 合成卡 目标卡名 · 一键合成品质卡（自动级联升到最高）",
                "- 一键合成品质碎片（把所有碎片批量转卡）",
                "> 每突破 60 级赠史诗卡；10 张低品质卡可合成为高一级卡，碎片 10 片兑 1 张同品质卡",
                "",
                "**【神器 / 秘技】**",
                "- 打造神器 名称 · 佩戴神器 名称 · 卸下神器",
                "- 参悟秘技 名称 · 遗忘秘技",
                "",
                "**【天赋 / 炼丹】**",
                "- 宠物觉醒 · 制作天赋符 · 使用天赋符 天赋",
                "- 炼丹 · 使用仙丹 名称 用户ID 数量",
                "- 治愈 用户ID · 复活 用户ID · 精力转移 用户ID 值",
                "- 复活他人宠物：『起死回生』天赋免费，无天赋耗『九转还魂丹』",
                "",
                "**【对战 / 排行】**",
                "- 宠物攻击 用户ID · 跨群挑战宠物 群号 用户ID",
                "- 宠物排行（本群）· 宠物神榜（全服）· 领取神榜奖励",
                "",
                "**【副本 / 任务】**",
                "> ⏳ 副本 15 分钟冷却",
                "- 宠物副本 · 进入副本 名称",
                "- 深渊秘境 · 深渊介绍 · 深渊商店 · 深渊祝福",
                "- 宠物剧情任务 · 领取任务 名称 · 提交任务 名称",
                "- 我的剧情任务 · 取消剧情任务（`取消剧情任务 任务名` 只取消单个）",
                "",
                "**【宠物摸金】**（独立财富系统）",
                "- 摸金 · 摸金商店 · 购买摸金道具 名称",
                "- 我的摸金 · 进入摸金 难度(1~4)",
                "- 摸金移动 方向 · 摸金探索 · 摸金开箱",
                "- 摸金使用 名称 · 摸金撤离 · 放弃摸金",
                "- 摸金排行 · 今日摸金神榜 · 昨日摸金神榜",
                "- 领取摸金奖励 · 摸金兑换",
                "- 摸金组队 用户ID · 摸金准备 · 摸金队伍 · 摸金取消组队（双排）",
                "- 摸金救援 · 摸金捡取 · 摸金传送（双排互动）",
                "",
                "**【棋类对弈】** 五子棋 · 中国象棋 · 军棋",
                "- 五子棋单人 1~4 · 象棋单人 1~4 · 军棋单人 1~4",
                "- 五子棋双人 @对方 · 象棋双人 @对方 · 军棋双人 @对方 · 接受棋局",
                "- 棋类帮助 · 棋局 · 棋局统计（每步10分钟，超时判放弃）",
                "**【宠物扫雷】**（全服积分排行）",
                "- 扫雷介绍 · 开始扫雷 难度(1~4)",
                "- 扫 坐标（支持多扫，如：扫a1b2）· 插旗 坐标",
                "- 扫雷地图 · 放弃扫雷 · 扫雷排行 · 扫雷兑换",
                "",
                "**【宠物家园】**（放置建造 · 离线产出）",
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
                "**【宠物银行】**（存款生息 · 信用贷款）",
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
                "**【宠物重生】**（涅槃新生 · 属性暴击）",
                "> 渡劫 Lv800 进入准备期，Lv999 可重生。",
                "- 重生 · 购买重生宝石 · 祭奠 积分/钻石 数量",
                "- 确认重生（需重生宝石 + Lv999）",
                "> 💎 重生宝石：1万钻石 + 10万积分",
                "> 🔥 祭奠：消耗积分/钻石提升高倍率概率",
                "> 🎲 属性暴击：2~10×随机（2×最高概率）",
                "> ⛔ 准备期（Lv800+）：禁止出售/转让/丢弃物品",
                "> 📦 重生后保留：品质卡/定制卡/宠物卡/品质碎片/自动修炼卡（其余清空）",
                "",
                "**【姻缘】**",
                "- 宠物追求 用户ID · 同意追求 用户ID",
                "- 宠物求婚 用户ID · 同意求婚 用户ID",
                "- 宠物分手 · 宠物离婚 · 宠物恋情",
                "",
                "**【个人】**",
                "- 我的信息 · 签到 · 我要氪金",
                "- 兑换 卡密 · 赠送金币/积分/钻石 用户ID 数量",
                "- 我的邀请情况 · 受邀 用户ID",
                "- 绑定QQ QQ号 · 验证码 123456 · 换绑QQ · 解绑QQ · 绑定教程",
                "> 必须先绑定QQ才能游玩；绑定后跨群通用、可用QQ号或@对方指定他人",
                "> 也可直接 @ 对方代替输入 用户ID（赠送/转让/PK/拜访等均支持）",
                "",
                "**【图鉴】**",
                "- 宠物种类 · 属性 · 状态 · 神器 · 秘技 · 仙丹 · 天赋",
                "- 查看说明 名称",
                "",
                "**【坐骑】**",
                "> 拥有即自动登场：入场发一次积分，30 分钟无消息自动退场，战力计入对战胜负。",
                "- 坐骑列表 · 我的坐骑 · 骑乘坐骑 名称 · 坐骑升级",
                "- 坐骑市场 · 购买坐骑 名称 · 坐骑图鉴 名称",
                "- 赠送坐骑 用户ID · 丢弃坐骑 名称 · 定制坐骑",
                "- 开启/关闭坐骑系统 · 开启/关闭入场提示 · 开启/关闭离场提示",
                "",
                "> 管理员指令请发送 `管理菜单` 查看。",
            ]
        )

    # ---------------------------------------------------------------------
    # 菜单美图化：把 _menu_text() 渲染成蓝绿分区菜单图片（HTML -> 无头 Chrome PNG，
    # 按内容 md5 缓存到 store.custom_images_dir，经 /custom_images 发送）。
    # 服务器无 emoji 字体，故菜单内容去除 emoji，用纯排版 + 金色装饰呈现。
    # ---------------------------------------------------------------------

    _MENU_EMOJI_RE = re.compile(
        "[\\U0001F000-\\U0001FAFF\\u2300-\\u23FF\\u2500-\\u25FF"
        "\\u2600-\\u27BF\\u2B00-\\u2BFF\\uFE0F\\u200D\\u20E3]"
    )
    _MENU_DISP_W = 720

    @staticmethod
    def _menu_esc(s: str) -> str:
        """转义 HTML 特殊字符，避免菜单内容破坏页面结构。"""
        return re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D\u20E3]", "", str(s)).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")

    def _menu_purify(self, s: str) -> str:
        """去除 emoji（无字体）与 Markdown 星号/反引号，折叠空白，供图片使用。"""
        s = self._MENU_EMOJI_RE.sub("", str(s))
        s = s.replace("`", "").replace("**", "")
        return re.sub(r"\s+", " ", s).strip()

    def _menu_html(self) -> str:
        """把 _menu_text() 逐行解析成分区菜单 HTML——文本与图片永不漂移。"""
        menu = self._menu_text()
        title_main, title_sub = "宠物乐园", "指令菜单"
        intro: list[str] = []
        sections: list[dict] = []
        cur = None
        for raw in menu.split("\n"):
            s = self._menu_purify(raw)
            if not s:
                continue
            if s.startswith("## "):
                t = s[3:].strip()
                if "·" in t:
                    a, b = t.split("·", 1)
                    title_main = a.strip() or title_main
                    title_sub = b.strip() or title_sub
                else:
                    title_main = t
                cur = None
                continue
            if s.startswith("> "):
                body = s[2:].strip()
                if "管理员指令请发送" in body:  # 底部提示单独放页脚
                    continue
                if cur is None:
                    intro.append(body)
                else:
                    cur["body"].append(("note", body))
                continue
            if s.startswith("**【"):
                rest = s[3:]
                tt, rr = rest.split("】**", 1) if "】**" in rest else (rest, "")
                cur = {"title": tt.strip(), "sub": "", "body": []}
                sub = rr.strip()
                if sub.startswith(("（", "(")) and sub.endswith(("）", ")")):
                    cur["sub"] = sub[1:-1].strip()
                elif sub:
                    cur["sub"] = sub
                sections.append(cur)
                continue
            if s.startswith("【") and "】" in s:
                cur = {"title": s[1:s.index("】")].strip(), "sub": "", "body": []}
                sections.append(cur)
                continue
            if s.startswith("- "):
                body = s[2:].strip()
                if cur is None:
                    cur = {"title": "", "sub": "", "body": []}
                    sections.append(cur)
                cur["body"].append(("item", body))
                continue
            if cur is None:
                intro.append(s)
            else:
                cur["body"].append(("text", s))

        esc = self._menu_esc

        def render_item(body: str) -> str:
            # 把（...）描述染成朱红，指令与「·」分隔保持墨色/金色
            parts = re.split(r"([（(][^（）()]*[）)])", body)
            out = []
            for p in parts:
                if not p:
                    continue
                if p[0] in "（(" and p[-1] in "）)":
                    out.append(f'<span class="desc">{esc(p)}</span>')
                else:
                    out.append(esc(p).replace(" · ", '<span class="sep">·</span>'))
            return "".join(out)

        def render_body(body_list) -> str:
            out = []
            for kind, body in body_list:
                if kind == "item":
                    out.append(f'<div class="item">{render_item(body)}</div>')
                elif kind == "note":
                    out.append(f'<div class="note">{esc(body)}</div>')
                else:
                    out.append(f'<div class="plain">{esc(body)}</div>')
            return "".join(out)

        sect_html = []
        for section_no, sec in enumerate(sections, 1):
            sub_h = f'<div class="sect-s">{esc(sec["sub"])}</div>' if sec["sub"] else ""
            sect_html.append(
                f'<div class="sect">'
                f'<div class="sect-h"><span class="sect-no">{section_no:02}</span>{esc(sec["title"])}</div>{sub_h}'
                f'{render_body(sec["body"])}</div>'
            )

        intro_html = f'<div class="intro">{esc(intro[0])}</div>' if intro else ""

        foot_lines = [self._menu_purify(r)[2:].strip() for r in menu.split("\n")
                      if self._menu_purify(r).startswith("> ")
                      and "管理员指令请发送" in self._menu_purify(r)]
        foot_html = f'<div class="foot">{esc(" · ".join(foot_lines))}</div>' if foot_lines else ""

        return (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<style>{card_theme.stylesheet('menu')}</style></head><body>"
            f'<div class="scroll"><div class="content">'
            f'<div class="brand">{esc(title_main)}</div>'
            f'<div class="brand-sub">{esc(title_sub)}</div>'
            f'<div class="rule"></div>{intro_html}'
            f'<div class="cols">{"".join(sect_html)}</div>{foot_html}'
            f'</div></div></body></html>'
        )

    def _crop_menu(self, img):
        """Trim the exact artwork rectangle without canvas margins."""
        return card_theme.crop_canvas(img)

    # ---- 通用 HTML -> PNG 渲染管线（菜单 / 宠物卡 / 背包卡共用） ----
    def _prune_images(self, prefix: str, keep: int = 5) -> None:
        """清理旧缓存图，只保留最近 keep 张（按前缀分开清理）。"""
        try:
            d = self.store.custom_images_dir
            rows = sorted(d.glob(f"{prefix}_*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
            for p in rows[keep:]:
                try:
                    p.unlink()
                except OSError:
                    pass
        except OSError:
            pass

    def _image_dims(self, path, disp_w: int) -> tuple[str, str]:
        """返回图片的显示宽高（#W #H），便于 QQ 端缩放。"""
        try:
            with Image.open(path) as im:
                w, h = im.size
        except OSError:
            return str(disp_w), "0"
        if not w:
            return str(disp_w), "0"
        return str(disp_w), str(max(1, round(disp_w * h / w)))

    def _html_png_ok(self, target) -> bool:
        """判断渲染产物是否可接受（存在且非过小）。"""
        try:
            return target.exists() and target.stat().st_size >= 1000
        except OSError:
            return False

    def _write_html_png(self, html: str, key: str, target, crop=None,
                        win_w: int = 900, win_h: int = 5200) -> bool:
        """用无头 Chrome 把 HTML 渲染成 PNG，可选 crop 后写回；成功返回 True。"""
        html_file = None
        try:
            rdir = Path(self.store.custom_images_dir) / ".render_tmp"
            rdir.mkdir(parents=True, exist_ok=True)
            html_file = rdir / f"{key}.html"
            html_file.write_text(html, encoding="utf-8")
            subprocess.run(
                ["google-chrome", "--headless=new", "--no-sandbox", "--disable-gpu",
                 "--disable-dev-shm-usage", "--hide-scrollbars", "--disable-extensions",
                 "--force-device-scale-factor=1", f"--window-size={win_w},{win_h}",
                 f"--screenshot={target}", html_file.resolve().as_uri()],
                timeout=60, check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            with Image.open(target) as im:
                rgb = im.convert("RGB")
            out = crop(rgb) if crop else rgb
            out.save(target, "PNG")
        except Exception as e:
            logger.warning(f"[petpark] 图片渲染失败：{e}")
            return False
        finally:
            if html_file is not None:
                try:
                    html_file.unlink()
                except OSError:
                    pass
        return True

    def _render_html_image(self, html: str, tag: str, disp_w: int, crop=None,
                           win_w: int = 900, win_h: int = 5200, keep: int = 5) -> str | None:
        """渲染 HTML 为图片并以 Markdown 返回；内容未变则复用缓存图，失败返回 None。"""
        html = card_theme.finish_html(html)
        key = hashlib.md5(html.encode("utf-8")).hexdigest()[:16]
        fname = f"{tag}_{key}.png"
        target = Path(self.store.custom_images_dir) / fname
        if not target.exists():
            rendered = self._write_html_png(html, key, target, crop=crop, win_w=win_w, win_h=win_h)
            if not rendered or not self._html_png_ok(target):
                logger.warning(f"[petpark] {tag} 图片渲染输出异常")
                return None
            self._prune_images(tag, keep)
        w, h = self._image_dims(target, disp_w)
        return f"![{tag} #{w} #{h}]({self._tomb_image_url(fname)})"

    def _render_menu_image(self) -> str | None:
        """渲染并返回菜单图片 Markdown；任何失败回退 None。"""
        try:
            html = self._menu_html()
        except Exception as e:
            logger.warning(f"[petpark] 菜单图片 HTML 生成失败：{e}")
            return None
        # Full command menu grows with active events; leave room for every section.
        return self._render_html_image(html, "menu", self._MENU_DISP_W, crop=self._crop_menu,
                                       win_h=max(5200, 900 + html.count('class="item"') * 150
                                                 + html.count('class="note"') * 100))

    # ---------------------------------------------------------------------
    # 宠物信息卡 · 蓝绿游戏面板（H5 -> 无头 Chrome PNG，嵌立绘 data-URI，无 emoji）
    # ---------------------------------------------------------------------

    @staticmethod
    def _short_num(n) -> str:
        """数值缩写：万→亿→兆→京→…→古戈尔（10^100），用于大数值展示。

        全程整数运算，不依赖 float（超大整数转 float 会报
        “int too large to convert to float”），支持任意大值；结果最多保留
        2 位小数，整数部分上万（如超古戈尔级巨值）时用 x.xxeN 压缩，绝不
        撑破布局。内联实现（不依赖子模块），保证 hot-reload 重载 main 即生效。
        """
        try:
            iv = int(n)
        except (TypeError, ValueError, OverflowError):
            try:
                n = float(n)
            except (TypeError, ValueError, OverflowError):
                return str(n)
            if n != n:  # NaN
                return "0"
            iv = int(n)
        neg = iv < 0
        a = -iv if neg else iv
        if a < 10_000:
            return ("-" + str(a)) if neg else str(a)
        sign = "-" if neg else ""
        units = [
            (10 ** 100, "古戈尔"), (10 ** 72, "大数"), (10 ** 68, "无量"),
            (10 ** 64, "不可思议"), (10 ** 60, "那由他"), (10 ** 56, "阿僧祇"),
            (10 ** 52, "恒河沙"), (10 ** 48, "极"), (10 ** 44, "载"),
            (10 ** 40, "正"), (10 ** 36, "涧"), (10 ** 32, "沟"),
            (10 ** 28, "穰"), (10 ** 24, "秭"), (10 ** 20, "垓"), (10 ** 16, "京"),
            (10 ** 12, "兆"), (10 ** 8, "亿"), (10 ** 4, "万"),
        ]
        for threshold, unit in units:
            if a >= threshold:
                whole = a // threshold
                wstr = str(whole)
                if len(wstr) >= 5:
                    head = wstr[:3]
                    return f"{sign}{head[0]}.{head[1:]}e{len(wstr) - 1}{unit}"
                # 保留到 1% 分辨率并四舍五入
                scaled = (a % threshold) * 100
                dec_q, dec_r = divmod(scaled, threshold)
                if dec_r * 2 >= threshold:
                    dec_q += 1
                if dec_q >= 100:  # 四舍五入进位
                    whole += 1
                    dec_q = 0
                return f"{sign}{whole}.{dec_q:02d}{unit}"
        return f"{sign}{a}"

    @staticmethod
    def _pct(v, total) -> int:
        """返回 v/total 的 0..100 百分比。"""
        try:
            if total <= 0:
                return 0
            return max(0, min(100, round(v * 100 / total)))
        except (TypeError, ZeroDivisionError):
            return 0

    def _pet_portrait_uri(self, pet) -> str | None:
        """返回宠物立绘的 data-URI（优先定制图，其次官方种类图）；无图返回 None。"""
        try:
            cf = pet.get("custom_image")
            if cf:
                p = Path(self.store.custom_images_dir) / cf
                if p.exists():
                    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
                    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode("ascii")
        except OSError:
            pass
        try:
            path = images.pet_image_path(pet.get("species"))
            if path:
                with open(path, "rb") as f:
                    return "data:image/jpeg;base64," + base64.b64encode(f.read()).decode("ascii")
        except OSError:
            pass
        return None

    def _card_crop(self, img):
        """Trim the exact artwork rectangle without canvas margins."""
        return card_theme.crop_canvas(img)

    def _pet_card_html(self, pet, mount_power: int = 0) -> str:
        """把宠物数据渲染成蓝绿游戏面板信息卡 HTML（含立绘，无 emoji）。"""
        esc = self._menu_esc
        petmod.refresh_energy(pet)
        gender = {"男": "雄", "女": "雌"}.get(pet.get("gender"), pet.get("gender", "—"))
        love = pet.get("love_state", "单身")
        mood = max(0, min(5, pet.get("mood", 0)))
        skills = "、".join(pet.get("skills", [])) or "无"
        artifact = pet.get("artifact") or "无"
        talent = pet.get("talent") or "未觉醒"
        ascended = petmod._is_ascended(pet)
        if ascended:
            need = data.ascend_xianyuan_to_next(pet["level"])
            res_k, res_v = "仙元", f"{pet.get('xianyuan', 0)}/{need}"
            res_pct = self._pct(pet.get("xianyuan", 0), need)
            res_extra = f"余 {pet.get('exp', 0)} 经验"
        else:
            need = petmod._exp_to_next(pet["level"])
            res_k, res_v = "经验", f"{pet['exp']}/{need}"
            res_pct = self._pct(pet["exp"], need)
            res_extra = ""
        species_display = pet.get("custom_species_name") or pet.get("species")
        lc = petmod.level_cap(pet)
        bp = petmod.battle_power(pet) + mount_power
        hp, hp_m = pet.get("hp", 0), pet.get("hp_max", 1)
        en, en_m = pet.get("energy", 0), pet.get("energy_max", 1)
        stage = pet.get("stage", ""); quality = pet.get("quality", "")
        element = pet.get("element", "")
        uri = self._pet_portrait_uri(pet)
        portrait = (f'<img class="portrait" src="{uri}" alt="{esc(pet["nickname"])}">'
                    if uri else '<div class="portrait-ph">暂无立绘</div>')
        stats_html = "".join(
            f'<div class="stat"><span>{esc(label)}</span><strong>{esc(self._short_num(pet.get(key, 0)))}</strong></div>'
            for label, key in [("攻击", "atk"), ("防御", "def"), ("智力", "intel")]
        )
        bars = "".join([
            self._bar_row("气血", hp, hp_m, "hp"),
            self._bar_row("精力", en, en_m, "en"),
            self._bar_row("心情", mood, 5, "mo"),
        ])
        rows = [
            ("种类", species_display),
            ("属性", element),
            ("阶段", stage),
            ("品质", quality),
            ("性别", gender),
            ("羁绊", love),
            ("状态", pet.get("status", "正常")),
        ]
        rows_html = "".join(
            f'<div class="row"><k>{esc(k)}</k><v>{esc(str(v))}</v></div>' for k, v in rows
        )
        tags = pet.get("tags", [])
        tags_html = f'<div class="tags">{"".join(f"<i>{esc(t)}</i>" for t in tags)}</div>' if tags else ""
        extra = ""
        if mount_power:
            extra += self._card_row("坐骑加成", f"+{self._short_num(mount_power)}（骑乘中）")
        if pet.get("love_target"):
            extra += self._card_row("伴侣", f"{self._display_uid(pet['love_target'])}　好感 {pet.get('favor', 0)}")
        frozen = ""
        if petmod.is_frozen(pet):
            frozen = ('<div class="warn">假死/惊魂中，剩余约 '
                      f'{petmod.frozen_remain_min(pet)} 分钟无法操作</div>')
        res_bar = (
            f'<div class="resource panel"><div class="res-head"><strong>{esc(res_k)}</strong>'
            f'<span>{esc(res_v)}</span></div><div class="bar">'
            f'<div class="fill mo" style="width:{res_pct}%"></div></div>'
            + (self._card_row("余量", res_extra) if res_extra else '') + '</div>'
        )
        abilities = ''.join(self._card_row(k, v) for k, v in
                            [("天赋", talent), ("秘技", skills), ("神器", artifact)])
        return (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<style>{card_theme.stylesheet('pet')}</style></head><body>"
            '<div class="card"><div class="masthead"><div><div class="mast-title">宠物灵鉴</div>'
            '<div class="mast-caption">宠物乐园 · 伙伴档案</div></div></div>'
            f'<div class="pet-heading"><div><div class="name">{esc(pet["nickname"])}</div>'
            f'<div class="identity">{esc(species_display)} / {esc(element)}属性 / {esc(stage)}</div>'
            f'</div><div class="rank">{esc(quality)}</div></div>'
            '<div class="pet-layout"><div>'
            f'<div class="portrait-wrap">{portrait}<div class="portrait-label">Lv.{pet["level"]} / {lc}</div></div>'
            f'{res_bar}<div class="vitals panel">{bars}</div></div><div>'
            f'<div class="power"><span>综合战力</span><strong>{self._short_num(bp)}</strong></div>'
            f'<div class="attributes panel">{rows_html}</div>'
            f'<div class="stats">{stats_html}</div>'
            f'<div class="abilities panel">{abilities}{extra}</div></div></div>{tags_html}{frozen}'
            '<div class="foot"><span>查看宠物：我的宠物</span><span>养成指引：宠物乐园</span></div>'
            '</div></body></html>'
        )

    def _bar_row(self, label, v, total, cls) -> str:
        return (f'<div class="bar-row"><span class="bar-k">{self._menu_esc(label)}</span>'
                f'<div class="bar"><div class="fill {cls}" style="width:{self._pct(v, total)}%"></div></div>'
                f'<span class="bar-n">{self._menu_esc(self._short_num(v))}/{self._menu_esc(self._short_num(total))}</span></div>')

    def _card_row(self, k, v) -> str:
        return f'<div class="row"><k>{self._menu_esc(k)}</k><v>{self._menu_esc(str(v))}</v></div>'

    def _render_pet_image(self, pet, mount_power: int = 0) -> str | None:
        """渲染并返回宠物信息卡 Markdown；任何失败返回 None。"""
        try:
            html = self._pet_card_html(pet, mount_power)
        except Exception as e:
            logger.warning(f"[petpark] 宠物卡 HTML 生成失败：{e}")
            return None
        return self._render_html_image(html, "petcard", 720, crop=self._card_crop,
                                       win_w=760, win_h=4200)

    # =====================================================================
    # 坐骑系统：卡片渲染 / 入场离场 / 后台循环 / 战力
    # =====================================================================
    MOUNT_IDLE_CHECK_SEC = 60  # 每 60 秒扫描一次离场

    def _mount_portrait_uri(self, name: str) -> str | None:
        """坐骑立绘 data-URI：优先 assets/mounts/<名>.png，无则 None（占位）。"""
        try:
            p = (Path(__file__).resolve().parent / "petpark" / "assets"
                 / "mounts" / f"{name}.png")
            if p.exists():
                return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode("ascii")
        except OSError:
            pass
        return None

    def _sync_mount_gifs(self) -> None:
        """启动时把仓库内坐骑 GIF 复制到 custom_images_dir（经 /custom_images 动画展示）。"""
        src_dir = Path(__file__).resolve().parent / "petpark" / "assets" / "mounts"
        try:
            for g in src_dir.glob("*.gif"):
                dst = Path(self.store.custom_images_dir) / g.name
                if not dst.exists():
                    shutil.copyfile(g, dst)
        except OSError:
            logger.exception("[petpark] 同步坐骑 GIF 失败")

    def _mount_gif_url(self, name: str) -> str:
        """坐骑 GIF 的 /custom_images URL；无图返回 ""。"""
        fname = f"{name}.gif"
        dst = Path(self.store.custom_images_dir) / fname
        if not dst.exists():
            src = Path(__file__).resolve().parent / "petpark" / "assets" / "mounts" / fname
            if src.exists():
                try:
                    shutil.copyfile(src, dst)
                except OSError:
                    return ""
        if not dst.exists():
            return ""
        return self._tomb_image_url(fname)

    def _mount_image_md(self, name: str, disp_w: int = 640) -> str:
        """坐骑 GIF 的 Markdown 图片；无图返回 ""。"""
        url = self._mount_gif_url(name)
        if not url:
            return ""
        try:
            target = Path(self.store.custom_images_dir) / f"{name}.gif"
            w, h = self._image_dims(target, disp_w)
        except Exception:
            return f"![{name}]({url})"
        return f"![{name} #{w} #{h}]({url})"

    def _mount_info_text(self, name: str, player: dict, kind: str = "enter", reward: int | None = None) -> str:
        """坐骑文字信息卡（GIF 之外的属性行；kind 控制主题/奖励行；reward 为已到账实际奖励）。"""
        cfg = data.MOUNTS.get(name, {})
        owned = name in (player.get("mounts") or {})
        inst = player.get("mounts", {}).get(name, {}) if owned else {}
        stars = int(cfg.get("stars", 1))
        star_str = "★" * stars + "☆" * (5 - stars)
        owner = self._display_uid(player.get("qq", "")) if owned else "—"
        plate = inst.get("plate", "骑-?") if owned else "未拥有"
        level = int(inst.get("level", 1))
        power = int(inst.get("power", cfg.get("base_power", 0)))
        value = self._short_num(cfg.get("value", 0))
        rmin, rmax = cfg.get("reward_min", 0), cfg.get("reward_max", 0)
        now_hhmm = time.strftime("%H:%M", time.localtime(int(time.time())))
        theme = {"enter": "闪★亮", "leave": "绝★尘", "my": "专属"}.get(kind, "骑")
        custom = bool(inst.get("custom"))
        lines = [
            f"┌★★—{theme}—★★┐",
            f"**【{name}】** {star_str} · Lv.{level}",
            f"坐骑战力：**{self._short_num(power)}**",
            f"主人：{owner} · 号牌：{plate}",
            f"价值：{value}",
        ]
        if reward is not None:
            lines.append(f"入场奖励：**+{self._short_num(reward)} 积分（已到账）**")
        elif kind in ("enter", "leave"):
            lines.append(f"奖励：{self._short_num(rmin)}~{self._short_num(rmax)} 积分")
        else:
            lines.append(f"入场奖励：{self._short_num(rmin)}~{self._short_num(rmax)} 积分")
        if custom:
            lines.append("来源：专属定制")
        lines.append(f"时间：{now_hhmm}")
        return "\n".join(lines)

    def _mount_full_message(self, name: str, player: dict, kind: str = "enter", reward: int | None = None) -> str:
        """完整坐骑消息：GIF（若有）+ 文字信息卡。reward 为入场已到账实际奖励。"""
        info = self._mount_info_text(name, player, kind, reward)
        img = self._mount_image_md(name)
        if img:
            return f"{img}\n\n{info}"
        return info

    def _mount_card_html(self, name: str, player: dict, kind: str = "enter") -> str:
        """坐骑卡 HTML（入场/离场/我的坐骑三种，主题由 kind 决定）。"""
        esc = self._menu_esc
        cfg = data.MOUNTS.get(name, {})
        inst = player.get("mounts", {}).get(name, {})
        stars = int(cfg.get("stars", 1))
        star_str = "★" * stars + "☆" * (5 - stars)
        owner = self._display_uid(player.get("qq", ""))
        plate = inst.get("plate", "骑-?")
        level = int(inst.get("level", 1))
        power = int(inst.get("power", cfg.get("base_power", 0)))
        value = self._short_num(cfg.get("value", 0))
        rmin, rmax = cfg.get("reward_min", 0), cfg.get("reward_max", 0)
        now_hhmm = time.strftime("%H:%M", time.localtime(int(time.time())))
        theme_word = {"enter": "闪★亮", "leave": "绝★尘", "my": "专属"}.get(kind, "骑")
        custom = bool(inst.get("custom"))
        uri = self._mount_portrait_uri(name)
        if uri:
            portrait = (f'<div class="portrait-wrap">'
                        f'<img class="portrait ph-img" src="{uri}" alt="{esc(name)}"></div>')
        else:
            portrait = (f'<div class="portrait-wrap"><div class="portrait-ph">'
                        f'<div>🐎</div><div>{esc(name)}</div>'
                        f'<div class="ph-sub">Lv.{level} · 坐骑战力 {self._short_num(power)}</div>'
                        f'</div></div>')
        rows = [
            ("坐骑", name),
            ("主人", owner),
            ("号牌", plate),
            ("灵智", star_str),
            ("价值", value),
            ("奖励" if kind in ("enter", "leave") else "入场",
             f"{self._short_num(rmin)}~{self._short_num(rmax)} 积分"),
            ("时间", now_hhmm),
        ]
        if custom:
            rows.append(("来源", "专属定制"))
        rows_html = "".join(
            f'<div class="row"><k>{esc(str(k))}</k><v>{esc(str(v))}</v></div>'
            for k, v in rows
        )
        return (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<style>{card_theme.stylesheet('mount')}</style></head><body>"
            '<div class="card"><div class="masthead"><div>'
            f'<div class="mast-title">骑兽灵鉴</div>'
            f'<div class="mast-caption">{esc(theme_word)} · 宠物乐园 坐骑档案</div></div></div>'
            f'<div class="mount-heading"><div><div class="name">{esc(name)}</div>'
            f'<div class="identity">{esc(star_str)} · Lv.{level}</div></div>'
            f'<div class="rank">{esc(star_str)}</div></div>'
            '<div class="mount-layout"><div>'
            f'{portrait}</div><div>'
            f'<div class="power"><span>坐骑战力</span><strong>{self._short_num(power)}</strong></div>'
            f'<div class="attributes panel mount-rows">{rows_html}</div></div></div>'
            '<div class="foot"><span>骑乘状态：我的坐骑</span><span>兑换入口：坐骑市场</span></div>'
            '</div></body></html>'
        )

    def _render_mount_card(self, name: str, player: dict, kind: str = "enter") -> str | None:
        """渲染并返回坐骑卡 Markdown；任何失败返回 None。"""
        try:
            html = self._mount_card_html(name, player, kind)
        except Exception as e:
            logger.warning(f"[petpark] 坐骑卡 HTML 生成失败：{e}")
            return None
        return self._render_html_image(html, "mountcard", 720, crop=self._card_crop,
                                       win_w=760, win_h=4200)

    def _mount_power(self, player: dict) -> int:
        """当前骑乘坐骑的战力（计入宠物对战）。"""
        m = player.get("active_mount") or ""
        inst = player.get("mounts", {}).get(m)
        return int(inst.get("power", 0)) if inst else 0

    @staticmethod
    def _pick_mount(mounts: dict) -> str | None:
        """挑选骑乘的坐骑：已有坐骑中基础战力最高者。"""
        if not mounts:
            return None
        return max(mounts, key=lambda n: data.MOUNTS.get(n, {}).get("base_power", 0))

    async def _mount_enter_tick(self, qq: str, group_id: str) -> None:
        """玩家在群发言时刷新 last_msg_ts，未骑乘则自动入场并推送入场卡。"""
        player = self.store.get_player(qq, group_id, create=False)
        if not player:
            return
        now = int(time.time())
        player["last_msg_ts"] = now
        if not player.get("mounts") or player.get("active_mount"):
            return  # 无坐骑 / 已在场（仅刷新 last_msg_ts）
        if not self.store.get_group(group_id).get("mount_enabled", True):
            return
        chosen = self._pick_mount(player["mounts"])
        if not chosen:
            return
        player["active_mount"] = chosen
        player["mount_group"] = group_id
        player["mount_enter_ts"] = now
        cfg = data.MOUNTS.get(chosen)
        reward = None
        if cfg:
            reward = random.randint(cfg["reward_min"], cfg["reward_max"])
            self.store.add_currency(player, "积分", reward)
        await self.store.save()
        if player.get("mount_enter_notify", True):
            await self._send_group_text(group_id, self._mount_full_message(chosen, player, "enter", reward=reward))

    async def _mount_idle_tick(self) -> None:
        """后台扫描：在场但超过 30 分钟无消息的玩家自动离场。"""
        now = int(time.time())
        timeout = data.MOUNT_IDLE_TIMEOUT_MIN * 60
        changed = False
        for player in self.store.all_players().values():
            act = player.get("active_mount") or ""
            if not act:
                continue
            if now - player.get("last_msg_ts", 0) < timeout:
                continue
            gid = player.get("mount_group") or ""
            name = act
            player["active_mount"] = ""
            player["mount_group"] = ""
            player["mount_enter_ts"] = 0
            changed = True
            if player.get("mount_leave_notify", True) and gid:
                await self._send_group_text(gid, self._mount_full_message(name, player, "leave"))
        if changed:
            await self.store.save()

    async def _mount_loop(self) -> None:
        while True:
            try:
                await self._mount_idle_tick()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[petpark] 坐骑离场循环异常")
            try:
                await asyncio.sleep(self.MOUNT_IDLE_CHECK_SEC)
            except asyncio.CancelledError:
                break

    # ---------------------------------------------------------------------
    # 背包卡 · 蓝绿游戏面板（H5 -> PNG，无 emoji）
    # ---------------------------------------------------------------------

    def _bag_card_html(self, player: dict) -> str:
        """把背包渲染成双列物品卡 HTML（无 emoji）。"""
        esc = self._menu_esc
        bag = player.get("bag", {})
        items = sorted(bag.items(), key=lambda kv: str(kv[0]))
        total_items = sum(int(c) for c in bag.values())
        if not items:
            body = '<div class="empty">空空如也，去商城选购吧</div>'
        else:
            body = "".join(
                f'<div class="item-slot"><div class="item-icon">{card_theme.item_icon(str(name))}</div><span class="rname">{esc(name)}</span>'
                f'<span class="rcount">{esc(str(count))}</span></div>'
                for name, count in items
            )
        subtitle = "共 %d 种 · 共 %d 件" % (len(items), total_items) if items else "暂无物品"
        return (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<style>{card_theme.stylesheet('bag')}</style></head><body>"
            f'<div class="card">'
            '<div class="masthead"><div><div class="mast-title">随行百宝</div>'
            '<div class="mast-caption">宠物乐园 · 我的背包</div></div></div>'
            f'<div class="bag-summary"><span>物品种类 <strong>{len(items)}</strong></span>'
            f'<span>持有总数 <strong>{total_items}</strong></span></div>'
            f'<div class="inventory">{body}</div>'
            f'<div class="foot">使用物品：发送「使用 物品名」 · 选购物品：发送「商城」</div>'
            f'</div></body></html>'
        )

    def _render_bag_image(self, player: dict) -> str | None:
        """渲染并返回背包卡 Markdown；任何失败返回 None。"""
        try:
            html = self._bag_card_html(player)
        except Exception as e:
            logger.warning(f"[petpark] 背包卡 HTML 生成失败：{e}")
            return None
        return self._render_html_image(html, "bagcard", 720, crop=self._card_crop,
                                       win_w=760, win_h=max(1800, 480 + sum(140 + 26 * (len(str(name)) // 10) for name in player.get("bag", {}))))

    def _official_site_text(self) -> str:
        """官方网站介绍：官方主站 + 绑定宠物指引（QQ Markdown，用 \n\n 分隔避免被吞）。"""
        return "\n".join(
            [
                "## 🌐 宠物乐园 · 官方主站",
                "",
                "🎉 这里是《宠物乐园》**官方主站**，手机/电脑随时可访问，与群内账号数据完全互通。",
                "登录注册**随便弄**（无需邀请码、无需繁琐验证），登录即自动同步你的宠物与资产。",
                "",
                "▶️ **点击直达**：[🎡 立即进入宠物乐园](https://bot.flyyye.cn/)",
                "",
                "**🐾 如何绑定宠物**",
                "> 1. 打开官方主站并登录注册（随意）。",
                "> 2. 进入「个人中心 / 绑定」页面。",
                "> 3. 输入你的**用户ID**（群内发送 `我的信息` 即可查看）。",
                "> 4. 绑定成功后即可在网页查看、操作你的宠物与资产。",
                "",
                "> 💡 记不住用户ID？登录后也可绑定 QQ号（群内发送 `绑定QQ QQ号`）代替，跨群通用。",
                "",
                "❓ 更多玩法请发送 `宠物乐园` 查看。",
            ]
        )

    def _admin_menu_text(self) -> str:
        return "\n".join(
            [
                "## 🛡️ 宠物乐园 · 管理菜单",
                "> 所有「用户ID」参数位均支持直接 **@对方**（如 `加金币 @某人 100`）",
                "",
                "**【群开关】**",
                "- 开启宠物乐园 · 关闭宠物乐园",
                "- 开启宠物跨群 · 关闭宠物跨群",
                "- 设为无限服 · 设为官方服（大管理员设定本群服类型）",
                "> 无限服：宠物神榜/跨群挑战关闭、数据完全群独立、小管理员加币积分无每日上限",
                "",
                "**【货币管理】**（大/小管理员）",
                "- 加金币 用户ID 数量 · 减金币 用户ID 数量",
                "- 加积分 用户ID 数量 · 减积分 用户ID 数量",
                "- 加钻石 用户ID 数量 · 减钻石 用户ID 数量",
                "> 💡 小管理员仅可增减金币/积分，加币有每日额度上限",
                "",
                "**【小管理员】**",
                "- 任命小管理 用户ID · 撤销小管理 用户ID",
                "- 小管理列表（大管理员查看全服）",
                "- 我的管理额度（小管理员查看今日额度）",
                "",
                "**【群授权】**",
                "- 授权状态（查看本群授权状态）",
                "- 授权 卡密（用授权卡激活/续期本群，卡密自带服类型）",
                "- 授权本群 天数 [官方服|无限服]（大管理员直接续期+设定服类型）",
                "",
                "**【群管理】**（群主/群管理员）",
                "- 禁言 @成员 时长（如 10分钟 / 1小时 / 1天，默认 10 分钟）",
                "- 解除禁言 @成员",
                "- 全体禁言（查询当前全员禁言状态）",
                "- 撤回 @成员 [数量]（撤回其最近发送的消息，默认 1 条，最多 10 条）",
                "- 踢出 @成员（移出本群，可一次 @ 多名，最多 20 名）",
                "> ⚠️ 需机器人被设为群管理员；新成员入群/退群会自动推送通知",
            ]
        )

    # =====================================================================
    # 商城
    # =====================================================================
    _CATEGORY_ORDER = ["药品", "道具", "宝石", "材料", "仙丹", "符箓", "其他"]

    def _shop_text(self, which: str) -> str:
        if which == "宠物商城":
            return self._shop_index_text()
        if which == "秘技商城":
            lines = [
                "## 📜 秘技商城",
                "> 购买后发送『使用 秘技名』参悟（需满足等级/智力）",
                "",
            ]
            for n, v in data.SKILLS.items():
                lines.append(
                    f"- **{n}** — {data.ITEMS[n]['price']} 积分　"
                    f"（Lv{v['level_req']}/智力{v['intel_req']}·战力+{v['power']}）"
                )
            return "\n".join(lines)
        if which == "神器商城":
            lines = [
                "## 🗡️ 神器商城",
                "> 购买后发送『佩戴神器 名称』穿戴（需满足等级，飞升可跨级佩戴）",
                "",
            ]
            for n, v in data.ARTIFACTS.items():
                lines.append(
                    f"- **{n}** — {data.ITEMS[n]['price']} 积分　"
                    f"（Lv{v['level_req']}·战力+{v['power']}）"
                )
            return "\n".join(lines)

        # 道具商城（金币 + 积分）/ 钻石商城（钻石）：按币种自动归类
        if which == "钻石商城":
            accepted = (data.CURRENCY_DIAMOND,)
            title = "## 💎 钻石商城"
        else:
            accepted = (data.CURRENCY_COIN, data.CURRENCY_JIFEN)
            title = "## 🏪 道具商城"
        lines = [title, "> 购买方式：`购买 物品名 [数量]`", ""]
        groups: dict[str, list[tuple[str, dict]]] = {}
        for n, it in data.ITEMS.items():
            if it.get("price", 0) <= 0:
                continue
            if it.get("category") in ("秘技书", "神器"):
                continue  # 由秘技/神器商城专属展示
            if n == "变种卡":
                continue  # 宠物市场专属（品质卡/变种卡）
            if it.get("currency") not in accepted:
                continue
            groups.setdefault(it.get("category", "其他"), []).append((n, it))
        for cat in sorted(
            groups,
            key=lambda c: (
                self._CATEGORY_ORDER.index(c) if c in self._CATEGORY_ORDER else 99
            ),
        ):
            lines.append(f"**【{cat}】**")
            for n, it in sorted(groups[cat], key=lambda kv: kv[1]["price"]):
                lines.append(f"- **{n}** — {it['price']} {it['currency']}")
            lines.append("")
        return "\n".join(lines).rstrip()

    def _shop_index_text(self) -> str:
        return "\n".join([
            "## 🛒 宠物商城",
            "> 发送对应指令进入商城：",
            "- **宠物商城** — 商城总览（当前）",
            "- **道具商城** — 金币/积分道具",
            "- **钻石商城** — 钻石道具（精力瓶·五系属性符）",
            "- **秘技商城** — 秘技书（最低 2 万积分起）",
            "- **神器商城** — 神器（最低 2 万积分起）",
            "- **宠物市场** — 品质卡 / 变种卡",
            "",
            "> 购买：`购买 物品名 [数量]` · 市场：`购买市场 物品名`",
        ])

    def _pet_market_text(self) -> str:
        lines = [
            "## 🐾 宠物市场 / 宠物专域",
            "> 现已改卖 **品质卡** 与 **变种卡**（不再按物种直售宠物）。",
            "> 购买方式：`购买市场 物品名 [数量]`（例如：`购买市场 史诗卡 10`，默认买 1）",
            "",
            "**【品质卡】**",
            "> 可 `使用 XXX卡 召唤` 随机召唤同品质宠物，或 `使用 XXX卡 宠物名` 给指定宠物提升品质。",
        ]
        for card, price in data.PET_MARKET_CARDS.items():
            lines.append(f"- **{card}** — {price} 积分")
        sc = data.SPECIES_CHANGE_CARD
        lines.append("")
        lines.append("**【变种卡】**")
        lines.append(f"- **{sc['name']}** — {sc['price']} 积分　`使用 {sc['name']} 宠物名` 随机改变该宠物种类（保留等级/品质/属性）")
        lines.append("")
        lines.append("> 注：圣灵/洪荒/创世/混沌为活动/定制限定品质，不在市场出售。")
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
        cd = self._cooldown_block(player, "砸蛋", "砸蛋", scope="player")
        if cd:
            return cd
        quality = self._roll_quality()
        shard_name = f"{quality}碎片"
        shard_n = random.randint(1, 2)
        self.store.add_item(player, shard_name, shard_n)
        lines = [f"💥 **砸蛋成功！**\n获得 **{shard_name} ×{shard_n}**"]
        no_pet = not (player.get("pets") or player.get("pet"))
        if no_pet:
            # 新手保护：一个宠物都没时，砸蛋必得 1 张宠物卡（召唤第一只宠）
            self.store.add_item(player, "宠物卡", 1)
            lines.append("🎴 **新手保护：必得 宠物卡 ×1**（使用 `使用 宠物卡` 获得你的第一只宠物）！")
        elif random.random() < data.PET_CARD_DROP_CHANCE:
            self.store.add_item(player, "宠物卡", 1)
            lines.append("🎴 附带掉落 **宠物卡 ×1**（使用 `使用 宠物卡` 随机获得一只宠物）！")
        lines.append(f"> {data.FRAGMENT_TO_CARD} 片可兑换 1 张【{quality}卡】，卡片可召唤宠物或提升品质。")
        self.store.set_player_cooldown(player, "砸蛋", data.EGG_COOLDOWN)
        return "\n".join(lines)

    def _smash_ten(self, player: dict) -> str:
        """砸蛋十连：一次抽 10 次品质碎片（不再出宠物）。与单发共享同一冷却键「砸蛋」（互斥）。"""
        need = 10
        cd = self._cooldown_block(player, "砸蛋", "砸蛋", scope="player")
        if cd:
            return cd
        shard_tot: dict[str, int] = {}
        card_cnt = 0
        for _ in range(need):
            quality = self._roll_quality()
            cnt = random.randint(1, 2)
            self.store.add_item(player, f"{quality}碎片", cnt)
            shard_tot[quality] = shard_tot.get(quality, 0) + cnt
            if random.random() < data.PET_CARD_DROP_CHANCE:
                self.store.add_item(player, "宠物卡", 1)
                card_cnt += 1
        # 新手保护：一个宠物都没时，十连砸蛋必得至少 1 张宠物卡
        if not (player.get("pets") or player.get("pet")) and card_cnt == 0:
            self.store.add_item(player, "宠物卡", 1)
            card_cnt += 1
        self.store.set_player_cooldown(player, "砸蛋", data.EGG_TEN_COOLDOWN)
        lines = ["💥 **砸蛋十连！**"]
        for q, n in shard_tot.items():
            lines.append(f"- **{q}碎片 ×{n}**")
        if card_cnt:
            lines.append(f"- 🎴 **宠物卡 ×{card_cnt}**（使用 `使用 宠物卡` 随机获得一只宠物）")
        lines.extend([
            "",
            f"> {data.FRAGMENT_TO_CARD} 片可兑换 1 张品质卡，卡片可召唤宠物或提升品质。",
            "> 十连冷却 25 分钟，与单发砸蛋共享冷却（其一进行中则另一不可用）。",
        ])
        return "\n".join(lines)

    def _exchange_fragment(self, player: dict, tokens: list[str]) -> str:
        """碎片转卡：同品质 10 片兑换 1 张该品质卡。"""
        if len(tokens) < 2:
            avg = "、".join(data.QUALITIES)
            return f"用法：碎片转卡 <品质>（例如：碎片转卡 普通）\n当前品质：{avg}"
        quality = tokens[1]
        fragment = f"{quality}碎片"
        card = f"{quality}卡"
        if quality not in data.QUALITIES or fragment not in data.ITEMS:
            return f"没有『{fragment}』这种碎片。可用品质：{'、'.join(data.QUALITIES)}。"
        need = data.FRAGMENT_TO_CARD
        have = player.get("bag", {}).get(fragment, 0)
        if have < need:
            return f"兑换 1 张【{card}】需要 {fragment} ×{need}，你当前只有 {have} 片。"
        self.store.remove_item(player, fragment, need)
        self.store.add_item(player, card, 1)
        return f"✅ 兑换成功！消耗 {fragment} ×{need}，获得 **{card}** ×1。"

    @staticmethod
    def _resolve_pet_target(player: dict, key: str) -> dict | None:
        """按宠物名/昵称/物种（或 1-based 序号）解析宠物。找不到返回 None。"""
        pets = player.get("pets", [])
        if not pets:
            return None
        key = key.strip()
        for p in pets:
            name = str(p.get("nickname", "")).strip()
            if name and (name == key or name.lower() == key.lower()):
                return p
        for p in pets:
            if str(p.get("species", "")).strip() == key:
                return p
        if key.isdigit():
            idx = int(key) - 1
            if 0 <= idx < len(pets):
                return pets[idx]
        return None

    def _buy_market_item(self, player: dict, tokens: list[str]) -> str:
        """宠物市场购买品质卡/变种卡。品质卡可召唤宠物或升品质，变种卡用于换物种。

        支持批量：`购买市场 物品名 数量`（默认 1）。
        """
        if len(tokens) < 2:
            return "用法：购买市场 物品名 [数量]（例如：购买市场 史诗卡 10 / 购买市场 变种卡）"
        name = tokens[1]
        count = self._parse_count(tokens, 2)
        jifen = self.store.get_currency(player, "积分")
        # 品质卡
        if name in data.PET_MARKET_CARDS:
            price = data.PET_MARKET_CARDS[name]
            cost = price * count
            if jifen < cost:
                return f"购买 {count} 张『{name}』需 {cost} 积分，积分不足（当前 {jifen}）。"
            self.store.add_currency(player, "积分", -cost)
            self.store.add_item(player, name, count)
            return (
                f"✅ **购买成功！** 花费 {cost} 积分，获得 **{name}** ×{count}。\n"
                f"> 发送 `使用 {name} 召唤` 召唤同品质宠物，或 `使用 {name} 宠物名` 提升品质。"
            )
        # 变种卡
        sc = data.SPECIES_CHANGE_CARD
        if name == sc["name"]:
            price = sc["price"]
            cost = price * count
            if jifen < cost:
                return f"购买 {count} 张『{name}』需 {cost} 积分，积分不足（当前 {jifen}）。"
            self.store.add_currency(player, "积分", -cost)
            self.store.add_item(player, name, count)
            return (
                f"✅ **购买成功！** 花费 {cost} 积分，获得 **{name}** ×{count}。\n"
                f"> 发送 `使用 {name} 宠物名` 随机改变该宠物种类（保留等级/品质/属性）。"
            )
        return (
            f"宠物市场没有『{name}』。当前在售：{'、'.join(data.PET_MARKET_CARDS)}、{sc['name']}。"
            "发送『宠物市场』查看。"
        )

    def _compose_quality_card(self, player: dict, tokens: list[str]) -> str:
        """品质卡合成：10 张低一级卡合成 1 张高一级卡。"""
        if len(tokens) < 2:
            available = "、".join(data.QUALITY_CARD_UPGRADE.keys()) or "暂无可合成卡片"
            return (
                "用法：`合成卡 目标卡名`（例如：`合成卡 圣灵卡`）\n"
                f"当前可合成：{available}\n"
                "规则：通常 10 张低一级品质卡合成 1 张高一级品质卡；顶级【混沌卡】需 20 张【创世卡】。"
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

    def _batch_exchange_fragments(self, player: dict) -> str:
        """一键合成品质碎片：把所有品质碎片按 10:1 批量兑换成同品质卡。"""
        bag = player.get("bag", {})
        need = data.FRAGMENT_TO_CARD
        done: list[str] = []
        for q in data.QUALITIES:
            frag = f"{q}碎片"
            card = f"{q}卡"
            have = bag.get(frag, 0)
            if have < need:
                continue
            n = have // need
            consume = n * need
            self.store.remove_item(player, frag, consume)
            self.store.add_item(player, card, n)
            note = "" if have % need == 0 else f"（余 {have % need} 片）"
            done.append(f"**{frag}** ×{consume} → **{card}** ×{n}{note}")
        if not done:
            bag_frags = [f"{q}碎片" for q in data.QUALITIES if bag.get(f"{q}碎片", 0) > 0]
            if not bag_frags:
                return "你背包里没有任何『品质碎片』，无法合成。"
            hold = "、".join(f"{k}×{bag.get(k, 0)}" for k in bag_frags)
            return f"碎片数量不足，无法兑换。每种品质需 {need} 片，当前持有：{hold}。"
        return "✅ **一键合成品质碎片完成！**\n" + "\n".join(f"- {d}" for d in done)

    def _batch_compose_cards(self, player: dict) -> str:
        """一键合成品质卡：从低品质卡起贪心向上级联合成，能升多高升多高。"""
        bag = player.get("bag", {})
        # 各品质卡当前数量（会在合成中逐步推进）
        has = {f"{q}卡": bag.get(f"{q}卡", 0) for q in data.QUALITIES}
        done: list[str] = []
        for i in range(len(data.QUALITIES) - 1):
            low_card = f"{data.QUALITIES[i]}卡"
            high_card = f"{data.QUALITIES[i + 1]}卡"
            if high_card not in data.QUALITY_CARD_UPGRADE:
                continue
            need = data.QUALITY_CARD_UPGRADE[high_card][1]
            have = has.get(low_card, 0)
            n = have // need
            if n <= 0:
                continue
            consume = n * need
            has[low_card] -= consume
            has[high_card] = has.get(high_card, 0) + n
            note = f"（余 {has[low_card]} 张）" if has[low_card] else ""
            done.append(f"**{low_card}** ×{consume} → **{high_card}** ×{n}{note}")
        if not done:
            any_cards = [f"{q}卡" for q in data.QUALITIES if has.get(f"{q}卡", 0) > 0]
            if not any_cards:
                return "你背包里没有任何『品质卡』，无法合成。"
            hold = "、".join(f"{c}×{has[c]}" for c in any_cards)
            return f"品质卡数量不足，无法级联合成（需 10 张低级卡换 1 张高级卡，创世→混沌需 20 张）。当前持有：{hold}。"
        # 写回背包（按净变化：同一种卡既被消耗又产出时取差值）
        for q in data.QUALITIES:
            card = f"{q}卡"
            orig = bag.get(card, 0)
            final = has.get(card, 0)
            if final > orig:
                self.store.add_item(player, card, final - orig)
            elif final < orig:
                self.store.remove_item(player, card, orig - final)
        return "✅ **一键合成品质卡完成！**\n" + "\n".join(f"- {d}" for d in done)

    # =====================================================================
    # 宠物查看 / 管理
    # =====================================================================
    def _need_pet(self, player: dict) -> dict | None:
        p = player.get("pet")
        if p is not None:
            self._cap_skills(player, p)
        return p

    def _cap_skills(self, player: dict, p: dict) -> list[str]:
        """把宠物秘技限定为战力最高的 data.SKILLS_MAX 个，超出的秘技书退回背包。

        返回被退回（移出）的秘技名列表。幂等：已达标则返回空列表。
        """
        skills = list(p.get("skills", []))
        if len(skills) <= data.SKILLS_MAX:
            return []
        skills.sort(key=lambda s: data.SKILLS.get(s, {}).get("power", 0), reverse=True)
        kept = skills[: data.SKILLS_MAX]
        removed = skills[data.SKILLS_MAX:]
        p["skills"] = kept
        for sk in removed:
            self.store.add_item(player, sk, 1)
        return removed

    def _skills_cap_check(self, player: dict, p: dict, name: str, power: int) -> str | None:
        """参悟前检查秘技上限：宠物已满且新秘技不优于当前最低者时返回拒绝文案，否则返回 None。

        会先归一化历史超过上限的秘技（多出的秘技书退回背包），再做判断。
        """
        self._cap_skills(player, p)
        skills = p.get("skills", [])
        if len(skills) < data.SKILLS_MAX:
            return None
        worst = min(skills, key=lambda k: data.SKILLS.get(k, {}).get("power", 0))
        worst_power = data.SKILLS.get(worst, {}).get("power", 0)
        if power <= worst_power:
            return (
                f"你的宠物已佩戴 {data.SKILLS_MAX} 个秘技，『{name}』(战力 +{power}) "
                f"未超过当前最低的『{worst}』(战力 +{worst_power})，秘技书保留在背包；"
                f"可先『遗忘秘技』腾出位置。"
            )
        return None

    @staticmethod
    def _match_species(sub: str) -> str | None:
        """匹配宠物品种名：精确 → 前缀唯一 → 包含唯一；找不到返回 None。"""
        sub = (sub or "").strip()
        if not sub:
            return None
        if sub in data.SPECIES_NAMES:
            return sub
        pre = [s for s in data.SPECIES_NAMES if s.startswith(sub)]
        if len(pre) == 1:
            return pre[0]
        cont = [s for s in data.SPECIES_NAMES if sub in s]
        if len(cont) == 1:
            return cont[0]
        return None

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
        mp = self._mount_power(player)
        md = self._render_pet_image(p, mp)
        if md:
            return ("我的宠物", md)
        # 渲染万一失败：退回文本卡，避免玩家看不到信息
        return petmod.render_pet(p, mp)

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
        mp = self._mount_power(tp)
        md = self._render_pet_image(pet, mp)
        if md:
            return ("宠物侦查", md)
        return petmod.render_pet(pet, mp)

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
        return f"🎁 已将『{removed['nickname']}』赠送给 `{self._display_uid(target)}`。"

    # =====================================================================
    # 坐骑系统：指令处理
    # =====================================================================
    def _mount_help(self) -> str:
        """坐骑玩法帮助。"""
        return (
            "## 🐎 坐骑系统\n"
            "拥有坐骑后，在本群发送任意消息即自动骑乘登场，入场获得一次积分奖励；"
            "**30 分钟无消息自动离场**，坐骑战力计入宠物对战。\n"
            "**常用指令**\n"
            "- 坐骑列表 · 我的坐骑 · 坐骑市场 · 购买坐骑 名称\n"
            "- 坐骑图鉴 名称 · 骑乘坐骑 名称 · 坐骑升级\n"
            "- 赠送坐骑 用户ID · 丢弃坐骑 名称\n"
            "- 开启/关闭坐骑系统 · 开启/关闭入场/离场提示\n"
            "- 定制坐骑（专属名称 + GIF 立绘，联系客服/群主）"
        )

    def _mount_group_toggle(self, group_id: str, cmd: str, event) -> str:
        """群级开关（需群主/管理员/插件管理员）。"""
        if not self._is_admin(event):
            return "仅管理员可开关坐骑系统。"
        group = self.store.get_group(group_id)
        on = cmd.startswith("开启")
        group["mount_enabled"] = bool(on)
        return f"本群坐骑系统已{'开启' if on else '关闭'}。"

    def _mount_notify(self, player: dict, kind: str, cmd: str) -> str:
        """玩家级入场/离场提示开关。"""
        on = cmd.startswith("开启")
        if kind == "enter":
            player["mount_enter_notify"] = bool(on)
            label = "入场"
        else:
            player["mount_leave_notify"] = bool(on)
            label = "离场"
        return f"坐骑{label}提示已{'开启' if on else '关闭'}。"

    def _mount_list(self, player: dict) -> str:
        """坐骑列表：已拥有坐骑清单（纯文本 Markdown，不带图片）。"""
        mounts = player.get("mounts") or {}
        if not mounts:
            return "你还没有坐骑。发送『坐骑市场』购买，或『坐骑图鉴』查看全部。"
        act = player.get("active_mount") or ""
        lines = [f"已拥有 {len(mounts)} 只坐骑："]
        for n, info in mounts.items():
            st = "（当前骑乘）" if n == act else ""
            mark = "★" * data.MOUNTS.get(n, {}).get("stars", 1)
            lv = int(info.get("level", 1))
            lines.append(f"- {n} {mark} · Lv.{lv} · 战力 {self._short_num(info.get('power', 0))}{st}")
        lines.append("\n发送『骑乘坐骑 名称』切换骑乘，『坐骑升级』提升战力，『我的坐骑』看当前骑乘详情。")
        return "\n".join(lines)

    def _my_mounts(self, player: dict) -> str:
        """我的坐骑：显示当前骑乘坐骑的详细资料（含图片）；未骑乘则提示。"""
        mounts = player.get("mounts") or {}
        if not mounts:
            return "你还没有坐骑。发送『坐骑市场』购买，或『坐骑图鉴』查看全部。"
        act = player.get("active_mount") or ""
        if not act or act not in mounts:
            return ("当前未骑乘坐骑。发送『坐骑列表』查看全部坐骑；"
                    "有坐骑时在本群发言即自动登场，或『骑乘坐骑 名称』手动骑乘。")
        name = act
        txt = (f"## 🐎 {name}（当前骑乘）\n\n"
               f"{self._mount_info_text(name, player, 'my')}")
        img = self._mount_image_md(name)
        if img:
            return (txt, img)
        return txt

    def _mount_ride(self, player: dict, group_id: str, tokens: list[str]) -> str:
        """切换骑乘；无参自动骑最高档。"""
        mounts = player.get("mounts") or {}
        if not mounts:
            return "你还没有坐骑。发送『坐骑市场』购买。"
        name = self._arg(tokens, 1)
        if not name:
            name = self._pick_mount(mounts)
        if not name or name not in mounts:
            return "指定坐骑不存在。发送『我的坐骑』查看。"
        now = int(time.time())
        player["active_mount"] = name
        player["mount_enter_ts"] = now
        player["last_msg_ts"] = now
        player["mount_group"] = group_id
        txt = (f"已骑乘『{name}』，战力计入对战胜负。\n\n"
               f"{self._mount_info_text(name, player, 'my')}")
        img = self._mount_image_md(name)
        if img:
            return (txt, img)
        return txt

    def _gift_mount(self, player: dict, group_id: str, tokens: list[str]) -> str:
        """赠送坐骑 用户ID [坐骑名]：仿赠送宠物，走转让限制。"""
        target = self._arg(tokens, 1)
        if not target:
            return "用法：赠送坐骑 用户ID（可选坐骑名，默认当前骑乘/最高档）"
        if target == player["qq"]:
            return "不能赠送给自己。"
        mounts = player.get("mounts") or {}
        if not mounts:
            return "你还没有坐骑可赠送。"
        tp, err = self._find_target(group_id, target)
        if err:
            return err
        name = self._arg(tokens, 2)
        if not name:
            name = player.get("active_mount") or self._pick_mount(mounts)
        if not name or name not in mounts:
            return f"你没有『{name}』。发送『我的坐骑』查看。"
        bank_block = self._bank_block_check(player, "gift")
        if bank_block:
            return bank_block
        limit_err, _ = self._check_transfer_limit(player, tp, group_id, 1, "mount")
        if limit_err:
            return limit_err
        inst = mounts.pop(name)
        if player.get("active_mount") == name:
            player["active_mount"] = ""
            player["mount_group"] = ""
            player["mount_enter_ts"] = 0
        tp.setdefault("mounts", {})[name] = inst
        return f"🎁 已将坐骑『{name}』赠送给 `{self._display_uid(target)}`。"

    def _mount_discard(self, player: dict, tokens: list[str]) -> str:
        """丢弃坐骑 坐骑名。"""
        mounts = player.get("mounts") or {}
        if not mounts:
            return "你还没有坐骑。"
        name = self._arg(tokens, 1)
        if not name or name not in mounts:
            return "用法：丢弃坐骑 坐骑名。发送『我的坐骑』查看。"
        mounts.pop(name)
        if player.get("active_mount") == name:
            player["active_mount"] = ""
            player["mount_group"] = ""
            player["mount_enter_ts"] = 0
        return f"已丢弃坐骑『{name}』。"

    def _mount_market_text(self) -> str:
        """坐骑市场：在售 12 款坐骑列表。"""
        lines = ["## 🐎 坐骑市场", "发送『购买坐骑 名称』入手，『坐骑图鉴 名称』看详情："]
        for name in data.MOUNTS_ORDER:
            cfg = data.MOUNTS[name]
            star = "★" * cfg["stars"]
            lines.append(f"- {name}（{star} · 初始战力 {self._short_num(cfg['base_power'])} · "
                         f"{self._short_num(cfg['price'])} 积分）")
        lines.append("\n> 拥有即自动登场：入场奖励积分，30 分钟无消息自动退场。")
        return "\n".join(lines)

    def _buy_mount(self, player: dict, tokens: list[str]) -> str:
        """购买坐骑 坐骑名：扣积分，获得坐骑 + 号牌。"""
        name = self._arg(tokens, 1)
        if not name:
            return "用法：购买坐骑 坐骑名（例如：购买坐骑 踏风玄鹿）"
        if name not in data.MOUNTS:
            return f"没有『{name}』。发送『坐骑市场』查看在售坐骑。"
        cfg = data.MOUNTS[name]
        jifen = self.store.get_currency(player, "积分")
        if jifen < cfg["price"]:
            return (f"购买『{name}』需 {self._short_num(cfg['price'])} 积分，"
                    f"积分不足（当前 {self._short_num(jifen)}）。")
        if name in (player.get("mounts") or {}):
            return f"你已经拥有『{name}』。"
        self.store.add_currency(player, "积分", -cfg["price"])
        player.setdefault("mounts", {})[name] = {
            "level": 1,
            "power": cfg["base_power"],
            "plate": self.store.next_mount_plate(),
            "obtained": time.time(),
            "custom": False,
        }
        head = (f"✅ **入手成功！** 花费 {self._short_num(cfg['price'])} 积分，获得坐骑 **{name}**。\n"
                f"> 在本群发送消息即自动骑乘登场；入场奖励见『坐骑图鉴』。")
        img = self._mount_image_md(name)
        if img:
            return (head, img)
        return head

    def _mount_codex(self, player: dict, tokens: list[str]) -> str:
        """坐骑图鉴：无参枚举全部；带参查详情（已拥有则渲染卡片）。"""
        if len(tokens) < 2:
            lines = ["## 📖 坐骑图鉴", "发送『坐骑图鉴 坐骑名』查看详情："]
            for name in data.MOUNTS_ORDER:
                cfg = data.MOUNTS[name]
                star = "★" * cfg["stars"]
                lines.append(f"- {name}（{star} · 初始 {self._short_num(cfg['base_power'])} 战力 · "
                             f"入场 {self._short_num(cfg['reward_min'])}~{self._short_num(cfg['reward_max'])} 积分）")
            lines.append("\n> 难度越高奖励越高，共 12 款。发送『坐骑市场』购买。")
            return "\n".join(lines)
        name = tokens[1]
        if name not in data.MOUNTS:
            return f"图鉴没有『{name}』。发送『坐骑图鉴』查看全部。"
        cfg = data.MOUNTS[name]
        own = name in (player.get("mounts") or {})
        head = (f"## 📖 『{name}』\n{cfg['desc']}\n"
                f"星级 {'★' * cfg['stars']} · 初始战力 {self._short_num(cfg['base_power'])} · "
                f"价值 {self._short_num(cfg['value'])} · 售价 {self._short_num(cfg['price'])} 积分\n"
                f"入场奖励 {self._short_num(cfg['reward_min'])}~{self._short_num(cfg['reward_max'])} 积分"
                f"（{'已拥有' if own else '未拥有'}）")
        img = self._mount_image_md(name)
        if img:
            return (head, img)
        return head

    def _mount_custom(self, player: dict) -> str:
        """定制坐骑：预留说明入口（本轮不接后台申请/录入）。"""
        return ("## 🎨 定制坐骑\n"
                "定制坐骑需联系客服或群主申请：自定义名称 + 专属 GIF 立绘。\n"
                "**起步 100 万积分**：第 1 只 100 万、第 2 只 500 万、第 3 只 1000 万、"
                "第 4 只 2000 万，此后每只 +1000 万，以此类推。\n"
                "定制完成同样：入场奖励积分、进群自动登场、战力计入对战。")

    def _mount_upgrade(self, player: dict, tokens: list[str]) -> str:
        """坐骑升级：耗 5000 钻石，Lv+1，战力 +500~1000 随机。"""
        mounts = player.get("mounts") or {}
        if not mounts:
            return "你还没有坐骑。发送『坐骑市场』购买。"
        name = self._arg(tokens, 1)
        if not name or name not in mounts:
            name = player.get("active_mount") or self._pick_mount(mounts)
        if not name or name not in mounts:
            return "指定坐骑不存在。发送『我的坐骑』查看。"
        cfg = data.MOUNTS[name]
        cost = data.MOUNT_UPGRADE_COST_DIAMOND
        diamond = self.store.get_currency(player, "钻石")
        if diamond < cost:
            return f"升级『{name}』需 {cost} 钻石，钻石不足（当前 {diamond}）。"
        inst = mounts[name]
        gain = random.randint(data.MOUNT_UPGRADE_POWER_MIN, data.MOUNT_UPGRADE_POWER_MAX)
        inst["level"] = inst.get("level", 1) + 1
        inst["power"] = inst.get("power", cfg["base_power"]) + gain
        self.store.add_currency(player, "钻石", -cost)
        return (f"✅ **坐骑升级成功！**\n『{name}』升至 Lv.{inst['level']}"
                f"（+{gain} 战力）→ 当前战力 {inst['power']}。\n"
                f"本轮消耗 {cost} 钻石，战力已计入对战胜负。")

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

    def _resolve_pet_index(self, player: dict, key: str) -> int | None:
        """按宠物名/昵称/物种（或 1-based 序号）返回宠物索引，找不到返回 None。"""
        pets = player.get("pets", [])
        if not pets:
            return None
        key = key.strip()
        for i, p in enumerate(pets):
            name = str(p.get("nickname", "")).strip()
            if name and (name == key or name.lower() == key.lower()):
                return i
        for i, p in enumerate(pets):
            if str(p.get("species", "")).strip() == key:
                return i
        if key.isdigit():
            idx = int(key) - 1
            if 0 <= idx < len(pets):
                return idx
        return None

    def _refine_pet(self, player: dict, tokens: list[str]) -> str:
        """炼化：消耗积分，将宠物或「宠物卡」化作对应品质的卡/碎片。

        20% 概率炼出对应品质的品质卡；80% 概率炼出对应品质碎片（随机 3-8 个）。
        - `炼化宠物`：默认炼化当前活跃宠物，可用姓名/昵称/物种/序号指定其他宠物。
        - `炼化宠物 宠物卡`：改炼化背包里的「宠物卡」（品质随机结算）。
        """
        if len(tokens) >= 2 and tokens[1] == "宠物卡":
            return self._refine_pet_card(player)
        if not self._need_pet(player):
            return "你还没有宠物，无法炼化。"
        if len(tokens) >= 2:
            idx = self._resolve_pet_index(player, tokens[1])
            if idx is None:
                return f"找不到宠物『{tokens[1]}』，发送『宠物列表』查看所有宠物及序号。"
        else:
            idx = player.get("active_pet", 0)
        if idx is None or not (0 <= idx < len(player.get("pets", []))):
            return "炼化失败，宠物数据异常。"
        target = player["pets"][idx]
        if target.get("locked"):
            return f"🔒 『{target.get('nickname', '?')}』已锁定，无法炼化。如需炼化请先发送『解锁宠物』。"
        if len(player.get("pets", [])) <= 1:
            return "⚠️ 这是你最后一只宠物，不能炼化（炼化会消耗宠物）。请先获取新宠物再炼化。"
        cost = data.REFINE_COST
        if self.store.get_currency(player, "积分") < cost:
            return f"炼化需要 **{cost} 积分**，当前积分不足。"
        quality = target.get("quality", "普通") or "普通"
        nick = target.get("nickname", "?")
        self.store.add_currency(player, "积分", -cost)
        removed = self._remove_pet(player, idx)
        if not removed:
            self.store.add_currency(player, "积分", cost)  # 回滚
            return "炼化失败，宠物移除异常。"
        if random.random() < data.REFINE_CARD_CHANCE:
            card = f"{quality}卡"
            self.store.add_item(player, card, 1)
            out = f"🎴 **炼化成功！**『{nick}』化作 **{card} ×1**！"
            hint = f"> 【{card}】可召唤同品质宠物，或用于提升品质。"
        else:
            frag = f"{quality}碎片"
            n = random.randint(*data.REFINE_FRAGMENT_RANGE)
            self.store.add_item(player, frag, n)
            out = f"🧩 **炼化成功！**『{nick}』化作 **{frag} ×{n}**。"
            hint = f"> {data.FRAGMENT_TO_CARD} 片【{frag}】可兑换 1 张【{quality}卡】。"
        return f"{out}\n\n💠 消耗 **{cost} 积分**。\n{hint}"

    def _refine_pet_card(self, player: dict) -> str:
        """炼化「宠物卡」（神秘卡）：品质随机结算，20% 出对应品质卡，80% 出对应品质碎片 3-8。"""
        name = "宠物卡"
        if not self.store.has_item(player, name, 1):
            return f"背包里没有『{name}』，无法炼化。"
        cost = data.REFINE_COST
        if self.store.get_currency(player, "积分") < cost:
            return f"炼化需要 **{cost} 积分**，当前积分不足。"
        self.store.add_currency(player, "积分", -cost)
        self.store.remove_item(player, name, 1)
        q = self._roll_quality()
        if random.random() < data.REFINE_CARD_CHANCE:
            card = f"{q}卡"
            self.store.add_item(player, card, 1)
            out = f"🎴 **炼化成功！**『{name}』化作 **{card} ×1**！"
            hint = f"> 【{card}】可召唤同品质宠物，或用于提升品质。"
        else:
            frag = f"{q}碎片"
            n = random.randint(*data.REFINE_FRAGMENT_RANGE)
            self.store.add_item(player, frag, n)
            out = f"🧩 **炼化成功！**『{name}』化作 **{frag} ×{n}**。"
            hint = f"> {data.FRAGMENT_TO_CARD} 片【{frag}】可兑换 1 张【{q}卡】。"
        return f"{out}\n\n💠 消耗 **{cost} 积分**。\n{hint}"

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
            bp = petmod.battle_power(pet) + self._mount_power(player)
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
        mp = self._mount_power(player)
        md = self._render_pet_image(p, mp)
        if md:
            return ("宠物信息", md)
        return petmod.render_pet(p, mp)

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
        if p.get("talent") == "起死回生":
            p["status"] = "正常"
            p["hp"] = p["hp_max"]
            p["mood"] = max(1, p["mood"])
            return f"『{p['nickname']}』已满血复活！（天赋·起死回生免费）"
        if not self.store.remove_item(player, "九转还魂丹"):
            return "复活需要『九转还魂丹』（可在商城购买），或觉醒『起死回生』天赋免费复活。"
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
                    extra = f"\n伴侣 {self._display_uid(p['love_target'])} 的好感度也 +50。"
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
            return f"商城没有『{name}』。发送『宠物商城』查看商城总览。"
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
        # 自动修炼卡：玩家级别效果，落地到当前宠物（加「自动修炼权限天数」）
        if it_check and it_check.get("effect", {}).get("add_cultivation_days"):
            if not self.store.has_item(player, name):
                return f"背包里没有『{name}』。"
            count = self._parse_count(tokens, 2)
            if not self.store.has_item(player, name, count):
                return f"背包里『{name}』数量不足。"
            p = self._need_pet(player)
            if not p:
                return "你没有宠物，无法使用『自动修炼卡』。"
            if p.get("custom"):
                return "定制宠物已永久享有自动修炼权限，无需使用此卡。"
            days = it_check["effect"]["add_cultivation_days"] * count
            now = int(time.time())
            ac = player.setdefault("auto_cultivation", {"card_until": 0})
            cur = int(ac.get("card_until", 0) or 0)
            base = cur if cur > now else now
            ac["card_until"] = base + days * 86400
            self.store.remove_item(player, name, count)
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(ac["card_until"]))
            return (
                f"🧘 使用『{name}』x{count}：自动修炼权限 +{days} 天！\n"
                f"> 有效期至 **{when}**\n"
                f"> 发送『开启自动修炼』即可开始挂机修炼。"
            )
        # 宠物定制卡：解锁主宠「定制」权限（自定义名称/图片），晋升混沌并加「定制」标签。
        if it_check and it_check.get("effect", {}).get("custom_pet"):
            if not self.store.has_item(player, name):
                return f"背包里没有『{name}』。"
            p = self._need_pet(player)
            if not p:
                return "你没有宠物，无法使用『宠物定制卡』。"
            if p.get("custom"):
                return "该宠物已解锁「定制」权限，无需重复使用。"
            p["custom"] = True
            ok, msg = petmod.upgrade_quality(p, "混沌")
            if not ok:
                return msg
            self.store.add_pet_tag(p, "定制")
            self.store.remove_item(player, name, 1)
            return (
                f"🎨 使用『{name}』：主宠已解锁**定制权限**（可自定义名称/图片）！\n"
                "> 品质已晋升为 **【混沌】**，并附带「定制」专属标签。"
            )
        # 宠物卡：召唤出随机品质+随机物种的宠物。召唤无需已有宠物，故须在 _need_pet 门槛前处理。
        if it_check and it_check.get("effect", {}).get("summon_pet_card"):
            if not self.store.has_item(player, name):
                return f"背包里没有『{name}』。"
            sub = tokens[2].strip() if len(tokens) > 2 else ""
            if sub not in ("", "召唤", "随机", "随机召唤", "开卡", "开"):
                return f"『{name}』使用方式：`使用 {name}`，随机获得一只宠物。"
            slots = player.get("pet_slots", data.PET_SLOTS_DEFAULT)
            if len(player.get("pets", [])) >= slots:
                return (
                    f"宠物席位已满（{len(player['pets'])}/{slots}），无法召唤新宠物。\n"
                    "请先 `放生宠物` 或使用 `宠物席位卡` 扩容。"
                )
            q = self._roll_quality()
            species = random.choice(data.SPECIES_NAMES)
            new_p = petmod.new_pet(species, q)
            if not self._add_pet(player, new_p):
                return "召唤失败，席位异常。"
            self.store.remove_item(player, name, 1)
            return (
                f"🎴 **召唤成功！** 消耗 {name} ×1，获得 【{q}】品质的 **{species}**！\n"
                "> 发送 `我的宠物` 查看详情。"
            )
        # 品质卡（普通卡~混沌卡）：双用途 —— ①「召唤」该品质随机宠物；②对指定宠物升品质。
        # 召唤不需要已有宠物，故必须在此分支处理（在 _need_pet 门槛之前）。
        if it_check and it_check.get("effect", {}).get("upgrade_quality"):
            if not self.store.has_item(player, name):
                return f"背包里没有『{name}』。"
            target_q = it_check["effect"]["upgrade_quality"]
            sub = tokens[2].strip() if len(tokens) > 2 else ""
            if sub in ("召唤", "随机", "随机召唤", "开卡", "开"):
                slots = player.get("pet_slots", data.PET_SLOTS_DEFAULT)
                if len(player.get("pets", [])) >= slots:
                    return (
                        f"宠物席位已满（{len(player['pets'])}/{slots}），无法召唤新宠物。\n"
                        "请先 `放生宠物` 或使用 `宠物席位卡` 扩容。"
                    )
                species = random.choice(data.SPECIES_NAMES)
                new_p = petmod.new_pet(species, target_q)
                if not self._add_pet(player, new_p):
                    return "召唤失败，席位异常。"
                self.store.remove_item(player, name, 1)
                return (
                    f"🎴 **召唤成功！** 消耗 {name} ×1，获得 【{target_q}】品质的 **{species}**！\n"
                    "> 发送 `我的宠物` 查看详情。"
                )
            # 指定宠物升品质：未指定 → 当前宠物；指定宠物名/序号 → 按名查找
            p = self._need_pet(player)
            if sub and not sub.isdigit():
                tp = self._resolve_pet_target(player, sub)
                if tp:
                    p = tp
                else:
                    return f"没有找到名为『{sub}』的宠物。发送《我的宠物》查看名字。"
            if not p:
                return "你没有宠物，无法使用品质卡。发送『砸蛋』获取一只。"
            ok, msg = petmod.upgrade_quality(p, target_q)
            if not ok:
                return msg
            self.store.remove_item(player, name, 1)
            return f"使用『{name}』x1：{msg}"
        # 变种卡：变更当前宠物为「指定品种」（保留等级/品质/属性）；必须显式填写目标品种，不能随机
        if it_check and it_check.get("effect", {}).get("species_change"):
            if not self.store.has_item(player, name):
                return f"背包里没有『{name}』。"
            sub = tokens[2].strip() if len(tokens) > 2 else ""
            if not sub:
                return (
                    f"⚠️ 使用『{name}』必须指定目标宠物种类（不能随机）：\n\n"
                    "用法：`使用 变种卡 <宠物种类>`\n\n"
                    "例如：`使用 变种卡 绿毛虫`\n\n"
                    "发送《宠物种类》查看所有可选品种。"
                )
            ts = self._match_species(sub)
            if not ts:
                return f"没有名为『{sub}』的宠物种类。发送《宠物种类》查看现有品种。"
            p = self._need_pet(player)
            if not p:
                return "你没有宠物，无法使用『变种卡』。发送『砸蛋』获取一只。"
            if p.get("locked"):
                return "🔒 宠物已锁定，无法改变种类。请先『解锁宠物』。"
            old_species = p.get("species", "")
            if ts == old_species:
                return f"『{old_species}』已经是『{ts}』了，换一个品种试试。"
            p["species"] = ts
            self.store.remove_item(player, name, 1)
            return (
                f"🔄 使用『{name}』×1：『{old_species}』变为『{ts}』！\n"
                "> 等级/品质/属性均保留。"
            )
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
                block = self._skills_cap_check(player, p, name, s["power"])
                if block:
                    return block
                p.setdefault("skills", []).append(name)
                self.store.remove_item(player, name, 1)
                petmod.refresh_energy(p)
                msg = f"📜 参悟成功！消耗秘技书『{name}』x1，习得秘技『{name}』，战力 +{s['power']}。"
                removed = self._cap_skills(player, p)
                if removed:
                    msg += f"\n> 已达秘技上限（{data.SKILLS_MAX}），顶替并退回最低秘技书：{'、'.join(removed)}。"
                return msg
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
            return f"攻击 +{eff['add_atk']}，当前攻击 {self._short_num(p['atk'])}。"
        if "add_def" in eff:
            p["def"] += eff["add_def"]
            return f"防御 +{eff['add_def']}，当前防御 {self._short_num(p['def'])}。"
        if "add_intel" in eff:
            p["intel"] += eff["add_intel"]
            return f"智力 +{eff['add_intel']}，当前智力 {self._short_num(p['intel'])}。"
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
        if self._group_is_infinite(group_id):
            tax_info = "（无限服：免一切税费与限制）"
        elif tax_rate == 0:
            if count <= 3:
                tax_info = "（🆓 少量免税）"
            else:
                tax_info = "（🟢 活跃免税）"
        elif tax_rate > data.TRANSFER_TAX_ITEM:
            tax_info = f"（税 {tax_count} 个，{tax_rate:.0%} ⚠️ 高频同用户）"
        else:
            tax_info = f"（税 {tax_count} 个，{tax_rate:.0%}）"
        return f"📦 已转让 {name} ×{receive_count} 给 `{self._display_uid(target)}`{tax_info}。"

    def _gift_currency(
        self, player: dict, group_id: str, cmd: str, tokens: list[str]
    ) -> str:
        # 赠送金币/积分/钻石 用户ID 数量
        currency = cmd.replace("赠送", "")
        tx_type = {"金币": "coin", "积分": "jifen", "钻石": "diamond"}.get(currency, "coin")
        if len(tokens) < 3:
            if self._group_is_infinite(group_id):
                return f"用法：{cmd} 用户ID 数量"
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
        if self._group_is_infinite(group_id):
            tax_info = "（无限服：免一切税费与限制）"
        elif tax_rate == 0:
            tax_info = "（🟢 活跃免税）"
        elif tax_rate > data.TRANSFER_TAX_COIN:
            tax_info = f"（税 {tax_amount}，{tax_rate:.0%} ⚠️ 高频同用户）"
        else:
            tax_info = f"（税 {tax_amount}，{tax_rate:.0%}）"
        return f"💰 已向 `{self._display_uid(target)}` 赠送 {currency} ×{receive_amount}{tax_info}。"

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
                    extra = f"\n💕 伴侣 `{self._display_uid(p['love_target'])}` 的好感度也 +{gain}。"
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
            item = random.choice(["红药水", "蓝药水", "三明治", "相思豆", "万能宝石", "五色药"])
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

    def _toggle_auto_level(self, player: dict, enable: bool) -> str:
        """开启/关闭经验满自动升级（默认开启）。"""
        if enable:
            player["auto_level"] = True
            return "已开启『自动升级』：经验满后自动一键升级。发送『关闭自动升级』可关闭。"
        player["auto_level"] = False
        return ("已关闭『自动升级』：经验满后不再自动升级，需发送『一键升级宠物』手动升级。"
                "发送『开启自动升级』可恢复。")

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
        block = self._skills_cap_check(player, p, name, s["power"])
        if block:
            return block
        self.store.remove_item(player, name, 1)
        p.setdefault("skills", []).append(name)
        msg = f"📜 参悟成功！习得秘技『{name}』，战力 +{s['power']}。"
        removed = self._cap_skills(player, p)
        if removed:
            msg += f"\n> 已达秘技上限（{data.SKILLS_MAX}），顶替并退回最低秘技书：{'、'.join(removed)}。"
        return msg

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
        return f"对 `{self._display_uid(target)}` 的宠物使用『{name}』×{count}：{msg}"

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
        return f"🌿 已治愈 `{self._display_uid(target)}` 的宠物，血量回满。"

    def _talent_revive(self, player: dict, group_id: str, tokens: list[str]) -> str:
        p = self._need_pet(player)
        if not p:
            return "你没有宠物。"
        has_talent = p.get("talent") == "起死回生"
        target = self._arg(tokens, 1)
        if not target:
            return "用法：复活 用户ID"
        tp, err = self._find_target(group_id, target)
        if err:
            return err
        if not tp.get("pet"):
            return "目标没有宠物。"
        tpet = tp["pet"]
        if not petmod.is_dead(tpet):
            return "目标宠物还活着，无需复活。"
        if not has_talent and not self.store.remove_item(player, "九转还魂丹"):
            return "复活需要『九转还魂丹』（可在商城购买），或觉醒『起死回生』天赋免费复活。"
        tpet["status"] = "正常"
        tpet["hp"] = tpet["hp_max"]
        cost = "（天赋·起死回生免费）" if has_talent else "消耗『九转还魂丹』x1。"
        return f"💫 已复活 `{self._display_uid(target)}` 的宠物。{cost}"

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
        return f"🔋 已向 `{self._display_uid(target)}` 的宠物转移 {amount} 点精力。"

    # =====================================================================
    # 对战 / 排行
    # =====================================================================
    def _battle(
        self, attacker: dict, defender: dict, ap_player: dict, dp_player: dict
    ) -> str:
        ap = petmod.effective_power_vs(attacker, defender) + self._mount_power(ap_player)
        dp = petmod.effective_power_vs(defender, attacker) + self._mount_power(dp_player)
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
                attacker, defender, ap_player, dp_player, flawless=True, ap=ap, dp=dp
            )
        # 首轮胜负判定：攻击方【攻击值】vs 防御方【防御值】——破防者直接占优；
        # 两者恰好相同（攻击==防御）时，才退回用有效战力(含属性克制)再比高低。
        a_atk = int(attacker.get("atk", 0) or 0)
        d_def = int(defender.get("def", 0) or 0)
        if a_atk > d_def or (a_atk == d_def and ap >= dp):
            return self._battle_win(
                attacker, defender, ap_player, dp_player, ap=ap, dp=dp
            )
        # 攻击方失败：扣血，可能因此死亡；『不死之体』则至少保留 1 点血
        pre_ahp = attacker["hp"]
        loss = attacker["hp_max"] // 3
        if attacker.get("talent") == "不死之体":
            attacker["hp"] = max(1, attacker["hp"] - loss)
            state_txt = "触发【不死之体】保住性命！"
        else:
            attacker["hp"] = max(0, attacker["hp"] - loss)
            if attacker["hp"] <= 0:
                attacker["status"] = "死亡"
                attacker["mood"] = max(1, attacker.get("mood", 5) - 1)
                state_txt = (
                    f"力竭身亡！心情降至 {attacker['mood']} 颗星。"
                )
            else:
                pct = attacker["hp"] / attacker["hp_max"] if attacker["hp_max"] else 0
                if pct <= 0.15:
                    state_txt = "重伤倒地，只剩一口气，危在旦夕！"
                elif pct <= 0.5:
                    state_txt = "受了重伤，行动艰难。"
                else:
                    state_txt = "受了点伤。"
        rounds = self._battle_rounds_text(defender, attacker, loss, pre_ahp)
        return (
            f"**⚔ 战斗失败！**\n\n"
            f"**🔁 战况回放**\n\n"
            f"{rounds}\n\n"
            f"**📊 战后状态**\n\n"
            f"『{attacker['nickname']}』(战力{ap}) {self._hp_line(attacker, pre_ahp)}，{state_txt}\n\n"
            f"『{defender['nickname']}』(战力{dp}) {self._hp_line(defender)}，毫发无伤。"
        )

    # ------------------------------------------------------------------
    # 战斗战况展示（回合回放 + 血条）
    # ------------------------------------------------------------------
    @staticmethod
    def _hp_bar(hp: int, hp_max: int, width: int = 8) -> str:
        """血条：▓▓▓░░░░░"""
        if hp_max <= 0:
            return "░" * width
        filled = max(0, min(width, round(hp / hp_max * width)))
        return "▓" * filled + "░" * (width - filled)

    def _hp_line(self, pet: dict, pre_hp: int | None = None) -> str:
        """血量行：HP ▓▓░░ **12/40**（-28）"""
        hp = int(pet.get("hp", 0) or 0)
        hp_max = int(pet.get("hp_max", 1) or 1)
        loss = (
            f"（-{pre_hp - hp}）"
            if pre_hp is not None and pre_hp > hp
            else ""
        )
        return f"HP {self._hp_bar(hp, hp_max)} **{hp}/{hp_max}**{loss}"

    def _battle_rounds_text(
        self,
        winner: dict,
        loser: dict,
        total_dmg: int,
        loser_pre_hp: int,
        nullified: bool = False,
        flawless: bool = False,
    ) -> str:
        """按回合生成战况回放。

        伤害数字与实际结算完全一致：胜方命中造成的伤害合计 = total_dmg
        （败方战前 HP = loser_pre_hp，逐回合扣到战后真实血量）；
        败方的还击全部被格挡/闪避（实际结算中败方不造成伤害）。
        nullified：败方触发【不死之体】，伤害尽数化解。
        每回合为独立段落（以 \\n\\n 分隔），避免 QQ Markdown 把单 \\n 当作软换行吞掉。
        """
        wname = winner.get("nickname", "")
        lname = loser.get("nickname", "")
        atk_verbs = ["猛击", "撕咬", "飞扑", "连击", "蓄力重击", "旋风爪击"]
        def_verbs = ["格挡了下来", "闪身躲过", "用护甲弹开", "侧身避开"]
        if flawless:
            return f"『{wname}』濒死之际爆发全部潜能，一击命中『{lname}』！"
        n = 3 if total_dmg <= 0 else random.randint(3, 5)
        parts: list[int] = []
        if total_dmg > 0:
            weights = [random.uniform(0.6, 1.4) for _ in range(n)]
            ws = sum(weights)
            parts = [max(1, round(total_dmg * w / ws)) for w in weights]
            parts[0] += total_dmg - sum(parts)  # 修正四舍五入误差
        lines = []
        hp_left = loser_pre_hp
        for i in range(n):
            dmg = parts[i] if i < len(parts) else 0
            verb = random.choice(atk_verbs)
            if nullified:
                body = f"『{wname}』{verb}命中，却被『{lname}』的【不死之体】尽数化解"
            elif dmg > 0:
                hp_left = max(0, hp_left - dmg)
                body = f"『{wname}』{verb}命中，造成 **{dmg}** 伤害（『{lname}』HP 剩 **{hp_left}**）"
            else:
                body = f"『{wname}』{verb}，却被『{lname}』{random.choice(def_verbs)}"
            if i < n - 1:
                body += f"；『{lname}』还击，被『{wname}』{random.choice(def_verbs)}"
            lines.append(f"▶ 回合{i + 1}：{body}")
        return "\n\n".join(lines)

    def _battle_win(
        self, attacker, defender, ap_player, dp_player, flawless=False, ap=0, dp=0
    ) -> str:
        # 不死之体
        killed = False
        pre_dhp = defender["hp"]
        nullified = defender.get("talent") == "不死之体"
        if not nullified:
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
        head = "💥 触发【蝶逆轮回】，一滴血秒杀对手！" if flawless else "⚔ 战斗胜利！"
        kill_txt = "（对方宠物已死亡）" if killed else ""
        dloss = pre_dhp - defender["hp"]
        rounds = self._battle_rounds_text(
            attacker, defender, dloss, pre_dhp,
            nullified=nullified, flawless=flawless,
        )
        # 胜方：普通胜利毫发无伤；蝶逆轮回时只剩 1 点血（战前满血）
        a_line = self._hp_line(attacker, attacker["hp_max"] if flawless else None)
        a_note = "，濒死爆发。" if flawless else "，毫发无伤。"
        if killed:
            d_note = "，重伤倒地。"
        elif nullified:
            d_note = "，毫发无损（不死之体）。"
        else:
            dpct = defender["hp"] / defender["hp_max"] if defender["hp_max"] else 0
            d_note = (
                "，性命垂危！"
                if dpct <= 0.15
                else ("，受了重伤。" if dpct <= 0.5 else "，受了点伤。")
            )
        pw = f"(战力{ap}) " if ap else ""
        pw2 = f"(战力{dp}) " if dp else ""
        return (
            f"**{head}**\n\n"
            f"**🔁 战况回放**\n\n"
            f"{rounds}\n\n"
            f"**📊 战后状态**\n\n"
            f"『{attacker['nickname']}』{pw}{a_line}{a_note}\n\n"
            f"『{defender['nickname']}』{pw2}{self._hp_line(defender, pre_dhp)}{d_note}\n\n"
            f"💠 **经验 +{exp}**{steal}{kill_txt}。"
        )

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
        if self._group_is_infinite(player.get("group", "")):
            return "⚠️ 本群为无限服，不支持跨群挑战。"
        if not group.get("cross", True):
            return "⚠️ 本群未开启宠物跨群功能。"
        busy = self._busy_reason(p)
        if busy:
            return busy
        # 跨群挑战宠物 [群号 用户ID]，或随机
        target_player = None
        if len(tokens) >= 3:
            target_group = str(tokens[1])
            if self._group_is_infinite(target_group):
                return f"⚠️ 群 `{target_group}` 是无限服，无法被跨群挑战。"
            target_player = self.store.get_player(
                tokens[2], tokens[1], create=False
            )
            if not target_player:
                return f"❌ 群 `{tokens[1]}` 内用户 `{tokens[2]}` 不存在。"
        if not target_player:
            inf = self._infinite_group_ids()
            self_key = self.store.make_key(player.get("group", ""), player["qq"])
            candidates = [
                pl
                for k, pl in self.store.all_players().items()
                if pl.get("pet")
                and k != self_key
                and self.store.resolve_group(str(pl.get("group", ""))) not in inf
                and not petmod.is_dead(pl["pet"])
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
        """战力显示：≥1亿用『X.XX亿』，≥1万用『X.XX万』，否则原值。"""
        return PetParkPlugin._short_num(bp)

    def _rank(self, player: dict, group_id: str, local: bool) -> str:
        if not local and self._group_is_infinite(group_id):
            return "⚠️ 本群为无限服，不参与宠物神榜（跨群共享功能已关闭）。"
        # 本群排行只统计本群玩家；神榜为全服（跨群），但排除无限服群。
        source = self.store.players_in_group(group_id) if local else self.store.all_players()
        if not local:
            inf = self._infinite_group_ids()
            source = {
                k: v
                for k, v in source.items()
                if self.store.resolve_group(str(v.get("group", ""))) not in inf
            }
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
        # 神榜为官方服跨群排行，以「群ID+用户ID」为唯一身份；无限服群不参与。
        inf = self._infinite_group_ids()
        entries = []
        for k, pl in self.store.all_players().items():
            if pl.get("pet") and self.store.resolve_group(str(pl.get("group", ""))) not in inf:
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
        if not player.get("auto_level", True):
            return "\n> 经验已满，发送『一键升级宠物』手动升级（当前已关闭自动升级）。"
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
                drop = "\n> 💎 掉落道具：**万能宝石** ×1"
            desc = f"您的{nick}在{name}遇见{monster}，激战{monster}结果**大胜**！"
            body = (
                f"> ⏱️ 耗时 {minutes} 分钟 · 👹 怪物战力 **{power}**\n"
                f"> 🎁 经验 **+{exp_gain}** · 积分 **+{jifen_gain}**{drop}\n"
                f"> 🔁 下次可挑战：{next_time}"
            )
            return f"{head}\n{desc}\n{body}{self._auto_level_note(player, p)}"
        desc = f"您的{nick}在{name}遇见{monster}，力战{monster}结果**惨败**！"
        body = (
            f"> ⏱️ 耗时 {minutes} 分钟 · 👹 怪物战力 **{power}**\n"
            "> 💔 战败没有经验奖励！\n"
            f"> 🔁 下次可挑战：{next_time}"
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
                drop_text = f"\n> 💎 掉落道具：**{drop['item']}** ×{drop.get('count', 1)}"
            body = (
                f"> 👹 神仙战力 **{power}** · 我方发挥 **{roll}**\n"
                f"> 🎁 仙元 **+{xianyuan_gain}** · 积分 **+{jifen_gain}**{drop_text}\n"
                f"> 🔁 下次可挑战：{next_time}"
            )
            return f"{head}\n✨ 你的『{nick}』击败『{monster}』，获得仙缘！\n{body}{self._auto_level_note(player, p)}"
        # 失败惩罚：损失一半血量
        p["hp"] = max(1, p["hp"] // 2)
        body = (
            f"> 👹 神仙战力 **{power}** · 我方发挥 **{roll}**\n"
            "> 💔 战败，宠物身受重伤，无仙元奖励。\n"
            f"> ❤️ 宠物血量：{p['hp']}/{p['hp_max']}\n"
            f"> 🔁 下次可挑战：{next_time}"
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
            lines.append(f"- 你的战力：{roll}　VS　守卫战力：{monster_power}")
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
                lines.append(f"- 你的战力：{roll}　VS　宝箱怪战力：{monster_power}")
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
            lines.append(f"- 你的战力：{roll}　VS　领主战力：{monster_power}")
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
        if stage and data.STAGES.index(p.get("stage") or data.STAGES[0]) < data.STAGES.index(stage):
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
        done = set(player.get("quest_done", []))
        claimed = player.get("quests", {})
        lines = [
            "## 📜 可领取剧情任务",
            "> `领取任务 任务名` 领取，完成后 `提交任务 任务名`",
            "> 每个任务只能完成一次，宠物重生后清空记录、可重新完成。",
            "",
            "| 任务 | 状态 | 前提 | 目标 | 奖励 |",
            "|:--:|:--:|:--:|:--:|:--:|",
        ]
        for n, q in data.QUESTS.items():
            need = "、".join(
                f"{data.QUEST_NEED_LABELS.get(k, k)}×{v}" for k, v in q["need"].items()
            ) or "直接领取"
            rwd = self._quest_reward_text(q.get("reward", {}))
            req = self._quest_req_text(q)
            if n in done:
                st = "✅ 已完成"
            elif n in claimed:
                st = "📥 进行中"
            elif not self._quest_req_met(player, q):
                st = "🔒 未达成"
            else:
                st = "🟢 可领取"
            lines.append(f"| {n} | {st} | {req} | {need} | {rwd} |")
        return "\n".join(lines)

    def _my_quests(self, player: dict) -> str:
        qs = player.get("quests", {})
        if not qs:
            return "📜 你还没有领取剧情任务，发送『宠物剧情任务』查看。"
        lines = [
            "## 📜 我的剧情任务",
            "",
            "| 任务 | 进度 |",
            "|:--:|:--:|",
        ]
        stats = player.get("stats", {})
        for n, base in qs.items():
            need = data.QUESTS.get(n, {}).get("need", {})
            base = base if isinstance(base, dict) else {}
            prog = "、".join(
                f"{data.QUEST_NEED_LABELS.get(k, k)} **{max(0, stats.get(k, 0) - base.get(k, 0))}**/{v}"
                for k, v in need.items()
            ) or "已完成前置，可直接提交"
            lines.append(f"| {n} | {prog} |")
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
            if name in player.get("quest_done", []):
                return f"❌ 『{name}』已完成，重生后重置才能重新领取。"
            if name in player.get("quests", {}):
                return f"『{name}』已在进行中。"
            # 记录领取时的进度快照，任务进度从领取时刻起算
            player.setdefault("quests", {})[name] = {k: stats.get(k, 0) for k in need}
            return f"已领取剧情任务『{name}』。"
        # 提交任务
        if name in player.get("quest_done", []):
            return f"『{name}』已完成，无法重复提交。"
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
        # 记为已完成：每个剧情任务只能完成一次，重生后才可重做
        player.setdefault("quest_done", []).append(name)
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
            return f"用户 {self._display_uid(target_qq)} 已经在另一个队伍中了。"
        if self._tomb_session_exists(tp):
            return f"用户 {self._display_uid(target_qq)} 正在摸金中，无法组队。"
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
            f"队长 {self._display_uid(my_qq)} 邀请 {self._display_uid(target_qq)} 组队摸金！\n\n"
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
        return f"## 队伍已解散\n{self._display_uid(player.get('qq', ''))} 取消了组队。"

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
                f"队长 {self._display_uid(my_qq)} · 队友 {self._display_uid(teammate_qq)}\n"
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
            card_line = f"- 命运卡牌：【{session['destiny_card']}】\n"
        pending = session.get("pending")
        pending_text = ""
        if pending:
            pmap = {"C": "宝箱待开（开箱/跳过）", "M": "怪物待战（战斗/逃跑）", "S": "祭坛待祭拜（祭拜/跳过）"}
            pending_text = f"\n- 当前：{pmap.get(pending['type'], '')}"

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
                    f"- {label} `{self._display_uid(qq)}`：HP {pd.get('hp', 0)}/{pd.get('hp_max', 0)}　"
                    f"战力 {power}（{wep_text}）\n"
                    f"　位置：({pos.get('x', 0)},{pos.get('y', 0)})　"
                    f"逃跑 {pd.get('escapes', 0)}/{data.TOMB_ESCAPES_PER_RAID}　"
                    f"眩晕 {pd.get('stunned', 0)}\n"
                    f"　冥币 {pd.get('mingbi', 0)}　背包 {inv_text}"
                )
            lines.append(f"- 合计冥币：{total_mingbi} / {session['required']}　剩余时间：{remain}{pending_text}")
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
            f"- 位置：({pos['x']},{pos['y']})\n"
            f"- 摸金HP：{session['hp']}/{session['hp_max']}\n"
            f"- 战力：{power}　武器：{wep_text}\n"
            f"- 逃跑次数：{session.get('escapes', 0)}/{data.TOMB_ESCAPES_PER_RAID}\n"
            f"- 背负冥币：{session['mingbi']} / {session['required']}\n"
            f"- 摸金背包：{inv_text}\n"
            f"- 剩余时间：{remain}　眩晕：{session.get('stunned', 0)}"
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
                f"- {xp_text}\n"
                f"- {pet_exp_text}\n"
                f"- 累计成功 {stats['success']} 次，总带出冥币 {stats['total_mingbi']}"
            )
        if reason == "timeout":
            stats["fail"] = stats.get("fail", 0) + 1
            return (
                f"⏰ 墓穴坍塌，撤离失败！\n"
                f"- {xp_text}\n"
                f"- {pet_exp_text}\n"
                f"- 本局冥币全部损失\n"
                f"- 装备背包全部掉落！储物柜不受影响"
            )
        if reason == "death":
            kept = int(session["mingbi"] * 0.2)
            self.store.add_tomb_mingbi(player, kept)
            stats["fail"] = stats.get("fail", 0) + 1
            return (
                f"💀 摸金角色阵亡，撤离失败！\n"
                f"- {xp_text}\n"
                f"- {pet_exp_text}\n"
                f"- 装备背包全部掉落！储物柜不受影响\n"
                f"- 仅保留 {kept} 冥币"
            )
        if reason == "revive":
            kept = int(session["mingbi"] * 0.5)
            self.store.add_tomb_mingbi(player, kept)
            stats["fail"] = stats.get("fail", 0) + 1
            return (
                f"🧧 招魂幡触发，你在濒死之际被强行送出墓穴！\n"
                f"- {xp_text}\n"
                f"- {pet_exp_text}\n"
                f"- 保留 {kept} 冥币\n"
                f"- 带入的武器和道具已带回"
            )
        # forfeit
        kept = int(session["mingbi"] * 0.5)
        self.store.add_tomb_mingbi(player, kept)
        return (
            f"🏃 你已放弃本次摸金，仅保留 {kept} 冥币。\n"
            f"- {xp_text}\n"
            f"- {pet_exp_text}\n"
            f"- 损失 {session['mingbi'] - kept} 冥币\n"
            f"- 带入的武器和道具已带回"
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
            f"你将 {self._display_uid(tqq)} 从绝境中救起！（HP恢复至 {revive_hp}）\n"
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
            return f"## 传送完成\n向 {self._display_uid(target_qq)} 传送 **冥币×{count}**。"
        my_inv = mydata.get("inventory", {})
        if my_inv.get(item_name, 0) < count:
            return f"你的背包中没有足够的「{item_name}」。"
        my_inv[item_name] -= count
        if my_inv[item_name] <= 0:
            my_inv.pop(item_name, None)
        tp_inv = tpdata.setdefault("inventory", {})
        tp_inv[item_name] = tp_inv.get(item_name, 0) + count
        return f"## 传送完成\n向 {self._display_uid(target_qq)} 传送 **{item_name}×{count}**。"
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
    def _tomb_display_qq(self, qq: str) -> str:
        """摸金/扫雷排行统一显示用户：优先已绑定QQ号，未绑定则返回平台用户ID(openid)。"""
        qq = str(qq or "")
        return (self.store.get_bound_qq(qq) or qq or "未知").replace("|", "丨")

    def _tomb_rank(self, player: dict, group_id: str) -> str:
        """摸金财富全服排行（按永久冥币）。无限服群不参与。"""
        if self._group_is_infinite(group_id):
            return "⚠️ 本群为无限服，不参与全服摸金排行。"
        skip = self._infinite_member_qqs()
        entries = []
        for qq, st in self.store._data.get("tomb_players", {}).items():
            mingbi = st.get("mingbi", 0)
            if mingbi > 0 and str(qq) not in skip:
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
        lines.append("| 排名 | 用户 | 冥币 |")
        lines.append("|:--:|:--:|--:|")
        for i, (_, qq_text, mingbi) in enumerate(entries[: self.rank_size], 1):
            rk = medals.get(i, str(i))
            lines.append(f"| {rk} | {qq_text} | {mingbi} |")
        return "\n".join(lines)

    def _tomb_daily_rank(self, player: dict) -> str:
        """今日摸金神榜（按今日获得冥币）。无限服群不参与。"""
        if self._group_is_infinite(str(player.get("group", ""))):
            return "⚠️ 本群为无限服，不参与全服摸金神榜。"
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        skip = self._infinite_member_qqs()
        entries = []
        for qq, st in self.store._data.get("tomb_players", {}).items():
            gain = st.get("daily_gains", {}).get(today, 0)
            if gain > 0 and str(qq) not in skip:
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
        lines.append("| 排名 | 用户 | 今日获得冥币 |")
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
        skip = self._infinite_member_qqs()
        entries = []
        for qq, st in self.store._data.get("tomb_players", {}).items():
            gain = st.get("daily_gains", {}).get(yesterday, 0)
            if gain > 0 and str(qq) not in skip:
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
        lines.append("| 排名 | 用户 | 昨日获得冥币 |")
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
        if self._group_is_infinite(group_id):
            return "⚠️ 本群为无限服，不参与全服摸金神榜奖励。"
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
        """使用 GPT 生成底座与精确格子渲染扫雷棋盘。"""
        from .petpark.minesweeper_view import render
        img = render(session, data.MS_DIFFICULTIES[session["difficulty"]], reveal, boom)
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
        filename = self._ms_draw_board(session, reveal=reveal, boom=boom)
        with Image.open(self.store.custom_images_dir / filename) as board:
            width, height = board.size
        url = self._tomb_image_url(filename)
        return f"![扫雷棋盘 #{width}px #{height}px]({url})"

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
                f"- 扫雷积分 +{cfg['score']}（累计 {st['score']}）\n"
                f"- 宠物经验 +{exp}（已暂存，发送「扫雷兑换」可发放到当前群宠物）",
                self._ms_board_md(session, reveal=True),
            )

        # 失败：按进度给安慰经验
        exp = int(cfg["exp"][0] * opened_ratio * data.MS_FAIL_EXP_RATIO)
        exp_text = ""
        if exp > 0:
            self.store.add_ms_pending_pet_exp(qq, exp)
            exp_text = f"\n- 宠物经验 +{exp}（已暂存，发送「扫雷兑换」可发放到当前群宠物）"
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
        """扫雷全服排行（按累计积分），使用 Markdown 表格展示。无限服群不参与。"""
        if self._group_is_infinite(str(player.get("group", ""))):
            return "⚠️ 本群为无限服，不参与全服扫雷排行。"
        skip = self._infinite_member_qqs()
        entries = []
        for qq, st in self.store.all_ms_players().items():
            score = st.get("score", 0)
            if score > 0 and str(qq) not in skip:
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

    _BJ_TZ = ZoneInfo("Asia/Shanghai")

    def _bj_localtime(self, secs: int | None = None) -> time.struct_time:
        """返回北京时间的 struct_time。"""
        if secs is None:
            return datetime.now(self._BJ_TZ).timetuple()
        return datetime.fromtimestamp(secs, self._BJ_TZ).timetuple()


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
            f"> 伴侣：`{self._display_uid(p['love_target'])}`　好感度：{p['favor']}"
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
            return f"💌 已向 {self._display_uid(target)} 的宠物发起追求，等待对方『同意追求 {self._display_uid(player['qq'])}』。"
        if cmd == "同意追求":
            pend = player.get("pending", {}).get("pursue")
            if self._display_uid(pend) != self._display_uid(target):
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
            return f"💍 已向 {self._display_uid(target)} 求婚，消耗『永恒钻戒』x1，等待对方『同意求婚 {self._display_uid(player['qq'])}』。"
        if cmd == "同意求婚":
            pend = player.get("pending", {}).get("marry")
            if self._display_uid(pend) != self._display_uid(target):
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
                    parts.append(f"🐾 {self._display_uid(disp_qq)}({mult_str})")
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
            f"- 产量：{self._homestead_prod_text(name, 1)}",
        ]
        if levelup:
            lines.append(f"- {levelup}")
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
            f"- 产量：{self._homestead_prod_text(name, new_lv)}",
        ]
        if levelup:
            lines.append(f"- {levelup}")
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
            f"- 累计投入 {total_cost} 金币，返还 **{refund}** 金币（20%）\n"
            f"- 建筑位已释放（{len(buildings)}/{data.homestead_slots(hs['level'])}）"
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
                    lines.append(f"　🐾 派遣：{self._display_uid(disp_info.get('qq','?'))} ×{mult}")
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
            return f"本群不存在用户 {self._display_uid(target_qq)}。"
        ths = self.store.homestead_state(tp)
        tbuildings = ths.get("buildings", {})
        tdispatch = ths.get("dispatch", {})
        hs["visit_today"] += 1
        player["coin"] = player.get("coin", 0) + data.HOMESTEAD_VISIT_REWARD_COIN
        tp["coin"] = tp.get("coin", 0) + data.HOMESTEAD_VISITED_REWARD_COIN
        tdefense = data.homestead_defense(ths)
        lines = [
            f"## 🏡 拜访 {self._display_uid(target_qq)} 的家园",
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
                    disp_tag = f" [🐾{self._display_uid(str(tdispatch[name].get('qq','?')))}]"
                lines.append(f"{icon} **{name}** Lv{lv}{disp_tag}　{prod_text}")
        lines.append("")
        lines.append(f"🤝 拜访成功！你 +{data.HOMESTEAD_VISIT_REWARD_COIN} 金，对方 +{data.HOMESTEAD_VISITED_REWARD_COIN} 金。")
        remain = data.HOMESTEAD_VISIT_MAX_PER_DAY - hs["visit_today"]
        lines.append(f"📅 剩余拜访 {remain} 次 · 💀 也可「顺手牵羊 {self._display_uid(target_qq)}」偷菜！")
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
            f"- 产量倍率：×**{mult}**{element_tag}\n"
            f"- 每小时消耗 {data.HOMESTEAD_DISPATCH_ENERGY_PER_HOUR} 点精力\n"
            f"- 发送「**召回 {name}**」召回宠物"
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
            lines.append(f"{icon} **{name}** ← {self._display_uid(owner_qq)} {pet_name}")
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
            return f"本群不存在用户 {self._display_uid(target_qq)}。"
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
            return f"刚偷过 {self._display_uid(target_qq)} 的家园，请 {m} 分钟后再来。"
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
                f"- 偷得 {self._display_uid(target_qq)} 的 💰{stolen_coin} 金币 + 💎{stolen_jifen} 积分\n"
                f"- 目标防御力：{target_defense}　今日剩余偷取：{data.HOMESTEAD_STEAL_MAX_PER_DAY - hs['steal_today']} 次"
            )
        else:
            player["coin"] = max(0, player.get("coin", 0) - data.HOMESTEAD_STEAL_FAIL_PENALTY)
            tp["coin"] = tp.get("coin", 0) + data.HOMESTEAD_STEAL_FAIL_PENALTY
            return (
                f"🚨 **偷菜被抓！**（成功率 {success_rate:.0%}）\n"
                f"- 被 {self._display_uid(target_qq)} 的哨塔发现了！赔偿 {data.HOMESTEAD_STEAL_FAIL_PENALTY} 金币\n"
                f"- 目标防御力：{target_defense}"
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
    def _homestead_dispatch_pet_names(self, hs: dict, group_id: str) -> list[str]:
        """收集该家园当前派遣中的宠物昵称（同园多只按序收集）。"""
        names: list[str] = []
        seen: set = set()
        for bname, dp in hs.get("dispatch", {}).items():
            owner = dp.get("qq", "")
            idx = dp.get("pet_index", -1)
            key = (owner, idx)
            if owner and idx >= 0 and key not in seen:
                seen.add(key)
                op = self.store.get_player(owner, group_id, create=False)
                if op:
                    pets = op.get("pets", [])
                    if 0 <= idx < len(pets):
                        n = pets[idx].get("nickname", "")
                        if n:
                            names.append(n)
        return names

    def _homestead_entries(self, group_id: str) -> list[tuple[str, str, dict]]:
        """按当前群服取家园候选：(键, openid, 家园状态)。

        无限服=本群局部（只取本群隔离键）；官方服=共享层（排除所有隔离键）。
        """
        hps = self.store._data.get("homestead_players", {})
        if self._group_is_infinite(group_id):
            gid = self.store.resolve_group(str(group_id))
            prefix = f"hom\x1f{gid}\x1f"
            return [(k, str(k)[len(prefix):], v) for k, v in hps.items() if str(k).startswith(prefix)]
        return [(k, str(k), v) for k, v in hps.items() if not self.store._is_isolated_state_key("hom", k)]

    def _homestead_rank(self, player: dict) -> str:
        """家园排行 —— 本周金币产出排行（无限服=本群，官方=共享层）。"""
        group_id = str(player.get("group", ""))
        is_inf = self._group_is_infinite(group_id)
        if is_inf:
            gid = self.store.resolve_group(group_id)
            my_key = f"hom\x1f{gid}\x1f{str(player.get('qq', ''))}"
        else:
            my_key = self.store._state_key("hom", group_id, str(player.get("qq", "")))
        entries = []
        for key, qq, hs in self._homestead_entries(group_id):
            self._homestead_update_weekly(hs)
            weekly = hs.get("weekly_coin", 0)
            if weekly > 0:
                entries.append({"qq": qq, "weekly": weekly, "level": hs.get("level", 1), "hs": hs, "key": key})
        entries.sort(key=lambda x: x["weekly"], reverse=True)
        top = entries[:data.HOMESTEAD_RANK_SIZE]
        lines = ["## 🏆 家园排行（本周金币产出）" + (" · 本群（无限服）" if is_inf else ""), ""]
        for i, e in enumerate(top):
            medal = {0: "🥇", 1: "🥈", 2: "🥉"}.get(i, f"{i + 1}.")
            pnames = self._homestead_dispatch_pet_names(e["hs"], group_id)
            suffix = f"（{'、'.join(pnames)}）" if pnames else ""
            lines.append(f"{medal} {self._display_uid(e['qq'])}{suffix} — 💰 {e['weekly']} 金（Lv{e['level']}）")
        if not top and is_inf:
            lines.append("> 本群（无限服）暂无玩家产出。")
        # 我的排名
        my_weekly = self.store.homestead_state(player).get("weekly_coin", 0)
        my_rank = next((i + 1 for i, e in enumerate(entries) if e["key"] == my_key), None)
        lines.append("")
        if my_rank:
            lines.append(f"📊 你的排名：第 {my_rank} 名（💰 {my_weekly} 金）")
        else:
            lines.append(f"📊 你本周暂无产出。快去建造家园！")
        # 奖励预告
        if not is_inf:
            lines.append(f"🏅 周榜前 3 奖励：🥇{data.HOMESTEAD_RANK_REWARD_COIN[1]} 🥈{data.HOMESTEAD_RANK_REWARD_COIN[2]} 🥉{data.HOMESTEAD_RANK_REWARD_COIN[3]} 金币")
        return "\n".join(lines)

    def _homestead_total_rank(self, player: dict) -> str:
        """家园总排行 —— 累计金币产出排行（无限服=本群，官方=共享层）。"""
        group_id = str(player.get("group", ""))
        is_inf = self._group_is_infinite(group_id)
        if is_inf:
            gid = self.store.resolve_group(group_id)
            my_key = f"hom\x1f{gid}\x1f{str(player.get('qq', ''))}"
        else:
            my_key = self.store._state_key("hom", group_id, str(player.get("qq", "")))
        entries = []
        for key, qq, hs in self._homestead_entries(group_id):
            total = hs.get("total_coin_earned", 0)
            if total > 0:
                entries.append({"qq": qq, "total": total, "level": hs.get("level", 1), "hs": hs, "key": key})
        entries.sort(key=lambda x: x["total"], reverse=True)
        top = entries[:data.HOMESTEAD_RANK_SIZE]
        lines = ["## 🏆 家园总排行（累计金币产出）" + (" · 本群（无限服）" if is_inf else ""), ""]
        for i, e in enumerate(top):
            medal = {0: "🥇", 1: "🥈", 2: "🥉"}.get(i, f"{i + 1}.")
            pnames = self._homestead_dispatch_pet_names(e["hs"], group_id)
            suffix = f"（{'、'.join(pnames)}）" if pnames else ""
            lines.append(f"{medal} {self._display_uid(e['qq'])}{suffix} — 💰 {e['total']} 金（Lv{e['level']}）")
        if not top and is_inf:
            lines.append("> 本群（无限服）暂无玩家累计产出。")
        my_total = self.store.homestead_state(player).get("total_coin_earned", 0)
        my_rank = next((i + 1 for i, e in enumerate(entries) if e["key"] == my_key), None)
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
