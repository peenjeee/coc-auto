from pathlib import Path
import shutil
import unittest

import cv2
import numpy as np

import bot
import buildings
import gear

ROOT = Path(__file__).resolve().parent


def panel(name):
    """A builder-list fixture (the list's 344 px on the right of the screen) on a blank 1280x720 frame."""
    image = np.zeros((720, 1280, 3), np.uint8)
    image[:, 936:] = cv2.imread(str(ROOT / 'fixtures' / f'{name}.png'))
    return image


class GearListTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = bot.load_config(ROOT / 'config.json')
        cls.spec = cls.config['walls']['home']
        cls.templates = buildings.load_templates(ROOT, cls.config)

    def cards(self, image):
        return [y for kind, y in buildings.items(image, self.spec['row']['region']) if kind == 'card']

    def test_each_cost_line_is_read_with_its_ore(self):
        image = panel('gear-list-top')
        found = [gear.costs(image, y, self.templates['digits']) for y in self.cards(image)[:4]]
        self.assertEqual(found, [{'shiny': 3000, 'glowy': 600, 'starry': 100}, {'shiny': 2500},
                                 {'shiny': 1800, 'glowy': 200, 'starry': 10}, {'shiny': 120}])

    def test_the_badge_beside_the_hero_portrait_gives_the_next_level(self):
        image = panel('gear-list-top')
        self.assertEqual([buildings.read_badge(buildings.band(image, gear.BADGE_BAND, y), self.templates['badge'])
                          for y in self.cards(image)], [21, 16, 9, 2, 15, 16, 15, 9, 2, 2])

    def test_names_match_the_catalog_and_titles_open_their_sections(self):
        self.assertEqual(buildings.named('Kitab ManHasakti', gear.CATALOG),
                         {'id': 'eternal_tome', 'name': 'Kitab Mahasakti', 'category': 'equipment', 'count': 1})
        self.assertEqual(buildings.named('Mystery Thing', gear.CATALOG)['id'], None)
        self.assertEqual([gear.title(text) for text in ('Dilepas', 'Unequipped', 'Dipasang', 'Botol Gesit')],
                         ['unequipped', 'unequipped', 'equipped', None])

    def test_the_dialog_is_told_apart_and_confirmed_only_at_the_listed_ores(self):
        detector = bot.Detector(self.config, ROOT)
        one, three, research = (cv2.imread(str(ROOT / 'fixtures' / f'{name}.png'))
                                for name in ('gear-confirm-one-ore', 'gear-confirm-three-ores', 'research-confirm'))
        self.assertEqual([detector.detect(image) for image in (one, three, research)],
                         ['gear_confirm', 'gear_confirm', 'building_confirm'])
        digits = self.templates['digits']
        self.assertEqual(gear.dialog_costs(three, digits), {'shiny': 3000, 'glowy': 600, 'starry': 100})
        self.assertIsNone(gear.refusal(one, {'costs': {'shiny': 2500}}, digits))
        self.assertIsNone(gear.refusal(three, {'costs': {'shiny': 3000, 'glowy': 600, 'starry': 100}}, digits))
        for costs in ({'shiny': 2400}, {'shiny': 3000, 'glowy': 600}, {'shiny': 840, 'glowy': None}, {}):
            with self.subTest(costs=costs):
                self.assertIsNotNone(gear.refusal(three if len(costs) > 1 else one, {'costs': costs}, digits))

    @unittest.skipUnless(shutil.which(bot.load_config(ROOT / 'config.json').get('tesseract') or ''), 'needs Tesseract')
    def test_the_list_reads_whole_with_tesseract(self):
        rows = gear.read_rows([panel('gear-list-next'), panel('gear-list-unequipped')], self.spec, self.templates,
                              bot.executable(self.config.get('tesseract'), 'Tesseract'))
        # The two locked rows with green names (Stun Blaster, Haste Vial) come under their own section.
        self.assertEqual([row['id'] for row in rows],
                         ['fireball', 'seeking_shield', 'royal_gem', 'jantung_api', 'ransel_roket', 'rage_vial',
                          'pemancar_kejut', 'haste_vial', 'dumbel_sakti', 'barbarian_puppet', 'snake_bracelet',
                          'archer_puppet', 'invisibility_vial', 'healer_puppet', 'henchmen_puppet'])
        self.assertEqual([row['section'] for row in rows], ['equipped'] * 6 + ['locked'] * 2 + ['unequipped'] * 7)
        self.assertEqual(rows[6], {'id': 'pemancar_kejut', 'name': 'Pemancar Kejut', 'category': 'equipment',
                                   'count': 1, 'next_level': None, 'costs': {}, 'section': 'locked'})
        self.assertEqual(rows[-1], {'id': 'henchmen_puppet', 'name': 'Boneka Pengawal', 'category': 'equipment',
                                    'count': 1, 'next_level': 3, 'costs': {'shiny': 240, 'glowy': 20},
                                    'section': 'unequipped'})


if __name__ == '__main__':
    unittest.main()
