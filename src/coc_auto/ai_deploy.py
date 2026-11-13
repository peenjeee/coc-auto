"""AI deploy planner: asks an OpenAI-compatible vision model for a staged drop plan and compiles the answer into the
same step dicts that Bot.deploy_card runs. Reply parsing, validation and compiling are pure; the HTTP call goes
through an injectable transport so tests run offline."""

import base64
import json
from urllib import error, request

import cv2
import numpy as np

MAX_STAGES = 8
MAX_STEPS = 40
MAX_TAPS = 400
MAX_STAGE_WAIT = 15.0
MAX_TOTAL_WAIT = 60.0
MAX_HOLD = 10.0
DEFAULT_HOLD = 6.0
MAX_TIMEOUT = 30.0
GOALS = {
    'loot': 'Goal: loot. Hit storages and collectors; prefer the edge with the fewest defences.',
    'trophy': 'Goal: trophies. Destroy the Town Hall and as much of the base as possible.',
    'stars': 'Goal: stars. Maximise destruction and take all three stars if possible.',
}
SYSTEM_PROMPT = (
    'You plan a Clash of Clans attack from a screenshot. Reply with JSON only, no prose: '
    '{"analysis": "<short>", "stages": [{"name": "<short>", "wait_before": <seconds>, "actions": ['
    '{"card": "<card id>", "place": [{"at": [x, y], "count": <int>}], "mode": "tap" or "hold", '
    '"seconds": <hold seconds, hold mode only>, "ability_after": <seconds, heroes only>}]}]}. '
    'Coordinates are screen pixels of the 1280x720 frame shown by the grid. Troops, siege machines and heroes '
    'must go outside the enemy base (a drop inside it is refused); spells may go anywhere, including over the '
    'base. Use only the card ids listed. Never exceed a card\'s count when it is known. '
    'Heroes take count 1 and get ability_after. Keep stages short and ordered: funnel first, then main push, '
    'then spells, then heroes if they are needed.'
)


SPELL_PROMPT = (
    'You place the remaining spells of a Clash of Clans attack that is already under way. You see the battle now: '
    'the army has landed. Reply with JSON only, no prose: {"analysis": "<short>", "stages": [{"name": "<short>", '
    '"wait_before": <seconds>, "actions": [{"card": "<spell id>", "place": [{"at": [x, y], "count": <int>}]}]}]}. '
    'Coordinates are screen pixels of the 1280x720 frame. Spells may go anywhere, including over the base. Put '
    'Rage and Heal on the troops where they are fighting now, Freeze on the defences the army is facing, '
    'Lightning and Earthquake on defences or storages. Use only the spell ids listed and never more than the '
    'count shown. Keep it to one or two stages.'
)


class AIPlanError(Exception):
    pass


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def parse_reply(text: str) -> dict:
    """The first JSON object in the reply; models wrap it in fences or prose now and then."""
    if not isinstance(text, str):
        raise AIPlanError('Balasan model bukan teks')
    start = text.find('{')
    if start < 0:
        raise AIPlanError('Balasan model tidak berisi JSON')
    try:
        value, _ = json.JSONDecoder().raw_decode(text, start)
    except ValueError:
        raise AIPlanError('JSON balasan model tidak valid') from None
    if not isinstance(value, dict):
        raise AIPlanError('JSON balasan model harus berupa objek')
    return value


def _number(value: object, cap: float) -> float | None:
    """A non-negative number capped at cap, or None when the value is not a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return None
    return min(max(float(value), 0.0), cap)


# The deployable ground is OUTSIDE the enemy base: a drop inside the base's no-deploy zone does nothing (checked live:
# drops 10 px outside the base hull all deployed, one between buildings inside was refused).
ZONE_MIN_SHARE, ZONE_MAX_SHARE = 0.15, 0.75   # a line hull outside this share of the frame is not a base
ZONE_MIN_LINE = 60        # px; the boundary line is long, plot outlines and building edges are short
BASE_LINK = 9             # px gap across which object pixels count as one cluster
BASE_MIN_PX = 2500        # object pixels the largest cluster needs to count as a base
HULL_RATIO = 1.5          # object hull this much bigger than the line hull = decoration pulled in; trust the line
MARGIN = 14               # px the no-deploy region is grown by
LINE_MISSING_EXTRA = 14   # more margin when no boundary line was found
GROUND_VAL = 150          # smoothed brightness the playable ground needs; the dark forest stays below it
NEAR_OBJECT = 16          # px kept clear around any sizeable object cluster
OBJECT_MIN_PX = 1500      # an object cluster this big (buildings, walls) keeps NEAR_OBJECT clear
REGION_MIN_PX = 1500      # droppable pockets smaller than this are dropped
DROPPABLE_MIN_SHARE = 0.02
# Fixed UI on the 1280x720 battle screen (x0, y0, x1, y1): loot box, resources, timer, End Battle, Next.
HUD_RECTS = ((0, 0, 270, 215), (985, 0, 1280, 175), (520, 0, 760, 85), (0, 500, 400, 651), (1050, 455, 1280, 651))


def _grass(hsv: np.ndarray, val_min: int = 70) -> np.ndarray:
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    return (hue >= 25) & (hue <= 50) & (sat >= 70) & (val >= val_min)


def _playable(shape: tuple, bar_top: int) -> np.ndarray:
    """Where a drop can be: above the card bar and clear of the fixed UI."""
    height, width = shape
    keep = np.ones(shape, bool)
    keep[bar_top:, :] = False
    for x0, y0, x1, y1 in HUD_RECTS:
        keep[y0:min(y1, height), x0:min(x1, width)] = False
    return keep


def line_hull(image: np.ndarray) -> np.ndarray | None:
    """Convex hull of the orange boundary strokes around the base (long, thin, grass beside them), as a bool mask.
    None when too few strokes were found or the hull does not look like a base."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    line = ((hue >= 5) & (hue <= 22) & (sat >= 90) & (val >= 90)).astype(np.uint8)
    grass_near = cv2.dilate(_grass(hsv).astype(np.uint8), np.ones((9, 9), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(line, connectivity=8)
    strokes = []
    for index in range(1, count):
        x, y, w, h, area = stats[index]
        long_side = max(w, h)
        if long_side < ZONE_MIN_LINE or area / float(long_side) > 4.0:
            continue
        x0, y0, x1, y1 = max(x - 4, 0), max(y - 4, 0), x + w + 4, y + h + 4
        comp = (labels[y0:y1, x0:x1] == index).astype(np.uint8)
        ring = cv2.dilate(comp, np.ones((7, 7), np.uint8)) - comp
        if ring.any() and float(grass_near[y0:y1, x0:x1][ring > 0].mean()) >= 0.4:
            strokes.append(np.argwhere(comp)[:, ::-1] + [x0, y0])
    if not strokes:
        return None
    points = np.vstack(strokes).astype(np.int32)
    if len(points) < 300:
        return None
    zone = np.zeros(line.shape, np.uint8)
    cv2.fillPoly(zone, [cv2.convexHull(points)], 1)
    height, width = line.shape
    if not ZONE_MIN_SHARE <= float(zone.mean()) <= ZONE_MAX_SHARE or not zone[height // 2, width // 2]:
        return None
    return zone.astype(bool)


def object_hull(image: np.ndarray, bar_top: int) -> np.ndarray | None:
    """Convex hull of the largest cluster of non-grass objects (the base: buildings and walls), as a bool mask."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    objects = (~_grass(hsv)) & _playable(hsv.shape[:2], bar_top)
    linked = cv2.dilate(objects.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (BASE_LINK, BASE_LINK)))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(linked, connectivity=8)
    if count < 2:
        return None
    base = (labels == 1 + int(np.argmax(stats[1:, 4]))) & objects
    points = cv2.findNonZero(base.astype(np.uint8))
    if points is None or len(points) < BASE_MIN_PX:
        return None
    zone = np.zeros(objects.shape, np.uint8)
    cv2.fillPoly(zone, [cv2.convexHull(points)], 1)
    return zone.astype(bool)


def droppable(image: np.ndarray, bar_top: int) -> tuple[np.ndarray | None, str]:
    """(mask, how) of the ground a drop may land on: playable ground outside the base, as a bool mask.

    The base is the union of the object hull and the line hull (each misses what the other sees: the line includes
    outlying buildings, the objects survive a hidden line); when the object hull is much bigger than the line hull,
    decorations pulled it out and the line alone is used. The base is grown by a margin, anything near a sizeable
    object cluster is kept clear, and small pockets are dropped. None when no base is found or too little is left."""
    objects_hull = object_hull(image, bar_top)
    lines_hull = line_hull(image)
    if objects_hull is None and lines_hull is None:
        return None, 'none'
    if objects_hull is not None and lines_hull is not None:
        if objects_hull.sum() > HULL_RATIO * lines_hull.sum():
            base, how = lines_hull, 'line'
        else:
            base, how = objects_hull | lines_hull, 'union'
    elif lines_hull is not None:
        base, how = lines_hull, 'line'
    else:
        base, how = objects_hull, 'objects'
    margin = MARGIN + (LINE_MISSING_EXTRA if lines_hull is None else 0)
    base = cv2.dilate(base.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * margin + 1,) * 2))
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    smooth = cv2.GaussianBlur(hsv[..., 2], (0, 0), 6)   # forest leaves speckle bright; their local mean stays dark
    ground = _grass(hsv) & (smooth >= GROUND_VAL)
    ground = cv2.morphologyEx(ground.astype(np.uint8), cv2.MORPH_OPEN, np.ones((7, 7), np.uint8)) > 0
    keep = _playable(ground.shape, bar_top)
    # Keep clear of any sizeable object cluster whatever the hulls say.
    objects = ((~_grass(hsv)) & keep).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        cv2.dilate(objects, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))), connectivity=8)
    big = [index for index in range(1, count) if stats[index, 4] >= OBJECT_MIN_PX]
    near = cv2.dilate((np.isin(labels, big) & (objects > 0)).astype(np.uint8),
                      cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * NEAR_OBJECT + 1,) * 2))
    zone = ground & keep & (base == 0) & (near == 0)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(zone.astype(np.uint8), connectivity=8)
    zone = np.isin(labels, [index for index in range(1, count) if stats[index, 4] >= REGION_MIN_PX])
    if float(zone.mean()) < DROPPABLE_MIN_SHARE:
        return None, how
    return zone, how


def snap_to_zone(point: list, zone: np.ndarray) -> list:
    """The point itself when it is droppable, else the nearest droppable pixel."""
    x, y = point
    if zone[y, x]:
        return point
    ys, xs = np.nonzero(zone)
    xs, ys = xs[::2].astype(np.int64), ys[::2].astype(np.int64)
    nearest = int(np.argmin((xs - x) ** 2 + (ys - y) ** 2))
    return [int(xs[nearest]), int(ys[nearest])]


def _point(value: object, bounds: tuple, stats: dict, zone: np.ndarray | None = None) -> list | None:
    """An [x, y] pixel inside the frame above the card bar; out-of-range points are clamped, not guessed. With a
    deploy zone, a point outside it is moved to the zone's edge."""
    if not isinstance(value, list) or len(value) != 2:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or v != v for v in value):
        return None
    width, bar_top = bounds
    x, y = int(round(value[0])), int(round(value[1]))
    clamped = [min(max(x, 0), width - 1), min(max(y, 0), bar_top - 1)]
    if clamped != [x, y]:
        stats['clamped'] += 1
    if zone is not None and not zone[clamped[1], clamped[0]]:
        stats['snapped'] += 1
        return snap_to_zone(clamped, zone)
    return clamped


def compile_plan(raw: dict, cards: dict, bounds: tuple, zone: np.ndarray | None = None) -> tuple[list, dict]:
    """Validated plan as deploy steps (the shapes auto_plan returns) and counts of what was clamped, snapped into
    the deploy zone, or dropped.

    cards: id -> {'slot': [x, y], 'kind': str, 'count': int | None}, from the card bar read.
    bounds: (frame width, card bar top y). zone: droppable mask (ground outside the base), or None when it could not be read.
    """
    stages = raw.get('stages')
    if not isinstance(stages, list) or not 1 <= len(stages) <= MAX_STAGES:
        raise AIPlanError('Rencana AI harus berisi 1 sampai 8 tahap')
    stats = {'clamped': 0, 'snapped': 0, 'dropped': 0, 'truncated': 0}
    steps, used, total_wait = [], {}, 0.0
    for stage in stages:
        actions = stage.get('actions') if isinstance(stage, dict) else None
        if not isinstance(actions, list):
            stats['dropped'] += 1
            continue
        wait = _number(stage.get('wait_before', 0), MAX_STAGE_WAIT) or 0.0
        wait = min(wait, MAX_TOTAL_WAIT - total_wait)
        total_wait += wait
        first = len(steps)
        for action in actions:
            for step in _action_steps(action, cards, bounds, used, stats, zone):
                if len(steps) >= MAX_STEPS:
                    stats['truncated'] += 1
                    continue
                steps.append(step)
        if wait > 0 and len(steps) > first:
            steps[first]['wait'] = wait
    if not steps:
        raise AIPlanError('Rencana AI kosong setelah divalidasi')
    return steps, stats


def _action_steps(action: object, cards: dict, bounds: tuple, used: dict, stats: dict,
                  zone: np.ndarray | None = None) -> list:
    card_id = action.get('card') if isinstance(action, dict) else None
    card = cards.get(card_id) if isinstance(card_id, str) else None
    if card is None:
        stats['dropped'] += 1
        return []
    if card['kind'] == 'spell':
        zone = None   # the no-deploy zone is for troops, siege and heroes; a spell may land on the base
    points = []
    for place in action.get('place') or []:
        at = _point(place.get('at') if isinstance(place, dict) else None, bounds, stats, zone)
        count = place.get('count') if isinstance(place, dict) else None
        if at is None or isinstance(count, bool) or not isinstance(count, int) or count < 1:
            stats['dropped'] += 1
            continue
        points += [at] * min(count, MAX_TAPS)
    budget = (card['count'] if card.get('count') is not None else MAX_TAPS) - used.get(card_id, 0)
    points = points[:max(budget, 0)]
    if not points:
        stats['dropped'] += 1
        return []
    used[card_id] = used.get(card_id, 0) + len(points)
    slot = card['slot']
    if card['kind'] in ('hero', 'warden'):
        steps = [{'slot': slot, 'count': 1, 'points': [points[0]]}]
        if action.get('ability_after') is not None:
            wait = _number(action['ability_after'], MAX_STAGE_WAIT) or 0.0
            steps.append({'slot': slot, 'count': 1, 'tap_only': True, 'wait': wait})
        else:
            steps.append({'slot': slot, 'count': 1, 'tap_only': True})
        return steps
    if action.get('mode') == 'hold':
        seconds = _number(action.get('seconds', DEFAULT_HOLD), MAX_HOLD)
        if seconds is None or seconds <= 0:
            seconds = DEFAULT_HOLD
        # A hold keeps one finger on one spot until the card empties, so only its first point is used.
        if len(points) > 1:
            stats['dropped'] += 1
        return [{'slot': slot, 'count': 1, 'hold': seconds, 'until_empty': True, 'points': [points[0]]}]
    return [{'slot': slot, 'count': len(points), 'points': points, 'burst': len(points)}]


def encode_jpeg(image: np.ndarray, max_edge: int = 1024, quality: int = 80) -> bytes:
    height, width = image.shape[:2]
    scale = min(1.0, max_edge / max(width, height))
    if scale < 1.0:
        image = cv2.resize(image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
    ok, buffer = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise AIPlanError('Gambar untuk model tidak bisa dikodekan')
    return buffer.tobytes()


def build_messages(goal: str, hint: str, cards: dict, bounds: tuple, jpegs: list) -> list:
    """Chat messages: the base screenshot, the card bar crop, and the card inventory as text."""
    inventory = [{'id': cid, 'kind': c['kind'], 'count': c.get('count')} for cid, c in cards.items()]
    lines = [GOALS.get(goal, GOALS['loot'])]
    if hint and hint.strip():
        lines.append(f'Player note: {hint.strip()}')
    lines.append(f'Frame width {bounds[0]}; the card bar starts at y {bounds[1]}, so drop only above it.')
    lines.append(f'Cards: {json.dumps(inventory)}. Count null means unknown.')
    text = '\n'.join(lines)
    content = [{'type': 'text', 'text': text}]
    for jpeg in jpegs:
        url = 'data:image/jpeg;base64,' + base64.b64encode(jpeg).decode('ascii')
        content.append({'type': 'image_url', 'image_url': {'url': url}})
    return [{'role': 'system', 'content': SYSTEM_PROMPT}, {'role': 'user', 'content': content}]


def _post(url: str, payload: bytes, headers: dict, timeout: float) -> bytes:
    opener = request.build_opener(NoRedirect)
    req = request.Request(url, data=payload, headers=headers, method='POST')
    try:
        with opener.open(req, timeout=timeout) as response:
            return response.read()
    except error.HTTPError as exc:
        raise AIPlanError('Model menolak permintaan (HTTP {})'.format(exc.code)) from None
    except (error.URLError, OSError, TimeoutError):
        # The exception text is left out: it can echo the request, which carries the key.
        raise AIPlanError('Tidak bisa terhubung ke model AI') from None


class ChatClient:
    """OpenAI-compatible /chat/completions. base_url is the API root, e.g. https://api.openai.com/v1."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 20.0, max_tokens: int = 800,
                 temperature: float = 0.2, transport=None) -> None:
        if not isinstance(base_url, str) or not base_url.startswith(('http://', 'https://')):
            raise AIPlanError('Base URL model harus diawali http:// atau https://')
        if not model:
            raise AIPlanError('Nama model belum diisi')
        self.url = base_url.rstrip('/') + '/chat/completions'
        self.api_key, self.model = api_key, model
        self.timeout = min(max(float(timeout), 1.0), MAX_TIMEOUT)
        self.max_tokens, self.temperature = max_tokens, temperature
        self.transport = transport or _post

    def with_timeout(self, timeout: float) -> 'ChatClient':
        """The same client with another timeout (the spell checkpoint must not hold up the battle)."""
        import copy
        other = copy.copy(self)
        other.timeout = min(max(float(timeout), 1.0), MAX_TIMEOUT)
        return other

    def complete(self, messages: list) -> str:
        body = {'model': self.model, 'messages': messages, 'max_tokens': self.max_tokens,
                'temperature': self.temperature}
        headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {self.api_key}'}
        raw = self.transport(self.url, json.dumps(body).encode('utf-8'), headers, self.timeout)
        try:
            data = json.loads(raw)
            return data['choices'][0]['message']['content']
        except (ValueError, KeyError, IndexError, TypeError):
            raise AIPlanError('Format balasan model tidak dikenal') from None


def plan(client: ChatClient, goal: str, hint: str, image: np.ndarray, cards: dict, bounds: tuple) -> tuple:
    """(steps, stats, analysis) for the frame, or raises AIPlanError; the caller falls back to the built-in plan."""
    zone, how = droppable(image, bounds[1])
    if zone is None:
        raise AIPlanError('Zona deploy tidak terbaca; pakai rencana bawaan')
    jpegs = [encode_jpeg(image)]
    reply = client.complete(build_messages(goal, hint, cards, bounds, jpegs))
    raw = parse_reply(reply)
    steps, stats = compile_plan(raw, cards, bounds, zone)
    stats['zone'] = how
    return steps, stats, str(raw.get('analysis', ''))[:300]


def split_spell_steps(steps: list, spell_x: set) -> tuple[list, list]:
    """(other steps, spell steps): the steps whose card sits at one of the spell slots' x positions."""
    spells = [step for step in steps if 'slot' in step and step['slot'][0] in spell_x]
    return [step for step in steps if not any(step is spell for spell in spells)], spells


def build_spell_messages(goal: str, hint: str, spells: dict, jpegs: list, focus: list | None) -> list:
    """Chat messages for the checkpoint: the battle as it is now and the spells still in the bar."""
    inventory = [{'id': sid, 'count': spell.get('count')} for sid, spell in spells.items()]
    lines = [GOALS.get(goal, GOALS['loot'])]
    if hint and hint.strip():
        lines.append(f'Player note: {hint.strip()}')
    if focus:
        lines.append(f'The army was dropped around x={focus[0]}, y={focus[1]}.')
    lines.append(f'Spells left: {json.dumps(inventory)}. Count null means unknown.')
    content = [{'type': 'text', 'text': '\n'.join(lines)}]
    for jpeg in jpegs:
        url = 'data:image/jpeg;base64,' + base64.b64encode(jpeg).decode('ascii')
        content.append({'type': 'image_url', 'image_url': {'url': url}})
    return [{'role': 'system', 'content': SPELL_PROMPT}, {'role': 'user', 'content': content}]


def spell_plan(client: ChatClient, goal: str, hint: str, image: np.ndarray, spells: dict, bounds: tuple,
               focus: list | None = None) -> tuple:
    """(steps, stats, analysis) for the spells still in the bar, from the battle frame; raises AIPlanError. Spells are
    not kept out of the base, so no zone is applied."""
    reply = client.complete(build_spell_messages(goal, hint, spells, [encode_jpeg(image)], focus))
    raw = parse_reply(reply)
    steps, stats = compile_plan(raw, spells, bounds, None)
    return steps, stats, str(raw.get('analysis', ''))[:300]


SETTINGS_FILE = 'ai.json'
SETTINGS_DEFAULTS = {'enabled': False, 'base_url': '', 'api_key': '', 'model': '', 'goal': 'loot', 'hint': '',
                     'timeout': 20.0, 'max_tokens': 4000, 'spell_checkpoint': True, 'checkpoint_wait': 10.0,
                     'checkpoint_timeout': 15.0}
MAX_HINT = 500


def load_settings(path) -> dict:
    """Saved AI settings merged over the defaults; a missing or unreadable file gives the defaults."""
    settings = dict(SETTINGS_DEFAULTS)
    try:
        saved = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return settings
    if isinstance(saved, dict):
        settings.update({key: saved[key] for key in SETTINGS_DEFAULTS if key in saved})
    return settings


def public_settings(settings: dict) -> dict:
    """What the dashboard may show: everything but the key, plus whether a key is saved."""
    return {key: value for key, value in settings.items() if key != 'api_key'} | {'key_set': bool(settings.get('api_key', settings.get('key_set')))}


def save_settings(path, value: dict) -> dict:
    """Validate and store the settings. The key is kept unless a new one is given; the file is written 0600."""
    import os
    if not isinstance(value, dict):
        raise AIPlanError('Pengaturan AI tidak valid')
    current = load_settings(path)
    merged = {**current, **{key: value[key] for key in SETTINGS_DEFAULTS if key in value}}
    if value.get('api_key'):
        merged['api_key'] = str(value['api_key']).strip()
    if merged['base_url'] and not str(merged['base_url']).startswith(('http://', 'https://')):
        raise AIPlanError('Base URL model harus diawali http:// atau https://')
    if merged['goal'] not in GOALS:
        raise AIPlanError('Pilihan tujuan AI tidak dikenal')
    if not isinstance(merged['hint'], str) or len(merged['hint']) > MAX_HINT:
        raise AIPlanError('Catatan AI terlalu panjang')
    if not isinstance(merged['enabled'], bool):
        raise AIPlanError('Pilihan nyala/mati AI tidak valid')
    if merged['enabled'] and not (merged['base_url'] and merged['model'] and merged['api_key']):
        raise AIPlanError('Isi base URL, nama model, dan API key sebelum menyalakan AI')
    merged['timeout'] = min(max(float(merged['timeout']), 1.0), MAX_TIMEOUT)
    if not isinstance(merged['spell_checkpoint'], bool):
        raise AIPlanError('Pilihan cek ulang spell tidak valid')
    merged['checkpoint_wait'] = min(max(float(merged['checkpoint_wait']), 0.0), 30.0)
    merged['checkpoint_timeout'] = min(max(float(merged['checkpoint_timeout']), 1.0), MAX_TIMEOUT)
    # Reasoning models spend the budget on thinking before the JSON; a small cap cuts the plan off mid-answer.
    if type(merged['max_tokens']) is not int or not 256 <= merged['max_tokens'] <= 16000:
        raise AIPlanError('Batas token model harus 256 sampai 16000')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with open(temporary, 'w', encoding='utf-8', opener=lambda p, f: os.open(p, f, 0o600)) as handle:
        json.dump(merged, handle)
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    return public_settings(merged)


def client_from_settings(settings: dict, transport=None) -> ChatClient | None:
    """A client when the AI is switched on in settings, else None (the built-in plan is used)."""
    if not settings.get('enabled'):
        return None
    return ChatClient(settings['base_url'], settings['api_key'], settings['model'], timeout=settings['timeout'],
                      max_tokens=settings['max_tokens'], transport=transport)
