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

Relay has users with five ranked roles: member, viewer, operator, admin, owner. A member
never opens the admin panel (it lacks the core `admin.open`): it signs in to use the features it
is granted and lands on `/home`, which lists them. What a
role may do is a list of named permissions, and the server checks one on every admin
route. A module declares its own; core never names them.

**Declare them** in `Module.permissions`, as `Permission(id, label, group, min_role)`
from `app.users`:

- the id is `<module id>.<action>`, so it must start with the module's id (the registry refuses
  the module otherwise);
- every module declares at least `<id>.view` and `<id>.manage` (the test checks);
- `label` is the sentence shown on the Users page, `group` the heading it is listed under;
- `min_role` is the lowest role that has it, and every higher role has it too. Pick the role
  that should be allowed to do it: `member` for a feature people use outside the admin panel
  (with a `UserPage`, below), `viewer` for looking, `operator` for live actions during a
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

**Features outside the admin panel.** A `UserPage(title, path, permission, note)` in
`Module.user_pages` lists one of the module's own pages on the home page (`/home`) of a signed-in
user who holds `permission`, while the module is on. `path` must sit under the module's
`route_prefixes` and `permission` must be one the module declares (the registry refuses it
otherwise). The page checks the permission itself with `require(...)`, the same call as an admin
route, and should serve only on the operator (HTTPS) port, where the sign-in cookie lives.

**Hide controls** in the module's script with `window.relayCan("<id>.<action>")`, which is
true when the signed-in user holds the permission. It only tidies the screen: the server
enforces the permission whatever the page shows.

**Tables of its own.** A module that needs database tables calls
`db.migrate("<module id>", <its migrations directory>)` (from `app.db`) and puts `NNNN_name.sql`
files in that directory. Each file runs once, in order, and what has run is recorded under the
module's id, so its tables never collide with core's. `migrate` raises `db.Unavailable` while the
database is down; the module must cope with that.

## A port of its own

A module whose pages are opened by devices that should reach nothing else (signage on a TV) can ask for a plain-HTTP
port of its own with `Module.listeners`:

```python
from app.modules import Listener

listeners = (Listener(name="screens", label="signage screens", port_setting="port", default_port=8090,
                      prefixes=("/signage",)),)
```

At startup `run.py` opens one socket for each listener of a module that is on, at the port in the module's own
setting `port_setting` (or `default_port`). Only the module's routes under `prefixes` answer on that port, and
those routes answer on no other port. A port that cannot be opened, or that Relay already uses, is a notice on
the admin page; Relay starts anyway. A change of port, or switching the module on, takes effect at the next start,
so say so in the module's health. `serving.listener_bound(prefixes)` gives the port really bound. The prefixes must
be under the module's `route_prefixes`, and the default port 1024 or above.

## Audio sources and outputs

Core captures audio from an `AudioSource` and plays audio through an `AudioOutput` (`app/audio.py`); it
registers PortAudio for both. A module that brings audio in or out (a network stream, a network sender)
registers its own in `attach()` and removes it in `detach()`, so it exists only while the module is on:

```python
from app import audio

class Stream(audio.AudioSource):
    id, label = "mixer_stream", "Mixer network stream"      # lower_snake_case, start with the module id
    def open(self, selector, channel, deliver):             # start; call deliver(block) from any thread
        ...                                                  # block: mono float32 in -1..1 at info.rate
        return audio.SourceInfo(rate=48000, device={"label": "Mixer: ch 1", "channels": 1})
    def close(self): ...                                     # stop delivering; safe to call twice
    def alive(self): ...                                     # False when the input is lost (optional)

def attach(self): audio.register_source(Stream()); audio.register_output(Sender())
def detach(self): audio.unregister_source("mixer_stream"); audio.unregister_output("mixer_stream")
```

The operator picks the source in `audio_source` (default `"portaudio"`); `audio_device` and `input_channel`
are passed to the source's `open` as its selector and channel. Resampling to 24 kHz, the meter, silence
gating, reconnecting and the consumers stay in core's pump, so a source only delivers frames. `open` raises
`audio.SourceError` with a fixed sentence (nothing from the network or a library) when it cannot start. An
`AudioOutput.play(pcm, rate, selector)` takes mono 16-bit PCM, returns at once and raises nothing.

## Pages that need HTTPS

A module whose pages need a secure context (a service worker, the microphone, web push) lists their prefixes in
`Module.panel_prefixes`, each under `route_prefixes`. Those paths answer only on the HTTPS operator port; on the
plain-HTTP viewer port, and so through a remote access tunnel (which points at it), they answer 404. The crew app
(`app/modules/crew/`) is the example. A page that needs a sign-in uses `app.modules.page_gate(request, perm)`
(through `asyncio.to_thread`), which returns the signed-in user or the response to send instead (the sign-in form,
or "not allowed"); a route used by a browser that sends no cookie (a web app manifest, a service worker) is public
on purpose and must carry nothing private.

## Tabs on the crew page

The crew page (`/crew`, the `crew` module, #226) hosts the other crew features as tabs. A `CrewTab(id, title,
template, permission)` in `Module.crew_tabs` adds one for a signed-in user who holds `permission` (one the module
declares) while the module is on. The module's template in `templates/` is rendered inside the tab's panel and the
scripts in `Module.crew_scripts` (from the module's `static/`) are loaded on the page; the page fires a `crew:tab`
event on `document` when the tab changes. The routes a tab calls check the permission themselves. A tab that needs
the microphone lists the page in `Module.microphone_pages` (`("/crew",)`): only then, and only while that module is
on, is the page sent `microphone=(self)` instead of `microphone=()`. A tab that needs the phone's position (crew
check-in, #231) lists the page in `Module.geolocation_pages` the same way and is sent `geolocation=(self)`.

## The audit log

Every change made through a module's `/api/admin` route already goes in the audit log (#172),
with the person or API token that made it, once it succeeded: core's middleware writes it as
"`POST /api/admin/<your path>`". To say what happened in words, call `note` from `app.audit`
inside the route:

```python
from ... import audit

@router.post("/api/admin/mixer/scene", dependencies=[Depends(require("mixer.manage"))])
def recall_scene(request: Request, payload: dict):
    ...
    audit.note(request, "mixer.scene_recall", "Recalled a mixer scene", target=name)
```

For the module's own work with no request behind it (an overnight sync, a schedule firing), call
`audit.record("mixer.sync", "Synced the scene list")`; with no `actor` it is entered as Relay
itself.

- the action is `<module id>.<what>`; the log's Kind filter lists it under the module's id;
- `summary` is the sentence people read; `target` names what it acted on;
- `detail` is a small dict of plain values. Never put a password, token, key or request body in
  any field: the log is read by every Admin.

Neither call raises: a database that is down loses the entry and logs a warning.

## Per-user preferences

Each person's own choices (a layout, a default view, a language) are kept per user in the user
database (#173), so they follow the person to every device. A module keeps its own under its id
with `app.prefs`:

```python
from ... import prefs

cols = prefs.get(user.id, "columns", 2, scope="mixer")    # the default when unset
prefs.set(user.id, "columns", 3, scope="mixer")           # any plain JSON; None removes it
prefs.scope_prefs(user.id, "mixer")                       # {"columns": 3}
```

- `scope` is the module's id; core's own are under `"core"`;
- a name is lowercase, `a-z`, `0-9` and `_`, with up to two dots (`"layout.columns"`);
- a value is plain JSON of at most 4000 bytes, and a person keeps at most 200 preferences in all;
  a refusal raises `prefs.Invalid`, whose text is a fixed sentence;
- they are personal, not configuration: never put a secret in one, and they are not in the audit log;
- they go when the user is deleted.

A page reads and writes the signed-in user's own through `GET /api/admin/me/prefs` (every scope),
`PUT /api/admin/me/prefs/<scope>/<name>` with `{"value": ...}`, and `DELETE` on the same path. The
API accepts `core` and the id of any installed module. `GET /api/admin/me` carries them too, so a
page has them as it loads. Calls on the database raise `db.Unavailable` while it is down.

**A preference is untrusted input.** Any signed-in user, of any role, and any API token can store any
plain JSON under `core` or under any installed module's id, so `prefs.get(..., scope="mymodule")` returns
whatever that person sent through the API, not necessarily something your module wrote (#288):

- check the type and range before using a value: a number is a number within the limits you allow, a
  choice is one of the strings you expect; otherwise use your default;
- never let a preference pick a host, port, path, file name, URL or command argument, or anything else
  the person could not already choose for themselves;
- show one on a page with `textContent` (or other DOM APIs that make text), or in a template through
  Jinja's autoescaping; never through `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`,
  `|safe` or `Markup` (#289). An API token's holder could otherwise plant script that runs in the
  person's signed-in admin session. `tests/test_prefs_render.py` refuses a line that sends a preference
  to one of those; it reads line by line, so it cannot see a value passed through a variable first.

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
6. **Modules ship inside Relay itself** (the desktop app, or the checkout a headless service runs). Their code is
   never downloaded at run time; a module updates when Relay does. Bump the module's `version` when it changes.
   A module may fetch a third-party program it supervises (intercom's `livekit-server`) once, on first switch-on,
   pinned to one release and checked against a SHA-256 in the module's source before it is unpacked; after that
   it runs with no internet.
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
