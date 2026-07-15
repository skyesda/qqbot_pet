"""AI 意图路由：把自然语言消息翻译为宠物乐园的标准指令。

三层路由中的第 2/3 层（第 1 层为 main.py 中的精确指令匹配）：

- 第 2 层（本地，零延迟）：同义词表 + 正则规则，覆盖常见的自然语言变体，
  如「帮我升级」→「宠物升级」、「转让给123456 还魂丹 2个」→「转让 123456 还魂丹 2」。
- 第 3 层（LLM 兜底）：本地规则未命中且消息疑似宠物相关时，调用 AstrBot
  当前启用的 LLM Provider 做意图解析，输出标准指令行后交回 dispatch 执行。

设计原则：AI 只负责「翻译成指令」，不生成回复内容，保证结果可控、token 少、
响应快；解析结果带缓存，同样的说法第二次零延迟。
"""

from __future__ import annotations

import asyncio
import json
import re
import time

from astrbot.api import logger

# LLM 调用默认超时（秒），可在插件配置面板调整；超时视为未识别，不阻塞用户
LLM_TIMEOUT = 20.0
# 触发 AI 兜底的消息最大长度（过长多半是闲聊）
MAX_TEXT_LEN = 50
# 缓存上限
CACHE_MAX = 512

# 高危指令：后果不可逆，禁止由 AI 路由自动执行，必须玩家手动发送完整指令
DANGEROUS_COMMANDS = {
    "放生宠物",
    "赠送宠物",
    "清空背包",
    "丢弃",
    "宠物变性",
}

# ---------------------------------------------------------------------------
# 第 2 层：本地同义词 / 正则规则
# ---------------------------------------------------------------------------

# 去掉常见的礼貌/口语前后缀，便于匹配
_PREFIX_RE = re.compile(
    r"^(请|麻烦|帮我|帮忙|给我|我要|我想|我想要|来|来个|快点|速度)+"
)
_SUFFIX_RE = re.compile(r"(吧|呀|啊|呢|哦|喔|了|一下|一下下|下)+$")

# 无参数指令的同义词表：说法 → 标准指令
SYNONYMS: dict[str, str] = {
    "升级": "宠物升级",
    "升一级": "宠物升级",
    "升个级": "宠物升级",
    "宠物升一级": "宠物升级",
    "一键升级": "一键升级宠物",
    "全部升级": "一键升级宠物",
    "升到顶": "一键升级宠物",
    "进化": "宠物进化",
    "飞升": "宠物飞升",
    "渡劫": "宠物渡劫",
    "觉醒": "宠物觉醒",
    "菜单": "宠物菜单",
    "帮助": "宠物帮助",
    "指令": "宠物指令",
    "签个到": "签到",
    "打卡": "签到",
    "砸个蛋": "砸蛋",
    "抽宠物": "砸蛋",
    "抽个宠物": "砸蛋",
    "排行榜": "宠物排行",
    "排行": "宠物排行",
    "神榜": "宠物神榜",
    "背包": "查看背包",
    "我的背包": "查看背包",
    "看背包": "查看背包",
    "商城": "宠物商城",
    "商店": "宠物商城",
    "副本": "宠物副本",
    "副本列表": "宠物副本",
    "我的状态": "宠物状态",
    "看看我的宠物": "我的宠物",
    "看我的宠物": "我的宠物",
    "查看我的宠物": "我的宠物",
    "信息": "我的信息",
    "改名": "宠物改名",
    "复活宠物": "宠物复活",
    "炼个丹": "炼丹",
    "开始修炼": "自动修炼",
    "停止修炼": "关闭自动修炼",
}

# 提取用户 ID（纯数字 QQ 或平台 openid 字符串）
_ID = r"([A-Za-z0-9]{5,})"
_NUM = r"(\d{1,4})"

# 正则规则：匹配 → 生成标准指令行。按顺序尝试，第一个命中的生效。
_REGEX_RULES: list[tuple[re.Pattern, callable]] = [
    # 升级 N 次 / 升 N 级
    (
        re.compile(rf"^(?:宠物)?升(?:级)?\s*{_NUM}\s*[次级]$"),
        lambda m: f"宠物升级 {m.group(1)}",
    ),
    # 转让给 xxx 物品 [数量]
    (
        re.compile(rf"^转(?:让|赠|给)\s*给?\s*{_ID}\s+(\S+?)\s*(\d{{1,3}})?\s*[个只件颗枚]?$"),
        lambda m: f"转让 {m.group(1)} {m.group(2)} {m.group(3) or 1}",
    ),
    # 赠送宠物给 xxx / 把宠物送给 xxx
    (
        re.compile(rf"^(?:把)?(?:我的)?宠物?[赠送转]+给?\s*{_ID}$"),
        lambda m: f"赠送宠物 {m.group(1)}",
    ),
    (
        re.compile(rf"^赠送宠物给\s*{_ID}$"),
        lambda m: f"赠送宠物 {m.group(1)}",
    ),
    # 攻击/挑战 xxx
    (
        re.compile(rf"^(?:宠物)?(?:攻击|挑战|打)\s*{_ID}$"),
        lambda m: f"宠物攻击 {m.group(1)}",
    ),
    # 查看 xxx 的宠物
    (
        re.compile(rf"^(?:查看|看看?)\s*{_ID}\s*的宠物$"),
        lambda m: f"查看宠物 {m.group(1)}",
    ),
    # 给宠物改名为 xxx
    (
        re.compile(r"^(?:给)?(?:宠物|它)?改名[为叫成]?\s*(\S{1,12})$"),
        lambda m: f"宠物改名 {m.group(1)}",
    ),
    # 买 N 个 xxx / 购买 xxx N 个
    (
        re.compile(rf"^(?:购)?买\s*{_NUM}\s*[个只件颗枚]\s*(\S+)$"),
        lambda m: f"购买 {m.group(2)} {m.group(1)}",
    ),
    (
        re.compile(rf"^(?:购)?买\s*(\S+?)\s*{_NUM}\s*[个只件颗枚]$"),
        lambda m: f"购买 {m.group(1)} {m.group(2)}",
    ),
    # 用 N 个 xxx / 使用 xxx N 个
    (
        re.compile(rf"^(?:使)?用\s*{_NUM}\s*[个只件颗枚]\s*(\S+)$"),
        lambda m: f"使用 {m.group(2)} {m.group(1)}",
    ),
    (
        re.compile(rf"^(?:使)?用\s*(\S+?)\s*{_NUM}\s*[个只件颗枚]$"),
        lambda m: f"使用 {m.group(1)} {m.group(2)}",
    ),
]

# 疑似宠物相关的关键词（用于决定是否触发 LLM 兜底，避免闲聊浪费调用）
_PET_KEYWORDS = (
    "宠物", "升级", "进化", "飞升", "渡劫", "觉醒", "砸蛋", "签到", "背包",
    "转让", "赠送", "喂食", "炼丹", "仙丹", "神器", "秘技", "天赋", "副本",
    "摸金", "深渊", "排行", "神榜", "攻击", "挑战", "改名", "放生", "复活",
    "商城", "商店", "兑换", "修炼", "金币", "积分", "钻石",
)

# 带参数指令的用法说明（写进 LLM 提示词，保证生成的指令行参数格式正确）
_USAGE = """转让 用户ID 物品名 数量
赠送金币 用户ID 数量
赠送积分 用户ID 数量
赠送钻石 用户ID 数量
宠物升级 [次数]
宠物攻击 用户ID
跨群挑战宠物 用户ID
查看宠物 [用户ID]
宠物侦查 用户ID
宠物改名 新昵称
购买 物品名 [数量]
使用 物品名 数量
出售 物品名 数量
丢弃 物品名 数量
喂食 物品名
购买宠物 宠物名 [品质]
合成卡 目标卡名
打造神器 神器名称
佩戴神器 神器名称
参悟秘技 秘技名称
使用天赋符 名称
使用仙丹 仙丹名 用户ID [数量]
治愈 用户ID
复活 用户ID
精力转移 用户ID 精力值
进入副本 副本名称
挑战神仙 等级
深渊购买 商品名
深渊祝福 祝福名
提交任务 任务名称
领取任务 任务名称
受邀 邀请人用户ID
兑换 卡密
查看说明 物品名称"""

_SYSTEM_PROMPT = (
    "你是QQ群宠物养成游戏『宠物乐园』的指令解析器。"
    "把玩家的自然语言消息翻译为一条标准指令行。"
    "只输出JSON，格式：{\"cmd\": \"指令行\"}；无法确定对应指令时输出 {\"cmd\": null}。"
    "不要输出任何解释。指令行的第一个词必须来自指令列表。"
)

_JSON_RE = re.compile(r"\{[^{}]*\}")


class AIRouter:
    """自然语言 → 标准指令 的路由器。"""

    def __init__(
        self,
        context,
        enabled: bool = True,
        timeout: float = LLM_TIMEOUT,
        provider_id: str = "",
    ):
        self._context = context
        self.enabled = enabled
        self.timeout = max(1.0, float(timeout))
        self.provider_id = (provider_id or "").strip()
        # 缓存：规范化文本 → 指令行（或 "" 表示已确认无法识别）
        self._cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    @staticmethod
    def _normalize(text: str) -> str:
        t = text.strip()
        t = re.sub(r"[，。！？!?,.、~～]", " ", t)
        t = _PREFIX_RE.sub("", t.strip())
        t = _SUFFIX_RE.sub("", t.strip())
        return t.strip()

    def match_local(self, text: str) -> str | None:
        """第 2 层：本地同义词 / 正则匹配。命中返回标准指令行。"""
        t = self._normalize(text)
        if not t:
            return None
        if t in SYNONYMS:
            return SYNONYMS[t]
        for pattern, build in _REGEX_RULES:
            m = pattern.match(t)
            if m:
                try:
                    return build(m)
                except Exception:
                    continue
        return None

    @staticmethod
    def looks_pet_related(text: str) -> bool:
        return any(k in text for k in _PET_KEYWORDS)

    # ------------------------------------------------------------------
    async def route(self, text: str, known_commands: set[str]) -> str | None:
        """完整路由：本地规则 → 缓存 → LLM 兜底。返回标准指令行或 None。"""
        if not self.enabled:
            return None
        local = self.match_local(text)
        if local and local.split()[0] in DANGEROUS_COMMANDS:
            return None
        if local and local.split()[0] in known_commands:
            return local
        # LLM 兜底触发条件：长度合理且疑似宠物相关
        if len(text) > MAX_TEXT_LEN or not self.looks_pet_related(text):
            return None
        norm = self._normalize(text)
        if norm in self._cache:
            return self._cache[norm] or None
        result = await self._llm_route(text, known_commands)
        if len(self._cache) >= CACHE_MAX:
            self._cache.clear()
        self._cache[norm] = result or ""
        return result

    def _get_provider(self):
        if self.provider_id:
            try:
                p = self._context.get_provider_by_id(self.provider_id)
                if p is not None:
                    return p
            except Exception:
                pass
        try:
            return self._context.get_using_provider()
        except Exception:
            return None

    async def _llm_route(self, text: str, known_commands: set[str]) -> str | None:
        provider = self._get_provider()
        if provider is None:
            return None
        cmd_list = "、".join(sorted(known_commands))
        prompt = (
            f"指令列表：{cmd_list}\n\n"
            f"带参数指令的用法（其余指令无参数）：\n{_USAGE}\n\n"
            f"玩家消息：{text}\n"
            "输出JSON："
        )
        start = time.monotonic()
        try:
            completion = await asyncio.wait_for(
                self._call_llm(provider, prompt), timeout=self.timeout
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"[petpark] AI 意图解析超时（{self.timeout:g}s，可通过配置 ai_router_timeout 调整）"
            )
            return None
        except Exception as e:
            logger.warning(f"[petpark] AI 意图解析失败：{e}")
            return None
        elapsed = time.monotonic() - start
        logger.info(f"[petpark] AI 意图解析耗时 {elapsed:.2f}s：{completion[:80]!r}")
        return self._parse(completion, known_commands)

    async def _call_llm(self, provider, prompt: str) -> str:
        """调用 LLM。优先直接用 OpenAI 兼容 client（可关闭思考模式、限制输出长度，
        大幅降低延迟），不可用时回退到 AstrBot 的 text_chat。"""
        client = getattr(provider, "client", None)
        model = None
        try:
            model = provider.get_model()
        except Exception:
            model = getattr(provider, "model_name", None)
        if client is not None and model and hasattr(client, "chat"):
            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
            # 意图解析只需几十个 token；思考模型（如 qwen-plus）默认会先输出
            # 大段推理内容导致严重超时，这里显式关闭思考并限制输出长度。
            for extra in (
                {"enable_thinking": False, "thinking": {"type": "disabled"}},
                {"enable_thinking": False},
                None,
            ):
                try:
                    kwargs = dict(
                        model=model,
                        messages=messages,
                        temperature=0,
                        max_tokens=100,
                    )
                    if extra:
                        kwargs["extra_body"] = extra
                    resp = await client.chat.completions.create(**kwargs)
                    return resp.choices[0].message.content or ""
                except Exception as e:
                    if extra is None:
                        logger.warning(f"[petpark] AI 直连调用失败，回退 text_chat：{e}")
        resp = await provider.text_chat(
            prompt=prompt,
            session_id=None,
            contexts=[],
            system_prompt=_SYSTEM_PROMPT,
        )
        return getattr(resp, "completion_text", "") or ""

    @staticmethod
    def _parse(completion: str, known_commands: set[str]) -> str | None:
        m = _JSON_RE.search(completion)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return None
        cmd = obj.get("cmd")
        if not cmd or not isinstance(cmd, str):
            return None
        cmd = cmd.strip()
        if not cmd or cmd.split()[0] not in known_commands:
            return None
        if cmd.split()[0] in DANGEROUS_COMMANDS:
            return None
        return cmd
