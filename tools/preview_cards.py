"""Render real card templates with sample data, without starting the bot."""
import ast
import base64
import re
import sys
import subprocess
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from petpark import card_theme, data, images, pet as petmod


def templates():
    tree = ast.parse((ROOT / 'main.py').read_text(encoding='utf-8'))
    names = {'_menu_text', '_menu_html', '_menu_purify', '_menu_esc',
             '_pet_card_html', '_bag_card_html', '_pet_portrait_uri', '_pct',
             '_bar_row', '_card_row', '_card_crop', '_crop_menu', '_write_html_png'}
    constants = {'_MENU_CSS', '_MENU_EMOJI_RE', '_PET_CARD_CSS', '_BAG_CARD_CSS'}
    owner = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                 and any(getattr(c, 'name', '') == '_pet_card_html' for c in n.body))
    selected = [n for n in owner.body if getattr(n, 'name', '') in names or
                isinstance(n, ast.Assign) and any(getattr(t, 'id', '') in constants for t in n.targets)]
    cls = ast.ClassDef(name='Cards', bases=[], keywords=[], body=selected, decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[]))
    from PIL import Image
    def chrome_run(args, **kwargs):
        if sys.platform == 'win32' and args[0] == 'google-chrome':
            args[0] = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
        return subprocess.run(args, **kwargs)
    env = dict(re=re, Path=Path, base64=base64, data=data, images=images, petmod=petmod,
               card_theme=card_theme, Image=Image, logger=SimpleNamespace(warning=print),
               subprocess=SimpleNamespace(run=chrome_run, DEVNULL=subprocess.DEVNULL))
    exec(compile(module, str(ROOT / 'main.py'), 'exec'), env)
    return env['Cards']()


if __name__ == '__main__':
    from playwright.sync_api import sync_playwright
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'card-previews'
    out.mkdir(parents=True, exist_ok=True)
    cards = templates()
    cards.store = SimpleNamespace(custom_images_dir=out, active_events=lambda: {})
    pet = petmod.new_pet('九尾狐', '传说')
    pet.update(nickname='月见', level=36, exp=1680, hp=2350, hp_max=3200,
               atk=860, intel=720, energy=76, energy_max=100, mood=4,
               skills=['灵光一闪', '御风'], talent='灵心', tags=['成长中', '出战伙伴'])
    bag = {'bag': {'九转还魂丹': 14, '史诗卡': 1, '宠物卡': 4,
                   '攻击宝符': 1, '普通卡': 5, '桂花酿': 4,
                   '混沌碎片': 1, '神级碎片': 6, '聚灵丹': 3}}
    samples = {'pet': (cards._pet_card_html(pet), 760, '.card'),
               'bag': (cards._bag_card_html(bag), 760, '.card'),
               'menu': (cards._menu_html(), 900, '.scroll'),
               'empty-bag': (cards._bag_card_html({'bag': {}}), 760, '.card'),
               'long-bag': (cards._bag_card_html({'bag': {'高级宠物成长材料测试超长名称完整显示': 99999}}), 760, '.card'),
               'large-bag': (cards._bag_card_html({'bag': {f'测试物品{i:03}': i for i in range(100)}}), 760, '.card')}
    assert '&quot;' in cards._menu_esc('"<测试>🐾')
    assert '🐾' not in cards._menu_esc('"<测试>🐾')
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe', headless=True)
        for name, (html, width, selector) in samples.items():
            html = card_theme.finish_html(html)
            (out / f'{name}.html').write_text(html, encoding='utf-8')
            page = browser.new_page(viewport={'width': width, 'height': 900}, device_scale_factor=1)
            page.set_content(html, wait_until='load')
            panel = page.locator(selector)
            panel.screenshot(path=str(out / f'{name}.png'))
            bounds = panel.bounding_box()
            overflow = page.locator('.rname, .name, .sect, .row v').evaluate_all('(els) => els.filter(e => e.scrollWidth > e.clientWidth + 1).map(e => e.textContent)')
            assert not overflow, (name, overflow)
            print(name, bounds, 'no text overflow')
            # Also exercise the bot's real full-window screenshot and crop path.
            from PIL import Image
            target = out / f'{name}-production.png'
            canvas_height = max(1800, int(bounds['height']) + 300)
            assert cards._write_html_png(html, name, target, crop=cards._card_crop,
                                        win_w=width, win_h=canvas_height)
            with Image.open(target) as produced:
                # Multicolumn balancing can differ with viewport height. Verify
                # complete bottom border and spare canvas instead of identical height.
                assert produced.width == width and 100 < produced.height < canvas_height - 50
                for x, y in [(0,0), (width-1,0), (0,produced.height-1), (width-1,produced.height-1)]:
                    assert produced.getpixel((x,y)) == (185,149,82), (name, x, y, produced.getpixel((x,y)))
            print(name, 'production crop verified; all four corners are gold, no canvas border')
            page.close()
        browser.close()
