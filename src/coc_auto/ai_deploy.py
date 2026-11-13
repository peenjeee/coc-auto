"""AI deploy planner: asks an OpenAI-compatible vision model for a staged drop plan and compiles the answer into the
same step dicts that Bot.deploy_card runs. Reply parsing, validation and compiling are pure; the HTTP call goes
through an injectable transport so tests run offline."""

import base64
import json
import math
import time
from urllib import error, request

import cv2
import numpy as np

from .paths import RESOURCE_ROOT

MAX_STEPS = 40
MAX_TAPS = 400
MAX_TIMEOUT = 30.0
GOALS = {
    'loot': 'Goal: loot. Hit storages and collectors; prefer the edge with the fewest defences.',
    'trophy': 'Goal: trophies. Destroy the Town Hall and as much of the base as possible.',
    'stars': 'Goal: stars. Maximise destruction and take all three stars if possible.',
}


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



T_MAX = 150.0             # latest second of the plan (the battle lasts 180 s from the first drop)
COMPASS = ('east', 'north-east', 'north', 'north-west', 'west', 'south-west', 'south', 'south-east')
TIMELINE_PROMPT = (
    'You plan a Clash of Clans attack as ONE timeline that is run open loop: nobody looks at the battle again, so '
    'estimate how it will unfold (where each troop group is and when it reaches the defences) and time every action '
    'to that estimate. Reply with JSON only, no prose: {"analysis": "<short>", "estimate": [{"t": <seconds>, '
    '"note": "<where the army is>"}], "timeline": [{"t": <seconds>, "do": "drop", "card": "<id>", "point": '
    '"<point id>", "count": <int>}, {"t": <seconds>, "do": "ability", "card": "<hero id>"}, {"t": <seconds>, '
    '"do": "spell", "card": "<id>", "at": [x, y], "count": <int>}]}. A drop puts exactly count units on the point '
    'at that moment, one tap each; split a big card over several moments if you want a trickle. t is seconds from now; the first drop is at t=0 and no action may be later than the time budget. '
    'Troops, siege machines and heroes may only be dropped on the verified drop points listed (each already '
    'tested in this very battle); give the point id, never coordinates. Spells use screen coordinates of the '
    '1280x720 frame and may land anywhere, including over the base. Use only the card ids listed and never more '
    'than the counts shown (counts are what is left after the tests). Heroes take count 1; fire their ability '
    'with an ability action once they are fighting. Funnel first, then the main push, then spells timed to when '
    'the army reaches the defences, using the reference table for speeds and durations (it is approximate). '
    'Rules: spend the whole army and every spell by the end of the timeline; nothing is held back. When three or '
    'more verified points are listed, use at least two different ones: the main push on one point and a funnel or '
    'second wave on another point at least 150 px from it. Fire every hero ability once the hero is fighting. Never '
    'put all spells at one moment; time each to when the army reaches what it should hit.'
)


def load_units(path=None) -> dict:
    """The editable troop/hero/spell reference the prompt hands to the model; {} when it cannot be read."""
    try:
        return json.loads((path or RESOURCE_ROOT / 'ai_units.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def side_label(point: list, centre: tuple) -> str:
    """Compass side of a point around the base centre, as seen on screen (north is up)."""
    angle = (math.degrees(math.atan2(-(point[1] - centre[1]), point[0] - centre[0])) + 360.0) % 360.0
    return COMPASS[int(((angle + 22.5) % 360.0) // 45.0)]


def _spread_order(points: list) -> list:
    """Farthest-point order, so the first few probes sit on different sides of the base."""
    if len(points) < 3:
        return points
    chosen = [points[0]]
    rest = points[1:]
    while rest:
        far = max(rest, key=lambda q: min(math.hypot(q[0] - c[0], q[1] - c[1]) for c in chosen))
        chosen.append(far)
        rest.remove(far)
    return chosen


def base_centre(image: np.ndarray, bar_top: int) -> tuple | None:
    """Centre of the enemy base on screen: the middle of its object hull, else of its line hull."""
    for hull in (object_hull(image, bar_top), line_hull(image)):
        if hull is not None:
            ys, xs = np.nonzero(hull)
            return float(xs.mean()), float(ys.mean())
    return None


def candidate_points(zone: np.ndarray, count: int, centre: tuple | None = None,
                     min_gap: float = 70.0) -> tuple[list, tuple | None]:
    """(points, centre): up to count spread-out proposals on the droppable ground, one per angular sector around the
    base centre (the ground's own centre when none is given), taken on the base side of the ring, in a spread-out
    order. Proposals only: the game decides which of them are valid, by accepting or refusing a probe drop."""
    ground = cv2.erode(zone.astype(np.uint8), np.ones((13, 13), np.uint8)) > 0
    if not ground.any():
        ground = zone
    ys, xs = np.nonzero(ground)
    if len(xs) == 0 or count < 1:
        return [], None
    centre = centre or (float(xs.mean()), float(ys.mean()))
    angle = np.arctan2(ys - centre[1], xs - centre[0])
    radius = np.hypot(xs - centre[0], ys - centre[1])
    picked = []
    for sector in range(count):
        low = -math.pi + 2.0 * math.pi * sector / count
        inside = (angle >= low) & (angle < low + 2.0 * math.pi / count)
        if not inside.any():
            continue
        near = np.nonzero(inside)[0]
        index = near[int(np.argmin(np.abs(radius[near] - np.percentile(radius[near], 30))))]
        point = [int(xs[index]), int(ys[index])]
        if all(math.hypot(point[0] - q[0], point[1] - q[1]) >= min_gap for q in picked):
            picked.append(point)
    return _spread_order(picked), centre


def _plain_point(value: object, bounds: tuple, stats: dict) -> list | None:
    """An [x, y] pixel inside the frame above the card bar; out-of-range values are clamped, not guessed."""
    if not isinstance(value, list) or len(value) != 2:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or v != v for v in value):
        return None
    width, bar_top = bounds
    x, y = int(round(value[0])), int(round(value[1]))
    clamped = [min(max(x, 0), width - 1), min(max(y, 0), bar_top - 1)]
    if clamped != [x, y]:
        stats['clamped'] += 1
    return clamped


def compile_timeline(raw: dict, cards: dict, points: dict, bounds: tuple, t_max: float = T_MAX) -> tuple[list, dict]:
    """The model's timeline as deploy steps, each with an 'at' offset in seconds, plus counts of what was clamped,
    dropped or truncated. No point is moved: troop drops use only the verified point ids, spells may use any
    coordinate.

    cards: id -> {'slot', 'kind', 'count'} (counts after the probe drops). points: verified id -> [x, y].
    """
    timeline = raw.get('timeline')
    if not isinstance(timeline, list) or not timeline:
        raise AIPlanError('Rencana AI harus berisi linimasa')
    stats = {'clamped': 0, 'dropped': 0, 'truncated': 0}
    entries = []
    for entry in timeline:
        moment = _number(entry.get('t'), t_max) if isinstance(entry, dict) else None
        if moment is None:
            stats['dropped'] += 1
            continue
        entries.append((moment, entry))
    entries.sort(key=lambda item: item[0])   # stable: equal times keep the model's order
    steps, used = [], {}
    for moment, entry in entries:
        card_id = entry.get('card')
        card = cards.get(card_id) if isinstance(card_id, str) else None
        action = entry.get('do')
        step = None
        if card is None:
            pass
        elif action == 'drop' and card['kind'] in ('troop', 'siege', 'hero', 'warden'):
            spot = points.get(entry.get('point')) if isinstance(entry.get('point'), str) else None
            count = 1 if card['kind'] in ('hero', 'warden') else entry.get('count')
            if spot is not None and isinstance(count, int) and not isinstance(count, bool) and count >= 1:
                budget = (card['count'] if card.get('count') is not None else MAX_TAPS) - used.get(card_id, 0)
                count = min(count, MAX_TAPS, max(budget, 0))
                if count >= 1:
                    used[card_id] = used.get(card_id, 0) + count
                    # Always exact taps: a held finger drops "until the card is empty" and would ignore the count.
                    step = {'slot': card['slot'], 'count': count, 'points': [list(spot)] * count, 'burst': count}
        elif action == 'ability' and card['kind'] in ('hero', 'warden'):
            step = {'slot': card['slot'], 'count': 1, 'tap_only': True}
        elif action == 'spell' and card['kind'] == 'spell':
            spot = _plain_point(entry.get('at'), bounds, stats)
            count = entry.get('count', 1)
            if spot is not None and isinstance(count, int) and not isinstance(count, bool) and count >= 1:
                budget = (card['count'] if card.get('count') is not None else MAX_TAPS) - used.get(card_id, 0)
                count = min(count, max(budget, 0))
                if count >= 1:
                    used[card_id] = used.get(card_id, 0) + count
                    step = {'slot': card['slot'], 'count': count, 'points': [spot] * count, 'burst': count}
        if step is None:
            stats['dropped'] += 1
        elif len(steps) >= MAX_STEPS:
            stats['truncated'] += 1
        else:
            step['at'] = round(moment, 2)
            steps.append(step)
    if not steps:
        raise AIPlanError('Rencana AI kosong setelah divalidasi')
    return steps, stats


def build_timeline_messages(goal: str, hint: str, cards: dict, points: list, bounds: tuple, jpegs: list,
                            t_left: float, probe: dict | None = None, units: dict | None = None) -> list:
    """Chat messages for the one planning call: the frame, the verified drop points, what is left in the bar."""
    inventory = [{'id': cid, 'kind': c['kind'], 'count': c.get('count')} for cid, c in cards.items()]
    lines = [GOALS.get(goal, GOALS['loot'])]
    if hint and hint.strip():
        lines.append(f'Player note: {hint.strip()}')
    lines.append(f'Time budget: {int(t_left)} seconds from now; no action later than that.')
    lines.append(f'Frame width {bounds[0]}; the card bar starts at y {bounds[1]}.')
    lines.append(f'Verified drop points (troops, siege and heroes go only here): {json.dumps(points)}')
    if probe:
        lines.append(f'One unit of card {probe["card"]} was already dropped on each verified point as a test; '
                     'those units are fighting.')
    lines.append(f'Cards left: {json.dumps(inventory)}. Count null means unknown.')
    if units:
        lines.append(f'Reference (approximate): {json.dumps(units, separators=(",", ":"))}')
    content = [{'type': 'text', 'text': '\n'.join(lines)}]
    for jpeg in jpegs:
        url = 'data:image/jpeg;base64,' + base64.b64encode(jpeg).decode('ascii')
        content.append({'type': 'image_url', 'image_url': {'url': url}})
    return [{'role': 'system', 'content': TIMELINE_PROMPT}, {'role': 'user', 'content': content}]


def _estimate(raw: dict) -> list:
    """The model's progression estimate, trimmed for the log: [{'t': seconds, 'note': text}]."""
    items = raw.get('estimate')
    out = []
    for item in items[:12] if isinstance(items, list) else []:
        if isinstance(item, dict) and isinstance(item.get('note'), str):
            moment = _number(item.get('t'), 600.0)
            out.append({'t': moment if moment is not None else 0.0, 'note': item['note'][:160]})
    return out


def plan_timeline(client: 'ChatClient', goal: str, hint: str, image: np.ndarray, cards: dict, points: list,
                  bounds: tuple, t_left: float, probe: dict | None = None, units: dict | None = None,
                  deadline: float | None = None) -> tuple[list, dict, dict]:
    """(steps, stats, info) from one model call on the post-probe frame; raises AIPlanError. A reply that is not
    valid JSON or leaves nothing after validation is asked for once more if the deadline (time.monotonic) allows;
    a connection or HTTP failure is not retried.

    points: [{'id': 'P1', 'at': [x, y], 'side': 'west'}] verified drop points."""
    messages = build_timeline_messages(goal, hint, cards, points, bounds, [encode_jpeg(image)], t_left, probe, units)
    spots = {item['id']: item['at'] for item in points}
    last = None
    for attempt in range(2):
        if attempt and deadline is not None and time.monotonic() >= deadline:
            break
        reply = client.complete(messages)
        try:
            raw = parse_reply(reply)
            steps, stats = compile_timeline(raw, cards, spots, bounds, min(T_MAX, float(t_left)))
        except AIPlanError as exc:
            last = exc
            continue
        return steps, stats, {'analysis': str(raw.get('analysis', ''))[:300], 'estimate': _estimate(raw),
                              'reply': reply[:4000], 'attempts': attempt + 1}
    raise last or AIPlanError('Rencana AI tidak diterima')


def encode_jpeg(image: np.ndarray, max_edge: int = 1024, quality: int = 80) -> bytes:
    height, width = image.shape[:2]
    scale = min(1.0, max_edge / max(width, height))
    if scale < 1.0:
        image = cv2.resize(image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
    ok, buffer = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise AIPlanError('Gambar untuk model tidak bisa dikodekan')
    return buffer.tobytes()



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



HISTORY_FILE = 'ai-history.jsonl'
HISTORY_MAX_BYTES = 1_000_000


def history_entry(record: dict, model: str, goal: str, damage: dict | None, seconds: float | None) -> dict:
    """One line for the outcome log, from the deploy record: what was probed, what the model planned and how long it
    took, how the timeline ran and how the battle ended. No key, no image, no model text beyond the short analysis."""
    probe = record.get('probe') or {}
    steps, ran = record.get('steps') or [], record.get('ran') or []
    late = [round(item['actual'] - item['at'], 1) for item in ran if 'actual' in item and 'at' in item]
    info = record.get('info') or {}
    stats = record.get('stats') or {}
    spells = {card['slot'][0] for card in (record.get('cards') or {}).values() if card.get('kind') == 'spell'}
    return {
        'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'model': model, 'goal': goal,
        'stopped': record.get('stopped'),
        'candidates': len(record.get('candidates') or []),
        'probe_tried': probe.get('tried'), 'probe_verified': len(probe.get('verified') or []),
        'probe_spent': probe.get('spent'), 'probe_seconds': probe.get('seconds'),
        'model_seconds': record.get('model_seconds'), 'attempts': info.get('attempts'),
        'steps': len(steps), 'dropped': stats.get('dropped'), 'clamped': stats.get('clamped'),
        'units': sum(step.get('count', 0) for step in steps if step.get('points') and step['slot'][0] not in spells),
        'spells': sum(step.get('count', 0) for step in steps if step.get('points') and step['slot'][0] in spells),
        'steps_run': len(ran), 'max_late': max(late) if late else None,
        'last_step_at': steps[-1]['at'] if steps else None,
        'damage_last': (damage or {}).get('last'), 'damage_max': (damage or {}).get('max'),
        'battle_seconds': seconds,
        'analysis': (info.get('analysis') or '')[:200],
    }


def append_history(path, entry: dict, max_bytes: int = HISTORY_MAX_BYTES) -> None:
    """Append one JSON line. When the file is over max_bytes it is moved to '<name>.1' first (one old file is kept)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.is_file() and path.stat().st_size > max_bytes:
            path.replace(path.with_name(path.name + '.1'))
    except OSError:
        pass
    with open(path, 'a', encoding='utf-8') as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + '\n')


def load_history(path) -> list:
    """The entries of the log, oldest first; lines that are not valid JSON objects are skipped."""
    entries = []
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return entries
    for line in lines:
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            entries.append(value)
    return entries


def summarize_history(entries: list) -> dict:
    """Totals for judging the AI over many battles: how many ran, how many stopped and why, and the mean damage, probe
    result and model time of the ones that ran a plan."""
    ran = [entry for entry in entries if not entry.get('stopped')]
    damages = [entry['damage_last'] for entry in ran if isinstance(entry.get('damage_last'), (int, float))]
    mean = lambda values: round(sum(values) / len(values), 1) if values else None
    reasons = {}
    for entry in entries:
        if entry.get('stopped'):
            reasons[entry['stopped']] = reasons.get(entry['stopped'], 0) + 1
    return {'battles': len(entries), 'planned': len(ran), 'stopped': reasons, 'damage_mean': mean(damages),
            'damage_max': max(damages) if damages else None,
            'probe_verified_mean': mean([entry['probe_verified'] for entry in ran if entry.get('probe_verified') is not None]),
            'model_seconds_mean': mean([entry['model_seconds'] for entry in ran if entry.get('model_seconds')])}


SETTINGS_FILE = 'ai.json'
SETTINGS_DEFAULTS = {'enabled': False, 'base_url': '', 'api_key': '', 'model': '', 'goal': 'loot', 'hint': '',
                     'timeout': 20.0, 'max_tokens': 4000, 'probe_points': 6, 'probe_max': 12, 't_max': 150.0}
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
    # Reasoning models spend the budget on thinking before the JSON; a small cap cuts the plan off mid-answer.
    if type(merged['max_tokens']) is not int or not 256 <= merged['max_tokens'] <= 16000:
        raise AIPlanError('Batas token model harus 256 sampai 16000')
    for key, low, high, message in (('probe_points', 2, 12, 'Jumlah titik uji harus 2 sampai 12'),
                                    ('probe_max', 2, 20, 'Batas percobaan titik harus 2 sampai 20')):
        if type(merged[key]) is not int or not low <= merged[key] <= high:
            raise AIPlanError(message)
    if merged['probe_max'] < merged['probe_points']:
        raise AIPlanError('Batas percobaan titik harus 2 sampai 20')
    merged['t_max'] = min(max(float(merged['t_max']), 30.0), 170.0)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with open(temporary, 'w', encoding='utf-8', opener=lambda p, f: os.open(p, f, 0o600)) as handle:
        json.dump(merged, handle)
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    return public_settings(merged)


def test_model(settings: dict, transport=None) -> float:
    """Seconds one tiny request with a small image took, to check the endpoint, key, model and image support from the
    saved settings (the AI does not have to be switched on). Raises AIPlanError with a reason that never holds the key."""
    if not (settings.get('base_url') and settings.get('model') and settings.get('api_key')):
        raise AIPlanError('Isi base URL, nama model, dan API key sebelum menyalakan AI')
    client = ChatClient(settings['base_url'], settings['api_key'], settings['model'],
                        timeout=min(float(settings.get('timeout', 20.0)), 20.0), max_tokens=64, transport=transport)
    jpeg = encode_jpeg(np.full((64, 64, 3), 128, np.uint8))
    url = 'data:image/jpeg;base64,' + base64.b64encode(jpeg).decode('ascii')
    content = [{'type': 'text', 'text': 'Reply with the single word ok.'},
               {'type': 'image_url', 'image_url': {'url': url}}]
    started = time.monotonic()
    client.complete([{'role': 'user', 'content': content}])
    return round(time.monotonic() - started, 1)


def client_from_settings(settings: dict, transport=None) -> ChatClient | None:
    """A client when the AI is switched on in settings, else None (the built-in plan is used)."""
    if not settings.get('enabled'):
        return None
    return ChatClient(settings['base_url'], settings['api_key'], settings['model'], timeout=settings['timeout'],
                      max_tokens=settings['max_tokens'], transport=transport)
