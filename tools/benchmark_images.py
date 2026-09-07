"""Measure actual bag-card generation and cached Markdown preparation, without QQ."""
import asyncio
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from PIL import Image
from qqbot_pet.tests.test_image_renderer import pipeline
from qqbot_pet.tools.preview_cards import templates
from qqbot_pet.petpark import card_theme
from qqbot_pet.petpark.image_renderer import chrome_path, image_reply


async def benchmark(out):
    cards = templates()
    html = cards._bag_card_html({'bag': {'九转还魂丹': 14, '宠物卡': 4, '混沌碎片': 6}})
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        p = pipeline(root)
        async def run(content):
            start = time.perf_counter()
            result = await image_reply(p._render_html_image, content, 'bench', 720,
                                       cards._card_crop, 760, 5200)
            assert result.startswith('!['), result
            return (time.perf_counter() - start) * 1000
        try:
            # Reproduce the old per-request process + full-canvas PNG encoding.
            source, target = root / 'baseline.html', root / 'baseline.png'
            source.write_text(card_theme.finish_html(html), encoding='utf-8')
            start = time.perf_counter()
            subprocess.run([chrome_path() or 'google-chrome', '--headless=new', '--no-sandbox',
                            '--disable-gpu', '--disable-dev-shm-usage', '--hide-scrollbars',
                            '--disable-extensions', '--force-device-scale-factor=1',
                            '--window-size=760,5200', f'--screenshot={target}', source.as_uri()],
                           check=True, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            with Image.open(target) as im:
                cropped = cards._card_crop(im.convert('RGB'))
            cropped.save(target, 'PNG')
            baseline_ms = (time.perf_counter() - start) * 1000
            cold = await run(html)
            warm = [await run(html.replace('14', str(100+i))) for i in range(5)]
            hits = [await run(html) for _ in range(30)]
            report = dict(baseline_cli_ms=baseline_ms, cold_ms=cold, warm_render_ms=warm,
                          cache_hit_p50_ms=statistics.median(hits), cache_hit_p95_ms=sorted(hits)[28],
                          html_bytes=len(html.encode()),
                          scope='Local HTML snapshot to PNG/Markdown; excludes HTML building, save, QQ/network delivery')
            out.mkdir(parents=True, exist_ok=True)
            (out / 'benchmark.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            latest = max(root.glob('bench_*.png'), key=lambda x: x.stat().st_mtime)
            (out / 'bag.png').write_bytes(latest.read_bytes())
            print(json.dumps(report, indent=2))
        finally:
            await asyncio.to_thread(p._image_renderer.close)


if __name__ == '__main__':
    asyncio.run(benchmark(Path(sys.argv[1]) if len(sys.argv) > 1 else Path('output/image-performance')))
