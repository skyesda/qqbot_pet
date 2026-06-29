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
import time
from pathlib import Path
from typing import Any, Optional


class PetStore:
    def __init__(
        self,
        data_path: Path,
        start_coin: int = 1000,
        start_jifen: int = 0,
        default_enabled: bool = True,
        default_cross: bool = True,
    ):
        self.path = data_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.start_coin = start_coin
        self.start_jifen = start_jifen
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
    @staticmethod
    def add_currency(player: dict, currency: str, amount: int) -> None:
        key = "jifen" if currency == "积分" else "coin"
        player[key] = max(0, player.get(key, 0) + amount)

    @staticmethod
    def get_currency(player: dict, currency: str) -> int:
        key = "jifen" if currency == "积分" else "coin"
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
