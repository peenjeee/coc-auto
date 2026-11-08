"""Reporting-only upgrade levels: game export baseline plus confirmed upgrade receipts.

Never used to choose a purchase or bypass its screen/resource/gem checks.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sqlite3
import time

STORE = Path('.coc-auto/upgrade-history.sqlite3')
EXPORT = Path('.coc-auto/village-export.json')
CHECKPOINT = Path('.coc-auto/village-export-checkpoint.json')
INVALID = Path('.coc-auto/upgrade-history-invalid.json')
SECTIONS = {'buildings':('buildings','traps','heroes'), 'lab':('units','siege_machines','spells'),
            'pets':('pets',), 'gear':('equipment',), 'builder':('buildings2','traps2','heroes2'), 'star':('units2',)}


@lru_cache(maxsize=1)
def identities():
    entries = json.loads(Path(__file__).with_name('village-export-catalog.json').read_text(encoding='utf-8'))['items']
    return {(item['list'],item['id']):{'data_id':int(key),**item} for key,item in entries.items() if item['id']}


def canonical_name(listing, row):
    item = identities().get((listing,row.get('id')))
    return item.get('name_id',item['name']) if item else row['name']


def connect(root):
    path = root/STORE
    path.parent.mkdir(parents=True,exist_ok=True)
    db = sqlite3.connect(path,timeout=5)
    db.row_factory = sqlite3.Row
    db.execute('''CREATE TABLE IF NOT EXISTS upgrades (
        id INTEGER PRIMARY KEY, ts REAL NOT NULL, account TEXT NOT NULL, listing TEXT NOT NULL,
        item TEXT NOT NULL, name TEXT NOT NULL, from_level INTEGER, target_level INTEGER,
        seconds INTEGER, evidence TEXT NOT NULL)''')
    return db


def checkpoint(root, data):
    """Mark receipts already represented by this exact export, including same-second upgrades."""
    last = 0
    if (root/STORE).exists():
        db = connect(root)
        try:
            last = db.execute('SELECT COALESCE(MAX(id),0) FROM upgrades WHERE account=?',(data['tag'],)).fetchone()[0]
        finally:
            db.close()
    value = {'sha256':hashlib.sha256((root/EXPORT).read_bytes()).hexdigest(), 'history_id':last}
    path = root/CHECKPOINT
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value),encoding='utf-8')
    temporary.replace(path)
    if (root/INVALID).exists():
        (root/INVALID).write_text('{"sha256":null}',encoding='utf-8')


def invalidate(root):
    """A lost receipt makes further inferred levels unsafe until the next successful sync."""
    source=root/EXPORT
    value={'sha256':hashlib.sha256(source.read_bytes()).hexdigest() if source.exists() else None}
    path=root/INVALID
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value),encoding='utf-8')
    temporary.replace(path)


def baseline(root, listing, key):
    identity = identities().get((listing,key))
    if identity is None:
        return 'unknown',[],None,None
    path = root/EXPORT
    try:
        raw = path.read_bytes()
        data = json.loads(raw)
        tag = data['tag']
        stamp = data['timestamp']
        if not isinstance(tag,str) or type(stamp) is not int:
            return 'unknown',[],None,None
    except (OSError,ValueError,KeyError,TypeError):
        return 'unknown',[],None,None
    pool = []
    for section in SECTIONS[listing]:
        for entry in data.get(section,[]):
            if entry.get('data') != identity['data_id'] or type(entry.get('lvl')) is not int:
                continue
            count = entry.get('cnt',1)
            if type(count) is not int or not 0 < count <= 10000:
                continue
            pending = type(entry.get('timer')) is int
            # A pending export entry already represents an upgrade to lvl+1.
            level = entry['lvl']+int(pending)
            ready = stamp+entry['timer'] if pending else 0
            pool.extend({'level':level,'ready':ready} for _ in range(count))
    watermark = None
    try:
        saved = json.loads((root/CHECKPOINT).read_text(encoding='utf-8'))
        if saved.get('sha256') == hashlib.sha256(raw).hexdigest():
            watermark = saved['history_id']
    except (OSError,ValueError,KeyError):
        pass
    return tag,pool,watermark,max(stamp,path.stat().st_mtime)


def advance(pool, previous, target, ready, now):
    matches = [item for item in pool if item['level']==previous]
    if not matches:
        if len(pool)==1:
            pool[0].update(level=target,ready=ready)
        return
    selected = min(matches,key=lambda item:(item['ready']>now,item['ready']))
    selected.update(level=target,ready=ready)


def record(root, listing, row, seconds=None, *, now=None, allow_inference=True):
    """Append exactly one receipt after confirmation; return a supported target or None.

Unique entities may advance from the last confirmed target. For grouped buildings,
infer only when eligible instances agree; do not guess between different levels.
"""
    now = time.time() if now is None else now
    key = row.get('id') or row['name']
    account,pool,watermark,synced_at = baseline(root,listing,key)
    db = connect(root)
    try:
        db.execute('BEGIN IMMEDIATE')
        prior = db.execute('SELECT * FROM upgrades WHERE account=? AND listing=? AND item=? ORDER BY id',
                           (account,listing,key)).fetchall()
        uncertain = not allow_inference
        try:
            invalid=json.loads((root/INVALID).read_text(encoding='utf-8'))
            if (root/EXPORT).exists() and invalid.get('sha256')==hashlib.sha256((root/EXPORT).read_bytes()).hexdigest():
                uncertain=True
        except (OSError,ValueError):
            pass
        for receipt in prior:
            if watermark is not None:
                if receipt['id'] <= watermark:
                    continue
            elif synced_at is not None and receipt['ts'] <= synced_at:
                continue
            target = receipt['target_level']
            if target is None:
                uncertain = True
                continue
            ready = receipt['ts']+receipt['seconds'] if receipt['seconds'] is not None else float('inf')
            advance(pool,receipt['from_level'],target,ready,now)
        target = row.get('next_level')
        evidence = 'visual'
        if type(target) is not int or not 1 <= target <= 200:
            target = None
            eligible = pool if len(pool)==1 else [item for item in pool if item['ready']<=now]
            levels = {item['level'] for item in eligible}
            if not uncertain and len(levels)==1:
                target = levels.pop()+1
                if target > 200:
                    target = None
                evidence = 'export_and_confirmed_history'
        if target is None:
            evidence = 'unknown'
        duration = seconds if type(seconds) is int and seconds>=0 else None
        db.execute('INSERT INTO upgrades(ts,account,listing,item,name,from_level,target_level,seconds,evidence) '
                   'VALUES(?,?,?,?,?,?,?,?,?)',
                   (now,account,listing,key,row['name'],target-1 if target else None,target,duration,evidence))
        db.commit()
        return target
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
