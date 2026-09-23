# Relay

Server-captured, low-latency live captioning and translation for remote
viewers. One vendor (OpenAI Realtime), native audio capture, text-only output.

Viewers open a link on their phone and read. Nothing to install, no account,
no microphone permission — the audio is captured on the host machine.

Two front doors, served by Caddy:

| | Serves | On the wire |
|---|---|---|
| **80** | `/` chooser, `/transcription`, `/translation`, `/both` — the link you give the room | plain HTTP |
| **8443** | `/admin` — the operator panel | HTTPS |

Behind them the relay is one process with two loopback sockets: `port` (8000)
for viewers and `admin_port` (8001) for the panel. Neither is on the network.
`host` is `0.0.0.0` because the room has to reach the viewer link; `admin_host`
is `127.0.0.1` because the panel reads and writes the OpenAI key and the admin
token, and those have no business crossing venue Wi-Fi in cleartext.

Admin routes are refused with a 404 on the viewer port — in the app and again
in Caddy — and the chooser page only offers the panel when it is served on the
admin port, so the audience link never exposes it. Set `admin_port` equal to
`port` in `config.json` to put everything back on one port.

The panel's certificate is self-signed and generated on this machine: there is
no public DNS name on a venue LAN and no ACME challenge to answer, so a real CA
is not on the table. Setup prints its SHA-256 fingerprint. Check that against
what the browser shows the first time and then accept it — that check is the
whole value of the warning.

**Running Relay at a venue, not developing it?** The
[Relay User Guide](https://github.com/justin-small/Relay/releases/latest/download/Relay-User-Guide.pdf)
([Word version](https://github.com/justin-small/Relay/releases/latest/download/Relay-User-Guide.docx))
walks through installing on macOS, Windows and Linux, getting an OpenAI key
with a spending limit, every screen of the panel, and the Bitfocus Companion
module, in plain English. Its source is in [`docs/guide/`](docs/guide/).

---

## Install

Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or
Docker Engine on Linux). Nothing else — no Python, no virtualenv, no Caddy.
Everything runs in one container, and setup uses the image's own Python to
write the credentials and mint the certificate.

**Setup runs once. Start runs every time.**

| | Set up | Start | Stop | Follow the log |
|---|---|---|---|---|
| **macOS** | double-click `setup.command` | double-click `start.command` | double-click `stop.command` | double-click `logs.command` |
| **Windows 11** | double-click `setup.bat` | double-click `start.bat` | double-click `stop.bat` | double-click `logs.bat` |
| **Linux** | `./setup.sh` | `./start.sh` | `./stop.sh` | `./logs.sh` |

`setup.*` checks Docker, builds the image, then asks for three things: your
**OpenAI API key**, an **admin token** of your choosing, and optionally a
**hostname** for this machine. The first two are written to
`docker-config/config.json` with permissions `0600`; neither is echoed to the
screen, and neither is ever committed. It then mints the operator panel's TLS
certificate and prints its SHA-256 fingerprint — write that down.

`start.*` starts the whole stack: Caddy and the relay, one container, one
command. It finds this machine's LAN address first, because the certificate has
to name whatever the operator's browser will dial and that changes with the
venue. If setup has not been run it says so and stops rather than launching a
half-configured relay.

The container runs in the background, so there is no window to keep open.
`start.*` waits until the relay answers, prints both URLs and the panel
certificate's SHA-256, opens the panel in the browser, then closes its window
after 30 seconds (press return to close it sooner). If anything fails, the
window stays open with the error and the last lines of the relay's log. The
relay keeps running until `stop.*`; `logs.*` shows what it is doing, and
closing the log window does not stop it.

Docker restarts the container if it crashes, and again when Docker Desktop
starts, unless `stop.*` ran first. On macOS that restart comes up without the
PulseAudio daemon, which only `start.command` launches, so after a reboot run
`start.command` again rather than relying on the automatic restart.

Run setup again at any time to change the credentials, add a hostname, or renew
the certificate.

<details>
<summary>Manual setup, without the launchers</summary>

```bash
mkdir -p docker-config && chmod 700 docker-config
export RELAY_UID=$(id -u) RELAY_GID=$(id -g)
C="docker compose -f docker/docker-compose.yml"

$C build
OPENAI_KEY='sk-…' ADMIN_TOKEN='your-token' RELAY_ADMIN_FQDN='' \
  $C run --rm --no-deps -e OPENAI_KEY -e ADMIN_TOKEN -e RELAY_ADMIN_FQDN \
  relay python tools/write_config.py
RELAY_ADMIN_IPS=10.0.1.42 $C run --rm --no-deps -e RELAY_ADMIN_IPS \
  relay python tools/setup_caddy.py

RELAY_ADMIN_IPS=10.0.1.42 $C -f docker/docker-compose.linux.yml up
```

The credentials go through the environment rather than the command line so
they never land in a process listing or a shell history file. `RELAY_ADMIN_IPS`
is this machine's LAN address: the container only sees its own bridge address,
so it cannot work that out for itself.
</details>

Audio is the only part that needs per-platform wiring, and the launchers handle
it — see [Docker](#docker) for what each one does and why.

## First run

1. Run `setup.command` / `setup.bat`. Enter your API key and admin token, and
   a hostname if you have one — blank certifies this machine's IP address only.
   Note the SHA-256 fingerprint it prints at the end.
2. Run `start.command` / `start.bat`. It prints both URLs: viewers get
   `http://10.0.1.42/`, the panel is at `https://10.0.1.42/admin`. It opens
   the panel for you, and its window closes by itself once the relay is up.
3. Open the panel. The browser will warn about the certificate the first time —
   check the fingerprint it shows against the one from setup, then accept it.
   Sign in with your admin token, and:
   - pick the **capture device** and channel — the meter should move when
     someone speaks into it;
   - toggle on the **target languages** the room needs;
   - press **Start capture**.
4. Session health should show one `connected` session per enabled target.
   Share the LAN viewer link with the room.

> **The admin token is what protects the panel**, and the panel controls a
> session billed to your OpenAI account. Choose a real password, not a word.
> There is no default token: leave it unset and every login is refused. TLS is
> what keeps that token off the wire, and the fingerprint check is what makes
> the TLS mean anything — skip it and you are trusting whatever answered.

## Target languages

The relay runs on `gpt-realtime-translate`, a streaming interpreter model. One
session per target carries **both** feeds — the English transcript and that
target's translation — so there is no separate transcription session to run or
pay for. Billing is per minute of audio, per open session.

Twelve targets are available (the 13 output languages the model supports, minus
the English source): Spanish, Portuguese, French, German, Italian, Russian,
Chinese, Japanese, Korean, Hindi, Indonesian, Vietnamese.

Two things follow from the model:

- **No dialect or register control.** The model takes an output language and
  nothing else — no prompt, no voice, no formality setting. The old
  `es-419 / es-MX / es-CA` selector is gone; a `variant` left in `config.json`
  is ignored. Spanish comes out in a neutral Latin American register.
- **A target must be enabled for anything to appear**, including
  `/transcription` — the English transcript is produced by the translation
  session, so with every target off both feeds are silent. The panel says so.

## Blocked words

Blocked words live in **`blocklist.txt`** next to `run.py`, one word or phrase
per line:

```
damn
hell
off the record
mierda
hijo de puta
```

The file is created on first start. **Save it and the change is live within
about two seconds — no restart, safe to edit mid-event.** The admin panel shows
the same list and can edit it, but the file is the source of truth.

Matches are **removed** from both the English transcript and the Spanish
translation — no asterisks, no gap, nothing on screen to notice.

- Case and accents are ignored: `nino` matches `Niño`, `cabron` matches `cabrón`.
- Whole words only: `ass` will not touch `class`.
- Multi-word phrases work: `off the record` removes all three words.
- Duplicates are collapsed automatically, so repeating a term across sections
  of the file is harmless.
- Lines starting with `#` are comments and are preserved when the panel saves.
- Comma-separated input works too, if you'd rather paste a list in one line.

**The two feeds are filtered independently.** A word blocked in English does not
block its Spanish translation, because the translation is produced from audio,
not from the English text. To remove a term from both, list both forms.

Because captions stream in fragments, a banned word can arrive split across
deltas (`"dam"` then `"n"`). The filter holds back any trailing text that could
still grow into a blocked term, so a partial word never renders and then
vanishes. That hold is narrow — measured against a 464-term list, roughly 0.2%
of words are briefly held — so the latency budget is untouched.

One caveat: the filter removes words, it does not rewrite grammar. Removing a
noun mid-sentence leaves a gap ("the first item on is the review"). For a word
you expect often, blocking the phrase around it usually reads better.

## Recording and export

**Off by default.** Turn it on in the panel under *Recordings & export*, or set
`recording.enabled` in `config.json`. Nothing is written to disk until you do.

A recording covers **exactly one run — from *Start capture* to *Stop***. Each
committed caption line is appended as it happens, so a crash or a power cut
mid-event still leaves you everything said up to that point. Blocked words are
removed before anything is written, so a term on the blocklist never reaches
the file.

Runs land in `recordings/` (`docker-config/recordings/` under Docker), one
directory per run:

```
recordings/20260920-143012/
  meta.json     start and stop time, source language, line count
  lines.jsonl   every committed line, as it happened
```

The panel lists past runs with their duration and line count. **Download the
whole run as a zip, or any single file.** Everything but `lines.jsonl` is
derived at download time, so nothing is duplicated on disk and a future
improvement to the pairing applies to old events too.

| File | What it is |
| --- | --- |
| `transcript-<LANGUAGE>.txt` | One timestamped line per caption, per language. What a client asks for after an event. |
| `pairs-<TARGET>.jsonl` | Each source line matched to its translation, with a `confidence` field. The audit copy. |
| `finetune-<TARGET>.jsonl` | The confident pairs only, in OpenAI chat format — upload to a fine-tuning job as-is. |

One file set per target language, so a run with Spanish and French produces a
clean English→Spanish pair set and a clean English→French one, rather than one
file you have to reshape.

### How source and translation are matched

There is **no segment id tying a source line to its translation.** The two
feeds arrive as separate event streams and are broken into lines independently
by this app's own idle timers, so the counts do not match: one spoken sentence
can commit as one English line and two Spanish ones, or the reverse.

What the feeds do share is the clock. Lines are matched on **overlapping time
span**, and every row in the pairs file says how sure that match is:

| `confidence` | Meaning |
| --- | --- |
| `exact` | One source line, one translation, neither claimed twice. |
| `merged` | Several source lines inside one translation's span, joined. |
| `split` | One source line answered by several translations. |
| `loose` | No overlap and no unambiguous near match; the nearest line within a wider window. |
| `unpaired` | Nothing plausible on the other side — usually a dropout. |

**Only `exact` rows are carried into the fine-tuning file.** The rest stay in
the pairs file, where they can be reviewed, corrected or ignored. The model is
interpreting, so a translation routinely lands just *after* the sentence it
answers with no overlap at all; that case is matched to the source line that
had just finished — which is also what stops the *next* sentence being swept in
when a speaker pauses briefly.

### Retention

`recording.keep_runs` (default 20) caps how many runs are kept. The oldest are
pruned when a new run starts, so a machine left running a season of events
cannot fill its disk. Individual runs can be deleted from the panel; the run
currently being recorded cannot.

Recordings are a transcript of everything said in the room. `recordings/` is
git-ignored, and under Docker the directory sits on the state volume so it
survives the container and can be collected from the host.

## Schedules

Capture can start and stop on its own. Under *Schedules* in the panel, add as
many as you need. Each one has a start time, a stop time and a US time zone, and
either repeats weekly on the days you tick or runs once on a date:

| Schedule | Repeats | Start | Stop |
| --- | --- | --- | --- |
| Sunday morning | Every Sun | 10:00 AM | 12:00 PM |
| Sunday evening | Every Sun | 5:00 PM | 7:00 PM |
| Wednesday night | Every Wed | 8:00 PM | 9:30 PM |
| Christmas Eve | Once, 12/24/2026 | 7:00 PM | 8:30 PM |

A weekly schedule can have optional start and end dates. A stop time earlier
than the start time means the next day, so 11:00 PM to 1:00 AM works. The panel
shows the next scheduled start, and the bottom bar says which schedule started
a live session and when it will stop.

**Formats and zones.** Dates are shown and entered as MM/DD/YYYY and times as
12-hour with AM/PM, whatever the browser's locale. The zones offered are
Eastern, Central, Mountain, Arizona (no daylight saving), Pacific, Alaska and
Hawaii. Times are wall-clock in the zone you pick, so 10:00 AM stays 10:00 AM
on both sides of a daylight-saving change. A time that falls in the
spring-forward gap runs at the first minute after it; one in the repeated
fall-back hour runs once.

**Rules.**

- **Already running when a window opens:** it keeps running and stops at the
  window's end.
- **You press Stop during a window:** the window is held, and nothing restarts
  until the next scheduled start. Pressing Start clears the hold, and that
  session stops at the window's end.
- **You start by hand outside any window:** no schedule stops it.
- **Windows that overlap or touch** run as one session, with no stop and
  restart at the boundary.
- **Changing or deleting a schedule while its session is live** never cuts the
  session off. Stop it by hand.
- **A start that fails** (no API key, audio device missing) shows the error in
  the panel and is retried every few seconds while the window is open.

**The machine and the relay must be running** for a schedule to fire. If the
relay starts during a window, the session starts straight away. A window missed
entirely is skipped, not run late. A hold is kept in memory only, so a restart
during a held window starts the session again.

Schedules are stored in `config.json` under `schedules`, with ISO dates
(`YYYY-MM-DD`), 24-hour times (`HH:MM`) and days counted from Sunday = 0. See
`app/schedules.py` for the shape. An entry edited by hand that does not
validate is ignored rather than stopping the relay.

## Docker

Docker is the only way this runs: one image, one container, one command.
Reproducible builds, pinned dependencies, and nothing to install on the host
beyond Docker itself.

Audio is the only part that needs per-platform wiring, and the launchers handle
it:

| Host | Start it with | Audio |
|---|---|---|
| **macOS** | `./start.command` | starts a PulseAudio daemon on the Mac and bridges it in |
| **Windows 11** | `start.bat` | reuses WSLg's PulseAudio, which already publishes the mic |
| **Linux** | `./start.sh` | `/dev/snd` passed straight in |

Each launcher runs `tools/check-audio.sh` first and refuses to go live quietly
if a link in the chain is broken, then finds this machine's LAN address and
hands it to the container. State lives in `./docker-config/`: the API key,
admin token, blocklist, the generated `Caddyfile` and the panel's certificate,
all surviving rebuilds.

Caddy runs **inside the same container** as the relay — there is no second
process to launch and no second thing to keep running. The entrypoint mints the
certificate if setup has not already, validates the generated Caddyfile, then
supervises both processes; if either exits, the container exits and
`restart: unless-stopped` brings back a known-good pair rather than leaving a
half-running container that still passes a shallow probe.

| | Outside | Caddy, in the namespace | Relay |
|---|---|---|---|
| **Viewers** | `:80`, plain HTTP | `:8080` | `127.0.0.1:8000` |
| **Panel** | `:443`, HTTPS | `:8443` | `127.0.0.1:8001` |

Caddy binds unprivileged ports inside and the host publishes 80 and 443 in
front of them. That is deliberate: binding a privileged port directly would
need `CAP_NET_BIND_SERVICE`, and the container keeps `cap_drop: ALL`.

### What the container is allowed to do

The image runs unprivileged with a read-only root filesystem and no Linux
capabilities at all. In practice that means:

| | |
|---|---|
| **User** | `10001:10001`, never root. On Linux, pass `RELAY_UID`/`RELAY_GID` so the bind-mounted `docker-config/` matches your account; Docker Desktop fakes ownership, so macOS and Windows can ignore this. |
| **Filesystem** | `read_only: true`. `/app` cannot be rewritten by the process that runs it. Writable: `docker-config/` (state), plus small tmpfs mounts for `/tmp` and `$HOME`. |
| **Privileges** | `cap_drop: ALL` and `no-new-privileges:true` — no setuid escalation path. |
| **Limits** | 1 GB memory, 2 CPUs, 256 pids, and log rotation at 3 × 10 MB, so a wedged run cannot fill the host. |
| **Base images** | `python:3.12-slim-bookworm` and `caddy:2-alpine`, both pinned by digest; dependencies installed wheels-only in a builder stage, so no compiler or pip ships in the runtime image. Only Caddy's binary is taken from its image, copied with `cp` so its `cap_net_bind_service` file capability is dropped — `execve` of a file with capabilities fails outright under `no-new-privileges`. |
| **Front end** | Caddy, same container, same lifecycle. `admin off` — its control socket is unauthenticated and can rewrite the whole config, and nothing here needs it. `auto_https off` — nothing is public, so it never reaches for ACME and never redirects viewers to a certificate they cannot trust. |
| **Panel key** | Minted in the container by setup, into `docker-config/certs/` at mode 0600. It is never in an image layer and never in a registry. Reused across restarts while it still covers the current names, so the fingerprint you wrote down at setup is the fingerprint you see at showtime. |

**The relay's own sockets never leave loopback.** `:8000` and `:8001` are
cleartext and live inside the network namespace; they are not published at all.
Everything from outside arrives through Caddy, which means the panel is only
ever reachable over TLS. The certificate names the host's LAN address, which
the container cannot discover for itself — the launchers pass it in as
`RELAY_ADMIN_IPS` on every start, and `admin_fqdn` in `config.json` (asked for
at setup) adds a hostname.

If you would rather not expose the panel at all, drop the `443:8443` line from
`docker/docker-compose.yml` and tunnel instead:

```bash
ssh -N -L 8443:127.0.0.1:8443 you@relay-host   # then open https://localhost:8443/admin
```

**Why the wiring is needed:** the relay captures from a host sound card
through PortAudio, and a container only sees devices the host hands it.

### Scanning the image

The base images are pinned by digest, which is right for reproducibility and
wrong for CVEs: the layer is frozen, so an advisory published against Debian
bookworm, CPython or Caddy lands silently and the build keeps succeeding.
Two things make the pin safe. Dependabot proposes the digest bump weekly
(`.github/dependabot.yml`), and scanning proves the new layer is actually
cleaner than the old one before it merges. `tools/scan-image.sh` runs it locally —
no CI, no account, nothing to install beyond Docker:

```bash
./tools/scan-image.sh                 # build, then scan
./tools/scan-image.sh --no-build      # scan the image already built
./tools/scan-image.sh --severity CRITICAL
./tools/scan-image.sh --all           # include won't-fix advisories (noisy)
./tools/scan-image.sh --json reports/ # also save the raw JSON
```

Three passes: image vulnerabilities (Debian packages *and* the Python venv
*and* the Caddy binary's Go modules), Dockerfile/compose misconfiguration, and
secrets in the working tree. Only the first gates — exit status is the number
of **fixable** HIGH/CRITICAL findings, so a clean run means every finding has
a version to move to. The other two print and never fail the run.

Trivy does the work, from its own pinned container by default; a `trivy` on
your PATH is used instead when you have one. The vulnerability database is
cached in a named volume, so only the first run pays the download. Run it
before an event and after any dependency bump — and on a repo nobody is
touching, which is exactly when a frozen base image is most likely to be
stale.

Findings come out in two flavours, and the fix differs:

| Where | Fix |
|---|---|
| OS package or Caddy's Go modules | Bump the pinned digest in `docker/Dockerfile` — usually by merging Dependabot's PR, or by hand with `docker pull python:3.12-slim-bookworm` (or `caddy:2-alpine`) then `docker image inspect ... --format '{{index .RepoDigests 0}}'`. For Caddy this currently changes nothing — see *Known findings* below. |
| Python package | Bump it in `requirements.txt` and rebuild. |

Pass 3 will flag your own `config.json` if it holds a real key — that is the
scanner working, not a leak; the file is git-ignored and never enters an image
layer. It is worth reading anyway, because it is the same check that would
catch a key pasted into a file that *is* tracked.

### Known findings: the Caddy binary's Go modules

As of 2026-09-21 a clean scan is not achievable, and the reason is worth
writing down so the next run does not re-litigate it. `caddy:2-alpine` ships
Caddy 2.11.4 built against Go 1.26.3, and Trivy reports **17 fixable HIGH**
findings inside that one binary:

| Module | In image | Fixed in | Count |
|---|---|---|---|
| Go `stdlib` | 1.26.3 | 1.26.6 | 11 |
| `google.golang.org/grpc` | 1.81.0 | 1.83.2 | 3 |
| `golang.org/x/crypto` | 0.52.0 | 0.55.0 | 1 |
| `golang.org/x/net` | 0.55.0 | 0.56.0 | 1 |
| `golang.org/x/text` | 0.37.0 | 0.39.0 | 1 |

Re-pinning the digest does **not** clear them. Upstream's current
`caddy:2-alpine` carries the same binary built against the same toolchain —
scanned directly to confirm, identical 17 findings, Alpine layer clean. This
is upstream not having rebuilt, not this repo being behind.

What actually reaches this deployment, since "17 HIGH" reads worse than it is:

- **Not reachable.** `x/crypto/ssh` (Caddy runs no SSH), the three gRPC
  findings (no gRPC listener; `admin off`), `dnsmessage` (no ACME DNS
  challenge — `auto_https off`), and the `os.Root` symlink traversal (no
  file-serving path takes an attacker-controlled root).
- **Reachable, LAN-scoped.** The `crypto/tls` KeyUpdate and HTTP/2 denial of
  service on the panel's 8443 listener, plus `net/url` and MIME header parsing
  on any request Caddy accepts. All denial of service, from something already
  on the venue LAN, against a service whose failure mode is "captions stop" —
  which the operator sees immediately.
- **`html/template` XSS** needs Caddy to render a template with attacker
  input. This deployment serves static viewer assets and proxies; it renders
  none.

None of it is remote code execution, and none of it is reachable from the
public internet, because nothing here is on the public internet.

So: wait for upstream, and re-pin the `caddy:2-alpine` digest in
`docker/Dockerfile` once it ships a Go 1.26.6 build. `./tools/scan-image.sh`
re-checks every run, so the fix announces itself. Nothing is suppressed —
there is deliberately no `.trivyignore`, because one would hide the next
genuine Caddy finding too, and the exit status stays honest at 17. If upstream
is still on 1.26.3 well after this was written, the alternative is building
Caddy in a builder stage from a current `golang:` image, which buys a
toolchain we control at the cost of the maintenance that follows.

### Windows: no, this does not add WSL2

Docker Desktop on Windows *already* runs on WSL2 — it is the default backend,
installed with Docker Desktop itself. `start.bat` runs `docker compose`
inside that existing WSL environment, because that is where Windows 11's WSLg
publishes the microphone (as a source named `RDPSource`). Operators
double-click the `.bat`; nobody opens a Linux shell.

Windows 10 has no WSLg and cannot reach the mic this way, so the container
cannot capture audio there.

### macOS: PulseAudio

`./start.command` does this for you. To run it by hand:

```bash
brew install pulseaudio
pulseaudio --exit-idle-time=-1 --log-target=stderr \
  --load="module-native-protocol-tcp port=4713 auth-anonymous=1 auth-ip-acl=127.0.0.1;192.168.65.0/24;172.16.0.0/12" &
docker compose -f docker/docker-compose.yml -f docker/docker-compose.macos.yml up
```

The ACL must cover Docker Desktop's VM subnet (`192.168.65.0/24`) and the
bridge range (`172.16.0.0/12`) — the container is not on your LAN, so
`127.0.0.1` alone refuses it. `--daemonize=yes` fails on the Homebrew build;
background it instead.

macOS gates capture per process: the terminal running PulseAudio needs
Microphone permission (System Settings → Privacy & Security → Microphone), or
sources appear and read pure silence.

With `PULSE_SERVER` set, the panel's device list shows the PulseAudio server's
inputs by name ("PulseAudio: MacBook Pro Microphone") rather than ALSA's
`default` and `pulse`, which both only reach the server's default input.
Picking one opens that source directly. A mic connected later (an iPhone, a
USB interface) shows up after **Rescan**, even while capture is running.

### Verifying the chain

`tools/check-audio.sh` walks the whole path and prints PASS/FAIL per link, so a
broken mic turns up at setup rather than at showtime. Exit status is the
number of failures.

```bash
./tools/check-audio.sh                                              # Linux
PULSE_SERVER=tcp:host.docker.internal:4713 ./tools/check-audio.sh   # macOS
PULSE_SERVER=unix:/mnt/wslg/PulseServer ./tools/check-audio.sh      # WSL2
```

It checks that the image exists, the server answers, sources are listed,
PortAudio enumerates an input, and one second of audio actually arrives — and
it fails separately on *digital silence*, the microphone-permission mistake on
both macOS and Windows, and the one that otherwise looks exactly like a
working setup.

### Rehearsal in Docker

```bash
RELAY_DEMO=1 RELAY_ADMIN_IPS=127.0.0.1 \
  docker compose -f docker/docker-compose.yml up
```

No audio wiring needed, no API calls, no billed session.

### Sample rate

PortAudio reports 44100 for ALSA's `pulse` device whatever the server actually
runs at, so a 48k source would be resampled to 44.1k by PulseAudio and then to
24k by soxr — two conversions, and a slower start (~4.8s to steady state
against ~1.6s). The entrypoint therefore sets `RELAY_NATIVE_RATE=48000`, which
`app/audio.py` uses in place of the reported default. Set it to your device's
rate if that is not 48k, or to `0` to take PortAudio's default. Linux hands
`/dev/snd` straight in, so `PULSE_SERVER` is unset there and none of this
applies.

### Caveats

Published ports, not host networking, so the console prints the container's
address rather than the LAN one — give the room the host machine's own LAN
address on `:80`. The same blind spot is why the certificate needs
`RELAY_ADMIN_IPS`: the container cannot name an address it cannot see.

The macOS path adds a network hop to a pipeline tuned for latency, and a
PulseAudio daemon that can fail on event day — check the audio before the room
fills up. Linux (`/dev/snd` straight in) and Windows via WSLg (the socket is
local, so the hop is cheap) have neither problem.

`auth-anonymous=1` means anything that can reach the PulseAudio port can
listen to that microphone. Keep `auth-ip-acl` narrow and do not forward the
port beyond the host.

## Rehearsal mode

Check fonts, projector legibility and the link on every phone in the room
**without opening a billed session**:

```bash
RELAY_DEMO=1 ./start.sh            # or start.command / start.bat
```

`RELAY_DEMO` is read from the environment and passed through to the container.

Canned captions stream to the viewer pages. No OpenAI connection is opened.

## Cost

Cost is `(enabled targets) × (time)`, billed per minute of audio, including
silence — the sessions stay open by design. One session per target carries both
the English transcript and that target's translation, so running Spanish is one
stream, not two. Viewers cost nothing.

**Press Stop between sessions.** That is the whole cost control.

## Tuning line breaks

**Phrase boundaries are not tunable.** `gpt-realtime-translate` decides where a
phrase ends from the audio itself, and the translations endpoint has no turn
detection to configure — there is no `semantic_vad`, no `eagerness`, no
millisecond threshold. A translation session has no turn lifecycle at all;
sending `turn_detection` is rejected outright. If you have an older
`config.json` carrying `vad_mode`, `vad_eagerness` or the `vad_*_ms` fields,
they do nothing and are dropped the next time the file is saved.

What you *can* steer is when this app commits a caption **line** — the point at
which streaming text stops changing and settles. Both controls are in the panel
under *Appearance & tuning*:

- **Caption line break after silence** (`realtime.segment_idle_s`, default 1 s)
  — a line is committed once its text has been quiet this long **and** it ends
  on a sentence boundary. Lower it for snappier lines.
- **Force a line break after** (`realtime.segment_max_idle_s`, default 10 s) —
  the backstop for a line that never reaches a sentence end, always applied at a
  word boundary so a word is never cut in half. Raise it if you see one sentence
  split across two lines.

In-progress text is already on screen as it streams, so a generous backstop
costs nothing: it changes when a line *settles*, not when it appears. Changing
either restarts live sessions.

If captions arrive late rather than in awkward chunks, the cause is upstream —
check the host's bandwidth and the session health table, not these two fields.

## Audio decisions

- **24 kHz / 16-bit / mono.** The format the Realtime API ingests. Captured at
  the interface's native rate, downsampled with soxr.
- **No silence trimming.** Audio streams continuously, silence included.
  Gating risks clipping word onsets and breaking the model's context, and saves
  nothing meaningful. A local RMS check drives the admin "speaking" indicator
  only — it never sits on the path to the API.
- **The input meter reads dBFS.** −60 dBFS at the left edge, 0 at the right,
  with the −18 dBFS target band marked: aim the loudest speech at that band.
  The bar is RMS, the thin marker is a peak hold that decays at 20 dB/s, and
  the **CLIP** indicator latches red for three seconds whenever a sample hits
  the rail. Clipping is the most common cause of bad transcription from a
  console feed, and short transients are invisible on an RMS bar alone — watch
  the LED, not the bar. The cumulative clipped-sample count sits beside it and
  resets on each Start.
- **Stereo feeds can be summed.** The channel selector offers **Mix** on any
  interface with more than one input channel, which averages them rather than
  discarding one — usually the right choice for a stereo board feed. Averaging,
  not adding, so the downmix cannot clip where the source channels did not.
- **Noise reduction defaults off.** The `Noise reduction` control in the admin
  panel's Audio input section sends nothing for a house console feed (already
  gated and gain-staged); reduction runs before the model's own phrase detection,
  so applying it to a conditioned board mix can chew word onsets. Switch to *Room or laptop mic*
  (`far_field`) or *Headset / lavalier* (`near_field`) only when the capture is
  actually one of those.

## Security

- The OpenAI key lives only in `config.json` on the host (mode `0600`,
  git-ignored) and is never sent to a browser. The admin panel shows only
  whether a key is set and its last four characters.
- Viewer pages are read-only and unauthenticated, and served in plain HTTP —
  appropriate for a venue LAN. They carry no secret, and a room full of phones
  will not install a certificate to read captions.
  **Do not expose this server to the public internet as-is**: viewer pages have
  no access control and no transport security.
- The operator panel is a different matter, and is served over HTTPS by Caddy
  (`docker-config/Caddyfile`, generated at setup). The relay's
  own admin socket binds `127.0.0.1` — `admin_host` in `config.json` — so the
  only route in is through TLS. The certificate is self-signed and generated on
  the host; setup prints its SHA-256 fingerprint, and checking that once
  against the browser is what makes the connection worth anything.
- Behind the front end the app reads `X-Forwarded-For` and `X-Forwarded-Proto`,
  but **only from a loopback peer**. A direct client on the LAN cannot forge
  either: not its address, to dodge the login lockout, and not the scheme, to
  influence the cookie.
- The admin token is set by `setup.command` / `setup.bat` and stored in the
  same file. **There is no default token.** An unset token does not leave the
  panel open — every login is refused — but it does mean nobody can operate the
  relay until setup has run.
- Signing in issues a random session id; the admin token itself never goes
  into a cookie. Sessions are held in memory, so they last 12 hours, die on
  restart, and are all revoked when the token is changed. The cookie is
  `HttpOnly` and `SameSite=Strict`, and `Secure` as well whenever the login
  actually arrived over TLS — not on a plain-HTTP login, where the browser
  would drop a `Secure` cookie and lock the operator out.
- Failed logins are delayed and logged, and an IP is locked out for five
  minutes after five failures in a row.
- Translation sessions are instructed to treat everything they hear as content
  to translate, never as instructions to follow.
- The container image is scanned locally with `tools/scan-image.sh` (see
  Docker → *Scanning the image*): pinned base layers do not age gracefully, and
  the scan is what turns the pin from a liability back into reproducibility.
- **If you ever paste an API key somewhere it should not be, revoke it** at
  <https://platform.openai.com/api-keys> rather than deleting the file. A key
  that has been written to disk, a log or a screenshot should be considered
  spent.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Incorrect API key provided` | The key is wrong. Sessions stop rather than retry — paste the right key and press Start again. |
| Sessions say `reconnecting` and climb | Network. Reconnect is automatic with backoff; check the host's connection. |
| Meter is flat while someone speaks | Wrong device or wrong channel. Try each channel, or **Mix** on a stereo feed; press Rescan if the interface was plugged in after launch. |
| CLIP indicator keeps lighting | The feed is too hot. Reduce gain at the console or interface until the peak marker sits at the −18 dBFS band and CLIP stays dark. Clipped audio transcribes badly and no model setting recovers it. |
| Meter moves but sits far left | Input gain is too low. Below about −40 dBFS the model has little to work with; bring the loudest speech up to the marked band. |
| `No input device matched` | The interface was unplugged. Reselect it and press Start. |
| Viewers see "No target language is live" | Spanish is toggled off, or capture is stopped. |
| A blocked word still appears in Spanish | The two feeds are filtered independently — add the Spanish form to the list as well. |
| Edited `blocklist.txt` but nothing changed | Give it ~2 s. Check the relay's log (`logs.*`) for the line `Blocked words reloaded: N term(s)`, and that the file is the one named in the admin panel. |
| A blocked word appears inside a longer word | By design: only whole words match. Add the longer word explicitly. |
| Captions lag or arrive in long blocks | Phrase boundaries come from the model and are not tunable. Long *lines* are a line-break setting — lower *Caption line break after silence*. Genuine lag is upstream: check the host's bandwidth. |
| `CERTIFICATE_VERIFY_FAILED` | Handled via certifi. If it reappears on macOS, run `Install Certificates.command` in your Python folder. |

## Layout

```
setup.command / .bat / .sh   one-time setup: image, credentials, panel TLS
start.command / .bat / .sh   start the whole stack (Caddy + relay, one
                             container) in the background. One per platform:
                             macOS, Windows, Linux — nothing else to launch.
stop.command / .bat / .sh    stop it (and, on macOS, its PulseAudio daemon)
logs.command / .bat / .sh    follow the running relay's log
run.py                       in-container entry point — binds both sockets
requirements.txt             pinned dependencies (installed into the image)
config.example.json          template, used when tests run outside the image
blocklist.txt                blocked words, one per line, hot-reloaded

docker-config/               all state, git-ignored, created by setup
  config.json                live config (0600): API key, admin token, admin_fqdn
  blocklist.txt              the editable copy the panel writes
  Caddyfile                  generated from config.json on every start
  certs/admin.{crt,key}      operator panel certificate (key 0600)
  recordings/                recorded runs, if recording is enabled

app/
  main.py                    FastAPI routes, SSE, admin API
  engine.py                  capture -> sessions -> hub wiring, target toggles
  audio.py                   PortAudio capture, resample, fan-out
  realtime.py                gpt-realtime-translate sessions, reconnect, errors
  hub.py                     delta broadcast, sequencing, late-joiner history
  recorder.py                per-run transcript on disk (opt-in), retention
  exporting.py               transcripts, source<->translation pairing, zip
  languages.py               source + target language table (label, ISO code)
  redact.py                  blocklist filtering, incremental-safe
  demo.py                    rehearsal mode
  config.py                  load, validate, atomic save, hot-reload
  schedules.py               schedule validation, windows, DST, US formats
  scheduler.py               starts and stops capture on the schedules
  templates/                 chooser, viewer, admin panel, login
  static/                    viewer + admin JS and CSS

docker/
  Dockerfile                 build context is the repo root
  docker-compose.yml         base service; overlays add the audio wiring
  docker-compose.{macos,linux,wsl}.yml
  entrypoint.sh              ALSA -> Pulse routing when PULSE_SERVER is set
  config.example.json        template for docker-config/config.json

tests/
  smoke_test.py              offline checks — no API calls
  test_redact.py             blocklist filtering, incl. split-delta cases
  test_schedules.py          schedule windows, DST, override rules, admin API

tools/
  check-audio.sh             PASS/FAIL walk of the whole capture chain
  wait-ready.sh              launchers: wait for /healthz, print fingerprint
  scan-image.sh              CVE / misconfig / secret scan of the built image
  write_config.py            writes credentials into config.json
  setup_caddy.py             panel certificate + Caddyfile
```

`write_config.py` and `setup_caddy.py` run *inside* the container, called by
setup through `docker compose run`. That is why the host needs no Python.

## Tests

The suites run against the source tree, not the image, so they need a local
virtualenv — the only reason to create one:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python tests/smoke_test.py   # config, hub, audio, routes, auth, blocklist, recording
.venv/bin/python tests/test_redact.py  # blocklist filtering, incl. split-delta cases
.venv/bin/python tests/test_schedules.py  # schedule windows, DST, override rules, admin API
```

None makes an API call. `tests/test_redact.py` includes a randomised check that
300 different delta chunkings of the same sentence all produce the identical
redacted result.

## Not in scope

Spoken output/TTS, browser microphone capture, speaker diarization, audio
recording (the transcript is recorded, the audio is not), auto source-language
detection.

## Status and licence

Built for a specific production need and shared as-is. There is no test matrix
across audio interfaces, no release process, and no support commitment — read
the [Security](#security) section before pointing it at anything that matters.

**No licence is granted.** This repository is public for reference; all rights
are reserved. If you want to use it for something, open an issue and ask.
