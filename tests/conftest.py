"""Lets pytest run the suite as it is, and new tests written for pytest beside it.

A test script (no top-level `def test_`) is one pytest item that runs the file in its own process, exactly as
`python tests/test_x.py` and tests/run_all.py do: importing it in pytest's process would run every check at
collection time and share one database and config between files. A file with top-level `def test_` functions is
collected by pytest as usual, so a file can be moved over to fixtures one at a time.

    python -m pytest tests                  # everything (add -n auto with pytest-xdist for parallel)
    python -m pytest tests -k crew          # files whose name matches
    python -m pytest tests --junitxml=r.xml
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
SKIP_MARK = "SKIPPED:"


def _is_script(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return not any(line.startswith(("def test_", "async def test_")) for line in text.splitlines())


def pytest_collect_file(parent, file_path: Path):
    if file_path.suffix == ".py" and file_path.parent == TESTS and file_path.name == "smoke_test.py":
        return ScriptFile.from_parent(parent, path=file_path)
    return None


@pytest.hookimpl(tryfirst=True)
def pytest_pycollect_makemodule(module_path: Path, parent):
    if module_path.parent == TESTS and module_path.name.startswith("test_") and _is_script(module_path):
        return ScriptFile.from_parent(parent, path=module_path)
    return None


class ScriptFile(pytest.File):
    def collect(self):
        yield ScriptItem.from_parent(self, name=self.path.stem)


class ScriptFailed(Exception):
    pass


class ScriptItem(pytest.Item):
    def runtest(self):
        tmp = tempfile.mkdtemp(prefix=f"relay-{self.path.stem}-")
        env = dict(os.environ, TMPDIR=tmp, TEMP=tmp, TMP=tmp, PYTHONUNBUFFERED="1")
        try:
            proc = subprocess.run([sys.executable, str(self.path)], cwd=ROOT, env=env, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=300)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        out = proc.stdout + proc.stderr
        if proc.returncode:
            raise ScriptFailed(out)
        reason = next((ln for ln in out.splitlines() if ln.startswith(SKIP_MARK)), None)
        if reason:
            pytest.skip(reason[len(SKIP_MARK):].strip())

    def repr_failure(self, excinfo):
        if isinstance(excinfo.value, ScriptFailed):
            lines = str(excinfo.value).rstrip().splitlines()
            fails = [ln for ln in lines if ln.lstrip().startswith("FAIL")]
            head = "\n".join(fails) + "\n\n--- full output (last 80 lines) ---\n" if fails else ""
            return head + "\n".join(lines[-80:])
        return super().repr_failure(excinfo)

    def reportinfo(self):
        return self.path, None, f"script {self.path.name}"
