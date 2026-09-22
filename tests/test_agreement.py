"""QQ 群强制同意用户协议：点过「同意」才准游玩灵契仙途。

规则（2026-09-22 需求 + 「与强制绑定QQ同原理」）：
- 拦的是**真指令**（dispatch 里排在已知指令过滤之后），普通聊天仍静默放行（返回 None），
  不会因为群里一句闲聊就刷一条协议提示；
- 拦截文案给出协议地址 https://bot.flyyye.cn/agreement，并挂一个「同意」按钮；
- 按钮是官方回调按钮（action type=1 / INTERACTION_CREATE），点击**不在群里冒出用户消息**；
- 按钮用官方 type=0 指定用户（specify_user_ids=点击者），只有本人能点；
- 载荷带 `同意协议 #<openid>`，服务端再兜底比对，非本人点击静默丢弃、不弹提醒；
- 同意状态跨群通用，按协议版本记录；改 AGREEMENT_VERSION 后旧同意全部失效；
- 菜单/帮助/绑定相关指令与「同意协议」本身不受拦截；大管理员（配置白名单）豁免。
"""
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'petbot_framework' / 'compat'))
from qqbot_pet.main import (
    AGREEMENT_URL, AGREEMENT_VERSION, _AGREEMENT_BLOCK_MARK,
    _AGREEMENT_ALWAYS_ALLOWED, KNOWN_COMMANDS, PetParkPlugin,
)
from qqbot_pet.petpark.store import PetStore

# 一个「会被协议闸门拦下、且放行后能在无完整 init 的测试插件上跑到底」的指令。
# 测试只关心闸门，不该为了断言「没被拦」去跑签到这类需要大量 init 态的业务。
BLOCKED_CMD = '宠物商城'
ALLOWED_CMD = '绑定教程'


class AgreementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = PetStore(Path(self.tmp.name) / 'data.json')
        self.plugin = PetParkPlugin.__new__(PetParkPlugin)
        self.plugin.store = self.store
        self.plugin.admins = {'root'}
        self.plugin.require_qq_bind = False   # 绑定拦截有自己的测试，这里只验协议闸门
        self.plugin.require_agreement = True
        self.plugin._active_event_commands = lambda: set()
        # 闸门放行后 dispatch 会继续往下走，中元活动模块与指令表也要在位
        self.plugin.zhongyuan = None
        self.plugin._zy_commands = set()
        # 协议闸门排在群授权校验之后，测试群里得先有有效授权
        self.store.get_group('g')['auth_until'] = int(time.time()) + 86400
        self.store.get_group('g2')['auth_until'] = int(time.time()) + 86400
        self.store.get_player('a', 'g')
        self.store.get_player('b', 'g')
        self.store._flush()

    def call(self, text, actor='b', group='g'):
        event = SimpleNamespace(get_sender_id=lambda: actor)
        return self.plugin.dispatch(event, actor, group, text)

    def agreed(self, qq):
        return self.store.get_agreement_version(qq)

    # ---------------- 注册 ----------------

    def test_registered_in_known_commands(self):
        self.assertIn('同意协议', KNOWN_COMMANDS,
                      '未注册进 KNOWN_COMMANDS 会被群聊分发过滤层 return None 吞掉，按钮点了没反应')

    # ---------------- 拦截 ----------------

    def test_blocks_real_commands(self):
        for text in (BLOCKED_CMD, '道具商城', '我的信息', '宠物种类', '属性', '宠物列表'):
            out = self.call(text)
            self.assertIn(_AGREEMENT_BLOCK_MARK, out or '', text)
            self.assertIn(AGREEMENT_URL, out, text)
            self.assertIn('同意', out, text)

    def test_gate_blocks_every_real_command(self):
        # 闸门本身：除白名单外一律拦（含管理指令与中元指令）
        for text in ('签到', '砸蛋', '灵宠升级', '宠物攻击', '宠物副本', '加金币 b 1', '中元活动'):
            self.assertTrue(self.plugin._agreement_block('b', text), text)

    def test_plain_chat_still_silent(self):
        # 关键：普通聊天返回 None，否则群里谁说句话都被顶一条协议提示 = 刷屏
        for text in ('今天天气不错', '哈哈哈', '这个协议写得真长'):
            self.assertIsNone(self.call(text), text)

    def test_allowed_commands_pass_through(self):
        for text in sorted(_AGREEMENT_ALWAYS_ALLOWED):
            self.assertIsNone(self.plugin._agreement_block('b', text), text)
        # 端到端验一个：真的没被拦，走到了业务逻辑
        self.assertIn('绑定QQ教程', self.call(ALLOWED_CMD) or '')

    def test_admin_exempt(self):
        self.assertNotIn(_AGREEMENT_BLOCK_MARK, self.call(BLOCKED_CMD, actor='root') or '')
        # 白名单里写的是绑定QQ号时也认得出
        self.store.set_qq_binding('pid_x', 'root')
        self.assertNotIn(_AGREEMENT_BLOCK_MARK, self.call(BLOCKED_CMD, actor='pid_x') or '')
        # 同一条指令，非管理员是要被拦的（证明上面确实是「豁免」而非「没接上闸门」）
        self.assertIn(_AGREEMENT_BLOCK_MARK, self.call(BLOCKED_CMD, actor='b'))

    def test_switch_off_disables_gate(self):
        self.plugin.require_agreement = False
        self.assertNotIn(_AGREEMENT_BLOCK_MARK, self.call(BLOCKED_CMD) or '')
        self.assertEqual(self.agreed('b'), '', '关闸门不该顺手写下同意记录')

    # ---------------- 同意 ----------------

    def test_consent_records_and_unlocks(self):
        self.assertIn('已同意用户协议', self.call('同意协议'))
        self.assertEqual(self.agreed('b'), AGREEMENT_VERSION)
        self.assertIn(AGREEMENT_VERSION, self.call('同意协议'))
        # 之后再发指令不再被拦
        self.assertNotIn(_AGREEMENT_BLOCK_MARK, self.call(BLOCKED_CMD) or '')

    def test_consent_is_cross_group(self):
        self.call('同意协议', group='g')
        self.assertNotIn(_AGREEMENT_BLOCK_MARK, self.call(BLOCKED_CMD, group='g2') or '')
        # 反过来：在 g 同意不影响别人
        self.assertIn(_AGREEMENT_BLOCK_MARK, self.call(BLOCKED_CMD, actor='a') or '')

    def test_version_bump_regates(self):
        self.call('同意协议')
        # 协议改版：旧版本号不再算数（模拟运营方改了 AGREEMENT_VERSION）
        self.store.set_agreement_version('b', '2020-01-01')
        self.assertIn(_AGREEMENT_BLOCK_MARK, self.call(BLOCKED_CMD) or '')

    def test_consent_does_not_leak_across_users(self):
        self.call('同意协议', actor='b')
        self.assertEqual(self.agreed('a'), '')
        self.assertIn(_AGREEMENT_BLOCK_MARK, self.call(BLOCKED_CMD, actor='a') or '')

    # ---------------- 按钮载荷的主人比对 ----------------

    def test_button_payload_from_b_records_for_b(self):
        self.assertIn('已同意用户协议', self.call('同意协议 #b', actor='b'))
        self.assertEqual(self.agreed('b'), AGREEMENT_VERSION)

    def test_foreign_owner_payload_silently_dropped(self):
        # 官方 type=0 已拦非本人，这里兜底：万一事件照推了，静默丢弃、不写同意、
        # 也绝不弹「无权」提醒（自己在群里点一下就看到「无权」，本身就是刷屏）
        self.assertIsNone(self.call('同意协议 #b', actor='a'))
        self.assertEqual(self.agreed('a'), '')
        self.assertEqual(self.agreed('b'), '')

    # ---------------- 按钮本身 ----------------

    def test_keyboard_is_owner_bound_callback_button(self):
        kb = self.plugin._agreement_keyboard('b')
        row = kb['rows'][0]
        self.assertEqual(len(kb['rows']), 1, '只有一个「同意」按钮')
        btn = row['buttons'][0]
        self.assertEqual(btn['render_data']['label'], '同意')
        self.assertEqual(btn['action']['type'], 1, '必须是回调按钮，否则点击会在群里冒出一条用户消息')
        self.assertNotIn('enter', btn['action'], '官方 schema：回调按钮不得带 enter')
        self.assertEqual(btn['action']['permission']['type'], 0, 'type=0 = 指定用户可操作')
        self.assertEqual(btn['action']['permission']['specify_user_ids'], ['b'])
        self.assertEqual(btn['action']['data'], '同意协议 #b')

    def test_keyboard_attached_to_block_reply_only(self):
        block = self.call(BLOCKED_CMD)
        kb = self.plugin._keyboard_for_cmd(BLOCKED_CMD, block, 'g', 'b')
        self.assertIsNotNone(kb, '协议拦截文案必须挂上同意按钮，否则用户无处可点')
        self.assertEqual(kb['rows'][0]['buttons'][0]['action']['data'], '同意协议 #b')
        # 同意之后的普通回复不该再挂协议按钮
        self.call('同意协议')
        ok = self.call(BLOCKED_CMD)
        self.assertNotIn(_AGREEMENT_BLOCK_MARK, ok or '')
        self.assertIsNone(self.plugin._keyboard_for_cmd(BLOCKED_CMD, ok, 'g', 'b'))

    def test_no_owner_means_no_per_user_limit(self):
        # 私聊等拿不到 openid 的场合退化为所有人可点，而不是造出一个空 specify_user_ids
        kb = self.plugin._agreement_keyboard('')
        self.assertEqual(kb['rows'][0]['buttons'][0]['action']['permission']['type'], 2)
        self.assertEqual(kb['rows'][0]['buttons'][0]['action']['data'], '同意协议')


if __name__ == '__main__':
    unittest.main()
