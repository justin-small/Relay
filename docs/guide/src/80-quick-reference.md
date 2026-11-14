# Quick reference card {#quick-reference}

## The Relay menu

Relay lives in the menu bar (macOS) or the system tray (Windows, Linux). Click
its icon:

| To | Choose |
|---|---|
| Open the operator panel | **Open panel** |
| Copy the link for the room | **Copy viewer link** |
| Check the certificate from another browser | **Show certificate fingerprint** |
| See what went wrong | **Open logs folder**, then `relay.log` |
| Find your settings | **Open state folder** |
| Clear a problem | **Restart Relay** |
| Install a new version | **Check for updates** |
| Start Relay with the computer | tick **Start at login** |
| Stop Relay and the captions | **Quit Relay** |

Closing the panel window does **not** stop captions. Only **Quit Relay** does.

## The two addresses

The **Viewer links** page in the panel shows both. The IP address changes
with the venue, so use what it shows today.

| | Address | Who uses it |
|---|---|---|
| **Viewer link** | `http://192.168.1.50/` | The room. Share it, or show it as a QR code. |
| **Operator panel** | `https://192.168.1.50/admin` | Operators. Sign in with your own username and password. |

On a Linux desktop, and wherever the usual ports are taken, the addresses
carry a port: `http://192.168.1.50:8080/` and `https://192.168.1.50:8443/admin`.

The first time you open the panel in a browser, check that the browser's
certificate fingerprint matches **Show certificate fingerprint**.

## Rehearsal mode

Made-up captions, no OpenAI connection, no cost. **Quit Relay** first, then:

| | Type this |
|---|---|
| **macOS** (Terminal) | `RELAY_DEMO=1 /Applications/Relay.app/Contents/MacOS/relay-desktop` |
| **Windows** (Command Prompt) | `set RELAY_DEMO=1`, then `"%LOCALAPPDATA%\Relay\relay-desktop.exe"` |
| **Linux** (terminal) | `RELAY_DEMO=1` followed by the AppImage's path, or `relay-desktop` |

To leave rehearsal mode: **Quit Relay**, then open Relay normally.

## Cost

::: warning
**Press Stop between sessions.** In the panel, press **Stop** whenever
nobody is speaking for a while. OpenAI charges for every minute that capture
is running, for each language that is turned on, even during silence. Viewers
cost nothing.
:::

## Where your settings live

Everything that belongs to you is in Relay's **state folder** (**Open state
folder** in the Relay menu):

| Computer | State folder |
|---|---|
| macOS | `~/Library/Application Support/Relay` |
| Windows | `%APPDATA%\Relay` |
| Linux desktop | `~/.local/share/relay` |
| Linux service | `/var/lib/relay` |

| File or folder | What it holds |
|---|---|
| `config.json` | Your API key and all panel settings |
| `relay.db` | The user accounts |
| `blocklist.txt` | Your blocked words |
| `certs/` | The panel's certificate and its private key |
| `recordings/` | Recorded sessions, if you turned recording on |
| `backups/` | A copy of the user accounts, made once a day (the newest 7 are kept), and a copy made before each update |
| `logs/` | Relay's log |

Updating or reinstalling Relay never touches this folder. Never share it, and
never put it in a cloud-synced folder: it holds your API key.

## After a restart

With **Start at login** ticked, Relay starts by itself when you log in. Check
the Relay menu says **Relay: running**, then open the viewer link on a phone
before the event.
