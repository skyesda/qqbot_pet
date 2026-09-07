import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from qqbot_pet.petpark.adventure.service import AdventureService
from qqbot_pet.petpark.adventure.combat import build_party, enemies, simulate
from qqbot_pet.petpark.adventure import content
from qqbot_pet.petpark.store import PetStore
from qqbot_pet.petpark.pet import new_pet


class AdventureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'petpark.json'
        self.store=PetStore(self.path)
        self.now=1800000000
        self.service=AdventureService(self.store,lambda:self.now)

    def call(self, text, qq='a', group='g'):
        return self.service.handle(group,qq,text.split())

    def create(self, profession='剑修', qq='a',group='g'):
        self.call('踏入仙途 '+profession,qq,group)
        return self.store.get_player(qq,group)

    def test_onboarding_progress_equipment_and_legacy(self):
        p=self.store.get_player('a','g');pet=new_pet('狐狸','普通')
        p.update(pets=[pet],active_pet=0,pet=pet,bag={'红药水':3})
        before=copy.deepcopy(pet)
        self.create()
        self.assertIn('修为＋',self.call('修士修炼'))
        self.assertIn('锻造成功',self.call('锻造 灵剑'))
        self.assertIn('获得灵材',self.call('历练 1'))
        self.assertEqual(p['bag'],{'红药水':3})
        self.assertEqual(p['pet'],before)
        self.assertIn('先通关',self.call('历练 3'))

    def test_six_equipment_migration_and_forging(self):
        from qqbot_pet.petpark.adventure.combat import hero_sheet
        p = self.create()
        a = p['adventure']
        self.assertEqual(len(a['equipment']), 6)
        a.update(level=5, ore=100, equipment={'weapon': 3, 'robe': 2, 'seal': 1})
        before = hero_sheet(a, p)
        self.assertIn('铜佩 Lv0', self.call('修士装备'))
        self.assertEqual(a['equipment']['weapon'], 3)
        for name in ('灵冠', '灵靴', '玉佩'):
            self.assertIn('锻造成功', self.call('锻造 ' + name))
        after = hero_sheet(a, p)
        for stat in ('hp', 'def', 'speed'):
            self.assertGreater(after[stat], before[stat])
        self.assertEqual(a['ore'], 91)
        self.assertEqual(after['atk'], before['atk'])
        a['equipment']['boots'] = 5
        self.assertIn('当前上限', self.call('锻造 灵靴'))
        self.assertEqual(self.store.get_player('a', 'g')['adventure']['ore'], 91)

    def test_portrait_selection_and_safe_card_text(self):
        from qqbot_pet.petpark.adventure.card import portrait_name, card_html
        p = self.create()
        portraits = set()
        for profession in content.PROFESSIONS:
            for gender in ('男', '女'):
                p['adventure'].update(profession=profession, gender=gender)
                portraits.add(portrait_name(p['adventure']))
        self.assertEqual(len(portraits), 6)
        p['adventure']['name'] = '<script>坏</script>'
        with patch('qqbot_pet.petpark.adventure.card.asset_uri', return_value='data:image/png;base64,'):
            html = card_html(p, self.service.key('g', 'a'))
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>坏</script>', html)
        self.assertEqual(html.count('class="gear"'), 6)

    def test_deterministic_bounded_and_no_mutation(self):
        p=self.create(); party=build_party(p,'a'); snapshot=copy.deepcopy(party)
        foes=enemies(content.MAPS['2'])
        first=simulate(party,foes,12)
        self.assertEqual(first,simulate(party,foes,12))
        self.assertEqual(party,snapshot)
        self.assertLessEqual(first['rounds'],24)
        self.assertTrue(all(0<=u['hp']<=u['max_hp'] for u in first['units']))

    def test_reward_budget_survives_switch_reload_and_day(self):
        p=self.create();p['adventure']['level']=20
        for _ in range(9):self.call('历练 1')
        self.assertEqual(p['adventure']['rewards'],8)
        store=PetStore(self.path); service=AdventureService(store,lambda:self.now)
        self.assertIn('8次',service.handle('g','a',['历练','1']))
        self.now+=86400
        self.call('历练 1')
        self.assertEqual(self.store.get_player('a','g')['adventure']['rewards'],1)

    def test_failed_save_rolls_back(self):
        self.create();before=copy.deepcopy(self.store._data)
        with patch.object(self.store,'_flush',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.call('锻造 灵剑')
        self.assertEqual(before,self.store._data)

    def test_group_isolation_and_preparation(self):
        for q in ['a','b','c']:
            self.create(qq=q)['adventure']['level']=10
        self.call('组队秘境 葬龙秘境')
        tid=next(iter(self.service.root()['teams']))
        self.create(qq='x',group='other')['adventure']['level']=10
        self.assertIn('不在本群',self.call('加入队伍 '+tid,'x','other'))
        for q in ['b','c']:self.call('加入队伍 '+tid,q)
        self.assertIn('未准备',self.call('队伍出发'))
        for q in ['a','b','c']:self.call('准备出发',q)
        self.assertIn('先退出',self.call('修士配装 破阵'))
        # Failed commands roll back dictionary references: always resolve fresh.
        self.assertIn('只有队长',self.call('队伍出发','b'))
        result=self.call('队伍出发')
        self.assertIn('通关',result)
        self.assertIn('没有队伍',self.call('队伍出发'))
        for q in ['a','b','c']:
            self.assertEqual(self.store.get_player(q,'g')['adventure']['rewards'],1)

    def test_world_claim_once_and_infinite_isolation(self):
        p=self.create();b=self.service.world('g')
        b['hp']=0;b['contributions'][self.service.key('g','a')]=100
        self.assertIn('已到账',self.call('首领奖励'))
        self.assertIn('已领取',self.call('首领奖励'))
        self.store.get_group('infinite')['server_type']='infinite'
        self.assertNotEqual(self.service.world('infinite')['hp'],0)

    def test_duel_requires_consent_and_preserves_assets(self):
        self.create();self.create('体修','b')
        self.call('仙途切磋 b')
        self.assertFalse(self.store.get_player('b','g')['adventure']['history'])
        self.assertIn('无损论道',self.call('接受切磋','b'))
        self.assertIn('没有有效',self.call('接受切磋','b'))

    def test_restart_prepared_team_and_deep(self):
        p=self.create();p['adventure']['level']=10
        self.call('组队秘境');self.call('准备出发')
        store=PetStore(self.path)
        service=AdventureService(store,lambda:self.now)
        self.assertIn('通关',service.handle('g','a',['队伍出发']))
        self.assertIn('深渊',service.handle('g','a',['仙途深渊']))

    def test_request_receipt_survives_restart(self):
        self.create()
        result=self.service.handle('g','a',['历练','1'],request_id='qq-message-1')
        store=PetStore(self.path)
        service=AdventureService(store,lambda:self.now)
        self.assertEqual(result,service.handle('g','a',['历练','1'],request_id='qq-message-1'))
        self.assertEqual(store.get_player('a','g')['adventure']['rewards'],1)

    def test_strategy_changes_hard_boss_outcome(self):
        p=self.create('体修');a=p['adventure'];a['level']=3
        a['gender']='女'  # 固定性别：男修+5%攻会让均衡形态也获胜，掩盖「策略改变战局」的断言
        a['equipment']={k:1 for k in a['equipment']}
        enc=dict(content.MAPS['2']);enc['scale']*=1.65
        a['style']='均衡'
        self.assertFalse(simulate(build_party(p,'a'),enemies(enc),1)['won'])
        a['style']='破阵'
        self.assertTrue(simulate(build_party(p,'a'),enemies(enc),1)['won'])

    def test_original_pet_investment_increases_combat_attributes(self):
        p=self.create();pet=new_pet('狐狸','普通')
        p['pets']=[pet];p['adventure']['companion_pet_id']=pet['pet_id']
        original=build_party(p,'a')[1]
        pet['atk']*=100;pet['hp_max']*=100;pet['def']*=100
        grown=build_party(p,'a')[1]
        for field in ['atk','max_hp','defense']:self.assertGreater(grown[field],original[field])
        self.assertLess(grown['atk'],original['atk']*100)

    def test_real_dispatch_and_rebirth_preserve_character(self):
        import sys
        from types import SimpleNamespace
        sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'petbot_framework'/'compat'))
        from qqbot_pet.main import PetParkPlugin, KNOWN_COMMANDS
        plugin=PetParkPlugin.__new__(PetParkPlugin)
        plugin.store=self.store;plugin.zhongyuan=None
        plugin._active_event_commands=lambda:set()
        plugin._is_group_authorized=lambda g:True
        plugin._is_admin=lambda e:True
        self.assertTrue(content.COMMANDS<=KNOWN_COMMANDS)
        self.assertIn('欢迎',plugin.dispatch(None,'a','g','踏入仙途 剑修'))
        with patch.object(plugin, '_render_html_image', return_value='![仙途地图](map.png)') as render:
            self.assertEqual('![仙途地图](map.png)', plugin.dispatch(None,'a','g','仙途地图'))
            self.assertEqual(render.call_args.args[1], 'adventuremap')
        with patch.object(plugin, '_render_html_image', return_value=None):
            self.assertIn('山海历练', plugin.dispatch(None,'a','g','仙途地图'))
        p=self.store.get_player('a','g');pet=new_pet('狐狸','普通')
        pet.update(stage='渡劫',level=999,rebirth_gem=True)
        p.update(pets=[pet],active_pet=0,pet=pet)
        before=copy.deepcopy(p['adventure'])
        plugin._rebirth_review=lambda p,pet:'回顾'
        plugin._rebirth_confirm(p)
        self.assertEqual(p['adventure'],before)


    def test_starter_claim_and_creation_are_idempotent(self):
        self.assertIn('请选择职业', self.call('创建角色'))
        self.assertIn('成为', self.call('选择职业 体修'))
        self.assertIn('已与', self.call('结契灵宠 卡比兽'))
        self.assertIn('已领取', self.call('结契灵宠 九尾狐'))
        p=self.store.get_player('a','g')
        self.assertEqual(len(p['pets']),1)
        self.assertEqual(p['adventure']['companion_pet_id'],p['pets'][0]['pet_id'])

    def test_heaven_personal_gate_failure_and_restart(self):
        self.create();self.create(qq='b')
        self.assertIn('需要Lv10',self.call('洞天突破'))
        a=self.store.get_player('a','g')['adventure']
        a.update(level=10,cultivation=1000)
        before=copy.deepcopy(self.store.get_player('a','g')['adventure'])
        with patch('qqbot_pet.petpark.adventure.service.simulate',return_value=dict(won=False,winner=1,rounds=1,reason='test',events=[],units=[],metrics={})):
            self.assertIn('突破失败',self.call('洞天突破'))
        a=self.store.get_player('a','g')['adventure']
        for field in ('heaven','cultivation','ore','level'):
            self.assertEqual(a[field],before[field])
        a['equipment']={k:10 for k in a['equipment']}
        self.call('灵宠专长 辅助')
        self.assertIn('突破成功',self.call('洞天突破'))
        self.assertEqual(self.store.get_player('b','g')['adventure']['heaven'],0)
        self.assertIn('Lv11',self.call('修士突破'))
        self.assertEqual(PetStore(self.path).get_player('a','g')['adventure']['heaven'],1)
        low=self.service.encounter(content.MAPS['1'],0)
        high=self.service.encounter(content.MAPS['1'],1)
        self.assertGreater(high['scale'],low['scale'])

    def test_team_tier_lock_and_reward(self):
        a=self.create()['adventure'];b=self.create(qq='b')['adventure']
        a.update(level=20,heaven=2);b.update(level=20,heaven=0)
        self.call('组队秘境 葬龙秘境')
        tid,team=self.service.team_for(self.service.key('g','a'))
        self.assertIn('尚未解锁',self.call('加入队伍 '+tid,qq='b'))
        self.assertEqual(team['tier'],2)
        a=self.store.get_player('a','g')['adventure']
        before=a['ore'];self.service.reward(a,3,tier=0)
        low=a['ore']-before;before=a['ore'];self.service.reward(a,3,tier=2)
        self.assertEqual(a['ore']-before-low,4)

    def test_tuning_validation_and_existing_boss_preserved(self):
        self.create()
        tuned=AdventureService(self.store,lambda:self.now,{'adventure_enemy_percent':150,'adventure_boss_hp':2000})
        self.assertEqual(tuned.encounter(content.MAPS['1'],0)['scale'],1.5)
        self.assertEqual(tuned.world('g')['hp'],2000)
        other=AdventureService(self.store,lambda:self.now,{'adventure_enemy_percent':'bad','adventure_boss_hp':8000})
        self.assertEqual(other.enemy_percent,100)
        self.assertEqual(other.world('g')['hp'],2000)
        self.now+=86400
        self.assertEqual(other.world('g')['hp'],8000)

    def test_body_can_break_level_27_heaven_two_shield(self):
        p=self.create('体修');a=p['adventure']
        a.update(level=27,heaven=2,style='破阵')
        a['equipment']={k:13 for k in a['equipment']}
        enc=self.service.encounter(content.MAPS['14'],2)
        result=simulate(build_party(p,'a'),enemies(enc),0)
        self.assertTrue(result['won'])
        self.assertTrue(any('破阵削弱' in e for e in result['events']))

    def test_tribulation_requires_material(self):
        p=self.create();a=p['adventure'];a.update(level=99)
        self.assertIn('渡劫需',self.call('渡劫'))
        self.assertEqual(self.store.get_player('a','g')['adventure']['realm'],0)

    def test_tribulation_success_unlocks_realm_and_tactic(self):
        p=self.create();a=p['adventure']
        a.update(level=99,wudao=10,gengu=10)
        p['bag']={'筑基丹':1}
        won=dict(won=True,winner=0,rounds=2,reason='test',events=[],units=[],metrics={})
        with patch('qqbot_pet.petpark.adventure.service.simulate',return_value=won):
            result=self.call('渡劫')
        self.assertIn('筑基',result)
        self.assertIn('神通',result)
        self.assertEqual(a['realm'],1)
        self.assertIn('剑意通明',a['tactics'])
        self.assertEqual(a['wudao'],13)
        self.assertEqual(a['gengu'],13)
        self.assertNotIn('筑基丹',p['bag'])

    def test_tribulation_failure_costs_material_and_cools_down(self):
        p=self.create();a=p['adventure']
        a.update(level=99)
        p['bag']={'筑基丹':1}
        lost=dict(won=False,winner=1,rounds=2,reason='test',events=[],units=[],metrics={})
        with patch('qqbot_pet.petpark.adventure.service.simulate',return_value=lost):
            result=self.call('渡劫')
        self.assertIn('渡劫失败',result)
        self.assertEqual(a['realm'],0)
        self.assertNotIn('筑基丹',p['bag'])
        self.assertGreater(a['tribulation_cd'],self.now)
        p['bag']={'筑基丹':1}
        self.assertIn('静养',self.call('渡劫'))

    def test_level_999_cap_and_realm_gate(self):
        p=self.create();a=p['adventure']
        a.update(level=99,cultivation=999999)
        self.assertIn('渡劫',self.call('修士突破'))
        self.assertEqual(self.store.get_player('a','g')['adventure']['level'],99)
        a=self.store.get_player('a','g')['adventure']
        a.update(realm=9,level=999,cultivation=999999)
        self.assertIn('无可再进',self.call('修士突破'))
        self.assertEqual(self.store.get_player('a','g')['adventure']['level'],999)

    def test_spirit_root_and_tactic_affect_hero_sheet(self):
        from qqbot_pet.petpark.adventure.combat import hero_sheet
        p=self.create('体修');a=p['adventure']
        a.update(level=20,spirit_root='金灵根',tactics=['灵台清明'])
        base=hero_sheet(a,p)
        a['tactics']=['灵台清明','剑意通明']
        boosted=hero_sheet(a,p)
        self.assertGreater(boosted['atk'],base['atk'])
        self.assertEqual(boosted['hp'],base['hp'])

    def test_water_root_accelerates_training(self):
        p=self.create();a=p['adventure'];a.update(spirit_root='水灵根')
        self.assertIn('修为＋144',self.call('修士修炼'))

    def test_legacy_realm_four_player_can_continue(self):
        # 老玩家：化神(realm=4) Lv80（旧上限），现应可继续突破到 499 封顶
        p=self.create();a=p['adventure']
        a.update(realm=4,level=80,cultivation=1000000)
        info=self.call('我的修士')
        self.assertIn('化神',info)
        self.assertIn('499',info)
        self.assertIn('突破成功',self.call('修士突破'))
        self.assertEqual(self.store.get_player('a','g')['adventure']['level'],81)

    def test_forge_locks_at_tier_cap(self):
        p=self.create();a=p['adventure']
        a.update(level=120,ore=100000,equipment={'weapon':99,'robe':0,'seal':0,'crown':0,'boots':0,'pendant':0})
        self.assertIn('品阶巅峰',self.call('锻造 灵剑'))
        self.assertEqual(a['equipment']['weapon'],99)

    def test_equip_advance_requires_material(self):
        p=self.create();a=p['adventure']
        a.update(level=120,equipment={'weapon':99,'robe':0,'seal':0,'crown':0,'boots':0,'pendant':0})
        self.assertIn('进阶需',self.call('装备进阶 灵剑'))
        self.assertEqual(a['equip_tier']['weapon'],0)

    def test_equip_advance_success_upgrades_and_rolls_affix(self):
        p=self.create();a=p['adventure']
        a.update(level=120,equipment={'weapon':99,'robe':0,'seal':0,'crown':0,'boots':0,'pendant':0})
        p['bag']={'玄铁':1}
        won=dict(won=True,winner=0,rounds=2,reason='test',events=[],units=[],metrics={})
        with patch('qqbot_pet.petpark.adventure.service.simulate',return_value=won):
            result=self.call('装备进阶 灵剑')
        self.assertIn('进阶成功',result)
        self.assertIn('灵器',result)
        self.assertEqual(a['equip_tier']['weapon'],1)
        self.assertIn(a['equip_affix']['weapon'],content.AFFIXES)
        self.assertNotIn('玄铁',p['bag'])

    def test_equip_advance_failure_costs_material_and_cools_down(self):
        p=self.create();a=p['adventure']
        a.update(level=120,equipment={'weapon':99,'robe':0,'seal':0,'crown':0,'boots':0,'pendant':0})
        p['bag']={'玄铁':1}
        lost=dict(won=False,winner=1,rounds=2,reason='test',events=[],units=[],metrics={})
        with patch('qqbot_pet.petpark.adventure.service.simulate',return_value=lost):
            result=self.call('装备进阶 灵剑')
        self.assertIn('进阶失败',result)
        self.assertEqual(a['equip_tier']['weapon'],0)
        self.assertNotIn('玄铁',p['bag'])
        self.assertGreater(a['forge_cd'],self.now)
        p['bag']={'玄铁':1}
        self.assertIn('静养',self.call('装备进阶 灵剑'))

    def test_refine_requires_affix_and_rerolls(self):
        p=self.create();a=p['adventure']
        a.update(level=120,equipment={'weapon':99,'robe':0,'seal':0,'crown':0,'boots':0,'pendant':0})
        self.assertIn('尚无词条',self.call('洗炼 灵剑'))
        a=self.store.get_player('a','g')['adventure']  # 失败回滚后重新取引用
        a['equip_affix']['weapon']='破军'
        a['ore']=30
        self.assertIn('洗炼成功',self.call('洗炼 灵剑'))
        self.assertIn(a['equip_affix']['weapon'],content.AFFIXES)
        self.assertEqual(a['ore'],0)

    def test_affix_and_tier_mult_change_hero_sheet(self):
        from qqbot_pet.petpark.adventure.combat import hero_sheet
        p=self.create('剑修');a=p['adventure']
        a.update(level=20,equipment={'weapon':10,'robe':0,'seal':0,'crown':0,'boots':0,'pendant':0})
        base=hero_sheet(a,p)
        a['equip_tier']['weapon']=1  # 灵器系数 1.12
        self.assertGreater(hero_sheet(a,p)['atk'],base['atk'])
        a['equip_tier']['weapon']=0
        a['equip_affix']['weapon']='破军'  # 攻击 +6%
        self.assertGreater(hero_sheet(a,p)['atk'],base['atk'])

    def test_profession_gear_names_differ(self):
        from qqbot_pet.petpark.adventure.content import gear_name
        self.assertEqual(gear_name('剑修','weapon',0),'凡铁剑')
        self.assertEqual(gear_name('体修','weapon',0),'铁砂拳套')
        self.assertEqual(gear_name('灵修','weapon',0),'桃木杖')
        self.assertEqual(gear_name('剑修','weapon',9),'鸿蒙剑')
        self.assertNotEqual(gear_name('剑修','robe',2),gear_name('体修','robe',2))

    def test_legacy_equip_tier_backfill_allows_continuing(self):
        p=self.create();a=p['adventure']
        a.update(level=160,realm=1,ore=100000,equipment={'weapon':150,'robe':0,'seal':0,'crown':0,'boots':0,'pendant':0})
        a['equip_tier'].pop('weapon')  # 模拟老档缺 tier 字段
        self.call('我的修士')  # 触发 setdefault 惰性回填
        self.assertEqual(a['equip_tier']['weapon'],1)
        self.assertIn('锻造成功',self.call('锻造 灵剑'))
        self.assertEqual(a['equipment']['weapon'],151)

if __name__=='__main__':unittest.main()
