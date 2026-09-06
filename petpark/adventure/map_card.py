"""Personalized twenty-stage atlas using GPT-generated map and landmarks."""
import base64
from functools import lru_cache
from html import escape
from pathlib import Path

from . import content as c

ASSETS = Path(__file__).resolve().parents[1] / 'assets' / 'adventure-map'
CHAPTERS = ('初入山海', '幽冥寻踪', '水火问心', '云海登仙', '星墟证道')


@lru_cache(maxsize=21)
def asset_uri(name):
    return 'data:image/png;base64,' + base64.b64encode((ASSETS / f'{name}.png').read_bytes()).decode('ascii')


def stage_state(a, stage):
    """Match the predecessor and level checks in AdventureService exactly."""
    cleared = set(a.get('cleared', []))
    if stage['id'] in cleared:
        return 'cleared', '已通关'
    if stage['id'] != '1' and str(int(stage['id']) - 1) not in cleared:
        return 'locked', '未解锁'
    if a.get('level', 1) < stage['level']:
        return 'level', f'需 Lv{stage["level"]}'
    return 'ready', '可挑战'


def map_html(player, enemy_percent=100):
    a = player['adventure']
    stages = list(c.MAPS.values())
    cleared = set(a.get('cleared', [])) & set(c.MAPS)
    next_stage = next((s for s in stages if s['id'] not in cleared and
                       (s['id'] == '1' or str(int(s['id']) - 1) in cleared)), None)
    if next_stage:
        state, _ = stage_state(a, next_stage)
        recommendation = (f'下一站 · {next_stage["name"]}　'
                          + (f'发送「历练 {next_stage["id"]}」' if state == 'ready'
                             else f'修士升至 Lv{next_stage["level"]} 后挑战'))
    else:
        recommendation = '山海尽览 · 二十关已通关，可继续挑战困难历练'
    tier = c.HEAVENS[a.get('heaven', 0)]
    sections = []
    for chapter, title in enumerate(CHAPTERS):
        nodes = []
        chapter_stages = stages[chapter * 4:chapter * 4 + 4]
        for s in chapter_stages:
            state, label = stage_state(a, s)
            hard = ' · 困难已过' if 'hard:' + s['id'] in a.get('milestones', []) else ''
            nodes.append(f'<div class="node {state}" data-stage="{s["id"]}" data-state="{state}">'
                         f'<div class="landmark"><img src="{asset_uri(s["id"].zfill(2))}" alt="{s["name"]}">'
                         f'<b class="number">{s["id"].zfill(2)}</b></div>'
                         f'<div class="node-info"><h3>{s["name"]}</h3><p>Lv{s["level"]} · {s["boss"]}</p>'
                         f'<span class="status">{label}{hard}</span></div></div>')
        sections.append(f'<section class="chapter chapter-{chapter}"><div class="chapter-title">'
                        f'<span>卷 {"一二三四五"[chapter]}</span><h2>{title}</h2>'
                        f'<small>{sum(s["id"] in cleared for s in chapter_stages)} / 4</small></div>'
                        f'<div class="route {"reverse" if chapter % 2 else ""}">{"".join(nodes)}</div></section>')
    css = (ASSETS / 'map.css').read_text(encoding='utf-8')
    return ('<!DOCTYPE html><html><head><meta charset="utf-8"><style>' + css + '</style></head><body>'
            f'<div class="card"><img class="world" src="{asset_uri("world")}" alt="山海仙途地图">'
            '<div class="veil"></div><header><div class="eyebrow">灵契仙途 · 山海历练</div>'
            '<div class="heading"><div><h1>山海仙途图</h1><p>一程山海，一步登仙</p></div>'
            f'<div class="progress"><strong>{len(cleared):02}</strong><span> / 20<br>历练通关</span></div></div>'
            f'<div class="meta">{escape(str(a.get("name", "修士")))} · Lv{a.get("level", 1)}'
            f'　｜　{tier["name"]}洞天 · 敌人 ×{tier["enemy"] * enemy_percent / 100:g}</div>'
            f'<div class="recommendation">{recommendation}</div></header>'
            '<main>' + ''.join(sections) + '</main><footer>'
            '<div class="legend"><span class="cleared">已通关</span><span class="ready">可挑战</span>'
            '<span class="level">等级不足</span><span class="locked">未解锁</span></div>'
            '<p>发送「历练 序号」前往关卡 · 通关上一关后解锁下一关</p>'
            '<p>通关后可发送「历练 序号 困难」· 灵材额外＋2，同样占一次收益</p>'
            f'<div class="daily">今日副本收益 {a.get("rewards", 0)} / {c.DAILY_REWARDS}　·　失败不消耗收益次数</div>'
            '</footer></div></body></html>')
