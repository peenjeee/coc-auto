"""Supabase Realtime relay for the web dashboard. The PC connects out to the device's channel, so no port has
to be opened. Everything it broadcasts (log, state heartbeat, history, command answers, deploy screenshots) is
signed with the device key, so a stranger on the public channel can neither fake the device nor read commands
into it; signed dashboard commands go to the supervisor (Realtime protocol 1.0.0, JSON text frames)."""
from __future__ import annotations

import asyncio
from collections import deque
import json
import threading
import time
from urllib.parse import urlencode

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

import remote

HEARTBEAT_SECONDS = 20  # Realtime drops a socket that sends nothing for 25 s.
STATE_EVERY_SECONDS = 15  # The dashboard counts the device offline when this goes quiet.
SEND_EVERY_SECONDS = 0.5
MAX_LINES_PER_MESSAGE = 200
HELLO_EVERY_SECONDS = 3
JOIN_SECONDS = 15
RECONNECT_FIRST_SECONDS, RECONNECT_MAX_SECONDS = 2, 60


class RelayError(Exception):
    """The channel refused the join or closed; the relay connects again."""


def nonce_of(body: object) -> str | None:
    try:
        nonce = json.loads(body).get('nonce') if isinstance(body, str) else None
    except (ValueError, AttributeError):
        return None
    return nonce if isinstance(nonce, str) else None


class Relay:
    def __init__(self, url: str, apikey: str, supervisor) -> None:
        base = url.rstrip('/').replace('https://', 'wss://', 1).replace('http://', 'ws://', 1)
        self.address = f'{base}/realtime/v1/websocket?' + urlencode({'apikey': apikey, 'vsn': '1.0.0'})
        self.supervisor = supervisor
        self.topic = f'realtime:{supervisor.channel}'
        self.heartbeat_seconds, self.state_seconds = HEARTBEAT_SECONDS, STATE_EVERY_SECONDS
        # Lines wait here only while connected; a dashboard that comes later asks for the history instead.
        self.lines: deque = deque(maxlen=remote.HISTORY_LINES)
        self.shots: deque = deque(maxlen=len(remote.DEPLOY_POINTS))
        self.state_changed = True
        self.connected: bool | None = None
        self.last_hello = 0.0
        self.ref, self.join_ref = 0, None
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=lambda: asyncio.run(self.main()), daemon=True)
        supervisor.subscribe(self.on_event)

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.stopping.set()
        self.supervisor.unsubscribe(self.on_event)

    def on_event(self, kind: str, data: object) -> None:
        with self.lock:
            if kind == 'state':
                self.state_changed = True
            elif not self.connected:
                return
            elif kind == 'line':
                self.lines.append(data)
            elif kind == 'snapshot':
                self.shots.append(data)

    async def main(self) -> None:
        wait = RECONNECT_FIRST_SECONDS
        while not self.stopping.is_set():
            try:
                # Uncompressed: Realtime acknowledges permessage-deflate broadcasts but never passes them on.
                async with connect(self.address, open_timeout=JOIN_SECONDS, ping_interval=None,
                                   compression=None) as socket:
                    await self.session(socket)
                wait = RECONNECT_FIRST_SECONDS
            except (OSError, asyncio.TimeoutError, WebSocketException, RelayError, ValueError) as exc:
                self.report(False, exc)
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline and not self.stopping.is_set():
                await asyncio.sleep(0.2)
            wait = min(wait * 2, RECONNECT_MAX_SECONDS)

    def report(self, connected: bool, reason: object = None) -> None:
        """Log only when the online link comes or goes, not on every retry."""
        if self.connected == connected:
            return
        with self.lock:
            self.connected = connected
            self.lines.clear()
        if connected:
            self.supervisor.log('REMOTE: dashboard online tersambung')
        else:
            self.supervisor.log(f'REMOTE: dashboard online belum tersambung ({reason or "koneksi putus"}); dicoba lagi')

    async def session(self, socket) -> None:
        self.ref = 0
        self.join_ref = self.next_ref()
        config = {'broadcast': {'ack': False, 'self': False}, 'presence': {'enabled': False}, 'private': False}
        # The join's own ref is its join_ref; broadcasts that carry another one are dropped.
        await self.push(socket, self.topic, 'phx_join', {'config': config}, self.join_ref, self.join_ref)
        await self.joined(socket)
        self.report(True)
        with self.lock:
            self.state_changed = True
        sender = asyncio.create_task(self.sender(socket))
        try:
            async for raw in socket:
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue  # Junk on the public channel.
                if isinstance(message, dict):
                    await self.received(socket, message)
        finally:
            sender.cancel()

    async def joined(self, socket) -> None:
        deadline = time.monotonic() + JOIN_SECONDS
        while True:
            message = json.loads(await asyncio.wait_for(socket.recv(), max(0.1, deadline - time.monotonic())))
            if isinstance(message, dict) and message.get('event') == 'phx_reply' and message.get('ref') == self.join_ref:
                if (message.get('payload') or {}).get('status') != 'ok':
                    raise RelayError(f'channel menolak: {message.get("payload")}')
                return

    async def received(self, socket, message: dict) -> None:
        event = message.get('event')
        if event in ('phx_error', 'phx_close') and message.get('topic') == self.topic:
            raise RelayError(event)
        payload = message.get('payload')
        if event != 'broadcast' or not isinstance(payload, dict):
            return
        name, data = payload.get('event'), payload.get('payload')
        if name == 'hello':
            # Answered at most every few seconds, so nobody can make the device flood the channel.
            if time.monotonic() - self.last_hello >= HELLO_EVERY_SECONDS:
                self.last_hello = time.monotonic()
                await self.broadcast(socket, 'history', {'lines': self.supervisor.history(),
                                                         'state': self.supervisor.state()})
        elif name == 'cmd' and isinstance(data, dict):
            # Stopping the bot can take a while; keep the socket loop free meanwhile.
            result = await asyncio.to_thread(self.supervisor.command, data.get('body'), data.get('sig'))
            if result['code'] != 'unsigned':
                await self.broadcast(socket, 'ack', {'nonce': nonce_of(data.get('body')), **result,
                                                     'state': self.supervisor.state()})

    async def sender(self, socket) -> None:
        """Batched log lines, deploy screenshots, state on change and as a heartbeat, socket heartbeats."""
        beat = shown = time.monotonic()
        while True:
            if self.stopping.is_set():
                await socket.close()
                return
            with self.lock:
                lines = [self.lines.popleft() for _ in range(min(len(self.lines), MAX_LINES_PER_MESSAGE))]
                shots = [self.shots.popleft() for _ in range(len(self.shots))]
                changed, self.state_changed = self.state_changed, False
            if changed or time.monotonic() - shown >= self.state_seconds:
                await self.broadcast(socket, 'state', self.supervisor.state())
                shown = time.monotonic()
            if lines:
                await self.broadcast(socket, 'log', {'lines': lines})
            for shot in shots:
                await self.broadcast(socket, 'snapshot', shot)
            if time.monotonic() - beat >= self.heartbeat_seconds:
                await self.push(socket, 'phoenix', 'heartbeat', {}, self.next_ref(), None)
                beat = time.monotonic()
            await asyncio.sleep(SEND_EVERY_SECONDS)

    def next_ref(self) -> str:
        self.ref += 1
        return str(self.ref)

    async def push(self, socket, topic: str, event: str, payload: dict, ref: str, join_ref: str | None = None) -> None:
        await socket.send(json.dumps({'topic': topic, 'event': event, 'payload': payload, 'ref': ref,
                                      'join_ref': join_ref}))

    async def broadcast(self, socket, name: str, data: dict) -> None:
        sealed = remote.sealed(self.supervisor.key, data)
        await self.push(socket, self.topic, 'broadcast', {'type': 'broadcast', 'event': name, 'payload': sealed},
                        self.next_ref(), self.join_ref)
