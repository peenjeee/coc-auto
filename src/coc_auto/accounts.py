"""Run on more than one Supercell ID account: load the account asked for from the game's own account list.

A card in that list is tapped only when its name reads exactly as the account asked for and no other card reads the
same. Which village is loaded is told by the player tag in the game's own data export, never by reading the screen:
before a switch, so an account already loaded is left alone, and after it, so a run never goes on in another village.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import subprocess

import cv2
import numpy as np

from . import bot
from . import buildings
from . import upgrade_refresh
from . import village_export
from .paths import runtime_dir

LOG = logging.getLogger(__name__)
NAME, TAG = re.compile(r'[\w.-](?:[\w .-]{0,38}[\w.-])?'), re.compile(r'#[0289PYLQGRJCUV]{3,15}')
MAX_ACCOUNTS = 20
# The profile window and its Settings tab, as the village export opens them.
PROFILE, SETTINGS, SETTINGS_CLOSE = [60, 60], [1010, 60], [1119, 56]
# Settings: the switch icon beside the Supercell ID "Open" button. The account list slides in from the right; its
# logo is in this box only once the list has stopped.
SWITCH_ICON, LIST_LOGO, LIST_CLOSE = (800, 100, 90, 80), (158, 330, 260, 60), [42, 47]
# The list's white account cards on its grey panel: a column clear of names and badges, a card's height, and the most
# room between two account cards. The "Log in to Supercell ID" card sits further down, under its own heading. A card
# that runs off the bottom of the screen still shows its name and this much of itself.
CARD_COLUMN, CARD_HEIGHT, CARD_GAP, CARD_WHITE, CARD_CUT = (1040, 1140), (90, 120), 40, 250, 60
# More accounts than the screen holds: the list is scrolled by a finger on the empty strip left of the cards (from x, y
# to x, y, in milliseconds: slow, so it does not fling), at most this many times. It moved when the cards' area
# changed by this much (mean pixel difference). After a scroll, a band that starts above where the first card sits
# runs under the list's top edge, its name hidden, unless it is a whole card below the list's header.
SCROLL, SCROLL_MS, SCROLL_MAX, LIST_MOVED = (588, 640, 588, 240), 900, 10, 2.0
LIST_AREA, LIST_EDGE, LIST_TOP = (slice(100, 720), slice(601, 1255)), 100, 117
# A card's Supercell ID name, its bold first line: x, then y from the card's top, width and height. A card is tapped
# in its empty middle, and only while its name still shows as it was read: the list at rest repeats pixel for pixel,
# another name there differs by 15 and more (mean pixel difference).
CARD_NAME, CARD_TAP_X, NAME_SAME = (701, 10, 420, 34), 900, 1.0


def account(name: object, tag: object, known: list) -> tuple:
    """One account, checked: its Supercell ID name and its village's player tag, neither already in known."""
    name = name.strip() if isinstance(name, str) else ''
    tag = '#' + tag.strip().lstrip('#').upper() if isinstance(tag, str) else ''
    if not NAME.fullmatch(name) or not TAG.fullmatch(tag):
        raise bot.BotError(f'Akun tidak valid: {name + tag!r}; tulis NamaSupercellID#TAGPEMAIN, pisahkan dengan koma')
    if any(same(name, other) or tag == used for other, used in known):
        raise bot.BotError(f'Akun {name} ({tag}) ditulis dua kali')
    return name, tag


def parse(text: str) -> tuple:
    """--accounts: NAME#TAG,NAME#TAG as (Supercell ID name, player tag) pairs, in the order given."""
    found = []
    for item in text.split(','):
        name, mark, tag = item.rpartition('#')
        found.append(account(name, tag, found) if mark else account(item, '', found))
    return tuple(found)


def registry(value: object) -> list:
    """config.local.json 'accounts', checked: this device's Supercell ID accounts in the order they take turns, each
    {name, tag, enabled}; only the enabled ones are worked on."""
    if not isinstance(value, list) or len(value) > MAX_ACCOUNTS:
        raise bot.BotError(f'Daftar akun harus berisi 0..{MAX_ACCOUNTS} akun')
    found = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {'name', 'tag', 'enabled'} or type(item['enabled']) is not bool:
            raise bot.BotError('Akun perlu name, tag dan enabled (true/false)')
        found.append((*account(item['name'], item['tag'], [entry[:2] for entry in found]), item['enabled']))
    return [{'name': name, 'tag': tag, 'enabled': enabled} for name, tag, enabled in found]


def village_picks(source: object) -> dict:
    """One village's upgrade picks, list by list, from source: config.local.json, or one of its 'account_picks'. A
    list never saved, or edited by hand into something --buildings refuses, is empty."""
    found = {}
    for listing in buildings.LISTS:
        try:
            found[listing] = buildings.selection((source if isinstance(source, dict) else {}).get(listing), listing)
        except bot.BotError:
            found[listing] = {'targets': [], 'keep_builders': 0} if listing == 'buildings' else {'targets': []}
    return found


def picks(config: dict, root, turns: tuple) -> dict:
    """--buildings with accounts: each account's own upgrade picks by player tag, as the dashboard filed them while
    that village was the one on its Upgrade page. An account without picks has no entry and is passed over."""
    filed = config.get('account_picks')
    found = {}
    for _, tag in turns:
        own = filed.get(tag) if isinstance(filed, dict) else None
        if isinstance(own, dict) and any((own.get(listing) or {}).get('targets') for listing in buildings.LISTS):
            found[tag] = buildings.validate({**{key: value for key, value in config.items() if key not in buildings.LISTS},
                                             **{listing: own[listing] for listing in buildings.LISTS if listing in own}},
                                            root)
    if not found:
        raise bot.BotError('Belum ada pilihan upgrade untuk akun yang dipilih; muat akunnya dari halaman Akun, pilih '
                           'upgrade di halaman Upgrade, lalu Simpan')
    return found


def bands(image: np.ndarray) -> list:
    """(top, bottom) of every white band across the card column, top to bottom."""
    white = np.all(image[:, CARD_COLUMN[0]:CARD_COLUMN[1]] >= CARD_WHITE, axis=(1, 2))
    edges = np.flatnonzero(np.diff(np.concatenate(([False], white, [False])).astype(np.int8)))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist()))


def cards(image: np.ndarray) -> list:
    """(top, bottom) of the account cards, top to bottom: white bands across the card column, each a card high and
    close after the last; the last may run off the bottom of the screen, and one cut by the list's top edge is passed
    over. A band of another height or a wider gap ends them, so the log-in card under its heading is never one of
    them, and neither is anything on a list that does not look as calibrated."""
    found = []
    for top, bottom in bands(image):
        if not found and top < LIST_TOP and (top < LIST_EDGE or bottom - top < CARD_HEIGHT[0]):
            continue
        whole = CARD_HEIGHT[0] <= bottom - top <= CARD_HEIGHT[1]
        cut = bottom == image.shape[0] and CARD_CUT <= bottom - top < CARD_HEIGHT[0]
        if not (whole or cut) or (found and top - found[-1][1] > CARD_GAP):
            break
        found.append((top, bottom))
    return found


def end_shown(image: np.ndarray) -> bool:
    """The end of the list is on screen: the log-in card, a card high, further below the band above it than account
    cards are apart."""
    found = bands(image)
    return any(CARD_HEIGHT[0] <= bottom - top <= CARD_HEIGHT[1] and top - above > CARD_GAP
               for (_, above), (top, bottom) in zip(found, found[1:]))


def same(read: str, name: str) -> bool:
    """Two names are the same account when they match letter for letter; only spaces and case are forgiven."""
    return ''.join(read.split()).casefold() == ''.join(name.split()).casefold() != ''


def pick(image: np.ndarray, name: str, read) -> tuple:
    """Of the cards on screen, the one whose name is that account, or None, and every name read; read(card) gives a
    card's name. Two cards that read as that account choose neither."""
    names = [(card, read(card)) for card in cards(image)]
    matches = [card for card, text in names if same(text, name)]
    if len(matches) > 1:
        raise bot.BotError(f'Akun {name} terbaca lebih dari sekali di daftar akun; tidak ada akun dipilih')
    return (matches[0] if matches else None), [text for _, text in names if text]


def name_line(image: np.ndarray, card: tuple) -> np.ndarray:
    x, dy, w, h = CARD_NAME
    return image[card[0] + dy:card[0] + dy + h, x:x + w]


def card_name(image: np.ndarray, card: tuple, program: str) -> str:
    """A card's Supercell ID name through Tesseract: one line of dark text on white."""
    line = name_line(image, card)
    gray = cv2.cvtColor(cv2.resize(line, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC), cv2.COLOR_BGR2GRAY)
    page = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    ok, encoded = cv2.imencode('.png', cv2.copyMakeBorder(page, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255))
    if not ok:
        raise bot.BotError('Tidak bisa menyiapkan crop OCR')
    try:
        result = subprocess.run([program, 'stdin', 'stdout', '--psm', '7', '-l', 'eng'], input=encoded.tobytes(),
                                capture_output=True, check=True, timeout=15, shell=False,
                                **bot.ocr_process_options(program))
    except (OSError, subprocess.SubprocessError) as exc:
        raise bot.BotError('Nama akun tidak bisa dibaca (OCR gagal); tidak ada akun dipilih') from exc
    return ' '.join(result.stdout.decode('utf-8', 'replace').split())


def sync(runner: bot.Bot) -> str:
    """The player tag of the village that is loaded, from the game's own data export. That export becomes this
    device's village, so the dashboard's Upgrade page and the upgrade history follow the account the bot is on."""
    data = village_export.Scanner(runner).read()
    try:
        snapshot = village_export.keep(runner, data)
        if (runtime_dir(root=runner.root) / 'stats.sqlite3').exists():
            upgrade_refresh.keep_scans(runner.root, snapshot)
    except (OSError, ValueError, sqlite3.Error) as exc:
        # Only what the dashboard shows; which account is loaded is known all the same.
        LOG.warning('Data desa tidak tersimpan untuk dashboard (%s)', exc)
    return data['tag']


def close_settings(scanner: village_export.Scanner) -> None:
    scanner.device.tap(SETTINGS_CLOSE)
    scanner.wait(lambda frame: not scanner.profile(frame), 'Pengaturan game tidak tertutup')


def switch(runner: bot.Bot, name: str, tag: str) -> None:
    """Load the account with that Supercell ID name and player tag, from a village screen; an account already loaded
    is left alone. The village that comes up must carry the tag, or the run stops there. Without a name the village
    is only checked: nothing is switched to."""
    before = sync(runner)
    if before == tag:
        LOG.info('Akun %s (%s) sudah dimuat', name or tag, tag)
        return
    if not name:
        raise bot.BotError(f'Desa yang dimuat ({before}) bukan desa di halaman Upgrade ({tag}); muat akunnya dari '
                           'halaman Akun dulu')
    program = bot.executable(runner.config.get('tesseract'), 'Tesseract')
    scanner = village_export.Scanner(runner)
    if runner.detector.detect(scanner.capture()) not in bot.VILLAGES:
        raise bot.BotError('Ganti akun hanya dari layar desa; tidak ada input dikirim')
    scanner.device.tap(PROFILE)
    scanner.wait(scanner.profile, 'Profil game tidak terbuka; akun tidak diganti')
    scanner.device.tap(SETTINGS)
    image, _ = scanner.wait(scanner.main, 'Pengaturan game tidak terbuka; akun tidak diganti')
    icon = scanner.find(image, 'switch', SWITCH_ICON)
    if not icon:
        close_settings(scanner)
        raise bot.BotError('Tombol ganti akun Supercell ID tidak terlihat; akun tidak diganti')
    x, y, w, h = icon
    scanner.device.tap([x + w // 2, y + h // 2])

    def listed(frame: np.ndarray) -> bool:
        return bool(scanner.find(frame, 'supercell', LIST_LOGO) and cards(frame))

    scanner.wait(listed, 'Daftar akun Supercell ID tidak terbuka; akun tidak diganti')
    image = scanner.settle()
    try:
        card, seen = None, []
        for scrolled in range(SCROLL_MAX + 1):
            if not listed(image):
                raise bot.BotError('Daftar akun Supercell ID berubah; tidak ada akun dipilih')
            card, names = pick(image, name, lambda row: card_name(image, row, program))
            seen += [text for text in names if text not in seen]
            if card or end_shown(image) or scrolled == SCROLL_MAX:
                break
            # More accounts further down: the list is scrolled with no finger on a card.
            LOG.info('Akun %s belum terlihat; daftar akun digulir', name)
            scanner.device.command('shell', 'input', 'swipe', *map(str, SCROLL), str(SCROLL_MS))
            moved = scanner.settle()
            if np.abs(moved[LIST_AREA].astype(np.int16) - image[LIST_AREA]).mean() < LIST_MOVED:
                break
            image = moved
        if not card:
            raise bot.BotError(f'Akun {name} tidak ada di daftar akun ({", ".join(seen) or "-"}); tidak ada akun '
                               'dipilih')
        # Reading the names took a moment: the card is tapped only if the list is still the one they were read from.
        now = scanner.capture()
        moved = np.abs(name_line(now, card).astype(np.int16) - name_line(image, card)).mean()
        if not listed(now) or cards(now) != cards(image) or moved >= NAME_SAME:
            raise bot.BotError('Daftar akun Supercell ID berubah; tidak ada akun dipilih')
    except bot.BotError:
        # The list is left through its own cross, and only while it is still the screen in front.
        if listed(scanner.capture()):
            scanner.device.tap(LIST_CLOSE)
            scanner.wait(scanner.main, 'Daftar akun Supercell ID tidak tertutup')
            close_settings(scanner)
        raise
    LOG.info('Ganti akun ke %s (%s)', name, tag)
    scanner.device.tap([CARD_TAP_X, sum(card) // 2])
    # The game loads the other village, at times by closing and starting again; popups on the way are closed like
    # after any restart.
    try:
        runner.settle()
    except bot.GameClosed:
        LOG.warning('CoC menutup sendiri saat memuat akun; dibuka lagi')
        runner.recover()
    found = sync(runner)
    if found == before:
        # The game came back on the account it had: the switch did not take, and is tried again from the start.
        raise bot.WaitTimeout(f'Akun {name} belum termuat (game kembali ke {found})')
    if found != tag:
        raise bot.BotError(f'Akun yang dimuat ({found}) bukan {name} ({tag}); tidak ada input lanjutan')
    LOG.info('Akun %s (%s) dimuat', name, tag)
