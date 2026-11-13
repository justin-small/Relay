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
