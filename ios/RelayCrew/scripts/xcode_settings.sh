#!/bin/bash
set -e

# ============================================================
# xcode_settings.sh — One-time onboarding for Xcode editor prefs
#   that .editorconfig CANNOT express (currently: spell checking).
#
#   Indentation / whitespace / final newline / max line length are
#   handled per-project by .editorconfig (Xcode 16+ reads it
#   automatically) — do NOT duplicate them here.
#
#   ⚠️  These are GLOBAL, per-user Xcode preferences (not project-
#       scoped) and the keys are undocumented. Quit Xcode before
#       running; changes apply on the next launch.
# ============================================================

if pgrep -x Xcode >/dev/null 2>&1; then
    echo "⚠️  Please quit Xcode first, then re-run (Xcode overwrites prefs on quit)."
    exit 1
fi

# Check spelling while typing — surfaces typos in comments/strings/identifiers
# at write time instead of during code review.
defaults write com.apple.dt.Xcode AutomaticallyCheckSpellingWhileTyping -bool YES

echo "✅ Applied. Relaunch Xcode for changes to take effect."
echo "   (Indentation/whitespace are governed by .editorconfig — nothing to do.)"
