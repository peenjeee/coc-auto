"""Refresh expired dashboard timers at a safe upgrade-worker boundary, never buy anything."""

import json
import logging
import math
from pathlib import Path
import sqlite3
import time
from contextlib import closing

from .paths import runtime_dir
from . import bot
from . import upgrade_history
from . import village_export

LOG = logging.getLogger(__name__)
RETRY_SECONDS = 300


def deadlines(root):
    path = runtime_dir(root=root) / "stats.sqlite3"
    if not path.exists():
        return []
    db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        saved = list(db.execute("SELECT list,scanned_at,rows FROM scans"))
    finally:
        db.close()
    result = []
    for listing, stamp, raw in saved:
        rows = json.loads(raw)
        if (
            not isinstance(stamp, (int, float))
            or not math.isfinite(stamp)
            or not isinstance(rows, list)
            or not all(isinstance(row, dict) for row in rows)
        ):
            continue
        projected = upgrade_history.project_scan(
            root, listing, {"scanned_at": stamp, "rows": rows}
        )
        for row in projected["rows"]:
            seconds = row.get("seconds")
            if (
                row.get("section") == "in_progress"
                and type(seconds) in (int, float)
                and math.isfinite(seconds)
                and seconds >= 0
            ):
                result.append(stamp + seconds)
    return result


def keep_scans(root, snapshot):
    """An export's rows as the dashboard's last scan of every list."""
    # Do not construct Statistics here: its startup recovery would mark
    # the currently running worker interrupted. Only update scan rows.
    with (
        closing(
            sqlite3.connect(
                str(runtime_dir(root=root) / "stats.sqlite3"), timeout=3
            )
        ) as db,
        db,
    ):
        for listing, rows in snapshot["lists"].items():
            village = "builder" if listing in ("builder", "star") else "home"
            db.execute(
                "INSERT OR REPLACE INTO scans(village,list,scanned_at,rows) VALUES(?,?,?,?)",
                (village, listing, snapshot["scanned_at"], json.dumps(rows)),
            )


def before_wait(runner, seconds):
    root = getattr(runner, "root", None)
    if not getattr(runner, "live", False) or root is None or seconds <= 0:
        return seconds
    root = Path(root)
    end = time.time() + seconds
    retry = getattr(runner, "upgrade_refresh_retry", 0)
    if retry > time.time():
        return min(seconds, max(1, math.ceil(retry - time.time())))
    try:
        known = deadlines(root)
        refreshed = False
        if any(finish <= time.time() for finish in known):
            LOG.info("Sinkron otomatis: memperbarui timer upgrade yang perlu diperiksa")
            runner.wait({"home", "builder"}, set(), 30)
            keep_scans(root, village_export.scan(runner))
            runner.upgrade_refresh_retry = 0
            runner.upgrade_busy_until = {}
            refreshed = True
            known = deadlines(root)
        now = time.time()
        remaining = max(0, math.ceil(end - now))
        if any(finish <= now for finish in known):
            runner.upgrade_refresh_retry = now + 30
            return min(remaining, 30)
        if refreshed:
            return 0  # Re-evaluate the queue with newly freed builders before sleeping.
        future = [max(1, math.ceil(finish - now)) for finish in known]
        return min([remaining, *future])
    except (bot.BotError, OSError, ValueError, sqlite3.Error) as exc:
        # A display refresh failure must not stop upgrading or cause endless UI retries.
        runner.upgrade_refresh_retry = time.time() + RETRY_SECONDS
        LOG.warning(
            "Sinkron otomatis gagal (%s); dicoba lagi sebelum menunggu lama", exc
        )
        return min(seconds, RETRY_SECONDS)
