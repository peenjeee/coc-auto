from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

import bot
import buildings
import gear
import upgrades

ROOT = Path(__file__).resolve().parent
CONFIG = bot.load_config(ROOT / 'config.json')
AMOUNTS = {'gold': 1000, 'elixir': 1000, 'dark': 100}
SPEC = CONFIG['walls']['home']


def row(name, key, next_level=2, cost=150, currency='gold', section='other'):
    return {'id': key, 'name': name, 'category': 'troop', 'count': 1, 'next_level': next_level, 'time': '8s',
            'cost': cost, 'currency': currency, 'section': section}


def picks(**targets):
    """A validated pick of every list, with targets in the lists named."""
    chosen = {listing: {'targets': []} for listing in buildings.LISTS}
    chosen['buildings']['keep_builders'] = 0
    for listing, names in targets.items():
        chosen[listing]['targets'] = [{'id': None, 'name': name, 'level': None} for name in names]
    return chosen


class UpgradeTests(unittest.TestCase):
    """One row tapped and its dialog confirmed, the screens faked: a tap only with the list in front, a confirm only
    for the row's own price."""

    def runner(self):
        calls = []

        def guarded_tap(states, location):
            # The location is resolved, and the dialog looked at again, before the tap.
            calls.append(('confirm', tuple(states), location('dialog') if callable(location) else location))
        return calls, SimpleNamespace(
            config=CONFIG, live=True, poll=0, guarded_tap=guarded_tap, wait_still=lambda seconds: 'dialog',
            device=SimpleNamespace(capture=lambda: 'list', tap_many=lambda taps: calls.append(('tap', taps))),
            wait=lambda targets, allowed, seconds: calls.append(('wait', tuple(targets))),
            action=lambda state, name: calls.append((state, name)))

    def upgrade(self, listing, refusals):
        calls, runner = self.runner()
        answers = iter(refusals)
        refused = lambda *args: next(answers)
        with patch.object(buildings, 'find_card', return_value=300) as find, \
                patch.object(buildings, 'list_in_front', return_value=True), \
                patch.object(upgrades, 'note_go', return_value=None), \
                patch.object(buildings, 'refusal', refused), patch.object(gear, 'refusal', refused), \
                patch.object(upgrades, 'back_to_list', lambda runner, dialog, *rest: calls.append(('back', dialog))), \
                self.assertLogs('upgrades', 'INFO') as logs:
            done = upgrades.upgrade(runner, listing, SPEC, ['frame'], 300, row('Valkyrie', 'valkyrie'), 'tesseract',
                                    {})
            upgrades.LOG.info('end')
        self.logs = logs.output
        return done, calls, find

    def test_a_lab_row_is_confirmed_on_its_dialog_only_once_the_price_matches(self):
        done, calls, find = self.upgrade('lab', [None, None])
        self.assertTrue(done)
        confirm = CONFIG['screens']['building_confirm']['actions']['confirm']
        self.assertEqual(calls, [('tap', [[SPEC['row']['tap_x'], 300 + buildings.NAME_CENTRE]]),
                                 ('wait', ('building_confirm',)), ('confirm', ('building_confirm',), confirm),
                                 ('back', 'building_confirm')])
        # The row's name is read again from the research catalog before the tap.
        self.assertEqual(find.call_args.args[6:], (buildings.RESEARCH, buildings.NAME_BAND))
        self.assertTrue(any('Riset di-upgrade: Valkyrie ke level 2' in line for line in self.logs))

    def test_a_builder_base_row_is_confirmed_on_its_button_where_the_dialog_shows_it_and_never_pays_gems(self):
        rules = {'keep_builders': 0, 'goblin': {'builder': True, 'researcher': True, 'max_gems': 50}}
        for refusal, confirmed in ((None, True), ('harga di dialog 5, di daftar 150', False)):
            calls, runner = self.runner()
            with self.subTest(refusal=refusal), patch.object(buildings, 'find_card', return_value=300), \
                    patch.object(buildings, 'list_in_front', return_value=True), \
                    patch.object(buildings, 'builder_refusal', return_value=refusal), \
                    patch.object(buildings, 'builder_button', return_value=(150, 'gold', [958, 627])), \
                    patch.object(upgrades, 'back_to_list',
                                 lambda runner, dialog, gems, name: calls.append(('back', dialog, gems))), \
                    self.assertLogs('upgrades', 'INFO'):
                done = upgrades.upgrade(runner, 'star', CONFIG['walls']['builder'], ['frame'], 300,
                                        row('Cannon Cart', 'cannon_cart'), 'tesseract', {}, rules)
            self.assertEqual(done, confirmed)
            self.assertEqual(('confirm', ('builder_upgrade_confirm',), [958, 627]) in calls, confirmed)
            self.assertEqual(('builder_upgrade_confirm', 'close') in calls, not confirmed)
            self.assertEqual(calls[-1], ('back', 'builder_upgrade_confirm', 0))

    def test_an_equipment_row_is_confirmed_on_the_blacksmith_dialog(self):
        done, calls, find = self.upgrade('gear', [None, None])
        self.assertTrue(done)
        confirm = CONFIG['screens']['gear_confirm']['actions']['confirm']
        self.assertIn(('confirm', ('gear_confirm',), confirm), calls)
        self.assertEqual(find.call_args.args[6:], (gear.CATALOG, gear.NAME_BAND))

    def test_nothing_is_tapped_unless_the_list_is_in_front(self):
        calls, runner = self.runner()
        with patch.object(buildings, 'find_card', return_value=300), \
                patch.object(buildings, 'list_in_front', return_value=False), self.assertRaises(bot.UnexpectedScreen):
            upgrades.upgrade(runner, 'pets', SPEC, ['frame'], 300, row('Lassi', 'lassi'), 'tesseract', {})
        self.assertEqual(calls, [])

    def test_a_dialog_that_does_not_match_is_closed_with_its_x_and_never_confirmed(self):
        # Wrong at once, or wrong in the very frame the confirm tap would follow.
        for listing in ('lab', 'buildings', 'gear'):
            dialog = buildings.DIALOGS[listing]
            for refusals in (['harga beda'], [None, 'harga beda']):
                with self.subTest(listing=listing, refusals=refusals):
                    done, calls, _ = self.upgrade(listing, refusals)
                    self.assertFalse(done)
                    self.assertEqual(calls[-2:], [(dialog, 'close'), ('back', dialog)])
                    self.assertFalse([call for call in calls if call[0] == 'confirm'])

    def test_the_lab_dialog_asks_the_rows_price_in_dark_elixir(self):
        # A research row opens the buildings' upgrade dialog, so its price is checked the same way.
        runner = bot.Bot(CONFIG, ROOT, None)
        image = cv2.imread(str(ROOT / 'fixtures' / 'research-confirm.png'))
        self.assertEqual(runner.detector.detect(image), 'building_confirm')
        valkyrie = row('Valkyrie', 'valkyrie', 6, 15750, 'dark')
        self.assertIsNone(buildings.refusal(runner, image, valkyrie))
        for wrong in ({'cost': 15700}, {'currency': 'elixir'}, {'cost': None}):
            with self.subTest(wrong=wrong):
                self.assertIsNotNone(buildings.refusal(runner, image, {**valkyrie, **wrong}))


class BackToListTests(unittest.TestCase):
    def runner(self, screens, in_front):
        calls = []
        states = iter(screens)

        def detect(frame):
            state = next(states)
            if state is None:
                raise bot.UnknownScreen('x')
            return state

        def wait(targets, allowed, seconds):
            calls.append(('wait', tuple(sorted(allowed))))
            raise bot.NoFreeBuilder('Semua tukang sibuk')
        fronts = iter(in_front)
        return calls, SimpleNamespace(poll=0, device=SimpleNamespace(capture=lambda: 'frame'),
                                      detector=SimpleNamespace(detect=detect), wait=wait), fronts

    def test_the_list_back_in_front_ends_the_wait_and_a_popup_is_left_to_wait(self):
        calls, runner, fronts = self.runner([None, 'building_confirm'], [False, False, True])
        with patch.object(buildings, 'list_in_front', lambda runner, frame: next(fronts)):
            upgrades.back_to_list(runner, 'building_confirm')
        self.assertEqual(calls, [])
        # All builders busy: wait() closes the gem offer with X and stops the try.
        calls, runner, fronts = self.runner(['builders_busy'], [False])
        with patch.object(buildings, 'list_in_front', lambda runner, frame: next(fronts)), \
                self.assertRaises(bot.NoFreeBuilder):
            upgrades.back_to_list(runner, 'building_confirm')
        self.assertEqual(calls, [('wait', ('build_list', 'building_confirm'))])

    def test_an_unknown_screen_that_stays_is_a_stall(self):
        calls, runner, fronts = self.runner([None], [False])
        with patch.object(buildings, 'list_in_front', lambda runner, frame: next(fronts)), \
                patch.object(bot, 'WALL_TAP_SECONDS', 0), self.assertRaises(bot.WaitTimeout):
            upgrades.back_to_list(runner, 'building_confirm')
        self.assertEqual(calls, [])


class VisitTests(unittest.TestCase):
    """One list opened on its tab, read, at most one row upgraded and the list closed from the builders tab."""

    def visit(self, listing, chosen, builders=2, slots=(1, 1), placed=None, done=True, rules=upgrades.RULES_OFF):
        calls, refused = [], set()
        runner = SimpleNamespace(
            config={'walls': CONFIG['walls']}, poll=0, progress=0, navigate=lambda village: calls.append(village),
            find_gem_row=lambda spec: None, start_gem_watch=lambda spec: calls.append('gems'),
            open_build_list=lambda village, spec: 'opened', list_indonesian=lambda image: False,
            close_menus=lambda: calls.append('close'), check_gems=lambda spec: calls.append('gems'),
            templates=lambda: {}, read_stable=lambda reader, limit: reader(np.zeros((720, 1280, 3), np.uint8)))
        placed = placed if placed is not None else [(row('Valkyrie', None), 40)]

        def upgrade(runner, name, spec, frames, place, chosen_row, program, digits, rules, amounts):
            calls.append(('upgrade', name, chosen_row['name'], place))
            return done

        def unlock(runner, name, spec, frames, place, chosen_row, program, rules, amounts):
            calls.append(('unlock', name, chosen_row['name'], place))
            return done
        with patch.object(buildings, 'village', return_value=(builders, AMOUNTS)), \
                patch.object(buildings, 'show_tab', lambda runner, x: calls.append(('tab', x)) or 'tab'), \
                patch.object(buildings, 'list_frames', return_value=['frame']), \
                patch.object(buildings, 'placed_rows', return_value=placed), \
                patch.object(gear, 'placed_rows', return_value=placed), \
                patch.object(bot, 'read_fraction', return_value=slots), \
                patch.object(upgrades, 'upgrade', upgrade), patch.object(upgrades, 'unlock', unlock), \
                patch.object(upgrades, 'LOG') as log:
            result = upgrades.visit(runner, listing, chosen[listing], refused, 'tesseract', {'digits': {}}, rules)
        logged = [call.args[0] % call.args[1:] for call in log.info.call_args_list]
        return result, calls, refused, runner, logged

    def test_a_lab_target_is_upgraded_on_its_tab_and_counted(self):
        result, calls, _, runner, logs = self.visit('lab', picks(lab=['Valkyrie']))
        self.assertEqual(result, (True, True, buildings.WAIT_MAX_SECONDS, None))
        self.assertEqual(calls, ['home', 'gems', ('tab', 1062), ('upgrade', 'lab', 'Valkyrie', 40), ('tab', 978),
                                 'close', 'gems'])
        self.assertEqual(runner.progress, 1)

    def test_a_locked_target_needs_a_builder_whatever_its_list(self):
        locked = {**row('Electro Owl', 'electro_owl', None, None, None, 'locked'), 'time': None}
        chosen = picks(pets=['Electro Owl'])
        chosen['pets']['targets'][0]['id'] = 'electro_owl'
        # The pet house's slot plays no part: the building it waits for takes a builder.
        _, calls, _, _, _ = self.visit('pets', chosen, builders=1, slots=(0, 1), placed=[(locked, 590)])
        self.assertIn(('unlock', 'pets', 'Electro Owl', 590), calls)
        _, calls, _, _, _ = self.visit('pets', chosen, builders=1, slots=(1, 1), placed=[(locked, 590)],
                                       rules={**upgrades.RULES_OFF, 'keep_builders': 1})
        self.assertFalse([call for call in calls if call[0] in ('unlock', 'upgrade')])

    def test_no_free_slot_or_no_spare_builder_means_no_tap(self):
        for listing, chosen, builders, slots in (('lab', picks(lab=['Valkyrie']), 5, (0, 1)),
                                                 ('pets', picks(pets=['Valkyrie']), 5, (0, 1)),
                                                 ('buildings', picks(buildings=['Valkyrie']), 1, (1, 1))):
            with self.subTest(listing=listing):
                result, calls, _, _, _ = self.visit(listing, chosen, builders, slots,
                                                    rules={**upgrades.RULES_OFF, 'keep_builders': 1})
                self.assertEqual(result[:2], (False, True))
                self.assertFalse([call for call in calls if call[0] == 'upgrade'])
                self.assertEqual(calls[-1], 'close')
        # Equipment has no slots; its ore is checked on the dialog.
        result, calls, _, _, logs = self.visit('gear', picks(gear=['Valkyrie']), 0, (0, 1))
        self.assertIn(('upgrade', 'gear', 'Valkyrie', 40), calls)

    def test_the_goblin_researchers_slot_and_the_goblin_builder_are_used_only_with_their_switch_on(self):
        on = {'keep_builders': 0, 'goblin': {'builder': True, 'researcher': True, 'max_gems': 50}}
        # The lab busy and the Goblin Researcher free: "1/2".
        for rules, used in ((upgrades.RULES_OFF, False), (on, True)):
            with self.subTest(rules=rules):
                _, calls, _, _, _ = self.visit('lab', picks(lab=['Valkyrie']), slots=(1, 2), rules=rules)
                self.assertEqual(('upgrade', 'lab', 'Valkyrie', 40) in calls, used)
        _, calls, _, _, _ = self.visit('lab', picks(lab=['Valkyrie']), slots=(2, 2))
        self.assertIn(('upgrade', 'lab', 'Valkyrie', 40), calls)
        # Every builder busy: tapped only when the Goblin Builder may be hired.
        for rules, used in ((upgrades.RULES_OFF, False), (on, True)):
            with self.subTest(rules=rules):
                _, calls, _, _, _ = self.visit('buildings', picks(buildings=['Valkyrie']), builders=0, rules=rules)
                self.assertEqual(('upgrade', 'buildings', 'Valkyrie', 40) in calls, used)

    def test_a_refused_row_is_left_for_later_and_a_running_target_is_waited_for(self):
        result, calls, refused, runner, _ = self.visit('pets', picks(pets=['Valkyrie']), done=False)
        self.assertEqual((result[0], refused, runner.progress), (False, {('Valkyrie', 2, 150)}, 0))
        self.assertEqual(calls[-1], 'close')
        running = row('Valkyrie', None, None, None, None, 'in_progress')
        result, calls, _, _, _ = self.visit('lab', picks(lab=['Valkyrie']), placed=[(running, 0)])
        self.assertEqual(result, (False, True, 8 + buildings.WAIT_MARGIN_SECONDS, None))
        self.assertFalse([call for call in calls if call[0] == 'upgrade'])

    def test_a_target_short_of_its_resource_asks_for_attacks_in_its_village_only_with_room_for_it(self):
        dear = row('Valkyrie', None, cost=10 ** 9)
        result, calls, _, _, _ = self.visit('lab', picks(lab=['Valkyrie']), placed=[(dear, 40)])
        self.assertEqual(result[3], ('home', 'gold'))
        self.assertFalse([call for call in calls if call[0] == 'upgrade'])
        # The lab busy: the run waits for it instead.
        result, _, _, _, _ = self.visit('lab', picks(lab=['Valkyrie']), slots=(0, 1), placed=[(dear, 40)])
        self.assertIsNone(result[3])

    def test_builder_base_lists_open_on_their_village_and_tab(self):
        result, calls, _, _, _ = self.visit('star', picks(star=['Valkyrie']), slots=(1, 1))
        self.assertEqual(calls, ['builder', 'gems', ('tab', 1193), ('upgrade', 'star', 'Valkyrie', 40),
                                 ('tab', 1021), 'close', 'gems'])
        # Its builders are counted on the list's tab: none free there, no tap.
        _, calls, _, _, _ = self.visit('builder', picks(builder=['Valkyrie']), slots=(0, 2))
        self.assertFalse([call for call in calls if call[0] == 'upgrade'])
        # A locked Star Laboratory row needs a free Builder Base builder, counted on the same tab bar.
        locked = {**row('Valkyrie', None, None, None, None, 'locked'), 'time': None}
        for slots, unlocked in (((1, 3), True), ((0, 3), False)):
            with self.subTest(slots=slots):
                _, calls, _, _, _ = self.visit('star', picks(star=['Valkyrie']), slots=slots, placed=[(locked, 590)])
                self.assertEqual(('unlock', 'star', 'Valkyrie', 590) in calls, unlocked)


class RunTests(unittest.TestCase):
    """The --buildings loop over the lists with the visits faked: when it waits, and when it stops."""

    def run_loop(self, chosen, rounds, farmed=()):
        calls = []
        answers = iter(rounds)
        runner = SimpleNamespace(config={'tesseract': 'tesseract'}, root=ROOT, poll=1, stop_at=None, live=False,
                                 check_limits=lambda: None, refresh_if_low_memory=lambda: None,
                                 run_until_full=lambda searches, limit: calls.append(
                                     ('farm', searches, limit, runner.goals)) or attacks.pop(0))
        attacks = list(farmed)

        def visit(runner, listing, chosen_list, refused, program, templates, rules):
            calls.append(listing)
            done, pending, seconds, refuse, *short = next(answers)
            refused.update(refuse)
            return done, pending, seconds, (short or [None])[0]
        with patch.object(bot, 'executable', return_value='tesseract'), \
                patch.object(buildings, 'load_templates', return_value={}), patch.object(upgrades, 'visit', visit), \
                patch.object(upgrades.time, 'sleep', lambda seconds: calls.append(('sleep', seconds))), \
                self.assertLogs('upgrades', 'INFO') as logs:
            done = upgrades.run(runner, chosen, 5)
        return done, calls, logs.output

    def test_home_lists_go_in_turn_and_the_run_ends_once_nothing_is_pending(self):
        chosen = picks(buildings=['Cannon'], lab=['Valkyrie'], gear=['Eternal Tome'])
        rounds = [(True, True, 60, ()), (False, True, 30, ()), (False, False, 600, ()),
                  # Nothing done: the soonest running upgrade of a list with something left is waited for.
                  (False, True, 60, ()), (False, True, 30, ()),
                  (False, True, 600, ()), (False, False, 600, ()),
                  (False, False, 600, ())]
        done, calls, logs = self.run_loop(chosen, rounds)
        self.assertEqual(done, 1)
        self.assertEqual(calls, ['buildings', 'lab', 'gear', 'buildings', 'lab', ('sleep', 30), 'buildings', 'lab',
                                 ('sleep', 600), 'buildings'])
        self.assertTrue(any('selesai' in line for line in logs))

    def test_a_refused_dialog_lets_the_next_target_go_first_before_any_wait(self):
        chosen = picks(pets=['Lassi'])
        done, calls, _ = self.run_loop(chosen, [(False, True, 60, {('Lassi', 2, 150)}), (True, True, 60, ()),
                                                (False, False, 600, ())])
        self.assertEqual((done, calls), (1, ['pets', 'pets', 'pets']))

    def test_builder_base_lists_are_visited_after_the_home_ones(self):
        done, calls, _ = self.run_loop(picks(star=['Cannon Cart'], lab=['Valkyrie']),
                                       [(False, False, 600, ()), (True, False, 600, ())])
        self.assertEqual((done, calls), (1, ['lab', 'star']))

    def test_a_target_short_of_resources_has_its_village_farmed_before_any_wait(self):
        chosen = picks(lab=['Valkyrie'], star=['Cannon Cart'])
        rounds = [(False, True, 3000, (), None), (False, True, 3000, (), ('builder', 'elixir')),
                  (True, False, 600, ()), (True, False, 600, ())]
        done, calls, logs = self.run_loop(chosen, rounds, farmed=[3])
        self.assertEqual(calls, ['lab', 'star', ('farm', 5, upgrades.FARM_ATTACKS, {'builder': {'elixir'}}), 'lab',
                                 'star'])
        self.assertEqual(done, 2)
        # Already full, so nothing was played: the run waits instead of attacking on and on.
        rounds = [(False, True, 300, (), ('home', 'dark')), (False, False, 600, ())]
        _, calls, _ = self.run_loop(picks(lab=['Valkyrie']), rounds, farmed=[0])
        self.assertEqual(calls, ['lab', ('farm', 5, upgrades.FARM_ATTACKS, {'home': {'dark'}}), ('sleep', 300), 'lab'])

    def test_the_time_limit_stops_a_wait_short(self):
        runner = SimpleNamespace(stop_at=upgrades.time.monotonic() + 30, poll=1, live=True)
        with patch.object(upgrades.time, 'sleep') as sleep:
            upgrades.pause(runner, 600, 'x')
        self.assertLessEqual(sleep.call_args.args[0], 30)

    def test_a_long_wait_is_spent_with_the_game_closed_and_opens_it_again(self):
        events = []
        device = SimpleNamespace(stop_game=lambda: events.append('stop'))
        runner = SimpleNamespace(stop_at=None, poll=1, live=True, device=device, recover=lambda: events.append('open'))
        with patch.object(upgrades.time, 'sleep', lambda seconds: events.append(seconds)):
            upgrades.pause(runner, 2 * 3600, 'x')
            # Days away: looked at again after the longest closed wait.
            upgrades.pause(runner, 5 * 86400, 'x')
            # Short waits keep the game open, at most ten minutes.
            upgrades.pause(runner, upgrades.CLOSED_WAIT_MIN_SECONDS - 1, 'x')
        self.assertEqual(events, ['stop', 7200, 'open', 'stop', upgrades.CLOSED_WAIT_MAX_SECONDS, 'open',
                                  buildings.WAIT_MAX_SECONDS])


def frame(name):
    return cv2.imread(str(ROOT / 'fixtures' / f'{name}.png'))


def panel(name):
    image = np.zeros((720, 1280, 3), np.uint8)
    image[:, 936:] = frame(name)
    return image


class GoblinTests(unittest.TestCase):
    """The Goblin offers from real frames, Indonesian and English: every button's gem price read, the cheapest paid
    only up to max_gems and re-read in the frame the tap follows."""

    @classmethod
    def setUpClass(cls):
        cls.real = bot.Bot(CONFIG, ROOT, None)

    def test_every_gem_button_is_read_and_the_cheapest_chosen(self):
        for name, prices, cheapest in (('goblin-offer', [40], (40, [921, 434])),
                                       ('goblin-offer-en', [40], (40, [921, 434])),
                                       ('goblin-builder-offer', [160, 10], (10, [1024, 447])),
                                       ('goblin-builder-offer-en', [160, 11], (11, [1024, 447]))):
            with self.subTest(name=name):
                self.assertEqual([price for price, _ in upgrades.goblin_buttons(self.real, frame(name))], prices)
                self.assertEqual(upgrades.cheapest(self.real, frame(name), 50), cheapest)
                # Above max_gems, or with max_gems 0, nothing is chosen.
                for gems in (9, 0):
                    with self.assertRaises(bot.TargetMissing):
                        upgrades.cheapest(self.real, frame(name), gems)
        with self.assertRaises(bot.TargetMissing):
            upgrades.cheapest(self.real, frame('locked-info'), 50)

    def runner(self, image):
        calls = []
        return calls, SimpleNamespace(
            root=ROOT, config=CONFIG, poll=0, templates=self.real.templates, gems_paid=0,
            wait_still=lambda seconds: image, action=lambda state, name: calls.append((state, name)),
            guarded_tap=lambda states, location: calls.append(('tap', tuple(states), location(image))))

    def test_the_cheapest_button_is_paid_and_the_gem_watch_told(self):
        calls, runner = self.runner(frame('goblin-builder-offer-en'))
        with self.assertLogs('upgrades', 'INFO') as logs:
            self.assertTrue(upgrades.hire(runner, 50, 'Clan Castle'))
        self.assertEqual((calls, runner.gems_paid), ([('tap', ('goblin_offer',), [1024, 447])], 11))
        self.assertTrue(any('Goblin disewa: 11 permata untuk Clan Castle' in line for line in logs.output))
        # Dearer than allowed: closed with X, nothing tapped, nothing allowed.
        calls, runner = self.runner(frame('goblin-offer'))
        with self.assertLogs('upgrades', 'WARNING'):
            self.assertFalse(upgrades.hire(runner, 30, 'Valkyrie'))
        self.assertEqual((calls, runner.gems_paid), ([('goblin_offer', 'close')], 0))

    def test_an_offer_is_paid_once_and_the_dialog_back_after_it_is_returned(self):
        states = iter(['goblin_offer', 'building_confirm'])
        runner = SimpleNamespace(poll=0, device=SimpleNamespace(capture=lambda: 'frame'),
                                 detector=SimpleNamespace(detect=lambda frame: next(states)))
        with patch.object(buildings, 'list_in_front', return_value=False), \
                patch.object(upgrades, 'hire', return_value=True) as hire:
            self.assertEqual(upgrades.back_to_list(runner, 'building_confirm', 50, 'Clan Castle'), 'building_confirm')
        hire.assert_called_once_with(runner, 50, 'Clan Castle')
        # Not paid (switch off, or refused): wait() closes it with X and the try stops.
        for gems, paid in ((0, True), (50, False)):
            with self.subTest(gems=gems):
                states = iter(['goblin_offer'])
                runner.detector = SimpleNamespace(detect=lambda frame: next(states))
                runner.wait = lambda targets, allowed, seconds: (_ for _ in ()).throw(bot.NoFreeBuilder('x'))
                with patch.object(buildings, 'list_in_front', return_value=False), \
                        patch.object(upgrades, 'hire', return_value=paid), self.assertRaises(bot.NoFreeBuilder):
                    upgrades.back_to_list(runner, 'building_confirm', gems, 'Valkyrie')

    def test_a_builder_freed_for_gems_has_its_dialog_confirmed_again_unpaid(self):
        backs, confirms = iter(['building_confirm', None]), []
        runner = SimpleNamespace(config=CONFIG, poll=0, wait_still=lambda seconds: 'dialog', action=None,
                                 guarded_tap=lambda states, location: confirms.append(location('dialog')))
        with patch.object(upgrades, 'back_to_list', side_effect=lambda *args: next(backs)) as back:
            self.assertTrue(upgrades.settle(runner, 'building_confirm', lambda image: None, 50, 'Clan Castle'))
        self.assertEqual(len(confirms), 2)
        self.assertEqual([call.args[2] for call in back.call_args_list], [50, 0])


class LockedTests(unittest.TestCase):
    """A locked row, the Town Hall's "Upgrade Locked" and a dialog with a red note: the game's green button selects
    the building waited for, upgraded at its button's price. The screens are faked, the readers run on real frames."""

    def runner(self):
        calls = []
        return calls, SimpleNamespace(
            config=CONFIG, live=True, poll=0, wait_still=lambda seconds: 'frame',
            device=SimpleNamespace(capture=lambda: 'list', tap_many=lambda taps: calls.append(('tap', taps))),
            wait=lambda targets, allowed, seconds: calls.append(('wait', tuple(sorted(targets)))) or 'building_confirm',
            action=lambda state, name: calls.append((state, name)),
            read_until=lambda reader, seconds: reader('frame'),
            guarded_tap=lambda states, location: calls.append(('guarded', tuple(states), location('frame'))))

    def unlock(self, listing='buildings', button=('gold', 80_000, [709, 560]), amounts={**AMOUNTS, 'gold': 100_000}):
        calls, runner = self.runner()
        locked = {**row('Ricochet Cannon', 'ricochet_cannon', None, None, None, 'locked'), 'time': None}
        hammer = (lambda *args: button) if isinstance(button, tuple) else \
            (lambda *args: (_ for _ in ()).throw(bot.TargetMissing(button)))
        with patch.object(buildings, 'find_card', return_value=587), \
                patch.object(buildings, 'list_in_front', return_value=True), \
                patch.object(upgrades, 'hammer', hammer), patch.object(upgrades, 'note_go', return_value=None), \
                patch.object(buildings, 'refusal', return_value=None), \
                patch.object(upgrades, 'back_to_list', return_value=None), \
                patch.object(upgrades, 'reopen', lambda runner, village, spec: calls.append('reopen')):
            done = upgrades.unlock(runner, listing, SPEC, ['frame'], 590, locked, 'tesseract', upgrades.RULES_OFF,
                                   amounts)
        return done, calls

    def test_a_locked_row_has_the_building_it_waits_for_upgraded(self):
        with self.assertLogs('upgrades', 'INFO') as logs:
            done, calls = self.unlock()
        self.assertTrue(done)
        confirm = CONFIG['screens']['building_confirm']['actions']['confirm']
        self.assertEqual(calls, [('tap', [[SPEC['row']['tap_x'], 587 + buildings.NAME_CENTRE]]),
                                 ('wait', ('locked_info',)), ('locked_info', 'go'), ('wait', ('home',)),
                                 ('guarded', ('home',), [709, 560]), ('wait', ('building_confirm',)),
                                 ('guarded', ('building_confirm',), confirm)])
        self.assertTrue(any('Bangunan di-upgrade untuk membuka Ricochet Cannon' in line for line in logs.output))
        # From another tab the list is opened again on its builders tab, which close_menus knows.
        _, calls = self.unlock('pets')
        self.assertEqual(calls[-1], 'reopen')

    def test_no_hammer_or_too_little_on_hand_upgrades_nothing(self):
        # No hammer (maxed or upgrading), or a price beyond the gold on hand.
        for button in ('0 tombol upgrade terlihat', ('gold', 80_000, [709, 560])):
            with self.subTest(button=button), self.assertLogs('upgrades', 'WARNING'):
                done, calls = self.unlock(button=button, amounts=AMOUNTS)
                self.assertFalse(done)
                self.assertEqual(calls[-1], ('wait', ('home',)))

    def test_a_builder_base_locked_row_has_its_building_upgraded_on_the_builder_base_dialog(self):
        calls, runner = self.runner()
        runner.templates = lambda: {}
        locked = {**row('Power P.E.K.K.A', 'power_pekka', None, None, None, 'locked'), 'time': None}
        spec = CONFIG['walls']['builder']
        with patch.object(buildings, 'find_card', return_value=587), \
                patch.object(buildings, 'list_in_front', return_value=True), \
                patch.object(upgrades, 'hammer', lambda *args: ('elixir', 1_000_000, [640, 560])), \
                patch.object(buildings, 'builder_refusal', return_value=None), \
                patch.object(buildings, 'builder_button', return_value=(1_000_000, 'elixir', [644, 632])), \
                patch.object(upgrades, 'back_to_list', lambda runner, dialog, gems, name: calls.append(('back', gems))), \
                patch.object(upgrades, 'reopen', lambda runner, village, spec: calls.append(('reopen', village))), \
                self.assertLogs('upgrades', 'INFO'):
            done = upgrades.unlock(runner, 'star', spec, ['frame'], 590, locked, 'tesseract',
                                   {'keep_builders': 0, 'goblin': {'builder': True, 'researcher': True, 'max_gems': 50}},
                                   {'gold': 0, 'elixir': 1_000_000})
        self.assertTrue(done)
        self.assertEqual(calls[1:], [('wait', ('locked_info',)), ('locked_info', 'go'), ('wait', ('builder',)),
                                     ('guarded', ('builder',), [640, 560]), ('wait', ('builder_upgrade_confirm',)),
                                     ('guarded', ('builder_upgrade_confirm',), [644, 632]), ('back', 0),
                                     ('reopen', 'builder')])

    def test_the_hammer_button_is_found_and_its_price_read_on_a_real_frame(self):
        real = bot.Bot(CONFIG, ROOT, None)
        self.assertEqual(upgrades.hammer(real, frame('locked-selected'), SPEC), ('gold', 80_000, [709, 560]))
        with self.assertRaises(bot.TargetMissing):
            upgrades.hammer(real, frame('locked-info'), SPEC)

    def test_upgrade_locked_points_at_its_required_buildings_in_either_language(self):
        for language in ('', '-en'):
            with self.subTest(language=language):
                self.assertEqual(upgrades.required_tiles(frame(f'upgrade-locked{language}')), [[584, 503], [696, 503]])
                # The list it opens marks the building's locked row with a white border.
                self.assertEqual(upgrades.marked_row(panel(f'building-list-marked{language}'), SPEC), 587)
        self.assertIsNone(upgrades.marked_row(panel('building-list-locked'), SPEC))
        self.assertEqual(upgrades.required_tiles(frame('locked-info')), [])

    def test_a_dialog_with_a_red_note_is_never_confirmed_and_its_go_button_followed(self):
        real = bot.Bot(CONFIG, ROOT, None)
        for language in ('', '-en'):
            with self.subTest(language=language):
                note = frame(f'upgrade-note{language}')
                # It passes for the upgrade dialog, so the note itself must stop the confirm.
                self.assertEqual(real.detector.detect(note), 'building_confirm')
                self.assertEqual(upgrades.note_go(note), [1081, 533])
                self.assertIsNotNone(upgrades.blocked(note))
                self.assertIsNotNone(buildings.refusal(real, note, {'cost': 8_000_000, 'currency': 'gold'}))
        for plain in ('building-confirm-gold', 'research-confirm', 'locked-info', 'gear-confirm-one-ore'):
            self.assertIsNone(upgrades.note_go(frame(plain)))
        calls, runner = self.runner()
        runner.wait_still = lambda seconds: frame('upgrade-note')
        with patch.object(buildings, 'find_card', return_value=300), \
                patch.object(buildings, 'list_in_front', return_value=True), \
                patch.object(upgrades, 'prerequisite', return_value=True) as prerequisite:
            runner.guarded_tap = lambda states, location: calls.append(('guarded', tuple(states),
                                                                        location(frame('upgrade-note'))))
            self.assertTrue(upgrades.upgrade(runner, 'buildings', SPEC, ['frame'], 300, row('Town Hall', 'town_hall'),
                                             'tesseract', {}, upgrades.RULES_OFF, AMOUNTS))
        self.assertEqual(calls[-1], ('guarded', ('building_confirm',), [1081, 533]))
        self.assertEqual(prerequisite.call_args.args[1], 'home')


class PickTests(unittest.TestCase):
    def test_an_equipment_row_needs_every_ore_cost_read_and_is_never_counted_from_the_village(self):
        target = [{'id': 'eternal_tome', 'name': 'Eternal Tome', 'level': 10}]
        tome = {'id': 'eternal_tome', 'name': 'Kitab Mahasakti', 'count': 1, 'next_level': 9,
                'costs': {'shiny': 3000, 'glowy': 600}, 'section': 'equipped'}
        self.assertEqual(buildings.pick([(tome, 5)], target, {}), ((tome, 5), True))
        for unread in ({'shiny': 3000, 'glowy': None}, {}):
            with self.subTest(costs=unread):
                self.assertEqual(buildings.pick([({**tome, 'costs': unread}, 5)], target, {}), (None, True))
        self.assertEqual(buildings.pick([(tome, 5)], target, {}, {buildings.row_key(tome)}), (None, True))
        self.assertEqual(buildings.pick([({**tome, 'next_level': 11}, 5)], target, {}), (None, False))


if __name__ == '__main__':
    unittest.main()
