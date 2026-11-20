"""Dante admin routes (#257). Mounted by the module registry, so each answers 404 while the module is off."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ... import audit
from .. import require
from .service import service

router = APIRouter()
VIEW = [Depends(require("dante.view"))]
MANAGE = [Depends(require("dante.manage"))]


@router.get("/api/admin/dante", dependencies=VIEW)
def admin_state():
    return service.snapshot()


@router.post("/api/admin/dante/scan", dependencies=MANAGE)
async def admin_scan(request: Request):
    await service.rescan()
    audit.note(request, "dante.scan", "Looked for Dante devices again")
    return service.snapshot()
