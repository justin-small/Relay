"""The interface every lighting program implements (#377). One backend is active at a time (setting `backend`).

A backend is written once here and gets cue pages, permissions, the audit trail and the `fire` hook for free. It
opens its connection through app/netsock.py (docs/modules.md) and never retries a press: a dropped connection may
mean the cue already happened, so the press fails and the operator decides.
"""
from __future__ import annotations

from dataclasses import dataclass


class BackendError(Exception):
    """The lighting program could not do it. The text is a short sentence for the operator (never a raw library
    message): it is shown on the admin page."""


@dataclass(frozen=True)
class Cue:
    id: str      # the program's own id for the button; opaque to Relay
    name: str


class Backend:
    id = ""                        # lower_snake_case, the value of the `backend` setting
    label = ""                     # shown in the admin page's list of programs
    can_list = False               # list_cues() returns the program's own buttons
    can_report_state = False       # state() says which cues are on

    def __init__(self, settings: dict) -> None:
        self.settings = settings   # the module's cleaned settings (host, port, password, ...)

    async def list_cues(self) -> list[Cue]:
        """The program's buttons, when `can_list`."""
        return []

    async def press(self, cue_id: str) -> None:
        raise NotImplementedError

    async def release(self, cue_id: str) -> None:
        """Let go of a cue that stays on while held. Backends with nothing to release leave this alone."""
        raise BackendError("This lighting program has nothing to release.")

    async def state(self) -> dict[str, bool]:
        """cue id -> is it on, when `can_report_state`."""
        return {}

    async def test(self) -> dict:
        """{"ok": bool, "detail": str}: can it reach the program with the saved settings?"""
        return {"ok": False, "detail": "This lighting program has no connection test."}

    async def close(self) -> None:
        """Drop the connection. Safe to call twice."""


# The backends Relay knows, by id. A backend's own module adds itself here when imported (the lighting package
# imports each one in `load()`); tests register fakes.
BACKENDS: dict[str, type[Backend]] = {}


def register(cls: type[Backend]) -> type[Backend]:
    BACKENDS[cls.id] = cls
    return cls


def unregister(backend_id: str) -> None:
    BACKENDS.pop(backend_id, None)
