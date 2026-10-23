"""CoC and LDPlayer controls for the dashboard: open or close the game, quit or start LDPlayer, and put the
instance on the screen the bot was calibrated for (1280x720 DPI 240), then back to the user's own when the bot is
left. CoC keeps its own language setting; neither Android's nor LDPlayer's language changes it."""
from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Callable

import ldplayer

DISPLAY_FILE = Path('.coc-auto') / 'display.json'
LD_STOP_SECONDS, LD_SETTLE_SECONDS = 30, 2
DISPLAY_LIMITS = {'width': (320, 7680), 'height': (240, 4320), 'dpi': (72, 640)}


class SettingRefused(Exception):
    """A screen change that cannot be made as asked; nothing was changed."""


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


class DeviceActions:
    """CoC and LDPlayer controls for the dashboard, through the adb and ldconsole set in the config."""

    def __init__(self, config_path: Path, display: Display | None = None) -> None:
        self.config_path = config_path
        self.display = display or Display(config_path)

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
