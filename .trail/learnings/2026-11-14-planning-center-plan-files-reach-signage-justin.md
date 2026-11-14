---
title: "Planning Center plan files reach signage as library pictures, with a source kind only for text slides"
author: Justin
date: 2026-11-14
branch: signage-planningcenter-209
cost: { minutes: 8, tokens: 179386 }
id: l-b56986bf
scope: repo
status: active
src: agent
touches:
  - AGENTS.md
  - README.md
  - app/modules/propresenter/__init__.py
  - app/modules/propresenter/ppcontrol.py
  - app/modules/propresenter/propresenter.py
  - app/modules/propresenter/routes.py
  - app/modules/propresenter/signagesync.py
  - app/modules/propresenter/signagetags.py
  - app/modules/propresenter/slidetags.py
  - app/modules/propresenter/static/pplive.js
---

## Decided
Justin answered four questions on issue 209 (2026-11-14):
- Pull attachments of plan items and of the plan itself, plus an optional text slide per plan item (title and one note category the operator names; notes can be private so none is used by default).
- Follow one chosen followed service type's current plan, else the next upcoming; look again every 5 minutes and on Sync now.
- One auto-managed playlist "Planning Center plan", optional, limited to plan items whose title holds a word; an item that stays keeps its time and dates. The plan's own files are left out while a word is set.
- Files are cached as signage library rows named "Planning Center: <item> <file>", images and MP4/WebM only; only rows with that prefix are ever deleted.
## Tried and ruled out
Converting PDFs to pictures (needs a rasterizer, none in requirements). A signage source kind for the files: library rows work, as in #208. The text slides do need a source kind ("pc_text", a data: page), because Relay has no image library to draw text onto a picture.
## Watch out
Not run against a real Planning Center account: the download address is the attachment's `url` attribute, taken from the OpenAPI spec, and whether it needs the token or redirects to signed storage is unconfirmed. The token goes only to planningcenteronline.com hosts and is dropped on a redirect elsewhere. Attachment-type filtering was left out (the relationship's shape is unverified).
In the code: app/modules/planning_center/signagesync.py, Client.stream_file in planningcenter.py, README "Signage from Planning Center".
