"""Calibrated CoC screen automation. Dry-run is the default."""

import argparse
import base64
import csv
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
SCREEN_NAMES = (
    'home', 'home_menu', 'home_army', 'home_scout', 'home_battle', 'home_result',
    'builder', 'builder_menu', 'builder_scout', 'builder_battle',
    'builder_stage2', 'builder_battle2', 'builder_result',
)
ACTIONS = {
    'home': ('attack', 'switch'), 'builder': ('attack', 'switch', 'cart'),
    'home_menu': ('find',), 'builder_menu': ('find',), 'home_army': ('attack',),
    'home_scout': ('next',), 'home_result': ('return',),
    'builder_result': ('return',), 'star_bonus': ('ok',), 'builder_star_bonus': ('ok',),
    'builder_cart': ('claim', 'close'),
}
DEPLOY_SCREENS = ('home_scout', 'builder_scout', 'builder_stage2')
OCR_MIN_VALUE, OCR_MAX_SATURATION, OCR_MIN_HEIGHT_RATIO = 200, 70, 0.6
OCR_ALIGN_RATIO = 0.2
OCR_MIN_CONFIDENCE = 50
OCR_SCALES = (3, 4, 5)
DIGIT_HEIGHT, DIGIT_WIDTH = 24, 20
DIGIT_MIN_SCORE, DIGIT_MIN_MARGIN = 0.75, 0.15
FRACTION_MIN_SCORE = 0.65
TARGET_MAX_SCORE = 0.2
ANCHOR_MAX_MARGIN = 40
TARGET_MAX_OFFSET = 200
TARGET_MIN_SCALE, TARGET_MAX_SCALE = 0.5, 2.0
PAN_DURATION_MS = 300
PAN_SETTLE_SECONDS = 2
PAN_MAX_ATTEMPTS = 4
VILLAGES = ('home', 'builder')
TOUCH_DEVICE = re.compile(r'/dev/input/event\d{1,2}')
PINCH_STEPS = 10
PINCH_STEP_SECONDS = 0.02
ZOOM_SETTLE_SECONDS = 1.5
STILL_DIFF = 1.5
CAPTURE_RETRIES = 2
ZOOM_MAX_PINCHES = 6
ZOOM_CLEAR_ATTEMPTS = 2
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
POPUPS = ('star_bonus', 'builder_star_bonus')
OPTIONAL_SCREENS = ('builder_cart',)
CART_CLAIM_SECONDS, CART_OPEN_SECONDS = 2, 5
TOOLTIP_SHOW_SECONDS, TOOLTIP_CLEAR_SECONDS = 3, 4
STAGE_SETTLE_SECONDS = 4
LOG = logging.getLogger(__name__)


class BotError(RuntimeError):
    """Stop without attempting recovery input."""


class UnknownScreen(BotError):
    """Frame matches no calibrated screen, e.g. a camera transition."""


class TargetMissing(BotError):
    """Target icon not visible or unsafe to tap; the camera may need another pan."""


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
        if name not in SCREEN_NAMES + ('loading',) + POPUPS + OPTIONAL_SCREENS or not isinstance(screen, dict):
            raise BotError('Nama/format layar tidak valid')
        anchors = screen.get('anchors', [])
        if len(anchors) < 2:
            raise BotError(f'{name}: perlu minimal dua anchor berbeda')
        boxes = [rectangle(a['roi'], resolution) for a in anchors]
        for i, (x, y, w, h) in enumerate(boxes):
            for xx, yy, ww, hh in boxes[:i]:
                if x < xx + ww and xx < x + w and y < yy + hh and yy < y + h:
                    raise BotError(f'{name}: anchor harus berbeda dan tidak overlap')
        for anchor in anchors:
            roi = rectangle(anchor['roi'], resolution)
            template = cv2.imread(str(asset_path(root, anchor['file'])))
            if template is None or list(template.shape[:2]) != [roi[3], roi[2]]:
                raise BotError(f'{name}: template hilang/ukuran salah')
            if float(template.std()) < 5:
                raise BotError(f'{name}: anchor terlalu polos')
            integer(anchor.get('margin', 0), 0, ANCHOR_MAX_MARGIN, 'anchor margin')
        overrides = screen.get('overrides', [])
        if not isinstance(overrides, list) or set(overrides) - set(SCREEN_NAMES) or name in overrides:
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
        required = [a for a in allowed if (a != 'switch' or mode == 'both') and a != 'cart' and a not in targets]
        for action in required:
            point(screen.get('actions', {}).get(action), resolution, action)
        if name in DEPLOY_SCREENS:
            point(screen.get('deploy'), resolution, 'deploy')
            troops = screen.get('troops', [])
            if not troops or len(troops) > 20:
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
    if config.get('cart_text'):
        rectangle(config['cart_text'], resolution)
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


def load_digit_templates(root: Path, folder: str) -> dict:
    templates = {}
    for digit in '0123456789':
        image = cv2.imread(str(asset_path(root, f'{folder}/{digit}.png')), cv2.IMREAD_GRAYSCALE)
        if image is None or image.shape != (DIGIT_HEIGHT, DIGIT_WIDTH):
            raise BotError(f'Template angka {digit} hilang/ukuran salah')
        templates[digit] = image.astype(np.float32) / 255
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
            raise BotError('ADB gagal/disconnect; periksa instance dan serial') from exc

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


class Detector:
    def __init__(self, config: dict, root: Path) -> None:
        self.resolution = config['resolution']
        self.templates = {
            name: [(a['roi'], cv2.imread(str(asset_path(root, a['file']))), a.get('margin', 0))
                   for a in screen['anchors']]
            for name, screen in config['screens'].items()
        }
        self.overrides = {name: set(screen.get('overrides', [])) for name, screen in config['screens'].items()}

    def score(self, image: np.ndarray, roi: list, template: np.ndarray, margin: int) -> float:
        x, y, w, h = roi
        width, height = self.resolution
        left, top = max(0, x - margin), max(0, y - margin)
        region = image[top:min(height, y + h + margin), left:min(width, x + w + margin)]
        return float(cv2.matchTemplate(region, template, cv2.TM_SQDIFF_NORMED).min())

    def detect(self, image: np.ndarray) -> str:
        if list(image.shape[1::-1]) != self.resolution:
            raise BotError('Resolusi layar berbeda dari kalibrasi')
        matches = []
        for name, anchors in self.templates.items():
            scores = [self.score(image, roi, template, margin) for roi, template, margin in anchors]
            if all(math.isfinite(score) and score <= 0.02 for score in scores):
                matches.append(name)
        if not matches:
            raise UnknownScreen('Layar tidak dikenal; tidak ada input dikirim')
        overridden = set().union(*(self.overrides.get(name, set()) for name in matches))
        matches = [name for name in matches if name not in overridden]
        if len(matches) != 1:
            raise BotError('Layar ambigu; tidak ada input dikirim')
        return matches[0]


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


def glyphs(image: np.ndarray) -> list:
    """Loot digits left to right, each scaled to a fixed height with aspect kept."""
    text = (digit_mask(image) == 0).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(text, connectivity=8)
    result = []
    for index in sorted(range(1, count), key=lambda i: stats[i, cv2.CC_STAT_LEFT]):
        x, y, w, h = stats[index, :4]
        glyph = (labels[y:y + h, x:x + w] == index).astype(np.float32)
        width = max(1, min(DIGIT_WIDTH, round(w * DIGIT_HEIGHT / h)))
        canvas = np.zeros((DIGIT_HEIGHT, DIGIT_WIDTH), np.float32)
        left = (DIGIT_WIDTH - width) // 2
        canvas[:, left:left + width] = cv2.resize(glyph, (width, DIGIT_HEIGHT), interpolation=cv2.INTER_AREA)
        result.append(canvas)
    return result


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


def card_empty(image: np.ndarray, slot: list) -> bool:
    """A used-up troop/spell card is rendered fully grey (saturation ~0)."""
    x, y = slot
    height, width = image.shape[:2]
    card = image[max(0, y - CARD_ABOVE):min(height, y + CARD_BELOW),
                 max(0, x - CARD_HALF_WIDTH):min(width, x + CARD_HALF_WIDTH)]
    return float(cv2.cvtColor(card, cv2.COLOR_BGR2HSV)[..., 1].mean()) < CARD_EMPTY_SATURATION


def classify(glyph: np.ndarray, templates: dict, min_score: float = DIGIT_MIN_SCORE) -> str | None:
    """Digit for a glyph, or None when the best match is weak or too close to the second best."""
    scores = sorted(((float(cv2.matchTemplate(glyph, template, cv2.TM_CCOEFF_NORMED)[0, 0]), digit)
                     for digit, template in templates.items()), reverse=True)
    (best, digit), (second, _) = scores[0], scores[1]
    if not math.isfinite(best) or best < min_score or best - second < DIGIT_MIN_MARGIN:
        return None
    return digit


def read_number(image: np.ndarray, templates: dict) -> int:
    """Template-match each glyph; a weak or close second-best match stops the bot."""
    found = glyphs(image)
    if not 1 <= len(found) <= 10:
        raise BotError('Jumlah angka loot tidak wajar; hentikan tanpa input')
    digits = [classify(glyph, templates) for glyph in found]
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
                 reader: Callable | None = None, timeout: float = 240, poll: float = 0.05) -> None:
        self.config, self.device, self.live, self.root = config, device, live, root
        self.detector = Detector(config, root)
        self.timeout, self.poll = timeout, poll
        self.reader = reader or self.read_loot
        self.cart_full = False
        self.capacity: dict = {}

    def observe(self) -> tuple[str, np.ndarray]:
        image = self.device.capture()
        return self.detector.detect(image), image

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
        while time.monotonic() < deadline:
            try:
                state, _ = self.observe()
            except UnknownScreen:
                time.sleep(self.poll)
                continue
            if state in targets:
                return state
            if state in POPUPS:
                self.action(state, 'ok')
                continue
            if state == 'builder_cart' and state not in targets | allowed:
                self.action(state, 'close')
                continue
            if state not in allowed | {'loading'}:
                raise BotError(f'Layar tidak diharapkan saat menunggu: {state}')
            time.sleep(self.poll)
        raise BotError('Timeout; tidak mencoba input pemulihan')

    def guarded_tap(self, states: set, location: list | Callable, terminal: set | None = None) -> str:
        state, image = self.observe()
        if terminal and state in terminal:
            return state
        if state not in states:
            raise BotError(f'Aksi dibatalkan: layar berubah menjadi {state}')
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
        observed, _ = self.observe()
        if observed != state:
            raise BotError(f'Zoom dibatalkan: layar {observed}')
        if not self.live:
            return
        for _ in range(zoom['pinches']):
            self.pinch_once(zoom)
        selection = zoom.get('selection')
        if not selection:
            return
        template = cv2.imread(str(asset_path(self.root, selection['file'])))
        for attempt in range(ZOOM_CLEAR_ATTEMPTS + 1):
            _, image = self.observe()
            try:
                find_target(image, template, selection['max_score'])
            except TargetMissing:
                return
            if attempt == ZOOM_CLEAR_ATTEMPTS:
                raise BotError('Bangunan tetap terpilih setelah zoom; tidak ada input lanjutan')
            self.pinch_once(zoom)

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
        for troop in screen['troops']:
            if troop.get('wait') and self.live:
                time.sleep(troop['wait'])
                state = self.checked_state(allowed, terminal)[0]
                if state in terminal:
                    return state
            state = self.deploy_card(screen, troop, allowed, terminal)
            if state in terminal:
                return state
        return None

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
            elif state != 'home_scout':
                raise BotError('Layar tidak diharapkan saat mencari lawan baru')
            else:
                delta = cv2.absdiff(previous, image)
                changed = float(np.mean(np.max(delta, axis=2) > 20)) > 0.02
                if changed and stable is not None and float(cv2.absdiff(stable, image).mean()) < 2:
                    return state
                stable = image if changed else None
            time.sleep(self.poll)
        raise BotError('Lawan belum berubah/stabil; hentikan pencarian')

    def home_attack(self, searches: int) -> None:
        self.action('home', 'attack')
        self.wait({'home_menu'}, {'home'}, 30)
        self.action('home_menu', 'find')
        self.wait({'home_army'}, {'home_menu'}, 30)
        self.action('home_army', 'attack')
        self.wait({'home_scout'}, {'home_army'}, 60)
        attempts = itertools.count() if searches == 0 else range(searches)
        for attempt in attempts:
            state, image = self.observe()
            if state != 'home_scout':
                raise BotError('Scout berubah sebelum OCR')
            loot = self.reader(image, self.config['screens']['home_scout'])
            LOG.info('Loot terbaca: %s', loot)
            if meets_minimum(loot, self.config.get('minimum', {})):
                self.deploy('home_scout', {'home_scout', 'home_battle'})
                self.wait({'home_result'}, {'home_scout', 'home_battle'})
                self.action('home_result', 'return')
                self.wait({'home'}, {'home_result'}, 30)
                return
            if searches == 0 or attempt + 1 < searches:
                self.action('home_scout', 'next')
                self.wait_new_scout(image)
        raise BotError('Batas pencarian tercapai; tidak ada serangan')

    def collect_cart(self) -> None:
        """Builder elixir comes only from the Elixir Cart: open it, claim, close. Skip if not visible."""
        target = self.config['screens']['builder'].get('targets', {}).get('cart')
        if not target or 'builder_cart' not in self.config['screens']:
            return
        try:
            self.action('builder', 'cart')
        except TargetMissing:
            LOG.warning('Gerobak Eliksir tidak terlihat; lewati klaim')
            return
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
        self.action('builder', 'attack')
        self.wait({'builder_menu'}, {'builder'}, 30)
        self.action('builder_menu', 'find')
        self.wait({'builder_scout'}, {'builder_menu'}, 60)
        self.deploy('builder_scout', {'builder_scout', 'builder_battle'})
        stage2 = {'builder_stage2', 'builder_battle2'}
        state = self.wait({'builder_stage2', 'builder_result'},
                          {'builder_scout', 'builder_battle', 'builder_battle2'})
        if state == 'builder_stage2':
            self.wait_still(STAGE_SETTLE_SECONDS if self.poll else 0)
            self.deploy('builder_stage2', stage2)
            self.wait({'builder_result'}, stage2)
        self.action('builder_result', 'return')
        self.wait({'builder'}, {'builder_result'}, 30)

    def storage_max(self, village: str, key: str, spec: dict) -> int:
        """Tap the storage bar once per session and read 'Maks' from its tooltip."""
        cached = self.capacity.get((village, key))
        if cached:
            return cached
        if self.live:
            self.guarded_tap({village}, spec['tap'])
        maximum = self.read_until(lambda image: read_number(crop(image, spec['max']), self.templates()),
                                  TOOLTIP_SHOW_SECONDS)
        if maximum <= 0:
            raise BotError('Kapasitas gudang tidak terbaca')
        self.capacity[(village, key)] = maximum
        if self.live:
            self.guarded_tap({village}, spec['tap'])
        deadline = time.monotonic() + (TOOLTIP_CLEAR_SECONDS if self.poll else 0)
        while time.monotonic() < deadline:
            try:
                read_number(crop(self.device.capture(), spec['max']), self.templates())
            except BotError:
                break
        else:
            if self.poll:
                raise BotError('Tooltip gudang tidak hilang; tidak ada input lanjutan')
        return maximum

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

    def village_full(self, village: str, image: np.ndarray) -> bool:
        capacity = self.config.get('capacity', {}).get(village)
        if capacity:
            amounts = {key: read_number(crop(image, spec['bar']), self.templates()) for key, spec in capacity.items()}
            maxima = {key: self.storage_max(village, key, spec) for key, spec in capacity.items()}
            LOG.info('Gudang %s: %s', village, {key: f'{amounts[key]:,}/{maxima[key]:,}' for key in capacity})
            return all(amounts[key] >= maxima[key] for key in capacity)
        bars = self.config.get('storage', {}).get(village, {})
        fills = {key: storage_fill(image, bar) for key, bar in bars.items()}
        LOG.info('Isi gudang %s: %s', village, {key: f'{value:.0%}' for key, value in fills.items()})
        return bool(fills) and all(value >= FULL_RATIO for value in fills.values())

    def builder_done(self, image: np.ndarray) -> bool:
        """Builder base is done only when gold, elixir and the Elixir Cart are all full."""
        storage_full = self.village_full('builder', image)
        if not (self.config.get('cart_text') or self.config.get('cart_bar')):
            return storage_full
        return storage_full and self.cart_full

    def run_until_full(self, searches: int) -> int:
        """Builder base first: attack until gold and elixir are full, then home; stop when both are."""
        integer(searches, 0, 1_000_000, 'searches')
        full, attacks = {}, 0
        self.wait(set(VILLAGES), set(), 30)
        village = 'builder'
        while True:
            self.navigate(village)
            if village == 'builder':
                self.collect_cart()
            _, image = self.observe()
            full[village] = self.builder_done(image) if village == 'builder' else self.village_full(village, image)
            if full[village]:
                other = 'builder' if village == 'home' else 'home'
                if full.get(other):
                    LOG.info('Gudang kedua desa penuh; selesai setelah %s serangan', attacks)
                    return attacks
                village = other
                continue
            if village == 'home':
                self.home_attack(searches)
            else:
                self.builder_attack(collect=False)
            attacks += 1
            LOG.info('Serangan selesai: %s (%s)', village, attacks)

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
    parser.add_argument('--searches', type=int, default=0, help='Batas pencarian lawan desa asal; 0 = tanpa batas')
    parser.add_argument('--until-full', action='store_true',
                        help='Serang desa yang emas/elixirnya belum penuh; berhenti saat kedua desa penuh')
    parser.add_argument('--live', action='store_true', help='Izinkan input akun; risiko ban dan resource terpakai')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    try:
        config = load_config(args.config)
        validate_config(config, args.config.parent, args.mode)
        adb = executable(config.get('adb'), 'ADB')
        if args.mode != 'builder' and any(config.get('minimum', {}).values()):
            executable(config.get('tesseract'), 'Tesseract')
        device = ADB(adb, config['serial'], config['resolution'], live=args.live,
                     touch=config.get('touch_device'))
        runner = Bot(config, args.config.parent, device, live=args.live)
        if args.until_full and args.live:
            runner.run_until_full(args.searches)
        else:
            runner.run(args.mode, args.cycles, args.searches)
        return 0
    except KeyboardInterrupt:
        LOG.warning('Dihentikan; tidak ada input lanjutan')
        return 130
    except (BotError, KeyError, TypeError, cv2.error) as exc:
        LOG.error('Berhenti: %s', exc)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
