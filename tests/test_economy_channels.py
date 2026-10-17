"""修士经济渠道收编测试：副本掉灵石/玄晶 + 材料碎片、世界首领货币、试炼秘境。

复用 test_adventure.py 的 setUp/call/create + 假时钟模式。覆盖：
- reward() 通关给灵石/玄晶，并受 8 次/日上限约束（超限不发放）。
- reward() 低概率掉材料碎片（随机选择材料、数量在区间内）。
- 世界首领「讨伐首领」每次给灵石/玄晶 + 低概率碎片；「首领奖励」击杀后给灵石/玄晶。
- 试炼秘境每日限次、通关给三类奖励 + 高碎片率、失败消耗次数、跨天重置。
- data.py 材料碎片物品与 MATERIAL_FRAGMENTS 映射一致性。
"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from qqbot_pet.petpark import data
from qqbot_pet.petpark.adventure import content
from qqbot_pet.petpark.adventure.service import AdventureService
from qqbot_pet.petpark.store import PetStore


class EconomyChannelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'petpark.json'
        self.store = PetStore(self.path)
        self.now = 1800000000
        self.service = AdventureService(self.store, lambda: self.now)

    def call(self, text, qq='a', group='g'):
        return self.service.handle(group, qq, text.split())

    def create(self, profession='剑修', qq='a', group='g'):
        self.call('踏入仙途 ' + profession, qq, group)
        return self.store.get_player(qq, group)

    def _win(self, key, damage=300):
        return {
            "won": True, "winner": 0, "rounds": 5, "reason": "敌方全灭",
            "events": ["回合1：剑修修士出剑"],
            "units": [{"side": 0, "kind": "hero", "owner": key, "name": "剑修修士"}],
            "metrics": {key: {"damage": damage, "healing": 0, "absorbed": 0}},
        }

    def _lose(self, key, damage=120):
        return {
            "won": False, "winner": 1, "rounds": 6, "reason": "我方倒下",
            "events": ["回合1：剑修修士出剑"],
            "units": [{"side": 0, "kind": "hero", "owner": key, "name": "剑修修士"}],
            "metrics": {key: {"damage": damage, "healing": 0, "absorbed": 0}},
        }

    # ---- reward()：通关给灵石/玄晶，随等级增长，受 8 次/日上限 ----
    def test_reward_grants_currency_scaled_by_level(self):
        p = self.create()
        a = p['adventure']
        before_coin = self.store.get_currency(p, '灵石')
        before_jifen = self.store.get_currency(p, '玄晶')
        with patch('qqbot_pet.petpark.adventure.service.random.random', return_value=0.99):
            out = self.service.reward(a, 10, player=p)
        self.assertIn(f'灵石×{content.ADV_COIN_BASE + 10 * content.ADV_COIN_PER_LEVEL}', out)
        self.assertIn(f'玄晶×{content.ADV_JIFEN_BASE + 10 // content.ADV_JIFEN_PER_LEVEL}', out)
        self.assertNotIn('碎片', out)
        self.assertEqual(self.store.get_currency(p, '灵石'),
                         before_coin + content.ADV_COIN_BASE + 10 * content.ADV_COIN_PER_LEVEL)
        self.assertEqual(self.store.get_currency(p, '玄晶'),
                         before_jifen + content.ADV_JIFEN_BASE + 10 // content.ADV_JIFEN_PER_LEVEL)

    def test_reward_respects_eight_per_day_cap(self):
        p = self.create()
        a = p['adventure']
        self.service.reward(a, 1, player=p)  # 建立当日计数（date=today）
        a['rewards'] = content.DAILY_REWARDS
        before_coin = self.store.get_currency(p, '灵石')
        before_jifen = self.store.get_currency(p, '玄晶')
        out = self.service.reward(a, 1, player=p)
        self.assertIn('已领取', out)
        self.assertEqual(self.store.get_currency(p, '灵石'), before_coin)
        self.assertEqual(self.store.get_currency(p, '玄晶'), before_jifen)

    def test_reward_fragment_drop(self):
        p = self.create()
        a = p['adventure']
        with patch('qqbot_pet.petpark.adventure.service.random.random', return_value=0.0), \
             patch('qqbot_pet.petpark.adventure.service.random.choice', return_value='体力丹碎片'):
            out = self.service.reward(a, 5, player=p)
        self.assertIn('体力丹碎片', out)
        bag = self.store.get_player('a', 'g')['bag']
        got = bag.get('体力丹碎片', 0)
        self.assertGreaterEqual(got, content.ADV_FRAGMENT_BUNDLE[0])
        self.assertLessEqual(got, content.ADV_FRAGMENT_BUNDLE[1])

    # ---- 世界首领：讨伐给灵石/玄晶 + 碎片；击杀后首领奖励给灵石/玄晶 ----
    def test_world_boss_hit_and_kill_reward(self):
        p = self.create()
        self.service.boss_hp = 150  # 低血量，一击即可击杀
        key = self.service.key('g', 'a')
        with patch('qqbot_pet.petpark.adventure.service.simulate',
                   return_value=self._win(key, damage=300)):
            out = self.call('讨伐首领')
        self.assertIn(f'灵石×{content.BOSS_HIT_COIN}', out)
        self.assertIn(f'玄晶×{content.BOSS_HIT_JIFEN}', out)
        self.assertIn('首领已被击败', out)
        # 击杀后领共同奖励
        with patch('qqbot_pet.petpark.adventure.service.random.random', return_value=0.99):
            out2 = self.call('首领奖励')
        self.assertIn(f'灵石×{content.BOSS_KILL_COIN}', out2)
        self.assertIn(f'玄晶×{content.BOSS_KILL_JIFEN}', out2)

    # ---- 试炼秘境：每日限次、通关奖励、失败消耗、跨天重置 ----
    def test_trial_daily_cap_and_reward(self):
        p = self.create()
        key = self.service.key('g', 'a')
        with patch('qqbot_pet.petpark.adventure.service.simulate', return_value=self._win(key)):
            out1 = self.call('试炼秘境 1')
            out2 = self.call('试炼秘境 1')
        self.assertIn('试炼通关', out1)
        self.assertIn('灵石', out1)
        self.assertEqual(self.store.get_player('a', 'g')['adventure']['trial']['used'], 2)
        # 每日上限：第三次拒绝
        self.assertIn('明日再来', self.call('试炼秘境 1'))
        # 跨天重置后可再挑战
        self.now += 86400
        with patch('qqbot_pet.petpark.adventure.service.simulate', return_value=self._win(key)):
            out3 = self.call('试炼秘境 1')
        self.assertIn('试炼通关', out3)
        self.assertEqual(self.store.get_player('a', 'g')['adventure']['trial']['used'], 1)

    def test_trial_loss_consumes_charge_no_currency(self):
        p = self.create()
        key = self.service.key('g', 'a')
        before_coin = self.store.get_currency(p, '灵石')
        with patch('qqbot_pet.petpark.adventure.service.simulate', return_value=self._lose(key)):
            out = self.call('试炼秘境 1')
        self.assertIn('挑战失利', out)
        self.assertEqual(self.store.get_player('a', 'g')['adventure']['trial']['used'], 1)
        self.assertEqual(self.store.get_currency(p, '灵石'), before_coin)

    def test_trial_requires_valid_floor(self):
        self.create()
        self.assertIn('1—5', self.call('试炼秘境 0'))

    # ---- data.py 材料碎片物品与映射一致性 ----
    def test_material_fragment_defs_consistent(self):
        self.assertEqual(content.MATERIAL_FRAGMENT_COMBINE, data.MATERIAL_FRAGMENT_COMBINE)
        for frag, mat in data.MATERIAL_FRAGMENTS.items():
            it = data.ITEMS.get(frag)
            self.assertIsNotNone(it, f'{frag} 未定义')
            self.assertFalse(it.get('usable'))
            self.assertEqual(it.get('category'), '材料')
            self.assertIsNotNone(data.ITEMS.get(mat), f'{mat} 原料未定义')
        # 所有材料碎片都在 content 池中（service.reward 随机选取用）
        self.assertEqual(set(data.MATERIAL_FRAGMENTS), set(content.MATERIAL_FRAGMENTS))


if __name__ == '__main__':
    unittest.main()
