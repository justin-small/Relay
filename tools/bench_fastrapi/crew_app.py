"""Relay's real crew router (app/modules/crew/routes.py, 66 routes, the most FastAPI of any module) served by
FastAPI+uvicorn or by fastrapi, over the same config, database and handlers.

    python crew_app.py fastapi|fastrapi PORT

The fastrapi side runs the same source with only its import lines swapped (see `ported_source`). Sign-in is a
stand-in dependency on both sides (it sets request.state.user to a real Owner), so the numbers compare the
frameworks and the handlers, not the cookie and token check in app/main.py.
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp(prefix="relay-crew-bench-")
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_CERT_DIR"] = os.path.join(TMP, "certs")
os.environ["RELAY_DATABASE"] = os.path.join(TMP, "relay.db")
os.environ["RELAY_ANNOUNCE"] = "0"

kind, port = sys.argv[1], int(sys.argv[2])

from app import config, users  # noqa: E402
from app import modules as modules_pkg  # noqa: E402

from app import db  # noqa: E402
db.init()
config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"crew": {"enabled": True}}})
users.SCRYPT_N = 2 ** 10
USER = users.get_by_username("owner") or users.create("owner", "Owner", "owner", "correct horse", temporary=False)
USER = users.get_by_username("owner")
from app.modules.crew import people  # noqa: E402
people.ready()


def make_require(Request, HTTPException):
    def require(perm):
        def dep(request: Request):
            request.state.user = USER
            return USER
        dep.permission = perm
        return dep
    return require


def ported_source() -> str:
    """crew/routes.py with its framework imports pointed at fastrapi; nothing else changes."""
    src = (ROOT / "app/modules/crew/routes.py").read_text(encoding="utf-8")
    old = ("from fastapi import APIRouter, Depends, HTTPException, Request\n"
           "from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response\n")
    new = ("from fastrapi import APIRouter, Depends, HTTPException, Request\n"
           "from fastrapi.responses import FileResponse, HTMLResponse, JSONResponse, Response\n")
    assert old in src
    return src.replace(old, new)


if kind == "fastapi":
    import uvicorn
    from fastapi import FastAPI, HTTPException, Request
    modules_pkg.require = make_require(Request, HTTPException)
    from app.modules.crew import routes
    app = FastAPI(docs_url=None, redoc_url=None)
    app.include_router(routes.router)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
else:
    from fastrapi import FastrAPI, HTTPException, Request
    modules_pkg.require = make_require(Request, HTTPException)
    mod = types.ModuleType("app.modules.crew._routes_fastrapi")
    mod.__package__ = "app.modules.crew"
    mod.__file__ = str(ROOT / "app/modules/crew/routes.py")
    exec(compile(ported_source(), mod.__file__, "exec"), mod.__dict__)
    app = FastrAPI(docs_url=None, redoc_url=None)
    app.include_router(mod.router)
    app.serve("127.0.0.1", port)
