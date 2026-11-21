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
  static/            its admin scripts and stylesheets (admin_styles), served at /static/modules/<id>/
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
        uses=(),                       # other modules it does more with, if they are on (it asks for them by name)
        section="Sound",               # its heading in the admin sidebar (see below)
    )
    defaults = {"host": "", "port": 0}
    admin_pages = (AdminPage(id="mixer", title="Mixing console", template="mixer.html"),)
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

**In the admin sidebar** each module that is on is one entry, under the heading named by `section`: one of
`NAV_SECTIONS` in `app/modules/__init__.py` (Show, Crew, Screens, Sound, Building), else "Modules". A module with
one admin page is a link straight to it, named by the page's `title`; one with several is an entry named by the
module's `name` that opens to its pages, so give those pages short titles that read well under it ("Control",
"Settings"). The entry's icon is its first page's `icon`, and a dot beside it shows `health()`.

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

**Tabs on a core page.** An `AdminPage` with `tab_of="network"` is not a sidebar entry: it is a tab on
the Network page, after core's own tabs (Racks, Map, Ports and notes, This computer, Find and add),
with the same permission rule. Core renders its template in the tab's panel (`data-pane="<page id>"`),
and `#<page id>` or `#network/<page id>` opens it. The UniFi module's Internet, Wi-Fi and who's on,
NDI and Dante, and UniFi console are tabs this way. `registry.admin_tabs(user)` lists them;
`TAB_PAGES` names the core pages that take tabs (Network, and Signage, whose template draws them: Signage web's
Web & YouTube is a tab on it, `#signage/signageweb`). A module that knows devices adds them to the map
itself through `gear.<id>` (see Gear, ports and channels), not through a tab.

**Pages as tabs.** A module with `admin_tabbed = True` and several pages gets one sidebar entry, and each of its pages
shows the same bar of tabs under its title (ProPresenter does; so does System, #591). Page tab bars are `.page > .tabs`
in admin.css: full width, one height on every page.

**Features outside the admin panel.** A `UserPage(title, path, permission, note)` in
`Module.user_pages` lists one of the module's own pages on the home page (`/home`) of a signed-in
user who holds `permission`, while the module is on. `path` must sit under the module's
`route_prefixes` and `permission` must be one the module declares (the registry refuses it
otherwise). The page checks the permission itself with `require(...)`, the same call as an admin
route, and should serve only on the operator (HTTPS) port, where the sign-in cookie lives.

**Hide controls** in the module's script with `relayUI.can("<id>.<action>")` (see "Shared helpers for module
scripts"), which is true when the signed-in user holds the permission. It only tidies the screen: the server
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
sock = netsock.multicast_socket("dante", "224.0.0.251", 5353)                # joined on the port's address
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
| Wireless mics (Shure) | `wireless_mics` | `open_tcp` | every receiver's connection starts again |
| REAPER | `reaper` | `udp_endpoint` (bound to Relay's listen port; 127.0.0.1 when REAPER is on this computer) | every computer's socket opens again |
| Planning Center | `planning_center` | `urlopen`, `opener` | asks again now |
| Remote access | `remote_access` | `urlopen`; cloudflared gets `--edge-bind-address` | cloudflared restarts |
| Intercom | `intercom` (media, strict) | LiveKit's `rtc.node_ip`, `rtc.interfaces` and `rtc.ips` | LiveKit restarts |
| Crew | `crew.push` | `opener` (web push) | next push |
| Signage web | `signage_web` | `opener` (link checks) | next check |
| Alert events | `alert_events` | `urlopen` (webhook), `udp_socket` (OSC) | next event |
| Dante (discovery and control-port check) | `dante` (media) | `scan_addresses` gives core's mDNS listener the port's address; `udp_socket` (control-port check) | scans again |
| Core translation | `translation` | `websocket` (realtime), `urlopen` (text) | next connection |

Not steered yet: the remote intercom bridge's media (aiortc gathers on every interface), the phones' LiveKit
signalling (it arrives on the crew port), and LiveKit's download (the system default, since the intercom port may
have no internet). Dante and NDI are built on this from the start: Dante discovery and control join and send
on the `dante` port with `multicast_socket`; Dante Virtual Soundcard and Dante Via choose their own network card in
their own app, so set the same one there. NDI reads its own adapter
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

## Soundboard channels for Planning Center

The Planning Center page's Team and mics tab offers the audio source channels core holds (see "Gear, ports and channels"):
a module that reads a mixing console (Audio Mixers, #150) reports its input channels there and they appear in the tab's "Add channel" list. The
operator adds the ones they want to the board and assigns each to positions of any service type; until some are reported
they type them in by hand. The board is saved as `planning_center.rundown.channels` (`{id, number, name, channel_ref,
positions: [...]}`), and the rundown turns it into the older position-to-mics shape in `rundown.effective_mics`.

## Lighting cues from another module

A module that wants to fire a lighting cue (a ProPresenter slide tag, a schedule) never imports `lighting`. It calls
core's `app.modules.fire_cue(cue_id, request=None, *, by_name=False, source="")`, which asks for the capability `lighting.cues` that the lighting module
provides in `attach()` (see [Reaching another module](#reaching-another-module)). The answer is `{"ok": True}` or `{"ok": False, "error": "..."}` (lighting off, the cue is not
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
this through the capabilities named `screen_pages.<query parameter>`, each an async handler a module provides in `attach()`
(`modules.using_all("screen_pages.")`), the same way the cue capability works; core never imports the module. Admin and chosen crew roles still press from signed-in pages.

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

## Screens and places

A physical display is one core **screen** (`app/screens.py`, tables in `app/migrations/0008_screens_places.sql`, #527), with
one name, one **place** (Foyer, Stage, Kids) and an address. Boards, signage, alerts and panels attach to it; a module
never keeps its own list of displays (`tests/test_shared_concepts.py` counts a module table named `*_screens` and its
baseline may only shrink).

**Attach by key.** Call `screens.adopt("<module>:<your id>", name=..., heard=False)` when your feature learns of a display
(`heard=False` records it without saying it was seen). The same name is the same screen: adopting a key whose name another
screen already has joins that screen, and renaming a screen to another's name merges the two (`screens.rename`). Show
the core name (`screens.names_by_key("<module>:")`), never a copy of your own. Signage does exactly this: `signage_screens`
keeps what signage adds (address, sound, default playlist) and its name and place are the core screen's.

**What a screen shows.** A screen shows one board or one signage playlist: `normal_*`, or `during_*` while a named schedule
is open, or `hold_*` put on by hand from the Screens page until cleared. `screens.showing_now(screen, signage_on=...)`
decides; while Signage is off a signage choice falls back to the board it last showed. A browser opened on a board or a
signage address is sent to the right one: the board poll answers `goto`, and the signage state carries `board` with a
port and path (the player follows it). A link from a screen's own address uses `?screen=screen-<id>` so the screen
keeps its identity across the move.

**Alerts and places.** An alert button's `screens` list may hold a place ref (`place:relay:3`); `alerts.targets_of` turns it into
every panel and browser screen in that place. Boards keep the place's name beside its ref (`place_ref`), and renaming a
place carries over.

## Gear, ports and channels

One seam, for everything a module has that is a piece of equipment, a connector on it, or a signal through it
(`app/gearfeed.py`, #545). A module offers it in `attach()` and core asks for it; the module never calls core's `ports.sync`
or `channels.report` itself, and there is no other hook (`Module.network_map()`, `getattr(mod, "mixer_channels")` and the
module's own channel reporting are gone; `tests/test_shared_concepts.py` counts them as `seam` with a shrink-only baseline).

```python
def attach(self):
    modules.provide("gear.<module id>", {"list": self.gear_list,                  # what it has now
                                         "can_rename": ..., "rename": ...})       # optional: rename on the gear (#543)
    modules.provide("routes.<module id>", {...})                                   # optional: apply routes (#544)

def gear_list(self) -> dict | None:
    return {"devices":  [{"mac": ..., "ip": ..., "host": ..., "name": ..., "kind": ..., "tags": [...]}],   # network map
            "ports":    [{"gear": <gear id>, "items": [{"key", "kind", "direction", "gear_name", ...}]}],
            "channels": [{"gear": <gear id> or None, "within": "<key prefix>", "items": [{"key", "kind", "direction", ...}]}]}
```

* `devices` are the boxes the Network map merges (see Gear and racks); read what is already in memory, send no secrets.
  The module's one-sentence `network_map_adds` stays on the `Module` class so the map's Sources card can say what it adds
  while the module is off.
* `ports` and `channels` are every connector and signal it has now, as the Ports and Channels sections describe. One it
  stops listing turns missing, not deleted, so its name, note and cables stay. Leave a device out (or return None) while you
  cannot see it: a Videohub that is not connected does not say how many inputs it has.
* Call `gearfeed.changed("<module id>")` when what you have changed (a scene list was read, a device connected). Core calls
  `list()`, records the answer and returns `{channel key: channel ref}`, or None when the database is not ready. It blocks;
  from async code use `asyncio.to_thread` where the call is slow. Cables and routes keep their own calls (`cables.sync`,
  `routing`).
* **Switching a module off** withdraws `gear.<id>` with everything else it provided; its gear stays in core, shown as missing,
  with its names, notes and cables. Switching it on and calling `changed` brings it back as the same records.
* **Core's own sources** (the audio bus, PortAudio, sound-level sources) are not modules and call `channels.report`
  directly; the stream itself stays in `app/audio.py`. The tally state (`app/tally.py`) carries live program and preview
  flags, not a list of channels: a tally camera points at its channel by `"channel"` (see Channels).

## Gear and racks

A piece of equipment (a switch, an ATEM, a Dante stage box, this computer) is one core **gear** record (`app/gear.py`, tables
in `app/migrations/0011_gear_racks.sql`, #547, part of the gear/ports/channels epic #539), with one name, one **rack** and one
place. A rack is inside a core place (Booth > Rack A); gear sits in a rack, or straight in a place. The network map and
Blackmagic attach to it; a module never keeps its own list of equipment, racks or places (`tests/test_shared_concepts.py`
counts a module table named `*_gear`, `*_equipment`, `*_racks` or `*_places`, and its baseline may only shrink).

**Attach by key.** Call `gear.adopt(["<module>:<your id>", ...], name=..., kind=..., model=..., heard=False)` when your
feature learns of a box. Every key you pass is the same box: keys that two records hold merge them, so a box seen first by
address and later by MAC is one record, and a name, a note and a rack survive a new IP address. Add the network keys with
`gear.net_keys(mac, ips, host)`: a record that has a MAC is known by it alone and drops its `ip:` and `host:` keys. Do not put
an address key on two devices of your own that are not one box. Refs are `gear:relay:<id>` and `rack:relay:<id>`.

**Names.** The gear's name is what Relay shows. It follows what the source reports until an operator names it
(`gear.rename`); after that it stays, and nothing is sent to the device. Show the core name, never a copy of your own.
Blackmagic keeps a device's connection settings (address, port, kind, tally, presets, cables) in its own config and its name,
model, drawing and place on gear (`blackmagic:<id>`); the rack-or-shelf place is "in a rack" or "in a room outside any rack",
and the list order is the gear's `position`.

**The network map.** It stores what it merges as gear on every fresh read (Dante boxes from mDNS discovery are gear of kind
`dante`, keyed `dante:<host name>` too). A closet on the Network page is a rack inside a core place of the same name. Names, notes
and closets saved in config before #547 move onto gear the first time they are read.

## Ports

Every physical connector is one core **port** on its gear (`app/ports.py`, table in `app/migrations/0012_ports.sql`, #540): a
switch's Ethernet or SFP cage, an SDI input, an XLR, a USB socket, a mains inlet, this computer's network adapter. A ref is
`port:relay:<id>`. A TCP port number is not a port; it stays a field of a host address (`"port": 9910`). Each port has a `key`
(the gear's own name for it: `"12"` on a switch, `"in1"` on a Blackmagic device, the adapter id on this computer), a kind
(`eth`, `sfp`, `sdi`, `hdmi`, `xlr`, `trs`, `usb`, `iec`, `dc`, `other`), a direction (`in`, `out`, `both`), the gear's name for
it, a Relay-only name, a note, and network fields (link, speed, PoE, errors) that are `None` when the source does not say.
`link_up` None is "unknown" and stays apart from False ("no link", "no signal"): never show a connector as live unless the
device reports it.

**Report, do not keep.** A module lists, under `ports` in its `gear.<id>` answer (Gear, ports and channels), `{"gear": gear_id,
"items": [{"key", "kind", "direction", "gear_name", ...}, ...]}` with every port its gear has now (core calls `ports.sync`); a port it no longer reports is marked `missing`, not dropped, so its note stays. Report only while you
can see the device (a Videohub that is not connected does not say how many inputs it has). A note or a name on a port the
source has not listed yet goes through `ports.adopt(gear_id, key, heard=False)`. Keep no list of ports or connectors of your
own, in a table, in config or in a page script: `tests/test_shared_concepts.py` counts a module table named `*_ports` or
`*_connectors` and a module script that lists connectors (`port('in1', 'sdi', ...)`), with a shrink-only baseline.

Who reports what: the network map syncs each switch's ports (keyed by number) from what its sources merge; the traffic routes
(`app/netroute.py`) sync this computer's hardware adapters on gear `relay:this`, and a route is saved as
`{"port": "port:relay:<id>", "key": "<adapter id>"}` so it still resolves while the database is down; Blackmagic builds each
model's faces on the server (`drawings.py`) and syncs their connectors while the device is connected. Port notes saved in
config before #540 (network map `"<switch>#<port>"`, Blackmagic `notes`) and traffic routes' `known_ports` move onto ports the
first time they are read.

## Cables

A cable joins two core ports (`app/cables.py`, table in `app/migrations/0013_cables.sql`, #541) on any gear: an ATEM's SDI out
to a Videohub input, an ATEM's Ethernet to a switch port, a device to the switch port UniFi says it is on. A ref is
`cable:relay:<id>`. Each cable says who said so (`by`): `"operator"` for one a person recorded, or the id of the module that
reports it. An operator can remove their own cable and add a note to any cable; a reported cable cannot be removed
(`cables.remove` refuses it), only annotated, because its source owns it. A port may have more than one cable (a patch panel,
a split), but the same two ports are one cable whichever way round they were entered.

**Report, do not keep.** A module that knows what is plugged into what calls `cables.sync("<module id>", [(port id, port id),
...])` with every cable it sees now; one it no longer reports is marked `missing`, not dropped, so its note stays, and a pair an
operator already cabled stays theirs. Call it only while the source is readable. UniFi's uplinks go through the network map
(`network_map.sync_cables`): a device's `uplink_port` is the switch port, and the device's own end is its uplink port or a
port `eth` made on it. A module that lets an operator record cables calls `cables.add(port id, port id)`; the Blackmagic page
does, so a cable recorded there shows on the Network page's Cables list and Map, and the other way round. Keep no list of
cables of your own: `tests/test_shared_concepts.py` counts a module table named `*_cables`, `*_wires` or `wiring`, and a module
default `"cables": []`, with a shrink-only baseline. Cables saved in Blackmagic config before #541 move onto core cables the first
time they are read.

## Channels

A channel is one audio, video or talk signal an operator thinks of as one thing, such as "Camera 2" (`app/channels.py`, tables in
`app/migrations/0014_channels.sql`, #542). A ref is `channel:relay:<id>`. A channel is made of **ends**: what each module
reports (NDI's `BOOTH (Camera 2)`, a Videohub input, an ATEM input, a tally camera). Every page shows the channel under one name:
the one an operator typed, else the first reporting end's own.

**Report, do not keep.** A module that has channels lists, under `channels` in its `gear.<id>` answer (Gear, ports and channels),
`{"gear": gear_id, "items": [{"key", "kind", "direction", "number", "gear_name"}, ...]}` with every channel it sees now; core calls
`channels.sync`. `kind` is
`audio`, `video` or `talk`; `direction` is `source` or `destination`; `key` is the module's own stable id for it, kept per module
(put the device id in it when the module has several devices, as Blackmagic does). One it no longer reports is marked `missing`, not
dropped, so its name and note stay; `within="<key prefix>"` limits that to one machine when a module reports one at a time (OBS).
Report only while the source is readable. Today NDI (sources and Relay's outputs), Blackmagic (ATEM inputs, Videohub inputs and
outputs), OBS (scenes), the audio bus (patched lanes and outputs), PortAudio input channels, sound-level sources and the intercom
(talk) report theirs.

**Joins are the operator's.** Relay never joins two ends by itself. `channels.suggestions()` lists likely pairs (the same name, or the
same number on one gear; a name a device gives everything, like "Input 3", is not a reason) and the Network page's Channels section
lets an operator join them, say they differ, or split an end back out. After a join the old ref still works: `channels.by_ref` follows it.

**Pick by ref.** A page that lets an operator choose a channel saves its ref beside the typed name and reads it back with
`channels.describe(ref, typed_name)`, which says `found` False for a name-only channel so the page can flag it. The rundown's board
channels and mic labels do this (`channel_ref`, `mic_refs`): a typed name that matches exactly one present audio channel adopts its ref
once. A tally camera may carry `"channel"`, the ref of its video channel, and shows that channel's name. Keep no list of channels of
your own: `tests/test_shared_concepts.py` counts a module table named `*channels` and a module default `"channels": []`, with a
shrink-only baseline (the intercom keeps its talk settings on its table, and the rundown keeps each channel's positions).

## Routes

A route says which source channel feeds which destination channel, such as "Lobby TV takes Camera 2", with a gain in dB where the
gear has one (`app/routing.py`, tables in `app/migrations/0015_routes.sql`, #544). Both ends are core channels (see Channels). A
**salvo** is a saved set of routes taken in one click; its `scope` says whose it is (`ndi`, `blackmagic:<device id>`).

**The module that owns the destination applies it.** It offers `modules.provide("routes.<module id>", {...})` in `attach()` with
`exclusive` (a destination takes one source, as a video output does, or mixes several, as an audio output does), `gain`
(the gear has a gain per route), `apply(end, wanted)` (point the destination end at `wanted`, a list of `{"ends": [the source
channel's ends], "gain_db"}`; pick the end you can use; return "" or a sentence saying why not) and `read(end)` (what the gear
really does, a list of `{"provider", "key", "gain_db"}` naming source ends, or None when it cannot say), and optionally
`permission`: a permission of its own that may route its destinations besides `network.manage` (NDI names `ndi.manage`,
Blackmagic `blackmagic.control`), so an operator routes from the module's page without Network rights. Core code that owns
destinations (the audio bus) uses `routing.provide_core(provider, api)`. A module whose gear forgets its routes when it
restarts calls `routing.reapply("<module id>")` from `start()`.

**The gear is the truth.** `routing.status(destination)` says what was asked for (`wanted`), what the gear does (`actual`) and
`state`: `same`, `differs`, `wanted` (the gear cannot say), `actual` (the gear does something nobody asked for here) or `none`.
Pages show a difference and Relay never quietly overwrites the gear. `routing.feeds(ref)` answers "what feeds this?" for any
channel ref and `routing.feeding(ref)` "where does this go?"; the Network page's Channels list shows both.

**Make a route from anywhere.** `routing.set(destination, source, gain_db)`, `clear`, `replace` and the salvo calls
(`save_salvo`, `take_salvo`, `delete_salvo`) block, so call them through `asyncio.to_thread`. The admin API is `/api/admin/routes`
(`network.view` to read, `network.manage` to change), and a board's **Route** button makes the same route. The NDI page, the
Blackmagic page (Videohub) and the audio bus crosspoints all read and write through it; the NDI router keeps only the outputs it
creates (`router_outputs`), the Videohub only its connection, and the audio bus's crosspoints are the routes into its output channels
(`out:<id>:<n>`, from lanes `lane<n>`). Routes and presets saved by those pages before #544 moved to core the first time they were read.

**Keep no list of routes.** `tests/test_shared_concepts.py` counts a module table named `*routes`, `*salvos`, `*matrix` or
`*crosspoints` and a module default `"routes": []`, `"salvos": []` or `"matrix": []`, with a shrink-only baseline.

## Phones and computers

A phone or computer a person carries is one core **device** (`app/devices.py`, tables in `app/migrations/0009_devices.sql`,
#528): a name, a platform, its user and when it was last seen. A ref is `device:relay:<id>`. What it can do is a **part**,
and each part stays in the table of the feature that owns it, with a `device_id` that references `devices (id)`:

| Part | Table | Owner |
| --- | --- | --- |
| Sign-in trust (a PIN works there) | `trusted_devices` | core |
| Intercom credential | `intercom_devices` | Intercom |
| Wi-Fi check-in (MAC address or hostname) | `crew_devices` | Crew |
| Web push for crew pages | `crew_push_subs` | Crew |

A module that holds a part does three things (`tests/test_shared_concepts.py` fails a module table of devices, phones or
push subscriptions that has no `device_id` referencing `devices`):

- its migration adds `device_id integer references devices (id) on delete cascade`, so the part goes when the device does,
  even while the module is switched off, and attaches the rows it already holds (one device each);
- it asks core for the device when it makes a part: `devices.for_part(user_id, "<kind>", "<table>", user_agent=..., q=tx)`
  returns the device it was told (`device=`), else the same user's device with the same non-empty user agent that has no part
  of this kind yet, else a new device. A part made without a user agent is a device of its own; an admin joins two with
  **Join…** (`devices.merge`);
- `attach()` registers it with `devices.register(kind, label, table, revoke, describe)` and `detach()` calls
  `devices.unregister(kind)`. `revoke(device_id)` ends the part's live side (the intercom closes the phone's socket);
  `describe(device_id)` returns the short sentences the phones list shows for it. Core holds this registry the way
  `users.signed_out_hooks` holds the account hooks, so it never imports a module.

`devices.revoke(id)` is "forget this phone": every registered part's `revoke` runs, then the device row goes and takes every
part row with it (sign-in trust is deleted by hand, because it outlives the admin who trusted it; PIN sessions from it end
with it). The routes are `GET /api/admin/phones`, `PUT|DELETE /api/admin/phones/{id}`, `POST /api/admin/phones/{id}/merge`
and the signed-in user's own `/api/admin/me/phones`. A module's own "sign this phone out" still ends only its part.

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

Options for a surface laid out in place on an admin page (the Boards page uses them): `bar: {title, note}` (a bar of
title, note, Undo, Cancel and Save instead of the toolbar), `between` (an element under the bar, such as page tabs),
`tray: true` (the widgets in a row of groups above the grid; a tap drops one in the first free space), `card: true`
(the selected item's settings in a card beside it, with `swatches` for its background), `canvasClass` and `hintText`.
`ed.setBar(bar)` changes the bar's words.

`kind` picks the drawing: `button`, `captions`, `clock`, `countdown`, `text`, `status`, `lines`, `list`, `fader`.
`RelayLayout.update(element, data)` feeds an item live data (`{lit}`, `{lines}`, `{rows}`, `{on_air, live_since,
viewers}`, `{strips}`).

A `fader` widget (#150) draws up to 16 touch faders from `{"strips": [{"id", "label", "pos", "db", "muted"}]}` (`pos` is
0..1, `db` the text shown above it). Like a button it needs its `press_permission`: moving a fader posts `{"strip", "pos"}`
and its mute button `{"strip", "mute"}` to the same press route, and the module reads them with `await request.json()` in
`board_press`. A strip the person touched in the last few seconds is not redrawn from a reading.

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

**Pages, places and what a screen shows.** A board has pages (each its own layout on the board's grid) and a place
(Foyer, Stage) the Boards page groups by. Which page shows is decided in core (`boards.current`): one page, a rotation
every N seconds (each screen turns it from the clock, so screens agree), a schedule by day and time, or following
another program. A module offers something to follow by overriding `board_follow()`:

```python
def board_follow(self) -> dict | None:    # called every few seconds while on: read what is in memory
    return {"name": "ProPresenter", "connected": True,
            "now": {"presentation": "Sunday", "group": "Sermon", "label": "POINT 1"},
            "suggest": {"presentation": [...], "group": [...], "label": [...]}}
```

A board's rules match the presentation, slide group or slide label (any case) and pick a page. A page chosen by hand
on the Boards page holds until what decides the page changes. Each screen polls `/board/<slug>/_/data` with its own id,
so the Boards page lists and names the screens showing a board, and reloads them when the board is saved or when
"Reload all screens" is pressed.

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
- `ndi_register_output(name, width=1280, height=720, fps=30)` returns an object with `send({"data": BGRA bytes})` and
  `close()`, or None (off, missing runtime, or the name is taken).
- `modules.provide("ndi.outputs.<your id>", fn)` in `attach()` offers pages as NDI outputs (#299): `fn()` returns
  `[{"id", "name", "url", "alpha", "group"}]`, where `url` is a path on the viewer port or `http://127.0.0.1:<port>/...` for
  a page on a port of its own (anything else is ignored). The NDI page lists them for the operator to switch on; the NDI
  module renders each in a hidden browser and sends it. Signage offers each signage screen this way, never importing `ndi`;
  the NDI module lists core's screens, overlays and public boards itself (a board that needs a sign-in cannot be rendered).

The router (#298) and page outputs are in `docs/ndi-router-and-outputs.md`.

Discovery is mDNS (`_ndi._tcp`) plus extra addresses asked directly; a BirdDog converter's `_birddog._tcp` adds what it
is set to. Everything that talks to NDI runs in Rust (`native/relay-ndi`, the `relay_ndi` wheel, #606): the finder, a receiver thread
per source, senders, routers, the picture scopes, the audio meter, the viewer's JPEG encode and the test patterns. The NDI
runtime is installed separately (NDI's licence does not let Relay bundle it; `native/relay-ndi` opens it with dlopen, so a
build needs no SDK, `app/modules/ndi/runtime.py`): without it the module says so on its page and stays off. Board widgets: `ndi.summary`,
`ndi.sources`, `ndi.source`, `ndi.view`. Discovery servers (#295) are set on its page and written to an NDI config in Relay's state folder; see `docs/ndi-discovery-server.md`.

**The single-source viewer** (#296, `app/modules/ndi/viewer.py`): `/ndi/view/<source>` plays one source in a browser as
MJPEG (`/ndi/stream/<source>`), on the HTTPS operator port only (`panel_prefixes`) and behind `ndi.view`, so it stays on
the LAN. A source is a **feed**: one lease on the shared pool, one JPEG encode at most `view_fps` times a second (Rust, `relay_ndi.jpeg`),
sent to every browser watching it; nothing is encoded with no viewer, and the lease is given back with the last one.
`view_fps` (1-30, 15), `view_quality` (20-90, 60), `view_width` (320-1920, 1280) and `view_max` (1-8, 4 sources at once;
the next is a 503) are settings. A source with a camera number in `tally` (source name -> number) gets a red or green
border from core's tally state (`app/tally.py`, filled by the ATEM, OBS and Tally Arbiter modules, which NDI lists in
`uses`). The audio meter reads a peak level the receiver sends alongside video (`audio_db`, from the Rust receiver;
proved on a real NDI stream carrying a -23 dBFS tone, #606). WebRTC would add lower latency and audio; it is not built. The `ndi.view` board
widget is a tile drawn by core's `frame` kind (`{"image": "/path", "tally", "note"}`): it shows only for people signed in
on the operator port, since it reads the same stream.

**Multiview** (#297, `app/modules/ndi/multiview.py`): saved multiviews (`multiviews` in the NDI settings, up to 20) are
a layout (2x2, 3x3, 4x4, or 1 big with 5 or 7 small) and one box each: an NDI source (label, audio meter, tally border
from the source's camera number), a clock, or a countdown (to a time of day, or to the next service start). Each has a
page, `/ndi/multiview/<id>`, and a stream, `/ndi/multiview/<id>/stream`, on the operator port behind `ndi.view` like the
single-source viewer. Relay composes the picture once with Pillow, at most `view_fps` times a second, however many
browsers watch it, holds one pool lease per source while anyone watches and releases them all after the last leaves.
`view_max` does not apply (a 4x4 holds up to 16 receivers). The service countdown asks core's
`modules.service_starts_at()`, which asks for `plan.starts_at`, the capability a module that knows service times (Planning
Center) provides. **Not built:** sending the composed picture out as an NDI source (#299 has no output yet).

## Recording and presentation hooks (and reaching another module)

A module that wants a recorder to start, stop, drop a marker or run an action never imports `reaper`. It calls core's
`app.modules.control_recording(command, value="", *, source="")` (`record`, `stop`, `marker` with the marker's name,
`action` with a REAPER command id), which asks for `reaper.recording`, provided by the REAPER module while it is on (#413). The answer is `{"ok": True, "done": [names]}` or `{"ok": False, "error": "..."}`; with a `source` the
REAPER module writes a system audit entry saying where it came from.

`app.modules.announce_presentation(name)` is called by the ProPresenter module each time a new presentation is up. A
module that wants to know calls `modules.listen("presentation.up", fn)` in `attach()` and `unlisten` in `detach()`; `fn(name)`
runs on the event loop and must not block. REAPER uses it for its song markers.

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

Capture reads one bus lane (`audio_lane`, 1 to 32, default 1): the engine opens the `"lane"` source with the lane
number as its selector. The input itself is patched to that lane on the Audio bus page. Resampling to 24 kHz, the meter, silence
gating, reconnecting and the consumers stay in core's pump, so a source only delivers frames. `open` raises
`audio.SourceError` with a fixed sentence (nothing from the network or a library) when it cannot start. An
`AudioOutput.play(pcm, rate, selector)` takes mono 16-bit PCM, returns at once and raises nothing.

### The 32-lane audio bus

Beside the main capture, core has a bus of 32 mono **lanes** at 48 kHz (`app/audiobus.py`, #92). Any
device of any registered source can be patched to any lane; a crosspoint **matrix** routes lanes to
**outputs** of up to 32 channels. It is always on (there is no switch): with nothing patched it opens nothing,
and a device that is missing is a lane or output that waits and reconnects. The operator's setting, saved in `audio_bus` and edited through `GET`/`POST /api/admin/audiobus`:

```json
{"lanes":   [{"lane": 1, "name": "Pastor", "source": "portaudio", "device": "Core Audio: Scarlett", "channel": 0},
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
code. The captions take a lane as their input (`audio_lane`, through the `"lane"` source), and
Relay's own sounds go to a named **destination**: `audiobus.bus.play_to("alerts", samples, rate)` mixes a mono
float32 clip into every output channel that destination is routed to, at that route's gain. Core offers `alerts`
(the tone for each alert level, `app/tones.py`), `test` and one `speech:<language>` per target; a module offers its own
with `audiobus.register_destination(key, label)`. Each destination is a source in the route editor, so the operator
routes it to output channels like any lane. Nothing under `app/` opens a sound device (`tests/test_bus_only.py`).
Outputs follow each lane's clock with a resampler trimmed by at most 0.5%, so two devices
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

## Steps for the "No sound?" walk

The "No sound?" walk (`app/nosound.py`, #263) starts at Relay's input and walks upstream so a volunteer can see where
the sound stops. Core owns the first steps (is Relay capturing, is sound arriving). A module that knows a link further
upstream (a Dante input, a sound desk mute) adds steps by providing `nosound.<module id>` in `attach()`: a function
`fn(audio) -> list[dict]` taking the capture status (`engine.capture.status()`) and returning `nosound.step(id, title,
state, checked, look)` entries, in order from nearest Relay to farthest. `state` is "ok", "fail" or "unknown" (Relay
cannot read it: never a guess); `checked` says what Relay looked at, `look` what the person should do next. Add steps
only when the input is yours, and read what the module already knows: send nothing. The walk stops at the first
"fail" and shows the rest as not checked. Wrap the provider in `nosound.upstream(order, fn)` to say where it sits on the
way upstream (lower is nearer Relay: Dante 10, the sound desk 20), so the nearest fault is reported first. The sound
desk step (`audio_mixers/walk.py`) finds the desk through `dante.routing`: the channel subscribed to Relay's input
names a device that is a configured desk, and its label a strip; it says whether that strip is muted, or "unknown" when
the desk is not heard, has not said, or the label matches no strip. Shown on the admin Health page and on the crew page's "No sound?" tab.

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
`obs`: it calls core's `app.modules.switch_scene(name, source="...")`, which asks for `obs.scenes`, provided by the OBS
module while it is on. Every connected OBS computer that has a scene of that name (any case)
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

## Talkback text from another module

The Talkback module (#93) transcribes one audio bus lane on a session of its own: its own capture (the `talkback_lane`
source), its own start, stop and restart, and a transcription backend chosen apart from the captions (blank follows
the main feed's). Its text is kept in the module, never in `hub`, so it cannot reach the caption feeds, a recording or an
export. Another module reads it through `modules.using("talkback.lines")`, a dict of `recent(limit)` (newest lines,
`{seq, ts, text}`) and `subscribe(fn)` (`fn({"type": "delta", "text"})` while a line is open, `fn({"type": "line", ...})`
when it is committed; returns the way to stop listening). Talkback is a second stream of billed audio seconds: its status
shows them beside the main feed's.

## Shared helpers for module scripts

Core's `app/static/relay-ui.js` (#461) gives every script `window.relayUI`. The operator panel, the crew page and
every standalone page whose script uses it load it first; a new page that uses it adds
`<script src="{{ static('relay-ui.js') }}"></script>` before its own script.

| Helper | What it does |
|---|---|
| `can(perm)` | True when the signed-in user holds the permission (true on a page with no permission check). |
| `api(method, path, body, opts)` | One JSON request. Throws an `Error` carrying the server's sentence (or "That did not work."), `.status` and `.data`. `opts`: `cache: 'no-store'`; `session: true` sends an ended panel session to the sign-in page; `note: element` writes the error there and answers `null`; `reload: [401]` reloads the page on those statuses; `status: true` makes the fallback "HTTP 500". |
| `client(opts)` | `api` with those options fixed: `const send = relayUI.client({ cache: 'no-store', session: true })`. |
| `el(tag, attrs, ...children)` | An element. `attrs` is a class name or an object of properties (`text` sets the text); children are nodes or text. Never markup. |
| `say(target, text, bad, opts)` | A status line (element or id), marked `err` or `ok`. `opts`: `plain`, `hide` (hidden while empty), `ms` (a toast that hides itself). |
| `confirm(text)` | A promise for the operator's yes or no: `if (!(await relayUI.confirm('Delete it?'))) return;`. |
| `button(text, fn, opts)` | A `type=button` button. `opts` is a class (`'primary'`, `'danger'`, `'quiet'`, `'sm'`) or `{ cls, error }`; with `error` a click clears it, disables the button while `fn` runs and shows what `fn` threw. |
| `buttons(error)` | `button` with the error line fixed: `const button = relayUI.buttons(showError);`. |
| `field(label, control, id)` | A `.field`: the label above the control, joined to it. |
| `pill(text, cls)` | A status pill with its dot (`'on'`, `'warn'`, `'err'`, `'idle'`). |
| `link(text, href)` | A link that opens in a new tab without handing the page to it. |
| `kv(dl, term, value)` | A `dt`/`dd` pair added to a `<dl>`, skipped when the value is empty. |
| `note(target)` | A function that shows an error line in `target` and hides it while empty. |
| `shown(node)` | True while the node's tab panel is open and the page is in front. |
| `portPicker(host, opts)` | Choose one port by its core ref (#546). Opens a drawer: gear on the left, its ports on the right, with a kind filter. `opts`: `value`, `kind` (fixes the connector kind), `label`, `disabled`, `onChange(ref, item)`. Returns `{ value(), set(ref), lock(kind) }`. |
| `channelPicker(host, opts)` | Choose one core channel by its ref. `opts`: `kind` (`audio`, `video` or `talk`) and `direction` (`source` or `destination`), either of which shows as text when fixed, plus `value`, `label`, `empty`, `disabled`, `onChange(ref, item)`. |
| `routeEditor(host, opts)` | Destinations down, sources across, one dot per route; audio takes a gain, video one source. Core stores the route and the module that owns the gear applies it. `opts`: `kind`, `provider` (only that module's ends), `gear` (only ends on that gear id), `anySource` (sources from every module), `perm` (the module permission that may route, as in its `routes.<id>`), `source(c)` and `destination(c)` (keep a channel), `extra(c)` (a node under a destination's name, such as a Remove button), `empty`, `onChange()`. Handle: `reload()`. Used by the NDI router, the Videohub routes on the Blackmagic page and the Audio bus page. |

Use them by alias (`const el = relayUI.el;`), never by copying. A helper core lacks is added to `relay-ui.js`, not
written into the module. `tests/test_ui_contract.py` fails on a module script that defines its own `can`, `api`,
`el`, `say` or `confirm`, on any direct `confirm()` call, on a page that loads a `relayUI` script without
`relay-ui.js` before it, and on any function body of more than 60 characters copied between two scripts. The copies
that were already there are listed in `tests/ui_contract_baseline.txt`, which may only shrink. The same test fails on any script other than `relay-ui.js` that calls core's `/api/admin/routes`, or reads its channel list or its cable gear list, to build a choice of its own; the baseline for that rule is empty, and the Network page's own list scripts are the only ones named in it.

These helpers are for a module's browser scripts. The server side has one pattern of its own for reaching core and
other modules, `modules.provide()` and `modules.using()` (see [Reaching another module](#reaching-another-module)); a
script never calls another module's routes by name either, since core pages and scripts do not name a module's routes.

## Shared styles for module pages

Every control is drawn by `app/static/components.css` (#463) on the tokens in `app/static/tokens.css`: buttons
(`button`, with `.primary`, `.danger`, `.quiet`, `.sm` and `.icon`), fields (one height per size: 34px, or 28px with
`.sm`), `.badge`, `.pill`/`.spill`, `.tabs` (mark the chosen tab with `aria-selected=true` or `.on`), `.seg`, tables
(`table` or `.tbl`), `.note`, `.notice`, cards (`main section`, `.pcard`), `.lrow` list rows and the switch. A module
places these; it does not redraw them.

- A module's styles live in its `static/*.css`, named in `admin_styles` (loaded by the operator panel while the module
  is on, for a user who may see one of its pages) or `crew_styles` (loaded by the crew page with its tab).
- Sizes, colours and radii come from the tokens: `var(--fs-sm)`, `var(--ctl-h)`, `var(--r-ctl)`, `var(--ok)`,
  `var(--danger)`, `var(--inset)`, `var(--font)`, `var(--mono)` and the rest in `tokens.css`.
- No `<style>` in an admin template, no CSS written from a script (`style.textContent`, `insertRule`, a `<style>`
  element, `cssText`), and no font stack outside `tokens.css`.

`tests/test_style_contract.py` enforces this with a ratchet: the literal px font sizes, hex/rgb colours and px radii
in each file may only go down from `tests/style_contract_baseline.txt`. `tests/test_browser.py` checks the drawn
result: on every operator page, in every preset, buttons are 34, 28 or 32px tall and text sits on the type scale.
The pages that keep their own sizes (the viewer pages, overlay, rundown, signage, boards and cue canvases, tally,
stage, the crew phone pages) are listed in `docs/ui-audit/standardization-spec.md`.

## Shared names, slugs, days and clock times

Core and every module compare names, make slugs and read days and times through two core files (#522, part of the
shared-concepts epic #521, audit in `docs/reference/shared-concepts-audit.md`):

- `app/text.py`: `same_name(a, b)` and `fold()` (case and spacing ignored) for matching a scene, cue, team, position or
  tag by name; `plain()` and `strip_accents()` for fuzzy matching; `slug(name, taken)` for a new address.
- `app/timewin.py`: **days count from Sunday = 0** everywhere (`weekday(d)`, never Python's `d.weekday()`, which
  counts from Monday); `is_clock`, `clean_clock`, `minutes` and `us_clock` for "HH:MM"; `window_start(now, start,
  end, day_ok)` for a daily window, including one past midnight.

Import the function (`from ...text import same_name`) rather than the module where a function has a local named `text`.
`tests/test_shared_concepts.py` fails on a new `.casefold()`, `unicodedata.normalize(`, `[^a-z0-9]` slug pattern,
`.weekday()` or HH:MM pattern outside those two files, and on a new import of another module's package (`from ..signage
import store`: use `modules.using("signage.content")`, see [Reaching another module](#reaching-another-module)); a use that is right is marked `# allow-shared: <why>` on its
line. Its baseline, `tests/shared_concepts_baseline.txt`, may only shrink.

**Time columns are ISO UTC text** (#529): a new column is `<name>_at text`, written with `db.now()` or `db.iso(ts)`
(`2027-01-15T08:00:00Z`) and read with `db.epoch(text)` when arithmetic or a page counting in seconds needs a number.
The test fails on a new `*_ts` or `*_epoch` column or an `_at` column typed real or integer. A session of the remote
intercom is a row of `intercom_sessions` with `via = 'remote'`, not a table of its own.

## Named schedules and the Relay time zone

One time zone, set under **Settings > System** (config `timezone`, `config.timezone()`), is read by every schedule,
signage slot, board row, call time, YouTube rule and Planning Center fallback (#526, part of #521). A module has no zone
setting of its own; an older one is carried over on load.

A **named schedule** (`app/schedules.py`, config `schedules`) is a name plus weekly windows and one-off dates, and can also
be open around each service time of today's Planning Center plan (`plan_times`, with minutes before and after). The
Schedules page edits them. A feature that runs on a schedule stores a **ref**, `schedule:relay:<id>` (`schedules.ref(id)`,
`ref_id`, `find`), in place of its own days and times, and asks `schedules.is_open_local(...)` or `schedules.windows(...)`:

- the engine's start and stop (`engine_schedules`, a list of refs), a signage slot (`signage_slots.schedule_ref`), a
  board rotation row (`schedule`) and a crew call time (`crew_calltimes.schedule_ref`, N minutes before each window
  starting today) pick one. Each keeps its own runner and rules for what to do when the window starts.
- The plan times come from Planning Center as `modules.provide("schedules.plan_times", fn)` (`fn(frm, to)` gives UTC
  `(start, start)` pairs); core passes `scheduler.plan_source()` and copes with None while the module is off.
- A module that picks schedules says where, so the Schedules page can list it and refuse to delete one in use: provide
  `schedules.used.<module>` (a function taking the ref and returning `[{"where", "what"}]`) in `attach()` and withdraw it
  in `detach()`. Core's own users (the engine, boards) are listed by `scheduler.used_by`.

## Who a person is in another product

`users` is the one person record, and `app/directory.py` (#524, part of the shared-concepts epic #521) says which person
in a provider's system a user is. The link lives in core (`person_links`: user, provider, the provider's id, the saved
name, how it was matched and who confirmed it; one link per user and provider, one user per provider id), so it works
whichever modules are on:

- `directory.user_for("pco", id)`: the Relay user for a Planning Center person, or None.
- `directory.links("pco")`: `{Planning Center id: user id}`; `directory.external_id(user_id, "pco")` goes the other way;
  `directory.person(user_id)` gives the user and every link they have.
- A person is named by a ref: `person:relay:<user id>` or `person:pco:<Planning Center id>` (`directory.ref`,
  `parse_ref`, `user_from_ref`). A module that stores a person stores a ref in a `*_ref` column; it does not add a
  `pc_person` or `person_id` text column (`tests/test_shared_concepts.py` fails on one).
- A provider module offers the people it knows with `modules.provide("directory.<provider>", fn)` (`fn()` returns
  `{"id", "name"}` dicts); Planning Center provides `directory.pco`, and `directory.people("pco")` is `[]` while it is off.
- Matching names to people stays in Crew (`people.scan`, fuzzy and confident only); it writes links with
  `directory.link()`, and the admin accept, dismiss and unlink actions go through the directory too. With Crew off,
  existing links keep answering for intercom, the rundown and parent paging; new ones are made when Crew is on.

### Teams and positions

Teams and positions (#525, step 4 of the epic) are named the same way: `team:pco:<id>` and
`position:pco:<team id>/<position id>` (`directory.team_ref`, `position_ref`, `parse_item`, `team_of`). A module that
stores one keeps the ref in a `*_ref` column and the name it had when saved in the column beside it; it does not add a
free-text `team` or `position` column on its own (`tests/test_shared_concepts.py` fails on one that has no `*_ref`).

- A provider module offers its teams with `modules.provide("directory.<provider>.teams", fn)` (`fn()` returns
  `{"id", "name", "positions": [{"id", "name"}]}` dicts, and `[]` until it has read them). Planning Center provides
  `directory.pco.teams` from its followed service types.
- `directory.sync("pco")` caches them in `directory_items`, announces `directory.changed` when one was added, renamed
  or went missing, and does nothing while the list is empty, so what was cached stays. `directory.teams()`,
  `positions()` and `item(ref)` read the cache, so a saved name still shows with Planning Center off.
- `directory.describe(ref, saved_name)` gives `{"ref", "name", "missing", "unmatched"}`: the current name, a ref the
  provider no longer lists, or a name that never matched one. Show those; never ignore them.
- `directory.match_name(kind, text)` finds the ref for a typed name (`text.same_name`, and only when exactly one
  matches); `directory.same_item(...)` compares saved ones by ref, else by name. A module rewrites its own saved names
  after a rename when it hears `directory.changed` (Crew: `refs.refresh`; the rundown: `refresh_saved`).
- Admin pages pick from the list and the server checks the pick (`crew/refs.py: clean_pick`); a typed name from before
  is matched when Planning Center's teams arrive (`refs.adopt`) and otherwise stays a name and is flagged on the page.
- Crew call times, checklists, leads and resources, and the rundown's mic mapping and channel assignments, are saved this
  way. A team ref is everyone on the team; a position ref is that position only. Crew's `crew_role` (the job a person
  fills, no longer called `role`) defaults to their usual Planning Center position, learned from the plans they are
  scheduled on, and an admin's own role replaces it.

## Reaching another module

One pattern for a module to give core or another module something, and for them to ask for it (#523, part of the
shared-concepts epic #521). Nothing imports a module: not core, and not another module.

```python
from app import modules                      # core's registry package

# In the module that has the thing, in attach(); the registry takes it back when the module is switched off,
# and detach() may call withdraw() too:
modules.provide("lighting.cues", fire)
modules.withdraw("lighting.cues")

# Anywhere that wants it, at the moment it needs it (not at import and not in attach(), so it does not matter which
# module came on first):
fire = modules.using("lighting.cues")        # None while the provider is off
if fire is None:
    return {"ok": False, "error": "Lighting is off."}
```

A name is `<area>` or `<area>.<what>`, lower case. The owner is the module the `provide` call is made from, so turning
that module off removes everything it provided (the registry calls `withdraw_all(owner)` after `detach`). Providing a
name another module holds replaces it, and is logged. `modules.using_all("screen_pages.")` returns every capability
under a prefix, by the rest of the name. `modules.provided()` lists them all with their owners (tests).

Other names offered today include `crew.notify` (crew: `(message, user ids)` sends a notification that buzzes until
read; the audio mixers' watchdog uses it), `plan.rundown_now` (Planning Center: the rundown with each mic's channel and
person) and `gear.<id>` / `routes.<id>` (below).

To hear about something instead of asking for it, `modules.listen(topic, fn)` in `attach()`, `modules.unlisten(topic, fn)`
in `detach()`; the provider calls `modules.announce(topic, *args)`. Planning Center announces `plan.changed` when the
followed plans change (the crew's people matching and the ProPresenter link listen); neither imports it.

A provider offers a function (`lighting.cues`), a small object (`ndi` is a dict of functions) or a namespace
(`signage.content`). It never has to offer a whole package: offer what an asker needs. The names Relay's own modules
provide:

| Name | Provided by | What it is |
| --- | --- | --- |
| `lighting.cues` | lighting | `async (cue, request, *, by_name, source) -> {"ok", "error"}`; core's `fire_cue` calls it |
| `obs.scenes` | obs | `async (name, *, source) -> {"ok", "error"}`; core's `switch_scene` calls it |
| `reaper.recording` | reaper | `async (command, value, *, source) -> {"ok", "error"}`; core's `control_recording` calls it |
| `gear.<module id>` | blackmagic | `{"can_rename": fn(key) -> bool, "rename": fn(key, name)}` for the channel ends a module can rename on its gear (core's rename flow, #543: a Videohub label goes to the router; a module without it is "Rename in Relay only"); `rename` raises ValueError when the gear refuses |
| `ndi` | ndi | `{"sources", "subscribe", "register_output"}`; core's `ndi_sources`, `ndi_subscribe`, `ndi_register_output` call them |
| `screen_pages.<query>` | lighting, obs | `async (request) -> response or None`; `/screen` asks each |
| `plan.label`, `plan.starts_at` | planning_center | today's plan title; when the next service starts (core's `plan_label()` and `service_starts_at()`) |
| `plan.service_types` | planning_center | the service types a panel may show or control |
| `plan.api` | planning_center | the client code (`client_from`, `connected`, `PlanningCenterError`, `operator_message`) |
| `plan.follower`, `plan.feed`, `plan.control`, `plan.rundown_now` | planning_center | the followed plans, the Service feed, LIVE control and today's rundown |
| `propresenter.follower`, `propresenter.api` | propresenter | the connection and its helpers (`configured`) |
| `propresenter.feed_extra` | propresenter | adds the plan item ProPresenter has up to the Service feed |
| `signage.content` | signage | a namespace: `store`, `media`, `live`, `takeover`, `schedule`, `sources` |
| `signage.source.<kind>` | planning_center, signage_web | a kind of playlist item: a dict of `label`, `resolve`, `describe`, `choices` (signage's `sources.py`) |
| `signage.layer.tag` | propresenter | the schedule's "tag" layer: `(screen, now) -> {"playlist_id", "why"} or None` |
| `dante.routing` | dante | `() -> [device, ...]`: each Dante device as last scanned (`name`, `reachable`, `transmit`, `subscriptions`); read-only, the signal map joins a patch list to it |
| `unifi.service` | unifi | who is on the network (the crew's Wi-Fi check-in) |

**Copes with it being off.** `using()` is None while a module is off, and a module that works with another (it names it
in `uses`) says what it does then: shows nothing, refuses with a sentence, or waits. Do not keep what `using()` returned
in a variable that lives past the call, since the provider can be switched off in between.

**When two modules are one feature.** A module that is the other half of another (the remote intercom is the same
feature as the intercom: its rooms, devices, presence and watchdog) may import it, listing it in `uses`. Such a use goes
on the baseline in `tests/shared_concepts_baseline.txt` with the reason; the baseline may only shrink.

## Rules

1. **Imports go one way, and never sideways.** A module may import core (`app.config`, `app.hub`, `app.engine`,
   `app.panels`, `app.alerts`, ...). Core never imports a module: it reaches modules only through the registry and
   what modules provide by name. If core needs to call into a module, the module provides a capability (`provide` in
   `attach()`), or it is a method on `Module` that the registry calls for every module that is on. A module never imports another
   module's package: it asks for what it needs with `using()` (see above).
2. **A module that works with another names it in `uses`** and copes with it being off (`using()` gives None). It
   may import only the modules it names, and `tests/test_shared_concepts.py` allows no new such import.
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
  name in `uses` (and `tests/test_shared_concepts.py` fails on any import of another module's package that is not on its
  baseline);
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
