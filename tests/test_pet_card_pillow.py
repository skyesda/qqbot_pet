import base64
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from petpark.pet_card_pillow import CardParser, render_pet_card
from petpark.image_renderer import ImageRenderer


def snapshot(skills='御风', portrait=True, extra=''):
    out=io.BytesIO();Image.new('RGB',(32,64),'white').save(out,'PNG')
    art=(f'<img class="portrait" src="data:image/png;base64,{base64.b64encode(out.getvalue()).decode()}">'
         if portrait else '<div class="portrait-ph">暂无立绘</div>')
    return f'''<html><body><div class="card">
      <div class="mast-title">宠物灵鉴</div><div class="mast-caption">灵契仙途 · 伙伴档案</div>
      <div class="name">云栖 &amp; 青岚</div><div class="identity">九尾狐 / 火属性 / 幼年期</div><div class="rank">传说</div>
      <div class="pet-layout"><div class="portrait-wrap">{art}<div class="portrait-label">Lv.36 / 120</div></div>
      <div class="resource"><div class="res-head"><strong>经验</strong><span>1680/3800</span></div><div class="fill" style="width:44%"></div>{extra}</div>
      <div class="vitals"><div class="bar-row"><span class="bar-k">气血</span><div class="fill hp" style="width:73%"></div><span class="bar-n">2350/3200</span></div></div>
      <div class="power"><span>综合战力</span><strong>1.55万</strong></div>
      <div class="attributes"><div class="row"><k>种类</k><v>九尾狐</v></div></div>
      <div class="stats"><div class="stat"><span>攻击</span><strong>860</strong></div><div class="stat"><span>防御</span><strong>128</strong></div><div class="stat"><span>智力</span><strong>720</strong></div></div>
      <div class="abilities"><div class="row"><k>秘技</k><v>{skills}</v></div><div class="row"><k>坐骑加成</k><v>+10万</v></div><div class="row"><k>伴侣</k><v>好友 · 好感100</v></div></div>
      </div><div class="tags"><i>成长中</i></div><div class="warn">假死中无法操作</div>
      <div class="foot"><span>查看宠物：我的宠物</span><span>养成指引：灵契仙途</span></div></div></body></html>'''


class PetCardPillowTests(unittest.TestCase):
    def test_long_values_grow_card_and_escaped_content_is_preserved(self):
        short=render_pet_card(snapshot())
        long=render_pet_card(snapshot('九霄御风诀、'*60,extra='<div class="row"><k>余量</k><v>123456789012345678901234567890 经验</v></div>'))
        self.assertEqual(short.width,900)
        self.assertGreater(long.height,short.height)
        p=CardParser();p.feed(snapshot())
        self.assertEqual(p.root.find('name')[0].text(),'云栖 & 青岚')
        self.assertEqual(len(p.root.find('abilities')[0].find('row')),3)
        self.assertIsNotNone(render_pet_card(snapshot(portrait=False)))
        from petpark.raster_cards import render_card
        self.assertEqual(render_card(snapshot(),760).width,760)

    def test_unknown_or_remote_image_layout_falls_back(self):
        self.assertIsNone(render_pet_card('<div>其他卡片</div>'))
        self.assertIsNone(render_pet_card(snapshot(),720))
        self.assertIsNone(render_pet_card(snapshot().replace('class="abilities"','class="new-schema"')))
        self.assertIsNone(render_pet_card(snapshot().replace('data:image/png;base64,','https://example.com/')))

    def test_native_path_never_launches_browser_and_atomically_writes_jpeg(self):
        with tempfile.TemporaryDirectory() as directory:
            renderer=ImageRenderer()
            try:
                target=Path(directory)/'pet.jpg'
                with patch.object(renderer,'_capture',side_effect=AssertionError('browser launched')):
                    self.assertTrue(renderer.write(snapshot(),target,None,760,4200))
                with Image.open(target) as result:
                    self.assertEqual(result.format,'JPEG')
                self.assertFalse(list(Path(directory).glob('.*.tmp.png')))
            finally:
                renderer.close()

    def test_native_failure_keeps_browser_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            renderer=ImageRenderer();raw=io.BytesIO()
            Image.effect_noise((120,180),60).convert('RGB').save(raw,'PNG')
            try:
                with patch('petpark.faithful_cards.render_game_card',side_effect=RuntimeError('font missing')):
                    with patch.object(renderer,'_capture',return_value=raw.getvalue()) as capture:
                        self.assertTrue(renderer.write(snapshot(),Path(directory)/'fallback.jpg',None,900,5200))
                        capture.assert_called_once()
            finally:
                renderer.close()


if __name__ == '__main__':
    unittest.main()
