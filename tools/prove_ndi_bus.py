"""Prove NDI sound on the lane bus against the real NDI runtime (#301): a real NDI audio source arrives as lane blocks
through the service and the bus input, and the bus output is received as an NDI audio source. Run from the repo root with
relay_ndi built and the NDI runtime present (NDI_RUNTIME_DIR_V6 if it is somewhere unusual):

    python tools/prove_ndi_bus.py
"""
import asyncio
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")
sys.path.insert(0, str(ROOT / 'tests'))
import _db
_db.start(); _db.reset()
import numpy as np, relay_ndi
from app import audio
from app.modules.ndi import bus, runtime, service as svc

def tone(n, start, db=-20.0):
    x = (10 ** (db / 20) * np.sin(2 * np.pi * 1000 * (np.arange(n) + start) / 48000)).astype(np.float32)
    return np.stack([x, x * 0.5], axis=1)

# a real NDI audio source, 2 channels
src = relay_ndi.AudioSink("BUS PROOF IN", 2, 48000)
stop = threading.Event()
def feed(sink):
    pos = 0
    while not stop.is_set():
        n = min(sink.free(), 480)
        if n: sink.write(tone(n, pos)); pos += n
        time.sleep(0.005)
threading.Thread(target=feed, args=(src,), daemon=True).start()
time.sleep(1.5)
name = next(s["name"] for s in relay_ndi.find(2.0) if "BUS PROOF IN" in s["name"])
print("found", name)

s = svc.service
asyncio.run(s.start())
inp = bus.NdiSource(s.hook_listen).lane_input()
blocks = []
info = inp.open(name, blocks.append)
print("input opened:", info.rate, info.device)
time.sleep(1.5)
data = np.concatenate(blocks)
peak = [20*np.log10(np.abs(data[:, c]).max()) for c in range(data.shape[1])]
print("lane blocks:", len(blocks), "frames", len(data), "peaks dBFS", [round(p, 1) for p in peak], "alive", inp.alive())

# the bus writing to an NDI audio output, received back through a plain connection
out = bus.NdiOutput(runtime.load).lane_sink("Bus Proof Out", 2)
def write_lane():
    pos = 0
    while not stop.is_set():
        n = min(out.free(), 480)
        if n: out.write(tone(n, pos, -12.0)); pos += n
        time.sleep(0.005)
threading.Thread(target=write_lane, daemon=True).start()
time.sleep(1.5)
oname = next(x["name"] for x in relay_ndi.find(2.0) if "Relay - Bus Proof Out" in x["name"])
rx = relay_ndi.Connection(oname, False, 5.0)
got, end, pk = 0, time.monotonic() + 3, -120
while time.monotonic() < end:
    f = rx.receive(200)
    if f and "audio" in f:
        got += 1; pk = max(pk, f["audio_db"])
rx.close()
print("sink output received", got, "chunks, peak", pk, "dBFS (sent -12.0)")
stop.set(); inp.close(); out.close(); src.close()
asyncio.run(s.stop())
ok = len(blocks) > 50 and abs(peak[0] + 20) < 1 and got > 50 and abs(pk + 12) < 1
print("PASS" if ok else "FAIL")
