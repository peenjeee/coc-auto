from coc_auto.paths import RESOURCE_ROOT, REPO_ROOT
from pathlib import Path as _TestPath
FIXTURES = _TestPath(__file__).resolve().parents[1] / "fixtures"
"""A real relay.Relay against relay_server.py: join, two-way broadcast, self: false, frame limit.

The raw clients below play the dashboard's part (web/realtime.js speaks the same frames); the sealed
broadcast check is the same one FakeRealtime.sent makes, so what arrives is proven to be the device's.
"""
import asyncio
import json
import sys
import threading
import time
import unittest

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from coc_auto import relay
from coc_auto import relay_server
from coc_auto import remote
from test_remote import KEY, FakeDevice, signed, until

TOKEN = 'relay-test-token'


class RealRelayServer:
    """relay_server.py on an ephemeral port, in its own loop thread like FakeRealtime."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.server = asyncio.run_coroutine_threadsafe(
            relay_server.start_server('127.0.0.1', 0, TOKEN), self.loop).result(5)
        self.port = self.server.sockets[0].getsockname()[1]

    @property
    def url(self):
        return f'http://127.0.0.1:{self.port}'

    def address(self, apikey=TOKEN):
        return f'ws://127.0.0.1:{self.port}/realtime/v1/websocket?apikey={apikey}&vsn=1.0.0'

    def close(self):
        async def shut():
            self.server.close()
            await self.server.wait_closed()
        asyncio.run_coroutine_threadsafe(shut(), self.loop).result(5)


def frame(topic, event, payload, ref='1', join_ref=None):
    return json.dumps({'topic': topic, 'event': event, 'payload': payload, 'ref': ref, 'join_ref': join_ref})


async def join(socket, topic, ref='1'):
    await socket.send(frame(topic, 'phx_join', {'config': {'broadcast': {'ack': False, 'self': False},
                                                       'presence': {'enabled': False}, 'private': False}}, ref, ref))
    message = json.loads(await asyncio.wait_for(socket.recv(), 5))
    assert message['event'] == 'phx_reply' and message['ref'] == ref
    assert message['payload']['status'] == 'ok'
    return ref


async def next_broadcast(socket, seconds=10):
    """The next broadcast frame on this socket, as (event name, inner payload)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        message = json.loads(await asyncio.wait_for(socket.recv(), max(0.1, deadline - time.monotonic())))
        if message.get('event') == 'broadcast':
            return message['payload']['event'], message['payload']['payload']
    raise AssertionError('no broadcast arrived')


class RawClientTests(unittest.TestCase):
    """relay_server.py alone: the protocol rules the dashboard relies on."""

    def setUp(self):
        self.server = RealRelayServer()
        self.addCleanup(self.server.close)
        self.topic = 'realtime:coc-test-channel'

    def scenario(self, body):
        return asyncio.run(body())

    def test_heartbeats_are_answered_with_the_same_ref(self):
        async def body():
            async with connect(self.server.address()) as socket:
                await socket.send(frame('phoenix', 'heartbeat', {}, '42'))
                message = json.loads(await asyncio.wait_for(socket.recv(), 5))
                self.assertEqual((message['topic'], message['event'], message['ref'], message['payload']['status']),
                                 ('phoenix', 'phx_reply', '42', 'ok'))
        self.scenario(body)

    def test_a_wrong_apikey_is_refused_and_the_server_survives(self):
        async def body():
            with self.assertRaises(InvalidStatus) as raised:
                async with connect(self.server.address('bukan-tokennya')):
                    pass
            self.assertEqual(raised.exception.response.status_code, 403)
            async with connect(self.server.address()) as socket:  # still serving the right token
                await join(socket, self.topic)
        self.scenario(body)

    def test_a_broadcast_reaches_the_others_not_the_sender(self):
        async def body():
            async with connect(self.server.address()) as sender, connect(self.server.address()) as listener:
                await join(sender, self.topic, '1')
                await join(listener, self.topic, '1')
                await sender.send(frame(self.topic, 'broadcast',
                                        {'type': 'broadcast', 'event': 'ping', 'payload': {'n': 1}}, '2', '1'))
                name, payload = await next_broadcast(listener, 5)
                self.assertEqual((name, payload), ('ping', {'n': 1}))
                with self.assertRaises(asyncio.TimeoutError):  # self: false — the sender hears nothing back
                    await asyncio.wait_for(sender.recv(), 0.5)
                await listener.send(frame(self.topic, 'broadcast',
                                          {'type': 'broadcast', 'event': 'pong', 'payload': {}}, '2', '1'))
                name, _ = await next_broadcast(sender, 5)  # ...but it does hear the other side
                self.assertEqual(name, 'pong')
        self.scenario(body)

    def test_a_broadcast_without_joining_goes_nowhere(self):
        async def body():
            async with connect(self.server.address()) as stranger, connect(self.server.address()) as listener:
                await join(listener, self.topic, '1')
                await stranger.send(frame(self.topic, 'broadcast',
                                          {'type': 'broadcast', 'event': 'sneak', 'payload': {}}, '2'))
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(listener.recv(), 0.5)
                await join(stranger, self.topic, '1')  # joined now: the same broadcast is forwarded
                await stranger.send(frame(self.topic, 'broadcast',
                                          {'type': 'broadcast', 'event': 'sneak', 'payload': {}}, '3', '1'))
                name, _ = await next_broadcast(listener, 5)
                self.assertEqual(name, 'sneak')
        self.scenario(body)

    def test_an_oversize_frame_closes_only_that_socket(self):
        async def body():
            async with connect(self.server.address(), max_size=None) as big:
                await join(big, self.topic)
                await big.send(frame(self.topic, 'broadcast',
                                     {'type': 'broadcast', 'event': 'huge', 'payload': 'x' * (1024 * 1024 + 1)}, '2', '1'))
                with self.assertRaises(ConnectionClosed) as raised:
                    await asyncio.wait_for(big.recv(), 5)
                self.assertEqual(raised.exception.rcvd.code, 1009)
            async with connect(self.server.address()) as socket:  # the server itself is unharmed
                await join(socket, self.topic)
        self.scenario(body)


class RelayClientTests(unittest.TestCase):
    """The bot's real relay.Relay, talking to relay_server.py instead of FakeRealtime."""

    def setUp(self):
        self.server = RealRelayServer()
        self.addCleanup(self.server.close)
        self.device = FakeDevice()
        self.supervisor = remote.Supervisor(KEY, [sys.executable, '-c', 'pass'], self.device)
        self.relay = relay.Relay(self.server.url, TOKEN, self.supervisor)
        self.relay.start()
        self.addCleanup(self.relay.close)
        self.topic = f'realtime:{self.supervisor.channel}'

    def unsealed(self, sealed):
        self.assertEqual(remote.sign(KEY, remote.AGENT_SIGNATURE_PREFIX + sealed['body']), sealed['sig'])
        return json.loads(sealed['body'])

    def test_the_device_joins_and_the_dashboard_hears_it_and_talks_back(self):
        heard = {}

        async def body():
            async with connect(self.server.address()) as dashboard:
                await join(dashboard, self.topic, '9')
                # A state change on the device is broadcast to the channel members.
                self.supervisor.publish('state', self.supervisor.state())
                while True:  # other broadcasts (e.g. from joining) can arrive first; wait for this state
                    name, sealed = await next_broadcast(dashboard)
                    if name == 'state':
                        break
                heard['state'] = self.unsealed(sealed)
                await dashboard.send(frame(self.topic, 'broadcast',
                                           {'type': 'broadcast', 'event': 'hello', 'payload': {}}, '10', '9'))
                while True:  # the device answers a hello with its history and state
                    name, sealed = await next_broadcast(dashboard)
                    if name == 'history':
                        heard['history'] = self.unsealed(sealed)
                        break
                body_, sig = signed('open_coc', nonce='server-1')
                await dashboard.send(frame(self.topic, 'broadcast',
                                           {'type': 'broadcast', 'event': 'cmd',
                                            'payload': {'body': body_, 'sig': sig}}, '11', '9'))
                while True:  # ...and a signed command with its acknowledgement
                    name, sealed = await next_broadcast(dashboard)
                    if name == 'ack':
                        heard['ack'] = self.unsealed(sealed)
                        break

        asyncio.run(body())
        self.assertEqual(heard['state']['state'], 'idle')
        self.assertEqual(heard['history']['state']['state'], 'idle')
        self.assertEqual((heard['ack']['nonce'], heard['ack']['ok']), ('server-1', True))
        self.assertEqual(self.device.calls, ['open_coc'])


if __name__ == '__main__':
    unittest.main()
