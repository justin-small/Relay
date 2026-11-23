# Working in Relay

## Integrations are modules (enforced)

Every integration with another product (ProPresenter, Planning Center, consoles, mics, lighting, streams,
anything that talks to something outside Relay) **must be a module** in `app/modules/<id>/` that the
operator can switch on and off. Never add an integration to core (`app/*.py`, `app/templates/`,
`app/static/`), and never import a module from core. Read `docs/modules.md` before starting one.

`tests/test_modules.py` enforces this in CI on every pull request; run it before committing:

```bash
.venv/bin/python tests/test_modules.py
```

If it fails, fix the design; do not add the new file to `CORE_FILES` or `CORE_NETWORK` to make it pass
unless the file really is core, and say why in the pull request.

In the Rust server (`native/`), an integration is a crate `native/modules/<id>` named `relay-module-<id>`, and only `relay-server`
depends on it. Read `docs/rust-server.md`; `tests/test_rust_modules.py` enforces it the same way.

## Shared UI helpers come from relay-ui.js (enforced)

Shared UI helpers come from `app/static/relay-ui.js` (`window.relayUI`: `can`, `api`, `client`, `el`, `say`,
`confirm`). Do not copy a helper into a module script; if core lacks one, add it to core. `tests/test_ui_contract.py`
enforces this, and its baseline of older copies (`tests/ui_contract_baseline.txt`) may only shrink. See
"Shared helpers for module scripts" in `docs/modules.md`.

## Shared styles come from components.css and tokens.css (enforced)

Operator and module pages use the components in `app/static/components.css` and the tokens in `app/static/tokens.css`;
no literal sizes or colours, no `<style>` in module templates and no CSS written from a script. A module's styles go
in its `static/*.css`, listed in `admin_styles` or `crew_styles`. `tests/test_style_contract.py` enforces this, and its
baseline (`tests/style_contract_baseline.txt`) may only go down; `tests/test_browser.py` checks the drawn sizes. See
"Shared styles for module pages" in `docs/modules.md` and the exceptions in `docs/ui-audit/standardization-spec.md`.

## Names, slugs, days and clock times come from text.py and timewin.py (enforced)

Compare names with `app/text.py` (`same_name`, `fold`, `plain`) and make slugs with its `slug`; read days and clock
times with `app/timewin.py`, where **days count from Sunday = 0** (`timewin.weekday(d)`, never `d.weekday()`).
`tests/test_shared_concepts.py` enforces this, and its baseline (`tests/shared_concepts_baseline.txt`) may only
shrink. See "Shared names, slugs, days and clock times" in `docs/modules.md`.

## Tests wait on events and run real code (enforced)

Python tests do not add fixed sleeps (use `wait_for`/`await_for`), `unittest.mock` or `monkeypatch`, hand-written
swaps of a module's functions (`routes.fetch = fake`), timeouts above the harness's 5 seconds, or loops that catch
`AssertionError` and retry. `tests/test_honest_tests.py` enforces this with a per-file count in
`tests/honest_tests_baseline.txt` that may only go down. A use that is right is marked on its line with a reason, e.g.
`# allow-sleep: shows no second push arrives` (a sleep only shows that something does *not* happen; a mock or
patch only stands in for a seam a test cannot reach, such as the OS network stack).

## Run the whole suite before every push

CI runs every `tests/test_*.py` plus `tests/smoke_test.py` on three platforms, and a new route, setting or module
action often breaks a test you did not touch (a new `/api/admin` route must be listed in `tests/test_auth.py`).
Running only the tests for your area is not enough. Before each push:

```bash
.venv/bin/python tests/run_all.py
```

It runs the files several at a time and ends with a line per failing file; exit 0 means it is safe to push.
It also runs `cargo clippy --locked --all-targets --workspace -- -D warnings` in `native/` (one Cargo workspace for all the Rust,
`docs/rust-server.md`), because CI fails on any warning and the Rust tests alone do not lint; run the same command while you work on a crate.
It first builds the Rust audio bus core (`tools/build_bus_wheel.py --if-stale`, the `relay_bus` wheel the app imports),
so Rust must be installed (rustup; on Linux also `libasound2-dev`).
`-k <part of a name>` narrows it while you work. CI also runs `node --test tests/js/*.test.mjs`, `cargo test` in
`desktop/src-tauri`, the browser test (`tests/test_browser.py`, skipped locally without Playwright), and fails
if coverage of `app/` drops below `fail_under` in `.coveragerc`.

## Run the security scans before every push

CI's Security workflow runs the same scans you can run locally: bandit (Python), gitleaks (secrets), actionlint
and zizmor (workflows) and cargo-deny (Rust). `python tests/run_security.py --install` prints the install
commands; `--install-hook` runs them before every `git push`.

```bash
.venv/bin/python tests/run_security.py
```

A new bandit finding is fixed, or accepted after review with `--rebaseline` (say why in the pull request).
Workflow actions are pinned by commit SHA with a version comment (`pinact run` does it). CodeQL runs on GitHub
only; read its alerts on the pull request.

## Writing a test

A test is a script in `tests/` named `test_*.py`. Use the shared checks in `tests/_harness.py` (`eq`, `check`,
`done()`), wrap each part in `with section("..."):` so one exception does not hide the checks after it, poll with
`wait_for`/`await_for` instead of a fixed `sleep` when waiting for something to happen, and make folders with
`tmpdir()` so they are removed. A fixed sleep is right only to show that something does *not* happen.
