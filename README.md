# Relay

Server-captured, low-latency live captioning and translation for remote
viewers. One vendor (OpenAI Realtime), native audio capture, text-only output.

Viewers open a link on their phone and read. Nothing to install, no account,
no microphone permission — the audio is captured on the host machine.

Two ports, one process:

| Port | Serves |
|---|---|
| **8000** (`port`) | `/` chooser, `/transcription`, `/translation`, `/both` — the link you give the room |
| **8001** (`admin_port`) | `/admin` — the operator panel (token required) |

Admin routes are refused with a 404 on the viewer port, and the chooser page
only offers the panel when it is served on the admin port, so the audience link
never exposes it. Set `admin_port` equal to `port` in `config.json` to put
everything back on one port.

---

## Install

Requires Python 3.10 or newer.

**Setup runs once. Start runs every time.**

| | Set up | Start |
|---|---|---|
| **macOS** | double-click `setup.command` | double-click `start.command` |
| **Windows** | double-click `setup.bat` | double-click `start.bat` |

`setup.*` creates the virtualenv, installs dependencies, and asks for two
things: your **OpenAI API key** and an **admin token** of your choosing. Both
are written to `config.json` with permissions `0600`. Neither is echoed to the
screen, and neither is ever committed — `config.json` is git-ignored.

`start.*` only starts the server. If setup has not been run it says so and
stops rather than launching a half-configured relay.

Run setup again at any time to change the credentials.

<details>
<summary>Manual setup, without the launchers</summary>

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
OPENAI_KEY='sk-…' ADMIN_TOKEN='your-token' .venv/bin/python tools/write_config.py
.venv/bin/python run.py
```

The credentials go through the environment rather than the command line so
they never land in a process listing or a shell history file.
</details>

There is also a Docker path — see [Docker](#docker). Native is still the
recommendation for live events on macOS; Docker is the better answer on Linux,
and for rehearsal and CI anywhere.

## First run

1. Run `setup.command` / `setup.bat` and enter your API key and admin token.
2. Run `start.command` / `start.bat`. The console prints the LAN viewer URL,
   e.g. `http://10.0.1.42:8000/`, and the panel URL,
   `http://10.0.1.42:8001/admin`.
3. Open the panel on port 8001, sign in with your admin token, and:
   - pick the **capture device** and channel — the meter should move when
     someone speaks into it;
   - toggle on the **target languages** the room needs;
   - press **Start capture**.
4. Session health should show one `connected` session per enabled target.
   Share the LAN viewer link with the room.

> **The admin token is the only thing protecting the panel**, and the panel
> controls a session billed to your OpenAI account. Choose a real password, not
> a word. There is no default token: leave it unset and every login is refused.

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

## Docker

Reproducible builds, pinned dependencies, one command. Audio is the only part
that needs per-platform wiring, and the launchers handle it:

| Host | Start it with | Audio |
|---|---|---|
| **macOS** | `./start-docker.command` | starts a PulseAudio daemon on the Mac and bridges it in |
| **Windows 11** | `start-docker.bat` | reuses WSLg's PulseAudio, which already publishes the mic |
| **Linux** | `docker compose -f docker/docker-compose.yml -f docker/docker-compose.linux.yml up` | `/dev/snd` passed straight in |

Each launcher runs `tools/check-audio.sh` first and refuses to go live quietly if a
link in the chain is broken. Viewer link on `:8000`, panel on `:8001`, exactly
as native. State lives in `./docker-config/config.json`, so the API key and
admin token survive rebuilds.

**Why the wiring is needed:** the relay captures from a host sound card
through PortAudio, and a container only sees devices the host hands it.

### Windows: no, this does not add WSL2

Docker Desktop on Windows *already* runs on WSL2 — it is the default backend,
installed with Docker Desktop itself. `start-docker.bat` runs `docker compose`
inside that existing WSL environment, because that is where Windows 11's WSLg
publishes the microphone (as a source named `RDPSource`). Operators
double-click the `.bat`; nobody opens a Linux shell.

Windows 10 has no WSLg and cannot reach the mic this way — use `start.bat` to
run natively there.

### macOS: PulseAudio

`./start-docker.command` does this for you. To run it by hand:

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
docker compose -f docker/docker-compose.yml run --rm -e RELAY_DEMO=1 -p 8000:8000 -p 8001:8001 relay
```

No audio wiring needed, no API calls, no billed session.

### Sample rate

PortAudio reports 44100 for ALSA's `pulse` device whatever the server actually
runs at, so a 48k source would be resampled to 44.1k by PulseAudio and then to
24k by soxr — two conversions, and a slower start (~4.8s to steady state
against ~1.6s). The entrypoint therefore sets `RELAY_NATIVE_RATE=48000`, which
`app/audio.py` uses in place of the reported default. Set it to your device's
rate if that is not 48k, or to `0` to take PortAudio's default. The native run
does not set it and is unaffected.

### Caveats

Published ports, not host networking, so the console prints the container's
address rather than the LAN one — give the room the host machine's own LAN
address on `:8000`.

The macOS path adds a network hop to a pipeline tuned for latency and one more
daemon to fail on event day; for live use on a Mac the native run is still the
recommended one. Docker earns its keep on Linux, on Windows via WSLg (where
the socket is local and the hop is cheap), in CI, and for rehearsal.

`auth-anonymous=1` means anything that can reach the PulseAudio port can
listen to that microphone. Keep `auth-ip-acl` narrow and do not forward the
port beyond the host.

## Rehearsal mode

Check fonts, projector legibility and the link on every phone in the room
**without opening a billed session**:

```bash
RELAY_DEMO=1 .venv/bin/python run.py
```

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
- Viewer pages are read-only and unauthenticated — appropriate for a venue LAN.
  **Do not expose this server to the public internet as-is**: there is no TLS
  and viewer pages have no access control.
- The admin token is set by `setup.command` / `setup.bat` and stored in the
  same file. **There is no default token.** An unset token does not leave the
  panel open — every login is refused — but it does mean nobody can operate the
  relay until setup has run.
- Signing in issues a random session id; the admin token itself never goes
  into a cookie. Sessions are held in memory, so they last 12 hours, die on
  restart, and are all revoked when the token is changed. The cookie is
  `HttpOnly` and `SameSite=Strict`.
- Failed logins are delayed and logged, and an IP is locked out for five
  minutes after five failures in a row.
- Translation sessions are instructed to treat everything they hear as content
  to translate, never as instructions to follow.
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
| Edited `blocklist.txt` but nothing changed | Give it ~2 s. Check the console log line `Blocked words reloaded: N term(s)`, and that the file is the one named in the admin panel. |
| A blocked word appears inside a longer word | By design: only whole words match. Add the longer word explicitly. |
| Captions lag or arrive in long blocks | Phrase boundaries come from the model and are not tunable. Long *lines* are a line-break setting — lower *Caption line break after silence*. Genuine lag is upstream: check the host's bandwidth. |
| `CERTIFICATE_VERIFY_FAILED` | Handled via certifi. If it reappears on macOS, run `Install Certificates.command` in your Python folder. |

## Layout

```
setup.command / setup.bat    one-time setup: venv, deps, credentials
start.command / start.bat    start the server
start-docker.command / .bat  start it in Docker instead
run.py                       entry point — binds both ports, serves
requirements.txt             pinned dependencies
config.example.json          template for config.json
config.json                  live config (git-ignored, 0600, created by setup)
blocklist.txt                blocked words, one per line, hot-reloaded

app/
  main.py                    FastAPI routes, SSE, admin API
  engine.py                  capture -> sessions -> hub wiring, target toggles
  audio.py                   PortAudio capture, resample, fan-out
  realtime.py                gpt-realtime-translate sessions, reconnect, errors
  hub.py                     delta broadcast, sequencing, late-joiner history
  languages.py               source + target language table (label, ISO code)
  redact.py                  blocklist filtering, incremental-safe
  demo.py                    rehearsal mode
  config.py                  load, validate, atomic save, hot-reload
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

tools/
  check-audio.sh             PASS/FAIL walk of the whole capture chain
  write_config.py            writes credentials into config.json
```

## Tests

```bash
.venv/bin/python tests/smoke_test.py   # config, hub, audio, routes, auth, blocklist
.venv/bin/python tests/test_redact.py  # blocklist filtering, incl. split-delta cases
```

Neither makes an API call. `tests/test_redact.py` includes a randomised check that
300 different delta chunkings of the same sentence all produce the identical
redacted result.

## Not in scope

Spoken output/TTS, browser microphone capture, speaker diarization, transcript
archival, auto source-language detection.

## Status and licence

Built for a specific production need and shared as-is. There is no test matrix
across audio interfaces, no release process, and no support commitment — read
the [Security](#security) section before pointing it at anything that matters.

**No licence is granted.** This repository is public for reference; all rights
are reserved. If you want to use it for something, open an issue and ask.
