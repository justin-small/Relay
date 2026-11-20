# Screens: module pages, audio/video and automation

Mockup file: `docs/mockups/admin-shell/pages/modules-av.js`. Each screen is an admin page that a module adds through
`AdminPage` (`app/modules/__init__.py`); it is rendered in the shell only while its module is on, and its nav link
needs the page's permission (empty means `<module id>.view`). All of these pages are `kind: 'page'`.

| Page id | Title | Module | Group (real) | Permission | Template | Scripts |
|---|---|---|---|---|---|---|
| `atem` | ATEM | `atem` | Output | `atem.view` | `atem/templates/atem.html` | `atem.js` |
| `ppcontrol` | ProPresenter control | `propresenter` | Output | `propresenter.view` | `control.html` | `ppcontrol.js`, `slideview.js` styles |
| `pplive` | Slide view and lobby | `propresenter` | Output | `propresenter.view` | `live.html` | `pplive.js`, `slideview.js` |
| `propresenter` | ProPresenter | `propresenter` | Automation | `propresenter.view` | `settings.html` | `propresenter.js`, `pclink.js` |
| `obs` | OBS Studio | `obs` | Output | `obs.view` | `obs/templates/obs.html` | `obs.js` |
| `youtube` | YouTube | `youtube` | Output | `youtube.view` | `youtube.html` | `youtube.js`, core `static/oauth.js` |
| `tallyarbiter` | Tally Arbiter | `tally_arbiter` | Output | `tally_arbiter.view` | `tally_arbiter.html` | `tally_arbiter.js` |
| `lighting` | Lighting | `lighting` | Output | `lighting.view` | `lighting.html` | `lighting-buttons.js`, `lighting.js`, `screen.css` |
| `planningcenter` | Planning Center | `planning_center` | Automation | `planning_center.view` | `planning_center/templates/admin.html` | `planningcenter.js`, `pcrundown.js`, `pcsignage.js` |

Routes: every page is a `#<page id>` panel (`<div id="page-<id>">`) of the admin page; the API each one talks to is the
module's `route_prefixes` (`/api/admin/atem`, `/api/admin/propresenter`, `/api/admin/obs`, `/api/admin/youtube`,
`/api/admin/tally-arbiter`, `/api/admin/lighting`, `/api/admin/planning-center`).
Not in this file: `obs/templates/screen.html` and `lighting/templates/screen.html` (tablet screens, another file) and
`propresenter/templates/stage.html` (the public `/stage` slide view, a standalone display).

Every mockup page has a row of small tabs under the heading that switches the state (Connected, Not connected, Error,
Not set up, View only...). The tabs are mockup furniture only and are not part of the product.

## Module pages in the new shell

Rules every module page follows, so a module converted later looks like the rest.

1. **Heading.** `<h2 class="t">Title</h2><p class="sub">one sentence</p>` first, exactly the module's `AdminPage.title` and
   the template's current subtitle. The old `<span class="sumstate">` after the title is gone: its text moves to the
   status row.
2. **Status row first.** One bordered strip (`.mv-status`: a `.badge.sbadge` with the level, a bold line, a muted
   second line, actions on the right). The bold line is the module's own `Health` detail (`Connected to ...`,
   `Not set up: ...`), the badge word is `Connected`, `On air`, `Needs a look`, `Not working`, `Not set up` or
   `Standing by` (levels `ok`, `warn`, `err`, none). The right side holds the module's **Test connection** (the same
   `Module.test()` the Modules page runs) and, where it exists, one quick action such as **Check now**. Everything the
   operator needs to know in one second is in this row.
3. **Notes under the status row.** Page-level warnings and errors are `.mv-note` with a coloured left edge
   (`err`, `warn`, `ok`, none). They replace `.note`, `.note.err`, `.note.warn` and the hidden `#xxErr` lines. The
   read-only line `You can view this page but not change it.` is a plain note right under the status row.
4. **One card per section.** Each template `<section><h3>` becomes a `.card` with an `h3` and a one-sentence `.d`
   description (the description is the template's intro paragraph, trimmed). Long help text that explains a field stays
   with the field (muted line under it); long help that explains a whole section goes in the card description or in a
   closing muted paragraph. Optional count or state next to the title is a muted `.mv-cnt` (`2 followed`, `running`).
5. **Settings are a form grid.** Fields are `.fld` (label above input) in an auto-fit grid (`.mv-form`, minimum
   190px, a max-width of 520 to 640px for short forms). On/off options are switches (`.sw` with a bold label and a
   muted explanation), never bare checkboxes; the only checkboxes left are in ticked lists (timers, service types,
   audio inputs) where a row of switches would be noise. Number fields keep their `min`, `max`, `step` and units in
   the label (`Seconds per slide`).
6. **Form footer.** A card with a form ends in a footer strip (top border): status text on the left (`small`, the old
   `#xxStatus` line), buttons on the right with the primary action last: `Save`, never a second primary. Secondary
   buttons that act on the saved settings (`Test connection`, `Find on this network`) sit with the footer or in the
   status row, not between fields.
7. **Live control cards are separate from settings cards.** A module that both shows live state and has settings puts
   the live part (status, scenes, slides, cues) above, and the settings below in their own cards or in a
   collapsed `Settings` disclosure when each item (a switcher, an OBS computer) has its own.
8. **Destructive and live actions.** `.btn.d` for Remove, Delete, Stop stream; the existing `window.confirm` text is kept.
   Anything that changes the show (Cut, Take, Start stream) keeps its confirm where it has one today.
9. **Lists** (cameras, cue rows, tally rows, log lines) are a `.mv-list` (rows separated by a hairline), a data
   table (`.dt` inside `.mv-over` for horizontal scroll) when there are three or more columns.
10. **States.** Every page shows connected, not connected/error, not set up (empty), and view-only. Empty text is the
    module's real text. Errors come from the module and are shown as text only (never as HTML).
11. **Icon.** The nav icon is the module's `AdminPage.icon` inner SVG; the shell takes it from the manifest, so a module
    needs nothing more than a valid 16x16 inner SVG that draws in `currentColor` and fills with tokens (see
    `pplive`, whose real icon filled with a literal near-black; the mockup uses `var(--card)`).
12. **Several pages in one module** share a sidebar parent (the module name) and are listed Control, then the
    page the operator uses in the booth, then Settings last.
13. **Hooks.** Keep every `id`, `data-*` attribute and class a module script finds. The restyle changes classes that
    exist only for looks.

## Shared notes for the real implementation

- The mockup helpers (`mvStatus`, `mvCard`, `mvF`, `mvSw`, `mvKv`) are mockup code. In the product they become Jinja
  macros in one `app/templates/_module_page.html` (`status_row`, `card`, `field`, `switch`, `kv`) so a module template is
  its own sections only.
- The scripts that fill these pages build DOM with `el(tag, text, cls)` and the admin pieces `.pcard`, `.spill`, `.pactions`,
  `dl`, `details.sect`, `.field`, `.note`, `.row`, `.ctl`, `.check`, `.plist`, `.tablescroll`. Where the mockup renames a
  piece, the script keeps working by one of two routes: keep the old class on the element next to the new one (cheapest),
  or change the one `el(...)` call. The old pieces are listed per screen under "Hooks".
- Live parts redraw by polling (ATEM 2 s, Tally Arbiter 2 s, YouTube 15 s, OBS meters 300 ms, Planning Center 3 s,
  ProPresenter 2 s). The redesign changes none of that; do not rebuild the settings form under a field being typed in
  (every script has a guard for it: `busy()`, `dirty`, `editing`, `formShown`, `document.activeElement`).
- No colour in a page may be a literal. The ProPresenter slide thumbnails, the OBS preview and the lighting buttons are
  drawn from tokens in the mockup; the real thumbnails are `<img>` from the module, and a cue's own colour is its data
  (`color`, `text_color`, set inline from the stored hex, as `lighting-buttons.js` does today).

---

## atem (ATEM)

- **Route / files.** `#atem`; `app/modules/atem/templates/atem.html`, `static/atem.js`; API `/api/admin/atem`.
- **Permission.** page `atem.view`; `atem.control` shows the Control block (Set preview, Set program, Cut, Auto, Fade to
  black, downstream keys, macros); `atem.manage` shows the switch, Settings, Find on the network, Add by address.
- **On the page.**
  - Status row: `all 2 switchers connected` / `N of M connected` / `not set up`, Test connection.
  - Notes: `#atErr` (error), `#atWarn` (overlapping tally ranges, text from the server).
  - Card **Switchers**: one block per switcher with name, state pill (`switched off`, `connected to <product>`, the error,
    `connecting…`), a connect switch (manage), a facts line (`host:port · model · protocol · tally cameras 1–40 ·
    does not drive tally · followed by its network id`), the old-firmware note, the `refound` note, a **Settings**
    disclosure (Name, Address, Port, First camera number, Drives the tally lights, help text, Save, Remove) and a
    **Control** disclosure (per M/E: program and preview text, source select, Set preview, Set program, Cut, Auto, Fade to
    black; per downstream key: Auto, On air, Off air; Macros with Stop macro or `None recorded on the switcher.`).
    Empty: `No switchers yet. Find one on the network below, or add it by its address.` and the DHCP note.
  - Card **Find on the network**: Scan, `Also try every address on this subnet`, a note (`Found N devices.` or
    `Nothing answered. ...`), table Device (name, model under it), Kind, Address, Firmware, action (`Add`, `Added`,
    `Not controlled yet`; Add disabled with a title when no address).
  - Card **Add by address**: Name, Address, Port (9910), Add switcher.
- **Hooks that must stay.** Ids `atSum`, `atErr`, `atWarn`, `atDevices`, `atNone`, `atFind`, `atScan`, `atProbe`,
  `atScanNote`, `atFound` (table), `atAdd`, `atName`, `atHost`, `atPort`, `atAddBtn`; `.acard` with `data-id` on each
  switcher (open state of its `<details>` is kept by index across redraws, so keep order: Settings, then Control);
  `window.RelayAdmin` and `window.relayCan`; the page container `#page-atem`.
- **Mockup changes.** `atSum` becomes the status row. Notes are `.mv-note`. The scan note and table are inside one
  result container (`atScanOut` in the mockup); the real script keeps its two nodes. The per-switcher rule lines
  (`border-top` inline) become the list hairline. Switches replace the checkbox `label.switch`.
- **Real implementation.** Put the status row in the template and have `draw()` write `atSum`'s text into the badge and
  line; keep `acard` rebuild-on-change. The Scan button keeps its `Scanning…` text. Nothing needs a new endpoint.

## ppcontrol, pplive, propresenter (ProPresenter)

One module, three pages, one sidebar parent `ProPresenter` (`parent:'ProPresenter'` in `reg()`), listed Control, Slide
view and lobby, Settings. The mockup lists all three under **Output**; the module registers Settings under Automation
today, and a parent cannot span two groups, so Settings moves to Output with its siblings (the module's
`AdminPage.group` for `propresenter` changes to `"Output"`). Pages carry short titles in the sidebar (`Control`,
`Slide view and lobby`, `Settings`) under the parent; their `<h2>` keeps the full real title.

### ppcontrol (ProPresenter control)

- **Route / files.** `#ppcontrol`; `control.html`, `static/ppcontrol.js`, `slideview.css`, `propresenter.css`;
  API `/api/admin/propresenter/*` (`/slides`, `/playlists`, `/looks`, `/macros`, `/timers`, `/messages`, ...).
- **Permission.** page `propresenter.view`; buttons need `propresenter.control` (else the note `You can view this page
  but not change it.`, `#ppCtlAccessNote`) and are off in view-only mode; `Choose what operators see` needs
  `propresenter.manage`.
- **On the page.** Cards: **Slides** (Previous, Next, Previous item, Next item with their key titles, the now box:
  title, part chip, slide x of y, playlist, current text, `Notes:`, `Next:`, empty `Nothing is up.`, the key help note),
  **Screens and announcements** (Audience screens: On/Off/?, Stage screens, screen list, the announcement box and its
  empty line, the view-only note), **Slide grid** (name, Reload slides, cue cards with group, number, LIVE marker, note
  lines `No presentation is up...`, `This presentation has no slides.`), **Playlists** (select, Reload, Check this
  playlist and its table `# Type Name Presentation Item ID Check`, header marks, item rows with thumbnail, number, name,
  tag and a `Slides` strip, `There are no playlists.`, `This playlist is empty.`), **Looks** (`No looks.`), **Macros**
  (buttons, notes, `Choose what operators see` with `Show every macro` and a ticked list), **Timers** (name, time,
  Start, Stop, Reset; `No timers.`), **Messages** (name, text, one input per text token, Show, Clear; `No messages are
  prepared in ProPresenter.`), **Media position** (seconds, `Go to time (seconds)`). A bottom dock **Clear**: Slide, Media,
  Props, Messages, Audio, Video input, Announcements (lit while that layer has content).
- **Hooks that must stay.** `pcSum`, `ppCtlAccessNote`, `pcErr`, `pcPrev`, `pcNext`, `pcPrevPres`, `pcNextPres`, `pcNow`,
  `pcScreens`, `pcAud`, `pcStageScr` (`data-screens`), `pcScreenList`, `pcAnn`, `pcGridName`, `pcGridRefresh`, `pcGrid`,
  `pcGridNote`, `pcPlaylist`, `pcPlaylistRefresh`, `pcDiag`, `pcDiagOut`, `pcItems` (`data-pid`), `pcPlaylistNote`, `pcLooks`,
  `pcMacros`, `pcMacroNote`, `pcMacroChoose`, `pcMacroPick`, `pcMacroAll`, `pcMacroList`, `pcMacroSave`, `pcTimers`,
  `pcMessages`, `pcSeconds`, `pcGoTo`, the dock `ppDock` with `[data-layer]` buttons, the data hooks `data-index`, `data-uuid`,
  `data-macro`, `data-timer-op`, `data-msg-op`, the classes `.ppcue` / `.ppitem` / `.ppitemrow` / `.ppstrip` / `.ppmark` /
  `.pptimer` / `.ppmsg` and `.lit` on the dock. The arrow-key handler is on the page; the `title` attributes carry the key help.
- **Mockup changes.** The fixed dock (`position:fixed; left:220px`) becomes a sticky bar at the bottom of the page card
  area (`position:sticky; bottom:0`) so it works with the inset layout and any sidebar width; its left offset is deleted.
  Slide thumbnails are drawn blocks in the mockup (`<img>` in the product). Button sizes: Previous/Next stay large for the
  booth. Switches/selects use shell controls.
- **Real implementation.** Change the dock CSS only (`#ppDock{position:sticky;bottom:0;left:auto;right:auto}`, move the
  element inside the page panel); keep `hidden` toggling by `ppcontrol.js`. Cue and item cards keep their classes; restyle them
  with tokens (`--primary` for LIVE, group colour still comes from `--pp` set inline by the script).

### pplive (Slide view and lobby)

- **Route / files.** `#pplive`; `live.html`, `static/pplive.js`, `slideview.js`, `slideview.css`; the public view is `/stage`.
- **Permission.** `propresenter.view`; the rotation buttons need `.control`, the settings `.manage`
  (`You can run the rotation but not change its settings.`).
- **On the page.** Cards: **Slide view** (the `RelaySlideView` component: title, part chip, `slide x of y`, Live and Next
  panes, countdown, media progress, notes `ProPresenter is not set up.`, `Waiting for ProPresenter.`, `Nothing is up.`),
  `Countdown on the slide` select, public switch (`Let the slide view be opened without the admin login, at /stage ...`),
  Save and message; **Lobby announcements** (state count, Playlist, Seconds per slide, `Start over when it reaches the end`,
  Save, Start/Restart, Resume (only when paused), Stop, `Stop and clear announcements`, the state line and its variants
  (`Not running.`, `Running: ... (item n of m, slide k).`, the two `Paused:` lines, `Waiting for ProPresenter to come back. Nothing is changed meanwhile.`,
  `Finished: ...`), view-only note); **Announcements on signage screens** (count `on` / `needs attention`, Playlist, switch,
  Save, Sync now, state `Off.` / error / `Waiting for the first copy.` / `N slides in the signage playlist "..."`).
- **Hooks that must stay.** `svSum`, `svAccessNote`, `lbErr`, `svRoot`, `svTimer`, `svPublic`, `svSave`, `svMsg`, `lbSum`,
  `lbPlaylist`, `lbInterval`, `lbLoop`, `lbSave`, `lbStart`, `lbResume`, `lbStop`, `lbStopClear`, `lbState`, `lbMsg`, `sgSum`,
  `sgPlaylist`, `sgOn`, `sgSave`, `sgSync`, `sgState`, `sgMsg`; `.sv`, `.sv-*` classes of `slideview.css`.
- **Mockup changes.** The slide view panes are drawn with token colours; the countdown pill and media bar sit on the
  Live pane as today. Resume stays hidden until paused. The three cards are independent forms with their own Save.
- **Real implementation.** `slideview.css` has to move its dark literals to tokens; the component's markup is unchanged.

### propresenter (ProPresenter settings)

- **Route / files.** `#propresenter`; `settings.html`, `static/propresenter.js`, `pclink.js`.
- **Permission.** `propresenter.view` to see; `.control` allows the stage message only
  (`You can change the stage message here but not the setup.`); `.manage` everything else. Separately the **View-only**
  setting (a module setting, not a permission) disables every change to ProPresenter.
- **On the page.** **Connection** (Machine name or address, Port, `Follow ProPresenter`, Save, Test connection,
  `Find on this network` with the found list as buttons `name · address:port`, `Nothing answered...`, the state line
  `Off. Enter the address...` / `Connected to ... on ...` / error, and the long setup note), **Safety** (View-only switch),
  **Now** (Presentation, Slide, current and next slide card, Look, Layers with content; `Not following ProPresenter.`,
  `Waiting for ProPresenter.`), **Lyrics feed** (licence note, four switches for Screens, Broadcast overlay, Caption
  panels, Phone viewer, `Songs only`, `Library that holds the songs`, `Save lyrics settings`), **Timers on panels** (Amber at,
  Red at, help, Timers to show list (up to four), `Save timer settings`), **Stage message** (field, Show, Clear, notes),
  **Slide-note tags** (the tag note, `Run slide-note tags`, `Settle time (seconds)`, `Save tag settings`, what was read from
  the slide: Slide up, Tags on the slide, Deck-wide tags, Runs for this slide, warnings, `On signage screens because of
  tags`, `What ran`), **Planning Center link** (`Follow the plan`, `ProPresenter drives Services LIVE`, `Stay up for
  (seconds)`, `Also go back`, Service type (only with two or more), `Save link settings`, status lines, `Match the plan
  now`, `Build the Relay playlist`, build note, the match table `Plan item, Status, ProPresenter presentation, How`, per-row
  `Pair` input with `Presentation name` and `Unpair` / `Pair other`).
- **Hooks that must stay.** `ppSum`, `ppAccessNote`, `ppHost`, `ppPort`, `ppEnabled`, `ppSave`, `ppTest`, `ppFind`, `ppFound`,
  `ppState`, `ppViewOnly`, `ppNow`, `ppLyScreen`, `ppLyOverlay`, `ppLyPanels`, `ppLyViewer`, `ppSongsOnly`, `ppLibrary`,
  `ppLyricsSave`, `ppTmAmber`, `ppTmRed`, `ppTmHelp`, `ppTmPick`, `ppTimersSave`, `ppStage`, `ppStageText`, `ppStageSet`,
  `ppStageClear`, `ppStageNote`, `ppTagsOn`, `ppTagsSettle`, `ppTagsHelp`, `ppTagsSave`, `ppTagsSeen`, `ppLink`, `plkFollow`,
  `plkDrive`, `plkDwell`, `plkBack`, `plkStField`, `plkSt`, `plkSave`, `plkMsg`, `plkStatus`, `plkMatch`, `plkBuild`,
  `plkBuildNote`, `plkTable`, `plkNames` (datalist). The `.plist`, `.mono`, `.muted`, `.check` classes are used by the
  scripts (`ppKv`, `timerPicker`); keep them next to the new ones.
- **Mockup changes.** Status row replaces `ppSum` and `ppState` (both texts stay, `ppState` as the note under the
  connection form). The 8 sections remain one page; a jump list (`Connection`, `Lyrics`, `Timers`, `Stage message`,
  `Tags`, `Planning Center link`) is recommended in the shell's page header slot but not drawn here. The Planning Center
  link section keeps its place at the end. The mockup has a `Can view only` and a `View-only mode` state to show the two
  different read-only meanings.
- **Real implementation.** `ppKv` rows become `.mv-kv`; `ppState`'s `ok`/`err` class becomes the note colour. The
  Save buttons per section stay (each saves only its section).

## obs (OBS Studio)

- **Route / files.** `#obs`; `obs/templates/obs.html`, `static/obs.js` (its `screen.html` is another file); API
  `/api/admin/obs` (`/settings`, `/output`, `/scene`, `/studio`, `/mute`, `/audio/<id>`, `/screen/<id>`, `/test`).
- **Permission.** `obs.view`; `obs.control` shows output buttons, scene buttons, studio mode, Mute; `obs.manage` shows
  the settings, Add OBS computer and Save.
- **On the page.** Status row (worst health across machines: `on air`, `Healthy`, `Needs a look`, `Trouble`, `Standing by`),
  the error line, then one **card per OBS computer** (name, `host · OBS version`): a status box (pills `LIVE h:mm:ss` or `Off
  air`, `REC` / `Paused`, health word, detail line when not ok), key-value rows (Program (and preview in studio mode),
  Streams to with `Open in YouTube Studio` and `Watch page` when OBS streams to YouTube, Stream sent and congestion,
  Dropped frames, Last minute, Recording, Replay buffer, Captions, OBS version fps, render, CPU, disk), output buttons
  (Start/Stop stream with confirm, Start/Pause/Resume/Stop recording, Start/Save/Stop replay buffer), a Program preview
  or a Bitrate chart (`The bitrate chart shows here while OBS streams.`), a **Scenes** box (Studio mode, Take: scene,
  `Studio mode: tap a scene to put it in preview, then Take.`, scene buttons with program primary and preview outlined), an
  **Audio** box (meter per watched input, level, MUTED, `not in OBS`, Mute/Unmute) and a **Settings** disclosure: Name,
  Address, Port, Password (`saved; leave blank to keep it`), preview switch; **Captions into the stream** (switch,
  Language, Delay in seconds); **Alerts** (two selects, `Add alert buttons on the Alerts page to choose them here.`);
  **Audio to watch** (per input: watch and `Warn if muted while live`; `Connect to OBS to list its audio inputs.`);
  **Tally** (`Camera [n] is scene [select]`, Remove, Add camera, `(not in OBS now)`); **Scene screen for a tablet**
  (Turn screen on/off, New link, link field); **This computer** (Test connection, Forget password, Remove computer, result
  line, `Changes take effect when you press Save at the bottom of the page.`). Below the cards: Add OBS computer, Save
  and the OBS WebSocket note. Empty: `No OBS computer yet. Add the computer that runs OBS.`; unsaved: `Save to connect.`
- **Hooks that must stay.** `obsSum` (also read for the `.live` span), `obsErr`, `obsMachines`, `obsAdd`, `obsSave`;
  per machine `.obsmachine`, `.obslive` (with its `data-sig`), `.obsstatus` (`.phead`, `dl`), `.obsside`, `img.obspreview`
  (`data-machine`), `.obschart`, `.obsscenes`, `.obsaudio` (`data-machine`) with `.meterwrap` (`data-input`), `.meter > i, b`
  (`.clipping`, `.hot`), `.meterread` (`.mutedread`), `.obsmute` (`data-muted`), `details.sect`. `redrawLive()` swaps nodes
  by these selectors, and `pollAudio()` writes meter widths: do not rename them.
- **Mockup changes.** Per-machine blocks become cards; the live part uses two bordered boxes in a two-column grid; the
  meters use the shell's `.meter` with a peak marker. The preview and chart are drawn from tokens. Switches replace the
  checkboxes in Settings. Buttons: Stop stream is `.btn.d`, Start stream `.btn.p`.
- **Real implementation.** Re-point the injected `<style>` in `obs.js` (it sets `#page-obs ...` rules with `--dim` and
  `--accent`) to the new tokens (`--muted`, `--primary`) or delete it in favour of the shared classes; keep the
  selectors above on the markup.

## youtube (YouTube)

- **Route / files.** `#youtube`; `youtube.html`, `static/youtube.js`, core `app/static/oauth.js` (the Connect card,
  `window.RelayOAuth.mount($('ytAccount'), '/api/admin/youtube/account', ...)`); API `/api/admin/youtube` (`/check`,
  `/settings`, `/account/*`).
- **Permission.** `youtube.view`; `youtube.manage` shows Check now, Connect/Disconnect and the settings.
- **On the page.** Status row (`on air`, health word, detail). Two cards: **Live status** (pill `On air` / `Standing by` /
  `Needs a look`, the health note, one block per broadcast: title, State, Live for, Watching now (`not shown by YouTube`),
  Privacy, `Watch page`, `Open in YouTube Studio`; `No broadcast is live or being tested.`; footer `Checked ... Relay
  checks every N min while something is on air and every N min otherwise.`, Check now) and the **account** card from
  `oauth.js` (service name, state pill `Connected` / `Waiting for you` / `Not connected` / `Sign in again`, Account,
  Connected (time, by), Access, the device code view with `On any phone or computer, go to ... and enter this code:`,
  the code and its minutes note, Connect / Connect again / Cancel / Disconnect, the intro `Connect the church's YouTube
  channel...`, `YouTube stopped accepting Relay. Press Connect to sign in again.`). **Settings**: seconds between checks
  while on air (30 to 600) and otherwise (120 to 3600), the daily allowance note, and a **Your own Google app** disclosure
  (Client ID, Client secret `Saved; leave blank to keep it`, the two notes), `Use Relay's app`, Save.
- **Hooks that must stay.** `ytSum`, `ytErr`, `ytLive`, `ytAccount`, `ytSettings`, `ytForm`, `ytLiveS`, `ytIdleS`, `ytCid`,
  `ytSecret`; `.ytbc` per broadcast; the OAuth card markup of `oauth.js` (`.pcard.oauthcard`, `.oauthcode`, `.oauthsteps`,
  `.phead`, `.pname`, `.spill`, `.pactions`).
- **Mockup changes.** `#ytSettings` stops being a collapsed `<details>` and becomes a card, with the Google app part
  collapsed inside it; the two cards side by side. The sign-in states (waiting, sign in again) are all shown.
- **Real implementation.** `oauth.js` is shared core: restyle it once (`.oauthcard`) for every module that uses it
  (Planning Center does not, Google and any other device-sign-in do); the card header becomes the card `h3` plus pill.

## tallyarbiter (Tally Arbiter)

- **Route / files.** `#tallyarbiter` (the module id is `tally_arbiter`, the AdminPage id has no underscore);
  `tally_arbiter.html`, `static/tally_arbiter.js`; API `/api/admin/tally-arbiter` (`/settings`).
- **Permission.** `tally_arbiter.view`; `.manage` enables Address, Port and Save.
- **On the page.** Status row (`connected` / `not connected` / `not set up`), card **Server** (Address, Port 4455, Save,
  status line `Connected.` / `Enter the server's address to begin.` / the error / `Connecting…`), error line, card **Cameras**
  (`n. name · on program` / `· on preview`; `None yet.`).
- **Hooks that must stay.** `taSum`, `taHost`, `taPort`, `taSave`, `taStatus`, `taErr`, `taList`, `taEmpty`. The script skips
  redrawing the fields while `editing` is set.
- **Mockup changes.** Program and preview rows also get a `PGM` / `PVW` badge (new, additive, optional); the status row
  adds Test connection.
- **Real implementation.** The `PGM`/`PVW` badge is an option, not required; everything else is markup only.

## lighting (Lighting)

- **Route / files.** `#lighting`; `lighting.html`, `static/lighting-buttons.js`, `lighting.js`, `screen.css`
  (`window.LightingButtons.draw` is shared with the tablet screen); API `/api/admin/lighting`.
- **Permission.** `lighting.view`; `lighting.control` fires cues and moves sliders; `lighting.manage` edits the program
  and the pages.
- **On the page.** Status row (`ready` / `not set up` / the service error), card **Lighting program** (Program select
  (`Choose a program`, QLC+ (web API), Lightkey (OSC, macOS), Chroma-Q Vista (OSC), ShowXpress / TheLightingController
  (Live API)), Address, Port, `Console type (Vista)`, `Fade time, seconds (Lightkey)`, Password `(saved)`, `Also list the
  program's functions (scenes and chases) that have no button`, Save, Test connection, status line, the fired-once note),
  error line, card **Cue pages**: per page a name field, cue rows (fire button or slider with percent, Look, Up, Down,
  Remove), `Cue id` and `Name` with Add cue, page Up, Down, Delete page, `Place buttons freely (drag and resize)`, `How it
  looks on the screen` (the preview drawn by `LightingButtons`, free pages with drag and a resize handle), the look form (Text, Shape
  Rounded / Square corners / Pill / Circle, Colour, Text colour, Wide and Tall in cells), screen controls (Turn screen on/off,
  New key, Show link and the link field, `Save the pages first to get this screen's link.`), Add page, Save pages, and
  **All** (`Cues the program lists that are not on any page.` with fire buttons and `Add to first page`).
- **Hooks that must stay.** `ltSum`, `ltBackend`, `ltHost`, `ltPort`, `ltConsole`, `ltFade`, `ltPass`, `ltFunctions`, `ltSave`,
  `ltTest`, `ltStatus`, `ltErr`, `ltPages`, `ltPageCtl`, `ltAddPage`, `ltSavePages`, `ltAll`, `ltAllCues`; per page the `_preview`
  and `_form` nodes, `.ltcue` (with `data-shape`), `.ltgrid`, `.ltfree`, `.ltresize`, `.picked`.
- **Mockup changes.** Each page is an inner bordered box in the Cue pages card; cue colours are tokens in the mockup (in
  the product they are the stored `color` / `text_color`). The Colour fields are native colour inputs.
- **Real implementation.** `lighting.js` builds with bare `.card` and `.row`; swap to the shared card and row classes. The
  free-layout drag code is untouched. The preview needs `screen.css` as today.

## planningcenter (Planning Center)

- **Route / files.** `#planningcenter`; `planning_center/templates/admin.html`, `static/planningcenter.js`, `pcrundown.js`,
  `pcsignage.js`; API `/api/admin/planning-center` (`/live`, `/signage/sync`, ...). The Signage module's *Planning Center plan
  item* source reads from here.
- **Permission.** `planning_center.view`; `.control` chooses today's plan and moves LIVE (Previous, Next, Take control,
  Release); `.manage` changes the setup (`You can choose the plan for today but not change the setup.`).
- **On the page.** Status row (`connected` / `not working` / none, the error or `Connected. The service type list
  refreshes...`), card **Connection** (Application ID and Secret as password fields, Save, Test connection, Disconnect, state
  line, the Personal Access Token note), **Service types Relay follows** (ticked list, Save selection, `n followed.` /
  `Unsaved changes...`, `This account has no service types.`), **Display** (`Show item notes on panels`), **Today** (per followed
  type a select `Plan followed today for <type>` with `Relay's pick for today`, and a line `Live · <who> is controlling · item
  · mm:ss left · service mm:ss behind · next: ... · checked ...`; the day-boundary line; **Presses from panels** with the
  time, panel, action and result), **Rundown** (Service type, Service time, the LIVE pill, Previous, Next, Take control,
  Release, the rules note, the item table `Planned, Actual / proj., Item, Leader, Mics, Length` with current and done rows,
  the times note), **Mics by position** (explanatory note, table `Team, Position, Mics, Sings, Label, This service` and Redraw /
  Remove, position select, Add position, Save mics, Redraw all), **Rundown on /screen** (switch with the privacy note, Song
  leader item note, `...or else this position`, Sermon notes item note, Save, and **Link for a screen**: View, Order of service,
  People, Teams, Service time, the generated `/screen?...` link and Copy), **Plan files on signage screens** (Service type,
  `Only plan items whose title has`, four switches, `Note category for text slides`, Save, Sync now, state).
- **Hooks that must stay.** `pcConnSum`, `pcSetupAccessNote`, `pcConnBox`, `pcAppId`, `pcSecret`, `pcSave`, `pcTest`,
  `pcDisconnect`, `pcState`, `pcTypes`, `pcTypeList`, `pcTypesSave`, `pcTypesNote`, `pcDisplay`, `pcNotesRow`, `pcNotes`,
  `pcToday`, `pcTodayList`, `pcTodayNote`, `pcActions`, `pcActionList`, `rdBox`, `rdType`, `rdTime`, `rdLive` (with
  `data-live` buttons), `rdNow`, `rdLiveMsg`, `rdItems`, `rdTimes`, `rdMicBox`, `rdMics`, `rdAddPos`, `rdAdd`, `rdMicSave`,
  `rdRedrawAll`, `rdMicCtl`, `rdMicMsg`, `rdSetBox`, `rdScreen`, `rdLeaderNote`, `rdLeaderPos`, `rdSermonNote`, `rdSetSave`,
  `rdLView`, `rdLLayout`, `rdLCards`, `rdLTeam`, `rdLTime`, `rdLink`, `rdCopy`, `rdSetMsg`, `sgBox`, `sgSum`, `sgType`,
  `sgMatch`, `sgItemFiles`, `sgPlanFiles`, `sgText`, `sgNote`, `sgPlaylist`, `sgOn`, `sgSave`, `sgSync`, `sgState`, `sgMsg`;
  classes `.rdtable`, `.cur`, `.done` on rows, `.plist`, `.pill`, `.forget`, `.tablescroll`; the sections are shown or hidden
  by `hidden` (`pcTypes`, `pcDisplay`, `pcToday`, `rdBox`, `rdMicBox`, `rdSetBox`, `sgBox`).
- **Mockup changes.** Sections that are hidden until connected stay hidden in the not-connected state. The `Error`
  state shows the connection card with the failure and hides the rest (the real page keeps the rest visible and only
  puts the error on the state line; either is fine, the mockup shows the clearer one). Mic inputs, `Sings` checkbox and
  label are inline in table cells as today.
- **Real implementation.** The page is the longest in the set; do not split it. Keep show/hide by `hidden`. Table
  `.rdtable` becomes `.dt` with the existing row classes.

## What the shell lacked

- A sidebar parent takes its icon from the first sub page. Wanted: an optional `picon` on `reg()` (module icon on the
  parent) so ProPresenter can show its own icon while Control keeps the page icon.
- A parent cannot appear under two groups (the mockup moves ProPresenter Settings from Automation to Output).
- No helper for a status row, a settings form footer, or a key-value list. The mockup defines them locally (`mv-`
  prefix); they should be promoted to shell components (the Jinja macros named above).
- No bottom-sticky action bar component (the ProPresenter Clear dock uses `position:sticky` inside the page).
