"""宠物乐园数据持久化层。

所有玩家数据保存在 AstrBot 的 data 目录下（而非插件目录），避免更新/重装插件时被覆盖。
数据结构（单个 JSON 文件）::

    {
      "players": { "<qq>": {player...} },
      "groups":  { "<group_id>": {"enabled": bool, "cross": bool} },
      "rune_bag": {...}            # 预留
    }

player 结构::

    {
      "qq": "123",
      "coin": 0, "jifen": 0,
      "bag": { "红药水": 3, ... },
      "pet": {pet...} | None,
      "last_actions": { "打工": 时间戳, ... }
    }
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from pathlib import Path
from typing import Any, Optional


class PetStore:
    def __init__(
        self,
        data_path: Path,
        start_coin: int = 1000,
        start_jifen: int = 0,
        start_diamond: int = 0,
        default_enabled: bool = True,
        default_cross: bool = True,
    ):
        self.path = data_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.start_coin = start_coin
        self.start_jifen = start_jifen
        self.start_diamond = start_diamond
        self.default_enabled = default_enabled
        self.default_cross = default_cross
        self._lock = asyncio.Lock()
        self._data: dict[str, Any] = {"players": {}, "groups": {}}
        self._load()

    # ----------------------------- 基础读写 -----------------------------
    def _load(self) -> None:
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {"players": {}, "groups": {}}
        self._data.setdefault("players", {})
        self._data.setdefault("groups", {})
        self._data.setdefault("cards", {})
        self._migrate_group_keys()

    @staticmethod
    def make_key(group_id: str, qq: str) -> str:
        """玩家在某个群内的唯一键：群ID + 用户ID（数据按群隔离）。"""
        return f"{group_id}\x1f{qq}"

    def _migrate_group_keys(self) -> None:
        """把旧版（按 QQ 全局保存）的玩家数据迁移为按『群ID+用户ID』隔离。"""
        players = self._data["players"]
        migrated: dict[str, Any] = {}
        changed = False
        for key, pl in list(players.items()):
            if "\x1f" in key:
                migrated[key] = pl
                continue
            changed = True
            gid = str(pl.get("group") or "private")
            qq = str(pl.get("qq") or key)
            pl.setdefault("qq", qq)
            pl["group"] = gid
            migrated[self.make_key(gid, qq)] = pl
        if changed:
            self._data["players"] = migrated

    def _flush(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.path)

    async def save(self) -> None:
        async with self._lock:
            self._flush()

    # ----------------------------- 玩家 -----------------------------
    def get_player(
        self, qq: str, group_id: str, create: bool = True
    ) -> Optional[dict]:
        """取某群内的玩家数据（按群隔离）。create=False 时不存在返回 None。"""
        qq = str(qq)
        group_id = str(group_id)
        key = self.make_key(group_id, qq)
        players = self._data["players"]
        if key not in players and create:
            players[key] = {
                "qq": qq,
                "group": group_id,
                "coin": self.start_coin,
                "jifen": self.start_jifen,
                "diamond": self.start_diamond,
                "bag": {},
                "pet": None,
                "last_actions": {},
                "stats": {"battle_win": 0, "explore": 0},
                "quests": {},
            }
        return players.get(key)

    def all_players(self) -> dict[str, dict]:
        """全服所有玩家（键为 群ID+用户ID）。用于跨群神榜。"""
        return self._data["players"]

    def players_in_group(self, group_id: str) -> dict[str, dict]:
        """某个群内的全部玩家。"""
        group_id = str(group_id)
        prefix = self.make_key(group_id, "")
        return {
            k: v for k, v in self._data["players"].items() if k.startswith(prefix)
        }

    # ----------------------------- 群设置 -----------------------------
    def get_group(self, group_id: str) -> dict:
        group_id = str(group_id)
        groups = self._data["groups"]
        if group_id not in groups:
            groups[group_id] = {
                "enabled": self.default_enabled,
                "cross": self.default_cross,
            }
        return groups[group_id]

    def next_sign_order(self, group_id: str, date_str: str) -> int:
        """记录并返回今天本群第几位签到（每天从 1 开始）。"""
        group = self.get_group(group_id)
        if group.get("sign_day") != date_str:
            group["sign_day"] = date_str
            group["sign_count"] = 0
        group["sign_count"] = int(group.get("sign_count", 0)) + 1
        return group["sign_count"]

    # ----------------------------- 货币 / 背包 -----------------------------
    CURRENCY_KEYS = {"金币": "coin", "积分": "jifen", "钻石": "diamond"}

    @classmethod
    def currency_key(cls, currency: str) -> str:
        return cls.CURRENCY_KEYS.get(currency, "coin")

    @classmethod
    def add_currency(cls, player: dict, currency: str, amount: int) -> None:
        key = cls.currency_key(currency)
        player[key] = max(0, player.get(key, 0) + amount)

    @classmethod
    def get_currency(cls, player: dict, currency: str) -> int:
        key = cls.currency_key(currency)
        return player.get(key, 0)

    @staticmethod
    def add_item(player: dict, name: str, count: int = 1) -> None:
        bag = player.setdefault("bag", {})
        bag[name] = bag.get(name, 0) + count
        if bag[name] <= 0:
            bag.pop(name, None)

    @staticmethod
    def remove_item(player: dict, name: str, count: int = 1) -> bool:
        bag = player.setdefault("bag", {})
        if bag.get(name, 0) < count:
            return False
        bag[name] -= count
        if bag[name] <= 0:
            bag.pop(name, None)
        return True

    @staticmethod
    def has_item(player: dict, name: str, count: int = 1) -> bool:
        return player.get("bag", {}).get(name, 0) >= count

    # ----------------------------- 卡密 -----------------------------
    CARD_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 去掉易混淆的 0/O/1/I

    def cards(self) -> dict:
        return self._data.setdefault("cards", {})

    def gen_card_code(self, prefix: str = "") -> str:
        cards = self.cards()
        prefix = "".join(c for c in str(prefix).upper() if c.isalnum())
        while True:
            body = "".join(random.choice(self.CARD_CHARS) for _ in range(16))
            code = f"{prefix}{body}" if prefix else body
            if code not in cards:
                return code

    @classmethod
    def normalize_rewards(cls, rewards: dict) -> dict:
        """清洗套餐奖励：仅保留 金币/积分/钻石 中数额 > 0 的项。"""
        out: dict[str, int] = {}
        for cur, amt in (rewards or {}).items():
            if cur in cls.CURRENCY_KEYS:
                try:
                    n = int(amt)
                except (TypeError, ValueError):
                    continue
                if n > 0:
                    out[cur] = n
        return out

    @staticmethod
    def card_rewards(card: dict) -> dict:
        """读取卡密奖励，兼容旧版单一货币格式 {currency, amount}。"""
        if isinstance(card.get("rewards"), dict):
            return {k: int(v) for k, v in card["rewards"].items() if int(v) > 0}
        cur = card.get("currency")
        amt = int(card.get("amount", 0) or 0)
        return {cur: amt} if cur and amt > 0 else {}

    def create_cards(
        self, currency: str, amount: int, count: int = 1, prefix: str = ""
    ) -> list[str]:
        """批量生成单一货币卡密（向后兼容）。currency ∈ {金币, 积分, 钻石}。"""
        if currency not in self.CURRENCY_KEYS:
            raise ValueError("货币类型必须为 金币 / 积分 / 钻石")
        return self.create_combo_cards({currency: int(amount)}, count, prefix)

    def create_combo_cards(
        self, rewards: dict, count: int = 1, prefix: str = ""
    ) -> list[str]:
        """批量生成套餐卡密：一张卡密可同时含 金币/积分/钻石 多种奖励。"""
        rewards = self.normalize_rewards(rewards)
        if not rewards:
            raise ValueError("请至少为 金币 / 积分 / 钻石 中的一项填写正数面额")
        count = max(1, int(count))
        cards = self.cards()
        created: list[str] = []
        now = int(time.time())
        for _ in range(count):
            code = self.gen_card_code(prefix)
            cards[code] = {
                "rewards": dict(rewards),
                "used": False,
                "used_by": None,
                "used_at": None,
                "created_at": now,
            }
            created.append(code)
        return created

    def redeem_card(self, code: str, player: dict, used_by: str):
        """兑换卡密：成功返回 (rewards字典, None)，失败返回 (None, 原因)。"""
        code = str(code).strip().upper()
        cards = self.cards()
        card = cards.get(code)
        if card is None:
            return None, "卡密不存在或输入有误"
        if int(card.get("auth_days", 0) or 0) > 0:
            return None, "这是群授权卡，请用『授权 卡密』兑换"
        if card.get("used"):
            return None, "该卡密已被使用"
        rewards = self.card_rewards(card)
        if not rewards:
            return None, "该卡密无有效奖励"
        card["used"] = True
        card["used_by"] = used_by
        card["used_at"] = int(time.time())
        for cur, amt in rewards.items():
            self.add_currency(player, cur, int(amt))
        return rewards, None

    def create_auth_cards(
        self, days: int, count: int = 1, prefix: str = ""
    ) -> list[str]:
        """批量生成群授权卡：兑换后为所在群延长 days 天授权时长。"""
        days = int(days)
        if days <= 0:
            raise ValueError("授权天数必须为正整数")
        count = max(1, int(count))
        cards = self.cards()
        created: list[str] = []
        now = int(time.time())
        for _ in range(count):
            code = self.gen_card_code(prefix)
            cards[code] = {
                "auth_days": days,
                "used": False,
                "used_by": None,
                "used_at": None,
                "created_at": now,
            }
            created.append(code)
        return created

    def redeem_auth_card(self, code: str, used_by: str):
        """兑换群授权卡：成功返回 (天数, None)，失败返回 (None, 原因)。"""
        code = str(code).strip().upper()
        cards = self.cards()
        card = cards.get(code)
        if card is None:
            return None, "卡密不存在或输入有误"
        days = int(card.get("auth_days", 0) or 0)
        if days <= 0:
            return None, "这不是群授权卡（货币卡请用『兑换 卡密』）"
        if card.get("used"):
            return None, "该授权卡已被使用"
        card["used"] = True
        card["used_by"] = used_by
        card["used_at"] = int(time.time())
        return days, None

    # ----------------------------- 冷却 -----------------------------
    @staticmethod
    def touch_action(player: dict, action: str) -> None:
        player.setdefault("last_actions", {})[action] = int(time.time())

    @staticmethod
    def last_action_ts(player: dict, action: str) -> int:
        return player.get("last_actions", {}).get(action, 0)

    @staticmethod
    def set_cooldown(player: dict, key: str, seconds: int) -> None:
        player.setdefault("cooldowns", {})[key] = int(time.time()) + int(seconds)

    @staticmethod
    def cooldown_remaining(player: dict, key: str) -> int:
        end = player.get("cooldowns", {}).get(key, 0)
        return max(0, int(end) - int(time.time()))

    # ----------------------------- 活动 -----------------------------
    def events(self) -> dict:
        return self._data.setdefault("events", {})

    def active_events(self, now: int | None = None) -> dict[str, dict]:
        """返回当前生效且 enabled 的活动 {id: config}。"""
        now = now or int(time.time())
        out = {}
        for eid, cfg in self.events().items():
            if not cfg.get("enabled"):
                continue
            if cfg.get("start_at", 0) <= now <= cfg.get("end_at", 0):
                out[eid] = cfg
        return out

    @staticmethod
    def player_event_state(player: dict, event_id: str) -> dict:
        st = player.setdefault("event_state", {}).setdefault(event_id, {})
        st.setdefault("tokens", {})
        st.setdefault("daily_counts", {})
        st.setdefault("daily_reset", "")
        st.setdefault("shop_bought", {})
        st.setdefault("progress", {})
        return st

    @staticmethod
    def add_event_token(player: dict, event_id: str, token: str, amount: int) -> None:
        st = PetStore.player_event_state(player, event_id)
        st["tokens"][token] = max(0, st["tokens"].get(token, 0) + amount)

    @staticmethod
    def get_event_token(player: dict, event_id: str, token: str) -> int:
        return PetStore.player_event_state(player, event_id)["tokens"].get(token, 0)

    @staticmethod
    def reset_event_daily(player: dict, event_id: str, date_str: str) -> None:
        st = PetStore.player_event_state(player, event_id)
        if st.get("daily_reset") != date_str:
            st["daily_counts"] = {}
            st["shop_bought"] = {}
            st["daily_reset"] = date_str

    @staticmethod
    def event_daily_count(player: dict, event_id: str, action: str) -> int:
        return PetStore.player_event_state(player, event_id)["daily_counts"].get(action, 0)

    @staticmethod
    def inc_event_daily(player: dict, event_id: str, action: str) -> None:
        st = PetStore.player_event_state(player, event_id)
        st["daily_counts"][action] = st["daily_counts"].get(action, 0) + 1

    @staticmethod
    def event_shop_bought(player: dict, event_id: str, item: str) -> int:
        return PetStore.player_event_state(player, event_id)["shop_bought"].get(item, 0)

    @staticmethod
    def inc_event_shop_bought(player: dict, event_id: str, item: str) -> None:
        st = PetStore.player_event_state(player, event_id)
        st["shop_bought"][item] = st["shop_bought"].get(item, 0) + 1

    # ----------------------------- 邀请 -----------------------------
    @staticmethod
    def record_invite(inviter: dict, invitee: dict) -> None:
        """记录一次成功邀请：inviter 的 invited_users 列表增加 invitee，invitee 标记 invited_by。"""
        invitee["invited_by"] = str(inviter.get("qq", ""))
        inviter.setdefault("invited_users", []).append(
            {"qq": str(invitee.get("qq", "")), "at": int(time.time())}
        )

    @staticmethod
    def get_invited_users(player: dict) -> list[dict]:
        return player.get("invited_users", [])

    @staticmethod
    def invited_by(player: dict) -> str | None:
        return player.get("invited_by")

    @staticmethod
    def is_already_invited_by(inviter: dict, invitee_qq: str) -> bool:
        for entry in inviter.get("invited_users", []):
            if str(entry.get("qq", "")) == str(invitee_qq):
                return True
        return False
