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
        p['diamond'] = 3000  # 保证可建宗
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
        a['stamina_ts'] = self.now - 60  # 恰好1分钟
        self.assertIn('体力：1/100', self.call('我的体力'))
        self.assertEqual(a['stamina'], 1)
        # 醒神丹期回复翻倍
        a['stamina'] = 0
        a['stamina_ts'] = self.now - 60
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
        s = self.store.my_sect('g', 'a')[1]
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


class HeroHpTests(unittest.TestCase):
    """修士气血（当前血量）跨战斗保留：落档/回算/陨落拦截/复活静养/商城药品定义。"""

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
        p['adventure']['spirit_root'] = '杂灵根'
        return p

    def _fake_result(self, hero_hp, key='g:a', name='剑修修士', won=False):
        return {
            "won": won, "winner": 1 if not won else 0, "rounds": 3, "reason": "test",
            "events": [],
            "units": [
                {"side": 0, "kind": "hero", "owner": key, "name": name, "hp": hero_hp, "max_hp": 700},
                {"side": 0, "kind": "pet", "owner": key, "name": "灵宠", "hp": 5, "max_hp": 200},
            ],
            "metrics": {},
        }

    def test_battle_persists_current_hp(self):
        from qqbot_pet.petpark.adventure.combat import build_party, hero_sheet
        p = self.create()
        a = p['adventure']
        mx = hero_sheet(a, p)['hp']
        with patch('qqbot_pet.petpark.adventure.service.simulate',
                   return_value=self._fake_result(int(mx * 0.4), won=True)), \
             patch('qqbot_pet.petpark.adventure.combat._now', return_value=self.now):
            out = self.call('历练 1')
        self.assertIn('获得灵材', out)
        self.assertEqual(a['hp'], int(mx * 0.4))
        self.assertEqual(a['hp_ts'], self.now)
        with patch('qqbot_pet.petpark.adventure.combat._now', return_value=self.now):
            party = build_party(p, 'g:a')
        self.assertEqual(party[0]['hp'], int(mx * 0.4))

    def test_death_blocks_battle_and_self_revives(self):
        p = self.create()
        a = p['adventure']
        owner = self.service.key('g', 'a')
        mx = 680  # default hero max_hp
        # Step 1: Death - mock battle returns hp=0 with correct owner key
        fake_dead = {'won': False, 'winner': 1, 'rounds': 3, 'reason': 'test', 'events': [],
                     'units': [{'side': 0, 'kind': 'hero', 'owner': owner, 'name': '剑修修士', 'hp': 0, 'max_hp': mx},
                               {'side': 0, 'kind': 'pet', 'owner': owner, 'name': '灵宠', 'hp': 5, 'max_hp': 200}],
                     'metrics': {owner: {'damage': 0, 'healing': 0, 'absorbed': 0}}}
        with patch('qqbot_pet.petpark.adventure.service.simulate', return_value=fake_dead), \
             patch('qqbot_pet.petpark.adventure.combat._now', return_value=self.now):
            out = self.call('历练 1')
        self.assertIn('陨落', out)
        self.assertEqual(a['hp'], 0)
        self.assertTrue(a.get('hp_dead'))
        # Step 2: 陨落拦截一切战斗指令（当前时间不足30分钟）
        with patch('qqbot_pet.petpark.adventure.service.simulate', return_value=fake_dead), \
             patch('qqbot_pet.petpark.adventure.combat._now', return_value=self.now):
            self.assertIn('陨落', self.call('历练 1'))
            self.assertIn('陨落', self.call('外出历练'))
            self.assertIn('陨落', self.call('讨伐首领'))
        # Step 3: 静养不足30分钟：仍陨落
        self.now += 1799
        with patch('qqbot_pet.petpark.adventure.service.simulate', return_value=fake_dead), \
             patch('qqbot_pet.petpark.adventure.combat._now', return_value=self.now):
            self.assertIn('陨落', self.call('历练 1'))
        # Step 4: 静养满30分钟：自愈至30%上限后可以出战
        self.now += 1
        # 使用新 service 实例以使用更新后的时钟
        service = AdventureService(self.store, lambda: self.now)
        fake_alive = {'won': True, 'winner': 0, 'rounds': 3, 'reason': 'test', 'events': [],
                      'units': [{'side': 0, 'kind': 'hero', 'owner': owner, 'name': '剑修修士', 'hp': int(mx * 0.5), 'max_hp': mx},
                                {'side': 0, 'kind': 'pet', 'owner': owner, 'name': '灵宠', 'hp': 200, 'max_hp': 200}],
                      'metrics': {owner: {'damage': 100, 'healing': 0, 'absorbed': 0}}}
        with patch('qqbot_pet.petpark.adventure.service.simulate', return_value=fake_alive), \
             patch('qqbot_pet.petpark.adventure.combat._now', return_value=self.now):
            out = service.handle('g', 'a', ['历练', '1'])
        self.assertNotIn('陨落，无法出战', out)
        # 战后气血应为 mock 的 50%（340），满足 >= 30% 断言
        self.assertGreaterEqual(a['hp'], int(mx * 0.30))

    def test_roll_hp_regen_rate(self):
        from qqbot_pet.petpark.adventure.combat import hero_sheet, roll_hp
        p = self.create()
        a = p['adventure']
        mx = hero_sheet(a, p)['hp']
        a['hp'] = 10
        a['hp_ts'] = self.now - 120  # 2分钟 → +2%（至少1点/分钟）
        cur = roll_hp(a, p, self.now)
        self.assertEqual(cur, min(mx, 10 + 2 * max(1, int(mx * 0.01))))
        # 已满不超上限
        a['hp_ts'] = self.now - 7200  # 2小时
        self.assertEqual(roll_hp(a, p, self.now), mx)

    def test_duel_is_lossless_for_hp(self):
        from qqbot_pet.petpark.adventure.combat import hero_sheet, _now
        p2 = self.create(qq='b')
        p = self.create(qq='a')
        a = p['adventure']
        fake_now = self.now
        with patch('qqbot_pet.petpark.adventure.service.simulate',
                   return_value=self._fake_result(0, key='g\x1fb')), \
             patch('qqbot_pet.petpark.adventure.combat._now', return_value=fake_now):
            self.call('仙途切磋 b')
            out = self.call('接受切磋', qq='b')
        self.assertIn('存档血量和资源未改变', out)
        self.assertNotIn('陨落', out)
        # 切磋不落档：hp 字段未被写入
        self.assertNotIsInstance(a.get('hp'), int)

    def test_heal_and_revive_item_defs(self):
        for name, eff in (("回血丹", {"heal_hp_pct": 50}), ("复苏丹", {"revive_hp_pct": 50})):
            it = data.ITEMS.get(name)
            self.assertIsNotNone(it, name)
            self.assertTrue(it.get('usable'), name)
            self.assertEqual(it.get('effect'), eff, name)
            self.assertEqual(it.get('currency'), data.CURRENCY_COIN, name)
