#!/usr/bin/env python3
"""Entry point. Reads host/ports from config.json, then serves.

Two listening sockets, one app, one process: viewers get `host:port`, the
operator panel gets `admin_host:admin_port`. Admin routes are refused on the
viewer port (see `_split_admin_port` in app/main.py), so the link handed to a
room cannot reach the panel.

The two sockets no longer share a host. `host` is every interface, because the
room has to reach the viewer link. `admin_host` defaults to 127.0.0.1, because
the panel reads and writes the OpenAI key and the admin token and has no TLS of
its own -- it is reached through the Caddy front end running beside it in the
same container, which terminates HTTPS on port 443.

Set `admin_port` equal to `port` to go back to a single port; that is an
explicit choice to serve the panel wherever the viewer link is served, so the
one socket uses `host` and `admin_host` is ignored.
"""
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import config  # noqa: E402


def _listen(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError as exc:
        raise SystemExit(f"Cannot listen on {host}:{port} — {exc}") from exc
    sock.listen(2048)
    sock.set_inheritable(True)
    return sock


def _is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "::1", "localhost")


def main() -> None:
    import uvicorn

    config.ensure_file()
    cfg = config.load()
    host = cfg["host"]
    admin_host = cfg["admin_host"]
    port = int(cfg["port"])
    admin_port = int(cfg["admin_port"])

    sockets = [_listen(host, port)]
    print(f"Viewer pages  -> {host}:{port}")
    if admin_port != port:
        sockets.append(_listen(admin_host, admin_port))
        print(f"Operator panel -> {admin_host}:{admin_port}", end="")
        print("  (loopback only)" if _is_loopback(admin_host) else "  (ALL INTERFACES)")
    else:
        print(f"Operator panel -> {host}:{port}  (single-port mode)")

    # One worker, deliberately. Do NOT add uvicorn --workers / workers=nproc:
    # engine, hub and AudioCapture are in-process singletons owning one sound
    # card. N workers would mean N capture attempts on the same device and N
    # separate hub states, with viewers load-balanced randomly between them.
    server = uvicorn.Server(
        uvicorn.Config("app.main:app", log_level="info", access_log=False)
    )
    server.run(sockets=sockets)


if __name__ == "__main__":
    main()
