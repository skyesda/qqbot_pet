"""DeepSeek 大模型客户端（OpenAI 兼容接口）。

用于现场生成中式怪谈谜题、文化问答、温情回文、诗词与 NPC 文案。
API Key 一律从环境变量 ``DEEPSEEK_API_KEY`` 读取（可被显式配置覆盖），
绝不写死进代码或文档，防止泄露。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger("petpark.zhongyuan.deepseek")

# 谜题生成用的 system prompt（中式文化 + 中元主题 + 合规 + 无唯一答案）
PUZZLE_SYSTEM_PROMPT = (
    "你是一位深谙中国中元节文化与传统民俗（符箓、五行、生肖、河灯、纸扎、阴司、铜钱卦、鬼门）的"
    "谜题设计者。请根据用户给出的母题，生成一道「规则怪谈」风格的中式解密谜题。要求："
    "1) 谜题必须给出唯一可判定的正确项（随随机参数确定，每次不同）；"
    "2) 文案用半文言，营造阴森克制的氛围，但不得出现血腥、猎奇、政治敏感或迷信误导内容；"
    "3) 谜题应为文字选择题或短答，附 3~6 个选项。"
    "只输出一个 JSON 对象，字段为：question(题干)、options(选项字符串数组)、"
    "answer(正确项，须等于某个选项的原文)、hint(一句提示)。不要输出 JSON 以外的任何内容。"
)

# 规则怪谈生成用的 system prompt（本场解密总线索，题目线索皆由此规则引出）
RULE_SYSTEM_PROMPT = (
    "你是中元节「幽影饲育馆」的馆主。请制定一整套「规则怪谈」式的馆内规则，作为本场解密的总线索。"
    "要求："
    "1) 给出 5~8 条独立的馆规，条与条之间的线索类型互不相同（数目、时辰、方位、动作、"
    "暗语、灯烛、铃铛、铜钱、铜镜、纸扎、香火、门扉等），避免雷同；"
    "2) 每条半文言、阴森克制、有东方怪谈质感，各自暗藏一个可解谜的「暗号/禁忌/数目/次序」；"
    "3) 不血腥、不猎奇、无政治敏感或迷信误导；"
    "4) 总篇幅 150~250 字，以「其一、其二、其三…」分条列写。"
    "只输出规则本身，不要任何解释、前缀或引号。"
)

# 围绕规则怪谈批量生成谜题（题目线索一律回扣该规则）
RULE_PUZZLE_SYSTEM_PROMPT = (
    "你是中元节「幽影饲育馆」的谜题设计者。馆主已定下一整套「规则怪谈」（含多条独立规则）。"
    "你要围绕这套规则设计一组解密谜题。要求："
    "1) 每题有唯一可判定的正确项；2) 文字选择题或短答，附 3~6 个选项；"
    "3) 半文言、阴森克制、不血腥、不猎奇、无政治敏感或迷信误导；"
    "4) 题目务必分散取材于规则中的不同线索点（数目、时辰、方位、动作、暗语、灯烛、铃铛、"
    "铜钱、铜镜、纸扎、香火、门扉等），每题回扣不同的规则条，题目之间不得重复或雷同。"
    "只输出一个 JSON 数组，元素字段为：question(题干)、options(选项字符串数组)、"
    "answer(正确项，须等于某个选项的原文)、hint(一句提示)。不要输出 JSON 以外的任何内容。"
)


class DeepSeekClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 30,
        temperature: float = 0.3,
        max_tokens: int = 800,
    ):
        self.base_url = (base_url or "https://api.deepseek.com").rstrip("/")
        self.api_key = api_key
        self.model = model or "deepseek-v4-flash-vision-exp"
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    async def chat(
        self,
        user: str,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """调用 DeepSeek 对话接口，返回 assistant 文本。失败抛异常由调用方兜底。"""
        import aiohttp

        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                *([{"role": "system", "content": system}] if system else []),
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(f"DeepSeek HTTP {resp.status}: {body[:200]}")
                data = await resp.json()
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"DeepSeek 响应格式异常：{e}") from e

    async def generate_puzzle(self, theme: str) -> dict | None:
        """按母题生成一道谜题，返回 dict(question/options/answer/hint)；失败返回 None。"""
        user = f"母题：{theme}。请生成谜题。"
        try:
            text = await self.chat(user, system=PUZZLE_SYSTEM_PROMPT)
        except Exception as e:  # noqa: BLE001
            logger.warning("[zhongyuan] DeepSeek 谜题生成失败：%s", e)
            return None
        return self._parse_puzzle(text)

    async def generate_rule(self) -> str | None:
        """生成一整套「规则怪谈」作为本场解密总线索；失败返回 None。"""
        try:
            text = await self.chat(
                "请制定一整套本馆的规则怪谈。",
                system=RULE_SYSTEM_PROMPT,
                max_tokens=2000,
            )
            return text.strip() or None
        except Exception as e:  # noqa: BLE001
            logger.warning("[zhongyuan] DeepSeek 规则怪谈生成失败：%s", e)
            return None

    async def generate_puzzles_batch(self, rule: str, count: int) -> list[dict]:
        """围绕规则怪谈分批生成 count 道谜题（题干/提示回扣规则）；失败返回空列表。

        推理模型 deepseek-v4-flash-vision-exp 在单次生成过多题目时，会把输出
        token 消耗在 reasoning_content 上，导致最终 content 为空、解析出 0 题；
        并发请求又易触发限流/空 content。故拆成每批 4 题串行生成，单批任务
        足够简单、content 稳定返回 JSON，一批失败自动降档重试。
        """
        if count <= 0:
            return []
        batch = 4
        total_batches = (count + batch - 1) // batch
        out: list[dict] = []
        guard = 0
        while len(out) < count and guard < 12:
            guard += 1
            need = min(batch, count - len(out))
            user = (
                f"规则怪谈：{rule}\n"
                f"请围绕此规则生成 {need} 道谜题（本场第 {guard} 批，共 {total_batches} 批；"
                f"请优先取规则中尚未用到的线索点，避免与他批重复）。"
            )
            try:
                text = await self.chat(
                    user,
                    system=RULE_PUZZLE_SYSTEM_PROMPT,
                    max_tokens=max(1200, int(need) * 300),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("[zhongyuan] DeepSeek 批量谜题生成失败：%s", e)
                break
            parsed = self._parse_puzzle_batch(text)
            if not parsed:
                # 单批 content 为空/格式异常：降档重试，仍失败则停止（由本地模板兜底）
                if batch > 1:
                    batch -= 1
                    continue
                break
            out.extend(parsed)
        return out[:count]

    async def reply_echo(self, prompt: str, system: str | None = None) -> str | None:
        """生成温情回文 / 文案，失败返回 None。"""
        try:
            return await self.chat(prompt, system=system)
        except Exception as e:  # noqa: BLE001
            logger.warning("[zhongyuan] DeepSeek 文案生成失败：%s", e)
            return None

    async def ping(self) -> tuple[bool, str, float]:
        """连通性测试：返回 (是否成功, 说明, 耗时秒)。供后台「测试连接」按钮调用。"""
        import asyncio
        import time as _time

        if not self.available:
            return False, "未配置 API Key（或未从环境变量 DEEPSEEK_API_KEY 读取到）", 0.0
        t0 = _time.monotonic()
        try:
            text = await asyncio.wait_for(
                self.chat("请回复「正常」二字。", system="连通性测试。", max_tokens=16),
                timeout=15,
            )
        except asyncio.TimeoutError:
            return False, "连接超时（15 秒无响应）", _time.monotonic() - t0
        except Exception as e:  # noqa: BLE001
            return False, f"连接失败：{e}", _time.monotonic() - t0
        dt = _time.monotonic() - t0
        if not text:
            return False, "连接成功但返回为空", dt
        return True, f"连接正常，模型回复：{text.strip()[:24]}", dt

    @staticmethod
    def _strip_option_prefix(text: str) -> str:
        """剥离选项/答案的编号前缀（A. / 1、 / ③ 等），返回纯文本。"""
        import re
        return re.sub(r"^[A-Za-z0-9０-９①②③④⑤⑥⑦⑧⑨]+[\.、．:：\)）]\s*", "", str(text)).strip()

    @staticmethod
    def _match_answer_to_option(answer: str, options: list[str]) -> str | None:
        """把模型 answer（可能是 A/B/C、1/2/3 或选项原文）对齐到选项原文；失败返回 None。"""
        import re
        a = str(answer).strip()
        if not a or not options:
            return None
        if a in options:
            return a
        # 去掉选项编号前缀（A. / 1、 / ③ 等）后与 answer 比对
        for opt in options:
            stripped = re.sub(r"^[A-Za-z0-9０-９①②③④⑤⑥⑦⑧⑨]+[\.、．:：\)）]\s*", "", opt).strip()
            if stripped and a == stripped:
                return opt
        # 单字母 A/B/C… → 按字母序映射
        if len(a) == 1 and a.isalpha():
            idx = ord(a.upper()) - ord("A")
            if 0 <= idx < len(options):
                return options[idx]
        # 纯数字 → 序号（1 起）
        if a.isdigit():
            idx = int(a) - 1
            if 0 <= idx < len(options):
                return options[idx]
        return None

    @staticmethod
    def _parse_puzzle(text: str) -> dict | None:
        """把模型输出解析为谜题 dict；容忍 ```json 围栏与前后空白。"""
        if not text:
            return None
        cleaned = text.strip()
        # 去掉 Markdown 代码围栏
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()
        # 取第一个 { ... } 块
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end < start:
            return None
        try:
            obj = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
        question = str(obj.get("question", "")).strip()
        options = [s for s in (DeepSeekClient._strip_option_prefix(o) for o in obj.get("options", [])) if s]
        answer = DeepSeekClient._strip_option_prefix(str(obj.get("answer", "")))
        hint = str(obj.get("hint", "")).strip()
        if not question or not answer:
            return None
        if options and answer not in options:
            # answer 可能是 A/B/C 或序号，先对齐到选项原文
            mapped = DeepSeekClient._match_answer_to_option(answer, options)
            answer = mapped or options[0]
        return {
            "question": question,
            "options": options,
            "answer": answer,
            "hint": hint,
            "theme": "",
            "source": "deepseek",
        }

    @classmethod
    def _parse_puzzle_batch(cls, text: str) -> list[dict]:
        """把模型输出的 JSON 数组解析为谜题列表；容忍围栏与前后空白，逐条清洗。"""
        if not text:
            return []
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start < 0 or end <= start:
            return []
        try:
            arr = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return []
        if not isinstance(arr, list):
            return []
        out: list[dict] = []
        for obj in arr:
            if not isinstance(obj, dict):
                continue
            question = str(obj.get("question", "")).strip()
            options = [s for s in (cls._strip_option_prefix(o) for o in obj.get("options", [])) if s]
            answer = cls._strip_option_prefix(str(obj.get("answer", "")))
            hint = str(obj.get("hint", "")).strip()
            if not question or not answer:
                continue
            if options and answer not in options:
                mapped = cls._match_answer_to_option(answer, options)
                answer = mapped or options[0]
            out.append({
                "question": question,
                "options": options,
                "answer": answer,
                "hint": hint,
                "theme": "",
                "source": "deepseek",
            })
        return out
