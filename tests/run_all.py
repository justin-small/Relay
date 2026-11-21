"""Run every test script at once and report every failure, not just the first.

    python tests/run_all.py                 # the whole suite, one process per file, several at a time
    python tests/run_all.py -k crew pclink  # only files whose name contains one of these
    python tests/run_all.py --coverage      # also record coverage (needs `pip install coverage`)

It first builds the Rust extensions, the audio bus core and the NDI engine (tools/build_bus_wheel.py --if-stale), which needs Rust installed, then lints both crates with `cargo clippy -D warnings`, as CI does.
    python tests/run_all.py --junit out.xml # a JUnit report, one test case per file

Each file runs in its own process with its own temp folder (TMPDIR), removed afterwards, so nothing a test
leaves behind piles up. A file passes when it exits 0; one that prints the _harness skip line is skipped.
Exit status is 1 if any file failed.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from xml.sax.saxutils import escape

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
SKIP_MARK = "SKIPPED:"


def scripts() -> list[Path]:
    return sorted(TESTS.glob("test_*.py")) + [TESTS / "smoke_test.py"]


def is_pytest_style(path: Path) -> bool:
    """A file with top-level `def test_` functions is written for pytest, not run as a script."""
    return any(line.startswith(("def test_", "async def test_")) for line in path.read_text().splitlines())


def command(path: Path, coverage: bool) -> list[str]:
    runner = [sys.executable, "-m", "coverage", "run", "-p", f"--rcfile={ROOT / '.coveragerc'}"] if coverage \
        else [sys.executable]
    if is_pytest_style(path):
        return runner + ["-m", "pytest", "-q", "-p", "no:cacheprovider", str(path)]
    return runner + [str(path)]


def run(path: Path, coverage: bool, timeout: float) -> dict:
    tmp = tempfile.mkdtemp(prefix=f"relay-{path.stem}-")
    env = dict(os.environ, TMPDIR=tmp, TEMP=tmp, TMP=tmp, PYTHONUNBUFFERED="1")
    env.setdefault("RELAY_KEY_STORE", "file")      # never the developer's Keychain (app/secretbox.py)
    start = time.monotonic()
    try:
        proc = subprocess.run(command(path, coverage), cwd=ROOT, env=env, capture_output=True, text=True,
                              timeout=timeout, encoding="utf-8", errors="replace")
        code, out = proc.returncode, proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as e:
        code = -1
        out = (e.stdout or "") if isinstance(e.stdout, str) else (e.stdout or b"").decode(errors="replace")
        out += f"\n(timed out after {timeout:.0f}s)"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    skipped = code == 0 and any(line.startswith(SKIP_MARK) for line in out.splitlines())
    return {"path": path, "code": code, "out": out, "secs": time.monotonic() - start, "skipped": skipped}


# Files that pace a fake device in real time (a 5 ms tick, a 10 ms feed). Run beside seven other test processes on a CI
# runner they are starved by the scheduler and fail for it, not for a fault, so they run alone, after the rest.
ALONE = {"test_audiobus.py"}


def junit(results: list[dict], dest: str) -> None:
    cases = []
    for r in results:
        name = escape(r["path"].name, {'"': "&quot;"})
        body = f'<skipped message="{escape(r["out"].strip().splitlines()[-1] if r["out"].strip() else "")}"/>' \
            if r["skipped"] else (f'<failure message="exit {r["code"]}">{escape(r["out"])}</failure>' if r["code"] else "")
        cases.append(f'  <testcase classname="tests" name="{name}" time="{r["secs"]:.2f}">{body}</testcase>')
    failed = sum(1 for r in results if r["code"])
    Path(dest).write_text(f'<?xml version="1.0" encoding="utf-8"?>\n<testsuite name="relay" tests="{len(results)}" '
                          f'failures="{failed}">\n' + "\n".join(cases) + "\n</testsuite>\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-k", nargs="*", default=[], help="only files whose name contains one of these")
    ap.add_argument("-j", type=int, default=min(8, os.cpu_count() or 2), help="files at a time (default: up to 8)")
    ap.add_argument("--coverage", action="store_true", help="record coverage into .coverage.* files")
    ap.add_argument("--junit", metavar="FILE", help="write a JUnit XML report")
    ap.add_argument("--timeout", type=float, default=300, help="seconds per file (default 300)")
    ap.add_argument("-v", action="store_true", help="print every file's output, not only failures")
    args = ap.parse_args(argv)

    todo = [p for p in scripts() if not args.k or any(k in p.name for k in args.k)]
    if not todo:
        print("no test files match")
        return 1
    # The audio bus core and the NDI engine are Rust extensions every test process may import: build it first when the sources changed.
    built = subprocess.run([sys.executable, str(ROOT / "tools" / "build_bus_wheel.py"), "--if-stale"])
    if built.returncode:
        print("The Rust extensions (relay_bus, relay_ndi) could not be built; see above.")
        return 1
    # CI runs clippy on both Rust crates with warnings as errors, and the tests alone do not: run it here so a push does not fail on it.
    for crate in ("relay-bus", "relay-ndi"):
        lint = subprocess.run(["cargo", "clippy", "--locked", "--all-targets", "--", "-D", "warnings"],
                              cwd=ROOT / "native" / crate)
        if lint.returncode:
            print(f"cargo clippy found problems in native/{crate}; see above.")
            return 1
    results = []

    def report(r):
        results.append(r)
        state = "skip" if r["skipped"] else ("ok  " if r["code"] == 0 else "FAIL")
        print(f"{state} {r['secs']:6.1f}s  {r['path'].relative_to(ROOT)}", flush=True)
        if args.v or r["code"]:
            print("\n".join("    " + line for line in r["out"].rstrip().splitlines()), flush=True)

    together = [p for p in todo if p.name not in ALONE]
    with cf.ThreadPoolExecutor(max_workers=max(1, args.j)) as pool:
        futures = [pool.submit(run, p, args.coverage, args.timeout) for p in together]
        for fut in cf.as_completed(futures):
            report(fut.result())
    for p in todo:
        if p.name in ALONE:
            report(run(p, args.coverage, args.timeout))

    results.sort(key=lambda r: r["path"].name)
    if args.junit:
        junit(results, args.junit)
    failed = [r for r in results if r["code"]]
    skipped = [r for r in results if r["skipped"]]
    print(f"\n{len(results)} files: {len(results) - len(failed) - len(skipped)} passed, "
          f"{len(skipped)} skipped, {len(failed)} failed")
    for r in skipped:
        reason = next((ln for ln in r["out"].splitlines() if ln.startswith(SKIP_MARK)), "")
        print(f"  skipped {r['path'].relative_to(ROOT)}  {reason[len(SKIP_MARK):].strip()}")
    for r in failed:
        print(f"  FAILED  {r['path'].relative_to(ROOT)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
