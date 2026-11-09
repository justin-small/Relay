# Relay display: firmware for the CrowPanel 7" caption panel

Firmware for the **Elecrow CrowPanel Advance 7.0" (DIS02170A), hardware V1.2 and later**:
ESP32-S3-WROOM-1-N16R8 (16 MB flash, 8 MB octal PSRAM), an 800 x 480 RGB panel, a GT911 touch
controller and LVGL 9.2. It is a touch-screen caption panel for Relay: a small screen on a wall or a
desk that shows the live transcription, or one translation, from a Relay server on the same network.

**This is the foundation.** It boots, lights the panel, joins Wi-Fi (from a setup page on the panel's
own network), keeps its settings, and shows a test screen. It does not show captions yet: the caption
stream, the touch menu, the panel registry and updates are separate pieces of work that build on this.

## What is here

| | |
|---|---|
| Panel | RGB driver and timings, the STC8 backlight (revision as a build setting), software 180-degree turn |
| Settings | One NVS blob: Wi-Fi network and password, address mode (DHCP or static), Relay address override, panel ID, saved feed, rotation |
| Panel ID | 32 random hex characters, made on first boot, kept until a factory reset. Names the panel to Relay |
| Wi-Fi | Station with DHCP or a static address, with backoff when the network is down |
| Setup | An access point and a page to join Wi-Fi (join code as a QR on the panel), with recovery |
| BOOT button | Hold 5 s: open the setup network. Hold 15 s: factory reset |
| Screen | LVGL: centred text with an optional QR code, and the colour-bar bench picture |
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
| Touch | GT911 on the same I2C bus (not read by this firmware yet) |

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

`firmware/relay-display/tests/test_seed_layout.py` needs no emulator: it checks the seed generator against
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
