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
| Logging | `tracing`, filter from `RELAY_LOG` (default `info`), to stderr. A log file and the event feed for #647 and #648 arrive with #656 |
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
| `listeners` | `listeners()`, opened by #653's listener code |
| `attach`, `detach`, `provide`, `using`, `withdraw` | `attach`, `detach`, `Host::provide`, `Host::using`, `Host::withdraw_all` (a capability is an `Arc<T>`, found by name and type) |
| `start`, `stop` (async) | `start`, `stop`, boxed futures so the trait can be a `dyn` |
| `health` | `health`, a `Health` whose detail is a `&'static str` |

The registry's life cycle is Python's: `attach` runs at startup for every module, `start` only for a module that is on,
`set_enabled` starts or stops it now and saves `modules.<id>.enabled`, `stop_all` runs in reverse order. A module that
fails to start is logged and left stopped; the others still start.

Not in the trait yet, because they need what a later issue builds: admin pages, crew tabs, user pages, permissions and
traffic classes (#654, #655 and #653 bring templates, accounts and ports), `test` and `status`, boards and pills (#660),
`network_changed`. Each is added to the trait with the issue that needs it, with its Python meaning unchanged.

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
the Rust `netsock` that routes each kind of traffic to its port, which arrives with #653. When it does, the same test checks
that no module source uses `std::net` or `tokio::net` directly, with a reasoned exception list like today's.
Adding a module is: `cargo new native/modules/<id>` as `relay-module-<id>`, one dependency line and one `Arc::new(...)` in
`relay-server`, and `tests/test_rust_modules.py` passes or says what is wrong.

## Config and database, read unchanged

- **The folder** is found as `app/paths.py` finds it: `RELAY_HOME`, the folder of `RELAY_CONFIG`, a `config.json` in the
  working directory, then the per-user default (Application Support on macOS, `%APPDATA%\Relay`, `$XDG_DATA_HOME/relay`).
- **config.json** is held as a JSON value with key order kept, so a key the Rust server does not know (a newer Python's, a
  module that is not compiled in) is written back as it was. A save is whole-file and atomic: a temp file beside it, owner-only,
  renamed over. A missing file is an empty config; a file that is not a JSON object stops the server rather than being overwritten.
- **Secrets** are `enc:v1:<kid>:<data>` (`app/secretbox.py`: AES-256-GCM, the key in the Keychain or Credential Manager, or
  `secret.key` on Linux). `relay-core` leaves them sealed. Opening them needs the same key store from Rust (`keyring` and
  `aes-gcm`), and is #656's job with the settings. Until then a Rust save writes the sealed text back untouched.
- **Module switch**: `modules.<id>.enabled`. A module with no switch is off in Rust. Python writes every switch at startup
  (and switches on a module that was already set up), so an install that has run Python has them all. A fresh install of the
  Rust server has none, and #656 decides the first-run rule.
- **The database** is SQLite in WAL mode, `relay.db` beside config.json, with the `schema_migrations(namespace, version)` table
  recording which numbered `.sql` files of the core and of each module have run. `sqlx` (#655) reads the same table and runs
  the same files, so neither server re-runs a migration. The migrations stay SQL files in the repository, shared by both servers,
  and a migration is never edited after it ships. While both servers can open one database, a schema change must work for both.

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

## Questions for later issues

- **The first-run switch rule** for a Rust-only install (#656).
- **Axum's state**: `Registry::routes()` returns a `Router<()>`; shared state (accounts, the hub) will be a typed `AppState` that
  modules receive through `Host`. Settled in #655 and #656 when there is state to share.
- **Windows config permissions**: Python restricts config.json with an owner-only ACL; the Rust save sets mode 0600 on Unix
  and leaves the ACL for #656.
