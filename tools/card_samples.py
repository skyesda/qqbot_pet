"""Offline built-in card snapshots. No framework startup, production store or sends."""
import ast
import base64
import io
import importlib.util
import re
import time
from pathlib import Path

from PIL import Image
from petpark import card_theme, data
from petpark.adventure.card import card_html, equipment_summary
from petpark.adventure.map_card import map_html
from petpark.breakthrough_card import card_html as breakthrough_html

def load_fixture_module(name):
    path=Path(__file__).resolve().parents[1]/'petpark/moonfest'/f'{name}.py'
    spec=importlib.util.spec_from_file_location('fixture_'+name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

greeting_html=load_fixture_module('card').greeting_html
wall_html=load_fixture_module('card').wall_html
wordcloud_html=load_fixture_module('wordcloud').wordcloud_html


def samples():
    root=Path(__file__).resolve().parents[1]
    tree=ast.parse((root/'main.py').read_text(encoding='utf-8'))
    owner=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='PetParkPlugin')
    names={'_bag_card_html','_mount_card_html','_mount_value','_mount_reward_range',
           '_menu_html','_menu_esc','_menu_purify','_short_num'}
    cls=ast.ClassDef(name='Cards',bases=[],keywords=[],body=[n for n in owner.body if getattr(n,'name','') in names],decorator_list=[])
    env=dict(re=re,time=time,data=data,card_theme=card_theme,equipment_summary=equipment_summary)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[])),'<cards>','exec'),env)
    bot=env['Cards']();bot._display_uid=lambda value:str(value)
    bot._MENU_EMOJI_RE=re.compile('[\U0001F000-\U0001FAFF]')
    bot._menu_text=lambda:'## 灵契仙途 · 指令菜单\n> 修士与灵宠同行\n'+''.join(
        f'**【修行卷{i}】**\n- 我的修士 · 我的宠物（查看伙伴）\n- 修士修炼 · 修士突破\n> 长说明：与灵宠同游山海，完成每日活动。\n' for i in range(1,9))
    bot._mount_custom_portrait_uri=lambda *_:None
    # A real small bundled portrait keeps output and benchmark deterministic.
    src=root/'petpark/assets/cultivator/sword-female.webp'
    picture='data:image/webp;base64,'+base64.b64encode(src.read_bytes()).decode()
    bot._mount_portrait_uri=lambda *_:picture
    player={'qq':'测试玩家','mounts':{'测试灵骑':{'custom':True,'level':12,'power':123456,'plate':'灵-2026','stars':5}}}
    a={'name':'云栖','profession':'剑修','gender':'女','realm':0,'level':14,'heaven':1,'cultivation':460,'ore':21,
       'equipment':{k:3 for k in ['weapon','robe','seal','crown','boots','pendant']},
       'equip_tier':{},'equip_affix':{},'bonus':{'atk':3},'style':'均衡','pet_role':'攻击',
       'wudao':6,'gengu':9,'spirit_root':'金灵根','tactics':['归元诀'],'stamina':72,'stamina_max':120,
       'cleared':['1','2'],'milestones':['hard:1'],'hp':0,'hp_at':0}
    player['adventure']=a
    player['pets']=[]
    result={
        'cultivator':(card_html(player,'g\x1fq'),760),
        'map':(map_html(player),940),
        'menu':(bot._menu_html(),900),
        'bag':(bot._bag_card_html({**player,'bag':{'聚灵丹':9,'国庆纪念符':3,'混沌碎片':7}}),760),
        'bag-large':(bot._bag_card_html({**player,'bag':{f'很长的测试物品名称{i}':10**25+i for i in range(100)}}),760),
        'bag-empty':(bot._bag_card_html({'bag':{}}),760),
        'mount':(bot._mount_card_html('测试灵骑',player,'my'),760),
        'greeting':(greeting_html('愿祖国繁荣昌盛，山河锦绣，万家灯火长明。','云栖','2026年10月1日','山河同庆',18,10,1),720),
        'wall':(wall_html([],[(i,{'text':f'第{i}笺：愿祖国繁荣昌盛，山河锦绣。','name':'云栖','likes':i}) for i in range(1,11)],'山河同庆',1,2,20),720),
        'cloud':(wordcloud_html(['国庆快乐，愿祖国繁荣昌盛，国泰民安。','山河锦绣，人民幸福，万事顺遂。'],2),720),
        'cloud-empty':(wordcloud_html([],0),720),
    }
    for action in ['进化','飞升','渡劫']:
        result['ritual-'+action]=(breakthrough_html({'nickname':'青岚','stage':'成长期'},action,'幼年期',True,
                                                action+'成功！\n消耗材料 ×1\n神器与秘技已返还背包\n下一步：查看我的宠物',picture),760)
    return result
