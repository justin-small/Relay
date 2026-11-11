#!/usr/bin/env bash
# Regenerate the caption view's fonts (`main/fonts/`). Needs node; lv_font_conv is fetched by npx at a pinned version.
#
# One pair of files per size in `sizes`: 27 px is the 75% UI scale and 23 px the 65% one (`main/ui_scale.h`,
# `main/ui_fonts.h`; 36 px was the 100% size). Only the size the scale picks ends up in the image.
#
# The face is LVGL's own Montserrat Medium (SIL OFL), which reads from a few metres on the 7" panel. It
# covers ASCII, Latin-1, Latin Extended-A, the Vietnamese letters of Latin Extended Additional and the euro sign, and
# not the rest of what `caption_glyph_kept` (main/caption_format.h) lets through. DejaVu Sans (Bitstream Vera license,
# also in LVGL's tree) fills in exactly those, as the first font's fallback. `tests/test_caption_font.py` checks that
# the two together cover every kept character; run it after changing a range here.
#
#   firmware/relay-display/tools/gen_caption_font.sh [size ...]
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
src="$here/managed_components/lvgl__lvgl/scripts/built_in_font"
conv="npx --yes lv_font_conv@1.5.3"
sizes=("$@")
[ ${#sizes[@]} -gt 0 ] || sizes=(27 23)

# The kept set, as lv_font_conv ranges.
kept="0x20-0x7E,0xA1-0xAC,0xAE-0x24F,0x1E00-0x1EFB,0x20AC"
# What Montserrat lacks in it (computed from its output; U+1EFC-1EFF are in neither font and are not kept).
more="0x180-0x18E,0x190-0x191,0x193-0x19F,0x1A2-0x1AE,0x1B1-0x1B6,0x1B8-0x1C3,0x1CF-0x1D2,0x1D5-0x1E3,0x1EC-0x1ED,0x1F0-0x1F9,0x21C-0x21D,0x220-0x229,0x22E-0x22F,0x234-0x236,0x238-0x24F,0x1E00-0x1E07,0x1E0A-0x1E0B,0x1E10-0x1E13,0x1E18-0x1E1B,0x1E1E-0x1E1F,0x1E22-0x1E23,0x1E26-0x1E29,0x1E2C-0x1E2D,0x1E30-0x1E35,0x1E38-0x1E39,0x1E3C-0x1E41,0x1E4A-0x1E4B,0x1E54-0x1E59,0x1E5C-0x1E5D,0x1E6A-0x1E6B,0x1E70-0x1E77,0x1E7C-0x1E7F,0x1E86-0x1E8D,0x1E90-0x1E91,0x1E94-0x1E96,0x1E98-0x1E9D,0x1E9F,0x1EFA-0x1EFB"

for size in "${sizes[@]}"; do
  common="--size $size --bpp 4 --no-compress --format lvgl --lv-include lvgl.h"
  $conv --font "$src/DejaVuSans.ttf" -r "$more" $common --lv-font-name relay_caption_${size}_more \
    -o "$here/main/fonts/relay_caption_${size}_more.c"
  $conv --font "$src/Montserrat-Medium.ttf" -r "$kept" $common --lv-font-name relay_caption_${size} \
    --lv-fallback relay_caption_${size}_more -o "$here/main/fonts/relay_caption_${size}.c"
  # The generator writes the command line, with this machine's paths, into each file's header. Drop that line.
  for f in "$here/main/fonts/relay_caption_${size}.c" "$here/main/fonts/relay_caption_${size}_more.c"; do
    grep -v '^ \* Opts:' "$f" > "$f.tmp" && mv "$f.tmp" "$f"
  done
done
