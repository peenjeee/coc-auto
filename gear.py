"""Hero equipment on the builder list's anvil tab ("Hero Equipment", "Peralatan Pahlawan"): every row's name, level
badge and ore costs, named from gear.json. An equipment upgrade is paid in ore and done at once, so a row has no
timer; a row's hero portrait and icon push its name further right than a building's."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

import bot
import buildings
import messages

CATALOG = Path(__file__).with_name('gear.json')
ORES = ('shiny', 'glowy', 'starry')
# Each cost line ends in its ore, in a column right of the price: shiny blue, glowy purple, starry yellow (OpenCV hues).
ORE_HUES = {'shiny': (95, 115), 'glowy': (125, 165), 'starry': (8, 35)}
ORE_COLUMNS, ORE_MIN_SATURATION, ORE_MIN_VALUE, ORE_MIN_PIXELS, ORE_MIN_HEIGHT = (1248, 1274), 110, 150, 3, 6
# [x, dy, w, h] at 1280x720 from a card's top. One to three cost lines are centred on the card; each is read between
# the midpoints to its neighbours, so the line above never reaches into it.
CARD_HEIGHT, NAME_BAND, BADGE_BAND = 50, (1030, 2, 170, 20), (985, 26, 14, 15)
COST_X, COST_HALF, COST_TOP_GAP = (1150, 1248), 8, 2
# "Equipped" checked after "Unequipped", which contains it; a locked row's green name also passes as a title and
# opens no section.
TITLE_WORDS = (('unequipped', ('unequip', 'dilepas')), ('equipped', ('equip', 'dipasang')))
# The upgrade dialog's green Confirm button ([x, y, w, h]) lists each ore's amount before its icon on one line; an
# amount the ore on hand does not cover is red, which never reads as a price. The grey button beside it uses an item.
DIALOG_PRICES, DIALOG_TEXT_PAD = (850, 615, 307, 50), 2


def costs(image: np.ndarray, y: int, digits: dict) -> dict:
    """{ore: amount} of the card at y; an amount that does not read cleanly is None."""
    hsv = cv2.cvtColor(image[y:y + CARD_HEIGHT, slice(*ORE_COLUMNS)], cv2.COLOR_BGR2HSV)
    lines = []
    for ore, (low, high) in ORE_HUES.items():
        mask = ((hsv[..., 0] >= low) & (hsv[..., 0] <= high) & (hsv[..., 1] > ORE_MIN_SATURATION)
                & (hsv[..., 2] > ORE_MIN_VALUE))
        lines += [(y + (start + end) // 2, ore) for start, end in buildings.runs(mask.sum(axis=1) >= ORE_MIN_PIXELS)
                  if end - start >= ORE_MIN_HEIGHT]
    lines.sort()
    found = {}
    for index, (centre, ore) in enumerate(lines):
        top = (lines[index - 1][0] + centre) // 2 + COST_TOP_GAP if index else centre - COST_HALF
        bottom = (centre + lines[index + 1][0]) // 2 + 1 if index + 1 < len(lines) else centre + COST_HALF
        price = bot.row_price(image, [COST_X[0], top - y, COST_X[1] - COST_X[0], bottom - top], y, digits)
        found[ore] = None if np.isinf(price) else int(price)
    return found


def ore_spans(hsv: np.ndarray, ore: str, axis: int) -> list[tuple[int, int]]:
    """Where the ore's colour runs along the other axis (rows with axis=1, columns with axis=0)."""
    low, high = ORE_HUES[ore]
    mask = (hsv[..., 0] >= low) & (hsv[..., 0] <= high) & (hsv[..., 1] > ORE_MIN_SATURATION) & (hsv[..., 2] > ORE_MIN_VALUE)
    return [(start, end) for start, end in buildings.runs(mask.sum(axis=axis) >= ORE_MIN_PIXELS)
            if end - start >= ORE_MIN_HEIGHT]


def dialog_costs(image: np.ndarray, digits: dict) -> dict:
    """{ore: amount} on the upgrade dialog's Confirm button; an amount that is red or unclear is None."""
    x, y, w, h = DIALOG_PRICES
    hsv = cv2.cvtColor(image[y:y + h, x:x + w], cv2.COLOR_BGR2HSV)
    icons = sorted((x + left, x + right, ore) for ore in ORE_HUES for left, right in ore_spans(hsv, ore, 0))
    found, start = {}, x
    for left, right, ore in icons:
        rows = [row for low, high in ore_spans(hsv[:, left - x:right - x], ore, 1) for row in (low, high)]
        top, bottom = y + min(rows) - DIALOG_TEXT_PAD, y + max(rows) + DIALOG_TEXT_PAD
        text = cv2.resize(image[top:bottom, start:left], None, fx=bot.PRICE_SCALE, fy=bot.PRICE_SCALE,
                          interpolation=cv2.INTER_CUBIC)
        try:
            found[ore] = bot.read_trailing_number(text, digits, bot.FRACTION_MIN_SCORE, bot.PRICE_MAX_SATURATION)
        except bot.BotError:
            found[ore] = None
        start = right
    return found


def refusal(image: np.ndarray, row: dict, digits: dict) -> str | None:
    """Why the dialog must not be confirmed; None only when its button asks exactly the row's ores, all affordable."""
    shown = dialog_costs(image, digits)
    if not row.get('costs') or None in row['costs'].values() or shown != row['costs']:
        return messages.translate(f'biaya di dialog {shown}, di daftar {row.get("costs")}')
    return None


def title(text: str) -> str | None:
    lowered = text.lower()
    return next((section for section, words in TITLE_WORDS if any(word in lowered for word in words)), None)


def read_rows(frames: list, spec: dict, templates: dict, program: str) -> list[dict]:
    """Every equipment row from frames taken top to bottom, each once: id, name, next_level, costs and section."""
    region = spec['row']['region']
    kept = []
    for offset, image in zip(buildings.offsets(frames, region), frames):
        for kind, y in buildings.items(image, region):
            if not any(kind == other and abs(offset + y - place) <= buildings.SAME_ROW_PX for other, place, _, _ in kept):
                kept.append((kind, offset + y, y, image))
    ordered = sorted(kept, key=lambda item: item[1])
    jobs = [(buildings.band(image, buildings.TITLE_BAND, y), program, *buildings.TITLE_TEXT) if kind == 'title' else
            (buildings.band(image, NAME_BAND, y), program) for kind, _, y, image in ordered]
    # Tesseract takes about half a second to start, so the lines are read side by side.
    with ThreadPoolExecutor(buildings.OCR_WORKERS) as pool:
        texts = list(pool.map(lambda job: buildings.ocr_line(*job), jobs))
    rows, section = [], 'equipped'
    for (kind, _, y, image), text in zip(ordered, texts):
        if kind == 'title':
            section = title(text) or section
            continue
        rows.append({**buildings.named(text, CATALOG),
                     'next_level':buildings.read_badge(buildings.band(image, BADGE_BAND, y), templates['badge']),
                     'costs': costs(image, y, templates['digits']), 'section': section})
    return rows
