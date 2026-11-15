"""What every test script shares: the checks, sections that survive an exception, waiting without fixed
sleeps, temp folders that are removed, and the summary at the end.

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _harness import check, eq, fails, section, done

    with section("off"):
        eq("the page is gone", client.get("/x").status_code, 404)
    done()

A test script is still a plain script: `python tests/test_x.py` runs it, a non-zero exit is a failure.
tests/run_all.py runs them all at once, and pytest collects them through tests/conftest.py.
"""
from __future__ import annotations

import asyncio
import atexit
import contextlib
import os
import shutil
import sys
import tempfile
import time
import traceback

fails: list[str] = []
SKIP_MARK = "SKIPPED:"          # tests/run_all.py and tests/conftest.py look for this line


def _report(ok: bool, label: str, detail: str = "") -> bool:
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        if detail:
            print(detail)
        fails.append(label)
    return ok


def eq(label, got, want) -> bool:
    """Pass when got == want; on failure show both."""
    ok = got == want
    return _report(ok, label, "" if ok else f"         got  {got!r}\n         want {want!r}")


def check(label, cond, extra="") -> bool:
    """Pass when cond is truthy. `extra` is shown on failure."""
    return _report(bool(cond), label, f"         {extra!r}" if extra != "" and not cond else "")


@contextlib.contextmanager
def section(name: str):
    """Print a heading; an exception inside is one failure and the script carries on with the next section."""
    print(name)
    try:
        yield
    except Exception:
        _report(False, f"{name}: raised", "".join("         " + line for line in
                                                  traceback.format_exc().splitlines(True)))


def wait_for(pred, timeout: float = 5.0, interval: float = 0.01):
    """Poll pred() until it is truthy (returned) or timeout passes (the last value is returned)."""
    deadline = time.monotonic() + timeout
    while True:
        value = pred()
        if value or time.monotonic() >= deadline:
            return value
        time.sleep(interval)


async def await_for(pred, timeout: float = 5.0, interval: float = 0.01):
    """wait_for inside an event loop: pred() is polled between awaits, so tasks keep running."""
    deadline = time.monotonic() + timeout
    while True:
        value = pred()
        if value or time.monotonic() >= deadline:
            return value
        await asyncio.sleep(interval)


_temps: list[str] = []


def tmpdir(prefix: str = "relay-test-") -> str:
    """A fresh folder removed when the script exits."""
    path = tempfile.mkdtemp(prefix=prefix)
    _temps.append(path)
    return path


@atexit.register
def _cleanup() -> None:
    for path in _temps:
        shutil.rmtree(path, ignore_errors=True)


def skip(reason: str) -> None:
    """End the script now as skipped (exit 0), e.g. when an optional tool is not installed."""
    print(f"{SKIP_MARK} {reason}")
    sys.exit(0)


def done() -> None:
    """Print the summary and exit 1 if anything failed."""
    if fails:
        print(f"\n{len(fails)} failed")
        sys.exit(1)
    print("\nall passed")
