import json
import unittest

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
        self.assertEqual(stats, {'clamped': 0, 'dropped': 0, 'truncated': 0})

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
