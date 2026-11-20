"""UniFi admin routes (#358). Mounted by the module registry, so each answers 404 while the module is off."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ... import audit, config
from .. import require
from .controller import clean_fingerprint
from . import history
from .service import KEY, clean_settings, service, settings

router = APIRouter()
VIEW = require("unifi.view")
MANAGE = require("unifi.manage")
FIELDS = ("host", "port", "site", "api_key", "fingerprint", "username", "password", "probe_target", "poll_s",
          "away_s")


@router.get("/api/admin/unifi", dependencies=[Depends(VIEW)])
async def admin_state() -> dict:
    return service.snapshot()


@router.put("/api/admin/unifi/settings", dependencies=[Depends(MANAGE)])
async def admin_settings(request: Request, payload: dict):
    """The controller's address, port and site, the API key (blank keeps the saved one), and how often to poll.
    A new address forgets the trusted certificate: it belongs to the old console."""
    cur = settings()
    new = dict(cur)
    for k in ("host", "port", "site", "poll_s", "away_s", "username", "probe_target"):
        if k in payload:
            new[k] = payload[k]
    for k in ("api_key", "password"):              # blank keeps the saved one
        if isinstance(payload.get(k), str) and payload[k].strip():
            new[k] = payload[k]
    if payload.get("clear_user") is True:
        new["username"], new["password"] = "", ""
    new = clean_settings(new)
    if "host" in payload and payload["host"] and not new["host"]:
        return JSONResponse({"error": "That is not an address or a name. Give the console's address, like "
                                      "192.168.1.1."}, status_code=400)
    if (new["host"], new["port"]) != (cur["host"], cur["port"]):
        new["fingerprint"] = ""
    config.save({KEY: {k: new[k] for k in FIELDS}})
    if new["api_key"] != cur["api_key"]:
        audit.note(request, "unifi.key", "Changed the UniFi API key")
    if (new["username"], new["password"]) != (cur["username"], cur["password"]):
        audit.note(request, "unifi.user", "Changed the UniFi local user", detail={"set": bool(new["username"])})
    if (new["host"], new["port"], new["site"]) != (cur["host"], cur["port"], cur["site"]):
        audit.note(request, "unifi.controller", "Changed which UniFi controller Relay reads",
                   target=new["host"], detail={"port": new["port"], "site": new["site"]})
    await service.check()          # answer with what the controller says now, so the page can show its certificate
    service.poke()
    return service.snapshot()


@router.post("/api/admin/unifi/trust", dependencies=[Depends(MANAGE)])
async def admin_trust(request: Request, payload: dict):
    """Trust the certificate the controller showed. The fingerprint must be the one Relay saw, so a page left open
    cannot trust a certificate that changed since."""
    fp = clean_fingerprint(payload.get("fingerprint"))
    if not service.untrusted:
        await service.check()
    if not fp or fp != service.untrusted:
        return JSONResponse({"error": "That is not the certificate the controller is showing now. Refresh and "
                                      "check again."}, status_code=409)
    config.save({KEY: {"fingerprint": fp}})
    audit.note(request, "unifi.trust", "Trusted the UniFi controller's certificate", target=settings()["host"],
               detail={"fingerprint": fp[:16]})
    service.untrusted = ""
    await service.check()
    return service.snapshot()


@router.delete("/api/admin/unifi/trust", dependencies=[Depends(MANAGE)])
async def admin_untrust(request: Request):
    config.save({KEY: {"fingerprint": ""}})
    audit.note(request, "unifi.untrust", "Stopped trusting the UniFi controller's certificate")
    await service.check()
    return service.snapshot()


@router.post("/api/admin/unifi/check", dependencies=[Depends(MANAGE)])
async def admin_check() -> dict:
    await service.check()
    return service.snapshot()


@router.get("/api/admin/unifi/history", dependencies=[Depends(VIEW)])
def admin_history(hours: float = 24):
    """Internet health over the last `hours` (up to 30 days), and the peaks over it."""
    hours = max(1.0, min(720.0, hours))
    try:
        return {"hours": hours, "rows": history.series(hours), "peaks": history.worst(hours)}
    except Exception:
        return JSONResponse({"error": "The history is not available right now."}, status_code=503)


@router.post("/api/admin/unifi/refresh", dependencies=[Depends(VIEW)])
async def admin_refresh() -> dict:
    """Read everything now, not on its schedule (the page's Refresh)."""
    await service.check()
    await service.extras(force=True)
    return service.snapshot()
