from pathlib import Path
import tempfile
import unittest

import ldplayer

LIST2 = '0,LDPlayer,0,0,1,1234,5678,1280,720,240\n1,Farm 2,0,0,0,-1,-1,1280,720,240\n'
DEVICES = 'List of devices attached\nemulator-5554\tdevice\nemulator-5556\toffline\n\n'
GETEVENT = '''add device 1: /dev/input/event2
  name:     "qwerty2"
  events:
    KEY (0001): KEY_ESC
add device 2: /dev/input/event4
  name:     "ldtouch"
  events:
    ABS (0003): ABS_MT_SLOT : value 0, min 0, max 9
                ABS_MT_POSITION_X     : value 0, min 0, max 1279, fuzz 0, flat 0, resolution 0
                ABS_MT_POSITION_Y     : value 0, min 0, max 719, fuzz 0, flat 0, resolution 0
'''


class ParserTests(unittest.TestCase):
    def test_instances_from_ldconsole(self):
        self.assertEqual(ldplayer.parse_instances(LIST2), [
            {'index': 0, 'name': 'LDPlayer', 'running': True, 'serial': 'emulator-5554'},
            {'index': 1, 'name': 'Farm 2', 'running': False, 'serial': 'emulator-5556'}])

    def test_only_connected_adb_devices_count(self):
        self.assertEqual(ldplayer.parse_devices(DEVICES), ['emulator-5554'])

    def test_the_touchscreen_is_the_node_with_multitouch_positions(self):
        self.assertEqual(ldplayer.parse_touch(GETEVENT), '/dev/input/event4')
        self.assertIsNone(ldplayer.parse_touch('add device 1: /dev/input/event2\n  KEY_ESC\n'))

    def test_the_touch_range_of_a_node_is_read(self):
        self.assertEqual(ldplayer.touch_range(GETEVENT, '/dev/input/event4'), (1279, 719))
        self.assertIsNone(ldplayer.touch_range(GETEVENT, '/dev/input/event2'))
        self.assertIsNone(ldplayer.touch_range(GETEVENT, '/dev/input/event9'))

    def test_a_touch_problem_says_what_does_not_fit(self):
        self.assertIsNone(ldplayer.touch_problem('x86_64\n' + GETEVENT, '/dev/input/event4', [1280, 720]))
        for kernel, node, size, words in (('i686', '/dev/input/event4', [1280, 720], '32-bit'),
                                          ('x86_64', '/dev/input/event9', [1280, 720], 'event9'),
                                          ('x86_64', '/dev/input/event2', [1280, 720], 'event2'),
                                          ('x86_64', '/dev/input/event4', [1600, 900], '1279x719')):
            with self.subTest(kernel=kernel, node=node, size=size):
                self.assertIn(words, ldplayer.touch_problem(kernel + '\n' + GETEVENT, node, size))

    def test_screen_size_prefers_the_override(self):
        self.assertEqual(ldplayer.parse_size('Physical size: 720x1280\nOverride size: 1280x720\n'), [1280, 720])
        self.assertIsNone(ldplayer.parse_size('error: no devices'))


class DetectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / 'LDPlayer' / 'LDPlayer9'
        self.folder.mkdir(parents=True)
        for name in ('adb.exe', 'ldconsole.exe', 'dnplayer.exe'):
            (self.folder / name).write_bytes(b'')
        self.calls = []

    def runner(self, outputs):
        def run(command):
            self.calls.append(command)
            for key, output in outputs.items():
                if key in ' '.join(command):
                    return output
            return ''
        return run

    def test_a_running_ldplayer_is_found_with_its_instance_touchscreen_and_size(self):
        run = self.runner({'Get-Process': str(self.folder / 'dnplayer.exe') + '\n', 'list2': LIST2, 'devices': DEVICES,
                           'getevent': GETEVENT, 'wm size': 'Physical size: 1280x720\n'})
        found = ldplayer.detect({}, run, roots=[])
        self.assertEqual(found['adb'], str((self.folder / 'adb.exe').resolve()))
        self.assertEqual(found['ldconsole'], str((self.folder / 'ldconsole.exe').resolve()))
        self.assertEqual((found['ld_index'], found['serial'], found['touch_device'], found['resolution']),
                         (0, 'emulator-5554', '/dev/input/event4', [1280, 720]))
        self.assertEqual(found['problems'], [])

    def test_an_installed_ldplayer_is_found_in_the_usual_folders(self):
        found = ldplayer.detect({}, self.runner({'list2': LIST2}), roots=[self.root])
        self.assertEqual(found['folders'], [str(self.folder.resolve())])
        self.assertIn('emulator-5554', found['problems'][0])

    def test_a_touchscreen_for_another_screen_size_is_reported(self):
        wide = GETEVENT.replace('max 1279', 'max 1599').replace('max 719', 'max 899')
        run = self.runner({'Get-Process': str(self.folder / 'dnplayer.exe'), 'list2': LIST2, 'devices': DEVICES,
                           'getevent': wide, 'wm size': 'Physical size: 1280x720\n'})
        found = ldplayer.detect({}, run, roots=[])
        self.assertTrue(any('1599x899' in problem for problem in found['problems']))

    def test_ldplayer_with_adb_debugging_off_is_reported(self):
        config = self.folder / 'vms' / 'config'
        config.mkdir(parents=True)
        (config / 'leidian0.config').write_text('{"basicSettings.adbDebug": 0}', encoding='utf-8')
        run = self.runner({'Get-Process': str(self.folder / 'dnplayer.exe'), 'list2': LIST2})
        found = ldplayer.detect({}, run, roots=[])
        self.assertTrue(any('ADB' in problem and 'debug' in problem.lower() for problem in found['problems']))

    def test_a_wrong_screen_size_is_reported(self):
        run = self.runner({'Get-Process': str(self.folder / 'dnplayer.exe'), 'list2': LIST2, 'devices': DEVICES,
                           'getevent': GETEVENT, 'wm size': 'Physical size: 1920x1080\n'})
        found = ldplayer.detect({}, run, roots=[])
        self.assertTrue(any('1280x720' in problem for problem in found['problems']))

    def test_no_ldplayer_is_reported_not_guessed(self):
        found = ldplayer.detect({}, self.runner({}), roots=[self.root / 'nothing'])
        self.assertEqual(found['folders'], [])
        self.assertNotIn('adb', found)
        self.assertTrue(found['problems'])

    def test_a_failing_tool_is_a_problem_not_a_crash(self):
        def broken(command):
            if 'list2' in command:
                raise OSError('access denied')
            return str(self.folder / 'dnplayer.exe') if 'Get-Process' in ' '.join(command) else ''
        found = ldplayer.detect({}, broken, roots=[])
        self.assertTrue(any('ldconsole' in problem for problem in found['problems']))


INSTANCE = ('{\r\n    "propertySettings.phoneModel": "SM-S9110",\r\n    "advancedSettings.resolution": {\r\n'
            '        "width": 1600,\r\n        "height": 900\r\n    },\r\n    "advancedSettings.resolutionDpi": 320,\r\n'
            '    "basicSettings.adbDebug": 1\r\n}')


class DisplayFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'leidian0.config'
        self.path.write_bytes(INSTANCE.encode())

    def test_the_instance_config_sits_in_the_ldplayer_folder(self):
        self.assertEqual(ldplayer.instance_config(r'C:\LDPlayer\LDPlayer9\ldconsole.exe', 2),
                         Path(r'C:\LDPlayer\LDPlayer9\vms\config\leidian2.config'))

    def test_the_screen_is_read(self):
        self.assertEqual(ldplayer.read_display(self.path), {'width': 1600, 'height': 900, 'dpi': 320})

    def test_only_the_screen_changes_and_the_file_keeps_ldplayers_format(self):
        ldplayer.write_display(self.path, {'width': 1280, 'height': 720, 'dpi': 240})
        expected = INSTANCE.replace('1600', '1280').replace('900', '720').replace('320', '240')
        self.assertEqual(self.path.read_bytes(), expected.encode())

    def test_adb_debugging_is_read_and_can_be_turned_on_with_the_screen(self):
        self.assertEqual(ldplayer.adb_debug(self.path), 1)
        self.path.write_bytes(INSTANCE.replace('"basicSettings.adbDebug": 1', '"basicSettings.adbDebug": 0').encode())
        self.assertEqual(ldplayer.adb_debug(self.path), 0)
        ldplayer.write_display(self.path, {'width': 1280, 'height': 720, 'dpi': 240}, adb_debug=1)
        self.assertEqual(ldplayer.adb_debug(self.path), 1)
        self.path.write_text('{}', encoding='utf-8')
        self.assertIsNone(ldplayer.adb_debug(self.path))

    def test_a_config_without_screen_keys_is_refused(self):
        self.path.write_text('{"basicSettings.adbDebug": 1}', encoding='utf-8')
        with self.assertRaises(ValueError):
            ldplayer.read_display(self.path)


if __name__ == '__main__':
    unittest.main()
