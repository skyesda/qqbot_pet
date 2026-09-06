"""Versioned, data-driven adventure content. No production player data required."""
from ..data import _DUNGEON_DEFS, TRIBULATION_FAIL_COOLDOWN

VERSION = 5
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
# 渡劫材料：境界 realm r → r+1 消耗 TRIBULATION_MATERIALS[r]（9 个境界门槛）。
TRIBULATION_MATERIALS = ["筑基丹", "结丹丹", "元婴丹", "化神丹", "炼虚草", "合体道果", "大乘舍利", "渡劫符", "真仙花"]


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
    "今日修行", "仙途地图", "历练", "挑战秘境", "修士装备", "锻造", "修士配装", "灵宠助战", "灵宠专长", "渡劫",
    "组队秘境", "仙途队伍", "加入队伍", "准备出发", "队伍出发", "退出队伍", "世界首领", "讨伐首领",
    "首领奖励", "仙途深渊", "深渊抉择", "深渊收手", "仙途切磋", "接受切磋", "拒绝切磋", "战斗详情", "仙途战绩",
}
READ_COMMANDS = {"灵契仙途", "仙途帮助", "我的修士", "今日修行", "仙途地图", "修士装备", "仙途队伍", "世界首领", "战斗详情", "仙途战绩"}
DAILY_REWARDS = 8


def map_for(key):
    return MAPS.get(key) or next((m for m in MAPS.values() if m["name"] == key), None)


def validate_content():
    assert len(MAPS) == 20 and len(RAIDS) == 3
    assert len(REALMS) == 10 and len(HEAVENS) == 10
    assert len(TACTICS) == len(REALMS)
    assert len(TRIBULATION_MATERIALS) == len(REALMS) - 1
    assert MAX_LEVEL == realm_cap(len(REALMS) - 1)
    for encounter in [*MAPS.values(), *RAIDS.values()]:
        assert encounter["scale"] > 0 and encounter["level"] > 0
        assert encounter["mechanic"] in MECHANICS
