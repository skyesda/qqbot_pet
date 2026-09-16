"""Unified 修士战力：把修士主线与灵宠/道侣折到同一个数。

仙途侧此前没有持久化战力，战斗由 combat.build_party 每次现场推演。本模块提供唯一的
「仙途战力」，供排行榜、摸金/神器/秘技/坐骑的强度展示与判定复用。

设计：
- 实时计算，不写入 adventure["power"]（输入随增改路径高频变化，缓存失效面太大）。
- 总战力 = 修士本体(去坐骑) + 灵宠项 + 坐骑项，再乘道侣与洞天。
  本体走 hero_power，灵宠/坐骑先走 combat 那套 projection 压缩后的**实战口径**，
  再按系数折算，三项单位一致可比可加：
  - 灵宠项 = 灵宠实战战力 × 15%，并以「本体 × 0.5」兜底封顶；
  - 坐骑项 = 坐骑给本体带来的实战增量 × 10%。
- 「修士没怎么练、靠一只氪金宠霸榜」是**结构上**被挡住的，不靠封顶：灵宠实战战力按
  修士等级缩放（companion_sheet(pet, a["level"])），等级低 → pg 小 → 宠物项自动小；
  再叠 15% 折算，宠物项稳定只占本体 7%~24%，排行仍由本体主导。封顶那条 min() 实测
  基本不触发（见 PET_CAP_RATIO 注释），只在宠物数值极端膨胀时兜底。
- 这里曾经的坑：取 pet.battle_power()（「我的宠物」面板的养成度显示值，未压缩）计入
  15%。同一只宠两个口径实测差 43 万倍（面板 135.83 亿 vs 实战 3.12 万），全服 28 人里
  9 人的修士本体被压到总战力的 0.00%。**折算系数只能乘在实战口径上，不能乘面板值。**
- 所带灵宠由 combat.active_pet 统一决定，与战斗/修士图同一口径——显示的必须就是打出来的。
- 不改动 combat.build_party / enemies（战斗模拟与「战力标尺」分离）。
"""
from .combat import active_pet, companion_sheet, hero_sheet

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


# 灵宠/坐骑战力折算系数（都乘在**实战口径**上，不是面板养成度）。
#
# 灵宠 15%：灵宠有氪金价值，不能白养，所以照战力线性给贡献；但单靠一只宠不该顶掉
#   整个修士主线，所以只按 15% 折算，并把差额留给本体。
# 坐骑 10%：同乘在实战增量上。
PET_POWER_RATIO = 0.15
MOUNT_POWER_RATIO = 0.10
# 灵宠项的极端上限倍数（保险丝，不是主要机制）：
# 灵宠实战战力按修士等级缩放（companion_sheet(pet, a["level"])），再经 projection
# 对数压缩，所以「本体弱 + 宠强」这个组合天然被压住——实测把宠物原始属性拉到 1e18 倍，
# 实战战力也只到 97236，宠物项/本体比值上限约 1.6，正常区间宠物项只占本体 7%~24%。
# 因此这道 min() 基本不会触发，只在宠物数值继续膨胀到极端时才兜底。
PET_CAP_RATIO = 0.5


def pet_power(pet, level):
    """灵宠战力：把 companion_sheet 的实战属性套修士同款换算，与 hero_power 可直接相加。

    必须走 companion_sheet（战斗同源），不能取 pet.battle_power()——后者是「我的宠物」
    面板的养成度显示值，两个口径能差亿倍，详见 companion_sheet 的说明。
    """
    s = companion_sheet(pet, level)
    return int(s["hp"] / 4 + s["atk"] * 3 + s["def"] * 3)


def _pet_contrib(pet, lv, hero):
    """灵宠战力贡献 = 实战战力 × PET_POWER_RATIO，并以本体 × PET_CAP_RATIO 兜底封顶。

    防「没练修士靠宠霸榜」靠的是结构：lv 取修士等级，宠实战战力随修士等级缩放，
    修士没练的玩家 pg 小、宠物项自动就小。封顶只在宠物数值极端膨胀时兜底，正常不触发。
    """
    raw = pet_power(pet, lv)
    return min(int(raw * PET_POWER_RATIO), int(hero * PET_CAP_RATIO))


def _mount_contrib(a, player):
    """坐骑战力贡献 = 坐骑给本体带来的真实增量 × MOUNT_POWER_RATIO。

    增量取 hero_sheet 含/不含坐骑之差，与战斗实际使用的属性面完全同口径。
    旧实现取 projection(inst.power, 10000, 5) * 3，漏掉成长曲线与神通/灵根乘区，
    实测低估 18.7 倍（power 335300 的坐骑算成 76，真实增量 1419）。
    """
    delta = (hero_power(a, player, include_mount=True)
             - hero_power(a, player, include_mount=False))
    return int(delta * MOUNT_POWER_RATIO)


def _unified_components(player, key):
    """统一战力的各分量（供 compute_unified_power 与总战力明细展示共用，保证两处数字一致）。"""
    a = player.get("adventure")
    if not a:
        return None
    hero = hero_power(a, player, include_mount=False)  # 修士本体（去坐骑，坐骑走独立项）
    pet = active_pet(player)  # 与战斗同一选宠口径，见 active_pet 的说明
    pet_pw = pet_power(pet, a["level"])  # 灵宠实战战力（折算前）
    contrib = _pet_contrib(pet, a["level"], hero)
    mount_pw = hero_power(a, player, include_mount=True) - hero  # 坐骑实战增量（折算前）
    mount = _mount_contrib(a, player)
    # 道侣加成：在任意一只宠物上存在「已婚且道侣指向其他玩家」即生效（双方各自结算，双人都享受 +15%）
    partner = 1.0
    for pt in (player.get("pets") or []):
        if pt.get("love_state") == "已婚":
            tgt = str(pt.get("love_target") or "")
            if tgt and tgt != str(player.get("qq", "")):
                partner = 1.15
                break
    heaven_margin = 1 + .02 * a.get("heaven", 0)
    base = hero + contrib + mount
    return {
        "hero": hero,
        "pet_name": pet.get("nickname") or pet.get("name") or "灵宠",
        "pet_power": pet_pw,      # 灵宠实战战力（折算前，供卡面透明展示）
        "pet_contrib": contrib,   # 计入总战力的灵宠项（×15%，受本体×0.5 封顶）
        "pet_part": contrib,
        "mount_power": mount_pw,  # 坐骑实战增量（折算前）
        "mount_contrib": mount,   # 计入总战力的坐骑项（×10%）
        "mount_part": mount,
        "pet_ratio": PET_POWER_RATIO,
        "pet_cap_ratio": PET_CAP_RATIO,
        "mount_ratio": MOUNT_POWER_RATIO,
        "partner": partner,
        "heaven_margin": heaven_margin,
        "base": base,
        "total": int(base * partner * heaven_margin),
    }


def compute_unified_power(player, key):
    """唯一仙途战力。未踏入仙途返回 0（引导去「踏入仙途」）。

    战力 = 修士本体(不含坐骑) + 灵宠实战战力×15%(上限 本体×0.5) + 坐骑实战增量×10%，
    再乘道侣与洞天增益。三项同走实战口径。所带灵宠由 combat.active_pet 统一决定——
    与战斗、修士图同一选宠口径，未结契或绑定悬空时为引路灵蝶。
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
