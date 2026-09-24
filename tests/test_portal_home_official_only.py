"""官网首页只统计官方服：无限服档案与无限服成员的摸金数据都不得进首页。

口径不是首页自创，而是把既有规则补到唯一漏掉的公开端点上（与群内「仙途战力榜全服」
「神榜」及摸金榜同口径，逻辑已下沉到 petpark/store.py）：

  · 有 group 的档案类榜（灵宠 / 仙途）→ **按群**剔除，玩家的官方服档案保留；
  · 摸金三榜按裸 openid 全局共享（store.tomb_state）→ 只能**按 openid** 连人一起剔。

已知局限（本文件最后一条用例固化）：摸金剔的是人不是数据，双服玩家在无限服赚的冥币
与官方服那份在同一张表里，无法拆分——这与群内 _tomb_rank 完全一致。
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
from petpark.adventure import content as advc
from petpark.pet import new_pet
from petpark.portal import PlayerPortal
from petpark.store import PetStore

OFF = 'g-off'      # 官方服群
INF = 'g-inf'      # 无限服群
ALIAS = 'g-ali'    # 指向无限服群的别名键（模拟历史 group_map）
FUTURE = 4102444800  # 2100-01-01，用作 auth_until


def cultivator(name, level=10):
    """照 petpark/adventure/service.py:253「踏入仙途」的初始化结构造修士。"""
    slots = list(advc.GEAR.items())
    return {
        'schema_version': advc.VERSION, 'heaven': 0, 'milestones': [],
        'name': name, 'profession': '剑修', 'level': level, 'realm': 0,
        'cultivation': 0, 'last_train': 0,
        'equipment': {slot: 0 for slot, _ in slots},
        'ore': 9,
        'equip_tier': {slot: 0 for slot, _ in slots},
        'equip_affix': {slot: None for slot, _ in slots},
        'forge_cd': 0, 'style': '均衡', 'pet_role': '攻击',
        'companion_pet_id': None, 'gender': '男',
        'bonus': {'atk': 0, 'def': 0, 'hp': 0, 'speed': 0},
        'wudao': 0, 'gengu': 0, 'name_customized': False,
        'spirit_root': advc.SPIRIT_ROOTS[0]['name'],
        'tactics': [advc.TACTICS[0]['name']],
        'tribulation_cd': 0, 'cleared': [], 'history': [], 'deep': None,
        'transfer_at': 0,
    }


class HomeOfficialOnlyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = PetStore(Path(temp.name) / 'store.json')
        self.store._data.setdefault('group_map', {})
        self.portal = PlayerPortal(self.store)

    # ---------- 造数据 ----------
    def group(self, gid, infinite=False):
        g = self.store.get_group(gid)
        g['auth_until'] = FUTURE
        g['server_type'] = 'infinite' if infinite else 'official'
        return g

    def player(self, gid, qq, *, name=None, pet_hp=0):
        pl = self.store.get_player(qq, gid)
        if name:
            pl['adventure'] = cultivator(name)
        if pet_hp:
            p = new_pet('妖冥', '普通')
            p['nickname'] = f'{qq}的宠'
            p['level'] = 10
            p['hp_max'] = pet_hp
            pl['pets'] = [p]
            pl['active_pet'] = 0
            # player["pet"] 是运行时别名、不是存档字段：不显式重建的话 pet_rank 恒空、
            # 灵宠榜相关断言会全部空转假绿。
            PetStore._restore_pet_ref(pl)
        return pl

    def tomb(self, qq, mingbi=0, today=0, yesterday=0):
        d = self.store._data.setdefault('tomb_players', {})
        day = time.strftime('%Y-%m-%d')
        prev = time.strftime('%Y-%m-%d', time.localtime(time.time() - 86400))
        d[str(qq)] = {'mingbi': mingbi, 'daily_gains': {day: today, prev: yesterday}}

    async def home(self):
        resp = await self.portal._api_home(None)
        return json.loads(resp.text)

    def names(self, data):
        return [row['name'] for row in data['cultivator_rank']]

    def pet_names(self, data):
        return [row['nickname'] for row in data['pet_rank']]

    # ---------- 用例 ----------
    async def test_infinite_group_cultivator_is_off_the_board(self):
        """按群剔除：无限服群的修士不上榜，官方服群的照常上。"""
        self.group(OFF)
        self.group(INF, infinite=True)
        self.player(OFF, 'o1', name='官方修士')
        self.player(INF, 'i1', name='无限修士')
        data = await self.home()
        self.assertTrue(data['ok'])
        self.assertIn('官方修士', self.names(data))
        self.assertNotIn('无限修士', self.names(data))

    async def test_dual_server_player_keeps_official_profile_only(self):
        """同一 openid 双服都有档案：无限服那份被剔，官方服那份仍在榜。"""
        self.group(OFF)
        self.group(INF, infinite=True)
        self.player(OFF, 'd1', name='双服官方档案', pet_hp=5000)
        self.player(INF, 'd1', name='双服无限档案', pet_hp=99999999)
        data = await self.home()
        self.assertEqual(self.names(data), ['双服官方档案'])
        self.assertEqual(self.pet_names(data), ['d1的宠'])
        self.assertEqual(data['pet_rank'][0]['power'] > 0, True)

    async def test_infinite_profiles_excluded_from_stats(self):
        """无限服档案不计入官方服玩家数 / 已领灵宠数。"""
        self.group(OFF)
        self.group(INF, infinite=True)
        self.player(OFF, 'o1', name='官方修士', pet_hp=100)
        self.player(OFF, 'o2', name='官方修士二', pet_hp=200)
        self.player(INF, 'i1', name='无限修士', pet_hp=300)
        self.player(INF, 'd1', name='双服无限档案', pet_hp=400)
        data = await self.home()
        self.assertEqual(data['stats']['players'], 2)
        self.assertEqual(data['stats']['pets'], 2)

    async def test_tomb_boards_exclude_infinite_members_by_openid(self):
        """摸金按 openid 剔人：无限服成员（含双服玩家）三榜皆无，纯官方服玩家保留。"""
        self.group(OFF)
        self.group(INF, infinite=True)
        self.player(OFF, 'o1', name='官方修士')
        self.player(INF, 'i1', name='无限修士')
        self.player(OFF, 'd1', name='双服官方档案')
        self.player(INF, 'd1', name='双服无限档案')   # 双服：无限服也有一份档案
        self.tomb('o1', mingbi=100, today=10, yesterday=1)
        self.tomb('i1', mingbi=999, today=99, yesterday=9)
        self.tomb('d1', mingbi=500, today=50, yesterday=5)
        data = await self.home()
        for board in ('tomb_rank', 'tomb_today', 'tomb_yesterday'):
            # 三张榜分别取 冥币总量 / 今日 / 昨日，值不同，故按「榜上是谁」断言
            self.assertEqual([row['qq'] for row in data[board]], ['o1'], board)
        self.assertEqual([row['value'] for row in data['tomb_rank']], [100])
        self.assertEqual([row['value'] for row in data['tomb_today']], [10])
        self.assertEqual([row['value'] for row in data['tomb_yesterday']], [1])
        self.assertEqual(data['stats']['tomb_players'], 1)

    async def test_no_infinite_group_leaves_every_number_unchanged(self):
        """对照组：没有无限服群时所有数字与改动前一致（非无限服数据零影响）。"""
        self.group(OFF)
        self.player(OFF, 'o1', name='官方修士', pet_hp=100)
        self.player(OFF, 'o2', name='官方修士二', pet_hp=200)
        self.tomb('o1', mingbi=100)
        self.tomb('o2', mingbi=200)
        data = await self.home()
        self.assertEqual(self.store.infinite_group_ids(), set())
        self.assertEqual(self.store.infinite_member_qqs(), set())
        self.assertEqual(data['stats']['players'], 2)
        self.assertEqual(data['stats']['pets'], 2)
        self.assertEqual(data['stats']['tomb_players'], 2)
        self.assertEqual(len(data['tomb_rank']), 2)
        self.assertEqual(sorted(self.names(data)), ['官方修士', '官方修士二'])

    async def test_auth_groups_counts_infinite_groups_too(self):
        """授权群数统计的是机器人已授权群，不是官方服玩家数据 → 无限服群照计。"""
        self.group(OFF)
        self.group(INF, infinite=True)
        data = await self.home()
        self.assertEqual(data['stats']['auth_groups'], 2)

    async def test_second_call_within_window_serves_the_cached_payload(self):
        """30 秒缓存契约不被新逻辑绕过：窗口内不重算。"""
        self.group(OFF)
        self.player(OFF, 'o1', name='官方修士')
        first = await self.home()
        self.player(OFF, 'o2', name='后加的修士')   # 窗口内新增，不应出现
        second = await self.home()
        self.assertEqual(second['stats']['players'], first['stats']['players'])
        self.assertEqual(self.names(second), ['官方修士'])
        self.portal._home_cache = None              # 过期后应看到
        self.assertEqual(set(self.names(await self.home())), {'官方修士', '后加的修士'})

    async def test_alias_key_uses_the_canonical_groups_server_type(self):
        """别名键自身的 server_type 不作数：以它 resolve 到的规范群为准。

        锁住 infinite_group_ids 的「先 resolve 再读规范群」选择——若日后有人改回
        「读原始键的 server_type」，别名下的玩家就会漏进首页，本用例会红。
        """
        self.group(OFF)
        self.group(INF, infinite=True)
        self.group(ALIAS)                      # 别名键自己记的是官方服
        self.store._data['group_map'][ALIAS] = INF
        self.player(ALIAS, 'a1', name='别名下的修士')
        self.player(OFF, 'o1', name='官方修士')
        data = await self.home()
        self.assertEqual(self.store.infinite_group_ids(), {INF})
        self.assertIn('a1', self.store.infinite_member_qqs())
        self.assertNotIn('别名下的修士', self.names(data))
        self.assertEqual(self.names(data), ['官方修士'])

    async def test_a_stale_alias_claiming_infinite_cannot_infect_the_canonical(self):
        """反向：别名键自称无限服、但它指向的规范群是官方服 → 以规范群为准。

        这条与上一条把「规范键权威」两个方向都钉住。若日后有人改回「读原始键的
        server_type」，这里就会把一整批官方服玩家误判成无限服、整群剔出首页（本用例变红）。
        """
        self.group(OFF)
        self.group(INF, infinite=True)
        self.group(ALIAS)
        self.store._data['groups'][ALIAS]['server_type'] = 'infinite'  # 别名键自称无限服
        self.store._data['group_map'][ALIAS] = OFF                     # 但它归属官方服群
        self.player(ALIAS, 'a1', name='别名下的修士')
        self.player(OFF, 'o1', name='官方修士')
        self.player(INF, 'i1', name='无限修士')
        data = await self.home()
        self.assertEqual(self.store.infinite_group_ids(), {INF})
        self.assertNotIn('a1', self.store.infinite_member_qqs())
        self.assertEqual(set(self.names(data)), {'别名下的修士', '官方修士'})

    async def test_public_get_never_creates_group_records(self):
        """公开 GET 不得改存档：未知群下的玩家不能被就地新建群记录。"""
        self.group(OFF)
        # 直接塞一条 group 不在 groups 里的玩家档案（get_player 不会建群）
        unknown = 'g-unknown'
        self.store._data['players'][self.store.make_key(unknown, 'u1')] = {
            'qq': 'u1', 'group': unknown, 'adventure': cultivator('野档案'),
        }
        before = set(self.store._data.get('groups', {}))
        data = await self.home()
        self.assertEqual(set(self.store._data.get('groups', {})), before)
        self.assertNotIn(unknown, self.store._data['groups'])
        self.assertIn('野档案', self.names(data))   # 未知群 ≠ 无限服 → 照常上

    async def test_empty_save_returns_an_empty_but_ok_payload(self):
        data = await self.home()
        self.assertTrue(data['ok'])
        self.assertEqual(data['pet_rank'], [])
        self.assertEqual(data['cultivator_rank'], [])
        self.assertEqual(data['tomb_rank'], [])
        self.assertEqual(data['stats'],
                         {'players': 0, 'auth_groups': 0, 'pets': 0, 'tomb_players': 0})

    async def test_infinite_gains_are_removed_with_the_person_not_split(self):
        """固化已知局限：摸金按 openid 剔人，双服玩家的冥币无法按服拆分。

        双服玩家在无限服赚的冥币和官方服那份同处一条记录，所以「剔人」是唯一可行口径，
        代价是他的官方服收益也一并从官网消失——与群内 _tomb_rank 行为一致。
        """
        self.group(OFF)
        self.group(INF, infinite=True)
        self.player(OFF, 'd1', name='双服官方档案')
        self.player(INF, 'd1', name='双服无限档案')
        self.tomb('d1', mingbi=500)
        data = await self.home()
        self.assertEqual(data['tomb_rank'], [])
        self.assertEqual(data['stats']['tomb_players'], 0)


if __name__ == '__main__':
    unittest.main()
