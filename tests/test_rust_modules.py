"""Rust modules (#652): "integrations are modules, never in core" in the Rust server. The Python rule is tests/test_modules.py;
this is its Rust form, read from the Cargo.toml files and the sources, so it needs no Rust toolchain.

It fails when:

* a crate in native/modules/ is not named `relay-module-<id>` in a folder `<id>`, or its id is not a valid module id;
* a crate other than relay-server depends on a module crate (core, the engine crates, the helper, another module);
* a module crate depends on relay-server, the helper, the front door, the RF spike or the PyO3 wheels (it may depend on
  relay-core and the engine crates relay-bus and relay-ndi);
* relay-server's dependencies on module crates and the list in relay-server/src/modules.rs disagree;
* core's sources (relay-core, relay-server except modules.rs) name a module crate;
* a module's crate is missing from the workspace (`modules/*` in native/Cargo.toml).

Run: .venv/bin/python tests/test_rust_modules.py
"""
import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import check, done, eq, section, tmpdir  # noqa: E402

NATIVE = Path(__file__).resolve().parent.parent / "native"
PREFIX = "relay-module-"
ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
# What a module crate may depend on inside the workspace.
MODULE_MAY_USE = {"relay-core", "relay-bus", "relay-ndi"}
# A module opens no socket of its own (the Rust form of netsock's rule, #345): `tokio::net`, `std::net::*Socket/Stream/Listener`,
# `socket2`. A `// allow-socket: <reason>` on the line marks one that is right (an address type is not a socket).
SOCKET_USE = re.compile(r"^(?!.*allow-socket:).*(tokio::net|std::net::(Tcp|Udp)|socket2)", re.M)


def manifest(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def dependencies(m: dict) -> set[str]:
    out: set[str] = set()
    for table in ("dependencies", "dev-dependencies", "build-dependencies"):
        out |= set(m.get(table, {}))
    for target in m.get("target", {}).values():
        for table in ("dependencies", "dev-dependencies", "build-dependencies"):
            out |= set(target.get(table, {}))
    return out


def workspace_crates() -> dict[str, tuple[Path, dict]]:
    """Package name -> (folder, parsed Cargo.toml) for every crate this workspace lists, globs expanded."""
    root = manifest(NATIVE / "Cargo.toml")
    out = {}
    for member in root["workspace"]["members"]:
        folders = sorted(p for p in NATIVE.glob(member) if (p / "Cargo.toml").exists())
        for folder in folders:
            m = manifest(folder / "Cargo.toml")
            out[m["package"]["name"]] = (folder, m)
    return out


def module_crates(crates: dict) -> dict[str, tuple[Path, dict]]:
    return {n: v for n, v in crates.items() if v[0].parent == NATIVE / "modules"}


def rust_files(folder: Path) -> list[Path]:
    return [p for p in sorted(folder.rglob("*.rs")) if "target" not in p.relative_to(folder).parts]


def socket_breaches(name: str, folder: Path) -> list[str]:
    return [f"{name}/{src.relative_to(folder)} opens its own socket: ask Host::net() for the traffic class's connection, so it "
            "leaves by the port the operator assigned (docs/rust-server.md)"
            for src in rust_files(folder / "src") if SOCKET_USE.search(src.read_text(encoding="utf-8"))]


def audit(crates: dict, server_modules_rs: str) -> list[str]:
    """Every rule's breaches as sentences; empty when the layout is sound. The test runs it on the real tree and on broken ones."""
    problems = []
    modules = module_crates(crates)
    for name, (folder, m) in modules.items():
        ident = folder.name
        if name != PREFIX + ident:
            problems.append(f"{name}: a module crate is named {PREFIX}<id>, in a folder called <id>")
        if not ID_RE.match(ident):
            problems.append(f"{name}: {ident!r} is not a valid module id")
    for name, (folder, m) in crates.items():
        deps = dependencies(m)
        used = sorted(d for d in deps if d in modules)
        if name == "relay-server":
            continue
        if used:
            problems.append(f"{name} depends on module crate(s) {used}: only relay-server names a module")
    for name, (folder, m) in modules.items():
        bad = sorted(d for d in dependencies(m) if d.startswith("relay-") and d not in MODULE_MAY_USE and d not in modules)
        if bad:
            problems.append(f"{name} depends on {bad}: a module may use only {sorted(MODULE_MAY_USE)}")
    server = crates.get("relay-server")
    if server:
        wired = {d for d in dependencies(server[1]) if d in modules}
        listed = set(re.findall(r"relay_module_([a-z0-9_]+)", server_modules_rs))
        for name in sorted(modules):
            ident = name[len(PREFIX):]
            if name not in wired:
                problems.append(f"{name} is not a dependency of relay-server, so it is never compiled in")
            if ident not in listed:
                problems.append(f"{name} is not listed in relay-server/src/modules.rs")
        for ident in sorted(listed):
            if PREFIX + ident not in modules:
                problems.append(f"relay-server/src/modules.rs names relay_module_{ident}, which is not a module crate")
    for name, (folder, _m) in modules.items():
        problems += socket_breaches(name, folder)
    for name in ("relay-core", "relay-server"):
        if name not in crates:
            continue
        folder = crates[name][0]
        for src in rust_files(folder):
            if src.name == "modules.rs" and name == "relay-server":
                continue
            if "relay_module_" in src.read_text(encoding="utf-8"):
                problems.append(f"{name}/{src.relative_to(folder)} names a module crate: core never does")
    return problems


with section("the real tree obeys the rules"):
    crates = workspace_crates()
    check("relay-core and relay-server are in the workspace", {"relay-core", "relay-server"} <= set(crates), sorted(crates))
    root = manifest(NATIVE / "Cargo.toml")
    check("native/Cargo.toml lists modules/*, so a module is built and tested without being added by hand",
          "modules/*" in root["workspace"]["members"])
    problems = audit(crates, (NATIVE / "relay-server" / "src" / "modules.rs").read_text(encoding="utf-8"))
    eq("no breach", problems, [])
    core = dependencies(crates["relay-core"][1])
    check("relay-core depends on no other Relay crate", not {d for d in core if d.startswith("relay-")}, sorted(core))

with section("the rules catch a breach"):
    tmp = Path(tmpdir("relay-rust-modules-"))

    def crate(folder: str, name: str, deps: str = "", src: str = "") -> tuple[Path, dict]:
        d = tmp / folder
        (d / "src").mkdir(parents=True, exist_ok=True)
        text = f'[package]\nname = "{name}"\nversion = "0.1.0"\n\n[dependencies]\n{deps}\n'
        (d / "Cargo.toml").write_text(text, encoding="utf-8")
        (d / "src" / "lib.rs").write_text(src, encoding="utf-8")
        return d, tomllib.loads(text)

    def tree(**overrides) -> dict:
        base = {
            "relay-core": crate("core", "relay-core"),
            "relay-server": crate("server", "relay-server", 'relay-core = {path="x"}\nrelay-module-demo = {path="y"}'),
        }
        demo = crate("demo", "relay-module-demo", 'relay-core = {path="x"}')
        # modules live in native/modules/: put the fake under a folder with that parent for module_crates()
        base["relay-module-demo"] = (NATIVE / "modules" / "demo", demo[1])
        base.update(overrides)
        return base

    listed = "vec![Arc::new(relay_module_demo::Demo)]"
    eq("a sound tree has no breach", audit(tree(), listed), [])
    check("core depending on a module is caught",
          any("depends on module" in p for p in audit(tree(**{"relay-core": crate("c2", "relay-core", 'relay-module-demo = {path="y"}')}), listed)))
    other = (NATIVE / "modules" / "other", tomllib.loads('[package]\nname = "relay-module-other"\n[dependencies]\nrelay-module-demo = {path="y"}\nrelay-server = {path="z"}'))
    caught = audit(tree(**{"relay-module-other": other}), listed + " relay_module_other")
    check("a module depending on another module is caught", any("relay-module-other depends on module" in p for p in caught), caught)
    check("a module depending on relay-server is caught", any("relay-module-other depends on ['relay-server']" in p for p in caught), caught)
    check("a module missing from modules.rs is caught", any("not listed in relay-server/src/modules.rs" in p for p in audit(tree(), "vec![]")))
    check("a module list naming a crate that does not exist is caught",
          any("not a module crate" in p for p in audit(tree(), listed + " relay_module_ghost")))
    bad = (NATIVE / "modules" / "demo", tomllib.loads('[package]\nname = "relay-demo"\n'))
    check("a module crate with the wrong name is caught", any("is named relay-module-<id>" in p for p in audit({k: v for k, v in tree().items() if k != "relay-module-demo"} | {"relay-demo": bad}, listed)))
    own = crate("demo", "relay-module-demo", 'relay-core = {path="x"}', "use tokio::net::TcpStream;")
    check("a module naming tokio::net is caught", socket_breaches("relay-module-demo", own[0]) != [])
    ok = crate("demo2", "relay-module-demo", 'relay-core = {path="x"}', "use tokio::net::TcpStream; // allow-socket: a type in a signature")
    eq("a marked line is allowed", socket_breaches("relay-module-demo", ok[0]), [])
    plain = crate("demo3", "relay-module-demo", 'relay-core = {path="x"}', "let s = host.net().udp(\"intercom\", &o);")
    eq("asking Host::net() is allowed", socket_breaches("relay-module-demo", plain[0]), [])
    leaky = crate("c3", "relay-core", "", "use relay_module_demo::Demo;")
    check("core source naming a module is caught", any("names a module crate" in p for p in audit(tree(**{"relay-core": leaky}), listed)))

done()
