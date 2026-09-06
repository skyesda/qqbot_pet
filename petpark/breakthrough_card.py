"""Breakthrough result cards using GPT backgrounds and unchanged pet portraits."""
import base64
import html
import re
from functools import lru_cache
from pathlib import Path

THEMES = {
    '进化': ('evolution', '#a9ffe4', '灵力汇聚 · 生命蜕变'),
    '飞升': ('ascension', '#ffe7a5', '踏云登天 · 仙途初启'),
    '渡劫': ('tribulation', '#dcc4ff', '九霄雷动 · 问道苍穹'),
}


@lru_cache(maxsize=3)
def background(action):
    name = THEMES[action][0]
    path = Path(__file__).parent / 'assets' / 'ui' / f'{name}-ritual.png'
    return 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')


def card_html(pet, action, previous_stage, success, message, portrait_uri):
    _, accent, caption = THEMES[action]
    esc = html.escape
    title = action + ('成功' if success else '失败')
    # Keep the authoritative outcome (including costs, equipment and cooldown).
    clean = re.sub(r'[\U00010000-\U0010ffff\u2600-\u27bf\ufe0f]', '', message).replace('**', '').strip()
    clean = re.sub(r'^' + re.escape(title) + r'[！!]?\s*', '', clean)
    portrait = (f'<img src="{esc(portrait_uri, quote=True)}" alt="宠物立绘">'
                if portrait_uri else '<div class="missing">暂无宠物立绘</div>')
    stage = (f'{previous_stage} → {pet.get("stage", "")}' if success
             else f'{pet.get("stage", "")} · 静养后再战')
    return f'''<!DOCTYPE html><html><head><meta charset="utf-8"><style>
    *{{box-sizing:border-box}}body{{margin:0;background:#ff00ff;font-family:"Microsoft YaHei",sans-serif}}
    .card{{width:720px;min-height:1080px;color:#fff;--accent:{accent};
      background:linear-gradient(180deg,rgba(4,9,26,.35),transparent 38%,rgba(4,9,26,.25)),url('{background(action)}') center/cover;
      padding:42px 38px 30px;text-align:center}}
    .caption{{font-size:18px;letter-spacing:5px;color:var(--accent)}}
    h1{{font-size:62px;letter-spacing:8px;margin:10px 0;text-shadow:0 3px 24px #071326}}
    .name{{font-size:27px;overflow-wrap:anywhere;text-shadow:0 2px 8px #000}}
    .portrait{{width:340px;height:340px;margin:38px auto 28px;border-radius:50%;padding:18px;
      background:#fff;border:5px solid var(--accent);box-shadow:0 0 60px #ffffff90;overflow:hidden}}
    .portrait img{{width:100%;height:100%;object-fit:contain;border-radius:50%}}
    .missing{{color:#475569;padding-top:125px;font-size:24px}}
    .stage{{font-size:30px;font-weight:700;color:var(--accent);text-shadow:0 2px 9px #000;
      background:#08162dcc;border:1px solid #ffffff40;border-radius:16px;padding:18px 12px}}
    .result{{margin-top:24px;background:rgba(5,13,30,.88);border:1px solid #ffffff45;
      border-radius:20px;padding:24px 26px;text-align:left;font-size:24px;line-height:1.65;
      white-space:pre-line;overflow-wrap:anywhere}}
    .footer{{font-size:17px;color:#e1e8f2;margin-top:22px;letter-spacing:2px}}
    </style></head><body><div class="card"><div class="caption">{caption}</div>
    <h1>{title}</h1><div class="name">{esc(str(pet.get('nickname') or pet.get('species') or '灵宠'))}</div>
    <div class="portrait">{portrait}</div><div class="stage">{esc(stage)}</div>
    <div class="result">{esc(clean)}</div><div class="footer">灵契仙途 · 宠物养成</div>
    </div></body></html>'''
