#!/usr/bin/env bash
# Relay — one-time setup for macOS. Double-click this before the first run.
#
# Everything the stack needs, in one pass: it checks Docker, builds the image,
# asks for your OpenAI API key and admin token, and mints the TLS certificate
# the operator panel is served with. All of it lands in docker-config/, which
# survives rebuilds.
#
# There is no Python or virtualenv on this machine to set up. The credentials
# and the certificate are written by the image's own Python, through
# `docker compose run`, so the only dependency here is Docker itself.
#
# It does not start the relay — use start.command for that.
cd "$(dirname "$0")" || exit 1
set -u

finish() { echo; read -r -p "Press return to close." _; }
trap finish EXIT

COMPOSE="docker compose -f docker/docker-compose.yml"

echo "Relay — setup"
echo "============="
echo

# ---------------------------------------------------------------- docker
if ! command -v docker >/dev/null 2>&1; then
    echo "  Docker is not installed."
    echo "  Install Docker Desktop from https://www.docker.com/products/docker-desktop/"
    echo "  and run this again."
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "  Docker is installed but not running."
    echo "  Start Docker Desktop, wait for it to settle, then run this again."
    exit 1
fi
echo "Docker — ok"

# Run the container as this user so the bind-mounted docker-config/ stays
# readable and writable on both sides. The image defaults to 10001:10001.
export RELAY_UID="$(id -u)" RELAY_GID="$(id -g)"

mkdir -p docker-config
chmod 700 docker-config

echo "Building the image (this can take a few minutes the first time)…"
if ! $COMPOSE build; then
    echo "  The build failed — see the output above."
    exit 1
fi
echo "Image — ok"
echo

# ---------------------------------------------------------------- config
SKIP_CREDS=0
if [ -f docker-config/config.json ]; then
    echo "docker-config/config.json already exists."
    read -r -p "Reconfigure the API key and admin token? [y/N] " ans
    # A "no" skips only the credentials. Setup still falls through to the
    # certificate below, so re-running this is the way to renew the panel
    # certificate, or to certify a new address, without retyping the API key.
    case "$ans" in [Yy]*) ;; *) SKIP_CREDS=1 ;; esac
    echo
fi

if [ "$SKIP_CREDS" = "0" ]; then
echo "OpenAI API key"
echo "  Create one at https://platform.openai.com/api-keys"
echo "  It is stored only in docker-config/config.json on this machine"
echo "  (permissions 0600). Input is hidden."
while :; do
    read -r -s -p "  API key: " OPENAI_KEY; echo
    OPENAI_KEY="${OPENAI_KEY#"${OPENAI_KEY%%[![:space:]]*}"}"
    OPENAI_KEY="${OPENAI_KEY%"${OPENAI_KEY##*[![:space:]]}"}"
    [ -n "$OPENAI_KEY" ] && break
    echo "  The API key cannot be empty."
done
echo

echo "Admin token"
echo "  You type this once in the operator panel, to create the first Owner account."
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

# The panel is served over HTTPS and its certificate has to name whatever the
# operator's browser will dial. The LAN address is found at every start (it
# changes with the venue); a hostname does not, so it is asked for once here
# and stored in config.json.
echo "Panel hostname (optional)"
echo "  The operator panel is served over HTTPS. Its certificate always covers"
echo "  this machine's LAN address. If you also reach this host by a hostname,"
echo "  type it now so the certificate covers that too."
echo "  Leave blank to use the IP address only."
read -r -p "  Hostname (FQDN), or blank: " ADMIN_FQDN
ADMIN_FQDN="${ADMIN_FQDN#"${ADMIN_FQDN%%[![:space:]]*}"}"
ADMIN_FQDN="${ADMIN_FQDN%"${ADMIN_FQDN##*[![:space:]]}"}"
echo

if ! OPENAI_KEY="$OPENAI_KEY" ADMIN_TOKEN="$ADMIN_TOKEN" RELAY_ADMIN_FQDN="$ADMIN_FQDN" \
    $COMPOSE run --rm --no-deps \
        -e OPENAI_KEY -e ADMIN_TOKEN -e RELAY_ADMIN_FQDN \
        relay python tools/write_config.py; then
    echo "  Could not write docker-config/config.json — see the output above."
    exit 1
fi
fi

# ------------------------------------------------------------------- tls
# Minted here rather than left to the first start so the operator reads the
# fingerprint now, while they are still at the keyboard, instead of hunting for
# it in a scrolling log on event day. Every start reuses it while it still
# covers the current address and has 30+ days left.
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo 127.0.0.1)"
echo "Operator panel certificate"
echo "  Certifying this machine's LAN address, $LAN_IP."
echo
if ! RELAY_ADMIN_IPS="$LAN_IP" $COMPOSE run --rm --no-deps \
        -e RELAY_ADMIN_IPS relay python tools/setup_caddy.py; then
    echo "  Could not generate the certificate — see the output above."
    exit 1
fi

echo
echo "Setup complete."
echo "  Next: double-click start.command to launch Relay."
echo "  Write down the SHA-256 fingerprint above — you check it against the"
echo "  browser the first time you open the panel."
echo "  To change the key, the token or the hostname later, open the operator"
echo "  panel or run this again."
