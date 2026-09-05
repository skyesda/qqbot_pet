"""19x19 Go with positional superko and explicit play-out area scoring."""
import random
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


class Go:
    width = height = 19
    komi = 7.5

    @staticmethod
    def initial():
        return [0] * 361

    @staticmethod
    def key(board):
        return ''.join(str(p + 1) for p in board)

    @staticmethod
    def neighbors(i):
        x, y = i % 19, i // 19
        return [ny * 19 + nx for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))
                if 0 <= nx < 19 and 0 <= ny < 19]

    @classmethod
    def group(cls, board, source):
        stones, liberties, pending = {source}, set(), [source]
        while pending:
            for i in cls.neighbors(pending.pop()):
                if board[i] == 0:
                    liberties.add(i)
                elif board[i] == board[source] and i not in stones:
                    stones.add(i)
                    pending.append(i)
        return stones, liberties

    @classmethod
    def apply(cls, board, move, side):
        if move is None:
            return board[:]
        if not isinstance(move, int) or not 0 <= move < 361 or board[move]:
            raise ValueError('该交点已有棋子或坐标超出棋盘。')
        result = board[:]
        result[move] = side
        checked = set()
        for i in cls.neighbors(move):
            if result[i] == -side and i not in checked:
                stones, liberties = cls.group(result, i)
                checked.update(stones)
                if not liberties:
                    for j in stones:
                        result[j] = 0
        if not cls.group(result, move)[1]:
            raise ValueError('禁入点：此处落子无气，且不能提掉对方棋子。')
        return result

    @classmethod
    def legal_result(cls, board, move, side, history=()):
        result = cls.apply(board, move, side)
        if move is not None and cls.key(result) in history:
            raise ValueError('打劫／同形禁止：不能重现本局已有盘面，请先在别处落子或停一手。')
        return result

    @classmethod
    def moves(cls, board, side, history=()):
        result = []
        for i, p in enumerate(board):
            if p:
                continue
            try:
                cls.legal_result(board, i, side, history)
                result.append(i)
            except ValueError:
                pass
        return result

    @classmethod
    def score(cls, board):
        scores = {1: board.count(1), -1: board.count(-1)}
        seen = set()
        for i, p in enumerate(board):
            if p or i in seen:
                continue
            empty, border, pending = {i}, set(), [i]
            seen.add(i)
            while pending:
                for j in cls.neighbors(pending.pop()):
                    if board[j]:
                        border.add(board[j])
                    elif j not in seen:
                        seen.add(j)
                        empty.add(j)
                        pending.append(j)
            if len(border) == 1:
                scores[next(iter(border))] += len(empty)
        return scores[1], scores[-1] + cls.komi

    @classmethod
    def evaluate(cls, board, side):
        # Local tactical value; full area scoring is reserved for the final position.
        seen, value = set(), 0
        for i, p in enumerate(board):
            if not p or i in seen:
                continue
            stones, liberties = cls.group(board, i)
            seen.update(stones)
            danger = len(stones) * (18 if len(liberties) == 1 else 3 if len(liberties) == 2 else 0)
            value += p * (len(stones) * 12 + min(len(liberties), 8) * 2 - danger)
        return side * value

    @classmethod
    def ranked(cls, board, side, history=()):
        candidates = []
        for i in cls.moves(board, side, history):
            adjacent = cls.neighbors(i)
            # Avoid filling a clearly owned eye unless the move captures.
            nxt = cls.apply(board, i, side)
            captures = board.count(-side) - nxt.count(-side)
            if all(board[j] == side for j in adjacent) and not captures:
                continue
            x, y = i % 19, i // 19
            edge = min(x, y, 18 - x, 18 - y)
            shape = 4 if edge in (2, 3) else -3 if edge == 0 else 0
            nearby = sum(board[j] != 0 for j in adjacent)
            score = captures * 30 + shape + nearby + cls.evaluate(nxt, side)
            candidates.append((score, i, nxt))
        return sorted(candidates, key=lambda c: (-c[0], c[1]))


def go_ai(board, side, difficulty, history=(), passes=0):
    if passes:
        black, white = Go.score(board)
        if (black - white) * side > 0:
            return None
    choices = Go.ranked(board, side, history)
    if not choices:
        return None
    if difficulty == 1:
        return random.choice(choices)[1]
    if difficulty == 2:
        return choices[0][1]
    deadline = time.perf_counter() + (0.5 if difficulty == 3 else 1.2)
    best, best_value = choices[0][1], -float('inf')
    for _, move, nxt in choices[:4 if difficulty == 3 else 10]:
        if time.perf_counter() >= deadline:
            break
        seen = set(history) | {Go.key(nxt)}
        worst = Go.evaluate(nxt, side)
        # Opponent replies include every legal point, with bounded evaluation time.
        complete = True
        for j, p in enumerate(nxt):
            if time.perf_counter() >= deadline:
                complete = False
                break
            if p:
                continue
            try:
                reply = Go.legal_result(nxt, j, -side, seen)
            except ValueError:
                continue
            worst = min(worst, Go.evaluate(reply, side))
        if complete and worst > best_value:
            best, best_value = move, worst
    return best


RULES = """## ⚫ 围棋 · 19 路对弈
### 快速开始
- `围棋单人 2` / `开始围棋2`：单人，玩家执黑先行；默认难度 1。
- `围棋双人 @对方` / `围棋邀请 用户ID`：邀请人执黑，对方发送 `接受棋局` 开局。
- `围棋落子 d4`：在空交点落子。列 a–s（包含 i），行 1–19；双方视角相同，大小写均可。
- `围棋停一手` / `围棋过`：放弃本回合落子；双方连续停一手即结算。
- `围棋棋盘` / `棋局`：查看棋盘、轮次、提子数及计时；`放弃围棋` / `认输`：认输。
- `围棋` / `围棋帮助` / `围棋介绍` / `围棋指令`：本页。

### 规则与计分
同色棋子按上下左右连成一块，紧邻的空交点是气；落子后先提走无气的敌方整块棋子。
禁止自杀，禁止落子后重现本局任意已有盘面（全局同形禁止，停一手豁免）。
采用盘面面积计分：在盘棋子 + 只与己方接壤的空区；双方共同接壤或无边界的空区不计分。
白贴 7.5 点，提子数仅作记录，不重复加分。双方连续停一手表示接受当前盘面并立即结算。
本版不自动判死活或协商移除死子：请先实际提掉死子、收完官子，再停一手；中途面积不代表最终胜负。
九个星位标记标准 19 路棋盘，青框标记上一手，停一手后取消落点标记。

### 四档休闲 AI
1 简单：随机合法候选；2 普通：提子、气与棋形评分；3 困难：少量候选的两层攻防；4 地狱：更多候选的两层攻防。
支持数字 1–4 或中文难度名。AI 是本地休闲算法，非专业围棋引擎；不会填明显的己方眼位，无合适候选或对方停一手且己方领先时停一手。
"""


def render_go(room):
    assets = Path(__file__).parent / 'assets' / 'boards'
    with Image.open(assets / 'wood-base.png') as base:
        canvas = base.convert('RGB').resize((1000, 1060), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype(str(assets / 'GoGlyphs.otf'), 19)
    title = ImageFont.truetype(str(assets / 'GoGlyphs.otf'), 29)
    ink, cell, ox, oy = '#50321e', 43, 113, 146
    draw.text((500, 62), '围棋 · 十九路', font=title, fill=ink, anchor='mm')
    for n in range(19):
        x, y = ox + n * cell, oy + n * cell
        draw.line((ox, y, ox + 18 * cell, y), fill=ink, width=2)
        draw.line((x, oy, x, oy + 18 * cell), fill=ink, width=2)
        for px in (ox - 34, ox + 18 * cell + 34):
            draw.text((px, y), str(n + 1), font=font, fill=ink, anchor='mm')
        for py in (oy - 34, oy + 18 * cell + 34):
            draw.text((x, py), chr(97 + n), font=font, fill=ink, anchor='mm')
    for x in (3, 9, 15):
        for y in (3, 9, 15):
            px, py = ox + x * cell, oy + y * cell
            draw.ellipse((px - 4, py - 4, px + 4, py + 4), fill=ink)
    for i, p in enumerate(room['board']):
        if not p:
            continue
        x, y = ox + i % 19 * cell, oy + i // 19 * cell
        draw.ellipse((x - 17, y - 16, x + 21, y + 22), fill='#80613e')
        draw.ellipse((x - 19, y - 19, x + 19, y + 19),
                     fill='#272526' if p == 1 else '#faf7ed', outline=ink, width=1)
        draw.arc((x - 15, y - 15, x + 13, y + 13), 200, 290,
                 fill='#555253' if p == 1 else '#ffffff', width=2)
    if room.get('last') is not None:
        i = room['last']
        x, y = ox + i % 19 * cell, oy + i // 19 * cell
        draw.rectangle((x - 22, y - 22, x + 22, y + 22), outline='#197a80', width=3)
    captures = room.get('captures', {'1': 0, '-1': 0})
    draw.text((500, 986), f"黑提子 {captures['1']} · 白提子 {captures['-1']} · 白贴 7.5 点",
              font=font, fill=ink, anchor='mm')
    draw.text((500, 1018), '围棋落子 d4 · 围棋停一手', font=font, fill=ink, anchor='mm')
    return canvas
