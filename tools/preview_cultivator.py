"""Verify all eight portrait selections and the real card screenshot pipeline."""
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from petpark import card_theme
from petpark.adventure import AdventureService
from petpark.adventure.card import card_html, portrait_name
from petpark.adventure.content import GEAR, PROFESSIONS
from petpark.store import PetStore
from preview_cards import templates
from playwright.sync_api import sync_playwright


def main():
    out = ROOT / 'card-previews' / 'cultivator'
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp, sync_playwright() as pw:
        store = PetStore(Path(temp) / 'sample.json')
        service = AdventureService(store)
        service.handle('preview', 'sample', ['踏入仙途', '剑修'])
        player = store.get_player('sample', 'preview')
        a = player['adventure']
        a.update(name='月照青山', level=36, realm=2, heaven=2, cultivation=1680, ore=128)
        a['equipment'] = {slot: rank for rank, (slot, _) in zip((30, 24, 18, 12, 6, 0), GEAR.values())}
        browser = pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe', headless=True)
        page = browser.new_page(viewport={'width': 760, 'height': 1800})
        for profession in PROFESSIONS:
            for gender in ('男', '女'):
                a.update(profession=profession, gender=gender)
                name = portrait_name(a)
                html = card_theme.finish_html(card_html(player, service.key('preview', 'sample')))
                page.set_content(html, wait_until='load')
                assert page.locator('.gear').count() == 6
                assert page.locator('img').evaluate_all('(els) => els.every(e => e.complete && e.naturalWidth > 0)')
                assert page.locator('.gear-name').evaluate_all('(els) => els.every(e => e.scrollWidth <= e.clientWidth)')
                assert page.locator('.card').evaluate('(el) => el.scrollWidth <= el.clientWidth')
                portrait = page.locator('.portrait').bounding_box()
                assert portrait['height'] > 450
                page.locator('.card').screenshot(path=str(out / f'{name}.png'))
                print(name, '7 images loaded; six slots; no equipment text overflow')
        a.update(name='甲乙丙丁戊己庚辛壬癸子丑')
        html = card_html(player, service.key('preview', 'sample'), equipment=True)
        page.set_content(card_theme.finish_html(html), wait_until='load')
        assert page.locator('.card').evaluate('(el) => el.scrollWidth <= el.clientWidth')
        assert page.locator('.identity').evaluate('(el) => el.scrollWidth <= el.clientWidth')
        assert page.locator('.gear-name').evaluate_all('(els) => els.every(e => e.scrollWidth <= e.clientWidth)')
        page.locator('.card').screenshot(path=str(out / 'equipment.png'))
        cards = templates()
        cards.store = SimpleNamespace(custom_images_dir=out)
        assert cards._write_html_png(html, 'cultivator-preview', out / 'production.png',
                                     crop=cards._card_crop, win_w=760, win_h=1800)
        print('Production renderer and crop passed')
        browser.close()


if __name__ == '__main__':
    main()
