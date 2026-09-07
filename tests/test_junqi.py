import tempfile
import time
import unittest
from collections import Counter
from pathlib import Path

from petpark.boardgames import BoardGames, ai_move, game_help, COMMANDS
from petpark.junqi import Junqi, render_junqi


class JunqiTests(unittest.TestCase):
    def empty(self):
        b = [0] * 60
        b[1], b[58] = -12, 12
        return b

    def test_deployment(self):
        b = Junqi.initial()
        expected = {1: 3, 2: 3, 3: 3, 4: 2, 5: 2, 6: 2, 7: 2, 8: 1, 9: 1, 10: 3, 11: 2, 12: 1}
        for side in (-1, 1):
            self.assertEqual(Counter(abs(p) for p in b if p * side > 0), expected)
        self.assertTrue(all(not b[i] for i in Junqi.camps))
        self.assertTrue(all(i in Junqi.headquarters for i, p in enumerate(b) if abs(p) == 12))
        self.assertTrue(all(i // 5 in (0, 1, 10, 11) for i, p in enumerate(b) if abs(p) == 10))
        self.assertFalse(any(abs(b[i]) == 11 for i in range(25, 35)))

    def test_rail_turns_blockers_and_middle(self):
        b = self.empty()
        b[5] = 2
        self.assertIn(25, Junqi.destinations(b, 5))
        self.assertNotIn(27, Junqi.destinations(b, 5))
        b[5] = 1
        self.assertIn(27, Junqi.destinations(b, 5))
        b[10], b[6] = 3, -9
        self.assertNotIn(27, Junqi.destinations(b, 5))
        self.assertIn(6, Junqi.destinations(b, 5))
        self.assertNotIn(7, Junqi.destinations(b, 5))
        self.assertNotIn(31, Junqi.road_neighbors(26))
        self.assertIn(32, Junqi.road_neighbors(27))

    def test_camp_and_headquarters(self):
        b = self.empty()
        b[5], b[11] = 1, -9
        self.assertNotIn(11, Junqi.destinations(b, 5))
        b[11] = 0
        self.assertIn(11, Junqi.destinations(b, 5))
        b[3] = 9
        self.assertEqual(Junqi.destinations(b, 3), [])
        b[5] = 10
        self.assertEqual(Junqi.destinations(b, 5), [])

    def test_combat(self):
        for attacker, defender, survivor in ((9, -8, 9), (2, -8, -8), (4, -4, 0),
                (1, -10, 1), (9, -10, -10), (11, -10, 0), (8, -11, 0),
                (11, -12, 0), (1, -12, 1)):
            b = self.empty()
            b[25], b[30] = attacker, defender
            result = Junqi.apply(b, (25, 30), 1)
            self.assertEqual((result[25], result[30]), (0, survivor))
            self.assertEqual(b[25], attacker)

    def test_ai_legal_and_tactical_flag(self):
        for difficulty in (1, 2, 3, 4):
            b = Junqi.initial()
            before = b[:]
            start = time.perf_counter()
            self.assertIn(ai_move('军棋', b, -1, difficulty), Junqi.moves(b, -1))
            self.assertEqual(b, before)
            self.assertLess(time.perf_counter() - start, 3)
        b = self.empty()
        b[6], b[50] = 1, -1
        self.assertEqual(ai_move('军棋', b, 1, 2), (6, 1))

    def test_sessions_restore_and_settle(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = (Path(tmp) / 'state.json', Path(tmp), str, str, lambda g, u: ({'qq': u}, None))
            games = BoardGames(*args)
            games.render = lambda r: 'preview'
            command = lambda user, text: games.handle('g', user, text.split())
            self.assertIn('军棋单人', COMMANDS)
            self.assertIn('铁路', game_help('军棋'))
            command('a', '军棋双人 b')
            command('b', '接受棋局')
            room = games.room_for('g', 'a')
            command('a', '军棋落子 a7 a6')
            self.assertEqual(room['moves'], 1)
            self.assertIn('a7 → a6', games.view(room)[0])
            restored = BoardGames(*args)
            self.assertEqual(restored.room_for('g', 'a')['board'], room['board'])
            room['board'] = self.empty()
            room['board'][6], room['board'][50] = 1, -1
            room['turn'] = 1
            command('a', '军棋落子 b2 b1')
            self.assertEqual(room['winner'], 'a')
            self.assertIn('夺取', room['result'])
            self.assertEqual(room['status'], 'finished')

    def test_no_moves_and_draw(self):
        with tempfile.TemporaryDirectory() as tmp:
            games = BoardGames(Path(tmp) / 's.json', Path(tmp), str, str, None)
            room = {'kind': '军棋', 'players': ['a', 'b'], 'difficulty': 1}
            games.start(room)
            room['board'] = self.empty()
            room['board'][25] = 1
            games.play(room, (25, 30))
            self.assertEqual(room['winner'], 'a')
            games.start(room)
            room['board'] = self.empty()
            room['board'][25], room['board'][50] = 1, -1
            room['quiet'] = 119
            games.play(room, (25, 30))
            self.assertIsNone(room['winner'])
            self.assertEqual(room['status'], 'finished')

    def test_production_render(self):
        img = render_junqi({'board': Junqi.initial(), 'last': [30, 25]})
        self.assertEqual(img.size, (760, 1180))


if __name__ == '__main__':
    unittest.main()
