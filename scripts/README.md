# BlueStacks Air on macOS (Apple Silicon)

`bsair.sh` prepares BlueStacks Air for coc-auto and keeps the setup reproducible.
It uses the bot's own `ADB` layer, so the same code path works on LDPlayer, AVD
and BlueStacks.

## Setup

```bash
# Python 3.12 is used because OpenCV has no wheel for 3.15 yet.
scripts/bsair.sh deps

# Enable ADB, start BlueStacks, fit the screen, probe the touch node,
# and write config.local.json for this machine.
scripts/bsair.sh all
```

`all` is idempotent: run it again after a BlueStacks update or a restart.

## Commands

| Command | What it does |
| --- | --- |
| `scripts/bsair.sh probe` | Read-only: ADB, Android, screen, touch node, CoC package. |
| `scripts/bsair.sh enable` | Sets `bst.enable_adb_access=1` and restarts BlueStacks if needed. |
| `scripts/bsair.sh start` / `stop` | Starts or quits BlueStacks and waits for its VM. |
| `scripts/bsair.sh fit` | `wm size 1280x720` and `wm density 240`. |
| `scripts/bsair.sh config` | Writes `config.local.json` (adb, serial, touch node, zoom). |
| `scripts/bsair.sh deps` | Creates `.venv` (Python 3.12) and installs `requirements.txt`. |
| `scripts/bsair.sh all` | `enable` + `fit` + `probe` + `config`. |
| `scripts/bsair.sh bot ...` | Ensures the device is ready, then runs `bot.py` with the repo venv. |

Examples:

```bash
# Dry run (no taps), no dashboard.
scripts/bsair.sh bot --mode home --cycles 1 --no-remote

# Live home-village farming (works without Builder Base).
scripts/bsair.sh bot --until-full --home all --live --no-remote

# Default until-full (needs Builder Base unlocked).
scripts/bsair.sh bot --until-full --live --no-remote
```

Without `--no-remote` the bot starts the dashboard on <http://localhost:8765>.

## Root

Root is **not** needed on BlueStacks Air:

- `adb shell id` is `uid=2000(shell)` and its groups include `input` (1004);
- SELinux is `Disabled`;
- `dd if=/dev/null of=/dev/input/event2` succeeds.

However, **CoC ignores synthetic raw touch events** on BlueStacks Air: a raw
tap does not open the attack menu, while `adb shell input tap` at the same
coordinates does. `bsair.sh` therefore defaults to `BSAIR_RAW=0`, which leaves
`touch_device` and `zoom_out` unset and makes the bot use `input tap` /
`input swipe`. Set `BSAIR_RAW=1` only if a future BlueStacks build accepts the
raw node; otherwise pinch and simultaneous fingers do not register.

## What it changes

- `<BlueStacks>/bluestacks.conf`: `bst.enable_adb_access="1"` (a `.bak` copy is
  made next to it).
- The running Android: `wm size 1280x720` and `wm density 240`. These are
  runtime overrides and are reapplied by `bsair.sh` after every BlueStacks
  restart.
- `config.local.json` in the repo (gitignored): the `adb` path, the
  `127.0.0.1:5555` serial, `touch_device`, `ldconsole: null`, and `zoom_out`
  pointed at the BlueStacks touch node.

## Terminal language

The project's own default is Indonesian. `bsair.sh` writes `"language": "en"` into
`config.local.json` (`BSAIR_LANG=en|id`), which translates the bot's log and error
messages. The language of the game's UI is separate and is matched by the
calibration templates.

## BlueStacks specifics

BlueStacks/QEMU expose the virtual touchscreen with `ABS_MT_POSITION_X/Y` in
`0..32767`, not screen pixels. `bot.py` (`ADB.scaled_events`) maps its pixel
coordinates to the node's range, and `ldplayer.touch_problem` accepts that
range. Raw `screencap` takes about 0.14 s at 1280x720 on an M-series Mac.

## Limitations

- `wm size`/`wm density` reset when BlueStacks restarts; run `bsair.sh` before a
  run (the `bot` command already does).
- A BlueStacks update may switch `bst.enable_adb_access` back off; run
  `scripts/bsair.sh enable` again.
- Without raw input there is no pinch zoom: the bundled calibration was captured
  zoomed out, so deploy points and some battle screens may need recalibration.
- BlueStacks Air is arm64-only on Apple Silicon. There is no Linux version; use
  the Android Studio emulator (`google_apis` image) or Waydroid on Linux.
