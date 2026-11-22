"""Local live meter viewer. Read-only toward the desk: hello + meter subscribe only."""
import sys, socket, time, json, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from app.modules.audio_mixers import ah
HOST = os.environ.get("AVANTIS_HOST", "192.168.0.10")   # set AVANTIS_HOST to the desk address
state = {"groups": {}, "frames": 0, "t": 0.0, "status": "connecting"}
pk = {}
lock = threading.Lock()

def frames(buf):
    """Length-based AHNet framing: F0 + 8 header bytes + len16 + payload + F7 (payload bytes may equal F7)."""
    while buf:
        if buf[0] == 0xE0:
            if len(buf) < 8: return
            yield bytes(buf[:8]); del buf[:8]; continue
        if buf[0] != 0xF0:
            del buf[0]; continue
        if len(buf) < 11: return
        end = 11 + ((buf[9] << 8) | buf[10])
        if len(buf) <= end: return
        if buf[end] != 0xF7:
            del buf[0]; continue
        yield bytes(buf[:end + 1]); del buf[:end + 1]

stats = {"ok": 0, "short": 0}
def reader():
    while True:
        try:
            s = socket.create_connection((HOST, 51321), timeout=3); s.settimeout(0.5)
            s.sendall(ah.ahnet_hello()); time.sleep(0.5)
            nm = b"Metering Sources"
            s.sendall(bytes.fromhex("f0000100000003000400") + bytes([len(nm) + 1]) + nm + b"\x00\xf7")
            for k in range(11):
                s.sendall(bytes([0xf0,0,1,3,0x50,0,3,0,6,0,0x0a,0,0,1,0x72,0x10,k,0x10,k+1,0,0,0xf7]))
            buf = bytearray(); last = time.time()
            with lock: state["status"] = "streaming"
            while True:
                try:
                    d = s.recv(65536)
                    if not d: break
                    buf += d
                except socket.timeout:
                    if time.time() - last > 5: break
                    continue
                for f in frames(buf):
                    if f[0] == 0xf0 and f[3:8].hex() == "0172035010":
                        ln = int.from_bytes(f[9:11], "big"); b = f[11:11+ln]
                        stats['ok' if len(b) == ln else 'short'] += 1
                        v = []
                        for i in range(ln // 2):
                            x = int.from_bytes(b[2*i:2*i+2], "big")
                            x = (x - 65536 if x >= 32768 else x) / 256
                            v.append(9 if x > -0.5 else round(x))
                        last = time.time()
                        with lock:
                            g = str(f[8]); state["groups"][g] = v; state["frames"] += 1; state["t"] = last
                            p = pk.get(g)
                            pk[g] = v if p is None or len(p) != len(v) else [max(a, b) if a != 9 and b != 9 else 9 for a, b in zip(p, v)]
            s.close()
        except OSError:
            with lock: state["status"] = "reconnecting"
            time.sleep(2)

names = {"names": {}, "colours": {}, "blocks": {}}
def fetch_names():
    """Read-only: SysEx name (01) and colour (04) asks for inputs 1-96 on the base MIDI channel (12)."""
    try:
        c = socket.create_connection((HOST, 51325), timeout=3); c.settimeout(0.3)
        for n in range(96):
            c.sendall(ah.SYSEX + bytes([11, 0x01, n, 0xF7])); c.sendall(ah.SYSEX + bytes([11, 0x04, n, 0xF7])); time.sleep(0.03)
        for n in range(10):
            c.sendall(ah.SYSEX + bytes([12, 0x01, n, 0xF7])); c.sendall(ah.SYSEX + bytes([13, 0x01, n, 0xF7])); time.sleep(0.03)
        for n in range(64, 76):
            c.sendall(ah.SYSEX + bytes([13, 0x01, n, 0xF7])); time.sleep(0.03)
        buf = bytearray(); end = time.time() + 3
        mixn = {}
        while time.time() < end:
            try:
                d = c.recv(65536)
                if not d: break
                buf += d
            except socket.timeout: pass
        for fr in ah.MidiParser().feed(bytes(buf)):
            if fr[0] == "sysex" and fr[1][:8] == ah.SYSEX and len(fr[1]) > 11 and fr[1][8] in (12, 13) and fr[1][9] == 0x02:
                mixn[(fr[1][8], fr[1][10])] = fr[1][11:-1].decode("ascii", "replace").rstrip("\0 ")
            if fr[0] == "sysex" and fr[1][:8] == ah.SYSEX and len(fr[1]) > 11 and fr[1][8] == 11:
                f = fr[1]; cmd, num, body = f[9], f[10], f[11:-1]
                if cmd == 0x02: names["names"][str(num)] = body.decode("ascii", "replace").rstrip("\0 ")
                elif cmd == 0x05 and body and body[0] < len(ah.COLOURS): names["colours"][str(num)] = ah.COLOURS[body[0]]
        c.close()
        b = names["blocks"]
        for n in range(10): b[str(n)] = "Group %d %s" % (n + 1, mixn.get((12, n), ""))
        for n in range(10): b[str(10 + n)] = "Aux %d %s" % (n + 1, mixn.get((13, n), ""))
        for k in range(12):
            nm = mixn.get((13, 64 + k), "") or "Stereo aux %d" % (k + 1)
            b[str(20 + 2 * k)] = nm + " L"; b[str(21 + 2 * k)] = nm + " R"
        b["44"] = "Main L"; b["45"] = "Main R"
    except OSError: pass
threading.Thread(target=fetch_names, daemon=True).start()

PAGE = open(os.path.join(os.path.dirname(__file__), "live.html")).read()
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith("/names"):
            body = json.dumps(names).encode(); ct = "application/json"
        elif self.path.startswith("/state"):
            with lock:
                out = dict(state); out["stats"] = dict(stats); out["groups"] = dict(pk) if pk else state["groups"]
                for g in list(pk): pk[g] = state["groups"].get(g, pk[g])
                body = json.dumps(out, separators=(",", ":")).encode()
            ct = "application/json"
        else:
            body = PAGE.encode(); ct = "text/html"
        self.send_response(200); self.send_header("Content-Type", ct); self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

threading.Thread(target=reader, daemon=True).start()
ThreadingHTTPServer(("127.0.0.1", 8765), H).serve_forever()
