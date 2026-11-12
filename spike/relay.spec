# PyInstaller spec for the freeze spike (#179). Build from the repo root:
#   pyinstaller spike/relay.spec --noconfirm [-- onefile]
import os
HERE = os.path.dirname(os.path.abspath(SPEC))
ROOT = os.path.dirname(HERE)
import sys; sys.path.insert(0, ROOT)  # collect_submodules("app") imports it
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs

onefile = os.environ.get("RELAY_ONEFILE") == "1"

# Frozen modules keep __file__ under sys._MEIPASS, so shipping the data at the same relative
# paths ("app/templates", ...) keeps ROOT / "app" / ... valid with no code change.
datas = [(f"{ROOT}/{p}", p) for p in ("app/templates", "app/static", "app/migrations")]
datas += [(f"{ROOT}/config.example.json", "."), (f"{ROOT}/blocklist.txt", ".")]
# Every module's templates/ and static/ (any folder under app/modules/<id>/ that is not Python).
for mod in sorted(os.listdir(f"{ROOT}/app/modules")):
    for sub in ("templates", "static"):
        if os.path.isdir(f"{ROOT}/app/modules/{mod}/{sub}"):
            datas.append((f"{ROOT}/app/modules/{mod}/{sub}", f"app/modules/{mod}/{sub}"))
datas += collect_data_files("tzdata") + collect_data_files("certifi")

# Modules are discovered by pkgutil.iter_modules on the folder, so every one has to be pulled in by name.
hidden = collect_submodules("app") + collect_submodules("uvicorn") + collect_submodules("cryptography") + ["uvloop", "httptools"]
binaries = collect_dynamic_libs("_sounddevice_data") + collect_dynamic_libs("soxr")

a = Analysis(["entry.py"], pathex=["..", "."], datas=datas, binaries=binaries, hiddenimports=hidden)
pyz = PYZ(a.pure)
if onefile:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, name="relay", console=True)
else:
    exe = EXE(pyz, a.scripts, exclude_binaries=True, name="relay", console=True)
    coll = COLLECT(exe, a.binaries, a.datas, name="relay")
