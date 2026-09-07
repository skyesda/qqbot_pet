"""Render all actual breakthrough outcomes and verify images/layout locally."""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from petpark import card_theme, pet as petmod
from petpark.breakthrough_card import card_html
from preview_cards import templates
from playwright.sync_api import sync_playwright


def main():
    out = ROOT / 'card-previews' / 'breakthrough'
    out.mkdir(parents=True, exist_ok=True)
    cards = templates()
    cards.store = SimpleNamespace(custom_images_dir=out)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe', headless=True)
        page = browser.new_page(viewport={'width': 760, 'height': 1600})
        for action, name, stage, level, roll in [
            ('进化', 'evolution', '幼年期', 60, 0.9),
            ('飞升', 'ascension', '超究极体', 120, 0.9),
            ('渡劫', 'tribulation', '飞升', 220, 0.9),
            ('渡劫', 'tribulation-failed', '飞升', 220, 0.1),
        ]:
            pet = petmod.new_pet('九尾狐', '传说')
            pet.update(stage=stage, level=level, nickname='月见', exp=230000)
            with patch.object(petmod.random, 'random', return_value=roll):
                ok, msg = {'进化': petmod.evolve, '飞升': petmod.ascend, '渡劫': petmod.tribulation}[action](pet)
            if not ok:
                msg += '\n天劫余威未散，30 分钟后才可再次渡劫。'
            html = card_html(pet, action, stage, ok, msg, cards._pet_portrait_uri(pet))
            page.set_content(card_theme.finish_html(html), wait_until='load')
            assert page.locator('img').evaluate_all('(es)=>es.every(e=>e.complete&&e.naturalWidth>0)')
            assert page.locator('.card').evaluate('(e)=>e.scrollWidth===e.clientWidth')
            page.locator('.card').screenshot(path=str(out / f'{name}.png'))
            print(name, 'portrait loaded; no horizontal overflow')
        assert cards._write_html_png(html, 'breakthrough-preview', out / 'production.png', crop=cards._card_crop, win_w=760, win_h=4200)
        browser.close()


if __name__ == '__main__':
    main()
