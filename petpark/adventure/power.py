"""Unified 修士战力：把修士主线与灵宠/道侣折到同一个数。

仙途侧此前没有持久化战力，战斗由 combat.build_party 每次现场推演；老宠物系统用
pet.battle_power(pet) 显示值。本模块提供唯一的「仙途战力」，供排行榜、副本缩放、
摸金/神器/秘技/坐骑的强度展示与判定复用。

设计：
- 实时计算，不写入 adventure["power"]（输入随增改路径高频变化，缓存失效面太大）。
- 总战力 = 修士本体(去坐骑) + 15%×所带灵宠真实战力 + 10%×坐骑真实战力，再乘道侣与洞天。
  灵宠取 battle_power(pet)（与「我的宠物」综合战力同源），坐骑取原始 power，不再压缩。
  坐骑在 hero_sheet 里只作战斗攻击加成，战力侧剥离以免双重计，改走独立 10% 项。
- 取「所带灵宠」(companion_pet_id)；无则取贡献最高一只；再无用引路灵蝶兜底。
- 不改动 combat.build_party / enemies（战斗模拟与「战力标尺」分离）。
"""
from .combat import hero_sheet
from ..pet import battle_power


def hero_power(a, player, include_mount=True):
    """修士本体：职业基础属性 + 等级成长 + 洞天装备（+ 坐骑战力可选），折成单个数。

    include_mount=False 用于统一战力里取「修士本体」——坐骑不渗在 hero 里，
    改由 _mount_contrib 以独立 10% 占比计入，避免双重计。
    「修士为主」：本体权重放大（攻防×3、生命/4），压过灵宠真实战力占比。
    """
    s = hero_sheet(a, player, include_mount=include_mount)
    return int(s["hp"] / 4 + s["atk"] * 3 + s["def"] * 3)


PET_POWER_RATIO = 0.15   # 结契灵宠战力贡献占比（修士为主：下调以压过灵宠真实战力）
MOUNT_POWER_RATIO = 0.10  # 骑乘坐骑战力贡献占比（修士为主：下调，坐骑退回锦上添花）


def _pet_contrib(pet, lv):
    """单只灵宠战力贡献：直接取灵宠真实 battle_power（与「我的宠物」综合战力同源）。

    「全面收编」后按用户要求：总战力 = 修士本体 + 15%×所带灵宠真实战力 + 10%×坐骑，
    不再用 projection 压缩（压缩口径会把灵宠战力折成极小值，导致占比看起来像没加上）。
    lv 参数保留以兼容调用签名，实际不再参与。
    """
    return int(battle_power(pet))


def _guide_pet(lv):
    """无宠物时的引路灵蝶兜底，保证未结宠的修士不至于战力归零。"""
    return _pet_contrib({"nickname": "引路灵蝶", "hp_max": 800, "atk": 50,
                         "def": 40, "intel": 30, "mood": 5}, lv)


def _mount_contrib(player, lv):
    """坐骑战力贡献：直接取坐骑真实 power（与「我的坐骑」显示同源）。

    总战力 = 修士本体 + 15%×所带灵宠真实战力 + 10%×坐骑真实战力，不再压缩。
    """
    m = player.get("active_mount") or ""
    inst = player.get("mounts", {}).get(m)
    if not inst:
        return 0
    return int(inst.get("power", 0))


def _unified_components(player, key):
    """统一战力的各分量（供 compute_unified_power 与总战力明细展示共用，保证两处数字一致）。"""
    a = player.get("adventure")
    if not a:
        return None
    hero = hero_power(a, player, include_mount=False)  # 修士本体（去坐骑，坐骑走独立 10% 项）
    pets = player.get("pets", []) or []
    cid = a.get("companion_pet_id")
    pet = next((p for p in pets if p.get("pet_id") == cid), None)
    if pet is None:
        pet = max(pets, key=lambda p: _pet_contrib(p, a["level"]), default=None)
    if pet is None:  # 无宠物：引路灵蝶兜底（name 仅供明细展示）
        pet = {"nickname": "引路灵蝶", "hp_max": 800, "atk": 50, "def": 40, "intel": 30, "mood": 5}
        contrib = _guide_pet(a["level"])
    else:
        contrib = _pet_contrib(pet, a["level"])
    mount = _mount_contrib(player, a["level"])
    partner = 1.15 if (pets and pets[0].get("love_state") == "已婚") else 1.0  # 修士道侣加成（修士与修士结道侣，灵宠为见证）
    heaven_margin = 1 + .02 * a.get("heaven", 0)
    base = hero + PET_POWER_RATIO * contrib + MOUNT_POWER_RATIO * mount
    return {
        "hero": hero,
        "pet_name": pet.get("nickname") or pet.get("name") or "灵宠",
        "pet_contrib": contrib,
        "pet_part": round(PET_POWER_RATIO * contrib),
        "mount_contrib": mount,
        "mount_part": round(MOUNT_POWER_RATIO * mount),
        "partner": partner,
        "heaven_margin": heaven_margin,
        "base": base,
        "total": int(base * partner * heaven_margin),
    }


def compute_unified_power(player, key):
    """唯一仙途战力。未踏入仙途返回 0（引导去「踏入仙途」）。

    战力 = 修士本体(不含坐骑) + 15%×所带灵宠真实战力 + 10%×坐骑真实战力，再乘道侣与洞天增益。
    所带灵宠按 companion_pet_id；无则取贡献最高一只；再无则引路灵蝶兜底。
    """
    c = _unified_components(player, key)
    return c["total"] if c else 0


def power_breakdown(player, key):
    """总战力明细：各分量与原值，供「我的修士」展示，确保数字透明可核（与 compute_unified_power 同源）。"""
    return _unified_components(player, key)


def power_to_scale(power, k=30000):
    """把战力折成怪物缩放系数，带 clamp，避免新手/巨佬失衡。"""
    return max(.6, min(3.0, .6 + power / k))


__all__ = ["compute_unified_power", "power_breakdown", "hero_power", "power_to_scale"]
