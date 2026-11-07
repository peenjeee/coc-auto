"""--buildings: the rows picked in the dashboard from the builder list's tabs upgraded, each list's first target first,
up to its level or for as long as the game lists it (Max): Home Village buildings with the builders beyond
keep_builders, Laboratory research and pets with a free slot, hero equipment with its ore. A price is paid only in
gold, elixir, dark elixir or ore, as the list showed it; a running upgrade is waited for and then upgraded on.
Builder Base picks are kept but not upgraded (buildings.DIALOGS)."""
from __future__ import annotations

import logging
import time

import numpy as np

import bot
import buildings
import gear
import messages

LOG = logging.getLogger(__name__)
# Free slots on the open list's tab bar ([x, y, w, h] at 1280x720). The builders' counter there does not read, so the
# Home builders are counted on the village (building_upgrade.free_builders); equipment has no slots, only ore.
SLOTS = {'lab': [1034, 678, 56, 24], 'pets': [1212, 678, 56, 24]}
DONE = {'buildings': 'Bangunan di-upgrade: %s ke level %s', 'lab': 'Riset di-upgrade: %s ke level %s',
        'pets': 'Hewan di-upgrade: %s ke level %s', 'gear': 'Peralatan di-upgrade: %s ke level %s'}


def room(runner: bot.Bot, listing: str, chosen: dict, builders: int) -> bool:
    """Whether the open list can start an upgrade: more builders free than kept for buildings, a free Laboratory or
    pet house slot; equipment always, its ore being checked on the dialog."""
    if listing == 'buildings':
        return builders > chosen['keep_builders']
    if listing not in SLOTS:
        return True
    templates = runner.templates()

    def read(image: np.ndarray) -> int:
        free, total = bot.read_fraction(bot.crop(image, SLOTS[listing]), templates)
        if free > total:
            raise bot.BotError('Jumlah slot bebas meragukan')
        return free
    try:
        return runner.read_stable(read, bot.GEM_READ_SECONDS if runner.poll else 0) > 0
    except bot.BotError as exc:
        raise bot.WaitTimeout(f'Slot bebas {listing} tidak terbaca ({exc}); tidak ada upgrade') from exc


def back_to_list(runner: bot.Bot, dialog: str) -> None:
    """Wait for the list in front again on its tab, where observe() knows it on the builders tab only, or the village
    when the list closed by itself. Any other known screen is left to wait(), which closes the gem offer or the busy
    builders with X and stops the try; an unknown one stops it at the timeout."""
    deadline = time.monotonic() + bot.WALL_TAP_SECONDS
    while True:
        frame = runner.device.capture()
        if buildings.list_in_front(runner, frame):
            return
        try:
            state = runner.detector.detect(frame)
        except bot.UnknownScreen:
            state = None
        if state in (*bot.VILLAGES, 'wall_selected'):
            return
        if state not in (None, dialog, 'build_list'):
            runner.wait(set(), {dialog, 'build_list'}, bot.WALL_TAP_SECONDS)
        if time.monotonic() >= deadline:
            raise bot.WaitTimeout(f'Daftar tidak kembali setelah {dialog}; tidak ada input')
        time.sleep(runner.poll)


def upgrade(runner: bot.Bot, listing: str, spec: dict, frames: list, place: int, row: dict, program: str,
            digits: dict) -> bool:
    """Tap the row with the list in front (the game opens its upgrade dialog) and confirm only when the dialog asks
    the row's own price; False when the dialog was closed unconfirmed. Any other screen after the tap stops the try:
    nothing unidentified is tapped."""
    dialog = buildings.DIALOGS[listing]
    names = (gear.CATALOG, gear.NAME_BAND) if listing == 'gear' else (buildings.LISTS[listing][2], buildings.NAME_BAND)
    y = buildings.find_card(runner, spec, frames, place, row, program, *names)
    if runner.live:
        # Off the builders tab observe() does not know the list, so guarded_tap cannot vouch for it.
        if not buildings.list_in_front(runner, runner.device.capture()):
            raise bot.UnexpectedScreen('Daftar Bangun tertutup layar lain; baris tidak disentuh')
        runner.device.tap_many([[spec['row']['tap_x'], y + buildings.NAME_CENTRE]])
    runner.wait({dialog}, {'build_list', 'wall_selected'}, bot.WALL_TAP_SECONDS)

    def problem(frame: np.ndarray) -> str | None:
        return gear.refusal(frame, row, digits) if dialog == 'gear_confirm' else buildings.refusal(runner, frame, row)

    def confirm(frame: np.ndarray) -> list:
        # Looked at again in the very frame the tap follows.
        again = problem(frame)
        if again:
            raise bot.TargetMissing(again)
        return runner.config['screens'][dialog]['actions']['confirm']
    reason = problem(runner.wait_still(bot.WALL_LIST_SETTLE_SECONDS if runner.poll else 0))
    if reason is None:
        try:
            runner.guarded_tap({dialog}, confirm)
        except bot.TargetMissing as exc:
            reason = str(exc)
    if reason:
        LOG.warning('Dialog upgrade %s tidak sesuai (%s); ditutup tanpa konfirmasi', row['name'], reason)
        runner.action(dialog, 'close')
    back_to_list(runner, dialog)
    return reason is None


def visit(runner: bot.Bot, listing: str, chosen: dict, refused: set, program: str, templates: dict) -> tuple:
    """One look at a list: (whether a row was upgraded, whether a target still has something left, seconds until the
    list's soonest running upgrade is done). The list is opened on its tab, read whole, at most one row tapped, and
    closed from the builders tab again."""
    village, tab, path = buildings.LISTS[listing]
    spec = runner.config['walls'][village]
    runner.navigate(village)
    runner.find_gem_row(spec)
    runner.start_gem_watch(spec)
    builders, amounts = buildings.village(runner)
    image = runner.open_build_list(village, spec)
    indonesian = runner.list_indonesian(image)
    first, done = buildings.BUILDERS_TAB[village], False
    try:
        if tab != first:
            image = buildings.show_tab(runner, tab)
        spare = room(runner, listing, chosen, builders)
        frames = buildings.list_frames(runner, spec, image)
        placed = (gear.placed_rows(frames, spec, templates, program) if listing == 'gear'
                  else buildings.placed_rows(frames, spec, templates, program, path))
        choice, pending = buildings.pick(placed, chosen['targets'], amounts, refused)
        if choice is not None and spare:
            row, place = choice
            try:
                done = upgrade(runner, listing, spec, frames, place, row, program, templates['digits'])
            except bot.NoFreeBuilder as exc:
                LOG.warning('Upgrade %s dilewati: %s', row['name'], exc)
            if done:
                runner.progress += 1
                LOG.info(DONE[listing], row['name'], row['next_level'])
            else:
                refused.add(buildings.row_key(row))
    finally:
        if tab != first:
            # close_menus knows the list by the builders tab only.
            buildings.show_tab(runner, first)
        runner.close_menus()
    if done:
        runner.check_gems(spec)
        if listing == 'buildings':
            after, _ = buildings.village(runner)
            if after >= builders:
                LOG.warning('Tukang bebas tidak berkurang setelah upgrade %s (%s -> %s); cek game', row['name'],
                            builders, after)
    return done, pending, buildings.pause_seconds([item for item, _ in placed], indonesian)


def pause(runner: bot.Bot, seconds_left: int, reason: str) -> None:
    if runner.stop_at is not None:
        seconds_left = max(0, min(seconds_left, round(runner.stop_at - time.monotonic())))
    LOG.info('Menunggu %s detik: %s', seconds_left, reason)
    time.sleep(seconds_left if runner.poll else 0)


def run(runner: bot.Bot, chosen: dict) -> int:
    """--buildings: look at every list with targets in turn (buildings.validate's pick, a LISTS key each) until each
    target reaches its level or leaves its list; the number upgraded. A round that upgraded or refused something is
    followed by the next at once, so the next target goes first; otherwise the run waits for the soonest running
    upgrade, at most buildings.WAIT_MAX_SECONDS."""
    kept = [listing for listing in buildings.LISTS if listing not in buildings.DIALOGS and chosen[listing]['targets']]
    if kept:
        LOG.warning('Pilihan %s tidak di-upgrade: dialog upgrade Desa Malam belum dikenali', ', '.join(kept))
    lists = [listing for listing in buildings.LISTS if listing in buildings.DIALOGS and chosen[listing]['targets']]
    program = bot.executable(runner.config.get('tesseract'), 'Tesseract')
    templates = buildings.load_templates(runner.root, runner.config)
    refused, upgraded = set(), 0
    while lists:
        tried, before, waits, left = len(refused), upgraded, [], []
        for listing in lists:
            runner.check_limits()
            runner.refresh_if_low_memory()
            done, pending, seconds = visit(runner, listing, chosen[listing], refused, program, templates)
            upgraded += done
            if pending:
                left.append(listing)
                waits.append(seconds)
        # A list whose targets are all reached stays so: levels only go up.
        lists = left
        if lists and upgraded == before and len(refused) == tried:
            pause(runner, min(waits), messages.translate('belum ada pilihan yang bisa di-upgrade sekarang'))
            # A dialog that did not match is tried again after the wait; meanwhile the next target went first.
            refused.clear()
    LOG.info('Semua pilihan sudah mencapai target (%s upgrade); selesai', upgraded)
    return upgraded
