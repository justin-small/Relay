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
    eq("slide text, lines kept", s.sections[0].slides[0].text, "Sample line 1\nSecond line 1")
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
    eq("sections", [(x.name, [sl.text for sl in x.slides]) for x in r.sections], [("Verse 1", ["One\nTwo", "Three\nFour"]), ("Chorus", ["Sing é"])])
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

with section("every feature of a song round-trips"):
    full = pf.Song(
        name="Everything", category="Worship", notes="Capo 2", music_key="Bb", original_key="G", album="Album",
        artist_credits="Band", ccli_number=42, show_ccli=True, background=(0.1, 0.2, 0.3), slide_show_seconds=8.0,
        transition=0.5, announcements=True, bible_reference="John 3:16-17", selected_arrangement="Short",
        sections=[
            pf.Section("Verse 1", hot_key="V", slides=[
                pf.Slide(text="Amazing grace", translation="Gracia sublime", label="Open", label_colour=(1.0, 0.5, 0.0),
                         notes="Softly", chords=[(0, "G"), (8, "D")], hot_key="1", advance_after=4.0, completion="next",
                         transition=1.5,
                         actions=[pf.SlideAction("macro", "Lights up", "11111111-1111-1111-1111-111111111111"),
                                  pf.SlideAction("look", "Worship look"), pf.SlideAction("timer", "Countdown", what="start"),
                                  pf.SlideAction("clear", what="messages"), pf.SlideAction("prop", "Lower third"),
                                  pf.SlideAction("message", "Welcome"), pf.SlideAction("stage", "Singers", screen="Stage 1"),
                                  pf.SlideAction("clear_group", "Everything off")]),
                pf.Slide(text="Disabled one", enabled=False, advance_after=2.0, completion="first")]),
            pf.Section("Chorus", slides=["Sing"])],
        arrangements=[("Long", ["Verse 1", "Chorus", "Verse 1"]), ("Short", ["Chorus"])])
    r = pf.parse_song(pf.build_song(full))
    for attr in ("category", "notes", "music_key", "original_key", "album", "artist_credits", "ccli_number", "show_ccli",
                 "background", "slide_show_seconds", "transition", "announcements", "bible_reference",
                 "selected_arrangement", "arrangements"):
        eq(attr, getattr(r, attr), getattr(full, attr))
    s1 = r.sections[0].slides[0]
    want = full.sections[0].slides[0]
    for attr in ("text", "translation", "label", "label_colour", "notes", "chords", "hot_key", "advance_after",
                 "completion", "transition"):
        eq("slide " + attr, getattr(s1, attr), getattr(want, attr))
    eq("slide actions", [(a.kind, a.target, a.what, a.screen) for a in s1.actions],
       [(a.kind, a.target, a.what, a.screen) for a in want.actions])
    eq("macro id kept", s1.actions[0].uuid, "11111111-1111-1111-1111-111111111111")
    eq("section hot key", r.sections[0].hot_key, "V")
    s2 = r.sections[0].slides[1]
    eq("disabled slide", (s2.enabled, s2.advance_after, s2.completion), (False, 2.0, "first"))
    pres = pf._classes().Presentation()
    pres.ParseFromString(pf.build_song(full))
    eq("two text elements on a translated slide",
       sum(1 for e in pres.cues[0].actions[0].slide.presentation.base_slide.elements if e.element.HasField("text")), 2)

with section("what Relay cannot set is refused"):
    def refused(label, make):
        try:
            make()
            check(label, False)
        except pf.FileError:
            check(label, True)

    refused("unknown action", lambda: pf.SlideAction("nope"))
    refused("unknown timer action", lambda: pf.SlideAction("timer", what="dance"))
    refused("unknown clear layer", lambda: pf.SlideAction("clear", what="sky"))
    refused("bad hot key", lambda: pf.build_song(pf.Song(name="X", sections=[pf.Section("V", [pf.Slide("x", hot_key="!!")])])))
    refused("bad key", lambda: pf.build_song(pf.Song(name="X", music_key="H", sections=[pf.Section("V", ["x"])])))
    refused("bad reference", lambda: pf.build_song(pf.Song(name="X", bible_reference="nonsense", sections=[pf.Section("V", ["x"])])))
    refused("arrangement naming a missing section", lambda: pf.build_song(pf.Song(name="X", arrangements=[("A", ["Nope"])], sections=[pf.Section("V", ["x"])])))
    refused("unknown selected arrangement", lambda: pf.build_song(pf.Song(name="X", selected_arrangement="Z", sections=[pf.Section("V", ["x"])])))
    refused("chord outside the text", lambda: pf.build_song(pf.Song(name="X", sections=[pf.Section("V", [pf.Slide("ab", chords=[(9, "G")])])])))
    refused("completion without a time", lambda: pf.build_song(pf.Song(name="X", sections=[pf.Section("V", [pf.Slide("x", completion="next")])])))

done()
