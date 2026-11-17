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

## Shared UI helpers come from relay-ui.js (enforced)

Shared UI helpers come from `app/static/relay-ui.js` (`window.relayUI`: `can`, `api`, `client`, `el`, `say`,
`confirm`). Do not copy a helper into a module script; if core lacks one, add it to core. `tests/test_ui_contract.py`
enforces this, and its baseline of older copies (`tests/ui_contract_baseline.txt`) may only shrink. See
"Shared helpers for module scripts" in `docs/modules.md`.

## Run the whole suite before every push

CI runs every `tests/test_*.py` plus `tests/smoke_test.py` on three platforms, and a new route, setting or module
action often breaks a test you did not touch (a new `/api/admin` route must be listed in `tests/test_auth.py`).
Running only the tests for your area is not enough. Before each push:

```bash
.venv/bin/python tests/run_all.py
```

It runs the files several at a time and ends with a line per failing file; exit 0 means it is safe to push.
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
