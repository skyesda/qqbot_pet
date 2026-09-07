"""全指令路由冒烟测试：注册表里每一个指令经真实 dispatcher 分发都不得抛未捕获异常。

覆盖两个入口：
- petpark/adventure/service.handle()：冒险/宗门轴（content.COMMANDS ∪ READ_COMMANDS）。
- main.PetParkPlugin.dispatch()：全 KNOWN_COMMANDS（含宠物/货币/银行/摸金/扫雷/坐骑/点歌等）。

目标不是断言每个指令的正确语义（那由各专项测试覆盖），而是守住「每个已注册指令被识别并路由、
不会因缺字段/缺状态而崩溃」的底线。棋类为 WIP 模块（项目测试套件已统一忽略），这里只验证它能被
路由器正确识别并交给棋类处理器，不验证棋类逻辑。

从父目录运行：cd C:\\Users\\18083\\Desktop\\bot && python -m pytest qqbot_pet/tests/test_command_sweep.py -q
"""
import sys
import time
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'petbot_framework' / 'compat'))

from qqbot_pet.main import PetParkPlugin, KNOWN_COMMANDS
from qqbot_pet.petpark import data
from qqbot_pet.petpark.adventure import content
from qqbot_pet.petpark.adventure.service import AdventureService
from qqbot_pet.petpark.pet import new_pet
from qqbot_pet.petpark.store import PetStore


def _make_store(tmpdir):
    store = PetStore(tmpdir / 'petpark.json')
    g = store.get_group('g')
    g['auth_until'] = int(time.time()) + 10 ** 8  # 授权，避免 _auth_blocked_text 挡住所有群指令
    g['enabled'] = True
    return store


def _plugin(store, tmpdir):
    plugin = PetParkPlugin.__new__(PetParkPlugin)
    plugin.store = store
    plugin.admins = {'root'}
    plugin._active_event_commands = lambda: set()
    plugin.config = {}
    plugin.require_qq_bind = False
    plugin.song_enabled = False
    plugin._song_sessions = {}
    plugin.song_silk_dir = tmpdir / 'song_silk'; plugin.song_silk_dir.mkdir(exist_ok=True)
    plugin.silk_url_base = ''
    plugin.silk_encoder_path = ''
    plugin.alapi_token = ''
    plugin._tomb_sessions = {}
    plugin._ms_sessions = {}
    plugin._group_reset_pending = {}
    plugin._pending_qq_bind = {}
    plugin._pending_group_bind = {}
    plugin._nick_cache = {}
    plugin._role_cache = {}
    plugin._member_api_ok = False
    plugin._tomb_coop_teams = {}
    plugin._tomb_coop_index = {}
    plugin._broadcast_tasks = set()
    plugin._group_msg_log = {}
    plugin._web = None
    plugin.zhongyuan = None
    plugin._zy_commands = set()
    plugin._ai_router = None
    plugin._image_renderer = None
    # 棋类为 WIP：给一个路由哨兵，证明 dispatcher 能正确识别并转交，但不测棋类逻辑。
    plugin._board_games = SimpleNamespace(handle=lambda *a, **k: '棋类WIP')
    plugin.mute_enabled = True
    plugin.auto_approve = True
    plugin.welcome_push = True
    plugin.leave_push = True
    plugin.attack_energy = 10
    plugin.rank_size = 10
    plugin.subadmin_daily_add_limit = 10 ** 9
    plugin.rank_reward_diamond_min = 10
    plugin.rank_reward_diamond_max = 50
    plugin.sign_jifen_min = 0; plugin.sign_jifen_max = 0
    plugin.sign_coin_min = 0; plugin.sign_coin_max = 0
    plugin.sign_streak_bonus = 0
    plugin.invite_coin = 0; plugin.invite_jifen = 0; plugin.invite_diamond = 0
    return plugin


def _provision(store):
    """造一个完整修士 + 灵宠 + 充裕货币 + 背包的玩家，让指令尽量进入真实逻辑分支。"""
    svc = AdventureService(store, lambda: time.time())
    svc.handle('g', 'root', '踏入仙途 剑修'.split())
    p = store.get_player('root', 'g')
    a = p['adventure']
    a['spirit_root'] = '杂灵根'  # 无属性，战斗类断言不被随机灵根/克制带偏
    a.update(level=20, cultivation=5000, ore=200, stamina=80, stamina_max=100,
             insight=5, wudao=30, gengu=20, rewards=0, world_hits=0,
             equipment={'weapon': 3, 'robe': 2, 'seal': 1, 'crown': 1, 'boots': 1, 'pendant': 1})
    a['tactics'] = ['裂云剑']
    pet = new_pet('狐狸', '普通')
    pet['element'] = '火'; pet['level'] = 20; pet['name'] = '狐火'; pet['star'] = 3
    p.update(pets=[pet], active_pet=0, pet=pet,
             coin=100000, jifen=100000, diamond=100000, lingshi=100000, xuanjing=100000, tianjing=20000,
             bag={'红药水': 3, '灵石': 100, '品质碎片': 5}, seat={})
    store._flush()


class CommandSweepTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)
        self.store = _make_store(self.tmpdir)
        _provision(self.store)
        self.plugin = _plugin(self.store, self.tmpdir)
        self.event = SimpleNamespace(get_sender_id=lambda: 'root',
                                     message_obj=SimpleNamespace(message_id='m1'))

    def _dispatch(self, cmd):
        return self.plugin.dispatch(self.event, 'root', 'g', cmd)

    def test_every_known_command_routes_without_crash(self):
        """全 KNOWN_COMMANDS 经真实 dispatch() 分发，不得抛未捕获异常。"""
        failures = []
        for cmd in sorted(KNOWN_COMMANDS):
            try:
                self._dispatch(cmd)
            except Exception as exc:  # noqa: BLE001 —— 冒烟测试只拦崩溃
                failures.append((cmd, f'{type(exc).__name__}: {exc}'))
        self.assertEqual(failures, [], f'{len(failures)} 个指令分发崩溃:\n' +
                         '\n'.join(f'  {c}: {e}' for c, e in failures[:40]))

    def test_every_adventure_command_routes_without_crash(self):
        """冒险/宗门轴全部指令经 AdventureService.handle() 分发，不得抛未捕获异常。"""
        svc = AdventureService(self.store, lambda: time.time())
        failures = []
        for cmd in sorted(content.COMMANDS | content.READ_COMMANDS):
            try:
                svc.handle('g', 'root', (cmd + ' ').split())
            except Exception as exc:  # noqa: BLE001
                failures.append((cmd, f'{type(exc).__name__}: {exc}'))
        self.assertEqual(failures, [], f'{len(failures)} 个指令分发崩溃:\n' +
                         '\n'.join(f'  {c}: {e}' for c, e in failures[:40]))

    def test_menu_keyboard_only_under_menu_cards(self):
        """主菜单快捷按钮只在「菜单/卡片图」指令下附带，历练/修炼/战利品等纯文本回复一律不加。"""
        menu = self.plugin._keyboard_for_cmd('灵契仙途', '## 灵契仙途')
        card = self.plugin._keyboard_for_cmd('我的修士', '![](...) 修士卡')
        self.assertIsNotNone(menu, '菜单指令应附主菜单按钮')
        self.assertIsNotNone(card, '卡片图指令应附主菜单按钮')
        self.assertIn('rows', menu or {})
        # 纯文本/战利品/战斗结果 → 不加按钮
        for text, reply in [('历练', '获得灵材、修为'), ('修士修炼', '修炼归来'), ('挑战秘境', '## 通关'),
                            ('讨伐首领', '造成伤害'), ('收获', '银杏叶 +6'), ('我的体力', '体力 80/100')]:
            self.assertIsNone(self.plugin._keyboard_for_cmd(text, reply),
                              f'纯文本回复不应附主菜单按钮: {text}')
        # 扫雷/棋类按钮各自保留
        self.assertIsNotNone(self.plugin._keyboard_for_cmd('扫雷', '...'))
        self.assertIsNotNone(self.plugin._keyboard_for_cmd('五子棋', '...'))

    def test_adventure_commands_are_registered(self):
        """每条冒险指令都必须注册进 main 层 KNOWN_COMMANDS（否则会被过滤器吞掉）。"""
        missing = sorted(c for c in (content.COMMANDS | content.READ_COMMANDS) if c not in KNOWN_COMMANDS)
        self.assertEqual(missing, [], f'未注册进 KNOWN_COMMANDS 的指令:\n  ' + '\n  '.join(missing))


if __name__ == '__main__':
    unittest.main()
