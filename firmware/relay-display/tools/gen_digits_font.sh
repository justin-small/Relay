#!/usr/bin/env bash
# Regenerate the timers view's digit fonts (`main/fonts/relay_digits_*.c`). Needs node; lv_font_conv is fetched by
# npx at a pinned version, as in gen_caption_font.sh.
#
# Digits, the colon and the minus sign only (0-9 : -), in LVGL's own Montserrat Medium (SIL OFL), at the sizes the
# timers view picks from (`main/pp_format.h`, pp_digit_px): a countdown has to be read from across a room, which
# LVGL's largest built-in size (48 px) is not. Twelve glyphs keep each file small (about 50 KB of flash at 140 px).
#
#   firmware/relay-display/tools/gen_digits_font.sh [size ...]
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
src="$here/managed_components/lvgl__lvgl/scripts/built_in_font"
conv="npx --yes lv_font_conv@1.5.3"
sizes=("$@")
[ ${#sizes[@]} -gt 0 ] || sizes=(140 80)
for size in "${sizes[@]}"; do
  out="$here/main/fonts/relay_digits_${size}.c"
  $conv --font "$src/Montserrat-Medium.ttf" -r 0x30-0x3A,0x2D --size "$size" --bpp 4 --no-compress --format lvgl \
    --lv-include lvgl.h --lv-font-name "relay_digits_${size}" -o "$out"
  # The generator writes the command line, with this machine's paths, into the header. Drop that line.
  grep -v '^ \* Opts:' "$out" > "$out.tmp" && mv "$out.tmp" "$out"
done
