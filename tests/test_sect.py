"""多宗门（一个群可多门 + 2000天晶建宗 + 活跃度扩招人数上限 + 一人一宗）核心交互测试。

完全复用 test_adventure.py 的 setUp/call/create + 假时钟模式，纯 AdventureService。

注意两条硬约束：
1. 任何发宗门指令的人都必须先「踏入仙途」（_handle 在宗门 dispatch 前就要求 adventure）。
2. service.handle 遇 RuleError 会 deepcopy 回滚并替换 self.store._data，因此**不要跨回滚持有
   self.state()/get_player() 的引用**——一律在断言前重新取。

create() 已给玩家注入 3000 天晶（可建宗），多数断言用 state()（默认取角色 'a' 所在宗门）。
"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from qqbot_pet.petpark.adventure.service import AdventureService
from qqbot_pet.petpark.adventure.sect import (
    DAILY_LIMITS, SEC_MAX_LEVEL, SECT_BASE_CAP, SECT_CAP_MAX, ACTIVITY_PER_CAP, sect_cap,
)
from qqbot_pet.petpark.store import PetStore


def _fake_member(qq):
    return {"role": "帮众", "contribution": 0, "joined_at": 0}


# 与 sect.py _ACTIVITY 对齐（任务/镇守胜/镇守负）。
_MISSION_ACT = 2
_GUARD_WIN_ACT = 5
_GUARD_LOSS_ACT = 1


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
        p = self.store.get_player(qq, group)
        p['diamond'] = 3000        # 保证可建宗（创建一次扣 2000）
        return p

    def state(self, group='g', qq='a'):
        return self.store.my_sect(group, qq)[1]

    def adv(self, qq='a'):
        return self.store.get_player(qq, 'g')['adventure']

    # ---- 建宗：天晶门槛 / 扣费绑定 / 本群多门 / 一人一宗 ----
    def test_create_requires_diamond(self):
        p = self.create()
        p['diamond'] = 0
        self.assertIn('2000 天晶', self.call('创建宗门 青云门'))

    def test_create_deducts_and_binds(self):
        p = self.create()
        self.assertIn('已创立', self.call('创建宗门 青云门'))
        self.assertEqual(p['diamond'], 1000)
        self.assertEqual(self.state()['name'], '青云门')
        self.assertEqual(self.state()['founder'], 'a')
        # 已入一宗，不能再建第二门
        self.assertIn('一人只能加入一个宗门', self.call('创建宗门 别门'))

    def test_multiple_sects_same_group(self):
        self.create()
        self.create(qq='b')
        self.call('创建宗门 铁剑门')
        self.call('创建宗门 青云门', qq='b')
        self.assertEqual(len(self.store.sects_in_group('g')), 2)
        self.assertEqual({x['name'] for x in self.store.sects_in_group('g')},
                         {'青云门', '铁剑门'})
        self.assertIn('铁剑门', self.call('宗门榜'))
        self.assertIn('青云门', self.call('宗门榜'))

    def test_one_person_one_sect(self):
        self.create()
        self.create(qq='b')
        self.call('创建宗门 铁剑门')
        self.call('创建宗门 青云门', qq='b')
        # a 已在铁剑门，不能申请青云门；b 已在青云门，不能再建
        self.assertIn('一人只能加入一个宗门', self.call('申请入宗 青云门'))
        self.assertIn('一人只能加入一个宗门', self.call('创建宗门 第三门', qq='b'))

    # ---- 入宗审批与权限 ----
    def test_create_join_approve_roster(self):
        self.create()
        self.create(qq='b')
        self.assertIn('已创立', self.call('创建宗门 铁剑门'))
        self.assertEqual(self.state()['name'], '铁剑门')
        self.assertEqual(self.state()['members']['a']['role'], '帮主')
        self.assertIn('宗', self.call('查看宗门'))
        self.assertIn('已提交', self.call('申请入宗 铁剑门', qq='b'))
        self.assertIn('b', self.state()['pending'])
        self.assertIn('已同意', self.call('同意入宗 b'))
        self.assertEqual(self.state()['members']['b']['role'], '帮众')
        self.assertNotIn('b', self.state()['pending'])
        self.assertIn('【帮众】', self.call('宗门名册'))

    def test_apply_requires_name_and_unknown(self):
        self.create()
        self.create(qq='b')
        self.call('创建宗门 铁剑门')
        self.assertIn('宗名', self.call('申请入宗', qq='b'))
        self.assertIn('没有「无名门」', self.call('申请入宗 无名门', qq='b'))

    def test_membership_permissions(self):
        self.create()
        self.create(qq='b')
        self.create(qq='c')
        # 未建宗/非成员
        self.assertIn('宗名', self.call('申请入宗', qq='c'))
        self.assertIn('你加入的宗门', self.call('宗门任务', qq='c'))
        self.call('创建宗门 铁剑门')
        # 帮主不可退出
        self.assertIn('帮主不可退出', self.call('退出宗门'))
        # 申请→审批，b 成为帮众后可测权限（非帮主/长老）
        self.assertIn('已提交', self.call('申请入宗 铁剑门', qq='b'))
        self.call('同意入宗 b')
        self.assertIn('只有帮主/长老', self.call('同意入宗 x', qq='b'))
        self.assertIn('只有帮主/长老可审批申请', self.call('拒绝入宗 x', qq='b'))
        self.assertIn('只有帮主', self.call('封官 c 长老', qq='b'))
        self.assertIn('只有帮主', self.call('宗门升级', qq='b'))
        # b 退出；非成员再退拒绝
        self.assertIn('你已退出宗门', self.call('退出宗门', qq='b'))
        self.assertIn('你加入的宗门', self.call('退出宗门', qq='b'))

    def test_announce_only_boss(self):
        self.create()
        self.create(qq='b')
        self.call('创建宗门 铁剑门')
        self.call('申请入宗 铁剑门', qq='b')
        self.call('同意入宗 b')
        self.assertIn('只有帮主', self.call('宗门公告 欢迎加入', qq='b'))
        self.assertIn('已更新', self.call('宗门公告 欢迎加入'))
        self.assertEqual(self.state()['announce'], '欢迎加入')

    # ---- 人数上限：基础10 / 活跃度每40扩招1人 / 封顶20 ----
    def test_cap_formula(self):
        self.create()
        self.call('创建宗门 铁剑门')
        s = self.state()
        self.assertEqual(sect_cap(s), SECT_BASE_CAP)          # 10
        s['activity'] = ACTIVITY_PER_CAP                      # 40 → +1
        self.assertEqual(sect_cap(s), SECT_BASE_CAP + 1)
        s['activity'] = 400                                   # +10 → 封顶 20
        self.assertEqual(sect_cap(s), SECT_CAP_MAX)

    def test_full_sect_rejects_apply(self):
        self.create()
        self.create(qq='b')
        self.call('创建宗门 铁剑门')
        s = self.state()
        for i in range(9):                                    # a + 9 = 10 满员
            s['members'][f'x{i}'] = _fake_member(f'x{i}')
        self.assertIn('人数已满', self.call('申请入宗 铁剑门', qq='b'))
        # 活跃度提升后可扩招（40 点 → 上限11）。须在满员回滚后重取，否则引用已失效。
        s = self.state()
        s['activity'] = ACTIVITY_PER_CAP
        self.assertIn('已提交', self.call('申请入宗 铁剑门', qq='b'))

    # ---- 宗门任务：需完成指定目标才发奖励（非一键白拿） + 冷却 + 每日上限 + 跨天重置 ----
    def test_mission_requires_completed_objective_then_rewards(self):
        self.create()
        self.create(qq='b')
        self.call('创建宗门 铁剑门')
        self.call('申请入宗 铁剑门', qq='b')
        self.call('同意入宗 b')
        # 接取：a 今日无任何活动 → 随机二选一（打怪×2 / 通关地图×1）；这里强制抽「打怪（历练/副本）×2」。
        with patch('qqbot_pet.petpark.adventure.sect.random.choice',
                   return_value=('rewards', '打怪（历练/副本）', 0, 2)):
            out = self.call('宗门任务')
        self.assertIn('接取宗门任务', out)
        self.assertIn('打怪', out)
        # 未达标时归还被拒；达标后一次性领赏，且不重复发。
        self.assertIn('任务进行中', self.call('宗门任务'))
        self.assertEqual(self.state()['members']['a']['mission']['target'], 2)
        self.adv()['rewards'] = 2   # 模拟已完成 2 次历练
        self.assertIn('任务达成', self.call('宗门任务'))
        s = self.state()
        # 帮贡分润 50% 入库；个人帮贡累计；活跃度 +2；任务已清除。
        self.assertEqual(s['treasury'], 15)
        self.assertEqual(s['members']['a']['contribution'], 30)
        self.assertEqual(s['activity'], _MISSION_ACT)
        self.assertNotIn('mission', s['members']['a'])
        self.assertEqual(int(self.adv()['stamina']), 90)   # 接取消耗 10 体力

    def test_mission_daily_cap_and_rollover(self):
        self.create()
        self.call('创建宗门 铁剑门')
        s = self.state()
        s['daily_date'] = self.service.today()
        s['daily'] = {'a': {'mission': DAILY_LIMITS['mission']}}
        self.assertIn('已完成', self.call('宗门任务'))
        # 跨天重置后任务计数清零，可再接。
        self.now += 86400
        self.assertIn('接取宗门任务', self.call('宗门任务'))

    def test_mission_stamina_shortfall_rolls_back(self):
        p = self.create()
        self.call('创建宗门 铁剑门')
        self.adv()['stamina'] = 5
        out = self.call('宗门任务')
        self.assertIn('体力不足', out)
        self.assertEqual(int(self.adv()['stamina']), 5)
        self.assertEqual(int(self.adv()['cultivation']), int(p['adventure']['cultivation']))

    def test_mission_claim_cooldown(self):
        self.create()
        self.call('创建宗门 铁剑门')
        # 接取（base=0）→ 完成后领赏 → 纳入短冷却，不能秒建新委托。
        # 宗门任务随机二选一（打怪×2 / 通关地图×1），把两路计数都补齐，确保无论抽到哪路都能达成。
        self.assertIn('接取', self.call('宗门任务'))
        self.adv()['rewards'] = 2
        self.adv().setdefault('trial', {})['used'] = 1
        self.assertIn('任务达成', self.call('宗门任务'))
        self.assertIn('冷却中', self.call('宗门任务'))
        self.now += 60
        self.assertIn('接取', self.call('宗门任务'))

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
        # 冷却（300s）先拦截；重置后再探索一次达 2 次，第 3 次触发每日上限。
        self.assertIn('冷却中', self.call('宗门探索'))
        self.now += 300
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

    # ---- 南金库（Lv3 + 战斗胜负 + 帮贡 + 活跃度） ----
    def _guard_result(self, won):
        return {
            "won": won, "winner": 0 if won else 1, "rounds": 3,
            "reason": "敌方全灭" if won else "我方倒下",
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
        s = self.state()
        self.assertEqual(s['daily']['a']['guard'], 1)
        self.assertEqual(s['activity'], _GUARD_WIN_ACT)
        self.assertIn('已完成', self.call('镇守宗门'))

    def test_guard_loss_bumps_daily(self):
        self.create()
        self.call('创建宗门 铁剑门')
        self.state()['level'] = 3
        with patch('qqbot_pet.petpark.adventure.sect._simulate_guard', return_value=self._guard_result(False)):
            out = self.call('镇守宗门')
        self.assertIn('镇守失利', out)
        self.assertEqual(self.state()['daily']['a']['guard'], 1)
        self.assertEqual(self.state()['activity'], _GUARD_LOSS_ACT)
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

    def test_sect_action_cooldowns(self):
        self.create()
        self.call('创建宗门 铁剑门')
        self.state()['level'] = 2
        # 北秘境：成功后进入 5 分钟冷却，期间不能连点。
        with patch('qqbot_pet.petpark.adventure.sect.random.random', return_value=0.9):
            self.assertIn('帮贡+', self.call('宗门探索'))
        self.assertIn('冷却中', self.call('宗门探索'))
        self.now += 300
        with patch('qqbot_pet.petpark.adventure.sect.random.random', return_value=0.9):
            self.assertIn('帮贡+', self.call('宗门探索'))     # 冷却重置后成功（今日第2次）
        self.assertIn('明日再来', self.call('宗门探索'))        # 第3次 → 每日上限

    def test_star_cooldown(self):
        self.create()
        self.call('创建宗门 铁剑门')
        self.state()['level'] = 4
        self.state()['treasury'] = 500
        self.assertIn('修为翻倍', self.call('星辰阁 星盘大阵'))
        self.assertIn('冷却中', self.call('星辰阁 灵材'))    # 星辰阁共用冷却

    def test_exchange_catalog_no_progression_items(self):
        self.create()
        self.call('创建宗门 铁剑门')
        out = self.call('宗门兑换')
        self.assertIn('| 物品 | 帮贡 | 效果 |', out)
        self.assertIn('体力丹', out)
        self.assertNotIn('聚灵丹', out)        # 不再直接兑换经验/战力
        self.assertNotIn('经验书', out)

    def test_exchange_currency_and_ore(self):
        self.create()
        self.call('创建宗门 铁剑门')
        s = self.state()
        s['members']['a']['contribution'] = 200
        before_coin = self.store.get_currency(self.store.get_player('a', 'g'), '灵石')
        self.call('宗门兑换 灵石袋')
        self.assertEqual(self.store.get_currency(self.store.get_player('a', 'g'), '灵石'), before_coin + 3000)
        before_ore = int(self.adv()['ore'])
        self.call('宗门兑换 灵材包')
        self.assertEqual(int(self.adv()['ore']), before_ore + 20)
        self.assertEqual(self.state()['members']['a']['contribution'], 200 - 50 - 50)

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
        self.now += 300   # 等星辰阁冷却（300s）结束，再测帮贡不足
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
