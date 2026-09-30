"""Render production greeting templates with explicitly synthetic sample data."""
import importlib.util
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('national_card', ROOT / 'petpark/moonfest/card.py')
card = importlib.util.module_from_spec(spec)
spec.loader.exec_module(card)


if __name__ == '__main__':
    out = ROOT.parent / 'output/national-day'
    out.mkdir(parents=True, exist_ok=True)
    rows = [(3, {'name': '云栖', 'text': '愿山河锦绣，国泰民安。愿万家灯火，岁岁长明。', 'likes': 28}),
            (2, {'name': '长风', 'text': '共赏盛世山河，同庆锦绣华诞。', 'likes': 16}),
            (1, {'name': '青岚', 'text': '把祝福写进秋风，愿祖国繁荣昌盛。', 'likes': 8})]
    samples = {
        'greeting': card.greeting_html(rows[0][1]['text'], '云栖', '2026年10月1日', '国泰民安', 18, 10, 1),
        'wall': card.wall_html(rows[:2], list(reversed(rows)), '今日主题 · 国泰民安'),
        'long-text': card.greeting_html('祝福祖国山河锦绣国泰民安万家灯火岁岁长明繁荣昌盛阖家团圆', '<云栖 & 长风>', '2026年10月1日', '愿山河锦绣国泰民安', 20, 0, 999),
        'full-wall': card.wall_html((rows * 2)[:5], (rows * 4)[:10], '国泰民安', page=1, pages=3, total=23),
        'older-wall': card.wall_html([], rows, '国泰民安', page=3, pages=3, total=23),
    }
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe', headless=True)
        page = browser.new_page(viewport={'width': 720, 'height': 5200}, device_scale_factor=1)
        for name, html in samples.items():
            (out / (name + '.html')).write_text(html, encoding='utf-8')
            page.set_content(html, wait_until='load')
            page.locator('.card').screenshot(path=str(out / (name + '.png')))
            height = page.locator('.card').bounding_box()['height']
            assert height < 5100, (name, height)
            assert page.evaluate('document.documentElement.scrollWidth') == 720
            assert not page.locator('.quote,.text,.signature').evaluate_all('(els)=>els.some(e=>e.scrollWidth>e.clientWidth+1)')
            print(name, 'height', height, 'no clipping')
        browser.close()
    print(out)
