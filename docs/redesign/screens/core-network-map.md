# Core page: Network map

Mockup: `docs/mockups/admin-shell/pages/core-network-map.js`, open `index.html#network_map`. Issue #358 (with #356).

The network map is a **core** page that modules feed. No module owns it, and it works with any set of modules on
or off. UniFi is one source among several; its own pages (**Internet health**, **Settings**) sit under a
**UniFi** parent in the nav and are marked *soon* in the mockup. The page is new, so it has no ids that existing
scripts or tests rely on.

## Sources

- **Relay (core, always on):** this computer and its network ports, from the Network page's data.
- **Discovery (core, always on):** NDI sources (`_ndi._tcp`) and Dante devices (`_netaudio-*`) that announce
  themselves over mDNS on Relay's networks. Core by decision (see below).
- **Modules, when on:** each one contributes devices and facts through one core hook and never imports another
  module. Core never imports a module.
  - **UniFi:** switches, APs, which port each device is on, link speed, PoE, errors, traffic, traffic flows.
  - **ATEM:** names its switchers.
  - **ProPresenter:** marks the machines it controls and their NDI output.
  - **OBS Studio:** marks the stream machine and which NDI sources it receives.

Turning a module off removes only what it added. Devices are merged by MAC address, then by IP address.

## Page (`network_map`)

- **Route / permission:** core admin page `network_map` in the **Live** group, `network.view`, like the Network
  page.
- **Status row:** how many devices are shown and which sources supplied them, plus when they last updated.
  **Refresh** asks every source again. If one source fails (the UniFi controller does not answer), the badge says
  *Partial*, that source's part of the map turns grey with its age, and the rest stays live.
- **Things to check:** shown only when a source can see the problem (slow links and busy uplinks need UniFi).
- **Stats:** switches and APs (shows "—" and "Needs a module that reads switches" without one), clients or
  devices found, NDI sources, Dante devices and the PTP leader.
- **Map card:**
  - **With a switch source:** gateway, core switch, then each switch and AP, with wired clients in blocks of up to
    three columns. Links show their speed and how much is used.
  - **Without one:** Relay server, then its network ports, then the devices found on each port.
  - Line colour: NDI, Dante, over 70% used, slower than expected (dashed). The last two appear only when a source
    knows link speed and traffic.
  - **Map / Table** tabs, the **All / AV only / NDI / Dante** filter, and **Find name, IP or MAC**.
- **Ports card:** the port table of the selected switch. Without a switch source, it says which kind of module
  fills it in and links to Modules.
- **Detail card:** whatever is selected. For a device it shows:
  - **Seen by:** the sources that reported it.
  - **Identified by:** how it was found to carry NDI or Dante, by mDNS, port numbers or traffic flows. A crossed-out
    method is not available.
- **Sources card:** every source with what it adds and whether it is on, plus a link to Modules.
- **Mockup states:** UniFi on; UniFi with no flow data; UniFi not answering; UniFi off; View only.

## What the real implementation must do

- A core registry, for example `app/network_map.py`, that modules add to with a hook in the same style as the other
  module hooks: each source returns devices and links with its id. The page asks for the merged JSON and draws the
  SVG itself, from tokens only, in every preset and both themes.
- Keep every source's credentials server-side.
- Mark a device NDI or Dante when **any** source says so, and show a note when they disagree.
- `tests/test_modules.py` must still pass: the map's core files import no module, and the list of sources comes
  from the registry, not from module names in core.
- At phone width the map scrolls inside its frame; the cards stack.

## Decided

- mDNS discovery of NDI and Dante is **core** (Justin, 2026-11-16). It listens for announcements on Relay's own
  networks and calls no product's API, so it is part of the map, like Relay's own ports. It is not a module and
  cannot be switched off. If `tests/test_modules.py` flags the listener as network code in core, list it in
  `CORE_NETWORK` and give this reason in the pull request.
