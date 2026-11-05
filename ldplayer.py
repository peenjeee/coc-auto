"""Finds LDPlayer for the dashboard's setup panel: its folder (adb.exe, ldconsole.exe), the running instance, the
adb serial, the touchscreen node and the screen size. Detection only lists and asks; the one thing written is an
instance's screen (resolution and DPI) in its config file, when the dashboard asks for it."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
from typing import Callable

TIMEOUT_SECONDS = 15
FIRST_PORT = 5554
DEFAULT_RESOLUTION = (1280, 720)
DRIVES = 'CDEFG'
FOLDER_PATTERNS = ('LDPlayer/LDPlayer*', 'LDPlayer*', 'Program Files/LDPlayer*', 'Program Files (x86)/LDPlayer*',
                   'XuanZhi/LDPlayer*')
PLAYER_PROCESSES = 'Get-Process dnplayer,dnmultiplayer -ErrorAction SilentlyContinue | ForEach-Object { $_.Path }'
TOUCH_NODE = re.compile(r'/dev/input/event\d{1,2}')
SIZE = re.compile(r'(Override|Physical) size:\s*(\d+)x(\d+)')
AXIS = re.compile(r'ABS_MT_POSITION_([XY])\s*:.*?max (\d+)')
CALIBRATED_DPI = 240
RESOLUTION_KEY, DPI_KEY = 'advancedSettings.resolution', 'advancedSettings.resolutionDpi'
ADB_DEBUG_KEY = 'basicSettings.adbDebug'
ADB_DEBUG_HINT = ('ADB debugging LDPlayer mati: Pengaturan LDPlayer > Lainnya > Debug ADB > Buka koneksi lokal (atau '
                  'tombol "sesuaikan layar" di dashboard), lalu nyalakan ulang LDPlayer')

Runner = Callable[[list], str]


def run(command: list) -> str:
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    return subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                          timeout=TIMEOUT_SECONDS, creationflags=flags).stdout


def safe(runner: Runner, command: list, problems: list, label: str) -> str:
    """The command's output; a tool that fails or hangs becomes a problem to show, not a crash."""
    try:
        return runner(command) or ''
    except (OSError, subprocess.SubprocessError) as exc:
        problems.append(f'{label} gagal: {exc}')
        return ''


def is_ld_folder(path: Path) -> bool:
    return (path / 'adb.exe').is_file() and (path / 'ldconsole.exe').is_file()


def running_folders(runner: Runner) -> list:
    """Folders of the LDPlayer windows open now. Only one way of finding LDPlayer, so a failed process list
    leaves the folder search to the others."""
    try:
        output = runner(['powershell', '-NoProfile', '-Command', PLAYER_PROCESSES]) or ''
    except (OSError, subprocess.SubprocessError):
        return []
    return [Path(line.strip()).parent for line in output.splitlines() if line.strip()]


def search_folders(roots: list) -> list:
    return [path for root in roots for pattern in FOLDER_PATTERNS for path in sorted(Path(root).glob(pattern))]


def drive_roots() -> list:
    return [Path(f'{letter}:/') for letter in DRIVES if Path(f'{letter}:/').exists()] if os.name == 'nt' else []


def ld_folders(configured: list, runner: Runner, roots: list) -> list:
    """LDPlayer folders, best first: the one config.json points at, then a running one, then installed ones."""
    candidates = [Path(value).parent for value in configured if isinstance(value, str) and value]
    candidates += running_folders(runner) + search_folders(roots)
    folders = []
    for path in candidates:
        if is_ld_folder(path) and str(path.resolve()) not in folders:
            folders.append(str(path.resolve()))
    return folders


def parse_instances(text: str) -> list:
    """`ldconsole list2` rows: index, title, window handles, android started (1/0), process ids, ..."""
    instances = []
    for line in text.splitlines():
        parts = line.strip().split(',')
        if len(parts) >= 5 and parts[0].isdigit():
            index = int(parts[0])
            instances.append({'index': index, 'name': parts[1], 'running': parts[4] == '1',
                              'serial': f'emulator-{FIRST_PORT + 2 * index}'})
    return instances


def parse_devices(text: str) -> list:
    """Serials `adb devices` lists as connected; offline and unauthorized ones cannot take input."""
    rows = [line.split() for line in text.splitlines()[1:]]
    return [row[0] for row in rows if len(row) >= 2 and row[1] == 'device']


def parse_touch(text: str) -> str | None:
    """The input node that reports multitouch positions, from `getevent -pl`."""
    for block in text.split('add device')[1:]:
        node = TOUCH_NODE.search(block)
        if node and 'ABS_MT_POSITION_X' in block:
            return node.group()
    return None


def touch_range(text: str, node: str) -> tuple | None:
    """(x max, y max) of a node's multitouch axes in `getevent -pl`; None for no such node or no such axes."""
    for block in text.split('add device')[1:]:
        lines = block.splitlines()
        if lines and lines[0].split(':', 1)[-1].strip() == node:
            axes = dict(AXIS.findall(block))
            return (int(axes['X']), int(axes['Y'])) if {'X', 'Y'} <= set(axes) else None
    return None


def touch_problem(text: str, node: str, resolution: list) -> str | None:
    """Why taps sent to node would land in the wrong place or nowhere, from `uname -m` then `getevent -pl`; None
    when they land. The bot writes 64-bit input events in screen pixels, and a wrong node fails silently."""
    kernel = text.split('\n', 1)[0].strip()
    if '64' not in kernel:
        return f'LDPlayer {kernel or "?"} 32-bit tidak didukung (sentuhan bot untuk Android 64-bit); pakai LDPlayer 9 ke atas'
    if f': {node}' not in text:
        return f'{node} tidak ada di LDPlayer ini; isi touch_device lewat deteksi otomatis di dashboard'
    span = touch_range(text, node)
    if span is None:
        return f'{node} bukan layar sentuh (tanpa ABS_MT_POSITION_X/Y); isi touch_device lewat deteksi otomatis'
    width, height = resolution
    if span != (width - 1, height - 1):
        return (f'Layar sentuh {node} {span[0]}x{span[1]} tidak cocok dengan layar {width}x{height}; samakan '
                'resolusi LDPlayer dengan kalibrasi (tombol "sesuaikan layar" di dashboard)')
    return None


def parse_size(text: str) -> list | None:
    """Screen size from `wm size`; an override is what the screen really shows."""
    sizes = {kind: [int(width), int(height)] for kind, width, height in SIZE.findall(text)}
    return sizes.get('Override') or sizes.get('Physical')


def instance_config(console: str, index: int) -> Path:
    return Path(console).parent / 'vms' / 'config' / f'leidian{index}.config'


def read_display(path: Path) -> dict:
    """The instance's screen as LDPlayer will start it: {'width', 'height', 'dpi'}."""
    config = json.loads(path.read_text(encoding='utf-8'))
    size, dpi = config.get(RESOLUTION_KEY), config.get(DPI_KEY)
    if not isinstance(size, dict) or not all(isinstance(value, int) for value in (size.get('width'), size.get('height'), dpi)):
        raise ValueError(f'{path.name}: resolusi/DPI tidak dikenal')
    return {'width': size['width'], 'height': size['height'], 'dpi': dpi}


def adb_debug(path: Path) -> int | None:
    """LDPlayer's ADB debugging switch (1 = local connection open); None when the file does not say."""
    value = json.loads(path.read_text(encoding='utf-8')).get(ADB_DEBUG_KEY)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def write_display(path: Path, display: dict, adb_debug: int | None = None) -> None:
    """Change only the screen (and ADB debugging, when given) and keep every other setting, in LDPlayer's own
    layout (4-space JSON, CRLF). `ldconsole modify` is never used: it rewrites the whole file and drops settings."""
    config = json.loads(path.read_text(encoding='utf-8'))
    config[RESOLUTION_KEY] = {**config.get(RESOLUTION_KEY, {}), 'width': display['width'], 'height': display['height']}
    config[DPI_KEY] = display['dpi']
    if adb_debug is not None:
        config[ADB_DEBUG_KEY] = adb_debug
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_bytes(json.dumps(config, indent=4, ensure_ascii=False).replace('\n', '\r\n').encode('utf-8'))
    os.replace(temporary, path)


def pick_instance(instances: list, configured: object) -> dict | None:
    running = [instance for instance in instances if instance['running']]
    return next((instance for instance in running if instance['index'] == configured), running[0] if running else None)


def detect(config: dict, runner: Runner = run, roots: list | None = None) -> dict:
    """What the setup fields should hold on this PC; 'problems' says what could not be found or does not fit."""
    problems: list = []
    folders = ld_folders([config.get('ldconsole'), config.get('adb')], runner,
                         drive_roots() if roots is None else roots)
    found: dict = {'folders': folders, 'problems': problems}
    if not folders:
        problems.append('LDPlayer tidak ditemukan; isi path adb.exe dan ldconsole.exe dari folder LDPlayer manual')
        return found
    folder = Path(folders[0])
    adb = found['adb'] = str(folder / 'adb.exe')
    console = found['ldconsole'] = str(folder / 'ldconsole.exe')
    instances = found['instances'] = parse_instances(safe(runner, [console, 'list2'], problems, 'ldconsole list2'))
    instance = pick_instance(instances, config.get('ld_index'))
    if instance is None:
        problems.append('Tidak ada instance LDPlayer yang menyala; nyalakan LDPlayer lalu deteksi lagi')
    else:
        found['ld_index'] = instance['index']
        settings = instance_config(console, instance['index'])
        try:
            if settings.is_file() and adb_debug(settings) == 0:
                problems.append(ADB_DEBUG_HINT)
        except (OSError, ValueError) as exc:
            problems.append(f'{settings.name} tidak terbaca: {exc}')
    devices = found['devices'] = parse_devices(safe(runner, [adb, 'devices'], problems, 'adb devices'))
    candidates = ([instance['serial']] if instance else []) + [config.get('serial')]
    serial = next((value for value in candidates if value in devices), None)
    if serial is None:
        if instance:
            problems.append(f'{instance["serial"]} belum tersambung ke adb; tunggu LDPlayer selesai menyala lalu '
                            'deteksi lagi')
        return found
    found['serial'] = serial
    expected = list(config.get('resolution') or DEFAULT_RESOLUTION)
    events = safe(runner, [adb, '-s', serial, 'shell', 'getevent', '-pl'], problems, 'adb getevent')
    touch = parse_touch(events)
    if touch:
        found['touch_device'] = touch
        span = touch_range(events, touch)
        if span and span != (expected[0] - 1, expected[1] - 1):
            problems.append(f'Layar sentuh {touch} {span[0]}x{span[1]} tidak cocok dengan layar {expected[0]}x{expected[1]}; '
                            'samakan resolusi LDPlayer dengan kalibrasi')
    else:
        problems.append('Layar sentuh tidak terbaca; isi touch_device manual, mis. /dev/input/event4')
    size = parse_size(safe(runner, [adb, '-s', serial, 'shell', 'wm', 'size'], problems, 'adb wm size'))
    if size is None:
        problems.append('Resolusi layar LDPlayer tidak terbaca')
    else:
        found['resolution'] = size
        if sorted(size) != sorted(expected):
            problems.append(f'Resolusi LDPlayer {size[0]}x{size[1]}; bot dikalibrasi untuk {expected[0]}x{expected[1]} '
                            '(ubah di Pengaturan LDPlayer > Tampilan)')
    return found
