# Crew and phone screens

Mockup: `docs/mockups/admin-shell/pages/screens-crew.js` (category "Crew & phones", ten standalone screens). Open
`index.html#screens`. Every screen has a floating **Demo states** chip (top right) that switches role, network, booth,
data and, on the intercom, link, channels, press outcome and audio route.

Not covered: `app/modules/intercom/templates/phones.html` is the **admin** "Signed-in phones" table (permission
`intercom.manage`), rendered inside the intercom admin page; it belongs to the operator pages. What a crew member sees
of their own phones is the "My phones" disclosure on the intercom tab (below).

## Shared design: the crew app shell

The real `/crew` is one scrolling page: header, cards (Today, Plan, Check in, Pages, Tools), then a pill tab strip
(Chat, Checklists, Resources, Activity, Intercom) and the selected panel, then Sign out. The mockup restructures it
for a phone held in one hand:

| Real | Mockup |
| --- | --- |
| Pill tab strip under the cards | **Bottom tab bar**, 58px tall, icon plus label, `role=tablist`. A new first tab **Today** holds the cards. Activity appears only for `crew.activity` holders (operators), so the bar has 5 or 6 items. |
| Offline note `#crNet` at the very bottom | A warn banner under the top bar, visible on every tab. |
| `.cr-bar` header | Same content (mark, app name, `name · role`), kept as a fixed top bar; the tab panel scrolls beneath it. |
| Booth off: default tab becomes Checklists | Booth off: **Today** is the landing tab, call time and position first. Checklists is one tap away. (Same intent as #234.) |
| Plain lists | Cards with the shared `--card`, `--border`, `--r`; 44px minimum touch targets; 16px inputs (no iOS zoom). |
| none | Unread count on the Chat tab (new), progress "3 of 6" on each checklist (new). Both optional, drop if the server cannot supply them cheaply. |

Hash routing stays: `#chat`, `#checklists`, `#resources`, `#activity`, `#intercom`; add `#today` (and no hash means
Today). The `crew:tab` event, `role=tab`/`tabpanel` ids (`tab-<id>`, `panel-<id>`, `data-tab`) and the arrow-key
behaviour in `crew.js` must stay; tabs are still contributed by modules through `CrewTab`, so the **bar is built from
`tabs`**, not hard-coded. Six tabs fit at 430px with 11px labels; if a module set ever adds a seventh, the bar must
fall back to four tabs plus a "More" sheet (not mocked).

### How a module links the shared stylesheet (the tokens problem)

Today `crew.css` styles against variables that `app/static/viewer.css` happens to define (`--bg --fg --line --dim
--accent --warn`). The redesign has a different token set (`--bg --fg --muted --card --border --primary --primary-fg
--hover --ok --warn --err --r --shadow`, four presets by light/dark) that lives in the admin shell.

Do this:

1. **Core extracts the tokens into one public stylesheet**, `app/static/tokens.css` (preset and theme variables, the
   `[data-theme]`/`[data-preset]` selectors, plus the status colours). `admin.css` and `viewer.css` `@import` or are
   built from it. Core also publishes `components.css` (buttons, cards, inputs, badges, switches: the classes the
   README lists) for modules that want them. These two files are the *documented contract*; nothing else in core's
   CSS is for a module to depend on.
2. A module template links core assets **by URL, not by import**: `<link rel="stylesheet" href="{{ static('tokens.css') }}">`
   then `<link rel="stylesheet" href="{{ static('modules/crew/crew.css') }}">`. `static()` is already what `crew.html`
   uses for `viewer.css`. This does not break "never import a module from core" nor "no integration in core": the
   dependency points from module to a stable core asset, and `tests/test_modules.py` is unaffected (it checks Python
   imports and core files, not asset URLs). Add a line to `docs/modules.md` ("a module may link `tokens.css` and
   `components.css`, nothing else from core's static").
3. `crew.css` is rewritten onto the shared names. Old to new: `--line` to `--border`, `--dim` to `--muted`, `--accent`
   to `--primary`, text on accent `var(--bg)` to `var(--primary-fg)`, plus `--card` for surfaces. Keep the `cr-*`,
   `chat-*`, `cl-*`, `rs-*` class names (scripts and tests use them). During migration keep a 6-line alias block at
   the top of `crew.css` mapping the old names, so `viewer.css` consumers still work.
4. **Theme and preset on a phone.** The crew app is on the same HTTPS origin as the panel, so it reads the same
   preference (the shell's stored theme/preset key; confirm the exact name when the shell is implemented) in a tiny
   inline script in `<head>` that sets `data-theme`/`data-preset` before first paint, and falls back to
   `prefers-color-scheme` and the `relay` preset. Crew accounts that never opened the panel therefore get the default
   preset in the phone's own light/dark. An operator's preset choice is not synced to other people's phones; if the
   church wants one look for all crew, add a `crew` module setting (`preset`) rendered into the template as
   `data-preset`.
5. `<meta name="theme-color">` and the manifest `theme_color`/`background_color` are currently `#000000`. Set them from
   the preset's `--bg` (a small server-side map keyed by preset, light/dark via two `theme-color` metas with `media`).
   The manifest is static per install: use the default preset's dark `--bg`.
6. **Service worker**: add `/static/tokens.css` and `/static/components.css` to `SHELL` in `sw.js` and bump `VERSION`
   (`crew-v3` to `crew-v4`). The `SHELL.indexOf(...) > 0` test treats index 0 (the offline page) specially; keep the
   offline page at index 0.
7. **Two pages cannot link external CSS and must stay self-contained**:
   * `offline.html` is served from the cache while the network is down; it keeps inline `<style>` (a generated
     ~1.5 KB block of the default tokens, dark and light via `prefers-color-scheme`). It may also link the cached
     `tokens.css`, but must look right without it.
   * `join.html` is served on the plain-HTTP join port, whose listener only exposes `/crew-join` (the `Listener`
     prefixes), so `/static/` answers 404 there. Inline the same token block. Do not widen the join listener for CSS.
   Both therefore duplicate the token block; generate it from `tokens.css` at build time (a test compares them) so
   they cannot drift.
8. `parent_paging/templates/room.html` already defines its own `:root` palette inline. Replace it with the shared
   tokens: link `tokens.css` (the page is on the HTTPS operator port, `/static` is served there; verify the page
   gate does not block static) and keep the inline `<style>` for layout only. Keep `<meta name="referrer"
   content="no-referrer">`.

## Screens

Shared across all crew-app screens, **hooks that must stay**: `body.crew-page`, `.cr-bar`, `.cr-main#crMain`,
`.cr-card`, `.cr-note`, `#crNet`, `[role=tab][data-tab]`, `.cr-panel`, the `crew:tab` event, `window.RELAY_CREW.plan`,
manifest link `rel="manifest"`, page `<title>` = `app_name` (tests look for the default "Relay crew"). Everything the
server sends is still put on the page with `textContent`. Permissions: page `crew.use` (Member+); a signed-out visitor
gets the sign-in form, a role without `crew.use` gets "not allowed".

### s-crew-home: Today (`/crew`)
* **Files**: `app/modules/crew/templates/crew.html`, `static/crew.js`, `static/crew.css`, route `crew_page` in
  `routes.py`; data from `/crew/api/plan`, `/crew/api/today`, `/crew/api/checkin`, `/crew/api/push*`.
* **Who/where**: every crew member (volunteers), on their own phone, standing in the building; also laptops.
* **On it**: Today (only while capture is not running): position `#crPos` (teams joined by ", " else role else "No
  position today"), call time `#crCall` ("Call time 8:15 AM", " · team" when more than one team, "No call time
  today."). Plan (`show_plan`): `#crPlanNote` (title · time, "Loading…", "There is no plan for today.", "No plan is
  connected.", "The plan could not be loaded."), `#crItems` rows with `.cr-time`, `.cr-title`, state class `cr-done` /
  `cr-current`, songs prefixed "Song: ". Check in `#crIn`/`#crInNote`/`#crInBtn` (arrived text with time, "Tap to
  check in when you are at the church.", "Check-in happens by itself when your phone is on the church network.",
  "Finding you…", "Allow location for this site to check in, then try again.", "This browser cannot share a location.").
  Pages `#crAlerts`/`#crAlertsNote`/`#crAlertsBtn` (Turn on pages / Turn off pages, blocked text, "Relay could not
  save this phone.", "This browser could not turn on pages."). Tools list `.cr-tools` (title, optional note). Sign out
  form `POST /admin/logout`. Offline note.
* **Hooks**: all ids above, `.cr-off` on `#crMain` (booth off), `hidden` toggling on `#crToday`, `#crIn`, `#crAlerts`,
  `#crInBtn`; polling every 30 s (`loadPlan`, `loadToday`); `online`/`offline` listeners.
* **Mockup changes**: tabs become the bottom bar and Today a tab; the Today card is larger (26px position) and keeps
  the accent border; the current plan row gets a "Now" pill; header rows (kind `header`) show as small caps with no
  time; the offline note moves to the top. The Pages and Tools cards keep their order.
* **Implement**: the Today tab panel is the existing sections wrapped in a `tabpanel` with `data-tab="today"`; `crew.js`
  `select()` must treat it like any other tab. Move `#crNet` to directly under `.cr-bar` (keep the id) and show it
  from every tab. The "booth off lands on Checklists" rule in `loadToday` is replaced by "land on Today". Position and
  call time come from the same `/crew/api/today` answer, nothing new on the server. The mockup demo "Plan card
  (show_plan)" maps to the existing setting; "Other tools" maps to `registry.user_nav` (empty when the user only has
  Crew).

### s-crew-chat: Chat (`/crew#chat`)
* **Files**: `templates/chat.html`, `static/chat.js`; permission `crew.chat`; moderators (`crew.moderate`) see every
  role channel and may delete any message.
* **Who/where**: all crew, phone. Phone is in a pocket often: the stream closes when hidden and a quiet web push is sent
  instead (the `#chatPush` toggle).
* **On it**: channel select `#chatChannel` (Team, the person's role, every role for moderators), checkbox `#chatPush`
  "Quiet notification when I am not looking", status line `#chatNote` ("No messages yet.", "That did not work.",
  "This browser cannot show new messages as they arrive."), list `#chatList` (`li.chat-msg[.mine][data-id]` with
  `.chat-who` "Author · Role · 8:02 AM", `.chat-body`, `.chat-del` "Delete" for own or moderator, aria-label "Delete
  message from X"), form `#chatForm` with `#chatText` (maxlength 500, Enter sends, Shift+Enter newline) and `#chatSend`.
* **Hooks**: every id and class above, `data-id`, the EventSource `/crew/api/chat/stream` message types (`ready`,
  `message`, `delete`), `crew:tab` and `visibilitychange` waking logic.
* **Mockup changes**: bubbles (mine right, tinted) instead of ruled rows; the composer is pinned above the tab bar
  (sticky) and the list fills the space; 44px targets. Loading shows skeleton bubbles (new). Offline disables Send
  (new, see below).
* **Implement**: only CSS and the pinned composer (`position: sticky; bottom: 0` inside `.cr-main`, drop the
  `max-height: 55vh` list cap since the panel scrolls). New offline behaviour: `chat.js` currently lets a failed
  send throw; catch it and show `#chatNote` "You are offline. Your message was not sent." and keep the draft. The
  unread count on the Chat tab needs a tiny `unread` counter in `chat.js` while another tab is showing (events already
  arrive only when the stream is open; when closed use the push). Optional.

### s-crew-checklists: Checklists (`/crew#checklists`)
* **Files**: `templates/checklists.html`, `static/checklists.js`; `crew.checklist` (member) to tick,
  `crew.checklist_edit` (operator) or a named team lead to edit (leads see only the positions they lead).
* **On it**: `#clNote` ("No checklists for you at this service."), `#clLists` with one `section.cr-card` per list:
  `h2` "Title · Position", `ul.cl-items` of `label > input[type=checkbox] + text`, done items `li.cr-done`. Editor
  `#clEditBox` (hidden unless `d.editor`): template rows `.cl-tpl` "Title · Position or Everyone (n)" with Edit and
  Delete (confirm), form `#clTitle`, `#clPos` (+ `#clPositions` datalist, placeholder "A Planning Center team, or
  blank for everyone"), `#clItems` (one per line, 6 rows), `#clSave` "Save checklist", `#clCancel`.
* **Hooks**: all ids, `.cl-items`, `.cr-done`, `.cl-tpl`, `.cl-form`, `.cl-btns`, `PUT /crew/api/checklists/item/{id}`
  re-drawing the list from the response, 30 s refresh while visible.
* **Mockup changes**: 24px checkboxes with 48px rows, struck-through done text, progress bar and "3 of 6" in each
  card heading (new, derivable from `items`), editor Edit/Delete buttons at 44px. Checkboxes disable offline.
* **Implement**: `checklists.js` `draw()` adds the count and the bar; keep the optimistic revert on failure. Delete
  confirm stays the browser `confirm()` (a sheet is out of scope).

### s-crew-resources: Resources (`/crew#resources`)
* **Files**: `templates/resources.html`, `static/resources.js`; `crew.resources` (member) / `crew.resources_edit`
  (operator or lead). 
* **On it**: `#rsNote` ("No resources for you yet."), `#rsLists` grouped by position (`h2` "Everyone" or the team),
  items `.rs-item` with link, meta ("PDF · 214 KB", "link", "page"), embedded pages get "Show here" / "Hide" and a
  sandboxed `iframe.rs-frame`. Editor `#rsEditBox`: manage rows (Edit/Delete), `#rsKind` (File from this phone / Link /
  Embedded page), `#rsTitle`, `#rsPos` + `#rsPositions`, `#rsUrl` + `#rsUrlLbl` ("Web address", hidden for files),
  `#rsFile` (accept list), `#rsHint` (per kind, lists the allowed embed hosts), `#rsSave` "Add"/"Save", `#rsCancel`.
* **Hooks**: all ids, `.rs-item`, `.rs-frame`, `/crew/api/resources/{id}/file`, upload as raw body to
  `/crew/api/resources/file?title=&position=`, iframe `sandbox` and `referrerpolicy`, the http(s) check in `safe()`.
* **Mockup changes**: nothing structural; larger touch targets, the iframe area is a placeholder (nothing loads in a
  mockup). Editing disables Kind and hides the file input, as in the script.
* **Implement**: CSS only; the native file picker already offers the camera.

### s-crew-activity: Activity (`/crew#activity`)
* **Files**: `templates/activity.html`, `static/activity.js`; permission `crew.activity` (operator).
* **On it**: `#acNote` ("Nothing yet for that filter."), card "Crew activity" with `#acWho` (Everyone plus people),
  `#acKind` (Everything, Chat, Check-ins, Checklists, Resources, Paging, Call times), `#acDays` (Last day, Last week,
  All kept (90 days)); `#acList.rs-list` rows `.rs-item` "Name · summary" + `.cr-note` date; `#acMore` "Older".
  Actor falls back to token name then "Relay". Message bodies never appear.
* **Hooks**: all ids; keyset paging with `before=<last id>`.
* **Mockup changes**: none beyond the shell. Operators see this tab only.
* **Implement**: CSS only.

### s-crew-intercom: Intercom (`/crew#intercom`)
* **Files**: `app/modules/intercom/templates/crew.html` (tab fragment, carries its own `<style>`),
  `static/crew.js` (plus `livekit-client.umd.js`), routes `/intercom/ws`, `/intercom/api/phones`,
  `/intercom/api/my-phones`; permission `intercom.use` (Member+). Pure logic exports are tested in
  `tests/js/intercom_crew.test.mjs` through `module.exports`: `backoff activityText resultText applyTalkMessage holding
  busy wantedRooms talkTargets guessName isHeadset ordinal` must keep their names and behaviour.
* **Who/where**: crew on a phone with a headset, mid-service; also a laptop (space bar). iPhone: only while the tab is
  in front.
* **On it**: chips `#icProd` (production label), `#icLocal` ("Local: connecting", "Local: connected · 23 ms",
  "Local: offline"; classes `ok`/`bad`), `#icHeadset`; banners `#icDown` ("Reconnecting in 4 s", alert), `#icCut`
  ("Cut off on Cameras. Dana Whitfield has priority.", alert), `#icMic` (microphone blocked text, alert), `#icLatchWarn`
  ("Latch ends in 10 s on Production."), `#icRoute` (phone mic and speaker, use a headset); `#icNoChannels`; one
  `.ch` card per channel in `#icChannels` (left border = channel colour, `h3` name, `.act` activity text, buttons Talk /
  Listen with `aria-pressed`, `input[type=range]` "<name> listen volume", `.res` result: "Talking", "Waiting · you're
  2nd in line", "Cut off · X has priority", "Busy · someone with priority is talking", "Muted by an operator", ...);
  the foot: `#icTo` ("Talks to: A, B" / "Pick Talk on a channel to use the button."), big `#icTalk` ("Hold to talk",
  "Talking…", "Waiting", "Talking", "Talking (latched)"), `#icLatch`, `#icMuteAll` "Mute all listen", checkbox `#icKeep`
  "Keep this phone signed in to the intercom", `details#icMine` "My phones" (`#icMyList` with Sign out per phone,
  `#icMyEmpty`), `#icIos`, the laptop/headset hint, live region `#icLive`, and `#icUnsupported` ("This browser cannot do
  the intercom. Use a current Safari or Chrome over https.").
* **Hooks**: every id above; `#ic` gets class `offline` when the socket is down; `.ch`, `.chip`, `.banner`, `.tog`;
  pointer events on `#icTalk` (`pointerdown`/`up`/`cancel`/`leave`, `touch-action: none`, no context menu); the `crew:tab`
  event for wake lock; `data-channel` on the hidden `<audio>` elements; `localStorage` key `relay-intercom-credential`.
  `tests/test_crew.py` asserts `id="tab-intercom"` and `id="icTalk"` on `/crew`.
* **Mockup changes**: the talk controls (`#icTo`, `#icTalk`, `#icLatch`, `#icMuteAll`) stay **sticky above the tab
  bar**, 92px talk button; the rest of the old foot (keep signed in, My phones, notes) moves out of the sticky area
  into the page, so the sticky part is about 190px, not 330px. Channel cards, chips and banners are unchanged in
  wording. Talk/Listen are 44px. The press simulation (Demo chip, or hold the space bar) shows each result string.
* **Implement**: move the inline `<style>` out of `crew.html` into `static/crew.css` of the intercom module, linked by
  the crew page: a tab fragment cannot add a `<link>`, so list it in a new `CrewTab`/module `crew_styles` tuple
  rendered by `crew.html` (core registry change, small), or simply put it in `crew.css` of the crew module under `#ic`.
  Replace hard-coded `#fff` on the talking button with `var(--bg)`; channel colour remains data (inline
  `border-left-color`), validated by the existing hex regex. Keep `render()` in `crew.js` rebuilding only
  `#icChannels` and the chips so a press in progress does not lose pointer capture (the mockup does the same).
  Consider ordering Intercom first in the bar for roles that hold it (needs a `CrewTab` order field; not required).

### s-crew-join: Join (`/crew-join`, plain HTTP join port, public)
* **Files**: `templates/join.html` (inline CSS), route `join_page`, `/crew-join/relay.crt`. Reached by the QR code on the
  admin Crew page.
* **Who/where**: a new volunteer, first time, on their phone, on the church Wi-Fi.
* **On it**: h1 "Join {app_name}", explanation, step 1 "Let your phone trust Relay" with the Download button and the
  iPhone/iPad and Android instructions, or "Relay has no certificate yet. Ask whoever runs the booth to open Relay
  once, then scan again."; step 2 "Open the crew page" with the button and the warning paragraph, or "Relay cannot
  tell what its address is right now. Ask whoever runs the booth."
* **Hooks**: link hrefs `/crew-join/relay.crt` and `{{ crew_url }}`; the `have_cert` and `crew_url` template variables;
  `NO_STORE` headers. Tests exercise the route, not ids.
* **Mockup changes**: tokens, the Relay lockup at the top, buttons at 48px; copy is unchanged word for word. The demo
  chip shows both missing-certificate and unknown-address states.
* **Implement**: stays self-contained with an inline token block (see the stylesheet section, item 7). Optional new
  step 3 "Add to home screen" shown only once the page is opened over HTTPS (not mocked).

### s-crew-page: Page confirmation (`/crew/notification/{token}`)
* **Files**: `templates/notification.html`, routes `GET`/`POST /crew/notification/{token}`; reached from the web push notification
  (`url` in `sw.js` `notificationclick`), so the person is signed in on that phone.
* **On it**: header, card "Page" with the message (`.cr-msg`, 22px), form with `.cr-confirm` "I have read this" or
  `.cr-ok` "Confirmed. The buzzing has stopped." (`role=status`), invalid token "This page link is not valid any
  more.", link "Open the crew page".
* **Hooks**: `.cr-msg`, `.cr-confirm`, `.cr-ok`, the POST (plain form post, no script), `renotify` behaviour in `sw.js`.
* **Mockup changes**: bigger message (24px), full-width confirm button, token styling. Copy unchanged.
* **Implement**: CSS only; the page links `tokens.css` + `crew.css` like `crew.html`.

### s-crew-offline: Offline (`/crew/_/offline`, from the service worker cache)
* **Files**: `templates/offline.html` (inline CSS), `static/sw.js`.
* **On it**: "Can't reach Relay", "Your phone is not on the network Relay is on. Join the same Wi-Fi as the booth and
  try again.", button "Try again" (`location.reload()`).
* **Service worker behaviour (unchanged, documented here)**: navigations are always network-first and fall back to this
  page; only `/crew/_/offline`, `crew.css`, `crew.js` (and, in the redesign, the token files) are cached; `/crew/api/*`
  and any page with a person's data is never stored; the manifest is `display: standalone`, `start_url`/`scope`
  `/crew`, so the app installs to the home screen only over trusted HTTPS (hence the join page). There is **no custom
  install prompt**; the browser's own offer is all there is. A hint card ("Add to home screen") on Today would be a
  new feature (not mocked).
* **Mockup changes**: tokens only. **Implement**: inline token block; keep `theme-color` consistent.

### s-paging-room: Parent paging room (`/paging/{secret}`)
* **Files**: `app/modules/parent_paging/templates/room.html` (inline CSS and script), routes `GET /paging/{secret}`,
  `GET /paging/{secret}/state`, `POST /paging/{secret}/page`, `POST /paging/{secret}/arrived/{pid}`. **No sign-in: the
  secret in the link is the key**; served on the HTTPS operator port only (404 through the tunnel or the viewer
  port); `no-store`, `no-referrer`, `noindex`. Permissions apply only to the admin side (`parent_paging.*`).
* **Who/where**: a kids-ministry volunteer at the room's check-in desk, on a phone or a tablet fixed in the room.
* **On it**: room name `#room`, line "Tap a child to page their parent. The screen shows only the code on their
  label.", `#note` (warn, hidden when empty; server notes such as "Planning Center is not set up. Save its application
  ID and secret first." and "That did not work. Try again."), "Paged now" `#upBox`/`#pages` (rows `.page`: `.code`,
  `.state` "Waiting for the booth" / "On screen" / "Sent to ProPresenter", button "Parent arrived"), "Checked in" with
  search `#find` ("Find a name or code", shown from 12 children), `#kids` rows `.kid` (`.name` first name and initial,
  `.code`, then "Page parent", or the confirm pair "Cancel" / "Page K7M2", or "Paged"); empty texts "Loading…",
  "Nobody matches.", "Nobody is checked in to this room right now."
* **Hooks**: ids `room note upBox pages find kids`, classes `.kid .page .name .code .state(.on/.wait) .paged .empty .note
  .list .go`, the `confirmId` two-step confirmation, 10 s refresh paused during a confirm and while hidden, a 404
  on `GET` reloads the page (revoked link).
* **Mockup changes**: tokens and 44-60px rows; the room name is placed as the page title. Copy unchanged.
  The mockup adds nothing the server does not know.
* **Implement**: replace the private `:root` palette with the shared tokens (stylesheet section, item 8); the primary
  confirm button becomes `.kc-btn.p` equivalent (`.btn.p` from `components.css`). Only the code leaves the page, never
  the full name: keep it that way in any new markup.

## Things the shell lacked

* A phone/PWA frame: the mockup built its own (`.kc`, 430px column with a ring on wide windows). If more phone screens
  come, promote it to a shell helper such as `phoneFrame({bar, tabs, body})`.
* An icon for chat and checklists (drawn locally in `screens-crew.js`) and a "mark only" helper (read from `.mk`).
* A documented `--primary-fg`-style token for text on `--err` (used `var(--bg)`); the shell has none.
* A token for the intercom channel colour is not needed: channel colours are user data, rendered inline.
