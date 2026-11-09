from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

import bot
import buildings
import upgrades

ROOT = Path(__file__).resolve().parent
REGION = [1130, 540, 70, 30]


def screen(name):
    image = np.zeros((720, 1280, 3), np.uint8)
    image[500:580, 1120:1220] = cv2.imread(str(ROOT / 'fixtures' / name))
    return image


class BuilderAvailabilityTests(unittest.TestCase):
    def test_available_goblin_is_not_a_free_regular_builder_in_both_real_captures(self):
        for name in ('goblin-builder-hud.png', 'goblin-builder-hud-variant.png'):
            image = screen(name)
            free = buildings.regular_builders(image, 1, 7, REGION)
            self.assertEqual(free, 0, name)
            self.assertFalse(upgrades.room(None, 'buildings', upgrades.RULES_OFF, free))

    def test_goblin_opt_in_still_allows_the_paid_slot_through_existing_gem_checks(self):
        free = buildings.regular_builders(screen('goblin-builder-hud.png'), 1, 7, REGION)
        rules = {'keep_builders': 0, 'goblin': {'builder': True, 'researcher': False, 'max_gems': 50}}
        self.assertTrue(upgrades.room(None, 'buildings', rules, free))

    def test_ordinary_portrait_does_not_subtract_a_real_free_builder(self):
        image = screen('regular-builder-hud.png')
        self.assertEqual(buildings.regular_builders(image, 1, 7, REGION), 1)
        self.assertEqual(buildings.regular_builders(image, 0, 7, REGION), 0)

    def test_only_one_paid_slot_is_subtracted_and_keep_builders_still_applies(self):
        free = buildings.regular_builders(screen('goblin-builder-hud.png'), 2, 7, REGION)
        self.assertEqual(free, 1)
        self.assertFalse(upgrades.room(None, 'buildings', {**upgrades.RULES_OFF, 'keep_builders': 1}, free))
        self.assertTrue(upgrades.room(None, 'buildings', upgrades.RULES_OFF, free))

    def test_event_with_five_regular_builders_and_one_goblin_is_supported(self):
        self.assertEqual(buildings.regular_builders(screen('goblin-builder-hud.png'), 1, 6, REGION), 0)

    def test_village_reader_excludes_goblin_from_real_hud_digits(self):
        config = bot.load_config(ROOT / 'config.json')
        templates = bot.load_digit_templates(ROOT, config['digits'])
        for name in ('goblin-builder-hud.png', 'goblin-builder-hud-variant.png', 'regular-builder-hud.png'):
            image = screen(name)
            runner = SimpleNamespace(config=config, poll=0, templates=lambda: templates,
                                     wait=lambda *args: None, read_stable=lambda reader, limit: reader(image))
            with self.subTest(fixture=name), patch.object(bot, 'read_number', return_value=100000):
                free, amounts = buildings.village(runner)
            self.assertEqual(free, 0)
            self.assertEqual(amounts, {'gold':100000, 'elixir':100000, 'dark':100000})

    def test_confirmed_busy_capacity_uses_running_timer_instead_of_ten_minute_retries(self):
        with patch.object(upgrades.time, 'monotonic', return_value=100):
            runner = SimpleNamespace()
            upgrades.mark_busy(runner, ('home', 'builders'), 7200, confirmed=True)
            self.assertEqual(upgrades.busy_wait(runner, ('home', 'builders')), 7200)
            upgrades.mark_busy(runner, ('home', 'builders'), 86400, confirmed=True)
            self.assertEqual(upgrades.busy_wait(runner, ('home', 'builders')), upgrades.CLOSED_WAIT_MAX_SECONDS)
            upgrades.mark_busy(runner, ('home', 'builders'), 7200)
            self.assertEqual(upgrades.busy_wait(runner, ('home', 'builders')), buildings.WAIT_MAX_SECONDS)


if __name__ == '__main__':
    unittest.main()
