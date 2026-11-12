## Result: freezing the Relay backend (#179)

**Go: PyInstaller 6.22 + Python 3.15, one-folder, as a Tauri sidecar.** One pin needs a change for Intel Macs. Branch `spike/freeze-179`, CI run 43316139079 (15 jobs, 5 OS x 3.13/3.14/3.15, all green).

| | Windows x64 | macOS arm64 | macOS Intel | Linux x64 | Linux arm64 |
|---|---|---|---|---|---|
| 1. Binary wheels for every pin (cp315) | yes, uvloop dropped (no Windows build) | yes | **no: cryptography 50.0.0 has no x86_64 wheel; 48.0.1 does** | yes | yes |
| 2. Frozen app starts, serves `/` | yes | yes | yes | yes | yes |
| 2. Audio devices listed | yes (none on runner) | yes, captured 1 s from mic (local) | yes | yes (PulseAudio needed) | yes |
| 3. Templates, static, migrations, module folders, tzdata | yes | yes | yes | yes | yes |
| 4. Start-up, one-folder / one-file | 0.6-1.1 s / 2.2-2.7 s | 0.6 s / 0.9 s | 1.4-1.6 s / 2.4-3.9 s | 0.5 s / 1.1 s | 0.6 s / 1.1 s |
| 5. Size, one-folder / one-file | 82 MB / 36 MB | 81 MB / 29 MB | 86 MB / 31 MB | 205 MB / 72 MB | 189 MB / 62 MB |
| 7. 3.14 and 3.13 | both work | both work | 3.13/3.14: same cryptography gap | both work | both work |

Not covered: antivirus behaviour (Windows), macOS microphone-prompt attribution when launched by a parent process, real input capture on Windows/Linux (runners have no mic).

Changes the freeze needs (no app code change yet):
1. Import `app.main` in the entry script. uvicorn imports `"app.main:app"` by string, which PyInstaller cannot see; without it: "Could not import module app.main".
2. Put the repo root on `sys.path` in the spec so `collect_submodules("app")` finds the modules. Module discovery (`pkgutil.iter_modules`) works frozen once they are in the archive.
3. `cryptography` needs an explicit hidden import.
4. Ship data at the same relative paths (`app/templates`, `app/static`, `app/migrations`, each module's `templates/` and `static/`, `blocklist.txt`, `config.example.json`) so `ROOT` resolves unchanged.
5. uvicorn falls back to asyncio without uvloop on Windows; the frozen app served fine.
6. Intel Mac: loosen `cryptography==50.0.0` (platform marker, or ship Intel on 48.x).

Recommendation: one-folder. One-file doubles start-up (extracts on every launch) and is the form antivirus flags most. Fallbacks (Nuitka, embedded runtime) are not needed.
