"""CoC and LDPlayer controls for the dashboard: open or close the game, quit or start LDPlayer, and put the
instance on the screen the bot was calibrated for (1280x720 DPI 240), then back to the user's own when the bot is
left. CoC keeps its own language setting; neither Android's nor LDPlayer's language changes it."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Callable

from . import ldplayer
from .paths import runtime_dir, config_root

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
        self.config_path, self.store = config_path, store or runtime_dir(config_path) / 'display.json'
        if store is None and not self.store.exists():
            from .paths import FROZEN
            legacy = Path.home() / DISPLAY_FILE
            if not FROZEN and not os.environ.get('COC_AUTO_DATA_DIR') and legacy.is_file():
                self.store = legacy
        self.sleep, self.make_emulator = sleep, emulator

    def instance(self) -> tuple:
        from . import bot
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
        debugging is opened on the way, since the bot cannot reach LDPlayer without it, and ASTC decoding turned off,
        since it makes LDPlayer's VM leak memory; a restore leaves both so."""
        emulator, path, target = self.instance()
        if ldplayer.fits(path, target):
            return False
        current = ldplayer.read_display(path)
        if self.saved() is None and current != target:
            keep(self.store, {'path': str(path), 'display': current})
        self.rewrite(emulator, path, target, relaunch=True, fit=True)
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

    def rewrite(self, emulator, path: Path, display: dict, relaunch: bool, fit: bool = False) -> None:
        if emulator.running():
            emulator.quit()
            for _ in range(LD_STOP_SECONDS):
                # Its virtual machine lets go of the disks after the window closes; launched earlier, it reports
                # "Disk virtual tidak valid (InvalidMedium)".
                if not (emulator.running() or emulator.vm_running()):
                    break
                self.sleep(1)
            else:
                raise SettingRefused('LDPlayer tidak berhenti; tutup manual lalu coba lagi')
            self.sleep(LD_SETTLE_SECONDS)  # LDPlayer writes its own config as it closes
        (ldplayer.fit if fit else ldplayer.write_display)(path, display)
        if relaunch:
            emulator.launch()


class DeviceActions:
    """CoC and LDPlayer controls for the dashboard, through the adb and ldconsole set in the config."""

    def __init__(self, config_path: Path, display: Display | None = None) -> None:
        self.config_path = config_path
        self.display = display or Display(config_path)

    def adb(self):
        from . import bot
        config = bot.load_config(self.config_path)
        return bot.ADB(bot.executable(config.get('adb'), 'ADB'), config['serial'], config['resolution'], live=True,
                       touch=config.get('touch_device'))

    def emulator(self):
        from . import bot
        config = bot.load_config(self.config_path)
        return bot.Emulator(bot.executable(config.get('ldconsole'), 'ldconsole'), config.get('ld_index', 0))

    def open_coc(self) -> None:
        from . import bot
        self.adb().launch(bot.GAME_ACTIVITY)

    def scan_buildings(self, listing: str = 'buildings') -> list[dict]:
        """Copy the game's village JSON once, refreshing all six upgrade lists without OCR."""
        from . import bot
        from . import village_export
        config, root = bot.load_config(self.config_path), config_root(self.config_path)
        if listing not in village_export.SECTIONS:
            raise SettingRefused('Unknown upgrade list')
        device = self.adb()
        runner = bot.Bot(config, root, device, live=True)
        # Long upgrade waits deliberately close CoC. Sync must open it before
        # asking the export scanner to recognize the village/settings screens.
        if not device.game_in_front():
            device.launch(bot.GAME_ACTIVITY)
            runner.wait({'home', 'builder'}, set(), 60)
        snapshot = village_export.scan(runner)
        self.last_village_scan = snapshot
        return snapshot['lists'][listing]

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
