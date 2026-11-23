"""Reading a lyric sheet into a song (#633): sections, slides, chords, the translation line, repeats, a PDF, and the same
slides cut again at N lines. Run: python tests/test_ppsongparse.py
"""
import base64
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from app.modules.propresenter import ppsongparse as sp, ppsongs, ppfile  # noqa: E402
from _harness import check, done, eq, section  # noqa: E402

SHEET = """Verse 1
G        C
I love You, Lord
/ Te amo, Señor
For Your mercy never fails me
All my days, I've been held

Chorus
[G]Cause You are good
All the time

Chorus
[G]Cause You are good
All the time
"""

with section("section headings"):
    eq("verse number", sp.is_header("Verse 1"), "Verse 1")
    eq("bracketed and shouted", sp.is_header("[CHORUS 2]:"), "Chorus 2")
    eq("short form", sp.is_header("v2"), "Verse 2")
    eq("a chord is not a heading", sp.is_header("[C]"), "")
    eq("a lyric is not a heading", sp.is_header("Verse of the day we sing"), "")

with section("chords"):
    eq("inline chords leave the words", sp.split_chords("I [G]love [C/E]You"), ("I love You", [(2, "G"), (7, "C/E")]))
    eq("and go back", sp.with_chords("I love You", [(2, "G"), (7, "C/E")]), "I [G]love [C/E]You")

with section("a pasted sheet"):
    r = sp.parse_text(SHEET, lines=2)
    eq("sections", [s["name"] for s in r["sections"]], ["Verse 1", "Chorus"])
    v = r["sections"][0]["slides"]
    eq("two lines a slide", [len(x["text"].split("\n")) for x in v], [2, 1])
    check("the chord above the first line is inline", v[0]["text"].startswith("[G]I love "))
    eq("translation line kept with its lyric", v[0]["translation"], "Te amo, Señor")
    eq("a repeated chorus is kept once", len(r["sections"]), 2)
    eq("and repeats in the order", r["arrangement"], ["Verse 1", "Chorus", "Chorus"])
    check("and says so", any("again" in w for w in r["warnings"]))

with section("options"):
    one = sp.parse_text("Verse 1\nA line\nAnother\n\nChorus\nThird", headers=False)
    eq("no headings: one section", len(one["sections"]), 1)
    eq("a blank line can stop splitting", len(sp.parse_text("one\ntwo\n\nthree", lines=0, blank_splits=False)["sections"][0]["slides"]), 1)
    eq("a blank line splits", len(sp.parse_text("one\ntwo\n\nthree", lines=0)["sections"][0]["slides"]), 2)
    eq("slash read as lyric when off", sp.parse_text("one\n/ two", lines=0, translation=False)["sections"][0]["slides"][0]["text"], "one\n/ two")
    for bad in ("", "   "):
        try:
            sp.parse_text(bad)
            check("nothing to read is refused: " + repr(bad), False)
        except sp.ParseError as exc:
            check("nothing to read is refused: " + repr(bad), bool(exc.message))

with section("a heading with no lines"):
    r = sp.parse_text("Verse 1\n\n")
    check("is kept, with a note", any("no lines" in w for w in r["warnings"]))

with section("cutting again"):
    secs = [{"name": "Verse 1", "slides": [{"text": "a\nb\nc\nd\ne", "translation": "A\nB\nC\nD\nE", "hot_key": "1", "label": "V"}]}]
    out = sp.resplit(secs, 2)[0]["slides"]
    eq("slides", [x["text"] for x in out], ["a\nb", "c\nd", "e"])
    eq("translation stays with its line", [x["translation"] for x in out], ["A\nB", "C\nD", "E"])
    eq("the first slide keeps its hot key and label", (out[0]["hot_key"], out[0]["label"]), ("1", "V"))
    eq("zero is one slide", len(sp.resplit(secs, 0)[0]["slides"]), 1)

with section("a PDF"):
    body = b"BT /F1 12 Tf 72 700 Td (Verse 1) Tj 0 -16 Td (I love You, Lord) Tj ET"
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(body)).encode() + b" >>\nstream\n" + body + b"\nendstream", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    pdf, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(pdf))
        pdf += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    x = len(pdf)
    pdf += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    pdf += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF".encode()
    r = ppsongs.parse_text({"pdf_b64": base64.b64encode(pdf).decode()})
    eq("sections from a PDF", [s["name"] for s in r["sections"]], ["Verse 1"])
    eq("its text", r["sections"][0]["slides"][0]["text"], "I love You, Lord")
    for label, data in (("not a PDF", b"hello"), ("empty", b"")):
        try:
            ppsongs.parse_text({"pdf_b64": base64.b64encode(data).decode()})
            check(label + " is refused", False)
        except ppsongs.SongError as exc:
            check(label + " is refused with a sentence", bool(exc.message))

with section("a song with inline chords is built"):
    song = ppsongs.song_from_payload({"name": "T", "sections": [{"name": "Verse 1", "slides": [{"text": "I [G]love You"}]}]})
    s = song.sections[0].slides[0]
    eq("text without markers", s.text, "I love You")
    eq("chord positions", s.chords, [(2, "G")])
    back = ppsongs.read_song(base64.b64encode(ppfile.build_song(song)).decode(), True)
    eq("an opened file shows its chords inline", back["sections"][0]["slides"][0]["text"], "I [G]love You")

with section("bad requests"):
    for body in (None, {"text": "x", "lines": "many"}, {"text": "x", "lines": 99}):
        try:
            ppsongs.parse_text(body)
            check("refused: " + repr(body), False)
        except ppsongs.SongError:
            check("refused: " + repr(body), True)
    try:
        ppsongs.split_song({"lines": 2})
        check("split needs sections", False)
    except ppsongs.SongError:
        check("split needs sections", True)

done()
