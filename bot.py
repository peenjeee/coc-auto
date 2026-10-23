"""Calibrated CoC screen automation. Dry-run is the default."""

import argparse
import base64
import csv
import gc
import io
import itertools
import json
import logging
import math
from pathlib import Path
import re
import shutil
import struct
import subprocess
import time
from typing import Callable

import cv2
import numpy as np

RESOURCES = ('gold', 'elixir', 'dark')
VILLAGE_GOALS = {'builder': ('gold', 'elixir', 'cart'), 'home': ('gold', 'elixir', 'dark')}
DEFAULT_GOALS = {'builder': ('gold', 'elixir', 'cart'), 'home': ('gold', 'elixir')}
SCREEN_NAMES = (
    'home', 'home_menu', 'home_army', 'home_scout', 'home_battle', 'home_result',
    'builder', 'builder_menu', 'builder_scout', 'builder_battle',
    'builder_stage2', 'builder_battle2', 'builder_result',
)
ACTIONS = {
    'home': ('attack', 'switch'), 'builder': ('attack', 'switch', 'cart'),
    'home_menu': ('find', 'close'), 'builder_menu': ('find', 'close'), 'home_army': ('attack', 'close'),
    'home_scout': ('next',), 'home_result': ('return',),
    'builder_result': ('return',), 'star_bonus': ('ok',), 'builder_star_bonus': ('ok',),
    'builder_cart': ('claim', 'close'), 'reward_chest': ('open',), 'reward_card': ('continue',),
    'layout_blocked': ('ok',), 'league': ('close',), 'welcome': ('ok',),
    'build_list': ('close',), 'wall_selected': ('close', 'build'), 'wall_confirm': ('confirm', 'close'),
    'builder_wall_confirm': ('confirm', 'close'), 'search_failed': ('close',), 'gem_offer': ('close',),
    'idle': ('reload',), 'builders_busy': ('close',), 'update_offer': ('close',), 'disconnected': ('retry',),
}
DEPLOY_SCREENS = ('home_scout', 'builder_scout', 'builder_stage2')
OCR_MIN_VALUE, OCR_MAX_SATURATION, OCR_MIN_HEIGHT_RATIO = 200, 70, 0.6
OCR_ALIGN_RATIO = 0.2
OCR_MIN_CONFIDENCE = 50
OCR_SCALES = (3, 4, 5)
DIGIT_HEIGHT, DIGIT_WIDTH = 24, 20
DIGIT_MIN_SCORE, DIGIT_MIN_MARGIN = 0.75, 0.15
FRACTION_MIN_SCORE = 0.65
# Builder-list prices are small, so they are doubled before reading, and red when the resource is short.
PRICE_SCALE, PRICE_MAX_SATURATION = 2, 160
TARGET_MAX_SCORE = 0.2
ANCHOR_MAX_MARGIN = 40
TARGET_MAX_OFFSET = 200
TARGET_MIN_SCALE, TARGET_MAX_SCALE = 0.5, 2.0
PAN_DURATION_MS = 300
PAN_SETTLE_SECONDS = 2
PAN_MAX_ATTEMPTS = 4
VILLAGES = ('home', 'builder')
BATTLE_STATES = ('home_battle', 'builder_battle', 'builder_battle2')
TOUCH_DEVICE = re.compile(r'/dev/input/event\d{1,2}')
PINCH_STEPS = 10
PINCH_STEP_SECONDS = 0.02
ZOOM_SETTLE_SECONDS = 1.5
STILL_DIFF = 1.5
CAPTURE_RETRIES = 2
ZOOM_MAX_PINCHES = 6
ZOOM_CLEAR_ATTEMPTS = 2
ZOOM_WAIT_SECONDS = 20
CARD_ABOVE, CARD_BELOW, CARD_HALF_WIDTH = 38, 50, 40
CARD_EMPTY_SATURATION = 20
DEPLOY_BURST = 4
TAP_HOLD_SECONDS, TAP_GAP_SECONDS = 0.03, 0.03
MAX_CARD_WAIT_SECONDS = 20
MAX_HOLD_SECONDS = 10
EVENT_DIR = '/data/local/tmp/cocbot'
UNKNOWN_RETRIES = 3
FULL_RATIO, STORAGE_RUN_RATIO, STORAGE_MIN_VALUE = 0.95, 0.8, 150
RGBA_8888, RAW_HEADERS, RAW_HEADER_MIN = 1, (12, 16), 16
POPUP_ACTIONS = {'star_bonus': 'ok', 'builder_star_bonus': 'ok', 'reward_chest': 'open', 'reward_card': 'continue',
                 'layout_blocked': 'ok', 'league': 'close', 'welcome': 'ok', 'idle': 'reload',
                 # "Pembaruan Tersedia!": a tap outside the box closes it; the update button is never tapped.
                 'update_offer': 'close', 'disconnected': 'retry'}
POPUPS = tuple(POPUP_ACTIONS)
# A retry with the network still down brings the dialog straight back, so give it time between tries.
POPUP_PAUSE_SECONDS = {'update_offer': 2, 'disconnected': 10}
UPDATE_CLOSE_ATTEMPTS = 3
POPUP_CLEAR_ATTEMPTS = 3
GAME_ACTIVITY = 'com.supercell.clashofclans/com.supercell.titan.GameApp'
RECOVER_ADB_SECONDS, RECOVER_LOAD_SECONDS, RECOVER_POLL_SECONDS = 300, 240, 5
MAX_RECOVERIES = 20
MEMORY_RETRY_SECONDS, BATTLE_END_SECONDS = 30, 240
LOW_MEMORY_MB, EMULATOR_STOP_SECONDS, EMULATOR_REFRESH_SECONDS = 1000, 60, 1800
TAP_RETRIES, TAP_RETRY_SECONDS = 3, 10
FIND_COOLDOWN_SECONDS, FIND_RETRIES = 180, 18
EMULATOR_SETTLE_SECONDS = 20
RESULT_SCREENS = ('home_result', 'builder_result')
SWEEP_TAPS = 12
BAR_CARD_STD, BAR_LABEL_WHITE, BAR_WARDEN_GREEN = 18, 100, 200
BAR_CARD_WIDTH, BAR_CARD_COLOUR = (65, 98), 0.43
BAR_CAMP_HEADER, BAR_SETTLE_SECONDS = 0.5, 3
SPELL_TAPS, SPELL_ROUND = 30, 3
LAUNCH_ATTEMPTS = 4
MENU_SCREENS = ('build_list', 'wall_selected', 'wall_confirm', 'builder_wall_confirm')
OPTIONAL_SCREENS = ('builder_cart', 'search_failed', 'gem_offer', 'other_device', 'builders_busy', *MENU_SCREENS)
KNOWN_SCREENS = SCREEN_NAMES + ('loading',) + POPUPS + OPTIONAL_SCREENS
SEARCH_RETRY_SECONDS, LOOT_READ_SECONDS = 60, 3
HUD_ALIGN_PX, HUD_GAP_RATIO, HUD_MIN_MEAN_VALUE = 2, 1.2, 235
# A digit is about as wide as tall; a blob much wider is touching digits, cut where the bridge is thinnest.
DIGIT_ASPECT, WIDE_RATIO, BRIDGE_RATIO = 0.95, 1.4, 0.15
ANCHOR_MAX_SCORE, SEARCH_ANCHOR_MAX_SCORE = 0.02, 0.05
WALL_RESOURCES = ('gold', 'elixir')
WALL_TAP_SECONDS, WALL_LIST_SETTLE_SECONDS, WALL_SCROLL_LIMIT, GEM_READ_SECONDS = 15, 2, 30, 5
SETTLE_MENU_LAYERS = 4
WALL_SELECT_SECONDS, WALL_LAYOUT_HOLD_SECONDS, WALL_PRICE_SECONDS = 4, 0.4, 2
WHITE_LEVEL = 200
CART_CLAIM_SECONDS, CART_OPEN_SECONDS = 2, 5
CART_MISS_LIMIT = 5
TOOLTIP_SHOW_SECONDS, TOOLTIP_CLEAR_SECONDS = 3, 4
STAGE_SETTLE_SECONDS = 4
LOG = logging.getLogger(__name__)


class BotError(RuntimeError):
    """Stop without attempting recovery input."""


class DeviceError(BotError):
    """ADB lost the emulator; the game may need to reconnect."""


class UnknownScreen(BotError):
    """Frame matches no calibrated screen, e.g. a camera transition."""


class WaitTimeout(BotError):
    """The expected screen did not appear in time."""


class UnexpectedScreen(WaitTimeout):
    """A known screen came up out of turn (a battle started before its scout screen was seen, a menu left
    open); recovery settles it like any other stall."""


class GemUnreadable(WaitTimeout):
    """The gem count could not be trusted (missing digits, a label or tooltip over the bar); nothing is tapped
    and recovery reads it again."""


class TargetMissing(BotError):
    """Target icon not visible or unsafe to tap; the camera may need another pan."""


class OutOfGold(BotError):
    """Skipping to the next opponent costs gold and none is left; the game offered gems instead."""


class NoFreeBuilder(BotError):
    """Builder Base walls need a free builder; the game offered to free one for gems, which is only ever closed."""


class OtherDevice(Exception):
    """The account was opened on another device; the run stops and never reloads, so that session stays."""


class GemSpent(Exception):
    """The gem count dropped during wall upgrades; never recovered, the run stops at once."""


class UpdateRequired(Exception):
    """The update offer stayed after taps outside it: the game must be updated from the Play Store first."""


class LimitReached(Exception):
    """A --hours/--minutes/--max-upgrades limit was reached between attacks or upgrades; a normal finish."""


def integer(value: object, low: int, high: int, label: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise BotError(f'{label}: perlu integer {low}..{high}')
    return value


def point(value: object, resolution: list, label: str) -> list:
    if not isinstance(value, list) or len(value) != 2:
        raise BotError(f'{label}: perlu [x, y]')
    return [integer(v, 0, limit - 1, label) for v, limit in zip(value, resolution)]


def rectangle(value: object, resolution: list) -> list:
    if not isinstance(value, list) or len(value) != 4:
        raise BotError('ROI perlu [x, y, width, height]')
    x, y = point(value[:2], resolution, 'ROI')
    w = integer(value[2], 1, resolution[0] - x, 'ROI width')
    h = integer(value[3], 1, resolution[1] - y, 'ROI height')
    return [x, y, w, h]


def asset_path(root: Path, name: object) -> Path:
    if not isinstance(name, str) or not name:
        raise BotError('Nama template kosong')
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or path.suffix.lower() != '.png':
        raise BotError('Template harus PNG di dalam folder config')
    return path


def crop(image: np.ndarray, roi: list) -> np.ndarray:
    x, y, w, h = roi
    return image[y:y+h, x:x+w]


def parse_resource(text: str) -> int:
    text = text.strip()
    if re.fullmatch(r'[0-9]+', text):
        return integer(int(text), 0, 999_999_999, 'Resource')
    match = re.fullmatch(r'([0-9]{1,3})([ ,.])([0-9]{3})(?:\2[0-9]{3})*', text)
    if not match:
        raise BotError('Angka OCR kosong/ambigu')
    return integer(int(text.replace(match[2], '')), 0, 999_999_999, 'Resource')


def meets_minimum(loot: dict, minimum: dict) -> bool:
    for key in RESOURCES:
        threshold = integer(minimum.get(key, 0), 0, 999_999_999, key)
        if threshold and key not in loot:
            raise BotError(f'OCR {key} tidak tersedia')
        if key in loot:
            integer(loot[key], 0, 999_999_999, key)
    return all(loot[key] >= value for key, value in minimum.items() if value)


def validate_config(config: dict, root: Path, mode: str) -> None:
    if mode not in ('home', 'builder', 'both'):
        raise BotError('Mode tidak dikenal')
    resolution = config.get('resolution')
    if not isinstance(resolution, list) or len(resolution) != 2:
        raise BotError('resolution perlu [width, height]')
    for value in resolution:
        integer(value, 1, 8192, 'resolution')
    serial = config.get('serial')
    if not isinstance(serial, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]+', serial) or serial.startswith('-'):
        raise BotError('serial ADB wajib eksplisit dan valid')
    screens = config.get('screens', {})
    if not isinstance(screens, dict) or not screens:
        raise BotError('Belum dikalibrasi: screens kosong')
    needed = [s for s in SCREEN_NAMES if mode == 'both' or s.startswith(mode)]
    for name in needed:
        if name not in screens:
            raise BotError(f'Kalibrasi layar belum tersedia: {name}')
    for name, screen in screens.items():
        if name not in KNOWN_SCREENS or not isinstance(screen, dict):
            raise BotError('Nama/format layar tidak valid')
        anchors = screen.get('anchors', [])
        if len(anchors) < 2:
            raise BotError(f'{name}: perlu minimal dua anchor berbeda')
        boxes = [rectangle(a['roi'], resolution) for a in anchors if 'search' not in a]
        for i, (x, y, w, h) in enumerate(boxes):
            for xx, yy, ww, hh in boxes[:i]:
                if x < xx + ww and xx < x + w and y < yy + hh and yy < y + h:
                    raise BotError(f'{name}: anchor harus berbeda dan tidak overlap')
        for anchor in anchors:
            if 'search' in anchor:
                validate_search_anchor(name, anchor, root, resolution)
                continue
            roi = rectangle(anchor['roi'], resolution)
            template = cv2.imread(str(asset_path(root, anchor['file'])))
            if template is None or list(template.shape[:2]) != [roi[3], roi[2]]:
                raise BotError(f'{name}: template hilang/ukuran salah')
            if float(template.std()) < 5:
                raise BotError(f'{name}: anchor terlalu polos')
            integer(anchor.get('margin', 0), 0, ANCHOR_MAX_MARGIN, 'anchor margin')
        for group in screen.get('alt_anchors', []):
            if not isinstance(group, list) or len(group) < 2:
                raise BotError(f'{name}: alt_anchors perlu minimal dua anchor')
            for anchor in group:
                roi = rectangle(anchor['roi'], resolution)
                template = cv2.imread(str(asset_path(root, anchor['file'])))
                if template is None or list(template.shape[:2]) != [roi[3], roi[2]] or float(template.std()) < 5:
                    raise BotError(f'{name}: template alt_anchors hilang/ukuran salah')
        overrides = screen.get('overrides', [])
        if not isinstance(overrides, list) or set(overrides) - set(SCREEN_NAMES + OPTIONAL_SCREENS) \
                or name in overrides:
            raise BotError(f'{name}: overrides tidak valid')
        allowed = ACTIONS.get(name, ())
        targets = screen.get('targets', {})
        if set(screen.get('actions', {})) - set(allowed) or set(targets) - set(allowed):
            raise BotError(f'{name}: aksi tidak diizinkan')
        for target in targets.values():
            alternatives = target.get('alt_files', [])
            if not isinstance(alternatives, list) or len(alternatives) > 4:
                raise BotError(f'{name}: alt_files target tidak valid')
            for alt in alternatives:
                if isinstance(alt, dict):
                    for value in alt.get('offset', [0, 0]):
                        integer(value, -TARGET_MAX_OFFSET, TARGET_MAX_OFFSET, 'offset')
            files = [target['file'], *[alt['file'] if isinstance(alt, dict) else alt for alt in alternatives]]
            for file in files:
                template = cv2.imread(str(asset_path(root, file)))
                if template is None or float(template.std()) < 5:
                    raise BotError(f'{name}: template target hilang/terlalu polos')
            if not isinstance(target['max_score'], float) or not 0 < target['max_score'] <= TARGET_MAX_SCORE:
                raise BotError(f'{name}: max_score target tidak valid')
            if 'pan' in target:
                pan = target['pan']
                if not isinstance(pan, list) or len(pan) != 2:
                    raise BotError(f'{name}: pan perlu titik awal dan akhir')
                for location in pan:
                    point(location, resolution, 'pan')
                integer(target.get('pan_attempts', 1), 1, PAN_MAX_ATTEMPTS, 'pan_attempts')
            offset = target.get('offset', [0, 0])
            if not isinstance(offset, list) or len(offset) != 2:
                raise BotError(f'{name}: offset target tidak valid')
            for value in offset:
                integer(value, -TARGET_MAX_OFFSET, TARGET_MAX_OFFSET, 'offset')
            scales = target.get('scales', [1.0])
            if not isinstance(scales, list) or not scales or len(scales) > 12 or not all(
                    isinstance(s, (int, float)) and TARGET_MIN_SCALE <= s <= TARGET_MAX_SCALE for s in scales):
                raise BotError(f'{name}: scales target tidak valid')
            for area in target.get('avoid', []):
                rectangle(area, resolution)
        optional = ('cart', 'close')
        required = [a for a in allowed if (a != 'switch' or mode == 'both') and a not in optional and a not in targets]
        present = [a for a in optional if a in screen.get('actions', {})]
        for action in required + present:
            point(screen.get('actions', {}).get(action), resolution, action)
        if name in DEPLOY_SCREENS:
            point(screen.get('deploy'), resolution, 'deploy')
            auto = screen.get('auto')
            if auto is not None:
                validate_auto(name, auto, resolution)
            troops = screen.get('troops', [])
            sweep = screen.get('sweep')
            if sweep:
                point(sweep.get('deploy'), resolution, 'sweep deploy')
                for location in sweep.get('slots', []):
                    point(location, resolution, 'sweep slot')
            if (not troops and not sweep) or len(troops) > 20:
                raise BotError(f'{name}: slot pasukan belum dikalibrasi')
            for troop in troops:
                point(troop['slot'], resolution, 'troop slot')
                if 'deploy' in troop:
                    point(troop['deploy'], resolution, 'troop deploy')
                points = troop.get('points', [])
                if not isinstance(points, list) or len(points) > 20:
                    raise BotError(f'{name}: points pasukan tidak valid')
                for location in points:
                    point(location, resolution, 'troop point')
                hold = troop.get('hold', 0)
                if not isinstance(hold, (int, float)) or not 0 <= hold <= MAX_HOLD_SECONDS:
                    raise BotError(f'{name}: hold pasukan tidak valid')
                wait = troop.get('wait', 0)
                if not isinstance(wait, (int, float)) or not 0 <= wait <= MAX_CARD_WAIT_SECONDS:
                    raise BotError(f'{name}: wait pasukan tidak valid')
                integer(troop['count'], 1, 300, 'troop count')
    minimum = config.get('minimum', {})
    if not isinstance(minimum, dict) or set(minimum) - set(RESOURCES):
        raise BotError('Nama filter resource tidak valid')
    meets_minimum({key: 999_999_999 for key in RESOURCES}, minimum)
    if mode != 'builder':
        for key in RESOURCES:
            if minimum.get(key, 0):
                rectangle(screens['home_scout'].get('loot', {}).get(key), resolution)
    if config.get('digits'):
        load_digit_templates(root, config['digits'])
    if (config.get('capacity') or config.get('cart_text')) and not config.get('digits'):
        raise BotError('capacity/cart_text perlu template angka (digits)')
    for village, specs in config.get('capacity', {}).items():
        if village not in VILLAGES or not isinstance(specs, dict):
            raise BotError('capacity hanya untuk home/builder')
        for spec in specs.values():
            rectangle(spec.get('bar'), resolution)
            rectangle(spec.get('max'), resolution)
            point(spec.get('tap'), resolution, 'capacity tap')
            if 'maximum' in spec:
                integer(spec['maximum'], 1, 999_999_999, 'capacity maximum')
            label = spec.get('label')
            if label:
                rectangle(label.get('region'), resolution)
                template = cv2.imread(str(asset_path(root, label.get('file'))))
                if template is None or not isinstance(label.get('max_score'), float):
                    raise BotError('capacity label tidak valid')
    if config.get('cart_text'):
        rectangle(config['cart_text'], resolution)
    button = config.get('speed_button')
    if button:
        rectangle(button.get('region'), resolution)
        template = cv2.imread(str(asset_path(root, button.get('file'))))
        score = button.get('max_score')
        if template is None or not isinstance(score, float) or not 0 < score <= TARGET_MAX_SCORE:
            raise BotError('speed_button tidak valid')
    if config.get('ldconsole') is not None and not isinstance(config['ldconsole'], str):
        raise BotError('ldconsole harus path ke ldconsole.exe')
    integer(config.get('ld_index', 0), 0, 99, 'ld_index')
    touch = config.get('touch_device')
    if touch is not None and (not isinstance(touch, str) or not TOUCH_DEVICE.fullmatch(touch)):
        raise BotError('touch_device harus /dev/input/eventN')
    zoom = config.get('zoom_out')
    if zoom:
        if not isinstance(zoom.get('device'), str) or not TOUCH_DEVICE.fullmatch(zoom['device']):
            raise BotError('zoom_out.device harus /dev/input/eventN')
        integer(zoom.get('pinches'), 1, ZOOM_MAX_PINCHES, 'zoom pinches')
        point(zoom.get('center'), resolution, 'zoom center')
        gap = zoom.get('gap')
        if not isinstance(gap, list) or len(gap) != 2:
            raise BotError('zoom_out.gap perlu [awal, akhir]')
        start_gap = integer(gap[0], 20, resolution[0], 'zoom gap')
        if integer(gap[1], 10, start_gap - 1, 'zoom gap') >= start_gap:
            raise BotError('zoom_out.gap harus menyempit')
        selection = zoom.get('selection')
        if selection:
            template = cv2.imread(str(asset_path(root, selection.get('file'))))
            score = selection.get('max_score')
            if template is None or not isinstance(score, float) or not 0 < score <= TARGET_MAX_SCORE:
                raise BotError('zoom_out.selection tidak valid')


def validate_auto(name: str, auto: dict, resolution: list) -> None:
    """Home Village: troop 'hold' seconds and 'spells' points. Builder Base: spread 'points', one per troop card."""
    rectangle(auto.get('bar'), resolution)
    if 'points' in auto:
        points = auto['points']
        if not isinstance(points, list) or not points or len(points) > 20:
            raise BotError(f'{name}: auto points perlu titik')
        for location in points:
            point(location, resolution, 'auto point')
        return
    spells = auto.get('spells')
    if not isinstance(spells, list) or not spells:
        raise BotError(f'{name}: auto spells perlu titik')
    for location in spells:
        point(location, resolution, 'auto spell')
    hold = auto.get('hold')
    if not isinstance(hold, (int, float)) or not 0 < hold <= MAX_HOLD_SECONDS:
        raise BotError(f'{name}: auto hold tidak valid')


def load_digit_templates(root: Path, folder: str) -> dict:
    """One template per digit ('7.png') plus optional font variants ('7-ui2.png')."""
    directory = asset_path(root, f'{folder}/0.png').parent
    templates = {}
    for digit in '0123456789':
        paths = [directory / f'{digit}.png'] + sorted(directory.glob(f'{digit}-*.png'))
        for path in paths:
            image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if image is None or image.shape != (DIGIT_HEIGHT, DIGIT_WIDTH):
                raise BotError(f'Template angka {path.name} hilang/ukuran salah')
            templates[path.stem] = image.astype(np.float32) / 255
    return templates


class ADB:
    def __init__(self, executable: str, serial: str, resolution: list, live: bool = False,
                 touch: str | None = None) -> None:
        self.executable, self.serial = executable, serial
        self.resolution, self.live, self.touch = resolution, live, touch
        self.event_files: set = set()

    def inject(self, steps: list, device: str | None = None) -> None:
        """Replay touch steps by cat-ing prepared event files: one whole write() per frame."""
        if not self.live:
            raise BotError('Input perangkat dinonaktifkan dalam dry-run')
        device = device or self.touch
        if not device or not TOUCH_DEVICE.fullmatch(device):
            raise BotError('Device sentuh tidak valid')
        files, lines = {}, []
        for step in steps:
            if step[0] == 'sleep':
                lines.append(f'sleep {step[1]}')
                continue
            if step[0] == 'frame':
                name, files[step[1]] = step[1], step[2]
            elif step[0] == 'down':
                x, y = point(list(step[1:]), self.resolution, 'touch')
                name = f'd_{x}_{y}'
                files[name] = [(3, 47, 0), (3, 57, 1), (3, 53, x), (3, 54, y), (3, 58, 1), (1, 330, 1), (0, 0, 0)]
            else:
                name = 'up'
                files[name] = [(3, 47, 0), (3, 57, -1), (1, 330, 0), (0, 0, 0)]
            lines.append(f'cat {EVENT_DIR}/{name} > {device}')
        missing = {name: events for name, events in files.items() if name not in self.event_files}
        if missing:
            writes = [f'echo {base64.b64encode(event_bytes(events)).decode()} | base64 -d > {EVENT_DIR}/{name}'
                      for name, events in missing.items()]
            self.command('shell', '; '.join([f'mkdir -p {EVENT_DIR}'] + writes))
            self.event_files |= set(missing)
        self.command('shell', '; '.join(lines))

    def command(self, *args: str) -> bytes:
        try:
            result = subprocess.run(
                [self.executable, '-s', self.serial, *args],
                capture_output=True, check=True, timeout=15, shell=False,
            )
            return result.stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise DeviceError('ADB gagal/disconnect; periksa instance dan serial') from exc
        except (MemoryError, IndexError) as exc:
            # A pipe reader thread that runs out of memory leaves subprocess with no output to return.
            raise MemoryError('Output ADB hilang; memori PC penuh') from exc

    def connected(self) -> bool:
        try:
            listing = subprocess.run([self.executable, 'devices'], capture_output=True, text=True,
                                     timeout=15, shell=False).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return f'{self.serial}	device' in listing

    def booted(self) -> bool:
        try:
            return self.command('shell', 'getprop sys.boot_completed').strip() == b'1'
        except DeviceError:
            return False

    def game_in_front(self) -> bool:
        focus = self.command('shell', 'dumpsys window | grep mCurrentFocus')
        return GAME_ACTIVITY.split('/')[0].encode() in focus

    def launch(self, activity: str) -> None:
        """Start the game app; used only to recover from a crash or a closed game."""
        if not self.live:
            raise BotError('Input perangkat dinonaktifkan dalam dry-run')
        self.command('shell', 'am', 'start', '-n', activity)

    def capture(self) -> np.ndarray:
        """Raw RGBA framebuffer; skips on-device PNG encoding, which costs ~0.5 s."""
        for attempt in range(CAPTURE_RETRIES + 1):
            try:
                return self.decode(self.command('exec-out', 'screencap'))
            except BotError:
                if attempt == CAPTURE_RETRIES:
                    raise

    def decode(self, raw: bytes) -> np.ndarray:
        if len(raw) < RAW_HEADER_MIN:
            raise BotError('Screenshot gagal/resolusi berubah')
        width, height, pixel_format = struct.unpack('<III', raw[:12])
        size = width * height * 4
        header = len(raw) - size
        if [width, height] != self.resolution or pixel_format != RGBA_8888 or header not in RAW_HEADERS:
            raise BotError('Screenshot gagal/resolusi berubah')
        pixels = np.frombuffer(raw, np.uint8, count=size, offset=header).reshape(height, width, 4)
        return cv2.cvtColor(pixels, cv2.COLOR_RGBA2BGR)

    def tap(self, location: list) -> None:
        if not self.live:
            raise BotError('Input perangkat dinonaktifkan dalam dry-run')
        x, y = point(location, self.resolution, 'tap')
        self.command('shell', 'input', 'tap', str(x), str(y))

    def tap_many(self, locations: list) -> None:
        """All taps of one card in a single shell call; raw touch events when a device is set."""
        if not self.live:
            raise BotError('Input perangkat dinonaktifkan dalam dry-run')
        taps = [point(location, self.resolution, 'tap') for location in locations]
        if not self.touch:
            self.command('shell', '; '.join(f'input tap {x} {y}' for x, y in taps))
            return
        self.inject([step for x, y in taps for step in
                     (('down', x, y), ('sleep', TAP_HOLD_SECONDS), ('up',), ('sleep', TAP_GAP_SECONDS))])

    def touch_down(self, location: list) -> None:
        """Finger stays down until touch_up, so troops keep streaming out."""
        x, y = point(location, self.resolution, 'hold')
        self.inject([('down', x, y)])

    def touch_up(self) -> None:
        self.inject([('up',)])

    def hold(self, slot: list | None, location: list, seconds: float) -> None:
        """Optionally select a card, then keep one finger down so troops stream out."""
        if not self.live:
            raise BotError('Input perangkat dinonaktifkan dalam dry-run')
        x, y = point(location, self.resolution, 'hold')
        select = [] if slot is None else [point(slot, self.resolution, 'slot')]
        if not self.touch:
            taps = [f'input tap {sx} {sy}' for sx, sy in select]
            self.command('shell', '; '.join(taps + [f'input swipe {x} {y} {x} {y} {round(seconds * 1000)}']))
            return
        steps = [step for sx, sy in select for step in
                 (('down', sx, sy), ('sleep', TAP_HOLD_SECONDS), ('up',), ('sleep', TAP_GAP_SECONDS))]
        self.inject(steps + [('down', x, y), ('sleep', seconds), ('up',)])

    def swipe(self, start: list, end: list) -> None:
        if not self.live:
            raise BotError('Input perangkat dinonaktifkan dalam dry-run')
        x1, y1 = point(start, self.resolution, 'swipe')
        x2, y2 = point(end, self.resolution, 'swipe')
        self.command('shell', 'input', 'swipe', str(x1), str(y1), str(x2), str(y2), str(PAN_DURATION_MS))

    def pinch(self, device: str, center: list, start_gap: int, end_gap: int) -> None:
        """Two-finger pinch via multitouch protocol B in one shell call."""
        if not self.live:
            raise BotError('Input perangkat dinonaktifkan dalam dry-run')
        if not isinstance(device, str) or not TOUCH_DEVICE.fullmatch(device):
            raise BotError('Device sentuh tidak valid')
        cx, cy = point(center, self.resolution, 'pinch center')
        width = self.resolution[0] - 1

        def fingers(gap: float) -> tuple[int, int]:
            return max(0, round(cx - gap / 2)), min(width, round(cx + gap / 2))

        left, right = fingers(start_gap)
        steps = [('frame', f'p_{left}_{right}_{cy}', [
            (3, 47, 0), (3, 57, 1), (3, 53, left), (3, 54, cy), (3, 58, 1),
            (3, 47, 1), (3, 57, 2), (3, 53, right), (3, 54, cy), (3, 58, 1), (1, 330, 1), (0, 0, 0)])]
        for step in range(1, PINCH_STEPS + 1):
            left, right = fingers(start_gap + (end_gap - start_gap) * step / PINCH_STEPS)
            steps += [('frame', f'm_{left}_{right}', [(3, 47, 0), (3, 53, left), (3, 47, 1), (3, 53, right),
                                                     (0, 0, 0)]), ('sleep', PINCH_STEP_SECONDS)]
        steps.append(('frame', 'pinch_up', [(3, 47, 0), (3, 57, -1), (3, 47, 1), (3, 57, -1),
                                            (1, 330, 0), (0, 0, 0)]))
        self.inject(steps, device)


class Emulator:
    """LDPlayer instance control through ldconsole; used only to start a stopped emulator."""

    def __init__(self, console: str, index: int) -> None:
        self.console, self.index = console, index

    def run(self, *args: str) -> str:
        try:
            return subprocess.run([self.console, *args, '--index', str(self.index)], capture_output=True,
                                  text=True, timeout=60, shell=False).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise DeviceError('ldconsole gagal; periksa path LDPlayer') from exc

    def running(self) -> bool:
        return self.run('isrunning').strip() == 'running'

    def launch(self) -> None:
        self.run('launch')

    def quit(self) -> None:
        self.run('quit')


def available_memory_mb() -> int | None:
    """Free commit memory (RAM + page file) on Windows; None where it cannot be read."""
    import ctypes
    if not hasattr(ctypes, 'windll'):
        return None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong) for name in ('total_phys', 'avail_phys', 'total_commit', 'avail_commit',
                                                    'total_virtual', 'avail_virtual', 'avail_extended')]
    status = MemoryStatus()
    status.length = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return status.avail_commit // (1024 * 1024)


def validate_search_anchor(name: str, anchor: dict, root: Path, resolution: list) -> None:
    x, y, w, h = rectangle(anchor['search'], resolution)
    template = cv2.imread(str(asset_path(root, anchor['file'])))
    scales = anchor.get('scales', [1.0])
    if template is None or float(template.std()) < 5 or template.shape[0] > h or template.shape[1] > w:
        raise BotError(f'{name}: template anchor search hilang/terlalu polos/terlalu besar')
    if not isinstance(scales, list) or not scales or not all(
            isinstance(s, (int, float)) and TARGET_MIN_SCALE <= s <= TARGET_MAX_SCALE for s in scales):
        raise BotError(f'{name}: scales anchor tidak valid')
    score = anchor.get('max_score', ANCHOR_MAX_SCORE)
    if not isinstance(score, float) or not 0 < score <= SEARCH_ANCHOR_MAX_SCORE:
        raise BotError(f'{name}: max_score anchor tidak valid')


def load_anchor(root: Path, anchor: dict) -> tuple:
    """(region, template, margin, scales, max_score). A 'search' anchor may sit anywhere in its region, e.g. wall
    buttons whose row moves with the picked wall."""
    template = cv2.imread(str(asset_path(root, anchor['file'])))
    if 'search' in anchor:
        return (anchor['search'], template, 0, tuple(anchor.get('scales', [1.0])),
                anchor.get('max_score', ANCHOR_MAX_SCORE))
    return anchor['roi'], template, anchor.get('margin', 0), (1.0,), ANCHOR_MAX_SCORE


class Detector:
    def __init__(self, config: dict, root: Path) -> None:
        self.resolution = config['resolution']
        self.templates = {name: [load_anchor(root, a) for a in screen['anchors']]
                          for name, screen in config['screens'].items()}
        self.overrides = {name: set(screen.get('overrides', [])) for name, screen in config['screens'].items()}
        self.alternatives = {
            name: [[load_anchor(root, a) for a in group] for group in screen.get('alt_anchors', [])]
            for name, screen in config['screens'].items()
        }

    def matches(self, image: np.ndarray, anchors: list) -> bool:
        for roi, template, margin, scales, max_score in anchors:
            score = self.score(image, roi, template, margin, scales)
            if not (math.isfinite(score) and score <= max_score):
                return False
        return True

    def score(self, image: np.ndarray, roi: list, template: np.ndarray, margin: int, scales: tuple = (1.0,)) -> float:
        x, y, w, h = roi
        width, height = self.resolution
        left, top = max(0, x - margin), max(0, y - margin)
        region = image[top:min(height, y + h + margin), left:min(width, x + w + margin)]
        best = math.inf
        for scale in scales:
            scaled = template if scale == 1.0 else cv2.resize(template, None, fx=scale, fy=scale)
            if scaled.shape[0] <= region.shape[0] and scaled.shape[1] <= region.shape[1]:
                best = min(best, float(cv2.matchTemplate(region, scaled, cv2.TM_SQDIFF_NORMED).min()))
        return best

    def detect(self, image: np.ndarray) -> str:
        if list(image.shape[1::-1]) != self.resolution:
            raise BotError('Resolusi layar berbeda dari kalibrasi')
        matches = []
        for name, anchors in self.templates.items():
            if self.matches(image, anchors) or any(self.matches(image, group) for group in self.alternatives[name]):
                matches.append(name)
        if not matches:
            raise UnknownScreen('Layar tidak dikenal; tidak ada input dikirim')
        overridden = set().union(*(self.overrides.get(name, set()) for name in matches))
        matches = [name for name in matches if name not in overridden]
        if len(matches) != 1:
            raise BotError('Layar ambigu; tidak ada input dikirim')
        return matches[0]


def calibrated(config: dict, village: str) -> set:
    """Resources whose fill level the config can read in that village."""
    found = set(config.get('capacity', {}).get(village, {})) | set(config.get('storage', {}).get(village, {}))
    if village == 'builder' and (config.get('cart_text') or config.get('cart_bar')):
        found.add('cart')
    return found


def parse_goals(config: dict, builder: str | None, home: str | None, first: str) -> dict:
    """Village -> resources to fill, in visiting order; no choice means both villages without dark elixir."""
    if builder is None and home is None:
        goals = {village: set(DEFAULT_GOALS[village]) & calibrated(config, village) for village in VILLAGES}
    else:
        goals = {}
        for village, text in (('builder', builder), ('home', home)):
            if text is None:
                continue
            names = VILLAGE_GOALS[village] if text == 'all' else text.split(',')
            if not all(names) or set(names) - set(VILLAGE_GOALS[village]):
                raise BotError(f'Target {village} tidak valid: {text!r}; pilih dari '
                               f'{",".join(VILLAGE_GOALS[village])} atau all')
            missing = set(names) - calibrated(config, village)
            if missing:
                raise BotError(f'Belum dikalibrasi di {village}: {", ".join(sorted(missing))}')
            goals[village] = set(names)
    return {village: goals[village] for village in sorted(goals, key=lambda village: village != first)}


def price_resource(band: np.ndarray, minimum: int) -> str | None:
    """Gold prices end in a yellow coin, elixir prices in a purple drop."""
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    hue, saturation, value = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    gold = int(((hue >= 18) & (hue <= 35) & (saturation > 120) & (value > 150)).sum())
    elixir = int(((hue >= 135) & (hue <= 165) & (saturation > 100) & (value > 100)).sum())
    if max(gold, elixir) < minimum:
        return None
    return 'gold' if gold > elixir else 'elixir'


def white_pixels(image: np.ndarray, region: list) -> int:
    """Prices are white when affordable and red when short, so white text means the resource is there."""
    return int(np.all(crop(image, region) > WHITE_LEVEL, axis=2).sum())


def find_rows(image: np.ndarray, template: np.ndarray, region: list, max_score: float,
              scales: tuple = (1.0,)) -> list:
    """Centre y of every row in the region that shows the template text, top row first. The row of the
    building in focus is drawn a little smaller, so it is matched at its own scale."""
    x, y, w, h = region
    centres = []
    for scale in scales:
        scaled = template if scale == 1.0 else cv2.resize(template, None, fx=scale, fy=scale,
                                                          interpolation=cv2.INTER_AREA)
        scores = cv2.matchTemplate(image[y:y + h, x:x + w], scaled, cv2.TM_SQDIFF_NORMED)
        centres += [y + int(top) + scaled.shape[0] // 2 for top in np.where(scores <= max_score)[0]]
    rows = []
    for centre in sorted(set(centres)):
        if not rows or centre - rows[-1] > template.shape[0]:
            rows.append(centre)
    return rows


def row_price(image: np.ndarray, band: list, y: int, templates: dict) -> float:
    """Price on the builder-list row whose title is centred at y; infinite when it cannot be read."""
    x, dy, w, h = band
    text = crop(image, [x, y + dy, w, h])
    if y + dy < 0 or text.shape[:2] != (h, w):
        return math.inf
    text = cv2.resize(text, None, fx=PRICE_SCALE, fy=PRICE_SCALE, interpolation=cv2.INTER_CUBIC)
    try:
        return read_trailing_number(text, templates, FRACTION_MIN_SCORE, PRICE_MAX_SATURATION)
    except BotError:
        return math.inf


def wall_order(choice: str, first: str) -> tuple:
    villages = VILLAGES if choice == 'both' else (choice,)
    return tuple(sorted(villages, key=lambda village: village != first))


def validate_walls(config: dict, root: Path, villages: tuple) -> None:
    resolution = config['resolution']
    for name in MENU_SCREENS:
        if name not in config.get('screens', {}):
            raise BotError(f'Kalibrasi layar belum tersedia: {name}')
    for village in villages:
        spec = config.get('walls', {}).get(village)
        if not isinstance(spec, dict):
            raise BotError(f'Upgrade tembok belum dikalibrasi di {village}')
        point(spec.get('build'), resolution, 'walls build')
        row = spec.get('row', {})
        rectangle(row.get('region'), resolution)
        integer(row.get('tap_x'), 0, resolution[0] - 1, 'walls row tap_x')
        price = row.get('price')
        if not isinstance(price, list) or len(price) != 4:
            raise BotError('walls row price perlu [x, dy, w, h]')
        rectangle([price[0], 0, price[2], price[3]], resolution)
        integer(price[1], -200, 200, 'walls row price dy')
        scales = row.get('scales', [1.0])
        if not isinstance(scales, list) or not scales or not all(
                isinstance(scale, float) and 0.5 <= scale <= 1.5 for scale in scales):
            raise BotError('walls row scales perlu daftar angka 0.5..1.5')
        scroll = spec.get('scroll')
        if not isinstance(scroll, list) or len(scroll) != 2:
            raise BotError('walls scroll perlu titik awal dan akhir')
        for location in scroll:
            point(location, resolution, 'walls scroll')
        gems = spec.get('gems')
        if not isinstance(gems, dict) or set(gems) != {'list', 'village'}:
            raise BotError('walls gems perlu area list dan village')
        for region in gems.values():
            rectangle(region, resolution)
        integer(spec.get('min_white'), 1, 100_000, 'walls min_white')
        if 'gold_reserve' in spec:
            integer(spec['gold_reserve'], 0, 100_000_000, 'walls gold_reserve')
            hud = spec.get('hud_gold')
            if not isinstance(hud, dict) or set(hud) != {'list', 'village'}:
                raise BotError('walls gold_reserve perlu hud_gold list dan village')
            for region in hud.values():
                rectangle(region, resolution)
        confirm = spec.get('confirm', {})
        if confirm.get('screen') not in MENU_SCREENS:
            raise BotError('walls confirm screen tidak valid')
        rectangle(confirm.get('price'), resolution)
        rectangle(confirm.get('icon'), resolution)
        integer(confirm.get('min_white'), 1, 100_000, 'walls confirm min_white')
        button = spec.get('button', {})
        rectangle(button.get('search'), resolution)
        for value in [*button.get('price', [None] * 4), *button.get('tap', [None] * 2)]:
            integer(value, -200, 200, 'walls button offset')
        integer(button.get('icon_min'), 1, 10_000, 'walls button icon_min')
        icons = confirm.get('icons', {})
        if not icons or set(icons) - set(WALL_RESOURCES):
            raise BotError('walls confirm icons hanya gold/elixir')
        files = [row.get('file'), button.get('file'), *icons.values()]
        for score in (button.get('max_score'),):
            if not isinstance(score, float) or not 0 < score <= SEARCH_ANCHOR_MAX_SCORE:
                raise BotError('max_score tombol tembok tidak valid')
        for file in files:
            if cv2.imread(str(asset_path(root, file))) is None:
                raise BotError(f'Template tembok hilang: {file}')
        for score in (row.get('max_score'), confirm.get('max_score')):
            if not isinstance(score, float) or not 0 < score <= TARGET_MAX_SCORE:
                raise BotError('max_score tembok tidak valid')


def memory_critical() -> bool:
    free = available_memory_mb()
    return free is not None and free < LOW_MEMORY_MB


def out_of_memory(exc: BaseException) -> bool:
    """A full PC memory is transient; other OpenCV errors are real bugs."""
    return isinstance(exc, MemoryError) or (isinstance(exc, cv2.error) and 'Insufficient memory' in str(exc))


def find_target(image: np.ndarray, template: np.ndarray, max_score: float,
                scales: tuple = (1.0,)) -> tuple[list, float]:
    """Best match over scales (camera zoom); returns centre and matched scale."""
    found = None
    for scale in scales:
        scaled = template if scale == 1.0 else cv2.resize(template, None, fx=scale, fy=scale,
                                                          interpolation=cv2.INTER_LINEAR)
        scores = cv2.matchTemplate(image, scaled, cv2.TM_SQDIFF_NORMED)
        best, _, location, _ = cv2.minMaxLoc(scores)
        if math.isfinite(best) and (found is None or best < found[0]):
            found = (best, location, scaled.shape[:2], scores, scale)
    if found is None or found[0] > max_score:
        raise TargetMissing('Target tidak ditemukan; tidak ada input dikirim')
    _, (x, y), (h, w), scores, scale = found
    others = scores.copy()
    others[max(0, y - h):y + h, max(0, x - w):x + w] = 1
    if float(others.min()) <= max_score:
        raise BotError('Target ambigu; tidak ada input dikirim')
    return [x + w // 2, y + h // 2], scale


def executable(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise BotError(f'{label} belum dikonfigurasi')
    found = shutil.which(value)
    if not found:
        raise BotError(f'{label} tidak ditemukan: {value}')
    return found


def digit_mask(image: np.ndarray) -> np.ndarray:
    """Bright, pale loot digits only; drops short blobs from the base behind them."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = ((hsv[..., 2] >= OCR_MIN_VALUE) & (hsv[..., 1] <= OCR_MAX_SATURATION)).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count <= 1:
        raise BotError('Angka loot tidak terlihat; hentikan tanpa input')
    heights = stats[1:, cv2.CC_STAT_HEIGHT]
    tall = 1 + np.flatnonzero(heights >= OCR_MIN_HEIGHT_RATIO * heights.max())
    tops = stats[tall, cv2.CC_STAT_TOP]
    bottoms = tops + stats[tall, cv2.CC_STAT_HEIGHT]
    top, bottom = float(np.median(tops)), float(np.median(bottoms))
    slack = OCR_ALIGN_RATIO * (bottom - top)
    aligned = tall[(np.abs(tops - top) <= slack) & (np.abs(bottoms - bottom) <= slack)]
    return np.where(np.isin(labels, aligned), 0, 255).astype(np.uint8)


def mask_canvas(glyph: np.ndarray) -> np.ndarray:
    """A glyph mask scaled to the fixed glyph height, aspect kept, centred."""
    h, w = glyph.shape
    width = max(1, min(DIGIT_WIDTH, round(w * DIGIT_HEIGHT / h)))
    canvas = np.zeros((DIGIT_HEIGHT, DIGIT_WIDTH), np.float32)
    left = (DIGIT_WIDTH - width) // 2
    canvas[:, left:left + width] = cv2.resize(glyph, (width, DIGIT_HEIGHT), interpolation=cv2.INTER_AREA)
    return canvas


def glyph_canvas(labels: np.ndarray, stats: np.ndarray, index: int) -> np.ndarray:
    """One component scaled to the fixed glyph height, aspect kept, centred."""
    x, y, w, h = stats[index, :4]
    return mask_canvas((labels[y:y + h, x:x + w] == index).astype(np.float32))


def split_glyphs(glyph: np.ndarray, count: int) -> list:
    """Touching digits cut into count glyphs at the thinnest columns, left to right, each without the bridge."""
    ink, width = glyph.sum(axis=0), glyph.shape[1]
    edges, cut = [0], 0
    for part in range(1, count):
        centre, reach = round(part * width / count), max(1, width // (2 * count))
        low, high = max(cut + 1, centre - reach), min(width - 1, centre + reach)
        if low > high:
            raise BotError('Angka menempel tidak bisa dipisah; tidak ada input')
        cut = low + int(np.argmin(ink[low:high + 1]))
        edges.append(cut)
    edges.append(width)
    pieces = []
    for left, right in zip(edges, edges[1:]):
        piece = glyph[:, left:right]
        rows = np.flatnonzero(piece.any(axis=1))
        columns = np.flatnonzero(piece.sum(axis=0) > BRIDGE_RATIO * piece.shape[0])
        if not len(rows) or not len(columns):
            raise BotError('Angka menempel tidak bisa dipisah; tidak ada input')
        pieces.append(piece[rows[0]:rows[-1] + 1, columns[0]:columns[-1] + 1])
    return pieces


def glyphs(image: np.ndarray) -> list:
    """Loot digits left to right, each scaled to a fixed height with aspect kept."""
    text = (digit_mask(image) == 0).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(text, connectivity=8)
    return [glyph_canvas(labels, stats, index)
            for index in sorted(range(1, count), key=lambda i: stats[i, cv2.CC_STAT_LEFT])]


def read_trailing_number(image: np.ndarray, templates: dict, min_score: float = DIGIT_MIN_SCORE,
                         max_saturation: int = OCR_MAX_SATURATION) -> int:
    """A right-aligned HUD number: the run of same-size glyphs ending at the right edge. Bars are see-through,
    so a village label behind one shows up at a different size and is left out."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    pale = ((hsv[..., 2] >= OCR_MIN_VALUE) & (hsv[..., 1] <= max_saturation)).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(pale, connectivity=8)
    # Pale walls seen through a bar pass the mask too, but stay dimmer than the solid digits.
    brightness = np.bincount(labels.ravel(), weights=hsv[..., 2].ravel(), minlength=count) / stats[:, cv2.CC_STAT_AREA]
    solid = [i for i in range(1, count) if brightness[i] >= HUD_MIN_MEAN_VALUE]
    if not solid:
        raise BotError('Angka tidak terlihat; hentikan tanpa input')
    tallest = max(stats[i, cv2.CC_STAT_HEIGHT] for i in solid)
    tall = [i for i in solid if stats[i, cv2.CC_STAT_HEIGHT] >= OCR_MIN_HEIGHT_RATIO * tallest]
    order = sorted(tall, key=lambda i: stats[i, cv2.CC_STAT_LEFT], reverse=True)
    top, height = stats[order[0], cv2.CC_STAT_TOP], stats[order[0], cv2.CC_STAT_HEIGHT]
    canvases, last_left = [], None
    for index in order:
        x, y, w, h = stats[index, :4]
        if abs(y - top) > HUD_ALIGN_PX or abs(h - height) > HUD_ALIGN_PX:
            continue
        # A thousands gap is narrower than a digit is tall; anything further left belongs to something else.
        if last_left is not None and last_left - (x + w) > HUD_GAP_RATIO * height:
            break
        glyph = (labels[y:y + h, x:x + w] == index).astype(np.float32)
        # Neighbouring digits can touch ("00"); skipping that blob would drop digits, so cut it apart.
        pieces = split_glyphs(glyph, max(2, round(w / (h * DIGIT_ASPECT)))) if w > WIDE_RATIO * h else [glyph]
        canvases.extend(mask_canvas(piece) for piece in reversed(pieces))
        last_left = x
    digits = [classify(canvas, templates, min_score) for canvas in reversed(canvases)]
    # A leading zero means digits on the left were lost.
    if not 1 <= len(digits) <= 10 or None in digits or (len(digits) > 1 and digits[0] == '0'):
        raise BotError('Angka meragukan; hentikan tanpa input')
    return int(''.join(digits))


def storage_fill(image: np.ndarray, bar: dict) -> float:
    """Storage bars fill from the icon (right) leftwards; tolerate gaps from digit outlines."""
    row, left, right = bar['row'], bar['left'], bar['right']
    hsv = cv2.cvtColor(image[row - 2:row + 3, left:right + 1], cv2.COLOR_BGR2HSV)
    low, high = bar['hue']
    colored = ((hsv[..., 0] >= low) & (hsv[..., 0] <= high) & (hsv[..., 1] > bar['min_saturation'])
               & (hsv[..., 2] > STORAGE_MIN_VALUE)).any(axis=0)
    if bar.get('from') == 'left':
        colored = colored[::-1]
    for start in range(len(colored)):
        if colored[start] and colored[start:].mean() >= STORAGE_RUN_RATIO:
            return (len(colored) - start) / len(colored)
    return 0.0


def small_gray(image: np.ndarray) -> np.ndarray:
    return cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), None, fx=0.25, fy=0.25,
                      interpolation=cv2.INTER_AREA)


def event_bytes(events: list) -> bytes:
    """Linux input_event structs for a 64-bit device: zero timestamp, type, code, value."""
    return b''.join(struct.pack('<qqHHi', 0, 0, kind, code, value) for kind, code, value in events)


def bar_cards(image: np.ndarray, region: list) -> list:
    """(left, right) of every card in the bar. The bar behind the cards is a flat dark strip, so a card is a run of
    columns with contrast; this follows whatever army is trained instead of fixed slots."""
    x, y, w, h = region
    gray = cv2.cvtColor(image[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY).astype(np.float32)
    busy = gray.std(axis=0) > BAR_CARD_STD
    cards, start = [], None
    for column, on in enumerate([*busy, False]):
        if on and start is None:
            start = column
        elif not on and start is not None:
            card = (x + start, x + column)
            if BAR_CARD_WIDTH[0] <= column - start <= BAR_CARD_WIDTH[1] and card_coloured(image, region, card):
                cards.append(card)
            start = None
    return cards


def card_coloured(image: np.ndarray, region: list, card: tuple) -> bool:
    """Empty slots are grey dashed outlines; a real card is mostly saturated colour."""
    _, y, _, h = region
    hsv = cv2.cvtColor(image[y:y + h, card[0]:card[1]], cv2.COLOR_BGR2HSV)
    return float(((hsv[..., 1] > 100) & (hsv[..., 2] > 80)).mean()) >= BAR_CARD_COLOUR


def card_kind(image: np.ndarray, region: list, card: tuple, camps: bool = False) -> str:
    """'counted' for troops and spells (an 'xN' label top right), 'siege' for the cyan siege card, 'warden' for the
    hero with the green ground/air toggle, 'hero' for the rest. Builder Base troop cards (camps) have no 'xN' label
    but a magenta header top right, which its hero cards lack."""
    left, right = card
    _, y, _, h = region
    if camps:
        header = cv2.cvtColor(image[y + 2:y + 18, right - 40:right - 4], cv2.COLOR_BGR2HSV)
        magenta = (header[..., 0] >= 135) & (header[..., 0] <= 165) & (header[..., 1] > 100) & (header[..., 2] > 120)
        return 'counted' if float(magenta.mean()) >= BAR_CAMP_HEADER else 'hero'
    label = image[y + 2:y + 20, right - 40:right - 2]
    if int(np.all(label > 215, axis=2).sum()) >= BAR_LABEL_WHITE:
        return 'counted'
    hsv = cv2.cvtColor(image[y:y + h, left:right], cv2.COLOR_BGR2HSV)
    hue, saturation, value = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    bright = (saturation > 100) & (value > 120)
    cyan = int((bright & (hue >= 80) & (hue <= 95)).sum())
    others = int((bright & (hue >= 95) & (hue <= 140)).sum())
    if cyan > others:
        return 'siege'
    bottom = hsv[h * 3 // 5:]
    green = int(((bottom[..., 0] >= 40) & (bottom[..., 0] <= 80) & (bottom[..., 1] > 120) & (bottom[..., 2] > 120)).sum())
    return 'warden' if green >= BAR_WARDEN_GREEN else 'hero'


def card_is_spell(image: np.ndarray, region: list, card: tuple) -> bool:
    """Only for armies with no siege or hero card to split troops from spells: troop cards are blue, spells purple."""
    _, y, _, h = region
    hsv = cv2.cvtColor(image[y:y + h, card[0]:card[1]], cv2.COLOR_BGR2HSV)
    bright = (hsv[..., 1] > 100) & (hsv[..., 2] > 120)
    blue = int((bright & (hsv[..., 0] >= 95) & (hsv[..., 0] <= 112)).sum())
    purple = int((bright & (hsv[..., 0] >= 118) & (hsv[..., 0] <= 140)).sum())
    return purple >= blue


def camp_plan(image: np.ndarray, auto: dict) -> list:
    """Builder Base deploy order for the cards on screen, left to right: a hero (Battle Machine, Battle Copter)
    dropped at the deploy point then its ability, and each troop card tapped once at the next spread point. One tap
    deploys the whole camp and its card stays coloured to show the camp's health, so a card is never held or tapped
    until grey; the Builder Base has no spells."""
    region, points = auto['bar'], auto['points']
    middle = region[1] + region[3] // 2
    plan, troops = [], 0
    for card in bar_cards(image, region):
        slot = [(card[0] + card[1]) // 2, middle]
        if card_kind(image, region, card, camps=True) == 'counted':
            plan.append({'slot': slot, 'count': 1, 'points': [points[troops % len(points)]]})
            troops += 1
        else:
            plan += [{'slot': slot, 'count': 1}, {'slot': slot, 'count': 1, 'tap_only': True}]
    return plan


def auto_plan(image: np.ndarray, auto: dict) -> list:
    """Deploy order for the cards on screen: every troop held at the deploy point until empty, the siege machine,
    the Grand Warden then the other heroes each followed by its ability, and the spells over the given points,
    listed as rounds of front, left and right, each round further in. With spread 'points' it is the Builder Base
    order instead."""
    if 'points' in auto:
        return camp_plan(image, auto)
    region = auto['bar']
    middle = region[1] + region[3] // 2
    cards = [(card, card_kind(image, region, card)) for card in bar_cards(image, region)]
    split = next((index for index, (_, kind) in enumerate(cards) if kind != 'counted'), None)
    troops, siege, heroes, spells = [], [], [], []
    for index, (card, kind) in enumerate(cards):
        slot = [(card[0] + card[1]) // 2, middle]
        if kind == 'counted':
            spell = index > split if split is not None else card_is_spell(image, region, card)
            (spells if spell else troops).append(slot)
        elif kind == 'siege':
            siege.append(slot)
        elif kind == 'warden':
            heroes.insert(0, slot)
        else:
            heroes.append(slot)
    plan = [{'slot': slot, 'count': 1, 'hold': auto['hold'], 'until_empty': True} for slot in troops]
    plan += [{'slot': slot, 'count': 1} for slot in siege]
    for slot in heroes:
        plan += [{'slot': slot, 'count': 1}, {'slot': slot, 'count': 1, 'tap_only': True}]
    points = auto['spells']
    # Each spell card starts one round (front, left, right) after the previous one, so no two land on one spot.
    for index, slot in enumerate(spells):
        start = index * SPELL_ROUND % len(points)
        plan.append({'slot': slot, 'count': SPELL_TAPS, 'until_empty': True, 'points': points[start:] + points[:start]})
    return plan


def card_empty(image: np.ndarray, slot: list) -> bool:
    """A used-up troop/spell card is rendered fully grey (saturation ~0)."""
    x, y = slot
    height, width = image.shape[:2]
    card = image[max(0, y - CARD_ABOVE):min(height, y + CARD_BELOW),
                 max(0, x - CARD_HALF_WIDTH):min(width, x + CARD_HALF_WIDTH)]
    return float(cv2.cvtColor(card, cv2.COLOR_BGR2HSV)[..., 1].mean()) < CARD_EMPTY_SATURATION


def classify(glyph: np.ndarray, templates: dict, min_score: float = DIGIT_MIN_SCORE) -> str | None:
    """Digit for a glyph, or None when the best match is weak or too close to the second best."""
    scores = sorted(((float(cv2.matchTemplate(glyph, template, cv2.TM_CCOEFF_NORMED)[0, 0]), name[0])
                     for name, template in templates.items()), reverse=True)
    best, digit = scores[0]
    second = next((score for score, other in scores[1:] if other != digit), -1.0)
    if not math.isfinite(best) or best < min_score or best - second < DIGIT_MIN_MARGIN:
        return None
    return digit


def read_number(image: np.ndarray, templates: dict, min_score: float = DIGIT_MIN_SCORE) -> int:
    """Template-match each glyph; a weak or close second-best match stops the bot."""
    found = glyphs(image)
    if not 1 <= len(found) <= 10:
        raise BotError('Jumlah angka loot tidak wajar; hentikan tanpa input')
    digits = [classify(glyph, templates, min_score) for glyph in found]
    if None in digits:
        raise BotError('Angka loot meragukan; hentikan tanpa input')
    return int(''.join(digits))


def read_fraction(image: np.ndarray, templates: dict) -> tuple[int, int]:
    """'current / maximum': exactly one unrecognised glyph (the slash) with digits on both sides."""
    found = glyphs(image)
    if not 3 <= len(found) <= 21:
        raise BotError('Teks isi/maks tidak wajar; hentikan tanpa input')
    digits = [classify(glyph, templates, FRACTION_MIN_SCORE) for glyph in found]
    unknown = [index for index, digit in enumerate(digits) if digit is None]
    if len(unknown) != 1 or unknown[0] in (0, len(digits) - 1):
        raise BotError('Teks isi/maks meragukan; hentikan tanpa input')
    split = unknown[0]
    return int(''.join(digits[:split])), int(''.join(digits[split + 1:]))


def ocr_pass(mask: np.ndarray, program: str, factor: int) -> int:
    scaled = cv2.resize(mask, None, fx=factor, fy=factor, interpolation=cv2.INTER_NEAREST)
    scaled = cv2.copyMakeBorder(scaled, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
    ok, encoded = cv2.imencode('.png', scaled)
    if not ok:
        raise BotError('Tidak bisa menyiapkan crop OCR')
    result = subprocess.run(
        [program, 'stdin', 'stdout', '--psm', '7', '-l', 'eng', 'tsv'],
        input=encoded.tobytes(), capture_output=True, check=True, timeout=15, shell=False,
    )
    rows = list(csv.DictReader(io.StringIO(result.stdout.decode('utf-8')), delimiter='\t'))
    words = [r for r in rows if r.get('text', '').strip()]
    if not words or any(not math.isfinite(float(r['conf']))
                        or not OCR_MIN_CONFIDENCE <= float(r['conf']) <= 100 for r in words):
        raise BotError('Confidence OCR rendah; hentikan tanpa input')
    return parse_resource(' '.join(r['text'].strip() for r in words))


def ocr_resource(image: np.ndarray, program: str) -> int:
    """Two OCR passes at different scales must agree before loot is trusted."""
    mask = digit_mask(image)
    try:
        values = {ocr_pass(mask, program, factor) for factor in OCR_SCALES}
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError, KeyError) as exc:
        raise BotError('OCR gagal; tidak ada serangan dilanjutkan') from exc
    if len(values) != 1:
        raise BotError('Dua bacaan OCR berbeda; hentikan tanpa input')
    return values.pop()


class Bot:
    def __init__(self, config: dict, root: Path, device: ADB, live: bool = False,
                 reader: Callable | None = None, timeout: float = 240, poll: float = 0.05,
                 emulator: 'Emulator | None' = None, goals: dict | None = None, walls: tuple = (),
                 minutes: int = 0, max_upgrades: int = 0) -> None:
        self.config, self.device, self.live, self.root = config, device, live, root
        self.goals = goals if goals is not None else parse_goals(config, None, None, 'builder')
        self.wall_order, self.walls_done, self.walls_skipped = walls, set(), set()
        self.stop_at = time.monotonic() + minutes * 60 if minutes else None
        self.upgrade_limit, self.upgrades_done = max_upgrades or None, 0
        self.gems_known: int | None = None
        self.wall_excluded: set = set()
        self.gems_fresh = False
        self.wall_list_top = False
        self.wall_price: float | None = None
        self.emulator = emulator
        self.emulator_started: float | None = None
        self.detector = Detector(config, root)
        self.timeout, self.poll = timeout, poll
        self.reader = reader or self.read_loot
        self.cart_full = False
        self.cart_misses, self.cart_lost = 0, False
        # Attacks finished plus walls upgraded; recoveries are counted again once it moves.
        self.progress = 0
        self.update_taps = 0
        self.capacity: dict = {}

    def observe(self) -> tuple[str, np.ndarray]:
        image = self.device.capture()
        state = self.detector.detect(image)
        if state == 'other_device':
            raise OtherDevice('Akun dibuka di perangkat lain; bot berhenti tanpa memuat ulang')
        if state != 'update_offer':
            self.update_taps = 0
        elif self.update_taps >= UPDATE_CLOSE_ATTEMPTS:
            raise UpdateRequired('CoC wajib di-update; update dulu lewat Play Store lalu jalankan bot lagi')
        return state, image

    def read_loot(self, image: np.ndarray, screen: dict) -> dict:
        active = [key for key in RESOURCES if self.config.get('minimum', {}).get(key, 0)]
        if not active:
            return {}
        if self.config.get('digits'):
            templates = load_digit_templates(self.root, self.config['digits'])
            return {key: read_number(crop(image, screen['loot'][key]), templates) for key in active}
        program = executable(self.config.get('tesseract'), 'Tesseract')
        return {key: ocr_resource(crop(image, screen['loot'][key]), program) for key in active}

    def wait(self, targets: set, allowed: set, timeout: float | None = None) -> str:
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        sped_up = False
        while time.monotonic() < deadline:
            try:
                state, image = self.observe()
            except UnknownScreen:
                time.sleep(self.poll)
                continue
            if state in targets:
                return state
            if not sped_up:
                sped_up = self.try_speed_up(image, state)
            if state in POPUPS:
                try:
                    self.action(state, POPUP_ACTIONS[state])
                except BotError:
                    LOG.info('Popup %s sudah tertutup sendiri', state)
                time.sleep(POPUP_PAUSE_SECONDS.get(state, 0) if self.poll else 0)
                continue
            if state == 'builder_cart' and state not in targets | allowed:
                self.action(state, 'close')
                continue
            if state in MENU_SCREENS and state not in targets | allowed:
                # A wall picked before a reload or a lost tap is still open; close it, never confirm it.
                self.close_menus()
                continue
            if state == 'builders_busy' and state not in targets | allowed:
                self.action(state, 'close')
                raise NoFreeBuilder('Semua tukang sibuk; tawaran gem ditutup tanpa membeli')
            if state == 'search_failed' and state not in targets | allowed:
                # The server refused the search ("coba lagi nanti"): go home, give it a minute, let recovery retry.
                self.action(state, 'close')
                time.sleep(SEARCH_RETRY_SECONDS if self.poll else 0)
                raise WaitTimeout('Pencarian lawan ditolak server; coba lagi nanti')
            if state == 'gem_offer' and state not in targets | allowed:
                # "Buy the missing resources with gems?" Only ever closed with X.
                self.action(state, 'close')
                raise WaitTimeout('Resource kurang; tawaran beli dengan gem ditutup tanpa membeli')
            if state not in allowed | {'loading'}:
                raise UnexpectedScreen(f'Layar tidak diharapkan saat menunggu: {state}')
            time.sleep(self.poll)
        raise WaitTimeout('Timeout; tidak mencoba input pemulihan')

    def tap_until(self, state: str, name: str, targets: set, timeout: float, retries: int = TAP_RETRIES) -> str:
        """Tap a menu button and wait; repeat the tap while the same menu is still shown (a lost tap)."""
        deadline = time.monotonic() + timeout
        taps = 0
        while True:
            if taps == 0 or (taps < retries and self.current_screen() == state):
                if taps:
                    LOG.warning('Tap %s di %s tidak berpengaruh; ulangi', name, state)
                self.action(state, name)
                taps += 1
            left = deadline - time.monotonic()
            try:
                return self.wait(targets, {state}, min(TAP_RETRY_SECONDS, max(left, 0)))
            except UnexpectedScreen:
                raise
            except WaitTimeout:
                if time.monotonic() >= deadline:
                    raise

    def current_screen(self) -> str | None:
        try:
            return self.observe()[0]
        except BotError:
            return None

    def try_speed_up(self, image: np.ndarray, state: str) -> bool:
        """Tap the battle speed button while it shows 1x (it toggles 1x <-> 4x); at most once per wait."""
        button = self.config.get('speed_button')
        if not button or not self.live or state not in BATTLE_STATES:
            return False
        x, y, _, _ = button['region']
        template = cv2.imread(str(asset_path(self.root, button['file'])))
        try:
            (px, py), _ = find_target(crop(image, button['region']), template, button['max_score'],
                                      tuple(button.get('scales', [1.0])))
        except BotError:
            return False
        self.device.tap_many([[x + px, y + py]])
        LOG.info('Kecepatan battle dinaikkan ke 4x')
        return True

    def guarded_tap(self, states: set, location: list | Callable, terminal: set | None = None) -> str:
        state, image = self.observe()
        for _ in range(POPUP_CLEAR_ATTEMPTS):
            if state not in POPUPS or state in states:
                break
            popup = self.config['screens'][state]['actions'][POPUP_ACTIONS[state]]
            if self.live:
                self.device.tap_many([popup])
            time.sleep(self.poll)
            state, image = self.observe()
        if terminal and state in terminal:
            return state
        if state not in states:
            raise UnexpectedScreen(f'Aksi dibatalkan: layar berubah menjadi {state}')
        resolved = location(image) if callable(location) else location
        if self.live:
            self.device.tap_many([resolved])
        return state

    def wait_still(self, limit: float) -> np.ndarray:
        """Return as soon as two consecutive frames match (camera stopped); give up after limit."""
        deadline = time.monotonic() + limit
        previous = small_gray(self.device.capture())
        while True:
            image = self.device.capture()
            current = small_gray(image)
            if float(cv2.absdiff(previous, current).mean()) < STILL_DIFF or time.monotonic() >= deadline:
                return image
            previous = current

    def action(self, state: str, name: str) -> None:
        screen = self.config['screens'][state]
        target = screen.get('targets', {}).get(name)
        if target is None:
            self.guarded_tap({state}, screen['actions'][name])
            if state == 'update_offer':
                self.update_taps += 1
            return
        def locate(image: np.ndarray) -> list:
            return self.locate_target(image, target)

        attempts = target.get('pan_attempts', 1) if 'pan' in target else 1
        search_first = target.get('search_first', False)
        if search_first and 'pan' in target:
            attempts += 1
        for attempt in range(attempts):
            if 'pan' in target and not (search_first and attempt == 0):
                self.pan(state, target['pan'])
            try:
                self.guarded_tap({state}, locate)
                return
            except TargetMissing:
                if attempt + 1 == attempts:
                    raise

    def locate_target(self, image: np.ndarray, target: dict) -> list:
        """Tap point for a target; alt_files cover other looks, e.g. the cart with an elixir bubble."""
        scales = tuple(target.get('scales', [1.0]))
        looks = [(target['file'], target.get('offset', [0, 0]))]
        looks += [(alt['file'], alt.get('offset', [0, 0])) if isinstance(alt, dict) else (alt, [0, 0])
                  for alt in target.get('alt_files', [])]
        for name, (dx, dy) in looks:
            template = cv2.imread(str(asset_path(self.root, name)))
            try:
                (x, y), scale = find_target(image, template, target['max_score'], scales)
            except TargetMissing:
                continue
            tap = point([round(x + dx * scale), round(y + dy * scale)], self.config['resolution'], 'target')
            for ax, ay, aw, ah in target.get('avoid', []):
                if ax <= tap[0] < ax + aw and ay <= tap[1] < ay + ah:
                    raise TargetMissing('Titik target jatuh di zona tombol UI; tidak ada input dikirim')
            return tap
        raise TargetMissing('Target tidak ditemukan; tidak ada input dikirim')

    def pan(self, state: str, vector: list) -> None:
        observed, _ = self.observe()
        if observed != state:
            raise BotError(f'Geser kamera dibatalkan: layar {observed}')
        if self.live:
            self.device.swipe(*vector)
            self.wait_still(PAN_SETTLE_SECONDS if self.poll else 0)

    def navigate(self, village: str) -> None:
        state = self.wait(set(VILLAGES), set(), 30)
        self.zoom_out(state)
        if state == village:
            return
        self.action(state, 'switch')
        self.wait({village}, {state}, 30)
        self.zoom_out(village)

    def zoom_out(self, state: str) -> None:
        """Pinch on the idle village only; battle zoom follows it into every stage."""
        zoom = self.config.get('zoom_out')
        if not zoom:
            return
        if state not in VILLAGES:
            raise BotError('Zoom hanya di layar desa; tidak ada input dikirim')
        self.wait({state}, set(), ZOOM_WAIT_SECONDS)
        if not self.live:
            return
        for _ in range(zoom['pinches']):
            self.pinch_once(zoom)
        selection = zoom.get('selection')
        if not selection:
            return
        template = cv2.imread(str(asset_path(self.root, selection['file'])))
        for attempt in range(ZOOM_CLEAR_ATTEMPTS + 1):
            state, image = self.observe()
            try:
                find_target(image, template, selection['max_score'])
            except TargetMissing:
                return
            if attempt == ZOOM_CLEAR_ATTEMPTS:
                raise UnexpectedScreen('Bangunan tetap terpilih setelah zoom; tidak ada input lanjutan')
            self.drop_selection(state, zoom)

    def drop_selection(self, state: str, zoom: dict) -> None:
        """On a village already zoomed out the pinch ends as a tap on the building under it, and pinching
        again selects it again; opening and closing the builder list drops the selection."""
        if state in MENU_SCREENS:
            self.close_menus()
            return
        spec = self.config.get('walls', {}).get(state)
        if not spec:
            self.pinch_once(zoom)
            return
        self.guarded_tap({state}, spec['build'])
        self.read_until(self.listed, WALL_TAP_SECONDS if self.poll else 0)
        self.action('build_list', 'close')
        self.wait({state}, {'build_list'}, WALL_TAP_SECONDS)

    def pinch_once(self, zoom: dict) -> None:
        start_gap, end_gap = zoom['gap']
        self.device.pinch(zoom['device'], zoom['center'], start_gap, end_gap)
        self.wait_still(ZOOM_SETTLE_SECONDS if self.poll else 0)

    def deploy(self, screen_name: str, allowed: set) -> str | None:
        screen = self.config['screens'][screen_name]
        terminal = {'home_result'} if screen_name == 'home_scout' else {'builder_result'}
        if screen_name == 'builder_scout':
            terminal |= {'builder_stage2'}
        state = self.checked_state(allowed, terminal)[0]
        if state in terminal:
            return state
        if self.battle_zoom():
            state = self.checked_state(allowed, terminal)[0]
            if state in terminal:
                return state
        if 'auto' in screen:
            # Read the card bar before any card is used; the plan follows the army that is trained.
            plan = self.bar_plan(screen['auto'], allowed)
            LOG.info('Rencana deploy dari bar: %s kartu', len({tuple(troop['slot']) for troop in plan}))
        else:
            plan = screen['troops']
        for troop in plan:
            if troop.get('wait') and self.live:
                time.sleep(troop['wait'])
                state = self.checked_state(allowed, terminal)[0]
                if state in terminal:
                    return state
            state = self.deploy_card(screen, troop, allowed, terminal)
            if state in terminal:
                return state
        sweep = screen.get('sweep')
        for slot in (sweep or {}).get('slots', []):
            state, image = self.checked_state(allowed, terminal)
            if state in terminal:
                return state
            if card_empty(image, slot):
                continue
            card = {'slot': slot, 'count': SWEEP_TAPS, 'until_empty': True, 'deploy': sweep['deploy']}
            state = self.deploy_card(screen, card, allowed, terminal)
            if state in terminal:
                return state
        return None

    def bar_plan(self, auto: dict, allowed: set) -> list:
        """Deploy plan from the card bar once two frames in a row show the same cards; at the start of Builder Base
        stage 2 the last cards are still sliding into the bar."""
        def read(image: np.ndarray) -> list:
            if self.detector.detect(image) not in allowed:
                raise BotError('Bar kartu dibaca di luar layar deploy')
            return auto_plan(image, auto)
        return self.read_stable(read, BAR_SETTLE_SECONDS if self.poll else 0)

    def hold_until_empty(self, troop: dict, location: list, allowed: set, terminal: set,
                         until_empty: bool) -> str:
        """Keep one finger down while watching the card; always lift the finger."""
        if self.live:
            self.device.tap_many([troop['slot']])
            self.device.touch_down(location)
        deadline = time.monotonic() + troop['hold']
        try:
            while True:
                state, image = self.checked_state(allowed, terminal)
                if state in terminal or (until_empty and card_empty(image, troop['slot'])):
                    return state
                if time.monotonic() >= deadline:
                    return state
        finally:
            if self.live:
                self.device.touch_up()

    def battle_zoom(self) -> bool:
        """Zoom out on the battle map before any card is selected, so edge deploy points line up."""
        zoom = self.config.get('zoom_out')
        if not zoom or not self.live:
            return False
        for _ in range(zoom.get('battle_pinches', zoom['pinches'])):
            start_gap, end_gap = zoom['gap']
            self.device.pinch(zoom['device'], zoom['center'], start_gap, end_gap)
        self.wait_still(ZOOM_SETTLE_SECONDS if self.poll else 0)
        return True

    def checked_state(self, allowed: set, terminal: set) -> tuple[str, np.ndarray]:
        for attempt in range(UNKNOWN_RETRIES + 1):
            try:
                state, image = self.observe()
                break
            except UnknownScreen:
                if attempt == UNKNOWN_RETRIES:
                    raise
        if state not in allowed | terminal:
            raise BotError(f'Deploy dibatalkan: layar berubah menjadi {state}')
        return state, image

    def deploy_card(self, screen: dict, troop: dict, allowed: set, terminal: set) -> str:
        """Select the card and tap in bursts; the screen is checked after every burst."""
        if troop.get('tap_only'):
            if self.live:
                self.device.tap_many([troop['slot']])
            return self.checked_state(allowed, terminal)[0]
        points = troop.get('points') or [troop.get('deploy', screen['deploy'])]
        until_empty = troop.get('until_empty', False)
        if troop.get('hold') and callable(getattr(self.device, 'touch_down', None)) \
                and getattr(self.device, 'touch', True):
            return self.hold_until_empty(troop, points[0], allowed, terminal, until_empty)
        if troop.get('hold'):
            state = ''
            for press in range(troop['count']):
                if self.live:
                    self.device.hold(troop['slot'] if press == 0 else None, points[press % len(points)], troop['hold'])
                state, image = self.checked_state(allowed, terminal)
                if state in terminal or (until_empty and card_empty(image, troop['slot'])):
                    break
            return state
        remaining, sent, state = troop['count'], 0, ''
        while remaining > 0:
            size = min(DEPLOY_BURST, remaining) if until_empty else remaining
            batch = [points[(sent + i) % len(points)] for i in range(size)]
            if self.live:
                self.device.tap_many(([troop['slot']] if sent == 0 else []) + batch)
            sent, remaining = sent + size, remaining - size
            state, image = self.checked_state(allowed, terminal)
            if state in terminal or (until_empty and card_empty(image, troop['slot'])):
                break
        return state

    def wait_new_scout(self, previous: np.ndarray) -> str:
        deadline = time.monotonic() + min(self.timeout, 60)
        stable = None
        while time.monotonic() < deadline:
            try:
                state, image = self.observe()
            except UnknownScreen:
                state = 'loading'
            if state == 'loading':
                stable = None
            elif state == 'gem_offer':
                self.action(state, 'close')
                raise OutOfGold('Gold habis untuk lawan berikutnya; tawaran gem ditutup')
            elif state != 'home_scout':
                raise UnexpectedScreen('Layar tidak diharapkan saat mencari lawan baru')
            else:
                delta = cv2.absdiff(previous, image)
                changed = float(np.mean(np.max(delta, axis=2) > 20)) > 0.02
                if changed and stable is not None and float(cv2.absdiff(stable, image).mean()) < 2:
                    return state
                stable = image if changed else None
            time.sleep(self.poll)
        raise BotError('Lawan belum berubah/stabil; hentikan pencarian')

    def scout_loot(self, image: np.ndarray) -> tuple:
        """(loot, frame) from a scout frame; a passing cloud or banner can make a single read fail."""
        if self.detector.detect(image) != 'home_scout':
            raise BotError('Scout berubah sebelum OCR')
        return self.reader(image, self.config['screens']['home_scout']), image

    def home_attack(self, searches: int) -> None:
        self.tap_until('home', 'attack', {'home_menu'}, 30)
        # After the game restarts the search button shows a short cooldown; keep tapping until it is available.
        self.tap_until('home_menu', 'find', {'home_army'}, FIND_COOLDOWN_SECONDS, FIND_RETRIES)
        self.tap_until('home_army', 'attack', {'home_scout'}, 60)
        broke = False
        for attempt in itertools.count(1):
            state, image = self.observe()
            if state != 'home_scout':
                raise BotError('Scout berubah sebelum OCR')
            try:
                loot, image = self.read_until(self.scout_loot, LOOT_READ_SECONDS if self.poll else 0)
                LOG.info('Loot terbaca: %s', loot)
            except BotError as exc:
                LOG.warning('Loot lawan tidak terbaca (%s); lewati lawan ini', exc)
                loot = None
            passes = loot is not None and meets_minimum(loot, self.config.get('minimum', {}))
            if attempt == searches and not (passes or broke):
                LOG.warning('Batas %s pencarian tercapai; serang lawan yang sedang tampil', searches)
            # Without gold for another search, or at the search limit, the base on screen is attacked instead of
            # stopping; its loot refills the gold.
            if passes or broke or attempt == searches:
                self.deploy('home_scout', {'home_scout', 'home_battle'})
                if self.wait({'home_result', 'home'}, {'home_scout', 'home_battle'}) == 'home':
                    LOG.warning('Battle berakhir tanpa layar hasil (game memuat ulang?); lanjut')
                    return
                self.action('home_result', 'return')
                self.wait({'home'}, {'home_result'}, 30)
                return
            self.action('home_scout', 'next')
            try:
                self.wait_new_scout(image)
            except OutOfGold as exc:
                LOG.warning('%s; serang lawan yang sedang tampil', exc)
                broke = True

    def collect_cart(self) -> None:
        """Builder elixir comes only from the Elixir Cart: open it, claim, close. Skip if not visible."""
        target = self.config['screens']['builder'].get('targets', {}).get('cart')
        if not target or 'builder_cart' not in self.config['screens']:
            return
        try:
            self.action('builder', 'cart')
        except TargetMissing:
            self.cart_misses += 1
            LOG.warning('Gerobak Eliksir tidak terlihat; lewati klaim')
            if self.cart_misses >= CART_MISS_LIMIT and not self.cart_lost:
                self.cart_lost = True
                LOG.warning('Gerobak Eliksir tidak terlihat %s kunjungan berturut-turut; target cart dianggap selesai',
                            CART_MISS_LIMIT)
            return
        self.cart_misses = 0
        try:
            self.wait({'builder_cart'}, {'builder'}, CART_OPEN_SECONDS)
        except BotError:
            if self.observe()[0] != 'builder':
                raise
            LOG.warning('Popup Gerobak Eliksir tidak muncul; lanjut tanpa klaim')
            return
        self.action('builder_cart', 'claim')
        self.cart_full = False
        if self.wait({'builder_cart', 'builder'}, set(), 15) == 'builder_cart':
            text, bar = self.config.get('cart_text'), self.config.get('cart_bar')
            if text:
                current, maximum = self.read_stable(
                    lambda image: read_fraction(crop(image, text), self.templates()),
                    CART_CLAIM_SECONDS if self.poll else 0)
                self.cart_full = maximum > 0 and current >= maximum
                LOG.info('Isi Gerobak Eliksir: %s/%s', f'{current:,}', f'{maximum:,}')
            elif bar:
                fill = storage_fill(self.observe()[1], bar)
                self.cart_full = fill >= FULL_RATIO
                LOG.info('Isi Gerobak Eliksir: %.0f%%', fill * 100)
            self.action('builder_cart', 'close')
            self.wait({'builder'}, {'builder_cart'}, 15)
        LOG.info('Gerobak Eliksir diklaim')

    def builder_attack(self, collect: bool = True) -> None:
        if collect:
            self.collect_cart()
        self.tap_until('builder', 'attack', {'builder_menu'}, 30)
        self.tap_until('builder_menu', 'find', {'builder_scout'}, FIND_COOLDOWN_SECONDS, FIND_RETRIES)
        self.deploy('builder_scout', {'builder_scout', 'builder_battle'})
        stage2 = {'builder_stage2', 'builder_battle2'}
        state = self.wait({'builder_stage2', 'builder_result', 'builder'},
                          {'builder_scout', 'builder_battle', 'builder_battle2'})
        if state == 'builder_stage2':
            self.wait_still(STAGE_SETTLE_SECONDS if self.poll else 0)
            self.deploy('builder_stage2', stage2)
            state = self.wait({'builder_result', 'builder'}, stage2)
        if state == 'builder':
            LOG.warning('Battle berakhir tanpa layar hasil (game memuat ulang?); lanjut')
            return
        self.action('builder_result', 'return')
        self.wait({'builder'}, {'builder_result'}, 30)

    def storage_max(self, village: str, key: str, spec: dict) -> int:
        """Tap the storage bar once per session and read 'Maks' from its tooltip."""
        if spec.get('maximum'):
            return spec['maximum']
        cached = self.capacity.get((village, key))
        if cached:
            return cached
        if self.live:
            self.guarded_tap({village}, spec['tap'])

        def read_max(image: np.ndarray) -> int:
            if not self.tooltip_open(image, spec):
                raise BotError('Tooltip gudang belum terbuka')
            return read_number(crop(image, spec['max']), self.templates(), FRACTION_MIN_SCORE)

        maximum = self.read_until(read_max, TOOLTIP_SHOW_SECONDS)
        if maximum <= 0:
            raise BotError('Kapasitas gudang tidak terbaca')
        self.capacity[(village, key)] = maximum
        if self.live and self.tooltip_open(self.device.capture(), spec):
            self.device.tap_many([spec['tap']])
        deadline = time.monotonic() + (TOOLTIP_CLEAR_SECONDS if self.poll else 0)
        while time.monotonic() < deadline:
            if not self.tooltip_open(self.device.capture(), spec):
                break
        else:
            if self.poll:
                raise BotError('Tooltip gudang tidak hilang; tidak ada input lanjutan')
        return maximum

    def tooltip_open(self, image: np.ndarray, spec: dict) -> bool:
        """With a 'Maks:' label template the tooltip is open only while the label is visible."""
        label = spec.get('label')
        if not label:
            try:
                read_number(crop(image, spec['max']), self.templates(), FRACTION_MIN_SCORE)
                return True
            except BotError:
                return False
        template = cv2.imread(str(asset_path(self.root, label['file'])))
        try:
            find_target(crop(image, label['region']), template, label['max_score'])
            return True
        except BotError:
            return False

    def read_until(self, reader: Callable, limit: float):
        """Read from fresh screenshots until the value appears; no fixed wait."""
        deadline = time.monotonic() + limit
        while True:
            try:
                return reader(self.device.capture())
            except BotError:
                if time.monotonic() >= deadline:
                    raise

    def read_stable(self, reader: Callable, limit: float):
        """Read until two consecutive screenshots give the same value; animation frames are skipped."""
        deadline = time.monotonic() + limit
        previous = None
        while True:
            try:
                current = reader(self.device.capture())
            except BotError:
                current = None
            if current is not None and current == previous:
                return current
            if time.monotonic() >= deadline:
                if current is None and previous is None:
                    raise BotError('Angka tidak terbaca; tidak ada input lanjutan')
                return current if current is not None else previous
            previous = current

    def templates(self) -> dict:
        return load_digit_templates(self.root, self.config['digits'])

    def village_full(self, village: str, image: np.ndarray, keys: set | None = None) -> bool:
        capacity = self.config.get('capacity', {}).get(village)
        if capacity:
            capacity = {key: spec for key, spec in capacity.items() if keys is None or key in keys}
            amounts = {key: read_number(crop(image, spec['bar']), self.templates(), FRACTION_MIN_SCORE)
                       for key, spec in capacity.items()}
            maxima = {key: self.storage_max(village, key, spec) for key, spec in capacity.items()}
            LOG.info('Gudang %s: %s', village, {key: f'{amounts[key]:,}/{maxima[key]:,}' for key in capacity})
            return all(amounts[key] >= maxima[key] for key in capacity)
        bars = {key: bar for key, bar in self.config.get('storage', {}).get(village, {}).items()
                if keys is None or key in keys}
        fills = {key: storage_fill(image, bar) for key, bar in bars.items()}
        LOG.info('Isi gudang %s: %s', village, {key: f'{value:.0%}' for key, value in fills.items()})
        return bool(fills) and all(value >= FULL_RATIO for value in fills.values())

    def village_done(self, village: str, image: np.ndarray) -> bool:
        """Done when every chosen storage and, if chosen, the Elixir Cart is full. A cart that stays out of sight
        visit after visit cannot be read, so it no longer keeps the run attacking."""
        wanted = self.goals[village]
        storage = wanted - {'cart'}
        if storage and not self.village_full(village, image, storage):
            return False
        return 'cart' not in wanted or self.cart_full or self.cart_lost

    def run_until_full(self, searches: int) -> int:
        """Farm the chosen villages in order until each one's chosen resources are full."""
        integer(searches, 0, 1_000_000, 'searches')
        full, attacks = set(), 0
        self.wait(set(VILLAGES), set(), 30)
        village = next(iter(self.goals))
        while True:
            self.check_limits()
            self.refresh_if_low_memory()
            self.navigate(village)
            if village == 'builder':
                self.collect_cart()
            self.wait({village}, set(), 30)
            _, image = self.observe()
            if self.village_done(village, image):
                full.add(village)
                pending = [name for name in self.goals if name not in full]
                if not pending:
                    LOG.info('Semua target penuh; selesai setelah %s serangan', attacks)
                    return attacks
                village = pending[0]
                continue
            if village == 'home':
                self.home_attack(searches)
            else:
                self.builder_attack(collect=False)
            attacks += 1
            self.progress += 1
            LOG.info('Serangan selesai: %s (%s)', village, attacks)

    def check_limits(self) -> None:
        """Called only between attacks and upgrades, so a limit never cuts a battle or an upgrade short."""
        if self.upgrade_limit is not None and self.upgrades_done >= self.upgrade_limit:
            reason = f'{self.upgrades_done} tembok sudah di-upgrade'
        elif self.stop_at is not None and time.monotonic() >= self.stop_at:
            reason = 'batas waktu tercapai'
        else:
            return
        self.close_menus()
        raise LimitReached(f'Selesai: {reason}; bot berhenti di desa')

    def list_open(self, image: np.ndarray) -> bool:
        return self.detector.matches(image, self.detector.templates['build_list'])

    def gem_region(self, image: np.ndarray, spec: dict) -> list:
        """The open builder list pushes the resource bars left, so the gem count moves with it."""
        return spec['gems']['list' if self.list_open(image) else 'village']

    def read_gems(self, image: np.ndarray, spec: dict) -> int:
        return read_trailing_number(crop(image, self.gem_region(image, spec)), self.templates(), FRACTION_MIN_SCORE)

    def stable_gems(self, spec: dict) -> int:
        """Gem count once two fresh frames agree; the resource bars slide while the builder list moves."""
        return self.read_stable(lambda image: self.read_gems(image, spec), GEM_READ_SECONDS if self.poll else 0)

    def gem_count(self, spec: dict) -> int:
        try:
            return self.stable_gems(spec)
        except BotError as exc:
            raise GemUnreadable(f'Gem tidak terbaca ({exc}); tidak ada upgrade') from exc

    def compare_gems(self, now: int) -> None:
        """A count with other digits than the one on record is a misread; the same digits but lower is a drop."""
        known = self.gems_known
        if len(str(now)) != len(str(known)):
            raise GemUnreadable(f'Gem terbaca {now:,}, tercatat {known:,}; dibaca ulang tanpa upgrade')
        if now < known:
            raise GemSpent(f'Gem turun {known:,} -> {now:,}; upgrade tembok dihentikan')

    def start_gem_watch(self, spec: dict) -> None:
        """Each wall session starts from the count on record, kept across recoveries; a top-up raises it."""
        now = self.gem_count(spec)
        if self.gems_known is not None:
            self.compare_gems(now)
        self.gems_known = now
        self.gems_fresh = True

    def check_gems(self, spec: dict) -> None:
        now = self.gem_count(spec)
        self.compare_gems(now)
        if now > self.gems_known:
            # Walls never add gems, so a higher count mid-session is a misread that could hide a drop.
            raise GemUnreadable(f'Gem terbaca naik {self.gems_known:,} -> {now:,}; dibaca ulang tanpa upgrade')
        self.gems_fresh = True

    def wall_rows(self, image: np.ndarray, spec: dict) -> list:
        """(price, y) of every 'Tembok' row in view, top first. A price running past the bottom of the list is
        half hidden by the tabs, so it counts as unread; that row shows whole after the next swipe."""
        row, templates = spec['row'], self.templates()
        template = cv2.imread(str(asset_path(self.root, row['file'])))
        _, dy, _, h = row['price']
        _, top, _, height = row['region']
        return [(row_price(image, row['price'], y, templates) if y + dy + h <= top + height else math.inf, y)
                for y in find_rows(image, template, row['region'], row['max_score'], row.get('scales', (1.0,)))]

    def priced_row(self, image: np.ndarray, spec: dict, price: float) -> int | None:
        found = [y for shown, y in self.wall_rows(image, spec) if shown == price]
        return found[-1] if found else None

    def wall_row(self, spec: dict) -> int | None:
        """y of the lowest-level 'Tembok' row, which is the cheapest: each wall level costs more than the one
        below. The list groups rows by resource and puts what is affordable first, so a row's place says nothing
        about its level; walk it from the top and come back to the cheapest price. Walls only level up, so while
        that price is listed its row is still the lowest: later picks stop at it, without scrolling while the
        list stays put."""
        down, up = spec['scroll'], spec['scroll'][::-1]
        if self.wall_price is not None and not self.wall_list_top:
            y = self.priced_row(self.device.capture(), spec, self.wall_price)
            if y is not None:
                return y
        settle = WALL_LIST_SETTLE_SECONDS if self.poll else 0
        image = self.wait_still(settle) if self.wall_list_top else self.scroll_list(spec, up, None)
        self.wall_list_top = False
        prices = []
        for _ in range(WALL_SCROLL_LIMIT):
            rows = self.wall_rows(image, spec)
            found = [y for price, y in rows if price == self.wall_price]
            if found:
                return found[-1]
            prices += [price for price, _ in rows]
            moved = self.scroll_list(spec, down, image, once=True)
            if moved is None:
                break
            image = moved
        if not prices:
            self.wall_price = None
            return None
        self.wall_price = min(prices)
        if math.isinf(self.wall_price):
            LOG.warning('Harga tembok tidak terbaca; pakai baris tembok paling bawah')
        for _ in range(WALL_SCROLL_LIMIT):
            y = self.priced_row(image, spec, self.wall_price)
            if y is not None:
                return y
            image = self.scroll_list(spec, up, image, once=True)
            if image is None:
                break
        # Something covered the list mid-walk (a popup, a reload); a stall, so recovery tidies up and walks again.
        raise WaitTimeout('Baris tembok termurah tidak ketemu lagi; tidak ada input')

    def scroll_list(self, spec: dict, swipe: list, image: np.ndarray | None, once: bool = False) -> np.ndarray | None:
        """Swipe the builder list until it stops moving (its end) and return that frame; with once, swipe one
        step and return the new frame, or None when the list did not move."""
        region = spec['row']['region']
        settle = WALL_LIST_SETTLE_SECONDS if self.poll else 0
        frame = image if image is not None else self.wait_still(settle)
        for _ in range(WALL_SCROLL_LIMIT):
            if self.live:
                self.device.swipe(*swipe)
            moved = self.wait_still(settle)
            if float(cv2.absdiff(crop(frame, region), crop(moved, region)).mean()) < STILL_DIFF:
                return None if once else moved
            if once:
                return moved
            frame = moved
        return frame

    def wall_buttons(self, image: np.ndarray, spec: dict) -> list:
        """(resource, tap, affordable) for every upgrade button on screen. The picked wall decides where its
        buttons go and whether they wrap into two rows, so they are searched for, not read at fixed spots."""
        button = spec['button']
        template = cv2.imread(str(asset_path(self.root, button['file'])))
        x0, y0, w0, h0 = button['search']
        region = image[y0:y0 + h0, x0:x0 + w0]
        hits = []
        for scale in button.get('scales', [1.0]):
            scaled = template if scale == 1.0 else cv2.resize(template, None, fx=scale, fy=scale)
            if scaled.shape[0] > region.shape[0] or scaled.shape[1] > region.shape[1]:
                continue
            scores = cv2.matchTemplate(region, scaled, cv2.TM_SQDIFF_NORMED)
            for y, x in zip(*np.where(scores <= button['max_score'])):
                hits.append((float(scores[y, x]), x0 + int(x), y0 + int(y), scale))
        height, width = template.shape[:2]
        found = []
        for _, x, y, scale in sorted(hits):
            if all(abs(x - fx) >= width or abs(y - fy) >= height for fx, fy, _ in found):
                found.append((x, y, scale))
        result = []
        for x, y, scale in found:
            px, py, pw, ph = button['price']
            band = [x + round(px * scale), y + round(py * scale), round(pw * scale), round(ph * scale)]
            resource = price_resource(crop(image, band), button['icon_min'])
            if resource is None:
                continue
            affordable = white_pixels(image, band) >= spec['min_white'] * scale * scale
            tap = [x + round(button['tap'][0] * scale), y + round(button['tap'][1] * scale)]
            result.append((resource, tap, affordable))
        return result

    def affordable_button(self, image: np.ndarray, spec: dict) -> tuple | None:
        """(resource, tap) of the first upgrade button with a white price, gold before elixir."""
        buttons = self.wall_buttons(image, spec)
        for resource in WALL_RESOURCES:
            if resource in self.wall_excluded or resource not in spec['confirm']['icons']:
                continue
            for found, tap, affordable in buttons:
                if found == resource and affordable:
                    return resource, tap
        return None

    def affordable_soon(self, spec: dict) -> bool:
        """A price still fading in reads red at first, so look again briefly before giving up on the wall."""
        def white(frame: np.ndarray) -> tuple:
            choice = self.affordable_button(frame, spec)
            if choice is None:
                raise BotError('Harga tembok belum putih')
            return choice
        try:
            self.read_until(white, WALL_PRICE_SECONDS if self.poll else 0)
            return True
        except BotError:
            buttons = self.wall_buttons(self.device.capture(), spec)
            LOG.info('Tombol upgrade tembok: %s', ', '.join(
                f'{resource} {"putih" if affordable else "merah"}' for resource, _, affordable in buttons) or 'tidak ada')
            return False

    def hud_gold(self, spec: dict) -> int:
        """Gold once two fresh frames agree; the bars slide while the builder list opens or closes."""
        regions = spec['hud_gold']
        return self.read_stable(
            lambda image: read_trailing_number(crop(image, regions['list' if self.list_open(image) else 'village']),
                                               self.templates(), FRACTION_MIN_SCORE),
            GEM_READ_SECONDS if self.poll else 0)

    def confirm_price(self, image: np.ndarray, spec: dict) -> int:
        return read_trailing_number(crop(image, spec['confirm']['price']), self.templates(), FRACTION_MIN_SCORE)

    def keeps_reserve(self, image: np.ndarray, spec: dict, gold: int, reserve: int) -> bool:
        try:
            return gold - self.confirm_price(image, spec) >= reserve
        except BotError:
            return False

    def confirm_safe(self, image: np.ndarray, spec: dict, resource: str) -> bool:
        """Konfirmasi is tapped only for a white price next to the chosen resource's icon, never a gem one."""
        confirm = spec['confirm']
        if white_pixels(image, confirm['price']) < confirm['min_white']:
            return False
        icon = cv2.imread(str(asset_path(self.root, confirm['icons'][resource])))
        try:
            find_target(crop(image, confirm['icon']), icon, confirm['max_score'])
        except BotError:
            return False
        return True

    def upgrade_wall(self, spec: dict) -> bool:
        """Upgrade the selected wall once with gold or elixir; False when neither price is affordable."""
        state, image = self.observe()
        if state != 'wall_selected':
            raise BotError(f'Tembok tidak terpilih: layar {state}')
        # The count read after the last confirm still holds while nothing has been tapped since.
        if not self.gems_fresh:
            self.check_gems(spec)
        if self.affordable_button(image, spec) is None and not self.affordable_soon(spec):
            return False
        reserve, gold = spec.get('gold_reserve'), None
        if reserve and 'gold' not in self.wall_excluded:
            try:
                gold = self.hud_gold(spec)
            except BotError:
                LOG.warning('Gold di HUD tidak terbaca; cadangan gold tidak bisa dijaga, tembok pakai elixir saja')
                self.wall_excluded.add('gold')
                return self.upgrade_wall(spec)
        picked = []

        def locate(frame: np.ndarray) -> list:
            # The list can slide shut after a pick and move the buttons, so look again in the tapped frame.
            choice = self.affordable_button(frame, spec)
            if choice is None:
                raise TargetMissing('Tombol upgrade tembok tidak terjangkau lagi; tidak ada input dikirim')
            picked.append(choice[0])
            return choice[1]
        self.gems_fresh = False
        try:
            self.guarded_tap({'wall_selected'}, locate)
        except TargetMissing:
            return False
        resource, dialog = picked[0], spec['confirm']['screen']
        after = {'wall_selected', 'build_list', *VILLAGES}
        self.wait({dialog}, {'wall_selected', 'build_list'}, WALL_TAP_SECONDS)
        _, image = self.observe()
        if not self.confirm_safe(image, spec, resource):
            LOG.warning('Dialog upgrade tembok (%s) tidak sesuai; ditutup tanpa konfirmasi', resource)
            self.action(dialog, 'close')
            self.wait(after, {dialog}, WALL_TAP_SECONDS)
            return False
        if resource == 'gold' and reserve and not self.keeps_reserve(image, spec, gold, reserve):
            # Searching for an opponent costs gold; with none left the game offers to sell it for gems.
            LOG.info('Gold %s disisakan untuk mencari lawan; tembok berikutnya pakai elixir', f'{reserve:,}')
            self.action(dialog, 'close')
            self.wait(after, {dialog}, WALL_TAP_SECONDS)
            self.wall_excluded.add('gold')
            return self.upgrade_wall(spec)
        self.action(dialog, 'confirm')
        self.wait(after, {dialog}, WALL_TAP_SECONDS)
        self.check_gems(spec)
        self.progress += 1
        LOG.info('Tembok di-upgrade dengan %s', resource)
        return True

    def listed(self, image: np.ndarray) -> np.ndarray:
        if not self.list_open(image):
            raise UnexpectedScreen('Daftar Bangun belum terbuka')
        return image

    def open_build_list(self, village: str, spec: dict) -> np.ndarray:
        """A frame with the builder list open; picking a wall row sometimes closes the list."""
        image = self.device.capture()
        if self.list_open(image):
            return image
        # A reopened list starts at its top.
        self.wall_list_top = True
        self.guarded_tap({village, 'wall_selected'}, spec['build'])
        return self.read_until(self.listed, WALL_TAP_SECONDS if self.poll else 0)

    def close_menus(self) -> None:
        """Close the wall dialog and the builder list, top layer first; a wall selected without the list is
        dropped by opening the list and closing it."""
        for _ in range(2 * len(MENU_SCREENS)):
            try:
                state, image = self.observe()
            except UnknownScreen:
                self.wait_still(WALL_LIST_SETTLE_SECONDS if self.poll else 0)
                continue
            if state not in MENU_SCREENS:
                return
            try:
                if state == 'wall_selected' and not self.list_open(image):
                    self.action(state, 'build')
                    self.read_until(self.listed, WALL_TAP_SECONDS if self.poll else 0)
                    continue
                # Closing the list can leave the wall selected, so look again instead of waiting for a new screen.
                self.action(state, 'close')
            except UnexpectedScreen:
                # The list was still sliding when it was looked at; look again.
                pass
            self.wait_still(WALL_LIST_SETTLE_SECONDS if self.poll else 0)
        raise UnexpectedScreen('Menu tembok tidak bisa ditutup; tidak ada input lanjutan')

    def settle_selection(self) -> None:
        """Go on as soon as the picked wall's button layout holds; the list may slide shut right after a pick."""
        deadline = time.monotonic() + (WALL_SELECT_SECONDS if self.poll else 0)
        layout, since = None, time.monotonic()
        while time.monotonic() < deadline:
            image = self.device.capture()
            try:
                state = self.detector.detect(image)
            except BotError:
                state = None
            now = (state, self.list_open(image))
            if now != layout:
                layout, since = now, time.monotonic()
            elif state == 'wall_selected' and time.monotonic() - since >= WALL_LAYOUT_HOLD_SECONDS:
                return

    def upgrade_walls(self, village: str) -> int | None:
        """Spend gold and elixir on walls one at a time; the number upgraded, or None once no wall is left."""
        spec = self.config['walls'][village]
        state = self.current_screen()
        if state in MENU_SCREENS:
            self.close_menus()
            state = self.current_screen()
        # The builder list needs no zoom; only the Builder Base zooms, for the Elixir Cart it claims first.
        if village == 'builder' or state != village:
            self.navigate(village)
        if village == 'builder':
            self.collect_cart()
        self.wait({village}, set(), 30)
        self.open_build_list(village, spec)
        self.start_gem_watch(spec)
        self.wall_excluded, self.wall_price = set(), None
        upgraded = 0
        while True:
            self.check_limits()
            self.open_build_list(village, spec)
            y = self.wall_row(spec)
            if y is None:
                self.listed(self.device.capture())
                LOG.info('Semua tembok %s sudah level maksimal (%s upgrade sesi ini)', village, upgraded)
                self.close_menus()
                return None
            self.guarded_tap({'build_list', 'wall_selected'}, [spec['row']['tap_x'], y])
            self.wait({'wall_selected'}, {'build_list', village}, WALL_TAP_SECONDS)
            self.settle_selection()
            if not self.upgrade_wall(spec):
                LOG.info('Gold dan elixir %s tidak cukup lagi setelah %s upgrade tembok', village, upgraded)
                self.close_menus()
                return upgraded
            upgraded += 1
            self.upgrades_done += 1

    def run_walls(self, searches: int) -> int:
        """Per village in order: spend gold and elixir on walls, farm both full, repeat until every wall is maxed."""
        for village in self.wall_order:
            farmed = False
            while village not in self.walls_done:
                try:
                    upgraded = self.upgrade_walls(village)
                except NoFreeBuilder as exc:
                    LOG.warning('Tembok %s dilewati: %s; jalankan lagi saat ada tukang bebas', village, exc)
                    self.close_menus()
                    self.walls_skipped.add(village)
                    break
                if upgraded is None:
                    self.walls_done.add(village)
                    break
                if upgraded == 0 and farmed:
                    raise BotError(f'Tembok {village} tidak bisa di-upgrade walau gudang penuh; periksa kalibrasi')
                self.goals = {village: {'gold', 'elixir'}}
                self.run_until_full(searches)
                farmed = True
        if self.walls_done:
            LOG.info('Semua tembok sudah maksimal: %s', ', '.join(sorted(self.walls_done)))
        return len(self.walls_done)

    def recover(self) -> None:
        """Wait for ADB, start the game if it is not in front, and wait for a village screen."""
        if self.emulator and not self.device.connected() and not self.emulator.running():
            LOG.warning('LDPlayer belum berjalan; menyalakan emulator')
            # A VM that was just quit is still shutting down; launching at once leaves it unbooted.
            time.sleep(EMULATOR_SETTLE_SECONDS if self.poll else 0)
            self.emulator.launch()
            self.emulator_started = time.monotonic()
        deadline = time.monotonic() + RECOVER_ADB_SECONDS
        while not (self.device.connected() and self.device.booted()):
            if time.monotonic() >= deadline:
                if self.emulator:
                    # LDPlayer can keep its window while the VM never boots; quit so the next try starts fresh.
                    LOG.warning('ADB tidak kembali; mematikan LDPlayer agar dinyalakan ulang')
                    self.emulator.quit()
                raise DeviceError('ADB tidak kembali; pemulihan dihentikan')
            time.sleep(RECOVER_POLL_SECONDS if self.poll else 0)
        for attempt in range(LAUNCH_ATTEMPTS):
            if not self.device.game_in_front():
                LOG.warning('CoC tidak terbuka; membuka game (percobaan %s)', attempt + 1)
                self.device.launch(GAME_ACTIVITY)
            try:
                # The game may come back mid-battle, on a result screen or in a menu; settle handles all of them.
                self.settle()
                LOG.info('Game siap kembali di layar desa')
                return
            except DeviceError:
                if self.emulator:
                    # ADB shell answers but screencap hangs: the emulator display is stuck; restart it.
                    LOG.warning('Layar emulator tidak bisa diambil; mematikan LDPlayer agar dinyalakan ulang')
                    self.emulator.quit()
                raise
            except BotError:
                if self.device.game_in_front() and attempt + 1 == LAUNCH_ATTEMPTS:
                    raise
        raise DeviceError('CoC tidak bisa dibuka; pemulihan dihentikan')

    def run_until_full_resilient(self, searches: int, max_recoveries: int = MAX_RECOVERIES,
                                 job: Callable | None = None) -> int:
        """Run job (until-full by default), reopening the game after a crash, closed app or ADB drop."""
        integer(max_recoveries, 0, 1000, 'max_recoveries')
        recoveries, progress = 0, self.progress
        try:
            ready = self.device.connected() and self.device.game_in_front()
        except DeviceError:
            ready = False
        # The recovery step runs inside the try, so a recovery that fails is retried like any other loss.
        # Started mid-battle or on a result screen, first let the battle end and return to the village.
        pending = self.settle if ready else self.recover
        while True:
            try:
                if pending:
                    step, pending = pending, None
                    step()
                return (job or self.run_until_full)(searches)
            except (MemoryError, cv2.error) as exc:
                recoveries, progress = self.recoveries_since(recoveries, progress)
                if not out_of_memory(exc) or recoveries >= max_recoveries:
                    raise
                recoveries += 1
                LOG.warning('Memori PC penuh (%s); jeda lalu lanjut, pemulihan %s/%s', exc, recoveries, max_recoveries)
                gc.collect()
                time.sleep(MEMORY_RETRY_SECONDS if self.poll else 0)
                self.release_touch()
                pending = self.settle_after_memory
            except BotError as exc:
                recoveries, progress = self.recoveries_since(recoveries, progress)
                lost = isinstance(exc, DeviceError)
                if not lost:
                    try:
                        lost = not self.device.game_in_front()
                    except DeviceError:
                        lost = True
                # With PC memory nearly exhausted, screenshots fail and any wait can time out.
                starved = not lost and memory_critical()
                # Slow matchmaking, a lagging screen or a passing animation, with the game still open.
                stalled = not (lost or starved) and isinstance(exc, (WaitTimeout, UnknownScreen))
                if not (lost or starved or stalled) or recoveries >= max_recoveries:
                    raise
                recoveries += 1
                self.release_touch()
                if starved:
                    LOG.warning('Memori PC hampir habis (%s); jeda lalu lanjut, pemulihan %s/%s',
                                exc, recoveries, max_recoveries)
                    gc.collect()
                    time.sleep(MEMORY_RETRY_SECONDS if self.poll else 0)
                    pending = self.settle_after_memory
                    continue
                if stalled:
                    LOG.warning('Layar macet (%s); rapikan layar lalu lanjut, pemulihan %s/%s',
                                exc, recoveries, max_recoveries)
                    pending = self.settle
                    continue
                LOG.warning('Game tertutup/terputus (%s); pemulihan %s/%s', exc, recoveries, max_recoveries)
                pending = self.recover

    def recoveries_since(self, recoveries: int, progress: int) -> tuple[int, int]:
        """Recoveries since the run last moved on: an attack finished or a wall upgraded since the last recovery starts
        the count again, so rare stalls spread over a long run do not add up to the limit."""
        if self.progress != progress:
            return 0, self.progress
        return recoveries, progress

    def settle_after_memory(self) -> None:
        self.settle()
        self.refresh_if_low_memory()

    def release_touch(self) -> None:
        touch_up = getattr(self.device, 'touch_up', None)
        if callable(touch_up) and getattr(self.device, 'touch', True):
            try:
                touch_up()
            except BotError:
                pass

    def refresh_if_low_memory(self) -> bool:
        """LDPlayer grows its memory over hours; restart it between attacks before the PC runs out."""
        if not (self.live and self.emulator):
            return False
        if self.emulator_started is not None and time.monotonic() - self.emulator_started < EMULATOR_REFRESH_SECONDS:
            return False
        free = available_memory_mb()
        if free is None or free >= LOW_MEMORY_MB:
            return False
        LOG.warning('Memori PC tinggal %s MB; restart LDPlayer untuk membebaskan memori', free)
        self.emulator.quit()
        deadline = time.monotonic() + (EMULATOR_STOP_SECONDS if self.poll else 0)
        while self.emulator.running() and time.monotonic() < deadline:
            time.sleep(RECOVER_POLL_SECONDS)
        self.recover()
        return True

    def settle(self) -> None:
        """After an interrupted run, close an open attack menu or let a battle end and leave its result screen."""
        for _ in range(SETTLE_MENU_LAYERS):
            state = self.current_screen()
            if state in MENU_SCREENS:
                self.close_menus()
                return
            if state in VILLAGES:
                return
            if not state or 'close' not in self.config['screens'][state].get('actions', {}):
                break
            # Attack menus stack (the army screen sits over the attack menu), so close one layer at a time.
            self.action(state, 'close')
            self.wait_still(WALL_LIST_SETTLE_SECONDS if self.poll else 0)
        busy =set(BATTLE_STATES) | {'home_scout', 'builder_scout', 'builder_stage2', *RESULT_SCREENS}
        state = self.wait(set(VILLAGES) | set(RESULT_SCREENS), busy, BATTLE_END_SECONDS if self.poll else self.timeout)
        if state in RESULT_SCREENS:
            self.action(state, 'return')
            self.wait(set(VILLAGES), {state}, 30)

    def run(self, mode: str, cycles: int, searches: int) -> int:
        integer(cycles, 1, 100, 'cycles')
        integer(searches, 0, 1_000_000, 'searches')
        if mode not in ('home', 'builder', 'both'):
            raise BotError('Mode tidak dikenal')
        if not self.live:
            state, image = self.observe()
            LOG.info('Dry-run: layar=%s; input perangkat OFF', state)
            if state == 'home_scout':
                loot = self.reader(image, self.config['screens'][state])
                LOG.info('Loot=%s; memenuhi filter=%s', loot, meets_minimum(loot, self.config.get('minimum', {})))
            return 0
        completed = 0
        villages = ('home', 'builder') if mode == 'both' else (mode,)
        for _ in range(cycles):
            for village in villages:
                self.navigate(village)
                if village == 'home':
                    self.home_attack(searches)
                else:
                    self.builder_attack()
                completed += 1
                self.progress += 1
                LOG.info('Serangan selesai: %s (%s)', village, completed)
        return completed


def load_config(path: Path) -> dict:
    try:
        config = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(config, dict):
            raise BotError('Config harus JSON object')
        return config
    except (OSError, ValueError) as exc:
        raise BotError('Config tidak terbaca; jalankan kalibrasi terlebih dahulu') from exc


def main() -> int:
    parser = argparse.ArgumentParser(description='CoC LDPlayer: default dry-run tanpa tap')
    parser.add_argument('--config', type=Path, default=Path('config.json'))
    parser.add_argument('--mode', choices=('home', 'builder', 'both'), default='both')
    parser.add_argument('--cycles', type=int, default=1)
    parser.add_argument('--searches', type=int, default=0,
                        help='Batas pencarian lawan desa asal; 0 = tanpa batas; di batas itu lawan yang tampil diserang')
    parser.add_argument('--until-full', action='store_true',
                        help='Serang desa yang emas/elixirnya belum penuh; berhenti saat kedua desa penuh')
    parser.add_argument('--builder', metavar='RESOURCES',
                        help='--until-full desa malam saja sampai penuh: gold,elixir,cart (pilih sebagian) atau all')
    parser.add_argument('--home', metavar='RESOURCES',
                        help='--until-full desa asal saja sampai penuh: gold,elixir,dark (pilih sebagian) atau all')
    parser.add_argument('--first', choices=('builder', 'home'), default='builder',
                        help='Desa yang dikerjakan dulu bila dua desa dipilih')
    parser.add_argument('--walls', choices=('home', 'builder', 'both'),
                        help='Habiskan gold+elixir yang ada untuk upgrade tembok, farming sampai penuh, ulangi '
                             'sampai semua tembok maksimal; tidak pernah memakai gem')
    parser.add_argument('--max-recoveries', type=int, default=MAX_RECOVERIES,
                        help='--until-full/--walls: batas pemulihan (crash, DC, layar macet) tanpa kemajuan; dihitung '
                             'ulang setelah serangan selesai atau tembok di-upgrade')
    parser.add_argument('--hours', type=int, default=0,
                        help='--until-full/--walls: berhenti setelah sekian jam (bisa digabung --minutes); serangan '
                             'atau upgrade yang sedang jalan diselesaikan dulu, lalu berhenti di desa')
    parser.add_argument('--minutes', type=int, default=0, help='Seperti --hours, dalam menit')
    parser.add_argument('--max-upgrades', type=int, default=0,
                        help='--walls: berhenti di desa setelah sekian tembok di-upgrade; 0 = tanpa batas')
    parser.add_argument('--live', action='store_true', help='Izinkan input akun; risiko ban dan resource terpakai')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    try:
        # Check every number before anything touches the emulator.
        integer(args.cycles, 1, 100, 'cycles')
        integer(args.searches, 0, 1_000_000, 'searches')
        integer(args.max_recoveries, 0, 1000, 'max_recoveries')
        integer(args.hours, 0, 1000, 'hours')
        integer(args.minutes, 0, 60_000, 'minutes')
        integer(args.max_upgrades, 0, 100_000, 'max_upgrades')
        until_full = args.until_full or args.builder is not None or args.home is not None
        minutes = args.hours * 60 + args.minutes
        if minutes and not (args.walls or (until_full and args.live)):
            raise BotError('--hours/--minutes hanya untuk --until-full/--builder/--home/--walls dengan --live')
        if args.max_upgrades and not args.walls:
            raise BotError('--max-upgrades hanya untuk --walls')
        config = load_config(args.config)
        if args.walls and until_full:
            raise BotError('--walls tidak bisa digabung dengan --until-full/--builder/--home')
        walls = wall_order(args.walls, args.first) if args.walls else ()
        goals = parse_goals(config, args.builder, args.home, args.first) if until_full else None
        villages = walls or tuple(goals or ())
        mode = (villages[0] if len(villages) == 1 else 'both') if villages else args.mode
        validate_config(config, args.config.parent, mode)
        if walls:
            if not args.live:
                raise BotError('--walls butuh --live')
            validate_walls(config, args.config.parent, walls)
        adb = executable(config.get('adb'), 'ADB')
        # Loot is read with Tesseract only when no digit templates are configured.
        if mode != 'builder' and any(config.get('minimum', {}).values()) and not config.get('digits'):
            executable(config.get('tesseract'), 'Tesseract')
        device = ADB(adb, config['serial'], config['resolution'], live=args.live,
                     touch=config.get('touch_device'))
        emulator = Emulator(executable(config['ldconsole'], 'ldconsole'), config.get('ld_index', 0))             if config.get('ldconsole') else None
        runner = Bot(config, args.config.parent, device, live=args.live, emulator=emulator, goals=goals, walls=walls,
                     minutes=minutes, max_upgrades=args.max_upgrades)
        if walls:
            runner.run_until_full_resilient(args.searches, args.max_recoveries, job=runner.run_walls)
        elif until_full and args.live:
            runner.run_until_full_resilient(args.searches, args.max_recoveries)
        else:
            runner.run(args.mode, args.cycles, args.searches)
        return 0
    except KeyboardInterrupt:
        LOG.warning('Dihentikan; tidak ada input lanjutan')
        return 130
    except GemSpent as exc:
        LOG.error('BERHENTI DARURAT: %s', exc)
        return 3
    except OtherDevice as exc:
        LOG.warning('%s', exc)
        return 4
    except UpdateRequired as exc:
        LOG.error('Berhenti: %s', exc)
        return 5
    except LimitReached as exc:
        LOG.info('%s', exc)
        return 0
    except (BotError, KeyError, TypeError, cv2.error) as exc:
        LOG.error('Berhenti: %s', exc)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
