"""Session recording: every committed line of a start->stop run, on disk.

`Channel.history` is a ring buffer sized for a late-joining viewer (40 lines by
default) and is cleared on every start, so it cannot answer "give me the
transcript of the event". This module keeps the other copy: an append-only
JSONL file per run, written as each line commits, so a crash or a container
restart still leaves the operator with everything said up to that point.

Off by default. Recording every word of a room to disk is the operator's
decision to make per deployment, not ours to make for them -- see
`recording.enabled` in config.json. Lines arrive here *after* the blocklist has
run, so a blocked term never reaches the file.

One run is one directory::

    <recordings dir>/20260920-143012/
        meta.json     run id, start/stop time, source language
        lines.jsonl   one committed caption line per row, as it happened

Exports (plain text, and the paired fine-tuning JSONL) are derived from
lines.jsonl on download -- see `exporting.py`. Nothing derived is stored, so a
better pairing pass can be re-run over an old event.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger("relay.recorder")

# A run id is a timestamp, and the id is used to build a filesystem path from
# an HTTP request. Nothing but this shape is ever accepted.
RUN_ID_RE = re.compile(r"^\d{8}-\d{6}(-\d+)?$")


class Recorder:
    """Writes the current run and reads back the finished ones.

    Every method is called from the event loop thread and does small, buffered
    file I/O: a committed caption line lands a few times a second at most, so
    an executor round trip would cost more than the write it defers.
    """

    def __init__(self) -> None:
        self.root: Path | None = None
        self.keep_runs = 20
        self.run_id: str | None = None
        self._dir: Path | None = None
        self._fh = None
        self._meta: dict[str, Any] = {}
        self._count = 0

    # -- lifecycle ----------------------------------------------------
    def configure(self, root: Path, keep_runs: int) -> None:
        self.root = Path(root)
        self.keep_runs = max(1, int(keep_runs or 1))

    @property
    def active(self) -> bool:
        return self._fh is not None

    def start_run(self, source_language: str, targets: list[str]) -> str | None:
        """Open a new run directory. Returns its id, or None if it could not
        be created -- a recording failure must never stop an event starting."""
        self.finish_run()
        if self.root is None:
            return None
        started = time.time()
        run_id = time.strftime("%Y%m%d-%H%M%S", time.localtime(started))
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            d = self.root / run_id
            # Two starts inside one second would otherwise land in the same
            # directory and interleave two events in one file.
            n = 1
            while d.exists():
                n += 1
                run_id = f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(started))}-{n}"
                d = self.root / run_id
            d.mkdir(parents=True)
            self._fh = open(d / "lines.jsonl", "a", encoding="utf-8")
        except OSError as exc:
            log.error("Could not start recording: %s", exc)
            self._fh = None
            return None
        self.run_id = run_id
        self._dir = d
        self._count = 0
        self._meta = {
            "run_id": run_id,
            "started": started,
            "ended": None,
            "source_language": source_language,
            "targets": list(targets),
            "lines": 0,
        }
        self._write_meta()
        self._prune()
        log.info("Recording run %s to %s", run_id, d)
        return run_id

    def append(self, stream: str, lang: str, seq: int, text: str,
               t0: float, t1: float) -> None:
        """Record one committed caption line. Errors are logged once and the
        run is closed: a full disk should cost the transcript, not the event."""
        if self._fh is None:
            return
        row = {"stream": stream, "lang": lang, "seq": seq,
               "t0": round(t0, 3), "t1": round(t1, 3), "text": text}
        try:
            self._fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            # Flushed per line: the value of this file is that it survives the
            # crash that loses everything else.
            self._fh.flush()
        except OSError as exc:
            log.error("Recording stopped, could not write line: %s", exc)
            self._close_fh()
            return
        self._count += 1

    def finish_run(self) -> str | None:
        """Close the open run and stamp its end time. Returns the run id."""
        if self._fh is None:
            return None
        run_id = self.run_id
        self._close_fh()
        self._meta["ended"] = time.time()
        self._meta["lines"] = self._count
        self._write_meta()
        log.info("Recorded %d lines to run %s", self._count, run_id)
        return run_id

    def _close_fh(self) -> None:
        try:
            if self._fh is not None:
                self._fh.close()
        except OSError:
            pass
        self._fh = None

    def _write_meta(self) -> None:
        if self._dir is None:
            return
        try:
            (self._dir / "meta.json").write_text(
                json.dumps(self._meta, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            log.error("Could not write run metadata: %s", exc)

    # -- reading back --------------------------------------------------
    def run_dir(self, run_id: str) -> Path | None:
        """The directory for `run_id`, or None if the id is not a run id we
        would have written. The id comes off an HTTP path, so this is the only
        place a caller is allowed to turn one into a path."""
        if self.root is None or not RUN_ID_RE.match(run_id or ""):
            return None
        d = self.root / run_id
        return d if d.is_dir() else None

    def list_runs(self) -> list[dict]:
        """Every recorded run, newest first, with the live one marked."""
        if self.root is None or not self.root.is_dir():
            return []
        out = []
        for d in self.root.iterdir():
            if not d.is_dir() or not RUN_ID_RE.match(d.name):
                continue
            meta = self._read_meta(d)
            if meta is None:
                continue
            if d.name == self.run_id and self.active:
                # The open run's meta.json still says lines: 0 -- it is stamped
                # at stop. Report what has actually been written so far.
                meta["lines"] = self._count
                meta["recording"] = True
                meta["interrupted"] = False  # still open, not cut short
            out.append(meta)
        out.sort(key=lambda m: m.get("started") or 0, reverse=True)
        return out

    def list_run(self, run_id: str) -> dict | None:
        """One run's metadata, with the same live/interrupted marking as
        `list_runs`."""
        for meta in self.list_runs():
            if meta.get("run_id") == run_id:
                return meta
        return None

    @classmethod
    def _read_meta(cls, d: Path) -> dict | None:
        try:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(meta, dict):
            return None
        meta.setdefault("run_id", d.name)
        meta.setdefault("lines", 0)
        meta["recording"] = False
        if meta.get("ended") is None:
            # Killed mid-run: the end time was never stamped and the line
            # count in meta.json is still zero. The lines themselves are on
            # disk (they are flushed as they commit), so count them and say
            # plainly that this run was cut short rather than showing it empty.
            meta["interrupted"] = True
            meta["lines"] = len(cls.read_lines(d))
        return meta

    @staticmethod
    def read_lines(d: Path) -> list[dict]:
        """Parse a run's lines.jsonl. A truncated final row -- the shape a kill
        during a write leaves behind -- is skipped, not fatal."""
        rows: list[dict] = []
        try:
            with open(d / "lines.jsonl", encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        row = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(row, dict) and row.get("text"):
                        rows.append(row)
        except OSError:
            return []
        return rows

    def delete_run(self, run_id: str) -> bool:
        d = self.run_dir(run_id)
        if d is None:
            return False
        if self.active and run_id == self.run_id:
            return False  # refuse to delete the run being written
        shutil.rmtree(d, ignore_errors=True)
        return not d.exists()

    def _prune(self) -> None:
        """Drop the oldest runs beyond `keep_runs`. Runs at start, so the cap
        is enforced before an event fills the disk rather than after."""
        runs = [m["run_id"] for m in self.list_runs()]
        for run_id in runs[self.keep_runs:]:
            if run_id == self.run_id:
                continue
            log.info("Pruning old recording %s", run_id)
            self.delete_run(run_id)


def iter_runs(root: Path) -> Iterator[Path]:
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if d.is_dir() and RUN_ID_RE.match(d.name):
                yield d


recorder = Recorder()
