"""Build Relay's Rust extensions and install them into this Python: the audio bus core (native/relay-bus, #601, the
`relay_bus` module) and the NDI engine (native/relay-ndi, #606, the `relay_ndi` module).

    python tools/build_bus_wheel.py              # build and install both
    python tools/build_bus_wheel.py --if-stale   # only those whose Rust sources are newer than what is installed

The Python backend imports both, so every checkout, test job and release build needs them.
It needs Rust (https://rustup.rs) and, on Linux, the ALSA headers (`sudo apt install libasound2-dev pkg-config`) for the
bus. The NDI engine opens the NDI runtime at run time and needs nothing installed to build.
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


class Wheel:
    def __init__(self, folder: str, crate: str, dist: str, module: str) -> None:
        self.native = ROOT / "native" / folder
        self.manifest = self.native / "crates" / crate / "Cargo.toml"
        self.dist, self.module = dist, module

    def newest_source(self) -> float:
        files = [p for p in self.native.rglob("*") if p.is_file() and "target" not in p.relative_to(self.native).parts
                 and p.suffix in (".rs", ".toml", ".lock", ".py") and ".venv-build" not in p.relative_to(self.native).parts
                 and "bench" not in p.relative_to(self.native).parts]
        return max((p.stat().st_mtime for p in files), default=0.0)

    def installed(self) -> Path | None:
        """The installed native module, if the wheel is there."""
        spec = importlib.util.find_spec(f"{self.module}._native") if importlib.util.find_spec(self.module) else None
        return Path(spec.origin) if spec and spec.origin else None


WHEELS = [Wheel("relay-bus", "relay-bus-py", "relay_bus", "relay_bus"), Wheel("relay-ndi", "relay-ndi-py", "relay_ndi", "relay_ndi")]


def run(*argv: str) -> None:
    print("+", " ".join(argv), flush=True)
    subprocess.run(argv, check=True)


def build(w: Wheel) -> int:
    out = tempfile.mkdtemp(prefix=f"{w.dist}-wheel-")
    try:
        compat = "linux" if sys.platform.startswith("linux") else "pypi"
        run(sys.executable, "-m", "maturin", "build", "--release", "--locked", "--compatibility", compat,
            "--manifest-path", str(w.manifest), "--out", out)
        wheels = sorted(Path(out).glob(f"{w.dist}-*.whl"))
        if not wheels:
            print("maturin built no wheel", file=sys.stderr)
            return 1
        run(sys.executable, "-m", "pip", "install", "--quiet", "--force-reinstall", "--no-deps", str(wheels[-1]))
    finally:
        shutil.rmtree(out, ignore_errors=True)
    run(sys.executable, "-c", f"import {w.module}; print('{w.module}', {w.module}.__file__)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--if-stale", action="store_true", help="do nothing for a wheel that is current")
    args = ap.parse_args()
    todo = []
    for w in WHEELS:
        here = w.installed()
        if args.if_stale and here is not None and here.stat().st_mtime >= w.newest_source():
            print(f"{w.module} is current")
        else:
            todo.append(w)
    if not todo:
        return 0
    if shutil.which("cargo") is None:
        print("Rust is not installed. Install it from https://rustup.rs, then run this again.", file=sys.stderr)
        return 1
    run(sys.executable, "-m", "pip", "install", "--quiet", "--require-hashes", "--only-binary=:all:",
        "-r", str(ROOT / "requirements-wheel.txt"))
    for w in todo:
        if build(w):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
