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

    async def reply_echo(self, prompt: str, system: str | None = None) -> str | None:
        """生成温情回文 / 文案，失败返回 None。"""
        try:
            return await self.chat(prompt, system=system)
        except Exception as e:  # noqa: BLE001
            logger.warning("[zhongyuan] DeepSeek 文案生成失败：%s", e)
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
        options = [str(o).strip() for o in obj.get("options", []) if str(o).strip()]
        answer = str(obj.get("answer", "")).strip()
        hint = str(obj.get("hint", "")).strip()
        if not question or not answer:
            return None
        if options and answer not in options:
            # answer 必须是某个选项原文，否则判分无法对齐
            answer = options[0]
        return {
            "question": question,
            "options": options,
            "answer": answer,
            "hint": hint,
            "theme": "",
            "source": "deepseek",
        }
