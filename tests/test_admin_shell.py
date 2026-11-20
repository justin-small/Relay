"""The operator panel's shell (#434): a person's look is read through an allow-list and written onto <html> as
attributes, the page keeps the hooks the scripts and the browser test use, and a hostile stored value never
reaches the markup.

Offline, throwaway SQLite (tests/_db.py). Run: .venv/bin/python tests/test_admin_shell.py
"""
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

from app import config, db, prefs, users  # noqa: E402
from app.main import app  # noqa: E402

users.SCRYPT_N = 2 ** 10
ADMIN = "http://relay.local:8001"
from _harness import check, done, eq, section  # noqa: E402

config.ensure_file()
config.save({"port": 8000, "admin_port": 8001, "blocklist_file": str(Path(os.environ["RELAY_CONFIG"]).parent / "blocklist.txt")})
db.init()

with section("the allow-list"):
    eq("nothing stored is the defaults", prefs.shell_view({}), prefs.SHELL_DEFAULTS)
    eq("not a dict is the defaults", prefs.shell_view("x"), prefs.SHELL_DEFAULTS)
    got = prefs.shell_view({"ui_preset": "brutalist", "ui_mode": "system", "ui_sidebar": "floating", "ui_collapse": "offcanvas",
                            "ui_layout": "full", "ui_navbar": "scroll", "ui_font": "mono", "ui_collapsed": True})
    eq("every allowed value is kept", got, {"ui_preset": "brutalist", "ui_mode": "system", "ui_sidebar": "floating",
                                            "ui_collapse": "offcanvas", "ui_layout": "full", "ui_navbar": "scroll",
                                            "ui_font": "mono", "ui_collapsed": True})
    bad = prefs.shell_view({"ui_preset": '"><script>x</script>', "ui_mode": 3, "ui_sidebar": ["inset"], "ui_collapse": None,
                            "ui_layout": {"a": 1}, "ui_navbar": "STICKY", "ui_font": "comic", "ui_collapsed": "yes"})
    eq("anything else falls back to the default", bad, prefs.SHELL_DEFAULTS)

with section("the page"):
    c = _db.client(app, "owner", base_url=ADMIN)
    owner = users.get_by_username("owner")
    html = c.get("/admin").text
    for want in ('data-preset="relay"', 'data-theme="dark"', 'data-sb="inset"', 'data-collapse="icon"', 'data-layout="centered"',
                 'data-navbar="sticky"', 'href="/static/admin.css', 'src="/static/shell.js'):
        check(f"first paint carries {want}", want in html, want)
    for ident in ("nav", "streamPill", "masterDot", "pillTxt", "modulePills", "viewerCount", "acctLink", "alert", "drawer",
                  "drawerBg", "startBtn", "stopBtn", "goBtn", "crumb", "lookBtn", "pal", "toast"):
        check(f"id {ident} is still there", re.search(rf'id="{ident}"', html) is not None)
    check("nav links keep data-page", len(re.findall(r'<a href="#\w+" data-page="\w+"', html)) >= 10)
    check("the inline stylesheet is gone", "<style>" not in html)

    prefs.set(owner.id, "ui_preset", "tangerine")
    prefs.set(owner.id, "ui_mode", "light")
    prefs.set(owner.id, "ui_collapsed", True)
    html = c.get("/admin").text
    for want in ('data-preset="tangerine"', 'data-theme="light"', 'class="cl"'):
        check(f"a stored choice reaches the page: {want}", want in html, want)

    prefs.set(owner.id, "ui_preset", '" onload="alert(1)')
    prefs.set(owner.id, "ui_font", "<img src=x onerror=alert(1)>")
    html = c.get("/admin").text
    markup = re.sub(r"window\.RELAY_ME = .*", "", html)      # the signed-in user's data, as escaped JSON in a script
    check("a hostile preset is not in the markup", "onload=" not in markup and "alert(1)" not in markup and "<img src=x" not in markup)
    check("…and is escaped inside the JSON", "<img" not in html)
    check("…and the default is used", 'data-preset="relay"' in html)

    prefs.set(owner.id, "ui_mode", "system")
    html = c.get("/admin").text
    check("system mode starts dark and lets the page script resolve it", 'data-mode="system"' in html and 'data-theme="dark"' in html)

done()
