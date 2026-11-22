"""Serial links (#629): framing, settings, and a Link over real TCP, UDP and a local serial port (a pty), against fake
devices on loopback. Run: python tests/test_serialio.py

The fake devices are in this file. The Waveshare RS232/485/422 to PoE ETH (B) was also run by hand with a loopback on
its RS-232 port (echo, latency, all four terminators, all 256 byte values, 2000 random bytes, reconnect), which is the
real-hardware proof; the Furman behind it is not proven yet.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import serialio  # noqa: E402
from _harness import await_for, check, done, eq, section  # noqa: E402

FAST = (0.02, 0.05)          # retry soon: the tests wait for a reconnect, they do not sleep through a backoff


def bad(fn, *args):
    """The sentence of the SettingsError fn raises, or None."""
    try:
        fn(*args)
    except serialio.SettingsError as exc:
        return str(exc)
    return None


async def raises(coro):
    """The sentence of the LinkError a coroutine raises, or None."""
    try:
        await coro
    except serialio.LinkError as exc:
        return str(exc)
    return None


with section("Framing"):
    eq("CR, LF and NUL each end a frame", serialio.split_frames(b"a\rb\nc\x00d"), ([b"a", b"b", b"c"], b"d"))
    eq("CRLF is one boundary", serialio.split_frames(b"one\r\ntwo\r\n", (b"\r\n",)), ([b"one", b"two"], b""))
    eq("CRLF listed with CR is still one boundary",
       serialio.split_frames(b"one\r\ntwo\r", (b"\r", b"\r\n")), ([b"one", b"two"], b""))
    eq("empty frames are dropped", serialio.split_frames(b"\r\r\rx\r"), ([b"x"], b""))
    eq("nothing ended, all kept", serialio.split_frames(b"abc"), ([], b"abc"))
    eq("the four named terminators", sorted(serialio.TERMINATORS), ["cr", "crlf", "lf", "nul"])

with section("Settings"):
    eq("TCP settings", serialio.clean_link({"kind": "tcp", "host": " 10.10.0.181 ", "port": "4196", "baud": 9}),
       {"kind": "tcp", "host": "10.10.0.181", "port": 4196})
    eq("kind defaults to tcp", serialio.clean_link({"host": "x", "port": 1})["kind"], "tcp")
    eq("a serial port gets 9600 8N1 by default", serialio.clean_link({"kind": "serial", "device": "/dev/ttyUSB0"}),
       {"kind": "serial", "device": "/dev/ttyUSB0", "baud": 9600, "databits": 8, "parity": "none", "stopbits": 1,
        "flow": "none"})
    eq("serial framing is kept", serialio.clean_link({"kind": "serial", "device": "COM3", "baud": 19200, "databits": 7,
                                                      "parity": "Even", "stopbits": 2, "flow": "rtscts"}),
       {"kind": "serial", "device": "COM3", "baud": 19200, "databits": 7, "parity": "even", "stopbits": 2,
        "flow": "rtscts"})
    check("an unknown kind is refused", bad(serialio.clean_link, {"kind": "carrier-pigeon"}))
    check("no host is refused", bad(serialio.clean_link, {"kind": "tcp", "port": 1}))
    check("a host with a slash is refused", bad(serialio.clean_link, {"kind": "tcp", "host": "a/b", "port": 1}))
    check("port 0 is refused", bad(serialio.clean_link, {"kind": "udp", "host": "a", "port": 0}))
    check("a port that is not a number is refused", bad(serialio.clean_link, {"kind": "udp", "host": "a", "port": "x"}))
    check("no serial port is refused", bad(serialio.clean_link, {"kind": "serial"}))
    check("a bad parity is refused", bad(serialio.clean_link, {"kind": "serial", "device": "d", "parity": "x"}))
    check("a bad flow is refused", bad(serialio.clean_link, {"kind": "serial", "device": "d", "flow": "x"}))
    check("3 stop bits are refused", bad(serialio.clean_link, {"kind": "serial", "device": "d", "stopbits": 3}))
    check("9 data bits are refused", bad(serialio.clean_link, {"kind": "serial", "device": "d", "databits": 9}))
    check("nothing is not settings", bad(serialio.clean_link, None))
    check("the port list is a list", isinstance(serialio.list_ports(), list))


class Device:
    """A fake serial server: answers each line with `OK <line>`, can push unasked, and can drop its clients."""

    def __init__(self):
        self.server = None
        self.port = 0
        self.writers: list[asyncio.StreamWriter] = []
        self.heard: list[bytes] = []
        self.opened = 0
        self.push = b""

    async def start(self, port: int = 0):
        self.server = await asyncio.start_server(self._client, "127.0.0.1", port)
        self.port = self.server.sockets[0].getsockname()[1]

    async def _client(self, reader, writer):
        self.opened += 1
        self.writers.append(writer)
        if self.push:
            writer.write(self.push)
        try:
            while True:
                line = await reader.readuntil(b"\r")
                self.heard.append(line)
                if line.startswith(b"?SLOW"):
                    continue                       # never answers
                writer.write(b"OK " + line)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass

    async def drop(self):
        for w in self.writers:
            w.close()
        self.writers.clear()

    async def stop(self):
        await self.drop()
        self.server.close()
        await self.server.wait_closed()


async def tcp() -> None:
    dev = Device()
    await dev.start()
    link = serialio.Link(serialio.clean_link({"kind": "tcp", "host": "127.0.0.1", "port": dev.port}), name="t",
                         backoff=FAST)
    with section("TCP: connect and request"):
        eq("it starts not connected", link.state()["connected"], False)
        await link.start()
        check("it connects", await link.wait_connected(2))
        st = link.state()
        check("state says connected, with no error and a start time", st["connected"] and not st["last_error"]
              and st["connected_since"])
        eq("a request gets its reply", await link.request(b"?ID 0\r"), b"OK ?ID 0\r")
        eq("the device heard it", dev.heard, [b"?ID 0\r"])
        check("bytes are counted", link.state()["bytes_out"] == 6 and link.state()["bytes_in"] == 9, link.state())
        eq("a reply ended by something other than CR", await link.request(b"x\r", until=b"\r"), b"OK x\r")

    with section("TCP: one request at a time"):
        replies = await asyncio.gather(*(link.request(b"n%d\r" % i) for i in range(5)))
        eq("every request gets its own reply, in order", replies, [b"OK n%d\r" % i for i in range(5)])

    with section("TCP: reading"):
        link.clear()
        async with link.exchange():
            await link.send(b"a\r")
            eq("read_exactly waits for n bytes", await link.read_exactly(5), b"OK a\r")
            await link.send(b"b\r")
            eq("read_until takes the first terminator of several",
               await link.read_until((b"\n", b"\r", b"\x00")), b"OK b\r")
        eq("a request that is never answered times out with a sentence",
           await raises(link.request(b"?SLOW\r", timeout=0.2)), "The device did not answer in time.")
        eq("and the next request still works", await link.request(b"ok\r"), b"OK ok\r")
        link.clear()
        dev.push = b""
        async with link.exchange():
            await link.send(b"z\r")
            eq("a reply with no end is an error once it is too long",
               await raises(link.read_until(b"\x00", timeout=0.5, limit=2)), "The reply had no end.")

    with section("TCP: reconnect"):
        before = dev.opened
        await dev.drop()
        check("losing the connection is seen", await await_for(lambda: not link.state()["connected"]
                                                                or dev.opened > before))
        check("it connects again by itself", await await_for(lambda: link.connected and dev.opened > before))
        eq("and works", await link.request(b"again\r"), b"OK again\r")
        before = dev.opened
        link.reconnect()
        check("reconnect() opens a fresh connection", await await_for(lambda: link.connected and dev.opened > before))
        eq("and that works too", await link.request(b"third\r"), b"OK third\r")

    with section("TCP: a device that is not there yet"):
        port = dev.port
        await dev.stop()
        check("the link notices", await await_for(lambda: not link.connected))
        check("it says why", await await_for(lambda: link.state()["last_error"]), link.state())
        eq("sending now is refused", await raises(link.send(b"x")), "Not connected.")
        eq("so is a request", await raises(link.request(b"x\r")), "Not connected.")
        check("attempts are counted", await await_for(lambda: link.state()["attempts"] >= 2), link.state())
        dev2 = Device()
        await dev2.start(port)
        check("the device appears: it connects", await link.wait_connected(3))
        check("the error clears", link.state()["last_error"] == "")
        eq("and it works", await link.request(b"back\r"), b"OK back\r")
        await dev2.stop()

    await link.close()
    eq("a closed link is not connected", link.connected, False)

    with section("TCP: unasked data and state callbacks"):
        dev3 = Device()
        dev3.push = b"*EVENT 1\r"
        await dev3.start()
        heard, states = [], []
        link3 = serialio.Link(serialio.clean_link({"kind": "tcp", "host": "127.0.0.1", "port": dev3.port}),
                              on_data=heard.append, on_state=lambda: states.append(link3.connected), backoff=FAST)
        await link3.start()
        check("on_data hears what the device says first", await await_for(lambda: b"".join(heard) == b"*EVENT 1\r"),
              heard)
        eq("on_state heard it connect", states[:1], [True])
        eq("a reader can still read it", await link3.read_until(b"\r"), b"*EVENT 1\r")
        await dev3.drop()
        check("on_state hears it drop", await await_for(lambda: False in states), states)
        await link3.close()
        await dev3.stop()

    with section("TCP: nothing listening"):
        dev4 = Device()
        await dev4.start()
        gone = dev4.port
        await dev4.stop()
        link4 = serialio.Link(serialio.clean_link({"kind": "tcp", "host": "127.0.0.1", "port": gone}), backoff=FAST)
        await link4.start()
        check("it reports a fixed sentence", await await_for(lambda: link4.state()["last_error"]
                                                              .startswith("Could not connect")), link4.state())
        await link4.close()


class Echo(asyncio.DatagramProtocol):
    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.transport.sendto(b"UDP " + data, addr)


async def udp() -> None:
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(Echo, local_addr=("127.0.0.1", 0))
    port = transport.get_extra_info("sockname")[1]
    with section("UDP"):
        link = serialio.Link(serialio.clean_link({"kind": "udp", "host": "127.0.0.1", "port": port}), backoff=FAST)
        await link.start()
        check("a UDP link is connected as soon as it is open", await link.wait_connected(2))
        eq("a datagram out, a datagram back", await link.request(b"hi\r"), b"UDP hi\r")
        eq("and again", await link.request(b"two\r"), b"UDP two\r")
        await link.close()
    transport.close()


async def local_serial() -> None:
    with section("Local serial port (a pty)"):
        if sys.platform == "win32":
            print("  (skipped: no pty on Windows)")
            return
        import tty

        master, slave = os.openpty()
        tty.setraw(slave)
        name = os.ttyname(slave)
        loop = asyncio.get_running_loop()
        got = bytearray()

        def device() -> None:
            """The fake device: answers each CR-ended line with OK and the line."""
            try:
                got.extend(os.read(master, 256))
            except OSError:
                return
            while b"\r" in got:
                line, _, rest = bytes(got).partition(b"\r")
                got[:] = rest
                os.write(master, b"OK " + line + b"\r")

        os.set_blocking(master, False)
        loop.add_reader(master, device)
        link = serialio.Link(serialio.clean_link({"kind": "serial", "device": name, "baud": 19200}), backoff=FAST)
        await link.start()
        check("the port opens", await link.wait_connected(3), link.state())
        eq("a request gets its reply", await link.request(b"?ID 0\r", timeout=2), b"OK ?ID 0\r")
        eq("and another", await link.request(b"?STATUS 0\r", timeout=2), b"OK ?STATUS 0\r")
        await link.close()
        loop.remove_reader(master)
        eq("closing leaves it not connected", link.connected, False)
        missing = serialio.Link(serialio.clean_link({"kind": "serial", "device": os.path.join(TMP, "no-such-port")}),
                                backoff=FAST)
        await missing.start()
        check("a port that is not there says so", await await_for(lambda: missing.state()["last_error"]
                                                                  .startswith("Could not open the serial port")),
              missing.state())
        await missing.close()
        os.close(master)
        os.close(slave)


async def main() -> None:
    await tcp()
    await udp()
    await local_serial()


asyncio.run(main())
done()
