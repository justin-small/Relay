# Multi-port networking: hardware test plan

Run by hand before a release that changes networking (`app/netinfo.py`, `app/netroute.py`, `app/netsock.py`,
`app/serving.py`, or a module's traffic). CI covers the logic with fake ports (`tests/_netfake.py`); only real
adapters show what the operating system does with them. Record the date, Relay version, OS version and adapters
used at the bottom of the pull request or release notes, and mark any step not run as untested.

## Setup

- A Mac and a Windows laptop, each with two USB Ethernet adapters, plus Wi-Fi.
- Adapter 1 on a Dante network: a switch with at least one Dante device (a console, a stagebox or Dante Virtual
  Soundcard on another computer). No DHCP, so the adapter takes a `169.254.x.x` address, unless the network has a
  DHCP server.
- Adapter 2 on a second LAN with DHCP and a phone on its Wi-Fi (the crew network).
- Wi-Fi on the venue network with internet.
- On a Linux box, the same once with and once without `AmbientCapabilities=CAP_NET_RAW` in the service unit.

Route on the **Network** page: crew to adapter 2, Dante to adapter 1 (strict), translation to Wi-Fi, viewers left
on the default network.

## Checks

| # | Check | Expected |
|---|---|---|
| 1 | Open **Network** | Three ports with names, addresses, link up, speed; no warnings except the self-assigned note for the Dante port. |
| 2 | **Test** on each routed class | "Leaves from" the chosen port's address. |
| 3 | Dante discovery | The Dante devices on adapter 1 are found; none appear when Dante is routed to adapter 2. |
| 4 | Dante control | A route change and a level change reach the device. |
| 5 | Panel off the Dante port | From a computer on the Dante network, `https://<Relay's Dante address>/admin` does not answer; from the crew phone, the crew address does. |
| 6 | Pull adapter 1's cable | Within 5 s the port shows down; the Dante module's health names the port; nothing Dante leaves through another port (check with Wireshark on Wi-Fi). |
| 7 | Re-plug it | The port comes back, the module reconnects by itself, health clears. |
| 8 | Unplug adapter 1 from USB | The port shows as not connected and keeps its route; plug it back in: the route works again without a change. |
| 9 | Pull adapter 2 (crew, fallback) | Crew pages move to the default network; the page says so; plug back: they move back. |
| 10 | Sleep and wake | After wake, every port and route is as before, and modules reconnect within 30 s. |
| 11 | Intercom routed to adapter 2 | A phone on the crew Wi-Fi talks and hears; LiveKit's log shows candidates on adapter 2 only. |
| 12 | Remote access routed to Wi-Fi | The tunnel connects; `cloudflared` runs with `--edge-bind-address`. |
| 13 | Two adapters on one subnet | The page warns that they share a network. |
| 14 | Linux without `CAP_NET_RAW` | The page shows the capability note; Dante devices on adapter 1 still answer. |
| 15 | Windows | All of the above; also that replies to the crew phone leave through adapter 2. |

## Not covered here

NDI's own adapter settings need the NDI SDK and a real NDI source; test them with the NDI module's own plan.
