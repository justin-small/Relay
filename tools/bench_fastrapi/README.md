# FastAPI vs fastrapi benchmark

Same seven routes on both frameworks (`app_fastapi.py`, `app_fastrapi.py`), driven by [oha](https://github.com/hatoo/oha).
`compat.py` lists which FastAPI/Starlette names Relay's `app/` imports that fastrapi lacks.

```bash
uv venv --python 3.14 /tmp/fa && uv pip install --python /tmp/fa/bin/python fastapi uvicorn jinja2 httpx
uv venv --python 3.14 /tmp/fr && uv pip install --python /tmp/fr/bin/python maturin pydantic jinja2 starlette python-multipart itsdangerous \
  "git+https://github.com/ppmpreetham/fastrapi"      # PyPI's 0.2.11 has no StaticFiles, templates, WebSocketDisconnect or Response
cargo install oha
python run.py --fastapi-python /tmp/fa/bin/python --fastrapi-python /tmp/fr/bin/python
/tmp/fr/bin/python compat.py
```

Not part of the test suite (no `test_` prefix). Results from the first run are in the pull request or chat that added this.

## Crew router, before and after

`crew_run.py` serves Relay's real `app/modules/crew/routes.py` (66 routes) on FastAPI+uvicorn and on fastrapi, with
only the framework import lines swapped (`crew_app.py`), checks that both answer the same, then loads eight routes.
Sign-in is a stand-in on both sides, so cookie/token checks in `app/main.py` are not measured. Needs Relay's
requirements in both interpreters (`uv pip install -r requirements.txt`).

```bash
python crew_run.py --fastapi-python /tmp/fa/bin/python --fastrapi-python /tmp/fr/bin/python --oha "$(which oha)"
```
