"""Jev（TypeSafe System One）本地调用工具。

Jev 不是聊天模型：它**不生成文本**，而是接收「state（要判断的内容）+
questions（带类型的问题）」，一次性并行返回**带校准概率的结构化答案**。
适合做分类、路由、打分、审核、护栏。

三种题型（可混在同一个请求里，一次全问完）：

| type     | 你给什么                          | 它回什么                                                   |
|----------|-----------------------------------|------------------------------------------------------------|
| `noul`   | 一个是非问句 / 陈述句             | `noul`: 答「是/成立」的概率 0~1（0.5 附近=不确定）          |
| `choice` | `criteria`: {选项名: 什么情况选它} | `choice` 选中项 + `confidence` + `probabilities` 全选项概率 |
| `score`  | `criteria`: [档位描述, ...]（有序）| `score` 概率加权均分（**可以是小数**）+ `legend` + `probabilities` |

用法（在本文件所在目录执行）：

    export TYPESAFE_API_KEY=apikey_xxx      # 或写进同目录 .jev_key（已 gitignore）
    python tools/jev_client.py              # 跑内置示例
    python tools/jev_client.py "帮我升个级"  # 换一句 state 跑同一套问题

注意：本机 HTTP(S)_PROXY 会让 TLS 握手失败，所以下面显式禁用了代理。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# ===========================================================================
# 一、连接配置
# ===========================================================================

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"  # 别名，当前指向 jev-1.13.0；也可写死具体版本
TIMEOUT = 30.0

_key_file = Path(__file__).with_name(".jev_key")


def load_api_key() -> str:
    """依次从 环境变量 → 同目录 .jev_key 读取密钥。"""
    key = (os.environ.get("TYPESAFE_API_KEY") or "").strip()
    if key:
        return key
    if _key_file.exists():
        return _key_file.read_text(encoding="utf-8").strip()
    raise SystemExit(
        "缺少 API Key。二选一：\n"
        "  export TYPESAFE_API_KEY=apikey_xxx\n"
        f"  或把密钥写进 {_key_file}"
    )


# 禁用代理：本机 127.0.0.1:7897 代理会让 api.typesafe.ai 的 TLS 握手失败
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def ask(state, questions: dict, model: str = MODEL, timeout: float = TIMEOUT) -> dict:
    """发一次请求，返回完整响应体（含 answers / usage）。

    state    : 要判断的内容。字符串、dict、list 都行（结构化字段比长文本更省 token）。
    questions: {"问题名": {题型定义}}，问题名由你自己起，响应里用同名 key 回给你。
    """
    body = json.dumps(
        {"model": model, "state": state, "questions": questions},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {load_api_key()}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with _opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise SystemExit(f"HTTP {e.code}：{detail}") from None


# --- 三种题型的便捷包装（只组装问题，不含任何业务判定） --------------------


def q_noul(instructions: str, criteria: dict | None = None) -> dict:
    """是非题。criteria 可选，形如 {"true": "什么算是", "false": "什么算否"}。"""
    q = {"type": "noul", "instructions": instructions}
    if criteria:
        q["criteria"] = criteria
    return q


def q_choice(instructions: str, criteria: dict) -> dict:
    """单选题。criteria 形如 {"选项名": "什么情况选它"}，描述可以不写（只靠名字）。"""
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def q_score(instructions: str, criteria: list) -> dict:
    """打分题。criteria 是**有序数组**，下标即分值（0 起）。"""
    return {"type": "score", "instructions": instructions, "criteria": criteria}


# ===========================================================================
# 二、输出判定：把概率变成「做不做 / 做哪个」
# ===========================================================================
# Jev 只给概率，**阈值是业务决策，必须你自己定**，这是它和聊天模型最大的区别。

# 是非题的判定门槛。**别默认取 0.5**，正确做法是实测一批样本，看两类分数的分布，
# 把门槛放进中间那段空白带里。本文件示例的实测值：
#   该执行的  「帮我升个级」0.92、「签到」0.74
#   该拒绝的  「我都已经进化过了」0.04、「进化要多少金币」0.06
# 两边隔着 0.06~0.74 一大段空白，取 0.7 稳；取 0.9 会把「签到」误杀。
NOUL_YES = 0.7

# 单选题最低置信度。低于它视为「模型也不知道」，应当放弃执行而不是硬选一个
CHOICE_MIN_CONFIDENCE = 0.5


def decide_noul(ans: dict, threshold: float = NOUL_YES) -> bool:
    """是非题 → 布尔。ans 是 answers["问题名"]。"""
    return float(ans.get("noul", 0.0)) >= threshold


def decide_choice(ans: dict, min_confidence: float = CHOICE_MIN_CONFIDENCE):
    """单选题 → 选中的选项名，置信度不够则返回 None。"""
    if float(ans.get("confidence", 0.0)) < min_confidence:
        return None
    return ans.get("choice")


def decide_score(ans: dict, cuts: list[float] | None = None):
    """打分题 → 档位下标。

    score 是概率加权均分，可能是 1.57 这种小数。不给 cuts 就四舍五入到最近档位；
    给了 cuts（升序阈值）就按区间落档，如 cuts=[1.5, 2.5] 把分数切成 0/1/2 档。
    """
    s = float(ans.get("score", 0.0))
    if cuts:
        return sum(1 for c in cuts if s >= c)
    return int(round(s))


# ===========================================================================
# 三、【改提示词就在这里】示例：把群里的自然语言翻成灵契仙途标准指令
# ===========================================================================
# 这套问题对标 petpark/ai_router.py 的第 3 层。改法见文件末尾说明。

# 候选指令表；key 是最终要执行的标准指令名，value 是给模型看的判定说明。
# 新增指令就在这里加一行——模型是照这份说明选的，说明写得越具体越准。
CMD_CRITERIA = {
    "宠物升级": "明确要求升级宠物，例如「帮我升个级」「升 3 级」",
    "宠物进化": "明确要求宠物进化",
    "签到": "明确要求签到 / 打卡",
    "无": "陈述已发生的事、提问、讨论玩法、闲聊，或以上都不是",
}


def build_questions(cmd_criteria: dict) -> dict:
    """组装问题组。三个问题一次请求并行算完，不额外增加延迟。"""
    return {
        # 第一道闸：是不是在「要求现在执行」？陈述句必须挡在这里
        "is_command": q_noul(
            "玩家是否在要求立即执行一个游戏操作？",
            {"true": "祈使句，要求现在就去执行", "false": "陈述已发生的事、提问、闲聊"},
        ),
        # 第二道闸：到底是哪条指令
        "which_cmd": q_choice("这条消息最匹配哪条指令？", cmd_criteria),
        # 旁证：模型对自己判断的确信程度
        "confidence": q_score("你对上面两个判断有多确信？", ["很低", "低", "中", "高", "很高"]),
    }


def route(state: str) -> str | None:
    """跑完整流程：提问 → 判定 → 返回要执行的指令，或 None 表示不执行。"""
    resp = ask(state, build_questions(CMD_CRITERIA))
    ans = resp["answers"]

    # 两道闸都过才执行：先看是不是指令，再看具体是哪条、够不够确信
    if not decide_noul(ans["is_command"], threshold=NOUL_YES):
        return None
    cmd = decide_choice(ans["which_cmd"], min_confidence=0.5)
    if cmd in (None, "无"):
        return None
    return cmd


def _demo(state: str) -> None:
    resp = ask(state, build_questions(CMD_CRITERIA))
    ans, usage = resp["answers"], resp.get("usage", {})
    print(f"state: {state}")
    print(f"model: {resp.get('model')}   tokens: in={usage.get('input_tokens')} out={usage.get('output_tokens')}")
    for name, a in ans.items():
        print(f"  {name:12s} {json.dumps(a, ensure_ascii=False)}")
    hit = route(state)
    print(f"  => 执行: {hit if hit else '不执行（判定为非指令/不确定）'}\n")


if __name__ == "__main__":
    # 直接给一句话就只跑那句，否则跑下面这组示例——覆盖「该执行」和「不该执行」两类
    cases = sys.argv[1:] or [
        "帮我升个级",          # 明确指令 → 应执行
        "我都已经进化过了",     # 陈述句   → 应拒绝
        "进化要多少金币",       # 提问     → 应拒绝
        "签到",                # 精确指令 → 应执行
    ]
    for c in cases:
        _demo(c)
