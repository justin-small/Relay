---
title: "ProPresenter drives Services LIVE by pressing Next/Previous from a fresh read of LIVE, once per activation, and Relay only ever writes the one playlist it made"
author: Justin
date: 2026-11-12
branch: propresenter-138-pclink
cost: { minutes: 8, tokens: 23817 }
id: l-ba647ea1
scope: repo
status: active
src: agent
touches:
  - ".github/workflows/release.yml"
  - README.md
  - app/alerts.py
  - app/config.py
  - app/main.py
  - app/panels.py
  - app/propresenter.py
  - app/slidetags.py
  - app/static/admin.js
  - app/templates/admin.html
---

## Decided
- Planning Center LIVE has no "go to item" action, only Next, Previous and toggle_control, so driving LIVE to a matched item presses Next/Previous one at a time through the panels' press path (`pccontrol.Control.drive`, same lock) and reads each answer: it stops on the target, on an overshoot (an excluded item or header that Planning Center steps over) or after 12 presses. It reads LIVE fresh first and does nothing unless the token's own person is the controller, so it can never take control; a wrongly cached controller would otherwise make toggle_control or a 403 the first sign.
- One try per activation of a presentation (a counter that rises when the active presentation's UUID changes), decided before the press, so a failed or refused try is not repeated and Planning Center being moved away by hand does not trigger another. Switching drive on while something is already up restarts the wait, so enabling it never moves LIVE at once. A failed read of ProPresenter's lists does not spend the activation; it retries every 20 s.
- Backward moves are a separate switch (`allow_back`, off): going back to replay a song is as likely a mis-click as intent.
- Relay writes to exactly one ProPresenter playlist: the one it created, named "Relay – <plan date>", its UUID stored in `propresenter.link.owned`. Every write re-checks that UUID and the name prefix, a same-named playlist Relay did not make is refused, and a save payload cannot set `owned`. The playlist is read back after the PUT and compared.
- Panel Service message gets only `pp_item` and `pp_agree`, not lyrics: the firmware reads that message by scanning for key names (service_format.h), so a lyrics string with its own "title"/"text" keys could be mistaken for plan fields. Lyrics keep going to panels through the lyrics feed.
- The operator's pairing for items like "Message" is stored by normalised title, not item id, because item ids change every week.

## Tried and ruled out
- Creating a test playlist on the live ProPresenter 22 to prove the create and PUT bodies: the API has no playlist delete, so it could not be removed afterwards. The setlist swap is checked with a fake only; the PUT item shape (`presentation_info.presentation_uuid` and `target_uuid`, `id.uuid` empty) is from the published description, not seen working.

## Watch out
- Open: whether Planning Center's Next skips header items. The drive loop does not depend on it (it re-reads the current item after each press) but the 12-press cap would be reached on a plan with many headers between two songs.
- In app/propresenter.py: `default_link`, `link_fields`, `OWNED_PREFIX`; matching in app/plmatch.py; the state machine in app/pclink.py (`tick`, `_fire`).
