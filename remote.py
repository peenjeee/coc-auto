"""Web dashboard link: a per-device API key, signed commands from the dashboard, and the bot run as a child
process whose log is streamed to the dashboard (online through the Supabase relay, or on localhost)."""
from __future__ import annotations

import base64
from collections import deque
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import queue
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Callable
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
BOT_SCRIPT, WEB_DIR = HERE / 'bot.py', HERE / 'web'
DEFAULT_PORT, DEFAULT_DASHBOARD = 8765, 'https://coc.0xpnj.dev'
HISTORY_LINES = 400
STOP_SECONDS = 20
STREAM_PING_SECONDS = 15
MAX_COMMAND_BYTES = 64 * 1024
STATIC_FILES = {'/': 'index.html', '/index.html': 'index.html', '/app.js': 'app.js', '/i18n.js': 'i18n.js',
                '/realtime.js': 'realtime.js', '/deploy.js': 'deploy.js', '/style.css': 'style.css',
                '/config.js': 'config.js', '/favicon.svg': 'favicon.svg'}
CONTENT_TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
                 '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml'}
LOCAL_HOSTS = ('localhost', '127.0.0.1')
# Device-to-dashboard messages are signed under their own prefix, so they never pass as a command.
AGENT_SIGNATURE_PREFIX = 'agent:'

KEY_PREFIX = 'coc_'
KEY_FILE = Path('.coc-auto') / 'device.json'
COMMAND_MAX_AGE_SECONDS = 120
ACTIONS = ('start', 'stop', 'restart', 'open_coc', 'close_coc', 'kill_ld', 'restart_ld', 'get_deploy', 'set_deploy')
VILLAGE_CHOICES = ('home', 'builder', 'both')
RESOURCES = {'home': ('gold', 'elixir', 'dark'), 'builder': ('gold', 'elixir', 'cart')}
# Option name, CLI flag, range; zero means "no limit" and is left out, except for recoveries.
LIMITS = (('searches', '--searches', 0, 1_000_000), ('hours', '--hours', 0, 1000),
          ('minutes', '--minutes', 0, 60_000), ('max_upgrades', '--max-upgrades', 0, 100_000))
TASK_OPTIONS = {
    'walls': {'walls', 'first', 'searches', 'hours', 'minutes', 'max_upgrades', 'max_recoveries'},
    'farm': {'home', 'builder', 'first', 'searches', 'hours', 'minutes', 'max_recoveries'},
    'attack': {'village', 'cycles', 'searches'},
    'dry': {'village'},
}
# Deploy screen -> its list of spread points: Home Village spells, Builder Base troop points.
DEPLOY_POINTS = {'home_scout': 'spells', 'builder_scout': 'points', 'builder_stage2': 'points'}
MAX_DEPLOY_POINTS = {'spells': 30, 'points': 20}
SNAPSHOT_SIZE, SNAPSHOT_QUALITY = (640, 360), 70


class CommandRefused(Exception):
    """A signed dashboard command asking for something the bot does not offer; nothing is run."""


class Unsigned(CommandRefused):
    """Not signed with this device's key, stale or replayed: anyone on the public channel can send that, so it
    is neither logged nor answered over the relay."""


def device_key(home: Path | None = None, renew: bool = False) -> str:
    """This device's API key, made once and kept in the user's home folder; renew replaces it."""
    path = (home or Path.home()) / KEY_FILE
    if not renew:
        try:
            key = json.loads(path.read_text(encoding='utf-8'))['key']
            if isinstance(key, str) and key.startswith(KEY_PREFIX) and len(key) >= 36:
                return key
        except (OSError, ValueError, KeyError, TypeError):
            pass
    key = KEY_PREFIX + secrets.token_urlsafe(24)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'key': key}) + '\n', encoding='utf-8')
    return key


def channel_name(key: str) -> str:
    """The relay channel for this key; a hash, so the channel name never gives the key away."""
    return 'coc-' + hashlib.sha256(key.encode()).hexdigest()[:32]


def sign(key: str, body: str) -> str:
    return hmac.new(key.encode(), body.encode(), hashlib.sha256).hexdigest()


def sealed(key: str, data: dict) -> dict:
    """A device message the dashboard can trust: the body and its signature under the agent prefix."""
    body = json.dumps({**data, 'ts': time.time()})
    return {'body': body, 'sig': sign(key, AGENT_SIGNATURE_PREFIX + body)}


def open_command(key: str, body: object, sig: object, seen: dict, not_before: float = 0.0) -> dict:
    """The command in body, once its signature, age and nonce check out. seen maps nonces to their time; a
    nonce is kept until its command is too old to pass the age check anyway."""
    if not isinstance(body, str) or not isinstance(sig, str) or not hmac.compare_digest(sign(key, body), sig):
        raise Unsigned('Perintah ditolak: tanda tangan tidak cocok dengan API key perangkat ini')
    try:
        command = json.loads(body)
    except ValueError as exc:
        raise Unsigned('Perintah ditolak: isi rusak') from exc
    if not isinstance(command, dict) or command.get('action') not in ACTIONS:
        raise CommandRefused('Perintah ditolak: aksi tidak dikenal')
    now, sent = time.time(), command.get('ts')
    # Commands from before this bot started could be replays from the public channel.
    if (isinstance(sent, bool) or not isinstance(sent, (int, float)) or not math.isfinite(sent)
            or abs(now - sent) > COMMAND_MAX_AGE_SECONDS or sent < not_before):
        raise Unsigned('Perintah ditolak: kedaluwarsa (cek jam perangkat)')
    for nonce, when in list(seen.items()):
        if when < now - 2 * COMMAND_MAX_AGE_SECONDS:
            del seen[nonce]
    nonce = command.get('nonce')
    if not isinstance(nonce, str) or not nonce or nonce in seen:
        raise Unsigned('Perintah ditolak: sudah pernah dipakai')
    seen[nonce] = sent
    if not isinstance(command.setdefault('args', {}), dict):
        raise CommandRefused('Perintah ditolak: pilihan rusak')
    return command


def whole(options: dict, name: str, low: int, high: int) -> int | None:
    value = options.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise CommandRefused(f'Pilihan {name} harus angka {low}..{high}')
    return value


def choice(options: dict, name: str, allowed: tuple, default: str | None = None) -> str:
    value = options.get(name, default)
    if not isinstance(value, str) or value not in allowed:
        raise CommandRefused(f'Pilihan {name} tidak dikenal')
    return value


def build_args(options: dict) -> list[str]:
    """bot.py arguments for the dashboard's choices; only the choices the CLI offers get through."""
    if not isinstance(options, dict):
        raise CommandRefused('Pilihan rusak')
    task = options.get('task')
    if not isinstance(task, str) or task not in TASK_OPTIONS:
        raise CommandRefused('Tugas tidak dikenal')
    unknown = set(options) - TASK_OPTIONS[task] - {'task'}
    if unknown:
        raise CommandRefused(f'Pilihan tidak berlaku untuk {task}: {", ".join(sorted(map(str, unknown)))}')
    if task in ('attack', 'dry'):
        args = ['--mode', choice(options, 'village', VILLAGE_CHOICES, 'both')]
        if task == 'dry':
            return args
        args += ['--cycles', str(whole(options, 'cycles', 1, 100) or 1)]
    elif task == 'walls':
        args = ['--walls', choice(options, 'walls', VILLAGE_CHOICES)]
    else:
        args = []
        for village in ('builder', 'home'):
            chosen = options.get(village) or []
            if not isinstance(chosen, list) or not all(isinstance(key, str) and key in RESOURCES[village]
                                                       for key in chosen):
                raise CommandRefused(f'Resource {village} tidak dikenal')
            if chosen:
                args += [f'--{village}', ','.join(key for key in RESOURCES[village] if key in chosen)]
        args = args or ['--until-full']
    if task != 'attack' and 'first' in options:
        args += ['--first', choice(options, 'first', ('builder', 'home'))]
    for name, flag, low, high in LIMITS:
        value = whole(options, name, low, high)
        if value:
            args += [flag, str(value)]
    recoveries = whole(options, 'max_recoveries', 0, 1000)
    if recoveries is not None:
        args += ['--max-recoveries', str(recoveries)]
    return args + ['--live']


class DeviceActions:
    """CoC and LDPlayer controls for the dashboard, through the adb and ldconsole set in config.json."""

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path

    def adb(self):
        import bot
        config = bot.load_config(self.config_path)
        return bot.ADB(bot.executable(config.get('adb'), 'ADB'), config['serial'], config['resolution'], live=True,
                       touch=config.get('touch_device'))

    def emulator(self):
        import bot
        config = bot.load_config(self.config_path)
        return bot.Emulator(bot.executable(config.get('ldconsole'), 'ldconsole'), config.get('ld_index', 0))

    def open_coc(self) -> None:
        import bot
        self.adb().launch(bot.GAME_ACTIVITY)

    def close_coc(self) -> None:
        self.adb().stop_game()

    def kill_ld(self) -> None:
        self.emulator().quit()

    def launch_ld(self) -> None:
        self.emulator().launch()


def pin(value: object, width: int, bottom: int) -> list[int]:
    """One deploy point: whole pixels on the battlefield, above the card bar."""
    if (not isinstance(value, list) or len(value) != 2
            or not all(isinstance(number, int) and not isinstance(number, bool) for number in value)):
        raise CommandRefused('Titik deploy harus [x, y] angka bulat')
    x, y = value
    if not (0 <= x < width and 0 <= y < bottom):
        raise CommandRefused('Titik deploy di luar layar atau di bar kartu')
    return [x, y]


class DeployPoints:
    """The deploy pins of config.json for the dashboard: read with the last screenshot of each deploy screen,
    and written back only after the checks bot.py makes on a calibration."""

    def __init__(self, config_path: Path, captures: Path, root: Path | None = None) -> None:
        self.config_path, self.captures = config_path, captures
        self.root = root or config_path.parent

    def config(self) -> dict:
        return json.loads(self.config_path.read_text(encoding='utf-8'))

    def read(self) -> dict:
        config = self.config()
        screens = {}
        for name, kind in DEPLOY_POINTS.items():
            screen = config['screens'].get(name, {})
            auto = screen.get('auto', {})
            if 'deploy' in screen and kind in auto:
                screens[name] = {'kind': kind, 'deploy': screen['deploy'], 'points': auto[kind], 'bar': auto['bar']}
        return {'resolution': config['resolution'], 'screens': screens}

    def snapshots(self) -> list[dict]:
        """The last frame bot.py saved on each deploy screen, small enough for one relay message."""
        import cv2
        shots = []
        for name in DEPLOY_POINTS:
            image = cv2.imread(str(self.captures / f'deploy-{name}.jpg'))
            if image is None:
                continue
            small = cv2.resize(image, SNAPSHOT_SIZE, interpolation=cv2.INTER_AREA)
            ok, data = cv2.imencode('.jpg', small, [cv2.IMWRITE_JPEG_QUALITY, SNAPSHOT_QUALITY])
            if ok:
                shots.append({'screen': name, 'image': 'data:image/jpeg;base64,' + base64.b64encode(data).decode()})
        return shots

    def write(self, screen: object, deploy: object, points: object) -> None:
        import bot
        if not isinstance(screen, str) or screen not in DEPLOY_POINTS:
            raise CommandRefused('Layar deploy tidak dikenal')
        config = self.config()
        kind = DEPLOY_POINTS[screen]
        target = config['screens'][screen]
        width, bottom = config['resolution'][0], target['auto']['bar'][1]
        if not isinstance(points, list) or not 1 <= len(points) <= MAX_DEPLOY_POINTS[kind]:
            raise CommandRefused(f'Jumlah titik harus 1..{MAX_DEPLOY_POINTS[kind]}')
        target['deploy'] = pin(deploy, width, bottom)
        target['auto'][kind] = [pin(point, width, bottom) for point in points]
        try:
            bot.validate_config(config, self.root, 'both')
        except bot.BotError as exc:
            raise CommandRefused(f'Titik deploy ditolak: {exc}') from exc
        text = json.dumps(config, indent=2) + '\n'
        backup, temporary = self.config_path.with_suffix('.json.bak'), self.config_path.with_suffix('.json.tmp')
        backup.write_text(self.config_path.read_text(encoding='utf-8'), encoding='utf-8')
        temporary.write_text(text, encoding='utf-8')
        os.replace(temporary, self.config_path)


class Supervisor:
    """Runs bot.py as a child process, keeps its log for the dashboard and carries out signed commands."""

    def __init__(self, key: str, command: list[str], device, deploy: DeployPoints | None = None) -> None:
        self.key, self.channel = key, channel_name(key)
        self.command_base, self.device, self.deploy = command, device, deploy
        self.lines: deque = deque(maxlen=HISTORY_LINES)
        self.subscribers: list[Callable[[str, object], None]] = []
        self.child: subprocess.Popen | None = None
        self.args: list[str] = []
        self.since: float | None = None
        self.started = time.time()
        self.seen: dict = {}
        self.host = socket.gethostname()
        self.lock = threading.Lock()  # log, history and listeners; never held while waiting
        self.nonce_lock = threading.Lock()
        self.control = threading.RLock()  # one command at a time, from any dashboard

    def subscribe(self, listener: Callable[[str, object], None]) -> None:
        with self.lock:
            self.subscribers.append(listener)

    def unsubscribe(self, listener: Callable[[str, object], None]) -> None:
        with self.lock:
            if listener in self.subscribers:
                self.subscribers.remove(listener)

    def publish(self, kind: str, data: object) -> None:
        with self.lock:
            listeners = list(self.subscribers)
        for listener in listeners:
            listener(kind, data)

    def log(self, line: str) -> None:
        with self.lock:
            self.lines.append(line)
        print(line, flush=True)
        self.publish('line', line)

    def history(self) -> list[str]:
        with self.lock:
            return list(self.lines)

    def running(self) -> bool:
        child = self.child
        return child is not None and child.poll() is None

    def state(self) -> dict:
        return {'state': 'running' if self.running() else 'idle', 'args': self.args, 'host': self.host,
                'since': self.since}

    def start(self, args: list[str]) -> None:
        with self.control:
            self.stop()
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
            child = subprocess.Popen(
                [*self.command_base, *args, '--worker'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', creationflags=flags,
                start_new_session=os.name != 'nt',
                env={**os.environ, 'PYTHONUNBUFFERED': '1', 'PYTHONIOENCODING': 'utf-8'})
            self.child, self.args, self.since = child, list(args), time.time()
            self.log(f'REMOTE: menjalankan python bot.py {" ".join(args)}')
            threading.Thread(target=self.pump, args=(child,), daemon=True).start()
        self.publish('state', self.state())

    def pump(self, child: subprocess.Popen) -> None:
        for raw in child.stdout:
            line = raw.rstrip('\r\n')
            if line:
                self.log(line)
        code = child.wait()
        self.log(f'REMOTE: bot berhenti (exit {code}); menunggu perintah dari dashboard')
        self.publish('state', self.state())

    def stop(self) -> bool:
        """Stop the bot like Ctrl+C, so a battle or upgrade in progress is left the way the bot leaves it;
        kill it if it does not end in time."""
        with self.control:
            child = self.child
            if child is None or child.poll() is not None:
                return False
            self.log('REMOTE: menghentikan bot')
            child.send_signal(signal.CTRL_BREAK_EVENT if os.name == 'nt' else signal.SIGINT)
            try:
                child.wait(STOP_SECONDS)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            return True

    def command(self, body: object, sig: object) -> dict:
        """Carry out a signed dashboard command: {'ok', 'code', 'message'} plus 'data' for what it read."""
        try:
            with self.nonce_lock:
                command = open_command(self.key, body, sig, self.seen, self.started)
            with self.control:
                code, message, data = self.carry_out(command['action'], command['args'])
            result = {'ok': True, 'code': code, 'message': message}
            if data is not None:
                result['data'] = data
        except Unsigned as exc:
            return {'ok': False, 'code': 'unsigned', 'message': str(exc)}
        except CommandRefused as exc:
            text = str(exc)
            result = {'ok': False, 'code': 'refused',
                      'message': text if text.startswith('Perintah ditolak') else f'Perintah ditolak: {text}'}
        except Exception as exc:  # A failed adb/ldconsole call is reported to the dashboard, never fatal here.
            result = {'ok': False, 'code': 'failed', 'message': f'Gagal: {exc}'}
        self.log(f'REMOTE: {result["message"]}')
        return result

    def carry_out(self, action: str, options: dict) -> tuple[str, str, dict | None]:
        if action == 'start':
            args = build_args(options)
            self.start(args)
            return 'started', 'Bot dijalankan: python bot.py ' + ' '.join(args), None
        if action == 'restart':
            if not self.args:
                raise CommandRefused('belum ada perintah untuk diulang; pakai Start')
            self.start(self.args)
            return 'restarted', 'Bot dijalankan ulang: python bot.py ' + ' '.join(self.args), None
        if action == 'stop':
            return ('stopped', 'Bot dihentikan', None) if self.stop() else ('not_running', 'Bot memang tidak berjalan', None)
        if action == 'open_coc':
            self.device.open_coc()
            return 'coc_opened', 'CoC dibuka', None
        if action in ('get_deploy', 'set_deploy'):
            if self.deploy is None:
                raise CommandRefused('titik deploy tidak tersedia di perangkat ini')
            if action == 'set_deploy':
                self.deploy.write(options.get('screen'), options.get('deploy'), options.get('points'))
                return 'deploy_saved', 'Titik deploy disimpan; berlaku saat Start berikutnya', None
            data = self.deploy.read()
            for shot in self.deploy.snapshots():
                self.publish('snapshot', shot)
            return 'deploy_loaded', 'Titik deploy dimuat', data
        # Closing the game or the emulator under a running bot would only start its recovery.
        self.stop()
        if action == 'close_coc':
            self.device.close_coc()
            return 'coc_closed', 'Bot dihentikan dan CoC ditutup', None
        self.device.kill_ld()
        if action == 'kill_ld':
            return 'ld_off', 'Bot dihentikan dan LDPlayer dimatikan', None
        self.device.launch_ld()
        return 'ld_restarted', 'LDPlayer dinyalakan ulang', None


def event(kind: str, data: object) -> bytes:
    return f'event: {kind}\ndata: {json.dumps(data)}\n\n'.encode()


def serve_dashboard(supervisor: Supervisor, web_dir: Path, port: int) -> ThreadingHTTPServer:
    """The dashboard on localhost: the page, a live log stream (SSE) and signed commands."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def local(self) -> bool:
            # A site that points its own name at 127.0.0.1 is refused as well.
            return self.headers.get('Host', '').rsplit(':', 1)[0] in LOCAL_HOSTS

        def same_origin(self) -> bool:
            """Only the dashboard page itself may post: other sites send an Origin, and a JSON body forces a
            preflight this server never answers."""
            origin = self.headers.get('Origin')
            if origin is not None and urlsplit(origin).hostname not in LOCAL_HOSTS:
                return False
            return self.headers.get('Content-Type', '').split(';')[0].strip() == 'application/json'

        def do_GET(self) -> None:
            if not self.local():
                self.send_error(403)
                return
            url = urlsplit(self.path)
            if url.path == '/api/stream':
                self.stream(parse_qs(url.query).get('channel', [''])[0])
                return
            name = STATIC_FILES.get(url.path)
            if name is None or not (web_dir / name).is_file():
                self.send_error(404)
                return
            self.reply(200, CONTENT_TYPES.get(Path(name).suffix, 'application/octet-stream'),
                       (web_dir / name).read_bytes())

        def do_POST(self) -> None:
            if not self.local() or self.path != '/api/command' or not self.same_origin():
                self.send_error(403)
                return
            try:
                length = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                length = -1
            if not 0 <= length <= MAX_COMMAND_BYTES:
                self.send_error(413)
                return
            try:
                payload = json.loads(self.rfile.read(length) or b'{}')
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                result = supervisor.command(payload.get('body'), payload.get('sig'))
            else:
                result = {'ok': False, 'code': 'unsigned', 'message': 'Perintah ditolak: isi rusak'}
            data = json.dumps({**result, 'state': supervisor.state()}).encode()
            self.reply(200 if result['ok'] else 403, 'application/json', data)

        def reply(self, status: int, content_type: str, data: bytes) -> None:
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy', "frame-ancestors 'none'")
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(data)

        def stream(self, channel: str) -> None:
            if not hmac.compare_digest(channel, supervisor.channel):
                self.send_error(403)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            events: queue.Queue = queue.Queue()

            def listener(kind: str, data: object) -> None:
                events.put((kind, data))
            supervisor.subscribe(listener)
            try:
                first = [event('state', supervisor.state())] + [event('line', line) for line in supervisor.history()]
                self.wfile.write(b''.join(first))
                self.wfile.flush()
                while True:
                    try:
                        chunk = event(*events.get(timeout=STREAM_PING_SECONDS))
                    except queue.Empty:
                        chunk = b': ping\n\n'
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except OSError:
                pass  # The page was closed.
            finally:
                supervisor.unsubscribe(listener)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def remote_settings(config_path: Path) -> dict:
    """The `remote` block of config.json: Supabase project URL and publishable key, local port, online URL."""
    try:
        raw = json.loads(config_path.read_text(encoding='utf-8')).get('remote') or {}
    except (OSError, ValueError, AttributeError):
        raw = {}
    port = raw.get('port', DEFAULT_PORT)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        port = DEFAULT_PORT
    return {'url': str(raw.get('supabase_url') or ''), 'key': str(raw.get('supabase_key') or ''), 'port': port,
            'dashboard': str(raw.get('dashboard') or DEFAULT_DASHBOARD)}


def supervise(job: list[str], config_path: Path, renew_key: bool = False) -> int:
    """Every bot command runs under this: show the device's API key, open the dashboards, run the job as a
    child, then stay on standby for Start from the dashboard until Ctrl+C."""
    settings = remote_settings(config_path)
    key = device_key(renew=renew_key)
    supervisor = Supervisor(key, [sys.executable, str(BOT_SCRIPT), '--config', str(config_path)],
                            DeviceActions(config_path), DeployPoints(config_path, config_path.parent / 'captures'))
    # The key goes to this terminal only, never into the streamed log.
    print('=' * 60)
    print(f'API key perangkat : {key}' + ('  (BARU; key lama tidak berlaku lagi)' if renew_key else ''))
    print(f'Dashboard lokal   : http://localhost:{settings["port"]}')
    print(f'Dashboard online  : {settings["dashboard"]}')
    print('Masukkan API key itu di dashboard untuk melihat log dan mengendalikan bot. Ctrl+C untuk keluar.')
    print('=' * 60, flush=True)
    try:
        server = serve_dashboard(supervisor, WEB_DIR, settings['port'])
    except OSError as exc:
        server = None
        supervisor.log(f'REMOTE: dashboard lokal tidak bisa dibuka di port {settings["port"]} ({exc})')
    link = None
    if settings['url'] and settings['key']:
        import relay
        link = relay.Relay(settings['url'], settings['key'], supervisor)
        link.start()
    else:
        supervisor.log('REMOTE: relay online belum diatur (config.json remote); hanya dashboard lokal')
    if job:
        supervisor.start(job)
    else:
        supervisor.log('REMOTE: standby; tekan Start di dashboard')
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        supervisor.log('REMOTE: keluar (Ctrl+C)')
        supervisor.stop()
    finally:
        if link:
            link.close()
        if server:
            server.shutdown()
    return 0
