#!/bin/bash
set -e

# ============================================================
# bootstrap.sh — Set up the lint/format environment
#   1. Mint (pins SwiftLint/SwiftFormat versions via Mintfile)
#   2. Tools from the Mintfile (mint bootstrap)
#   3. pre-commit + install git hooks
#
# Usage: bash scripts/bootstrap.sh   (or: make bootstrap)
# ============================================================

echo "🚀 Bootstrapping iOS dev environment..."

# --- 1. Homebrew ---
if ! command -v brew >/dev/null 2>&1; then
    echo "❌ Homebrew is not installed. Install it from https://brew.sh and re-run."
    exit 1
fi

# --- 2. Mint ---
if ! command -v mint >/dev/null 2>&1; then
    echo "📦 Installing Mint..."
    brew install mint
else
    echo "✅ Mint already installed."
fi

# --- 3. Tools from the Mintfile (SwiftLint, SwiftFormat — pinned versions) ---
echo "📦 Bootstrapping tools from Mintfile..."
mint bootstrap

# --- 4. pre-commit ---
if ! command -v pre-commit >/dev/null 2>&1; then
    echo "📦 Installing pre-commit..."
    brew install pre-commit
else
    echo "✅ pre-commit already installed."
fi

echo "🪝 Installing git hooks..."
pre-commit install

echo ""
echo "🎉 Done! Try: make help"
