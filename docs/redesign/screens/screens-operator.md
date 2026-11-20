# Operator and user screens (standalone, no sidebar)

Mockup: `docs/mockups/admin-shell/pages/screens-operator.js`. Every screen below has a floating "Demo state" bar in
the mockup; that bar is mockup-only and is not part of the product.

## Which stylesheet each screen should use

| Screen | Stylesheet | Why |
|---|---|---|
| Sign in, setup, change password, database down, home | **`admin.css` tokens** (new, shared with the operator shell) | Same people, same login, same four presets and light/dark. These pages are served by `app/templates/_auth_head.html`, which today pulls `viewer.css` and a hand-written `<style>`; replace both with `admin.css` and the `.split` / `.cent` / `.fbox` auth classes. |
| Landing (`/`) | Keep `viewer.css` | It is the public front door that people reach before any account exists, next to the viewer pages, and follows the viewer's `relay.theme` choice, not the operator's preset. Only restyle its `.choice` cards to match. |
| Alerts, Status, Tally | Keep `viewer.css` (its `--bg --fg --dim --line --accent --warn --err` tokens) but map them onto the `admin.css` token names | They are phone, tablet and wall screens that share viewer.css's `.dot`, `.toast` and theme switch. Adopting admin tokens would break the `/screen`-family look; instead alias the names once. |
| Flash a panel | **`admin.css` tokens** | An operator tool opened from Panels, desktop only, uses the same cards, buttons and notices as the shell. |
| Rundown (`/screen?rundown=`) | Keep its own `rundown.css` | A fixed 1920x1080 kiosk scaled by `rundown.js`; layout is in pixels and must not follow shell spacing. Only its colours should come from tokens. |

New tokens the mockup needed and the shell lacks: an **info** colour (steady blue on alerts and stage messages; the
mockup falls back to `--primary`), and a **faint** text colour (`color-mix(muted, bg)`; derived inline in the rundown).
Text on status fills uses `var(--bg)` so it stays readable in both themes.

## Sign-in family

### s-login / s-login-centered: operator sign in, PIN, first-run setup
- Route: `GET /admin` (`_login_page`), posts `/admin/login`, `/admin/login/pin`, `/admin/setup`, `/admin/import`.
- Files: `app/templates/login.html`, `_auth_head.html`, `_brand_head.html`; no script.
- Users: anyone opening the operator panel, on any device. First-run setup is one person, once.
- Three modes from one template (`mode` = `setup` | `pin` | `signin`):
  - **Password:** username, password, Sign in, "Use a PIN instead" (only when a trusted device has PIN users). Errors: "Wrong username or password.", "This account is turned off. Ask an admin to turn it back on.", "Too many attempts. Try again in N min." A username is echoed back after an error; a password never is.
  - **PIN:** "This browser is trusted (device name). Pick your name and type your PIN."; Name select, PIN (numeric, max 8), "Use my password instead" (`/admin?pw=1`). Errors: "That PIN is not right.", "Too many wrong PINs. Sign in with your password and set a new PIN."
  - **Setup:** Admin token (when `token_set`) or Setup code (when `code_set`, with the installer / `journalctl -u relay` hint) or neither (only reachable from this computer); OpenAI API key (optional); Username; Your name; Password; Confirm password; "Create the Owner". Remote visitor: only the sentence "Relay has not been set up yet. Open this page in a browser on the computer Relay runs on…" and no fields. A second form, "Coming from Relay 2.0?" (`folder`, "Import from a previous Relay"), shows unless `remote` or `code_set`. After an import a `role="status"` notice lists what was imported and the certificate fingerprint. Errors: bad token, bad code, "The two passwords do not match.", "Relay is already set up. Sign in instead.", "Finish setting up Relay on the computer it runs on: open this page there.", "The old folder could not be read."
- Must stay: field `id`/`name` pairs (`username`, `password`, `user_id`, `pin`, `token`, `api_key`, `name`, `confirm`, `folder`), form `action`s and `method="post"`, `role="alert"` on errors, `role="status"` on the notice, `autofocus` rules (password when a username is echoed, else username), `autocomplete` values, `minlength="8"`, PIN `inputmode`/`pattern`/`maxlength`, the `?pw=1` link, and the `<title>` ("Set up Relay" / "Operator sign in"). `tests/test_auth.py` and the setup tests post these names.
- Mockup changes: the split layout adds a brand panel (the headline text is marketing copy supplied by `authLayout`, not in today's page) and a language chip bottom right that the real page does not have (drop it or keep it only if sign-in is ever translated); the import form moves into the same card (split) or a second card (centered). Server-rendered, no JS needed; keep it that way so sign-in works on a locked-down kiosk.
- Real implementation: pick one layout (split is the default recommendation; centered for the compact/embedded case) and render both modes from the one Jinja template with the same `mode` context. Keep `Cache-Control: no-store`.

### s-password-change: forced or voluntary password change
- Route: `GET/POST /admin/password`; voluntary form is `GET /home?password=1` (`_password_page(voluntary=True)`).
- File: `app/templates/password_change.html`; no script.
- On it: Current password, New password, Confirm new password, Change password; below it "Sign out" (forced, posts `/admin/logout`) or "Back" (voluntary, to `/home`). Forced lead: "You are signed in with a temporary password. Choose your own to continue." Voluntary: "Choose a new password. You stay signed in here; your other sign-ins end." and a hidden `voluntary=1`.
- Errors: "The password is wrong.", "The two passwords do not match.", "Choose a different password from the current one.", "A password is 8 to 200 characters.", "Too many attempts. Try again in N min."
- Must stay: ids `current`, `new`, `confirm`; names `current`, `new`, `confirm`, `voluntary`; `role="alert"`; the logout form with `button type=submit` (the real Sign out is a POST, not a link: the mockup's link look must still submit a form).

### s-db-down: operator panel unavailable (and the 403 page)
- Route: every non-`/api/` request while the user database is down (`db.Unavailable` handler, HTTP 503, `no-store`); `/api/` gets JSON instead. The 403 state is `_denied_page()`.
- File: `app/templates/db_down.html`; no script.
- On it: title, `role="alert"` message ("The user database is not available. Relay is still captioning; try again in a moment."), "Try again" (a plain link to `/admin`).
- Mockup changes: Try again becomes a button (still a link to `/admin`); an added line says captions keep running. The 403 "Not allowed." page is today a bare unstyled `HTMLResponse`; the mockup shows it as a proper card with a Go to Home action, which needs a small template (`not_allowed.html`) and the sentence "Your account does not have the role this page needs. Ask an admin to change it." (new text).

### s-landing: public landing
- Route: `GET /` (`index.html`); `show_admin` is true only on the admin port.
- File: `app/templates/index.html` with `viewer.css`; the inline script only applies the saved `relay.theme`.
- On it: lockup, "Live Captions", "Pick a view. Nothing to install — the page updates itself.", cards Translation, Transcription ("Live {source_label} of what is being said"), Both ("{source_label} and translation, side by side"), and "Operator panel / Token required" only when `show_admin`.
- Must stay: `.index`, `.choices`, `.choice` classes and the three hrefs (`/translation`, `/transcription`, `/both`), `/admin` link conditional on `show_admin`.
- Mockup changes: none structural; cards pick up the token card style. Keep `viewer.css`.

## Operator tools

### s-home
- Route: `GET /home` (also where a login lands for a role without `admin.open`).
- File: `app/templates/home.html`; no script.
- On it: lockup, "Hello, {user.name}", "What you can use in Relay." and a list of tools (core: Alerts when `alerts.send`; then every module's `user_nav`: title plus note), plus "Operator panel / Control Relay" when `admin.open`. Empty state: "Your account has nothing to use in Relay yet. Ask an admin to give it a role that can." Below the card: "Change my password" (`/home?password=1`) and "Sign out" (POST `/admin/logout`).
- Must stay: `ul.tools` links `href` values, the logout form. The mockup adds an icon and arrow per row; `user_nav` entries need an optional `icon` key (fall back to none).

### s-alert: alert sender (phone / tablet) and receiving screen
- Routes: `GET /alert` (needs `alerts.send` when sign-in is required, then `data-account` names the sender and there is no name form); the receiving side is `/screen?alert=<name>`.
- Files: `app/templates/alert.html`, `app/static/alert.js`, `alert-icons.js`, `alert-screen.js` (receiving side), classes in `viewer.css`. Streams: `/stream?stream=alert`, `/api/alerts/messages/stream`; posts `/api/alerts/<id>/send`, `/api/alerts/messages/send|clear`, `/api/alerts/ack`.
- Users: a drummer, camera operator or deacon on a phone, mid-service; one thumb. Cards stay 64 px or taller.
- On it: "Need someone?"; "as {name} · home" (account) or "as {name} · change" with a "Who are you?" name form (placeholder `Drums`, Save, "Shown on the screen you flash, so they know who needs them."); connection dot (ok / err); Level select (Each button's own / Info (blue) / Attention (amber, flashes) / Urgent (red, flashes)); one card per alert with icon and status; empty text "No alert buttons yet. The operator sets them up in Relay admin, under Alerts."; "Tap a card to flash its screens. Tap it again to stop."; "Message the stage" with preset buttons (kind tag "Banner · attention"), a free message (max 80, Banner checkbox, Send), and "Showing: … Clear" rows. Toast text: "Say who you are first".
- Card states, exact text: `Flashing · from {who} · m:ss left · tap to stop` (the "from" is left out on your own alert), `Seen on {screen} · “{reply}” · {age}`, `Not seen · {age}`, `Stopped · {age}`; ages "just now", "N s ago", "N min ago".
- Must stay: ids `buttons`, `empty`, `statusDot`, `nameForm`, `nameInput`, `sendingAs`, `senderName`, `changeName`, `levelPick`, `msgSection`, `msgPresets`, `msgForm`, `msgText`, `msgBanner`, `msgLive`, `toast`; classes `alert-btn(.flashing, .was-seen|missed|cancelled)`, `alert-btn-icon|body|name|state`, `msg-btn.lvl-*(.showing)`, `msg-live`; `data-id` on cards, `aria-pressed`, `aria-live="polite"` on `#buttons`, `body[data-account]`, localStorage key `relay.alert.from`. Receiving: `#alertFlash` (`data-level`), `#alertFrom`, `#alertTo`, `#alertReplies`, `#stageBanner`, `#stageMessage`, `#stageFrom`, `#stageText`, `body[data-alert]`.
- Flash timing stays at once a second (under the 3 Hz seizure limit) and the flash is **not** stopped by reduced motion; the mockup keeps both.
- Mockup changes: cards use tokens (flashing = `--err` and `--bg` alternating); level colours come from `--err`, `--warn`, and info. The receiving-screen tab is added so the flash, reply buttons, stage message card and banner are designed in the same file; the real ones are owned by the `/screen` display page and keep their hard-coded flash colours until a status-colour token set exists.

### s-flash: panel firmware flasher
- Route: `GET /admin/flash?board=<id>` (`panels.manage`); posts `/api/admin/panels/flash?board=` (upload `POST`, remove `DELETE`); reads `/api/admin/panels/flash/<board>.json|.bin`.
- Files: `app/templates/flash.html`, `app/static/flasher.js`, `flash_steps.js`, `vendor/esptool-js/`.
- Users: the installer or an admin at a desktop with Chrome or Edge and a USB cable.
- On it: header (lockup, "Back to Panels"), "Flash a panel", board chooser (only when more than one board; each with "Firmware {version}" or "No USB image"); 1 The firmware (current image sentence, the `relay-display.bin` vs `relay-display-app.bin` note, file input, Upload/Replace, Remove, upload error); 2 Connect the panel (three steps, BOOT/RESET) shown only when an image exists; 3 Write firmware {version}: unsupported-browser notice, "Choose the panel's port and flash", the four-step list (Fetch and check the image, Connect to the panel, Read the firmware it runs, Write {percent}), progress bar, new-board confirmation ("Write it" / "Cancel"), and the eleven result sentences (done, cancelled, image-missing, manifest-wrong, image-size, image-sha, image-project, connect-failed, wrong-chip, wrong-size, read-failed, write-failed).
- Must stay (flasher.js and tests select on them): `[data-flasher]` with `data-board|project|chip|flash-mb`, `[data-flash-start]`, `[data-flash-steps]`, `[data-step=image|connect|check|write]` and the `.on` + `aria-current="step"` toggle, `[data-slot=percent|version|chip]`, `[data-flash-progress]`, `[data-flash-confirm]`, `[data-flash-yes]`, `[data-flash-no]`, `[data-result=<code>]` with `tabindex="-1"` and `hidden`, `[data-flash-unsupported]`, `[data-flash-upload]`, `[data-upload-file]`, `[data-upload]`, `[data-upload-remove]`, `[data-upload-error]`, `role="status"` wrapper, `type="module"` script. Every sentence stays in the server HTML; JS only toggles `hidden`.
- Mockup changes: the step list gains a "done" tick (a `.done` class that `io.step` would also set for earlier steps); the layout uses cards. It never erases the board, and the page keeps saying so through "Nothing was written."
- Admin-only; not a phone page.

### s-rundown: service rundown kiosk
- Route: `GET /screen?rundown=full|side|micboard&layout=fit|compact&cards=person|position&team=&time=HHMM&type=` (`_rundown_page`; off until switched on, refused remotely).
- Files: `app/templates/rundown.html`, `app/static/rundown.js`, `rundown.css`; stream `/stream?stream=rundown` (Planning Center module). Older mockups: `docs/mockups/rundown/`.
- Users: the production team, on a wall or stage monitor (1920x1080). Nobody presses anything.
- On it: top bar (LIVE tag, current item, key, "led by", Left countdown, Next, Planned end, Projected, Running over/under, service clock). **Full:** Order of service (Pre-service / Service / Post-service, planned vs actual / projected, lengths, song key, leader, mics), On the platform (mic cards for mics in use, spare cards, label cards), Notes (shrinks to fit), Scheduled roster (team groups; person or position cards; pending / declined / unfilled; mic numbers). **Side:** the captions frame plus a Now box and a rail of upcoming items. **Micboard:** every mic in 1, 2 or 3 rows (8 / 19 / 20+ mics). States: "No plan today for the service type this screen follows." and "The rundown is switched off. Turn it on in Planning Center › Rundown on /screen."
- Must stay: body `data-view|layout|cards|team|time|type` and `rd-*` classes, `#stage`, `#bar`, `#hLive|hTitle|hKey|hLead|hLeft|hLeftBox|hNext|hPlanned|hProj|hOver|hSvc|hClock`, `#msg`, `#body` / `#board`, `#rows`, `#mics`, `#micsFor`, `#notes`, `#notesH`, `#notesAt`, `#roster`, `#teamNote`, `#sTitle|sLeft|sMeta|rail`, the 1920x1080 scale transform, `.rows` flex-share sizing. The mockup renames the row class (`.r` to `.rr`) only to avoid shell collisions; the real `rundown.css` keeps its names.
- Mockup changes: colours derive from tokens so it follows light/dark; faces use a hash hue instead of the fixed 12-colour list. No behavioural change; text shrink-to-fit is not reproduced (the sample data fits). Below about 900 px wide the board is just scaled down; there is no phone layout and none is planned.

### s-status: status screen
- Route: `GET /status`; stream `/stream?stream=status`.
- Files: `app/templates/status.html`, `app/static/status.js`, viewer.css `.st-*`.
- Users: the sound desk or booth, on a spare monitor or tablet. Read only.
- On it: four cards: Livestream (On air in red with elapsed time and one row per source "live H:MM:SS · healthy | needs a look | trouble · N watching"; "Off air"; "Unknown"; "Not watched / No livestream module is on"), Captions (Running · elapsed · languages, or "no language live"; Stopped), Input (OK, Silent, Clipping, Lost, Off, dB, meter), Watching (viewer count "on Relay's caption pages"); a footer dot "Live from Relay" / "Not hearing from Relay" (stale after 20 s).
- Must stay: ids `airCard`, `airText`, `airSub`, `streams`, `capCard`, `capText`, `capSub`, `inCard`, `inText`, `inSub`, `inFill`, `viewCard`, `viewText`, `viewSub`, `connDot`, `connText`; card classes `st-card`, `st-air`, tone classes `st-ok|warn|err|live`, `st-stream st-h-*`, `.st-meter-fill` width set inline. The page recolours cards by class on every message.
- Mockup changes: none structural. The red "On air" card stays; state is never carried by colour alone (the word is always there).

### s-tally: camera tally
- Route: `GET /tally?camera=<n>` (camera phone), `?view=operator&camera=<n>`, `?view=board`; stream `/stream?stream=tally`.
- Files: `app/templates/tally.html`, `app/static/tally.js`, viewer.css `.ty-*`.
- Users: camera operators on a phone or tablet clipped to the camera; the director on a board.
- On it: **Camera:** a huge number, name, state word (LIVE red / NEXT green / blank) and a grey screen when the connection is silent (15 s). Sentences: "Connecting…", "Connection lost", "Add ?camera=1 to the address", "No switcher connected", "No camera {n}". **Operator:** the same plus a USB camera preview and picker ("Choose USB camera…", "No USB camera"), and a Live / Next bar. **Board:** one tile per camera, a "Show USB cameras" button, and a footer (Live / Connection lost / Connecting…).
- Must stay: body classes `tally-page tally-{view}` and `data-view`, `data-camera`; ids `cam`, `camNum`, `camName`, `camState`, `op`, `opMain`, `opNum`, `opName`, `opState`, `opVideo`, `opPick`, `opSel`, `opLive`, `opNext`, `board`, `connDot`, `connText`, `usbAll`; tone classes `ty-live|next|lost`; localStorage key `relay.tally.usb.<n>`. The USB picker only appears on https or localhost.
- Mockup changes: red and green come from `--err` and `--ok` instead of fixed `#d4161c`/`#129a3c`. The state word is always printed so it reads without colour. Test the two tokens still read as clearly different on the Brutalist and Soft Pop presets before adopting this; if not, keep fixed tally colours as an exception.

## Routes checked and not covered here
- `/transcription`, `/translation`, `/both`, `/present`, `/screen`, `/overlay`, `/overlay/{id}` (viewer family, another owner). `/admin` (the shell). Module pages (another owner). The raw `HTMLResponse` "Not allowed." 403 is covered under s-db-down.
