# Development

## Run from source

Install Python 3.10 or newer (3.12 recommended) and Git, then:

```sh
git clone https://github.com/peenjeee/coc-auto.git
cd coc-auto
python -m venv .venv
```

Activate the environment:

- Windows PowerShell: `.venv\Scripts\Activate.ps1`
- macOS/Linux: `source .venv/bin/activate`

```sh
python -m pip install -e .
python -m coc_auto
```

The last command opens the local dashboard. Follow its device setup checklist.
Source installations need Tesseract installed separately for building-list OCR.
The existing Mac setup script handles the BlueStacks-specific configuration;
see [its guide](../../scripts/README.md).

## Commands

```sh
python bot.py --help
python calibrate.py --help
python relay_server.py --help
python -m unittest discover -s tests/python
node --test tests/web/*.mjs
```

On Windows PowerShell, run browser tests with:

```powershell
node --test (Get-ChildItem tests/web/test_*.mjs).FullName
```

Node is only needed for browser tests, not for running the dashboard.
Python tests run offline against recorded screenshots. Tests requiring an external
Tesseract or platform-specific LDPlayer paths may be skipped when unavailable.

`config.local.json` at the repository root stores personal overrides. Shared
calibration defaults are in `src/coc_auto/resources/config.json`.
The old shared names (`--config config.json` and `--config config.legacy.json`) still resolve to bundled resources.
Other explicit `--config` paths keep their own sibling overrides and relative assets.

## Generated data and releases

Catalog and sprite refresh tools remain under `scripts`. They update the packaged
resources or browser assets; their download caches are ignored.
See [Windows packaging](../../packaging/README.md) to build the packaged application.
