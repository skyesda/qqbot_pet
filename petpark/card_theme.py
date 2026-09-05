"""Celestial game card assets and exact, border-free canvas trimming."""
import base64
from functools import lru_cache
from pathlib import Path

ASSETS = Path(__file__).parent / 'assets' / 'ui'


@lru_cache(maxsize=3)
def stylesheet(kind: str) -> str:
    if kind not in {'pet', 'bag', 'menu', 'mount'}:
        raise ValueError(kind)
    filename = {'pet': 'celestial-clouds.png', 'bag': 'treasure-atelier.png',
                'menu': 'mountain-gate.png', 'mount': 'celestial-clouds.png'}[kind]
    background = base64.b64encode((ASSETS / filename).read_bytes()).decode('ascii')
    return (ASSETS / 'common.css').read_text(encoding='utf-8').replace(
        '__BACKGROUND__', 'data:image/png;base64,' + background
    ) + (ASSETS / f'{kind}.css').read_text(encoding='utf-8')


def crop_canvas(image):
    # The page canvas is a sentinel color, never part of the rectangular artwork.
    # Unlike luminance thresholding, this preserves dark artwork and adds no margin.
    from PIL import Image, ImageChops
    rgb = image.convert('RGB')
    delta = ImageChops.difference(rgb, Image.new('RGB', rgb.size, (255, 0, 255)))
    box = delta.getbbox()
    return rgb.crop(box) if box else rgb


def finish_html(html: str) -> str:
    """Snap the outside border to whole pixels before Chrome takes its screenshot."""
    script = "<script>const panel=document.querySelector('.card,.scroll');if(panel){panel.style.height=Math.ceil(panel.getBoundingClientRect().height)+'px';}</script>"
    return html.replace('</body>', script + '</body>')


def item_icon(name: str) -> str:
    """Small vector item pictograms, independent of emoji fonts."""
    if any(word in name for word in ('丹', '药', '酿', '水')):
        shape = '<path d="M18 5h12v8l7 10v16q-13 8-26 0V23l7-10Z"/><path d="M18 10h12M12 29h24M20 33h8"/>'
    elif any(word in name for word in ('碎片', '石', '晶')):
        shape = '<path d="m24 4 15 14-6 22-18 3L8 21Z"/><path d="m24 4-5 19 14 17M8 21l11 2 20-5M19 23l-4 20"/>'
    elif any(word in name for word in ('卡', '符', '卷')):
        shape = '<rect x="12" y="5" width="26" height="35" rx="3"/><path d="M8 12v31h25M18 13h14M18 31h14m-7-13 5 6-5 5-5-5Z"/>'
    else:
        shape = '<path d="M7 17h34v23H7ZM5 10h38v8H5ZM21 10v30h7V10M24 10C8 12 10-3 24 10ZM24 10c16 2 14-13 0 0Z"/>'
    return '<svg viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true">' + shape + '</svg>'
