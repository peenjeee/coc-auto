"""The game's builder list ("Buildings & Heroes", "Bangunan dan Pahlawan") read top to bottom: the name, count, level
badge, time and cost of every row, named from buildings.json; its Laboratory and pet tabs, named from research.json,
read the same way. A scan only opens, swipes and closes the list.
The helpers here also serve --buildings (upgrades.run), which upgrades the rows picked in the dashboard."""
from __future__ import annotations

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

import bot
import messages

LOG = logging.getLogger(__name__)
CATALOG = Path(__file__).with_name('buildings.json')
# Lab and pet names stay out of the buildings catalog: "Raksasa" (Giant) would match "Bom Raksasa" (Giant Bomb).
RESEARCH = Path(__file__).with_name('research.json')
GEAR = Path(__file__).with_name('gear.json')
BUILDER = Path(__file__).with_name('builder.json')
STAR = Path(__file__).with_name('star.json')
# Each list: the village whose builder list has it, its tab's x along the list's bottom (y TAB_Y) and the catalog its
# rows are named from. The first tab of each village is its builders'.
LISTS = {'buildings': ('home', 978, CATALOG), 'lab': ('home', 1062, RESEARCH), 'gear': ('home', 1150, GEAR),
         'pets': ('home', 1240, RESEARCH), 'builder': ('builder', 1021, BUILDER), 'star': ('builder', 1193, STAR)}
BUILDERS_TAB = {'home': 978, 'builder': 1021}
TAB_Y = 690
# Upgrade dialogs a row opens over the list.
ROW_DIALOGS = ('building_confirm', 'gear_confirm')
# The dialog a row of each list opens, for the lists --buildings upgrades. A Builder Base row opens a dialog of
# another layout that no calibration knows (a Star Laboratory row can open the laboratory itself), so those picks are
# kept but never tapped.
DIALOGS = {'buildings': 'building_confirm', 'lab': 'building_confirm', 'gear': 'gear_confirm',
           'pets': 'building_confirm'}
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
    text = ''
    # A short name alone on the line ("Balon", "Minion") can come back empty as a line; it reads as one word.
    for mode in ('7', '8'):
        try:
            result = subprocess.run([program, 'stdin', 'stdout', '--psm', mode, '-l', 'eng'], input=encoded.tobytes(),
                                    capture_output=True, check=True, timeout=15, shell=False)
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
        number = ''.join(bot.classify(bot.mask_canvas(piece), digits) or '?' for piece in pieces)
        number = None if '?' in number else number
        # A '1' is drawn a little shorter than the other digits, as short as a unit letter ("18j").
        found = (number or letter) if tall else (letter or number)
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


def placed_rows(frames: list, spec: dict, templates: dict, program: str,
                path: Path = CATALOG) -> list[tuple[dict, int]]:
    """read_rows with each row's place down the list: its card's top as the first frame would show it."""
    region, digits, units = spec['row']['region'], templates['digits'], templates['units']
    kept = []
    for index, (offset, image) in enumerate(zip(offsets(frames, region), frames)):
        for kind, y in items(image, region):
            # A card cut off by the list's bottom edge loses its badge and price; the next frame shows it whole.
            if kind == 'card' and index + 1 < len(frames) and y + CARD_DEPTH > region[1] + region[3]:
                continue
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


def list_frames(runner: bot.Bot, spec: dict, image: np.ndarray) -> list:
    """Frames of the open builder list from its top to its end."""
    frames = [runner.scroll_list(spec, spec['scroll'][::-1], image)]
    for _ in range(bot.WALL_SCROLL_LIMIT):
        moved = runner.scroll_list(spec, spec['scroll'], frames[-1], once=True)
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


def scan(runner: bot.Bot, listing: str = 'buildings') -> list[dict]:
    """Open the builder list of listing's village (a LISTS key; the boat is taken when the other village shows) on
    listing's tab, read it from its top to its end, and close it again."""
    import gear  # gear reads its rows with this module's helpers
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


def validate(config: dict, root: Path) -> dict:
    """--buildings: the wall calibration it shares (builder list, price and icon on the upgrade dialog's button, gem
    bars), its own (free builder counter, dark elixir icon, upgrade dialog) and every list's pick (a LISTS key each),
    with at least one target in a list it upgrades (DIALOGS)."""
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
    empty = {listing: {'targets': []} for listing in LISTS}
    empty['buildings']['keep_builders'] = 0
    chosen = {listing: selection(config.get(listing, empty[listing]), listing) for listing in LISTS}
    if not any(chosen[listing]['targets'] for listing in DIALOGS):
        raise bot.BotError('Belum ada bangunan, riset, hewan atau peralatan Desa Utama dipilih; pilih di dashboard '
                           '(Upgrade) lalu Simpan')
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
    return row['cost'] is not None and row['currency'] in CURRENCIES and row['cost'] <= amounts[row['currency']]


def pick(placed: list, targets: list, amounts: dict, refused: set = frozenset()) -> tuple:
    """((row, place) to upgrade now or None, whether any target still has something left). The first target in
    priority order wins with a listed row that is not running, does not go past its level and is payable. A row
    going past the level means that target is reached; a running one, or one short of resources or not read, is left
    for later."""
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
            if (row['section'] == 'in_progress' or row_key(row) in refused
                    or (level is not None and row['next_level'] is None)):
                continue
            if payable(row, amounts):
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


def find_card(runner: bot.Bot, spec: dict, frames: list, place: int, row: dict, program: str, path: Path = CATALOG,
              name_band: tuple = NAME_BAND) -> int:
    """y of the row's card, swiping the list back up from its end until it shows. Its name (at name_band, named from
    the catalog at path) is read again first, so a list that moved under the bot never gets another row tapped."""
    region = spec['row']['region']
    image, offset = frames[-1], offsets(frames, region)[-1]
    for _ in range(bot.WALL_SCROLL_LIMIT):
        for kind, y in items(image, region):
            if kind == 'card' and abs(offset + y - place) <= SAME_ROW_PX:
                seen = named(ocr_line(band(image, name_band, y), program), path)
                if (seen['id'], seen['name'], seen['count']) != (row['id'], row['name'], row['count']):
                    raise bot.WaitTimeout(f'Baris {row["name"]} berubah jadi {seen["name"]}; tidak ada input')
                return y
        moved = runner.scroll_list(spec, spec['scroll'][::-1], image, once=True)
        if moved is None:
            break
        offset -= list_shift(moved, image, region)
        image = moved
    raise bot.WaitTimeout(f'Baris {row["name"]} tidak ketemu lagi di daftar; tidak ada input')
