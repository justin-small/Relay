# Mockup page files

`../index.html` is the shell (sidebar, header, preferences, menus, palette, tokens, components). Every screen of
Relay is one entry registered by a file in this folder. Open `../index.html` in a browser (not a preview pane
that blocks hash navigation); `#screens` lists every standalone screen.

## How a page file works

Plain script (no modules, no imports), loaded after the shell's script and before `boot()`:

```js
// a page inside the shell (listed in the sidebar):
reg({kind:'page', id:'atem', t:'ATEM', g:'Live', ic:'mod', module:'atem'});      // id = the real data-page / AdminPage.id
P.atem = () => `<h2 class="t">ATEM</h2><p class="sub">…</p> …cards…`;            // returns the page's HTML
AFTER.atem = () => { /* optional: wire events after the HTML is in the DOM */ };

// a standalone screen (no shell; a "← All screens" chip is added):
reg({kind:'full', id:'s-viewer', t:'Viewer', cat:'Viewer', desc:'…', path:'/transcription'});
FULL['s-viewer'] = () => `…whole screen html…`;
AFTER['s-viewer'] = () => {};
```

`reg` fields: `kind` (`page` | `full`), `id`, `t` (title), `g` (nav group for pages: `Live`, `Output`,
`Automation`, `Settings`), `ic` (icon name, see `I` in index.html), `module` (module id for module pages),
`parent` (a nav parent label: pages of one module with two or more pages share one, e.g. `ProPresenter`),
`badge` (`new` | `updated` | `soon`), `perm` (the real permission), `cat` (for `full`: `Sign-in`,
`Operator tools`, `Displays & stage`, `Crew & phones`, `Viewer`), `desc`, `path` (the real URL).

Ids for standalone screens start with `s-`. Page ids are the real ones from the app.

## What you can use

- Tokens only: `var(--bg --fg --muted --card --border --primary --primary-fg --hover --hover-fg --sb --r --shadow
  --ok --warn --err --brand)`. No hard-coded colours except inside a screen that is a kiosk/stage display (and
  then derive from tokens where you can). Both `data-theme=dark` and `light`, and every preset, must look right.
- Layout: `.grid` with `.g2` (2fr 1fr), `.g3`, `.g4`; `.row` (flex, wrap); `.card` (+ `h3`, `.d` description);
  `stat(label, value, sub, 'up'|'dn')`; `bars(n)` chart.
- Controls: `input`, `select`, `textarea`, `label`, `.btn` (`.p` primary, `.d` destructive, `.sm`), `.sw`
  switch (add `on`; `onclick="this.classList.toggle('on')"`), `.tabs` + `onclick="tab(event)"`, `.tg` connected
  toggle group (`aria-pressed`), `.badge` (`ok|warn|err`), `.sbadge`, `.pill`, `.dot`, `kbd`, `.meter`,
  `.av` avatars, `.two` two-line cell, `table`, `.dt` data table look, `.dtbar`, `.pg`/`.pgn`, `.sel`.
- Helpers: `ic(name)` icon, `toast(msg)`, `toggle(anchor,{items,side,align})` menus (see `acctItems`),
  `injectCSS(key, css)` for CSS a screen needs (scope every selector under your own class prefix), `LOCK`
  (the lockup svg), `authLayout({...})`, `stream()`, `$`.
- Page headings: `<h2 class="t">Title</h2><p class="sub">one line</p>` as the first elements.

## Fidelity rules

1. Read the real template **and the script that fills it**. Show every section, field, button, table column,
   note and empty/error text the real page has, using its real labels, in its real order of importance.
2. Show the states that matter: the view-only note (`You can view this page but not change it.`), empty
   list, a connected / not connected module, an error line, a running vs stopped state. Use small `tabs` or a
   toggle in the page for state demos where a state changes the layout a lot.
3. Realistic sample data (a church: sanctuary, nursery, youth, worship), never lorem ipsum.
4. Nothing you build may need a server. Buttons call `toast()` or toggle local state.
5. Keep each page file readable: one function per page, shared bits in local helpers.

## Spec fragment

Each page file has a twin in `docs/redesign/screens/<same name>.md`: for every screen, its route, template and
script files, permission, what is on it, the **element ids/classes/`data-` hooks that existing scripts and
tests rely on and must stay**, what the mockup changes, and what the real implementation must do
(markup moved, components used, anything the mockup could not show).
