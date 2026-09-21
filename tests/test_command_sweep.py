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
    plugin._assistant_pending = {}
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
        def labels(kb):
            return [b.get('action', {}).get('data') for r in (kb or {}).get('rows', []) for b in r.get('buttons', [])]
        # 我要氪金/官方网站只在「灵契仙途」菜单图下方；其它卡片视图不再带底部按钮。
        self.assertIn('我要氪金', labels(menu))
        self.assertIn('官方网站', labels(menu))
        self.assertNotIn('我要氪金', labels(card))
        self.assertNotIn('官方网站', labels(card))
        # 纯文本/战利品/战斗结果 → 不加按钮
        for text, reply in [('历练', '获得灵材、修为'), ('修士修炼', '修炼归来'), ('挑战秘境', '## 通关'),
                            ('讨伐首领', '造成伤害'), ('收获', '银杏叶 +6'), ('我的体力', '体力 80/100')]:
            self.assertIsNone(self.plugin._keyboard_for_cmd(text, reply),
                              f'纯文本回复不应附主菜单按钮: {text}')
        # 扫雷/棋类按钮各自保留
        self.assertIsNotNone(self.plugin._keyboard_for_cmd('扫雷', '...'))
        self.assertIsNotNone(self.plugin._keyboard_for_cmd('五子棋', '...'))

    # ------------------------------------------------------------------
    # 自动助手面板：唯一的回调按钮（action.type=1）用例
    # ------------------------------------------------------------------
    @staticmethod
    def _btns(kb):
        return [b for r in (kb or {}).get('rows', []) for b in r.get('buttons', [])]

    def test_assistant_panel_is_callback_keyboard(self):
        """助手面板必须是回调按钮（type=1）：点击只推互动事件，不会在群里冒出玩家消息。

        action.type=2 是「指令按钮」——点击等于让玩家发一条 ``@机器人 助手选 3``；
        type=1 才是「回调按钮」，由框架 on_interaction_create 收事件后合成消息走同一条路由。
        """
        kb = self.plugin._keyboard_for_cmd('自动助手', '## 🧘 自动助手', 'g', 'root')
        self.assertIsNotNone(kb, '自动助手应附勾选面板')
        rows = kb['rows']
        self.assertEqual(len(rows), 5, '面板应正好 5 行（QQ 按钮上限）')
        btns = self._btns(kb)
        self.assertEqual(len(btns), len(data.ASSISTANT_TASKS) + 1, '19 个任务 + 确定')
        for b in btns:
            action = b['action']
            self.assertEqual(action['type'], 1, f"{b['id']} 应为回调按钮")
            # 官方 schema：enter/reply/anchor 是「指令按钮可用」，回调按钮必须不带；
            # unsupport_tips 是低版本客户端不认回调按钮时的兜底文案。
            self.assertNotIn('enter', action, f"{b['id']} 回调按钮不得带 enter")
            self.assertIn('unsupport_tips', action, f"{b['id']} 回调按钮应带 unsupport_tips")
            self.assertTrue(action['data'].startswith('助手'), f"{b['id']} 载荷应是助手指令")
        # 全部是光标签：不加 ✅/⬜ 前缀（前缀会把标签撑过 6 字节上限而被截断）
        labels = [b['render_data']['label'] for b in btns]
        self.assertEqual(labels, [t[1] for t in data.ASSISTANT_TASKS] + ['确定'])
        self.assertIn('助手确定', [b['action']['data'] for b in btns])

    def test_assistant_button_labels_fit_qq_cap(self):
        """回归：QQ 对按钮 label 卡约 6 字节，超出会被截成「首字 + ...」。

        真机实测（4 个/行）：`神仙劫`→`神...`、`家园收取`→`家...`，而 2 个汉字的
        `砸蛋`/`打工`/`学习` 完整显示；按钮右侧大片留白说明卡的是**字节数**而不是
        宽度，所以减少每行按钮数救不了，只能压短标签。`确定生效`（12 字节）同理
        必须压成 `确定`。
        """
        for _key, label, _desc, _axis in data.ASSISTANT_TASKS:
            size = len(label.encode('utf-8'))
            self.assertLessEqual(size, 6, f'按钮标签「{label}」{size} 字节 > 6，会被截断')
        self.assertLessEqual(len('确定'.encode('utf-8')), 6)

    def test_assistant_selection_shows_in_text_not_labels(self):
        """勾选态只能走面板正文：标签放不下前缀（待选态在内存，确定前不落盘）。"""
        pet = self.store.get_player('root', 'g')['pet']
        self.plugin._assistant_pending[self.plugin._assistant_pending_key('g', 'root', pet)] = {
            'picked': ['砸蛋', '打工'], 'ts': int(time.time())}
        kb = self.plugin._keyboard_for_cmd('助手选 1', '', 'g', 'root')
        labels = [b['render_data']['label'] for b in self._btns(kb)]
        self.assertEqual(labels, [t[1] for t in data.ASSISTANT_TASKS] + ['确定'],
                         '勾选与否都不得改动按钮标签')
        text = self.plugin._assistant_panel_text(
            self.store.get_player('root', 'g'), pet, ['砸蛋', '打工'])
        self.assertIn('已选 2/4：砸蛋、打工', text, '勾选态必须出现在面板正文里')
        self.assertEqual(self.plugin._assistant_state(pet)['tasks'], [],
                         '「确定」之前不得写进宠物存档')

    def test_assistant_panel_needs_pet(self):
        """无宠物时面板不出现（助手是按宠物配置的，没有宠物无处挂载）。"""
        self.assertEqual(self.plugin._keyboard_for_cmd('自动助手', '', 'g', 'nobody'), None)

    def test_other_keyboards_stay_command_buttons(self):
        """其它既有键盘保持指令按钮（type=2），本次改动只动助手面板。"""
        for cmd, reply in [('灵契仙途', '## 灵契仙途'), ('我的修士', '![](...) 修士卡'),
                           ('扫雷', '...'), ('五子棋', '...')]:
            kb = self.plugin._keyboard_for_cmd(cmd, reply, 'g', 'root')
            self.assertIsNotNone(kb, f'{cmd} 应保留键盘')
            btns = self._btns(kb)
            self.assertTrue(btns, f'{cmd} 键盘不应为空')
            for b in btns:
                action = b['action']
                self.assertEqual(action['type'], 2, f'{cmd} 的 {b["id"]} 应仍是指令按钮')
                self.assertIn('enter', action, f'{cmd} 的 {b["id"]} 指令按钮应保留 enter')
                self.assertNotIn('unsupport_tips', action, f'{cmd} 的 {b["id"]} 不该带回调按钮字段')

    def test_assistant_brief_takes_first_meaningful_line(self):
        """执行日志摘要要取第一条有实义的文字，跳过标题井号与纯分隔线。

        回归：判定原写成 `set(line) > set('━─-—= ')`，要求整行**同时包含**每一种
        分隔符才认可，实际永不成立 → 每条日志都退化成「✅ 砸蛋：已执行」，
        玩家在『助手状态』里看不到任何执行内容。
        """
        brief = self.plugin._assistant_brief
        self.assertEqual(brief('## 🧘 打工归来\n━━━━━\n玄晶 +1200'), '🧘 打工归来')
        self.assertEqual(brief('━━━━━\n获得灵材、修为\n更多'), '获得灵材、修为')
        self.assertEqual(brief('**砸蛋** 得到神级碎片'), '砸蛋 得到神级碎片')
        self.assertEqual(brief(''), '已执行')
        self.assertEqual(brief('━━━━━\n─────\n'), '已执行')
        self.assertEqual(brief('x' * 120), 'x' * 60, '超长摘要应截断到 60 字')

    def test_adventure_commands_are_registered(self):
        """每条冒险指令都必须注册进 main 层 KNOWN_COMMANDS（否则会被过滤器吞掉）。"""
        missing = sorted(c for c in (content.COMMANDS | content.READ_COMMANDS) if c not in KNOWN_COMMANDS)
        self.assertEqual(missing, [], f'未注册进 KNOWN_COMMANDS 的指令:\n  ' + '\n  '.join(missing))


if __name__ == '__main__':
    unittest.main()
