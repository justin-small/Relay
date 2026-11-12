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
- a module's route is outside its `route_prefixes`, or answers while the module is off;
- a module adds an admin page or status while it is off;
- a core template or script names a module's route.

## Upgrades

A module with no switch saved yet starts **on** if its integration was already set up (`configured()`),
and **off** otherwise. The switch is written to config.json at startup, so setting the integration up later
never turns the module on by itself.
