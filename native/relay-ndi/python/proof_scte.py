"""Prove that SCTE-104 cues sent as NDI video-frame metadata reach Relay through the real NDI runtime (issue 560): one
process sends NDI's documented <vancData> example on its pictures, this one receives and decodes it. Run from the repo root
with relay_ndi installed and the NDI runtime present:

    python native/relay-ndi/python/proof_scte.py

This proves the runtime carries and the decoder reads the metadata. It does not prove a real broadcast sender produces it.
"""
from __future__ import annotations

import subprocess
import sys
import time

import relay_ndi

NAME = "RELAY SCTE PROOF"
XML = ('<vancData version="1.0"><vancPacket did="97" sdid="1" line="9">lmlZT38BWHL0/ICA</vancPacket>'
       '<vancPacket did="65" sdid="7" line="10">CP//AB4AAQEAAgAAAQEBAA4BAAAAAwAAAAAAAAAAAA==</vancPacket></vancData>')


def send() -> None:
    tx = relay_ndi.Sender(NAME, 320, 180, 30.0, "BGRA")
    frame = bytes(320 * 180 * 4)
    t0 = time.monotonic()
    n = 0
    while time.monotonic() - t0 < 14:
        tx.send(frame, None, XML if n % 30 == 0 else None)       # a cue on one picture in thirty
        n += 1


def receive() -> int:
    for _ in range(12):
        found = [s["name"] for s in relay_ndi.find(1.0) if NAME in s["name"]]
        if found:
            break
    else:
        print("the sender never appeared")
        return 1
    rx = relay_ndi.Connection(found[0], False, 5.0)
    end, frames, cues = time.monotonic() + 6, 0, []
    while time.monotonic() < end:
        f = rx.receive(200)
        if f and "data" in f:
            frames += 1
            if f.get("frame_meta"):
                cues += relay_ndi.scte_cues(f["frame_meta"])
    rx.close()
    print(f"{frames} pictures, {len(cues)} cues; first: {cues[:1]}")
    return 0 if cues and cues[0]["action"] == "start" and cues[0]["event_id"] == 3 else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["--send"]:
        send()
        sys.exit(0)
    child = subprocess.Popen([sys.executable, __file__, "--send"])
    time.sleep(1.5)
    try:
        code = receive()
    finally:
        child.terminate()
    print("PASS" if code == 0 else "FAIL")
    sys.exit(code)
