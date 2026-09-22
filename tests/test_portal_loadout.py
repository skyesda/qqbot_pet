"""Portal portraits and slots must match the in-game cultivator card."""
import copy
from pathlib import Path
import sys
import unittest
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / 'petbot_framework/compat'))
from petpark.adventure.portal_loadout import loadout_summary
from petpark.adventure import content as c


class PortalLoadoutTests(unittest.TestCase):
    def test_all_professions_and_genders_have_existing_art_and_six_slots(self):
        for profession in ['剑修', '体修', '灵修', '魔修']:
            for gender in ['男', '女']:
                with self.subTest(profession=profession, gender=gender):
                    result = loadout_summary({'profession': profession, 'gender': gender})
                    self.assertEqual([x['slot'] for x in result['equipment']],
                                     ['weapon', 'robe', 'seal', 'crown', 'boots', 'pendant'])
                    self.assertTrue(result['portrait_url'].endswith(('female' if gender == '女' else 'male') + '.webp'))
                    for url in [result['portrait_url']] + [x['image_url'] for x in result['equipment']]:
                        self.assertTrue((ROOT / 'petpark/assets/cultivator' / url.rsplit('/', 1)[1]).is_file())

    def test_actual_level_tier_and_affix_are_preserved_without_mutation(self):
        adventure = {'profession': '魔修', 'gender': '女', 'equipment': {'weapon': 27},
                     'equip_tier': {'weapon': 7}, 'equip_affix': {'weapon': '破军'}}
        snapshot = copy.deepcopy(adventure)
        slots = loadout_summary(adventure)['equipment']
        self.assertEqual(slots[0]['level'], 27)
        self.assertEqual(slots[0]['name'], c.gear_name('魔修', 'weapon', 7))
        self.assertEqual(slots[0]['affix'], '破军 ' + c.affix_bonus('破军'))
        self.assertEqual(slots[-1]['level'], 0)
        self.assertEqual(slots[-1]['affix'], '未觉醒词条')
        self.assertEqual(adventure, snapshot)

    def test_legacy_role_uses_card_defaults(self):
        result = loadout_summary({})
        self.assertEqual(result['portrait_url'], '/cultivator_assets/sword-male.webp')
        self.assertTrue(all(x['level'] == 0 for x in result['equipment']))

    def test_player_portal_summary_includes_real_loadout(self):
        from petpark.store import PetStore
        from petpark.portal import PlayerPortal
        from petpark.adventure.service import AdventureService
        with tempfile.TemporaryDirectory() as temp:
            store = PetStore(Path(temp) / 'store.json')
            AdventureService(store).handle('preview-group', 'preview-user', ['踏入仙途', '灵修'])
            player = store.get_player('preview-user', 'preview-group')
            player['adventure']['equipment']['weapon'] = 15
            portal = PlayerPortal(store)
            role = portal._slot_role_summary(player)['adventure']
            self.assertEqual(role['equipment'][0]['level'], 15)
            self.assertEqual(len(role['equipment']), 6)
            self.assertIn('/spirit-', role['portrait_url'])


if __name__ == '__main__':
    unittest.main()
