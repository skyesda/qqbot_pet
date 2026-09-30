"""Render and exercise admin profiles using an isolated temporary store."""
import asyncio
import copy
from pathlib import Path
import sys
import tempfile
import threading
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / 'petbot_framework/compat'))
from petpark.store import PetStore
from petpark.webadmin import WebAdmin
from petpark.pet import new_pet
from petpark.adventure.service import AdventureService
from playwright.sync_api import sync_playwright

OUT = ROOT.parent / 'output/admin-player'
OUT.mkdir(parents=True, exist_ok=True)
loop = asyncio.new_event_loop()
thread = threading.Thread(target=loop.run_forever, daemon=True)
thread.start()

with tempfile.TemporaryDirectory() as temp:
    store = PetStore(Path(temp) / 'store.json')
    AdventureService(store).handle('547205828', '100001', ['踏入仙途', '剑修'])
    player = store.get_player('100001', '547205828')
    player['adventure'].update(name='云栖', gender='女', level=68, realm=4, stamina=72)
    player['coin'], player['jifen'], player['diamond'] = 128500, 6820, 360
    player['pets'] = [new_pet('九尾狐', '稀有'), new_pet('卡比兽', '普通'), new_pet('七夕青鸟', '精品')]
    for i, pet in enumerate(player['pets']):
        pet.update(nickname=['青岚', '团子', '长歌'][i], level=68-i*5)
    player['active_pet'] = 0
    player['bag'] = {f'示例道具{i:02}': i+1 for i in range(95)}
    key = store.make_key('547205828', '100001')
    other = copy.deepcopy(player)
    other['group'] = '88888'
    other['adventure'].update(name='长风', gender='男', level=40, realm=3)
    store._data['players'][store.make_key('88888', '100001')] = other
    admin = WebAdmin(store, '127.0.0.1', 0, 'preview', 'preview')
    admin._tokens.add('preview-session')
    asyncio.run_coroutine_threadsafe(admin.start(), loop).result(30)
    site = next(iter(admin._runner.sites))
    port = site._server.sockets[0].getsockname()[1]
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge')
            context = browser.new_context(viewport={'width': 1440, 'height': 1100})
            context.add_cookies([{'name': 'pp_session', 'value': 'preview-session', 'url': f'http://127.0.0.1:{port}'}])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{port}/admin/player?key={quote(key)}')
            page.locator('#profile-save').wait_for()
            assert page.locator('.profile-gear').count() == 6
            assert page.locator('.profile-pet').count() == 3
            assert page.locator('#modal').is_hidden()
            page.screenshot(path=str(OUT/'desktop.png'), full_page=True)
            page.screenshot(path=str(OUT/'desktop-top.png'))
            page.locator('#fp_nickname').fill('临时修改')
            page.locator('#fp_nickname').fill('青岚')
            assert page.evaluate('profileDraft.pets[0].nickname') == '青岚'
            level = page.locator('[data-profile-field="adventure.level"]')
            level.fill('69')
            level.fill('68')
            assert page.evaluate('profileDraft.adventure.level') == 68
            page.locator('#fp_nickname').fill('编辑青岚')
            page.locator('.profile-pet').nth(1).click()
            page.locator('#fp_nickname').fill('编辑团子')
            page.locator('.profile-pet').nth(0).click()
            assert page.locator('#fp_nickname').input_value() == '编辑青岚'
            assert player['pets'][0]['nickname'] == '青岚'
            player['pets'][2]['exp'] = 777
            page.locator('#profile-save').click()
            page.get_by_text('修改已保存', exact=True).wait_for()
            saved = store._data['players'][key]
            assert saved['pets'][0]['nickname'] == '编辑青岚'
            assert saved['pets'][1]['nickname'] == '编辑团子'
            assert saved['pets'][2]['exp'] == 777
            assert saved['active_pet'] == 0
            assert 'battle_power' not in saved['pets'][0]
            page.locator('#profile-bag').scroll_into_view_if_needed()
            for _ in range(6):
                if page.locator('#profile-bag-more').is_hidden(): break
                page.locator('#profile-bag-more').click()
            assert page.locator('[data-bag]').count() == 95
            page.set_viewport_size({'width': 390, 'height': 844})
            page.evaluate('window.scrollTo(0,0)')
            page.screenshot(path=str(OUT/'mobile.png'), full_page=True)
            page.screenshot(path=str(OUT/'mobile-top.png'))
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors, errors
            print('PROFILE_BROWSER_CHECKS_OK: selection, saves, live merge, 95 bag entries, mobile overflow, JS errors')
            print(OUT)
            browser.close()
    finally:
        asyncio.run_coroutine_threadsafe(admin._runner.cleanup(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
