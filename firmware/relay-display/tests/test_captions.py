"""The caption client against a fake Relay: connect, receive, drop, resume, without duplicates or gaps.

The emulator's network puts the host at 10.0.2.2, so `FakeRelay` listens on the host's loopback and the
panel reaches it there. A feed is pointed at it from the console (`feed <slot> <url> <stream> [lang]`),
and the panel's buffer is read back with `captions <slot>`, which logs every line it holds with its seq.
Each check compares that with the fake's own history, line for line.

`provisioned` is one board for the session. Each test sets the feeds it uses and turns them off after,
and a new feed starts with an empty buffer, so the tests do not see each other's lines.

What this does not cover is the radio: Wi-Fi drops, roaming and an hour of real traffic are the bench
item in README.md.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest

from conftest import wait_ready
from fake_relay import FakeRelay
from qemu import Board, free_port

ANSI = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture
def panel(provisioned: Board) -> Iterator[Board]:
    wait_ready(provisioned)
    yield provisioned
    assert provisioned.uart is not None
    mark = provisioned.uart.mark()
    for slot in (0, 1):
        _send(provisioned, f"feed {slot} off")
    # Turning slot 0 off puts the bench picture back, on the screen task's next turn, which the emulator takes a
    # moment to reach. A later test reads the framebuffer, so it is waited for here.
    provisioned.uart.wait_for_after(mark, r"panel: test screen shown, backlight on", timeout=30)


@pytest.fixture
def relay() -> Iterator[FakeRelay]:
    fake = FakeRelay()
    yield fake
    fake.close()


def _send(board: Board, line: str) -> int:
    assert board.uart is not None
    mark = board.uart.mark()
    board.uart.send_line(line)
    board.uart.wait_for_after(mark, r"console: feed set|captions\[\d\]: dump end")
    return mark


def set_feed(board: Board, slot: int, url: str, stream: str = "translation", lang: str = "SPANISH") -> int:
    """Point `slot` at `url`. Returns the console mark from before the command."""
    tail = f" {lang}" if stream == "translation" else ""
    return _send(board, f"feed {slot} {url} {stream}{tail}")


def wait(board: Board, mark: int, pattern: str, timeout: float = 30.0) -> re.Match[str]:
    assert board.uart is not None
    return board.uart.wait_for_after(mark, pattern, timeout=timeout)


def dump(board: Board, slot: int) -> list[tuple[int, str]]:
    """What the panel holds for `slot`, oldest first."""
    assert board.uart is not None
    mark = _send(board, f"captions {slot}")
    text = ANSI.sub("", board.uart.text[mark:])
    begin = text.index(f"captions[{slot}]: dump begin")
    end = text.index(f"captions[{slot}]: dump end", begin)
    return [
        (int(m.group(1)), m.group(2))
        for m in re.finditer(rf"captions\[{slot}\]: #(\d+) (.*?)\r?$", text[begin:end], re.M)
    ]


def test_a_new_feed_fills_from_the_snapshot(panel: Board, relay: FakeRelay) -> None:
    for i in range(5):
        relay.commit(f"Línea número {i}.")
    mark = set_feed(panel, 0, relay.url)
    wait(panel, mark, r"captions\[0\]: stream open, fresh")
    wait(panel, mark, r"captions\[0\]: snapshot, 5 lines")
    wait(panel, mark, r"captions\[0\]: connected")
    assert dump(panel, 0) == relay.lines()

    request = relay.requests[-1]
    assert request == {"stream": "translation", "lang": "SPANISH", "finals": "1"}

    relay.targets()
    wait(panel, mark, r"captions\[0\]: targets \(\d+ bytes\)")


def test_finished_lines_arrive_as_they_are_committed(panel: Board, relay: FakeRelay) -> None:
    mark = set_feed(panel, 0, relay.url)
    wait(panel, mark, r"captions\[0\]: snapshot|captions\[0\]: reset")
    for i in range(10):
        seq = relay.commit(f"line {i}")
        wait(panel, mark, rf"captions\[0\]: line {seq} ")
    assert dump(panel, 0) == relay.lines()


def test_a_drop_resumes_with_only_the_lines_missed(panel: Board, relay: FakeRelay) -> None:
    mark = set_feed(panel, 0, relay.url)
    wait(panel, mark, r"captions\[0\]: stream open, fresh")
    before = [relay.commit(f"before the drop {i}") for i in range(3)]
    wait(panel, mark, rf"captions\[0\]: line {before[-1]} ")

    # The stream is cut, and Relay commits three lines before the panel is back.
    relay.door.clear()
    dropped = panel.uart.mark()
    relay.drop()
    wait(panel, dropped, r"captions\[0\]: connection closed by the server")
    relay.wait_requests(2)
    during = [relay.commit(f"while away {i}") for i in range(3)]
    relay.door.set()

    wait(panel, dropped, rf"captions\[0\]: stream open, resuming after {before[-1]}")
    wait(panel, dropped, rf"captions\[0\]: line {during[-1]} ")
    assert relay.requests[-1]["last_id"] == str(before[-1])
    # Only the missed lines came, once each, and nothing was rebuilt.
    assert panel.uart.count(r"captions\[0\]: snapshot", after=dropped) == 0
    for seq in before:
        assert panel.uart.count(rf"captions\[0\]: line {seq} ", after=dropped) == 0
    after = relay.commit("after the drop")
    wait(panel, dropped, rf"captions\[0\]: line {after} ")

    held = dump(panel, 0)
    assert held == relay.lines()
    assert len({seq for seq, _ in held}) == len(held) == 7


def test_the_buffer_keeps_the_newest_200_lines(panel: Board, relay: FakeRelay) -> None:
    mark = set_feed(panel, 0, relay.url)
    wait(panel, mark, r"captions\[0\]: stream open, fresh")
    seqs = [relay.commit(f"line {i}") for i in range(250)]
    wait(panel, mark, rf"captions\[0\]: line {seqs[-1]} \(200 kept\)", timeout=60)
    held = dump(panel, 0)
    assert len(held) == 200
    assert held == relay.lines()[-200:]


def test_a_reboot_refills_200_lines_from_the_snapshot(panel: Board, relay: FakeRelay) -> None:
    """Relay set to keep 200 lines or more: a panel with an empty buffer gets all 200 back."""
    for i in range(300):
        relay.commit(f"history {i}")
    mark = set_feed(panel, 0, relay.url)
    wait(panel, mark, r"captions\[0\]: snapshot, 200 lines", timeout=60)
    assert dump(panel, 0) == relay.lines()[-200:]


def test_a_reset_clears_the_buffer(panel: Board, relay: FakeRelay) -> None:
    for i in range(3):
        relay.commit(f"old {i}")
    mark = set_feed(panel, 0, relay.url)
    wait(panel, mark, r"captions\[0\]: snapshot, 3 lines")
    relay.reset()
    wait(panel, mark, r"captions\[0\]: reset, buffer cleared")
    assert dump(panel, 0) == []
    seq = relay.commit("new run")
    wait(panel, mark, rf"captions\[0\]: line {seq} ")
    assert dump(panel, 0) == [(seq, "new run")]


def test_a_reset_while_away_clears_the_buffer_on_resume(panel: Board, relay: FakeRelay) -> None:
    """Relay is reset (Start) while the panel is disconnected, and nothing is said before it is back."""
    mark = set_feed(panel, 0, relay.url)
    wait(panel, mark, r"captions\[0\]: stream open, fresh")
    seq = relay.commit("from the last run")
    wait(panel, mark, rf"captions\[0\]: line {seq} ")
    relay.door.clear()
    away = panel.uart.mark()
    relay.drop()
    relay.wait_requests(2)
    relay.reset()
    relay.door.set()
    wait(panel, away, rf"captions\[0\]: stream open, resuming after {seq}")
    wait(panel, away, r"captions\[0\]: reset, buffer cleared")
    assert dump(panel, 0) == []


def test_a_silent_stream_is_reopened_and_resumed(panel: Board, relay: FakeRelay) -> None:
    mark = set_feed(panel, 0, relay.url)
    wait(panel, mark, r"captions\[0\]: stream open, fresh")
    seq = relay.commit("last word before the silence")
    wait(panel, mark, rf"captions\[0\]: line {seq} ")
    relay.muted = True
    quiet = panel.uart.mark()
    wait(panel, quiet, r"captions\[0\]: silent for 4 s; reconnecting")
    relay.muted = False
    wait(panel, quiet, rf"captions\[0\]: stream open, resuming after {seq}")
    assert dump(panel, 0) == relay.lines()


def test_relay_not_running_until_it_starts(panel: Board) -> None:
    port = free_port()
    mark = set_feed(panel, 0, f"http://10.0.2.2:{port}")
    wait(panel, mark, r"captions\[0\]: relay not running")
    wait(panel, mark, r"captions\[0\]: connection refused; trying again")
    relay = FakeRelay(port=port)
    try:
        seq = relay.commit("Relay is up")
        wait(panel, mark, r"captions\[0\]: stream open, fresh", timeout=60)
        wait(panel, mark, r"captions\[0\]: connected")
        wait(panel, mark, r"captions\[0\]: snapshot, 1 lines")
        assert dump(panel, 0) == [(seq, "Relay is up")]
    finally:
        relay.close()


def test_a_language_relay_does_not_offer_is_refused(panel: Board, relay: FakeRelay) -> None:
    mark = set_feed(panel, 0, relay.url, lang="KLINGON")
    wait(panel, mark, r"captions\[0\]: Relay answered 400")
    wait(panel, mark, r"captions\[0\]: feed refused")


def test_two_feeds_have_their_own_connections_and_buffers(panel: Board, relay: FakeRelay) -> None:
    mark = set_feed(panel, 0, relay.url)
    set_feed(panel, 1, relay.url, stream="transcription")
    wait(panel, mark, r"captions\[0\]: stream open, fresh")
    wait(panel, mark, r"captions\[1\]: stream open, fresh")
    for i in range(4):
        es = relay.commit(f"hola {i}")
        en = relay.commit(f"hello {i}", stream="transcription", lang="ENGLISH")
        wait(panel, mark, rf"captions\[0\]: line {es} ")
        wait(panel, mark, rf"captions\[1\]: line {en} ")
    assert dump(panel, 0) == relay.lines()
    assert dump(panel, 1) == relay.lines("transcription", "ENGLISH")
    assert {r["stream"] for r in relay.requests} == {"translation", "transcription"}


def test_characters_the_font_lacks_show_as_a_mark(panel: Board, relay: FakeRelay) -> None:
    mark = set_feed(panel, 0, relay.url)
    wait(panel, mark, r"captions\[0\]: stream open, fresh")
    seq = relay.commit("“Hola” — café \U0001F44D, señor 李…")
    wait(panel, mark, rf"captions\[0\]: line {seq} ")
    assert dump(panel, 0) == [(seq, '"Hola" - café ?, señor ?...')]
