#!/bin/bash
set -eo pipefail
shopt -s nocasematch

# ============================================================
# check_image_size.sh — Pre-commit guard against oversized assets
#   Scans staged image files (added / modified / renamed) and blocks
#   the commit if any exceeds its size budget. Sizes are read from the
#   STAGED blob (what is actually committed), not the working tree.
#
#   Per-category budgets (different formats behave very differently):
#     - icon   : AppIcon / *.appiconset / Icons folders
#     - vector : pdf / svg — should be tiny; a large one usually means
#                raster data was embedded into a vector by mistake
#     - raster : png / jpg / jpeg / heic / webp / bmp / tiff — typical UI
#                images (bmp/tiff are uncompressed and prone to being huge)
#     - gif    : animated GIFs can be legitimately large, but prefer
#                Lottie / video / APNG for animations
#
#   Keep this format list in sync with scripts/find_duplicate_images.py.
# ============================================================

# --- Size budgets (bytes) ---
MAX_ICON_SIZE=$((100 * 1024))       # 100 KB — app icons
MAX_VECTOR_SIZE=$((100 * 1024))     # 100 KB — pdf / svg (vectors should be small)
MAX_RASTER_SIZE=$((1024 * 1024))    # 1 MB   — png / jpg / jpeg / heic / webp / bmp / tiff
MAX_GIF_SIZE=$((3 * 1024 * 1024))   # 3 MB   — animated gif

# --- Extensions treated as images / vectors (case-insensitive) ---
EXT_REGEX='\.(png|jpg|jpeg|heic|gif|webp|pdf|svg|bmp|tiff|tif)$'

violations=()
has_gif_violation=false

# Human-readable byte formatter (no external `bc` dependency)
human() {
    awk -v b="$1" 'BEGIN { if (b >= 1048576) printf "%.2f MB", b / 1048576; else printf "%.1f KB", b / 1024 }'
}

echo "🚀 Checking staged image/icon sizes..."

# Staged paths excluding deletions (A/C/M/R/T), NUL-delimited for safe filenames
while IFS= read -r -d '' FILE; do
    [[ "$FILE" =~ $EXT_REGEX ]] || continue

    # Size of the staged blob — what will actually be committed
    SIZE=$(git cat-file -s ":$FILE" 2>/dev/null) || continue

    # Classify into a budget category (icon path wins over extension)
    if [[ "$FILE" == *.appiconset/* || "$FILE" =~ appicon || "$FILE" =~ /icons?/ ]]; then
        KIND="icon";   LIMIT=$MAX_ICON_SIZE
    elif [[ "$FILE" =~ \.(pdf|svg)$ ]]; then
        KIND="vector"; LIMIT=$MAX_VECTOR_SIZE
    elif [[ "$FILE" =~ \.gif$ ]]; then
        KIND="gif";    LIMIT=$MAX_GIF_SIZE
    else
        KIND="image";  LIMIT=$MAX_RASTER_SIZE
    fi

    if (( SIZE > LIMIT )); then
        violations+=("  ⚠️  [$KIND] $FILE — $(human "$SIZE") (limit $(human "$LIMIT"))")
        [[ "$KIND" == "gif" ]] && has_gif_violation=true
    fi
done < <(git diff --cached --name-only --diff-filter=d -z)

if (( ${#violations[@]} > 0 )); then
    echo "❌ Commit blocked — oversized assets:"
    printf '%s\n' "${violations[@]}"
    echo ""
    echo "→ Compress them (ImageOptim / pngquant / svgo) or keep them out of the app bundle."
    if [[ "$has_gif_violation" == true ]]; then
        echo "→ For animations, prefer Lottie (JSON), a video, or APNG over large GIFs."
    fi
    exit 1
fi

echo "✅ All staged assets within size limits."
exit 0
