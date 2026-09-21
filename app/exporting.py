"""Turn a recorded run into the files a client (or a fine-tune) wants.

Everything here is derived on download from `lines.jsonl`; nothing is stored,
so improving the pairing below improves every past event too.

Three kinds of file come out of one run:

* ``transcript-<LANG>.txt``  -- one timestamped line per committed caption,
  per language. What a client asks for after an event.
* ``pairs-<TARGET>.jsonl``   -- every source line matched to its translation,
  each row carrying a `confidence` field. The audit copy: filter on it.
* ``finetune-<TARGET>.jsonl`` -- the confident pairs only, in OpenAI chat
  format, ready to upload to a fine-tuning job with no conversion step.

**On pairing.** There is no segment id tying a source line to its translation.
The two feeds arrive as separate event streams and are re-segmented
independently by this app's own idle timers (see `realtime.py`), so the line
counts do not match: one spoken sentence can commit as one English line and two
Spanish ones, or the reverse. What both feeds do share is the clock -- they are
produced from the same audio, near-simultaneously -- so lines are matched by
overlapping time span, and every row says how sure that match is:

    exact   one source line, one translation, neither claimed twice
    merged  several source lines inside one translation's span, joined
    split   one source line answered by several translations
    loose   no overlap and no unambiguous near match; nearest line in a
            wider window
    unpaired  nothing plausible on the other side (a dropout, usually)

Only `exact` rows reach the fine-tuning file. The rest stay in the pairs file
where they can be inspected, corrected or ignored.
"""
from __future__ import annotations

import io
import json
import time
import zipfile

from . import languages

# The rescue window for a translation that overlaps nothing -- the model is
# interpreting, so its line can land just after the source line it answers.
# Only used when it picks out exactly one source line; a tolerance wide enough
# to reach two is a tolerance wide enough to pick the wrong one.
PAIR_TOLERANCE_S = 1.5
# Last resort: the nearest source line within this window, reported as a
# `loose` match. Beyond it the translation is called unpaired.
LOOSE_TOLERANCE_S = 4.5

TRANSCRIPTION_STREAM = "transcription"
TRANSLATION_STREAM = "translation"


def _label(lang: str) -> str:
    spec = languages.SOURCE_LANGUAGES.get(lang) or languages.TARGETS.get(lang)
    return spec["label"] if spec else lang.title()


def _clock(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def _stamp(ts: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "—"


def split_streams(rows: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    """Source lines, and translation lines grouped by target language.

    Grouping is done from the rows themselves rather than from the run's
    metadata, so a target enabled or disabled part-way through an event still
    exports exactly the lines it produced.
    """
    source: list[dict] = []
    targets: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("stream") == TRANSCRIPTION_STREAM:
            source.append(row)
        elif row.get("stream") == TRANSLATION_STREAM:
            targets.setdefault(row.get("lang") or "?", []).append(row)
    source.sort(key=lambda r: r.get("t0") or 0)
    for lines in targets.values():
        lines.sort(key=lambda r: r.get("t0") or 0)
    return source, targets


def _span(row: dict) -> tuple[float, float]:
    t0 = float(row.get("t0") or row.get("t1") or 0.0)
    t1 = float(row.get("t1") or t0)
    return (t0, t1) if t1 >= t0 else (t1, t0)


def _overlaps(span: tuple[float, float], a0: float, a1: float, tol: float = 0.0) -> bool:
    b0, b1 = span
    return b1 + tol >= a0 and b0 - tol <= a1


def pair_lines(source: list[dict], target: list[dict],
               tol: float = PAIR_TOLERANCE_S) -> list[dict]:
    """Match translation lines to source lines by overlapping time span.

    Returns one row per pair, in time order, each with a `confidence` (see the
    module docstring). Source lines nothing matched are emitted too, as
    `unpaired` rows with an empty translation, so a dropout is visible in the
    export instead of silently absent.

    Two passes: the first works out which source lines each translation could
    belong to, the second grades the match -- a source line claimed by two
    translations is only detectable once every translation has been looked at.
    """
    # -- pass 1: candidates ------------------------------------------------
    # A genuine time overlap decides the match. `tol` is only a rescue for a
    # translation that lands just after the source line it answers (the model
    # is interpreting, so it lags), and never widens a single match into
    # several -- otherwise the sentence *next* to the right one gets swept in
    # whenever a speaker pauses for less than the tolerance.
    claims: list[list[int]] = []
    rescued: list[bool] = []
    claimed_by: dict[int, int] = {}
    for tr in target:
        a0, a1 = _span(tr)
        hits = [i for i, src in enumerate(source) if _overlaps(_span(src), a0, a1)]
        loose = False
        if not hits:
            # No overlap. The one thing we know about the missing direction is
            # that a translation cannot precede its source: the model is
            # interpreting, so its line lands *after* the sentence it answers.
            # So the only plausible candidate is the source line that had just
            # finished -- which also rules out the next sentence, the one a
            # symmetric window would otherwise reach whenever the speaker
            # pauses for less than `tol`.
            near = [i for i, src in enumerate(source)
                    if 0 <= a0 - _span(src)[1] <= tol]
            if len(near) == 1:
                hits = near
            else:
                pick = _nearest(source, a0, LOOSE_TOLERANCE_S)
                hits = [pick] if pick is not None else []
                loose = bool(hits)
        claims.append(hits)
        rescued.append(loose)
        for i in hits:
            claimed_by[i] = claimed_by.get(i, 0) + 1

    # -- pass 2: grade -----------------------------------------------------
    rows: list[dict] = []
    for tr, hits, loose in zip(target, claims, rescued):
        a0, _ = _span(tr)
        if not hits:
            rows.append(_pair_row(None, tr, "unpaired", a0))
            continue
        srcs = [source[i] for i in hits]
        if len(hits) > 1:
            confidence = "merged"
        elif claimed_by.get(hits[0], 0) > 1:
            confidence = "split"
        else:
            confidence = "loose" if loose else "exact"
        rows.append(_pair_row(srcs, tr, confidence, a0))

    for i, src in enumerate(source):
        if not claimed_by.get(i):
            rows.append(_pair_row([src], None, "unpaired", _span(src)[0]))

    rows.sort(key=lambda r: r["t0"])
    return rows


def _nearest(source: list[dict], t0: float, window: float) -> int | None:
    best, best_d = None, window
    for i, src in enumerate(source):
        d = abs(_span(src)[0] - t0)
        if d < best_d:
            best, best_d = i, d
    return best


def _pair_row(srcs: list[dict] | None, tr: dict | None,
              confidence: str, t0: float) -> dict:
    return {
        "t0": round(t0, 3),
        "time": _clock(t0),
        "confidence": confidence,
        "source": " ".join(s["text"] for s in srcs) if srcs else "",
        "target": tr["text"] if tr else "",
        "source_seq": [s.get("seq") for s in srcs] if srcs else [],
        "target_seq": tr.get("seq") if tr else None,
    }


def transcript_text(meta: dict, lang: str, lines: list[dict]) -> str:
    out = [
        f"# {_label(lang)} transcript — Relay run {meta.get('run_id', '?')}",
        f"# started {_stamp(meta.get('started'))}"
        f"   ended {_stamp(meta.get('ended'))}"
        f"   {len(lines)} lines",
        "",
    ]
    out += [f"[{_clock(_span(r)[0])}] {r['text']}" for r in lines]
    return "\n".join(out) + "\n"


def pairs_jsonl(source_lang: str, target_lang: str, pairs: list[dict]) -> str:
    out = []
    for row in pairs:
        out.append(json.dumps(
            {"source_language": source_lang, "target_language": target_lang, **row},
            ensure_ascii=False,
        ))
    return "\n".join(out) + ("\n" if out else "")


def finetune_jsonl(source_lang: str, target_lang: str, pairs: list[dict]) -> str:
    """The confident pairs, in OpenAI chat fine-tuning format.

    Strictly `{"messages": [...]}` per row and nothing else: the uploader
    rejects unknown top-level keys, and the provenance a reviewer wants lives
    in the pairs file next to it.
    """
    system = (
        f"Translate the user's {_label(source_lang)} into {_label(target_lang)}. "
        "This is live speech from a conference floor: keep the speaker's "
        "register, translate only, and add nothing."
    )
    out = []
    for row in pairs:
        if row["confidence"] != "exact" or not row["source"] or not row["target"]:
            continue
        out.append(json.dumps({"messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": row["source"]},
            {"role": "assistant", "content": row["target"]},
        ]}, ensure_ascii=False))
    return "\n".join(out) + ("\n" if out else "")


def build_exports(meta: dict, rows: list[dict]) -> dict[str, str]:
    """Every derived file for one run, as {filename: text}."""
    source_lang = meta.get("source_language") or "ENGLISH"
    source, targets = split_streams(rows)
    files: dict[str, str] = {}
    if source:
        files[f"transcript-{source_lang}.txt"] = transcript_text(meta, source_lang, source)
    for lang, lines in sorted(targets.items()):
        files[f"transcript-{lang}.txt"] = transcript_text(meta, lang, lines)
        pairs = pair_lines(source, lines)
        files[f"pairs-{lang}.jsonl"] = pairs_jsonl(source_lang, lang, pairs)
        ft = finetune_jsonl(source_lang, lang, pairs)
        if ft:
            files[f"finetune-{lang}.jsonl"] = ft
    files["manifest.txt"] = _manifest(meta, source, targets, files)
    return files


def _manifest(meta: dict, source: list[dict], targets: dict[str, list[dict]],
              files: dict[str, str]) -> str:
    started, ended = meta.get("started"), meta.get("ended")
    dur = int((ended or started or 0) - (started or 0))
    out = [
        f"Relay run {meta.get('run_id', '?')}",
        f"  started  {_stamp(started)}",
        f"  ended    {_stamp(ended)}",
        f"  duration {dur // 3600:d}h {dur % 3600 // 60:02d}m {dur % 60:02d}s",
        f"  source   {_label(meta.get('source_language') or 'ENGLISH')}"
        f" ({len(source)} lines)",
        "  targets  " + (", ".join(
            f"{_label(l)} ({len(v)} lines)" for l, v in sorted(targets.items())
        ) or "none"),
        "",
        "Files",
        "  lines.jsonl       every committed line as it happened (the raw record)",
        "  transcript-*.txt  one timestamped line per caption, per language",
        "  pairs-*.jsonl     source matched to translation, with a confidence field",
        "  finetune-*.jsonl  the 'exact' pairs only, in OpenAI chat format",
        "",
        "Pairing confidence: exact | merged (several source lines in one",
        "translation) | split (one source line, several translations) | loose",
        "(no time overlap, nearest match) | unpaired (nothing on the other side).",
        "Only 'exact' rows are carried into the fine-tuning file.",
        "",
        "Contents",
    ]
    out += [f"  {name}" for name in sorted(files)]
    return "\n".join(out) + "\n"


def zip_bytes(meta: dict, rows: list[dict], raw: str = "") -> bytes:
    """Everything for one run in a single download."""
    files = build_exports(meta, rows)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        run = meta.get("run_id", "run")
        for name, text in sorted(files.items()):
            z.writestr(f"{run}/{name}", text)
        if raw:
            z.writestr(f"{run}/lines.jsonl", raw)
        z.writestr(f"{run}/meta.json", json.dumps(meta, indent=2, ensure_ascii=False))
    return buf.getvalue()
