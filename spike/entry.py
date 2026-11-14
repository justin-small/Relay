"""Frozen entry point for the spike. `relay --probe` checks what a frozen app can lose, then exits;
`relay --reset-owner <username>` is lockout recovery; anything else starts the server like run.py. Importing app.main here is also what makes PyInstaller
collect the app (uvicorn imports it by string, which the analysis cannot see)."""
import sys, traceback
import app.main  # noqa: F401


def probe() -> int:
    import json, importlib, platform
    out = {"python": sys.version.split()[0], "platform": platform.platform(), "frozen": getattr(sys, "frozen", False)}
    from app import config, modules
    out["root"] = str(config.ROOT)
    out["modules_found"] = modules.package_names()
    out["modules_loaded"] = sorted(m.manifest.id for m in modules.modules.all())
    base = config.ROOT / "app"
    out["paths"] = {p: (base / p).exists() for p in ("templates/index.html", "static", "migrations/0001_users.sql",
                    "modules/propresenter/templates", "modules/propresenter/static")}
    from zoneinfo import ZoneInfo
    try:
        out["tzdata"] = str(__import__("datetime").datetime(2026,7,1,tzinfo=ZoneInfo("America/Chicago")).utcoffset())
    except Exception as e:
        out["tzdata"] = f"FAIL {e!r}"
    for name in ("uvloop", "httptools", "orjson", "soxr", "numpy", "cryptography", "certifi", "websockets"):
        try:
            m = importlib.import_module(name); out[name] = getattr(m, "__version__", "ok")
        except Exception as e:
            out[name] = f"FAIL {e!r}"
    try:
        import sounddevice as sd
        devs = sd.query_devices()
        ins = [d["name"] for d in devs if d["max_input_channels"] > 0]
        out["inputs"] = ins
        if ins and "--capture" in sys.argv:
            import numpy as np
            rec = sd.rec(16000, samplerate=16000, channels=1, dtype="int16"); sd.wait()
            out["capture"] = f"{len(rec)} frames, peak {int(np.abs(rec).max())}"
    except Exception as e:
        out["audio"] = f"FAIL {e!r}"
    print(json.dumps(out, indent=1, default=str))
    return 0


def reset_owner() -> int:
    """`relay --reset-owner <username>`: lockout recovery for an installed app, which has no Python of its own
    to run tools/reset_owner.py with. Same tool, same state folder (RELAY_HOME, else this OS's own)."""
    from app import db
    from tools import reset_owner as tool
    i = sys.argv.index("--reset-owner")
    try:
        return tool.main(["relay --reset-owner", *sys.argv[i + 1:i + 2]])
    finally:
        db.close()


if __name__ == "__main__":
    if "--probe" in sys.argv:
        sys.exit(probe())
    if "--reset-owner" in sys.argv:
        sys.exit(reset_owner())
    import run
    run.main()
