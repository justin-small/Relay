"""Relay's real app/main.py (all modules) served by FastAPI+uvicorn or by fastrapi, with no file changed.

    python main_app.py fastapi|fastrapi PORT [--list]

For fastrapi the name `fastapi` is pointed at fastrapi before app.main imports it (the "change the import" claim,
tested without editing the repo). Config and database are throwaway; no account exists, so `x-admin-token: t0ken`
acts as Owner. RELAY_DEMO=1 keeps the engine from touching real audio or devices.
"""
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp(prefix="relay-main-bench-")
os.environ.update({"RELAY_CONFIG": os.path.join(TMP, "config.json"), "RELAY_CERT_DIR": os.path.join(TMP, "certs"),
                   "RELAY_DATABASE": os.path.join(TMP, "relay.db"), "RELAY_ANNOUNCE": "0", "RELAY_DEMO": "1"})
kind, port = sys.argv[1], int(sys.argv[2])

if kind == "fastrapi":
    import fastrapi
    sys.modules["fastapi"] = fastrapi
    for sub in ("responses", "staticfiles", "templating", "testclient", "exceptions", "security", "middleware",
                "requests", "websockets", "routing", "datastructures", "encoders", "status", "param_functions"):
        try:
            sys.modules["fastapi." + sub] = importlib.import_module("fastrapi." + sub)
        except ImportError:
            pass

from app import config  # noqa: E402

config.ensure_file()
ids = [p.name for p in (ROOT / "app/modules").iterdir() if (p / "__init__.py").exists()]
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {i: {"enabled": True} for i in ids}})
if kind == "fastrapi":
    # Edits fastrapi forces, made to the source in memory (the repo file is untouched) so the run can go on and
    # find the next break. Each one is a finding.
    import re
    import types
    src = (ROOT / "app/main.py").read_text(encoding="utf-8")
    src, n1 = re.subn(r"class RevalidatedStaticFiles\(StaticFiles\):.*?return response\n",
                      "RevalidatedStaticFiles = StaticFiles  # PATCH 1: fastrapi's StaticFiles cannot be subclassed\n",
                      src, count=1, flags=re.S)
    src, n2 = re.subn(r"directory=(MODULES_DIR / _mid / \"static\"|BASE / \"static\")", r"directory=str(\1)", src)
    print("patches applied:", n1, n2, "(expected 1 and 2)", flush=True)
    main = types.ModuleType("app.main")
    main.__package__, main.__file__ = "app", str(ROOT / "app/main.py")
    sys.modules["app.main"] = main
    exec(compile(src, main.__file__, "exec"), main.__dict__)
else:
    import app.main as main  # noqa: E402

app = main.app
print("framework:", type(app).__module__, type(app).__name__, flush=True)
if "--list" in sys.argv:
    out = []
    for r in app.routes:
        out.append({"path": getattr(r, "path", None), "methods": sorted(getattr(r, "methods", None) or []),
                    "kind": type(r).__name__})
    print(json.dumps(out))
    sys.exit(0)
if kind == "fastapi":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
else:
    app.serve("127.0.0.1", port)
