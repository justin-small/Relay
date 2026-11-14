# Troubleshooting the installation {#install-troubleshooting}

This chapter lists the problems people meet while installing and starting
Relay, and what to do about each one. Find the message or symptom you see,
then follow the advice next to it.

Two tools help with almost every problem:

- **The log.** Choose **Open logs folder** from the Relay menu and open
  `relay.log`. It shows what Relay is doing and why it stopped. On a service
  install, run `journalctl -u relay`.
- **Restart Relay.** Choose **Restart Relay** from the Relay menu. This clears
  many problems, especially after a change of network or sound card.

## Installing and opening Relay

| Symptom | What to do |
|---|---|
| macOS: **"Relay" Not Opened**, or **Apple could not verify "Relay" is free of malware** | Expected the first time, until Relay is signed. Open **System Settings → Privacy & Security** and click **Open Anyway**. See [Opening Relay the first time](#opening-relay-the-first-time). |
| macOS: **"Relay" is damaged and can't be opened** | The download did not finish, or was changed on the way. Delete it, download it again, and check that you took the right file for your Mac (`aarch64` for Apple silicon, `x64` for Intel). |
| Windows: **Windows protected your PC** | This is SmartScreen. Click **More info**, then **Run anyway**. |
| Windows: the browser blocks the download | Choose **Keep** (in Edge: **…**, **Keep**, then **Keep anyway**). |
| Linux: the AppImage does nothing, or says it needs **FUSE** | Make it executable with `chmod +x`, and install FUSE 2 (`sudo apt install libfuse2t64` on Ubuntu 24.04). |
| No Relay icon in the tray or menu bar | Relay may be hidden: on Windows, click the **^** arrow by the clock; on a Mac with a full menu bar, close some other menu-bar apps. On GNOME, turn on the **AppIndicator** extension. Relay is still running even when you cannot see the icon. |
| The window says **Relay could not start** or the status line says **error** | Open the log (see above) and read the last lines. The tables below cover the usual causes. If you cannot tell, send those lines to whoever supports your Relay installation. |

## Opening the panel and the viewer link

| Symptom | What to do |
|---|---|
| Another computer's browser warns **Your connection is not private** (or similar) | Expected: the certificate was made by Relay itself. Compare the fingerprint with **Show certificate fingerprint** in the Relay menu. If they match, continue. If they do not, stop and find out why. |
| The fingerprint changed since last week | Relay renews its certificate when the computer's address changes (a different network) or when it is close to expiring. The **Viewer links** page says so. Check the new fingerprint and accept it. |
| `http://192.168.1.50/admin` shows **404 Not Found** | The panel only works over HTTPS. Type `https://`, not `http://`. |
| The **Set up Relay** page says to open it on the computer Relay runs on | Until the first account exists, the setup page answers only on that computer. Use the Relay window there. On a service install, use the setup code instead. |
| Phones cannot open the viewer link | Check that the phones are on the same Wi-Fi network as the Relay computer, not a guest network or mobile data: guest networks often stop devices from seeing each other. Check the computer's firewall (Windows: allow Relay on **Private networks**, and make sure the network is **Private**; macOS: **Local Network** permission). Make sure people type `http://`, not `https://`. |
| The viewer link has `:8080` in it | Port 80 was taken by another program, or not allowed (Linux). That is fine: the link, the QR codes and the caption panels all carry the port. To use port 80, see your system's chapter. |
| Nobody can sign in, or you have forgotten the only Owner's password | See [If nobody can sign in](#lockout). |
| The panel says **The user database is not available** | Captions are not affected. Wait a minute and reload. If it stays, see [The user database](#user-database). |

## Sound

| Symptom | What to do |
|---|---|
| The input list is empty | Relay cannot see any sound input. Plug in the microphone or interface, then press **Rescan**. On Linux, check `arecord -l` lists it. |
| The meter stays flat for every input | The system is blocking the microphone. **macOS:** **System Settings → Privacy & Security → Microphone**, turn on **Relay**. **Windows:** see [Microphone privacy settings](#microphone-privacy-settings). Then choose **Restart Relay**. |
| The meter is flat on one input only | Wrong device or wrong channel. Try each channel, or **Mix** on a stereo feed. On Windows the same device can appear more than once: try the next one. |
| The input you used before is gone after a restart | The interface was unplugged, or the system gave it a new name. Plug it in, press **Rescan**, choose it again and press **Start capture**. |
| Another program has the sound card | Some programs (video calls, recording software) take the input for themselves. Close them, then press **Start capture** again. |

## Once Relay is running

These problems appear in the operator panel rather than during installation.
Later chapters explain the panel in detail.

| Symptom | What to do |
|---|---|
| `Incorrect API key provided` | The API key is wrong. Relay stops rather than retrying. Paste the right key in **Settings** and press **Start capture** again. |
| Sessions say `reconnecting`, and the count climbs | A network problem. Relay reconnects by itself, waiting a little longer each time. Check the computer's internet connection. |
| `No input device matched` | The audio interface was unplugged. Plug it in, choose it again and press **Start capture**. |
| Viewers see **No target language is live** | The language is turned off, or capture is stopped. |
| The CLIP indicator keeps lighting | The sound is too loud. Turn the gain down at the mixing desk or interface. |
| The meter moves but stays far to the left | The sound is too quiet. Turn the gain up until the loudest speech reaches the marked band. |
| Caption panels find an old Relay | A Relay 2.0 container is still running (Docker Desktop starts it again when it opens). Quit Docker Desktop, or remove the old install: see [Upgrading from Relay 2.0](#upgrading). |
