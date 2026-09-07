"""群级共享宗门（sect.py）核心交互与帮贡经济测试。

完全复用 test_adventure.py 的 setUp/call/create + 假时钟模式，纯 AdventureService。

注意两条测试硬约束：
1. 任何想发宗门指令的人都必须先「踏入仙途」（_handle 在宗门 dispatch 前就要求 adventure）。
2. service.handle 遇 RuleError 会 deepcopy 回滚并替换 self.store._data，因此**不要跨回滚持有
   self.state()/get_player() 的引用**——一律在断言前重新取。

构造了宗门创建/入宗审批/任务/探索/镇守/兑换/星辰阁/升级/权限边界/体力约束全链路。
"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from qqbot_pet.petpark.adventure.service import AdventureService
from qqbot_pet.petpark.adventure.sect import DAILY_LIMITS, SEC_MAX_LEVEL
from qqbot_pet.petpark.store import PetStore


class SectTests(unittest.TestCase):
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

    def state(self, group='g'):
        return self.store.sect_state(group)

    def adv(self, qq='a'):
        return self.store.get_player(qq, 'g')['adventure']

    # ---- 建宗 / 入宗 / 审批 / 名册 ----
    def test_create_join_approve_roster(self):
        self.create()
        self.create(qq='b')
        self.assertIn('已创立', self.call('创建宗门 铁剑门'))
        self.assertEqual(self.state()['name'], '铁剑门')
        self.assertEqual(self.state()['members']['a']['role'], '帮主')
        self.assertIn('宗', self.call('查看宗门'))
        self.assertIn('已提交', self.call('申请入宗', qq='b'))
        self.assertIn('b', self.state()['pending'])
        self.assertIn('已同意', self.call('同意入宗 b'))
        self.assertEqual(self.state()['members']['b']['role'], '帮众')
        self.assertNotIn('b', self.state()['pending'])
        self.assertIn('【帮众】', self.call('宗门名册'))

    def test_membership_permissions(self):
        self.create()
        self.create(qq='b')
        self.create(qq='c')
        # 未建宗申请 / 无宗任务
        self.assertIn('本群还没有宗门', self.call('申请入宗', qq='c'))
        self.assertIn('本群还没有宗门', self.call('宗门任务', qq='c'))
        self.call('创建宗门 铁剑门')
        # 帮主不可退出
        self.assertIn('帮主不可退出', self.call('退出宗门'))
        # 申请→审批权限
        self.assertIn('已提交', self.call('申请入宗', qq='b'))
        self.assertIn('只有帮主/长老', self.call('同意入宗 b', qq='c'))
        self.assertIn('只有帮主/长老可审批申请', self.call('拒绝入宗 b', qq='c'))
        # 非帮主不可封官/升级
        self.assertIn('只有帮主', self.call('封官 b 长老', qq='b'))
        self.assertIn('只有帮主', self.call('宗门升级', qq='b'))
        # 正常审批后 b 可退出，非成员再退拒绝
        self.call('同意入宗 b')
        self.assertIn('你已退出宗门', self.call('退出宗门', qq='b'))
        self.assertIn('你还不是宗门成员', self.call('退出宗门', qq='b'))

    def test_announce_only_boss(self):
        self.create()
        self.create(qq='b')
        self.call('创建宗门 铁剑门')
        self.call('申请入宗', qq='b')
        self.call('同意入宗 b')
        self.assertIn('只有帮主', self.call('宗门公告 欢迎加入', qq='b'))
        self.assertIn('已更新', self.call('宗门公告 欢迎加入'))
        self.assertEqual(self.state()['announce'], '欢迎加入')

    # ---- 宗门任务（每日次数 + 帮贡分润 + 跨天重置） ----
    def test_mission_daily_treasury_and_rollover(self):
        self.create()
        self.create(qq='b')
        self.call('创建宗门 铁剑门')
        self.call('申请入宗', qq='b')
        self.call('同意入宗 b')
        for _ in range(DAILY_LIMITS['mission']):
            self.assertIn('帮贡+', self.call('宗门任务'))
        s = self.state()
        # 帮贡分润 50% 入宗库；个人帮贡累计
        self.assertEqual(s['treasury'], 15 * DAILY_LIMITS['mission'])
        self.assertEqual(s['members']['a']['contribution'], 30 * DAILY_LIMITS['mission'])
        self.assertIn('已完成', self.call('宗门任务'))
        # 跨天重置后可再接
        self.now += 86400
        self.assertIn('帮贡+', self.call('宗门任务'))

    def test_mission_stamina_shortfall_rolls_back(self):
        p = self.create()
        self.call('创建宗门 铁剑门')
        self.adv()['stamina'] = 5
        out = self.call('宗门任务')
        self.assertIn('体力不足', out)
        self.assertEqual(int(self.adv()['stamina']), 5)
        self.assertEqual(int(self.adv()['cultivation']), int(p['adventure']['cultivation']))

    # ---- 北秘境（Lv2 + 消耗体力 + 概率掉落） ----
    def test_explore_level_gate_stamina_and_item(self):
        p = self.create()
        self.call('创建宗门 铁剑门')
        self.state()['level'] = 2
        self.assertEqual(self.adv()['stamina'], 100)
        with patch('qqbot_pet.petpark.adventure.sect.random.random', return_value=0.1), \
             patch('qqbot_pet.petpark.adventure.sect.random.choice', return_value='小经验书'):
            out = self.call('宗门探索')
        self.assertIn('修为×', out)
        self.assertIn('灵材×', out)
        self.assertIn('『小经验书』', out)
        self.assertEqual(self.adv()['stamina'], 100 - 20)
        self.assertTrue(self.store.has_item(p, '小经验书', 1))
        # 每日上限
        self.call('宗门探索')
        self.assertIn('明日再来', self.call('宗门探索'))

    def test_explore_no_drop(self):
        self.create()
        self.call('创建宗门 铁剑门')
        self.state()['level'] = 2
        with patch('qqbot_pet.petpark.adventure.sect.random.random', return_value=0.9):
            out = self.call('宗门探索')
        self.assertIn('帮贡+', out)
        self.assertNotIn('额外掉落', out)

    # ---- 南金库（Lv3 + 战斗胜负 + 帮贡） ----
    def _guard_result(self, won):
        return {
            "won": won, "winner": 0 if won else 1, "rounds": 3, "reason": "敌方全灭" if won else "我方倒下",
            "events": ["回合1：剑修修士出剑"],
            "units": [{"side": 0, "kind": "hero", "owner": "a", "name": "剑修修士"}],
            "metrics": {"a": {"damage": 120 if won else 40, "healing": 0, "absorbed": 0}},
        }

    def test_guard_level_gate_and_win(self):
        self.create()
        self.call('创建宗门 铁剑门')
        self.assertIn('南金库需宗门 Lv3', self.call('镇守宗门'))
        self.state()['level'] = 3
        with patch('qqbot_pet.petpark.adventure.sect._simulate_guard', return_value=self._guard_result(True)):
            out = self.call('镇守宗门')
        self.assertIn('镇守成功', out)
        self.assertIn('帮贡+', out)
        self.assertEqual(self.adv()['stamina'], 100 - 15)
        self.assertIn('镇守成功', out)
        s = self.state()
        self.assertEqual(s['daily']['a']['guard'], 1)
        self.assertIn('已完成', self.call('镇守宗门'))

    def test_guard_loss_bumps_daily(self):
        self.create()
        self.call('创建宗门 铁剑门')
        self.state()['level'] = 3
        with patch('qqbot_pet.petpark.adventure.sect._simulate_guard', return_value=self._guard_result(False)):
            out = self.call('镇守宗门')
        self.assertIn('镇守失利', out)
        self.assertEqual(self.state()['daily']['a']['guard'], 1)
        self.assertIn('已完成', self.call('镇守宗门'))

    # ---- 西仓库兑换 / 星辰阁 / 宗门升级 / 捐献 ----
    def test_exchange_and_shortfall(self):
        self.create()
        self.call('创建宗门 铁剑门')
        self.state()['members']['a']['contribution'] = 10
        self.assertIn('需要 30 帮贡', self.call('宗门兑换 体力丹'))
        self.state()['members']['a']['contribution'] = 50
        p = self.store.get_player('a', 'g')
        self.assertIn('『体力丹』', self.call('宗门兑换 体力丹'))
        self.assertTrue(self.store.has_item(p, '体力丹', 1))
        self.assertEqual(self.state()['members']['a']['contribution'], 20)

    def test_star_gate_and_buff(self):
        self.create()
        self.call('创建宗门 铁剑门')
        self.assertIn('星辰阁需宗门 Lv4', self.call('星辰阁'))
        self.state()['level'] = 4
        self.state()['treasury'] = 500
        self.assertIn('修为翻倍', self.call('星辰阁 星盘大阵'))
        self.assertGreater(self.adv()['exp_buff_until'], self.now)
        self.assertLessEqual(self.adv()['exp_buff_until'], self.now + 3 * 86400 + 60)
        self.assertEqual(self.state()['treasury'], 200)
        self.state()['treasury'] = 10
        self.assertIn('帮贡不足', self.call('星辰阁 星盘大阵'))

    def test_upgrade_cost_and_cap(self):
        self.create()
        self.call('创建宗门 铁剑门')
        self.state()['treasury'] = 2000
        self.assertIn('升至 Lv2', self.call('宗门升级'))
        self.assertEqual(self.state()['level'], 2)
        self.assertEqual(self.state()['treasury'], 2000 - 1500)
        self.state()['level'] = SEC_MAX_LEVEL
        self.assertIn('最高', self.call('宗门升级'))

    def test_donate(self):
        self.create()
        self.call('创建宗门 铁剑门')
        self.adv()['ore'] = 20
        self.assertIn('帮贡 +', self.call('宗门捐献 5'))
        self.assertEqual(self.adv()['ore'], 15)
        self.assertEqual(self.state()['members']['a']['contribution'], 50)
        self.assertEqual(self.state()['treasury'], 25)
        # 灵材不足拒绝（捐献到 0 才触发守卫）
        self.adv()['ore'] = 0
        self.assertIn('灵材不足', self.call('宗门捐献 10'))
