from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

import bot
import buildings
import upgrades

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

    def test_lab_and_pet_names_match_their_own_catalog(self):
        # Against the buildings catalog "Raksasa" (Giant) came out "Bom Raksasa", "Orang Barbar" "Raja Barbar".
        for text, expected in (('Raksasa', ('giant', 'troop', 'Raksasa')),
                               ('Orang Barbar', ('barbarian', 'troop', 'Orang Barbar')),
                               ('PenvembuH', ('healer', 'troop', 'Penyembuh')),
                               ('Mantra JeRrangkong', ('skeleton_spell', 'dark_spell', 'Mantra Jerangkong')),
                               ('P.E.K.K.A', ('pekka', 'troop', 'P.E.K.K.A')),
                               ('L.A.S.5.1', ('lassi', 'pet', 'L.A.S.S.I'))):
            with self.subTest(text=text):
                self.assertEqual(buildings.identify(text, buildings.RESEARCH), expected)

    def test_a_short_name_the_line_reading_misses_is_read_as_one_word(self):
        answers = iter([b'\n', b'Balon\n'])
        with patch.object(buildings.subprocess, 'run',
                          side_effect=lambda args, **options: SimpleNamespace(stdout=next(answers))) as run:
            self.assertEqual(buildings.ocr_line(np.zeros((20, 198, 3), np.uint8), 'tesseract'), 'Balon')
        self.assertEqual([call.args[0][4] for call in run.call_args_list], ['7', '8'])

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

    def test_a_scan_keeps_the_list_part_of_its_frames_in_place_of_the_last_ones(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            kept = Path(folder) / 'lab'
            buildings.keep_frames(kept, [np.zeros((720, 1280, 3), np.uint8)] * 3)
            buildings.keep_frames(kept, [np.full((720, 1280, 3), 9, np.uint8)] * 2)
            self.assertEqual(sorted(path.name for path in kept.glob('*.png')), ['00.png', '01.png'])
            self.assertEqual(cv2.imread(str(kept / '00.png')).shape, (720, 344, 3))

    def test_a_faint_digit_on_a_running_bar_still_reads(self):
        # The Battle Machine's "4j 42m": its second 4 scored 0.74 against the digits' 0.75 floor.
        image = panel('builder-list-battle-machine')
        self.assertEqual(buildings.read_time(buildings.band(image, buildings.BAR_TIME, 71), self.templates['digits'],
                                             self.templates['units']), '4j 42m')

    def test_the_researchers_face_before_a_timer_is_left_out(self):
        # Valkyrie's "21j 44m" with the researcher's face on its left, read as a 'j' before the first digit.
        image = panel('lab-list-valkyrie-face')
        self.assertEqual(buildings.read_time(buildings.band(image, buildings.BAR_TIME, 71), self.templates['digits'],
                                             self.templates['units']), '21j 44m')

    def test_light_beside_or_inside_a_badge_is_not_read_as_a_digit(self):
        # A 3 joined by the icon's light (once cut in two), a 7 whose square met the icon's dark outline, and a face
        # before a timer that reads as nothing at all.
        import gear
        badge = self.templates['badge']
        self.assertEqual(buildings.read_badge(buildings.band(panel('gear-list-wide-three'), gear.BADGE_BAND, 261), badge), 3)
        self.assertEqual(buildings.read_badge(buildings.band(panel('lab-list-wall-breaker'), buildings.BADGE_BAND, 575), badge), 7)
        self.assertEqual(buildings.read_time(buildings.band(panel('lab-list-valkyrie-face-unread'), buildings.BAR_TIME, 71),
                                             self.templates['digits'], self.templates['units']), '20j 25m')

    def test_a_bold_nine_is_told_from_a_five_by_its_counter(self):
        # "90 000" on the Star Laboratory list: its 9 scored too close to a 5 and the price was not read.
        price = self.config['walls']['builder']['row']['price']
        self.assertEqual(bot.row_price(panel('star-list-top'), price, 389 + buildings.NAME_CENTRE,
                                       self.templates['digits']), 90_000)
        self.assertEqual([bot.holes(self.templates['digits'][name]) for name in ('9', '5', '8', '0')], [1, 0, 2, 1])

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

    def test_rows_that_look_alike_are_lined_up(self):
        # Real frames on which a second spot scored 0.039 against the right one's 0.034: the scan used to give up.
        first, second, third = (panel(f'building-list-alike-{name}') for name in 'abc')
        self.assertEqual(buildings.offsets([first, second, third], self.spec['row']['region']), [0, 313, 627])

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

    @unittest.skipUnless(shutil.which(bot.load_config(ROOT / 'config.json').get('tesseract') or ''), 'needs Tesseract')
    def test_the_lab_and_pet_tabs_read_with_their_own_catalog(self):
        rows = buildings.read_rows([panel('lab-list-giant-barbarian')], self.spec, self.templates,
                                   self.config['tesseract'], buildings.RESEARCH)
        self.assertEqual([row['id'] for row in rows], ['baby_dragon', 'miner', 'jump_spell', 'healing_spell', 'giant',
                                                       'barbarian', 'pekka', 'wizard', 'archer'])
        self.assertEqual([(row['category'], row['cost'], row['currency']) for row in rows[4:6]],
                         [('troop', 400_000, 'elixir'), ('troop', 400_000, 'elixir')])
        # The pets: L.A.S.S.I upgrading, every other pet still locked behind the Pet House and left out.
        pets = buildings.read_rows([panel('pet-list-lassi')], self.spec, self.templates, self.config['tesseract'],
                                   buildings.RESEARCH)
        self.assertEqual([(row['id'], row['category'], row['section'], row['time']) for row in pets],
                         [('lassi', 'pet', 'in_progress', '13j 57m')])


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
            scroll_list=scroll_list, list_indonesian=lambda image: image == 'opened', live=True, poll=0,
            device=SimpleNamespace(tap_many=lambda taps: calls.append(('tap', taps)), capture=lambda: 'tab'),
            detector=SimpleNamespace(shows=lambda image, name: False), wait_still=lambda seconds: 'tab')

    def test_the_list_is_read_from_its_top_to_its_end_then_closed(self):
        calls, runner = self.runner(['home'], ['second', 'third'])
        with patch.object(bot, 'executable', return_value='tesseract'), \
                patch.object(buildings, 'load_templates', return_value={}), \
                patch.object(buildings, 'read_rows', return_value=[{'section': 'in_progress', 'time': '1h 23j'},
                                                                   {'section': 'other', 'time': '2h'}]) as read:
            # A running upgrade comes with its time left in seconds, for the dashboard to count down; the units
            # are the language the list's header showed (Indonesian here: 'j' is hours).
            self.assertEqual(buildings.scan(runner), [{'section': 'in_progress', 'time': '1h 23j', 'seconds': 169_200},
                                                      {'section': 'other', 'time': '2h'}])
        self.assertEqual(read.call_args.args[0], ['top', 'second', 'third'])
        down = ('swipe', ((1110, 560), (1110, 300)), True)
        self.assertEqual(calls, ['check_touch', ('open', 'home'), ('swipe', ((1110, 300), (1110, 560)), False),
                                 down, down, down, 'close'])

    def test_a_tab_is_touched_only_with_the_list_in_front(self):
        # A tab sits where a row's upgrade dialog has its confirm button: the dialog is closed with its X first.
        calls, shown = [], {'building_confirm'}
        runner = SimpleNamespace(
            live=True, poll=0, wait_still=lambda seconds: 'frame',
            device=SimpleNamespace(capture=lambda: 'frame', tap_many=lambda taps: calls.append(('tap', taps))),
            detector=SimpleNamespace(shows=lambda image, name: name in shown),
            action=lambda state, name: calls.append((state, name)) or shown.clear())
        with patch.object(buildings, 'list_in_front', return_value=True):
            buildings.show_tab(runner, 1062)
        self.assertEqual(calls, [('building_confirm', 'close'), ('tap', [[1062, 690]])])
        calls.clear()
        with patch.object(buildings, 'list_in_front', return_value=False), self.assertRaises(bot.UnexpectedScreen):
            buildings.show_tab(runner, 978)
        self.assertEqual(calls, [])
        # The list's own close button tells it is in front on any tab; a dialog over it dims that button.
        config = bot.load_config(ROOT / 'config.json')
        real = SimpleNamespace(config=config, detector=bot.Detector(config, ROOT))
        self.assertTrue(buildings.list_in_front(real, panel('lab-list-giant-barbarian')))
        self.assertFalse(buildings.list_in_front(real, cv2.imread(str(ROOT / 'fixtures' / 'research-confirm.png'))))

    def test_the_lab_tab_is_read_and_the_builders_tab_shown_again_before_closing(self):
        calls, runner = self.runner(['home'], ['second'])
        with patch.object(buildings, 'list_in_front', return_value=True), \
                patch.object(bot, 'executable', return_value='tesseract'), \
                patch.object(buildings, 'load_templates', return_value={}), \
                patch.object(buildings, 'list_frames', wraps=buildings.list_frames) as frames, \
                patch.object(buildings, 'read_rows', return_value=[{'section': 'in_progress', 'time': '2j'}]) as read:
            self.assertEqual(buildings.scan(runner, 'lab'), [{'section': 'in_progress', 'time': '2j', 'seconds': 7200}])
        # The lab's own frame starts the list, its rows named from the research catalog.
        self.assertEqual(frames.call_args.args[2], 'tab')
        self.assertEqual((read.call_args.args[0], read.call_args.args[4]), (['top', 'second'], buildings.RESEARCH))
        down = ('swipe', ((1110, 560), (1110, 300)), True)
        self.assertEqual(calls, ['check_touch', ('open', 'home'), ('tap', [[1062, 690]]),
                                 ('swipe', ((1110, 300), (1110, 560)), False), down, down, ('tap', [[978, 690]]),
                                 'close'])
        # A swipe that fails on the pets tab still shows the builders tab before closing.
        calls, runner = self.runner(['home'], [])
        with patch.object(buildings, 'list_in_front', return_value=True), \
                patch.object(bot, 'executable', return_value='tesseract'), \
                patch.object(buildings, 'load_templates', return_value={}), \
                patch.object(runner, 'scroll_list', side_effect=bot.BotError('x')), self.assertRaises(bot.BotError):
            buildings.scan(runner, 'pets')
        self.assertEqual(calls[-3:], [('tap', [[1240, 690]]), ('tap', [[978, 690]]), 'close'])

    def test_a_list_left_open_is_closed_first_and_the_lists_village_is_travelled_to(self):
        with patch.object(bot, 'executable', return_value='tesseract'), \
                patch.object(buildings, 'load_templates', return_value={}), \
                patch.object(buildings, 'read_rows', return_value=[]) as read:
            calls, runner = self.runner(['build_list', 'builder'], [])
            runner.navigate = lambda village: calls.append(('navigate', village))
            buildings.scan(runner)
            self.assertEqual(calls[:4], ['check_touch', 'close', ('navigate', 'home'), ('open', 'home')])
            # The Builder Base list is read in the Builder Base, named from its own catalog.
            calls, runner = self.runner(['home'], [])
            runner.config['walls']['builder'] = runner.config['walls']['home']
            runner.navigate = lambda village: calls.append(('navigate', village))
            buildings.scan(runner, 'builder')
            self.assertEqual(calls[1:3], [('navigate', 'builder'), ('open', 'builder')])
            self.assertEqual(read.call_args.args[4], buildings.BUILDER)
            # A swipe that fails still leaves the list closed.
            calls, runner = self.runner(['home'], [])
            with patch.object(runner, 'scroll_list', side_effect=bot.BotError('x')), self.assertRaises(bot.BotError):
                buildings.scan(runner)
            self.assertEqual(calls[-1], 'close')


def row(name, key, next_level=2, cost=150, currency='gold', section='other', time='8s', count=1):
    return {'id': key, 'name': name, 'category': 'resource', 'count': count, 'next_level': next_level, 'time': time,
            'cost': cost, 'currency': currency, 'section': section}


AMOUNTS = {'gold': 1000, 'elixir': 1000, 'dark': 100}


class PickTests(unittest.TestCase):
    def test_the_first_target_in_priority_order_that_is_affordable_wins(self):
        mine, bomb = row('Gold Mine', 'gold_mine'), row('Bomb', 'bomb', cost=500)
        placed = [(mine, 10), (bomb, 60)]
        targets = [{'id': 'bomb', 'name': 'Bomb', 'level': None}, {'id': 'gold_mine', 'name': 'Gold Mine', 'level': 5}]
        self.assertEqual(buildings.pick(placed, targets, AMOUNTS), ((bomb, 60), True))
        # Short of gold for the bomb: the next target goes first, the bomb stays for later.
        self.assertEqual(buildings.pick(placed, targets, {**AMOUNTS, 'gold': 200}), ((mine, 10), True))
        self.assertEqual(buildings.pick(placed, targets, {**AMOUNTS, 'gold': 100}), (None, True))
        # Its own resource counts: a dark elixir cost is not paid from gold.
        king = row('Barbarian King', 'barbarian_king', cost=5250, currency='dark')
        target = [{'id': 'barbarian_king', 'name': 'Barbarian King', 'level': None}]
        self.assertEqual(buildings.pick([(king, 0)], target, {**AMOUNTS, 'gold': 10 ** 9}), (None, True))
        self.assertEqual(buildings.pick([(king, 0)], target, {**AMOUNTS, 'dark': 5250}), ((king, 0), True))

    def test_a_level_target_is_reached_once_the_next_level_passes_it(self):
        target = [{'id': 'gold_mine', 'name': 'Gold Mine', 'level': 2}]
        self.assertEqual(buildings.pick([(row('Gold Mine', 'gold_mine', 2), 0)], target, AMOUNTS)[0][1], 0)
        self.assertEqual(buildings.pick([(row('Gold Mine', 'gold_mine', 3), 0)], target, AMOUNTS), (None, False))
        # Max keeps going while the building is listed; a level that was not read is never guessed past.
        self.assertIsNotNone(buildings.pick([(row('Gold Mine', 'gold_mine', 30), 0)],
                                            [{**target[0], 'level': None}], AMOUNTS)[0])
        self.assertEqual(buildings.pick([(row('Gold Mine', 'gold_mine', None), 0)], target, AMOUNTS), (None, True))
        # Not listed at all: maxed or gone, so nothing is left.
        self.assertEqual(buildings.pick([(row('Bomb', 'bomb'), 0)], target, AMOUNTS), (None, False))

    def test_running_unread_and_refused_rows_are_left_for_later(self):
        target = [{'id': 'gold_mine', 'name': 'Gold Mine', 'level': None}]
        running = row('Gold Mine', 'gold_mine', None, None, None, 'in_progress', '20m')
        self.assertEqual(buildings.pick([(running, 0)], target, AMOUNTS), (None, True))
        for unread in (row('Gold Mine', 'gold_mine', cost=None), row('Gold Mine', 'gold_mine', currency=None)):
            self.assertEqual(buildings.pick([(unread, 0)], target, AMOUNTS), (None, True))
        mine = row('Gold Mine', 'gold_mine')
        self.assertEqual(buildings.pick([(mine, 0)], target, AMOUNTS, {buildings.row_key(mine)}), (None, True))

    def test_a_row_the_catalog_does_not_know_is_matched_by_its_name(self):
        unknown = row('Mystery Machine', None)
        target = [{'id': None, 'name': 'mystery machine', 'level': None}]
        self.assertEqual(buildings.pick([(unknown, 7)], target, AMOUNTS), ((unknown, 7), True))
        # A catalog building of that name is another building.
        self.assertEqual(buildings.pick([({**unknown, 'id': 'gold_mine'}, 7)], target, AMOUNTS), (None, False))


class TimerTests(unittest.TestCase):
    def test_timers_read_in_either_language(self):
        for text, indonesian, expected in (('1h 23m', False, 4980), ('4d 7h', False, 370_800),
                                           ('30m 0s', False, 1800), ('8s', False, 8), ('2h 12j', True, 216_000),
                                           ('3j', True, 10_800), ('8d', True, 8), ('45m', True, 2700)):
            with self.subTest(text=text):
                self.assertEqual(buildings.seconds(text, indonesian), expected)
        for text, indonesian in (('2j', False), ('8s', True), ('1h23m', False), ('', False), (None, False),
                                 ('1h 23m x', False)):
            with self.subTest(text=text):
                self.assertIsNone(buildings.seconds(text, indonesian))

    def test_the_wait_ends_with_the_soonest_running_upgrade_and_ten_minutes_at_most(self):
        rows = [row('A', None, section='in_progress', time='1h 23m'), row('B', None, section='in_progress', time='8s'),
                row('C', None, time='1s')]
        self.assertEqual(buildings.pause_seconds(rows, False), 8 + buildings.WAIT_MARGIN_SECONDS)
        self.assertEqual(buildings.pause_seconds(rows[:1], False), buildings.WAIT_MAX_SECONDS)
        self.assertEqual(buildings.pause_seconds([row('A', None, section='in_progress', time=None)], False),
                         buildings.WAIT_MAX_SECONDS)


class UpgradeDialogTests(unittest.TestCase):
    """The building upgrade dialog, from real frames: confirmed only for the row's own price and resource."""

    @classmethod
    def setUpClass(cls):
        cls.config = bot.load_config(ROOT / 'config.json')
        cls.runner = bot.Bot(cls.config, ROOT, None)
        cls.gold = cv2.imread(str(ROOT / 'fixtures' / 'building-confirm-gold.png'))
        cls.dark = np.zeros((720, 1280, 3), np.uint8)
        cls.dark[570:690, 790:1170] = cv2.imread(str(ROOT / 'fixtures' / 'building-confirm-dark-button.png'))

    def test_the_dialog_is_its_own_screen_and_the_wall_dialog_stays_the_wall_one(self):
        self.assertEqual(self.runner.detector.detect(self.gold), 'building_confirm')
        wall = cv2.imread(str(ROOT / 'fixtures' / 'wall-confirm-sale-gold.png'))
        self.assertEqual(self.runner.detector.detect(wall), 'wall_confirm')

    def test_only_the_rows_price_and_resource_are_confirmed(self):
        collector = row('Elixir Collector', 'elixir_collector', cost=150, currency='gold', count=7)
        self.assertIsNone(buildings.refusal(self.runner, self.gold, collector))
        king = row('Barbarian King', 'barbarian_king', 11, 5250, 'dark')
        self.assertIsNone(buildings.refusal(self.runner, self.dark, king))
        for wrong in ({'cost': 300}, {'cost': None}, {'currency': 'elixir'}, {'currency': 'gems'},
                      {'currency': None}):
            with self.subTest(wrong=wrong):
                self.assertIsNotNone(buildings.refusal(self.runner, self.gold, {**collector, **wrong}))
        self.assertIsNotNone(buildings.refusal(self.runner, self.dark, {**king, 'currency': 'gold'}))
        self.assertIsNotNone(buildings.refusal(self.runner, np.zeros_like(self.gold), collector))


class CommandLineTests(unittest.TestCase):
    def test_buildings_needs_live_and_runs_on_its_own(self):
        for argv in (['--buildings'], ['--buildings', '--live', '--walls', 'home'],
                     ['--buildings', '--live', '--until-full'], ['--buildings', '--live', '--home', 'gold'],
                     ['--buildings', '--live', '--builder', 'gold'], ['--buildings', '--live', '--max-upgrades', '3']):
            with self.subTest(argv=argv), patch('sys.argv', ['bot.py', *argv, '--no-remote']):
                self.assertEqual(bot.main(), 1)

    def test_buildings_runs_its_pick_under_the_recovery(self):
        chosen = {'buildings': {'targets': [{'id': 'bomb', 'name': 'Bomb', 'level': None}], 'keep_builders': 0}}
        with patch('sys.argv', ['bot.py', '--buildings', '--live', '--hours', '2', '--no-remote']), \
                patch('bot.load_config'), patch('bot.validate_config'), patch('bot.executable'), patch('bot.ADB'), \
                patch('bot.Bot') as runner, patch('bot.fit_screen', return_value=False), \
                patch.object(buildings, 'validate', return_value=chosen):
            self.assertEqual(bot.main(), 0)
        self.assertEqual(runner.call_args.kwargs['minutes'], 120)
        job = runner.return_value.run_until_full_resilient.call_args.kwargs['job']
        with patch.object(upgrades, 'run', return_value=2) as run:
            self.assertEqual(job(0), 2)
        run.assert_called_once_with(runner.return_value, chosen)

    def test_the_pick_is_checked_before_a_run(self):
        config = bot.load_config(ROOT / 'config.json')
        config = {key: value for key, value in config.items() if key not in buildings.LISTS}
        target = {'id': 'gold_mine', 'name': 'Gold Mine', 'level': 2}
        chosen = {'targets': [target], 'keep_builders': 1}
        picked = buildings.validate({**config, 'buildings': chosen}, ROOT)
        self.assertEqual(picked, {'buildings': chosen, 'lab': {'targets': []}, 'gear': {'targets': []},
                                  'pets': {'targets': []}, 'builder': {'targets': []}, 'star': {'targets': []}})
        # Any list it upgrades may carry the pick on its own.
        research = {'targets': [{'id': 'valkyrie', 'name': 'Valkyrie', 'level': None}]}
        self.assertEqual(buildings.validate({**config, 'lab': research}, ROOT)['lab'], research)
        for bad in ({'buildings': {'targets': [], 'keep_builders': 0}},
                    {'buildings': {'targets': [{**target, 'id': 'nope'}], 'keep_builders': 0}},
                    {'lab': {'targets': [], 'keep_builders': 0}},
                    # Builder Base picks alone: their upgrade dialogs are not recognised, so nothing would be done.
                    {'builder': {'targets': [{'id': None, 'name': 'Battle Copter', 'level': None}]}}):
            with self.subTest(bad=bad), self.assertRaises(bot.BotError):
                buildings.validate({**config, **bad}, ROOT)
        with self.assertRaises(bot.BotError):
            buildings.validate(config, ROOT)
        with self.assertRaises(bot.BotError):
            buildings.validate({**config, 'buildings': chosen, 'building_upgrade': {'free_builders': [0, 0, 1, 1]}},
                               ROOT)


if __name__ == '__main__':
    unittest.main()
