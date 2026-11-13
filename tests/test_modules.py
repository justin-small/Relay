"""Modules (#157): every integration is a module that is switched on and off on its own.

This file is the rule's enforcement, run in CI on every pull request. It fails when:

* a core file (anything in app/ outside app/modules/<id>/) imports a module package;
* a new Python file is added to app/ outside app/modules/ without being listed as core here -- a new
  integration belongs in app/modules/<id>/ (docs/modules.md), so adding to CORE_FILES needs a reason;
* a core file imports a network client (an integration's tell) unless it is listed in CORE_NETWORK;
* a module's manifest is invalid, or it imports another module it does not name in `uses`;
* a module's route is outside its declared prefixes, or answers while the module is off;
* a core template or script names a module's route (the way into a module is its own pages);
* a module starts work while it is off.

Offline: no OpenAI, no hardware. Run: .venv/bin/python tests/test_modules.py
"""
import ast
import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402  (a throwaway SQLite file for the user system; must come before app.main)

_db.start()
_db.reset()

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from app import config
from app import modules as modmod
from app.main import app
from app.modules import Manifest, Module, modules as registry

APP = ROOT / "app"
MODULES = APP / "modules"

# Relay's core. A new file here is a new piece of core, not an integration: say why in the pull request.
CORE_FILES = {
    "__init__.py", "alerts.py", "audio.py", "config.py", "db.py", "demo.py", "engine.py", "exporting.py", "hub.py",
    "languages.py", "main.py", "netinfo.py", "overlays.py", "panelfw.py", "panels.py", "paths.py", "realtime.py", "recorder.py", "redact.py",
    "scheduler.py", "schedules.py", "texttranslate.py", "users.py",
}
# Network clients, and the core files allowed them: OpenAI (realtime, text translation) and the host's own
# address for the viewer links (main, netinfo).
NETWORK = {"socket", "ssl", "http.client", "urllib.request", "websockets", "httpx", "aiohttp", "requests",
           "zeroconf", "paho", "pyserial", "serial"}
CORE_NETWORK = {"main.py": {"socket"}, "netinfo.py": {"socket"}, "realtime.py": {"ssl", "websockets"}, "texttranslate.py": {"urllib.request"}}

fails = []


def check(label, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + label + ((" — " + str(extra)) if extra and not cond else ""))
    if not cond:
        fails.append(label)


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


def imports(path: Path) -> list[str]:
    """Every module a file imports, as an absolute dotted name ("app.modules.x.y", "socket")."""
    rel = path.relative_to(ROOT).with_suffix("")
    pkg = list(rel.parts[:-1]) if rel.name != "__init__" else list(rel.parts[:-1])
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg[: len(pkg) - (node.level - 1)] if node.level > 1 else pkg
                head = ".".join(base + ([node.module] if node.module else []))
            else:
                head = node.module or ""
            out.append(head)
            out += [f"{head}.{a.name}" for a in node.names]
    return out


def core_py_files() -> list[Path]:
    """Every .py in app/ that is not inside a module package (the registry, app/modules/__init__.py, is core)."""
    out = []
    for p in sorted(APP.rglob("*.py")):
        rel = p.relative_to(APP)
        if rel.parts[0] == "modules" and len(rel.parts) > 2:
            continue
        if "__pycache__" in rel.parts:
            continue
        out.append(p)
    return out


def module_ids() -> list[str]:
    return modmod.package_names()


# ------------------------------------------------------------------ core never imports a module
print("core stays free of modules")
ids = module_ids()
for path in core_py_files():
    rel = path.relative_to(APP).as_posix()
    bad = sorted({i for i in imports(path) if any(i == f"app.modules.{m}" or i.startswith(f"app.modules.{m}.")
                                                 for m in ids)})
    check(f"{rel} imports no module package", not bad, bad)

top = {p.name for p in APP.glob("*.py")}
unknown = sorted(top - CORE_FILES)
check("every Python file in app/ is listed core (a new integration goes in app/modules/<id>/, see "
      "docs/modules.md)", not unknown, unknown)
stray = sorted(p.relative_to(APP).as_posix() for p in APP.rglob("*.py")
               if p.parent != APP and p.relative_to(APP).parts[0] != "modules" and "__pycache__" not in p.parts)
check("no Python packages in app/ outside app/modules/", not stray, stray)

for name in sorted(CORE_FILES & top):
    used = {i for i in imports(APP / name) if i in NETWORK or i.split(".")[0] in NETWORK}
    used = {i for i in used if i in NETWORK}
    extra = sorted(used - CORE_NETWORK.get(name, set()))
    check(f"{name} opens no network connection of its own (integrations go in modules)", not extra, extra)

# ------------------------------------------------------------------ each real module keeps the contract
print("module contract")
config.ensure_file()
config.save({"port": 8000, "admin_port": 8001})
admin = _db.client(app, base_url="http://relay.local:8001")
viewer = TestClient(app, base_url="http://relay.local:8000")
check("logged in to the admin page", 'id="page-modules"' in admin.get("/admin").text)

core_web = ""
for p in list((APP / "templates").glob("*.html")) + list((APP / "static").glob("*.js")):
    core_web += p.read_text(encoding="utf-8")

for mid in ids:
    mod = registry.get(mid)
    eq(f"{mid}: manifest is valid", modmod.validate(mod, mid), [])
    mine = MODULES / mid
    for path in sorted(mine.rglob("*.py")):
        rel = path.relative_to(MODULES).as_posix()
        others = sorted({i.split(".")[2] for i in imports(path)
                         if i.startswith("app.modules.") and len(i.split(".")) > 2 and i.split(".")[2] in ids}
                        - {mid} - set(mod.manifest.uses))
        check(f"{rel} imports only the modules its manifest uses", not others, others)
    paths = [r.path for router in mod.routers() for r in router.routes]
    outside = [p for p in paths if not any(p.startswith(pre) for pre in mod.route_prefixes)]
    check(f"{mid}: every route is under its declared prefixes", not outside, outside)
    named = sorted(pre for pre in mod.route_prefixes if pre in core_web)
    check(f"{mid}: no core template or script names its routes", not named, named)
    check(f"{mid}: settings key is not a core config key", mod.manifest.settings_key not in
          {"modules", "panels", "alerts", "schedules", "recording", "realtime", "targets"})

    # Permissions: every module declares view and manage, and every admin route asks for one of its own.
    declared = {perm.id for perm in mod.permissions}
    check(f"{mid}: declares {mid}.view and {mid}.manage", {f"{mid}.view", f"{mid}.manage"} <= declared, sorted(declared))
    unguarded = []
    for router in mod.routers():
        for r in router.routes:
            if not r.path.startswith(("/api/admin", "/admin")):
                continue          # public streams, viewer pages and panel routes carry their own rules
            asks = [d.call.permission for d in r.dependant.dependencies if hasattr(d.call, "permission")]
            if len(asks) != 1 or asks[0] not in declared or not asks[0].startswith(mid + "."):
                unguarded.append((sorted(r.methods), r.path, asks))
    check(f"{mid}: every admin route asks for one of the module's own permissions (never require_admin or a core one)",
          not unguarded, unguarded[:5])

    # A page is in the nav only for a user who holds its permission.
    class Holds:
        def __init__(self, *perms):
            self.perms = set(perms)

        def can(self, perm):
            return perm in self.perms

    async def nav_for(user):
        await registry.set_enabled(mid, True)
        try:
            return [pg["id"] for groups in registry.admin_nav(user).values() for pg in groups]
        finally:
            await registry.set_enabled(mid, False)
    for pg in mod.admin_pages:
        need = pg.permission or f"{mid}.view"
        check(f"{mid}: page {pg.id} needs a permission the module (or core) declares",
              need in declared or need.startswith(mid + "."), need)
        check(f"{mid}: page {pg.id} is absent from the nav for a user without {need}",
              pg.id not in asyncio.run(nav_for(Holds("modules.manage", "settings.view"))))
        check(f"{mid}: page {pg.id} is in the nav for a user with {need}", pg.id in asyncio.run(nav_for(Holds(need))))

    # Off: every route is gone, and nothing runs.
    asyncio.run(registry.set_enabled(mid, False))
    check(f"{mid}: not running while off", not registry.running(mid))
    for router in mod.routers():
        for r in router.routes:
            path = re.sub(r"\{[^}]+\}", "x", r.path)
            for method in sorted(getattr(r, "methods", None) or {"GET"}):
                if method == "HEAD":
                    continue
                client = admin if path.startswith(("/api/admin", "/admin")) else viewer
                got = client.request(method, path).status_code
                check(f"{mid}: {method} {r.path} answers 404 while off", got == 404, got)
    check(f"{mid}: no admin page while off",
          all(f'data-page="{p.id}"' not in admin.get("/admin").text for p in mod.admin_pages))
    check(f"{mid}: adds nothing to the status while off", not (set(mod.status()) & set(registry.status())))

    # On: its pages are rendered, then it is switched off again.
    async def on_then_off():
        await registry.set_enabled(mid, True)
        page = admin.get("/admin").text
        await registry.set_enabled(mid, False)
        return page
    page = asyncio.run(on_then_off())
    check(f"{mid}: admin pages, nav links and scripts rendered while on",
          all(f'data-page="{p.id}"' in page and f'href="#{p.id}"' in page for p in mod.admin_pages)
          and all(f"/static/modules/{mid}/{s}" in page for s in mod.admin_scripts))

# ------------------------------------------------------------------ the registry, with a module made up here
print("registry")


class Fake(Module):
    manifest = Manifest(id="fake", name="Fake", version="1.0.0", summary="A module for this test.",
                        settings_key="fake_settings")
    defaults = {"host": "", "secret": ""}
    route_prefixes = ("/api/fake",)

    def __init__(self):
        self.started = 0
        self.stopped = 0

    def public(self, settings):
        return {"host": settings["host"], "connected": bool(settings["secret"])}

    def configured(self, settings):
        return bool(settings.get("host"))

    def routers(self):
        r = APIRouter()

        @r.get("/api/fake/hello")
        async def hello():
            return {"hi": True}
        return [r]

    async def start(self):
        self.started += 1

    async def stop(self):
        self.stopped += 1

    def status(self):
        return {"fake": {"up": True}}


fake = Fake()
eq("a valid module validates", modmod.validate(fake, "fake")[:1], [])  # its templates/static are not on disk
eq("the id must match the package", modmod.validate(fake, "other")[0],
   "other: manifest id 'fake' must equal the package name")
check("a non-Module is refused", modmod.validate(object(), "x"))

saved = dict(registry._modules)
registry._modules = {**saved, "fake": fake}
try:
    # Upgrade: no switch saved -> on only if it was set up.
    cfg = {"fake_settings": {"host": "10.0.0.5", "secret": "s"}}
    registry.clean_config(cfg)
    eq("set up before modules: starts on", cfg["modules"]["fake"], {"enabled": True})
    cfg = {"fake_settings": {}}
    registry.clean_config(cfg)
    eq("never set up: starts off", cfg["modules"]["fake"], {"enabled": False})
    eq("defaults fill the settings", cfg["fake_settings"], {"host": "", "secret": ""})
    cfg = {"fake_settings": {"host": "10.0.0.5"}, "modules": {"fake": {"enabled": False}}}
    registry.clean_config(cfg)
    eq("a saved switch wins over being set up", cfg["modules"]["fake"], {"enabled": False})
    cfg = {"fake_settings": {"host": "h", "secret": "s"}}
    registry.clean_config(cfg)
    registry.redact_config(cfg)
    eq("public() strips the secret", cfg["fake_settings"], {"host": "h", "connected": True})

    # settle() writes the worked-out switch once, so configuring the integration later flips nothing.
    config.save({"fake_settings": {"host": "", "secret": ""}})
    registry.settle()
    eq("settle writes the switch", config.read_raw()["modules"]["fake"], {"enabled": False})
    config.save({"fake_settings": {"host": "10.0.0.9"}})
    eq("set up later: still off until switched on", registry.enabled("fake"), False)

    # Switching on and off starts and stops it, and its routes follow.
    probe = FastAPI()
    for router in fake.routers():
        probe.include_router(router, dependencies=[Depends(registry.require_enabled("fake"))])
    client = TestClient(probe)
    eq("off: route answers 404", client.get("/api/fake/hello").status_code, 404)
    eq("off: nothing in the status", "fake" in registry.status(), False)
    asyncio.run(registry.set_enabled("fake", True))
    eq("on: started once", fake.started, 1)
    eq("on: route answers", client.get("/api/fake/hello").json(), {"hi": True})
    eq("on: status merged", registry.status().get("fake"), {"up": True})
    asyncio.run(registry.start_enabled())
    eq("starting again does not start it twice", fake.started, 1)
    asyncio.run(registry.set_enabled("fake", False))
    eq("off: stopped", fake.stopped, 1)
    eq("off: route gone again", client.get("/api/fake/hello").status_code, 404)

    # The admin API.
    r = admin.get("/api/admin/modules")
    eq("modules list answers", r.status_code, 200)
    row = next(m for m in r.json()["modules"] if m["id"] == "fake")
    eq("listed with its switch and health", (row["enabled"], row["health"]["state"]), (False, "idle"))
    eq("refuses a non-boolean switch", admin.post("/api/admin/modules/fake", json={"enabled": "yes"}).status_code, 400)
    eq("unknown module is 404", admin.post("/api/admin/modules/nope", json={"enabled": True}).status_code, 404)
    eq("test refused while off", admin.post("/api/admin/modules/fake/test").status_code, 409)
    eq("switch on over the API", admin.post("/api/admin/modules/fake", json={"enabled": True}).json()["enabled"], True)
    eq("no test defined: says so", admin.post("/api/admin/modules/fake/test").json()["ok"], False)
    eq("viewer port cannot reach the modules API", viewer.get("/api/admin/modules").status_code, 404)
    asyncio.run(registry.set_enabled("fake", False))
finally:
    registry._modules = saved

if fails:
    print(f"\n{len(fails)} FAILED")
    sys.exit(1)
print("\nall passed")
