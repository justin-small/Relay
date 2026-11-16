# Operator panel redesign: the shell and every screen

Everything needed to apply the new operator-panel look to Relay later, without having to look at the
reference project or this conversation again.

- **The mockup (the target):** [`docs/mockups/admin-shell/index.html`](../mockups/admin-shell/index.html) is the
  shell (tokens, sidebar, header, preferences, menus, palette, components); every one of Relay's screens is a
  page file in [`docs/mockups/admin-shell/pages/`](../mockups/admin-shell/pages/) (see
  [Every screen](#every-screen-67)). No build and no network. Serve the folder and open it
  (`python3 -m http.server --directory docs/mockups/admin-shell`), or open `index.html` directly in a normal
  browser; embedded previews that block local scripts cannot load the page files. `#screens` lists every
  standalone screen. The CSS in `index.html` and its script are meant to be lifted, not re-drawn; how the page
  files work is in [`pages/README.md`](../mockups/admin-shell/pages/README.md).
- **The reference it came from:** [`arhamkhnz/next-shadcn-admin-dashboard`](https://github.com/arhamkhnz/next-shadcn-admin-dashboard)
  (a Next.js 16 + shadcn/ui admin template, MIT). It was read locally; nothing from it is vendored. What was
  taken is a design method, listed in [What was taken from the reference](#what-was-taken-from-the-reference).

## Decision: keep the stack, take the method

Relay stays FastAPI + Jinja + plain JavaScript. Nothing moves to Next.js, React, Tailwind or shadcn.

Why: the admin panel is about 3,800 lines of server-rendered templates and vanilla JS that the module system
(`app/modules/`, `admin_nav`) assembles per user and per switched-on module; the desktop app (Tauri) and the
Raspberry Pi packaging ship it as static files with no Node. A React rewrite would break all three and gain
nothing the CSS below doesn't. The reference's look comes from a small set of ideas (tokens, an inset shell,
state attributes, a few menu patterns) that are CSS and a few hundred lines of JS.

## What changes for the operator

- A new shell: collapsible sidebar (inset, flat or floating), a header with breadcrumb, search (⌘K), stream
  status and an account menu, and a content area that is centred or full width.
- Pages move from flat hairline sections to cards, with consistent buttons, inputs, tabs, switches, badges and
  tables.
- A **Preferences** popover (gear in the header): theme preset, font, light / dark / system, page layout,
  navbar behaviour, sidebar style and collapse mode, Restore Defaults. Saved per person.
- Four presets: **Relay** (the brand green, default), Soft Pop, Tangerine, Brutalist, each light and dark.
- Menus, a command palette, tooltips on the collapsed rail, toasts and small motion (all respect reduced
  motion).
- The sign-in pages (`login.html`, `password_change.html`) get the split and centred layouts.

## Constraints that must hold (read before starting)

These come from `AGENTS.md`, `docs/modules.md` and the tests. They are why parts of the mockup must **not** be
copied literally.

1. **Integrations stay modules.** The mockup's "Integrations" parent item (ATEM, ProPresenter, YouTube,
   Planning Center, with "new / updated / soon" badges) is a *demonstration of the badge and sub-menu pattern
   only*. In Relay a module's pages are listed by `module_registry.admin_nav()` under the existing groups
   (`Live`, `Output`, `Automation`, `Settings`) and rendered by `admin.html`'s `module_links()` macro. Keep
   that. Do not hard-code any module name in core files. If sub-menus are wanted, add an optional
   `parent`/`badge` to `AdminPage` in `app/modules/__init__.py` and let modules opt in.
2. **Navigation is built from permissions.** Every nav link in `admin.html` is wrapped in a
   `'<perm>' in perms` test. The redesigned markup keeps `a[data-page]`, `href="#<page>"` and the same
   conditions. `tests/test_browser.py` asserts exactly which `nav a[data-page]` a role sees and clicks each
   one; `#startBtn` must exist only for `session.control`.
3. **Page switching is hash based** (`showPage()` in `app/static/admin.js`, `.page[data-page]` with `hidden`).
   Keep it. The shell adds to it; it does not replace it.
4. **Existing ids the scripts use stay:** `nav`, `navToggle`, `streamPill`, `masterDot`, `pillTxt`,
   `modulePills`, `viewerCount`, `acctLink` (and `.acctname`), `alert`, `drawer`, `drawerBg`, `startBtn`, and
   every id inside the pages. Restyle them; do not rename them. `window.RELAY_ME` and `relayCan()` stay.
5. **New API routes need a line in `tests/test_auth.py`.** Preferences need none: use the existing per-user
   store (`app/prefs.py`, `GET /api/admin/me/prefs`, `PUT/DELETE /api/admin/me/prefs/core/<name>`).
6. **Stored preferences are untrusted input** (`app/prefs.py` says so). Read each one through an allow-list
   (`preset ∈ {relay, softpop, tangerine, brutalist}` and so on) and fall back to the default; never put a
   stored value into HTML as markup.
7. **New static files are core files.** Check `CORE_FILES` / `CORE_NETWORK` in `tests/test_modules.py` and add
   the new `admin.css` / `shell.js` there if the test requires it, saying why in the pull request.
8. **CI:** `tests/run_all.py`, `tests/run_security.py`, `node --test tests/js/*.test.mjs`, the browser test
   (needs Playwright), and the coverage floor in `.coveragerc` (`fail_under`) all run on every push. Pure
   shell logic should live in functions that can be unit-tested in `tests/js/`.
9. **Viewer pages are not part of this.** `viewer.css` is shared by the viewer, screen, rundown and tally pages.
   Do not retheme it; the admin gets its own stylesheet.

## Files to change

| File | Change |
|---|---|
| `app/static/admin.css` (**new**) | Tokens, presets, shell, components, animation. Replaces the ~450-line inline `<style>` in `admin.html`. Source: the mockup's `<style>`. |
| `app/static/shell.js` (**new**) | Preferences, popups (menu/popover/select), tooltips, command palette, sidebar collapse, nav parents, toasts. Source: the mockup's script, minus the fake pages and data. |
| `app/templates/admin.html` | New header + sidebar + content wrapper markup; keep every `data-page`, id and permission test; link `admin.css` and `shell.js`; remove the inline `<style>`; set `data-*` attributes on `<html>` from the user's preferences. |
| `app/static/admin.js` | `showPage()`: also open the parent group of the current page, update the breadcrumb, close the mobile drawer. Replace direct `#navToggle` handling with the shell's. Stream pill text/state hooks stay. |
| `app/static/*.js` page scripts (`users.js`, `network.js`, `modules.js`, `audit.js`, `overlay-builder.js`, `alert.js`, `status.js`, `rundown.js`, `tally.js`, `flasher.js`, `oauth.js`) | Only where markup they build changes (tables, rows, badges, empty states): they keep their ids and logic. Each per-group spec lists the hooks to preserve. |
| `app/templates/home.html`, `index.html`, `db_down.html`, `alert.html`, `flash.html`, `rundown.html`, `status.html`, `tally.html` | Standalone screens: link the shared tokens, adopt buttons, inputs, cards and badges; keep their layout. See `screens/screens-operator.md`. |
| `app/templates/viewer.html`, `app/static/viewer.css`, `viewer.js`, overlay templates | Tokens only (so presets and light/dark apply); reading ergonomics unchanged. Fix the top bar overflow at 375px. See `screens/screens-display.md`. |
| `app/modules/*/templates/*.html`, `app/modules/*/static/*` | Every module admin page into the new card patterns (rules in `screens/modules-av.md`), and the module's own screens (crew, intercom, parent paging, signage player, lighting/OBS panels, ProPresenter stage) onto the shared tokens. No module name may appear in a core file. |
| `app/modules/__init__.py` (`AdminPage`) | Optional: `parent` and `badge` for sub-menus (ProPresenter and Intercom have several pages) and a way for a page to be hidden from the sidebar. |
| `app/templates/login.html`, `password_change.html`, `_auth_head.html` | Split / centred auth layouts from the mockup (`.split`, `.cent`, `.fbox`). **No Register page** (Relay has none) and no social button; "Use a device code" is only a placeholder idea, drop it unless a device-code sign-in exists for users. |
| `app/templates/_lockup.svg`, `app/static/brand/` | Reuse. The collapsed rail shows the mark; add `_mark.svg` (the `R` glyph, `currentColor` bar + `var(--brand)` accent) beside `_lockup.svg`. The lockup's accent fill becomes `var(--brand)`, a constant `#4ade80`, so the logo never changes with the preset. |
| `app/main.py` | Read the six preferences for the signed-in user and pass them to `admin.html` so `<html data-…>` is right on first paint (no flash). Allow-list each value. |
| `tests/test_browser.py` | Run the existing per-role checks at three layout settings (inset/icon, flat/offcanvas, floating) and at phone width. |
| `tests/test_prefs.py` or new `tests/test_admin_shell.py` | Preference allow-list and defaults. |
| `tests/js/` | Unit tests for the pure helpers (page-number window, preference parsing, menu placement clamp). |
| `docs/guide/` | A short "Customize the panel" page with screenshots of each preset. |

## Design tokens

Names are the mockup's. They sit on `:root` / `html[data-preset][data-theme]`, so a module's page picks them
up for free.

| Token | Role | Old `viewer.css` / `admin.html` equivalent |
|---|---|---|
| `--bg` | page background | `--bg` |
| `--fg` | text | `--fg` |
| `--muted` | secondary text | `--dim` |
| `--card` | card and popup surface | `--card-bg` / `#0a0b0d` |
| `--border` | hairlines, input borders | `--line` |
| `--primary`, `--primary-fg` | main colour and text on it | `--accent` (partly) |
| `--hover`, `--hover-fg` | hover, active nav, selected rows | `#16171a`, `#1d1f23` |
| `--sb` | sidebar surface | `#050506` |
| `--r` | base radius | hard-coded 5–11px |
| `--shadow` | card shadow (zero for most presets) | none |
| `--ok`, `--warn`, `--err` | status colours, per mode | `--accent`, `--warn`, `--err` |
| `--brand` | logo green, constant | `--accent` in the lockup |
| `--font` | body font stack | system stack |

Mapping rule: wherever the old CSS used `--accent` as "the colour of good news" (live dot, `.ok`), use `--ok`;
wherever it meant "the brand / selected thing", use `--primary`. In the Relay preset they are the same green;
in the others they differ, so the distinction matters.

### Presets

Values are copied from the reference's `src/styles/presets/*.css` (converted from the same `oklch`), except
Relay, which uses the existing brand green (`#4ade80` dark, `#15803d` light, the logo's own colours).

| Preset | Mode | primary | background | card | border | radius | hover (active nav) |
|---|---|---|---|---|---|---|---|
| Relay | dark | `#4ade80` | `#09090b` | `#111113` | `#27272a` | 10px | primary at 14% |
| Relay | light | `#15803d` | `#f4f4f5` | `#fff` | `#e4e4e7` | 10px | primary at 12% |
| Soft Pop | light | `oklch(.5106 .2301 276.97)` | `oklch(.9789 .0082 121.6)` | `#fff` | `#000` | 16px | amber `oklch(.7686 .1647 70.08)` |
| Soft Pop | dark | `oklch(.6801 .1583 276.93)` | `#000` | `oklch(.2455 .0217 257.28)` | `oklch(.4459 0 0)` | 16px | yellow `oklch(.879 .1534 91.6)` |
| Tangerine | light | `oklch(.64 .17 36.44)` | `oklch(.94 0 236.5)` | `#fff` | `oklch(.9 .01 247.88)` | 10px | `oklch(.91 .02 243.82)` |
| Tangerine | dark | `oklch(.64 .17 36.44)` | `oklch(.26 .03 262.67)` | `oklch(.31 .03 268.64)` | `oklch(.38 .03 269.73)` | 10px | `oklch(.34 .06 267.59)` |
| Brutalist | light | `oklch(.6489 .237 26.97)` | `#fff` | `#fff` | `#000` | 0 | blue `oklch(.5635 .2408 260.82)`, white text; 4px hard shadow |
| Brutalist | dark | `oklch(.7044 .1872 23.19)` | `#000` | `oklch(.3211 0 0)` | `#fff` | 0 | blue `oklch(.6755 .1765 252.26)`; 4px hard shadow |

The full declarations are lines 11–18 of the mockup. Copy them as they are.

Fonts: four system stacks (System UI, Rounded, Serif, Monospace). The reference offers 18 web fonts; Relay runs
offline on a LAN, so no web fonts.

Browser support: `oklch()` and `color-mix()` need Chrome/Edge 111+, Safari 16.4+, Firefox 113+. The Tauri
webviews and current Chromium on the Pi meet this. If an older Pi browser must be supported, precompute hex
fallbacks for each token before the `oklch` line.

## Layout and behaviour

### State attributes (all on `<html>`)

| Attribute | Values | Default |
|---|---|---|
| `data-theme` | `dark`, `light` (resolved from the mode; `system` follows `prefers-color-scheme` and listens for changes) | `dark` |
| `data-preset` | `relay`, `softpop`, `tangerine`, `brutalist` | `relay` |
| `data-sb` | `inset`, `sidebar`, `floating` | `inset` |
| `data-collapse` | `icon`, `offcanvas` | `icon` |
| `data-layout` | `centered` (max 1536px), `full` | `centered` |
| `data-navbar` | `sticky`, `scroll` | `sticky` |
| class `cl` | sidebar currently collapsed (desktop only) | off |

Persisted per person with `PUT /api/admin/me/prefs/core/<name>`: `ui_preset`, `ui_mode`, `ui_sidebar`,
`ui_collapse`, `ui_layout`, `ui_navbar`, `ui_font`, `ui_collapsed`. The server renders them into the `<html>`
tag; the popover updates the attributes immediately and writes the preference in the background. A
`localStorage` mirror is optional (the mockup uses one only because it has no server).

### Dimensions

- Sidebar 272px, icon rail 48px (64px for floating, which has 8px of outer padding). Collapse animates width
  over 200ms, linear, as the reference does.
- Header 48px, `border-bottom`, sticky with `color-mix(bg 50%, transparent)` and `backdrop-filter: blur(12px)`.
  The scroll container is the content card, so the header sits inside it and keeps the card's rounded top.
- Content padding 24px (16px on phones); centred layout caps the page at 1536px. The current `main` cap of
  880px (and 1240px for the overlay builder via `main:has(#page-overlay…)`) goes away; wide pages use the
  card grid instead. Keep the overlay builder's two-column layout.
- Breakpoint: the mockup collapses at 900px; the app's current breakpoint is 760px. Pick one (recommend 900,
  since the sidebar is wider) and use it for both CSS and the JS `matchMedia`.

### Sidebar

- Top: the lockup (rail: the mark). Under it the **primary action**. In the mockup it is the stream button
  ("Start/Stop stream", with an Alerts shortcut beside it), which stands in for the reference's "Quick Create".
  In the app, wire it to the existing `#startBtn` flow and `session.control`; render it only for users with
  that permission, and show nothing if they lack it.
- Groups (`Live`, `Output`, `Automation`, `Settings`) with 32px labels that fade out on the rail.
- Items are 32px high, `calc(var(--r) - 2px)` radius. Active item uses `--hover` / `--hover-fg` plus weight 500
  and `aria-current="page"`. Hover is the same look.
- Parent items (only if a module declares sub-pages): chevron rotates 90° over 200ms; the child list expands
  with `grid-template-rows: 0fr → 1fr` (no height measuring); a left hairline indents the children. A parent
  is "active" when a child is, and opens itself.
- Badges (`new`, `updated`, `soon`): 11px, tinted border and fill (emerald / blue / zinc), hidden on the rail.
- Rail: labels, badges, group labels, help card and user text are hidden; items centre; parents open a
  **flyout menu** to the right; every item has a tooltip (`data-tip`). Group labels use `margin-top: -32px` +
  opacity 0 (the reference's trick) so groups stay compact.
- Help card above the user button: hidden on the rail. Content is Relay's own (guide link, issue link). Drop it
  if there is nothing useful to say.
- Bottom: the signed-in user (avatar, name, email-or-role, ellipsis) opens a menu: Account (`#account`),
  Sign out (the existing `POST /admin/logout` form; a menu item must submit it, not link to it).
- Mobile (≤ breakpoint): the sidebar becomes a drawer: slides from the left over 200ms ease-in-out with a
  blurred scrim; collapse and variant settings are ignored; tapping a link, the scrim, or pressing Esc closes
  it. Replaces the current `.menu` button and `nav.open` rule.

### Header

Left to right: sidebar toggle, vertical separator, search button (opens the palette; shows `⌘K`, or `Ctrl K`
off macOS), breadcrumb (`Group / Page`), spacer, stream pill (`#streamPill`, keeps the existing
`STREAMING` / `STOPPED` states and the dot), module pills (`#modulePills`), viewer count (`#viewerCount`,
hidden under 1100px), Preferences, theme cycle (monitor / sun / moon, as the reference), account avatar.

The current "Sign out" button in the header moves into the account menu. The stream pill's pulse is tied to the
real running state, so it is legitimate motion; the design-quality hook flags pulsing dots in general, so
suppress `pulsing-dot` for the file with that reason (the mockup already does, in `.impeccable/config.json`).

### Preferences popover

288px wide, padding 16. Rows in this order, each as in the reference: Theme Preset (custom select with a colour
dot), Fonts (custom select), Theme Mode (Light | Dark | System), Page Layout (Centered | Full Width), Navbar
Behavior (Sticky | Scroll), Sidebar Style (Inset | Sidebar | Floating), Sidebar Collapse Mode (Icon | OffCanvas),
Restore Defaults. Toggle groups are connected outline buttons, 32px high, the pressed one uses `--hover` and a
`--primary` border. Changes apply live.

### Popups (one implementation for all)

Menu, popover and select list share `popup(anchor, options)`: fixed position, placed from
`getBoundingClientRect()` on a side (`bottom`, `right`, `top`) and alignment (`start`, `end`), clamped to the
viewport with 8px margin, `transform-origin` set from the side, fade + scale from 95% over 100ms. Closes on
outside `pointerdown`, `Esc` (focus returns to the anchor), route change and resize. Up/Down move focus between
`.mi` items. Anchors carry `aria-haspopup` and `aria-expanded`. Menu items are `<button role="menuitem">`.

### Command palette (⌘K / Ctrl K)

Dialog, 560px, results grouped by nav group with the page icon, arrow keys + Enter + mouse hover, "No results
found." state, and an **Actions** group (start/stop stream, cycle theme, open preferences, toggle sidebar).
**Build the list from the DOM** (`nav a[data-page]` and their group labels) so module pages appear and
permission-hidden pages do not. Skip disabled items.

### Components

| Component | Spec |
|---|---|
| Card | `--card`, 1px `--border`, `--r`, 16px 18px padding, `--shadow`. Hover tints the border 35% toward `--primary`. Replaces `main section` hairlines. Title 14px/600, description 12px muted. |
| Stat card | label 12px muted, value 26px/700, delta line (use `--ok` / `--err`). |
| Button | 32px; default (card surface), primary (`--primary`), destructive. 1px nudge down on press. 3px focus ring at 50% `--primary`. |
| Input / select | `--bg` fill, `--border`, `calc(var(--r) - 2px)`. |
| Switch | 36×20, thumb slides 150ms, on = `--primary`. |
| Tabs | pill group on `--hover`; selected tab on `--card`. Replaces `.ovtabs`. |
| Badge | pill, status tints from `--ok/--warn/--err`; status badges in the users table add a 6px dot. |
| Table | 12px muted headers, 12px cell padding, row hover at 3% foreground, selected row `--hover`. |
| Avatar | rounded square, hue from the name, with an optional status dot (green check < 1 min, amber clock < 4 h, red < 7 d, grey ✕ older). |
| Toast | bottom-right, 200ms rise + fade, 1.8s. |
| Level meter | `--hover` track, `--primary` bar (the existing dBFS meter's bands stay). |

### Motion

All of it is CSS; nothing animates anything but `opacity`, `transform` and (sidebar only) width.

| Where | What | Time |
|---|---|---|
| Popup, popover, tooltip | fade + scale 95%→100% | 100ms ease-out |
| Command palette | fade + box scale | 100ms |
| Mobile drawer | slide, scrim fades | 200ms ease-in-out |
| Sidebar collapse | width, label opacity | 200ms linear |
| Nav parent | chevron rotate, child grid rows | 200ms |
| Page change | cards rise 6px + fade, 30ms stagger | 200ms |
| Buttons, nav, switches | colour, background | 150ms |
| Toast | rise + fade | 200ms |
| Live dot | pulse, only while streaming | 2s loop |
| Loading placeholder | shimmer (`.skel`, unused so far) | 1.4s loop |

`@media (prefers-reduced-motion: reduce)` turns every animation and transition off. The reference follows the
same rules (100–200ms, transform and opacity only, no bounce, charts not animated).

## Every screen (67)

The mockup has all of them: **35 pages inside the shell** (listed in the sidebar; "My account" is reached from the account menu) and **32 standalone screens** (listed at `#screens`, opened full page). Each group has its own spec with the route, source files, permission, what is on the screen, the ids and hooks that must stay, and what the real implementation must do:

| Group | Mockup file | Spec |
|---|---|---|
| Core live and output pages | `pages/core-live-output.js` | [`screens/core-live-output.md`](screens/core-live-output.md) |
| Core settings pages | `pages/core-settings.js` | [`screens/core-settings.md`](screens/core-settings.md) |
| AV module pages | `pages/modules-av.js` | [`screens/modules-av.md`](screens/modules-av.md) |
| People and signage module pages | `pages/modules-people.js` | [`screens/modules-people.md`](screens/modules-people.md) |
| Sign-in and operator tools | `pages/screens-operator.js` | [`screens/screens-operator.md`](screens/screens-operator.md) |
| Viewer, overlay, stage and room displays | `pages/screens-display.js` | [`screens/screens-display.md`](screens/screens-display.md) |
| Crew and phone apps | `pages/screens-crew.js` | [`screens/screens-crew.md`](screens/screens-crew.md) |

### Pages inside the shell

| Page id | Title | Nav group | Module | Spec |
|---|---|---|---|---|
| `health` | Session health | Live | core | core-live-output |
| `audio` | Audio input | Live | core | core-live-output |
| `targets` | Target languages | Live | core | core-live-output |
| `panels` | Panels | Output | core | core-live-output |
| `alerts` | Alerts | Output | core | core-live-output |
| `urls` | Viewer links | Output | core | core-live-output |
| `overlay` | Broadcast overlay | Output | core | core-live-output |
| `recordings` | Recordings & export | Output | core | core-live-output |
| `schedules` | Schedules | Automation | core | core-settings |
| `blocklist` | Blocked words | Settings | core | core-settings |
| `credentials` | Credentials | Settings | core | core-settings |
| `appearance` | Appearance & tuning | Settings | core | core-settings |
| `modules` | Modules | Settings | core | core-settings |
| `network` | Network | Settings | core | core-settings |
| `users` | Users | Settings | core | core-settings |
| `audit` | Audit log | Settings | core | core-settings |
| `account` | My account (account menu) | Settings | core | core-settings |
| `atem` | ATEM | Output | atem | modules-av |
| `tallyarbiter` | Tally Arbiter | Output | tally_arbiter | modules-av |
| `obs` | OBS Studio | Output | obs | modules-av |
| `youtube` | YouTube | Output | youtube | modules-av |
| `lighting` | Lighting | Output | lighting | modules-av |
| `ppcontrol` | Control | Output › ProPresenter | propresenter | modules-av |
| `pplive` | Slide view and lobby | Output › ProPresenter | propresenter | modules-av |
| `propresenter` | Settings | Output › ProPresenter | propresenter | modules-av |
| `planningcenter` | Planning Center | Automation | planning_center | modules-av |
| `crew` | Crew app | Settings | crew | modules-people |
| `intercom` | Intercom | Automation › Intercom | intercom | modules-people |
| `intercom-phones` | Signed-in phones | Automation › Intercom | intercom | modules-people |
| `intercom-remote` | Remote crew | Automation | intercom_remote | modules-people |
| `parentpaging` | Parent paging | Live | parent_paging | modules-people |
| `remoteaccess` | Remote access | Output | remote_access | modules-people |
| `signage` | Signage | Output | signage | modules-people |
| `signageweb` | Web & YouTube | Output | signage_web | modules-people |
| `alertevents` | Alert events | Automation | alert_events | modules-people |

### Standalone screens

| Screen id | Title | Category | Real route |
|---|---|---|---|
| `s-login` | Sign in (split) | Sign-in | `/admin` |
| `s-login-centered` | Sign in (centered) | Sign-in | `/admin` |
| `s-password-change` | Change password | Sign-in | `/admin/password` |
| `s-db-down` | Database unavailable | Sign-in | `any page (503) and the 403 page` |
| `s-landing` | Landing | Sign-in | `/` |
| `s-home` | Home | Operator tools | `/home` |
| `s-alert` | Alerts | Operator tools | `/alert` |
| `s-flash` | Flash a panel | Operator tools | `/admin/flash` |
| `s-rundown` | Rundown | Operator tools | `/screen?rundown=...` |
| `s-status` | Status | Operator tools | `/status` |
| `s-tally` | Tally | Operator tools | `/tally` |
| `s-viewer-translation` | Viewer: translation | Viewer | `/translation` |
| `s-viewer-both` | Viewer: both | Viewer | `/both` |
| `s-viewer-transcription` | Viewer: transcription | Viewer | `/transcription` |
| `s-viewer-phone` | Viewer: phone (375px) | Viewer | `any viewer mode` |
| `s-present` | Present (projector) | Viewer | `/present` |
| `s-screen` | Screen (room display) | Viewer | `/screen` |
| `s-overlay` | Overlay (broadcast captions) | Viewer | `/overlay, /overlay/{id}` |
| `s-stage-slides` | Slide view (/stage) | Displays & stage | `/stage` |
| `s-signage-player` | Signage player | Displays & stage | `/signage/{slug}` |
| `s-lighting-screen` | Lighting touch panel | Displays & stage | `/screen?lighting=` |
| `s-obs-screen` | OBS touch panel | Displays & stage | `/screen?obs=` |
| `s-crew-home` | Crew app: Today | Crew & phones | `/crew` |
| `s-crew-chat` | Crew app: Chat | Crew & phones | `/crew#chat` |
| `s-crew-checklists` | Crew app: Checklists | Crew & phones | `/crew#checklists` |
| `s-crew-resources` | Crew app: Resources | Crew & phones | `/crew#resources` |
| `s-crew-activity` | Crew app: Activity | Crew & phones | `/crew#activity` |
| `s-crew-intercom` | Crew app: Intercom | Crew & phones | `/crew#intercom` |
| `s-crew-join` | Join the crew app | Crew & phones | `/crew-join` |
| `s-crew-page` | Page confirmation | Crew & phones | `/crew/page/{token}` |
| `s-crew-offline` | Crew app offline | Crew & phones | `/crew/_/offline` |
| `s-paging-room` | Parent paging room | Crew & phones | `/paging/{secret}` |

### How the three kinds of screen adopt the design

1. **Operator pages (shell).** Markup moves into the new shell; every id, `data-` hook and permission test stays (the per-group specs list them). Module pages follow the rules in the "Module pages in the new shell" section of [`screens/modules-av.md`](screens/modules-av.md).
2. **Sign-in, operator tools and crew/phone apps (standalone).** They link the shared token stylesheet and use the same buttons, inputs, cards and badges, but keep their own layout (no sidebar). Modules that serve their own pages (crew, intercom, parent paging, signage) link the shared tokens from core's static path; see [`screens/screens-crew.md`](screens/screens-crew.md) for how and why `join.html` and `offline.html` keep inline CSS.
3. **Viewer and display screens.** The viewer, present, screen, overlay, stage, signage player and touch panels keep their reading ergonomics (large type, 34ch measure, settled and open line colours). They adopt the *tokens* only, so a preset and light/dark apply, and nothing about their size or contrast changes. [`screens/screens-display.md`](screens/screens-display.md) has the `viewer.css` variable mapping.

### Things in the mockup that are not product

Demo chips ("Demo states", "Demo: view-only role"), state strips, the fake data, decorative QR codes, and the "All screens" gallery exist only to review the design. Do not build them.

### Shell changes made after the screens were written

Done in the mockup shell: `--info`, `--err-fg` and `--faint` tokens, tab contrast fixed for Soft Pop, `.card .d` no longer hits the destructive button, a parent's icon can be set with `picon`, `hidden:true` pages stay out of the sidebar, an `authLayout` helper.

Still open, to build once in `admin.css` rather than per screen (each group spec lists where it was needed): a modal dialog, a side drawer, a disabled-button style, a status row, a form footer, a key-value list, a neutral badge, QR / thumbnail / banner components, a phone-frame wrapper for the crew pages, an API for a screen to choose its own theme and preset (the viewer and crew apps), and allowing a sidebar parent to span two groups (ProPresenter's Settings is under Automation in the app but Output in the mockup).

## What the mockup invents (do not build)

- The fake data (sessions, panels, users, recordings, languages).
- The **Integrations** parent and its badges (see constraint 1).
- **Register** page and the "Use a device code" button.
- The **account switcher** (switching users). Relay has one signed-in user; the header avatar menu is just
  Account and Sign out.
- "Notifications" menu item, the "Need a hand?" copy, and the Alerts shortcut next to the stream button unless
  the permission exists (`alerts.view`).
- Per-language URL list and QR on `urls` are illustrative; use what the page already renders.

## What was taken from the reference

Read from `~/Downloads/next-shadcn-admin-dashboard-main` (v2.2.0). Files studied and what came from each:

| Reference file | Taken |
|---|---|
| `src/app/globals.css`, `src/styles/presets/*.css` | token names and the preset values (`brutalist`, `soft-pop`, `tangerine`) |
| `src/lib/preferences/preferences-config.ts` | the preference set, values and defaults (`theme_mode`, `theme_preset`, `font`, `content_layout`, `navbar_style`, `sidebar_variant`, `sidebar_collapsible`) |
| `src/app/(main)/dashboard/layout.tsx` | header height, sticky blur, centred max width (`max-w-screen-2xl`), padding, sidebar width (`--spacing * 68`) |
| `_components/header/layout-controls.tsx` | the Preferences popover: rows, order, labels, Restore Defaults |
| `_components/header/theme-switcher.tsx` | the monitor / sun / moon cycle |
| `_components/header/account-switcher.tsx`, `sidebar/nav-user.tsx` | account menu structure (label, items, separator, log out) and the right-hand menu from the sidebar user |
| `_components/header/search-dialog.tsx` | grouped command palette built from the nav config |
| `_components/sidebar/nav-main.tsx`, `navigation/sidebar/sidebar-items.ts` | groups, parent / sub-items, badges, the collapsed-rail flyout, the primary action slot |
| `_components/sidebar/support-card.tsx` | the help card |
| `components/ui/sidebar.tsx` | variants (`sidebar`, `inset`, `floating`), collapse modes, 200ms linear width |
| `components/ui/{dialog,sheet,tooltip,switch,button,tabs,skeleton}.tsx`, `tw-animate-css` | animation timings and which properties animate |
| `app/(main)/dashboard/users/_components/*` | data-table design: columns, avatar with status badge, status badge with dot, two-line role cell, pagination window of three |
| `app/(main)/auth/v1`, `v2` | centred and split auth layouts |

Not taken: React / Next / Tailwind / Radix / Base UI code, TanStack Table, Recharts, the 18 web fonts, the demo
dashboards (CRM, finance, e-commerce, kanban, calendar, mail, chat), and the landing page.

## Rollout (one pull request each; run the full suite and security scans before every push)

1. **Tokens, no visual change.** Add `admin.css` with the tokens only, in the Relay preset set to match today's
   look; move the inline `<style>` out of `admin.html`. Proves the extraction and the CSS cache path.
2. **Shell.** New header, sidebar (inset, icon collapse), content wrapper, mobile drawer, breadcrumb. Pages keep
   their old inner look. Update `showPage()`; extend `tests/test_browser.py` for phone width.
3. **Shared components.** Cards, buttons, inputs, tabs, switches, badges, tables, toasts, plus the open items
   under "Shell changes made after the screens were written" (dialog, side drawer, status row, form footer,
   key-value list, neutral badge, disabled style).
4. **Core pages** in three pull requests: live and output (`screens/core-live-output.md`), settings
   (`screens/core-settings.md`, including the users table and account), then the overlay builder on its own.
5. **Module pages**, one pull request per module family (`screens/modules-av.md`, `screens/modules-people.md`),
   each following the module-page rules; add `parent` / `badge` support to `AdminPage` first.
6. **Preferences and presets.** Popover, the preferences through `/api/admin/me/prefs`, server-rendered
   `data-*`, presets, fonts, flat/floating variants, off-canvas collapse, `tests/` for the allow-lists.
7. **Palette, menus, tooltips, motion.** Popup system, ⌘K from the DOM nav, rail tooltips and flyouts, animation
   and reduced-motion.
8. **Sign-in and operator tools** (`screens/screens-operator.md`): login, password change, home, alert sender,
   flasher, rundown, status, tally, database-down.
9. **Viewer and displays** (`screens/screens-display.md`): tokens into `viewer.css`, overlay, present, screen,
   stage, signage player, touch panels. Check against real captions and the 375px bar fix.
10. **Crew and phone apps** (`screens/screens-crew.md`): shared tokens linked from the module, phone layout,
    offline and join pages keeping inline CSS.
11. **Guide and screenshots** in `docs/guide/`, and removal of any dead CSS.

## Acceptance checklist

- [ ] With the Relay preset in dark mode, inset sidebar, the panel is visually the mockup; every existing page
      still works and every id in the constraints list is intact.
- [ ] `tests/run_all.py`, `tests/run_security.py`, `node --test tests/js/*.test.mjs` pass; the browser test
      passes for each role at inset/icon, flat/offcanvas, floating, and at phone width.
- [ ] A user's preferences survive reload and sign-in on another device; an invalid stored value falls back to
      the default; no stored value is rendered as HTML.
- [ ] All four presets pass WCAG AA for body text and for text on `--primary` and `--hover` in both modes
      (check Brutalist's white-on-blue hover and Soft Pop's black-on-amber).
- [ ] Keyboard: every menu, the popover, the palette and the drawer open, move, select and close without a
      mouse; focus is visible; `Esc` returns focus to the control that opened it.
- [ ] `aria-current="page"` on the active link, `aria-expanded` on parents and menu buttons, tooltips are not
      the only label for a rail item (`aria-label` or the visible text stays in the DOM).
- [ ] Reduced-motion users get no animation.
- [ ] No module name appears in a core file; `tests/test_modules.py` passes.
- [ ] The Pi and the Tauri webview render the shell (check `oklch` / `color-mix` support).

## Known gaps in the mockup

- The page files were written by reading each real template and script, but not every state of every screen was
  checked in every preset and both modes; the per-group specs say what each author did and did not look at.
  Review the screens you care most about in the preferences popover's presets before starting that pull request.
- Where the mockup differs from the app on purpose (a redesigned crew tab bar, the ProPresenter nav group, extra
  confirm dialogs, an account Preferences card, a 403 card), the group spec says so; those are design proposals,
  not existing behaviour.
- The `system` theme follows the OS and listens for changes. Relay's light mode was checked less than its dark mode.
- `--hover` for Relay is a tint of `--primary`; the other presets use their own accent colours, as the reference does.
- The mockup is about 660 KB of script across seven page files. That is fine for documentation; none of it ships.
