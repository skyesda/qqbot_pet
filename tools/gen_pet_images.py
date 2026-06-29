#!/usr/bin/env python3
"""宠物种类图片生成脚本（仅开发/部署期一次性使用，不在运行时调用）。

调用 flyyye 的 OpenAI 兼容生图接口，为 petpark/data.py 中的每个宠物种类生成
一张图片，统一处理为 512×512、纯白背景的 JPG，保存到 petpark/assets/pets/。

用法::

    # 必须通过环境变量提供密钥，密钥不会写入代码/仓库
    FLYYYE_KEY=sk-xxxx python tools/gen_pet_images.py            # 生成所有缺失的
    FLYYYE_KEY=sk-xxxx python tools/gen_pet_images.py --force 皮卡丘   # 重生成指定种类
    FLYYYE_KEY=sk-xxxx python tools/gen_pet_images.py --only 皮卡丘 九尾狐  # 只生成这些
    FLYYYE_KEY=sk-xxxx python tools/gen_pet_images.py --smoke     # 冒烟：只生成第 1 个并打印响应结构

可选环境变量::

    FLYYYE_BASE   默认 https://api.flyyye.cn/v1
    FLYYYE_MODEL  默认 gpt-image-2
    GEN_SIZE      生图请求尺寸，默认 1024x1024
    OUT_SIZE      落地正方形边长，默认 512
    WORKERS       并发数，默认 4

依赖：Pillow（pip install pillow）。HTTP 用标准库 urllib，无需 requests。
"""

from __future__ import annotations

import argparse
import ast
import base64
import io
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit("缺少 Pillow，请先安装：pip install pillow")

ROOT = Path(__file__).resolve().parent.parent
DATA_PY = ROOT / "petpark" / "data.py"
OUT_DIR = ROOT / "petpark" / "assets" / "pets"

BASE = os.environ.get("FLYYYE_BASE", "https://api.flyyye.cn/v1").rstrip("/")
MODEL = os.environ.get("FLYYYE_MODEL", "gpt-image-2")
GEN_SIZE = os.environ.get("GEN_SIZE", "1024x1024")
OUT_SIZE = int(os.environ.get("OUT_SIZE", "512"))
WORKERS = int(os.environ.get("WORKERS", "4"))
RETRIES = 3
TIMEOUT = 180


def load_species() -> dict[str, str]:
    """从 petpark/data.py 解析 SPECIES 字典（不 import，避免依赖 astrbot）。"""
    tree = ast.parse(DATA_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "SPECIES":
                    return ast.literal_eval(node.value)
    raise RuntimeError("未在 data.py 中找到 SPECIES 字典")


def build_prompt(species: str, element: str) -> str:
    return (
        f"一只名为「{species}」的可爱卡通游戏宠物，{element}属性，"
        f"全身居中，纯白色背景(#FFFFFF)，无文字、无边框、无地面阴影，"
        f"高质量数字插画风格，正方形构图"
    )


def call_api(prompt: str, key: str) -> bytes:
    """调用生图接口，返回原始图片字节。兼容 b64_json / url 两种响应。"""
    body = json.dumps(
        {"model": MODEL, "prompt": prompt, "n": 1, "size": GEN_SIZE}
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/images/generations",
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    item = payload["data"][0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    if item.get("url"):
        with urllib.request.urlopen(item["url"], timeout=TIMEOUT) as r2:
            return r2.read()
    raise RuntimeError(f"响应无 b64_json/url 字段：{json.dumps(payload)[:300]}")


def to_white_square(raw: bytes, size: int) -> Image.Image:
    """贴到纯白正方形画布并统一尺寸（保持比例居中，不拉伸变形）。"""
    src = Image.open(io.BytesIO(raw)).convert("RGBA")
    canvas = Image.new("RGB", (size, size), (255, 255, 255))
    fitted = src.copy()
    fitted.thumbnail((size, size), Image.LANCZOS)
    x = (size - fitted.width) // 2
    y = (size - fitted.height) // 2
    # 用 alpha 通道作为蒙版，使透明区域显示为白色
    canvas.paste(fitted, (x, y), fitted)
    return canvas


def gen_one(species: str, element: str, key: str, force: bool) -> tuple[str, str]:
    out = OUT_DIR / f"{species}.jpg"
    if out.exists() and not force:
        return species, "skip"
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            raw = call_api(build_prompt(species, element), key)
            img = to_white_square(raw, OUT_SIZE)
            out.parent.mkdir(parents=True, exist_ok=True)
            img.save(out, "JPEG", quality=88)
            return species, "ok"
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2 * attempt)
    return species, f"fail: {last_err}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", nargs="*", default=None,
                    help="重生成指定种类（覆盖已存在）；不带名则强制全部重生成")
    ap.add_argument("--only", nargs="*", default=None,
                    help="只处理指定种类")
    ap.add_argument("--smoke", action="store_true",
                    help="冒烟测试：只生成第 1 个并打印响应结构")
    args = ap.parse_args()

    key = os.environ.get("FLYYYE_KEY")
    if not key:
        sys.exit("请通过环境变量 FLYYYE_KEY 提供 API 密钥")

    species_map = load_species()
    names = list(species_map.keys())
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.smoke:
        name = names[0]
        print(f"[smoke] base={BASE} model={MODEL} 生成：{name}")
        sp, status = gen_one(name, species_map[name], key, force=True)
        print(f"[smoke] {sp} -> {status}")
        return

    if args.only:
        targets = [n for n in args.only if n in species_map]
        force_set = set(targets)  # only 模式下默认覆盖
    else:
        force_arg = args.force
        if force_arg is None:
            force_set: set[str] = set()
            targets = names
        elif len(force_arg) == 0:
            force_set = set(names)
            targets = names
        else:
            force_set = {n for n in force_arg if n in species_map}
            targets = list(force_set)

    todo = [n for n in targets
            if force_set and n in force_set or not (OUT_DIR / f"{n}.jpg").exists()]
    print(f"共 {len(names)} 种，本次待生成 {len(todo)} 张（并发 {WORKERS}）")

    ok = skip = fail = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(gen_one, n, species_map[n], key, n in force_set): n
                for n in todo}
        done = 0
        total = len(futs)
        for fut in as_completed(futs):
            sp, status = fut.result()
            done += 1
            if status == "ok":
                ok += 1
            elif status == "skip":
                skip += 1
            else:
                fail += 1
            print(f"[{done}/{total}] {sp} -> {status}")

    have = sum(1 for _ in OUT_DIR.glob("*.jpg"))
    print(f"完成：成功 {ok}，跳过 {skip}，失败 {fail}；当前已存图片 {have}/{len(names)}")
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
