"""宠物乐园（宠物联盟）静态数据表。

集中存放品质、成长阶段、属性克制、宠物种类、商城物品、仙丹、天赋、秘技、神器、
副本、剧情任务等所有"配置型"数据，方便统一调整与做 `查看种类/属性/...` 一类的查询。
"""

from __future__ import annotations

# ----------------------------------------------------------------------------
# 成长阶段（幼年期 -> 渡劫）
# ----------------------------------------------------------------------------
STAGES = [
    "幼年期",
    "成长期",
    "成熟期",
    "完全体",
    "究极体",
    "超究极体",
    "飞升",
    "渡劫",
]

# 各阶段满级。超究极体及以前为 120；飞升 220；渡劫 999。
STAGE_LEVEL_CAP = {
    "幼年期": 120,
    "成长期": 120,
    "成熟期": 120,
    "完全体": 120,
    "究极体": 120,
    "超究极体": 120,
    "飞升": 220,
    "渡劫": 999,
}

# 进化等级要求（达到该等级即可进化）
EVOLVE_MIN_LEVEL = 60

# ----------------------------------------------------------------------------
# 宠物级别（品质）：普通 -> 混沌
# ----------------------------------------------------------------------------
QUALITIES = [
    "普通",
    "精品",
    "稀有",
    "神级",
    "传说",
    "史诗",
    "圣灵",
    "洪荒",
    "创世",
    "混沌",
]

# 不同品质的基础成长系数（品质越高，每级成长值越高）
QUALITY_GROWTH = {
    "普通": 1.0,
    "精品": 1.4,
    "稀有": 1.9,
    "神级": 2.5,
    "传说": 3.2,
    "史诗": 4.0,
    "圣灵": 5.0,
    "洪荒": 6.2,
    "创世": 7.6,
    "混沌": 9.2,
}

# 砸蛋 / 随机宠物时各品质出现的权重（数值之和≈100，即约等于百分比概率）
# 普通占比最高；圣灵/洪荒/创世/混沌为活动/定制限定，砸蛋概率控制在 0.2% 以下。
QUALITY_WEIGHT = {
    "普通": 60,
    "精品": 19.2,
    "稀有": 10.4,
    "神级": 5.6,
    "传说": 2.4,
    "史诗": 1.28,
    "圣灵": 0.18,
    "洪荒": 0.15,
    "创世": 0.12,
    "混沌": 0.08,
}

# 宠物市场禁止直接购买的品质（留给活动、宠物定制等渠道）
PET_MARKET_BANNED_QUALITIES = {"圣灵", "洪荒", "创世", "混沌"}

# ----------------------------------------------------------------------------
# 属性与克制：金-木-水-火-土-金；风-雷-冰-风；光-暗-光
# 表示 key 克制 value 列表中的属性，PK 时额外 +50% 战力。
# ----------------------------------------------------------------------------
ELEMENTS = ["金", "木", "水", "火", "土", "风", "雷", "冰", "光", "暗"]

ELEMENT_RESTRAIN = {
    "金": ["木"],
    "木": ["土"],
    "土": ["水"],
    "水": ["火"],
    "火": ["金"],
    "风": ["雷"],
    "雷": ["冰"],
    "冰": ["风"],
    "光": ["暗"],
    "暗": ["光"],
}


def restrains(attacker: str, defender: str) -> bool:
    """attacker 属性是否克制 defender 属性。"""
    return defender in ELEMENT_RESTRAIN.get(attacker, [])


# ----------------------------------------------------------------------------
# 宠物种类（取自参考宠物图）。每个种类给定一个默认属性，便于克制计算。
# ----------------------------------------------------------------------------
# species -> 默认属性
SPECIES = {
    "七夕青鸟": "风",
    "九尾狐": "火",
    "五彩蜂": "风",
    "亚斯": "火",
    "代发": "冰",
    "佑碧": "水",
    "倒萨": "冰",
    "冥王龙": "暗",
    "冰妖狐": "冰",
    "凤凰": "火",
    "利欧": "火",
    "卡比兽": "土",
    "古拉顿": "土",
    "君主蛇": "木",
    "呆河马": "水",
    "哮天犬": "金",
    "唐伯虎": "金",
    "喵小将": "金",
    "喷火龙": "火",
    "固拉多": "土",
    "土台龟": "木",
    "塔尔": "水",
    "墨海马": "水",
    "大力蛙": "水",
    "大寺": "火",
    "大甲": "金",
    "大葱鸭": "水",
    "大钢蛇": "金",
    "天蝎王": "土",
    "奇犽": "光",
    "妖冥": "暗",
    "宫奇": "暗",
    "宙斯": "雷",
    "小岩": "土",
    "尼尔": "火",
    "布谷鸟": "风",
    "帝纳": "土",
    "忍蛙": "水",
    "快龙": "风",
    "急冻鸟": "冰",
    "战斗兔": "金",
    "战锤龙": "土",
    "斗笠菇": "木",
    "朱雀": "火",
    "暴鲤龙": "水",
    "梦幻": "光",
    "梭鲁": "光",
    "水君": "水",
    "水蛭": "水",
    "水龙": "水",
    "沙瓦郎": "暗",
    "洛奇亚": "风",
    "洛托姆": "雷",
    "海星": "水",
    "火焰鸟": "火",
    "火焰鸡": "火",
    "火神虫": "火",
    "烈咬陆鲨": "土",
    "烈焰猴": "火",
    "烈焰马": "火",
    "牛魔王": "火",
    "独角犀": "土",
    "独角狼": "冰",
    "玛细拉": "水",
    "电龙": "雷",
    "皮卡丘": "雷",
    "皮皮": "光",
    "皮皮虾": "水",
    "皮神": "雷",
    "盔甲鸟": "金",
    "盖欧卡": "水",
    "穿山甲": "土",
    "紫幻": "暗",
    "纱奈朵": "光",
    "绿毛虫": "木",
    "美女猫": "金",
    "美纳斯": "水",
    "耿鬼": "暗",
    "胡地": "光",
    "苍龙": "木",
    "莱伊": "暗",
    "莲小妖": "木",
    "蓝羚猫": "风",
    "蓝龙": "水",
    "蜥蜴王": "木",
    "裂空座": "风",
    "西斯": "暗",
    "超梦": "光",
    "钢龙": "金",
    "野猪精": "土",
    "长耳兔": "金",
    "闪电鸟": "雷",
    "雪拉比": "木",
    "雷朵": "雷",
    "霸王螺": "水",
    "青焰驹": "火",
    "青龙": "木",
    "飞天鼠": "风",
    "魔王菇": "木",
    "麒麟": "光",
}

SPECIES_NAMES = list(SPECIES.keys())

# ----------------------------------------------------------------------------
# 宠物专域 / 宠物市场可购买的宠物（积分价格，取自参考宠物商城截图）
# ----------------------------------------------------------------------------
PET_MARKET = {
    "九尾狐": 15000,
    "五彩蜂": 30000,
    "亚斯": 45000,
    "代发": 60000,
    "佑碧": 75000,
    "倒萨": 90000,
    "冥王龙": 105000,
    "凤凰": 120000,
    "利欧": 135000,
    "卡比兽": 150000,
    "古拉顿": 165000,
    "君主蛇": 180000,
    "呆河马": 195000,
    "哮天犬": 210000,
    "唐伯虎": 225000,
    "喵小将": 240000,
    "喷火龙": 255000,
    "固拉多": 270000,
    "土台龟": 285000,
    "塔尔": 300000,
}

# 宠物市场改卖品质卡与变种卡（不再按物种直售宠物）。
# 品质卡定价（积分）：普通→史诗；圣灵/洪荒/创世/混沌仍为活动/定制限定，不可购买。
# 卡片可 `使用 XXX卡 召唤` 随机召唤同品质宠物，或 `使用 XXX卡 宠物名` 给指定宠物升品质。
PET_MARKET_CARDS = {
    "普通卡": 2000,
    "精品卡": 6000,
    "稀有卡": 15000,
    "神级卡": 33000,
    "传说卡": 80000,
    "史诗卡": 200000,
}

# 变种卡：指定宠物后随机改变其种类（保留等级/品质/属性）。
SPECIES_CHANGE_CARD = {
    "name": "变种卡",
    "price": 50000,
    "desc": "随机改变一只宠物的种类（保留等级/品质/属性）。",
}

# ----------------------------------------------------------------------------
# 物品系统
# 货币：jifen（积分）、coin（金币）
# 物品定义：name -> {price, currency, category, usable, desc, effect}
#   effect 由 main.py 中的物品使用逻辑解释。
# ----------------------------------------------------------------------------
CURRENCY_JIFEN = "积分"
CURRENCY_COIN = "金币"
CURRENCY_DIAMOND = "钻石"

ITEMS = {
    # ---- 宠物商城 ----
    "红药水": {
        "price": 200,
        "currency": CURRENCY_COIN,
        "category": "药品",
        "usable": True,
        "desc": "恢复宠物 300 点血量。",
        "effect": {"heal_hp": 300},
    },
    "蓝药水": {
        "price": 1000,
        "currency": CURRENCY_COIN,
        "category": "药品",
        "usable": True,
        "desc": "恢复宠物 10 点精力。",
        "effect": {"heal_energy": 10},
    },
    "改名卡": {
        "price": 2000,
        "currency": CURRENCY_JIFEN,
        "category": "道具",
        "usable": False,
        "desc": "改名所需道具（首次改名免费）。",
        "effect": {},
    },
    "变性药水": {
        "price": 3000,
        "currency": CURRENCY_JIFEN,
        "category": "道具",
        "usable": False,
        "desc": "改变宠物性别所需道具。",
        "effect": {},
    },
    "变种卡": {
        "price": 50000,
        "currency": CURRENCY_JIFEN,
        "category": "道具",
        "usable": True,
        "desc": "把当前宠物变为指定种类（保留等级/品质/属性）。用法：`使用 变种卡 <宠物种类>`，必须写现有品种；发送《宠物种类》查看可选品种。",
        "effect": {"species_change": "random"},
    },
    # ---- 道具商城 ----
    "永恒钻戒": {
        "price": 800,
        "currency": CURRENCY_JIFEN,
        "category": "道具",
        "usable": False,
        "desc": "宠物求婚所需消耗的信物。",
        "effect": {},
    },
    "三明治": {
        "price": 1600,
        "currency": CURRENCY_JIFEN,
        "category": "药品",
        "usable": True,
        "desc": "解除『肌饿』状态。",
        "effect": {"cure": "肌饿"},
    },
    "大补丸": {
        "price": 2400,
        "currency": CURRENCY_JIFEN,
        "category": "药品",
        "usable": True,
        "desc": "解除『虚弱』状态。",
        "effect": {"cure": "虚弱"},
    },
    "镇定剂": {
        "price": 3200,
        "currency": CURRENCY_JIFEN,
        "category": "药品",
        "usable": True,
        "desc": "解除『亢奋』状态。",
        "effect": {"cure": "亢奋"},
    },
    "疏筋丸": {
        "price": 4000,
        "currency": CURRENCY_JIFEN,
        "category": "药品",
        "usable": True,
        "desc": "解除『麻痹』状态。",
        "effect": {"cure": "麻痹"},
    },
    "清醒剂": {
        "price": 4800,
        "currency": CURRENCY_JIFEN,
        "category": "药品",
        "usable": True,
        "desc": "解除『沉眠』状态。",
        "effect": {"cure": "沉眠"},
    },
    "解毒剂": {
        "price": 5600,
        "currency": CURRENCY_JIFEN,
        "category": "药品",
        "usable": True,
        "desc": "解除『中毒』状态。",
        "effect": {"cure": "中毒"},
    },
    "九转还魂丹": {
        "price": 6400,
        "currency": CURRENCY_JIFEN,
        "category": "药品",
        "usable": True,
        "desc": "复活死亡的宠物并回满血量。",
        "effect": {"revive": True},
    },
    "进化神石": {
        "price": 7200,
        "currency": CURRENCY_JIFEN,
        "category": "道具",
        "usable": False,
        "desc": "宠物进化时消耗的材料（需 Lv60+，发送『宠物进化』时自动消耗 1 颗）。",
        "effect": {},
    },
    "万能宝石": {
        "price": 8000,
        "currency": CURRENCY_JIFEN,
        "category": "宝石",
        "usable": False,
        "desc": "可镶嵌到装备上的万能宝石。",
        "effect": {},
    },
    "神器图纸": {
        "price": 50000,
        "currency": CURRENCY_JIFEN,
        "category": "材料",
        "usable": False,
        "desc": "记载神器打造方法的图纸，打造神器时消耗。",
        "effect": {},
    },
    "小精力瓶": {
        "price": 5,
        "currency": CURRENCY_DIAMOND,
        "category": "药品",
        "usable": True,
        "desc": "恢复 10 点精力。",
        "effect": {"heal_energy": 10},
    },
    "普通经验书": {
        "price": 1000,
        "currency": CURRENCY_JIFEN,
        "category": "药品",
        "usable": True,
        "desc": "经验 +200。",
        "effect": {"add_exp": 200},
    },
    "五色药": {
        "price": 10400,
        "currency": CURRENCY_JIFEN,
        "category": "药品",
        "usable": True,
        "desc": "血量上限 +300 并回满血量。",
        "effect": {"add_hp_max": 300},
    },
    "聚灵丹": {
        "price": 50000,
        "currency": CURRENCY_JIFEN,
        "category": "仙丹",
        "usable": True,
        "desc": "经验 +10 万。",
        "effect": {"add_exp": 100000},
    },
    "经验丹": {
        "price": 0,
        "currency": CURRENCY_JIFEN,
        "category": "仙丹",
        "usable": True,
        "desc": "使用后宠物获得 5000 经验。",
        "effect": {"add_exp": 5000},
    },
    "神秘宝箱": {
        "price": 0,
        "currency": CURRENCY_JIFEN,
        "category": "道具",
        "usable": True,
        "desc": "宗门贡献兑换的神秘宝箱，使用后随机开出金币/积分/道具。",
        "effect": {"mystery_box": True},
    },
    # ---- 属性符（永久增加属性，钻石计价）----
    "智力宝符": {
        "price": 100,
        "currency": CURRENCY_DIAMOND,
        "category": "符箓",
        "usable": True,
        "desc": "使用后永久增加 10 点智力。",
        "effect": {"add_intel": 10},
    },
    "智力仙符": {
        "price": 480,
        "currency": CURRENCY_DIAMOND,
        "category": "符箓",
        "usable": True,
        "desc": "使用后永久增加 50 点智力。",
        "effect": {"add_intel": 50},
    },
    "智力神符": {
        "price": 900,
        "currency": CURRENCY_DIAMOND,
        "category": "符箓",
        "usable": True,
        "desc": "使用后永久增加 100 点智力。",
        "effect": {"add_intel": 100},
    },
    "精力宝符": {
        "price": 100,
        "currency": CURRENCY_DIAMOND,
        "category": "符箓",
        "usable": True,
        "desc": "使用后永久增加 10 点精力（上限）。",
        "effect": {"add_energy_max": 10},
    },
    "精力仙符": {
        "price": 480,
        "currency": CURRENCY_DIAMOND,
        "category": "符箓",
        "usable": True,
        "desc": "使用后永久增加 50 点精力（上限）。",
        "effect": {"add_energy_max": 50},
    },
    "精力神符": {
        "price": 900,
        "currency": CURRENCY_DIAMOND,
        "category": "符箓",
        "usable": True,
        "desc": "使用后永久增加 100 点精力（上限）。",
        "effect": {"add_energy_max": 100},
    },
    "攻击宝符": {
        "price": 100,
        "currency": CURRENCY_DIAMOND,
        "category": "符箓",
        "usable": True,
        "desc": "使用后永久增加 10 点攻击。",
        "effect": {"add_atk": 10},
    },
    "攻击仙符": {
        "price": 480,
        "currency": CURRENCY_DIAMOND,
        "category": "符箓",
        "usable": True,
        "desc": "使用后永久增加 50 点攻击。",
        "effect": {"add_atk": 50},
    },
    "攻击神符": {
        "price": 900,
        "currency": CURRENCY_DIAMOND,
        "category": "符箓",
        "usable": True,
        "desc": "使用后永久增加 100 点攻击。",
        "effect": {"add_atk": 100},
    },
    "防御宝符": {
        "price": 100,
        "currency": CURRENCY_DIAMOND,
        "category": "符箓",
        "usable": True,
        "desc": "使用后永久增加 10 点防御。",
        "effect": {"add_def": 10},
    },
    "防御仙符": {
        "price": 480,
        "currency": CURRENCY_DIAMOND,
        "category": "符箓",
        "usable": True,
        "desc": "使用后永久增加 50 点防御。",
        "effect": {"add_def": 50},
    },
    "防御神符": {
        "price": 900,
        "currency": CURRENCY_DIAMOND,
        "category": "符箓",
        "usable": True,
        "desc": "使用后永久增加 100 点防御。",
        "effect": {"add_def": 100},
    },
    "生命宝符": {
        "price": 100,
        "currency": CURRENCY_DIAMOND,
        "category": "符箓",
        "usable": True,
        "desc": "使用后永久增加 10 点生命（上限）并回满。",
        "effect": {"add_hp_max": 10},
    },
    "生命仙符": {
        "price": 480,
        "currency": CURRENCY_DIAMOND,
        "category": "符箓",
        "usable": True,
        "desc": "使用后永久增加 50 点生命（上限）并回满。",
        "effect": {"add_hp_max": 50},
    },
    "生命神符": {
        "price": 900,
        "currency": CURRENCY_DIAMOND,
        "category": "符箓",
        "usable": True,
        "desc": "使用后永久增加 100 点生命（上限）并回满。",
        "effect": {"add_hp_max": 100},
    },
    # ---- 活动道具（中元功德商店等）----
    "自动修炼卡": {
        "price": 0,
        "currency": CURRENCY_DIAMOND,
        "category": "道具",
        "usable": True,
        "desc": "使用后获得 1 天自动修炼权限。",
        "effect": {"add_cultivation_days": 1},
    },
    # ---- 深渊道具 ----
    "净化药水": {
        "price": 5000,
        "currency": CURRENCY_JIFEN,
        "category": "药品",
        "usable": True,
        "desc": "清除 5 点深渊侵蚀，恢复理智后再战深渊。",
        "effect": {"clear_abyss_corruption": 5},
    },
    # ---- 精力瓶（恢复精力，钻石计价）----
    "中精力瓶": {
        "price": 20,
        "currency": CURRENCY_DIAMOND,
        "category": "药品",
        "usable": True,
        "desc": "恢复 50 点精力。",
        "effect": {"heal_energy": 50},
    },
    "大精力瓶": {
        "price": 35,
        "currency": CURRENCY_DIAMOND,
        "category": "药品",
        "usable": True,
        "desc": "恢复 100 点精力。",
        "effect": {"heal_energy": 100},
    },
    # ---- 相思豆（婚恋）----
    "相思豆": {
        "price": 0,
        "currency": CURRENCY_JIFEN,
        "category": "道具",
        "usable": False,
        "desc": "喂给恋爱中的宠物可增加 50 点好感度。",
        "effect": {},
    },
    # ---- 品质提升卡（宠物每达到 60 级自动赠送史诗卡，其余可通过活动/奖品获得）----
    # 低阶卡（普通~传说）主要由「品质碎片合成」/ 砸蛋产出，可对指定宠物升品质或召唤随机宠物。
    "普通卡": {
        "price": 0,
        "currency": CURRENCY_JIFEN,
        "category": "道具",
        "usable": True,
        "desc": "可召唤一只【普通】品质随机宠物，或对指定宠物提升该品质。",
        "effect": {"upgrade_quality": "普通"},
    },
    "精品卡": {
        "price": 0,
        "currency": CURRENCY_JIFEN,
        "category": "道具",
        "usable": True,
        "desc": "将宠物品质提升为【精品】，属性随品质同步飞跃。精品及以上品质无法使用。",
        "effect": {"upgrade_quality": "精品"},
    },
    "稀有卡": {
        "price": 0,
        "currency": CURRENCY_JIFEN,
        "category": "道具",
        "usable": True,
        "desc": "将宠物品质提升为【稀有】，属性随品质同步飞跃。稀有及以上品质无法使用。",
        "effect": {"upgrade_quality": "稀有"},
    },
    "神级卡": {
        "price": 0,
        "currency": CURRENCY_JIFEN,
        "category": "道具",
        "usable": True,
        "desc": "将宠物品质提升为【神级】，属性随品质同步飞跃。神级及以上品质无法使用。",
        "effect": {"upgrade_quality": "神级"},
    },
    "传说卡": {
        "price": 0,
        "currency": CURRENCY_JIFEN,
        "category": "道具",
        "usable": True,
        "desc": "将宠物品质提升为【传说】，属性随品质同步飞跃。传说及以上品质无法使用。",
        "effect": {"upgrade_quality": "传说"},
    },
    "史诗卡": {
        "price": 0,
        "currency": CURRENCY_JIFEN,
        "category": "道具",
        "usable": True,
        "desc": "将宠物品质提升为【史诗】，属性随品质同步飞跃。史诗及以上品质无法使用。",
        "effect": {"upgrade_quality": "史诗"},
    },
    "圣灵卡": {
        "price": 0,
        "currency": CURRENCY_JIFEN,
        "category": "道具",
        "usable": True,
        "desc": "将宠物品质提升为【圣灵】，属性随品质同步飞跃。圣灵及以上品质无法使用。",
        "effect": {"upgrade_quality": "圣灵"},
    },
    "洪荒卡": {
        "price": 0,
        "currency": CURRENCY_JIFEN,
        "category": "道具",
        "usable": True,
        "desc": "将宠物品质提升为【洪荒】，属性随品质同步飞跃。洪荒及以上品质无法使用。",
        "effect": {"upgrade_quality": "洪荒"},
    },
    "创世卡": {
        "price": 0,
        "currency": CURRENCY_JIFEN,
        "category": "道具",
        "usable": True,
        "desc": "将宠物品质提升为【创世】，属性随品质同步飞跃。创世及以上品质无法使用。",
        "effect": {"upgrade_quality": "创世"},
    },
    "混沌卡": {
        "price": 0,
        "currency": CURRENCY_JIFEN,
        "category": "道具",
        "usable": True,
        "desc": "将宠物品质提升为【混沌】，属性随品质同步飞跃。已是混沌品质无法使用。",
        "effect": {"upgrade_quality": "混沌"},
    },
    # ---- 多宠物系统 ----
    "宠物席位卡": {
        "price": 0,
        "currency": CURRENCY_JIFEN,
        "category": "道具",
        "usable": True,
        "desc": "使用后永久增加1个宠物席位（上限10个），仅可通过管理员卡密获得。",
        "effect": {"add_pet_slot": 1},
    },
    "宠物卡": {
        "price": 0,
        "currency": CURRENCY_JIFEN,
        "category": "材料",
        "usable": True,
        "desc": "神秘的宠物卡，使用『使用 宠物卡 召唤』时随机获得一只宠物（品质与种类均随机，与品质卡互相独立）。",
        "effect": {"summon_pet_card": True},
    },
    # 生辰盛典专属：开启主宠「定制」权限（自定义名称/图片），并晋升混沌、加「定制」标签。
    "宠物定制卡": {
        "price": 0,
        "currency": CURRENCY_JIFEN,
        "category": "道具",
        "usable": True,
        "desc": "开启主宠「定制」权限（自定义名称/图片），并晋升为【混沌】品质、加「定制」标签。用法：『使用 宠物定制卡』。",
        "effect": {"custom_pet": True},
    },
}

# 品质碎片（砸蛋副产物）：同品质 10 片兑换 1 张该品质卡。不可直接使用，仅作合成素材。
FRAGMENT_TO_CARD = 10  # 兑换 1 张卡所需碎片数
for _q in QUALITIES:
    ITEMS[f"{_q}碎片"] = {
        "price": 0,
        "currency": CURRENCY_JIFEN,
        "category": "材料",
        "usable": False,
        "desc": f"【{_q}】品质碎片，{FRAGMENT_TO_CARD} 片可兑换 1 张【{_q}卡】。",
        "effect": {},
    }

# 品质卡合成链（10 张低一级卡合成 1 张高一级卡；顶级创世→混沌需 20 张，刻意加码）。
# 仅对实际存在的品质卡建立映射；缺少低级卡时链从第一个存在的卡开始。
TOP_CARD_NAME = "混沌卡"
TOP_CARD_COST = 20  # 创建世→混沌的合成张数专门上调，避免顶级太易得
QUALITY_CARD_UPGRADE: dict[str, tuple[str, int]] = {}
for _i, _q in enumerate(QUALITIES):
    _src_card = f"{_q}卡"
    if _i + 1 < len(QUALITIES):
        _dst_card = f"{QUALITIES[_i + 1]}卡"
        if _src_card in ITEMS and _dst_card in ITEMS:
            _need = TOP_CARD_COST if _dst_card == TOP_CARD_NAME else FRAGMENT_TO_CARD
            QUALITY_CARD_UPGRADE[_dst_card] = (_src_card, _need)

# 在品质卡说明里追加合成信息
for _dst_card, (_src_card, _need) in QUALITY_CARD_UPGRADE.items():
    if _dst_card in ITEMS:
        ITEMS[_dst_card]["desc"] = ITEMS[_dst_card]["desc"].rstrip("。") + f"，可由 {_need} 张【{_src_card}】合成。"
    if _src_card in ITEMS:
        ITEMS[_src_card]["desc"] = ITEMS[_src_card]["desc"].rstrip("。") + f"，{_need} 张可合成 1 张【{_dst_card}】。"

# ----------------------------------------------------------------------------
# 异常状态 -> 解除物品
# ----------------------------------------------------------------------------
STATUSES = ["正常", "死亡", "中毒", "沉眠", "麻痹", "亢奋", "虚弱", "肌饿"]

STATUS_CURE_ITEM = {
    "中毒": "解毒剂",
    "沉眠": "清醒剂",
    "麻痹": "疏筋丸",
    "亢奋": "镇定剂",
    "虚弱": "大补丸",
    "肌饿": "三明治",
}

# ----------------------------------------------------------------------------
# 天赋系统
# name -> desc。need_custom 表示必须"定制宠物"才有机会觉醒。
# ----------------------------------------------------------------------------
TALENTS = {
    "妙手摘星": {
        "desc": "成功击杀对方宠物时有 40% 概率偷到对方背包内某随机物品。",
        "need_custom": False,
    },
    "神隐遁术": {
        "desc": "被比自己战力高的宠物攻击时，有一半概率逃脱。",
        "need_custom": False,
    },
    "不死之体": {"desc": "宠物永远不会死亡。", "need_custom": False},
    "天火御甲": {
        "desc": "战斗时提高 30% 防御力，免疫所有负面效果。",
        "need_custom": False,
    },
    "狂暴怒火": {"desc": "战斗时额外提升 30% 的攻击力。", "need_custom": False},
    "蝶逆轮回": {
        "desc": "满血状态下与比自己战力高的宠物战斗时有 30% 概率触发，自身剩余一滴血，秒杀敌方。",
        "need_custom": False,
    },
    "七星化海": {"desc": "战斗结束后会额外增加 10%-30% 的经验。", "need_custom": False},
    "鸿运当头": {"desc": "探险时 100% 有所收获。", "need_custom": False},
    "妙手回春": {"desc": "可随意治愈残血的宠物，指令：治愈 QQ。", "need_custom": False},
    "起死回生": {"desc": "可随意复活死亡的宠物，指令：复活 QQ。", "need_custom": False},
    "事半功倍": {"desc": "精力恢复速度将提升至 2 倍。", "need_custom": False},
    "绝影丹心": {
        "desc": "可提炼出各种各样的仙丹，每次炼丹消耗 20 万经验、20 万积分、50 点精力。",
        "need_custom": False,
    },
    "精力转移": {
        "desc": "可将自己的精力转移给别的宠物，指令：精力转移 QQ 精力值。",
        "need_custom": True,
    },
}

TALENT_NAMES = list(TALENTS.keys())

# 觉醒/制符/使用符 消耗
AWAKEN_COST = {
    "stage": "超究极体",
    "level": 120,
    "exp": 1_000_000,
    "jifen": 1_000_000,
    "energy": 100,
}
# 飞升后觉醒改用仙元：固定 200 仙元起步，避免经验和积分在飞升后贬值导致成本失衡
ASCEND_AWAKEN_COST = {
    "stage": "超究极体",
    "level": 120,
    "xianyuan": 200,
    "energy": 100,
}
TALENT_RUNE_MAKE_COST = {"jifen": 500_000, "exp": 500_000, "energy": 50}
TALENT_RUNE_USE_COST = {"jifen": 100_000, "exp": 100_000, "energy": 50}
# 飞升后经验类消耗统一改为仙元（参照觉醒：约为直接折算的 10~20 倍，避免仙元贬值）
ASCEND_TALENT_RUNE_MAKE_COST = {"jifen": 500_000, "xianyuan": 100, "energy": 50}
ASCEND_TALENT_RUNE_USE_COST = {"jifen": 100_000, "xianyuan": 20, "energy": 50}

# ----------------------------------------------------------------------------
# 炼丹系统：仙丹
# name -> {desc, effect}。effect 由 main.py 使用仙丹逻辑解释。
# ----------------------------------------------------------------------------
ELIXIRS = {
    "镇魂丹": {
        "desc": "镇压宠物灵魂，使其进入假死状态，4 小时内无法操作宠物。",
        "effect": {"freeze_hours": 4},
    },
    "惊魂丹": {
        "desc": "使灵魂受到惊吓，2 小时内无法操作宠物。",
        "effect": {"freeze_hours": 2},
    },
    "破厄丹": {
        "desc": "解除各种限制和异常，使宠物恢复正常状态。",
        "effect": {"cure_all": True},
    },
    "死亡丹": {
        "desc": "立即死亡，心情变成 1 颗星，血量变成 0。",
        "effect": {"kill": True},
    },
    "神力丹": {"desc": "增加 500 点攻击力。", "effect": {"atk": 500}},
    "金刚丹": {"desc": "增加 500 点防御力。", "effect": {"def": 500}},
    "万寿丹": {"desc": "增加 1 万点生命上限。", "effect": {"hp_max": 10000}},
    "聚灵丹": {"desc": "增加 10 万经验。", "effect": {"exp": 100000}},
    "忘尘丹": {"desc": "使其遗忘秘技。", "effect": {"forget_skill": True}},
    "清虚丹": {"desc": "生命上限减少 1 万。", "effect": {"hp_max": -10000}},
    "破武丹": {"desc": "减少 500 攻击力。", "effect": {"atk": -500}},
    "破防丹": {"desc": "减少 500 防御力。", "effect": {"def": -500}},
}

ELIXIR_NAMES = list(ELIXIRS.keys())
ELIXIR_CRAFT_COST = {"exp": 200_000, "jifen": 200_000, "energy": 50}
# 飞升后炼丹改用仙元
ASCEND_ELIXIR_CRAFT_COST = {"xianyuan": 40, "jifen": 200_000, "energy": 50}

# ----------------------------------------------------------------------------
# 秘技系统（参悟秘技）：name -> {power, intel_req, level_req, desc}
# power 计入战力的"秘技加成"。
# ----------------------------------------------------------------------------
# (秘技名, 等级门槛)，战力加成 = 等级 × 1000，智力门槛 = 等级 × 10。
_SKILL_DEFS = [
    ("飞龙探云手", 10),
    ("乾坤大挪移", 20),
    ("紫电狂龙吼", 30),
    ("万骨噬魂道", 40),
    ("圣光审判术", 50),
    ("八荒弑神斩", 60),
    ("九幻玄雷光", 70),
    ("五雷震天诀", 80),
    ("佛怒炎火莲", 90),
    ("幻灭绝神杀", 100),
    ("碎魂之挽歌", 110),
    ("封尘绝念斩", 120),
    ("花舞醉魂曲", 130),
    ("血魔天防祭", 140),
    ("龙象般若功", 150),
    ("芥子藏身法", 160),
    ("霹雳震光遁", 170),
    ("水幕天华流", 180),
    ("玄阴神煞掌", 190),
    ("九幽转轮术", 200),
    ("天魔裂地法", 210),
    ("青鸾转生诀", 220),
]
SKILLS = {
    name: {
        "power": lv * 1000,
        "intel_req": lv * 10,
        "level_req": lv,
        "desc": f"需 Lv{lv}、智力 {lv * 10}，参悟战力 +{lv * 1000}。",
    }
    for name, lv in _SKILL_DEFS
}

SKILL_NAMES = list(SKILLS.keys())

# 将各秘技也作为可掉落、可交易的背包物品（由 _use_item 特殊处理学习）
for _sk_name, _sk_info in SKILLS.items():
    ITEMS.setdefault(_sk_name, {
        "price": _sk_info["level_req"] * 1000,
        "currency": CURRENCY_JIFEN,
        "category": "秘技书",
        "usable": False,
        "desc": _sk_info["desc"],
        "effect": {},
    })

# ----------------------------------------------------------------------------
# 神器系统（打造/佩戴神器）：name -> {power, level_req, material, desc}
# power 计入战力的"武器加成"。
# ----------------------------------------------------------------------------
# (神器名, 等级门槛)，战力加成 = 等级 × 1000。
_ARTIFACT_DEFS = [
    ("旋影针", 10),
    ("荒芜神鼎", 20),
    ("九天玄琴", 30),
    ("金时空锁链", 40),
    ("破灭琉璃弓", 50),
    ("逆炎轮回戟", 60),
    ("妄虚混沌戒", 70),
    ("弑神无极剑", 80),
    ("震天阴阳环", 90),
    ("太极神魔图", 100),
    ("魏武青虹", 110),
    ("偃月青龙", 120),
    ("定魂珠", 130),
    ("蚩尤弓", 140),
    ("神龙杵", 150),
    ("断尘焚天刀", 160),
    ("凌霄破宇枪", 170),
    ("幽寂斩仙剑", 180),
    ("荒澜碎穹戟", 190),
    ("沧元定道棍", 200),
    ("混沌开天斧", 210),
    ("鸿蒙紫霄剑", 220),
]
ARTIFACTS = {
    name: {
        "power": lv * 1000,
        "level_req": lv,
        "desc": f"需 Lv{lv}，佩戴战力 +{lv * 1000}。",
    }
    for name, lv in _ARTIFACT_DEFS
}

ARTIFACT_NAMES = list(ARTIFACTS.keys())
# 打造神器消耗
ARTIFACT_FORGE_COST = {
    "jifen": 100_000,
    "material": "万能宝石",
    "material_count": 1,
    "blueprint": "神器图纸",
    "blueprint_count": 1,
}

# ----------------------------------------------------------------------------
# 宠物副本
# ----------------------------------------------------------------------------
# 每个副本：等级门槛、消耗精力、怪物名、怪物战力、通关奖励经验/积分。
# 战力 ≥ 怪物战力即可通关（含 ±10% 浮动），否则惨败无奖励。
_DUNGEON_DEFS = [
    # (副本名, 等级, 怪物名)
    ("城外山林", 10, "顽皮小猪"),
    ("魔之森林", 20, "森林狼王"),
    ("兽王妖谷", 30, "狂暴妖虎"),
    ("死亡之塔", 40, "骷髅守卫"),
    ("机关迷城", 50, "青铜傀儡"),
    ("鬼火之泽", 60, "怨灵鬼火"),
    ("封魔遗迹", 70, "封印魔将"),
    ("葬龙秘境", 80, "堕落骨龙"),
    ("水云之涧", 90, "碧水蛟龙"),
    ("暗月幽林", 100, "暗月妖狐"),
    ("囚灵古墓", 110, "千年僵尸"),
    ("烈焰魔窟", 120, "炎狱魔犬"),
    ("琉璃幻境", 130, "琉璃仙姬"),
    ("鹊桥仙境", 140, "织女星灵"),
    ("无垠海域", 150, "深海巨鲲"),
    ("古魔禁地", 160, "上古魔神"),
    ("虚无囚牢", 170, "虚空囚徒"),
    ("焚天炼域", 180, "焚天火凤"),
    ("噬神之窟", 190, "噬神魔影"),
    ("神陨星域", 200, "陨落星神"),
]
# 副本奖励重平衡：
# - 经验 ≈ 升到下一级所需经验的 25%，避免一次副本连升数级。
#   _exp_to_next(lv) = 200 + lv * 100，因此经验 = int((200 + lv * 100) * 0.25)。
# - 积分 ≈ 100 + lv * 8，保持为日常打工/修炼的 1~2 倍，但不再远超商城物价。
# 副本统一精力消耗 10，降低日常门槛；经验/积分已重平衡，避免单局收益过高。
DUNGEONS = {
    name: {
        "level_req": lv,
        "energy": 10,
        "monster": monster,
        "power": lv * 950,
        "exp": int((200 + lv * 100) * 0.25),
        "jifen": 100 + lv * 8,
    }
    for name, lv, monster in _DUNGEON_DEFS
}

# ----------------------------------------------------------------------------
# 剧情任务
# ----------------------------------------------------------------------------
# req: 领取前提（可选）。stage 表示最低阶段索引，level 表示最低等级。
# need: 领取后需要完成的统计增量（与现有 stats 机制一样，从领取时快照）。
# reward: 支持 jifen（积分）、xianyuan（仙元）、exp（经验）、item（物品）、item_count（数量，默认 1）。
QUESTS = {
    # ---- 非飞升新手任务（保留给早期玩家） ----
    "初入江湖": {"need": {"battle_win": 1}, "reward": {"jifen": 2000, "exp": 1000}},
    "降妖除魔": {"need": {"battle_win": 10}, "reward": {"jifen": 20000, "exp": 10000}},
    "探索秘境": {
        "need": {"explore": 20},
        "reward": {"jifen": 30000, "item": "进化神石"},
    },
    # ---- 飞升后专属剧情任务「登仙之路」 ----
    "初入飞升": {
        "req": {"stage": "飞升"},
        "need": {},
        "reward": {"jifen": 5000, "xianyuan": 20, "item": "小精力瓶", "item_count": 1},
    },
    "仙元初聚": {
        "req": {"stage": "飞升"},
        "need": {"ascended_fantasy_treasure": 5},
        "reward": {"jifen": 8000, "xianyuan": 40},
    },
    "劫火炼心": {
        "req": {"stage": "飞升"},
        "need": {"ascended_immortal_calamity": 3},
        "reward": {"jifen": 10000, "xianyuan": 50, "item": "生命宝符", "item_count": 1},
    },
    "飞升·斩妖除魔": {
        "req": {"stage": "飞升"},
        "need": {"ascended_battle_win": 10},
        "reward": {"jifen": 15000, "xianyuan": 80, "item": "攻击宝符", "item_count": 1},
    },
    "秘境探险家": {
        "req": {"stage": "飞升"},
        "need": {"ascended_dungeon_clear": 10},
        "reward": {"jifen": 15000, "xianyuan": 60, "item": "防御宝符", "item_count": 1},
    },
    "深渊行者": {
        "req": {"stage": "飞升"},
        "need": {"ascended_abyss": 10},
        "reward": {"jifen": 20000, "xianyuan": 100},
    },
    "神器再铸": {
        "req": {"stage": "飞升"},
        "need": {"forge_artifact": 1},
        "reward": {"jifen": 25000, "xianyuan": 100, "item": "万能宝石", "item_count": 2},
    },
    "问道姻缘": {
        "req": {"stage": "飞升"},
        "need": {"shuangxiu": 5},
        "reward": {"jifen": 10000, "xianyuan": 50, "item": "相思豆", "item_count": 3},
    },
    "百炼成钢": {
        "req": {"stage": "飞升", "level": 50},
        "need": {},
        "reward": {"jifen": 30000, "xianyuan": 150},
    },
    "飞升圆满": {
        "req": {"stage": "飞升", "level": 100},
        "need": {},
        "reward": {"jifen": 50000, "xianyuan": 200, "item": "生命仙符", "item_count": 1},
    },
}

# 飞升后资源换算：1 仙元 = 100000 经验
ASCEND_XIANYUAN_PER_EXP = 100000


def ascend_xianyuan_to_next(level: int) -> int:
    """飞升/渡劫阶段，从当前等级升一级所需仙元。

    采用线性递增：Lv120 约需 385 仙元，Lv220 约需 685 仙元。
    整体节奏让幻境寻宝（10~15 分钟一次，约 100~180 仙元）成为主要来源，
    大约 2~4 次寻宝可升 1 级；日常经验（按 1:100000 折算）只起补充作用。
    """
    return 25 + level * 3


# 飞升后才能用的幻境寻宝 / 神仙劫奖励范围
ASCEND_TREASURE = {
    "energy": 60,
    "jifen": (500, 3000),  # 积分基础范围
    "jifen_chance": 0.5,   # 50% 概率获得积分
    "xianyuan": (0.8, 1.5),    # 每级系数，实际 = 等级 × 系数 + 基础
    "xianyuan_base": 5,
    "cooldown": (600, 900),  # 10~15 分钟随机冷却
}


def ascend_treasure_xianyuan(level: int) -> tuple[int, int]:
    """幻境寻宝随等级提升的仙元奖励范围。

    Lv120 约 101~185 仙元，Lv220 约 181~335 仙元，
    与 `ascend_xianyuan_to_next(level)` 保持平衡，约 2~4 次寻宝升 1 级。
    """
    low = max(1, int(level * ASCEND_TREASURE["xianyuan"][0] + ASCEND_TREASURE["xianyuan_base"]))
    high = max(low, int(level * ASCEND_TREASURE["xianyuan"][1] + ASCEND_TREASURE["xianyuan_base"]))
    return low, high


# ----------------------------------------------------------------------------
# 飞升副本：挑战神仙
# ----------------------------------------------------------------------------
# 120~220 级每 10 级一位神仙，飞升后解锁。
# - 精力消耗 30，冷却 20 分钟。
# - power 按同等级普通飞升宠物战力的约 1.1~1.4 倍设计，同级可过、越级有难度。
# - 仙元奖励约为同级升级消耗的 1/8，平均 8 次副本可升 1 级。
_ASCEND_DUNGEON_DEFS = [
    (120, "散仙·清风"),
    (130, "地仙·土地"),
    (140, "水仙·河伯"),
    (150, "城隍·司命"),
    (160, "日游神"),
    (170, "夜游神"),
    (180, "雷部正神"),
    (190, "电母元君"),
    (200, "风伯真人"),
    (210, "雨师仙君"),
    (220, "托塔天王"),
]

ASCEND_DUNGEONS = {
    lv: {
        "level_req": lv,
        "energy": 30,
        "name": name,
        # 120级≈89300，220级≈203300，普通飞升玩家同级战力浮动后可胜
        "power": int(lv * 1140 - 47500),
        # 每次奖励约等于当前等级升级消耗的 1/12~1/15，平均 12~15 次副本可升 1 级
        "xianyuan": (max(1, (25 + lv * 3) // 15), max(2, (25 + lv * 3) // 10)),
        # 飞升后经验自动折算为仙元，作为小额添头
        "exp": int((100 + lv * 80) * 0.3),
        "jifen": 200 + lv * 10,
        # 小概率掉落飞升常用道具
        "drop": {"item": "小精力瓶", "chance": 0.15, "count": 1},
    }
    for lv, name in _ASCEND_DUNGEON_DEFS
}

ASCEND_DUNGEON_COOLDOWN = 1200  # 20 分钟

# ----------------------------------------------------------------------------
# 婚恋
# ----------------------------------------------------------------------------
LOVE_STATES = ["单身", "恋爱", "已婚"]
FAVOR_MAX = 999999
FAVOR_MARRY_REQUIRE = 200  # 好感度达到 200 即可求婚
LOVE_INIT_FAVOR = 100  # 追求成功初始好感度

# ----------------------------------------------------------------------------
# 日常活动消耗与产出
# ----------------------------------------------------------------------------
ENERGY_REGEN_PER_MIN = 1  # 每分钟恢复 1 点精力

# 发起对战（宠物攻击 / 跨群挑战）消耗的精力，防止无限刷经验。
ATTACK_ENERGY = 10

# 各类行为冷却（秒）。日常活动为 [下限, 上限] 随机区间。
EGG_COOLDOWN = 180  # 砸蛋单发冷却 3 分钟
EGG_TEN_COOLDOWN = 1500  # 砸蛋十连冷却 25 分钟（与单发共用同一冷却键，互斥）
PET_CARD_DROP_CHANCE = 0.15  # 砸蛋每抽独立判定掉落「宠物卡」的概率（碎片仍必定获得）
# 宠物炼化：将宠物化作对应品质的卡/碎片
REFINE_COST = 1000           # 每次炼化消耗积分
REFINE_CARD_CHANCE = 0.2     # 20% 概率炼出对应品质的品质卡，80% 出碎片
REFINE_FRAGMENT_RANGE = (3, 8)  # 出碎片时的数量区间
DUNGEON_COOLDOWN = 900  # 进入副本冷却 15 分钟
DAILY_COOLDOWN_RANGE = (600, 1200)  # 修炼等日常活动冷却 10~20 分钟随机
TRIBULATION_FAIL_COOLDOWN = 1800  # 渡劫失败后冷却 30 分钟
BATTLE_COOLDOWN_RANGE = (600, 1200)  # 宠物攻击/跨群挑战冷却 10~20 分钟随机
CRAFT_COOLDOWN_RANGE = (600, 1200)  # 觉醒/制符/使用天赋符/炼丹冷却 10~20 分钟随机

# 各日常活动：精力消耗 + 说明
DAILY_ACTIONS = {
    "约会": {"energy": 10, "desc": "增加好感度"},
    "修炼": {"energy": 10, "desc": "获得经验"},
    "双修": {"energy": 20, "desc": "获得 2 倍经验（需已婚）"},
    "打工": {"energy": 10, "desc": "获得积分"},
    "闭关": {"energy": 5, "desc": "恢复血量"},
    "学习": {"energy": 10, "desc": "增加智力"},
    "玩耍": {"energy": 5, "desc": "恢复心情"},
    "洗髓": {"energy": 15, "desc": "降低智力转化为攻击或者防御（随机值）"},
    "探险": {
        "energy": 20,
        "desc": "随机事件（可获得神器、秘技、机缘、图纸、材料、道具等）",
    },
    "冥想": {"energy": 30, "desc": "永久增加随机属性值（需定制宠物才行）"},
}

# ----------------------------------------------------------------------------
# 深渊秘境
# ----------------------------------------------------------------------------
ABYSS_LEVEL_REQ = 20
ABYSS_BASE_ENERGY = 20
ABYSS_BASE_COOLDOWN = 300  # 5 分钟
ABYSS_MAX_ENERGY = 80
ABYSS_MAX_COOLDOWN = 1800  # 30 分钟
ABYSS_CORRUPTION_DECAY_INTERVAL = 1200  # 20 分钟
ABYSS_CORRUPTION_DECAY_AMOUNT = 1
ABYSS_EXP_BASE = 200
ABYSS_EXP_PER_LEVEL = 100


def exp_to_next(level: int) -> int:
    """升到下一级所需经验，与 pet.py 中的 _exp_to_next 保持一致。"""
    return ABYSS_EXP_BASE + level * ABYSS_EXP_PER_LEVEL


# 深渊秘境事件池（每次进入抽一个事件）
ABYSS_EVENTS = [
    {
        "id": "guard",
        "name": "🗡️ 深渊守卫",
        "weight": 30,
        "exp_mult": 0.2,
        "crystal": (1, 3),
        "power_mult": 0.5,
    },
    {
        "id": "chest",
        "name": "🎁 深渊宝箱",
        "weight": 25,
        "exp_mult": 0.12,
        "crystal": (1, 2),
        "mimic_chance": 0.2,
        "mimic_exp_mult": 0.3,
        "power_mult": 0.35,
    },
    {
        "id": "turbulence",
        "name": "⚡ 深渊乱流",
        "weight": 15,
    },
    {
        "id": "altar",
        "name": "🌀 古老祭坛",
        "weight": 15,
        "exp_mult_safe": 0.12,
        "exp_mult_sacrifice": 0.35,
        "hp_sacrifice_pct": 0.15,
    },
    {
        "id": "blessing",
        "name": "✨ 深渊赐福",
        "weight": 10,
        "exp_mult": 0.6,
        "crystal": (2, 4),
        "heal": True,
        "power_mult": 0.0,  # 直接奖励，无战斗
    },
    {
        "id": "lord",
        "name": "👹 深渊领主",
        "weight": 5,
        "exp_mult": 0.9,
        "crystal": (3, 5),
        "power_mult": 1.2,
    },
]


# 深渊商店：用深渊结晶购买一次性道具/Buff
ABYSS_SHOP = {
    "净化药水": {
        "cost": 5,
        "type": "item",
        "give": "净化药水",
        "desc": "清除 5 点深渊侵蚀（购买后入背包）",
    },
    "深渊护符": {
        "cost": 10,
        "type": "buff",
        "buff": "no_corruption",
        "desc": "下一次挑战不增加深渊侵蚀",
    },
    "深渊回春石": {
        "cost": 30,
        "type": "buff",
        "buff": "revive",
        "desc": "下一次挑战中若宠物死亡，自动复活并保留 30% HP",
    },
}

# 深渊祝福：购买后下一次挑战生效
ABYSS_BLESSINGS = {
    "幸运之星": {
        "cost": 20,
        "desc": "本次挑战经验收益 +20%",
    },
    "侵蚀压制": {
        "cost": 30,
        "desc": "本次挑战不增加深渊侵蚀",
    },
    "怜悯加速": {
        "cost": 25,
        "desc": "本次挑战大奖概率 +10%",
    },
    "精力回收": {
        "cost": 15,
        "desc": "本次挑战结束后返还 50% 精力",
    },
}


# ----------------------------------------------------------------------------
# 宠物摸金（独立财富系统）
# ----------------------------------------------------------------------------
TOMB_CURRENCY = "冥币"
TOMB_DAILY_FREE = 3  # 已废弃，保留兼容
TOMB_EXTRA_TOKEN = "棺椁令"
TOMB_EXTRA_TOKEN_COST = 200  # 冥币
TOMB_COOLDOWN = 300  # 每次摸金结束后冷却秒数（5分钟）

# 摸金独立等级系统（无等级上限）
TOMB_MAX_LEVEL = None

# 摸金独立角色系统
TOMB_MAX_HP = 100
TOMB_BASE_ATTACK = 10  # 摸金战力基础
TOMB_LEVEL_ATTACK = 2  # 每级摸金等级提供的战力
TOMB_ESCAPES_PER_RAID = 3  # 每局可逃跑次数


def tomb_player_attack(tomb_level: int, weapon_attack: int = 0) -> int:
    """摸金战力 = 基础 + 等级成长 + 武器攻击。"""
    return TOMB_BASE_ATTACK + tomb_level * TOMB_LEVEL_ATTACK + weapon_attack


# 摸金武器（带入摸金，决定摸金战力；有耐久，阵亡全丢）
TOMB_WEAPONS = {
    "木棍": {"attack": 5, "price": 30, "durability": 10},
    "铁剑": {"attack": 15, "price": 100, "durability": 15},
    "黑金匕": {"attack": 30, "price": 300, "durability": 20},
    "镇墓刀": {"attack": 60, "price": 800, "durability": 25},
    "冥火枪": {"attack": 100, "price": 2000, "durability": 30},
}

# 运气战斗常数
TOMB_BATTLE = {
    "player_luck": (0.8, 1.2),
    "monster_luck": (0.85, 1.15),
    "crit_chance": 0.15,
    "crit_mult": 1.5,
    "dodge_chance": 0.10,
    "miss_chance": 0.10,
    "miss_mult": 0.7,
}

# 陷阱运气结果权重：(结果, 权重)
TOMB_TRAP_OUTCOMES = [
    ("avoid", 40),   # 完全避开
    ("light", 40),   # 轻伤 -15
    ("heavy", 20),   # 重伤 -30 + 眩晕1
]


def tomb_exp_to_next(level: int) -> int:
    """升到下一摸金等级所需经验（无等级上限，随等级线性增长）。"""
    return 80 + level * 40


# 摸金等级经验奖励：成功 / 失败（超时、死亡、放弃均算失败）
TOMB_XP_REWARD = {
    "success": {1: 30, 2: 60, 3: 110, 4: 180},
    "failure": {1: 10, 2: 20, 3: 35, 4: 60},
}

# 难度配置：尺寸、怪物数、宝箱数、陷阱数、祭坛数、时间(秒)、需带回冥币、精力消耗、摸金等级要求
TOMB_DIFFICULTIES = {
    1: {
        "name": "简单",
        "size": (9, 9),
        "monsters": 3,
        "chests": 3,
        "traps": 3,
        "altars": 1,
        "gold_piles": 2,
        "gas_zones": 2,
        "portals": 1,
        "springs": 1,
        "bosses": 0,
        "time": 480,
        "required": 80,
        "energy": 15,
        "tomb_level_req": 1,
        "entry_tokens": 0,
        "monster_mult": 0.40,
        "monster_power": 19,
        "chest_mingbi": (15, 30),
        "monster_mingbi": (5, 15),
    },
    2: {
        "name": "普通",
        "size": (13, 13),
        "monsters": 5,
        "chests": 4,
        "traps": 4,
        "altars": 1,
        "gold_piles": 2,
        "gas_zones": 3,
        "portals": 1,
        "springs": 1,
        "bosses": 1,
        "time": 600,
        "required": 200,
        "energy": 25,
        "tomb_level_req": 5,
        "entry_tokens": 0,
        "monster_mult": 0.60,
        "monster_power": 43,
        "chest_mingbi": (20, 45),
        "monster_mingbi": (10, 20),
    },
    3: {
        "name": "困难",
        "size": (17, 17),
        "monsters": 8,
        "chests": 6,
        "traps": 5,
        "altars": 2,
        "gold_piles": 3,
        "gas_zones": 4,
        "portals": 2,
        "springs": 1,
        "bosses": 1,
        "time": 720,
        "required": 450,
        "energy": 40,
        "tomb_level_req": 10,
        "entry_tokens": 1,
        "monster_mult": 0.85,
        "monster_power": 81,
        "chest_mingbi": (35, 70),
        "monster_mingbi": (15, 35),
    },
    4: {
        "name": "噩梦",
        "size": (21, 21),
        "monsters": 10,
        "chests": 8,
        "traps": 7,
        "altars": 2,
        "gold_piles": 4,
        "gas_zones": 5,
        "portals": 2,
        "springs": 2,
        "bosses": 2,
        "time": 900,
        "required": 900,
        "energy": 60,
        "tomb_level_req": 15,
        "entry_tokens": 2,
        "monster_mult": 1.15,
        "monster_power": 133,
        "chest_mingbi": (55, 110),
        "monster_mingbi": (20, 45),
    },
}

# 地图绘制配色
TOMB_COLORS = {
    "bg": (25, 22, 22),
    "wall": (60, 55, 55),
    "floor": (30, 28, 28),
    "grid": (50, 48, 48),
    "entrance": (80, 160, 80),
    "exit": (220, 80, 80),
    "monster": (180, 50, 50),
    "chest": (200, 160, 60),
    "trap": (150, 60, 150),
    "altar": (60, 120, 180),
    "gold": (255, 215, 0),
    "gas": (100, 255, 80),
    "portal": (180, 100, 255),
    "spring": (80, 220, 240),
    "boss": (255, 60, 20),
    "text": (220, 220, 220),
}

TOMB_CELL_SIZE = 48
TOMB_PADDING = 24

# Boss 属性加成与掉落倍率
TOMB_BOSS_POWER_MULT = 1.5
TOMB_BOSS_MINGBI_MULT = 3
TOMB_BOSS_DROP_CHANCE = 1.0  # 击败 Boss 必定掉落一件摸金道具

# 摸金双排倍率
TOMB_COOP_MULT = 1.5           # 怪物/宝箱/陷阱/金币/毒雾/传送/生命泉/Boss 通用倍率
TOMB_COOP_REQUIRED_MULT = 2.0  # 撤离所需冥币倍率
TOMB_COOP_RESCUE_HP_COST = 0.30    # 救援消耗 HP 比例
TOMB_COOP_RESCUE_REVIVE_HP = 0.20  # 救援复活 HP 比例
TOMB_COOP_RANGE = 3            # 救援/传送最大曼哈顿距离
TOMB_COOP_SELF_COLOR = (0, 255, 255)      # 自己：青色
TOMB_COOP_TEAMMATE_COLOR = (255, 220, 40) # 队友：黄色
TOMB_COOP_DOWNED_COLOR = (255, 20, 20)    # 倒地标记：红色

# 摸金商店道具（与主背包隔离）
TOMB_ITEMS = {
    "引路香": {
        "price": 50,
        "desc": "下一次移动不触发怪物。",
        "effect": "avoid_monster",
    },
    "镇尸钉": {
        "price": 120,
        "desc": "下一场战斗锁定必胜。",
        "effect": "auto_win",
    },
    "洛阳铲": {
        "price": 80,
        "desc": "下一次开箱冥币+30%。",
        "effect": "chest_bonus",
    },
    "招魂幡": {
        "price": 300,
        "desc": "摸金HP归0时自动复活到1并强制撤离（保留50%冥币）。",
        "effect": "revive",
    },
    "回春散": {
        "price": 90,
        "desc": "恢复90点摸金HP。",
        "effect": "heal_tomb",
        "amount": 90,
    },
    "绷带": {
        "price": 20,
        "desc": "恢复10点摸金HP。",
        "effect": "heal_tomb",
        "amount": 10,
    },
    "金创药": {
        "price": 50,
        "desc": "恢复40点摸金HP。",
        "effect": "heal_tomb",
        "amount": 40,
    },
    "还魂丹": {
        "price": 200,
        "desc": "摸金HP归0时自动复活到50HP。",
        "effect": "revive_tomb",
    },
    TOMB_EXTRA_TOKEN: {
        "price": TOMB_EXTRA_TOKEN_COST,
        "desc": "额外的摸金入场券。",
        "effect": "token",
    },
    "普通经验书": {
        "price": 10,
        "desc": "购买后进入主背包，使用后宠物经验 +200。",
        "effect": "main_bag_item",
    },
}

# 摸金命运卡牌（开局随机抽 3 选 1，每局不同的 Buff/Debuff）
TOMB_DESTINY_CARDS = {
    "财迷心窍": {
        "desc": "宝箱冥币×1.8，但怪物攻击×1.5",
        "effects": {"chest_mingbi_mult": 1.8, "monster_attack_mult": 1.5},
    },
    "鹰眼": {
        "desc": "全图无迷雾，但时间-25%",
        "effects": {"no_fog": True, "time_mult": 0.75},
    },
    "铁胃": {
        "desc": "毒雾免疫，但祭坛无法使用",
        "effects": {"gas_immune": True, "altar_blocked": True},
    },
    "先知": {
        "desc": "Boss 位置穿透迷雾可见，但传送门禁用",
        "effects": {"boss_visible": True, "portal_blocked": True},
    },
    "狂战士": {
        "desc": "玩家攻击+40%，但战后扣 8 HP",
        "effects": {"player_attack_mult": 1.4, "post_battle_hp_loss": 8},
    },
    "摸金校尉": {
        "desc": "开箱额外+25 冥币，但怪物遭遇率+30%",
        "effects": {"chest_mingbi_bonus": 25, "monster_encounter_mult": 1.3},
    },
    "幸运星": {
        "desc": "陷阱避开率+30%，但宝箱冥币-25%",
        "effects": {"trap_dodge_bonus": 0.3, "chest_mingbi_mult": 0.75},
    },
    "吸血鬼": {
        "desc": "战胜回复 15 HP，但治疗道具效果-50%",
        "effects": {"post_battle_hp_heal": 15, "heal_item_mult": 0.5},
    },
    "时间掌控者": {
        "desc": "时间+40%，但怪物血量×1.5",
        "effects": {"time_mult": 1.4, "monster_hp_mult": 1.5},
    },
    "富可敌国": {
        "desc": "起始+80 冥币，但撤离要求+40%",
        "effects": {"start_mingbi": 80, "required_mult": 1.4},
    },
    "武器大师": {
        "desc": "武器攻击×2，但耐久消耗×2",
        "effects": {"weapon_attack_mult": 2.0, "weapon_durability_mult": 2.0},
    },
    "和平主义者": {
        "desc": "逃跑必成功，但战斗不会掉落冥币",
        "effects": {"escape_guaranteed": True, "combat_mingbi_zero": True},
    },
    "淘金热": {
        "desc": "金币堆收益×2，但毒雾伤害×2",
        "effects": {"gold_mult": 2.0, "gas_damage_mult": 2.0},
    },
    "涅槃": {
        "desc": "死亡自动复活 1 次（40 HP），但初始 HP-25",
        "effects": {"auto_revive": True, "revive_hp": 40, "start_hp_mod": -25},
    },
    "赌徒": {
        "desc": "伤害随机范围扩大到±40%",
        "effects": {"luck_range_mult": 1.4},
    },
    "潜行者": {
        "desc": "怪物遭遇率-40%，但 Boss 战力+40%",
        "effects": {"monster_encounter_mult": 0.6, "boss_attack_mult": 1.4},
    },
    "回春体": {
        "desc": "生命泉回复×2，但陷阱伤害×1.5",
        "effects": {"spring_heal_mult": 2.0, "trap_damage_mult": 1.5},
    },
    "天选之人": {
        "desc": "暴击率+10%，陷阱避开率+10%（纯正面）",
        "effects": {"crit_chance_bonus": 0.1, "trap_dodge_bonus": 0.1},
    },
    "死灵法师": {
        "desc": "战胜回复 10 HP，但生命泉无法使用",
        "effects": {"post_battle_hp_heal": 10, "spring_blocked": True},
    },
    "探险家": {
        "desc": "视野+1（纯正面）",
        "effects": {"vision_bonus": 1},
    },
}

# 成功撤离后额外经验（区间随机，不影响现有财富），失败得一半
TOMB_SUCCESS_EXP_RANGE = {
    1: (400, 1000),
    2: (800, 2000),
    3: (1500, 3500),
    4: (2500, 5000),
}

# 今日摸金神榜前三奖励（宠物主经验），排名越靠前区间越高
TOMB_DAILY_REWARD_EXP = {
    1: (15000, 25000),
    2: (8000, 15000),
    3: (2500, 7500),
}


# ----------------------------------------------------------------------------
# 签到称号：(累计签到天数门槛, 称号名)，按累计天数从高到低匹配。
# ----------------------------------------------------------------------------
SIGN_TITLES = [
    (1, "偶尔冒泡"),
    (8, "略懂一二"),
    (15, "渐入佳境"),
    (30, "小有名气"),
    (60, "驾轻就熟"),
    (100, "宠园达人"),
    (200, "骨灰玩家"),
    (365, "宠园传说"),
]


def sign_title(total_days: int) -> tuple[str, int | None, str | None]:
    """根据累计签到天数返回 (当前称号, 距下一称号还需天数, 下一称号名)。"""
    current = SIGN_TITLES[0][1]
    for need, name in SIGN_TITLES:
        if total_days >= need:
            current = name
        else:
            return current, need - total_days, name
    return current, None, None


# ----------------------------------------------------------------------------
# 宠物扫雷（全服独立积分系统）
# ----------------------------------------------------------------------------
# 难度配置：棋盘尺寸、雷数、时间(秒)、胜利宠物经验区间、胜利积分
MS_DIFFICULTIES = {
    1: {"name": "简单", "size": (6, 6), "mines": 5, "time": 300, "exp": (400, 800), "score": 10},
    2: {"name": "普通", "size": (9, 9), "mines": 12, "time": 600, "exp": (900, 1800), "score": 25},
    3: {"name": "困难", "size": (12, 12), "mines": 24, "time": 900, "exp": (1800, 3200), "score": 60},
    4: {"name": "地狱", "size": (15, 15), "mines": 40, "time": 1200, "exp": (3000, 5500), "score": 150},
}

# 失败安慰经验 = 胜利经验下限 × 已翻开安全格比例 × 该系数
MS_FAIL_EXP_RATIO = 0.3

# 棋盘绘制
MS_CELL_SIZE = 44
MS_PADDING = 20
MS_COLORS = {
    "bg": (248, 249, 250),
    "closed": (106, 133, 106),
    "closed_alt": (96, 123, 96),
    "open": (255, 255, 255),
    "open_alt": (245, 245, 245),
    "grid": (200, 205, 210),
    "text": (40, 44, 52),
    "coord": (255, 255, 255),
    "flag": (220, 60, 60),
    "mine": (30, 30, 30),
    "boom": (230, 80, 80),
}
# 数字配色（经典扫雷配色）
MS_NUMBER_COLORS = {
    1: (25, 118, 210),
    2: (56, 142, 60),
    3: (211, 47, 47),
    4: (123, 31, 162),
    5: (255, 143, 0),
    6: (0, 151, 167),
    7: (93, 64, 55),
    8: (69, 90, 100),
}


# ----------------------------------------------------------------------------
# 宗门战 / 跨群联赛
# ----------------------------------------------------------------------------
SECT_BATTLE_TIME = 21  # 每天开打小时（24 小时制）
SECT_ENROLL_DEADLINE_HOUR = 20
SECT_ENROLL_DEADLINE_MIN = 30
SECT_FORCED_COUNT = 3   # 强制出战人数
SECT_ENROLL_COUNT = 7   # 报名出战人数
SECT_TEAM_SIZE = SECT_FORCED_COUNT + SECT_ENROLL_COUNT  # 总出战 10 人
SECT_MIN_BATTLE_MEMBERS = 5  # 最少几人才能参战
SECT_MAX_DEPUTIES = 3   # 副宗主人数上限
SECT_SIGN_POINTS = 5    # 宗门签到获得宗门积分
SECT_WIN_POINTS = 50    # 宗门战胜利获得宗门积分
SECT_LOSE_POINTS = 10   # 宗门战失败获得宗门积分
SECT_DRAW_POINTS = 20   # 平局宗门积分
SECT_BYE_POINTS = 5     # 轮空宗门积分
SECT_WIN_JIFEN = 100    # 胜利个人积分奖励
SECT_WIN_COIN = 500     # 胜利个人金币奖励
SECT_LOSE_JIFEN = 30    # 失败个人积分奖励
SECT_LOSE_COIN = 100    # 失败个人金币奖励
SECT_DRAW_JIFEN = 50    # 平局个人积分奖励
SECT_DRAW_COIN = 250    # 平局个人金币奖励

# 宗门商店：宗门积分 / 宗门贡献 兑换商品
#   cost_type: 消耗类型
#     - "sect_points": 宗门积分（全宗共享，仅宗主/副宗主可花）
#     - "contribution": 宗门贡献（个人资产，任何人可花）
#   points: 消耗宗门积分（cost_type=sect_points 时）
#   contribution: 消耗宗门贡献（cost_type=contribution 时）
#   item + count: 发放道具
#   currency + amount: 发放货币
SECT_SHOP = {
    "经验丹": {"cost_type": "sect_points", "points": 50, "item": "经验丹", "count": 1, "desc": "使用后宠物获得经验。"},
    "进化神石": {"cost_type": "sect_points", "points": 300, "item": "进化神石", "count": 1, "desc": "宠物进化所需道具。"},
    "史诗卡": {"cost_type": "sect_points", "points": 1500, "item": "史诗卡", "count": 1, "desc": "可将宠物品质提升至史诗。"},
    "金币袋": {"cost_type": "sect_points", "points": 100, "currency": "金币", "amount": 1000, "desc": "打开获得 1000 金币。"},
    "积分袋": {"cost_type": "sect_points", "points": 100, "currency": "积分", "amount": 200, "desc": "打开获得 200 积分。"},
    "神秘宝箱": {"cost_type": "contribution", "contribution": 100, "item": "神秘宝箱", "count": 1, "desc": "宗门贡献兑换的神秘宝箱，可开出随机道具。"},
}

# 宗门贡献获取配置
SECT_CONTRIBUTION_SIGN = 1       # 宗门签到获得贡献
SECT_CONTRIBUTION_BATTLE = 5     # 宗门战每场胜利获得贡献（参战者）
SECT_CONTRIBUTION_BATTLE_LOSE = 2  # 宗门战失败获得贡献

# 宗门等级经验表：历史累计宗门积分达到一定值升级
SECT_LEVEL_EXP = {
    1: 0,
    2: 500,
    3: 1500,
    4: 3000,
    5: 5000,
    6: 8000,
    7: 12000,
    8: 17000,
    9: 23000,
    10: 30000,
}

# ----------------------------------------------------------------------------
# 宗门战新流程时间轴（每日）
#   20:30 自动匹配并定向广播  20:40 第1回合开始
#   20:50 第1回合结束  21:00 第2回合结束  21:10 第3回合结束（决赛）
# ----------------------------------------------------------------------------
SECT_WAR_MATCH_HOUR = 20
SECT_WAR_MATCH_MIN = 30
SECT_WAR_START_HOUR = 20
SECT_WAR_START_MIN = 40
SECT_WAR_ROUNDS = 3            # 总回合数
SECT_WAR_ROUND_MINUTES = 10    # 每回合时长（分钟）

# 加油机制
SECT_CHEER_MIN = 1000          # 单次加油最小战力
SECT_CHEER_MAX = 20000         # 单次加油最大战力
SECT_CHEER_CD_MIN = 30         # 加油冷却最小秒数
SECT_CHEER_CD_MAX = 120        # 加油冷却最大秒数

# 回合结束扣血（占最大血量百分比）
SECT_WAR_HP_LOSS_MIN_PCT = 5
SECT_WAR_HP_LOSS_MAX_PCT = 15

# 宗门战基础战力差距上限（防止一方碾压）
SECT_WAR_POWER_GAP_CAP = 100000

# 新版宗门战：全群参与，初始随机战力上限
SECT_WAR_BASE_POWER_MAX = 50000

# ============================================================================
# 宠物家园（放置建造 · 纯金币升级 · 数据隔离 · 宠物派遣 · 偷菜护院）
# ============================================================================

HOMESTEAD_BASE_MAX_ACCUMULATE = 12 * 3600  # 基础最大累计 12 小时

# 家园等级 → 建筑位数量
_HOMESTEAD_SLOTS_TABLE = {
    1: 2, 2: 3, 3: 4, 4: 5, 5: 6,
    6: 7, 7: 7, 8: 8, 9: 8, 10: 9,
}


def homestead_slots(level: int) -> int:
    """家园等级对应的建筑位数量（Lv10 后每 2 级 +1 位，上限 15）。"""
    if level <= 10:
        return _HOMESTEAD_SLOTS_TABLE.get(level, 2)
    return min(15, 9 + (level - 10) // 2)


def homestead_exp_to_next(level: int) -> int:
    """家园升到下一级所需经验。"""
    return 200 + level * 300


def homestead_upgrade_cost(level: int, base: int = 500) -> int:
    """建筑升级到指定等级所需金币（升级=level N→N+1，传入当前等级）。"""
    return int(base * (level + 1) * (1.0 + (level + 1) * 0.3))


def homestead_max_accumulate(warehouse_level: int = 0) -> int:
    """计算实际最大累计秒数（仓库每级 +2h）。"""
    return HOMESTEAD_BASE_MAX_ACCUMULATE + warehouse_level * 2 * 3600


# ============================================================================
# 建筑定义（7 种 → 建筑位有限 → 战略取舍）
# ============================================================================
HOMESTEAD_BUILDINGS = {
    "金币矿": {
        "desc": "稳定产出金币，每小时自动累积。",
        "icon": "💰",
        "build_cost": 500,
        "base_coin": 100,
        "coin_per_lv": 28,
        "base_jifen": 0,
        "jifen_per_lv": 0,
        "prefer_element": "金",
    },
    "积分工坊": {
        "desc": "稳定产出积分，每小时自动累积。",
        "icon": "🏭",
        "build_cost": 500,
        "base_coin": 0,
        "base_jifen": 85,
        "coin_per_lv": 0,
        "jifen_per_lv": 22,
        "prefer_element": "水",
    },
    "聚宝盆": {
        "desc": "同时产出金币+积分，单资源约为专精的 60%。",
        "icon": "🏛️",
        "build_cost": 1000,
        "base_coin": 60,
        "base_jifen": 50,
        "coin_per_lv": 16,
        "jifen_per_lv": 14,
        "prefer_element": "土",
    },
    "经验泉": {
        "desc": "产出宠物经验，可用「派遣」驻扎宠物加速。",
        "icon": "🌿",
        "build_cost": 2000,
        "base_coin": 0,
        "base_jifen": 0,
        "base_exp": 50,
        "exp_per_lv": 18,
        "coin_per_lv": 0,
        "jifen_per_lv": 0,
        "unlock_pet_level": 60,
        "prefer_element": "木",
    },
    "仓库": {
        "desc": "提升离线累计上限，每级 +2 小时（基础 12h）。",
        "icon": "📦",
        "build_cost": 10000,
        "base_coin": 0,
        "base_jifen": 0,
        "coin_per_lv": 0,
        "jifen_per_lv": 0,
        "warehouse": True,
        "prefer_element": "土",
    },
    "哨塔": {
        "desc": "提升家园防御力，降低被偷菜成功率。",
        "icon": "🏹",
        "build_cost": 1200,
        "base_coin": 0,
        "base_jifen": 0,
        "coin_per_lv": 0,
        "jifen_per_lv": 0,
        "defense_per_lv": 25,
        "prefer_element": "火",
    },
    "祈福坛": {
        "desc": "提升收取时触发好事件的概率，降低坏事件概率。",
        "icon": "🕯️",
        "build_cost": 1500,
        "base_coin": 0,
        "base_jifen": 0,
        "coin_per_lv": 0,
        "jifen_per_lv": 0,
        "luck_per_lv": 3,  # 每级 +3% 好运权重偏移
        "prefer_element": "光",
    },
}


def homestead_production(building: str, level: int) -> dict:
    """计算建筑当前每小时产量（返回 {coin, jifen, exp?}）。"""
    cfg = HOMESTEAD_BUILDINGS[building]
    result = {
        "coin": cfg.get("base_coin", 0) + cfg.get("coin_per_lv", 0) * (level - 1),
        "jifen": cfg.get("base_jifen", 0) + cfg.get("jifen_per_lv", 0) * (level - 1),
    }
    if "base_exp" in cfg:
        result["exp"] = cfg["base_exp"] + cfg.get("exp_per_lv", 0) * (level - 1)
    return result


# ============================================================================
# 宠物派遣
# ============================================================================
# 派遣对产量的加成系数
HOMESTEAD_DISPATCH_LEVEL_FACTOR = 0.006   # 宠物每级 +0.6% 产量
HOMESTEAD_DISPATCH_QUALITY_FACTOR = 0.04  # 品质每档 +4%（普通=0, 混沌=9→36%）
HOMESTEAD_DISPATCH_ELEMENT_MATCH = 0.10   # 属性匹配额外 +10%
HOMESTEAD_DISPATCH_ENERGY_PER_HOUR = 2    # 派遣每小时消耗宠物精力
HOMESTEAD_DISPATCH_MIN_ENERGY = 5         # 派遣最低精力要求


def homestead_dispatch_multiplier(pet: dict, building: str) -> float:
    """计算派遣宠物对指定建筑的产量倍率。"""
    if not pet:
        return 1.0
    level = pet.get("level", 1)
    quality = pet.get("quality", "普通")
    element = pet.get("element", "")
    quality_idx = list(QUALITY_GROWTH.keys()).index(quality) if quality in QUALITY_GROWTH else 0
    mult = 1.0 + level * HOMESTEAD_DISPATCH_LEVEL_FACTOR + quality_idx * HOMESTEAD_DISPATCH_QUALITY_FACTOR
    cfg = HOMESTEAD_BUILDINGS.get(building, {})
    if cfg.get("prefer_element") == element:
        mult += HOMESTEAD_DISPATCH_ELEMENT_MATCH
    return round(mult, 3)


# ============================================================================
# 偷菜系统
# ============================================================================
HOMESTEAD_STEAL_MAX_PER_DAY = 5          # 每日最大偷菜次数
HOMESTEAD_STEAL_COOLDOWN_SAME = 7200     # 同一目标冷却 2 小时
HOMESTEAD_STEAL_RATIO_MIN = 0.10         # 偷取最小比例
HOMESTEAD_STEAL_RATIO_MAX = 0.30         # 偷取最大比例
HOMESTEAD_STEAL_BASE_DEFENSE = 50        # 基础防御值
HOMESTEAD_STEAL_FAIL_PENALTY = 50        # 偷取失败赔偿金币
HOMESTEAD_MAX_BE_STOLEN_PER_DAY = 3      # 每天最多被偷 3 次


def homestead_steal_success_rate(attacker_level: int, target_defense: int) -> float:
    """偷菜成功率（0~1）。"""
    return attacker_level / (attacker_level + target_defense + HOMESTEAD_STEAL_BASE_DEFENSE)


def homestead_defense(hs: dict) -> int:
    """计算家园防御值（哨塔 + 派遣宠物）。"""
    defense = 0
    buildings = hs.get("buildings", {})
    if "哨塔" in buildings:
        defense += buildings["哨塔"].get("level", 1) * HOMESTEAD_BUILDINGS["哨塔"].get("defense_per_lv", 25)
    dispatch = hs.get("dispatch", {})
    for bname, pet_data in dispatch.items():
        if pet_data and isinstance(pet_data, dict):
            defense += int(pet_data.get("level", 1) * 0.5)
    return defense


# ============================================================================
# 流浪商人
# ============================================================================
HOMESTEAD_MERCHANT_CHANCE = 0.10  # 收取时 10% 触发

# 商人货架（随机抽取 3 件）
HOMESTEAD_MERCHANT_ITEMS = [
    {"name": "进化神石", "price_type": "coin", "price": 5000, "desc": "宠物进化材料", "item": "进化神石"},
    {"name": "万能宝石", "price_type": "coin", "price": 3000, "desc": "打造神器材料", "item": "万能宝石"},
    {"name": "神器图纸", "price_type": "coin", "price": 20000, "desc": "打造神器图纸", "item": "神器图纸"},
    {"name": "史诗卡", "price_type": "coin", "price": 50000, "desc": "品质提升至史诗", "item": "史诗卡"},
    {"name": "小精力瓶", "price_type": "coin", "price": 1000, "desc": "恢复 10 点精力", "item": "小精力瓶"},
    {"name": "普通经验书", "price_type": "jifen", "price": 3000, "desc": "宠物经验 +200", "item": "普通经验书"},
    {"name": "聚灵丹", "price_type": "jifen", "price": 10000, "desc": "宠物经验 +10 万", "item": "聚灵丹"},
    {"name": "进化神石×3", "price_type": "coin", "price": 12000, "desc": "3 颗进化神石", "item": "进化神石", "item_count": 3},
    {"name": "相思豆×5", "price_type": "jifen", "price": 2000, "desc": "5 颗相思豆", "item": "相思豆", "item_count": 5},
    {"name": "建筑加速券", "price_type": "coin", "price": 2000, "desc": "立即跳过 2 小时建筑累积", "effect": "speed_2h"},
    {"name": "护院符", "price_type": "coin", "price": 3000, "desc": "12 小时内免疫偷菜", "effect": "shield_12h"},
    {"name": "双倍券", "price_type": "coin", "price": 5000, "desc": "下次收取产量翻倍", "effect": "double_next"},
]


# ============================================================================
# 家园收取随机事件（扩容版）
# ============================================================================
HOMESTEAD_EVENTS = [
    {"name": "天降横财", "weight": 3, "mult": 3.0, "emoji": "🎉", "text": "天降横财！本次收获 ×3！", "good": True},
    {"name": "丰收", "weight": 8, "mult": 2.0, "emoji": "🌟", "text": "大丰收！本次收获 ×2！", "good": True},
    {"name": "流浪商人", "weight": 10, "mult": 1.0, "emoji": "🧳", "merchant": True, "text": "一位流浪商人路过你的家园…", "good": True},
    {"name": "宠物帮忙", "weight": 10, "mult": 1.0, "emoji": "🐾", "pet_bonus": 0.08, "text": "宠物帮忙打理家园，额外产出 +{bonus}！", "good": True},
    {"name": "幸运日", "weight": 12, "mult": 1.0, "emoji": "🍀", "extra_coin": (50, 300), "text": "幸运日！额外获得 {bonus} 金币！", "good": True},
    {"name": "地脉涌动", "weight": 8, "mult": 1.0, "emoji": "⛰️", "extra_all": 1.3, "text": "地脉涌动！所有建筑额外产出 30%！", "good": True},
    {"name": "正常", "weight": 35, "mult": 1.0, "emoji": "", "text": "", "good": False},
    {"name": "小偷", "weight": 8, "mult": 0.7, "emoji": "🐀", "text": "有小偷光顾！本次收获 -30%…", "good": False},
    {"name": "暴风雨", "weight": 6, "mult": 0.5, "emoji": "⛈️", "next_bonus": 0.5, "text": "暴风雨袭击！本次 -50%，下次 +50%！", "good": False},
]


def homestead_roll_event(hs: dict) -> dict:
    """收取时随机事件抽取（祈福坛偏移好坏权重）。"""
    import random as _random
    luck_bonus = 0
    if "祈福坛" in hs.get("buildings", {}):
        luck_bonus = hs["buildings"]["祈福坛"].get("level", 1) * HOMESTEAD_BUILDINGS["祈福坛"].get("luck_per_lv", 3)
    events = HOMESTEAD_EVENTS
    adjusted = []
    for e in events:
        w = e["weight"]
        if e.get("good") and luck_bonus > 0:
            w = int(w * (1 + luck_bonus / 100))
        elif not e.get("good") and luck_bonus > 0:
            w = max(1, int(w * (1 - luck_bonus / 100)))
        adjusted.append((w, e))
    total = sum(w for w, _ in adjusted)
    r = _random.randint(1, total)
    acc = 0
    for w, e in adjusted:
        acc += w
        if r <= acc:
            return e
    return events[-1]


# ============================================================================
# 家园排行
# ============================================================================
HOMESTEAD_RANK_SIZE = 10
HOMESTEAD_RANK_REWARD_COIN = {1: 5000, 2: 3000, 3: 1000}

# 家园拜访
HOMESTEAD_VISIT_MAX_PER_DAY = 3
HOMESTEAD_VISIT_REWARD_COIN = 50
HOMESTEAD_VISITED_REWARD_COIN = 20

# ============================================================================
# 转让/赠送限制（防小号滥用）
# ============================================================================
TRANSFER_DAILY_MAX_OPS = 10         # 每天所有转让+赠送合计次数上限
TRANSFER_PER_TX_MAX = 5000          # 单次金币/积分/钻石转让数量上限
TRANSFER_TAX_COIN = 0.20            # 金币转让税率
TRANSFER_TAX_JIFEN = 0.20           # 积分转让税率
TRANSFER_TAX_DIAMOND = 0.20         # 钻石转让税率
TRANSFER_TAX_ITEM = 0.10            # 道具转让税率
TRANSFER_WEEKLY_SAME_LIMIT = 5      # 7天内向同一人转让超过此次数，触发双倍税
TRANSFER_DOUBLE_TAX_MULT = 2.0      # 双倍税倍率

# ============================================================================
# 宠物银行（存款/贷款/利息/信用/逾期冻结）
# ============================================================================
# 利率
BANK_INTEREST_WEEKLY = 0.01          # 周利率 1%
BANK_INTEREST_DAY = 0.01 / 7         # 日利率（仅用于显示，实际按周计息）

# 贷款额度（每种货币独立额度）
BANK_LOAN_MIN_AMOUNT = 1_000         # 最低单次贷款金额（防刷信用分）
BANK_LOAN_COOLDOWN_AFTER_REPAY = 86400  # 还清后24小时内不能重新贷款同币种
BANK_LOAN_BASE = 100_000             # 基础贷款额度
BANK_LOAN_PER_CREDIT = 100           # 信用分每超过500，每100分增加1万额度
BANK_LOAN_MAX = 500_000              # 贷款上限
BANK_LOAN_MIN = 10_000               # 贷款下限（信用低于300时）

# 信用分
BANK_CREDIT_INITIAL = 500            # 初始信用分
BANK_CREDIT_REPAY_ON_TIME = (10, 30) # 按时还款加分（随机范围）
BANK_CREDIT_REPAY_LATE = -20         # 逾期7天内还款扣分
BANK_CREDIT_OVERDUE = (-100, -50)    # 逾期超7天冻结后还清扣分（随机范围）

# 逾期时间
BANK_LOAN_DURATIONS = {7: "7天", 14: "14天", 30: "30天"}  # 可选贷款期限
BANK_LOAN_DEFAULT_DAYS = 7           # 默认贷款期限
BANK_OVERDUE_FREEZE_DAYS = 7         # 逾期后再宽限7天，之后冻结


def bank_loan_limit(credit_score: int) -> int:
    """根据信用分计算贷款额度上限（每种货币独立）。"""
    base = BANK_LOAN_BASE
    if credit_score >= 500:
        bonus_tiers = max(0, (credit_score - 500) // 100)
        base += bonus_tiers * 10000
    elif credit_score < 300:
        base = BANK_LOAN_MIN
    return min(BANK_LOAN_MAX, max(BANK_LOAN_MIN, base))

# ============================================================================
# 宠物重生（Lv800准备期 → Lv999重生 → 属性暴击 2~10×）
# ============================================================================
REBIRTH_PREP_LEVEL = 800            # 进入重生准备期的等级
REBIRTH_MAX_LEVEL = 999             # 可执行重生的等级（渡劫满级）
REBIRTH_GEM_COST_DIAMOND = 10000    # 重生宝石钻石价格
REBIRTH_GEM_COST_JIFEN = 100000     # 重生宝石积分价格
REBIRTH_SACRIFICE_MIN_JIFEN = 10000 # 祭奠积分最低
REBIRTH_SACRIFICE_MIN_DIAMOND = 1000# 祭奠钻石最低
REBIRTH_KEEP_ITEMS = {              # 重生后保留的物品（长期养成投入，不随重生清零）
    *(f"{q}卡" for q in QUALITIES),  # 全部品质卡（普通碎片→混沌卡）
    *(f"{q}碎片" for q in QUALITIES),  # 全部品质碎片
    "宠物卡", "自动修炼卡", "定制卡",
}

# 重生属性倍率表：(倍率, 基础权重)，权重越大越容易出
# 倍率均为整数（无小数点），2/2.5→2、3/3.5→3 合并，总权重保持 1000
REBIRTH_MULTIPLIER_TABLE = [
    (2, 570),
    (3, 250),
    (4, 60),
    (5, 40),
    (6, 30),
    (7, 20),
    (8, 15),
    (9, 10),
    (10, 5),
]
REBIRTH_MULTIPLIER_TOTAL_WEIGHT = sum(w for _, w in REBIRTH_MULTIPLIER_TABLE)

# 祭奠提升高倍率权重：每 10000 积分或 1000 钻石 = 1 个祭奠点
# 每个祭奠点将最高 3 档倍率权重各 +5
REBIRTH_SACRIFICE_PER_POINT_JIFEN = 10000
REBIRTH_SACRIFICE_PER_POINT_DIAMOND = 1000
REBIRTH_SACRIFICE_WEIGHT_PER_POINT = 5
REBIRTH_SACRIFICE_MAX_POINTS = 50     # 最多 50 点祭奠


def rebirth_roll_multiplier(sacrifice_points: int = 0) -> int:
    """根据祭奠点数随机重生倍率。返回最终整数倍率值。"""
    import random as _random
    pts = min(sacrifice_points, REBIRTH_SACRIFICE_MAX_POINTS)
    adjusted = []
    n = len(REBIRTH_MULTIPLIER_TABLE)
    for i, (mult, w) in enumerate(REBIRTH_MULTIPLIER_TABLE):
        # 最高 3 档倍率（8×/9×/10×）享受祭奠加成
        if i >= n - 3 and pts > 0:
            w += REBIRTH_SACRIFICE_WEIGHT_PER_POINT * pts
        adjusted.append((mult, w))
    total = sum(w for _, w in adjusted)
    r = _random.randint(1, total)
    acc = 0
    for mult, w in adjusted:
        acc += w
        if r <= acc:
            return mult
    return 2


# ============================================================================
# 多宠物系统
# ============================================================================
PET_SLOTS_DEFAULT = 2          # 默认宠物席位
PET_SLOTS_MAX = 10             # 最大宠物席位
PET_SLOT_CARD_NAME = "宠物席位卡"  # 席位卡道具名
