---
title: Teams and positions are stored as a directory ref plus a saved name; checklists may be for a team or one position; crew role defaults from the usual position
author: Justin
date: 2026-11-19
branch: shared-concepts-person-links
cost: { minutes: 10, tokens: 306658 }
id: l-86f22a9b
scope: repo
status: active
src: agent
touches:
  - app/directory.py
  - app/modules/planning_center/rundown.py
---

## Decided
Issue 525 (2026-11-18), following the shared-concepts epic decisions:
- Core `directory_items` caches Planning Center teams and positions (`team:pco:<id>`, `position:pco:<team id>/<position id>`), so saved names show with Planning Center off. Planning Center provides `directory.pco.teams` from a slow `/teams?include=team_positions` read, because plan rows carry no position id.
- Call times, checklists, leads, resources and the rundown mic mapping keep a ref plus the saved name. A checklist or resource "position" used to mean a team name, so its ref may be a team (everyone on it) or a position (that position only). Names from before are matched with text.same_name only when exactly one team or position has that name; the rest stay name-only and are flagged.
- A deleted team or position stays in the cache with missing_at and shows as missing. An empty read from Planning Center changes nothing, so a failed read never marks everything missing.
- Crew role is `crew_profiles.crew_role`; empty means the person's most-scheduled Planning Center position (crew_position_history, one count per day).

## Tried and ruled out
- Keying leads by name only: a team and a position can share a name.
- Reading the directory while loading config: mic mappings stay pure on load and adopt refs in rundown.refresh_saved (on directory.changed and on save).

## Watch out
Saved names are rewritten on rename by each module (crew/refs.py refresh, rundown.refresh_saved); a name-only row never follows a rename. In the code: app/directory.py, app/modules/crew/refs.py, app/modules/planning_center/rundown.py (_pick, refresh_saved).
