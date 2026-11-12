"""Spike #179: start a frozen Relay, time it to the first 200 on /, report size. Usage: smoke.py <exe> <dist-path> <label>"""
import os, subprocess, sys, tempfile, time, urllib.request, pathlib

exe, dist, label = sys.argv[1:4]
p = pathlib.Path(dist)
size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size
print(f"## {label}: size {size/1e6:.1f} MB")
for run in (1, 2):
    env = dict(os.environ, RELAY_CONFIG=os.path.join(tempfile.mkdtemp(), "config.json"))
    t0 = time.time()
    proc = subprocess.Popen([exe], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    code = None
    while time.time() - t0 < 60:
        try:
            code = urllib.request.urlopen("http://127.0.0.1:8000/", timeout=2).status
            break
        except Exception:
            time.sleep(0.1)
    print(f"## {label}: run {run}: / -> {code} after {time.time()-t0:.2f}s")
    proc.terminate()
    try:
        out = proc.communicate(timeout=10)[0].decode(errors="replace")
    except Exception:
        proc.kill(); out = ""
    if code != 200:
        print(out[-2000:]); sys.exit(1)
