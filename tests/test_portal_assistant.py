"""Portal assistant edits share the game registry and respect pet ownership."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / 'petbot_framework/compat'))
from aiohttp import web
from petpark import data
from petpark.portal import PlayerPortal, _COOKIE_NAME, _CSRF_HEADER
from petpark.store import PetStore


class Request:
    def __init__(self, body, token, csrf='test-csrf'):
        self.body = body
        self.cookies = {_COOKIE_NAME: token}
        self.headers = {_CSRF_HEADER: csrf}

    async def json(self):
        return self.body


class PortalAssistantTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = PetStore(Path(temp.name) / 'store.json')
        self.player = self.store.get_player('user', 'group')
        self.player['pets'] = [
            {'pet_id': str(i), 'nickname': f'宠物{i}', 'assistant': {
                'enabled': True, 'tasks': ['打工'], 'total_runs': 7,
                'last_run_at': 123, 'log': ['原有执行记录']}}
            for i in range(2)
        ]
        self.player['pet'] = self.player['pets'][0]
        self.player['assistant']['quota'] = 100
        account = self.store.create_account('user', 'unused', 'unused')
        self.store.bind_slot_to_account(account['id'], 'group', 'user')
        self.portal = PlayerPortal(self.store)
        self.token = self.portal._sign({'aid': account['id'], 'csrf': 'test-csrf', 'exp': int(time.time()) + 300})

    async def call(self, changes, **request_options):
        body = {'group_id': 'group', 'qq': 'user', 'pet_index': 1, **changes}
        response = await self.portal._api_assistant(Request(body, self.token, **request_options))
        return json.loads(response.text)

    async def test_edit_four_tasks_only_changes_selected_pet_and_persists(self):
        before = copy.deepcopy(self.player['pets'][0])
        chosen = ['打工', '家园收取', '砸蛋十连', '修士突破']
        result = await self.call({'tasks': chosen})
        self.assertTrue(result['ok'])
        self.assertEqual(self.player['pets'][0], before)
        saved = self.player['pets'][1]['assistant']
        self.assertEqual(saved['tasks'], chosen)
        self.assertEqual((saved['enabled'], saved['total_runs'], saved['last_run_at'], saved['log']),
                         (True, 7, 123, ['原有执行记录']))
        self.assertEqual(self.store.assistant_quota(self.player), 100)
        disk = json.loads(self.store.path.read_text(encoding='utf-8'))
        self.assertEqual(disk['players'][self.store.make_key('group', 'user')]['pets'][1]['assistant']['tasks'], chosen)

    async def test_invalid_selections_and_indexes_do_not_change_state(self):
        before = copy.deepcopy(self.player)
        bad = [{'tasks': data.ASSISTANT_TASK_KEYS[:5]}, {'tasks': ['打工', '打工']},
               {'tasks': ['未知活动']}, {'tasks': '打工'}, {'tasks': [{}]},
               {'tasks': None}, {'enabled': 'false'}, {'pet_index': -1, 'tasks': ['打工']},
               {'pet_index': 2, 'tasks': ['打工']}, {'pet_index': True, 'tasks': ['打工']}]
        for changes in bad:
            with self.subTest(changes=changes):
                self.assertFalse((await self.call(changes))['ok'])
                self.assertEqual(self.player, before)

    async def test_clear_stops_assistant_and_saving_does_not_start_it(self):
        self.assertTrue((await self.call({'tasks': []}))['ok'])
        self.assertFalse(self.player['pets'][1]['assistant']['enabled'])
        self.assertTrue((await self.call({'tasks': ['探险']}))['ok'])
        self.assertFalse(self.player['pets'][1]['assistant']['enabled'])

    async def test_zero_quota_allows_edit_and_stop_but_not_start(self):
        self.player['assistant']['quota'] = 0
        self.assertTrue((await self.call({'enabled': False}))['ok'])
        self.assertTrue((await self.call({'tasks': ['学习']}))['ok'])
        self.assertFalse((await self.call({'enabled': True}))['ok'])
        self.assertFalse(self.player['pets'][1]['assistant']['enabled'])

    async def test_free_window_allows_start_without_quota(self):
        self.player['assistant']['quota'] = 0
        now = int(time.time())
        self.store._data['assistant_free'] = {'enabled': True, 'start_at': now - 60, 'end_at': now + 60}
        result = await self.call({'enabled': True})
        self.assertTrue(result['ok'])
        self.assertTrue(result['assistant']['free'])
        self.assertTrue(result['assistant']['enabled'])
        self.assertEqual(result['assistant']['quota'], 0)

    async def test_csrf_and_role_ownership_are_required(self):
        before = copy.deepcopy(self.player)
        with self.assertRaises(web.HTTPForbidden):
            await self.call({'tasks': ['学习']}, csrf='wrong')
        with self.assertRaises(web.HTTPForbidden):
            await self.call({'qq': 'another-user', 'tasks': ['学习']})
        self.assertEqual(self.player, before)

    async def test_options_follow_current_game_registry(self):
        result = await self.call({'enabled': False})
        assistant = result['assistant']
        self.assertEqual([o['key'] for o in assistant['options']], data.ASSISTANT_TASK_KEYS)
        self.assertEqual(assistant['max_tasks'], data.ASSISTANT_MAX_TASKS)


if __name__ == '__main__':
    unittest.main()
