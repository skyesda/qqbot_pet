"""中元节《青灯伴萌宠 · 幽影饲育馆》活动 —— 可配置项与常量。

设计目标（与活动文档一致）：
- 唯一货币「功德」：既是排行榜积分，也是唯一奖励，不出现任何道具/其它货币；
- 活动作为独立模块部署，代码集中在 ``petpark/zhongyuan/``，删除该目录即整体下架；
- 后台提供「活动总开关 / 一键关闭全部玩法 / 一键删除活动代码」三个控制项；
- 所有数值尽量可配置，写死进代码的只剩无法参数化的核心闭环。
"""
from __future__ import annotations

import copy
from typing import Any

# ---------------------------------------------------------------------------
# 活动标识
# ---------------------------------------------------------------------------
ACTIVITY_KEY = "zhongyuan"          # 数据文件 zhongyuan.json 与事件命名空间
ACTIVITY_NAME = "中元节《青灯伴萌宠 · 幽影饲育馆》"
ACTIVITY_TAG = "🕯️ 中元节活动"

# ---------------------------------------------------------------------------
# 段位（仅前 20 名授段位；名次区间 + 奖励功德，后台可改）
# ---------------------------------------------------------------------------
DEFAULT_TIERS: list[dict[str, Any]] = [
    {"name": "引渡人", "min": 1, "max": 1, "gongde": 5000},
    {"name": "掌灯人", "min": 2, "max": 3, "gongde": 3000},
    {"name": "摆渡人", "min": 4, "max": 10, "gongde": 1500},
    {"name": "点灯客", "min": 11, "max": 20, "gongde": 800},
]

# ---------------------------------------------------------------------------
# 群里程碑（全群累计功德达标 → 每位参与者直接发放共享功德，不入暂存）
# 分 5 个阶段，最高档「1 万功德」为满；可后台配置。
# ---------------------------------------------------------------------------
DEFAULT_MILESTONES: list[dict[str, Any]] = [
    {"threshold": 2000, "gongde": 50},
    {"threshold": 4000, "gongde": 50},
    {"threshold": 6000, "gongde": 100},
    {"threshold": 8000, "gongde": 100},
    {"threshold": 10000, "gongde": 200},
]

# ---------------------------------------------------------------------------
# 默认配置（可被 zhongyuan.json 里的 config 覆盖，亦可通过管理指令「中元配置」热改）
# ---------------------------------------------------------------------------
DEFAULT_CONFIG: dict[str, Any] = {
    # ---- 总控 ----
    "enabled": True,                # 活动总开关
    "start_at": 0,                  # 活动开始时间戳（0=不限；「中元开始/结束」指令或后台设置）
    "end_at": 0,                    # 活动结束时间戳（0=不限）
    # ---- 时间 ----
    "open_hour": 8,                 # 每日开放时段（含）
    "close_hour": 22,               # 每日停止时段（不含，22:00 后当日停止）
    "trigger_interval_min": 60,     # 解密触发频率（分钟）
    "dungeon_limit_min": 40,        # 单场解密时限（分钟）
    "bind_open_hours_before": 24,   # 绑定领号开启（活动开始前 N 小时）
    "bind_close_hours_before": 1,   # 绑定领号截止（活动结束前 N 小时）
    "redeem_window_hours": 48,      # 活动结束后兑换窗口（小时）
    # ---- 抽人 ----
    "max_draw_per_day": 2,          # 每人每日最多被抽入次数
    "max_dungeon_per_day": 8,       # 副本每日最多拉入场数（全活动总量）
    # ---- 解密（协作副本：全体共享进度）----
    "puzzle_count": 20,             # 每场题数（20 题，全体共享进度）
    "no_response_sec": 90,          # 全体连续无响应判定（秒，超时自动进入下一题）
    "dungeon_status_interval_sec": 120,  # 副本进行中每隔 N 秒全群播报一次倒计时/进度/存活
    "individual_fail_wrong": 3,     # 个人答错满 N 次即个人出局
    "pull_min_pct": 20,             # 每次拉入参与人数比例下限（%）
    "pull_max_pct": 50,             # 每次拉入参与人数比例上限（%）
    # ---- 功德（唯一货币 / 唯一奖励）----
    "gongde_clear": 300,            # 通关基础功德（存活玩家）
    "perfect_reward_mult": 2,       # 完美成就（答对次数前 10%）奖励倍数
    # ---- 文化玩法：放河灯 / 供灯焚香 / 中元问答（次数 / 冷却 / 随机功德）----
    "lantern_daily_limit": 10,      # 放河灯每日次数
    "incense_daily_limit": 10,      # 供灯 / 焚香每日次数
    "quiz_daily_limit": 20,         # 中元问答每日次数
    "lantern_cooldown_min": 20,     # 放河灯冷却（分钟）
    "incense_cooldown_min": 20,     # 供灯 / 焚香冷却（分钟）
    "quiz_timeout_sec": 60,         # 问答作答超时（秒，超时判失败）
    "gongde_lantern_min": 10,       # 放河灯功德随机下限
    "gongde_lantern_max": 30,       # 放河灯功德随机上限
    "gongde_incense_min": 10,       # 供灯 / 焚香功德随机下限
    "gongde_incense_max": 30,       # 供灯 / 焚香功德随机上限
    "gongde_quiz_min": 10,          # 问答答对功德随机下限
    "gongde_quiz_max": 20,          # 问答答对功德随机上限
    "gongde_sign": 10,              # 每日签到功德
    "yin_penalty_min": 60,          # 副本失败「阴气缠身」时长（分钟，默认 1 小时）
    "yin_clear_cost": 100,          # 快速解除「阴气缠身」消耗功德
    # ---- 段位 / 里程碑 ----
    "tiers": DEFAULT_TIERS,
    "milestones": DEFAULT_MILESTONES,
    # ---- DeepSeek 大模型 ----
    "deepseek_enabled": True,       # 是否启用 DeepSeek 生成谜题（关闭/失败则用本地模板）
    "deepseek_model": "deepseek-v4-flash-vision-exp",
    "deepseek_base_url": "https://api.deepseek.com",
    "deepseek_api_key": "",         # 默认从环境变量 DEEPSEEK_API_KEY 读取，绝不写死
    "deepseek_temperature": 0.3,
    "deepseek_max_tokens": 800,
    "deepseek_timeout": 30,         # 单次请求超时（秒）
}

# 允许通过「中元配置 <key> <value>」热改的整型/浮点/字符串键（白名单，防误改结构字段）
_EDITABLE_KEYS = {
    "enabled", "open_hour", "close_hour", "trigger_interval_min", "dungeon_limit_min",
    "bind_open_hours_before", "bind_close_hours_before", "redeem_window_hours",
    "max_draw_per_day", "max_dungeon_per_day", "puzzle_count", "no_response_sec", "dungeon_status_interval_sec",
    "individual_fail_wrong", "pull_min_pct", "pull_max_pct",
    "gongde_clear", "perfect_reward_mult",
    "lantern_daily_limit", "incense_daily_limit", "quiz_daily_limit",
    "lantern_cooldown_min", "incense_cooldown_min", "quiz_timeout_sec",
    "gongde_lantern_min", "gongde_lantern_max", "gongde_incense_min", "gongde_incense_max",
    "gongde_quiz_min", "gongde_quiz_max", "gongde_sign",
    "yin_penalty_min", "yin_clear_cost",
    "deepseek_enabled", "deepseek_model", "deepseek_base_url", "deepseek_api_key",
    "deepseek_temperature", "deepseek_max_tokens", "deepseek_timeout",
}


def merge_config(base: dict, override: dict | None) -> dict:
    """深拷贝默认配置，再用 override 覆盖（list/dict 字段整体替换）。"""
    cfg = copy.deepcopy(base)
    if override:
        for k, v in override.items():
            cfg[k] = v
    return cfg


def editable_keys() -> set[str]:
    return _EDITABLE_KEYS


def tier_name_for_rank(rank: int, tiers: list[dict]) -> str | None:
    """按名次（从 1 起）返回段位名；未命中返回 None。"""
    for t in tiers:
        if int(t.get("min", 1)) <= rank <= int(t.get("max", 1 << 30)):
            return str(t.get("name", ""))
    return None


def tier_gongde_for_rank(rank: int, tiers: list[dict]) -> int:
    for t in tiers:
        if int(t.get("min", 1)) <= rank <= int(t.get("max", 1 << 30)):
            return int(t.get("gongde", 0))
    return 0
