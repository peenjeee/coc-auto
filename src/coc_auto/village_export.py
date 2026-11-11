"""Read the game's own village JSON via Profile > Settings > More Settings > Data Export.

Only navigation is visual. Levels, counts and running timers never go through OCR.
LDPlayer synchronizes the game's Copy button to the Windows clipboard.
"""
from __future__ import annotations
from .images import read_image, write_image
from .paths import runtime_dir

from .paths import RESOURCE_ROOT

import ctypes
from functools import lru_cache
import json
import logging
import os
from pathlib import Path
import re
import time

import cv2
import numpy as np

from . import bot

LOG = logging.getLogger(__name__)
MAX_BYTES = 1024 * 1024
NAV_SECONDS, CLIPBOARD_SECONDS, MAX_SWIPES = 8, 5, 8
MATCH_LIMIT = 0.04
SECTIONS = {'buildings': ('buildings','traps','heroes'), 'lab': ('units','siege_machines','spells'),
            'pets': ('pets',), 'gear': ('equipment',), 'builder': ('buildings2','traps2','heroes2'), 'star': ('units2',)}


class ExportError(bot.BotError):
    pass


def windows_clipboard() -> tuple[int, str]:
    """Read Unicode without clearing/replacing the user's clipboard or spawning PowerShell."""
    if os.name != 'nt':
        raise ExportError('Automatic village export requires Windows with LDPlayer clipboard synchronization.')
    from ctypes import wintypes
    user, kernel = ctypes.WinDLL('user32', use_last_error=True), ctypes.WinDLL('kernel32', use_last_error=True)
    user.GetClipboardSequenceNumber.restype = wintypes.DWORD
    user.OpenClipboard.argtypes, user.OpenClipboard.restype = [wintypes.HWND], wintypes.BOOL
    user.GetClipboardData.argtypes, user.GetClipboardData.restype = [wintypes.UINT], wintypes.HANDLE
    kernel.GlobalLock.argtypes, kernel.GlobalLock.restype = [wintypes.HGLOBAL], ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalSize.argtypes, kernel.GlobalSize.restype = [wintypes.HGLOBAL], ctypes.c_size_t
    sequence = user.GetClipboardSequenceNumber()
    if not user.OpenClipboard(None):
        return sequence, ''  # Another app briefly owns it; the bounded reader retries.
    try:
        handle = user.GetClipboardData(13)  # CF_UNICODETEXT
        size = kernel.GlobalSize(handle) if handle else 0
        if not handle or not 0 < size <= MAX_BYTES * 2:
            return sequence, ''
        address = kernel.GlobalLock(handle)
        if not address:
            return sequence, ''
        try:
            text = ctypes.wstring_at(address, size // 2).split('\0', 1)[0]
        finally:
            kernel.GlobalUnlock(handle)
        return (sequence, text) if sequence == user.GetClipboardSequenceNumber() else (sequence, '')
    finally:
        user.CloseClipboard()


def whole(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ExportError(f'Invalid village export {name}.')
    return value


def parse(text: str, *, now: float, copied_after: float) -> dict:
    """Reject stale/unrelated/partial clipboard content before replacing any saved scan."""
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_BYTES:
        raise ExportError('Village export is too large or is not text.')
    try:
        data = json.loads(text.lstrip('\ufeff'))
    except (ValueError, RecursionError) as exc:
        raise ExportError('The game did not copy valid village JSON.') from exc
    if not isinstance(data, dict) or not isinstance(data.get('tag'), str) or not re.fullmatch(r'#[A-Z0-9]{3,15}', data['tag']):
        raise ExportError('Clipboard text is not a village export.')
    stamp = whole(data.get('timestamp'), 1, 10**11, 'timestamp')
    if not copied_after - 5 <= stamp <= now + 5 or now - stamp > 30:
        raise ExportError('The clipboard export is stale; no saved scans were changed.')
    for sections in SECTIONS.values():
        for name in sections:
            entries = data.get(name)
            if not isinstance(entries, list) or len(entries) > 5000:
                raise ExportError(f'Village export is missing or has invalid {name}.')
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ExportError(f'Invalid village export row in {name}.')
                whole(entry.get('data'), 1, 2**31-1, 'game ID')
                if 'lvl' in entry:
                    whole(entry['lvl'], 0, 200, 'level')
                elif not isinstance(entry.get('types'), list) or not entry['types']:
                    raise ExportError('A village export item has no level.')
                whole(entry.get('cnt', 1), 1, 10000, 'count')
                if 'timer' in entry:
                    whole(entry['timer'], 0, 5*365*86400, 'timer')
    return data


@lru_cache(maxsize=1)
def catalog() -> dict:
    return json.loads((RESOURCE_ROOT / 'village-export-catalog.json').read_text(encoding='utf-8'))['items']


def rows(data: dict, *, language: str = 'en') -> dict[str, list[dict]]:
    """Keep each level/count group separate; never infer a missing level, price or timer."""
    result = {}
    for listing, sections in SECTIONS.items():
        found = []
        for section in sections:
            for entry in data[section]:
                identity = catalog().get(str(entry['data']))
                if identity is not None and identity['list'] != listing:
                    raise ExportError(f'Game ID {entry["data"]} appears in an unexpected export section.')
                identity = identity or {'id':None, 'category':'other', 'name':f'Game item {entry["data"]}'}
                level = entry.get('lvl')
                running = 'timer' in entry
                row = {'id':identity['id'], 'data_id':entry['data'], 'category':identity['category'],
                       'name':identity.get('name_id',identity['name']) if language == 'id' else identity['name'],
                       'count':entry.get('cnt',1), 'current_level':level,
                       'next_level':level+1 if level is not None else None,
                       'section':'in_progress' if running else 'other', 'source':'game_export',
                       'selectable':identity['id'] is not None and level is not None and identity['id'] != 'wall',
                       'time':'', 'cost':None, 'currency':None}
                if running:
                    row['seconds'] = entry['timer']
                if 'types' in entry:
                    row['modules'] = entry['types']
                found.append(row)
        result[listing] = sorted(found, key=lambda r:(r['section'] != 'in_progress',r['name'],
                                                    r['current_level'] if r['current_level'] is not None else -1))
    return result


class Scanner:
    def __init__(self, runner: bot.Bot, *, clipboard=windows_clipboard, sleep=time.sleep,
                 clock=time.monotonic, wall_clock=time.time):
        self.runner, self.device = runner, runner.device
        self.clipboard, self.sleep, self.clock, self.wall_clock = clipboard, sleep, clock, wall_clock
        self.templates = {}
        for path in (runner.root/'calibration/export').glob('*.png'):
            image = read_image(str(path))
            if image is not None:
                self.templates[path.stem] = image
        self.last = None
        self.captures = self.swipes = 0

    def capture(self):
        self.last = self.device.capture()
        self.captures += 1
        if self.last.shape[:2] != (720,1280):
            raise ExportError('Village export navigation requires the calibrated 1280x720 screen.')
        return self.last

    def find(self, image, prefix, region):
        x,y,w,h = region
        area = image[y:y+h,x:x+w]
        for name, template in self.templates.items():
            if name != prefix and not name.startswith(prefix+'-'):
                continue
            if template.shape[0] > h or template.shape[1] > w:
                continue
            score, _, point, _ = cv2.minMaxLoc(cv2.matchTemplate(area,template,cv2.TM_SQDIFF_NORMED))
            if score <= MATCH_LIMIT:
                return (x+point[0], y+point[1],template.shape[1],template.shape[0])
        return None

    def profile(self, image):
        return self.find(image,'close',(1090,20,60,70))

    def more(self, image):
        return (self.profile(image) and self.find(image,'settings',(938,20,150,76))
                and self.find(image,'back',(75,20,50,85)))

    def main(self, image):
        return self.profile(image) and self.find(image,'more',(535,600,220,70))

    def wait(self, predicate, message):
        deadline = self.clock()+NAV_SECONDS
        while True:
            image = self.capture()
            try:
                result = predicate(image)
            except bot.UnknownScreen:
                result = None
            if result:
                return image, result
            if self.clock() >= deadline:
                raise ExportError(message)
            self.sleep(.2)

    def settle(self):
        previous = self.capture()
        deadline, same = self.clock()+2, 0
        while self.clock() < deadline:
            self.sleep(.1)
            current = self.capture()
            difference = np.abs(current[150:630,260:1035].astype(np.int16)-previous[150:630,260:1035]).mean()
            same = same+1 if difference < 1 else 0
            if same >= 2:
                return current
            previous = current
        return previous

    def read(self) -> dict:
        started = self.clock()
        export_requested = self.wall_clock()
        self.clipboard()  # Check platform support before sending any game input.
        error = None
        try:
            image = self.capture()
            if not (self.main(image) or self.more(image)):
                state = self.runner.detector.detect(image)
                if state == 'idle':
                    self.runner.wait({'home','builder'},set(),30)
                    image = self.capture()
                    state = self.runner.detector.detect(image)
                if state in bot.MENU_SCREENS:
                    self.runner.close_menus()
                    image = self.capture()
                    state = self.runner.detector.detect(image)
                if state not in ('home','builder'):
                    raise ExportError('Return to your village before exporting game data.')
                self.device.tap([60,60])
                self.wait(self.profile,'The game profile did not open.')
                self.device.tap([1010,60])
                image,_ = self.wait(lambda f:self.main(f) or self.more(f),'Game Settings did not open.')
            if not self.more(image):
                self.device.tap([640,637])
                image,_ = self.wait(self.more,'More Settings did not open.')
            for attempt in range(MAX_SWIPES+1):
                if not self.more(image):
                    raise ExportError('More Settings changed unexpectedly; export stopped.')
                match = self.find(image,'export',(260,150,780,480))
                if match:
                    break
                if attempt == MAX_SWIPES:
                    raise ExportError('Data Export was not found; no settings were changed.')
                self.device.command('shell','input','swipe','650','600','650','200','350')
                self.swipes += 1
                image = self.settle()
            # The text identifies Data Export, not the adjacent API-token or account buttons.
            _,y,_,height = match
            target_y = y+height//2
            button = cv2.cvtColor(image[max(0,target_y-12):target_y+12,850:1010],cv2.COLOR_BGR2HSV)
            if not ((button[...,0] >= 30) & (button[...,0] <= 90) & (button[...,1] > 70)).mean() > .5:
                raise ExportError('The Data Export Copy button was not ready.')
            old_sequence,_ = self.clipboard()
            self.device.tap([930,target_y])
            deadline, last_problem = self.clock()+CLIPBOARD_SECONDS, None
            while self.clock() < deadline:
                sequence,text = self.clipboard()
                if sequence != old_sequence and text:
                    try:
                        # CoC prepares the JSON when its profile opens, before the Copy tap.
                        data = parse(text,now=self.wall_clock(),copied_after=export_requested)
                        break
                    except ExportError as exc:
                        last_problem = exc
                self.sleep(.1)
            else:
                raise ExportError(str(last_problem) if last_problem else
                                  'The game export did not reach Windows. Enable LDPlayer clipboard synchronization and retry.')
            LOG.info('Village JSON export read in %.2fs (%d screenshots, %d scrolls)',
                     self.clock()-started,self.captures,self.swipes)
            return data
        except BaseException as exc:
            error = exc
            folder = runtime_dir(root=self.runner.root)/'export-diagnostics'
            folder.mkdir(parents=True,exist_ok=True)
            if self.last is not None:
                write_image(str(folder/'last-failure.png'),self.last)
            raise
        finally:
            # Close only a positively identified profile/settings window.
            if self.last is not None and self.profile(self.last):
                try:
                    current = self.capture()
                    if self.profile(current):
                        self.device.tap([1119,56])
                        self.wait(lambda f:self.runner.detector.detect(f) in ('home','builder'),
                                  'Game data was copied, but the village did not reappear.')
                except Exception:
                    if error is None:
                        raise


def scan(runner: bot.Bot) -> dict:
    data = Scanner(runner).read()
    converted = rows(data,language=runner.config.get('language','en'))
    store = runtime_dir(root=runner.root)/'village-export.json'
    store.parent.mkdir(parents=True,exist_ok=True)
    temporary = store.with_suffix('.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    temporary.replace(store)
    from . import upgrade_history
    upgrade_history.checkpoint(runner.root,data)
    return {'scanned_at':data['timestamp'], 'lists':converted}
