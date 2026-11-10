"""Run the bundled executable with an isolated profile and minimal system PATH."""

import hashlib
import hmac
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request


def smoke(app):
    with tempfile.TemporaryDirectory(prefix="CoC Auto test é ") as temporary:
        data = Path(temporary)
        installed = data / "インストール é" / "CoC Auto"
        shutil.copytree(app, installed)
        exe = str(installed / "CoC Auto.exe")
        env = {
            **os.environ,
            "COC_AUTO_DATA_DIR": str(data),
            "COC_AUTO_NO_BROWSER": "1",
            "PATH": str(Path(os.environ["SystemRoot"]) / "System32"),
        }
        subprocess.run([exe, "--self-test"], env=env, check=True, timeout=90)
        subprocess.run(
            [exe, "--worker", "--help"],
            env=env,
            check=True,
            timeout=30,
            stdout=subprocess.DEVNULL,
        )
        # Run the extracted app from an unrelated working directory.
        log = (data / "smoke.txt").open("w", encoding="utf-8")
        process = subprocess.Popen(
            [exe],
            cwd=data,
            env=env,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        try:
            deadline = time.monotonic() + 30
            while not (data / "launcher.json").is_file():
                if process.poll() is not None:
                    raise RuntimeError((data / "smoke.txt").read_text())
                if time.monotonic() > deadline:
                    raise TimeoutError("Dashboard did not start")
                time.sleep(0.1)
            port = json.loads((data / "launcher.json").read_text())["port"]
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base = f"http://localhost:{port}"
            with opener.open(base + "/api/health") as response:
                assert json.load(response)["app"] == "coc-auto-local-dashboard"
            with opener.open(base + "/") as response:
                assert b'id="setup-check"' in response.read()
            key = json.loads((data / "device.json").read_text())["key"]
            body = json.dumps(
                {
                    "action": "get_settings",
                    "args": {},
                    "ts": time.time(),
                    "nonce": "release-smoke-settings",
                }
            )
            payload = json.dumps(
                {
                    "body": body,
                    "sig": hmac.new(
                        key.encode(), body.encode(), hashlib.sha256
                    ).hexdigest(),
                }
            ).encode()
            request = urllib.request.Request(
                base + "/api/command",
                data=payload,
                headers={"Content-Type": "application/json", "Origin": base},
            )
            with opener.open(request) as response:
                assert json.load(response)["ok"]
            # A repeated launch exits, while the first service remains alive.
            subprocess.run([exe], cwd=data, env=env, check=True, timeout=20)
            assert process.poll() is None
        finally:
            if process.poll() is None:
                process.send_signal(signal.CTRL_BREAK_EVENT)
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            log.close()
        assert not (data / "launcher.json").exists(), (
            "Launcher did not cleanly release its state"
        )


if __name__ == "__main__":
    smoke(Path(sys.argv[1]).resolve())
