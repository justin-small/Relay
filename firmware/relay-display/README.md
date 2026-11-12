# Relay display: firmware for the CrowPanel 7" caption panel

Firmware for the **Elecrow CrowPanel Advance 7.0" (DIS02170A), hardware V1.2 and later**:
ESP32-S3-WROOM-1-N16R8 (16 MB flash, 8 MB octal PSRAM), an 800 x 480 RGB panel, a GT911 touch
controller and LVGL 9.2. It is a touch-screen caption panel for Relay: a small screen on a wall or a
desk that shows the live transcription, or one translation, from a Relay server on the same network.

**What it does.** It boots, lights the panel, joins Wi-Fi (from a setup page on the panel's own network),
keeps its settings, finds Relay by mDNS, and shows the finished lines of up to two caption feeds (200 lines
each) with a touch menu to choose the feed. It checks in with Relay, takes a feed set from Relay admin, and
installs signed firmware updates. Latin-script languages only, because the one caption font is Latin: other
languages are hidden from the menu and refused by Relay (see "Feed picker").

The operator's view of all this is in the Relay user guide (`docs/guide/src/51-panels.md`); the server side is
in the root README ("Caption panels").

## What is here

| | |
|---|---|
| Boards | The CrowPanel, and groundwork for a second (see "Boards"): a board is a header, an sdkconfig file and a panel driver |
| Panel | RGB driver and timings, the STC8 backlight (revision as a build setting), software 180-degree turn |
| Settings | One NVS blob: Wi-Fi network and password, address mode (DHCP or static), Relay address override, panel ID, saved feed, rotation |
| Panel ID | 32 random hex characters, made on first boot, kept until a factory reset. Names the panel to Relay |
| Wi-Fi | Station with DHCP or a static address, with backoff when the network is down |
| Setup | An access point and a page to join Wi-Fi (join code as a QR on the panel), with recovery |
| BOOT button | Hold 5 s: open the setup network. Hold 15 s: factory reset |
| Screen | LVGL: centred text with an optional QR code, the caption view (see "Caption view"), and the colour-bar bench picture |
| Feed picker | A menu button on the caption screen: Transcription or a live translation, the Service, the Alert buttons, or ProPresenter's lyrics or timers (see "ProPresenter"), one feed or two side by side, saved in NVS (see "Feed picker") |
| Panel link | Checks in with Relay every 30 s and keeps an event stream open; applies the feed the operator sets there (see "Panel registry link") |
| Captions | Up to two feeds from Relay's `/stream?finals=1`, each with its own connection and 200-line buffer in PSRAM, resumed with `?last_id=` after a drop (see "Caption client") |
| Partitions | Two app slots, `otadata`, `nvs` |
| Firmware update | Fetches a signed image Relay offers into the spare slot, refuses it on the panel unless the signature holds, restarts at a quiet moment, and rolls back an image that does not check in (see "Firmware update") |
| `build.sh` | Release, `--dev` and `--qemu` builds, `--sign`, the native checks and the image check |
| Tests | Native checks (g++), and an emulator suite under `qemu-system-xtensa` |

Not here: VXP pairing and pinned TLS (Relay panels use plain HTTP on the LAN), the room screen, the
RTC, audio, the SD card, Bluetooth.

**The released image belongs to no installation.** No Wi-Fi network, address or panel ID is baked in:
they are written to the panel's flash by its own setup page. `tools/image_check.py` reads the bytes of
the images `build.sh` produces and fails the build if one carries a host name, a UUID, a PEM block or a
key-shaped string.

## Boards

One firmware, and a board is the small part of it that differs. The released board is the CrowPanel; a second
is being added (#114).

| `--board` | Board | Chip | Panel | State |
|---|---|---|---|---|
| `crowpanel-adv7` (the default) | Elecrow CrowPanel Advance 7.0" (DIS02170A) | ESP32-S3 | 7.0" 800 x 480, RGB | Released. Everything below this section is about it unless it says otherwise |
| `waveshare-p4-10.1` | Waveshare ESP32-P4-WIFI6-Touch-LCD-10.1 | ESP32-P4, Wi-Fi from an ESP32-C6 | 10.1" 800 x 1280, MIPI-DSI (JD9365) | **Groundwork.** It configures; its image has not finished a compile yet. It has not been on a bench, and it is not released |

**What a board is**, and so what a new one adds:

| File | Holds |
|---|---|
| `sdkconfig.board.<name>` | The chip, the module's flash and PSRAM, and the settings that belong to that panel's interface. `build.sh --board <name>` applies it and then `sdkconfig.defaults`, which every board shares |
| `main/boards/<name>.h` | The board's facts: the picture's size, the pins, the touch controller's ID and finger count. `main/config.h` includes one, and lists what every board header has to define |
| `main/board_<kind>.cpp` | The panel and backlight driver, behind `main/board_panel.h`: `board_rgb_stc8.cpp` (RGB interface, STC8 backlight) and `board_dsi_jd9365.cpp` (MIPI-DSI, PWM backlight). `main/CMakeLists.txt` compiles the one for the build's chip |
| `main/Kconfig.projbuild`, "Board" | The choice, which follows the chip |
| `main/idf_component.yml` | Managed components only that chip needs, each behind a `target ==` rule |
| `main/test_native.cpp` | The board's own checks (its size, pins and touch ID). `build.sh` runs the native checks once for every board, whichever is being built |

Everything else is shared and does not know the board: LVGL and the screens, the caption and service views
(sized from `kLcdWidth` and `kLcdHeight`), touch (the GT9271 speaks the GT911's registers), the I2C bus,
settings, Wi-Fi and the setup page (`esp_wifi_remote` gives the ESP32-P4 the same `esp_wifi` calls), the
caption client, the panel link and the firmware update. `display.cpp` paints strips into whichever `esp_lcd`
panel the board's driver hands back.

### The Waveshare ESP32-P4 10.1": what is left

The values are Waveshare's, from their board support package (`waveshare/esp32_p4_wifi6_touch_lcd_x` 2.0.3) and
repository (github.com/waveshareteam/ESP32-P4-WIFI6-Touch-LCD-X). The package itself is not used: it needs ESP-IDF
5.5 and brings its own LVGL port, and this firmware is pinned to 5.4.4 with one of its own. The JD9365's start-up
table is copied from it (`main/boards/waveshare_p4_10_1_init.c`, Apache-2.0).

To confirm on the first board:

- [ ] The chip's silicon revision. The build is for revision 3.x, which Waveshare ship now; a revision 1.3 board
      needs the other two lines (`sdkconfig.board.waveshare-p4-10.1`), and the image for one does not start on the other
- [ ] The panel lights and shows the colour bars (the DSI pad supply, the reset on GPIO27, the start-up table)
- [ ] The backlight is dark through the boot and on after it (GPIO26, high for on)
- [ ] The touch controller answers as a GT9271 at 0x5D or 0x14, and a touch lands where the finger is
- [ ] BOOT is GPIO35 (the chip's strapping pin; Waveshare's package does not name the button)
- [ ] The boot line reports `psram 33554432 bytes`
- [ ] Wi-Fi: the ESP32-C6's factory firmware speaks to the pinned `esp_hosted` 1.4.7, as a station and as the
      setup network's access point (Waveshare's own example builds the station only)
- [ ] A signed update installs and a bad one rolls back, as on the CrowPanel

Not written yet:

- **Landscape.** The glass is portrait, 800 x 1280, and that is the picture drawn now. A wall panel wants
  1280 x 800, and a DSI panel has no quarter turn: it goes in `display.cpp` beside the half turn (and in
  `touch_format.h`), with 90 and 270 as rotation settings. The views then need a pass at that size: they are
  sized from the panel's constants and were laid out for 800 x 480
- **Relay.** `app/panelfw.py` accepts one image, for the ESP32-S3. A second board means the panel names its
  board at check-in and Relay keeps and offers an image for each
- **A finished compile.** `build.sh --board waveshare-p4-10.1` configures and builds ESP-IDF's own components;
  this firmware's sources, the DSI driver among them, have not been through the ESP32-P4 compiler yet
- **Release and CI.** The release builds and publishes the CrowPanel's two files, and CI builds only the
  CrowPanel
- **Emulator.** The suite runs the CrowPanel's build. It covers the shared code, which is nearly all of it,
  and nothing of this board's driver

## The board

The CrowPanel Advance 7.0".

| | |
|---|---|
| Module | ESP32-S3-WROOM-1-N16R8 |
| Display | 7.0" 800 x 480 RGB565 (16 data lines), 16 MHz pixel clock |
| Backlight | Switched by an STC8H1K28 microcontroller on the I2C bus, at address 0x30 |
| Touch | GT911 on the same I2C bus (0x5D, or 0x14 if the board straps it that way; `touch.cpp` tries both), read into LVGL as its pointer. Coordinates follow the panel rotation. The emulator takes touches from the console: `touch <x> <y>`, `touch up` |

**Revision.** V1.2 and V1.3-or-later send the STC8 different bytes for backlight on and off, and the
firmware cannot read the revision back. Build for the board's revision (`Board revision` in
`idf.py menuconfig`, under "Relay display"; the default is V1.3 and later, which Elecrow sells now).
A build for the wrong revision is wrong without a sign: check the backlight on the bench.

### Pin map and timing

Elecrow's values for this board, from `main/boards/crowpanel_adv7.h`.

| Signal | GPIO |
|---|---|
| PCLK / HSYNC / VSYNC / DE | 39 / 40 / 41 / 42 |
| B0..B4 | 21, 47, 48, 45, 38 |
| G0..G5 | 9, 10, 11, 12, 13, 14 |
| R0..R4 | 7, 17, 18, 3, 46 |
| I2C SDA / SCL | 15 / 16 (400 kHz) |
| BOOT button (active low) | 0 |

Sync pulse / back porch / front porch: 4 / 8 / 8 pixels horizontally and 4 / 8 / 8 lines vertically; 820 x 500
clocks a frame. The RGB driver reads its frame from PSRAM through a 10-line bounce buffer in internal RAM,
so a late PSRAM read costs a strip and not the whole picture (`RELAY_BOUNCE_LINES`).

## Build

ESP-IDF **5.4.4**, with LVGL 9.2.2 from the component registry (pinned in `main/idf_component.yml`).
The same container CI uses has everything:

```bash
docker run --rm -v "$PWD":/p -w /p/firmware/relay-display \
  espressif/idf:v5.4.4@sha256:9ce51d4ace496845678065dc86f8e55fb9c25fc9f01f45597042afa7ed546926 \
  bash -c '. $IDF_PATH/export.sh && ./build.sh'
```

or, with ESP-IDF installed, `. $IDF_PATH/export.sh` and then:

```bash
firmware/relay-display/build.sh            # release, into build/
firmware/relay-display/build.sh --dev      # bench image with diagnostics, into build-dev/
firmware/relay-display/build.sh --qemu     # emulator image and seeds, into build-qemu/
firmware/relay-display/build.sh --sign key.pem   # release, signed (see below)
firmware/relay-display/build.sh --board waveshare-p4-10.1   # another board, into build-waveshare-p4-10.1/ (see "Boards")
```

Every build runs the native checks first (`main/test_native.cpp`, a second of g++), then compiles, then
for a release build checks the image for installation-specific values. The merged image is
`relay-display.bin`; `relay-display-app.bin` is the application alone, the file an update carries (a release attaches it, signed).

**Signing.** An unsigned release is a good image, and no panel will accept it as an update. `--sign`
signs the application with a Secure Boot V2 key (RSA-3072, `espsecure.py generate_signing_key --version 2
--scheme rsa3072`) after the build, so the private key never has to be in the build. `--dev` and `--qemu`
sign with a throwaway key in their build directory, because a running application with no signature
aborts at start.

## Flash

The merged image goes in at 0x0 in one write:

```bash
python -m esptool --chip esp32s3 -b 460800 write_flash --flash_mode dio --flash_size keep \
  --flash_freq 80m 0x0 firmware/relay-display/build/relay-display.bin
```

Or from the browser: the operator panel's **Flash a panel** page (`/admin/flash`, Chrome or Edge) writes
the same file at 0x0 over Web Serial, after checking it and the board.

Hold BOOT while plugging in USB if the board does not enter the bootloader by itself. `nvs`
sits above the application slots, so flashing a new image does not erase the panel's settings.

## Set up and recover

1. A panel with no stored Wi-Fi opens its setup network. The panel shows the network name
   (`Relay-Panel-XXXX`), a one-time password and a QR code that joins it.
2. Join it from a phone, open `http://192.168.4.1/`, and give it the Wi-Fi network and password. The
   address mode, and the **Relay address** (optional: leave it empty and the panel finds Relay itself),
   are on the same page.
3. The panel tries the network. On success it saves the settings, shows the address it got, and closes
   the setup network a minute later.

**Recovery.** If the network changes or the settings are wrong:

* **Hold BOOT for 5 seconds**, or type `recover` on the serial console (115200 baud), to open the setup
  network again. A configured panel keeps its stored network until a new one works, and closes the setup
  network after 15 idle minutes.
* **Hold BOOT for 15 seconds** to factory reset: every stored value is erased, the panel ID with it, and
  the panel restarts blank. It is a new panel to Relay afterwards.
* `id` on the serial console prints the panel ID.

## Caption client

`main/captions.cpp` holds up to two feeds (slot 0, and slot 1 for the split view). Each is a task with one
plain-HTTP connection to `GET /stream?stream=...&lang=...&finals=1` on Relay's viewer port and a buffer of
the last **200 finished lines**, oldest dropped first, in PSRAM. The rules are in `main/caption_format.h`,
which has no ESP-IDF in it and is what the native checks test.

* **What is read.** Finished lines (`final: true`, with `id:` = seq), snapshots, `targets` and `: ping`.
  Partial lines and anything else are ignored. Chunked and plain bodies, and CRLF, LF or CR line ends.
* **Resume.** After a drop the feed reconnects with `?last_id=<highest seq it holds>`, so Relay sends only
  the lines it missed. A seq it already holds is dropped. Retries back off from 1 s to 30 s, plus up to a
  quarter at random. A stream with nothing on it, not even a ping, for 45 s is reopened
  (`RELAY_CAPTION_SILENCE_S`; Relay pings every 15 s).
* **Snapshots replace the buffer**, as the browser viewer does: on a fresh connection, on a resume older
  than Relay's history, on a reset (Start), which is a snapshot with no lines and empties the feed, and on
  a resume from before a reset or from a Relay that has since restarted (`Hub._resume_frames`), so a reset
  the panel was away for still clears it.
  **Set Relay's `history_lines` to 200** (it allows up to 500) so a rebooted panel refills all 200 lines;
  at the default 40 it gets 40.
* **State for the UI** (`captions_state`): off, waiting for network, connecting, connected, reconnecting,
  relay not running (the connection was refused, or Caddy answered 502-504), feed refused (Relay answered
  4xx, such as a language it does not offer). While retries keep failing the last state stays on screen.
* **Text.** Lines are stored folded to what a Latin font draws: ASCII, U+00A1-U+024F, U+1E00-U+1EFB and €
  stay; typographic quotes, dashes and the ellipsis become plain ones; zero-width characters and combining
  marks go; every other run (emoji, other scripts, broken UTF-8) becomes one `?`, so text never vanishes
  without a sign. A line is cut at 1024 bytes with `...`. **LVGL's built-in Montserrat covers ASCII only**,
  so the caption view has a font of its own (below).
* **Where the feed comes from.** At boot, slot 0 shows the stored Relay address and feed when both are
  set, or Relay discovery finds it (below). On the serial console, for the bench:
  `feed <slot> <url> translation <LANG>`, `feed <slot> <url> transcription`, `feed <slot> off` (not saved),
  and `captions <slot>` prints the state and every line held.

### Relay discovery

With **no Relay address stored**, `main/discovery.cpp` waits for an address, then browses mDNS for
`_relay-captions._tcp` (what Relay announces is in the top-level README, "Caption panels: Relay announces
itself") and calls `feeds_use_relay` with the first answer. The rules are in `main/discovery_format.h`,
checked on a laptop:

* **A typed address wins.** Discovery is off whenever the setup page's Relay address is filled in.
* **The first Relay is kept.** Its install name (the TXT `name`, folded to printable ASCII) is saved in its
  own NVS key, `relay_name`. From then on only that name is taken, so with two Relays on one network the
  panel does not move to the other when the first is off. The factory reset clears it.
* **The address is not saved.** It is read again every 30 s, and a Relay that comes back at another address is
  followed. While none answers it browses every 5 s.
* Uses `espressif/mdns` (`main/idf_component.yml`). The native test covers the rules; on a CrowPanel against a
  Mac announcing with `dns-sd` it finds Relay a few seconds after the panel has an address, keeps the first one
  with a second on offer, and follows it to a new address and back within one 30 s refresh.

### Panel registry link

`main/panel_link.cpp` keeps the panel in Relay's registry (top-level README, "Caption panels: the panel registry"). Two
tasks, both on the address the caption feeds use (a typed one, or what discovery found), both quiet until the panel has
one:

* **Check-in**, `POST /api/panels/checkin` every 30 s (8 s while Relay is not taking it, with up to 2 s of jitter): the
  panel ID, the name `Panel xxxx` (the operator renames it in Relay), the firmware version, `scripts: ["latin"]`, what
  each pane shows now, its IP, uptime, Wi-Fi signal (absent on the emulator) and `feed_rev`.
* **Event stream**, `GET /api/panels/<id>/events`, opened once Relay has taken a check-in and reopened with the feed
  client's backoff (1 s to 30 s; a silent stream is reopened after `RELAY_CAPTION_SILENCE_S`). Relay answers 404 for a
  panel it does not know (it restarted), and the panel then checks in again at once.

A `set_feed` from either (one or two feeds) goes to `feeds_apply`: saved in NVS and shown at once, **newest change wins**
against a choice at the screen. The panel applies a revision once, whichever way it arrives, and reports it as
`feed_rev`, which is what stops Relay sending it again. An `update_available` (and the `update` a check-in reply carries) is
handed to the firmware update (see "Firmware update"). The console's `panel` logs the link's state.

The rules (the body, the two requests, the messages, when to apply) are in `main/panel_link_format.h`, checked on a
laptop; `tests/test_panel_link.py` runs the whole link against `fake_relay.py`, which copies Relay's side by hand.

## Firmware update

Relay admin uploads a signed `relay-display-app.bin`; each panel row then shows "Update available", and the operator
updates one panel or all that are online. The panel's side is `main/firmware_update.cpp`, with the decisions in
`main/update_format.h` and `main/ota_format.h` (both checked on a laptop). Adapted from the VXP room panel's update
(`firmware_update.cpp`, `update_format.h`, `components/vxp_ota`).

1. **Relay asks.** `update_available` on the event stream, and `update` in every check-in reply while the ask stands:
   `{version, size, sha256, path, now}`. A reply with no `update` is the ask withdrawn (the image was replaced or
   removed), and a verified image waiting for its restart is dropped. Nothing is taken from the offer but a version
   (`X.Y.Z`), a size no bigger than the slot, a checksum, and a path on the Relay the panel already talks to.
2. **The panel downloads** into the spare slot over plain HTTP, 4 KB at a time, hashing as it writes. The first 112
   bytes decide before a slot is opened: an ESP-IDF application (not the merged serial image), for
   `relay-display-app`, at the version Relay named, and not older than the one running (an update only goes forward).
3. **The signature is checked on the panel**, by `esp_ota_end` against the public key this firmware was built with
   (`CONFIG_SECURE_SIGNED_ON_UPDATE_NO_SECURE_BOOT`). **Images travel over plain HTTP on the LAN, so this check is what
   keeps a stranger's image off the panel.** An unsigned or wrongly signed image is refused here, not only in Relay,
   and the slot is never made bootable: the bootloader is pointed at it only at the moment of the restart.
4. **It restarts at a quiet moment**: no new caption line for `CONFIG_RELAY_UPDATE_QUIET_S` (120 s), or at once when
   the operator chose "Update now". A power cut before that boots the image that was running.
5. **The new image must check in.** It boots on approval and has `CONFIG_RELAY_UPDATE_CONFIRM_S` (600 s) to be
   answered with a 200 by Relay; then `esp_ota_mark_app_valid_cancel_rollback`. If it is not (a crash, or no route to
   Relay), the panel restarts and the bootloader returns to the old image. The attempt is recorded in NVS (`update`)
   before the restart, so the old image knows it was a rollback and does not fetch that version again until Relay has
   been told.

The panel tells Relay what is happening with `POST /api/panels/<id>/update` (`downloading` with a progress, `ready`,
`restarting`, `failed` with an error word, and after the restart `installed` or `rolled_back`), which the admin page
shows on the panel's row. The words are in `update_format.h` and `app/panels.py`, and `tests/test_panels.py` holds the
two lists to each other. A failed update ends the ask: asking again is the operator's.

**Release signing keys stay out of the repo**, as with VXP. A release is signed in `release.yml` from a secret
(`PANEL_FIRMWARE_SIGNING_KEY`, a Secure Boot V2 RSA-3072 PEM); the public half is in every image built from it, so
rotating the key means panels in the field must be flashed over USB once. `--dev` and `--qemu` builds sign with a
throwaway key of their own and accept only images signed with it.

## Caption view

`main/caption_view.cpp` draws slot 0's feed: a header with the feed's name ("Transcription", or the language in
words) and a coloured dot with the connection in words (Live, Reconnecting, Relay not running...), and under it
the finished lines, newest at the bottom, each wrapped in the caption font. Only finished lines are drawn, so
nothing appears word by word (the client asks for `finals=1`).

* **It follows live.** Each new line scrolls into view. A drag back stops that and shows **Jump to live**; tapping
  it, or dragging back to the bottom, follows again. A new line never moves a view someone is reading, and when
  the oldest of the 200 lines drop out the view keeps its place.
* **A paused view returns to live by itself after 60 s with no touch** (`kCaptionAutoLiveMs`), so a wall panel
  someone scrolled and walked away from does not show old lines for good. A snapshot or reset from Relay also puts
  the view back on live: where the finger had it no longer exists.
* **The UI scale is one number:**  `RELAY_UI_SCALE_PCT` in `main/ui_scale.h` (65 now; 75 also works; 100 was the first size, a 36 px caption font), and every size is `ui_px(<size at 100%>)`. The caption font is fixed at 23 px at 65%, readable from a few metres on the 7". Following Relay's `default_font_px` is not
  done: the panel does not receive it yet, and a size that changes needs the wrapped lines measured again.
* **The font** is Montserrat Medium (SIL OFL) with DejaVu Sans (its own license, permissive) as a fallback for the
  Latin Extended letters Montserrat lacks, generated into `main/fonts/` by `tools/gen_caption_font.sh` (needs node;
  `lv_font_conv` is fetched by `npx` at a pinned version). Together they cover every character the client keeps,
  Vietnamese's stacked diacritics from the main face, and `tests/test_caption_font.py` fails if the kept set and the
  font drift apart. About 300 KB of flash.
* **LVGL allocates from the heap** (`CONFIG_LV_USE_CLIB_MALLOC`), not from its own 64 KB pool: 200 line labels do not
  fit in that.
* **Two feeds** (see "Feed picker") put two of these side by side, 398 px each with a 4 px rule between. Each pane
  follows, scrolls and jumps on its own; the header of a half-width pane shows the connection as a dot only.
* The rules (following, what to add or drop when the buffer changes, the header's words) are in
  `main/caption_view_format.h`, with native checks. `view` on the serial console logs whether the list follows, its
  line count and how far it is from the bottom; the emulator tests read that.

## Feed picker

The menu button at the top right of the caption screen opens a card over the captions: **One feed / Two feeds**,
which pane a pick is for (Left / Right, with two feeds), and the feeds on offer. A tap applies at once and the card
stays up until **Done** (or a tap outside it). The rules are in `main/feed_format.h` (native checks); `main/feeds.cpp`
keeps the choice and points the two caption slots at it; the menu and the panes are in `main/caption_view.cpp`.

* **What is offered.** Transcription, then the live translations from Relay's `targets`, **only those the panel can
  draw**: Relay marks each language with a `script`, and only `"latin"` is offered, because the caption font is Latin.
  A language with no `script`, and a transcription whose source language is not Latin, are not offered either.
* **How the panel learns what is live.** Relay's `/stream` says what is live only when it *changes*, not when a client
  connects, so a panel also asks `GET /api/targets` (`captions_fetch_targets`) from its own task: every 2.5 s until it
  has an answer, then every 10 s, and the stream's `targets` messages update it at once.
* **Saved in NVS** (schema 2: `feed_stream`/`feed_lang` for the left pane, `feed2_*` for the right, `feed_split`), by the
  feeds task and not on the tap. A schema 1 blob is still read: network, panel ID and feed are kept, and the split
  reads as off. With nothing chosen a pane shows the transcription.
* **A language that stops being live** falls back to the transcription, with an amber strip under the header ("Spanish
  is not live"), and **goes back by itself when the language returns**: the pick is never overwritten by the fall-back.
  A 4xx on a language before Relay has said what is live also counts as not live. The menu keeps listing what was
  chosen, dim, ticked and marked "(not live)".
* **Turning on two feeds** with nothing chosen for the right pane gives it the first live translation that is not the
  left's (the `/both` page's pairing), else the transcription.
* **Newest wins, from either side.** A person at the screen and Relay admin (a later change) make the same two calls,
  `feeds_choose` and `feeds_set_split`; the last one is shown and saved.
* Console: `feeds` logs what each pane has saved and shows and what is offered; `relay <url>` points the feeds at a
  Relay without saving it; `feed <slot> ...` still sets a slot by hand and takes it away from the feeds until `relay`.

Open questions from the issue, not decided here: whether the operator can **lock** a panel so the screen cannot change it,
and whether a panel **returns to the operator's choice** after a period of no touch.

## Service feed

The Planning Center service (#82), chosen from the same menu as Transcription and the languages, full screen or as one
half of the split view. The rules are in `main/service_format.h` (native checks); the drawing is `main/service_view.cpp`,
which `main/caption_view.cpp` builds in a pane whose feed is the service.

* **Offered only while the operator allows it.** Relay sends the panel's **service mode** (Off, Show only, Control only,
  Show and control, set per panel in Relay admin) in every check-in reply's `service` and as a `service_mode` event on
  its stream (`main/panel_link.cpp`). Off, the default, offers no Service feed. Relay's `targets` lists the service types
  it follows (`service`); the menu offers each one ("Service", or "Service: <name>" with several), or only the one the
  operator set for this panel. A saved Service pick that is no longer offered falls back to the transcription, with the
  amber strip, and returns by itself.
* **The feed** is a caption slot pointed at `GET /stream?stream=service&type=<id>`: the same socket, backoff and silence
  clock as the captions, with the service state in place of the 200 lines. **Each `service` message is the whole state**,
  so there is nothing to resume; a `service_tick` every 15 s keeps the silence clock happy and says when Planning Center
  last answered.
* **On the glass**: the current item in the caption font with its time left (`montserrat_26`, red with a `+` once over)
  and the next item; the order of service below, scrollable, the current item highlighted, headers as small section
  breaks, songs with their key and arrangement sequence and items with their length; a footer with over/under and who
  is controlling, or "Not live", "No service planned today", "Planning Center unreachable".
* **Timers count on the panel** from the last message, as the message stood at Relay's `server_time`: time left goes
  down, and the service only falls further behind once the current item has run past its length.
* **Following**: the list keeps the current item in view. A finger's scroll pauses it and shows "Jump to now"; a minute
  without a touch goes back, as the caption view does.
* **Notes** (only sent while Relay's "Show item notes on panels" is on) show as a file mark on the row; a tap opens them
  in a card over the glass, a tap anywhere closes it.
* **The LIVE buttons** (#83), when the panel's service mode includes control: **Previous**, **Next** and **Take
  control** (Release once Relay's own Planning Center person has it) in a bar under the order of service; in "Control
  only" they fill the pane under the current and next item. A press goes to Relay
  (`POST /api/panels/<id>/service/<action>` with the panel's control key, `main/service_control.cpp`), which presses
  it in Planning Center; the item changes on the glass when Relay's feed says so. Take control asks "Take control from
  <name>?" first when someone else has it. **A second tap while the first is unanswered is ignored**, so a double tap
  is one press. A refusal reads under the buttons for a few seconds ("Take control first", "Not allowed", "Planning
  Center unreachable"). A change of mode in admin rebuilds the view, so the buttons come and go at once.
* **Fonts.** Only the caption font draws accents, so item titles use it. The smaller text (key, sequence, footer,
  header, notes) is LVGL's Montserrat, ASCII only: an accented letter is drawn without its accent ("José" reads "Jose").
* Console: `feeds` lists a service option as `service:<id>`; `view` adds a line per service pane (items, current, next,
  the timer and footer as drawn, following or paused, and where the current row is).

## ProPresenter: lyrics and timers (#138)

Two more feeds, set like the Service and Alert feeds (`kFeedLyrics` and `kFeedTimers`, saved in the same NVS fields, set
by the operator as `{"stream":"lyrics"}` and `{"stream":"timers"}`, which need no setting of their own) and offered in
the menu from the `propresenter` object of Relay's `targets` message (`{"lyrics":true,"timers":true}`). Each is a caption
slot pointed at its own stream and holding its own state instead of caption lines. The rules, all with no LVGL in them
and checked on a laptop (`check_propresenter` in `main/test_native.cpp`), are in `main/pp_format.h`; the panes are
`main/lyrics_view.cpp` and `main/timers_view.cpp`.

* **Lyrics** (`GET /stream?stream=lyrics&for=panel`). Whole state per message, repeated at Relay's 15 s heartbeat. The
  current slide's lines are large and centred, the first words of the next slide dim under them, and the part
  ("Verse 1", "Chorus") and the song's title small along the foot. The size is the largest of three that fits
  (`lyrics_pick_tier`): Montserrat 48 and the large tier draw ASCII only, so a slide with accents is drawn in the
  caption font, which keeps them and is smaller. With no song up (`live` false), a blank slide, Relay switched off for
  panels, or Relay away, the pane is a few dim words and never the last slide's text (`lyrics_idle_text`).
* **Timers** (`GET /stream?stream=pptimers&for=panel&panel=<this panel's id>`). Each timer is a row: name and state,
  and its time in the largest digit font that fits (140 px, 80 px, or Montserrat 48; `pp_digit_pick`). The panel names
  itself, so Relay can send this panel's own timers and thresholds (set per panel in admin, or Timers on panels for all).
  **The panel counts the time**: a message gives each timer's seconds as of `server_time` and the panel counts from
  arrival, down for a countdown and up for an elapsed timer, so it ticks with no request a second. The colour is worked
  out from the counted time: white while running, amber from `amber_s` seconds left, red from `red_s`, red with a minus
  once past zero; a stopped timer is grey. After 40 s with no message (Relay repeats every 15 s) the digits go grey and
  say "not updating". The digits are generated (`tools/gen_digits_font.sh`: 0-9, the colon and the minus sign only) and
  sit in fixed-width cells, one label each, because the font's digits are proportional and a ticking "1" must not move its
  neighbours.
* **A still screen is not redrawn.** Relay's heartbeat repeats the state, and its `rev` moves whenever anything in
  ProPresenter does (a running timer included), so neither says the screen changed: a message that says what the glass
  already says is `same` and bumps no revision (`lyrics_apply`, `timers_apply`), and the views set a label, a colour or
  a visibility only when it differs (`main/pp_view_util.h`). A timers pane redraws only the digit that changed.
* **Stacks.** `lyrics_state` is about 700 bytes and `timers_state` about 500: both are allocated from PSRAM with the
  slot's other states and never built on a task's stack. The menu is built in place (`feed_options_add_*`): three by-value
  `feed_options` (1.5 KB each) overflowed the `feeds` task's 6 KB stack, which the emulator suite found.
* Console: `feeds` lists `lyrics` and `timers` among the options; `view` adds a line per ProPresenter pane
  (`pane 1 lyrics, a slide up` or `idle`; `pane 1 timers, 2 shown`). The emulator suite is
  `tests/test_propresenter_view.py`, against `tests/fake_relay.py`'s copy of the wire format.

## Tests

```bash
firmware/relay-display/build.sh                       # runs the native checks first
firmware/relay-display/build.sh --qemu                # emulator image and seeds
RELAY_QEMU=/path/to/qemu-system-xtensa pytest firmware/relay-display/tests --firmware
```

`pytest` skips, rather than fails, when there is no emulator or no build; `--firmware` turns the skip into
a failure, for a run that must not be skipped. **CI does not run this suite**: it runs locally, and CI only compiles the
`--qemu` build. It needs **Espressif's fork** of QEMU (the esp32s3 machine is not in
upstream or in distribution packages): the container above has one at
`/opt/esp/tools/qemu-xtensa/.../qemu-system-xtensa`, so the whole suite can run inside it:

```bash
docker run --rm -v "$PWD":/p -w /p/firmware/relay-display \
  espressif/idf:v5.4.4@sha256:9ce51d4ace496845678065dc86f8e55fb9c25fc9f01f45597042afa7ed546926 \
  bash -c '. $IDF_PATH/export.sh && pip install -q pytest && ./build.sh --qemu \
    && RELAY_QEMU=$(which qemu-system-xtensa) python -m pytest tests --firmware'
```

The emulator suite boots the image and reads its console and framebuffer. It proves the boot order, the
stored configuration (read back, refused when wrong, defaults when erased), the panel ID (made once,
kept across a reset, different per panel), the colour bars and the software turn, and that the setup
screen draws. It does **not** reach what the emulator has no model of: the RGB interface's timing, the
backlight, Wi-Fi, the touch panel, PSRAM timing. Those are the bench checks below.

`tests/test_captions.py` runs the caption client against `tests/fake_relay.py`, a stand-in for Relay's
stream on the host (the emulator reaches it at 10.0.2.2): a fresh feed fills from the snapshot, lines arrive
as committed, a dropped stream resumes with only the missed lines and no duplicates, the buffer keeps the
newest 200, a 300-line history refills 200, a reset clears (also one made while the panel was away), a silent stream is reopened, a refused
connection reads "relay not running" until Relay starts, a refused language reads "feed refused", two feeds
keep apart, and unsupported characters show as `?`. The fake copies Relay's wire format and resume rule by
hand; `app/hub.py`'s own tests own the server side.

`tests/test_caption_view.py` runs the caption view against the fake Relay with a finger injected from the console:
lines arrive and the view follows, a drag back pauses and shows the button, new lines do not move a paused view,
Jump to live and a drag to the bottom each resume, a full 200-line buffer keeps following and keeps a paused view
paused, the header shows the feed and a green dot, and Vietnamese and accented text draws. The emulator draws a
frame a second or so and reads the finger once per frame, so the tests wait for each press to be seen and hold the
finger still before lifting.

`tests/test_service_view.py` runs the Service feed against the fake Relay (which copies `app/servicefeed.py`'s wire
format): it is offered only while the operator's service mode allows it; chosen from the menu it shows the current and
next item and the footer; the current item moves when Relay says so; the timer runs over and the service falls behind;
Planning Center going away and coming back reads in the footer; nobody controlling reads "Not live"; and a service
type Relay stops following falls back to the transcription and returns; and a finger held on a row with notes for a
second (past LVGL's long-press time) opens them. The notes card's own tap-to-close is covered there too. Whether a real
finger on a row scrolls or opens is a bench item.

`tests/test_service_buttons.py` runs the LIVE buttons against the fake Relay: Next before control reads "Take control
first"; Take control asks first and then shows Release; Next and Previous move the service; Release hands it back; a
double tap is one press; a key Relay no longer knows reads "Not allowed"; and turning control off in admin takes the
buttons away.

`tests/test_update.py` runs the firmware update against the fake Relay, with images made by `tests/build_update.py`
(the same firmware at 0.2.0, signed with the build's throwaway key, and the same image signed with a key the panel does
not hold). A good image installs: it downloads, is verified, the panel restarts into it, it checks in and is
confirmed, and Relay hears each step. A bad signature is refused on the panel and it never restarts. An image whose
check-ins Relay refuses is taken back by the bootloader, the old version runs again and reports the rollback, and the
refused version is not fetched a second time. Build the images first:

```bash
firmware/relay-display/build.sh --qemu && python3 firmware/relay-display/tests/build_update.py
```

`firmware/relay-display/tests/test_seed_layout.py` and `test_caption_font.py` need no emulator. The first checks the seed generator against
`main/settings_format.h`.

## Bench checklist

Not proven by any test, and to be done on the board:

- [ ] The picture is lit and steady; the bars read white, yellow, cyan, green, magenta, red, blue, black
      from the left, with a white border and the red marker top-left (swap `RELAY_PCLK_HZ` or the data pin
      order if not: README "Pin map")
- [ ] The backlight comes on at boot and goes off at power-down (and the revision setting is right)
- [x] The boot line reports `psram 8388608 bytes`
- [ ] A blank panel opens the setup network; a phone joins it from the QR code and the page saves a
      real network; the panel shows its address
- [x] The panel rejoins that network after a power cycle without the setup network opening
- [ ] Hold BOOT for 5 s opens the setup network; hold for 15 s erases everything
- [ ] The picture does not shift or flicker while Wi-Fi is busy (the bounce buffer exists for this)
- [ ] Caption view: drag scrolling is smooth with 200 lines in the buffer while new lines keep arriving (the
      emulator is too slow to say); a flick settles; the caption text reads from the back of the room
- [ ] Caption view: touch lands where the finger is (the GT911 and the rotation), and Jump to live is easy to hit
- [ ] Feed picker: the rows (36 px at 65%) and the menu button (29 px, whose touch area is 12 px wider on every side) are easy to hit with a finger, with two feeds as well as one
- [ ] Feed picker: with two feeds, the half-width panes read well from the back of the room
- [ ] Service feed: the current item, its timer and the next item read from a few metres; the order of service scrolls
      smoothly and a finger on a row with notes opens them without scrolling by mistake
- [ ] Service feed: a song title and a controller name with accents read right (titles in the caption font, the
      smaller text without its accents)
- [ ] LIVE buttons: Previous / Next / Take control are easy to hit with a thumb, and scrolling the order of service
      never presses one by mistake; Next moves the real service on the stage screen
- [ ] Lyrics: a verse on screen reads from the back of the room, and the next slide's dim line is readable and not
      distracting; a slide with accents (the caption font) is acceptable at its size; the panel goes quiet at once when
      ProPresenter clears
- [ ] Timers: the digits read from across a room, a running countdown ticks once a second with no flicker (`feeds` and the
      diag build's "lvgl N flushes" show the screen is still when nothing counts), amber and red come on at the times set
      in admin, and the panel agrees with ProPresenter's own display to within a second over ten minutes
- [ ] Captions: an hour on Wi-Fi against a running Relay with no lost or doubled lines (`captions 0`
      against Relay's history), across at least one Wi-Fi drop and one Relay restart
- [ ] The I2C bus has pull-ups on the board; the internal ones are off (`main/board_i2c.cpp`). A scan that
      finds nothing at 0x30 is the first sign they are missing
- [x] PCLK and sync polarity (`main/board_rgb_stc8.cpp`): the picture locks and is not shifted
- [x] Flash mode (`sdkconfig.board.crowpanel-adv7`): the board boots in QIO at 80 MHz
- [x] Two frame buffers: a scrolling caption list never shows old and new rows in one frame, and the 180 degree
      turn made in software is right
- [x] mDNS discovery with no Relay address typed: it finds Relay, keeps the first one when a second is announced,
      and follows it to a new address (a `dns-sd -P` announcement of the same name at another address)

## Copied from VXP

This target started from the VXP compact display firmware, **`firmware/vxp-compact-display` at 0.2.2**
(VXP repository commit `b7214739`). It was **copied, not shared**: the two diverge from here, and a fix in
one reaches the other by hand. What came from where, and what was changed:

| Here | From VXP | Changed |
|---|---|---|
| `main/display.*`, `board_i2c.*`, `board_format.h`, `config.h` | same names | Renamed; the RTC address removed |
| `main/panel_format.h` | same | Bench title "RELAY" instead of "VXP" |
| `main/settings*.{h,cpp}` | same | New layout: Relay address override, panel ID and saved feed replace the VXP URL and room; one schema; pairing, evacuation and update records removed; panel ID made at boot |
| `main/net.*`, `wifi_policy.h`, `wired_qemu.cpp` | same | Renamed |
| `main/provision.*`, `setup_format.h`, `setup_page.h` | same | Setup SSID is `Relay-Panel-XXXX`; the form takes an optional Relay address (`http://`, not `https://`); pairing and room screens removed |
| `main/button.*`, `components/relay_button` | `button.*`, `vxp_button` | Renamed |
| `components/relay_auth`, `relay_captive`, `relay_form` | `vxp_auth`, `vxp_captive`, `vxp_form` | Renamed |
| `main/screen.*` | `room_screen.cpp` | The LVGL port (partial buffer, flush, task, lock) and the text/QR drawing, rewritten for this target; no room, notice or evacuation drawing; LVGL's Montserrat fonts instead of the generated Geist ones |
| `main/diag.*` | same | Renamed |
| `main/test_native.cpp` | same | Checks for pairing, room and clock dropped; settings, Relay address, form, page and update checks rewritten |
| `main/firmware_update.*`, `update_format.h`, `ota_format.h` | `firmware_update.*`, `update_format.h`, `components/vxp_ota` | Plain HTTP from Relay in place of the pinned TLS stream; Relay's offer in place of the heartbeat's manifest; the quiet moment is no caption line, not an unchanged room; the outcome (`installed`, `rolled_back`) is kept in NVS until Relay has been told |
| `build.sh`, `CMakeLists.txt`, `partitions.csv`, `sdkconfig.*` | same | Renamed; the `cache` partition, TLS and clock settings dropped |
| `tools/image_check.py` | `tools/firmware_image_check.py` | I/O-board-specific allowlist entries removed |
| `tests/qemu.py`, `uart.py`, `staleness.py` | `tests/firmware/` | Comments for the I/O board removed |
| `tests/conftest.py`, `seeds.py`, `test_boot.py`, `test_settings.py`, `test_screen.py` | `tests/firmware_compact/` | New seed layout; Relay names; panel ID tests |

To carry a VXP fix across, diff the VXP file against its row here, apply what is not one of the listed
changes, and bump the version recorded above.
