# Installing on Windows {#install-windows}

This chapter installs Relay on a Windows PC and takes you to the operator
panel.

## Requirements

- **Windows 10 or Windows 11, 64-bit.** Relay uses Microsoft Edge WebView2 to
  show its window. Windows 11 always has it, and up-to-date Windows 10 does
  too; if it is missing, the installer adds it.
- **About 500 MB of free disk space**, plus room for recordings if you turn
  recording on.
- **A sound input.** A USB microphone or headset works for a test. For a
  service, use an audio interface fed from the mixing desk.
- **A network connection.** The PC must be on the same local network (LAN) as
  the phones and screens that show captions, and it must be able to reach the
  internet, so Relay can talk to OpenAI.
- **An OpenAI API key.** Part 2 explains how to get one. You can add it later.

You do not need to be an administrator. Relay installs for your Windows
account only.

## Installing

1. Open your **Downloads** folder in File Explorer and double-click the file
   you downloaded, for example `Relay_3.0.0_x64-setup.exe`.

2. The installer is not yet signed with a developer certificate, so Windows
   shows **Windows protected your PC** (this is **SmartScreen**). Click
   **More info**, then **Run anyway**.

   Your browser may also warn that the file is not commonly downloaded. Choose
   **Keep** (in Edge: **…**, then **Keep**, then **Keep anyway**).

3. The installer runs by itself and shows a progress bar. When it finishes,
   Relay starts.

   A Relay icon appears in the system tray, at the bottom right of the screen
   next to the clock (click the **^** arrow if you do not see it), and a window
   opens on the **Set up Relay** page.

The installer also adds Relay to the Start menu.

## Allowing Relay through the firewall

The first time Relay starts, **Windows Security** asks whether to allow Relay
on networks.

1. Tick **Private networks, such as my home or work network**.
2. Click **Allow**.

Without this, phones and caption panels cannot reach Relay; only this PC can.

::: warning
If the venue network is marked as **Public** in Windows, the firewall still
blocks phones. Open **Settings → Network & internet**, click the network, and
set **Network profile type** to **Private network**.
:::

## Creating the first account {#windows-first-run}

The **Set up Relay** page creates the first account, the **Owner**. Until it
exists, this page opens only on this PC, so nobody else on the network can
take it.

1. **OpenAI API key.** Paste your key, or leave it empty and add it later in
   **Settings**. The key starts with `sk-`.
2. **Username** and **Your name.** The username is what you sign in with.
3. **Password**, twice. Use at least 8 characters, and choose a real
   password, not a word: this account can spend money on your OpenAI account.
4. Click **Create the Owner**.

   You are signed in, and the operator panel opens.

If you used Relay 2.0 before, use **Coming from Relay 2.0?** on the same page
instead. See [Upgrading from Relay 2.0](#upgrading).

Part 3 explains the panel. To check that everything works, choose your sound
input under **Audio input**: the meter should move when someone speaks.

## The Relay menu {#windows-menu}

Click the Relay icon in the system tray (right-click works too) to see
Relay's menu:

| Item | What it does |
|---|---|
| **Relay: running** | The status line. It says **capturing** while captions run, and **error** if something is wrong. |
| **Open panel** | Opens the operator panel window. |
| **Open panel in browser (for the firmware flasher)** | Opens the panel in your web browser instead. Needed only for flashing caption panels. |
| **Copy viewer link** | Copies the link for the room, ready to paste. |
| **Show certificate fingerprint** | Shows the panel certificate's fingerprint (see below). |
| **Open logs folder** | Opens the folder with Relay's log, for when something goes wrong. |
| **Open state folder** | Opens the folder with your settings. |
| **Restart Relay** | Stops and starts Relay's engine. |
| **Check for updates** | Looks for a new version. See [Updating the desktop app](#updating). |
| **Start at login** | Ticked: Relay starts by itself when you sign in to Windows. |
| **Quit Relay** | Stops Relay. Captions stop too. |

Closing the panel window does not stop Relay. Viewers keep their captions
until you choose **Quit Relay**.

::: tip
On the PC that runs Relay for every service, tick **Start at login**. Then a
restart or a Windows update never leaves the room without captions.
:::

## Opening the panel from another computer {#windows-remote-panel}

The panel window on this PC is all most people need. To run Relay from
another computer or a tablet on the same network, open
`https://<the PC's address>/admin` there, for example
`https://192.168.1.50/admin`. The **Viewer links** page in the panel shows the
PC's address.

That browser warns once, with **Your connection isn't private** or similar.
This is expected: Relay made its own certificate, on this PC. Before you
continue, check the certificate's **fingerprint**: a long code made of pairs
of letters and digits that is different for every certificate.

1. In the browser's warning, click the **Not secure** label in the address
   bar, then the certificate details, and find the **SHA-256 fingerprint**.
2. On the PC running Relay, choose **Show certificate fingerprint** from the
   Relay menu.
3. If the two match, continue to the site (**Advanced**, then **Continue**).
   If they do not, stop and find out why: another computer may be pretending
   to be Relay.

The panel window on the PC itself never warns: it checks the fingerprint for
you.

## Platform notes {#windows-notes}

### Choosing the microphone {#windows-mic}

Relay lists every input Windows knows, under the names Windows gives them,
such as **Microphone (USB Audio CODEC)**. The same device may appear more than
once, through different Windows sound systems. Choose the plain one first; if
the meter does not move, try the next.

### Microphone privacy settings

If the meter stays flat for every input, Windows is blocking the microphone.
Open **Settings → Privacy & security → Microphone** (Windows 10: **Settings →
Privacy → Microphone**) and turn on:

- **Microphone access** (Windows 10: **Allow access to the microphone on this
  device**);
- **Let apps access your microphone**;
- **Let desktop apps access your microphone**.

Then choose **Restart Relay** from the Relay menu.

### Ports 80 and 443

Relay serves viewers on port 80 and the panel on port 443. If another program
on the PC already uses one of them (a web server such as IIS, or another copy
of Relay), Relay uses **8080** and **8443** instead. The **Viewer links** page
says so, and the viewer link then carries the port, for example
`http://192.168.1.50:8080/`.

## Rehearsal mode

**Rehearsal mode** shows made-up captions on the viewer pages. Use it to check
fonts, the projector and the link on every phone in the room. It does not
connect to OpenAI, so it costs nothing.

1. Choose **Quit Relay** from the Relay menu.
2. Open **Command Prompt** (search for `cmd` in the Start menu).
3. Type these two lines, pressing `Enter` after each:

   ```bat
   set RELAY_DEMO=1
   "%LOCALAPPDATA%\Relay\relay-desktop.exe"
   ```

   Relay opens as usual. Leave the Command Prompt window open while you
   rehearse.

4. Open the viewer link. You should see sample captions appear.

To leave rehearsal mode, choose **Quit Relay**, then open Relay again from the
Start menu as usual. A normal start never uses rehearsal mode.

## Removing Relay

1. Choose **Quit Relay** from the Relay menu.
2. Open **Settings → Apps → Installed apps** (Windows 10: **Apps & features**),
   find **Relay**, and choose **Uninstall**.
3. To remove your settings, accounts and recordings as well, delete the state
   folder: type `%APPDATA%\Relay` in File Explorer's address bar, then delete
   the folder. Keep it if you might install Relay again.
