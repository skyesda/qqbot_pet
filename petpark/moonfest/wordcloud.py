"""Deterministic, escaped blessing keyword cloud on generated stationery."""
import base64
from collections import Counter
from functools import lru_cache
from html import escape
import math
from pathlib import Path
import random
import re

from PIL import ImageFont

KEYWORDS = tuple(sorted(set('祖国 国庆 国庆快乐 繁荣昌盛 国泰民安 山河锦绣 锦绣山河 安居乐业 幸福安康 平安喜乐 岁月静好 蒸蒸日上 欣欣向荣 五谷丰登 风调雨顺 长治久安 人民幸福 家国团圆 盛世华诞 万家灯火 江山如画 砥砺前行 未来可期 前程似锦 万事顺遂 繁荣富强 国富民强 和平 幸福 平安 团圆 富强 昌盛 繁荣 山河 华夏 中国 祖国万岁 中华民族 中华儿女 母亲 强盛 美好 健康 安康 快乐 发展 腾飞 辉煌 复兴 希望 自豪 致敬 奋进 荣光 青春 梦想 盛世 祝福 人民 家园 民族 家国 祖国母亲 永远 万岁'.split()), key=lambda w: (-len(w), w)))


def keyword_counts(texts):
    result = Counter()
    for text in texts:
        remaining = str(text)
        found = set()
        for word in KEYWORDS:
            if word in remaining:
                found.add(word)
                remaining = remaining.replace(word, ' ')
        # Short complete clauses retain personal wishes beyond the common lexicon.
        for phrase in re.findall(r'[\u4e00-\u9fff]{2,8}', remaining):
            if phrase not in {'祝愿', '祝你', '愿你', '我们', '大家', '越来越', '祝大家'}:
                found.add(phrase)
        result.update(found)
    return dict(sorted(result.items(), key=lambda item: (-item[1], item[0]))[:60])


@lru_cache(maxsize=32)
def _font(size):
    for path in ['C:/Windows/Fonts/simkai.ttf', '/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc',
                 '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc']:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return None


def cloud_layout(counts):
    rng = random.Random(20261001)
    boxes, words = [], []
    maximum = max(counts.values(), default=1)
    for word, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        size = int(23 + 47 * math.sqrt(count / maximum))
        for size in range(size, 19, -4):
            font = _font(size)
            width = math.ceil(font.getlength(word) if font else len(word) * size) + 12
            height = size * 1.4 + 10
            if width > 610:
                continue
            for attempt in range(300):
                radius = min(1, attempt / 180)
                x = 310 - width / 2 + rng.uniform(-1, 1) * 260 * radius
                y = 255 - height / 2 + rng.uniform(-1, 1) * 235 * radius
                box = (x, y, x + width, y + height)
                if x < 5 or y < 5 or box[2] > 615 or box[3] > 510:
                    continue
                if any(x < b[2] and box[2] > b[0] and y < b[3] and box[3] > b[1] for b in boxes):
                    continue
                boxes.append(box)
                words.append({'word': word, 'count': count, 'size': size, 'x': round(x), 'y': round(y)})
                break
            else:
                continue
            break
    return words


@lru_cache(maxsize=1)
def _background():
    path = Path(__file__).parents[1] / 'assets/ui/national-wordcloud.webp'
    return 'data:image/webp;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')


def wordcloud_html(texts, people, settled=False, paid=0):
    counts = keyword_counts(texts)
    colors = ['#9c382b', '#264d43', '#8f682f', '#446958', '#a85237']
    words = ''.join(f'<span style="left:{w["x"]}px;top:{w["y"]}px;font-size:{w["size"]}px;color:{colors[i%len(colors)]}">{escape(w["word"])}</span>' for i, w in enumerate(cloud_layout(counts)))
    if not words:
        words = '<div class="empty">静候第一声祝福<br><small>通过审核的祝福将在这里汇聚</small></div>'
    phase = f'已开奖 · 已分配 {paid:,} 月华' if settled else '祝福汇聚中 · 21:00 开奖'
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>
*{box-sizing:border-box}html,body{margin:0;width:720px;background:#ff00ff}
.card{width:720px;height:900px;border:2px solid #bd995d;background:#f5efe2 url("''' + _background() + '''") center/cover;color:#183a34;font-family:"KaiTi","Noto Serif CJK SC","SimSun",serif;padding:95px 48px 0;text-align:center}
.eyebrow{font-size:16px;letter-spacing:5px;color:#866133}h1{font-size:48px;letter-spacing:6px;font-weight:normal;margin:17px 0;color:#9c382b}.stats{font-size:18px;line-height:1.7}.cloud{position:relative;width:620px;height:520px;margin:18px auto 0;background:rgba(251,246,233,.78)}.cloud span{position:absolute;white-space:nowrap;line-height:1.4}.foot{font-size:16px;line-height:1.9;color:#3c5144;background:rgba(251,246,233,.9);padding:8px 12px}.empty{padding-top:190px;font-size:30px;color:#79674b}.empty small{font-size:18px}
</style><main class="card"><div class="eyebrow">灵契仙途 · 十月一日</div><h1>山河同祝</h1><div class="stats">''' + f'{people} 位修士 · {len(texts)} 条有效祝福<br>{phase}' + '''</div><div class="cloud">''' + words + '''</div><div class="foot">字越大，越多祝福提到它<br>发送「国庆词云」随时查看 · 愿祖国繁荣昌盛</div></main></html>'''
