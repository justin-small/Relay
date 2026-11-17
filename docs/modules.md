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

    secret_settings = ("password",)        # settings config.json keeps encrypted (app/secretbox.py)
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

## Secrets

A setting that is a password, token or key goes in `secret_settings`. config.json keeps it encrypted with the
machine key (`app/secretbox.py`, #278); in memory, `clean()` and the module see plain text, and `public()` must
still strip it. A secret a module keeps elsewhere (a file, a database row) is sealed with
`secretbox.seal(text, "<module id>:<what>")` and opened with `secretbox.unseal(value, same purpose)`, which
returns None when this computer's key no longer opens it. Never write a secret to a file another program reads:
pass it in that program's environment, as the intercom does for LiveKit.

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

`traffic` names the class whose network port the listener uses (default `"viewers"`; the crew join page uses
`"crew"`, and a module may name one of its own classes). Unrouted, it listens as before. Routed, it listens only on
that port's address and 127.0.0.1, and moves by itself when that address changes. For links and QR codes use
`netroute.link_addresses(traffic)`, never `netinfo.lan_ips()`.

## Network traffic

A machine running Relay often has several network ports: the church Wi-Fi, a crew network, a Dante or NDI
network. A module that talks to devices declares each kind of traffic it sends, and the operator chooses which
port carries it (`app/netroute.py`). The operator's side, with a typical cabling layout and switch settings, is
the guide's [Using more than one network port](guide/src/36-network-ports.md) chapter.

A module that talks to anything outside Relay does four things:

1. **Declares** each kind of traffic in `Manifest.traffic`, with an id that is the module's id or starts with
   `"<module id>."`.
2. **Asks** `netroute.resolve(class_id)` where it goes, and does not start a strict class that is not available.
3. **Connects** only through the `app/netsock.py` helpers, passing the class id.
4. **Reconnects** in `network_changed` when the port goes away, comes back or is re-chosen.

`tests/test_modules.py` checks 1 and 3 for every module.

```python
from app.modules import Manifest, Traffic

manifest = Manifest(
    id="dante", ...,
    traffic=(Traffic("dante", "Dante control and discovery", media=True),
             Traffic("dante.audio", "Dante audio", media=True, exclusive=True)),
)
```

- Each id is the module's id or starts with `"<module id>."`, like permissions. Core has `viewers`, `crew` and
  `discovery`.
- `media=True` (audio, video): when the chosen port is missing or down the class does **not** run, and the
  module's health shows a warning naming the port. Core adds that warning; the module writes nothing. Other
  classes use the system's default network instead. The operator can change either per class.
- `exclusive=True`: the class needs a port no other exclusive class is on.

Ask where to send, never read `config["network"]`:

```python
from app import netroute

r = netroute.resolve("dante")      # r.address, r.port, r.available, r.reason, r.fallback
if not r.available:
    ...                            # do not start; r.reason is a sentence for the module's page
```

A class with no route follows the system default, as before routes existed. When a class's port goes away, comes
back, changes address or is re-chosen, core calls `network_changed(resolved)` on the running module with every
one of its classes resolved again. It runs on the event loop: start work with asyncio, never block.

Routes are chosen from the ports Relay has seen (`known_ports` in config.json), never typed in. A route to a port
Relay has never seen is dropped with a notice when config loads.

Open sockets through `app/netsock.py`, never `socket` directly. Binding to the port's address alone picks only the
source address, and the OS may still send through another port. Each helper takes a class id (or a `Resolved`),
pins the socket to the port (`SO_BINDTODEVICE` on Linux, `IP_BOUND_IF` on macOS, `IP_UNICAST_IF` on Windows), and
gives an ordinary socket when the class is not routed:

```python
from app import netsock

reader, writer = await netsock.open_tcp("propresenter", host, port)          # or connect_tcp(...) -> socket
transport, proto = await netsock.udp_endpoint("blackmagic", AtemProtocol)          # or udp_socket(...) -> socket
sock = netsock.multicast_socket("aes67", "239.69.1.1", 5004)                 # joined on the port's address
server = await netsock.start_server("signage", handle, 8090)                 # or listen_tcp(...) -> socket

conn = netsock.http_connection("propresenter", host, port, timeout=5)        # http.client, HTTPS with tls=True
with netsock.urlopen("planning_center", request, timeout=30, context=ctx):   # urllib
opener = netsock.opener("crew.push", NoRedirect)                             # urllib with handlers; context=, not
                                                                             # an HTTPSHandler of your own
async with netsock.websocket_session("tally_arbiter", url) as ws:            # or ws = await netsock.websocket(...)
transport, proto = await netsock.udp_endpoint("blackmagic", Proto, remote=(host, 9910))   # connected, like remote_addr
sock = netsock.multicast_sender("propresenter", ttl=255)                     # an mDNS question, no group joined
```

**A module never opens a socket itself** (#348). `tests/test_modules.py` fails on a module that calls
`socket.socket`, `urllib.request.urlopen` or `build_opener`, `http.client.HTTPConnection`, `websockets.connect`,
`create_datagram_endpoint` and the like. The exceptions, listed in `MODULE_SOCKETS` there, are this computer's own
ports (LiveKit on 127.0.0.1) and LiveKit's one-time download.

A helper that cannot do its job raises `netsock.RouteError`, an `OSError` whose message is a fixed sentence, so a
client that already catches `OSError` keeps working. For the module's page and its connection test, use
`netsock.failure(class_id, "Could not reach the switcher")`: the route's own reason when its port is missing or
down, your sentence plus the port it went through when routed, and your sentence alone when not. It is built from
the route, never from the exception (CodeQL flags exception text reaching a page). Core adds a routed class's
missing port to the module's health, for a strict class and for one that fell back to the default network.

Each module's classes, and what happens when the port changes (`network_changed`):

| Module | Class | Goes through | On a port change |
|---|---|---|---|
| Blackmagic (ATEM, HyperDeck, Videohub) | `blackmagic` | `udp_endpoint` (ATEM), `open_tcp` (HyperDeck, Videohub); `multicast_sender` and `udp_socket` (discovery) | every device's connection starts again |
| ProPresenter (and its Planning Center link) | `propresenter` | `http_connection`, `multicast_sender` (discovery) | the live connection opens again |
| Tally Arbiter | `tally_arbiter` | `websocket_session` | connects again |
| REAPER | `reaper` | `udp_endpoint` (bound to Relay's listen port; 127.0.0.1 when REAPER is on this computer) | every computer's socket opens again |
| Planning Center | `planning_center` | `urlopen`, `opener` | asks again now |
| Remote access | `remote_access` | `urlopen`; cloudflared gets `--edge-bind-address` | cloudflared restarts |
| Intercom | `intercom` (media, strict) | LiveKit's `rtc.node_ip`, `rtc.interfaces` and `rtc.ips` | LiveKit restarts |
| Crew | `crew.push` | `opener` (web push) | next push |
| Signage web | `signage_web` | `opener` (link checks) | next check |
| Alert events | `alert_events` | `urlopen` (webhook), `udp_socket` (OSC) | next event |
| Core translation | `translation` | `websocket` (realtime), `urlopen` (text) | next connection |

Not steered yet: the remote intercom bridge's media (aiortc gathers on every interface), the phones' LiveKit
signalling (it arrives on the crew port), and LiveKit's download (the system default, since the intercom port may
have no internet). Dante, AES67 and NDI are built on this from the start: Dante discovery and control join and send
on the `dante` port with `multicast_socket`; Dante Virtual Soundcard and Dante Via choose their own network card in
their own app, so set the same one there. AES67 sends and joins with `multicast_socket`. NDI reads its own adapter
list (`ndi-config.v1.json`); check what the SDK version supports before writing it per run.

`netsock.notes(class_id)` lists what the operator should know about a route:

- Linux refused the pin because Relay lacks `CAP_NET_RAW`. The socket still sends from the port's address,
  which works for devices on that port's own network.
- Strict reverse-path filtering (`rp_filter=1`) may drop replies.
- Two ports are on the same subnet. Nothing can steer between them reliably.

On Windows, the weak host model can still send replies out of another port; keep each port on its own subnet.

### Dante

A Dante device's primary and secondary (redundant) ports are the device's, not Relay's. Relay joins one Dante
network, through the port routed to the `dante` class: the primary. Do not declare a second class for the
secondary network. Dante discovery (mDNS) and control (ARC on UDP 4440 and its neighbours) answer only on the
network the device shares with Relay, so a Dante module never falls back: its classes are `media=True`. In the
module's pages and docs, say "works with Dante devices" (Dante is Audinate's trademark), never anything implying
certification; for NDI, "NDI is a registered trademark of Vizrt NDI AB".

### Testing without a second network card

`tests/_netfake.py` has fake ports (`WIFI`, `USB`, `DANTE_NET`), `use_ports(...)` to make them the machine's
ports, `lan(...)` for the default network's addresses, `route(...)` to save a route in config, and
`loopback_route()` for real pinned sockets over loopback. Use it rather than defining ports in each test. Before a
release that changes networking, run the manual [hardware test plan](reference/multi-nic-hardware-test.md).

## Lighting cues from another module

A module that wants to fire a lighting cue (a ProPresenter slide tag, a schedule) never imports `lighting`. It calls
core's `app.modules.fire_cue(cue_id, request=None, *, by_name=False, source="")`, which the lighting module backs with a hook it sets in `attach()`
and clears in `detach()`. The answer is `{"ok": True}` or `{"ok": False, "error": "..."}` (lighting off, the cue is not
on a cue page, or the program did not answer). With no `request`, a `source` ("a ProPresenter slide tag") is recorded as a system audit entry saying where the press came
from, and `by_name=True` takes the cue's name on its page. Only a cue the operator put on a page can be fired this way, never the
controller's whole list, and a press is sent once: a dropped connection is reported, never retried, because the cue
may already have happened. A new lighting program is a `Backend` (`app/modules/lighting/backend.py`) registered in
`BACKENDS`; it gets cue pages, permissions and the audit trail from the module.

**Cue pages on a screen** (#379). Each page can have its own browser screen at `/screen?lighting=<page>&key=<key>`,
a page of big buttons for a tablet. A page holds the cues the operator chose (a cue may sit on several pages) on a grid
of its own, laid out with core's shared layout editor (#421, see [Boards and the shared layout
editor](#boards-and-the-shared-layout-editor)): each cue is a button with a place in whole cells and a look (text,
shape, colours, icon). Pages saved before #421 with free placement were moved once to the nearest cells of an 8 x 6 grid.
`/screen` is plain
HTTP with no sign-in, so the page's own key is its whole authority: 128 random bits that admin turns on, rotates (the old
link stops at once) and turns off, one per page, so a leaked link opens one page. The key can press only that page's cues,
is shown only by a `lighting.manage` route, and never reaches a config read. A wrong key, a page that is off and a page
that does not exist all answer the same plain 404; wrong keys are counted per address and slowed (429) and never logged,
and a press (20 per 10 seconds per address) is logged only once the key checked out ("Lighting: Worship, Verse (from the
Worship screen)"). The screen is told only `unreachable`, `refused` or `slow`, never the program's own text. Core serves
this through `modules.screen_pages`, a hook a module sets in `attach()` (query parameter -> async handler), the same way
the cue hook works; core never imports the module. Admin and chosen crew roles still press from signed-in pages.

The first backend is **QLC+ v4** (`qlcplus.py`): start QLC+ with `-w`, then set the host and port (default 9999) on the
Lighting page. It lists the virtual console buttons, presses one (a toggle button toggles) and reads which are on. It
also lists sliders (ids like `s14`, set as 0 to 100 percent through `POST /api/admin/lighting/level`) and, when
"Also list the program's functions" is on, scenes and chases (ids like `f12`, toggled by pressing). State comes from one
held connection that re-reads on QLC+'s function pushes and every few seconds, so the page shows lit cues without a
connection per read. It uses the `lighting` traffic class, so it follows the network port chosen for it. QLC+'s OSC and
MIDI routes are not used, and its web API has no command for frames, pages or a blackout (make a blackout a button in
the QLC+ project).

The second backend is **ShowXpress / TheLightingController** (`thelightingcontroller.py`; also sold as Showtec QuickDMX
and Sweetlight Controller): its Live API over TCP port 7348. Set the address, port and the Live API password. It lists the
buttons (a cue id is `b` plus ten hex digits of the button's name, since the API knows buttons only by name, so a
rename is a new cue), presses one (a toggle button toggles, a flash button is pressed and let go), releases, and lists
the faders as sliders (`s<index>`, 0 to 100 percent is -100 to 0 on the controller). A held connection follows the
controller's pushes and re-reads the list when its interface changes, reconnecting with a growing wait; a press is
sent once and never retried. Buttons whose name holds a `|` or a line break cannot be addressed and are left out.

The third is **Chroma-Q Vista** (`vista.py`), over OSC (UDP, Vista 3 R5 or later). In Vista, switch on File >
Preferences > OSC > OSC Server Enabled and add this computer as an OSC client; in Relay set the console's address,
the port (8000 unless changed) and the console type (`EX`). Vista has no OSC "jump to cue", so a cue is a playback
button or action grid cell that runs the cuelist, added by hand to a cue page with the id
`<type>.<console>.<panel>.<bank>.<row>.<column>` (`EX.1.0.1.0.0`, or `1.0.1.0.0` to use the console type setting).
Vista lists nothing and reports nothing, so these cues never show on or off. A press is a tap (the OSC strings `true`,
then `false` 20 ms later), exactly as the Bitfocus Companion module sends it; numbers outside Vista's limits (panel 0-10,
bank 0-100, row 0-11, column 0-59) are refused before anything is sent, because Companion warns they have crashed
Vista. The test message only proves Relay could send it: Vista does not answer.

The fourth is **Lightkey** (`lightkey.py`), over OSC (UDP, default port 21600; Lightkey also takes TCP, which Relay does not
use). **Lightkey runs on macOS only, so a church on Windows or Linux cannot use this backend**; the Relay computer can be
any system, since Relay sends OSC across the network to the Mac. Set the Mac's address, the port and, if you want one, a
fade time in seconds (blank lets each cue use its own). A cue is added by hand to a cue page with the id `Page:Cue`,
Lightkey's own spelling with spaces as underscores (`My_Control_Panel:Walk-in`; `selected:` is the page open in Lightkey;
in Lightkey, control-click a cue and choose External Control > Copy OSC Address to see it). Only letters, digits and
`_ . -` are allowed, so the OSC wildcards cannot fire every cue. A press sends `/live/<page>/cue/<cue>/activate` and a
release `.../deactivate`, each with the fade time when one is set. Lightkey lists nothing and reports nothing over OSC
(its feedback is MIDI), so these cues never show on or off. Lightkey's External Control Log (Window menu, OSC tab) shows
every message Relay sends and whether it matched a cue.

## Boards and the shared layout editor

Core has one editor for laying items out on a grid (#421): `app/static/layout-editor.js`, drawn by
`app/static/layout-render.js` with `app/static/layout.css`, and checked by `app/layout.py`. Boards (`app/boards.py`,
`/board/<slug>`, #156) and the lighting module's cue pages use it; any later surface that lays out buttons or widgets
(panel button surfaces #223, crew pages) must use it too rather than build its own.

**The schema** (`app/layout.py`). A grid is `cols` x `rows` whole cells (1 to 24). An item has `x`, `y`, `w`, `h` in
cells and a `style`: `label`, `shape` (rounded, rect, pill, circle), `bg`, `fg`, `pressed` (`#rrggbb` only) and `icon`
(from a fixed list). Store your items next to your own data and pass them through `layout.clean_style(raw,
your_defaults)` and `layout.place(items, cols, rows)`, which keeps them inside the grid and never overlapping. Never
send raw CSS to a page.

**In a page.** Load `layout-render.js`, `layout-editor.js` and `layout.css` (the operator panel already does), then:

```js
const ed = RelayLayout.edit(holder, {
  title: 'Worship', noun: 'page', paletteName: 'Cues', inline: true,           // inline: always editing, list beside
  cols: 4, rows: 3, sizes: [[4, 3], [6, 4]], ratio: '4/3',
  items: [{id, kind: 'button', title, x, y, w, h, style, props}],
  palette: [{group, name, kind, type, w, h, props, style}],
  fields: function (item) { return [{key, label, type: 'text' | 'select' | 'check' | 'time' | 'ro' | 'note', options}]; },
  addById: {label, placeholder, make: function (id) { return paletteEntry; }},
  onSave: async function (state) { /* PUT it; throw an Error to keep editing */ },
});
RelayLayout.draw(root, {cols, rows, items}, onPress);   // the same drawing, no editor (a screen)
```

`kind` picks the drawing: `button`, `captions`, `clock`, `countdown`, `text`, `status`, `lines`, `list`.
`RelayLayout.update(element, data)` feeds an item live data (`{lit}`, `{lines}`, `{rows}`, `{on_air, live_since,
viewers}`).

**Widgets on boards from a module.** Override three methods; core never imports the module:

```python
def board_widgets(self) -> list[dict]:          # while on: what the board's widget list offers
    return [{"type": "lighting.cue", "name": "Lighting cue", "group": "Lighting", "kind": "button", "w": 2, "h": 1,
             "style": {...}, "press_permission": "lighting.control",
             "fields": [{"key": "cue", "label": "Cue", "type": "select", "options": [["walk", "Walk-in"]]}],
             "presets": [{"name": "Walk-in", "props": {"cue": "walk"}}]}]

async def board_data(self, items) -> dict:      # live data for your items on a board, by item id
    return {item["id"]: {"lit": True} for item in items}

async def board_press(self, item, request) -> dict:   # a press on one of your buttons
    audit.note(request, "lighting.press", "Lighting: Walk-in")
    return {"ok": True}                         # or {"ok": False, "error": "a fixed sentence"}
```

- A widget type starts with the module's id; a `press_permission` must be one the module declares. The registry leaves
  out a definition that breaks either rule.
- Settings are checked against `fields`: a `select` keeps only an offered value, text is cut, unknown keys are dropped.
- A board may be public, on the viewer port: `board_data` sends nothing private, no addresses and no error text.
- Core checks the press: someone signed in on the operator port who holds `press_permission`, even on a public board,
  at most 20 presses per 10 seconds per address. Core writes the `audit.note`s the module makes.
- While the module is off its widgets stay on boards, drawn as off, and its presses are refused.

## NDI sources, receivers and outputs

The `ndi` module (#294) finds NDI sources and owns every NDI receiver. A module that wants video from an NDI
source (a viewer, multiview, router) never opens its own receiver and never imports `ndi`; it calls core's
`app.modules.ndi_sources()`, `ndi_subscribe(source, on_frame)` and `ndi_register_output(name)`, which the module backs
with hooks set in `attach()` and cleared in `detach()`.

- `ndi_sources()` lists `{name, machine, source, address, port, groups, converter, online, width, height, fps,
  listeners}`; empty while NDI is off. A source in a custom group is listed only if that group is watched.
- `ndi_subscribe(source, on_frame)` returns a function that stops listening (or None while NDI is off or the runtime is
  missing). There is one receiver per source however many subscribe, closed when the last one leaves. `on_frame`
  gets `{"width", "height", "fps", "fourcc", "data"}` on the receiver's thread and must not block.
- `ndi_register_output(name)` returns an object with `send(frame)` and `close()`, or None (off, missing runtime, or the
  name is taken). Used by the NDI output (#299).

Discovery is mDNS (`_ndi._tcp`) plus extra addresses asked directly; a BirdDog converter's `_birddog._tcp` adds what it
is set to. The NDI runtime and `cyndilib` are installed separately (NDI's licence does not let Relay bundle them,
`app/modules/ndi/runtime.py`): without them the module says so on its page and stays off. Board widgets: `ndi.summary`,
`ndi.sources`, `ndi.source`, `ndi.view`. Discovery servers (#295) are set on its page and written to an NDI config in Relay's state folder; see `docs/ndi-discovery-server.md`.

**The single-source viewer** (#296, `app/modules/ndi/viewer.py`): `/ndi/view/<source>` plays one source in a browser as
MJPEG (`/ndi/stream/<source>`), on the HTTPS operator port only (`panel_prefixes`) and behind `ndi.view`, so it stays on
the LAN. A source is a **feed**: one lease on the shared pool, one JPEG encode at most `view_fps` times a second (Pillow),
sent to every browser watching it; nothing is encoded with no viewer, and the lease is given back with the last one.
`view_fps` (1-30, 15), `view_quality` (20-90, 60), `view_width` (320-1920, 1280) and `view_max` (1-8, 4 sources at once;
the next is a 503) are settings. A source with a camera number in `tally` (source name -> number) gets a red or green
border from core's tally state (`app/tally.py`, filled by the ATEM, OBS and Tally Arbiter modules, which NDI lists in
`uses`). The audio meter reads a peak level the receiver sends alongside video (`audio_db`, in `runtime.py`; written
from cyndilib's documentation and **not proved on real NDI**: if the audio frame reads differently, the meter stays
empty and the picture is unaffected). WebRTC would add lower latency and audio; it is not built. The `ndi.view` board
widget is a tile drawn by core's `frame` kind (`{"image": "/path", "tally", "note"}`): it shows only for people signed in
on the operator port, since it reads the same stream.

**Multiview** (#297, `app/modules/ndi/multiview.py`): saved multiviews (`multiviews` in the NDI settings, up to 20) are
a layout (2x2, 3x3, 4x4, or 1 big with 5 or 7 small) and one box each: an NDI source (label, audio meter, tally border
from the source's camera number), a clock, or a countdown (to a time of day, or to the next service start). Each has a
page, `/ndi/multiview/<id>`, and a stream, `/ndi/multiview/<id>/stream`, on the operator port behind `ndi.view` like the
single-source viewer. Relay composes the picture once with Pillow, at most `view_fps` times a second, however many
browsers watch it, holds one pool lease per source while anyone watches and releases them all after the last leaves.
`view_max` does not apply (a 4x4 holds up to 16 receivers). The service countdown asks core's
`modules.service_starts_at()`, which a module that knows service times (Planning Center) backs by overriding
`Module.service_starts_at`. **Not built:** sending the composed picture out as an NDI source (#299 has no output yet).

## Recording and presentation hooks

A module that wants a recorder to start, stop, drop a marker or run an action never imports `reaper`. It calls core's
`app.modules.control_recording(command, value="", *, source="")` (`record`, `stop`, `marker` with the marker's name,
`action` with a REAPER command id), which the REAPER module backs with a hook it sets in `attach()` and clears in
`detach()` (#413). The answer is `{"ok": True, "done": [names]}` or `{"ok": False, "error": "..."}`; with a `source` the
REAPER module writes a system audit entry saying where it came from.

`app.modules.announce_presentation(name)` is called by the ProPresenter module each time a new presentation is up. A
module that wants to know appends a callable to `app.modules.presentation_listeners` in `attach()` and removes it in
`detach()`; it runs on the event loop and must not block. REAPER uses it for its song markers.

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

### The 32-lane audio bus

Beside the main capture, core has a bus of 32 mono **lanes** at 48 kHz (`app/audiobus.py`, #92). Any
device of any registered source can be patched to any lane; a crosspoint **matrix** routes lanes to
**outputs** of up to 32 channels. It is off until `audio_bus.enabled` is set, and the main capture does not
depend on it. The operator's setting, saved in `audio_bus` and edited through `GET`/`POST /api/admin/audiobus`:

```json
{"enabled": true,
 "lanes":   [{"lane": 1, "name": "Pastor", "source": "portaudio", "device": "Core Audio: Scarlett", "channel": 0},
             {"lane": 9, "source": "mixer_stream", "device": "ch 9", "channel": "mix"}],
 "outputs": [{"id": "monitor", "output": "portaudio", "device": "Core Audio: Speakers", "channels": 2, "latency_ms": 60}],
 "matrix":  [{"in": 1, "out": "monitor", "ch": 1, "gain_db": -6}]}
```

A device is opened once however many lanes use it, and changing one lane, input or output leaves the others
running. A module takes part with the two hooks it already has, plus an optional second one:

```python
class Stream(audio.AudioSource):
    def lane_input(self): return MyInput()      # default: your `open` as one mono lane

class MyInput(audio.LaneInput):
    def open(self, selector, deliver):          # deliver(float32 block shaped (frames, channels)), never blocking
        return audio.SourceInfo(rate=48000, device={"label": "Mixer", "channels": 32}, channel="all")
    def close(self): ...
    def alive(self): ...                        # False when the input is lost: the bus reconnects it

class Sender(audio.AudioOutput):
    def lane_sink(self, selector, channels): return MySink()   # raise audio.SourceError with a fixed sentence

class MySink(audio.LaneSink):                   # paced by the sender's own clock
    rate = 48000                                # any rate: the bus resamples to it
    def free(self): ...                         # frames write() takes now
    def queued(self): ...                       # frames waiting to play
    def write(self, block): ...                 # float32 (frames, channels); returns the frames taken
```

Consume a lane from core or a module with `audiobus.bus.lane(n).subscribe(callback)` (called on the input's
thread, so it must not block; any number of subscribers share a lane) or `subscribe_queue(loop)` for async
code. The engine can take a lane as its input with `audio_source: "lane"` and `audio_device: "<lane>"`, and
Relay's own sounds can be played into a bus output through `audio.get_output("bus").play(pcm, rate,
"<output id>:3,4")`. Outputs follow each lane's clock with a resampler trimmed by at most 0.5%, so two devices
on different clocks do not click; the cost is the output's `latency_ms` (default 60) of passthrough delay.

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

**OBS Studio** (`app/modules/obs/`, #250) publishes one entry per OBS computer it follows (up to four: a stream PC
and a record PC, say), with the source `obs_<computer id>`. It holds one obs-websocket 5 connection per computer and
does everything over it: following the scene and the outputs, switching scenes, the admin preview and the caption
sender. Health is fixed: error when OBS cannot be reached or the stream stopped without passing through "stopping"
(for ten minutes, or until it starts again); warn while reconnecting or when over 1% of frames were dropped (network),
skipped (encoding) or lagged (rendering) in the last minute; ok while streaming or recording; idle otherwise.

**YouTube** (`app/modules/youtube/`, #252) publishes one entry, `youtube`, labelled "YouTube": on air while the
channel has a broadcast whose lifecycle is `live`, with the summed concurrent viewers and the earliest start. It signs
in with core's device sign-in (below) and checks through the YouTube Data API, whose daily quota every install shares,
so it waits `live_s` (60 s) while it or any other entry in the stream status is on air and `idle_s` (600 s) otherwise,
and stops asking until after midnight Pacific when the quota is used up. Health: warn when the sign-in is refused or
the channel cannot be read, ok while live, idle otherwise. Broadcast titles go in `detail` only.

**Scheduled streams and Planning Center Publishing** (`app/modules/youtube/schedule.py`, #411): the YouTube module
`uses` Planning Center. Its planner lists upcoming broadcasts every 30 minutes (only while a schedule or a linked
episode is set up), creates and binds one broadcast per service (weekly rules and followed service types' plans,
remembered under `created` so none is made twice), and writes the matching broadcast's watch link into each linked
Publishing episode's times through the Planning Center client (`channels`, `episodes`, `set_episode_video`). Creating
needs a scope that can write: `youtube.force-ssl` through relay-cloud, `youtube` with a church's own device-flow app.
Refusals are looked up in `schedule.WORDS`, never an exception's text.

**Scenes from another module.** A module that wants to switch a video scene (a ProPresenter slide tag) never imports
`obs`: it calls core's `app.modules.switch_scene(name, source="...")`, which the OBS module backs with `scene_hook`,
set in `attach()` and cleared in `detach()`. Every connected OBS computer that has a scene of that name (any case)
switches; the answer is `{"ok": True}` or `{"ok": False, "error": "..."}`. When an operator maps scenes to camera
numbers, the OBS module also publishes into core's tally (`app/tally.py`) as `obs_<computer id>`.

## Signing in to another service

A module whose service needs a signed-in account (YouTube, Facebook) uses core's `app/oauth.py` (#304) instead of
asking the operator to paste a token. The operator presses **Connect**, the page shows a short code and an address
(google.com/device, facebook.com/device), and they sign in there on any phone or computer. This is OAuth device
authorization (RFC 8628): it needs no address the service sends the browser back to, which Relay, on a church's own
computer, does not have. Services that only do browser redirects (Restream) are not covered yet.

```python
from ... import oauth
from .. import require

class Google(oauth.Provider):                  # RFC 8628 defaults; override what the service does differently
    id, name = "google", "YouTube"
    device_url = "https://oauth2.googleapis.com/device/code"
    token_url = "https://oauth2.googleapis.com/token"
    revoke_url = "https://oauth2.googleapis.com/revoke"
    async def identity(self, tokens): ...      # {"id", "name"} of the channel or Page; may fill tokens.extra

ACCOUNT = oauth.Account("youtube", Google("youtube"), settings_key="youtube",
                        client=lambda: oauth.Client(id=..., secret=...), scopes=("…/auth/youtube.readonly",))
oauth.add_routes(router, "/api/admin/youtube/account", ACCOUNT,
                 view=require("youtube.view"), manage=require("youtube.manage"))
```

- **Keep it sealed.** The account is one JSON value in the module's own settings (`"account"`, or the `field` you
  pass). Put it in `defaults` as `""` and in `secret_settings`, and drop it in `public()`. A refreshed access token
  stays in memory; config.json is written only on connect, disconnect and a rotated refresh token.
- **Use it.** `await ACCOUNT.token()` gives a current access token, refreshed shortly before it runs out. It raises
  `oauth.NeedsSignIn` when the service no longer accepts Relay (revoked, password changed, Page role lost) and
  `oauth.Problem` when the service could not be asked; both carry a fixed sentence. When the service answers 401 to a
  token, call `ACCOUNT.refused()` and try again later.
- **Health.** `ACCOUNT.health()` is `("warn", "YouTube needs signing in again.")` while the operator must sign in
  again, else None. Return it from the module's `health()`.
- **Stop.** `await ACCOUNT.close()` in `stop()` ends a wait for a code.
- **The page.** `RelayOAuth.mount(container, '/api/admin/youtube/account', {manage: 'youtube.manage'})` (core's
  `app/static/oauth.js`) draws the Connect card from the panel's own pieces. Connect, Cancel and Disconnect need
  the module's `manage` permission (Admin and up); the card shows only the account's name, its scopes and who
  connected it, never a token.
- **Audit.** Connecting (with who did it), disconnecting and the service refusing Relay's sign-in are recorded as
  `<id>.connect`, `<id>.disconnect` and `<id>.signed_out`, with the account's name, never a token.
- The calls go out through `netsock.fetch` on the provider's traffic class (the module's own), so they follow the
  network port the operator chose. Only `https://` addresses are called.
- Disconnect asks the service to revoke Relay's access; if it does not confirm, the card tells the operator to remove
  it from the account's own security page.

The client IDs Relay signs in with, and how to make them, are in
[reference/oauth-apps.md](reference/oauth-apps.md).

## Captions from another module

Core hands every committed line, after the blocklist, to the recorder (`hub.line_sink`, set by the engine) and then to
each sink in `hub.line_sinks`. A module adds one with `hub.add_line_sink(fn)` in `attach()` and removes it with
`hub.remove_line_sink(fn)` in `detach()`. `fn(stream, lang, seq, text, started, ended)` may be called off the event
loop and must not block: queue the line and send it from the module's own task. A sink that raises is ignored, so a
sender can never cost viewers a line. The OBS module's caption sender is the first: per computer, a switch and a
language (the source captions or one translation), finished lines only, split to 32 characters for CEA-608, sent with
`SendStreamCaption` and only while that OBS is streaming.

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
- a module opens a socket or connection itself instead of through `app/netsock.py` (exceptions, with a reason,
  in `MODULE_SOCKETS`), or declares a traffic class whose id does not start with its own id;
- a module's manifest is invalid, its templates or scripts are missing, or it imports a module it does not
  name in `uses`;
- a module does not declare `<id>.view` and `<id>.manage`, an admin route does not ask for one of
  the module's own permissions, or a page needs a permission nobody declares;
- a module's route is outside its `route_prefixes`, or answers while the module is off;
- a module adds an admin page or status while it is off;
- a core template or script names a module's route.

## Renaming a module

A module that changes its id sets `Manifest.renamed_from` to the old one (`renamed_from="atem"` on `blackmagic`, #432). When
config loads, the registry moves what was saved under the old id to the new one, once: the module's settings key, its
on/off switch, and any network route or policy for its traffic classes (`atem` and `atem.x` become `blackmagic` and
`blackmagic.x`). Nothing under the new id is overwritten. The module also renames its permissions (`atem.view` is
`blackmagic.view`) and its routes; roles come from each permission's `min_role`, so no stored grant has to move, but an API
token or script that called the old routes needs the new address.

## Upgrades

A module with no switch saved yet starts **on** if its integration was already set up (`configured()`),
and **off** otherwise. The switch is written to config.json at startup, so setting the integration up later
never turns the module on by itself.
