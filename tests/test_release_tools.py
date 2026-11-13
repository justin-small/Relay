"""tools/release_tools.py: the version comes from the tag, and latest.json lists only signed artifacts. Offline."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import release_tools as rt  # noqa: E402

failures = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        failures.append(name)


def refuses(fn, *a, **k):
    try:
        fn(*a, **k)
    except SystemExit:
        return True
    return False


check("tag v1.2.0 is 1.2.0", rt.version_from_tag("v1.2.0") == "1.2.0")
check("pre-release tag keeps its suffix", rt.version_from_tag("v1.2.0-rc.1") == "1.2.0-rc.1")
check("a non-version tag is refused", refuses(rt.version_from_tag, "vNext"))

with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    (d / "tauri.conf.json").write_text('{\n  "productName": "Relay",\n  "version": "0.1.0",\n  "identifier": "x"\n}\n')
    (d / "Cargo.toml").write_text('[package]\nname = "relay-desktop"\nversion = "0.1.0"\n\n[dependencies]\nserde = { version = "1" }\n')
    rt.set_version("2.3.4-beta.1", d)
    check("tauri.conf.json version set", json.loads((d / "tauri.conf.json").read_text())["version"] == "2.3.4-beta.1")
    cargo = (d / "Cargo.toml").read_text()
    check("Cargo.toml package version set", 'version = "2.3.4-beta.1"' in cargo)
    check("dependency versions untouched", 'serde = { version = "1" }' in cargo)

with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    files = {
        "Relay_1.2.0_aarch64.app.tar.gz": "sig-mac-arm",
        "Relay_1.2.0_x64.app.tar.gz": "sig-mac-intel",
        "Relay_1.2.0_x64-setup.exe": "sig-win",
        "Relay_1.2.0_amd64.AppImage": "sig-linux",
    }
    for name, sig in files.items():
        (d / name).write_bytes(b"x")
        (d / (name + ".sig")).write_text(sig + "\n")
    (d / "Relay_1.2.0_amd64.deb").write_bytes(b"x")  # not an update artifact: no .sig needed
    (d / "Relay_1.2.0_aarch64.dmg").write_bytes(b"x")
    out = rt.latest_json(d, "v1.2.0", "acme/Relay", "notes", "2026-01-01T00:00:00Z")
    p = out["platforms"]
    check("four platforms", sorted(p) == ["darwin-aarch64", "darwin-x86_64", "linux-x86_64", "windows-x86_64"])
    check("signature is the .sig text", p["darwin-aarch64"]["signature"] == "sig-mac-arm")
    check("url points at the tag's release asset",
          p["windows-x86_64"]["url"] == "https://github.com/acme/Relay/releases/download/v1.2.0/Relay_1.2.0_x64-setup.exe")
    check("version has no v", out["version"] == "1.2.0")
    (d / "Relay_1.2.0_x64-setup.exe.sig").unlink()
    check("an unsigned artifact fails loudly", refuses(rt.latest_json, d, "v1.2.0", "acme/Relay"))

with tempfile.TemporaryDirectory() as d:
    check("no artifacts fails loudly", refuses(rt.latest_json, Path(d), "v1.2.0", "acme/Relay"))

if failures:
    print(f"\n{len(failures)} failed")
    sys.exit(1)
print("\nall passed")
