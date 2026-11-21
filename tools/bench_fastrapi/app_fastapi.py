"""The benchmark app on FastAPI + uvicorn. Same routes as app_fastrapi.py."""
import sys
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

HERE = Path(__file__).parent
app = FastAPI(docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(HERE / "templates"))
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


class Item(BaseModel):
    name: str
    qty: int
    tags: list[str] = []


def need_token(request: Request):
    if request.headers.get("x-token") != "secret":
        raise HTTPException(status_code=401, detail="no")
    return "ok"


@app.get("/json")
def json_(): return {"ok": True, "n": 1}


@app.get("/item/{item_id}")
def item(item_id: int, q: str = ""): return {"id": item_id, "q": q}


@app.get("/guarded")
def guarded(who: str = Depends(need_token)): return {"who": who}


@app.post("/echo")
def echo(body: Item): return {"name": body.name, "qty": body.qty * 2, "tags": body.tags}


@app.get("/page", response_class=HTMLResponse)
def page(request: Request):
    return templates.TemplateResponse(request, "page.html", {"rows": list(range(30))})


@app.get("/slow")
def slow():
    import time; time.sleep(0.01)  # a blocking call, like a device poll
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning", access_log=False,
                workers=int(sys.argv[2]) if len(sys.argv) > 2 else 1) if len(sys.argv) > 2 and int(sys.argv[2]) > 1 else \
        uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning", access_log=False)
