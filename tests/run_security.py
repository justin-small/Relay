"""Run the security scans CI runs, on this machine, before the push.

    python tests/run_security.py                 # every scan, several at a time
    python tests/run_security.py -k bandit zizmor  # only scans whose name contains one of these
    python tests/run_security.py --strict        # a scan whose tool is missing fails (CI uses this)
    python tests/run_security.py --install       # print the commands that install the tools
    python tests/run_security.py --install-hook  # run this before every `git push` (opt in)

The scans (the same commands as .github/workflows/security.yml, which calls this script):

    bandit      Python code issues in app/, run.py, tools/ (medium and up). Findings already known are in
                security/bandit-baseline.json; only new ones fail. Re-baseline after reviewing:
                    python tests/run_security.py --rebaseline
    gitleaks    Secrets in the git history.
    actionlint  Mistakes in .github/workflows (including shell scripts in `run:`).
    zizmor      Security problems in the workflows (unpinned actions, injection, token scope).
    cargo-deny  Rust crates: advisories, licences, sources (desktop/src-tauri/deny.toml).

pip-audit, npm audit and cargo audit stay in tests.yml (the "Dependency audit" job). CodeQL runs on GitHub
(repository default setup); it has no local equivalent here, so read its alerts on the pull request.

Exit status is 1 if any scan found something. A missing tool is skipped with the install command, unless --strict.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import shlex
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = "security/bandit-baseline.json"
BANDIT_TARGETS = ["app", "run.py", "tools"]


@dataclass
class Scan:
  name: str
  tool: str                 # the executable that must exist
  argv: list[str]
  hint: str                 # how to install it
  cwd: Path = ROOT
  module: str | None = None  # a Python module to run with this interpreter when `tool` is not on PATH


def _bandit_argv(*extra: str) -> list[str]:
  return ["-r", *BANDIT_TARGETS, "-x", "app/static", "-ll", "-q", *extra]


SCANS = [
  Scan("bandit", "bandit", _bandit_argv("-b", BASELINE), "pip install bandit==1.9.4", module="bandit"),
  Scan("gitleaks", "gitleaks", ["git", "--no-banner", "--redact", "-l", "error", "."], "brew install gitleaks"),
  Scan("actionlint", "actionlint", ["-color"], "brew install actionlint"),
  Scan("zizmor", "zizmor", ["--no-progress", ".github"], "brew install zizmor   (or: pip install zizmor==1.32.0)"),
  Scan("cargo-deny", "cargo-deny", ["check"], "cargo install cargo-deny --locked --version 0.20.2   (CI installs the same version prebuilt)",
       cwd=ROOT / "desktop" / "src-tauri"),
]


def command(scan: Scan) -> list[str] | None:
  """The full command line, or None when the tool is not installed."""
  # A tool in this checkout's venv counts even when the venv is not activated.
  venv = ROOT / ".venv" / ("Scripts" if os.name == "nt" else "bin")
  exe = shutil.which(scan.tool) or shutil.which(scan.tool, path=str(venv))
  if scan.tool == "cargo-deny":
    # `cargo deny` is the subcommand form of the same binary.
    return [shutil.which("cargo") or "cargo", "deny", *scan.argv] if exe else None
  if exe:
    return [exe, *scan.argv]
  if scan.module:
    probe = subprocess.run([sys.executable, "-c", f"import {scan.module}"], capture_output=True)
    if probe.returncode == 0:
      return [sys.executable, "-m", scan.module, *scan.argv]
  return None


def run(scan: Scan, strict: bool) -> tuple[Scan, str, str]:
  """(scan, status, output) where status is ok, FAILED or skipped."""
  argv = command(scan)
  if argv is None:
    return scan, ("FAILED" if strict else "skipped"), f"{scan.tool} is not installed.  Install: {scan.hint}\n"
  p = subprocess.run(argv, cwd=scan.cwd, capture_output=True, text=True)
  out = (p.stdout or "") + (p.stderr or "")
  return scan, ("ok" if p.returncode == 0 else "FAILED"), out


def rebaseline() -> int:
  import json
  scan = SCANS[0]
  argv = command(scan)
  if argv is None:
    print(f"bandit is not installed.  Install: {scan.hint}")
    return 1
  base = argv[: len(argv) - len(scan.argv)]
  out = ROOT / BASELINE
  subprocess.run([*base, *_bandit_argv("-f", "json", "-o", str(out))], cwd=ROOT, capture_output=True)
  data = json.loads(out.read_text())
  for key in ("generated_at", "metrics", "errors"):
    data.pop(key, None)
  out.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
  print(f"{len(data['results'])} accepted findings written to {BASELINE}. Review the diff before committing it.")
  return 0


def install_hook() -> int:
  hook = ROOT / ".git" / "hooks" / "pre-push"
  if hook.exists() and "run_security.py" not in hook.read_text():
    print(f"{hook} already exists and is not ours; add `python tests/run_security.py` to it yourself.")
    return 1
  py = ROOT / ".venv" / "bin" / "python"
  hook.write_text(
    "#!/bin/sh\n"
    "# Installed by `python tests/run_security.py --install-hook`. Skip once with: git push --no-verify\n"
    f'cd "{ROOT}" && exec {shlex.quote(str(py) if py.exists() else sys.executable)} tests/run_security.py\n')
  hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
  print(f"Installed {hook}. It runs the security scans before every push.")
  return 0


def main() -> int:
  ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
  ap.add_argument("-k", nargs="+", metavar="PART", help="only scans whose name contains one of these")
  ap.add_argument("--strict", action="store_true", help="a missing tool fails instead of being skipped")
  ap.add_argument("--install", action="store_true", help="print the commands that install the tools")
  ap.add_argument("--install-hook", action="store_true", help="run these scans in a git pre-push hook")
  ap.add_argument("--rebaseline", action="store_true", help="accept bandit's current findings")
  a = ap.parse_args()

  if a.install:
    print("\n".join(s.hint for s in SCANS))
    return 0
  if a.install_hook:
    return install_hook()
  if a.rebaseline:
    return rebaseline()

  chosen = [s for s in SCANS if not a.k or any(k in s.name for k in a.k)]
  if not chosen:
    print("No scan matches that name.")
    return 2
  failed, skipped = [], []
  with cf.ThreadPoolExecutor(max_workers=len(chosen)) as pool:
    for scan, status, out in pool.map(lambda s: run(s, a.strict), chosen):
      print(f"\n=== {scan.name}: {status}")
      if status != "ok" and out.strip():
        print(out.rstrip())
      (failed if status == "FAILED" else skipped if status == "skipped" else []).append(scan.name)
  print("\n" + "-" * 60)
  for scan in chosen:
    s = "FAILED" if scan.name in failed else "skipped" if scan.name in skipped else "ok"
    print(f"  {s:8} {scan.name}")
  if skipped:
    print(f"\n{len(skipped)} skipped because the tool is missing: python tests/run_security.py --install")
  return 1 if failed else 0


if __name__ == "__main__":
  sys.exit(main())
