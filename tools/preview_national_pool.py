"""Render the new command with temporary data and a simulated event clock."""
import tempfile
import types
from pathlib import Path

import markdown
from playwright.sync_api import sync_playwright
from preview_festival_markdown import ROOT
from preview_petpark.petpark.moonfest.engine import MoonfestActivity
from preview_petpark.petpark.moonfest.national_pool import START, END, DRAW


if __name__ == '__main__':
    out = ROOT.parent / 'output/national-pool'
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        act = MoonfestActivity(types.SimpleNamespace(), Path(tmp))
        act.jev.enabled = False
        act._spawn = lambda coro: coro.close()
        act._now = lambda: START+1
        player = act._get_player('10001', '20001')
        player['name'] = '云栖'
        player['national_round'] = {'seed': 502, 'stride': 1, 'cursor': 0}
        samples = {'抽题': act.dispatch(None, '20001', '10001', '国庆快乐')}
        state = act.national_pool.state()
        state['spent'][0] = 1000
        act._now = lambda: START+3600
        samples['小时结转'] = act.dispatch(None, '20001', '10001', '国庆奖池')
        act._now = lambda: END
        samples['祝福时刻'] = act.dispatch(None, '20001', '10001', '祝福时刻')
        # Synthetic counts to illustrate the weighted draw, not real submissions.
        state['counts'] = {'20001': 2, '20002': 1}
        state['participants'] = {'20001': {'group': '10001'}, '20002': {'group': '10001'}}
        act._now = lambda: DRAW
        samples['开奖公告'] = act.national_pool.tick()[0]
        samples['我的开奖'] = act.dispatch(None, '20001', '10001', '国庆奖池')
        assert state['allocations'] == {'20001': 66000, '20002': 33000}
    css = '''*{box-sizing:border-box}body{margin:0;background:#f5f0e4;color:#173b35;font:21px/1.8 "KaiTi","Noto Serif CJK SC",serif}
    .card{width:720px;padding:32px 42px;border-top:5px solid #a74432}h2{font-size:32px;color:#a74432;margin-top:0}
    h3{font-size:25px;border-bottom:1px solid #d5c8a9;padding-bottom:10px}p{margin:15px 0}strong{color:#a74432}
    blockquote{margin:24px 0;padding:10px 20px;background:#ece3ce;border-left:3px solid #a74432;font-size:27px}
    .note{font-size:15px;color:#7e7766;margin-bottom:24px}'''
    (out/'展示示例.md').write_text('\n\n---\n\n'.join(samples.values()), encoding='utf-8')
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe', headless=True)
        page = browser.new_page(viewport={'width': 720, 'height': 1200})
        for name, reply in samples.items():
            html = '<!doctype html><meta charset="utf-8"><style>'+css+'</style><main class="card"><div class="note">排版预览 · 模拟日期和示例数据；QQ 字体由客户端决定</div>'+markdown.markdown(reply)+'</main>'
            (out/(name+'.html')).write_text(html, encoding='utf-8')
            page.set_content(html, wait_until='load')
            assert page.evaluate('document.documentElement.scrollWidth') == 720
            page.locator('.card').screenshot(path=str(out/(name+'.png')))
        browser.close()
    print(out)
