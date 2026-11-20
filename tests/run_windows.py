"""Run the test suite on a Windows PC over SSH, against this working tree (uncommitted changes included).

    python tests/run_windows.py                 # the whole suite (tests/run_all.py), as CI's Windows job runs it
    python tests/run_windows.py -k lighting     # arguments after the options go to run_all.py
    python tests/run_windows.py --js            # also run node --test tests/js/*.test.mjs
    python tests/run_windows.py --shell "python -c 'import sys; print(sys.version)'"   # any command in the copy

The PC needs OpenSSH Server, key login, Python 3.15 (`py -3.15`) and, for --js, Node 22. The host is an SSH
alias (default `relay-win`, or $RELAY_WIN_HOST) set up in ~/.ssh/config. Each run sends the files git knows about
(tracked, plus untracked ones that are not ignored) to a fresh %USERPROFILE%\\relay-test\\src; the virtualenv beside
it is kept and reinstalled only when requirements.txt changes. Exits with the remote suite's exit code.
"""

import argparse
import base64
import hashlib
import os
import subprocess  # nosec B404
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Test-only packages, as tests.yml installs them for the Windows job.
TEST_PACKAGES = "httpx2==2.13.1"

REMOTE = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
$base = Join-Path $env:USERPROFILE 'relay-test'
$venv = Join-Path $base 'venv'
$py = Join-Path $venv 'Scripts\python.exe'
Set-Location (Join-Path $base 'src')
if (-not (Test-Path $py)) { py -3.15 -m venv $venv; if ($LASTEXITCODE) { exit $LASTEXITCODE } }
$stamp = Join-Path $base 'requirements.sha256'
if (-not (Test-Path $stamp) -or (Get-Content $stamp) -ne '__REQ_HASH__') {
    & $py -m pip install -q -U pip
    & $py -m pip install -q -r requirements.txt __TEST_PACKAGES__
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
    Set-Content $stamp '__REQ_HASH__'
}
$env:Path = (Join-Path $venv 'Scripts') + ';' + $env:Path
$ErrorActionPreference = 'Continue'
__COMMAND__
exit $LASTEXITCODE
"""


def ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=os.environ.get("RELAY_WIN_HOST", "relay-win"))
    ap.add_argument("--js", action="store_true", help="also run the JavaScript tests with node")
    ap.add_argument("--shell", help="run this PowerShell command in the copy instead of the suite")
    ap.add_argument("--no-sync", action="store_true", help="reuse the copy already on the PC")
    args, rest = ap.parse_known_args()

    if not args.no_sync:
        files = subprocess.run(  # nosec B603 B607
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT, check=True, capture_output=True).stdout
        # Deleted-but-unstaged files are still listed by --cached; tar must not trip on them.
        names = [n for n in files.split(b"\0") if n and (ROOT / os.fsdecode(n)).exists()]
        print(f"sending {len(names)} files to {args.host}", flush=True)
        tar = subprocess.Popen(  # nosec B603 B607
            ["tar", "-cf", "-", "--null", "-T", "-"], cwd=ROOT,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            env={**os.environ, "COPYFILE_DISABLE": "1"})  # no macOS ._ files
        # cmd is the SSH shell; %VAR% expands when the line is read, so no `set` on the same line.
        d = '"%USERPROFILE%\\relay-test\\src"'
        unpack = f"(if exist {d} rmdir /s /q {d}) & mkdir {d} && tar -xf - -C {d}"
        ssh = subprocess.Popen(["ssh", args.host, unpack], stdin=tar.stdout)  # nosec B603 B607
        tar.stdout.close()
        tar.stdin.write(b"\0".join(names) + b"\0")
        tar.stdin.close()
        if tar.wait() or ssh.wait():
            print("sending the files failed", file=sys.stderr)
            return 1

    if args.shell:
        command = args.shell
    else:
        command = "python tests/run_all.py " + " ".join(ps_quote(a) for a in rest)
        if args.js:
            command += ("\n$suite = $LASTEXITCODE\n"
                        "node --test (Get-ChildItem tests/js/*.test.mjs | ForEach-Object FullName)\n"
                        "if ($suite) { exit $suite }")

    req_hash = hashlib.sha256((ROOT / "requirements.txt").read_bytes() + TEST_PACKAGES.encode()).hexdigest()
    script = (REMOTE.replace("__REQ_HASH__", req_hash).replace("__TEST_PACKAGES__", TEST_PACKAGES)
              .replace("__COMMAND__", command))
    # -EncodedCommand, not `-Command -`: from stdin PowerShell reads line by line and silently drops multi-line
    # blocks. cmd (the SSH shell) caps a line at 8191 characters, which this stays well under.
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    return subprocess.run(  # nosec B603 B607
        ["ssh", args.host, "powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand " + encoded],
        stdin=subprocess.DEVNULL).returncode


if __name__ == "__main__":
    sys.exit(main())
