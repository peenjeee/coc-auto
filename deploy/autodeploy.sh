#!/usr/bin/env bash
# coc-auto auto-deploy: pull origin main into the production checkout and restart only what changed.
# Driven by coc-autodeploy.timer (every 5 minutes). Writes only to its log; a no-change run logs nothing.
set -u

REPO=/home/hatch/workspace/coc-auto
LOG=/home/hatch/workspace/coc-auto-autodeploy.log

exec 9>/tmp/coc-autodeploy.lock
flock -n 9 || exit 0

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

# Keep the log bounded (~200 KB).
if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG")" -gt 204800 ]; then
    tail -c 102400 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

# systemd services start with a clean environment, but this VM reaches the internet only
# through its egress proxy; borrow the proxy variables from the stack's persistent env file.
PROXY_ENV=/home/hatch/workspace/9router-hermes-setup/openclaw-gateway.env
if [ -f "$PROXY_ENV" ]; then
    for v in http_proxy https_proxy HTTP_PROXY HTTPS_PROXY no_proxy NO_PROXY; do
        val=$(grep -E "^$v=" "$PROXY_ENV" | head -1 | cut -d= -f2- | tr -d '"')
        [ -n "$val" ] && export "$v=$val"
    done
fi

TOK=$(cat /home/hatch/workspace/.github-pat 2>/dev/null) || { log "FAIL: cannot read GitHub PAT"; exit 1; }
REMOTE="https://x-access-token:${TOK}@github.com/peenjeee/coc-auto"

cd "$REPO" || { log "FAIL: checkout missing"; exit 1; }
GIT="git -c safe.directory=$REPO"
$GIT fetch -q "$REMOTE" main || { log "FAIL: git fetch"; exit 1; }

LOCAL=$($GIT rev-parse HEAD)
REMOTE_HEAD=$($GIT rev-parse FETCH_HEAD)
[ "$LOCAL" = "$REMOTE_HEAD" ] && exit 0

CHANGED=$($GIT diff --name-only "$LOCAL" "$REMOTE_HEAD")
if ! $GIT merge --ff-only -q "$REMOTE_HEAD"; then
    log "FAIL: not a fast-forward (${LOCAL:0:9} -> ${REMOTE_HEAD:0:9}); running version left untouched"
    exit 1
fi
log "updated ${LOCAL:0:9} -> ${REMOTE_HEAD:0:9} | files: $(echo "$CHANGED" | tr '\n' ' ')"

# systemd unit changes: install first (relay unit needs its token substituted), reload, then restart.
if echo "$CHANGED" | grep -qE '^deploy/coc-(web|relay|analytics)\.service$'; then
    if echo "$CHANGED" | grep -q 'coc-relay\.service'; then
        COC_TOKEN=$(grep -o "RELAY_KEY = '[^']*'" "$REPO/web/config.js" | sed "s/.*'\(.*\)'/\1/")
        sed "s/__RELAY_TOKEN__/$COC_TOKEN/" "$REPO/deploy/coc-relay.service" | sudo tee /etc/systemd/system/coc-relay.service >/dev/null
    fi
    echo "$CHANGED" | grep -q 'coc-web\.service' && sudo cp -f "$REPO/deploy/coc-web.service" /etc/systemd/system/coc-web.service
    echo "$CHANGED" | grep -q 'coc-analytics\.service' && sudo cp -f "$REPO/deploy/coc-analytics.service" /etc/systemd/system/coc-analytics.service
    sudo systemctl daemon-reload
    for u in coc-relay coc-web coc-analytics; do
        echo "$CHANGED" | grep -q "deploy/$u.service" && sudo systemctl restart "$u" && log "$u unit updated + restarted"
    done
fi

# Caddyfile changes: validate before restarting coc-web; an invalid file never takes the site down.
if echo "$CHANGED" | grep -qx 'deploy/Caddyfile'; then
    B=$(sed -n 's/^ANALYTICS_BCRYPT=//p' /home/hatch/workspace/coc-analytics/analytics.env | tr -d "'\"")
    if ANALYTICS_BCRYPT="$B" /home/hatch/workspace/bin/caddy validate --config "$REPO/deploy/Caddyfile" --adapter caddyfile >/dev/null 2>&1; then
        sudo systemctl restart coc-web && log "coc-web restarted (Caddyfile changed, valid)"
    else
        log "FAIL: new Caddyfile is invalid; coc-web NOT restarted"
    fi
fi

# Relay code is self-contained in relay_server.py (stdlib only).
if echo "$CHANGED" | grep -qx 'relay_server.py'; then
    sudo systemctl restart coc-relay && log "coc-relay restarted (relay_server.py changed)"
fi

sleep 2
for s in coc-relay coc-web coc-analytics; do
    systemctl is-active -q "$s" || log "FAIL: $s is not active after the update"
done
exit 0
