"""月饼重制挑战 —— 专门为「月饼再次合成」写的小挑战题库。

与 ``puzzles.py``（灯谜/巡礼题库）**刻意分开**：重制挑战是「有唯一确定答案的手艺
考核」（配方记忆 / 倍数心算 / 工序排序 / 火候估时 / 月相推演），不是文化常识或谜语
连答，所以不复用那边的大题库。

出题与判分约定：
- 每题统一结构 ``{kind, q, a, answer_type, hint}``，语义题额外带 ``pass_keys``；
- 判分一律复用 ``puzzles.normalize_answer`` / ``puzzles.is_correct``（含同义归一），
  **不另写一套判分器**；
- 难度只由「步数 + 数值区间 + 限时」控制，题型池按阶段放开（见 ``_KINDS_BY_STAGE``）。

Jev 语义题（``kind == "semantic"``）的判定顺序：
1. 命中 ``pass_keys`` 任一关键词 → **直接算对**（客观命中无条件通过，照灯谜「字面
   命中优先」的口径，也省掉一次请求）；
2. 未命中且 Jev 可用 → 问 Jev「玩家回答是否合理回答了这道题」；
3. 未命中且 Jev 不可用 → 算错。

**Jev 不可用时不放水**：``build_steps`` 的 ``jev_available=False`` 会让语义题根本
不出现在题面里，改用同阶段等量的确定性题顶上（而不是出个语义题然后自动判过）——
线上当前就没配 Jev Key，若按「不可用即放行」处理，高阶阶段会退化成点两下就过。
"""
from __future__ import annotations

import random
import re
from typing import Any

from . import moonphase as MP
from . import puzzles

# 阶段 → 可用题型池
_KINDS_BY_STAGE: dict[int, tuple[str, ...]] = {
    1: ("recipe", "multiply"),
    2: ("recipe", "multiply", "order"),
    3: ("recipe", "multiply", "order", "timing"),
    4: ("recipe", "multiply", "order", "timing", "phase"),
    5: ("recipe", "multiply", "order", "timing", "phase"),
}

# 出语义题的最低阶段（4 阶段起掺 1 道）
SEMANTIC_MIN_STAGE = 4

# ---------------------------------------------------------------------------
# 素材
# ---------------------------------------------------------------------------
_INGREDIENTS = ["桂花", "蜂蜜", "面粉", "蛋黄", "莲蓉", "冰皮", "糖浆", "坚果",
                "豆沙", "猪油", "枧水", "咸蛋黄", "糯米粉", "植物油"]

_FLAVORS = ["五仁", "豆沙", "蛋黄莲蓉", "冰皮", "流心"]

# 月饼标准工序（顺序即正解）
_CRAFT_STEPS = ["和面", "醒面", "制馅", "包馅", "压模", "烘烤", "回油"]

# 4 个可作推演起点的标准月相（月龄用平朔望月的名义值）
_PHASE_STARTS: list[tuple[str, float]] = [
    ("新月", 0.0),
    ("上弦月", MP.SYNODIC_MONTH / 4.0),
    ("满月", MP.SYNODIC_MONTH / 2.0),
    ("下弦月", MP.SYNODIC_MONTH * 3.0 / 4.0),
]
_PHASE_OFFSETS = (4, 7, 8, 11, 15)
# 目标月龄距相界太近就丢弃（玩家按 3.7 天/相推出来的会跟程序算的是相邻两相）
_PHASE_EDGE_MARGIN = 0.5

# Jev 语义题：``keys`` 是判分关键词（命中任一即算对），也是 Jev 不可用时的兜底
_SEMANTIC_ITEMS: list[dict[str, Any]] = [
    {"q": "冰皮月饼为什么不用烘烤？",
     "keys": ["糯米", "蒸", "冷藏", "冰皮", "不用烤", "免烤", "凉"],
     "hint": "想想它的皮是什么做的、又是怎么定型的。"},
    {"q": "流心月饼里的「流心」是怎么来的？",
     "keys": ["馅", "夹心", "奶黄", "咸蛋黄", "流沙", "加热", "融化", "遇热"],
     "hint": "关键在于两种馅的软硬与受热后的变化。"},
    {"q": "五仁月饼的「五仁」通常指哪些果仁？",
     "keys": ["花生", "核桃", "瓜子", "杏仁", "芝麻", "橄榄仁", "果仁", "坚果"],
     "hint": "至少说出两三种常见的果仁。"},
    {"q": "月饼做好后为什么要「回油」几天？",
     "keys": ["糖浆", "渗", "变软", "油", "皮", "色泽", "转化糖浆"],
     "hint": "想想饼皮里的糖浆和油脂会往哪儿走。"},
    {"q": "蛋黄莲蓉月饼里的咸蛋黄一般取自什么蛋？",
     "keys": ["鸭蛋", "咸鸭蛋", "鸭"],
     "hint": "腌制咸蛋用的那种蛋。"},
    {"q": "中秋节为什么要吃月饼？",
     "keys": ["团圆", "圆", "祭月", "拜月", "团聚", "供"],
     "hint": "从月饼的形状和节日的主题想一想。"},
    {"q": "桂花为什么常在中秋前后出现？",
     "keys": ["八月", "秋", "开花", "花期", "香", "季节"],
     "hint": "想想它的花期赶在哪个时节。"},
    {"q": "广式月饼的饼皮为什么能又薄又软？",
     "keys": ["糖浆", "枧水", "转化糖浆", "回油", "碱水"],
     "hint": "关键在拌皮时加的那两样东西。"},
]


def _int_from(text: str) -> int | None:
    m = re.search(r"-?\d+", text or "")
    return int(m.group()) if m else None


# ---------------------------------------------------------------------------
# 各题型生成器
# ---------------------------------------------------------------------------
def make_recipe(rng: random.Random, stage: int) -> dict[str, Any]:
    """配方记忆：给出用料序列，问其中某一味。"""
    length = 5 if stage <= 2 else 6
    seq = rng.sample(_INGREDIENTS, length)
    idx = rng.randrange(length)
    return {
        "kind": "recipe",
        "q": f"🥮 今日配方：「{' → '.join(seq)}」。排在第 {idx + 1} 味的是哪一样？",
        "a": seq[idx],
        "answer_type": "str",
        "hint": "按箭头方向从左往右数，注意别数错开头。",
    }


def make_multiply(rng: random.Random, stage: int) -> dict[str, Any]:
    """配料倍数心算：份数 × 每份用量。"""
    if stage <= 2:
        n, m = rng.randint(2, 5), rng.randint(2, 6)
    elif stage == 3:
        n, m = rng.randint(3, 8), rng.randint(3, 9)
    else:
        n, m = rng.randint(4, 9), rng.randint(4, 12)
    flavor, ing = rng.choice(_FLAVORS), rng.choice(_INGREDIENTS)
    return {
        "kind": "multiply",
        "q": f"🥮 要做 {n} 份{flavor}月饼，每份需要 {m} 钱{ing}，一共需要几钱{ing}？",
        "a": str(n * m),
        "answer_type": "int",
        "hint": "份数乘以每份用量。直接回一个数字就行。",
    }


def make_order(rng: random.Random, stage: int) -> dict[str, Any]:
    """工序排序：给打乱的四道工序，问某道之后紧接着的是哪道。"""
    start = rng.randrange(0, len(_CRAFT_STEPS) - 4)
    window = _CRAFT_STEPS[start:start + 4]
    # 提问点只取前三道，保证「之后紧接着」一定有答案
    ask_i = rng.randrange(0, 3)
    shown = window[:]
    rng.shuffle(shown)
    marks = "①②③④"
    display = " ".join(f"{marks[i]}{s}" for i, s in enumerate(shown))
    return {
        "kind": "order",
        "q": f"🥮 月饼工序被打乱了：{display}。按做月饼的常理，「{window[ask_i]}」之后紧接着的一步是什么？",
        "a": window[ask_i + 1],
        "answer_type": "str",
        "hint": "和面醒面在前，制馅包馅居中，压模烘烤回油在后。",
    }


def make_timing(rng: random.Random, stage: int) -> dict[str, Any]:
    """火候估时：三段用时求和。"""
    if stage <= 2:
        a, b, c = rng.randint(5, 15), rng.randint(5, 15), rng.randint(3, 10)
    elif stage == 3:
        a, b, c = rng.randint(8, 22), rng.randint(6, 20), rng.randint(5, 15)
    else:
        a, b, c = rng.randint(10, 30), rng.randint(8, 25), rng.randint(5, 20)
    return {
        "kind": "timing",
        "q": (f"🥮 一炉月饼：先烤 {a} 分钟，取出刷蛋液后再烤 {b} 分钟，"
              f"最后晾凉 {c} 分钟。从头到尾一共多少分钟？"),
        "a": str(a + b + c),
        "answer_type": "int",
        "hint": "三段相加。直接回一个数字就行。",
    }


def make_phase_question(rng: random.Random, stage: int) -> dict[str, Any]:
    """月相推演：从某个标准月相出发，推 N 天后的月相。

    只在「目标月龄离相界足够远」时出题（见 ``_PHASE_EDGE_MARGIN``）：落在相界上
    的题，玩家按 3.7 天/相推出来的答案和程序算出来的会是相邻两相，等于没有正解。
    候选中至少有一半以上是合法的，所以这里重试几次即可，不写复杂的构造。
    """
    for _ in range(24):
        name, nominal = rng.choice(_PHASE_STARTS)
        n = rng.choice(_PHASE_OFFSETS)
        target_age = (nominal + n) % MP.SYNODIC_MONTH
        if MP.phase_distance_to_edge(target_age) < _PHASE_EDGE_MARGIN:
            continue
        answer = MP.phase_name_for_age(target_age)
        return {
            "kind": "phase",
            "q": (f"🌙 今夜是{name}（月龄约 {nominal:.1f} 天，一个朔望月约 "
                  f"{MP.SYNODIC_MONTH:.1f} 天）。再过 {n} 天是什么月相？"),
            "a": answer,
            "answer_type": "str",
            "hint": "月相一循环约 29.5 天，8 个月相各约 3.7 天，按顺序往下数。",
        }
    # 兜底（理论上到不了）：退回最安全的组合
    return {
        "kind": "phase",
        "q": (f"🌙 今夜是新月（月龄约 0.0 天，一个朔望月约 {MP.SYNODIC_MONTH:.1f} 天）。"
              "再过 7 天是什么月相？"),
        "a": MP.phase_name_for_age(7.0),
        "answer_type": "str",
        "hint": "月相一循环约 29.5 天，8 个月相各约 3.7 天。",
    }


def make_semantic(rng: random.Random, stage: int) -> dict[str, Any]:
    """Jev 语义题：开放回答，关键词命中或 Jev 判「答得合理」即通过。"""
    item = rng.choice(_SEMANTIC_ITEMS)
    return {
        "kind": "semantic",
        "q": f"🥮 {item['q']}（用一句话说清即可）",
        "a": "／".join(item["keys"][:3]),
        "answer_type": "str",
        "hint": item["hint"],
        "pass_keys": list(item["keys"]),
    }


_KIND_FACTORY = {
    "recipe": make_recipe,
    "multiply": make_multiply,
    "order": make_order,
    "timing": make_timing,
    "phase": make_phase_question,
    "semantic": make_semantic,
}


# ---------------------------------------------------------------------------
# 对外：建一局挑战
# ---------------------------------------------------------------------------
def kinds_for_stage(stage: int) -> tuple[str, ...]:
    """该阶段可用的确定性题型（不含语义题）。"""
    return _KINDS_BY_STAGE.get(max(1, min(int(stage), 5)), _KINDS_BY_STAGE[1])


def build_steps(now_ts: float, stage: int, count: int, jev_available: bool,
                rng: random.Random | None = None) -> list[dict[str, Any]]:
    """为一次重制挑战生成 ``count`` 道题（阶段 4/5 且 Jev 可用时掺 1 道语义题）。

    ``jev_available=False`` 时**不会**出现语义题：用同阶段的确定性题补足步数，
    保证高阶挑战依然要真答对才能过。
    """
    rng = rng or random.Random()
    stage = max(1, min(int(stage), 5))
    count = max(1, int(count))
    pool = kinds_for_stage(stage)
    steps: list[dict[str, Any]] = []
    semantic_allowed = bool(jev_available) and stage >= SEMANTIC_MIN_STAGE
    semantic_at = count - 1 if semantic_allowed else -1
    for i in range(count):
        kind = "semantic" if i == semantic_at else rng.choice(pool)
        steps.append(_KIND_FACTORY[kind](rng, stage))
    return steps


# ---------------------------------------------------------------------------
# 对外：判分
# ---------------------------------------------------------------------------
def keyword_hit(step: dict[str, Any], user_text: str) -> bool:
    """语义题的关键词兜底判定（命中任一即可）。"""
    text = (user_text or "").strip()
    if not text:
        return False
    return any(k and k in text for k in (step.get("pass_keys") or []))


def is_correct(step: dict[str, Any], user_text: str) -> bool:
    """确定性题判分（复用 puzzles 的归一化/同义逻辑）。"""
    return puzzles.is_correct(user_text, {
        "answer": step.get("a"),
        "answer_type": step.get("answer_type", "str"),
    })


def grade(step: dict[str, Any], user_text: str, jev=None) -> tuple[bool, bool]:
    """判分 → ``(是否正确, 是否由 Jev 判对)``。

    语义题的判定顺序见模块 docstring：关键词命中优先（不请求 Jev），未命中且 Jev
    可用才问 Jev；Jev 不可用且未命中关键词则算错（绝不放水）。
    """
    if step.get("kind") != "semantic":
        return is_correct(step, user_text), False
    if keyword_hit(step, user_text):
        return True, False
    if jev is not None and _jev_ready(jev):
        verdict = jev.noul_bool(
            {"题目": step.get("q"), "玩家回答": (user_text or "").strip()},
            "玩家的回答是否合理回答了这道题（不要求与参考答案逐字一致，意思对即可）？",
            {"true": "回答到了要点、方向正确，即使措辞与参考答案不同也算对",
             "false": "答非所问、明显错误或与题意无关"},
        )
        if verdict is True:
            return True, True
    return False, False


def _jev_ready(jev) -> bool:
    """Jev 是否真的可用（没有 available() 的老封装按「可用」处理，靠它自己兜底）。"""
    fn = getattr(jev, "available", None)
    if callable(fn):
        try:
            return bool(fn())
        except Exception:  # noqa: BLE001 - 任何异常都当作不可用
            return False
    return bool(getattr(jev, "enabled", False))
