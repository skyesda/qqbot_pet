"""统一双轨结算的「灵宠轴」基座：给结契灵宠发放养成奖励。

「全面收编」后，宠物侧玩法收编为「修士+灵宠协作出征」，每次作战/副本结算
两条成长轴同时发放：修士拿修为+灵材（AdventureService.reward），灵宠拿经验/仙元
（本模块 pet_reward），双方一起成长，灵宠成长再经 main._pet_to_cultivation 反哺修士修为。

本模块只负责灵宠轴，不碰修士轴公式，避免两处公式漂移。所有函数幂等、纯函数式：
未踏入仙途（无 adventure）或未结契灵宠（无 companion_pet_id）时静默返回空串，
不影响纯宠物老玩家。
"""
from ..pet import add_exp, add_xianyuan


def companion(player: dict):
    """定位结契灵宠：按 adventure.companion_pet_id 在 player['pets'] 中查找，找不到返回 None。"""
    a = player.get("adventure")
    if not a:
        return None
    cid = a.get("companion_pet_id")
    if not cid:
        return None
    for p in player.get("pets", []) or []:
        if p.get("pet_id") == cid:
            return p
    return None


def pet_reward(player: dict, *, exp: int = 0, xianyuan: int = 0, quality_fragment: str = "") -> str:
    """灵宠轴结算：给结契灵宠发经验/仙元，品质碎片进背包。

    返回一句话总结（如「灵宠经验×120 · 品质碎片×1」）；无灵宠或无产出返回空串。
    经验在灵宠飞升后经 add_exp 自动按 1仙元=10w经验 折算，余数保留在 exp 中。
    """
    pet = companion(player)
    if pet is None:
        return ""
    parts = []
    if exp > 0:
        add_exp(pet, exp)
        parts.append(f"灵宠经验×{exp}")
    if xianyuan > 0:
        add_xianyuan(pet, xianyuan)
        parts.append(f"仙元×{xianyuan}")
    if quality_fragment:
        bag = player.setdefault("bag", {})
        bag[quality_fragment] = bag.get(quality_fragment, 0) + 1
        parts.append(f"{quality_fragment}×1")
    return " · ".join(parts)
