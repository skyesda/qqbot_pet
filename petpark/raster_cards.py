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

from PIL import Image, ImageDraw, ImageOps, ImageFont, ImageColor

from .pet_card_pillow import CardParser, Node, font, render_pet_card

ASSETS = Path(__file__).parent / 'assets/ui'
FONTS = Path(__file__).parent / 'assets/fonts'
INK, GOLD, PAPER = '#244f53', '#dfc385', '#f7f2e3'
INLINE = {'span', 'b', 'strong', 'em', 'i', 'small', 'k', 'v', 'br'}
SKIP = {'script', 'style', 'meta', 'link'}


@lru_cache(maxsize=64)
def stationery_font(size):
    for path in (FONTS / 'PetparkSerif-Regular.otf', 'C:/Windows/Fonts/simkai.ttf', '/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc',
                 '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc'):
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return font(size)


@lru_cache(maxsize=32)
def body_font(size):
    path=FONTS / 'PetparkSans-Regular.otf'
    return ImageFont.truetype(path,size) if path.is_file() else font(size)


@lru_cache(maxsize=32)
def bold_font(size):
    path=FONTS / 'PetparkSans-Bold.otf'
    if path.is_file():return ImageFont.truetype(path,size)
    for path in ('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc','C:/Windows/Fonts/msyhbd.ttc'):
        if Path(path).is_file():return ImageFont.truetype(path,size)
    return body_font(size)


@lru_cache(maxsize=32)
def gradient(width, height, start, end):
    a,b=ImageColor.getrgb(start),ImageColor.getrgb(end)
    strip=Image.new('RGB',(1,height))
    strip.putdata([tuple(round(a[j]+(b[j]-a[j])*i/max(1,height-1)) for j in range(3)) for i in range(height)])
    return strip.resize((width,height))


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
        self.row_heights = {}
        self.font = stationery_font if theme in {'national','cloud'} else body_font

    def wrap(self, value, size, width, face=None):
        output, line, used = [], '', 0
        f = face or self.font(size)
        for char in str(value):
            if char == '\n':
                output.append(line); line, used = '', 0
                continue
            key = (id(f), size, char)
            cw = self.widths.get(key)
            if cw is None:
                cw = self.widths[key] = f.getlength(char)
            if line and used+cw > max(1, width):
                output.append(line); line, used = '', 0
            line += char; used += cw
        if line or not output:
            output.append(line)
        return output

    def text(self, value, x, y, width, size=18, color=INK, align='left', paint=False, bold=False, display=False):
        face=stationery_font(size) if display else bold_font(size) if bold else self.font(size)
        lines = self.wrap(value, size, width,face)
        step = math.ceil(size*1.45)
        if paint:
            if self.audit is not None:
                self.audit.append(str(value))
            for i, line in enumerate(lines):
                xx = x
                if align == 'center':
                    xx += max(0, (width-face.getlength(line))/2)
                elif align == 'right':
                    xx += max(0, width-face.getlength(line))
                self.draw.text((round(xx), round(y+i*step)), line, font=face, fill=color)
        return len(lines)*step

    def box(self, x, y, w, h, paint, fill=PAPER):
        if paint:
            # Subtle paper/gold gradients reproduce the original material panels.
            if fill==PAPER:
                self.image.paste(gradient(max(1,round(w)),max(1,round(h)),'#fffaf0','#eeefdf'),(round(x),round(y)))
            else:self.draw.rectangle((x,y,x+w,y+h),fill=fill)
            self.draw.rectangle((x,y,x+w,y+h),outline='#c8b787')
            if fill==PAPER and w>90 and h>100:
                self.draw.rectangle((x+5,y+5,x+w-5,y+h-5),outline='#e4dcc2')

    def fields(self,n,x,y,w,paint,mode='detail'):
        """Keep labels, primary values and explanatory text on distinct lines."""
        kids=children(n);label=next((c for c in kids if c.tag=='span'),None)
        primary=next((c for c in kids if c.tag in {'b','strong'}),None)
        notes=[c for c in kids if c not in (label,primary)]
        if primary is None:return None
        pad=14;yy=y+pad
        lh=self.text(content(label) if label else '',x+pad,yy,w-2*pad,12,'#987239',paint=paint)
        if mode=='stat':
            vh=self.text(content(primary),x+w*.4,yy-2,w*.6-pad,20,align='right',paint=paint,bold=True)
            h=max(lh,vh)+2*pad
        else:
            yy+=lh+5
            yy+=self.text(content(primary),x+pad,yy,w-2*pad,17,paint=paint,bold=True)+7
            for c in notes:yy+=self.text(content(c),x+pad,yy,w-2*pad,12,'#728073',paint=paint)+3
            h=yy-y+pad-3
        return max(56,h)

    def field_panel(self,n,x,y,w,paint,mode='detail'):
        h=self.fields(n,x,y,w,False,mode)
        if h is None:return None
        if paint:h=max(h,self.row_heights.get(id(n),0))
        self.box(x,y,w,h,paint)
        self.fields(n,x,y,w,paint,mode)
        return h

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
                if paint:self.row_heights[id(n)]=max(heights,default=0)
                self.node(n,x+i*(cw+gap),yy,cw,paint)
                self.row_heights.pop(id(n),None)
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
        if self.theme=='cultivator' and 'identity' in cs:
            power=next((c for c in kids if 'power' in classes(c)),None)
            detail=next((c for c in kids if c is not power),None)
            pw=150;gap=18
            dh=self.stack(children(detail),x,y,w-pw-gap,False,4) if detail else 0
            ph=self.node(power,x+w-pw,y,pw,False) if power else 0
            if detail:self.stack(children(detail),x,y,w-pw-gap,paint,4)
            if power:self.node(power,x+w-pw,y,pw,paint)
            return max(dh,ph)+12
        if 'power' in cs and self.theme in {'cultivator','mount'}:
            value=next((c for c in kids if c.tag=='strong'),None)
            label=''.join(content(c) if isinstance(c,Node) else c.strip() for c in n.children if c is not value)
            h=76 if self.theme=='cultivator' else 85
            if paint:
                self.image.paste(gradient(round(w),h,'#f9e7b3','#ddbf75'),(round(x),round(y)))
                self.draw.rectangle((x,y,x+w,y+h),outline='#b8954b')
            self.text(label,x+12,y+9,w-24,13,'#805f30',align='right',paint=paint)
            if value:self.text(content(value),x+12,y+29,w-24,30,'#90682e',align='right',paint=paint,bold=True)
            return h
        if self.theme=='cultivator' and 'info-card' in cs:
            h=self.field_panel(n,x,y,w,paint)
            if h is not None:return h
        if self.theme=='cultivator' and 'lineage-grid' in cs:
            cw=(w-12)/2
            hs=[self.fields(c,0,0,cw,False) for c in kids]
            h=max(v or 0 for v in hs)
            for i,c in enumerate(kids):
                self.box(x+i*(cw+12),y,cw,h,paint)
                self.fields(c,x+i*(cw+12),y,cw,paint)
            return h
        if self.theme=='cultivator' and 'power-formula' in cs:
            h=self.fields(n,x,y,w,False)
            if h is not None:
                self.box(x,y,w,h,paint, '#f0ead7')
                if paint:self.draw.rectangle((x,y,x+4,y+h),fill='#b79149')
                self.fields(n,x,y,w,paint)
                return h
        if self.theme=='cultivator' and 'actions' in cs:
            cw=(w-2)/3
            def action(c,xx,do):
                yy=y+16
                for child in children(c):
                    yy+=self.text(content(child),xx+10,yy,cw-20,14 if child.tag=='b' else 12,
                                  '#f1d48b' if child.tag=='b' else '#e7f0e7',align='center',paint=do,bold=child.tag=='b')+5
                return yy-y+11
            h=max(action(c,x,False) for c in kids)
            for i,c in enumerate(kids):
                xx=x+i*(cw+1);self.box(xx,y,cw,h,paint,'#214e50');action(c,xx,paint)
            return h
        if self.theme=='cultivator' and n.tag=='div' and len(kids)>=2 and kids[0].tag=='span' and kids[1].tag=='b' and not n.find('barwrap') and 'gear-name' not in cs:
            h=self.field_panel(n,x,y,w,paint,'stat')
            if h is not None:return h
        if 'masthead' in cs and self.theme=='national':
            return 288+self.stack(kids,x,y+288,w,paint)
        if 'masthead' in cs:
            h=self.stack(kids,x,y+18,w,paint)
            if paint:
                self.draw.line((x+8,y+50,x+78,y+50),fill=GOLD)
                self.draw.line((x+w-78,y+50,x+w-8,y+50),fill=GOLD)
            return max(150,h+32) if self.theme=='bag' else h+36
        if 'chapter-title' in cs:
            return self.text(' · '.join(content(c) for c in kids),x,y,w,23,'#284c48',paint=paint)
        if n.find('barwrap') and all(c.tag in INLINE for c in kids):
            label=next((c for c in kids if c.tag=='span' and 'barwrap' not in classes(c)),None)
            value=next((c for c in kids if c.tag=='b'),None)
            pad=12
            vh=self.text(content(value) if value else '',x+62,y+12,w-74,14,paint=False)
            hints=[c for c in kids if c.tag=='small']
            h=12+max(20,vh)+18+sum(self.text(content(c),x+pad,y,w-2*pad,12,paint=False) for c in hints)+12
            if paint:h=max(h,self.row_heights.get(id(n),0))
            self.box(x,y,w,h,paint)
            if label:self.text(content(label),x+pad,y+12,45,14,paint=paint)
            if value:self.text(content(value),x+62,y+12,w-74,14,align='right',paint=paint,bold=True)
            yy=y+12+max(20,vh)
            for bar in n.find('barwrap'):yy+=self.node(bar,x+pad,yy,w-2*pad,paint)
            for hint in hints:yy+=self.text(content(hint),x+pad,yy,w-2*pad,12,'#728073',paint=paint)
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
                color=re.search(r'background:(#[\da-fA-F]{6})',fill.attrs.get('style',''))
                tint=color.group(1) if color else '#4e7f9b' if 'stamina-bar' in classes(fill) else '#578d55'
                self.draw.rectangle((x,y+3,x+w*max(0,min(100,pct))/100,y+13),fill=tint)
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
        if self.theme=='mount' and 'mount-heading' in cs and len(kids)==2:
            rw=min(150,w*.28);gap=18
            left=self.stack(children(kids[0]),x,y,w-rw-gap,False,4)
            right=self.node(kids[1],x+w-rw,y,rw,False)
            self.stack(children(kids[0]),x,y,w-rw-gap,paint,4)
            self.node(kids[1],x+w-rw,y,rw,paint)
            return max(left,right)+12
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
            th=self.text(label,x+12,y+91,w-24,18,paint=False)
            ch=self.text(count,x+12,y+104+th,w-24,18,paint=False)
            h=max(175,120+th+ch)
            self.box(x,y,w,h,paint)
            if paint:self.icon(label,x+w/2-31,y+18)
            self.text(label,x+12,y+91,w-24,18,align='center',paint=paint,bold=True)
            badge_width=min(w-24,max(62,self.font(18).getlength(count)+28))
            bx=x+(w-badge_width)/2;by=y+103+th
            self.box(bx,by,badge_width,ch+8,paint,'#28585b')
            self.text(count,bx+10,by+3,badge_width-20,18,'#fff2cb',align='center',paint=paint)
            return h
        if 'cols' in cs:
            # Keep each section intact in two balanced columns; never split commands.
            pad=22;col=(w-2*pad-32)/2; groups=[[],[]];heights=[0,0]
            for child in kids:
                h=self.node(child,0,0,col,False)+16
                i=0 if heights[0]<=heights[1] else 1
                groups[i].append(child);heights[i]+=h
            h=max(heights,default=0)+2*pad
            self.box(x,y,w,h,paint)
            if paint:self.draw.line((x+w/2,y+pad,x+w/2,y+h-pad),fill='#ccba90')
            for i,group in enumerate(groups):self.stack(group,x+pad+i*(col+32),y+pad,col,paint,16)
            return h
        if 'stats' in cs and n.find('vitals'):
            vitals=[c for c in kids if 'vitals' in classes(c)]
            rest=[c for c in kids if c not in vitals]
            h=self.stack(vitals,x,y,w,paint)+12
            return h+self.grid(rest,x,y+h,w,3,paint)
        if 'bag-summary' in cs:
            h=68
            self.box(x,y,w,h,paint,'#faedc6')
            for i,c in enumerate(kids):
                values=children(c);value=content(values[0]) if values else ''
                label=''.join(v for v in c.children if isinstance(v,str)).strip()
                xx=x+20+i*w/2
                self.text(label,xx,y+24,w/2-40,17,paint=paint)
                self.text(value,xx+86,y+12,w/2-126,30,paint=paint,bold=True)
            return h+12
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
        if self.theme=='national' and 'details' in cs:
            pad=18;cw=(w-3*pad)/2
            def detail(c,xx,do):
                yy=y+pad
                for part in c.children:
                    if isinstance(part,Node) and part.tag=='br':yy+=5;continue
                    value=content(part) if isinstance(part,Node) else part.strip()
                    if not value:continue
                    strong=isinstance(part,Node) and part.tag=='strong'
                    yy+=self.text(value,xx,yy,cw,24 if strong else 17,'#a04430' if strong else '#655c46',paint=do)+3
                return yy-y+pad
            h=max(detail(c,x,False) for c in kids)
            self.box(x,y,w,h,paint)
            for i,c in enumerate(kids):detail(c,x+pad+i*(cw+pad),paint)
            return h
        if 'entry' in cs:
            numbers=n.find('number');words=n.find('words')
            h=self.node(words[0],x+50,y+10,w-60,False)+20
            if paint:self.draw.line((x,y,x+w,y),fill='#d6c4a0')
            if numbers:self.text(content(numbers[0]),x,y+10,40,23,'#a04430',paint=paint)
            self.node(words[0],x+50,y+10,w-60,paint)
            return h
        if self.theme=='menu' and 'sect' in cs:
            return self.stack(kids,x,y,w,paint,9)+14
        if self.theme=='menu' and 'sect-h' in cs:
            nums=[c for c in kids if 'sect-no' in classes(c)]
            title=''.join(c for c in n.children if isinstance(c,str)).strip()
            if nums:self.text(content(nums[0]),x,y+5,23,12,'#9b7c42',paint=paint)
            h=self.text(title,x+28,y,w-28,21,'#304c48',paint=paint,bold=True)+10
            if paint:self.draw.line((x,y+h-4,x+w,y+h-4),fill='#c8b78e')
            return h
        # Pair rows and inline labels retain spacing, values, percentages and hints.
        if 'gear-name' in cs:
            if len(kids)==2:
                value=content(kids[1]);vw=self.font(12).getlength(value)+6
                a=self.text(content(kids[0]),x,y,w-vw-4,12,paint=paint)
                b=self.text(value,x+w-vw,y,vw,12,'#946d2f',align='right',paint=paint,bold=True)
                return max(a,b)
            return self.text(content(n),x,y,w,13,align='center',paint=paint)
        if 'row' in cs or 'byline' in cs:
            values=[content(c) for c in kids]
            if len(values)==2:
                left=min(w*.33,110);gap=10
                lh=self.text(values[0],x,y,w if not values[1] else left,16,'#647363',paint=paint)
                rh=self.text(values[1],x+left+gap,y,w-left-gap,17,align='right',paint=paint)
                return max(lh,rh)+8
        size=18;color=INK;align='left'
        if n.tag=='h1' or cs & {'mast-title','brand'}:size=46;color='#f5d999';align='center'
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
        if self.theme=='cultivator' and n.tag=='h1':size=32
        if self.theme=='cultivator' and 'eyebrow' in cs:size=13
        if self.theme=='cultivator' and n.tag=='p':size=15
        if self.theme=='menu' and 'brand' in cs:size=60
        if self.theme=='menu' and 'item' in cs:size=16;color='#ae4539'
        if self.theme=='menu' and cs & {'note','sect-s','plain'}:size=14;color='#6b7569'
        if self.theme=='map' and n.tag=='h1':color=INK;align='left'
        if self.theme=='mount' and cs & {'name','identity','rank'}:color='#f8e4b6'
        if self.theme=='mount' and 'rank' in cs:color='#284c48';size=18
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
        boxed=bool(cs & {'panel','sect','info-card','attributes','abilities','wall-section','blessing','details','result','adv-sack','power-formula','intro','foot','power','rank'}) or (self.theme=='map' and n.tag=='header') or (self.theme=='ritual' and 'stage' in cs)
        panel_fill='#08162d' if self.theme=='ritual' else PAPER
        if self.theme=='cultivator' and 'foot' in cs:boxed=False
        if self.theme in {'menu','bag','mount'} and cs & {'intro','foot'}:panel_fill='#163f43';color='#f8ebc9';align='center';size=14
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
            self.text(value,x+pad,y+pad,w-2*pad,size,color,align,paint,
                      bold=size>=30 and self.theme not in {'national','cloud'},display=bool(cs & {'mast-title','brand'}) or n.tag=='h1' and self.theme in {'national','cloud'})
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
        d=self.draw
        column=min(2,int(x/(self.width/3)))
        ink=['#346961','#71597b','#9e7138'][column]
        fills=[('#fff8d5','#ddcd93'),('#fff0e6','#ded0dc'),('#fff5c6','#e0cd91')][column]
        tile=gradient(62,62,*fills);mask=Image.new('L',(62,62))
        ImageDraw.Draw(mask).rounded_rectangle((0,0,61,61),radius=14,fill=255)
        self.image.paste(tile,(round(x),round(y)),mask)
        d.rounded_rectangle((x,y,x+61,y+61),radius=14,outline='#bca468')
        ox,oy=x+10,y+9
        def line(points):d.line([(ox+px,oy+py) for px,py in points],fill=ink,width=2)
        if any(v in name for v in ('丹','药','酿','水')):
            line([(18,5),(30,5),(30,13),(37,23),(37,36),(30,40),(18,40),(11,36),(11,23),(18,13),(18,5)])
            line([(18,10),(30,10)]);line([(12,29),(36,29)]);line([(20,33),(28,33)])
        elif any(v in name for v in ('石','晶','碎片')):
            line([(24,4),(39,18),(33,40),(15,43),(8,21),(24,4)])
            line([(24,4),(19,23),(33,40)]);line([(8,21),(19,23),(39,18)]);line([(19,23),(15,43)])
        elif any(v in name for v in ('卡','符','卷')):
            d.rounded_rectangle((ox+12,oy+5,ox+38,oy+40),radius=3,outline=ink,width=2)
            line([(8,12),(8,43),(33,43)]);line([(18,13),(32,13)]);line([(18,31),(32,31)])
            line([(25,18),(30,24),(25,29),(20,24),(25,18)])
        else:
            d.rectangle((ox+7,oy+17,ox+41,oy+40),outline=ink,width=2)
            d.rectangle((ox+5,oy+10,ox+43,oy+18),outline=ink,width=2)
            line([(21,10),(21,40)]);line([(28,10),(28,40)])
            d.ellipse((ox+12,oy+2,ox+24,oy+11),outline=ink,width=2)
            d.ellipse((ox+24,oy+2,ox+36,oy+11),outline=ink,width=2)


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
    margin=28 if kind=='cultivator' else 38 if kind in {'menu','bag','mount'} else 30
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
    else:p.image=gradient(native,h,'#fcf8ed','#e6ece2').copy()
    p.draw=ImageDraw.Draw(p.image)
    p.stack(nodes,margin,margin+top,native-2*margin,True)
    for inset in (2,10):p.draw.rectangle((inset,inset,native-inset-1,h-inset-1),outline=GOLD,width=2)
    if native!=width:
        # Content cards with fixed 720/900px CSS keep those dimensions; callers'
        # viewport widths are just padding in their old browser screenshots.
        return p.image
    return p.image
