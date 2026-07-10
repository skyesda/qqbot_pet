"""宠物对象的创建与核心数值逻辑。

宠物以 dict 形式保存（方便 JSON 序列化），本模块提供一组纯函数对其进行操作：
创建、精力懒惰恢复、升级成长、进化/飞升/渡劫、战力计算等。
"""

from __future__ import annotations

import random
import time

from . import data


def _exp_to_next(level: int) -> int:
    """升到下一级所需经验。级别越高需求越高。"""
    return 200 + level * 100


def new_pet(species: str, quality: str, gender: str | None = None) -> dict:
    """根据种类与品质生成一只 1 级幼年期宠物。"""
    growth = data.QUALITY_GROWTH.get(quality, 1.0)
    element = data.SPECIES.get(species, random.choice(data.ELEMENTS))
    base_hp = int(800 * growth)
    pet = {
        "species": species,
        "nickname": species,
        "element": element,
        "quality": quality,
        "gender": gender or random.choice(["男", "女"]),
        "stage": data.STAGES[0],
        "level": 1,
        "exp": 0,
        "hp_max": base_hp,
        "hp": base_hp,
        "atk": int(50 * growth),
        "def": int(40 * growth),
        "intel": int(30 * growth),
        "mood": 5,  # 心情 1-5 颗星
        "energy": 100,
        "energy_max": 100,
        "status": "正常",
        # 婚恋
        "love_state": "单身",
        "love_target": None,
        "favor": 0,
        # 装备 / 技能 / 天赋
        "artifact": None,
        "skills": [],
        "talent": None,
        "custom": False,  # 是否为"定制宠物"
        # 计时
        "created_at": int(time.time()),
        "last_energy_ts": int(time.time()),
        "frozen_until": 0,  # 假死/惊魂：在此时间戳前无法操作
        # 进化/飞升计数
        "ascended": False,
    }
    return pet


# --------------------------------------------------------------------------
# 精力恢复（懒惰计算）
# --------------------------------------------------------------------------
def refresh_energy(pet: dict) -> None:
    now = int(time.time())
    last = pet.get("last_energy_ts", now)
    elapsed_min = (now - last) // 60
    if elapsed_min <= 0:
        return
    rate = data.ENERGY_REGEN_PER_MIN
    if pet.get("talent") == "事半功倍":
        rate *= 2
    gain = int(elapsed_min * rate)
    if gain > 0:
        pet["energy"] = min(pet.get("energy_max", 100), pet.get("energy", 0) + gain)
        pet["last_energy_ts"] = last + elapsed_min * 60


def is_frozen(pet: dict) -> bool:
    return int(time.time()) < pet.get("frozen_until", 0)


def frozen_remain_min(pet: dict) -> int:
    return max(0, (pet.get("frozen_until", 0) - int(time.time())) // 60 + 1)


def is_dead(pet: dict) -> bool:
    return pet.get("status") == "死亡" or pet.get("hp", 0) <= 0


# --------------------------------------------------------------------------
# 等级上限 / 经验 / 升级
# --------------------------------------------------------------------------
def level_cap(pet: dict) -> int:
    return data.STAGE_LEVEL_CAP.get(pet.get("stage", "幼年期"), 120)


def _is_ascended(pet: dict) -> bool:
    return data.STAGES.index(pet.get("stage", "")) >= data.STAGES.index("飞升")


def add_exp(pet: dict, amount: int) -> int:
    """增加经验。飞升后自动按 1仙元=10w经验 折算为仙元，余数保留在 exp 中。"""
    amount = max(0, amount)
    if _is_ascended(pet):
        total = pet.get("exp", 0) + amount
        xianyuan_gain = total // data.ASCEND_XIANYUAN_PER_EXP
        pet["xianyuan"] = pet.get("xianyuan", 0) + xianyuan_gain
        pet["exp"] = total % data.ASCEND_XIANYUAN_PER_EXP
        return pet["xianyuan"]
    pet["exp"] = pet.get("exp", 0) + amount
    return pet["exp"]


def add_xianyuan(pet: dict, amount: int) -> int:
    """直接增加仙元（飞升后玩法使用）。"""
    pet["xianyuan"] = pet.get("xianyuan", 0) + max(0, amount)
    return pet["xianyuan"]


def _grow_on_levelup(pet: dict) -> None:
    growth = data.QUALITY_GROWTH.get(pet.get("quality", "普通"), 1.0)
    pet["hp_max"] += int(random.randint(40, 90) * growth)
    pet["atk"] += int(random.randint(3, 8) * growth)
    pet["def"] += int(random.randint(3, 8) * growth)
    pet["intel"] += int(random.randint(2, 6) * growth)
    pet["hp"] = pet["hp_max"]


def _level_cost(pet: dict) -> tuple[int, str]:
    """返回 (数值, 单位) 的下一级消耗。"""
    if _is_ascended(pet):
        return data.ascend_xianyuan_to_next(pet["level"]), "仙元"
    return _exp_to_next(pet["level"]), "经验"


def level_up(pet: dict, times: int = 1) -> tuple[int, str]:
    """尝试升级 times 级，消耗经验/仙元与精力。返回 (实际升级数, 备注)。"""
    cap = level_cap(pet)
    leveled = 0
    note = ""
    ascended = _is_ascended(pet)
    for _ in range(times):
        if pet["level"] >= cap:
            note = f"已达当前阶段满级 Lv{cap}"
            break
        need, unit = _level_cost(pet)
        if ascended:
            if pet.get("xianyuan", 0) < need:
                note = f"仙元不足（升级需 {need}，当前 {pet.get('xianyuan', 0)}）"
                break
        else:
            if pet.get("exp", 0) < need:
                note = f"经验不足（升级需 {need}，当前 {pet.get('exp', 0)}）"
                break
        if pet.get("energy", 0) < 1:
            note = "精力不足"
            break
        if ascended:
            pet["xianyuan"] -= need
        else:
            pet["exp"] -= need
        pet["energy"] -= 1
        pet["level"] += 1
        _grow_on_levelup(pet)
        leveled += 1
    return leveled, note


def auto_level_up(pet: dict) -> int:
    """一键升级：尽可能地连续升级。"""
    total = 0
    while True:
        n, _ = level_up(pet, 1)
        if n == 0:
            break
        total += n
    return total


def exp_enough_to_level(pet: dict) -> bool:
    """经验/仙元是否已满足升下一级（且未到当前阶段满级）。"""
    if pet["level"] >= level_cap(pet):
        return False
    if _is_ascended(pet):
        return pet.get("xianyuan", 0) >= data.ascend_xianyuan_to_next(pet["level"])
    return pet.get("exp", 0) >= _exp_to_next(pet["level"])


def upgrade_quality(pet: dict, target_quality: str = "史诗") -> tuple[bool, str]:
    """提升宠物品质到 target_quality，并按新品质成长系数重新计算基础属性。"""
    current = pet.get("quality", "普通")
    if current == target_quality:
        return False, f"宠物当前已是【{target_quality}】品质，无需使用。"
    if current not in data.QUALITIES or target_quality not in data.QUALITIES:
        return False, "品质信息异常，无法使用。"
    if data.QUALITIES.index(current) >= data.QUALITIES.index(target_quality):
        return False, f"宠物当前品质为【{current}】，不低于【{target_quality}】，无法使用。"
    old_growth = data.QUALITY_GROWTH.get(current, 1.0)
    new_growth = data.QUALITY_GROWTH.get(target_quality, 1.0)
    ratio = new_growth / old_growth
    pet["hp_max"] = int(pet["hp_max"] * ratio)
    pet["hp"] = pet["hp_max"]
    pet["atk"] = int(pet["atk"] * ratio)
    pet["def"] = int(pet["def"] * ratio)
    pet["intel"] = int(pet["intel"] * ratio)
    pet["quality"] = target_quality
    return True, f"🎴 **品质飞升！**由【{current}】进阶为【{target_quality}】，属性全面提升！"


# --------------------------------------------------------------------------
# 进化 / 飞升 / 渡劫
# --------------------------------------------------------------------------
def evolve(pet: dict, force: bool = False) -> tuple[bool, str]:
    stage = pet.get("stage")
    idx = data.STAGES.index(stage)
    if idx >= data.STAGES.index("超究极体"):
        return False, "已是超究极体，请使用『宠物飞升』。"
    if not force and pet["level"] < data.EVOLVE_MIN_LEVEL:
        return False, f"进化需要等级达到 {data.EVOLVE_MIN_LEVEL} 级。"
    pet["stage"] = data.STAGES[idx + 1]
    pet["atk"] *= 2
    pet["def"] *= 2
    pet["level"] = 1
    pet["exp"] = 0
    pet["hp"] = pet["hp_max"]
    # 进化后神器和秘技自动脱落到背包（由调用方处理脱落入包），这里仅清空。
    dropped = {"artifact": pet.get("artifact"), "skills": list(pet.get("skills", []))}
    pet["artifact"] = None
    pet["skills"] = []
    return (
        True,
        f"🌟 **进化成功！**进入【{pet['stage']}】，攻击/防御翻倍，等级重置为 Lv1。"
        + (_drop_text(dropped)),
    )


def _drop_text(dropped: dict) -> str:
    parts = []
    if dropped.get("artifact"):
        parts.append(dropped["artifact"])
    parts.extend(dropped.get("skills", []))
    if parts:
        return f" 脱落至背包：{'、'.join(parts)}"
    return ""


def ascend(pet: dict) -> tuple[bool, str]:
    """飞升：超究极体 -> 飞升。等级保持不变，属性翻倍。"""
    if pet.get("stage") != "超究极体":
        return False, "只有【超究极体】的宠物才能飞升。"
    if pet["level"] < level_cap(pet):
        return False, f"飞升需先升满当前阶段（Lv{level_cap(pet)}）。"
    leftover_exp = pet.get("exp", 0)
    pet["stage"] = "飞升"
    pet["exp"] = leftover_exp % data.ASCEND_XIANYUAN_PER_EXP
    pet["xianyuan"] = leftover_exp // data.ASCEND_XIANYUAN_PER_EXP
    pet["ascended"] = True
    pet["hp_max"] = int(pet["hp_max"] * 2)
    pet["hp"] = pet["hp_max"]
    pet["atk"] = int(pet["atk"] * 2)
    pet["def"] = int(pet["def"] * 2)
    pet["intel"] = int(pet["intel"] * 2)
    return True, (
        f"🕊️ **飞升成功！**进入【飞升】阶段，等级保持 Lv{pet['level']}，"
        f"生命/攻击/防御/智力全部翻倍！\n"
        f"> 剩余经验已折算为 {pet['xianyuan']} 仙元（1 仙元=10w 经验）。"
        if pet['xianyuan'] else
        f"🕊️ **飞升成功！**进入【飞升】阶段，等级保持 Lv{pet['level']}，"
        f"生命/攻击/防御/智力全部翻倍！"
    )


def tribulation(pet: dict) -> tuple[bool, str]:
    """渡劫：飞升 -> 渡劫。等级保持不变，属性再翻倍。"""
    if pet.get("stage") != "飞升":
        return False, "只有【飞升】阶段的宠物才能渡劫。"
    if pet["level"] < level_cap(pet):
        return False, f"渡劫需先升满飞升阶段（Lv{level_cap(pet)}）。"
    if random.random() < 0.3:
        pet["hp"] = max(1, pet["hp_max"] // 2)
        return False, "💥 **渡劫失败！**天劫降下，宠物身受重伤，请恢复后再试。"
    leftover_exp = pet.get("exp", 0)
    pet["stage"] = "渡劫"
    pet["exp"] = leftover_exp % data.ASCEND_XIANYUAN_PER_EXP
    pet["xianyuan"] = pet.get("xianyuan", 0) + leftover_exp // data.ASCEND_XIANYUAN_PER_EXP
    pet["hp_max"] = int(pet["hp_max"] * 2)
    pet["hp"] = pet["hp_max"]
    pet["atk"] = int(pet["atk"] * 2)
    pet["def"] = int(pet["def"] * 2)
    pet["intel"] = int(pet["intel"] * 2)
    return True, (
        f"⚡ **渡劫成功！**进入【渡劫】阶段，等级保持 Lv{pet['level']}，"
        f"生命/攻击/防御/智力再次翻倍！"
    )


# --------------------------------------------------------------------------
# 战力
# --------------------------------------------------------------------------
def artifact_power(pet: dict) -> int:
    art = pet.get("artifact")
    return data.ARTIFACTS.get(art, {}).get("power", 0) if art else 0


def skill_power(pet: dict) -> int:
    return sum(data.SKILLS.get(s, {}).get("power", 0) for s in pet.get("skills", []))


def battle_power(pet: dict) -> int:
    """战力 = 攻击*3 + (智力+防御)*2 + (生命*心情) + 武器加成 + 秘技加成"""
    bp = (
        pet["atk"] * 3
        + (pet["intel"] + pet["def"]) * 2
        + pet["hp_max"] * pet.get("mood", 5)
        + artifact_power(pet)
        + skill_power(pet)
    )
    if pet.get("talent") == "狂暴怒火":
        bp = int(bp * 1.3)
    if pet.get("talent") == "天火御甲":
        bp = int(bp * 1.15)
    return int(bp)


def effective_power_vs(pet: dict, enemy: dict) -> int:
    """考虑属性克制（PK 额外 +50%）后的有效战力。"""
    bp = battle_power(pet)
    if data.restrains(pet.get("element", ""), enemy.get("element", "")):
        bp = int(bp * 1.5)
    return bp


# --------------------------------------------------------------------------
# 文本渲染
# --------------------------------------------------------------------------
def render_pet(pet: dict) -> str:
    refresh_energy(pet)
    gender = {"男": "雄", "女": "雌"}.get(pet.get("gender"), pet.get("gender", "—"))
    love = pet.get("love_state", "单身")
    mood = pet.get("mood", 0)
    stars = "★" * mood + "☆" * (5 - mood)
    skills = "、".join(pet.get("skills", [])) or "无"
    artifact = pet.get("artifact") or "无"
    talent = pet.get("talent") or "未觉醒"
    ascended = _is_ascended(pet)
    if ascended:
        need = data.ascend_xianyuan_to_next(pet["level"])
        resource_line = f"● **仙元**：{pet.get('xianyuan', 0)}/{need}（余 {pet.get('exp', 0)} 经验）"
    else:
        need = _exp_to_next(pet["level"])
        resource_line = f"● **经验**：{pet['exp']}/{need}"
    species_display = pet.get("custom_species_name") or pet.get("species")
    lines = [
        "┏━─★─ 宠 ☆ 物 ─★─┓",
        f"● **等级**：Lv{pet['level']}/{level_cap(pet)}",
        f"● **昵称**：{pet['nickname']}",
        f"● **种类**：{species_display}",
        f"● **属性**：{pet['element']}",
        f"● **阶段**：{pet['stage']}",
        f"● **级别**：{pet['quality']}",
        f"● **战力**：{battle_power(pet)}",
        f"● **智力**：{pet['intel']}",
        f"● **攻击**：{pet['atk']}",
        f"● **防御**：{pet['def']}",
        f"● **秘技**：{skills}",
        f"● **神器**：{artifact}",
        f"● **性别**：{gender}（{love}）",
        f"● **状态**：{pet['status']}",
        f"● **天赋**：{talent}",
        f"● **心情**：{stars}",
        f"● **精力**：{pet['energy']}/{pet['energy_max']}",
        f"● **血量**：{pet['hp']}/{pet['hp_max']}",
        resource_line,
    ]
    if pet.get("love_target"):
        lines.append(f"● **伴侣**：`{pet['love_target']}`　好感度 {pet.get('favor', 0)}")
    tags = pet.get("tags", [])
    if tags:
        lines.append(f"● **标签**：{' '.join(f'[{t}]' for t in tags)}")
    lines.append("┗━─★─ 信 ☆ 息 ─★─┛")
    if is_frozen(pet):
        lines.append(f"> ⚠️ 假死/惊魂中，剩余约 **{frozen_remain_min(pet)}** 分钟无法操作")
    return "\n".join(lines)
