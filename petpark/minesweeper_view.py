"""Presentation-only Minesweeper renderer; consumes the existing session unchanged."""
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).parent / "assets" / "boards"
INK = "#edf4e9"
MUTED = "#a9c9c0"
GOLD = "#edc783"
NUMBERS = {1: "#246ba2", 2: "#257351", 3: "#b64040", 4: "#6f4d9a", 5: "#925127", 6: "#147d83", 7: "#35394b", 8: "#646b66"}


def font(size):
    return ImageFont.truetype(str(ASSETS / "BoardGlyphs.otf"), size)


def draw_flag(draw, x, y, scale=1):
    draw.line((x - 5 * scale, y - 13 * scale, x - 5 * scale, y + 12 * scale), fill="#fff0cc", width=max(2, int(3 * scale)))
    draw.polygon([(x - 3 * scale, y - 13 * scale), (x + 15 * scale, y - 6 * scale), (x - 3 * scale, y + scale)], fill="#f2bb65")
    draw.line((x - 11 * scale, y + 13 * scale, x + 4 * scale, y + 13 * scale), fill="#fff0cc", width=2)


def draw_mine(draw, x, y, scale=1):
    r = 9 * scale
    for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
        draw.line((x - dx * r * 1.45, y - dy * r * 1.45, x + dx * r * 1.45, y + dy * r * 1.45), fill="#3f4144", width=3)
    draw.ellipse((x - r, y - r, x + r, y + r), fill="#303739", outline="#131e24", width=2)
    draw.ellipse((x - r / 2, y - r / 2, x - r / 8, y - r / 8), fill="#a3b5b3")


def render(session, cfg, reveal=False, boom=None):
    w, h = session["w"], session["h"]
    cell = 64 if w <= 6 else 56 if w <= 9 else 52
    width = max(560, w * cell + 128)
    ox, oy = (width - w * cell) // 2, 248
    height = oy + h * cell + 166
    with Image.open(ASSETS / "minesweeper-base.png") as base:
        img = base.convert("RGB").resize((width, height), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(img)
    small, body, big, number = font(15), font(19), font(32), font(28)
    left, right = 44, width - 44
    title_x = max(80, int(width * 0.14))
    draw.text((title_x, 44), "PET PARK  /  MINESWEEPER", font=small, fill=GOLD)
    draw.text((title_x, 70), "宠物扫雷", font=big, fill=INK)
    badge = f"{session['difficulty']:02d}  {cfg['name']}"
    badge_right = width - title_x
    draw.rounded_rectangle((badge_right - 126, 72, badge_right, 111), radius=12, fill="#284840", outline="#b99d65")
    draw.text((badge_right - 63, 91), badge, font=body, fill=GOLD, anchor="mm")
    remain = max(0, int(session["deadline"] - time.time()))
    safe = w * h - session["mines_total"]
    opened = len(session["opened"])
    state = "探索中" if not reveal else "挑战成功" if opened >= safe else "触雷结束" if boom else "探索结束"
    draw.text((title_x, 114), f"{state}  ·  {w} × {h} 棋盘", font=small, fill=MUTED)
    gap = 10
    card_w = (right - left - gap * 2) // 3
    values = [("待标记 / 总雷数", f"{max(0, session['mines_total'] - len(session['flags']))} / {session['mines_total']}"),
              ("剩余时间", f"{remain // 60:02d}:{remain % 60:02d}"), ("安全格进度", f"{opened} / {safe}")]
    for i, (title, value) in enumerate(values):
        x = left + i * (card_w + gap)
        draw.rounded_rectangle((x, 145, x + card_w, 210), radius=12, fill="#17332f", outline="#38574d")
        draw.text((x + 13, 154), title, font=small, fill=MUTED)
        draw.text((x + 13, 177), value, font=font(23), fill="#ffb5a2" if i == 1 and remain <= 60 else GOLD)
    # A separate coordinate gutter keeps labels out of playable cells.
    draw.rounded_rectangle((ox - 8, oy - 8, ox + w * cell + 8, oy + h * cell + 8), radius=12, fill="#0b201e", outline="#5e8070", width=2)
    for x in range(w):
        px = ox + x * cell + cell / 2
        for py in (oy - 23, oy + h * cell + 25):
            draw.text((px, py), chr(97 + x), font=small, fill=GOLD, anchor="mm")
    for y in range(h):
        py = oy + y * cell + cell / 2
        for px in (ox - 25, ox + w * cell + 25):
            draw.text((px, py), str(y + 1), font=small, fill=GOLD, anchor="mm")
        for x in range(w):
            pos = (x, y)
            px, py = ox + x * cell, oy + y * cell
            cx, cy = px + cell / 2, py + cell / 2
            flagged, is_open = pos in session["flags"], pos in session["opened"]
            is_mine = pos in (session["mines"] or ())
            show_mine = reveal and is_mine
            tile = (px + 3, py + 3, px + cell - 3, py + cell - 3)
            if is_open or show_mine or (reveal and flagged):
                fill = "#f4e3c0" if (x + y) % 2 else "#e8d7b5"
                if show_mine:
                    fill = "#f3b4a0" if pos == boom else "#d8c8af"
                draw.rounded_rectangle(tile, radius=7, fill=fill)
                if show_mine:
                    draw_mine(draw, cx, cy)
                    if flagged:
                        draw.ellipse((px + 7, py + 7, px + 13, py + 13), fill="#2c7b52")
                elif reveal and flagged and not is_mine:
                    draw.line((cx - 9, cy - 9, cx + 9, cy + 9), fill="#b4413d", width=4)
                    draw.line((cx + 9, cy - 9, cx - 9, cy + 9), fill="#b4413d", width=4)
                else:
                    num = session["numbers"].get(pos, 0)
                    if num:
                        draw.text((cx, cy - 1), str(num), font=number, fill=NUMBERS.get(num, "#303c36"), anchor="mm")
                    else:
                        draw.ellipse((cx - 2, cy - 2, cx + 2, cy + 2), fill="#b4ad91")
            else:
                draw.rounded_rectangle((px + 3, py + 6, px + cell - 3, py + cell - 1), radius=7, fill="#081c1a")
                draw.rounded_rectangle(tile, radius=7, fill="#315f52" if (x + y) % 2 else "#2c564b", outline="#52826a")
                draw.line((px + 11, py + 5, px + cell - 11, py + 5), fill="#719b7d", width=1)
                if flagged:
                    draw_flag(draw, cx, cy - 1)
                else:
                    draw.text((cx, cy - 1), f"{chr(97 + x)}{y + 1}", font=font(17), fill="#d5e5cc", anchor="mm")
    bar_y = oy + h * cell + 48
    draw.rounded_rectangle((left, bar_y, right, bar_y + 5), radius=2, fill="#25443a")
    if opened:
        draw.rounded_rectangle((left, bar_y, left + max(5, (right - left) * min(1, opened / max(1, safe))), bar_y + 5), radius=2, fill=GOLD)
    draw.text((width / 2, bar_y + 28), "扫 a1  翻开   ·   插旗 a1  标记 / 取消", font=small, fill=INK, anchor="mm")
    draw.text((width / 2, bar_y + 52), "浅色为已翻开  ·  数字为周围雷数" if not reveal else "棋局已结束  ·  发送 开始扫雷 再挑战", font=small, fill=MUTED, anchor="mm")
    return img
