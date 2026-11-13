#!/usr/bin/env python3
"""Entry point. Reads host/ports from config.json, then serves.

Two listening sockets, one app, one process, no proxy:

  viewers  plain HTTP on `host:port`       (every interface, port 80; the link handed to a room)
  panel    HTTPS on `admin_host:admin_port` (every interface, port 443; Relay's own certificate)

The panel carries the OpenAI key and every account, so it is never served in cleartext: its socket always
speaks TLS (app/tls.py), and admin routes are refused on the viewer port (`_split_admin_port` in
app/main.py), so the link handed to a room cannot reach the panel.

If 80 or 443 cannot be opened (in use, or not permitted) Relay falls back to 8080 / 8443, says so on the
admin page, and every link and the mDNS record carry the port actually in use (app/serving.py).

`python run.py --fingerprint` prints the panel certificate's fingerprint and exits.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import config, serving, tls  # noqa: E402


def _print_identity(info, viewer_port: int, admin_port: int, fqdn: str) -> None:
    primary = fqdn or (info.ips[0] if info.ips else "127.0.0.1")
    print()
    print(f"  Certificate names : {', '.join(info.dns + info.ips)}")
    print(f"  SHA-256           : {info.fingerprint}")
    print()
    print(f"  Panel   : {serving.link('https', primary, admin_port, '/admin')}")
    print(f"  Viewers : {serving.link('http', info.ips[0] if info.ips else '127.0.0.1', viewer_port)}")
    print()
    print("  The certificate is self-signed, so the browser will warn once. Check the fingerprint it")
    print("  shows against the SHA-256 above before you accept it.")
    print()


def main() -> None:
    config.ensure_file()
    cfg = config.load()

    if "--fingerprint" in sys.argv[1:]:
        info = tls.panel.start(cfg["admin_fqdn"])
        print(info.fingerprint)
        return

    host, admin_host = cfg["host"], cfg["admin_host"]
    viewer_sock, viewer_port, note1 = serving.listen(host, int(cfg["port"]), "viewer pages")
    try:
        admin_sock, admin_port, note2 = serving.listen(admin_host, int(cfg["admin_port"]), "operator panel")
    except SystemExit:
        viewer_sock.close()
        raise
    notes = [n for n in (note1, note2) if n]
    serving.publish(viewer_port, admin_port, notes)

    info = tls.panel.start(cfg["admin_fqdn"])
    print(f"Viewer pages   -> http://{host}:{viewer_port}   (plain HTTP)")
    print(f"Operator panel -> https://{admin_host}:{admin_port}   (HTTPS, self-signed)")
    for n in notes:
        print(f"  {n}")
    if tls.panel.changed:
        print("  The panel certificate was replaced, so its fingerprint changed. Trust the new one below.")
    _print_identity(info, viewer_port, admin_port, cfg["admin_fqdn"])

    # One worker, deliberately. Do NOT add uvicorn --workers / workers=nproc:
    # engine, hub and AudioCapture are in-process singletons owning one sound
    # card. N workers would mean N capture attempts on the same device and N
    # separate hub states, with viewers load-balanced randomly between them.
    server = serving.make_server("app.main:app", [viewer_sock], [admin_sock], tls.panel.context)
    server.run(sockets=[viewer_sock, admin_sock])


if __name__ == "__main__":
    main()
