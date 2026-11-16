# Using more than one network port {#network-ports}

Most churches run Relay on one network: the venue's Wi-Fi or a single cable.
Some production computers have several **network ports**: the built-in
Ethernet, USB or Thunderbolt Ethernet adapters, and Wi-Fi. Each one can sit on
a different network, for example one for the crew, one for audio and one for
the internet.

The **Network** page in the admin panel shows every port and lets you choose
which one each kind of Relay traffic uses. This chapter shows a typical layout,
what Relay can and cannot steer, and what to set on your network switches.

This is optional. With no port chosen, everything uses the computer's
**default network**, as Relay always has.

## A typical layout

A computer with four ports might be cabled like this:

| Port | Network | What Relay sends on it |
|---|---|---|
| A: built-in Ethernet | Crew and venue Wi-Fi | Caption and translation pages, the operator panel and crew pages, caption panels, intercom audio, ProPresenter, ATEM, Tally Arbiter |
| B: USB Ethernet | Dante primary | Discovery and control of Dante devices |
| C: Thunderbolt Ethernet | NDI video | NDI sources and outputs |
| D: Wi-Fi or a second USB adapter | Internet | OpenAI (translation), Planning Center, remote access, crew notifications |

Your layout can be simpler. A common one is two ports: one for everything in the
building, and one for Dante.

::: tip
Give each port its own network, with its own range of addresses: for example
`192.168.1.x` for the crew, `10.10.0.x` for Dante and `10.20.0.x` for NDI. If
two ports are on the same network, the computer cannot tell which one to use,
and the **Network** page warns you.
:::

## Choosing a port for each kind of traffic

1. Open the admin panel, then **Network** (under **Settings**). You need the
   Admin role to change anything.
2. Under **Ports**, check that each port shows the address and link you expect.
   Pull a cable: within a few seconds its **Link** shows it is down.

   ![The Network page: each port with its address, link and the traffic on it.](images/admin-network-ports.png){width=100%}

3. Under **Traffic**, choose a **Network port** for each kind of traffic you
   want on a port of its own. Leave the rest on the default network.
4. In **If the port is missing**, choose what happens when that port is
   unplugged or down:
   - **Do not run it.** The traffic stops until the port is back. Audio and
     video (intercom, Dante, NDI) use this, so they never end up on the crew
     Wi-Fi by accident.
   - **Use the default network.** The traffic carries on over the default
     network, and the module's health says so.
5. Press **Test** on a row. It shows the address the traffic leaves from, without
   sending anything.

   ![The Traffic table: each kind of traffic, its port, and what happens when the port is missing.](images/admin-network-traffic.png){width=100%}

A change applies at once. Relay moves its pages to the new port and each module
reconnects. A port you unplug is remembered, so its setting comes back when you
plug it in again.

::: warning
If you open the admin panel from another computer, Relay refuses a change that
would take the operator panel off the address you are using. Make that change
on the Relay computer itself, at `https://127.0.0.1/admin`, or from a browser on
the new network.
:::

When a chosen port is missing, the module's card on the **Modules** page says
so, for example "Intercom audio (LiveKit media): USB LAN is not connected to
this computer."

## What Relay can steer, and what it cannot

Relay sends each kind of traffic out of the port you choose:

- the caption and translation pages, and the operator panel and crew pages;
- Relay's announcement to caption panels;
- translation (OpenAI), Planning Center, remote access and crew notifications;
- ProPresenter, ATEM, Tally Arbiter, alert webhooks and OSC;
- intercom audio from the phones.

Relay's Dante and NDI modules, as they arrive, use the same settings.

Some things choose their own port, and Relay cannot change them:

- **Dante Virtual Soundcard and Dante Via** pick their network card in their
  own settings. Set them to the same port you chose for Dante in Relay.
- **The remote intercom bridge** (phones off site) sends its audio on whichever
  port the computer prefers.
- **Answers to devices on the default network.** On Windows, the computer can
  send a reply out of another port than the one the question came in on. Keep
  each port on its own network and this does not happen.
- **Programs Relay does not run**, such as ProPresenter itself or your
  livestream software.

On Linux, Relay needs one extra permission to tie traffic to a port. Without it,
traffic still leaves from the right address, which is enough for devices on that
port's own network. The **Network** page says when this applies.

## Dante

Relay works with Dante devices. Dante is a trademark of Audinate. These notes
apply once Relay's Dante module is available.

- A Dante device can have a **primary** and a **secondary** port, for a
  redundant network. These are the device's own ports. Relay joins one Dante
  network: cable Relay's Dante port to the **primary** network.
- Dante devices are found only on the network they share with Relay. If Relay
  finds no Dante devices, check that the Dante port is chosen and up.
- Dante networks often have no DHCP server, so the port gets a self-assigned
  address (`169.254.x.x`). Relay can use it, and the **Network** page notes that
  only devices on that same network can reach it.

## NDI

NDI is a registered trademark of Vizrt NDI AB. NDI keeps network settings of its
own. Once Relay's NDI module is available, choose a port for NDI on the
**Network** page, and check that any other NDI software on this computer uses
the same one. Use a wired, gigabit (or faster) port: NDI video is heavy.

## What to set on your switches

Ask whoever looks after your network to check these:

- **Separate networks.** Use a separate switch, or a separate **VLAN** on a
  managed switch, for each network: crew, Dante, NDI. Keep Dante and NDI off
  the venue Wi-Fi.
- **IGMP snooping, with a querier.** Dante discovery, AES67 audio and NDI use
  **multicast**. Turn on IGMP snooping on each switch that carries them, so
  multicast reaches only the ports that ask for it, and make sure one switch
  (or the router) on each of those networks is the **IGMP querier**. Without a
  querier, snooping switches stop sending multicast after a few minutes.
- **No two ports on one network.** Each of the Relay computer's ports goes to a
  different network, with its own range of addresses.
- **Energy-efficient Ethernet off** on switch ports that carry Dante. Audinate
  recommends this for clock stability.

## If something does not work

| What you see | What to do |
|---|---|
| A module says its port "is not connected to this computer" | Plug the adapter back in, or choose another port on the **Network** page. |
| A module says its port "is down (no cable or no link)" | Check the cable and the switch port. |
| "has no network address" | The port is connected but got no address. Check the DHCP server on that network, or set an address on the port. |
| "are both on 10.0.0.0/24" | Two ports are on the same network. Move one to another network. |
| "cannot pin it to that port: it needs the CAP_NET_RAW capability" | Linux only. Devices on that port's network still work. Ask whoever installed Relay to add the permission. |
| Phones lose the operator panel after a change | You moved the crew pages to another port. Open the new address shown on the **Network** page. |
| Multicast (Dante, NDI) works for a few minutes, then stops | The network has IGMP snooping but no querier. Turn on the querier. |
