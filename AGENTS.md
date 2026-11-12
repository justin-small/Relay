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
