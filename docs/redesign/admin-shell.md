# Operator panel redesign: the admin shell

Everything needed to apply the new operator-panel look to Relay later, without having to look at the
reference project or this conversation again.

- **The mockup (the target):** [`docs/mockups/admin-shell/index.html`](../mockups/admin-shell/index.html).
  One self-contained file, no network, no build. Open it in a browser. It is the visual and behavioural
  source of truth; the CSS between its `<style>` tags and the script at the bottom are meant to be lifted,
  not re-drawn.
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
| `app/static/users.js` (`#users`) | Table restyle; optional sort, filter and pagination from the mockup's data table. |
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

## Page-by-page application

Every existing page keeps its content, ids and scripts; only the wrapper changes. Each `<section>` becomes a
`.card`; each `h2.title` + `p.sub` stays as the page heading; two-column pages use `.grid.g2`; stat rows use
`.grid.g4` / `.g3`.

| Page (`data-page`) | Notes |
|---|---|
| `health` | Four stat cards (stream, viewers, latency, languages) above the transcript and control cards, then the sessions table. Needs a latency figure if one is not already reported; otherwise omit that card. |
| `audio` | Meter card + device card. |
| `targets` | Switch list (one row per language) + engine card. |
| `panels` | Stat row + panels table with Identify/Update; the existing drawer keeps working. |
| `alerts` | Tabs (Buttons, Stage messages, Replies, History) replace the long page; the History tab keeps its `alerts.history` permission test. |
| `urls` | Links card with Copy buttons + QR card (the QR already exists in `qrcode.min.js`). |
| `overlay` | Keep the builder's two-column layout and sticky preview; wrap controls in cards. |
| `recordings`, `schedules`, `blocklist`, `credentials`, `appearance`, `audit`, `network`, `modules` | Card wrap and the shared components. `network` keeps its 5-second refresh and per-class Test results. `modules` becomes the card grid with switches shown in the mockup. |
| `users` | Table restyle, plus optional sort / filter / pagination (client-side, from the mockup's data table). Keep the existing add / edit / reset flows in `users.js`. |
| module pages | They inherit tokens and components automatically. They do not need edits unless they use hard-coded colours; grep each module's templates for `#` colours and replace them with tokens. |

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
3. **Components.** Cards, buttons, inputs, tabs, switches, badges, tables, toasts; restyle page by page
   (group the pages into two or three pull requests).
4. **Preferences and presets.** Popover, the six preferences through `/api/admin/me/prefs`, server-rendered
   `data-*`, presets, fonts, flat/floating variants, off-canvas collapse, `tests/` for the allow-lists.
5. **Palette, menus, tooltips, motion.** Popup system, ⌘K from the DOM nav, rail tooltips and flyouts, animation
   and reduced-motion.
6. **Users table and sign-in pages.**
7. **Guide and screenshots** in `docs/guide/`, and removal of any dead CSS.

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

- Static HTML: the sidebar's hash navigation works when the file is opened directly in a browser, but not in
  embedded previews that block it.
- Only the Users page has real table behaviour; other tables are static.
- The `system` theme follows the OS and listens for changes; the Relay preset's light mode was checked less
  than its dark mode.
- Light and dark `--hover` for Relay is a tint of `--primary`; the other presets use their own accent colours,
  which is how the reference does it.
