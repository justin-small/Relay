## What and why

## Checklist
- [ ] Any new integration is a module in `app/modules/<id>/` that can be switched on and off ([docs/modules.md](../docs/modules.md)); core imports no module
- [ ] `tests/test_modules.py` passes, and no file was added to `CORE_FILES` / `CORE_NETWORK` without a reason given above
- [ ] A per-user preference is checked (type, range, expected choice) before use and shown as text, never with `innerHTML` or `|safe` ([docs/modules.md](../docs/modules.md#per-user-preferences))
- [ ] Tests added or updated
