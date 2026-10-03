"""Pillow layouts for every built-in bot card, from immutable HTML snapshots.

This is a semantic card renderer, not a general CSS browser. Only recognized
game layouts enter it; new/unknown documents retain the browser fallback.
No scripts run and no URL or filesystem path from HTML is fetched.
"""
from __future__ import annotations

import base64
import io
import math
import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps, ImageFont

from .pet_card_pillow import CardParser, Node, font, render_pet_card

ASSETS = Path(__file__).parent / 'assets/ui'
INK, GOLD, PAPER = '#244f53', '#dfc385', '#f7f2e3'
INLINE = {'span', 'b', 'strong', 'em', 'i', 'small', 'k', 'v', 'br'}
SKIP = {'script', 'style', 'meta', 'link'}


@lru_cache(maxsize=64)
def stationery_font(size):
    for path in ('C:/Windows/Fonts/simkai.ttf', '/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc',
                 '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc'):
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return font(size)


def children(node):
    return [c for c in node.children if isinstance(c, Node) and c.tag not in SKIP]


def classes(node):
    return set(node.attrs.get('class', '').split())


def content(node):
    """Preserve authored line breaks and inline spacing, never re-evaluate values."""
    if node.tag == 'br':
        return '\n'
    if node.tag in SKIP:
        return ''
    return ''.join(content(c) if isinstance(c, Node) else c for c in node.children).strip()


@lru_cache(maxsize=128)
def asset(name):
    with Image.open(ASSETS / name) as source:
        return source.convert('RGB')


def embedded(uri):
    if not uri.startswith('data:image/') or ';base64,' not in uri:
        raise ValueError('Card image must be embedded data')
    raw = base64.b64decode(uri.split(',', 1)[1], validate=True)
    with Image.open(io.BytesIO(raw)) as source:
        return source.convert('RGB')


class Painter:
    """Measure first, then paint: no fixed screenshot height or hidden tail."""
    def __init__(self, width, theme, audit=None):
        self.width, self.theme = width, theme
        self.image = self.draw = None
        self.widths = {}
        self.audit = audit
        self.font = stationery_font if theme in {'national','cloud'} else font

    def wrap(self, value, size, width):
        output, line, used = [], '', 0
        f = self.font(size)
        for char in str(value):
            if char == '\n':
                output.append(line); line, used = '', 0
                continue
            key = (size, char)
            cw = self.widths.get(key)
            if cw is None:
                cw = self.widths[key] = f.getlength(char)
            if line and used+cw > max(1, width):
                output.append(line); line, used = '', 0
            line += char; used += cw
        if line or not output:
            output.append(line)
        return output

    def text(self, value, x, y, width, size=18, color=INK, align='left', paint=False):
        lines = self.wrap(value, size, width)
        step = math.ceil(size*1.45)
        if paint:
            if self.audit is not None:
                self.audit.append(str(value))
            for i, line in enumerate(lines):
                xx = x
                if align == 'center':
                    xx += max(0, (width-self.font(size).getlength(line))/2)
                elif align == 'right':
                    xx += max(0, width-self.font(size).getlength(line))
                self.draw.text((round(xx), round(y+i*step)), line, font=self.font(size), fill=color)
        return len(lines)*step

    def box(self, x, y, w, h, paint, fill=PAPER):
        if paint:
            self.draw.rounded_rectangle((x,y,x+w,y+h), radius=4, fill=fill, outline='#c6af79')

    def picture(self, node, x, y, w, h, paint, cover=False, circle=False):
        if not paint:
            return h
        im = embedded(node.attrs.get('src', ''))
        im = ImageOps.fit(im, (int(w),int(h)), method=Image.Resampling.BILINEAR) if cover else ImageOps.contain(im,(int(w),int(h)),Image.Resampling.BILINEAR)
        px,py=round(x+(w-im.width)/2),round(y+(h-im.height)/2)
        if circle:
            mask=Image.new('L',im.size);ImageDraw.Draw(mask).ellipse((0,0,im.width-1,im.height-1),fill=255)
            self.image.paste(im,(px,py),mask)
        else:
            self.image.paste(im,(px,py))
        return h

    def grid(self, nodes, x, y, w, columns, paint, gap=12):
        cw=(w-gap*(columns-1))/columns
        yy=y
        for offset in range(0,len(nodes),columns):
            row=nodes[offset:offset+columns]
            heights=[self.node(n,x+i*(cw+gap),yy,cw,False) for i,n in enumerate(row)]
            for i,n in enumerate(row):
                self.node(n,x+i*(cw+gap),yy,cw,paint)
            yy+=max(heights,default=0)+gap
        return max(0,yy-y-gap)

    def stack(self, nodes, x, y, w, paint, gap=10):
        yy=y
        for n in nodes:
            yy+=self.node(n,x,yy,w,paint)+gap
        return max(0,yy-y-gap)

    def node(self, n, x, y, w, paint):
        cs=classes(n); kids=children(n)
        if n.tag in SKIP or n.tag=='svg' or 'world' in cs or 'veil' in cs:
            return 0
        if n.tag=='img':
            h=105 if 'gear-image' in cs else min(380,w*1.1)
            return self.picture(n,x,y,w,h,paint)
        if 'masthead' in cs and self.theme=='national':
            return 288+self.stack(kids,x,y+288,w,paint)
        if 'masthead' in cs:
            return self.stack(kids,x,y,w,paint)
        if 'chapter-title' in cs:
            return self.text(' · '.join(content(c) for c in kids),x,y,w,23,'#284c48',paint=paint)
        if n.find('barwrap') and all(c.tag in INLINE for c in kids):
            value='  '.join(content(c) for c in kids if content(c))
            h=self.text(value,x,y,w,16,paint=paint)
            for bar in n.find('barwrap'):
                h+=self.node(bar,x,y+h,w,paint)
            return h
        if cs & {'bar','barwrap','fill'}:
            match=re.search(r'width:([\d.]+)%',n.attrs.get('style',''))
            fill=n
            if not match:
                found=n.find('fill') or n.find('bar')
                if found:
                    fill=found[0];match=re.search(r'width:([\d.]+)%',fill.attrs.get('style',''))
            pct=float(match.group(1)) if match else 0
            if paint:
                self.draw.rectangle((x,y+3,x+w,y+13),fill='#d7dace')
                self.draw.rectangle((x,y+3,x+w*max(0,min(100,pct))/100,y+13),fill='#578780')
            return 18
        if 'loadout' in cs:
            if len(kids)!=3:
                raise ValueError('Unknown six-slot loadout')
            side=round(w*.2);gap=12;middle=w-2*side-2*gap
            heights=[self.stack(children(kids[0]),x,y,side,False),self.stack(children(kids[2]),x,y,side,False)]
            h=max(heights)
            self.box(x+side+gap,y,middle,h,paint)
            images=kids[1].find('portrait') or children(kids[1])
            if images:
                self.picture(images[0],x+side+gap+4,y+4,middle-8,h-8,paint)
            self.stack(children(kids[0]),x,y,side,paint)
            self.stack(children(kids[2]),x+side+gap+middle+gap,y,side,paint)
            return h
        if 'gear' in cs:
            imgs=[c for c in kids if c.tag=='img']
            texts=[c for c in kids if c.tag!='img']
            th=self.stack(texts,x+7,y+117,w-14,False,gap=3)
            h=124+th
            self.box(x,y,w,h,paint)
            if imgs:self.picture(imgs[0],x+7,y+7,w-14,103,paint)
            self.stack(texts,x+7,y+117,w-14,paint,gap=3)
            return h
        if 'inventory' in cs:
            return self.grid(kids,x,y,w,3,paint)
        if 'item-slot' in cs:
            names=n.find('rname');counts=n.find('rcount')
            if len(names)!=1 or len(counts)!=1:raise ValueError('Unknown inventory item')
            label=content(names[0]);count=content(counts[0])
            th=self.text(label,x+8,y+77,w-16,18,paint=False)
            ch=self.text(count,x+8,y+83+th,w-16,17,paint=False)
            h=97+th+ch
            self.box(x,y,w,h,paint)
            if paint:self.icon(label,x+w/2-23,y+13)
            self.text(label,x+8,y+77,w-16,18,align='center',paint=paint)
            self.text(count,x+8,y+83+th,w-16,17,align='center',paint=paint)
            return h
        if 'cols' in cs:
            # Keep each section intact in two balanced columns; never split commands.
            col=(w-18)/2; groups=[[],[]];heights=[0,0]
            for child in kids:
                h=self.node(child,0,0,col,False)+16
                i=0 if heights[0]<=heights[1] else 1
                groups[i].append(child);heights[i]+=h
            for i,group in enumerate(groups):self.stack(group,x+i*(col+18),y,col,paint,16)
            return max(heights,default=0)
        if 'stats' in cs and n.find('vitals'):
            vitals=[c for c in kids if 'vitals' in classes(c)]
            rest=[c for c in kids if c not in vitals]
            h=self.stack(vitals,x,y,w,paint)+12
            return h+self.grid(rest,x,y+h,w,3,paint)
        if 'bag-summary' in cs:
            h=self.text(content(n),x+14,y+14,w-28,20,paint=False)+28
            self.box(x,y,w,h,paint)
            self.text('  ·  '.join(content(c) for c in kids),x+14,y+14,w-28,20,paint=paint)
            return h
        if cs & {'route','dashboard','lineage-grid','actions','stats','vitals','mount-layout'} and any(c.tag not in INLINE for c in kids):
            cols=4 if 'route' in cs else 3 if cs & {'actions','stats'} else 2
            return self.grid(kids,x,y,w,cols,paint)
        if 'cloud' in cs:
            cloud_width=min(620,w)
            x+=(w-cloud_width)/2
            w=cloud_width
            self.box(x,y,w,520,paint)
            for child in kids:
                if child.tag=='span':
                    style=child.attrs.get('style','')
                    values={key:float(val) for key,val in re.findall(r'(left|top|font-size):([\d.]+)px',style)}
                    color=re.search(r'color:(#[\da-fA-F]{6})',style)
                    scale=w/620
                    self.text(content(child),x+values['left']*scale,y+values['top'],w-values['left']*scale,
                              max(12,round(values['font-size']*scale)),color.group(1) if color else INK,paint=paint)
                else:self.node(child,x+12,y+190,w-24,paint)
            return 520
        if 'node' in cs:
            landmark=n.find('landmark')[0]; info=n.find('node-info')[0]
            h=self.stack(children(info),x+8,y+136,w-16,False,3)+150
            self.box(x,y,w,h,paint)
            pictures=[c for c in children(landmark) if c.tag=='img']
            if pictures:self.picture(pictures[0],x+7,y+7,w-14,118,paint,cover=True)
            numbers=n.find('number')
            if numbers:self.text(content(numbers[0]),x+12,y+10,w-24,17,'#ffffff',paint=paint)
            self.stack(children(info),x+8,y+136,w-16,paint,3)
            return h
        if cs & {'portrait','portrait-wrap'}:
            pictures=n.find('portrait') if 'portrait-wrap' in cs else [c for c in kids if c.tag=='img']
            pictures=[p for p in pictures if p.tag=='img']
            if self.theme=='ritual':
                diameter=min(340,w)
                px=x+(w-diameter)/2
                if paint:
                    self.draw.ellipse((px,y+16,px+diameter,y+16+diameter),fill='#ffffff',outline=GOLD,width=5)
                if pictures:self.picture(pictures[0],px+20,y+36,diameter-40,diameter-40,paint,circle=True)
                else:self.text(content(n) or '暂无宠物立绘',px+20,y+150,diameter-40,24,align='center',paint=paint)
                return diameter+44
            h=min(380,w*.85)
            self.box(x,y,w,h,paint)
            if pictures:self.picture(pictures[0],x+8,y+8,w-16,h-16,paint,circle=self.theme=='ritual')
            else:self.text(content(n) or '暂无立绘',x+12,y+h/2,w-24,20,paint=paint)
            return h
        if 'entry' in cs:
            numbers=n.find('number');words=n.find('words')
            h=self.node(words[0],x+50,y+10,w-60,False)+20
            if paint:self.draw.line((x,y,x+w,y),fill='#d6c4a0')
            if numbers:self.text(content(numbers[0]),x,y+10,40,23,'#a04430',paint=paint)
            self.node(words[0],x+50,y+10,w-60,paint)
            return h
        # Pair rows and inline labels retain spacing, values, percentages and hints.
        if 'gear-name' in cs:
            return self.text('  '.join(content(c) for c in kids),x,y,w,13,align='center',paint=paint)
        if 'row' in cs or 'byline' in cs:
            values=[content(c) for c in kids]
            if len(values)==2:
                left=min(w*.33,110);gap=10
                lh=self.text(values[0],x,y,w if not values[1] else left,16,'#647363',paint=paint)
                rh=self.text(values[1],x+left+gap,y,w-left-gap,17,align='right',paint=paint)
                return max(lh,rh)+8
        size=18;color=INK;align='left'
        if n.tag=='h1' or cs & {'mast-title','brand'}:size=38;color='#f5d999';align='center'
        elif n.tag=='h2' or cs & {'chapter-title','sect-h'}:size=23;color='#805f30'
        elif n.tag=='h3':size=20
        elif cs & {'caption','eyebrow','mast-caption','brand-sub','subtitle'}:size=16;color='#896c3e'
        elif cs & {'quote','stage'}:size=28;color='#a04430';align='center'
        elif cs & {'name','rank'}:size=28;color='#8d692e'
        elif cs & {'number'}:size=23;color='#a04430'
        elif n.tag=='small' or cs & {'sect-s','note','affix','signature'}:size=15;color='#657365'
        elif cs & {'text','result'}:size=22
        elif cs & {'status','recommendation'}:size=17;color='#95632e'
        if self.theme in {'national','cloud'} and n.tag=='h1':size=58 if self.theme=='national' else 48;color='#9c372b'
        if self.theme=='national' and cs & {'quote','signature','seal','subtitle','eyebrow','foot'}:
            align='center'
            if 'quote' in cs:size=38
            if 'signature' in cs:size=20
        if self.theme=='cloud' and cs & {'eyebrow','stats','foot','empty'}:align='center'
        if self.theme=='cultivator' and n.tag=='h1':color=INK;align='left'
        if self.theme=='map' and n.tag=='h1':color=INK;align='left'
        if self.theme=='mount' and cs & {'name','identity','rank'}:color='#f8e4b6'
        if 'mast-caption' in cs or 'brand-sub' in cs:color='#f6e9c5';align='center'
        if 'affix' in cs:size=12;color='#80653c'
        if self.theme=='ritual':
            color='#f5e8c6';align='center'
            if n.tag=='h1':size=62
            if 'caption' in cs:size=18
            if 'stage' in cs:size=30
            if 'result' in cs:size=24;align='left'
        # Pure inline nodes are painted as one coherent string; mixed containers
        # retain direct text as well as blocks instead of silently dropping it.
        pure=not kids or all(c.tag in INLINE for c in kids)
        boxed=bool(cs & {'panel','sect','info-card','attributes','abilities','wall-section','blessing','details','result','adv-sack','power-formula','intro','foot','power'}) or (self.theme=='map' and n.tag=='header') or (self.theme=='ritual' and 'stage' in cs)
        panel_fill='#08162d' if self.theme=='ritual' else PAPER
        pad=12 if boxed else 0
        if pure:
            value=content(n)
            if n.tag not in INLINE and kids and all(c.tag in INLINE for c in kids):
                # Label/value siblings need a visual separator; nested emphasis
                # within a command remains untouched by content().
                value='  '.join(content(c) if isinstance(c,Node) else c.strip() for c in n.children
                                if (isinstance(c,Node) and content(c)) or (isinstance(c,str) and c.strip()))
            if not value:return 0
            h=self.text(value,x+pad,y+pad,w-2*pad,size,color,align,False)+2*pad
            if boxed:self.box(x,y,w,h,paint,panel_fill)
            self.text(value,x+pad,y+pad,w-2*pad,size,color,align,paint)
            return h
        blocks=[]
        inline=[]
        def flush():
            if inline:
                node=Node('p');node.children.extend(inline);blocks.append(node);inline.clear()
        for child in n.children:
            if isinstance(child,str) or (isinstance(child,Node) and child.tag in INLINE):
                inline.append(child)
            elif isinstance(child,Node) and child.tag not in SKIP:
                flush();blocks.append(child)
        flush()
        h=self.stack(blocks,x+pad,y+pad,w-2*pad,False)+2*pad
        if boxed:self.box(x,y,w,h,paint,panel_fill)
        self.stack(blocks,x+pad,y+pad,w-2*pad,paint)
        return h

    def icon(self,name,x,y):
        d=self.draw;box=(x,y,x+46,y+46)
        d.rounded_rectangle(box,radius=9,fill='#e9ddb3',outline='#b49b61')
        if any(v in name for v in ('丹','药','酿','水')):
            d.rectangle((x+17,y+6,x+29,y+14),fill='#447b79')
            d.rounded_rectangle((x+11,y+14,x+35,y+39),radius=6,outline='#447b79',width=3)
            d.line((x+12,y+28,x+34,y+28),fill='#447b79',width=3)
        elif any(v in name for v in ('石','晶','碎片')):
            d.polygon([(x+23,y+5),(x+38,y+19),(x+30,y+39),(x+12,y+36),(x+7,y+18)],fill='#68879a',outline='#375d66')
            d.line((x+23,y+5,x+18,y+22,x+30,y+39),fill='#d0dfdb',width=2)
        elif any(v in name for v in ('卡','符','卷')):
            d.rectangle((x+12,y+6,x+35,y+40),fill='#fcf3d6',outline='#827055',width=2)
            for yy in (14,22,30):d.line((x+17,y+yy,x+30,y+yy),fill='#827055',width=2)
        else:
            d.rectangle((x+8,y+15,x+38,y+39),outline='#8a6742',width=3)
            d.line((x+23,y+15,x+23,y+39),fill='#8a6742',width=3)


def card_kind(html):
    # Exact structural signatures prevent accidental use for future layouts.
    signatures=(('pet','pet-layout'),('mount','mount-layout'),('bag','inventory'),
                ('menu','cols'),('cultivator','loadout'),('map','chapter chapter-'),
                ('ritual','result'),('cloud','cloud'),('national','blessing'),('national','wall-section'))
    for kind,signature in signatures:
        if re.search(r'class=[\"\'][^\"\']*\b'+re.escape(signature),html):
            return kind
    return None


def render_card(html,width,audit=None):
    kind=card_kind(html)
    if kind is None:return None
    if kind=='pet':
        # Preserve the existing pet implementation, including a local user edit
        # limiting it to 900px. Scale centrally for production's 760px canvas.
        picture=render_pet_card(html,900)
        if picture is None:return None
        return picture if width==900 else picture.resize((width,round(picture.height*width/900)),Image.Resampling.BILINEAR)
    parser=CardParser();parser.feed(html)
    cards=parser.root.find('card') or parser.root.find('scroll')
    if len(cards)!=1:return None
    root=cards[0]
    # Some HTML has no body (wordcloud); parser supports both shapes.
    native=900 if kind=='map' else 720 if kind in {'cultivator','ritual','national','cloud'} else width
    p=Painter(native,kind,audit)
    margin=30
    nodes=children(root)
    top=220 if kind=='menu' else 65 if kind=='cloud' else 0
    h=900 if kind=='cloud' else max(400,math.ceil(p.stack(nodes,margin,margin+top,native-2*margin,False)+2*margin+top))
    if kind=='ritual':h=max(1080,h)
    filename={'bag':'treasure-atelier.webp','menu':'mountain-gate.webp','mount':'celestial-clouds.webp',
              'national':'national-day.webp','cloud':'national-wordcloud.webp'}.get(kind)
    if kind=='ritual':
        caption=content(root.find('caption')[0])
        filename=('ascension' if '踏云' in caption else 'tribulation' if '九霄' in caption else 'evolution')+'-ritual.webp'
    if kind=='map':
        p.image=ImageOps.fit(embedded(root.find('world')[0].attrs['src']),(native,h),method=Image.Resampling.BILINEAR)
        veil=Image.new('RGB',(native,h),'#f5f1df');p.image=Image.blend(p.image,veil,.52)
    elif filename:
        if kind in {'national','menu'}:
            source=asset(filename)
            top_image=source.resize((native,round(source.height*native/source.width)),Image.Resampling.BILINEAR)
            p.image=Image.new('RGB',(native,h),'#f5efe2' if kind=='national' else '#214d54')
            p.image.paste(top_image,(0,0))
        else:p.image=asset(filename).resize((native,h),Image.Resampling.BILINEAR)
    else:p.image=Image.new('RGB',(native,h),'#efefe3')
    p.draw=ImageDraw.Draw(p.image)
    p.stack(nodes,margin,margin+top,native-2*margin,True)
    for inset in (2,10):p.draw.rectangle((inset,inset,native-inset-1,h-inset-1),outline=GOLD,width=2)
    if native!=width:
        # Content cards with fixed 720/900px CSS keep those dimensions; callers'
        # viewport widths are just padding in their old browser screenshots.
        return p.image
    return p.image
