"""Record what Python renders, so the Rust server can be checked against it (#654).

Runs the existing test scripts with Jinja2's `Template.render` wrapped. Every render writes one JSON file with the
template's name, the context it got and the HTML it produced, into `native/relay-server/tests/pages/` (one file per
distinct name and context). The Rust test `tests/pages.rs` renders the same context with minijinja and compares.

    .venv/bin/python tools/capture_pages.py                 # every tests/test_*.py
    .venv/bin/python tools/capture_pages.py test_crew.py    # just these

Contexts hold only what JSON can carry; a template given anything else (a set, an object) fails the capture loudly, so the
Rust side is never checked against a value it could not have received.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "native" / "relay-server" / "tests" / "pages"
ENV = "RELAY_CAPTURE_PAGES"


def _plain(value, path="context"):
    """The value as JSON would carry it, or a loud error naming where it could not."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _plain(v, f"{path}.{k}") for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v, f"{path}[]") for v in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        # An object a template reads attributes of (`user.name`): its fields are all Rust can be given.
        return {f.name: _plain(getattr(value, f.name), f"{path}.{f.name}") for f in dataclasses.fields(value)}
    raise TypeError(f"{path} is a {type(value).__name__}, which JSON cannot carry")


def _install(out: Path) -> None:
    import jinja2

    real = jinja2.Template.render
    real_get = jinja2.Environment.get_template
    out.mkdir(parents=True, exist_ok=True)
    loaded: list[str] = []

    def get_template(self, name, *args, **kwargs):
        loaded.append(str(name))
        template = real_get(self, name, *args, **kwargs)
        try:
            template._requested = str(name)        # a module's template knows itself as "join.html", not "crew/join.html"
        except AttributeError:
            pass
        return template

    jinja2.Environment.get_template = get_template

    def render(self, *args, **kwargs):
        start = len(loaded)
        html = real(self, *args, **kwargs)
        name = getattr(self, "_requested", None) or self.name or ""
        uses = sorted({n for n in loaded[start:] if not n.startswith("_")} | {name})
        del loaded[start:]
        if not name or name.startswith("_"):
            return html
        ctx = dict(*args, **kwargs)
        ctx.pop("request", None)
        try:
            ctx = _plain(ctx)
        except TypeError as e:
            sys.stderr.write(f"capture: {name}: {e}\n")
            return html
        body = json.dumps({"template": name, "uses": uses, "context": ctx, "html": html}, ensure_ascii=False, indent=1)
        key = hashlib.sha1(json.dumps([name, ctx], sort_keys=True).encode()).hexdigest()[:10]
        (out / f"{name.replace('/', '__')}.{key}.json").write_text(body, encoding="utf-8")
        return html

    jinja2.Template.render = render


def main(argv: list[str]) -> int:
    if os.environ.get(ENV):
        # Child: wrap render, then run the one test script as __main__.
        _install(Path(os.environ[ENV]))
        script = Path(argv[0])
        sys.path.insert(0, str(script.parent))
        sys.path.insert(0, str(ROOT))
        runpy.run_path(str(script), run_name="__main__")
        return 0
    names = argv or sorted(p.name for p in (ROOT / "tests").glob("test_*.py") if p.name != "test_browser.py")
    names = ["../tools/capture_pages_all.py" if n == "all" else n for n in names]
    if not argv:
        names.append("../tools/capture_pages_all.py")
    OUT.mkdir(parents=True, exist_ok=True)
    if not argv:
        for old in OUT.glob("*.json"):
            old.unlink()
    for name in names:
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(ROOT / "tests" / name)],
                              env={**os.environ, ENV: str(OUT)}, cwd=ROOT, capture_output=True, text=True, timeout=600)
        print(f"{'ok  ' if proc.returncode == 0 else 'FAIL'} {name}")
        if "capture:" in proc.stderr:
            print("\n".join(l for l in proc.stderr.splitlines() if l.startswith("capture:")))
    files = sorted(OUT.glob("*.json"))
    seen = sorted({json.loads(f.read_text())["template"] for f in files})
    print(f"{len(files)} renders of {len(seen)} templates")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
