# Standardization specification

The contract for how Relay's pages draw controls and text. Implemented by #463: `app/static/tokens.css` holds the
values, `app/static/components.css` holds the one definition of each control, `window.relayUI` builds them from
scripts, and two tests keep pages from drifting (`tests/test_style_contract.py`, a ratchet on literal sizes and colours;
`tests/test_browser.py`, the drawn sizes on every operator page in every preset). The rest of `docs/ui-audit/` is a
local audit and is not committed.

## Canonical foundations

- Theme semantics are grounded in `tokens.css`: `--bg`, `--fg`, `--muted`, `--border`, `--primary`, `--hover`, `--hover-fg`, plus `--ok`, `--warn`, `--err`, `--info`. Maintain light/dark and named presets.
- Extend a documented scale for spacing, radii, typography, focus ring, elevation, control heights, motion and z-index. Keep specialized viewer/display palettes where a different high-contrast context justifies it.
- Native HTML controls remain the default. Shared styles should not erase native keyboard, form, or browser behavior.

## Component contracts

| Category | Canonical audit references | Standard |
|---|---|---|
| Buttons | **BTN-031** | Primary/secondary/destructive/quiet variants; consistent minimum height and focus ring; explicit disabled/busy states; avoid color-only status. Confirm actions for consequential controls. |
| Text/number inputs and selects | **INPUT-029**, **DROP-016** | Shared height, padding, border, radius, label/help/error slots; visible keyboard focus; associate label and described error/help. Preserve native select unless a compelling tested need exists. |
| Checkboxes/toggles | **CHECK-013** | Native input or accessible switch with keyboard operation, clear checked/disabled state, label hit area and non-color cue. |
| Notifications | **ALERT-014** | One visual severity vocabulary and live-region behavior; distinguish transient toast, persistent page notice and validation error. |
| Modals | **MODAL-001** | Use native dialog or verified accessible equivalent; name/description, focus entry/return, Escape policy and contained mobile layout. |
| Cards/sections | **CARD-027**, **LAYOUT-043** | Semantic heading hierarchy, consistent surface/padding/border; avoid nesting cards without clear grouping. |
| Tables | **TABLE-011** | Shared header/row/empty/loading/error styles; responsive overflow or purposeful card transformation; accessible caption and headers. |
| Navigation/tabs | **NAV-001**, **TAB-003** | Consistent current/expanded state, keyboard model, semantics, focus visibility and compact-screen behavior. |
| Typography/spacing | **LAYOUT-043** | Semantic type and spacing scales; avoid page-specific one-off values for recurring patterns. |

## Accessibility baseline

WCAG 2.2 AA is the proposed target: keyboard operation, clear focus, accessible names, labels, status announcements, sufficient contrast, reduced motion, target sizing appropriate to touch, and no reliance on color alone. Validate with actual keyboard and screen-reader testing; static ARIA presence alone does not establish conformance.

## Scales (tokens.css)

| Scale | Tokens |
|---|---|
| Type | `--fs-xs` 11, `--fs-sm` 12, `--fs-md` 13, `--fs-base` 14 (body text uses only these four); `--fs-lg` 15, `--fs-xl` 18, `--fs-2xl` 22, `--fs-3xl` 24 for headings and figures |
| Fonts | `--font` (the one sans stack), `--mono` (the one mono stack) |
| Control heights | `--ctl-h` 34 (regular), `--ctl-h-sm` 28 (`.sm`), `--ctl-h-icon` 32 (icon buttons, menu rows) |
| Radii | `--r-sm`, `--r-ctl`, `--r` (the preset's), `--r-lg`, `--r-pill`; Brutalist's `--r: 0` squares them all |
| Spacing | `--sp-1` 4 to `--sp-5` 24 |
| Colour | the preset's `--bg --fg --muted --card --border --primary --hover`, status `--ok --warn --err --info`, `--danger`/`--danger-hover`/`--danger-fg`, `--inset`, `--scrim`, `--video-bg`, `--qr-bg`, `--knob`, `--focus-ring` |

The primary button is the preset's `--primary` ringed in `--fg` (Justin, #463, option B of three shown). The 14% tint
(`--hover`) means a live or pressed control, never a primary action.

## Exceptions

These keep their own sizes because they draw something other than the interface: the operator's chosen look, a
display read from across a room, or a touch target held in one hand during a service. They still use the colour and
status tokens where they load `tokens.css`, and the ratchet stops them gaining literal values.

| What | Files | Why |
|---|---|---|
| Viewer pages (captions, translation, status, present) | `viewer.css`, `viewer.html`, `index.html` | Read at a distance on phones and screens; sizes scale with the viewer's A+/A- choice |
| Overlay | `viewer.css` (`body.ov-*`), overlay preset thumbnails and stage in `admin.css` (`.ovpreset`, `.ovstage`) | Draws the operator's chosen caption look and fonts over program video |
| Rundown | `rundown.css` | A kiosk view of the service, drawn to scale |
| Signage | `modules/signage/static/player.js`, `player.html` | Full-screen slides placed per item |
| Boards and cue canvases | `layout.css`, `board.html`, `modules/lighting/templates/screen.html`, `.cuebox` | Each button is the operator's own label, shape and colours |
| Tally, stage and slide view | `tally.html`, `modules/propresenter/static/slideview.css`, `stage.html`, `#svRoot` | Displays for the stage and camera operators |
| Screens for OBS and NDI | `modules/obs/static/screen.css`, `modules/ndi/templates/*.html` | Full-screen scene and video views |
| ProPresenter control | `.ppcue` slide cards, `button.big` transport | Hit quickly during a live service |
| Crew phone pages | `.cr-tabs`, `.cr-confirm`, `.cr-alerts`, the intercom tab (`#ic .tog`, `#icTalk`), `crew/templates/join.html`, `offline.html` | One-handed touch targets on a phone |
| Pages that cannot load `tokens.css` | `crew/templates/join.html`, `offline.html`, `intercom_remote/static/phone.html`, `parent_paging/templates/room.html` | Served on their own listener, through the remote tunnel, or offline; they keep their own font stack |
| The viewer pages' font stack | `viewer.css`, `viewer.html` | `viewer.html` must not load `tokens.css`: its `color-scheme` paints the overlay's transparent page dark in a browser source |
| Shell controls with a fixed size | `.user` (48px account row), `.nav` rows, `.search`, `.ib`, `.qc`, `.tg`, `.sel`, `.mi` | Part of the shell's own layout |

`tests/_style_probe.py` lists the selectors the browser check leaves out, and `tests/test_style_contract.py` the files
allowed a font stack or script-set styles.

## Canonical implementation selection

The IDs in this specification designate the core admin shell groups in `app/templates/admin.html` as the first shared visual baseline. They are not imposed on full-screen viewers or specialist module displays. `INPUT-030` is the shell textarea group; `FORM-006` is the shell form group. Compare module alternatives listed by their exact IDs in `duplicate-components.md` and `component-registry.csv` before consolidation. `UI-001` through `UI-054` identify dynamic source-file groups, not canonical reusable widgets.
