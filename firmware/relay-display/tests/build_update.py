"""The update images the emulator suite offers the panel.

An update is the same firmware at a higher version, built the way the image the suite boots is
(`build.sh --qemu`) and signed with that build's throwaway test key. The running image holds that key's public
half (`CONFIG_SECURE_SIGNED_ON_UPDATE_NO_SECURE_BOOT`), so the update passes the same check a release does, and
nothing about the check is weakened to let a test pass.

Two files come out:

    build-qemu-update/relay-display-app.bin             0.2.0, signed with the test key: the one that installs
    build-qemu-update/relay-display-app-wrongkey.bin    the same image, signed with a key the panel does not hold

The version is a line of source (`RELAY_FW_VERSION` in `main/config.h`), so the image is built from a copy of the
tree with that one line changed. The repository's own files are never edited. The second image is the first with
its signature block taken off and a fresh one made with another throwaway key, so it is whole, correctly
checksummed and validly signed, and refused only because the key is not the panel's.

    python3 tests/build_update.py            # in the pinned ESP-IDF container
    python3 tests/build_update.py --native   # with ESP-IDF sourced on this machine

Run `firmware/relay-display/build.sh --qemu` first: the test key is taken from its build directory, so both the
image the suite boots and this one are signed by the same key. Standard library only.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
REPO = PROJECT.parents[1]
OUT = PROJECT / "build-qemu-update"
QEMU_BUILD = PROJECT / "build-qemu"
UPDATE_VERSION = "0.2.0"
IMAGE_NAME = "relay-display-app.bin"
WRONG_KEY_NAME = "relay-display-app-wrongkey.bin"
TEST_KEY = "test_signing_key.pem"
SECTOR = 4096
IGNORE = shutil.ignore_patterns(
    "build", "build-dev", "build-qemu", "build-qemu-update", "sdkconfig", "sdkconfig.old", "__pycache__", ".pytest_cache"
)


def pinned_container() -> str:
    """The digest-pinned ESP-IDF image `.github/workflows/firmware.yml` builds in."""
    text = (REPO / ".github" / "workflows" / "firmware.yml").read_text()
    match = re.search(r"container: (espressif/idf:v[0-9.]+@sha256:[0-9a-f]{64})", text)
    if match is None:
        raise SystemExit("could not read a digest-pinned espressif/idf out of .github/workflows/firmware.yml")
    return match.group(1)


def stage(tree: Path) -> None:
    """Copy what `build.sh --qemu` reads and bump the version in the copy."""
    # `managed_components` holds the fetched LVGL and the emulator's LCD component, so the container build does not
    # need the component registry.
    shutil.copytree(PROJECT, tree / "relay-display", ignore=IGNORE)
    config = tree / "relay-display" / "main" / "config.h"
    text = config.read_text()
    bumped, count = re.subn(r'(#define RELAY_FW_VERSION ")[^"]*(")', rf"\g<1>{UPDATE_VERSION}\g<2>", text)
    if count != 1:
        raise SystemExit("main/config.h: RELAY_FW_VERSION is not where this script looks for it")
    config.write_text(bumped)


def in_container(tree: Path, image: str, script: str) -> None:
    """Run `script` in the pinned container with only the copy mounted, so it cannot reach the repository."""
    name = f"relay-fw-update-{os.getpid()}"
    command = ["docker", "run", "--rm", "--name", name, "-v", f"{tree}:/w", "-w", "/w", image, "bash", "-c",
               f". $IDF_PATH/export.sh >/dev/null 2>&1 && {script}"]
    subprocess.run(command, check=True)  # noqa: S603


def build(tree: Path, native: bool, image: str) -> Path:
    """`build.sh --qemu` on the copy, signed with the suite's test key. Returns the application image."""
    out = tree / "out"
    out.mkdir()
    shutil.copy2(QEMU_BUILD / TEST_KEY, out / TEST_KEY)
    if native:
        subprocess.run(["bash", "-c", f"relay-display/build.sh --qemu {out}"], cwd=tree, check=True)  # noqa: S603
    else:
        in_container(tree, image, "relay-display/build.sh --qemu /w/out")
    built = out / IMAGE_NAME
    if not built.exists():
        raise SystemExit(f"the build left no {IMAGE_NAME}")
    return built


def sign_with_another_key(tree: Path, native: bool, image: str, good: Path) -> Path:
    """The good image with its signature block replaced by one from a key the panel does not hold."""
    data = good.read_bytes()
    if data[-SECTOR:][:4] != b"\xe7\x02\x00\x00":
        raise SystemExit("the built image does not end in a signature block")
    work = tree / "resign"
    work.mkdir()
    (work / "unsigned.bin").write_bytes(data[:-SECTOR])
    script = ("espsecure.py generate_signing_key --version 2 --scheme rsa3072 resign/other.pem && "
              "espsecure.py sign_data --version 2 --keyfile resign/other.pem --output resign/wrong.bin resign/unsigned.bin")
    if native:
        subprocess.run(["bash", "-c", script], cwd=tree, check=True)  # noqa: S603
    else:
        in_container(tree, image, script)
    return work / "wrong.bin"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--native", action="store_true", help="use the ESP-IDF on this machine")
    args = parser.parse_args(argv)
    if not (QEMU_BUILD / TEST_KEY).exists():
        print("build-qemu/ has no test key. Run firmware/relay-display/build.sh --qemu first.", file=sys.stderr)
        return 1
    image = pinned_container()
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="relay-fw-update-", dir=OUT) as scratch:
        tree = Path(scratch) / "tree"
        tree.mkdir()
        stage(tree)
        good = build(tree, args.native, image)
        wrong = sign_with_another_key(tree, args.native, image, good)
        shutil.copy2(good, OUT / IMAGE_NAME)
        shutil.copy2(wrong, OUT / WRONG_KEY_NAME)
    print(f"{OUT / IMAGE_NAME}   version {UPDATE_VERSION}, signed with the test key")
    print(f"{OUT / WRONG_KEY_NAME}   the same image, signed with another key")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
