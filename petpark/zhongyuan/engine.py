"""中元节《青灯伴萌宠 · 幽影饲育馆》活动引擎。

独立模块：本活动所有逻辑集中在 ``petpark/zhongyuan/``，与宠物乐园主玩法解耦。
- 数据独立持久化到 ``<data>/plugin_data/astrbot_plugin_petpark/zhongyuan.json``；
- 后台「活动总开关 / 一键关闭全部玩法 / 一键删除活动代码」三控制项；
  关闭 = 停玩法留数据；删除 = 清数据/卸载（见命令行 ``删除中元活动`` 与文档第九/十节）。

对外接口（由宠物乐园 main.py 以最小钩子接入）：
- ``commands()``        -> 返回本活动的指令首词集合（供 KNOWN_COMMANDS / AI 路由）。
- ``dispatch(event, qq, group_id, text)`` -> 处理一条指令，返回回复文本或 None。
- ``async loop()``      -> 后台循环（每小时抽人、解密时限、活动结算、定时保存）。
- ``async save()``      -> 落盘。
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from astrbot.api import logger

from . import puzzles
from .config import (
    ACTIVITY_KEY,
    ACTIVITY_NAME,
    ACTIVITY_TAG,
    DEFAULT_CONFIG,
    editable_keys,
    merge_config,
    tier_gongde_for_rank,
    tier_name_for_rank,
)
from .deepseek import DeepSeekClient

BJ = ZoneInfo("Asia/Shanghai")

# 本活动指令首词（用于 KNOWN_COMMANDS 放行与 AI 意图路由）
COMMANDS = {
    # 玩家
    "中元活动", "中元活动介绍", "中元状态", "我的中元",
    "副本进度", "我的副本",
    "相约中元", "中元功德榜", "中元排行", "中元签到", "中元里程碑", "功德里程碑",
    "放河灯", "中元问答", "焚香", "供灯", "答", "中元答", "解除阴气", "功德商店",
    # 管理
    "开启中元活动", "关闭中元活动", "中元配置", "中元开始", "中元结束",
    "删除中元活动", "重置中元活动", "中元结算",
}

# 中元文化问答（本地题库，DeepSeek 可扩充）
_CULTURE_QUIZ = [
    {"q": "农历七月十五，道教称之为什么节？", "a": "中元节", "hint": "与地官赦罪相关。"},
    {"q": "盂兰盆节源自哪位佛弟子的救母典故？", "a": "目连", "hint": "亦作「目犍连」。"},
    {"q": "中元节民间最重要的习俗之一，是放什么到河上寄托思念？", "a": "河灯", "hint": "为亡魂照路。"},
    {"q": "中元节祭祖时，民间常点几炷香？", "a": "三炷香", "hint": "一敬天、二敬地、三敬祖先。"},
    {"q": "佛教称七月十五为什么节？", "a": "盂兰盆节", "hint": "源自目连救母。"},
    {"q": "中元节的核心情感，是勾连阴阳两界、寄托什么？", "a": "思念", "hint": "中元不是鬼节。"},
]

# 放河灯温情回文（本地兜底，DeepSeek 可用时优先生成）
_LANTERN_ECHOES = [
    "灯随水流去，思念寄远乡。愿你所念之人，一切安好。",
    "一盏青灯照归途，两处相思各平安。",
    "河灯明灭，如你心事；流水东去，替你捎去一声珍重。",
    "此灯不为驱鬼，只为照见思念的归途。",
]


class ZhongyuanActivity:
    """中元活动引擎。``bot`` 为宠物乐园插件实例（提供 store / context / 权限与推送）。"""

    def __init__(self, bot, data_dir: Path, config: dict | None = None):
        self.bot = bot
        self.data_path = Path(data_dir) / f"{ACTIVITY_KEY}.json"
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._data: dict[str, Any] = {}
        self._load()
        # 配置：默认值 <- json 里的 config 覆盖 <- 显式传入覆盖
        self.cfg = merge_config(DEFAULT_CONFIG, self._data.get("config") or {})
        if config:
            for k, v in config.items():
                if k in self.cfg:
                    self.cfg[k] = v
        # DeepSeek 客户端（Key 优先环境变量，其次配置，绝不写死）
        api_key = self.cfg.get("deepseek_api_key") or os.environ.get("DEEPSEEK_API_KEY", "")
        self.cfg["deepseek_api_key"] = api_key
        self._deepseek = DeepSeekClient(
            base_url=self.cfg.get("deepseek_base_url", "https://api.deepseek.com"),
            api_key=api_key if self.cfg.get("deepseek_enabled", True) else "",
            model=self.cfg.get("deepseek_model", "deepseek-v4-flash-vision-exp"),
            timeout=float(self.cfg.get("deepseek_timeout", 30)),
            temperature=float(self.cfg.get("deepseek_temperature", 0.3)),
            max_tokens=int(self.cfg.get("deepseek_max_tokens", 800)),
        )
        # 后台任务引用（实例属性，terminate 统一取消）
        self._loop_task_ref = None

    # ------------------------------------------------------------------
    # 数据读写
    # ------------------------------------------------------------------
    def _load(self) -> None:
        try:
            if self.data_path.exists():
                self._data = json.loads(self.data_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._data = {}
        self._data.setdefault("config", {})
        self._data.setdefault("meta", {})
        self._data.setdefault("players", {})
        self._data.setdefault("groups", {})
        self._data.setdefault("sessions", {})
        # 迁移：旧版按「群\x1f用户」隔离，现改为按用户 ID 全局唯一（相约中元）。
        migrated: dict[str, Any] = {}
        for k, v in self._data.get("players", {}).items():
            ks = str(k)
            if "\x1f" in ks:
                gid, qq = ks.split("\x1f", 1)
                v.setdefault("qq", qq)
                v.setdefault("group", gid)
                v.pop("pet_id", None)
                v.pop("pet_level", None)
                if "name" not in v and v.get("pet_name"):
                    v["name"] = v["pet_name"]
                v.pop("pet_name", None)
                migrated[qq] = v  # 同一用户跨群重复时，后者覆盖（活动 ID 按用户唯一）
            else:
                migrated[ks] = v
        if migrated:
            self._data["players"] = migrated

    def _flush(self) -> None:
        try:
            self.data_path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[zhongyuan] 落盘失败：%s", e)

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

    @classmethod
    def _bj_date(cls) -> str:
        return cls._now_bj().strftime("%Y-%m-%d")

    @classmethod
    def _bj_hour(cls) -> int:
        return cls._now_bj().hour

    def _cfg(self, key: str, default=None):
        return self.cfg.get(key, default)

    def _int_cfg(self, key: str, default: int = 0) -> int:
        try:
            return int(self.cfg.get(key, default))
        except (TypeError, ValueError):
            return default

    def apply_config(self, updates: dict) -> tuple[int, list[str]]:
        """应用一批配置（web 后台保存用）。返回 (成功数, 失败键列表)。"""
        ok, bad = 0, []
        for key, raw in updates.items():
            if key not in self.cfg:
                bad.append(key)
                continue
            try:
                new = self._coerce_config(self.cfg[key], raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                bad.append(key)
                continue
            self.cfg[key] = new
            self._data["config"][key] = new
            ok += 1
        # DeepSeek 相关配置变更后重建客户端，即时生效
        if any(
            k in ("deepseek_api_key", "deepseek_model", "deepseek_base_url",
                  "deepseek_timeout", "deepseek_temperature", "deepseek_max_tokens",
                  "deepseek_enabled")
            for k in updates
            if k in self.cfg
        ):
            api_key = self.cfg.get("deepseek_api_key") or os.environ.get("DEEPSEEK_API_KEY", "")
            self._deepseek = DeepSeekClient(
                base_url=self.cfg.get("deepseek_base_url", "https://api.deepseek.com"),
                api_key=api_key if self.cfg.get("deepseek_enabled", True) else "",
                model=self.cfg.get("deepseek_model", "deepseek-v4-flash-vision-exp"),
                timeout=float(self.cfg.get("deepseek_timeout", 30)),
                temperature=float(self.cfg.get("deepseek_temperature", 0.3)),
                max_tokens=int(self.cfg.get("deepseek_max_tokens", 800)),
            )
        return ok, bad

    @staticmethod
    def _coerce_config(cur, raw):
        """按当前值类型把 raw 转换为目标类型；list/dict 字段接受 JSON 字符串或原对象。"""
        if isinstance(cur, bool):
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in ("true", "1", "yes", "on", "开启", "开")
        if isinstance(cur, int):
            return int(raw)
        if isinstance(cur, float):
            return float(raw)
        if isinstance(cur, (list, dict)):
            if isinstance(raw, str):
                return json.loads(raw)
            return raw
        return raw

    # ------------------------------------------------------------------
    # 活动状态
    # ------------------------------------------------------------------
    def _enabled(self) -> bool:
        if not self._cfg("enabled", True):
            return False
        now = self._now()
        start = self._int_cfg("start_at", 0)
        end = self._int_cfg("end_at", 0)
        if start and now < start:
            return False
        if end and now > end:
            return False
        return True

    def _in_open_hours(self) -> bool:
        h = self._bj_hour()
        return self._int_cfg("open_hour", 8) <= h < self._int_cfg("close_hour", 22)

    def _activity_over(self) -> bool:
        end = self._int_cfg("end_at", 0)
        return bool(end) and self._now() > end

    def _redeem_open(self) -> bool:
        if not self._activity_over():
            return False
        end = self._int_cfg("end_at", 0)
        window = self._int_cfg("redeem_window_hours", 48) * 3600
        return end < self._now() <= end + window

    # ------------------------------------------------------------------
    # 数据访问
    # ------------------------------------------------------------------
    def _players(self) -> dict:
        return self._data.setdefault("players", {})

    def _groups(self) -> dict:
        return self._data.setdefault("groups", {})

    def _sessions(self) -> dict:
        return self._data.setdefault("sessions", {})

    def _key(self, group_id: str, qq: str) -> str:
        # 活动身份按用户 ID（QQ）全局唯一，跨群共享，不再与群/宠物绑定
        return str(qq)

    def _get_player(self, group_id: str, qq: str, create: bool = False) -> dict | None:
        players = self._players()
        key = self._key(group_id, qq)
        if key not in players and create:
            players[key] = {
                "qq": str(qq),
                "group": str(group_id),
                "name": str(qq),
                "activity_id": 0,
                "gongde": 0,
                "clear_count": 0,
                "perfect_count": 0,
                "fail_count": 0,
                "best_time_sec": 0,
                "bound_at": self._now(),
                "yin_until": 0,
                "last_draw_date": "",
                "draw_count_today": 0,
                "escrow": 0,
                "daily": {"date": "", "lantern": 0, "quiz": 0, "incense": 0, "sign": 0},
                "quiz": {},
                "last_lantern_ts": 0,
                "last_incense_ts": 0,
            }
        return players.get(key)

    def _group_state(self, group_id: str) -> dict:
        groups = self._groups()
        gid = str(group_id)
        if gid not in groups:
            groups[gid] = {
                "gongde_total": 0,
                "milestone_reached": [],
                # 首次注册等到下一个整点再抽人，避免刚绑定就被勾入副本
                "next_trigger_ts": self._next_hour_ts(self._now()),
            }
        g = groups[gid]
        g.setdefault("gongde_total", 0)
        g.setdefault("milestone_reached", [])
        g.setdefault("next_trigger_ts", self._next_hour_ts(self._now()))
        return g

    def _session(self, group_id: str) -> dict | None:
        return self._sessions().get(str(group_id))

    # ------------------------------------------------------------------
    # 帮助方法
    # ------------------------------------------------------------------
    def commands(self) -> set[str]:
        return set(COMMANDS)

    def _spawn(self, coro) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)
        except RuntimeError:
            pass

    def _fmt_time(self, ts: int) -> str:
        return datetime.fromtimestamp(ts, BJ).strftime("%m-%d %H:%M")

    def _fmt_remain(self, ts: int) -> str:
        remain = ts - self._now()
        if remain <= 0:
            return "已到期"
        m, s = divmod(max(0, remain), 60)
        return f"{m} 分 {s} 秒"

    # ------------------------------------------------------------------
    # 相约中元（参与活动领号，按用户 ID 绑定，跨群唯一；不可重复参加）
    # ------------------------------------------------------------------
    def _bind_user(self, group_id: str, qq: str, event=None) -> str:
        if not self._enabled():
            return f"❌ {ACTIVITY_TAG} 尚未开启或已结束。"
        ap = self._get_player(group_id, qq, create=True)
        if ap.get("activity_id"):
            return (
                f"❌ 你已相约中元（活动 ID #{ap['activity_id']:04d}），"
                "活动期间不可重复参加、不可更换。"
            )
        ap["activity_id"] = self._alloc_activity_id()
        ap["group"] = str(group_id)
        # 展示名：昵称优先，取不到回退为 QQ 号
        ap["name"] = self._user_name(event, qq)
        # 注册本群状态，使其进入每小时抽人的候选群
        self._group_state(group_id)
        return (
            f"🕯️ 相约中元！你已踏入阴阳两界。\n"
            f"> 你的活动 ID：**#{ap['activity_id']:04d}**\n"
            "> ⚠️ 此 ID 与你本人绑定、跨群唯一，活动期间不可更换。"
        )

    def _user_name(self, event, qq: str) -> str:
        if event is None:
            return str(qq)
        try:
            name = self.bot._sender_name(event)
        except Exception:  # noqa: BLE001
            name = ""
        return str(name).strip() or str(qq)

    def _alloc_activity_id(self) -> int:
        seq = int(self._data["meta"].get("activity_id_seq", 0)) + 1
        self._data["meta"]["activity_id_seq"] = seq
        return seq

    # ------------------------------------------------------------------
    # 文化玩法：放河灯 / 问答 / 焚香 / 签到
    # ------------------------------------------------------------------
    def _daily_reset(self, ap: dict) -> dict:
        d = ap.setdefault("daily", {})
        today = self._bj_date()
        if d.get("date") != today:
            d.update({"date": today, "lantern": 0, "quiz": 0, "incense": 0, "sign": 0})
        return d

    def _add_gongde(self, ap: dict, amount: int) -> None:
        ap["gongde"] = int(ap.get("gongde", 0)) + max(0, int(amount))

    def _rand_gongde(self, min_key: str, max_key: str, default_min: int, default_max: int) -> int:
        """在配置的 [min, max] 区间内取随机功德（闭合区间，上下限相等则固定）。"""
        lo = self._int_cfg(min_key, default_min)
        hi = self._int_cfg(max_key, default_max)
        if lo > hi:
            lo, hi = hi, lo
        if lo == hi:
            return lo
        return random.randint(lo, hi)

    def _group_add_gongde(self, group_id: str, amount: int) -> None:
        g = self._group_state(group_id)
        g["gongde_total"] = int(g.get("gongde_total", 0)) + max(0, int(amount))
        self._check_milestones(group_id)

    def _check_milestones(self, group_id: str) -> list[str]:
        """群累计功德达标时，向本群每位参与者直接发放共享功德（不入暂存），返回新达成的档位。"""
        g = self._group_state(group_id)
        total = int(g.get("gongde_total", 0))
        reached = set(g.get("milestone_reached", []))
        newly = []
        last_info = None
        for i, ms in enumerate(self.cfg.get("milestones", [])):
            if i in reached:
                continue
            if total >= int(ms.get("threshold", 0)):
                reached.add(i)
                rw = int(ms.get("gongde", 0))
                newly.append(f"阶段{i + 1}（{int(ms['threshold'])} 功德 → +{rw}）")
                last_info = (i + 1, int(ms.get("threshold", 0)), rw)
                # 每位参与者直接发放，不入暂存
                for ap in self._players_in_group(group_id):
                    self._add_gongde(ap, rw)
        g["milestone_reached"] = sorted(reached)
        if newly and last_info:
            stage, th, rw = last_info
            self._spawn(self._push_group(
                group_id,
                f"## 🎊 中元里程碑 · 第 {stage} 阶段达成\n"
                f"群累计功德突破 **{th}**，本群每位参与者直接获得 **+{rw}** 功德！",
            ))
        return newly

    def _players_in_group(self, group_id: str) -> list[dict]:
        gid = str(group_id)
        return [v for v in self._players().values() if str(v.get("group", "")) == gid]

    def _cmd_lantern(self, group_id: str, qq: str, event, message: str) -> str:
        if not self._enabled():
            return f"❌ {ACTIVITY_TAG} 尚未开启或已结束。"
        ap = self._get_player(group_id, qq, create=True)
        if not ap.get("activity_id"):
            return "❌ 请先「相约中元」领取活动 ID 后再放河灯。"
        d = self._daily_reset(ap)
        limit = self._int_cfg("lantern_daily_limit", 10)
        if d["lantern"] >= limit:
            return f"❌ 今日已放过 {limit} 盏河灯，明日再来吧。"
        cd = self._int_cfg("lantern_cooldown_min", 20) * 60
        last = int(ap.get("last_lantern_ts", 0))
        if last and self._now() - last < cd:
            return f"❌ 河灯随流水远去，还需 **{self._fmt_remain(last + cd)}** 才能再放下一盏。"
        d["lantern"] += 1
        ap["last_lantern_ts"] = self._now()
        reward = self._rand_gongde("gongde_lantern_min", "gongde_lantern_max", 10, 30)
        self._add_gongde(ap, reward)
        self._group_add_gongde(group_id, reward)
        msg = (message or "").strip() or "愿逝者安息，愿生者珍重"
        echo = random.choice(_LANTERN_ECHOES)
        # DeepSeek 温情回文（后台异步推送，不阻塞）
        self._spawn(self._lantern_echo(group_id, ap.get("name", ""), msg))
        return (
            f"🕯️ 你点亮一盏河灯，写下：\n> 「{msg}」\n"
            f"灯随水流去，思念寄远乡。\n> **{echo}**\n"
            f"功德 **+{reward}**（当前 {ap['gongde']}）\n"
            f"> 今日 {d['lantern']}/{limit} 盏 · 冷却 {self._int_cfg('lantern_cooldown_min', 20)} 分钟"
        )

    async def _lantern_echo(self, group_id: str, name: str, message: str) -> None:
        if not self._deepseek.available:
            return
        system = "你是中元节的摆渡人，为玩家的思念寄语写一句温婉、克制、治愈的中式回文，不超过 40 字。"
        echo = await self._deepseek.reply_echo(f"玩家给思念之人的寄语：{message}", system=system)
        if echo:
            await self._push_group(group_id, f"💫 青灯回音：{echo.strip()}")

    def _cmd_quiz(self, group_id: str, qq: str, answer: str | None) -> str:
        if not self._enabled():
            return f"❌ {ACTIVITY_TAG} 尚未开启或已结束。"
        ap = self._get_player(group_id, qq, create=True)
        if not ap.get("activity_id"):
            return "❌ 请先「相约中元」领取活动 ID 后再参与问答。"
        d = self._daily_reset(ap)
        limit = self._int_cfg("quiz_daily_limit", 20)
        if answer is None:
            if d["quiz"] >= limit:
                return f"❌ 今日已答满 {limit} 题，明日再来吧。"
            q = random.choice(_CULTURE_QUIZ)
            ap["quiz"] = {"q": q["q"], "a": q["a"], "date": self._bj_date(), "ts": self._now()}
            return (
                f"🕯️ 中元知多少 · 文化问答（今日 {d['quiz']}/{limit}）\n> {q['q']}\n"
                f"发送「中元问答 <你的答案>」作答，答对得随机功德；"
                f"答错或超时（{self._int_cfg('quiz_timeout_sec', 60)} 秒）即判失败并揭晓答案。"
            )
        quiz = ap.get("quiz", {})
        if not quiz or quiz.get("date") != self._bj_date():
            return "❌ 请先发送「中元问答」领取今日题目。"
        if d["quiz"] >= limit:
            return f"❌ 今日已答满 {limit} 题。"
        timeout = self._int_cfg("quiz_timeout_sec", 60)
        elapsed = self._now() - int(quiz.get("ts", 0))
        if elapsed > timeout:
            d["quiz"] += 1
            ap["quiz"] = {}
            return (
                f"⏰ 答题超时（超过 {timeout} 秒）。正确答案：**{quiz.get('a')}**\n"
                f"❌ 本次无功德。今日已答 {d['quiz']}/{limit}，发送「中元问答」领取下一题。"
            )
        if (answer or "").strip() != str(quiz.get("a", "")).strip():
            d["quiz"] += 1
            ap["quiz"] = {}
            return (
                f"❌ 答错了。正确答案：**{quiz.get('a')}**\n"
                f"本次无功德。今日已答 {d['quiz']}/{limit}，发送「中元问答」领取下一题。"
            )
        d["quiz"] += 1
        ap["quiz"] = {}
        reward = self._rand_gongde("gongde_quiz_min", "gongde_quiz_max", 10, 20)
        self._add_gongde(ap, reward)
        self._group_add_gongde(group_id, reward)
        return f"✅ 答对了！功德 **+{reward}**（当前 {ap['gongde']}）。中元不是鬼节，是勾连阴阳的思念。"

    def _cmd_incense(self, group_id: str, qq: str, kind: str) -> str:
        if not self._enabled():
            return f"❌ {ACTIVITY_TAG} 尚未开启或已结束。"
        ap = self._get_player(group_id, qq, create=True)
        if not ap.get("activity_id"):
            return "❌ 请先「相约中元」领取活动 ID 后再祭祖。"
        d = self._daily_reset(ap)
        limit = self._int_cfg("incense_daily_limit", 10)
        if d["incense"] >= limit:
            return f"❌ 今日已「供灯/焚香」{limit} 次，明日再来吧。"
        cd = self._int_cfg("incense_cooldown_min", 20) * 60
        last = int(ap.get("last_incense_ts", 0))
        if last and self._now() - last < cd:
            return f"❌ 香火未尽，还需 **{self._fmt_remain(last + cd)}** 才能再次祭祖。"
        d["incense"] += 1
        ap["last_incense_ts"] = self._now()
        reward = self._rand_gongde("gongde_incense_min", "gongde_incense_max", 10, 30)
        self._add_gongde(ap, reward)
        self._group_add_gongde(group_id, reward)
        act = "焚三炷香" if kind == "焚香" else "供一盏灯"
        return (
            f"🕯️ 你{act}，一敬天、二敬地、三敬祖先。\n"
            f"> 香火相传，追思绵长。功德 **+{reward}**（当前 {ap['gongde']}）。\n"
            f"> 今日 {d['incense']}/{limit} 次 · 冷却 {self._int_cfg('incense_cooldown_min', 20)} 分钟"
        )

    def _cmd_sign(self, group_id: str, qq: str) -> str:
        if not self._enabled():
            return f"❌ 活动未开启。"
        ap = self._get_player(group_id, qq, create=True)
        if not ap.get("activity_id"):
            return "❌ 请先「相约中元」领取活动 ID 后再签到。"
        d = self._daily_reset(ap)
        if d["sign"] >= 1:
            return "❌ 今日已签到。"
        d["sign"] += 1
        reward = self._int_cfg("gongde_sign", 10)
        self._add_gongde(ap, reward)
        self._group_add_gongde(group_id, reward)
        return f"🕯️ 中元签到成功，功德 **+{reward}**（当前 {ap['gongde']}）。"

    # ------------------------------------------------------------------
    # 解密会话（阴面）
    # ------------------------------------------------------------------
    async def _start_dungeon(self, group_id: str) -> None:
        """每次随机拉入本群参与人数的 20%~50%，协作解密一场副本。"""
        gid = str(group_id)
        self._group_state(gid)
        today = self._bj_date()
        # 本群可被抽取的已绑定玩家（排除当日已到被抽上限者）
        bound = [
            ap for ap in self._players_in_group(gid)
            if ap.get("activity_id")
            and not (ap.get("last_draw_date") == today
                     and int(ap.get("draw_count_today", 0)) >= self._int_cfg("max_draw_per_day", 2))
        ]
        if not bound:
            return
        # 随机拉入 20%~50%
        lo = max(1, min(100, self._int_cfg("pull_min_pct", 20)))
        hi = max(lo, min(100, self._int_cfg("pull_max_pct", 50)))
        pct = random.randint(lo, hi) if lo < hi else lo
        count = max(1, int(len(bound) * pct / 100))
        count = min(count, len(bound))
        chosen = random.sample(bound, count)
        for ap in chosen:
            ap["last_draw_date"] = today
            ap["draw_count_today"] = int(ap.get("draw_count_today", 0)) + 1
        # 规则怪谈 + 围绕规则生成谜题
        rule = await self._generate_rule()
        puzzles_list = await self._generate_puzzles(rule)
        participants = {
            str(ap["qq"]): {
                "qq": str(ap["qq"]),
                "activity_id": ap.get("activity_id"),
                "name": ap.get("name", "?"),
                "correct": 0,
                "wrong": 0,
                "alive": True,
            } for ap in chosen
        }
        self._sessions()[gid] = {
            "group": gid,
            "rule": rule,
            "puzzles": puzzles_list,
            "index": 0,
            "participants": participants,
            "started_at": self._now(),
            "deadline": self._now() + self._int_cfg("dungeon_limit_min", 40) * 60,
            "last_activity": self._now(),
            "last_status_ts": self._now(),
        }
        names = "、".join(f"#{p['activity_id']:04d} {p['name']}" for p in participants.values())
        await self._push_group(
            gid,
            f"## 🚪 阴门开 · 幽影饲育馆（协作解密）\n"
            f"本次共拉入 **{count}** 名驯宠师：{names}\n"
            f"> 📜 **规则怪谈**：{rule}\n"
            f"> 全队共享进度，{self._int_cfg('dungeon_limit_min', 40)} 分钟内解完 {len(puzzles_list)} 题即通关；"
            f"个人答错满 {self._int_cfg('individual_fail_wrong', 3)} 次会被淘汰出局。",
        )
        await self._push_puzzle(gid)

    async def _generate_rule(self) -> str:
        """生成「规则怪谈」总线索：优先 DeepSeek，失败/关闭回退本地模板。"""
        if self._deepseek.available:
            rule = await self._deepseek.generate_rule()
            if rule:
                return rule
        return random.choice(puzzles.LOCAL_RULES)

    async def _generate_puzzles(self, rule: str) -> list[dict]:
        """围绕规则怪谈生成整场谜题：优先 DeepSeek 批量，失败/关闭回退本地模板补齐。"""
        count = max(1, self._int_cfg("puzzle_count", 20))
        out: list[dict] = []
        if self._deepseek.available:
            out = await self._deepseek.generate_puzzles_batch(rule, count)
        while len(out) < count:
            out.append(puzzles.local_puzzle())
        return out[:count]

    async def _push_puzzle(self, group_id: str) -> None:
        s = self._session(group_id)
        if not s:
            return
        p = s["puzzles"][s["index"]]
        opts = "\n".join(f"{i + 1}. {o}" for i, o in enumerate(p.get("options", [])))
        total = len(s["puzzles"])
        idx = int(s.get("index", 0))
        remain = self._fmt_remain(int(s.get("deadline", 0)))
        alive = [x for x in s["participants"].values() if x.get("alive")]
        await self._push_group(
            group_id,
            f"## 🕯️ 第 {idx + 1}/{total} 题\n"
            f"> {p.get('question')}\n\n{opts}\n"
            f"> 任意参与者以「答 <答案>」作答；有人答对，全体进度 +1。\n"
            f"> ⏳ 倒计时 **{remain}** · 存活 **{len(alive)}/{len(s['participants'])}** 人",
        )

    async def _push_dungeon_status(self, group_id: str) -> None:
        """副本进行中，向全群播报倒计时 / 答题进度 / 存活与淘汰情况。"""
        s = self._session(group_id)
        if not s:
            return
        total = len(s.get("puzzles", []))
        idx = int(s.get("index", 0))
        remain = self._fmt_remain(int(s.get("deadline", 0)))
        parts = list(s.get("participants", {}).values())
        alive = [p for p in parts if p.get("alive")]
        dead = [p for p in parts if not p.get("alive")]
        lines = [
            f"⏳ **副本进行中** · 倒计时 {remain}",
            f"📊 答题进度 **{idx}/{total}** 题 · 存活 **{len(alive)}/{len(parts)}** 人",
        ]
        if dead:
            lines.append("💀 已淘汰：" + "、".join(f"#{p['activity_id']:04d}" for p in dead))
        await self._push_group(group_id, "\n".join(lines))

    def _check_answer(self, group_id: str, qq: str, text: str) -> str | None:
        """协作副本作答：有人答对全体进度 +1；个人答错满 N 次即个人出局。"""
        s = self._session(group_id)
        if not s:
            return None
        p = s["participants"].get(str(qq))
        if p is None or not p.get("alive"):
            return None  # 非参与者或已出局，忽略
        if self._now() > s.get("deadline", 0):
            return "⏰ 本场副本已超时。"
        puzzle = s["puzzles"][s["index"]]
        if puzzles.is_correct(text, puzzle):
            p["correct"] = int(p.get("correct", 0)) + 1
            s["last_activity"] = self._now()
            s["index"] += 1
            if s["index"] >= len(s["puzzles"]):
                return self._finish_dungeon(group_id)
            self._spawn(self._push_puzzle(group_id))
            return f"✅ #{p['activity_id']:04d} 答对！全体进度 {s['index']}/{len(s['puzzles'])}。"
        p["wrong"] = int(p.get("wrong", 0)) + 1
        s["last_activity"] = self._now()
        limit = self._int_cfg("individual_fail_wrong", 3)
        if p["wrong"] >= limit:
            return self._eliminate_participant(group_id, qq)
        return (
            f"❌ #{p['activity_id']:04d} 答错（个人 {p['wrong']}/{limit}）。\n"
            f"> 提示：{puzzle.get('hint', '') or '规则怪谈里藏着答案，再想想。'}"
        )

    def _eliminate_participant(self, group_id: str, qq: str) -> str:
        """个人答错满 N 次出局，并「阴气缠身」；若全员出局则副本失败。"""
        s = self._session(group_id)
        p = s["participants"].get(str(qq))
        p["alive"] = False
        ap = self._get_player(group_id, qq)
        if ap is not None:
            self._apply_yin(ap)
            ap["fail_count"] = int(ap.get("fail_count", 0)) + 1
        alive = [x for x in s["participants"].values() if x.get("alive")]
        msg = (
            f"💀 编号 #{p['activity_id']:04d}（{p.get('name', '?')}）答错满 "
            f"{self._int_cfg('individual_fail_wrong', 3)} 次，被阴气淘汰，退出本场。"
        )
        if not alive:
            self._sessions().pop(str(group_id), None)
            msg += "\n全员淘汰，副本失败。"
        return msg

    def _finish_dungeon(self, group_id: str) -> str:
        """通关结算：存活玩家共享通关功德；答对次数前 10% 达成完美，奖励翻倍。"""
        s = self._sessions().pop(group_id, None)
        gid = str(group_id)
        survivors = [p for p in s["participants"].values() if p.get("alive")]
        if not survivors:
            return "🕯️ 全员淘汰，阴门闭合。"
        survivors.sort(key=lambda p: -int(p.get("correct", 0)))
        total_correct = sum(int(p.get("correct", 0)) for p in survivors)
        if total_correct <= 0:
            for p in survivors:
                ap = self._get_player(gid, p["qq"])
                if ap is not None:
                    self._apply_yin(ap)
                    ap["fail_count"] = int(ap.get("fail_count", 0)) + 1
            return (
                f"🕯️ 全员沉默，阴门闭合。本场无人答对，计为失败，存活玩家 {len(survivors)} 人"
                f"均被「阴气缠身」（{self._int_cfg('yin_penalty_min', 60)} 分钟）。"
            )
        perfect_n = max(1, (len(survivors) + 9) // 10)  # 前 10%，至少 1 人
        perfect_set = {p["qq"] for p in survivors[:perfect_n] if int(p.get("correct", 0)) > 0}
        base = self._int_cfg("gongde_clear", 300)
        mult = self._int_cfg("perfect_reward_mult", 2)
        lines = []
        for p in survivors:
            ap = self._get_player(gid, p["qq"])
            if ap is None:
                continue
            perfect = p["qq"] in perfect_set
            reward = base * mult if perfect else base
            self._add_gongde(ap, reward)
            self._group_add_gongde(gid, reward)
            ap["clear_count"] = int(ap.get("clear_count", 0)) + 1
            if perfect:
                ap["perfect_count"] = int(ap.get("perfect_count", 0)) + 1
            tag = "✨完美" if perfect else "🎉通关"
            lines.append(f"{tag} #{p['activity_id']:04d} {p.get('name','?')}：答对 {p['correct']} 题，功德 +{reward}")
        return (
            f"## 🎉 幽影饲育馆通关\n"
            f"> 全队协作解完 {len(s['puzzles'])} 题，存活 {len(survivors)} 人。\n"
            + "\n".join(lines)
        )

    def _fail_dungeon(self, group_id: str, reason: str) -> str:
        """整场超时失败：存活玩家全部「阴气缠身」。"""
        s = self._sessions().pop(group_id, None)
        gid = str(group_id)
        survivors = [p for p in s["participants"].values() if p.get("alive")]
        for p in survivors:
            ap = self._get_player(gid, p["qq"])
            if ap is not None:
                self._apply_yin(ap)
                ap["fail_count"] = int(ap.get("fail_count", 0)) + 1
        return (
            f"❌ 副本失败（{reason}）。存活玩家 {len(survivors)} 人均被「阴气缠身」"
            f"（{self._int_cfg('yin_penalty_min', 60)} 分钟）。"
        )

    def _apply_yin(self, ap: dict) -> None:
        """施加「阴气缠身」：禁止宠物指令，持续 yin_penalty_min 分钟。"""
        ap["yin_until"] = self._now() + self._int_cfg("yin_penalty_min", 60) * 60

    def yin_lock_block(self, qq: str, group_id: str) -> str | None:
        """「阴气缠身」锁定：main.py 在宠物指令前调用。锁定则返回拦截文案，否则 None。"""
        ap = self._get_player(group_id, qq, create=False)
        if not ap:
            return None
        until = int(ap.get("yin_until", 0))
        if until <= self._now():
            return None
        return (
            f"🕯️ 你的宠物正被「阴气缠身」，无法执行任何宠物指令"
            f"（剩余 {self._fmt_remain(until)}）。\n"
            f"> 期间仅可参与中元活动；发送「解除阴气」消耗 "
            f"{self._int_cfg('yin_clear_cost', 100)} 功德立即解除。"
        )

    # ------------------------------------------------------------------
    # 解除阴气
    # ------------------------------------------------------------------
    def _cmd_clear_yin(self, group_id: str, qq: str) -> str:
        ap = self._get_player(group_id, qq, create=False)
        if not ap or not ap.get("activity_id"):
            return "❌ 你尚未相约中元。"
        until = int(ap.get("yin_until", 0))
        if until <= self._now():
            return "✅ 你当前并无「阴气缠身」。"
        cost = self._int_cfg("yin_clear_cost", 100)
        if int(ap.get("gongde", 0)) < cost:
            return f"❌ 功德不足，快速解除需 {cost} 功德（当前 {ap['gongde']}）；或等剩余时间自然解除。"
        ap["gongde"] = int(ap.get("gongde", 0)) - cost
        ap["yin_until"] = 0
        return f"✅ 已耗费 **{cost}** 功德解除「阴气缠身」，你的宠物重归清明。"

    # ------------------------------------------------------------------
    # 排行 / 状态 / 兑换
    # ------------------------------------------------------------------
    def _cmd_rank(self, group_id: str) -> str:
        players = self._players_in_group(group_id)
        ranked = sorted(
            players,
            key=lambda p: (
                -int(p.get("gongde", 0)),
                -int(p.get("clear_count", 0)),
                int(p.get("best_time_sec", 0)) or (1 << 30),
            ),
        )
        if not ranked:
            return "🕯️ 中元功德榜：本群暂无已绑定玩家。"
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = ["## 🕯️ 中元功德榜（前 20）", ""]
        lines.append("| 排名 | 段位 | 活动ID | 玩家 | 功德 | 通关 | 完美 |")
        lines.append("|:--:|:--:|:--:|:--:|--:|--:|--:|")
        for i, p in enumerate(ranked[:20], start=1):
            tier = tier_name_for_rank(i, self.cfg.get("tiers", [])) or "—"
            name = str(p.get("name", "?")).replace("|", "丨")
            rk = medals.get(i, str(i))
            lines.append(
                f"| {rk} | {tier} | #{p['activity_id']:04d} | {name} | "
                f"{p.get('gongde', 0)} | {p.get('clear_count', 0)} | {p.get('perfect_count', 0)} |"
            )
        return "\n".join(lines)

    def _cmd_milestone(self, group_id: str) -> str:
        """查看全群累计功德里程碑进度（5 个阶段，最高档 1 万功德为满）。"""
        g = self._group_state(group_id)
        total = int(g.get("gongde_total", 0))
        reached = set(g.get("milestone_reached", []))
        ms = self.cfg.get("milestones", [])
        if not ms:
            return "🕯️ 本活动中元里程碑尚未配置。"
        lines = ["## 🎊 中元里程碑（全群累计功德）", ""]
        for i, m in enumerate(ms):
            th = int(m.get("threshold", 0))
            rw = int(m.get("gongde", 0))
            mark = "✅ 已达成" if i in reached else "⏳ 未达成"
            lines.append(f"{mark} · 阶段{i + 1}：累计 **{th}** 功德 → 每位参与者 **+{rw}** 功德（直接发放）")
        lines.append("")
        lines.append(f"> 当前全群累计功德：**{total}**")
        return "\n".join(lines)

    def _cmd_status(self, group_id: str, qq: str) -> str:
        ap = self._get_player(group_id, qq, create=False)
        if not ap or not ap.get("activity_id"):
            return (
                "🕯️ 你尚未相约中元。\n"
                "> 发送「相约中元」领取活动 ID，踏入阴阳两界。"
            )
        yin = "🈳 无" if int(ap.get("yin_until", 0)) <= self._now() else (
            f"⚠️ 阴气缠身（剩余 {self._fmt_remain(int(ap['yin_until']))}，"
            f"禁止宠物指令，「解除阴气」需 {self._int_cfg('yin_clear_cost', 100)} 功德）"
        )
        return (
            f"## 🕯️ 我的中元\n"
            f"> 活动 ID：**#{ap['activity_id']:04d}**\n"
            f"> 玩家：**{ap.get('name', '?')}**\n"
            f"> 功德：**{ap.get('gongde', 0)}**\n"
            f"> 暂存功德：{ap.get('escrow', 0)}\n"
            f"> 通关 {ap.get('clear_count', 0)} 次 · 完美 {ap.get('perfect_count', 0)} 次 · 失败 {ap.get('fail_count', 0)} 次\n"
            f"> 状态：{yin}"
        )

    def _cmd_dungeon_status(self, group_id: str, qq: str) -> str:
        """副本进行中：查看自己的答题进度（答对/答错）与是否已淘汰。"""
        s = self._session(group_id)
        if not s:
            return "🕯️ 当前本群没有进行中的副本（阴门未开）。"
        p = s["participants"].get(str(qq))
        if p is None:
            return "🕯️ 你不在本场副本的参与者之列。"
        idx = int(s.get("index", 0))
        total = len(s.get("puzzles", []))
        correct = int(p.get("correct", 0))
        wrong = int(p.get("wrong", 0))
        limit = self._int_cfg("individual_fail_wrong", 3)
        alive = bool(p.get("alive"))
        status = "✅ 存活" if alive else "💀 已淘汰（阴气缠身）"
        remain = self._fmt_remain(int(s.get("deadline", 0)))
        return (
            f"## 🕯️ 你的副本进度\n"
            f"> 编号：**#{p['activity_id']:04d}** · 玩家：**{p.get('name', '?')}**\n"
            f"> 状态：{status}\n"
            f"> 个人答对：**{correct}** 题\n"
            f"> 个人答错：**{wrong}/{limit}**"
            f"{'（再错 ' + str(max(0, limit - wrong)) + ' 次即淘汰）' if alive else ''}\n"
            f"> 全队进度：**{idx}/{total}** 题\n"
            f"> 本场剩余：{remain}"
        )

    def _cmd_shop(self, group_id: str, qq: str) -> str:
        ap = self._get_player(group_id, qq, create=False)
        if not ap or not ap.get("activity_id"):
            return "❌ 你尚未相约中元。"
        return "🕯️ 功德商店暂未上架任何商品，敬请期待。"

    # ------------------------------------------------------------------
    # 管理指令
    # ------------------------------------------------------------------
    def _cmd_admin(self, group_id: str, qq: str, event, cmd: str, args: list[str]) -> str | None:
        if not self.bot._is_admin(event):
            return "❌ 仅管理员可操作中元活动后台。"
        if cmd in ("开启中元活动", "关闭中元活动"):
            self.cfg["enabled"] = cmd.startswith("开启")
            self._data["config"]["enabled"] = self.cfg["enabled"]
            return f"✅ 中元活动已**{'开启' if self.cfg['enabled'] else '关闭'}**（关闭即停全部玩法，数据保留）。"
        if cmd == "中元开始":
            self.cfg["start_at"] = self._now()
            self._data["config"]["start_at"] = self.cfg["start_at"]
            self._spawn(self._push_all_groups(
                f"## 🕯️ 中元活动已开启\n"
                f"《{ACTIVITY_NAME}》正式开启！全群共享功德数据，人人可参与。\n"
                f"> 发送「中元活动」查看玩法；「相约中元」领取活动 ID 踏入阴阳两界。"
            ))
            return f"✅ 中元活动已开始，并已向全群通报（start_at={self.cfg['start_at']}）。"
        if cmd == "中元结束":
            self.cfg["end_at"] = self._now()
            self._data["config"]["end_at"] = self.cfg["end_at"]
            self._settle()
            self._spawn(self._push_all_groups(
                f"## 🕯️ 中元活动已结束\n"
                f"《{ACTIVITY_NAME}》已落下帷幕，段位功德已结算完毕。\n"
                f"> 段位功德已结算，可发送「功德商店」查看奖励。"
            ))
            return f"✅ 中元活动已结束并结算，已向全群通报（end_at={self.cfg['end_at']}）。"
        if cmd in ("删除中元活动", "重置中元活动"):
            return self._cmd_reset()
        if cmd == "中元结算":
            self._settle()
            return "✅ 已重新结算段位功德。"
        if cmd == "中元配置":
            return self._cmd_config(args)
        return None

    def _cmd_config(self, args: list[str]) -> str:
        if not args:
            # 展示当前配置（关键项）
            keys = ["enabled", "start_at", "end_at", "open_hour", "close_hour",
                    "trigger_interval_min", "dungeon_limit_min", "puzzle_count", "no_response_sec",
                    "individual_fail_wrong", "pull_min_pct", "pull_max_pct",
                    "gongde_clear", "perfect_reward_mult",
                    "yin_penalty_min", "yin_clear_cost", "deepseek_model", "deepseek_enabled"]
            rows = []
            for k in keys:
                v = self.cfg.get(k, "")
                if k in ("start_at", "end_at") and v:
                    v = self._fmt_time(int(v))
                rows.append(f"> `{k}` = {v}")
            return "## ⚙️ 中元活动配置\n" + "\n".join(rows) + "\n> 改配置：`中元配置 <键> <值>`"
        key = args[0]
        if key not in editable_keys():
            return f"❌ `{key}` 不可热改（可改：{', '.join(sorted(editable_keys()))}）。"
        if len(args) < 2:
            return f"`{key}` = {self.cfg.get(key, '')}"
        raw = args[1]
        cur = self.cfg.get(key)
        if isinstance(cur, bool):
            self.cfg[key] = raw.lower() in ("true", "1", "yes", "on", "开启", "开")
        elif isinstance(cur, int):
            self.cfg[key] = int(raw)
        elif isinstance(cur, float):
            self.cfg[key] = float(raw)
        else:
            self.cfg[key] = raw
        self._data["config"][key] = self.cfg[key]
        return f"✅ 已设置 `{key}` = {self.cfg[key]}"

    def _cmd_reset(self) -> str:
        self.reset_data()
        return "✅ 中元活动数据已清空（代码与配置保留）。若要彻底下架，删除 `petpark/zhongyuan/` 目录及 main.py 中的接入钩子即可。"

    def reset_data(self) -> None:
        """清空活动数据（玩家 / 群 / 副本会话 + 活动 ID 序号），保留配置与代码。"""
        self._data["players"] = {}
        self._data["groups"] = {}
        self._data["sessions"] = {}
        self._data.setdefault("meta", {})["activity_id_seq"] = 0

    def _settle(self) -> None:
        """活动结算：按功德榜名次向前 20 名发放段位功德（入暂存）。"""
        self._data.setdefault("settled", False)
        if self._data.get("settled"):
            return
        # 全服（所有群）合并结算：段位按每个群的功德榜独立授予
        for gid in list(self._groups().keys()):
            ranked = sorted(
                self._players_in_group(gid),
                key=lambda p: (-int(p.get("gongde", 0)), -int(p.get("clear_count", 0))),
            )
            for rank, ap in enumerate(ranked[:20], start=1):
                g = tier_gongde_for_rank(rank, self.cfg.get("tiers", []))
                if g:
                    ap["escrow"] = int(ap.get("escrow", 0)) + g
        self._data["settled"] = True

    # ------------------------------------------------------------------
    # 指令分发
    # ------------------------------------------------------------------
    def dispatch(self, event, qq: str, group_id: str, text: str) -> str | None:
        """处理一条活动指令；命中即返回回复文本，否则返回 None。命中后异步落盘。"""
        result = self._dispatch(event, qq, group_id, text)
        if result is not None:
            self._spawn(self.save())
        return result

    def _dispatch(self, event, qq: str, group_id: str, text: str) -> str | None:
        tokens = text.split()
        cmd = tokens[0] if tokens else ""
        # 管理指令优先（可随时操作）
        if cmd in ("开启中元活动", "关闭中元活动", "中元开始", "中元结束",
                   "删除中元活动", "重置中元活动", "中元结算", "中元配置"):
            return self._cmd_admin(group_id, qq, event, cmd, tokens[1:])
        # 解密作答（仅被勾中玩家）
        if cmd in ("答", "中元答"):
            answer = text[len(cmd):].strip()
            if not answer:
                return "❌ 请以「答 <答案>」作答。"
            return self._check_answer(group_id, qq, answer)
        if cmd in ("中元活动", "中元活动介绍"):
            return self._cmd_menu(group_id)
        if cmd in ("中元状态", "我的中元"):
            return self._cmd_status(group_id, qq)
        if cmd in ("副本进度", "我的副本"):
            return self._cmd_dungeon_status(group_id, qq)
        if cmd == "相约中元":
            return self._bind_user(group_id, qq, event)
        if cmd in ("中元功德榜", "中元排行"):
            return self._cmd_rank(group_id)
        if cmd in ("中元里程碑", "功德里程碑"):
            return self._cmd_milestone(group_id)
        if cmd == "中元签到":
            return self._cmd_sign(group_id, qq)
        if cmd == "放河灯":
            message = text[len(cmd):].strip()
            return self._cmd_lantern(group_id, qq, event, message)
        if cmd == "中元问答":
            answer = text[len(cmd):].strip() or None
            return self._cmd_quiz(group_id, qq, answer)
        if cmd in ("焚香", "供灯"):
            return self._cmd_incense(group_id, qq, cmd)
        if cmd == "解除阴气":
            return self._cmd_clear_yin(group_id, qq)
        if cmd == "功德商店":
            return self._cmd_shop(group_id, qq)
        return None

    def _cmd_menu(self, group_id: str) -> str:
        state = "🟢 进行中" if self._enabled() else "🔴 未开启/已结束"
        return (
            f"## {ACTIVITY_NAME}\n"
            f"> 状态：{state}\n\n"
            "**🎭 阴面 · 幽影饲育馆**（协作解密 · 每日 8:00–22:00）\n"
            "> 相约中元 → 领取活动 ID → 每小时被勾入馆解谜 → 功德\n"
            "**🕯️ 阳面 · 青灯寄思**（文化温情 · 全天开放）\n"
            "> 放河灯 / 中元问答 / 供灯焚香 / 中元签到 → 功德\n\n"
            "**指令一览**\n"
            "`相约中元` · `中元状态` · `中元功德榜` · `中元里程碑` · `中元签到`\n"
            "`放河灯 <寄语>` · `中元问答` · `焚香`/`供灯` · `解除阴气` · `功德商店`\n"
            "> 中元不是鬼节，是勾连阴阳两界的思念。"
        )

    # ------------------------------------------------------------------
    # 后台循环
    # ------------------------------------------------------------------
    async def loop(self) -> None:
        await asyncio.sleep(3)  # 让框架先装配好
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                logger.exception("[zhongyuan] 后台循环异常")
            await asyncio.sleep(30)

    async def _tick(self) -> None:
        now = self._now()
        # 1. 解密时限 / 无响应判定
        for gid, s in list(self._sessions().items()):
            if now > s.get("deadline", 0):
                reply = self._fail_dungeon(gid, "超时")
                await self._push_group(gid, reply)
                continue
            if now - s.get("last_activity", now) > self._int_cfg("no_response_sec", 90):
                # 全体连续无响应：自动进入下一题（由 AI 推进）
                s["index"] += 1
                s["last_activity"] = now
                if s["index"] >= len(s["puzzles"]):
                    reply = self._finish_dungeon(gid)
                    await self._push_group(gid, reply)
                else:
                    await self._push_group(gid, "⏳ 长时间无人作答，已自动进入下一题。")
                    await self._push_puzzle(gid)
                continue
            # 周期性播报倒计时 / 答题进度 / 存活与淘汰
            if now - s.get("last_status_ts", 0) >= self._int_cfg("dungeon_status_interval_sec", 120):
                s["last_status_ts"] = now
                await self._push_dungeon_status(gid)
        # 2. 每小时抽人（活动开启 + 开放时段内）
        if self._enabled() and self._in_open_hours():
            for gid in list(self._groups().keys()):
                g = self._group_state(gid)
                if now >= g.get("next_trigger_ts", 0) and not self._session(gid):
                    if self._eligible_group(gid):
                        await self._start_dungeon(gid)
                    # 推进到下一个整点
                    g["next_trigger_ts"] = self._next_hour_ts(now)
        # 3. 活动结束自动结算
        if self._activity_over() and not self._data.get("settled"):
            self._settle()
        # 4. 落盘
        await self.save()

    def _eligible_group(self, group_id: str) -> bool:
        g = self.bot.store.get_group(group_id)
        if not g.get("enabled", True):
            return False
        if not self.bot._is_group_authorized(group_id):
            return False
        if not g.get("umo"):
            return False
        return True

    def _next_hour_ts(self, now: int) -> int:
        return ((now // 3600) + 1) * 3600

    # ------------------------------------------------------------------
    # 推送
    # ------------------------------------------------------------------
    async def _push_group(self, group_id: str, text: str) -> None:
        try:
            await self.bot._send_to_group(str(group_id), text)
        except Exception as e:  # noqa: BLE001
            logger.warning("[zhongyuan] 推送群 %s 失败：%s", group_id, e)

    async def _push_all_groups(self, text: str) -> None:
        """向所有已注册群广播（全群通报）；单群失败不影响其余群。"""
        try:
            gids = list(self.bot.store._data.get("groups", {}).keys())
        except Exception:  # noqa: BLE001
            gids = list(self._groups().keys())
        for gid in gids:
            await self._push_group(gid, text)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._loop_task_ref is None or self._loop_task_ref.done():
            self._loop_task_ref = asyncio.create_task(self.loop())

    async def terminate(self) -> None:
        if self._loop_task_ref is not None and not self._loop_task_ref.done():
            self._loop_task_ref.cancel()
        await asyncio.sleep(0)
        await self.save()
