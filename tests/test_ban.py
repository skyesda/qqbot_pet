"""大管理员「封号（可限天数）/ 永久封号 / 解封 / 查看封号状态」：本群封号，被封用户所有指令不可用。

规则（2026-09-20 需求 + 用户三选确认）：
- 仅大管理员可发（小管理员无权）；
- 只作用于本群，其它群照常游玩；
- 大管理员不可被封（要停权请从后台 admins 配置移除）；
- 被封用户的一切游戏指令不可用（含口令抽奖、管理指令）；
- 别人也不能给他转让/赠送（物品/货币/宠物/坐骑四条路都汇到 _check_transfer_limit）；
- 普通聊天仍静默放行（返回 None），不在群里刷屏。

天数规则（第二轮三选）：
- `封号 用户ID [天数]`，天数省略即永久；`永久封号 用户ID` 是等价显式写法；
- 重复封号按新天数**重新计时**（覆盖，可缩短）；永久封号**不被有限天数降格**；
- 到期靠读取时判断自动失效，不需要清理任务。
"""
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'petbot_framework' / 'compat'))
from qqbot_pet.main import (
    BAN_REPLY, KNOWN_COMMANDS, WEB_BLOCKED_COMMANDS, PetParkPlugin,
)
from qqbot_pet.petpark.store import PetStore

DAY = 86400


class BanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = PetStore(Path(self.tmp.name) / 'data.json')
        self.plugin = PetParkPlugin.__new__(PetParkPlugin)
        self.plugin.store = self.store
        self.plugin.admins = {'root'}
        self.plugin._active_event_commands = lambda: set()
        self.a = self.store.get_player('a', 'g')
        self.b = self.store.get_player('b', 'g')
        self.store._flush()

    def call(self, text, actor='root', group='g'):
        event = SimpleNamespace(get_sender_id=lambda: actor)
        return self.plugin.dispatch(event, actor, group, text)

    def banned(self, group='g'):
        """生效中的封号 {ID: 到期ts}（已到期的不会出现在这里）。"""
        return self.plugin._banned_in_group(group)

    def raw_banned(self, group='g'):
        """群档案里**原样**存着的 banned 字段（用于验证规范化/清理）。"""
        return self.store.get_group(group).get('banned')

    def expire_of(self, qq, group='g'):
        return self.plugin._ban_expire(group, qq)

    # ---------------- 注册与权限 ----------------

    def test_registered_in_known_commands(self):
        for c in ('封号', '永久封号', '解封', '封号状态', '封号列表'):
            self.assertIn(c, KNOWN_COMMANDS, f'{c} 未注册进 KNOWN_COMMANDS，群聊分发会被吞掉')
            self.assertIn(c, WEB_BLOCKED_COMMANDS, f'{c} 未在网页端屏蔽')

    def test_super_admin_only(self):
        self.store.get_group('g')['subadmins'] = ['sub']
        for actor in ('sub', 'b'):
            self.assertIn('仅大管理员', self.call('封号 b', actor=actor))
            self.assertIn('仅大管理员', self.call('永久封号 b', actor=actor))
            self.assertIn('仅大管理员', self.call('解封 b', actor=actor))
        self.assertEqual(self.banned(), {})

    # ---------------- 封号生效 ----------------

    def test_ban_blocks_all_commands(self):
        self.assertIn('已在本群封禁', self.call('封号 b'))
        self.assertIn('b', self.banned())
        # 普通玩家指令
        self.assertEqual(self.call('签到', actor='b'), BAN_REPLY)
        # 小管理员的加币指令也一并拦下
        self.store.get_group('g')['subadmins'] = ['b']
        self.assertEqual(self.call('加金币 a 100', actor='b'), BAN_REPLY)
        # 大管理员的管理指令同样拦（被封者不是大管理员）
        self.assertEqual(self.call('加道具 b 聚灵丹 1', actor='b'), BAN_REPLY)

    def test_plain_chat_still_silent(self):
        # 关键：普通聊天返回 None，否则对方在群里随便说句话都被顶一条封号提示 = 刷屏
        self.call('封号 b')
        self.assertIsNone(self.call('今天天气不错', actor='b'))
        self.assertIsNone(self.call('哈哈哈', actor='b'))

    def test_ban_does_not_affect_others_or_other_groups(self):
        self.call('封号 b')
        self.assertNotEqual(self.call('签到', actor='a'), BAN_REPLY)
        # 按群隔离：b 在别的群照常
        self.assertEqual(self.banned('g2'), {})
        self.assertNotEqual(self.call('签到', actor='b', group='g2'), BAN_REPLY)

    def test_ban_target_need_not_exist_yet(self):
        # 刻意不要求「已在本群参与过」：要封的多半是刷完就跑或还没落档的人
        self.assertIn('已在本群封禁', self.call('封号 ghost'))
        self.assertIn('ghost', self.banned())
        # 名单按ID匹配，对方之后进群也照样受限
        self.assertEqual(self.call('签到', actor='ghost'), BAN_REPLY)

    def test_ban_by_bound_qq_number(self):
        # 管理员手边多半只有QQ号，得能靠绑定表反查到平台ID
        self.store.set_qq_binding('b', '10001')
        self.assertIn('已在本群封禁', self.call('封号 10001'))
        self.assertIn('b', self.banned())
        self.assertEqual(self.call('签到', actor='b'), BAN_REPLY)

    # ---------------- 天数 / 永久 ----------------

    def test_default_ban_is_permanent(self):
        out = self.call('封号 b')
        self.assertIn('已封号（永久）', out)
        self.assertEqual(self.expire_of('b'), 0)  # 0 = 永久
        self.assertIn('永久', self.call('封号状态 b'))

    def test_timed_ban_expires_at_right_moment(self):
        now = int(time.time())
        out = self.call('封号 b 7')
        self.assertIn('已封号（', out)
        exp = self.expire_of('b')
        self.assertLessEqual(abs(exp - (now + 7 * DAY)), 10, '到期时间应为 now + 7 天')
        # 期限封号的回执要带剩余时间，让人知道还要等多久（具体文案见 _ban_left_text 单测）
        self.assertIn('剩余', self.call('签到', actor='b'))

    def test_expired_ban_auto_lifts(self):
        # 直接写一个过去的时间戳（1970），不冻结时钟也能验证「到期即失效」
        self.store.get_group('g')['banned'] = {'b': 1}
        self.assertFalse(self.plugin._is_banned('g', 'b'))
        self.assertEqual(self.banned(), {})
        self.assertNotEqual(self.call('签到', actor='b'), BAN_REPLY)
        self.assertIn('未被封号', self.call('封号状态 b'))

    def test_legacy_list_form_read_as_permanent(self):
        # v3.10.23 的中间形态（list）若已落档也不能崩：一律视为永久
        self.store.get_group('g')['banned'] = ['b']
        self.assertTrue(self.plugin._is_banned('g', 'b'))
        self.assertEqual(self.expire_of('b'), 0)
        self.assertEqual(self.call('签到', actor='b'), BAN_REPLY)

    def test_write_normalizes_legacy_list(self):
        self.store.get_group('g')['banned'] = ['pw']
        self.call('封号 c 3')
        raw = self.raw_banned()
        self.assertIsInstance(raw, dict, '写路径要把旧 list 形态规范化成 dict')
        self.assertEqual(raw.get('pw'), 0)
        self.assertIn('c', raw)

    def test_write_prunes_expired(self):
        # 写路径顺手清掉已到期条目，避免名单越积越多
        self.store.get_group('g')['banned'] = {'old': 1, 'keep': 0}
        self.call('封号 c 3')
        raw = self.raw_banned()
        self.assertNotIn('old', raw)
        self.assertEqual(raw.get('keep'), 0)

    def test_reban_retimes_with_new_days(self):
        self.call('封号 b 7')
        first = self.expire_of('b')
        self.call('封号 b 30')
        second = self.expire_of('b')
        self.assertGreater(second, first + 20 * DAY, '重复封号应按新天数重新计时（覆盖）')
        # 剩余时间的**文案**由 _ban_left_text 单测覆盖，这里只确认状态页把它显示出来了
        # （不比对上「30 天」这种字符串：跨秒执行时它会合理地变成「29 天 23 小时」）
        self.assertIn('剩余：', self.call('封号状态 b'))
        # 也可以缩短（用户确认过：覆盖为新天数，会缩短）
        self.call('封号 b 3')
        self.assertLess(self.expire_of('b'), second)
        self.assertEqual(len(self.banned()), 1, '重复封号不产生重复条目')

    def test_permanent_ban_not_downgraded_by_days(self):
        self.call('封号 b')
        out = self.call('封号 b 3')
        self.assertIn('已是**永久封号**', out)
        self.assertEqual(self.expire_of('b'), 0, '有限天数不得把永久封号降格')
        self.assertIn('永久', self.call('封号状态 b'))

    def test_explicit_permanent_command(self):
        self.assertIn('已封号（永久）', self.call('永久封号 b'))
        self.assertEqual(self.expire_of('b'), 0)
        self.assertEqual(self.call('签到', actor='b'), BAN_REPLY)

    def test_timed_reply_shows_remaining_but_permanent_uses_plain_text(self):
        # 限期封号要告诉对方还要等多久，否则只能反复试指令来猜
        self.call('封号 b 2')
        out = self.call('签到', actor='b')
        self.assertIn('剩余', out)
        self.assertNotEqual(out, BAN_REPLY)
        self.call('解封 b')
        self.call('封号 a')
        self.assertEqual(self.call('签到', actor='a'), BAN_REPLY)

    def test_left_text_formatting(self):
        # 直接打这个格式化函数，不比对上层的存储时间戳——避开秒级漂移带来的边界抖动
        f = self.plugin._ban_left_text
        now = int(time.time())
        self.assertEqual(f(0), '永久')
        self.assertEqual(f(now - 100), '已到期')
        self.assertEqual(f(now + 2 * DAY + 1800), '2 天')  # 整点后半小时，漂移不越界
        self.assertEqual(f(now + DAY + 5 * 3600 + 1800), '1 天 5 小时')
        self.assertEqual(f(now + 30 * 60 + 45), '30 分钟')
        self.assertEqual(f(now + 90), '1 分钟')  # 不足一分钟也别说「0 分钟」

    # ---------------- 大管理员豁免 ----------------

    def test_admin_cannot_be_banned(self):
        self.assertIn('大管理员不受封号影响', self.call('封号 root'))
        self.assertIn('大管理员不受封号影响', self.call('永久封号 root'))
        self.assertEqual(self.banned(), {})
        # 白名单里写的是绑定QQ号时也要认出来
        self.store.set_qq_binding('pid_x', 'root')
        self.assertIn('大管理员不受封号影响', self.call('封号 pid_x'))
        self.assertNotIn('pid_x', self.banned())

    def test_admin_bypasses_stale_ban_entry(self):
        # 「先被封、后晋升」：名单里留着旧记录，大管理员仍照常可用（也才能自己解封）
        self.store.get_group('g')['banned'] = {'root': 0}
        self.assertNotEqual(self.call('宠物列表', actor='root'), BAN_REPLY)

    # ---------------- 解封 ----------------

    def test_unban(self):
        self.call('封号 b 5')
        self.assertIn('已解除', self.call('解封 b'))
        self.assertEqual(self.banned(), {})
        self.assertNotEqual(self.call('签到', actor='b'), BAN_REPLY)
        self.assertIsNone(self.raw_banned().get('b'), '解封后不该留下残留条目')

    def test_unban_reports_when_not_banned(self):
        self.assertIn('未被封号', self.call('解封 b'))
        self.assertIn('未被封号', self.call('解封 ghost'))

    def test_unban_by_bound_qq_number(self):
        self.store.set_qq_binding('b', '10001')
        self.call('封号 10001 5')
        self.assertIn('已解除', self.call('解封 10001'))
        self.assertEqual(self.banned(), {})

    # ---------------- 查看封号状态 ----------------

    def test_status_for_one_user(self):
        self.assertIn('未被封号', self.call('封号状态 b'))
        self.call('封号 b 5')
        out = self.call('封号状态 b')
        self.assertIn('在本群已被封号', out)
        self.assertIn('剩余：', out)
        self.assertIn('解封 b', out)

    def test_status_lists_group(self):
        self.assertIn('没有被封号的用户', self.call('封号状态'))
        self.call('封号 b 5')
        self.call('封号 a')
        for cmd in ('封号状态', '封号列表'):
            out = self.call(cmd)
            self.assertIn('本群封号名单（2 人）', out)
            self.assertIn('`b`', out)
            self.assertIn('`a`', out)
            self.assertIn('剩余', out)   # 表头列
            self.assertIn('永久', out)   # 永久封号显示永久
        # 只列本群：别的群的封号不出现
        self.plugin._ban('g2', 'zzz')
        self.assertNotIn('zzz', self.call('封号状态'))

    # ---------------- 禁止收礼 ----------------

    def test_all_transfer_kinds_blocked_inbound(self):
        self.call('封号 b')
        # 物品/货币/宠物/坐骑四条转让路径都汇到 _check_transfer_limit
        for kind in ('item', 'coin', 'jifen', 'diamond', 'pet', 'mount'):
            err, _ = self.plugin._check_transfer_limit(self.a, self.b, 'g', 1, kind)
            self.assertIn('对方已被封号', err or '', kind)

    def test_timed_ban_also_blocks_transfer(self):
        self.call('封号 b 5')
        err, _ = self.plugin._check_transfer_limit(self.a, self.b, 'g', 1, 'item')
        self.assertIn('对方已被封号', err or '')

    def test_expired_ban_stops_blocking_transfer(self):
        self.store.get_group('g')['banned'] = {'b': 1}
        err, _ = self.plugin._check_transfer_limit(self.a, self.b, 'g', 1, 'item')
        self.assertNotIn('对方已被封号', err or '')

    def test_transfer_block_beats_exemptions(self):
        root = self.store.get_player('root', 'g')
        self.call('封号 b')
        # 大管理员当发方也照样拦：豁免的是发方的税与次数，不是收方能不能收
        err, _ = self.plugin._check_transfer_limit(root, self.b, 'g', 1, 'item')
        self.assertIn('对方已被封号', err or '')
        # 无限服也拦：封号是管理手段，不该因服类型失效
        self.store.get_group('g')['server_type'] = 'infinite'
        err, _ = self.plugin._check_transfer_limit(self.a, self.b, 'g', 1, 'item')
        self.assertIn('对方已被封号', err or '')
        # 反向（被封者给别人转）不在这里拦，而是被指令闸门整体挡掉
        self.assertNotIn('对方已被封号',
                         self.plugin._check_transfer_limit(self.b, self.a, 'g', 1, 'item')[0] or '')

    def test_unbanned_target_transfer_ok(self):
        err, _ = self.plugin._check_transfer_limit(self.a, self.b, 'g', 1, 'item')
        self.assertNotIn('对方已被封号', err or '')

    def test_group_staff_commands_blocked(self):
        # 禁言/撤回/踢人走的是 dispatch 之外的独立分支，dispatch 里的闸门拦不到，
        # 故由 _staff_cmd_blocked 单独判一次（这里直接打这个判据）
        self.call('封号 b')
        for text in ('禁言 @b', '解除禁言 @b', '全体禁言', '撤回 @b', '踢出 @b'):
            self.assertTrue(self.plugin._staff_cmd_blocked(text, 'g', 'b'), text)
        # 未封的人不受影响
        for text in ('禁言 @a', '撤回 @a', '踢出 @a'):
            self.assertFalse(self.plugin._staff_cmd_blocked(text, 'g', 'a'), text)
        # 只管这三类：普通聊天（含以「撤回」开头的闲聊）不在此列
        for text in ('哈哈哈', '撤回了一条消息真可惜', '禁言制度该改改了'):
            self.assertFalse(self.plugin._staff_cmd_blocked(text, 'g', 'b'), text)
        # 按群隔离 + 大管理员豁免
        self.assertFalse(self.plugin._staff_cmd_blocked('禁言 @b', 'g2', 'b'))
        self.plugin._ban('g', 'root')
        self.assertFalse(self.plugin._staff_cmd_blocked('禁言 @x', 'g', 'root'))
        # 到期后不再拦
        self.store.get_group('g')['banned'] = {'b': 1}
        self.assertFalse(self.plugin._staff_cmd_blocked('禁言 @b', 'g', 'b'))

    # ---------------- 口令抽奖（排在已知指令过滤之前，需单独拦）----------------

    def test_lottery_password_blocked(self):
        self.store.set_lottery({
            'enabled': True, 'password': '天选之人', 'drawn': False,
            'start_at': 0, 'draw_at': 2 ** 31,
        })
        self.assertNotEqual(self.call('天选之人', actor='a'), BAN_REPLY)
        self.call('封号 b')
        self.assertEqual(self.call('天选之人', actor='b'), BAN_REPLY)

    # ---------------- 参数校验 ----------------

    def test_bad_args(self):
        for text in ('封号', '解封', '封号   ', '永久封号', '永久封号   '):
            self.assertIn('用法：', self.call(text), text)
        self.assertEqual(self.banned(), {})

    def test_bad_days_rejected_not_silently_permanent(self):
        # 天数写错宁可报错，也不要静默当成永久——那是「本想封3天、结果封到天荒地老」
        for text in ('封号 b 0', '封号 b -3', '封号 b abc', '封号 b 3.5', '封号 b 一'):
            self.assertIn('用法：', self.call(text), text)
            self.assertEqual(self.banned(), {}, text)
        # 永久封号不接受天数
        self.assertIn('用法：', self.call('永久封号 b 3'))
        self.assertEqual(self.banned(), {})
        # 带 + 号的正整数照收
        self.assertIn('已封号', self.call('封号 b +3'))


if __name__ == '__main__':
    unittest.main()
