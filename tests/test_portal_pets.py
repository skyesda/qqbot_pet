"""玩家中心灵宠列表：按槽位（群+用户ID = 一位修士）实时展开其名下全部宠物。

绑定语义是修士级（`account["bound_slots"]`），账号的 `bound_pets` 只是绑定当时
那一只的快照，展示层不得以它为准。
"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / 'petbot_framework/compat'))
from petpark.portal import PlayerPortal, _COOKIE_NAME
from petpark.store import PetStore


class Request:
    def __init__(self, token):
        self.cookies = {_COOKIE_NAME: token}
        self.headers = {}


def pet(name, species='妖冥', level=10, quality='普通'):
    return {'pet_id': name, 'nickname': name, 'species': species,
            'level': level, 'quality': quality}


class PortalPetListTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = PetStore(Path(temp.name) / 'store.json')
        self.player = self.store.get_player('user', 'group')
        self.player['pets'] = [pet('k帝', level=67, quality='超脱'), pet('霸王螺'), pet('佑碧', level=5)]
        self.player['active_pet'] = 2
        self.account = self.store.create_account('user', 'unused', 'unused')
        self.store.bind_slot_to_account(self.account['id'], 'group', 'user')
        self.portal = PlayerPortal(self.store)
        self.token = self.portal._sign({
            'aid': self.account['id'], 'csrf': 'test-csrf',
            'exp': int(time.time()) + 300,
        })

    async def me(self):
        resp = await self.portal._api_me(Request(self.token))
        return json.loads(resp.text)

    async def test_lists_every_pet_of_the_cultivator_not_just_the_bound_one(self):
        result = await self.me()
        self.assertTrue(result['ok'])
        self.assertEqual([p['nickname'] for p in result['pets']], ['k帝', '霸王螺', '佑碧'])
        self.assertEqual([p['pet_index'] for p in result['pets']], [0, 1, 2])
        self.assertEqual(result['pets'][0]['level'], 67)
        self.assertEqual(result['pets'][0]['quality'], '超脱')
        self.assertTrue(all(p['group_id'] == 'group' and p['qq'] == 'user' for p in result['pets']))
        # 槽位内联的 pets 与顶层一致，且 pet_count 等于真实只数
        self.assertEqual(result['slots'][0]['pets'], result['pets'])
        self.assertEqual(result['slots'][0]['pet_count'], 3)
        # 出战位按 active_pet 标注（此处为第 3 只）
        self.assertEqual(result['slots'][0]['active_pet'], 2)
        self.assertEqual([bool(p['active']) for p in result['pets']], [False, False, True])
        # 旧字段名保留为别名，兜住仍开着旧页面的标签页
        self.assertEqual(result['bound_pets'], result['pets'])

    async def test_bound_pets_snapshot_is_only_one_entry_and_does_not_limit_the_list(self):
        # 存档里的 bound_pets 每个槽位最多一条，且是绑定当时的旧名/旧索引
        stored = self.account['bound_pets']
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]['nickname'], 'k帝')
        self.account['bound_pets'] = [{
            'group': 'group', 'qq': 'user', 'pet_index': 0,
            'nickname': 'Skye', 'species': '妖冥',
        }]
        result = await self.me()
        self.assertEqual([p['nickname'] for p in result['pets']], ['k帝', '霸王螺', '佑碧'])
        self.assertNotIn('Skye', [p['nickname'] for p in result['pets']])

    async def test_slot_without_pets_yields_empty_list(self):
        self.player['pets'] = []
        self.player['active_pet'] = -1
        result = await self.me()
        self.assertEqual(result['pets'], [])
        self.assertEqual(result['slots'][0]['pets'], [])
        self.assertEqual(result['slots'][0]['pet_count'], 0)
        self.assertEqual(result['slots'][0]['active_pet'], -1)

    async def test_unbound_slot_data_is_not_exposed(self):
        self.store.get_player('other', 'group')['pets'] = [pet('别人的宠')]
        result = await self.me()
        self.assertEqual([p['nickname'] for p in result['pets']], ['k帝', '霸王螺', '佑碧'])

    async def test_slot_carries_cultivator_identity_for_the_chat_page(self):
        # 「网页游玩」按角色（槽位）建列表：用修士道号当名字、修士立绘当头像
        from petpark.adventure.service import AdventureService
        AdventureService(self.store).handle('group', 'user', ['踏入仙途', '灵修'])
        result = await self.me()
        adventure = result['slots'][0]['adventure']
        self.assertTrue(adventure['name'])
        self.assertTrue(adventure['portrait_url'])


if __name__ == '__main__':
    unittest.main()
