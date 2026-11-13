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
    'Coordinates are screen pixels of the 1280x720 frame shown by the grid. Only place troops outside the '
    'enemy base border. Use only the card ids listed. Never exceed a card\'s count when it is known. '
    'Heroes take count 1 and get ability_after. Keep stages short and ordered: funnel first, then main push, '
    'then spells, then heroes if they are needed.'
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


def _point(value: object, bounds: tuple, stats: dict) -> list | None:
    """An [x, y] pixel inside the frame above the card bar; out-of-range points are clamped, not guessed."""
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


def compile_plan(raw: dict, cards: dict, bounds: tuple) -> tuple[list, dict]:
    """Validated plan as deploy steps (the shapes auto_plan returns) and counts of what was clamped or dropped.

    cards: id -> {'slot': [x, y], 'kind': str, 'count': int | None}, from the card bar read.
    bounds: (frame width, card bar top y).
    """
    stages = raw.get('stages')
    if not isinstance(stages, list) or not 1 <= len(stages) <= MAX_STAGES:
        raise AIPlanError('Rencana AI harus berisi 1 sampai 8 tahap')
    stats = {'clamped': 0, 'dropped': 0, 'truncated': 0}
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
            for step in _action_steps(action, cards, bounds, used, stats):
                if len(steps) >= MAX_STEPS:
                    stats['truncated'] += 1
                    continue
                steps.append(step)
        if wait > 0 and len(steps) > first:
            steps[first]['wait'] = wait
    if not steps:
        raise AIPlanError('Rencana AI kosong setelah divalidasi')
    return steps, stats


def _action_steps(action: object, cards: dict, bounds: tuple, used: dict, stats: dict) -> list:
    card_id = action.get('card') if isinstance(action, dict) else None
    card = cards.get(card_id) if isinstance(card_id, str) else None
    if card is None:
        stats['dropped'] += 1
        return []
    points = []
    for place in action.get('place') or []:
        at = _point(place.get('at') if isinstance(place, dict) else None, bounds, stats)
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
    jpegs = [encode_jpeg(image)]
    reply = client.complete(build_messages(goal, hint, cards, bounds, jpegs))
    raw = parse_reply(reply)
    steps, stats = compile_plan(raw, cards, bounds)
    return steps, stats, str(raw.get('analysis', ''))[:300]
