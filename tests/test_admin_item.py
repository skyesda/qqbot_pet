"""大管理员「加道具 / 减道具」：增减指定玩家背包里的道具。

规则（2026-09-20 需求）：
- 仅大管理员可发（小管理员无权）；
- 数量不设上限，但必须是正整数；
- 减道具时对方数量不足 → 原样报错、一个字都不动；
- 道具名先查 data.ITEMS，再兜活动道具（与「使用」同一口径）。
"""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'petbot_framework' / 'compat'))
from qqbot_pet.main import PetParkPlugin, KNOWN_COMMANDS
from qqbot_pet.petpark import data
from qqbot_pet.petpark.store import PetStore


class AdminItemTests(unittest.TestCase):
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
        self.store._flush()

    def call(self, text, actor='root', group='g'):
        event = SimpleNamespace(get_sender_id=lambda: actor)
        return self.plugin.dispatch(event, actor, group, text)

    def bag(self, player):
        return player.get('bag') or {}

    def add_event_item(self, name='桂花酿'):
        self.store.events()['e1'] = {
            'enabled': True, 'start_at': 0, 'end_at': 2 ** 31,
            'event_items': {name: {
                'price': 1, 'currency': '积分', 'category': '道具',
                'usable': True, 'desc': '活动道具', 'effect': {},
            }},
        }

    # ---------------- 注册与权限 ----------------

    def test_registered_in_known_commands(self):
        for c in ('加道具', '减道具'):
            self.assertIn(c, KNOWN_COMMANDS, f'{c} 未注册进 KNOWN_COMMANDS，群聊分发会被吞掉')

    def test_super_admin_only(self):
        self.assertIn('×10', self.call('加道具 b 聚灵丹 10'))
        self.store.get_group('g')['subadmins'] = ['sub']
        self.assertIn('仅大管理员', self.call('加道具 b 聚灵丹 5', actor='sub'))
        self.assertIn('仅大管理员', self.call('加道具 b 聚灵丹 5', actor='b'))
        # 被拒时不得污染目标背包
        self.assertEqual(self.bag(self.b), {'聚灵丹': 10})

    # ---------------- 加道具 ----------------

    def test_add_item(self):
        out = self.call('加道具 b 聚灵丹 10')
        self.assertIn('已为用户 `b` 增加 📦**聚灵丹 ×10**', out)
        self.assertIn('聚灵丹：0 → **10**', out)
        self.assertEqual(self.bag(self.b), {'聚灵丹': 10})
        # 累加
        self.assertIn('聚灵丹：10 → **30**', self.call('加道具 b 聚灵丹 20'))
        self.assertEqual(self.bag(self.b), {'聚灵丹': 30})
        # 只影响目标
        self.assertEqual(self.bag(self.a), {})

    def test_add_event_item(self):
        # 「桂花酿」这类活动道具不在 data.ITEMS 里，仍须能发放
        self.add_event_item('桂花酿')
        self.assertNotIn('桂花酿', data.ITEMS)
        self.assertIn('×3', self.call('加道具 b 桂花酿 3'))
        self.assertEqual(self.bag(self.b), {'桂花酿': 3})

    def test_no_quantity_cap(self):
        # 用户明确要求不限量：超大数字不应被拦（回执按 _short_num 缩写，落库是原值）
        out = self.call('加道具 b 聚灵丹 999999999')
        self.assertIn('×10.00亿', out)
        self.assertEqual(self.bag(self.b), {'聚灵丹': 999999999})

    # ---------------- 减道具 ----------------

    def test_remove_item(self):
        self.b['bag'] = {'聚灵丹': 30}
        out = self.call('减道具 b 聚灵丹 10')
        self.assertIn('已为用户 `b` 减少 📦**聚灵丹 ×10**', out)
        self.assertIn('聚灵丹：30 → **20**', out)
        self.assertEqual(self.bag(self.b), {'聚灵丹': 20})

    def test_remove_exact_clears_key(self):
        self.b['bag'] = {'聚灵丹': 5}
        self.assertIn('聚灵丹：5 → **0**', self.call('减道具 b 聚灵丹 5'))
        self.assertEqual(self.bag(self.b), {})  # 归零即删键，不留 0 值

    def test_remove_insufficient_touches_nothing(self):
        self.b['bag'] = {'聚灵丹': 30}
        out = self.call('减道具 b 聚灵丹 100')
        self.assertIn('只有 30 个，不足 100，未做任何改动', out)
        self.assertEqual(self.bag(self.b), {'聚灵丹': 30})  # 一个字都没动

    def test_remove_missing_item_touches_nothing(self):
        out = self.call('减道具 b 聚灵丹 1')
        self.assertIn('只有 0 个', out)
        self.assertEqual(self.bag(self.b), {})

    def test_remove_legacy_item_not_in_config(self):
        # 配置里已下线的遗留道具：只要对方背包真有，允许运营清理
        self.b['bag'] = {'上古残片': 7}
        self.assertNotIn('上古残片', data.ITEMS)
        self.assertIn('上古残片：7 → **0**', self.call('减道具 b 上古残片 7'))
        self.assertEqual(self.bag(self.b), {})
        # 但加道具不能凭空造出不存在的道具
        self.assertIn('没有叫', self.call('加道具 b 上古残片 1'))

    # ---------------- 参数校验 ----------------

    def test_unknown_item_rejected(self):
        self.assertIn('没有叫『压根不存在』的道具', self.call('加道具 b 压根不存在 1'))

    def test_bad_args(self):
        for text in ('加道具', '加道具 b', '加道具 b 聚灵丹',
                     '加道具 b 聚灵丹 abc', '加道具 b 聚灵丹 0', '减道具 b 聚灵丹 -1'):
            self.assertIn(('用法：加道具' if text.startswith('加') else '用法：减道具'),
                          self.call(text), text)
        self.assertEqual(self.bag(self.b), {})

    def test_target_not_found(self):
        self.assertIn('不存在', self.call('加道具 ghost 聚灵丹 1'))


if __name__ == '__main__':
    unittest.main()
