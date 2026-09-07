import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'petbot_framework'/'compat'))
from qqbot_pet.main import PetParkPlugin, KNOWN_COMMANDS
from qqbot_pet.petpark.admin_reset import COMMANDS
from qqbot_pet.petpark.store import PetStore


class ResetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=PetStore(Path(self.tmp.name)/'data.json')
        self.plugin=PetParkPlugin.__new__(PetParkPlugin)
        self.plugin.store=self.store;self.plugin.admins={'root'}
        self.plugin._active_event_commands=lambda:set()
        for group in ('g','g2'):
            self.store.get_player('a',group)['coin']=123
            self.store.get_player('b',group)['coin']=456
        self.store._flush()

    def call(self,text,actor='root',group='g'):
        event=SimpleNamespace(get_sender_id=lambda:actor)
        return self.plugin.dispatch(event,actor,group,text)

    def token(self):
        self.assertIn('2 名用户',self.call('清空本群用户数据'))
        return self.plugin._group_reset_pending[('g','root')]['code']

    def test_permission_private_expiry_and_actor_scope(self):
        self.assertTrue(COMMANDS<=KNOWN_COMMANDS)
        self.assertIn('仅配置白名单',self.call('清空本群用户数据',actor='staff'))
        self.assertIn('QQ群内',self.call('清空本群用户数据',group='private'))
        code=self.token();self.plugin.admins.add('other')
        self.assertIn('无效',self.call('确认清空本群用户数据 '+code,actor='other'))
        self.assertIn('无效',self.call('确认清空本群用户数据 '+code,group='g2'))
        with patch('qqbot_pet.petpark.admin_reset.time.time',return_value=10**12):
            self.assertIn('过期',self.call('确认清空本群用户数据 '+code))
        self.assertEqual(len(self.store.all_players()),4)

    def test_scoped_reset_backup_and_replay(self):
        data=self.store._data
        data['bank_players']={'g\x1fa':{'balance':9},'g2\x1fa':{'balance':8}}
        data['homestead_players']={'a':{'level':7},'hom\x1fg\x1fa':{'level':8}}
        data['qq_bindings']={'a':'123'}
        data['adventure_world']={'teams':{'t':{'group':'g','members':['g\x1fa']}},
            'duels':{'g2\x1fa':{'challenger':'g\x1fa'}},
            'bosses':{'official:today':{'hp':77,'contributions':{'g\x1fa':9,'g2\x1fa':8},'claimed':['g\x1fa']}}}
        data['adventure_receipts']={'g:a:1':'old','g2:a:1':'other'}
        data['tomb_active_sessions']={'g\x1fa':{'floor':3},'g2\x1fa':{'floor':2}}
        data['tomb_players']={'a':{'mingbi':9},'b':{'mingbi':4}}
        data['ms_players']={'a':{'level':6},'b':{'level':2}}
        self.plugin._tomb_sessions=copy.deepcopy(data['tomb_active_sessions'])
        other=copy.deepcopy(self.store.get_player('a','g2'))
        code=self.token()
        self.assertIn('已清空本群 2',self.call('确认清空本群用户数据 '+code))
        self.assertIsNone(self.store.get_player('a','g',create=False))
        self.assertEqual(self.store.get_player('a','g2'),other)
        self.assertEqual(data['bank_players'],{'g2\x1fa':{'balance':8}})
        self.assertEqual(data['homestead_players'],{'a':{'level':7}})
        # 摸金(全局按QQ)与行进中摸金快照跨群共享，本群重置不清理。
        self.assertEqual(data['tomb_active_sessions'],{'g\x1fa':{'floor':3},'g2\x1fa':{'floor':2}})
        self.assertEqual(self.plugin._tomb_sessions,{'g\x1fa':{'floor':3},'g2\x1fa':{'floor':2}})
        self.assertEqual(data['tomb_players'],{'a':{'mingbi':9},'b':{'mingbi':4}})
        self.assertEqual(data['ms_players'],{'a':{'level':6},'b':{'level':2}})
        self.assertEqual(data['qq_bindings'],{'a':'123'})
        self.assertEqual(data['adventure_world']['teams'],{})
        self.assertEqual(data['adventure_world']['duels'],{})
        self.assertEqual(data['adventure_receipts'],{'g2:a:1':'other'})
        self.assertEqual(data['adventure_world']['bosses']['official:today']['hp'],77)
        backup=next((self.store.path.parent/'group_reset_backups').glob('*.json'))
        self.assertEqual(len(json.loads(backup.read_text(encoding='utf-8'))['players']),4)
        self.assertEqual(len(PetStore(self.store.path).all_players()),2)
        self.assertIn('无效',self.call('确认清空本群用户数据 '+code))

    def test_failed_write_rollback_and_new_member_repreview(self):
        code=self.token();before=copy.deepcopy(self.store._data)
        with patch.object(self.store,'_flush',side_effect=OSError('disk')):
            self.assertIn('未完成',self.call('确认清空本群用户数据 '+code))
        self.assertEqual(self.store._data,before)
        self.assertEqual(len(PetStore(self.store.path).all_players()),4)
        self.store.get_player('new','g')
        self.assertIn('已变化',self.call('确认清空本群用户数据 '+code))
        self.assertEqual(len(self.store.all_players()),5)

    def test_cancel_and_group_alias(self):
        code=self.token();self.call('取消清空本群用户数据')
        self.assertIn('无效',self.call('确认清空本群用户数据 '+code))
        self.store.set_group_map('alias','g')
        self.assertIn('2 名用户',self.call('清空本群用户数据',group='alias'))
        code=self.plugin._group_reset_pending[('g','root')]['code']
        self.assertIn('已清空本群 2',self.call('确认清空本群用户数据 '+code,group='alias'))
        self.assertEqual(len(self.store.all_players()),2)

    def test_homestead_group_isolated_and_tomb_global_kept(self):
        # 无限服：家园按群隔离（hom\x1f<群>\x1f<QQ>），本群重置只清本群那份，不跨群共享。
        self.store.get_group('g')['server_type']='infinite'
        self.store.get_group('g2')['server_type']='infinite'
        data=self.store._data
        data['homestead_players']={
            'hom\x1fg\x1fa':{'level':3},
            'hom\x1fg2\x1fa':{'level':9},
            'a':{'level':5},           # 官方共享键（跨群共享），本群重置不应清它
        }
        data['tomb_active_sessions']={'g\x1fa':{'floor':3},'g2\x1fa':{'floor':4}}
        data['tomb_players']={'a':{'mingbi':7},'b':{'mingbi':1}}
        self.plugin._tomb_sessions=copy.deepcopy(data['tomb_active_sessions'])
        code=self.token()
        self.assertIn('已清空本群 2',self.call('确认清空本群用户数据 '+code))
        # 家园：本群隔离键被清空，他群隔离键与官方共享键保留 → 群隔离且不共享。
        self.assertEqual(data['homestead_players'],{'hom\x1fg2\x1fa':{'level':9},'a':{'level':5}})
        # 摸金：全局财富与行进中快照均为按QQ共享，本群分组不清理。
        self.assertEqual(data['tomb_active_sessions'],{'g\x1fa':{'floor':3},'g2\x1fa':{'floor':4}})
        self.assertEqual(self.plugin._tomb_sessions,{'g\x1fa':{'floor':3},'g2\x1fa':{'floor':4}})
        self.assertEqual(data['tomb_players'],{'a':{'mingbi':7},'b':{'mingbi':1}})
        self.assertIsNone(self.store.get_player('a','g',create=False))

    def test_set_server_type_registered_and_flips(self):
        """设为无限服/设为官方服/宠物解锁/宠物锁定 必须注册进 KNOWN_COMMANDS，
        且「设为无限服」经真实 dispatch 分发应真正翻转群 server_type（回归：
        此四指令此前只存在于 dispatch 分支，未进 KNOWN_COMMANDS，被过滤层 return None 吞掉，看似无效果）。"""
        for c in ('设为无限服', '设为官方服', '宠物解锁', '宠物锁定'):
            self.assertIn(c, KNOWN_COMMANDS, f'{c} 未注册进 KNOWN_COMMANDS，群聊分发会被吞掉')
        self.store.get_group('g')
        self.assertEqual(self.store.get_group('g').get('server_type'), 'official')
        self.assertIn('已设为无限服', self.call('设为无限服'))
        self.assertEqual(self.store.get_group('g').get('server_type'), 'infinite')
        self.assertIn('已设为官方服', self.call('设为官方服'))
        self.assertEqual(self.store.get_group('g').get('server_type'), 'official')
