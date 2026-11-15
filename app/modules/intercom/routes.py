"""Intercom's admin routes. Mounted by the module registry, so each answers 404 while the module is off."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import require
from . import store
from .livekit import supervisor

router = APIRouter()


@router.get("/api/admin/intercom", dependencies=[Depends(require("intercom.view"))])
async def intercom_view():
    """LiveKit's state and log tail. No key, secret or config text: they never leave the server."""
    return supervisor.view()


@router.post("/api/admin/intercom/restart", dependencies=[Depends(require("intercom.manage"))])
async def intercom_restart():
    await supervisor.restart()
    return supervisor.view()
