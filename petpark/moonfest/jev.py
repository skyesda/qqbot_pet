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

import importlib.util
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any, Callable

# Jev 客户端 tools/jev_client.py 在**插件根**下，而插件被框架以包名
# ``astrbot_plugin_petpark`` 加载时，sys.path 上是 ``plugins/``（插件根的**父**
# 目录），插件根自己并不在 sys.path 上 —— 所以 ``from tools.jev_client import …``
# 在线上必然 ModuleNotFoundError（本地手工探针因 cwd=插件根 才碰巧成功）。
# 现改为按**文件路径**加载，与 sys.path 无关；仍以 ``tools/jev_client.py`` 为
# 唯一真源（阈值校准脚本共用同一份，不复制副本）。
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
_CLIENT_PATH = _PLUGIN_ROOT / "tools" / "jev_client.py"

_LOAD_ERR = ""


def _load_client():
    """按路径加载 tools/jev_client.py 并注册进 sys.modules。

    注册是必需的：importlib 手工加载的模块不会自动入 sys.modules，而
    ``urllib.request`` 之类的惰性导入会依赖它。
    """
    if not _CLIENT_PATH.is_file():
        raise FileNotFoundError(f"未找到 {_CLIENT_PATH}")
    spec = importlib.util.spec_from_file_location("_moonfest_jev_client", _CLIENT_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法为 {_CLIENT_PATH} 构建加载器")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


try:
    _client = _load_client()
    ask = _client.ask
    decide_choice = _client.decide_choice
    decide_noul = _client.decide_noul
    decide_score = _client.decide_score
    load_api_key = _client.load_api_key
    q_choice = _client.q_choice
    q_noul = _client.q_noul
    q_score = _client.q_score
    _CLIENT_OK = True
except Exception as _e:  # noqa: BLE001 - 任何导入失败都降级为纯本地模式
    _CLIENT_OK = False
    _LOAD_ERR = f"{type(_e).__name__}: {_e}"

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

# ---------------------------------------------------------------------------
# API Key：后台配置 "jev.api_key" 的落点
# ---------------------------------------------------------------------------
# tools.jev_client.load_api_key() 的优先级是「环境变量 TYPESAFE_API_KEY →
# 同目录 .jev_key」，所以后台填的 key 写进环境变量即可生效，无需改动那个工具
# 的签名。``_OWN_ENV_KEY`` 用来区分「key 是本模块写进去的」还是「框架环境本来
# 就有的」——只回收自己写的那份，绝不误删框架设的 TYPESAFE_API_KEY。
_OWN_ENV_KEY = False


def set_api_key(key: str | None) -> None:
    """用后台配置的 Jev API Key 覆盖环境变量；留空则退回环境变量 / .jev_key。"""
    global _OWN_ENV_KEY
    k = (key or "").strip()
    if k:
        os.environ["TYPESAFE_API_KEY"] = k
        _OWN_ENV_KEY = True
    elif _OWN_ENV_KEY:
        os.environ.pop("TYPESAFE_API_KEY", None)
        _OWN_ENV_KEY = False

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
    def available(self) -> bool:
        """Jev 现在**真的能发请求**吗（已启用 + 客户端导入成功 + 拿得到 Key）。

        与「调用返回 None」的区别很重要：调用返回 None 是「结果未知」，而这里
        回答的是「能不能指望 Jev」。月饼重制挑战用它决定**要不要出语义题** ——
        线上当前没配 Jev Key，必须据此换成确定性题，而不是出个语义题再自动放行。

        ``load_api_key()`` 在缺 Key 时会 ``raise SystemExit``（jev_client 的约定），
        所以必须连 SystemExit 一起吞。
        """
        if not self.enabled or not _CLIENT_OK:
            return False
        try:
            return bool(load_api_key())
        except SystemExit:
            return False
        except Exception:  # noqa: BLE001
            return False

    def ping(self) -> dict | None:
        """webadmin 测试用：问一个最简是非题，返回响应原文（含 key 是否可用）。

        失败时**必须带上真实原因**（导入报错原文 / 找 key 的位置），否则线上只
        看到一句「不可用」，无从排查。
        """
        if not _CLIENT_OK:
            return {"ok": False,
                    "msg": f"jev_client 不可导入，Jev 不可用（{_CLIENT_PATH} → {_LOAD_ERR}）"}
        try:
            key = load_api_key()
        except SystemExit:
            return {"ok": False,
                    "msg": "缺少 Jev API Key：请在后台本卡片「Jev API Key」填写，"
                           f"或设环境变量 TYPESAFE_API_KEY / 建文件 {_CLIENT_PATH.with_name('.jev_key')}"}
        if not key:
            return {"ok": False, "msg": "Jev API Key 为空"}
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
