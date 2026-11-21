"""Allen & Heath I/O patch from an exported show file (#261): the Avantis show read the way the desk shows it (two
trimmed real exports, Bridge-3 and Bridge-4), a dLive show built to the published layout, refusals that say why and
change nothing, and the admin routes that keep the patch for the signal map (#262).
Run: python tests/test_ah_showfile.py
"""
import gzip
import io
import os
import struct
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ.pop("RELAY_DB_BACKUPS", None)
sys.path.insert(0, str(HERE))
import _db  # noqa: E402

_db.start()
_db.reset()

import app.main as main  # noqa: E402,F401
from app import config  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as _modules  # noqa: E402
from app.modules.audio_mixers import service as svc, showfile as sf  # noqa: E402
from _harness import check, done, eq, section  # noqa: E402

FIX = HERE / "fixtures" / "ah"
B4 = (FIX / "avantis-bridge-4.tar.gz").read_bytes()      # firmware 1.x, from a real Avantis export
B3 = (FIX / "avantis-bridge-3.tar.gz").read_bytes()


def targz(files: list[tuple[str, bytes]]) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as tf:
        for name, data in files:
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return raw.getvalue()


def sect(label: str, payload: bytes) -> bytes:
    body = label.encode() + b"\0" + payload
    return struct.pack(">H", len(body)) + body


def show(version: str, dat: bytes, scene: str = "StageBoxScene65535") -> bytes:
    return targz([("Show/Version.dat", version.encode() + b"\n"),
                  (f"Show/Scenes/{scene}.tar.gz", targz([(f"{scene}.dat", dat)]))])


def name_block(label: str, names: list[str]) -> bytes:
    k = len(names)
    return sect(label + " Name Colour Manager", b"\x01" + b"".join(n.encode().ljust(9, b"\0") for n in names) + bytes(k))


def avantis_dat(records: list[tuple[int, int]]) -> bytes:
    return b"\0" * 64 + name_block("Input Channel", ["Vox"] + [str(n) for n in range(2, 97)]) + \
        sect("Channel Mapper", b"\x01" + b"".join(bytes([t, i >> 8, i & 255]) for t, i in records))


NONE = [(0x11, 0)] * sf.AVANTIS_RECORDS

# -- the Avantis fixtures ----------------------------------------------------------------------------------------------
with section("a Bridge-4 Avantis show, as the desk shows it"):
    got = sf.read(B4)
    eq("family and firmware", (got["family"], got["firmware"]), ("avantis", "Bridge-4"))
    eq("63 inputs patched", len(got["inputs"]), 63)
    first = got["inputs"][0]
    eq("input 1 is named and patched", (first["channel"], first["name"], first["named"]), (1, "Lead", True))
    eq("its source", first["source"], {"kind": "slink", "port": None, "socket": 1, "label": "SLink socket 1"})
    unnamed = next(i for i in got["inputs"] if i["channel"] == 5)
    eq("an unnamed strip holds its number", (unnamed["name"], unnamed["named"]), ("5", False))
    eq("outputs are listed", got["outputs"][0], {"destination": "Local out 1", "kind": "local", "port": None,
                                                 "socket": 1, "carries": "Mono Aux 1 (Wedge 1)"})
    check("verified on real exports", got["verified"] is True)

with section("a Bridge-3 show with Local and I/O Port sockets"):
    got = sf.read(B3)
    eq("firmware", got["firmware"], "Bridge-3")
    by = {i["channel"]: i for i in got["inputs"]}
    eq("12 local XLRs", [by[n]["source"]["label"] for n in (1, 12)], ["Local XLR 1", "Local XLR 12"])
    eq("then I/O Port 1", by[13]["source"], {"kind": "io_port", "port": 1, "socket": 1, "label": "I/O Port 1 socket 1"})
    eq("named", by[13]["name"], "Kick")
    eq("the patch can skip sockets", (by[57]["source"]["socket"], by[59]["source"]["socket"]), (60, 53))
    out = {o["destination"]: o for o in got["outputs"]}
    eq("the main goes to the card", (out["I/O Port 1 out 1"]["carries"], out["I/O Port 1 out 2"]["carries"]),
       ("Main L", "Main R"))
    eq("a named aux", out["I/O Port 1 out 4"]["carries"], "Mono Aux 2 (Wedge 2)")
    eq("the card's outputs know their port", (out["I/O Port 1 out 1"]["kind"], out["I/O Port 1 out 1"]["port"]),
       ("io_port", 1))

with section("source types"):
    recs = list(NONE)
    recs[0], recs[1], recs[2], recs[3], recs[4], recs[5], recs[6] = (0, 12), (0, 13), (3, 0), (2, 4), (0x25, 3), \
        (0x26, 0), (0x30, 9)
    got = sf.read(show("Bridge-4", avantis_dat(recs)))
    eq("AES, SLink, port 2, USB, generator and an unknown type", [i["source"]["label"] for i in got["inputs"]],
       ["Local AES L", "Local AES R", "SLink socket 1", "I/O Port 2 socket 5", "USB 3", "Signal generator",
        "Source type 0x30 index 9"])
    eq("channel 1 named from the section", got["inputs"][0]["name"], "Vox")
    recs = list(NONE)
    for k, t in enumerate((0x09, 0x0A, 0x0A, 0x0D, 0x0D, 0x0D, 0x0E, 0x0F, 0x04, 0x05, 0x10, 0x10, 0x1B, 0x19, 0x44)):
        recs[1061 + k] = (t, [0, 0, 1, 0, 1, 2, 0, 1, 6, 6, 1, 2, 3, 2, 0][k])
    recs[1077 + 127] = (0x0B, 2)        # SLink out 128
    recs[1370 + 5] = (0x0C, 3)          # I/O Port 2 out 6: stereo aux 2 right
    recs[1498 + 39] = (0x04, 0)         # ME out 40
    got = sf.read(show("Bridge-4", avantis_dat(recs)))
    eq("what the outputs carry", [o["carries"] for o in got["outputs"][:15]],
       ["Mono Group 1", "Stereo Group 1 L", "Stereo Group 1 R", "Main L", "Main R", "Main M", "Mono Matrix 1",
        "Stereo Matrix 1 R", "Direct out of input 7", "Insert 1 send of input 7", "PAFL R", "PAFL 2 L", "Monitor bus 4",
        "FX rack output 3", "type 0x44 index 0"])
    last = {o["destination"]: o["carries"] for o in got["outputs"]}
    eq("the far ends of each group", (last["SLink out 128"], last["I/O Port 2 out 6"], last["ME out 40"]),
       ("Mono Aux 3", "Stereo Aux 2 R", "Direct out of input 1 (Vox)"))

# -- dLive, built to the published layout (no real export has been tried yet) -------------------------------------------
def dlive_dat(active: int, refs: list[tuple[int, int]], names_n: int = 128) -> bytes:
    inputs = b""
    for n in range(128):
        a, r = (active, refs) if n < 3 else (0, [(sf.NONE_DLIVE, 0)] * 4)
        inputs += bytes([a]) + b"".join(bytes([t, i >> 8, i & 255]) for t, i in r)
    payload = (b"\x06" + inputs).ljust(sf.DLIVE_MAPPER_LEN, b"\0")
    return b"\0" * 64 + name_block("Input Channel", ["Pastor", "2", "Keys"] + [str(n) for n in range(4, names_n + 1)]) \
        + sect("Channel Mapper", payload)


with section("a dLive show"):
    got = sf.read(show("14", dlive_dat(0, [(0, 4), (sf.NONE_DLIVE, 0), (sf.NONE_DLIVE, 0), (sf.NONE_DLIVE, 0)])))
    eq("family, firmware", (got["family"], got["firmware"]), ("dlive", "14"))
    check("not yet verified on a real export", got["verified"] is False)
    eq("three inputs", [(i["channel"], i["name"], i["source"]["label"]) for i in got["inputs"]],
       [(1, "Pastor", "MixRack socket 5"), (2, "2", "MixRack socket 5"), (3, "Keys", "MixRack socket 5")])
    got = sf.read(show("13", dlive_dat(2, [(0, 0), (3, 9), (sf.NONE_DLIVE, 0), (sf.NONE_DLIVE, 0)])))
    eq("the active source B is the one listed", got["inputs"][0]["source"],
       {"kind": "io_port", "port": 2, "socket": 10, "label": "MixRack I/O Port 2 socket 10"})
    got = sf.read(show("14", dlive_dat(1, [(1, 50), (0, 0), (0, 0), (0, 0)])))
    eq("a surface port socket", got["inputs"][0]["source"]["label"], "Surface I/O Port 4 socket 7")
    got = sf.read(show("14", dlive_dat(1, [(1, 3), (0, 0), (0, 0), (0, 0)])))
    eq("a surface local", got["inputs"][0]["source"]["kind"], "local")

# -- refusals ---------------------------------------------------------------------------------------------------------
def refused(why: str, data: bytes, *words: str) -> None:
    try:
        sf.read(data)
    except sf.ShowFileError as e:
        check(f"{why}: says why", all(w in str(e) for w in words), str(e))
    else:
        check(f"{why}: refused", False, "it was read")


with section("a file that is not a readable show"):
    refused("empty", b"", "Nothing")
    refused("not gzip", b"PK\x03\x04 a zip", "not a show file", "Qu")
    refused("gzip but not a tar", gzip.compress(b"hello"), "not a show file")
    refused("a truncated archive", B4[:300], "not a show file")
    refused("a tar with no show in it", targz([("readme.txt", b"hi")]), "no Show/Version.dat", "Qu shows")
    refused("too large", b"\x1f\x8b" + b"\0" * sf.MAX_UPLOAD, "too large")
    refused("a show with no live scene", targz([("Show/Version.dat", b"Bridge-4\n")]), "no live scene")
    refused("another Avantis format", show("Bridge-5", avantis_dat(NONE)), "Bridge-5", "Nothing was read")
    refused("an unknown format", show("7", avantis_dat(NONE)), "not one Relay reads")
    refused("a scene alone", targz([("StageBoxScene65535.dat", avantis_dat(NONE))]), "which desk")
    refused("no mapper", show("Bridge-4", b"\0" * 64 + name_block("Input Channel", ["a"])), "no Channel Mapper")
    refused("a different number of records", show("Bridge-4", avantis_dat(NONE[:1500])), "1500 patch records",
            "1830")
    refused("dLive with another mapper", show("14", b"\0" * 64 + sect("Channel Mapper", b"\x07" + b"\0" * 100)),
            "patch section Relay does not know")
    try:
        sf._walk(gzip.compress(b"\0" * 100_000), lambda n, s: 0, limit=1000)
    except sf.ShowFileError as e:
        check("an archive that expands too far is stopped", "far more" in str(e), str(e))
    else:
        check("an archive that expands too far is stopped", False, "it was read")

with section("the stored shape"):
    good = {**sf.read(B3), "file": "FOH.tar.gz", "read_at": 5.0}
    eq("a good patch round-trips", sf.clean_patch(good), good)
    eq("junk is dropped", (sf.clean_patch("x"), sf.clean_patch({"family": "qu"}), sf.clean_patch(None)),
       (None, None, None))
    bad = sf.clean_patch({"family": "dlive", "inputs": [1, {"source": "x"}, {"channel": "9", "source": {"label": 4}}],
                          "outputs": ["x"], "firmware": 7, "verified": "yes"})
    eq("bad rows are cleaned", (bad["firmware"], bad["verified"], len(bad["inputs"]), bad["outputs"]),
       ("", True, 1, []))

# -- the admin routes -------------------------------------------------------------------------------------------------
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"audio_mixers": {"enabled": True}},
             "audio_mixers": svc.clean_settings({})})
_modules.attach("audio_mixers")
admin = _db.client(app, base_url="http://relay.local:8001")
viewer = _db.client(app, role="viewer", username="viewer1", base_url="http://relay.local:8001")
A = "/api/admin/audio_mixers"
ids = {}
r = admin.put(A + "/settings", json={"desks": [{"model": "avantis", "name": "FOH"}, {"model": "dlive", "name": "Stage"},
                                              {"model": "qu16", "name": "Youth"}, {"model": "x32", "name": "Other"}]})
eq("desks saved", r.status_code, 200)
for d in r.json()["settings"]["desks"]:
    ids[d["model"]] = d["id"]


def upload(who, model, data, name="FOH.tar.gz"):
    return who.post(f"{A}/{ids[model]}/patch?name={name}", content=data,
                    headers={"content-type": "application/octet-stream"})


with section("uploading a show to a desk"):
    r = upload(admin, "avantis", B3)
    eq("read", r.status_code, 200)
    p = r.json()["patch"]
    eq("kept with its file name and firmware", (p["file"], p["firmware"], len(p["inputs"])), ("FOH.tar.gz", "Bridge-3", 64))
    check("and the time it was read", p["read_at"] > 0)
    eq("read back", admin.get(f"{A}/{ids['avantis']}/patch").json()["patch"], p)
    eq("a viewer may read it", viewer.get(f"{A}/{ids['avantis']}/patch").status_code, 200)
    eq("a viewer cannot upload", upload(viewer, "avantis", B3).status_code, 403)
    check("it is saved with the settings", config.get()["audio_mixers"]["patches"][ids["avantis"]]["firmware"] == "Bridge-3")
    after = [x for x in svc.service.patch_list()]
    eq("the signal map gets it", [(x["desk"], x["name"], x["model"], x["family"], len(x["inputs"])) for x in after],
       [(ids["avantis"], "FOH", "avantis", "avantis", 64)])

with section("a bad file changes nothing"):
    r = upload(admin, "avantis", b"not a show")
    eq("refused with a sentence", (r.status_code, "not a show file" in r.json()["error"]), (400, True))
    eq("the stored patch is as it was", admin.get(f"{A}/{ids['avantis']}/patch").json()["patch"], p)
    r = upload(admin, "avantis", show("Bridge-9", avantis_dat(NONE)))
    eq("a newer format is refused", r.status_code, 400)
    eq("still as it was", admin.get(f"{A}/{ids['avantis']}/patch").json()["patch"], p)
    r = upload(admin, "dlive", B4)
    eq("an Avantis show on a dLive desk", (r.status_code, "Nothing was changed" in r.json()["error"]), (400, True))
    eq("nothing stored for the dLive", admin.get(f"{A}/{ids['dlive']}/patch").json()["patch"], None)
    r = upload(admin, "qu16", B4)
    eq("Qu is refused for now", (r.status_code, "Qu shows are not supported" in r.json()["error"]), (400, True))
    eq("other makes too", upload(admin, "x32", B4).status_code, 400)
    eq("no such desk", admin.post(f"{A}/nope/patch", content=B4).status_code, 404)
    eq("no such desk to read", admin.get(f"{A}/nope/patch").status_code, 404)

with section("a dLive desk and clearing"):
    r = upload(admin, "dlive", show("14", dlive_dat(0, [(0, 4), (sf.NONE_DLIVE, 0), (sf.NONE_DLIVE, 0), (sf.NONE_DLIVE, 0)])))
    eq("a dLive show on a dLive desk", (r.status_code, r.json()["patch"]["family"]), (200, "dlive"))
    eq("both desks reach the signal map", [x["name"] for x in svc.service.patch_list()], ["FOH", "Stage"])
    eq("a viewer cannot clear", viewer.delete(f"{A}/{ids['dlive']}/patch").status_code, 403)
    eq("cleared", admin.delete(f"{A}/{ids['dlive']}/patch").json(), {"patch": None})
    eq("only one left", [x["name"] for x in svc.service.patch_list()], ["FOH"])
    r = admin.put(A + "/settings", json={"desks": [{"id": ids["avantis"], "model": "avantis", "name": "FOH renamed"}]})
    eq("saving desks keeps their patch", len(r.json()["settings"]["patches"][ids["avantis"]]["inputs"]), 64)
    r = admin.put(A + "/settings", json={"desks": []})
    eq("removing a desk drops its patch", r.json()["settings"]["patches"], {})
    eq("an old config without patches still answers", svc.clean_settings({"desks": []})["patches"], {})

done()
