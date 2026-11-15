---
title: "Crew resources are files, links and allow-listed embeds per Planning Center team, edited by the same operators and leads as checklists"
author: Justin
date: 2026-11-15
branch: crew-resources-233
cost: { minutes: 7, tokens: 69178 }
id: l-41f55631
scope: repo
status: active
src: agent
---

## Decided
Justin answered questions on issue 233 (2026-11-15): same authoring as checklists (operators any position, named team leads their own, one shared leads table); files up to 25 MB of PDF, images, plain text and Word/Excel/PowerPoint; embedded pages only for hosts on an operator-edited allow-list in the crew settings (default YouTube, Google Docs/Drive, Vimeo), anything else is a link; plan media copied by tag rules split off as a follow-up (it belongs to the Planning Center module).
## Tried and ruled out
Iframing any https page (many refuse and it widens risk) and link-only embeds: offered, not chosen.
## Watch out
File type comes from the first bytes (and zip entries for Office), never the name; stored under random names; pictures/text are served with a sandbox CSP, Office files download. Files are not copied into backups (unlike signage media).
In the code: app/modules/crew/resources.py, migration 0007_resources.sql, permissions crew.resources and crew.resources_edit.
