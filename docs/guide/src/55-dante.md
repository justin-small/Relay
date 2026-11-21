# Dante: taking Relay's sound off the network {#dante-input}

If your sound desk or stage boxes use **Dante**, Relay can take its sound
straight off the network, with no cable to the computer. Relay does not need
any special code for this: it uses an ordinary **sound card that Dante
adds to the computer**, and picks it like any microphone or speaker.

This is optional. Everything here works with Dante devices; nothing here is
certified by Audinate.

::: warning
Each route below is marked **tested** or **not tested**. At the time of writing
none has been followed on real equipment by the Relay team, so treat every
route as **not tested** until this page says otherwise.
:::

## Choose a route

| Your computer | Route | Status |
|---|---|---|
| Any | **Dante Virtual Soundcard** (paid, from Audinate) | Not tested |
| Any | **Dante Via** (free, from Audinate) | Not tested |
| Any | A **Dante AVIO USB** adapter, plugged in like any sound card | Not tested |

Relay does not send or receive AES67 itself. If your gear is in AES67 mode, use
Dante Virtual Soundcard (or Dante Via) on the Relay computer to bring it in.
Dante Controller stays the place for anything important.

## Set up

1. Install one of the routes above and restart the computer if it asks.
2. In **Dante Controller**, **subscribe**
   the desk's output channels to the sound card's receive channels.
3. In Relay's admin panel, open **Audio** and click **Rescan**.
4. Pick the sound card as the **Capture device**, then pick the **channel**
   (or **Mix**).

Relay marks the inputs it recognises with **[Dante]** after the
name. It recognises them by name, so a sound card renamed by hand may show no
mark. The mark changes nothing about how it works.

## Which channel feeds Relay?

With the **Dante** module switched on, and the sound card being Dante Virtual
Soundcard or Dante Via on **this** computer, the Audio page shows **Fed by**
under the capture device: the Dante channel (for example *Lead Vox@STAGE*)
subscribed to the channel you picked.

This line comes from asking the device for its subscriptions, a feature that has
**not been tried on real Dante equipment** yet. If the device answers in a way
Relay cannot read, the line stays empty. An empty line does not mean the route
is broken.

## If you hear nothing

- Check the subscription in Dante Controller first: a subscription that does not
  resolve gives silence.
- Check the sound card is the one picked in Relay, and the channel is the one you
  subscribed.
- Check Relay's **Dante** page for problems and the clock: a clock that is not
  locked can cut the sound out.

## Sending Relay's sound onto Dante {#dante-output}

Alert tones and spoken translation channels go out the same way: as an ordinary
sound device.

1. Install **Dante Virtual Soundcard** (or Dante Via, or plug in a Dante AVIO
   USB adapter) and restart the computer if it asks.
2. In Relay's admin panel, open **Audio** and click **Rescan**.
3. On the **Audio bus** page, add an **output**, choose the Dante sound card as
   its device, and patch the alert tones and each spoken language to the
   channels you want them on.
4. In **Dante Controller**, **subscribe** the receivers you want (a desk input,
   a speaker, a stage box) to the sound card's transmit channels.

If you hear nothing, check the subscription in Dante Controller first, then
that the sound card is the one chosen in Relay.

## The Dante page: what Relay finds on the network

With the **Dante** module on, its admin page lists every Dante device Relay can
see: stage boxes, Dante Virtual Soundcard, Dante Via and bridges. Each is listed
once, by its Dante name, even when a computer announces itself on two network
ports. For each device Relay shows the address, model, sample rate, how many
channels it sends and receives, the transmit channels it names, and what each
receive channel listens to.

Relay checks a device two ways. It asks the device's control port (4440, or
24440 for Dante Via), and it listens for the device's own multicast heartbeat.
A device that sends heartbeats but does not answer on its control port is shown
as *alive, control port silent*.

::: warning
The heartbeat does not cross a router. If Relay is on the main network and the
Dante gear is on a Dante VLAN, only the control port tells whether a device is
there, so the router must let UDP through to it. On the church network this was
tried on 2026-11-20: the control port answered across the router from stage
boxes, a Mac running Dante Virtual Soundcard and Dante Via, while the heartbeat
was seen only on the Dante network itself.
:::
