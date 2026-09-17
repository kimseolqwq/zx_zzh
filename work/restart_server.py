from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
pid_file = ROOT / "work" / "uvicorn.pid"
try:
    PID = int(pid_file.read_text(encoding="ascii").strip())
except (OSError, ValueError):
    PID = 0

def windows_listener_pids(port: int) -> set[int]:
    try:
        completed = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except OSError:
        return set()
    pids: set[int] = set()
    for line in completed.stdout.splitlines():
        if "LISTENING" not in line.upper():
            continue
        match = re.search(rf":{port}\s+\S+\s+LISTENING\s+(\d+)\s*$", line, re.IGNORECASE)
        if match:
            pids.add(int(match.group(1)))
    return pids


stale_pids = {PID} if PID else set()
if os.name == "nt":
    stale_pids |= windows_listener_pids(8000)
for stale_pid in stale_pids:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(stale_pid), "/T", "/F"],
            capture_output=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        try:
            os.kill(stale_pid, signal.SIGTERM)
        except OSError:
            pass
time.sleep(1.5)

for _ in range(40):
    if not windows_listener_pids(8000):
        break
    time.sleep(0.25)

python = ROOT / ".venv" / "Scripts" / "python.exe"
stdout = (ROOT / "work" / "uvicorn.out.log").open("ab")
stderr = (ROOT / "work" / "uvicorn.err.log").open("ab")
creationflags = 0
if os.name == "nt":
    creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
process = subprocess.Popen(
    [str(python), "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
    cwd=ROOT,
    stdin=subprocess.DEVNULL,
    stdout=stdout,
    stderr=stderr,
    creationflags=creationflags,
    close_fds=True,
)
(ROOT / "work" / "uvicorn.pid").write_text(str(process.pid), encoding="ascii")

healthy = False
payload = {}
for _ in range(40):
    if process.poll() is not None:
        break
    time.sleep(0.5)
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        healthy = payload.get("status") == "ok"
        if healthy:
            break
    except Exception:
        continue
print(json.dumps({"pid": process.pid, "healthy": healthy, "health": payload}, ensure_ascii=False))
