"""Visual QA for all four sizes and end-state symbols, using the production renderer."""
import sys
import time
import random
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from petpark import data
from petpark.minesweeper_view import render

out = Path(__file__).resolve().parents[1] / "board-previews"
out.mkdir(exist_ok=True)
for diff, cfg in data.MS_DIFFICULTIES.items():
    w, h = cfg["size"]
    candidates = [(x, y) for y in range(h) for x in range(w) if (x, y) not in ((0, 0), (1, 1))]
    mines = {(0, 0)} | set(random.Random(diff).sample(candidates, cfg["mines"] - 1))
    numbers = {(x, y): sum((x + dx, y + dy) in mines for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy)
               for y in range(h) for x in range(w)}
    session = {"w": w, "h": h, "difficulty": diff, "deadline": time.time() + 325,
               "mines_total": cfg["mines"], "mines": mines, "numbers": numbers,
               "opened": {(x, y) for y in range(h) for x in range(w) if 1 < x < w - 1 and 1 < y < h - 1 and (x, y) not in mines},
               "flags": {(0, 0), (1, 1)}}
    for state in ("playing", "ended"):
        img = render(session, cfg, reveal=state == "ended", boom=(0, 0) if state == "ended" else None)
        path = out / f"minesweeper-{diff}-{state}.png"
        img.save(path)
        print(path)
