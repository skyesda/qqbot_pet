import io
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tools'))
from card_samples import samples
from petpark.faithful_cards import render_game_card as render_card
from petpark.raster_cards import card_kind, CardParser, Node, children, content
from petpark.image_renderer import ImageRenderer


class RasterCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cards=samples()

    def test_all_builtin_snapshots_render_without_browser_and_are_complete(self):
        # Compare visible authored leaf text to the actual paint calls. This
        # catches accidentally dropping resource values, commands or footers.
        with tempfile.TemporaryDirectory() as directory:
            renderer=ImageRenderer()
            try:
                with patch.object(renderer,'_capture',side_effect=AssertionError('browser used')):
                    for name,(html,width) in self.cards.items():
                        with self.subTest(name=name):
                            audit=[];image=render_card(html,width,audit)
                            self.assertIsNotNone(image)
                            target=Path(directory)/(name+'.jpg')
                            self.assertTrue(renderer.write(html,target,None,width,5200))
                            with Image.open(target) as result:self.assertEqual(result.format,'JPEG')
                            parser=CardParser();parser.feed(html)
                            def leaves(n):
                                if n.tag in {'style','script','svg','meta','link','img','path','rect','br'}:return []
                                kids=children(n)
                                if not kids or all(c.tag in {'span','b','strong','em','i','small','k','v','br'} for c in kids):
                                    return [content(n)] if content(n) else []
                                return [value for c in kids for value in leaves(c)]
                            painted=re.sub(r'[\s·]+','',''.join(audit))
                            for value in leaves(parser.root):
                                self.assertIn(re.sub(r'[\s·]+','',value),painted,(name,value))
            finally:renderer.close()

    def test_all_inventory_entries_and_tail_survive(self):
        html,width=self.cards['bag-large'];audit=[]
        image=render_card(html,width,audit)
        self.assertGreater(image.height,5200)
        self.assertIn('很长的测试物品名称99',audit)
        self.assertEqual(sum(t.startswith('很长的测试物品名称') for t in audit),100)
        self.assertTrue(any('使用物品' in t for t in audit))

    def test_map_has_twenty_landmarks_and_states(self):
        html,width=self.cards['map'];audit=[]
        render_card(html,width,audit)
        self.assertEqual(sum(t.zfill(2) in {f'{i:02}' for i in range(1,21)} for t in audit),20)
        self.assertTrue(any('困难已过' in t for t in audit))
        self.assertTrue(any('未解锁' in t for t in audit))

    def test_cloud_positions_are_bounded_and_empty_card_is_supported(self):
        html,width=self.cards['cloud'];audit=[]
        image=render_card(html,width,audit)
        self.assertEqual(image.size,(720,900))
        self.assertTrue(any('祖国' in t for t in audit))
        audit=[];render_card(self.cards['cloud-empty'][0],720,audit)
        self.assertTrue(any('静候第一声祝福' in t for t in audit))

    def test_unknown_layout_and_external_portrait_are_not_fetched(self):
        self.assertIsNone(render_card('<html><body>其他页面</body></html>',900))
        html,width=self.cards['mount']
        html=re.sub(r'src="data:image/[^\"]+"','src="https://example.test/private.jpg"',html)
        with self.assertRaises(ValueError):render_card(html,width)

    def test_warmup_only_loads_fonts_and_images(self):
        renderer=ImageRenderer()
        try:
            with patch.object(renderer,'_screenshot',side_effect=AssertionError('warmup started Chrome')):
                renderer._worker.submit(renderer._warmup).result()
            self.assertIsNone(renderer._browser)
        finally:renderer.close()


if __name__=='__main__':unittest.main()
