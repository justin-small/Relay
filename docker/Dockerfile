# syntax=docker/dockerfile:1.7
# Live Caption Relay
#
# Two processes, one container: Caddy on the network side, uvicorn on loopback.
# Caddy serves the viewer pages in plain HTTP on 8080 (published as 80) and the
# operator panel over HTTPS on 8443 (published as 443); the relay's own sockets
# never leave 127.0.0.1 inside the network namespace, so the panel and the
# OpenAI key it holds are never on the wire in cleartext. The container needs a capture
# device from the host -- see docker-compose.yml and README "Docker".
#
# Two stages: the builder owns pip and the wheel cache, the runtime image
# carries only the finished virtualenv. Base image is pinned by digest so a
# rebuild is reproducible.
#
# The digest lives on a FROM line, aliased once and reused by both stages,
# rather than in an ARG. That is not style: Dependabot's Docker parser reads
# FROM lines only and never resolves ARG indirection, so a digest hidden
# behind `FROM ${PYTHON_IMAGE}` is a pin nothing can bump. See
# .github/dependabot.yml -- weekly digest PRs are what keeps this current.
# By hand it is still:
#   docker pull python:3.12-slim-bookworm && \
#   docker image inspect python:3.12-slim-bookworm --format '{{index .RepoDigests 0}}'
FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS python-base

# Caddy ships a static Go binary, so the alpine image is only a delivery
# vehicle -- the binary is copied into the Debian runtime below and nothing
# else from this image is used. Pinned by digest, and bumped by Dependabot,
# for the same reasons as above.
FROM caddy:2-alpine@sha256:de23def33b17fb5d1290b0f6c2add1d70780e52341896c00a4c8a2a2fe9d355e AS caddy

# ---------------------------------------------------------------- builder ---
FROM python-base AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PYTHONDONTWRITEBYTECODE=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY requirements.txt ./
# Every dependency ships a manylinux/aarch64 wheel, so no compiler is needed.
# --only-binary=:all: turns a missing wheel into a build failure rather than a
# silent source build that would pull a toolchain into the image.
RUN pip install --no-cache-dir --only-binary=:all: -r requirements.txt

# ---------------------------------------------------------------- runtime ---
FROM python-base AS runtime

# PortAudio (sounddevice) plus the ALSA->PulseAudio plugin, so the container
# can see a sound card handed in from the host. libsndfile/soxr arrive as
# wheels. Debugging CLIs (alsa-utils, pulseaudio-utils) are deliberately NOT
# installed -- add them ad hoc with `docker exec`/a debug build if you need
# arecord or pactl inside the container.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libportaudio2 \
        libasound2-plugins \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged runtime account. High, fixed ids so they cannot collide with a
# real host account through a bind mount. `audio` is Debian's gid 29, which is
# what /dev/snd is handed in as on a Linux host.
ARG RELAY_UID=10001
ARG RELAY_GID=10001
RUN groupadd --gid "${RELAY_GID}" relay \
    && useradd --uid "${RELAY_UID}" --gid "${RELAY_GID}" \
        --home-dir /home/relay --create-home --shell /usr/sbin/nologin relay \
    && usermod --append --groups audio relay

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    HOME=/home/relay \
    RELAY_CONFIG=/app/config/config.json \
    RELAY_BLOCKLIST=/app/config/blocklist.txt \
    # Recorded runs land on the state volume for the same reason the
    # blocklist does: /app is read-only, and the operator has to be able to
    # reach the files from the host. Nothing is written unless the operator
    # turns recording on.
    RELAY_RECORDINGS=/app/config/recordings \
    # The certificate outlives the container: it lives on the state volume, so
    # a restart does not change the fingerprint the operator trusted.
    RELAY_CERT_DIR=/app/config/certs \
    # Alongside the certificate on the state volume, so the operator can read
    # the generated config: /app is read-only and this is the one writable
    # place they can see from the host (docker-config/Caddyfile).
    RELAY_CADDY_DIR=/app/config \
    # Inside the namespace Caddy binds unprivileged ports; the host publishes
    # 80 and 443 in front of them (see docker-compose.yml). This is what lets
    # the container keep cap_drop: ALL -- binding a privileged port directly
    # would need CAP_NET_BIND_SERVICE.
    RELAY_HTTP_PORT=8080 \
    RELAY_HTTPS_PORT=8443 \
    # The ports the operator actually dials. Used only for the URLs printed at
    # startup, so they name the front door rather than the internal socket.
    RELAY_PUBLIC_HTTP=80 \
    RELAY_PUBLIC_HTTPS=443 \
    # Caddy's own state. Both land on tmpfs; with `admin off` and an explicit
    # certificate there is nothing here worth persisting.
    XDG_DATA_HOME=/home/relay/.local/share \
    XDG_CONFIG_HOME=/home/relay/.config

COPY --from=builder /opt/venv /opt/venv
# Staged, then copied with `cp`, deliberately. The upstream binary carries a
# cap_net_bind_service file capability so it can bind :80 as a normal user.
# This container runs with `no-new-privileges` and cap_drop: ALL, and execve
# of a file with capabilities in its permitted set fails with EPERM under
# those flags -- the binary simply would not start. Plain `cp` copies contents
# and mode but not xattrs, so the capability is dropped; Caddy does not need it
# here, because it binds 8080/8443 inside the namespace and the host publishes
# 80 in front (see docker-compose.yml).
COPY --from=caddy /usr/bin/caddy /tmp/caddy.bin
RUN cp /tmp/caddy.bin /usr/local/bin/caddy \
    && rm -f /tmp/caddy.bin \
    && chown root:root /usr/local/bin/caddy \
    && chmod 0555 /usr/local/bin/caddy \
    && /usr/local/bin/caddy version

WORKDIR /app

# Application code is owned by root and read-only to the runtime account: the
# process can execute it but never rewrite it. Only /app/config is writable.
COPY --chown=root:root --chmod=0644 run.py blocklist.txt ./
# Copied as a directory, not a file: a --chmod=0644 on a COPY that has to
# create ./tools would apply that mode to the directory too, and a directory
# with no execute bit cannot be traversed. Only setup_caddy.py reaches the
# build context (see .dockerignore).
COPY --chown=root:root tools ./tools
# --chmod pinned: without it the mode comes from whatever the file happens to
# have in the build context, and a 0600 checkout produced an image the
# unprivileged runtime account could not read on first start.
COPY --chown=root:root --chmod=0644 docker/config.example.json ./config.example.json
# No --chmod on a directory copy: BuildKit applies it to the directories too,
# and a directory without the execute bit cannot be traversed.
COPY --chown=root:root app ./app
COPY --chmod=0555 docker/entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# Mount point for the state volume (config.json holds the API key and the
# admin token, so it stays out of the image and survives rebuilds).
RUN install -d -o "${RELAY_UID}" -g "${RELAY_GID}" -m 0700 /app/config

# 8080 viewer pages (plain HTTP, published as 80), 8443 operator panel (HTTPS,
# published as 443). 8000/8001 are the relay's own sockets and stay on loopback
# inside the namespace -- they are deliberately not exposed.
EXPOSE 8080 8443

USER relay:relay

# Through Caddy on the viewer port, so one probe covers both processes: a dead
# front end fails it even when uvicorn is fine. Adjust if you change `port` in
# config.json.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/healthz', timeout=3).read()"]

ENTRYPOINT ["docker-entrypoint.sh"]
# "serve" is the whole stack: certificate, Caddy, uvicorn, supervised together.
# Any other command is run as-is, which is how setup.command / setup.bat reach
# tools/write_config.py and tools/setup_caddy.py without a Python on the host.
CMD ["serve"]
