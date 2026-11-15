"""A per-user preference is never shown as HTML (#289).

Any signed-in user or API token can store any plain JSON as a preference (#173, #288), so a page that put one
into innerHTML, or a template that marked one |safe, would be a stored XSS in the person's admin session.
This reads every page script and template, core's and the modules', and refuses a line that names a
preference and an HTML sink together. It reads line by line, so a value passed through a variable first is
not seen: docs/modules.md says how to show one safely, and review covers the rest.

Offline, no server. Run: .venv/bin/python tests/test_prefs_render.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# A preference by name: prefs / pref as a word, .prefs, prefs., scope_prefs, /me/prefs.
PREF = re.compile(r"\bprefs?\b|scope_prefs|\.prefs\b")
# Places that turn a string into markup rather than text.
SINKS = re.compile(
    r"\.innerHTML\b|\.outerHTML\b|insertAdjacentHTML|document\.write(ln)?\s*\(|\|\s*safe\b|\bMarkup\s*\(")

fails = []


def check(label, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + label + ((" — " + str(extra)) if extra and not cond else ""))
    if not cond:
        fails.append(label)


def offending(text):
    """The (line number, line) pairs that send a preference to an HTML sink."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        code = line.split("//", 1)[0] if not line.lstrip().startswith(("{#", "<!--")) else ""
        if PREF.search(code) and SINKS.search(code):
            out.append((n, line.strip()))
    return out


def page_files():
    """Every script and template a browser is sent: core's and each module's."""
    globs = ("app/static/**/*.js", "app/templates/**/*.html", "app/modules/**/*.js", "app/modules/**/*.html")
    seen = set()
    for g in globs:
        for p in ROOT.glob(g):
            if p.is_file() and p not in seen:
                seen.add(p)
                yield p


# The detector itself.
check("innerHTML with a preference is caught", offending("el.innerHTML = me.prefs.core.title;"))
check("insertAdjacentHTML with a preference is caught",
      offending("box.insertAdjacentHTML('beforeend', pref.value)"))
check("|safe on a preference is caught", offending("<td>{{ prefs.core.layout|safe }}</td>"))
check("Markup on a preference is caught", offending("Markup(prefs.get(uid, 'note', ''))"))
check("textContent with a preference is fine", not offending("el.textContent = me.prefs.core.title;"))
check("autoescaped preference is fine", not offending("<td>{{ prefs.core.layout }}</td>"))
check("a prefix is not a preference", not offending("el.innerHTML = route_prefixes.join(', ');"))
check("a comment is not code", not offending("el.innerHTML = '';  // set from prefs later, as text"))

# The pages as they are.
files = list(page_files())
check("found the page scripts and templates", len(files) > 5, len(files))
for p in files:
    bad = offending(p.read_text(encoding="utf-8", errors="replace"))
    check(f"{p.relative_to(ROOT)} shows no preference as HTML", not bad,
          "; ".join(f"line {n}: {line}" for n, line in bad))

if fails:
    print(f"\n{len(fails)} FAILED")
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("\nall preference rendering checks passed")
