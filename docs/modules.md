# Modules: how integrations are built

**Rule: every integration with another product is a module that can be switched on and off.**
ProPresenter, Planning Center, mixing consoles, wireless mics, lighting, streaming encoders — anything
that talks to something outside Relay goes in `app/modules/<id>/`, never into core. A church that uses
none of them runs none of their code.

The rule is enforced. `tests/test_modules.py` runs on every pull request (`.github/workflows/tests.yml`)
and fails the build when it is broken. See [What the test checks](#what-the-test-checks).

## What counts as core

Captions, translation, the audio path, viewers, panels and their firmware protocol, alerts, schedules,
recordings and the admin panel itself. Core is the list `CORE_FILES` in `tests/test_modules.py`. Adding a
file to that list is a design decision: say why in the pull request.

## Where a module lives

```
app/modules/<id>/
  __init__.py        defines MODULE = <YourModule>(), a subclass of app.modules.Module
  *.py               the integration itself: client, follower, routes
  templates/         its admin pages (included in admin.html only while it is on)
  static/            its admin scripts and styles, served at /static/modules/<id>/
```

`<id>` is lower_snake_case and is also the module's key under `"modules"` in config.json.

## The interface

```python
from app.modules import AdminPage, Health, Manifest, Module
from app.users import Permission

class Mixer(Module):
    manifest = Manifest(
        id="mixer", name="Mixing console", version="1.0.0",
        summary="Follows the console's scenes and mutes.",
        settings_key="mixer",          # config.json key for its own settings
        guide="/docs/mixer",           # setup guide, shown next to its settings
        uses=(),                       # other modules it does more with, if they are on
    )
    defaults = {"host": "", "port": 0}
    admin_pages = (AdminPage(id="mixer", title="Mixing console", template="mixer.html", group="Automation"),)
    admin_scripts = ("mixer.js",)
    route_prefixes = ("/api/admin/mixer",)
    permissions = (
        Permission("mixer.view", "See the console's scenes and mutes", "Mixing console", "viewer"),
        Permission("mixer.manage", "Change the console setup", "Mixing console", "admin"),
    )

    def clean(self, raw): ...              # normalise stored settings (runs on every config load)
    def public(self, settings): ...        # what the admin browser may see: strip secrets
    def configured(self, settings): ...    # was it set up before modules? decides its first switch
    def routers(self): ...                 # FastAPI routers; mounted behind the on/off switch
    async def start(self): ...             # start background work; set any hooks into core
    async def stop(self): ...              # stop it all and clear those hooks
    def health(self): ...                  # Health("ok" | "warn" | "error" | "idle", "detail")
    async def test(self): ...              # {"ok": bool, "detail": str} for "Test connection"
    def status(self): ...                  # keys merged into the status panels and admin read
    def stream(self, name, request, params): ...   # a /stream?stream=<name> it owns, else None

MODULE = Mixer()
```

What the registry (`app/modules/__init__.py`) does with it:

| While the module is **on**                          | While it is **off**                         |
|-----------------------------------------------------|---------------------------------------------|
| `start()` at startup and when switched on            | `stop()` when switched off; never started   |
| its routes answer                                   | every one of its routes answers 404         |
| its admin pages, nav links and scripts are rendered | none of them are on the admin page          |
| `status()` and `stream()` reach panels and viewers  | it adds nothing                             |

The operator switches modules on the admin page's **Modules** page, which also shows each module's
health, its setup guide, a link to its settings and its connection test.

## Permissions

Relay has users with four ranked roles: viewer, operator, admin, owner. What a
role may do is a list of named permissions, and the server checks one on every admin
route. A module declares its own; core never names them.

**Declare them** in `Module.permissions`, as `Permission(id, label, group, min_role)`
from `app.users`:

- the id is `<module id>.<action>`, so it must start with the module's id (the registry refuses
  the module otherwise);
- every module declares at least `<id>.view` and `<id>.manage` (the test checks);
- `label` is the sentence shown on the Users page, `group` the heading it is listed under;
- `min_role` is the lowest role that has it, and every higher role has it too. Pick the role
  that should be allowed to do it: `viewer` for looking, `operator` for live actions during a
  service (ProPresenter's `propresenter.control`), `admin` for setup, `owner` only for what an
  admin must not touch.

**Guard every admin route** with `require("<id>.<action>")` from `app.modules`, as a
dependency: `@router.post("/api/admin/mixer/mute", dependencies=[Depends(require("mixer.manage"))])`.
It answers 401 when nobody is signed in and 403 when the role lacks the permission, like core's
routes. Never use plain `require_admin`: it means the core permission `modules.manage`, says
nothing about your module, and the test fails a route that uses it. Each route under `/api/admin`
or `/admin` asks for exactly one of the module's own permissions. Routes that are public on
purpose (viewer streams, panel routes) carry their own rules instead.

**Pages.** An `AdminPage` shows in the nav only for a user who holds its `permission`; empty
means `<module id>.view`. `admin_nav(user)` does the filtering, and a module's scripts are
loaded only for a user who can see at least one of its pages.

**Hide controls** in the module's script with `window.relayCan("<id>.<action>")`, which is
true when the signed-in user holds the permission. It only tidies the screen: the server
enforces the permission whatever the page shows.

**Tables of its own.** A module that needs database tables calls
`db.migrate("<module id>", <its migrations directory>)` (from `app.db`) and puts `NNNN_name.sql`
files in that directory. Each file runs once, in order, and what has run is recorded under the
module's id, so its tables never collide with core's. `migrate` raises `db.Unavailable` while the
database is down; the module must cope with that.

## Livestream status

A module that watches a livestream (OBS, Resi, YouTube, ...) publishes into the shared stream status in core
(`app/streamstatus.py`), which the status view (`/status`, and a panel on the Status feed) shows:

```python
from ...streamstatus import streams

streams.publish("obs", label="OBS Studio", on_air=True, health="ok", live_since=started, viewers=None,
                detail="Connected to 10.0.0.5")   # `detail` is for the admin page only; never sent to the status view
```

The source is the module's id. Publish from the module's own background work when what it knows changes, and call
`streams.clear("<id>")` in `detach()`, so a module switched off leaves nothing on the screen. The status view is on
the viewer port with no login: put reasons, addresses and keys in `detail`, never in `label`.

## Rules

1. **Imports go one way.** A module may import core (`app.config`, `app.hub`, `app.engine`, `app.panels`,
   `app.alerts`, ...). Core never imports a module: it reaches modules only through the registry's hooks.
   If core needs to call into a module, add a hook (a callable core holds, set in `start()` and cleared in
   `stop()`), or a method on `Module` that the registry calls for every module that is on.
2. **A module that works with another names it in `uses`** and copes with it being off. It may import only
   the modules it names.
3. **Its routes are under its `route_prefixes`.** Core templates and scripts never name them; the way in
   is the module's own admin pages.
4. **Settings stay under its own config key**, cleaned by `clean()`, with secrets stripped by `public()`.
5. **No background work outside `start()`**, and `stop()` undoes all of it, so switching off at run time
   leaves nothing running.
6. **Modules ship inside the signed Docker image.** Nothing is downloaded at run time; a module updates
   when Relay does. Bump the module's `version` when it changes.
7. **Tests**: add `tests/test_<id>.py`. The contract checks in `tests/test_modules.py` cover every
   module automatically.

## What the test checks

`tests/test_modules.py` fails when:

- a core file imports a module package;
- a new `.py` file appears in `app/` outside `app/modules/` without being listed in `CORE_FILES`;
- a core file imports a network client (`socket`, `http.client`, `urllib.request`, `websockets`, ...)
  that is not allowed for it in `CORE_NETWORK`;
- a module's manifest is invalid, its templates or scripts are missing, or it imports a module it does not
  name in `uses`;
- a module does not declare `<id>.view` and `<id>.manage`, an admin route does not ask for one of
  the module's own permissions, or a page needs a permission nobody declares;
- a module's route is outside its `route_prefixes`, or answers while the module is off;
- a module adds an admin page or status while it is off;
- a core template or script names a module's route.

## Upgrades

A module with no switch saved yet starts **on** if its integration was already set up (`configured()`),
and **off** otherwise. The switch is written to config.json at startup, so setting the integration up later
never turns the module on by itself.
