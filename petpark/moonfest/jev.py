"""「月耀华诞」Jev（TypeSafe System One）判定封装。

Jev 不是聊天模型：它不生成文本，只返回带校准概率的结构化判定。本模块把
``tools/jev_client.py`` 的 ``ask``/``decide_*`` 包一层业务语义，供 engine.py 调用。

**关键约定**：
- 单条玩家消息最多触发 1 次 Jev 请求（一次并行问完所有问题）；
- **任何失败都必须兜底**：``load_api_key``/``ask`` 都可能 ``raise SystemExit``，
  所以每个公开方法都 try/except Exception（含 SystemExit）→ 返回 None / 保底档，
  调用方据此走确定性本地判定，活动永不因 Jev 瘫痪；
- 无 key / 未启用 → 纯本地模式（返回 None，调用方全走兜底）；
- 超时 30s；显式禁代理（本机 HTTP(S)_PROXY 会让 TLS 握手失败，照 jev_client.py:57）。
"""
from __future__ import annotations

import os
import urllib.request
from typing import Any, Callable

# Jev 客户端。导入失败（如路径不在 sys.path）时整模块降级为「不可用」，
# 不影响活动纯本地运行。
try:  # pragma: no cover - 导入路径依赖部署环境
    from tools.jev_client import (
        ask,
        decide_choice,
        decide_noul,
        decide_score,
        load_api_key,
        q_choice,
        q_noul,
        q_score,
    )
    _CLIENT_OK = True
except Exception:  # noqa: BLE001 - 任何导入失败都降级
    _CLIENT_OK = False

    def ask(*args: Any, **kwargs: Any):  # type: ignore[misc]
        raise RuntimeError("jev_client 不可用")

    def decide_noul(*args: Any, **kwargs: Any) -> bool:  # type: ignore[misc]
        return False

    def decide_choice(*args: Any, **kwargs: Any) -> None:
        return None

    def decide_score(*args: Any, **kwargs: Any) -> int:
        return 0

    load_api_key = lambda: ""  # noqa: E731

    q_noul = q_choice = q_score = lambda **kwargs: {}  # type: ignore[assignment]

# 判定阈值（照 jev_client.py 的默认值；业务上灯谜 noul 用 NOUL_YES=0.7）
NOUL_YES = 0.7
CHOICE_MIN_CONFIDENCE = 0.5


class _Jev:
    """Jev 判定封装。engine.py 以 ``self.jev`` 访问，所有方法失败即兜底。"""

    # 是否已从配置启用（engine 每次调用前会同步 cfg 里的开关）
    enabled = True

    # ------------------------------------------------------------------
    # 内部：一次请求并吞掉所有异常
    # ------------------------------------------------------------------
    @staticmethod
    def _state(**fields: Any) -> dict:
        """把业务字段组装成 Jev 的 state（结构化字段比长文本省 token）。"""
        return {k: v for k, v in fields.items() if v is not None}

    def _ask(self, state: dict, questions: dict) -> dict | None:
        """发一次请求 → answers dict；任何失败返回 None。"""
        if not _CLIENT_OK:
            return None
        try:
            resp = ask(state, questions)
        except SystemExit:
            return None
        except Exception:  # noqa: BLE001 - 网络/JSON/超时全部兜底
            return None
        if not isinstance(resp, dict):
            return None
        ans = resp.get("answers")
        return ans if isinstance(ans, dict) else None

    # ------------------------------------------------------------------
    # 公开判定方法
    # ------------------------------------------------------------------
    def ping(self) -> dict | None:
        """webadmin 测试用：问一个最简是非题，返回响应原文（含 key 是否可用）。"""
        if not _CLIENT_OK:
            return {"ok": False, "msg": "jev_client 不可导入，Jev 不可用"}
        try:
            key = load_api_key()
        except SystemExit:
            return {"ok": False, "msg": "缺少 TYPESAFE_API_KEY / .jev_key"}
        if not key:
            return {"ok": False, "msg": "TYPESAFE_API_KEY 为空"}
        ans = self._ask(
            {"text": "今天天气很好"},
            {"q": q_noul("这句话说的是好天气吗？")},
        )
        if ans is None:
            return {"ok": False, "msg": "请求失败或超时（30s）"}
        return {"ok": True, "answers": ans, "model": "jev-latest"}

    def noul_bool(
        self,
        state: dict,
        instructions: str,
        criteria: dict | None = None,
        threshold: float = NOUL_YES,
    ) -> bool | None:
        """是非题 → bool；失败/不确定… 不，失败返回 None，确定才返回 bool。

        返回 True 表示「是/成立」概率 ≥ threshold；False 表示明确否。
        None 表示 Jev 不可用或请求失败（调用方必须自行兜底）。
        """
        if not self.enabled:
            return None
        ans = self._ask(state, {"q": q_noul(instructions, criteria)})
        if ans is None or "q" not in ans:
            return None
        try:
            return decide_noul(ans["q"], threshold=threshold)
        except Exception:  # noqa: BLE001
            return None

    def choice_one(
        self,
        state: dict,
        instructions: str,
        criteria: dict,
        min_confidence: float = CHOICE_MIN_CONFIDENCE,
    ) -> str | None:
        """单选题 → 选中的选项名；置信度不够返回 None（「模型不知道」）。

        返回 None 可能是「不确定」也可能是「失败」，调用方按拒绝/兜底处理。
        """
        if not self.enabled:
            return None
        ans = self._ask(state, {"q": q_choice(instructions, criteria)})
        if ans is None or "q" not in ans:
            return None
        try:
            return decide_choice(ans["q"], min_confidence=min_confidence)
        except Exception:  # noqa: BLE001
            return None

    def score_tier(
        self,
        state: dict,
        instructions: str,
        criteria: list,
        cuts: list[float] | None = None,
    ) -> int | None:
        """打分题 → 档位下标（0 起）；失败返回 None（调用方按固定档兜底）。

        criteria 是**有序数组**，下标即分值。cuts 可选：不传就四舍五入。
        """
        if not self.enabled:
            return None
        ans = self._ask(state, {"q": q_score(instructions, criteria)})
        if ans is None or "q" not in ans:
            return None
        try:
            return decide_score(ans["q"], cuts=cuts)
        except Exception:  # noqa: BLE001
            return None


# 单例：engine.py `from .jev import JEV` 直接拿
JEV = _Jev()
