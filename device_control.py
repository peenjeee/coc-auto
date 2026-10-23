"""CoC and LDPlayer controls for the dashboard: open or close the game, quit or start LDPlayer, and put the
instance on the screen and language the bot was calibrated for (1280x720 DPI 240, Indonesian), then back to the
user's own when the bot is left."""
from __future__ import annotations

import json
from pathlib import Path
import re
import time
from typing import Callable

import ldplayer

DISPLAY_FILE = Path('.coc-auto') / 'display.json'
LANGUAGE_FILE = Path('.coc-auto') / 'language.json'
LD_STOP_SECONDS, LD_SETTLE_SECONDS = 30, 2
DISPLAY_LIMITS = {'width': (320, 7680), 'height': (240, 4320), 'dpi': (72, 640)}
GAME_PACKAGE = 'com.supercell.clashofclans'
BOT_LOCALE = 'id-ID'
LOCALE_TAG = re.compile(r'[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*')
APP_LOCALES = re.compile(r'are \[([^\]]*)\]')


class SettingRefused(Exception):
    """A screen or language change that cannot be made as asked; nothing was changed."""


def saved_file(store: Path) -> dict | None:
    try:
        data = json.loads(store.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def keep(store: Path, data: dict) -> None:
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps(data), encoding='utf-8')


class Display:
    """Puts the LDPlayer instance on the screen the bot was calibrated on (resolution and DPI) and back on the
    user's own. The user's screen is kept in ~/.coc-auto/display.json until it is put back, and only the first one
    is kept, so switching twice never loses it. LDPlayer reads its config when it starts and writes it when it
    quits, so the file is changed only while the instance is off."""

    def __init__(self, config_path: Path, store: Path | None = None, sleep: Callable[[float], None] = time.sleep,
                 emulator: Callable | None = None) -> None:
        self.config_path, self.store = config_path, store or Path.home() / DISPLAY_FILE
        self.sleep, self.make_emulator = sleep, emulator

    def instance(self) -> tuple:
        import bot
        config = bot.load_config(self.config_path)
        console, index = bot.executable(config.get('ldconsole'), 'ldconsole'), config.get('ld_index', 0)
        path = ldplayer.instance_config(console, index)
        if not path.is_file():
            raise SettingRefused(f'config LDPlayer tidak ditemukan: {path}')
        width, height = config['resolution']
        target = {'width': width, 'height': height, 'dpi': config.get('dpi', ldplayer.CALIBRATED_DPI)}
        return (self.make_emulator or bot.Emulator)(console, index), path, target

    def saved(self) -> dict | None:
        return saved_file(self.store)

    def status(self) -> dict:
        _, path, target = self.instance()
        return {'current': ldplayer.read_display(path), 'target': target, 'saved': self.saved() is not None}

    def fit(self) -> bool:
        """False when the screen already fits; otherwise LDPlayer is restarted on the calibrated screen. ADB
        debugging is opened on the way, since the bot cannot reach LDPlayer without it; a restore leaves it open."""
        emulator, path, target = self.instance()
        current = ldplayer.read_display(path)
        if current == target and ldplayer.adb_debug(path) != 0:
            return False
        if self.saved() is None and current != target:
            keep(self.store, {'path': str(path), 'display': current})
        self.rewrite(emulator, path, target, relaunch=True, adb_debug=1)
        return True

    def restore(self) -> bool:
        """False when nothing was switched; LDPlayer is started again only if it was on."""
        saved = self.saved()
        if saved is None:
            return False
        display = saved.get('display')
        if (not isinstance(saved.get('path'), str) or not isinstance(display, dict) or set(display) != set(DISPLAY_LIMITS)
                or not all(isinstance(display[name], int) and not isinstance(display[name], bool)
                           and low <= display[name] <= high for name, (low, high) in DISPLAY_LIMITS.items())):
            raise SettingRefused(f'setelan layar tersimpan rusak; atur layar LDPlayer manual ({self.store})')
        emulator, path, _ = self.instance()
        if Path(saved['path']) != path:
            raise SettingRefused(f'setelan layar tersimpan untuk instance lain ({saved["path"]})')
        self.rewrite(emulator, path, display, relaunch=emulator.running())
        self.store.unlink()
        return True

    def rewrite(self, emulator, path: Path, display: dict, relaunch: bool, adb_debug: int | None = None) -> None:
        if emulator.running():
            emulator.quit()
            for _ in range(LD_STOP_SECONDS):
                if not emulator.running():
                    break
                self.sleep(1)
            else:
                raise SettingRefused('LDPlayer tidak berhenti; tutup manual lalu coba lagi')
            self.sleep(LD_SETTLE_SECONDS)  # LDPlayer writes its own config as it closes
        ldplayer.write_display(path, display, adb_debug)
        if relaunch:
            emulator.launch()


def app_locales(text: str) -> list:
    """CoC's own language list from `cmd locale get-app-locales`; empty means it follows Android's language."""
    found = APP_LOCALES.search(text)
    if found is None:
        raise SettingRefused(f'bahasa CoC tidak terbaca ({text.strip()[:80]}); butuh LDPlayer dengan Android 13+')
    tags = [tag.strip() for tag in found.group(1).split(',') if tag.strip()]
    if not all(LOCALE_TAG.fullmatch(tag) for tag in tags):
        raise SettingRefused(f'bahasa CoC tidak dikenal: {found.group(1)}')
    return tags


def indonesian(tag: str) -> bool:
    return tag.lower().replace('_', '-').split('-')[0] in ('id', 'in')


class Language:
    """Puts CoC in Indonesian, the language the bot's screens were calibrated in, through Android's per-app
    language (Android 13+: no root, LDPlayer keeps running), and back to the user's own choice, kept in
    ~/.coc-auto/language.json until then. A language picked inside CoC's own settings is not seen here."""

    def __init__(self, adb: Callable[[], object], store: Path | None = None) -> None:
        self.adb, self.store = adb, store or Path.home() / LANGUAGE_FILE

    def saved(self) -> dict | None:
        return saved_file(self.store)

    def read(self, device) -> dict:
        app = app_locales(device.command('shell', 'cmd', 'locale', 'get-app-locales', GAME_PACKAGE, '--user', '0')
                          .decode('utf-8', 'replace'))
        system = device.command('shell', 'getprop', 'persist.sys.locale').decode('utf-8', 'replace').strip()
        return {'app': app, 'system': system, 'current': (app or [system])[0] or '?'}

    def status(self) -> dict:
        return {**self.read(self.adb()), 'target': BOT_LOCALE, 'saved': self.saved() is not None}

    def fit(self) -> bool:
        """False when CoC already shows Indonesian; otherwise CoC is set to it and restarted."""
        device = self.adb()
        now = self.read(device)
        if indonesian(now['current']):
            return False
        if self.saved() is None:
            keep(self.store, {'locales': now['app']})
        self.apply(device, [BOT_LOCALE], relaunch=True)
        return True

    def restore(self) -> bool:
        """False when nothing was switched; CoC is reopened only if it was in front."""
        saved = self.saved()
        if saved is None:
            return False
        locales = saved.get('locales')
        if not isinstance(locales, list) or not all(isinstance(tag, str) and LOCALE_TAG.fullmatch(tag) for tag in locales):
            raise SettingRefused(f'bahasa tersimpan rusak; atur bahasa CoC manual ({self.store})')
        device = self.adb()
        self.apply(device, locales, relaunch=device.game_in_front())
        self.store.unlink()
        return True

    def apply(self, device, locales: list, relaunch: bool) -> None:
        import bot
        command = ['shell', 'cmd', 'locale', 'set-app-locales', GAME_PACKAGE, '--user', '0']
        # No --locales sets an empty list: CoC follows Android's language again.
        device.command(*command, *(['--locales', ','.join(locales)] if locales else []))
        # A running game keeps the language it started with.
        device.stop_game()
        if relaunch:
            device.launch(bot.GAME_ACTIVITY)


class DeviceActions:
    """CoC and LDPlayer controls for the dashboard, through the adb and ldconsole set in the config."""

    def __init__(self, config_path: Path, display: Display | None = None, language: Language | None = None) -> None:
        self.config_path = config_path
        self.display = display or Display(config_path)
        self.language = language or Language(self.adb)

    def adb(self):
        import bot
        config = bot.load_config(self.config_path)
        return bot.ADB(bot.executable(config.get('adb'), 'ADB'), config['serial'], config['resolution'], live=True,
                       touch=config.get('touch_device'))

    def emulator(self):
        import bot
        config = bot.load_config(self.config_path)
        return bot.Emulator(bot.executable(config.get('ldconsole'), 'ldconsole'), config.get('ld_index', 0))

    def open_coc(self) -> None:
        import bot
        self.adb().launch(bot.GAME_ACTIVITY)

    def close_coc(self) -> None:
        self.adb().stop_game()

    def kill_ld(self) -> None:
        self.emulator().quit()

    def launch_ld(self) -> None:
        self.emulator().launch()

    def fit_display(self) -> bool:
        return self.display.fit()

    def restore_display(self) -> bool:
        return self.display.restore()

    def display_status(self) -> dict:
        return self.display.status()

    def fit_language(self) -> bool:
        return self.language.fit()

    def restore_language(self) -> bool:
        return self.language.restore()

    def language_status(self) -> dict:
        return self.language.status()
