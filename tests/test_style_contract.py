"""Shared styles (#463): buttons, fields and type come from app/static/components.css and the tokens in
app/static/tokens.css, and a page cannot drift back to its own sizes and colours.

This file is the rule's enforcement, run in CI on every pull request. It fails when:

* a file has more literal px font sizes, hex/rgb colours or px radii than tests/style_contract_baseline.txt allows
  (a ratchet: counts may only go down, and a file that went down must have its line lowered, so the gain is kept);
* a module admin template gets a <style> block;
* a module script builds CSS itself (style.textContent, insertRule, createElement('style'), or a cssText string);
* a font stack (a font-family naming a font, not var(--font) / var(--mono) / inherit) is written outside tokens.css;
* a file in the baseline no longer exists (delete its line).

tokens.css is where the values live, so it is not counted. Files that draw the operator's own looks (the viewer
pages, the overlay, rundown, signage, boards and cue canvases, tally, stage) are listed in
docs/ui-audit/standardization-spec.md: they keep their own sizes, and the ratchet still stops them gaining more.

Run: .venv/bin/python tests/test_style_contract.py        (--rebaseline rewrites the baseline after a clean-up)
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
BASELINE = Path(__file__).resolve().parent / "style_contract_baseline.txt"
TOKENS = APP / "static" / "tokens.css"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, done, eq, section  # noqa: E402

FONT_PX = re.compile(r"font(?:-size)?\s*:[^;{}\"'`]*?\b\d+(?:\.\d+)?px|fontSize\s*=\s*['\"`]\d+(?:\.\d+)?px")
HEX = re.compile(r"(?<![\w&/#-])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b(?!\s*[{,]|-)")
RGB = re.compile(r"\brgba?\(")
RADIUS_PX = re.compile(r"(?:border-radius|borderRadius)\s*[:=]\s*['\"`]?[^;{}\"'`]*?\b[1-9]\d*(?:\.\d+)?px")
FONT_STACK = re.compile(r"font-family\s*:\s*([^;}\"`]+)|\bfont\s*:\s*[^;}\"`]*?(?:\d+px|\bmedium|\bsmall)[^;}\"`]*?\s([\w\"' ,.-]*(?:monospace|sans-serif|serif|system-ui|Menlo|Arial|Helvetica)[^;}\"`]*)")
STYLE_BLOCK = re.compile(r"<style[\s>]", re.I)
STYLE_ATTR = re.compile(r"\sstyle=\"([^\"]*)\"")
INJECT = re.compile(r"style\.textContent|\binsertRule\(|createElement\(\s*['\"]style['\"]\s*\)|\.cssText\s*=")
SKIP_JS = ("/vendor/", "livekit-client.umd.js")
# The signage player draws each slide full screen with the slide's own placement, set per item from script; it is a
# display, not a panel page (docs/ui-audit/standardization-spec.md).
INJECT_ALLOWED = {"app/modules/signage/static/player.js"}


def files() -> list[Path]:
    out = []
    for p in sorted(APP.rglob("*")):
        if p.suffix not in (".css", ".html", ".js") or p == TOKENS:
            continue
        rel = p.relative_to(ROOT).as_posix()
        if any(s in rel for s in SKIP_JS) or p.name.endswith(".min.js") or p.name == "overlay-fonts.css":
            continue
        out.append(p)
    return out


def css_text(p: Path) -> str:
    """The CSS a file carries: all of a stylesheet; the <style> blocks and style="" attributes of a template; the
    string literals of a script (where it writes styles)."""
    src = p.read_text(encoding="utf-8", errors="replace")
    if p.suffix == ".css":
        return re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    if p.suffix == ".html":
        parts = re.findall(r"<style[^>]*>(.*?)</style>", src, flags=re.S | re.I)
        parts += STYLE_ATTR.findall(src)
        return "\n".join(parts)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"(?m)^\s*//.*$", "", src)
    return src


def count(p: Path) -> dict:
    t = css_text(p)
    colours = len(HEX.findall(t)) + len(RGB.findall(t))
    if p.suffix == ".js":
        # In a script, only colours in style context count (an id like '#log' is not a colour).
        colours = len(re.findall(r"(?:color|background|border|fill|stroke|shadow|outline)[\w-]*\s*[:=]\s*['\"`]?[^;'\"`]*?(?:#[0-9a-fA-F]{3,8}\b|rgba?\()", t, flags=re.I))
    return {"font": len(FONT_PX.findall(t)), "colour": colours, "radius": len(RADIUS_PX.findall(t))}


# Pages that cannot load tokens.css keep a stack of their own: those served on their own listener or offline (the crew
# join page and its offline copy, the remote phone page behind the tunnel, a parent-paging room screen), and the
# viewer pages.
STACK_ALLOWED = {"app/modules/crew/templates/join.html", "app/modules/crew/templates/offline.html",
                 "app/modules/intercom_remote/static/phone.html", "app/modules/parent_paging/templates/room.html",
                 # The viewer pages and the overlay: tokens.css sets color-scheme, which paints the overlay's
                 # transparent page dark in a browser source, so viewer.html does not load it.
                 "app/static/viewer.css"}
# The caption fonts the operator picks for the overlay (body.ov-font-*) are looks, not the interface's type.
LOOK_RULE = re.compile(r"\.ov-font-[\w-]+[^{]*\{[^}]*\}")


def stacks(p: Path) -> list[str]:
    bad = []
    if p.relative_to(ROOT).as_posix() in STACK_ALLOWED:
        return bad
    for m in FONT_STACK.finditer(LOOK_RULE.sub("", css_text(p))):
        v = (m.group(1) or m.group(2) or "").strip()
        if not v or re.fullmatch(r"(?:var\(--(?:font|mono)\)|inherit|initial|unset)\s*(?:!important)?", v):
            continue
        if not re.search(r"monospace|sans-serif|serif|system-ui|Menlo|Arial|Helvetica|Segoe", v):
            continue  # a font the operator picked (overlay, signage), not a stack
        bad.append(v[:60])
    return bad


def read_baseline() -> dict:
    out = {}
    if BASELINE.exists():
        for line in BASELINE.read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                rel, f, c, r = line.split()
                out[rel] = {"font": int(f), "colour": int(c), "radius": int(r)}
    return out


def measure() -> dict:
    out = {}
    for p in files():
        n = count(p)
        if any(n.values()):
            out[p.relative_to(ROOT).as_posix()] = n
    return out


def write_baseline(now: dict) -> None:
    lines = ["# Literal px font sizes, hex/rgb colours and px radii per file when tests/test_style_contract.py came in (#463).",
             "# The counts may only go down. Use the tokens in app/static/tokens.css and the classes in components.css,",
             "# then run tests/test_style_contract.py --rebaseline to lower a line.",
             "# file  font  colour  radius"]
    lines += [f"{rel} {n['font']} {n['colour']} {n['radius']}" for rel, n in sorted(now.items())]
    BASELINE.write_text("\n".join(lines) + "\n")


if __name__ == "__main__" and "--rebaseline" in sys.argv:
    write_baseline(measure())
    print(f"wrote {BASELINE.relative_to(ROOT)}")
    sys.exit(0)

if __name__ == "__main__" and "--report" in sys.argv:
    now = measure()
    tot = {"font": 0, "colour": 0, "radius": 0}
    for rel, n in sorted(now.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"{sum(n.values()):4}  {n['font']:3} {n['colour']:3} {n['radius']:3}  {rel}")
        for k in tot:
            tot[k] += n[k]
    print("total", tot)
    sys.exit(0)

with section("literal sizes and colours only go down"):
    base, now = read_baseline(), measure()
    for rel, n in sorted(now.items()):
        b = base.get(rel, {"font": 0, "colour": 0, "radius": 0})
        for k in ("font", "colour", "radius"):
            check(f"{rel}: {n[k]} literal {k} values (baseline {b[k]}); use the tokens in tokens.css", n[k] <= b[k])
            if n[k] < b[k]:
                check(f"{rel}: {k} went down to {n[k]} from {b[k]}; run tests/test_style_contract.py --rebaseline", False)
    for rel, b in sorted(base.items()):
        if rel not in now:
            gone = not (ROOT / rel).exists()
            check(f"{rel}: {'no longer exists' if gone else 'is clean now'}; run tests/test_style_contract.py --rebaseline", False)

with section("no <style> in a module admin template"):
    for p in sorted((APP / "modules").glob("*/templates/*.html")):
        rel = p.relative_to(ROOT).as_posix()
        src = p.read_text(encoding="utf-8")
        is_page = "<html" in src.lower()  # a full page of its own (a phone, a player), not an admin include
        check(f"{rel}: a <style> block; put the rules in the module's static/*.css on the shared classes",
              is_page or not STYLE_BLOCK.search(src))

with section("no CSS built in a module script"):
    for p in sorted((APP / "modules").glob("*/static/*.js")) + [APP / "static" / "oauth.js"]:
        rel = p.relative_to(ROOT).as_posix()
        if any(s in rel for s in SKIP_JS) or rel in INJECT_ALLOWED:
            continue
        hits = INJECT.findall(p.read_text(encoding="utf-8"))
        eq(f"{rel}: CSS injected from script; move it to a stylesheet", hits, [])

with section("font stacks are written only in tokens.css"):
    for p in files():
        rel = p.relative_to(ROOT).as_posix()
        eq(f"{rel}: font stack outside tokens.css; use var(--font) or var(--mono)", stacks(p), [])

done()
