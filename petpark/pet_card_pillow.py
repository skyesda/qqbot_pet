"""Direct raster layout for the fixed pet card; consumes an immutable HTML snapshot.

HTML remains the single source of displayed game values. Unknown layouts return
None so their CSS renderer remains available instead of silently losing fields.
"""
from __future__ import annotations

import base64
import io
import os
import re
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


class Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def text(self):
        return ''.join(c.text() if isinstance(c, Node) else c for c in self.children).strip()

    def find(self, name):
        found = []
        for c in self.children:
            if isinstance(c, Node):
                if name in c.attrs.get('class', '').split():
                    found.append(c)
                found.extend(c.find(name))
        return found


class CardParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in {'img', 'meta', 'br', 'hr', 'input', 'link'}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, text):
        if self.stack[-1].tag not in {'style', 'script'}:
            self.stack[-1].children.append(text)


@lru_cache(maxsize=16)
def font(size):
    for path in [os.environ.get('PETPARK_CARD_FONT', ''),
                 '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
                 '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
                 'C:/Windows/Fonts/msyh.ttc']:
        if path and Path(path).is_file():
            return ImageFont.truetype(path, size)
    raise RuntimeError('Chinese card font unavailable')


@lru_cache(maxsize=1)
def background():
    path = Path(__file__).parent / 'assets/ui/celestial-clouds.webp'
    with Image.open(path) as source:
        return source.convert('RGB')


def render_pet_card(html, width=900):
    if 'class="pet-layout"' not in html or '宠物灵鉴' not in html:
        return None
    if width != 900:
        return None
    parser = CardParser()
    parser.feed(html)
    root = parser.root
    # Do not apply this layout to an adventure card or a future schema.
    required = ['name', 'identity', 'rank', 'portrait-label', 'resource',
                'vitals', 'power', 'attributes', 'stats', 'abilities', 'foot']
    if any(len(root.find(k)) != 1 for k in required):
        return None
    def one(k):
        return root.find(k)[0]

    def lines(text, size, limit):
        f = font(size)
        result, line = [], ''
        for c in text:
            if c == '\n' or (line and f.getlength(line+c) > limit):
                result.append(line)
                line = ''
            if c != '\n':
                line += c
        result.append(line)
        return result

    ink, gold = '#244f53', '#efd392'
    left, right, col = 32, 459, 409
    # Measure before painting; long skills, names and resource values can grow.
    name = lines(one('name').text(), 34, 650)
    identity = lines(one('identity').text(), 17, 650)
    y0 = 130 + len(name)*44 + len(identity)*26
    panels = {}
    def rows(node):
        result = []
        for row in node.find('row'):
            fields = [c for c in row.children if isinstance(c, Node)]
            if len(fields) != 2:
                raise ValueError('Unknown pet row')
            key, value = fields[0].text(), fields[1].text()
            result.append((key, lines(value, 17, col-112)))
        return result
    attribute_rows, ability_rows = rows(one('attributes')), rows(one('abilities'))
    right_h = 66 + 12 + sum(max(36, len(v)*26+10) for k,v in attribute_rows)+20
    right_h += 74+16+sum(max(40, len(v)*26+14) for k,v in ability_rows)+18
    head = next(c for c in one('resource').children if isinstance(c, Node))
    resource_fields = [c.text() for c in head.children if isinstance(c, Node)]
    if len(resource_fields) != 2:
        return None
    resource_value = lines(resource_fields[1], 16, col-95)
    resource_rows = rows(one('resource'))
    resource_h = max(66, 30+len(resource_value)*24)+sum(max(36,len(v)*26+10) for k,v in resource_rows)
    bars = one('vitals').find('bar-row')
    bar_h = [max(36, len(lines(b.find('bar-n')[0].text(), 14, 160))*22+10) for b in bars]
    left_h = 360+14+resource_h+14+sum(bar_h)+24
    bottom = y0+max(left_h, right_h)
    tags = [c.text() for n in root.find('tags') for c in n.children if isinstance(c, Node)]
    tag_lines = lines('  ·  '.join(tags), 16, 814) if tags else []
    warning = [line for n in root.find('warn') for line in lines(n.text(),17,800)]
    height = bottom+len(tag_lines)*28+(len(warning)*28+22 if warning else 0)+100
    canvas = background().resize((width, height), Image.Resampling.BILINEAR)
    draw = ImageDraw.Draw(canvas)
    def text(x,y,value,size=17,color=ink):
        draw.text((x,y),value,font=font(size),fill=color)
    def panel(x,y,w,h):
        draw.rounded_rectangle((x,y,x+w,y+h),radius=3,fill='#f5f1df',outline='#c4ab73',width=1)
    def paint_rows(x,y,items):
        for k,v in items:
            h=max(36,len(v)*26+10)
            text(x+15,y+6,k,color='#637365')
            for i,line in enumerate(v):
                text(x+col-15-font(17).getlength(line),y+6+i*26,line)
            draw.line((x+14,y+h,x+col-14,y+h),fill='#d8d5c5')
            y+=h
        return y
    def progress(x,y,w,pct,color):
        draw.rectangle((x,y,x+w,y+10),fill='#d8ddce')
        draw.rectangle((x,y,x+max(0,min(w,w*pct/100)),y+10),fill=color)
    def percent(node):
        match=re.search(r'width:([\d.]+)%',node.attrs.get('style',''))
        return float(match.group(1)) if match else 0
    for inset in (1,7,12):
        draw.rectangle((inset,inset,width-inset-1,height-inset-1),outline=gold,width=2 if inset==1 else 1)
    title=one('mast-title').text(); caption=one('mast-caption').text()
    text((width-font(38).getlength(title))/2,35,title,38,gold)
    text((width-font(14).getlength(caption))/2,85,caption,14,'#e8dfc4')
    y=126
    for line in name:
        text(left,y,line,34,'#fff4d5'); y+=44
    for line in identity:
        text(left,y,line,17,'#e8dfc4');y+=26
    rank=one('rank').text();rw=max(100,font(20).getlength(rank)+32)
    draw.rectangle((width-rw-32,130,width-32,175),fill='#e7cc88',outline=gold)
    text(width-rw-16,139,rank,20)
    # Portrait is embedded data only; never perform network/file reads from HTML.
    panel(left,y0,col,360)
    portraits=root.find('portrait')
    if portraits:
        uri=portraits[0].attrs.get('src','')
        if not uri.startswith('data:image/') or ';base64,' not in uri:
            return None
        with Image.open(io.BytesIO(base64.b64decode(uri.split(',',1)[1]))) as source:
            picture=ImageOps.contain(source.convert('RGB'),(col-14,310),Image.Resampling.LANCZOS)
        canvas.paste(picture,(left+(col-picture.width)//2,y0+7+(310-picture.height)//2))
    else:
        text(left+140,y0+140,'暂无立绘',20)
    draw.rectangle((left+7,y0+319,left+col-7,y0+353),fill='#19484f')
    text(left+100,y0+325,one('portrait-label').text(),16,gold)
    y=y0+374
    panel(left,y,col,resource_h)
    text(left+15,y+12,resource_fields[0],16)
    for i,line in enumerate(resource_value):
        text(left+col-15-font(16).getlength(line),y+12+i*24,line,16)
    progress(left+15,y+resource_h-20,col-30,percent(one('resource').find('fill')[0]),'#c29b55')
    if resource_rows:
        paint_rows(left,y+12+len(resource_value)*24,resource_rows)
    y+=resource_h+14
    panel(left,y,col,sum(bar_h)+24);y+=12
    for bar,h in zip(bars,bar_h):
        text(left+15,y+4,bar.find('bar-k')[0].text(),16)
        fill=bar.find('fill')[0]
        color='#bd6559' if 'hp' in fill.attrs.get('class','') else '#57999c' if 'en' in fill.attrs.get('class','') else '#c29b55'
        progress(left+60,y+12,165,percent(fill),color)
        for i,line in enumerate(lines(bar.find('bar-n')[0].text(),14,160)):
            text(left+col-15-font(14).getlength(line),y+4+i*22,line,14)
        y+=h
    y=y0
    draw.rectangle((right,y,right+col,y+66),fill='#eed796',outline='#c4ab73')
    fields=[c.text() for c in one('power').children if isinstance(c,Node)]
    text(right+16,y+22,fields[0]);text(right+col-16-font(30).getlength(fields[1]),y+13,fields[1],30)
    y+=78;h=sum(max(36,len(v)*26+10) for k,v in attribute_rows)+20
    panel(right,y,col,h);paint_rows(right,y+10,attribute_rows);y+=h+12
    stats=one('stats').find('stat')
    for i,stat in enumerate(stats):
        fields=[c.text() for c in stat.children if isinstance(c,Node)]
        x=right+i*col/3;panel(x,y,col/3-1,74)
        text(x+40,y+10,fields[0],15,'#637365')
        for j,line in enumerate(lines(fields[1],22,col/3-12)):
            text(x+(col/3-font(22).getlength(line))/2,y+32+j*26,line,22)
    y+=90;h=sum(max(40,len(v)*26+14) for k,v in ability_rows)+18
    panel(right,y,col,h);paint_rows(right,y+8,ability_rows)
    y=bottom+12
    for line in tag_lines:
        text(left,y,line,16,gold);y+=28
    if warning:
        panel(left,y,836,len(warning)*28+16)
        for line in warning:
            text(left+12,y+8,line,17,'#8a342f');y+=28
        y+=20
    y=height-72
    draw.rectangle((left,y,width-left,y+42),fill='#10383e')
    fields=[c.text() for c in one('foot').children if isinstance(c,Node)]
    text(left+14,y+12,fields[0],15,'#f8ebc9')
    text(width-left-14-font(15).getlength(fields[-1]),y+12,fields[-1],15,'#f8ebc9')
    return canvas
