"""The per-user state folder (app/paths.py): where it is, how it is chosen, what first run creates.

Offline. Run: .venv/bin/python tests/test_paths.py
"""
import importlib
import os
import stat
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for k in ("RELAY_CONFIG", "RELAY_HOME", "RELAY_BLOCKLIST", "RELAY_RECORDINGS"):
    os.environ.pop(k, None)

from app import paths  # noqa: E402

fails = []


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


def check(label, cond):
    eq(label, bool(cond), True)


home = Path.home()
with mock.patch.object(sys, "platform", "darwin"):
    eq("macOS default", paths.default_home(), home / "Library" / "Application Support" / "Relay")
with mock.patch.object(sys, "platform", "linux"), mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/x/data"}):
    eq("Linux honours XDG_DATA_HOME", paths.default_home(), Path("/x/data/relay"))
with mock.patch.object(sys, "platform", "linux"), mock.patch.dict(os.environ, {}, clear=True):
    eq("Linux falls back to ~/.local/share/relay", paths.default_home(), home / ".local" / "share" / "relay")
with mock.patch.object(sys, "platform", "win32"), mock.patch.dict(os.environ, {"APPDATA": "C:/Users/a/AppData/Roaming"}):
    eq("Windows uses %APPDATA%\\Relay", paths.default_home(), Path("C:/Users/a/AppData/Roaming") / "Relay")

work = Path(tempfile.mkdtemp())
with mock.patch.dict(os.environ, {"RELAY_HOME": str(work / "h")}, clear=False):
    eq("RELAY_HOME sets the folder", paths.state_dir(), work / "h")
with mock.patch.dict(os.environ, {"RELAY_HOME": str(work / "h"), "RELAY_CONFIG": str(work / "c" / "x.json")}):
    eq("RELAY_CONFIG still wins for the file", paths.config_path(), work / "c" / "x.json")
    eq("…and the state folder follows it", paths.state_dir(), work / "c")

# First run in a fresh home: folder 0700, config 0600, blocklist seeded from the packaged copy.
fresh = work / "fresh" / "Relay"
code = f"""
import os, sys
sys.path.insert(0, {str(ROOT)!r})
from app import config
config.ensure_file()
bl = config.ensure_blocklist_file()
print(config.CONFIG_PATH, bl, config.recordings_path(), sep="\\n")
"""
import subprocess

env = {k: v for k, v in os.environ.items() if k not in ("RELAY_CONFIG", "RELAY_BLOCKLIST", "RELAY_RECORDINGS")}
env["RELAY_HOME"] = str(fresh)
out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
check("first run succeeds", out.returncode == 0)
cfg, bl, rec = (Path(p) for p in out.stdout.split("\n")[:3])
eq("config.json is in the home", cfg, fresh / "config.json")
check("config.json exists", cfg.is_file())
check("blocklist seeded from the packaged copy", bl == fresh / "blocklist.txt"
      and bl.read_text(encoding="utf-8") == (ROOT / "blocklist.txt").read_text(encoding="utf-8"))
eq("recordings default into the home", rec, fresh / "recordings")
if sys.platform != "win32":
    eq("folder is 0700", stat.S_IMODE(fresh.stat().st_mode), 0o700)
    eq("config.json is 0600", stat.S_IMODE(cfg.stat().st_mode), 0o600)

print()
if fails:
    print(f"{len(fails)} failed")
    sys.exit(1)
print("all paths checks passed")
