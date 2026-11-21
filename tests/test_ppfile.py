"""ProPresenter song files (#497): a song ProPresenter exported (ProPresenter 22, lyrics replaced) is read the way the
program shows it, a song Relay builds reads back the same, and what is not a song is refused.
Run: python tests/test_ppfile.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from app.modules.propresenter import ppfile as pf  # noqa: E402
from _harness import check, done, eq, section  # noqa: E402

EXPORTED = (HERE / "fixtures" / "pp" / "exported-song.pro").read_bytes()

with section("a song ProPresenter wrote"):
    s = pf.parse_song(EXPORTED)
    eq("name", s.name, "Exported sample")
    eq("sections in order", [x.name for x in s.sections], ["Chorus", "Bridge"])
    eq("slides per section", [len(x.slides) for x in s.sections], [2, 3])
    eq("slide text, lines kept", s.sections[0].slides[0], "Sample line 1\nSecond line 1")
    check("group colour read", s.sections[0].colour == (1.0, 0.0, 0.0))
    check("lyrics() joins every section", "Bridge" in s.lyrics() and "Sample line 5" in s.lyrics())

with section("rtf text"):
    eq("escapes", pf.rtf_to_text(rb"{\rtf1\ansi{\fonttbl\f0 Arial;}\pard A\'e9 b\line c\par d \u233? e}"), "Aé b\nc\nd é e")
    rtf = pf.text_to_rtf(b"{\\rtf1\\strokec0 old}", "a{b}\\c\nd é")
    eq("text_to_rtf round-trips braces, backslash, accents", pf.rtf_to_text(rtf), "a{b}\\c\nd é")

with section("a song Relay builds"):
    song = pf.Song(name="Built song", ccli_number=7654321, author="A. Writer", publisher="Pub", copyright_year=2001,
                   sections=[pf.Section("Verse 1", ["One\nTwo", "Three\nFour"]), pf.Section("Chorus", ["Sing é"])],
                   arrangements=[("Default", ["Verse 1", "Chorus", "Verse 1"])])
    data = pf.build_song(song)
    r = pf.parse_song(data)
    eq("name", r.name, "Built song")
    eq("sections", [(x.name, x.slides) for x in r.sections], [("Verse 1", ["One\nTwo", "Three\nFour"]), ("Chorus", ["Sing é"])])
    eq("arrangement", r.arrangements, [("Default", ["Verse 1", "Chorus", "Verse 1"])])
    eq("CCLI", (r.ccli_number, r.author, r.publisher, r.copyright_year), (7654321, "A. Writer", "Pub", 2001))
    eq("every build gets its own identity", pf.parse_song(pf.build_song(song)).uuid != r.uuid, True)
    C = pf._classes()
    pres = C.Presentation()
    pres.ParseFromString(data)
    ids = [c.uuid.string for c in pres.cues]
    eq("no two slides share an identity", len(set(ids)), len(ids))
    eq("nor two actions", len({a.uuid.string for c in pres.cues for a in c.actions}), sum(len(c.actions) for c in pres.cues))

with section("refusals"):
    for bad, why in ((b"not a protobuf at all \xff\xff", "garbage"), (b"", "empty")):
        try:
            pf.parse_song(bad)
            check(f"{why} refused", False)
        except pf.FileError as exc:
            eq(f"{why}: fixed sentence", str(exc), "This is not a ProPresenter song file.")
    for bad_song, why in ((pf.Song(name="", sections=[pf.Section("V", ["x"])]), "no name"),
                          (pf.Song(name="X", sections=[pf.Section("V", [])]), "no slides")):
        try:
            pf.build_song(bad_song)
            check(f"{why} refused", False)
        except pf.FileError:
            check(f"{why} refused", True)

done()
