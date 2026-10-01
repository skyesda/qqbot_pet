import asyncio
import ast
import copy
import json
import unittest
import types
from pathlib import Path
from unittest.mock import patch

from test_moonfest_challenges import _EngineCase, _FakeBot
from qqbot_pet.petpark.moonfest.engine import MoonfestActivity
from qqbot_pet.petpark.moonfest.national_pool import (
    START, END, DRAW, TOTAL, STRIDES, questions, normalize, weighted_shares,
)


class NationalPoolTests(_EngineCase):
    def setUp(self):
        super().setUp()
        self.now = START + 1
        self.act._now = lambda: self.now
        self.phase = 'both'
        self.pool = self.act.national_pool

    def active(self):
        return self.ap()['national_round']['active']

    def correct(self):
        return 'ABCD'[questions()[self.active()['index']]['answer']]

    def answer(self, amount=100):
        self.say('国庆快乐')
        answer = self.correct()
        with patch('qqbot_pet.petpark.moonfest.national_pool.random.randint', return_value=amount):
            return self.say('国庆快乐 ' + answer)

    def test_exact_hour_boundaries_and_single_day(self):
        self.now = START-1
        self.say('国庆快乐')
        self.assertNotIn('national_round', self.ap())
        self.now = START
        self.assertIn('剩余 **30 秒**', self.say('国庆快乐'))
        cursor = self.ap()['national_round']['cursor']
        for timestamp in [END, END+86400]:
            self.now = timestamp
            self.say('国庆快乐')
            self.assertEqual(self.ap()['national_round']['cursor'], cursor)

    def test_repeated_draw_and_cross_group_do_not_reset_or_create_another_question(self):
        self.say('国庆快乐')
        active = copy.deepcopy(self.active())
        self.now += 20
        self.assertIn('剩余 **10 秒**', self.say('国庆快乐'))
        self.act.dispatch(None, self.qq, '99999', '国庆快乐')
        self.assertEqual(self.active(), active)

    def test_correct_answer_cap_and_no_cooldown(self):
        for _ in range(3):
            self.assertIn('月华 ×100', self.answer())
        self.assertEqual(self.earned(), 300)
        self.assertEqual(self.pool.state()['spent'][0], 300)

    def test_duplicate_answer_never_pays_twice(self):
        self.say('国庆快乐')
        answer = self.correct()
        self.say('国庆快乐 ' + answer)
        earned = self.earned()
        self.assertIn('没有待答题目', self.say('国庆快乐 ' + answer))
        self.assertEqual(self.earned(), earned)

    def test_30_second_deadline_and_wrong_answer(self):
        self.say('国庆快乐')
        answer = self.correct()
        self.now += 30
        self.assertIn('超过 **30 秒**', self.say('国庆快乐 ' + answer))
        self.assertEqual(self.earned(), 0)
        self.say('国庆快乐')
        self.assertIn('未答对', self.say('国庆快乐 不正确的答案'))
        self.assertEqual(self.earned(), 0)

    def test_partial_final_payment_and_hour_exhaustion(self):
        self.pool.state()['spent'][0] = 9997
        self.assertIn('月华 ×3', self.answer())
        self.assertEqual(self.earned(), 3)
        self.assertEqual(self.pool.state()['spent'][0], 10000)
        self.assertIn('本小时奖池已发完', self.say('国庆快乐'))

    def test_cross_hour_answer_uses_current_hour_and_rolls_unused_funds(self):
        self.now = START+3590
        self.say('国庆快乐')
        answer = self.correct()
        self.now = START+3601
        with patch('qqbot_pet.petpark.moonfest.national_pool.random.randint', return_value=100):
            self.say('国庆快乐 ' + answer)
        state = self.pool.state()
        self.assertEqual(state['spent'][0], 0)
        self.assertEqual(state['spent'][1], 100)
        self.assertEqual(sum(state['budgets']), TOTAL)

    def test_roll_is_exact_idempotent_and_recovers_skipped_hours(self):
        state = self.pool.state()
        state['spent'][0] = 1000
        self.pool.roll(START+3600)
        self.assertEqual(state['budgets'], [1000]+[11000]*9)
        saved = copy.deepcopy(state)
        self.pool.roll(START+3600)
        self.assertEqual(state, saved)
        self.pool.roll(END)
        self.assertEqual(state['budgets'][:-1], [1000]+[0]*8)
        self.assertEqual(state['budgets'][-1], 99000)
        self.assertEqual(sum(state['budgets']), TOTAL)

    def test_question_survives_restart_and_payout_does_not_reinitialize(self):
        self.answer(50)
        self.say('国庆快乐')
        active = copy.deepcopy(self.active())
        restored = MoonfestActivity(_FakeBot(), Path(self.tmp.name))
        restored._now = lambda: self.now
        restored._spawn = lambda coro: coro.close()
        restored.dispatch(None, self.qq, self.gid, '国庆快乐')
        self.assertEqual(restored._get_player(self.gid, self.qq)['national_round']['active'], active)
        self.assertEqual(restored.national_pool.state()['spent'][0], 50)

    def test_failed_atomic_save_rolls_back_credit_and_pool_debit(self):
        self.say('国庆快乐')
        answer = self.correct()
        before = copy.deepcopy(self.act._data)
        with patch.object(Path, 'replace', side_effect=OSError('simulated disk failure')):
            self.assertIn('保存失败', self.say('国庆快乐 ' + answer))
        self.assertEqual(self.act._data, before)

    def test_unique_question_cycle_has_1000_distinct_indices(self):
        for stride in STRIDES:
            self.assertEqual(len({(37+i*stride) % 1000 for i in range(1000)}), 1000)

    def test_weighted_draw_conserves_the_remaining_pool_and_is_idempotent(self):
        self.answer(100)
        state = self.pool.state()
        state['counts'] = {'20001': 1, '20002': 3}
        state['participants'] = {'20001': {'group': self.gid}, '20002': {'group': self.gid}}
        self.now = DRAW
        messages = self.pool.tick()
        self.assertIn('开奖', messages[0])
        self.assertEqual(state['allocations'], {'20001': 24975, '20002': 74925})
        self.assertEqual(sum(state['spent'])+sum(state['allocations'].values()), TOTAL)
        earned = self.earned()
        self.assertEqual(self.pool.tick(), [])
        self.assertEqual(self.earned(), earned)
        restored = MoonfestActivity(_FakeBot(), Path(self.tmp.name))
        restored._now = lambda: DRAW+100
        self.assertEqual(restored.national_pool.tick(), [])
        self.assertEqual(restored._get_player(self.gid, self.qq)['yuehua_earned'], earned)

    def test_no_valid_blessings_does_not_award_unrelated_players(self):
        self.now = DRAW
        self.pool.tick()
        self.assertEqual(self.pool.state()['allocations'], {})
        self.assertEqual(self.earned(), 0)

    def test_largest_remainders_are_exact_even_for_tiny_balances(self):
        for amount in [0, 1, 2, 7, 99999, 100000]:
            shares = weighted_shares(amount, {'a': 1, 'b': 2, 'c': 3})
            self.assertEqual(sum(shares.values()), amount)
            self.assertTrue(all(x >= 0 for x in shares.values()))

    def test_bank_is_1000_unique_valid_questions_with_explanations(self):
        bank = questions()
        self.assertEqual(len(bank), 1000)
        self.assertEqual(len({q['id'] for q in bank}), 1000)
        self.assertEqual(len({q['question'] for q in bank}), 1000)
        for q in bank:
            self.assertEqual(len(set(q['options'])), 4)
            self.assertIn(q['answer'], range(4))
            self.assertTrue(q['explanation'])

    def test_blessings_exact_semantic_duplicate_and_deadline(self):
        self.now = END
        with patch.object(self.pool, 'review_blessing', return_value='accepted'):
            out = asyncio.run(self.pool.blessing_message(None, self.qq, self.gid, '祝祖国繁荣昌盛！', explicit=True))
            self.assertIn('祝福已收录', out)
            out = asyncio.run(self.pool.blessing_message(None, '20002', self.gid, '祝 祖国繁荣昌盛。', explicit=True))
            self.assertIn('不重复计入', out)
        with patch.object(self.pool, 'review_blessing', return_value='duplicate'):
            out = asyncio.run(self.pool.blessing_message(None, self.qq, self.gid, '愿祖国昌盛繁荣', explicit=True))
            self.assertIn('相似度达到 80%', out)
        self.assertEqual(self.pool.state()['counts'][self.qq], 1)
        self.now = DRAW
        asyncio.run(self.pool.blessing_message(None, self.qq, self.gid, '国庆快乐', explicit=True))
        self.assertEqual(self.pool.state()['counts'][self.qq], 1)

    def test_audit_unavailable_and_unrelated_do_not_give_weights(self):
        self.now = END
        for decision in [None, 'irrelevant']:
            with patch.object(self.pool, 'review_blessing', return_value=decision):
                asyncio.run(self.pool.blessing_message(None, self.qq, self.gid, '测试消息', explicit=True))
        self.assertEqual(self.pool.state()['counts'], {})

    def test_jev_only_checks_suitability_not_semantic_novelty(self):
        self.act.jev.enabled = True
        with patch.object(self.act.jev, 'available', return_value=True), patch.object(self.act.jev, '_ask', return_value={
            'original': {'noul': .1}, 'suitable': {'noul': .98},
        }) as ask:
            self.assertEqual(self.pool.review_blessing('愿祖国繁荣昌盛', [f'祝福{i}' for i in range(150)]), 'accepted')
            self.assertEqual(ask.call_count, 1)
            self.assertEqual(set(ask.call_args.args[1]), {'suitable'})
            self.assertNotIn('既有有效祝福', ask.call_args.args[0])

    def test_text_similarity_threshold_and_normalization(self):
        from petpark.moonfest.national_pool import is_duplicate_blessing
        self.assertTrue(is_duplicate_blessing('abcdefghij', ['abcdefghXY']))
        self.assertFalse(is_duplicate_blessing('abcdefghij', ['abcdefgXYZ']))
        self.assertTrue(is_duplicate_blessing('祝 祖国繁荣昌盛！', ['祝祖国繁荣昌盛。']))
        self.assertFalse(is_duplicate_blessing('祝祖国繁荣昌盛', ['愿华夏山河锦绣，百姓安居乐业']))
        self.assertTrue(is_duplicate_blessing('abcdefghij', ['无关内容']*150+['abcdefghXY']))
        with patch.object(self.act.jev, '_ask') as ask:
            self.assertEqual(self.pool.review_blessing('abcdefghij', ['abcdefghXY']), 'duplicate')
            ask.assert_not_called()

    def test_two_simultaneous_identical_blessings_count_once(self):
        self.now = END
        async def run():
            return await asyncio.gather(
                self.pool.blessing_message(None, self.qq, self.gid, '国庆快乐，祝祖国繁荣昌盛', explicit=True),
                self.pool.blessing_message(None, '20002', self.gid, '国庆快乐，祝祖国繁荣昌盛', explicit=True))
        with patch.object(self.pool, 'review_blessing', return_value='accepted'):
            asyncio.run(run())
        self.assertEqual(sum(self.pool.state()['counts'].values()), 1)

    def test_all_ten_hours_plus_blessing_draw_never_exceed_total(self):
        for hour in range(10):
            self.now = START+hour*3600+1
            for _ in range(7):
                self.answer(100)
        state = self.pool.state()
        self.assertEqual(sum(state['spent']), 7000)
        self.assertEqual(sum(state['budgets']), TOTAL)
        state['counts'] = {self.qq: 7}
        state['participants'] = {self.qq: {'group': self.gid}}
        self.now = DRAW
        self.pool.tick()
        self.assertEqual(self.earned(), TOTAL)
        self.assertIn('尚未分配 **0**', self.pool.status(self.qq))


class NationalBlessingRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root/'main.py').read_text(encoding='utf-8'))
        method = next(n for cls in tree.body if isinstance(cls, ast.ClassDef)
                      for n in cls.body if getattr(n, 'name', '') == '_national_blessing_message')
        module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
        env = {'KNOWN_COMMANDS': {'国庆快乐', '祝福时刻', '拜月'}}
        exec(compile(module, 'main.py', 'exec'), env)
        self.calls = []
        async def collect(*args, **kwargs):
            self.calls.append((args, kwargs))
            return 'accepted'
        self.pool = types.SimpleNamespace(blessing_time=lambda: True, blessing_message=collect)
        self.authorized, self.banned, self.agreement, self.bound = True, False, True, True
        self.host = types.SimpleNamespace(
            moonfest=types.SimpleNamespace(national_pool=self.pool),
            _is_group=lambda group: group != 'private',
            _is_group_authorized=lambda group: self.authorized,
            store=types.SimpleNamespace(get_group=lambda group: {'enabled': True}),
            _is_banned=lambda group, qq: self.banned, _is_admin_id=lambda qq: False,
            _is_admin=lambda event: False,
            _agreement_block=lambda qq, cmd: None if self.agreement else 'blocked',
            _qq_bind_block=lambda qq, cmd: None if self.bound else 'blocked',
        )
        self.route = env['_national_blessing_message']

    async def test_freeform_and_explicit_blessings_route_without_consuming_other_commands(self):
        for text in ['祝祖国繁荣昌盛', '国庆快乐 祝祖国国泰民安', '祝福时刻 愿山河锦绣']:
            self.assertEqual(await self.route(self.host, None, 'qq', 'group', text), 'accepted')
        self.assertIsNone(await self.route(self.host, None, 'qq', 'group', '拜月'))
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.calls[-1][0][-1], '愿山河锦绣')

    async def test_normal_game_gates_and_private_messages_are_not_bypassed(self):
        for field, value in [('authorized', False), ('banned', True), ('agreement', False), ('bound', False)]:
            original = getattr(self, field)
            setattr(self, field, value)
            self.assertIsNone(await self.route(self.host, None, 'qq', 'group', '祝祖国繁荣昌盛'))
            setattr(self, field, original)
        self.assertIsNone(await self.route(self.host, None, 'qq', 'private', '祝祖国繁荣昌盛'))
        self.pool.blessing_time = lambda: False
        self.assertIsNone(await self.route(self.host, None, 'qq', 'group', '祝祖国繁荣昌盛'))
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
