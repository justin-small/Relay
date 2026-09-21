#!/usr/bin/env bash
# Scan the relay image for CVEs, locally, without CI.
#
#   ./tools/scan-image.sh              # build, then scan: vulns + config + secrets
#   ./tools/scan-image.sh --no-build   # scan the image that is already built
#   ./tools/scan-image.sh --all        # include won't-fix advisories (noisy)
#   ./tools/scan-image.sh --severity CRITICAL
#   ./tools/scan-image.sh --json out/  # also write raw JSON reports there
#
# Three passes, in order:
#   1. image vulnerabilities  -- Debian packages + the Python venv (gates exit)
#   2. Dockerfile/compose misconfiguration                    (advisory)
#   3. secrets in the repo working tree                       (advisory)
#
# Exit status is the number of fixable findings at or above --severity in
# pass 1 (clamped to 125; 2 means the scan itself could not run); the advisory passes print but never fail the run. The base image is
# pinned by digest (see docker/Dockerfile), so a clean run here is what makes
# that pin safe: a CVE published against bookworm or CPython after the pin was
# taken shows up in pass 1 with no code change on this side.
#
# Trivy runs from its own container by default, so nothing has to be installed
# on the host; a `trivy` on PATH is used instead when present. The vulnerability
# database is cached in the named volume `relay-trivy-cache`, so only the first
# run pays the download.
set -u

IMAGE="${RELAY_IMAGE:-live-caption-relay}"
TAG="${RELAY_SCAN_TAG:-scan}"
REF="${IMAGE}:${TAG}"
# Pinned for the same reason the base image is: a scanner that changes under
# you turns "no new findings" into an unreliable statement. Bump deliberately.
TRIVY_IMAGE="${RELAY_TRIVY_IMAGE:-aquasec/trivy:0.74.0}"
CACHE_VOL="${RELAY_TRIVY_CACHE:-relay-trivy-cache}"
SEVERITY="HIGH,CRITICAL"
BUILD=1
IGNORE_UNFIXED="--ignore-unfixed"
JSON_DIR=""

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

while [ $# -gt 0 ]; do
    case "$1" in
        --no-build) BUILD=0 ;;
        --all) IGNORE_UNFIXED="" ;;
        --severity) SEVERITY="${2:?--severity needs a value}"; shift ;;
        --severity=*) SEVERITY="${1#*=}" ;;
        --json) JSON_DIR="${2:?--json needs a directory}"; shift ;;
        --json=*) JSON_DIR="${1#*=}" ;;
        -h|--help) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
    esac
    shift
done

command -v docker >/dev/null 2>&1 || {
    echo "docker is not on PATH -- this scans a container image, so Docker has to be running" >&2
    exit 2
}

if [ -n "$JSON_DIR" ]; then
    mkdir -p "$JSON_DIR" || exit 2
    JSON_DIR="$(cd "$JSON_DIR" && pwd)"
fi

# Host trivy if there is one, otherwise the pinned container. The container
# form needs the Docker socket (to read the image) and the repo mounted
# read-only (for passes 2 and 3).
if command -v trivy >/dev/null 2>&1; then
    RUNNER="host"
    trivy_run() { trivy "$@"; }
else
    RUNNER="container ($TRIVY_IMAGE)"
    DOCKER_SOCK="${RELAY_DOCKER_SOCK:-/var/run/docker.sock}"
    [ -S "$DOCKER_SOCK" ] || {
        echo "no Docker socket at $DOCKER_SOCK -- set RELAY_DOCKER_SOCK, or install trivy on the host" >&2
        exit 2
    }
    trivy_run() {
        docker run --rm \
            -v "$DOCKER_SOCK:/var/run/docker.sock:ro" \
            -v "$CACHE_VOL:/root/.cache/trivy" \
            -v "$REPO_ROOT:/repo:ro" \
            ${JSON_DIR:+-v "$JSON_DIR:/out"} \
            "$TRIVY_IMAGE" "$@"
    }
fi

# Where the repo and the JSON output live from the scanner's point of view.
if [ "$RUNNER" = "host" ]; then SRC="$REPO_ROOT"; OUT="$JSON_DIR"; else SRC="/repo"; OUT="/out"; fi

echo "Live Caption Relay -- image vulnerability scan"
echo "image: $REF   severity: $SEVERITY   unfixed: $([ -n "$IGNORE_UNFIXED" ] && echo hidden || echo shown)"
echo "trivy: $RUNNER"
echo

if [ "$BUILD" -eq 1 ]; then
    echo "== build =="
    # Same Dockerfile and context the real image is built from, so the scan
    # describes what ships. --pull is deliberately absent: the base is pinned
    # by digest, and pulling would not change which layer is scanned.
    docker build -f "$REPO_ROOT/docker/Dockerfile" -t "$REF" "$REPO_ROOT" || {
        echo "build failed -- nothing to scan" >&2
        exit 2
    }
    echo
else
    docker image inspect "$REF" >/dev/null 2>&1 || {
        echo "no image $REF -- drop --no-build, or set RELAY_IMAGE/RELAY_SCAN_TAG" >&2
        exit 2
    }
fi

echo "== 1/3  image vulnerabilities (OS packages + Python venv) =="
# --exit-code 1 would only tell us pass/fail; the count comes from the JSON,
# which is written either way so a failing run leaves something to read.
count_json="$(mktemp -t relay-scan.XXXXXX)"
trap 'rm -f "$count_json"' EXIT

trivy_run image --scanners vuln --severity "$SEVERITY" $IGNORE_UNFIXED \
    --format table "$REF" || {
        echo "trivy could not scan $REF" >&2
        exit 2
    }

# Second pass over the same cached results, for a machine-readable count and
# the optional saved report. Quiet, because the table above already printed.
if [ -n "$JSON_DIR" ]; then
    trivy_run image --scanners vuln --severity "$SEVERITY" $IGNORE_UNFIXED \
        --format json --quiet --output "$OUT/image-vulns.json" "$REF" >/dev/null 2>&1
    cp "$JSON_DIR/image-vulns.json" "$count_json" 2>/dev/null
else
    trivy_run image --scanners vuln --severity "$SEVERITY" $IGNORE_UNFIXED \
        --format json --quiet "$REF" > "$count_json" 2>/dev/null
fi

findings="$(python3 - "$count_json" <<'PY' 2>/dev/null || echo 0
import json, sys
try:
    with open(sys.argv[1]) as fh:
        report = json.load(fh)
except Exception:
    print(0); raise SystemExit
print(sum(len(r.get("Vulnerabilities") or []) for r in (report.get("Results") or [])))
PY
)"
findings="${findings:-0}"
echo

echo "== 2/3  Dockerfile and compose misconfiguration (advisory) =="
trivy_run config --severity "$SEVERITY" "$SRC/docker" \
    || echo "  (config scan did not complete)"
[ -n "$JSON_DIR" ] && trivy_run config --severity "$SEVERITY" --quiet \
    --format json --output "$OUT/config.json" "$SRC/docker" >/dev/null 2>&1
echo

echo "== 3/3  secrets in the working tree (advisory) =="
# The image itself carries no config.json -- it arrives on the state volume at
# runtime -- so this looks at the checkout, where a stray key would be.
trivy_run fs --scanners secret "$SRC" \
    || echo "  (secret scan did not complete)"
[ -n "$JSON_DIR" ] && trivy_run fs --scanners secret --quiet \
    --format json --output "$OUT/secrets.json" "$SRC" >/dev/null 2>&1
echo

if [ "$findings" -eq 0 ]; then
    echo "RESULT: no fixable $SEVERITY findings in $REF"
else
    echo "RESULT: $findings fixable $SEVERITY finding(s) in $REF"
    echo "  -> rebuild against a current base: Dependabot opens the digest bump"
    echo "     weekly (.github/dependabot.yml); by hand, docker pull"
    echo "     python:3.12-slim-bookworm and re-pin the python-base FROM line."
    echo "  -> a finding in the venv is a requirements.txt bump instead."
    echo "  -> findings in the Caddy binary's Go modules are triaged in the README"
    echo "     (Docker > Scanning the image > Known findings); upstream caddy:2-alpine"
    echo "     has not rebuilt against a fixed Go toolchain yet."
fi
[ -n "$JSON_DIR" ] && echo "reports: $JSON_DIR"
# Exit status is the finding count, clamped: a shell status is one byte, and
# 2 is already taken above for "could not scan".
[ "$findings" -gt 125 ] && findings=125
exit "$findings"
