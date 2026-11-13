<picture>
  <source media="(prefers-color-scheme: dark)" srcset="brand/export/svg/relay-lockup-on-dark.svg">
  <img src="brand/export/svg/relay-lockup-on-light.svg" alt="Relay" height="56">
</picture>

# Relay

Server-captured, low-latency live captioning and translation for remote
viewers. One vendor (OpenAI Realtime), native audio capture, text-only output.

Viewers open a link on their phone and read. Nothing to install, no account,
no microphone permission. The audio is captured on the host machine.

Two sockets, one process, nothing in front of it:

| | Default | Serves |
|---|---|---|
| **viewers** | `0.0.0.0:80` | `/` chooser, `/transcription`, `/translation`, `/both`: the link you give the room (plain HTTP) |
| **operator panel** | `0.0.0.0:443` | `/admin`, over **HTTPS only**, with a certificate Relay makes itself |

The panel carries the OpenAI key and everyone's sign-in, so it is never served in
cleartext: its socket always speaks TLS, and `http://<address>/admin` is refused on the
viewer port (admin routes answer 404 there, and the chooser only offers the panel when it
is served on the admin port). `port` and `admin_port` in `config.json` change the ports; they
cannot be the same. If port 80 or 443 cannot be opened (already in use, or not permitted)
Relay uses **8080 / 8443** instead, says so on the *Viewer links* page, and puts the port in
every viewer link and in the mDNS record. Set `admin_host` to `127.0.0.1` to keep the panel
off the network altogether.

### The panel certificate

Relay mints a self-signed certificate (`certs/` in the state folder, key `0600`, never
logged) covering the machine's LAN addresses, its host name and an optional `admin_fqdn`
from `config.json`. It is reused while it still covers those names and has more than 30
days left, so a restart keeps the same fingerprint. When the LAN address changes to one it
does not cover (a laptop on a new Wi-Fi network) a new one is minted at once, without a
restart, and the *Viewer links* page says the fingerprint changed.

The browser warns once. Check the SHA-256 fingerprint it shows against the one Relay prints at
start-up, shows on the *Viewer links* page, or prints on demand:

```bash
python run.py --fingerprint
```

Per-OS notes for ports 80 and 443:

- **Windows.** The first run raises a Windows Defender Firewall prompt; allow *Private
  networks* or other machines cannot reach either port. Ports 80 and 443 are often taken by
  IIS or another web server; Relay then falls back to 8080 / 8443.
- **macOS.** The first run asks to allow *local network* access (and, in a packaged app, to
  accept incoming connections); refusing it blocks other computers, not this one. Ports below
  1024 need no privileges on macOS 10.14 and later.
- **Linux.** An unprivileged account cannot open ports below 1024, so Relay falls back to
  8080 / 8443. To use 80 and 443, either run
  `sudo setcap 'cap_net_bind_service=+ep' $(readlink -f $(which python3))`, lower
  `net.ipv4.ip_unprivileged_port_start` to 80, or run it from a systemd unit with
  `AmbientCapabilities=CAP_NET_BIND_SERVICE`. Do not run it as root. The firewall (`ufw`,
  `firewalld`) must allow the two ports.

These notes follow each platform's documented behaviour; the Windows and Linux prompts have not yet been
observed on a clean install.

---

## Run it natively

Relay runs straight from Python on Windows, macOS and Linux, with no container:

```bash
pip install -r requirements.txt
python run.py
```

Then open <https://127.0.0.1/admin> **on that computer** (<https://127.0.0.1:8443/admin> if Relay
fell back to the other ports; the start-up message prints the real address, and the browser warns once
about the self-signed certificate). The first-run page asks for
your OpenAI API key (optional here; it can be added later in Settings) and creates the
first Owner account. Until that account exists the page answers only to the machine
itself, so nobody else on the network can claim it. Viewer links are worked out from
the machine's own network addresses and follow a change of network.

Audio comes straight from the sound card through PortAudio (WASAPI, Core Audio,
ALSA or PipeWire) under the names the OS gives the devices. On Linux install
`libportaudio2` and run a sound server. If the OS has blocked microphone access, grant
it to your terminal or Python in the system's privacy settings.

Everything Relay writes lives in one per-user folder, never in the install directory:
`%APPDATA%\Relay` on Windows, `~/Library/Application Support/Relay` on macOS and
`$XDG_DATA_HOME/relay` (default `~/.local/share/relay`) on Linux. Set `RELAY_HOME` to move it.

**Relay runs as the logged-in user.** There is no container around it: it can read
and write whatever you can, and it holds your OpenAI key in `config.json` (owner-only
permissions) in that folder. Run it from an account you would trust with that key,
and do not run it as an administrator or root.

**Moving from a Docker install.** Stop the old Relay, then on Windows run
`powershell -ExecutionPolicy Bypass -File tools\migrate-from-docker.ps1`. It copies
`config.json` (your key and settings), `relay.db` (accounts), `blocklist.txt`, `recordings\`,
`backups\` and `panel-firmware\` from the old `docker-config\` folder (or straight out of the
container) into the state folder, and leaves the Docker install untouched. Pass `-Source <folder>`
to name the folder yourself, and `-Force` to replace an existing native config.

## First run

1. `pip install -r requirements.txt && python run.py`.
2. Open <https://127.0.0.1/admin> on that computer. Enter your OpenAI API key
   (or add it later in Settings) and create the first account, the Owner. You are
   signed in when it is done.
3. In the panel:
   - pick the **capture device** and channel (the meter should move when
     someone speaks into it);
   - toggle on the **target languages** the room needs;
   - press **Start capture**.
4. Session health should show one `connected` session per enabled target.
   Share the viewer link from the panel with the room.

> **Your password is what protects the panel**, and the panel controls a
> session billed to your OpenAI account. Choose a real password, not a word.
> Until the first Owner exists the setup page answers only to this computer.

## Users and roles

Everyone signs in to the operator panel as themselves, with a username and a
password. Each account has one of four roles, ranked **Viewer**, **Operator**,
**Admin**, **Owner**. A role is a set of permissions, one per action, and the
server checks the permission on every admin API route. The panel also hides
whatever a role may not use.

**First run.** Open the panel on the computer Relay runs on and it asks for an
OpenAI key (optional) and then a username, your name and a password. That creates
the first Owner and signs you in. Until that account exists the page is reachable
only from this machine. Usernames are 3 to 32 characters (letters, digits, dot,
dash, underscore, lowercase); passwords are 8 to 200 characters.

**What each role can do.** Each role can do everything the roles to its left
can, and more.

| Area | Viewer | Operator | Admin | Owner |
|---|---|---|---|---|
| Session health and status | see | see | see | see |
| Start and stop captions, target languages | no | yes | yes | yes |
| Overlays | see | create, change, delete | same | same |
| Recordings | no | list and download | also delete, change settings | same |
| Panels, alerts, schedules | see | see | change (panels: firmware, flash, reboot, forget) | same |
| Audio device, input channel, source language | no | no | change | same |
| Blocked words | no | no | edit | edit |
| Appearance, tuning, realtime settings | read (secrets hidden) | read | change | change |
| OpenAI API key (Credentials) | no | no | no | change |
| Modules | see which exist and their health | see | switch on and off, run a test | same |
| ProPresenter, Planning Center | see | also control live actions | also change setup | same |
| Users and trusted devices | no | no | see, create, change, reset, trust devices | same |

Each module declares its own permissions (see [docs/modules.md](docs/modules.md)).

**Adding people.** An Admin or Owner opens **Users** and creates the account
with a role and a temporary password. They give the person that password, and
the person must change it at their first sign-in. There is no email. If someone
forgets their password, an admin resets it: they get a new temporary password,
are signed out everywhere, and their PIN is cleared. Turning off or deleting an
account ends its sessions immediately.

**Rules.** You cannot change an account ranked above yours, or give anyone a
role above your own, so an Admin cannot touch an Owner. The last active Owner
cannot be deleted, turned off or demoted. You cannot turn off or delete your
own account.

**PIN and trusted devices.** A person may set a 4 to 8 digit PIN from their
account (it asks for their password). A PIN works only in a browser an admin has
marked as a trusted device, such as the sound booth computer: the admin signs in
there, opens **Users**, and trusts that browser under a name. On a trusted
browser the sign-in page lists the people who have a PIN, with a link back to
the password form. Five wrong PINs in a row clear that person's PIN, and they
must sign in with their password and set a new one. Removing a trusted device
ends the PIN sessions that came from it. A password sign-in lasts 30 days, a PIN
sign-in 12 hours.

Display panels, alert senders and the other devices on the viewer port
authenticate as before. Devices are not users.

**Anything that sent `x-admin-token` stops working** once the first Owner
exists. That includes the Bitfocus Companion module (see `COMPANION_MODULE.md`)
and `docs/guide/tools/capture.mjs` (which now signs in with a username and
password). Until per-user API tokens exist
([#174](https://github.com/justin-small/Relay/issues/174)), Companion cannot
sign in. Also not built yet: an audit log
([#172](https://github.com/justin-small/Relay/issues/172)), per-user
preferences ([#173](https://github.com/justin-small/Relay/issues/173)) and
sign-ins for people who never open the admin page
([#175](https://github.com/justin-small/Relay/issues/175)). Single sign-on
would come later, as a module.

### If nobody can sign in

If the only Owner is locked out or has forgotten their password, run this on the
Relay computer:

```bash
python tools/reset_owner.py <username>
```

It creates `<username>` as an Owner, or, if that account exists, turns it on and
makes it an Owner. It asks for a new password twice, without showing it, then
ends their sessions and clears their PIN. Sign in with that password. It opens
the `relay.db` the app uses (beside `config.json` in the state folder, or the file
`RELAY_DATABASE` names) and needs that file to be openable.

## Target languages

The relay runs on `gpt-realtime-translate`, a streaming interpreter model. One
session per target carries **both** feeds, the English transcript and that
target's translation, so there is no separate transcription session to run or
pay for. Billing is per minute of audio, per open session.

Twelve targets are available (the 13 output languages the model supports, minus
the English source): Spanish, Portuguese, French, German, Italian, Russian,
Chinese, Japanese, Korean, Hindi, Indonesian, Vietnamese.

Two things follow from the model:

- **No dialect or register control.** The model takes an output language and
  nothing else: no prompt, no voice, no formality setting. The old
  `es-419 / es-MX / es-CA` selector is gone; a `variant` left in `config.json`
  is ignored. Spanish comes out in a neutral Latin American register.
- **A target must be enabled for anything to appear**, including
  `/transcription`. The English transcript is produced by the translation
  session, so with every target off both feeds are silent. The panel says so.

## Blocked words

Blocked words live in **`blocklist.txt` in the state folder**, one word or phrase per
line:

```
damn
hell
off the record
mierda
hijo de puta
```

On first start the file is copied from `blocklist.txt` in the Relay folder.
**Save it and the change is live within about two seconds, with no restart, so
it is safe to edit mid-event.** The admin panel shows
the same list and can edit it, but the file is the source of truth.

Matches are **removed** from the English transcript and from every
translation. There are no asterisks and no gap on screen.

- Case and accents are ignored: `nino` matches `Niño`, `cabron` matches `cabrón`.
- Whole words only: `ass` will not touch `class`.
- Multi-word phrases work: `off the record` removes all three words.
- Duplicates are collapsed automatically, so repeating a term across sections
  of the file is harmless.
- Lines starting with `#` are comments and are preserved when the panel saves.
- Comma-separated input works too, if you'd rather paste a list in one line.

**Each feed is filtered independently.** A word blocked in English does not
block its translation, because the translation is produced from audio, not from
the English text. To remove a term everywhere, list it in each language.

Because captions stream in fragments, a banned word can arrive split across
deltas (`"dam"` then `"n"`). The filter holds back any trailing text that could
still grow into a blocked term, so a partial word never renders and then
vanishes. The hold is narrow: measured against a 464-term list, roughly 0.2%
of words are briefly held, so latency is unaffected.

One caveat: the filter removes words, it does not rewrite grammar. Removing a
noun mid-sentence leaves a gap ("the first item on is the review"). For a word
you expect often, blocking the phrase around it usually reads better.

## Recording and export

**Off by default.** Turn it on in the panel under *Recordings & export*, or set
`recording.enabled` in `config.json`. Nothing is written to disk until you do.

A recording covers **exactly one run, from *Start capture* to *Stop***. Each
committed caption line is appended as it happens, so a crash or a power cut
mid-event still leaves you everything said up to that point. Blocked words are
removed before anything is written, so a term on the blocklist never reaches
the file.

Runs land in `recordings/` in the state folder, one
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
| `finetune-<TARGET>.jsonl` | The confident pairs only, in OpenAI chat format, ready to upload to a fine-tuning job as-is. |

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
| `unpaired` | Nothing plausible on the other side, usually a dropout. |

**Only `exact` rows are carried into the fine-tuning file.** The rest stay in
the pairs file, where they can be reviewed, corrected or ignored. The model is
interpreting, so a translation routinely lands just *after* the sentence it
answers with no overlap at all. That case is matched to the source line that
had just finished, which also stops the *next* sentence being swept in when a
speaker pauses briefly.

### Retention

`recording.keep_runs` (default 20) caps how many runs are kept. The oldest are
pruned when a new run starts, so a machine left running a season of events
cannot fill its disk. Individual runs can be deleted from the panel; the run
currently being recorded cannot.

Recordings are a transcript of everything said in the room. `recordings/` is
git-ignored, and the real one lives in the state folder, outside the install.

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

## Database and backups

The user accounts are kept in one SQLite file, `relay.db`, beside `config.json`.
There is no database server to install, start or watch, and nothing listens on
the network for it.

**Where the data is.**

| | |
|---|---|
| Live database | `relay.db` in the state folder; `RELAY_DATABASE` names another file |
| Backups | `backups/` in the state folder |

The file is created with mode `0600`. It runs in WAL mode, so `relay.db-wal` and
`relay.db-shm` may sit beside it while Relay is running; they belong to it.

**Daily backups.** Relay checks a minute after it starts and then every hour, and
takes a backup if none was taken in the last 24 hours. It uses SQLite's online
backup, so the copy is consistent while Relay is running, written to a temporary
name and renamed into place, as one self-contained file named
`relay-YYYYMMDD-HHMMSS.db` (the time is UTC, mode `0600`). The newest 7 are kept
and older ones are deleted. A backup that fails is logged and tried again an hour
later.

**Backing up.** Copy the state folder: it holds the database, the backups and the
API key, so it is a complete backup. To take one on demand,
for example before an upgrade:

```bash
python -c "from app import db; print(db.backup())"
```

A name that does not look like `relay-YYYYMMDD-HHMMSS.db` is never deleted by the
daily cleanup.

**Restoring a backup by hand.** This replaces the accounts with what the backup
held, so everyone's accounts and sessions go back to that moment. Stop Relay,
then:

```bash
cd <the state folder>
rm -f relay.db relay.db-wal relay.db-shm
cp backups/relay-20261112-030000.db relay.db
```

then start Relay again.

**Starting over.** Stop Relay, delete `relay.db` in the state folder (and any
`relay.db-wal` and `relay.db-shm`), then start Relay. It starts with an empty
database and asks for the first Owner again. The backups are untouched.

**When the database cannot be opened.** A corrupt file, a full or read-only
disk, or the wrong owner: captions, viewer pages, panels and schedules keep
running. The operator panel says **The user database is not available** and stays
closed until it can be opened, for everyone, including people already signed in.
Relay tries again every few seconds. The reason is in Relay's console output, in lines that start with `relay:`. Files written by another uid are
the usual cause (files written by another account, or by a run as administrator); restoring a backup
fixes a corrupt file.

**Looking inside.** `sqlite3 relay.db` in the state folder, if you have
it. Change accounts in the panel, not by hand.

## Rehearsal mode

Check fonts, projector legibility and the link on every phone in the room
**without opening a billed session**:

```bash
RELAY_DEMO=1 python run.py         # on Windows: set RELAY_DEMO=1 first
```

`RELAY_DEMO` is read from the environment.

Canned captions stream to the viewer pages. No OpenAI connection is opened.

## Cost

Cost is `(enabled targets) × (time)`, billed per minute of audio, including
silence, because the sessions stay open by design. One session per target carries both
the English transcript and that target's translation, so running Spanish is one
stream, not two. Viewers cost nothing.

In **Text** translation mode there is one transcription session and a text
model translates each finished line, so extra languages cost almost nothing;
captions then arrive a line at a time.

**Press Stop between sessions.** Nothing else controls the cost.

## Tuning line breaks

**Phrase boundaries are not tunable.** `gpt-realtime-translate` decides where a
phrase ends from the audio itself, and the translations endpoint has no turn
detection to configure: there is no `semantic_vad`, no `eagerness`, no
millisecond threshold. A translation session has no turn lifecycle at all;
sending `turn_detection` is rejected outright. If you have an older
`config.json` carrying `vad_mode`, `vad_eagerness` or the `vad_*_ms` fields,
they do nothing and are dropped the next time the file is saved.

What you *can* steer is when this app commits a caption **line**: the point at
which streaming text stops changing and settles. Both controls are in the panel
under *Appearance & tuning*:

- **Caption line break after silence** (`realtime.segment_idle_s`, default
  1 s). A line is committed once its text has been quiet this long **and** it
  ends on a sentence boundary. Lower it for snappier lines.
- **Force a line break after** (`realtime.segment_max_idle_s`, default 10 s).
  The backstop for a line that never reaches a sentence end, always applied at a
  word boundary so a word is never cut in half. Raise it if you see one sentence
  split across two lines.

In-progress text is already on screen as it streams, so a generous backstop
costs nothing: it changes when a line *settles*, not when it appears. Changing
either restarts live sessions.

If captions arrive late rather than in awkward chunks, the cause is upstream.
Check the host's bandwidth and the session health table, not these two fields.

## Audio decisions

- **24 kHz / 16-bit / mono.** The format the Realtime API ingests. Captured at
  the interface's native rate, downsampled with soxr.
- **No silence trimming.** Audio streams continuously, silence included.
  Gating risks clipping word onsets and breaking the model's context, and saves
  nothing meaningful. A local RMS check drives the admin "speaking" indicator
  only; it never sits on the path to the API.
- **The input meter reads dBFS.** −60 dBFS at the left edge, 0 at the right,
  with the −18 dBFS target band marked: aim the loudest speech at that band.
  The bar is RMS, the thin marker is a peak hold that decays at 20 dB/s, and
  the **CLIP** indicator latches red for three seconds whenever a sample hits
  the rail. Clipping is the most common cause of bad transcription from a
  console feed, and short transients are invisible on an RMS bar alone. Watch
  the LED, not the bar. The cumulative clipped-sample count sits beside it and
  resets on each Start.
- **Stereo feeds can be summed.** The channel selector offers **Mix** on any
  interface with more than one input channel, which averages them rather than
  discarding one. That is usually the right choice for a stereo board feed. Averaging,
  not adding, so the downmix cannot clip where the source channels did not.
- **Noise reduction defaults off.** The `Noise reduction` control in the admin
  panel's Audio input section sends nothing for a house console feed, which is
  already gated and gain-staged. Reduction runs before the model's own phrase
  detection, so on a conditioned board mix it can clip the start of words.
  Switch to *Room or laptop mic* (`far_field`) or *Headset / lavalier*
  (`near_field`) only when the capture really is one of those.

## Security

- The OpenAI key lives only in `config.json` on the host (mode `0600`,
  git-ignored) and is never sent to a browser. The admin panel shows only
  whether a key is set and its last four characters.
- Viewer pages are read-only and unauthenticated, and served in plain HTTP,
  which suits a venue LAN. They carry no secret, and a room full of phones
  will not install a certificate to read captions.
  **Do not expose this server to the public internet as-is**: viewer pages have
  no access control and no transport security.
- The operator panel is a different matter. It is served only over HTTPS, by Relay itself,
  on its own port (443), with a self-signed certificate whose fingerprint you check once.
  Its responses carry HSTS (397 days), `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer`, and the panel is never
  reachable in cleartext on any interface: the viewer port refuses its routes.
- If Relay ever sits behind a reverse proxy, the app reads `X-Forwarded-For` and
  `X-Forwarded-Proto`, but **only from a loopback peer**. A direct client on the LAN cannot
  forge either: not its address, to dodge the login lockout, and not the scheme, to
  influence the cookie.
- First-run setup has no default password or token. With no admin token in `config.json`
  it answers only to this computer until the first Owner exists, and once any account
  exists it is never available again. (A `config.json` that carries an `admin_token`
  still requires it on that page, and as an `x-admin-token` header until the first Owner.)
  If the user database is ever wiped (see [Database and backups](#database-and-backups)),
  first-run setup opens again.
- People sign in with a username and password. Passwords and PINs are stored
  only as salted scrypt hashes. Signing in issues a random session id; the
  password never goes into a cookie, and only a hash of the session id is
  stored. Sessions are kept in the database, so they survive a restart: 30 days
  after a password sign-in, 12 hours after a PIN sign-in. Turning off or
  deleting an account ends its sessions at once, and so does a password change
  or reset. The cookie is `HttpOnly` and `SameSite=Strict`. It is also
  `Secure` whenever the login arrived over TLS, but not on a plain-HTTP login,
  where the browser would drop a `Secure` cookie and lock the operator out.
- The server checks a permission on every admin API route, not only in the
  menus. See [Users and roles](#users-and-roles).
- Failed sign-ins, PINs and first-run attempts are delayed and logged, and an IP
  is locked out for five minutes after five failures in a row.
- The Planning Center token is stored only in `config.json` and is never
  sent to a browser, a panel or a log. Panels on the viewer port can read the
  Service feed with no login, and a panel the operator allows to control sends
  a key over plain HTTP: see *Planning Center Services LIVE*.
- Translation sessions are instructed to treat everything they hear as content
  to translate, never as instructions to follow.
- **If you ever paste an API key somewhere it should not be, revoke it** at
  <https://platform.openai.com/api-keys> rather than deleting the file. A key
  that has been written to disk, a log or a screenshot should be considered
  spent.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Incorrect API key provided` | The key is wrong. Sessions stop rather than retry. Paste the right key and press Start again. |
| Sessions say `reconnecting` and climb | Network. Reconnect is automatic with backoff; check the host's connection. |
| Meter is flat while someone speaks | Wrong device or wrong channel. Try each channel, or **Mix** on a stereo feed; press Rescan if the interface was plugged in after launch. |
| CLIP indicator keeps lighting | The feed is too hot. Reduce gain at the console or interface until the peak marker sits at the −18 dBFS band and CLIP stays dark. Clipped audio transcribes badly and no model setting recovers it. |
| Meter moves but sits far left | Input gain is too low. Below about −40 dBFS the model has little to work with; bring the loudest speech up to the marked band. |
| `No input device matched` | The interface was unplugged. Reselect it and press Start. |
| Viewers see "No target language is live" | Every target is toggled off, or capture is stopped. |
| A blocked word still appears in a translation | Each feed is filtered independently. Add that language's form to the list as well. |
| Edited `blocklist.txt` but nothing changed | Give it about 2 s. Check the relay's console output for the line `Blocked words reloaded: N term(s)`, and that the file is the one named in the admin panel. |
| A blocked word appears inside a longer word | By design: only whole words match. Add the longer word explicitly. |
| The panel says "The user database is not available" | `relay.db` cannot be opened (a corrupt file, a full or read-only disk, or the wrong owner). Captions and viewer pages are not affected. The panel opens again by itself once the database is back; if it does not, read the log (`logs.*`) for lines starting `relay:`. See [Database and backups](#database-and-backups). |
| Nobody can sign in, or the only Owner is locked out | [Recover with `tools/reset_owner.py`](#if-nobody-can-sign-in). |
| Captions lag or arrive in long blocks | Phrase boundaries come from the model and are not tunable. Long *lines* are a line-break setting: lower *Caption line break after silence*. Real lag is upstream: check the host's bandwidth. |

## Layout

```
run.py                       entry point; binds both sockets
requirements.txt             pinned dependencies
config.example.json          template, used when tests run from a checkout
blocklist.txt                default blocked words, copied to the state folder on first start

<state folder>/              per-user, outside the install (see "Run it natively");
                             git-ignored when it is a checkout's own
  config.json                live config (0600): API key, admin_fqdn
  relay.db                   the user database (0600)
  blocklist.txt              the live list: hot-reloaded, written by the panel
  certs/admin.{crt,key}      operator panel certificate (key 0600)
  recordings/                recorded runs, if recording is enabled
  backups/                   daily database copies, relay-YYYYMMDD-HHMMSS.db

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
  announce.py                announces Relay to caption panels over mDNS, started on launch
  netinfo.py                 this machine's LAN addresses, for viewer links
  db.py                      SQLite file, migrations, daily backup
  users.py                   accounts, roles, permissions, sessions, trusted devices
  migrations/                core schema, NNNN_name.sql, applied once each
  schedules.py               schedule validation, windows, DST, US formats
  overlays.py                broadcast overlay options, presets, named overlays
  scheduler.py               starts and stops capture on the schedules
  templates/                 chooser, viewer, admin panel, sign-in
  static/                    viewer + admin JS and CSS
  modules/                   integrations, each switched on and off on its own
                             (docs/modules.md); __init__.py is the registry

tests/
  smoke_test.py              offline checks, no API calls
  test_redact.py             blocklist filtering, incl. split-delta cases
  test_schedules.py          schedule windows, DST, override rules, admin API
  test_overlays.py           overlay options, presets, named overlays, page
  test_announce.py           mDNS records and answers of app/announce.py
  test_serving.py            certificate minting and reuse, port fallback, a real HTTPS panel + HTTP viewer server
  test_users.py              accounts, roles, sessions, devices, database, dumps
  test_auth.py               first run, sign-in, PIN, every admin route per role
  _db.py                     throwaway SQLite file the tests make for themselves

tools/
  migrate-from-docker.ps1    Windows: copy a Docker install's settings and data to the state folder
  reset_owner.py             lockout recovery: make someone an Owner with a new password
```

## Tests

The suites run against the source tree, not the image, so they need a local
virtualenv. That is the only reason to create one:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt httpx2
.venv/bin/python tests/smoke_test.py   # config, hub, audio, routes, auth, blocklist, recording
.venv/bin/python tests/test_redact.py  # blocklist filtering, incl. split-delta cases
.venv/bin/python tests/test_schedules.py  # schedule windows, DST, override rules, admin API
.venv/bin/python tests/test_users.py   # accounts, roles, sessions, database, backups
.venv/bin/python tests/test_auth.py    # first run, sign-in, PIN, every admin route per role
python3 tests/test_announce.py         # panel announcement records
```

Nothing needs installing for the suites: each test process makes its own
throwaway SQLite file in a temp directory and deletes it at exit.

The app opens `relay.db` beside `config.json`. Set `RELAY_DATABASE` to put the file elsewhere.

None makes an API call. `tests/test_redact.py` includes a randomised check that
300 different delta chunkings of the same sentence all produce the identical
redacted result.

`tests/test_modules.py` enforces that every integration is a module that can be
switched on and off (see [docs/modules.md](docs/modules.md)). The **Tests** workflow
runs it, and every other suite, on each pull request.

`httpx2` is test-only: starlette's `TestClient` needs it, the app does not, so
it stays out of `requirements.txt` and the image.

## Releasing

Push a version tag; [`.github/workflows/release.yml`](.github/workflows/release.yml)
does the rest:

```bash
git tag v1.2.0 && git push origin v1.2.0
```

It runs the three suites, builds the user guide with `GUIDE_VERSION` taken
from the tag, and publishes a GitHub release with `Relay-v1.2.0.zip` (a
`git archive` of the tag, so nothing gitignored ships),
`Relay-User-Guide.pdf` and `Relay-User-Guide.docx`. A tag with a hyphen
(`v1.2.0-rc.1`) becomes a pre-release, which `releases/latest` skips. Pull
requests that touch the workflow or `docs/guide/` run everything but the
publish.

The Companion module releases the same way from its own repo, with the tag
checked against `package.json` and `companion/manifest.json`.

## Caption panels

A caption panel is a small touch screen for a wall or a desk that shows
Relay's captions without a phone. It shows finished lines only, the
transcription or one translation (or two feeds side by side), follows the
live text, and lets a person scroll back and tap **Jump to live**.

**Supported board:** the Elecrow CrowPanel Advance 7.0" (DIS02170A, hardware
V1.2 or later), 800 x 480, ESP32-S3. One board, one size. The firmware is in
`firmware/relay-display/` (see its README for the pin map, building, signing
and the bench checklist); a release attaches `relay-display.bin` (first flash
over USB) and `relay-display-app.bin` (the signed update, uploaded in the
admin page).

**How a panel finds Relay:** it joins Wi-Fi from its own setup page, then
browses mDNS for Relay's announcement (next section). Typing Relay's address
into the setup page skips that and always wins.

**Ports.** A panel never uses the operator port or logs in.

| Who | Port | What |
|---|---|---|
| Panel to Relay | viewer port (80), plain HTTP | `/stream`, `/api/targets`, `/api/panels/*`, firmware download |
| Relay to the LAN | UDP 5353 (multicast) | the mDNS announcement, made by Relay itself |
| Operator to Relay | operator port (443), HTTPS | Panels section of `/admin` |
| Phone to a panel in setup | the panel's own Wi-Fi, `http://192.168.4.1/` | joining the panel to the venue Wi-Fi |

**Languages.** The caption font is Latin only, so a panel offers the
transcription and the live translations whose `script` is `latin` in
`app/languages.py` (English, Spanish, Portuguese, French, German, Italian,
Indonesian, Vietnamese and the like). Russian, Chinese, Japanese, Korean,
Hindi, Arabic and the other non-Latin languages are hidden from the panel's
menu, and Relay refuses to set one on a panel from admin. A transcription
whose spoken language is not Latin is not offered either. They still work on
phones.

A panel can also be turned 0, 90, 180 or 270 degrees (from the admin page or
its own menu; the newest change wins), restarted from admin, and set to show
the Planning Center service, the Alert buttons feed (below), or ProPresenter's lyrics or timers (see "ProPresenter").

For operators there is a chapter in the user guide
(`docs/guide/src/51-panels.md`).

## Caption panels: Relay announces itself

Touch-screen caption panels (`firmware/relay-display/`) find Relay on the LAN
without anyone typing an address. Relay announces itself over mDNS / DNS-SD:

| | |
|---|---|
| Service | `_relay-captions._tcp` |
| Instance | the install name (default: the computer's name) |
| Port | the viewer port (80, or 8080 when 80 was taken) |
| TXT | `v=<Relay version>` `name=<install name>` `path=/` |

Nothing else is announced: no address of the operator panel, no key, no
language list. A panel that finds the announcement then asks the viewer port
for captions, as any phone would.

Relay starts the announcement itself when it launches (`app/announce.py`, a background
thread), on the viewer port, and stops it when Relay exits. If the announcement cannot run
(port 5353 taken, no network) Relay logs it and carries on.

**Turn it off:** start with `RELAY_ANNOUNCE=0` (on Windows `set RELAY_ANNOUNCE=0` first). Name this install with
`RELAY_INSTALL_NAME`. Panels already holding that name stop finding it, and an
unannounced Relay still works at its address.

**What a panel does.** With no Relay address on its setup page, a panel browses
once it has Wi-Fi and connects to the first answer. It remembers that
install's name, so with **two Relays on one network it stays with the one it
first chose** and never moves to the other if the first is off. A Relay that
comes back at a new address is followed. **Typing an address** in the panel's
setup page always wins and is the way in when the network blocks multicast
(guest Wi-Fi often does). To move a panel to another Relay, give it that
Relay's address, or factory-reset it.

## Caption panels: the panel registry

Relay lists the panels it can see and can tell each one what to show. The code
is `app/panels.py`; the panel's side is `firmware/relay-display/main/panel_link.cpp`.

**Panel to Relay**, on the viewer port, plain HTTP, no login (panels do not
pair, by decision):

- `POST /api/panels/checkin` every 30 s: panel ID, name, firmware version, the
  scripts it can draw, the feed or feeds it shows, its IP, uptime and Wi-Fi
  signal.
- `GET /api/panels/{id}/events`: a server-sent event stream the panel keeps
  open. Relay writes `set_feed` and `update_available` to it, so a change
  arrives at once and not at the next check-in. It is only served to a panel
  that has checked in.

**Relay's side.** The list is in memory. A panel not heard from for 90 s shows
as offline, and is online again at its next check-in. What the *operator* sets
for a panel, its name and its feed, is saved in `config.json` under `panels`
and survives a restart; what a panel says about itself is never written there.

**Control is on the operator port.** Everything that changes a panel is under
`/api/admin/*` on the operator port, and the viewer port refuses it:

| | |
|---|---|
| `GET /api/admin/panels` | every panel: online or not, version, feeds, IP, uptime, signal |
| `POST /api/admin/panels/{id}` | `{"name": "Lobby"}` and/or `{"feeds": [{"stream": "translation", "lang": "SPANISH"}]}` (one or two feeds; `null` stops overriding). A feed is `transcription` or a `translation` with a language |
| `DELETE /api/admin/panels/{id}` | forget a panel (one that is still checking in comes straight back, unnamed) |
| `GET /api/admin/panels/firmware` | the signed panel image Relay holds, or `null` |
| `POST /api/admin/panels/firmware` | upload `relay-display-app.bin` (the raw body). Relay checks it is a panel's application with a readable version, whole, and signed at all, and refuses it with a sentence if not. Whether the signature is the right one is the panel's to say |
| `DELETE /api/admin/panels/firmware` | forget the image |
| `GET /api/admin/panels/flash` | the merged USB image (`relay-display.bin`) Relay holds for the browser flasher, or `null` |
| `POST /api/admin/panels/flash` | upload `relay-display.bin` (the raw body); the application inside it is checked as an update's image is. Kept apart from the update image and never offered to a panel |
| `DELETE /api/admin/panels/flash` | forget the USB image |
| `GET /api/admin/panels/flash/<board>.json`, `.bin` | the manifest (version, size, SHA-256) and the bytes the flasher page fetches |
| `POST /api/admin/panels/firmware/update` | `{"id": "<panel id>", "now": false}` asks one panel to install it; no `id` asks every online panel that has an update available. `now` skips the wait for a quiet moment |

**Firmware updates.** The Panels section of the admin page has an upload box for
`relay-display-app.bin` (a release publishes it, signed). A panel whose version
is older than the image shows "Update available"; **Update** installs at a quiet
moment (no new caption line for two minutes), **Update now** restarts it at once,
and each row shows the progress and the result (downloading, ready, restarting,
updated, failed with the reason, or rolled back). The image is kept beside
`config.json` in `panel-firmware/` and served to panels on the viewer port at
`/api/panels/firmware/<version>.bin`. **Images travel over plain HTTP on the LAN,
so the signature check on the panel is what keeps a stranger's image off it**:
a panel refuses anything not signed with the release key, and an image that does
not check in with Relay within ten minutes of its first boot is rolled back. The
asks are held in memory: a restart of Relay forgets them. Panels report on
`POST /api/panels/{id}/update`, bounded like a check-in.

**USB driver.** The panel's USB chip is a CH340. If the panel does not show up
in the port list, install the driver for
[macOS](https://sparks.gogo.co.nz/assets/_site_/downloads/CH341SER_MAC_V1_8.zip) or
[Windows](https://sparks.gogo.co.nz/assets/_site_/downloads/CH34x_Install_Windows_v4_0.zip),
then replug the panel.

**Flashing a new panel from the browser.** `/admin/flash` (linked from Panels)
writes a new or recovered panel's whole flash at 0x0 over USB from Chrome or
Edge, with [esptool-js](https://github.com/espressif/esptool-js) over Web
Serial; it is VXP's board flasher, copied. Its image is the merged
`relay-display.bin`, uploaded on that page. Before it writes, the page checks
the image's size and SHA-256 against Relay's manifest, that the board is an
ESP32-S3 with 16 MB of flash, and reads the firmware in the board's first slot:
a Relay panel's is rewritten without asking, anything else (blank, another
vendor's) is asked about first. It writes at 115200 (the CrowPanel's CH340
drops at higher rates), never erases, and ends once the write is checked: the
person presses RESET on the panel, because the CrowPanel does not restart from
USB. Web Serial is allowed on that one path
(`Permissions-Policy: serial=(self)`) and denied everywhere else. esptool-js is
vendored in `app/static/vendor/esptool-js/` (Apache-2.0; `VERSION` says which);
the steps are tested with `node --test tests/js/`.

**A screen choice is not undone.** The operator's feed carries a revision. The
panel reports the last one it applied (`feed_rev`) and Relay sends a `set_feed`
only while it has not, so a feed picked at the screen afterwards stands until
the operator sets another. **Newest wins, from either side.**

**What a stray device on the LAN can do.** Anything can check in as a panel or
read a panel's events: they carry a feed choice and nothing secret. It cannot
fill memory or config: at most 64 panels are held (a new one past that makes
room by dropping the stalest offline panel the operator never named, and is
refused with 429 when there is none), a check-in body over 2 KB is refused
with 413 before it is read whole, every field is length-limited, and at most
two event streams are open per panel. It cannot control anything.

Run the server's checks with `python tests/test_panels.py`.

## Planning Center Services LIVE

Relay can follow **today's service in Planning Center Services** and show it on
the caption panels: the current and next item with timers, the order of service,
song keys and arrangements, and who is running LIVE. A panel can also be allowed
to move the service on (**Next**, **Previous**, **Take control**). It is a
module ([docs/modules.md](docs/modules.md)): switch it on under **Modules** in the
operator panel. While it is off, or on with no token saved, Relay behaves exactly
as without it.

**Connecting.** One church, one **Personal Access Token**, made at
<https://api.planningcenteronline.com/personal_access_tokens> by a person who has
access to **Services**. Paste its application ID and secret into the operator
panel's **Planning Center** section, press **Test connection**, and tick the
service types to follow. The token is stored in `config.json` only; the ID and
the secret are never sent back to the browser, to a panel or to a log.

**Everything Relay does in Planning Center runs as the token's owner.** A press
of **Next** on a panel is that person pressing Next in Services LIVE. To control
LIVE that person must be allowed to control it for the service type: if they can
press Next in Services LIVE themselves, Relay can too. Without it a press reads
"Planning Center said no" on the panel.

**What Relay does** (`app/modules/planning_center/planningcenter.py`, `pclive.py`):

- Finds **today's plan** for each followed service type, by the organisation's own
  time zone, every few minutes, and re-reads it every 30 s near a service (5 min
  otherwise) so plan edits reach panels quickly. The operator can pick another plan for today; the
  pick lapses at midnight.
- **Polls LIVE**: every 2 s from an hour before a service until shortly after it
  (or while an item is open), every 3 minutes otherwise, held to half of
  Planning Center's rate limit. Planning Center's webhook list does show a LIVE
  event (`services.v2.events.plan.live.updated`), but Relay sits on the LAN and
  cannot receive it, so it polls.
- Works out the **timers** itself: item time left, and how far ahead or behind
  the service is.

**What panels get** (`app/modules/planning_center/servicefeed.py`): `GET /stream?stream=service&type=<id>`
on the viewer port, the whole state per message, a new message only when
something shown changes, and a tick every 15 s. Followed service types are listed
under `service` in `/api/targets`.

**Per panel, in admin** (each panel's **Service** button): **Off** (the default:
no Service feed in its menu), **Show only**, **Control only**, or **Show and
control**, and which service type it follows. **Item notes** reach panels only
while **Show item notes on panels** is on (off by default), because notes can
hold names and pastoral details.

**Panel control** (`app/modules/planning_center/pccontrol.py`): `POST /api/panels/<id>/service/<next|previous|take_control|release>`
on the viewer port with the panel's key. Relay checks the panel's mode, its
random 128-bit key (made when control is turned on, dropped when it is turned
off), one press a second per panel, and a followed type with a plan today, then
presses the button in Planning Center and shows the result to every panel.
Presses are listed in admin under **Presses from panels**.

**What anyone on the LAN can reach.** The Service feed is on the viewer port with **no login**, like the captions:
anyone on the venue network can read the plan title, the song names and, while
the switch is on, the item notes. The control key travels over **plain HTTP**:
it stops casual misuse, not someone who can read the network. Give control only
to panels in places you trust, on a network you trust.

`tools/check_planningcenter.py --live` checks a token against the real API,
read-only, from a git-ignored `planningcenter-test.env`. For operators there is
a chapter in the user guide (`docs/guide/src/52-planning-center.md`).

## Alerts

A musician taps a button on `/alert` (viewer port, no login: anyone with the
link can send) or on a panel set to the **Alert buttons** feed, and the
panels and browser screens that button lists flash red and white once a
second until someone taps one. The first tap anywhere acknowledges for all;
with no tap the alert ends after the auto-stop (30 s to 10 min, default 2 min)
and the sender sees "Not seen". Buttons (up to 16, each with an icon, panels
by id and screens by name) are set under **Alerts** in admin and kept in
`config.json`. A browser screen flashes when opened as `/screen?alert=<name>`.
Code: `app/alerts.py`, `app/static/alert*.js`, `tests/test_alerts.py`; panel
side in `firmware/relay-display/main/alert_*`. For operators there is a chapter
in the user guide (`docs/guide/src/53-alerts.md`).

## ProPresenter

Relay follows ProPresenter's current slide (`app/modules/propresenter/propresenter.py`; part of
issue 138). It is a module ([docs/modules.md](docs/modules.md)): switch it on under
**Modules** in admin; while it is off Relay never contacts ProPresenter. Under **ProPresenter** in admin: the machine's address and the
port set in ProPresenter's Network preferences (no default is assumed), a
**Test connection** button (`GET /version`), mDNS discovery
(`_proapiv1ws._tcp`, standard library only), a **view-only** switch, the lyrics feed switches and a stage message.

- One `POST /v1/status/updates` connection with a fixed path list; messages
  are split on the blank line, not on HTTP chunks. Because a live
  ProPresenter does not push every slide change, the slide is re-read with
  `GET /v1/status/slide` on any slide signal and about once a second, and a
  result with the same text is ignored. No presentation means no lyrics. The
  `timer/system_time` heartbeat (every 1 to 3 s) going quiet for 10 s drops
  and reopens the connection with backoff. Two misses in a row mark it down,
  announced once, and recovery once. What a live install does is in
  `docs/reference/propresenter-live-test.md`.
- **One client.** Every call to ProPresenter goes through
  `propresenter.Client`, which refuses every write in view-only mode: `read()`
  (GET, refused for a path that acts), `action()` (a GET that triggers or
  clears), `put()`, `post()`, `delete()`.
- **Lyrics feed.** `GET /stream?stream=lyrics&for=viewer|screen|overlay|panel`
  on the viewer port, whole state in each message, repeated at the heartbeat:
  `{"type":"lyrics","enabled":true,"live":true,"song":true,"title":"...",
  "part":"Verse 1","index":3,"text":"line1\nline2","next":"...","rev":12,
  "server_time":...}`. Each audience is the operator's switch; the phone
  viewer is off by default because song lyrics are licensed, and a switched
  off audience is refused with 403. `/screen?lyrics=1` and `/overlay?lyrics=1`
  show it. The viewer port has no login, so the switch decides what Relay
  offers, not who may ask.
- **Stage message.** `PUT /v1/stage/message` with a bare JSON string,
  `DELETE` to clear. An Alerts button with **Also show on the ProPresenter
  stage display** sets it when the alert starts and clears it when the alert
  ends, unless something else has replaced the message since.
- ProPresenter's API has no authentication: Relay only talks out to the
  address typed in admin. Allow the port in the ProPresenter machine's
  firewall. Standard library only (`http.client`), like Planning Center.

- **Control (stage 2).** `app/modules/propresenter/ppcontrol.py` and `app/modules/propresenter/static/ppcontrol.js`
  (admin page *ProPresenter control*, plus a clear dock on every admin page):
  next/previous, the slide grid, clears, playlists, looks, macros, timers,
  messages, media position. All writes go through `propresenter.Client`
  (view-only refuses them there, the routes answer 409); `Client.action`
  refuses the bulk `timers/start|stop|reset` and `capture/start` outright.
  Thumbnails are proxied behind the admin login and cached in memory by
  presentation + index + a hash of the slides. Macros: `macros_mode`
  (`selected` or `all`) and `macros_allowed` (UUIDs) in the `propresenter`
  config block; the server checks them on every trigger. The media position
  is `PUT /v1/transport/presentation/time` with a bare number (the issue
  said `go_to_time`; the published description says `time`).

- **Panels (stage 3).** A caption panel can be set to **Lyrics** (the stream
  above, `for=panel`) or **Timers**, which are new firmware views
  (`firmware/relay-display`, "ProPresenter: lyrics and timers"). Both are
  offered in `/api/targets` and the caption `targets` message as
  `"propresenter":{"lyrics":true,"timers":true}` (lyrics only while
  `lyrics.panels` is on; timers while ProPresenter is set up), and as panel
  feeds `{"stream":"lyrics"}` and `{"stream":"timers"}`.
  `GET /stream?stream=pptimers&for=panel[&panel=<panel id>]` carries the whole
  state of up to four timers in each message, sent when a timer is started,
  stopped, reset, renamed or changed in ProPresenter (never once a second) and
  at the heartbeat: `{"type":"pptimers","enabled":true,"live":true,"amber_s":120,
  "red_s":30,"timers":[{"id":"...","name":"...","state":"running","dir":"down",
  "value":298.0,"level":"ok"}],"rev":12,"server_time":...}`. `value` is the
  seconds as of `server_time` (negative while a countdown overruns) and the
  panel counts it itself, down for `dir` `down` and up for `up` (read from
  `GET /v1/timers`, re-read now and then). The thresholds (`propresenter.timers`:
  `amber_s`, `red_s`, `show`; defaults 2:00, 0:30, every timer) are global and a
  panel can override each in its own settings (the `timers` block of the panel's
  entry, set with `POST /api/admin/panels/<id>`); the panel names itself in the
  request. The viewer port has no login, so the panel id picks settings and is
  not a credential.

- **Slide-note tags (stage 5).** `app/modules/propresenter/slidetags.py`; settings `propresenter.tags`
  (`enabled`, default **off**; `settle_s`, default 1.5, 0.2 to 30). A tag in a slide's notes,
  `[relay:action]` or `[relay:action=value]`, runs a Relay action once the slide has stayed live for
  `settle_s`; `[relay-deck:...]` in any slide's notes is the default for slides of that presentation that
  have no `[relay:...]` tag. Actions: `capture-start`, `capture-stop` (Relay's own captioning, through the
  same `scheduler.manual_*` and `engine.start/stop` as the Start and Stop buttons; ProPresenter's
  `capture/start` is never called), `lyrics` and `captions` (`panels.Registry.show_lyrics/show_captions`:
  a `set_feed` that is not stored, so the operator's own choice stays), `lang=<code or name>`
  (`show_language`, validated against `languages.py` and the enabled targets) and `alert=<button name>`
  (`Alerts.raise_named`). The same resolved set does not run twice in a row. The follower hands `observe` the
  live slide's notes (from `status/slide`) and the deck's tags, parsed from the one
  `presentation/{uuid}` read made when the presentation changes and cached by uuid and a hash of the notes.
  Nothing is written to ProPresenter, so view-only does not apply. `GET /api/admin/propresenter` carries
  `tags`: `seen`, `fired` and `actions`. Tests: `tests/test_slidetags.py`, and the follower side in
  `tests/test_propresenter.py`. Tag reference: the guide's ProPresenter chapter.

- **Live state and playlist details (stage 4).** The follower also follows
  `announcement/active` and `announcement/slide_index` (position read every
  second, the announcement itself only when it changes), `status/screens`,
  `status/audience_screens` and `status/stage_screens` (re-read every few
  seconds), and keeps each cue's part name, colour and label, so the admin
  state has `item` (title, part and colour, slide number, notes of this and the
  next slide, active playlist item), `announcement` and `screens`.
  `POST /api/admin/propresenter/screens` (`{"kind":"audience"|"stage","on":bool}`)
  is a `PUT /v1/status/{kind}_screens` with a bare boolean; `POST .../item`
  (`{"direction":"next"|"previous"}`) goes to
  `playlist/{active playlist}/{direction}/trigger`, or the focused playlist
  without one. **Not** `presentation/active/next/trigger`: that is the next
  cue, and moves a slide (tried live). Playlist thumbnails are
  `GET .../playlists/{id}/thumbnail/{item}/{cue}?v=` (ProPresenter's
  `playlist/{id}/{item}/thumbnail/{cue}`, drawn for the item's arrangement),
  cached with the presentation thumbnails; `.../playlists/{id}/items/{item}/cues`
  lists an item's cues in its arrangement (the same cue list as the slide grid,
  `propresenter.cue_list`), and `.../playlists/{id}/diagnostics` counts the
  items and checks each presentation exists.

- **The Planning Center link (stage 6).** `app/modules/propresenter/plmatch.py` (pure matching rules), `app/modules/propresenter/pclink.py`
  (reads ProPresenter, caches, follows, drives), settings `propresenter.link` (`follow`, `drive`, both
  default **off**; `dwell_s` default 10, 1 to 300; `allow_back`; `service_type`; `threshold`; `mapping`;
  `owned`, set by Relay only). Matching order per plan item: the operator's pairing (`mapping`, kept by
  normalised title), ProPresenter's playlist synced from Planning Center (the `is_pco` playlist that fits
  the plan best, in its order; an unlinked entry is a `placeholder`, never guessed), the same title once
  normalised (`plmatch.normalise`: case, accents, punctuation, "(Live)", key and arrangement suffixes),
  then the closest title at or above `threshold` and clearly ahead of the runner-up (a tie is `ambiguous`).
  `POST /api/admin/propresenter/link/playlist` (the operator's click) builds the one playlist Relay made,
  `Relay – <plan date>`: `POST /v1/playlists` once, then `PUT /v1/playlist/{uuid}` with the matched
  presentations in order, read back to verify. The UUID is kept in `link.owned`; a write checks it and the
  name prefix, and a same-named playlist Relay did not make is refused. Never any other playlist. View-only
  answers 409 from `propresenter.Client`. `drive`: after a presentation has been the active one for
  `dwell_s` seconds, `pccontrol.Control.drive` reads LIVE fresh, does nothing unless the token's own person
  is the controller, and presses Next (or Previous with `allow_back`) until LIVE is on the matched item,
  stopping on an overshoot or after 12 presses; one try per activation. It uses the panels' press path and
  lock, and each outcome is logged as a `ProPresenter` press. Routes under `/api/admin/propresenter/link`
  (state, presentations, refresh, mapping, playlist); the Service feed message gains `pp_item` and
  `pp_agree` while `follow` or `drive` is on. Tests: `tests/test_pclink.py`.

- **Lobby announcements and the slide view (stage 7).** `app/modules/propresenter/pplobby.py` (the rotation), `app/modules/propresenter/ppview.py` (the
  slide view's data and images), `app/modules/propresenter/static/slideview.js` + `slideview.css` (the view, shared by the admin page
  *Slide view and lobby* and by `/stage`), `app/modules/propresenter/static/pplive.js` (the admin page's controls). Settings
  `propresenter.lobby` (`playlist`, `interval_s` default 8, 3 to 600, `loop` default on) and `propresenter.view`
  (`timer`, `public` default **off**). A rotation is never a setting: it runs only after
  `POST /api/admin/propresenter/lobby/start` (so Relay never starts one on its own, and a restart of Relay does
  not resume it); `.../lobby/stop` (`{"clear": true}` also clears the announcements layer), `.../lobby/resume`,
  `GET .../lobby`. ProPresenter does not say which playlists are announcement playlists, so the operator picks
  one. Each step is `GET /v1/announcement/active/{n}/trigger` (next cue) or `GET /v1/playlist/{id}/{index}/trigger`
  (next item) through `propresenter.Client` (view-only answers 409). The tick compares the announcement the
  follower reads (`status/layers.announcements`, `announcement/active`, `announcement/slide_index`) with what
  Relay put up: a slide of one of the playlist's own presentations the operator moved to is adopted; anything
  else pauses the rotation (no writes) until the layer has been empty 5 s; a cleared layer pauses until Resume.
  ProPresenter dropping off holds it (nothing written); on return an empty layer restarts the playlist.
  Switching ProPresenter off, picking another playlist, view-only or `lobby.cancel()` at shutdown ends it.
  The slide view: `GET /api/admin/propresenter/live` (slide, next, the chosen timer's value and the video's time
  and duration, as of the read) and `.../live/image/{current|next}?v=` (ProPresenter's thumbnail at 960 px, the
  same cache as the grid; only the slide that is up, a stale `v` is a 404). The follower now reads
  `transport/presentation/time` and `.../current` with the slide each second, only while the slide or media layer
  has content. The same view is open without the admin login at `/stage` (`/api/stage/live`) only when
  `view.public` is on (default off, 404 otherwise). Tests: `tests/test_pclobby.py`.

Tests: `tests/test_propresenter.py` (a fake ProPresenter on localhost), and for
the panel views `firmware/relay-display/tests/test_propresenter_view.py`. For
operators there is a chapter in the user guide
(`docs/guide/src/54-propresenter.md`).

## Not in scope

Spoken output/TTS, browser microphone capture, speaker diarization, audio
recording (the transcript is recorded, the audio is not), auto source-language
detection.

## Status and licence

Built for a specific production need and shared as-is. There is no test matrix
across audio interfaces and no support commitment. Read the
[Security](#security) section before pointing it at anything that matters.

**No licence is granted.** This repository is public for reference; all rights
are reserved. If you want to use it for something, open an issue and ask.
