import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from coc_auto import ai_deploy
from coc_auto.ai_deploy import AIPlanError, ChatClient, parse_reply

BOUNDS = (1280, 651)


class ParseReplyTest(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(parse_reply('{"stages": []}'), {'stages': []})

    def test_fenced_and_prose_wrapped(self):
        text = 'Here is the plan:\n```json\n{"stages": [{"name": "a}b"}]}\n```\nGood luck.'
        self.assertEqual(parse_reply(text)['stages'][0]['name'], 'a}b')

    def test_no_json_raises(self):
        with self.assertRaises(AIPlanError):
            parse_reply('I cannot help with that.')

    def test_truncated_json_raises(self):
        with self.assertRaises(AIPlanError):
            parse_reply('{"stages": [{"name": "x"')

    def test_array_is_not_a_plan(self):
        with self.assertRaises(AIPlanError):
            parse_reply('[1, 2]')



FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures' / 'ai'


def load(name):
    image = cv2.imread(str(FIXTURES / name))
    assert image is not None, name
    return image


BAR_TOP = 651


class DroppableTest(unittest.TestCase):
    """The droppable ground is outside the enemy base. The oracle frame was checked live with single Giants: the
    left-hand point deployed, the point between buildings inside the base was refused."""

    def zone(self, name):
        zone, how = ai_deploy.droppable(load(name), BAR_TOP)
        self.assertIsNotNone(zone, name)
        return zone, how

    def test_oracle_points_match_what_the_game_accepted(self):
        zone, _ = self.zone('scout-oracle.png')
        self.assertTrue(zone[400, 280])       # light ground left of the base: deployed live
        self.assertFalse(zone[305, 585])      # between buildings inside the base: refused live

    def test_every_sample_frame_gives_ground_outside_the_base(self):
        for name in ('scout-oracle.png', 'scout-clean.png', 'scout-sprawl.png', 'zone-scout.png', 'zone-selected.png'):
            with self.subTest(name=name):
                zone, how = self.zone(name)
                self.assertGreaterEqual(float(zone.mean()), ai_deploy.DROPPABLE_MIN_SHARE)
                self.assertLess(float(zone.mean()), 0.5)
                self.assertFalse(zone[BAR_TOP:, :].any())      # never on the card bar
                self.assertFalse(zone[360, 640])                # the middle of the base is never droppable

    def test_ground_keeps_clear_of_the_fixed_ui(self):
        zone, _ = self.zone('scout-clean.png')
        for x0, y0, x1, y1 in ai_deploy.HUD_RECTS:
            self.assertFalse(zone[y0:y1, x0:x1].any())

    def test_clean_base_uses_both_hulls(self):
        self.assertEqual(self.zone('scout-clean.png')[1], 'union')

    def test_decorated_base_trusts_the_line_hull(self):
        # the object hull is more than 1.5x the line hull here (trees and rocks pulled it out)
        self.assertEqual(self.zone('scout-sprawl.png')[1], 'line')

    def test_blank_frame_has_no_zone(self):
        zone, _ = ai_deploy.droppable(np.full((720, 1280, 3), 90, np.uint8), BAR_TOP)
        self.assertIsNone(zone)


class ClientTest(unittest.TestCase):
    def reply_for(self, content):
        return json.dumps({'choices': [{'message': {'role': 'assistant', 'content': content}}]}).encode()

    def test_request_shape_and_auth_header(self):
        seen = {}

        def transport(url, payload, headers, timeout):
            seen.update(url=url, body=json.loads(payload), headers=headers, timeout=timeout)
            return self.reply_for('{"stages": []}')

        client = ChatClient('https://example.test/v1/', 'sk-test', 'vision-model', timeout=500, transport=transport)
        content = client.complete([{'role': 'user', 'content': 'hi'}])
        self.assertEqual(content, '{"stages": []}')
        self.assertEqual(seen['url'], 'https://example.test/v1/chat/completions')
        self.assertEqual(seen['headers']['Authorization'], 'Bearer sk-test')
        self.assertEqual(seen['body']['model'], 'vision-model')
        self.assertEqual(seen['timeout'], ai_deploy.MAX_TIMEOUT)

    def test_base_url_must_be_http(self):
        with self.assertRaises(AIPlanError):
            ChatClient('file:///etc', 'k', 'm')

    def test_http_error_hides_key(self):
        def transport(url, payload, headers, timeout):
            raise AIPlanError('Model menolak permintaan (HTTP 401)')

        client = ChatClient('https://example.test/v1', 'sk-secret-123', 'm', transport=transport)
        with self.assertRaises(AIPlanError) as caught:
            client.complete([])
        self.assertNotIn('sk-secret-123', str(caught.exception))

    def test_unexpected_reply_shape_raises(self):
        client = ChatClient('https://example.test/v1', 'k', 'm', transport=lambda *a: b'{"nope": 1}')
        with self.assertRaises(AIPlanError):
            client.complete([])

    def test_redirects_are_not_followed(self):
        self.assertIsNone(ai_deploy.NoRedirect().redirect_request(None, None, 302, 'Found', {}, 'https://x'))


class MessagesTest(unittest.TestCase):

    def test_encode_jpeg_downscales_large_frames(self):
        import cv2
        jpeg = ai_deploy.encode_jpeg(np.zeros((720, 1280, 3), np.uint8), max_edge=640)
        decoded = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(max(decoded.shape[:2]), 640)


if __name__ == '__main__':
    unittest.main()


class BotHookTest(unittest.TestCase):
    """Bot.ai_cards / ai_plan on the real card-bar fixture, with the model replaced by a fake transport."""

    def setUp(self):
        import types
        from coc_auto import bot as bot_module
        self.bot_module = bot_module
        from coc_auto.paths import RESOURCE_ROOT
        self.config = json.loads((RESOURCE_ROOT / 'config.json').read_text(encoding='utf-8'))
        self.auto = self.config['screens']['home_scout']['auto']
        self.image = load('zone-selected.png')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stub = types.SimpleNamespace(digits=lambda: None, root=self.root)
        self.stub.ai_cards = lambda image, auto: bot_module.Bot.ai_cards(self.stub, image, auto)
        self.stub.keep_ai_plan = lambda *args: bot_module.Bot.keep_ai_plan(self.stub, *args)

    def test_card_inventory_has_troops_spells_and_hero(self):
        cards = self.bot_module.Bot.ai_cards(self.stub, self.image, self.auto)
        kinds = {cid: card['kind'] for cid, card in cards.items()}
        self.assertEqual(kinds['T1'], 'troop')
        self.assertEqual(kinds['H1'], 'hero')
        self.assertTrue(any(kind == 'spell' for kind in kinds.values()))
        self.assertEqual(len(cards), len(set(cards)))
        for card in cards.values():
            self.assertEqual(card['slot'][1], self.auto['bar'][1] + self.auto['bar'][3] // 2)

    def test_counted_cards_before_the_spells_are_troops_not_siege(self):
        # This bar: five troop cards (the 25-wizard card among them), a hero, three spells.
        cards = self.bot_module.Bot.ai_cards(self.stub, self.image, self.auto)
        kinds = [card['kind'] for card in cards.values()]
        self.assertEqual(kinds.count('troop'), 5)
        self.assertEqual(kinds.count('spell'), 3)
        self.assertEqual(kinds.count('hero'), 1)
        self.assertNotIn('siege', kinds)
        self.assertEqual([cid for cid in cards if cid.startswith('T')], ['T1', 'T2', 'T3', 'T4', 'T5'])


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'ai.json'

    def valid(self, **extra):
        return {'enabled': True, 'base_url': 'http://127.0.0.1:1234/v1', 'api_key': 'sk-live-999',
                'model': 'qwen-vl', 'goal': 'trophy', 'hint': 'two dragons only', **extra}

    def test_save_then_load_round_trip_hides_key(self):
        shown = ai_deploy.save_settings(self.path, self.valid())
        self.assertNotIn('api_key', shown)
        self.assertTrue(shown['key_set'])
        loaded = ai_deploy.load_settings(self.path)
        self.assertEqual(loaded['api_key'], 'sk-live-999')
        self.assertEqual(loaded['goal'], 'trophy')
        self.assertNotIn('sk-live-999', json.dumps(ai_deploy.public_settings(loaded)))

    def test_key_kept_when_not_resent(self):
        ai_deploy.save_settings(self.path, self.valid())
        ai_deploy.save_settings(self.path, {'goal': 'stars'})
        self.assertEqual(ai_deploy.load_settings(self.path)['api_key'], 'sk-live-999')

    def test_rejects_bad_values(self):
        for bad in ({'goal': 'chaos'}, {'base_url': 'ftp://x'}, {'hint': 'x' * 600},
                    {'enabled': True, 'api_key': ''}):
            with self.subTest(bad=bad):
                with self.assertRaises(ai_deploy.AIPlanError):
                    ai_deploy.save_settings(self.path, {**self.valid(), **bad})

    def test_missing_file_gives_defaults_and_no_client(self):
        settings = ai_deploy.load_settings(self.path)
        self.assertFalse(settings['enabled'])
        self.assertIsNone(ai_deploy.client_from_settings(settings))

    def test_enabled_settings_build_client(self):
        ai_deploy.save_settings(self.path, self.valid())
        self.assertIsInstance(ai_deploy.client_from_settings(ai_deploy.load_settings(self.path)), ai_deploy.ChatClient)




TL_CARDS = {
    'T1': {'slot': [143, 651], 'kind': 'troop', 'count': 12},
    'T2': {'slot': [237, 651], 'kind': 'troop', 'count': 5},
    'H1': {'slot': [637, 651], 'kind': 'hero', 'count': None},
    'S1': {'slot': [746, 651], 'kind': 'spell', 'count': 2},
}
TL_POINTS = {'P1': [200, 400], 'P2': [300, 500]}


def entry(moment, do, card, **extra):
    return {'t': moment, 'do': do, 'card': card, **extra}


def timeline(*entries):
    return {'analysis': 'x', 'timeline': list(entries)}


class CompileTimelineTest(unittest.TestCase):
    def compile(self, *entries, **kwargs):
        return ai_deploy.compile_timeline(timeline(*entries), TL_CARDS, TL_POINTS, BOUNDS, **kwargs)

    def test_drops_use_the_verified_point_and_carry_their_time(self):
        steps, stats = self.compile(entry(0, 'drop', 'T1', point='P1', count=4))
        self.assertEqual(steps, [{'slot': [143, 651], 'count': 4, 'points': [[200, 400]] * 4, 'burst': 4, 'at': 0.0}])
        self.assertEqual(stats, {'clamped': 0, 'dropped': 0, 'truncated': 0})

    def test_unknown_point_or_card_is_dropped(self):
        steps, stats = self.compile(entry(0, 'drop', 'T1', point='P9', count=2), entry(0, 'drop', 'T9', point='P1', count=2),
                                    entry(1, 'drop', 'T2', point='P2', count=2))
        self.assertEqual([s['slot'][0] for s in steps], [237])
        self.assertEqual(stats['dropped'], 2)

    def test_a_troop_cannot_be_dropped_by_coordinates(self):
        with self.assertRaises(AIPlanError):
            self.compile(entry(0, 'drop', 'T1', at=[300, 300], count=2))

    def test_steps_are_sorted_by_time_and_equal_times_keep_order(self):
        steps, _ = self.compile(entry(9, 'drop', 'T2', point='P2', count=1), entry(2, 'drop', 'T1', point='P1', count=1),
                                entry(2, 'drop', 'T2', point='P1', count=1))
        self.assertEqual([(s['at'], s['slot'][0]) for s in steps], [(2.0, 143), (2.0, 237), (9.0, 237)])

    def test_counts_are_capped_by_what_is_left_across_entries(self):
        steps, _ = self.compile(entry(0, 'drop', 'T2', point='P1', count=4), entry(5, 'drop', 'T2', point='P2', count=4))
        self.assertEqual([s['count'] for s in steps], [4, 1])

    def test_hero_is_dropped_once_and_fires_its_ability(self):
        steps, _ = self.compile(entry(0, 'drop', 'H1', point='P1', count=9), entry(8, 'ability', 'H1'))
        self.assertEqual(steps[0]['count'], 1)
        self.assertEqual(steps[1], {'slot': [637, 651], 'count': 1, 'tap_only': True, 'at': 8.0})

    def test_ability_needs_a_hero(self):
        steps, stats = self.compile(entry(0, 'drop', 'T1', point='P1', count=1), entry(3, 'ability', 'T1'))
        self.assertEqual(len(steps), 1)
        self.assertEqual(stats['dropped'], 1)

    def test_spells_keep_their_coordinates_even_over_the_base(self):
        steps, _ = self.compile(entry(14, 'spell', 'S1', at=[640, 334], count=1))
        self.assertEqual(steps[0]['points'], [[640, 334]])

    def test_spell_coordinates_are_clamped_to_the_frame_above_the_bar(self):
        steps, stats = self.compile(entry(14, 'spell', 'S1', at=[2000, 900], count=1))
        self.assertEqual(steps[0]['points'], [[1279, 650]])
        self.assertEqual(stats['clamped'], 1)

    def test_spell_by_point_id_or_troop_as_spell_is_dropped(self):
        steps, stats = self.compile(entry(0, 'spell', 'S1', point='P1'), entry(0, 'spell', 'T1', at=[1, 1]),
                                    entry(1, 'drop', 'S1', point='P1', count=1), entry(2, 'drop', 'T1', point='P1', count=1))
        self.assertEqual(len(steps), 1)
        self.assertEqual(stats['dropped'], 3)

    def test_times_are_capped_and_bad_times_dropped(self):
        steps, stats = self.compile(entry(900, 'drop', 'T1', point='P1', count=1), entry('soon', 'drop', 'T2', point='P1', count=1),
                                    entry(-4, 'drop', 'T2', point='P1', count=1), t_max=100.0)
        self.assertEqual([s['at'] for s in steps], [0.0, 100.0])
        self.assertEqual(stats['dropped'], 1)

    def test_hold_mode_for_a_troop(self):
        steps, _ = self.compile(entry(0, 'drop', 'T1', point='P1', count=12, mode='hold', seconds=99))
        self.assertEqual(steps[0]['hold'], ai_deploy.MAX_HOLD)
        self.assertTrue(steps[0]['until_empty'])
        self.assertEqual(steps[0]['points'], [[200, 400]])

    def test_step_cap_truncates(self):
        entries = [entry(i, 'spell', 'S1', at=[10, 10], count=1) for i in range(ai_deploy.MAX_STEPS + 3)]
        cards = {'S1': {**TL_CARDS['S1'], 'count': None}}
        steps, stats = ai_deploy.compile_timeline(timeline(*entries), cards, TL_POINTS, BOUNDS, t_max=500.0)
        self.assertEqual(len(steps), ai_deploy.MAX_STEPS)
        self.assertEqual(stats['truncated'], 3)

    def test_empty_or_missing_timeline_raises(self):
        for raw in ({'timeline': []}, {'stages': []}, {'timeline': [entry(0, 'drop', 'T9', point='P1', count=1)]}):
            with self.subTest(raw=raw):
                with self.assertRaises(AIPlanError):
                    ai_deploy.compile_timeline(raw, TL_CARDS, TL_POINTS, BOUNDS)


class PlanTimelineTest(unittest.TestCase):
    POINTS = [{'id': 'P1', 'at': [200, 400], 'side': 'west'}, {'id': 'P2', 'at': [300, 500], 'side': 'south-west'}]
    IMAGE = np.zeros((720, 1280, 3), np.uint8)

    def reply(self, content):
        return json.dumps({'choices': [{'message': {'content': content}}]}).encode()

    def good(self):
        return json.dumps({'analysis': 'push west', 'estimate': [{'t': 0, 'note': 'giants land'}, {'t': 14, 'note': 'wall', 'x': 1}, 'junk'],
                           'timeline': [entry(0, 'drop', 'T1', point='P1', count=3)]})

    def call(self, transport, deadline=None):
        client = ChatClient('https://example.test/v1', 'k', 'm', transport=transport)
        return ai_deploy.plan_timeline(client, 'loot', 'note', self.IMAGE, TL_CARDS, self.POINTS, BOUNDS, 150.0,
                                       probe={'card': 'T2'}, units={'troops': {'Giant': {'speed': 12}}}, deadline=deadline)

    def test_success_returns_steps_and_the_logged_estimate(self):
        seen = {}

        def transport(url, payload, headers, timeout):
            seen['text'] = json.loads(payload)['messages'][1]['content'][0]['text']
            seen['system'] = json.loads(payload)['messages'][0]['content']
            return self.reply(self.good())

        steps, stats, info = self.call(transport)
        self.assertEqual(steps[0]['points'], [[200, 400]] * 3)
        self.assertEqual(info['analysis'], 'push west')
        self.assertEqual(info['estimate'], [{'t': 0.0, 'note': 'giants land'}, {'t': 14.0, 'note': 'wall'}])
        self.assertEqual(info['attempts'], 1)
        self.assertEqual(seen['system'], ai_deploy.TIMELINE_PROMPT)
        for part in ('Time budget: 150', '"id": "P1"', 'T2', 'Giant', 'Cards left'):
            self.assertIn(part, seen['text'])

    def test_invalid_json_is_asked_for_once_more(self):
        replies = [self.reply('no json here'), self.reply(self.good())]
        steps, _, info = self.call(lambda *a: replies.pop(0))
        self.assertEqual(info['attempts'], 2)
        self.assertEqual(len(steps), 1)

    def test_no_retry_after_the_deadline(self):
        calls = []

        def transport(*args):
            calls.append(1)
            return self.reply('no json here')

        with self.assertRaises(AIPlanError):
            self.call(transport, deadline=0.0)
        self.assertEqual(len(calls), 1)

    def test_connection_failure_is_not_retried(self):
        calls = []

        def transport(*args):
            calls.append(1)
            raise AIPlanError('Tidak bisa terhubung ke model AI')

        with self.assertRaises(AIPlanError):
            self.call(transport)
        self.assertEqual(len(calls), 1)


class CandidatePointsTest(unittest.TestCase):
    def test_compass_sides(self):
        centre = (500.0, 400.0)
        self.assertEqual(ai_deploy.side_label([700, 400], centre), 'east')
        self.assertEqual(ai_deploy.side_label([500, 200], centre), 'north')
        self.assertEqual(ai_deploy.side_label([300, 400], centre), 'west')
        self.assertEqual(ai_deploy.side_label([500, 600], centre), 'south')
        self.assertEqual(ai_deploy.side_label([700, 200], centre), 'north-east')

    def test_candidates_on_real_frames_are_spread_and_on_droppable_ground(self):
        for name in ('scout-oracle.png', 'scout-clean.png', 'scout-sprawl.png'):
            with self.subTest(name=name):
                image = load(name)
                zone, _ = ai_deploy.droppable(image, BAR_TOP)
                centre = ai_deploy.base_centre(image, BAR_TOP)
                points, used = ai_deploy.candidate_points(zone, 24, centre)
                self.assertEqual(used, centre)
                self.assertGreaterEqual(len(points), 6)
                for x, y in points:
                    self.assertTrue(zone[y, x])
                for index, a in enumerate(points):
                    for b in points[index + 1:]:
                        self.assertGreaterEqual(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5, 70.0)
                self.assertGreaterEqual(len({ai_deploy.side_label(p, centre) for p in points}), 4)

    def test_first_candidates_are_on_different_sides(self):
        image = load('scout-clean.png')
        zone, _ = ai_deploy.droppable(image, BAR_TOP)
        centre = ai_deploy.base_centre(image, BAR_TOP)
        points, _ = ai_deploy.candidate_points(zone, 24, centre)
        self.assertGreaterEqual(len({ai_deploy.side_label(p, centre) for p in points[:3]}), 2)

    def test_empty_ground_gives_no_candidates(self):
        self.assertEqual(ai_deploy.candidate_points(np.zeros((720, 1280), bool), 12), ([], None))

    def test_base_centre_is_inside_the_playable_frame(self):
        for name in ('scout-oracle.png', 'scout-clean.png', 'scout-sprawl.png'):
            x, y = ai_deploy.base_centre(load(name), BAR_TOP)
            self.assertTrue(200 < x < 1100 and 150 < y < BAR_TOP, (name, x, y))


class UnitsAndProbeSettingsTest(unittest.TestCase):
    def test_units_reference_loads(self):
        units = ai_deploy.load_units()
        self.assertIn('Giant', units['troops'])
        self.assertIn('Rage', units['spells'])

    def test_missing_units_file_gives_empty(self):
        self.assertEqual(ai_deploy.load_units(Path('nowhere.json')), {})

    def test_probe_settings_defaults_and_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ai.json'
            settings = ai_deploy.load_settings(path)
            self.assertEqual((settings['probe_points'], settings['probe_max'], settings['t_max']), (6, 12, 150.0))
            self.assertEqual(ai_deploy.save_settings(path, {'t_max': 999})['t_max'], 170.0)
            for bad in ({'probe_points': 1}, {'probe_points': 13}, {'probe_max': 30}, {'probe_points': 8, 'probe_max': 4}):
                with self.subTest(bad=bad):
                    with self.assertRaises(AIPlanError):
                        ai_deploy.save_settings(path, bad)


class ProbeTest(unittest.TestCase):
    """Bot.probe_drop_points and pick_probe_card with a fake game: a drop lowers the card count only on valid points."""

    def setUp(self):
        import types
        from unittest import mock
        from coc_auto import bot as bot_module
        from coc_auto.paths import RESOURCE_ROOT
        self.bot_module = bot_module
        config = json.loads((RESOURCE_ROOT / 'config.json').read_text(encoding='utf-8'))
        self.auto = config['screens']['home_scout']['auto']
        self.image = load('zone-selected.png')
        self.valid = {(100, 100), (200, 200), (300, 300), (400, 400), (500, 500)}
        self.taps, self.count, self.state = [], 12, 'home_battle'
        outer = self

        class Device:
            def tap_many(self, points):
                outer.taps.append([list(p) for p in points])
                if tuple(points[-1]) in outer.valid:
                    outer.count -= 1

        self.read = {'value': True}
        patcher = mock.patch.object(bot_module, 'card_count',
                                    lambda *args, **kwargs: self.count if self.read['value'] else None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.stub = types.SimpleNamespace(live=True, poll=0, digits=lambda: {'0': 1}, device=Device())
        self.stub.checked_state = lambda allowed, terminal, image=None: (self.state, self.image)
        self.card = {'slot': [142, 651], 'count': 12}

    def probe(self, candidates, target=3, max_tries=12):
        return self.bot_module.Bot.probe_drop_points(self.stub, self.image, self.auto, candidates, self.card, target,
                                                     max_tries, {'home_scout', 'home_battle'}, {'home_result'})

    def test_valid_points_are_verified_and_refused_ones_failed(self):
        result = self.probe([[100, 100], [150, 150], [200, 200], [250, 250], [300, 300]], target=3)
        self.assertEqual(result['verified'], [[100, 100], [200, 200], [300, 300]])
        self.assertEqual(result['failed'], [[150, 150], [250, 250]])
        self.assertEqual((result['tried'], result['spent'], result['assumed']), (5, 3, False))

    def test_it_stops_at_the_target(self):
        result = self.probe([[100, 100], [200, 200], [300, 300], [400, 400]], target=2)
        self.assertEqual(result['verified'], [[100, 100], [200, 200]])
        self.assertEqual(len(self.taps), 2)

    def test_it_stops_at_the_try_limit(self):
        result = self.probe([[1, 1], [2, 2], [3, 3], [4, 4], [100, 100]], target=3, max_tries=3)
        self.assertEqual((result['verified'], result['tried']), ([], 3))
        self.assertEqual(result['failed'], [[1, 1], [2, 2], [3, 3]])

    def test_first_tap_selects_the_card_and_later_taps_only_drop(self):
        self.probe([[100, 100], [200, 200]], target=2)
        self.assertEqual(self.taps, [[[142, 651], [100, 100]], [[200, 200]]])

    def test_an_unreadable_count_stops_the_probe_without_guessing(self):
        self.read['value'] = False
        result = self.probe([[100, 100], [200, 200]], target=2)
        self.assertEqual((result['verified'], result['failed'], len(self.taps)), ([], [], 1))

    def test_a_result_screen_ends_the_probe(self):
        self.state = 'home_result'
        result = self.probe([[100, 100], [200, 200]], target=2)
        self.assertEqual((result['verified'], result['state']), ([], 'home_result'))

    def test_not_live_taps_nothing_and_marks_the_points_assumed(self):
        self.stub.live = False
        result = self.probe([[1, 1], [2, 2], [3, 3], [4, 4]], target=3)
        self.assertEqual(result['verified'], [[1, 1], [2, 2], [3, 3]])
        self.assertTrue(result['assumed'])
        self.assertEqual(self.taps, [])

    def test_probe_card_is_the_troop_with_the_most_units(self):
        cards = {'T1': {'kind': 'troop', 'count': 12}, 'T2': {'kind': 'troop', 'count': 25}, 'T3': {'kind': 'troop', 'count': None},
                 'S1': {'kind': 'spell', 'count': 40}, 'H1': {'kind': 'hero', 'count': None}}
        pick = self.bot_module.Bot.pick_probe_card
        self.assertEqual(pick(self.stub, cards, 16), 'T2')
        self.assertIsNone(pick(self.stub, cards, 26))          # the best stack is too small to spend from
        self.assertIsNone(pick(self.stub, {'T1': {'kind': 'troop', 'count': None}}, 1))
        self.assertIsNone(pick(self.stub, {}, 1))


class DeployAiTest(unittest.TestCase):
    """Bot.deploy_ai and run_timeline with stubs: probe -> one model call -> timeline, or stop."""

    CARDS = {'T1': {'slot': [143, 651], 'kind': 'troop', 'count': 12}, 'T2': {'slot': [237, 651], 'kind': 'troop', 'count': 25},
             'H1': {'slot': [637, 651], 'kind': 'hero', 'count': None}, 'S1': {'slot': [746, 651], 'kind': 'spell', 'count': 2}}
    PROBE = {'verified': [[200, 400], [300, 500], [400, 520]], 'failed': [[250, 300]], 'tried': 4, 'spent': 3,
             'seconds': 3.2, 'assumed': False, 'state': 'home_battle'}
    PLAN = {'analysis': 'push west', 'estimate': [{'t': 0, 'note': 'land'}], 'timeline': [
        entry(14, 'spell', 'S1', at=[640, 334], count=1), entry(0, 'drop', 'T1', point='P2', count=3),
        entry(1, 'drop', 'H1', point='P1', count=1), entry(8, 'ability', 'H1')]}

    def setUp(self):
        import types
        from coc_auto import bot as bot_module
        from coc_auto.paths import RESOURCE_ROOT
        self.bot_module = bot_module
        config = json.loads((RESOURCE_ROOT / 'config.json').read_text(encoding='utf-8'))
        self.screen = config['screens']['home_scout']
        self.image = load('scout-oracle.png')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.deployed, self.left, self.requests, self.state = [], [], [], 'home_battle'
        bot = bot_module.Bot
        stub = types.SimpleNamespace(
            live=False, ai_probe_points=3, ai_probe_max=12, ai_t_max=150.0, ai_goal='loot', ai_hint='', hero_slots=None,
            battle_deadline=7, root=Path(self.tmp.name), probe=dict(self.PROBE), cards=dict(self.CARDS))
        stub.ai_cards = lambda image, auto: stub.cards
        stub.pick_probe_card = lambda cards, needed: bot.pick_probe_card(stub, cards, needed)
        stub.probe_drop_points = lambda *args: stub.probe
        stub.checked_state = lambda allowed, terminal, image=None: (self.state, self.image)
        stub.deploy_card = lambda screen, step, allowed, terminal: (self.deployed.append(step), self.state)[1]
        stub.keep_ai_run = lambda record: bot.keep_ai_run(stub, record)
        stub.run_timeline = lambda *args: bot.run_timeline(stub, *args)
        stub.ai_stop = lambda record, reason: bot.ai_stop(stub, record, reason)
        stub.leave_home_search = lambda: self.left.append(True)
        self.stub = stub
        self.reply = json.dumps({'choices': [{'message': {'content': json.dumps(self.PLAN)}}]}).encode()

    def client(self, transport=None):
        def default(url, payload, headers, timeout):
            self.requests.append(json.loads(payload))
            return self.reply

        self.stub.ai = ChatClient('https://example.test/v1', 'k', 'm', transport=transport or default)

    def run_ai(self):
        return self.bot_module.Bot.deploy_ai(self.stub, self.screen, {'home_scout', 'home_battle'}, {'home_result'}, self.image)

    def record(self):
        return json.loads((Path(self.tmp.name) / 'captures' / 'ai-plan.json').read_text(encoding='utf-8'))

    def test_the_timeline_runs_in_time_order_on_the_verified_points(self):
        self.client()
        self.assertIsNone(self.run_ai())
        self.assertEqual([step['at'] for step in self.deployed], [0.0, 1.0, 8.0, 14.0])
        self.assertEqual(self.deployed[0]['points'], [[300, 500]] * 3)     # P2
        self.assertEqual(self.deployed[1]['points'], [[200, 400]])          # hero on P1
        self.assertTrue(self.deployed[2]['tap_only'])
        self.assertEqual(self.deployed[3]['points'], [[640, 334]])          # spell, free coordinate

    def test_the_model_is_told_the_verified_points_the_probe_card_and_what_is_left(self):
        self.client()
        self.run_ai()
        self.assertEqual(len(self.requests), 1)                              # one call, no second look
        text = self.requests[0]['messages'][1]['content'][0]['text']
        self.assertIn('"id": "P1"', text)
        self.assertIn('"id": "P3"', text)
        self.assertNotIn('250, 300', text)                                   # a failed point is never offered
        self.assertIn('card T2', text)
        self.assertIn('Time budget: 150', text)

    def test_the_record_keeps_candidates_probe_points_steps_and_real_times(self):
        self.client()
        self.run_ai()
        record = self.record()
        self.assertGreaterEqual(len(record['candidates']), 2)
        self.assertEqual(record['probe']['spent'], 3)
        self.assertEqual([p['id'] for p in record['points']], ['P1', 'P2', 'P3'])
        self.assertEqual(len(record['steps']), 4)
        self.assertEqual(len(record['ran']), 4)
        self.assertEqual(record['info']['analysis'], 'push west')
        self.assertIsNone(record['stopped'])

    def test_no_probe_troop_stops_before_any_model_call_and_leaves_when_live(self):
        self.stub.cards = {**self.CARDS, 'T2': {**self.CARDS['T2'], 'count': 5}, 'T1': {**self.CARDS['T1'], 'count': 5}}
        self.stub.live = True
        self.client(lambda *a: self.fail('no call expected'))
        self.assertIsNone(self.run_ai())
        self.assertEqual((self.deployed, self.left), ([], [True]))
        self.assertIn('uji titik deploy', self.record()['stopped'])
        self.assertIsNone(self.stub.battle_deadline)

    def test_fewer_than_two_verified_points_stops(self):
        self.stub.probe = {**self.PROBE, 'verified': [[200, 400]]}
        self.client(lambda *a: self.fail('no call expected'))
        self.assertIsNone(self.run_ai())
        self.assertEqual(self.deployed, [])
        self.assertIn('kurang dari 2', self.record()['stopped'])

    def test_a_model_failure_stops_dropping(self):
        def transport(*args):
            raise AIPlanError('Tidak bisa terhubung ke model AI')

        self.stub.live = True
        self.client(transport)
        self.assertIsNone(self.run_ai())
        self.assertEqual((self.deployed, self.left), ([], [True]))

    def test_a_plan_with_nothing_valid_stops_dropping(self):
        self.reply = json.dumps({'choices': [{'message': {'content': json.dumps({'timeline': [entry(0, 'drop', 'T1', point='P9', count=1)]})}}]}).encode()
        self.client()
        self.assertIsNone(self.run_ai())
        self.assertEqual(self.deployed, [])
        self.assertEqual(len(self.requests), 2)                              # one retry, then give up

    def test_a_result_screen_after_the_probe_ends_the_deploy(self):
        self.stub.probe = {**self.PROBE, 'state': 'home_result'}
        self.client(lambda *a: self.fail('no call expected'))
        self.assertEqual(self.run_ai(), 'home_result')

    def test_run_timeline_waits_for_each_step_when_live(self):
        import time
        self.stub.live = True
        self.client()
        steps = [{'slot': [143, 651], 'count': 1, 'points': [[1, 1]], 'at': 0.0}, {'slot': [143, 651], 'count': 1, 'points': [[2, 2]], 'at': 0.4}]
        record = {'ran': []}
        started = time.monotonic()
        self.assertIsNone(self.bot_module.Bot.run_timeline(self.stub, self.screen, steps, {'home_battle'}, {'home_result'}, started, record))
        self.assertLess(record['ran'][0]['actual'], 0.3)
        self.assertGreaterEqual(record['ran'][1]['actual'], 0.4)

    def test_run_timeline_stops_when_the_battle_ends_while_waiting(self):
        import time
        self.stub.live = True
        self.state = 'home_result'
        steps = [{'slot': [143, 651], 'count': 1, 'points': [[1, 1]], 'at': 0.3}]
        record = {'ran': []}
        state = self.bot_module.Bot.run_timeline(self.stub, self.screen, steps, {'home_battle'}, {'home_result'}, time.monotonic(), record)
        self.assertEqual((state, self.deployed, record['ran']), ('home_result', [], []))


class ReadCardCountTest(unittest.TestCase):
    """The probe reads a selected card, whose count label is wider and shifted right (real frames, Wizard card x25)."""

    def setUp(self):
        from coc_auto import bot as bot_module
        from coc_auto.paths import RESOURCE_ROOT
        self.bot_module = bot_module
        config = json.loads((RESOURCE_ROOT / 'config.json').read_text(encoding='utf-8'))
        self.auto = config['screens']['home_scout']['auto']
        self.templates = bot_module.load_digit_templates(RESOURCE_ROOT, config['digits'])
        self.before, self.selected = load('probe-before.png'), load('probe-selected.png')
        found, _ = bot_module.read_bar(self.before, self.auto)
        self.box = next(cell for cell, _kind, _group in found if abs((cell[0] + cell[1]) // 2 - 336) <= 12)

    def read(self, image, expected):
        return self.bot_module.read_card_count(image, self.auto['bar'], self.box, self.templates, expected)

    def test_plain_card_reads_its_count(self):
        self.assertEqual(self.read(self.before, 25), 25)

    def test_selected_card_reads_after_one_drop(self):
        self.assertEqual(self.read(self.selected, 25), 24)

    def test_the_unshifted_read_alone_fails_on_the_selected_card(self):
        self.assertIsNone(self.bot_module.card_count(self.selected, self.auto['bar'], self.box, self.templates))

    def test_an_implausible_number_is_never_accepted(self):
        self.assertIsNone(self.read(self.selected, 30))   # a misread such as 21 for 24 must not pass as a drop
        self.assertIsNone(self.read(self.selected, 27))


class HistoryTest(unittest.TestCase):
    RECORD = {
        'candidates': [[1, 1]] * 6, 'probe': {'tried': 5, 'verified': [[1, 1]] * 4, 'spent': 4, 'seconds': 4.7},
        'cards': {'T1': {'slot': [143, 651], 'kind': 'troop', 'count': 12}, 'S1': {'slot': [746, 651], 'kind': 'spell', 'count': 2}},
        'info': {'analysis': 'a' * 500, 'attempts': 1}, 'stats': {'dropped': 1, 'clamped': 0},
        'model_seconds': 17.9, 'stopped': None,
        'steps': [{'slot': [143, 651], 'count': 8, 'points': [[1, 1]] * 8, 'at': 0.0},
                  {'slot': [637, 651], 'count': 1, 'tap_only': True, 'at': 8.0},
                  {'slot': [746, 651], 'count': 1, 'points': [[5, 5]], 'at': 20.0}],
        'ran': [{'at': 0.0, 'actual': 1.0, 'slot': 143}, {'at': 8.0, 'actual': 10.5, 'slot': 637}, {'at': 20.0, 'actual': 21.2, 'slot': 746}],
    }

    def entry(self, **changes):
        record = {**self.RECORD, **changes}
        return ai_deploy.history_entry(record, 'some/model', 'loot', {'last': 60, 'max': 71}, 143.2)

    def test_entry_summarises_the_battle_without_images_or_keys(self):
        entry = self.entry()
        self.assertEqual((entry['probe_tried'], entry['probe_verified'], entry['probe_spent']), (5, 4, 4))
        self.assertEqual((entry['steps'], entry['dropped'], entry['steps_run']), (3, 1, 3))
        self.assertEqual((entry['units'], entry['spells']), (8, 1))       # the ability tap has no points; the spell is counted apart
        self.assertEqual((entry['max_late'], entry['last_step_at']), (2.5, 20.0))
        self.assertEqual((entry['damage_last'], entry['damage_max'], entry['battle_seconds']), (60, 71, 143.2))
        self.assertEqual(len(entry['analysis']), 200)
        self.assertEqual((entry['model'], entry['goal']), ('some/model', 'loot'))
        json.dumps(entry)                                                  # serialisable as it is

    def test_a_stopped_battle_keeps_its_reason_and_empty_numbers(self):
        entry = ai_deploy.history_entry({'stopped': 'titik deploy terverifikasi kurang dari 2', 'candidates': [], 'probe': None},
                                        'm', 'loot', {}, 12.0)
        self.assertEqual(entry['stopped'], 'titik deploy terverifikasi kurang dari 2')
        self.assertEqual((entry['steps'], entry['units'], entry['max_late'], entry['damage_last']), (0, 0, None, None))

    def test_append_load_round_trip_skips_damaged_lines(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'sub' / ai_deploy.HISTORY_FILE
            ai_deploy.append_history(path, self.entry())
            ai_deploy.append_history(path, self.entry(stopped='x'))
            with open(path, 'a', encoding='utf-8') as handle:
                handle.write('not json\n[1, 2]\n')
            entries = ai_deploy.load_history(path)
            self.assertEqual([bool(e['stopped']) for e in entries], [False, True])
            self.assertEqual(ai_deploy.load_history(Path(folder) / 'nothing.jsonl'), [])

    def test_the_log_is_rotated_when_it_gets_big(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ai_deploy.HISTORY_FILE
            for index in range(5):
                ai_deploy.append_history(path, {'n': index}, max_bytes=30)
            old = path.with_name(path.name + '.1')
            self.assertTrue(old.is_file())
            self.assertLess(path.stat().st_size, 60)
            self.assertEqual(ai_deploy.load_history(path)[-1], {'n': 4})

    def test_summary_counts_stops_and_averages_the_battles_that_ran(self):
        entries = [self.entry(), ai_deploy.history_entry(self.RECORD, 'm', 'loot', {'last': 40}, 100.0),
                   {'stopped': 'zona deploy tidak terbaca'}, {'stopped': 'zona deploy tidak terbaca'}]
        summary = ai_deploy.summarize_history(entries)
        self.assertEqual((summary['battles'], summary['planned']), (4, 2))
        self.assertEqual(summary['stopped'], {'zona deploy tidak terbaca': 2})
        self.assertEqual((summary['damage_mean'], summary['damage_max']), (50.0, 60))
        self.assertEqual((summary['probe_verified_mean'], summary['model_seconds_mean']), (4.0, 17.9))
        self.assertEqual(ai_deploy.summarize_history([])['battles'], 0)


class BattleOutcomeTest(unittest.TestCase):
    """Bot.note_battle / finish_ai_outcome: the damage the log gets comes from battle frames and never from a guess."""

    def setUp(self):
        import types
        from unittest import mock
        from coc_auto import bot as bot_module
        self.bot_module = bot_module
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.damage = [None]
        self.stub = types.SimpleNamespace(
            ai_outcome={'record': {'stopped': None, 'candidates': [], 'probe': None}, 'damage': {}, 'started': 100.0,
                        'read_at': float('-inf')},
            root=Path(self.tmp.name), ai=types.SimpleNamespace(model='m'), ai_goal='loot')
        self.stub.read_damage = lambda image: self.damage[0]
        self.image = np.zeros((720, 1280, 3), np.uint8)
        patcher = mock.patch.object(bot_module, 'runtime_dir', lambda *a, **k: Path(self.tmp.name) / '.coc-auto')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.clock = [100.0]
        patcher = mock.patch.object(bot_module.time, 'monotonic', lambda: self.clock[0])
        patcher.start()
        self.addCleanup(patcher.stop)

    def note(self, state, damage):
        self.damage[0] = damage
        self.bot_module.Bot.note_battle(self.stub, state, self.image)

    def test_last_and_highest_damage_are_kept(self):
        self.note('home_battle', 40)
        self.clock[0] += 2
        self.note('home_battle', 71)
        self.clock[0] += 2
        self.note('home_battle', 65)
        self.assertEqual(self.stub.ai_outcome['damage'], {'last': 65, 'max': 71})

    def test_reads_are_spaced_out_and_misreads_skipped(self):
        self.note('home_battle', 40)
        self.note('home_battle', 90)                       # too soon: not read
        self.assertEqual(self.stub.ai_outcome['damage']['last'], 40)
        self.clock[0] += 2
        self.note('home_battle', None)                      # unreadable: the last good value stays
        self.assertEqual(self.stub.ai_outcome['damage'], {'last': 40, 'max': 40})

    def test_other_screens_and_no_battle_are_ignored(self):
        self.note('home_scout', 55)
        self.assertEqual(self.stub.ai_outcome['damage'], {})
        self.stub.ai_outcome = None
        self.note('home_battle', 55)                         # nothing logged, nothing raised
        self.assertIsNone(self.stub.ai_outcome)

    def test_finishing_writes_one_line_and_stops_watching(self):
        self.note('home_battle', 62)
        self.clock[0] += 50
        self.bot_module.Bot.finish_ai_outcome(self.stub)
        self.assertIsNone(self.stub.ai_outcome)
        entries = ai_deploy.load_history(Path(self.tmp.name) / '.coc-auto' / ai_deploy.HISTORY_FILE)
        self.assertEqual(len(entries), 1)
        self.assertEqual((entries[0]['damage_last'], entries[0]['model'], entries[0]['battle_seconds']), (62, 'm', 50.0))
        self.bot_module.Bot.finish_ai_outcome(self.stub)     # a second call writes nothing more
        self.assertEqual(len(ai_deploy.load_history(Path(self.tmp.name) / '.coc-auto' / ai_deploy.HISTORY_FILE)), 1)
