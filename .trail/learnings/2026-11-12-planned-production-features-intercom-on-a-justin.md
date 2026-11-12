---
title: "Planned production features: intercom on a local LiveKit server, no personal in-ear mixes, presentation software may advance Planning Center LIVE but never tak…"
author: Justin
date: 2026-11-12
branch: main
cost: { minutes: 28, tokens: 211098 }
id: l-766a85a3
scope: repo
status: active
src: agent
---

## Decided
- Intercom (#145) runs on a local LiveKit media server in its own container, chosen as the more robust option: one server forwards audio, so it copes with weak venue Wi-Fi, reconnects cleanly and is not capped at a few phones. Relay's own code stays Python (issues room tokens) and JavaScript (crew page client).
- Presentation software (ProPresenter #138, Proclaim #154) may advance the Planning Center LIVE item when a presentation has been up for a set time. A setting turns it on or off, off by default. It never takes LIVE control: it advances only while Relay's token already holds control.
- Crew pages live on the viewer port behind a crew login, not on a separate listener.
- All new production features are built in Relay's existing languages (Python, plain JavaScript, C++ firmware), never Rust.

## Tried and ruled out
- Phone-to-phone WebRTC mesh intercom with Relay only relaying setup messages: breaks down past a handful of phones and on poor Wi-Fi.
- Personal in-ear monitor mixes moved by volunteers from their phones: Justin decided not to do it; desk control (#150) stays admin-only.
- Taking LIVE control automatically when presentation software changes item: dropped, it would pull control from the person running LIVE.
- A separate port for crew tools.

## Watch out
- Justin asked that the outside projects these feature lists came from are not named anywhere in this repo or its issues. Keep issue text and code comments free of them; if code from an MIT project is ever copied, its licence notice still has to come with it.

In the code: none yet; the plans are in issues #138, #140, #144, #145, #148-#159.
