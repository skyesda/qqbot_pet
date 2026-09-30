"""Profile reads and concurrent edits must not change other player progress."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / 'petbot_framework/compat'))
from aiohttp import web
from petpark.store import PetStore
from petpark.portal import PlayerPortal
from petpark.webadmin import WebAdmin
from petpark.pet import new_pet
from petpark.adventure.service import AdventureService


class Request:
    remote = 'test'
    def __init__(self, body, authenticated=True):
        self.body = body
        self.cookies = {'pp_session': 'test'} if authenticated else {}
    async def json(self):
        return self.body


class ProfileTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = PetStore(Path(self.tmp.name) / 'store.json')
        AdventureService(self.store).handle('group', 'user', ['踏入仙途', '剑修'])
        self.player = self.store.get_player('user', 'group')
        self.player['pets'] = [new_pet('九尾狐', '普通'), new_pet('卡比兽', '稀有')]
        self.player['active_pet'] = 1
        self.key = self.store.make_key('group', 'user')
        self.admin = WebAdmin(self.store, '127.0.0.1', 0, 'test', 'unused')
        self.admin._tokens.add('test')
        self.admin._portal = PlayerPortal(self.store)
        self.store.save = AsyncMock()
    async def call(self, method, body):
        return json.loads((await getattr(self.admin, method)(Request(body))).text)
    async def test_read_all_pets_and_same_user_roles_without_mutation(self):
        other = copy.deepcopy(self.player)
        other['group'] = 'other'
        self.store._data['players'][self.store.make_key('other', 'user')] = other
        before = copy.deepcopy(self.store._data)
        result = await self.call('_api_player_profile', {'key': self.key})
        self.assertEqual(len(result['pets']), 2)
        self.assertEqual(len(result['roles']), 2)
        self.assertEqual(len(result['role']['adventure']['equipment']), 6)
        self.assertTrue(result['role']['adventure']['portrait_url'])
        self.assertEqual(before, self.store._data)
    async def test_profile_requires_admin_session(self):
        for method in ['_player_page', '_api_player_profile']:
            with self.assertRaises(web.HTTPFound):
                await getattr(self.admin, method)(Request({'key': self.key}, False))
    async def test_pet_edit_preserves_live_progress_and_new_companion(self):
        base = copy.deepcopy(self.player)
        edited = copy.deepcopy(base)
        edited['pets'][0]['nickname'] = '青岚'
        self.player['pets'][1]['exp'] = 999
        self.player['pets'].append(new_pet('九尾狐', '精品'))
        self.player['coin'] = 12345
        result = await self.call('_api_upsert', {'table': 'players', 'key': self.key,
            'base': base, 'value': edited, 'profile_edit': True, 'require_exists': True})
        self.assertTrue(result['ok'])
        saved = self.store._data['players'][self.key]
        self.assertEqual(saved['pets'][0]['nickname'], '青岚')
        self.assertEqual(saved['pets'][1]['exp'], 999)
        self.assertEqual(len(saved['pets']), 3)
        self.assertEqual(saved['coin'], 12345)
        self.assertEqual(saved['active_pet'], 1)
    async def test_removed_pet_conflicts_without_discarding_live_data(self):
        base = copy.deepcopy(self.player)
        edited = copy.deepcopy(base)
        edited['pets'][0]['nickname'] = '青岚'
        self.player['pets'].pop(0)
        before = copy.deepcopy(self.store._data)
        result = await self.call('_api_upsert', {'table': 'players', 'key': self.key,
            'base': base, 'value': edited, 'profile_edit': True})
        self.assertFalse(result['ok'])
        self.assertEqual(before, self.store._data)
    async def test_deleted_profile_cannot_be_resurrected(self):
        base = copy.deepcopy(self.player)
        del self.store._data['players'][self.key]
        result = await self.call('_api_upsert', {'table': 'players', 'key': self.key,
            'base': base, 'value': base, 'require_exists': True})
        self.assertFalse(result['ok'])
        self.assertNotIn(self.key, self.store._data['players'])
    async def test_new_profile_cannot_overwrite_existing(self):
        result = await self.call('_api_upsert', {'table': 'players', 'key': self.key,
            'value': {}, 'create_only': True})
        self.assertFalse(result['ok'])


if __name__ == '__main__':
    unittest.main()
