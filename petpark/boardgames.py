"""Chat board games: deterministic rules, bounded local AI, durable invitations and clocks."""
from __future__ import annotations

import json
import random
import re
import threading
import time
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

NAMES = {1: "简单", 2: "普通", 3: "困难", 4: "地狱"}
KINDS = ("五子棋", "象棋")
COMMANDS = {"棋类帮助", "棋局", "棋局统计", "接受棋局", "拒绝棋局", "取消棋局邀请", "认输", "求和", "同意和棋", "拒绝和棋"}
for _kind in (*KINDS, "中国象棋"):
    COMMANDS.update({_kind, f"{_kind}帮助", f"{_kind}介绍", f"{_kind}指令", f"开始{_kind}", f"{_kind}单人", f"{_kind}双人", f"{_kind}邀请", f"{_kind}落子", f"{_kind}棋盘", f"放弃{_kind}"})


def coord(text, width, height):
    if not re.fullmatch(r"[a-zA-Z](?:[1-9]|1[0-5])", text):
        raise ValueError("坐标格式错误，请使用 a1 这样的坐标。")
    x, y = ord(text[0].lower()) - 97, int(text[1:]) - 1
    if x >= width or y >= height:
        raise ValueError("坐标超出棋盘范围。")
    return y * width + x


def label(index, width):
    return f"{chr(97 + index % width)}{index // width + 1}"


class Gomoku:
    width, height = 15, 15

    @staticmethod
    def initial():
        return [0] * 225

    @staticmethod
    def won(board, index, side):
        x, y = index % 15, index // 15
        for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
            count = 1
            for sign in (-1, 1):
                nx, ny = x + sign * dx, y + sign * dy
                while 0 <= nx < 15 and 0 <= ny < 15 and board[ny * 15 + nx] == side:
                    count += 1
                    nx, ny = nx + sign * dx, ny + sign * dy
            if count >= 5:
                return True
        return False

    @staticmethod
    def moves(board, side):
        return [i for i, p in enumerate(board) if p == 0]

    @staticmethod
    def apply(board, move, side):
        result = board[:]
        result[move] = side
        return result

    @staticmethod
    def candidates(board):
        occupied = [i for i, p in enumerate(board) if p]
        if not occupied:
            return [112]
        return sorted({y * 15 + x for i in occupied
                       for y in range(max(0, i // 15 - 2), min(15, i // 15 + 3))
                       for x in range(max(0, i % 15 - 2), min(15, i % 15 + 3))
                       if not board[y * 15 + x]})

    @staticmethod
    def threat(board, index, side):
        x, y = index % 15, index // 15
        score = 0
        for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
            count, ends = 1, 0
            for sign in (-1, 1):
                nx, ny = x + sign * dx, y + sign * dy
                while 0 <= nx < 15 and 0 <= ny < 15 and board[ny * 15 + nx] == side:
                    count += 1
                    nx, ny = nx + sign * dx, ny + sign * dy
                ends += int(0 <= nx < 15 and 0 <= ny < 15 and board[ny * 15 + nx] == 0)
            if count >= 5:
                score += 1000000
            elif ends:
                score += (0, 2, 20, 250, 10000)[count] * (8 if ends == 2 else 1)
        return score

    @classmethod
    def ranked(cls, board, side):
        return sorted(cls.candidates(board), key=lambda i: (
            cls.threat(board, i, side) * 1.1 + cls.threat(board, i, -side),
            -abs(i % 15 - 7) - abs(i // 15 - 7)), reverse=True)

    @classmethod
    def evaluate(cls, board, side):
        candidates = cls.candidates(board)
        own = sorted((cls.threat(board, i, side) for i in candidates), reverse=True)[:2]
        other = sorted((cls.threat(board, i, -side) for i in candidates), reverse=True)[:2]
        return sum(own) - sum(other) * 1.1


class Xiangqi:
    """Positive red, negative black. K A E H R C P => 1..7."""
    width, height = 9, 10
    values = {1: 100000, 2: 120, 3: 120, 4: 300, 5: 650, 6: 350, 7: 70}

    @staticmethod
    def initial():
        board = [0] * 90
        back = [5, 4, 3, 2, 1, 2, 3, 4, 5]
        board[:9] = [-p for p in back]
        board[81:] = back
        for x in (1, 7):
            board[18 + x], board[63 + x] = -6, 6
        for x in (0, 2, 4, 6, 8):
            board[27 + x], board[54 + x] = -7, 7
        return board

    @staticmethod
    def pseudo(board, source):
        p = board[source]
        if not p:
            return []
        side, kind = (1 if p > 0 else -1), abs(p)
        x, y = source % 9, source // 9
        result = []

        def add(nx, ny):
            if 0 <= nx < 9 and 0 <= ny < 10 and board[ny * 9 + nx] * side <= 0:
                result.append(ny * 9 + nx)

        if kind in (5, 6):
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny, screen = x + dx, y + dy, False
                while 0 <= nx < 9 and 0 <= ny < 10:
                    target = board[ny * 9 + nx]
                    if not screen:
                        if not target:
                            add(nx, ny)
                        else:
                            if kind == 5:
                                add(nx, ny)
                                break
                            screen = True
                    elif target:
                        add(nx, ny)
                        break
                    nx, ny = nx + dx, ny + dy
        elif kind == 4:
            for dx, dy in ((1, 2), (-1, 2), (1, -2), (-1, -2), (2, 1), (2, -1), (-2, 1), (-2, -1)):
                lx, ly = x + (dx // 2 if abs(dx) == 2 else 0), y + (dy // 2 if abs(dy) == 2 else 0)
                if 0 <= lx < 9 and 0 <= ly < 10 and not board[ly * 9 + lx]:
                    add(x + dx, y + dy)
        elif kind == 3:
            for dx, dy in ((2, 2), (2, -2), (-2, 2), (-2, -2)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < 9 and 0 <= ny < 10 and (ny >= 5 if side == 1 else ny <= 4):
                    if not board[(y + dy // 2) * 9 + x + dx // 2]:
                        add(nx, ny)
        elif kind in (1, 2):
            directions = ((1, 0), (-1, 0), (0, 1), (0, -1)) if kind == 1 else ((1, 1), (1, -1), (-1, 1), (-1, -1))
            for dx, dy in directions:
                nx, ny = x + dx, y + dy
                if 3 <= nx <= 5 and (7 <= ny <= 9 if side == 1 else 0 <= ny <= 2):
                    add(nx, ny)
            if kind == 1:
                ny = y - side
                while 0 <= ny < 10:
                    target = board[ny * 9 + x]
                    if target:
                        if target == -side:
                            add(x, ny)
                        break
                    ny -= side
        elif kind == 7:
            add(x, y - side)
            if y <= 4 if side == 1 else y >= 5:
                add(x - 1, y)
                add(x + 1, y)
        return result

    @classmethod
    def checked(cls, board, side):
        if side not in board:
            return True
        king = board.index(side)
        return any(king in cls.pseudo(board, i) for i, p in enumerate(board) if p * side < 0)

    @staticmethod
    def apply(board, move, side):
        result = board[:]
        source, target = move
        result[target], result[source] = result[source], 0
        return result

    @classmethod
    def moves(cls, board, side):
        return [(i, target) for i, p in enumerate(board) if p * side > 0
                for target in cls.pseudo(board, i)
                if not cls.checked(cls.apply(board, (i, target), side), side)]

    @classmethod
    def ranked(cls, board, side):
        return sorted(cls.moves(board, side), key=lambda m: cls.values.get(abs(board[m[1]]), 0) * 10 - cls.values[abs(board[m[0]])], reverse=True)

    @classmethod
    def evaluate(cls, board, side):
        value = 0
        for i, p in enumerate(board):
            if p:
                sign = 1 if p > 0 else -1
                advance = (9 - i // 9) if p > 0 else i // 9
                bonus = (advance * 8 + (40 if advance >= 5 else 0)) if abs(p) == 7 else 0
                value += sign * (cls.values[abs(p)] + bonus)
        return value * side


ENGINES = {"五子棋": Gomoku, "象棋": Xiangqi}


def ai_move(kind, board, side, difficulty):
    """Iterative negamax; always return a legal fallback if the search budget expires."""
    engine = ENGINES[kind]
    moves = engine.ranked(board, side)
    if not moves:
        return None
    if difficulty == 1:
        return random.choice(moves)
    if kind == "五子棋":
        for owner in (side, -side):
            wins = [m for m in moves if engine.won(engine.apply(board, m, owner), m, owner)]
            if wins:
                return wins[0]
    budget = {2: 0.15, 3: 0.5, 4: 1.2}[difficulty]
    deadline = time.perf_counter() + budget
    max_depth = {2: 1, 3: 2, 4: 4}[difficulty]
    width = {2: 8, 3: 10, 4: 14}[difficulty]

    def search(position, turn, depth, alpha, beta, ply):
        if time.perf_counter() >= deadline:
            raise TimeoutError
        if kind == "象棋" and turn not in position:
            return -10000000 + ply
        if depth == 0:
            return engine.evaluate(position, turn)
        choices = engine.ranked(position, turn)
        if not choices:
            return (-10000000 + ply) if kind == "象棋" else 0
        if kind == "五子棋":
            choices = choices[:width]
        best = -float("inf")
        for move in choices:
            nxt = engine.apply(position, move, turn)
            if kind == "五子棋" and engine.won(nxt, move, turn):
                value = 10000000 - ply
            else:
                value = -search(nxt, -turn, depth - 1, -beta, -alpha, ply + 1)
            best, alpha = max(best, value), max(alpha, value)
            if alpha >= beta:
                break
        return best

    best_move = moves[0]
    for depth in range(1, max_depth + 1):
        current, score = best_move, -float("inf")
        try:
            choices = moves[:width] if kind == "五子棋" else moves
            for move in choices:
                value = -search(engine.apply(board, move, side), -side, depth - 1, -float("inf"), -score, 1)
                if value > score:
                    current, score = move, value
            best_move = current
            moves.remove(best_move)
            moves.insert(0, best_move)
        except TimeoutError:
            break
    return best_move


SHARED_HELP = """### 🤝 邀请与结束
- `接受棋局`：仅被邀请人可接受，接受后正式开局。
- `拒绝棋局`：被邀请人拒绝；`取消棋局邀请`：发起人撤销。
- `棋局`：查看当前棋盘、轮次、最近一步和剩余时间。
- `认输`：立即判自己负、对手胜。
- `求和`：双人局向对方申请和棋。
- `同意和棋` / `拒绝和棋`：回应对方请求，不能同意自己的请求；落子后请求失效。
- `棋局统计`：全群累计胜/负/和，按棋种、单/双人及 AI 难度分别统计。

### ⏳ 10 分钟规则
邀请发出后 10 分钟未接受自动失效，不计败局。开局及每次合法落子后，轮到的一方有 10 分钟；超时视为放弃，对手获胜。
查看棋盘、普通聊天、无效落子和求和都不会延长时间。AI 对局只能按规则自动和棋或主动认输。

### 🌐 全群共享
棋局、邀请、计时和战绩全群共享，换群仍可查看、接受邀请和继续落子；全群范围每人同时只能有一个棋局或待回应邀请。
可 @本群成员，也可填写已有宠物乐园档案的用户ID/已绑定QQ跨群邀请。对方须自行发送指令回应，系统不会代发私信。
仍需满足所在群的宠物乐园使用条件。重启后恢复原对局和截止时间。双方看相同坐标方向，只有轮到的本人可以落子。
"""


def game_help(kind):
    name = "中国象棋" if kind == "象棋" else "五子棋"
    example = "h8" if kind == "五子棋" else "a7 a6"
    rules = ("""### ⚫ 棋盘与胜负
15×15 棋盘，列 a–o 从左到右、行 1–15 从上到下。`h8` 是正中央交点，大小写均可。
黑先白后：单人玩家执黑，双人邀请人执黑；每步在空交点放一枚棋子，不能移动或覆盖已有棋子。
横、竖、斜连续至少五子即胜，长连也算胜；采用无禁手规则。满盘未分胜负则和棋。
青色边框标记上一手落点。
""" if kind == "五子棋" else """### 🔴 棋盘与胜负
9 列×10 行，列 a–i 从左到右、行 1–10 从上到下；黑方在上、红方在下，双方坐标不翻转。
红先黑后：单人玩家执红，双人邀请人执红。例：`中国象棋落子 a7 a6`，把 a7 的红兵向前走一步。
车沿直线走，不能越子；马走日字，注意蹩马腿；相/象走田字，不能过河且不能塞象眼。
仕/士在九宫内斜走一格；帅/将在九宫内直走一格，将帅不能直接照面。
炮不吃子时沿直线走，吃子时必须恰好隔一个炮架；兵/卒每次向前一格，过河后可横走，不能后退。
不能送将；被将军时必须先解将。将死或困毙（无合法着法）均判负。
休闲和棋约定：同一局面及行棋方三次重复，或连续 120 步未吃子，自动和棋；不采用专业赛事长将长捉责任裁定。
青色大框标记上一步终点，小框标记起点。
""")
    return f"""## {'⚫' if kind == '五子棋' else '🎴'} {name} · 完整玩法指南
### 🚀 快速开始
单人：`{name}单人 2` → 等待棋盘 → `{name}落子 {example}`。
双人：`{name}双人 @对方` → 对方发送 `接受棋局` → 邀请人先走。

### 🤖 单人四档 AI
- `{name}单人 1`：简单，随机合法候选着法，适合入门。
- `{name}单人 2`：普通，局面评分与基础攻防。
- `{name}单人 3`：困难，最多两层前瞻搜索。
- `{name}单人 4`：地狱，最多四层迭代搜索。
默认难度 1，也支持中文难度名；`开始{name} 2`、`开始{name}2` 同样可用。AI 棋力受搜索时间预算限制。

### 📋 本游戏全部指令
- `{name}` / `{name}帮助` / `{name}介绍` / `{name}指令`：本页说明。
- `{name}单人 [1–4]` / `开始{name} [1–4]`：单人开局。
- `{name}双人 @对方` / `{name}邀请 用户ID`：定向邀请，双人不选 AI 难度。
- `{name}落子 {example}`：{'落下一枚棋子' if kind == '五子棋' else '起点与终点之间用空格分开'}。
- `{name}棋盘`：查看棋盘；`放弃{name}`：立即认输。
{'所有“中国象棋”指令均可简写为“象棋”，例如 `象棋落子 a7 a6`。' if kind == '象棋' else ''}

{rules}
{SHARED_HELP}"""


HELP = """## 🎴 棋类大厅 · 全群共享
### 选择玩法
- `五子棋` / `五子棋帮助`：五子棋全部指令、坐标及胜负规则。
- `中国象棋` / `中国象棋帮助`：中国象棋全部指令、棋子走法及胜负规则。
### 立即开局
- `五子棋单人 2` / `中国象棋单人 2`：挑战 AI。
- `五子棋双人 @对方` / `中国象棋双人 @对方`：邀请玩家。
- 四档 AI：1 简单 / 2 普通 / 3 困难 / 4 地狱，与扫雷难度名称一致。
- 五子棋落子例：`五子棋落子 h8`；象棋落子例：`中国象棋落子 a7 a6`。

""" + SHARED_HELP


class BoardGames:
    def __init__(self, path, image_dir, image_url, display_user, find_target):
        self.path, self.image_dir = Path(path), Path(image_dir)
        self.image_url, self.display_user, self.find_target = image_url, display_user, find_target
        self.lock = threading.RLock()
        self.state = {"rooms": {}, "stats": {}}
        if self.path.exists():
            # Refuse to silently overwrite an unreadable saved game file.
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
        self.migrate_global()
        self.expire()

    def migrate_global(self):
        """Merge legacy per-group records once, preserving completed results."""
        if self.state.get("version", 1) >= 2:
            return
        merged = {}
        for key, stats in self.state["stats"].items():
            parts = json.loads(key)
            global_key = json.dumps(parts[1:] if len(parts) == 5 else parts)
            total = merged.setdefault(global_key, {"胜": 0, "负": 0, "和": 0})
            for field in total:
                total[field] += stats.get(field, 0)
        self.state["stats"] = merged
        occupied = set()
        # Preserve the newest active room if legacy groups contain overlapping players.
        for room in sorted(self.state["rooms"].values(), key=lambda r: r["created"], reverse=True):
            if room["status"] not in ("pending", "playing"):
                continue
            users = set(room["players"]) - {"@AI"}
            if occupied & users:
                room.update(status="cancelled", result="全群共享升级：保留了你或对手较新的棋局，本局取消，不计胜负。", ended=time.time())
            else:
                occupied.update(users)
        self.state["version"] = 2
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.path)

    def room_for(self, group, user, active=True):
        matches = [r for r in self.state["rooms"].values() if user in r["players"]
                   and (not active or r["status"] in ("pending", "playing"))]
        return max(matches, key=lambda r: r["created"], default=None)

    def finish(self, room, winner, reason):
        if room["status"] != "playing":
            return
        room.update(status="finished", winner=winner, result=reason, ended=time.time())
        for user in room["players"]:
            if user == "@AI":
                continue
            key = json.dumps([user, room["kind"], "AI" if "@AI" in room["players"] else "双人", room["difficulty"]])
            stats = self.state["stats"].setdefault(key, {"胜": 0, "负": 0, "和": 0})
            stats["和" if winner is None else "胜" if winner == user else "负"] += 1

    def expire(self):
        with self.lock:
            changed = False
            now = time.time()
            for room in self.state["rooms"].values():
                if room["status"] in ("pending", "playing") and now >= room["deadline"]:
                    if room["status"] == "pending":
                        room.update(status="cancelled", result="邀请已超过 10 分钟，自动取消。", ended=now)
                    else:
                        loser = room["players"][0 if room["turn"] == 1 else 1]
                        winner = room["players"][1 if room["turn"] == 1 else 0]
                        self.finish(room, winner, f"{self.name(loser)} 10 分钟未落子，视为放弃。")
                    changed = True
            completed = sorted((r for r in self.state["rooms"].values() if r["status"] not in ("pending", "playing")), key=lambda r: r.get("ended", r["created"]), reverse=True)
            for room in completed[200:]:
                del self.state["rooms"][room["id"]]
                changed = True
            if changed:
                self.save()

    def name(self, user):
        return "AI" if user == "@AI" else str(self.display_user(user))

    @staticmethod
    def position_key(room):
        return str(room["turn"]) + ":" + ",".join(map(str, room["board"]))

    def start(self, room):
        room.update(status="playing", board=ENGINES[room["kind"]].initial(), turn=1,
                    deadline=time.time() + 600, moves=0, quiet=0, last=None, offer=None, positions={})
        room["positions"][self.position_key(room)] = 1

    def play(self, room, move):
        engine, side = ENGINES[room["kind"]], room["turn"]
        capture = room["kind"] == "象棋" and room["board"][move[1]] != 0
        room["board"] = engine.apply(room["board"], move, side)
        room["last"], room["moves"], room["offer"] = move, room["moves"] + 1, None
        room["quiet"] = 0 if capture else room["quiet"] + 1
        winner = room["players"][0 if side == 1 else 1]
        if room["kind"] == "五子棋":
            if engine.won(room["board"], move, side):
                self.finish(room, winner, "五子连珠！")
            elif all(room["board"]):
                self.finish(room, None, "棋盘已满，和棋。")
        elif not engine.moves(room["board"], -side):
            self.finish(room, winner, "对方被将死或困毙。")
        room["turn"] = -side
        if room["status"] == "playing" and room["kind"] == "象棋":
            key = self.position_key(room)
            room["positions"][key] = room["positions"].get(key, 0) + 1
            if room["positions"][key] >= 3 or room["quiet"] >= 120:
                self.finish(room, None, "三次重复局面或连续 120 步未吃子，和棋。")
        room["deadline"] = time.time() + 600

    def handle(self, group, user, tokens):
        with self.lock:
            self.expire()
            try:
                return self._handle(str(group), str(user), tokens)
            finally:
                # Image delivery failures must not undo a legally completed move.
                self.save()

    def _handle(self, group, user, tokens):
        cmd = tokens[0].replace("中国象棋", "象棋")
        args = tokens[1:]
        kind = "五子棋" if "五子棋" in cmd else "象棋" if "象棋" in cmd else None
        room = self.room_for(group, user)
        if cmd in (*KINDS, "棋类帮助") or cmd.endswith(("帮助", "介绍", "指令")):
            return game_help(kind) if kind else HELP
        if cmd == "棋局统计":
            lines = ["## 棋局统计 · 全群共享"]
            for key, stats in self.state["stats"].items():
                u, k, mode, diff = json.loads(key)
                if u == user:
                    lines.append(f"{k} · {mode} {NAMES[diff] if mode == 'AI' else ''}：{stats['胜']}胜 / {stats['负']}负 / {stats['和']}和")
            return "\n".join(lines) if len(lines) > 1 else "你还没有已结算的棋局。战绩全群共享。"
        if kind and (cmd.startswith("开始") or cmd.endswith(("单人", "双人", "邀请"))):
            if room:
                return "你已有棋局或待回应邀请。发送「棋局」查看，或先认输/取消邀请。"
            invite = cmd.endswith(("双人", "邀请")) or (args and args[0] == "双人")
            if args and args[0] in ("单人", "双人"):
                args = args[1:]
            if invite:
                if len(args) != 1:
                    return f"用法：{kind}双人 @对方（或对方用户ID）。"
                target, error = self.find_target(group, args[0])
                if error or not target:
                    return error or "找不到邀请对象。"
                opponent = str(target["qq"])
                if opponent == user:
                    return "不能邀请自己下棋。"
                if self.room_for(group, opponent):
                    return "对方已有棋局或待回应邀请。"
                difficulty = 1
            else:
                if len(args) > 1 or (args and args[0] not in ("1", "2", "3", "4", *NAMES.values())):
                    return "难度只有 1简单 / 2普通 / 3困难 / 4地狱。例：五子棋单人 3"
                value = args[0] if args else "1"
                difficulty = int(value) if value.isdigit() else next(d for d, n in NAMES.items() if n == value)
                opponent = "@AI"
            now = time.time()
            room = {"id": uuid.uuid4().hex, "kind": kind, "group": group, "players": [user, opponent],
                    "difficulty": difficulty, "status": "pending", "created": now, "deadline": now + 600}
            self.state["rooms"][room["id"]] = room
            if not invite:
                self.start(room)
            return self.view(room)
        if not room:
            recent = self.room_for(group, user, active=False)
            if recent and cmd in ("棋局", "五子棋棋盘", "象棋棋盘", "认输", "五子棋落子", "象棋落子", "放弃五子棋", "放弃象棋", "接受棋局"):
                return self.view(recent)
            return "你没有进行中的棋局或邀请。发送「棋类帮助」查看玩法。"
        if kind and kind != room["kind"]:
            return f"你当前参与的是{room['kind']}，请使用对应指令。"
        if cmd == "棋局" or cmd.endswith("棋盘"):
            return self.view(room)
        if room["status"] == "pending":
            if cmd == "接受棋局":
                if user != room["players"][1]:
                    return "只有被邀请人可以接受棋局。"
                self.start(room)
            elif cmd in ("拒绝棋局", "取消棋局邀请"):
                required = room["players"][1 if cmd == "拒绝棋局" else 0]
                if user != required:
                    return "请由被邀请人拒绝，或邀请人取消邀请。"
                room.update(status="cancelled", result="邀请已拒绝或取消。", ended=time.time())
            else:
                return "邀请尚未接受。对方发送「接受棋局」后才能落子。"
            return self.view(room)
        if cmd == "认输" or cmd.startswith("放弃"):
            self.finish(room, next(p for p in room["players"] if p != user), f"{self.name(user)} 主动认输。")
            return self.view(room)
        if cmd in ("求和", "同意和棋", "拒绝和棋"):
            if "@AI" in room["players"]:
                return "AI 对局通过棋盘规则自动判和；也可以主动认输。"
            if cmd == "求和":
                if room["offer"]:
                    return "已有求和请求，请对方回复。"
                room["offer"] = user
                return "已申请和棋，对方可发送「同意和棋」或「拒绝和棋」。计时继续，落子后请求失效。"
            if not room["offer"] or room["offer"] == user:
                return "没有来自对方的求和请求。"
            if cmd == "同意和棋":
                self.finish(room, None, "双方同意和棋。")
                return self.view(room)
            room["offer"] = None
            return "已拒绝和棋，请继续落子。"
        if cmd.endswith("落子"):
            if room["players"][0 if room["turn"] == 1 else 1] != user:
                return "还没轮到你落子，请等待对方。"
            engine = ENGINES[room["kind"]]
            try:
                if len(args) != (1 if kind == "五子棋" else 2):
                    raise ValueError("用法：五子棋落子 h8；象棋落子 a7 a6。")
                coords = [coord(a, engine.width, engine.height) for a in args]
                move = coords[0] if kind == "五子棋" else tuple(coords)
                if move not in engine.moves(room["board"], room["turn"]):
                    raise ValueError("落子不合法：位置被占用、棋子走法错误，或此步会使己方被将军。")
            except ValueError as exc:
                return str(exc)
            self.play(room, move)
            if room["status"] == "playing" and "@AI" in room["players"]:
                reply = ai_move(room["kind"], room["board"], room["turn"], room["difficulty"])
                if reply is None:
                    self.finish(room, user if kind == "象棋" else None, "对方已无合法着法。")
                else:
                    self.play(room, reply)
            return self.view(room)
        return "该操作不适用于当前棋局，发送「棋类帮助」查看指令。"

    def view(self, room):
        first, second = map(self.name, room["players"])
        title = f"## {room['kind']} · {'单人 ' + NAMES[room['difficulty']] if '@AI' in room['players'] else '双人对弈'}"
        if room["status"] == "pending":
            return f"{title}\n{first} 邀请 {second} 对弈。\n请 {second} 发送「接受棋局」或「拒绝棋局」。10 分钟内有效。"
        if room["status"] == "cancelled":
            return f"{title}\n{room['result']}"
        roles = "黑 / 白" if room["kind"] == "五子棋" else "红 / 黑"
        text = f"{title}\n{roles}：{first} / {second} · 已走 {room['moves']} 步\n"
        if room["status"] == "finished":
            text += room["result"] + (f"\n获胜：{self.name(room['winner'])}" if room["winner"] else "\n结果：和棋")
        else:
            who = room["players"][0 if room["turn"] == 1 else 1]
            left = max(0, int(room["deadline"] - time.time()))
            text += f"轮到 {self.name(who)} · 剩余 {left // 60}:{left % 60:02d}，超时视为放弃。"
            if room["kind"] == "象棋" and Xiangqi.checked(room["board"], room["turn"]):
                text += "\n⚠️ 将军！请先解除将军。"
            text += "\n" + ("五子棋落子 h8" if room["kind"] == "五子棋" else "象棋落子 a7 a6")
        if room["last"] is not None:
            last = room["last"]
            text += "\n上一手：" + (label(last, 15) if room["kind"] == "五子棋" else f"{label(last[0], 9)} → {label(last[1], 9)}")
        return text, self.render(room)

    def render(self, room):
        engine = ENGINES[room["kind"]]
        width, height = engine.width, engine.height
        cell = 44 if width == 15 else 66
        ox, oy = (100, 110) if width == 15 else (110, 120)
        size = (ox * 2 + cell * (width - 1), oy * 2 + cell * (height - 1))
        asset = Path(__file__).parent / "assets" / "boards" / "wood-base.png"
        with Image.open(asset) as base:
            canvas = base.convert("RGB").resize(size, Image.Resampling.LANCZOS)
        draw = ImageDraw.Draw(canvas)
        font_paths = [Path(__file__).parent / "assets" / "boards" / "BoardGlyphs.otf",
                      Path("C:/Windows/Fonts/msyh.ttc"), Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
                      Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc")]
        font_path = next((p for p in font_paths if p.exists()), None)
        font = ImageFont.truetype(str(font_path), 20) if font_path else ImageFont.load_default()
        piece_font = ImageFont.truetype(str(font_path), 32) if font_path else font
        ink = "#50321e"
        ruler_gap = 28 if width == 15 else 47
        for y in range(height):
            draw.line((ox, oy + y * cell, ox + (width - 1) * cell, oy + y * cell), fill=ink, width=2)
            for x in (ox - ruler_gap, ox + (width - 1) * cell + ruler_gap):
                draw.text((x, oy + y * cell), str(y + 1), font=font, fill=ink, anchor="mm")
        for x in range(width):
            px = ox + x * cell
            if width == 9 and x not in (0, 8):
                draw.line((px, oy, px, oy + 4 * cell), fill=ink, width=2)
                draw.line((px, oy + 5 * cell, px, oy + 9 * cell), fill=ink, width=2)
            else:
                draw.line((px, oy, px, oy + (height - 1) * cell), fill=ink, width=2)
            for py in (oy - ruler_gap, oy + (height - 1) * cell + ruler_gap):
                draw.text((px, py), chr(97 + x), font=font, fill=ink, anchor="mm")
        if width == 9:
            for y in (0, 7):
                draw.line((ox + 3 * cell, oy + y * cell, ox + 5 * cell, oy + (y + 2) * cell), fill=ink, width=2)
                draw.line((ox + 5 * cell, oy + y * cell, ox + 3 * cell, oy + (y + 2) * cell), fill=ink, width=2)
            draw.text((ox + 4 * cell, oy + 4.5 * cell), "楚 河       汉 界" if font_path else "CHU HE     HAN JIE", font=font, fill=ink, anchor="mm")
        else:
            for x, y in ((3, 3), (11, 3), (7, 7), (3, 11), (11, 11)):
                px, py = ox + x * cell, oy + y * cell
                draw.ellipse((px - 4, py - 4, px + 4, py + 4), fill=ink)
        last = room["last"]
        if isinstance(last, (list, tuple)):
            sx, sy = ox + last[0] % width * cell, oy + last[0] // width * cell
            draw.rectangle((sx - 12, sy - 12, sx + 12, sy + 12), outline="#197a80", width=3)
        for i, p in enumerate(room["board"]):
            if not p:
                continue
            px, py = ox + i % width * cell, oy + i // width * cell
            r = 18 if width == 15 else 27
            draw.ellipse((px - r + 2, py - r + 3, px + r + 2, py + r + 3), fill="#755034")
            fill = ("#272526" if p == 1 else "#faf7ed") if width == 15 else "#f8dfa9"
            draw.ellipse((px - r, py - r, px + r, py + r), fill=fill, outline=ink, width=2)
            if width == 9:
                chars = " 帅仕相马车炮兵" if p > 0 else " 将士象马车炮卒"
                text = chars[abs(p)] if font_path else " KAEHRCP"[abs(p)]
                color = "#ae2426" if p > 0 else "#222d39"
                draw.ellipse((px - r + 4, py - r + 4, px + r - 4, py + r - 4), outline=color, width=1)
                draw.text((px, py - 1), text, font=piece_font, fill=color, anchor="mm")
            if i == (last if width == 15 else last[1] if last is not None else None):
                draw.rectangle((px - r - 4, py - r - 4, px + r + 4, py + r + 4), outline="#197a80", width=3)
        filename = f"board_{room['id']}_{room['moves']}.png"
        self.image_dir.mkdir(parents=True, exist_ok=True)
        output = self.image_dir / filename
        temp = output.with_suffix(".tmp")
        canvas.save(temp, format="PNG")
        temp.replace(output)
        url = self.image_url(filename)
        return f"![{room['kind']}棋盘 #{size[0]}px #{size[1]}px]({url})"
