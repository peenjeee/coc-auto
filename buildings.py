"""The game's builder list ("Buildings & Heroes", "Bangunan dan Pahlawan") read top to bottom: the name, count, level
badge, time and cost of every row, named from buildings.json. The list is only opened, swiped and closed; no row,
upgrade button or confirm is ever tapped, so nothing is ever bought."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import difflib
from functools import lru_cache
import json
from pathlib import Path
import re
import subprocess

import cv2
import numpy as np

import bot

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


def list_shift(previous: np.ndarray, current: np.ndarray, region: list) -> int:
    """How far the list moved up between two frames: where the previous frame's bottom strip shows in the next."""
    _, y, _, h = region
    bottom = y + h - SHIFT_MARGIN
    left, right = PANEL_COLUMNS
    strip = previous[bottom - SHIFT_STRIP:bottom, left:right]
    scores = cv2.matchTemplate(current[y:y + h, left:right], strip, cv2.TM_SQDIFF_NORMED)[:, 0]
    found = int(np.argmin(scores))
    others = np.delete(scores, range(max(0, found - SAME_ROW_PX), found + SAME_ROW_PX))
    if not (scores[found] <= SHIFT_MAX_SCORE and (not others.size or others.min() >= SHIFT_UNIQUE * scores[found])):
        raise bot.BotError('Daftar bangunan bergeser terlalu jauh untuk disambung; scan dibatalkan')
    return bottom - SHIFT_STRIP - y - found


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


def read_rows(frames: list, spec: dict, templates: dict, program: str) -> list[dict]:
    """Every row of the list from frames taken top to bottom, each once: id, name, category, count, next_level,
    time, cost, currency and section."""
    region, digits, units = spec['row']['region'], templates['digits'], templates['units']
    offset, kept = 0, []
    for index, image in enumerate(frames):
        if index:
            offset += list_shift(frames[index - 1], image, region)
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
    for (kind, _, y, image), text in zip(ordered, texts):
        if kind == 'title':
            section = title_section(text, titles)
            titles += 1
        elif kind == 'bar':
            rows.append({**named(text), 'next_level': None, 'time': read_time(band(image, BAR_TIME, y), digits, units),
                         'cost': None, 'currency': None, 'section': 'in_progress'})
        else:
            cost = bot.row_price(image, spec['row']['price'], y + NAME_CENTRE, digits)
            rows.append({**named(text),
                         'next_level': read_badge(band(image, BADGE_BAND, y), templates['badge']),
                         'time': read_time(band(image, TIME_BAND, y), digits, units),
                         'cost': None if np.isinf(cost) else int(cost), 'currency': currency(band(image, COIN_BAND, y)),
                         'section': section or 'other'})
    return rows


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
        frames = [runner.scroll_list(spec, spec['scroll'][::-1], image)]
        for _ in range(bot.WALL_SCROLL_LIMIT):
            moved = runner.scroll_list(spec, spec['scroll'], frames[-1], once=True)
            if moved is None:
                break
            frames.append(moved)
    finally:
        runner.close_menus()
    return read_rows(frames, spec, templates, program)
