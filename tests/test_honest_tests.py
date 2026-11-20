"""Tests that cannot lie: no guessed waits, no swapped-out behaviour, no masked flakes, in the Python tests.

This file is the rule's enforcement, run in CI on every pull request. It reads every tests/*.py (but
tests/_harness.py, whose wait_for is the one sanctioned poll) and counts, per file:

* sleep    a fixed time.sleep / asyncio.sleep (sleep(0), a bare yield, is fine). Wait on the thing itself with
           wait_for / await_for from tests/_harness.py, or await the operation. A sleep guesses how long real
           work takes and fails on a loaded CI machine;
* mock     unittest.mock (patch, patch.object, Mock, MagicMock, AsyncMock, create_autospec) or pytest's
           monkeypatch.setattr. patch.dict(os.environ, ...) is configuration, not behaviour, and is not counted.
           Run the real code; fake only the far side of a wire (a fake server on a real socket);
* patch    a hand-written swap of a function or object on an imported module (routes.fetch = fake).
           Not counted: UPPER_CASE tuning constants, state set to a constant or literal (svc.running = True),
           and putting the original back (routes.fetch = _real_fetch);
* timeout  wait_for / await_for given a timeout above the harness default of 5 seconds. A test that needs
           longer is waiting on the wrong thing;
* retry    a loop that catches AssertionError and goes round again. A test that passes on a rerun is a flake.

Each count is checked against tests/honest_tests_baseline.txt, which holds what existed when this test came in
and may only shrink: a file over its count fails, and a file under its count fails until the line is lowered
(run with --rebaseline, which refuses to raise any count).

A use that is right goes on a line with a reason: `time.sleep(0.3)  # allow-sleep: shows no second push arrives`.
AGENTS.md allows a fixed sleep only to show that something does *not* happen; mock and patch only at a seam
Relay cannot reach in a test (the OS network stack, sys.platform).

Run: .venv/bin/python tests/test_honest_tests.py        (--rebaseline lowers the baseline after a fix)
"""
from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BASELINE = HERE / "honest_tests_baseline.txt"
SKIP = {"_harness.py"}
DEFAULT_TIMEOUT = 5.0
RULES = ("sleep", "mock", "patch", "timeout", "retry")
ALLOW = re.compile(r"#\s*allow-(" + "|".join(RULES) + r"):\s*\S")
MOCK_CALLS = {"patch", "Mock", "MagicMock", "AsyncMock", "NonCallableMock", "create_autospec", "PropertyMock"}
MOCK_PATCH_ATTRS = {"object", "multiple", "dict"}
RESTORE = re.compile(r"_*(real|orig|old|saved|prev|was)", re.I)
ADVICE = {
    "sleep": "wait on the event with wait_for/await_for, or mark '# allow-sleep: <why nothing should happen>'",
    "mock": "run the real code, fake the far side of the wire, or mark '# allow-mock: <the seam>'",
    "patch": "pass the dependency in, or mark '# allow-patch: <the seam>'",
    "timeout": "wait on the right thing within the default 5 s, or mark '# allow-timeout: <why>'",
    "retry": "fix the flake instead of going round again",
}

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, done, eq, section  # noqa: E402


def scanned_files() -> list[Path]:
    return sorted(p for p in HERE.glob("*.py") if p.name not in SKIP)


def dotted(node: ast.AST) -> str:
    """'mock.patch.object' for the expression mock.patch.object, '' for anything that is not a plain name chain."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ""
    parts.append(node.id)
    return ".".join(reversed(parts))


def root_name(node: ast.AST) -> str | None:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


class Scanner(ast.NodeVisitor):
    def __init__(self, src: str):
        self.lines = src.splitlines()
        self.hits: list[tuple[str, int]] = []
        self.imported: set[str] = set()
        self.sleep_names: set[str] = set()     # `from time import sleep`
        self.mock_names: set[str] = set()      # `from unittest.mock import patch, MagicMock`
        self.mock_modules: set[str] = set()    # `from unittest import mock`, `import unittest.mock as m`
        self.loops = 0

    def allowed(self, rule: str, node: ast.AST) -> bool:
        first, last = node.lineno, getattr(node, "end_lineno", node.lineno) or node.lineno
        return any(m.group(1) == rule for ln in range(first, last + 1)
                   for m in ALLOW.finditer(self.lines[ln - 1]))

    def hit(self, rule: str, node: ast.AST) -> None:
        if not self.allowed(rule, node):
            self.hits.append((rule, node.lineno))

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            name = a.asname or a.name.split(".")[0]
            self.imported.add(name)
            if a.name == "unittest.mock" and a.asname:
                self.mock_modules.add(a.asname)
            if a.name == "mock":
                self.mock_modules.add(a.asname or "mock")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for a in node.names:
            name = a.asname or a.name
            self.imported.add(name)
            if node.module in ("time", "asyncio") and a.name == "sleep":
                self.sleep_names.add(name)
            if node.module == "unittest" and a.name == "mock":
                self.mock_modules.add(name)
            if node.module in ("unittest.mock", "mock") and a.name in MOCK_CALLS:
                self.mock_names.add(name)

    def is_sleep(self, func: ast.AST) -> bool:
        name = dotted(func)
        return name in ("time.sleep", "asyncio.sleep", "trio.sleep") or name in self.sleep_names

    def mock_kind(self, func: ast.AST) -> str | None:
        """'patch.dict' / 'patch.object' / 'MagicMock' ... for a mock call, None otherwise."""
        parts = dotted(func).split(".")
        if not parts[0]:
            return None
        if parts[0] in self.mock_modules and len(parts) >= 2 and parts[1] in MOCK_CALLS:
            rest = parts[1:]
        elif parts[0] in self.mock_names:
            rest = parts
        elif parts[-2:] == ["monkeypatch", "setattr"] or parts == ["monkeypatch", "setattr"]:
            return "monkeypatch.setattr"
        else:
            return None
        if rest[0] == "patch" and len(rest) == 2 and rest[1] in MOCK_PATCH_ATTRS:
            return "patch." + rest[1]
        return rest[0] if len(rest) == 1 else None

    def visit_Call(self, node: ast.Call) -> None:
        if self.is_sleep(node.func):
            arg = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg in ("secs", "delay")), None)
            if not (isinstance(arg, ast.Constant) and arg.value == 0):
                self.hit("sleep", node)
        kind = self.mock_kind(node.func)
        if kind and not (kind == "patch.dict" and node.args and dotted(node.args[0]) == "os.environ"):
            self.hit("mock", node)
        if dotted(node.func).split(".")[-1] in ("wait_for", "await_for") and dotted(node.func) != "asyncio.wait_for":
            for k in node.keywords:
                if (k.arg == "timeout" and isinstance(k.value, ast.Constant)
                        and isinstance(k.value.value, (int, float)) and k.value.value > DEFAULT_TIMEOUT):
                    self.hit("timeout", node)
        self.generic_visit(node)

    @staticmethod
    def swaps_behaviour(value: ast.AST | None) -> bool:
        """False for state (a constant or a literal) and for putting the original back (x.f = _real_f)."""
        if value is None:
            return True
        if isinstance(value, (ast.Constant, ast.Dict, ast.List, ast.Set, ast.Tuple)) or (
                isinstance(value, ast.UnaryOp) and isinstance(value.operand, ast.Constant)):
            return False
        return not (isinstance(value, ast.Name) and RESTORE.match(value.id))

    def visit_Assign(self, node: ast.Assign) -> None:
        for t in node.targets:
            if isinstance(t, ast.Tuple):
                values = node.value.elts if isinstance(node.value, ast.Tuple) and len(node.value.elts) == len(t.elts) \
                    else [None] * len(t.elts)
                pairs = zip(t.elts, values)
            else:
                pairs = [(t, node.value)]
            for target, value in pairs:
                if (isinstance(target, ast.Attribute) and root_name(target) in self.imported
                        and not target.attr.isupper() and self.swaps_behaviour(value)):
                    self.hit("patch", target)
        self.generic_visit(node)

    def visit_loop(self, node: ast.AST) -> None:
        self.loops += 1
        self.generic_visit(node)
        self.loops -= 1

    visit_For = visit_While = visit_AsyncFor = visit_loop

    def visit_Try(self, node: ast.Try) -> None:
        if self.loops:
            for h in node.handlers:
                types = h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]
                if any(dotted(t) == "AssertionError" for t in types if t is not None):
                    self.hit("retry", h)
        self.generic_visit(node)

    visit_TryStar = visit_Try


def scan(path: Path) -> list[tuple[str, int]]:
    src = path.read_text(encoding="utf-8")
    s = Scanner(src)
    tree = ast.parse(src, filename=str(path))
    # Imports first, so a name imported below its first use (or inside a function) is still known.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            s.visit_Import(node)
        elif isinstance(node, ast.ImportFrom):
            s.visit_ImportFrom(node)
    s.visit(tree)
    return sorted(s.hits, key=lambda h: (h[1], h[0]))


def counts() -> dict[tuple[str, str], list[int]]:
    """(rule, file) -> the lines it is used on."""
    out: dict[tuple[str, str], list[int]] = {}
    for p in scanned_files():
        rel = p.relative_to(ROOT).as_posix()
        for rule, line in scan(p):
            out.setdefault((rule, rel), []).append(line)
    return out


def read_baseline() -> dict[tuple[str, str], int]:
    out = {}
    if BASELINE.exists():
        for line in BASELINE.read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                rule, n, rel = line.split()
                out[(rule, rel)] = int(n)
    return out


def write_baseline(rows: dict[tuple[str, str], int]) -> None:
    head = ("# Fixed sleeps, mocks, hand-written patches, raised timeouts and retries in the Python tests when\n"
            "# tests/test_honest_tests.py came in. This list may only shrink: fix one, lower its count here\n"
            "# (or run the test with --rebaseline).\n"
            "# rule  count  file\n")
    body = "".join(f"{rule} {n} {rel}\n" for (rule, rel), n in sorted(rows.items(), key=lambda r: (r[0][1], r[0][0])))
    BASELINE.write_text(head + body)


def selftest() -> None:
    sample = '''
import asyncio, time
from unittest import mock
from unittest.mock import MagicMock
import routes
from _harness import wait_for
time.sleep(0.5)
time.sleep(0)
asyncio.sleep(0, result=1)
time.sleep(0.2)  # allow-sleep: shows nothing is pushed twice
code = "import time; time.sleep(9)"
with mock.patch.object(routes, "fetch", return_value=1):
    pass
with mock.patch.dict(os.environ, {"A": "1"}):
    pass
m = MagicMock()
routes.fetch = lambda: 1
routes.RETRY_S = 0.1
routes.state, routes.fetch = "on", _real_fetch
wait_for(lambda: True, timeout=30)
wait_for(lambda: True, timeout=2)
for _ in range(3):
    try:
        run()
    except AssertionError:
        continue
'''
    s = Scanner(sample)
    tree = ast.parse(sample)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            s.visit_Import(node)
        elif isinstance(node, ast.ImportFrom):
            s.visit_ImportFrom(node)
    s.visit(tree)
    got = sorted(s.hits, key=lambda h: (h[1], h[0]))
    eq("the scanner finds exactly the planted cases", got,
       [("sleep", 7), ("mock", 12), ("mock", 16), ("patch", 17), ("timeout", 20), ("retry", 25)])


def main() -> None:
    if "--rebaseline" in sys.argv:
        now = {k: len(v) for k, v in counts().items()}
        old = read_baseline()
        raised = {k: (old.get(k, 0), n) for k, n in now.items() if n > old.get(k, 0)}
        if raised and BASELINE.exists():
            print("Not rewriting: these counts went up, and the baseline may only shrink:")
            for (rule, rel), (was, n) in sorted(raised.items()):
                print(f"   {rule} {rel}: {was} -> {n}")
            sys.exit(1)
        write_baseline(now)
        print(f"baseline: {sum(old.values())} -> {sum(now.values())} uses")
        return

    with section("the scanner"):
        selftest()

    with section("no new sleeps, mocks, patches, raised timeouts or retries"):
        now, base = counts(), read_baseline()
        over, under = [], []
        for key in sorted(set(now) | set(base)):
            rule, rel = key
            lines, allowed = now.get(key, []), base.get(key, 0)
            if len(lines) > allowed:
                over.append(f"{rel}: {len(lines)} {rule} (baseline {allowed}) at lines "
                            f"{', '.join(map(str, lines))}; {ADVICE[rule]}")
            elif len(lines) < allowed:
                under.append(f"{rel}: {rule} {allowed} -> {len(lines)}")
        eq("files over their baseline", over, [])
        eq("files under their baseline (lower the count in tests/honest_tests_baseline.txt, or --rebaseline)",
           under, [])

    with section("every test file parses"):
        for p in scanned_files():
            try:
                ast.parse(p.read_text(encoding="utf-8"))
                ok = True
            except SyntaxError as e:
                ok = e
            check(f"{p.name} parses", ok is True, ok)

    done()


if __name__ == "__main__":
    main()
