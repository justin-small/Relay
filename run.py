#!/usr/bin/env python3
"""Entry point. Reads host/ports from config.json, then serves.

Two listening sockets, one app, one process, no proxy:

  viewers  plain HTTP on `host:port`       (every interface, port 80; the link handed to a room)
  panel    HTTPS on `admin_host:admin_port` (every interface, port 443; Relay's own certificate)

The panel carries the OpenAI key and every account, so it is never served in cleartext: its socket always
speaks TLS (app/tls.py), and admin routes are refused on the viewer port (`_split_admin_port` in
app/main.py), so the link handed to a room cannot reach the panel.

Which addresses each socket binds follows the traffic routes (app/netroute.py): with none set, every interface as
below; with "crew" or "viewers" routed to a network port, that port's address and 127.0.0.1 only (app/serving.py).

Modules may add plain-HTTP ports of their own (`Listener`, app/modules/__init__.py), such as signage screens on
8090: one is opened for each listener of a module that is on, and only that module's routes answer on it. One that
cannot be opened is a notice on the admin page, not a reason to stop.

If 80 or 443 cannot be opened (in use, or not permitted) Relay falls back to 8080 / 8443, says so on the
admin page, and every link and the mDNS record carry the port actually in use (app/serving.py).

`python run.py --fingerprint` prints the panel certificate's fingerprint and exits.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import config, serving, sidecar, tls  # noqa: E402


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


def _hosts(group) -> str:
    return ", ".join(group.socks)


def _module_listeners(host: str, cfg: dict, taken: set[int]):
    """Open the plain-HTTP ports of the modules that are on. Returns (groups, lines to print)."""
    from app.modules import modules

    groups, lines, published = [], [], {}
    for mod, lst, port in modules.listeners(cfg):
        what = f"{mod.manifest.name} ({lst.label})"
        if port in taken:
            serving.add_notice(f"{what} is set to port {port}, which Relay already uses, so it is not served. "
                               f"Choose another port in its settings and restart Relay.")
            continue
        try:
            group = serving.listen_group(f"{mod.manifest.id}.{lst.name}", what, lst.traffic, host, port)
        except SystemExit as exc:
            serving.add_notice(f"{exc} Choose another port in its settings and restart Relay.")
            continue
        taken.add(group.port)
        groups.append(group)
        published[group.port] = lst.prefixes
        lines.append(f"{what} -> http://{_hosts(group)} port {group.port}   (plain HTTP)")
    serving.publish_listeners(published)
    return groups, lines


def main() -> None:
    config.ensure_file()
    cfg = config.load()

    if "--fingerprint" in sys.argv[1:]:
        info = tls.panel.start(cfg["admin_fqdn"])
        print(info.fingerprint)
        return

    host, admin_host = cfg["host"], cfg["admin_host"]
    viewers = serving.listen_group("viewers", "viewer pages", "viewers", host, int(cfg["port"]))
    try:
        panel = serving.listen_group("panel", "operator panel", "crew", admin_host, int(cfg["admin_port"]), secure=True)
    except SystemExit:
        for sock in viewers.socks.values():
            sock.close()
        raise
    viewer_port, admin_port = viewers.port, panel.port
    serving.publish(viewer_port, admin_port, [])
    extra_groups, extra_lines = _module_listeners(host, cfg, {viewer_port, admin_port})
    serving.publish_groups([viewers, panel, *extra_groups])

    info = tls.panel.start(cfg["admin_fqdn"])
    print(f"Viewer pages   -> http://{_hosts(viewers)} port {viewer_port}   (plain HTTP)")
    print(f"Operator panel -> https://{_hosts(panel)} port {admin_port}   (HTTPS, self-signed)")
    for line in extra_lines:
        print(line)
    for n in serving.notices():
        print(f"  {n}")
    if tls.panel.changed:
        print("  The panel certificate was replaced, so its fingerprint changed. Trust the new one below.")
    _print_identity(info, viewer_port, admin_port, cfg["admin_fqdn"])

    # One worker, deliberately. Do NOT add uvicorn --workers / workers=nproc:
    # engine, hub and AudioCapture are in-process singletons owning one sound
    # card. N workers would mean N capture attempts on the same device and N
    # separate hub states, with viewers load-balanced randomly between them.
    def ready() -> None:
        sidecar.emit("ready", viewer_port=viewer_port, admin_port=admin_port, fingerprint=info.fingerprint,
                     version=os.environ.get("RELAY_VERSION") or "dev", pid=os.getpid(), ips=list(info.ips))

    all_groups = serving.groups()
    server = serving.make_server("app.main:app", [], [], tls.panel.context, on_ready=ready, groups=all_groups)
    if sidecar.enabled():
        # The desktop shell owns this process: `quit` or its pipe closing is a graceful stop (app/sidecar.py).
        sidecar.watch_parent(lambda: setattr(server, "should_exit", True))
    server.run(sockets=[s for g in all_groups for s in g.socks.values()])


if __name__ == "__main__":
    main()
