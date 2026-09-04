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
    # 当前活跃实例：供 classmethod 形式的摸金接口定位全局 tomb_players 表，
    # 避免仅依赖 player["tomb"] 的对象引用共享（引用一旦被替换就会跨群分叉）
    _active: "PetStore | None" = None

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
        self.feedback_images_dir = self.path.parent / "feedback_images"
        self.feedback_images_dir.mkdir(parents=True, exist_ok=True)
        self.app_release_dir = self.path.parent / "app_release"
        self.app_release_dir.mkdir(parents=True, exist_ok=True)
        self.start_coin = start_coin
        self.start_jifen = start_jifen
        self.start_diamond = start_diamond
        self.default_enabled = default_enabled
        self.default_cross = default_cross
        self._lock = asyncio.Lock()
        self._data: dict[str, Any] = {"players": {}, "groups": {}}
        self._load()
        PetStore._active = self

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
        self._data.setdefault("group_map", {})
        self._data.setdefault("portal_secret", "".join(random.choices("abcdef0123456789", k=32)))
        self._data.setdefault("tomb_players", {})
        self._data.setdefault("ms_players", {})
        self._data.setdefault("sect_season", self._default_sect_season())
        self._data.setdefault("tomb_active_sessions", {})
        self._data.setdefault("tomb_active_coops", {})
        self._data.setdefault("tomb_active_coop_index", {})
        self._data.setdefault("homestead_players", {})
        self._data.setdefault("bank_players", {})
        self._data.setdefault("qq_bindings", {})      # 平台用户ID -> QQ号（全局）
        self._data.setdefault("email_config", {})      # 邮箱服务配置（SMTP）
        self._data.setdefault("lottery", None)         # 口令抽奖（单例：一个进行中的口令抽奖）
        self._data.setdefault("prize_wallet", {})      # 全局奖品背包：按 openid 主键，全群共享（不按群隔离）
        self._data.setdefault("custom_push", {"jobs": []})   # 自定义文本群推送任务
        self._data.setdefault("celebrate", {                  # 生辰盛典：每日 10 次定时开奖箱 + 奖池瓜分（后台可配）
            "enabled": False, "name": "生辰盛典",
            "start_at": 0, "end_at": 0,
            "announce": "🎂 生辰盛典开启！", "announce_end": "🎂 生辰盛典收官",
            "announced_start": False, "announced_end": False,
            "howto": "🎂 如何参与【生辰盛典】\n\n- 发送 `生日抽奖` 报名下一轮开奖箱（每轮1次，到点自动开奖，约80%中奖）\n- 发送 `生日快乐` 瓜分千万积分/金币/钻石（每日 `07:00` 开启，每15~30分钟随机冷却）\n\n> 场次详情见 `生辰活动`。",
            "howto_interval_h": 0, "howto_last_ts": 0,   # 每N小时全群推一次「如何参与」，0=关闭
            "gacha": {"enabled": True, "cmd": "生日抽奖", "menu_cmd": "生辰活动",
                      "win_rate": 0.8,                    # 每轮中奖人数≈参与人数×80%
                      "grand_item": "宠物定制卡", "grand_count": 1, "grand_used": False,   # 仅最后一轮保发的大奖（定制卡不进库存）
                      "stock": {"洪荒卡": 1, "变种卡": 5, "史诗卡": 10, "自动修炼卡": 94},          # 抽奖共享库存（配置总量）
                      "stock_remain": {"洪荒卡": 1, "变种卡": 5, "史诗卡": 10, "自动修炼卡": 94},   # 剩余量（动态扣减）
                      "rounds": []},
            "pool": {"enabled": True, "cmd": "生日快乐", "start_time": "07:00", "cooldown_min": 15, "cooldown_max": 30, "currencies": {}},
            "pool_remain": {},          # {"积分": int, "金币": int, "钻石": int}
            "players": {},              # openid -> {"pool_ts": int, "pool_next": int}
        })
        self._migrate_group_keys()
        self._migrate_tomb_to_global()
        self._migrate_multi_pet()
        self._migrate_bank_to_per_group()
        self._migrate_clear_cooldowns_once()

    def _migrate_clear_cooldowns_once(self) -> None:
        """一次性清空所有玩家冷却（修复时区后重置）。仅在未标记时执行一次。"""
        if self._data.get("cooldowns_cleared_v1"):
            return
        for pl in self._data.get("players", {}).values():
            # 玩家级冷却
            if "cooldowns" in pl:
                pl["cooldowns"] = {}
            # 每只宠物的冷却
            for pet in pl.get("pets", []):
                if isinstance(pet, dict) and "cooldowns" in pet:
                    pet["cooldowns"] = {}
        self._data["cooldowns_cleared_v1"] = True

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

    @staticmethod
    def _default_sect_season() -> dict:
        """宗门战赛季全局状态默认值。"""
        return {
            "season_id": "",
            "started_at": 0,
            "ended_at": 0,
            "matches": [],
            "rankings": {},
        }

    @staticmethod
    def _default_group_sect() -> dict:
        """单个群的宗门数据默认值。"""
        return {
            "enabled": True,
            "name": "",
            "level": 1,
            "exp": 0,
            "points": 0,          # 当前可用宗门积分（可消耗）
            "total_points": 0,    # 历史累计宗门积分（用于升级）
            "season_points": 0,
            "win": 0,
            "lose": 0,
            "draw": 0,
            "battles": 0,
            "honor": 0,
            "notice": "",
            "master_qq": "",
            "deputy_qqs": [],
            "today": {
                "date": "",
                "enroll": [],
                "forced": [],
                "confirmed": [],
                "signed": [],
            },
            "history": [],
        }

    @staticmethod
    def _default_player_sect() -> dict:
        """玩家宗门相关数据默认值。"""
        return {
            "contribution": 0,          # 当前可用宗门贡献（可消耗）
            "total_contribution": 0,    # 历史累计宗门贡献
            "season_contribution": 0,   # 本赛季累计宗门贡献
            "wins": 0,
            "battles": 0,
            "last_battle": 0,
            "active_score": 0,
            "last_active_at": 0,
        }

    @staticmethod
    def _default_tomb_state() -> dict:
        """全局摸金状态默认值。"""
        return {
            "mingbi": 0,
            "level": 1,
            "exp": 0,
            "equipped_weapon": "",
            "weapons": {},
            "storage_items": {},
            "equip_items": {},
            "stats": {"raids": 0, "success": 0, "fail": 0, "total_mingbi": 0},
            "daily": {"reset": "", "count": 0},
            "inventory": {},
            "daily_gains": {},
            "pending_pet_exp": 0,
        }

    def _migrate_tomb_to_global(self) -> None:
        """把各群的摸金数据合并为按 QQ 全局一份，保留财富最多的那份。"""
        global_tomb = self._data["tomb_players"]
        for key, pl in list(self._data.get("players", {}).items()):
            qq = str(pl.get("qq", ""))
            if not qq:
                continue
            old_tomb = pl.get("tomb")
            if not old_tomb or not isinstance(old_tomb, dict):
                continue
            # 应用默认值，避免旧字段缺失
            merged = self._default_tomb_state()
            merged.update(old_tomb)
            existing = global_tomb.get(qq)
            if existing is None:
                global_tomb[qq] = merged
            else:
                # 保留财富更多（或等级更高）的一份
                if merged.get("mingbi", 0) > existing.get("mingbi", 0):
                    global_tomb[qq] = merged
                elif merged.get("mingbi", 0) == existing.get("mingbi", 0) and merged.get("level", 1) > existing.get("level", 1):
                    global_tomb[qq] = merged
            # 删除玩家身上的 tomb，统一使用全局引用
            pl.pop("tomb", None)
        # 重新把全局引用挂到每个玩家身上
        for key, pl in self._data.get("players", {}).items():
            qq = str(pl.get("qq", ""))
            if qq:
                pl["tomb"] = global_tomb.setdefault(qq, self._default_tomb_state())

    def _ensure_global_tomb(self, qq: str) -> dict:
        """获取/创建某个 QQ 的全局摸金状态。"""
        return self._data["tomb_players"].setdefault(qq, self._default_tomb_state())

    # ----------------------------- 多宠物迁移 -----------------------------
    def _migrate_multi_pet(self) -> None:
        """将旧的单宠物 player["pet"] 迁移为 player["pets"] 列表。"""
        for pl in self._data["players"].values():
            if "pets" in pl:
                continue  # 已迁移
            old_pet = pl.pop("pet", None)
            if isinstance(old_pet, dict):
                # 迁移旧 cooldowns 到宠物身上
                if "cooldowns" in pl:
                    old_pet.setdefault("cooldowns", {}).update(pl.pop("cooldowns"))
                old_pet.setdefault("pet_id", str(int(time.time())) + "_" + secrets.token_hex(4))
                pl["pets"] = [old_pet]
                pl["active_pet"] = 0
            else:
                pl["pets"] = []
                pl["active_pet"] = -1
            pl.setdefault("pet_slots", 2)
        self._restore_pet_refs()

    def _restore_pet_refs(self) -> None:
        """重建 player["pet"] 运行时引用，指向 player["pets"][active_pet]。"""
        for pl in self._data["players"].values():
            idx = pl.get("active_pet", -1)
            pets = pl.get("pets", [])
            if 0 <= idx < len(pets):
                pl["pet"] = pets[idx]
            else:
                pl["pet"] = None

    def _migrate_bank_to_per_group(self) -> None:
        """将旧的全局银行数据（QQ key）迁移为按群隔离（group_id\x1fQQ key）。"""
        bank = self._data.get("bank_players", {})
        if not bank:
            return
        players = self._data.get("players", {})
        migrated = {}
        for key, bk in list(bank.items()):
            if "\x1f" in key:
                migrated[key] = bk  # 已迁移
                continue
            # 旧 QQ-only key → 找到该QQ所在群
            qq = key
            for pkey, pl in players.items():
                if pl.get("qq") == qq:
                    new_key = self.make_key(pl.get("group", ""), qq)
                    if new_key not in migrated:
                        migrated[new_key] = bk
                    break
            # 找不到归属群的孤立数据：跳过
        self._data["bank_players"] = migrated

    def _flush(self) -> None:
        # 序列化前剥离运行时 pet 引用（避免重复序列化）
        for pl in self._data["players"].values():
            pl.pop("pet", None)
        try:
            payload = json.dumps(self._data, ensure_ascii=False, indent=2)
        finally:
            # 无论如何都要恢复运行时引用
            self._restore_pet_refs()
        # 原子写入：先写 .tmp 再替换，写入前备份旧文件
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        # 验证写入内容可解析
        try:
            json.loads(tmp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return  # 写入损坏，放弃本次保存，保留旧数据
        # 保留一份备份
        bak = self.path.with_suffix(".bak")
        try:
            if self.path.exists():
                bak.write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass
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
                "pets": [],
                "active_pet": -1,
                "pet_slots": 2,
                "last_actions": {},
                "stats": {"battle_win": 0, "explore": 0},
                "quests": {},
                "auto_cultivation": {
                    "card_until": 0,
                },
                "auto_level": True,
                "abyss_corruption": 0,
                "abyss_pity": 0,
                "abyss_crystal": 0,
                "abyss_last_decay": 0,
                "abyss_last_reset": "",
                "sect": self._default_player_sect(),
            }
            qq = str(qq)
            players[key]["tomb"] = self._ensure_global_tomb(qq)
        # 老玩家兼容：补充 sect 字段
        pl = players.get(key)
        if pl is not None:
            pl.setdefault("sect", self._default_player_sect())
            pl.setdefault("auto_level", True)
        return pl

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
        group = groups[group_id]
        group.setdefault("enabled", self.default_enabled)
        group.setdefault("cross", self.default_cross)
        group.setdefault("sect", self._default_group_sect())
        sect = group["sect"]
        # 补充今日数据默认值并检查日期重置
        today = time.strftime("%Y-%m-%d")
        sect.setdefault("today", {
            "date": "",
            "enroll": [],
            "forced": [],
            "confirmed": [],
            "signed": [],
            "war": None,
        })
        if sect["today"].get("date") != today:
            sect["today"] = {
                "date": today,
                "enroll": [],
                "forced": [],
                "confirmed": [],
                "signed": [],
                "war": None,
            }
        sect.setdefault("history", [])
        sect.setdefault("deputy_qqs", [])
        return group

    # ----------------------------- 群映射（跨机器人群身份统一） -----------------------------
    def resolve_group(self, group_id: str) -> str:
        """把某机器人视角的群 openid 解析为规范群 ID（跨机器人数据互通的键）。

        QQ 官方机器人的 group_openid 按 appid 隔离：同一物理群在不同机器人处
        openid 不同。通过 ``group_map`` 把「其他机器人视角的 openid」映射到
        「主机器人视角的 openid」，使授权/宗门/群设置/跨群等按同一逻辑群共享。
        无映射时原样返回；带环保护（至多跟随若干次）。
        """
        group_id = str(group_id)
        mapping = self._data.get("group_map") or {}
        seen: set[str] = set()
        while group_id in mapping and group_id not in seen:
            seen.add(group_id)
            group_id = str(mapping[group_id])
        return group_id

    def group_map(self) -> dict:
        return self._data.setdefault("group_map", {})

    def set_group_map(self, src: str, dst: str) -> None:
        """把 src 群 openid 映射到规范群 dst openid。"""
        self._data.setdefault("group_map", {})[str(src)] = str(dst)

    def unset_group_map(self, src: str) -> bool:
        mapping = self._data.setdefault("group_map", {})
        if str(src) in mapping:
            del mapping[str(src)]
            return True
        return False

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
        """设置冷却（优先存储在活跃宠物上，实现按宠物隔离）。"""
        p = player.get("pet")
        if p is not None and isinstance(p, dict):
            p.setdefault("cooldowns", {})[key] = int(time.time()) + int(seconds)
        else:
            player.setdefault("cooldowns", {})[key] = int(time.time()) + int(seconds)

    @staticmethod
    def cooldown_remaining(player: dict, key: str) -> int:
        """查询冷却剩余（优先从活跃宠物读取）。"""
        p = player.get("pet")
        if p is not None and isinstance(p, dict) and "cooldowns" in p:
            end = p["cooldowns"].get(key, 0)
        else:
            end = player.get("cooldowns", {}).get(key, 0)
        return max(0, int(end) - int(time.time()))

    @staticmethod
    def set_player_cooldown(player: dict, key: str, seconds: int) -> None:
        """设置玩家级冷却（跨宠物共享，切换宠物不清除）。"""
        player.setdefault("cooldowns", {})[key] = int(time.time()) + int(seconds)

    @staticmethod
    def player_cooldown_remaining(player: dict, key: str) -> int:
        """查询玩家级冷却剩余（仅读玩家级，不受所持宠物影响）。"""
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
    def inc_event_shop_bought(player: dict, event_id: str, item: str, count: int = 1) -> None:
        st = PetStore.player_event_state(player, event_id)
        st["shop_bought"][item] = st["shop_bought"].get(item, 0) + max(1, int(count))

    @staticmethod
    def get_event_pity(player: dict, event_id: str, pity_name: str) -> int:
        return PetStore.player_event_state(player, event_id).get("pity_counts", {}).get(pity_name, 0)

    @staticmethod
    def inc_event_pity(player: dict, event_id: str, pity_name: str) -> None:
        st = PetStore.player_event_state(player, event_id)
        st.setdefault("pity_counts", {})[pity_name] = st.get("pity_counts", {}).get(pity_name, 0) + 1

    @staticmethod
    def reset_event_pity(player: dict, event_id: str, pity_name: str) -> None:
        st = PetStore.player_event_state(player, event_id)
        if "pity_counts" in st and pity_name in st["pity_counts"]:
            st["pity_counts"][pity_name] = 0

    # ----------------------------- 口令抽奖 / 全局奖品背包 -----------------------------
    # 设计要点：奖品数据「全群共享、以用户 id（openid）为主键、不做群隔离」。
    # 玩家发口令 -> 登记进 lottery.entries（按 openid 去重）；到点开奖 -> 把奖品记入
    # 全局 prize_wallet（此时尚未发放到任何群）；玩家在群里「我的奖品 - 兑换」时，
    # 才把奖品发放到指定群（该群的玩家记录），并移入 claimed 列表。
    def lottery(self) -> Optional[dict]:
        """当前口令抽奖配置（None 表示尚未创建）。"""
        return self._data.get("lottery")

    def set_lottery(self, cfg: dict) -> None:
        self._data["lottery"] = cfg

    def clear_lottery(self) -> None:
        self._data["lottery"] = None

    def prize_wallet(self) -> dict:
        return self._data.setdefault("prize_wallet", {})

    @staticmethod
    def wallet_for(wallet: dict, openid: str) -> dict:
        """取某一用户（openid）的全局奖品背包（unclaimed/claimed 两个列表）。"""
        w = wallet.setdefault(str(openid), {})
        w.setdefault("unclaimed", [])
        w.setdefault("claimed", [])
        return w

    @staticmethod
    def add_prize(wallet: dict, openid: str, entry: dict) -> None:
        """把一份已开奖奖品记入用户背包（unclaimed）。entry 至少含 id/lottery/prize/text/won_at/claimed=False。"""
        w = PetStore.wallet_for(wallet, openid)
        entry["claimed"] = False
        w["unclaimed"].append(entry)

    @staticmethod
    def move_unclaimed_to_claimed(
        wallet: dict, openid: str, prize_id: str, claimed_group: str, now: int
    ) -> Optional[dict]:
        """把背包中指定的奖品从 unclaimed 移入 claimed。应由调用方先把奖品真正发放到群后调用，
        返回被移走的奖品字典；id 不存在返回 None。"""
        w = PetStore.wallet_for(wallet, openid)
        for i, p in enumerate(w["unclaimed"]):
            if p.get("id") == prize_id:
                p = w["unclaimed"].pop(i)
                p["claimed"] = True
                p["claimed_at"] = now
                p["claimed_group"] = claimed_group
                w["claimed"].append(p)
                return p
        return None

    @staticmethod
    def prize_display_text(prize: dict) -> str:
        """把奖品字典 {kind,name,count} 转成人类可读文本（如『金币 x1000』、『还魂丹 x2』）。"""
        if not isinstance(prize, dict):
            return ""
        kind = prize.get("kind")
        name = prize.get("name", "")
        count = prize.get("count", 1)
        if kind == "currency":
            return f"{name} x{count}"
        if kind == "item":
            return f"{name} x{count}"
        return str(name)

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
    @classmethod
    def tomb_state(cls, player: dict) -> dict:
        """返回玩家摸金状态（全局按 QQ 共享）。

        总是以全局 tomb_players[qq] 为准：若玩家身上挂的是一份脱离全局的
        旧副本（如后台编辑整条替换过玩家记录），先保留更富的一份并重新
        统一引用，确保摸金数据跨群互通。
        """
        st = player.get("tomb")
        store = cls._active
        qq = str(player.get("qq", ""))
        if store is not None and qq:
            g = store._data["tomb_players"].setdefault(qq, cls._default_tomb_state())
            if isinstance(st, dict) and st is not g:
                if st.get("mingbi", 0) > g.get("mingbi", 0) or (
                    st.get("mingbi", 0) == g.get("mingbi", 0)
                    and st.get("level", 1) > g.get("level", 1)
                ):
                    g.clear()
                    g.update(st)
            st = player["tomb"] = g
        if not st or not isinstance(st, dict):
            st = player.setdefault("tomb", cls._default_tomb_state())
        # 兼容旧数据字段
        if "level" not in st:
            st["level"] = 1
        if "exp" not in st:
            st["exp"] = 0
        if "equipped_weapon" not in st:
            st["equipped_weapon"] = ""
        if "weapons" not in st:
            st["weapons"] = {}
        if "storage_items" not in st:
            st["storage_items"] = dict(st.get("inventory", {}))
        if "equip_items" not in st:
            st["equip_items"] = {}
        # 旧武器结构 {name: 耐久} 迁移为 {name: {durability, location}}
        for wname, wval in st.get("weapons", {}).items():
            if isinstance(wval, int):
                st["weapons"][wname] = {"durability": wval, "location": "equip"}
        if "daily_gains" not in st:
            st["daily_gains"] = {}
        if "pending_pet_exp" not in st:
            st["pending_pet_exp"] = 0
        return st

    # ---- 摸金运行时 session 持久化（插件重载后恢复） ----
    @staticmethod
    def _tomb_serialize(obj):
        """递归将 set/frozenset 转为 list，tuple 转为 list（确保 JSON 可序列化）。"""
        if isinstance(obj, (set, frozenset)):
            return [PetStore._tomb_serialize(v) for v in obj]
        if isinstance(obj, tuple):
            return [PetStore._tomb_serialize(v) for v in obj]
        if isinstance(obj, dict):
            return {k: PetStore._tomb_serialize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [PetStore._tomb_serialize(v) for v in obj]
        return obj

    @staticmethod
    def _tomb_deserialize_sets(obj, set_keys=frozenset({"visited", "ready"})):
        """递归将已知字段的 list 恢复为 set（元素为 tuple 时也恢复）。"""
        if isinstance(obj, dict):
            result = {}
            for k, v in obj.items():
                if k in set_keys and isinstance(v, list):
                    # visited: list of [x, y] → set of (x, y)
                    # ready: list of str → set of str
                    inner = [PetStore._tomb_deserialize_sets(item, set_keys) for item in v]
                    result[k] = {tuple(i) if isinstance(i, list) else i for i in inner}
                else:
                    result[k] = PetStore._tomb_deserialize_sets(v, set_keys)
            return result
        if isinstance(obj, list):
            return [PetStore._tomb_deserialize_sets(v, set_keys) for v in obj]
        return obj

    @classmethod
    def save_tomb_runstate(cls, sessions: dict, teams: dict, index: dict) -> None:
        """持久化当前所有活跃摸金状态（单次 flush，插件重载后恢复）。"""
        store = cls._active
        if store is None:
            return
        store._data["tomb_active_sessions"] = cls._tomb_serialize(dict(sessions))
        store._data["tomb_active_coops"] = cls._tomb_serialize(dict(teams))
        store._data["tomb_active_coop_index"] = dict(index)
        store._flush()

    @classmethod
    def load_tomb_sessions(cls) -> dict:
        """加载上次持久化的活跃单人摸金 session，恢复 set 类型。"""
        store = cls._active
        if store is None:
            return {}
        raw = store._data.get("tomb_active_sessions", {})
        return cls._tomb_deserialize_sets(dict(raw))

    @classmethod
    def load_tomb_coops(cls) -> tuple[dict, dict]:
        """加载上次持久化的活跃摸金双排队伍及索引，恢复 set 类型。"""
        store = cls._active
        if store is None:
            return {}, {}
        raw_teams = store._data.get("tomb_active_coops", {})
        raw_index = store._data.get("tomb_active_coop_index", {})
        return (
            cls._tomb_deserialize_sets(dict(raw_teams)),
            dict(raw_index),
        )

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
        """增加/扣除冥币（不会扣到负数）。增加时同步计入今日摸金获得。"""
        st = cls.tomb_state(player)
        st["mingbi"] = max(0, st.get("mingbi", 0) + amount)
        if amount > 0:
            cls._add_tomb_daily_gain(player, amount)
        return st["mingbi"]

    @classmethod
    def _add_tomb_daily_gain(cls, player: dict, amount: int) -> None:
        """记录一笔今日摸金冥币获得，并清理过时日期（仅保留昨天和今天）。"""
        from datetime import datetime, timedelta
        st = cls.tomb_state(player)
        daily_gains = st.setdefault("daily_gains", {})
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        # 清理早于昨天的记录
        for d in list(daily_gains.keys()):
            if d < yesterday:
                daily_gains.pop(d, None)
        daily_gains[today] = daily_gains.get(today, 0) + max(0, amount)

    @classmethod
    def get_tomb_daily_gain(cls, player: dict, date_str: str) -> int:
        """获取玩家指定日期的摸金冥币获得量。"""
        st = cls.tomb_state(player)
        return st.get("daily_gains", {}).get(date_str, 0)

    @classmethod
    def get_tomb_today_gain(cls, player: dict) -> int:
        from datetime import datetime
        return cls.get_tomb_daily_gain(player, datetime.now().strftime("%Y-%m-%d"))

    @classmethod
    def get_tomb_mingbi(cls, player: dict) -> int:
        return cls.tomb_state(player).get("mingbi", 0)

    @classmethod
    def add_tomb_item(cls, player: dict, name: str, count: int = 1, location: str = "storage") -> int:
        """向储物柜/装备背包增加道具。location: storage/equip。"""
        key = "storage_items" if location == "storage" else "equip_items"
        inv = cls.tomb_state(player).setdefault(key, {})
        inv[name] = max(0, inv.get(name, 0) + count)
        return inv[name]

    @classmethod
    def remove_tomb_item(cls, player: dict, name: str, count: int = 1, location: str = "storage") -> bool:
        """从储物柜/装备背包扣除道具，返回是否成功。"""
        key = "storage_items" if location == "storage" else "equip_items"
        inv = cls.tomb_state(player).get(key, {})
        if inv.get(name, 0) < count:
            return False
        inv[name] -= count
        if inv[name] <= 0:
            inv.pop(name, None)
        return True

    @classmethod
    def has_tomb_item(cls, player: dict, name: str, count: int = 1) -> bool:
        st = cls.tomb_state(player)
        return st.get("storage_items", {}).get(name, 0) + st.get("equip_items", {}).get(name, 0) >= count

    @classmethod
    def move_tomb_item(cls, player: dict, name: str, count: int, src: str, dst: str) -> bool:
        """在储物柜与装备背包之间移动道具。"""
        if src == dst:
            return False
        src_key = "storage_items" if src == "storage" else "equip_items"
        dst_key = "storage_items" if dst == "storage" else "equip_items"
        st = cls.tomb_state(player)
        src_inv = st.get(src_key, {})
        if src_inv.get(name, 0) < count:
            return False
        src_inv[name] -= count
        if src_inv[name] <= 0:
            src_inv.pop(name, None)
        dst_inv = st.setdefault(dst_key, {})
        dst_inv[name] = dst_inv.get(name, 0) + count
        return True

    @classmethod
    def add_tomb_token(cls, player: dict, count: int = 1) -> int:
        """增加额外入场券棺椁令（放入储物柜）。"""
        return cls.add_tomb_item(player, data.TOMB_EXTRA_TOKEN, count, "storage")

    @classmethod
    def consume_tomb_token(cls, player: dict, count: int = 1) -> bool:
        """消耗 count 枚棺椁令（先储物柜后装备背包），返回是否成功。"""
        st = cls.tomb_state(player)
        storage = st.setdefault("storage_items", {})
        equip = st.setdefault("equip_items", {})
        token = data.TOMB_EXTRA_TOKEN
        have_storage = storage.get(token, 0)
        have_equip = equip.get(token, 0)
        if have_storage + have_equip < count:
            return False
        from_storage = min(have_storage, count)
        if from_storage:
            storage[token] = have_storage - from_storage
            if storage[token] <= 0:
                storage.pop(token, None)
        remain = count - from_storage
        if remain:
            equip[token] = have_equip - remain
            if equip[token] <= 0:
                equip.pop(token, None)
        return True

    @classmethod
    def get_tomb_token_count(cls, player: dict) -> int:
        st = cls.tomb_state(player)
        return st.get("storage_items", {}).get(data.TOMB_EXTRA_TOKEN, 0) + st.get("equip_items", {}).get(data.TOMB_EXTRA_TOKEN, 0)

    @classmethod
    def get_tomb_level(cls, player: dict) -> int:
        return cls.tomb_state(player).get("level", 1)

    @classmethod
    def get_tomb_exp(cls, player: dict) -> int:
        return cls.tomb_state(player).get("exp", 0)

    @classmethod
    def add_tomb_exp(cls, player: dict, amount: int) -> tuple[int, int]:
        """增加摸金经验，自动升级（无等级上限）。返回 (当前等级, 当前经验)。"""
        st = cls.tomb_state(player)
        level = st.get("level", 1)
        exp = st.get("exp", 0) + max(0, amount)
        while True:
            need = data.tomb_exp_to_next(level)
            if exp < need:
                break
            exp -= need
            level += 1
        st["level"] = level
        st["exp"] = exp
        return level, exp

    @classmethod
    def add_tomb_pending_pet_exp(cls, player: dict, amount: int) -> int:
        """暂存一笔待兑换的宠物经验。"""
        st = cls.tomb_state(player)
        st["pending_pet_exp"] = st.get("pending_pet_exp", 0) + max(0, amount)
        return st["pending_pet_exp"]

    @classmethod
    def get_tomb_pending_pet_exp(cls, player: dict) -> int:
        return cls.tomb_state(player).get("pending_pet_exp", 0)

    @classmethod
    def clear_tomb_pending_pet_exp(cls, player: dict) -> None:
        cls.tomb_state(player)["pending_pet_exp"] = 0

    @classmethod
    def consume_tomb_pending_pet_exp(cls, player: dict, amount: int) -> int:
        """消费指定数量的待兑换宠物经验，返回实际消费数量。"""
        st = cls.tomb_state(player)
        pending = st.get("pending_pet_exp", 0)
        actual = min(pending, max(0, amount))
        st["pending_pet_exp"] = pending - actual
        return actual

    # ---- 摸金武器 ----
    @classmethod
    def get_tomb_weapons(cls, player: dict) -> dict:
        """返回 {武器名: {durability, location}}。"""
        return cls.tomb_state(player).get("weapons", {})

    @classmethod
    def get_tomb_equipped_weapon(cls, player: dict) -> str:
        return cls.tomb_state(player).get("equipped_weapon", "")

    @classmethod
    def add_tomb_weapon(cls, player: dict, name: str, location: str = "storage") -> int:
        """获得/修复武器：耐久恢复到满，默认放入储物柜。返回当前耐久。"""
        st = cls.tomb_state(player)
        weapons = st.setdefault("weapons", {})
        weapons[name] = {"durability": data.TOMB_WEAPONS[name]["durability"], "location": location}
        return weapons[name]["durability"]

    @classmethod
    def move_tomb_weapon(cls, player: dict, name: str, location: str) -> bool:
        """在储物柜/装备背包之间移动武器。"""
        st = cls.tomb_state(player)
        weapons = st.get("weapons", {})
        if name not in weapons:
            return False
        weapons[name]["location"] = location
        # 装备的武器被移出装备背包时自动卸下
        if location != "equip" and st.get("equipped_weapon") == name:
            st["equipped_weapon"] = ""
        return True

    @classmethod
    def equip_tomb_weapon(cls, player: dict, name: str) -> bool:
        """装备一把已在装备背包的武器。"""
        st = cls.tomb_state(player)
        weapons = st.get("weapons", {})
        if name not in weapons:
            return False
        if weapons[name].get("location") != "equip":
            return False
        st["equipped_weapon"] = name
        return True

    @classmethod
    def decrement_tomb_weapon(cls, player: dict, name: str) -> int | None:
        """武器耐久 -1，仅对装备背包中的武器生效；储物柜中的武器不掉耐久。
        耐久归 0 则破碎消失。返回剩余耐久（None 表示武器不存在）。
        """
        st = cls.tomb_state(player)
        weapons = st.get("weapons", {})
        if name not in weapons:
            return None
        w = weapons[name]
        # 保险柜中的武器不参与战斗耐久消耗
        if w.get("location") != "equip":
            return w.get("durability")
        w["durability"] -= 1
        remaining = w["durability"]
        if remaining <= 0:
            weapons.pop(name, None)
            if st.get("equipped_weapon") == name:
                st["equipped_weapon"] = ""
            return 0
        return remaining

    @classmethod
    def clear_tomb_loadout(cls, player: dict) -> None:
        """撤离失败掉落：清空装备背包（带入的道具+武器），储物柜保留。"""
        st = cls.tomb_state(player)
        st["equip_items"] = {}
        # 移除位于装备背包的武器
        st["weapons"] = {
            name: w for name, w in st.get("weapons", {}).items()
            if w.get("location") != "equip"
        }
        if st.get("equipped_weapon") not in st["weapons"]:
            st["equipped_weapon"] = ""

    @classmethod
    def writeback_tomb_equip(cls, player: dict, session_inventory: dict) -> None:
        """把当局剩余道具写回装备背包。"""
        cls.tomb_state(player)["equip_items"] = dict(session_inventory or {})

    # ----------------------------- 摸金每日神榜奖励 -----------------------------
    def tomb_daily_reward(self) -> dict:
        """全局：今日摸金神榜昨日前三奖励状态。"""
        return self._data.setdefault("tomb_daily_reward", {
            "date": "",
            "winners": [],
            "claimed": {},
        })

    def _tomb_yesterday_str(self) -> str:
        from datetime import datetime, timedelta
        return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    def get_or_compute_tomb_daily_reward(self) -> dict:
        """懒计算并缓存昨日神榜前三。返回 {date, winners, claimed}。"""
        yesterday = self._tomb_yesterday_str()
        reward = self.tomb_daily_reward()
        if reward.get("date") == yesterday:
            return reward
        entries = []
        for qq, st in self._data.get("tomb_players", {}).items():
            gain = st.get("daily_gains", {}).get(yesterday, 0)
            if gain > 0:
                entries.append({"key": str(qq), "gain": gain})
        entries.sort(key=lambda x: x["gain"], reverse=True)
        winners = []
        for w in entries[:3]:
            winners.append({
                "key": w["key"],
                "qq": w["key"],
                "gain": w["gain"],
            })
        reward["date"] = yesterday
        reward["winners"] = winners
        reward["claimed"] = {}
        return reward

    def claim_tomb_daily_reward(self, player: dict, group_id: str, qq: str) -> tuple[bool, int | None, str]:
        """尝试领取昨日神榜奖励。返回 (成功, 经验值, 提示)。"""
        from . import data
        qq = str(qq)
        reward = self.get_or_compute_tomb_daily_reward()
        winners = reward.get("winners", [])
        rank = None
        for i, w in enumerate(winners, 1):
            if w.get("key") == qq:
                rank = i
                break
        if rank is None:
            return False, None, "只有昨日神榜前三名可领取奖励。"
        claimed = reward.setdefault("claimed", {})
        if claimed.get(qq):
            return False, None, "今日已领取过摸金神榜奖励。"
        low, high = data.TOMB_DAILY_REWARD_EXP.get(rank, (5000, 15000))
        exp = random.randint(low, high)
        claimed[qq] = True
        return True, exp, ""

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

    def get_account_by_email(self, email: str) -> Optional[dict]:
        email = str(email or "").strip().lower()
        if not email:
            return None
        for acc in self.accounts().values():
            if str(acc.get("email") or "").strip().lower() == email:
                return acc
        return None

    def create_account(
        self, qq: str, password_hash: str, salt: str, email: Optional[str] = None
    ) -> dict:
        """创建门户账号。调用方需确保 QQ 未被注册。"""
        qq = str(qq)
        account_id = self.gen_card_code("U")  # 复用卡密生成器产生随机 ID
        account = {
            "id": account_id,
            "qq": qq,
            "email": (email or "").strip().lower() or None,
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
        self, account_id: str, group_id: str, qq: str, pet_index: int = 0
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
        pets = player.get("pets", [])
        idx = max(0, min(pet_index, len(pets) - 1)) if pets else 0
        pet = pets[idx] if 0 <= idx < len(pets) else {}
        bound.append({
            "group": group_id,
            "qq": qq,
            "pet_index": idx,
            "nickname": pet.get("nickname", "未命名"),
            "species": pet.get("species", "未知"),
        })
        return True, "绑定成功"

    def reclaim_pet_binding(
        self, account_id: str, group_id: str, qq: str, pet_index: int = 0
    ) -> tuple[bool, str]:
        """宠物所有方（其 QQ 绑定了该槽位）强行要回绑定权。

        仅当 `account["qq"]` 是该槽位宠物所有 QQ 时才允许：解除其他账号对该槽位的绑定，
        并改绑到本账号。返回 (是否成功, 提示)。
        """
        account = self.get_account(account_id)
        if not account:
            return False, "账号不存在"
        group_id, qq = str(group_id), str(qq)
        account_qq = str(account.get("qq", "")).strip()
        # 所有权校验：该槽位的用户ID属于本账号绑定的 QQ（直连或经 qq_bindings）
        if not (qq == account_qq or str(self.get_bound_qq(qq)) == account_qq):
            return False, "只有绑定该宠物所在 QQ 的账号才能强制要回"
        key = self.make_key(group_id, qq)
        if key not in self._data.get("players", {}):
            return False, "该群聊与用户 ID 下不存在宠物"
        player = self._data["players"][key]
        pets = player.get("pets", []) or []
        idx = max(0, min(int(pet_index or 0), len(pets) - 1)) if pets else 0
        pet = pets[idx] if 0 <= idx < len(pets) else {}
        # 移除所有账号对该槽位的绑定
        for acc in self.accounts().values():
            acc["bound_pets"] = [
                bp for bp in acc.get("bound_pets", [])
                if not (str(bp.get("group")) == group_id and str(bp.get("qq")) == qq)
            ]
        # 绑定到本账号
        bound = account.setdefault("bound_pets", [])
        bound.append({
            "group": group_id,
            "qq": qq,
            "pet_index": idx,
            "nickname": pet.get("nickname", "未命名"),
            "species": pet.get("species", "未知"),
        })
        return True, "已强行要回绑定权"

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
        self, code: str, player: dict, used_by: str, pet_index: int = 0
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
        # 多宠物支持：通过 pet_index 定位目标宠物
        pets = player.get("pets", [])
        idx = max(0, min(pet_index, len(pets) - 1)) if pets else 0
        pet = pets[idx] if 0 <= idx < len(pets) else player.get("pet")
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

    @staticmethod
    def auto_cultivation_active(player: dict, pet: dict = None) -> bool:
        """判断指定宠物是否享有自动修炼权限。

        定制宠物永久有效；非定制宠物需自动修炼卡在有效期内。
        """
        if pet is None:
            pet = player.get("pet") if player else None
        if pet and pet.get("custom"):
            return True
        ac = player.get("auto_cultivation", {}) if player else {}
        until = int(ac.get("card_until", 0) or 0)
        return until > int(time.time())

    def create_auto_cultivation_cards(
        self, count: int = 1, prefix: str = ""
    ) -> list[str]:
        """批量生成自动修炼卡密：1 张卡 = 1 天自动修炼权限。"""
        count = max(1, int(count))
        cards = self.cards()
        created: list[str] = []
        now = int(time.time())
        for _ in range(count):
            code = self.gen_card_code(prefix)
            cards[code] = {
                "auto_cultivation_days": 1,
                "used": False,
                "used_by": None,
                "used_at": None,
                "created_at": now,
            }
            created.append(code)
        return created

    def redeem_auto_cultivation_card(
        self, code: str, player: dict, used_by: str
    ) -> tuple[Optional[int], Optional[str]]:
        """兑换自动修炼卡：成功返回 (天数, None)，失败返回 (None, 原因)。"""
        code = str(code).strip().upper()
        cards = self.cards()
        card = cards.get(code)
        if card is None:
            return None, "卡密不存在或输入有误"
        days = int(card.get("auto_cultivation_days", 0) or 0)
        if days <= 0:
            return None, "这不是自动修炼卡"
        if card.get("used"):
            return None, "该卡密已被使用"
        pet = player.get("pet")
        if not pet:
            return None, "你没有宠物，无法使用自动修炼卡"
        if pet.get("custom"):
            return None, "你的宠物已是定制宠物，已永久享有自动修炼权限，无需此卡"
        now = int(time.time())
        ac = player.setdefault("auto_cultivation", {
            "card_until": 0,
        })
        cur = int(ac.get("card_until", 0) or 0)
        base = cur if cur > now else now
        ac["card_until"] = base + days * 86400
        card["used"] = True
        card["used_by"] = used_by
        card["used_at"] = now
        return days, None

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

    # ------------------------------------------------------------------
    # 玩家反馈（Bug / 建议）
    # ------------------------------------------------------------------
    def app_release(self) -> dict:
        """安卓 App 发布信息：{version_code, version_name, changelog, filename, updated_at}。"""
        return self._data.setdefault("app_release", {})

    def feedbacks(self) -> dict:
        return self._data.setdefault("feedbacks", {})

    def feedback_image_path(self, filename: str) -> Path:
        return self.feedback_images_dir / filename

    def create_feedback(
        self,
        account_id: str,
        account_qq: str,
        kind: str,
        content: str,
        occur_time: str = "",
        group_id: str = "",
        user_id: str = "",
        images: Optional[list[str]] = None,
    ) -> dict:
        fid = secrets.token_hex(8)
        fb = {
            "id": fid,
            "account_id": account_id,
            "qq": account_qq,
            "kind": kind,  # bug / suggestion
            "content": str(content or ""),
            "occur_time": str(occur_time or ""),
            "group": str(group_id or ""),
            "user_id": str(user_id or ""),
            "images": list(images or []),
            "status": "pending",  # pending / resolved
            "reply": "",
            "created_at": int(time.time()),
            "replied_at": 0,
        }
        self.feedbacks()[fid] = fb
        return fb

    def account_feedbacks(self, account_id: str) -> list[dict]:
        out = [
            fb for fb in self.feedbacks().values()
            if fb.get("account_id") == account_id
        ]
        out.sort(key=lambda x: x.get("created_at", 0), reverse=True)
        return out

    def reply_feedback(self, feedback_id: str, reply: str) -> tuple[bool, str]:
        fb = self.feedbacks().get(feedback_id)
        if not fb:
            return False, "反馈记录不存在"
        fb["reply"] = str(reply or "")
        fb["status"] = "resolved"
        fb["replied_at"] = int(time.time())
        return True, "已回复"

    def delete_feedback(self, feedback_id: str) -> bool:
        fb = self.feedbacks().pop(feedback_id, None)
        if fb:
            for img in fb.get("images", []):
                try:
                    self.feedback_image_path(img).unlink(missing_ok=True)
                except OSError:
                    pass
        return fb is not None

    # ----------------------------- 宠物扫雷（全服独立积分系统） -----------------------------
    @staticmethod
    def _default_ms_state() -> dict:
        """全局扫雷状态默认值（按 QQ 全服共享）。"""
        return {
            "score": 0,
            "wins": 0,
            "plays": 0,
            "best_time": {},
            "pending_pet_exp": 0,
        }

    def ms_state(self, qq: str) -> dict:
        """返回某 QQ 的全局扫雷状态，不存在则创建。"""
        st = self._data.setdefault("ms_players", {}).setdefault(
            str(qq), self._default_ms_state()
        )
        for k, v in self._default_ms_state().items():
            st.setdefault(k, v)
        return st

    def all_ms_players(self) -> dict[str, dict]:
        return self._data.setdefault("ms_players", {})

    def add_ms_pending_pet_exp(self, qq: str, amount: int) -> int:
        st = self.ms_state(qq)
        st["pending_pet_exp"] = st.get("pending_pet_exp", 0) + max(0, int(amount))
        return st["pending_pet_exp"]

    def get_ms_pending_pet_exp(self, qq: str) -> int:
        return self.ms_state(qq).get("pending_pet_exp", 0)

    def consume_ms_pending_pet_exp(self, qq: str, amount: int) -> int:
        """消费指定数量的待兑换扫雷宠物经验，返回实际消费数量。"""
        st = self.ms_state(qq)
        pending = st.get("pending_pet_exp", 0)
        actual = min(pending, max(0, int(amount)))
        st["pending_pet_exp"] = pending - actual
        return actual

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

    # ----------------------------- 宠物家园（放置建造 · 数据隔离） -----------------------------
    @staticmethod
    def _default_homestead_state() -> dict:
        return {
            "level": 1,
            "exp": 0,
            "buildings": {},          # {建筑名: {level, last_collect}}
            "dispatch": {},           # {建筑名: {qq, level, quality, element, since}}  派遣记录
            "visit_today": 0,
            "visit_date": "",
            "next_collect_bonus": 0.0,
            "steal_today": 0,         # 今日已偷次数
            "steal_date": "",
            "steal_targets": {},      # {目标QQ: 最后偷取时间戳}
            "be_stolen_today": 0,     # 今日被偷次数
            "be_stolen_date": "",
            "shield_until": 0,        # 护院符到期时间戳
            "weekly_coin": 0,         # 本周金币产出（排行用）
            "weekly_date": "",
            "total_coin_earned": 0,   # 累计金币产出
            "merchant_pending": None,  # 待处理的商人货架
        }

    @classmethod
    def homestead_state(cls, player: dict) -> dict:
        """返回玩家家园状态（全局按 QQ 共享，与宠物数据隔离）。"""
        store = cls._active
        qq = str(player.get("qq", ""))
        if store is not None and qq:
            g = store._data["homestead_players"].setdefault(qq, cls._default_homestead_state())
            # 兼容旧字段
            for field, default in cls._default_homestead_state().items():
                if field not in g:
                    g[field] = default
            return g
        return player.setdefault("_homestead_fallback", cls._default_homestead_state())

    @staticmethod
    def _default_bank_state() -> dict:
        """银行系统默认状态（存款/贷款/信用分/计息记录）。"""
        return {
            "deposit_coin": 0,           # 金币存款
            "deposit_jifen": 0,          # 积分存款
            "loan_coin": 0,              # 金币贷款余额
            "loan_jifen": 0,             # 积分贷款余额
            "loan_coin_due": "",         # 金币贷款日期 YYYY-MM-DD
            "loan_jifen_due": "",        # 积分贷款日期
            "loan_coin_repaid_at": 0,    # 金币贷款还清时间戳（冷却用）
            "loan_jifen_repaid_at": 0,   # 积分贷款还清时间戳
            "overdue_reminded_coin": False,   # 金币逾期是否已提醒
            "overdue_reminded_jifen": False,  # 积分逾期是否已提醒
            "credit_score": 500,         # 信用分
            "total_repaid": 0,           # 累计还款
            "total_borrowed": 0,         # 累计借款
            "total_interest_earned": 0,  # 累计利息收入
            "total_interest_paid": 0,    # 累计利息支出
            "last_interest_week": "",    # 上次计息周 YYYY-WW
        }

    @classmethod
    def bank_state(cls, player: dict) -> dict:
        """返回玩家银行状态（按群隔离，与玩家数据一致）。"""
        store = cls._active
        qq = str(player.get("qq", ""))
        group_id = str(player.get("group", ""))
        if store is not None and qq and group_id:
            key = cls.make_key(group_id, qq)
            g = store._data["bank_players"].setdefault(key, cls._default_bank_state())
            for field, default in cls._default_bank_state().items():
                if field not in g:
                    g[field] = default
            return g
        return {}

    # ----------------------------- QQ 绑定 -----------------------------
    def qq_bindings(self) -> dict:
        """返回 QQ 绑定表（平台用户ID -> QQ号）。"""
        return self._data.setdefault("qq_bindings", {})

    def get_bound_qq(self, platform_id: str) -> str:
        """返回某平台用户ID绑定的QQ号，未绑定返回空串。"""
        return str(self.qq_bindings().get(str(platform_id), ""))

    def set_qq_binding(self, platform_id: str, qq_num: str) -> None:
        """绑定平台用户ID与QQ号。若该QQ号已被其他ID绑定，先解除旧的。"""
        pid = str(platform_id)
        qq_num = str(qq_num)
        bindings = self.qq_bindings()
        # 防止一个QQ号绑多个ID：移除旧绑定
        for k, v in list(bindings.items()):
            if str(v) == qq_num and k != pid:
                del bindings[k]
        bindings[pid] = qq_num

    def unbind_qq(self, platform_id: str) -> bool:
        """解除绑定，返回是否曾绑定。"""
        pid = str(platform_id)
        bindings = self.qq_bindings()
        if pid in bindings:
            del bindings[pid]
            return True
        return False

    def find_platform_id_by_qq(self, qq_num: str) -> str:
        """按QQ号反查平台用户ID，未找到返回空串。"""
        qq_num = str(qq_num)
        for pid, q in self.qq_bindings().items():
            if str(q) == qq_num:
                return pid
        return ""

    def email_config(self) -> dict:
        """返回邮箱服务配置（SMTP）。"""
        return self._data.setdefault("email_config", {})

