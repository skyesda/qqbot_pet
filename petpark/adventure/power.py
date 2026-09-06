"""Unified 修士战力：把修士主线与灵宠/道侣贡献折到同一个数。

仙途侧此前没有持久化战力，战斗由 combat.build_party 每次现场推演；老宠物系统用
pet.battle_power(pet) 显示值。两者是两把尺。本模块提供唯一的「仙途战力」，
供排行榜、副本缩放、摸金/神器/秘技/坐骑的强度展示与判定复用。

设计：
- 实时计算，不写入 adventure["power"]（输入随增改路径高频变化，缓存失效面太大）。
- 灵宠贡献沿用 combat.projection() 压缩口径，避免老 battle_power(pet) 的 hp_max*mood
  把宠物战力推到几十万、架空修士主线；被 build_party 忽略的 quality/level/stage/mood
  作为「有界乘区」套上去。
- 总战力 = 修士本体(去坐骑) + 40%×所带灵宠有界贡献 + 20%×坐骑有界贡献，再乘道侣与洞天。
  坐骑在 hero_sheet 里只作战斗攻击加成，战力侧剥离以免双重计，改走独立 20% 项。
- 取「所带灵宠」(companion_pet_id)；无则取贡献最高一只；再无用引路灵蝶兜底。
- 不改动 combat.build_party / enemies（战斗模拟与「战力标尺」分离）。
"""
import math
from .combat import hero_sheet, projection
from .. import data as legacy


def hero_power(a, player, include_mount=True):
    """修士本体：职业基础属性 + 等级成长 + 洞天装备（+ 坐骑战力可选），折成单个数。

    include_mount=False 用于统一战力里取「修士本体」——坐骑不渗在 hero 里，
    改由 _mount_contrib 以独立 20% 占比计入，避免双重计。
    """
    s = hero_sheet(a, player, include_mount=include_mount)
    return int(s["hp"] / 10 + s["atk"] * 2 + s["def"] * 2)


PET_POWER_RATIO = 0.40   # 结契灵宠战力贡献占比
MOUNT_POWER_RATIO = 0.20  # 骑乘坐骑战力贡献占比
_MOUNT_POW_W = 15         # 坐骑有界压缩权重（可调）


def _stage_idx(stage):
    """形态在 STAGES 中的序号+1，None/未知形态兜底为 0（不影响乘区放大）。"""
    if stage in legacy.STAGES:
        return legacy.STAGES.index(stage) + 1
    return 0


def _pet_contrib(pet, lv):
    """单只灵宠战力贡献：与 build_party companion 同压缩口径 + 有界乘区。"""
    raw_atk = projection(pet.get("atk", 0), 50, 12)
    raw_hp = projection(pet.get("hp_max", 0), 800, 45)
    raw_def = projection(pet.get("def", 0), 40, 8)
    raw_intel = projection(pet.get("intel", 0), 30, 12)
    legacy_ps = legacy.ARTIFACTS.get(pet.get("artifact"), {}).get("power", 0)
    legacy_ps += sum(legacy.SKILLS.get(name, {}).get("power", 0) for name in pet.get("skills", []))
    legacy_p = projection(legacy_ps, 10000, 6)
    qual = legacy.QUALITY_GROWTH.get(pet.get("quality", "普通"), 1.0)
    stage = _stage_idx(pet.get("stage"))
    mood = max(1, int(pet.get("mood", 5)))
    love = 1.10 if pet.get("love_state") == "已婚" else 1.0
    mult = (1 + .04 * pet.get("level", 1)) * qual * (1 + .06 * max(stage, 1)) * (1 + .04 * mood) * love
    return int((raw_atk * 2 + raw_hp / 10 + raw_def * 2 + raw_intel + legacy_p)
               * mult * (1 + .08 * (lv - 1)))


def _guide_pet(lv):
    """无宠物时的引路灵蝶兜底，保证未结宠的修士不至于战力归零。"""
    return _pet_contrib({"nickname": "引路灵蝶", "hp_max": 800, "atk": 50,
                         "def": 40, "intel": 30, "mood": 5}, lv)


def _mount_contrib(player, lv):
    """坐骑战力有界折算：与灵宠贡献同尺度压缩，供 20% 占比使用，避免随修士等级膨胀。

    取值与 main.py _mount_power 同源（player["active_mount"] / player["mounts"]），
    但用 projection() 压缩到英雄量级，不取坐骑原始 1万→23万。
    """
    m = player.get("active_mount") or ""
    inst = player.get("mounts", {}).get(m)
    if not inst:
        return 0
    return int(projection(inst.get("power", 0), 10000, _MOUNT_POW_W))


def compute_unified_power(player, key):
    """唯一仙途战力。未踏入仙途返回 0（引导去「踏入仙途」）。

    战力 = 修士本体(不含坐骑) + 40%×所带灵宠有界贡献 + 20%×坐骑有界贡献，再乘道侣与洞天增益。
    所带灵宠按 companion_pet_id；无则取贡献最高一只；再无则引路灵蝶兜底。
    """
    a = player.get("adventure")
    if not a:
        return 0
    hero = hero_power(a, player, include_mount=False)  # 修士本体（去坐骑，坐骑走独立 20% 项）
    pets = player.get("pets", []) or []
    cid = a.get("companion_pet_id")
    pet = next((p for p in pets if p.get("pet_id") == cid), None)
    if pet is None:
        pet = max(pets, key=lambda p: _pet_contrib(p, a["level"]), default=None)
    contrib = _pet_contrib(pet, a["level"]) if pet else _guide_pet(a["level"])
    mount = _mount_contrib(player, a["level"])
    partner = 1.15 if (pets and pets[0].get("love_state") == "已婚") else 1.0  # 道侣加成
    heaven_margin = 1 + .02 * a.get("heaven", 0)
    total = hero + PET_POWER_RATIO * contrib + MOUNT_POWER_RATIO * mount
    return int(total * partner * heaven_margin)


def power_to_scale(power, k=30000):
    """把战力折成怪物缩放系数，带 clamp，避免新手/巨佬失衡。"""
    return max(.6, min(3.0, .6 + power / k))


__all__ = ["compute_unified_power", "hero_power", "power_to_scale"]
