import json
from pathlib import Path
import tempfile
import unittest

import device_control

REPO = Path(device_control.__file__).resolve().parent


class FakeEmulator:
    def __init__(self, running=True, stops=True):
        self.on, self.stops, self.calls = running, stops, []

    def running(self):
        return self.on

    def quit(self):
        self.calls.append('quit')
        self.on = not self.stops

    def launch(self):
        self.calls.append('launch')
        self.on = True


class DisplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        ld = folder / 'LDPlayer9'
        (ld / 'vms' / 'config').mkdir(parents=True)
        (ld / 'ldconsole.exe').write_bytes(b'')
        self.instance = ld / 'vms' / 'config' / 'leidian0.config'
        self.write_instance(1600, 900, 320)
        config = json.loads((REPO / 'config.json').read_text(encoding='utf-8'))
        self.config = folder / 'config.json'
        self.config.write_text(json.dumps({**config, 'ldconsole': str(ld / 'ldconsole.exe'), 'ld_index': 0}),
                               encoding='utf-8')
        self.store = folder / 'home' / 'display.json'
        self.emulator = FakeEmulator()
        self.display = device_control.Display(self.config, self.store, sleep=lambda seconds: None,
                                              emulator=lambda console, index: self.emulator)

    def write_instance(self, width, height, dpi, adb=1):
        self.instance.write_text(json.dumps({'propertySettings.phoneModel': 'X', 'advancedSettings.resolution':
                                             {'width': width, 'height': height}, 'advancedSettings.resolutionDpi': dpi,
                                             'basicSettings.adbDebug': adb}, indent=4), encoding='utf-8')

    def test_fitting_also_opens_adb_debugging_and_restoring_leaves_it_open(self):
        self.write_instance(1280, 720, 240, adb=0)
        self.assertTrue(self.display.fit())
        self.assertEqual(self.screen(), (1280, 720, 240, 1))
        self.assertFalse(self.store.exists())  # the screen already fitted: nothing of the user's to put back
        self.write_instance(1600, 900, 320, adb=0)
        self.display.fit()
        self.display.restore()
        self.assertEqual(self.screen(), (1600, 900, 320, 1))

    def screen(self):
        data = json.loads(self.instance.read_text(encoding='utf-8'))
        return (data['advancedSettings.resolution']['width'], data['advancedSettings.resolution']['height'],
                data['advancedSettings.resolutionDpi'], data['basicSettings.adbDebug'])

    def test_the_calibrated_screen_is_set_with_ldplayer_off_and_the_users_own_is_kept(self):
        self.assertEqual(self.display.status(), {'current': {'width': 1600, 'height': 900, 'dpi': 320},
                                                 'target': {'width': 1280, 'height': 720, 'dpi': 240}, 'saved': False})
        self.assertTrue(self.display.fit())
        self.assertEqual(self.screen(), (1280, 720, 240, 1))
        self.assertEqual(self.emulator.calls, ['quit', 'launch'])
        self.assertTrue(self.display.status()['saved'])
        self.assertFalse(self.display.fit())

    def test_the_users_screen_comes_back_and_ldplayer_only_restarts_if_it_was_on(self):
        self.display.fit()
        self.emulator.calls.clear()
        self.assertTrue(self.display.restore())
        self.assertEqual(self.screen(), (1600, 900, 320, 1))
        self.assertEqual(self.emulator.calls, ['quit', 'launch'])
        self.assertFalse(self.store.exists())
        self.assertFalse(self.display.restore())
        self.display.fit()
        self.emulator.on, self.emulator.calls = False, []
        self.assertTrue(self.display.restore())
        self.assertEqual(self.emulator.calls, [])

    def test_the_first_saved_screen_is_never_overwritten(self):
        self.display.fit()
        self.write_instance(1920, 1080, 280)
        self.display.fit()
        self.display.restore()
        self.assertEqual(self.screen(), (1600, 900, 320, 1))

    def test_a_screen_that_already_fits_changes_nothing(self):
        self.write_instance(1280, 720, 240)
        self.assertFalse(self.display.fit())
        self.assertEqual(self.emulator.calls, [])
        self.assertFalse(self.store.exists())

    def test_ldplayer_that_does_not_stop_leaves_the_file_alone(self):
        self.emulator.stops = False
        original = self.instance.read_text(encoding='utf-8')
        with self.assertRaises(device_control.SettingRefused):
            self.display.fit()
        self.assertEqual(self.instance.read_text(encoding='utf-8'), original)

    def test_a_damaged_saved_screen_is_not_written_back(self):
        self.display.fit()
        self.store.write_text(json.dumps({'path': str(self.instance), 'display': {'width': 'x', 'height': 1, 'dpi': 1}}),
                              encoding='utf-8')
        with self.assertRaises(device_control.SettingRefused):
            self.display.restore()
        self.assertEqual(self.screen(), (1280, 720, 240, 1))


if __name__ == '__main__':
    unittest.main()
