"""Cultivator portrait and six equipment slots; local GPT-generated artwork."""
import base64
from functools import lru_cache
from html import escape
from pathlib import Path

from .. import card_theme
from . import content as c
from .combat import hero_sheet
from .power import power_breakdown

ASSETS = Path(__file__).resolve().parents[1] / "assets" / "cultivator"
PORTRAITS = {"剑修": "sword", "体修": "body", "灵修": "spirit", "魔修": "demon"}
GEAR_ASSET_PREFIX = {"剑修": "sword", "体修": "body", "灵修": "spirit", "魔修": "demon"}


def portrait_name(adventure):
    return PORTRAITS.get(adventure.get("profession"), "sword") + (
        "-female" if adventure.get("gender") == "女" else "-male")


@lru_cache(maxsize=16)
def asset_uri(name):
    """优先用压缩 WebP，缺失才回退原 PNG，避免内嵌大图把 HTML 撑到 MB 级拖慢渲染。"""
    webp = ASSETS / f"{name}.webp"
    src = webp if webp.is_file() else ASSETS / f"{name}.png"
    mime = "image/webp" if src == webp else "image/png"
    return f"data:{mime};base64," + base64.b64encode(src.read_bytes()).decode("ascii")


def equipment_summary(adventure):
    eq = adventure.get("equipment", {})
    profession = adventure.get("profession", "剑修")
    tiers = adventure.get("equip_tier", {})
    return " · ".join(
        f"{c.gear_name(profession, slot, tiers.get(slot, 0))} Lv{eq.get(slot, 0)}"
        for slot, _label in c.GEAR.values()
    )


def card_html(player, key, equipment=False):
    a = player["adventure"]
    s = hero_sheet(a, player)
    bd = power_breakdown(player, key)
    eq = a.get("equipment", {})
    slots = []
    profession = a.get("profession", "剑修")
    asset_prefix = GEAR_ASSET_PREFIX.get(profession, "sword")
    for _generic_name, (slot, label) in c.GEAR.items():
        rank = eq.get(slot, 0)
        tier = a.get("equip_tier", {}).get(slot, 0)
        name = c.gear_name(profession, slot, tier)
        slots.append(f'<div class="gear"><img src="{asset_uri(asset_prefix + "-" + slot)}" alt="{escape(name)}">'
                     f'<div class="gear-name"><span>{escape(name)}</span><b>Lv{rank}</b></div>'
                     f'<small>{"尚未强化" if rank == 0 else "提升" + label}</small></div>')
    bonus = a.get("bonus") or {}
    # 悟性加点分配（攻/防/血/速）直接以 +N 徽标附着在各属性后，直观呈现。
    stats = "".join(
        f'<div><span>{label}</span><b>{s[field]}'
        + (f'<em>+{int(bonus.get(field, 0))}</em>' if bonus.get(field, 0) else '')
        + '</b></div>'
        for label, field in [("血量上限", "hp"), ("攻击", "atk"), ("防御", "def"),
                             ("速度", "speed"), ("悟性", "wudao"), ("根骨", "gengu")])
    # 灵根／属性（五行相克）／神通：修士「道基」信息原图缺失，单独成行展示。
    # 灵根补实际加成（金→攻+10%）、神通补被动效果（灵台清明→攻+4%/防+4%），不再只给名字。
    root = escape(str(a.get("spirit_root") or "无"))
    root_bonus = escape(c.root_bonus(a.get("spirit_root") or ""))
    element = escape(str(c.element_line(c.hero_element(a))))
    _tactics = a.get("tactics", [])
    tactics = escape(" · ".join(
        t + (f"（{c.tactic_effect(t)}）" if c.tactic_effect(t) else "") for t in _tactics) or "无")
    lineage = (f'<div class="lineage-grid"><div><span>灵根 · 属性</span><b>{root}</b>'
               f'<small>{root_bonus} · {element}</small></div>'
               f'<div><span>已悟神通</span><b>{tactics}</b></div></div>')
    # 体力条：宗门任务/探索/镇守消耗，画成一根进度条让玩家一眼看懂。
    _st = int(a.get("stamina", 100)); _mx = int(a.get("stamina_max", 100)) or 100
    _pct = max(0, min(100, int(_st / _mx * 100)))
    _stamina_bar = f'<span class="barwrap"><span class="bar" style="width:{_pct}%"></span></span>'
    # 结契灵宠属性：随战斗克制生效，原图未展示，补上并标出相克。
    _companion = next((p for p in player.get("pets", []) if p.get("pet_id") == a.get("companion_pet_id")), None)
    pet_element = c.element_line(_companion.get("element")) if _companion else "无属性"
    details = (f'<div class="power-formula"><span>战力构成</span>'
               f'<b>本体 {bd["hero"]} <i>＋</i> 灵宠 {bd["pet_contrib"]} <i>＋</i> 坐骑 {bd["mount_contrib"]}</b>'
               f'<small>灵宠计 15% · 坐骑计 10% · 道侣 ×{bd["partner"]:.2f} · 洞天 ×{bd["heaven_margin"]:.2f}</small></div>') if bd else ""
    # 升级进度：显示距下一级/破境还需多少修为，或已可突破/渡劫。
    lv_cap = c.realm_cap(a["realm"])
    if a["level"] >= c.MAX_LEVEL:
        level_msg = "已臻化境 · 满级圆满"
    elif a["level"] >= lv_cap:
        level_msg = f"已至{c.REALMS[a['realm']]}巅峰 Lv{a['level']}，待「渡劫」破境"
    else:
        cost = 60 + a["level"] * 20
        need = cost - int(a["cultivation"])
        level_msg = f"可突破 → Lv{a['level'] + 1}" if need <= 0 else f"距突破还需 {need} 修为"
    dashboard = (
        '<div class="dashboard">'
        f'<div class="info-card"><span>修行资源</span><b>修为 {a["cultivation"]}</b><small>灵材 {a["ore"]} · 副本收益 {a.get("rewards", 0)}/8 · 首领 {a.get("world_hits", 0)}/3</small></div>'
        f'<div class="info-card stamina"><span>当前体力</span><b>{_st}/{_mx}</b>{_stamina_bar}<small>每分钟恢复 1 点</small></div>'
        f'<div class="info-card"><span>战斗配置</span><b>{escape(str(a["style"]))} · {escape(str(a.get("pet_role", "攻击")))}</b><small>{escape(str(bd["pet_name"] if bd else "引路灵蝶"))} · {escape(str(pet_element))}</small></div>'
        f'<div class="info-card progress"><span>修炼进度</span><b>{escape(str(level_msg))}</b><small>悟性点 {a.get("insight", 0)} 可用</small></div>'
        '</div>')
    footer = ('<div class="actions">'
              '<div><b>装备养成</b><span>锻造 · 装备进阶 · 洗炼</span><small>格式：指令＋装备名</small></div>'
              '<div><b>修士培养</b><span>修士修炼 · 修士突破</span><small>悟性加点 · 修士配装</small></div>'
              '<div><b>角色管理</b><span>道号 · 性别</span><small>我的体力 · 今日修行</small></div>'
              '</div>')
    css = """
    .card{width:720px;padding:26px;box-sizing:border-box;color:#304c48;background:linear-gradient(145deg,#fcf8ed,#e6ece2)}
    .identity{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:16px;color:#304c48}
    .identity>div:first-child{min-width:0}.identity h1{font-size:30px;margin:3px 0;overflow-wrap:anywhere;max-width:450px;line-height:1.08}
    .eyebrow{font-size:13px;letter-spacing:3px;color:#8d713e}.identity p{margin:4px 0;font-size:15px}
    .power{display:block;text-align:right;flex-shrink:0;padding:8px;margin:0}.power strong{display:block;font-size:32px;color:#947035}
    .loadout{display:grid;grid-template-columns:126px 1fr 126px;gap:14px;align-items:stretch}
    .gear-column{display:grid;gap:12px;grid-template-rows:repeat(3,1fr);min-width:0}
    .gear{border:1px solid #c5ad77;background:#fffaf0e8;padding:7px;text-align:center;box-shadow:0 3px 12px #65562d12;min-width:0}
    .gear img{width:108px;height:105px;object-fit:contain;display:block}
    .gear-name{display:flex;justify-content:space-between;gap:4px;font-size:13px;align-items:center;white-space:nowrap;min-width:0}
    .gear-name span{min-width:0}.gear-name b{font-size:13px;color:#946d2f;flex-shrink:0}.gear small{font-size:12px;color:#7e8879}
    .portrait{height:auto;position:relative;overflow:hidden;border:1px solid #c5ad77;background:#f4f0e5}
    .portrait img{width:100%;height:100%;object-fit:contain;display:block;position:absolute}
    .stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:16px}
    .stats div{padding:9px 12px;background:#fcfaf0cc;border-bottom:1px solid #cabc98;display:flex;justify-content:space-between}
    .stats em{font-style:normal;font-size:12px;color:#3f8f4f;margin-left:6px}
    .dashboard{display:grid;grid-template-columns:repeat(2,1fr);gap:9px;margin-top:14px}
    .info-card{min-height:67px;padding:10px 13px;box-sizing:border-box;background:#fffaf0cc;border:1px solid #d3c394;display:grid;grid-template-columns:1fr auto;gap:3px 10px;align-items:center}
    .info-card span,.lineage-grid span,.power-formula>span{font-size:12px;letter-spacing:1px;color:#947035}
    .info-card b{font-size:16px;text-align:right}.info-card small{grid-column:1/-1;color:#6c796e;font-size:12px;overflow-wrap:anywhere}
    .info-card.progress b{font-size:14px}.stamina .barwrap{grid-column:1/-1}
    .barwrap{display:block;width:100%;height:9px;background:#e7e3d0;border-radius:6px;overflow:hidden;border:1px solid #cabc98;box-sizing:border-box}
    .bar{display:block;height:100%;background:linear-gradient(90deg,#6fae5f,#4e8d43);border-radius:6px}
    .lineage-grid{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:9px}
    .lineage-grid>div{padding:9px 12px;background:#fffaf0cc;border:1px solid #d3c394;display:flex;flex-direction:column;gap:3px;min-width:0}
    .lineage-grid b{font-size:14px;color:#5f4730;overflow-wrap:anywhere}.lineage-grid small{font-size:12px;color:#6c796e}
    .power-formula{margin-top:9px;padding:9px 13px;border-left:4px solid #b79149;background:#f1ead8;display:grid;grid-template-columns:80px 1fr;gap:3px 8px;align-items:center}
    .power-formula b{font-size:13px;text-align:right}.power-formula i{font-style:normal;color:#b79149;margin:0 3px}.power-formula small{grid-column:1/-1;text-align:right;color:#6c796e;font-size:11px}
    .foot{display:block;margin-top:12px}.actions{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:#d1bb82;border:1px solid #c1a565}
    .actions div{background:#214e50;color:#f5f0dc;padding:10px 8px;text-align:center;display:flex;flex-direction:column;gap:4px}
    .actions b{font-size:14px;color:#f1d48b}.actions span{font-size:12px}.actions small{font-size:11px;color:#c5d8cf}
    """
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'><style>" + card_theme.stylesheet("pet") + css +
            '</style></head><body><div class="card"><div class="identity"><div>'
            f'<div class="eyebrow">灵契仙途 · 我的修士</div>'
            f'<h1>{escape(str(a["name"]))}</h1><p>{escape(str(a["profession"]))} · {escape(str(a.get("gender", "男")))} · '
            f'{c.REALMS[a["realm"]]} Lv{a["level"]} · {c.HEAVENS[a.get("heaven", 0)]["name"]}洞天</p></div>'
            f'<div class="power">总战力<strong>{bd["total"] if bd else 0}</strong></div></div>'
            f'<div class="loadout"><div class="gear-column">{"".join(slots[:3])}</div>'
            f'<div class="portrait"><img src="{asset_uri(portrait_name(a))}" alt="修士立绘"></div>'
            f'<div class="gear-column">{"".join(slots[3:])}</div></div>'
            f'<div class="stats">{stats}</div>{dashboard}{lineage}{details}'
            f'<div class="foot">{footer}</div></div></body></html>')
