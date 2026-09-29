"""「月耀华诞」中秋 × 国庆 双阶段活动 —— 主引擎。

结构照 petpark/zhongyuan/engine.py（独立活动模块，独立 moonfest.json），
关键差异：
- **不接 DeepSeek**：所有判定走本地 Jev 封装（.jev.JEV），失败即确定性本地兜底；
- **双阶段时间**：phase_midautumn / phase_national 各自 enabled/start_at/end_at，
  重叠日（默认 10-01）两阶段同时开放，`_phase()` 返回 midautumn/national/both/None；
- **月华只进不出**：`_add_yuehua` 只增不减，唯一排行键 `yuehua_earned`；
  全活动无兑换商店、无花销出口；
- **活动结束一次性结算**：两阶段 end_at 均过后 `_settle()` 发全服总榜前 20 名
  纯月华（写回 players 桶），`meta.settled` 幂等，不调 store.add_item。

指令（COMMANDS）：全部为玩家玩法指令，**无群内管理员指令** ——
  拜月 / 华诞签到 / 猜灯谜 / 喂玉兔 / 做月饼 / 贺词 / 点赞 / 巡礼 /
  月华榜 / 里程碑 / 月华墙 / 活动帮助
  活动的开关、双阶段起止时间、全部数值与奖励只在后台「节日活动」页配置；
  结算由后台循环在两阶段 end_at 均已过后自动执行一次，不经指令。
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from astrbot.api import logger

from . import puzzles
from . import templates as T
from .config import (
    ACTIVITY_KEY,
    ACTIVITY_NAME,
    ACTIVITY_TAG,
    DEFAULT_CONFIG,
    merge_config,
    tier_yuehua_for_rank,
)
from .jev import (
    CHOICE_MIN_CONFIDENCE,
    NOUL_YES,
    JEV,
    decide_choice,
    decide_noul,
    q_choice,
    q_noul,
    set_api_key,
)

BJ = ZoneInfo("Asia/Shanghai")

COMMANDS = {
    "拜月", "华诞签到", "猜灯谜", "喂玉兔", "做月饼",
    "贺词", "点赞", "巡礼", "月华榜", "里程碑", "月华墙", "活动帮助",
}
# 刻意**不提供**任何群内管理员指令：活动的开始/结束/时间/数值/奖励全部只在
# 后台「节日活动」页配置（改 phase_*.start_at/end_at 即开始/结束），结算由
# 后台循环在「两阶段 end_at 均已过」时自动执行一次。群内只留玩家玩法指令。

# 做月饼可选口味（收集向，每种首次合成发一次 craft_bonus）
CRAFT_FLAVORS = ["五仁", "豆沙", "蛋黄莲蓉", "冰皮", "流心"]

# 贺词本地敏感词兜底表（Jev 不可用/不确定时用；保守——只有命中明确词才拒，
# 其余放行）。词条刻意避开「天安门」等正常祝福里可能出现的中性词。
_SENSITIVE_WORDS = [
    "法轮功", "天安门事件", "六四", "台独", "藏独", "疆独", "港独", "占中", "邪教",
    "傻逼", "草泥马", "操你妈", "cnm", "fuck", "shit",
    "加微信", "加v信", "兼职", "刷单", "代练", "外挂", "私服",
    "赌博", "博彩", "色情", "约炮", "贷款",
]

# 各玩法所属阶段（用于阶段门控）
_PHASE_GATES = {
    "拜月": "midautumn",
    "猜灯谜": "midautumn",
    "喂玉兔": "midautumn",
    "做月饼": "midautumn",
    "华诞签到": "national",
    "贺词": "national",
    "点赞": "national",
    "巡礼": "national",
}


class MoonfestActivity:
    """月耀华诞活动。生命周期：main.py 构造 → start() 起后台循环 → terminate() 收尾。"""

    def __init__(self, bot, data_dir: Path, config: dict | None = None):
        self.bot = bot
        self.data_path = Path(data_dir) / f"{ACTIVITY_KEY}.json"
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._data: dict[str, Any] = {}
        self._load()
        # 配置优先级：DEFAULT_CONFIG < moonfest.json#config < 构造参数
        self.cfg = merge_config(DEFAULT_CONFIG, self._data.get("config") or {})
        if config:
            self.cfg = merge_config(self.cfg, config)
        self._loop_task_ref = None
        # Jev 单例（.jev.JEV）；开关与 API Key 均跟随配置
        self.jev = JEV
        self._sync_jev()

    def _sync_jev(self) -> None:
        """把配置里的 Jev 开关与 API Key 同步到 Jev 单例。

        ``jev.api_key`` 是后台卡片上那个 Key 输入框的唯一落点：此前只读了
        ``enabled``、Key 从没被用过，后台填了也不生效（线上只能靠环境变量或
        插件根 tools/.jev_key，而后者是 gitignore 的、服务器上根本没有）。
        """
        jev = self.cfg.get("jev") or {}
        self.jev.enabled = bool(jev.get("enabled", True))
        set_api_key(jev.get("api_key"))

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def _load(self) -> None:
        try:
            raw = json.loads(self.data_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data = raw
        except (json.JSONDecodeError, OSError):
            self._data = {}
        self._data.setdefault("config", {})
        self._data.setdefault("meta", {})
        self._data.setdefault("players", {})
        self._data.setdefault("groups", {})
        self._data.setdefault("wall", [])

    def _flush(self) -> None:
        try:
            tmp = self.data_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.data_path)
        except OSError as e:
            logger.warning("[moonfest] 落盘失败：%s", e)

    async def save(self) -> None:
        async with self._lock:
            self._flush()

    # ------------------------------------------------------------------
    # 时间工具
    # ------------------------------------------------------------------
    @staticmethod
    def _now() -> int:
        return int(time.time())

    @staticmethod
    def _now_bj() -> datetime:
        return datetime.now(BJ)

    def _bj_date(self) -> str:
        return self._now_bj().strftime("%Y-%m-%d")

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------
    def _cfg(self, key, default=None):
        return self.cfg.get(key, default)

    def _int_cfg(self, key, default=0) -> int:
        try:
            return int(self.cfg.get(key, default))
        except (TypeError, ValueError):
            return default

    def _rand_int(self, min_key, max_key, dmin, dmax) -> int:
        """在 [min_key, max_key] 配置区间内取随机整数（照中元 _rand_gongde）。"""
        lo, hi = self._int_cfg(min_key, dmin), self._int_cfg(max_key, dmax)
        if lo > hi:
            lo, hi = hi, lo
        if lo == hi:
            return lo
        return random.randint(lo, hi)

    def _coerce_config(self, cur, raw):
        """把后台/指令传入的原始值转成与当前配置同类型；无法转换返回 None。"""
        if isinstance(cur, bool):
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in ("true", "1", "yes", "on", "开启", "开")
        if isinstance(cur, int):
            try:
                return int(raw)
            except (TypeError, ValueError):
                return None
        if isinstance(cur, float):
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None
        if isinstance(cur, (list, dict)):
            if isinstance(raw, (list, dict)):
                return raw
            if isinstance(raw, str):
                try:
                    v = json.loads(raw)
                    return v if isinstance(v, (list, dict)) else None
                except (json.JSONDecodeError, TypeError):
                    return None
            return None
        return str(raw)

    def apply_config(self, updates: dict):
        """批量应用配置（webadmin 用）。返回 (ok, bad) 键名列表。"""
        ok, bad = [], []
        for key, raw in (updates or {}).items():
            if key not in self.cfg:
                bad.append(key)
                continue
            new = self._coerce_config(self.cfg[key], raw)
            if new is None:
                bad.append(key)
                continue
            cur = self.cfg.get(key)
            # 嵌套 dict（phase_* / daily / jev）做一层合并，允许只提交部分键，
            # 避免「只改 start_at」把同一 dict 里的 end_at 一并抹掉。
            if isinstance(cur, dict) and isinstance(new, dict):
                new = {**cur, **new}
            self.cfg[key] = new
            self._data.setdefault("config", {})[key] = new
            ok.append(key)
        # jev 开关 / API Key 变更后同步到 Jev 单例
        if "jev" in self.cfg:
            self._sync_jev()
        return ok, bad

    # ------------------------------------------------------------------
    # 活动状态 / 阶段
    # ------------------------------------------------------------------
    def _in_open_hours(self) -> bool:
        d = self.cfg.get("daily") or {}
        open_h = int(d.get("open_hour", 0))
        close_h = int(d.get("close_hour", 24))
        if open_h >= close_h:  # 跨天或全开
            return True
        return open_h <= self._now_bj().hour < close_h

    def _phase_enabled_now(self, key) -> bool:
        p = self.cfg.get(key) or {}
        if not p.get("enabled", True):
            return False
        now = self._now()
        start = int(p.get("start_at", 0) or 0)
        end = int(p.get("end_at", 0) or 0)
        if start and now < start:
            return False
        if end and now > end:
            return False
        return True

    def _phase(self) -> str | None:
        """当前阶段：midautumn / national / both / None。"""
        cur = []
        if self._phase_enabled_now("phase_midautumn"):
            cur.append("midautumn")
        if self._phase_enabled_now("phase_national"):
            cur.append("national")
        if not cur:
            return None
        if len(cur) == 2:
            return "both"
        return cur[0]

    def _enabled(self) -> bool:
        return bool(self.cfg.get("enabled", True)) and self._in_open_hours() and self._phase() is not None

    def _activity_over(self) -> bool:
        """两阶段 end_at 均过 → 整个活动结束（触发唯一一次结算）。"""
        for key in ("phase_midautumn", "phase_national"):
            p = self.cfg.get(key) or {}
            end = int(p.get("end_at", 0) or 0)
            if not end or self._now() <= end:
                return False
        return True

    def _meta(self) -> dict:
        return self._data.setdefault("meta", {})

    def _is_settled(self) -> bool:
        """整个活动是否已结算（幂等保护，只可能为 True 一次）。"""
        return bool(self._meta().get("settled"))

    def _mark_settled(self) -> None:
        self._meta()["settled"] = True
        self._meta()["settled_at"] = self._now()

    # ------------------------------------------------------------------
    # 数据访问
    # ------------------------------------------------------------------
    def _players(self) -> dict:
        return self._data.setdefault("players", {})

    def _groups(self) -> dict:
        return self._data.setdefault("groups", {})

    @staticmethod
    def _key(group_id, qq) -> str:
        return str(qq)  # 全服跨群，按 QQ 全局唯一

    def _group_state(self, group_id) -> dict:
        gid = str(group_id)
        gs = self._groups().setdefault(gid, {"yuehua_total": 0, "milestone_reached": []})
        gs.setdefault("yuehua_total", 0)
        gs.setdefault("milestone_reached", [])
        return gs

    def _players_in_group(self, group_id) -> list[dict]:
        gid = str(group_id)
        return [p for p in self._players().values() if str(p.get("group", "")) == gid]

    def _get_player(self, group_id, qq, create: bool = True, event=None) -> dict | None:
        key = self._key(group_id, qq)
        players = self._players()
        ap = players.get(key)
        if ap is None:
            if not create:
                return None
            ap = {
                "qq": str(qq),
                "group": str(group_id),
                "name": "",
                "yuehua_earned": 0,
                "sign": {"mid": "", "nat": "", "count": 0},
                "daily": {"date": "", "lantern": 0, "feed": 0, "bake": 0, "firework": 0, "quiz": 0, "like": 0},
                "quiz": {},
                "quiz_streak": 0,
                "last_lantern_ts": 0,
                "last_feed_ts": 0,
                "last_bake_ts": 0,
                "fireworks": [],
                "craft": {"mooncake": {}},
                "bound_at": self._now(),
            }
            players[key] = ap
        else:
            if str(ap.get("group", "")) != str(group_id):
                ap["group"] = str(group_id)
            ap.setdefault("name", "")
            ap.setdefault("yuehua_earned", 0)
            ap.setdefault("sign", {"mid": "", "nat": "", "count": 0})
            ap.setdefault("daily", {"date": "", "lantern": 0, "feed": 0, "bake": 0, "firework": 0, "quiz": 0, "like": 0})
            ap.setdefault("quiz", {})
            ap.setdefault("quiz_streak", 0)
            ap.setdefault("craft", {"mooncake": {}})
            ap.setdefault("fireworks", [])
        # 有 event 时顺手刷新昵称
        if event is not None:
            name = self._user_name(event, qq)
            if name and ap.get("name") != name:
                ap["name"] = name
        return ap

    def _user_name(self, event, qq) -> str:
        try:
            return str(self.bot._sender_name(event) or str(qq))
        except Exception:
            return str(qq)

    # ------------------------------------------------------------------
    # 月华结算（只增不减）
    # ------------------------------------------------------------------
    @staticmethod
    def _add_yuehua(ap, amount) -> None:
        amt = max(0, int(amount))
        if not amt:
            return
        ap["yuehua_earned"] = int(ap.get("yuehua_earned", 0)) + amt

    def _group_add_yuehua(self, group_id, amount) -> None:
        amt = max(0, int(amount))
        if not amt:
            return
        gs = self._group_state(group_id)
        gs["yuehua_total"] = int(gs.get("yuehua_total", 0)) + amt
        self._check_milestones(group_id)

    def _grant_yuehua(self, ap, group_id, amount) -> None:
        """发月华给玩家 + 计入群累计并查里程碑。"""
        self._add_yuehua(ap, amount)
        self._group_add_yuehua(group_id, amount)

    def _check_milestones(self, group_id) -> list[int]:
        ms = self.cfg.get("milestones") or []
        gs = self._group_state(group_id)
        reached = set(int(i) for i in (gs.get("milestone_reached") or []))
        total = int(gs.get("yuehua_total", 0))
        newly = []
        for i, m in enumerate(ms):
            if i in reached:
                continue
            if total >= int(m.get("threshold", 0)):
                reached.add(i)
                newly.append(m)
        if newly:
            gs["milestone_reached"] = sorted(reached)
            for m in newly:
                amt = int(m.get("gongde", 0))
                for ap in self._players_in_group(group_id):
                    self._add_yuehua(ap, amt)
                self._spawn(self._push_group(
                    group_id,
                    T.MILESTONE_REACHED.format(threshold=m.get("threshold"), amount=amt),
                ))
        return [int(m.get("threshold", 0)) for m in newly]

    def _daily_reset(self, ap) -> None:
        d = ap.setdefault("daily", {})
        today = self._bj_date()
        if d.get("date") != today:
            d.update({"date": today, "lantern": 0, "feed": 0, "bake": 0, "firework": 0, "quiz": 0, "like": 0})

    # ------------------------------------------------------------------
    # 管理权限
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # 玩法：拜月 / 华诞签到
    # ------------------------------------------------------------------
    def _cmd_sign_mid(self, event, qq, group_id) -> str:
        ap = self._get_player(group_id, qq, event=event)
        sign = ap["sign"]
        today = self._bj_date()
        if sign.get("mid") == today:
            return T.BAIYUE_REPEAT
        amt = self._int_cfg("sign_midautumn", 10)
        self._grant_yuehua(ap, group_id, amt)
        sign["mid"] = today
        sign["count"] = int(sign.get("count", 0)) + 1
        return T.rand(T.BAIYUE_LINES).format(amount=amt)

    def _cmd_sign_nat(self, event, qq, group_id) -> str:
        ap = self._get_player(group_id, qq, event=event)
        sign = ap["sign"]
        today = self._bj_date()
        if sign.get("nat") == today:
            return T.HUADAN_REPEAT
        amt = self._int_cfg("sign_national", 15)
        self._grant_yuehua(ap, group_id, amt)
        sign["nat"] = today
        sign["count"] = int(sign.get("count", 0)) + 1
        return T.rand(T.HUADAN_LINES).format(amount=amt)

    # ------------------------------------------------------------------
    # 玩法：猜灯谜（Jev noul 语义判定 + 字符串兜底）
    # ------------------------------------------------------------------
    def _cmd_lantern(self, event, qq, group_id, rest: str) -> str:
        ap = self._get_player(group_id, qq, event=event)
        self._daily_reset(ap)
        d = ap["daily"]
        limit = self._int_cfg("lantern_daily_limit", 20)
        timeout = self._int_cfg("lantern_timeout_sec", 60)
        quiz = ap.get("quiz") or {}
        if not rest:
            if int(d.get("lantern", 0)) >= limit:
                return T.LANTERN_DAILY_LIMIT.format(limit=limit)
            # 已有进行中的题（同一天）→ 复用，不重复出题
            if quiz.get("kind") == "lantern" and quiz.get("date") == self._bj_date():
                return T.LANTERN_ASK.format(question=quiz.get("q"), hint=quiz.get("hint", ""), timeout=timeout)
            # 答对后的冷却（lantern_cooldown_min，默认 0=无冷却）
            cooldown = self._int_cfg("lantern_cooldown_min", 0) * 60
            last = int(ap.get("last_lantern_ts", 0) or 0)
            now = self._now()
            if cooldown and last and now - last < cooldown:
                mins = max(1, int((cooldown - (now - last)) // 60))
                return T.LANTERN_COOLDOWN.format(mins=mins)
            p = puzzles.local_lantern()
            ap["quiz"] = {
                "kind": "lantern", "q": p.get("question"), "a": str(p.get("answer", "")),
                "answer_type": p.get("answer_type"), "hint": p.get("hint", ""),
                "date": self._bj_date(), "ts": now,
            }
            d["lantern"] = int(d.get("lantern", 0)) + 1
            return T.LANTERN_ASK.format(question=p.get("question"), hint=p.get("hint", ""), timeout=timeout)
        # 作答
        if quiz.get("kind") != "lantern" or quiz.get("date") != self._bj_date():
            return T.LANTERN_ANSWER_FORMAT
        if self._now() - int(quiz.get("ts", 0)) > timeout:
            ap.pop("quiz", None)
            return T.LANTERN_TIMEOUT.format(answer=quiz.get("a"))
        user_ans = rest.strip()
        answer = str(quiz.get("a", ""))
        # 字符串归一化兜底：字面命中无条件通过
        norm = puzzles.normalize_answer(user_ans, {"answer": answer, "answer_type": quiz.get("answer_type")})
        if norm is not None and norm == answer:
            ap.pop("quiz", None)
            return self._lantern_right(ap, group_id, user_ans, answer, by_jev=False)
        # Jev noul 语义等价判定（谐音/别解/近义）
        verdict = JEV.noul_bool(
            {"谜面": quiz.get("q"), "标准答案": answer, "玩家回答": user_ans},
            "玩家的回答是否语义等价于这道灯谜的谜底？",
            {"true": "语义等价，包含谐音、别解、近义、口语化表达", "false": "与谜底完全无关或明显错误"},
        )
        ap.pop("quiz", None)
        if verdict is True:
            return self._lantern_right(ap, group_id, user_ans, answer, by_jev=True)
        return T.LANTERN_WRONG.format(answer=answer)

    def _lantern_right(self, ap, group_id, user_ans, answer, by_jev=False) -> str:
        amt = self._rand_int("gongde_lantern_min", "gongde_lantern_max", 10, 30)
        self._grant_yuehua(ap, group_id, amt)
        ap["last_lantern_ts"] = self._now()
        if by_jev:
            return T.LANTERN_RIGHT_JEV.format(user=user_ans, answer=answer, amount=amt)
        return T.LANTERN_RIGHT.format(answer=answer, amount=amt)

    # ------------------------------------------------------------------
    # 玩法：喂玉兔（Jev score 4 档）
    # ------------------------------------------------------------------
    def _cmd_feed(self, event, qq, group_id, rest: str) -> str:
        ap = self._get_player(group_id, qq, event=event)
        self._daily_reset(ap)
        d = ap["daily"]
        limit = self._int_cfg("feed_daily_limit", 10)
        if int(d.get("feed", 0)) >= limit:
            return T.FEED_DONE.format(limit=limit)
        thing = rest.strip()
        if not thing:
            return T.FEED_NEED_THING.format(names="、".join(T.FEED_THINGS))
        cooldown = self._int_cfg("feed_cooldown_min", 10) * 60
        last = int(ap.get("last_feed_ts", 0) or 0)
        now = self._now()
        if cooldown and last and now - last < cooldown:
            mins = max(1, int((cooldown - (now - last)) // 60))
            return T.FEED_COOLDOWN.format(mins=mins)
        tier = JEV.score_tier(
            {"投喂物品": thing, "今日已喂次数": int(d.get("feed", 0))},
            "玉兔对这份食物的喜爱程度？",
            ["厌恶", "无感", "喜欢", "非常喜欢"],
        )
        if tier is None:
            tier = 1  # 兜底：无感档
        tier = max(0, min(int(tier), 3))
        gongde = self.cfg.get("gongde_feed") or [5, 10, 20, 30]
        amt = int(gongde[tier]) if 0 <= tier < len(gongde) else 10
        self._grant_yuehua(ap, group_id, amt)
        d["feed"] = int(d.get("feed", 0)) + 1
        ap["last_feed_ts"] = now
        return T.FEED_RESPONSES[tier].format(thing=thing[:12], amount=amt)

    # ------------------------------------------------------------------
    # 玩法：做月饼（收集向，首次解锁发一次）
    # ------------------------------------------------------------------
    def _cmd_craft(self, event, qq, group_id, rest: str) -> str:
        flavor = rest.strip()
        if not flavor:
            return T.CRAFT_UNKNOWN.format(name="？", names="、".join(CRAFT_FLAVORS))
        if flavor not in CRAFT_FLAVORS:
            return T.CRAFT_UNKNOWN.format(name=flavor, names="、".join(CRAFT_FLAVORS))
        ap = self._get_player(group_id, qq, event=event)
        mc = ap.setdefault("craft", {}).setdefault("mooncake", {})
        n = int(mc.get(flavor, 0))
        mc[flavor] = n + 1
        if n == 0:
            amt = self._int_cfg("craft_bonus", 20)
            self._grant_yuehua(ap, group_id, amt)
            return T.CRAFT_ORIGINAL.format(name=flavor, amount=amt)
        got = len([k for k, v in mc.items() if int(v) > 0])
        return T.CRAFT_DUPLICATE.format(name=flavor, n=got, total=len(CRAFT_FLAVORS))

    # ------------------------------------------------------------------
    # 玩法：贺词（Jev 双闸审核 + 本地敏感词兜底）
    # ------------------------------------------------------------------
    def _firework_review(self, text: str) -> tuple[bool, str]:
        """返回 (是否上墙, 拒绝原因)。Jev 一次请求并行问完两道闸。"""
        for w in _SENSITIVE_WORDS:
            if w in text:
                return False, T.FIREWORK_REASON_SENSITIVE
        if not self.jev.enabled:
            return True, ""  # Jev 未启用 → 本地词表已过，保守放行
        ans = JEV._ask(
            {"贺词内容": text},
            {
                "compliance": q_noul(
                    "贺词内容是否合规（文明正面、无涉政敏感/辱骂/广告/不当内容）？",
                    {"true": "文明、正面、无敏感/辱骂/广告", "false": "涉及敏感、辱骂、广告或不适内容"},
                ),
                "theme": q_choice(
                    "这条贺词的主题是什么？",
                    {"国庆祝福": "祝福祖国/华诞/庆典", "中秋祝福": "中秋/团圆/月亮祝福",
                     "游戏相关内容": "与本游戏玩法相关", "其他": "不属于以上类别"},
                ),
            },
        )
        if ans is None:
            return True, ""  # Jev 请求失败/超时 → 保守放行
        compliance = ans.get("compliance") or {}
        try:
            ok = decide_noul(compliance, threshold=NOUL_YES)
        except Exception:
            ok = False
        if not ok:
            return False, T.FIREWORK_REASON_SENSITIVE
        theme = ans.get("theme") or {}
        try:
            t = decide_choice(theme, min_confidence=CHOICE_MIN_CONFIDENCE)
        except Exception:
            t = None
        if t in ("国庆祝福", "中秋祝福"):
            return True, ""
        return False, T.FIREWORK_REASON_IRRELEVANT

    def _cmd_firework(self, event, qq, group_id, rest: str) -> str:
        ap = self._get_player(group_id, qq, event=event)
        self._daily_reset(ap)
        d = ap["daily"]
        limit = self._int_cfg("firework_daily_limit", 3)
        if int(d.get("firework", 0)) >= limit:
            return T.FIREWORK_DAILY_LIMIT.format(limit=limit)
        text = rest.strip()
        if not text:
            return T.FIREWORK_REJECT.format(reason="内容为空，请写一句祝福")
        max_len = self._int_cfg("firework_max_len", 30)
        if len(text) > max_len:
            return T.FIREWORK_TOO_LONG.format(limit=max_len)
        ok, reason = self._firework_review(text)
        if not ok:
            return T.FIREWORK_REJECT.format(reason=reason)
        amt = self._rand_int("gongde_firework_min", "gongde_firework_max", 5, 20)
        self._grant_yuehua(ap, group_id, amt)
        d["firework"] = int(d.get("firework", 0)) + 1
        wall = self._data.setdefault("wall", [])
        wall.append({
            "text": text, "name": ap.get("name") or str(qq), "qq": str(qq),
            "group": str(group_id), "likes": 0, "ts": self._now(),
        })
        return T.FIREWORK_ON_WALL.format(text=text, amount=amt)

    # ------------------------------------------------------------------
    # 玩法：月华墙 / 点赞
    # ------------------------------------------------------------------
    def _cmd_wall(self, event, qq, group_id, rest: str) -> str:
        wall = self._data.get("wall") or []
        if not wall:
            return T.FIREWORK_WALL_EMPTY
        lines = [T.FIREWORK_WALL_HEADER]
        for i, w in enumerate(reversed(wall), 1):
            lines.append(T.FIREWORK_WALL_ITEM.format(idx=i, text=w.get("text", ""), name=w.get("name", "")))
        return "\n".join(lines)

    def _cmd_like(self, event, qq, group_id, rest: str) -> str:
        ap = self._get_player(group_id, qq, event=event)
        self._daily_reset(ap)
        d = ap["daily"]
        limit = self._int_cfg("like_daily_limit", 5)
        if int(d.get("like", 0)) >= limit:
            return T.LIKE_DONE.format(limit=limit)
        m = re.search(r"\d+", rest)
        if not m:
            return T.LIKE_NEED_INDEX
        n = int(m.group())
        wall = self._data.get("wall") or []
        if n < 1 or n > len(wall):
            return T.LIKE_NOT_FOUND.format(idx=n)
        w = wall[-n]  # 墙上按新→旧展示，编号 n 对应倒序第 n 条
        if str(w.get("qq")) == str(qq):
            return T.LIKE_SELF
        w["likes"] = int(w.get("likes", 0)) + 1
        d["like"] = int(d.get("like", 0)) + 1
        amt = self._int_cfg("gongde_like", 2)
        author = self._get_player(str(w.get("group")), str(w.get("qq")))
        self._grant_yuehua(author, w.get("group"), amt)
        return T.LIKE_OK.format(amount=amt)

    # ------------------------------------------------------------------
    # 玩法：华诞巡礼（Jev score 自适应难度 + 连对倍率）
    # ------------------------------------------------------------------
    def _quiz_difficulty(self, ap) -> int:
        tier = JEV.score_tier(
            {"累计月华": int(ap.get("yuehua_earned", 0)),
             "已签到天数": int((ap.get("sign") or {}).get("count", 0)),
             "连续答对": int(ap.get("quiz_streak", 0))},
            "为该玩家选择本轮巡礼题目难度",
            ["简单", "中等", "困难"],
        )
        if tier is None:
            return 2  # 兜底：中等
        return max(1, min(int(tier) + 1, 3))

    @staticmethod
    def _quiz_ask_text(quiz, timeout: int) -> str:
        diff = int(quiz.get("difficulty", 2))
        text = T.QUIZ_ASK.format(
            question=quiz.get("q"),
            options=puzzles.quiz_options_text({"options": quiz.get("options") or []}),
            timeout=timeout,
        )
        return text + f"\n\n难度：{T.QUIZ_DIFF_TAG.get(diff, '中等')}"

    def _cmd_quiz(self, event, qq, group_id, rest: str) -> str:
        ap = self._get_player(group_id, qq, event=event)
        self._daily_reset(ap)
        d = ap["daily"]
        limit = self._int_cfg("quiz_daily_limit", 20)
        timeout = self._int_cfg("quiz_timeout_sec", 60)
        quiz = ap.get("quiz") or {}
        if not rest:
            if int(d.get("quiz", 0)) >= limit:
                return T.QUIZ_DAILY_LIMIT.format(limit=limit)
            if quiz.get("kind") == "xunli" and quiz.get("date") == self._bj_date():
                return self._quiz_ask_text(quiz, timeout)
            difficulty = self._quiz_difficulty(ap)
            p = puzzles.local_quiz(difficulty)
            ap["quiz"] = {
                "kind": "xunli", "q": p.get("q"), "a": str(p.get("answer", "")),
                "options": p.get("options") or [], "answer_type": p.get("answer_type"),
                "date": self._bj_date(), "ts": self._now(), "difficulty": difficulty,
            }
            d["quiz"] = int(d.get("quiz", 0)) + 1
            return self._quiz_ask_text(ap["quiz"], timeout)
        # 作答
        if quiz.get("kind") != "xunli" or quiz.get("date") != self._bj_date():
            return T.QUIZ_WRONG.format(answer="（当前没有进行中的巡礼题，请先发「巡礼」）")
        if self._now() - int(quiz.get("ts", 0)) > timeout:
            ap.pop("quiz", None)
            ap["quiz_streak"] = 0
            return T.QUIZ_TIMEOUT.format(answer=quiz.get("a"))
        user_ans = rest.strip()
        correct = puzzles.is_correct(user_ans, {
            "answer": quiz.get("a"), "answer_type": quiz.get("answer_type"),
            "options": quiz.get("options") or [],
        })
        ap.pop("quiz", None)
        if correct:
            return self._quiz_right(ap, group_id, quiz)
        ap["quiz_streak"] = 0
        return T.QUIZ_WRONG.format(answer=quiz.get("a"))

    def _quiz_right(self, ap, group_id, quiz) -> str:
        diff = int(quiz.get("difficulty", 2))
        lo, hi = self._int_cfg("gongde_quiz_min", 5), self._int_cfg("gongde_quiz_max", 20)
        if lo > hi:
            lo, hi = hi, lo
        if diff == 1:
            base = random.randint(lo, lo + (hi - lo) // 2)
        elif diff == 3:
            base = random.randint(lo + (hi - lo) // 2, hi)
        else:
            base = random.randint(lo, hi)
        streak = int(ap.get("quiz_streak", 0)) + 1
        ap["quiz_streak"] = streak
        rate = 1 + 0.1 * min(streak // 5, 3)  # 每连对 5 题 +10%，上限 +30%
        amt = int(base * rate)
        self._grant_yuehua(ap, group_id, amt)
        lines = [T.QUIZ_RIGHT.format(answer=quiz.get("a"), amount=amt)]
        if rate > 1:
            lines.append(T.QUIZ_COMBO.format(n=streak, rate=rate))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 月华榜 / 里程碑 / 结算
    # ------------------------------------------------------------------
    def _cmd_rank(self, event, qq, group_id, rest: str) -> str:
        players = [p for p in self._players().values() if int(p.get("yuehua_earned", 0)) > 0]
        if not players:
            return T.RANK_EMPTY
        players.sort(key=lambda p: (-int(p.get("yuehua_earned", 0)), int(p.get("bound_at", 0))))
        lines = [T.RANK_HEADER]
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        for i, p in enumerate(players[:20], 1):
            medal = medals.get(i, f"{i}.")
            lines.append(T.RANK_ROW.format(
                medal=medal, rank=i, name=p.get("name") or p.get("qq", "?"),
                score=p.get("yuehua_earned", 0), days=int((p.get("sign") or {}).get("count", 0)),
            ))
        return "\n".join(lines)

    def _cmd_milestone(self, event, qq, group_id, rest: str) -> str:
        ms = self.cfg.get("milestones") or []
        gs = self._group_state(group_id)
        total = int(gs.get("yuehua_total", 0))
        reached = set(int(i) for i in (gs.get("milestone_reached") or []))
        for i, m in enumerate(ms):
            if i not in reached:
                return T.MILESTONE_QUERY.format(total=total, next=m.get("threshold"), amount=m.get("gongde"))
        return T.MILESTONE_QUERY.format(total=total, next="已全部达成", amount=0)

    def reset_data(self) -> None:
        """清空活动数据（玩家 / 群 / 月华墙 + 结算标志），保留配置与代码。

        注意 ``meta.settled`` 必须一并清掉：否则清完数据活动仍被标记为「已结算」，
        再也发不出结算奖励。
        """
        self._data["players"] = {}
        self._data["groups"] = {}
        self._data["wall"] = []
        meta = self._data.setdefault("meta", {})
        meta.pop("settled", None)
        meta.pop("settled_at", None)

    def _settle(self) -> None:
        """整个活动结束后唯一一次结算：全服总榜前 20 名，纯月华写回 players 桶。"""
        if self._is_settled():
            return
        players = [p for p in self._players().values() if int(p.get("yuehua_earned", 0)) > 0]
        players.sort(key=lambda p: (-int(p.get("yuehua_earned", 0)), int(p.get("bound_at", 0))))
        end_rewards = self.cfg.get("end_rewards") or []
        lines = [T.SETTLE_HEADER]
        for i, p in enumerate(players[:20], 1):
            score = int(p.get("yuehua_earned", 0))
            reward = tier_yuehua_for_rank(i, end_rewards)
            if reward > 0:
                self._add_yuehua(p, reward)
            lines.append(T.SETTLE_ROW.format(
                rank=i, name=p.get("name") or p.get("qq", "?"), score=score, reward=reward,
            ))
        self._mark_settled()
        text = "\n".join(lines)
        self._push_all_groups(text)
        logger.info("[moonfest] 月耀华诞活动结算完成：%d 人上榜", min(20, len(players)))

    # ------------------------------------------------------------------
    # 管理指令
    # ------------------------------------------------------------------
    def commands(self) -> set[str]:
        return COMMANDS

    def dispatch(self, event, qq, group_id, text: str) -> str | None:
        result = self._dispatch(event, qq, group_id, text)
        if result is not None:
            self._spawn(self.save())
        return result

    def _dispatch(self, event, qq, group_id, text: str) -> str | None:
        tokens = (text or "").strip().split()
        if not tokens:
            return None
        cmd = tokens[0]
        rest = (text or "").strip()[len(cmd):].strip()

        if not self._enabled():
            return T.NOT_OPEN

        phase = self._phase()
        # 只读指令：活动开放期间随时可用
        if cmd == "活动帮助":
            return T.HELP_TEXT
        if cmd == "月华榜":
            return self._cmd_rank(event, qq, group_id, rest)
        if cmd == "里程碑":
            return self._cmd_milestone(event, qq, group_id, rest)
        if cmd == "月华墙":
            return self._cmd_wall(event, qq, group_id, rest)

        # 阶段门控
        need = _PHASE_GATES.get(cmd)
        if need and phase != "both" and need != phase:
            return T.PHASE_NOT_OPEN.format(phase=T.PHASE_NAME.get(need, need))

        if cmd == "拜月":
            return self._cmd_sign_mid(event, qq, group_id)
        if cmd == "华诞签到":
            return self._cmd_sign_nat(event, qq, group_id)
        if cmd == "猜灯谜":
            return self._cmd_lantern(event, qq, group_id, rest)
        if cmd == "喂玉兔":
            return self._cmd_feed(event, qq, group_id, rest)
        if cmd == "做月饼":
            return self._cmd_craft(event, qq, group_id, rest)
        if cmd == "贺词":
            return self._cmd_firework(event, qq, group_id, rest)
        if cmd == "点赞":
            return self._cmd_like(event, qq, group_id, rest)
        if cmd == "巡礼":
            return self._cmd_quiz(event, qq, group_id, rest)
        return None

    async def loop(self) -> None:
        await asyncio.sleep(3)
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[moonfest] 后台循环异常")
            await asyncio.sleep(30)

    async def _tick(self) -> None:
        # 活动彻底结束且未结算 → 自动结算；随后落盘
        if self._activity_over() and not self._is_settled():
            self._settle()
        await self.save()

    def _spawn(self, coro) -> None:
        try:
            asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            pass

    async def _push_group(self, group_id, text: str) -> None:
        """向单个群主动推送（走插件主类的 ``_send_group_text``）。

        此前这里调的是 ``self.bot._send_to_group(...)`` —— 该方法在整个仓库里
        **根本不存在**，每次推送都抛 AttributeError 并被下面的 except 吞掉，只留
        一行「推送群 X 失败」（连异常原文都没打）。于是全群通报、里程碑公告、结算
        公告全部静默失效，后台点「全群通报测试」毫无反应。
        """
        try:
            await self.bot._send_group_text(str(group_id), text)
        except Exception:  # noqa: BLE001 - 单群失败不影响其余群，但必须留原文
            logger.warning("[moonfest] 推送群 %s 失败", group_id, exc_info=True)

    def _push_all_groups(self, text: str) -> int:
        """向所有已注册群广播，返回目标群数（里程碑 / 结算 / 后台测试按钮）。

        群 ID 取宿主 store 的 groups 桶——其键就是 group_openid，与
        ``bot.send_group`` 的入参、以及本模块 players 里记的 group 是同一空间。
        """
        gids = []
        try:
            gids = list(self.bot.store._data.get("groups", {}).keys())
        except Exception:  # noqa: BLE001 - store 不可用则退回模块自己的群桶
            gids = []
        if not gids:
            gids = list(self._groups().keys())
        if not gids:
            logger.warning("[moonfest] 全群通报：没有可推送的群（store 与模块 groups 均为空）")
            return 0
        logger.info("[moonfest] 全群通报：目标 %d 个群", len(gids))
        for gid in gids:
            self._spawn(self._push_group(gid, text))
        return len(gids)

    def start(self) -> None:
        if self._loop_task_ref is None or self._loop_task_ref.done():
            self._loop_task_ref = asyncio.create_task(self.loop())

    async def terminate(self) -> None:
        if self._loop_task_ref is not None:
            self._loop_task_ref.cancel()
            await asyncio.sleep(0)
            self._loop_task_ref = None
        await self.save()
