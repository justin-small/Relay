# Upgrading from Relay 2.0 {#upgrading}

Relay 2.0 ran in Docker. The desktop app does not need Docker, Caddy or a
PulseAudio daemon, and it can bring your settings with it, so you do not retype
your OpenAI key or lose recordings. This chapter moves a 2.0 install over and
then removes what you no longer need.

Your old folder is only ever read. Nothing in it is moved, changed or deleted,
so you can go back to 2.0 at any time until you remove it yourself.

## What comes across

| Imported | Not imported |
|---|---|
| `config.json`: OpenAI key, languages, modules, schedules, alert buttons, paired panels | `certs`: the old certificate (a new one is made) |
| `blocklist.txt` | The generated `Caddyfile` |
| `recordings` | |
| `panel-firmware` | |

Relay 2.0 has no user accounts, so there are none to move. You create the first
account, the Owner, with the **admin token** from your old `config.json`. It
works once, for that, and is then removed from the settings.

## Move your settings across

1. Stop Relay 2.0 (`stop.bat` on Windows, `./stop.sh` on macOS and Linux).
2. Install and open the Relay desktop app once so it creates its own folder.
3. On the first-run page, find **Coming from Relay 2.0?**, type the path of
   your old `Relay` folder (or the `docker-config` folder inside it) and press
   **Import from a previous Relay**.
4. Enter your old admin token, choose a username and password, and create the
   Owner.

Prefer the command line? With Relay closed, run this from the Relay folder:

```
python tools/import_docker.py "C:\path\to\old\Relay"
```

Running it twice is safe: the second run changes nothing. If this Relay already
has an OpenAI key of its own, the import stops rather than replace it; add
`--force` to replace it, and the current file is kept as
`config.json.before-import`.

## What changes

- **One more certificate warning.** The old certificate is not carried over, so
  Relay makes a new one. Each browser that used the operator panel warns once
  more. Check that the fingerprint shown in Settings matches what the browser
  shows, then accept it.
- **Your audio input.** An input saved under a PulseAudio name is replaced by
  this computer's default input, with a notice. Pick the input again in
  Settings and save.
- **Ports and addresses.** The container's own ports and listening address are
  dropped; the app chooses its own.
- **Caption panels keep working.** Their pairing is in `config.json` and Relay
  announces itself under the same name, so a paired panel reconnects with no
  action on the panel.

## Remove the old install

When the desktop app is captioning and your panels are connected:

1. Remove the old container: open the old `Relay` folder and run
   `docker compose down`.
2. Remove the PulseAudio daemon you installed for Docker. On macOS run
   `brew uninstall pulseaudio` and delete the `.pulseaudio` folder in your home
   folder; on Windows delete the `.pulseaudio` folder in your user folder.
3. If nothing else on the computer uses Docker, uninstall Docker Desktop.
4. Keep the old `Relay` folder until you are sure you no longer need it. It
   holds a full copy of your 2.0 settings and recordings.
