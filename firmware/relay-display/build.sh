#!/usr/bin/env bash
# Build the Relay panel's firmware. ESP-IDF, and one command.
#
#   firmware/relay-display/build.sh              # into build/
#   firmware/relay-display/build.sh /tmp/out     # somewhere else
#   firmware/relay-display/build.sh --sign key.pem
#   firmware/relay-display/build.sh --qemu       # into build-qemu/, for the emulator
#   firmware/relay-display/build.sh --dev        # into build-dev/, for the bench
#   firmware/relay-display/build.sh --board <name>   # another board, into build-<name>/
#
# **A board on hold is not built or checked** (`on_hold` below). The Waveshare ESP32-P4 10.1" is on
# hold (#114): `--board waveshare-p4-10.1` is refused, and the native checks skip it, so a change to
# the shared code is never held up by a board nobody is working on. `--on-hold` builds and checks it
# anyway; to bring it back, take it out of `on_hold` (and out of `BOARDS` on hold in app/panelfw.py).
#
# **`--board` names the board, and without it the build is the CrowPanel's.** A board is a file,
# `sdkconfig.board.<name>`, which sets the chip and the module, and a header under `main/boards/`.
# That file is applied first and `sdkconfig.defaults`, which every board shares, after it. The
# CrowPanel's directories keep their names (`build/`, `build-dev/`, `build-qemu/`); another board's
# carry its name. `--board` combines with `--dev` and `--sign`. It does not combine with `--qemu`:
# the emulator models the CrowPanel's chip, and the code the suite covers is the same on every board.
#
# **`--dev` is a bench image, flashed over USB.** It applies `sdkconfig.dev` on top of
# `sdkconfig.defaults` (the diagnostics in `main/diag.cpp`) and signs with a throwaway key
# in its build directory, for the same reason `--qemu` does: an unsigned application aborts
# at startup. A panel running it accepts only updates signed with that throwaway key, so
# flash a release image over USB to put the panel back on the release key.
#
# **`--qemu` is a test image and not a different product.** It applies `sdkconfig.qemu` on top
# of `sdkconfig.defaults`, which turns on the virtual RGB panel and leaves Wi-Fi unstarted, and
# it writes the seeded stored configurations that `tests/` boots from. Everything else, the
# partition table included, is what a panel ships with. It is signed with a throwaway key in
# its build directory, because the running application has to carry a signature to start at
# all. It is never released.
#
# **`--sign` is what makes an image updatable over the network.** The application is signed
# after it is built and before it is merged, so the merged file and the application file carry
# the same signed application. Without it the build is still a good image, and no board will
# accept it as an update. That is the right default: the private key exists in one place, and a
# laptop build has no business holding it.
#
# **The native test runs first.** It is a second of g++, and it covers the parts of the
# firmware that can be wrong without a panel: the stored configuration's defaults, decode and
# string bounds, the setup page's form checks, the Wi-Fi start decisions, the rotation's mirror
# flags and the test screen's colours. A build that compiles proves the source compiles. This
# proves some of it is right, and it runs on a laptop before the board's toolchain does.
#
# It needs an ESP-IDF export already sourced, `. $IDF_PATH/export.sh`. README.md names the
# version this is pinned to.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

default_board="crowpanel-adv7"
# Boards whose development is paused. Space-separated names of `sdkconfig.board.<name>` files.
on_hold="waveshare-p4-10.1"
board="$default_board"
signing_key=""
qemu=0
dev=0
with_on_hold=0
while true; do
  case "${1:-}" in
    --board) board="${2:?--board needs a name}"; shift 2 ;;
    --on-hold) with_on_hold=1; shift ;;
    --sign) signing_key="${2:?--sign needs a key file}"; shift 2 ;;
    --qemu) qemu=1; shift ;;
    --dev) dev=1; shift ;;
    *) break ;;
  esac
done

if [ "$qemu" = 1 ] && [ "$dev" = 1 ]; then
  echo "--qemu and --dev do not combine." >&2
  exit 2
fi

is_on_hold() {
  case " $on_hold " in *" $1 "*) return 0 ;; esac
  return 1
}

# Every board there is: one `sdkconfig.board.<name>` each. A board on hold is left out unless
# `--on-hold` asks for it.
boards=""
for f in "$here"/sdkconfig.board.*; do
  b="${f##*/sdkconfig.board.}"
  if is_on_hold "$b" && [ "$with_on_hold" = 0 ]; then
    continue
  fi
  boards="$boards $b"
done
if [ ! -f "$here/sdkconfig.board.$board" ]; then
  echo "No such board: $board. The boards are:$boards" >&2
  exit 2
fi
if is_on_hold "$board" && [ "$with_on_hold" = 0 ]; then
  echo "The $board is on hold (#114): it is not built. Pass --on-hold to build it anyway." >&2
  exit 2
fi
if [ "$qemu" = 1 ] && [ "$board" != "$default_board" ]; then
  echo "--qemu is the $default_board's: the emulator has no model of another board's chip." >&2
  exit 2
fi
# The default board's directories have no board in their names, so the paths CI and the release
# read (`build/relay-display-app.bin`) stay where they were.
if [ "$board" = "$default_board" ]; then
  build_base="$here/build"
else
  build_base="$here/build-$board"
fi
board_defaults="sdkconfig.board.$board;sdkconfig.defaults"

if [ "$qemu" = 1 ]; then
  # **The emulator's image is signed, with a throwaway key of its own.** `sdkconfig.defaults`
  # sets CONFIG_SECURE_SIGNED_APPS_NO_SECURE_BOOT, and a running app with no signature
  # block aborts at startup (`secure_boot_v2: No signatures were found`), so an unsigned
  # emulator image never gets as far as `app_main`. The key is generated below, into the
  # build directory, and never touches the release key.
  if [ -n "$signing_key" ]; then
    echo "--qemu and --sign do not combine: the emulator signs with its own test key." >&2
    exit 2
  fi
  out="${1:-$build_base-qemu}"
  defaults="$board_defaults;sdkconfig.qemu"
elif [ "$dev" = 1 ]; then
  if [ -n "$signing_key" ]; then
    echo "--dev and --sign do not combine: the bench image signs with its own test key." >&2
    exit 2
  fi
  out="${1:-$build_base-dev}"
  defaults="$board_defaults;sdkconfig.dev"
else
  out="${1:-$build_base}"
  defaults="$board_defaults"
fi
mkdir -p "$out"

echo "==> native checks"
# The headers this checks have no ESP-IDF in them, and they sit beside the test
# file, so its own directory is the include path. The shared components it
# includes (`relay_auth`, `relay_button` for the BOOT button, `relay_captive` for the setup
# network's DNS, and `relay_form`) are named here as the `-I` lines the target's REQUIRES list
# gives it. The test file is kept out of the component's SRCS (`main/CMakeLists.txt` says why).
#
# **Once for every board, whichever one is being built.** The board's header changes the panel's size
# and the touch controller, which the shared headers are written against, so a change made for one
# board is checked against the others before any of them is compiled. The native test has no
# sdkconfig: the board is named with a `-D`, which `main/config.h` reads.
for b in $boards; do
  define="RELAY_NATIVE_BOARD_$(printf '%s' "$b" | tr 'a-z.-' 'A-Z__')"
  echo "    $b"
  c++ -std=c++17 -Wall -Werror "-D$define" \
    -I "$here/components/relay_auth/include" \
    -I "$here/components/relay_button/include" \
    -I "$here/components/relay_captive/include" \
    -I "$here/components/relay_form/include" \
    -o "$out/test_native" "$here/main/test_native.cpp"
  "$out/test_native"
done

if ! command -v idf.py >/dev/null 2>&1; then
  echo "idf.py is not on PATH. Source the ESP-IDF export script first:" >&2
  echo "  . \$IDF_PATH/export.sh" >&2
  echo "See firmware/relay-display/README.md for the version this is pinned to." >&2
  exit 1
fi

# **A generated `sdkconfig` older than the defaults it came from is thrown away.**
# ESP-IDF applies `SDKCONFIG_DEFAULTS` when it creates that file and then lets the
# existing file win on every later build, so an option added to the defaults never
# reaches a build directory that already exists. Nothing reports it. The I/O board
# found this on a laptop with a day-old build directory, while CI, which always
# starts clean, was green. This is the general guard against that.
for defaults_file in ${defaults//;/ }; do
  if [ -f "$out/sdkconfig" ] && [ "$here/$defaults_file" -nt "$out/sdkconfig" ]; then
    echo "==> $defaults_file is newer than $out/sdkconfig; regenerating it"
    rm -f "$out/sdkconfig"
    break
  fi
done

if [ "$qemu" = 1 ] || [ "$dev" = 1 ]; then
  signing_key="$out/test_signing_key.pem"
  if [ ! -f "$signing_key" ]; then
    echo "==> test signing key (throwaway, in the build directory)"
    espsecure.py generate_signing_key --version 2 --scheme rsa3072 "$signing_key"
  fi
fi

echo "==> compile"
# `-DSDKCONFIG` keeps the generated file inside this build directory, so two
# configurations built into two directories cannot share one set of answers.
idf.py -C "$here" -B "$out" \
  -DSDKCONFIG_DEFAULTS="$defaults" -DSDKCONFIG="$out/sdkconfig" build

if [ -n "$signing_key" ]; then
  echo "==> sign"
  # **Signing writes over ninja's own output, so the next build inherits it.**
  # Ninja leaves an output newer than its inputs alone, so a second run into a
  # warm build directory would sign an already signed image and append a second
  # signature block. Nothing fails; the bootloader reads the first block and the
  # extra sector is dead weight. The image is therefore deleted and linked again
  # before it is signed, so there is no earlier signature to inherit.
  rm -f "$out/relay-display-app.bin" "$out/relay-display-app.elf"
  idf.py -C "$here" -B "$out" \
    -DSDKCONFIG_DEFAULTS="$defaults" -DSDKCONFIG="$out/sdkconfig" build
  # Through a second file and then over the first. `espsecure.py` refuses an output
  # that is its own input, and that is the right refusal: a signer that wrote over
  # the bytes it read would leave a half-signed image behind on failure.
  espsecure.py sign_data --version 2 --keyfile "$signing_key" \
    --output "$out/relay-display-app.signed.bin" "$out/relay-display-app.bin"
  mv "$out/relay-display-app.signed.bin" "$out/relay-display-app.bin"

  # Exactly one signature block. A block is one 4 KB sector beginning with the
  # magic byte 0xE7. Four bytes of header, not one, so an application byte that
  # happens to be 0xE7 is not taken for a signature.
  echo "==> one signature block"
  python3 - "$out/relay-display-app.bin" <<'ONESIG'
import sys

SECTOR = 4096
HEADER = b"\xe7\x02\x00\x00"
image = open(sys.argv[1], "rb").read()
if len(image) % SECTOR or image[-SECTOR:][:4] != HEADER:
    sys.exit("the signed image does not end in a signature block; see build.sh.")
if len(image) >= 2 * SECTOR and image[-2 * SECTOR :][:4] == HEADER:
    sys.exit("the signed image carries two signature blocks: it was signed twice. See build.sh.")
print(f"one signature block, at 0x{len(image) - SECTOR:x}")
ONESIG
fi

# `merge-bin` takes the flash mode, frequency and size from `sdkconfig`, so the
# bootloader header and the chip cannot disagree about them here.
echo "==> merge"
idf.py -C "$here" -B "$out" merge-bin -o "$out/relay-display.bin"


if [ -n "$signing_key" ]; then
  # `merge-bin` is a second `idf.py` run that re-enters ninja with the application
  # already changed underneath it. The merged file is checked against the signed
  # application byte for byte, so a regenerated application cannot slip through.
  echo "==> merged image carries the signed application"
  python3 - "$out/relay-display.bin" "$out/relay-display-app.bin" <<'CHECK'
import sys
# `app0`'s offset in partitions.csv. Repeated because merge-bin takes the table,
# not an offset.
APP0 = 0x10000
merged, app = (open(p, "rb").read() for p in sys.argv[1:3])
if merged[APP0 : APP0 + len(app)] != app:
    sys.exit("the merged image does not contain the signed application; see build.sh.")
print(f"merged image carries the signed application, {len(app)} bytes at 0x{APP0:x}")
CHECK
fi

if [ "$qemu" = 1 ]; then
  # The stored configurations the emulator boots from. `tests/firmware_compact`
  # says what each one is for. Each becomes an `nvs` image of the partition's size: the
  # 0x8000 `partitions.csv` gives `nvs`. The harness writes the image at the partition's
  # offset behind the merged file.
  echo "==> seeds"
  python3 "$here/tests/seeds.py" "$out"
  nvs_gen="$IDF_PATH/components/nvs_flash/nvs_partition_generator/nvs_partition_gen.py"
  for csv in "$out"/seed/*.csv; do
    name="$(basename "$csv" .csv)"
    python3 "$nvs_gen" generate "$csv" "$out/$name.bin" 0x8000
  done
  echo
  echo "==> $out/relay-display.bin   the emulated image, for tests/firmware_compact"
  ls -l "$out/relay-display.bin" "$out"/nvs*.bin
  exit 0
fi

# The image belongs to no installation: a grep over the bytes that are about
# to be handed to somebody, for a host name, a UUID, a PEM or a token. Run here as
# well as in CI, so a laptop build applies it too.
echo "==> image"
python3 "$here/tools/image_check.py" \
  "$out/relay-display.bin" "$out/relay-display-app.bin"

echo
echo "==> $out/relay-display.bin       one file, flashed at 0x0 with esptool"
if [ -n "$signing_key" ]; then
  echo "==> $out/relay-display-app.bin   the application alone, signed"
else
  echo "==> $out/relay-display-app.bin   the application alone. Unsigned: no board will"
  echo "                                       accept it as an update. Pass --sign to make it one."
fi
ls -l "$out/relay-display.bin" "$out/relay-display-app.bin"
