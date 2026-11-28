#!/bin/bash
# ============================================================
# run-swiftlint-incremental.sh — Xcode Build Phase
#   Lints ONLY the changed Swift files (vs HEAD) so warnings
#   show inline in Xcode and are click-to-navigate (reporter: xcode).
#   Does not fail the build (|| true) — real enforcement lives in
#   the pre-commit hook and CI.
#
# Install: Target -> Build Phases -> + -> New Run Script Phase ->
#          bash scripts/run-swiftlint-incremental.sh
# ============================================================

export PATH="$PATH:/opt/homebrew/bin:/usr/local/bin"

if ! command -v mint >/dev/null 2>&1; then
    echo "warning: Mint is not installed — run 'make bootstrap'. Skipping SwiftLint."
    exit 0
fi

cd "${SRCROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}" || exit 0

CHANGED=$(git diff --name-only --diff-filter=d HEAD -- '*.swift' 2>/dev/null)
if [ -z "$CHANGED" ]; then
    exit 0
fi

echo "$CHANGED" | tr '\n' '\0' | xargs -0 mint run swiftlint lint --quiet || true
