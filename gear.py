"""Hero equipment on the builder list's anvil tab ("Hero Equipment", "Peralatan Pahlawan"): every row's name, level
badge and ore costs, named from gear.json. An equipment upgrade is paid in ore and done at once, so a row has no
timer; a row's hero portrait and icon push its name further right than a building's."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import difflib
from functools import lru_cache
import json
from pathlib import Path
import re

import cv2
import numpy as np

import bot
import buildings

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


@lru_cache(maxsize=1)
def catalog() -> dict:
    """Name as the cleaned OCR text would read -> (id, name as written)."""
    return {clean(spelling): (key, spelling)
            for key, spellings in json.loads(CATALOG.read_text(encoding='utf-8')).items() for spelling in spellings}


def clean(text: str) -> str:
    return re.sub(r"[^a-z' -]", '', text.lower()).strip()


def named(text: str) -> dict:
    """The closest catalog name; an unknown one stays as read, with no id."""
    found = difflib.get_close_matches(clean(text), list(catalog()), n=1, cutoff=buildings.NAME_MIN_RATIO)
    key, name = catalog()[found[0]] if found else (None, text)
    return {'id': key, 'name': name}


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
        rows.append({**named(text), 'category': 'equipment',
                     'next_level': buildings.read_badge(buildings.band(image, BADGE_BAND, y), templates['badge']),
                     'costs': costs(image, y, templates['digits']), 'section': section})
    return rows
