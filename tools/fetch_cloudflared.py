#!/usr/bin/env python3
"""Put a pinned, checksum-checked cloudflared beside Relay, for the Remote access module (#50).

Relay never downloads anything at run time; this runs when an install is built (the desktop release workflow) or set
up (tools/install-service.sh). The version is pinned and every file's SHA-256 is written here, taken from the
checksums Cloudflare publishes with the release, so a changed file on the download side fails the build.

  python tools/fetch_cloudflared.py --dest desktop/src-tauri/relay-backend      this machine's platform
  python tools/fetch_cloudflared.py --dest DIR --platform linux-arm64           another one

To move to a newer cloudflared: pick a release at least two weeks old, copy its "SHA256 Checksums" lines from
https://github.com/cloudflare/cloudflared/releases into ASSETS below, the macOS archives' digests (gh api
repos/cloudflare/cloudflared/releases/tags/<version>, field digest) into ARCHIVE_SHA256, and change VERSION.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import platform
import ssl
import stat
import sys
import tarfile
import urllib.request
from pathlib import Path

VERSION = "2026.10.6"
URL = "https://github.com/cloudflare/cloudflared/releases/download/{version}/{asset}"

# platform -> (release asset, SHA-256 of the binary). The release notes' checksum for a macOS .tgz is of the
# cloudflared binary inside it, not of the archive, so the archive's own digest (GitHub's, shown on the release's
# asset list) is pinned separately in ARCHIVE_SHA256.
ASSETS = {
    "darwin-arm64": ("cloudflared-darwin-arm64.tgz",
                     "380451015c3676789adc4200f43757e950e7173b920b0094e272e8bea59d7927"),
    "darwin-x64": ("cloudflared-darwin-amd64.tgz",
                   "2b74a9a6b9e5161dbf79a77c2fcc6337cd7e9d2b23eee9d1b6635ecb17608589"),
    "linux-x64": ("cloudflared-linux-amd64",
                  "1d356dac769d0dbf62ebe588c4034824bbff98d5790c2f844cc99e0e2f57462d"),
    "linux-arm64": ("cloudflared-linux-arm64",
                    "0ae0e179475b2c77651a718d2b376e0151f4fca5954702a84f02843a4d5fcac0"),
    "windows-x64": ("cloudflared-windows-amd64.exe",
                    "d521b3bf68982165f0d06d76732f22d9a105ef057ff3ae4c0986f3a23190950f"),
}
ARCHIVE_SHA256 = {
    "cloudflared-darwin-arm64.tgz": "ea1e5105d35bbe16cee5b2f7b4742059ada341fbd35938ed827c24b85efa51c8",
    "cloudflared-darwin-amd64.tgz": "6c04a13835ca5ff29a0c6f58d17d1f3956aad1b7df1631f5b5efbc06269deded",
}


def _check(what: str, data: bytes, want: str) -> None:
    got = hashlib.sha256(data).hexdigest()
    if got != want:
        sys.exit(f"cloudflared {VERSION} {what}: SHA-256 is {got}, expected {want}. Not installing it.")


def this_platform() -> str:
    system = {"darwin": "darwin", "linux": "linux", "win32": "windows"}.get(sys.platform, sys.platform)
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64" if machine in ("x86_64", "amd64") else machine
    return f"{system}-{arch}"


def _context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def fetch(plat: str, dest: Path) -> Path:
    if plat not in ASSETS:
        sys.exit(f"No cloudflared build is pinned for {plat}. Pinned: {', '.join(ASSETS)}")
    asset, want = ASSETS[plat]
    url = URL.format(version=VERSION, asset=asset)
    with urllib.request.urlopen(url, timeout=120, context=_context()) as resp:
        data = resp.read()
    if asset.endswith(".tgz"):
        _check(asset, data, ARCHIVE_SHA256[asset])
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            member = next((m for m in tar.getmembers() if m.isfile() and Path(m.name).name == "cloudflared"), None)
            if member is None:
                sys.exit(f"{asset} has no cloudflared in it.")
            data = tar.extractfile(member).read()
    _check(asset if not asset.endswith(".tgz") else asset + " (the binary inside)", data, want)
    name = "cloudflared.exe" if plat.startswith("windows") else "cloudflared"
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / name
    out.write_bytes(data)
    out.chmod(out.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dest", required=True, type=Path, help="folder to put cloudflared in")
    ap.add_argument("--platform", default=this_platform(), choices=sorted(ASSETS))
    args = ap.parse_args()
    out = fetch(args.platform, args.dest)
    print(f"cloudflared {VERSION} ({args.platform}) -> {out}")


if __name__ == "__main__":
    main()
