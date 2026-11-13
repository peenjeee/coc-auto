"""Build, smoke-test and zip the Windows release. Run from the repository root."""

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import sysconfig
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def build(version):
    if sys.platform != "win32":
        raise SystemExit("Build the Windows release on Windows.")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-.][A-Za-z0-9.]+)?", version):
        raise SystemExit("Invalid version")
    subprocess.run([sys.executable, str(ROOT / "packaging/prepare_ocr.py")], check=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            str(ROOT / "packaging/coc-auto.spec"),
            "--clean",
            "--noconfirm",
        ],
        cwd=ROOT,
        check=True,
    )
    app = ROOT / "dist/CoC Auto"
    for name in (
        "Import existing setup.cmd",
        "START-HERE.txt",
        "THIRD-PARTY-NOTICES.md",
    ):
        shutil.copy2(ROOT / "packaging" / name, app / name)
    shutil.copy2(ROOT / "LICENSE", app / "LICENSE")
    license_dir = app / "licenses"
    license_dir.mkdir(exist_ok=True)
    stdlib = Path(sysconfig.get_path("stdlib"))
    for source in (Path(sys.base_prefix) / "LICENSE.txt", stdlib / "LICENSE.txt"):
        if source.is_file():
            shutil.copy2(source, license_dir / "Python-LICENSE.txt")
            break
    # Copy Python distribution metadata licenses, omitted by some freezing hooks.
    from importlib.metadata import distribution

    for name in ("numpy", "opencv-python", "websockets", "pyinstaller"):
        dist = distribution(name)
        for file in dist.files or []:
            if any(
                "license" in part.lower() or "copying" in part.lower()
                for part in file.parts
            ):
                source = Path(dist.locate_file(file))
                if source.is_file():
                    target = license_dir / name / Path(*file.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    (app / "version.json").write_text(
        json.dumps({"version": version}), encoding="utf-8"
    )
    subprocess.run(
        [sys.executable, str(ROOT / "packaging/smoke_windows.py"), str(app)], check=True
    )
    # Reject writable state or per-user overrides in the distributable.
    forbidden = {
        ".coc-auto",
        "config.local.json",
        "device.json",
        "discord.json",
        "ai.json",
        "ai-history.jsonl",
        "stats.sqlite3",
        "application.log",
    }
    for path in app.rglob("*"):
        if path.name in forbidden or path.suffix in (
            ".log",
            ".sqlite3",
            ".bak",
            ".tmp",
        ):
            raise RuntimeError(f"Personal data in release: {path}")
    release = ROOT / "dist" / f"coc-auto-{version}-windows-x64.zip"
    with zipfile.ZipFile(
        release, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(app.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(app.parent))
    release.with_suffix(".zip.sha256").write_text(
        hashlib.sha256(release.read_bytes()).hexdigest() + "  " + release.name + "\n",
        encoding="ascii",
    )
    print(release)


if __name__ == "__main__":
    build(sys.argv[1])
