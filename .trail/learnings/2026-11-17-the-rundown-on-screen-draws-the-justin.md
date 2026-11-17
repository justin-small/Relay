---
title: "The rundown on /screen draws the service to scale, shows Planning Center photos on the mics, and was chosen over a console look and a big-type confidence monit…"
author: Justin
date: 2026-11-17
branch: rundown-ruler
cost: { minutes: 16, tokens: 208252 }
id: l-59f3c52f
scope: repo
status: active
src: agent
touches:
  - docs/mockups/rundown-redesign/common.js
  - docs/mockups/rundown-redesign/common.css
  - docs/mockups/rundown-redesign/a-ruler.html
  - docs/mockups/rundown-redesign/b-strips.html
  - docs/mockups/rundown-redesign/c-distance.html
  - docs/mockups/rundown-redesign/index.html
  - app/static/rundown.css
  - app/static/rundown.js
  - app/templates/rundown.html
  - app/modules/planning_center/rundown.py
---

## Decided
Justin, 2026-11-17: of three redesigns mocked up (docs/mockups/rundown-redesign), he picked A, "the hour to scale", and asked that the mics use each person's Planning Center profile picture.
- Full view: the order of service is a vertical ruler where an item is as tall as its minutes, with the planned times in a narrow lane beside it and a slanted red line wherever an item started late. The middle column is now / time left / how far behind in words / next / who is on a mic now (photo, mic, first name). The right column is the sermon notes and only the people serving without a mic, plus unfilled positions.
- Micboard: the photo fills the top of each tile with the mic number on a plate; each tile has its own strip of the service, lit for the songs that mic is drawn for (and for the current spoken item when the mic is on).
- Side view: the ruler from two minutes before the current item to the end, with the faces of who is on a mic in the current block.
- No backend change: the stream already carries planned/actual/projected starts, lengths, each song's mic draw and photo_thumbnail.

## Tried and ruled out
- B, mics as console channel strips coloured like the church's WING snapshots, with a lamp for "on now": not chosen.
- C, very large type for reading from the platform, showing only roster problems: not chosen; it dropped the full roster and the notes.

## Watch out
- A done item ends where the next item actually started, not at its planned length; the current item grows until now. The board redraws every 15 seconds so the playhead moves.
- The "board configs" .snap files are Behringer WING Compact snapshots, not Avantis, despite the AllenHeath-Avantis folder next to them.
- trail check flags the micboard row comment ("Up to 8 mics in one row…") as bringing back "one row": it does not; the 1/2/3-row rule is unchanged.

In the code:
- app/static/rundown.js (spans, ruler, hour, channel, slip), app/static/rundown.css, app/templates/rundown.html
