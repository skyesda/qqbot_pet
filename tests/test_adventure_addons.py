"""幻世仙魔收编：修士轴新增玩法（悟性加点/体力/修为翻倍/结契喂书/道具定义）测试。

复用 test_adventure.py 的 setUp/call/create + 假时钟模式。覆盖：
- 悟性点池：突破累积、自由加点、hero_sheet 战力上升、不足拒绝。
- 修士体力：查看/惰性回算/醒神丹翻倍。（丹药与形象卡走 main.py _use_item，见性能说明）
- 修为翻倍（蓄力丸/星盘大阵等经 exp_buff_until）作用于离线修炼 rate ×2。
- 宗门练武堂被动修炼加成。
- 结契灵宠定位（经验书喂的是结契目标而非当前活跃宠物）。
- 全部新道具定义与 effect 键一致性。
"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from qqbot_pet.petpark import data
from qqbot_pet.petpark.adventure import content
from qqbot_pet.petpark.adventure.service import AdventureService
from qqbot_pet.petpark.adventure.combat import hero_sheet
from qqbot_pet.petpark.adventure.economy import companion
from qqbot_pet.petpark.store import PetStore
from qqbot_pet.petpark.pet import new_pet


class AdventureAddonTests(unittest.TestCase):
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
        p['adventure']['spirit_root'] = '杂灵根'  # 排除水灵根 cult 加速，保证数值可断言
        return p

    # ---- 悟性点池：突破累积 + 加点 + 战力上升 + 不足拒绝 ----
    def test_breakthrough_grants_insight(self):
        p = self.create()
        a = p['adventure']
        a['cultivation'] = 500
        a['level'] = 1
        out = self.call('修士突破')
        self.assertIn('悟性点+1', out)
        self.assertEqual(a['wudao'], 1)
        self.assertEqual(a['gengu'], 1)
        self.assertEqual(a['insight'], 1)

    def test_insight_alloc_and_power_up(self):
        p = self.create()
        a = p['adventure']
        a['insight'] = 10
        before = hero_sheet(a, p)
        out = self.call('悟性加点 攻 5')
        self.assertIn('已给【atk】+5', out)
        self.assertEqual(a['bonus']['atk'], 5)
        self.assertEqual(a['insight'], 5)
        after = hero_sheet(a, p)
        self.assertGreater(after['atk'], before['atk'])
        # 不足拒绝（回滚，不加点）
        a['insight'] = 2
        self.assertIn('悟性点不足', self.call('悟性加点 攻 8'))
        self.assertEqual(a['insight'], 2)
        self.assertEqual(a['bonus']['atk'], 5)

    def test_insight_help_shows_cap(self):
        p = self.create()
        a = p['adventure']
        a['realm'] = 2
        out = self.call('悟性加点')
        cap = min(content.INSIGHT_CAP_MAX, content.INSIGHT_CAP_BASE + 2 * content.INSIGHT_CAP_PER_REALM)
        self.assertIn(f'上限{cap}', out)

    def test_tribulation_success_grants_insight(self):
        p = self.create()
        a = p['adventure']
        a['level'] = 99
        a['realm'] = 0
        a['tribulation_cd'] = 0
        self.store.add_item(p, content.TRIBULATION_MATERIALS[0], 1)
        result = {
            "won": True, "winner": 0, "rounds": 5, "reason": "敌方全灭",
            "events": ["回合1：剑修修士引来天劫"],
            "units": [{"side": 0, "kind": "hero", "owner": "a", "name": "剑修修士"}],
            "metrics": {"a": {"damage": 200, "healing": 0, "absorbed": 0}},
        }
        with patch('qqbot_pet.petpark.adventure.service.simulate', return_value=result):
            out = self.call('渡劫')
        self.assertIn('渡劫成功', out)
        self.assertIn('悟性点+5', out)
        self.assertEqual(a['insight'], 5)
        self.assertEqual(a['realm'], 1)

    # ---- 体力：查看 / 惰性回算 / 醒神丹翻倍 ----
    def test_stamina_view_and_regen(self):
        p = self.create()
        a = p['adventure']
        a['stamina'] = 0
        a['stamina_ts'] = self.now - 300  # 恰好5分钟
        self.assertIn('体力：1/100', self.call('我的体力'))
        self.assertEqual(a['stamina'], 1)
        # 醒神丹期回复翻倍
        a['stamina'] = 0
        a['stamina_ts'] = self.now - 300
        a['stamina_buff_until'] = self.now + 86400
        self.assertIn('体力：2/100', self.call('修士体力'))
        self.assertEqual(a['stamina'], 2)

    # ---- 修士修炼：修为翻倍 Buff + 练武堂被动 ----
    def test_train_rate_exp_buff(self):
        p = self.create()
        a = p['adventure']
        a['realm'] = 0
        # 无 buff
        a['exp_buff_until'] = 0
        a['last_train'] = self.now - 3600
        self.assertIn('修为＋120', self.call('修士修炼'))
        # 有 buff：rate ×2（2*2=4 / 分 * 60 分 = 240）
        a['last_train'] = self.now - 3600
        a['exp_buff_until'] = self.now + 86400
        self.assertIn('修为＋240', self.call('修士修炼'))

    def test_train_rate_martial_passive(self):
        p = self.create()
        a = p['adventure']
        a['realm'] = 0
        a['last_train'] = self.now - 3600
        # 建宗升级练武堂，加入被动
        self.call('创建宗门 铁剑门')
        s = self.store.sect_state('g')
        s['level'] = 4
        s['buildings']['martial'] = 4
        self.assertIn('修为＋134', self.call('修士修炼'))  # 2*1.12*60 = 134.4 → 134

    # ---- 结契灵宠定位：经验书喂的是结契目标，而非活跃宠物 ----
    def test_companion_targets_bound_pet(self):
        p = self.create()
        pet1 = new_pet('狐狸', '普通')
        pet2 = new_pet('狐狸', '普通')
        p.update(pets=[pet1, pet2], active_pet=0)
        p['adventure']['companion_pet_id'] = pet2['pet_id']
        self.assertIs(companion(p), pet2)
        self.assertIsNot(companion(p), pet1)
        p['adventure']['companion_pet_id'] = None
        self.assertIsNone(companion(p))

    # ---- 数据一致性：新道具 effect 定义与处理器键对齐 ----
    def test_new_item_defs_consistent(self):
        expected = {
            "体力丹": {"heal_stamina": 30},
            "扩体散": {"add_stamina_max": 20},
            "小经验书": {"companion_exp": 1000},
            "中经验书": {"companion_exp": 5000},
            "大经验书": {"companion_exp": 10000},
            "蓄力丸": {"exp_buff_days": 1},
            "提神丹": {"exp_buff_days": 1},
            "神龙果": {"exp_buff_days": 30},
            "醒神丹": {"stamina_buff_days": 1},
            "涤魂散": {"clear_abyss_corruption": 5},
            "形象卡": {"change_appearance": True},
            "星盘大阵": {"exp_buff_days": 3},
        }
        for name, eff in expected.items():
            it = data.ITEMS.get(name)
            self.assertIsNotNone(it, name)
            self.assertTrue(it.get('usable'), f"{name} 应可数使用")
            self.assertEqual(it.get('effect'), eff, name)
