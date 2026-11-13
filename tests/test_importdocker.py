"""Importing a v2.0 Docker install's docker-config/ into the state folder (app/importdocker.py, the first-run
form and tools/import_docker.py). Offline. Run: .venv/bin/python tests/test_importdocker.py
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
work = Path(tempfile.mkdtemp())
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ["RELAY_CONFIG"] = str(work / "web" / "config.json")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

from app import config, importdocker  # noqa: E402

fails = []


def check(label, cond):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        fails.append(label)


def snapshot(folder: Path) -> dict:
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


OLD = {
    "openai_api_key": "sk-test-key", "admin_token": "tok-old", "audio_device": "PulseAudio: Some Mic",
    "source_language": "ENGLISH",
    "targets": {"SPANISH": {"enabled": True}, "FRENCH": {"enabled": False}},
    "blocklist_file": "/app/config/blocklist.txt",
    "recording": {"enabled": True, "dir": "/app/config/recordings", "keep_runs": 5},
    "schedules": [{"id": "s1", "name": "Sunday"}],
    "modules": {"propresenter": {"enabled": True}},
    "panels": {"abc": {"control_key": "k1", "feed_rev": 3}},
    "host": "0.0.0.0", "admin_host": "127.0.0.1", "port": 8000, "admin_port": 8001,
}
old = work / "Relay" / "docker-config"
(old / "recordings" / "20261101-100000").mkdir(parents=True)
(old / "recordings" / "20261101-100000" / "t.txt").write_text("hello")
(old / "panel-firmware" / "crowpanel").mkdir(parents=True)
(old / "panel-firmware" / "crowpanel" / "fw.bin").write_bytes(b"\x01\x02")
(old / "certs").mkdir()
(old / "certs" / "admin.crt").write_text("old cert")
(old / "blocklist.txt").write_text("badword\n")
(old / "config.json").write_text(json.dumps(OLD))
(old / "Caddyfile").write_text("x")
before = snapshot(work / "Relay")

print("import")
dest = work / "new"
rep = importdocker.import_from(work / "Relay", dest)       # the old Relay folder, not docker-config
got = json.loads((dest / "config.json").read_text())
check("settings kept: key, languages, modules, schedules, panels",
      got["openai_api_key"] == "sk-test-key" and got["targets"] == OLD["targets"] and got["modules"] == OLD["modules"]
      and got["schedules"] == OLD["schedules"] and got["panels"] == OLD["panels"])
check("container paths made relative", got["blocklist_file"] == "blocklist.txt" and got["recording"]["dir"] == "recordings")
check("container host and ports dropped", not any(k in got for k in ("host", "admin_host", "port", "admin_port")))
check("old PulseAudio input falls back to the default, with a notice",
      got["audio_device"] is None and any("PulseAudio" in n for n in rep.notes))
check("the shared admin token is carried so first-run can use it once", got["admin_token"] == "tok-old")
check("blocklist, recordings and firmware copied",
      (dest / "blocklist.txt").read_text() == "badword\n"
      and (dest / "recordings" / "20261101-100000" / "t.txt").read_text() == "hello"
      and (dest / "panel-firmware" / "crowpanel" / "fw.bin").read_bytes() == b"\x01\x02")
check("certs and the Caddyfile are not imported", not (dest / "certs").exists() and not (dest / "Caddyfile").exists())
check("the certificate sentence is in the report", any("new one" in n for n in rep.notes))
check("the original folder is byte-for-byte unchanged", snapshot(work / "Relay") == before)
if os.name == "posix":
    check("config.json is owner-only", (dest / "config.json").stat().st_mode & 0o077 == 0)

print("twice")
mine = snapshot(dest)
rep2 = importdocker.import_from(old, dest)
check("a second import changes nothing", snapshot(dest) == mine and not rep2.changed)

print("refusals")
(dest / "config.json").write_text(json.dumps({"openai_api_key": "sk-other"}))
try:
    importdocker.import_from(old, dest)
    check("a config with its own key is not replaced", False)
except importdocker.ImportRefused:
    check("a config with its own key is not replaced",
          json.loads((dest / "config.json").read_text())["openai_api_key"] == "sk-other")
importdocker.import_from(old, dest, force=True)
check("--force replaces it and keeps the old one", (dest / "config.json.before-import").exists()
      and json.loads((dest / "config.json").read_text())["openai_api_key"] == "sk-test-key")
(dest / "blocklist.txt").write_text("mine\n")
(dest / "recordings" / "20261101-100000" / "t.txt").write_text("edited")
importdocker.import_from(old, dest)
check("a blocklist or recording already here is kept",
      (dest / "blocklist.txt").read_text() == "mine\n"
      and (dest / "recordings" / "20261101-100000" / "t.txt").read_text() == "edited")
for bad in (work / "empty", old / "recordings"):
    bad.mkdir(exist_ok=True)
    try:
        importdocker.import_from(bad, work / "x")
        check(f"{bad.name}: not a Relay folder is refused", False)
    except importdocker.ImportRefused:
        check(f"{bad.name}: not a Relay folder is refused", not (work / "x").exists())
try:
    importdocker.import_from(old, old)
    check("same folder refused", False)
except importdocker.ImportRefused:
    check("same folder refused", True)

print("command line")
cli = work / "cli"
r = subprocess.run([sys.executable, str(ROOT / "tools" / "import_docker.py"), str(old), "--to", str(cli)],
                   capture_output=True, text=True)
check("tools/import_docker.py imports", r.returncode == 0 and (cli / "config.json").exists())
check("…and prints no secret", "sk-test-key" not in r.stdout + r.stderr and "tok-old" not in r.stdout + r.stderr)
r = subprocess.run([sys.executable, str(ROOT / "tools" / "import_docker.py"), str(work / "empty"), "--to", str(cli)],
                   capture_output=True, text=True)
check("…and exits 1 on a folder that is not Relay's", r.returncode == 1)

print("first-run page")
from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app import main as M  # noqa: E402

config.ensure_file()
local = TestClient(app, base_url="http://relay.local:8001", client=("127.0.0.1", 5))
remote = TestClient(app, base_url="http://relay.local:8001", client=("10.0.0.9", 5))
check("the first-run page offers the import", 'action="/admin/import"' in local.get("/admin").text)
check("…not to another machine", remote.post("/admin/import", data={"folder": str(old)}).status_code == 403)
r = local.post("/admin/import", data={"folder": str(work / "empty")})
check("a wrong folder is a sentence, not a crash", r.status_code == 400 and "config.json" in r.text)
r = local.post("/admin/import", data={"folder": str(old)})
check("importing from this machine works and shows the fingerprint note",
      r.status_code == 200 and "Imported:" in r.text and "certificate" in r.text)
check("…and the live config now has the old settings", config.get()["openai_api_key"] == "sk-test-key")
check("…which asks for the old admin token", 'name="token"' in r.text)
r = local.post("/admin/setup", data={"token": "tok-old", "username": "boss", "name": "Boss",
                                     "password": "first password", "confirm": "first password"},
               follow_redirects=False)
check("the old token creates the first Owner", r.status_code == 303)
check("…and is retired afterwards", config.read_raw().get("admin_token", "") == "")
check("…and the import is refused once an account exists",
      TestClient(app, base_url="http://relay.local:8001", client=("127.0.0.1", 5))
      .post("/admin/import", data={"folder": str(old)}).status_code == 403)

print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
