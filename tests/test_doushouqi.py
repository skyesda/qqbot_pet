import tempfile
import time
import unittest
from pathlib import Path

from petpark.boardgames import BoardGames, COMMANDS, ai_move, game_help
from petpark.doushouqi import Doushouqi, render_doushouqi


class DoushouqiTests(unittest.TestCase):
    def test_initial_position_and_basic_moves(self):
        board = Doushouqi.initial()
        self.assertEqual(sum(piece > 0 for piece in board), 8)
        self.assertEqual(sum(piece < 0 for piece in board), 8)
        self.assertEqual({abs(piece) for piece in board if piece}, set(range(1, 9)))
        self.assertIn((42, 35), Doushouqi.moves(board, 1))
        self.assertNotIn((56, 59), Doushouqi.moves(board, 1))

    def test_rat_river_capture_and_elephant_cycle(self):
        board = [0] * 63
        board[21], board[22] = 1, -1
        self.assertNotIn(22, Doushouqi.destinations(board, 21))
        board = [0] * 63
        board[21], board[28] = 8, -1
        self.assertNotIn(28, Doushouqi.destinations(board, 21))
        board[21], board[28] = 1, -8
        self.assertIn(28, Doushouqi.destinations(board, 21))

    def test_lion_tiger_jump_and_rat_block(self):
        board = [0] * 63
        board[24] = 7
        self.assertIn(27, Doushouqi.destinations(board, 24))
        board[25] = -1
        self.assertNotIn(27, Doushouqi.destinations(board, 24))

    def test_traps_dens_and_winner(self):
        board = [0] * 63
        board[9], board[10] = -1, 8
        self.assertIn(10, Doushouqi.destinations(board, 9))
        board = [0] * 63
        board[10] = 1
        self.assertIn(3, Doushouqi.destinations(board, 10))
        won = Doushouqi.apply(board, (10, 3), 1)
        self.assertEqual(Doushouqi.winner(won), 1)

    def test_ai_and_short_move_session(self):
        for difficulty in range(1, 5):
            board = Doushouqi.initial()
            before = board[:]
            started = time.perf_counter()
            self.assertIn(ai_move("斗兽棋", board, -1, difficulty), Doushouqi.moves(board, -1))
            self.assertEqual(board, before)
            self.assertLess(time.perf_counter() - started, 3)
        with tempfile.TemporaryDirectory() as tmp:
            games = BoardGames(Path(tmp) / "s.json", Path(tmp), str, str,
                               lambda group, user: ({"qq": user}, None))
            games.render = lambda room: "board"
            games.handle("g", "a", ["斗兽棋双人", "b"])
            games.handle("g", "b", ["接受棋局"])
            room = games.room_for("g", "a")
            games.handle("g", "a", ["落", "a7", "a6"])
            self.assertEqual(room["board"][35], 8)
            self.assertIn("斗兽棋单人", COMMANDS)
            self.assertIn("兽穴", game_help("斗兽棋"))

    def test_render(self):
        image = render_doushouqi({"board": Doushouqi.initial(), "last": (42, 35)})
        self.assertEqual(image.size, (840, 1080))


if __name__ == "__main__":
    unittest.main()
