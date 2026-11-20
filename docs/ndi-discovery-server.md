# NDI across subnets: use a discovery server

NDI finds sources with multicast DNS, which stops at a router. On a church network with several VLANs, or where the
network blocks multicast, sources on another VLAN do not show up. An **NDI Discovery Server** fixes that: every NDI
machine registers its sources with one server and asks it for the list, so the network between them does not matter.
On a multi-VLAN site use a discovery server first and treat a router's mDNS proxy as the fallback (a proxy answers
with the gateway's address and often leaves out the records a receiver needs).

## Set it up

1. **Run the server.** Install NDI Tools from <https://ndi.video/tools> on a computer that is always on and open
   *Discovery Service*. Give it a fixed address. It listens on TCP 5959. Relay does not run one: NDI's licence does
   not allow bundling it.
2. **Point every NDI machine at it.** In NDI Tools' *Access Manager* on each camera PC, converter host, vMix, OBS and
   ProPresenter computer, add the server's address under *Discovery Server*. Use the same address everywhere.
   Hardware (PTZ cameras, BirdDog and similar converters) has the setting in its own web page.
3. **Point Relay at it.** Admin > NDI > *Discovery server*: enter the address (port 5959 unless you changed it) and
   save. Relay shows *connected* and how many sources it reports. Turn off *Also look with multicast DNS* if
   multicast is blocked. **Restart Relay** after changing it: the NDI runtime reads these settings once, at start.

Relay writes its own NDI config in its state folder (`ndi/ndi-config.v1.json`) and never changes the NDI settings of
the computer it runs on. A source in a custom group still needs that group in *Groups to watch*.
