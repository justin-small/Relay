# Relay desktop shell (Tauri 2)

A thin shell: it starts the frozen backend, sits in the tray, shows the panel and starts at login. It adds
nothing to `app/` beyond the sidecar channel (`app/sidecar.py`).

- **Backend**: `src-tauri/src/backend.rs` runs `relay-backend/relay` (PyInstaller one-folder, bundled as a
  Tauri resource) with `RELAY_HOME` and `RELAY_SIDECAR=1`. Events arrive as `RELAY-EVENT {json}` lines on
  stdout; `quit` on stdin (or the pipe closing) stops it. An unexpected exit restarts it with backoff.
- **Panel**: the window loads `/admin` through a loopback proxy (`proxy.rs`) that pins the panel certificate's
  SHA-256 fingerprint (reported by the backend). Certificate checks are never disabled.
- **Security**: `capabilities/default.json` grants the webview nothing; remote content gets no IPC.

## Build

```bash
pip install pyinstaller && (cd spike && pyinstaller relay.spec --noconfirm --distpath /tmp/relay-dist)
cp -R /tmp/relay-dist/relay/. desktop/src-tauri/relay-backend/
cd desktop && npm install && npx tauri build          # --debug --bundles app for a quick local bundle
```

`cargo run` in `src-tauri/` runs against the checkout's `.venv` and `run.py` when no frozen backend is bundled.
Set `RELAY_HOME` to use a throwaway state folder.

## Releases and updates (#184)

Tagging `vX.Y.Z` runs `.github/workflows/release.yml`: tests, guide, panel firmware, then the four desktop
builds (Windows x64, macOS arm64, macOS Intel, Linux x64), then the GitHub release with every installer,
`latest.json` and the source zip. The tag is the only place a version is written (`tools/release_tools.py`
copies it into `tauri.conf.json` and `Cargo.toml`; the shell passes it on as `RELAY_VERSION`). A tag with a
hyphen is a pre-release and is not offered to stable installs. Pull requests that touch `desktop/` build the
installers without signing or publishing.

**Updater key.** Updates are verified with a minisign key pair that is separate from OS code signing. The
public half is `plugins.updater.pubkey` in `tauri.conf.json`; the private half is the repository secret
`TAURI_SIGNING_PRIVATE_KEY` (and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`, empty if the key has none). A tag
without the secret fails. To rotate: `npx tauri signer generate -w relay-updater.key`, replace the pubkey,
set the secret. Installs holding the old public key cannot verify the new one, so ship one release signed
with the old key that carries the new pubkey first.

**Where installs look** (`src-tauri/src/updater.rs`), first match wins:

1. the `RELAY_UPDATE_URL` environment variable,
2. `"update_url"` in `shell.json` in the state folder,
3. the built-in default, `https://github.com/justin-small/Relay/releases/latest/download/latest.json`.

Either of the first two takes a full URL to a `latest.json` or just `owner/repo`. If the repository moves,
set it there; no rebuild is needed. (GitHub also redirects the old repository's release URLs for a while,
so installs keep working in the meantime.) A new build can change the default and the `endpoints` in
`tauri.conf.json`.

**Flow.** Checks 20 s after start and daily; the tray's *Check for updates* item turns into *Install Relay
X and restart* and the panel shows a banner with the same button. Installing is always the operator's click,
with one more question if captions are running. The backend is stopped gracefully, the files are replaced,
Relay restarts. The state folder is never touched. A download whose signature does not verify is refused.

**To test by hand** (nothing here has run on real machines yet): install the build for N, serve a `latest.json`
for N+1 from a local HTTPS-capable host or a draft release, point `RELAY_UPDATE_URL` at it, and check the
tray. Also reinstall the same version over itself on Windows to check the NSIS installer replaces the
bundled backend (tauri-apps/tauri#15134), and edit one byte of the update file to see it refused.

**Backup and automatic rollback** (`src-tauri/src/rollback.rs`). Just before the installer runs, the installed
app (macOS bundle, Windows install folder, Linux AppImage) and the state folder (not recordings or logs) are
copied to `backups/` in the state folder; if either copy fails, nothing is installed. The new version is on
trial until its backend reports ready. If the backend is still in error 30 s after failing, or the new version
has been launched three times without ever getting ready, Relay restarts and the next launch puts the settings
and the old app back (on Windows through a detached PowerShell script, since the running program's files are
in use) and says so once. The first install of a build that has this code cannot protect itself; the next
update can. Untested on real machines, like the rest: to try it, build an update whose backend exits at start.
