"""Build, test and flash the panel firmware on the Windows PC, from this checkout (uncommitted changes included).

    python3 firmware/relay-display/tools/build_windows.py build            # build.sh, release, into build/
    python3 firmware/relay-display/tools/build_windows.py build --dev      # any build.sh arguments after `build`
    python3 firmware/relay-display/tools/build_windows.py test             # --qemu build, update images, emulator suite
    python3 firmware/relay-display/tools/build_windows.py flash            # flash build/relay-display.bin over USB
    python3 firmware/relay-display/tools/build_windows.py flash --dev --port COM7
    python3 firmware/relay-display/tools/build_windows.py monitor          # the panel's serial console (Ctrl+] quits)
    python3 firmware/relay-display/tools/build_windows.py ports            # the USB serial ports the PC sees

**Where it runs.** The PC reaches over SSH (alias `relay-win`, or $RELAY_WIN_HOST) builds in WSL2 (Ubuntu-24.04),
with Docker Engine running the same digest-pinned `espressif/idf` container CI uses, read from
`.github/workflows/firmware.yml`. So `build.sh` runs unchanged, as it does in CI. Flashing runs on Windows itself,
with esptool on the panel's COM port, so no USB passthrough into WSL is needed.

**What is sent.** The files git knows about under `firmware/relay-display/` and the workflow file
(`tests/build_update.py` reads the container from it) go to /root/relay-fw/repo in WSL, laid out as in the
repository. Build directories and `managed_components/` there are kept between runs, so a second build is
incremental; everything else is replaced. The images a build makes are copied back into the same build directory
here, so `flash` from the Mac's own tools, or the operator page's Flash a panel, can use them too.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import re
import shlex
import subprocess  # nosec B404
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
REPO = PROJECT.parents[1]
DISTRO = "Ubuntu-24.04"
WSL_ROOT = "/root/relay-fw/repo"
WSL_PROJECT = f"{WSL_ROOT}/firmware/relay-display"
# Where flashing reads the image on the Windows side, and where esptool's virtualenv lives.
WIN_BASE = r"%USERPROFILE%\relay-fw"
ESPTOOL = "esptool==5.1.0"
PYTEST = "pytest==9.2.0 pytest-xdist==3.8.0"
# Kept on the PC between syncs: what the builds make, and the components the registry gave them.
KEEP = ("build", "build-*", "managed_components", "dependencies.lock", ".pytest_cache")


def pinned_container() -> str:
    text = (REPO / ".github" / "workflows" / "firmware.yml").read_text()
    match = re.search(r"container: (espressif/idf:v[0-9.]+@sha256:[0-9a-f]{64})", text)
    if match is None:
        raise SystemExit("could not read a digest-pinned espressif/idf out of .github/workflows/firmware.yml")
    return match.group(1)


def ssh(host: str, command: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["ssh", host, command], **kw)  # nosec B603 B607


def wsl(host: str, script: str, **kw) -> int:
    """Run a bash script as root in WSL. Sent base64 so neither cmd nor PowerShell reads any of it."""
    encoded = base64.b64encode(script.encode()).decode()
    return ssh(host, f'wsl -d {DISTRO} -u root -- bash -c "echo {encoded} | base64 -d | bash"', **kw).returncode


def powershell(host: str, script: str, tty: bool = False) -> int:
    """Run a PowerShell script on Windows. -EncodedCommand, because from stdin PowerShell drops multi-line blocks."""
    encoded = base64.b64encode(("$ProgressPreference='SilentlyContinue'\n" + script).encode("utf-16-le")).decode()
    args = ["ssh"] + (["-t"] if tty else []) + [host, f"powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {encoded}"]
    return subprocess.run(args).returncode  # nosec B603 B607


def sync(host: str) -> None:
    files = subprocess.run(  # nosec B603 B607
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--",
         "firmware/relay-display", ".github/workflows/firmware.yml"],
        cwd=REPO, check=True, capture_output=True).stdout
    names = [n for n in files.split(b"\0") if n and (REPO / os.fsdecode(n)).exists()]
    print(f"==> sending {len(names)} files to {host} (WSL {WSL_ROOT})", flush=True)
    keep = " ".join(f"! -name '{k}'" for k in KEEP)
    script = (f"set -e; mkdir -p {WSL_PROJECT}; "
              f"find {WSL_ROOT} -mindepth 1 -maxdepth 1 ! -name firmware -exec rm -rf {{}} +; "
              f"find {WSL_PROJECT} -mindepth 1 -maxdepth 1 {keep} -exec rm -rf {{}} +; "
              f"tar -xf - -C {WSL_ROOT}")
    encoded = base64.b64encode(script.encode()).decode()
    tar = subprocess.Popen(["tar", "-cf", "-", "--null", "-T", "-"], cwd=REPO,  # nosec B603 B607
                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, env={**os.environ, "COPYFILE_DISABLE": "1"})
    remote = subprocess.Popen(  # nosec B603 B607
        ["ssh", host, f'wsl -d {DISTRO} -u root -- bash -c "echo {encoded} | base64 -d > /tmp/relay-sync.sh; bash /tmp/relay-sync.sh"'],
        stdin=tar.stdout)
    tar.stdout.close()
    tar.stdin.write(b"\0".join(names) + b"\0")
    tar.stdin.close()
    if tar.wait() or remote.wait():
        raise SystemExit("sending the files failed")


def out_dir(build_args: list[str]) -> str:
    """The build directory build.sh writes for these arguments (its own rules, repeated)."""
    board = build_args[build_args.index("--board") + 1] if "--board" in build_args else "crowpanel-adv7"
    base = "build" if board == "crowpanel-adv7" else f"build-{board}"
    if "--qemu" in build_args:
        return base + "-qemu"
    if "--dev" in build_args:
        return base + "-dev"
    return base


def fetch(host: str, rel: str, names: list[str]) -> None:
    """Copy the images from WSL back into the same directory here."""
    local = PROJECT / rel
    local.mkdir(parents=True, exist_ok=True)
    for name in names:
        got = ssh(host, f"wsl -d {DISTRO} -u root -- cat {WSL_PROJECT}/{rel}/{name}", capture_output=True)
        if got.returncode == 0 and got.stdout:
            (local / name).write_bytes(got.stdout)
            print(f"    {local.relative_to(REPO)}/{name}  {len(got.stdout)} bytes")


def in_container(host: str, command: str, extra: str = "") -> int:
    image = pinned_container()
    return wsl(host, f"""set -e
cd {WSL_ROOT}
docker image inspect {image} >/dev/null 2>&1 || docker pull -q {image}
docker run --rm -v {WSL_ROOT}:/p -w /p/firmware/relay-display {image} bash -c {shlex.quote('. $IDF_PATH/export.sh >/dev/null && ' + command)}
{extra}""")


def cmd_build(host: str, args: list[str]) -> int:
    if "--sign" in args:
        raise SystemExit("--sign is not sent to the PC: the release key stays where it is. Build without it.")
    sync(host)
    code = in_container(host, f"./build.sh {shlex.join(args)}")
    if code == 0:
        rel = out_dir(args)
        print(f"==> copying the images back into {rel}/")
        fetch(host, rel, ["relay-display.bin", "relay-display-app.bin", "relay-display-app.elf"])
    return code


def source_hash() -> str:
    """The firmware's sources as the update image is built from them: everything synced but the tests."""
    files = subprocess.run(  # nosec B603 B607
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "firmware/relay-display"],
        cwd=REPO, check=True, capture_output=True).stdout
    digest = hashlib.sha256()
    for name in sorted(n for n in files.split(b"\0") if n and not n.startswith(b"firmware/relay-display/tests/")):
        path = REPO / os.fsdecode(name)
        if path.is_file():
            digest.update(name + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def cmd_test(host: str, args: argparse.Namespace, rest: list[str]) -> int:
    sync(host)
    # As README.md "Tests": the emulator build, the update images (built in the container by build_update.py on
    # the WSL side), then the suite against the container's own Espressif QEMU.
    code = in_container(host, "./build.sh --qemu")
    if code:
        return code
    # build_update.py is a clean build of a copied tree, minutes on its own, and its images depend only on the
    # sources and on build-qemu's test key. So it is skipped when neither changed since it last ran.
    stamp = source_hash() + ":" + "$(sha256sum build-qemu/test_signing_key.pem | cut -c1-64)"
    code = wsl(host, f"""set -e; cd {WSL_PROJECT}
want="{stamp}"
if [ -f build-qemu-update/relay-display-app.bin ] && [ -f build-qemu-update/relay-display-app-wrongkey.bin ] \\
   && [ "$(cat build-qemu-update/.sources 2>/dev/null)" = "$want" ]; then
  echo "==> update images: sources unchanged, kept"
else
  echo "==> update images"; python3 tests/build_update.py; echo "$want" > build-qemu-update/.sources
fi""")
    if code:
        return code
    quoted = shlex.join(rest)
    # One emulator per worker, whole files per worker (`loadfile`): a file's tests share its module fixtures, and
    # test_update.py's fake Relay is on a fixed port (8100) that only one worker may hold. The others use port 0.
    workers = f"-n {args.workers} --dist loadfile" if args.workers > 1 else ""
    return in_container(host, f"pip install -q --root-user-action=ignore {PYTEST} && "
                              "RELAY_QEMU=$(which qemu-system-xtensa) "
                              f"python -m pytest tests --firmware {workers} --durations=15 {quoted}")


ENSURE_ESPTOOL = rf"""
$base = [Environment]::ExpandEnvironmentVariables('{WIN_BASE}')
$py = Join-Path $base 'venv\Scripts\python.exe'
if (-not (Test-Path $py)) {{ New-Item -ItemType Directory -Force $base | Out-Null; py -3.15 -m venv (Join-Path $base 'venv') }}
& $py -c "import esptool, serial" 2>$null
if ($LASTEXITCODE) {{ & $py -m pip install -q '{ESPTOOL}' pyserial; if ($LASTEXITCODE) {{ exit $LASTEXITCODE }} }}
function Pick-Port($want) {{
    if ($want) {{ return $want }}
    $ports = @(& $py -c "from serial.tools import list_ports; [print(p.device, '|', p.description) for p in list_ports.comports() if p.vid]")
    if ($ports.Count -eq 1) {{ return ($ports[0] -split ' ')[0] }}
    if ($ports.Count -eq 0) {{ Write-Host 'No USB serial port: is the panel plugged into the PC? (Hold BOOT while plugging in if it does not show.)' }}
    else {{ Write-Host 'More than one USB serial port; pass --port:'; $ports | ForEach-Object {{ Write-Host "  $_" }} }}
    exit 2
}}
"""


def cmd_flash(host: str, args: argparse.Namespace) -> int:
    rel = out_dir(["--dev"] if args.dev else ([] if not args.board else ["--board", args.board]))
    if not args.no_build:
        code = cmd_build(host, (["--dev"] if args.dev else []) + (["--board", args.board] if args.board else []))
        if code:
            return code
    # The image goes from WSL to Windows through /mnt/c, so esptool reads a plain Windows file.
    image = f"{WSL_PROJECT}/{rel}/relay-display.bin"
    win_dir = "/mnt/c/Users/$(cmd.exe /c 'echo %USERNAME%' 2>/dev/null | tr -d '\\r')/relay-fw"
    code = wsl(host, f'set -e; d="{win_dir}"; mkdir -p "$d"; cp {image} "$d/relay-display.bin"; ls -l "$d/relay-display.bin"')
    if code:
        return code
    chip = "esp32p4" if args.board and "p4" in args.board else "esp32s3"
    return powershell(host, ENSURE_ESPTOOL + rf"""
$port = Pick-Port '{args.port or ""}'
Write-Output "==> flashing {rel}/relay-display.bin to $port"
& $py -m esptool --chip {chip} --port $port -b {args.baud} write_flash --flash_mode dio --flash_size keep --flash_freq 80m 0x0 (Join-Path $base 'relay-display.bin')
exit $LASTEXITCODE
""")


def cmd_monitor(host: str, args: argparse.Namespace) -> int:
    return powershell(host, ENSURE_ESPTOOL + rf"""
$port = Pick-Port '{args.port or ""}'
Write-Host "==> $port at 115200. Ctrl+] quits."
& $py -m serial.tools.miniterm --raw --eol LF $port 115200
""", tty=True)


def cmd_ports(host: str) -> int:
    return powershell(host, ENSURE_ESPTOOL + r"""
& $py -c "from serial.tools import list_ports; [print(p.device, '|', p.description, '|', p.hwid) for p in list_ports.comports()]"
""")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=os.environ.get("RELAY_WIN_HOST", "relay-win"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="build.sh in the pinned container; arguments after it go to build.sh")
    te = sub.add_parser("test", help="the emulator suite; other arguments go to pytest")
    te.add_argument("--workers", type=int, default=8, help="emulators at once (default 8; 1 runs serially)")
    fl = sub.add_parser("flash", help="build, then flash the merged image over USB")
    fl.add_argument("--dev", action="store_true", help="the bench image (build-dev/)")
    fl.add_argument("--board", help="another board (build.sh --board)")
    fl.add_argument("--port", help="COM port; found by itself when only one USB serial port is present")
    fl.add_argument("--baud", type=int, default=460800)
    fl.add_argument("--no-build", action="store_true", help="flash what the last build on the PC made")
    mo = sub.add_parser("monitor", help="the panel's serial console")
    mo.add_argument("--port")
    sub.add_parser("ports", help="list the PC's serial ports")
    args, rest = ap.parse_known_args()
    if args.cmd == "build":
        return cmd_build(args.host, rest)
    if args.cmd == "test":
        return cmd_test(args.host, args, rest)
    if rest:
        ap.error(f"unrecognised arguments: {' '.join(rest)}")
    if args.cmd == "flash":
        return cmd_flash(args.host, args)
    if args.cmd == "monitor":
        return cmd_monitor(args.host, args)
    return cmd_ports(args.host)


if __name__ == "__main__":
    sys.exit(main())
