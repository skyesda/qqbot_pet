import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from petpark.boardgames import BoardGames, Gomoku, Xiangqi, ai_move, coord


class RulesTests(unittest.TestCase):
    def test_gomoku_lines_and_overline(self):
        for step, start in ((1, 105), (15, 7), (16, 0), (14, 14)):
            board = Gomoku.initial()
            for n in range(6):
                board[start + step * n] = 1
            self.assertTrue(Gomoku.won(board, start, 1))
        board = Gomoku.initial()
        for i in (13, 14, 15, 16, 17):
            board[i] = 1
        self.assertFalse(Gomoku.won(board, 15, 1))

    def test_coordinates(self):
        self.assertEqual(coord("I10", 9, 10), 89)
        for text in ("j1", "a11", "a0", "a01", "a1b2", "-1", "a1junk"):
            with self.assertRaises(ValueError):
                coord(text, 9, 10)

    def test_initial_xiangqi_perft(self):
        board = Xiangqi.initial()
        moves = Xiangqi.moves(board, 1)
        self.assertEqual(len(moves), 44)
        self.assertEqual(sum(len(Xiangqi.moves(Xiangqi.apply(board, m, 1), -1)) for m in moves), 1920)
        self.assertEqual(sum(p != 0 for p in board), 32)

    def test_horse_leg_and_elephant_eye(self):
        board = [0] * 90
        board[40] = 4
        self.assertIn(59, Xiangqi.pseudo(board, 40))
        board[49] = 7
        self.assertNotIn(59, Xiangqi.pseudo(board, 40))
        board = [0] * 90
        board[83] = 3
        self.assertIn(63, Xiangqi.pseudo(board, 83))
        board[73] = 7
        self.assertNotIn(63, Xiangqi.pseudo(board, 83))
        board = [0] * 90
        board[47] = 3
        self.assertTrue(all(i // 9 >= 5 for i in Xiangqi.pseudo(board, 47)))

    def test_cannon_screens(self):
        board = [0] * 90
        board[45], board[48] = 6, -5
        self.assertNotIn(48, Xiangqi.pseudo(board, 45))
        board[46] = 7
        self.assertIn(48, Xiangqi.pseudo(board, 45))
        self.assertNotIn(47, Xiangqi.pseudo(board, 45))
        board[47] = -7
        self.assertNotIn(48, Xiangqi.pseudo(board, 45))

    def test_palace_pawn_and_facing_kings(self):
        board = [0] * 90
        board[85], board[4], board[49] = 1, -1, 5
        self.assertNotIn((49, 48), Xiangqi.moves(board, 1))
        board[49] = 0
        self.assertTrue(Xiangqi.checked(board, 1))
        self.assertTrue(Xiangqi.checked(board, -1))
        board = Xiangqi.initial()
        self.assertEqual(Xiangqi.pseudo(board, 54), [45])
        board = [0] * 90
        board[36] = 7
        self.assertEqual(set(Xiangqi.pseudo(board, 36)), {27, 37})
        board[85] = 1
        self.assertTrue(all(3 <= i % 9 <= 5 and i // 9 >= 7 for i in Xiangqi.pseudo(board, 85)))

    def test_ai_tactical_win_and_block(self):
        for difficulty in (2, 3, 4):
            board = Gomoku.initial()
            board[105:109] = [-1] * 4
            self.assertEqual(ai_move("五子棋", board, -1, difficulty), 109)
            board[105:109] = [1] * 4
            self.assertEqual(ai_move("五子棋", board, -1, difficulty), 109)

    def test_checkmate_and_stalemate(self):
        board = [0] * 90
        board[4], board[85], board[13], board[21], board[23] = -1, 1, 5, 5, 5
        self.assertTrue(Xiangqi.checked(board, -1))
        self.assertEqual(Xiangqi.moves(board, -1), [])
        board = [0] * 90
        board[4], board[85], board[49], board[21], board[23], board[9] = -1, 1, 7, 5, 5, 5
        self.assertFalse(Xiangqi.checked(board, -1))
        self.assertEqual(Xiangqi.moves(board, -1), [])

    def test_all_ai_levels_legal_bounded_and_do_not_mutate(self):
        for kind, engine in (("五子棋", Gomoku), ("象棋", Xiangqi)):
            for difficulty in range(1, 5):
                board = engine.initial()
                if kind == "五子棋":
                    board[112] = 1
                before = board[:]
                started = time.perf_counter()
                move = ai_move(kind, board, -1, difficulty)
                self.assertIn(move, engine.moves(board, -1))
                self.assertEqual(board, before)
                self.assertLess(time.perf_counter() - started, 3)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.args = (root / "games.json", root / "images", lambda f: "https://test/" + f,
                     str, lambda g, u: ({"qq": {"alias": "b"}.get(u, u)}, None))
        self.games = BoardGames(*self.args)
        self.games.render = lambda r: "board"

    def command(self, user, text, group="g"):
        return self.games.handle(group, user, text.split())

    def start_pair(self, kind="五子棋"):
        self.command("a", kind + "双人 b")
        self.command("b", "接受棋局")
        return self.games.room_for("g", "a")

    def test_invite_authorization_and_cancel(self):
        self.command("a", "五子棋邀请 alias")
        self.assertIn("只有被邀请人", self.command("a", "接受棋局"))
        self.assertIn("没有", self.command("c", "接受棋局"))
        self.assertIn("已有", self.command("b", "象棋单人 1"))
        self.assertIn("被邀请人拒绝", self.command("b", "取消棋局邀请"))
        self.command("b", "拒绝棋局")
        self.assertIsNone(self.games.room_for("g", "a"))
        self.command("a", "五子棋邀请 b")
        self.command("a", "取消棋局邀请")
        self.assertIsNone(self.games.room_for("g", "b"))
        self.assertIn("不能邀请自己", self.command("b", "五子棋邀请 alias"))

    def test_turn_group_and_clock(self):
        room = self.start_pair()
        deadline = room["deadline"]
        self.assertIn("还没轮到", self.command("b", "五子棋落子 h8"))
        self.command("a", "五子棋落子 p1")
        self.command("a", "棋局")
        self.assertEqual(room["deadline"], deadline)
        self.command("a", "五子棋落子 h8", "other")
        self.assertEqual(room["board"][112], 1)
        self.assertEqual(room["turn"], -1)
        deadline = room["deadline"]
        self.command("b", "五子棋落子 h8")
        self.assertEqual(room["deadline"], deadline)

    def test_global_invite_stats_and_busy(self):
        self.command("a", "五子棋邀请 b", "g1")
        self.assertIn("已有", self.command("a", "象棋单人 1", "g2"))
        self.assertIn("对方已有", self.command("c", "象棋邀请 b", "g3"))
        self.command("b", "接受棋局", "g2")
        room = self.games.room_for("g3", "a")
        self.assertEqual(room["status"], "playing")
        self.command("a", "认输", "g3")
        self.assertEqual(self.command("b", "棋局统计", "g1"), self.command("b", "棋局统计", "g2"))
        self.assertIn("1胜", self.command("b", "棋局统计", "g4"))

    def test_legacy_stats_migration_is_idempotent_and_conflicts_cancel(self):
        room = self.start_pair()
        import copy
        older = copy.deepcopy(room)
        older.update(id="older", group="elsewhere", created=room["created"] - 1)
        self.games.state["rooms"]["older"] = older
        self.games.state.pop("version")
        self.games.state["stats"] = {json.dumps([g, "a", "五子棋", "AI", 2]): {"胜": 2, "负": 1, "和": 0} for g in ("g1", "g2")}
        self.games.save()
        restored = BoardGames(*self.args)
        self.assertEqual(restored.state["rooms"]["older"]["status"], "cancelled")
        self.assertEqual(restored.room_for("any", "a")["id"], room["id"])
        self.assertEqual(list(restored.state["stats"].values()), [{"胜": 4, "负": 2, "和": 0}])
        self.assertEqual(BoardGames(*self.args).state["stats"], restored.state["stats"])

    def test_separate_complete_help(self):
        for kind in ("五子棋", "中国象棋", "象棋"):
            for suffix in ("", "帮助", "介绍", "指令"):
                text = self.command("a", kind + suffix)
                self.assertIn("完整玩法指南", text)
                for command in ("接受棋局", "拒绝棋局", "取消棋局邀请", "同意和棋", "拒绝和棋", "认输", "棋局统计"):
                    self.assertIn(command, text)
                self.assertIn("全群共享", text)
                self.assertIn("10 分钟", text)

    def test_timeout_restart_and_exact_once(self):
        room = self.start_pair()
        self.command("a", "五子棋落子 h8")
        room["deadline"] = 1000
        self.games.save()
        with patch("petpark.boardgames.time.time", return_value=1000):
            recovered = BoardGames(*self.args)
            recovered.expire()
        old = recovered.room_for("g", "a", active=False)
        self.assertEqual(old["winner"], "a")
        self.assertEqual(old["status"], "finished")
        self.assertEqual(sum(s["胜"] for s in recovered.state["stats"].values()), 1)
        self.assertEqual(sum(s["负"] for s in recovered.state["stats"].values()), 1)

    def test_invite_timeout_does_not_count_loss(self):
        self.command("a", "象棋双人 b")
        room = self.games.room_for("g", "a")
        room["deadline"] = 0
        self.games.expire()
        self.assertEqual(room["status"], "cancelled")
        self.assertFalse(self.games.state["stats"])

    def test_draw_and_resign(self):
        room = self.start_pair()
        deadline = room["deadline"]
        self.command("a", "求和")
        self.assertEqual(room["deadline"], deadline)
        self.assertIn("没有来自对方", self.command("a", "同意和棋"))
        self.command("b", "拒绝和棋")
        self.assertIsNone(room["offer"])
        self.command("b", "求和")
        self.command("a", "同意和棋")
        self.assertIsNone(room["winner"])
        room = self.start_pair("象棋")
        self.command("a", "认输")
        self.assertEqual(room["winner"], "b")

    def test_single_and_restore(self):
        self.command("a", "象棋单人 2")
        self.command("a", "象棋落子 a7 a6")
        room = self.games.room_for("g", "a")
        self.assertEqual(room["moves"], 2)
        self.assertEqual(room["turn"], 1)
        recovered = BoardGames(*self.args)
        self.assertEqual(recovered.room_for("g", "a")["board"], room["board"])

    def test_render_failure_keeps_move_on_disk(self):
        self.start_pair()
        self.games.render = lambda r: (_ for _ in ()).throw(OSError("image unavailable"))
        with self.assertRaises(OSError):
            self.command("a", "五子棋落子 h8")
        recovered = BoardGames(*self.args)
        self.assertEqual(recovered.room_for("g", "a")["board"][112], 1)

    def test_xiangqi_stalemate_settles_as_loss(self):
        room = self.start_pair("象棋")
        board = [0] * 90
        board[4], board[85], board[49], board[21], board[23], board[18] = -1, 1, 7, 5, 5, 5
        room["board"] = board
        self.command("a", "象棋落子 a3 a2")
        self.assertEqual(room["status"], "finished")
        self.assertEqual(room["winner"], "a")

    def test_parallel_accept_is_single_game(self):
        from concurrent.futures import ThreadPoolExecutor
        self.command("a", "象棋邀请 b")
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(lambda _: self.command("b", "接受棋局"), range(2)))
        self.assertEqual(len(self.games.state["rooms"]), 1)
        self.assertEqual(self.games.room_for("g", "a")["moves"], 0)

    def test_full_gomoku_win(self):
        room = self.start_pair()
        for x in "abcd":
            self.command("a", f"五子棋落子 {x}1")
            self.command("b", f"五子棋落子 {x}3")
        self.command("a", "五子棋落子 e1")
        self.assertEqual(room["winner"], "a")
        self.assertIsNone(self.games.room_for("g", "a"))

    def test_xiangqi_repetition_and_quiet_draw(self):
        room = self.start_pair("象棋")
        for _ in range(2):
            for user, move in (("a", "b10 c8"), ("b", "b1 c3"), ("a", "c8 b10"), ("b", "c3 b1")):
                self.command(user, "象棋落子 " + move)
        self.assertEqual(room["status"], "finished")
        self.assertIsNone(room["winner"])
        room = self.start_pair("象棋")
        room["quiet"] = 119
        self.command("a", "象棋落子 a7 a6")
        self.assertEqual(room["status"], "finished")


if __name__ == "__main__":
    unittest.main()
