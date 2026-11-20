# NDI router and page outputs

Both live on the **NDI** admin page and need the NDI runtime (see the NDI page) switched on.

## Router: point an output at any source

A **routed output** is an NDI source called `Relay - <name>`. Set a TV, decoder or switcher input to receive it once, then
choose what it shows from the Router section. The NDI library does the switching inside itself, so nothing is decoded or
re-encoded, and the picture follows within a moment.

- **Add output**, then pick a source in its row. "Nothing" clears it.
- **Presets** save every output's current route; **Take** applies them in one click. **Update to current** replaces a
  preset with what is routed now.
- Routes and presets are saved and come back after Relay restarts.
- Sources include Relay's own page outputs (below), so one TV can switch between a camera, the signage loop and the
  captions screen.
- Hardware decoders such as the Magewell Pro Convert announce nothing and keep their web API behind a login. Point them
  at a routed output once instead of driving them.

## Page outputs: send Relay pages as NDI

Each **screen** (every language, plus the source language) and each **overlay** (the default and every saved one) can be
sent as an NDI source called `Relay - <name>`. Overlays keep their transparency, so a lower third keys over video with
key and fill in one source.

Pages are drawn in a hidden Chromium browser, which needs Playwright: `pip install playwright`, then
`playwright install chromium`. The page says so when it is missing. Each output shows its frame rate and the share of one
core Relay spends on it; switch on only what you use (at most eight). Pick 1280 x 720 unless a switcher needs 1080.

Signage adds its screens as `ndi.outputs.signage` (`modules.provide`); public boards are listed with the screens and overlays. See `docs/modules.md`.

## Not covered yet

- Audio on page outputs, signage, boards and multiview outputs.
- Controlling routes from slide notes (`[relay:ndi=...]`), an HTTP API for Companion, and a separate permission for who may
  route (today it is `ndi.manage`).
- Reading where a BirdDog converter points, and setting a Magewell decoder. Each is its own integration and so its own module.

## Test patterns (#302)

Going out → **Test patterns** sends built-in pictures as NDI sources named `Relay - Test <name>`: colour bars, grid and
crosshair with safe areas, greyscale ramp, solid colours, a moving bar (time-driven, so dropped frames show as jumps),
an alpha checker, or your own PNG (kept with its transparency). The name, size and a running clock can be written on the
picture. Sound is a 1 kHz tone at a chosen dBFS, a left-then-right identification tone, or silence. Size (up to 4K) and
frame rate are set per source; at most 4 run at once. They are drawn with numpy and Pillow (no browser) in
`app/modules/ndi/testpatterns.py`, and, like page outputs, appear as inputs in the router.

Proved against the real NDI library on macOS (video plus stereo audio sent through cyndilib); not yet looked at on a
separate receiver machine.
