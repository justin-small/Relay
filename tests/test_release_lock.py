"""The desktop release installs requirements-release.txt (hash-pinned, release.yml), not requirements.txt. This
checks the two agree: every pin in requirements.txt is in the release set at the same version and with the same
platform marker, and every package in the release set carries a hash. Regenerate with the command at the top of
requirements-release.in when it fails.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIN = re.compile(r"^([A-Za-z0-9_.\-]+)==([^\s;\\]+)\s*(?:;\s*([^\\]+?))?\s*\\?$")


def norm_name(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def pins(path):
    found = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = PIN.match(line.strip())
        if m:
            name, version, marker = m.groups()
            found[(norm_name(name), version)] = marker
    return found


app = pins(ROOT / "requirements.txt")
release = pins(ROOT / "requirements-release.txt")
assert app, "no pins read from requirements.txt"

for (name, version), marker in app.items():
    assert (name, version) in release, f"{name}=={version} is in requirements.txt but not in requirements-release.txt"
    # uv rewrites markers (order, quotes); compare the clauses, not the text.
    want, got = marker or "", release[(name, version)] or ""
    for clause in re.findall(r"(sys_platform|platform_machine)\s*(==|!=)\s*['\"]([^'\"]+)['\"]", want):
        assert re.search(rf"{clause[0]}\s*{clause[1]}\s*'{re.escape(clause[2])}'", got), \
            f"{name}: marker {want!r} lost in the release set ({got!r})"

assert any(n == "pyinstaller" for n, _ in release), "pyinstaller is missing from the release set"

# Every requirement block carries at least one hash, or pip --require-hashes refuses the whole file.
blocks = re.split(r"\n(?=[A-Za-z0-9])", (ROOT / "requirements-release.txt").read_text(encoding="utf-8"))
for block in blocks:
    head = block.strip().splitlines()[0] if block.strip() else ""
    if PIN.match(head) or "==" in head.split(";")[0]:
        assert "--hash=sha256:" in block, f"no hash for {head}"

print(f"release lock: {len(app)} app pins present, {len(release)} packages hashed")
