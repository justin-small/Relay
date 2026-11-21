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
