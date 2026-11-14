# Installing on macOS {#install-macos}

This chapter installs Relay on a Mac and takes you to the operator panel.

## Requirements

- **A Mac with macOS 11 (Big Sur) or later.** Both Apple silicon (M1 and
  later) and Intel Macs work. Download the right file for yours: see
  [Downloading Relay](#download).
- **About 500 MB of free disk space**, plus room for recordings if you turn
  recording on.
- **A sound input.** The Mac's own microphone works for a test. For a service,
  use an audio interface fed from the mixing desk.
- **A network connection.** The Mac must be on the same local network (LAN)
  as the phones and screens that show captions, and it must be able to reach
  the internet, so Relay can talk to OpenAI.
- **An OpenAI API key.** Part 2 explains how to get one. You can add it later.

## Installing

1. Open your **Downloads** folder in Finder and double-click the file you
   downloaded, for example `Relay_3.0.0_aarch64.dmg`.

   A window opens with the Relay icon and an **Applications** folder.

2. Drag the **Relay** icon onto the **Applications** folder.

3. Close the window, then eject the disk image: in the Finder sidebar, click
   the eject button next to **Relay**.

## Opening Relay the first time

The installer is not yet signed with an Apple developer certificate, so macOS
refuses to open it the first time. You allow it once:

1. Open **Applications** and double-click **Relay**.

   macOS says **"Relay" Not Opened** or **Apple could not verify "Relay" is
   free of malware**. Click **Done** (or **OK**). Do not click **Move to
   Trash**.

2. Open the Apple menu and choose **System Settings**, then **Privacy &
   Security**.

3. Scroll down to the **Security** section. You should see **"Relay" was
   blocked to protect your Mac**. Click **Open Anyway**, then confirm with your
   Mac password or Touch ID.

4. Click **Open Anyway** once more in the box that appears.

   Relay starts. A Relay icon appears in the menu bar at the top right of the
   screen, and a window opens on the **Set up Relay** page.

You do this only once. After that, Relay opens like any other app.

macOS then asks two questions. Answer both with **Allow**:

- **"Relay" would like to find and connect to devices on your local
  network.** Without it, phones and caption panels cannot reach Relay.
- **"Relay" would like to access the microphone.** This appears the first time
  you choose a sound input. Without it, Relay records only silence.

## Creating the first account {#macos-first-run}

The **Set up Relay** page creates the first account, the **Owner**. Until it
exists, this page opens only on this Mac, so nobody else on the network can
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

## The Relay menu {#macos-menu}

Click the Relay icon in the menu bar to see Relay's menu:

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
| **Start at login** | Ticked: Relay starts by itself when you log in to the Mac. |
| **Quit Relay** | Stops Relay. Captions stop too. |

Closing the panel window does not stop Relay. Viewers keep their captions
until you choose **Quit Relay**.

::: tip
On the computer that runs Relay for every service, tick **Start at login**.
Then a restart of the Mac never leaves the room without captions.
:::

## Opening the panel from another computer {#macos-remote-panel}

The panel window on this Mac is all most people need. To run Relay from
another computer or a tablet on the same network, open
`https://<the Mac's address>/admin` there, for example
`https://192.168.1.50/admin`. The **Viewer links** page in the panel shows the
Mac's address.

That browser warns once, with **Your connection is not private** or similar.
This is expected: Relay made its own certificate, on this Mac. Before you
continue, check the certificate's **fingerprint**: a long code made of pairs
of letters and digits that is different for every certificate.

1. In the browser's warning, open the certificate details (usually by clicking
   **Advanced** or **Show Details**, then the certificate or **View
   certificate**). Find the **SHA-256 fingerprint**.
2. On the Mac, choose **Show certificate fingerprint** from the Relay menu.
3. If the two match, continue to the site. If they do not, stop and find out
   why: another computer may be pretending to be Relay.

The panel window on the Mac itself never warns: it checks the fingerprint for
you.

## Platform notes {#macos-notes}

### The Mac's IP address

Phones reach Relay at the Mac's address on the network, such as
`http://192.168.1.50/`. Relay finds it by itself and follows a change of
network. If the Mac moves to a different network (another Wi-Fi), the viewer
link changes, and the panel certificate is renewed: browsers on other
computers warn once more.

To keep the same address, ask whoever runs the network to give the Mac a
**reserved** (fixed) address.

### Microphone permission

If you clicked **Don't Allow** when macOS asked about the microphone, Relay
records silence and the meter stays flat. Open **System Settings → Privacy &
Security → Microphone**, turn on **Relay**, then choose **Restart Relay** from
the Relay menu.

### Local network permission

If phones cannot open the viewer link but the panel works on the Mac, open
**System Settings → Privacy & Security → Local Network** and turn on
**Relay**.

### The firewall

If the Mac's firewall is on (**System Settings → Network → Firewall**) and
asks whether Relay may accept incoming connections, click **Allow**.

## Rehearsal mode

**Rehearsal mode** shows made-up captions on the viewer pages. Use it to check
fonts, the projector and the link on every phone in the room. It does not
connect to OpenAI, so it costs nothing.

1. Choose **Quit Relay** from the Relay menu.
2. Open **Terminal** (in **Applications → Utilities**).
3. Type this line and press `Return`:

   ```bash
   RELAY_DEMO=1 /Applications/Relay.app/Contents/MacOS/relay-desktop
   ```

   Relay opens as usual. Leave the Terminal window open while you rehearse.

4. Open the viewer link. You should see sample captions appear.

To leave rehearsal mode, choose **Quit Relay**, then open Relay again from
**Applications** as usual. A normal start never uses rehearsal mode.

## Removing Relay

1. Choose **Quit Relay** from the Relay menu.
2. Drag **Relay** from **Applications** to the Trash.
3. To remove your settings, accounts and recordings as well, delete the state
   folder, `~/Library/Application Support/Relay`. Keep it if you might install
   Relay again.
