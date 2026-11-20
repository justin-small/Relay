# Core Live and Output screens

Mockup: [`docs/mockups/admin-shell/pages/core-live-output.js`](../../mockups/admin-shell/pages/core-live-output.js)
(open `../../mockups/admin-shell/index.html`, pages `#health` … `#recordings`). Source of truth for what each
page holds: the `<div class="page" data-page="…">` sections of `app/templates/admin.html` and the scripts that
fill them (`app/static/admin.js`, `overlay-builder.js`, `alert-icons.js`, `qrcode.min.js`).

Rules that hold for every page below:

- Every page keeps its `.page[data-page]` wrapper with the same `id="page-<name>"`. `tests/test_browser.py`
  clicks `nav a[data-page='<name>']` for each role and checks which links exist (`NAV` there maps page to
  permission), and checks `#startBtn` exists only with `session.control`. The new sidebar must keep
  `a[data-page]` and `aria-current="page"`. A new page or route must also be listed in `tests/test_auth.py`.
- Permissions are the real ones (`session.view`, `audio.manage`, `targets.manage`, `panels.view` /
  `panels.manage`, `alerts.view` / `alerts.manage` / `alerts.history`, `overlays.view` / `overlays.manage`,
  `recordings.view` / `recordings.manage`, `settings.manage`, `session.control`). The mockup shows the owner
  view, with a small "Demo" switch under the title that turns on the lower-permission rendering where there is
  one (`You can view this page but not change it.` is the `viewonly(perm)` macro, kept as is). The demo switch
  is mockup chrome and is not part of the product.
- Scripts build many controls by id and text (`$('id')`, `el()`); markup moved into cards must keep those ids.
  The mockup keeps almost every real id, listed per screen.
- The status stream (`/stream?stream=status`, `applyStatus`) fills all of the live bits; nothing here adds an
  endpoint. Sample data (a church, Spanish/Portuguese/Korean live) is mockup only.
- Extra CSS in the mockup is scoped with the `clo-` prefix; in the real change it becomes component CSS
  (card, stat, tabs, data table, switch, badge, drawer). Only the overlay preview stage uses literal colours,
  because it draws real output colours (chroma green `#00B140`, key matte, black).

---

## Session health (`#health`)

Template `page-health`, script `admin.js` (`applyStatus`, `applyMeter`, `feed*`). Permission `session.view`;
Start and Stop need `session.control`.

On it: Stream control (state text, Start capture, Stop, note that changes with permission), Live transcript
(feed select, Auto-scroll, state, log box), Sessions table (Session, State, Last delta, Retries, Audio dropped,
"Not started." when empty, error text beside the state).

Must stay: `#masterState` `#startBtn` `#stopBtn` (disabled flags toggled by `applyStatus`/`applyMeter`),
`#feedSel` `#feedScroll` `#feedState` `#feedBox` (`role="log"`, children `p.settled`/`p.open`, `.empty`),
`#sessions` (tbody, five columns), state classes `ok`/`warn`/`err` come from `stateClass()`. The top-bar pill
`#streamPill` / `#pillTxt` / `#masterDot` link here. `feedSync(name === 'health')` opens the `/stream` listener
only while this page is shown; keep that.

Mockup changes: a row of four stat cards (capture, sessions connected, viewers, audio input) summarising the
same status data; Stream control becomes a full-width card with the state in large type; Sessions and Live
transcript sit side by side. State words become `.badge` pills. The stat cards are new: they need no new data
(`st.running`, `st.sessions`, `st.viewers`, `st.audio`) but the "19 phones, 5 screens" split and the
"Started by the Sunday schedule" line are samples; `masterText()` already gives the schedule wording.
Start/Stop in the mockup flips the shared sidebar stream state.

## Audio input (`#audio`)

Template `page-audio`, script `admin.js` (`renderDevices`, `renderChannels`, `saveDevice`, `paintMeter`).
Permission `audio.manage`; the noise-reduction select is disabled without `settings.manage`.

On it: Capture device (device select, channel select, Rescan, `#deviceNotice`, noise reduction with its note),
Input level (state pill, dBFS meter with peak marker and scale, CLIP LED, readout, `#audioNote`).

Must stay: `#device` `#channel` `#rescan` `#deviceNotice` (`role="status"`, `hidden` when empty) `#noise`
(values `""`, `far_field`, `near_field`), `#audioDot` `#audioState`, `#meter` (first child is the bar, class
`hot` / `clipping`), `#meterPeak`, `#clipLed` (class `clip on`), `#meterRead` (class `clipped`), `#audioNote`
(class `err`). Meter maths (`DB_FLOOR = -60`, amber from -6 dBFS) is unchanged. Channel list is `Channel 1..n`
plus `Mix (n ch)`.

Mockup changes: two cards instead of two hairline sections; the meter is thicker; a small tab strip lets the
reviewer force each state (Speaking, Quiet, Clipping, Waiting, Error, Idle) and a switch forces "No input
devices found". Neither strip is product UI. The mockup animates the bar through the shell's `#mt` / `#mtv`
ids; the real page keeps `paintMeter`. The "no devices" notice text is a sample; the real text comes from
`body.device_notice`. Not shown: the `Reconnecting` wording beyond the "Waiting" state.

## Target languages (`#targets`)

Template `page-targets`, script `admin.js` (`renderTargets`, `targetState`, `setAllTargets`, `renderMode`).
Permission `targets.manage`; `#xmode` disabled without `settings.manage` (and the note then adds "An
administrator sets the engine.").

On it: heading summary `#targetsSum` ("Spanish, Portuguese, Korean live" or "… armed" or "none on"), Translation
engine select with the two real option texts and note, Enabled languages (count, Select all, Clear, grid, note).

Must stay: `#xmode`, `#targetsSum` (spans, `.live`), `#targetsCount` ("N enabled"), `#targetsAll`
`#targetsNone`, `#targets` grid, one checkbox per language with `#tstate-<TARGET>` for its state text
(`live`, `armed — starts with capture`, `off`, `Text engine only`, `needs the Text engine`), `#targetsNote`
(two texts by engine). Text-only languages (Arabic, Nepali, Telugu, Hmong) are disabled while the Realtime
engine is chosen. Select all asks `confirm()` first in Realtime mode; the mockup only toasts that text.
`renderTargets` also feeds `feedTargets()` for the Session health select; keep that call.

Mockup changes: engine and languages become two cards; each language is a bordered tile with its state
line; the engine card adds two explanatory tiles (one per engine) that mirror the select. The tiles are new
and optional. The mockup left the `.langgrid` label class `check` as plain markup.

## Panels (`#panels`) and the device drawer (`#drawer`)

Template `page-panels` plus `<aside id="drawer">` at the end of the body; script `admin.js` (`renderPanels`,
`panelRow`, `renderDrawer`, `refreshDrawerDevice`, `renderFirmware`, `updateRow`). Permissions `panels.view`
and `panels.manage` (the "Set up a new panel over USB" link, firmware controls, Save, Forget, Update, Reboot).

On the page: `#panelsSum` ("4 of 5 online"), the flash call-to-action to `/admin/flash` (manage only), Paired
panels (Name button, Board, Showing, Firmware, Status with `Online`/`Offline` and a `update` badge; empty text
"No panels have checked in yet."), Panel firmware (one row per board: name, file, version, size, upload age,
online and behind counts; file input, Upload, Update all online, Remove, and the long note, manage only), How
panels work.

In the drawer: Name; Display (Number of feeds radios, Feed 1, Feed 2, "Set from admin" note); Screen rotation
0/90/180/270 with note; At the screen (Lock, PIN with "Remove the PIN", locks-again minutes, Go back with
minutes); ProPresenter timers (own settings, Amber at, Red at, Timers to show, notes; only while a Timers feed
is chosen or the panel has its own); Planning Center (Service mode with help, service type; only while that
module is on); Device (status, last seen, showing, feed changed, screen, last touched, board, rotation,
firmware, address, Wi-Fi, reports-as, update progress, Update, Update now, Reboot). Footer: Save, Cancel,
Forget; a view-only role gets Close only and disabled fields.

Must stay: `#panelList` (rows `.prow`, name button `.plink`, `.panelseen` ticking text), `#panelsSum`, `#fwBox`
`#fwTypes` (`.fwtype .fwname .fwmeta .fwrow`), `#drawer` `#drawerBg` `#dTitle` `#dClose` `#dBody` `#dFoot`
(`hidden` + `aria-hidden` toggled by `openDrawer`/`closeDrawer`, Escape and backdrop click close it),
field ids `panelName-<id>`, `panelFeed0-<id>`, `panelFeed1-<id>`, `panelPin-<id>`, `panelUnlock-<id>`,
`panelReturn-<id>`, `panelTmAmber-<id>`, `panelTmRed-<id>`, `panelSvc-<id>`, `panelSvcType-<id>`, `#devInfo`,
radio names `nfeeds-<id>` and `rot-<id>`. The mockup used short ids (`dName`, `dFeed0`, …); the real
ones above stay. The drawer must not be rebuilt while `drawerTouched` or focus is inside it; Save sends only
changed fields; the rotation and Update now confirms stay. The module hooks `RelayAdmin.panelHooks.timers`
and `.serviceOn` decide whether the timers and Planning Center blocks exist.

Mockup changes: list and firmware become cards (list wide, firmware and "How panels work" stacked at the
side); the status dot is a token-coloured `.clo-st`; the drawer is a token-styled right sheet. The mockup
appends the drawer to `<body>` so it sits above the sticky header; the real page already does.
Not shown: an unnamed panel ("Reports as"), the update states other than Downloading and available, a failed
update (the red `bad` row), the "No firmware uploaded" firmware row, and the offline Reboot-disabled tooltip.

## Alerts (`#alerts`)

Template `page-alerts`, script `admin.js` (`renderAlerts`, `alertCard`, `renderMessageCards`, `targetPicker`,
`renderAlertLinks`, `loadAlertHistory`, `alertSave`), `alert-icons.js`. Permissions `alerts.view`,
`alerts.manage` (all editing; without it fields are disabled and Add/Remove/Save are absent), `alerts.history`
(History).

Long page becomes six tabs, in the real order: **Buttons**, **Stage messages**, **Replies**, **When nobody
taps**, **History**, **Where to send from**. The `#alertsSum` text ("1 flashing" or "N buttons") sits in the
title.

- Buttons: per card icon preview and picker (ten icons, from `alert-icons.js`), name (32 chars), level, live
  state text (`Flashing · from Sam Lee`, `Seen on Booth TV — “On my way”`, `Not seen`, `Stopped`), Remove;
  "Panels it flashes" checkboxes (offline panels marked, forgotten panels listed as "Forgotten panel xxxx"),
  "Browser screens it flashes" with the `Open now:` hint, the ProPresenter stage-display tick (only while that
  module sets `panelHooks.stageDisplay`); Add button; empty text "No alert buttons yet. Add one for each place a
  musician may need to reach."
- Stage messages: the long note, preset cards (text 80 chars, level, Banner, Remove, "Shown on" picker with a
  screens field), Add message, empty text "No preset messages yet…", "Typed messages go to" picker plus
  "Typed messages are" level, "A message stays" and "A banner stays" seconds (3 to 120, 30 to 3600).
- Replies: textarea (6 lines of 24 characters) and note.
- When nobody taps: Stop flashing after (30 s to 10 min), the seizure-safety note, "Only signed-in people can
  send" with its note.
- History: When, Who, What (+ small "Alert · urgent · to …"), Seen; "Loading…", "Nothing sent yet.", "The
  history could not be read.", Show older, "Kept for 30 days."
- Where to send from: `#alertLinks` rows (Sender page (phones), one per distinct screen name) and the note. The
  sender page row moves to the `https://` operator address when sign-in is required.

Must stay: `#alertList`, `#alertAdd`, `#msgList`, `#msgAdd`, `#msgFree`, `#msgSeconds`, `#bannerSeconds`,
`#alertReplies`, `#alertAutoStop`, `#alertSignin`, `#alertSave`, `#alertDirty`, `#alertsSum`, `#alertHistList`
`#alertHistMore` `#alertHistMoreRow` `#alertHistKeep`, `#alertLinks`, classes `.pcard .acard .phead .atargets
.astate (on|seen|missed|cancelled) .aiconpick .alevelpick`, `data-astate`, `data-ahint`, `id="alertScreens<i>"`.
`renderAlerts` never rebuilds under the cursor or while `alertDirty`; keep that when the cards are tabbed (a tab
switch must not reset edits). Hidden tabs must still be in the DOM so `alertSave` can read `#msgSeconds`,
`#bannerSeconds`, `#alertReplies`, `#alertAutoStop`, `#alertSignin`.

Mockup changes: tabs with counts; "Save alerts" and "Unsaved changes" move to a bar beside the tabs and are
visible on every tab (in the page they live at the end of "When nobody taps"); the History and links get a
small state strip (Rows, Empty, Loading, Error) that is mockup only; Copy buttons beside each link in
"Where to send from" are new (the real page has links only). History's timestamps are samples
(`toLocaleString` in the page). Alert card fields use the same inputs as today.

## Viewer links (`#urls`)

Template `page-urls`, script `admin.js` (`renderUrls`, `urlRow`, `renderSecure`, `wireCopies`, `paintQrs`,
`watchUrls`). Permission `session.view`.

On it: `#urls`: "For the room" (Translation, Transcription, Both, Chooser page, Screen (kiosk display)),
"For your switcher" (Overlay (OBS / vMix / ProPresenter)), each row with one link per network host, a Copy
button and a local QR; Notes. `#secure`: "Operator panel (HTTPS)" with notices, the "certificate was replaced"
warning, panel URLs, the SHA-256 fingerprint and "Valid until … It is self-signed…" note.

Must stay: `#urls` `#secure` (both rebuilt every 10 s when the addresses change; built with `textContent` for
server values), `.urlgroup .urllabel .urllinks .urllink .urlrow .urlcopy[data-copy] .urlqr[data-href]`, the
`Copied` acknowledgement, `qrcode()` painting into `.urlqr`, `viewerHosts` feeding `renderAlertLinks`.

Mockup changes: the four sections become cards, with the HTTPS block and Notes in a side column; QR codes sit
beside each link. The mockup draws a decorative placeholder pattern, not a scannable code: the real page keeps
`qrcode.min.js` (dark on light, which must stay for scanning; do not recolour it from tokens). Port notices
and the fingerprint are samples. A "certificate changed" strip is mockup only.

## Broadcast overlay (`#overlay`)

Template `page-overlay`, script `overlay-builder.js` (preview is the real `/overlay?…&demo=1` in an iframe),
`app/overlays.py` for defaults and presets. Permission `overlays.view`; `overlays.manage` for saving, editing
and deleting (without it: no "Editing" strip, no save form, saved rows only have Copy link).

On it: two tabs, **Build** and **Setup guides** (`data-ovtab="build|guides"`).

Build, controls: Layout presets (Lower third, Top strip, Accessibility slate, Two languages, each with a
thumbnail and a one-line description), Captions (Show: Translation / Original / Two, stacked; Language and
Second language; Lines on screen; "Finished lines only" with its note; Style Roll-up / Pop-on with
`#ovModeHint`; Hide after silence; "Language names over each feed"), Position & size (Position, Alignment,
Text size, Max width, Title-safe margin, Canvas with the four sizes and note), Look (Font, Text edge, Text
colour, Plate colour, Plate opacity), Output (Transparent, Chroma green, Key matte, Black, `#ovBgHint`).
Build, preview: Preview with backdrop Over video / Transparency / Black and a Title-safe guide tick, the stage,
"Sample captions (off shows the live feed)", Browser source link (`#ovUrl`, Copy link, Open), the save form
("Save as a named overlay: a short link that keeps working when you change it here", name, Save or Save
changes), Saved overlays (Name, Link, Copy link / Edit / Delete; "None saved yet.").
Setup guides: OBS Studio, vMix, ProPresenter, Blackmagic ATEM, with their exact steps.

Must stay: `#ovBuild` `#ovGuides` `[data-ovtab]`, `.ovpreset[data-p]`, every control's `data-k`
(`stream lang lang2 lines finals mode hold labels pos align font maxw safe res family edge color platec plate
out`), `.seg[data-k] button[data-v]` with class `on`, `.rng output`, ids `ovLang ovLang2 ovLangF ovLang2F
ovLangLbl ovLabelsF ovModeHint ovBgHint ovPlatecF ovPlateF ovBackdrop ovGuideLines ovStage ovFrame ovSafe-guide
ovSample ovUrl ovCopy ovOpen ovSaveForm ovName ovSave ovList ovEditing ovEditingName ovEditDone`, the stage
classes `bk-program|checker|black`, the saved link shape `/overlay/<id>` and the long form `/overlay?preset=…`
(only what differs from the preset). The builder loads once on `#overlay`; presets, defaults and live languages
come from `/api/admin/overlays`; the server validates every value again.

Mockup changes: each fieldset becomes a card in a left column; preview, link, save form and saved list form a
sticky right column; the seg buttons use the shared connected toggle group. Presets are cards with the same
thumbnails. The mockup replaces the `<iframe id="ovFrame">` with an in-page renderer so it needs no server: it
draws the lines, plate/outline/shadow edge, font, alignment, max width, safe margin, output background, stacked
second language and pop-on/roll-up from the chosen values, scaled to the canvas size. It does not model
"Hide after silence" or "Finished lines only". The real implementation keeps the iframe. The stage uses literal
colours (green, black, white) because those are the real output colours. Overlay settings and preset values
match `app/overlays.py`; saved overlays are samples and live only in memory.

## Recordings & export (`#recordings`)

Template `page-recordings`, script `admin.js` (`loadRuns`, `renderRuns`, `showRunFiles`, `deleteRun`,
`saveRecording`). Permission `recordings.view`; `recordings.manage` for the switch, runs kept, Save and Delete.

On it: Recording (the long note, "Record each run to disk" switch, "Runs kept (oldest pruned at the next
start)", Save, "Stored in `recordings/`"), Runs (Run, Duration, Lines, Download, Delete), What's in the export
(both notes: file naming and the pairing/confidence note).

Must stay: `#recEnabled` (a checkbox inside `label.switch`; the mockup draws a `.sw` button, the real input
must remain a checkbox or the code that reads `.checked` changes), `#recKeep` (1 to 500), `#saveRecording`,
`#recDir`, `#recRuns` (five columns), row content: `<strong>` date, `· recording` (`ok`), `· cut short`
(`warn`), `in progress`, `All (.zip)` and `Files…` links (the second expands to per-file links, skipping
`manifest.txt`, and says "no lines recorded" when empty), Delete disabled with the title "This run is still
being recorded." and a `confirm()` that says the transcript cannot be recovered. Empty texts: "Nothing
recorded yet — the next run will be saved." (on) and "Recording is off, so no run has been saved." (off).
`loadRuns()` is re-run when a run ends.

Mockup changes: Recording is a full-width card with the controls in one row; Runs and the export explanation
sit side by side; the export file names are also listed as chips. The "Runs / No runs" strip is mockup only.
The in-progress row is shown only while the shared stream is running and recording is on. Not shown: the
`Files…` request failing (the page shows the global `#alert` line).
