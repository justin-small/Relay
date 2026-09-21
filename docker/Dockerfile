# syntax=docker/dockerfile:1.7
# Live Caption Relay
#
# One process, two ports (viewer + admin). The container needs a capture
# device from the host -- see docker-compose.yml and README "Docker".
#
# Two stages: the builder owns pip and the wheel cache, the runtime image
# carries only the finished virtualenv. Base image is pinned by digest so a
# rebuild is reproducible; bump it deliberately with:
#   docker pull python:3.12-slim-bookworm && \
#   docker image inspect python:3.12-slim-bookworm --format '{{index .RepoDigests 0}}'
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e

# ---------------------------------------------------------------- builder ---
FROM ${PYTHON_IMAGE} AS builder

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
FROM ${PYTHON_IMAGE} AS runtime

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
    RELAY_BLOCKLIST=/app/config/blocklist.txt

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Application code is owned by root and read-only to the runtime account: the
# process can execute it but never rewrite it. Only /app/config is writable.
COPY --chown=root:root --chmod=0644 run.py blocklist.txt ./
COPY --chown=root:root docker/config.example.json ./config.example.json
COPY --chown=root:root app ./app
COPY --chmod=0555 docker/entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# Mount point for the state volume (config.json holds the API key and the
# admin token, so it stays out of the image and survives rebuilds).
RUN install -d -o "${RELAY_UID}" -g "${RELAY_GID}" -m 0700 /app/config

EXPOSE 8000 8001

USER relay:relay

# Viewer port only -- the admin port serves the same process, so one probe is
# enough. Adjust if you change `port` in config.json.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read()"]

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "run.py"]
