"""Readiness safety regressions. No emulator or network commands are sent."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np
import bot


def frame(text='1234', scenery=0):
    image = np.full((90, 160, 3), scenery, np.uint8)
    image[4:44, 2:92] = 0
    for y in (20, 40):
        cv2.putText(image, text, (4, y), cv2.FONT_HERSHEY_SIMPLEX, .5, (255, 255, 255), 1)
    return image


class Clock:
    def __init__(self): self.now = 0
    def monotonic(self): return self.now
    def sleep(self, seconds): self.now += max(seconds, .1)


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.bot = bot.Bot.__new__(bot.Bot)
        self.bot.root = Path(self.temp.name)
        self.bot.config = {'minimum': {'gold': 100, 'elixir': 200, 'dark': 0},
            'screens': {'home_scout': {'loot': {'gold': [2, 4, 90, 18], 'elixir': [2, 24, 90, 18]}}}}
        self.bot.timeout = 60; self.bot.poll = .1
        self.bot.action = Mock(); self.bot.keep_scout_failure = Mock()
        self.clock = Clock()
        self.addCleanup(patch.stopall)
        patch('bot.time.monotonic', self.clock.monotonic).start()
        patch('bot.time.sleep', self.clock.sleep).start()

    def sequence(self, states):
        iterator = iter(states); last = states[-1]
        def observe():
            value = next(iterator, last)
            if isinstance(value, Exception): raise value
            return value
        self.bot.observe = Mock(side_effect=observe)

    def test_real_digit_hud_is_stable_while_scenery_changes(self):
        old, first, second = frame(), frame('5678', 40), frame('5678', 180)
        self.assertGreater(float(cv2.absdiff(first, second).mean()), 2)
        self.sequence([('home_scout', first), ('home_scout', second)])
        self.assertEqual(self.bot.wait_new_scout(old), 'home_scout')
        self.bot.action.assert_not_called()

    def test_animated_same_opponent_does_not_count_as_a_new_one(self):
        self.sequence([('home_scout', frame(scenery=120))])
        with self.assertRaises(bot.WaitTimeout): self.bot.wait_new_scout(frame())
        self.assertLess(self.clock.now, 8.2)
        self.bot.keep_scout_failure.assert_called_once()

    def test_a_few_noisy_mask_pixels_do_not_prove_a_new_opponent(self):
        signature=self.bot.scout_signature(frame())
        noisy=tuple(x.copy() for x in signature)
        noisy[0][2,2]=255-noisy[0][2,2]
        self.assertTrue(self.bot.same_scout_signature(signature,noisy))
        self.assertTrue(self.bot.same_scout_signature(signature,noisy,.05))
        self.bot.scout_signature=Mock(side_effect=[signature]+[noisy]*100)
        self.sequence([('home_scout',frame(scenery=120))])
        with self.assertRaises(bot.WaitTimeout):self.bot.wait_new_scout(frame())

    def test_identical_loot_after_observed_loading_is_allowed(self):
        self.sequence([('loading', frame()), ('home_scout', frame()), ('home_scout', frame(scenery=100))])
        self.assertEqual(self.bot.wait_new_scout(frame()), 'home_scout')

    def test_unknown_cloud_frame_is_tolerated(self):
        self.sequence([bot.UnknownScreen('clouds'), ('home_scout', frame()), ('home_scout', frame())])
        self.assertEqual(self.bot.wait_new_scout(frame()), 'home_scout')

    def test_loading_keeps_sixty_second_budget(self):
        self.sequence([('loading', frame())])
        with self.assertRaises(bot.WaitTimeout): self.bot.wait_new_scout(frame())
        self.assertGreaterEqual(self.clock.now, 60)
        self.assertLess(self.clock.now, 60.2)

    def test_slow_matchmaking_can_exceed_visible_budget(self):
        self.sequence([('loading', frame())] * 120 + [('home_scout', frame('5678'))] * 2)
        self.assertEqual(self.bot.wait_new_scout(frame()), 'home_scout')
        self.assertGreater(self.clock.now, 8)

    def test_unreadable_or_cloud_covered_digits_do_not_become_ready(self):
        for image in (np.zeros((90,160,3), np.uint8), np.full((90,160,3),255,np.uint8)):
            with self.subTest(value=int(image[0,0,0])):
                self.clock.now=0; self.sequence([('loading',image),('home_scout',image)])
                with self.assertRaises(bot.WaitTimeout): self.bot.wait_new_scout(frame())

    def test_changing_hud_does_not_become_ready(self):
        self.sequence([('home_scout',frame(str(3000+i))) for i in range(100)])
        with self.assertRaises(bot.WaitTimeout): self.bot.wait_new_scout(frame())

    def test_needs_two_consecutive_visible_hud_frames(self):
        a,b=frame('5678'),frame('9876')
        self.sequence([('home_scout',a),('loading',a),('home_scout',b),('home_scout',b)])
        self.assertEqual(self.bot.wait_new_scout(frame()), 'home_scout')
        self.assertEqual(self.bot.observe.call_count,4)

    def test_auto_started_battle_is_recoverable(self):
        self.sequence([('home_battle',frame())])
        with self.assertRaises(bot.WaitTimeout): self.bot.wait_new_scout(frame())
        self.bot.action.assert_not_called()

    def test_unrelated_screen_and_ambiguity_fail_closed(self):
        self.sequence([('builder_scout',frame())])
        with self.assertRaises(bot.UnexpectedScreen): self.bot.wait_new_scout(frame())
        self.bot.action.assert_not_called()
        self.bot.observe.side_effect=bot.BotError('Ambiguous screen')
        with self.assertRaisesRegex(bot.BotError,'Ambiguous'): self.bot.wait_new_scout(frame())

    def test_gem_offer_is_closed_without_purchase(self):
        self.sequence([('gem_offer',frame())])
        with self.assertRaises(bot.OutOfGold): self.bot.wait_new_scout(frame())
        self.bot.action.assert_called_once_with('gem_offer','close')

    def test_no_minimum_requires_no_ocr(self):
        self.bot.config['minimum']={}
        self.bot.reader=Mock(side_effect=AssertionError('OCR should not run'))
        self.sequence([('home_scout',frame('5678'))]*2)
        self.assertEqual(self.bot.wait_new_scout(frame()),'home_scout')
        self.bot.reader.assert_not_called()

    def test_pale_yellow_gold_and_every_trailing_digit_remain_distinguishable(self):
        for color in ((255,255,255),(153,255,255)):
            signatures=[]
            for digit in range(10):
                im=frame(str(3000+digit));im[np.all(im==255,axis=2)]=color
                signature=self.bot.scout_signature(im)
                self.assertFalse(any(self.bot.same_scout_signature(old,signature) for old in signatures))
                signatures.append(signature)

    def test_retry_once_then_cleanup_without_deploying(self):
        self.bot.wait_new_scout=Mock(side_effect=bot.WaitTimeout('not ready'))
        self.bot.leave_home_search=Mock();self.sequence([('home_scout',frame())])
        with self.assertRaises(bot.WaitTimeout):self.bot.next_home_scout(frame())
        self.assertEqual(self.bot.action.call_count,2)
        self.bot.leave_home_search.assert_called_once_with()

    def test_lost_tap_can_succeed_on_second_attempt(self):
        self.bot.wait_new_scout=Mock(side_effect=[bot.WaitTimeout('not ready'),'home_scout'])
        self.bot.leave_home_search=Mock();self.sequence([('home_scout',frame())])
        self.bot.next_home_scout(frame())
        self.assertEqual(self.bot.action.call_count,2)
        self.bot.leave_home_search.assert_not_called()

    def test_auto_started_battle_cleanup_does_not_retry_next(self):
        self.bot.wait_new_scout=Mock(side_effect=bot.WaitTimeout('battle'))
        self.bot.leave_home_search=Mock();self.sequence([('home_battle',frame())])
        with self.assertRaises(bot.WaitTimeout):self.bot.next_home_scout(frame())
        self.bot.action.assert_called_once_with('home_scout','next')
        self.bot.leave_home_search.assert_called_once_with()

    def test_battle_racing_the_next_tap_uses_cleanup(self):
        self.bot.action.side_effect=bot.UnexpectedScreen('battle during guarded Next')
        self.bot.wait_new_scout=Mock();self.bot.leave_home_search=Mock()
        self.sequence([('home_battle',frame())])
        with self.assertRaises(bot.WaitTimeout):self.bot.next_home_scout(frame())
        self.bot.wait_new_scout.assert_not_called()
        self.bot.leave_home_search.assert_called_once_with()

    def test_cloud_timeout_never_clicks_unknown_screen(self):
        self.bot.wait_new_scout=Mock(side_effect=bot.WaitTimeout('clouds'))
        self.bot.leave_home_search=Mock();self.sequence([('loading',frame())])
        with self.assertRaises(bot.WaitTimeout):self.bot.next_home_scout(frame())
        self.bot.action.assert_called_once_with('home_scout','next')
        self.bot.leave_home_search.assert_not_called()

    def test_unknown_frame_after_timeout_preserves_recovery(self):
        original=bot.WaitTimeout('readiness timeout')
        self.bot.wait_new_scout=Mock(side_effect=original)
        self.bot.observe=Mock(side_effect=bot.UnknownScreen('clouds'))
        with self.assertRaises(bot.WaitTimeout) as caught:self.bot.next_home_scout(frame())
        self.assertIs(caught.exception,original)

    def test_cleanup_uses_guarded_surrender_and_return(self):
        self.sequence([('home_battle',frame())]);self.bot.surrender_battle=Mock(return_value='home_result')
        self.bot.tap_until=Mock(return_value='home')
        self.bot.leave_home_search()
        self.bot.surrender_battle.assert_called_once_with('home_battle',{'home_result','home'})
        self.bot.tap_until.assert_called_once_with('home_result','return',{'home'},30)

    def test_cleanup_reobserves_scout_to_battle_guard_race(self):
        self.sequence([('home_scout',frame()),('home_battle',frame())])
        self.bot.config['screens']['home_battle']={'actions':{'surrender':[92,537]}}
        self.bot.guarded_tap=Mock(side_effect=bot.UnexpectedScreen('race'))
        self.bot.surrender_battle=Mock(return_value='home')
        self.bot.leave_home_search()
        self.bot.guarded_tap.assert_called_once_with({'home_scout'},[92,537])
        self.bot.surrender_battle.assert_called_once_with('home_battle',{'home_result','home'})

    def test_cleanup_scout_uses_existing_battle_coordinate_with_scout_guard(self):
        self.sequence([('home_scout',frame())])
        self.bot.config['screens']['home_battle']={'actions':{'surrender':[92,537]}}
        self.bot.guarded_tap=Mock();self.bot.wait=Mock(return_value='surrender_confirm')
        self.bot.tap_until=Mock(return_value='home')
        self.bot.leave_home_search()
        self.bot.guarded_tap.assert_called_once_with({'home_scout'},[92,537])
        self.bot.tap_until.assert_called_once_with('surrender_confirm','confirm',{'home_result','home'},30,
                                                 passing={'home_scout','home_battle'})

    def test_evidence_is_bounded_and_written(self):
        bot.Bot.keep_scout_failure(self.bot,frame(),frame('5678'),'test')
        self.assertEqual(len(list((self.bot.root/'captures').iterdir())),3)
        bot.Bot.keep_scout_failure(self.bot,frame(),frame('9876'),'test2')
        self.assertEqual(len(list((self.bot.root/'captures').iterdir())),3)




class RecordedHUDTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.runner = bot.Bot.__new__(bot.Bot)
        self.runner.root = Path(folder.name)
        self.runner.config = {'minimum': {'gold': 100, 'elixir': 200}, 'screens': {
            'home_scout': {'loot': {'gold': [0, 0, 150, 26], 'elixir': [0, 26, 150, 26]}}}}
        self.runner.timeout, self.runner.poll = 60, .1
        self.runner.keep_scout_failure = Mock()
        self.runner.action = Mock()

    def recorded(self, name):
        image = cv2.imread(str(Path(__file__).parent / 'fixtures/opponent-readiness' / f'{name}.png'))
        self.assertIsNotNone(image)
        return image

    def test_recorded_loot_is_ready_while_the_original_scenery_keeps_moving(self):
        first, second = self.recorded('stable-a'), self.recorded('stable-b')
        self.runner.observe = Mock(side_effect=[('home_scout', first), ('home_scout', second)])
        self.assertEqual(self.runner.wait_new_scout(self.recorded('different-opponent')), 'home_scout')
        self.runner.action.assert_not_called()

    def test_recorded_pixel_noise_cannot_prove_a_new_opponent(self):
        before, after = self.recorded('noise-before'), self.recorded('noise-after')
        a, b = self.runner.scout_signature(before), self.runner.scout_signature(after)
        self.assertEqual([np.count_nonzero(x != y) for x, y in zip(a, b)], [0, 1])
        self.runner.observe = Mock(return_value=('home_scout', after))
        clock = Clock()
        with patch('bot.time.monotonic', clock.monotonic), patch('bot.time.sleep', clock.sleep):
            with self.assertRaises(bot.WaitTimeout):
                self.runner.wait_new_scout(before)
        self.runner.action.assert_not_called()


if __name__ == '__main__':
    unittest.main()
