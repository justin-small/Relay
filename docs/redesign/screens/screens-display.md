# Viewer and display screens

Mockup file: `docs/mockups/admin-shell/pages/screens-display.js` (open `index.html#screens`, categories **Viewer** and
**Displays & stage**). Eleven standalone screens, none inside the operator shell, all with a floating "Demo controls"
chip (bottom right, movable with the arrows button, collapsible) that switches states, theme and look. The chip is
mockup furniture and does not exist in the product.

Screens covered: `s-viewer-translation`, `s-viewer-both`, `s-viewer-transcription`, `s-viewer-phone`, `s-present`,
`s-screen`, `s-overlay`, `s-stage-slides`, `s-signage-player`, `s-lighting-screen`, `s-obs-screen`.

Not covered here (someone else's file, or not a display): the chooser `/` (`templates/index.html`), `/status`, `/tally`,
`/alert`, `/screen?rundown=` (`rundown.html`), and the admin page partial
`app/modules/propresenter/templates/live.html` ("Slide view and lobby"). `live.html` is an admin page, not a display;
the only display part in it is the slide-view widget, which is the same `RelaySlideView` that `/stage` mounts, shown
here as `s-stage-slides`.

## How viewer.css adopts the tokens without changing reading ergonomics

`viewer.css` has its own token set (`--bg --fg --dim --line --accent --bar-bg --btn-bg --btn-active --settled --open
--card-bg ...`, one dark and one light block, switched by `data-theme` on `<html>`). The redesign replaces those values
with the shared tokens and changes **nothing else** in how text is laid out.

| viewer.css token | becomes | note |
|---|---|---|
| `--bg`, `--fg` | `--bg`, `--fg` | same names, now preset-aware |
| `--dim`, `--line` | `--muted`, `--border` | |
| `--accent` | `--primary` | caret, `button.on` border, focus |
| `--ok`/`--warn`/`--err` (status dot) | same names | already status tokens |
| `--bar-bg` | `--sb` | the top bar is a sidebar-grade surface |
| `--btn-bg` / `--btn-active` | `--card` / `--hover` | buttons, picker, jump pill |
| `--card-bg`, `--card-hover` | `--card`, `--hover` | status/alert cards |
| `--label-bg`, `--toast-bg` | `color-mix(in srgb, var(--bg) 75%, transparent)`, `--card` | |
| `--settled` | `color-mix(in srgb, var(--fg) 78%, var(--bg))` | settled line, dimmer than open |
| `--open` | `--fg` | the open line is the brightest thing on screen |
| `--font-px` | unchanged | still set by `viewer.js` from A+/A- (18 to 140, step 4) |

Keep verbatim, because these are the product: `.feed` (`font-size: var(--font-px)`, `line-height: 1.5`, `weight 500`,
`padding: 28px 22px 45vh`, 16vh tail when `.panes.dual` stacks), `.feed p { max-width: 34ch; margin: 0 auto .55em }`, the
settled/open colour split, the blinking `--primary` caret on `p.open`, the reduced-motion rule, `.pane-label`, the
`@media (min-width: 900px)` dual-row switch and the `max-width: 520px` phone tightening. Contrast: settled text must stay
at least 7:1 on `--bg` in every preset, both themes (check Soft pop light, whose `--bg` is warm off-white, and Tangerine).
`:root` must keep setting `data-theme` from `relay.theme` in localStorage; `viewer.js` writes it and the shell's
preset attribute `data-preset` is read from the same `<html>` element, so a viewer can eventually inherit the church's
preset without a second theme system.

The mockup uses `cqw`/`cqh` and `@container` instead of `vw`/`vh`/`@media` only so a screen can be shown inside a phone
frame and inside a 16:9 overlay frame. The real CSS keeps `vw`, `vh`, `@media`.

Kiosk and output exceptions (literal colour stays, as in the real CSS, because they are safety or broadcast contracts):
the red and white alert flash (1 Hz, seizure-safe), the amber flash, the steady blue of an info message (`--dv-info`),
the whole overlay colour set (`--ov-color`, plate colour, edge colour, matte black and white), the signage TV (always
black, slides are operator media) and the slide-view frame. Everything else is a token.

## Common viewer contract (all of `/transcription`, `/translation`, `/both`, `/present`, `/screen`, `/overlay`)

One template (`app/templates/viewer.html`), one script (`app/static/viewer.js`), one stylesheet (`viewer.css`),
branching on `mode`, `present`, `screen`, `overlay`. The mockup keeps this: the screens are the same markup with
different wrapper classes (`.present`, `.screen.align-*`, `.overlay.ov-*`).

Hooks that `viewer.js`, `alert-screen.js`, `lyrics.js`, `tests/js/*.test.mjs` and the browser test rely on and must stay:

- Body classes (become wrapper classes only in the mockup): `present`, `screen`, `align-bottom|center`, `screen-fixedfont`,
  `overlay`, `ov-hidden`, `ov-pos-*`, `ov-align-*`, `ov-font-*`, `ov-edge-*`, `ov-rollup|ov-popon`, `overlay-matte`,
  `alert-page`, `status-page`, `tally-page`. `data-alert="<name>"` on `<body>` of `/screen`.
- Ids: `statusDot`, `langPicker`, `themeToggle`, `wakeToggle`, `presentBtn`, `presentExit`, `toast`, `lyrics`,
  `lyricsText`, `alertFlash`, `alertFrom`, `alertTo`, `alertReplies`, `stageBanner`, `stageMessage`, `stageFrom`,
  `stageText`, `screenFault`.
- Classes: `.bar` (+ `.spacer`), `.panes` (+ `.dual`, `.ov-canvas`), `.pane`, `.pane-label`, `.ov-label`, `.feed`, `.log`
  (`role="log" aria-live="polite" aria-atomic="false"`), `.empty`, `.jump`, `.dot` (+ `.ok .warn .err`), `.toast` (+
  `.show`), `p.settled`, `p.open` (`aria-hidden="true"` until it settles), `.lyrics`, `.lyrics-text`, `.alert-flash*`,
  `.stage-message*`, `.stage-banner`, `.screen-fault`.
- Data attributes: `data-font="up|down"`, `data-stream="transcription|translation"`, `data-lang`, `data-lines`,
  `data-finals="1"`, `data-level="info|attention|urgent"`, `lang`/`dir` on `.feed` (RTL for ar, fa, he, ur).
- `window.RELAY` inline config object (`fontPx sourceLanguage sourceIso mode present screen screenLang screenFont overlay
  overlayLang overlayW overlayH overlayFont overlayHold overlayPopOn overlayDemo`).
- localStorage keys: `relay.font`, `relay.lang`, `relay.theme`, `relay.wake`.
- Behaviour that must not change: the open line lives outside `.log` and moves into it when it settles (screen readers
  announce settled text once); the pane unpins when the reader scrolls up more than `max(120px, 3 x font)` and `.jump`
  reappears; reconnect backoff 500 ms doubling to 5 s; `lastSeq` replay; kiosk modes (screen, overlay) never show
  toasts, pickers, placeholders or operator-facing text.

## 1. `s-viewer-translation`: /translation

- **Files**: `app/templates/viewer.html`, `app/static/viewer.js`, `app/static/viewer.css`; route `page_translation` in
  `app/main.py`.
- **Who/device**: anyone in the congregation, on their own phone (most), tablet or laptop; no sign-in.
- **On it**: top bar (live dot, links English / Translation / Both, language picker, theme, keep-awake, present,
  A- and A+), then one full-height feed of the translation. Settled lines dimmer, the open line white with a blinking
  caret, the reader pinned to the bottom with 45vh of tail so the live line sits mid-screen. States in the demo chip:
  Live; Waiting for audio (dot amber, empty text `Waiting for audio…`); Reconnecting (dot red, `Reconnecting…` pill rises
  from the bottom); Languages live: two / one (picker disabled) / none (picker shows `No languages live`, pane shows
  `No target language is live yet. Ask the operator to enable one.`); text size; "Scroll back" shows the `Jump to live
  ↓` pill.
- **Changes in the mockup**: tokens only (table above); the bar gains left padding in the mockup only to clear the
  shell's "All screens" chip. No change to wording, order or behaviour. The brief called the empty state "waiting for the
  service to start"; the product says `Waiting for audio…`, and the mockup keeps the product wording. If the new wording is
  wanted it is a one-line text change in `viewer.html` and the placeholder fallback in `viewer.js`.
- **Real implementation**: swap the token block at the top of `viewer.css`; keep every selector. Add `data-preset` to
  `<html>` only if the church preset should reach viewers (optional, not needed for this redesign). Picker options are
  English target labels (`Spanish`, `Portuguese`) from `/api/targets`; keep that (a native-name option would be new
  behaviour). The language picker has `max-width: 42vw`; see the phone note about the bar overflowing.

## 2. `s-viewer-both`: /both

Same files. Two panes: `English` (source label from `source_label`) and `Translation`, each with a `.pane-label` top right.
Side by side at 900px and up, stacked below that (16vh tail instead of 45vh when stacked). Each pane has its own `.jump`
button and scroll state. The translation pane follows the picker; the source pane is fixed. Demo chip as for 1.
Real implementation: unchanged markup; only tokens.

## 3. `s-viewer-transcription`: /transcription

Same files. Only the source-language transcript, no picker (the bar loses `#langPicker`). Page `<title>` is
`{source_label} Transcript`, `<html lang>` is `source_iso`. Demo chip: service state, text size, scroll back.

## 4. `s-viewer-phone`: the viewer at 320, 375 and 414 px

Not a second template: the same page inside a phone frame, with `/transcription`, `/translation`, `/both` selectable and
a default text size of 28 (a phone that has pressed A- twice; the config default is 40). At 520px and below the bar gets
`gap: 6px`, `padding: 7px 9px`, smaller buttons and links, `.feed` padding 20px 16px, and `/both` stacks.

- **Finding (change to make)**: in the real CSS the bar is a single non-wrapping flex row. With dot, three links, the
  picker (up to 42vw) and five buttons it is wider than a 375px phone, so the right-hand buttons (present, A-, A+) are
  pushed off screen. The mockup lets the bar wrap to a second row at 520px and below (`flex-wrap: wrap`). The real
  implementation should do the same, or move the text-size and present buttons to a second row. Cheap alternative:
  hide the page links under 520px and rely on the picker. Whatever is chosen, A- and A+ must stay reachable, because
  they are the accessibility control.
- Keep-awake (`#wakeToggle`) is `hidden` until `navigator.wakeLock` exists; the mockup shows it on.
- Tests: `tests/test_browser.py` should add a 375px viewport assertion that `[data-font=up]` is inside the viewport.

## 5. `s-present`: /present

- **Files**: same, route `page_present`; `?mode=` picks the feed (default translation); `?present=1` on any viewer route
  does the same; `presentBtn` toggles it in-page and requests fullscreen; `fullscreenchange` leaves it on Esc unless the
  page was loaded as present.
- **Who/device**: a projector, a confidence monitor, or a laptop on the stage; one tech sets it up.
- **On it**: no bar, bottom-anchored feed that never scrolls (`overflow: hidden`, `justify-content: flex-end`), type 15%
  larger than the reader's setting, padding `4vh 6vw 6vh`, no jump pill, and `#presentExit` (a 40px circle, top right,
  50% opacity). Demo chip: `?mode=`, state, languages, size. Clicking the exit button shows the normal bar, as it does
  on the real page.
- **Real implementation**: tokens only. The exit circle uses `--card` and `--border`; keep its low opacity so it does
  not burn into a projector.

## 6. `s-screen`: /screen (room display)

- **Files**: `viewer.html` (`screen` branch), `viewer.js` (kiosk), `alert-screen.js` (`?alert=`), `lyrics.js` (`?lyrics=1`),
  `viewer.css` ("screen mode" and "alerts"); route `page_screen`. Also the dispatcher for module screens
  (`?lighting=`, `?obs=`) and `?rundown=`; those have their own screens.
- **Who/device**: a lobby or sanctuary TV or a wall display behind a kiosk browser; configured once by typing the URL.
  The URL is the entire settings surface: `stream`, `lang`, `lines` (default 3), `align` (`bottom` or `center`), `font`
  (px, pins the size), `alert=<name>`, `lyrics=1`.
- **On it**: only captions. Type scales with the screen (`clamp(30px, 5.5vw, 110px)`) unless `font` pins it, last N
  lines kept (`data-lines`), no caret (burn-in), no scrollbar, no picker, no toasts, no placeholder. A 12px red dot in the
  bottom-right corner appears only while a stream is down (`#screenFault`). Demo chip: stream (translation,
  transcription, both with pane labels), lines 1/2/3/5, align, auto vs pinned font, alert flash by level (urgent
  red/white, attention amber/dark, info steady blue) with the reply buttons, stage message card (info or attention) or
  banner, a lyrics slide in place of captions, the fault dot. Tapping the flash dismisses it (`POST /api/alerts/ack`);
  reply buttons send their text.
- **Hooks that must stay**: all `alert-flash*`, `stage-*`, `screen-fault`, `lyrics*` ids; `data-level`; the 1 Hz flash;
  `body[data-alert]`; no `.empty` in kiosk; `data-lines`.
- **Mockup change**: tokens; the stage message and banner keep their literal blue (`--dv-info`) because info is a
  colour code, not a brand colour. The flash colours are a safety spec and are not themed.
- **Real implementation**: tokens only. Do not introduce any always-on bright element (burn-in rule). The lyrics slide
  keeps `--lyrics-px` (default `5.2vw`) and `pre-line` whitespace.

## 7. `s-overlay`: /overlay and /overlay/{id}

- **Files**: `viewer.html` (`overlay` branch), `viewer.js` (overlay + demo), `viewer.css` ("overlay mode"),
  `app/overlays.py` (`PRESETS`, `SAMPLES`, `resolve`), `overlay-fonts.css`; routes `page_overlay`, `page_named_overlay`
  (404 `unknown overlay`). The admin builder previews `/overlay?demo=1` in a frame; ATEM key uses `?matte=1`.
- **Who/device**: an OBS, vMix or ProPresenter browser source, or a keyer on an ATEM; the livestream tech. Never a
  person's phone.
- **On it**: a fixed 1920 x 1080 canvas scaled to the source, transparent everywhere nothing is drawn (the mockup shows it
  over a stand-in programme video, or over a checkerboard, which is what the source itself renders). Each caption line
  carries its own plate sized to the text; options through the URL: `preset` (lower third, top strip, accessibility slate,
  two languages), `pos`, `align`, `edge` (plate, outline, shadow, none), `mode` (roll-up, pop-on with finals only),
  `lines`, `font`, `maxw`, `safe` (title-safe inset), `family`, `color`, `platec`, `plate`, `bg`, `hold` (fade after
  silence), `labels` (native language name above each of two stacked feeds), `matte` (white on black for a linear key),
  `lyrics=1`. Demo chip exercises preset, position, alignment, edge, mode, matte, backdrop and Speaker silent (the fade
  after `hold`). Two-language preset shows `Español` over `English` labels.
- **Hooks that must stay**: `.overlay`, `.ov-canvas`, `.ov-label`, `--ov-w --ov-h --ov-font --ov-pad-x --ov-pad-y
  --ov-plate --ov-platec --ov-color --ov-maxw --ov-bg --ov-hold-ms` (set from `overlays.resolve`, never from raw
  input), `data-finals`, `data-lines`, `html:has(body.overlay)` transparent background. The overlay does not take the
  shared tokens: `--settled` and `--open` are pinned to `--ov-color`, because the viewer's theme toggle still runs on this
  page and must not recolour a broadcast graphic.
- **Mockup change**: none to the output. The info strip and checkerboard are explanation, not product.
- **Real implementation**: the admin overlay builder (another screen) should preview through the same page, as it does.

## 8. `s-stage-slides`: /stage (ProPresenter slide view)

- **Files**: `app/modules/propresenter/templates/stage.html`, `static/slideview.js` (`RelaySlideView.create`),
  `static/slideview.css`; routes `/stage`, `/api/stage/live`, `/api/stage/live/image/{which}` in
  `app/modules/propresenter/routes.py`. 404 unless **Let the slide view be opened without the admin login** is on (the
  mockup shows the 404 body `{"detail":"Not found"}` as "Off").
- **Who/device**: the sound or production desk, or a stage monitor, on a laptop or tablet in the room; no login when
  public.
- **On it**: a header (`.sv-title`, a coloured `.sv-part` pill such as Verse 1, `.sv-count` as `slide 3 of 7`), the **Live**
  slide at 2fr with the countdown top right (`.sv-timer`, amber and red levels, idle when paused, overrun shown as a
  negative clock) and the video progress along the bottom (`.sv-media` with `current / total`, paused colour), the
  **Next** slide at 1fr, and a one-line note: `ProPresenter is not set up.`, `Waiting for ProPresenter.`, `Nothing is up.`,
  `Cannot reach Relay.`. Without an image the slide text is shown instead (`.sv-text`). Polls once a second, clocks tick
  every 250 ms from the reading's age. Stacks to one column under 720px.
- **Hooks**: `RelaySlideView.create(root, {base, active})`, `.sv .sv-head .sv-title .sv-part .sv-count .sv-current
  .sv-next .sv-label .sv-frame(.sv-img) .sv-text .sv-timer(.amber .red .idle) .sv-media(.paused) .bar > i .sv-note`,
  `--sv-part` (part colour from ProPresenter), and `hidden` on the timer, media, part and next elements. Everything from
  the server is text or an image URL under `base`; never markup. The same widget is mounted by the admin page
  (`live.html`, `#svRoot`), so changes here change that page.
- **Mockup change**: tokens for the page, labels, pill and level colours (`--ok`, `--warn`, `--err`); the slide picture
  keeps a dark frame because it stands for licensed slide imagery. Page padding is larger only to clear the chip.
- **Real implementation**: replace the hard-coded greys and `#4f8cff` progress colour in `slideview.css` with `--muted`,
  `--border`, `--primary`; keep `var(--dim, ...)` fallbacks only if the stylesheet must work without the viewer tokens.
  Note `stage.html` links `viewer.css`, so it inherits the viewer token block; keep that or add the shared tokens.

## 9. `s-signage-player`: /signage/{slug}

- **Files**: `app/modules/signage/templates/player.html`, `static/player.js` (`SignagePlayer`); served on the signage
  port only. Endpoints: `/signage/_/state|events|now|done/<slug>`. Used also by the admin preview (`preview: true`).
- **Who/device**: a lobby TV, a smart-TV or kiosk browser, always black. No operator at the screen.
- **On it**: nothing but the playlist: image items (fit or fill) and video items, shown with an 800 ms crossfade between two
  layers so there is never a blank frame; items outside their dates are skipped; with nothing playable the screen is
  black. Takeovers cover the playlist (it resumes where it stopped): a pushed file (image or video, once or until a time),
  a framed live view of Relay (live captions, the service rundown), and an alert banner along the bottom (`role="alert"`,
  with `(+N more)`). Demo chip: Playlist, Pushed file, Live captions view, Service rundown view, Nothing scheduled, and
  Alert banner on or off. A click or key press goes fullscreen; the cursor is hidden.
- **Hooks**: `#stage` container, `.sg-layer`, `role="alert"` banner, the `iframe[sandbox="allow-scripts allow-same-origin"]`
  for `live` takeovers, `nowUrl`/`doneUrl` options.
- **Mockup change**: none to behaviour. Slide artwork is a stand-in (gradients from the primary, warning and error tokens
  mixed with black). The banner uses `--err` instead of `#c62828`.
- **Real implementation**: the player stays token-free for the playlist (it is operator media on a black TV). Only the
  alert banner colour should come from `--err`/`--bg` so presets do not make it unreadable; the player has no stylesheet
  today, so inject a two-variable `<style>` in `player.html`.

## 10. `s-lighting-screen`: /screen?lighting=\<page\>

- **Files**: `app/modules/lighting/templates/screen.html`, `static/screen.js`, `static/screen.css`,
  `static/lighting-buttons.js` (`LightingButtons.draw`, shared with the admin preview); routes `/lighting/_/press/<page>`
  and `/lighting/_/state/<page>`; the key `?key=` is a short code.
- **Who/device**: a tablet in the booth or on the stage; the lighting volunteer.
- **On it**: only a page of cue buttons, no header. Grid layout (`.ltgrid`, `grid-column: span w`, rows of 110px) or free
  layout (`.ltfree`, absolute `%` positions). Each cue has its own colour, text colour and shape (`data-shape`: rect,
  rounded, pill, circle). A tap flashes the button (`.sent`, 250 ms) and fires the cue once; cues the lighting program
  reports as active are outlined (`.lit`, state polled every 3 s). Errors appear as a toast for 4 s: `The lighting program
  did not answer.`, `That button does not work.`, `Too many taps. Wait a moment.`, `That did not work.`.
- **Hooks**: `#ltScreen[data-page]`, `.ltgrid`, `.ltfree`, `.ltcue` (+ `.sent .lit`, `data-shape`), `#ltToast`, the
  inline `#ltData` JSON (`{page, key}`), `window.LightingButtons`.
- **Mockup change**: page background, outline and toast from tokens (`--bg`, `--fg`, `--err`); cue colours are the
  operator's data in the product. In the mockup they are token mixes so every preset looks right; the real values stay
  hex strings from config. The admin builder should offer a palette, not a free hex field, if cues are to survive presets.
- **Real implementation**: replace `#111` and `#fff` in `screen.html`/`screen.css` with `--bg`/`--fg` (load the viewer
  token block or the shared tokens); the toast moves from `#7a1f1f` to `--err`.

## 11. `s-obs-screen`: /screen?obs=\<computer\>

- **Files**: `app/modules/obs/templates/screen.html`, `static/screen.js`, `static/screen.css`; routes `/obs/_/scene/<id>`
  and `/obs/_/state/<id>`.
- **Who/device**: a tablet at the production desk; the volunteer who switches scenes.
- **On it**: a header with the computer name (`#obsName`, also the page title `<name>: scenes`) and three badges: `LIVE`
  (`#obsLive`), `REC` (`#obsRec`), `OBS not connected` (`#obsOff`); one big button per scene (`.obsscene`, 110px rows,
  `data-scene`), the program scene red and outlined (`.program`). A tap switches scenes, polled every 1.5 s. Errors as a
  toast for 4 s: `OBS did not answer.`, `That scene is not in OBS any more.`, `Too many taps. Wait a moment.`, `That did
  not work.`. When OBS is not connected the scenes dim in the mockup (the real page keeps them tappable; the server
  answers `unreachable`).
- **Hooks**: `#obsHead`, `#obsName`, `#obsLive`, `#obsRec`, `#obsOff`, `#obsScenes`, `.obsscene` (+ `.program .sent`),
  `#obsToast`, `#obsData` JSON.
- **Mockup change**: tokens for background, scene buttons (a primary tint over `--card`), program (`--err`), badges. The
  dimming when disconnected is an addition; decide before building (it is cheap, and clearer than a silent failure).
- **Real implementation**: replace the hard-coded `#111`, `#2b3a55`, `#c62828`, `#8e24aa`, `#555` in `screen.css`.

## What the shell lacked

- A way to register standalone screens with no sidebar worked, but there is no per-screen way to set the document
  `<html data-theme>`/`data-preset` from the screen itself; the mockup mutates the shell's attributes from the demo chip.
- No token for "info blue" or "output black/white" (alert info, overlay, signage). They stay literal and are listed above.
- No shared component for a floating demo chip; this file defines its own (`.dvchip`).
