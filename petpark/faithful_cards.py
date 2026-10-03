"""Dedicated Pillow compositions matching the authored CSS card geometry.

This module intentionally keeps art and text sizes independent of the viewport:
rendering a 760px pet card must not shrink a 900px layout into tiny type.
"""
from __future__ import annotations

import math
import re
import types

from PIL import Image, ImageDraw, ImageOps, ImageEnhance, ImageFilter

from .raster_cards import (Painter, CardParser, children, classes, content, asset,
                           embedded, gradient, stationery_font, body_font, GOLD, INK)


def one(root, name):
    nodes=root.find(name)
    if len(nodes)!=1:raise ValueError('Unsupported card field: '+name)
    return nodes[0]


class Composition(Painter):
    def start(self,height,background=None):
        self.image=(asset(background).resize((self.width,height),Image.Resampling.BILINEAR)
                    if background else gradient(self.width,height,'#fcf8ed','#e6ece2').copy())
        self.draw=ImageDraw.Draw(self.image)

    def paper(self,x,y,w,h,paint=True):
        if not paint:return
        self.image.paste(gradient(round(w),math.ceil(h),'#fffbec','#ede9d7'),(round(x),round(y)))
        self.draw.rounded_rectangle((x,y,x+w,y+h),radius=4,outline='#d8bd7e')
        self.draw.rounded_rectangle((x+3,y+3,x+w-3,y+h-3),radius=3,outline='#fffdf1')

    def title(self,value,x,y,w,size=38,color='#ffe8aa',spacing=7,paint=True,align='center'):
        face=stationery_font(size)
        # Long titles wrap before drawing; add authored tracking to measurement.
        lines=self.wrap(value,size,max(1,w-spacing*len(value)),face)
        if paint:
            if self.audit is not None:self.audit.append(value)
            for j,line in enumerate(lines):
                length=face.getlength(line)+spacing*max(0,len(line)-1)
                xx=x+(w-length)/2 if align=='center' else x
                for char in line:
                    self.draw.text((round(xx),round(y+j*size*1.35+2)),char,font=face,fill='#1a3537',stroke_width=1)
                    self.draw.text((round(xx),round(y+j*size*1.35)),char,font=face,fill=color,stroke_width=1,stroke_fill=color)
                    xx+=face.getlength(char)+spacing
        return math.ceil(len(lines)*size*1.35)

    def frame(self):
        w,h=self.image.size
        self.draw.rectangle((1,1,w-2,h-2),outline='#b99552',width=3)
        self.draw.rectangle((6,6,w-7,h-7),outline='#dbbd7b',width=2)
        self.draw.rectangle((11,11,w-12,h-12),outline='#e8d19b')

    def mast(self,root,y=34):
        self.title(content(one(root,'mast-title')),90,y,self.width-180)
        self.text(content(one(root,'mast-caption')),90,y+48,self.width-180,13,'#dfd6b7','center',True)
        for x in (30,self.width-86):
            for yy in (y+20,y+32):self.draw.line((x,yy,x+54,yy),fill='#e0bf76')

    def rows(self,node,x,y,w,paint=True,size=18,minheight=36):
        yy=y
        for index,row in enumerate(node.find('row')):
            values=children(row)
            if len(values)!=2:raise ValueError('Unsupported attribute row')
            label,value=map(content,values)
            vh=self.text(value,x+92,yy+5,w-100,size,paint=False,bold=True)
            lh=self.text(label,x+5,yy+5,82,size,paint=False)
            rh=max(minheight,max(vh,lh)+10)
            if paint:
                if index%2==1:self.draw.rectangle((x,yy,x+w,yy+rh),fill='#e5e7d6')
                self.text(label,x+5,yy+5,82,size,'#637365',paint=True)
                self.text(value,x+92,yy+5,w-100,size,align='right',paint=True,bold=True)
                self.draw.line((x,yy+rh,x+w,yy+rh),fill='#d5d2ba')
            yy+=rh
        return yy-y

    def power(self,node,x,y,w,height=58):
        fields=children(node)
        if len(fields)!=2:raise ValueError('Unsupported power panel')
        self.image.paste(gradient(round(w),height,'#e7cc88','#fff0b8'),(round(x),round(y)))
        self.draw.rectangle((x,y,x+w,y+height),outline='#d1ac63')
        self.text(content(fields[0]),x+17,y+17,110,17,paint=True)
        self.text(content(fields[1]),x+127,y+8,w-144,32,align='right',paint=True,bold=True)

    def footer(self,node,y):
        fields=children(node);w=self.width-64
        lh=self.text(content(fields[0]),46,y+14,w/2-24,15,paint=False)
        rh=self.text(content(fields[-1]),self.width/2,y+14,w/2-14,15,paint=False)
        h=max(lh,rh)+28
        self.draw.rectangle((32,y,self.width-32,y+h),fill='#10383e')
        self.text(content(fields[0]),46,y+14,w/2-24,15,'#f8ebc9',paint=True)
        self.text(content(fields[-1]),self.width/2,y+14,w/2-14,15,'#f8ebc9',align='right',paint=True)
        return h


def pet_card(root,width,audit):
    p=Composition(width,'pet',audit);left=32;col=(width-64-18)/2;right=left+col+18
    names=one(root,'name');identity=one(root,'identity');rank=one(root,'rank')
    nh=p.text(content(names),left,0,width-220,34,paint=False,bold=True)
    ih=p.text(content(identity),left,0,width-64,16,paint=False)
    y0=126+nh+ih+14
    attributes=one(root,'attributes');abilities=one(root,'abilities');resource=one(root,'resource')
    ah=p.rows(attributes,0,0,col-30,False)+20
    bh=p.rows(abilities,0,0,col-34,False,17,40)+18
    reshead=children(resource)[0];resvalues=children(reshead)
    if len(resvalues)!=2:raise ValueError('Unsupported resource header')
    rv=p.text(content(resvalues[1]),0,0,col-100,16,paint=False)
    resource_h=max(66,rv+42)+p.rows(resource,0,0,col-30,False,14)
    bars=one(root,'vitals').find('bar-row')
    bar_heights=[max(34,p.text(content(one(b,'bar-n')),0,0,128,14,paint=False)+10) for b in bars]
    vital_h=sum(bar_heights)+24
    left_h=368+14+resource_h+14+vital_h
    stats=one(root,'stats').find('stat');cw=col/3
    stat_h=max(74,max(p.text(content(children(n)[1]),0,0,cw-8,22,paint=False,bold=True)+40 for n in stats))
    right_h=58+12+ah+12+stat_h+16+bh
    bottom=y0+max(left_h,right_h)
    tags=[content(c) for n in root.find('tags') for c in children(n)]
    tag_rows=1 if tags else 0;used=0
    for tag in tags:
        tw=body_font(15).getlength(tag)+32
        if used and used+tw>width-64:tag_rows+=1;used=0
        used+=tw
    tag_h=tag_rows*34
    warnings=root.find('warn');warn_h=sum(p.text(content(n),0,0,width-96,17,paint=False)+24 for n in warnings)
    foot=one(root,'foot');foot_h=max(p.text(content(c),0,0,(width-64)/2-24,15,paint=False) for c in children(foot))+28
    h=math.ceil(bottom+tag_h+warn_h+foot_h+64)
    p.start(h,'celestial-clouds.webp');p.mast(root)
    p.text(content(names),left,122,width-220,34,'#fff4d5',paint=True,bold=True)
    p.text(content(identity),left,122+nh,width-64,16,'#e8e3cd',paint=True)
    rtext=content(rank);rw=max(80,body_font(20).getlength(rtext)+36)
    p.image.paste(gradient(round(rw),50,'#f2d797','#b89555'),(round(width-32-rw),130))
    p.draw.rectangle((width-32-rw,130,width-32,180),outline='#e3c98f')
    p.text(rtext,width-32-rw+12,141,rw-24,20,align='center',paint=True,bold=True)
    # Real 310px portrait height at 760px output, as in the original CSS.
    p.image.paste(gradient(round(col),368,'#f9e2a6','#907044'),(left,round(y0)))
    p.draw.rectangle((left+7,y0+7,left+col-7,y0+317),fill='#fffcf0',outline='#887749')
    portraits=root.find('portrait')
    if portraits:p.picture(portraits[0],left+8,y0+8,col-16,308,True)
    else:p.text('暂无立绘',left+15,y0+145,col-30,20,align='center',paint=True)
    p.draw.rectangle((left+7,y0+318,left+col-7,y0+361),fill='#19484f')
    p.text(content(one(root,'portrait-label')),left+15,y0+329,col-30,16,'#f8e2af','center',True)
    yy=y0+382;p.paper(left,yy,col,resource_h)
    p.text(content(resvalues[0]),left+15,yy+12,80,16,'#8b6631',paint=True,bold=True)
    p.text(content(resvalues[1]),left+95,yy+12,col-110,16,align='right',paint=True)
    fill=one(resource,'fill');pct=float(re.search(r'width:([\d.]+)%',fill.attrs['style']).group(1))
    def bar(x,y,w,pct,a,b):
        p.draw.rectangle((x,y,x+w,y+10),fill='#d8ddce',outline='#b6c3b5')
        ww=round(w*max(0,min(100,pct))/100)
        if ww:p.image.paste(gradient(ww,8,a,b),(round(x)+1,round(y)+1))
    bar(left+15,yy+resource_h-22,col-30,pct,'#ad8844','#dcb86c')
    p.rows(resource,left+15,yy+12+rv,col-30,True,14)
    yy+=resource_h+14;p.paper(left,yy,col,vital_h);yy+=12
    for b,rh in zip(bars,bar_heights):
        p.text(content(one(b,'bar-k')),left+15,yy+4,38,16,paint=True)
        fill=one(b,'fill');pct=float(re.search(r'width:([\d.]+)%',fill.attrs['style']).group(1))
        colors=('#9c433e','#da8070') if 'hp' in classes(fill) else ('#327681','#79b6b6') if 'en' in classes(fill) else ('#ad8844','#dcb86c')
        number=content(one(b,'bar-n'));nw=min(128,max(62,body_font(14).getlength(number)+6))
        bar(left+60,yy+10,col-82-nw,pct,*colors)
        p.text(number,left+col-15-nw,yy+4,nw,14,align='right',paint=True);yy+=rh
    p.power(one(root,'power'),right,y0,col)
    yy=y0+70;p.paper(right,yy,col,ah);p.rows(attributes,right+15,yy+10,col-30);yy+=ah+12
    for i,n in enumerate(stats):
        fields=children(n);xx=right+i*cw;p.paper(xx,yy,cw,stat_h)
        p.text(content(fields[0]),xx+4,yy+11,cw-8,15,'#657363','center',True)
        p.text(content(fields[1]),xx+4,yy+34,cw-8,22,align='center',paint=True,bold=True)
    yy+=stat_h+16;p.paper(right,yy,col,bh);p.rows(abilities,right+17,yy+9,col-34,True,17,40)
    yy=bottom+14;xx=left
    for tag in tags:
        tw=body_font(15).getlength(tag)+24
        if xx+tw>width-32:xx=left;yy+=34
        p.draw.rectangle((xx,yy,xx+tw,yy+30),fill='#174950',outline='#c6ad71')
        p.text(tag,xx+12,yy+4,tw-24,15,'#f3e6c5',paint=True);xx+=tw+8
    yy=bottom+tag_h+18
    for n in warnings:
        hh=p.text(content(n),left+16,yy+12,width-96,17,paint=False)+24
        p.paper(left,yy,width-64,hh);p.text(content(n),left+16,yy+12,width-96,17,'#8a342f',paint=True);yy+=hh
    p.footer(foot,h-foot_h-28);p.frame()
    return p.image


def atlas(root,audit):
    p=Composition(900,'map',audit);x=35;w=830
    header=next(c for c in children(root) if c.tag=='header')
    chapters=root.find('chapter');chapter_heights=[]
    for chapter in chapters:
        hs=[]
        for node in chapter.find('node'):
            info=one(node,'node-info');parts=children(info)
            hs.append(160+p.text(content(parts[0]),0,0,180,19,paint=False,bold=True)+
                      p.text(content(parts[1]),0,0,180,12,paint=False)+p.text(content(parts[2]),0,0,180,12,paint=False)+25)
        chapter_heights.append(max(hs)+46)
    h=math.ceil(254+sum(chapter_heights)+182)
    p.image=ImageOps.fit(embedded(one(root,'world').attrs['src']),(900,h),method=Image.Resampling.BILINEAR)
    # Original translucent top/bottom veil, leaving the atlas visible in the middle.
    stops=[(0,.93),(.16,.53),(.4,.09),(.78,.13),(.95,.93),(1,.93)]
    overlay=Image.new('RGBA',(1,h))
    colors=[]
    for yy in range(h):
        t=yy/h
        for (t0,a0),(t1,a1) in zip(stops,stops[1:]):
            if t0<=t<=t1:alpha=a0+(a1-a0)*(t-t0)/(t1-t0);break
        colors.append((247,242,223,round(alpha*255)))
    overlay.putdata(colors);p.image=Image.alpha_composite(p.image.convert('RGBA'),overlay.resize((900,h))).convert('RGB');p.draw=ImageDraw.Draw(p.image)
    p.text(content(one(header,'eyebrow')),x,36,w,14,'#7e673c',paint=True)
    heading=one(header,'heading');detail=children(heading)[0]
    p.title(content(children(detail)[0]),x,61,570,48,'#1d4b47',8,align='left')
    p.text(content(children(detail)[1]),x,133,600,14,'#627867',paint=True)
    progress_node=one(header,'progress');progress=children(progress_node)
    saved=p.audit;p.audit=None
    p.text(content(progress[0]),695,59,80,54,'#856632',paint=True)
    p.text(content(progress[1]),779,82,85,14,'#856632',paint=True)
    p.audit=saved
    if saved is not None:saved.append(content(progress_node))
    meta=content(one(header,'meta'));mh=p.text(meta,x,164,w,14,paint=True)
    rec=content(one(header,'recommendation'));rh=p.text(rec,x+16,190,w-32,16,paint=False)+26
    p.draw.rectangle((x,190,x+w,190+rh),fill='#234c48',outline='#bba46b')
    p.text(rec,x+16,203,w-32,16,'#fff2c7',paint=True)
    yy=max(254,190+rh+24)
    for chapter,ch in zip(chapters,chapter_heights):
        title=children(one(chapter,'chapter-title'))
        p.paper(x,yy,58,26);p.text(content(title[0]),x+5,yy+3,48,12,paint=True)
        p.text(content(title[1]),x+73,yy-2,530,23,paint=True,bold=True)
        p.text(content(title[2]),x+w-60,yy+4,60,14,align='right',paint=True)
        p.draw.line((x+360,yy+16,x+w-76,yy+16),fill='#9c986c')
        nodes=chapter.find('node');positions=list(range(4))
        if one(chapter,'route').attrs.get('class','').find('reverse')>=0:positions.reverse()
        cy=yy+46;p.draw.line((x+94,cy+63,x+w-94,cy+63),fill='#af9154',width=3)
        for n,index in zip(nodes,positions):
            nx=x+index*(188+26);px=nx+31;cs=classes(n)
            border='#4f8d78' if 'cleared' in cs else '#d9ad45' if 'ready' in cs else '#bfa66b'
            if 'ready' in cs:p.draw.ellipse((px-5,cy-5,px+131,cy+131),fill='#ead7a4')
            p.draw.ellipse((px,cy,px+126,cy+126),fill='#f5eedb',outline=border,width=2)
            im=embedded(children(one(n,'landmark'))[0].attrs['src']);im=ImageOps.fit(im,(114,114),method=Image.Resampling.BILINEAR)
            if 'locked' in cs:im=ImageEnhance.Color(im).enhance(.32);im=Image.blend(im,Image.new('RGB',im.size,'#f5eedb'),.28)
            mask=Image.new('L',(114,114));ImageDraw.Draw(mask).ellipse((0,0,113,113),fill=255);p.image.paste(im,(round(px+6),round(cy+6)),mask)
            p.draw.rounded_rectangle((px+44,cy+112,px+82,cy+140),radius=3,fill='#a5762e' if 'ready' in cs else '#284f4a',outline='#dbbd79')
            p.text(content(one(n,'number')),px+46,cy+114,34,17,'#fff4d7','center',True,bold=True)
            parts=children(one(n,'node-info'));iy=cy+147
            ih=sum(p.text(content(c),0,0,180,19 if c.tag=='h3' else 12,paint=False,bold=c.tag=='h3') for c in parts)+20
            p.paper(nx,iy,188,ih);ty=iy+8
            for c in parts:
                color='#77827e' if 'status' in classes(c) and 'locked' in cs else '#9c621e' if 'status' in classes(c) and 'ready' in cs else '#38675a' if 'status' in classes(c) else INK
                ty+=p.text(content(c),nx+4,ty,180,19 if c.tag=='h3' else 12,color,'center',True,bold=c.tag=='h3')
        yy+=ch
    footer=next(c for c in children(root) if c.tag=='footer')
    p.draw.line((x,yy,x+w,yy),fill='#a38f5b');yy+=18
    legend=one(footer,'legend');xx=205
    for c in children(legend):
        p.paper(xx,yy,108,27);p.text(content(c),xx+4,yy+3,100,14,'#627867','center',True);xx+=128
    yy+=40
    for c in children(footer):
        if c is legend:continue
        yy+=p.text(content(c),x+16,yy,w-32,12 if 'daily' in classes(c) else 14,'#627867','center',True)+5
    p.frame();return p.image


def mount_card(root,width,audit):
    p=Composition(width,'mount',audit);left=32;col=(width-64-18)/2;right=left+col+18
    name=one(root,'name');identity=one(root,'identity')
    nh=p.text(content(name),left,0,width-230,36,paint=False,bold=True)
    ih=p.text(content(identity),left,0,width-64,16,paint=False)
    y0=126+nh+ih+18
    rows=one(root,'mount-rows');rh=p.rows(rows,0,0,col-30,False)+20
    h=math.ceil(y0+max(264,58+12+rh)+96)
    p.start(h,'celestial-clouds.webp');p.mast(root)
    p.text(content(name),left,122,width-230,36,'#fff4d5',paint=True,bold=True)
    p.text(content(identity),left,122+nh,width-64,16,'#e8e3cd',paint=True)
    rank=one(root,'rank');rtext=content(rank)
    if rtext:
        rw=min(180,max(95,body_font(18).getlength(rtext)+36));p.image.paste(gradient(round(rw),50,'#f2d797','#b89555'),(round(width-32-rw),130))
        p.text(rtext,width-32-rw+12,141,rw-24,18,align='center',paint=True,bold=True)
    p.image.paste(gradient(round(col),264,'#f9e2a6','#907044'),(left,round(y0)))
    p.draw.rectangle((left+7,y0+7,left+col-7,y0+257),fill='#fffcf0',outline='#887749')
    portraits=root.find('portrait') or root.find('ph-img')
    if portraits:p.picture(portraits[0],left+8,y0+8,col-16,248,True)
    else:
        placeholders=root.find('portrait-ph')
        if placeholders:p.text(content(placeholders[0]),left+18,y0+100,col-36,22,align='center',paint=True)
    p.power(one(root,'power'),right,y0,col)
    p.paper(right,y0+70,col,rh);p.rows(rows,right+15,y0+80,col-30)
    p.footer(one(root,'foot'),h-78);p.frame();return p.image


def ritual_card(root,audit):
    p=Composition(720,'ritual',audit);nodes=children(root)
    title=next(c for c in nodes if c.tag=='h1');caption=one(root,'caption')
    text=content(caption)
    theme='ascension' if '踏云' in text else 'tribulation' if '九霄' in text else 'evolution'
    accent={'evolution':'#a9ffe4','ascension':'#ffe7a5','tribulation':'#dcc4ff'}[theme]
    name=one(root,'name');stage=one(root,'stage');result=one(root,'result');footer=one(root,'footer')
    nh=p.text(content(name),38,0,644,27,paint=False)
    sh=p.text(content(stage),50,0,620,30,paint=False,bold=True)+36
    rh=p.text(content(result),64,0,592,24,paint=False)+48
    portrait_y=42+26+10+84+12+nh+38
    stage_y=portrait_y+340+28
    result_y=stage_y+sh+24
    h=math.ceil(max(1080,result_y+rh+22+32+30))
    p.start(h,theme+'-ritual.webp')
    # Reproduce CSS object-fit: cover instead of stretching the background.
    p.image=ImageOps.fit(asset(theme+'-ritual.webp'),(720,h),method=Image.Resampling.BILINEAR)
    shade=Image.new('RGBA',(720,h))
    strip=Image.new('RGBA',(1,h));strip.putdata([(4,9,26,round(255*(.35*(1-y/(h*.38)) if y<h*.38 else .25*(y-h*.38)/(h*.62)))) for y in range(h)])
    shade=strip.resize((720,h));p.image=Image.alpha_composite(p.image.convert('RGBA'),shade).convert('RGB');p.draw=ImageDraw.Draw(p.image)
    p.text(text,38,42,644,18,accent,'center',True)
    p.text(content(title),38,80,644,62,'#ffffff','center',True,bold=True)
    p.text(content(name),38,176,644,27,'#ffffff','center',True)
    # A single circular matte behind a contained portrait. Do not cut an oval
    # out of a non-square contained image; the original art keeps its silhouette.
    px=190;mask=Image.new('L',(720,h));md=ImageDraw.Draw(mask)
    md.ellipse((px-7,portrait_y-7,px+347,portrait_y+347),fill=115)
    mask=mask.filter(ImageFilter.GaussianBlur(17))
    glow=Image.new('RGB',(720,h),accent);p.image=Image.composite(glow,p.image,mask);p.draw=ImageDraw.Draw(p.image)
    p.draw.ellipse((px,portrait_y,px+340,portrait_y+340),fill='#ffffff',outline=accent,width=5)
    portraits=children(one(root,'portrait'));pictures=[n for n in portraits if n.tag=='img']
    if pictures:
        im=ImageOps.contain(embedded(pictures[0].attrs['src']),(294,294),Image.Resampling.BILINEAR)
        # The CSS image box is square: mask its square canvas, not the image.
        matte=Image.new('RGB',(294,294),'#ffffff');matte.paste(im,((294-im.width)//2,(294-im.height)//2))
        circle=Image.new('L',(294,294));ImageDraw.Draw(circle).ellipse((0,0,293,293),fill=255)
        p.image.paste(matte,(px+23,round(portrait_y+23)),circle)
    else:p.text(content(one(root,'portrait')),px+25,portrait_y+145,290,24,INK,'center',True)
    def plate(y,height,alpha,radius):
        layer=Image.new('RGBA',(720,h));d=ImageDraw.Draw(layer)
        d.rounded_rectangle((38,y,682,y+height),radius=radius,fill=(5,13,30,alpha),outline=(255,255,255,65))
        p.image=Image.alpha_composite(p.image.convert('RGBA'),layer).convert('RGB');p.draw=ImageDraw.Draw(p.image)
    plate(stage_y,sh,204,16);p.text(content(stage),50,stage_y+18,620,30,accent,'center',True,bold=True)
    plate(result_y,rh,224,20);p.text(content(result),64,result_y+24,592,24,'#ffffff',paint=True)
    p.text(content(footer),38,result_y+rh+22,644,17,'#e1e8f2','center',True)
    return p.image


def render_faithful(html,width,kind,audit=None):
    parser=CardParser();parser.feed(html)
    roots=parser.root.find('card')
    if len(roots)!=1:return None
    root=roots[0]
    if kind=='pet':return pet_card(root,width,audit)
    if kind=='map':return atlas(root,audit)
    if kind=='mount':return mount_card(root,width,audit)
    if kind=='ritual':return ritual_card(root,audit)
    return None


class RestoredPainter(Composition):
    """Remaining layouts retain their original paper and typographic hierarchy."""
    def box(self,x,y,w,h,paint,fill='#f7f2e3'):
        if fill=='#f7f2e3':
            if self.theme in {'national','cloud'} and paint:
                base=self.image.crop((round(x),round(y),round(x+w),round(y+h)))
                paper=Image.new('RGB',base.size,'#faf6eb')
                self.image.paste(Image.blend(base,paper,.86),(round(x),round(y)))
                self.draw.rectangle((x,y,x+w,y+h),outline='#d6c4a0')
            else:self.paper(x,y,w,h,paint)
        else:super().box(x,y,w,h,paint,fill)

    def fields(self,n,x,y,w,paint,mode='detail'):
        if self.theme!='cultivator' or mode!='detail':return super().fields(n,x,y,w,paint,mode)
        kids=children(n);label=next((c for c in kids if c.tag=='span'),None)
        value=next((c for c in kids if c.tag in {'b','strong'}),None)
        if value is None:return None
        notes=[c for c in kids if c not in (label,value)];pad=12
        lh=self.text(content(label) if label else '',x+pad,y+10,w*.30-pad,12,'#947035',paint=paint)
        vh=self.text(content(value),x+w*.30,y+9,w*.70-pad,16,align='right',paint=paint,bold=True)
        yy=y+10+max(lh,vh)+6
        for c in notes:yy+=self.text(content(c),x+pad,yy,w-2*pad,12,'#6c796e',paint=paint)+3
        return max(67,yy-y+10)

    def node(self,n,x,y,w,paint):
        cs=classes(n);kids=children(n)
        if self.theme=='bag' and 'item-slot' in cs:
            label=content(one(n,'rname'));count=content(one(n,'rcount'))
            th=self.text(label,0,0,w-24,18,paint=False,bold=True)
            ch=self.text(count,0,0,w-24,18,paint=False)
            h=max(175,120+th+ch)
            self.box(x,y,w,h,paint)
            if paint:self.icon(label,x+w/2-23,y+23)
            self.text(label,x+12,y+91,w-24,18,align='center',paint=paint,bold=True)
            bw=min(w-24,max(62,body_font(18).getlength(count)+28))
            bx=x+(w-bw)/2;by=y+103+th
            self.box(bx,by,bw,ch+8,paint,'#28585b')
            self.text(count,bx+10,by+3,bw-20,18,'#fff2cb','center',paint)
            return h
        if cs & {'mast-title','brand'}:
            return self.title(content(n),x,y,w,66 if 'brand' in cs else 38,
                              '#ffe8aa',14 if 'brand' in cs else 7,paint)
        if self.theme=='menu' and 'cols' in cs:
            pad=20;gap=38;cw=(w-2*pad-gap)/2
            heights=[self.node(c,0,0,cw,False)+22 for c in kids]
            cuts=range(1,len(kids)) if len(kids)>1 else [len(kids)]
            cut=min(cuts,key=lambda i:abs(sum(heights[:i])-sum(heights[i:])))
            h=max(sum(heights[:cut]),sum(heights[cut:]))+2*pad
            self.box(x,y,w,h,paint)
            if paint:self.draw.line((x+w/2,y+pad,x+w/2,y+h-pad),fill='#c3ad7a')
            for i,group in enumerate((kids[:cut],kids[cut:])):
                self.stack(group,x+pad+i*(cw+gap),y+pad,cw,paint,22)
            return h
        if self.theme=='menu' and 'sect' in cs:
            return self.stack(kids,x,y,w,paint,12)+14
        if self.theme=='cultivator' and 'loadout' in cs:
            if len(kids)!=3:raise ValueError('Unsupported loadout')
            side=126;gap=14;middle=w-2*side-2*gap
            h=max(self.stack(children(c),0,0,side,False,12) for c in (kids[0],kids[2]))
            px=x+side+gap
            if paint:self.draw.rectangle((px,y,px+middle,y+h),fill='#f4f0e5',outline='#c5ad77')
            pictures=children(kids[1])
            if pictures:self.picture(pictures[0],px+1,y+1,middle-2,h-2,paint)
            self.stack(children(kids[0]),x,y,side,paint,12)
            self.stack(children(kids[2]),px+middle+gap,y,side,paint,12)
            return h
        if self.theme=='national' and 'blessing' in cs:
            pad=24
            th=self.stack(kids,x+pad,y+32,w-2*pad,False,24)
            h=max(350,th+64)
            self.box(x,y,w,h,paint)
            self.stack(kids,x+pad,y+32,w-2*pad,paint,24)
            return h
        if self.theme=='national' and 'seal' in cs:
            value=content(n);bw=body_font(15).getlength(value)+24;xx=x+(w-bw)/2
            if paint:self.draw.rectangle((xx,y,xx+bw,y+32),outline='#a94432')
            self.text(value,xx+12,y+5,bw-24,15,'#a94432',paint=paint)
            return 32
        return super().node(n,x,y,w,paint)

    def icon(self,name,x,y):
        # Original CSS icon box is 62px; legacy callers supply a 46px origin.
        x-=8;y-=5
        column=min(2,int(x/(self.width/3)))
        ink=['#346961','#71597b','#9e7138'][column]
        fills=[('#fff8d5','#ddcd93'),('#fff0e6','#ded0dc'),('#fff5c6','#e0cd91')][column]
        tile=gradient(62,62,*fills);mask=Image.new('L',(62,62))
        ImageDraw.Draw(mask).rounded_rectangle((0,0,61,61),radius=14,fill=255)
        self.image.paste(tile,(round(x),round(y)),mask)
        d=self.draw;d.rounded_rectangle((x,y,x+61,y+61),radius=14,outline='#bca468')
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


def render_game_card(html,width,audit=None):
    from . import raster_cards
    kind=raster_cards.card_kind(html)
    if kind in {'pet','map','mount','ritual'}:
        return render_faithful(html,width,kind,audit)
    # Inject the composed painter into a private function namespace. Avoid
    # mutable module globals: parallel jobs must not swap each other's painters.
    namespace=dict(raster_cards.render_card.__globals__,Painter=RestoredPainter)
    render=types.FunctionType(raster_cards.render_card.__code__,namespace,
                              argdefs=raster_cards.render_card.__defaults__)
    return render(html,width,audit)
