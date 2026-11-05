"""Exercise the real recovery controller with virtual cloud timeouts, without ADB or sleeping."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import bot


class CloudRecoveryTests(unittest.TestCase):
    def runner(self, app_works=False, emulator_works=False, with_emulator=True):
        state = SimpleNamespace(cloud=False, front=True, up=True, elapsed=0, events=[], waits=[])
        runner = object.__new__(bot.Bot)
        runner.live, runner.poll, runner.timeout, runner.progress = True, .05, 240, 0
        runner.root, runner.config = Path('.'), {'resolution': [1280, 720], 'screens': {}}
        runner.check_touch = Mock()
        runner.release_touch = Mock(side_effect=lambda: state.events.append('release'))
        runner.current_screen = lambda: None if state.cloud else 'home'
        runner.game_ready = lambda: True
        def sleep(seconds): state.elapsed += seconds
        def wait(targets, allowed, timeout):
            state.waits.append(timeout)
            sleep(timeout)
            raise bot.WaitTimeout('still loading')
        runner.wait = wait
        def stop_game():
            state.events.append('stop game')
            state.front = False
        def launch(activity):
            state.events.append('launch game')
            state.front = True
            if app_works or (emulator_works and 'launch emulator' in state.events): state.cloud = False
        runner.device = SimpleNamespace(connected=lambda: state.up, booted=lambda: state.up,
            game_in_front=lambda: state.front, stop_game=Mock(side_effect=stop_game),
            launch=Mock(side_effect=launch), tap_many=Mock())
        def quit_emulator():
            state.events.append('quit emulator')
            state.up = state.front = False
        def launch_emulator():
            state.events.append('launch emulator')
            state.up = True
        runner.emulator = SimpleNamespace(quit=Mock(side_effect=quit_emulator), running=lambda: state.up,
            vm_running=lambda: False, refit=Mock(return_value=False), launch=Mock(side_effect=launch_emulator)) if with_emulator else None
        def job(searches):
            if state.cloud or not state.events.count('launch game'):
                state.cloud = True
                sleep(60)
                raise bot.WaitTimeout('home_scout timeout')
            return 13
        runner.run_until_full = job
        return runner, state, sleep

    def run_virtual(self, runner, state, sleep, budget=20):
        with patch.object(bot, 'memory_critical', return_value=False), \
                patch.object(bot.time, 'monotonic', side_effect=lambda: state.elapsed), \
                patch.object(bot.time, 'sleep', side_effect=sleep):
            return runner.run_until_full_resilient(0, max_recoveries=budget)

    def test_cloud_timeout_restarts_the_game_after_one_failed_settle(self):
        runner, state, sleep = self.runner(app_works=True)
        self.assertEqual(self.run_virtual(runner, state, sleep), 13)
        self.assertEqual(state.waits, [240])
        self.assertEqual(state.elapsed, 300)  # 60 s search + one 240 s settle, rather than twenty.
        runner.device.stop_game.assert_called_once_with()
        runner.emulator.quit.assert_not_called()
        runner.device.tap_many.assert_not_called()
        self.assertLess(state.events.index('release'), state.events.index('stop game'))

    def test_failed_app_restart_escalates_to_emulator_without_four_launch_waits(self):
        runner, state, sleep = self.runner(emulator_works=True)
        self.assertEqual(self.run_virtual(runner, state, sleep), 13)
        self.assertEqual(state.waits, [240, 240])
        self.assertEqual(state.events.count('quit emulator'), 1)
        self.assertLess(state.events.index('stop game'), state.events.index('quit emulator'))
        self.assertEqual(runner.device.launch.call_count, 2)
        runner.device.tap_many.assert_not_called()

    def test_permanent_clouds_still_exhaust_the_configured_budget(self):
        runner, state, sleep = self.runner()
        with self.assertRaises(bot.WaitTimeout): self.run_virtual(runner, state, sleep, budget=3)
        self.assertEqual(state.events.count('stop game'), 1)
        self.assertEqual(state.events.count('quit emulator'), 1)
        self.assertEqual(state.waits, [240, 240, 240])

    def test_no_emulator_retries_app_restart_within_the_same_budget(self):
        runner, state, sleep = self.runner(with_emulator=False)
        with self.assertRaises(bot.WaitTimeout): self.run_virtual(runner, state, sleep, budget=3)
        self.assertEqual(state.events.count('stop game'), 2)
        runner.device.tap_many.assert_not_called()

    def test_zero_recovery_budget_does_not_restart_anything(self):
        runner, state, sleep = self.runner()
        with self.assertRaises(bot.WaitTimeout): self.run_virtual(runner, state, sleep, budget=0)
        self.assertEqual(state.waits, [])
        runner.device.stop_game.assert_not_called()
        runner.emulator.quit.assert_not_called()

    def test_progress_between_cloud_stalls_gets_a_fresh_recovery_budget(self):
        runner, state, sleep = self.runner(app_works=True)
        attempts = 0
        def job(searches):
            nonlocal attempts
            attempts += 1
            if attempts <= 2:
                if attempts == 2: runner.progress += 1
                state.cloud = True
                raise bot.WaitTimeout('home_scout timeout')
            return 14
        runner.run_until_full = job
        self.assertEqual(self.run_virtual(runner, state, sleep, budget=2), 14)
        self.assertEqual(state.events.count('stop game'), 2)
        runner.emulator.quit.assert_not_called()
        self.assertEqual(state.waits, [240, 240])

    def test_first_recovery_limit_stops_before_any_restart(self):
        runner, state, sleep = self.runner()
        with self.assertRaises(bot.WaitTimeout): self.run_virtual(runner, state, sleep, budget=1)
        self.assertEqual(state.waits, [240])
        runner.device.stop_game.assert_not_called()
        runner.emulator.quit.assert_not_called()

    def test_dry_run_cannot_force_stop_the_game_or_emulator(self):
        runner, state, sleep = self.runner()
        runner.live = False
        for emulator in [False, True]:
            with self.assertRaises(bot.WaitTimeout): runner.restart_after_stall(emulator=emulator)
        runner.device.stop_game.assert_not_called()
        runner.emulator.quit.assert_not_called()
