from coc_auto.paths import RESOURCE_ROOT, REPO_ROOT
from pathlib import Path as _TestPath
FIXTURES = _TestPath(__file__).resolve().parents[1] / "fixtures"
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from coc_auto import bot
from coc_auto import buildings


class MemoryRecoveryTests(unittest.TestCase):
    def test_memory_restart_happens_before_any_potentially_blocking_screen_wait(self):
        runner = object.__new__(bot.Bot)
        runner.refresh_if_low_memory = Mock(return_value=True)
        runner.settle = Mock(side_effect=bot.WaitTimeout('unreadable starved emulator'))
        runner.settle_after_memory()
        runner.refresh_if_low_memory.assert_called_once_with()
        runner.settle.assert_not_called()

    def test_without_restart_screen_settling_remains_required(self):
        events = []
        runner = object.__new__(bot.Bot)
        runner.refresh_if_low_memory = lambda: events.append('refresh') or False
        runner.settle = lambda: events.append('settle')
        runner.settle_after_memory()
        self.assertEqual(events, ['refresh', 'settle'])

    def test_restart_failure_propagates_to_the_bounded_recovery_controller(self):
        runner = object.__new__(bot.Bot)
        runner.refresh_if_low_memory = Mock(side_effect=bot.DeviceError('restart failed'))
        runner.settle = Mock()
        with self.assertRaises(bot.DeviceError):
            runner.settle_after_memory()
        runner.settle.assert_not_called()


class ListOverlapTests(unittest.TestCase):
    def test_shorter_swipes_keep_direction_and_do_not_mutate_shared_wall_config(self):
        spec = {'scroll': [[1110, 560], [1110, 300]]}
        self.assertEqual(buildings.overlapping_swipe(spec), [[1110, 560], [1110, 400]])
        self.assertEqual(buildings.overlapping_swipe(spec, True), [[1110, 300], [1110, 460]])
        self.assertEqual(spec['scroll'], [[1110, 560], [1110, 300]])
        self.assertEqual(buildings.overlapping_swipe({'scroll': [[5, 20], [5, 10]]}), [[5, 20], [5, 10]])


if __name__ == '__main__':
    unittest.main()
