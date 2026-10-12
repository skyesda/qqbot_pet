import ast
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from petpark.image_delivery import image_url, publish_image, unpublish_image


class ImageDeliveryTests(unittest.TestCase):
    def test_publish_reuse_replacement_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'card.jpg'
            config = {'image_publish_dir': str(root / 'public')}
            source.write_bytes(b'first artwork')
            self.assertTrue(publish_image(source, config))
            public = root / 'public' / source.name
            self.assertTrue(os.path.samefile(source, public))
            replacement = root / 'replacement.jpg'
            replacement.write_bytes(b'new artwork')
            os.replace(replacement, source)
            self.assertTrue(publish_image(source, config))
            self.assertEqual(public.read_bytes(), b'new artwork')
            unpublish_image(source.name, config)
            self.assertFalse(public.exists())
            self.assertTrue(source.exists())

    def test_copy_fallback_and_publish_failure_preserve_original(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'card.jpg'
            source.write_bytes(b'artwork')
            config = {'image_publish_dir': str(Path(directory) / 'public')}
            with patch('petpark.image_delivery.os.link', side_effect=OSError('different filesystem')):
                self.assertTrue(publish_image(source, config))
            self.assertEqual((Path(config['image_publish_dir']) / source.name).read_bytes(), b'artwork')
            with patch('petpark.image_delivery.os.replace', side_effect=PermissionError):
                self.assertFalse(publish_image(source, config))
            self.assertEqual(source.read_bytes(), b'artwork')
            self.assertEqual([p.name for p in Path(config['image_publish_dir']).iterdir()], ['card.jpg'])

    def test_https_url_encodes_entire_filename(self):
        self.assertEqual(image_url('九尾狐 1.jpg', {}),
                         'https://bot.flyyye.cn/custom_images/%E4%B9%9D%E5%B0%BE%E7%8B%90%201.jpg')
        self.assertEqual(image_url('card.jpg', {'image_public_base_url': 'https://images.example/'}),
                         'https://images.example/custom_images/card.jpg')
        with self.assertRaises(ValueError):
            image_url('card.jpg', {'image_public_base_url': 'file:///tmp'})

    def test_recent_images_survive_pruning_and_old_published_links_are_removed(self):
        tree = ast.parse((Path(__file__).parents[1] / 'main.py').read_text(encoding='utf-8'))
        owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'PetParkPlugin')
        method = next(n for n in owner.body if getattr(n, 'name', '') == '_prune_images')
        method.body = [n for n in method.body if not isinstance(n, ast.ImportFrom)]
        import time
        env = {'time': time, 'unpublish_image': unpublish_image}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), '<prune>', 'exec'), env)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {'image_publish_dir': str(root / 'public')}
            for i in range(258):
                p = root / f'card_{i}.jpg'
                p.write_bytes(b'artwork')
            recent = root / 'card_recent.jpg'
            recent.write_bytes(b'recent')
            os.utime(recent, (time.time() - 2 * 86400,) * 2)
            old = root / 'card_old.jpg'
            old.write_bytes(b'old')
            os.utime(old, (time.time() - 8 * 86400,) * 2)
            publish_image(old, config)
            env['_prune_images'](SimpleNamespace(store=SimpleNamespace(custom_images_dir=root), config=config), 'card')
            self.assertTrue(recent.exists())
            self.assertFalse(old.exists())
            self.assertFalse((root / 'public' / old.name).exists())


if __name__ == '__main__':
    unittest.main()
