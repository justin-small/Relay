# Relay Helper

A small program that runs on a show computer (Mac or Windows) and does things Relay cannot do from another machine.
It is generic: it holds one connection to Relay and runs **capabilities**. The first is ProPresenter; later ones (a
camera, a lighting desk's local app, a file drop) are added as new capabilities without a new connection or install.

Source: `native/relay-helper/` (Rust, one binary, no runtime to install). Tests: `cargo test` there (a fake Relay and a
fake ProPresenter run on the same machine). CI runs them on Linux, macOS and Windows.

## Why it exists

ProPresenter 22 keeps its library in a database that only the program writes: a song gets in by opening the file with
ProPresenter, and its network API listens on its own computer. When Relay runs on another machine neither is
reachable. The helper sits next to ProPresenter, dials out to Relay (so no firewall or port-forward is needed) and
does both on Relay's behalf.

## Using it

```bash
relay-helper pair ws://192.168.1.20:8080 ABC-123 "FOH Mac"   # once; the code comes from Relay's admin page
relay-helper port 50402                                      # ProPresenter's Network port, from its settings
relay-helper run                                             # leave running; it reconnects by itself
```

### Installing it

Each release carries an installer for the show computer, linked from Relay's Helpers page: `Relay-Helper-windows-x64.exe`,
`Relay-Helper-macos-arm64.pkg` (Apple silicon) and `Relay-Helper-macos-x64.pkg` (Intel). Each puts the program in place
and runs `relay-helper install`, so the helper starts now and at every login. Until it is paired it waits; running
`relay-helper pair` (the Helpers page prints the whole command) is the one step left. The installers are unsigned until
a signing certificate exists, so the computer warns once.

| Command | Does |
| --- | --- |
| `relay-helper install` | macOS: a launchd agent (`~/Library/LaunchAgents/org.relay.helper.plist`, restarted if it exits, log in `~/Library/Logs/Relay Helper.log`). Windows: a value in the user's `Run` key. Then starts the helper |
| `relay-helper uninstall` | Removes that, and stops the running helper |

It runs as the signed-in user on purpose: ProPresenter's window lives in that session, so a Windows service (which runs
apart from the desktop) could not open a `.pro` file. Release binaries are built by the `helper` job in
`.github/workflows/release.yml`; the Windows installer is `native/relay-helper/packaging/windows/relay-helper.nsi`, the
macOS one a `pkgbuild` package whose `packaging/macos/postinstall` registers the launch agent for the signed-in user.

### A Relay with its own certificate

Relay serves its operator panel over HTTPS with a self-signed certificate (`app/tls.py`), so no public authority vouches
for it. Pair with `wss://` and the certificate's SHA-256 fingerprint, which the Helpers page prints in the command:

```bash
relay-helper pair wss://192.168.1.20 ABC-123 "FOH Mac" --pin 3F:9A:...
```

The pin is saved in the settings (`tls_pin`); from then on the helper accepts that certificate and no other, before it
sends anything, even if the name differs. Pairing again without `--pin` clears it. A Relay that gets a new certificate
(a new address, or near its expiry; Relay says so on its admin page) must be paired again with the new fingerprint.
With no pin, `wss://` uses the public root certificates, which suits a Relay behind a proxy with a real certificate.

### Sleep and flaky networks

The helper pings Relay every 20 seconds and drops the connection when nothing at all has arrived for 50, then dials
again (1 s, doubling to 30 s). A laptop that sleeps or loses Wi-Fi comes back without anyone touching it.

Settings are one JSON file (`~/Library/Application Support/Relay Helper/helper.json`, or `%APPDATA%\Relay Helper\` on
Windows, mode 0600 where the OS has modes). `RELAY_HELPER_CONFIG` points elsewhere.

## The connection

The helper opens `ws(s)://<relay>/ws/helper` and sends JSON text frames (at most 24 MB each).

| Helper to Relay | Meaning |
| --- | --- |
| `pair {code, name, os, version}` | First frame of a new helper: trade the one-use code for a token |
| `hello {helper_id, name, os, version, capabilities, token}` | First frame of a paired helper; `capabilities` is `[{id, version, ops}]`. The token is in the frame, never in the address |
| `result {id, ok, data?, error?}` | Answer to a `call`; `error` is a sentence the operator may see |
| `pong`, `event {cap, name, data}` | Heartbeat, and things a capability noticed |

| Relay to helper | Meaning |
| --- | --- |
| `paired {helper_id, token}` | Keep the token |
| `welcome {relay_version}` | Hello accepted |
| `call {id, cap, op, args}` | Run an operation; answer with the same `id` |
| `ping` | Heartbeat |
| `refused {reason}` | Bad token or removed helper: the helper stops until paired again |

The helper reconnects after 1 s, doubling to 30 s. Calls run concurrently.

## Capabilities

### `propresenter` (version 1)

| Operation | Arguments | Result |
| --- | --- | --- |
| `status` | none | `{api_configured, api_reachable, version?}` |
| `open_files` | `{files: [{name, data_b64}]}` | `{opened: n}`: writes each `.pro` to a scratch folder and opens it with ProPresenter, which imports it (macOS `open -a ProPresenter`, Windows `start`) |
| `api` | `{method, path, body?}` | `{status, body}`: a call to ProPresenter's own API on this computer |

Limits that keep the helper from becoming a way into the computer: `open_files` takes plain `.pro` names only (no
folders, no hidden names) of at most 16 MB, 200 at a time; `api` takes GET, POST, PUT or DELETE on a plain `/v1/...`
path of the configured host and port only. Files are deleted ten minutes after they are opened.

### Adding a capability

Implement `Capability` (`id`, `version`, `ops`, `call`) in `native/relay-helper/src/` and register it in
`client::registry_for`. Keep each operation's arguments validated in the capability, and its errors fixed sentences.

## Relay's side

Not built yet: a core hub (`app/helperhub.py`) that makes pairing codes, accepts `/ws/helper`, and lets modules call a
helper by capability. Modules never import the helper; they ask the hub.
