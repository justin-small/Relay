# Dante: taking Relay's sound off the network {#dante}

If your sound desk or stage boxes use **Dante**, Relay can take its sound
straight off the network, with no cable to the computer. Relay does not need
any special code for this: it uses an ordinary **sound card that Dante or AES67
adds to the computer**, and picks it like any microphone.

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
| Windows | **openaes67**, a free AES67 sound card (MIT licence): <https://github.com/ylzwd/openaes67> | Not tested |
| Mac | **AES67 macOS Driver**: <https://github.com/soundsofthesir/AES67_macos_Driver> | Not tested |
| Linux | **Inferno**, an unofficial Dante-compatible driver | Not tested |

The Mac driver is licensed GPL-3.0, so Relay only **links** to it and never
includes it: download it from its own page. Inferno was made by studying how
Dante talks, not by Audinate; check its licence yourself before you rely on it,
and keep Dante Controller for anything important.

## Set up

1. Install one of the routes above and restart the computer if it asks.
2. In **Dante Controller** (or the AES67 driver's own settings), **subscribe**
   the desk's output channels to the sound card's receive channels.
3. In Relay's admin panel, open **Audio** and click **Rescan**.
4. Pick the sound card as the **Capture device**, then pick the **channel**
   (or **Mix**).

Relay marks the inputs it recognises with **[Dante]** or **[AES67]** after the
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
