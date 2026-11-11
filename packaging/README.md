# Windows packaging

Build on Windows x64 with Python 3.12. The user download contains Python, the web
interface, calibration, catalogs, OCR, and their required notices.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --require-hashes -r packaging/requirements-build.txt
python -m pip install -e . --no-deps --no-build-isolation
python packaging/build_windows.py 0.1.0
```

Output: `dist/coc-auto-0.1.0-windows-x64.zip` and its `.sha256` checksum.
The build refuses non-Windows platforms. OCR preparation can be checked separately
on macOS/Linux with `python packaging/prepare_ocr.py` after installing zstandard.

`windows-ocr.lock.json` pins every conda-forge archive and model by URL and SHA-256.
`prepare_ocr.py` verifies downloads and keeps only the Tesseract executable, the DLLs it
loads, the required language models, and licenses. It fails when Tesseract needs a DLL that
neither the lock nor Windows provides. It never runs downloaded installers.
The build includes a clean-process smoke test before writing the ZIP.

To update Python build dependencies, edit `requirements-build.in` and regenerate:

```sh
uv pip compile packaging/requirements-build.in --python-version 3.12 --python-platform windows --generate-hashes -o packaging/requirements-build.txt
```

OCR updates require a newly resolved Windows dependency closure, explicit versions
and checksums in the lock, and repeat testing of OCR in a clean Windows process.
Preserve package/model license notices when updating either lock.

## Release workflow

The Windows workflow builds on a version tag (`v0.1.0`) or manual dispatch.
Both paths upload a ZIP and checksum as workflow artifacts. Tags additionally
create a **draft GitHub Release** for maintainer review. The README's latest-release
link becomes usable after a maintainer publishes that draft. Manual dispatch does
not publish a release. No workflow has been triggered as part of local development.

Before publishing, test the ZIP in a clean Windows VM without Python/Git/Tesseract:
extract it, double-click, follow setup, check screenshots and OCR against LDPlayer,
start/stop a worker, reopen the running dashboard, import a previous setup, and
replace the app with a newer build. Include paths with spaces/non-ASCII characters,
multiple instances, disabled ADB, mismatched display settings, both languages,
and unrecognized screens. Ask a new user to follow only the README.

Signing the executable requires maintainer-owned credentials and is not configured.
