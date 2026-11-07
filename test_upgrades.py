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
                patch.object(buildings, 'refusal', refused), patch.object(gear, 'refusal', refused), \
                patch.object(upgrades, 'back_to_list', lambda runner, dialog: calls.append(('back', dialog))):
            done = upgrades.upgrade(runner, listing, SPEC, ['frame'], 300, row('Valkyrie', 'valkyrie'), 'tesseract',
                                    {})
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

    def visit(self, listing, chosen, builders=2, slots=(1, 1), placed=None, done=True):
        calls, refused = [], set()
        runner = SimpleNamespace(
            config={'walls': {'home': SPEC}}, poll=0, progress=0, navigate=lambda village: calls.append(village),
            find_gem_row=lambda spec: None, start_gem_watch=lambda spec: calls.append('gems'),
            open_build_list=lambda village, spec: 'opened', list_indonesian=lambda image: False,
            close_menus=lambda: calls.append('close'), check_gems=lambda spec: calls.append('gems'),
            templates=lambda: {}, read_stable=lambda reader, limit: reader(np.zeros((720, 1280, 3), np.uint8)))
        placed = placed if placed is not None else [(row('Valkyrie', None), 40)]

        def upgrade(runner, name, spec, frames, place, chosen_row, program, digits):
            calls.append(('upgrade', name, chosen_row['name'], place))
            return done
        with patch.object(buildings, 'village', return_value=(builders, AMOUNTS)), \
                patch.object(buildings, 'show_tab', lambda runner, x: calls.append(('tab', x)) or 'tab'), \
                patch.object(buildings, 'list_frames', return_value=['frame']), \
                patch.object(buildings, 'placed_rows', return_value=placed), \
                patch.object(gear, 'placed_rows', return_value=placed), \
                patch.object(bot, 'read_fraction', return_value=slots), \
                patch.object(upgrades, 'upgrade', upgrade), patch.object(upgrades, 'LOG') as log:
            result = upgrades.visit(runner, listing, chosen[listing], refused, 'tesseract', {'digits': {}})
        logged = [call.args[0] % call.args[1:] for call in log.info.call_args_list]
        return result, calls, refused, runner, logged

    def test_a_lab_target_is_upgraded_on_its_tab_and_counted(self):
        result, calls, _, runner, logs = self.visit('lab', picks(lab=['Valkyrie']))
        self.assertEqual(result, (True, True, buildings.WAIT_MAX_SECONDS))
        self.assertEqual(calls, ['home', 'gems', ('tab', 1062), ('upgrade', 'lab', 'Valkyrie', 40), ('tab', 978),
                                 'close', 'gems'])
        self.assertEqual(runner.progress, 1)
        self.assertTrue(any('Riset di-upgrade: Valkyrie ke level 2' in line for line in logs))

    def test_no_free_slot_or_no_spare_builder_means_no_tap(self):
        for listing, chosen, builders, slots in (('lab', picks(lab=['Valkyrie']), 5, (0, 1)),
                                                 ('pets', picks(pets=['Valkyrie']), 5, (0, 1)),
                                                 ('buildings', picks(buildings=['Valkyrie']), 1, (1, 1))):
            if listing == 'buildings':
                chosen['buildings']['keep_builders'] = 1
            with self.subTest(listing=listing):
                result, calls, _, _, _ = self.visit(listing, chosen, builders, slots)
                self.assertEqual(result[:2], (False, True))
                self.assertFalse([call for call in calls if call[0] == 'upgrade'])
                self.assertEqual(calls[-1], 'close')
        # Equipment has no slots; its ore is checked on the dialog.
        result, calls, _, _, logs = self.visit('gear', picks(gear=['Valkyrie']), 0, (0, 1))
        self.assertIn(('upgrade', 'gear', 'Valkyrie', 40), calls)
        self.assertTrue(any('Peralatan di-upgrade: Valkyrie ke level 2' in line for line in logs))

    def test_a_refused_row_is_left_for_later_and_a_running_target_is_waited_for(self):
        result, calls, refused, runner, _ = self.visit('pets', picks(pets=['Valkyrie']), done=False)
        self.assertEqual((result[0], refused, runner.progress), (False, {('Valkyrie', 2, 150)}, 0))
        self.assertEqual(calls[-1], 'close')
        running = row('Valkyrie', None, None, None, None, 'in_progress')
        result, calls, _, _, _ = self.visit('lab', picks(lab=['Valkyrie']), placed=[(running, 0)])
        self.assertEqual(result, (False, True, 8 + buildings.WAIT_MARGIN_SECONDS))
        self.assertFalse([call for call in calls if call[0] == 'upgrade'])


class RunTests(unittest.TestCase):
    """The --buildings loop over the lists with the visits faked: when it waits, and when it stops."""

    def run_loop(self, chosen, rounds):
        calls = []
        answers = iter(rounds)
        runner = SimpleNamespace(config={'tesseract': 'tesseract'}, root=ROOT, poll=1, stop_at=None,
                                 check_limits=lambda: None, refresh_if_low_memory=lambda: None)

        def visit(runner, listing, chosen_list, refused, program, templates):
            calls.append(listing)
            done, pending, seconds, refuse = next(answers)
            refused.update(refuse)
            return done, pending, seconds
        with patch.object(bot, 'executable', return_value='tesseract'), \
                patch.object(buildings, 'load_templates', return_value={}), patch.object(upgrades, 'visit', visit), \
                patch.object(upgrades.time, 'sleep', lambda seconds: calls.append(('sleep', seconds))), \
                self.assertLogs('upgrades', 'INFO') as logs:
            done = upgrades.run(runner, chosen)
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

    def test_builder_base_picks_are_never_opened(self):
        done, calls, logs = self.run_loop(picks(builder=['Battle Copter'], star=['Cannon Cart']), [])
        self.assertEqual((done, calls), (0, []))
        self.assertTrue(any('Desa Malam' in line for line in logs))
        # With Home Village picks too, only those lists are visited.
        done, calls, _ = self.run_loop(picks(builder=['Battle Copter'], lab=['Valkyrie']), [(False, False, 600, ())])
        self.assertEqual(calls, ['lab'])

    def test_the_time_limit_stops_a_wait_short(self):
        runner = SimpleNamespace(stop_at=upgrades.time.monotonic() + 30, poll=1)
        with patch.object(upgrades.time, 'sleep') as sleep:
            upgrades.pause(runner, 600, 'x')
        self.assertLessEqual(sleep.call_args.args[0], 30)


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
