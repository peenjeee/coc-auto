# Hosting the dashboard and the online relay on the VM

Two services, no build step:

- **coc-relay** — `relay_server.py`, a small WebSocket server for the subset of the Phoenix/Realtime
  protocol the bot and the dashboard already speak. Listens on `127.0.0.1:4000` only.
- **coc-web** — Caddy serving the static `web/` folder on `127.0.0.1:8080` and reverse-proxying
  `/realtime/` to the relay, with the dashboard's security headers (see `Caddyfile`).

The VM has no public IP and no open ports. Public HTTPS is terminated by the Cloudflare Tunnel in front
of it: point the tunnel ingress for `coc.0xpnj.dev` at `http://127.0.0.1:8080`. Page and relay share that
one origin, which is why `web/config.js` uses `location.origin` as the relay address.

## Install

```bash
# 1. Checkout (the units and Caddyfile below assume this path; adjust them if you clone elsewhere)
git clone https://github.com/peenjeee/coc-auto /home/hatch/workspace/coc-auto
cd /home/hatch/workspace/coc-auto

# 2. The relay's venv: websockets only. OpenCV and NumPy are for the bot on the PC, not for the VM.
python3 -m venv .venv
.venv/bin/pip install "websockets>=14,<16"

# 3. Install Caddy (https://caddyserver.com/docs/install); the unit expects /usr/bin/caddy.

# 4. Put the relay token into the relay unit: replace __RELAY_TOKEN__ in coc-relay.service with the
#    RELAY_KEY value from web/config.js (the same token as remote.relay_key in config.json).
#    The token is public by design; it only keeps casual connections out.

# 5. Install and start both services
cp deploy/coc-relay.service deploy/coc-web.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now coc-relay coc-web
```

## Check

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/        # 200: the dashboard page
systemctl status coc-relay coc-web                                     # both active
.venv/bin/python -m unittest test_relay_server                         # protocol tests against the server
```

Then open https://coc.0xpnj.dev: the page loads without CSP errors, and with the bot running on the PC
the dashboard shows the device online, streams the log and answers Start/Stop.

## Update

```bash
cd /home/hatch/workspace/coc-auto
git pull
systemctl restart coc-relay coc-web
```

Nothing is built: the web folder is served as-is and the relay is a single Python file.
