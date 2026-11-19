"""Shared concepts (#521, #522): one way to compare names, make slugs and handle days and clock times.

This file is the rule's enforcement, run in CI on every pull request. It reads every app/**/*.py except the two
shared files themselves (app/text.py and app/timewin.py) and counts, per file:

* name     tidying a name by hand: `.casefold()`, `unicodedata.normalize(` or a `[^a-z0-9]` slug pattern. Use
           app/text.py (`fold`, `same_name`, `plain`, `strip_accents`, `slug`);
* weekday  `.weekday()` or `.isoweekday()`, which count from Monday. Relay counts days from Sunday = 0: use
           `timewin.weekday(d)`;
* clock    a hand-written HH:MM pattern (`2[0-3]`). Use `timewin.is_clock`, `clean_clock`, `minutes` or `us_clock`.

Each count is checked against tests/shared_concepts_baseline.txt, which may only shrink: a file over its count
fails, and a file under its count fails until the line is lowered (run with --rebaseline, which refuses to raise
any count). Later steps of #521 add their rules here (free-text team and position columns, imports of another
module's internals).

A use that is right goes on a line with a reason: `x.casefold()  # allow-shared: offsets must match the original`.

Run: .venv/bin/python tests/test_shared_concepts.py        (--rebaseline lowers the baseline after a fix)
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
APP = ROOT / "app"
BASELINE = HERE / "shared_concepts_baseline.txt"
SHARED = {"app/text.py", "app/timewin.py"}
ALLOW = "# allow-shared:"

RULES: dict[str, re.Pattern] = {
    "name": re.compile(r"\.casefold\(\)|unicodedata\.normalize\(|\[\^a-z0-9\]"),
    "weekday": re.compile(r"\.(?:iso)?weekday\(\)"),
    "clock": re.compile(r"2\[0-3\]"),
}

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, done, eq, section  # noqa: E402


def sources() -> list[Path]:
    return [p for p in sorted(APP.rglob("*.py"))
            if "__pycache__" not in p.parts and p.relative_to(ROOT).as_posix() not in SHARED]


def code_part(line: str) -> str:
    """The line without a trailing comment (good enough here: the patterns never appear inside a "#" string)."""
    return line.split("#", 1)[0] if "#" in line else line


def hits() -> dict[tuple[str, str], list[int]]:
    """(rule, file) -> line numbers of every use not marked allow-shared."""
    out: dict[tuple[str, str], list[int]] = {}
    for p in sources():
        rel = p.relative_to(ROOT).as_posix()
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if ALLOW in line:
                continue
            code = code_part(line)
            for rule, pattern in RULES.items():
                if pattern.search(code):
                    out.setdefault((rule, rel), []).append(n)
    return out


def counts() -> dict[tuple[str, str], int]:
    return {k: len(v) for k, v in hits().items()}


def read_baseline() -> dict[tuple[str, str], int]:
    out = {}
    if BASELINE.exists():
        for line in BASELINE.read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                rule, n, rel = line.split()
                out[(rule, rel)] = int(n)
    return out


def write_baseline(rows: dict[tuple[str, str], int]) -> None:
    head = ("# Hand-written name tidying, Monday-first weekdays and HH:MM patterns outside app/text.py and\n"
            "# app/timewin.py when tests/test_shared_concepts.py came in (#522). This list may only shrink: fix one,\n"
            "# lower its count here (or run the test with --rebaseline).\n"
            "# rule  count  file\n")
    body = "".join(f"{rule} {n} {rel}\n" for (rule, rel), n in sorted(rows.items(), key=lambda kv: (kv[0][1], kv[0][0])))
    BASELINE.write_text(head + body)


def main() -> None:
    if "--rebaseline" in sys.argv:
        old, now = read_baseline(), counts()
        raised = [f"{rel}: {rule} {old.get((rule, rel), 0)} -> {n}" for (rule, rel), n in now.items()
                  if n > old.get((rule, rel), 0)]
        if raised:
            print("Not rewriting: these counts went up, and the baseline may only shrink:")
            print("\n".join(raised))
            sys.exit(1)
        write_baseline(now)
        print(f"baseline: {sum(old.values())} -> {sum(now.values())} uses")
        return

    with section("the rules find what they are meant to"):
        def found(line: str) -> list[str]:
            return [r for r, pat in RULES.items() if pat.search(code_part(line))]
        eq("casefold", found('want = name.casefold()'), ["name"])
        eq("accents", found('t = unicodedata.normalize("NFKD", s)'), ["name"])
        eq("slug pattern", found('re.sub(r"[^a-z0-9]+", "-", s)'), ["name"])
        eq("weekday", found("if d.weekday() == 6:"), ["weekday"])
        eq("isoweekday", found("d.isoweekday()"), ["weekday"])
        eq("clock pattern", found('re.compile(r"([01]\\d|2[0-3]):[0-5]\\d")'), ["clock"])
        eq("a comment is not code", found("x = 1  # the old .weekday() counted from Monday"), [])
        eq("helpers are fine", found("if timewin.weekday(d) in days and same_name(a, b):"), [])

    with section("the shared files exist and are left out"):
        check("app/text.py exists", (ROOT / "app/text.py").exists())
        check("app/timewin.py exists", (ROOT / "app/timewin.py").exists())
        check("shared files are not scanned", not any(p.relative_to(ROOT).as_posix() in SHARED for p in sources()))

    with section("no new copies (tests/shared_concepts_baseline.txt may only shrink)"):
        found_lines, base = hits(), read_baseline()
        over, under = [], []
        for (rule, rel), lines in sorted(found_lines.items()):
            allowed = base.get((rule, rel), 0)
            if len(lines) > allowed:
                over.append(f"{rel}: {len(lines)} {rule} (baseline {allowed}) at lines {lines}")
        for (rule, rel), allowed in sorted(base.items()):
            n = len(found_lines.get((rule, rel), []))
            if n < allowed:
                under.append(f"{rel}: {rule} {n} (baseline {allowed})")
        eq("files over their baseline (use app/text.py or app/timewin.py, or mark a right use '# allow-shared: why')",
           over, [])
        eq("files under their baseline (lower the count in tests/shared_concepts_baseline.txt, or --rebaseline)",
           under, [])
    done()


if __name__ == "__main__":
    main()
