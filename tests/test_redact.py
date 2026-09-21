"""Blocklist redactor tests, including the split-delta cases that matter most.

Run: .venv/bin/python tests/test_redact.py
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.redact import Redactor

fails = []
def check(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else f"\n         got  {got!r}\n         want {want!r}"))
    if not ok: fails.append(label)

def stream(terms, deltas):
    """Feed deltas one at a time; return (visible_at_each_step, final_text)."""
    r = Redactor(terms)
    seen, out = [], ""
    for d in deltas:
        out += r.feed(d)
        seen.append(out)
    out += r.flush()
    return seen, out

print("\nwhole-string")
r = Redactor(["damn"])
check("removes the word", r.whole("Well damn, that is fast."), "Well, that is fast.")
check("case-insensitive", r.whole("DAMN it"), "it")
check("whole words only", Redactor(["ass"]).whole("a class assignment"), "a class assignment")
check("diacritic-insensitive", Redactor(["nino"]).whole("el niño corre"), "el corre")
check("accented term matches plain", Redactor(["niño"]).whole("el nino corre"), "el corre")
check("multi-word phrase", Redactor(["off the record"]).whole("This is off the record, ok?"), "This is, ok?")
check("phrase across hyphen", Redactor(["off the record"]).whole("say off-the-record now"), "say now")
check("empty list is pass-through", Redactor([]).whole("anything at all"), "anything at all")
check("removes every occurrence", Redactor(["x"]).whole("x and x and x"), "and and")
check("spanish target word", Redactor(["mierda"]).whole("¡Qué mierda de día!"), "¡Qué de día!")

print("\nstreaming — word split across deltas")
seen, final = stream(["damn"], ["Well ", "dam", "n", ", ok"])
check("never visible mid-stream", any("dam" in s for s in seen), False)
check("final text", final, "Well, ok")
print("       frames: " + " | ".join(repr(s) for s in seen))

seen, final = stream(["damn"], ["d", "a", "m", "n"])
check("char-by-char never leaks", any("dam" in s or "damn" in s for s in seen), False)
check("char-by-char final empty", final, "")

seen, final = stream(["damn"], ["dam", "age control"])
check("prefix that is NOT the term survives", final, "damage control")
check("held only while ambiguous", seen[0], "")

print("\nstreaming — latency: unrelated words must not be held")
seen, _ = stream(["damn"], ["Good ", "afternoon ", "everyone"])
check("first word out immediately", seen[0], "Good")
check("second word out immediately", seen[1], "Good afternoon")

print("\nstreaming — phrases")
seen, final = stream(["off the record"], ["This is ", "off ", "the ", "record", ", ok"])
check("phrase never partially visible", any("off" in s for s in seen), False)
check("phrase final", final, "This is, ok")
seen, final = stream(["off the record"], ["turn ", "off ", "the ", "lights"])
check("phrase prefix that diverges is released", final, "turn off the lights")

print("\nstreaming — spacing and punctuation")
_, final = stream(["damn"], ["hello ", "damn", " world"])
check("mid-sentence gap closed", final, "hello world")
_, final = stream(["damn"], ["damn", " world"])
check("leading removal", final, "world")
_, final = stream(["damn"], ["hello ", "damn"])
check("trailing removal", final, "hello")
_, final = stream(["damn"], ["hello ", "damn", "."])
check("punctuation not stranded", final, "hello.")

print("\nstreaming — equivalence with whole-string, random chunking")
terms = ["damn", "off the record", "niño", "hell"]
texts = [
    "Well damn, this is off the record and the niño ran.",
    "damn",
    "hell no, damn it, off the record please.",
    "nothing here is banned at all",
    "damnation is not damn",
]
bad = 0
random.seed(7)
for text in texts:
    want = Redactor(terms).whole(text)
    for _ in range(60):
        deltas, i = [], 0
        while i < len(text):
            n = random.randint(1, 5)
            deltas.append(text[i:i+n]); i += n
        _, got = stream(terms, deltas)
        if got.strip() != want.strip():
            bad += 1
            if bad == 1:
                print(f"         first mismatch on {text!r}\n         got  {got!r}\n         want {want!r}")
check("300 random chunkings match whole-string", bad, 0)
check("'damnation' is not redacted", Redactor(["damn"]).whole("damnation"), "damnation")

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("redactor: all checks passed")
