"""Reusable Chrome worker and request-local deferred image generation."""
from __future__ import annotations

import asyncio
import io
import logging
import os
import shutil
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from pathlib import Path

from PIL import Image

log = logging.getLogger(__name__)
pending_images = ContextVar('petpark_pending_images', default=None)


async def image_reply(builder, *args, **kwargs):
    """Run game logic on its event loop; offload only immutable HTML snapshots."""
    jobs = []
    token = pending_images.set(jobs)
    try:
        reply = builder(*args, **kwargs)
    finally:
        pending_images.reset(token)
    if not jobs:
        return reply
    for marker, render in jobs:
        result = await asyncio.to_thread(render)
        replacement = result or '图片暂时生成失败，请稍后重试。'
        if isinstance(reply, tuple):
            reply = tuple(part.replace(marker, replacement) if isinstance(part, str) else part
                          for part in reply)
        elif isinstance(reply, str):
            reply = reply.replace(marker, replacement)
    return reply


def chrome_path():
    explicit = os.environ.get('PETPARK_CHROME_PATH')
    if explicit:
        return explicit
    for name in ('google-chrome', 'chromium', 'chromium-browser'):
        found = shutil.which(name)
        if found:
            return found
    for root in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
        path = Path(os.environ.get(root, '')) / 'Google/Chrome/Application/chrome.exe'
        if path.is_file():
            return str(path)
    return None


class ImageRenderer:
    """Playwright objects live exclusively on one dedicated worker thread."""

    def __init__(self):
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='petpark-chrome')
        self._playwright = self._browser = self._page = None
        self._retry_at = 0.0

    def write(self, html, target, crop, width, height):
        return self._worker.submit(self._write, html, Path(target), crop, width, height).result()

    def warmup(self):
        self._worker.submit(self._warmup)

    def _warmup(self):
        try:
            self._screenshot('<html><body></body></html>', 32, 32)
        except Exception as exc:
            log.warning('[petpark] 图片浏览器预热失败: %s', exc)
            self._reset()
            self._retry_at = time.monotonic() + 60

    def _reset(self):
        for obj, method in ((self._browser, 'close'), (self._playwright, 'stop')):
            if obj is not None:
                try:
                    getattr(obj, method)()
                except Exception:
                    pass
        self._playwright = self._browser = self._page = None

    def close(self):
        self._worker.submit(self._reset).result()
        self._worker.shutdown(wait=True)

    def _screenshot(self, html, width, height, clip_panel=False):
        if self._browser is None or not self._browser.is_connected():
            self._reset()
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
            options = {'headless': True, 'args': ['--no-sandbox', '--disable-dev-shm-usage']}
            executable = chrome_path()
            if executable:
                options['executable_path'] = executable
            self._browser = self._playwright.chromium.launch(**options)
            self._page = self._browser.new_page(device_scale_factor=1)
            self._page.set_default_timeout(15000)
        page = self._page
        page.set_viewport_size({'width': width, 'height': height})
        # A fresh document avoids globals/styles leaking across reusable-page renders.
        page.goto('about:blank')
        page.set_content(html, wait_until='load', timeout=15000)
        page.evaluate('''() => Promise.race([
            Promise.all([document.fonts.ready,
                ...[...document.images].map(i => i.decode().catch(() => {}))]),
            new Promise((_, reject) => setTimeout(() => reject(new Error('asset timeout')), 15000))
        ])''')
        options = {}
        if clip_panel:
            panel = page.locator('body > .card, body > .scroll')
            if panel.count() == 1:
                box = panel.bounding_box()
                if box and box['width'] > 0 and box['height'] > 0:
                    options['clip'] = box
        return page.screenshot(type='png', animations='disabled', timeout=15000, **options)

    def _write(self, html, target, crop, width, height):
        # Serialized double-check coalesces concurrent requests for identical content.
        if target.is_file() and target.stat().st_size >= 1000:
            return True
        started = time.perf_counter()
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name('.' + target.name + '.' + uuid.uuid4().hex + '.tmp.png')
        html_file = temp.with_suffix('.html')
        backend = 'persistent'
        try:
            raw = None
            if time.monotonic() >= self._retry_at:
                try:
                    raw = self._screenshot(html, width, height,
                                           getattr(crop, '__name__', '') in ('_card_crop', '_crop_menu'))
                except Exception as exc:
                    log.warning('[petpark] 常驻浏览器不可用，临时回退 Chrome CLI: %s', exc)
                    self._reset()
                    self._retry_at = time.monotonic() + 60
            if raw is None:
                backend = 'cli'
                html_file.write_text(html, encoding='utf-8')
                subprocess.run(
                    [chrome_path() or 'google-chrome', '--headless=new', '--no-sandbox',
                     '--disable-dev-shm-usage', '--hide-scrollbars', '--disable-extensions',
                     '--force-device-scale-factor=1', f'--window-size={width},{height}',
                     f'--screenshot={temp}', html_file.resolve().as_uri()],
                    timeout=60, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                raw = temp.read_bytes()
            with Image.open(io.BytesIO(raw)) as image:
                rgb = image.convert('RGB')
            output = crop(rgb) if crop else rgb
            output.save(temp, 'PNG', compress_level=1)
            if temp.stat().st_size < 1000:
                return False
            os.replace(temp, target)
            log.info('[petpark] image_render backend=%s elapsed_ms=%.1f bytes=%d',
                     backend, (time.perf_counter() - started) * 1000, target.stat().st_size)
            return True
        except Exception:
            log.exception('[petpark] 图片渲染失败')
            return False
        finally:
            temp.unlink(missing_ok=True)
            html_file.unlink(missing_ok=True)
