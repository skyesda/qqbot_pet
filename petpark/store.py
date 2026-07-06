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
import secrets
import time
from pathlib import Path
from typing import Any, Optional

from . import images

from . import data


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
        self.custom_images_dir = self.path.parent / "custom_images"
        self.custom_images_dir.mkdir(parents=True, exist_ok=True)
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
        self._data.setdefault("events", {})
        self._data.setdefault("accounts", {})
        self._data.setdefault("custom_reviews", {})
        self._data.setdefault("portal_secret", "".join(random.choices("abcdef0123456789", k=32)))
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
                "abyss_corruption": 0,
                "abyss_pity": 0,
                "abyss_crystal": 0,
                "abyss_last_decay": 0,
                "abyss_last_reset": "",
                "tomb": {
                    "mingbi": 0,
                    "level": 1,
                    "exp": 0,
                    "equipped_weapon": "",
                    "weapons": {},
                    "stats": {
                        "raids": 0,
                        "success": 0,
                        "fail": 0,
                        "total_mingbi": 0,
                    },
                    "daily": {"reset": "", "count": 0},
                    "inventory": {},
                },
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
        """清洗货币奖励：仅保留 金币/积分/钻石 中数额 > 0 的项。"""
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

    @classmethod
    def normalize_items(cls, items: dict) -> dict:
        """清洗道具奖励：仅保留系统中存在且数量 > 0 的项。"""
        out: dict[str, int] = {}
        valid = set(getattr(data, "ITEMS", {}))
        for name, cnt in (items or {}).items():
            if name not in valid:
                continue
            try:
                n = int(cnt)
            except (TypeError, ValueError):
                continue
            if n > 0:
                out[name] = n
        return out

    @staticmethod
    def card_rewards(card: dict) -> dict:
        """读取卡密货币奖励，兼容旧版单一货币格式 {currency, amount}。"""
        if isinstance(card.get("rewards"), dict):
            return {k: int(v) for k, v in card["rewards"].items() if int(v) > 0}
        cur = card.get("currency")
        amt = int(card.get("amount", 0) or 0)
        return {cur: amt} if cur and amt > 0 else {}

    @staticmethod
    def card_items(card: dict) -> dict:
        """读取卡密道具奖励。"""
        if isinstance(card.get("items"), dict):
            return {k: int(v) for k, v in card["items"].items() if int(v) > 0}
        return {}

    def create_cards(
        self, currency: str, amount: int, count: int = 1, prefix: str = ""
    ) -> list[str]:
        """批量生成单一货币卡密（向后兼容）。currency ∈ {金币, 积分, 钻石}。"""
        if currency not in self.CURRENCY_KEYS:
            raise ValueError("货币类型必须为 金币 / 积分 / 钻石")
        return self.create_combo_cards({currency: int(amount)}, count=count, prefix=prefix)

    def create_combo_cards(
        self, rewards: dict, items: Optional[dict] = None, count: int = 1, prefix: str = ""
    ) -> list[str]:
        """批量生成套餐卡密：可同时含 金币/积分/钻石 以及系统道具。"""
        rewards = self.normalize_rewards(rewards)
        items = self.normalize_items(items)
        if not rewards and not items:
            raise ValueError("请至少填写 金币/积分/钻石 中的一项，或选择一种道具及数量")
        count = max(1, int(count))
        cards = self.cards()
        created: list[str] = []
        now = int(time.time())
        for _ in range(count):
            code = self.gen_card_code(prefix)
            card: dict[str, Any] = {
                "used": False,
                "used_by": None,
                "used_at": None,
                "created_at": now,
            }
            if rewards:
                card["rewards"] = dict(rewards)
            if items:
                card["items"] = dict(items)
            cards[code] = card
            created.append(code)
        return created

    def redeem_card(self, code: str, player: dict, used_by: str):
        """兑换卡密：成功返回 (货币奖励字典, 道具奖励字典, None)，失败返回 (None, None, 原因)。"""
        code = str(code).strip().upper()
        cards = self.cards()
        card = cards.get(code)
        if card is None:
            return None, None, "卡密不存在或输入有误"
        if int(card.get("auth_days", 0) or 0) > 0:
            return None, None, "这是群授权卡，请用『授权 卡密』兑换"
        if card.get("used"):
            return None, None, "该卡密已被使用"
        rewards = self.card_rewards(card)
        items = self.card_items(card)
        if not rewards and not items:
            return None, None, "该卡密无有效奖励"
        card["used"] = True
        card["used_by"] = used_by
        card["used_at"] = int(time.time())
        for cur, amt in rewards.items():
            self.add_currency(player, cur, int(amt))
        for name, cnt in items.items():
            self.add_item(player, name, int(cnt))
        return rewards, items, None

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

    # ----------------------------- 深渊秘境 -----------------------------
    @staticmethod
    def abyss_state(player: dict) -> dict:
        """返回玩家深渊状态，自动 lazy init 所需字段。"""
        st = player.setdefault("abyss", {})
        st.setdefault("corruption", player.get("abyss_corruption", 0))
        st.setdefault("pity", player.get("abyss_pity", 0))
        st.setdefault("crystal", player.get("abyss_crystal", 0))
        st.setdefault("last_decay", player.get("abyss_last_decay", 0))
        st.setdefault("last_reset", player.get("abyss_last_reset", ""))
        st.setdefault("buffs", {})
        st.setdefault("blessing", "")
        return st

    @classmethod
    def refresh_abyss(cls, player: dict) -> dict:
        """刷新深渊状态：每日 0 点清零；按时间自然衰减侵蚀。"""
        today = time.strftime("%Y-%m-%d")
        st = cls.abyss_state(player)
        # 迁移旧字段到 st 后，删除顶层字段避免歧义
        for old_key in ("abyss_corruption", "abyss_pity", "abyss_crystal",
                        "abyss_last_decay", "abyss_last_reset"):
            player.pop(old_key, None)
        if st.get("last_reset") != today:
            st["corruption"] = 0
            st["pity"] = 0
            st["last_reset"] = today
        # 自然衰减
        now = int(time.time())
        last = st.get("last_decay", 0)
        from . import data  # 延迟导入避免循环
        interval = data.ABYSS_CORRUPTION_DECAY_INTERVAL
        amount = data.ABYSS_CORRUPTION_DECAY_AMOUNT
        if interval > 0 and now > last:
            elapsed = now - last
            drops = elapsed // interval
            if drops > 0:
                st["corruption"] = max(0, st.get("corruption", 0) - drops * amount)
                st["last_decay"] = now
        return st

    @classmethod
    def add_abyss_corruption(cls, player: dict, amount: int = 1) -> int:
        st = cls.abyss_state(player)
        st["corruption"] = max(0, st.get("corruption", 0) + amount)
        return st["corruption"]

    @classmethod
    def clear_abyss_corruption(cls, player: dict, amount: int) -> int:
        st = cls.abyss_state(player)
        before = st.get("corruption", 0)
        st["corruption"] = max(0, before - amount)
        return before - st["corruption"]

    @classmethod
    def get_abyss_corruption(cls, player: dict) -> int:
        return cls.abyss_state(player).get("corruption", 0)

    @classmethod
    def add_abyss_pity(cls, player: dict, amount: int = 1) -> int:
        st = cls.abyss_state(player)
        st["pity"] = max(0, st.get("pity", 0) + amount)
        return st["pity"]

    @classmethod
    def reset_abyss_pity(cls, player: dict) -> None:
        cls.abyss_state(player)["pity"] = 0

    @classmethod
    def get_abyss_pity(cls, player: dict) -> int:
        return cls.abyss_state(player).get("pity", 0)

    @classmethod
    def add_abyss_crystal(cls, player: dict, amount: int) -> int:
        st = cls.abyss_state(player)
        st["crystal"] = max(0, st.get("crystal", 0) + amount)
        return st["crystal"]

    @classmethod
    def get_abyss_crystal(cls, player: dict) -> int:
        return cls.abyss_state(player).get("crystal", 0)

    @classmethod
    def get_abyss_buffs(cls, player: dict) -> dict:
        return cls.abyss_state(player).setdefault("buffs", {})

    @classmethod
    def add_abyss_buff(cls, player: dict, key: str, count: int = 1) -> int:
        buffs = cls.get_abyss_buffs(player)
        buffs[key] = max(0, buffs.get(key, 0) + count)
        return buffs[key]

    @classmethod
    def consume_abyss_buff(cls, player: dict, key: str) -> bool:
        buffs = cls.get_abyss_buffs(player)
        if buffs.get(key, 0) > 0:
            buffs[key] -= 1
            if buffs[key] <= 0:
                buffs.pop(key, None)
            return True
        return False

    @classmethod
    def get_abyss_blessing(cls, player: dict) -> str:
        return cls.abyss_state(player).get("blessing", "")

    @classmethod
    def set_abyss_blessing(cls, player: dict, name: str) -> None:
        cls.abyss_state(player)["blessing"] = name

    @classmethod
    def clear_abyss_blessing(cls, player: dict) -> None:
        cls.abyss_state(player)["blessing"] = ""

    # ----------------------------- 宠物摸金（独立财富系统） -----------------------------
    @staticmethod
    def tomb_state(player: dict) -> dict:
        """返回玩家摸金状态，自动 lazy init 所需字段。"""
        st = player.setdefault("tomb", {
            "mingbi": 0,
            "level": 1,
            "exp": 0,
            "equipped_weapon": "",
            "weapons": {},
            "stats": {"raids": 0, "success": 0, "fail": 0, "total_mingbi": 0},
            "daily": {"reset": "", "count": 0},
            "inventory": {},
        })
        # 兼容旧数据：补上缺少的摸金等级/武器字段
        if "level" not in st:
            st["level"] = 1
        if "exp" not in st:
            st["exp"] = 0
        if "equipped_weapon" not in st:
            st["equipped_weapon"] = ""
        if "weapons" not in st:
            st["weapons"] = {}
        return st

    @classmethod
    def refresh_tomb_daily(cls, player: dict) -> dict:
        """刷新摸金每日次数。"""
        today = time.strftime("%Y-%m-%d")
        st = cls.tomb_state(player)
        daily = st.setdefault("daily", {"reset": "", "count": 0})
        if daily.get("reset") != today:
            daily["reset"] = today
            daily["count"] = 0
        return st

    @classmethod
    def add_tomb_mingbi(cls, player: dict, amount: int) -> int:
        """增加/扣除冥币（不会扣到负数）。"""
        st = cls.tomb_state(player)
        st["mingbi"] = max(0, st.get("mingbi", 0) + amount)
        return st["mingbi"]

    @classmethod
    def get_tomb_mingbi(cls, player: dict) -> int:
        return cls.tomb_state(player).get("mingbi", 0)

    @classmethod
    def add_tomb_item(cls, player: dict, name: str, count: int = 1) -> int:
        """向摸金背包增加道具。"""
        inv = cls.tomb_state(player).setdefault("inventory", {})
        inv[name] = max(0, inv.get(name, 0) + count)
        return inv[name]

    @classmethod
    def remove_tomb_item(cls, player: dict, name: str, count: int = 1) -> bool:
        """从摸金背包扣除道具，返回是否成功。"""
        inv = cls.tomb_state(player).get("inventory", {})
        if inv.get(name, 0) < count:
            return False
        inv[name] -= count
        if inv[name] <= 0:
            inv.pop(name, None)
        return True

    @classmethod
    def has_tomb_item(cls, player: dict, name: str, count: int = 1) -> bool:
        return cls.tomb_state(player).get("inventory", {}).get(name, 0) >= count

    @classmethod
    def add_tomb_token(cls, player: dict, count: int = 1) -> int:
        """增加额外入场券棺椁令。"""
        return cls.add_tomb_item(player, data.TOMB_EXTRA_TOKEN, count)

    @classmethod
    def consume_tomb_token(cls, player: dict, count: int = 1) -> bool:
        """消耗 count 枚棺椁令，返回是否成功。"""
        return cls.remove_tomb_item(player, data.TOMB_EXTRA_TOKEN, count)

    @classmethod
    def get_tomb_token_count(cls, player: dict) -> int:
        return cls.tomb_state(player).get("inventory", {}).get(data.TOMB_EXTRA_TOKEN, 0)

    @classmethod
    def get_tomb_level(cls, player: dict) -> int:
        return cls.tomb_state(player).get("level", 1)

    @classmethod
    def get_tomb_exp(cls, player: dict) -> int:
        return cls.tomb_state(player).get("exp", 0)

    @classmethod
    def add_tomb_exp(cls, player: dict, amount: int) -> tuple[int, int]:
        """增加摸金经验，自动升级直到满级。返回 (当前等级, 当前经验)。"""
        st = cls.tomb_state(player)
        level = st.get("level", 1)
        exp = st.get("exp", 0) + max(0, amount)
        while level < data.TOMB_MAX_LEVEL:
            need = data.tomb_exp_to_next(level)
            if exp < need:
                break
            exp -= need
            level += 1
        if level >= data.TOMB_MAX_LEVEL:
            level = data.TOMB_MAX_LEVEL
            exp = 0
        st["level"] = level
        st["exp"] = exp
        return level, exp

    # ---- 摸金武器 ----
    @classmethod
    def get_tomb_weapons(cls, player: dict) -> dict:
        """返回 {武器名: 剩余耐久}。"""
        return cls.tomb_state(player).get("weapons", {})

    @classmethod
    def get_tomb_equipped_weapon(cls, player: dict) -> str:
        return cls.tomb_state(player).get("equipped_weapon", "")

    @classmethod
    def add_tomb_weapon(cls, player: dict, name: str) -> int:
        """获得/修复武器：耐久恢复到满。返回当前耐久。"""
        st = cls.tomb_state(player)
        weapons = st.setdefault("weapons", {})
        weapons[name] = data.TOMB_WEAPONS[name]["durability"]
        return weapons[name]

    @classmethod
    def equip_tomb_weapon(cls, player: dict, name: str) -> bool:
        """装备一把已拥有的武器。"""
        st = cls.tomb_state(player)
        if name and name not in st.get("weapons", {}):
            return False
        st["equipped_weapon"] = name
        return True

    @classmethod
    def decrement_tomb_weapon(cls, player: dict, name: str) -> int | None:
        """武器耐久 -1，耐久归 0 则破碎消失。返回剩余耐久（None 表示武器不存在）。"""
        st = cls.tomb_state(player)
        weapons = st.get("weapons", {})
        if name not in weapons:
            return None
        weapons[name] -= 1
        remaining = weapons[name]
        if remaining <= 0:
            weapons.pop(name, None)
            if st.get("equipped_weapon") == name:
                st["equipped_weapon"] = ""
            return 0
        return remaining

    @classmethod
    def clear_tomb_loadout(cls, player: dict) -> None:
        """阵亡掉落：清空所有带入物品（武器+摸金背包道具）。棺椁令也一并掉落。"""
        st = cls.tomb_state(player)
        st["weapons"] = {}
        st["equipped_weapon"] = ""
        st["inventory"] = {}

    # ----------------------------- 邀请 -----------------------------
    @staticmethod
    def record_invite(inviter: dict, invitee: dict) -> None:
        """记录一次成功邀请：inviter 的 invited_users 列表增加 invitee，invitee 标记 invited_by。"""
        invitee["invited_by"] = str(inviter.get("qq", ""))
        inviter.setdefault("invited_users", []).append(
            {"qq": str(invitee.get("qq", "")), "at": int(time.time())}
        )

    # ----------------------------- 玩家门户账号 -----------------------------
    def portal_secret(self) -> str:
        """用于签名门户会话 Cookie 的密钥，首次自动随机生成。"""
        secret = self._data.get("portal_secret")
        if not secret:
            secret = "".join(random.choices("abcdef0123456789", k=32))
            self._data["portal_secret"] = secret
        return secret

    def accounts(self) -> dict:
        return self._data.setdefault("accounts", {})

    def get_account(self, account_id: str) -> Optional[dict]:
        return self.accounts().get(str(account_id))

    def get_account_by_qq(self, qq: str) -> Optional[dict]:
        qq = str(qq)
        for acc in self.accounts().values():
            if acc.get("qq") == qq:
                return acc
        return None

    def create_account(self, qq: str, password_hash: str, salt: str) -> dict:
        """创建门户账号。调用方需确保 QQ 未被注册。"""
        qq = str(qq)
        account_id = self.gen_card_code("U")  # 复用卡密生成器产生随机 ID
        account = {
            "id": account_id,
            "qq": qq,
            "password_hash": password_hash,
            "salt": salt,
            "bound_pets": [],  # [{group, qq, nick?}]
            "created_at": int(time.time()),
            "last_login": None,
        }
        self.accounts()[account_id] = account
        return account

    def account_for_pet(self, group_id: str, qq: str) -> Optional[str]:
        """查询某个群+QQ 的宠物已被绑定到哪个账号 ID。"""
        target = {"group": str(group_id), "qq": str(qq)}
        for acc_id, acc in self.accounts().items():
            for bp in acc.get("bound_pets", []):
                if bp.get("group") == target["group"] and bp.get("qq") == target["qq"]:
                    return acc_id
        return None

    def bind_pet_to_account(
        self, account_id: str, group_id: str, qq: str
    ) -> tuple[bool, str]:
        """绑定宠物到账号。返回 (是否成功, 提示)。"""
        account = self.get_account(account_id)
        if not account:
            return False, "账号不存在"
        group_id, qq = str(group_id), str(qq)
        key = self.make_key(group_id, qq)
        if key not in self._data.get("players", {}):
            return False, "该群聊与用户 ID 下不存在宠物"
        existing = self.account_for_pet(group_id, qq)
        if existing and existing != account_id:
            return False, "该宠物已被其他账号绑定"
        bound = account.setdefault("bound_pets", [])
        for bp in bound:
            if bp.get("group") == group_id and bp.get("qq") == qq:
                return True, "已经绑定过该宠物"
        player = self._data["players"][key]
        pet = player.get("pet") or {}
        bound.append({
            "group": group_id,
            "qq": qq,
            "nickname": pet.get("nickname", "未命名"),
            "species": pet.get("species", "未知"),
        })
        return True, "绑定成功"

    # ----------------------------- 宠物定制 -----------------------------
    def custom_reviews(self) -> dict:
        return self._data.setdefault("custom_reviews", {})

    def custom_image_path(self, filename: str) -> Path:
        return self.custom_images_dir / filename

    def create_custom_cards(self, count: int = 1, prefix: str = "") -> list[str]:
        """批量生成宠物定制卡密：兑换后为当前宠物解锁定制权限并晋升为混沌品质。"""
        count = max(1, int(count))
        cards = self.cards()
        created: list[str] = []
        now = int(time.time())
        for _ in range(count):
            code = self.gen_card_code(prefix)
            cards[code] = {
                "custom_pet": True,
                "used": False,
                "used_by": None,
                "used_at": None,
                "created_at": now,
            }
            created.append(code)
        return created

    def redeem_custom_card(
        self, code: str, player: dict, used_by: str
    ) -> tuple[Optional[dict], Optional[str]]:
        """兑换宠物定制卡：成功返回 (宠物数据, None)，失败返回 (None, 原因)。"""
        code = str(code).strip().upper()
        cards = self.cards()
        card = cards.get(code)
        if card is None:
            return None, "卡密不存在或输入有误"
        if not card.get("custom_pet"):
            return None, "这不是宠物定制卡"
        if card.get("used"):
            return None, "该卡密已被使用"
        pet = player.get("pet")
        if not pet:
            return None, "你没有宠物，无法使用定制卡"
        if pet.get("custom"):
            return None, "该宠物已解锁定制权限"
        from . import pet as petmod
        if pet.get("quality") != "混沌":
            ok, msg = petmod.upgrade_quality(pet, "混沌")
            if not ok:
                return None, msg
        pet["custom"] = True
        card["used"] = True
        card["used_by"] = used_by
        card["used_at"] = int(time.time())
        self.add_pet_tag(pet, "定制")
        return pet, None

    def unlock_pet_custom(self, player: dict) -> tuple[bool, str]:
        """直接为当前宠物解锁定制权限（内部/测试用）。"""
        pet = player.get("pet")
        if not pet:
            return False, "你没有宠物"
        if pet.get("custom"):
            return False, "该宠物已解锁定制权限"
        from . import pet as petmod
        if pet.get("quality") != "混沌":
            ok, msg = petmod.upgrade_quality(pet, "混沌")
            if not ok:
                return False, msg
        pet["custom"] = True
        self.add_pet_tag(pet, "定制")
        return True, "宠物定制权限已解锁，品质已晋升为【混沌】"

    @staticmethod
    def add_pet_tag(pet: dict, tag: str) -> None:
        tags = pet.setdefault("tags", [])
        if tag not in tags:
            tags.append(tag)

    @staticmethod
    def remove_pet_tag(pet: dict, tag: str) -> None:
        tags = pet.get("tags", [])
        if tag in tags:
            tags.remove(tag)
        if not tags:
            pet.pop("tags", None)

    @staticmethod
    def _month_key(ts: int) -> str:
        return time.strftime("%Y-%m", time.localtime(ts))

    def custom_change_counts(self, player: dict, typ: str) -> list[int]:
        return player.setdefault(f"custom_{typ}_changes", [])

    def can_custom_change(self, player: dict, typ: str, limit: int = 3) -> bool:
        month = self._month_key(int(time.time()))
        return (
            sum(
                1
                for ts in self.custom_change_counts(player, typ)
                if self._month_key(ts) == month
            )
            < limit
        )

    def remaining_custom_changes(self, player: dict, typ: str, limit: int = 3) -> int:
        month = self._month_key(int(time.time()))
        used = sum(
            1
            for ts in self.custom_change_counts(player, typ)
            if self._month_key(ts) == month
        )
        return max(0, limit - used)

    def create_custom_review(
        self,
        account_id: str,
        group_id: str,
        qq: str,
        changes: dict,
    ) -> tuple[Optional[dict], str]:
        """提交一次定制修改审核。changes 可含 image（文件名）和 species_name。"""
        player = self._data["players"].get(self.make_key(group_id, qq))
        if not player:
            return None, "未找到该宠物"
        pet = player.get("pet")
        if not pet:
            return None, "该账号下没有宠物"
        if not pet.get("custom"):
            return None, "该宠物尚未解锁定制权限"
        if self.get_pet_custom_reviews(group_id, qq, status="pending"):
            return None, "当前已有待审核的修改，请等待审核完成后再提交"
        want_image = bool(changes.get("image"))
        want_name = bool(changes.get("species_name"))
        if not want_image and not want_name:
            return None, "请至少修改一项内容"
        if want_image and not self.can_custom_change(player, "image"):
            return None, "本月宠物图片修改次数已达 3 次上限"
        if want_name and not self.can_custom_change(player, "species_name"):
            return None, "本月宠物种类名称修改次数已达 3 次上限"
        review_id = secrets.token_hex(8)
        now = int(time.time())
        old_image = pet.get("custom_image") or images.pet_image_url(pet.get("species"))
        old_name = pet.get("custom_species_name") or pet.get("species")
        review = {
            "id": review_id,
            "account_id": account_id,
            "group": group_id,
            "qq": qq,
            "old": {"image": old_image, "species_name": old_name},
            "new": {
                "image": changes.get("image") or old_image,
                "species_name": changes.get("species_name") or old_name,
            },
            "status": "pending",
            "reason": "",
            "created_at": now,
        }
        self.custom_reviews()[review_id] = review
        return review, "已提交审核，预计 3 个工作日内处理完毕"

    def apply_custom_review(self, review_id: str) -> tuple[bool, str]:
        review = self.custom_reviews().get(review_id)
        if not review:
            return False, "审核记录不存在"
        player = self._data["players"].get(self.make_key(review["group"], review["qq"]))
        if not player:
            return False, "玩家不存在"
        pet = player.get("pet")
        if not pet:
            return False, "宠物不存在"
        now = int(time.time())
        new = review["new"]
        old = review["old"]
        if new.get("image") and new["image"] != old.get("image"):
            pet["custom_image"] = new["image"]
            self.custom_change_counts(player, "image").append(now)
        if new.get("species_name") and new["species_name"] != old.get("species_name"):
            pet["custom_species_name"] = new["species_name"]
            self.custom_change_counts(player, "species_name").append(now)
        review["status"] = "approved"
        review["reviewed_at"] = now
        self.add_pet_tag(pet, "定制")
        return True, "审核已通过并生效"

    def reject_custom_review(self, review_id: str, reason: str) -> tuple[bool, str]:
        review = self.custom_reviews().get(review_id)
        if not review:
            return False, "审核记录不存在"
        review["status"] = "rejected"
        review["reason"] = str(reason or "不符合定制规范")
        review["reviewed_at"] = int(time.time())
        return True, "已拒绝"

    def get_pet_custom_reviews(
        self, group_id: str, qq: str, status: Optional[str] = None
    ) -> list[dict]:
        out = []
        key = self.make_key(group_id, qq)
        for r in self.custom_reviews().values():
            if self.make_key(r.get("group", ""), r.get("qq", "")) != key:
                continue
            if status and r.get("status") != status:
                continue
            out.append(r)
        return out

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
