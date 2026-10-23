import copy
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

import bot


def profile(root):
    rng = np.random.default_rng(7)
    screens, frames = {}, {}
    for name in bot.SCREEN_NAMES:
        frame = rng.integers(0, 256, (90, 160, 3), dtype=np.uint8)
        anchors = []
        for number, roi in enumerate(([5, 5, 15, 12], [130, 5, 15, 12])):
            x, y, w, h = roi
            path = root / f'{name}-{number}.png'
            cv2.imwrite(str(path), frame[y:y+h, x:x+w])
            anchors.append({'roi': roi, 'file': path.name})
        screens[name] = {'anchors': anchors, 'actions': {}}
        frames[name] = frame
    actions = {
        'home': {'attack': [10, 75], 'switch': [150, 75]},
        'builder': {'attack': [10, 75], 'switch': [150, 75]},
        'home_menu': {'find': [80, 50]},
        'builder_menu': {'find': [80, 50]},
        'home_army': {'attack': [120, 80]},
        'home_scout': {'next': [140, 60]},
        'home_result': {'return': [80, 70]},
        'builder_result': {'return': [80, 70]},
    }
    for name, value in actions.items():
        screens[name]['actions'] = value
    for name in ('home_scout', 'builder_scout', 'builder_stage2'):
        screens[name].update(deploy=[40, 40], troops=[{'slot': [20, 80], 'count': 2}])
    screens['home_scout']['loot'] = {key: [2, 20+i*10, 40, 8] for i, key in enumerate(bot.RESOURCES)}
    config = {'adb': 'adb.exe', 'serial': 'emulator-5554', 'tesseract': 'tesseract.exe',
              'resolution': [160, 90], 'minimum': {'gold': 100, 'elixir': 200, 'dark': 0},
              'screens': screens}
    return config, frames


class Device:
    def __init__(self, frames, initial='home', loot=None):
        self.frames, self.state = frames, initial
        self.inputs = []
        self.loot = iter(loot or [{'gold': 100, 'elixir': 200}])
        self.deploys = 0

    def capture(self):
        return self.frames[self.state].copy()

    def booted(self):
        return True

    def swipe(self, start, end):
        self.inputs.append(('swipe', tuple(start), tuple(end)))

    def hold(self, slot, point, seconds):
        self.inputs.append(('hold', tuple(slot) if slot else None, tuple(point), seconds))

    def tap_many(self, points):
        self.bursts = getattr(self, 'bursts', []) + [[tuple(p) for p in points]]
        for point in points:
            self.tap(point)

    def pinch(self, device, center, start_gap, end_gap):
        self.inputs.append(('pinch', device, tuple(center)))

    def tap(self, point):
        self.inputs.append(tuple(point))
        if self.state in ('home', 'builder'):
            if point == [150, 75]:
                self.state = 'builder' if self.state == 'home' else 'home'
            else:
                self.state += '_menu'
        elif self.state == 'home_menu':
            self.state = 'home_army'
        elif self.state == 'home_army':
            self.state = 'home_scout'
        elif self.state.endswith('_menu'):
            self.state = self.state.replace('_menu', '_scout')
        elif self.state.endswith('_result'):
            self.state = self.state.replace('_result', '')
        elif self.state == 'home_scout' and point == [140, 60]:
            changed = self.frames['home_scout'].copy()
            changed[50:70, 50:100] = 255 - changed[50:70, 50:100]
            self.frames = {**self.frames, 'home_scout': changed}
        elif point == [40, 40]:
            self.deploys += 1
            if self.state.startswith('home'):
                self.state = 'home_battle' if self.deploys % 2 else 'home_result'
            elif self.state in ('builder_scout', 'builder_battle'):
                self.state = 'builder_battle' if self.deploys % 2 else 'builder_stage2'
            else:
                self.state = 'builder_battle2' if self.deploys % 2 else 'builder_result'


class BotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config, self.frames = profile(self.root)

    def make_bot(self, device, live=False):
        reader = lambda image, screen: next(device.loot)
        return bot.Bot(self.config, self.root, device, live=live, reader=reader, timeout=0.02, poll=0)

    def test_resource_grouping_and_malformed_ocr(self):
        for text, expected in [('123456', 123456), ('123 456', 123456), ('1,234,567', 1234567), ('123.456', 123456), ('0', 0)]:
            self.assertEqual(bot.parse_resource(text), expected)
        for text in ('', '12k', '-100', '1O0', '12 34', '1,234.567', '100\n200'):
            with self.subTest(text=text), self.assertRaises(bot.BotError):
                bot.parse_resource(text)

    def test_all_active_minimums_required(self):
        minimum = {'gold': 100, 'elixir': 200, 'dark': 0}
        self.assertTrue(bot.meets_minimum({'gold': 100, 'elixir': 200}, minimum))
        self.assertFalse(bot.meets_minimum({'gold': 99, 'elixir': 300}, minimum))
        with self.assertRaises(bot.BotError):
            bot.meets_minimum({'gold': 100}, minimum)

    def test_config_rejects_invalid_points_and_paths(self):
        bot.validate_config(self.config, self.root, 'both')
        for value in ([160, 5], [-1, 2], [True, 2], [1.5, 2]):
            config = copy.deepcopy(self.config)
            config['screens']['home']['actions']['attack'] = value
            with self.subTest(value=value), self.assertRaises(bot.BotError):
                bot.validate_config(config, self.root, 'home')
        config = copy.deepcopy(self.config)
        config['screens']['home']['anchors'][0]['file'] = '../outside.png'
        with self.assertRaises(bot.BotError):
            bot.validate_config(config, self.root, 'home')

    def test_real_templates_detect_state_and_reject_unknown(self):
        matcher = bot.Detector(self.config, self.root)
        self.assertEqual(matcher.detect(self.frames['home']), 'home')
        with self.assertRaises(bot.BotError):
            matcher.detect(np.zeros((90, 160, 3), dtype=np.uint8))
        with self.assertRaises(bot.BotError):
            matcher.detect(np.zeros((100, 160, 3), dtype=np.uint8))

    @staticmethod
    def bundled():
        """The shipped config.json with its templates, and a helper that draws anchors on a blank 1280x720 frame."""
        root = Path(bot.__file__).parent
        config = bot.load_config(root / 'config.json')
        bot.validate_config(config, root, 'both')

        def frame(*anchors):
            image = np.zeros((720, 1280, 3), dtype=np.uint8)
            for anchor in anchors:
                x, y, w, h = anchor['roi']
                image[y:y + h, x:x + w] = cv2.imread(str(root / anchor['file']))
            return image
        return config, bot.Detector(config, root), frame

    def test_the_bundled_calibration_never_takes_a_low_town_hall_for_the_builder_base(self):
        config, detector, frame = self.bundled()
        screens = config['screens']
        home, builder, low = screens['home']['anchors'], screens['builder']['anchors'], screens['home']['alt_anchors'][0]
        self.assertEqual(detector.detect(frame(*home, *low)), 'home')
        self.assertEqual(detector.detect(frame(*builder)), 'builder')
        # Below Town Hall 7 there is no dark elixir, and the gem count moves up to where the Builder Base shows it.
        gems = next(anchor for anchor in builder if 'gem' in anchor['file'])
        self.assertEqual(detector.detect(frame(*low, gems)), 'home')

    def test_the_bundled_stage_two_is_recognised_whatever_army_the_bar_shows(self):
        config, detector, frame = self.bundled()
        army = np.random.default_rng(29).integers(0, 256, (140, 1280, 3), dtype=np.uint8)
        for screen in ('builder_stage2', 'builder_battle2'):
            image = frame(*config['screens'][screen]['anchors'])
            image[580:] = army
            with self.subTest(screen=screen):
                self.assertEqual(detector.detect(image), screen)

    def test_ambiguous_templates_abort(self):
        config = copy.deepcopy(self.config)
        config['screens']['builder']['anchors'] = config['screens']['home']['anchors']
        with self.assertRaises(bot.BotError):
            bot.Detector(config, self.root).detect(self.frames['home'])

    def test_dry_run_never_inputs_even_on_good_scout(self):
        device = Device(self.frames, 'home_scout')
        self.assertEqual(self.make_bot(device).run('home', 1, 3), 0)
        self.assertEqual(device.inputs, [])

    def test_both_villages_and_builder_second_stage(self):
        device = Device(self.frames)
        self.assertEqual(self.make_bot(device, True).run('both', 1, 3), 2)
        self.assertEqual(device.state, 'builder')
        self.assertEqual(device.inputs.count((40, 40)), 6)
        self.assertEqual(device.inputs.count((150, 75)), 1)

    def test_unreadable_loot_is_skipped_until_the_search_limit_attacks_the_base_on_screen(self):
        device = Device(self.frames)
        runner = self.make_bot(device, True)
        runner.reader = lambda image, screen: (_ for _ in ()).throw(bot.BotError('OCR ambiguous'))
        runner.wait_new_scout = lambda image: 'home_scout'
        self.assertEqual(runner.run('home', 1, 3), 1)
        self.assertEqual(device.inputs.count((140, 60)), 2)
        last_next = len(device.inputs) - 1 - device.inputs[::-1].index((140, 60))
        self.assertGreater(device.inputs.index((40, 40)), last_next)

    def test_the_search_limit_attacks_the_base_on_screen_instead_of_stopping(self):
        device = Device(self.frames, loot=[{'gold': 1, 'elixir': 1}] * 3)
        runner = self.make_bot(device, True)
        runner.timeout = 1
        with self.assertLogs('bot', level='WARNING') as logs:
            self.assertEqual(runner.run('home', 1, 3), 1)
        self.assertEqual(device.inputs.count((140, 60)), 2)
        self.assertIn((40, 40), device.inputs)
        self.assertTrue(any('Batas 3 pencarian' in line for line in logs.output))
        self.assertEqual(runner.progress, 1)

    def test_battle_timeout_does_not_send_recovery_input(self):
        device = Device(self.frames, 'home_battle')
        with self.assertRaises(bot.BotError):
            self.make_bot(device, True).wait({'home_result'}, {'home_battle'})
        self.assertEqual(device.inputs, [])

    def test_wait_tolerates_transition_frames_without_input(self):
        blank = np.zeros((90, 160, 3), dtype=np.uint8)
        frames = iter([blank, blank, self.frames['builder_stage2']])
        device = Device(self.frames, 'builder_battle')
        device.capture = lambda: next(frames).copy()
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0)
        self.assertEqual(runner.wait({'builder_stage2'}, {'builder_battle'}), 'builder_stage2')
        self.assertEqual(device.inputs, [])

    def test_wait_times_out_on_endless_unknown_screen(self):
        device = Device({'home_battle': np.zeros((90, 160, 3), dtype=np.uint8)}, 'home_battle')
        with self.assertRaises(bot.BotError):
            self.make_bot(device, True).wait({'home_result'}, {'home_battle'})
        self.assertEqual(device.inputs, [])

    def test_unknown_screen_before_action_prevents_tap(self):
        device = Device({'home': np.zeros((90, 160, 3), dtype=np.uint8)})
        with self.assertRaises(bot.BotError):
            self.make_bot(device, True).action('home', 'attack')
        self.assertEqual(device.inputs, [])

    def test_adb_uses_explicit_target_and_no_shell(self):
        device = bot.ADB('adb.exe', 'emulator-5554', [160, 90], live=True)
        with patch('bot.subprocess.run') as run:
            run.return_value.stdout = b''
            device.tap([10, 20])
            args, kwargs = run.call_args
            self.assertEqual(args[0], ['adb.exe', '-s', 'emulator-5554', 'shell', 'input', 'tap', '10', '20'])
            self.assertFalse(kwargs.get('shell', False))
            self.assertGreater(kwargs['timeout'], 0)

    def test_adb_dry_run_guard_and_disconnect(self):
        device = bot.ADB('adb.exe', 'emulator-5554', [160, 90])
        with patch('bot.subprocess.run') as run:
            with self.assertRaises(bot.BotError):
                device.tap([10, 20])
            run.assert_not_called()
        with patch('bot.subprocess.run', side_effect=OSError('offline')):
            with self.assertRaises(bot.BotError):
                device.capture()

    def test_adb_capture_reads_raw_rgba_without_png(self):
        pixels = np.zeros((90, 160, 4), dtype=np.uint8)
        pixels[..., :3] = (10, 20, 30)
        device = bot.ADB('adb.exe', 'emulator-5554', [160, 90])
        with patch('bot.subprocess.run') as run:
            run.return_value.stdout = struct.pack('<IIII', 160, 90, 1, 0) + pixels.tobytes()
            image = device.capture()
            self.assertEqual(run.call_args.args[0][3:], ['exec-out', 'screencap'])
        self.assertEqual(image[0, 0].tolist(), [30, 20, 10])
        with patch('bot.subprocess.run') as run:
            run.return_value.stdout = struct.pack('<IIII', 100, 90, 1, 0) + pixels.tobytes()
            with self.assertRaises(bot.BotError):
                device.capture()

    def test_adb_capture_retries_transient_failure(self):
        pixels = np.zeros((90, 160, 4), dtype=np.uint8)
        ok = type('R', (), {'stdout': struct.pack('<IIII', 160, 90, 1, 0) + pixels.tobytes()})()
        device = bot.ADB('adb.exe', 'emulator-5554', [160, 90])
        failure = bot.subprocess.CalledProcessError(1, 'adb')
        with patch('bot.subprocess.run', side_effect=[failure, ok]):
            self.assertEqual(device.capture().shape, (90, 160, 3))
        with patch('bot.subprocess.run', side_effect=[failure] * 3):
            with self.assertRaises(bot.BotError):
                device.capture()
        short = type('R', (), {'stdout': ok.stdout[:1000]})()
        with patch('bot.subprocess.run', side_effect=[short, ok]):
            self.assertEqual(device.capture().shape, (90, 160, 3))

    def test_adb_output_lost_to_full_memory_is_reported_as_memory_error(self):
        device = bot.ADB('adb.exe', 'emulator-5554', [160, 90])
        for lost in (MemoryError(), IndexError('list index out of range')):
            with self.subTest(lost=type(lost).__name__), patch('bot.subprocess.run', side_effect=lost):
                with self.assertRaises(MemoryError):
                    device.capture()

    def test_star_bonus_popup_is_dismissed_while_waiting(self):
        popup = np.random.default_rng(3).integers(0, 256, (90, 160, 3), dtype=np.uint8)
        anchors = []
        for number, roi in enumerate(([5, 5, 15, 12], [130, 5, 15, 12])):
            x, y, w, h = roi
            cv2.imwrite(str(self.root / f'star-{number}.png'), popup[y:y+h, x:x+w])
            anchors.append({'roi': roi, 'file': f'star-{number}.png'})
        screens = {**self.config['screens'], 'star_bonus': {'anchors': anchors, 'actions': {'ok': [70, 70]}}}
        config = {**self.config, 'screens': screens}
        bot.validate_config(config, self.root, 'both')
        device = Device({**self.frames, 'star_bonus': popup}, 'star_bonus')
        device.tap = lambda point: (device.inputs.append(tuple(point)), setattr(device, 'state', 'home'))
        runner = bot.Bot(config, self.root, device, live=True, timeout=1, poll=0)
        self.assertEqual(runner.wait({'home'}, set(), 1), 'home')
        self.assertEqual(device.inputs, [(70, 70)])

    def army_bar(self, kinds):
        """A 1000x200 frame whose bar (y 100-200) holds one painted card per kind, 97 px apart. 'camp' is a Builder
        Base troop card (magenta header top right), 'machine' a Builder Base hero card (shorter, no header)."""
        colours = {'troop': (182, 127, 64), 'siege': (220, 200, 60), 'hero': (211, 44, 60),
                   'warden': (211, 44, 60), 'spell': (184, 68, 88), 'camp': (80, 180, 60), 'machine': (200, 110, 70)}
        image = np.full((200, 1000, 3), 40, dtype=np.uint8)
        for index, kind in enumerate(kinds):
            left = 10 + index * 97
            image[100:200, left:left + 88] = colours[kind]
            image[170:200, left:left + 88] = 0
            if kind in ('troop', 'spell'):
                image[103:118, left + 52:left + 84] = 255
            if kind == 'warden':
                image[172:196, left + 50:left + 84] = (60, 200, 60)
            if kind == 'camp':
                image[100:120, left + 32:left + 88] = (230, 70, 210)
            if kind == 'machine':
                image[100:118, left:left + 88] = 40
        return image

    def test_the_deploy_plan_follows_the_cards_in_the_bar(self):
        image = self.army_bar(['troop', 'troop', 'siege', 'hero', 'warden', 'hero', 'spell', 'spell'])
        auto = {'bar': [0, 100, 1000, 100], 'hold': 10,
                'spells': [[50, 50], [30, 30], [30, 70], [70, 50], [60, 30], [60, 70]]}
        kinds = [bot.card_kind(image, auto['bar'], card) for card in bot.bar_cards(image, auto['bar'])]
        self.assertEqual(kinds, ['counted', 'counted', 'siege', 'hero', 'warden', 'hero', 'counted', 'counted'])
        plan = bot.auto_plan(image, auto)
        centre = [54 + 97 * i for i in range(8)]
        self.assertEqual([(step['slot'][0], step.get('hold'), step.get('tap_only', False)) for step in plan], [
            (centre[0], 10, False), (centre[1], 10, False), (centre[2], None, False),
            (centre[4], None, False), (centre[4], None, True), (centre[3], None, False), (centre[3], None, True),
            (centre[5], None, False), (centre[5], None, True),
            (centre[6], None, False), (centre[7], None, False)])
        rounds = auto['spells']
        self.assertEqual([step['points'] for step in plan[-2:]], [rounds, rounds[3:] + rounds[:3]])
        self.assertTrue(all(step['slot'][1] == 150 for step in plan))

    def test_an_army_without_siege_or_heroes_tells_troops_from_spells_by_colour(self):
        image = self.army_bar(['troop', 'spell'])
        plan = bot.auto_plan(image, {'bar': [0, 100, 1000, 100], 'hold': 10, 'spells': [[50, 50]]})
        self.assertEqual([('hold' in step, 'points' in step) for step in plan], [(True, False), (False, True)])

    def test_the_builder_base_plan_drops_the_machine_then_taps_each_camp_at_the_next_spread_point(self):
        image = self.army_bar(['machine', 'camp', 'camp', 'camp', 'camp'])
        auto = {'bar': [0, 100, 1000, 100], 'points': [[10, 10], [20, 20], [30, 30]]}
        cards = bot.bar_cards(image, auto['bar'])
        self.assertEqual([bot.card_kind(image, auto['bar'], card, camps=True) for card in cards],
                         ['hero', 'counted', 'counted', 'counted', 'counted'])
        centre = [54 + 97 * i for i in range(5)]
        plan = bot.auto_plan(image, auto)
        self.assertEqual([(step['slot'][0], step.get('points'), step.get('tap_only', False)) for step in plan], [
            (centre[0], None, False), (centre[0], None, True), (centre[1], [[10, 10]], False),
            (centre[2], [[20, 20]], False), (centre[3], [[30, 30]], False), (centre[4], [[10, 10]], False)])
        self.assertTrue(all(step['count'] == 1 and 'hold' not in step for step in plan))

    def test_the_bar_is_read_once_two_frames_show_the_same_cards(self):
        auto = {'bar': [0, 100, 1000, 100], 'points': [[10, 10]]}
        sliding, settled = self.army_bar(['machine', 'camp']), self.army_bar(['machine', 'camp', 'camp'])
        device = Device(self.frames)
        shots = iter([sliding, settled, settled])
        device.capture = lambda: next(shots).copy()
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0.001)
        runner.detector.detect = lambda image: 'builder_stage2'
        self.assertEqual(runner.bar_plan(auto, {'builder_stage2'}), bot.auto_plan(settled, auto))

    def test_builder_auto_needs_spread_points_instead_of_spells(self):
        config = copy.deepcopy(self.config)
        config['screens']['builder_scout']['auto'] = {'bar': [0, 60, 160, 30], 'points': [[40, 40], [50, 30]]}
        bot.validate_config(config, self.root, 'builder')
        for points in ([], [[160, 5]], 'none'):
            broken = copy.deepcopy(config)
            if points == 'none':
                del broken['screens']['builder_scout']['auto']['points']
            else:
                broken['screens']['builder_scout']['auto']['points'] = points
            with self.subTest(points=points), self.assertRaises(bot.BotError):
                bot.validate_config(broken, self.root, 'builder')

    def test_reward_chest_and_card_are_tapped_through_to_village(self):
        rng = np.random.default_rng(13)
        frames = {name: rng.integers(0, 256, (90, 160, 3), dtype=np.uint8) for name in ('reward_chest', 'reward_card')}
        screens = dict(self.config['screens'])
        for name, frame in frames.items():
            anchors = []
            for number, roi in enumerate(([5, 5, 15, 12], [130, 5, 15, 12])):
                x, y, w, h = roi
                cv2.imwrite(str(self.root / f'{name}-{number}.png'), frame[y:y+h, x:x+w])
                anchors.append({'roi': roi, 'file': f'{name}-{number}.png'})
            screens[name] = {'anchors': anchors, 'actions': {'open' if name == 'reward_chest' else 'continue':
                                                              [70, 40] if name == 'reward_chest' else [70, 80]}}
        config = {**self.config, 'screens': screens}
        bot.validate_config(config, self.root, 'both')
        device = Device({**self.frames, **frames}, 'reward_chest')
        flow = iter(['reward_chest', 'reward_card', 'home'])
        next(flow)
        device.tap = lambda point: (device.inputs.append(tuple(point)), setattr(device, 'state', next(flow)))
        runner = bot.Bot(config, self.root, device, live=True, timeout=1, poll=0)
        self.assertEqual(runner.wait({'home'}, {'home_result'}, 1), 'home')
        self.assertEqual(device.inputs, [(70, 40), (70, 80)])

    def test_a_refused_search_goes_home_and_counts_as_a_timeout(self):
        failed = np.random.default_rng(23).integers(0, 256, (90, 160, 3), dtype=np.uint8)
        anchors = []
        for number, roi in enumerate(([5, 5, 15, 12], [130, 5, 15, 12])):
            x, y, w, h = roi
            cv2.imwrite(str(self.root / f'failed-{number}.png'), failed[y:y+h, x:x+w])
            anchors.append({'roi': roi, 'file': f'failed-{number}.png'})
        config = {**self.config, 'screens': {**self.config['screens'],
                                             'search_failed': {'anchors': anchors, 'actions': {'close': [20, 80]}}}}
        bot.validate_config(config, self.root, 'both')
        device = Device({**self.frames, 'search_failed': failed}, 'search_failed')
        device.tap = lambda point: (device.inputs.append(tuple(point)), setattr(device, 'state', 'home'))
        runner = bot.Bot(config, self.root, device, live=True, timeout=1, poll=0)
        with self.assertRaises(bot.WaitTimeout):
            runner.wait({'home_army'}, {'home_menu'}, 1)
        self.assertEqual(device.inputs, [(20, 80)])

    def test_bad_numbers_are_refused_before_the_emulator_is_touched(self):
        for flags in (['--searches', '-1'], ['--max-recoveries', '5000'], ['--cycles', '0']):
            with patch('sys.argv', ['bot.py', '--until-full', '--live', *flags]), \
                    patch('bot.load_config') as load, patch('bot.ADB') as adb:
                self.assertEqual(bot.main(), 1)
            load.assert_not_called()
            adb.assert_not_called()

    def test_busy_builders_are_closed_with_x_and_stop_that_village(self):
        busy = np.random.default_rng(43).integers(0, 256, (90, 160, 3), dtype=np.uint8)
        anchors = []
        for number, roi in enumerate(([5, 5, 15, 12], [130, 5, 15, 12])):
            x, y, w, h = roi
            cv2.imwrite(str(self.root / f'busy-{number}.png'), busy[y:y+h, x:x+w])
            anchors.append({'roi': roi, 'file': f'busy-{number}.png'})
        config = {**self.config, 'screens': {**self.config['screens'],
                                             'builders_busy': {'anchors': anchors, 'actions': {'close': [150, 8]}}}}
        bot.validate_config(config, self.root, 'both')
        device = Device({**self.frames, 'builders_busy': busy}, 'builders_busy')
        device.tap = lambda point: (device.inputs.append(tuple(point)), setattr(device, 'state', 'builder'))
        runner = bot.Bot(config, self.root, device, live=True, timeout=1, poll=0)
        with self.assertRaises(bot.NoFreeBuilder):
            runner.wait({'wall_confirm'}, {'wall_selected'}, 1)
        self.assertEqual(device.inputs, [(150, 8)])

    def test_walls_skip_a_village_with_no_free_builder(self):
        runner = self.make_bot(Device(self.frames), True)
        runner.wall_order = ('builder', 'home')
        calls = []
        runner.run_until_full = lambda searches: calls.append('farm')
        runner.upgrade_walls = lambda village: calls.append(village) or (
            (_ for _ in ()).throw(bot.NoFreeBuilder('sibuk')) if village == 'builder' else None)
        runner.run_walls(0)
        self.assertEqual(calls, ['builder', 'home'])
        self.assertEqual(runner.walls_skipped, {'builder'})

    def test_a_leftover_wall_menu_is_closed_while_waiting(self):
        runner = self.make_bot(Device(self.frames), True)
        screens = iter([('wall_selected', None), ('home', None)])
        closed = []
        runner.observe = lambda: next(screens)
        runner.close_menus = lambda: closed.append(1)
        runner.wait_still = lambda limit: None
        self.assertEqual(runner.wait({'home'}, set(), 1), 'home')
        self.assertEqual(closed, [1])

    def test_an_idle_disconnect_is_reloaded(self):
        idle = np.random.default_rng(41).integers(0, 256, (90, 160, 3), dtype=np.uint8)
        anchors = []
        for number, roi in enumerate(([5, 5, 15, 12], [130, 5, 15, 12])):
            x, y, w, h = roi
            cv2.imwrite(str(self.root / f'idle-{number}.png'), idle[y:y+h, x:x+w])
            anchors.append({'roi': roi, 'file': f'idle-{number}.png'})
        config = {**self.config, 'screens': {**self.config['screens'],
                                             'idle': {'anchors': anchors, 'actions': {'reload': [40, 60]}}}}
        bot.validate_config(config, self.root, 'both')
        device = Device({**self.frames, 'idle': idle}, 'idle')
        device.tap = lambda point: (device.inputs.append(tuple(point)), setattr(device, 'state', 'home'))
        runner = bot.Bot(config, self.root, device, live=True, timeout=1, poll=0)
        self.assertEqual(runner.wait({'home'}, set(), 1), 'home')
        self.assertEqual(device.inputs, [(40, 60)])

    def test_another_device_on_the_account_stops_the_run_without_reloading(self):
        taken = np.random.default_rng(37).integers(0, 256, (90, 160, 3), dtype=np.uint8)
        anchors = []
        for number, roi in enumerate(([5, 5, 15, 12], [130, 5, 15, 12])):
            x, y, w, h = roi
            cv2.imwrite(str(self.root / f'taken-{number}.png'), taken[y:y+h, x:x+w])
            anchors.append({'roi': roi, 'file': f'taken-{number}.png'})
        config = {**self.config, 'screens': {**self.config['screens'], 'other_device': {'anchors': anchors}}}
        bot.validate_config(config, self.root, 'both')
        device = Device({**self.frames, 'other_device': taken}, 'other_device')
        device.connected = device.game_in_front = lambda: True
        runner = bot.Bot(config, self.root, device, live=True, timeout=1, poll=0)
        with self.assertRaises(bot.OtherDevice):
            runner.wait({'home'}, set(), 1)
        with self.assertRaises(bot.OtherDevice):
            runner.run_until_full_resilient(0, max_recoveries=3)
        self.assertEqual(device.inputs, [])

    def test_league_window_is_closed_while_waiting_for_the_village(self):
        league = np.random.default_rng(17).integers(0, 256, (90, 160, 3), dtype=np.uint8)
        anchors = []
        for number, roi in enumerate(([5, 5, 15, 12], [130, 5, 15, 12])):
            x, y, w, h = roi
            cv2.imwrite(str(self.root / f'league-{number}.png'), league[y:y+h, x:x+w])
            anchors.append({'roi': roi, 'file': f'league-{number}.png'})
        config = {**self.config, 'screens': {**self.config['screens'],
                                             'league': {'anchors': anchors, 'actions': {'close': [150, 8]}}}}
        bot.validate_config(config, self.root, 'both')
        device = Device({**self.frames, 'league': league}, 'league')
        device.tap = lambda point: (device.inputs.append(tuple(point)), setattr(device, 'state', 'home'))
        runner = bot.Bot(config, self.root, device, live=True, timeout=1, poll=0)
        self.assertEqual(runner.wait({'home'}, set(), 1), 'home')
        self.assertEqual(device.inputs, [(150, 8)])

    def test_guarded_tap_dismisses_popup_then_taps(self):
        popup = np.random.default_rng(21).integers(0, 256, (90, 160, 3), dtype=np.uint8)
        anchors = []
        for number, roi in enumerate(([5, 5, 15, 12], [130, 5, 15, 12])):
            x, y, w, h = roi
            cv2.imwrite(str(self.root / f'star-{number}.png'), popup[y:y+h, x:x+w])
            anchors.append({'roi': roi, 'file': f'star-{number}.png'})
        screens = {**self.config['screens'], 'star_bonus': {'anchors': anchors, 'actions': {'ok': [70, 70]}}}
        config = {**self.config, 'screens': screens}
        device = Device({**self.frames, 'star_bonus': popup}, 'star_bonus')
        def tap(point):
            device.inputs.append(tuple(point))
            if device.state == 'star_bonus':
                device.state = 'home'
        device.tap = tap
        runner = bot.Bot(config, self.root, device, live=True, timeout=1, poll=0)
        runner.guarded_tap({'home'}, [10, 75])
        self.assertEqual(device.inputs, [(70, 70), (10, 75)])

    def test_wait_ignores_popup_that_closes_itself(self):
        popup = np.random.default_rng(23).integers(0, 256, (90, 160, 3), dtype=np.uint8)
        anchors = []
        for number, roi in enumerate(([5, 5, 15, 12], [130, 5, 15, 12])):
            x, y, w, h = roi
            cv2.imwrite(str(self.root / f'star-{number}.png'), popup[y:y+h, x:x+w])
            anchors.append({'roi': roi, 'file': f'star-{number}.png'})
        screens = {**self.config['screens'], 'star_bonus': {'anchors': anchors, 'actions': {'ok': [70, 70]}}}
        config = {**self.config, 'screens': screens}
        shots = iter([popup, self.frames['home'], self.frames['home'], self.frames['home']])
        device = Device(self.frames)
        device.capture = lambda: next(shots).copy()
        runner = bot.Bot(config, self.root, device, live=True, timeout=1, poll=0)
        self.assertEqual(runner.wait({'home'}, set(), 1), 'home')

    def cart_config(self):
        popup = np.random.default_rng(5).integers(0, 256, (90, 160, 3), dtype=np.uint8)
        anchors = []
        for number, roi in enumerate(([5, 5, 15, 12], [130, 5, 15, 12])):
            x, y, w, h = roi
            cv2.imwrite(str(self.root / f'cart-{number}.png'), popup[y:y+h, x:x+w])
            anchors.append({'roi': roi, 'file': f'cart-{number}.png'})
        icon = self.frames['builder'][40:50, 60:72]
        cv2.imwrite(str(self.root / 'cart-icon.png'), icon)
        builder = {**self.config['screens']['builder'],
                   'targets': {'cart': {'file': 'cart-icon.png', 'max_score': 0.12}}}
        screens = {**self.config['screens'], 'builder': builder,
                   'builder_cart': {'anchors': anchors, 'actions': {'claim': [100, 80], 'close': [150, 10]}}}
        return {**self.config, 'screens': screens}, popup

    def test_collect_cart_claims_then_closes(self):
        config, popup = self.cart_config()
        bot.validate_config(config, self.root, 'builder')
        device = Device({**self.frames, 'builder_cart': popup}, 'builder')

        def tap(point):
            device.inputs.append(tuple(point))
            if tuple(point) == (66, 45):
                device.state = 'builder_cart'
            elif tuple(point) == (150, 10):
                device.state = 'builder'
        device.tap = tap
        bot.Bot(config, self.root, device, live=True, timeout=1, poll=0).collect_cart()
        self.assertEqual(device.inputs, [(66, 45), (100, 80), (150, 10)])
        self.assertEqual(device.state, 'builder')

    def test_target_alt_file_matches_changed_look(self):
        config, _ = self.cart_config()
        missing = self.frames['home'][0:1, 0:1]
        cv2.imwrite(str(self.root / 'cart-empty-look.png'), np.random.default_rng(11).integers(0, 256, (10, 12, 3), dtype=np.uint8))
        cart = {'file': 'cart-empty-look.png', 'alt_files': ['cart-icon.png'], 'max_score': 0.12}
        builder = {**config['screens']['builder'], 'targets': {'cart': cart}}
        config = {**config, 'screens': {**config['screens'], 'builder': builder}}
        bot.validate_config(config, self.root, 'builder')
        device = Device(self.frames, 'builder')
        bot.Bot(config, self.root, device, live=True, timeout=1, poll=0).guarded_tap(
            {'builder'}, lambda image: bot.Bot(config, self.root, device).locate_target(image, cart))
        self.assertEqual(device.inputs, [(66, 45)])
        self.assertIsNotNone(missing)

    def test_read_stable_tolerates_animation_frames(self):
        blank = np.zeros((90, 160, 3), dtype=np.uint8)
        shots = iter([blank, self.frames['home'], self.frames['home']])
        device = Device(self.frames)
        device.capture = lambda: next(shots).copy()
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0.3)

        def reader(image):
            if not image.any():
                raise bot.BotError('animasi')
            return 7
        self.assertEqual(runner.read_stable(reader, 5), 7)
        shots = iter([blank, self.frames['home']])
        device.capture = lambda: next(shots).copy()
        self.assertEqual(runner.read_until(reader, 5), 7)

    def test_until_full_closes_cart_popup_before_reading_storage(self):
        config, popup = self.cart_config()
        device = Device({**self.frames, 'builder_cart': popup}, 'builder')
        device.tap = lambda point: (device.inputs.append(tuple(point)), setattr(device, 'state', 'builder'))
        runner = bot.Bot(config, self.root, device, live=True, timeout=1, poll=0)
        runner.navigate = lambda village: setattr(device, 'state', village)
        runner.collect_cart = lambda: setattr(device, 'state', 'builder_cart')
        seen = []
        runner.goals = {'builder': {'gold', 'elixir', 'cart'}}
        runner.village_done = lambda village, image: seen.append(device.state) or True
        runner.run_until_full(0)
        self.assertEqual(seen, ['builder'])
        self.assertIn((150, 10), device.inputs)

    def test_battle_ending_back_in_village_is_not_an_error(self):
        for village, attack in (('builder', 'builder_attack'), ('home', 'home_attack')):
            device = Device(self.frames, village)
            runner = bot.Bot(self.config, self.root, device, live=True, reader=lambda i, s: {'gold': 100, 'elixir': 200},
                             timeout=1, poll=0)
            steps = {(village, 'attack'): f'{village}_menu', (f'{village}_menu', 'find'):
                     'home_army' if village == 'home' else 'builder_scout', ('home_army', 'attack'): 'home_scout'}
            actions = []
            runner.action = lambda s, n: (actions.append((s, n)), setattr(device, 'state', steps.get((s, n), device.state)))
            runner.deploy = lambda screen, allowed: setattr(device, 'state', village)
            runner.collect_cart = lambda: None
            with self.subTest(village=village):
                getattr(runner, attack)(0) if village == 'home' else runner.builder_attack(collect=False)
                self.assertNotIn((f'{village}_result', 'return'), actions)

    def speed_config(self, frame):
        icon = frame[40:52, 100:114]
        cv2.imwrite(str(self.root / 'speed1x.png'), icon)
        return {**self.config, 'speed_button': {'file': 'speed1x.png', 'max_score': 0.08, 'region': [90, 30, 40, 40]}}

    def test_wait_taps_1x_speed_button_once_during_battle(self):
        frame = self.frames['home_battle']
        config = self.speed_config(frame)
        bot.validate_config(config, self.root, 'both')
        device = Device(self.frames, 'home_battle')
        shots = []
        def capture():
            shots.append(1)
            if len(shots) >= 4:
                device.state = 'home_result'
            return device.frames[device.state].copy()
        device.capture = capture
        runner = bot.Bot(config, self.root, device, live=True, timeout=2, poll=0)
        self.assertEqual(runner.wait({'home_result'}, {'home_battle'}), 'home_result')
        self.assertEqual(device.inputs, [(107, 46)])

    def test_speed_button_not_tapped_when_absent_or_outside_battle(self):
        config = self.speed_config(self.frames['home_battle'])
        changed = self.frames['home_battle'].copy()
        changed[40:52, 100:114] = 255 - changed[40:52, 100:114]
        for frames, state in (({**self.frames, 'home_battle': changed}, 'home_battle'), (self.frames, 'home')):
            device = Device(frames, state)
            runner = bot.Bot(config, self.root, device, live=True, timeout=1, poll=0)
            with self.subTest(state=state):
                runner.try_speed_up(device.capture(), state)
                self.assertEqual(device.inputs, [])

    def test_wait_closes_leftover_cart_popup(self):
        config, popup = self.cart_config()
        device = Device({**self.frames, 'builder_cart': popup}, 'builder_cart')
        device.tap = lambda point: (device.inputs.append(tuple(point)), setattr(device, 'state', 'builder'))
        runner = bot.Bot(config, self.root, device, live=True, timeout=1, poll=0)
        self.assertEqual(runner.wait(set(bot.VILLAGES), set(), 1), 'builder')
        self.assertEqual(device.inputs, [(150, 10)])

    def test_collect_cart_skips_when_cart_not_visible(self):
        config, _ = self.cart_config()
        hidden = self.frames['builder'].copy()
        hidden[40:50, 60:72] = 255 - hidden[40:50, 60:72]
        device = Device({**self.frames, 'builder': hidden}, 'builder')
        bot.Bot(config, self.root, device, live=True, timeout=1, poll=0).collect_cart()
        self.assertEqual(device.inputs, [])

    def test_a_cart_missing_on_several_visits_in_a_row_no_longer_holds_the_run(self):
        config, popup = self.cart_config()
        hidden = self.frames['builder'].copy()
        hidden[40:50, 60:72] = 255 - hidden[40:50, 60:72]
        device = Device({**self.frames, 'builder': hidden, 'builder_cart': popup}, 'builder')
        device.tap = lambda point: (device.inputs.append(tuple(point)), setattr(
            device, 'state', 'builder_cart' if tuple(point) == (66, 45) else 'builder'))
        runner = bot.Bot(config, self.root, device, live=True, timeout=1, poll=0)
        runner.goals = {'builder': {'cart'}}
        for _ in range(bot.CART_MISS_LIMIT - 1):
            runner.collect_cart()
        device.frames['builder'] = self.frames['builder']
        runner.collect_cart()
        self.assertIn((66, 45), device.inputs)
        device.frames['builder'] = hidden
        for _ in range(bot.CART_MISS_LIMIT - 1):
            runner.collect_cart()
        self.assertFalse(runner.village_done('builder', None))
        with self.assertLogs('bot', level='WARNING') as logs:
            for _ in range(3):
                runner.collect_cart()
        self.assertTrue(runner.village_done('builder', None))
        self.assertEqual(sum('dianggap selesai' in line for line in logs.output), 1)

    def test_disabled_filters_do_not_require_ocr_executable(self):
        config = {**self.config, 'minimum': {'gold': 0, 'elixir': 0, 'dark': 0}, 'tesseract': ''}
        runner = bot.Bot(config, self.root, Device(self.frames))
        self.assertEqual(runner.read_loot(self.frames['home_scout'], config['screens']['home_scout']), {})

    def test_calibration_init_and_no_overwrite(self):
        import calibrate
        path = self.root / 'new-config.json'
        with patch('sys.argv', ['calibrate.py', '--init', '--config', str(path), '--serial', 'emulator-5554', '--gold', '500000']):
            self.assertEqual(calibrate.main(), 0)
            config = json.loads(path.read_text())
            self.assertEqual(config['minimum']['gold'], 500000)
            with self.assertLogs('calibrate', level='ERROR'):
                self.assertEqual(calibrate.main(), 1)
            self.assertEqual(json.loads(path.read_text()), config)

    def recalibrate(self, config, screen, *flags):
        """Run calibrate.py on a saved shot with the ROI and point pickers answered; returns the saved config."""
        import calibrate
        path, shot = self.root / 'config.json', self.root / 'shot.png'
        path.write_text(json.dumps(config), encoding='utf-8')
        cv2.imwrite(str(shot), self.frames['home'])
        rois = iter([[5, 5, 15, 12], [130, 5, 15, 12]])
        argv = ['calibrate.py', '--config', str(path), '--image', str(shot), '--screen', screen, *flags]
        with patch('sys.argv', argv), patch('calibrate.choose_roi', lambda image, label: next(rois)), \
                patch('calibrate.choose_point', lambda image, label: [10, 20]):
            self.assertEqual(calibrate.main(), 0)
        return json.loads(path.read_text(encoding='utf-8'))

    def test_calibration_replace_keeps_everything_but_the_anchors_and_tap_points(self):
        config = copy.deepcopy(self.config)
        builder = config['screens']['builder']
        builder.update(targets={'switch': {'file': 'boat.png', 'max_score': 0.1}}, overrides=['builder_battle'],
                       alt_anchors=[builder['anchors']], actions={'attack': [1, 1], 'cart': [2, 2]})
        scout = config['screens']['home_scout']
        scout.update(auto={'bar': [0, 60, 160, 30], 'hold': 5, 'spells': [[40, 40]]},
                     sweep={'slots': [[20, 80]], 'deploy': [40, 40]})
        saved = self.recalibrate(config, 'builder', '--replace')['screens']['builder']
        self.assertEqual(saved['actions'], {'attack': [10, 20], 'cart': [10, 20]})
        self.assertEqual({key: saved[key] for key in ('targets', 'overrides', 'alt_anchors')},
                         {key: builder[key] for key in ('targets', 'overrides', 'alt_anchors')})
        self.assertNotEqual(saved['anchors'], builder['anchors'])
        saved = self.recalibrate(config, 'home_scout', '--replace')['screens']['home_scout']
        for key in ('auto', 'sweep', 'deploy', 'troops', 'loot'):
            self.assertEqual(saved[key], scout[key], key)
        self.assertEqual([anchor['roi'] for anchor in saved['anchors']], [[5, 5, 15, 12], [130, 5, 15, 12]])

    def test_calibration_accepts_popups_and_wall_menus(self):
        for screen in ('gem_offer', 'star_bonus', 'wall_confirm', 'other_device'):
            with self.subTest(screen=screen):
                saved = self.recalibrate(self.config, screen)['screens'][screen]
                self.assertEqual(set(saved['actions']), set(bot.ACTIONS.get(screen, ())))
                self.assertEqual(len(saved['anchors']), 2)

    def test_png_capture_and_no_overwrite(self):
        import calibrate
        path = self.root / 'capture.png'
        calibrate.write_png(path, self.frames['home'])
        np.testing.assert_array_equal(cv2.imread(str(path)), self.frames['home'])
        with self.assertRaises(FileExistsError):
            calibrate.write_png(path, self.frames['builder'])
        np.testing.assert_array_equal(cv2.imread(str(path)), self.frames['home'])

    def test_next_requires_changed_opponent_before_reading_loot(self):
        device = Device(self.frames, 'home_scout')
        runner = self.make_bot(device, True)
        runner.action('home_scout', 'next')
        state, _ = runner.observe()
        self.assertEqual(state, 'home_scout')
        unchanged = self.frames['home_scout']
        self.assertEqual(runner.wait_new_scout(unchanged), 'home_scout')
        with self.assertRaises(bot.BotError):
            runner.wait_new_scout(device.capture())

    @staticmethod
    def digits_image():
        image = np.zeros((15, 50, 3), dtype=np.uint8)
        image[3:12, 5:9] = 255
        image[3:12, 15:19] = 255
        image[2:4, 30:32] = 255
        return image

    @staticmethod
    def rendered(text):
        image = np.zeros((30, 14 * len(text) + 10, 3), dtype=np.uint8)
        cv2.putText(image, text, (4, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        return image

    def digit_templates(self, digits='0123456789'):
        return {d: bot.glyphs(self.rendered(d))[0] for d in digits}

    def test_digit_reader_reads_grouped_number(self):
        self.assertEqual(bot.read_number(self.rendered('504 548'), self.digit_templates()), 504548)

    def test_hud_number_ignores_smaller_text_showing_through_on_its_left(self):
        image = np.zeros((30, 120, 3), dtype=np.uint8)
        cv2.putText(image, 'ahl', (2, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
        cv2.putText(image, '1 073', (36, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        templates = self.digit_templates()
        self.assertEqual(bot.read_trailing_number(image, templates), 1073)
        self.assertEqual(bot.read_trailing_number(self.rendered('504 548'), templates), 504548)
        speck = np.zeros((30, 120, 3), dtype=np.uint8)
        cv2.putText(speck, '1  073', (20, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        speck[12:22, 38:42] = 255
        self.assertEqual(bot.read_trailing_number(speck, templates), 1073)
        with self.assertRaises(bot.BotError):
            bot.read_trailing_number(np.zeros((30, 120, 3), dtype=np.uint8), templates)

    def test_classify_threshold_is_adjustable(self):
        templates = self.digit_templates()
        glyph = bot.glyphs(self.rendered('7'))[0]
        score = float(cv2.matchTemplate(glyph, templates['7'], cv2.TM_CCOEFF_NORMED)[0, 0])
        self.assertIsNone(bot.classify(glyph, templates, score + 0.01))
        self.assertEqual(bot.classify(glyph, templates, score - 0.01), '7')
        self.assertLess(bot.FRACTION_MIN_SCORE, bot.DIGIT_MIN_SCORE)

    def test_read_fraction_splits_on_single_unknown_glyph(self):
        templates = self.digit_templates()
        image = self.rendered('120 / 1600')
        self.assertEqual(bot.read_fraction(image, templates), (120, 1600))
        with self.assertRaises(bot.BotError):
            bot.read_fraction(self.rendered('1600'), templates)

    def test_village_full_compares_bar_with_tooltip_max(self):
        templates = self.digit_templates()
        folder = self.root / 'digits'
        folder.mkdir()
        for digit, glyph in templates.items():
            cv2.imwrite(str(folder / f'{digit}.png'), (glyph * 255).astype(np.uint8))

        def with_number(base, text, top):
            out = base.copy()
            out[top:top + 30, 20:120] = 0
            digits = self.rendered(text)
            out[top:top + 30, 20:20 + digits.shape[1]] = digits
            return out

        frame = with_number(self.frames['home'], '140', 22)
        tip = with_number(frame, '140', 55)
        cap = {'bar': [20, 22, 100, 30], 'tap': [100, 10], 'max': [20, 55, 100, 30]}
        config = {**self.config, 'digits': 'digits', 'capacity': {'home': {'gold': cap}}}
        bot.validate_config(config, self.root, 'both')
        device = Device({**self.frames, 'home': frame})
        def toggle(point):
            device.inputs.append(tuple(point))
            shown = device.frames['home'] is tip
            device.frames = {**device.frames, 'home': frame if shown else tip}
        device.tap = toggle
        runner = bot.Bot(config, self.root, device, live=True, timeout=1, poll=0)
        self.assertTrue(runner.village_full('home', frame))
        self.assertEqual(device.inputs, [(100, 10), (100, 10)])
        self.assertIs(device.frames['home'], frame)
        self.assertFalse(runner.village_full('home', with_number(frame, '139', 22)))
        self.assertEqual(device.inputs, [(100, 10), (100, 10)])

    def test_tooltip_label_gates_max_reading(self):
        templates = self.digit_templates()
        folder = self.root / 'digits'
        folder.mkdir()
        for digit, glyph in templates.items():
            cv2.imwrite(str(folder / f'{digit}.png'), (glyph * 255).astype(np.uint8))

        def with_number(base, text, top):
            out = base.copy()
            out[top:top + 30, 20:120] = 0
            digits = self.rendered(text)
            out[top:top + 30, 20:20 + digits.shape[1]] = digits
            return out

        frame = with_number(with_number(self.frames['home'], '140', 22), '999', 55)
        label = np.random.default_rng(31).integers(0, 256, (10, 14, 3), dtype=np.uint8)
        tip = with_number(frame, '140', 55)
        tip[70:80, 130:144] = label
        cv2.imwrite(str(self.root / 'maks.png'), label)
        cap = {'bar': [20, 22, 100, 30], 'tap': [100, 10], 'max': [20, 55, 100, 30],
               'label': {'file': 'maks.png', 'region': [120, 60, 40, 30], 'max_score': 0.05}}
        config = {**self.config, 'digits': 'digits', 'capacity': {'home': {'gold': cap}}}
        bot.validate_config(config, self.root, 'both')
        device = Device({**self.frames, 'home': frame})
        def toggle(point):
            device.inputs.append(tuple(point))
            device.frames = {**device.frames, 'home': frame if device.frames['home'] is tip else tip}
        device.tap = toggle
        tip[5:17, 5:20] = 0
        runner = bot.Bot(config, self.root, device, live=True, timeout=1, poll=0)
        self.assertEqual(runner.storage_max('home', 'gold', cap), 140)
        self.assertIs(device.frames['home'], frame)
        self.assertEqual(device.inputs, [(100, 10), (100, 10)])
        closed = {**self.frames, 'home': frame}
        never = Device(closed)
        never.tap = lambda point: never.inputs.append(tuple(point))
        runner = bot.Bot(config, self.root, never, live=True, timeout=1, poll=0)
        with self.assertRaises(bot.BotError):
            runner.storage_max('home', 'gold', cap)

    def test_digit_reader_ignores_unaligned_background_blobs(self):
        image = self.rendered('504 548')
        image = np.pad(image, ((0, 0), (40, 0), (0, 0)))
        cv2.line(image, (5, 2), (25, 18), (255, 255, 255), 2)
        self.assertEqual(bot.read_number(image, self.digit_templates()), 504548)

    def test_configured_maximum_skips_tooltip(self):
        cap = {'bar': [20, 22, 100, 30], 'tap': [100, 10], 'max': [20, 55, 100, 30], 'maximum': 140}
        config = {**self.config, 'digits': 'digits', 'capacity': {'home': {'gold': cap}}}
        folder = self.root / 'digits'
        folder.mkdir()
        for digit, glyph in self.digit_templates().items():
            cv2.imwrite(str(folder / f'{digit}.png'), (glyph * 255).astype(np.uint8))
        bot.validate_config(config, self.root, 'both')
        device = Device(self.frames)
        runner = bot.Bot(config, self.root, device, live=True, timeout=1, poll=0)
        self.assertEqual(runner.storage_max('home', 'gold', cap), 140)
        self.assertEqual(device.inputs, [])
        with self.assertRaises(bot.BotError):
            bot.validate_config({**config, 'capacity': {'home': {'gold': {**cap, 'maximum': -5}}}}, self.root, 'both')

    def test_read_number_threshold_is_adjustable(self):
        templates = self.digit_templates()
        image = self.rendered('7')
        glyph = bot.glyphs(image)[0]
        score = float(cv2.matchTemplate(glyph, templates['7'], cv2.TM_CCOEFF_NORMED)[0, 0])
        with self.assertRaises(bot.BotError):
            bot.read_number(image, templates, score + 0.01)
        self.assertEqual(bot.read_number(image, templates, score - 0.01), 7)

    def test_digit_variants_extend_templates(self):
        base = self.digit_templates()
        folder = self.root / 'digits'
        folder.mkdir()
        for digit, glyph in base.items():
            cv2.imwrite(str(folder / f'{digit}.png'), (glyph * 255).astype(np.uint8))
        thick = cv2.dilate(bot.glyphs(self.rendered('9'))[0], np.ones((3, 3), np.uint8))
        cv2.imwrite(str(folder / '9-ui2.png'), (thick * 255).astype(np.uint8))
        templates = bot.load_digit_templates(self.root, 'digits')
        self.assertIn('9-ui2', templates)
        self.assertEqual(bot.classify(thick, templates), '9')
        self.assertEqual(bot.classify(base['4'], templates), '4')

    def test_digit_reader_rejects_unknown_glyph(self):
        with self.assertRaises(bot.BotError):
            bot.read_number(self.rendered('509'), self.digit_templates('0123456785'.replace('9', '')))

    def test_read_loot_uses_digit_templates_without_tesseract(self):
        folder = self.root / 'digits'
        folder.mkdir()
        for digit, glyph in self.digit_templates().items():
            cv2.imwrite(str(folder / f'{digit}.png'), (glyph * 255).astype(np.uint8))
        image = np.zeros((90, 160, 3), dtype=np.uint8)
        image[20:50, 2:110] = self.rendered('123 456')[:, :108]
        loot = {'gold': [2, 20, 108, 30], 'elixir': [2, 20, 108, 30], 'dark': [2, 20, 108, 30]}
        config = {**self.config, 'tesseract': '', 'digits': 'digits'}
        bot.validate_config(config, self.root, 'both')
        runner = bot.Bot(config, self.root, Device(self.frames))
        self.assertEqual(runner.read_loot(image, {'loot': loot}), {'gold': 123456, 'elixir': 123456})
        with self.assertRaises(bot.BotError):
            bot.validate_config({**config, 'digits': 'missing'}, self.root, 'both')

    def test_learn_digits_builds_templates_from_labelled_screens(self):
        import calibrate
        frame = np.zeros((90, 160, 3), dtype=np.uint8)
        frame[0:30, 0:150] = self.rendered('1234567890')[:, :150]
        loot = {'gold': [0, 0, 150, 30], 'elixir': [0, 0, 150, 30]}
        calibrate.learn_digits([(frame, '1234567890', '1234567890')], loot, self.root / 'digits')
        templates = bot.load_digit_templates(self.root, 'digits')
        self.assertEqual(bot.read_number(frame[0:30, 0:150], templates), 1234567890)
        with self.assertRaises(bot.BotError):
            calibrate.learn_digits([(frame, '12', '12')], loot, self.root / 'other')

    def test_ocr_rejects_low_or_nonfinite_confidence(self):
        for confidence in ('49', 'nan', 'inf', '-1'):
            tsv = f'level\tconf\ttext\n5\t{confidence}\t123456\n'.encode()
            with self.subTest(confidence=confidence), patch('bot.subprocess.run') as run:
                run.return_value.stdout = tsv
                with self.assertRaises(bot.BotError):
                    bot.ocr_resource(self.digits_image(), 'tesseract.exe')

    def test_ocr_accepts_high_confidence_grouped_number(self):
        with patch('bot.subprocess.run') as run:
            run.return_value.stdout = b'level\tconf\ttext\n5\t96\t123\n5\t75\t456\n'
            self.assertEqual(bot.ocr_resource(self.digits_image(), 'tesseract.exe'), 123456)
            self.assertEqual(run.call_count, 3)

    def test_ocr_rejects_disagreeing_passes(self):
        first = type('R', (), {'stdout': b'level\tconf\ttext\n5\t96\t123456\n'})()
        second = type('R', (), {'stdout': b'level\tconf\ttext\n5\t96\t128456\n'})()
        with patch('bot.subprocess.run', side_effect=[first, second, first]):
            with self.assertRaises(bot.BotError):
                bot.ocr_resource(self.digits_image(), 'tesseract.exe')

    def test_ocr_without_bright_digits_stops_before_tesseract(self):
        with patch('bot.subprocess.run') as run:
            with self.assertRaises(bot.BotError):
                bot.ocr_resource(np.zeros((15, 50, 3), dtype=np.uint8), 'tesseract.exe')
            run.assert_not_called()

    def test_duplicate_anchor_is_not_two_independent_checks(self):
        config = copy.deepcopy(self.config)
        config['screens']['home']['anchors'][1] = config['screens']['home']['anchors'][0]
        with self.assertRaises(bot.BotError):
            bot.validate_config(config, self.root, 'both')

    def test_overriding_screen_wins_when_both_match(self):
        config = copy.deepcopy(self.config)
        frame = self.frames['builder_battle2'].copy()
        config['screens']['builder_stage2']['anchors'] = copy.deepcopy(config['screens']['builder_battle2']['anchors'])
        config['screens']['builder_stage2']['anchors'][1] = {'roi': [60, 60, 15, 12], 'file': 'card7.png'}
        cv2.imwrite(str(self.root / 'card7.png'), frame[60:72, 60:75])
        with self.assertRaises(bot.BotError):
            bot.Detector(config, self.root).detect(frame)
        config['screens']['builder_stage2']['overrides'] = ['builder_battle2']
        bot.validate_config(config, self.root, 'both')
        self.assertEqual(bot.Detector(config, self.root).detect(frame), 'builder_stage2')
        config['screens']['builder_stage2']['overrides'] = ['nope']
        with self.assertRaises(bot.BotError):
            bot.validate_config(config, self.root, 'both')

    def test_alternative_anchor_set_matches_other_layout(self):
        loss = np.random.default_rng(41).integers(0, 256, (90, 160, 3), dtype=np.uint8)
        alt = []
        for number, roi in enumerate(([20, 30, 15, 12], [100, 60, 15, 12])):
            x, y, w, h = roi
            cv2.imwrite(str(self.root / f'loss-{number}.png'), loss[y:y+h, x:x+w])
            alt.append({'roi': roi, 'file': f'loss-{number}.png'})
        screens = {**self.config['screens'],
                   'home_result': {**self.config['screens']['home_result'], 'alt_anchors': [alt]}}
        config = {**self.config, 'screens': screens}
        bot.validate_config(config, self.root, 'both')
        detector = bot.Detector(config, self.root)
        self.assertEqual(detector.detect(loss), 'home_result')
        self.assertEqual(detector.detect(self.frames['home_result']), 'home_result')
        self.assertEqual(detector.detect(self.frames['home']), 'home')

    def test_anchor_margin_tolerates_shifted_ui(self):
        shifted = self.frames['home'].copy()
        shifted[5:17, 2:17] = self.frames['home'][5:17, 5:20]
        with self.assertRaises(bot.BotError):
            bot.Detector(self.config, self.root).detect(shifted)
        anchors = [{**self.config['screens']['home']['anchors'][0], 'margin': 4},
                   self.config['screens']['home']['anchors'][1]]
        home = {**self.config['screens']['home'], 'anchors': anchors}
        config = {**self.config, 'screens': {**self.config['screens'], 'home': home}}
        bot.validate_config(config, self.root, 'both')
        self.assertEqual(bot.Detector(config, self.root).detect(shifted), 'home')
        bad = {**home, 'anchors': [{**anchors[0], 'margin': 999}, anchors[1]]}
        with self.assertRaises(bot.BotError):
            bot.validate_config({**config, 'screens': {**config['screens'], 'home': bad}}, self.root, 'both')

    def test_dimmed_popup_background_does_not_match_normal_screen(self):
        dimmed = (self.frames['home'].astype(float) * 0.75).astype(np.uint8)
        with self.assertRaises(bot.BotError):
            bot.Detector(self.config, self.root).detect(dimmed)

    def test_early_result_during_deploy_stops_after_one_burst(self):
        device = Device(self.frames, 'home_scout')
        device.deploys = 1
        runner = self.make_bot(device, True)
        scout = {**self.config['screens']['home_scout'], 'troops': [{'slot': [20, 80], 'count': 1}] * 2}
        runner.config = {**self.config, 'screens': {**self.config['screens'], 'home_scout': scout}}
        self.assertEqual(runner.deploy('home_scout', {'home_scout', 'home_battle'}), 'home_result')
        self.assertLessEqual(device.inputs.count((40, 40)), bot.DEPLOY_BURST)

    def test_each_card_is_one_burst_without_per_tap_screenshots(self):
        scout = {**self.config['screens']['builder_scout'],
                 'troops': [{'slot': [20, 80], 'count': 1, 'deploy': [30, 50]},
                            {'slot': [35, 80], 'count': 1}]}
        config = {**self.config, 'screens': {**self.config['screens'], 'builder_scout': scout}}
        device = Device(self.frames, 'builder_scout')
        runner = bot.Bot(config, self.root, device, live=True, timeout=0.02, poll=0)
        shots = []
        original = device.capture
        device.capture = lambda: shots.append(1) or original()
        runner.deploy('builder_scout', {'builder_scout', 'builder_battle'})
        self.assertEqual(device.bursts, [[(20, 80), (30, 50)], [(35, 80), (40, 40)]])
        self.assertEqual(len(shots), 3)

    def test_hold_deploy_presses_until_card_grey(self):
        frame = self.frames['home_scout']
        grey = frame.copy()
        grey[22:90, 0:60] = cv2.cvtColor(cv2.cvtColor(grey[22:90, 0:60], cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        scout = {**self.config['screens']['home_scout'],
                 'troops': [{'slot': [20, 60], 'count': 3, 'hold': 1.2, 'until_empty': True}]}
        config = {**self.config, 'screens': {**self.config['screens'], 'home_scout': scout}}
        bot.validate_config(config, self.root, 'both')
        device = Device(self.frames, 'home_scout')
        holds = lambda: sum(1 for i in device.inputs if i[0] == 'hold')
        device.capture = lambda: (grey if holds() >= 2 else frame).copy()
        bot.Bot(config, self.root, device, live=True, timeout=0.02, poll=0).deploy('home_scout', {'home_scout'})
        self.assertEqual(device.inputs, [('hold', (20, 60), (40, 40), 1.2), ('hold', None, (40, 40), 1.2)])
        bad = {**scout, 'troops': [{'slot': [20, 60], 'count': 1, 'hold': 99}]}
        with self.assertRaises(bot.BotError):
            bot.validate_config({**config, 'screens': {**config['screens'], 'home_scout': bad}}, self.root, 'both')

    def test_hold_keeps_finger_down_until_card_grey(self):
        frame = self.frames['home_scout']
        grey = frame.copy()
        grey[22:90, 0:60] = cv2.cvtColor(cv2.cvtColor(grey[22:90, 0:60], cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        scout = {**self.config['screens']['home_scout'],
                 'troops': [{'slot': [20, 60], 'count': 1, 'hold': 5, 'until_empty': True}]}
        config = {**self.config, 'screens': {**self.config['screens'], 'home_scout': scout}}
        device = Device(self.frames, 'home_scout')
        device.touch_down = lambda p: device.inputs.append(('down', tuple(p)))
        device.touch_up = lambda: device.inputs.append(('up',))
        device.tap_many = lambda pts: device.inputs.extend(tuple(p) for p in pts)
        shots = []
        device.capture = lambda: shots.append(1) or (grey if len(shots) >= 3 else frame).copy()
        bot.Bot(config, self.root, device, live=True, timeout=0.02, poll=0).deploy('home_scout', {'home_scout'})
        self.assertEqual(device.inputs, [(20, 60), ('down', (40, 40)), ('up',)])
        broken = Device({'home_scout': frame}, 'home_scout')
        broken.touch_down = lambda p: broken.inputs.append(('down', tuple(p)))
        broken.touch_up = lambda: broken.inputs.append(('up',))
        broken.tap_many = lambda pts: None
        calls = []
        broken.capture = lambda: calls.append(1) or (frame if len(calls) < 2 else np.zeros_like(frame))
        with self.assertRaises(bot.BotError):
            bot.Bot(config, self.root, broken, live=True, timeout=0.02, poll=0).deploy('home_scout', {'home_scout'})
        self.assertEqual(broken.inputs[-1], ('up',))

    def test_deploy_tolerates_one_transient_unknown_frame(self):
        frame = self.frames['home_scout']
        scout = {**self.config['screens']['home_scout'], 'troops': [{'slot': [20, 60], 'count': 1}]}
        config = {**self.config, 'screens': {**self.config['screens'], 'home_scout': scout}}
        device = Device(self.frames, 'home_scout')
        device.tap = lambda point: device.inputs.append(tuple(point))
        shots = iter([frame, np.zeros_like(frame), frame])
        device.capture = lambda: next(shots).copy()
        bot.Bot(config, self.root, device, live=True, timeout=0.02, poll=0).deploy('home_scout', {'home_scout'})
        self.assertEqual(device.inputs, [(20, 60), (40, 40)])

    def test_storage_fill_reads_bar_from_right(self):
        image = np.zeros((90, 160, 3), dtype=np.uint8)
        image[40:46, 60:121] = (0, 215, 255)
        bar = {'row': 43, 'left': 21, 'right': 120, 'hue': [15, 35], 'min_saturation': 150}
        self.assertAlmostEqual(bot.storage_fill(image, bar), 0.61, places=2)
        image[40:46, 21:121] = (0, 215, 255)
        image[40:46, 50:53] = 0
        self.assertGreaterEqual(bot.storage_fill(image, bar), bot.FULL_RATIO)

    def test_storage_fill_from_left_for_cart_bar(self):
        image = np.zeros((90, 160, 3), dtype=np.uint8)
        image[40:46, 21:121] = (40, 90, 130)
        image[40:46, 21:61] = (230, 60, 200)
        bar = {'row': 43, 'left': 21, 'right': 120, 'hue': [125, 165], 'min_saturation': 100, 'from': 'left'}
        self.assertAlmostEqual(bot.storage_fill(image, bar), 0.40, places=2)

    def test_until_full_attacks_unfilled_village_then_switches_and_stops(self):
        runner = self.make_bot(Device(self.frames), True)
        runner.goals = {'builder': {'gold', 'elixir'}, 'home': {'gold', 'elixir'}}
        fills = {'builder': iter([False, False, True]), 'home': iter([False, True])}
        calls = []
        runner.navigate = lambda village: calls.append(('go', village)) or setattr(runner, 'here', village)
        runner.observe = lambda: (runner.here, None)
        runner.village_done = lambda village, image: next(fills[village])
        runner.home_attack = lambda searches: calls.append(('attack', 'home'))
        runner.builder_attack = lambda collect=True: calls.append(('attack', 'builder'))
        runner.here = 'home'
        self.assertEqual(runner.run_until_full(0), 3)
        attacks = [c[1] for c in calls if c[0] == 'attack']
        self.assertEqual(attacks, ['builder', 'builder', 'home'])
        self.assertEqual(calls[0], ('go', 'builder'))
        self.assertEqual(runner.progress, 3)

    def goal_config(self):
        bar = {'row': 1, 'left': 0, 'right': 10, 'hue': [0, 10], 'min_saturation': 1, 'from': 'left'}
        return {**self.config, 'cart_bar': bar,
                'storage': {'home': {'gold': bar, 'elixir': bar, 'dark': bar},
                            'builder': {'gold': bar, 'elixir': bar}}}

    def test_goals_default_to_both_villages_builder_first_without_dark(self):
        goals = bot.parse_goals(self.goal_config(), None, None, 'builder')
        self.assertEqual(list(goals), ['builder', 'home'])
        self.assertEqual(goals['builder'], {'gold', 'elixir', 'cart'})
        self.assertEqual(goals['home'], {'gold', 'elixir'})

    def test_goals_pick_resources_village_and_order(self):
        config = self.goal_config()
        self.assertEqual(bot.parse_goals(config, 'gold', None, 'builder'), {'builder': {'gold'}})
        self.assertEqual(bot.parse_goals(config, None, 'elixir,dark', 'builder'), {'home': {'elixir', 'dark'}})
        both = bot.parse_goals(config, 'all', 'all', 'home')
        self.assertEqual(list(both), ['home', 'builder'])
        self.assertEqual(both['home'], {'gold', 'elixir', 'dark'})
        self.assertEqual(both['builder'], {'gold', 'elixir', 'cart'})

    def test_goals_reject_unknown_and_uncalibrated_resources(self):
        config = self.goal_config()
        for builder, home in (('dark', None), (None, 'cart'), ('', None), ('gold,,elixir', None)):
            with self.assertRaises(bot.BotError):
                bot.parse_goals(config, builder, home, 'builder')
        without_dark = {**config, 'storage': {**config['storage'], 'home': {'gold': config['cart_bar']}}}
        with self.assertRaises(bot.BotError):
            bot.parse_goals(without_dark, None, 'dark', 'builder')

    def test_village_done_checks_only_the_chosen_resources(self):
        runner = self.make_bot(Device(self.frames), True)
        asked = []
        runner.village_full = lambda village, image, keys: asked.append(set(keys)) or True
        runner.cart_full = False
        runner.goals = {'builder': {'cart'}}
        self.assertFalse(runner.village_done('builder', None))
        self.assertEqual(asked, [])
        runner.cart_full = True
        self.assertTrue(runner.village_done('builder', None))
        runner.goals = {'builder': {'gold'}, 'home': {'elixir', 'dark'}}
        runner.cart_full = False
        self.assertTrue(runner.village_done('builder', None))
        self.assertTrue(runner.village_done('home', None))
        self.assertEqual(asked, [{'gold'}, {'elixir', 'dark'}])

    def test_until_full_farms_only_the_chosen_village(self):
        runner = self.make_bot(Device(self.frames), True)
        runner.goals = {'home': {'gold'}}
        fills = iter([False, False, True])
        calls = []
        runner.navigate = lambda village: calls.append(('go', village)) or setattr(runner, 'here', village)
        runner.observe = lambda: (runner.here, None)
        runner.village_done = lambda village, image: next(fills)
        runner.home_attack = lambda searches: calls.append(('attack', 'home'))
        runner.builder_attack = lambda collect=True: calls.append(('attack', 'builder'))
        runner.collect_cart = lambda: calls.append(('cart',))
        runner.here = 'builder'
        self.assertEqual(runner.run_until_full(0), 2)
        self.assertEqual({c[1] for c in calls if c[0] in ('go', 'attack')}, {'home'})
        self.assertNotIn(('cart',), calls)

    def test_until_full_follows_the_chosen_village_order(self):
        runner = self.make_bot(Device(self.frames), True)
        runner.goals = {'home': {'gold'}, 'builder': {'gold'}}
        fills = {'home': iter([False, True]), 'builder': iter([True])}
        calls = []
        runner.navigate = lambda village: calls.append(('go', village)) or setattr(runner, 'here', village)
        runner.observe = lambda: (runner.here, None)
        runner.village_done = lambda village, image: next(fills[village])
        runner.home_attack = lambda searches: calls.append(('attack', 'home'))
        runner.builder_attack = lambda collect=True: calls.append(('attack', 'builder'))
        runner.collect_cart = lambda: None
        runner.here = 'builder'
        self.assertEqual(runner.run_until_full(0), 1)
        self.assertEqual([c[1] for c in calls if c[0] == 'go'], ['home', 'home', 'builder'])

    def wall_spec(self):
        rng = np.random.default_rng(31)
        icons = {}
        for name in ('gold', 'elixir'):
            icon = rng.integers(0, 256, (10, 10, 3), dtype=np.uint8)
            cv2.imwrite(str(self.root / f'wall-{name}.png'), icon)
            icons[name] = icon
        row = rng.integers(0, 256, (6, 20, 3), dtype=np.uint8)
        cv2.imwrite(str(self.root / 'wall-row.png'), row)
        cv2.imwrite(str(self.root / 'hammer.png'), rng.integers(0, 256, (8, 12, 3), dtype=np.uint8))
        spec = {'build': [150, 80], 'row': {'file': 'wall-row.png', 'region': [100, 0, 60, 90],
                                           'max_score': 0.05, 'tap_x': 130},
                'scroll': [[130, 70], [130, 20]], 'min_white': 100,
                'gems': {'list': [0, 0, 30, 10], 'village': [0, 12, 30, 10]},
                'button': {'file': 'hammer.png', 'search': [0, 40, 160, 50], 'scales': [1.0], 'max_score': 0.02,
                           'price': [-10, -12, 30, 8], 'tap': [6, 4], 'icon_min': 8},
                'confirm': {'screen': 'wall_confirm', 'price': [10, 30, 30, 8], 'icon': [45, 25, 20, 20],
                            'min_white': 40, 'max_score': 0.05,
                            'icons': {'gold': 'wall-gold.png', 'elixir': 'wall-elixir.png'}}}
        return spec, icons, row

    def place_button(self, image, x, y, resource, white):
        image[y:y + 8, x:x + 12] = cv2.imread(str(self.root / 'hammer.png'))
        image[y - 12:y - 4, x - 10:x + 20] = (255, 255, 255) if white else (40, 40, 230)
        image[y - 12:y - 8, x + 14:x + 18] = (0, 200, 255) if resource == 'gold' else (200, 40, 180)

    def priced(self, spec, white, shown=('gold', 'elixir')):
        image = np.zeros((90, 160, 3), dtype=np.uint8)
        for resource, x in (('gold', 20), ('elixir', 60)):
            if resource in shown:
                self.place_button(image, x, 70, resource, resource in white)
        return image

    def confirm_frame(self, spec, icon, white=True):
        image = np.zeros((90, 160, 3), dtype=np.uint8)
        x, y, w, h = spec['confirm']['price']
        image[y:y + h, x:x + w] = (255, 255, 255) if white else (40, 40, 230)
        image[30:40, 50:60] = icon
        return image

    def wall_runner(self, frames, gems, tap_frames=None):
        runner = self.make_bot(Device(self.frames), True)
        runner.gems_known = 500
        taps = []
        shots = iter(frames)
        counts = iter(gems)
        seen = []
        runner.observe = lambda: seen.append(next(shots)) or seen[-1]
        runner.read_gems = lambda image, spec: next(counts)
        later = iter(tap_frames or [])

        def tap(states, location, terminal=None):
            if callable(location):
                location = location(next(later, seen[-1][1]))
            taps.append(tuple(location))
            return 'ok'
        runner.guarded_tap = tap
        runner.action = lambda state, name: taps.append((state, name))
        runner.wait = lambda targets, allowed, timeout=None: sorted(targets)[0]
        runner.wait_still = lambda limit: None
        return runner, taps

    def test_buttons_are_told_apart_by_their_price_icon_and_colour(self):
        spec, _, _ = self.wall_spec()
        runner = self.make_bot(Device(self.frames), True)
        image = self.priced(spec, {'elixir'})
        self.assertEqual(sorted(runner.wall_buttons(image, spec)),
                         [('elixir', [66, 74], True), ('gold', [26, 74], False)])
        self.assertEqual(runner.affordable_button(image, spec), ('elixir', [66, 74]))
        self.assertIsNone(runner.affordable_button(self.priced(spec, set()), spec))

    def test_buttons_are_found_wherever_the_picked_wall_puts_them(self):
        spec, icons, _ = self.wall_spec()
        frame = np.zeros((90, 160, 3), dtype=np.uint8)
        self.place_button(frame, 100, 55, 'gold', True)
        frames = [('wall_selected', frame), ('wall_confirm', self.confirm_frame(spec, icons['gold'])),
                  ('wall_selected', frame)]
        runner, taps = self.wall_runner(frames, [500, 500])
        self.assertTrue(runner.upgrade_wall(spec))
        self.assertEqual(taps, [(106, 59), ('wall_confirm', 'confirm')])

    def test_the_upgrade_button_is_found_again_in_the_frame_it_is_tapped_on(self):
        spec, icons, _ = self.wall_spec()
        closed = np.zeros((90, 160, 3), dtype=np.uint8)
        self.place_button(closed, 100, 55, 'gold', True)
        frames = [('wall_selected', self.priced(spec, {'gold'})),
                  ('wall_confirm', self.confirm_frame(spec, icons['gold'])), ('wall_selected', closed)]
        runner, taps = self.wall_runner(frames, [500, 500], tap_frames=[closed])
        self.assertTrue(runner.upgrade_wall(spec))
        self.assertEqual(taps, [(106, 59), ('wall_confirm', 'confirm')])
        frames = [('wall_selected', self.priced(spec, {'gold'}))]
        runner, taps = self.wall_runner(frames, [500], tap_frames=[self.priced(spec, set())])
        self.assertFalse(runner.upgrade_wall(spec))
        self.assertEqual(taps, [])

    def test_menus_close_layer_by_layer_even_when_the_wall_stays_selected(self):
        runner = self.make_bot(Device(self.frames), True)
        listed, bare = np.ones((2, 2, 3), np.uint8), np.zeros((2, 2, 3), np.uint8)
        shots = iter([('wall_confirm', listed), ('wall_selected', listed), ('wall_selected', bare),
                      ('build_list', listed), ('home', bare)])
        actions = []
        runner.observe = lambda: next(shots)
        runner.list_open = lambda image: bool(image.any())
        runner.action = lambda state, name: actions.append((state, name))
        runner.read_until = lambda reader, limit: None
        runner.wait_still = lambda limit: None
        runner.close_menus()
        self.assertEqual(actions, [('wall_confirm', 'close'), ('wall_selected', 'close'),
                                   ('wall_selected', 'build'), ('build_list', 'close')])

    def test_gem_count_is_read_again_while_the_bars_slide(self):
        spec, _, _ = self.wall_spec()
        runner = bot.Bot(self.config, self.root, Device(self.frames), live=True, timeout=1, poll=0.01)
        reads = iter([bot.BotError('Angka loot meragukan'), 101, 100, 100])

        def read(image, spec):
            value = next(reads)
            if isinstance(value, Exception):
                raise value
            return value
        runner.read_gems = read
        self.assertEqual(runner.stable_gems(spec), 100)

    def test_gems_are_read_where_the_hud_sits(self):
        spec, _, _ = self.wall_spec()
        runner = self.make_bot(Device(self.frames), True)
        runner.list_open = lambda image: True
        self.assertEqual(runner.gem_region(None, spec), spec['gems']['list'])
        runner.list_open = lambda image: False
        self.assertEqual(runner.gem_region(None, spec), spec['gems']['village'])

    def test_wall_rows_are_found_top_first(self):
        spec, _, row = self.wall_spec()
        image = np.zeros((90, 160, 3), dtype=np.uint8)
        image[50:56, 110:130] = row
        image[12:18, 110:130] = row
        self.assertEqual(bot.find_rows(image, row, spec['row']['region'], 0.05), [15, 53])
        self.assertEqual(bot.find_rows(np.zeros_like(image), row, spec['row']['region'], 0.05), [])

    def test_wall_is_not_touched_when_no_price_is_affordable(self):
        spec, _, _ = self.wall_spec()
        runner, taps = self.wall_runner([('wall_selected', self.priced(spec, set()))], [500])
        self.assertFalse(runner.upgrade_wall(spec))
        self.assertEqual(taps, [])

    def test_a_button_missing_from_the_layout_is_never_tapped(self):
        spec, _, _ = self.wall_spec()
        frame = self.priced(spec, {'gold', 'elixir'}, shown=())
        runner, taps = self.wall_runner([('wall_selected', frame)], [500])
        self.assertFalse(runner.upgrade_wall(spec))
        self.assertEqual(taps, [])

    def test_wall_is_upgraded_with_an_affordable_resource_after_a_safe_confirm(self):
        spec, icons, _ = self.wall_spec()
        frames = [('wall_selected', self.priced(spec, {'elixir'})),
                  ('wall_confirm', self.confirm_frame(spec, icons['elixir'])),
                  ('wall_selected', self.priced(spec, {'elixir'}))]
        runner, taps = self.wall_runner(frames, [500, 500])
        self.assertTrue(runner.upgrade_wall(spec))
        self.assertEqual(taps, [(66, 74), ('wall_confirm', 'confirm')])
        self.assertEqual(runner.progress, 1)

    def test_confirm_with_red_price_or_wrong_icon_is_closed_not_confirmed(self):
        spec, icons, _ = self.wall_spec()
        for confirm in (self.confirm_frame(spec, icons['elixir'], white=False),
                        self.confirm_frame(spec, icons['gold'])):
            frames = [('wall_selected', self.priced(spec, {'elixir'})), ('wall_confirm', confirm)]
            runner, taps = self.wall_runner(frames, [500])
            self.assertFalse(runner.upgrade_wall(spec))
            self.assertEqual(taps, [(66, 74), ('wall_confirm', 'close')])

    def test_gem_drop_stops_wall_upgrades_for_good(self):
        spec, icons, _ = self.wall_spec()
        runner, taps = self.wall_runner([('wall_selected', self.priced(spec, {'gold'}))], [499])
        with self.assertRaises(bot.GemSpent):
            runner.upgrade_wall(spec)
        self.assertEqual(taps, [])
        frames = [('wall_selected', self.priced(spec, {'gold'})),
                  ('wall_confirm', self.confirm_frame(spec, icons['gold'])),
                  ('wall_selected', self.priced(spec, {'gold'}))]
        runner, taps = self.wall_runner(frames, [500, 498])
        with self.assertRaises(bot.GemSpent):
            runner.upgrade_wall(spec)
        device = Device(self.frames)
        device.connected = device.game_in_front = lambda: True
        runner = self.make_bot(device, True)
        runner.settle = runner.recover = lambda: None
        attempts = []
        runner.run_walls = lambda searches: (attempts.append(1), (_ for _ in ()).throw(bot.GemSpent('gem')))[1]
        with self.assertRaises(bot.GemSpent):
            runner.run_until_full_resilient(0, max_recoveries=3, job=runner.run_walls)
        self.assertEqual(len(attempts), 1)

    def test_a_fresh_gem_count_is_not_read_again_before_the_next_tap(self):
        spec, icons, _ = self.wall_spec()
        frames = [('wall_selected', self.priced(spec, {'elixir'})),
                  ('wall_confirm', self.confirm_frame(spec, icons['elixir'])),
                  ('wall_selected', self.priced(spec, {'elixir'}))]
        runner, taps = self.wall_runner(frames, [500])
        runner.gems_fresh = True
        self.assertTrue(runner.upgrade_wall(spec))
        self.assertEqual(taps, [(66, 74), ('wall_confirm', 'confirm')])
        self.assertTrue(runner.gems_fresh)

    def test_the_wall_is_upgraded_once_its_button_layout_holds(self):
        runner = bot.Bot(self.config, self.root, Device(self.frames), live=True, timeout=1, poll=0.01)
        layouts = iter([True, False, False, False])
        captures = []
        runner.device.capture = lambda: captures.append(1) or np.zeros((2, 2, 3), np.uint8)
        runner.detector.detect = lambda image: 'wall_selected'
        runner.list_open = lambda image: next(layouts)
        with patch('bot.WALL_LAYOUT_HOLD_SECONDS', 0):
            runner.settle_selection()
        self.assertEqual(len(captures), 3)

    def test_gold_below_the_search_reserve_is_never_spent_on_walls(self):
        spec, icons, _ = self.wall_spec()
        spec['gold_reserve'] = 200_000
        both = self.priced(spec, {'gold', 'elixir'})
        frames = [('wall_selected', both), ('wall_confirm', self.confirm_frame(spec, icons['gold'])),
                  ('wall_selected', both), ('wall_confirm', self.confirm_frame(spec, icons['elixir']))]
        runner, taps = self.wall_runner(frames, [500, 500, 500])
        runner.wall_excluded = set()
        runner.hud_gold = lambda spec: 2_100_000
        runner.confirm_price = lambda image, spec: 2_000_000
        self.assertTrue(runner.upgrade_wall(spec))
        self.assertEqual(taps, [(26, 74), ('wall_confirm', 'close'), (66, 74), ('wall_confirm', 'confirm')])
        self.assertEqual(runner.wall_excluded, {'gold'})

    def test_a_gem_offer_is_closed_and_never_accepted(self):
        offer = np.random.default_rng(29).integers(0, 256, (90, 160, 3), dtype=np.uint8)
        anchors = []
        for number, roi in enumerate(([5, 5, 15, 12], [130, 5, 15, 12])):
            x, y, w, h = roi
            cv2.imwrite(str(self.root / f'offer-{number}.png'), offer[y:y+h, x:x+w])
            anchors.append({'roi': roi, 'file': f'offer-{number}.png'})
        config = {**self.config, 'screens': {**self.config['screens'],
                                             'gem_offer': {'anchors': anchors, 'actions': {'close': [150, 8]}}}}
        bot.validate_config(config, self.root, 'both')
        device = Device({**self.frames, 'gem_offer': offer}, 'gem_offer')
        device.tap = lambda point: (device.inputs.append(tuple(point)), setattr(device, 'state', 'home_menu'))
        runner = bot.Bot(config, self.root, device, live=True, timeout=1, poll=0)
        with self.assertRaises(bot.WaitTimeout):
            runner.wait({'home_army'}, {'home_menu'}, 1)
        self.assertEqual(device.inputs, [(150, 8)])

    def test_a_gem_count_that_reads_higher_stops_without_upgrading(self):
        spec, _, _ = self.wall_spec()
        runner, taps = self.wall_runner([('wall_selected', self.priced(spec, {'gold'}))], [25001])
        runner.gems_known = 1073
        with self.assertRaises(bot.GemUnreadable):
            runner.upgrade_wall(spec)
        self.assertEqual(taps, [])

    def test_a_gem_count_with_missing_digits_is_read_again_not_an_emergency(self):
        spec, _, _ = self.wall_spec()
        runner, taps = self.wall_runner([('wall_selected', self.priced(spec, {'gold'}))], [73])
        runner.gems_known = 1073
        with self.assertRaises(bot.GemUnreadable):
            runner.upgrade_wall(spec)
        self.assertEqual(taps, [])
        self.assertTrue(issubclass(bot.GemUnreadable, bot.WaitTimeout))

    def test_each_wall_session_must_match_the_gem_count_on_record(self):
        spec, _, _ = self.wall_spec()
        runner = self.make_bot(Device(self.frames), True)
        reads = iter([1073, 1080, 73, 1070])
        runner.read_gems = lambda image, spec: next(reads)
        runner.start_gem_watch(spec)
        self.assertEqual(runner.gems_known, 1073)
        runner.start_gem_watch(spec)
        self.assertEqual(runner.gems_known, 1080)
        with self.assertRaises(bot.GemUnreadable):
            runner.start_gem_watch(spec)
        with self.assertRaises(bot.GemSpent):
            runner.start_gem_watch(spec)

    def test_the_lowest_wall_row_is_found_coming_up_from_the_end_of_the_list(self):
        spec, _, row = self.wall_spec()
        blank = np.zeros((90, 160, 3), dtype=np.uint8)
        other = blank.copy()
        other[40:60, 100:160] = 90
        walls = blank.copy()
        walls[12:18, 110:130] = row
        walls[50:56, 110:130] = row
        device = Device(self.frames)
        swipes = []
        device.swipe = lambda start, end: swipes.append((tuple(start), tuple(end)))
        runner = self.make_bot(device, True)
        shots = iter([blank, other, other, walls])
        runner.wait_still = lambda limit: next(shots)
        self.assertEqual(runner.wall_row(spec), 53)
        down, up = ((130, 70), (130, 20)), ((130, 20), (130, 70))
        self.assertEqual(swipes, [down, down, up])
        self.assertTrue(runner.wall_view_known)

    def test_the_next_pick_takes_the_lowest_row_in_view_without_scrolling(self):
        spec, _, row = self.wall_spec()
        walls = np.zeros((90, 160, 3), dtype=np.uint8)
        walls[12:18, 110:130] = row
        walls[50:56, 110:130] = row
        device = Device(self.frames)
        swipes = []
        device.swipe = lambda start, end: swipes.append((tuple(start), tuple(end)))
        device.capture = lambda: walls
        runner = self.make_bot(device, True)
        runner.wall_view_known = True
        self.assertEqual(runner.wall_row(spec), 53)
        self.assertEqual(swipes, [])

    def test_no_wall_row_anywhere_in_the_list_means_every_wall_is_maxed(self):
        spec, _, _ = self.wall_spec()
        blank = np.zeros((90, 160, 3), dtype=np.uint8)
        device = Device(self.frames)
        swipes = []
        device.swipe = lambda start, end: swipes.append((tuple(start), tuple(end)))
        runner = self.make_bot(device, True)
        shots = iter([blank] * 3)
        runner.wait_still = lambda limit: next(shots)
        self.assertIsNone(runner.wall_row(spec))
        self.assertFalse(runner.wall_view_known)

    def test_walls_farm_then_upgrade_until_maxed_in_the_chosen_order(self):
        runner = self.make_bot(Device(self.frames), True)
        runner.wall_order = ('home', 'builder')
        calls = []
        rounds = {'home': iter([3, 2, None]), 'builder': iter([0, None])}
        runner.run_until_full = lambda searches: calls.append(('farm', dict(runner.goals)))
        runner.upgrade_walls = lambda village: calls.append(('walls', village)) or next(rounds[village])
        runner.run_walls(0)
        self.assertEqual(calls, [('walls', 'home'), ('farm', {'home': {'gold', 'elixir'}}),
                                 ('walls', 'home'), ('farm', {'home': {'gold', 'elixir'}}), ('walls', 'home'),
                                 ('walls', 'builder'), ('farm', {'builder': {'gold', 'elixir'}}),
                                 ('walls', 'builder')])

    def test_walls_stop_when_full_storage_upgrades_nothing(self):
        runner = self.make_bot(Device(self.frames), True)
        runner.wall_order = ('builder',)
        runner.run_until_full = lambda searches: None
        runner.upgrade_walls = lambda village: 0
        with self.assertRaises(bot.BotError):
            runner.run_walls(0)

    def test_wall_order_and_calibration_are_checked(self):
        self.assertEqual(bot.wall_order('both', 'home'), ('home', 'builder'))
        self.assertEqual(bot.wall_order('builder', 'home'), ('builder',))
        spec, _, _ = self.wall_spec()
        config = {**self.config, 'walls': {'home': spec}}
        with self.assertRaises(bot.BotError):
            bot.validate_walls(config, self.root, ('home',))
        anchors = self.config['screens']['home']['anchors']
        screens = {name: {'anchors': anchors, 'actions': {'close': [10, 10], 'confirm': [20, 20]}}
                   for name in bot.MENU_SCREENS}
        config = {**config, 'screens': {**self.config['screens'], **screens}}
        bot.validate_walls(config, self.root, ('home',))
        with self.assertRaises(bot.BotError):
            bot.validate_walls(config, self.root, ('builder',))

    def test_unreadable_loot_is_read_again_then_the_base_is_skipped(self):
        device = Device(self.frames, loot=[{'gold': 100, 'elixir': 200}])
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=0.02, poll=0.001)
        reads = iter([bot.BotError('Angka loot meragukan'), {'gold': 100, 'elixir': 200}])

        def reader(image, screen):
            value = next(reads)
            if isinstance(value, Exception):
                raise value
            return value
        runner.reader = reader
        runner.wait_new_scout = lambda image: 'home_scout'
        runner.home_attack(0)
        self.assertIn((40, 40), device.inputs)
        device = Device(self.frames)
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=0.02, poll=0)
        reads = iter([bot.BotError('Angka loot meragukan'), {'gold': 100, 'elixir': 200}])
        runner.reader = reader
        runner.wait_new_scout = lambda image: 'home_scout'
        runner.home_attack(0)
        self.assertEqual(device.inputs.count((140, 60)), 1)

    def test_out_of_gold_for_next_attacks_the_base_on_screen(self):
        device = Device(self.frames, loot=[{'gold': 1, 'elixir': 1}] * 3)
        runner = self.make_bot(device, True)
        closed = []
        runner.action = lambda state, name: closed.append((state, name)) if state == 'gem_offer' else \
            bot.Bot.action(runner, state, name)

        def no_gold(image):
            runner.action('gem_offer', 'close')
            raise bot.OutOfGold('Gold habis')
        runner.wait_new_scout = no_gold
        runner.home_attack(0)
        self.assertEqual(closed, [('gem_offer', 'close')])
        self.assertEqual(device.inputs.count((140, 60)), 1)
        self.assertIn((40, 40), device.inputs)

    def test_unlimited_searches_keep_skipping_until_loot_passes(self):
        lows = [{'gold': 1, 'elixir': 1}] * 25
        device = Device(self.frames, loot=lows + [{'gold': 100, 'elixir': 200}])
        runner = self.make_bot(device, True)
        runner.wait_new_scout = lambda image: 'home_scout'
        runner.home_attack(0)
        self.assertEqual(device.inputs.count((140, 60)), 25)
        self.assertIn((40, 40), device.inputs)

    def test_guarded_tap_has_no_fixed_sleep(self):
        device = Device(self.frames)
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0.3)
        with patch('bot.time.sleep') as sleep:
            runner.guarded_tap({'home'}, [10, 75])
        sleep.assert_not_called()
        self.assertEqual(device.bursts, [[(10, 75)]])

    def test_wait_still_returns_once_two_frames_match(self):
        moving = [self.frames['home'], self.frames['builder'], self.frames['home'], self.frames['home']]
        shots = iter(moving)
        device = Device(self.frames)
        device.capture = lambda: next(shots).copy()
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0.3)
        with patch('bot.time.sleep') as sleep:
            runner.wait_still(5)
        sleep.assert_not_called()
        self.assertIsNone(next(shots, None))

    def test_wait_still_gives_up_after_limit_without_error(self):
        frames = [self.frames['home'], self.frames['builder']]
        count = []
        device = Device(self.frames)
        device.capture = lambda: frames[len(count) % 2].copy() if not count.append(1) else None
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0)
        runner.wait_still(0.05)
        self.assertGreaterEqual(len(count), 2)

    def test_resilient_run_relaunches_closed_game_and_resumes(self):
        device = Device(self.frames)
        device.front = True
        device.launches = []
        device.game_in_front = lambda: device.front
        device.launch = lambda activity: (device.launches.append(activity), setattr(device, 'front', True))
        device.connected = lambda: True
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0)
        calls = []

        def flaky(searches):
            calls.append(searches)
            if len(calls) == 1:
                device.front = False
                raise bot.BotError('Layar tidak dikenal')
            return 3
        runner.run_until_full = flaky
        self.assertEqual(runner.run_until_full_resilient(0, max_recoveries=2), 3)
        self.assertEqual(len(calls), 2)
        self.assertEqual(device.launches, [bot.GAME_ACTIVITY])

    def test_resilient_run_starts_game_that_is_not_open(self):
        device = Device(self.frames)
        device.front = False
        device.launches = []
        device.game_in_front = lambda: device.front
        device.launch = lambda activity: (device.launches.append(activity), setattr(device, 'front', True))
        device.connected = lambda: True
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0)
        runner.run_until_full = lambda searches: 1
        self.assertEqual(runner.run_until_full_resilient(0, max_recoveries=2), 1)
        self.assertEqual(device.launches, [bot.GAME_ACTIVITY])

    def test_resilient_run_stops_on_real_errors_and_after_limit(self):
        device = Device(self.frames)
        device.game_in_front = lambda: True
        device.launch = lambda activity: None
        device.connected = lambda: True
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0)
        runner.run_until_full = lambda searches: (_ for _ in ()).throw(bot.BotError('Angka loot meragukan'))
        with self.assertRaises(bot.BotError):
            runner.run_until_full_resilient(0, max_recoveries=5)
        attempts = []
        runner.run_until_full = lambda searches: (attempts.append(1), (_ for _ in ()).throw(bot.DeviceError('ADB gagal')))[1]
        with self.assertRaises(bot.DeviceError):
            runner.run_until_full_resilient(0, max_recoveries=2)
        self.assertEqual(len(attempts), 3)

    def test_resilient_run_retries_after_out_of_memory_and_leaves_result_screen(self):
        device = Device(self.frames, initial='home_result')
        device.game_in_front = lambda: True
        device.launch = lambda activity: None
        device.connected = lambda: True
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0)
        calls = []

        def flaky(searches):
            calls.append(searches)
            if len(calls) == 1:
                raise cv2.error('OpenCV: (-4:Insufficient memory) Failed to allocate 22166424 bytes')
            return 4
        runner.run_until_full = flaky
        self.assertEqual(runner.run_until_full_resilient(0, max_recoveries=2), 4)
        self.assertEqual(len(calls), 2)
        self.assertIn((80, 70), device.inputs)
        self.assertEqual(device.state, 'home')

    def test_error_while_pc_memory_is_critical_is_recovered_like_out_of_memory(self):
        device = Device(self.frames)
        device.game_in_front = lambda: True
        device.connected = lambda: True
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0)
        calls = []

        def flaky(searches):
            calls.append(searches)
            if len(calls) == 1:
                raise bot.WaitTimeout('Timeout; tidak mencoba input pemulihan')
            return 8
        runner.run_until_full = flaky
        with patch('bot.available_memory_mb', return_value=bot.LOW_MEMORY_MB - 1):
            self.assertEqual(runner.run_until_full_resilient(0, max_recoveries=2), 8)
        self.assertEqual(len(calls), 2)

    def test_timeout_with_the_game_open_is_settled_and_retried(self):
        device = Device(self.frames)
        device.game_in_front = lambda: True
        device.connected = lambda: True
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0)
        calls = []

        def stalled_once(searches):
            calls.append(searches)
            if len(calls) == 1:
                raise bot.WaitTimeout('Timeout; tidak mencoba input pemulihan')
            return 4
        runner.run_until_full = stalled_once
        with patch('bot.available_memory_mb', return_value=bot.LOW_MEMORY_MB + 1):
            self.assertEqual(runner.run_until_full_resilient(0, max_recoveries=2), 4)
        self.assertEqual(len(calls), 2)
        attempts = []
        runner.run_until_full = lambda searches: (attempts.append(1), (_ for _ in ()).throw(bot.WaitTimeout('Timeout')))[1]
        with patch('bot.available_memory_mb', return_value=bot.LOW_MEMORY_MB + 1), \
                self.assertRaises(bot.WaitTimeout):
            runner.run_until_full_resilient(0, max_recoveries=2)
        self.assertEqual(len(attempts), 3)

    def test_recoveries_are_counted_again_after_the_run_makes_progress(self):
        device = Device(self.frames)
        device.game_in_front = lambda: True
        device.connected = lambda: True
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0)
        calls = []

        def stalls_after_attacks(searches):
            calls.append(searches)
            if len(calls) <= 5:
                runner.progress += 1
            raise bot.WaitTimeout('Timeout; tidak mencoba input pemulihan')
        runner.run_until_full = stalls_after_attacks
        with patch('bot.available_memory_mb', return_value=bot.LOW_MEMORY_MB + 1), \
                self.assertRaises(bot.WaitTimeout):
            runner.run_until_full_resilient(0, max_recoveries=2)
        # The first five stalls each follow an attack, so the count starts again; two more use up both recoveries.
        self.assertEqual(len(calls), 7)

    def test_unknown_screen_with_the_game_open_is_settled_and_retried(self):
        device = Device(self.frames)
        device.game_in_front = lambda: True
        device.connected = lambda: True
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0)
        calls = []

        def flashed_once(searches):
            calls.append(searches)
            if len(calls) == 1:
                raise bot.UnknownScreen('Layar tidak dikenal; tidak ada input dikirim')
            return 6
        runner.run_until_full = flashed_once
        with patch('bot.available_memory_mb', return_value=bot.LOW_MEMORY_MB + 1):
            self.assertEqual(runner.run_until_full_resilient(0, max_recoveries=2), 6)
        self.assertEqual(len(calls), 2)

    def test_other_errors_with_the_game_open_still_stop(self):
        device = Device(self.frames)
        device.game_in_front = lambda: True
        device.connected = lambda: True
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0)
        attempts = []
        runner.run_until_full = lambda searches: (attempts.append(1), (_ for _ in ()).throw(bot.BotError('Layar ambigu')))[1]
        with patch('bot.available_memory_mb', return_value=bot.LOW_MEMORY_MB + 1), \
                self.assertRaises(bot.BotError):
            runner.run_until_full_resilient(0, max_recoveries=2)
        self.assertEqual(len(attempts), 1)

    def test_resilient_run_reraises_other_opencv_errors_and_memory_after_limit(self):
        device = Device(self.frames)
        device.game_in_front = lambda: True
        device.connected = lambda: True
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0)
        runner.run_until_full = lambda searches: (_ for _ in ()).throw(cv2.error('bad template size'))
        with self.assertRaises(cv2.error):
            runner.run_until_full_resilient(0, max_recoveries=3)
        attempts = []
        runner.run_until_full = lambda searches: (attempts.append(1), (_ for _ in ()).throw(MemoryError()))[1]
        with self.assertRaises(MemoryError):
            runner.run_until_full_resilient(0, max_recoveries=1)
        self.assertEqual(len(attempts), 2)

    def restartable(self):
        device = Device(self.frames)
        device.up, device.front, device.launches = True, True, []
        device.connected = lambda: device.up
        device.booted = lambda: device.up
        device.game_in_front = lambda: device.front
        device.launch = lambda activity: (device.launches.append(activity), setattr(device, 'front', True))

        class Emulator:
            events = []
            def running(self):
                return device.up
            def quit(self):
                Emulator.events.append('quit')
                device.up, device.front = False, False
            def launch(self):
                Emulator.events.append('launch')
                device.up = True
        return device, Emulator()

    def test_low_pc_memory_restarts_emulator_between_attacks(self):
        device, emulator = self.restartable()
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0, emulator=emulator)
        with patch('bot.available_memory_mb', return_value=bot.LOW_MEMORY_MB - 1):
            self.assertTrue(runner.refresh_if_low_memory())
        self.assertEqual(emulator.events, ['quit', 'launch'])
        self.assertEqual(device.launches, [bot.GAME_ACTIVITY])
        with patch('bot.available_memory_mb', return_value=bot.LOW_MEMORY_MB - 1):
            self.assertFalse(runner.refresh_if_low_memory())
        self.assertEqual(emulator.events, ['quit', 'launch'])

    def test_resilient_run_started_on_a_result_screen_returns_to_the_village_first(self):
        device = Device(self.frames, initial='builder_result')
        device.game_in_front = lambda: True
        device.connected = lambda: True
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0)
        seen = []
        runner.run_until_full = lambda searches: seen.append(device.state) or 6
        self.assertEqual(runner.run_until_full_resilient(0, max_recoveries=1), 6)
        self.assertIn((80, 70), device.inputs)
        self.assertEqual(seen, ['builder'])

    def test_recover_quits_an_emulator_whose_adb_never_comes_back(self):
        device, emulator = self.restartable()
        device.up = False
        emulator.running = lambda: True
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0, emulator=emulator)
        with patch('bot.RECOVER_ADB_SECONDS', 0), self.assertRaises(bot.DeviceError):
            runner.recover()
        self.assertEqual(emulator.events, ['quit'])

    def test_resilient_run_started_on_an_attack_menu_closes_it_first(self):
        self.config['screens']['builder_menu']['actions']['close'] = [150, 20]
        device = Device(self.frames, initial='builder_menu')
        device.game_in_front = lambda: True
        device.connected = lambda: True
        real_tap = device.tap
        device.tap = lambda point: (setattr(device, 'state', 'builder') if point == [150, 20] and
                                    device.state == 'builder_menu' else real_tap(point))
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0)
        seen = []
        runner.run_until_full = lambda searches: seen.append(device.state) or 7
        self.assertEqual(runner.run_until_full_resilient(0, max_recoveries=1), 7)
        self.assertEqual(seen, ['builder'])

    def test_lost_tap_on_a_menu_is_repeated_until_the_screen_changes(self):
        device = Device(self.frames, initial='builder_menu')
        runner = self.make_bot(device, True)
        real_tap, lost = device.tap, []

        def flaky_tap(point):
            if device.state == 'builder_menu' and not lost:
                lost.append(point)
                return
            real_tap(point)
        device.tap = flaky_tap
        with patch('bot.TAP_RETRY_SECONDS', 0.05):
            self.assertEqual(runner.tap_until('builder_menu', 'find', {'builder_scout'}, 1), 'builder_scout')
        self.assertEqual(len(lost), 1)

    def test_tap_until_keeps_tapping_through_an_attack_cooldown(self):
        device = Device(self.frames, initial='home_menu')
        runner = self.make_bot(device, True)
        real_tap, ignored = device.tap, []

        def cooldown_tap(point):
            if device.state == 'home_menu' and len(ignored) < 5:
                ignored.append(point)
                return
            real_tap(point)
        device.tap = cooldown_tap
        with patch('bot.TAP_RETRY_SECONDS', 0.02):
            self.assertEqual(runner.tap_until('home_menu', 'find', {'home_army'}, 2, retries=10), 'home_army')
        self.assertEqual(len(ignored), 5)

    def test_tap_until_gives_up_after_the_retry_limit(self):
        device = Device(self.frames, initial='builder_menu')
        runner = self.make_bot(device, True)
        taps = []
        device.tap = lambda point: taps.append(point)
        with patch('bot.TAP_RETRY_SECONDS', 0.02), self.assertRaises(bot.BotError):
            runner.tap_until('builder_menu', 'find', {'builder_scout'}, 0.2)
        self.assertEqual(len(taps), bot.TAP_RETRIES)

    def test_recover_quits_an_emulator_whose_screen_cannot_be_captured(self):
        device, emulator = self.restartable()
        device.capture = lambda: (_ for _ in ()).throw(bot.DeviceError('screencap timeout'))
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0, emulator=emulator)
        with self.assertRaises(bot.DeviceError):
            runner.recover()
        self.assertEqual(emulator.events, ['quit'])

    def test_recover_finishes_a_battle_that_was_running_when_adb_dropped(self):
        device = Device(self.frames, initial='builder_result')
        device.connected = lambda: True
        device.game_in_front = lambda: True
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0)
        runner.recover()
        self.assertIn((80, 70), device.inputs)
        self.assertEqual(device.state, 'builder')

    def test_unknown_screen_while_the_game_is_closing_is_recovered(self):
        device = Device(self.frames)
        device.connected = lambda: True
        checks = iter([True, False])
        device.game_in_front = lambda: next(checks, True)
        device.launch = lambda activity: None
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0)
        calls = []

        def flaky(searches):
            calls.append(searches)
            if len(calls) == 1:
                raise bot.UnknownScreen('Layar tidak dikenal; tidak ada input dikirim')
            return 9
        runner.run_until_full = flaky
        with patch('bot.available_memory_mb', return_value=None):
            self.assertEqual(runner.run_until_full_resilient(0, max_recoveries=2), 9)

    def test_failed_recovery_is_retried_instead_of_stopping(self):
        device = Device(self.frames)
        device.game_in_front = lambda: True
        device.connected = lambda: True
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0)
        runs, recoveries = [], []

        def flaky_run(searches):
            runs.append(1)
            if len(runs) == 1:
                raise bot.DeviceError('ADB gagal')
            return 5

        def flaky_recover():
            recoveries.append(1)
            if len(recoveries) == 1:
                raise bot.DeviceError('ADB tidak kembali')
        runner.run_until_full, runner.recover = flaky_run, flaky_recover
        self.assertEqual(runner.run_until_full_resilient(0, max_recoveries=3), 5)
        self.assertEqual((len(runs), len(recoveries)), (2, 2))

    def test_enough_memory_or_dry_run_never_restarts_emulator(self):
        device, emulator = self.restartable()
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0, emulator=emulator)
        with patch('bot.available_memory_mb', return_value=bot.LOW_MEMORY_MB + 1):
            self.assertFalse(runner.refresh_if_low_memory())
        with patch('bot.available_memory_mb', return_value=None):
            self.assertFalse(runner.refresh_if_low_memory())
        dry = bot.Bot(self.config, self.root, device, live=False, timeout=1, poll=0, emulator=emulator)
        with patch('bot.available_memory_mb', return_value=0):
            self.assertFalse(dry.refresh_if_low_memory())
        self.assertEqual(emulator.events, [])

    def test_recover_starts_stopped_emulator_then_game(self):
        device = Device(self.frames)
        device.up = False
        device.front = False
        device.launches = []
        device.connected = lambda: device.up
        device.booted = lambda: device.up
        device.game_in_front = lambda: device.front
        device.launch = lambda activity: (device.launches.append(activity), setattr(device, 'front', True))

        class Emulator:
            starts = 0
            def running(self):
                return device.up
            def launch(self):
                Emulator.starts += 1
                device.up = True
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0, emulator=Emulator())
        runner.run_until_full = lambda searches: 2
        self.assertEqual(runner.run_until_full_resilient(0, max_recoveries=1), 2)
        self.assertEqual(Emulator.starts, 1)
        self.assertEqual(device.launches, [bot.GAME_ACTIVITY])

    def test_recover_retries_launch_when_game_does_not_open(self):
        device = Device(self.frames)
        device.front = False
        device.launches = []
        device.connected = lambda: True
        device.game_in_front = lambda: device.front
        def launch(activity):
            device.launches.append(activity)
            device.front = len(device.launches) >= 2
        device.launch = launch
        shots = []
        def capture():
            shots.append(1)
            return (self.frames['home'] if device.front else np.zeros((90, 160, 3), dtype=np.uint8)).copy()
        device.capture = capture
        runner = bot.Bot(self.config, self.root, device, live=True, timeout=1, poll=0)
        runner.recover()
        self.assertEqual(len(device.launches), 2)

    def test_emulator_console_commands(self):
        emulator = bot.Emulator('ldconsole.exe', 0)
        with patch('bot.subprocess.run') as run:
            run.return_value.stdout = 'running'
            self.assertTrue(emulator.running())
            self.assertEqual(run.call_args.args[0], ['ldconsole.exe', 'isrunning', '--index', '0'])
            run.return_value.stdout = 'stop'
            self.assertFalse(emulator.running())
            emulator.launch()
            self.assertEqual(run.call_args.args[0], ['ldconsole.exe', 'launch', '--index', '0'])
            self.assertIs(run.call_args.kwargs.get('shell'), False)

    def test_adb_command_failure_is_device_error(self):
        device = bot.ADB('adb.exe', 'emulator-5554', [160, 90])
        with patch('bot.subprocess.run', side_effect=OSError('offline')):
            with self.assertRaises(bot.DeviceError):
                device.command('shell', 'true')
        with patch('bot.subprocess.run') as run:
            run.return_value.stdout = b'  mCurrentFocus=Window{1 u0 com.supercell.clashofclans/x}'
            self.assertTrue(device.game_in_front())
            run.return_value.stdout = b'  mCurrentFocus=Window{1 u0 com.android.launcher3/x}'
            self.assertFalse(device.game_in_front())

    def test_sweep_deploys_every_card_still_coloured(self):
        frame = self.frames['home_scout'].copy()
        frame[22:90, 10:90] = cv2.cvtColor(cv2.cvtColor(frame[22:90, 10:90], cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        scout = {**self.config['screens']['home_scout'], 'troops': [],
                 'sweep': {'slots': [[50, 60], [110, 60]], 'deploy': [40, 40]}}
        config = {**self.config, 'screens': {**self.config['screens'], 'home_scout': scout}}
        bot.validate_config(config, self.root, 'both')
        device = Device({**self.frames, 'home_scout': frame}, 'home_scout')
        device.tap = lambda point: device.inputs.append(tuple(point))
        bot.Bot(config, self.root, device, live=True, timeout=0.02, poll=0).deploy('home_scout', {'home_scout'})
        self.assertNotIn((50, 60), device.inputs)
        self.assertIn((110, 60), device.inputs)
        self.assertIn((40, 40), device.inputs)

    def test_tap_only_card_presses_hero_ability(self):
        scout = {**self.config['screens']['home_scout'],
                 'troops': [{'slot': [20, 60], 'count': 1}, {'slot': [20, 60], 'count': 1, 'tap_only': True}]}
        config = {**self.config, 'screens': {**self.config['screens'], 'home_scout': scout}}
        bot.validate_config(config, self.root, 'both')
        device = Device(self.frames, 'home_scout')
        device.tap = lambda point: device.inputs.append(tuple(point))
        bot.Bot(config, self.root, device, live=True, timeout=0.02, poll=0).deploy('home_scout', {'home_scout'})
        self.assertEqual(device.inputs, [(20, 60), (40, 40), (20, 60)])

    def test_adb_hold_is_one_long_press(self):
        fast = bot.ADB('adb.exe', 'emulator-5554', [1280, 720], live=True, touch='/dev/input/event4')
        with patch('bot.subprocess.run') as run:
            fast.hold([130, 650], [125, 346], 1.5)
        script = run.call_args.args[0][4]
        self.assertIn('sleep 1.5', script)
        self.assertIn('cat /data/local/tmp/cocbot/d_125_346 > /dev/input/event4', script)
        plain = bot.ADB('adb.exe', 'emulator-5554', [1280, 720], live=True)
        with patch('bot.subprocess.run') as run:
            plain.hold(None, [125, 346], 1.5)
        self.assertIn('input swipe 125 346 125 346 1500', run.call_args.args[0][4])
        with self.assertRaises(bot.BotError):
            bot.ADB('adb.exe', 'emulator-5554', [1280, 720]).hold(None, [125, 346], 1.5)

    def test_spell_points_cycle_inside_base(self):
        scout = {**self.config['screens']['home_scout'],
                 'troops': [{'slot': [20, 80], 'count': 3, 'points': [[70, 30], [90, 40]]}]}
        config = {**self.config, 'screens': {**self.config['screens'], 'home_scout': scout}}
        bot.validate_config(config, self.root, 'both')
        device = Device(self.frames, 'home_scout')
        device.tap = lambda point: device.inputs.append(tuple(point))
        bot.Bot(config, self.root, device, live=True, timeout=0.02, poll=0).deploy('home_scout', {'home_scout'})
        self.assertEqual(device.inputs, [(20, 80), (70, 30), (90, 40), (70, 30)])
        timed = {**scout, 'troops': [{'slot': [20, 80], 'count': 1, 'points': [[70, 30]], 'wait': 2.5}]}
        slept = []
        with patch('bot.time.sleep', side_effect=slept.append):
            bot.Bot({**config, 'screens': {**config['screens'], 'home_scout': timed}}, self.root,
                    Device(self.frames, 'home_scout'), live=True, timeout=0.02, poll=0).deploy('home_scout', {'home_scout'})
        self.assertIn(2.5, slept)
        bad = {**scout, 'troops': [{'slot': [20, 80], 'count': 1, 'points': [[999, 1]]}]}
        with self.assertRaises(bot.BotError):
            bot.validate_config({**config, 'screens': {**config['screens'], 'home_scout': bad}}, self.root, 'both')

    def test_adb_tap_many_uses_one_shell_call(self):
        device = bot.ADB('adb.exe', 'emulator-5554', [1280, 720], live=True)
        with patch('bot.subprocess.run') as run:
            device.tap_many([[10, 20], [30, 40]])
        self.assertEqual(run.call_count, 1)
        self.assertIn('input tap 10 20', run.call_args.args[0][4])
        fast = bot.ADB('adb.exe', 'emulator-5554', [1280, 720], live=True, touch='/dev/input/event4')
        with patch('bot.subprocess.run') as run:
            fast.tap_many([[10, 20], [30, 40], [10, 20]])
        prepare, taps = (call.args[0][4] for call in run.call_args_list)
        self.assertIn('base64 -d', prepare)
        self.assertIn('cat /data/local/tmp/cocbot/d_30_40 > /dev/input/event4', taps)
        self.assertEqual(taps.count('/data/local/tmp/cocbot/up'), 3)
        with patch('bot.subprocess.run') as run:
            fast.tap_many([[30, 40]])
        self.assertEqual(run.call_count, 1)
        with self.assertRaises(bot.BotError):
            bot.ADB('adb.exe', 'emulator-5554', [1280, 720]).tap_many([[10, 20]])
        with self.assertRaises(bot.BotError):
            bot.ADB('adb.exe', 'emulator-5554', [1280, 720], live=True, touch='/x; rm').tap_many([[10, 20]])

    def with_switch_target(self):
        icon = self.frames['home'][40:50, 60:72]
        cv2.imwrite(str(self.root / 'boat.png'), icon)
        home = {**self.config['screens']['home'], 'actions': {'attack': [10, 75]},
                'targets': {'switch': {'file': 'boat.png', 'max_score': 0.12}}}
        return {**self.config, 'screens': {**self.config['screens'], 'home': home}}, icon

    def test_target_action_taps_located_icon(self):
        config, _ = self.with_switch_target()
        bot.validate_config(config, self.root, 'both')
        device = Device(self.frames)
        runner = bot.Bot(config, self.root, device, live=True, timeout=0.02, poll=0)
        runner.action('home', 'switch')
        self.assertEqual(device.inputs, [(66, 45)])

    def test_target_missing_or_duplicated_stops_without_tap(self):
        config, icon = self.with_switch_target()
        missing = self.frames['home'].copy()
        missing[40:50, 60:72] = 255 - missing[40:50, 60:72]
        duplicated = self.frames['home'].copy()
        duplicated[60:70, 90:102] = icon
        for frame in (missing, duplicated):
            device = Device({**self.frames, 'home': frame})
            runner = bot.Bot(config, self.root, device, live=True, timeout=0.02, poll=0)
            with self.subTest(), self.assertRaises(bot.BotError):
                runner.action('home', 'switch')
            self.assertEqual(device.inputs, [])

    def test_target_pans_camera_then_taps_with_offset(self):
        config, _ = self.with_switch_target()
        target = {**config['screens']['home']['targets']['switch'],
                  'pan': [[80, 40], [100, 30]], 'offset': [-4, 10]}
        home = {**config['screens']['home'], 'targets': {'switch': target}}
        config = {**config, 'screens': {**config['screens'], 'home': home}}
        bot.validate_config(config, self.root, 'both')
        device = Device(self.frames)
        runner = bot.Bot(config, self.root, device, live=True, timeout=0.02, poll=0)
        runner.action('home', 'switch')
        self.assertEqual(device.inputs, [('swipe', (80, 40), (100, 30)), (62, 55)])
        dry = Device(self.frames)
        bot.Bot(config, self.root, dry, live=False, timeout=0.02, poll=0).action('home', 'switch')
        self.assertEqual(dry.inputs, [])

    def test_until_empty_stops_when_card_turns_grey(self):
        frame = self.frames['home_scout']
        grey = frame.copy()
        grey[22:90, 0:60] = cv2.cvtColor(cv2.cvtColor(grey[22:90, 0:60], cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        scout = {**self.config['screens']['home_scout'],
                 'troops': [{'slot': [20, 60], 'count': 10, 'until_empty': True}]}
        config = {**self.config, 'screens': {**self.config['screens'], 'home_scout': scout}}
        bot.validate_config(config, self.root, 'both')
        device = Device(self.frames, 'home_scout')
        deploys = lambda: device.inputs.count((40, 40))
        device.capture = lambda: (grey if deploys() >= 3 else frame).copy()
        device.tap = lambda point: device.inputs.append(tuple(point))
        runner = bot.Bot(config, self.root, device, live=True, timeout=0.02, poll=0)
        runner.deploy('home_scout', {'home_scout'})
        self.assertEqual(device.inputs, [(20, 60)] + [(40, 40)] * bot.DEPLOY_BURST)

    def test_troops_can_spread_to_own_deploy_points(self):
        scout = {**self.config['screens']['builder_scout'],
                 'troops': [{'slot': [20, 80], 'count': 1, 'deploy': [30, 50]},
                            {'slot': [35, 80], 'count': 1}]}
        config = {**self.config, 'screens': {**self.config['screens'], 'builder_scout': scout}}
        bot.validate_config(config, self.root, 'both')
        device = Device(self.frames, 'builder_scout')
        runner = bot.Bot(config, self.root, device, live=True, timeout=0.02, poll=0)
        runner.deploy('builder_scout', {'builder_scout', 'builder_battle'})
        self.assertEqual(device.inputs, [(20, 80), (30, 50), (35, 80), (40, 40)])
        bad = {**scout, 'troops': [{'slot': [20, 80], 'count': 1, 'deploy': [999, 50]}]}
        with self.assertRaises(bot.BotError):
            bot.validate_config({**config, 'screens': {**config['screens'], 'builder_scout': bad}},
                                self.root, 'both')

    def test_target_found_at_other_zoom_with_scaled_offset(self):
        config, icon = self.with_switch_target()
        big = cv2.resize(icon, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_LINEAR)
        frame = self.frames['home'].copy()
        frame[40:50, 60:72] = 0
        frame[30:30 + big.shape[0], 90:90 + big.shape[1]] = big
        target = {**config['screens']['home']['targets']['switch'], 'scales': [1.0, 1.5], 'offset': [0, 10]}
        home = {**config['screens']['home'], 'targets': {'switch': target}}
        config = {**config, 'screens': {**config['screens'], 'home': home}}
        bot.validate_config(config, self.root, 'both')
        device = Device({**self.frames, 'home': frame})
        bot.Bot(config, self.root, device, live=True, timeout=0.02, poll=0).action('home', 'switch')
        self.assertEqual(device.inputs, [(99, 52)])

    def test_target_pans_again_until_icon_visible(self):
        config, _ = self.with_switch_target()
        hidden = self.frames['home'].copy()
        hidden[40:50, 60:72] = 255 - hidden[40:50, 60:72]
        target = {**config['screens']['home']['targets']['switch'],
                  'pan': [[80, 40], [100, 30]], 'pan_attempts': 3}
        home = {**config['screens']['home'], 'targets': {'switch': target}}
        config = {**config, 'screens': {**config['screens'], 'home': home}}
        bot.validate_config(config, self.root, 'both')
        device = Device(self.frames)
        swipes = lambda: sum(1 for i in device.inputs if i[0] == 'swipe')
        device.capture = lambda: (self.frames['home'] if swipes() >= 2 else hidden).copy()
        bot.Bot(config, self.root, device, live=True, timeout=0.02, poll=0).action('home', 'switch')
        self.assertEqual(swipes(), 2)
        self.assertEqual(device.inputs[-1], (66, 45))
        never = Device(self.frames)
        never.capture = lambda: hidden.copy()
        with self.assertRaises(bot.BotError):
            bot.Bot(config, self.root, never, live=True, timeout=0.02, poll=0).action('home', 'switch')
        self.assertEqual([i for i in never.inputs if i[0] != 'swipe'], [])

    def zoom_config(self):
        return {**self.config, 'zoom_out': {'device': '/dev/input/event4', 'pinches': 2,
                                            'center': [80, 45], 'gap': [120, 20]}}

    def test_zoom_out_in_village_before_switch_and_attack_both_villages(self):
        config = self.zoom_config()
        bot.validate_config(config, self.root, 'both')
        device = Device(self.frames)
        runner = bot.Bot(config, self.root, device, live=True, reader=lambda i, s: next(device.loot),
                         timeout=0.02, poll=0)
        runner.run('both', 1, 3)
        pinches = [i for i in device.inputs if i[0] == 'pinch']
        self.assertGreaterEqual(len(pinches), 2 * 2)
        first_switch = device.inputs.index((150, 75))
        self.assertEqual(device.inputs[first_switch - 1][0], 'pinch')
        attack_taps = [n for n, i in enumerate(device.inputs) if i == (10, 75)]
        self.assertTrue(all(device.inputs[n - 1][0] == 'pinch' for n in attack_taps))

    def test_zoom_out_clears_building_selection_or_stops(self):
        popup = self.frames['home'][60:72, 100:114]
        cv2.imwrite(str(self.root / 'info.png'), popup)
        config = self.zoom_config()
        config = {**config, 'zoom_out': {**config['zoom_out'], 'selection': {'file': 'info.png', 'max_score': 0.08}}}
        bot.validate_config(config, self.root, 'both')
        clean = self.frames['home'].copy()
        clean[60:72, 100:114] = 255 - clean[60:72, 100:114]
        device = Device(self.frames)
        pinches = lambda: sum(1 for i in device.inputs if i[0] == 'pinch')
        device.capture = lambda: (clean if pinches() > 2 else self.frames['home']).copy()
        bot.Bot(config, self.root, device, live=True, timeout=0.02, poll=0).zoom_out('home')
        self.assertEqual(pinches(), 3)
        stuck = Device(self.frames)
        with self.assertRaises(bot.BotError):
            bot.Bot(config, self.root, stuck, live=True, timeout=0.02, poll=0).zoom_out('home')
        self.assertTrue(all(i[0] == 'pinch' for i in stuck.inputs))

    def test_zoom_out_waits_through_transition_frames(self):
        blank = np.zeros((90, 160, 3), dtype=np.uint8)
        shots = iter([blank, blank] + [self.frames['home']] * 10)
        device = Device(self.frames)
        device.capture = lambda: next(shots).copy()
        bot.Bot(self.zoom_config(), self.root, device, live=True, timeout=1, poll=0).zoom_out('home')
        self.assertEqual(sum(1 for i in device.inputs if i[0] == 'pinch'), 2)

    def test_deploy_zooms_out_before_selecting_any_card(self):
        config = self.zoom_config()
        scout = {**config['screens']['home_scout'], 'troops': [{'slot': [20, 80], 'count': 1}]}
        config = {**config, 'screens': {**config['screens'], 'home_scout': scout}}
        device = Device(self.frames, 'home_scout')
        device.tap = lambda point: device.inputs.append(tuple(point))
        bot.Bot(config, self.root, device, live=True, timeout=0.02, poll=0).deploy('home_scout', {'home_scout'})
        kinds = [i[0] if isinstance(i[0], str) else 'tap' for i in device.inputs]
        self.assertEqual(kinds[:2], ['pinch', 'pinch'])
        self.assertEqual(device.inputs[2], (20, 80))
        dry = Device(self.frames, 'home_scout')
        bot.Bot(config, self.root, dry, live=False, timeout=0.02, poll=0).deploy('home_scout', {'home_scout'})
        self.assertEqual(dry.inputs, [])

    def test_zoom_out_never_pinches_during_battle(self):
        runner = bot.Bot(self.zoom_config(), self.root, Device(self.frames, 'builder_stage2'),
                         live=True, timeout=0.02, poll=0)
        with self.assertRaises(bot.BotError):
            runner.zoom_out('builder_stage2')
        self.assertEqual(runner.device.inputs, [])

    def test_zoom_out_dry_run_and_bad_device_are_safe(self):
        config = self.zoom_config()
        device = Device(self.frames)
        bot.Bot(config, self.root, device, live=False, timeout=0.02, poll=0).zoom_out('home')
        self.assertEqual(device.inputs, [])
        for bad in ('/dev/input/event4; reboot', '/sdcard/x', ''):
            broken = {**config, 'zoom_out': {**config['zoom_out'], 'device': bad}}
            with self.subTest(bad=bad), self.assertRaises(bot.BotError):
                bot.validate_config(broken, self.root, 'both')

    def test_adb_pinch_sends_one_multitouch_script(self):
        device = bot.ADB('adb.exe', 'emulator-5554', [1280, 720], live=True)
        with patch('bot.subprocess.run') as run:
            device.pinch('/dev/input/event4', [640, 360], 600, 100)
        prepare, gesture = (call.args[0] for call in run.call_args_list)
        self.assertEqual(gesture[:4], ['adb.exe', '-s', 'emulator-5554', 'shell'])
        self.assertIn('base64 -d', prepare[4])
        self.assertNotIn('sendevent', gesture[4])
        self.assertEqual(gesture[4].count('> /dev/input/event4'), bot.PINCH_STEPS + 2)
        self.assertIs(run.call_args.kwargs.get('shell'), False)
        with patch('bot.subprocess.run') as run:
            device.pinch('/dev/input/event4', [640, 360], 600, 100)
        self.assertEqual(run.call_count, 1)
        with self.assertRaises(bot.BotError):
            bot.ADB('adb.exe', 'emulator-5554', [1280, 720]).pinch('/dev/input/event4', [640, 360], 600, 100)

    def test_target_inside_avoid_zone_stops_without_tap(self):
        config, _ = self.with_switch_target()
        target = {**config['screens']['home']['targets']['switch'], 'avoid': [[60, 40, 20, 20]]}
        home = {**config['screens']['home'], 'targets': {'switch': target}}
        config = {**config, 'screens': {**config['screens'], 'home': home}}
        bot.validate_config(config, self.root, 'both')
        device = Device(self.frames)
        with self.assertRaises(bot.BotError):
            bot.Bot(config, self.root, device, live=True, timeout=0.02, poll=0).action('home', 'switch')
        self.assertEqual(device.inputs, [])

    def test_target_config_rejects_unknown_action_and_bad_score(self):
        config, _ = self.with_switch_target()
        for target in ({'next': {'file': 'boat.png', 'max_score': 0.12}},
                       {'switch': {'file': 'boat.png', 'max_score': 0.9}},
                       {'switch': {'file': 'missing.png', 'max_score': 0.12}},
                       {'switch': {'file': 'boat.png', 'max_score': 0.12, 'pan': [[80, 40]]}},
                       {'switch': {'file': 'boat.png', 'max_score': 0.12, 'offset': [0, 999]}},
                       {'switch': {'file': 'boat.png', 'max_score': 0.12, 'scales': [5.0]}},
                       {'switch': {'file': 'boat.png', 'max_score': 0.12, 'pan_attempts': 9,
                                   'pan': [[80, 40], [100, 30]]}},
                       {'switch': {'file': 'boat.png', 'max_score': 0.12, 'avoid': [[0, 0, 999, 10]]}}):
            home = {**config['screens']['home'], 'targets': target}
            broken = {**config, 'screens': {**config['screens'], 'home': home}}
            with self.subTest(target=target), self.assertRaises(bot.BotError):
                bot.validate_config(broken, self.root, 'both')

    def test_calibration_json_write_preserves_existing_on_failure(self):
        import calibrate
        path = self.root / 'config.json'
        path.write_text('{"old": true}', encoding='utf-8')
        with patch('calibrate.os.replace', side_effect=OSError('locked')):
            with self.assertRaises(OSError):
                calibrate.save_config(path, {'new': True})
        self.assertEqual(json.loads(path.read_text()), {'old': True})


if __name__ == '__main__':
    unittest.main()
