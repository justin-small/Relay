"""The pages in a real browser (Playwright, headless Chromium), used as real people would: the viewer pages with
no account, then one person of each role (Member, Viewer, Operator, Admin, Owner) signing in through the sign-in
form. For each role the operator panel shows exactly the pages that role's permissions allow (worked out from
app/users.py, not written out here), the Start button only where it may start captions, and every page the role
can open runs with no script error, no console error and no failed request -- a 403 there means the page asked
for something its user may not have. A Member is sent home instead of into the panel.
This is the only test that runs the templates and app/static/ JavaScript together.

Skipped when Playwright is not installed. To run it:

    pip install playwright==1.65.2 && python -m playwright install chromium-headless-shell
    python tests/test_browser.py
"""
import os
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, done, eq, section, skip, tmpdir  # noqa: E402

try:
    from playwright.sync_api import Error as PlaywrightError, sync_playwright
except ImportError:
    skip("Playwright is not installed (pip install playwright, then python -m playwright install "
         "chromium-headless-shell)")

TMP = tmpdir()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ.pop("RELAY_DB_BACKUPS", None)
import _db  # noqa: E402

_db.start()
_db.reset()

import uvicorn  # noqa: E402

from app import config, users  # noqa: E402
from app.main import app  # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


PORT = free_port()
config.ensure_file()
# The browser talks to the operator port (plain HTTP here); the viewer port is another, unused one.
config.save({"admin_token": "t0ken", "port": free_port(), "admin_port": PORT})
users.SCRYPT_N = 2 ** 10
PASSWORD = "correct horse"
for role in users.ROLES:                    # one person of each role, named after it
    users.create(role, role.title(), role, PASSWORD, temporary=False)

server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning", lifespan="on"))
threading.Thread(target=server.run, daemon=True).start()
deadline = time.monotonic() + 20
while not server.started and time.monotonic() < deadline:
    time.sleep(0.05)
if not server.started:
    check("the server starts", False)
    done()
BASE = f"http://127.0.0.1:{PORT}"

# Viewer pages anyone may open.
PUBLIC = ["/", "/transcription", "/translation", "/both", "/present", "/status"]
# The operator panel's own pages (data-page in app/templates/admin.html) and the permission each needs.
# Module pages come and go with their modules and are not listed.
NAV = {"health": "session.view", "audio": "audio.manage", "targets": "targets.manage", "panels": "panels.view",
       "alerts": "alerts.view", "urls": "session.view", "overlay": "overlays.view", "recordings": "recordings.view",
       "schedules": "schedules.view", "blocklist": "blocklist.manage", "credentials": "credentials.manage",
       "appearance": "settings.manage", "users": "users.view", "audit": "audit.view", "modules": "modules.view"}
# Long-lived streams are cut when a page is closed; that is not a failure.
STREAMS = ("/stream", "/events", "/live/stream", "/ws")


def watch(page):
    seen = {"errors": [], "console": [], "failed": []}
    page.on("pageerror", lambda e: seen["errors"].append(str(e)))
    page.on("console", lambda m: seen["console"].append(m.text) if m.type == "error" else None)
    page.on("requestfailed", lambda r: None if any(s in r.url for s in STREAMS)
            else seen["failed"].append(f"{r.url} ({r.failure})"))
    page.on("response", lambda r: seen["failed"].append(f"{r.url} {r.status}")
            if r.status >= 400 and not r.url.endswith("/favicon.ico") else None)
    return seen


def visit(context, path):
    page = context.new_page()
    seen = watch(page)
    r = page.goto(BASE + path, wait_until="load")
    page.wait_for_timeout(500)              # let the page's scripts run their first fetches
    return page, r, seen


def clean(path, r, seen):
    eq(f"{path}: loads", r.status if r else None, 200)
    eq(f"{path}: no script error", seen["errors"], [])
    eq(f"{path}: no console error", seen["console"], [])
    eq(f"{path}: no failed request", seen["failed"], [])


try:
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except PlaywrightError as e:
            server.should_exit = True
            skip(f"Playwright's Chromium is not installed ({str(e).splitlines()[0]})")
        context = browser.new_context()

        with section("viewer pages"):
            for path in PUBLIC:
                page, r, seen = visit(context, path)
                clean(path, r, seen)
                page.close()

        for role in users.ROLES:
            allowed = users.role_permissions(role)
            ctx = browser.new_context()
            with section(f"{role}: signing in"):
                page, r, seen = visit(ctx, "/admin")
                check(f"{role}: the operator panel asks for a sign-in",
                      page.locator("input[type=password]").count() > 0, page.url)
                page.fill("input[name=username]", role)
                page.fill("input[name=password]", PASSWORD)
                page.locator("form").filter(has=page.locator("input[name=password]")).locator(
                    "button, input[type=submit]").first.click()
                page.wait_for_load_state("load")
                page.wait_for_timeout(500)
                eq(f"{role}: no script error while signing in", seen["errors"], [])
                page.close()

            if "admin.open" not in allowed:
                with section(f"{role}: kept out of the operator panel"):
                    page, r, seen = visit(ctx, "/admin")
                    check(f"{role}: /admin sends them home", page.url.rstrip("/").endswith("/home"), page.url)
                    clean(f"{role} /home", r, seen)
                    check(f"{role}: no operator navigation on their page",
                          page.locator("a[data-page]").count() == 0)
                    page.close()
                ctx.close()
                continue

            with section(f"{role}: the operator panel"):
                page, r, seen = visit(ctx, "/admin")
                clean(f"{role} /admin", r, seen)
                eq(f"{role}: the page knows the role's permissions",
                   sorted(page.evaluate("window.RELAY_ME.permissions")), sorted(allowed))
                shown = set(page.eval_on_selector_all("nav a[data-page]", "as => as.map(a => a.dataset.page)"))
                want = {name for name, perm in NAV.items() if perm in allowed}
                eq(f"{role}: exactly the pages the role may open are in the menu", sorted(shown & set(NAV)),
                   sorted(want))
                eq(f"{role}: the Start button only for those who may start captions",
                   page.locator("#startBtn").count() > 0, "session.control" in allowed)

            with section(f"{role}: every page in the menu"):
                for name in sorted(shown):
                    seen["errors"].clear(); seen["console"].clear(); seen["failed"].clear()
                    page.click(f"nav a[data-page='{name}']")
                    page.wait_for_timeout(400)
                    eq(f"{role} #{name}: no script error", seen["errors"], [])
                    eq(f"{role} #{name}: no console error", seen["console"], [])
                    eq(f"{role} #{name}: no refused or failed request", seen["failed"], [])
                page.close()
            ctx.close()

        browser.close()
finally:
    server.should_exit = True

done()
