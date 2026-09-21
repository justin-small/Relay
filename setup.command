#!/usr/bin/env bash
# Relay — one-time setup for macOS. Double-click this before the first run.
#
# Creates the virtualenv, installs dependencies, then writes config.json with
# your OpenAI API key and an admin token of your choosing. It does not start
# the server — use start.command for that.
cd "$(dirname "$0")" || exit 1
set -u

finish() { echo; read -r -p "Press return to close." _; }
trap finish EXIT

echo "Relay — setup"
echo "============="
echo

# ---------------------------------------------------------------- python
if ! command -v python3 >/dev/null 2>&1; then
    echo "  Python 3 is not installed."
    echo "  Install it from https://www.python.org/downloads/ and run this again."
    exit 1
fi

PYV="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "  Python 3.10 or newer is required (found $PYV)."
    exit 1
fi
echo "Python $PYV — ok"

# ------------------------------------------------------------ virtualenv
if [ -d .venv ]; then
    echo "Virtualenv already exists — updating dependencies…"
else
    echo "Creating virtualenv…"
    python3 -m venv .venv || { echo "  Could not create .venv"; exit 1; }
fi

.venv/bin/python -m pip install -q --upgrade pip || { echo "  pip upgrade failed"; exit 1; }
echo "Installing dependencies (this can take a minute)…"
.venv/bin/pip install -q -r requirements.txt || { echo "  Dependency install failed"; exit 1; }
echo "Dependencies — ok"
echo

# ---------------------------------------------------------------- config
if [ -f config.json ]; then
    echo "config.json already exists."
    read -r -p "Reconfigure the API key and admin token? [y/N] " ans
    case "$ans" in [Yy]*) ;; *) echo; echo "Setup complete. Run start.command to launch."; exit 0 ;; esac
    echo
fi

echo "OpenAI API key"
echo "  Create one at https://platform.openai.com/api-keys"
echo "  It is stored only in config.json on this machine (permissions 0600)."
echo "  Input is hidden."
while :; do
    read -r -s -p "  API key: " OPENAI_KEY; echo
    OPENAI_KEY="${OPENAI_KEY#"${OPENAI_KEY%%[![:space:]]*}"}"
    OPENAI_KEY="${OPENAI_KEY%"${OPENAI_KEY##*[![:space:]]}"}"
    [ -n "$OPENAI_KEY" ] && break
    echo "  The API key cannot be empty."
done
echo

echo "Admin token"
echo "  This is the password for the operator panel on port 8001."
echo "  Choose something only you know — anyone with it controls the session"
echo "  and can spend against your OpenAI account. Minimum 8 characters."
while :; do
    read -r -s -p "  Admin token: " ADMIN_TOKEN; echo
    ADMIN_TOKEN="${ADMIN_TOKEN#"${ADMIN_TOKEN%%[![:space:]]*}"}"
    ADMIN_TOKEN="${ADMIN_TOKEN%"${ADMIN_TOKEN##*[![:space:]]}"}"
    if [ ${#ADMIN_TOKEN} -lt 8 ]; then
        echo "  Too short — use at least 8 characters."; continue
    fi
    case "$ADMIN_TOKEN" in
        changeme|password|admin|relay)
            echo "  That token is guessable. Pick another."; continue ;;
    esac
    read -r -s -p "  Confirm admin token: " CONFIRM; echo
    [ "$ADMIN_TOKEN" = "$CONFIRM" ] && break
    echo "  Tokens did not match — try again."
done
echo

OPENAI_KEY="$OPENAI_KEY" ADMIN_TOKEN="$ADMIN_TOKEN" .venv/bin/python tools/write_config.py || exit 1

echo
echo "Setup complete."
echo "  Next: double-click start.command to launch Relay."
echo "  To change these later, open the operator panel or run this again."
