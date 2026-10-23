"""Finds LDPlayer for the dashboard's setup panel: its folder (adb.exe, ldconsole.exe), the running instance, the
adb serial, the touchscreen node and the screen size. Read-only: it only lists and asks, it never changes LDPlayer."""
from __future__ import annotations

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


def parse_size(text: str) -> list | None:
    """Screen size from `wm size`; an override is what the screen really shows."""
    sizes = {kind: [int(width), int(height)] for kind, width, height in SIZE.findall(text)}
    return sizes.get('Override') or sizes.get('Physical')


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
    devices = found['devices'] = parse_devices(safe(runner, [adb, 'devices'], problems, 'adb devices'))
    candidates = ([instance['serial']] if instance else []) + [config.get('serial')]
    serial = next((value for value in candidates if value in devices), None)
    if serial is None:
        if instance:
            problems.append(f'{instance["serial"]} belum tersambung ke adb; tunggu LDPlayer selesai menyala lalu '
                            'deteksi lagi')
        return found
    found['serial'] = serial
    touch = parse_touch(safe(runner, [adb, '-s', serial, 'shell', 'getevent', '-pl'], problems, 'adb getevent'))
    if touch:
        found['touch_device'] = touch
    else:
        problems.append('Layar sentuh tidak terbaca; isi touch_device manual, mis. /dev/input/event4')
    size = parse_size(safe(runner, [adb, '-s', serial, 'shell', 'wm', 'size'], problems, 'adb wm size'))
    expected = list(config.get('resolution') or DEFAULT_RESOLUTION)
    if size is None:
        problems.append('Resolusi layar LDPlayer tidak terbaca')
    else:
        found['resolution'] = size
        if sorted(size) != sorted(expected):
            problems.append(f'Resolusi LDPlayer {size[0]}x{size[1]}; bot dikalibrasi untuk {expected[0]}x{expected[1]} '
                            '(ubah di Pengaturan LDPlayer > Tampilan)')
    return found
