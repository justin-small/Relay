"""The released firmware image belongs to no installation.

Every buyer runs their own Relay, on their own network, and flashes the image this repository
releases. No value inside that image may belong to any particular church or site: the Wi-Fi
network, the Relay address and the panel ID are settings in NVS, written on the panel's own page.
This reads the bytes that are about to be handed to somebody, and refuses an image that has one
in it.

**Why grep a binary at all, when the source is right there.** Because the source is what a
reviewer already reads and the image is what a customer receives, and the second is assembled by
a toolchain out of files nobody diffs. A grep over 3 MB costs a second, it runs on the same
artifact that goes into the release, and it is the only check here that cannot be satisfied by
editing the wrong file.

What it looks for, and nothing else:

* a **PEM block**: a `-----BEGIN` marker with content behind it;
* a **UUID**;
* a **host name or URL**: anything with a dotted name in it that is not on the short allowlist
  below;
* a **secret-shaped token**: a long mixed-case-and-digits run, which is what an API key looks like
  and what none of this firmware's own strings do.

Every allowlist entry carries its reason. A new legitimate string failing this check is the
intended outcome: somebody reads what it found, decides it names nobody, and adds it here with a
sentence.

    tools/image_check.py firmware/relay-display/build/relay-display.bin

`build.sh` runs it on both images it produces, so a laptop build, a CI run and a release all
apply it to the bytes they are about to hand somebody.

Adapted from the VXP firmware's `tools/image_check.py` (see README.md, "Copied from VXP").
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# What counts as a string
# ---------------------------------------------------------------------------

# Six printable characters. Shorter runs are overwhelmingly instruction bytes
# that happen to be printable, and none of the four shapes below can fit in
# five characters anyway.
PRINTABLE = re.compile(rb"[ -~]{6,}")

# A PEM marker, and what follows it.
#
# **mbedtls carries these literals itself** — `-----BEGIN CERTIFICATE-----` and
# six siblings are in the library's own read path, in every image that links it,
# with no certificate anywhere near them. So the marker alone is not the finding.
# The finding is a marker with *content* behind it: a stored PEM is the marker,
# a newline and base64, while mbedtls's is the marker and then the string
# terminator. That one byte is the whole difference and it is completely
# reliable, which is why this looks at bytes rather than at extracted strings.
#
# **Its write path carries them with the newline.** `pk_write` and `x509write` literals are
# `-----BEGIN PRIVATE KEY-----\n` and the terminator. So two bytes are read: a newline then a
# terminator is the writer's literal, and a newline then anything else is a stored PEM.
PEM_MARKER = re.compile(rb"-----BEGIN [A-Z0-9 ]{0,40}-----([\s\S]{0,2})")

UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)

# **No `%` in the authority, and that is not a detail.** The firmware's own
# console line is `printf("... http://%s/\n", ip)`, and a percent in a host name
# is a format string rather than an address. Allowing it made every build fail on
# the board's own log line, which is the sort of false positive that gets a check
# switched off.
URL = re.compile(r"\b[a-z]{2,6}://[A-Za-z0-9._~-]*[A-Za-z0-9]")

# A dotted name ending in something that is a TLD rather than a file extension.
# Deliberately not "any two labels with a dot between them": half of an ESP-IDF
# image is `esp_wifi.c` and `lwip/src/core/ipv4/ip4.c`.
HOSTNAME = re.compile(
    # `(?<!%)` keeps `mdns: %s.local` — the board's own console line — from
    # reading as the host `s.local`. A percent before a name is a format
    # specifier, and this firmware prints several.
    r"(?<!%)\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
    r"(?:com|net|org|io|dev|cloud|local|uk|de|fr|nl|eu|us|co|ai|sh|xyz|info|biz)\b"
)

# An API key and a signing secret are long, opaque and mixed. The three
# character classes together are what tells one from a symbol name, a path or a
# format string: `/IDF/components/lwip/src/core/ipv4/ip4` is thirty-nine
# characters of the same alphabet with no capital in it.
TOKEN = re.compile(r"[A-Za-z0-9_-]{32,}")


def _token_is_secret_shaped(token: str) -> bool:
    return (
        any(c.islower() for c in token)
        and any(c.isupper() for c in token)
        and any(c.isdigit() for c in token)
    )


# ---------------------------------------------------------------------------
# What a clean image legitimately contains
# ---------------------------------------------------------------------------

# Each entry is a string this check would otherwise flag, and the reason it
# names nobody. Keep the reasons: an allowlist without them becomes a list
# somebody appends to rather than reads.
ALLOWED_HOSTS = {
    # ESP-IDF's own component registry, named in the component manager's strings. It is where the
    # build came from, not where a board points.
    "components.espressif.com": "ESP-IDF's component registry, from the toolchain",
    "www.espressif.com": "Espressif's own URL, from the toolchain",
    # The setup network: ESP-IDF's default access-point address, the same on every board, and the
    # page a person opens while setting the board up. It names no installation: the access point
    # exists only while the board is being set up.
    "192.168.4.1": "the setup network's fixed address, ESP-IDF's default access point on every board",
}

ALLOWED_TOKENS = {
    # mbedtls's base64 alphabet, as one 64-character literal. It is the alphabet
    # every base64 encoder holds; it is not a key.
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789": (
        "the base64 alphabet, from mbedtls"
    ),
}


def _token_allowed(token: str) -> bool:
    if token in ALLOWED_TOKENS:
        return True
    # An Itanium-mangled C++ name — `N9__gnu_cxx24__concurrence_lock_errorE` and
    # its siblings reach the image as `type_info` names. Every one of them
    # carries a double underscore and no key ever would, which is a cheaper and
    # more honest test than listing the names a toolchain happens to emit.
    return "__" in token


# ---------------------------------------------------------------------------

# **A released image must not carry what the emulator suite is seeded with.** QEMU's user-mode
# network is a fixed private range, so these are literal rather than shaped. The seeds are written
# into an NVS partition by `tests/seeds.py`, never into the application. This is here for the day
# somebody moves a default into `config.h` to make a test easier and does not notice that `config.h`
# is compiled into what a customer flashes.
TEST_ADDRESS_STRINGS = (
    b"10.0.2.2",
    b"10.0.2.15",
    b"relay-qemu-psk",
    b"Relay-QEMU-Net",
)


@dataclass(frozen=True)
class Finding:
    image: str
    kind: str
    value: str
    why: str

    def __str__(self) -> str:
        return f"{self.image}: {self.kind}: {self.value!r} — {self.why}"


def _strings(blob: bytes) -> list[str]:
    return [m.group(0).decode("ascii") for m in PRINTABLE.finditer(blob)]


def scan(blob: bytes, image: str = "image") -> list[Finding]:
    """Every installation-shaped value in `blob`, in the order they were found."""
    findings: list[Finding] = []

    for m in PEM_MARKER.finditer(blob):
        trailer = m.group(1)
        # Nothing behind the marker at all, the string terminator, or a newline
        # and then the terminator: a library's own literal for reading or for
        # writing, and not a stored certificate.
        if trailer in (b"", b"\n") or trailer.startswith(b"\x00") or trailer == b"\n\x00":
            continue
        findings.append(
            Finding(
                image,
                "PEM",
                m.group(0).decode("ascii", "replace"),
                "a certificate in the image is one installation's certificate; "
                "the CA is pasted on the board's own page",
            )
        )

    for text in _strings(blob):
        for m in UUID.finditer(text):
            findings.append(
                Finding(
                    image,
                    "UUID",
                    m.group(0),
                    "the only UUID this firmware handles is a building, and a "
                    "building is one customer's",
                )
            )
        for m in URL.finditer(text):
            host = m.group(0).split("://", 1)[1]
            if host in ALLOWED_HOSTS:
                continue
            findings.append(
                Finding(image, "URL", m.group(0), "a receiver URL is set on the board's own page")
            )
        for m in HOSTNAME.finditer(text):
            if m.group(0) in ALLOWED_HOSTS:
                continue
            findings.append(
                Finding(
                    image,
                    "host name",
                    m.group(0),
                    "a host name in the image points the board at somebody; add it to "
                    "ALLOWED_HOSTS with a reason if it points at nobody",
                )
            )
        for m in TOKEN.finditer(text):
            token = m.group(0)
            if not _token_is_secret_shaped(token) or _token_allowed(token):
                continue
            findings.append(
                Finding(
                    image,
                    "token",
                    token,
                    "an API key and a signing secret look exactly like this; add it to "
                    "ALLOWED_TOKENS with a reason if it is neither",
                )
            )

    for literal in TEST_ADDRESS_STRINGS:
        if literal in blob:
            findings.append(
                Finding(
                    image,
                    "test address",
                    literal.decode("ascii"),
                    "a value the emulator suite is seeded with is in this image. It belongs "
                    "in the harness's NVS partition, never in the application.",
                )
            )

    # The same literal can sit in an image more than once, and reporting it
    # twice tells the reader nothing the first line did not.
    return list(dict.fromkeys(findings))


def check(paths: list[Path]) -> list[Finding]:
    return [f for p in paths for f in scan(p.read_bytes(), p.name)]


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        print("usage: image_check.py <image.bin> [...]", file=sys.stderr)
        return 2
    paths = [Path(a) for a in argv]
    missing = [p for p in paths if not p.exists()]
    if missing:
        for p in missing:
            print(f"{p}: no such image", file=sys.stderr)
        return 2

    findings = check(paths)
    for f in findings:
        print(f, file=sys.stderr)
    if findings:
        print(
            f"\n{len(findings)} installation-specific value(s) in the firmware image. "
            "The released image belongs to no installation.",
            file=sys.stderr,
        )
        return 1
    total = sum(p.stat().st_size for p in paths)
    print(f"firmware image: clean, {total} bytes over {len(paths)} image(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
