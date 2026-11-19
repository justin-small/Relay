# Shared concepts audit: people, teams, positions, screens, boards, crew, users, items

Date: 2026-11-18. Scope: `app/*.py`, `app/migrations`, every `app/modules/*` (Python and SQL). Templates and JS
were checked only where a concept's name showed up in them.

**Confidence** says how sure the audit is that the finding is a real duplicate that will cause drift or bugs, not
just a similar word:

- **High**: the same thing is stored or computed in two or more places, and the copies can disagree today.
- **Medium**: overlapping concepts with different purposes; worth merging behind one interface, but each copy works.
- **Low**: naming collisions or small repeats; fix when touching the code.

## Summary

Relay has a good contract for *UI* (relay-ui.js, components.css, enforced by tests) but no contract for *shared
data*. Each module invents its own person link, team name, screen and schedule. The worst cases are people/teams/
positions (free text matched three different ways) and screens (one TV can be registered as up to four different
things). Core has no place where "a person", "a team", "a screen" or "a place" lives with a stable id.

| # | Concept | Confidence | Copies |
| --- | --- | --- | --- |
| H1 | Person ↔ Planning Center link | High | crew owns it; 4 raw SQL rebuilds; no core API |
| H2 | Teams and positions | High | 7 free-text columns/fields, 3 matching rules |
| H3 | Screens | High | 6 meanings, 4 separate registries |
| H4 | Slug helpers | High | 4 implementations, 2 separators |
| H5 | Name normalising / fuzzy matching | High | 7 implementations |
| H6 | Weekday numbering | High | Sunday=0 and Monday=0 both in use |
| M1 | A person's phone / devices | Medium | 6 device tables or registries |
| M2 | Sessions | Medium | `intercom_remote_sessions` mirrors `intercom_sessions` |
| M3 | Places and groups | Medium | board `place`, `signage_groups`, `intercom_orgs` |
| M4 | Time windows and schedules | Medium | 5 schedule engines, 4 HH:MM parsers |
| M5 | Cross-module seams | Medium | 10+ registry/hook patterns, plus imports of module internals |
| M6 | Timestamps | Medium | ISO text and epoch real mixed |
| M7 | Items | Medium-Low | Mostly distinct; only plan items cross modules |
| L1–L4 | Naming collisions | Low | `role`, `page`, `screen_names`, `intercom_orgs` |

---

## High confidence

### H1. Person ↔ Planning Center link lives inside Crew

- Core: `users` (`app/migrations/0001_users.sql`) is the only person record.
- Crew: `crew_profiles.pc_person`, `pc_name`, `suggested_name`, `dismissed_name`
  (`app/modules/crew/migrations/0001_people.sql`) is the only link from a Relay user to a Planning Center person.
- The `{pc_person: user_id}` map is rebuilt by hand with the same SQL in
  [paging.py:124](app/modules/crew/paging.py:124), [paging.py:143](app/modules/crew/paging.py:143),
  [checkin.py:127](app/modules/crew/checkin.py:127) and [checklists.py:144](app/modules/crew/checklists.py:144).
- Planning Center people also appear, unlinked, in `planningcenter.py` (scheduled people, `controller`), the
  rundown's mic draw, parent paging check-ins and `intercom_production.pco_plan_id`.
- `intercom_remote_users` is a third per-user table that only says "this user may use remote intercom".

**Risk**: any module other than Crew that wants "who is this Planning Center person in Relay" (intercom, rundown,
ProPresenter, a future soundboard) must import Crew's tables or do its own matching. Switching Crew off loses the
link for everyone.

### H2. Teams and positions are free-text strings, matched three ways

Stored as plain names copied from Planning Center:

| Where | Field |
| --- | --- |
| `crew_calltimes` | `team` |
| `crew_checklists`, `crew_checklist_leads`, `crew_resources` | `position` |
| `crew_profiles` | `role` (admin-set free text, also names chat role channels) |
| Planning Center rundown mic mapping (config) | `{team, position}` |
| `planningcenter.py` scheduled people / needed positions | `team`, `position` (live, from `team_position_name`) |

Matched differently:

- exact: [checkin.py:114](app/modules/crew/checkin.py:114) `rule["team"] in teams`, [paging.py:146](app/modules/crew/paging.py:146)
- `.lower()`: [rundown.py:183](app/modules/planning_center/rundown.py:183)
- `.strip().casefold()`: [checklists.py:49](app/modules/crew/checklists.py:49)

**Risk**: a team renamed in Planning Center silently orphans call times, checklists and resources; "Sound " and
"sound" match in one feature and not in another. Crew `role` and Planning Center `position` are the same idea
with no link between them.

### H3. "Screen" means six different things

| Meaning | Where | Identity |
| --- | --- | --- |
| Browser showing a board | `boards.py` `screen_names`, `SCREEN_ID`; live list `_board_live` in [main.py:2687](app/main.py:2687) | random 8–40 char id, in memory + config |
| Browser showing alerts | [alerts.py](app/alerts.py) `/screen?alert=<name>` | free-text name |
| Signage display | `signage_screens` (`app/modules/signage/migrations/0001_signage.sql`) | DB row + slug |
| Hardware panel | `panels.py`, `cfg["panels"]` | 32-hex id |
| Tally screen | `app/tally.py` `/tally` | none (reads shared state) |
| Module tablet pages | `screen_pages` in [modules/__init__.py:395](app/modules/__init__.py:395) (lighting, OBS); NDI "Screens" tab | module-defined |

**Risk**: the lobby TV is a board screen, an alert target and a signage screen with three ids and three names.
Alerts cannot target "the lobby TV" if it is running signage; the Boards page cannot show what signage is on it.
The Alerts page and Boards page each keep their own screen list.

### H4. Slug helpers, four copies

- [overlays.py:225](app/overlays.py:225) `slug()` → `-`, fallback `overlay`
- [boards.py:496](app/boards.py:496) `slug_for()` → `-`, max 32, numbered on clash
- [alert_events/routes.py:47](app/modules/alert_events/routes.py:47) `_slug()` → **`_`**, fallback `button`
- signage `SLUG_RE` in `app/modules/signage/store.py` (validates a typed slug, no generator)

### H5. Name normalising and fuzzy matching, seven copies

- [crew/people.py:74](app/modules/crew/people.py:74) `norm()` + `similarity()` (NFKD, accents off, difflib, nicknames)
- [propresenter/plmatch.py:42](app/modules/propresenter/plmatch.py:42) `normalise()` + `similarity()` (NFKD, casefold, difflib + Dice)
- [propresenter/signagetags.py:83](app/modules/propresenter/signagetags.py:83) `_fold()`
- inline `" ".join(name.split()).casefold()` in [obs/service.py:588](app/modules/obs/service.py:588) and [lighting/service.py:184](app/modules/lighting/service.py:184)
- `.strip().casefold()` in checklists, `.lower()` in rundown
- `app/redact.py` `fold()` (accent folding for redaction)

The two `similarity()` functions are tuned for different things (people vs song titles) and should stay separate,
but they should share one `fold()`.

### H6. Two weekday conventions

- **Sunday = 0**: `app/schedules.py` (engine schedules), signage schedule ([schedule.py:15](app/modules/signage/schedule.py:15) says "as in the rest of Relay").
- **Monday = 0** (Python `weekday()`): boards `DAY_NUMBERS` ([boards.py:72](app/boards.py:72)),
  crew call times ([calltimes.py:103](app/modules/crew/calltimes.py:103)), YouTube rules ([youtube/schedule.py:176](app/modules/youtube/schedule.py:176)).

Not verified whether each UI sends the matching number (the JS was not traced); the inconsistency itself is
certain and makes a cross-feature schedule (e.g. "signage follows call times") an off-by-one waiting to happen.

---

## Medium confidence

### M1. "A person's phone" and other devices

`trusted_devices` (core sign-in), `intercom_devices` (intercom credential per phone), `crew_devices` (MAC/hostname
for check-in), `crew_push_subs` (web push per browser), UniFi clients by MAC (`unifi/presence.py`), panels, and
`network_map.py`. The first four are all "this user's phone" with no shared id, so a phone is enrolled up to four
times and revoking one does not revoke the others.

### M2. Sessions

`intercom_sessions` already has `via in ('local','remote')`; `intercom_remote_sessions` repeats the same columns
(`device_id, user_id, started_at, ended_at, end_reason`). Possibly deliberate so the remote module owns its rows;
if so, document it, otherwise write remote sessions to `intercom_sessions` with `via='remote'`.

### M3. Places and groups

Board `place` (free text, "Foyer"), `signage_groups`, alert button target lists, and `intercom_orgs` (one hard-coded
row, "Relay"). "The foyer" is not one thing that boards, signage and alerts can all point at.

### M4. Schedules and time windows

Five engines: `schedules.py`/`scheduler.py` (engine on/off), signage slots/overrides, board rotation schedule
(`boards.current`), YouTube rules, crew call times. Each parses HH:MM itself (`boards._hhmm`/`_minutes`,
calltimes `_CLOCK`, schedules.py, signage). Each has its own time-zone setting (signage zone, Planning Center zone,
schedule zone).

### M5. Cross-module seams have no single pattern

Core hook globals (`cue_hook`, `scene_hook`, `recording_hook`, `ndi_hooks`, `screen_pages`,
`panel_registry.followed_service_types`, `users.signed_out_hooks`), plus registries in `metering`, `audio`,
`signage/sources`, `signage/schedule` layers, `intercom/watchdog`, `lighting/backend`. Modules also import other
modules' internals directly, e.g. `crew → planning_center.pclive.follower` and `planning_center.routes.rundown_now`,
`propresenter → signage.store/live/takeover`, `parent_paging/youtube → planning_center.planningcenter`. These are
the only way to share people/teams/screens today, which is why each consumer re-derives them.

### M6. Timestamps

Core and intercom store ISO text (`created_at text`); crew stores epoch `real` (`arrived_ts`, `sent_ts`,
`done_ts`); some crew tables have both (`crew_pages.sent_ts` and `created_at`). Joins across modules need
conversion.

### M7. Items

Board items (widgets), `signage_items` (playlist), Planning Center plan items, rundown rows, `crew_checklist_items`.
These are genuinely different. The one shared case, a plan item shown as signage, already goes through the signage
source registry (`signage_sources.register`), which is the pattern to copy.

---

## Low confidence (naming)

- **L1** `users.role` is a permission role; `crew_profiles.role` is a job. Rename the crew one to `job` or link it to positions (H2).
- **L2** "Page" means a crew notification (`crew_pages`), a board page, a lighting cue page and a parent page.
- **L3** Board `screen_names` vs `signage_screens.name`: same idea, two stores (falls out of H3).
- **L4** `intercom_orgs` is a multi-tenant placeholder nothing else uses.

---

## Recommended standard: a core Directory

Add one core service, `app/directory.py`, that owns the *identity* of shared things. Modules keep owning their
feature data but refer to directory ids instead of copying names. This fits AGENTS.md: the directory is core and
imports no module; integrations (Planning Center, UniFi) *feed* it through a registry.

### 1. Stable references

Every shared thing has a ref string `kind:provider:id`, e.g. `person:relay:12`, `team:pco:381`,
`position:pco:381/9012`, `screen:relay:lobby-tv`, `place:relay:foyer`. Tables store the ref plus a `*_name`
snapshot for display when the provider is off.

### 2. Kinds and who owns them

| Kind | Owner of the record | Fed by |
| --- | --- | --- |
| person | core `users` + new `person_links (user_id, provider, external_id, name, matched_by, confirmed_by, at)` | Planning Center (and later others) |
| team, position | provider, cached in core `directory_cache` | Planning Center via `directory.register_provider()` |
| screen | new core `screens` table (one row per physical display) with capability rows: board, signage, alert, tally | boards, signage, alerts attach to it |
| place | new core `places` table | board `place`, signage groups, alert targets |
| device | new core `devices` (user_id, kind, credential/MAC, label) | intercom, crew check-in, push, sign-in |

### 3. Core API (sketch)

```python
directory.register_provider("pco", teams=..., positions=..., people_today=..., label="Planning Center")
directory.teams() / directory.positions(team=None)        # [{ref, name, provider}]
directory.user_for("pco", person_id) -> int | None         # replaces the four raw SQL maps
directory.link(user_id, "pco", person_id, matched_by="fuzzy", confirmed_by=admin_id)
directory.screen(ref) / directory.screens(capability="signage")
directory.resolve(ref) -> {ref, name, alive: bool}          # name snapshot when the provider is off
```

### 4. Shared helpers in core

- `app/text.py`: `fold()`, `slug(name, taken, sep="-")`, `same_name(a, b)`; crew `similarity` and plmatch
  `similarity` stay, built on `fold()`.
- `app/timewin.py`: one weekday convention (pick Sunday = 0, as `schedules.py` documents), `parse_hhmm`,
  `in_window`, zone handling. Signage, boards, call times and YouTube use it.
- Timestamps: new tables use ISO text in UTC (`strftime('%Y-%m-%dT%H:%M:%SZ')`), as core does.

### 5. One seam pattern

Replace the hook globals with `modules.provide(kind, impl)` / `modules.using(kind)` in `app/modules/__init__.py`
(the NDI hooks are already close). Directory providers, signage sources, audio/metering sources and cue/scene/
recording actions all register the same way, and a module stops importing another module's internals.

### 6. Enforce it like the UI contract

`tests/test_shared_concepts.py` with a shrink-only baseline, like `test_ui_contract.py`:

- no new module column named `team`, `position`, `pc_person`, `person_id` or `screen` holding free text; use `*_ref`
- no new `slug`/`norm`/`fold` helper outside `app/text.py`
- no new `from ..<other module>.<internal>` imports outside the `uses` list; go through `modules.using()`

---

## Decisions (Justin, 2026-11-18)

| Topic | Decision |
| --- | --- |
| Scope | **Core Directory**: core owns the identity of people, teams, positions, screens, places and devices with stable refs; modules store refs plus a saved name. |
| Person link | **Core `person_links`, any provider**. Crew's fuzzy matching and admin rename screen write to it; it works with Crew off. |
| Teams / positions | **Provider id plus saved name**. Renames follow; the saved name shows when Planning Center is off; admin pages pick from a list. |
| Crew role | **Stays its own field, defaulted from the person's usual Planning Center position**; the admin can override it. |
| Screens | **One core screen per physical display**, which boards, signage, alerts and tally attach to. |
| Boards + signage | **A screen shows either a board page or a signage playlist**, with one combined schedule choosing which. Signage stays a switchable module that contributes playlists (keeps the issue-204 choice); boards stay core. |
| Panels | **In the screens list as a screen type**; panel check-in and firmware code are unchanged. |
| Places | **Core places list** (Foyer, Stage…). Screens belong to a place; signage groups and alert targets can pick a place. Signage groups stay. |
| Devices | **One core device per phone, owned by a user**, with sign-in trust, intercom credential, MAC and push subscription as parts. Revoking the phone revokes all of them. |
| Weekdays | **Sunday = 0** everywhere, in a core helper; migrate saved call-time, YouTube and board day values. |
| Schedules | **One shared time-window helper and one Relay-wide time zone**, plus a **central Schedules page of named, reusable schedules** ("Sunday services") that the engine, signage, board rotation and call times pick by name. Planning Center plan times can be a source. Each feature keeps its own runner. |
| Seams | **`modules.provide()` / `modules.using()`**; existing hooks move over, and a test blocks imports of another module's internals. |
| Sessions | Merge `intercom_remote_sessions` into `intercom_sessions` (`via='remote'`). |
| Timestamps | ISO UTC text for new tables, **and migrate existing crew epoch columns**. |
| Naming | Rename crew `role` (not a permission role) and crew `pages` (notifications) to clear the clashes. |
| Enforcement | `tests/test_shared_concepts.py` with a shrink-only baseline **from step 1**. |
| Delivery | **GitHub epic, one issue and PR per step**; screens/signage and the Schedules page get a mockup first. |

### Order (one issue and PR each)

1. Core `app/text.py` (fold, slug) and `app/timewin.py` (Sunday = 0, HH:MM, windows, Relay time zone); fix weekday
   numbering with migrations; add `tests/test_shared_concepts.py` and its baseline (H4, H5, H6).
2. `modules.provide()` / `using()` registry, so the following steps use it (M5).
3. (done, #524) Core `person_links` + `app/directory.py`; migrate `crew_profiles.pc_person`; `directory.user_for()` replaces the
   four SQL maps (H1).
4. Planning Center provides teams and positions; crew call times, checklists, resources and the rundown mic mapping
   store refs plus a saved name; crew role defaults from position and is renamed (H2, L1).
5. Central named Schedules page and Relay time zone; the engine, signage, board rotation and call times pick a
   named schedule (M4). Mockup first.
6. Core `screens` + `places`, panels as a screen type; boards, signage, alerts and tally attach; a screen shows a
   board or a signage playlist from one combined schedule (H3, M3, L3). Mockup first.
7. Core `devices` with credentials as parts (M1).
8. Cleanups: merge intercom remote sessions, convert crew timestamps, rename crew pages (M2, M6, L2).
