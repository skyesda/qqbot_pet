"""Dou Shou Qi (Jungle / Animal Chess): rules, AI helpers and renderer."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


class Doushouqi:
    """Positive red starts at the bottom; negative blue starts at the top."""

    width, height = 7, 9
    names = {1: "鼠", 2: "猫", 3: "狗", 4: "狼", 5: "豹", 6: "虎", 7: "狮", 8: "象"}
    values = {1: 90, 2: 120, 3: 170, 4: 230, 5: 310, 6: 430, 7: 520, 8: 650}
    rivers = frozenset(y * 7 + x for y in (3, 4, 5) for x in (1, 2, 4, 5))
    dens = {-1: 3, 1: 59}
    traps = {-1: frozenset((2, 4, 10)), 1: frozenset((52, 58, 60))}

    @staticmethod
    def initial():
        board = [0] * 63
        top = {0: 7, 6: 6, 8: 3, 12: 2, 14: 1, 16: 5, 18: 4, 20: 8}
        bottom = {42: 8, 44: 4, 46: 5, 48: 1, 50: 2, 54: 3, 56: 6, 62: 7}
        for i, piece in top.items():
            board[i] = -piece
        for i, piece in bottom.items():
            board[i] = piece
        return board

    @classmethod
    def _jump(cls, board, source, dx, dy):
        """Return a lion/tiger river-jump destination, or None."""
        x, y = source % 7, source // 7
        nx, ny = x + dx, y + dy
        if not (0 <= nx < 7 and 0 <= ny < 9) or ny * 7 + nx not in cls.rivers:
            return None
        while 0 <= nx < 7 and 0 <= ny < 9 and ny * 7 + nx in cls.rivers:
            if board[ny * 7 + nx]:  # Only rats can be in water, and any rat blocks the jump.
                return None
            nx, ny = nx + dx, ny + dy
        return ny * 7 + nx if 0 <= nx < 7 and 0 <= ny < 9 else None

    @classmethod
    def can_capture(cls, board, source, target):
        attacker, defender = board[source], board[target]
        if not defender or attacker * defender >= 0:
            return not defender
        side = 1 if attacker > 0 else -1
        # A trapped enemy loses all rank while standing in the attacker's traps.
        if target in cls.traps[side]:
            return True
        a, d = abs(attacker), abs(defender)
        if a == 1 and ((source in cls.rivers) != (target in cls.rivers)):
            return False
        if a == 1 and d == 8:
            return True
        if a == 8 and d == 1:
            return False
        return a >= d

    @classmethod
    def destinations(cls, board, source):
        piece = board[source]
        if not piece:
            return []
        side, rank = (1 if piece > 0 else -1), abs(piece)
        x, y = source % 7, source // 7
        result = []
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if not (0 <= nx < 7 and 0 <= ny < 9):
                continue
            target = ny * 7 + nx
            if rank in (6, 7):
                jump = cls._jump(board, source, dx, dy)
                if jump is not None:
                    target = jump
            if target in cls.rivers and rank != 1:
                continue
            if target == cls.dens[side]:
                continue
            if cls.can_capture(board, source, target):
                result.append(target)
        return result

    @classmethod
    def moves(cls, board, side):
        return [(i, target) for i, piece in enumerate(board) if piece * side > 0
                for target in cls.destinations(board, i)]

    @staticmethod
    def apply(board, move, side):
        source, target = move
        result = board[:]
        result[target], result[source] = result[source], 0
        return result

    @classmethod
    def winner(cls, board):
        if board[cls.dens[-1]] > 0:
            return 1
        if board[cls.dens[1]] < 0:
            return -1
        if not any(piece > 0 for piece in board):
            return -1
        if not any(piece < 0 for piece in board):
            return 1
        return 0

    @classmethod
    def evaluate(cls, board, side):
        winner = cls.winner(board)
        if winner:
            return 10_000_000 if winner == side else -10_000_000
        score = 0
        for i, piece in enumerate(board):
            if not piece:
                continue
            owner = 1 if piece > 0 else -1
            enemy_den = cls.dens[-owner]
            distance = abs(i % 7 - enemy_den % 7) + abs(i // 7 - enemy_den // 7)
            value = cls.values[abs(piece)] + (14 - distance) * 8
            score += owner * value
        return score * side

    @classmethod
    def ranked(cls, board, side):
        enemy_den = cls.dens[-side]
        return sorted(cls.moves(board, side), key=lambda move: (
            move[1] == enemy_den,
            cls.values.get(abs(board[move[1]]), 0),
            -abs(move[1] % 7 - enemy_den % 7) - abs(move[1] // 7 - enemy_den // 7),
        ), reverse=True)


RULES = """### 🐾 棋盘与胜负
7 列×9 行，列 a–g 从左到右、行 1–9 从上到下；蓝方在上、红方在下，双方坐标不翻转。红先蓝后，单人玩家与双人邀请人执红。
棋子由弱到强为鼠、猫、狗、狼、豹、虎、狮、象。通常只能吃同级或更弱的棋子；鼠能吃象，象不能吃鼠。
所有棋子每步上下左右走一格。只有鼠能进河；水陆交界处的鼠不能互吃。虎、狮可沿直线跳过整条河，但河中有任意一只鼠时不能跳。
棋子进入对方陷阱后失去战力，可被任意敌子吃掉；不能进入己方兽穴。任一棋子进入对方兽穴，或吃光对方棋子、令对方无合法着法即获胜。
青色边框标记上一步，浅蓝河流、金色兽穴与红色陷阱均在棋盘上标出。
"""


def render_doushouqi(room):
    asset = Path(__file__).parent / "assets" / "boards" / "doushouqi-base.png"
    with Image.open(asset) as base:
        canvas = base.convert("RGB").resize((840, 1080), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(canvas)
    font_path = Path(__file__).parent / "assets" / "boards" / "DoushouqiGlyphs.otf"
    font = ImageFont.truetype(str(font_path), 22)
    piece_font = ImageFont.truetype(str(font_path), 30)
    small = ImageFont.truetype(str(font_path), 18)
    ox, oy, cell = 112, 150, 88
    ink, red, blue, teal = "#50321e", "#a9272b", "#263f68", "#197a80"

    for y in range(9):
        for x in range(7):
            left, top = ox + x * cell, oy + y * cell
            idx = y * 7 + x
            fill = "#8bc2c9" if idx in Doushouqi.rivers else "#e8c27d"
            if idx in Doushouqi.dens.values():
                fill = "#d8aa45"
            elif idx in Doushouqi.traps[-1] or idx in Doushouqi.traps[1]:
                fill = "#d89b88"
            draw.rounded_rectangle((left + 4, top + 4, left + cell - 4, top + cell - 4),
                                   radius=10, fill=fill, outline=ink, width=2)
            if idx in Doushouqi.dens.values():
                draw.text((left + 44, top + 44), "穴", font=piece_font, fill=ink, anchor="mm")
            elif idx in Doushouqi.traps[-1] or idx in Doushouqi.traps[1]:
                draw.text((left + 44, top + 44), "陷", font=piece_font, fill="#7a2826", anchor="mm")
    for x in range(7):
        draw.text((ox + x * cell + 44, oy - 23), chr(97 + x), font=font, fill=ink, anchor="mm")
        draw.text((ox + x * cell + 44, oy + 9 * cell + 23), chr(97 + x), font=font, fill=ink, anchor="mm")
    for y in range(9):
        draw.text((ox - 23, oy + y * cell + 44), str(y + 1), font=font, fill=ink, anchor="mm")
        draw.text((ox + 7 * cell + 23, oy + y * cell + 44), str(y + 1), font=font, fill=ink, anchor="mm")

    last = room.get("last")
    if last is not None:
        for idx, size in ((last[0], 29), (last[1], 37)):
            x, y = idx % 7, idx // 7
            cx, cy = ox + x * cell + 44, oy + y * cell + 44
            draw.rectangle((cx - size, cy - size, cx + size, cy + size), outline=teal, width=4)
    for i, piece in enumerate(room["board"]):
        if not piece:
            continue
        x, y = i % 7, i // 7
        cx, cy = ox + x * cell + 44, oy + y * cell + 44
        color = red if piece > 0 else blue
        draw.ellipse((cx - 33 + 3, cy - 33 + 4, cx + 33 + 3, cy + 33 + 4), fill="#755034")
        draw.ellipse((cx - 33, cy - 33, cx + 33, cy + 33), fill="#f7dfaa", outline=color, width=4)
        draw.ellipse((cx - 27, cy - 27, cx + 27, cy + 27), outline=color, width=2)
        draw.text((cx, cy - 1), Doushouqi.names[abs(piece)], font=piece_font, fill=color, anchor="mm")
    draw.text((420, 82), "斗兽棋", font=piece_font, fill=ink, anchor="mm")
    draw.text((420, 1015), "落 a7 a6", font=small, fill=ink, anchor="mm")
    return canvas
