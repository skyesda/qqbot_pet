"""Synchronous transactions: simulate on copies, commit once before sending messages."""
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo
import random
import time
import uuid
from . import content as c
from ..pet import new_pet
from .combat import build_party, enemies, hero_sheet, simulate
from .power import compute_unified_power, power_breakdown, power_to_scale

MENU = """## 灵契仙途
修士问道，灵宠同行。

初次游玩：`创建角色` → `选择职业 剑修`（体修 / 灵修）→ `结契灵宠 九尾狐`
老玩家可用 `灵宠助战 序号` 选择已有宠物。
① `修士修炼` 领取离线修为 → `修士突破` 提升等级（上限 999）
② 每个境界满级后 `渡劫` 破境（需天材地宝 + 天劫战斗），解锁「神通」
③ `仙途地图` → `历练 1` 挑战首领 · `锻造 灵剑` 培养装备
④ `组队秘境 葬龙秘境` 邀请群友并肩作战

`我的修士`（看灵根/神通/战力）· `我的洞天` · `洞天突破`
`修士配装 破阵` · `灵宠专长 辅助` · `道号` · `性别`
`世界首领` · `仙途深渊` · `仙途切磋 @对方`
`战斗详情` · `今日修行` · `仙途战绩`
完整指令（含原宠物玩法）请发送 `灵契仙途` 查看菜单图。"""


class RuleError(Exception):
    pass


class AdventureService:
    def __init__(self, store, clock=time.time, config=None):
        self.store, self.clock = store, clock
        c.validate_content()
        config = config or {}
        def bounded(name, default, low, high):
            try:
                value = int(config.get(name, default))
                return max(low, min(high, value))
            except (TypeError, ValueError, OverflowError):
                return default
        self.enemy_percent = bounded("adventure_enemy_percent", 100, 50, 200)
        self.boss_hp = bounded("adventure_boss_hp", 100000, 1000, 10000000)
        # 统一战力×敌人数值缩放：0=关闭(现行为不变) · 1=完全按战力缩放。灰度先行。
        try:
            self.adventure_power_scale = float(config.get("adventure_power_scale", 0.0))
        except (TypeError, ValueError):
            self.adventure_power_scale = 0.0
        self.adventure_power_scale = max(0.0, min(1.0, self.adventure_power_scale))

    def handle(self, group, qq, tokens, request_id=None):
        # No await inside transaction. Works with the shared plugin's single event loop.
        before = deepcopy(self.store._data)
        try:
            receipts = self.store._data.setdefault("adventure_receipts", {})
            receipt_key = f"{group}:{qq}:{request_id}" if request_id else None
            if receipt_key and receipt_key in receipts:
                return receipts[receipt_key]
            result = self._handle(str(group), str(qq), tokens)
            if receipt_key:
                receipts[receipt_key] = result
                while len(receipts) > 500:
                    del receipts[next(iter(receipts))]
            self.store._flush()
            return result
        except RuleError as e:
            self.store._data = before
            self.store._restore_pet_refs()
            return str(e)
        except Exception:
            self.store._data = before
            self.store._restore_pet_refs()
            raise

    def require(self, condition, message):
        if not condition:
            raise RuleError(message)

    def root(self):
        return self.store._data.setdefault("adventure_world", {"teams": {}, "bosses": {}, "duels": {}})

    def key(self, group, qq):
        return self.store.make_key(group, qq)

    def player(self, key):
        p = self.store._data["players"].get(key)
        self.require(p and p.get("adventure"), "请先发送「踏入仙途 剑修 / 体修 / 灵修」。")
        return p

    def today(self):
        return datetime.fromtimestamp(self.clock(), ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")

    def daily(self, a):
        if a.get("date") != self.today():
            a.update(date=self.today(), rewards=0, world_hits=0)

    def team_for(self, key):
        for tid, team in list(self.root()["teams"].items()):
            if team["expires"] <= self.clock():
                del self.root()["teams"][tid]
            elif key in team["members"]:
                return tid, team
        return None, None

    def can_edit(self, key):
        _, team = self.team_for(key)
        self.require(not team or key not in team["ready"], "你已准备出发，请先退出队伍再调整角色或助战。")

    def record(self, a, result, title):
        record = {"id": uuid.uuid4().hex[:12], "title": title, "time": int(self.clock()), "result": result}
        a.setdefault("history", []).append(record)
        a["history"] = a["history"][-10:]
        return self.report(record)

    @staticmethod
    def report(record):
        r = record["result"]
        outcome = ('平局' if r['winner'] is None else ('胜利' if r['won'] else '落败')) if record['title']=='无损论道' else ('通关' if r['won'] else '未通关')
        lines = [f"## {record['title']} · {outcome}",
                 f"{r['rounds']}回合 · {r['reason']}"]
        lines.extend(r["events"][-5:])
        for u in r['units']:
            if u['side']==0 and u['kind']=='hero':
                m=r['metrics'][u['owner']]
                lines.append(f"{u['name']}与灵宠：伤害{m['damage']} · 治疗{m['healing']} · 护盾吸收{m['absorbed']}")
        lines.append("发送「战斗详情」查看完整战报。")
        return "\n".join(lines)

    @staticmethod
    def _has_daolv(p):
        """道侣判定：修士与修士结道侣，以结契灵宠为『已婚』为准（与 power.py 口径一致）。"""
        pets = p.get("pets") or []
        return bool(pets and pets[0].get("love_state") == "已婚")

    def reward(self, a, level, first=False, tier=None, partner=False):
        self.daily(a)
        if a["rewards"] >= c.DAILY_REWARDS:
            return "今日8次副本收益已领取，仍可自由练习；首次通关记录保留。"
        a["rewards"] += 1
        ore = 3 + level // 3 + (3 if first else 0) + c.HEAVENS[a.get("heaven", 0) if tier is None else tier]["bonus"]
        a["ore"] += ore
        # 修为随「修士等级」增长（999 级长线节奏），灵材仍按副本等级结算。
        cult = 35 + a["level"] * 6
        if partner:
            cult = int(cult * 1.2)  # 道侣修为加速 +20%
        a["cultivation"] += cult
        return f"获得灵材×{ore}、修为×{cult}{'（道侣加成）' if partner else ''}（今日收益 {a['rewards']}/8）。"

    def _power_scale_factor(self, p, key):
        """统一战力→敌人数值缩放的灰度系数。开关为 0 时恒为 1.0（行为不变）。"""
        if self.adventure_power_scale <= 0:
            return 1.0
        target = power_to_scale(compute_unified_power(p, key))
        return 1.0 + (target - 1.0) * self.adventure_power_scale

    def encounter(self, enc, tier):
        result = deepcopy(enc)
        result['scale'] *= c.HEAVENS[tier]['enemy'] * self.enemy_percent / 100
        return result

    def world(self, group):
        # Infinite groups never contribute to official shared bosses.
        scope = "infinite:" + group if self.store._is_infinite_group(group) else "official"
        day = self.today()
        name = f"{scope}:{day}"
        bosses = self.root()["bosses"]
        for old in list(bosses):
            if not old.endswith(day):
                del bosses[old]
        if name not in bosses:
            cfg = c.WORLD_BOSSES[datetime.fromtimestamp(self.clock(), ZoneInfo('Asia/Shanghai')).toordinal() % 3]
            bosses[name] = {**cfg, "hp": self.boss_hp, "max_hp": self.boss_hp, "contributions": {}, "claimed": []}
        return bosses[name]

    def _handle(self, group, qq, tokens):
        cmd, args = tokens[0], tokens[1:]
        arg = args[0] if args else ""
        if cmd in ("灵契仙途", "仙途帮助"):
            return MENU
        p = self.store.get_player(qq, group)
        key = self.key(group, qq)
        if cmd == "创建角色":
            self.require(not p.get("adventure"), "角色已存在，请查看「我的修士」。")
            p.setdefault("adventure_draft", {"created_at": int(self.clock())})
            return "角色已创建。请选择职业：选择职业 剑修 / 体修 / 灵修。剑修爆发、体修护卫、灵修治疗。"
        if cmd in ("踏入仙途", "选择职业"):
            self.require(not p.get("adventure"), "你已踏入仙途。查看「我的修士」，更换职业使用「修士转职 职业」。")
            self.require(arg in c.PROFESSIONS, "请选择：踏入仙途 剑修 / 体修 / 灵修")
            pet = p.get("pet") or {}
            root = random.choices(c.SPIRIT_ROOTS, weights=[r["weight"] for r in c.SPIRIT_ROOTS], k=1)[0]["name"]
            p["adventure"] = {"schema_version": c.VERSION, "heaven": 0, "milestones": [], "name": f"{arg}修士", "profession": arg,
                "level": 1, "realm": 0, "cultivation": 0, "last_train": int(self.clock()) - 3600,
                "equipment": {slot: 0 for slot, _ in c.GEAR.values()}, "ore": 9,
                "equip_tier": {slot: 0 for slot, _ in c.GEAR.values()}, "equip_affix": {slot: None for slot, _ in c.GEAR.values()}, "forge_cd": 0,
                "style": "均衡", "pet_role": "攻击", "companion_pet_id": pet.get("pet_id"),
                "gender": random.choice(["男", "女"]), "bonus": {"atk": 0, "def": 0, "hp": 0, "speed": 0},
                "wudao": 0, "gengu": 0, "name_customized": False,
                "spirit_root": root, "tactics": [c.TACTICS[0]["name"]], "tribulation_cd": 0,
                "cleared": [], "history": [], "deep": None, "transfer_at": 0}
            p.pop("adventure_draft", None)
            return f"## 欢迎踏入灵契仙途\n你已成为{arg}，觉醒了「{root}」！获赠9份灵材与一小时修炼积累。\n「修士修炼」→「历练 1」→「锻造 灵剑」\n下一步：结契灵宠 九尾狐 / 卡比兽 / 七夕青鸟（新玩家任选一只）；老玩家用「灵宠助战 序号」。"
        a = self.player(key)["adventure"]
        a.setdefault("heaven", 0)
        a.setdefault("milestones", [])
        a.setdefault("gender", "男")
        a.setdefault("bonus", {"atk": 0, "def": 0, "hp": 0, "speed": 0})
        a.setdefault("wudao", 0)
        a.setdefault("gengu", 0)
        a.setdefault("name_customized", False)
        a.setdefault("tribulation_cd", 0)
        for slot, _ in c.GEAR.values():
            a.setdefault("equipment", {}).setdefault(slot, 0)
        a.setdefault("forge_cd", 0)
        for slot, _ in c.GEAR.values():
            # 老档回填：品阶按已有等级推断（min(9, 等级//100)），不追溯锁定。
            a.setdefault("equip_tier", {}).setdefault(slot, min(9, a["equipment"].get(slot, 0) // 100))
            a.setdefault("equip_affix", {}).setdefault(slot, None)
        # 惰性补齐：老玩家无灵根则补随机一个；无神通则按当前境界补发已解锁神通（炼气期必得灵台清明）。
        if not a.get("spirit_root"):
            a["spirit_root"] = random.choices(c.SPIRIT_ROOTS, weights=[r["weight"] for r in c.SPIRIT_ROOTS], k=1)[0]["name"]
        if not a.get("tactics"):
            a["tactics"] = [c.TACTICS[r]["name"] for r in range(min(a.get("realm", 0), len(c.TACTICS) - 1) + 1)]
        a["schema_version"] = c.VERSION
        self.daily(a)
        if cmd == "结契灵宠":
            self.can_edit(key)
            self.require(arg in c.STARTERS, "结契灵宠 九尾狐（攻击）/ 卡比兽（守护）/ 七夕青鸟（辅助）")
            self.require(not a.get("starter_claimed") and not p.get("pets"), "已有宠物或已领取初始伙伴，请使用「灵宠助战 序号」。")
            pet = new_pet(arg, "普通")
            p.update(pets=[pet], active_pet=0, pet=pet)
            a.update(starter_claimed=True, companion_pet_id=pet['pet_id'], pet_role=c.STARTERS[arg])
            return f"已与{arg}结契，获得普通品质初始伙伴！发送「修士修炼」→「历练 1」。"
        if cmd == "我的洞天":
            tier = c.HEAVENS[a['heaven']]
            nxt = c.HEAVENS[a['heaven']+1] if a['heaven'] < len(c.HEAVENS)-1 else None
            return (f"## 个人洞天 · {tier['name']}（{a['heaven']}阶）\n敌人倍率×{tier['enemy'] * self.enemy_percent / 100:.2f} · 每次副本收益额外灵材＋{tier['bonus']}\n"
                    + (f"Lv{nxt['level']}可挑战「洞天突破」，通过后进入{nxt['name']}，敌人倍率×{nxt['enemy']}、额外灵材＋{nxt['bonus']}。" if nxt else "已达最高洞天阶。")
                    + "\n不会随其他玩家升级。突破失败无损耗；突破后常驻提高难度。组队使用建队时队长的洞天阶，队员须已解锁。")
        if cmd == "洞天突破":
            self.can_edit(key)
            self.require(not a['deep'], "请先结束本轮深渊再突破洞天。")
            self.require(a['heaven'] < len(c.HEAVENS)-1, f"洞天已达{c.HEAVENS[-1]['name']}。")
            target = a['heaven']+1
            tier = c.HEAVENS[target]
            self.require(a['level'] >= tier['level'], f"洞天突破需要Lv{tier['level']}。")
            enc = dict(boss=f"{tier['name']}试炼使", scale=1+tier['level']*.14, mechanic="strike")
            enc["scale"] *= self.enemy_percent / 100
            result = simulate(build_party(p,key), enemies(enc), random.randrange(2**32))
            text = self.record(a,result,"洞天突破试炼")
            if result['won']:
                a['heaven'] = target
                return text + f"\n洞天突破成功：{tier['name']}（{target}阶）。个人副本倍率×{tier['enemy']}，收益额外灵材＋{tier['bonus']}。"
            return text + "\n突破失败，洞天与资源不变。提升装备或改用辅助灵宠后重试。"
        if cmd == "仙途毕业":
            goals = {"真仙Lv999": a['level'] == 999, "九渡天劫": a['realm'] == 9, "仙门洞天": a['heaven'] == len(c.HEAVENS) - 1,
                     "二十张地图": len(a['cleared']) == 20, "终章困难": "hard:20" in a['milestones'],
                     "三大秘境": all("raid:"+n in a['milestones'] for n in c.RAIDS),
                     "五层深渊": "deep:5" in a['milestones'], "已结契伙伴": bool(a.get('companion_pet_id'))}
            return "## 仙途毕业进度\n" + "\n".join(("✓ " if ok else "○ ")+name for name,ok in goals.items()) + ("\n恭喜，完成当前版本全部毕业目标！" if all(goals.values()) else "\n按未完成目标继续修行。")
        if cmd in ("我的修士", "今日修行"):
            units = build_party(p, key)
            s = hero_sheet(a, p)
            bd = power_breakdown(p, key)
            nxt = min(20, len(a["cleared"]) + 1)
            if bd:
                power_lines = (
                    f"总战力 {bd['total']}\n"
                    f"　构成：本体 {bd['hero']} ＋ 40%×灵宠『{bd['pet_name']}』{bd['pet_contrib']} ＋ 20%×坐骑 {bd['mount_contrib']} ＝ {bd['base']:.1f}\n"
                    f"　再乘：道侣×{bd['partner']:.2f} · 洞天×{bd['heaven_margin']:.2f} → {bd['total']}"
                )
            else:
                power_lines = "总战力 0"
            tactics = " · ".join(a.get("tactics", [])) or "无"
            cap = c.realm_cap(a["realm"])
            cap_hint = "" if a["realm"] >= len(c.REALMS) - 1 else f"\n境界封顶 Lv{cap}（满级后「渡劫」破境）"
            return (f"## 灵契仙途 · {a['profession']}\n道号 {a['name']} · {a['gender']} · {c.REALMS[a['realm']]} Lv{a['level']} · 修为 {a['cultivation']}\n"
                    f"灵根：{a.get('spirit_root') or '无'} · 神通：{tactics}\n"
                    f"洞天：{c.HEAVENS[a['heaven']]['name']}（{a['heaven']}阶）\n{power_lines}\n性命 {s['hp']} · 攻击 {s['atk']} · 防御 {s['def']} · 速度 {s['speed']}\n"
                    f"悟性 {s['wudao']} · 根骨 {s['gengu']}\n功法：{a['style']} · 灵宠：{units[1]['name']}（{a['pet_role']}）\n"
                    f"灵材 {a['ore']} · 今日副本收益 {a['rewards']}/8 · 首领挑战 {a['world_hits']}/3"
                    f"{cap_hint}\n"
                    f"下一步：历练 {nxt}（{c.MAPS[str(nxt)]['name']}）\n修士修炼 · 修士突破 · 修士装备 · 渡劫 · 道号 · 性别")
        if cmd == "修士转职":
            self.can_edit(key)
            self.require(arg in c.PROFESSIONS, "职业：剑修 / 体修 / 灵修")
            self.require(self.clock() >= a["transfer_at"], "转职间隔24小时，境界和装备不会丢失。")
            a["profession"] = arg
            if not a.get("name_customized"):
                a["name"] = f"{arg}修士"
            a["transfer_at"] = self.clock() + 86400
            return f"已转为{arg}，保留境界、装备、道号与进度。"
        if cmd == "道号":
            self.can_edit(key)
            if not arg:
                return f"当前道号：{a['name']}。首次起名免费；再次修改消耗『改名符』（灵石商城）。用法：道号 新道号（≤12字）"
            self.require(len(arg) <= 12, "道号最多12个字。")
            self.require(arg != a["name"], "与当前道号相同。")
            if not a.get("name_customized"):
                a["name_customized"] = True
                a["name"] = arg
                return f"道号已定为：{a['name']}。以后修改需消耗『改名符』（灵石商城）。"
            if not self.store.remove_item(p, "改名符"):
                return "修改道号需要『改名符』×1（灵石商城购买）。"
            a["name"] = arg
            return f"道号已改：{a['name']}，消耗『改名符』×1。"
        if cmd == "性别":
            self.can_edit(key)
            self.require(arg in ("男", "女"), "性别 男 / 女")
            self.require(arg != a.get("gender"), f"当前已是{a.get('gender')}。")
            if not self.store.remove_item(p, "变性丹"):
                return "改变性别需要『变性丹』×1（灵石商城购买）。"
            a["gender"] = arg
            return f"已变性为{arg}修。" + ("攻击+5%" if arg == "男" else "防御与速度+5%")
        if cmd == "修士修炼":
            rate = 2 + a["realm"] * 4
            root = c.spirit_root_by_name(a.get("spirit_root", ""))
            if root and root.get("cult"):
                rate *= 1 + root["cult"]
            minutes = min(720, max(0, int((self.clock() - a["last_train"]) // 60)))
            self.require(minutes > 0, "每分钟积累修为（随境界与灵根加速），最多储存12小时，请稍后领取。")
            gain = int(minutes * rate)
            a["cultivation"] += gain
            a["last_train"] = int(self.clock())
            return f"修炼归来，修为＋{gain}（{rate:g}修为/分）。发送「修士突破」提升修为等级。"
        if cmd == "修士突破":
            self.can_edit(key)
            cap = c.realm_cap(a["realm"])
            self.require(a["level"] < c.MAX_LEVEL, "已臻真仙圆满，臻于化境，无可再进。")
            self.require(a["level"] < cap, f"已达{c.REALMS[a['realm']]}巅峰Lv{cap}，须「渡劫」方可破境。")
            cost = 60 + a["level"] * 20
            self.require(a["cultivation"] >= cost, f"突破需要{cost}修为，当前{a['cultivation']}。")
            a["cultivation"] -= cost
            a["level"] += 1
            a["wudao"] = a.get("wudao", 0) + 1
            a["gengu"] = a.get("gengu", 0) + 1
            return f"突破成功：{c.REALMS[a['realm']]} Lv{a['level']}！悟性+1 · 根骨+1"
        if cmd == "渡劫":
            self.can_edit(key)
            self.require(a["realm"] < len(c.REALMS) - 1, "已臻真仙，无劫可渡。")
            cap = c.realm_cap(a["realm"])
            self.require(a["level"] >= cap, f"需先修炼至{c.REALMS[a['realm']]}巅峰Lv{cap}，方可渡劫。")
            self.require(self.clock() >= a.get("tribulation_cd", 0), "渡劫失利，需静养30分钟方可再试。")
            mat = c.TRIBULATION_MATERIALS[a["realm"]]
            self.require(self.store.remove_item(p, mat), f"渡劫需『{mat}』×1（灵石商城购买，或历练/首领低概率掉落）。")
            enc = dict(boss=f"{c.REALMS[a['realm']]}天劫", scale=c.tribulation_scale(a["realm"]), mechanic="strike")
            result = simulate(build_party(p, key), enemies(enc), random.randrange(2**32))
            text = self.record(a, result, "渡劫")
            if result["won"]:
                a["realm"] += 1
                tactic = c.TACTICS[a["realm"]]["name"]
                if tactic not in a.setdefault("tactics", []):
                    a["tactics"].append(tactic)
                a["wudao"] = a.get("wudao", 0) + 3
                a["gengu"] = a.get("gengu", 0) + 3
                a["tribulation_cd"] = 0
                return text + f"\n⛈ 渡劫成功！你已踏入{c.REALMS[a['realm']]}（{a['realm']}境）！\n解锁神通「{tactic}」· 悟性+3 · 根骨+3"
            a["tribulation_cd"] = self.clock() + c.TRIBULATION_FAIL_COOLDOWN
            return text + f"\n⛈ 渡劫失败：雷劫加身，境界未退，『{mat}』已消耗。30分钟内不可再渡劫。"
        if cmd == "修士配装":
            self.can_edit(key)
            self.require(arg in c.STYLES, "修士配装 均衡 / 破阵 / 守心\n" + "\n".join(f"{k}：{v}" for k,v in c.STYLES.items()))
            a["style"] = arg
            return f"已选择{arg}：{c.STYLES[arg]}"
        if cmd == "灵宠专长":
            self.can_edit(key)
            self.require(arg in c.PET_ROLES, "灵宠专长 攻击 / 守护 / 辅助（免费调整）")
            a["pet_role"] = arg
            return f"灵宠已采用{arg}专长，原天赋和属性未修改。"
        if cmd == "灵宠助战":
            self.can_edit(key)
            self.require(arg.isdigit() and 1 <= int(arg) <= len(p.get("pets", [])), "用法：灵宠助战 宠物序号，发送「宠物列表」查看。")
            pet = p["pets"][int(arg)-1]
            a["companion_pet_id"] = pet["pet_id"]
            return f"已与{pet['nickname']}结契。"
        if cmd == "修士装备":
            return ("## 修士装备\n" + "\n".join(
                f"{c.gear_name(a['profession'], slot, a['equip_tier'][slot])} Lv{a['equipment'][slot]} · {c.GEAR_TIERS[a['equip_tier'][slot]]} · 词条「{a['equip_affix'][slot] or '无'}」 · 提升{label}"
                for name, (slot, label) in c.GEAR.items())
                + f"\n灵材 {a['ore']}"
                + "\n锻造 装备名：每级 3＋当前等级×2 灵材，品阶巅峰须「装备进阶」。"
                + "\n装备进阶 装备名：消耗进阶材料＋器劫试炼，成功晋升品阶并觉醒词条；洗炼 装备名：30灵材重roll词条。")
        if cmd == "锻造":
            self.can_edit(key)
            self.require(arg in c.GEAR, "用法：锻造 " + c.GEAR_NAMES)
            slot = c.GEAR[arg][0]
            rank = a["equipment"][slot]
            tier = a["equip_tier"][slot]
            cap = min(a["level"], c.tier_cap(tier))
            self.require(rank < cap, "该装备已达当前上限（角色等级或品阶巅峰），先「修士突破」或「装备进阶」。")
            cost = 3 + rank * 2
            self.require(a["ore"] >= cost, f"需要{cost}灵材，可通过历练获得。")
            a["ore"] -= cost
            a["equipment"][slot] += 1
            return f"锻造成功：{c.gear_name(a['profession'], slot, tier)}＋{rank + 1}。"
        if cmd == "装备进阶":
            self.can_edit(key)
            self.require(arg in c.GEAR, "用法：装备进阶 " + c.GEAR_NAMES)
            slot = c.GEAR[arg][0]
            rank = a["equipment"][slot]
            tier = a["equip_tier"][slot]
            self.require(tier < len(c.GEAR_TIERS) - 1, "该装备已臻鸿蒙，无可进阶。")
            self.require(rank >= c.tier_cap(tier), f"需先将{c.gear_name(a['profession'], slot, tier)}锻造至{c.GEAR_TIERS[tier]}巅峰Lv{c.tier_cap(tier)}，方可进阶。")
            self.require(self.clock() >= a.get("forge_cd", 0), "进阶失利，需静养30分钟方可再试。")
            mat = c.FORGE_MATERIALS[tier]
            self.require(self.store.remove_item(p, mat), f"进阶需『{mat}』×1（灵石商城购买，或历练/首领低概率掉落）。")
            enc = dict(boss=f"{c.GEAR_TIERS[tier]}器劫", scale=c.forge_scale(tier), mechanic="strike")
            result = simulate(build_party(p, key), enemies(enc), random.randrange(2**32))
            text = self.record(a, result, "装备进阶")
            if result["won"]:
                a["equip_tier"][slot] += 1
                a["equip_affix"][slot] = random.choice(list(c.AFFIXES))
                a["forge_cd"] = 0
                new_tier = a["equip_tier"][slot]
                return text + f"\n⚒ 进阶成功：{c.gear_name(a['profession'], slot, new_tier)}晋升{c.GEAR_TIERS[new_tier]}！\n觉醒词条「{a['equip_affix'][slot]}」"
            a["forge_cd"] = self.clock() + c.FORGE_FAIL_COOLDOWN
            return text + f"\n⚒ 进阶失败：器劫加身，品阶未退，『{mat}』已消耗。30分钟内不可再进阶。"
        if cmd == "洗炼":
            self.can_edit(key)
            self.require(arg in c.GEAR, "用法：洗炼 " + c.GEAR_NAMES)
            slot = c.GEAR[arg][0]
            self.require(a["equip_affix"].get(slot), "该装备尚无词条，先「装备进阶」成功觉醒词条。")
            self.require(a["ore"] >= 30, "洗炼需要30灵材。")
            a["ore"] -= 30
            a["equip_affix"][slot] = random.choice(list(c.AFFIXES))
            return f"洗炼成功：{c.gear_name(a['profession'], slot, a['equip_tier'][slot])} 新词条「{a['equip_affix'][slot]}」。"
        if cmd == "仙途地图":
            return f"## 山海历练 · {c.HEAVENS[a['heaven']]['name']}洞天 · 敌人×{c.HEAVENS[a['heaven']]['enemy']}\n" + "\n".join(f"{m['id']}. {m['name']} · Lv{m['level']} · {m['boss']} {'✓' if m['id'] in a['cleared'] else ''}" for m in c.MAPS.values()) + "\n历练 序号 · 首关直接开放，之后逐关解锁。通关后可「历练 序号 困难」：敌人加强，灵材额外＋2，同样占一次收益。"
        if cmd in ("历练", "挑战秘境"):
            enc = c.map_for(arg or str(min(20, len(a['cleared'])+1)))
            self.require(enc, "发送「仙途地图」查看，使用「历练 序号」。")
            self.require(enc['id'] == '1' or str(int(enc['id'])-1) in a['cleared'], "先通关上一张地图。")
            self.require(a['level'] >= enc['level'], f"本关需要修士Lv{enc['level']}。")
            hard = len(args)>1 and args[1]=='困难'
            self.require(len(args)<2 or args[1]=='困难','用法：历练 序号 [困难]')
            self.require(not hard or enc['id'] in a['cleared'],'先通关普通难度。')
            enc=self.encounter(enc, a["heaven"])
            if hard:
                enc['scale']*=1.65
            result = simulate(build_party(p,key), enemies(enc, scale_factor=self._power_scale_factor(p, key)), random.randrange(2**32))
            text = self.record(a,result,enc['name']+('·困难' if hard else ''))
            if result['won']:
                if hard and 'hard:'+enc['id'] not in a['milestones']:
                    a['milestones'].append('hard:'+enc['id'])
                first = enc['id'] not in a['cleared']
                if first:
                    a['cleared'].append(enc['id'])
                eligible=a['rewards']<c.DAILY_REWARDS
                text += "\n" + self.reward(a,enc['level'],first,partner=self._has_daolv(p))
                if hard and eligible:
                    a['ore']+=2
                    text+=' 困难额外灵材＋2。'
            else:
                text += "\n" + c.MECHANICS[enc['mechanic']][1] + " 未消耗收益次数。"
            return text
        if cmd in ("组队秘境", "加入队伍", "准备出发", "队伍出发", "退出队伍", "仙途队伍"):
            return self.party(group,key,cmd,arg)
        if cmd == "世界首领":
            b = self.world(group)
            return f"## 世界首领 · {b['name']}\n生命 {b['hp']}/{b['max_hp']}\n{c.MECHANICS[b['mechanic']][1]}\n每天轮换，个人每日3次；讨伐获得灵材，击败后可领共同奖励。\n讨伐首领 · 首领奖励"
        if cmd == "讨伐首领":
            b = self.world(group)
            self.require(b['hp'] > 0, "首领已被击败，发送「首领奖励」。")
            self.require(a['world_hits'] < 3, "今日首领挑战已完成，明日再来。")
            enc = self.encounter(dict(boss=b['name'],scale=1+c.HEAVENS[a['heaven']]['level']*.16,mechanic=b['mechanic']),a['heaven'])
            result = simulate(build_party(p,key), enemies(enc, scale_factor=self._power_scale_factor(p, key)), random.randrange(2**32),max_rounds=12)
            damage = min(b['hp'],sum(v['damage'] for k,v in result['metrics'].items() if k==key))
            b['hp'] -= damage
            b['contributions'][key] = b['contributions'].get(key,0)+damage
            a['world_hits'] += 1
            a['ore'] += 3
            return self.record(a,result,b['name']) + f"\n本次贡献{damage}伤害，获得3灵材。首领剩余{b['hp']}。" + ("\n首领已被击败！参与者可发送「首领奖励」。" if not b['hp'] else "")
        if cmd == "首领奖励":
            b = self.world(group)
            self.require(b['hp']==0 and b['contributions'].get(key,0)>0, "需要参与并击败今日首领。")
            self.require(key not in b['claimed'], "今日首领奖励已领取。")
            b['claimed'].append(key)
            a['ore'] += 12
            return "共同讨伐奖励：灵材×12，已到账。"
        if cmd in ("仙途深渊", "深渊抉择", "深渊收手"):
            return self.deep(p,key,cmd,arg)
        if cmd in ("仙途切磋", "接受切磋", "拒绝切磋"):
            return self.duel(group,key,cmd,arg)
        if cmd == "战斗详情":
            self.require(a['history'], "尚无战报，发送「历练 1」。")
            r = a['history'][-1]
            page = int(arg) if arg.isdigit() else 1
            pages = max(1,(len(r['result']['events'])+14)//15)
            self.require(1<=page<=pages, f"页码范围1—{pages}。")
            return f"## {r['title']} · 战报 {page}/{pages}\n" + "\n".join(r['result']['events'][(page-1)*15:page*15]) + f"\n战斗编号 {r['id']} · 战斗详情 页码"
        if cmd == "仙途战绩":
            return "## 最近战绩\n" + ("\n".join(f"{r['title']} · {'胜' if r['result']['won'] else '未胜'} · {r['result']['rounds']}回合" for r in reversed(a['history'])) or "尚无战绩")
        raise RuleError("发送「灵契仙途」查看玩法。")

    def party(self, group, key, cmd, arg):
        teams = self.root()['teams']
        tid, team = self.team_for(key)
        a = self.player(key)['adventure']
        if cmd == '组队秘境':
            self.require(not team, '你已有队伍，先发送「退出队伍」。')
            name = arg or '葬龙秘境'
            self.require(name in c.RAIDS, '秘境：葬龙秘境 / 机关迷城 / 暗月幽林')
            self.require(a['level'] >= c.RAIDS[name]['level'], f"需要Lv{c.RAIDS[name]['level']}。")
            tid = uuid.uuid4().hex[:8]
            teams[tid] = dict(group=group, leader=key, name=name, members=[key], ready={}, tier=a["heaven"], expires=self.clock()+1800)
            return f"## {name}招募中\n队伍编号 {tid} · 洞天{a['heaven']}阶 · 30分钟有效\n群友发送「加入队伍 {tid}」，然后各自「准备出发」。最多3人。"
        if cmd == '加入队伍':
            self.require(not team,'你已在队伍中。')
            target = teams.get(arg)
            self.require(target and target['group']==group and target['expires']>self.clock(), '队伍不存在、已过期或不在本群。')
            self.require(len(target['members'])<3, '队伍已满。')
            self.require(a['level']>=c.RAIDS[target['name']]['level'], '角色等级不足。')
            self.require(a.get('heaven',0)>=target.get('tier',0), '尚未解锁队伍的洞天阶，请由较低洞天阶的玩家建队。')
            target['members'].append(key)
            return '已加入，请发送「准备出发」。'
        self.require(team,'你还没有队伍，发送「组队秘境 葬龙秘境」。')
        if cmd == '仙途队伍':
            return f"## {team['name']} · {tid}\n" + '\n'.join(f"{self.player(k)['adventure']['profession']} · {'已准备' if k in team['ready'] else '待准备'}" for k in team['members']) + '\n准备出发 · 队伍出发 · 退出队伍'
        if cmd == '退出队伍':
            team['members'].remove(key)
            team['ready'].pop(key,None)
            if not team['members']:
                del teams[tid]
            elif team['leader']==key:
                team['leader']=team['members'][0]
            return '已退出队伍。'
        if cmd == '准备出发':
            team['ready'][key]=build_party(self.player(key),key)
            return '已准备，锁定本次角色与灵宠快照。通关占用一次副本收益；次数已满则无额外收益。队长可发送「队伍出发」。'
        self.require(team['leader']==key,'只有队长可以出发。')
        self.require(all(k in team['ready'] for k in team['members']),'还有队友未准备。')
        party = [u for k in team['members'] for u in team['ready'][k]]
        result = simulate(party,enemies(self.encounter(c.RAIDS[team['name']],team.get('tier',0)),len(team['members']),scale_factor=self._power_scale_factor(self.player(key),key)),random.randrange(2**32))
        messages=[]
        for k in team['members']:
            member=self.player(k)['adventure']
            self.record(member,result,team['name'])
            if result['won']:
                if 'raid:'+team['name'] not in member.setdefault('milestones',[]):
                    member['milestones'].append('raid:'+team['name'])
                messages.append(member['profession']+'：'+self.reward(member,c.RAIDS[team['name']]['level'],tier=team.get('tier',0),partner=self._has_daolv(self.player(k))))
        title=team['name']
        del teams[tid]
        return self.report({'title':title,'result':result})+'\n'+'\n'.join(messages)

    def deep(self,p,key,cmd,arg):
        a=p['adventure']
        if cmd=='深渊收手':
            self.require(a['deep'],'你不在仙途深渊中。')
            floor=a['deep']['floor']
            tier=a['deep'].get('tier',0)
            a['deep']=None
            return '已离开深渊。'+(self.reward(a,floor*3,tier=tier,partner=self._has_daolv(p)) if floor else '尚未通关，没有奖励。')
        if cmd=='仙途深渊' and not a['deep']:
            a['deep']={'tier':a['heaven'],'floor':0,'party':build_party(p,key),'seed':random.randrange(2**32)}
        self.require(a['deep'],'先发送「仙途深渊」。')
        d=a['deep']
        if cmd=='仙途深渊' and d['floor']:
            return f"已通关{d['floor']}/5层。发送「深渊抉择 强攻 / 固守 / 回春」继续，或「深渊收手」。"
        if cmd=='深渊抉择':
            self.require(arg in ('强攻','固守','回春'),'深渊抉择 强攻 / 固守 / 回春')
            for u in d['party']:
                if arg=='强攻': u['atk']*=1.15
                elif arg=='固守': u['defense']*=1.25
                else: u['hp']=min(u['max_hp'],u['hp']+int(u['max_hp']*.45))
        floor=d['floor']+1
        result=simulate(d['party'],enemies(self.encounter(dict(boss=f'深渊守卫·{floor}',scale=1+floor*.3,mechanic=['strike','shield','burn','pack','rage'][floor-1]),d.get('tier',0)),scale_factor=self._power_scale_factor(p,key)),d['seed']+floor)
        text=self.record(a,result,f'仙途深渊 {floor}层')
        if not result['won']:
            passed=d['floor']; a['deep']=None
            return text+'\n'+(self.reward(a,passed,tier=d.get("tier",0),partner=self._has_daolv(p)) if passed else '本轮结束，没有消耗收益次数。')
        d['floor']=floor
        d['party']=[u for u in result['units'] if u['side']==0]
        for u in d['party']: u['burn']=0
        if floor==5:
            if 'deep:5' not in a['milestones']:
                a['milestones'].append('deep:5')
            a['deep']=None
            return text+'\n'+self.reward(a,20,tier=d.get("tier",0),partner=self._has_daolv(p))
        return text+'\n深渊抉择 强攻 / 固守 / 回春，或深渊收手。'

    def duel(self,group,key,cmd,arg):
        duels=self.root()['duels']
        for k in list(duels):
            if duels[k]['expires']<=self.clock(): del duels[k]
        if cmd=='仙途切磋':
            target=self.key(group,arg)
            self.require(target!=key,'不能与自己切磋。')
            self.player(target)
            self.require(target not in duels,'对方已有邀请，请稍后。')
            duels[target]={'challenger':key,'expires':self.clock()+300}
            return '已发起无损切磋邀请，5分钟内由对方发送「接受切磋」或「拒绝切磋」。无资源消耗和奖励。'
        invite=duels.pop(key,None)
        self.require(invite,'没有有效切磋邀请。')
        if cmd=='拒绝切磋': return '已拒绝切磋。'
        other=invite['challenger']
        result=simulate(build_party(self.player(key),key),build_party(self.player(other),other,1),random.randrange(2**32))
        text=self.record(self.player(key)['adventure'],result,'无损论道')
        reverse=deepcopy(result); reverse['won']=result['winner']==1
        self.record(self.player(other)['adventure'],reverse,'无损论道')
        return text+'\n双方存档血量和资源未改变。'
