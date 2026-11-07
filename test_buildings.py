from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

import bot
import buildings

ROOT = Path(__file__).resolve().parent


def panel(name):
    """A builder-list fixture (the list's 344 px on the right of the screen) on a blank 1280x720 frame."""
    image = np.zeros((720, 1280, 3), np.uint8)
    image[:, 936:] = cv2.imread(str(ROOT / 'fixtures' / f'{name}.png'))
    return image


class BuildingListTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = bot.load_config(ROOT / 'config.json')
        cls.spec = cls.config['walls']['home']
        cls.templates = buildings.load_templates(ROOT, cls.config)

    def card(self, image, y):
        """Badge, timer, price and coin of the card whose plain right edge starts at y."""
        return (buildings.read_badge(buildings.band(image, buildings.BADGE_BAND, y), self.templates['badge']),
                buildings.read_time(buildings.band(image, buildings.TIME_BAND, y), self.templates['digits'],
                                    self.templates['units']),
                bot.row_price(image, self.spec['row']['price'], y + buildings.NAME_CENTRE, self.templates['digits']),
                buildings.currency(buildings.band(image, buildings.COIN_BAND, y)))

    def test_names_match_the_catalog_in_either_language_and_an_unknown_one_stays_as_read(self):
        # As Tesseract reads the game's small capitals and outlined letters.
        for text, expected in (('ARCHER Tower x 4', ('archer_tower', 'defense', 'Archer Tower', 4)),
                               ('SPRING Trap x9', ('spring_trap', 'trap', 'Spring Trap', 9)),
                               ('Rir Defense x 4', ('air_defense', 'defense', 'Air Defense', 4)),
                               ('MownolitH', ('monolith', 'defense', 'Monolith', 1)),
                               ('Aula Panlawan', ('hero_hall', 'army', 'Aula Pahlawan', 1)),
                               ('Penyimpanan Emas x 4', ('gold_storage', 'resource', 'Penyimpanan Emas', 4)),
                               ('Tembok x 95', ('wall', 'defense', 'Tembok', 95)),
                               ('Barbarian King', ('barbarian_king', 'hero', 'Barbarian King', 1)),
                               ('Mystery Machine x 2', (None, 'other', 'Mystery Machine', 2))):
            with self.subTest(text=text):
                row = buildings.named(text)
                self.assertEqual((row['id'], row['category'], row['name'], row['count']), expected)

    def test_a_card_gives_its_level_badge_timer_price_and_coin(self):
        image = panel('building-list-archer-towers')
        cards = [y for kind, y in buildings.items(image, self.spec['row']['region']) if kind == 'card']
        self.assertEqual(len(cards), 10)
        # "Archer Tower x 4": badge 8 (level 7 -> 8), 2h, 100 000 gold; "Wizard Tower" below it 40m 0s.
        self.assertEqual(self.card(image, cards[3]), (8, '2h', 100_000, 'gold'))
        self.assertEqual(self.card(image, cards[0]), (2, '1h 30m', 175_000, 'gold'))
        self.assertEqual(self.card(image, cards[5])[1], '40m 0s')
        # The Indonesian list: "2h 12j" is 2 days 12 hours, kept as the game writes it; a wall has no timer.
        image = panel('build-list-wali-besar')
        cards = [y for kind, y in buildings.items(image, self.spec['row']['region']) if kind == 'card']
        self.assertEqual(self.card(image, cards[0]), (5, '2h 12j', 1_250_000, 'elixir'))
        self.assertEqual(self.card(image, cards[1]), (14, None, 1_000_000, 'elixir'))
        self.assertEqual(self.card(image, cards[4])[1:], ('8d', 150, 'elixir'))

    def test_running_upgrades_titles_and_cards_are_told_apart(self):
        image = panel('building-list-top')
        found = buildings.items(image, self.spec['row']['region'])
        self.assertEqual([kind for kind, _ in found], ['bar'] * 6 + ['title', 'card', 'card', 'card', 'title', 'card',
                                                                        'card'])
        bars = [y for kind, y in found if kind == 'bar']
        self.assertEqual([buildings.read_time(buildings.band(image, buildings.BAR_TIME, y), self.templates['digits'],
                                              self.templates['units']) for y in bars],
                         ['1h 23m', '3h 24m', '17h 57m', '20h 4m', '20h 4m', '4d 7h'])
        # The black drop of dark elixir: Barbarian King, 5 250.
        self.assertEqual(self.card(image, found[7][1]), (11, '11h', 5250, 'dark'))

    def test_two_frames_are_lined_up_and_every_row_is_kept_once(self):
        top, below = panel('building-list-top'), panel('building-list-next')
        self.assertEqual(buildings.list_shift(top, below, self.spec['row']['region']), 326)
        # Without the right words a title opens the sections in order: suggestions, then the others.
        with patch.object(buildings, 'ocr_line', lambda image, program, *colours: '' if colours else 'Cannon x 2'):
            rows = buildings.read_rows([top, below], self.spec, self.templates, 'tesseract')
        self.assertEqual([row['section'] for row in rows], ['in_progress'] * 6 + ['suggested'] * 3 + ['other'] * 8)
        self.assertEqual([row['next_level'] for row in rows[6:]], [11, 10, 2, 2, 2, 2, 2, 2, 11, 2, 2])
        self.assertEqual([row['cost'] for row in rows[-4:]], [25_000, 5250, 5000, 2500])
        self.assertEqual(set(rows[0]), {'id', 'name', 'category', 'count', 'next_level', 'time', 'cost', 'currency',
                                        'section'})
        with self.assertRaises(bot.BotError):
            buildings.list_shift(top, panel('building-list-archer-towers'), self.spec['row']['region'])

    def test_a_title_names_its_section_in_either_language(self):
        self.assertEqual(buildings.title_section('Other upgrades', 0), 'other')
        self.assertEqual(buildings.title_section('Suggested upgrades', 1), 'suggested')
        self.assertEqual(buildings.title_section('Peningkatan Lainnya', 0), 'other')
        self.assertEqual(buildings.title_section('', 1), 'other')

    @unittest.skipUnless(shutil.which(bot.load_config(ROOT / 'config.json').get('tesseract') or ''), 'needs Tesseract')
    def test_the_list_reads_whole_with_tesseract(self):
        rows = buildings.read_rows([panel('building-list-top'), panel('building-list-next')], self.spec,
                                   self.templates, self.config['tesseract'])
        self.assertEqual([row['id'] for row in rows],
                         ['archer_tower', 'cannon', 'eagle_artillery', 'archer_tower', 'archer_tower', 'mortar',
                          'barbarian_king', 'hero_hall', 'elixir_collector', 'monolith', 'crafting_station',
                          'crafting_station', 'crafting_station', 'dragon_duke', 'archer_queen', 'royal_champion',
                          'minion_prince'])
        self.assertEqual(rows[8], {'id': 'elixir_collector', 'name': 'Elixir Collector', 'category': 'resource',
                                   'count': 7, 'next_level': 2, 'time': '8s', 'cost': 150, 'currency': 'gold',
                                   'section': 'suggested'})


class ScanTests(unittest.TestCase):
    """The scan only opens the list, swipes it and closes it; the runner here has nothing else to call."""

    def runner(self, states, frames):
        calls = []
        moves = iter(frames)

        def scroll_list(spec, swipe, image, once=False):
            calls.append(('swipe', tuple(map(tuple, swipe)), once))
            return next(moves, None) if once else 'top'
        config = {'walls': {'home': {'scroll': [[1110, 560], [1110, 300]]}}, 'tesseract': 'tesseract'}
        states = iter(states)
        return calls, SimpleNamespace(
            config=config, root=ROOT, check_touch=lambda: calls.append('check_touch'),
            wait=lambda targets, allowed, seconds: next(states),
            close_menus=lambda: calls.append('close'),
            open_build_list=lambda village, spec: calls.append(('open', village)) or 'opened',
            scroll_list=scroll_list)

    def test_the_list_is_read_from_its_top_to_its_end_then_closed(self):
        calls, runner = self.runner(['home'], ['second', 'third'])
        with patch.object(bot, 'executable', return_value='tesseract'), \
                patch.object(buildings, 'load_templates', return_value={}), \
                patch.object(buildings, 'read_rows', return_value=['rows']) as read:
            self.assertEqual(buildings.scan(runner), ['rows'])
        self.assertEqual(read.call_args.args[0], ['top', 'second', 'third'])
        down = ('swipe', ((1110, 560), (1110, 300)), True)
        self.assertEqual(calls, ['check_touch', ('open', 'home'), ('swipe', ((1110, 300), (1110, 560)), False),
                                 down, down, down, 'close'])

    def test_a_list_left_open_is_closed_first_and_only_the_home_village_is_scanned(self):
        with patch.object(bot, 'executable', return_value='tesseract'), \
                patch.object(buildings, 'load_templates', return_value={}):
            calls, runner = self.runner(['build_list', 'builder'], [])
            with self.assertRaises(bot.BotError):
                buildings.scan(runner)
            self.assertEqual(calls, ['check_touch', 'close'])
            # A swipe that fails still leaves the list closed.
            calls, runner = self.runner(['home'], [])
            with patch.object(runner, 'scroll_list', side_effect=bot.BotError('x')), self.assertRaises(bot.BotError):
                buildings.scan(runner)
            self.assertEqual(calls[-1], 'close')


if __name__ == '__main__':
    unittest.main()
