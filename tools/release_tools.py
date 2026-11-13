"""Release helpers for .github/workflows/release.yml (#184). Standard library only.

  release_tools.py version v1.2.0
      Make 1.2.0 the version of the desktop app: desktop/src-tauri/tauri.conf.json and Cargo.toml. The shell
      hands the Tauri app version to the backend as RELAY_VERSION, and the backend announces it in the mDNS
      `v` record, so the git tag is the one place a version is written.

  release_tools.py latest-json DIR --tag v1.2.0 --repo owner/name [--notes FILE]
      Write DIR/latest.json, the file the Tauri updater reads: for each platform, the update artifact's URL on
      the release and the contents of its .sig (the updater's own signature, made with the key in the
      repository secret). An artifact with no .sig is an error, not a gap: a release that quietly could not
      update some platform would be worse than one that does not publish.
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TAURI = ROOT / "desktop" / "src-tauri"
SEMVER = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")


def version_from_tag(tag: str) -> str:
    v = tag[1:] if tag.startswith("v") else tag
    if not SEMVER.match(v):
        raise SystemExit(f"'{tag}' is not a version tag like v1.2.0 or v1.2.0-rc.1")
    return v


def set_version(version: str, tauri_dir: Path = TAURI) -> None:
    conf = tauri_dir / "tauri.conf.json"
    text = conf.read_text()
    text, n = re.subn(r'("version"\s*:\s*")[^"]*(")', lambda m: m.group(1) + version + m.group(2), text, count=1)
    if n != 1:
        raise SystemExit(f"no version in {conf}")
    conf.write_text(text)
    cargo = tauri_dir / "Cargo.toml"
    text = cargo.read_text()
    text, n = re.subn(r'(?m)^(version\s*=\s*")[^"]*(")', lambda m: m.group(1) + version + m.group(2), text, count=1)
    if n != 1:
        raise SystemExit(f"no package version in {cargo}")
    cargo.write_text(text)


def platform_of(name: str) -> str | None:
    """Tauri's updater platform key for an update artifact, or None if the file is not one."""
    low = name.lower()
    if low.endswith(".app.tar.gz"):
        os_ = "darwin"
    elif low.endswith("-setup.exe"):
        os_ = "windows"
    elif low.endswith(".appimage") or low.endswith(".appimage.tar.gz"):
        os_ = "linux"
    else:
        return None
    if re.search(r"aarch64|arm64", low):
        arch = "aarch64"
    elif re.search(r"x86_64|x64|amd64", low):
        arch = "x86_64"
    else:
        raise SystemExit(f"cannot tell the architecture of {name}: name it with x64 or aarch64")
    return f"{os_}-{arch}"


def latest_json(directory: Path, tag: str, repo: str, notes: str = "", pub_date: str | None = None) -> dict:
    version = version_from_tag(tag)
    platforms: dict[str, dict] = {}
    for sig in sorted(directory.glob("*.sig")):
        artifact = sig.with_suffix("")
        key = platform_of(artifact.name)
        if key is None:
            continue
        if not artifact.exists():
            raise SystemExit(f"{sig.name} has no artifact beside it")
        if key in platforms:
            raise SystemExit(f"two update artifacts for {key}: {platforms[key]['url']} and {artifact.name}")
        platforms[key] = {
            "signature": sig.read_text().strip(),
            "url": f"https://github.com/{repo}/releases/download/{tag}/{artifact.name}",
        }
    for artifact in sorted(directory.iterdir()):
        key = platform_of(artifact.name)
        if key and key not in platforms:
            raise SystemExit(f"{artifact.name} has no .sig: was the build signed?")
    if not platforms:
        raise SystemExit(f"no signed update artifacts in {directory}")
    return {
        "version": version,
        "notes": notes,
        "pub_date": pub_date or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platforms": platforms,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("version")
    v.add_argument("tag")
    j = sub.add_parser("latest-json")
    j.add_argument("directory", type=Path)
    j.add_argument("--tag", required=True)
    j.add_argument("--repo", required=True)
    j.add_argument("--notes", type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "version":
        set_version(version_from_tag(a.tag))
        print(version_from_tag(a.tag))
    else:
        notes = a.notes.read_text() if a.notes else ""
        out = latest_json(a.directory, a.tag, a.repo, notes)
        (a.directory / "latest.json").write_text(json.dumps(out, indent=2) + "\n")
        print(f"latest.json: {version_from_tag(a.tag)} for {', '.join(sorted(out['platforms']))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
