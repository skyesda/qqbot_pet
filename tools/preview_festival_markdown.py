"""Offline examples of every festival command; never submits real game actions."""
import sys
import tempfile
import types
from pathlib import Path

import markdown
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / 'petbot_framework/compat'))
pkg = types.ModuleType('preview_petpark')
pkg.__path__ = [str(ROOT)]
sys.modules[pkg.__name__] = pkg
from preview_petpark.petpark.moonfest.engine import COMMANDS, MoonfestActivity


def examples():
    with tempfile.TemporaryDirectory() as tmp:
        activity = MoonfestActivity(types.SimpleNamespace(), Path(tmp))
        activity.jev.enabled = False
        activity._phase = lambda: 'both'
        activity._spawn = lambda coro: coro.close()
        activity._firework_review = lambda text: (True, '')
        activity._get_player('10001', '20001')['name'] = '云栖'
        replies = {}
        for command in sorted(COMMANDS):
            replies[command] = activity.dispatch(None, '20001', '10001', command)
        replies['月华榜'] = activity.dispatch(None, '20001', '10001', '月华榜')
        replies['签到成功'] = activity.dispatch(None, '20001', '20002', '拜月')
        replies['首次合成'] = activity.dispatch(None, '20001', '20001', '做月饼 五仁')
        replies['重制开炉'] = activity.dispatch(None, '20001', '20001', '做月饼 五仁')
        replies['喂养成功'] = activity.dispatch(None, '20001', '20001', '喂玉兔 月饼')
        replies['月华信息'] = activity.dispatch(None, '20001', '20001', '月华信息')
        return replies


if __name__ == '__main__':
    out = ROOT.parent / 'output/festival-markdown'
    out.mkdir(parents=True, exist_ok=True)
    replies = examples()
    (out / 'all-commands.md').write_text(
        '# 中秋国庆活动 · Markdown 回复示例\n\n示例数据，活动阶段为预览模拟，不代表线上状态。\n\n' +
        '\n\n---\n\n'.join(f'发送指令：{command}\n\n{text}' for command, text in replies.items()), encoding='utf-8')
    css = '''*{box-sizing:border-box}body{margin:0;background:#ede9dd;color:#173b35;font:19px/1.8 "KaiTi","Noto Serif CJK SC",serif}
    .card{width:720px;border-top:5px solid #a74432;padding:32px 40px;background:#f8f4e9}h2{font-size:32px;color:#a74432;margin:0 0 24px}
    h3{font-size:24px;margin:28px 0 15px;border-bottom:1px solid #d1c7af;padding-bottom:8px}p{margin:16px 0}
    blockquote{border-left:3px solid #a74432;padding:10px 20px;margin:20px 0;background:#f0e8d5;font-size:24px}
    table{width:100%;border-collapse:collapse;font-size:18px}th,td{padding:10px;border-bottom:1px solid #d7ccb7}th{background:#e6dfcd}
    strong{color:#a74432}ul{padding-left:24px}.note{font-size:14px;color:#807764;margin-bottom:16px}'''
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe', headless=True)
        page = browser.new_page(viewport={'width': 720, 'height': 1500})
        for command in ['活动帮助', '猜灯谜', '巡礼', '月华商店', '月华信息', '重制开炉']:
            body = markdown.markdown(replies[command], extensions=['tables'])
            html = '<!doctype html><meta charset="utf-8"><style>' + css + '</style><main class="card"><div class="note">排版预览 · 示例数据；QQ 实际字体由客户端决定</div>' + body + '</main>'
            (out / (command + '.html')).write_text(html, encoding='utf-8')
            page.set_content(html, wait_until='load')
            page.locator('.card').screenshot(path=str(out / (command + '.png')))
            assert page.evaluate('document.documentElement.scrollWidth') == 720, command
            assert page.locator('pre').count() == 0, command
            print(command, 'rendered without horizontal overflow or accidental code blocks')
        browser.close()
    print(out / 'all-commands.md')
