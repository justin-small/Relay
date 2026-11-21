"""Build the audio bus core (native/relay-bus, #601) and install it into this Python.

    python tools/build_bus_wheel.py              # build and install
    python tools/build_bus_wheel.py --if-stale   # only when the Rust sources are newer than what is installed

The Python backend imports `relay_bus`, a Rust extension, so every checkout, test job and release build needs it.
It needs Rust (https://rustup.rs) and, on Linux, the ALSA headers (`sudo apt install libasound2-dev pkg-config`).
maturin is installed first from requirements-wheel.txt, which pins it with hashes. One abi3 wheel covers every Python
from 3.10. The Linux wheel is tagged for this machine (`--compatibility linux`) because it links the system ALSA
library, which the manylinux rules forbid.
"""
from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NATIVE = ROOT / "native" / "relay-bus"
MANIFEST = NATIVE / "crates" / "relay-bus-py" / "Cargo.toml"


def newest_source() -> float:
    files = [p for p in NATIVE.rglob("*") if p.is_file() and "target" not in p.relative_to(NATIVE).parts
             and p.suffix in (".rs", ".toml", ".lock", ".py") and ".venv-build" not in p.relative_to(NATIVE).parts
             and "bench" not in p.relative_to(NATIVE).parts]
    return max((p.stat().st_mtime for p in files), default=0.0)


def installed() -> Path | None:
    """The installed native module, if the wheel is there."""
    spec = importlib.util.find_spec("relay_bus._native") if importlib.util.find_spec("relay_bus") else None
    return Path(spec.origin) if spec and spec.origin else None


def run(*argv: str) -> None:
    print("+", " ".join(argv), flush=True)
    subprocess.run(argv, check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--if-stale", action="store_true", help="do nothing when the installed wheel is current")
    args = ap.parse_args()
    here = installed()
    if args.if_stale and here is not None and here.stat().st_mtime >= newest_source():
        print("relay_bus is current")
        return 0
    if shutil.which("cargo") is None:
        print("Rust is not installed. Install it from https://rustup.rs, then run this again.", file=sys.stderr)
        return 1
    run(sys.executable, "-m", "pip", "install", "--quiet", "--require-hashes", "--only-binary=:all:",
        "-r", str(ROOT / "requirements-wheel.txt"))
    out = tempfile.mkdtemp(prefix="relay-bus-wheel-")
    try:
        compat = "linux" if sys.platform.startswith("linux") else "pypi"
        run(sys.executable, "-m", "maturin", "build", "--release", "--locked", "--compatibility", compat,
            "--manifest-path", str(MANIFEST), "--out", out)
        wheels = sorted(Path(out).glob("relay_bus-*.whl"))
        if not wheels:
            print("maturin built no wheel", file=sys.stderr)
            return 1
        run(sys.executable, "-m", "pip", "install", "--quiet", "--force-reinstall", "--no-deps", str(wheels[-1]))
    finally:
        shutil.rmtree(out, ignore_errors=True)
    run(sys.executable, "-c", "import relay_bus; print('relay_bus', relay_bus.__file__)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
