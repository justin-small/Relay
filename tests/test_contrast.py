"""Every preset, light and dark, meets WCAG AA (#434): body and muted text on the page, card and sidebar,
the text on --primary and on the hover/active surface, and the status colours on a card. Reads the tokens in
app/static/tokens.css, so a preset edited later is checked without a browser.

Offline. Run: .venv/bin/python tests/test_contrast.py
"""
import math
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, done, section  # noqa: E402

CSS = (Path(__file__).resolve().parent.parent / "app" / "static" / "tokens.css").read_text()


def lin(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def oklch(light, chroma, hue):
    a, b = chroma * math.cos(math.radians(hue)), chroma * math.sin(math.radians(hue))
    l_, m_, s_ = light + 0.3963377774 * a + 0.2158037573 * b, light - 0.1055613458 * a - 0.0638541728 * b, light - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    rgb = (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
           -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)
    return [max(0.0, min(1.0, x)) for x in rgb]


def colour(text):
    """Linear RGB of #hex or oklch(); a color-mix(in srgb, X n%, transparent) is (rgb, alpha)."""
    t = text.strip()
    m = re.match(r"color-mix\(in srgb,\s*(#[0-9a-fA-F]{3,6}) (\d+)%,\s*transparent\)", t)
    if m:
        return colour(m.group(1)), int(m.group(2)) / 100
    if t.startswith("#"):
        h = t[1:]
        h = "".join(c * 2 for c in h) if len(h) == 3 else h
        return [lin(int(h[i:i + 2], 16) / 255) for i in (0, 2, 4)]
    m = re.match(r"oklch\(([\d.]+)\s*([\d.]*)\s*([\d.]*)\)", t)
    if m:
        return oklch(float(m.group(1)), float(m.group(2) or 0), float(m.group(3) or 0))
    raise ValueError(text)


def flat(c, under):
    return [c[0][i] * c[1] + under[i] * (1 - c[1]) for i in range(3)] if isinstance(c, tuple) else c


def lum(c):
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def ratio(a, b):
    hi, lo = max(lum(a), lum(b)), min(lum(a), lum(b))
    return (hi + 0.05) / (lo + 0.05)


def block(selector):
    m = re.search(re.escape(selector) + r"\{([^}]*)\}", CSS)
    return dict(p.split(":", 1) for p in re.split(r";(?![^()]*\))", m.group(1)) if ":" in p)


with section("every preset meets AA"):
    status = {"dark": block("[data-theme=dark]"), "light": block("[data-theme=light]")}
    found = 0
    for p in ("relay", "softpop", "tangerine", "brutalist"):
        for t in ("dark", "light"):
            v = block(f"[data-preset={p}][data-theme={t}]")
            found += 1
            bg, card, sb = colour(v["--bg"]), colour(v["--card"]), colour(v["--sb"])
            fg, muted, primary, pfg = colour(v["--fg"]), colour(v["--muted"]), colour(v["--primary"]), colour(v["--primary-fg"])
            hover = flat(colour(v["--hover"]), sb)
            hfg = colour(v["--hover-fg"]) if "--hover-fg" in v else fg
            for name, a, b, need in (("text on the page", fg, bg, 4.5), ("text on a card", fg, card, 4.5), ("text on the sidebar", fg, sb, 4.5),
                                     ("muted on the page", muted, bg, 4.5), ("muted on a card", muted, card, 4.5),
                                     ("muted on the sidebar", muted, sb, 4.5), ("text on primary", pfg, primary, 4.5),
                                     ("text on hover", hfg, hover, 4.5), ("primary against a card (UI)", primary, card, 3.0)):
                r = ratio(a, b)
                check(f"{p} {t}: {name} is {r:.2f}:1 (needs {need})", r >= need)
            for k in ("ok", "warn", "err"):
                r = ratio(colour(status[t]["--" + k]), card)
                check(f"{p} {t}: --{k} on a card is {r:.2f}:1", r >= 4.5)
    check("all eight presets were read", found == 8, found)

done()
