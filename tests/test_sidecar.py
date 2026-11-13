"""The desktop shell's channel (app/sidecar.py, run.py): ready event, quit and parent-gone shutdown.

Starts the real server as a child process with RELAY_SIDECAR=1, the way the shell does. Offline.
Run: .venv/bin/python tests/test_sidecar.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import sidecar  # noqa: E402

fails = []


def check(label, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + label + ((" — " + str(extra)) if extra and not cond else ""))
    if not cond:
        fails.append(label)


print("events")
os.environ.pop("RELAY_SIDECAR", None)
buf = io.StringIO()
real, sys.stdout = sys.stdout, buf
sidecar.emit("ready", x=1)
os.environ["RELAY_SIDECAR"] = "1"
sidecar.emit("ready", x=1)
sys.stdout = real
lines = buf.getvalue().splitlines()
check("nothing is printed unless the shell started Relay", len(lines) == 1, lines)
check("an event is one prefixed JSON line", lines and lines[0] == 'RELAY-EVENT {"event":"ready","x":1}', lines)

print("watching the parent")
stopped = threading.Event()
killed = []
sidecar.watch_parent(stopped.set, stdin=io.StringIO("hello\nquit\n"), grace=0.05, kill=killed.append)
check("`quit` asks for a graceful stop", stopped.wait(2))
time.sleep(0.2)
check("a stop that never finishes is ended after the grace period", killed == [0], killed)
stopped.clear()
sidecar.watch_parent(stopped.set, stdin=io.StringIO(""), grace=5, kill=killed.append)
check("the pipe closing is a graceful stop too", stopped.wait(2))

print("a real backend")
state = tempfile.mkdtemp()
cfg = Path(state) / "config.json"
cfg.write_text(json.dumps({"port": 0, "admin_port": 0, "host": "127.0.0.1", "admin_host": "127.0.0.1"}))
env = dict(os.environ, RELAY_HOME=state, RELAY_SIDECAR="1", RELAY_ANNOUNCE="0", PYTHONUNBUFFERED="1")
proc = subprocess.Popen([sys.executable, str(ROOT / "run.py")], env=env, stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
import queue

t0 = time.time()
out_lines = queue.Queue()
collected = []


def pump():  # a thread, so a child that never prints cannot block the test
    for line in proc.stdout:
        collected.append(f"{time.time() - t0:6.1f}s {line.rstrip()}"[:200])
        out_lines.put(line)


threading.Thread(target=pump, daemon=True).start()
ready = None
deadline = time.time() + 90
while time.time() < deadline and ready is None:
    try:
        line = out_lines.get(timeout=1)
    except queue.Empty:
        if proc.poll() is not None:
            break
        continue
    if line.startswith(sidecar.PREFIX):
        ready = json.loads(line[len(sidecar.PREFIX):])
check("it reports ready with its ports and certificate fingerprint",
      bool(ready) and ready["event"] == "ready" and ready["viewer_port"] > 0 and len(ready["fingerprint"]) == 95, ready)
if ready:
    import http.client
    c = http.client.HTTPConnection("127.0.0.1", ready["viewer_port"], timeout=10)
    c.request("GET", "/healthz")
    check("/healthz answers on the viewer port", c.getresponse().status == 200)
proc.stdin.close()  # the shell crashed: no `quit`, just a closed pipe
try:
    code = proc.wait(timeout=30)
except subprocess.TimeoutExpired:
    proc.kill()
    code = None
check("closing its stdin stops the backend", code is not None, code)
if proc.poll() is None:
    proc.kill()
if fails:
    print("--- backend output ---")
    print("\n".join(collected[-80:]))

if fails:
    print(f"\n{len(fails)} FAILED")
    sys.exit(1)
print("\nall ok")
