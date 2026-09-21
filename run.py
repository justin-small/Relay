#!/usr/bin/env python3
"""Entry point. Reads host/ports from config.json, then serves.

Two listening sockets, one app, one process: viewers get `port`, the operator
panel gets `admin_port`. Admin routes are refused on the viewer port (see
`_split_admin_port` in app/main.py), so the link handed to a room cannot reach
the panel. Set `admin_port` equal to `port` to go back to a single port.
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


def main() -> None:
    import uvicorn

    config.ensure_file()
    cfg = config.load()
    host = cfg.get("host", "0.0.0.0")
    ports = list(dict.fromkeys([int(cfg["port"]), int(cfg["admin_port"])]))
    sockets = [_listen(host, p) for p in ports]

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
