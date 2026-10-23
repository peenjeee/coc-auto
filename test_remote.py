import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

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
        return json.loads(self.config.read_text(encoding='utf-8'))['screens'][name]

    def test_the_pins_of_every_deploy_screen_are_read(self):
        data = self.points.read()
        self.assertEqual(data['resolution'], [1280, 720])
        home, builder = data['screens']['home_scout'], data['screens']['builder_stage2']
        self.assertEqual((home['kind'], home['deploy'], home['points']),
                         ('spells', self.screen('home_scout')['deploy'], self.screen('home_scout')['auto']['spells']))
        self.assertEqual((builder['kind'], builder['bar']), ('points', self.screen('builder_stage2')['auto']['bar']))

    def test_moved_pins_are_written_and_the_rest_of_the_config_is_kept(self):
        before = json.loads(self.config.read_text(encoding='utf-8'))
        self.points.write('builder_scout', [200, 380], [[300, 200], [800, 150], [1100, 360]])
        after = json.loads(self.config.read_text(encoding='utf-8'))
        self.assertEqual(after['screens']['builder_scout']['deploy'], [200, 380])
        self.assertEqual(after['screens']['builder_scout']['auto']['points'], [[300, 200], [800, 150], [1100, 360]])
        after['screens']['builder_scout'] = before['screens']['builder_scout']
        self.assertEqual(after, before)
        self.assertTrue(self.config.with_suffix('.json.bak').is_file())

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
            (self.ld / name).write_bytes(b'')
        self.asked = []
        self.settings = remote.Settings(self.config, REPO, detector=lambda config: self.asked.append(config) or
                                        {'adb': 'C:/LD/adb.exe', 'problems': []})

    def stored(self):
        return json.loads(self.config.read_text(encoding='utf-8'))

    def setup(self, **changes):
        return {'adb': str(self.ld / 'adb.exe'), 'ldconsole': str(self.ld / 'ldconsole.exe'), 'serial': 'emulator-5556',
                'ld_index': 1, 'touch_device': '/dev/input/event5', **changes}

    def test_minimums_and_the_ldplayer_setup_are_read(self):
        data, config = self.settings.read(), self.stored()
        self.assertEqual(data['minimum'], {key: config['minimum'].get(key, 0) for key in ('gold', 'elixir', 'dark')})
        self.assertEqual(data['setup']['serial'], config['serial'])
        self.assertEqual(data['setup']['touch_device'], config['touch_device'])

    def test_minimums_are_saved_and_the_rest_of_the_config_is_kept(self):
        before = self.stored()
        self.settings.write_minimum({'gold': 300000, 'elixir': 250000, 'dark': 4000})
        after = self.stored()
        self.assertEqual(after['minimum'], {'gold': 300000, 'elixir': 250000, 'dark': 4000})
        self.assertEqual({**after, 'minimum': before['minimum']}, before)
        self.assertTrue(self.config.with_suffix('.json.bak').is_file())

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

    def test_anything_outside_the_commands_is_refused(self):
        for options in ({'task': 'shell'}, {'task': 'walls', 'walls': 'moon'}, {'task': 'walls', 'hours': -1},
                        {'task': 'farm', 'home': ['gems']}, {'task': 'walls', 'walls': 'home', 'extra': '--x'},
                        {'task': 'attack', 'cycles': '3; rm'}, {'task': 'farm', 'max_upgrades': 3},
                        {'task': 'walls', 'walls': 'home', 'hours': True}, {'task': 'farm', 'dark': 'yes'},
                        {'task': 'farm', 'dark': True, 'home': ['gold']}, {'task': 'walls', 'walls': 'home', 'dark': True}):
            with self.subTest(options=options), self.assertRaises(remote.CommandRefused):
                remote.build_args(options)


if __name__ == '__main__':
    unittest.main()
