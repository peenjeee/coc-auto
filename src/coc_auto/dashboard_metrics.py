"""Persistent dashboard statistics derived from the worker's canonical, confirmed log events.
Opponent loot offers are deliberately excluded. Resource gains are positive storage deltas.
"""
import ast
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
import time

RESOURCES = ('gold', 'elixir', 'dark', 'builder_gold', 'builder_elixir')
FIELDS = (*RESOURCES, 'walls', 'upgrades', 'attacks', 'recoveries', 'searches', 'errors')

class Statistics:
    def __init__(self, path=None, clock=time.time):
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.clock, self.lock = clock, threading.RLock()
        self.db = sqlite3.connect(str(path) if path else ':memory:', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY, started REAL, ended REAL, task TEXT, status TEXT);
            CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, run INTEGER, ts REAL,
            kind TEXT, detail TEXT, gold INTEGER DEFAULT 0, elixir INTEGER DEFAULT 0,
            dark INTEGER DEFAULT 0, walls INTEGER DEFAULT 0, attacks INTEGER DEFAULT 0,
            recoveries INTEGER DEFAULT 0, searches INTEGER DEFAULT 0, errors INTEGER DEFAULT 0);
            CREATE INDEX IF NOT EXISTS events_time ON events(ts);
            CREATE INDEX IF NOT EXISTS events_run ON events(run);
            CREATE TABLE IF NOT EXISTS scans (village TEXT, list TEXT, scanned_at REAL, rows TEXT,
            PRIMARY KEY(village, list));''')
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(events)')}
        for column in ('builder_gold', 'builder_elixir', 'resource_version', 'upgrades'):
            if column not in columns:
                self.db.execute(f'ALTER TABLE events ADD COLUMN {column} INTEGER DEFAULT 0')
        # Older resource totals mixed villages. Keep the history but do not assign those sums to Home.
        # New resource rows are explicitly versioned; ambiguous legacy amounts remain unreported.
        # An interrupted agent must not leave a previous run accumulating time forever.
        self.db.execute("UPDATE runs SET status='interrupted', ended=COALESCE((SELECT MAX(ts) FROM events WHERE run=runs.id), started) WHERE ended IS NULL")
        self.db.commit()
        self.baselines = {}

    def start(self, args):
        task = ('Wall upgrades' if '--walls' in args else 'Building upgrades' if '--buildings' in args else
                'Farming' if any(x in args for x in ['--until-full', '--home', '--builder']) else 'Attacks')
        with self.lock, self.db:
            run = self.db.execute('INSERT INTO runs(started,task,status) VALUES(?,?,?)', (self.clock(), task, 'running')).lastrowid
            self.baselines[run] = {}
            return run

    def finish(self, run, code):
        if run is None: return
        with self.lock, self.db:
            self.db.execute('UPDATE runs SET ended=?,status=? WHERE id=?',
                (self.clock(), 'completed' if code == 0 else 'stopped' if code in [-2, 130, -1073741510] else 'failed', run))
            self.baselines.pop(run, None)

    def record(self, run, kind, detail='', **values):
        if run is None: return
        values = {k: int(v) for k, v in values.items() if k in FIELDS and isinstance(v, (int, float)) and 0 <= v <= 10**12}
        with self.lock, self.db:
            keys = list(values)
            self.db.execute(f"INSERT INTO events(run,ts,kind,detail,resource_version{''.join(','+k for k in keys)}) VALUES({','.join('?' for _ in range(5+len(keys)))})",
                [run, self.clock(), kind, detail[:160], 1, *values.values()])

    def keep_scan(self, village, listing, rows, scanned_at):
        """Keep only the latest scan of one in-game list, e.g. the Home Village builder list ('home', 'buildings')."""
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO scans(village,list,scanned_at,rows) VALUES(?,?,?,?)',
                            (village, listing, scanned_at, json.dumps(rows)))

    def last_scan(self, village, listing):
        """The latest scan of that list; rows None when there is none or it does not read back as one."""
        with self.lock:
            row = self.db.execute('SELECT scanned_at,rows FROM scans WHERE village=? AND list=?', (village, listing)).fetchone()
        try:
            rows = json.loads(row['rows']) if row else None
        except (TypeError, ValueError):
            rows = None
        scanned_at = row['scanned_at'] if row else None
        if (isinstance(rows, list) and all(isinstance(item, dict) for item in rows)
                and isinstance(scanned_at, (int, float)) and math.isfinite(scanned_at)):
            return {'scanned_at': scanned_at, 'rows': rows}
        return {'rows': None}

    def consume(self, line, run):
        if run is None: return None
        text = re.sub(r'^(?:INFO|WARNING|ERROR|CRITICAL):\s*', '', line)
        attack = re.fullmatch(r'(?:Serangan selesai|Attack finished): (home|builder) \(\d+\)', text)
        bulk = re.fullmatch(r'(\d+) (?:tembok di-upgrade sekaligus dengan|walls upgraded at once with) (gold|elixir)', text)
        wall = re.fullmatch(r'(?:Tembok di-upgrade dengan|Wall upgraded with) (gold|elixir)', text)
        storage = re.fullmatch(r'(?:Gudang|Storage) (home|builder): (\{.{1,600}\})', text)
        upgrade = re.fullmatch(r'(?:(?:Bangunan|Riset|Hewan|Peralatan) di-upgrade|(?:Building|Research|Pet|Equipment) '
                               r'upgraded): (.{1,60}?)(?: (?:ke|to) level (\d{1,3}|None))?', text)
        if attack:
            self.record(run, 'attack', 'Home Village attack' if attack[1] == 'home' else 'Builder Base attack', attacks=1)
            return 'attack'
        if wall or bulk or upgrade:
            count = int(bulk[1]) if bulk else 1
            if count > 100000: return None
            if upgrade:
                detail = (f'{upgrade[1]} upgraded to level {upgrade[2]}'
                          if upgrade[2] not in (None,'None') else f'{upgrade[1]} upgraded')
                self.record(run, 'upgrade', detail, upgrades=1)
            else:
                self.record(run, 'walls', f'{count:,} wall' + ('s' if count != 1 else '') + ' upgraded', walls=count)
            # Spending must not make the next farming session's baseline hide its gains.
            with self.lock: self.baselines.setdefault(run, {}).clear()
            return 'upgrade' if upgrade else 'walls'
        if re.fullmatch(r'(?:Akun .{1,60} \(#\w{3,15}\) dimuat|Account .{1,60} \(#\w{3,15}\) loaded)', text):
            # --accounts loaded another village: its storages are not the last one's, so their difference is no gain.
            with self.lock: self.baselines.setdefault(run, {}).clear()
            return None
        if storage:
            try:
                amounts = ast.literal_eval(storage[2])
                if not isinstance(amounts, dict): return None
                amounts = {k: int(str(v).split('/')[0].replace(',', '')) for k,v in amounts.items() if k in ('gold','elixir','dark')}
                if any(not 0 <= v <= 999999999 for v in amounts.values()): return None
            except (ValueError, SyntaxError, TypeError): return None
            with self.lock:
                previous = self.baselines.setdefault(run, {}).setdefault(storage[1], {})
                new_keys = set(amounts) - set(previous)
                metric_key = lambda key: f'builder_{key}' if storage[1] == 'builder' else key
                if new_keys: self.record(run, 'baseline', ','.join(sorted(metric_key(k) for k in new_keys)))
                gains = {k:max(0,v-previous[k]) for k,v in amounts.items() if k in previous}
                previous.update(amounts)
                if any(gains.values()): self.record(run, 'resources', ('Builder Base' if storage[1] == 'builder' else 'Home Village') + ' storage increased', **{metric_key(k):v for k,v in gains.items()})
            return 'storage'
        if re.search(r'(?:pemulihan|recovery) \d+/\d+', text, re.I) and line.startswith('WARNING:'):
            self.record(run, 'recovery', 'Recovery attempt', recoveries=1)
            return 'recovery'
        if line.startswith(('ERROR:', 'CRITICAL:')):
            self.record(run, 'error', 'Worker error', errors=1)
            return 'error'
        # The loot read here is an offer on an opponent, not earned resources.
        if text.startswith(('Loot terbaca:', 'Loot read:')):
            self.record(run, 'search', 'Opponent checked', searches=1)
        return None

    def snapshot(self, period='run', run_id=None):
        now = self.clock()
        if period not in ('run','24h','7d'): period='run'
        with self.lock:
            last = self.db.execute('SELECT id FROM runs ORDER BY id DESC LIMIT 1').fetchone()
            start = now - (86400 if period=='24h' else 604800)
            where, params = ('run=?', [run_id if run_id is not None else last['id'] if last else -1]) if period=='run' else ('ts>=?', [start])
            totals = dict(self.db.execute('SELECT '+','.join(f'COALESCE(SUM({"CASE WHEN resource_version=1 THEN "+k+" ELSE 0 END" if k in RESOURCES else k}),0) AS {k}' for k in FIELDS)+' FROM events WHERE '+where, params).fetchone())
            runs_where, runs_params = ('id=?', params) if period=='run' else ('COALESCE(ended,?)>=?', [now,start])
            runs = [dict(r) for r in self.db.execute('SELECT * FROM runs WHERE '+runs_where+' ORDER BY id DESC',runs_params)]
            # Sum only the selected time window, not the full lifetime of a crossing run.
            duration = sum(max(0,min(r['ended'] or now,now)-max(r['started'],start if period!='run' else r['started'])) for r in runs)
            earliest = self.db.execute('SELECT MIN(ts) FROM events WHERE '+where,params).fetchone()[0]
            lower = earliest or now
            width = max(60, int((now-lower)/18)) if period=='run' else (3600 if period=='24h' else 21600)
            buckets = [dict(r) for r in self.db.execute('SELECT CAST(ts/? AS INTEGER)*? AS ts,'+','.join(f'SUM({"CASE WHEN resource_version=1 THEN "+k+" ELSE 0 END" if k in RESOURCES else k}) AS {k}' for k in FIELDS)+' FROM events WHERE '+where+' GROUP BY 1 ORDER BY 1', [width,width,*params])]
            recent = [dict(r) for r in self.db.execute('SELECT ts,kind,detail FROM events WHERE '+where+" AND kind NOT IN ('search','baseline') ORDER BY id DESC LIMIT 8",params)]
            run_count = len(runs)
            runs = runs[:30]
            for r in runs:
                r['duration'] = max(0,(r['ended'] or now)-r['started'])
                counts = self.db.execute('SELECT COALESCE(SUM(attacks),0),COALESCE(SUM(walls),0) FROM events WHERE run=?',[r['id']]).fetchone()
                r['attacks'],r['walls']=counts
            measured = sorted({key for row in self.db.execute('SELECT detail FROM events WHERE '+where+" AND kind='baseline' AND resource_version=1",params) for key in row['detail'].split(',')})
        return {'totals':totals,'buckets':buckets,'recent':recent,'runs':runs,'duration':round(duration), 'period':period,'run_count':run_count,
                'attacks_per_hour':round(totals['attacks']*3600/duration,1) if duration else 0,
                'measured':measured,'updated_at':now,'resource_basis':'storage_increases'}
