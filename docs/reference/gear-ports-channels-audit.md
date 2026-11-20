# Shared concepts audit, part 2: gear, ports, cables, channels and routes

Epic #539. Written 2026-11-19, after the first shared-concepts epic (#521) gave Relay one core Directory for people, teams,
positions, screens, places and phones. This audit looks at the next set of things every AV module ends up keeping
its own copy of: the **equipment** in the building, the **ports** (physical connectors) on it, the **cables** between
them, the **channels** (audio, video and intercom talk channels) that flow through them, and the **routes** that send
one channel to another.

## Summary

| Concept | Where it lives today | Copies |
| --- | --- | --- |
| Gear (a piece of equipment) | network map (merged on every read, never stored), Blackmagic devices (config), UniFi devices (live), OBS/REAPER/ProPresenter computers (config), audio devices (PortAudio, live) | 5+ |
| Places for gear | network map `places` (racks/closets, config), Blackmagic `place` (`rack`/`shelf`), core places (#527) | 3 |
| Ports | network map `ports` (switch ports by number), netinfo `Port` (this computer), Blackmagic connectors (only in `bmdraw.js`; the server knows ids only) | 3 |
| Port notes and names | network map `names`/`notes` keyed `mac` or `mac#port`, Blackmagic `notes` keyed by connector id | 2 |
| Cables | Blackmagic `cables` (only between Blackmagic devices), UniFi uplink (`uplink_mac` + `uplink_port`, live) | 2 |
| Audio channels | audio bus lanes and outputs, PortAudio device channels, rundown soundboard `channels` (typed name/number), rundown wireless mics (`mics` labels), metering sources | 5 |
| Video channels | NDI sources (mDNS discovery and the NDI module), NDI outputs, Videohub inputs/outputs (live labels), ATEM inputs, tally camera numbers, OBS scenes | 6 |
| Talk channels | `intercom_channels` (intercom module) | 1 |
| Routes | NDI router routes and salvos, Videohub routing, audio bus matrix, rundown channel ← position assignments | 4 |
| Seams | `Module.network_map()`, `Module.network_changed()`, `getattr(mod, "mixer_channels")`, `tally.publish`, `audio.register_source/output`, `metering.register_source`, `modules.ndi_sources()` | 7 |

## High confidence

### G1. There is no stored record of a piece of equipment

The network map (`app/network_map.py`) merges what core, discovery and five modules report (`network_map()` on UniFi,
OBS, REAPER, ProPresenter and Blackmagic) **on every read** and keeps nothing but a 20-second cache. A switch, a
Videohub or a stage box has no id that lasts: it is matched by MAC, else IP, else host name each time. Names and notes
the operator types are saved separately in config under `network_map.names`/`notes`, keyed by that MAC or IP text, so
a device that changes address loses them.

Blackmagic keeps its own device list in config (`blackmagic.devices`, ids like `atem-1`), with its own `place`, `model`,
`drawing` and `notes`. The same ATEM is one entry there and one merged device on the network map, with no link
between them.

### G2. Three "places" for equipment

- Core places (#527, `places` table): Foyer, Stage, Kids. Screens belong to one.
- Network map places (`network_map.places` in config): closets and racks, each with a list of gear keys (MAC or IP).
- Blackmagic `place`: `rack` or `shelf`, an enum that only says how to draw it.

A rack in the booth is a network map place, the booth is a core place, and the ATEM in that rack is "rack" in
Blackmagic. None of them point at each other.

### G3. "Port" means three things, and one more by accident

- A **switch or computer port**: network map `ports` (number, name, up, speed, PoE, client), netinfo `Port` (this
  computer's adapters, with an id), UniFi's port table. Keyed by number on a MAC (`aa:bb:..#12`).
- A **connector on AV gear**: Blackmagic's SDI, HDMI, XLR, Ethernet, USB and power connectors. Their list exists only
  in `app/modules/blackmagic/static/bmdraw.js` (`port('in1', 'sdi', ...)`); the server stores notes and cables by the
  connector id (`PORT_RE`) without knowing which connectors a device has.
- **Traffic routes** (`app/netroute.py`, #344): which network port a kind of traffic leaves by, keyed by netinfo's
  port id.
- By accident, a **TCP port number**: every device config has `"port": 9990` and every module listener has a
  `port_setting`. Any shared "port" name must not be confused with these.

### G4. Cables are recorded only between Blackmagic devices

Blackmagic's Wiring tab saves `cables: [{a: [device, connector], b: [device, connector], ...}]` in its own config.
A cable from the ATEM to a switch port, or from a Videohub to a TV, cannot be recorded. UniFi knows which switch port
a device is on (`uplink_mac`, `uplink_port`) but only live, and it is not shown as a cable.

### G5. Audio channels are kept five ways

- **Audio bus** (`app/audiobus.py`, core): 32 named lanes, each patched from a device channel; outputs of up to 32
  channels; a crosspoint matrix (lanes → output channels).
- **PortAudio devices** (`app/audio.py`): each device's channel count, read live.
- **Rundown soundboard channels** (`planning_center.rundown.channels`): typed `name` and `number` per channel, with the
  positions that sing on it. A soundboard module was expected to offer a list through `mixer_channels()`, found by
  `getattr` on every module (`planning_center/routes.py channel_catalog`). None exists yet.
- **Rundown wireless mics** (`rundown.mics`): mic labels such as "01" drawn for a position.
- **Metering sources** (`app/metering.py`): Open Sound Meter and ProdMesh RTA register a source id and label.

Dante is seen only as a device that announces `_netaudio-*` over mDNS; its channels are not read.

### G6. Video channels are kept six ways

NDI sources (core mDNS discovery and the NDI module's finder, `modules.ndi_sources()`), NDI outputs (the NDI module's
page outputs and router outputs), Videohub inputs and outputs (labels read live from the router), ATEM inputs, tally
cameras (`app/tally.py`, a camera number published by ATEM or Tally Arbiter), and OBS scenes. "Camera 2" can be ATEM
input 2, Videohub input 5, tally camera 2 and an NDI source, with nothing saying they are one signal.

### G7. Routes are kept four ways

- **NDI router** (`app/modules/ndi/router.py`): named outputs, each with a source, plus salvos.
- **Videohub routing**: output → input, read from and written to the router.
- **Audio bus matrix**: lane → output channel, with a gain.
- **Rundown channel assignments**: soundboard channel ← Planning Center position, per service type.

Each has its own editor, its own storage and its own idea of a "source".

### G8. Seams for gear and signals are ad hoc

`Module.network_map()` and `network_changed()` are methods on the module class; `mixer_channels` is found by
`getattr`; tally uses `tally.publish(source, cameras)`; audio uses `audio.register_source/register_output`; metering
uses `metering.register_source`; NDI uses `modules.provide("ndi")`. #523's `provide()`/`using()` covers only the last.

## Medium confidence

### M1. Names come from the gear, but Relay sometimes keeps its own

Videohub labels are read from the router and renamed there (`/api/admin/blackmagic/videohub/labels`). The network map
keeps Relay-only names (`network_map.names`). Audio bus lanes are named only in Relay. There is no rule for which name
wins, or whether a rename goes to the gear.

### M2. Intercom talk channels are a different kind of channel

`intercom_channels` are groups of people with a talk mode (preempt, party, queue), not signals. They can still be the
source or destination of a route (a mixer output feeding a talk channel, a talk channel feeding a stage speaker),
so they belong in the shared list with their own kind.

### M3. Signal presence is not known

The Blackmagic page decided (2026-11-18) not to light a connector green unless the device reports it. The shared
port and channel records must keep "unknown" apart from "no signal".

## Recommended standard: core Gear

1. **Stable refs.** `gear:relay:<id>`, `port:relay:<id>`, `cable:relay:<id>`, `channel:relay:<id>`,
   `route:relay:<id>`, built and read by one core module (`app/gear.py`), like `app/directory.py` and `app/screens.py`.
2. **Gear is stored.** One row per piece of equipment, adopted by key the way screens are (`unifi:<mac>`,
   `blackmagic:<id>`, `relay:this`, `dante:<name>`): two keys that are the same box join one record. It has a place
   (core places), a kind, a model and a last-seen time.
3. **Ports are any physical connector**, with a kind (`eth`, `sfp`, `sdi`, `hdmi`, `xlr`, `trs`, `usb`, `iec`, ...), a
   direction and optional network fields (speed, PoE, link up). Notes move here from the network map and Blackmagic.
4. **Cables join two ports**, typed in by an operator or reported by a module (UniFi's uplink), with who said so.
5. **Channels are audio, video or talk**, with a direction (source or destination), a number on their gear, and three
   names: the gear's, a Relay-only display name, and whether a rename can be sent to the gear.
6. **Routes send a source channel to a destination channel.** Core stores and shows them; the module that owns the
   destination applies them on the gear and reports what the gear really does (the gear is the truth).
7. **Stored, refreshed from modules.** Like teams and positions (#525), core caches what modules report, so names,
   cables and routes still show when a module or the gear is off, and something the gear no longer has is shown as
   missing.
8. **One seam.** A module provides `gear.<module id>` (its gear, ports and channels) and `routes.<module id>` (apply a
   route) with `modules.provide`; `network_map()`, `mixer_channels` and the other one-off hooks go.
9. **Enforced** by new rules in `tests/test_shared_concepts.py` with the shrink-only baseline: no new module table or
   config list of ports, connectors, cables, channels or routes.

## Decisions (Justin, 2026-11-19)

- **Port** is any physical connector (Ethernet, SFP, SDI, HDMI, XLR, USB, power). Network-only details are extra fields.
- **Channels** cover audio signals, video signals and intercom talk channels.
- **Core owns routes and cables** (one shared route/patch); each module carries out a change on its own gear.
- **A new core gear record** owns ports and channels; phones stay core devices (#528).
- **Names:** the gear is the source of truth. Relay can rename on the gear where the gear supports it, and can also
  keep a Relay-only display name.
- **Stored**, refreshed from modules; a missing one is flagged, not dropped.
- **Process:** this audit, then an epic with one issue and pull request per step.

### Answered the same day

- **Naming:** physical connectors are `ports` in code too (table, refs `port:relay:<id>`, API and UI). A TCP number stays
  only a field of a host address, never a table or ref.
- **Dante:** devices only for now. Dante boxes become gear from discovery; reading channels and subscriptions waits for
  a Dante module, which plugs into the same seam.
- **Racks:** a rack is inside a place. Core places stay rooms; racks are a level below them (Booth > Rack A), and gear
  sits in a rack or straight in a room.

## Order (one issue and PR each)

1. Core `app/gear.py`: gear records, refs and adoption by key; the network map stores what it merges; racks inside
   core places (network map racks and Blackmagic `place` move there); Dante devices from discovery; the shared-concepts
   rules.
2. Core ports: the `ports` table and port kinds; network map ports, this computer's ports and Blackmagic's connectors
   (moved from `bmdraw.js` to the server) become ports; notes migrate.
3. Core cables: Blackmagic cables migrate; UniFi uplinks show as reported cables; any two ports can be cabled.
4. Core channels: audio bus lanes and outputs, NDI sources and outputs, Videohub and ATEM inputs and outputs, tally
   cameras and intercom talk channels become channels; the rundown's soundboard channels pick from them.
5. Names: one rename flow with gear name, rename-on-gear when supported, and Relay-only name; Videohub labels first.
6. Core routes: NDI router, Videohub routing and the audio bus matrix read and write through one route model; the
   rundown's channel assignments use channel refs.
7. One seam: `provide("gear.<id>")` and `provide("routes.<id>")` replace `network_map()`, `mixer_channels`,
   `tally.publish` and the rest; docs/modules.md updated.
8. Shared pickers: one port picker, channel picker and route editor in `relay-ui.js`, used by the Network, Blackmagic,
   NDI, Audio and Planning Center pages.
