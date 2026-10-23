import asyncio
import json
import sys
import threading
import time
import unittest

from websockets.asyncio.server import serve

import relay
import remote
from test_remote import KEY, FakeDevice, signed, until


class FakeRealtime:
    """Answers joins like Supabase Realtime, records what the device sends and can broadcast to it."""

    def __init__(self):
        self.received, self.sockets = [], []
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        asyncio.run_coroutine_threadsafe(self.open(), self.loop).result(5)

    async def open(self):
        self.server = await serve(self.handle, '127.0.0.1', 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def handle(self, socket):
        self.sockets.append(socket)
        self.path = socket.request.path
        async for raw in socket:
            message = json.loads(raw)
            self.received.append(message)
            if message['event'] in ('phx_join', 'heartbeat'):
                await socket.send(json.dumps({'topic': message['topic'], 'event': 'phx_reply', 'ref': message['ref'],
                                              'payload': {'status': 'ok', 'response': {}}}))

    def broadcast(self, topic, name, payload):
        message = {'topic': topic, 'event': 'broadcast', 'ref': None,
                   'payload': {'type': 'broadcast', 'event': name, 'payload': payload}}
        asyncio.run_coroutine_threadsafe(self.sockets[-1].send(json.dumps(message)), self.loop).result(5)

    def send_raw(self, text):
        asyncio.run_coroutine_threadsafe(self.sockets[-1].send(text), self.loop).result(5)

    def sent(self, name):
        """What the device broadcast under this name, each checked against its device signature."""
        found = []
        for message in list(self.received):
            if message['event'] != 'broadcast' or message['payload'].get('event') != name:
                continue
            sealed = message['payload']['payload']
            if remote.sign(KEY, remote.AGENT_SIGNATURE_PREFIX + sealed['body']) != sealed['sig']:
                raise AssertionError(f'unsigned {name} broadcast')
            found.append(json.loads(sealed['body']))
        return found

    def close(self):
        async def shut():
            self.server.close()
        asyncio.run_coroutine_threadsafe(shut(), self.loop).result(5)


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeRealtime()
        self.addCleanup(self.server.close)
        self.device = FakeDevice()
        self.supervisor = remote.Supervisor(KEY, [sys.executable, '-c', 'pass'], self.device)
        self.relay = relay.Relay(f'http://127.0.0.1:{self.server.port}', 'sb_publishable_test', self.supervisor)
        self.relay.start()
        self.addCleanup(self.relay.close)
        self.topic = f'realtime:{self.supervisor.channel}'
        self.assertTrue(until(lambda: self.server.sent('state')))

    def test_it_joins_the_device_channel_and_shows_the_device_online(self):
        join = next(m for m in self.server.received if m['event'] == 'phx_join')
        self.assertEqual(join['topic'], self.topic)
        self.assertIn('apikey=sb_publishable_test', self.server.path)
        self.assertIn('vsn=1.0.0', self.server.path)
        state = self.server.sent('state')[0]
        self.assertEqual((state['state'], state['host']), ('idle', self.supervisor.host))
        self.assertLess(abs(state['ts'] - time.time()), 5)

    def test_the_state_is_repeated_so_a_silent_device_reads_as_offline(self):
        self.relay.state_seconds = 0.2
        self.assertTrue(until(lambda: len(self.server.sent('state')) >= 3, 5))

    def test_the_log_is_broadcast_in_batches(self):
        for number in range(3):
            self.supervisor.log(f'INFO: line {number}')
        self.assertTrue(until(lambda: sum(len(p['lines']) for p in self.server.sent('log')) >= 3))
        lines = [line for payload in self.server.sent('log') for line in payload['lines']]
        self.assertEqual(lines[-3:], ['INFO: line 0', 'INFO: line 1', 'INFO: line 2'])

    def test_a_signed_command_is_carried_out_and_acknowledged(self):
        body, sig = signed('open_coc', nonce='relay-1')
        self.server.broadcast(self.topic, 'cmd', {'body': body, 'sig': sig})
        self.assertTrue(until(lambda: self.server.sent('ack')))
        ack = self.server.sent('ack')[0]
        self.assertEqual((ack['nonce'], ack['ok']), ('relay-1', True))
        self.assertEqual(self.device.calls, ['open_coc'])

    def test_a_command_with_a_wrong_signature_gets_no_answer(self):
        body, sig = signed('kill_ld', nonce='relay-2', key='coc_someone_else_entirely_0000000000')
        self.server.broadcast(self.topic, 'cmd', {'body': body, 'sig': sig})
        body, sig = signed('start', {'task': 'shell'}, nonce='relay-3')
        self.server.broadcast(self.topic, 'cmd', {'body': body, 'sig': sig})
        self.assertTrue(until(lambda: self.server.sent('ack')))
        time.sleep(0.6)
        self.assertEqual([(ack['nonce'], ack['code']) for ack in self.server.sent('ack')], [('relay-3', 'refused')])
        self.assertEqual(self.device.calls, [])

    def test_deploy_screenshots_go_out_one_per_message(self):
        self.supervisor.publish('snapshot', {'screen': 'home_scout', 'image': 'data:image/jpeg;base64,AAAA'})
        self.assertTrue(until(lambda: self.server.sent('snapshot')))
        self.assertEqual(self.server.sent('snapshot')[0]['screen'], 'home_scout')

    def test_malformed_messages_on_the_public_channel_are_ignored(self):
        # Anyone who knows the channel can send junk; the link must keep serving the real dashboard.
        self.server.send_raw('not json')
        self.server.send_raw(json.dumps([1, 2, 3]))
        self.server.send_raw(json.dumps({'topic': self.topic, 'event': 'broadcast', 'payload': [1]}))
        self.server.broadcast(self.topic, 'cmd', ['body', 'sig'])
        self.server.broadcast(self.topic, 'cmd', {'body': 7, 'sig': None})
        body, sig = signed('open_coc', nonce='after-junk')
        self.server.broadcast(self.topic, 'cmd', {'body': body, 'sig': sig})
        self.assertTrue(until(lambda: any(ack['nonce'] == 'after-junk' for ack in self.server.sent('ack'))))
        self.assertEqual(self.device.calls, ['open_coc'])
        self.assertEqual(len(self.server.sockets), 1)

    def test_a_new_dashboard_gets_the_history(self):
        self.supervisor.log('INFO: before the page opened')
        self.server.broadcast(self.topic, 'hello', {})
        self.assertTrue(until(lambda: self.server.sent('history')))
        history = self.server.sent('history')[0]
        self.assertIn('INFO: before the page opened', history['lines'])
        self.assertEqual(history['state']['state'], 'idle')

    def test_a_flood_of_hellos_gets_one_history(self):
        for _ in range(10):
            self.server.broadcast(self.topic, 'hello', {})
        self.assertTrue(until(lambda: self.server.sent('history')))
        time.sleep(0.6)
        self.assertEqual(len(self.server.sent('history')), 1)

    def test_heartbeats_keep_the_connection(self):
        self.relay.heartbeat_seconds = 0.2
        self.assertTrue(until(lambda: any(m['event'] == 'heartbeat' and m['topic'] == 'phoenix'
                                          for m in self.server.received), 5))


if __name__ == '__main__':
    unittest.main()
