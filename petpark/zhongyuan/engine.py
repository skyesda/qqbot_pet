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
    "绑定中元宠物", "中元绑定", "中元功德榜", "中元排行", "中元签到",
    "放河灯", "中元问答", "焚香", "供灯", "答", "中元答", "解除阴气", "中元兑换",
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
        return f"{group_id}\x1f{qq}"

    def _get_player(self, group_id: str, qq: str, create: bool = False) -> dict | None:
        players = self._players()
        key = self._key(group_id, qq)
        if key not in players and create:
            players[key] = {
                "qq": str(qq),
                "group": str(group_id),
                "activity_id": 0,
                "pet_id": "",
                "pet_name": "",
                "pet_level": 1,
                "gongde": 0,
                "clear_count": 0,
                "perfect_count": 0,
                "fail_count": 0,
                "best_time_sec": 0,
                "bound_at": self._now(),
                "yin_until": 0,
                "yin_fail_today": 0,
                "yin_fail_date": "",
                "last_draw_date": "",
                "draw_count_today": 0,
                "escrow": 0,
                "daily": {"date": "", "lantern": 0, "quiz": 0, "incense": 0, "sign": 0},
                "quiz": {},
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
    # 绑定宠物（绑定后不可换、不可解）
    # ------------------------------------------------------------------
    def _bind_pet(self, group_id: str, qq: str, index: int | None = None) -> str:
        if not self._enabled():
            return f"❌ {ACTIVITY_TAG} 尚未开启或已结束。"
        tp = self.bot.store.get_player(qq, group_id, create=False)
        if not tp or not tp.get("pets"):
            return "❌ 你尚未在本群拥有任何宠物，无法绑定（先去「砸蛋」抽一只吧）。"
        pets = tp["pets"]
        if index is None:
            index = tp.get("active_pet", 0)
            if index < 0 or index >= len(pets):
                index = 0
        else:
            index -= 1  # 用户按 1 起序号
            if index < 0 or index >= len(pets):
                return f"❌ 序号超出范围，你共有 {len(pets)} 只宠物。"
        pet = pets[index]
        ap = self._get_player(group_id, qq, create=True)
        if ap.get("activity_id"):
            return (
                f"❌ 你已绑定宠物「{ap.get('pet_name')}」（活动 ID #{ap['activity_id']:04d}），"
                "活动期间不可换绑、不可解绑。"
            )
        ap["pet_id"] = pet.get("pet_id", "")
        ap["pet_name"] = pet.get("nickname", pet.get("species", "?"))
        ap["pet_level"] = int(pet.get("level", 1))
        ap["activity_id"] = self._alloc_activity_id()
        # 注册本群状态，使其进入每小时抽人的候选群
        self._group_state(group_id)
        return (
            f"🕯️ 绑定成功！你已将群宠物 **{ap['pet_name']}** 托付给中元之夜。\n"
            f"> 你的活动 ID：**#{ap['activity_id']:04d}**\n"
            "> ⚠️ 绑定后不可放生、赠送或换绑；解不开馆里的规矩，你与它，就都留下吧。"
        )

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

    def _group_add_gongde(self, group_id: str, amount: int) -> None:
        g = self._group_state(group_id)
        g["gongde_total"] = int(g.get("gongde_total", 0)) + max(0, int(amount))
        self._check_milestones(group_id)

    def _check_milestones(self, group_id: str) -> list[str]:
        """群累计功德达标时，向全群每位已绑定玩家发放共享功德（入暂存），返回新达成的档位。"""
        g = self._group_state(group_id)
        total = int(g.get("gongde_total", 0))
        reached = set(g.get("milestone_reached", []))
        newly = []
        for i, ms in enumerate(self.cfg.get("milestones", [])):
            if i in reached:
                continue
            if total >= int(ms.get("threshold", 0)):
                reached.add(i)
                newly.append(f"{int(ms['threshold'])}（+{int(ms.get('gongde', 0))} 功德）")
                # 全员各得一份，入暂存
                for ap in self._players_in_group(group_id):
                    ap["escrow"] = int(ap.get("escrow", 0)) + int(ms.get("gongde", 0))
        g["milestone_reached"] = sorted(reached)
        if newly:
            self._spawn(self._push_group(
                group_id,
                f"## 🎊 全群里程碑达成\n群累计功德突破 **{newly[-1]}**，"
                f"全群每位已绑定驯宠师共享功德已入「暂存」，活动结束后兑换！",
            ))
        return newly

    def _players_in_group(self, group_id: str) -> list[dict]:
        prefix = self._key(group_id, "")
        return [v for k, v in self._players().items() if k.startswith(prefix)]

    def _cmd_lantern(self, group_id: str, qq: str, event, message: str) -> str:
        if not self._enabled() or not self._in_open_hours():
            return f"❌ 当前不在中元活动开放时段。"
        ap = self._get_player(group_id, qq, create=True)
        if not ap.get("activity_id"):
            return "❌ 请先「绑定中元宠物」领取活动 ID 后再放河灯。"
        d = self._daily_reset(ap)
        if d["lantern"] >= 1:
            return "❌ 今日已放过河灯，每日限一盏。"
        d["lantern"] += 1
        reward = self._int_cfg("gongde_lantern", 10)
        self._add_gongde(ap, reward)
        self._group_add_gongde(group_id, reward)
        msg = (message or "").strip() or "愿逝者安息，愿生者珍重"
        echo = random.choice(_LANTERN_ECHOES)
        # DeepSeek 温情回文（后台异步推送，不阻塞）
        self._spawn(self._lantern_echo(group_id, ap.get("pet_name", ""), msg))
        return (
            f"🕯️ 你点亮一盏河灯，写下：\n> 「{msg}」\n"
            f"灯随水流去，思念寄远乡。\n> **{echo}**\n"
            f"功德 **+{reward}**（当前 {ap['gongde']}）"
        )

    async def _lantern_echo(self, group_id: str, pet_name: str, message: str) -> None:
        if not self._deepseek.available:
            return
        system = "你是中元节的摆渡人，为玩家的思念寄语写一句温婉、克制、治愈的中式回文，不超过 40 字。"
        echo = await self._deepseek.reply_echo(f"玩家给思念之人的寄语：{message}", system=system)
        if echo:
            await self._push_group(group_id, f"💫 青灯回音：{echo.strip()}")

    def _cmd_quiz(self, group_id: str, qq: str, answer: str | None) -> str:
        if not self._enabled() or not self._in_open_hours():
            return f"❌ 当前不在中元活动开放时段。"
        ap = self._get_player(group_id, qq, create=True)
        if not ap.get("activity_id"):
            return "❌ 请先「绑定中元宠物」领取活动 ID 后再参与问答。"
        d = self._daily_reset(ap)
        if answer is None:
            q = random.choice(_CULTURE_QUIZ)
            ap["quiz"] = {"q": q["q"], "a": q["a"], "date": self._bj_date()}
            return (
                f"🕯️ 中元知多少 · 文化问答\n> {q['q']}\n"
                f"发送「中元问答 <你的答案>」作答，答对 +{self._int_cfg('gongde_quiz', 10)} 功德。"
            )
        quiz = ap.get("quiz", {})
        if not quiz or quiz.get("date") != self._bj_date():
            return "❌ 请先发送「中元问答」领取今日题目。"
        if d["quiz"] >= 1:
            return "❌ 今日已答过题。"
        if answer.strip() == quiz["a"]:
            d["quiz"] += 1
            reward = self._int_cfg("gongde_quiz", 10)
            self._add_gongde(ap, reward)
            self._group_add_gongde(group_id, reward)
            return f"✅ 答对了！功德 **+{reward}**（当前 {ap['gongde']}）。中元不是鬼节，是勾连阴阳的思念。"
        return f"❌ 不太对。提示：{quiz.get('q', '')}（可再答，直到答对；今日限一次奖励）"

    def _cmd_incense(self, group_id: str, qq: str, kind: str) -> str:
        if not self._enabled() or not self._in_open_hours():
            return f"❌ 当前不在中元活动开放时段。"
        ap = self._get_player(group_id, qq, create=True)
        if not ap.get("activity_id"):
            return "❌ 请先「绑定中元宠物」领取活动 ID 后再祭祖。"
        d = self._daily_reset(ap)
        if d["incense"] >= 1:
            return "❌ 今日已「供灯/焚香」过，每日限一次。"
        d["incense"] += 1
        reward = self._int_cfg("gongde_incense", 10)
        self._add_gongde(ap, reward)
        self._group_add_gongde(group_id, reward)
        act = "焚三炷香" if kind == "焚香" else "供一盏灯"
        return (
            f"🕯️ 你{act}，一敬天、二敬地、三敬祖先。\n"
            f"> 香火相传，追思绵长。功德 **+{reward}**（当前 {ap['gongde']}）。"
        )

    def _cmd_sign(self, group_id: str, qq: str) -> str:
        if not self._enabled():
            return f"❌ 活动未开启。"
        ap = self._get_player(group_id, qq, create=True)
        if not ap.get("activity_id"):
            return "❌ 请先「绑定中元宠物」领取活动 ID 后再签到。"
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
        """为一个群抽取一名玩家并生成谜题、开始解密。"""
        gid = str(group_id)
        g = self._group_state(gid)
        today = self._bj_date()
        # 本群可被抽取的已绑定玩家（排除当日已到上限者）
        bound = [
            ap for ap in self._players_in_group(gid)
            if ap.get("activity_id")
            and not (ap.get("last_draw_date") == today
                     and int(ap.get("draw_count_today", 0)) >= self._int_cfg("max_draw_per_day", 2))
        ]
        if not bound:
            return
        ap = random.choice(bound)
        pet = self._resolve_pet(ap)
        if pet is None:
            # 宠物已被放生/丢失，跳过并提示
            self._spawn(self._push_group(
                gid, f"⚠️ 编号 #{ap['activity_id']:04d} 的绑定宠物已不在，无法拉入副本。"))
            return
        ap["last_draw_date"] = today
        ap["draw_count_today"] = int(ap.get("draw_count_today", 0)) + 1
        # 生成谜题
        puzzles_list = await self._generate_puzzles()
        session = {
            "qq": ap["qq"],
            "activity_id": ap["activity_id"],
            "pet_name": ap.get("pet_name", "?"),
            "puzzles": puzzles_list,
            "index": 0,
            "yin": 0,
            "perfect": True,
            "started_at": self._now(),
            "deadline": self._now() + self._int_cfg("dungeon_limit_min", 30) * 60,
            "last_activity": self._now(),
        }
        self._sessions()[gid] = session
        await self._push_group(
            gid,
            f"## 🚪 阴门开 · 诡异副本降临\n"
            f"【{self._bj_hour():02d}时 阴门开】编号 **#{ap['activity_id']:04d}** 的驯宠师，"
            f"其宠物 **{ap.get('pet_name')}** 已被勾入「幽影饲育馆」。\n"
            f"> 全群围观见证，被勾者请在 {self._int_cfg('dungeon_limit_min', 30)} 分钟内解完 "
            f"{self._int_cfg('puzzle_count', 3)} 道谜题。\n"
            f"> 以「答 <答案>」作答；答错积阴气，阴气满格（{self._int_cfg('yin_max', 3)} 层）即败。",
        )
        await self._push_puzzle(gid)

    async def _generate_puzzles(self) -> list[dict]:
        """生成单场谜题：优先 DeepSeek，失败/关闭回退本地模板。"""
        count = max(1, self._int_cfg("puzzle_count", 3))
        out = []
        if self._deepseek.available:
            themes = random.sample(puzzles.THEMES, k=min(count, len(puzzles.THEMES)))
            results = await asyncio.gather(
                *(self._deepseek.generate_puzzle(t) for t in themes),
                return_exceptions=True,
            )
            for theme, p in zip(themes, results):
                if isinstance(p, dict) and p:
                    out.append(p)
                else:
                    out.append(puzzles.local_puzzle(theme))
        else:
            for _ in range(count):
                out.append(puzzles.local_puzzle())
        # 不足则补齐
        while len(out) < count:
            out.append(puzzles.local_puzzle())
        return out[:count]

    async def _push_puzzle(self, group_id: str) -> None:
        s = self._session(group_id)
        if not s:
            return
        p = s["puzzles"][s["index"]]
        opts = "\n".join(f"{i + 1}. {o}" for i, o in enumerate(p.get("options", [])))
        await self._push_group(
            group_id,
            f"## 🕯️ 第 {s['index'] + 1}/{len(s['puzzles'])} 题 · {p.get('theme', '')}\n"
            f"> {p.get('question')}\n\n{opts}\n"
            f"> 以「答 <答案>」作答。",
        )

    def _check_answer(self, group_id: str, qq: str, text: str) -> str | None:
        """被勾中的玩家作答；返回回复文本（异步推送后续题/结算在 loop 中处理）。"""
        s = self._session(group_id)
        if not s:
            return None
        if str(s.get("qq")) != str(qq):
            return None  # 非被勾中玩家，忽略
        if self._now() > s.get("deadline", 0):
            return "⏰ 本场解密已超时。"
        p = s["puzzles"][s["index"]]
        if puzzles.is_correct(text, p):
            s["index"] += 1
            s["last_activity"] = self._now()
            if s["index"] >= len(s["puzzles"]):
                return self._finish_dungeon(group_id)
            # 推进下一题（异步推送）
            self._spawn(self._push_puzzle(group_id))
            return f"✅ 答对。阴气 {s['yin']}/{self._int_cfg('yin_max', 3)}。"
        else:
            s["yin"] += 1
            s["perfect"] = False
            s["last_activity"] = self._now()
            if s["yin"] >= self._int_cfg("yin_max", 3):
                return self._fail_dungeon(group_id, "阴气满格")
            return (
                f"❌ 答错。阴气 +1（{s['yin']}/{self._int_cfg('yin_max', 3)}）。\n"
                f"> 提示：{p.get('hint', '') or '再想想。'}"
            )

    def _finish_dungeon(self, group_id: str) -> str:
        s = self._sessions().pop(group_id, None)
        ap = self._get_player(group_id, s["qq"])
        if ap is None:
            return "✅ 通关。"
        ap["clear_count"] = int(ap.get("clear_count", 0)) + 1
        elapsed = self._now() - s["started_at"]
        if not ap.get("best_time_sec") or elapsed < ap["best_time_sec"]:
            ap["best_time_sec"] = elapsed
        perfect = bool(s.get("perfect"))
        reward = self._int_cfg("gongde_clear", 300)
        if perfect:
            ap["perfect_count"] = int(ap.get("perfect_count", 0)) + 1
            reward += self._int_cfg("gongde_perfect", 200)
        self._add_gongde(ap, reward)
        self._group_add_gongde(group_id, reward)
        tag = "✨ 完美通关" if perfect else "🎉 通关"
        return (
            f"{tag}！编号 #{s['activity_id']:04d} 解开了《规矩簿》，用时 {elapsed // 60} 分 {elapsed % 60} 秒。\n"
            f"> 功德 **+{reward}**（当前 {ap['gongde']}）。"
        )

    def _fail_dungeon(self, group_id: str, reason: str) -> str:
        s = self._sessions().pop(group_id, None)
        ap = self._get_player(group_id, s["qq"])
        if ap is None:
            return f"❌ 失败（{reason}）。"
        ap["fail_count"] = int(ap.get("fail_count", 0)) + 1
        # 失败安慰功德
        reward = self._int_cfg("gongde_fail", 20)
        self._add_gongde(ap, reward)
        self._group_add_gongde(group_id, reward)
        # 阴气缠身 debuff（至次日 8:00）
        ap["yin_until"] = self._next_open_ts()
        # 记录当日失败次数（用于解除折扣）
        today = self._bj_date()
        if ap.get("yin_fail_date") != today:
            ap["yin_fail_date"] = today
            ap["yin_fail_today"] = 0
        ap["yin_fail_today"] = int(ap.get("yin_fail_today", 0)) + 1
        return (
            f"❌ 失败（{reason}）。编号 #{s['activity_id']:04d} 的宠物被「阴气缠身」"
            f"（至次日 8:00）。\n> 安慰功德 **+{reward}**（当前 {ap['gongde']}）。"
        )

    def _resolve_pet(self, ap: dict) -> dict | None:
        tp = self.bot.store.get_player(ap.get("qq"), ap.get("group"), create=False)
        if not tp:
            return None
        for p in tp.get("pets", []):
            if p.get("pet_id") == ap.get("pet_id"):
                return p
        return None

    def _next_open_ts(self) -> int:
        now = self._now_bj()
        open_hour = self._int_cfg("open_hour", 8)
        target = now.replace(hour=open_hour, minute=0, second=0, microsecond=0)
        if target <= now:
            from datetime import timedelta
            target += timedelta(days=1)
        return int(target.timestamp())

    # ------------------------------------------------------------------
    # 解除阴气
    # ------------------------------------------------------------------
    def _cmd_clear_yin(self, group_id: str, qq: str) -> str:
        ap = self._get_player(group_id, qq, create=False)
        if not ap or not ap.get("activity_id"):
            return "❌ 你尚未绑定中元宠物。"
        until = int(ap.get("yin_until", 0))
        if until <= self._now():
            return "✅ 你当前并无「阴气缠身」。"
        cost = self._int_cfg("yin_clear_cost", 300)
        # 单日两次失败折扣
        if int(ap.get("yin_fail_today", 0)) >= 2:
            cost = int(cost * float(self._cfg("yin_clear_discount", 0.7)))
        if int(ap.get("gongde", 0)) < cost:
            return f"❌ 功德不足，快速解除需 {cost} 功德（当前 {ap['gongde']}）；或等次日 8:00 自然解除。"
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
        lines.append("| 排名 | 段位 | 活动ID | 宠物 | 功德 | 通关 | 完美 |")
        lines.append("|:--:|:--:|:--:|:--:|--:|--:|--:|")
        for i, p in enumerate(ranked[:20], start=1):
            tier = tier_name_for_rank(i, self.cfg.get("tiers", [])) or "—"
            name = str(p.get("pet_name", "?")).replace("|", "丨")
            rk = medals.get(i, str(i))
            lines.append(
                f"| {rk} | {tier} | #{p['activity_id']:04d} | {name} | "
                f"{p.get('gongde', 0)} | {p.get('clear_count', 0)} | {p.get('perfect_count', 0)} |"
            )
        return "\n".join(lines)

    def _cmd_status(self, group_id: str, qq: str) -> str:
        ap = self._get_player(group_id, qq, create=False)
        if not ap or not ap.get("activity_id"):
            return (
                "🕯️ 你尚未绑定中元宠物。\n"
                "> 发送「绑定中元宠物」领取活动 ID，踏入阴阳两界。"
            )
        yin = "🈳 无" if int(ap.get("yin_until", 0)) <= self._now() else f"⚠️ 阴气缠身（{self._fmt_remain(int(ap['yin_until']))}）"
        return (
            f"## 🕯️ 我的中元\n"
            f"> 活动 ID：**#{ap['activity_id']:04d}**\n"
            f"> 绑定宠物：**{ap.get('pet_name', '?')}**（Lv.{ap.get('pet_level', 1)}）\n"
            f"> 功德：**{ap.get('gongde', 0)}**\n"
            f"> 暂存功德：{ap.get('escrow', 0)}\n"
            f"> 通关 {ap.get('clear_count', 0)} 次 · 完美 {ap.get('perfect_count', 0)} 次 · 失败 {ap.get('fail_count', 0)} 次\n"
            f"> 状态：{yin}"
        )

    def _cmd_redeem(self, group_id: str, qq: str) -> str:
        ap = self._get_player(group_id, qq, create=False)
        if not ap or not ap.get("activity_id"):
            return "❌ 你尚未绑定中元宠物。"
        if not self._activity_over():
            return "❌ 活动尚未结束，暂存功德需等活动结算后兑换。"
        if not self._redeem_open():
            return "❌ 兑换窗口已关闭。"
        escrow = int(ap.get("escrow", 0))
        if escrow <= 0:
            return "✅ 你暂无待兑换的暂存功德。"
        ap["gongde"] = int(ap.get("gongde", 0)) + escrow
        ap["escrow"] = 0
        return f"✅ 已兑换 **{escrow}** 暂存功德给绑定宠物「{ap.get('pet_name')}」！最终功德 {ap['gongde']}。"

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
            return f"✅ 中元活动已开始（start_at={self.cfg['start_at']}）。"
        if cmd == "中元结束":
            self.cfg["end_at"] = self._now()
            self._data["config"]["end_at"] = self.cfg["end_at"]
            self._settle()
            return f"✅ 中元活动已结束并结算（end_at={self.cfg['end_at']}）。"
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
                    "trigger_interval_min", "dungeon_limit_min", "puzzle_count", "yin_max",
                    "gongde_clear", "gongde_perfect", "gongde_fail", "deepseek_model",
                    "deepseek_enabled"]
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
        self._data["players"] = {}
        self._data["groups"] = {}
        self._data["sessions"] = {}
        self._data["meta"]["activity_id_seq"] = 0
        return "✅ 中元活动数据已清空（代码与配置保留）。若要彻底下架，删除 `petpark/zhongyuan/` 目录及 main.py 中的接入钩子即可。"

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
        if cmd in ("绑定中元宠物", "中元绑定"):
            index = None
            if len(tokens) > 1 and tokens[1].isdigit():
                index = int(tokens[1])
            return self._bind_pet(group_id, qq, index)
        if cmd in ("中元功德榜", "中元排行"):
            return self._cmd_rank(group_id)
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
        if cmd == "中元兑换":
            return self._cmd_redeem(group_id, qq)
        return None

    def _cmd_menu(self, group_id: str) -> str:
        state = "🟢 进行中" if self._enabled() else "🔴 未开启/已结束"
        return (
            f"## {ACTIVITY_NAME}\n"
            f"> 状态：{state}\n\n"
            "**🎭 阴面 · 幽影饲育馆**（恐怖解密）\n"
            "> 绑定宠物 → 领取活动 ID → 每小时被勾入馆解谜 → 功德\n"
            "**🕯️ 阳面 · 青灯寄思**（文化温情）\n"
            "> 放河灯 / 中元问答 / 供灯焚香 / 中元签到 → 功德\n\n"
            "**指令一览**\n"
            "`绑定中元宠物` · `中元状态` · `中元功德榜` · `中元签到`\n"
            "`放河灯 <寄语>` · `中元问答` · `焚香`/`供灯` · `解除阴气` · `中元兑换`\n"
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
            elif now - s.get("last_activity", now) > self._int_cfg("no_response_sec", 90):
                reply = self._fail_dungeon(gid, "连续无响应")
                await self._push_group(gid, reply)
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
