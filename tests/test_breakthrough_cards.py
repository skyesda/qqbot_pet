import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'petbot_framework' / 'compat'))
from qqbot_pet.main import PetParkPlugin
from qqbot_pet.petpark import pet as petmod
from qqbot_pet.petpark.store import PetStore


class BreakthroughTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bot = PetParkPlugin.__new__(PetParkPlugin)
        self.bot.store = PetStore(Path(tmp.name) / 'data.json')
        self.player = self.bot.store.get_player('a', 'g')
        self.pet = petmod.new_pet('九尾狐', '普通')
        self.bot._need_pet = lambda _: self.pet
        self.bot._busy_reason = lambda _: None
        self.bot._cooldown_block = lambda *args: None
        self.bot._render_html_image = lambda *args, **kwargs: '![result](https://example.test/card.png)'

    def test_evolve_keeps_portrait_and_returns_equipment(self):
        self.pet.update(level=60, artifact='测试神器', skills=['测试秘技'])
        self.bot.store.add_item(self.player, '进化神石', 1)
        portrait = self.bot._pet_portrait_uri(self.pet)
        self.assertTrue(self.bot._evolve(self.player).startswith('!['))
        self.assertEqual(self.pet['stage'], '成长期')
        self.assertEqual(self.pet['level'], 1)
        self.assertEqual(self.bot._pet_portrait_uri(self.pet), portrait)
        self.assertFalse(self.bot.store.has_item(self.player, '进化神石', 1))
        self.assertTrue(self.bot.store.has_item(self.player, '测试神器', 1))
        self.assertTrue(self.bot.store.has_item(self.player, '测试秘技', 1))

    def test_ascend_and_tribulation_success(self):
        self.pet.update(stage='超究极体', level=120)
        self.assertTrue(self.bot._ascend(self.player).startswith('!['))
        self.assertEqual(self.pet['stage'], '飞升')
        self.pet['level'] = 220
        with patch('qqbot_pet.petpark.pet.random.random', return_value=0.9):
            self.assertTrue(self.bot._tribulation(self.player).startswith('!['))
        self.assertEqual(self.pet['stage'], '渡劫')

    def test_failure_cooldown_and_renderer_fallback(self):
        self.pet.update(stage='飞升', level=220)
        with patch('qqbot_pet.petpark.pet.random.random', return_value=0.1), patch.object(self.bot.store, 'set_cooldown') as cooldown:
            self.assertTrue(self.bot._tribulation(self.player).startswith('!['))
            cooldown.assert_called_once_with(self.player, '渡劫', 1800)
        self.assertEqual(self.pet['stage'], '飞升')
        self.assertEqual(self.pet['hp'], self.pet['hp_max'] // 2)
        self.pet.update(stage='幼年期', level=60)
        self.bot.store.add_item(self.player, '进化神石', 1)
        with patch.object(self.bot, '_render_html_image', side_effect=RuntimeError('renderer offline')):
            self.assertIn('进化成功', self.bot._evolve(self.player))
        self.assertEqual(self.pet['stage'], '成长期')

    def test_invalid_attempt_and_portal_text(self):
        self.bot.store.add_item(self.player, '进化神石', 1)
        self.assertIn('等级', self.bot._evolve(self.player))
        self.assertTrue(self.bot.store.has_item(self.player, '进化神石', 1))
        self.pet['level'] = 60
        self.assertIn('进化成功', self.bot._evolve(self.player, render_image=False))
