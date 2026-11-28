# The Rust server: design (#652, part of #650)

This settles the shape before anything is ported, so each later issue (#653 to #665) fits it instead of inventing its own.
The strangler approach from #650 stands: the axum front door (`native/relay-front`, #651) forwards what is not ported to
the running FastAPI, route by route. Everything below is for the Rust side of that line.

## Decisions

| Topic | Decision |
|---|---|
| Workspace | One Cargo workspace for everything Rust under `native/`: `native/Cargo.toml`, one `Cargo.lock`, one `target/`, one `deny.toml`. `desktop/src-tauri` stays its own until #664 |
| Crates | `relay-core` (library, knows no integration), `relay-server` (the program), `modules/<id>` (one crate per integration, `relay-module-<id>`), beside the existing `relay-bus`, `relay-ndi`, `relay-rf`, `relay-helper`, `relay-front` |
| Modules | One crate each, all compiled into the server, switched on and off at run time like today. Not feature flags and not plugins loaded at run time |
| The rule | Only `relay-server` depends on a module crate, and no module depends on another. `tests/test_rust_modules.py` enforces it in CI |
| Config and data | `config.json` and `relay.db` are read in place. Same path rules, same keys, same sealed secrets, same migrations table. A Python install upgrades by starting the Rust server, and can go back |
| Errors | One `AppError` with a fixed sentence per code. A response is `{"detail": "<sentence>"}`, never an exception's text. The cause goes to the log |
| Logging | `tracing`, filter from `RELAY_LOG` (default `info`), to stderr. A log file and the event feed for #647 and #648 are not part of #656 (settings and streams); they come with the first issue that needs them |
| Shutdown | One `Shutdown` signal. Ctrl-C or SIGTERM sets it, modules stop in reverse order, and the process exits after 10 seconds regardless |

## The workspace

```
native/
  Cargo.toml  Cargo.lock  deny.toml      one workspace, one lockfile, one policy
  relay-core/                            the module trait, registry, errors, config, paths, shutdown
  relay-server/                          main.rs, modules.rs (the only list of modules)
  modules/<id>/                          relay-module-<id>; none yet, the first arrive with #661
  relay-bus/crates/{relay-bus,relay-bus-cpal,relay-bus-py}
  relay-ndi/crates/{relay-ndi,relay-ndi-py}
  relay-rf  relay-helper  relay-front
```

The server links `relay-bus` and `relay-ndi` as ordinary path dependencies, with no PyO3 in between (#657, #658). The
`-py` crates stay in the workspace, because maturin builds the wheels the Python server still imports, but they are not in
`default-members`, so `cargo build` and `cargo test` skip them (linking a Python extension needs libpython). Build and
test them explicitly: `cargo clippy -p relay-bus-py`.

Profiles are per workspace, so the old per-crate settings moved to the root: the bus, NDI and RF crates and every
dependency build optimised in dev and test (the tests run an hour of audio in seconds); the server's own crates do not.
The helper's size-optimised, abort-on-panic release is the `release-helper` profile:

```bash
cargo build --profile release-helper -p relay-helper --locked --target <triple>   # target/<triple>/release-helper/
```

CI, the release build, `tests/run_all.py`, `tests/run_security.py` and `tools/build_bus_wheel.py` run from `native/` now.
`relay-front` loses its own `release` profile (LTO, one codegen unit); it is a spike and does not ship.

## Modules

Python's `Module` (`app/modules/__init__.py`, `docs/modules.md`) maps to the `Module` trait in `relay-core/src/module.rs`:

| Python | Rust |
|---|---|
| `Manifest` (id, name, version, summary, settings_key, guide, uses, renamed_from, section) | `Manifest`, a `static` in the module crate |
| `defaults`, `clean`, `secret_settings` | the methods of the same names, on `serde_json::Value` |
| `routers()` mounted behind `require_enabled` | `routes()`; `Registry::routes()` wraps each module's router so it answers 404 while off |
| `listeners` | `listeners()`, opened by the server's listener code (#653, "Listeners and TLS" below); `Listener::traffic` names the class that places it |
| `attach`, `detach`, `provide`, `using`, `withdraw` | `attach`, `detach`, `Host::provide`, `Host::using`, `Host::withdraw_all` (a capability is an `Arc<T>`, found by name and type) |
| `start`, `stop` (async) | `start`, `stop`, boxed futures so the trait can be a `dyn` |
| `health` | `health`, a `Health` whose detail is a `&'static str` |

The registry's life cycle is Python's: `attach` runs at startup for every module, `start` only for a module that is on,
`set_enabled` starts or stops it now and saves `modules.<id>.enabled`, `stop_all` runs in reverse order. A module that
fails to start is logged and left stopped; the others still start.

Not in the trait yet, because they need what a later issue builds: crew tabs, user pages, `test` and `status`,
`network_changed`. (Admin pages, permissions and traffic classes are in: #719, #655 and #653.) Each is added to the trait with the issue that needs it, with its Python meaning unchanged.

### Admin pages (#719)

`Module::admin_pages()` returns `AdminPage { id, title, template, icon, permission, tab_of }` (empty `permission` is
`<module id>.view`; `tab_of` is `"network"` or `"signage"` for a tab of a core page), and `admin_scripts()`, `admin_styles()` and
`admin_tabbed()` are Python's fields of the same names. `Registry::admin_view(can)` is `admin_nav`, `admin_tabs`, `admin_scripts`
and `admin_styles` in one call: only modules that are on, only pages `can` allows, one sidebar entry per module under its
`NAV_SECTIONS` heading.

The server serves `GET /admin` itself (`relay-server/src/accounts/page.rs`): `_page_gate`'s sign-in, forced password change and
"members go home" answers, then `admin.html` rendered by minijinja with the same context Python gives it (`me`, `module_nav`,
`module_tabs`, `module_scripts`, `module_styles`, `look`). Templates, scripts and styles are still read from `app/modules/<id>/`,
and `statics.rs` serves them with the caching headers it serves every static file with.

While a module is still only in Python, the page lists it too: the server asks the app for `GET /api/internal/front/admin`
(`ModuleRegistry.admin_listing`: every module that is on, its pages with the permission of each, its scripts and styles) and
cuts it down to the signed-in user the way `admin_view` does. A module the server has is never listed from the app, so a ported
module's page is drawn from the Rust declaration alone and the app's copy is ignored. With no account yet, `/admin` is still the
app's first-run form (`/admin/setup` stores the OpenAI key, which needs the sealed store). When #664 removes the app, the
listing call and the first-run hand-over go with it.

### How "integrations are modules" is enforced

Python's `tests/test_modules.py` reads imports with `ast`. Rust has no imports to read, but it has something stricter: a
crate can only use what its `Cargo.toml` lists. `tests/test_rust_modules.py` reads the Cargo files and the sources and fails when:

- a crate in `native/modules/` is not `relay-module-<id>` in a folder `<id>`;
- any crate except `relay-server` depends on a module crate (core cannot import a module, a module cannot import a module);
- a module depends on anything in the workspace except `relay-core`, `relay-bus` and `relay-ndi`;
- the server's dependencies on module crates and `relay-server/src/modules.rs` disagree (a module that is never compiled
  in, or one that is listed and does not exist);
- the sources of `relay-core`, or of `relay-server` outside `modules.rs`, name a module crate.

The rule "core never opens a network connection of its own" (`CORE_NETWORK`, `MODULE_SOCKETS`) has no Rust form yet: it needs
the Rust `netsock` that routes each kind of traffic to its port. #653 ported only the half the listeners need (which address a
listener binds, `relay-server/src/netroute.rs`); the outbound half (connect, UDP, multicast) arrives with the first Rust module
that opens a connection (#661). When it does, the same test checks that no module source uses `std::net` or `tokio::net`
directly, with a reasoned exception list like today's.
Adding a module is: `cargo new native/modules/<id>` as `relay-module-<id>`, one dependency line and one `Arc::new(...)` in
`relay-server`, and `tests/test_rust_modules.py` passes or says what is wrong.

## Config and database, read unchanged

- **The folder** is found as `app/paths.py` finds it: `RELAY_HOME`, the folder of `RELAY_CONFIG`, a `config.json` in the
  working directory, then the per-user default (Application Support on macOS, `%APPDATA%\Relay`, `$XDG_DATA_HOME/relay`).
- **config.json** is held as a JSON value with key order kept, so a key the Rust server does not know (a newer Python's, a
  module that is not compiled in) is written back as it was. A save is whole-file and atomic: a temp file beside it, owner-only,
  renamed over. A missing file is an empty config; a file that is not a JSON object stops the server rather than being overwritten.
- **Secrets** are `enc:v1:<kid>:<data>` (`app/secretbox.py`: AES-256-GCM, the key in the Keychain or Credential Manager, or
  `secret.key` on Linux). `relay-core` leaves them sealed; `relay-config` (#656, below) opens and seals them.
- **Module switch**: `modules.<id>.enabled`. A module with no switch is off in Rust. Python writes every switch at startup
  (and switches on a module that was already set up), so an install that has run Python has them all. A fresh install of the
  Rust server has none, and #656 decides the first-run rule.
- **The database** is SQLite in WAL mode, `relay.db` beside config.json, with the `schema_migrations(namespace, version)` table
  recording which numbered `.sql` files of the core and of each module have run. `relay-auth` (#655) does **not** replay the
  migrations: a new file gets `native/relay-auth/schema.sql` (the auth tables as they are after migrations 0001 to 0004, 0009 and 0010)
  and those six versions are recorded, and an existing file must already hold them or is refused with the version it lacks. The
  Python migrations stay in the repository for the Python server, and a migration is never edited after it ships. While both
  servers can open one database, a schema change to an auth table must work for both, and `schema.sql` changes with it.

## Errors, logging and shutdown

**Errors** (`relay-core/src/error.rs`). `ErrorCode` has nine codes, each with one status and one fixed sentence (the three Python
already sends keep their wording: "Sign in required", "Your role is not allowed to do that", "Not found"). An
`AppError` carries the code, a sentence (the code's, or a module's own `&'static str`), and a cause kept only for the log.
Because a sentence is a `&'static str`, text built from an error cannot become one; that is the CodeQL rule ("information
exposure through an exception", even through `to_string()` of a fixed sentence) held by the type system. `Internal`,
`Unavailable` and `Upstream` are logged at error level, the rest at debug.

**Logging** is `tracing`, so #647 and #648 can add spans and counters to the same stream. The filter comes from `RELAY_LOG`.

**Shutdown** is one `Shutdown` (a watch channel) cloned into every listener, task and module through `Host`. The server waits
for Ctrl-C or SIGTERM, sets it, and gives `stop_all` 10 seconds. A module's `stop` ends everything its `start` began.

## What this issue ships, and does not

Ships: this document, the workspace, `relay-core`, an empty `relay-server` that reads config.json, starts the modules that are
on (none yet) and waits for a signal, the tests (`native/relay-core/tests/core.rs`, `tests/test_rust_modules.py`), and CI, the
security scans and the release build moved onto the workspace.

Does not ship: a listener, TLS, templates, the database, accounts, or the front door's route list. Those are #653 to #656.

## Listeners and TLS (#653)

`relay-server` is now a library (`src/lib.rs`, so the tests drive it) and the program (`src/main.rs`). It does what `run.py`,
`app/serving.py`, `app/tls.py` and the `_Gate` middleware of `app/main.py` do, with the same sentences and the same rules.

| Python | Rust (`native/relay-server/src/`) |
|---|---|
| `app/serving.py` sockets, fallback, `plan`, `rebind` | `listen.rs` (bind, 80 to 8080 and 443 to 8443, notices), `serve.rs` (one group of sockets per listener, `Server::rebind`) |
| `app/netroute.py` `resolve` | `netroute.rs`, a pure function of the saved routes, the ports and the LAN addresses |
| `app/netinfo.py` ports and ids | `netinfo.rs`, read with `netdev`; the ids (`mac:<mac>`, `name:<name>`) match, because a saved route names one |
| `app/tls.py` | `tls.rs`: `rcgen` mints, `x509-parser` reads, a rustls `ResolvesServerCert` swaps the certificate for new handshakes. Same `certs/admin.crt` and `admin.key`, so either server reads what the other wrote |
| `_Gate` | `gate.rs`: admin routes refused on the viewer port, a module's port answers only its prefixes, the panel's headers, HSTS and the Permissions-Policy |
| `app/announce.py` | `announce.rs`, `mdns-sd`, on the viewer port actually bound |

TLS is `rustls` over a plain accept loop on hyper, not `axum-server`: `axum-server` serves one bound socket, and the listeners
here must move (a routed port's address changes) and hot-swap a certificate, which is a few lines on a loop of our own.

**The strangler.** Until the last route is ported the Rust server owns the sockets and the Python app runs behind it on one
loopback port (`python run.py --upstream-port N`, with the same `RELAY_FRONT_TOKEN` the server gets). What the server does not
serve goes to the app (`forward.rs`); the client's address and the port it reached travel in `X-Forwarded-For`,
`X-Forwarded-Proto` and `X-Relay-Port`, which the app trusts from a loopback peer and the server always overwrites. Two calls
(`front.rs`, `app/front.py`) carry what each side alone knows: the app tells the server its module listeners and reserved
prefixes, the server tells the app what it bound, so links, notices and the Network page say what is true. Both need the token
and neither is ever answered to a client.

**Rebinding.** Every 5 seconds (Python's `netinfo.POLL_SECONDS`) the server reads config.json and the ports again: a route changed
on the Network page moves a listener (old sockets close first, the port stays), a new LAN address re-mints the certificate if
its names no longer fit, the announcement follows the "discovery" class's address.

Left for later: outbound `netsock` (above); the module `Permissions-Policy` pages (microphone, geolocation) for routes the
server serves itself, which arrive with the modules that name them (the app still adds its own to what it serves); the
database-backed port names `netroute` uses in sentences when a port is unplugged (Rust says what `known_ports` in config.json
last saw); the desktop shell's `sidecar` messages (#664).

## Questions for later issues

- **The first-run switch rule** for a Rust-only install. #656 left it alone: Python's rule ("a module with no switch is on only if its
  integration was already set up") needs each module's `configured`, so it is settled with the first ported module (#661).
- **Axum's state**: `Registry::routes()` returns a `Router<()>`; shared state (accounts, the hub) will be a typed `AppState` that
  modules receive through `Host`. Settled in #655 and #656 when there is state to share.
- **Windows config permissions**: Python restricts config.json with an owner-only ACL; `relay-config` makes the same `icacls` call
  (`private.rs`). Not yet run on Windows.

## Static files and pages (#654)

`relay-server` serves `/static` and renders the Jinja templates itself, so a page no longer needs Python.

| Python (`app/main.py`) | Rust (`native/relay-server/src/`) |
|---|---|
| `RevalidatedStaticFiles` on `/static` and `/static/modules/<id>` | `statics.rs`: `tower-http`'s `ServeDir` for the file, then `Cache-Control: no-cache`, an `ETag` and `Last-Modified`, and a 304 on a matching `If-None-Match` |
| `templates.env.globals["static"]` (`_static_url`) | `pages::static_url`: `/static/<path>?v=<mtime in nanoseconds, hex>` |
| `Jinja2Templates` with `ChoiceLoader` + `PrefixLoader` | `pages.rs`: `minijinja` with a loader that reads `app/templates/<name>` and `app/modules/<id>/templates/<file>` (a module's template is `<id>/<file>`) |

The server finds the folder with `--app-dir` or `RELAY_APP_DIR` (default `app`), the one holding `static/`, `templates/` and `modules/`.

Where minijinja differs from Jinja2 and what `pages.rs` does about it:

- `None`, `True` and `False` print as Python prints them (`None`, not `none`).
- Escaping is markupsafe's five characters (`& < > ' "`), not minijinja's, which also writes `/` as `&#x2f;`.
- `tojson` sorts keys, uses `", "` and `": "`, writes non-ASCII as `\uXXXX` and escapes `< > & '`, as Jinja's does.
- Python's `str`, `list` and `dict` methods (`startswith`, `items`) come from `minijinja-contrib`'s `pycompat`.
- minijinja lists cannot be appended to, so `admin.html` builds its two lists without `.append` (an inline `if ... else none`, then `reject('none')`). A template must not mutate a list, in either server.

**The check.** `tools/capture_pages.py` wraps Jinja2's `render` while the Python test scripts run, and `tools/capture_pages_all.py` visits every
page with every module on (and renders the few pages no test server reaches, from contexts shaped like their handlers'). Each render is
saved as `native/relay-server/tests/pages/<template>.<hash>.json`: the template, the context and the HTML. `tests/pages.rs` renders the
same context with minijinja and requires the same bytes, and requires every template (a module's admin page counts when `admin.html`
included it) to be in some capture. `tests/pages_allowed.txt` lists reviewed differences; it is empty. Run
`.venv/bin/python tools/capture_pages.py` again after a template or a handler's context changes.

`crew/offline.html` has no Jinja; Python sends the file as it is.

Not in this issue: routes that render a page. The page handlers (their contexts come from accounts, settings and the modules) arrive with
#655, #656 and #661; each calls `Pages::render` with the context its Python handler builds. Until then the front door forwards those routes.

## Database, accounts and sessions (#655)

`native/relay-auth` is `app/db.py`, `app/users.py`, `app/audit.py` and the sign-in rules of `app/main.py`, on the same `relay.db`.
`sqlx` (SQLite, WAL, owner-only file) is the database layer; the crate is a library, so the tests open a real file.

| Python | Rust |
|---|---|
| `db.py` open, pragmas, migrations | `Auth::open`: a new file gets `schema.sql`; an existing one must hold `AUTH_VERSIONS` (see above) |
| `users.py` accounts, rank rule, last owner | `accounts.rs`: the same rules and the same fixed sentences (`Refusal`) |
| roles, `can`, `TOKEN_BARRED` | `perms.rs`: five roles, the catalogue, `register` for a module's permissions |
| sessions, trusted devices, API tokens | `sessions.rs`, `devices.rs`, `tokens.rs`: only the sha256 is stored, so a session or token made by either server works on the other |
| `audit.py` | `audit.rs`: `record` never fails the caller; 90 days, 200000 rows, sorted-key detail |
| `_LOGIN_FAILS` | `lockout.rs`: 5 failures lock an address for 10 minutes, 0.75 s per failure |

**Hashes.** New password and PIN hashes are argon2id (19 MiB, 2 passes). The scrypt hashes Python wrote still verify, and a user who
signs in with one is rewritten as argon2id. That is one way: Python cannot read argon2id, so going back to the Python server
asks those people for a new password. (The Python server signs sessions in the same way, so a session cookie the Rust server issued
works in Python.)

**Routes** (`relay-server/src/accounts.rs`, merged ahead of the forward to Python): `POST /admin/login`, `/admin/login/pin`,
`/admin/password`, `/admin/logout`, and under `/api/admin`: `me`, `me/password`, `me/pin`, `me/tokens`, `tokens`, `users`,
`trusted-devices`, `audit`. Each writes its own audit entry once it succeeds (Python's `_Gate` still writes the entry for the routes it
serves, with their notes, so a forwarded route is not audited twice). The route-to-action table is the `acc.note` call in each
handler; `AUDIT_ROUTES` stays in Python until the routes it names are ported.

**Not here.** First-run setup (`/admin/setup`, `/admin/import`) stays with Python: it stores the OpenAI key as a sealed secret, which
is #656. The pages (`/admin`, `/home`, the sign-in form) arrive with the settings and modules they list. Preferences are read (for `/me`)
but written by Python. Phones (`/api/admin/phones`) wait for the modules that hold a phone's other parts.

**The check.** `native/relay-auth/tests/auth.rs` (the rules of `test_users.py` and `test_audit.py`, a database the Python server made in
`tests/fixtures/python-made.db`, scrypt and argon2id) and `native/relay-server/tests/accounts.rs` (the routes of `test_auth.py`). The
Python tests keep testing the Python server until it is removed in #665; #663 replaces `TestClient` with a harness that drives the
running Rust server, and moves those three files over then.

## Settings, secrets and the event streams (#656)

Two new homes: **`native/relay-config`** (`app/config.py` and `app/secretbox.py`) and, in `relay-core`, **`hub.rs`** (`app/hub.py`);
`relay-server` gains `streams.rs`, `status.rs` and `ws.rs`.

**Secrets** (`relay-config/src/secretbox.rs`) are byte-compatible both ways with `app/secretbox.py`: the same `enc:v1:<kid>:<data>`
format, the purpose (`config:<field>`) as the GCM associated data, the same key ids, the same key stores (login Keychain, Windows
Credential Manager, or `secret.key` in the state folder; `RELAY_KEY_STORE=file|keyring`), the same fallbacks. A value Python sealed
opens in Rust and the reverse (`tests/config.rs` carries a value Python made; the other direction was checked with Python reading a
Rust-saved config). The keyring path (macOS Keychain, Windows Credential Manager) is written to Python's naming but only the file
store runs in CI; the Windows credential name is unchecked.

**Settings** (`relay-config/src/settings.rs`) load, validate and save config.json: defaults merged under the file, secrets opened on
load and sealed on save (a sealed value this computer cannot open is kept until a new one replaces it), a hand-typed secret sealed
at once, hot reload when the file's time changes, an atomic 0600 save, `replace` for removable entries, `redacted()` for the browser.
It does what `config.py` does on its own: ports (the panel never shares the viewer's), the languages and targets, the font, history
and lane clamps, the realtime and recording blocks, `host` and `admin_fqdn`. A block another Python module cleans (`audio_bus`,
`schedules`, `overlays`, `boards`, `panels`, `alerts`, `network`, `network_map`, `ai`, `metering`, `mixreport`, a module's own
settings, the legacy audio keys and `blocklist`) is **kept exactly as stored** until the issue that ports that module adds its
`clean`; so is a key nothing knows, in its place. `relay_core::Config` (the raw value the listeners read) stays; its `update` now starts
from the file as it is, so a module switch cannot undo what `Settings` saved since.

**The hub** (`relay-core/src/hub.rs`) has Python's channels, ring buffer, resync anchors every 20th delta, `Last-Event-ID` resume rules
(including the two resumes it refuses and answers with a snapshot) and finals-only feed, and the same frame bytes
(`id: <seq>\ndata: <compact JSON>\n\n`, key order `stream, lang, seq, delta, final, ts[, text]`). A frame is serialised once and the
same `Bytes` goes to every viewer. What differs: a subscriber is a bounded channel, one that falls 256 frames behind is dropped and
**its stream ends** (Python dropped it and left the connection waiting on heartbeats), so the browser reconnects and resumes; line
sinks run after the hub's lock is released; the blocklist is a `Redact` trait with a pass-through default, because `app/redact.py` ports
with captions (#659).

**Streams** (`relay-server/src/streams.rs`): `/stream` for `transcription`, `translation` and `status`, `/api/admin/status/stream`,
all with a 15 second heartbeat and `X-Accel-Buffering: no`, each a task that ends with the client or at shutdown. A module's own stream
(alerts, tally, lyrics, rundown) is `Streams::provide`d by that module when it is ported (#660, #661). The status feed's input is the
`Show` trait (`status.rs`), implemented by the engine, capture and panels as they move (#657, #659, #660); `Idle` until then.
`Accounts::require` is the guard for the admin stream (`session.view`).

**Websockets** (`ws.rs`): `serve(upgrade, shutdown, options, handler)` gives a handler a `Peer`; the pump pings, closes an idle socket
(1008), limits message size, closes with 1001 on shutdown and finishes a close as a handshake. The helper, intercom and remote intercom
routes (#497's hub, #662) are handlers on it.

**Mounted by `--rust-captions`** (#659, "Live captions" below). Without it the caption engine still publishes into the Python hub, so the
front door keeps forwarding `/stream` and the status stream to Python. The tests drive the
router on real sockets, including 500 held-open viewers (`five_hundred_held_open_viewers_all_keep_up`), the in-Rust counterpart of
`tools/stress.py sse`, which starts the Python app and so could not be pointed at these routes yet.


## Audio bus, routes and meters (#657)

`native/relay-audio` is `app/audiobus.py`, `app/routing.py`, `app/channels.py` and `app/metering.py` on `relay-bus` directly, with no
PyO3 between the routes and the engine. `relay-server` mounts the routes (`audio.rs`) and a loopback door for the Python app
(`internal.rs`, `pyapp.rs`). Start it with `relay-server --rust-audio` (or `RELAY_RUST_AUDIO=1`) **and** the app with
`RELAY_RUST_AUDIO=1`: two programs cannot both open the sound cards, so the server owns the engine and the app follows it. Without the
switch nothing changes.

| Python | Rust |
|---|---|
| `audiobus.normalize`, `first_setup`, `needs_setup`, `migrate_legacy_input` | `relay-audio/src/normalize.rs`: the same clamps, drops and `int()`/`float()` coercions, on JSON values, so config.json is read and written unchanged |
| `channels.py` (list, sync, ensure, merge, split, decline, forget, suggestions) | `channels.rs`, on migration 0014's tables |
| `routing.py` (wanted and actual, routes, presets, permissions) | `routing.rs`, on migration 0015's tables; a destination's module is a `Provider` |
| `audiobus.Bus` (apply, status, `play_to`, destinations, first setup) | `bus.rs`: `AudioBus` on `relay_bus::Engine` and the cpal backend, one supervisor thread, as the wheel's `Bus` did |
| `metering.Metering` | `metering.rs`: a `ProbeHub` probe per meter, up to 32 (one per lane) |
| the `/api/admin/audiobus*`, `metering*`, `routes*` and `network/channels*` routes | `relay-server/src/audio.rs`: the same permissions, fixed sentences, statuses and audit entries |

**The matrix cache (#648).** Python rebuilt the matrix with a query per channel on every page load (371 ms with a full bus). Here the
matrix is three queries, kept in memory, and the engine runs from it. It is invalidated by any write through this process (a route, a
saved matrix, a channel report: each has a generation number that goes into the cache key) and re-read at most [`MAX_AGE`] (2 s) after
the app behind the server writes a route. The routes in `relay.db` stay the stored truth, so either server reads them. Measured through
the panel's TLS with 32 lanes, three 8-channel outputs and 768 crosspoints: **p50 17.7 ms** (`tests/test_rust_audio.py`); in process, 6 ms.

**What stays Python until its module moves.** A module's gear: how to apply a route to it, read what it does, rename on it, and
which audio sources and outputs it registered. The server asks the app under `/api/internal/` (`app/front.py`, `pyapp.rs`) and refreshes
every 5 s, so a module switched on or off shows up. A module's network source or sender (NDI, Dante, AES67) is still opened and read by
Python: `app/audiobus_remote.py` keeps the API every consumer uses (`bus.lane(n).subscribe`, `play_to`, `output(id).inject`) and sends
it to `/internal/audio/*`: a lane is a streaming GET of raw float32, a source is POSTed to the engine, a sender is drained with `GET pull`,
and the state of both is POSTed back so the Audio bus page shows it. The door answers only a loopback peer with `X-Relay-Front`.

**Not done here.**

- The remote intercom (`app/modules/intercom_remote`) links its mixer to `audiobus.bus.engine`, a `relay_bus.Bus` in the same process.
  With the server owning the engine it has no such object; that follows when the intercom moves (#662).
- The status feed (`status.rs`) is mounted with `--rust-captions` (#659); its `sound=1` meters (`Show::sound`) are not implemented here yet.
- A new database made by the Rust server has no channels or routes tables (they are the Python migrations 0014 and 0015), so with
  `--rust-audio` the server says so and leaves the routes to the app, as it does for accounts (#655).
- PortAudio device labels ("Core Audio: Name") are mapped to cpal names by the part after the first `: ` and otherwise passed through;
  a device the engine cannot name is reported missing, as an unplugged one is. Not run on a real interface here (only the engine's fake
  sound card, and no sound card on the machine this was built on).
- `setup_owner` (accounts, #655) compares `x-admin-token` to `admin_token` in config.json as stored; Python seals that secret, so the
  setup token does not work against a config the Python app has started. Found while writing the end-to-end test; it signs in with a session instead.

## Alerts, stage messages, panels and screens (#660)

`native/relay-show` is `app/alerts.py`, `app/panels.py`, `app/panelfw.py` and `app/screens.py` with no HTTP in the crate, and
`relay-server/src/show.rs` mounts the routes. Start it with `relay-server --rust-show` (or `RELAY_RUST_SHOW=1`); without the switch
nothing changes. The signage modules (`signage`, `signage_web`) are the rest of #660; they are below.

| Python | Rust |
|---|---|
| `alerts.py` settings (`clean_stored`, `validate`, messages, replies) | `relay-show/src/alerts/rules.rs`: the same clamps, the same fixed sentences, on JSON values, so config.json is read and written unchanged |
| `alerts.Alerts` (press, ack, expire, stage messages, streams) | `alerts/mod.rs`: one state machine behind a mutex; `Env`, `Store` and `History` traits are what it needs from outside |
| `panels.Registry` (check-in, feeds, rotation, lock, return, service mode, tags, firmware offers) | `panels.rs`: the same rules; operator changes take an `Update` where Python took keyword arguments |
| `panelfw.py` (image checks, the `panel-firmware/` store) | `firmware.rs`: reads and writes the files a Python Relay wrote; the same sentences for a refused image |
| `screens.py` and the screens, places and keys tables | `screens.rs`, on migration 0008's tables (a database the Rust server made itself has none, and the app behind it serves them) |
| `/api/alerts/*`, `/api/panels/*`, `/stream?stream=alert`, `/api/admin/alerts*`, `/api/admin/panels*` | `show.rs`: viewer-port routes with no login, admin routes behind `alerts.*` and `panels.*`, each writing the audit entry Python's gate wrote |

**Time is injected.** `Clock` gives the alert and panel timers a monotonic second count and the history a wall-clock stamp, so the tests
move a clock instead of sleeping.

**What a stalled reader costs.** A sender, panel or screen stream is a bounded queue of 16 frames. A reader that stops reading is dropped
and its stream ends, so the browser or panel reconnects and is told the state (Python closed it the same way).

**`/stream`.** Only `stream=alert` is answered here; every other feed is forwarded to the app, which still publishes the captions.

**Not here yet, and why.**

- The Python modules that call alerts and panels (OBS, REAPER, Dante, NDI, ProPresenter, Planning Center, tones, alert events) keep their
  own in-memory copy of both. Run them against `--rust-show` only once #661 has ported them; until then the switch is for testing.
- The admin page's live updates for alerts and panels ride the admin status stream, which the caption engine still feeds in Python
  (#659). `GET /api/admin/alerts` and `/api/admin/panels` answer from Rust; the page sees changes when it asks.
- The Screens and Places admin routes, `/screen` and the board routes move with boards and signage.
- Planning Center's followed service types (a panel's `service_type`) are asked of the module, so until it is ported a panel cannot be set
  to a service through the Rust route.

### Boards (#660)

`relay-show/src/boards.rs`, `layout.rs`, `timewin.rs` and `schedules.rs` are `app/boards.py`, `app/layout.py`, `app/timewin.py` and the
part of `app/schedules.py` boards and screens ask: is a named schedule open now. They are pure functions on JSON values, so config.json is
read and written unchanged, and `tests/boards.rs` runs the rules of `tests/test_boards.py` that need no server (bounded loading, widget
settings checked against their definitions, pages, rotation, schedule rows, following, templates, create, update, export, import).

Two choices where Python asks something outside the crate:

- **Choices that come from outside.** The audio sources modules registered, and the channels a route button joins, are not known here, so
  those select fields are marked `"open"` and keep any plain string; Python would reset an unknown one to the default.
- **Schedules** are read as stored. The US zones' daylight-saving rule (since 2007) is in `schedules.rs`; a spring-forward time that does not
  exist moves to the next minute that does, and a repeated fall-back time takes the first, as Python's `localize` does. A schedule that
  adds the Planning Center plan's service times has only its own windows checked until that module is ported (#661).

**The board routes are not mounted.** `/api/admin/boards*`, `/board/<id>` and its data and press routes stay in the app, because each
needs something that is Python-only until #661: the widgets modules offer (lighting cues, ProPresenter follow), and the live state of the
screens asking for a board. Mounting the admin routes alone would drop module widgets from the Boards page's list. The rules are ready
for the day those routes move.

### Signage (#660)

`native/modules/signage` (`relay-module-signage`) is `app/modules/signage`: media, playlists, groups, screens, schedule slots, overrides,
takeovers, the alert banner, the player page and its event stream. It depends on `relay-core` only. What the server hands it through `Host`
(`provide_core` in `app.rs`): the database (`core.db`), the screens (`core.screens`, `relay_core::screens::ScreenDirectory`, implemented by
`relay_show::Screens`), the app folder (the player script), the addresses the machine is reached at, and, while `--rust-show` runs, the alerts
flashing now. `relay_core::timewin` and `relay_core::schedules` (moved out of `relay-show`, which re-exports them) read days from Sunday = 0,
daily windows and whether a named schedule is open, with the US daylight-saving rule.

The module applies the Python migrations 0001 to 0004 itself the first time it starts (namespace `signage` in `schema_migrations`, as Python
records them), so either server reads what the other wrote. Uploads are named by their sha-256 and typed from their first bytes; a thumbnail is
made by the browser and only stored. `Host::note` writes the audit entry Python's `audit.note` wrote.

**Not in the module yet**, because it needs another module or the admin shell:

- items from other modules' sources (ProPresenter announcement slides, Planning Center media, web pages and YouTube) are skipped until those
  modules are ported (#661), and so are the `signage.layer.takeover` and `signage.layer.tag` layers other modules provide;
- offering each screen to the NDI module as an output, the Schedules page's "used by" list, and the Boards widgets (`signage.takeovers`);
- the "bound port" a screen's address shows is the configured one, because the module cannot ask which port the listener really opened;
- `prune_mirror` keeps every backup copy while any `relay-*.db` snapshot exists, where Python reads each snapshot to see what it names.

### Signage: web pages and YouTube (#660)

`native/modules/signage_web` (`relay-module-signage_web`) is `app/modules/signage_web`: saved web pages and YouTube links, the checks that a
site allows being framed or a video embedded, and the two signage sources that play them. It depends on `relay-core` only. `links.rs` is
`links.py` (address cleaning, YouTube ids, what the player is told, framing headers, the saved list in config.json `signage_web`, so either
server reads what the other wrote); `check.rs` is the fetch and the two checks; `lib.rs` is the module and its four admin routes behind
`signage_web.view` and `signage_web.manage`, each writing the audit entry Python's route wrote.

While the module is on it provides `signage.source.web_page` and `signage.source.youtube` (`relay_core::screens::SignageSource`) and
withdraws them when it stops, so signage plays, describes and offers the links without naming this crate; a removed link or a switched-off
module makes a playlist skip the item, as in Python.

**Checks never reach into the network Relay runs on.** Every hop of a check (the address, then each of up to three redirects) resolves the
host with `relay_core::net::resolve_all` and is refused unless every address is public (`check::is_public`: not private, loopback,
link-local, shared, documentation, reserved or multicast, IPv4-mapped IPv6 judged as its IPv4). The connection then goes to the address that
was checked, not to a second lookup of the name, through `Host::net()` for the `signage_web` traffic class, so it leaves by the port the
operator assigned. TLS is checked against the bundled roots.

The "Web & YouTube" tab and `signageweb.js` are declared by the module (`admin_pages`, `tab_of: "signage"`, #719) and drawn by the server.
`tests/test_signage_web.py` stays as the check of the Python module until it is retired (#664).

### Planning Center (#666)

`native/modules/planning_center` (`relay-module-planning_center`) is `app/modules/planning_center`: the Personal Access Token client, today's plan
and its LIVE state (the follower), the Service feed, panels' Next / Previous / Take control / Release, the rundown for /screen, and plan files and
text slides for signage. It depends on `relay-core` only. Settings are read and cleaned as Python does, so an existing `config.json` loads
unchanged; the secret is sealed by the server's store. (The issue says OAuth; the Python module signs in with a token, and so does this.)

| Python | Rust (`modules/planning_center/src/`) |
|---|---|
| `planningcenter.py` `Client`, `pick_plan`, `timers`, `clean_stored` | `client.rs` (`Services`, one `Op` per call, so a test answers from a table), `plan.rs`, `settings.rs` |
| `pclive.py` `Follower` | `follower.rs`: same timers and rate sharing; time zones by `jiff` with its bundled database |
| `servicefeed.py`, `pccontrol.py` | `feed.rs`, `control.rs` |
| `rundown.py` | `rundown.rs` |
| `signagesync.py` | `signagesync.rs` (`PlanSync`) |
| `routes.py` | `routes.rs`, including the viewer-port press route and the two feeds |

What core gained: `relay_core::caps` (the plan, the panels, signage content, the directory, the channels, feed extras), `relay_core::text`,
`SettingsStore::replace` (a save only merges, so a key could not be removed), and `Module::streams` / `Module::stream`, which the server hands to
`/stream` while a module is on. The server provides the panels capability while `--rust-show` runs; signage provides its content capability
while it is on, and the module's `pc_text` source and plan capability only while Planning Center is on.

**Not here yet, and why.**

- **Directory and channels.** Neither exists in Rust, so refs are kept as saved and names are matched as typed (Python's rule while the
  directory is empty). The capability traits are ready for the day they do.
- **Python consumers of `plan.*`** (ProPresenter's link and `drive`, crew, parent paging, YouTube, mixers) still reach the Python module's
  names; `PlanCap` is what Rust consumers will use, and `drive` and the Publishing and Check-Ins calls come with them.
- **`plan.changed` / `directory.changed` announcements**, and the panel `targets` push when a service type is followed, need an event bus in
  the module host; the follower and feed already expose `on_change` and `on_offered_change` for it.
- **Health detail** is a fixed sentence, so "Following 2 service types" shows no number; an error shows one sentence, not Planning Center's reply.
- **The admin page's "who pressed"** is "Admin page": the module is not told the signed-in user's name.
- `/stream?stream=service` and `rundown` are served only where `/stream` is mounted (`--rust-captions`).
- Not run against a real Planning Center account.

## Live captions (#659)

`native/relay-captions` is `app/redact.py`, `app/realtime.py`, `app/transcribe.py`, `app/texttranslate.py`, `app/recorder.py`,
`app/exporting.py`, `app/demo.py`, the part of `app/audio.py` after the source, and what `app/engine.py`, `app/stream.py` and
`app/modules/captions` do with them. `relay-server/src/captions.rs` mounts the routes. Start it with `relay-server --rust-audio --rust-captions`
(or `RELAY_RUST_AUDIO=1 RELAY_RUST_CAPTIONS=1`): the room's audio is a lane of the Rust bus, so captions need `--rust-audio`, and without it the
switch says so and the app keeps captions. Without the switch nothing changes.

| Python | Rust |
|---|---|
| `redact.Redactor` | `redact.rs`: the same rules over `char`s (Python matches with one look-behind regex; the regex crate has none), as the hub's `Redact`. A property test checks a banned word is never on screen however the text is cut into deltas. |
| `RealtimeSession` and `ScribeSession`'s event handling | `transcript.rs`: when a line is committed, with no socket and no clock of its own (the caller passes the time) |
| `RealtimeSession._run` (connect, hello, audio, backoff) | `session.rs`, over `Connect` (two channels), so a test is a scripted server and the real one (`net.rs`) is a TLS WebSocket through `Host::net()` for the `translation` class |
| `transcribe.py` (`Backend`, `resolve`, the OpenAI and Scribe sessions) | `transcribe.rs`: a `SessionSpec` (URL, handshake, first frame, how audio goes) for each backend; a module registers its own with `Backends::register` |
| `TextTranslator`, `aimodels.resolve` | `translate.rs` (one request per language, all at once, in order, a backlog sent as one) and `aimodels.rs`, over `Chat` |
| `Recorder`, `exporting.py` | `recorder.rs` (the run directory Python wrote, `meta.json` and `lines.jsonl`) and `exporting.rs` (transcripts, pairs, fine-tune file, zip) |
| `AudioCapture`'s meter, gate, `soxr` resample, consumers | `capture.rs`: a windowed-sinc resampler, the speaking gate with its 320 ms lead-in, and a queue per session that evicts its oldest frame and counts it |
| `Engine`, `Stream`, `Pipeline` | `engine.rs`: `Captions`, over `Settings`, `Capture`, `Chat`, `Connect` and `Outside` |
| `demo.py` | `demo.rs` |
| `/api/admin/status`, `start`, `stop`, `target/{t}`, `/api/targets`, `/api/admin/recordings*`, `/stream`, `/api/admin/status/stream` | `relay-server/src/captions.rs`, each behind the permission Python asked and writing the audit entry Python's gate wrote |

**What changed and why.**

- **The capture is a tap on a bus lane, at 24 kHz**, converted on the bus's reader thread (`AudioBus::open_tap_at`). Python opened a sound card
  itself; here the bus owns it, so an unplugged input is the bus's to report and there is no reconnect loop in captions.
- **Times are in the one Relay zone** (config `timezone`), not the computer's: run ids, transcript times and the exports. The two agree
  on every install that set its zone to the computer's.
- **The settings change is followed within a second**, not in the route: Python's `/api/admin/config` still saves (it stays in the app until
  settings move) and the engine reads config.json each second. A changed lane, language or `realtime` block restarts the session; `stream_captions` is
  followed live; the AI models page and the keys move the translator only; the blocklist file reloads when its modified time changes.
- **JSON is written compact** (`lines.jsonl`, the pairs and fine-tune files); Python put a space after each `,` and `:`. Either reads the same.
- **A fatal error stops retrying** in both the session and the translator, as in Python; the translator's HTTP errors are `HTTP <status>: <detail>`
  (the same text `is_fatal` reads), and its other failures are fixed sentences, not an exception's text.

**Found while porting.** The backoff test caught that the first draft never reset the one-second backoff after a good connection; the
property test on the line-committing rules found nothing wrong with Python's, but its first draft assumed passthrough text comes out unchanged,
which `tidy` (closing up spaces) does not do, in Python either.

**Not here yet, and why.**

- The Python modules that follow Start (OBS, REAPER, ProPresenter and the rest) are told `stream.started` and `stream.stopped` through
  `/api/internal/stream/announce`, and what they add to the pages (pills, health) is read from `/api/internal/captions/outside`. They are ported by #661.
- **Python's own schedule still starts Python's pipeline** (`app/scheduler.py` calls Python's stream): leave the schedule off in the app while
  captions run here, or both open the audio. The scheduler is not in #659's list.
- `/api/admin/config`, `/api/admin/ai*` and the blocklist editor's save stay in the app; the engine follows what they save.
- The audio input is a bus lane, so a card that is not on the bus cannot be captioned here; Python's PortAudio source and the `lane` source
  are not ported.
- Not run against the live Vercel gateway or OpenAI from this Mac, as Python's Scribe session was not either (its wire format is the
  `@ai-sdk/gateway`'s); the tests run the real clients against local HTTP and WebSocket servers, and the session loop against a scripted one.
