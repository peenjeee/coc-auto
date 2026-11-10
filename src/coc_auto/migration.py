"""Explicit, backed-up import from an old source checkout. Never modify its files."""

from datetime import datetime, timezone
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile

STATE_FILES = (
    "stats.sqlite3",
    "upgrade-history.sqlite3",
    "village-export.json",
    "village-export-checkpoint.json",
    "upgrade-history-invalid.json",
    "discord.json",
    "device.json",
    "display.json",
)


def import_setup(source: Path, settings_home: Path, state_home: Path) -> Path:
    source = source.resolve()
    if not source.is_dir() or source in (settings_home.resolve(), state_home.resolve()):
        raise ValueError("Pilih folder instalasi lama")
    local = source / "config.local.json"
    if not local.is_file():
        raise ValueError("config.local.json tidak ditemukan")
    values = json.loads(local.read_text(encoding="utf-8"))
    if not isinstance(values, dict):
        raise ValueError("config.local.json must be an object")
    # Materialize custom calibrations: keep their paths valid without modifying bundled defaults.
    base = source / "config.json"
    settings_home.mkdir(parents=True, exist_ok=True)
    state_home.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = state_home / "backups" / f"import-{stamp}"
    backup.mkdir(parents=True)
    targets = [(local, settings_home / "config.local.json")]
    for name in STATE_FILES:
        old = source / ".coc-auto" / name
        if not old.is_file() and name in ("device.json", "display.json"):
            old = Path.home() / ".coc-auto" / name
        if old.is_file():
            targets.append((old, state_home / name))
    # Validate everything into staging before replacing the destination. Use SQLite's backup API
    # so committed WAL data is included, even if the old dashboard is open.
    with tempfile.TemporaryDirectory(dir=state_home) as staging:
        staged = []
        for old, dest in targets:
            candidate = Path(staging) / dest.name
            if old.suffix == ".sqlite3":
                with closing(
                    sqlite3.connect(f"{old.as_uri()}?mode=ro", uri=True)
                ) as src:
                    with closing(sqlite3.connect(candidate)) as dst:
                        src.backup(dst)
                        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                            raise ValueError("Database rusak")
            else:
                json.loads(old.read_text(encoding="utf-8"))
                shutil.copy2(old, candidate)
            staged.append((candidate, dest))
        calibration = (
            json.loads(base.read_text(encoding="utf-8")) if base.is_file() else {}
        )
        if not isinstance(calibration, dict):
            raise ValueError("Invalid config.json")
        from .bot import merged
        from .paths import RESOURCE_ROOT, default_config

        defaults = json.loads(default_config().read_text(encoding="utf-8"))
        imported = settings_home / "imported-resources" / stamp

        def preserve_assets(value):
            if isinstance(value, dict):
                return {k: preserve_assets(v) for k, v in value.items()}
            if isinstance(value, list):
                return [preserve_assets(v) for v in value]
            if not isinstance(value, str):
                return value
            try:
                old = (source / value).resolve()
            except (OSError, ValueError):
                return value
            if not old.is_relative_to(source):
                return value
            relative = old.relative_to(source)
            bundled = RESOURCE_ROOT / relative
            if old.is_file() and old.suffix.lower() == ".png":
                if bundled.is_file() and old.read_bytes() == bundled.read_bytes():
                    return relative.as_posix()
                target = imported / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old, target)
                return str(target)
            if old.is_dir() and any(old.glob("*.png")):
                images = list(old.rglob("*.png"))
                if all(
                    (bundled / image.relative_to(old)).is_file()
                    and image.read_bytes()
                    == (bundled / image.relative_to(old)).read_bytes()
                    for image in images
                ):
                    return relative.as_posix()
                target = imported / relative
                shutil.copytree(old, target, dirs_exist_ok=True)
                return str(target)
            return value

        def differences(old, new):
            return {
                k: differences(v, new[k])
                if isinstance(v, dict) and isinstance(new.get(k), dict)
                else v
                for k, v in old.items()
                if k not in new or v != new[k]
            }

        # Detect image-only calibration changes too, even when config.json is unchanged.
        # Unmodified templates keep using the bundled resources; custom assets are copied.
        combined = differences(preserve_assets(merged(calibration, values)), defaults)
        (Path(staging) / "config.local.json").write_text(
            json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        replaced = []
        try:
            for candidate, dest in staged:
                if dest.exists():
                    if dest.suffix == ".sqlite3":
                        with closing(
                            sqlite3.connect(f"{dest.as_uri()}?mode=ro", uri=True)
                        ) as current:
                            with closing(sqlite3.connect(backup / dest.name)) as saved:
                                current.backup(saved)
                    else:
                        shutil.copy2(dest, backup / dest.name)
                if dest.suffix == ".sqlite3":
                    for suffix in ("-wal", "-shm"):
                        Path(str(dest) + suffix).unlink(missing_ok=True)
                candidate.replace(dest)
                replaced.append(dest)
        except Exception:
            for dest in reversed(replaced):
                saved = backup / dest.name
                if saved.exists():
                    shutil.copy2(saved, dest)
                else:
                    dest.unlink(missing_ok=True)
            raise
    return backup
