"""Which FastAPI names Relay imports does fastrapi provide?  Run with the interpreter that has fastrapi:
    python compat.py            (reads app/ for the names, tries each one on fastrapi)
"""
import ast
import importlib
import sys
from collections import Counter
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
used = Counter()
for path in APP.rglob("*.py"):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in ("fastapi", "starlette"):
            for a in node.names:
                used[(node.module.replace("starlette", "fastapi"), a.name)] += 1

# Names fastrapi has, but not at the same import path (a rename in every file, not a gap).
MOVED = {"FastAPI": ("fastrapi", "FastrAPI"), "Request": ("fastrapi.request", "Request"),
         "WebSocket": ("fastrapi.websocket", "WebSocket")}
missing = []
for (mod, name), n in sorted(used.items(), key=lambda kv: -kv[1]):
    target = mod.replace("fastapi", "fastrapi", 1)
    if name in MOVED:
        target, name = MOVED[name]
    try:
        ok = hasattr(importlib.import_module(target), name)
    except Exception:
        ok = False
    if not ok:
        missing.append((n, mod, name))
total = sum(used.values())
print(f"Relay imports {len(used)} distinct names ({total} import sites) from fastapi/starlette")
print(f"fastrapi {importlib.import_module('importlib.metadata').version('fastrapi')} lacks {len(missing)}:")
for n, mod, name in missing:
    print(f"  {n:3d}x  {mod}.{name}")
sys.exit(1 if missing else 0)
