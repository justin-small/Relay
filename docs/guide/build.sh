#!/usr/bin/env bash
# Builds Relay-User-Guide.pdf and Relay-User-Guide.docx from src/*.md.
#
#   ./build.sh            both
#   ./build.sh pdf        PDF only
#   ./build.sh docx       DOCX only
#
# Needs pandoc (3.x) and typst on the PATH. Without them it runs the same
# build in the pandoc/typst container, so Docker alone is enough.
set -euo pipefail
cd "$(dirname "$0")"

OUT=out
mkdir -p "$OUT"
WHAT="${1:-all}"

VERSION="${GUIDE_VERSION:-$(git describe --tags --always --dirty 2>/dev/null || echo dev)}"
DATE="${GUIDE_DATE:-$(date '+%B %-d, %Y')}"

if command -v pandoc >/dev/null && command -v typst >/dev/null; then
  PANDOC=(pandoc)
  PY=python3
else
  echo "pandoc/typst not found locally; using the pandoc/typst container." >&2
  IMG="pandoc/typst:3.7"
  PANDOC=(docker run --rm -u "$(id -u):$(id -g)" -v "$PWD:/data" -w /data "$IMG")
  PY="docker run --rm -u $(id -u):$(id -g) -v $PWD:/data -w /data --entrypoint python3 $IMG"
fi

COMMON=(
  metadata.yaml src/*.md
  --from markdown+fenced_divs+pipe_tables+implicit_figures
  --resource-path=.
  --lua-filter=build/callouts.lua
  -M "version=$VERSION" -M "date=$DATE"
)

if [[ "$WHAT" == all || "$WHAT" == pdf ]]; then
  "${PANDOC[@]}" "${COMMON[@]}" \
    --toc --pdf-engine=typst --template=build/guide.typst \
    -o "$OUT/Relay-User-Guide.pdf"
  echo "wrote $OUT/Relay-User-Guide.pdf"
fi

if [[ "$WHAT" == all || "$WHAT" == docx ]]; then
  if [[ "$PY" == python3 ]]; then
    python3 build/make_reference.py pandoc "$OUT/reference.docx"
  else
    # The container has no Python; build the reference with pandoc alone.
    "${PANDOC[@]}" -o "$OUT/reference.docx" --print-default-data-file reference.docx
    echo "note: callout styles fall back to Word defaults without python3." >&2
  fi
  "${PANDOC[@]}" "${COMMON[@]}" \
    --reference-doc="$OUT/reference.docx" \
    -o "$OUT/Relay-User-Guide.docx"
  rm -f "$OUT/reference.docx"
  echo "wrote $OUT/Relay-User-Guide.docx"
fi
