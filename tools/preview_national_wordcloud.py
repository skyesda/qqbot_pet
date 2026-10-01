"""Preview real cloud rendering and draw result with isolated example data."""
import sys
from pathlib import Path
import tempfile
import types
import markdown
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT.parent/'petbot_framework/compat'))
from petpark.moonfest.engine import MoonfestActivity
from petpark.moonfest.national_pool import DRAW
from petpark.moonfest.wordcloud import wordcloud_html

texts=['祝祖国繁荣昌盛，国泰民安，山河锦绣。','愿人民安居乐业，幸福安康，家国团圆。','国庆快乐，愿中华儿女砥砺前行，未来可期！',
       '致敬祖国母亲，祝愿祖国繁荣富强，五谷丰登。','愿华夏风调雨顺，万家灯火，平安喜乐。','祖国万岁，祝祖国蒸蒸日上，长治久安。',
       '愿中国腾飞发展，梦想绽放，青春奋进。','祖国生日快乐，江山如画，锦绣山河！','祝祖国欣欣向荣，民族复兴，盛世华诞。',
       '愿祖国人民健康平安，万事顺遂，生活美好。','为祖国自豪，为家园祝福，愿山河锦绣。','祝国庆快乐，愿大家前程似锦，幸福团圆。']
out=ROOT.parent/'output/national-wordcloud'
out.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory() as temp:
    act=MoonfestActivity(types.SimpleNamespace(),Path(temp))
    act._now=lambda:DRAW
    state=act.national_pool.state()
    state['spent'][0]=1917
    state['counts']={str(i):1 for i in range(12)}
    state['participants']={str(i):{'group':'示例群','name':['云栖','长风','青岚'][i%3]+str(i+1)} for i in range(12)}
    state['blessings']={str(i):{'text':text} for i,text in enumerate(texts)}
    act.national_pool.tick()
    cloud=wordcloud_html(texts,12,True,sum(state['allocations'].values()))
    empty=wordcloud_html([],0)
    result=act.national_pool.results()
    (out/'词云.html').write_text(cloud,encoding='utf-8')
    (out/'瓜分结果.md').write_text(result,encoding='utf-8')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge')
        page=browser.new_page(viewport={'width':720,'height':1000})
        for name,html in [('词云',cloud),('空词云',empty)]:
            page.set_content(html,wait_until='load')
            assert page.evaluate('document.documentElement.scrollWidth')==720
            boxes=page.locator('.cloud span').evaluate_all('(els)=>els.map(e=>{const r=e.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom}})')
            for i,a in enumerate(boxes):
                for b in boxes[i+1:]:
                    assert not(a['left']<b['right'] and a['right']>b['left'] and a['top']<b['bottom'] and a['bottom']>b['top'])
            page.locator('.card').screenshot(path=str(out/(name+'.png')))
        page.set_content('<meta charset="utf-8"><style>body{width:720px;margin:0;padding:32px;background:#f5efe2;color:#193c34;font:20px/1.8 KaiTi}h2{color:#9c382b}table{width:100%;border-collapse:collapse}th,td{border-bottom:1px solid #cbb997;padding:9px}strong{color:#9c382b}</style>'+markdown.markdown(result,extensions=['tables']))
        page.screenshot(path=str(out/'瓜分结果.png'),full_page=True)
        browser.close()
print('WORDCLOUD_PREVIEW_OK: no overlaps, current draw totals, empty state')
print(out)
