"""The game's builder list ("Buildings & Heroes", "Bangunan dan Pahlawan") read top to bottom: the name, count, level
badge, time and cost of every row, named from buildings.json. A scan only opens, swipes and closes the list.
--buildings (run) upgrades the buildings picked in the dashboard from it, paying only gold, elixir or dark elixir at
the price the list showed."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import difflib
from functools import lru_cache
import json
import logging
from pathlib import Path
import re
import subprocess
import time

import cv2
import numpy as np

import bot
import messages

LOG = logging.getLogger(__name__)
CATALOG = Path(__file__).with_name('buildings.json')
UNITS, BADGE_DIGITS = 'calibration/units', 'calibration/badge'
SECTIONS = ('in_progress', 'suggested', 'other')
# A card's right edge is plain card brown, brighter than the gaps, titles and running bars between cards; its text
# sits at fixed offsets ([x, dy, w, h] at 1280x720) from where that brightness starts.
PANEL_COLUMNS, EDGE_COLUMNS, EDGE_MIN_RED, CARD_MIN_HEIGHT = (944, 1276), (1270, 1275), 135, 36
NAME_BAND, TIME_BAND = (994, 1, 198, 20), (1170, 1, 84, 18)
COIN_BAND, BADGE_BAND = (1252, 20, 22, 20), (944, 25, 32, 18)
# The wall rows' calibrated price band is placed from the centre of the name line.
NAME_CENTRE = 11
# A running upgrade is a bar that is green from its left edge however little is done, its name and timer on it.
BAR_COLUMNS, BAR_HEIGHT, BAR_NAME, BAR_TIME = (946, 947), (24, 36), (952, -10, 220, 20), (1170, -9, 84, 18)
# "Suggested upgrades" / "Other upgrades": pale grey text on the dark strip between two cards.
TITLE_COLUMNS, TITLE_MIN_PIXELS, TITLE_HEIGHT, TITLE_BAND = (990, 1230), 8, (6, 16), (990, -9, 240, 18)
TITLE_CLEARANCE, TITLE_TEXT = 4, (110, 80)
TITLE_WORDS = {'other': ('other', 'lain'), 'suggested': ('sugg', 'saran')}
# The gold coin, purple drop or black drop after a price, as pixels of its colour.
COIN_MIN_PIXELS, DARK_MIN_PIXELS = 40, 90
# Rows seen in two frames land within a few pixels of each other once the frames are lined up.
SAME_ROW_PX = 12
# A strip near the bottom of the previous frame is looked up in the next one to tell how far the list moved; the
# list's last pixels fade into the tabs and are left out. The list stops between whole pixels, so even the right
# spot scores a little off, but clearly better than the next best one.
SHIFT_STRIP, SHIFT_MARGIN, SHIFT_MAX_SCORE, SHIFT_UNIQUE = 60, 20, 0.05, 2
# Rows that look alike can make a second spot score close to the right one; a spot whose move matches the previous
# swipe's (each swipe moves the list about as far) is taken then. Seen live: 314 px against 86 px, scores 0.034/0.039.
SHIFT_PRIOR_PX = 60
NAME_MIN_RATIO, OCR_WORKERS = 0.75, 8
# Timer glyphs: digits are full height, unit letters ('h', 'm', 's') shorter; 'd' and 'j' reach full height too.
TIME_SCALE, TIME_MIN_HEIGHT, TIME_LETTER_HEIGHT, TIME_GAP = 2, 0.45, 0.85, 1.0
# Glyph height range at 1280x720, and the widest a glyph gets (two touching digits) for its height.
TIME_GLYPH_HEIGHT, TIME_GLYPH_ASPECT = (3, 11), 2.2
# The level badge: a black square with a light rim; its digits have their own small font (calibration/badge).
BADGE_SCALE, BADGE_DARK, BADGE_BRIGHT, BADGE_INK, BADGE_MIN_HEIGHT = 4, 70, 170, 120, 4
BADGE_WIDE, BADGE_CUT = 6, (0.2, 0.55)
OPEN_SECONDS = 30
COUNT = re.compile(r'^(.*?)\s*[xX×]\s*(\d{1,3})$')
CURRENCIES = ('gold', 'elixir', 'dark')
# Timer letters: in Indonesian 'j' (jam) is hours, 'h' (hari) days and 'd' (detik) seconds.
TIME_UNITS = {False: {'d': 86_400, 'h': 3600, 'm': 60, 's': 1}, True: {'h': 86_400, 'j': 3600, 'm': 60, 'd': 1}}
# With nothing to upgrade the run looks again when the soonest running upgrade is done, a little after, at the latest
# after the maximum.
WAIT_MAX_SECONDS, WAIT_MARGIN_SECONDS = 600, 5
MAX_TARGETS, MAX_LEVEL, MAX_KEEP_BUILDERS, NAME_MAX = 100, 200, 10, 40


@lru_cache(maxsize=1)
def catalog() -> dict:
    """Lowercased English and Indonesian name -> (id, category, name as written)."""
    names = {}
    for category, buildings in json.loads(CATALOG.read_text(encoding='utf-8')).items():
        for key, spellings in buildings.items():
            for spelling in spellings:
                names[spelling.lower()] = (key, category, spelling)
    return names


def identify(text: str) -> tuple:
    """(id, category, name) of the closest catalog name; an unknown name stays as read, with no id, under other."""
    cleaned = re.sub(r"[^a-z' -]", '', text.lower()).strip()
    found = difflib.get_close_matches(cleaned, list(catalog()), n=1, cutoff=NAME_MIN_RATIO)
    return catalog()[found[0]] if found else (None, 'other', text)


def split_count(text: str) -> tuple[str, int]:
    """'Archer Tower x 4' -> ('Archer Tower', 4); a single building has no count."""
    found = COUNT.match(text)
    return (found[1], int(found[2])) if found else (text, 1)


def load_templates(root: Path, config: dict) -> dict:
    """Glyph templates of the list: the price digits, the timer's unit letters ('h.png', 'j.png'...) and the level
    badge's own small digits, all in the digit templates' format."""
    units = {}
    for path in sorted(bot.asset_path(root, f'{UNITS}/h.png').parent.glob('*.png')):
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None or image.shape != (bot.DIGIT_HEIGHT, bot.DIGIT_WIDTH):
            raise bot.BotError(f'Template angka {path.name} hilang/ukuran salah')
        units[path.stem] = image.astype(np.float32) / 255
    return {'digits': bot.load_digit_templates(root, config['digits']), 'units': units,
            'badge': bot.load_digit_templates(root, BADGE_DIGITS)}


def ocr_line(image: np.ndarray, program: str, min_value: int = 200, max_saturation: int = 60) -> str:
    """One line of the game's outlined text; enlarged before it is cut from the background, which keeps the letter
    shapes Tesseract knows."""
    large = cv2.resize(image, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    hsv = cv2.cvtColor(large, cv2.COLOR_BGR2HSV)
    text = (hsv[..., 2] >= min_value) & (hsv[..., 1] <= max_saturation)
    page = cv2.GaussianBlur(np.where(text, 0, 255).astype(np.uint8), (3, 3), 0)
    ok, encoded = cv2.imencode('.png', cv2.copyMakeBorder(page, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255))
    if not ok:
        raise bot.BotError('Tidak bisa menyiapkan crop OCR')
    try:
        result = subprocess.run([program, 'stdin', 'stdout', '--psm', '7', '-l', 'eng'], input=encoded.tobytes(),
                                capture_output=True, check=True, timeout=15, shell=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise bot.BotError('OCR gagal; scan bangunan dibatalkan') from exc
    return ' '.join(result.stdout.decode('utf-8', 'replace').split())


def time_glyphs(image: np.ndarray) -> list[tuple[np.ndarray, bool]]:
    """The timer's glyphs ending at the band's right edge, left to right, each with whether it is full height: a
    digit, 'd' or 'j', where 'h', 'm' and 's' are shorter."""
    hsv = cv2.cvtColor(cv2.resize(image, None, fx=TIME_SCALE, fy=TIME_SCALE, interpolation=cv2.INTER_CUBIC),
                       cv2.COLOR_BGR2HSV)
    # Timers are always white; a running bar's light green and a helper's face beside it are not text.
    white = ((hsv[..., 2] >= bot.OCR_MIN_VALUE) & (hsv[..., 1] <= bot.OCR_MAX_SATURATION)).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(white, connectivity=8)
    low, high = (TIME_SCALE * size for size in TIME_GLYPH_HEIGHT)
    blobs = [index for index in range(1, count) if low <= stats[index, cv2.CC_STAT_HEIGHT] <= high
             and stats[index, cv2.CC_STAT_WIDTH] <= TIME_GLYPH_ASPECT * stats[index, cv2.CC_STAT_HEIGHT]]
    if not blobs:
        return []
    tallest = max(stats[index, cv2.CC_STAT_HEIGHT] for index in blobs)
    blobs = sorted((index for index in blobs if stats[index, cv2.CC_STAT_HEIGHT] >= TIME_MIN_HEIGHT * tallest),
                   key=lambda index: stats[index, cv2.CC_STAT_LEFT], reverse=True)
    timer, left = [], None
    for index in blobs:
        x, y, w, h = stats[index, :4]
        # A long name reaching into the band ends further left than the gap between the timer's two parts.
        if left is not None and left - (x + w) > TIME_GAP * tallest:
            break
        timer.insert(0, ((labels[y:y + h, x:x + w] == index).astype(np.float32), h >= TIME_LETTER_HEIGHT * tallest))
        left = x
    return timer


def read_time(image: np.ndarray, digits: dict, units: dict) -> str | None:
    """The timer as the game writes it ('1h 30m', '8s', '3d 12h', '2j' in Indonesian); None when there is none
    (walls) or it does not read cleanly."""
    text = ''
    for glyph, tall in time_glyphs(image):
        letter = bot.classify(bot.mask_canvas(glyph), units, bot.FRACTION_MIN_SCORE)
        if not tall:
            found = letter
        else:
            h, w = glyph.shape
            # Neighbouring digits can touch ("30").
            pieces = bot.split_glyphs(glyph, max(2, round(w / (h * bot.DIGIT_ASPECT)))) if w > bot.WIDE_RATIO * h \
                else [glyph]
            found = ''.join(bot.classify(bot.mask_canvas(piece), digits) or '?' for piece in pieces)
            if '?' in found:
                found = letter
        if found is None:
            return None
        text += found
    if not re.fullmatch(r'(\d+[a-z]){1,2}', text):
        return None
    return re.sub(r'([a-z])(?=\d)', r'\1 ', text)


def badge_glyphs(image: np.ndarray) -> list:
    """The badge's digits, left to right, from inside its black square, enlarged. They are a few pixels each and
    grey-edged: columns with bright ink tell the digits apart, and a span too wide for one digit is two touching ones,
    cut where the narrow '1' on the left ends."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    black = ((hsv[..., 2] < BADGE_DARK) & (hsv[..., 1] < 30)).astype(np.uint8)
    count, _, squares, _ = cv2.connectedComponentsWithStats(black, connectivity=4)
    if count < 2:
        return []
    x, y, w, h = squares[1 + int(np.argmax(squares[1:, cv2.CC_STAT_AREA])), :4]
    gray = cv2.cvtColor(cv2.resize(image[y:y + h, x:x + w], None, fx=BADGE_SCALE, fy=BADGE_SCALE,
                                   interpolation=cv2.INTER_CUBIC), cv2.COLOR_BGR2GRAY)
    spans = []
    for left, right in runs((gray >= BADGE_BRIGHT).any(axis=0)):
        if right - left > BADGE_WIDE * BADGE_SCALE:
            weight = (gray[:, left:right] >= BADGE_INK).sum(axis=0) + gray[:, left:right].mean(axis=0) / 255
            low, high = int((right - left) * BADGE_CUT[0]), int((right - left) * BADGE_CUT[1])
            cut = left + low + int(np.argmin(weight[low:high]))
            spans += [(left, cut), (cut, right)]
        elif right - left >= BADGE_SCALE:
            spans.append((left, right))
    glyphs = []
    for left, right in spans:
        ink = gray[:, left:right] >= BADGE_INK
        rows, columns = np.flatnonzero(ink.any(axis=1)), np.flatnonzero(ink.any(axis=0))
        if len(rows) >= BADGE_MIN_HEIGHT * BADGE_SCALE:
            glyphs.append(bot.mask_canvas(ink[rows[0]:rows[-1] + 1, columns[0]:columns[-1] + 1].astype(np.float32)))
    return glyphs


def read_badge(image: np.ndarray, templates: dict) -> int | None:
    """The level on the corner of a row's icon, the level the upgrade reaches; None when it does not read cleanly."""
    found = [bot.classify(glyph, templates) for glyph in badge_glyphs(image)]
    if not 1 <= len(found) <= 3 or None in found:
        return None
    return int(''.join(found))


def currency(image: np.ndarray) -> str | None:
    """'gold', 'elixir' or 'dark' from the coin after a price; None for a row without one."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hue, saturation, value = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    gold = int(((hue >= 18) & (hue <= 35) & (saturation > 120) & (value > 150)).sum())
    elixir = int(((hue >= 135) & (hue <= 165) & (saturation > 100) & (value > 100)).sum())
    if max(gold, elixir) >= COIN_MIN_PIXELS:
        return 'gold' if gold > elixir else 'elixir'
    return 'dark' if int((value < 70).sum()) >= DARK_MIN_PIXELS else None


def runs(flags: np.ndarray) -> list[tuple[int, int]]:
    """(start, end) of every run of True, end exclusive."""
    edges = np.flatnonzero(np.diff(np.concatenate([[0], flags.astype(np.int8), [0]])))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist()))


def items(image: np.ndarray, region: list) -> list[tuple[str, int]]:
    """(kind, y) of every row wholly in view: 'card' at its top, 'bar' (a running upgrade) and 'title' at their
    middle. Rows cut by the edge of the list show whole after a swipe."""
    _, top, _, h = region
    area = image[top:top + h]
    edge = area[:, EDGE_COLUMNS[0]:EDGE_COLUMNS[1], 2].mean(axis=1) >= EDGE_MIN_RED
    left = area[:, BAR_COLUMNS[0]:BAR_COLUMNS[1]].astype(int)
    green = ((left[..., 1] > 120) & (left[..., 1] > left[..., 2] + 50)).all(axis=1)
    cards, bars = runs(edge), [(start, end) for start, end in runs(green) if end - start >= BAR_HEIGHT[0]]
    hsv = cv2.cvtColor(area[:, TITLE_COLUMNS[0]:TITLE_COLUMNS[1]], cv2.COLOR_BGR2HSV)
    text = ((hsv[..., 1] <= 80) & (hsv[..., 2] >= 110) & (hsv[..., 2] <= 230)).sum(axis=1) >= TITLE_MIN_PIXELS
    # Cards and bars carry text of their own; a title is text in the strip between them.
    for start, end in cards + bars:
        text[max(0, start - TITLE_CLEARANCE):end + TITLE_CLEARANCE] = False

    def whole(spans: list, low: int, high: int) -> list:
        return [(start, end) for start, end in spans if low <= end - start <= high and start > 0 and end < h]
    found = [('card', top + start) for start, _ in whole(cards, CARD_MIN_HEIGHT, h)]
    found += [('bar', top + (start + end) // 2) for start, end in whole(bars, *BAR_HEIGHT)]
    found += [('title', top + (start + end) // 2) for start, end in whole(runs(text), *TITLE_HEIGHT)]
    return sorted(found, key=lambda item: item[1])


def list_shift(previous: np.ndarray, current: np.ndarray, region: list, expected: int | None = None) -> int:
    """How far the list moved up between two frames: where the previous frame's bottom strip shows in the next.
    expected: the previous swipe's move, which settles a spot that is not clearly the best on its own."""
    _, y, _, h = region
    bottom = y + h - SHIFT_MARGIN
    left, right = PANEL_COLUMNS
    strip = previous[bottom - SHIFT_STRIP:bottom, left:right]
    scores = cv2.matchTemplate(current[y:y + h, left:right], strip, cv2.TM_SQDIFF_NORMED)[:, 0]
    found = int(np.argmin(scores))
    others = np.delete(scores, range(max(0, found - SAME_ROW_PX), found + SAME_ROW_PX))
    moved = bottom - SHIFT_STRIP - y - found
    unique = not others.size or others.min() >= SHIFT_UNIQUE * scores[found]
    steady = expected is not None and abs(moved - expected) <= SHIFT_PRIOR_PX
    if not (scores[found] <= SHIFT_MAX_SCORE and (unique or steady)):
        # A stall to the upgrade run, which tidies the screen and reads the list again.
        raise bot.WaitTimeout('Daftar bangunan bergeser terlalu jauh untuk disambung; scan dibatalkan')
    return moved


def band(image: np.ndarray, spec: tuple, y: int) -> np.ndarray:
    x, dy, w, h = spec
    return bot.crop(image, [x, y + dy, w, h])


def title_section(text: str, seen: int) -> str:
    """The section a title opens, by its words in either language; otherwise by order, suggestions first."""
    lowered = text.lower()
    for section, words in TITLE_WORDS.items():
        if any(word in lowered for word in words):
            return section
    return SECTIONS[1 + min(seen, 1)]


def named(text: str) -> dict:
    words, count = split_count(text)
    key, category, name = identify(words)
    return {'id': key, 'name': name, 'category': category, 'count': count}


def offsets(frames: list, region: list) -> list[int]:
    """How far down the list each frame shows, the first one at 0."""
    found = [0]
    for previous, image in zip(frames, frames[1:]):
        expected = found[-1] - found[-2] if len(found) > 1 else None
        found.append(found[-1] + list_shift(previous, image, region, expected))
    return found


def read_rows(frames: list, spec: dict, templates: dict, program: str) -> list[dict]:
    """Every row of the list from frames taken top to bottom, each once: id, name, category, count, next_level,
    time, cost, currency and section."""
    return [row for row, _ in placed_rows(frames, spec, templates, program)]


def placed_rows(frames: list, spec: dict, templates: dict, program: str) -> list[tuple[dict, int]]:
    """read_rows with each row's place down the list: its card's top as the first frame would show it."""
    region, digits, units = spec['row']['region'], templates['digits'], templates['units']
    kept = []
    for offset, image in zip(offsets(frames, region), frames):
        for kind, y in items(image, region):
            if not any(kind == other and abs(offset + y - place) <= SAME_ROW_PX for other, place, _, _ in kept):
                kept.append((kind, offset + y, y, image))
    ordered = sorted(kept, key=lambda item: item[1])
    jobs = [(band(image, TITLE_BAND, y), program, *TITLE_TEXT) if kind == 'title' else
            (band(image, BAR_NAME if kind == 'bar' else NAME_BAND, y), program) for kind, _, y, image in ordered]
    # Tesseract takes about half a second to start, so the lines are read side by side.
    with ThreadPoolExecutor(OCR_WORKERS) as pool:
        texts = list(pool.map(lambda job: ocr_line(*job), jobs))
    rows, section, titles = [], None, 0
    for (kind, place, y, image), text in zip(ordered, texts):
        if kind == 'title':
            section = title_section(text, titles)
            titles += 1
        elif kind == 'bar':
            rows.append(({**named(text), 'next_level': None,
                          'time': read_time(band(image, BAR_TIME, y), digits, units), 'cost': None, 'currency': None,
                          'section': 'in_progress'}, place))
        else:
            cost = bot.row_price(image, spec['row']['price'], y + NAME_CENTRE, digits)
            rows.append(({**named(text),
                          'next_level': read_badge(band(image, BADGE_BAND, y), templates['badge']),
                          'time': read_time(band(image, TIME_BAND, y), digits, units),
                          'cost': None if np.isinf(cost) else int(cost),
                          'currency': currency(band(image, COIN_BAND, y)), 'section': section or 'other'}, place))
    return rows


def list_frames(runner: bot.Bot, spec: dict, image: np.ndarray) -> list:
    """Frames of the open builder list from its top to its end."""
    frames = [runner.scroll_list(spec, spec['scroll'][::-1], image)]
    for _ in range(bot.WALL_SCROLL_LIMIT):
        moved = runner.scroll_list(spec, spec['scroll'], frames[-1], once=True)
        if moved is None:
            break
        frames.append(moved)
    return frames


def scan(runner: bot.Bot) -> list[dict]:
    """Open the Home Village builder list, read it from its top to its end, and close it again."""
    spec = runner.config['walls']['home']
    program = bot.executable(runner.config.get('tesseract'), 'Tesseract')
    templates = load_templates(runner.root, runner.config)
    runner.check_touch()
    state = runner.wait({'home', 'builder', 'build_list'}, set(), OPEN_SECONDS)
    if state == 'build_list':
        # Whichever village is behind a list left open is out of sight; closing it shows which.
        runner.close_menus()
        state = runner.wait({'home', 'builder'}, set(), OPEN_SECONDS)
    if state != 'home':
        raise bot.BotError('Scan bangunan hanya di Desa Utama; pindah ke Desa Utama dulu')
    image = runner.open_build_list('home', spec)
    try:
        frames = list_frames(runner, spec, image)
    finally:
        runner.close_menus()
    indonesian = runner.list_indonesian(frames[0])
    # A running upgrade's time left in seconds, so the dashboard can count it down until the next scan.
    return [{**row, 'seconds': seconds(row['time'], indonesian)} if row['section'] == 'in_progress' else row
            for row in read_rows(frames, spec, templates, program)]


def seconds(text: object, indonesian: bool) -> int | None:
    """A timer as the list writes it in seconds ('1h 23m' is 4980, '2h 12j' in Indonesian 2 days 12 hours); None
    when it was not read."""
    units = TIME_UNITS[indonesian]
    parts = re.findall(r'(\d+)([a-z])', text) if isinstance(text, str) else []
    if not parts or ' '.join(number + unit for number, unit in parts) != text or any(
            unit not in units for _, unit in parts):
        return None
    return sum(int(number) * units[unit] for number, unit in parts)


def selection(value: object) -> dict:
    """The dashboard's pick, checked: targets in priority order, each {id, name, level} (id None for a row the
    catalog does not know, matched by name; level None upgrades for as long as the building is listed), and how many
    builders to leave free. Walls have --walls."""
    if not isinstance(value, dict) or set(value) != {'targets', 'keep_builders'}:
        raise bot.BotError('Pilihan bangunan perlu targets dan keep_builders')
    keep, targets = value['keep_builders'], value['targets']
    bot.integer(keep, 0, MAX_KEEP_BUILDERS, 'keep_builders')
    if not isinstance(targets, list) or len(targets) > MAX_TARGETS:
        raise bot.BotError(f'Target bangunan harus daftar berisi 0..{MAX_TARGETS}')
    known = {key for key, _, _ in catalog().values()} - {'wall'}
    checked, seen = [], set()
    for target in targets:
        if not isinstance(target, dict) or set(target) != {'id', 'name', 'level'}:
            raise bot.BotError('Target bangunan perlu id, name dan level')
        key, name, level = target['id'], target['name'], target['level']
        if key is not None and key not in known:
            raise bot.BotError(f'Bangunan tidak dikenal: {key}')
        if not isinstance(name, str) or not name.strip() or len(name) > NAME_MAX or not name.isprintable():
            raise bot.BotError('Nama bangunan tidak valid')
        if level is not None:
            bot.integer(level, 1, MAX_LEVEL, 'level')
        mark = key or name.strip().lower()
        if mark in seen:
            raise bot.BotError(f'Bangunan dipilih dua kali: {name}')
        seen.add(mark)
        checked.append({'id': key, 'name': name.strip(), 'level': level})
    return {'targets': checked, 'keep_builders': keep}


def validate(config: dict, root: Path) -> dict:
    """--buildings: the wall calibration it shares (builder list, price and icon on the upgrade dialog's button, gem
    bars), its own (free builder counter, dark elixir icon, upgrade dialog) and a pick with at least one target."""
    bot.validate_walls(config, root, ('home',))
    upgrade = config.get('building_upgrade')
    if not isinstance(upgrade, dict) or 'building_confirm' not in config['screens']:
        raise bot.BotError('--buildings butuh kalibrasi: building_upgrade dan building_confirm')
    bot.rectangle(upgrade.get('free_builders'), config['resolution'])
    icons = upgrade.get('icons')
    if not isinstance(icons, dict) or set(icons) != {'dark'} or \
            cv2.imread(str(bot.asset_path(root, icons['dark']))) is None:
        raise bot.BotError('building_upgrade icons perlu ikon dark')
    if not set(CURRENCIES) <= set(config.get('capacity', {}).get('home', {})):
        raise bot.BotError('--buildings butuh kalibrasi: capacity home gold, elixir dan dark')
    chosen = selection(config.get('buildings', {'targets': [], 'keep_builders': 0}))
    if not chosen['targets']:
        raise bot.BotError('Belum ada bangunan dipilih; pilih di dashboard (Pengaturan perangkat) lalu Simpan')
    return chosen


def matches(row: dict, target: dict) -> bool:
    if target['id'] is not None:
        return row['id'] == target['id']
    return row['id'] is None and row['name'].lower() == target['name'].lower()


def row_key(row: dict) -> tuple:
    return row['id'] or row['name'], row['next_level'], row['cost']


def pick(placed: list, targets: list, amounts: dict, refused: set = frozenset()) -> tuple:
    """((row, place) to upgrade now or None, whether any target still has something left). The first target in
    priority order wins with a listed row that is not running, does not go past its level and costs no more than
    the resource on hand. A row going past the level means that target is reached; a running one, or one short of
    resources or not read, is left for later."""
    pending = False
    for target in targets:
        for row, place in placed:
            if not matches(row, target):
                continue
            level = target['level']
            if row['section'] != 'in_progress' and level is not None and row['next_level'] is not None \
                    and row['next_level'] > level:
                continue
            pending = True
            if (row['section'] == 'in_progress' or row_key(row) in refused or row['cost'] is None
                    or row['currency'] not in CURRENCIES or (level is not None and row['next_level'] is None)):
                continue
            if row['cost'] <= amounts[row['currency']]:
                return (row, place), True
    return None, pending


def pause_seconds(rows: list, indonesian: bool) -> int:
    """Until the soonest running upgrade is done, at most WAIT_MAX_SECONDS."""
    timers = [seconds(row['time'], indonesian) for row in rows if row['section'] == 'in_progress']
    timers = [timer for timer in timers if timer is not None]
    return min(WAIT_MAX_SECONDS, min(timers) + WAIT_MARGIN_SECONDS) if timers else WAIT_MAX_SECONDS


def dialog_spec(config: dict) -> dict:
    """The Home Village wall spec with dark elixir's icon added: the upgrade dialog is the walls' one."""
    spec = config['walls']['home']
    icons = {**spec['confirm']['icons'], **config['building_upgrade']['icons']}
    return {**spec, 'confirm': {**spec['confirm'], 'icons': icons}}


def refusal(runner: bot.Bot, image: np.ndarray, row: dict) -> str | None:
    """Why the upgrade dialog must not be confirmed; None only when its button shows the row's cost, affordable,
    beside that row's gold, elixir or dark elixir icon. A gem price never matches one of those."""
    spec = dialog_spec(runner.config)
    try:
        price = runner.confirm_price(image, spec)
    except bot.BotError:
        price = None
    if price != row['cost']:
        return messages.translate(f'harga di dialog {price}, di daftar {row["cost"]}')
    if row['currency'] not in CURRENCIES or not runner.confirm_safe(image, spec, row['currency']):
        return messages.translate(f'harga {row["currency"]} tidak putih atau ikonnya lain')
    return None


def village(runner: bot.Bot) -> tuple[int, dict]:
    """Free builders and the gold, elixir and dark elixir on hand, from the Home Village once two frames agree; an
    open builder list would push the resource bars aside."""
    templates, bars = runner.templates(), runner.config['capacity']['home']
    region = runner.config['building_upgrade']['free_builders']

    def read(image: np.ndarray) -> tuple:
        free, total = bot.read_fraction(bot.crop(image, region), templates)
        if free > total:
            raise bot.BotError('Jumlah tukang bebas meragukan')
        return free, tuple(bot.read_number(bot.crop(image, bars[key]['bar']), templates, bot.FRACTION_MIN_SCORE)
                           for key in CURRENCIES)
    runner.wait({'home'}, set(), OPEN_SECONDS)
    try:
        free, amounts = runner.read_stable(read, bot.GEM_READ_SECONDS if runner.poll else 0)
    except bot.BotError as exc:
        raise bot.WaitTimeout(f'Tukang bebas/resource desa tidak terbaca ({exc}); tidak ada upgrade') from exc
    return free, dict(zip(CURRENCIES, amounts))


def home(runner: bot.Bot, spec: dict) -> tuple[int, dict]:
    """On the Home Village with the gem count on record: free builders and resources."""
    runner.navigate('home')
    runner.find_gem_row(spec)
    runner.start_gem_watch(spec)
    return village(runner)


def find_card(runner: bot.Bot, spec: dict, frames: list, place: int, row: dict, program: str) -> int:
    """y of the row's card, swiping the list back up from its end until it shows. Its name is read again first, so
    a list that moved under the bot never gets another row tapped."""
    region = spec['row']['region']
    image, offset = frames[-1], offsets(frames, region)[-1]
    for _ in range(bot.WALL_SCROLL_LIMIT):
        for kind, y in items(image, region):
            if kind == 'card' and abs(offset + y - place) <= SAME_ROW_PX:
                seen = named(ocr_line(band(image, NAME_BAND, y), program))
                if (seen['id'], seen['name'], seen['count']) != (row['id'], row['name'], row['count']):
                    raise bot.WaitTimeout(f'Baris {row["name"]} berubah jadi {seen["name"]}; tidak ada input')
                return y
        moved = runner.scroll_list(spec, spec['scroll'][::-1], image, once=True)
        if moved is None:
            break
        offset -= list_shift(moved, image, region)
        image = moved
    raise bot.WaitTimeout(f'Baris {row["name"]} tidak ketemu lagi di daftar; tidak ada input')


def upgrade(runner: bot.Bot, spec: dict, frames: list, place: int, row: dict, program: str) -> bool:
    """Tap the row (the game opens its upgrade dialog) and confirm it only when refusal finds nothing; False when
    the dialog was closed unconfirmed."""
    y = find_card(runner, spec, frames, place, row, program)
    runner.guarded_tap({'build_list'}, [spec['row']['tap_x'], y + NAME_CENTRE])
    runner.wait({'building_confirm'}, {'build_list', 'wall_selected'}, bot.WALL_TAP_SECONDS)
    image = runner.wait_still(bot.WALL_LIST_SETTLE_SECONDS if runner.poll else 0)
    after = {'build_list', 'wall_selected', 'home'}
    problem = refusal(runner, image, row)

    def confirm(frame: np.ndarray) -> list:
        # Looked at again in the very frame the tap follows.
        again = refusal(runner, frame, row)
        if again:
            raise bot.TargetMissing(again)
        return runner.config['screens']['building_confirm']['actions']['confirm']
    if problem is None:
        try:
            runner.guarded_tap({'building_confirm'}, confirm)
        except bot.TargetMissing as exc:
            problem = str(exc)
    if problem:
        LOG.warning('Dialog upgrade %s tidak sesuai (%s); ditutup tanpa konfirmasi', row['name'], problem)
        runner.action('building_confirm', 'close')
        runner.wait(after, {'building_confirm'}, bot.WALL_TAP_SECONDS)
        return False
    # All builders busy or resources short bring up a gem offer; wait() closes it with X and stops this try.
    runner.wait(after, {'building_confirm'}, bot.WALL_TAP_SECONDS)
    runner.check_gems(spec)
    return True


def pause(runner: bot.Bot, seconds_left: int, reason: str) -> None:
    if runner.stop_at is not None:
        seconds_left = max(0, min(seconds_left, round(runner.stop_at - time.monotonic())))
    LOG.info('Menunggu %s detik: %s', seconds_left, reason)
    time.sleep(seconds_left if runner.poll else 0)


def run(runner: bot.Bot, chosen: dict) -> int:
    """--buildings: keep the builders busy on the picked Home Village buildings, the first target first, until each
    reaches its level (or, without one, leaves the list); the number upgraded. Builders beyond keep_builders are
    used only, and a cost only when the resource on hand covers it."""
    spec = runner.config['walls']['home']
    program = bot.executable(runner.config.get('tesseract'), 'Tesseract')
    templates = load_templates(runner.root, runner.config)
    targets, keep = chosen['targets'], chosen['keep_builders']
    refused, upgraded = set(), 0
    while True:
        runner.check_limits()
        runner.refresh_if_low_memory()
        free, amounts = home(runner, spec)
        frames = list_frames(runner, spec, runner.open_build_list('home', spec))
        indonesian = runner.list_indonesian(frames[0])
        placed = placed_rows(frames, spec, templates, program)
        choice, pending = pick(placed, targets, amounts, refused)
        if choice is None and not pending:
            runner.close_menus()
            LOG.info('Semua bangunan pilihan sudah mencapai target (%s upgrade); selesai', upgraded)
            return upgraded
        if choice is None or free <= keep:
            runner.close_menus()
            reason = (messages.translate(f'tukang bebas {free}, disisakan {keep}') if free <= keep
                      else messages.translate('belum ada bangunan pilihan yang terjangkau'))
            pause(runner, pause_seconds([row for row, _ in placed], indonesian), reason)
            # A dialog that did not match is tried again after the wait; meanwhile the next target went first.
            refused.clear()
            continue
        row, place = choice
        try:
            done = upgrade(runner, spec, frames, place, row, program)
        except bot.NoFreeBuilder as exc:
            LOG.warning('Upgrade %s dilewati: %s', row['name'], exc)
            done = False
        runner.close_menus()
        if not done:
            refused.add(row_key(row))
            continue
        upgraded += 1
        runner.progress += 1
        after, _ = home(runner, spec)
        if after >= free:
            LOG.warning('Tukang bebas tidak berkurang setelah upgrade %s (%s -> %s); cek game', row['name'], free,
                        after)
        LOG.info('Bangunan di-upgrade: %s ke level %s', row['name'], row['next_level'])
