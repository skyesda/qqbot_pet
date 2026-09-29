"""月相推算 —— 纯本地确定性计算，不联网、不依赖任何第三方库。

用途（两处共用同一份推算，避免两套口径打架）：
- 月饼重制挑战里的「月相推演」题（见 challenges.make_phase_question）；
- 桂花酿的「近满加成」：距望日 ≤ full_moon_window_days 天时酿出「酿王」概率翻倍。

**拜月刻意不用月相**：它就是每日普通签到（固定月华，不分档、不赠物）。

口径：以已知朔日 2000-01-06 18:14 UTC 为锚点，朔望月长 29.530588853 天（平朔望
月），线性外推出任意时刻的月龄。这是个**近似**（真实朔望月有 ±0.3 天的摄动），
但对「今夜月相叫什么」和「再过几天是什么月相」这种玩法量级的判定完全够用，而且
好处是**完全确定**：同一时刻永远算出同一结果，可写进单元测试当锚点。

月龄 age ∈ [0, 29.5306)，0 = 朔（新月），14.765 = 望（满月）。
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

_BJ = ZoneInfo("Asia/Shanghai")
_UTC = dt.timezone.utc

# 已知朔（新月）：2000-01-06 18:14 UTC
_NEW_MOON_EPOCH = dt.datetime(2000, 1, 6, 18, 14, tzinfo=_UTC)
# 平朔望月长度（天）
SYNODIC_MONTH = 29.530588853
# 望 = 月龄走到半个朔望月
FULL_MOON_AGE = SYNODIC_MONTH / 2.0

# 8 个月相（每相约 3.69 天）
PHASES = ["新月", "蛾眉月", "上弦月", "盈凸月", "满月", "亏凸月", "下弦月", "残月"]

_DAY = 86400.0


def _age_at(ts: float) -> float:
    """给定 Unix 时间戳，返回月龄（天，已对朔望月取模）。"""
    days = (float(ts) - _NEW_MOON_EPOCH.timestamp()) / _DAY
    return days % SYNODIC_MONTH


def moon_age(ts: float) -> float:
    """月龄（天，0=朔，约 14.77=望）。"""
    return _age_at(ts)


def phase_index_from_age(age: float) -> int:
    """月龄 → 8 相下标。

    每相宽 ``SYNODIC_MONTH / 8``，并整体偏移半相（``SYNODIC_MONTH / 16``），
    这样「朔」「望」正好落在「新月」「满月」两相的中点，而不是压在相界上。
    """
    span = SYNODIC_MONTH / 8.0
    return int(((age + span / 2.0) % SYNODIC_MONTH) // span) % 8


def phase_name(ts: float) -> str:
    """给定时刻的月相名（8 相之一）。"""
    return PHASES[phase_index_from_age(_age_at(ts))]


def phase_name_for_age(age: float) -> str:
    """月龄 → 月相名（月相推演题用：拿算出来的目标月龄直接取名字）。"""
    return PHASES[phase_index_from_age(age % SYNODIC_MONTH)]


def days_from_full_moon(ts: float) -> float:
    """距最近一次「望」的天数（0 = 正在望，最大约 14.77 = 新月）。"""
    return abs(_age_at(ts) - FULL_MOON_AGE)


def phase_distance_to_edge(age: float) -> float:
    """月龄距**最近一个相界**的天数。

    「月相推演」题必须在相界附近避免出题：答案落在相界上时，玩家按「每相约
    3.7 天」推出来的月份和程序算出来的会是相邻的两相，题就变得没有正确答案。
    出题器用它来丢弃边界题（见 challenges.make_phase_question）。
    """
    span = SYNODIC_MONTH / 8.0
    offset = (age + span / 2.0) % span  # 距本相起点的距离
    return min(offset, span - offset)


def is_near_full(ts: float, window_days: float) -> bool:
    """是否处于「近满」窗口（距最近一次望 ≤ ``window_days`` 天）。

    桂花酿用它决定「酿王」概率是否翻倍。``window_days`` 默认必须 ≥ 3：2026 年的
    望在 09-26 22:51，而中秋活动窗（09-27 ~ 10-01）从望的次日才开始，窗口只给
    1 天的话只有 09-27 命中、之后整段活动里这个加成都是死的；3 天窗口 →
    09-27/28/29 连续三天有效。
    """
    return days_from_full_moon(ts) <= max(1.0, float(window_days))


def local_date_string(ts: float) -> str:
    """北京时间日期字符串（诊断/日志用）。"""
    return dt.datetime.fromtimestamp(float(ts), _BJ).strftime("%Y-%m-%d")


def full_moon_timestamps(start_ts: float, end_ts: float) -> list[float]:
    """[start_ts, end_ts] 区间内所有「望」的时刻（测试与后台诊断用）。"""
    out: list[float] = []
    age = _age_at(start_ts)
    # 先推进到区间内第一个望
    delta = (FULL_MOON_AGE - age) % SYNODIC_MONTH
    t = float(start_ts) + delta * _DAY
    while t <= float(end_ts):
        out.append(t)
        t += SYNODIC_MONTH * _DAY
    return out
