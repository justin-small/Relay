---
title: "Crew checklists belong to a Planning Center team or to everyone, are ticked per person per plan, and are edited by operators and named team leads"
author: Justin
date: 2026-11-15
branch: main
cost: { minutes: 6, tokens: 127423 }
id: l-bbb4723d
scope: repo
status: active
src: agent
---

## Decided
Justin answered questions on issue 232 (2026-11-15): team leads as well as operators write templates; a template matches a Planning Center team name (ignoring case) or, left blank, everyone; one fresh list per plan with history kept (180 days) and readable by the operator; the operator sees a live progress board (polled like the arrival board).
A "team lead" is a person an operator names for one position (`crew_checklist_leads`), not a new role, because crew use the core roles. Leads edit from the crew page's Checklists tab, since members cannot open admin.
Each person ticks their own items: a tick is (plan, item, user).

## Tried and ruled out
Alerting crew whose list is unfinished at call time: offered, not chosen.

## Watch out
The "position" is the Planning Center team name, the only thing the follower reads; a PC position inside a team is not available.
Editing a list keeps the id of any item whose text is unchanged so this service's ticks survive.
In the code: app/modules/crew/checklists.py, migration 0006_checklists.sql, permissions crew.checklist and crew.checklist_edit.
