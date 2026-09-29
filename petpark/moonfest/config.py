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
    {"min": 1, "max": 1, "yuehua": 5000},
    {"min": 2, "max": 3, "yuehua": 3000},
    {"min": 4, "max": 10, "yuehua": 1600},
    {"min": 11, "max": 20, "yuehua": 800},
]

# ---------------------------------------------------------------------------
# 群里程碑（全群累计月华达标 → 群内每位参与者发放月华 + 全群公告）
# 分 5 档，最高档「群累计 1 万月华」为满；可后台配置。
# ---------------------------------------------------------------------------
DEFAULT_MILESTONES: list[dict[str, Any]] = [
    {"threshold": 1600, "gongde": 5},
    {"threshold": 3200, "gongde": 10},
    {"threshold": 6400, "gongde": 15},
    {"threshold": 11000, "gongde": 25},
    {"threshold": 16000, "gongde": 40},
]

# ---------------------------------------------------------------------------
# 默认配置（可被 moonfest.json 里的 config 覆盖；线上只在后台「节日活动」页改）
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
    # ---- 月饼重制（首次合成解锁后，再次合成要走一条挑战链）----
    # 难度**按该玩家自己的重制成功次数**递增（不是全局、也不是按口味），后来者不被
    # 前面的玩家拖累。下面 5 个 list 一一对应 5 个阶段（下标 0 = 阶段 1）。
    "craft_remake_daily_limit": 3,       # 每日重制挑战次数上限
    "craft_remake_cooldown_min": 10,     # 挑战结束后的基础冷却（分钟）
    "craft_remake_cooldown_step_min": 5, # 每上一阶段冷却再加 N 分钟
    "craft_remake_cooldown_cap_min": 60, # 冷却上限（分钟）
    "craft_remake_steps": [0, 2, 5, 9, 14],          # 进入该阶段所需的累计成功次数
    "craft_remake_counts": [1, 2, 3, 3, 4],          # 该阶段每局题数
    "craft_remake_times": [30, 26, 22, 18, 14],      # 该阶段每题限时（秒）
    "craft_remake_rewards": [20, 30, 45, 70, 100],   # 该阶段通关月华
    "craft_star_max": 5,                 # 单个口味的星级上限
    "craft_star_rewards": {"3": 60, "5": 120},       # 口味星级一次性奖励（JSON）
    # ---- 拜月 / 华诞签到（每日 1 次固定月华；拜月就是普通签到，不分档、不额外赠物）----
    # 月相只用在两处玩法上：月饼重制的「月相推演」题、桂花酿的近满加成（见下）。
    "full_moon_window_days": 3,          # 「近满」窗口（距望 ≤N 天），供桂花酿加成判定
    "brew_full_moon_mult": 2.0,          # 近满窗口内酿出「酿王」的概率倍数
    # ---- 桂花酿（中秋段新增：材料 → 起坛 → 取酒 → 喂玉兔）----
    "brew_daily_limit": 1,               # 每日起坛上限
    "brew_minutes": 30,                  # 酿造时长（分钟）
    "brew_guihua_per_batch": 3,          # 每坛消耗桂花
    "brew_drop_pct": 35,                 # 猜灯谜答对掉落桂花的概率（%，桂花的主来源）
    "brew_grade_bonus": [20, 40, 80],    # 清酿/醇酿/酿王 首次酿出各发一次
    # ---- 玉兔同行（中秋段新增：每日一局多步闯关）----
    "rabbit_run_daily_limit": 1,
    "rabbit_run_steps": 5,
    "rabbit_run_fail_max": 2,            # 本局答错多少次即结束
    "rabbit_run_reward_min": 4,
    "rabbit_run_reward_max": 8,
    "rabbit_run_perfect_bonus": 25,      # 零错通关额外月华
    "rabbit_intimacy_step": 10,          # 累计喂食多少次升 1 级亲密度
    "rabbit_run_intimacy_mult": [1.0, 1.15, 1.3],  # 亲密度 3 级对应倍率
    # ---- 猜灯谜做深：连对倍率 + 难题档 ----
    "lantern_combo_rate": 0.1,           # 每连对 5 题 +10%
    "lantern_combo_cap": 3,              # 倍率上限（+30%）
    "lantern_hard_daily_limit": 5,       # 「猜灯谜 难题」每日次数上限
    "lantern_hard_mult": 1.5,            # 难题基础月华倍率
    # ---- 巡礼做深：单题问答 → 多站路线闯关 ----
    "route_stations": 5,                 # 一条路线几站
    "route_fail_max": 2,                 # 答错几次本路线重置
    "route_complete_bonus": 40,          # 走完全程额外月华
    "route_perfect_bonus": 60,           # 零错走完额外月华
    # ---- 献礼（国庆段新增：群协作进度轴，与「群累计月华」里程碑正交）----
    "offering_ladder": [
        {"threshold": 40, "yuehua": 5},
        {"threshold": 120, "yuehua": 10},
        {"threshold": 260, "yuehua": 15},
        {"threshold": 500, "yuehua": 25},
    ],
    "offering_quiz_point": 1,            # 巡礼答对 +N 献礼点
    "offering_firework_point": 3,        # 贺词上墙 +N 献礼点
    "offering_like_point": 1,            # 点赞 +N 献礼点
    # ---- 双庆（两阶段重叠日限定挑战，一年一次）----
    "double_festival_questions": 5,
    "double_festival_timeout_sec": 20,
    "double_festival_reward": 150,
    # ---- 贺词做深：每日主题 + 契合度加成 ----
    "firework_themes": [
        "写给祖国的一句话", "写给家人的一句话", "写给你思念的人",
        "写一句中秋团圆祝福", "写一句家国同庆的祝福",
    ],
    "firework_theme_bonus": 10,          # 投稿契合当日主题的额外月华
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

# 刻意**不设**「可热改键白名单」：活动没有任何群内管理指令，全部数值只在后台
# 「节日活动」页配置（后端 apply_config 负责类型强制与 JSON 解析）。

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


def tier_yuehua_for_rank(rank: int, end_rewards: list[dict]) -> int:
    """按名次（从 1 起）返回结算月华档位；未命中返回 0。"""
    for t in end_rewards:
        if int(t.get("min", 1)) <= rank <= int(t.get("max", 1 << 30)):
            return int(t.get("yuehua", 0))
    return 0
