"""宠物种类图片定位（纯本地，无网络）。

图片在开发期由 ``tools/gen_pet_images.py`` 一次性生成，随插件打包进仓库，
存放于 ``petpark/assets/pets/<种类名>.jpg``（统一 512×512、纯白背景）。
运行时只按宠物的 ``species`` 字段查找对应文件，存在则返回路径，供消息链配图。
"""

from __future__ import annotations

import urllib.parse
from pathlib import Path

# 资源目录与本模块同包：petpark/assets/pets/
PETS_DIR = Path(__file__).parent / "assets" / "pets"

# ---------------------------------------------------------------------------
# QQ 官方机器人(qq_official)平台限制：一条消息要么是「原生 Markdown 消息」
# (msg_type=2，## / ** 会被渲染)，要么是「富媒体/图片消息」(msg_type=7，文本只
# 当纯文本)，二者不能共存——只要消息链里带 Image 组件，适配器就会丢掉 markdown。
# 因此想让「图片 + 渲染后的文本」同处一条消息，唯一办法是把图片以 Markdown 图片
# 语法 ![alt #宽 #高](url) 内嵌进文本里，让 QQ 服务端按 URL 拉取并渲染。
#
# QQ 服务端按 URL 抓图，必须是公网可达的 HTTPS 直链。插件图片已随仓库提交到
# GitHub，这里用 jsDelivr 的 GitHub CDN 直链（国内可达性优于 raw.githubusercontent）。
# 分支名 devin/petpark-plugin 含 "/" 会让 CDN 路径解析歧义，故固定到 commit SHA。
# 若日后新增/重绘图片并提交，更新此 SHA 即可（取 `git rev-parse HEAD`）。
_IMG_COMMIT = "9376e54f0d77057e307e47641d5da5b84169dc41"
_CDN_BASE = (
    f"https://cdn.jsdelivr.net/gh/skyesda/qqbot_pet@{_IMG_COMMIT}/petpark/assets/pets/"
)
# Markdown 内嵌图片的显示尺寸（原图 512×512 纯白底），统一为正方形。
_IMG_DISPLAY = "260px"


def pet_image_path(species: str | None) -> str | None:
    """按种类名返回图片绝对路径；不存在或无种类时返回 None。"""
    if not species:
        return None
    p = PETS_DIR / f"{species}.jpg"
    return str(p) if p.exists() else None


def pet_image_url(species: str | None) -> str | None:
    """按种类名返回该图片的公网 HTTPS 直链；本地无此图则返回 None。

    以「本地是否打包了该图」为准（缺图的种类不生成坏链），URL 指向同一份图片的
    CDN 直链，供 QQ 原生 Markdown 内嵌渲染。
    """
    if not species:
        return None
    if not (PETS_DIR / f"{species}.jpg").exists():
        return None
    return _CDN_BASE + urllib.parse.quote(species) + ".jpg"


def pet_image_md(species: str | None) -> str | None:
    """返回内嵌到 Markdown 文本里的图片语法串；无图返回 None。

    形如 ``![九尾狐 #260px #260px](https://.../九尾狐.jpg)``。把它拼到回复文本最前，
    整条消息仍是纯文本(无 Image 组件)，QQ 适配器走 msg_type=2 原生 Markdown，
    文本的 ## / ** 与这张图片就能在同一条消息里一起渲染出来。
    """
    url = pet_image_url(species)
    if not url:
        return None
    return f"![{species} #{_IMG_DISPLAY} #{_IMG_DISPLAY}]({url})"


def all_image_count() -> int:
    """已打包的宠物图片数量（用于自检/排查）。"""
    if not PETS_DIR.exists():
        return 0
    return sum(1 for _ in PETS_DIR.glob("*.jpg"))
