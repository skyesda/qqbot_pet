"""Unified 修士战力：把修士主线与灵宠/道侣折到同一个数。

仙途侧此前没有持久化战力，战斗由 combat.build_party 每次现场推演。本模块提供唯一的
「仙途战力」，供排行榜、摸金/神器/秘技/坐骑的强度展示与判定复用。

设计：
- 实时计算，不写入 adventure["power"]（输入随增改路径高频变化，缓存失效面太大）。
- 总战力 = 修士本体(去坐骑) + 灵宠实战战力 + 坐骑实战战力，再乘道侣与洞天。
  **三项同口径、同单位直接相加**：灵宠/坐骑都走 combat 那套 projection 压缩后的实战
  属性，再套与 hero_power 相同的换算，所以三个数可比、可加。
- 这里曾经取 pet.battle_power()（「我的宠物」面板的养成度显示值：生命上限×心情，未压缩）
  计入 15%。同一只宠两个口径实测差 2.7 亿倍（显示 125 万亿 vs 实战攻击 470），结果全服
  28 人里 9 人的修士本体被压到总战力的 0.00%。口径统一后本体中位占 ~78%。
- 所带灵宠由 combat.active_pet 统一决定，与战斗/修士图同一口径——显示的必须就是打出来的。
- 不改动 combat.build_party / enemies（战斗模拟与「战力标尺」分离）。
"""
from .combat import active_pet, companion_sheet, hero_sheet, projection

_NUM_UNITS = (
    (10**100, "古戈尔"), (10**72, "大数"), (10**68, "无量"),
    (10**64, "不可思议"), (10**60, "那由他"), (10**56, "阿僧祇"),
    (10**52, "恒河沙"), (10**48, "极"), (10**44, "载"),
    (10**40, "正"), (10**36, "涧"), (10**32, "沟"),
    (10**28, "穰"), (10**24, "秭"), (10**20, "垓"), (10**16, "京"),
    (10**12, "兆"), (10**8, "亿"), (10**4, "万"),
)


def fmt_power(n) -> str:
    """战力/大数显示：≥1万 按 万→亿→兆→…→古戈尔(10^100) 缩写，保留 2 位小数，
    不足万原样；整数运算，与主站 _short_num 完全同口径，供修士卡/文字版/榜单复用。"""
    try:
        iv = int(n)
    except (TypeError, ValueError, OverflowError):
        return str(n)
    neg = iv < 0
    a = -iv if neg else iv
    if a < 10_000:
        return str(iv)
    sign = "-" if neg else ""
    for threshold, unit in _NUM_UNITS:
        if a >= threshold:
            whole = a // threshold
            wstr = str(whole)
            if len(wstr) >= 5:
                head = wstr[:3]
                return f"{sign}{head[0]}.{head[1:]}e{len(wstr) - 1}{unit}"
            scaled = (a % threshold) * 100
            dec_q, dec_r = divmod(scaled, threshold)
            if dec_r * 2 >= threshold:
                dec_q += 1
            if dec_q >= 100:
                whole += 1
                dec_q = 0
            return f"{sign}{whole}.{dec_q:02d}{unit}"
    return f"{sign}{a}"


def hero_power(a, player, include_mount=True):
    """修士本体：职业基础属性 + 等级成长 + 洞天装备（+ 坐骑战力可选），折成单个数。

    include_mount=False 用于统一战力里取「修士本体」——坐骑不渗在 hero 里，
    改由 _mount_contrib 以同口径独立计入，避免双重计。
    """
    s = hero_sheet(a, player, include_mount=include_mount)
    return int(s["hp"] / 4 + s["atk"] * 3 + s["def"] * 3)


# 灵宠/坐骑战力计入倍率。1.0 = 与修士本体同口径直接相加。
# 口径统一后灵宠实战战力本来就与本体可比（全服中位为本体的 0.30 倍），
# 不需要再挂系数补偿，也**不应该**再加系数——任何系数都会让某一方失真。
PET_POWER_RATIO = 1.0
MOUNT_POWER_RATIO = 1.0


def pet_power(pet, level):
    """灵宠战力：把 companion_sheet 的实战属性套修士同款换算，与 hero_power 可直接相加。

    必须走 companion_sheet（战斗同源），不能取 pet.battle_power()——后者是「我的宠物」
    面板的养成度显示值，两个口径能差亿倍，详见 companion_sheet 的说明。
    """
    s = companion_sheet(pet, level)
    return int(s["hp"] / 4 + s["atk"] * 3 + s["def"] * 3)


def _pet_contrib(pet, lv):
    """单只灵宠战力贡献：与战斗同口径，可与 hero_power 直接比大小。"""
    return pet_power(pet, lv)


def _mount_contrib(player, lv):
    """坐骑战力贡献：与 hero_sheet 同口径——power 经 projection(power,10000,5) 折成
    攻击加成，再按 hero_power 的攻防权重 ×3；不取坐骑原始 power（那是展示值）。
    """
    m = player.get("active_mount") or ""
    inst = player.get("mounts", {}).get(m)
    if not inst:
        return 0
    return int(projection(inst.get("power", 0), 10000, 5) * 3)


def _unified_components(player, key):
    """统一战力的各分量（供 compute_unified_power 与总战力明细展示共用，保证两处数字一致）。"""
    a = player.get("adventure")
    if not a:
        return None
    hero = hero_power(a, player, include_mount=False)  # 修士本体（去坐骑，坐骑走同口径独立项）
    pet = active_pet(player)  # 与战斗同一选宠口径，见 active_pet 的说明
    contrib = _pet_contrib(pet, a["level"])
    mount = _mount_contrib(player, a["level"])
    # 道侣加成：在任意一只宠物上存在「已婚且道侣指向其他玩家」即生效（双方各自结算，双人都享受 +15%）
    partner = 1.0
    for pt in (player.get("pets") or []):
        if pt.get("love_state") == "已婚":
            tgt = str(pt.get("love_target") or "")
            if tgt and tgt != str(player.get("qq", "")):
                partner = 1.15
                break
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

    战力 = 修士本体(不含坐骑) + 灵宠实战战力 + 坐骑实战战力，再乘道侣与洞天增益。
    三项同口径，直接相加。所带灵宠由 combat.active_pet 统一决定——与战斗、修士图
    同一选宠口径，未结契或绑定悬空时为引路灵蝶。
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
