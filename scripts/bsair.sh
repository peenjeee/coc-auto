#!/usr/bin/env bash
# BlueStacks Air (macOS, Apple Silicon) setup for coc-auto.
#
# BlueStacks Air exposes ADB on emulator-5554, but keeps it switched off in
# bluestacks.conf (bst.enable_adb_access="0"). This script turns it on, waits
# for Android, fits the screen to the bot's 1280x720 calibration, probes the
# touchscreen node and writes config.local.json. Everything it changes is
# idempotent, so a fresh checkout can be set up again with one command.
#
# Usage:
#   scripts/bsair.sh probe        # read-only diagnostics, changes nothing
#   scripts/bsair.sh enable       # enable ADB (restarts BlueStacks if needed)
#   scripts/bsair.sh start        # start BlueStacks and wait for ADB
#   scripts/bsair.sh stop         # quit BlueStacks and wait for its VM
#   scripts/bsair.sh fit          # wm size 1280x720 + wm density 240
#   scripts/bsair.sh config       # write config.local.json for this device
#   scripts/bsair.sh deps         # create .venv and install requirements
#   scripts/bsair.sh all          # enable + fit + probe + config
#   scripts/bsair.sh bot ...      # ensure the device and venv, then run bot.py ...
#
# Environment:
#   BLUESTACKS_APP     default /Applications/BlueStacks.app
#   BLUESTACKS_CONF    default /Users/Shared/Library/Application Support/BlueStacks/bluestacks.conf
#   BLUESTACKS_SERIAL  default: the first device `adb devices` reports
#   COC_RESOLUTION     default 1280x720 (the calibration in config.json)
#   COC_DPI            default 240
#   BOOT_TIMEOUT       default 180 seconds
#   BSAIR_RAW          default 0: Coc on BlueStacks ignores synthetic raw touch
#                      events, so the bot uses `input tap`/`input swipe`. Set 1
#                      to use the raw /dev/input node instead (pinch + fingers,
#                      but the game may ignore them).
#   BSAIR_LANG         default en: terminal messages (the project defaults to
#                      Indonesian). The game language is separate.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="${BLUESTACKS_APP:-/Applications/BlueStacks.app}"
CONF="${BLUESTACKS_CONF:-/Users/Shared/Library/Application Support/BlueStacks/bluestacks.conf}"
RESOLUTION="${COC_RESOLUTION:-1280x720}"
DPI="${COC_DPI:-240}"
TIMEOUT="${BOOT_TIMEOUT:-180}"
RAW="${BSAIR_RAW:-0}"
LANGUAGE="${BSAIR_LANG:-en}"
SERIAL="${BLUESTACKS_SERIAL:-}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# The repo's venv (created with Python 3.12 here: OpenCV has no wheel for 3.15).
PYTHON="$ROOT/.venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"

ensure_python() {
  [[ -x "$ROOT/.venv/bin/python" ]] && { PYTHON="$ROOT/.venv/bin/python"; return 0; }
  log "creating $ROOT/.venv and installing requirements..."
  if command -v uv >/dev/null 2>&1; then
    uv venv --python 3.12 "$ROOT/.venv"
    uv pip install --python "$ROOT/.venv/bin/python" -r "$ROOT/requirements.txt"
  else
    local py
    py="$(command -v python3.12 || command -v python3.13 || command -v python3)"
    "$py" -m venv "$ROOT/.venv"
    "$ROOT/.venv/bin/python" -m pip install -r "$ROOT/requirements.txt"
  fi
  PYTHON="$ROOT/.venv/bin/python"
}

log()  { printf '%s\n' "$*" >&2; }
die()  { log "error: $*"; exit 1; }

[[ "$(uname -s)" == "Darwin" ]] || die "BlueStacks Air is macOS only"
[[ -f "$CONF" ]] || die "BlueStacks config not found: $CONF"

# The app's own adb understands its restricted shell best; homebrew adb works
# too once ADB access is on. Fall back to it so the script also runs on a Mac
# where BlueStacks lives elsewhere.
if [[ -x "$APP/Contents/MacOS/hd-adb" ]]; then
  ADB="$APP/Contents/MacOS/hd-adb"
elif command -v adb >/dev/null 2>&1; then
  ADB="$(command -v adb)"
else
  die "adb not found (looked in $APP/Contents/MacOS/hd-adb and PATH)"
fi

running() { pgrep -f "$APP/Contents/MacOS/BlueStacks" >/dev/null 2>&1; }
vm_running() { pgrep -f 'qemu-system-aarch64' >/dev/null 2>&1; }

# BlueStacks Air listens on its instance's adb_port (5555 by default) but does
# not announce itself, so `adb devices` stays empty until an explicit connect.
adb_port() {
  local port
  port="$(sed -n 's/^bst\.instance\.[^.]*\.adb_port="\([0-9][0-9]*\)"/\1/p' "$CONF" | head -1)"
  printf '%s' "${port:-5555}"
}

connect_device() {
  "$ADB" connect "127.0.0.1:$(adb_port)" >/dev/null 2>&1 || true
}

detect_serial() {
  connect_device
  local want serial
  want="127.0.0.1:$(adb_port)"
  serial="$("$ADB" devices 2>/dev/null | awk -v want="$want" '$1 == want && $2 == "device" { print $1; exit }')"
  [[ -n "$serial" ]] || serial="$("$ADB" devices 2>/dev/null | awk 'NR > 1 && $2 == "device" { print $1; exit }')"
  printf '%s' "$serial"
}

booted() {
  local serial="$1"
  [[ "$("$ADB" -s "$serial" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == "1" ]]
}

wait_for_boot() {
  local deadline=$((SECONDS + TIMEOUT)) serial=""
  "$ADB" start-server >/dev/null 2>&1 || true
  while (( SECONDS < deadline )); do
    connect_device
    serial="${SERIAL:-$(detect_serial || true)}"
    if [[ -n "$serial" ]] && booted "$serial"; then
      SERIAL="$serial"
      log "device $SERIAL is up"
      return 0
    fi
    sleep 2
  done
  die "BlueStacks did not boot within ${TIMEOUT}s (is the instance running?)"
}

quit_app() {
  running || { log "BlueStacks is not running"; return 0; }
  log "stopping BlueStacks..."
  osascript -e 'tell application "BlueStacks" to quit' >/dev/null 2>&1 || true
  for _ in $(seq 1 30); do
    running || { log "BlueStacks stopped"; return 0; }
    sleep 1
  done
  pkill -TERM -f "$APP/Contents/MacOS/BlueStacks" >/dev/null 2>&1 || true
  for _ in $(seq 1 20); do
    running || break
    sleep 1
  done
  running && die "BlueStacks did not quit; close it by hand and run again"
  for _ in $(seq 1 30); do
    vm_running || { log "VM stopped"; return 0; }
    sleep 1
  done
  log "warning: the VM is still up; starting BlueStacks anyway"
  return 0
}

start_app() {
  if ! running; then
    log "starting BlueStacks..."
    open -a "$APP"
  fi
  wait_for_boot
}

adb_enabled() {
  grep -q '^bst.enable_adb_access="1"' "$CONF" 2>/dev/null
}

shell_works() {
  local serial="${SERIAL:-$(detect_serial || true)}"
  [[ -n "$serial" ]] || return 1
  "$ADB" -s "$serial" shell id >/dev/null 2>&1
}

enable_adb() {
  if adb_enabled && shell_works; then
    log "ADB access is already enabled"
    SERIAL="${SERIAL:-$(detect_serial)}"
    return 0
  fi
  if ! adb_enabled; then
    quit_app
    cp -p "$CONF" "$CONF.bak"
    log "setting bst.enable_adb_access=1 in $CONF (backup: $CONF.bak)"
    python3 - "$CONF" <<'PY'
import sys
from pathlib import Path
path = Path(sys.argv[1])
text = path.read_text(encoding='utf-8')
text = text.replace('bst.enable_adb_access="0"', 'bst.enable_adb_access="1"')
path.write_text(text, encoding='utf-8')
PY
  else
    # The key is on but the running instance still has the old adbd: restart it.
    if running; then
      log "ADB key is on but the shell is closed; restarting BlueStacks"
      quit_app
    else
      log "ADB key is on; starting BlueStacks"
    fi
  fi
  start_app
  shell_works || die "ADB is on but 'adb shell id' still fails; check for an adb version clash: '$ADB kill-server' and retry"
}

touch_node() {
  "$ADB" -s "$SERIAL" shell getevent -pl >"$TMP/events" 2>/dev/null || true
  (cd "$ROOT" && python3 - "$TMP/events" <<'PY'
import sys
import ldplayer
text = open(sys.argv[1], encoding='utf-8', errors='replace').read()
print(ldplayer.parse_touch(text) or '')
PY
)
}

# A raw write into the input node is how the bot taps: no root is needed when
# this succeeds. `dd` writes nothing and fails with a permission error.
touch_writable() {
  local node="$1" out
  out="$("$ADB" -s "$SERIAL" shell "dd if=/dev/null of=$node" 2>&1 || true)"
  case "$out" in
    *denied*|*'Read-only'*|*'No such file'*) return 1 ;;
  esac
  "$ADB" -s "$SERIAL" shell "test -w $node" >/dev/null 2>&1
}

fit_screen() {
  log "setting the screen to $RESOLUTION, DPI $DPI"
  "$ADB" -s "$SERIAL" shell "wm size $RESOLUTION; wm density $DPI"
  sleep 2
  "$ADB" -s "$SERIAL" shell 'wm size; wm density'
}

probe() {
  SERIAL="${SERIAL:-$(detect_serial || true)}"
  [[ -n "$SERIAL" ]] || die "no device in 'adb devices'"
  local node writable="no" coc="not installed"
  node="$(touch_node)"
  if [[ -n "$node" ]] && touch_writable "$node"; then writable="yes"; fi
  "$ADB" -s "$SERIAL" shell 'pm list packages 2>/dev/null | grep -i supercell' >"$TMP/coc" 2>/dev/null || true
  [[ -s "$TMP/coc" ]] && coc="$(tr -d '\r' <"$TMP/coc")"
  log "------------------------------------------------------------"
  log "repo:        $ROOT"
  log "adb:         $ADB"
  log "serial:      $SERIAL"
  log "android:     $("$ADB" -s "$SERIAL" shell getprop ro.build.version.release | tr -d '\r') (SDK $("$ADB" -s "$SERIAL" shell getprop ro.build.version.sdk | tr -d '\r'), $("$ADB" -s "$SERIAL" shell getprop ro.product.cpu.abi | tr -d '\r'))"
  log "shell:       $("$ADB" -s "$SERIAL" shell id | tr -d '\r')"
  log "selinux:     $("$ADB" -s "$SERIAL" shell getenforce | tr -d '\r')"
  log "screen:      $("$ADB" -s "$SERIAL" shell wm size | tr -d '\r') / $("$ADB" -s "$SERIAL" shell wm density | tr -d '\r')"
  log "touch node:  ${node:-not found}"
  log "raw write:   $writable (BSAIR_RAW=$RAW; CoC ignores synthetic raw touches on BlueStacks)"
  log "clash:       $coc"
  log "------------------------------------------------------------"
}

write_config() {
  SERIAL="${SERIAL:-$(detect_serial || true)}"
  [[ -n "$SERIAL" ]] || die "no device in 'adb devices'"
  local node writable="0"
  node="$(touch_node)"
  if [[ "$RAW" == "1" ]] && [[ -n "$node" ]] && touch_writable "$node"; then writable="1"; fi
  (cd "$ROOT" && python3 - "$ADB" "$SERIAL" "$node" "$writable" "$LANGUAGE" <<'PY'
import json
import sys
from pathlib import Path

adb, serial, node, writable, language = (sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] == '1',
                                         sys.argv[5])
if language not in ('id', 'en'):
    raise SystemExit(f'BSAIR_LANG must be id or en, not {language!r}')
root = Path.cwd()
path = root / 'config.local.json'
try:
    local = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
except ValueError:
    local = {}
base = json.loads((root / 'config.json').read_text(encoding='utf-8'))

local['adb'] = adb
local['serial'] = serial
local['resolution'] = [1280, 720]
# Terminal messages; the game's own language is separate.
local['language'] = language
# No ldconsole on macOS: the bot must not try to launch LDPlayer.
local['ldconsole'] = None

if writable and node:
    local['touch_device'] = node
    if base.get('zoom_out'):
        zoom = dict(base['zoom_out'])
        zoom['device'] = node
        local['zoom_out'] = zoom
    print(f'touch_device={node} (raw input enabled: pinch zoom and multi-finger holds are on)')
else:
    # Without raw input the bot falls back to `input tap` / `input swipe`.
    # Pinch zoom has no fallback, so it has to be switched off; the
    # calibration was made zoomed out, so expect a recalibration.
    local['touch_device'] = None
    local['zoom_out'] = None
    print('using input tap/input swipe: BlueStacks/CoC ignores raw touch events, so '
          'touch_device and zoom_out are off (no pinch, no simultaneous fingers)')

path.write_text(json.dumps(local, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
print(f'wrote {path}')
PY
)
}

case "${1:-all}" in
  probe)  probe ;;
  enable) enable_adb ;;
  start)  start_app ;;
  stop)   quit_app ;;
  fit)    SERIAL="${SERIAL:-$(detect_serial || true)}"; [[ -n "$SERIAL" ]] || die "no device"; fit_screen ;;
  config) write_config ;;
  deps)   ensure_python ;;
  all)
    enable_adb
    fit_screen
    probe
    write_config
    ;;
  bot)
    shift
    ensure_python
    enable_adb
    fit_screen
    log "running: $PYTHON $ROOT/bot.py $*"
    exec "$PYTHON" "$ROOT/bot.py" "$@"
    ;;
  *) die "unknown command: $1 (probe, enable, start, stop, fit, config, deps, all, bot)" ;;
esac
