"""The game's builder list ("Buildings & Heroes", "Bangunan dan Pahlawan") read top to bottom: the name, count, level
badge, time and cost of every row, named from buildings.json; its Laboratory and pet tabs, named from research.json,
read the same way. A scan only opens, swipes and closes the list.
The helpers here also serve --buildings (upgrades.run), which upgrades the rows picked in the dashboard."""
from __future__ import annotations
from .images import read_image, write_image

from .paths import RESOURCE_ROOT

from concurrent.futures import ThreadPoolExecutor
import difflib
from functools import lru_cache
import json
import logging
from pathlib import Path
import re
import subprocess

import cv2
import numpy as np

from . import bot
from . import messages

LOG = logging.getLogger(__name__)
CATALOG = (RESOURCE_ROOT / 'buildings.json')
# Lab and pet names stay out of the buildings catalog: "Raksasa" (Giant) would match "Bom Raksasa" (Giant Bomb).
RESEARCH = (RESOURCE_ROOT / 'research.json')
GEAR = (RESOURCE_ROOT / 'gear.json')
BUILDER = (RESOURCE_ROOT / 'builder.json')
STAR = (RESOURCE_ROOT / 'star.json')
# Each list: the village whose builder list has it, its tab's x along the list's bottom (y TAB_Y) and the catalog its
# rows are named from. The first tab of each village is its builders'.
LISTS = {'buildings': ('home', 978, CATALOG), 'lab': ('home', 1062, RESEARCH), 'gear': ('home', 1150, GEAR),
         'pets': ('home', 1240, RESEARCH), 'builder': ('builder', 1021, BUILDER), 'star': ('builder', 1193, STAR)}
BUILDERS_TAB = {'home': 978, 'builder': 1021}
TAB_Y = 690
# Upgrade dialogs a row opens over the list.
ROW_DIALOGS = ('building_confirm', 'gear_confirm', 'builder_upgrade_confirm')
# The dialog a row of each list opens, for --buildings.
DIALOGS = {'buildings': 'building_confirm', 'lab': 'building_confirm', 'gear': 'gear_confirm',
           'pets': 'building_confirm', 'builder': 'builder_upgrade_confirm', 'star': 'builder_upgrade_confirm'}
# A Builder Base upgrade dialog has its one green price button where its layout puts it: centred and larger for a
# building, at the bottom right for a hero or a Star Laboratory upgrade. It is found in this band ([x, y, w, h]) as
# the largest green patch; the price is white beside the resource's icon, told by colour: Builder Gold yellow,
# Builder Elixir purple (seen: 1900 and 1000 pixels of its own colour, none of the other). A button smaller than
# BUTTON_MIN_PIXELS green is no price button.
BUILDER_BUTTONS, BUTTON_MIN_PIXELS, ICON_MIN_SHARE, ICON_OTHER_SHARE = [480, 540, 660, 170], 6000, 0.03, 0.005
BUILDER_ICONS = {'gold': ((12, 28), 120, 120), 'elixir': ((125, 160), 80, 80)}
# Tesseract reads the small capitals' S and I as digits ("L.A.S.5.1").
OCR_DIGITS = str.maketrans('51', 'si')
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
# A locked row ("Terkunci"/locked) is a darker card without price, timer or badge, told in any language by the padlock
# at its right end: [x, x end) searched, best score (seen: locked rows 0.016 at most, anything else 0.11 at least) and
# the card's rows from the padlock's top. Its name is white or, for what the next level brings, green.
PADLOCK = RESOURCE_ROOT / 'calibration' / 'list-padlock.png'
LOCK_COLUMNS, LOCK_MAX_SCORE, LOCK_CARD, LOCKED_TEXT = (1215, 1280), 0.05, (-3, 39), (190, 255)
KEPT_LEFT = 936
# From a card's top to below its level badge and price.
CARD_DEPTH = 45
# A strip near the bottom of the previous frame is looked up in the next one to tell how far the list moved; the
# list's last pixels fade into the tabs and are left out. The list stops between whole pixels, so each frame draws
# its rows a little differently: a light blur evens that out, and a strip about two rows tall tells rows that look
# alike apart. Seen live over 30 swipes: the right spot scored at most 0.005, any other at least 0.013.
SHIFT_STRIP, SHIFT_MARGIN, SHIFT_MAX_SCORE, SHIFT_UNIQUE, SHIFT_BLUR = 120, 20, 0.01, 2, (7, 7)
NAME_MIN_RATIO, OCR_WORKERS = 0.75, 8
# Timer glyphs: digits are full height, unit letters ('h', 'm', 's') shorter; 'd' and 'j' reach full height too.
TIME_SCALE, TIME_MIN_HEIGHT, TIME_LETTER_HEIGHT, TIME_GAP = 2, 0.45, 0.85, 1.0
# Glyph height range at 1280x720, and the widest a glyph gets (two touching digits) for its height; a running bar's
# helper face beside its timer reaches taller than any glyph.
TIME_GLYPH_HEIGHT, TIME_GLYPH_ASPECT = (3, 10), 2.2
# The level badge: a black square with a light rim; its digits have their own small font (calibration/badge).
BADGE_SCALE, BADGE_DARK, BADGE_BRIGHT, BADGE_INK, BADGE_MIN_HEIGHT, BADGE_CORE = 4, 70, 170, 120, 4, 200
# The widest one digit's white gets, and the widest the black square is, in pixels at 1280x720 (a single digit
# measured 2.75-5, the square 13-14).
BADGE_ONE, BADGE_SQUARE = 5, 15
BADGE_WIDE, BADGE_CUT = 6, (0.2, 0.55)
OPEN_SECONDS = 30
COUNT = re.compile(r'^(.*?)\s*[xX×]\s*(\d{1,3})$')
CURRENCIES = ('gold', 'elixir', 'dark')
# Timer letters: in Indonesian 'j' (jam) is hours, 'h' (hari) days and 'd' (detik) seconds.
TIME_UNITS = {False: {'d': 86_400, 'h': 3600, 'm': 60, 's': 1}, True: {'h': 86_400, 'j': 3600, 'm': 60, 'd': 1}}
# With nothing to upgrade the run looks again when the soonest running upgrade is done, a little after; with the game
# open at the latest after the maximum (upgrades.pause).
WAIT_MAX_SECONDS, WAIT_MARGIN_SECONDS = 600, 5
MAX_TARGETS, MAX_LEVEL, MAX_KEEP_BUILDERS, NAME_MAX = 100, 200, 10, 40
GOBLIN_OFF, MAX_GOBLIN_GEMS = {'builder': False, 'researcher': False, 'max_gems': 0}, 1000


def cleaned(text: str) -> str:
    return re.sub(r"[^a-z' -]", '', text.lower().translate(OCR_DIGITS)).strip()


@lru_cache(maxsize=None)
def catalog(path: Path = CATALOG) -> dict:
    """English and Indonesian name, cleaned like a read one ("L.A.S.S.I" is "lassi") -> (id, category, name as
    written)."""
    names = {}
    for category, entries in json.loads(path.read_text(encoding='utf-8')).items():
        for key, spellings in entries.items():
            for spelling in spellings:
                names[cleaned(spelling)] = (key, category, spelling)
    return names


def identify(text: str, path: Path = CATALOG) -> tuple:
    """(id, category, name) of the closest catalog name; an unknown name stays as read, with no id, under other."""
    found = difflib.get_close_matches(cleaned(text), list(catalog(path)), n=1, cutoff=NAME_MIN_RATIO)
    return catalog(path)[found[0]] if found else (None, 'other', text)


def split_count(text: str) -> tuple[str, int]:
    """'Archer Tower x 4' -> ('Archer Tower', 4); a single building has no count."""
    found = COUNT.match(text)
    return (found[1], int(found[2])) if found else (text, 1)


def load_templates(root: Path, config: dict) -> dict:
    """Glyph templates of the list: the price digits, the timer's unit letters ('h.png', 'j.png'...) and the level
    badge's own small digits, all in the digit templates' format."""
    units = {}
    for path in sorted(bot.asset_path(root, f'{UNITS}/h.png').parent.glob('*.png')):
        image = read_image(str(path), cv2.IMREAD_GRAYSCALE)
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
    text = ''
    # A short name alone on the line ("Balon", "Minion") can come back empty as a line; it reads as one word.
    for mode in ('7', '8'):
        try:
            result = subprocess.run([program, 'stdin', 'stdout', '--psm', mode, '-l', 'eng'], input=encoded.tobytes(),
                                    capture_output=True, check=True, timeout=15, shell=False, **bot.ocr_process_options(program))
        except (OSError, subprocess.SubprocessError) as exc:
            raise bot.BotError('OCR gagal; scan bangunan dibatalkan') from exc
        text = ' '.join(result.stdout.decode('utf-8', 'replace').split())
        if text:
            break
    return text


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
        h, w = glyph.shape
        # Neighbouring digits can touch ("30").
        pieces = bot.split_glyphs(glyph, max(2, round(w / (h * bot.DIGIT_ASPECT)))) if w > bot.WIDE_RATIO * h \
            else [glyph]
        # The same floor as a row's price: a timer on a running bar's green reads a little fainter.
        number = ''.join(bot.classify(bot.mask_canvas(piece), digits, bot.FRACTION_MIN_SCORE) or '?' for piece in pieces)
        number = None if '?' in number else number
        # A '1' is drawn a little shorter than the other digits, as short as a unit letter ("18j").
        found = (number or letter) if tall else (letter or number)
        # A timer starts with a digit; a letter, or a short glyph that reads as nothing, before it is the helper's face
        # beside a running bar's timer.
        if not text and (found is None and not tall or found is not None and not found.isdigit()):
            continue
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
    # The icon's dark outline can join the square on its right; the square itself is never wider than this.
    w = min(w, BADGE_SQUARE)
    gray = cv2.cvtColor(cv2.resize(image[y:y + h, x:x + w], None, fx=BADGE_SCALE, fy=BADGE_SCALE,
                                   interpolation=cv2.INTER_CUBIC), cv2.COLOR_BGR2GRAY)
    spans = []
    bright = (gray >= BADGE_BRIGHT).any(axis=0)
    for left, right in runs(bright):
        # A digit has black on both sides; a bright column within a pixel of the square's edge is its light rim.
        if left < BADGE_SCALE or right > len(bright) - BADGE_SCALE:
            continue
        # Light from the icon behind can join a digit's span or stand beside it; only the digits' own white tells.
        core = np.flatnonzero(gray[:, left:right].max(axis=0) >= BADGE_CORE)
        if not core.size:
            continue
        if right - left > BADGE_WIDE * BADGE_SCALE and core.size and core[-1] - core[0] < BADGE_ONE * BADGE_SCALE:
            left, right = left + core[0], left + core[-1] + 1
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


@lru_cache(maxsize=1)
def padlock() -> np.ndarray:
    image = read_image(str(PADLOCK))
    if image is None:
        raise bot.BotError(f'Template gembok {PADLOCK.name} hilang')
    return image


def padlocks(area: np.ndarray) -> list[tuple[int, int]]:
    """(top, bottom) in area of every locked row's card, placed from the padlock at its right end."""
    scores = cv2.matchTemplate(area[:, slice(*LOCK_COLUMNS)], padlock(), cv2.TM_SQDIFF_NORMED).min(axis=1)
    tops = [start + int(np.argmin(scores[start:end])) for start, end in runs(scores <= LOCK_MAX_SCORE)]
    return [(top + LOCK_CARD[0], top + LOCK_CARD[1]) for top in tops]


def items(image: np.ndarray, region: list) -> list[tuple[str, int]]:
    """(kind, y) of every row wholly in view: 'card' and 'locked' at its top, 'bar' (a running upgrade) and 'title' at
    their middle. Rows cut by the edge of the list show whole after a swipe."""
    _, top, _, h = region
    area = image[top:top + h]
    edge = area[:, EDGE_COLUMNS[0]:EDGE_COLUMNS[1], 2].mean(axis=1) >= EDGE_MIN_RED
    left = area[:, BAR_COLUMNS[0]:BAR_COLUMNS[1]].astype(int)
    green = ((left[..., 1] > 120) & (left[..., 1] > left[..., 2] + 50)).all(axis=1)
    cards, bars = runs(edge), [(start, end) for start, end in runs(green) if end - start >= BAR_HEIGHT[0]]
    locked = padlocks(area)
    hsv = cv2.cvtColor(area[:, TITLE_COLUMNS[0]:TITLE_COLUMNS[1]], cv2.COLOR_BGR2HSV)
    text = ((hsv[..., 1] <= 80) & (hsv[..., 2] >= 110) & (hsv[..., 2] <= 230)).sum(axis=1) >= TITLE_MIN_PIXELS
    # Cards, bars and locked rows carry text of their own; a title is text in the strip between them.
    for start, end in cards + bars + locked:
        text[max(0, start - TITLE_CLEARANCE):end + TITLE_CLEARANCE] = False

    def whole(spans: list, low: int, high: int) -> list:
        return [(start, end) for start, end in spans if low <= end - start <= high and start > 0 and end < h]
    found = [('card', top + start) for start, _ in whole(cards, CARD_MIN_HEIGHT, h)]
    found += [('locked', top + start) for start, _ in whole(locked, 0, h)]
    found += [('bar', top + (start + end) // 2) for start, end in whole(bars, *BAR_HEIGHT)]
    found += [('title', top + (start + end) // 2) for start, end in whole(runs(text), *TITLE_HEIGHT)]
    return sorted(found, key=lambda item: item[1])


def list_shift(previous: np.ndarray, current: np.ndarray, region: list) -> int:
    """How far the list moved up between two frames: where the previous frame's bottom strip shows in the next."""
    _, y, _, h = region
    bottom = y + h - SHIFT_MARGIN
    left, right = PANEL_COLUMNS
    previous, current = (cv2.GaussianBlur(image[:, left:right], SHIFT_BLUR, 0) for image in (previous, current))
    scores = cv2.matchTemplate(current[y:y + h], previous[bottom - SHIFT_STRIP:bottom], cv2.TM_SQDIFF_NORMED)[:, 0]
    found = int(np.argmin(scores))
    others = np.delete(scores, range(max(0, found - SAME_ROW_PX), found + SAME_ROW_PX))
    if scores[found] > SHIFT_MAX_SCORE or (others.size and others.min() < SHIFT_UNIQUE * scores[found]):
        # A stall to the upgrade run, which tidies the screen and reads the list again.
        raise bot.WaitTimeout('Daftar bangunan bergeser terlalu jauh untuk disambung; scan dibatalkan')
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


def named(text: str, path: Path = CATALOG) -> dict:
    words, count = split_count(text)
    key, category, name = identify(words, path)
    return {'id': key, 'name': name, 'category': category, 'count': count}


def offsets(frames: list, region: list) -> list[int]:
    """How far down the list each frame shows, the first one at 0."""
    found = [0]
    for previous, image in zip(frames, frames[1:]):
        found.append(found[-1] + list_shift(previous, image, region))
    return found


def read_rows(frames: list, spec: dict, templates: dict, program: str, path: Path = CATALOG) -> list[dict]:
    """Every row of the list from frames taken top to bottom, each once: id, name, category, count, next_level,
    time, cost, currency and section, named from the catalog at path."""
    return [row for row, _ in placed_rows(frames, spec, templates, program, path)]


def listed(frames: list, region: list) -> list[tuple[str, int, int, np.ndarray]]:
    """Every title, bar and card of frames taken top to bottom, each once and in list order: (kind, place down the
    list, y in its frame, that frame)."""
    kept = []
    for index, (offset, image) in enumerate(zip(offsets(frames, region), frames)):
        for kind, y in items(image, region):
            # A card cut off by the list's bottom edge loses its badge and price; the next frame shows it whole.
            if kind in ('card', 'locked') and index + 1 < len(frames) and y + CARD_DEPTH > region[1] + region[3]:
                continue
            if not any(kind == other and abs(offset + y - place) <= SAME_ROW_PX for other, place, _, _ in kept):
                kept.append((kind, offset + y, y, image))
    return sorted(kept, key=lambda item: item[1])


def read_lines(ordered: list, program: str, name_band: tuple) -> list[str]:
    """The text line of each item listed: a title's grey words, a running bar's name, a card's name at name_band and a
    locked row's there too, white or green."""
    jobs = [(band(image, TITLE_BAND, y), program, *TITLE_TEXT) if kind == 'title' else
            (band(image, BAR_NAME, y), program) if kind == 'bar' else
            (band(image, name_band, y), program, *LOCKED_TEXT) if kind == 'locked' else
            (band(image, name_band, y), program) for kind, _, y, image in ordered]
    # Tesseract takes about half a second to start, so the lines are read side by side.
    with ThreadPoolExecutor(OCR_WORKERS) as pool:
        return list(pool.map(lambda job: ocr_line(*job), jobs))


def placed_rows(frames: list, spec: dict, templates: dict, program: str,
                path: Path = CATALOG) -> list[tuple[dict, int]]:
    """read_rows with each row's place down the list: its card's top as the first frame would show it."""
    region, digits, units = spec['row']['region'], templates['digits'], templates['units']
    ordered = listed(frames, region)
    texts = read_lines(ordered, program, NAME_BAND)
    rows, section, titles = [], None, 0
    for (kind, place, y, image), text in zip(ordered, texts):
        if kind == 'title':
            section = title_section(text, titles)
            titles += 1
        elif kind == 'locked':
            rows.append(({**named(text, path), 'next_level': None, 'time': None, 'cost': None, 'currency': None,
                          'section': 'locked'}, place))
        elif kind == 'bar':
            rows.append(({**named(text, path), 'next_level': None,
                          'time': read_time(band(image, BAR_TIME, y), digits, units), 'cost': None, 'currency': None,
                          'section': 'in_progress'}, place))
        else:
            cost = bot.row_price(image, spec['row']['price'], y + NAME_CENTRE, digits)
            rows.append(({**named(text, path),
                          'next_level': read_badge(band(image, BADGE_BAND, y), templates['badge']),
                          'time': read_time(band(image, TIME_BAND, y), digits, units),
                          'cost': None if np.isinf(cost) else int(cost),
                          'currency': currency(band(image, COIN_BAND, y)), 'section': section or 'other'}, place))
    return rows


def overlapping_swipe(spec: dict, reverse: bool = False) -> list:
    """Keep enough overlap for list_shift despite the game's inertial scrolling."""
    start, end = spec['scroll'][::-1] if reverse else spec['scroll']
    distance = max(abs(end[0] - start[0]), abs(end[1] - start[1]))
    fraction = min(1, 160 / distance) if distance else 1
    return [list(start), [round(start[i] + (end[i] - start[i]) * fraction) for i in (0, 1)]]


def list_frames(runner: bot.Bot, spec: dict, image: np.ndarray) -> list:
    """Frames of the open builder list from its top to its end."""
    frames = [runner.scroll_list(spec, spec['scroll'][::-1], image)]
    for _ in range(bot.WALL_SCROLL_LIMIT):
        moved = runner.scroll_list(spec, overlapping_swipe(spec), frames[-1], once=True)
        if moved is None:
            break
        frames.append(moved)
    return frames


def list_in_front(runner: bot.Bot, image: np.ndarray) -> bool:
    """The list's own close button shows undimmed, on any of its tabs: nothing lies over the list."""
    screen = runner.config['screens']['build_list']
    cx, cy = screen['actions']['close']
    anchors = [template for template, anchor in zip(runner.detector.templates['build_list'], screen['anchors'])
               if anchor['roi'][0] <= cx < anchor['roi'][0] + anchor['roi'][2]
               and anchor['roi'][1] <= cy < anchor['roi'][1] + anchor['roi'][3]]
    return bool(anchors) and runner.detector.matches(image, anchors)


def show_tab(runner: bot.Bot, x: int) -> np.ndarray:
    """Tap one of the open list's tabs; the frame once the list holds still. A tab sits where a row's upgrade dialog has
    its confirm button, so such a dialog is closed with its X first and nothing is tapped unless the list is in front."""
    if runner.live:
        frame = runner.device.capture()
        for dialog in ROW_DIALOGS:
            if runner.detector.shows(frame, dialog):
                runner.action(dialog, 'close')
                frame = runner.wait_still(bot.WALL_LIST_SETTLE_SECONDS if runner.poll else 0)
        if not list_in_front(runner, frame):
            raise bot.UnexpectedScreen('Daftar Bangun tertutup layar lain; tab tidak disentuh')
        runner.device.tap_many([[x, TAB_Y]])
    return runner.wait_still(bot.WALL_LIST_SETTLE_SECONDS if runner.poll else 0)


def keep_frames(folder: Path, frames: list) -> None:
    """The list part of every frame a scan read (the fixtures' 344 px), replacing the last scan's: a row that did not
    read can be looked at later without the game."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob('*.png'):
            old.unlink()
        for index, frame in enumerate(frames):
            write_image(str(folder / f'{index:02d}.png'), frame[:, KEPT_LEFT:])
    except OSError as exc:
        LOG.warning('Frame scan tidak tersimpan (%s)', exc)


def scan(runner: bot.Bot, listing: str = 'buildings', keep: Path | None = None) -> list[dict]:
    """Open the builder list of listing's village (a LISTS key; the boat is taken when the other village shows) on
    listing's tab, read it from its top to its end, and close it again; with keep, the frames are kept there."""
    from . import gear  # gear reads its rows with this module's helpers
    village, tab, path = LISTS[listing]
    spec = runner.config['walls'][village]
    program = bot.executable(runner.config.get('tesseract'), 'Tesseract')
    templates = load_templates(runner.root, runner.config)
    runner.check_touch()
    state = runner.wait({'home', 'builder', 'build_list'}, set(), OPEN_SECONDS)
    if state == 'build_list':
        # Whichever village is behind a list left open is out of sight; closing it shows which.
        runner.close_menus()
        state = runner.wait({'home', 'builder'}, set(), OPEN_SECONDS)
    if state != village:
        runner.navigate(village)
    image = runner.open_build_list(village, spec)
    # The list opens on the builders tab, whose header tells the language.
    indonesian = runner.list_indonesian(image)
    builders = BUILDERS_TAB[village]
    try:
        frames = list_frames(runner, spec, image if tab == builders else show_tab(runner, tab))
    finally:
        if tab != builders:
            # close_menus knows the list by the builders tab only, and the game may reopen the tab it was left on.
            show_tab(runner, builders)
        runner.close_menus()
    if keep is not None:
        keep_frames(keep, frames)
    rows = (gear.read_rows(frames, spec, templates, program) if listing == 'gear'
            else read_rows(frames, spec, templates, program, path))
    # A running upgrade's time left in seconds, so the dashboard can count it down until the next scan.
    return [{**row, 'seconds': seconds(row['time'], indonesian)} if row['section'] == 'in_progress' else row
            for row in rows]


def seconds(text: object, indonesian: bool) -> int | None:
    """A timer as the list writes it in seconds ('1h 23m' is 4980, '2h 12j' in Indonesian 2 days 12 hours); None
    when it was not read."""
    units = TIME_UNITS[indonesian]
    parts = re.findall(r'(\d+)([a-z])', text) if isinstance(text, str) else []
    if not parts or ' '.join(number + unit for number, unit in parts) != text or any(
            unit not in units for _, unit in parts):
        return None
    return sum(int(number) * units[unit] for number, unit in parts)


def selection(value: object, listing: str = 'buildings') -> dict:
    """The dashboard's pick from one list (a LISTS key), checked: targets in priority order, each {id, name, level}
    (id None for a row the catalog does not know, matched by name; level None upgrades for as long as the row is
    listed), and for buildings how many builders to leave free. Walls have --walls."""
    builders = listing == 'buildings'
    if not isinstance(value, dict) or set(value) != ({'targets', 'keep_builders'} if builders else {'targets'}):
        raise bot.BotError('Pilihan bangunan perlu targets dan keep_builders' if builders else
                           'Pilihan riset perlu targets')
    targets = value['targets']
    if builders:
        bot.integer(value['keep_builders'], 0, MAX_KEEP_BUILDERS, 'keep_builders')
    if not isinstance(targets, list) or len(targets) > MAX_TARGETS:
        raise bot.BotError(f'Target bangunan harus daftar berisi 0..{MAX_TARGETS}')
    known = {key for key, _, _ in catalog(LISTS[listing][2]).values()} - {'wall'}
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
    return {'targets': checked, 'keep_builders': value['keep_builders']} if builders else {'targets': checked}


def goblin(value: object) -> dict:
    """The Goblin Builder and Goblin Researcher switches (config.local.json 'goblin'), checked: the only gems --buildings
    may ever pay are for hiring one whose switch is on, at most max_gems a hire (0 never)."""
    if not isinstance(value, dict) or set(value) != set(GOBLIN_OFF) or not all(
            type(value[key]) is bool for key in ('builder', 'researcher')):
        raise bot.BotError('Goblin perlu builder dan researcher (true/false) serta max_gems')
    bot.integer(value['max_gems'], 0, MAX_GOBLIN_GEMS, 'max_gems')
    return {key: value[key] for key in GOBLIN_OFF}


def validate(config: dict, root: Path) -> dict:
    """--buildings: the wall calibration it shares (builder list, price and icon on the upgrade dialog's button, gem
    bars), its own (free builder counter, dark elixir icon, upgrade dialog), the Builder Base's when it has picks
    there, and every list's pick (a LISTS key each), with at least one target."""
    bot.validate_walls(config, root, ('home',))
    upgrade = config.get('building_upgrade')
    if not isinstance(upgrade, dict) or 'building_confirm' not in config['screens']:
        raise bot.BotError('--buildings butuh kalibrasi: building_upgrade dan building_confirm')
    bot.rectangle(upgrade.get('free_builders'), config['resolution'])
    icons = upgrade.get('icons')
    if not isinstance(icons, dict) or set(icons) != {'dark'} or \
            read_image(str(bot.asset_path(root, icons['dark']))) is None:
        raise bot.BotError('building_upgrade icons perlu ikon dark')
    if not set(CURRENCIES) <= set(config.get('capacity', {}).get('home', {})):
        raise bot.BotError('--buildings butuh kalibrasi: capacity home gold, elixir dan dark')
    goblin(config.get('goblin', GOBLIN_OFF))
    empty = {listing: {'targets': []} for listing in LISTS}
    empty['buildings']['keep_builders'] = 0
    chosen = {listing: selection(config.get(listing, empty[listing]), listing) for listing in LISTS}
    if not any(chosen[listing]['targets'] for listing in LISTS):
        raise bot.BotError('Belum ada pilihan upgrade; pilih di dashboard (Upgrade) lalu Simpan')
    if any(chosen[listing]['targets'] for listing in LISTS if LISTS[listing][0] == 'builder'):
        bot.validate_walls(config, root, ('builder',))
        if 'builder_upgrade_confirm' not in config['screens'] or \
                not {'gold', 'elixir'} <= set(config.get('capacity', {}).get('builder', {})):
            raise bot.BotError('Upgrade Desa Malam butuh kalibrasi: builder_upgrade_confirm dan capacity builder '
                               'gold dan elixir')
    return chosen


def matches(row: dict, target: dict) -> bool:
    if target['id'] is not None:
        return row['id'] == target['id']
    return row['id'] is None and row['name'].lower() == target['name'].lower()


def row_key(row: dict) -> tuple:
    return row['id'] or row['name'], row['next_level'], row.get('cost')


def payable(row: dict, amounts: dict) -> bool:
    """A row's price is read and covered by the resource on hand. Equipment's ore is not shown on the village, so
    there every ore cost need only be read: the dialog shows an amount the ore on hand does not cover in red."""
    if 'costs' in row:
        return bool(row['costs']) and None not in row['costs'].values()
    # The Builder Base has no dark elixir: a row read as dark there is never payable.
    return row['cost'] is not None and row['currency'] in CURRENCIES and row['cost'] <= amounts.get(row['currency'], 0)


def pick(placed: list, targets: list, amounts: dict, refused: set = frozenset()) -> tuple:
    """((row, place) to upgrade now or None, whether any target still has something left). The first target in
    priority order wins with a listed row that is not running, does not go past its level and is payable. A row
    going past the level means that target is reached; a running one, or one short of resources or not read, is left
    for later. A locked row has no level or price of its own: it wins as it is, the building it waits for being
    upgraded at that one's price (upgrades.unlock), and stays pending until the game lists it unlocked."""
    pending = False
    for target in targets:
        for row, place in placed:
            if not matches(row, target):
                continue
            if row['section'] == 'locked':
                pending = True
                if row_key(row) not in refused:
                    return (row, place), True
                continue
            level = target['level']
            if row['section'] != 'in_progress' and level is not None and row['next_level'] is not None \
                    and row['next_level'] > level:
                continue
            pending = True
            if (row['section'] == 'in_progress' or row_key(row) in refused
                    or (level is not None and row['next_level'] is None)):
                continue
            if payable(row, amounts):
                return (row, place), True
    return None, pending


def pause_seconds(rows: list, indonesian: bool) -> int:
    """Until the soonest running upgrade is done, a little after; WAIT_MAX_SECONDS when none is running."""
    timers = [seconds(row['time'], indonesian) for row in rows if row['section'] == 'in_progress']
    timers = [timer for timer in timers if timer is not None]
    return min(timers) + WAIT_MARGIN_SECONDS if timers else WAIT_MAX_SECONDS


def shortfall(placed: list, targets: list, amounts: dict, refused: set = frozenset()) -> str | None:
    """The resource pick would pay its row with now if the village had enough of it: the reason nothing is picked
    when that row's price is read but not covered. None for a locked row or equipment, whose price is not on hand."""
    rich, _ = pick(placed, targets, dict.fromkeys(CURRENCIES, float('inf')), refused)
    if rich is None or rich[0]['section'] == 'locked' or 'costs' in rich[0]:
        return None
    return rich[0]['currency']


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


def builder_button(image: np.ndarray, templates: dict) -> tuple:
    """(price, resource, tap) of a Builder Base upgrade dialog's green button: the price None when it does not read
    white and whole, the resource None unless the icon is all Builder Gold or all Builder Elixir colour; None when
    there is no such button."""
    left, top, _, _ = BUILDER_BUTTONS
    hsv = cv2.cvtColor(bot.crop(image, BUILDER_BUTTONS), cv2.COLOR_BGR2HSV)
    green = ((hsv[..., 0] >= 30) & (hsv[..., 0] <= 50) & (hsv[..., 1] > 120) & (hsv[..., 2] > 150)).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(green)
    if count < 2:
        return None
    best = max(range(1, count), key=lambda index: stats[index, cv2.CC_STAT_AREA])
    x, y, w, h, area = (int(value) for value in stats[best])
    if area < BUTTON_MIN_PIXELS:
        return None
    x, y = left + x, top + y
    button = cv2.cvtColor(image[y:y + h, x:x + w], cv2.COLOR_BGR2HSV)
    shares, columns = {}, {}
    for resource, ((low, high), saturation, value) in BUILDER_ICONS.items():
        mask = ((button[..., 0] >= low) & (button[..., 0] <= high) & (button[..., 1] > saturation)
                & (button[..., 2] > value))
        shares[resource] = mask.sum() / mask.size
        columns[resource] = np.nonzero(mask.any(axis=0))[0]
    found = [resource for resource, share in shares.items() if share >= ICON_MIN_SHARE]
    resource = found[0] if len(found) == 1 and all(
        share <= ICON_OTHER_SHARE for other, share in shares.items() if other != found[0]) else None
    price = None
    if resource is not None:
        # The digits end just left of the icon; the button's rim and shine above and below are left out.
        digits = image[y + h // 5:y + h * 4 // 5, x + 6:x + int(columns[resource][0]) - 2]
        try:
            price = bot.read_trailing_number(digits, templates, bot.FRACTION_MIN_SCORE)
        except bot.BotError:
            price = None
    return price, resource, [x + w // 2, y + h // 2]


def builder_refusal(image: np.ndarray, templates: dict, row: dict) -> str | None:
    """Why a Builder Base upgrade dialog must not be confirmed; None only when its green button shows the row's cost,
    white, beside the row's resource icon. A gem price never has a Builder Gold or Builder Elixir icon."""
    found = builder_button(image, templates)
    if found is None:
        return messages.translate('tombol harga tidak terlihat')
    price, resource, _ = found
    if resource != row['currency']:
        return messages.translate(f'ikon harga {resource}, di daftar {row["currency"]}')
    if price != row['cost']:
        return messages.translate(f'harga di dialog {price}, di daftar {row["cost"]}')
    return None


@lru_cache(maxsize=1)
def goblin_builder_badge() -> np.ndarray:
    image = read_image(str((RESOURCE_ROOT / 'calibration') / 'goblin-builder-hud.png'))
    if image is None:
        raise bot.BotError('Template ikon Goblin Builder hilang')
    return image


def regular_builders(image: np.ndarray, free: int, total: int, region: list) -> int:
    """The green Goblin portrait indicates an available paid slot in the HUD count.

    Exclude it from regular builders; room() separately applies the Goblin opt-in.
    The badge is beside the numeric counter, independent of game language/zoom.
    """
    if free == 0 or total < 6:
        return free
    x, y, w, _ = region
    area = image[max(0, y - 40):y + 3, max(0, x):x + w]
    template = goblin_builder_badge()
    if area.shape[0] < template.shape[0] or area.shape[1] < template.shape[1]:
        return free
    score = cv2.minMaxLoc(cv2.matchTemplate(area, template, cv2.TM_SQDIFF_NORMED))[0]
    return max(0, free - 1) if score <= 0.035 else free


def village(runner: bot.Bot, name: str = 'home') -> tuple[int, dict]:
    """Free builders and the resources on hand once two frames agree, read on the village itself (an open builder
    list would push the resource bars aside): the Home Village's builders, gold, elixir and dark elixir; the Builder
    Base's gold and elixir, its builders being counted on the list's tab instead (0 here)."""
    templates, bars = runner.templates(), runner.config['capacity'][name]
    keys = CURRENCIES if name == 'home' else ('gold', 'elixir')
    region = runner.config['building_upgrade']['free_builders']

    def read(image: np.ndarray) -> tuple:
        free, total = bot.read_fraction(bot.crop(image, region), templates) if name == 'home' else (0, 0)
        if free > total:
            raise bot.BotError('Jumlah tukang bebas meragukan')
        if name == 'home':
            free = regular_builders(image, free, total, region)
        return free, tuple(bot.read_number(bot.crop(image, bars[key]['bar']), templates, bot.FRACTION_MIN_SCORE)
                           for key in keys)
    runner.wait({name}, set(), OPEN_SECONDS)
    try:
        free, amounts = runner.read_stable(read, bot.GEM_READ_SECONDS if runner.poll else 0)
    except bot.BotError as exc:
        raise bot.WaitTimeout(f'Tukang bebas/resource desa tidak terbaca ({exc}); tidak ada upgrade') from exc
    return free, dict(zip(keys, amounts))


def find_card(runner: bot.Bot, spec: dict, frames: list, place: int, row: dict, program: str, path: Path = CATALOG,
              name_band: tuple = NAME_BAND) -> int:
    """y of the row's card, swiping the list back up from its end until it shows. Its name (at name_band, named from
    the catalog at path) is read again first, so a list that moved under the bot never gets another row tapped."""
    region = spec['row']['region']
    image, offset = frames[-1], offsets(frames, region)[-1]
    wanted, colours = ('locked', LOCKED_TEXT) if row['section'] == 'locked' else ('card', ())
    for _ in range(bot.WALL_SCROLL_LIMIT):
        for kind, y in items(image, region):
            if kind == wanted and abs(offset + y - place) <= SAME_ROW_PX:
                seen = named(ocr_line(band(image, name_band, y), program, *colours), path)
                if (seen['id'], seen['name'], seen['count']) != (row['id'], row['name'], row['count']):
                    raise bot.WaitTimeout(f'Baris {row["name"]} berubah jadi {seen["name"]}; tidak ada input')
                return y
        moved = runner.scroll_list(spec, overlapping_swipe(spec, reverse=True), image, once=True)
        if moved is None:
            break
        offset -= list_shift(moved, image, region)
        image = moved
    raise bot.WaitTimeout(f'Baris {row["name"]} tidak ketemu lagi di daftar; tidak ada input')
