"""Exercise the actual plugin dispatcher with the repository's AstrBot compatibility API."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

workspace = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(workspace))
sys.path.insert(0, str(workspace / "petbot_framework" / "compat"))
from qqbot_pet.main import PetParkPlugin, KNOWN_COMMANDS
from qqbot_pet.petpark.boardgames import BoardGames, COMMANDS


class DispatchTests(unittest.TestCase):
    def test_actual_dispatch_commands_aliases_and_mentions(self):
        with tempfile.TemporaryDirectory() as folder:
            plugin = PetParkPlugin.__new__(PetParkPlugin)
            players = {"a": {"qq": "a"}, "b": {"qq": "b"}}
            plugin.store = SimpleNamespace(lottery=lambda: None, get_group=lambda g: {"enabled": True},
                                           get_player=lambda q, g: players[q], active_events=lambda: {})
            plugin.zhongyuan = None
            plugin._active_event_commands = lambda: set()
            plugin._is_group_authorized = lambda g: True
            plugin._is_admin = lambda e: True
            plugin._handle_info = lambda c, t: None
            plugin._bank_block_check = lambda p: None
            plugin.store.all_players = lambda: {"g1:a": players["a"], "g2:b": players["b"]}
            plugin._resolve_user_token = lambda token: "b" if token == "bound_qq" else token
            plugin._board_games = BoardGames(Path(folder) / "games.json", Path(folder), str, str,
                                             plugin._find_board_target)
            plugin._board_games.render = lambda r: "board"
            self.assertTrue(COMMANDS <= KNOWN_COMMANDS)
            self.assertIn("_board_clock_task_ref", plugin._BG_TASK_REFS)
            self.assertIn("四档 AI", plugin.dispatch(None, "a", "g", "棋类帮助"))
            response = plugin.dispatch(None, "a", "g", "/开始中国象棋2")
            self.assertIn("单人 普通", response[0])
            plugin.dispatch(None, "a", "g", "中国象棋落子 a7 a6")
            self.assertEqual(plugin._board_games.room_for("g", "a")["moves"], 2)
            plugin.dispatch(None, "a", "g", "认输")
            plugin.dispatch(None, "a", "g", "五子棋双人<@!b>")
            plugin.dispatch(None, "b", "other_group", "接受棋局")
            response = plugin.dispatch(None, "a", "g", "五子棋落子 H8")
            self.assertIn("轮到 b", response[0])
            self.assertEqual(plugin._board_games.room_for("g", "a")["board"][112], 1)
            self.assertEqual(plugin._find_board_target("unrelated", "bound_qq")[0], players["b"])
            plugin.dispatch(None, "a", "g", "认输")
            response = plugin.dispatch(None, "a", "g", "/开始军棋2")
            self.assertIn("军棋 · 单人 普通", response[0])
            plugin.dispatch(None, "a", "g", "军棋落子 a7 a6")
            self.assertEqual(plugin._board_games.room_for("g", "a")["moves"], 2)
            plugin.dispatch(None, "a", "g", "认输")
            plugin.dispatch(None, "a", "g", "军棋双人<@!b>")
            plugin.dispatch(None, "b", "other_group", "接受棋局")
            response = plugin.dispatch(None, "a", "g", "军棋落子 A7 A6")
            self.assertIn("轮到 b", response[0])
            plugin.dispatch(None, "a", "g", "认输")
            response = plugin.dispatch(None, "a", "g", "/开始围棋2")
            self.assertIn("围棋 · 单人 普通", response[0])
            plugin.dispatch(None, "a", "g", "围棋落子 S19")
            self.assertEqual(plugin._board_games.room_for("g", "a")["moves"], 2)
            plugin.dispatch(None, "a", "g", "认输")
            plugin.dispatch(None, "a", "g", "围棋双人<@!b>")
            plugin.dispatch(None, "b", "other_group", "接受棋局")
            plugin.dispatch(None, "a", "g", "围棋停一手")
            response = plugin.dispatch(None, "b", "g", "围棋过")
            self.assertIn("双方连续停一手", response[0])


if __name__ == "__main__":
    unittest.main()
