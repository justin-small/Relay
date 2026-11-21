"""Nothing outside the audio bus opens a sound device (#599). Sound goes in and out through the bus (the Rust core opens the
devices); Relay's own sounds go to the bus's destinations. So no file under app/ may open a sounddevice stream or play or
record straight to a device. A use that is right is marked on its line with a reason, e.g. `# allow-device: why`.
Run: .venv/bin/python tests/test_bus_only.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import check, done, eq, section  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OPENS = re.compile(r"\b(?:sd|sounddevice)\.(?:InputStream|OutputStream|Stream|RawInputStream|RawOutputStream|RawStream|play|rec|playrec)\b"
                   r"|\brtmixer\b")
ALLOW = "# allow-device:"


def offences(text: str) -> list[int]:
    """Line numbers that open a sound device without saying why."""
    return [n for n, line in enumerate(text.splitlines(), 1)
            if OPENS.search(line) and ALLOW not in line and not line.lstrip().startswith("#")]


with section("the scanner finds what it should"):
    planted = "\n".join([
        "stream = sd.InputStream(device=1)",
        "with sounddevice.OutputStream(samplerate=48000) as s: pass",
        "sd.play(data)",
        "import rtmixer",
        "sd.InputStream(device=2)  # allow-device: a test seam",
        "# sd.OutputStream is only a comment here",
        "sd.query_devices()",
    ])
    eq("four uses are found; an allowed one, a comment and a listing are not", offences(planted), [1, 2, 3, 4])

with section("no file under app/ opens a sound device"):
    found = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        for n in offences(path.read_text(encoding="utf-8")):
            found.append(f"{path.relative_to(ROOT)}:{n}")
    check("the audio bus is the only way in or out of a sound device", not found,
          "opened outside the bus (play to a destination, or patch a lane; or mark '# allow-device: why'): " + ", ".join(found))

done()
