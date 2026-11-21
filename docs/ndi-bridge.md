# NDI Bridge across sites (#303)

NDI Bridge is NDI's own tool for carrying NDI between buildings (a second campus, an overflow room, a remote producer) over one
encrypted TCP port. Relay does not run or replace it. On the NDI page, Setup tab, **Other sites (NDI Bridge)** helps with it.

## What the spike found

- **Windows:** the Bridge app takes `/host`, `/join`, `/local`, `/autorun` and `/noui`. A Windows Bridge Service has an API and an
  optional web page.
- **Linux:** the Embedded Bridge is a command line that only **joins** (a JSON config; needs a vendor ID from NDI); a Bridge
  Service does host and join.
- **macOS:** no Bridge is named in NDI's documentation.
- No documented way to list a Bridge's connections, so Relay cannot read its status, and it cannot start or stop one.
- Licence and redistribution terms were not on the pages read; Relay bundles nothing of Bridge.

## What Relay does

- Keeps a list of your Bridge machines: name, Host or Join, address and port (default 5990).
- Checks that a Host answers on its port every scan, so a missing port forward shows before the service.
- Marks sources whose address is a listed machine **Via Bridge** in the source list.
- Writes the start-up text: the Windows command line, or the Linux Embedded Bridge JSON, with the encryption key left as
  `YOUR-ENCRYPTION-KEY`. **Relay never stores the key**; type it into Bridge.

## Setting up two sites

1. At the main site, run Bridge as a **Host** on an always-on computer. Forward TCP 5990 from the router to it.
2. At the other site, run Bridge as a **Join** pointed at the Host's public address and port, with the same key.
3. List both here. The Host should show "Answers on port 5990"; sources from the other site show **Via Bridge** after the next scan.
4. Add a NDI discovery server or extra addresses if the other site's sources do not appear (Setup tab).

**Not proved:** none of this was run against a real Bridge, only against a local listener and hand-made source records.
