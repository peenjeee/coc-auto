# Advanced device setup

For source installations and custom calibration. Most Windows users should follow
[Installation](installation.md) instead.

## Requirements

- Windows with **LDPlayer** (tested on LDPlayer 14, Android 14, **1280×720**, the game in Indonesian or English),
  or a Mac with Apple Silicon and **BlueStacks Air**, set up by one script (see [macOS](#macos-bluestacks-air))
- **Python 3.10+**
- ADB from LDPlayer (`adb.exe`) with **local ADB debugging** enabled in LDPlayer settings (the dashboard's "fit
  screen" turns it on)
- A **64-bit** LDPlayer (LDPlayer 9 or newer): touch input is written as 64-bit Linux input events
- Tesseract OCR for the dashboard's building scan (building names); for loot it is only a fallback when digit
  templates are not configured

The account:

- A **Dark Elixir Storage** (Town Hall 7+) only for dark elixir (`--dark`, `--home dark`). `--walls home` works with
  or without one: without a dark elixir bar the gem bar moves up into its row, and the bot finds it by its green gem
  icon before every wall session (no gem icon, no upgrade).
- **Builder Base unlocked** for the default `--until-full` and for `--builder`/`--walls builder`; without it use
  `--home`.
- A loot `minimum` and `walls.home.gold_reserve` that fit the Town Hall (see
  [First-time setup](installation.md)).

## First-time setup for another PC or account

The bundled `config.json` was made on one PC for one account. Before the first live run on yours, check each
row below. Everything here is either an LDPlayer setting or a config key. Put your own values in
**`config.local.json`** in the repository root, with only the keys you change (for example
`{"serial": "emulator-5556", "ld_index": 1, "minimum": {"gold": 200000}}`). It is read on top of `config.json`, is
not in git, so `git pull` never collides with it, and the dashboard saves its settings and moved deploy pins there.

| What | Must be | Why / how to check |
| --- | --- | --- |
| LDPlayer resolution | **1280×720, DPI 240**, set for you | Screens are matched pixel for pixel, and text renders differently at another size or DPI. A live run on any other screen (such as LDPlayer's default 1920×1080, DPI 280) switches the instance to 1280×720, DPI 240 first, when `ldconsole` is set (LDPlayer restarts once, waiting until its virtual machine has let go of the disks), and puts your own screen back when a `--no-remote` run exits; under the dashboard it goes back when you leave the dashboard link. "Fit screen" in the dashboard does the same by hand. The calibrated DPI can be changed with the optional `dpi` key (default 240). |
| Game language | **Bahasa Indonesia** or **English** (a few rare popups not recognised yet) | Many anchors are text ("Pertempuran"/"Battle", "Tembok"/"Wall", "Maks:"/"Max:"). Each template with text has an English twin under the same file name in `calibration/en/`, tried after the Indonesian one; a few English-only anchors cover labels whose box differs ("Upgrade time", "Battle starts in", "Defender", "Battle ends in"). Popups that could not be brought up on purpose (rewards, league, gem offer, all builders busy, update offer) are calibrated in Indonesian only; the Gold Pass offer is told by its X button and shield alone, so it needs no
language. The Builder Base scout screen is also recognised when Android's status bar covers the top of the screen, as it can after an LDPlayer restart. A home battle is also recognised by its "Surrender" button and "Overall Damage" label alone: the loot icons and "Battle ends in" show the map through them and miss on a bright base. With another language every text anchor has to be recalibrated. Set it in CoC's own settings (Town Hall icon top left → Settings → Language): CoC keeps its own language, so changing Android's or LDPlayer's language does not change it. |
| `adb`, `ldconsole` | Paths in your LDPlayer install folder | e.g. `C:\LDPlayer\LDPlayer14\adb.exe` and `C:\LDPlayer\LDPlayer14\ldconsole.exe`; the folder name depends on the LDPlayer version. |
| `ld_index`, `serial` | Your instance | `ld_index` is the instance number in LDMultiPlayer (0 for the first). `serial` is `emulator-<5554 + 2 × index>`: `emulator-5554` for index 0, `emulator-5556` for index 1. Check with `adb devices`. |
| `touch_device`, `zoom_out.device` | The touchscreen node | Run `adb -s <serial> shell getevent -pl` and use the `/dev/input/eventN` whose block lists `ABS_MT_POSITION_X`. Set both keys to it; holds, pinches and fast taps go there. |
| `minimum` | `0` (the default), or a loot filter that fits your Town Hall | `0` attacks every opponent as it comes without reading its loot, the fastest farm. To skip poor bases, `500000` gold and elixir suits high Town Halls; set it lower for a low Town Hall, or every opponent is skipped. `0` turns a resource off. If searching runs out of gold 3 attacks in a row, the bot stops (exit code 6) and asks you to lower it. |
| `walls.home.gold_reserve` | Gold kept for searching | `200000` by default; lower it for a low Town Hall whose gold storage is small, or gold wall upgrades never pass it. |
| `tesseract` | Your Tesseract install, e.g. `C:\Program Files\Tesseract-OCR\tesseract.exe` | The building scan reads building names with it. Loot is read with the digit templates in `calibration/digits`; for loot Tesseract is needed only if you remove `digits`. |

The LDPlayer rows (`adb`, `ldconsole`, `ld_index`, `serial`, `touch_device`) can also be filled from the
[web dashboard](dashboard.md): **auto-detect** under device settings finds the LDPlayer folder, the running instance,
its adb serial, the touchscreen node and the screen size, and lists anything that does not fit (for example a
resolution other than 1280×720). Check the result and press save. `minimum` can be set there too.

Then open the village and run the dry run, which sends no input:

```bash
python bot.py
```

It should log `Dry-run: layar=home` (or `builder`; `Dry-run: screen=home` with `"language": "en"`). *"Layar tidak dikenal"* means the screen does not match; recheck
resolution, DPI and language before recalibrating anything. Like every command, it then stays on standby for the
dashboard; leave with **Ctrl+C**, or run `python bot.py --no-remote` to exit right after the check.
