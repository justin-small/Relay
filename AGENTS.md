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

## Run the whole suite before every push

CI runs every `tests/test_*.py` plus `tests/smoke_test.py` on three platforms, and a new route, setting or module
action often breaks a test you did not touch (a new `/api/admin` route must be listed in `tests/test_auth.py`).
Running only the tests for your area is not enough. Before each push:

```bash
for t in tests/test_*.py tests/smoke_test.py; do .venv/bin/python "$t" >/dev/null 2>&1 || echo "FAILED $t"; done
```

Nothing printed means it is safe to push.
