from coc_auto.paths import RESOURCE_ROOT
from contextlib import closing
import json
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from coc_auto import accounts
from coc_auto import bot
from coc_auto import village_export as export
from coc_auto.dashboard_metrics import Statistics

ROOT = RESOURCE_ROOT
MAIN, OTHER, SECOND = ('MainVillage', '#PPPPPPPP'), ('NotAsked', '#GGGGGGGG'), ('SecondOne', '#QQQQQQQQ')
CARDS = {117: MAIN, 246: OTHER, 375: SECOND}
LOG_IN_CARD = (573, 678)


class FakeGame:
    """The game's profile, Settings and account list as the bot sees them: its own templates on plain frames."""

    def __init__(self, loaded=MAIN):
        self.state, self.loaded, self.inputs = 'home', loaded, []
        self.seconds, self.slides, self.settles, self.reopened = 0., 0, 0, 0
        # What the game does after a card is tapped: '' loads it, 'closes' closes itself first and comes back on that
        # account, 'reverts' closes and comes back on the account it had.
        self.after_tap = ''
        self.names = {top: account[0] for top, account in CARDS.items()}
        self.loads = dict(CARDS)
        self.frames = {name: np.zeros((720, 1280, 3), np.uint8)
                       for name in ('home', 'profile', 'settings', 'sliding', 'list', 'loading')}

        def place(state, name, x, y):
            template = cv2.imread(str(ROOT / 'calibration/export' / name))
            h, w = template.shape[:2]
            self.frames[state][y:y + h, x:x + w] = template

        for state in ('profile', 'settings'):
            place(state, 'close.png', 1096, 34)
        place('settings', 'more-id.png', 551, 617)
        place('settings', 'switch.png', 822, 116)
        # The list slides in from the right: 86 pixels short of its place, then in place.
        for state, shift in (('sliding', 86), ('list', 0)):
            self.frames[state][:, 576 + shift:] = 240
            place(state, 'supercell.png', 162 + shift, 334)
            for top in (*CARDS, LOG_IN_CARD[0]):
                self.frames[state][top:top + 105, 601 + shift:] = 255
            self.frames[state][:, 1255 + shift:] = 240
        self.runner = SimpleNamespace(device=self, detector=SimpleNamespace(detect=self.detect), settle=self.settle,
                                      recover=self.recover, config={'tesseract': sys.executable})

    def detect(self, image):
        if self.state != 'home':
            raise bot.UnknownScreen('Layar tidak dikenal; tidak ada input dikirim')
        return 'home'

    def capture(self):
        self.seconds += .05
        frame = self.frames[self.state].copy()
        if self.state == 'sliding':
            self.slides += 1
            if self.slides >= 2:
                self.state = 'list'
        return frame

    def sleep(self, seconds):
        self.seconds += seconds

    def tap(self, point):
        x, y = point
        self.inputs.append((self.state, x, y))
        if self.state == 'home' and point == [60, 60]:
            self.state = 'profile'
        elif self.state == 'profile' and point == [1010, 60]:
            self.state = 'settings'
        elif self.state == 'settings' and 822 <= x < 866 and 116 <= y < 160:
            self.state = 'sliding'
        elif self.state == 'list' and point == [42, 47]:
            self.state = 'settings'
        elif self.state == 'list' and x >= 601:
            card = next((top for top in (*CARDS, LOG_IN_CARD[0]) if top <= y < top + 105), None)
            if card in self.loads:
                if self.after_tap != 'reverts':
                    self.loaded = self.loads[card]
                self.state = 'closed' if self.after_tap else 'loading'
        elif self.state in ('profile', 'settings') and point == [1119, 56]:
            self.state = 'home'

    def settle(self):
        self.settles += 1
        if self.state == 'closed':
            raise bot.GameClosed('CoC tertutup saat menunggu layar builder/home')
        if self.state == 'loading':
            self.state = 'home'

    def recover(self):
        self.reopened += 1
        self.state = 'home'

    def card_name(self, image, card, program):
        # A name is only ever read from the list in its place.
        assert self.state == 'list' and np.array_equal(image, self.frames['list'])
        return self.names.get(card[0], 'LN')

    def card_taps(self):
        return [(x, y) for state, x, y in self.inputs if state == 'list' and x >= 576]


class ParseTests(unittest.TestCase):
    def test_accounts_are_names_with_their_player_tags_in_the_order_given(self):
        self.assertEqual(accounts.parse(' Main Village#pppppppp , Second.One#QQQQQQQQ'),
                         (('Main Village', '#PPPPPPPP'), ('Second.One', '#QQQQQQQQ')))

    def test_a_missing_or_impossible_tag_and_a_repeated_account_are_refused(self):
        for text in ('', 'MainVillage', 'MainVillage#', '#PPPPPPPP', 'MainVillage#PPPPPPPP,', 'MainVillage#PPPPOPPP',
                     'MainVillage#2P', 'Main,Village#PPPPPPPP', 'A#PPPPPPPP,a#QQQQQQQQ', 'A#PPPPPPPP,B#pppppppp',
                     'x' * 41 + '#PPPPPPPP'):
            with self.subTest(text=text), self.assertRaises(bot.BotError):
                accounts.parse(text)


class VillageTests(unittest.TestCase):
    def test_the_export_read_for_an_account_becomes_the_devices_village(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        data = {'tag': SECOND[1], 'timestamp': 1000,
                **{section: [] for sections in export.SECTIONS.values() for section in sections}}
        runner = SimpleNamespace(root=root, config={'language': 'en'})
        scanner = patch.object(export, 'Scanner', lambda runner: SimpleNamespace(read=lambda: data))
        # A device that never ran the dashboard has no scans to keep; the tag is known all the same.
        with scanner:
            self.assertEqual(accounts.sync(runner), SECOND[1])
        self.assertEqual(export.current_tag(root / '.coc-auto'), SECOND[1])
        Statistics(root / '.coc-auto' / 'stats.sqlite3').db.close()
        with scanner:
            self.assertEqual(accounts.sync(runner), SECOND[1])
        with closing(sqlite3.connect(root / '.coc-auto' / 'stats.sqlite3')) as db:
            self.assertEqual(sorted(found[0] for found in db.execute('SELECT list FROM scans')), sorted(export.SECTIONS))
        self.assertIsNone(export.current_tag(root / 'nowhere'))

    def test_each_account_takes_the_picks_filed_for_its_own_village(self):
        own = {'buildings': {'targets': [{'id': 'cannon', 'name': 'Cannon', 'level': None}], 'keep_builders': 1}}
        config = {**bot.load_config(ROOT / 'config.json'),
                  'buildings': {'targets': [{'id': 'archer_tower', 'name': 'Archer Tower', 'level': 3}],
                                'keep_builders': 0},
                  'lab': {'targets': [{'id': 'giant', 'name': 'Giant', 'level': None}]},
                  'account_picks': {SECOND[1]: own, OTHER[1]: {'lab': {'targets': []}}}}
        found = accounts.picks(config, ROOT, (MAIN, SECOND, OTHER))
        self.assertEqual(list(found), [SECOND[1]])
        # Its own list, and nothing of the picks another village left on the page.
        self.assertEqual((found[SECOND[1]]['buildings'], found[SECOND[1]]['lab']), (own['buildings'], {'targets': []}))
        with self.assertRaisesRegex(bot.BotError, 'Belum ada pilihan upgrade'):
            accounts.picks(config, ROOT, (MAIN, OTHER))

    def test_the_devices_account_list_is_checked_like_the_command_line(self):
        self.assertEqual(accounts.registry([{'name': ' Main Village ', 'tag': 'pppppppp', 'enabled': False}]),
                         [{'name': 'Main Village', 'tag': '#PPPPPPPP', 'enabled': False}])
        for value in ('MainVillage#PPPPPPPP', [{'name': 'MainVillage', 'tag': '#PPPPPPPP', 'enabled': 1}],
                      [{'name': 'Main,Village', 'tag': '#PPPPPPPP', 'enabled': True}]):
            with self.subTest(value=value), self.assertRaises(bot.BotError):
                accounts.registry(value)


class ListTests(unittest.TestCase):
    def test_the_log_in_card_under_the_accounts_is_never_an_account_card(self):
        game = FakeGame()
        self.assertEqual(accounts.cards(game.frames['list']), [(117, 222), (246, 351), (375, 480)])
        self.assertEqual(accounts.cards(game.frames['home']), [])

    def test_only_the_one_card_with_exactly_that_name_is_picked(self):
        frame = FakeGame().frames['list']
        names = {117: 'MainVillage', 246: 'Main Village2', 375: 'mainvillage3'}
        self.assertEqual(accounts.pick(frame, 'main village', lambda card: names[card[0]]), (117, 222))
        for wanted in ('MainVillag', 'MainVillage1', 'Village', ''):
            with self.subTest(wanted=wanted), self.assertRaises(bot.BotError):
                accounts.pick(frame, wanted, lambda card: names[card[0]])
        twins = {117: 'Twin', 246: 'twin', 375: 'Other'}
        with self.assertRaisesRegex(bot.BotError, 'tepat satu kali'):
            accounts.pick(frame, 'Twin', lambda card: twins[card[0]])

    def test_a_band_of_another_height_or_a_wider_gap_ends_the_cards(self):
        def listing(*tops):
            frame = np.full((720, 1280, 3), 240, np.uint8)
            for top in tops:
                frame[top:top + 105, 601:1255] = 255
            return frame

        self.assertEqual(accounts.cards(listing(117, 262)), [(117, 222), (262, 367)])
        self.assertEqual(accounts.cards(listing(117, 263)), [(117, 222)])
        # A list that does not start with a whole card is not the calibrated one: nothing on it is a card.
        odd = listing(117, 246)
        odd[40:80, 601:1255] = 255
        self.assertEqual(accounts.cards(odd), [])
        clipped = listing(117, 246)
        clipped[680:, 601:1255] = 255
        self.assertEqual(accounts.cards(clipped), [(117, 222), (246, 351)])

    def test_a_fifth_card_that_runs_off_the_screen_is_a_card_while_its_name_still_shows(self):
        frame = np.full((720, 1280, 3), 240, np.uint8)
        for top in (117, 246, 375, 504, 633):
            frame[top:top + 105, 601:1255] = 255
        self.assertEqual(accounts.cards(frame), [(117, 222), (246, 351), (375, 480), (504, 609), (633, 720)])
        self.assertEqual(accounts.name_line(frame, (633, 720)).shape[:2], (34, 420))
        # With four accounts the log-in card peeks in at the bottom, further down than a card would.
        frame[633:702] = 240
        self.assertEqual(accounts.cards(frame), [(117, 222), (246, 351), (375, 480), (504, 609)])

    def test_a_cards_name_is_its_first_line_read_as_one_line_of_text(self):
        frame = FakeGame().frames['list']
        with patch.object(accounts.subprocess, 'run', return_value=SimpleNamespace(stdout=b' Second  One\r\n')) as run:
            self.assertEqual(accounts.card_name(frame, (375, 480), 'tesseract'), 'Second One')
        self.assertEqual(run.call_args.args[0], ['tesseract', 'stdin', 'stdout', '--psm', '7', '-l', 'eng'])
        page = cv2.imdecode(np.frombuffer(run.call_args.kwargs['input'], np.uint8), cv2.IMREAD_GRAYSCALE)
        self.assertEqual(page.shape, (34 * 3 + 40, 420 * 3 + 40))
        with patch.object(accounts.subprocess, 'run', side_effect=OSError('missing')), \
                self.assertRaisesRegex(bot.BotError, 'tidak ada akun dipilih'):
            accounts.card_name(frame, (375, 480), 'tesseract')


class SwitchTests(unittest.TestCase):
    def game(self, **options):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        game = FakeGame(**options)
        game.runner.root = Path(temporary.name)
        shutil.copytree(ROOT / 'calibration/export', game.runner.root / 'calibration/export')
        scanner = export.Scanner
        for target, name, value in (
                (accounts, 'sync', lambda runner: game.loaded[1]), (accounts, 'card_name', game.card_name),
                (export, 'Scanner', lambda runner: scanner(runner, sleep=game.sleep, clock=lambda: game.seconds))):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        return game

    def test_the_account_asked_for_is_loaded_by_its_own_card_and_confirmed_by_its_tag(self):
        game = self.game()
        accounts.switch(game.runner, *SECOND)
        self.assertEqual(game.loaded, SECOND)
        self.assertEqual(game.inputs, [('home', 60, 60), ('profile', 1010, 60), ('settings', 844, 138),
                                       ('list', 900, 427)])
        self.assertEqual((game.state, game.settles), ('home', 1))
        self.assertGreaterEqual(game.slides, 2)

    def test_an_account_already_loaded_is_left_alone(self):
        game = self.game(loaded=SECOND)
        accounts.switch(game.runner, *SECOND)
        self.assertEqual((game.inputs, game.settles), ([], 0))

    def test_a_name_that_is_not_in_the_list_closes_it_without_choosing_an_account(self):
        game = self.game()
        game.names[375] = 'SecondOme'
        with self.assertRaisesRegex(bot.BotError, 'tidak ada akun dipilih'):
            accounts.switch(game.runner, *SECOND)
        self.assertEqual(game.card_taps(), [])
        self.assertEqual(game.inputs[-2:], [('list', 42, 47), ('settings', 1119, 56)])
        self.assertEqual((game.loaded, game.state), (MAIN, 'home'))

    def test_two_cards_with_the_name_asked_for_choose_neither(self):
        game = self.game()
        game.names[246] = 'secondone'
        with self.assertRaisesRegex(bot.BotError, 'tepat satu kali'):
            accounts.switch(game.runner, *SECOND)
        self.assertEqual((game.card_taps(), game.loaded, game.state), ([], MAIN, 'home'))

    def changing(self, game, change):
        """The game with a list that changes once its last name has been read."""
        read = game.card_name

        def card_name(image, card, program):
            name = read(image, card, program)
            if card[0] == 375:
                change()
            return name

        patcher = patch.object(accounts, 'card_name', card_name)
        patcher.start()
        self.addCleanup(patcher.stop)
        return game

    def test_a_card_whose_name_changed_after_it_was_read_is_not_tapped(self):
        game = self.game()
        changed = game.frames['list'].copy()
        changed[390:410, 720:900] = 0
        self.changing(game, lambda: game.frames.update(list=changed))
        with self.assertRaisesRegex(bot.BotError, 'berubah'):
            accounts.switch(game.runner, *SECOND)
        self.assertEqual((game.card_taps(), game.loaded, game.state), ([], MAIN, 'home'))
        self.assertEqual(game.inputs[-2:], [('list', 42, 47), ('settings', 1119, 56)])

    def test_a_list_that_lost_a_card_after_its_names_were_read_gets_no_tap_on_a_card(self):
        game = self.game()
        changed = game.frames['list'].copy()
        changed[246:351, 601:1255] = 240
        self.changing(game, lambda: game.frames.update(list=changed))
        with self.assertRaisesRegex(bot.BotError, 'berubah'):
            accounts.switch(game.runner, *SECOND)
        self.assertEqual((game.card_taps(), game.loaded, game.state), ([], MAIN, 'home'))

    def test_a_list_that_went_away_after_its_names_were_read_gets_no_input_at_all(self):
        game = self.game()
        self.changing(game, lambda: setattr(game, 'state', 'loading'))
        with self.assertRaisesRegex(bot.BotError, 'berubah'):
            accounts.switch(game.runner, *SECOND)
        self.assertEqual(game.inputs, [('home', 60, 60), ('profile', 1010, 60), ('settings', 844, 138)])

    def test_a_game_that_closes_itself_while_loading_the_account_is_opened_again_and_checked(self):
        game = self.game()
        game.after_tap = 'closes'
        accounts.switch(game.runner, *SECOND)
        self.assertEqual((game.loaded, game.state, game.reopened, len(game.card_taps())), (SECOND, 'home', 1, 1))

    def test_a_game_that_comes_back_on_the_account_it_had_is_tried_again_and_never_taken_for_the_one_asked(self):
        game = self.game()
        game.after_tap = 'reverts'
        # A stall: the run's recovery starts the switch again from the village, the card checked again.
        with self.assertRaisesRegex(bot.WaitTimeout, r'SecondOne belum termuat \(game kembali ke #PPPPPPPP\)'):
            accounts.switch(game.runner, *SECOND)
        self.assertEqual((game.loaded, game.reopened, len(game.card_taps())), (MAIN, 1, 1))

    def test_another_village_coming_up_after_the_switch_stops_the_run(self):
        game = self.game()
        game.loads[375] = OTHER
        with self.assertRaisesRegex(bot.BotError, r'\(#GGGGGGGG\) bukan SecondOne \(#QQQQQQQQ\)'):
            accounts.switch(game.runner, *SECOND)
        self.assertEqual(len(game.card_taps()), 1)

    def test_settings_without_the_switch_button_are_closed_and_nothing_else_is_tapped(self):
        game = self.game()
        game.frames['settings'][100:180, 800:890] = 0
        with self.assertRaisesRegex(bot.BotError, 'Tombol ganti akun'):
            accounts.switch(game.runner, *SECOND)
        self.assertEqual(game.inputs, [('home', 60, 60), ('profile', 1010, 60), ('settings', 1119, 56)])
        self.assertEqual((game.loaded, game.state), (MAIN, 'home'))

    def test_a_screen_that_is_not_a_village_gets_no_input(self):
        game = self.game()
        game.state = 'loading'
        with self.assertRaises(bot.UnknownScreen):
            accounts.switch(game.runner, *SECOND)
        self.assertEqual(game.inputs, [])

    def test_without_a_name_the_village_is_only_checked_and_nothing_is_switched_to(self):
        game = self.game(loaded=SECOND)
        accounts.switch(game.runner, '', SECOND[1])
        with self.assertRaisesRegex(bot.BotError, r'\(#QQQQQQQQ\) bukan desa di halaman Upgrade \(#PPPPPPPP\)'):
            accounts.switch(game.runner, '', MAIN[1])
        self.assertEqual((game.inputs, game.loaded), ([], SECOND))


class RunTests(unittest.TestCase):
    def runner(self, **options):
        device = SimpleNamespace(capture=lambda: np.zeros((720, 1280, 3), np.uint8), connected=lambda: True,
                                 game_in_front=lambda: True)
        runner = bot.Bot({'resolution': [1280, 720], 'screens': {}}, ROOT, device, live=True, **options)
        runner.settle = lambda: None
        return runner

    def test_a_run_for_an_account_loads_it_before_every_start_of_its_job(self):
        order = []

        def job(searches):
            order.append('job')
            if order.count('job') == 1:
                raise bot.WaitTimeout('Layar macet')
            return 7

        with patch.object(accounts, 'switch', side_effect=lambda runner, name, tag: order.append((name, tag))), \
                patch('coc_auto.bot.memory_critical', return_value=False):
            self.assertEqual(self.runner(account=SECOND).run_until_full_resilient(0, 2, job=job), 7)
            self.assertEqual(order, [SECOND, 'job', SECOND, 'job'])
            self.assertEqual(self.runner().run_until_full_resilient(0, 2, job=lambda searches: 3), 3)
            self.assertEqual(order, [SECOND, 'job', SECOND, 'job'])

    def test_a_run_whose_account_could_not_be_loaded_never_starts_its_job(self):
        ran = []
        with patch.object(accounts, 'switch', side_effect=bot.BotError('Akun yang dimuat (#GGGGGGGG) bukan SecondOne')), \
                patch('coc_auto.bot.memory_critical', return_value=False), self.assertRaisesRegex(bot.BotError, 'bukan'):
            self.runner(account=SECOND).run_until_full_resilient(0, 2, job=ran.append)
        self.assertEqual(ran, [])

    def test_the_task_runs_once_on_each_account_and_a_limit_ends_only_that_accounts_turn(self):
        argv = ['bot.py', '--walls', 'home', '--live', '--no-remote', '--max-upgrades', '3', '--accounts',
                'MainVillage#PPPPPPPP,SecondOne#qqqqqqqq']
        with patch('sys.argv', argv), patch('coc_auto.bot.load_config'), patch('coc_auto.bot.validate_config'), \
                patch('coc_auto.bot.validate_walls'), patch('coc_auto.bot.executable'), patch('coc_auto.bot.ADB'), \
                patch('coc_auto.bot.Bot') as runner, patch('coc_auto.bot.fit_screen', return_value=False):
            runner.return_value.run_until_full_resilient.side_effect = [bot.LimitReached('Selesai'), 1]
            self.assertEqual(bot.main(), 0)
        self.assertEqual([call.kwargs['account'] for call in runner.call_args_list], [MAIN, SECOND])
        self.assertEqual(runner.return_value.run_until_full_resilient.call_count, 2)

    def main(self, argv, results, config=None, **patches):
        """bot.main() for those arguments with the task's results turn by turn: the accounts whose turns were played,
        the accounts each new runner was for, the runner and time.sleep."""
        with patch('sys.argv', ['bot.py', *argv, '--live', '--no-remote']), \
                patch('coc_auto.bot.load_config', return_value=config) if config else patch('coc_auto.bot.load_config'), \
                patch('coc_auto.bot.validate_config'), patch('coc_auto.bot.validate_walls'), \
                patch('coc_auto.bot.executable'), patch('coc_auto.bot.ADB'), patch('coc_auto.bot.Bot') as runner, \
                patch('coc_auto.bot.fit_screen', return_value=False), patch.object(bot.time, 'sleep') as sleep, \
                self.assertLogs('coc_auto.bot', 'INFO') as logs:
            runner.return_value.run_until_full_resilient.side_effect = results
            for name, value in patches.items():
                patcher = patch.object(accounts, name, value)
                patcher.start()
                self.addCleanup(patcher.stop)
            self.assertEqual(bot.main(), 0)
        names = {MAIN[0]: MAIN, SECOND[0]: SECOND, OTHER[0]: OTHER}
        played = [names[found[1]] for line in logs.output for found in [re.search(r'Giliran akun (\S+) \(', line)]
                  if found]
        return played, [call.kwargs['account'] for call in runner.call_args_list], runner.return_value, sleep

    def test_an_account_a_limit_stopped_gets_another_turn_for_as_many_rounds_as_asked(self):
        both = ['--walls', 'home', '--max-upgrades', '3', '--accounts', 'MainVillage#PPPPPPPP,SecondOne#QQQQQQQQ']
        limit = bot.LimitReached('Selesai')
        # The second account finished on its first turn; only the first one comes back, each time with fresh limits.
        played, made, _, _ = self.main([*both, '--rounds', '3'], [limit, 1, limit, limit])
        self.assertEqual((played, made), ([MAIN, SECOND, MAIN, MAIN], [MAIN, SECOND, MAIN, MAIN]))
        played, _, _, _ = self.main([*both, '--rounds', '0'], [limit, limit, limit, 1, 1])
        self.assertEqual(played, [MAIN, SECOND, MAIN, SECOND, MAIN])
        self.assertEqual(self.main(both, [limit, limit])[0], [MAIN, SECOND])

    def test_upgrades_take_turns_and_the_game_sleeps_only_while_every_account_waits(self):
        from coc_auto import buildings
        own = {listing: {'targets': []} for listing in buildings.LISTS}
        own['buildings'] = {'targets': [{'id': 'bomb', 'name': 'Bomb', 'level': None}], 'keep_builders': 0}
        argv = ['--buildings', '--accounts', 'MainVillage#PPPPPPPP,NotAsked#GGGGGGGG,SecondOne#QQQQQQQQ']
        # Both accounts' upgrades are running: one for two hours, the other for ten minutes.
        played, made, runner, sleep = self.main(argv, [bot.TurnOver(7200), bot.TurnOver(600), 2, 4],
                                                picks=lambda config, root, asked: {MAIN[1]: own, SECOND[1]: own})
        # The account without picks is passed over, and the one whose upgrades end first plays first after the sleep.
        self.assertEqual(played, [MAIN, SECOND, SECOND, MAIN])
        # A wait is no limit: the default single round does not end a turn the upgrades made wait, and an account
        # that comes back carries on with its own runner, its time limit still running.
        self.assertEqual(made, [MAIN, SECOND])
        self.assertEqual(runner.device.stop_game.call_count, 2)
        # The clock stands still in this test, so the second sleep is the whole wait of the first account.
        self.assertEqual([round(call.args[0], -1) for call in sleep.call_args_list], [600, 7200])

    def test_with_accounts_on_the_device_a_run_without_turns_upgrades_the_village_on_the_upgrade_page(self):
        from coc_auto import buildings
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        (folder / 'config.json').write_text('{}', encoding='utf-8')
        (folder / '.coc-auto').mkdir()
        (folder / '.coc-auto' / 'village-export.json').write_text(json.dumps({'tag': SECOND[1]}), encoding='utf-8')
        own = {listing: {'targets': []} for listing in buildings.LISTS}
        config = {'serial': 'emulator-5554', 'resolution': [1280, 720], 'screens': {},
                  'accounts': [{'name': SECOND[0], 'tag': SECOND[1], 'enabled': True}]}
        asked = []
        played, made, _, _ = self.main(
            ['--buildings', '--config', str(folder / 'config.json')], [1], config,
            picks=lambda config, root, turns: asked.append(turns) or {SECOND[1]: own})
        # Its own picks, and a runner that first makes sure that village is the one loaded, switching to nothing.
        self.assertEqual((asked, made, played), ([(('', SECOND[1]),)], [('', SECOND[1])], []))
        # No village synced yet: which picks to use is unknown, so nothing runs.
        (folder / '.coc-auto' / 'village-export.json').unlink()
        with patch('sys.argv', ['bot.py', '--buildings', '--live', '--no-remote', '--config', str(folder / 'config.json')]), \
                patch('coc_auto.bot.load_config', return_value=config), patch('coc_auto.bot.Bot') as runner:
            self.assertEqual(bot.main(), 1)
        runner.assert_not_called()

    def test_accounts_need_live_input_and_rounds_need_accounts(self):
        for argv in (['--until-full', '--accounts', 'MainVillage#PPPPPPPP'],
                     ['--until-full', '--live', '--rounds', '2'],
                     ['--until-full', '--live', '--accounts', 'MainVillage#PPPPPPPP', '--rounds', '1001'],
                     ['--until-full', '--live', '--accounts', 'MainVillage']):
            with self.subTest(argv=argv), patch('sys.argv', ['bot.py', *argv, '--no-remote']), \
                    patch('coc_auto.bot.load_config') as load:
                self.assertEqual(bot.main(), 1)
                load.assert_not_called()


if __name__ == '__main__':
    unittest.main()
