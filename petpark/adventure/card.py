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
PORTRAITS = {"剑修": "sword", "体修": "body", "灵修": "spirit"}


def portrait_name(adventure):
    return PORTRAITS.get(adventure.get("profession"), "sword") + (
        "-female" if adventure.get("gender") == "女" else "-male")


@lru_cache(maxsize=12)
def asset_uri(name):
    return "data:image/png;base64," + base64.b64encode((ASSETS / f"{name}.png").read_bytes()).decode("ascii")


def equipment_summary(adventure):
    eq = adventure.get("equipment", {})
    return " · ".join(f"{name} Lv{eq.get(slot, 0)}" for name, (slot, _) in c.GEAR.items())


def card_html(player, key, equipment=False):
    a = player["adventure"]
    s = hero_sheet(a, player)
    bd = power_breakdown(player, key)
    eq = a.get("equipment", {})
    slots = []
    for name, (slot, label) in c.GEAR.items():
        rank = eq.get(slot, 0)
        slots.append(f'<div class="gear"><img src="{asset_uri(slot)}" alt="{name}">'
                     f'<div class="gear-name">{name}<b>Lv{rank}</b></div>'
                     f'<small>{"尚未强化" if rank == 0 else "提升" + label}</small></div>')
    stats = "".join(f'<div><span>{label}</span><b>{s[field]}</b></div>' for label, field in
                    [("性命", "hp"), ("攻击", "atk"), ("防御", "def"), ("速度", "speed"), ("悟性", "wudao"), ("根骨", "gengu")])
    details = (f'本体 {bd["hero"]} · 灵宠贡献 {bd["pet_contrib"]} ×40% · 坐骑贡献 {bd["mount_contrib"]} ×20%'
               f'<br>道侣 ×{bd["partner"]:.2f} · 洞天 ×{bd["heaven_margin"]:.2f}') if bd else ""
    footer = ('锻造 ' + c.GEAR_NAMES + '<br>每级消耗 3＋当前等级×2 灵材，强化上限为修士等级。') if equipment else (
        '修士修炼 · 修士突破 · 修士装备 · 道号 · 性别')
    css = """
    .card{width:720px;padding:26px;box-sizing:border-box;color:#304c48;background:linear-gradient(145deg,#fcf8ed,#e6ece2)}
    .identity{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;color:#304c48}
    .identity h1{font-size:30px;margin:3px 0;overflow-wrap:anywhere;max-width:450px}
    .eyebrow{font-size:13px;letter-spacing:3px;color:#8d713e}.identity p{margin:4px 0;font-size:15px}
    .power{display:block;text-align:right;flex-shrink:0;padding:8px;margin:0}.power strong{display:block;font-size:32px;color:#947035}
    .loadout{display:grid;grid-template-columns:126px 1fr 126px;gap:14px;align-items:stretch}
    .gear-column{display:grid;gap:12px;grid-template-rows:repeat(3,1fr)}
    .gear{border:1px solid #c5ad77;background:#fffaf0e8;padding:7px;text-align:center;box-shadow:0 3px 12px #65562d12}
    .gear img{width:108px;height:105px;object-fit:contain;display:block}
    .gear-name{display:flex;justify-content:space-between;gap:4px;font-size:16px;align-items:center}
    .gear-name b{font-size:14px;color:#946d2f}.gear small{font-size:12px;color:#7e8879}
    .portrait{height:auto;position:relative;overflow:hidden;border:1px solid #c5ad77;background:#f4f0e5}
    .portrait img{width:100%;height:100%;object-fit:contain;display:block;position:absolute}
    .stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:16px}
    .stats div{padding:9px 12px;background:#fcfaf0cc;border-bottom:1px solid #cabc98;display:flex;justify-content:space-between}
    .resources,.details{font-size:14px;line-height:1.8;text-align:center;margin-top:13px;overflow-wrap:anywhere}
    .details{font-size:12px;color:#6c796e}.foot{display:block;font-size:13px;line-height:1.8;margin-top:16px;text-align:center}
    """
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'><style>" + card_theme.stylesheet("pet") + css +
            '</style></head><body><div class="card"><div class="identity"><div>'
            f'<div class="eyebrow">灵契仙途 · {"修士装备" if equipment else "我的修士"}</div>'
            f'<h1>{escape(str(a["name"]))}</h1><p>{escape(str(a["profession"]))} · {escape(str(a.get("gender", "男")))} · '
            f'{c.REALMS[a["realm"]]} Lv{a["level"]} · {c.HEAVENS[a.get("heaven", 0)]["name"]}洞天</p></div>'
            f'<div class="power">总战力<strong>{bd["total"] if bd else 0}</strong></div></div>'
            f'<div class="loadout"><div class="gear-column">{"".join(slots[:3])}</div>'
            f'<div class="portrait"><img src="{asset_uri(portrait_name(a))}" alt="修士立绘"></div>'
            f'<div class="gear-column">{"".join(slots[3:])}</div></div>'
            f'<div class="stats">{stats}</div><div class="resources">修为 {a["cultivation"]} · 灵材 {a["ore"]}'
            f' · 功法 {escape(str(a["style"]))}<br>灵宠 {escape(str(bd["pet_name"] if bd else "引路灵蝶"))}'
            f' · {escape(str(a.get("pet_role", "攻击")))} · 今日副本收益 {a.get("rewards", 0)}/8'
            f' · 首领挑战 {a.get("world_hits", 0)}/3</div><div class="details">{details}</div>'
            f'<div class="foot">{footer}</div></div></body></html>')
