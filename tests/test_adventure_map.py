import unittest
from unittest.mock import patch

from qqbot_pet.petpark.adventure.content import MAPS
from qqbot_pet.petpark.adventure.map_card import map_html, stage_state


class AdventureMapTests(unittest.TestCase):
    def test_unlocks_match_predecessor_and_level(self):
        a = {'level': 1, 'cleared': []}
        self.assertEqual(stage_state(a, MAPS['1'])[0], 'ready')
        self.assertEqual(stage_state(a, MAPS['2'])[0], 'locked')
        a['cleared'] = ['1']
        self.assertEqual(stage_state(a, MAPS['1'])[0], 'cleared')
        self.assertEqual(stage_state(a, MAPS['2'])[0], 'level')
        a['level'] = 3
        self.assertEqual(stage_state(a, MAPS['2'])[0], 'ready')
        self.assertEqual(stage_state(a, MAPS['3'])[0], 'locked')

    @patch('qqbot_pet.petpark.adventure.map_card.asset_uri', return_value='data:image/png;base64,')
    def test_complete_atlas_and_dynamic_text(self, _):
        a = {'name': '<仙人>', 'level': 20, 'heaven': 2, 'cleared': ['1'], 'milestones': ['hard:1']}
        html = map_html({'adventure': a}, 150)
        self.assertEqual(html.count('data-stage='), 20)
        self.assertIn('敌人 ×1.83', html)
        self.assertIn('困难已过', html)
        self.assertIn('&lt;仙人&gt;', html)
        self.assertIn('发送「历练 2」', html)
        a['cleared'] = list(MAPS)
        self.assertIn('二十关已通关', map_html({'adventure': a}))

    @patch('qqbot_pet.petpark.adventure.map_card.asset_uri', return_value='data:image/png;base64,')
    def test_level_gated_recommendation(self, _):
        html = map_html({'adventure': {'level': 1, 'cleared': ['1']}})
        self.assertIn('修士升至 Lv3 后挑战', html)


if __name__ == '__main__':
    unittest.main()
