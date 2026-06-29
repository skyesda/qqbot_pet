"""宠物种类图片定位（纯本地，无网络）。

图片在开发期由 ``tools/gen_pet_images.py`` 一次性生成，随插件打包进仓库，
存放于 ``petpark/assets/pets/<种类名>.jpg``（统一 512×512、纯白背景）。
运行时只按宠物的 ``species`` 字段查找对应文件，存在则返回路径，供消息链配图。
"""

from __future__ import annotations

from pathlib import Path

# 资源目录与本模块同包：petpark/assets/pets/
PETS_DIR = Path(__file__).parent / "assets" / "pets"


def pet_image_path(species: str | None) -> str | None:
    """按种类名返回图片绝对路径；不存在或无种类时返回 None。"""
    if not species:
        return None
    p = PETS_DIR / f"{species}.jpg"
    return str(p) if p.exists() else None


def all_image_count() -> int:
    """已打包的宠物图片数量（用于自检/排查）。"""
    if not PETS_DIR.exists():
        return 0
    return sum(1 for _ in PETS_DIR.glob("*.jpg"))
