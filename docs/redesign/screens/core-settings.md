# Core settings screens

Mockup file: `docs/mockups/admin-shell/pages/core-settings.js`. All nine are `kind:'page'` inside the shell. Real
source: the matching `<div class="page" data-page="...">` in `app/templates/admin.html` plus the script named under
each screen. The mockup states (the dashed "Mockup state" strip at the top of every page) exist only to show
states in one file; the real page has no such strip.

Rules that hold for all nine (the scripts and `tests/test_browser.py` depend on them):

- `data-page="<id>"` on the page container, `id="page-<id>"`, and `a[data-page]` on every nav link. test_browser.py
  clicks `nav a[data-page='<id>']` for every page the role may open and asserts the menu holds exactly the pages in its
  `NAV` map (schedules, blocklist, credentials, appearance, users, audit, modules, network; `account` is not in it and
  must stay out of the menu, reached only from the header link `#acctLink`).
- No script error, console error or failed request may occur while a page is opened (same test).
- Permission gating is server-side in the template (`{% if 'x' in perms %}`); the view-only note is the
  `viewonly('<manage perm>')` macro text `You can view this page but not change it.`
- Every script sets server text with `textContent` (never HTML). The restyle must keep that; the mockup builds HTML
  from sample data only.
- The scripts use `A.api`, `A.okMsg`, `A.alertMsg` (admin.js, the top status bar) and `window.confirm`. The mockup
  replaces `confirm` with a modal and puts messages inline plus a toast; the real implementation may keep `alertMsg`/
  `okMsg` as the toast layer and swap `window.confirm` for a shared confirm dialog, but then must keep the same
  wording and the cancel path.

## schedules (`#schedules`, Automation)

Template `page-schedules`; script: the schedules block in `app/static/admin.js` (about lines 536-860). Permission
`schedules.view` to see, `schedules.manage` (Admin) to change; the view-only note shows without manage.

On it: status note (`#schedStatus`); table Schedule / Next start / On / actions (`#schedList`); `Add schedule`
(`#schedAdd`); the `New schedule` form (`#schedForm`, hidden until Add or Edit); `How schedules work`.
Form fields: Name (optional), Repeats (Every week / One time only), Days chips, Starting on (optional) or Date,
Ending on (optional), Start time, Stop time (three selects each: hour, minute in 5s, AM/PM), Time zone (seven US
zones), the overnight note, `Save schedule`/`Save changes`/`Add schedule` and `Cancel`. Row text: `· live window`,
`· finished`, `off`, `No schedules yet.`; Delete asks `Delete the schedule "<name>"?`. Status note variants: none set,
`Inside "x".`, `Stopped by hand during "x" - it stays off until the next scheduled start.` (class `warn`),
`Next start: ...`, `Nothing scheduled in the days ahead.`, `Every schedule is turned off.`

Must stay: ids `schedStatus schedList schedAdd schedForm schedName schedRepeat schedDaysField schedDays
schedStartDateField schedStartDateLabel schedStartDate schedEndDateField schedEndDate schedStart schedStop
schedStartLabel schedStopLabel schedTz schedOvernight schedSave schedCancel`; `.datepick[data-for]` +
`.nativedate`; the day chips are `input[type=checkbox][value=0..6]` inside `#schedDays`; time pickers are three
`select`s labelled Hour / Minute / AM or PM built inside `#schedStart`/`#schedStop`; `.switch` per row with
`aria-label="Schedule on"`. Dates are typed and shown as MM/DD/YYYY and stored ISO; times are 12-hour in the UI,
24-hour on the server. The status stream (`schedStatusChanged`) re-fetches the list; the 'Master' pill text comes
from the same data.

Mockup changes: form is a card below the list with the same fields; the calendar button opens a small popover
month grid instead of the browser's date picker; row switch uses the shell `.sw`; Delete uses a confirm modal;
validation messages sit inline under the form. Real implementation: keep the native `<input type=date>` fallback or
replace it with the popover, but keep the typed MM/DD/YYYY field; the validation strings in the mockup are
suggestions (the real script throws from `readSchedForm`, see admin.js ~696).

## blocklist (`#blocklist`, Settings: Blocked words)

Template `page-blocklist`; script: blocklist block in `admin.js` (~250-290). Permission `blocklist.manage` (Admin).

On it: `Blocklist` (note with file name, textarea, `Save to file`, count text), `How matching works`.
Must stay: `blocklistPath blocklist blocklistCount saveBlocklist`. The textarea is never overwritten while focused
(`renderBlocklist` guard) and tracks edits made to the file. Count text: `none`, `N term(s) active`,
` (limit reached)`. Save messages: `Saved to file - live now.`, `Saved - N duplicate(s) removed. Live now.`, and the
persistent `Saved, but the list is capped at N terms: M terms were left out. Remove some to make room.`

Mockup adds: an "Edited, not saved yet" hint, whole-word examples under How matching works, live count while typing.
Real implementation: the capped message must stay on screen (the real code uses `alertMsg` for it so it does not
fade); the hint and examples are optional.

## credentials (`#credentials`, Settings)

Template `page-credentials`; script: `admin.js` (~290-345). Permission `credentials.manage` (Owner only).

On it: `Keys` (OpenAI API key, state `· set <hint>` green / `· not set` red, `Save credentials`, the config.json /
keychain note with `#keyStore`), `Backup passphrase` (explanation, Passphrase, `Set passphrase`, `Remove passphrase`,
restore note). Must stay: `apikey keyState saveCreds keyStore backupPass backupPassState saveBackupPass
clearBackupPass`; the key field stays `type=password autocomplete=off`, passphrase `autocomplete=new-password
minlength=10`. Messages: `Nothing to save.`, `Saved. Restart capture to use the new key.`, `The passphrase needs at
least 10 characters.`, `Passphrase set. The next backup opens with it too.`, `Passphrase removed. Backups open only
on this computer.` The key value is never rendered back, only the last characters in `keyState`.

Mockup changes: second column card "Who can change these" (mockup only; drop it if not wanted); Remove passphrase
asks for confirmation (not in the real page today). Could not show: the keychain name is whatever
`cfg.secret_store` reports.

## appearance (`#appearance`, Settings: Appearance & tuning)

Template `page-appearance`; script: `admin.js` (~322-335, the form is filled around line 2498 from `cfg`).
Permission `settings.manage` (Admin).

On it: `Viewer display` (Default viewer font size (px) `#font` 16-160 step 2; History lines kept per stream
`#history` 1-500), `Caption line breaks` (`#segIdle` 0.2-10 step 0.1; `#segMaxIdle` 1-60 step 0.5), `Save`
(`#saveAppearance`), the long explanatory note. Save message: `Saved. Viewers pick up the new font size on reload;
caption timing restarts live sessions.` Must stay: `font history segIdle segMaxIdle saveAppearance`, number
inputs with those min/max/step.

Mockup adds: a scaled caption preview card, a small timeline strip that reflects the two timing values, inline
range validation, and a confirm modal when a live session is running and the timing changed. None of these exist
today; the confirm needs the live state from the status stream. The preview is illustrative (scaled by 0.34).

## modules (`#modules`, Settings)

Template `page-modules`; script `app/static/modules.js` (126 lines). Permission `modules.view` (Viewer);
`modules.manage` (Admin) for the switches and Test connection.

On it: `Installed modules` list (`#moduleList`), footer note. Each card (`.pcard.mcard`): name, `vX.Y.Z`, switch
(disabled when view-only), summary, health line (`.mhealth ok|warn|error|idle`; `Off` when disabled), `Settings`
link (the module's last page id, only when on), `Setup guide` link, `Test connection` + result span (only on, with
`has_test`, with manage), and the `Works with ... / Does more with ... on too (... is off).` note. Empty:
`No modules are installed.`; error: `Could not load the modules: <message>`; loading: `Loading...`. Health words:
Working / Needs attention / Not working / Idle.

Must stay: `moduleList`; the switch is a real `input[type=checkbox]` with `aria-label=<module name>` inside
`label.switch`; switching posts, sets `location.hash` to the module's page (or `modules`) and `location.reload()`
(a module's pages and scripts are rendered by the server only while it is on); a failed switch reverts and shows an
error span in the footer. The list re-loads on `hashchange` to `#modules`. Module pages' own footer
(`p.note.modfoot`: `<module> module vX · Setup guide · Switch it off on the Modules page`) links back here.

Mockup changes: two-column card grid, an "N of M are on" count, Off modules visually muted, a confirm modal when
switching a module off. Real implementation: the confirm is new; the reload behaviour and the server-rendered page
set remain. The mockup cannot show the reload or the nav changing.

## network (`#network`, Settings)

Template `page-network`; script `app/static/network.js` (235 lines); API `/api/admin/network*`. Permission
`network.view` (Viewer), `network.manage` (Admin) to change.

On it: warnings box (`#netWarnings`, class `netwarn`, shown only when `warnings`/`notices` exist), `Ports` table
(Port, Address, Link, Speed, Traffic on it, Forget), `Traffic` table (Traffic, Network port, If the port is missing,
Status, Test), the apply-at-once / 127.0.0.1 note, error line (`#netError`). Port row: friendly name + small line
`name · kind · default route · VPN/virtual`, address mono (`No address`, `self-assigned (169.254)`), link
Up/Down/Unknown/Not connected, speed, classes. Status chips: OK / Default / Port down / Port missing / Using default /
Address changed. Policies: `Do not run it` / `Use the default network` (`(default)` marks the default). Test result
lines stay under the status and survive redraws.

Must stay: `netWarnings netPorts netClasses netError`; `.nettable`, `.netchip.<status>`, `.nettest`, `tr.gone`;
selects carry `aria-label="Network port for <label>"` and `"If the port for <label> is missing"`; the page re-reads
every 5 seconds only while `#network` is open and the tab is visible, and skips the redraw while a select has focus
(do not redraw under an open dropdown); a failed change reverts the select and shows the server's message. A change
that would take the operator panel off the address in use is refused server-side (409) and shown in `#netError`.

Mockup changes: both tables in cards with the shell table look; status as a chip; states for all-good,
needs-attention (missing/down/fallback), view-only, loading, no ports. The refusal text in the mockup is a
suggestion. The mockup does not poll.

## users (`#users`, Settings)

Template `page-users`; script `app/static/users.js` (728 lines, also drives My account). Permissions: `users.view`
(Admin), `users.manage` (Admin) for every button, `tokens.manage` (Owner) for the all-tokens section.

Important: the real page has **no per-person permission editing**. A person has one of five fixed roles (Member <
Viewer < Operator < Admin < Owner); each permission has a `min_role` (`app/users.py`). The "What each role can do"
matrix is read-only and built from `/api/admin/users` (`roles`, `permissions`). The mockup's role editor therefore
shows a read-only summary ("Adds N: ...") of what changing the role gives or removes; the real page does not have
that line.

On it, in order: title with ` - N person/people` (`#usersSum`); `People` table (Name with `(you)`, Username mono,
Role `.rolepill`, Status Active / Must change password / Turned off, PIN Set or dash, Last sign-in or `never`,
Edit); inline `#userPanel` (Add a user / Edit <name> / Reset password for <name>); `#userSecret` (one-time
temporary password with Copy and Done); `Add user` (`#userAdd`); note `A new user signs in with the temporary
password...`; `Trusted devices` (note, table Device / Added by / Added / Last used / Remove, `.thisdev` tag
`This browser`, `#devicePanel` Trust this browser, `#deviceAdd` hidden once this browser is trusted); `API tokens`
(Owner: Token / Person / Starts with `relay_<hint>...` / Expires / Last used / Revoke); `What each role can do`
matrix (`.rolegrid`, groups as `tr.grp`, cells `.yes`/`.no` with a visible tick or dash and an `.sr` Yes/No).

Edit panel: Name, Role (only roles up to your own rank; disabled with `You cannot change your own role.` or
`This is the only Owner. Make someone else an Owner first.`), Save/Cancel, then Turn off/Turn on (not self, not last
owner), Reset password, Sign out everywhere, Delete (confirm: `Delete <name> (<username>)? They are signed out at once
and the account cannot be brought back.`; not self, not last owner). Add panel: Username (note `3 to 32 letters,
numbers, dots, dashes or underscores. They type this to sign in.`), Name, Role (default Viewer), Temporary password
with Generate (16 characters, no look-alikes, groups of four, from `crypto.getRandomValues`). Reset panel note: `This
signs them out everywhere and clears their PIN. They choose a new password the next time they sign in.` A generated
password is shown once in `#userSecret` (`Give <name> this temporary password. It is shown once; they must change it
when they sign in.`); a typed one is not shown again. Device remove confirm: `Remove <name>? A PIN will stop working
there.` Token revoke confirm: `Revoke <person>'s token "<name>"? Anything using it stops working at once.`

A restyle of users.js must preserve:
- ids `usersSum userList userSecret userPanel userAdd deviceList devicePanel deviceAdd allTokenList roleRef`, and the
  generated field ids `newUserName newUserFull newUserRole newUserPass editUserName editUserRole resetUserPass
  trustName`; `page-users` presence is how `initUsers` decides to run.
- `rankOf`/`myRank` rules: no Edit button on an account above your own rank, role list capped at your rank, own role
  and last-owner role locked, no Turn off/Delete for self or the last active Owner. The server refuses these too;
  the UI must not offer them.
- Secrets: passwords and PINs are read from the form, sent once, cleared; never put in an attribute, log or URL.
  `type=password`, `autocomplete=new-password`. The mockup writes the sample password into `data-t` for the Copy
  button; the real code must keep it in a closure (`secretText`), not in an attribute.
- Panels close and the secret is cleared on `hashchange` away from `#users`. Opening Add closes the device panel and
  the reverse. `load()` re-reads users, devices and (with `tokens.manage`) tokens together after every change, and
  also refreshes My account when the changed token or the own name belongs to the signed-in person
  (`#acctLink .acctname`, `paintAccount`).
- `showOnce`'s clipboard fallback (select the text, show `Press Ctrl/Cmd+C`) for plain-HTTP LAN addresses where
  `navigator.clipboard` is missing.
- The shared helpers `el`, `A.api`, `A.okMsg`, `A.alertMsg`, `window.relayCan`, `window.RELAY_ME`.
- Status class names `stat-off`, `stat-warn`, row action class `userrow-acts`, `.rolepill`, `.thisdev`, `.shown`,
  `.userform`, `.passbox`, `.tablescroll`, or equivalent CSS for them.

Mockup changes: avatars and chip statuses in the People table, a People filter (new; the real page has none and lists
everyone), buttons for Edit only (matches real), role "adds/removes" summary, the role matrix highlights the viewer's
own column. Mockup states: Owner, Admin (no API tokens section, no Edit on the Owner row), View-only.

## audit (`#audit`, Settings: Audit log)

Template `page-audit`; script `app/static/audit.js` (124 lines); API `/api/admin/audit`. Permission `audit.view`
(Admin). Read-only: nothing here changes the log; API tokens cannot read it.

On it: sub line with `#auditKeep` (90 days); filters Person (`#auditWho`: Everyone, each person `Name (username)`,
`Relay itself, or nobody signed in`), Kind (`#auditKind`: Everything + kinds named Sign-ins, First-run setup, Own
account, Users, Trusted devices, API tokens, Settings, Captions, Alerts, Panels, Schedules, Overlays, Recordings,
Modules, Other changes; a module's own kind is its id as words), When (`#auditDays`: Last 24 hours / Last 7 days /
Last 30 days / All kept), Search (`#auditQ`, 300 ms debounce); table When / Who / What / From (`#auditList`,
`table.audit`); `Show older` (`#auditMore`, row `#auditMoreRow`, keyset by oldest id). Who: name + username small +
`API token: <name>`, or `Nobody signed in` for auth/setup, else `Relay`. What: summary + target + `Device: ...` +
`With ...`. Failed rows (`auth.failed`, `auth.locked`, `setup.failed`) get class `failed`. Empty: `Nothing matches.`
Must stay: all those ids, `tr.failed`, labels on the filters (class `sr`), loading on `hashchange` to `#audit`, the
filter lists refilled from each first-page response, and that a failed sign-in never records what was typed
(only a known account's name).

Mockup changes: filters in one card, failed rows tinted with a left bar, dates in the browser's locale with seconds
(as the real page). Show older pages by 10 locally. The mockup cannot show real filter choices from the server.

## account (`#account`, Settings, hidden from the sidebar)

Template `page-account`; script `app/static/users.js` (`initAccount`, `initTokens`). Reached only from the header
link `#acctLink` (`My account`, shows name and role); no permission beyond being signed in. API tokens need
`tokens.own` (Admin and Owner). `reg()` carries `hidden:true`; the shell must route `#account` without a menu entry.
The mockup's shell menu still links Account to `#users` (acctItems in index.html); that should point to `#account`.

On it: sub line `#acctWho` = `Name - username - Role`; `Password` (Current / New / New again, `Change password`,
`At least 8 characters. Changing it signs you out of other browsers.`; mismatch `The new passwords do not
match.`); `PIN` (note `A PIN is 4 to 8 digits...` + `#pinState` `You have a PIN set.` / `You have no PIN set.`, Your
password, PIN, `Set PIN` or `Change PIN`, `Remove PIN` when set; `A PIN is 4 to 8 digits.`); `API tokens` (tokens.own:
note about Bearer use, table Name / Starts with / Created / Expires / Last used / Revoke, `#tokenSecret` one-time
secret, `#tokenPanel` New API token: What it is for (placeholder `Stream Deck in the booth`), Expires after (30 days,
90 days default, 365 days, Never), `Make token`; `New token` button; empty `No tokens yet.`); `This browser`
(`#deviceState`: trusted with device name, or not trusted with `You can trust it on the Users page.` / `An
administrator can trust it for you.`).

Must stay: ids `acctWho pwForm pwCurrent pwNew pwConfirm pinState pinForm pinPassword pinNew pinSave pinRemove
tokenList tokenSecret tokenPanel tokenAdd tokenName tokenDays deviceState`; `pinNew` is
`type=password inputmode=numeric pattern=[0-9]* minlength=4 maxlength=8`; the `#acctLink .acctname` element is
updated when the person edits their own name on the Users page. Password and PIN fields are cleared after every send.
The one-time token secret (`Copy this token now and keep it somewhere safe. It is shown once; if it is lost, make a new
one.`) must not be stored or re-shown.

Mockup adds: a `Preferences` card with `Open layout preferences` that opens the shell's layout popover (theme,
preset, layout). The real account page has no preferences section today; the shell's preferences are per-browser
(localStorage) and not part of the account. Remove PIN gets a confirm in the mockup (not in the real page). States:
Owner (PIN set, trusted browser, tokens) and Operator (no PIN, untrusted browser, no tokens section).
