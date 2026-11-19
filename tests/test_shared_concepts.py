"""Shared concepts (#521, #522, #523): one way to compare names, make slugs, handle days and clock times, and reach
another module.

This file is the rule's enforcement, run in CI on every pull request. It reads every app/**/*.py except the two
shared files themselves (app/text.py and app/timewin.py) and counts, per file:

* name     tidying a name by hand: `.casefold()`, `unicodedata.normalize(` or a `[^a-z0-9]` slug pattern. Use
           app/text.py (`fold`, `same_name`, `plain`, `strip_accents`, `slug`);
* weekday  `.weekday()` or `.isoweekday()`, which count from Monday. Relay counts days from Sunday = 0: use
           `timewin.weekday(d)`;
* clock    a hand-written HH:MM pattern (`2[0-3]`). Use `timewin.is_clock`, `clean_clock`, `minutes` or `us_clock`;
* import   a module importing another module's package (`from ..signage import store`, `from app.modules.x import`).
           A module offers what others need with `modules.provide("<area>.<what>", impl)` in `attach()` and others ask
           with `modules.using(...)` and cope with None (docs/modules.md, "Reaching another module").

Each count is checked against tests/shared_concepts_baseline.txt, which may only shrink: a file over its count
fails, and a file under its count fails until the line is lowered (run with --rebaseline, which refuses to raise
any count). Later steps of #521 add their rules here (free-text team and position columns).

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
MODULES = APP / "modules"
# `from ..<other> import` or `from ..<other>.x import` inside app/modules/<id>/ (two dots: a sibling module; three are core),
# and the absolute spelling.
SIBLING = re.compile(r"^\s*(?:from\s+\.\.(\w+)\b|from\s+app\.modules\.(\w+)\b|import\s+app\.modules\.(\w+)\b)")

# A module column that holds a person as free text (a Planning Center id or a name): the link lives in core's
# person_links (app/directory.py, #524) and a module stores a ref (person:pco:<id>) in a column named *_ref.
PERSON_COLUMN = re.compile(r"^\s*(?:pc_person|person_id)\s+text\b", re.I)

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


def module_sql() -> list[Path]:
    """Every module migration: where a module's tables are made."""
    return sorted(MODULES.glob("*/migrations/*.sql"))


def code_part(line: str) -> str:
    """The line without a trailing comment (good enough here: the patterns never appear inside a "#" string)."""
    return line.split("#", 1)[0] if "#" in line else line


def other_module(p: Path, code: str) -> bool:
    """Whether this line, in a file of one module's package, imports a different module's package."""
    rel = p.relative_to(MODULES).parts if MODULES in p.parents else ()
    if len(rel) < 2:                     # app/modules/__init__.py is the registry; only <id>/... files are modules
        return False
    m = SIBLING.match(code)
    name = next((g for g in m.groups() if g), "") if m else ""
    return bool(name) and name != rel[0] and (MODULES / name).is_dir()


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
            if other_module(p, code):
                out.setdefault(("import", rel), []).append(n)
    for p in module_sql():
        rel = p.relative_to(ROOT).as_posix()
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "allow-shared:" not in line and PERSON_COLUMN.match(line):
                out.setdefault(("person", rel), []).append(n)
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
            "# app/timewin.py when tests/test_shared_concepts.py came in (#522), and modules that import another\n"
            "# module's package (#523), and a module column holding a person as text (#524). This list may only shrink: fix one, lower its count here (or run the test\n"
            "# with --rebaseline).\n"
            "# intercom_remote -> intercom stays: the remote intercom is the same feature as the intercom (its rooms, its\n"
            "# devices, its presence, its watchdog), not an add-on that has to cope with the intercom being off.\n"
            "# intercom_remote -> remote_access stays: it reads the enrolment and the cloud client that module holds even\n"
            "# while the tunnel (remote access) is off, so asking by name would stop the remote intercom when it is.\n"
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
        check("a pc_person text column is found", bool(PERSON_COLUMN.match("    pc_person       text unique,")))
        check("so is person_id", bool(PERSON_COLUMN.match("  person_id text not null,")))
        check("a ref column is fine", not PERSON_COLUMN.match("    person_ref      text not null,"))
        check("a person id column of numbers is fine", not PERSON_COLUMN.match("    person_id integer,"))
        eq("helpers are fine", found("if timewin.weekday(d) in days and same_name(a, b):"), [])

        def sibling(rel: str, line: str) -> bool:
            return other_module(MODULES / rel, line)
        check("another module's package is found", sibling("crew/routes.py", "    from ..planning_center import x"))
        check("deeper in it too", sibling("crew/routes.py", "from ..signage.store import y"))
        check("the absolute spelling too", sibling("crew/routes.py", "from app.modules.unifi.service import z"))
        check("a module's own package is fine", not sibling("crew/routes.py", "from ..crew import people"))
        check("core is three dots", not sibling("crew/routes.py", "from ... import audit"))
        check("the registry is fine", not sibling("crew/routes.py", "from .. import using"))
        check("the registry file is not a module", not sibling("__init__.py", "from ..netroute import Resolved"))

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
