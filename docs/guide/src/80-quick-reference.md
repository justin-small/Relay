# Quick reference card {#quick-reference}

## The scripts

**Setup runs once. Start runs every time.**

| | macOS | Windows 10 / 11 | Linux |
|---|---|---|---|
| **Set up** (once) | double-click `setup.command` | double-click `setup.bat` | `./setup.sh` |
| **Start** (every event) | double-click `start.command` | double-click `start.bat` | `./start.sh` |
| **Stop** (after every event) | double-click `stop.command` | double-click `stop.bat` | `./stop.sh` |
| **Show the log** | double-click `logs.command` | double-click `logs.bat` | `./logs.sh` |

Before you start: Docker must be running (the whale icon on macOS and
Windows).

## The two addresses

Start prints both. The IP address changes with the venue, so use the ones
printed today.

| | Address | Who uses it |
|---|---|---|
| **Viewer link** | `http://192.168.1.50/` | The room. Share it, or show it as a QR code. |
| **Operator panel** | `https://192.168.1.50/admin` | You only. Sign in with your admin token. |

The first time you open the panel, check that the browser's certificate
fingerprint matches the `SHA-256` line that start printed.

## Rehearsal mode

Made-up captions, no OpenAI connection, no cost. Stop Relay first.

| | Type this |
|---|---|
| **macOS** (Terminal) | `cd ~/Relay` then `RELAY_DEMO=1 ./start.command` |
| **Windows** (Command Prompt) | `cd /d C:\Relay`, `set RELAY_DEMO=1`, then `start.bat` |
| **Linux** (terminal) | `cd ~/Relay` then `RELAY_DEMO=1 ./start.sh` |

To leave rehearsal mode: stop, then start normally.

## Cost

::: warning
**Press Stop between sessions.** In the panel, press **Stop** whenever
nobody is speaking for a while. OpenAI charges for every minute that capture
is running, for each language that is turned on, even during silence. Viewers
cost nothing.
:::

## Where your settings live

Everything that belongs to you is in the `docker-config` folder inside the
Relay folder:

| File or folder | What it holds |
|---|---|
| `config.json` | Your API key, admin token and all panel settings |
| `blocklist.txt` | Your blocked words |
| `certs/` | The panel's certificate and its private key |
| `Caddyfile` | The web server settings, made by setup |
| `recordings/` | Recorded sessions, if you turned recording on |

Keep this folder when you update Relay. Never share it, and never put it in a
cloud-synced folder: it holds your API key.

## After a restart

- **macOS:** always double-click `start.command` again. It restarts the
  microphone bridge.
- **Windows and Linux:** run start again to check the audio and see the
  current addresses.
