"""Shared UI helpers (#461): one copy of each helper, in app/static/relay-ui.js, and nothing copied between scripts.

This file is the rule's enforcement, run in CI on every pull request. It fails when:

* a function body of more than 60 characters (whitespace collapsed) appears in two script files, unless
  tests/ui_contract_baseline.txt lists that copy. The baseline holds the copies that existed when this test came in
  and may only shrink: a new copy fails, and a copy that is gone fails until its line is deleted;
* a module script defines its own function named like a relayUI helper (can, api, el, say, confirm) instead of
  using window.relayUI;
* any script other than relay-ui.js calls the browser's confirm() (use relayUI.confirm);
* a page loads a script that uses relayUI without loading relay-ui.js before it.

Vendored files (vendor/, *.min.js, livekit-client.umd.js) are left out.

Run: .venv/bin/python tests/test_ui_contract.py        (--rebaseline rewrites the baseline after a removal)
"""
import hashlib
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
BASELINE = Path(__file__).resolve().parent / "ui_contract_baseline.txt"
SHARED = APP / "static" / "relay-ui.js"
HELPERS = ("can", "api", "el", "say", "confirm")
MIN_BODY = 60
# The remote phone page is reached through the remote-access tunnel, which exposes only its own four routes
# (app/modules/intercom_remote/routes.py REMOTE_ROUTES), so it cannot load core's script.
OWN_HELPERS_ALLOWED = {"app/modules/intercom_remote/static/phone.js"}

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, done, eq, section  # noqa: E402


def scripts() -> list[Path]:
    out = []
    for p in sorted(APP.rglob("*.js")):
        rel = p.relative_to(ROOT).as_posix()
        if "/vendor/" in rel or p.name.endswith(".min.js") or p.name == "livekit-client.umd.js":
            continue
        out.append(p)
    return out


REGEX_BEFORE = set("(,=:[!&|?{};+-*%<>~^")
REGEX_WORDS = {"return", "typeof", "case", "in", "of", "delete", "void", "throw", "new", "else", "do"}


def mask(src: str) -> str:
    """The source with the inside of every string, comment and regex blanked out, same length, so code can be
    searched and braces matched without being fooled by what is quoted."""
    out = list(src)
    i, n = 0, len(src)

    def blank(a, b):
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    def prev_sig(k):
        k -= 1
        while k >= 0 and src[k] in " \t\r\n":
            k -= 1
        if k < 0:
            return ""
        if src[k].isalnum() or src[k] in "_$":
            e = k + 1
            while k >= 0 and (src[k].isalnum() or src[k] in "_$"):
                k -= 1
            return src[k + 1:e]
        return src[k]

    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            e = src.find("\n", i)
            e = n if e == -1 else e
            blank(i, e)
            i = e
        elif c == "/" and i + 1 < n and src[i + 1] == "*":
            e = src.find("*/", i + 2)
            e = n if e == -1 else e + 2
            blank(i, e)
            i = e
        elif c in "'\"`":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            blank(i + 1, j)
            i = j + 1
        elif c == "/":
            p = prev_sig(i)
            if p == "" or p in REGEX_BEFORE or p in REGEX_WORDS:
                j, in_class = i + 1, False
                while j < n and src[j] != "\n":
                    if src[j] == "\\":
                        j += 2
                        continue
                    if src[j] == "[":
                        in_class = True
                    elif src[j] == "]":
                        in_class = False
                    elif src[j] == "/" and not in_class:
                        break
                    j += 1
                blank(i + 1, j)
                i = j + 1
            else:
                i += 1
        else:
            i += 1
    return "".join(out)


def close_of(masked: str, i: int) -> int:
    """masked[i] opens a bracket: the index just after its partner, or -1."""
    pairs = {"(": ")", "{": "}", "[": "]"}
    stack = [pairs[masked[i]]]
    i += 1
    while i < len(masked):
        c = masked[i]
        if c in pairs:
            stack.append(pairs[c])
        elif c in ")}]":
            if c != stack[-1]:
                return -1
            stack.pop()
            if not stack:
                return i + 1
        i += 1
    return -1


DEF = re.compile(r"\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(|\b([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?function\b\s*\*?\s*\(")
ARROW = re.compile(r"\b([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?(?:\([^()]*\)|[A-Za-z_$][\w$]*)\s*=>")


def functions(path: Path):
    """(name, normalised "(params) {body}", body length) for every named function in a script."""
    src = path.read_text(encoding="utf-8")
    masked = mask(src)
    for m in DEF.finditer(masked):
        name = m.group(1) or m.group(2)
        po = m.end() - 1
        pc = close_of(masked, po)
        if pc == -1:
            continue
        bo = pc
        while bo < len(masked) and masked[bo] in " \t\r\n":
            bo += 1
        if bo >= len(masked) or masked[bo] != "{":
            continue
        bc = close_of(masked, bo)
        if bc == -1:
            continue
        body = " ".join(src[bo:bc].split())
        yield name, " ".join(src[po:pc].split()) + " " + body, len(body)


def defined_helpers(path: Path) -> list[str]:
    masked = mask(path.read_text(encoding="utf-8"))
    names = [m.group(1) or m.group(2) for m in DEF.finditer(masked)] + [m.group(1) for m in ARROW.finditer(masked)]
    return sorted({n for n in names if n in HELPERS})


def copies() -> set[tuple[str, str, str]]:
    """(name, hash, file) for every function whose body is in more than one file."""
    seen: dict[str, set[tuple[str, str]]] = {}
    for p in scripts():
        rel = p.relative_to(ROOT).as_posix()
        for name, text, length in functions(p):
            if length <= MIN_BODY:
                continue
            h = hashlib.sha1(text.encode()).hexdigest()[:10]
            seen.setdefault(h, set()).add((name, rel))
    out = set()
    for h, where in seen.items():
        if len({rel for _, rel in where}) > 1:
            out |= {(name, h, rel) for name, rel in where}
    return out


def read_baseline() -> set[tuple[str, str, str]]:
    if not BASELINE.exists():
        return set()
    out = set()
    for line in BASELINE.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            name, h, rel = line.split()
            out.add((name, h, rel))
    return out


def write_baseline(rows: set[tuple[str, str, str]]) -> None:
    head = ("# Functions copied word for word between scripts when tests/test_ui_contract.py came in (#461).\n"
            "# This list may only shrink. Move a helper into app/static/relay-ui.js, delete its lines here.\n"
            "# name  body-hash  file\n")
    BASELINE.write_text(head + "".join(f"{n} {h} {r}\n" for n, h, r in sorted(rows, key=lambda r: (r[1], r[2]))))


SCRIPT_SRC = re.compile(r"<script[^>]*\bsrc=\"([^\"]+)\"")


def script_file(src: str) -> Path | None:
    m = re.search(r"static\('([^']+)'\)", src)
    path = m.group(1) if m else (src[len("/static/"):] if src.startswith("/static/") else None)
    if path is None:
        return None
    if path.startswith("modules/"):
        _, mid, rest = path.split("/", 2)
        return APP / "modules" / mid / "static" / rest
    return APP / "static" / path


def templates() -> list[Path]:
    return sorted(list((APP / "templates").glob("*.html")) + list((APP / "modules").glob("*/templates/*.html")))


def main() -> None:
    if "--rebaseline" in sys.argv:
        now, old = copies(), read_baseline()
        new = now - old
        if new and BASELINE.exists():
            print("Not rewriting: these copies are new, and the baseline may only shrink:")
            for row in sorted(new):
                print("  ", *row)
            sys.exit(1)
        write_baseline(now)
        print(f"baseline: {len(old)} -> {len(now)} lines")
        return

    with section("the shared script"):
        check("app/static/relay-ui.js exists", SHARED.exists())
        text = SHARED.read_text()
        for name in HELPERS:
            check(f"relayUI exports {name}", re.search(r"\b" + name + r": " + name + r"\b", text))

    with section("no new copies between scripts"):
        now, base = copies(), read_baseline()
        new = sorted(now - base)
        gone = sorted(base - now)
        eq("copies not in the baseline (move the helper into relay-ui.js instead)", new, [])
        eq("baseline lines for copies that are gone (delete them, or run with --rebaseline)", gone, [])
        for name in ("can", "send", "h", "el", "say", "api"):
            check(f"the baseline holds no copy of {name}()", not any(r[0] == name for r in base))

    with section("module scripts use the shared helpers"):
        bad = {}
        for p in scripts():
            rel = p.relative_to(ROOT).as_posix()
            if not rel.startswith("app/modules/") or rel in OWN_HELPERS_ALLOWED:
                continue
            names = defined_helpers(p)
            if names:
                bad[rel] = names
        eq("module scripts defining can/api/el/say/confirm (use relayUI's)", bad, {})

    with section("confirm() goes through relayUI"):
        bad = []
        for p in scripts():
            if p == SHARED:
                continue
            masked = mask(p.read_text(encoding="utf-8"))
            for m in re.finditer(r"(?<![\w$.])(?:window\.)?confirm\s*\(", masked):
                bad.append(f"{p.relative_to(ROOT).as_posix()}:{masked.count(chr(10), 0, m.start()) + 1}")
        eq("direct confirm() calls (use await relayUI.confirm)", bad, [])

    with section("pages load relay-ui.js before the scripts that use it"):
        for t in templates():
            html = t.read_text(encoding="utf-8")
            rel = t.relative_to(ROOT).as_posix()
            srcs = SCRIPT_SRC.findall(html)
            loaded = False
            for src in srcs:
                f = script_file(src)
                if f == SHARED:
                    loaded = True
                elif f is not None and f.exists() and "relayUI" in f.read_text(encoding="utf-8"):
                    check(f"{rel}: relay-ui.js before {f.name}", loaded)
            if "module_scripts" in html:
                at = html.find("module_scripts")
                first = html.find("relay-ui.js")
                check(f"{rel}: relay-ui.js before the module scripts", first != -1 and first < at)

    with section("the scanner reads every script"):
        # A script the scanner cannot balance would hide its copies; this keeps it honest.
        for p in scripts():
            masked = mask(p.read_text(encoding="utf-8"))
            depth = sum(masked.count(c) for c in "({[") - sum(masked.count(c) for c in ")}]")
            check(f"{p.relative_to(ROOT).as_posix()} balances", depth == 0, depth)

    done()


if __name__ == "__main__":
    main()
