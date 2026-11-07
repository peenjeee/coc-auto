import http.client
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import base64

import messages
import remote

KEY = 'coc_testkey_0123456789abcdefghijklmnop'
CHILD = '''
import signal, sys, time
if hasattr(signal, "SIGBREAK"):
    signal.signal(signal.SIGBREAK, signal.default_int_handler)
print("INFO: args " + " ".join(sys.argv[1:]), flush=True)
try:
    while True:
        time.sleep(0.05)
except KeyboardInterrupt:
    print("WARNING: stopped", flush=True)
    sys.exit(130)
'''


def signed(action, args=None, nonce=None, key=KEY):
    body = json.dumps({'action': action, 'args': args or {}, 'ts': time.time(),
                       'nonce': nonce or f'{action}-{time.monotonic_ns()}'})
    return body, remote.sign(key, body)


def until(condition, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


class FakeDevice:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda: self.calls.append(name)


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        child = Path(self.temp.name) / 'child.py'
        child.write_text(CHILD, encoding='utf-8')
        self.device = FakeDevice()
        self.supervisor = remote.Supervisor(KEY, [sys.executable, str(child)], self.device)
        self.addCleanup(self.supervisor.stop)
        self.seen = []
        self.supervisor.subscribe(lambda kind, data: self.seen.append((kind, data)))

    def lines(self):
        return [data for kind, data in self.seen if kind == 'line']

    def test_live_screen_is_signed_read_only_and_preserves_the_frame(self):
        import cv2
        import numpy as np
        frame = np.full((720, 1280, 3), (30, 80, 180), dtype=np.uint8)
        adb = Mock()
        adb.capture.return_value = frame
        self.device.adb = Mock(return_value=adb)
        self.supervisor.stop = Mock()
        invalid = self.supervisor.command(*signed('get_screen', key='wrong-key'))
        self.assertEqual(invalid['code'], 'unsigned')
        self.device.adb.assert_not_called()
        before = time.time()
        result = self.supervisor.command(*signed('get_screen'))
        self.assertTrue(result['ok'])
        self.assertEqual(result['code'], 'screen_loaded')
        self.assertGreaterEqual(result['data']['captured_at'], before)
        raw = base64.b64decode(result['data']['image'].split(',', 1)[1])
        decoded = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(decoded.shape, (360, 640, 3))
        self.assertTrue(np.allclose(decoded[0, 0], frame[0, 0], atol=3))
        self.supervisor.stop.assert_not_called()
        self.assertEqual(self.lines(), [])

    def test_unavailable_screen_does_not_stop_worker_or_flood_log(self):
        self.device.adb = Mock(side_effect=RuntimeError('ADB unavailable'))
        self.supervisor.stop = Mock()
        result = self.supervisor.command(*signed('get_screen'))
        self.assertFalse(result['ok'])
        self.assertEqual(result['code'], 'failed')
        self.supervisor.stop.assert_not_called()
        self.assertEqual(self.lines(), [])

    def test_the_bot_runs_as_a_child_and_its_log_reaches_the_dashboard(self):
        self.supervisor.start(['--walls', 'home', '--live'])
        self.assertTrue(until(lambda: any('args --walls home --live --worker' in line for line in self.lines())))
        self.assertEqual(self.supervisor.state()['state'], 'running')
        self.assertTrue(self.supervisor.stop())
        # Stopped like Ctrl+C, so the bot finishes its own way.
        self.assertTrue(until(lambda: any('stopped' in line for line in self.lines())))
        self.assertTrue(until(lambda: self.supervisor.state()['state'] == 'idle'))
        self.assertIn(('line', 'INFO: args --walls home --live --worker'), self.seen)

    def test_start_from_the_dashboard_runs_the_chosen_command(self):
        result = self.supervisor.command(*signed('start', {'task': 'dry', 'village': 'home'}))
        self.assertEqual((result['ok'], result['code']), (True, 'started'), result)
        self.assertTrue(until(lambda: any('args --mode home --worker' in line for line in self.lines())))
        self.assertEqual(self.supervisor.command(*signed('restart'))['code'], 'restarted')
        self.assertTrue(until(lambda: sum('args --mode home --worker' in line for line in self.lines()) == 2))

    def test_a_language_switch_changes_output_without_restarting_the_worker(self):
        self.addCleanup(messages.use, 'id')
        self.supervisor.settings = SimpleNamespace(write_language=lambda language: language, read=lambda: {})
        # The child deliberately requests English; its supervisor still receives canonical text.
        script = ('import logging, messages\n'
                  'logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")\n'
                  'messages.install("en")\n'
                  'logging.info("Serangan selesai: %s (%s)", "home", 1)\n' + CHILD)
        self.supervisor.command_base = [sys.executable, '-c', script]
        stream = io.StringIO()
        with redirect_stdout(stream):
            self.supervisor.start([])
            self.assertTrue(until(lambda: 'INFO: Serangan selesai: home (1)' in self.supervisor.history()))
            child = self.supervisor.child
            result = self.supervisor.command(*signed('set_language', {'language': 'en'}))
            self.assertTrue(result['ok'])
            self.supervisor.log('INFO: Menyerah: kerusakan mencapai 50%')
            self.assertIn('INFO: Surrender: damage reached 50%', stream.getvalue())
            self.assertEqual(self.supervisor.history()[-1], 'INFO: Menyerah: kerusakan mencapai 50%')
            self.assertEqual(self.lines()[-1], 'INFO: Menyerah: kerusakan mencapai 50%')
            self.supervisor.command(*signed('set_language', {'language': 'id'}))
            self.supervisor.log('INFO: Menyerah: kerusakan mencapai 50%')
            self.assertIn('INFO: Menyerah: kerusakan mencapai 50%', stream.getvalue())
            self.assertIs(self.supervisor.child, child)
            self.assertIsNone(child.poll())
            self.supervisor.stop()
            self.assertTrue(until(lambda: any('bot berhenti' in line for line in self.supervisor.history())))

    def test_english_refusals_keep_a_canonical_reason_for_the_dashboard(self):
        self.addCleanup(messages.use, 'id')
        messages.use('en')
        result = self.supervisor.command(*signed('start', {'task': 'attack', 'cycles': 101}))
        self.assertEqual(result['code'], 'refused')
        self.assertEqual(result['message'], 'Command rejected: Option cycles must be a number 1..100')
        self.assertEqual(result['source_message'], 'Perintah ditolak: Pilihan cycles harus angka 1..100')
        self.assertEqual(self.device.calls, [])

    def test_device_actions_stop_the_bot_first(self):
        self.supervisor.start(['--mode', 'home'])
        self.assertTrue(until(lambda: self.supervisor.state()['state'] == 'running'))
        for action, calls, code in (('close_coc', ['close_coc'], 'coc_closed'), ('kill_ld', ['kill_ld'], 'ld_off'),
                                    ('restart_ld', ['kill_ld', 'launch_ld'], 'ld_restarted'),
                                    ('open_coc', ['open_coc'], 'coc_opened')):
            self.device.calls.clear()
            result = self.supervisor.command(*signed(action))
            with self.subTest(action=action):
                self.assertEqual((result['ok'], result['code']), (True, code), result)
                self.assertEqual(self.device.calls, calls)
                self.assertEqual(self.supervisor.state()['state'], 'idle')

    def test_a_building_scan_waits_for_a_stopped_bot_and_answers_with_the_rows(self):
        rows = [{'id': 'cannon', 'name': 'Cannon', 'category': 'defense', 'count': 5, 'next_level': 8,
                 'time': '1h 30m', 'cost': 80000, 'currency': 'gold', 'section': 'other'}]
        self.device.scan_buildings = Mock(return_value=rows)
        self.supervisor.start(['--mode', 'home'])
        self.assertTrue(until(lambda: self.supervisor.state()['state'] == 'running'))
        # The scan taps the game, so it is refused under a running bot, which it never stops by itself.
        refused = self.supervisor.command(*signed('scan_buildings'))
        self.assertEqual((refused['ok'], refused['code']), (False, 'refused'))
        self.device.scan_buildings.assert_not_called()
        self.assertEqual(self.supervisor.state()['state'], 'running')
        self.supervisor.stop()
        result = self.supervisor.command(*signed('scan_buildings'))
        self.assertEqual((result['ok'], result['code'], result['data']), (True, 'buildings_scanned', {'rows': rows}))
        self.assertEqual(result['source_message'], 'Scan bangunan selesai: 1 baris')

    def test_unsigned_or_bad_commands_do_nothing(self):
        for body, sig, code in (signed('close_coc', key='coc_someone_else_entirely_0000000000') + ('unsigned',),
                                signed('start', {'task': 'shell'}) + ('refused',)):
            result = self.supervisor.command(body, sig)
            self.assertEqual((result['ok'], result['code']), (False, code))
            self.assertIn('ditolak', result['message'])
        self.assertEqual(self.device.calls, [])
        self.assertEqual(self.supervisor.state()['state'], 'idle')
        self.assertFalse(self.supervisor.command(*signed('restart'))['ok'])

    def test_strangers_on_the_channel_do_not_fill_the_log(self):
        for number in range(50):
            self.supervisor.command(*signed('kill_ld', nonce=f'x{number}', key='coc_someone_else_entirely_0000000000'))
        self.assertEqual(self.lines(), [])

    def test_commands_from_before_this_bot_started_are_refused(self):
        body = json.dumps({'action': 'open_coc', 'args': {}, 'ts': self.supervisor.started - 5, 'nonce': 'early'})
        self.assertEqual(self.supervisor.command(body, remote.sign(KEY, body))['code'], 'unsigned')
        self.assertEqual(self.device.calls, [])


REPO = Path(remote.__file__).resolve().parent


class DeployPointsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        self.config = folder / 'config.json'
        self.config.write_text((REPO / 'config.json').read_text(encoding='utf-8'), encoding='utf-8')
        self.captures = folder / 'captures'
        self.points = remote.DeployPoints(self.config, self.captures, REPO)

    def screen(self, name):
        import bot
        return bot.load_config(self.config)['screens'][name]

    def test_the_pins_of_every_deploy_screen_are_read(self):
        data = self.points.read()
        self.assertEqual(data['resolution'], [1280, 720])
        home, builder = data['screens']['home_scout'], data['screens']['builder_stage2']
        self.assertEqual((home['kind'], home['deploy'], home['points']),
                         ('spells', self.screen('home_scout')['deploy'], self.screen('home_scout')['auto']['spells']))
        self.assertEqual((builder['kind'], builder['bar']), ('points', self.screen('builder_stage2')['auto']['bar']))

    def test_the_ring_and_quake_pins_are_their_own_tabs_on_the_home_map(self):
        data = self.points.read()
        auto = self.screen('home_scout')['auto']
        ring, around = data['screens']['home_ring'], data['screens']['home_spell_spread']
        quake = data['screens']['home_quake']
        self.assertEqual([(tab['kind'], tab['points']) for tab in (ring, around, quake)],
                         [('ring', auto['ring']), ('spell_spread', auto['spell_spread']), ('quake', auto['quake'])])
        self.points.write('home_quake', quake['deploy'], [[600, 300], [700, 300]])
        moved = self.screen('home_scout')['auto']
        self.assertEqual((moved['quake'], moved['spells'], moved['ring']),
                         ([[600, 300], [700, 300]], auto['spells'], auto['ring']))
        with self.assertRaises(remote.CommandRefused):
            self.points.write('home_quake', quake['deploy'], [[600, 300]] * 9)

    def test_moved_pins_go_to_the_local_config_and_the_shared_one_is_kept(self):
        import bot
        shared = self.config.read_text(encoding='utf-8')
        before = bot.load_config(self.config)
        self.points.write('builder_scout', [200, 380], [[300, 200], [800, 150], [1100, 360]])
        after = bot.load_config(self.config)
        self.assertEqual(after['screens']['builder_scout']['deploy'], [200, 380])
        self.assertEqual(after['screens']['builder_scout']['auto']['points'], [[300, 200], [800, 150], [1100, 360]])
        after['screens']['builder_scout'] = before['screens']['builder_scout']
        self.assertEqual(after, before)
        # A git pull of new calibration never collides with the pins this PC moved.
        self.assertEqual(self.config.read_text(encoding='utf-8'), shared)
        local = json.loads(self.config.with_name('config.local.json').read_text(encoding='utf-8'))
        self.assertEqual(local, {'screens': {'builder_scout': {'deploy': [200, 380], 'auto': {'points':
                                                                [[300, 200], [800, 150], [1100, 360]]}}}})
        self.points.write('builder_scout', [210, 380], [[300, 200]])
        self.assertTrue(self.config.with_name('config.local.json.bak').is_file())

    def test_pins_off_the_screen_on_the_card_bar_or_malformed_are_refused(self):
        original = self.config.read_text(encoding='utf-8')
        bar_top = self.screen('home_scout')['auto']['bar'][1]
        for screen, deploy, points in (('home_scout', [100, 300], [[500, bar_top + 5]]),
                                       ('home_scout', [1400, 300], [[500, 300]]),
                                       ('home_scout', [100, 300], []),
                                       ('home_scout', [100, 300], [[500, 300]] * 31),
                                       ('builder_scout', [True, 300], [[500, 300]]),
                                       ('builder_scout', [100, 300], [['500', 300]]),
                                       ('home_army', [100, 300], [[500, 300]])):
            with self.subTest(screen=screen, deploy=deploy, points=points[:2]), self.assertRaises(remote.CommandRefused):
                self.points.write(screen, deploy, points)
        self.assertEqual(self.config.read_text(encoding='utf-8'), original)
        self.assertFalse(self.config.with_name('config.local.json').exists())

    def test_the_last_deploy_screenshot_is_sent_small(self):
        import base64
        import cv2
        import numpy as np
        self.assertEqual(self.points.snapshots(), [])
        self.captures.mkdir()
        cv2.imwrite(str(self.captures / 'deploy-home_scout.jpg'), np.full((720, 1280, 3), 90, np.uint8))
        shots = self.points.snapshots()
        self.assertEqual([shot['screen'] for shot in shots], ['home_scout'])
        prefix = 'data:image/jpeg;base64,'
        self.assertTrue(shots[0]['image'].startswith(prefix))
        image = cv2.imdecode(np.frombuffer(base64.b64decode(shots[0]['image'][len(prefix):]), np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(image.shape[:2], (360, 640))
        self.assertLess(len(shots[0]['image']), 200_000)

    def test_the_dashboard_loads_and_saves_pins_through_signed_commands(self):
        supervisor = remote.Supervisor(KEY, [sys.executable, '-c', 'pass'], FakeDevice(), self.points)
        seen = []
        supervisor.subscribe(lambda kind, data: seen.append(kind))
        self.captures.mkdir()
        import cv2
        import numpy as np
        cv2.imwrite(str(self.captures / 'deploy-builder_scout.jpg'), np.zeros((720, 1280, 3), np.uint8))
        result = supervisor.command(*signed('get_deploy'))
        self.assertEqual((result['ok'], result['code']), (True, 'deploy_loaded'))
        self.assertIn('home_scout', result['data']['screens'])
        self.assertIn('snapshot', seen)
        result = supervisor.command(*signed('set_deploy', {'screen': 'home_scout', 'deploy': [130, 340],
                                                            'points': [[400, 340], [300, 270], [300, 420]]}))
        self.assertEqual((result['ok'], result['code']), (True, 'deploy_saved'))
        self.assertEqual(self.screen('home_scout')['auto']['spells'], [[400, 340], [300, 270], [300, 420]])


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        self.config = folder / 'config.json'
        self.config.write_text((REPO / 'config.json').read_text(encoding='utf-8'), encoding='utf-8')
        self.ld = folder / 'LDPlayer9'
        self.ld.mkdir()
        for name in ('adb.exe', 'ldconsole.exe'):
            path = self.ld / name
            path.write_bytes(b'')
            os.chmod(path, 0o755)  # bot.executable checks X_OK on POSIX
        self.asked = []
        self.settings = remote.Settings(self.config, REPO, detector=lambda config: self.asked.append(config) or
                                        {'adb': 'C:/LD/adb.exe', 'problems': []})

    def stored(self):
        import bot
        return bot.load_config(self.config)

    def setup(self, **changes):
        return {'adb': str(self.ld / 'adb.exe'), 'ldconsole': str(self.ld / 'ldconsole.exe'), 'serial': 'emulator-5556',
                'ld_index': 1, 'touch_device': '/dev/input/event5', **changes}

    def test_minimums_and_the_ldplayer_setup_are_read(self):
        data, config = self.settings.read(), self.stored()
        self.assertEqual(data['minimum'], {key: config['minimum'].get(key, 0) for key in ('gold', 'elixir', 'dark')})
        self.assertEqual(data['setup']['serial'], config['serial'])
        self.assertEqual(data['setup']['touch_device'], config['touch_device'])

    def test_minimums_are_saved_and_the_rest_of_the_config_is_kept(self):
        before, shared = self.stored(), self.config.read_text(encoding='utf-8')
        self.settings.write_minimum({'gold': 300000, 'elixir': 250000, 'dark': 4000})
        after = self.stored()
        self.assertEqual(after['minimum'], {'gold': 300000, 'elixir': 250000, 'dark': 4000})
        self.assertEqual({**after, 'minimum': before['minimum']}, before)
        self.assertEqual(self.config.read_text(encoding='utf-8'), shared)
        self.assertEqual(json.loads(self.config.with_name('config.local.json').read_text(encoding='utf-8')),
                         {'minimum': {'gold': 300000, 'elixir': 250000, 'dark': 4000}})

    def test_bad_minimums_are_refused(self):
        original = self.config.read_text(encoding='utf-8')
        for minimum in ('500000', {'gold': 5}, {'gold': -1, 'elixir': 0, 'dark': 0},
                        {'gold': 1_000_000_000, 'elixir': 0, 'dark': 0}, {'gold': True, 'elixir': 0, 'dark': 0},
                        {'gold': 1.5, 'elixir': 0, 'dark': 0}, {'gold': None, 'elixir': 0, 'dark': 0},
                        {'gold': 0, 'elixir': 0, 'dark': 0, 'gems': 1}):
            with self.subTest(minimum=minimum), self.assertRaises(remote.CommandRefused):
                self.settings.write_minimum(minimum)
        self.assertEqual(self.config.read_text(encoding='utf-8'), original)

    def test_a_minimum_the_bot_cannot_read_is_refused(self):
        config = self.stored()
        del config['screens']['home_scout']['loot']['dark']
        self.config.write_text(json.dumps(config), encoding='utf-8')
        with self.assertRaises(remote.CommandRefused):
            self.settings.write_minimum({'gold': 0, 'elixir': 0, 'dark': 5000})

    def test_the_ldplayer_setup_is_saved_with_the_zoom_touchscreen(self):
        self.settings.write_setup(self.setup())
        after = self.stored()
        self.assertEqual((after['adb'], after['ldconsole'], after['serial'], after['ld_index'], after['touch_device']),
                         (str(self.ld / 'adb.exe'), str(self.ld / 'ldconsole.exe'), 'emulator-5556', 1,
                          '/dev/input/event5'))
        self.assertEqual(after['zoom_out']['device'], '/dev/input/event5')

    def test_a_setup_that_is_not_ldplayer_is_refused(self):
        original = self.config.read_text(encoding='utf-8')
        for setup in (self.setup(adb=sys.executable), self.setup(adb='adb.exe'),
                      self.setup(adb=r'\\attacker.example\share\adb.exe'), self.setup(adb='//attacker.example/s/adb.exe'),
                      self.setup(ldconsole=str(self.ld / 'missing' / 'ldconsole.exe')),
                      self.setup(serial='-s emulator-5554'), self.setup(serial='emulator-5554;calc'),
                      self.setup(ld_index=100), self.setup(ld_index=True), self.setup(ld_index=None),
                      self.setup(touch_device='/dev/input/mouse0'),
                      self.setup(extra='x'), {'serial': 'emulator-5554'}, 'emulator-5554'):
            with self.subTest(setup=setup), self.assertRaises(remote.CommandRefused):
                self.settings.write_setup(setup)
        self.assertEqual(self.config.read_text(encoding='utf-8'), original)

    def test_detection_gets_the_current_config(self):
        self.assertEqual(self.settings.detect(), {'adb': 'C:/LD/adb.exe', 'problems': []})
        self.assertEqual(self.asked[0]['serial'], self.stored()['serial'])

    def test_the_dashboard_reads_detects_and_saves_through_signed_commands(self):
        supervisor = remote.Supervisor(KEY, [sys.executable, '-c', 'pass'], FakeDevice(), settings=self.settings)
        self.addCleanup(supervisor.statistics.db.close)  # Windows cannot delete an open database file
        for action, args, code in (('get_settings', {}, 'settings_loaded'),
                                   ('set_minimum', {'minimum': {'gold': 1, 'elixir': 2, 'dark': 0}}, 'minimum_saved'),
                                   ('detect_setup', {}, 'setup_detected'),
                                   ('set_setup', {'setup': self.setup()}, 'setup_saved')):
            result = supervisor.command(*signed(action, args))
            with self.subTest(action=action):
                self.assertEqual((result['ok'], result['code']), (True, code), result)
                self.assertIn('data', result)
        self.assertEqual(self.stored()['minimum'], {'gold': 1, 'elixir': 2, 'dark': 0})
        bare = remote.Supervisor(KEY, [sys.executable, '-c', 'pass'], FakeDevice())
        self.assertEqual(bare.command(*signed('get_settings'))['code'], 'refused')

    def test_the_dashboard_language_is_saved_for_the_terminal_and_used_at_once(self):
        self.addCleanup(messages.use, 'id')
        supervisor = remote.Supervisor(KEY, [sys.executable, '-c', 'pass'], FakeDevice(), settings=self.settings)
        self.addCleanup(supervisor.statistics.db.close)
        result = supervisor.command(*signed('set_language', {'language': 'en'}))
        self.assertEqual((result['ok'], result['code'], result['data']['language']), (True, 'language_saved', 'en'))
        self.assertEqual(self.stored()['language'], 'en')
        self.assertEqual(result['message'], 'Display language saved; logs use it immediately')
        self.assertEqual(messages.translate('REMOTE: menghentikan bot'), 'REMOTE: stopping bot')
        for language in ('fr', None, 1, ['en']):
            with self.subTest(language=language), self.assertRaises(remote.CommandRefused):
                self.settings.write_language(language)
        self.assertEqual(self.stored()['language'], 'en')


class DeviceSwitchTests(unittest.TestCase):
    def test_leaving_the_bot_puts_the_screen_back(self):
        device = FakeDevice()
        supervisor = remote.Supervisor(KEY, [sys.executable, '-c', 'pass'], device)
        remote.leave(supervisor)
        self.assertEqual(device.calls, ['restore_display'])

    def test_the_dashboard_switches_the_screen_after_stopping_the_bot(self):
        device = FakeDevice()
        supervisor = remote.Supervisor(KEY, [sys.executable, '-c', 'pass'], device)
        for action, code in (('fit_display', 'display_ok'), ('restore_display', 'display_unchanged')):
            device.calls.clear()
            result = supervisor.command(*signed(action))
            with self.subTest(action=action):
                self.assertEqual((result['ok'], result['code']), (True, code), result)
                self.assertEqual(device.calls, [action, 'display_status'])

    def test_a_switch_the_device_cannot_make_is_a_refusal(self):
        import device_control

        class Stuck(FakeDevice):
            def fit_display(self):
                raise device_control.SettingRefused('LDPlayer tidak berhenti')
        supervisor = remote.Supervisor(KEY, [sys.executable, '-c', 'pass'], Stuck())
        result = supervisor.command(*signed('fit_display'))
        self.assertEqual((result['ok'], result['code']), (False, 'refused'))
        self.assertIn('LDPlayer', result['message'])


class KeyTests(unittest.TestCase):
    def test_the_key_is_shown_without_running_anything(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as home:
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                self.assertEqual(remote.show_key(REPO / 'config.json', home=Path(home)), 0)
            key = remote.device_key(Path(home))
        self.assertIn(key, printed.getvalue())
        self.assertIn('--agent', printed.getvalue())


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        web = Path(self.temp.name) / 'web'
        web.mkdir()
        (web / 'index.html').write_text('<title>coc</title>', encoding='utf-8')
        self.device = FakeDevice()
        self.supervisor = remote.Supervisor(KEY, [sys.executable, '-c', 'pass'], self.device)
        self.server = remote.serve_dashboard(self.supervisor, web, 0)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def request(self, method, path, body=None, host=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        sent = {'Host': host or f'127.0.0.1:{self.port}', 'Content-Type': 'application/json', **(headers or {})}
        connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=sent)
        response = connection.getresponse()
        self.last_headers = dict(response.getheaders())
        return response.status, response.read().decode()

    def test_the_page_is_served_on_localhost_only(self):
        self.assertEqual(self.request('GET', '/'), (200, '<title>coc</title>'))
        self.assertEqual(self.last_headers.get('X-Frame-Options'), 'DENY')
        self.assertEqual(self.request('GET', '/', host='evil.example')[0], 403)
        self.assertEqual(self.request('GET', '/../remote.py')[0], 404)

    def test_the_installable_app_files_are_served_with_their_types(self):
        web = Path(self.temp.name) / 'web'
        (web / 'icons').mkdir()
        (web / 'manifest.webmanifest').write_text('{}', encoding='utf-8')
        (web / 'icons' / 'icon-192.png').write_bytes(b'png')
        (web / 'messages.js').write_text('export const example = 1;', encoding='utf-8')
        (web / 'messages_en.json').write_text('{}', encoding='utf-8')
        for path, kind in (('/manifest.webmanifest', 'application/manifest+json'), ('/icons/icon-192.png', 'image/png'),
                           ('/messages.js', 'text/javascript; charset=utf-8'),
                           ('/messages_en.json', 'application/json; charset=utf-8')):
            with self.subTest(path=path):
                self.assertEqual(self.request('GET', path)[0], 200)
                self.assertEqual(self.last_headers.get('Content-Type'), kind)

    def test_the_sample_deploy_maps_are_served_as_images(self):
        maps = Path(self.temp.name) / 'web' / 'maps'
        maps.mkdir()
        (maps / 'builder_stage2.jpg').write_bytes(b'jpg')
        self.assertEqual(self.request('GET', '/maps/builder_stage2.jpg')[0], 200)
        self.assertEqual(self.last_headers.get('Content-Type'), 'image/jpeg')
        self.assertEqual(self.request('GET', '/maps/home_scout.jpg')[0], 404)
        self.assertEqual(self.request('GET', '/maps/../../remote.py')[0], 404)

    def test_other_sites_cannot_post_commands_or_fill_the_log(self):
        body, sig = signed('open_coc')
        for headers in ({'Origin': 'https://evil.example'}, {'Content-Type': 'text/plain'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request('POST', '/api/command', {'body': body, 'sig': sig}, headers=headers)[0], 403)
        self.assertEqual(self.device.calls, [])
        self.assertEqual(self.supervisor.history(), [])
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        connection.putrequest('POST', '/api/command', skip_host=True)
        connection.putheader('Host', f'127.0.0.1:{self.port}')
        connection.putheader('Content-Type', 'application/json')
        connection.putheader('Content-Length', '-1')
        connection.endheaders()
        self.assertEqual(connection.getresponse().status, 413)

    def test_commands_need_the_device_signature(self):
        body, sig = signed('open_coc')
        self.assertEqual(self.request('POST', '/api/command', {'body': body, 'sig': sig})[0], 200)
        self.assertEqual(self.device.calls, ['open_coc'])
        body, sig = signed('kill_ld', key='coc_someone_else_entirely_0000000000')
        self.assertEqual(self.request('POST', '/api/command', {'body': body, 'sig': sig})[0], 403)
        self.assertEqual(self.device.calls, ['open_coc'])

    def test_the_log_stream_needs_the_channel_and_starts_with_history(self):
        self.assertEqual(self.request('GET', '/api/stream?channel=coc-wrong')[0], 403)
        self.supervisor.log('INFO: earlier line')
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        connection.request('GET', f'/api/stream?channel={self.supervisor.channel}')
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        received = []
        reader = threading.Thread(target=lambda: received.append(response.read1(4096).decode()), daemon=True)
        reader.start()
        self.assertTrue(until(lambda: received and 'earlier line' in received[0], 5))
        self.assertIn('event: state', received[0])


class DeviceKeyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)

    def test_a_device_keeps_one_key(self):
        key = remote.device_key(self.home)
        self.assertTrue(key.startswith('coc_'))
        self.assertGreaterEqual(len(key), 36)
        self.assertEqual(remote.device_key(self.home), key)

    def test_each_device_gets_its_own_key(self):
        other = Path(self.temp.name) / 'other'
        self.assertNotEqual(remote.device_key(self.home), remote.device_key(other))

    def test_the_channel_is_named_after_the_key_without_revealing_it(self):
        key = remote.device_key(self.home)
        channel = remote.channel_name(key)
        self.assertTrue(channel.startswith('coc-'))
        self.assertNotIn(key[4:12], channel)
        self.assertEqual(channel, remote.channel_name(key))


class CommandTests(unittest.TestCase):
    key = 'coc_testkey_0123456789abcdefghijklmnop'

    def signed(self, action, args=None, ts=None, nonce='n1', key=None):
        body = json.dumps({'action': action, 'args': args or {}, 'ts': ts or time.time(), 'nonce': nonce})
        return body, remote.sign(key or self.key, body)

    def test_a_signed_command_is_opened(self):
        body, sig = self.signed('stop')
        self.assertEqual(remote.open_command(self.key, body, sig, {})['action'], 'stop')

    def test_a_command_signed_with_another_key_is_refused(self):
        body, sig = self.signed('stop', key='coc_someone_else_entirely_0000000000')
        with self.assertRaises(remote.CommandRefused):
            remote.open_command(self.key, body, sig, {})

    def test_a_changed_body_is_refused(self):
        body, sig = self.signed('stop')
        with self.assertRaises(remote.CommandRefused):
            remote.open_command(self.key, body.replace('stop', 'kill_ld'), sig, {})

    def test_an_old_or_replayed_command_is_refused(self):
        body, sig = self.signed('stop', ts=time.time() - 3600)
        with self.assertRaises(remote.CommandRefused):
            remote.open_command(self.key, body, sig, {})
        seen = {}
        body, sig = self.signed('stop', nonce='once')
        remote.open_command(self.key, body, sig, seen)
        with self.assertRaises(remote.CommandRefused):
            remote.open_command(self.key, body, sig, seen)

    def test_a_recent_command_cannot_be_replayed_however_many_follow(self):
        seen = {}
        body, sig = self.signed('stop', nonce='first')
        remote.open_command(self.key, body, sig, seen)
        for number in range(1100):
            remote.open_command(self.key, *self.signed('stop', nonce=f'next-{number}'), seen)
        with self.assertRaises(remote.CommandRefused):
            remote.open_command(self.key, body, sig, seen)

    def test_a_timestamp_that_is_not_a_number_is_refused(self):
        body = json.dumps({'action': 'stop', 'args': {}, 'ts': float('nan'), 'nonce': 'nan'})
        with self.assertRaises(remote.CommandRefused):
            remote.open_command(self.key, body, remote.sign(self.key, body), {})

    def test_a_renewed_key_replaces_the_old_one(self):
        with tempfile.TemporaryDirectory() as home:
            old = remote.device_key(Path(home))
            new = remote.device_key(Path(home), renew=True)
            self.assertNotEqual(old, new)
            self.assertEqual(remote.device_key(Path(home)), new)

    def test_an_unknown_action_is_refused(self):
        body, sig = self.signed('format_disk')
        with self.assertRaises(remote.CommandRefused):
            remote.open_command(self.key, body, sig, {})


class BuildArgsTests(unittest.TestCase):
    def test_walls(self):
        self.assertEqual(remote.build_args({'task': 'walls', 'walls': 'both', 'first': 'home', 'hours': 2,
                                            'max_upgrades': 20}),
                         ['--walls', 'both', '--first', 'home', '--hours', '2', '--max-upgrades', '20', '--live'])

    def test_farm_with_chosen_resources(self):
        self.assertEqual(remote.build_args({'task': 'farm', 'builder': ['gold', 'cart'], 'home': ['elixir'],
                                            'first': 'builder', 'minutes': 45, 'searches': 30}),
                         ['--builder', 'gold,cart', '--home', 'elixir', '--first', 'builder', '--searches', '30',
                          '--minutes', '45', '--live'])

    def test_farm_with_nothing_chosen_fills_both_villages(self):
        self.assertEqual(remote.build_args({'task': 'farm'}), ['--until-full', '--live'])
        self.assertEqual(remote.build_args({'task': 'farm', 'dark': False}), ['--until-full', '--live'])

    def test_farm_can_fill_dark_elixir_too(self):
        self.assertEqual(remote.build_args({'task': 'farm', 'dark': True, 'hours': 2}),
                         ['--until-full', '--dark', '--hours', '2', '--live'])

    def test_single_attacks_and_a_dry_run(self):
        self.assertEqual(remote.build_args({'task': 'attack', 'village': 'home', 'cycles': 3}),
                         ['--mode', 'home', '--cycles', '3', '--live'])
        self.assertEqual(remote.build_args({'task': 'dry', 'village': 'both'}), ['--mode', 'both'])

    def test_recoveries_are_passed_on(self):
        self.assertEqual(remote.build_args({'task': 'walls', 'walls': 'home', 'max_recoveries': 5}),
                         ['--walls', 'home', '--max-recoveries', '5', '--live'])

    def test_bulk_walls_are_a_wall_task_switch(self):
        self.assertEqual(remote.build_args({'task': 'walls', 'walls': 'both', 'bulk_walls': True}),
                         ['--walls', 'both', '--bulk-walls', '--live'])
        self.assertEqual(remote.build_args({'task': 'walls', 'walls': 'home', 'bulk_walls': False}),
                         ['--walls', 'home', '--live'])
        for bad in ({'task': 'walls', 'walls': 'home', 'bulk_walls': 'yes'}, {'task': 'farm', 'bulk_walls': True}):
            with self.subTest(options=bad), self.assertRaises(remote.CommandRefused):
                remote.build_args(bad)

    def test_walls_can_come_back_after_some_attacks_and_pay_with_one_resource(self):
        self.assertEqual(remote.build_args({'task': 'walls', 'walls': 'home', 'wall_every': 5,
                                            'wall_resource': 'elixir'}),
                         ['--walls', 'home', '--wall-resource', 'elixir', '--wall-every', '5', '--live'])
        self.assertEqual(remote.build_args({'task': 'walls', 'walls': 'home', 'wall_every': 0,
                                            'wall_resource': 'both'}),
                         ['--walls', 'home', '--live'])
        for bad in ({'task': 'walls', 'walls': 'home', 'wall_resource': 'gems'},
                    {'task': 'walls', 'walls': 'home', 'wall_every': 1001},
                    {'task': 'walls', 'walls': 'home', 'wall_every': '5'},
                    {'task': 'farm', 'wall_resource': 'gold'}, {'task': 'attack', 'wall_every': 3}):
            with self.subTest(options=bad), self.assertRaises(remote.CommandRefused):
                remote.build_args(bad)

    def test_surrender_is_passed_on(self):
        self.assertEqual(remote.build_args({'task': 'farm', 'surrender': 'builder', 'surrender_after': 45}),
                         ['--until-full', '--surrender', 'builder', '--surrender-after', '45', '--live'])
        self.assertEqual(remote.build_args({'task': 'attack', 'village': 'home', 'surrender': 'both'}),
                         ['--mode', 'home', '--cycles', '1', '--surrender', 'both', '--live'])

    def test_the_ring_attack_is_passed_on(self):
        self.assertEqual(remote.build_args({'task': 'farm', 'home_style': 'ring', 'quake': True, 'surrender_at': 50}),
                         ['--until-full', '--ring', 'home', '--quake', '--surrender-at', '50', '--live'])
        self.assertEqual(remote.build_args({'task': 'farm', 'quake': False}), ['--until-full', '--live'])
        # 0 turns off the bot's default surrender at 50%, so it is passed on as well.
        self.assertEqual(remote.build_args({'task': 'farm', 'surrender_at': 0}),
                         ['--until-full', '--surrender-at', '0', '--live'])
        # Each village's style goes out as --ring/--stack with its villages.
        self.assertEqual(remote.build_args({'task': 'farm', 'home_style': 'stack', 'builder_style': 'stack',
                                            'surrender_stars': 2}),
                         ['--until-full', '--stack', 'both', '--surrender-stars', '2', '--live'])
        self.assertEqual(remote.build_args({'task': 'farm', 'home_style': 'ring', 'builder_style': 'stack'}),
                         ['--until-full', '--ring', 'home', '--stack', 'builder', '--live'])
        # The trophy floor is off at 0, so it only goes out when set.
        self.assertEqual(remote.build_args({'task': 'farm', 'trophy_floor': 2000}),
                         ['--until-full', '--trophy-floor', '2000', '--live'])
        self.assertEqual(remote.build_args({'task': 'farm', 'trophy_floor': 0}), ['--until-full', '--live'])
        for bad in ({'builder_style': 'pile'}, {'surrender_stars': 4}, {'builder_stack': True},
                    {'trophy_floor': 10_001}, {'trophy_floor': '2000'}):
            with self.subTest(bad=bad), self.assertRaises(remote.CommandRefused):
                remote.build_args({'task': 'farm', **bad})

    def test_anything_outside_the_commands_is_refused(self):
        for options in ({'task': 'shell'}, {'task': 'walls', 'walls': 'moon'}, {'task': 'walls', 'hours': -1},
                        {'task': 'farm', 'home': ['gems']}, {'task': 'walls', 'walls': 'home', 'extra': '--x'},
                        {'task': 'attack', 'cycles': '3; rm'}, {'task': 'farm', 'max_upgrades': 3},
                        {'task': 'walls', 'walls': 'home', 'hours': True}, {'task': 'farm', 'dark': 'yes'},
                        {'task': 'farm', 'dark': True, 'home': ['gold']}, {'task': 'walls', 'walls': 'home', 'dark': True},
                        {'task': 'farm', 'surrender': 'moon'}, {'task': 'farm', 'surrender_after': 30},
                        {'task': 'farm', 'surrender': 'home', 'surrender_after': 121},
                        {'task': 'dry', 'village': 'home', 'surrender': 'home'}, {'task': 'farm', 'ring': 'yes'},
                        {'task': 'farm', 'surrender_at': 100}, {'task': 'dry', 'village': 'home', 'quake': True}):
            with self.subTest(options=options), self.assertRaises(remote.CommandRefused):
                remote.build_args(options)


if __name__ == '__main__':
    unittest.main()
