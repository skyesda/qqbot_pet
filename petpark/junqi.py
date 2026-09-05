"""Two-player open Junqi. Shared coordinates, legal automatic deployment."""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path


class Junqi:
    width, height = 5, 12
    # Strength increases from engineer to commander; flag/mine/bomb are special.
    names = {1: "工兵", 2: "排长", 3: "连长", 4: "营长", 5: "团长", 6: "旅长",
             7: "师长", 8: "军长", 9: "司令", 10: "地雷", 11: "炸弹", 12: "军旗"}
    values = {1: 130, 2: 60, 3: 85, 4: 110, 5: 150, 6: 200, 7: 260,
              8: 350, 9: 450, 10: 100, 11: 220, 12: 100000}
    camps = frozenset((11, 13, 17, 21, 23, 36, 38, 42, 46, 48))
    headquarters = frozenset((1, 3, 56, 58))

    @staticmethod
    def initial():
        # Top army: mines in rear two rows, bombs behind front row, camps empty.
        top = [10, 12, 10, 2, 10,
               3, 11, 4, 11, 3,
               5, 0, 7, 0, 5,
               6, 8, 0, 9, 6,
               1, 0, 7, 0, 1,
               2, 3, 1, 4, 2]
        return [-p for p in top] + list(reversed(top))

    @staticmethod
    def rail_neighbors(i):
        x, y = i % 5, i // 5
        result = []
        if y in (1, 5, 6, 10):
            result.extend(y * 5 + nx for nx in (x - 1, x + 1) if 0 <= nx < 5)
        if x in (0, 4):
            result.extend(ny * 5 + x for ny in (y - 1, y + 1) if 1 <= ny <= 10)
        if x == 2 and y in (5, 6):
            result.append((11 - y) * 5 + x)
        return result

    @classmethod
    def road_neighbors(cls, i):
        x, y = i % 5, i // 5
        result = set()
        for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0),
                       (1, 1), (1, -1), (-1, 1), (-1, -1)):
            nx, ny = x + dx, y + dy
            if not (0 <= nx < 5 and 0 <= ny < 12):
                continue
            j = ny * 5 + nx
            if y // 6 != ny // 6:
                if dx == 0 and x in (0, 2, 4):
                    result.add(j)
            elif not dx or not dy or i in cls.camps or j in cls.camps:
                result.add(j)
        return result

    @classmethod
    def destinations(cls, board, source):
        p = board[source]
        if not p or abs(p) in (10, 12) or source in cls.headquarters:
            return []
        side = 1 if p > 0 else -1
        targets = cls.road_neighbors(source)
        if abs(p) == 1:
            seen, pending = {source}, [source]
            while pending:
                for j in cls.rail_neighbors(pending.pop()):
                    if j in seen:
                        continue
                    seen.add(j)
                    targets.add(j)
                    if not board[j]:
                        pending.append(j)
        else:
            for neighbor in cls.rail_neighbors(source):
                delta, current = neighbor - source, source
                while current + delta in cls.rail_neighbors(current):
                    current += delta
                    targets.add(current)
                    if board[current]:
                        break
        return sorted(j for j in targets if j != source and board[j] * side <= 0
                      and not (j in cls.camps and board[j]))

    @classmethod
    def moves(cls, board, side):
        if 12 * side not in board:
            return []
        return [(i, j) for i, p in enumerate(board) if p * side > 0
                for j in cls.destinations(board, i)]

    @staticmethod
    def apply(board, move, side):
        result = board[:]
        source, target = move
        attacker, defender = board[source], board[target]
        a, d = abs(attacker), abs(defender)
        result[source] = 0
        if not defender:
            result[target] = attacker
        elif a == 11 or d == 11 or a == d:
            result[target] = 0
        elif d == 12 or (d == 10 and a == 1) or (d != 10 and a > d):
            result[target] = attacker
        return result

    @classmethod
    def evaluate(cls, board, side):
        return sum((1 if p > 0 else -1) * cls.values[abs(p)] for p in board if p) * side

    @classmethod
    def ranked(cls, board, side):
        return sorted(cls.moves(board, side),
                      key=lambda m: cls.evaluate(cls.apply(board, m, side), side), reverse=True)


RULES = """### 🚩 双人明棋规则
5 列×12 行，列 a–e、行 1–12，黑方在上、红方在下；双方坐标不翻转。红先黑后。
采用固定合法布阵，双方 25 枚棋子全部公开，单人玩家与邀请人执红。例：`军棋落子 a7 a6`。
司令＞军长＞师长＞旅长＞团长＞营长＞连长＞排长＞工兵；大吃小，同级同归于尽。
公路每次一步；铁路沿直线可走任意距离，工兵可沿空铁路转弯，都不能越子。
行营可沿相连斜线出入，营内棋子不能被攻击。中央仅 a、c、e 三条铁路连接双方。
地雷、军旗不能移动；任何棋子进入大本营后不能再移动。
工兵排雷后存活，其他普通棋子撞雷阵亡、地雷保留；炸弹与任何敌子同归于尽。
夺取军旗（包括炸弹炸旗）或令对手无合法着法即胜，无需先排光地雷。
同一局面及行棋方三次重复，或连续 120 步未交战，自动和棋。
浅绿圆形为行营，双框为大本营，双线带枕木为铁路；青框标记上一手。
"""


def render_junqi(room):
    assets = Path(__file__).parent / "assets" / "boards"
    with Image.open(assets / "junqi-base.png") as base:
        canvas = base.convert("RGB").resize((760, 1180), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype(str(assets / "JunqiGlyphs.otf"), 22)
    small = ImageFont.truetype(str(assets / "JunqiGlyphs.otf"), 17)
    title = ImageFont.truetype(str(assets / "JunqiGlyphs.otf"), 30)
    ink = "#50321e"

    def point(i):
        return 140 + i % 5 * 120, 150 + i // 5 * 74 + (42 if i // 5 >= 6 else 0)

    draw.text((380, 65), "军棋 · 双人明棋", font=title, fill=ink, anchor="mm")
    for i in range(60):
        for j in Junqi.road_neighbors(i):
            if j > i:
                draw.line((*point(i), *point(j)), fill=ink, width=2)
        for j in Junqi.rail_neighbors(i):
            if j <= i:
                continue
            x, y = point(i)
            xx, yy = point(j)
            dx, dy = (1, 0) if y == yy else (0, 1)
            for offset in (-4, 4):
                draw.line((x + dy * offset, y + dx * offset,
                           xx + dy * offset, yy + dx * offset), fill=ink, width=2)
            length = abs(xx - x) + abs(yy - y)
            for distance in range(9, length, 12):
                px, py = x + dx * distance, y + dy * distance
                draw.line((px - dy * 6, py - dx * 6, px + dy * 6, py + dx * 6), fill=ink, width=1)
    for i in range(60):
        x, y = point(i)
        if i in Junqi.camps:
            draw.ellipse((x - 33, y - 25, x + 33, y + 25), fill="#dce2b9", outline="#626541", width=2)
            draw.text((x, y), "行营", font=small, fill="#626541", anchor="mm")
        else:
            draw.rounded_rectangle((x - 39, y - 24, x + 39, y + 24), radius=5,
                                   fill="#e7c48b", outline=ink, width=2)
            if i in Junqi.headquarters:
                draw.rectangle((x - 44, y - 29, x + 44, y + 29), outline=ink, width=2)
                draw.text((x, y), "本营", font=small, fill=ink, anchor="mm")
    for x in range(5):
        for y in (109, 1060):
            draw.text((140 + x * 120, y), chr(97 + x), font=font, fill=ink, anchor="mm")
    for y in range(12):
        for x in (77, 683):
            draw.text((x, point(y * 5)[1]), str(y + 1), font=font, fill=ink, anchor="mm")
    for i, p in enumerate(room["board"]):
        if not p:
            continue
        x, y = point(i)
        color = "#ae2426" if p > 0 else "#222d39"
        draw.rounded_rectangle((x - 38 + 2, y - 23 + 4, x + 38 + 2, y + 23 + 4), radius=6, fill="#755034")
        draw.rounded_rectangle((x - 38, y - 23, x + 38, y + 23), radius=6, fill="#f8dfa9", outline=ink, width=2)
        draw.rounded_rectangle((x - 33, y - 18, x + 33, y + 18), radius=4, outline=color, width=1)
        draw.text((x, y - 1), Junqi.names[abs(p)], font=font, fill=color, anchor="mm")
    if room.get("last") is not None:
        for n, i in enumerate(room["last"]):
            x, y = point(i)
            r = 10 if n == 0 else 43
            h = 10 if n == 0 else 28
            draw.rectangle((x - r, y - h, x + r, y + h), outline="#197a80", width=3)
    draw.text((380, 1102), "红方先行 · 棋子公开 · 夺旗获胜", font=small, fill=ink, anchor="mm")
    draw.text((380, 1130), "军棋落子 a7 a6", font=small, fill=ink, anchor="mm")
    return canvas
