import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from coc_auto import ai_deploy
from coc_auto.ai_deploy import AIPlanError, ChatClient, compile_plan, parse_reply

BOUNDS = (1280, 651)
CARDS = {
    'T1': {'slot': [143, 651], 'kind': 'counted', 'count': 12},
    'T2': {'slot': [237, 651], 'kind': 'counted', 'count': 5},
    'H1': {'slot': [637, 651], 'kind': 'hero', 'count': 1},
    'S1': {'slot': [746, 651], 'kind': 'counted', 'count': 2},
}


def stage(actions, wait=0, name='s'):
    return {'name': name, 'wait_before': wait, 'actions': actions}


def place(x, y, count):
    return {'at': [x, y], 'count': count}


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


class CompilePlanTest(unittest.TestCase):
    def test_troop_tap_places_one_batch_per_place(self):
        raw = {'stages': [stage([{'card': 'T1', 'place': [place(140, 300, 4), place(160, 420, 3)]}])]}
        steps, stats = compile_plan(raw, CARDS, BOUNDS)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]['slot'], [143, 651])
        self.assertEqual(steps[0]['count'], 7)
        self.assertEqual(steps[0]['points'], [[140, 300]] * 4 + [[160, 420]] * 3)
        self.assertEqual(stats, {'clamped': 0, 'snapped': 0, 'dropped': 0, 'truncated': 0})

    def test_unknown_card_is_dropped(self):
        raw = {'stages': [stage([{'card': 'X9', 'place': [place(100, 100, 1)]}])]}
        with self.assertRaises(AIPlanError):
            compile_plan(raw, CARDS, BOUNDS)

    def test_bad_card_id_type_is_dropped_not_crashing(self):
        raw = {'stages': [stage([{'card': ['T1'], 'place': [place(100, 100, 1)]},
                                 {'card': 'T2', 'place': [place(100, 100, 1)]}])]}
        steps, stats = compile_plan(raw, CARDS, BOUNDS)
        self.assertEqual(stats['dropped'], 1)
        self.assertEqual(len(steps), 1)

    def test_out_of_frame_points_are_clamped_and_counted(self):
        raw = {'stages': [stage([{'card': 'T1', 'place': [place(-30, 9999, 2)]}])]}
        steps, stats = compile_plan(raw, CARDS, BOUNDS)
        self.assertEqual(steps[0]['points'], [[0, 650]] * 2)
        self.assertEqual(stats['clamped'], 1)

    def test_non_numeric_point_is_dropped(self):
        raw = {'stages': [stage([{'card': 'T1', 'place': [{'at': ['a', 2], 'count': 1},
                                                          {'at': [True, 2], 'count': 1},
                                                          place(100, 100, 1)]}])]}
        steps, stats = compile_plan(raw, CARDS, BOUNDS)
        self.assertEqual(steps[0]['points'], [[100, 100]])
        self.assertEqual(stats['dropped'], 2)

    def test_count_capped_by_known_card_count(self):
        raw = {'stages': [stage([{'card': 'S1', 'place': [place(300, 300, 9)]}])]}
        steps, _ = compile_plan(raw, CARDS, BOUNDS)
        self.assertEqual(steps[0]['count'], 2)

    def test_repeated_card_across_actions_respects_total(self):
        raw = {'stages': [stage([{'card': 'S1', 'place': [place(300, 300, 2)]}]),
                          stage([{'card': 'S1', 'place': [place(500, 300, 2)]}], name='b')]}
        steps, stats = compile_plan(raw, CARDS, BOUNDS)
        self.assertEqual(sum(step['count'] for step in steps), 2)
        self.assertEqual(stats['dropped'], 1)

    def test_hero_gets_drop_then_ability_with_wait(self):
        raw = {'stages': [stage([{'card': 'H1', 'place': [place(130, 360, 5)], 'ability_after': 6}])]}
        steps, _ = compile_plan(raw, CARDS, BOUNDS)
        self.assertEqual(steps[0], {'slot': [637, 651], 'count': 1, 'points': [[130, 360]]})
        self.assertEqual(steps[1], {'slot': [637, 651], 'count': 1, 'tap_only': True, 'wait': 6.0})

    def test_hold_mode_uses_first_point_and_caps_seconds(self):
        raw = {'stages': [stage([{'card': 'T1', 'mode': 'hold', 'seconds': 99,
                                  'place': [place(125, 346, 3), place(140, 400, 1)]}])]}
        steps, stats = compile_plan(raw, CARDS, BOUNDS)
        self.assertEqual(steps[0]['hold'], ai_deploy.MAX_HOLD)
        self.assertTrue(steps[0]['until_empty'])
        self.assertEqual(steps[0]['points'], [[125, 346]])
        self.assertEqual(stats['dropped'], 1)

    def test_stage_wait_goes_on_first_step_and_is_capped(self):
        raw = {'stages': [stage([{'card': 'T1', 'place': [place(100, 100, 1)]}], wait=500),
                          stage([{'card': 'T2', 'place': [place(100, 100, 1)]}], wait=20, name='b')]}
        steps, _ = compile_plan(raw, CARDS, BOUNDS)
        self.assertEqual(steps[0]['wait'], ai_deploy.MAX_STAGE_WAIT)
        self.assertEqual(steps[1]['wait'], ai_deploy.MAX_STAGE_WAIT)
        self.assertLessEqual(sum(s.get('wait', 0) for s in steps), ai_deploy.MAX_TOTAL_WAIT)

    def test_too_many_stages_rejected(self):
        raw = {'stages': [stage([]) for _ in range(ai_deploy.MAX_STAGES + 1)]}
        with self.assertRaises(AIPlanError):
            compile_plan(raw, CARDS, BOUNDS)

    def test_step_count_truncated(self):
        actions = [{'card': 'T1', 'place': [place(100, 100, 1)]} for _ in range(ai_deploy.MAX_STEPS + 5)]
        steps, stats = compile_plan({'stages': [stage(actions)]}, {'T1': {**CARDS['T1'], 'count': None}}, BOUNDS)
        self.assertEqual(len(steps), ai_deploy.MAX_STEPS)
        self.assertEqual(stats['truncated'], 5)

    def test_empty_plan_raises(self):
        with self.assertRaises(AIPlanError):
            compile_plan({'stages': [stage([{'card': 'NOPE', 'place': []}])]}, CARDS, BOUNDS)


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

    def test_snap_moves_a_base_point_out_to_droppable_ground(self):
        zone, _ = self.zone('scout-oracle.png')
        snapped = ai_deploy.snap_to_zone([585, 305], zone)
        self.assertTrue(zone[snapped[1], snapped[0]])
        self.assertEqual(ai_deploy.snap_to_zone([280, 400], zone), [280, 400])   # already droppable: unchanged

    def test_compile_snaps_points_out_of_the_base(self):
        zone, _ = self.zone('scout-oracle.png')
        cards = {'T1': {'slot': [143, 651], 'kind': 'counted', 'count': 12}}
        raw = {'stages': [stage([{'card': 'T1', 'place': [place(585, 305, 2), place(280, 400, 1)]}])]}
        steps, stats = compile_plan(raw, cards, BOUNDS, zone)
        self.assertEqual(stats['snapped'], 1)
        for x, y in steps[0]['points']:
            self.assertTrue(zone[y, x])

    def test_spells_are_not_snapped_out_of_the_base(self):
        zone, _ = self.zone('scout-oracle.png')
        cards = {'S1': {'slot': [746, 651], 'kind': 'spell', 'count': 2},
                 'T1': {'slot': [143, 651], 'kind': 'troop', 'count': 12}}
        inside = place(585, 305, 1)
        raw = {'stages': [stage([{'card': 'S1', 'place': [inside]}, {'card': 'T1', 'place': [inside]}])]}
        steps, stats = compile_plan(raw, cards, BOUNDS, zone)
        self.assertEqual(steps[0]['points'], [[585, 305]])     # the spell stays over the base
        self.assertNotEqual(steps[1]['points'], [[585, 305]])  # the troop is moved out
        self.assertEqual(stats['snapped'], 1)

    def test_compile_without_zone_keeps_points(self):
        cards = {'T1': {'slot': [143, 651], 'kind': 'counted', 'count': 12}}
        raw = {'stages': [stage([{'card': 'T1', 'place': [place(10, 10, 1)]}])]}
        steps, stats = compile_plan(raw, cards, BOUNDS, None)
        self.assertEqual(steps[0]['points'], [[10, 10]])
        self.assertEqual(stats['snapped'], 0)

    def test_no_zone_refuses_the_ai_plan_before_calling_the_model(self):
        client = ChatClient('https://example.test/v1', 'k', 'm', transport=lambda *a: self.fail('no call expected'))
        with self.assertRaises(AIPlanError):
            ai_deploy.plan(client, 'loot', '', np.full((720, 1280, 3), 90, np.uint8), {}, BOUNDS)


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
    def test_images_sent_as_data_uri_and_inventory_listed(self):
        jpeg = ai_deploy.encode_jpeg(np.zeros((720, 1280, 3), np.uint8))
        messages = ai_deploy.build_messages('loot', 'keep heroes safe', CARDS, BOUNDS, [jpeg])
        self.assertEqual(messages[0]['role'], 'system')
        content = messages[1]['content']
        self.assertIn('keep heroes safe', content[0]['text'])
        self.assertIn('"id": "T1"', content[0]['text'])
        self.assertTrue(content[1]['image_url']['url'].startswith('data:image/jpeg;base64,'))

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

    def test_plan_from_model_replaces_builtin_plan(self):
        reply = {'analysis': 'funnel left', 'stages': [
            {'name': 'funnel', 'wait_before': 0, 'actions': [
                {'card': 'T1', 'place': [{'at': [300, 300], 'count': 3}]}]},
            {'name': 'heroes', 'wait_before': 2, 'actions': [
                {'card': 'H1', 'place': [{'at': [640, 300], 'count': 1}], 'ability_after': 5}]}]}

        def transport(url, payload, headers, timeout):
            return json.dumps({'choices': [{'message': {'content': json.dumps(reply)}}]}).encode()

        self.stub.ai = ai_deploy.ChatClient('https://example.test/v1', 'k', 'm', transport=transport)
        self.stub.ai_goal, self.stub.ai_hint = 'loot', ''
        steps = self.bot_module.Bot.ai_plan(self.stub, self.image, self.auto)
        self.assertIsNotNone(steps)
        self.assertEqual(steps[0]['slot'], [142, 651])
        self.assertEqual(steps[0].get('wait', 0), 0)
        self.assertTrue(any(step.get('tap_only') and step.get('wait') == 5 for step in steps))
        record = json.loads((self.root / 'captures' / 'ai-plan.json').read_text(encoding='utf-8'))
        self.assertEqual(record['analysis'], 'funnel left')

    def test_model_failure_falls_back_to_none(self):
        def transport(url, payload, headers, timeout):
            raise ai_deploy.AIPlanError('Tidak bisa terhubung ke model AI')

        self.stub.ai = ai_deploy.ChatClient('https://example.test/v1', 'k', 'm', transport=transport)
        self.stub.ai_goal, self.stub.ai_hint = 'loot', ''
        self.assertIsNone(self.bot_module.Bot.ai_plan(self.stub, self.image, self.auto))


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
