"""Generate actual production-renderer previews: python tools/preview_boards.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from petpark.boardgames import BoardGames

root = Path(__file__).resolve().parents[1] / "board-previews"
games = BoardGames(root / "preview-state.json", root, lambda f: f, str, lambda g, u: ({"qq": u}, None))
for kind in ("五子棋", "象棋", "军棋", "围棋", "斗兽棋"):
    room = {"id": {"五子棋": "gomoku", "象棋": "xiangqi", "军棋": "junqi", "围棋": "go", "斗兽棋": "doushouqi"}[kind], "kind": kind,
            "group": "preview", "players": ["玩家", "好友"], "difficulty": 1, "created": 0}
    games.start(room)
    if kind == "五子棋":
        for move in (112, 113, 127, 96, 142, 128, 157):
            games.play(room, move)
    elif kind == "象棋":
        games.play(room, (54, 45))
    elif kind == "军棋":
        games.play(room, (30, 25))
    elif kind == "围棋":
        for move in (60, 300, 72, 288, 79, 80, 98, 99, 117, 118, 137, 136,
                     155, 156, 269, 270, 250, 251, 231, 232, 212):
            games.play(room, move)
    else:
        games.play(room, (42, 35))
    print(games.render(room))
