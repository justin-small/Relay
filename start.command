#!/usr/bin/env bash
# Relay — start the server on macOS. Run setup.command first.
cd "$(dirname "$0")" || exit 1
set -u

if [ ! -x .venv/bin/python ]; then
    echo "Relay is not set up yet."
    echo "  Double-click setup.command first — it installs the dependencies"
    echo "  and asks for your OpenAI API key and admin token."
    echo; read -r -p "Press return to close." _; exit 1
fi

if [ ! -f config.json ]; then
    echo "config.json is missing."
    echo "  Double-click setup.command to create it."
    echo; read -r -p "Press return to close." _; exit 1
fi

exec .venv/bin/python run.py
