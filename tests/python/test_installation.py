"""Regression coverage for packaged/source paths, migration, and launcher lifecycle."""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from coc_auto import bot, launcher, ldplayer, messages, migration, paths, remote


class InstallationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="CoC test é ")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name).resolve()
        self.env = patch.dict(
            os.environ, {"COC_AUTO_DATA_DIR": str(self.folder / "profile")}
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(messages.use, "id")

    def test_default_resources_are_separate_from_overrides_and_runtime_files(self):
        config = paths.default_config()
        self.assertEqual(paths.config_root(config), paths.RESOURCE_ROOT)
        self.assertEqual(
            bot.local_config_path(config), self.folder / "profile/config.local.json"
        )
        self.assertEqual(
            paths.captures_dir(paths.RESOURCE_ROOT), self.folder / "profile/captures"
        )
        with patch.object(paths, "FROZEN", True):
            self.assertEqual(paths.runtime_dir(config), self.folder / "profile")
        custom = self.folder / "custom/config.json"
        self.assertEqual(
            bot.local_config_path(custom), custom.with_name("config.local.json")
        )
        self.assertEqual(paths.config_root(custom), custom.parent)
        self.assertEqual(paths.runtime_dir(custom), custom.parent / ".coc-auto")

    def test_image_io_handles_unicode_filenames_without_opencv_filename_apis(self):
        import numpy as np
        from coc_auto.images import read_image, write_image

        target = self.folder / "desa 日本語.png"
        frame = np.full((4, 6, 3), 123, dtype=np.uint8)
        with (
            patch("cv2.imread", side_effect=AssertionError("Unicode native read")),
            patch("cv2.imwrite", side_effect=AssertionError("Unicode native write")),
        ):
            self.assertTrue(write_image(target, frame))
            self.assertTrue(np.array_equal(read_image(target), frame))
        self.assertIsNone(read_image(self.folder / "不存在.png"))

    def test_bundled_ocr_gets_private_relative_models_without_changing_parent_environment(
        self,
    ):
        program = self.folder / "ocr/bin/tesseract.exe"
        before = dict(os.environ)
        with patch("coc_auto.paths.bundled_ocr", return_value=program):
            options = paths.ocr_process_options(str(program))
        self.assertEqual(options["cwd"], str(program.parent))
        self.assertEqual(options["env"]["TESSDATA_PREFIX"], "../tessdata")
        self.assertEqual(dict(os.environ), before)

    def test_old_shared_config_names_resolve_but_custom_paths_are_untouched(self):
        from unittest.mock import patch

        with patch("pathlib.Path.exists", return_value=False):
            self.assertEqual(
                paths.config_argument("config.legacy.json"),
                paths.RESOURCE_ROOT / "config.legacy.json",
            )
            self.assertEqual(
                paths.config_argument("custom/config.json"), Path("custom/config.json")
            )
        self.assertEqual(
            paths.local_config(paths.RESOURCE_ROOT / "config.legacy.json"),
            paths.user_home() / "config.legacy.local.json",
        )

    def test_config_override_is_applied_without_changing_bundled_defaults(self):
        defaults = paths.default_config().read_bytes()
        path = bot.local_config_path(paths.default_config())
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"minimum": {"gold": 12345}, "language": "en"}))
        self.assertEqual(
            bot.load_config(paths.default_config())["minimum"]["gold"], 12345
        )
        self.assertEqual(messages.configured_language(paths.default_config()), "en")
        self.assertEqual(paths.default_config().read_bytes(), defaults)

    def test_frozen_worker_reuses_the_executable_and_source_worker_uses_the_module(
        self,
    ):
        import sys

        with patch.object(paths, "FROZEN", True):
            self.assertEqual(
                paths.worker_command(self.folder / "config.json"),
                [sys.executable, "--config", str(self.folder / "config.json")],
            )
        with patch.object(paths, "FROZEN", False):
            self.assertEqual(
                paths.worker_command(paths.default_config())[1:3], ["-m", "coc_auto"]
            )

    def test_lock_is_exclusive_and_a_crash_style_close_releases_it(self):
        first = launcher.InstanceLock(self.folder)
        second = launcher.InstanceLock(self.folder)
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.close()
            self.assertTrue(second.acquire())
        finally:
            if not first.file.closed:
                first.close()
            second.close()

    def test_repeated_launch_reopens_without_starting_a_second_service(self):
        with (
            patch("coc_auto.launcher.InstanceLock") as lock,
            patch(
                "coc_auto.launcher.existing_dashboard",
                return_value="http://localhost:8765/#key=example",
            ),
            patch("coc_auto.launcher.open_browser") as opened,
            patch("coc_auto.remote.supervise") as supervise,
            patch("sys.argv", ["coc-auto"]),
        ):
            lock.return_value.acquire.return_value = False
            self.assertEqual(launcher.main(), 0)
            opened.assert_called_once()
            supervise.assert_not_called()

    def test_launcher_starts_an_idle_service_and_only_opens_the_browser_once_ready(
        self,
    ):
        def serve(job, config, **options):
            self.assertEqual(job, [])
            options["on_ready"](Mock(server_address=("127.0.0.1", 8765)))
            return 0

        with (
            patch("coc_auto.remote.supervise", side_effect=serve),
            patch("coc_auto.launcher.open_browser") as opened,
            patch("sys.argv", ["coc-auto"]),
        ):
            self.assertEqual(launcher.main(), 0)
            self.assertIn("#key=coc_", opened.call_args.args[0])
            self.assertFalse((paths.runtime_dir() / "launcher.json").exists())

    def test_import_copies_committed_wal_records_and_backs_up_destination(self):
        old = self.folder / "old"
        old.mkdir()
        state = old / ".coc-auto"
        state.mkdir()
        (old / "config.local.json").write_text(
            '{"language":"en","minimum":{"gold":12345}}'
        )
        db = sqlite3.connect(state / "stats.sqlite3")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE sample (value INTEGER)")
        db.execute("INSERT INTO sample VALUES (42)")
        db.commit()
        self.addCleanup(db.close)
        settings = self.folder / "profile"
        settings.mkdir()
        target = settings / "config.local.json"
        target.write_text('{"language":"id"}')
        before = hashlib.sha256((old / "config.local.json").read_bytes()).hexdigest()
        backup = migration.import_setup(old, settings, settings)
        self.assertEqual(json.loads(target.read_text())["minimum"]["gold"], 12345)
        self.assertEqual(
            json.loads((backup / "config.local.json").read_text()), {"language": "id"}
        )
        # The with-block commits but does not close; close explicitly so Windows can delete the folder.
        with closing(sqlite3.connect(settings / "stats.sqlite3")) as copied:
            self.assertEqual(
                copied.execute("SELECT value FROM sample").fetchone()[0], 42
            )
        self.assertEqual(
            hashlib.sha256((old / "config.local.json").read_bytes()).hexdigest(), before
        )

    def test_bad_import_does_not_replace_existing_settings(self):
        old = self.folder / "old"
        old.mkdir()
        (old / "config.local.json").write_text("{broken")
        settings = self.folder / "profile"
        settings.mkdir()
        target = settings / "config.local.json"
        target.write_text('{"language":"en"}')
        with self.assertRaises(ValueError):
            migration.import_setup(old, settings, settings)
        self.assertEqual(target.read_text(), '{"language":"en"}')

    def test_imported_custom_templates_survive_removal_of_the_old_folder(self):
        import shutil

        old = self.folder / "old"
        old.mkdir()
        (old / "calibration").mkdir()
        (old / "calibration/custom.png").write_bytes(b"custom")
        (old / "config.json").write_text(
            json.dumps(
                {
                    "screens": {
                        "custom": {"anchors": [{"file": "calibration/custom.png"}]}
                    }
                }
            )
        )
        (old / "config.local.json").write_text('{"language":"en"}')
        settings = paths.user_home()
        migration.import_setup(old, settings, settings)
        shutil.rmtree(old)
        imported = json.loads((settings / "config.local.json").read_text(encoding="utf-8"))
        template = imported["screens"]["custom"]["anchors"][0]["file"]
        self.assertEqual(
            bot.asset_path(paths.RESOURCE_ROOT, template).read_bytes(), b"custom"
        )

    def test_image_only_calibration_changes_are_not_lost_during_import(self):
        defaults = json.loads(paths.default_config().read_text())

        def find_template(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if (
                        key == "file"
                        and isinstance(child, str)
                        and child.startswith("calibration/")
                    ):
                        return child
                    found = find_template(child)
                    if found:
                        return found
            if isinstance(value, list):
                for child in value:
                    found = find_template(child)
                    if found:
                        return found
            return None

        template = find_template(defaults)
        self.assertIsNotNone(template)
        old = self.folder / "old"
        old.mkdir()
        (old / "config.json").write_text(json.dumps(defaults))
        (old / "config.local.json").write_text("{}")
        custom = old / template
        custom.parent.mkdir(parents=True)
        custom.write_bytes(b"changed calibration")
        migration.import_setup(old, paths.user_home(), paths.runtime_dir())
        config = bot.load_config(paths.default_config())

        def copied(value):
            if isinstance(value, dict):
                return any(copied(v) for v in value.values())
            if isinstance(value, list):
                return any(copied(v) for v in value)
            return isinstance(value, str) and "imported-resources" in value

        self.assertTrue(copied(config))

    def test_check_reads_one_frame_without_sending_input_and_rejects_unknown_screens(
        self,
    ):
        config = paths.default_config()
        settings = remote.Settings(config)
        device = Mock()
        device.display_status.return_value = {
            "current": {"width": 1280, "height": 720, "dpi": 240},
            "target": {"width": 1280, "height": 720, "dpi": 240},
        }
        with (
            patch("coc_auto.remote.DeviceActions", return_value=device),
            patch("coc_auto.bot.Detector") as detector,
        ):
            detector.return_value.detect.return_value = "home"
            self.assertTrue(settings.check()["ready"])
            detector.return_value.detect.side_effect = bot.UnknownScreen("unknown")
            self.assertFalse(settings.check()["ready"])
            self.assertEqual(device.adb.return_value.capture.call_count, 2)
            device.adb.return_value.tap.assert_not_called()
            device.open_coc.assert_not_called()

    def test_explicit_stopped_instance_never_falls_back_to_another_running_instance(
        self,
    ):

        settings = remote.Settings(
            paths.default_config(), detector=lambda config: config
        )
        self.assertEqual(settings.detect(2)["ld_index"], 2)
        with (
            patch("coc_auto.ldplayer.ld_folders", return_value=["C:/LDPlayer"]),
            patch(
                "coc_auto.ldplayer.safe",
                return_value="0,First,0,0,1,0,0\n2,Second,0,0,0,0,0",
            ),
        ):
            found = ldplayer.detect(
                {"ld_index": 2, "_explicit_instance": True}, roots=[]
            )
            self.assertEqual(found["ld_index"], 2)
            self.assertTrue(found["problems"])
            self.assertNotIn("serial", found)


if __name__ == "__main__":
    unittest.main()
