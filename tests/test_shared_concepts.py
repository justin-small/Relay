"""Shared concepts (#521, #522, #523): one way to compare names, make slugs, handle days and clock times, and reach
another module.

This file is the rule's enforcement, run in CI on every pull request. It reads every app/**/*.py except the two
shared files themselves (app/text.py and app/timewin.py) and counts, per file:

* name     tidying a name by hand: `.casefold()`, `unicodedata.normalize(` or a `[^a-z0-9]` slug pattern, lower-casing
           and squeezing it (`" ".join(x.lower().split())`) or comparing two names lower-cased (`a.lower() == b.lower()`).
           Use app/text.py (`fold`, `same_name`, `plain`, `strip_accents`, `slug`);
* weekday  `.weekday()` or `.isoweekday()`, which count from Monday. Relay counts days from Sunday = 0: use
           `timewin.weekday(d)`;
* clock    a hand-written HH:MM pattern (`2[0-3]`). Use `timewin.is_clock`, `clean_clock`, `minutes` or `us_clock`;
* screen   a module's own table of screens (`create table <x>_screens`): a display is one core screen
           (app/screens.py); a module attaches to it by key (`screens.adopt("<module>:<id>")`);
* device   a module table of phones or devices (`create table <x>_devices`, `_phones`, `_push_subs`) that is not a part of
           a core device: it needs a `device_id` that references `devices (id)` (app/devices.py, #528), so forgetting a phone
           ends it with the rest;
* time     a time column that is not ISO UTC text (#529): a column named `*_ts` or `*_epoch`, or a `*_at` column typed
           real or integer. Core and every module store time as `<name>_at text` written by `db.now()` or `db.iso(ts)`
           (`2027-01-15T08:00:00Z`), so a join across modules needs no conversion; `db.epoch(text)` gives seconds when
           arithmetic is needed;
* gear     a module table of equipment, racks or places (`create table <x>_gear`, `_equipment`, `_racks`, `_places`), or one named
           `gear`, `racks` or `places`: a piece of equipment is one core gear record (app/gear.py, #547), a rack sits inside a
           core place, and a module attaches to the gear by key (`gear.adopt(["<module>:<id>", ...])`) and keeps only what it
           adds (its address, its connection settings). Core's own tables are in app/migrations;
* port     a module table of ports or connectors (`create table <x>_ports`, `_connectors`), or a module page script that
           lists a device's connectors itself (`port('in1', 'sdi', ...)`): every physical connector is one core port on its
           gear (app/ports.py, #540). A module reports what its gear has with `ports.sync(gear_id, [...])` and keeps
           nothing of its own; a page draws the connectors the server sends;
* import   a module importing another module's package (`from ..signage import store`, `from app.modules.x import`).
           A module offers what others need with `modules.provide("<area>.<what>", impl)` in `attach()` and others ask
           with `modules.using(...)` and cope with None (docs/modules.md, "Reaching another module").

Each count is checked against tests/shared_concepts_baseline.txt, which may only shrink: a file over its count
fails, and a file under its count fails until the line is lowered (run with --rebaseline, which refuses to raise
any count). Later steps of #521 add their rules here.

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

# A module column that holds a team or position as free text (#525): teams and positions are stored as a core directory
# ref (team:pco:<id>, position:pco:<team id>/<position id>, app/directory.py) in a column named *_ref, and the typed
# name stays beside it only as the saved name. So a `team` or `position` text column is fine only when the same table
# also has `team_ref` / `position_ref`, made in its create table or by a later `alter table ... add column`.
PLACE_COLUMN = re.compile(r"^\s*(team|position)\s+text\b", re.I)
# A module table that is its own list of screens (#527): a display is one core screen (app/screens.py, migration
# 0008_screens_places.sql) whichever features show on it, so a module attaches to it by the key "<module>:<id>" and does not
# keep a registry of displays. `signage_screens` is baselined: it keeps what signage adds to a screen (its address,
# sound and playlist), and its name and place are the core screen's.
SCREEN_TABLE = re.compile(r"^\s*create\s+table\s+\w*screens?\s*\(", re.I)
# A module table that holds something of a person's phone (#528): a credential, a MAC address, a push subscription.
# It is a part of a core device (app/devices.py, migration 0009_devices.sql): it has a `device_id` that references
# `devices (id)`, made in its create table or by a later `alter table ... add column device_id`, so the phone's other
# parts and its revoke find it. `intercom_remote_devices` is baselined: it flags an intercom credential as remote and is
# keyed by that credential's row, which is the part.
DEVICE_TABLE = re.compile(r"^\s*create\s+table\s+(\w*(?:devices?|phones?|push_subs?))\s*\(", re.I)
DEVICE_REF = re.compile(r"\bdevice_id\b[^,;]*\breferences\s+devices\s*\(", re.I)
# A module table that is its own list of equipment, racks or places (#547): equipment is one core gear record whichever
# feature lists it, so a module attaches to it by the key "<module>:<id>" and does not keep a registry of boxes.
GEAR_TABLE = re.compile(r"^\s*create\s+table\s+(?:if\s+not\s+exists\s+)?\w*(?:gear|equipment|racks?|places?)\s*\(", re.I)
# A module table or page script that is its own list of a device's connectors (#540): a connector is one core port
# (app/ports.py, migration 0012_ports.sql) on the device's gear, whichever module reports it.
PORT_TABLE = re.compile(r"^\s*create\s+table\s+(?:if\s+not\s+exists\s+)?\w*(?:ports?|connectors?)\s*\(", re.I)
CONNECTOR_JS = re.compile(r"""\bport\(\s*['"][\w-]+['"]\s*,\s*['"](?:eth|sfp|sdi|hdmi|xlr|trs|mini|usb|iec|barrel|dc)['"]""")
# A module table or page script that is its own list of cables (#541): a cable is one core cable (app/cables.py, migration
# 0013_cables.sql) between two core ports, whoever records it; a module records or reports one, it keeps no list.
CABLE_TABLE = re.compile(r"^\s*create\s+table\s+(?:if\s+not\s+exists\s+)?\w*(?:cables?|wires?|wiring)\s*\(", re.I)
CABLE_LIST = re.compile(r"""['"](?:cables|wires)['"]\s*:\s*\[\s*\]""")
# A module table or default that is its own list of audio, video or talk channels (#542): a channel is one core channel
# (app/channels.py, migration 0014_channels.sql) whichever module reports it, so a module reports its channels with
# channels.sync and keeps no list. `intercom_channels` is baselined: it keeps the talk settings (mode, talkers, latch) of a
# channel whose name core holds as a talk channel; the rundown's board channels keep their assignments to positions and
# point at a core channel by `channel_ref`.
CHANNEL_TABLE = re.compile(r"^\s*create\s+table\s+(?:if\s+not\s+exists\s+)?\w*channels?\s*\(", re.I)
CHANNEL_LIST = re.compile(r"""['"]channels['"]\s*:\s*\[\s*\]""")
EPOCH_COLUMN = re.compile(r"^\s*(?:alter\s+table\s+\w+\s+add\s+column\s+)?(?:\w+_(?:ts|epoch)\s+\w+|\w+_at\s+(?:real|integer|int|numeric|float)\b)", re.I)
CREATE_TABLE = re.compile(r"create\s+table\s+(\w+)\s*\((.*?)\n\)\s*;", re.I | re.S)

RULES: dict[str, re.Pattern] = {
    "name": re.compile(r"\.casefold\(\)|unicodedata\.normalize\(|\[\^a-z0-9\]"
                       r"|join\([^()]*\.lower\(\)\.split\(\)\)|\.lower\(\)\s*==\s*[\w.\[\]\"']+\.lower\(\)"),
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
            if MODULES in p.parents and CABLE_LIST.search(code):
                out.setdefault(("cable", rel), []).append(n)
            if MODULES in p.parents and CHANNEL_LIST.search(code) and "allow-shared:" not in line:
                out.setdefault(("channel", rel), []).append(n)
    for p in sorted((APP / "migrations").glob("*.sql")) + module_sql():
        rel = p.relative_to(ROOT).as_posix()
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "allow-shared:" not in line and not line.lstrip().startswith("--") and EPOCH_COLUMN.match(line):
                out.setdefault(("time", rel), []).append(n)
    for p in module_sql():
        rel = p.relative_to(ROOT).as_posix()
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "allow-shared:" not in line and PERSON_COLUMN.match(line):
                out.setdefault(("person", rel), []).append(n)
        for n in place_lines(p):
            out.setdefault(("place", rel), []).append(n)
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "allow-shared:" not in line and SCREEN_TABLE.match(line):
                out.setdefault(("screen", rel), []).append(n)
        for n in device_lines(p):
            out.setdefault(("device", rel), []).append(n)
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "allow-shared:" not in line and GEAR_TABLE.match(line):
                out.setdefault(("gear", rel), []).append(n)
            if "allow-shared:" not in line and PORT_TABLE.match(line):
                out.setdefault(("port", rel), []).append(n)
            if "allow-shared:" not in line and CABLE_TABLE.match(line):
                out.setdefault(("cable", rel), []).append(n)
            if "allow-shared:" not in line and CHANNEL_TABLE.match(line):
                out.setdefault(("channel", rel), []).append(n)
    for p in sorted(MODULES.glob("*/static/*.js")):
        rel = p.relative_to(ROOT).as_posix()
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "allow-shared:" not in line and CONNECTOR_JS.search(line):
                out.setdefault(("port", rel), []).append(n)
    return out


def has_ref(module_sql_text: str, table: str, column: str, body: str) -> bool:
    """Whether `table` gets a `<column>_ref` column: in its own create table, by a later alter table, or in the
    `<table>_new` it was rebuilt into."""
    ref = rf"\b{column}_ref\b"
    return bool(re.search(ref, body, re.I)
                or re.search(rf"alter\s+table\s+{table}\s+add\s+column\s+{column}_ref\b", module_sql_text, re.I)
                or any(re.search(ref, b, re.I) for t, b in CREATE_TABLE.findall(module_sql_text) if t == f"{table}_new"))


def place_lines(p: Path) -> list[int]:
    """Line numbers in a module migration of a `team` or `position` text column that has no ref beside it."""
    text = p.read_text(encoding="utf-8")
    everything = "\n".join(q.read_text(encoding="utf-8") for q in sorted(p.parent.glob("*.sql")))
    bad: list[int] = []
    for m in CREATE_TABLE.finditer(text):
        table, body = m.group(1), m.group(2)
        for n, line in enumerate(body.splitlines()):
            col = PLACE_COLUMN.match(line)
            if col and "allow-shared:" not in line and not has_ref(everything, table, col.group(1).lower(), body):
                bad.append(text[:m.start(2)].count("\n") + 1 + n)
    return bad


def device_lines(p: Path) -> list[int]:
    """Line numbers in a module migration of a devices/phones table that is not a part of a core device."""
    text = p.read_text(encoding="utf-8")
    everything = "\n".join(q.read_text(encoding="utf-8") for q in sorted(p.parent.glob("*.sql")))
    bad: list[int] = []
    for n, line in enumerate(text.splitlines(), 1):
        m = DEVICE_TABLE.match(line)
        if not m or "allow-shared:" in line:
            continue
        table = m.group(1)
        body = next((b for t, b in CREATE_TABLE.findall(text) if t == table), "")
        later = re.search(rf"alter\s+table\s+{table}\s+add\s+column\s+device_id\b[^;]*\breferences\s+devices\s*\(",
                          everything, re.I)
        if not DEVICE_REF.search(body) and not later:
            bad.append(n)
    return bad


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
        check("an epoch column is found", bool(EPOCH_COLUMN.match("    arrived_ts  real    not null,")))
        check("so is an integer _at", bool(EPOCH_COLUMN.match("    seen_at integer,")))
        check("so is an added epoch column", bool(EPOCH_COLUMN.match("alter table x add column done_ts real")))
        check("an ISO text column is fine", not EPOCH_COLUMN.match("    created_at  text    not null,"))
        check("a duration is fine", not EPOCH_COLUMN.match("    duration_s  real    not null default 10,"))
        check("a pc_person text column is found", bool(PERSON_COLUMN.match("    pc_person       text unique,")))
        check("so is person_id", bool(PERSON_COLUMN.match("  person_id text not null,")))
        check("a ref column is fine", not PERSON_COLUMN.match("    person_ref      text not null,"))
        check("a person id column of numbers is fine", not PERSON_COLUMN.match("    person_id integer,"))
        check("a team text column is found", bool(PLACE_COLUMN.match("    team        text    not null,")))
        check("so is position", bool(PLACE_COLUMN.match("    position    text not null default '',")))
        check("a ref column is fine for a team", not PLACE_COLUMN.match("    team_ref    text not null default '',"))
        t = CREATE_TABLE.findall("create table x (\n    team text not null\n);")[0]
        check("a table with a team and no ref is caught", not has_ref("create table x (\n    team text\n);", "x", "team", t[1]))
        check("a ref in its own table is enough", has_ref("", "x", "team", "team text,\n team_ref text"))
        check("so is a later alter table", has_ref("alter table x add column team_ref text", "x", "team", "team text"))
        check("a module table of screens is found", bool(SCREEN_TABLE.match("create table lobby_screens (")))
        check("so is a plain screens table", bool(SCREEN_TABLE.match("create table screens(")))
        check("a table that only mentions screens is fine", not SCREEN_TABLE.match("create table screen_sources ("))
        check("a module table of gear is found", bool(GEAR_TABLE.match("create table rack_gear (")))
        check("so is a racks table", bool(GEAR_TABLE.match("create table racks(")))
        check("and a places table", bool(GEAR_TABLE.match("create table if not exists signage_places (")))
        check("a table that only mentions racks is fine", not GEAR_TABLE.match("create table rack_notes ("))
        check("a module table of ports is found", bool(PORT_TABLE.match("create table bm_ports (")))
        check("so is one of connectors", bool(PORT_TABLE.match("create table if not exists hub_connectors (")))
        check("a table that only mentions ports is fine", not PORT_TABLE.match("create table port_notes ("))
        check("a page script listing connectors is found", bool(CONNECTOR_JS.search("b.push(port('in1', 'sdi', 40, 56, 'IN 1'))")))
        check("drawing a connector the server sent is fine", not CONNECTOR_JS.search("if (k === 'sdi') { svgEl('circle') }"))
        check("a module table of cables is found", bool(CABLE_TABLE.match("create table bm_cables (")))
        check("so is a wiring table", bool(CABLE_TABLE.match("create table if not exists wiring(")))
        check("a table that only mentions cables is fine", not CABLE_TABLE.match("create table cable_notes ("))
        check("a module's own list of cables is found", bool(CABLE_LIST.search('defaults = {"devices": [], "cables": []}')))
        check("a cable the server sent is fine", not CABLE_LIST.search('return {"cables": cables(items)}'))
        check("a module table of channels is found", bool(CHANNEL_TABLE.match("create table mixer_channels (")))
        check("a table that only mentions channels is fine", not CHANNEL_TABLE.match("create table channel_log ("))
        check("a module's own list of channels is found", bool(CHANNEL_LIST.search('defaults = {"mics": [], "channels": []}')))
        check("a channel the server sent is fine", not CHANNEL_LIST.search('return {"channels": channels.listing()}'))
        check("a devices table is found", bool(DEVICE_TABLE.match("create table crew_devices (")))
        check("so is a push subscriptions table", bool(DEVICE_TABLE.match("create table x_push_subs (")))
        check("a table that only mentions devices is fine", not DEVICE_TABLE.match("create table device_log ("))
        check("a reference to core devices is a part", bool(DEVICE_REF.search("device_id integer references devices (id) on delete cascade")))
        check("a reference to another table is not", not DEVICE_REF.search("device_id integer references intercom_devices (id)"))
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
