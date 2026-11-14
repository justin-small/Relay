#!/usr/bin/env bash
# Builds Relay-User-Guide.pdf and Relay-User-Guide.docx from src/*.md.
#
#   ./build.sh            both
#   ./build.sh pdf        PDF only
#   ./build.sh docx       DOCX only
#
# Needs pandoc (3.x), typst and python3 on the PATH (brew install pandoc typst;
# the release workflow installs them on Ubuntu).
set -euo pipefail
cd "$(dirname "$0")"

OUT=out
mkdir -p "$OUT"
WHAT="${1:-all}"

VERSION="${GUIDE_VERSION:-$(git describe --tags --always --dirty 2>/dev/null || echo dev)}"
DATE="${GUIDE_DATE:-$(date '+%B %-d, %Y')}"

for tool in pandoc typst python3; do
  command -v "$tool" >/dev/null || { echo "build.sh needs $tool on the PATH (brew install pandoc typst)." >&2; exit 1; }
done

COMMON=(
  metadata.yaml src/*.md
  --from markdown+fenced_divs+pipe_tables+implicit_figures
  --resource-path=.
  --lua-filter=build/callouts.lua
  -M "version=$VERSION" -M "date=$DATE"
)

if [[ "$WHAT" == all || "$WHAT" == pdf ]]; then
  pandoc "${COMMON[@]}" \
    --toc --pdf-engine=typst --template=build/guide.typst \
    -o "$OUT/Relay-User-Guide.pdf"
  echo "wrote $OUT/Relay-User-Guide.pdf"
fi

if [[ "$WHAT" == all || "$WHAT" == docx ]]; then
  python3 build/make_reference.py pandoc "$OUT/reference.docx"
  pandoc "${COMMON[@]}" \
    --reference-doc="$OUT/reference.docx" \
    -o "$OUT/Relay-User-Guide.docx"
  rm -f "$OUT/reference.docx"
  echo "wrote $OUT/Relay-User-Guide.docx"
fi
