#!/bin/bash
set -euo pipefail

# ============================================================
# new_app.sh — Scaffold a new app from this template.
#   1. Reads NEW_PROJECT_NAME / NEW_BUNDLE_ID from .env
#   2. Validates them (non-empty, hyphen-free name)
#   3. Renames the project (rename_project.sh)
#   4. Verifies the renamed project still builds
#
# Usage: make new-app   (or: bash scripts/new_app.sh)
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PARENT_DIR="$(cd "$ROOT_DIR/.." && pwd)"

# --- 1. Read config ---
ENV_FILE="$ROOT_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi
NAME="${NEW_PROJECT_NAME:-}"
BUNDLE="${NEW_BUNDLE_ID:-}"

# --- 2. Validate ---
if [[ -z "$NAME" || -z "$BUNDLE" ]]; then
    echo "❌ Set NEW_PROJECT_NAME and NEW_BUNDLE_ID in .env first, then run: make new-app"
    exit 1
fi

CURRENT_NAME="$(basename "$ROOT_DIR")"
if [[ "$NAME" == "$CURRENT_NAME" ]]; then
    echo "❌ NEW_PROJECT_NAME ('$NAME') is the same as the current project. Change it in .env."
    exit 1
fi

if [[ "$NAME" == *-* || "$NAME" == *" "* ]]; then
    echo "❌ Project name '$NAME' contains a hyphen/space → becomes an underscore in Swift"
    echo "   type names and trips the SwiftLint 'type_name' rule. Use e.g. '${NAME//[- ]/}'."
    exit 1
fi

echo "🚀 Creating new app: $NAME  ($BUNDLE)"

# --- 3. Rename ---
bash "$SCRIPT_DIR/rename_project.sh" "$NAME" "$BUNDLE"

# --- 4. Verify the renamed project builds (rename_project.sh may move the root) ---
NEW_ROOT="$PARENT_DIR/$NAME"
[[ -d "$NEW_ROOT" ]] || NEW_ROOT="$ROOT_DIR"
cd "$NEW_ROOT"

echo "🔨 Verifying the renamed project builds (this can take a few minutes)..."
if xcodebuild build \
    -project "$NAME.xcodeproj" \
    -scheme "$NAME-Dev" \
    -destination 'generic/platform=iOS Simulator' \
    CODE_SIGNING_ALLOWED=NO \
    -quiet; then
    echo ""
    echo "✅ Done — '$NAME' renamed and builds cleanly."
    echo "   Location: $NEW_ROOT"
else
    echo ""
    echo "⚠️  Renamed, but the verification build FAILED — the rename likely missed something."
    echo "   Review the output above before committing."
    exit 1
fi
