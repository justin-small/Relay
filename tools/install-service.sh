#!/usr/bin/env bash
# Run Relay as a system service on a headless Linux machine (a small server, a Raspberry Pi in a rack).
# No Docker, no desktop session. Supported: Debian 12/13, Ubuntu 24.04+, Raspberry Pi OS (arm64).
#
#   sudo tools/install-service.sh                 install (or repair) and start
#   sudo tools/install-service.sh update          re-copy this checkout, refresh packages, restart
#   sudo tools/install-service.sh uninstall       remove the service and program; keep the state folder
#   sudo tools/install-service.sh uninstall --purge   ...and delete /var/lib/relay and the relay user
#   sudo tools/install-service.sh reset-owner <username>   lockout recovery (tools/reset_owner.py)
#   sudo tools/install-service.sh setup-code      show the pending one-time setup code
#
# Options for install/update: --python <path to python3.13 or newer>  --no-apt
# Logs: journalctl -u relay        State: /var/lib/relay        Program: /opt/relay
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR=/opt/relay
STATE_DIR=/var/lib/relay
UNIT=/etc/systemd/system/relay.service
SVC_USER=relay
# Python 3.13 or newer: the freeze spike (spike/RESULT.md) proved 3.13, 3.14 and 3.15 on Linux x64 and arm64.
SHIP=(app run.py requirements.txt blocklist.txt config.example.json tools/reset_owner.py)

say()  { printf '  %s\n' "$*"; }
die()  { printf '\n  %s\n\n' "$*" >&2; exit 1; }

need_root() { [ "$(id -u)" -eq 0 ] || die "Run this with sudo: sudo $0 ${CMD:-}"; }

find_python() {
  local want="${PYTHON:-}" cand
  if [ -n "$want" ]; then
    "$want" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 13) else 1)' 2>/dev/null \
      || die "$want is not Python 3.13 or newer."
    echo "$want"; return
  fi
  for cand in python3.15 python3.14 python3.13 python3; do
    if command -v "$cand" >/dev/null 2>&1 \
       && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 13) else 1)' 2>/dev/null; then
      command -v "$cand"; return
    fi
  done
  return 1
}

install_packages() {
  [ "$NO_APT" = 1 ] && return
  command -v apt-get >/dev/null 2>&1 || { say "No apt: install python3-venv and the PortAudio library (libportaudio2) yourself."; return; }
  say "Installing system packages (python3-venv, libportaudio2, ca-certificates)"
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3-venv libportaudio2 ca-certificates >/dev/null
}

make_user() {
  if ! id "$SVC_USER" >/dev/null 2>&1; then
    say "Creating the unprivileged user '$SVC_USER'"
    useradd --system --home-dir "$STATE_DIR" --no-create-home --shell /usr/sbin/nologin --user-group "$SVC_USER"
  fi
  getent group audio >/dev/null || groupadd --system audio
  id -nG "$SVC_USER" | tr ' ' '\n' | grep -qx audio || usermod -aG audio "$SVC_USER"
  install -d -m 0700 -o "$SVC_USER" -g "$SVC_USER" "$STATE_DIR"
}

copy_program() {
  local item
  install -d -m 0755 "$APP_DIR" "$APP_DIR/tools"
  for item in "${SHIP[@]}"; do
    [ -e "$SRC/$item" ] || die "$SRC/$item is missing: run this from a complete Relay checkout."
    rm -rf "${APP_DIR:?}/$item"
    cp -R "$SRC/$item" "$APP_DIR/$item"
  done
  find "$APP_DIR/app" -name __pycache__ -type d -prune -exec rm -rf {} +
  chown -R root:root "$APP_DIR"
  chmod -R go-w "$APP_DIR"
}

make_venv() {
  local py
  py="$(find_python)" || die "Relay needs Python 3.13 or newer and this machine has an older one (Debian 12 ships 3.11).
  Install one and pass it: sudo $0 --python /path/to/python3.13
  (for example 'uv python install 3.13', or build it from python.org), then run this again."
  if [ ! -x "$APP_DIR/venv/bin/python" ] || ! "$APP_DIR/venv/bin/python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 13) else 1)' 2>/dev/null; then
    say "Creating the virtualenv with $("$py" --version)"
    rm -rf "$APP_DIR/venv"
    "$py" -m venv "$APP_DIR/venv"
  fi
  say "Installing the pinned requirements"
  "$APP_DIR/venv/bin/python" -m pip install --quiet --disable-pip-version-check --require-virtualenv -r "$APP_DIR/requirements.txt"
  "$APP_DIR/venv/bin/python" -m compileall -q "$APP_DIR/app" "$APP_DIR/run.py" >/dev/null 2>&1 || true
}

write_unit() {
  install -m 0644 "$SRC/tools/relay.service" "$UNIT"
  systemctl daemon-reload
}

wait_for_code() {
  # The service writes the code to the state folder as soon as it starts, while no account exists.
  for _ in $(seq 1 40); do
    [ -s "$STATE_DIR/setup-code" ] && return 0
    sleep 0.5
  done
  return 1
}

fingerprint() {
  runuser -u "$SVC_USER" -- env RELAY_HOME="$STATE_DIR" "$APP_DIR/venv/bin/python" "$APP_DIR/run.py" --fingerprint 2>/dev/null | tail -1 || true
}

report() {
  local host ip fp code
  host="$(hostname -f 2>/dev/null || hostname)"
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  fp="$(fingerprint)"
  echo
  say "Relay is running as the 'relay' service and starts at boot."
  say "Open the operator panel from another device:  https://${ip:-$host}/admin"
  [ -n "$fp" ] && say "The certificate is self-signed. Its SHA-256 fingerprint is: $fp"
  if wait_for_code; then
    code="$(cat "$STATE_DIR/setup-code")"
    echo
    say "One-time setup code (creates the first Owner; stops working once they exist):"
    say "    $code"
    say "Show it again with: sudo $0 setup-code   (or: journalctl -u relay | grep -A1 'setup code')"
  else
    echo
    say "No setup code is pending: an Owner already exists. If you are locked out: sudo $0 reset-owner <username>"
  fi
  echo
  say "Logs: journalctl -u relay -f      Check: systemd-analyze security relay"
}

do_install() {
  need_root
  [ -d /run/systemd/system ] || die "This machine is not running systemd."
  install_packages
  make_user
  copy_program
  make_venv
  write_unit
  systemctl enable relay >/dev/null 2>&1
  systemctl restart relay
  report
}

do_uninstall() {
  need_root
  systemctl disable --now relay >/dev/null 2>&1 || true
  rm -f "$UNIT"
  systemctl daemon-reload
  rm -rf "$APP_DIR"
  if [ "$PURGE" = 1 ]; then
    rm -rf "$STATE_DIR"
    id "$SVC_USER" >/dev/null 2>&1 && userdel "$SVC_USER" 2>/dev/null || true
    say "Relay, its settings and its accounts are removed."
  else
    say "Relay is removed. Its settings and accounts are kept in $STATE_DIR (add --purge to delete them)."
  fi
}

do_reset_owner() {
  need_root
  [ -n "${1:-}" ] || die "Usage: sudo $0 reset-owner <username>"
  [ -x "$APP_DIR/venv/bin/python" ] || die "Relay is not installed."
  # Stop first so the running service and this tool never write the database together.
  systemctl stop relay
  runuser -u "$SVC_USER" -- env RELAY_HOME="$STATE_DIR" "$APP_DIR/venv/bin/python" "$APP_DIR/tools/reset_owner.py" "$1" || {
    systemctl start relay; exit 1; }
  systemctl start relay
}

do_setup_code() {
  need_root
  [ -s "$STATE_DIR/setup-code" ] && { say "Setup code: $(cat "$STATE_DIR/setup-code")"; return; }
  say "No setup code is pending: an Owner already exists (sudo $0 reset-owner <username> if you are locked out)."
}

CMD=install; PYTHON=""; NO_APT=0; PURGE=0; REST=()
while [ $# -gt 0 ]; do
  case "$1" in
    install|update|uninstall|reset-owner|setup-code) CMD="$1" ;;
    --python) PYTHON="${2:?--python needs a path}"; shift ;;
    --no-apt) NO_APT=1 ;;
    --purge) PURGE=1 ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) die "Unknown option $1 (see --help)." ;;
    *) REST+=("$1") ;;
  esac
  shift
done

case "$CMD" in
  install|update) do_install ;;
  uninstall)      do_uninstall ;;
  reset-owner)    do_reset_owner "${REST[0]:-}" ;;
  setup-code)     do_setup_code ;;
esac
