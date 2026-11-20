---
title: "Every mixing console is one switchable Audio Mixers module with drivers per desk family, several desks at once, built from published protocols"
author: Justin
date: 2026-11-20
branch: shared-pickers-546
cost: { minutes: 3, tokens: 89344 }
id: l-09978a6a
scope: repo
status: active
src: agent
---

## Decided
Issue 150 (2026-11-20), Justin's answers: one module named "Audio Mixers" (`app/modules/audio_mixers/`) for every desk, not a `sound_desk` module or one module per brand. One PR for every bullet of #150 plus: several desks at once (#486), Allen & Heath GLD and Qu, Yamaha DM3/DM7, desk metering into the core metering seam, and bus sends as core routes (`routes.audio_mixers`).
Allen & Heath Avantis/dLive: documented MIDI/TCP (51325) for control; AH-Net (51321) only as an off-by-default setting for live readback, with its proprietary/EULA warning shown.
No desk was available to test: every driver is built from published protocols and the bitfocus/community Companion modules (all MIT), and marked untested.
Each desk is a core gear record; its channels go out through `gear.audio_mixers` list(), so Planning Center's channel list fills itself; channel names go to the desk through can_rename/rename (#543).

## Tried and ruled out
- Yamaha CL/QL over MIDI SysEx as the Companion clql module does: RCP on TCP 49280 covers CL, QL, TF and DM with one driver.
- Shipping in two PRs or desk by desk: offered, not chosen.

## Watch out
Nothing that moves a desk starts on its own; the weekly name push needs a preview and a confirm. Prove each driver on real hardware before trusting it.
