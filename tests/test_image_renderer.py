import asyncio
import ast
import hashlib
import io
import logging
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from qqbot_pet.petpark import card_theme
from qqbot_pet.petpark.image_renderer import ImageRenderer, image_reply, pending_images


def pipeline(directory):
    """Load the actual plugin methods without booting QQ or game background jobs."""
    tree = ast.parse((Path(__file__).parents[1] / 'main.py').read_text(encoding='utf-8'))
    owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'PetParkPlugin')
    names = {'_render_html_image', '_write_html_png', '_html_png_ok', '_image_dims', '_prune_images'}
    cls = ast.ClassDef(name='Pipeline', bases=[], keywords=[],
                       body=[n for n in owner.body if getattr(n, 'name', '') in names], decorator_list=[])
    env = dict(Path=Path, Image=Image, ImageRenderer=ImageRenderer, pending_images=pending_images,
               uuid=uuid, time=time, hashlib=hashlib, card_theme=card_theme,
               logger=logging.getLogger(__name__))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), '<pipeline>', 'exec'), env)
    obj = env['Pipeline']()
    obj.store = SimpleNamespace(custom_images_dir=Path(directory))
    obj._tomb_image_url = lambda name: 'https://example.test/custom_images/' + name
    obj._image_renderer = ImageRenderer()
    return obj


def sample_png():
    buffer = io.BytesIO()
    Image.effect_noise((400, 300), 60).convert('RGB').save(buffer, 'PNG')
    return buffer.getvalue()


class ImagePipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.p = pipeline(self.temp.name)

    async def asyncTearDown(self):
        await asyncio.to_thread(self.p._image_renderer.close)
        self.temp.cleanup()

    async def test_dedup_cache_invalidation_and_loop_responsiveness(self):
        started = threading.Event()
        release = threading.Event()
        def screenshot(*args):
            started.set()
            release.wait(3)
            return sample_png()
        def build(html='first', width=400):
            return self.p._render_html_image(html, 'test', 400, win_w=width, win_h=300)
        with patch.object(self.p._image_renderer, '_screenshot', side_effect=screenshot) as shot:
            first = asyncio.create_task(image_reply(build))
            await asyncio.wait_for(asyncio.to_thread(started.wait), 2)
            # The event loop is still running while the renderer is blocked.
            second = asyncio.create_task(image_reply(build))
            await asyncio.sleep(0.02)
            self.assertFalse(first.done())
            release.set()
            a, b = await asyncio.gather(first, second)
            self.assertEqual(a, b)
            self.assertEqual(shot.call_count, 1)
            self.assertEqual(await image_reply(build), a)
            self.assertNotEqual(await image_reply(build, 'changed'), a)
            self.assertNotEqual(await image_reply(build, 'first', 500), a)
            self.assertEqual(shot.call_count, 3)
            self.assertNotIn('__PETPARK_IMAGE_', a)

    async def test_tuple_and_failure_never_leak_placeholders(self):
        with patch.object(self.p._image_renderer, 'write', return_value=False):
            result = await image_reply(lambda: ('游戏结果', self.p._render_html_image('x', 'test', 400)))
        self.assertEqual(result, ('游戏结果', '图片暂时生成失败，请稍后重试。'))
        self.assertIsNone(pending_images.get())

    async def test_builder_exception_restores_context(self):
        with self.assertRaises(ValueError):
            await image_reply(lambda: (_ for _ in ()).throw(ValueError('test')))
        self.assertIsNone(pending_images.get())

    async def test_failed_write_leaves_no_public_or_temporary_file(self):
        with patch.object(self.p._image_renderer, '_screenshot', return_value=b'invalid png'):
            result = await image_reply(self.p._render_html_image, 'x', 'test', 400)
        self.assertIn('失败', result)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
