"""Versioned, data-driven adventure content. No production player data required."""
from ..data import (_DUNGEON_DEFS, TRIBULATION_FAIL_COOLDOWN, MATERIAL_FRAGMENTS,
                    MATERIAL_FRAGMENT_COMBINE, ELEMENT_RESTRAIN)

VERSION = 7
# Each tier is unlocked individually by a trial, never by another player.
HEAVENS = [
    {"name": "初识", "level": 1, "enemy": 1.0, "bonus": 0},
    {"name": "凝灵", "level": 10, "enemy": 1.10, "bonus": 2},
    {"name": "通玄", "level": 20, "enemy": 1.22, "bonus": 4},
    {"name": "御虚", "level": 40, "enemy": 1.36, "bonus": 6},
    {"name": "归真", "level": 60, "enemy": 1.52, "bonus": 8},
    {"name": "乘云", "level": 150, "enemy": 1.70, "bonus": 10},
    {"name": "凌虚", "level": 250, "enemy": 1.90, "bonus": 12},
    {"name": "太清", "level": 400, "enemy": 2.12, "bonus": 14},
    {"name": "无量", "level": 600, "enemy": 2.36, "bonus": 16},
    {"name": "仙门", "level": 800, "enemy": 2.62, "bonus": 18},
]
STARTERS = {"九尾狐": "攻击", "卡比兽": "守护", "七夕青鸟": "辅助"}
PROFESSIONS = {
    "剑修": {"hp": 680, "atk": 110, "def": 30, "speed": 110, "desc": "三回合剑意爆发，破甲斩敌"},
    "体修": {"hp": 880, "atk": 85, "def": 48, "speed": 80, "desc": "护卫队友，护盾与反击"},
    "灵修": {"hp": 710, "atk": 100, "def": 34, "speed": 100, "desc": "受伤时治疗，无需治疗时施法"},
    "魔修": {"hp": 735, "atk": 118, "def": 28, "speed": 96, "desc": "以血催煞，攻击汲取生命"},
}
MAX_LEVEL = 999
REALM_SIZE = 100
REALMS = ["炼气", "筑基", "金丹", "元婴", "化神", "炼虚", "合体", "大乘", "渡劫", "真仙"]
STYLES = {"均衡": "自动选择攻击目标", "破阵": "优先小怪，攻击削弱目标一半护盾，每三回合群攻并再削除75%护盾", "守心": "每三回合净化自身并获得护盾"}
PET_ROLES = ("攻击", "守护", "辅助")
GEAR = {"灵剑": ("weapon", "攻击"), "法衣": ("robe", "生命"), "灵印": ("seal", "防御"),
        "灵冠": ("crown", "防御"), "灵靴": ("boots", "速度"), "玉佩": ("pendant", "生命")}
GEAR_NAMES = " / ".join(GEAR)
# 神通/功法：每个境界一被动，炼气期即送「灵台清明」，其后每次渡劫成功解锁下一境界神通。
# 键值（atk/hp/def/speed）为百分比乘区（0.10 = +10%），在 combat.hero_sheet 末尾叠加。
TACTICS = [
    {"name": "灵台清明", "atk": 0.04, "def": 0.04},
    {"name": "剑意通明", "atk": 0.10},
    {"name": "不灭道体", "hp": 0.15},
    {"name": "身外化身", "speed": 0.08},
    {"name": "大衍神诀", "atk": 0.12, "def": 0.08},
    {"name": "万剑归宗", "atk": 0.18},
    {"name": "金刚不坏", "def": 0.20},
    {"name": "混沌神体", "hp": 0.25},
    {"name": "紫气东来", "atk": 0.10, "hp": 0.10, "def": 0.10, "speed": 0.10},
    {"name": "真仙降世", "atk": 0.25, "hp": 0.25},
]
# 灵根/天赋：创建角色时加权随机；水灵根的「cult」额外加速修士修炼速率。
SPIRIT_ROOTS = [
    {"name": "金灵根", "weight": 15, "atk": 0.10},
    {"name": "木灵根", "weight": 15, "hp": 0.12},
    {"name": "水灵根", "weight": 15, "cult": 0.20},
    {"name": "火灵根", "weight": 15, "atk": 0.08, "speed": 0.05},
    {"name": "土灵根", "weight": 15, "def": 0.12},
    {"name": "天灵根", "weight": 5, "atk": 0.12, "hp": 0.12, "def": 0.10, "speed": 0.08},
    {"name": "杂灵根", "weight": 20},
]

# —— 灵根→五行属性：修士属性由灵根决定，参与战斗伤害克制（相克矩阵见 data.ELEMENT_RESTRAIN）。
# 天/杂灵根为「无属性」：不克制别人、也不被克制，是稳定但无克制加成的选择。
ELEMENT_OF_ROOT = {"金灵根": "金", "木灵根": "木", "水灵根": "水", "火灵根": "火", "土灵根": "土"}
# 属性克制伤害系数：克制敌属伤害×(1+COUNTER_BONUS)，被敌属克制×(1-COUNTER_PENALTY)。
COUNTER_BONUS = 0.20
COUNTER_PENALTY = 0.15

# 敌方五行映射：按 boss 主题指定；未命中的合成敌方（渡劫/器劫/试炼/深渊守卫…）按强度轮换，
# 保证任何战斗都存在可被克制的属性，灵根/宠物属性不再「完全用不上」。
_BOSS_ELEMENTS = {
    "顽皮小猪": "土", "森林狼王": "木", "狂暴妖虎": "金", "骷髅守卫": "暗", "青铜傀儡": "金",
    "怨灵鬼火": "火", "封印魔将": "暗", "堕落骨龙": "暗", "碧水蛟龙": "水", "暗月妖狐": "木",
    "千年僵尸": "土", "炎狱魔犬": "火", "琉璃仙姬": "风", "织女星灵": "光", "深海巨鲲": "水",
    "上古魔神": "暗", "虚空囚徒": "暗", "焚天火凤": "火", "噬神魔影": "暗", "陨落星神": "光",
    "九幽骨龙": "暗", "焚天金乌": "火", "太古玄武": "土", "千机城主": "金",
}
_ELEM_CYCLE = ["金", "木", "水", "火", "土"]

def hero_element(a):
    """修士属性：由灵根决定；天/杂灵根返回 None（无属性，不参与克制）。"""
    return ELEMENT_OF_ROOT.get(a.get("spirit_root", ""))

def element_for(enc):
    """敌方属性：主题 boss 固定五行；合成敌方按强度轮换，保证永远有克制可打。"""
    name = enc.get("boss")
    if name in _BOSS_ELEMENTS:
        return _BOSS_ELEMENTS[name]
    return _ELEM_CYCLE[int(float(enc.get("scale", 0)) * 100) % len(_ELEM_CYCLE)]

def element_line(element):
    """属性克制一句话：`金 · 克木 · 畏火`；无属性返回占位说明。"""
    if not element:
        return "无属性"
    beats = "".join(ELEMENT_RESTRAIN.get(element, []))
    feared = "".join(k for k, lst in ELEMENT_RESTRAIN.items() if element in lst)
    return f"{element} · 克{beats or '—'} · 畏{feared or '—'}"
# 渡劫材料：境界 realm r → r+1 消耗 TRIBULATION_MATERIALS[r]（9 个境界门槛）。
TRIBULATION_MATERIALS = ["筑基丹", "结丹丹", "元婴丹", "化神丹", "炼虚草", "合体道果", "大乘舍利", "渡劫符", "真仙花"]

# 装备品阶：10 品阶对齐 10 境界，品阶靠「装备进阶」解锁、等级不能超过品阶封顶。
GEAR_TIERS = ["凡器", "灵器", "法器", "宝器", "道器", "仙器", "神器", "圣器", "至宝", "鸿蒙"]
# 进阶材料：品阶 t → t+1 消耗 FORGE_MATERIALS[t]（9 个品阶门槛）。
FORGE_MATERIALS = ["玄铁", "精金", "星辰沙", "悟道石", "仙晶", "神血石", "圣辉玉", "混沌精", "鸿蒙紫气"]
FORGE_FAIL_COOLDOWN = 1800  # 与渡劫同 30 分钟；失败不降品阶，仅消耗材料＋冷却。
EXPLORE_COOLDOWN = 900  # 外出历练冷却：免费、不耗体力、不占每日副本收益，约 15 分钟一次。
# 词条：装备进阶随机 roll 一条，属性百分比乘区（与神通/灵根同通道，见 combat.hero_sheet）。
AFFIXES = {
    "破军": {"atk": 0.06}, "御守": {"def": 0.06}, "气血": {"hp": 0.06},
    "疾风": {"speed": 0.06}, "混元": {"atk": 0.02, "hp": 0.02, "def": 0.02, "speed": 0.02},
    "锐金": {"atk": 0.04, "def": 0.02}, "木华": {"hp": 0.04, "speed": 0.02}, "水火": {"atk": 0.03, "hp": 0.03},
}
# 职业差异化装备命名：GEAR_NAME_BY_PROF[职业][slot_key][品阶0..9]，末品阶统一「鸿蒙」前缀。
GEAR_NAME_BY_PROF = {
    "剑修": {
        "weapon": ["凡铁剑", "青霜剑", "紫电剑", "赤霄剑", "龙渊剑", "诛仙剑", "弑神剑", "轩辕剑", "开天剑", "鸿蒙剑"],
        "robe": ["粗布袍", "青丝袍", "云纹袍", "紫绶袍", "玄武袍", "太极袍", "太乙袍", "混沌袍", "万法袍", "鸿蒙袍"],
        "seal": ["剑心石印", "青锋剑印", "云海剑印", "紫电剑印", "玄武剑印", "太极剑印", "太乙剑印", "混沌剑印", "万法剑印", "鸿蒙剑印"],
        "crown": ["青竹剑冠", "青玉剑冠", "云纹剑冠", "紫金剑冠", "玄武剑冠", "太极剑冠", "太乙剑冠", "混沌剑冠", "万法剑冠", "鸿蒙剑冠"],
        "boots": ["青锋草履", "青云剑靴", "流云剑靴", "紫金剑靴", "玄武剑靴", "太极剑靴", "太乙剑靴", "混沌剑靴", "万法剑靴", "鸿蒙剑靴"],
        "pendant": ["剑穗铜佩", "青玉剑佩", "云纹剑佩", "紫金剑佩", "玄武剑佩", "太极剑佩", "太乙剑佩", "混沌剑佩", "万法剑佩", "鸿蒙剑佩"],
    },
    "体修": {
        "weapon": ["铁砂拳套", "玄铁拳套", "精金拳套", "赤铜护腕", "龙鳞护腕", "荒古护腕", "不灭护腕", "混沌拳套", "万劫拳套", "鸿蒙拳套"],
        "robe": ["粗布战甲", "青铁战甲", "云纹战甲", "紫铜战甲", "玄龟战甲", "荒古战甲", "不灭战甲", "混沌战甲", "万劫战甲", "鸿蒙战甲"],
        "seal": ["镇岳石印", "铁山印", "精钢印", "玄铁山印", "荒古山印", "不灭山印", "金刚山印", "混沌山印", "万劫山印", "鸿蒙山印"],
        "crown": ["炼体布冠", "铁骨冠", "赤铜冠", "玄铁战冠", "荒古战冠", "不灭战冠", "金刚战冠", "混沌战冠", "万劫战冠", "鸿蒙战冠"],
        "boots": ["练功草履", "铁骨战靴", "赤铜战靴", "玄铁战靴", "荒古战靴", "不灭战靴", "金刚战靴", "混沌战靴", "万劫战靴", "鸿蒙战靴"],
        "pendant": ["炼体铜佩", "铁骨护佩", "精钢护佩", "玄铁护佩", "荒古护佩", "不灭护佩", "金刚护佩", "混沌护佩", "万劫护佩", "鸿蒙护佩"],
    },
    "灵修": {
        "weapon": ["桃木杖", "青藤杖", "云纹杖", "紫檀杖", "玄冥杖", "太玄杖", "太乙杖", "混沌杖", "万灵杖", "鸿蒙杖"],
        "robe": ["粗布法袍", "青丝法袍", "云纹法袍", "紫绶法袍", "玄冥法袍", "太玄法袍", "太乙法袍", "混沌法袍", "万灵法袍", "鸿蒙法袍"],
        "seal": ["养灵石印", "青藤灵印", "云纹灵印", "紫檀灵印", "玄冥灵印", "太玄灵印", "太乙灵印", "混沌灵印", "万灵法印", "鸿蒙灵印"],
        "crown": ["灵竹冠", "青藤灵冠", "云纹灵冠", "紫檀灵冠", "玄冥灵冠", "太玄灵冠", "太乙灵冠", "混沌灵冠", "万灵法冠", "鸿蒙灵冠"],
        "boots": ["青藤草履", "青藤灵靴", "流云灵靴", "紫檀灵靴", "玄冥灵靴", "太玄灵靴", "太乙灵靴", "混沌灵靴", "万灵法靴", "鸿蒙灵靴"],
        "pendant": ["灵蝶铜佩", "青藤灵佩", "云纹灵佩", "紫檀灵佩", "玄冥灵佩", "太玄灵佩", "太乙灵佩", "混沌灵佩", "万灵法佩", "鸿蒙灵佩"],
    },
    "魔修": {
        "weapon": ["噬魂魔刃", "幽冥魔刃", "血煞魔刃", "修罗魔刃", "天魔刃", "灭仙魔刃", "弑神魔刃", "混沌魔刃", "万劫魔刃", "鸿蒙魔刃"],
        "robe": ["幽冥魔袍", "玄煞魔袍", "血炼魔袍", "修罗魔袍", "天魔袍", "灭仙魔袍", "弑神魔袍", "混沌魔袍", "万劫魔袍", "鸿蒙魔袍"],
        "seal": ["血煞魔印", "幽冥魔印", "摄魂魔印", "修罗魔印", "天魔印", "灭仙魔印", "弑神魔印", "混沌魔印", "万劫魔印", "鸿蒙魔印"],
        "crown": ["玄煞魔冠", "幽冥魔冠", "血炼魔冠", "修罗魔冠", "天魔冠", "灭仙魔冠", "弑神魔冠", "混沌魔冠", "万劫魔冠", "鸿蒙魔冠"],
        "boots": ["踏狱魔靴", "幽冥魔靴", "血影魔靴", "修罗魔靴", "天魔靴", "灭仙魔靴", "弑神魔靴", "混沌魔靴", "万劫魔靴", "鸿蒙魔靴"],
        "pendant": ["摄魂魔佩", "幽冥魔佩", "血煞魔佩", "修罗魔佩", "天魔佩", "灭仙魔佩", "弑神魔佩", "混沌魔佩", "万劫魔佩", "鸿蒙魔佩"],
    },
}


def realm_cap(realm):
    """境界(realm 0~9)对应的等级上限：炼气封顶99，真仙封顶999。"""
    return min(MAX_LEVEL, (realm + 1) * REALM_SIZE - 1)


def tribulation_scale(realm):
    """渡劫天劫强度：随境界线性抬升，保证每次渡劫都是「有挑战的门」。"""
    return 10 + realm * 8


def spirit_root_by_name(name):
    return next((r for r in SPIRIT_ROOTS if r["name"] == name), None)


def tactic_by_name(name):
    return next((t for t in TACTICS if t["name"] == name), None)


def root_bonus(root_name):
    """灵根实际加成一句话：金灵根→攻+10%，水灵根→修炼+20%，杂灵根→无加成。"""
    root = spirit_root_by_name(root_name)
    if not root:
        return "无加成"
    parts = []
    for key, label in (("atk", "攻"), ("def", "防"), ("hp", "血"), ("speed", "速"), ("cult", "修炼")):
        if root.get(key):
            parts.append(f"{label}+{int(root[key] * 100)}%")
    return "、".join(parts) or "无加成"


def tactic_effect(name):
    """神通被动效果一句话：灵台清明→攻+4%/防+4%；无该神通返回空串。"""
    t = tactic_by_name(name)
    if not t:
        return ""
    parts = []
    for key, label in (("atk", "攻"), ("def", "防"), ("hp", "血"), ("speed", "速")):
        if t.get(key):
            parts.append(f"{label}+{int(t[key] * 100)}%")
    return "、".join(parts)


def tier_cap(tier):
    """装备品阶 tier 的等级封顶：凡器 99、灵器 199 … 鸿蒙 999。"""
    return min(MAX_LEVEL, (tier + 1) * REALM_SIZE - 1)


def forge_scale(tier):
    """装备进阶试炼强度：比渡劫弱（门不是主战场），随品阶线性抬升。"""
    return 6 + tier * 6


def tier_mult(tier):
    """装备品阶每级属性系数：凡器 1.0 → 鸿蒙 2.08，进阶带来质变。"""
    return 1 + 0.12 * tier


def gear_name(profession, slot, tier):
    """职业差异化装备名；未知职业/槽位回退到通用中文名。"""
    names = GEAR_NAME_BY_PROF.get(profession, {}).get(slot)
    if names:
        return names[min(max(int(tier), 0), len(names) - 1)]
    for name, (s, _label) in GEAR.items():
        if s == slot:
            return name
    return slot


def gear_slot(profession, name):
    """接受通用装备名或当前职业任一品阶装备名，返回槽位。"""
    generic = GEAR.get(name)
    if generic:
        return generic[0]
    for slot, names in GEAR_NAME_BY_PROF.get(profession, {}).items():
        if name in names:
            return slot
    return None


def gear_command_names(profession, tiers=None):
    """当前职业六件装备名，用于帮助和错误提示。"""
    tiers = tiers or {}
    return " / ".join(
        gear_name(profession, slot, tiers.get(slot, 0))
        for slot, _label in GEAR.values()
    )


def affix_by_name(name):
    return AFFIXES.get(name)
MECHANICS = {
    "strike": ("蓄力重击", "第三回合重击：护盾和治疗有助于生存。"),
    "pack": ("召唤狼群", "第二回合召唤小怪：破阵会优先清理小怪。"),
    "shield": ("机关护盾", "每三回合护盾：破阵能削弱护盾。"),
    "burn": ("灼烧", "每三回合施加灼烧：守心或辅助灵宠可以净化。"),
    "mark": ("锁魂标记", "第三回合重击最虚弱者：体修能护卫队友。"),
    "mirror": ("妖狐分身", "第二回合召唤分身：尽快清理，否则持续受击。"),
    "rage": ("星陨狂暴", "半血后伤害提高：保留生存能力，应对最终阶段。"),
}
_TYPES = ["strike", "pack", "strike", "burn", "shield", "burn", "shield", "mark", "burn", "mirror", "mark", "burn", "mirror", "shield", "strike", "rage", "mark", "burn", "mirror", "rage"]
MAPS = {
    str(i + 1): {"id": str(i + 1), "name": name, "boss": boss,
                 "level": 1 + i * 2, "scale": 1 + i * .32, "mechanic": _TYPES[i],
                 "chapter": i // 4 + 1}
    for i, (name, _, boss) in enumerate(_DUNGEON_DEFS)
}
RAIDS = {
    "葬龙秘境": {"name": "葬龙秘境", "boss": "堕落骨龙", "level": 5, "scale": 1.7, "mechanic": "mark"},
    "机关迷城": {"name": "机关迷城", "boss": "千机城主", "level": 12, "scale": 3.6, "mechanic": "shield"},
    "暗月幽林": {"name": "暗月幽林", "boss": "暗月妖狐", "level": 22, "scale": 6.4, "mechanic": "mirror"},
}
WORLD_BOSSES = [
    {"name": "九幽骨龙", "mechanic": "mark"},
    {"name": "焚天金乌", "mechanic": "burn"},
    {"name": "太古玄武", "mechanic": "shield"},
]
COMMANDS = {
    "创建角色", "结契灵宠", "我的洞天", "洞天突破", "仙途毕业", "灵契仙途", "仙途帮助", "我的修士", "踏入仙途", "选择职业", "修士转职", "修士修炼", "修士突破", "道号", "性别",
    "今日修行", "仙途地图", "历练", "挑战秘境", "修士装备", "锻造", "装备进阶", "洗炼", "修士配装", "灵宠助战", "灵宠专长", "渡劫",
    "组队秘境", "仙途队伍", "加入队伍", "准备出发", "队伍出发", "退出队伍", "世界首领", "讨伐首领",
    "首领奖励", "仙途深渊", "深渊抉择", "深渊收手", "仙途切磋", "接受切磋", "拒绝切磋", "战斗详情", "仙途战绩",
}
#……灵契仙途·修士加点、体力、加速Buff（悟性/体力非宗门专属，放 CONTENT 常量而不是 SECT）
SECT_COMMANDS = {
    "创建宗门", "申请入宗", "同意入宗", "拒绝入宗", "退出宗门",
    "查看宗门", "宗门公告", "宗门升级", "宗门名册", "封官", "免职", "踢出宗门", "宗门榜",
    "宗门任务", "宗门探索", "镇守宗门", "宗门兑换", "星辰阁", "宗门捐献",
}
COMMANDS |= SECT_COMMANDS
READ_COMMANDS = {"灵契仙途", "仙途帮助", "我的修士", "今日修行", "仙途地图", "修士装备", "仙途队伍", "世界首领", "战斗详情", "仙途战绩"}
READ_COMMANDS |= {"查看宗门", "宗门名册", "宗门榜", "宗门任务", "镇守宗门", "宗门兑换"}
# 修士轴补充指令：悟性加点 / 查看体力（幻世仙魔收编）。
COMMANDS |= {"悟性加点", "我的体力", "修士体力", "宗门帮助", "试炼秘境", "外出历练"}
READ_COMMANDS |= {"我的体力", "宗门帮助"}
DAILY_REWARDS = 8

# —— 副本/历练货币（在 reward() 结算，平衡可调；灵石=coin、玄晶=jifen）——
ADV_COIN_BASE = 30        # 副本胜利灵石基准
ADV_COIN_PER_LEVEL = 6    # 每个副本等级额外灵石
ADV_JIFEN_BASE = 10       # 副本胜利玄晶基准
ADV_JIFEN_PER_LEVEL = 3   # 每个副本等级额外玄晶（level//3 取整）
# —— 材料碎片（免费渠道）：副本胜利低概率掉落，每 MATERIAL_FRAGMENT_COMBINE 片合成 1 份材料 ——
ADV_FRAGMENT_RATE = 0.30  # 胜利时掉材料碎片的概率
ADV_FRAGMENT_BUNDLE = (1, 3)  # 每次掉落碎片数量区间

# —— 世界首领附加货币（每次讨伐 + 击杀奖励，平衡可调）——
BOSS_HIT_COIN = 15     # 每次讨伐额外灵石
BOSS_HIT_JIFEN = 5     # 每次讨伐额外玄晶
BOSS_HIT_FRAGMENT_RATE = 0.20  # 每次讨伐掉材料碎片概率
BOSS_KILL_COIN = 120   # 击杀后共同奖励灵石
BOSS_KILL_JIFEN = 40   # 击杀后共同奖励玄晶

# —— 试炼秘境（每日限次玩法，额外灵石/玄晶 + 高碎片率，平衡可调）——
TRIAL_DAILY = 2               # 每日可挑战次数
TRIAL_COIN_BASE = 60          # 通关灵石基准
TRIAL_COIN_PER_LEVEL = 10     # 每修士等级额外灵石
TRIAL_JIFEN_BASE = 20         # 通关玄晶基准
TRIAL_JIFEN_PER_LEVEL = 5     # 每修士等级额外玄晶
TRIAL_CULT_BASE = 50          # 通关修为基准
TRIAL_CULT_PER_LEVEL = 8      # 每修士等级额外修为
TRIAL_FRAGMENT_RATE = 0.5     # 通关掉材料碎片概率（高于普通副本）
TRIAL_FRAGMENT_BUNDLE = (2, 5)  # 通关掉碎片数量区间

# 悟性点可分配上限（随境界提高，防无脑堆一项）：min(INSIGHT_CAP_MAX, INSIGHT_CAP_BASE + realm*INSIGHT_CAP_PER_REALM)
INSIGHT_CAP_MAX = 200
INSIGHT_CAP_BASE = 10
INSIGHT_CAP_PER_REALM = 5


def map_for(key):
    return MAPS.get(key) or next((m for m in MAPS.values() if m["name"] == key), None)


def validate_content():
    assert len(MAPS) == 20 and len(RAIDS) == 3
    assert len(REALMS) == 10 and len(HEAVENS) == 10
    assert len(TACTICS) == len(REALMS)
    assert len(TRIBULATION_MATERIALS) == len(REALMS) - 1
    assert MAX_LEVEL == realm_cap(len(REALMS) - 1)
    assert len(GEAR_TIERS) == 10 and len(FORGE_MATERIALS) == len(GEAR_TIERS) - 1
    assert set(GEAR_NAME_BY_PROF) == set(PROFESSIONS)
    for _prof, slots in GEAR_NAME_BY_PROF.items():
        assert set(slots) == {slot for slot, _ in GEAR.values()}
        assert all(len(v) == len(GEAR_TIERS) for v in slots.values())
    for slot, _label in GEAR.values():
        all_names = [name for slots in GEAR_NAME_BY_PROF.values() for name in slots[slot]]
        assert len(all_names) == len(set(all_names)), f"职业装备名重复：{slot}"
    assert len(AFFIXES) >= 4
    for encounter in [*MAPS.values(), *RAIDS.values()]:
        assert encounter["scale"] > 0 and encounter["level"] > 0
        assert encounter["mechanic"] in MECHANICS
