# Screens: people and screens modules

Mockup file: [`docs/mockups/admin-shell/pages/modules-people.js`](../../mockups/admin-shell/pages/modules-people.js).
Nine admin pages from eight modules, all inside the shell. Each is an `AdminPage` of its module, so it is only
rendered while the module is on, and its nav link needs the page's permission (`<module>.view` unless stated).

## What every page here has in common

**Keep (all scripts depend on these):**

- The page wrapper `#page-<AdminPage.id>` (for example `#page-intercom-remote`, `#page-parentpaging`). Every module
  script starts with `if (!R || !document.getElementById('page-<id>')) return;` and several poll only while
  `#page-<id>`'s `.page` ancestor is not `hidden`. The new shell must keep one `.page` per page with `hidden`
  toggled, or change `visible()` in each script at the same time.
- `window.RelayAdmin` (`$`, `api`, `post`, `el`, `agoText`, `cfg`, `okMsg`), `window.relayCan(perm)` and the
  `relay:config` document event. None of this is visual.
- Scripts build rows with `createElement` and `textContent` (never markup). A restyle changes classes, not that.
- Plain class names the scripts set or read: `.note` with `.err`, `.ok`, `.warn`; `.muted`; `.mono`; `.ctl`;
  `.field`; `.row`; `.tbl`; `.tablescroll`; `.check`/`.chk`; `.sumstate`; the buttons' `.primary`, `.danger`,
  `.forget`; `.sg-*` and `.ic-*` (see the pages). The component library has equivalents; map them in the CSS, do
  not rename them in the scripts unless the script is edited in the same change.
- Every `hidden` attribute the scripts toggle (`el.hidden = ...`). The new CSS must not set `display` on a
  class without a `[hidden]{display:none!important}` guard (signage already has one at the top of its template).
- Permission handling: view-only users see the controls disabled or hidden by script, plus (on some pages) the
  note `You can view this page but not change it.` The mockup's **Preview as: Admin / Viewer** switch demonstrates
  both: controls that the real script *disables* are disabled; controls it *hides* (`.mp-manage`: Restart
  LiveKit, Sign out, Release this address, the Remote crew buttons, signage's add/edit forms...) disappear.

**What the mockup changes everywhere:**

- Sections become cards (`.card`, `h3` plus `.d` for the section's first note), tables use the `.dt` look, buttons
  and inputs use the component library, checkboxes that mean on/off are `.sw` switches where the page is a
  setting (Crew "Show today's plan", Alert events "Send a webhook"/"Send OSC messages").
- `sumstate` (the small word next to the title) becomes a `.badge` (`ok`/`warn`/`err`).
- Big pages get tabs; every control stays. The demo switches at the top right of each page (Preview as, data
  state, Planning Center on/off, and so on) exist only in the mockup; the real page shows one state at a time.
- Standalone sample data is realistic (Grace Fellowship, sanctuary, nursery, youth); none of it is real.

**Mockup-only things to ignore:** the state switches, the `data-g`/`data-v`/`data-mp` attributes, hidden alternate
panes, the three `#sgEditor` copies (one real editor, filled per playlist), duplicate ids across alternate states
(`icState`, `irState`, `raState`, `sgAlertNow` exist once on the real page).

**Shell gaps found while building (for the shell owner):**

- The shell's `.card .d` (description line: muted, 12px, bottom margin) also matches the destructive button
  `.btn.d` inside any card. The mockup overrides it (`.mp-page .btn.d`); the shell should scope the description to
  `.card p.d` or rename the button modifier.
- `.tabs` inactive buttons use `--muted` on a `--hover` background: unreadable in the Soft Pop preset (yellow
  hover). Needs a token pairing or a different inactive colour.
- No shared component for: a QR code box (needs a fixed light quiet zone; the mockup uses `#fff`/`#000` inside
  `.mp-qr`, as the real pages do for scanning), a thumbnail tile, a collapsible "details" row, a progress or
  upload line, an inline field-error style (the pages use `.note.err`), a banner (`.mp-banner` here).
- `.badge` and `.sbadge` have no "neutral" colour next to `ok`/`warn`/`err`; the mockup uses the plain badge.

---

## Crew app

- **Route:** `/admin#crew`. **Nav:** Settings. **Module:** `crew`.
- **Template / script:** `app/modules/crew/templates/admin.html`, `app/modules/crew/static/crewadmin.js`
  (QR from the vendored `qrcode` generator). The crew-facing pages (`crew.html`, chat, resources...) are separate.
- **Permission:** nav `crew.view`. Save, location, hosts, call times, name matching, Make lead: `crew.manage`
  (inputs disabled without it). Arrivals, Checklists and Page the crew sections: `crew.page` (the section stays
  `hidden` without it). Team leads: `crew.checklist_edit`.
- **On it:** The page (name on the home screen, show today's plan, church location lat/lon/metres, allowed embed
  sites, Save); Join by QR code (QR, join page and crew page addresses); Arrivals (count, Clear arrivals, table
  Name/Team/Call time/Status with Arrived, Arrived late, Not yet, Overdue, No crew account, `(location)`);
  Checklists (Service select, count, table Name/Team/Checklist/Done, hover title `Still open: ...`, Team leads:
  person select, Planning Center team, Make lead, table Name/Position/Remove); Page the crew (text, teams serving
  today with reachable/people, people with role and `(no phone)`, Send page, table Page/Who with `(buzzing)` and
  receipts `✓`, `… waiting`, `no phone`); Call times (team with datalist, When before/fixed with minutes or
  day+time, message, Add call time, table Team/When/Says/Last/Turn off-on, Remove); People (Match names now, table
  Name/Role input/Planning Center with `Use it`, `Keep mine`, `Wrong person`, `—`, `(off)`); Opening it on a phone.
- **Hooks that must stay:** `crName crPlan crLat crLon crRad crHosts crErr crSave crMsg`; `crQr crJoinUrl
  crCrewUrl`; `crBoardBox crBoardErr crBoardCount crBoardClear crBoard(tbody)`; `crListBox crListErr crListPlan
  crListCount crLists(tbody) crLeadBox crLeadUser crLeadPos crLeadAdd crLeads(tbody)`; `crPageBox crPageText
  crPageTeams crPagePeople crPageErr crPageSend crPageMsg crPages(tbody)`; `crCallBox ctTeam ctTeams ctKind ctBefore
  ctMin ctFixed ctDay ctClock ctMsg ctErr ctAdd ctRules(tbody)`; `crScan crPeopleErr crPeople(tbody)`; `crLink`.
  Scripts do `document.querySelector('#<table> tbody')`, so each table keeps its id and a `tbody`.
- **Mockup changes:** four tabs (Setup, Today, Call times, People) in place of one long page; "Show today's plan"
  is a switch (`#crPlan` stays the control: the script reads `.checked`, so a switch needs a hidden checkbox or
  the script reads `aria-checked`); statuses become status badges; the QR sits beside the form.
- **Real implementation:** keep the show/hide logic (`crBoardBox`, `crListBox`, `crPageBox`, `crLeadBox` start
  hidden and are revealed by permission). The tab state must not stop the 15 s refresh of Arrivals and Checklists;
  poll only when the tab or page is showing. Planning Center on/off changes People (the scan button and
  suggestions) and the teams list in Page the crew; the mockup's switch shows both. Not shown: the datalist
  autocomplete, the time and day controls swapping on `ctKind` is shown, the live 3 s receipt polling.

## Intercom (channels, live, health, LiveKit)

- **Route:** `/admin#intercom`. **Nav:** Automation, parent `Intercom` (the sub-item is also titled `Intercom`; the
  second sub-item is Signed-in phones). **Module:** `intercom`.
- **Template / scripts:** `app/modules/intercom/templates/admin.html`; `static/intercom.js` (LiveKit state, phones,
  channels, editor), `static/live.js` (the Live section, one event stream), `static/health.js` (Health, thresholds,
  log).
- **Permission:** nav `intercom.view`. Live buttons (`[data-live]`): `intercom.live` (hidden without it). Restart,
  channel editing, thresholds, Sign out: `intercom.manage` (channels section hidden without it).
- **On it:** the page title with a state word; Live (production label, Set label, Use Planning Center plan name,
  Release all talk, Mute all/Unmute all, the muted banner `Everything is muted by the operator. Nobody can talk until
  you unmute.`, Channels now table Channel/Mode/Talking/Listening/Waiting/Mute channel, Connected table Person/Role/
  Priority/Phone/Where/Talking/Listening/Loss/Jitter/Mute, Sign out, empty `No channels yet.` and `Nobody is on the
  intercom.`, the note about live state); Health (seven cards with OK/Warning/Problem/Idle, collapsible Thresholds
  with the 14 numeric inputs, Save thresholds, Back to defaults; Connection log with Show, Kind filter, Export CSV,
  table Time/Event/Person/Phone/Channel/By/Detail, `Nothing logged in this period.`); LiveKit (state line, version
  and node address, Restart LiveKit, the ports note, the log tail); Channels (production label with Save label and
  the plan button, draggable table, New channel, the editor with name, colour, mode, max talkers, latch limit,
  members table with Talk/Listen/Priority, Add a user with datalist, Save, Cancel, Delete channel, `icEErr`).
- **Hooks that must stay:** `icSum icLiveSum icLive icLiveBar icLiveLabel icLiveLabelSave icLiveLabelPlan
  icReleaseAll icMuteAll icMutedBanner icLiveChEmpty icLiveChTable icLiveCh icLiveConEmpty icLiveConTable icLiveCon
  icLiveErr`; `icHealth icHealthSum icCards icThBox icTh icThSave icThReset icLogHours icLogKind icLogCsv
  icEventsEmpty icEventsTable icEvents icHealthErr` and `[data-key]` on each threshold input; `icState icFacts
  icPorts icRestart icLog icErr`; `icChannelsSection icLabel icLabelSave icLabelPlan icChannelsEmpty
  icChannelsTable icChannels icNewName icNewChannel icEdit icEditTitle icEName icEColour icEMode icEMax icELatch
  icMembers icAddQ icAddList icAdd icESave icECancel icEDelete icEErr`; the `.icPrio` class on the Priority
  header and cells (hidden when the mode is Party line); `data-live` on the live buttons; `tr[draggable]` with
  `data-id` on channel rows (drag reorder posts the id list). The classes the script sets on cells,
  `ic-warn`, `ic-bad` (loss/jitter levels, from `"ic-" + level`), and on cards `ic-card ok|warn|error|idle`.
- **Mockup changes:** four tabs (Live, Channels, Health, LiveKit) in place of one scroll; health cards get the
  token colours; the editor sits under the table; channel colours are operator data and stay literal (mockup: hsl).
  The page also hosts the `INTERCOM LIVE · <label>` top-bar pill from `live.pills()` (global, not part of the
  page); the shell's header pill area should carry it.
- **Real implementation:** the inline `<style>` blocks in the template (`.ic-warn`, `.ic-cards`, `.ic-th`) move
  to the shared stylesheet using tokens (they hard-code `#b45309`/`#b91c1c`). Live opens its `EventSource` only
  while the page shows; with tabs it should open while the Live tab is showing. `health.js` skips polling when
  `page.offsetParent === null`; keep that or switch it to the tab state. `drawThresholds` runs once so typing is
  never overwritten; keep. Not shown: drag handles in motion, the `confirm()` on Delete channel (the mockup toasts),
  the colour input's native picker.

## Signed-in phones

- **Route:** `/admin#intercom-phones`. **Nav:** Automation, parent `Intercom`. **Module:** `intercom`.
- **Template / script:** `app/modules/intercom/templates/phones.html`; rendered by `intercom.js` (`renderPhones`).
- **Permission:** nav `intercom.manage` (set on the AdminPage). Sign out and Sign out all phones need it too.
- **On it:** the note, table Person/Phone/Signed in/Last seen/Key renews/Status/Sign out, Sign out all phones,
  empty `No phones are signed in.`, error line. Status words `On intercom`, `Idle`, `Expired`.
- **Hooks that must stay:** `icPhonesSection icPhonesEmpty icPhonesTable icPhones icSignOutAll icPhonesErr`.
- **Mockup changes:** status becomes a badge (ok/warn/err); a populated/empty switch. **Real:** the script's
  `fail()` writes to `icErr` first, then `icPhonesErr`; on this page only `icPhonesErr` exists, keep that order.
  `Sign out all phones` uses `confirm()` (not shown).

## Remote crew

- **Route:** `/admin#intercom-remote`. **Nav:** Automation. **Module:** `intercom_remote`.
- **Template / script:** `app/modules/intercom_remote/templates/admin.html`, `static/remote.js`. The remote phone
  page (`static/phone.html`) is someone else's.
- **Permission:** nav `intercom_remote.view`; Allow/Remove/Sign out remote phones: `intercom_remote.manage`
  (buttons are not drawn without it).
- **On it:** title with `live` / `not connected`, the intro, the bridge state line (`Connected to Cloudflare`,
  `Lost the link to Cloudflare. Trying again.`, the missing text), Who may connect remotely (table Person/Role/
  Remote phones/actions: `Allow remote` or `Remove remote access`, `Sign out remote phones` when the person has
  remote phones), Remote connections (Person/Started/Ended `now`/Why), error line.
- **Hooks that must stay:** `irSum irState irPeople(table, uses tBodies[0]) irSessions(table, tBodies[0]) irErr`;
  `irState`'s class `note ok|err`.
- **Mockup changes:** four bridge states; avatars; an Allowed / Not allowed badge in the action cell (the real
  page conveys it only by the button label, so the badge is an addition; if kept, render it from `p.allowed`).
  **Real:** the tables are `table.grid` with `tBodies[0]`; keep a real `<table>` and `<tbody>`.

## Parent paging

- **Route:** `/admin#parentpaging`. **Nav:** Live. **Module:** `parent_paging` (needs ProPresenter and Planning
  Center on). `room.html` (the kids workers' page) is someone else's.
- **Template / script:** `app/modules/parent_paging/templates/admin.html`, `static/parentpaging.js`.
- **Permission:** nav `parent_paging.view`; Show, Clear, Clear all: `parent_paging.control`; settings and rooms:
  `parent_paging.manage` (the add row is hidden, the fields disabled, the room buttons not drawn).
- **On it:** title with `N waiting` or `N up`; the needs banner (`Switch on ProPresenter and Planning Center on the
  Modules page: parent paging needs both.`); the error line (ProPresenter's last refusal); Pages up now (code, room,
  `Waiting for you`/`On screen`/`In the ProPresenter message` with age, Show in queue mode, Clear, Clear all,
  `ProPresenter message token: ...`, empty `No parents are being paged.`); ProPresenter (mode select with its three
  labels, message, token, take down after, Save, the note); Rooms (name, `Check-Ins: <event>`, Link with QR, address
  and Copy, New link, Rename, Remove, empty `No rooms yet.`, the add row with Load rooms and Add room, the note).
- **Hooks that must stay:** `ppgSum ppgNeeds ppgErr ppgPages ppgNone ppgScreen ppgClearAll ppgMode ppgMessage
  ppgToken ppgTimeout ppgSave ppgRooms ppgNoRooms ppgAddRow ppgLoc ppgRoomName ppgAddCtl ppgLoad ppgAdd`;
  `data-age="<page id>"` on each status line (the 3 s poll updates ages in place without rebuilding); the
  rebuild-only-when-changed signature logic. The chime needs one click on the page first.
- **Mockup changes:** three states (Pages up, Quiet, Needs setup); the Mode select really shows or hides Show;
  the code is large and tabular; the Nursery link box is open to show the QR. **Real:** the page is polled
  whichever admin page is open (so the chime works elsewhere); keep that listener outside tab state. Not shown:
  `confirm()`/`prompt()` for New link, Rename, Remove; the sound.

## Remote access

- **Route:** `/admin#remoteaccess`. **Nav:** Output. **Module:** `remote_access`.
- **Template / script:** `app/modules/remote_access/templates/admin.html`, `static/remoteaccess.js`.
- **Permission:** nav `remote_access.view`; Enroll and Release this address: `remote_access.manage` (not drawn
  without it; a viewer who is not enrolled sees `An admin enrolls this install to give it a remote link.`).
- **On it:** title with `live`/`not connected`; Remote viewer link (QR, address, Copy link, the state line, the
  two notes, Release this address); Enroll this install (code field `XXXX-XXXX-XXXX`, Enroll, the note, error
  `Enter the enrollment code you were given.`). States: connected, reconnecting, cloudflared missing, not enrolled.
- **Hooks that must stay:** `raSum raAccessNote raLinkBox raQr raUrl raCopy raState raRelease raEnrollBox raCode
  raEnroll raErr`; `.ra-qr` (QR needs a light quiet zone whatever the theme); `raState`'s class `note ok|err`.
  Copy changes its label to `Copied` for 1.5 s; Enroll shows `Enrolling…`.
- **Mockup changes:** a state switch; the QR is a stand-in. **Real:** the inline `<style>` for `.ra-qr` moves to
  the shared sheet; `raLinkBox`/`raEnrollBox` keep their `hidden` toggling.

## Signage

- **Route:** `/admin#signage`. **Nav:** Output. **Module:** `signage`. `player.html` and `player.js` (the TV
  page and the preview player) are someone else's, but the Preview overlay on this page uses `SignagePlayer`.
- **Template / script:** `app/modules/signage/templates/admin.html`, `static/signage.js` (and `player.js` for
  Preview). Thumbnails are made in the browser on upload.
- **Permission:** nav `signage.view`; everything marked `.sg-manage` (forms, buttons, uploads, Save buttons):
  `signage.manage` (hidden without it; `An admin sets up screens, playlists and media.` shows instead).
- **On it (six tabs, already the real structure):**
  - **Screens:** table Now (thumbnail, label, `Offline`, `Last seen ... ago`, `Not opened yet`)/Screen (group,
    `Sound on`)/Playing (playlist, `Nothing assigned`, `Taken over: ... until ...`, `Alert banner showing`, the
    schedule reason)/Address (up to two links)/Preview, Edit, Delete; empty text; Add a screen and its form (Name,
    Address name auto-slug, Group, Default playlist, `Play video sound on this screen`, the note, Save screen,
    Cancel); Screen groups table Group/Playlist for the whole group (select)/Screens/Delete, empty text, New group.
  - **Playlists:** one button per playlist `Name (n)`, New playlist, the editor (title, Name with Rename, Duplicate,
    Preview, Delete; item rows with thumbnail, name, source, `Ended`/`Not started`, Seconds, From, Until,
    Transition Fade/Cut, Fit, Up, Down, Remove; empty text; the note; Save playlist with `Not saved yet`; Add to
    this playlist with a From source select and a clickable media grid).
  - **Schedule:** Schedule (note with the time zone, table Applies to/When/Plays/Edit, Delete, Add a time slot and
    its form: Applies to, Plays, Repeats, First/Last day, Days, All day, From/Until, the note); Put a playlist on by
    hand (On, Playlist, Play now, active overrides with Clear, the note).
  - **Takeovers:** Taken over now (list with Return to playlist, `(no screen shows it now)`, Return all to
    playlist, empty text); Push a file now (To, File, Plays Once/For a set time/Until cleared, Seconds, Push, the
    note); Live captions or service view (On, Show, Switch, the note); Alerts (status line).
  - **Media:** storage pill, `Less than 2 GB free on this disk.`, drop zone with the file types and sizes, upload
    lines (progress, `too large (... the limit is ...)`), cards (thumbnail, name, kind/size/dimensions/length, uses,
    Rename, Delete), empty text, the note.
  - **Settings:** Schedule (Time zone, A playlist put on by hand lasts, Save, note); Alerts on screens (mode, groups,
    Save, note); Screen port (Port, Save, two notes). Above the tabs: the restart-pending line (`sgRestart`).
- **Hooks that must stay:** `[data-sgtab]` and `[data-sgpane]` with `aria-selected` (the script owns tab state), and
  every id in the template: `sgSum sgRestart sgAccess sgScreens sgNoScreens sgScreenAdd sgScreenForm sgScreenName
  sgScreenSlug sgScreenGroup sgScreenPlaylist sgScreenSound sgScreenSave sgScreenCancel sgGroups sgNoGroups
  sgGroupName sgGroupAdd sgPlaylists sgPlaylistName sgPlaylistAdd sgEditor sgEditorTitle sgRename sgRenameSave
  sgDuplicate sgPreviewList sgPlaylistDelete sgItems sgNoItems sgItemsSave sgItemsState sgSourcePick sgSource
  sgPicker sgZoneName sgSlots sgNoSlots sgSlotAdd sgSlotForm sgSlotTarget sgSlotPlaylist sgSlotKind sgSlotFromBox
  sgSlotFrom sgSlotToBox sgSlotTo sgSlotDays sgSlotAllDay sgSlotTimes sgSlotStart sgSlotEnd sgSlotCancel sgOvTarget
  sgOvPlaylist sgOvSet sgOverrides sgTaken sgNoTaken sgReturnAll sgPushTarget sgPushMedia sgPushMode sgPushSecsBox
  sgPushSecs sgPushGo sgLiveTarget sgLiveView sgLiveGo sgAlertNow sgStorage sgLowDisk sgDrop sgFile sgUploads
  sgMedia sgNoMedia sgZone sgOvUntil sgSchedSave sgAlertMode sgAlertSave sgAlertGroups sgPort sgPortSave
  sgPortState sgErr sgPreview sgStage sgPreviewTitle sgPreviewClose`; the class `.sg-manage` on everything that
  needs `signage.manage`; `.sg-item`, `.sg-grid`, `.sg-card.pick`, `.sg-thumb`, `.sg-drop.over` (drag state),
  `.sg-row`. The playlist item inputs are created by script; keep a row shape the script can build.
- **Mockup changes:** the real tab order and names kept; the tab bar becomes the component `.tabs`; thumbnails are
  gradient tiles; a Data switch (populated/empty), a Disk switch (low disk) and a Port switch (restart pending)
  at the top right stand in for states. The inline `<style>` (hard-coded `#111216`, `#000`, `var(--accent)`,
  `rgba(...)` scrim) must be rewritten with tokens. **Real:** refresh keeps skipping redraws while a form is open
  or a playlist is dirty (`editing`, `slotEditing`, `dirty`); the 10 s tick runs only on Screens, Schedule and
  Takeovers. Not shown: the real preview player (the overlay shows a still), drag-and-drop upload in motion, the
  native date pickers' popups, `confirm()`/`prompt()` dialogs, the image/video thumbnail generation.

## Web & YouTube

- **Route:** `/admin#signageweb`. **Nav:** Output. **Module:** `signage_web` (adds two sources to Signage).
- **Template / script:** `app/modules/signage_web/templates/admin.html`, `static/signageweb.js`.
- **Permission:** nav `signage_web.view`; Add, Check again, Rename, Remove: `signage_web.manage` (Add is disabled,
  row buttons not drawn).
- **On it:** title with `N saved`; the intro and the internet note; Add a link (Address, Name (optional), Add shows
  `Checking…` while it runs, the note, error `Enter a web address or a YouTube link.`); Saved links (name, `Web
  page`/`YouTube` · address, the check result line, Check again, Rename, Remove; empty `Nothing saved yet.`; the
  note). Check states: `ok` and (`blocked`, `gone`) coloured, others plain: the mockup shows all five kinds of
  result text from the module.
- **Hooks that must stay:** `swSum swUrl swName swAdd swErr swList swEmpty`; the result line's class `note ok|err`.
- **Mockup changes:** a Saved/Empty switch only. **Real:** rows are built by script (`row()`), so the row layout
  (name, meta, result, buttons) is the contract; truncate the address with an ellipsis as the script does.

## Alert events

- **Route:** `/admin#alertevents`. **Nav:** Automation. **Module:** `alert_events`.
- **Template / script:** `app/modules/alert_events/templates/admin.html`, `static/alertevents.js`.
- **Permission:** nav `alert_events.view`; fields, Save and Send a test event: `alert_events.manage` (disabled).
- **On it:** title with `N output(s) on`; Webhook (Send a webhook, Address, the JSON body note); OSC (Send OSC
  messages, Address, Port, the four-message note); error line; Save, Send a test event and the result text
  (`Saved`, the test's detail line, or an error); Bitfocus Companion note.
- **Hooks that must stay:** `aeSum aeHookOn aeHookUrl aeOscOn aeOscHost aeOscPort aeErr aeSave aeTest aeMsg`.
  `aeHookOn` and `aeOscOn` are checkboxes read with `.checked`; the mockup renders them as switches, so either keep
  hidden checkboxes behind the switches or read `aria-checked`.
- **Mockup changes:** switches for the two checkboxes (the summary count follows them); monospaced event names.
  **Real:** nothing is validated client-side; the server's message goes to `aeErr`. Could add: a sample of the
  Companion JSON.
