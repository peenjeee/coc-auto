"""--buildings: the rows picked in the dashboard from the builder list's tabs upgraded, each list's first target first,
up to its level or for as long as the game lists it (Max): Home Village buildings with the builders beyond
keep_builders, Laboratory research and pets with a free slot, hero equipment with its ore. A price is paid only in
gold, elixir, dark elixir or ore, as the list showed it; a running upgrade is waited for and then upgraded on. A
picked locked row, and a building whose dialog points at another one first, is worked toward the way a player does:
the game's own green button selects the building it waits for, which is upgraded at the price its button shows. The
only gems ever paid hire the Goblin Builder or Goblin Researcher, each only with its switch on and up to max_gems.
Builder Base picks are kept but not upgraded (buildings.DIALOGS)."""
from __future__ import annotations

import logging
import time

import cv2
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
RULES_OFF = {'keep_builders': 0, 'goblin': buildings.GOBLIN_OFF}
# The Goblin offers' gem icons, searched in the band of their green buttons ([x, y, w, h]): a large one on the Goblin
# Researcher's single button, a smaller one on each of the builders' offer's two ("Goblin Builder", "Free a Builder").
# Each button's price is the white number just left of its gem, within this many gem widths.
GOBLIN_GEMS = ('calibration/goblin_offer-gem.png', 'calibration/goblin_offer-gem-small.png')
GOBLIN_BUTTONS, GOBLIN_GEM_SCORE, GOBLIN_PRICE_WIDTH = [600, 370, 580, 130], 0.03, 2.3
# A selected building's hammer button shows the map through it, so only its whitest digits are read.
BUTTON_PRICE_SATURATION = 30
# A building's upgrade dialog that waits for another building first: a red "Note!" bar over the grey confirm button
# with a green "Go" ("Ayo") button at its right ([x, y, w, h] at 1280x720 and the least red and green pixels there; a
# live one showed 33 000 red and 5 000 green, a plain dialog none of either).
NOTE_BAR, NOTE_GO, NOTE_MIN_RED, NOTE_MIN_GREEN = [600, 495, 560, 90], [990, 500, 180, 80], 15000, 3000
# "Upgrade Locked" (a Town Hall waiting for buildings to be built): the band of the required buildings' blue tiles
# ([x, y, w, h]), their height and width range; the list it then opens marks that building's row with a white border
# down these columns.
REQUIRED_TILES, TILE_HEIGHT, TILE_WIDTH, MARKED_COLUMNS = [200, 420, 880, 160], (70, 120), (80, 130), (944, 947)
MARK_WHITE, MARK_MIN_ROWS = 230, 30


def goblin_gems(rules: dict, builder: bool) -> int:
    """The most gems a Goblin of that kind may be paid: max_gems with its switch on, otherwise none."""
    goblin = rules['goblin']
    return goblin['max_gems'] if goblin['builder' if builder else 'researcher'] else 0


def room(runner: bot.Bot, listing: str, rules: dict, builders: int, builds: bool = False) -> bool:
    """Whether the open list can start an upgrade: for buildings, or for the building a locked row waits for (builds),
    more builders free than kept, or none free at all with the Goblin Builder allowed; a free Laboratory or pet house
    slot of the house itself (the Goblin Researcher shows as one more slot, "1/2", used only with its switch on);
    equipment always, its ore being checked on the dialog."""
    if listing == 'buildings' or builds:
        return builders > rules['keep_builders'] or (rules['goblin']['builder'] and builders == 0)
    if listing not in SLOTS:
        return True
    templates = runner.templates()

    def read(image: np.ndarray) -> int:
        free, total = bot.read_fraction(bot.crop(image, SLOTS[listing]), templates)
        if free > total:
            raise bot.BotError('Jumlah slot bebas meragukan')
        return free if rules['goblin']['researcher'] else free - (total - 1)
    try:
        return runner.read_stable(read, bot.GEM_READ_SECONDS if runner.poll else 0) > 0
    except bot.BotError as exc:
        raise bot.WaitTimeout(f'Slot bebas {listing} tidak terbaca ({exc}); tidak ada upgrade') from exc


def goblin_buttons(runner: bot.Bot, image: np.ndarray) -> list:
    """(price, tap) of every gem button on a Goblin offer, left to right; a price that does not read is None."""
    left, top, _, _ = GOBLIN_BUTTONS
    band, found = bot.crop(image, GOBLIN_BUTTONS), []
    for name in GOBLIN_GEMS:
        gem = cv2.imread(str(bot.asset_path(runner.root, name)))
        h, w = gem.shape[:2]
        scores = cv2.matchTemplate(band, gem, cv2.TM_SQDIFF_NORMED)
        for y, x in sorted(zip(*np.where(scores <= GOBLIN_GEM_SCORE)), key=lambda spot: scores[spot]):
            if all(abs(left + int(x) - other) >= w for other, _, _, _ in found):
                found.append((left + int(x), top + int(y), w, h))
    buttons = []
    for x, y, w, h in sorted(found):
        width = round(GOBLIN_PRICE_WIDTH * w)
        try:
            price = bot.read_trailing_number(bot.crop(image, [x - width, y, width, h]), runner.templates(),
                                             bot.FRACTION_MIN_SCORE)
        except bot.BotError:
            price = None
        buttons.append((price, [x + w // 2, y + h // 2]))
    return buttons


def cheapest(runner: bot.Bot, image: np.ndarray, gems: int) -> tuple:
    """(price, tap) of the offer's cheapest gem button, only when every button's price reads cleanly and that one is
    at most gems; TargetMissing otherwise."""
    buttons = goblin_buttons(runner, image)
    prices = [price for price, _ in buttons]
    if not buttons or None in prices or not 0 < min(prices) <= gems:
        raise bot.TargetMissing(messages.translate(f'harga Goblin {prices}, maksimal {gems}'))
    return min(buttons)


def hire(runner: bot.Bot, gems: int, name: str) -> bool:
    """Pay the Goblin offer's cheapest button (the Goblin Builder or Researcher, or freeing a builder) only when every
    price reads cleanly and that one is at most gems, the same price again in the very frame the tap follows; the gem
    watch then lets exactly that drop pass once. Otherwise closed with X."""
    try:
        price, _ = cheapest(runner, runner.wait_still(bot.WALL_LIST_SETTLE_SECONDS if runner.poll else 0), gems)

        def pay(frame: np.ndarray) -> list:
            again, tap = cheapest(runner, frame, gems)
            if again != price:
                raise bot.TargetMissing(messages.translate(f'harga Goblin {again}, maksimal {gems}'))
            return tap
        runner.guarded_tap({'goblin_offer'}, pay)
    except bot.TargetMissing as exc:
        LOG.warning('Tawaran Goblin untuk %s ditutup tanpa membayar (%s)', name, exc)
        runner.action('goblin_offer', 'close')
        return False
    runner.gems_paid = price
    LOG.info('Goblin disewa: %s permata untuk %s', price, name)
    return True


def back_to_list(runner: bot.Bot, dialog: str, gems: int = 0, name: str = '') -> str | None:
    """Wait for the list in front again on its tab, where observe() knows it on the builders tab only, or the village
    when the list closed by itself. A Goblin offer is paid once when gems allow it (hire); the dialog back in front
    after that (a builder freed for gems, the upgrade not started yet) is returned for its confirm to be checked again.
    Any other known screen is left to wait(), which closes the gem offers, the busy builders and an unpaid Goblin with
    X and stops the try; an unknown one stops it at the timeout."""
    deadline, hired = time.monotonic() + bot.WALL_TAP_SECONDS, False
    while True:
        frame = runner.device.capture()
        if buildings.list_in_front(runner, frame):
            return None
        try:
            state = runner.detector.detect(frame)
        except bot.UnknownScreen:
            state = None
        if state in (*bot.VILLAGES, 'wall_selected'):
            return None
        if state == dialog and hired:
            return dialog
        if state == 'goblin_offer' and gems and not hired:
            if not hire(runner, gems, name):
                raise bot.NoFreeBuilder('Tukang atau Lab sibuk; tawaran Goblin ditutup tanpa membayar gem')
            hired = True
            continue
        if state not in (None, dialog, 'build_list'):
            runner.wait(set(), {dialog, 'build_list'}, bot.WALL_TAP_SECONDS)
        if time.monotonic() >= deadline:
            raise bot.WaitTimeout(f'Daftar tidak kembali setelah {dialog}; tidak ada input')
        time.sleep(runner.poll)


def tap_row(runner: bot.Bot, spec: dict, y: int) -> None:
    if runner.live:
        # Off the builders tab observe() does not know the list, so guarded_tap cannot vouch for it.
        if not buildings.list_in_front(runner, runner.device.capture()):
            raise bot.UnexpectedScreen('Daftar Bangun tertutup layar lain; baris tidak disentuh')
        runner.device.tap_many([[spec['row']['tap_x'], y + buildings.NAME_CENTRE]])


def settle(runner: bot.Bot, dialog: str, problem, gems: int, name: str) -> bool:
    """Confirm the open upgrade dialog only while problem finds nothing wrong, in a still frame and again in the very
    frame the tap follows; otherwise close it with X. Then wait for the list or the village again (back_to_list).
    False when it was closed unconfirmed."""
    def confirm(frame: np.ndarray) -> list:
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
        LOG.warning('Dialog upgrade %s tidak sesuai (%s); ditutup tanpa konfirmasi', name, reason)
        runner.action(dialog, 'close')
    if back_to_list(runner, dialog, gems if reason is None else 0, name) == dialog:
        # Freed for gems, the builder still has to be given the upgrade: checked and confirmed as before, unpaid.
        return settle(runner, dialog, problem, 0, name)
    return reason is None


def note_go(image: np.ndarray) -> list | None:
    """Where the green Go button of an upgrade dialog's red "Note!" bar is; None without such a bar."""
    hsv = cv2.cvtColor(bot.crop(image, NOTE_BAR), cv2.COLOR_BGR2HSV)
    hue, saturation, value = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    if int((((hue <= 8) | (hue >= 170)) & (saturation > 120) & (value > 150)).sum()) < NOTE_MIN_RED:
        return None
    hsv = cv2.cvtColor(bot.crop(image, NOTE_GO), cv2.COLOR_BGR2HSV)
    green = (hsv[..., 0] >= 33) & (hsv[..., 0] <= 50) & (hsv[..., 1] > 100) & (hsv[..., 2] > 180)
    if int(green.sum()) < NOTE_MIN_GREEN:
        return None
    ys, xs = np.nonzero(green)
    return [NOTE_GO[0] + int(xs.mean()), NOTE_GO[1] + int(ys.mean())]


def blocked(image: np.ndarray) -> str | None:
    return messages.translate('dialog menunggu bangunan lain dulu') if note_go(image) else None


def hammer(runner: bot.Bot, image: np.ndarray, spec: dict) -> tuple:
    """(resource, price, tap) of the one upgrade button beside the selected building, its price white and read whole;
    TargetMissing otherwise (no button: maxed or upgrading; a red price; more than one button)."""
    hits = runner.button_hits(image, spec, spec['button'])
    if len(hits) != 1:
        raise bot.TargetMissing(messages.translate(f'{len(hits)} tombol upgrade terlihat'))
    resource, tap, affordable, _, band = hits[0]
    if not affordable:
        raise bot.TargetMissing(messages.translate(f'harga {resource} di tombol upgrade merah'))
    try:
        price = bot.read_trailing_number(bot.crop(image, band), runner.templates(), bot.FRACTION_MIN_SCORE,
                                         BUTTON_PRICE_SATURATION)
    except bot.BotError as exc:
        raise bot.TargetMissing(messages.translate('harga di tombol upgrade tidak terbaca')) from exc
    return resource, price, tap


def prerequisite(runner: bot.Bot, village: str, spec: dict, amounts: dict, gems: int, name: str) -> bool:
    """After the game's green button: the building it selected on the village upgraded through its hammer button at
    the price that button shows, covered by the resource on hand, on its own upgrade dialog confirmed like a row's.
    False, with nothing paid, when any of it is not so."""
    runner.wait({village}, {'locked_info', 'building_confirm', 'build_list'}, bot.WALL_TAP_SECONDS)
    try:
        resource, price, _ = runner.read_until(lambda image: hammer(runner, image, spec),
                                               bot.WALL_PRICE_SECONDS if runner.poll else 0)
        if price > amounts.get(resource, 0):
            raise bot.TargetMissing(messages.translate(f'harga {price} {resource} melebihi yang ada'))

        def locate(frame: np.ndarray) -> list:
            found = hammer(runner, frame, spec)
            if found[:2] != (resource, price):
                raise bot.TargetMissing(messages.translate('tombol upgrade berubah'))
            return found[2]
        runner.guarded_tap({village}, locate)
    except bot.TargetMissing as exc:
        LOG.warning('Bangunan syarat %s tidak di-upgrade (%s)', name, exc)
        return False
    runner.wait({'building_confirm'}, {village}, bot.WALL_TAP_SECONDS)
    expected = {'cost': price, 'currency': resource}
    done = settle(runner, 'building_confirm',
                  lambda frame: blocked(frame) or buildings.refusal(runner, frame, expected), gems, name)
    if done:
        LOG.info('Bangunan di-upgrade untuk membuka %s', name)
    return done


def reopen(runner: bot.Bot, village: str, spec: dict) -> None:
    """The list opened again after the game closed it on another tab: it may come back on that tab, where only its own
    close button tells it is open, so its builders tab, the one close_menus knows, is shown again."""
    def opened(image: np.ndarray) -> np.ndarray:
        if not buildings.list_in_front(runner, image):
            raise bot.UnexpectedScreen('Daftar Bangun belum terbuka')
        return image
    runner.guarded_tap({village}, spec['build'])
    runner.read_until(opened, bot.WALL_TAP_SECONDS if runner.poll else 0)
    buildings.show_tab(runner, buildings.BUILDERS_TAB[village])


def follow_locked(runner: bot.Bot, listing: str, spec: dict, amounts: dict, gems: int, name: str) -> bool:
    """The locked row just tapped: its info dialog's green button closes the list and selects the building it waits
    for, which is upgraded (prerequisite). The list is left on its builders tab or closed."""
    village, tab, _ = buildings.LISTS[listing]
    runner.wait({'locked_info'}, {'build_list', 'wall_selected'}, bot.WALL_TAP_SECONDS)
    runner.action('locked_info', 'go')
    try:
        return prerequisite(runner, village, spec, amounts, gems, name)
    finally:
        if tab != buildings.BUILDERS_TAB[village]:
            reopen(runner, village, spec)


def required(runner: bot.Bot, spec: dict, amounts: dict, gems: int, name: str) -> bool:
    """"Upgrade Locked": the row's building waits for others to be built. The first required building's tile is
    tapped, the game opens the list at that building's row marked with a white border, and that row, a locked one, is
    worked toward (follow_locked). Anything else is closed and nothing is paid."""
    frame = runner.wait_still(bot.WALL_LIST_SETTLE_SECONDS if runner.poll else 0)
    if not required_tiles(frame):
        LOG.warning('Bangunan yang dibutuhkan %s tidak terlihat; ditutup', name)
        runner.action('upgrade_locked', 'close')
        return False

    def tile(image: np.ndarray) -> list:
        tiles = required_tiles(image)
        if not tiles:
            raise bot.TargetMissing(messages.translate('ubin bangunan yang dibutuhkan hilang'))
        return tiles[0]
    runner.guarded_tap({'upgrade_locked'}, tile)
    runner.read_until(runner.listed, bot.WALL_TAP_SECONDS if runner.poll else 0)
    y = marked_row(runner.wait_still(bot.WALL_LIST_SETTLE_SECONDS if runner.poll else 0), spec)
    if y is None:
        LOG.warning('Baris bertanda untuk %s bukan baris terkunci; tidak disentuh', name)
        return False
    tap_row(runner, spec, y)
    return follow_locked(runner, 'buildings', spec, amounts, gems, name)


def required_tiles(image: np.ndarray) -> list:
    """Tap points of the required buildings' blue tiles on "Upgrade Locked", left to right."""
    x, y, _, _ = REQUIRED_TILES
    hsv = cv2.cvtColor(bot.crop(image, REQUIRED_TILES), cv2.COLOR_BGR2HSV)
    blue = (hsv[..., 0] >= 90) & (hsv[..., 0] <= 112) & (hsv[..., 1] > 120) & (hsv[..., 2] > 150)
    taps = []
    for top, bottom in buildings.runs(blue.sum(axis=1) >= 20):
        if not TILE_HEIGHT[0] <= bottom - top <= TILE_HEIGHT[1]:
            continue
        # The tile's foot is plain blue all across; its icon and count break the rows above.
        foot = blue[bottom - 12:bottom - 4].sum(axis=0) >= 4
        taps += [[x + (left + right) // 2, y + (top + bottom) // 2] for left, right in buildings.runs(foot)
                 if TILE_WIDTH[0] <= right - left <= TILE_WIDTH[1]]
    return taps


def marked_row(image: np.ndarray, spec: dict) -> int | None:
    """y of the locked row the game marked with a white border, from the top of its card; None when the marked row is
    not a locked one or there is none."""
    _, top, _, h = spec['row']['region']
    left, right = MARKED_COLUMNS
    white = bot.crop(image, [left, top, right - left, h]).min(axis=2).min(axis=1) >= MARK_WHITE
    marks = [(top + start, top + end) for start, end in buildings.runs(white) if end - start >= MARK_MIN_ROWS]
    locked = [y for kind, y in buildings.items(image, spec['row']['region']) if kind == 'locked']
    # The border's corners are rounded, so its left side starts a few rows below the card's top.
    return next((y for y in locked for start, end in marks if start - buildings.SAME_ROW_PX <= y < end), None)


def upgrade(runner: bot.Bot, listing: str, spec: dict, frames: list, place: int, row: dict, program: str,
            digits: dict, rules: dict = RULES_OFF, amounts: dict | None = None) -> bool:
    """Tap the row with the list in front (the game opens its upgrade dialog) and confirm only when the dialog asks
    the row's own price; False when the dialog was closed unconfirmed. A building's dialog that points at another
    building first (a red note with Go, or the Town Hall's "Upgrade Locked") has that one upgraded instead, never its
    own grey confirm. Any other screen after the tap stops the try: nothing unidentified is tapped."""
    dialog = buildings.DIALOGS[listing]
    builds, amounts = listing == 'buildings', amounts or {}
    names = (gear.CATALOG, gear.NAME_BAND) if listing == 'gear' else (buildings.LISTS[listing][2], buildings.NAME_BAND)
    y = buildings.find_card(runner, spec, frames, place, row, program, *names)
    tap_row(runner, spec, y)
    shown = runner.wait({dialog, 'upgrade_locked'} if builds else {dialog}, {'build_list', 'wall_selected'},
                        bot.WALL_TAP_SECONDS)
    if shown == 'upgrade_locked':
        return required(runner, spec, amounts, goblin_gems(rules, True), row['name'])
    if builds and note_go(runner.wait_still(bot.WALL_LIST_SETTLE_SECONDS if runner.poll else 0)) is not None:
        def go(frame: np.ndarray) -> list:
            found = note_go(frame)
            if found is None:
                raise bot.TargetMissing(messages.translate('tombol Go catatan hilang'))
            return found
        try:
            runner.guarded_tap({dialog}, go)
        except bot.TargetMissing:
            pass  # The dialog changed under the tap: settle looks at it again and closes it unless it is a plain one.
        else:
            return prerequisite(runner, 'home', spec, amounts, goblin_gems(rules, True), row['name'])

    def problem(frame: np.ndarray) -> str | None:
        if dialog == 'gear_confirm':
            return gear.refusal(frame, row, digits)
        return blocked(frame) or buildings.refusal(runner, frame, row)
    done = settle(runner, dialog, problem, 0 if listing == 'gear' else goblin_gems(rules, builds), row['name'])
    if done:
        LOG.info(DONE[listing], row['name'], row['next_level'])
    return done


def unlock(runner: bot.Bot, listing: str, spec: dict, frames: list, place: int, row: dict, program: str,
           rules: dict, amounts: dict) -> bool:
    """A picked locked row worked toward: tapped with the list in front, it opens its info dialog, whose green button
    selects the building it waits for, upgraded at the price its button shows (follow_locked)."""
    names = (gear.CATALOG, gear.NAME_BAND) if listing == 'gear' else (buildings.LISTS[listing][2], buildings.NAME_BAND)
    y = buildings.find_card(runner, spec, frames, place, row, program, *names)
    tap_row(runner, spec, y)
    return follow_locked(runner, listing, spec, amounts, goblin_gems(rules, True), row['name'])


def visit(runner: bot.Bot, listing: str, chosen: dict, refused: set, program: str, templates: dict,
          rules: dict = RULES_OFF) -> tuple:
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
    first, done, builds = buildings.BUILDERS_TAB[village], False, listing == 'buildings'
    try:
        if tab != first:
            image = buildings.show_tab(runner, tab)
        frames = buildings.list_frames(runner, spec, image)
        placed = (gear.placed_rows(frames, spec, templates, program) if listing == 'gear'
                  else buildings.placed_rows(frames, spec, templates, program, path))
        choice, pending = buildings.pick(placed, chosen['targets'], amounts, refused)
        locked = choice is not None and choice[0]['section'] == 'locked'
        builds = builds or locked
        if choice is not None and room(runner, listing, rules, builders, locked):
            row, place = choice
            try:
                done = (unlock(runner, listing, spec, frames, place, row, program, rules, amounts) if locked else
                        upgrade(runner, listing, spec, frames, place, row, program, templates['digits'], rules,
                                amounts))
            except bot.NoFreeBuilder as exc:
                LOG.warning('Upgrade %s dilewati: %s', row['name'], exc)
            if done:
                runner.progress += 1
            else:
                refused.add(buildings.row_key(row))
    finally:
        if tab != first:
            # close_menus knows the list by the builders tab only.
            buildings.show_tab(runner, first)
        runner.close_menus()
    if done:
        runner.check_gems(spec)
        if builds and builders:
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
    rules = {'keep_builders': chosen['buildings']['keep_builders'],
             'goblin': buildings.goblin(runner.config.get('goblin', buildings.GOBLIN_OFF))}
    refused, upgraded = set(), 0
    while lists:
        tried, before, waits, left = len(refused), upgraded, [], []
        for listing in lists:
            runner.check_limits()
            runner.refresh_if_low_memory()
            done, pending, seconds = visit(runner, listing, chosen[listing], refused, program, templates, rules)
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
