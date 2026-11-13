# Setting up a caption panel {#panels}

This chapter takes you from a panel in its box to captions on the screen. Set
aside about 30 minutes for the first panel. The second one takes five.

## What you need

- **A supported panel.** The only one is the **Elecrow CrowPanel Advance
  7.0 inch**, model DIS02170A, hardware version 1.2 or later. It has an
  800 by 480 touch screen. Other screens and other sizes do not work.
- **Relay, installed and started**, on the same network (**LAN**) as the
  panel. See Part 1.
- **The Wi-Fi name and password** of that network. The panel joins Wi-Fi by
  itself. It does not need a cable.
- **A computer with a USB cable and Python**, for the first flash only.
- **An operator account** with the Admin role. See Part 1.

::: note
A panel needs a Wi-Fi network that is **not** a guest network. Guest networks
often block devices from finding each other, and the panel cannot find Relay.
If you have to use one, type Relay's address into the panel's setup page, as
described below.
:::

## What a panel shows

A panel shows **finished lines** only. A line appears when the speaker
finishes it, not word by word. The newest line is at the bottom. A panel
keeps the last 200 lines.

- It shows the **transcription** (what was said), or **one translation**, or
  two of these side by side.
- If someone drags the captions back to read an earlier line, the panel stops
  following. A **Jump to live** button appears. Tap it to catch up. If nobody
  touches the screen for a minute, it jumps to live by itself.
- The text size is fixed. It is made to be read from a few metres away.

### Which languages a panel can show

A panel can show **languages written in the Latin alphabet**: English,
Spanish, Portuguese, French, German, Italian, Indonesian, Vietnamese and
similar.

It cannot show Russian, Chinese, Japanese, Korean, Hindi, Arabic, Nepali,
Telugu or other languages with their own letters. The panel's text is drawn
with a single small font that holds only Latin letters. A language it cannot
draw would appear as empty boxes. So the panel **hides** those languages from
its menu, and the operator panel refuses to set one on it. They still work on
phones and on the viewer pages.

A transcription is hidden too if the language being spoken is not written in
Latin letters.

## Step 1: Flash the panel

**First, install the USB driver if your computer does not see the panel.** The
panel talks to your computer through a CH340 chip. Linux and recent versions
of macOS often include a driver for it. If the panel does not appear in the
port list, install one:

- **macOS:** [download the CH340 driver for Mac](https://sparks.gogo.co.nz/assets/_site_/downloads/CH341SER_MAC_V1_8.zip)
- **Windows:** [download the CH34x driver for Windows](https://sparks.gogo.co.nz/assets/_site_/downloads/CH34x_Install_Windows_v4_0.zip)

Unzip the download, run the installer, then unplug the panel and plug it in
again. On a Mac you may need to allow the driver in **System Settings >
Privacy & Security**, and restart.

You do this once for each new panel. Relay's releases include a file called
`relay-display.bin`. Download it from the same release page as Relay itself.

**From the operator panel (Chrome or Edge).** Open **Panels**, then **Flash a
panel** (`/admin/flash`).

1. Upload `relay-display.bin` there. Relay keeps it for the next panel too.
2. Plug the panel into the computer with a USB cable that carries data.
3. Click **Choose the panel's port and flash**, and pick the panel's port in
   the browser's list.

The page checks the file and the board before it writes anything, asks before
writing a board that runs something else, and shows the progress (about two
minutes). When it says the firmware is written, press **RESET** on the panel to
start it. It never erases the panel's Wi-Fi or settings.

**From a terminal, in any browser or none.**

1. Plug the panel into your computer with a USB cable.
2. Install the flashing tool: `python3 -m pip install esptool`.
3. Run this, in the folder that holds the file:

   ```
   python3 -m esptool --chip esp32s3 -b 460800 write_flash \
     --flash_mode dio --flash_size keep --flash_freq 80m 0x0 relay-display.bin
   ```

4. The tool shows a progress bar and ends with `Hard resetting`.

   If it says it cannot connect, hold the **BOOT** button on the back of the
   panel while you plug the cable in, then try again.

You should see the panel light up and show the setup screen from the next
step.

::: tip
The file does not hold your Wi-Fi password or any address. The same file goes
on every panel. You give each panel its Wi-Fi on its own screen.
:::

## Step 2: Join the panel to Wi-Fi

A new panel has no Wi-Fi yet, so it makes its own small network for setup.

1. Look at the panel's screen. It shows a network name that starts with
   `Relay-Panel-`, a password, and a **QR code**.
2. On your phone, scan the QR code. Your phone joins the panel's network.
   You can type the name and password instead.
3. Open `http://192.168.4.1/` in the phone's browser. Your phone may open it
   by itself.
4. Choose your venue's Wi-Fi name and type its password.
5. Leave **Relay address** empty. The panel finds Relay by itself. Fill it in
   only if the panel cannot find Relay (see the troubleshooting table below).
6. Save.

The panel tries the network. When it works, the panel shows the address it
got, and a minute later it closes its setup network. Within a few seconds it
shows captions, or a message saying **Relay not running** if capture is not
started.

## Step 3: Name the panel and choose its feed

The panel appears in Relay by itself. You do not add it.

1. Open the operator panel and sign in. See [Opening the operator
   panel](#operator-access).
2. Open **Panels**. Each panel is a row. A new one is called **Panel**
   followed by a few letters, and says **Online**.

Here is the page with three panels:

![The Panels page with three panels online.](images/admin-panels.png){width=100%}

1. **Set up a new panel over USB.** Opens the flasher from Step 1.
2. **Name.** Click it to open the panel's settings.
3. **Showing.** What the panel shows now.
4. **Firmware.** The panel's software version.
5. **Status.** **Online**, or **Offline** if Relay has not heard from the
   panel for 90 seconds.
6. **Panel firmware.** Where you upload updates (Step 4).

Click a panel's name. Its settings open at the side.

![A panel's settings.](images/admin-panel-settings.png){width=60%}

1. **Name.** Type where the panel is, for example `Lobby`.
2. **Feed.** Choose **Transcription**, a language, the **Planning Center**
   service, **Alert buttons** (see [Alerts](#alerts)), or ProPresenter's
   **Lyrics** or **Timers** (see [ProPresenter](#propresenter-panels)), or
   **Status** (see [The status view](#status-view)). Choose **Two
   (split)** under **Number of feeds** to show two side by side.
3. **Screen rotation.** **0°**, **90°**, **180°** or **270°**, for a panel
   mounted upright or upside down. A change restarts the panel, which takes
   about half a minute.
4. **Device.** The panel's board, software, address and Wi-Fi strength.
   **Reboot** restarts it. **Update** and **Update now** appear here when a
   newer version is available.
5. **Save**, **Cancel**, or **Forget** to remove the panel from the list.

The panel changes at once. **Showing** tells you what the panel shows now.

::: note
Anyone can also change what a panel shows from the panel's own screen: tap the
menu button at the top right. It can turn the picture too. **The newest change
wins.** If you set a feed in the operator panel and a person then changes it at
the screen, their choice stays until you set another. The panel's settings say
**Feed changed at the screen** or **from admin** so you can tell.
:::

If a language you chose stops being live, the panel shows the transcription
with an amber strip saying so, and returns to your language by itself when it
comes back.

## Step 4: Update a panel

New panel software comes with new Relay releases. A release includes
`relay-display-app.bin`. It is signed, which means it carries a mark that only
the Relay team can make.

1. Download `relay-display-app.bin` from the release.
2. In **Panels**, under **Panel firmware**, choose the file and
   click **Upload**.
3. Each panel with older software shows **Update available** and the version.
4. Open a panel's settings and click **Update**. It installs at a quiet moment, when no new
   caption line has come in for two minutes. Or click **Update now** to
   restart it at once. Captions stop for about half a minute. **Update all
   online panels** does every panel that is on.
5. The panel's settings show each step: **Downloading**, **Downloaded and verified**,
   **Restarting into the new version**, and then **Updated**.

You should see the **Firmware** line in its settings show the new version.

::: warning
A panel only installs an update that is signed with the Relay release key. It
checks this itself, so nobody on your network can push other software to it.
An update that does not work is undone by the panel: if the new version cannot
reach Relay within ten minutes, the panel goes back to the old one and the
panel's settings say **Rolled back**.
:::

## The status view {#status-view}

The **Status** feed is for the sound desk and the booth. It shows, at a
glance:

- **Livestream**: a red **ON AIR** band with how long it has been live, or
  **OFF AIR**. It says **NOT WATCHED** until a livestream module (such as OBS)
  is switched on, on the **Modules** page.
- **Captions**: running or stopped, and for how long.
- **Input**: OK, silent, clipping or lost, with the level in dB.
- **Languages**: the translations that are live.
- **Watching**: how many people have Relay's caption pages open.
- One row for each livestream source, with its health and, where the
  service gives one, its viewer count.

If the panel stops hearing from Relay, the values turn grey.

The same view is a web page: open `http://<Relay's address>/status` on any
screen in the building. It needs no sign-in, so it shows no addresses, keys
or error messages. The reasons stay on the module's admin page.

## Recovering a panel

Use the **BOOT** button on the back of the panel.

| What to do | What happens |
|---|---|
| Hold **BOOT** for **5 seconds** | The panel opens its setup network again, as in Step 2. Use this when the Wi-Fi name or password changed. Its settings are kept until a new network works. |
| Hold **BOOT** for **15 seconds** | **Factory reset.** Everything is erased, including the panel's identity. It restarts blank and Relay sees a new, unnamed panel. |

After a factory reset, remove the old entry with **Forget** in the panel's
settings.

## Troubleshooting

| What you see | What to do |
|---|---|
| The panel says **Relay not running** | Relay is not started, or capture is stopped, or the panel cannot find it. Check the Relay computer first. |
| The panel is on Wi-Fi but never appears in **Panels** | It cannot find Relay. Guest networks and some routers block this. Hold **BOOT** for 5 seconds, then type Relay's address in **Relay address**, for example `http://192.168.1.50`. Use `http://`, not `https://`. |
| The panel says **Offline** | Relay has not heard from the panel for 90 seconds. Check its power and Wi-Fi. |
| A language you want is not in the panel's menu | It is not written in Latin letters. See the list above. |
| Two Relays on one network | A panel stays with the first one it found. To move it, type the other Relay's address on its setup page, or factory-reset it. |
| **Update failed** | The panel's settings say why. Check that the file is `relay-display-app.bin` from a Relay release, then try again. |
| **Forget** brings the panel straight back | Expected, if the panel is still on. It returns unnamed. |

## Where panels use the network

You only need this if your network has firewall rules. Panels never sign in
to the operator panel. They use Relay's **viewer port** (80, plain HTTP). The
operator panel stays on port 443. Relay announces itself on the network with
mDNS (UDP port 5353) so panels can find it.
