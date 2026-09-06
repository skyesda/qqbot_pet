"""Pure bounded combat simulation. Only snapshots are mutated, never saved pets."""
from copy import deepcopy
import math
import random
from .content import PROFESSIONS, VERSION
from .. import data as legacy


def unit(uid, name, side, hp, atk, defense, speed=90, **extra):
    return dict(id=uid, name=name, side=side, hp=max(1, int(hp)), max_hp=max(1, int(hp)),
                atk=max(1, int(atk)), defense=max(0, int(defense)), speed=speed,
                shield=0, burn=0, kind="hero", owner=uid, role="", style="均衡", **extra)


def projection(value, base, weight):
    # Monotonic per-stat compression: preserves growth direction, including reborn Lv1 pets.
    return weight * math.log2(1 + max(0, int(value)) / base)


def hero_sheet(a, player, include_mount=True):
    """修士完整属性面（战斗与战力共用的唯一事实源）。

    基础：职业基础属性 + 等级成长 + 洞天装备 + 坐骑攻击加成；
    进阶：装备/道具带来的 bonus（力量/铁骨/气血/疾风丹）、持久属性 悟性/根骨、性别微调。
    旧存档缺字段一律 setdefault 惰性补默认，零迁移。
    include_mount=False 时排除坐骑攻击加成：用于修士「本体」战力归零坐骑项，
    坐骑改由 power._mount_contrib 以独立 20% 占比计入，避免在英雄本体里双重计。
    """
    spec = PROFESSIONS[a["profession"]]
    growth = 1 + .16 * (a["level"] - 1)
    eq = a.get("equipment", {"weapon": 0, "robe": 0, "seal": 0})
    mount = {} if not include_mount else player.get("mounts", {}).get(player.get("active_mount"), {})
    mount_atk = 0 if not include_mount else projection(mount.get("power", 0), 10000, 5)
    b = a.setdefault("bonus", {"atk": 0, "def": 0, "hp": 0, "speed": 0})
    a.setdefault("gender", "男")
    wudao = a.get("wudao", 0)
    gengu = a.get("gengu", 0)
    hp = (spec["hp"] + eq.get("robe", 0) * 55 + eq.get("pendant", 0) * 35 + b.get("hp", 0)) * growth
    atk = (spec["atk"] + eq.get("weapon", 0) * 7 + mount_atk + b.get("atk", 0) + wudao) * growth
    dfn = (spec["def"] + eq.get("seal", 0) * 5 + eq.get("crown", 0) * 3 + b.get("def", 0) + gengu) * growth
    spd = spec["speed"] + eq.get("boots", 0) * 2 + b.get("speed", 0) + gengu
    # 性别微调：男修 +5% 攻击；女修 +5% 防御与速度。
    if a.get("gender") == "男":
        atk = int(atk * 1.05)
    elif a.get("gender") == "女":
        dfn = int(dfn * 1.05)
        spd = int(spd * 1.05)
    return {"hp": int(hp), "atk": int(atk), "def": int(dfn), "speed": int(spd),
            "wudao": wudao, "gengu": gengu}


def build_party(player, key, side=0):
    a = player["adventure"]
    s = hero_sheet(a, player)
    hero = unit(key, a["name"], side, s["hp"], s["atk"], s["def"], s["speed"])
    hero.update(role=a["profession"], style=a["style"])
    pet = next((p for p in player.get("pets", []) if p.get("pet_id") == a.get("companion_pet_id")), None)
    # A free guide makes the introduction playable without destroying/replacing legacy pets.
    pet = pet or {"nickname": "引路灵蝶", "hp_max": 800, "atk": 50, "def": 40, "intel": 30}
    pg = 1 + .08 * (a["level"] - 1)
    companion = unit(key + ":pet", pet.get("nickname", "灵宠"), side,
                     (180 + projection(pet.get("hp_max", 0), 800, 45)) * pg,
                     (20 + projection(pet.get("atk", 0), 50, 12)) * pg,
                     (12 + projection(pet.get("def", 0), 40, 8)) * pg, 95)
    companion.update(kind="pet", owner=key, role=a["pet_role"],
                     heal_power=(25 + projection(pet.get("intel", 0), 30, 12)) * pg,
                     talent=pet.get("talent"), saved=False)
    legacy_bonus = legacy.ARTIFACTS.get(pet.get('artifact'), {}).get('power', 0)
    legacy_bonus += sum(legacy.SKILLS.get(name, {}).get('power', 0) for name in pet.get('skills', []))
    companion['atk'] += int(projection(legacy_bonus, 10000, 6) * pg)
    # Legacy stat bonuses remain relevant without restoring unbounded instant kills.
    if companion["talent"] == "狂暴怒火":
        companion["atk"] = int(companion["atk"] * 1.3)
    if companion["talent"] == "天火御甲":
        companion["defense"] = int(companion["defense"] * 1.3)
    return [hero, companion]


def enemies(encounter, members=1, scale_factor=1.0):
    s = encounter["scale"] * scale_factor
    boss = unit("boss", encounter["boss"], 1, 850 * s * members, 88 * s,
                22 * s, 85)
    boss.update(kind="boss", mechanic=encounter["mechanic"], spawned=False, target="")
    return [boss]


def simulate(party, opposition, seed, max_rounds=24):
    units = deepcopy(party + opposition)
    rng = random.Random(seed)
    events, metrics = [], {}
    for u in units:
        metrics.setdefault(u["owner"], dict(damage=0, healing=0, absorbed=0, taken=0, cleanses=0))

    def log(round_no, text):
        if len(events) < 180:
            events.append(f"第{round_no}回合 · {text}")

    def alive(side):
        return [u for u in units if u["side"] == side and u["hp"] > 0]

    def hit(source, target, raw, round_no, reflect=True, pierce=0):
        if target["hp"] <= 0:
            return 0
        amount = max(1, int(raw * 100 / (100 + target["defense"] * (1 - pierce))))
        absorbed = min(target["shield"], amount)
        target["shield"] -= absorbed
        metrics[target["owner"]]["absorbed"] += absorbed
        dealt = min(target["hp"], amount - absorbed)
        if target.get("talent") == "不死之体" and not target.get("saved") and dealt >= target["hp"]:
            dealt = max(0, target["hp"] - 1)
            target["saved"] = True
            log(round_no, f"{target['name']}触发不死之体，保住一命")
        target["hp"] -= dealt
        if source['side'] == 0 or source['kind'] == 'boss':
            log(round_no, f"{source['name']}攻击{target['name']}，伤害{dealt}" + (f"（护盾吸收{absorbed}）" if absorbed else ""))
        metrics[source["owner"]]["damage"] += dealt
        metrics[target["owner"]]["taken"] += dealt
        if target["hp"] == 0:
            log(round_no, f"{target['name']}倒下")
        if reflect and target["role"] == "体修" and target["hp"] > 0 and dealt:
            hit(target, source, dealt * .3, round_no, reflect=False)
        return dealt

    def heal(source, target, amount, round_no):
        restored = min(target["max_hp"] - target["hp"], max(0, int(amount)))
        target["hp"] += restored
        metrics[source["owner"]]["healing"] += restored
        if restored:
            log(round_no, f"{source['name']}为{target['name']}恢复{restored}生命")

    rounds = 0
    for turn in range(1, max_rounds + 1):
        rounds = turn
        if not alive(0) or not alive(1):
            break
        order = sorted([u for u in units if u["hp"] > 0], key=lambda u: (-u["speed"], u["id"]))
        for u in order:
            if u["hp"] <= 0:
                continue
            friends, foes = alive(u["side"]), alive(1 - u["side"])
            if not foes:
                break
            if u["style"] == "守心" and turn % 3 == 0:
                if u["burn"]:
                    u["burn"] = 0
                    metrics[u["owner"]]["cleanses"] += 1
                    log(turn, f"{u['name']}守心净化灼烧")
                u["shield"] = min(int(u["max_hp"] * .3), u["shield"] + int(u["max_hp"] * .09))
            weak = min(friends, key=lambda f: f["hp"] / f["max_hp"])
            if u["role"] == "灵修" and weak["hp"] < weak["max_hp"] * .68:
                heal(u, weak, u["atk"] * 1.5, turn)
                if weak["burn"]:
                    weak["burn"] = 0
                    metrics[u["owner"]]["cleanses"] += 1
                continue
            if u["kind"] == "pet":
                if u["role"] == "辅助" or u.get("talent") == "妙手回春":
                    burning = next((f for f in friends if f["burn"]), None)
                    if burning:
                        burning["burn"] = 0
                        metrics[u["owner"]]["cleanses"] += 1
                        log(turn, f"{u['name']}净化了{burning['name']}")
                    if weak["hp"] < weak["max_hp"] * .85:
                        heal(u, weak, u["heal_power"] * 1.8, turn)
                        continue
                if u["role"] == "守护" and turn % 2 == 1:
                    target = next((f for f in friends if f["id"] == u["owner"]), weak)
                    target["shield"] = min(int(target["max_hp"] * .35), target["shield"] + int(u["max_hp"] * .22))
                    log(turn, f"{u['name']}护卫{target['name']}")
            if u["role"] == "体修" and turn % 3 == 1:
                u["shield"] = min(int(u["max_hp"] * .35), u["shield"] + int(u["max_hp"] * .13))
                log(turn, f"{u['name']}展开护体罡气")
            target = min(foes, key=lambda f: (f["kind"] != "add", f["hp"])) if u["style"] == "破阵" else foes[0]
            power = u["atk"] * rng.uniform(.95, 1.05)
            if u["role"] == "剑修" and turn % 3 == 0:
                power *= 1.8
                log(turn, f"{u['name']}剑意爆发")
            if u["kind"] == "boss":
                mechanic = u["mechanic"]
                guards = [f for f in foes if f["role"] == "体修"]
                target = guards[0] if guards else next((f for f in foes if f["kind"] == "hero"), foes[0])
                if mechanic in ("pack", "mirror") and turn == 2 and not u["spawned"]:
                    u["spawned"] = True
                    for i in range(2):
                        add = unit(f"add{i}", "狼群" if mechanic == "pack" else "分身", 1,
                                   u["max_hp"] * .12, u["atk"] * .42, u["defense"] * .4, 75)
                        add.update(kind="add", owner=u["owner"])
                        units.append(add)
                    log(turn, f"{u['name']}召唤两名援军")
                if mechanic == "shield" and turn % 3 == 1:
                    u["shield"] = min(int(u["max_hp"] * .25), u["shield"] + int(u["max_hp"] * .14))
                    log(turn, f"{u['name']}展开机关护盾")
                if mechanic == "burn" and turn % 3 == 1:
                    for f in foes:
                        f["burn"] = 3
                    log(turn, f"{u['name']}释放灼烧")
                if mechanic in ("strike", "mark") and turn % 3 == 0:
                    if mechanic == "mark" and not guards:
                        target = min(foes, key=lambda f: f["hp"] / f["max_hp"])
                    power *= 2
                    log(turn, f"{u['name']}重击{target['name']}")
                if mechanic == "rage" and u["hp"] <= u["max_hp"] * .5:
                    power *= 1.7
            if u["style"] == "破阵" and target["shield"]:
                target["shield"] = int(target["shield"] * .5)
                log(turn, f"{u['name']}破阵削弱{target['name']}护盾")
            if u["style"] == "破阵" and turn % 3 == 0:
                for f in foes:
                    f["shield"] = int(f["shield"] * .25)
                    hit(u, f, power * .85, turn, pierce=.3)
                log(turn, f"{u['name']}破阵横扫")
            else:
                hit(u, target, power * (1.2 if u["role"] == "攻击" else 1), turn,
                    pierce=.25 if u["role"] == "剑修" else 0)
        # Burning is a bounded status tick, not a reflected attack.
        for u in units:
            if u["hp"] > 0 and u["burn"]:
                amount = min(u["hp"], max(1, int(u["max_hp"] * .05)))
                u["hp"] -= amount
                u["burn"] -= 1
                metrics[u["owner"]]["taken"] += amount
        if not alive(0) or not alive(1):
            break
    won = bool(alive(0)) and not alive(1)
    winner = 0 if won else (1 if alive(1) and not alive(0) else None)
    return {"version": VERSION, "seed": seed, "won": won, "winner": winner, "rounds": rounds,
            "events": events, "metrics": metrics, "units": units,
            "reason": "敌方全灭" if won else ("我方倒下" if not alive(0) else "达到回合上限")}
