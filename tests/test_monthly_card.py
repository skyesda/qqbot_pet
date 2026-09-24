"""月卡卡密：生成/兑换、档位叠加、限时免费暂停计时、门户开机门禁放行。"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / 'petbot_framework/compat'))
from petpark import data
from petpark.portal import PlayerPortal, _COOKIE_NAME, _CSRF_HEADER
from petpark.store import PetStore


class Request:
    def __init__(self, body, token, csrf='test-csrf'):
        self.body = body
        self.cookies = {_COOKIE_NAME: token}
        self.headers = {_CSRF_HEADER: csrf}

    async def json(self):
        return self.body


class MonthlyCardStoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = PetStore(Path(temp.name) / 'store.json')
        self.player = self.store.get_player('user', 'group')

    def test_create_and_redeem_normal(self):
        [code] = self.store.create_monthly_cards('normal', count=1, prefix='M')
        card = self.store.cards()[code]
        self.assertEqual(card['monthly'], 'normal')
        self.assertEqual(card['monthly_days'], 30)
        tier, days, instant, err = self.store.redeem_monthly_card(code, self.player, 'u')
        self.assertIsNone(err)
        self.assertEqual((tier, days, instant), ('normal', 30, 0))
        st = self.store.monthly_state(self.player)
        self.assertEqual(st['tier'], 'normal')
        self.assertEqual(st['days'], 30)
        self.assertEqual(self.store.monthly_active(self.player), 'normal')
        # 已使用不可再兑
        _, _, _, err2 = self.store.redeem_monthly_card(code, self.player, 'u')
        self.assertEqual(err2, '该卡密已被使用')

    def test_invalid_tier_and_unknown_code(self):
        with self.assertRaises(ValueError):
            self.store.create_monthly_cards('super', count=1)
        _, _, _, err = self.store.redeem_monthly_card('NOSUCHCODE', self.player, 'u')
        self.assertEqual(err, '卡密不存在或输入有误')

    def test_flagship_instant_diamond_and_tier_stack(self):
        [fc] = self.store.create_monthly_cards('flagship', count=1)
        tier, days, instant, err = self.store.redeem_monthly_card(fc, self.player, 'u')
        self.assertIsNone(err)
        self.assertEqual(instant, data.MONTHLY_FLAGSHIP_INSTANT_DIAMOND)
        self.assertEqual(self.player.get('diamond'), data.MONTHLY_FLAGSHIP_INSTANT_DIAMOND)
        self.assertEqual(self.store.monthly_active(self.player), 'flagship')
        # 再兑普通卡：时长叠加、档位不降
        [nc] = self.store.create_monthly_cards('normal', count=1)
        self.store.redeem_monthly_card(nc, self.player, 'u')
        st = self.store.monthly_state(self.player)
        self.assertEqual(st['tier'], 'flagship')
        self.assertEqual(st['days'], 60)
        # 旗舰再兑一张：时长继续叠、每张各兑一次 6000 天晶
        [fc2] = self.store.create_monthly_cards('flagship', count=1)
        self.store.redeem_monthly_card(fc2, self.player, 'u')
        self.assertEqual(self.store.monthly_state(self.player)['days'], 90)
        self.assertEqual(self.player.get('diamond'), 2 * data.MONTHLY_FLAGSHIP_INSTANT_DIAMOND)

    def test_free_window_pause_and_expiry(self):
        [code] = self.store.create_monthly_cards('normal', count=1)
        self.store.redeem_monthly_card(code, self.player, 'u')
        now = int(time.time())
        # 上次结算在 10 天前，免费窗口覆盖其中 8 天 → 只扣 2 天，剩 28 天
        self.store._data['assistant_free'] = {
            'enabled': True,
            'start_at': now - 10 * 86400,
            'end_at': now - 2 * 86400,
        }
        self.player['monthly']['updated_at'] = now - 10 * 86400
        st = self.store.monthly_state(self.player, now)
        self.assertEqual(st['days'], 28)
        # 关闭免费窗口、把结算点拨到 31 天前 → 一次性扣穿、字段清理
        self.store._data['assistant_free']['enabled'] = False
        self.player['monthly']['updated_at'] = now - 31 * 86400
        self.assertIsNone(self.store.monthly_active(self.player, now))
        self.assertNotIn('monthly', self.player)

    def test_redeem_card_guard_rejects_monthly(self):
        [code] = self.store.create_monthly_cards('normal', count=1)
        rewards, items, err = self.store.redeem_card(code, self.player, 'u')
        self.assertIsNone(rewards)
        self.assertIsNone(items)
        self.assertEqual(err, '这是月卡，请用『兑换 卡密』兑换')


class MonthlyPortalGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = PetStore(Path(temp.name) / 'store.json')
        self.player = self.store.get_player('user', 'group')
        self.player['pets'] = [{'pet_id': '1', 'nickname': '测试宠', 'assistant': {
            'enabled': False, 'tasks': ['打工'], 'total_runs': 0,
            'last_run_at': 0, 'log': []}}]
        self.player['pet'] = self.player['pets'][0]
        self.player['assistant']['quota'] = 0
        account = self.store.create_account('user', 'unused', 'unused')
        self.store.bind_slot_to_account(account['id'], 'group', 'user')
        self.portal = PlayerPortal(self.store)
        self.token = self.portal._sign({
            'aid': account['id'], 'csrf': 'test-csrf',
            'exp': int(time.time()) + 300,
        })

    async def call(self, **changes):
        body = {'group_id': 'group', 'qq': 'user', 'pet_index': 0, **changes}
        resp = await self.portal._api_assistant(Request(body, self.token))
        return json.loads(resp.text)

    async def test_quota_zero_gate_opens_with_monthly(self):
        # 0 次数、无免费窗口、无月卡 → 拒绝开启，提示带「月卡」
        result = await self.call(enabled=True)
        self.assertFalse(result['ok'])
        self.assertIn('月卡', result['msg'])
        # 兑换月卡后 → 可开启，summary 带 monthly，次数余额原样保留 0
        [code] = self.store.create_monthly_cards('normal', count=1)
        self.store.redeem_monthly_card(code, self.player, 'u')
        result = await self.call(enabled=True)
        self.assertTrue(result['ok'])
        self.assertTrue(result['assistant']['enabled'])
        self.assertEqual(result['assistant']['monthly']['tier'], 'normal')
        self.assertEqual(self.store.assistant_quota(self.player), 0)


if __name__ == '__main__':
    unittest.main()
