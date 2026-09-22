"""数据追溯 / 异常检测「三件套」：append-only 审计流水（哈希链锚定）+ 规则异常检测 + 后台工具。

设计要点（2026-09-22）：
- 指令流水在 dispatch 已知指令过滤之后（4490-4502）落，普通聊天与被拦指令不落；
- 资源流水带 before/after/delta，挂在真正的资源变动 handler（如 _admin_adjust 的 admin_coin）；
- 每条流水带 prev = 上一条的 SHA-256，任意一条被篡改 audit_verify() 即报断链；
- 流水有界环形（audit_cap 默认 20000，超限丢最旧）；
- 轻量实时规则（_audit_live_rules，O(1)）与全量扫描（audit_scan，5 条规则）命中都落 audit_flags；
- 同规则同 pid 只留一条 open（去重）；audit_enabled 关闭时不落任何日志。
"""
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'petbot_framework' / 'compat'))
from qqbot_pet.main import (
    KNOWN_COMMANDS, PetParkPlugin,
)
from qqbot_pet.petpark.store import PetStore

# 一个能穿过全部拦截链、且放行后能在无完整 init 的测试插件上跑到底的指令
# （_bind_tutorial 返回静态文本，无资源副作用，不会触发实时规则）。
CMD = '绑定教程'


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = PetStore(Path(self.tmp.name) / 'data.json')
        self.plugin = PetParkPlugin.__new__(PetParkPlugin)
        self.plugin.store = self.store
        self.plugin.admins = {'root'}
        self.plugin.require_qq_bind = False   # 绑定/协议拦截自有测试，这里只验审计
        self.plugin.require_agreement = False
        self.plugin.audit_enabled = True
        self.plugin._active_event_commands = lambda: set()
        self.plugin.zhongyuan = None
        self.plugin._zy_commands = set()
        # 指令流水排在群授权校验之后，测试群里得先有有效授权
        self.store.get_group('g')['auth_until'] = int(time.time()) + 86400
        self.store.get_group('g2')['auth_until'] = int(time.time()) + 86400
        self.store.get_player('a', 'g')
        self.store.get_player('b', 'g')
        self.store._flush()

    def call(self, text, actor='b', group='g'):
        event = SimpleNamespace(get_sender_id=lambda: actor)
        return self.plugin.dispatch(event, actor, group, text)

    def log(self):
        return self.store._data['audit_log']

    def flags(self):
        return self.store._data['audit_flags']

    # ---------------- ① 追加 + 有界裁剪 ----------------

    def test_append_and_ring_cap(self):
        self.assertIn(CMD, KNOWN_COMMANDS, '指令必须注册进 KNOWN_COMMANDS，否则被过滤层吞掉')
        self.store._data['audit_cap'] = 3
        for i in range(1, 5):   # 4 条，cap 3 → 最旧一条被丢
            self.store._audit({"action": "cmd", "group": "g", "pid": "p1", "qq": "p1",
                               "detail": str(i)})
        log = self.log()
        self.assertEqual(len(log), 3)
        self.assertEqual(log[0]['detail'], '2', '最旧一条（detail=1）应被环形裁剪丢弃')
        self.assertEqual(log[-1]['detail'], '4')
        # 裁剪只是丢最旧、不改哈希链，整链仍可校验
        self.assertTrue(self.store.audit_verify()['ok'])

    # ---------------- ② 哈希链锚定 ----------------

    def test_hash_chain_and_tamper_detection(self):
        for i in range(3):
            self.store._audit({"action": "cmd", "group": "g", "pid": "p1", "qq": "p1",
                               "detail": f"cmd{i}"})
        log = self.log()
        self.assertEqual(log[0]['prev'], '', '首条 prev 必须为空串')
        self.assertEqual(len(log[1]['prev']), 64, '第二条 prev 应为 SHA-256 hex')
        self.assertTrue(self.store.audit_verify()['ok'])
        # 篡改中间一条（delta/ts 都行）→ 断链报错指向下一条
        log[1]['delta'] = 999
        res = self.store.audit_verify()
        self.assertFalse(res['ok'])
        self.assertEqual(res['broken_index'], 2, '改 index1 后 index2 的 prev 失配，断点应在 index2')

    # ---------------- ③ dispatch 指令流水 ----------------

    def test_dispatch_logs_real_cmd_only(self):
        before = len(self.log())
        out = self.call(CMD)
        self.assertIn('绑定QQ教程', out or '')
        self.assertEqual(len(self.log()), before + 1, '真指令应落一条指令流水')
        last = self.log()[-1]
        self.assertEqual(last['action'], 'cmd')
        self.assertEqual(last['pid'], 'b')
        self.assertEqual(last['group'], 'g')
        self.assertIn(CMD, last['detail'])

    def test_plain_chat_not_logged(self):
        before = len(self.log())
        self.assertIsNone(self.call('今天天气不错'))
        self.assertEqual(len(self.log()), before, '普通聊天不得落指令流水')

    # ---------------- ④ 资源流水（admin_coin）----------------

    def test_admin_adjust_resource_log(self):
        before = len(self.log())
        event = SimpleNamespace(get_sender_id=lambda: 'root')
        out = self.plugin._admin_adjust(event, 'root', 'g', '加金币', ['加金币', 'b', '100'])
        self.assertIn('增加', out or '')
        self.assertEqual(len(self.log()), before + 1, '加金币应落一条资源流水')
        last = self.log()[-1]
        self.assertEqual(last['action'], 'admin_coin')
        self.assertEqual(last['by'], 'root')
        self.assertEqual(last['currency'], '金币')
        self.assertEqual(last['delta'], 100)
        self.assertEqual(last['after'] - last['before'], 100)

    # ---------------- ⑤ 轻量实时规则（big_single）+ 去重 ----------------

    def test_live_rule_big_single_and_dedup(self):
        self.store._audit_rule_big_single = 1000
        self.store._audit({"action": "admin_coin", "group": "g", "pid": "p1", "qq": "p1",
                           "delta": 5000})
        f = next(iter(self.flags().values()))
        self.assertEqual(f['type'], 'big_single')
        self.assertEqual(f['severity'], 'high')
        self.assertEqual(f['status'], 'open')
        self.assertEqual(f['pid'], 'p1')
        # 同 pid 再命中：去重，仍只一条 open
        self.store._audit({"action": "admin_coin", "group": "g", "pid": "p1", "qq": "p1",
                           "delta": 7000})
        self.assertEqual(len(self.flags()), 1, '同规则同 pid 已有一条 open，不得重复标记')
        # 换 pid：新目标，落第二条
        self.store._audit({"action": "admin_coin", "group": "g", "pid": "p2", "qq": "p2",
                           "delta": 5000})
        self.assertEqual(len(self.flags()), 2)
        # 未超限的不落
        self.store._audit({"action": "admin_coin", "group": "g", "pid": "p3", "qq": "p3",
                           "delta": 500})
        self.assertEqual(len(self.flags()), 2)

    # ---------------- ⑥ audit_scan 全量扫描 ----------------

    def test_scan_flags_newbie_fat_and_big_single(self):
        pl = self.store.get_player('newbie', 'g')
        pl['created_ts'] = int(time.time()) - 3600   # 注册不足 24h
        # 3 条单次 4e8：实时阈值默认 1e9 不命中；累计 1.2e9 > 新号阈值 1e8
        for _ in range(3):
            self.store._audit({"action": "sign", "group": "g", "pid": "newbie",
                               "qq": "newbie", "delta": 400_000_000})
        self.assertEqual(len(self.flags()), 0, '实时阈值（默认 1e9）不应命中单条 4e8')
        # 把单次阈值降到 1e8 后全量扫描：big_single 与 newbie_fat 各落一条
        self.store._audit_rule_big_single = 100_000_000
        res = self.store.audit_scan()
        self.assertEqual(res['new_flags'], 2, 'big_single + newbie_fat 各新增一条')
        types = {f['type'] for f in self.flags().values()}
        self.assertIn('big_single', types)
        self.assertIn('newbie_fat', types)
        self.assertEqual(res['open_count'], len(self.flags()))

    # ---------------- ⑦ 开关关闭 ----------------

    def test_disabled_switch_logs_nothing(self):
        self.plugin.audit_enabled = False
        before = len(self.log())
        out = self.call(CMD)
        self.assertIn('绑定QQ教程', out or '')
        self.assertEqual(len(self.log()), before, 'audit_enabled=False 时指令照常执行但不得落日志')


if __name__ == '__main__':
    unittest.main()
