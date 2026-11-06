"""Self-hosted online relay for the web dashboard: a WebSocket server speaking the small part of the
Phoenix/Realtime protocol (vsn 1.0.0, JSON text frames) that relay.py on the PC and web/realtime.js in the
browser already use, so both clients only change the address they connect to.

Clients connect to ws://<host>/realtime/v1/websocket?apikey=<token>&vsn=1.0.0, join one `realtime:<channel>`
topic with `phx_join`, keep the socket alive with `heartbeat` on the `phoenix` topic and exchange `broadcast`
messages. A broadcast goes to every OTHER socket joined on the same topic (the clients ask for self: false);
broadcasts from a socket that never joined the topic are dropped. The relay never looks inside a broadcast:
commands and device messages are signed with the device API key, so it could neither read nor forge them.

The token only keeps casual connections out. It is public by design (it ships in web/config.js), so it is no
secret; set it with the RELAY_TOKEN environment variable or --token, matching remote.relay_key in
config.json and RELAY_KEY in web/config.js.

Limits, chosen for one device plus a few dashboard tabs: frames of at most 1 MiB (live view frames are
50-100 KB), at most 64 topics with 8 sockets each, about 20 messages per second per socket with a short
burst of 40, and a socket that sends nothing for 60 seconds is dropped (both clients heartbeat every
20 seconds). Malformed or unknown messages are ignored; a bad client cannot take the server down.

Run it with a venv that only needs websockets>=14,<16:

    RELAY_TOKEN=... python relay_server.py            # 127.0.0.1:4000
    python relay_server.py --host 127.0.0.1 --port 4000 --token ...
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
import hmac
import http
import json
import os
import sys
import time
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

PATH = '/realtime/v1/websocket'
MAX_FRAME_BYTES = 1024 * 1024  # websockets max_size: a bigger frame closes that socket with code 1009.
MAX_TOPICS = 64
MAX_SOCKETS_PER_TOPIC = 8
RATE_PER_SECOND = 20.0
RATE_BURST = 40
IDLE_SECONDS = 60


class Bucket:
    """One socket's message allowance: RATE_PER_SECOND refills, up to a RATE_BURST of credit."""

    def __init__(self) -> None:
        self.credit = float(RATE_BURST)
        self.at = time.monotonic()

    def take(self) -> bool:
        now = time.monotonic()
        self.credit = min(RATE_BURST, self.credit + (now - self.at) * RATE_PER_SECOND)
        self.at = now
        if self.credit < 1:
            return False
        self.credit -= 1
        return True


def reply(topic: str, ref: object, status: str = 'ok') -> str:
    return json.dumps({'topic': topic, 'event': 'phx_reply', 'ref': ref,
                       'payload': {'status': status, 'response': {}}})


class RelayServer:
    def __init__(self, token: str) -> None:
        self.token = token
        self.topics: dict[str, set] = defaultdict(set)

    def process_request(self, connection, request):
        """Only the one websocket path, and only with the configured token as apikey."""
        parts = urlsplit(request.path)
        if parts.path != PATH:
            return connection.respond(http.HTTPStatus.NOT_FOUND, 'Not Found\n')
        apikey = parse_qs(parts.query).get('apikey', [''])[0]
        if not apikey or not hmac.compare_digest(apikey, self.token):
            return connection.respond(http.HTTPStatus.FORBIDDEN, 'Forbidden\n')
        return None

    async def handle(self, socket) -> None:
        joined: set[str] = set()
        bucket = Bucket()
        last_seen = time.monotonic()
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(socket.recv(), max(0.1, IDLE_SECONDS - (time.monotonic() - last_seen)))
                except asyncio.TimeoutError:
                    break  # Idle for IDLE_SECONDS; both real clients heartbeat every 20.
                last_seen = time.monotonic()
                if not bucket.take():
                    continue  # Over the rate: the message is dropped, the socket stays.
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(message, dict):
                    await self.dispatch(socket, joined, message)
        except ConnectionClosed:
            pass
        finally:
            for topic in joined:
                self.topics[topic].discard(socket)
                if not self.topics[topic]:
                    del self.topics[topic]

    async def dispatch(self, socket, joined: set[str], message: dict) -> None:
        topic, event, ref = message.get('topic'), message.get('event'), message.get('ref')
        if event == 'heartbeat' and topic == 'phoenix':
            await socket.send(reply('phoenix', ref))
        elif event == 'phx_join' and isinstance(topic, str) and topic.startswith('realtime:'):
            if topic not in joined:
                if topic not in self.topics and len(self.topics) >= MAX_TOPICS:
                    await socket.send(reply(topic, ref, 'error'))
                    return
                if len(self.topics[topic]) >= MAX_SOCKETS_PER_TOPIC:
                    await socket.send(reply(topic, ref, 'error'))
                    return
                self.topics[topic].add(socket)
                joined.add(topic)
            await socket.send(reply(topic, ref))
        elif event == 'phx_leave' and topic in joined:
            joined.discard(topic)
            self.topics[topic].discard(socket)
            if not self.topics[topic]:
                del self.topics[topic]
            await socket.send(reply(topic, ref))
        elif event == 'broadcast' and topic in joined and isinstance(message.get('payload'), dict):
            frame = json.dumps({'topic': topic, 'event': 'broadcast', 'ref': None, 'payload': message['payload']})
            for other in list(self.topics[topic]):
                if other is socket:
                    continue  # self: false, as both clients configure it.
                try:
                    await other.send(frame)
                except ConnectionClosed:
                    pass  # Its own handler cleans the membership up.


async def start_server(host: str, port: int, token: str):
    """The listening server; tests start (and close) their own on an ephemeral port."""
    relay = RelayServer(token)
    return await serve(relay.handle, host, port, max_size=MAX_FRAME_BYTES, ping_interval=None,
                       process_request=relay.process_request)


async def run(host: str, port: int, token: str) -> None:
    server = await start_server(host, port, token)
    print(f'RELAY: server relay online di ws://{host}:{port}{PATH}', flush=True)
    try:
        await asyncio.Future()
    finally:
        server.close()


def main() -> int:
    parser = argparse.ArgumentParser(description='Online relay for the coc-auto web dashboard (Phoenix/Realtime subset).')
    parser.add_argument('--host', default=os.environ.get('RELAY_HOST', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=int(os.environ.get('RELAY_PORT', '4000')))
    parser.add_argument('--token', default=os.environ.get('RELAY_TOKEN', ''),
                        help='the apikey clients must present; or set RELAY_TOKEN')
    args = parser.parse_args()
    if not args.token:
        print('RELAY: token belum diatur; isi RELAY_TOKEN atau --token (samakan dengan config.json dan web/config.js)',
              file=sys.stderr)
        return 2
    try:
        asyncio.run(run(args.host, args.port, args.token))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
