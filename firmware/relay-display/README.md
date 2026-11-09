# Relay display: firmware for the CrowPanel 7" caption panel

Firmware for the **Elecrow CrowPanel Advance 7.0" (DIS02170A), hardware V1.2 and later**:
ESP32-S3-WROOM-1-N16R8 (16 MB flash, 8 MB octal PSRAM), an 800 x 480 RGB panel, a GT911 touch
controller and LVGL 9.2. It is a touch-screen caption panel for Relay: a small screen on a wall or a
desk that shows the live transcription, or one translation, from a Relay server on the same network.

**This is the foundation and the caption client.** It boots, lights the panel, joins Wi-Fi (from a setup
page on the panel's own network), keeps its settings, shows a test screen, and keeps the last 200 finished
lines of up to two caption feeds from Relay. It does not draw captions yet: the caption view, the touch
menu, Relay discovery, the panel registry and updates are separate pieces of work that build on this.

## What is here

| | |
|---|---|
| Panel | RGB driver and timings, the STC8 backlight (revision as a build setting), software 180-degree turn |
| Settings | One NVS blob: Wi-Fi network and password, address mode (DHCP or static), Relay address override, panel ID, saved feed, rotation |
| Panel ID | 32 random hex characters, made on first boot, kept until a factory reset. Names the panel to Relay |
| Wi-Fi | Station with DHCP or a static address, with backoff when the network is down |
| Setup | An access point and a page to join Wi-Fi (join code as a QR on the panel), with recovery |
| BOOT button | Hold 5 s: open the setup network. Hold 15 s: factory reset |
| Screen | LVGL: centred text with an optional QR code, the caption view (see "Caption view"), and the colour-bar bench picture |
| Feed picker | A menu button on the caption screen: Transcription or a live translation, one feed or two side by side, saved in NVS (see "Feed picker") |
| Captions | Up to two feeds from Relay's `/stream?finals=1`, each with its own connection and 200-line buffer in PSRAM, resumed with `?last_id=` after a drop (see "Caption client") |
| Partitions | Two app slots, `otadata`, `nvs`; signing set up (the update itself is a later change) |
| `build.sh` | Release, `--dev` and `--qemu` builds, `--sign`, the native checks and the image check |
| Tests | Native checks (g++), and an emulator suite under `qemu-system-xtensa` |

Not here: VXP pairing and pinned TLS (Relay panels use plain HTTP on the LAN), the room screen, the
RTC, audio, the SD card, Bluetooth.

**The released image belongs to no installation.** No Wi-Fi network, address or panel ID is baked in:
they are written to the panel's flash by its own setup page. `tools/image_check.py` reads the bytes of
the images `build.sh` produces and fails the build if one carries a host name, a UUID, a PEM block or a
key-shaped string.

## The board

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

Elecrow's values for this board, from `main/config.h`.

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
```

Every build runs the native checks first (`main/test_native.cpp`, a second of g++), then compiles, then
for a release build checks the image for installation-specific values. The merged image is
`relay-display.bin`; `relay-display-app.bin` is the application alone, the file an update would carry.

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
  set. Discovery and the feed picker set it later. On the serial console, for the bench:
  `feed <slot> <url> translation <LANG>`, `feed <slot> <url> transcription`, `feed <slot> off` (not saved),
  and `captions <slot>` prints the state and every line held.

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
* **The font is fixed at 36 px**, readable from a few metres on the 7". Following Relay's `default_font_px` is not
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

`firmware/relay-display/tests/test_seed_layout.py` and `test_caption_font.py` need no emulator. The first checks the seed generator against
`main/settings_format.h`.

## Bench checklist

Not proven by any test, and to be done on the board:

- [ ] The picture is lit and steady; the bars read white, yellow, cyan, green, magenta, red, blue, black
      from the left, with a white border and the red marker top-left (swap `RELAY_PCLK_HZ` or the data pin
      order if not: README "Pin map")
- [ ] The backlight comes on at boot and goes off at power-down (and the revision setting is right)
- [ ] The boot line reports `psram 8388608 bytes`
- [ ] A blank panel opens the setup network; a phone joins it from the QR code and the page saves a
      real network; the panel shows its address
- [ ] The panel rejoins that network after a power cycle without the setup network opening
- [ ] Hold BOOT for 5 s opens the setup network; hold for 15 s erases everything
- [ ] The picture does not shift or flicker while Wi-Fi is busy (the bounce buffer exists for this)
- [ ] Caption view: drag scrolling is smooth with 200 lines in the buffer while new lines keep arriving (the
      emulator is too slow to say); a flick settles; the 36 px text reads from the back of the room
- [ ] Caption view: touch lands where the finger is (the GT911 and the rotation), and Jump to live is easy to hit
- [ ] Feed picker: the menu button and the 56 px rows are easy to hit with a finger, with two feeds as well as one
- [ ] Feed picker: with two feeds, the half-width panes read well from the back of the room
- [ ] Captions: an hour on Wi-Fi against a running Relay with no lost or doubled lines (`captions 0`
      against Relay's history), across at least one Wi-Fi drop and one Relay restart

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
| `main/test_native.cpp` | same | Checks for pairing, room, clock and update dropped; settings, Relay address, form and page checks rewritten |
| `build.sh`, `CMakeLists.txt`, `partitions.csv`, `sdkconfig.*` | same | Renamed; the `cache` partition, TLS, update and clock settings dropped |
| `tools/image_check.py` | `tools/firmware_image_check.py` | I/O-board-specific allowlist entries removed |
| `tests/qemu.py`, `uart.py`, `staleness.py` | `tests/firmware/` | Comments for the I/O board removed |
| `tests/conftest.py`, `seeds.py`, `test_boot.py`, `test_settings.py`, `test_screen.py` | `tests/firmware_compact/` | New seed layout; Relay names; panel ID tests |

To carry a VXP fix across, diff the VXP file against its row here, apply what is not one of the listed
changes, and bump the version recorded above.
