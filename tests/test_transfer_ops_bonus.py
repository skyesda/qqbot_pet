"""大管理员「加次数」：为指定玩家追加当日的转让/赠送次数。

规则（2026-09-18 需求）：
- 仅大管理员可发（小管理员无权）；
- 只影响被指定的那个玩家，只影响今天；
- 次日自动回到 TRANSFER_DAILY_MAX_OPS 次。
"""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'petbot_framework' / 'compat'))
from qqbot_pet.main import PetParkPlugin, KNOWN_COMMANDS
from qqbot_pet.petpark import data
from qqbot_pet.petpark.store import PetStore

TODAY = '2026-09-18'
TOMORROW = '2026-09-19'


class TransferOpsBonusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = PetStore(Path(self.tmp.name) / 'data.json')
        self.plugin = PetParkPlugin.__new__(PetParkPlugin)
        self.plugin.store = self.store
        self.plugin.admins = {'root'}
        self.plugin._active_event_commands = lambda: set()
        self.a = self.store.get_player('a', 'g')
        self.b = self.store.get_player('b', 'g')
        self.c = self.store.get_player('c', 'g2')
        self.store._flush()

    def call(self, text, actor='root', group='g', day=TODAY):
        event = SimpleNamespace(get_sender_id=lambda: actor)
        with patch('qqbot_pet.main.time.strftime', return_value=day):
            return self.plugin.dispatch(event, actor, group, text)

    def limit(self, sender, target, group='g', count=1, kind='item', day=TODAY):
        """直接打 _check_transfer_limit，返回 (错误提示, 税率)。"""
        with patch('qqbot_pet.main.time.strftime', return_value=day):
            return self.plugin._check_transfer_limit(sender, target, group, count, kind)

    # ---------------- 注册与权限 ----------------

    def test_registered_in_known_commands(self):
        for c in ('加次数', '加转让次数', '加赠送次数'):
            self.assertIn(c, KNOWN_COMMANDS, f'{c} 未注册进 KNOWN_COMMANDS，群聊分发会被吞掉')

    def test_super_admin_only(self):
        self.assertIn('10 → **30**', self.call('加次数 b 20'))
        self.store.get_group('g')['subadmins'] = ['sub']
        self.assertIn('仅大管理员', self.call('加次数 b 20', actor='sub'))
        self.assertIn('仅大管理员', self.call('加次数 a 20', actor='b'))  # 普通玩家
        # 被拒时不得污染目标数据
        self.assertNotIn('bonus', self.a.get('_tx_daily', {}))

    # ---------------- 加次数生效 ----------------

    def test_grant_raises_cap_for_target_only(self):
        # b 先把今天 10 次用完
        self.b['_tx_daily'] = {'date': TODAY, 'count': 10}
        self.assertIn('（10次/天）', self.limit(self.b, self.a)[0])
        out = self.call('加次数 b 20')
        self.assertIn('10 → **30**', out)
        self.assertIn('今日已用 10 次，还可 20 次', out)
        self.assertIn('仅今日有效', out)
        # b 可以继续转，计数继续累加
        self.assertIsNone(self.limit(self.b, self.a)[0])
        self.b['_tx_daily']['count'] = 30
        over = self.limit(self.b, self.a)[0]
        self.assertIn('（30次/天，含管理员今日追加 20 次）', over)
        # 只影响 b：a / c 的上限仍是 10
        self.a['_tx_daily'] = {'date': TODAY, 'count': 10}
        self.assertIn('（10次/天）', self.limit(self.a, self.b)[0])
        self.c['_tx_daily'] = {'date': TODAY, 'count': 10}
        self.assertIn('（10次/天）', self.limit(self.c, self.a, group='g2')[0])

    def test_bonus_is_cumulative_and_capped(self):
        self.assertIn('10 → **30**', self.call('加次数 b 20'))
        self.assertIn('30 → **35**', self.call('加次数 b 5'))
        over = data.TRANSFER_DAILY_BONUS_MAX + 1
        self.assertIn('超出', self.call(f'加次数 b {over}'))
        # 单次超限不应改动已有额度
        self.assertEqual(self.b['_tx_daily']['bonus'], 25)

    def test_short_aliases_work(self):
        self.assertIn('10 → **12**', self.call('加转让次数 b 2'))
        self.assertIn('12 → **14**', self.call('加赠送次数 b 2'))

    # ---------------- 仅当天有效 ----------------

    def test_bonus_expires_next_day(self):
        self.b['_tx_daily'] = {'date': TODAY, 'count': 10}
        self.call('加次数 b 20')
        self.assertEqual(self.b['_tx_daily']['bonus'], 20)
        with patch('qqbot_pet.main.time.strftime', return_value=TODAY):
            self.assertEqual(self.plugin._tx_daily_cap(self.b), 30)
        # 次日额度即失效（同一份数据，换个日期读出的上限就回到 10）
        with patch('qqbot_pet.main.time.strftime', return_value=TOMORROW):
            self.assertEqual(self.plugin._tx_daily_cap(self.b), 10)
        # 次日首次转让：跨日整体换新 → 上限回到 10、已用清零、额度作废
        self.assertIsNone(self.limit(self.b, self.a, day=TOMORROW)[0])
        self.assertEqual(self.b['_tx_daily']['date'], TOMORROW)
        self.assertNotIn('bonus', self.b['_tx_daily'])
        # 次日用满 10 次照样被拦，且提示不回带昨日的追加
        self.b['_tx_daily']['count'] = 10
        over = self.limit(self.b, self.a, day=TOMORROW)[0]
        self.assertIn('（10次/天）', over)
        self.assertNotIn('含管理员今日追加', over)

    def test_grant_after_day_rollover_resets_stale_count(self):
        # b 的 _tx_daily 还停在昨天且已用满 → 今天加次数应先重置，别把昨天的账带过来
        self.b['_tx_daily'] = {'date': '2026-09-17', 'count': 10}
        out = self.call('加次数 b 20')
        self.assertEqual(self.b['_tx_daily'],
                         {'date': TODAY, 'count': 0, 'bonus': 20})
        self.assertIn('今日已用 0 次，还可 30 次', out)

    # ---------------- 参数校验与边界 ----------------

    def test_bad_args(self):
        for text in ('加次数', '加次数 b', '加次数 b abc', '加次数 b 0', '加次数 b -3'):
            self.assertIn('用法：加次数', self.call(text), text)
        self.assertIn('不存在', self.call('加次数 ghost 20'))

    def test_self_transfer_still_blocked(self):
        # 加次数只放宽次数上限，不放宽其它规则（自转仍被拒）
        self.call('加次数 b 20')
        self.assertIn('不能转让/赠送给自己', self.limit(self.b, self.b)[0])

    def test_admin_sender_still_exempt(self):
        # 大管理员自己转让本就全豁免，加次数不改变其行为
        root = self.store.get_player('root', 'g')
        self.call('加次数 root 5', actor='root')
        self.assertIsNone(self.limit(root, self.a)[0])

    def test_infinite_server_unaffected(self):
        # 无限服本就免每日次数，加次数不改变其放行结果
        self.store.get_group('g')['server_type'] = 'infinite'
        self.a['_tx_daily'] = {'date': TODAY, 'count': 999}
        self.assertIsNone(self.limit(self.a, self.b)[0])


if __name__ == '__main__':
    unittest.main()
