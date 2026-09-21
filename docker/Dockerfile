# Live Caption Relay
#
# One process, two ports (viewer + admin). The container needs a capture
# device from the host -- see docker-compose.yml and README "Docker".
FROM python:3.12-slim

# PortAudio (sounddevice) + ALSA/Pulse clients so the container can see a
# sound card handed in from the host. libsndfile/soxr pull in via wheels.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libportaudio2 \
        libasound2-plugins \
        alsa-utils \
        pulseaudio-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY run.py blocklist.txt config.example.json ./
COPY tests ./tests
COPY app ./app
COPY docker/entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# config.json is a mounted volume (holds the API key and admin token) so it
# survives rebuilds and never lands in the image.
VOLUME ["/app/config"]
ENV RELAY_CONFIG=/app/config/config.json

EXPOSE 8000 8001

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "run.py"]
