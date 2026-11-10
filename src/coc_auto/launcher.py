"""Double-click entry point for the packaged local dashboard."""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import time
import threading
import urllib.request
import webbrowser

from . import paths, messages

APP_ID = "coc-auto-local-dashboard"


class InstanceLock:
    """OS-released lock; a crash cannot leave the app permanently locked."""

    def __init__(self, folder: Path):
        folder.mkdir(parents=True, exist_ok=True)
        self.file = (folder / "launcher.lock").open("a+b")
        self.acquired = False

    def acquire(self) -> bool:
        self.file.seek(0)
        if self.file.read(1) == b"":
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            return False
        self.acquired = True
        return True

    def close(self):
        self.file.close()
        self.acquired = False


def dashboard_url(port: int) -> str:
    from .remote import device_key

    # The fragment never travels in HTTP requests or access logs.
    return f"http://localhost:{port}/#key={device_key()}"


def existing_dashboard(state: Path) -> str | None:
    try:
        port = json.loads(state.read_text(encoding="utf-8"))["port"]
        if type(port) is not int or not 1 <= port <= 65535:
            return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://localhost:{port}/api/health", timeout=1) as response:
            health = json.load(response)
        return dashboard_url(port) if health.get("app") == APP_ID else None
    except (OSError, ValueError, KeyError):
        return None


def choose_existing_setup() -> Path | None:
    import tkinter as tk
    from tkinter import filedialog

    window = tk.Tk()
    window.withdraw()
    try:
        chosen = filedialog.askdirectory(
            title=messages.translate("Impor pengaturan CoC Auto lama")
        )
        return Path(chosen) if chosen else None
    finally:
        window.destroy()


def notify(title: str, text: str):
    text = messages.translate(text)
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, title, 0)
    else:
        print(f"{title}: {text}", file=sys.stderr)


def open_browser(url):
    if os.environ.get("COC_AUTO_NO_BROWSER") != "1":
        webbrowser.open(url)


def self_test():
    """Exercise frozen imports/resources and OCR without an emulator or game input."""
    import subprocess
    import cv2
    import numpy as np
    from . import bot, buildings, gear, village_export, upgrade_history

    config = bot.load_config(paths.default_config())
    bot.validate_config(config, paths.RESOURCE_ROOT, "both")
    assert (paths.WEB_ROOT / "index.html").is_file()
    assert buildings.CATALOG.is_file() and gear.CATALOG.is_file()
    assert village_export.catalog() and upgrade_history.identities()
    program = paths.bundled_ocr()
    if not program:
        raise RuntimeError("Bundled OCR missing")
    image = np.full((100, 500, 3), 255, dtype=np.uint8)
    cv2.putText(image, "123456", (15, 72), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 3)
    encoded, buffer = cv2.imencode(".png", image)
    assert encoded
    for lang in ("eng", "ind"):
        text = subprocess.check_output(
            [str(program), "stdin", "stdout", "-l", lang, "--psm", "7"],
            input=buffer.tobytes(),
            **paths.ocr_process_options(str(program)),
        )
        assert b"123456" in text, (lang, text)
    print("Packaged resources and OCR OK")
    return 0


@contextlib.contextmanager
def console_shutdown(stop, finished):
    """Give workers a chance to stop when the Windows console is closed."""
    if os.name != "nt":
        yield
        return
    import ctypes
    from ctypes import wintypes

    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

    @callback_type
    def handler(event):
        if event not in (0, 1, 2, 5, 6):
            return False
        stop.set()
        if event in (2, 5, 6):
            finished.wait(4)
        return True

    api = ctypes.windll.kernel32.SetConsoleCtrlHandler
    api.argtypes = (callback_type, wintypes.BOOL)
    api.restype = wintypes.BOOL
    if not api(handler, True):
        raise ctypes.WinError()
    try:
        yield
    finally:
        api(handler, False)


def main() -> int:
    if "--worker" in sys.argv[1:]:
        from .bot import main as worker

        return worker()
    parser = argparse.ArgumentParser(description="Open the local CoC Auto dashboard")
    parser.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--config", type=paths.config_argument, default=paths.default_config()
    )
    parser.add_argument("--import-setup", nargs="?", const="", metavar="FOLDER")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    from . import remote

    messages.initialize_cli(args.config)
    folder = paths.runtime_dir(args.config)
    lock = InstanceLock(folder)
    state = folder / "launcher.json"
    try:
        if not lock.acquire():
            if args.import_setup is not None:
                notify("CoC Auto", "Tutup CoC Auto sebelum mengimpor.")
                return 1
            # A second click can arrive while the first process is still starting.
            for _ in range(50):
                url = existing_dashboard(state)
                if url:
                    open_browser(url)
                    return 0
                time.sleep(0.2)
            notify(
                "CoC Auto",
                "CoC Auto sedang dimulai atau tidak tersedia. Coba lagi sebentar.",
            )
            return 1
        if args.import_setup is not None:
            from .migration import import_setup

            source = (
                Path(args.import_setup)
                if args.import_setup
                else choose_existing_setup()
            )
            if source is None:
                return 0
            backup = import_setup(source, paths.user_home(), folder)
            notify("CoC Auto", f"Impor selesai. Cadangan: {backup}")

        def ready(server):
            port = server.server_address[1]
            state.write_text(json.dumps({"port": port}), encoding="utf-8")
            open_browser(dashboard_url(port))

        log = RotatingFileHandler(
            folder / "application.log",
            maxBytes=2_000_000,
            backupCount=2,
            encoding="utf-8",
        )
        log.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger = logging.getLogger("coc_auto")
        logger.setLevel(logging.INFO)
        logger.addHandler(log)
        try:
            print(
                messages.translate("Biarkan jendela ini terbuka. Tutup untuk keluar.")
            )
            stop, finished = threading.Event(), threading.Event()
            with console_shutdown(stop, finished):
                try:
                    return remote.supervise(
                        [], args.config, on_ready=ready, stop_event=stop
                    )
                finally:
                    finished.set()
        finally:
            logger.removeHandler(log)
            log.close()
    except Exception as exc:
        notify("CoC Auto", f"Tidak dapat memulai: {exc}")
        return 1
    finally:
        # Only the owner removes its state; a second launch must not remove it.
        if lock.acquired:
            with contextlib.suppress(OSError):
                state.unlink()
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
