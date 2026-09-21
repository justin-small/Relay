"""Rehearsal mode: canned captions, no API calls.

Set RELAY_DEMO=1 to drive the viewer pages from a scripted transcript so the
operator can check fonts, projector legibility and the link on every phone in
the room before the event -- without opening a billed session.
"""
from __future__ import annotations

import asyncio
import random

from .engine import TRANSCRIPTION_STREAM, TRANSLATION_STREAM
from .hub import hub

SCRIPT = [
    ("Good afternoon everyone, and thank you for coming.",
     "Buenas tardes a todos, y gracias por venir."),
    ("We have a lot to cover today, so let's get started.",
     "Tenemos mucho que cubrir hoy, así que empecemos."),
    ("The first item on the agenda is the quarterly review.",
     "El primer punto de la agenda es la revisión trimestral."),
    ("Revenue came in at four point two million, up eleven percent.",
     "Los ingresos fueron de cuatro punto dos millones, un once por ciento más."),
    ("If you have questions, please hold them until the end.",
     "Si tienen preguntas, por favor guárdenlas para el final."),
]


async def _feed(stream: str, lang: str, text: str) -> None:
    """Emit word-by-word deltas the way the model does, then commit the line."""
    words = text.split(" ")
    for i, word in enumerate(words):
        hub.publish_delta(stream, lang, word if i == 0 else " " + word)
        await asyncio.sleep(random.uniform(0.06, 0.16))
    hub.publish_final(stream, lang, text)


async def run(source_language: str = "ENGLISH", target: str = "SPANISH") -> None:
    await asyncio.sleep(1.0)
    while True:
        for english, spanish in SCRIPT:
            await asyncio.gather(
                _feed(TRANSCRIPTION_STREAM, source_language, english),
                _feed(TRANSLATION_STREAM, target, spanish),
            )
            await asyncio.sleep(random.uniform(0.5, 1.2))
        await asyncio.sleep(2.0)
