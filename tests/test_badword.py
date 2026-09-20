"""大管理员「违规词管理（加 / 减 / 查看）」：本群命中即撤回，群主/管理员只提醒。

规则（2026-09-20 需求 + 用户四选确认）：
- 仅大管理员可管理（加/减/查看）；词表按群存在群档案 badwords 里，别的群不受影响；
- **子串**匹配、不分词：设置「死」时「你去死」也命中——一个字的词就触发；
- 匹配前去掉全部空白并转小写，故「去 死」「FUCK」这类写法同样命中；
- 群成员（含身份未知）→ 撤回该条消息；群主/管理员 → 只提醒不撤回；
- 大管理员 → 整体豁免（否则「加违规词 死」这条指令会把自己撤回、词根本加不进去）；
- 即时检查 + 后台每 10 秒兜底重扫；两条路靠 _badword_done 去重，同一条不会撤两次。

兜底轮询存在的意义（也是本测试文件的重点）：
① 词可能是消息**发出之后**才加进词表的——即时检查当时词表里还没有它；
② 即时检查半路抛异常时，那条消息就只能靠轮询捡回来。
"""
from pathlib import Path
import asyncio
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'petbot_framework' / 'compat'))
from qqbot_pet.main import (
    BADWORD_BUF_MAX, BADWORD_TEXT_MAX, BADWORD_WINDOW_SEC, BADWORD_WORD_MAX,
    BADWORD_WORDS_MAX, KNOWN_COMMANDS, WEB_BLOCKED_COMMANDS, PetParkPlugin,
)
from qqbot_pet.petpark.store import PetStore


class BadwordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = PetStore(Path(self.tmp.name) / 'data.json')
        self.plugin = PetParkPlugin.__new__(PetParkPlugin)
        self.plugin.store = self.store
        self.plugin.admins = {'root'}
        self.plugin._active_event_commands = lambda: set()
        # 轮询没有 event，撤回时只能退回框架的 bot 客户端（这里给个取不到的环境，
        # 由 fake_recall 兜住——真实环境里 _get_bot 拿得到）
        self.plugin.context = SimpleNamespace(get_bot=lambda: None)
        # __init__ 里建的缓冲/task 都不跑，这里手动给缓冲（task 不需要，测试直接驱动 _badword_sweep）
        self.plugin._badword_recent = {}
        self.plugin._badword_done = {}
        self.recalls = []      # 记录 (群, 消息ID, api)
        self.recall_ok = True  # 让「撤回失败」这一路也能测
        plugin = self.plugin

        async def fake_recall(group_id, mid, api=None):
            self.recalls.append((group_id, mid, api))
            return self.recall_ok

        plugin._recall_message = fake_recall

    # ---------------- 小工具 ----------------

    def call(self, text, actor='root', group='g'):
        """走真实 dispatch（验证指令注册与分发都通）。"""
        event = SimpleNamespace(get_sender_id=lambda: actor)
        return self.plugin.dispatch(event, actor, group, text)

    def event(self, actor='u1', mid='m1', role='member'):
        return SimpleNamespace(
            get_sender_id=lambda: actor,
            sender_role=role,
            message_obj=SimpleNamespace(message_id=mid),
            bot=SimpleNamespace(api=SimpleNamespace(name='bot-api')),
        )

    def gate(self, text, actor='u1', mid='m1', role='member', group='g'):
        """即时检查这条路（等价于 on_message 里那一句）。"""
        ev = self.event(actor, mid, role)
        return asyncio.run(self.plugin._badword_gate(ev, actor, group, text))

    def sweep(self):
        return asyncio.run(self.plugin._badword_sweep())

    def words(self, group='g'):
        return self.plugin._badwords_in_group(group)

    # ---------------- 注册与权限 ----------------

    def test_registered_in_known_commands(self):
        for c in ('加违规词', '减违规词', '违规词'):
            self.assertIn(c, KNOWN_COMMANDS, f'{c} 未注册进 KNOWN_COMMANDS，群聊分发会被吞掉')
            self.assertIn(c, WEB_BLOCKED_COMMANDS, f'{c} 未在网页端屏蔽')

    def test_super_admin_only(self):
        self.store.get_group('g')['subadmins'] = ['sub']
        for actor in ('sub', 'u1'):
            self.assertIn('仅大管理员', self.call('加违规词 死', actor=actor))
            self.assertIn('仅大管理员', self.call('减违规词 死', actor=actor))
            self.assertIn('仅大管理员', self.call('违规词', actor=actor))
        self.assertEqual(self.words(), [])

    # ---------------- 加 / 减 / 查看 ----------------

    def test_add_then_view(self):
        out = self.call('加违规词 死 滚')
        self.assertIn('已添加违规词（2 个）', out)
        self.assertEqual(self.words(), ['死', '滚'])
        # 列表页
        out = self.call('违规词')
        self.assertIn('本群违规词（2 个）', out)
        self.assertIn('`死`', out)
        self.assertIn('`滚`', out)
        # 单查
        self.assertIn('**已在**', self.call('违规词 死'))
        self.assertIn('不在本群违规词表里', self.call('违规词 傻逼'))

    def test_view_when_empty(self):
        out = self.call('违规词')
        self.assertIn('还没有设置违规词', out)
        self.assertIn('加违规词', out)

    def test_remove(self):
        self.call('加违规词 死 滚')
        out = self.call('减违规词 滚')
        self.assertIn('已移除违规词（1 个）', out)
        self.assertEqual(self.words(), ['死'])
        self.assertIn('不在本群违规词表里', self.call('违规词 滚'))

    def test_remove_missing_reports(self):
        self.call('加违规词 死')
        out = self.call('减违规词 死 滚')
        self.assertIn('已移除违规词（1 个）', out)
        self.assertIn('不在表里（跳过）', out)
        self.assertEqual(self.words(), [])

    def test_add_skips_duplicates(self):
        self.call('加违规词 死')
        out = self.call('加违规词 死 滚')
        self.assertIn('已在表里（跳过）', out)
        self.assertIn('已添加违规词（1 个）', out)
        self.assertEqual(self.words(), ['死', '滚'], '重复添加不应产生重复条目')

    def test_add_rejects_overlong_word(self):
        long_word = 'x' * (BADWORD_WORD_MAX + 1)
        out = self.call(f'加违规词 {long_word}')
        self.assertIn('过长', out)
        self.assertNotIn(long_word, self.words())
        # 正好到上限照收
        ok_word = 'y' * BADWORD_WORD_MAX
        self.assertIn('已添加', self.call(f'加违规词 {ok_word}'))
        self.assertIn(ok_word, self.words())

    def test_add_respects_words_max(self):
        self.store.get_group('g')['badwords'] = [f'w{i}' for i in range(BADWORD_WORDS_MAX)]
        out = self.call('加违规词 新词')
        self.assertIn(f'已达每群上限 {BADWORD_WORDS_MAX} 个', out)
        self.assertNotIn('新词', self.words())
        self.assertEqual(len(self.words()), BADWORD_WORDS_MAX)

    def test_bad_args(self):
        for cmd in ('加违规词', '减违规词'):
            self.assertIn('用法：', self.call(cmd), cmd)

    def test_words_are_per_group(self):
        self.call('加违规词 死', group='g')
        self.assertEqual(self.words('g2'), [])
        self.assertIsNone(self.gate('你去死', group='g2'))
        self.assertNotIn('g2', self.plugin._badword_done)

    def test_write_normalizes_dirty_list(self):
        # 落档里混进空串/空白/重复也要能用、且写回时被规范掉
        self.store.get_group('g')['badwords'] = [' 死 ', '', None, '死']
        self.assertEqual(self.words(), ['死'])
        self.call('加违规词 滚')
        self.assertEqual(self.store.get_group('g')['badwords'], ['死', '滚'])

    # ---------------- 匹配规则 ----------------

    def test_single_char_word_matches_substring(self):
        # 需求原话：设置「死」时用户说「去死」也是违规、也要撤回
        self.call('加违规词 死')
        out = self.gate('你这人怎么不去死')
        self.assertIn('已撤回', out)
        self.assertEqual(len(self.recalls), 1)

    def test_reply_does_not_leak_the_word(self):
        # 回执里绝不能出现命中的词——那等于把词表公开，玩家照着绕
        self.call('加违规词 死')
        out = self.gate('你去死吧')
        self.assertNotIn('死', out)

    def test_no_hit_returns_none(self):
        self.call('加违规词 死')
        self.assertIsNone(self.gate('今天天气不错'))
        self.assertEqual(self.recalls, [])

    def test_no_words_set_returns_none(self):
        self.assertIsNone(self.gate('你去死'))
        self.assertEqual(self.recalls, [])

    def test_whitespace_and_case_folded(self):
        self.call('加违规词 fuck 死')
        # 中间插空格的拆字写法
        self.assertIsNotNone(self.gate('去 死'))
        # 英文大小写不敏感
        self.assertIsNotNone(self.gate('you FUCK'))
        # 纯空白消息不会因为「去空白后为空」而误命中
        self.assertIsNone(self.gate('   '))

    def test_multi_char_word_does_not_match_partial(self):
        self.call('加违规词 傻逼')
        self.assertIsNone(self.gate('傻'))
        self.assertIsNotNone(self.gate('你个傻逼'))

    # ---------------- 按身份分流 ----------------

    def test_member_hit_recalls(self):
        self.call('加违规词 死')
        out = self.gate('你去死', actor='u1', mid='m9', role='member')
        self.assertIn('已撤回', out)
        self.assertEqual(self.recalls[0][0], 'g')
        self.assertEqual(self.recalls[0][1], 'm9')

    def test_owner_and_admin_only_warned(self):
        self.call('加违规词 死')
        for role in ('owner', 'admin'):
            out = self.gate('你去死', actor=f'u-{role}', mid=f'm-{role}', role=role)
            self.assertIn('违规提醒', out)
            self.assertIn('你是本群管理者', out)
            self.assertEqual(self.recalls, [], f'{role} 的消息不该被撤回')
            self.assertNotIn('死', out)

    def test_unknown_role_treated_as_member(self):
        # 群身份取不到时按成员处理（需求默认），撤回失败会自动退化成提醒
        self.call('加违规词 死')
        for role in ('', 'unknown', None):
            self.recalls.clear()
            out = self.gate('你去死', mid=f'm-{role}', role=role)
            self.assertIn('已撤回', out, str(role))
            self.assertEqual(len(self.recalls), 1)

    def test_recall_failure_degrades_to_warn(self):
        self.call('加违规词 死')
        self.recall_ok = False
        out = self.gate('你去死')
        self.assertIn('违规提醒', out)
        self.assertIn('撤回失败', out)
        self.assertEqual(len(self.recalls), 1, '仍然要尝试过撤回')

    def test_super_admin_exempt_and_can_still_add(self):
        # 关键闭环：大管理员说「加违规词 死」，这条消息本身含「死」，绝不能被自己撤回
        self.call('加违规词 死')
        self.assertIsNone(self.gate('加违规词 死', actor='root', role='member'))
        self.assertEqual(self.recalls, [])
        # 同一句话，普通成员发就照样撤回——豁免看的是身份，不是「内容像不像指令」
        self.assertIn('已撤回', self.gate('加违规词 死', actor='u1', mid='mX', role='member'))
        self.assertIn('已在表里（跳过）', self.call('加违规词 死 滚', actor='root'))
        self.assertIn('滚', self.words())

    def test_super_admin_by_bound_qq(self):
        self.store.set_qq_binding('pid_x', 'root')
        self.call('加违规词 死')
        self.assertIsNone(self.gate('你去死', actor='pid_x', role='member'))
        self.assertEqual(self.recalls, [])

    def test_not_a_group_skipped(self):
        self.assertEqual(self.gate('你去死', group='private'), None)
        self.assertEqual(self.gate('你去死', group=''), None)
        self.assertEqual(self.plugin._badword_recent, {}, '非群聊不记缓冲')

    # ---------------- 撤回用哪只机器人 ----------------

    def test_recall_uses_event_bot_api(self):
        # 即时路径必须用「收到这条消息的那个机器人」的 api 去撤（多机器人场景）
        self.call('加违规词 死')
        ev = self.event(mid='m7')
        asyncio.run(self.plugin._badword_gate(ev, 'u1', 'g', '你去死'))
        self.assertIs(self.recalls[0][2], ev.bot.api)

    # ---------------- 缓冲记录 ----------------

    def test_buffer_records_even_without_hit(self):
        # 没设词时也要记：词可能是这条消息之后才加的，轮询要能拿新词回头重扫
        self.assertIsNone(self.gate('随便说句话', mid='m1'))
        buf = self.plugin._badword_recent['g']
        self.assertEqual(len(buf), 1)
        mid, qq, role, ts, txt = buf[0]
        self.assertEqual((mid, qq, role, txt), ('m1', 'u1', 'member', '随便说句话'))
        self.assertLessEqual(abs(ts - time.time()), 5)

    def test_buffer_skips_messages_without_id(self):
        # 没有 message_id 就撤不掉，记了也白记
        self.gate('随便说句话', mid='')
        self.assertEqual(self.plugin._badword_recent.get('g', []), [])

    def test_buffer_is_bounded_and_truncates(self):
        for i in range(BADWORD_BUF_MAX + 20):
            self.gate('x' * (BADWORD_TEXT_MAX + 100), mid=f'm{i}')
        buf = self.plugin._badword_recent['g']
        self.assertEqual(len(buf), BADWORD_BUF_MAX)
        self.assertEqual(buf[0][0], 'm20', '最老的 20 条应被挤出去')
        self.assertEqual(buf[-1][0], f'm{BADWORD_BUF_MAX + 19}')
        self.assertEqual(len(buf[-1][4]), BADWORD_TEXT_MAX)

    # ---------------- 兜底轮询 ----------------

    def test_sweep_empty(self):
        self.assertEqual(self.sweep(), 0)

    def test_sweep_catches_word_added_after_message(self):
        # 本功能的核心理由：先说话、后加词，即时检查当时词表里还没有这个词
        self.assertIsNone(self.gate('你去死', mid='m5'))
        self.assertEqual(self.recalls, [])
        self.call('加违规词 死')
        self.assertEqual(self.sweep(), 1)
        self.assertEqual(self.recalls, [('g', 'm5', None)])  # 轮询没有 event，退回 bot 客户端

    def test_sweep_does_not_repunish(self):
        # 即时已经处置过的，轮询不能再撤一次
        self.call('加违规词 死')
        self.assertIsNotNone(self.gate('你去死', mid='m1'))
        self.assertEqual(self.sweep(), 0)
        self.assertEqual(len(self.recalls), 1)

    def test_sweep_skips_outside_recall_window(self):
        self.call('加违规词 死')
        self.plugin._badword_remember('g', 'm-old', 'u1', 'member', '你去死')
        mid, qq, role, _, txt = self.plugin._badword_recent['g'][-1]
        self.plugin._badword_recent['g'][-1] = (
            mid, qq, role, time.time() - BADWORD_WINDOW_SEC - 10, txt,
        )
        self.assertEqual(self.sweep(), 0, '超出 2 分钟窗口的消息撤不掉，不该白试')

    def test_sweep_skips_groups_without_words(self):
        self.gate('你去死', mid='m1')
        self.assertEqual(self.sweep(), 0)
        self.assertEqual(self.recalls, [])

    def test_sweep_respects_exemptions(self):
        self.call('加违规词 死')
        self.plugin._badword_remember('g', 'm-root', 'root', 'member', '你去死')
        self.plugin._badword_remember('g', 'm-owner', 'u9', 'owner', '你去死')
        self.assertEqual(self.sweep(), 1, '大管理员豁免不算处置，群主只提醒才算')
        self.assertEqual(self.recalls, [], '两条都不该被撤回')

    def test_sweep_prunes_done_set(self):
        self.call('加违规词 死')
        self.plugin._badword_done['g'] = {'gone-1', 'gone-2'}
        self.gate('随便说句话', mid='m1')
        self.sweep()
        self.assertEqual(self.plugin._badword_done['g'], set(), '滑出缓冲的消息ID要从 done 里清掉')

    def test_sweep_per_group_isolation(self):
        self.call('加违规词 死', group='g')
        self.plugin._badword_remember('g2', 'm1', 'u1', 'member', '你去死')
        self.assertEqual(self.sweep(), 0)
        self.assertEqual(self.recalls, [])

    def test_sweep_counts_multiple_hits(self):
        self.call('加违规词 死')
        for i in range(3):
            self.plugin._badword_remember('g', f'm{i}', 'u1', 'member', '你去死')
        self.assertEqual(self.sweep(), 3)
        self.assertEqual(len(self.recalls), 3)

    # ---------------- 与封号/普通指令互不干扰 ----------------

    def test_normal_commands_unaffected(self):
        # 没违规的普通聊天必须静默放行（返回 None），否则每句话都顶一条提示 = 刷屏
        self.call('加违规词 死')
        self.assertIsNone(self.gate('签到', actor='u1', mid='m2'))
        self.assertIsNone(self.gate('哈哈哈', actor='u1', mid='m3'))
        self.assertEqual(self.recalls, [])

    def test_full_flow(self):
        # 加词 → 成员触发即撤 → 减词 → 不再触发
        self.assertIn('已添加违规词', self.call('加违规词 死 滚'))
        self.assertIn('已撤回', self.gate('滚', mid='mA'))
        self.assertIn('已移除违规词', self.call('减违规词 滚'))
        self.assertIsNone(self.gate('滚', mid='mB'))
        self.assertIn('已撤回', self.gate('你去死', mid='mC'))
        self.assertEqual([call[1] for call in self.recalls], ['mA', 'mC'])


if __name__ == '__main__':
    unittest.main()
