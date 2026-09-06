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
        self.assertIn('修为＋60',self.call('修士修炼'))
        self.assertIn('锻造成功',self.call('锻造 灵剑'))
        self.assertIn('获得灵材',self.call('历练 1'))
        self.assertEqual(p['bag'],{'红药水':3})
        self.assertEqual(p['pet'],before)
        self.assertIn('先通关',self.call('历练 3'))

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
        self.assertIn('山海历练',plugin.dispatch(None,'a','g','仙途地图'))
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
        self.assertIn('先发送',self.call('修士突破'))
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

if __name__=='__main__':unittest.main()
