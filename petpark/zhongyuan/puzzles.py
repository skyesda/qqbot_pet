"""中式谜题生成：DeepSeek 现场生成 + 本地模板兜底。

每个母题对应一个本地模板函数，模板用「随机参数」生成题干、选项与正确项，
保证同一母题每次触发的答案都不同（无唯一答案）。DeepSeek 可用时优先调用大模型
生成更灵动、更贴合玩家的谜题；失败或关闭时回退到本地模板，活动不中断。

判分约定：
- answer_type == "int"：从玩家输入里取第一个整数与 answer 比较；
- answer_type == "str"：玩家输入去掉「答案是/答/第X」等前缀后与 answer 逐项匹配。
"""
from __future__ import annotations

import random
import re
from typing import Any

from .config import ACTIVITY_KEY

# ---------------------------------------------------------------------------
# 母题库（名称 + 中式文化主题说明）。后台可继续扩充。
# ---------------------------------------------------------------------------
THEMES = [
    "青灯引魂",   # 河灯 / 中元放灯
    "五行生克",   # 五行 / 阴阳
    "十二生肖",   # 生肖 / 地支
    "纸扎点灵",   # 纸扎 / 傀儡
    "符箓真伪",   # 符箓 / 道教科仪
    "铜钱问卦",   # 铜钱 / 六爻
    "鬼门开关",   # 阴司 / 鬼门
]

# 本地「规则怪谈」兜底（DeepSeek 可用时优先 AI 生成）
LOCAL_RULES = [
    "馆内只准点灯，不准吹灯。谁吹灭一盏灯，谁的影子就替它站到天明。",
    "夜半三更，若听见有人唤你名字，务必先数清自己脚下的影子，少一盏就别回头。",
    "供桌上的香，一炷不能断。香尽而灯未续者，须面壁抄写《规矩簿》三遍。",
    "馆中门扉共有七扇，逢单数时辰只可走单数门，走错者与门内之物对坐一炷香。",
    "灯油以七钱为限，多一分则魂魄外溢，少一分则灯芯噬主。",
]

# 五行相生：木→火→土→金→水→木
WUXING_SHENG = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
WUXING = list(WUXING_SHENG.keys())

# 十二地支与生肖（顺序一一对应）
DIZHI = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
SHENGXIAO = ["鼠", "牛", "虎", "兔", "龙", "蛇", "马", "羊", "猴", "鸡", "狗", "猪"]
DIZHI_TO_SHENGXIAO = dict(zip(DIZHI, SHENGXIAO))
# 生肖六冲
SHENGXIAO_CHONG = {
    "鼠": "马", "马": "鼠", "牛": "羊", "羊": "牛", "虎": "猴", "猴": "虎",
    "兔": "鸡", "鸡": "兔", "龙": "狗", "狗": "龙", "蛇": "猪", "猪": "蛇",
}

_INT_RE = re.compile(r"\d+")


def _int_puzzle(question: str, options: list[str], answer: int, hint: str, theme: str) -> dict[str, Any]:
    return {
        "question": question,
        "options": options,
        "answer": str(answer),
        "answer_type": "int",
        "hint": hint,
        "theme": theme,
        "source": "local",
    }


def _str_puzzle(question: str, options: list[str], answer: str, hint: str, theme: str) -> dict[str, Any]:
    return {
        "question": question,
        "options": options,
        "answer": answer,
        "answer_type": "str",
        "hint": hint,
        "theme": theme,
        "source": "local",
    }


# ---------------------------------------------------------------------------
# 本地模板
# ---------------------------------------------------------------------------
def _t_青灯引魂() -> dict:
    n = random.randint(5, 9)
    p = random.randint(1, n)
    a, b = p - 1, n - p
    question = (
        f"堂前 {n} 盏青灯排成一列，其中唯有一盏燃的是「阳火」，其余尽灭。"
        f"你定神数去：阳火左边 {a} 盏、右边 {b} 盏。阳火是第几盏？"
    )
    return _int_puzzle(question, [str(i) for i in range(1, n + 1)], p, "左边盏数加一即为阳火之位。", "青灯引魂")


def _t_五行生克() -> dict:
    x = random.choice(WUXING)
    ans = WUXING_SHENG[x]
    question = f"五行相生，生生不息。堂上供着五行牌位，问：「{x}」生何物？"
    return _str_puzzle(question, list(WUXING), ans, f"五行相生：木生火、火生土、土生金、金生水、水生木。", "五行生克")


def _t_十二生肖() -> dict:
    if random.random() < 0.5:
        z = random.choice(DIZHI)
        ans = DIZHI_TO_SHENGXIAO[z]
        question = f"十二地支轮转，问：地支「{z}」对应哪个生肖？"
        hint = "十二地支：子丑寅卯辰巳午未申酉戌亥。"
    else:
        s = random.choice(SHENGXIAO)
        ans = SHENGXIAO_CHONG[s]
        question = f"生肖相冲为忌。问：与生肖「{s}」相冲的是哪个生肖？"
        hint = "六冲：鼠马、牛羊、虎猴、兔鸡、龙狗、蛇猪。"
    return _str_puzzle(question, list(SHENGXIAO), ans, hint, "十二生肖")


def _t_纸扎点灵() -> dict:
    n = random.randint(4, 8)
    p = random.randint(1, n)
    a, b = p - 1, n - p
    question = (
        f"馆主扎了 {n} 只纸扎灵宠，只有一只被「点睛」而有了灵性。灵宠并排而立，"
        f"被点睛的那只左边有 {a} 只、右边有 {b} 只。是哪一只？"
    )
    return _int_puzzle(question, [str(i) for i in range(1, n + 1)], p, "从左边数，左边个数加一。", "纸扎点灵")


def _t_符箓真伪() -> dict:
    n = random.randint(6, 10)
    p = random.randint(1, n)
    q = n - p + 1
    question = (
        f"墙上贴着 {n} 张符箓，只有一张真符。道士低语：「真符，自右向左数第 {q} 张。」"
        f"那么自左向右数，真符是第几张？"
    )
    return _int_puzzle(question, [str(i) for i in range(1, n + 1)], p, "总数减右边序号再加一。", "符箓真伪")


def _t_铜钱问卦() -> dict:
    # 正面比反面多 d 枚，合计 n 枚（n、d 同奇偶，确保正反面为整数）
    n = random.choice([4, 6, 8])
    d = random.choice([0, 2, 4])
    while d > n:
        d -= 2
    if (n - d) % 2 != 0:
        d += 1
        if d > n:
            d = 0
    a = (n + d) // 2
    question = (
        f"占卦掷铜钱，掷得正面 {a} 枚、反面 {n - a} 枚。"
        f"已知「正面比反面多 {d} 枚，且正反合计 {n} 枚」，验证无误，共掷了几枚铜钱？"
    )
    return _int_puzzle(question, [str(i) for i in range(1, 13)], n, "正面 + 反面 = 总枚数。", "铜钱问卦")


def _t_鬼门开关() -> dict:
    # 生门左边门数 = 右边门数 × k；总门数 n，则 n-1 需能被 (k+1) 整除
    k = random.choice([1, 2, 3])
    # 找满足 (n-1) % (k+1) == 0 的 n（4~9）
    candidates = [n for n in range(4, 10) if (n - 1) % (k + 1) == 0]
    n = random.choice(candidates)
    right = (n - 1) // (k + 1)
    p = right * k + 1
    question = (
        f"幽影饲育馆有 {n} 扇门，其中只有一扇「生门」，其余通阴司。"
        f"你推得：生门左边的门数是右边门数的 {k} 倍。生门是第几扇（从左数）？"
    )
    return _int_puzzle(question, [str(i) for i in range(1, n + 1)], p, "设右边门数为 x，则 x + kx = n-1。", "鬼门开关")


_LOCAL_GENERATORS = {
    "青灯引魂": _t_青灯引魂,
    "五行生克": _t_五行生克,
    "十二生肖": _t_十二生肖,
    "纸扎点灵": _t_纸扎点灵,
    "符箓真伪": _t_符箓真伪,
    "铜钱问卦": _t_铜钱问卦,
    "鬼门开关": _t_鬼门开关,
}


def local_puzzle(theme: str | None = None) -> dict:
    """本地模板生成一道谜题（兜底用）。"""
    if theme in _LOCAL_GENERATORS:
        return _LOCAL_GENERATORS[theme]()
    return _LOCAL_GENERATORS[random.choice(THEMES)]()


def normalize_answer(user_text: str, puzzle: dict) -> str | None:
    """把玩家输入归一化为可判分的形式；无法判定返回 None。"""
    t = (user_text or "").strip()
    if not t:
        return None
    if puzzle.get("answer_type") == "int":
        m = _INT_RE.search(t)
        return m.group(0) if m else None
    # str：剥离常见前缀后与选项/答案比对
    t2 = re.sub(r"^(答案是|答案|答|选|我选|第|是)", "", t).strip()
    options = puzzle.get("options") or []
    for opt in options:
        if opt and (t == opt or t2 == opt):
            return opt
    # 用户输入纯数字 → 视为选项序号（1 起），对齐到对应选项原文
    if t2.isdigit() and options:
        idx = int(t2) - 1
        if 0 <= idx < len(options):
            return options[idx]
    # 兜底：答案关键词包含在输入里
    ans = str(puzzle.get("answer", ""))
    if ans and ans in t:
        return ans
    return t2 or t


def is_correct(user_text: str, puzzle: dict) -> bool:
    """判断玩家答案是否正确。"""
    ans = str(puzzle.get("answer", ""))
    if not ans:
        return False
    norm = normalize_answer(user_text, puzzle)
    if norm is None:
        return False
    if puzzle.get("answer_type") == "int":
        return norm == ans
    return norm == ans
