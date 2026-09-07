import tempfile
import time
import unittest
from pathlib import Path

from petpark.boardgames import BoardGames, coord, game_help
from petpark.go import Go, go_ai, render_go


class GoTests(unittest.TestCase):
    def test_coords_and_group(self):
        self.assertEqual(coord('S19', 19, 19), 360)
        for value in ('t1', 's20', 'a0', 'a01'):
            with self.assertRaises(ValueError):
                coord(value, 19, 19)
        b = Go.initial()
        b[0] = b[1] = 1
        self.assertEqual(Go.group(b, 0), ({0, 1}, {2, 19, 20}))

    def test_capture_and_suicide(self):
        b = Go.initial()
        b[0], b[1], b[19] = -1, -1, 1
        b[2] = 1
        nxt = Go.apply(b, 20, 1)
        self.assertEqual(nxt[:2], [0, 0])
        self.assertEqual(b[0], -1)
        b = Go.initial()
        b[1] = b[19] = -1
        with self.assertRaises(ValueError):
            Go.apply(b, 0, 1)
        # Capturing into an otherwise surrounded point is legal.
        b[2] = b[20] = 1
        self.assertEqual(Go.apply(b, 0, 1)[1], 0)

    def test_ko_and_history(self):
        b = Go.initial()
        for i in (1, 19, 39):
            b[i] = 1
        for i in (20, 2, 22, 40):
            b[i] = -1
        taken = Go.legal_result(b, 21, 1, [Go.key(b)])
        self.assertEqual(taken[20], 0)
        with self.assertRaises(ValueError):
            Go.legal_result(taken, 20, -1, [Go.key(b), Go.key(taken)])
        self.assertEqual(Go.legal_result(taken, None, -1, [Go.key(taken)]), taken)
        self.assertNotIn(20, Go.moves(taken, -1, [Go.key(b)]))

    def test_area_neutral_and_komi(self):
        self.assertEqual(Go.score(Go.initial()), (0, 7.5))
        b = Go.initial()
        b[1] = b[19] = 1
        b[360] = -1
        self.assertEqual(Go.score(b), (3, 8.5))
        b = [1] * 361
        b[0] = 0
        self.assertEqual(Go.score(b), (361, 7.5))

    def test_ai_legal_and_does_not_mutate(self):
        b = Go.initial()
        for i in (60, 300, 72, 288, 180, 181, 161, 200):
            b = Go.apply(b, i, 1 if b.count(1) == b.count(-1) else -1)
        history = [Go.key(b)]
        before = b[:]
        for difficulty in (1, 2, 3, 4):
            start = time.perf_counter()
            move = go_ai(b, -1, difficulty, history)
            Go.legal_result(b, move, -1, history)
            self.assertEqual(before, b)
            self.assertLess(time.perf_counter() - start, 4)
        self.assertIsNone(go_ai(Go.initial(), -1, 2, [], 1))

    def test_session_turn_pass_restore_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = (Path(tmp) / 's.json', Path(tmp), str, str, lambda g, u: ({'qq': u}, None))
            games = BoardGames(*args)
            games.render = lambda r: 'image'
            command = lambda u, text: games.handle('g', u, text.split())
            self.assertIn('7.5', game_help('围棋'))
            command('a', '围棋双人 b')
            self.assertIn('尚未接受', command('a', '围棋停一手'))
            command('b', '接受棋局')
            room = games.room_for('g', 'a')
            self.assertIn('还没轮到', command('b', '围棋停一手'))
            command('a', '围棋落子 d4')
            self.assertEqual(room['last'], 60)
            command('b', '围棋停一手')
            deadline = room['deadline']
            self.assertIn('已有棋子', command('a', '围棋落子 d4'))
            self.assertEqual(room['deadline'], deadline)
            self.assertEqual(room['passes'], 1)
            command('a', '围棋落子 q16')
            self.assertEqual(room['passes'], 0)
            restored = BoardGames(*args).room_for('other', 'a')
            self.assertEqual(restored['go_history'], room['go_history'])
            self.assertEqual(restored['captures'], room['captures'])
            command('b', '围棋过')
            command('a', '围棋停一手')
            self.assertEqual(room['status'], 'finished')
            self.assertEqual(room['winner'], 'a')
            self.assertIn('盘面面积', room['result'])
            stats = str(games.state['stats'])
            command('a', '围棋停一手')
            self.assertEqual(str(games.state['stats']), stats)

    def test_single_player_pass_end_and_capture_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            games = BoardGames(Path(tmp) / 's.json', Path(tmp), str, str, None)
            games.render = lambda r: 'image'
            games.handle('g', 'a', ['围棋单人', '2'])
            room = games.room_for('g', 'a')
            games.handle('g', 'a', ['围棋停一手'])
            self.assertEqual(room['winner'], '@AI')
            self.assertEqual(room['moves'], 2)
            games.start(room)
            room['board'][0], room['board'][1] = -1, 1
            games.play_go(room, 19)
            self.assertEqual(room['captures']['1'], 1)

    def test_render(self):
        img = render_go({'board': Go.initial(), 'last': 360})
        self.assertEqual(img.size, (1000, 1060))


if __name__ == '__main__':
    unittest.main()
