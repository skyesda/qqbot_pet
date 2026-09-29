"""「月耀华诞」中秋 × 国庆 双阶段活动 —— 可配置项与常量。

设计目标（与活动设计方案一致）：
- 唯一货币「月华」：既是排行榜积分，也是唯一奖励，只进不出（无花销出口、无兑换商店）；
- 活动作为独立模块部署，代码集中在 ``petpark/moonfest/``，删除该目录即整体下架；
- 中秋、国庆两阶段各自有可配置起止时间，活动结束后一次性结算排行奖励；
- 所有数值尽量可配置，写死进代码的只剩无法参数化的核心闭环。
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

_BJ = ZoneInfo("Asia/Shanghai")


def _ts(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    """北京时间年月日 → Unix 时间戳（北京时间 = UTC+8）。"""
    return int(datetime(year, month, day, hour, minute, tzinfo=_BJ).timestamp())

# ---------------------------------------------------------------------------
# 活动标识
# ---------------------------------------------------------------------------
ACTIVITY_KEY = "moonfest"           # 数据文件 moonfest.json 与事件命名空间
ACTIVITY_NAME = "月耀华诞 · 中秋 × 国庆"
ACTIVITY_TAG = "🌙 月耀华诞活动"

# ---------------------------------------------------------------------------
# 活动结束排行榜奖励（唯一一次结算，纯月华，写回月华账户，不碰主背包/主经济）
# 名次区间 + 月华，后台可改。
# ---------------------------------------------------------------------------
DEFAULT_END_REWARDS: list[dict[str, Any]] = [
    {"min": 1, "max": 1, "yuehua": 3000},
    {"min": 2, "max": 3, "yuehua": 2000},
    {"min": 4, "max": 10, "yuehua": 1000},
    {"min": 11, "max": 20, "yuehua": 500},
]

# ---------------------------------------------------------------------------
# 群里程碑（全群累计月华达标 → 群内每位参与者发放月华 + 全群公告）
# 分 5 档，最高档「群累计 1 万月华」为满；可后台配置。
# ---------------------------------------------------------------------------
DEFAULT_MILESTONES: list[dict[str, Any]] = [
    {"threshold": 1000, "gongde": 5},
    {"threshold": 2000, "gongde": 10},
    {"threshold": 4000, "gongde": 15},
    {"threshold": 7000, "gongde": 25},
    {"threshold": 10000, "gongde": 40},
]

# ---------------------------------------------------------------------------
# 默认配置（可被 moonfest.json 里的 config 覆盖，亦可通过管理指令「月耀配置」热改）
# ---------------------------------------------------------------------------
DEFAULT_CONFIG: dict[str, Any] = {
    # ---- 总控 ----
    "enabled": True,                # 活动总开关
    # ---- 双阶段时间（每阶段独立 enabled/start_at/end_at；10-01 两阶段重叠日同时开放）----
    "phase_midautumn": {"enabled": True, "start_at": _ts(2026, 9, 27), "end_at": _ts(2026, 10, 1, 23, 59)},  # 中秋·月耀
    "phase_national": {"enabled": True, "start_at": _ts(2026, 10, 1), "end_at": _ts(2026, 10, 7, 23, 59)},   # 国庆·华诞
    # ---- 每日开放时段（两阶段共用；0~24 全天）----
    "daily": {"open_hour": 0, "close_hour": 24},
    # ---- Jev（TypeSafe System One）----
    "jev": {"enabled": True, "api_key": ""},   # key 默认从 TYPESAFE_API_KEY/.jev_key 读
    # ---- 拜月 / 华诞签到（每日 1 次固定月华）----
    "sign_midautumn": 10,           # 中秋·拜月签到月华
    "sign_national": 15,            # 国庆·华诞签到月华
    # ---- 猜灯谜（中秋段，Jev noul 判定 + 字符串兜底）----
    "lantern_daily_limit": 20,      # 猜灯谜每日题数上限
    "lantern_cooldown_min": 0,      # 答对后冷却（分钟，默认无）
    "lantern_timeout_sec": 60,      # 作答超时（秒，超时揭晓谜底并计次数）
    "gongde_lantern_min": 10,       # 猜灯谜答对月华随机下限
    "gongde_lantern_max": 30,       # 猜灯谜答对月华随机上限
    # ---- 玉兔喂养（Jev score 4 档）----
    "feed_daily_limit": 10,         # 玉兔喂养每日次数
    "feed_cooldown_min": 10,        # 喂养冷却（分钟）
    "gongde_feed": [5, 10, 20, 30],  # 玉兔 score 4 档对应月华（厌恶/无感/喜欢/非常喜欢）
    # ---- 月饼合成（收集向，每种口味首次合成发一次）----
    "craft_bonus": 20,              # 月饼口味首次合成奖励月华（仅一次）
    # ---- 华诞巡礼（国庆段，Jev score 自适应难度）----
    "quiz_daily_limit": 20,         # 巡礼每日题数上限
    "quiz_timeout_sec": 60,         # 巡礼作答超时（秒）
    "gongde_quiz_min": 5,           # 巡礼答对月华随机下限
    "gongde_quiz_max": 20,          # 巡礼答对月华随机上限
    # ---- 烟火贺词（Jev 双闸审核 + 本地敏感词兜底）----
    "firework_daily_limit": 3,      # 贺词每日投稿次数
    "firework_max_len": 30,         # 贺词最大长度（字）
    "gongde_firework_min": 5,       # 贺词上墙月华随机下限
    "gongde_firework_max": 20,      # 贺词上墙月华随机上限
    # ---- 点赞（国庆段）----
    "like_daily_limit": 5,          # 每日点赞他人次数上限
    "gongde_like": 2,               # 被赞者所得月华
    # ---- 群里程碑 / 活动结束排行奖励 ----
    "milestones": DEFAULT_MILESTONES,
    "end_rewards": DEFAULT_END_REWARDS,
}

# 允许通过「月耀配置 <key> <value>」热改的整型/浮点/字符串键（白名单，防误改结构字段）
_EDITABLE_KEYS = {
    "enabled",
    "sign_midautumn", "sign_national",
    "lantern_daily_limit", "lantern_cooldown_min", "lantern_timeout_sec",
    "gongde_lantern_min", "gongde_lantern_max",
    "feed_daily_limit", "feed_cooldown_min",
    "craft_bonus",
    "quiz_daily_limit", "quiz_timeout_sec",
    "gongde_quiz_min", "gongde_quiz_max",
    "firework_daily_limit", "firework_max_len",
    "gongde_firework_min", "gongde_firework_max",
    "like_daily_limit", "gongde_like",
}

# 默认双阶段时间（北京时间，已通过 _ts() 写入 DEFAULT_CONFIG）：
# 中秋 2026-09-27 00:00 ~ 2026-10-01 23:59；国庆 2026-10-01 00:00 ~ 2026-10-07 23:59。
# 10-01 为双节同庆重叠日，两阶段同时生效。0=不限（照中元 start_at/end_at 语义）。


def merge_config(base: dict, override: dict | None) -> dict:
    """深拷贝默认配置，再用 override 覆盖。

    对**嵌套 dict**（phase_midautumn / phase_national / daily / jev）做一层深合并，
    而不是整体替换：否则旧存档里少一个键（如 phase 只有 start_at 没有 end_at）
    会让 ``end_at`` 退回 0 = 「不限」，活动永不结束、结算永不触发。
    list（milestones / end_rewards / gongde_feed）保持整体替换语义。
    """
    cfg = copy.deepcopy(base)
    if not override:
        return cfg
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k] = {**cfg[k], **v}
        else:
            cfg[k] = v
    return cfg


def editable_keys() -> set[str]:
    return _EDITABLE_KEYS


def tier_yuehua_for_rank(rank: int, end_rewards: list[dict]) -> int:
    """按名次（从 1 起）返回结算月华档位；未命中返回 0。"""
    for t in end_rewards:
        if int(t.get("min", 1)) <= rank <= int(t.get("max", 1 << 30)):
            return int(t.get("yuehua", 0))
    return 0
