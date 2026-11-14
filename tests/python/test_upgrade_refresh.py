import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from contextlib import closing
from unittest.mock import Mock, patch

from coc_auto import bot
from coc_auto import upgrade_refresh as refresh


class CompletionRefreshTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / ".coc-auto").mkdir()
        self.db = self.root / ".coc-auto/stats.sqlite3"
        with closing(sqlite3.connect(self.db)) as db, db:
            db.executescript(
                "CREATE TABLE scans(village TEXT,list TEXT,scanned_at REAL,rows TEXT,PRIMARY KEY(village,list));"
                'CREATE TABLE runs(id INTEGER,status TEXT); INSERT INTO runs VALUES(1,"running");'
            )
        self.runner = SimpleNamespace(root=self.root, live=True, wait=Mock())

    def save(self, seconds):
        rows = [{"id": "firespitter", "section": "in_progress", "seconds": seconds}]
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO scans VALUES(?,?,?,?)",
                ("home", "buildings", 1000, json.dumps(rows)),
            )

    def test_manual_completion_is_refreshed_before_wait_and_does_not_interrupt_the_run_record(
        self,
    ):
        self.save(100)
        self.runner.upgrade_busy_until = {("home", "builders"): 99999}
        snapshot = {
            "scanned_at": 1200,
            "lists": {
                "buildings": [
                    {"id": "firespitter", "section": "other", "current_level": 3}
                ]
            },
        }
        with (
            patch.object(refresh.time, "time", return_value=1200),
            patch.object(refresh.village_export, "scan", return_value=snapshot) as scan,
        ):
            self.assertEqual(refresh.before_wait(self.runner, 17000), 0)
            scan.assert_called_once_with(self.runner)
        self.assertEqual(self.runner.upgrade_busy_until, {})
        self.runner.wait.assert_called_once_with({"home", "builder"}, set(), 30)
        with closing(sqlite3.connect(self.db)) as db, db:
            self.assertEqual(
                json.loads(db.execute("SELECT rows FROM scans").fetchone()[0])[0][
                    "section"
                ],
                "other",
            )
            self.assertEqual(
                db.execute("SELECT status FROM runs").fetchone()[0], "running"
            )

    def test_a_manual_timer_limits_sleep_even_when_the_queued_target_finishes_later(
        self,
    ):
        self.save(500)
        with (
            patch.object(refresh.time, "time", return_value=1200),
            patch.object(refresh.village_export, "scan") as scan,
        ):
            self.assertEqual(refresh.before_wait(self.runner, 17000), 300)
            scan.assert_not_called()

    def test_failed_sync_preserves_cached_scan_and_retries_without_stopping_upgrades(
        self,
    ):
        self.save(100)
        with (
            patch.object(refresh.time, "time", return_value=1200),
            patch.object(
                refresh.village_export, "scan", side_effect=bot.WaitTimeout("failed")
            ) as scan,
        ):
            self.assertEqual(refresh.before_wait(self.runner, 17000), 300)
            self.assertEqual(refresh.before_wait(self.runner, 17000), 300)
            self.assertEqual(scan.call_count, 1)
        with closing(sqlite3.connect(self.db)) as db, db:
            self.assertEqual(
                json.loads(db.execute("SELECT rows FROM scans").fetchone()[0])[0][
                    "seconds"
                ],
                100,
            )

    def test_missing_data_dry_run_and_stopped_time_limit_never_touch_the_game(self):
        with patch.object(refresh.village_export, "scan") as scan:
            self.assertEqual(refresh.before_wait(self.runner, 100), 100)
            self.runner.live = False
            self.assertEqual(refresh.before_wait(self.runner, 100), 100)
            self.runner.live = True
            self.assertEqual(refresh.before_wait(self.runner, 0), 0)
            scan.assert_not_called()

    def test_successful_refresh_returns_to_queue_even_when_export_took_time(self):
        self.save(100)
        clock = [1200]

        def export(runner):
            clock[0] += 15
            return {"scanned_at": clock[0], "lists": {"buildings": []}}

        with (
            patch.object(refresh.time, "time", side_effect=lambda: clock[0]),
            patch.object(refresh.village_export, "scan", side_effect=export),
        ):
            self.assertEqual(refresh.before_wait(self.runner, 100), 0)

    def test_refresh_rechecks_an_eligible_queue_target_without_sleeping_on_old_busy_decision(
        self,
    ):
        from coc_auto import upgrades, buildings

        self.save(100)
        self.runner.stop_at = None
        self.runner.poll = 1
        self.runner.config = {"tesseract": "tesseract"}
        self.runner.check_limits = Mock()
        self.runner.refresh_if_low_memory = Mock()
        self.runner.device = Mock()
        chosen = {listing: {"targets": []} for listing in buildings.LISTS}
        chosen["buildings"] = {
            "targets": [{"id": "cannon", "level": 2}],
            "keep_builders": 0,
        }
        snapshot = {
            "scanned_at": 1200,
            "lists": {
                "buildings": [
                    {"id": "firespitter", "section": "other", "current_level": 3}
                ]
            },
        }
        visits = []

        def visit(*args):
            visits.append(True)
            if len(visits) == 1:
                self.runner.upgrade_busy_until = {("home", "builders"): 99999}
                return False, True, 17000, None
            self.assertEqual(self.runner.upgrade_busy_until, {})
            return True, False, 600, None

        with (
            patch.object(refresh.time, "time", return_value=1200),
            patch.object(refresh.village_export, "scan", return_value=snapshot),
            patch.object(upgrades.time, "sleep") as sleep,
            patch.object(upgrades.bot, "executable", return_value="tesseract"),
            patch.object(buildings, "load_templates", return_value={}),
            patch.object(upgrades, "visit", side_effect=visit),
        ):
            self.assertEqual(upgrades.run(self.runner, chosen), 1)
        self.assertEqual(len(visits), 2)
        sleep.assert_not_called()
        self.runner.device.stop_game.assert_not_called()

    def test_elapsed_run_limit_prevents_automatic_sync_and_sleep(self):
        from coc_auto import upgrades

        self.save(100)
        self.runner.stop_at = 50
        with (
            patch.object(upgrades.time, "monotonic", return_value=100),
            patch.object(refresh.village_export, "scan") as scan,
            patch.object(upgrades.time, "sleep") as sleep,
        ):
            upgrades.pause(self.runner, 17000, "busy")
        scan.assert_not_called()
        self.runner.wait.assert_not_called()
        sleep.assert_not_called()

    def test_all_cached_lists_contribute_to_the_earliest_wake_up(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            for index, listing in enumerate(
                ("buildings", "lab", "pets", "gear", "builder", "star")
            ):
                row = {
                    "id": "example",
                    "section": "in_progress",
                    "seconds": 600 - index * 100,
                }
                village = "builder" if listing in ("builder", "star") else "home"
                db.execute(
                    "INSERT INTO scans VALUES(?,?,?,?)",
                    (village, listing, 1000, json.dumps([row])),
                )
        with (
            patch.object(refresh.time, "time", return_value=1000),
            patch.object(refresh.village_export, "scan") as scan,
        ):
            self.assertEqual(refresh.before_wait(self.runner, 17000), 100)
        scan.assert_not_called()

    def test_newer_upgrade_receipt_supersedes_an_expired_cached_timer(self):
        from coc_auto import upgrade_history

        rows = [
            {
                "id": "minion_prince",
                "count": 1,
                "current_level": 36,
                "next_level": 37,
                "section": "in_progress",
                "seconds": 100,
            }
        ]
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(
                "INSERT INTO scans VALUES(?,?,?,?)",
                ("home", "buildings", 1000, json.dumps(rows)),
            )
        (self.root / upgrade_history.EXPORT).write_text(
            json.dumps(
                {
                    "tag": "#AAA",
                    "timestamp": 1000,
                    "heroes": [{"data": 28000006, "lvl": 36, "timer": 100}],
                }
            )
        )
        upgrade_history.record(
            self.root,
            "buildings",
            {"id": "minion_prince", "name": "Minion Prince", "next_level": 38},
            72000,
            now=1100,
        )
        with (
            patch.object(refresh.time, "time", return_value=1200),
            patch.object(refresh.village_export, "scan") as scan,
        ):
            self.assertEqual(refresh.before_wait(self.runner, 17000), 17000)
        scan.assert_not_called()

    def test_cached_scan_lookup_accepts_unicode_and_hash_in_the_data_path(self):
        destination = self.root / "profile # 玩家"
        destination.mkdir()
        (self.root / ".coc-auto").rename(destination / ".coc-auto")
        self.root = destination
        self.db = self.root / ".coc-auto/stats.sqlite3"
        self.runner.root = self.root
        self.save(500)
        self.assertEqual(refresh.deadlines(self.root), [1500])

    def test_shared_resources_use_the_separate_user_data_directory(self):
        from coc_auto.paths import RESOURCE_ROOT

        self.save(100)
        self.runner.root = RESOURCE_ROOT
        snapshot = {"scanned_at": 1200, "lists": {"buildings": []}}
        with (
            patch.dict("os.environ", {"COC_AUTO_DATA_DIR": str(self.root)}),
            patch.object(refresh.time, "time", return_value=1200),
            patch.object(refresh.village_export, "scan", return_value=snapshot),
        ):
            self.assertEqual(refresh.before_wait(self.runner, 17000), 0)
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(
                json.loads(db.execute("SELECT rows FROM scans").fetchone()[0]), []
            )

    def test_packaged_windows_data_layout_is_refreshed_outside_resources(self):
        from coc_auto.paths import RESOURCE_ROOT

        self.save(100)
        packaged_db = self.root / "stats.sqlite3"
        self.db.rename(packaged_db)
        self.runner.root = RESOURCE_ROOT
        snapshot = {"scanned_at": 1200, "lists": {"buildings": []}}
        with (
            patch.dict("os.environ", {"COC_AUTO_DATA_DIR": str(self.root)}),
            patch("coc_auto.paths.FROZEN", True),
            patch.object(refresh.time, "time", return_value=1200),
            patch.object(refresh.village_export, "scan", return_value=snapshot),
        ):
            self.assertEqual(refresh.before_wait(self.runner, 17000), 0)
        with closing(sqlite3.connect(packaged_db)) as db:
            self.assertEqual(
                json.loads(db.execute("SELECT rows FROM scans").fetchone()[0]), []
            )

    def test_a_still_expired_fresh_export_has_a_short_retry_without_repeated_scans(
        self,
    ):
        self.save(100)
        snapshot = {
            "scanned_at": 1200,
            "lists": {
                "buildings": [
                    {"id": "firespitter", "section": "in_progress", "seconds": 0}
                ]
            },
        }
        with (
            patch.object(refresh.time, "time", return_value=1200),
            patch.object(refresh.village_export, "scan", return_value=snapshot) as scan,
        ):
            self.assertEqual(refresh.before_wait(self.runner, 17000), 30)
            self.assertEqual(refresh.before_wait(self.runner, 17000), 30)
            self.assertEqual(scan.call_count, 1)
