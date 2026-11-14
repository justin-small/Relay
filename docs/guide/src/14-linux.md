# Installing on Linux {#install-linux}

There are two ways to run Relay on Linux:

- **On a desktop** (a laptop or PC with a screen): the Relay app, exactly as
  on macOS and Windows. See [On a Linux desktop](#linux-desktop).
- **On a computer with no screen** (a small server or a Raspberry Pi in a
  rack): Relay as a **service**, which starts with the computer and has no
  window. You run it from another device's browser. See
  [On a computer with no screen](#linux-service).

## On a Linux desktop {#linux-desktop}

### Requirements

- **A 64-bit (x86-64) Linux desktop** from 2024 or later, such as Ubuntu
  24.04, Debian 13 or Fedora 40. For a Raspberry Pi, use the service install
  below.
- **About 500 MB of free disk space**, plus room for recordings if you turn
  recording on.
- **A sound input** that Linux can see, and a sound server (PipeWire or
  PulseAudio; every desktop has one).
- **A network connection.** The computer must be on the same local network
  (LAN) as the phones and screens that show captions, and it must be able to
  reach the internet, so Relay can talk to OpenAI.
- **An OpenAI API key.** Part 2 explains how to get one. You can add it later.

### Installing

**The AppImage** works on most distributions. It is one file that you run
directly:

1. Open a terminal, the window where you type commands.
2. Make the file executable, then run it:

   ```bash
   chmod +x ~/Downloads/Relay_3.0.0_amd64.AppImage
   ~/Downloads/Relay_3.0.0_amd64.AppImage
   ```

   Keep the file somewhere permanent, for example a folder called
   `Applications` in your home folder: Relay runs from it, and updates replace
   it in place.

If it fails with a message about **FUSE**, install the FUSE 2 library
(`sudo apt install libfuse2t64` on Ubuntu 24.04, `libfuse2` on Debian) and
try again.

**The `.deb` package** suits Ubuntu and Debian, and adds Relay to the
applications menu:

```bash
sudo apt install ~/Downloads/Relay_3.0.0_amd64.deb
```

Then open **Relay** from the applications menu.

Either way, a Relay icon appears in the system tray (where your desktop shows
it; on GNOME this needs the **AppIndicator** extension, which Ubuntu has
already), and a window opens on the **Set up Relay** page.

### Creating the first account

The **Set up Relay** page creates the first account, the **Owner**. Until it
exists, this page opens only on this computer, so nobody else on the network
can take it.

1. **OpenAI API key.** Paste your key, or leave it empty and add it later in
   **Settings**. The key starts with `sk-`.
2. **Username** and **Your name.** The username is what you sign in with.
3. **Password**, twice. Use at least 8 characters, and choose a real
   password, not a word: this account can spend money on your OpenAI account.
4. Click **Create the Owner**.

   You are signed in, and the operator panel opens.

If you used Relay 2.0 before, use **Coming from Relay 2.0?** on the same page
instead. See [Upgrading from Relay 2.0](#upgrading).

The Relay menu in the tray has the same items as on Windows: see
[The Relay menu](#windows-menu). To open the panel from another computer, and
check the certificate fingerprint there, see
[Opening the panel from another computer](#windows-remote-panel).

### Ports 8080 and 8443

On Linux an ordinary program may not use ports below 1024, so the Relay app
serves viewers on port **8080** and the panel on **8443**. The **Viewer
links** page shows the right link, for example `http://192.168.1.50:8080/`,
and the QR codes carry it. Caption panels find the port by themselves.

To use the usual ports 80 and 443 (so the link needs no port), let unprivileged
programs use low ports, then restart Relay:

```bash
echo 'net.ipv4.ip_unprivileged_port_start=80' | sudo tee /etc/sysctl.d/50-relay.conf
sudo sysctl --system
```

### Firewall

If phones cannot open the viewer link, allow Relay's ports in the firewall.
With `ufw` (Ubuntu):

```bash
sudo ufw allow 8080/tcp
sudo ufw allow 8443/tcp
```

Use `80/tcp` and `443/tcp` instead if you changed the ports as above.

### Rehearsal mode

**Rehearsal mode** shows made-up captions on the viewer pages, without
connecting to OpenAI. Choose **Quit Relay** from the Relay menu, then start
Relay from a terminal with `RELAY_DEMO=1` in front:

```bash
RELAY_DEMO=1 ~/Applications/Relay_3.0.0_amd64.AppImage   # the AppImage
RELAY_DEMO=1 relay-desktop                               # the .deb
```

Open the viewer link: sample captions appear. To leave rehearsal mode, quit
Relay and open it again as usual.

## On a computer with no screen {#linux-service}

The service install runs Relay under its own user account, starts it with the
computer, and keeps it apart from the rest of the system. It has no window and
no tray: you use the operator panel from another device's browser.

### Requirements

- **Debian 12 or 13, Ubuntu 24.04 or later, or Raspberry Pi OS (64-bit).**
- **Python 3.13 or later.** Debian 13 and Ubuntu 24.04 have it. On Debian 12,
  install one first (for example with `uv python install 3.13`) and add
  `--python /path/to/python3.13` to the install command.
- **A sound input** that Linux can see. List them with `arecord -l` (from the
  `alsa-utils` package).
- A network connection, and an OpenAI API key, as above.

### Installing the service

1. Download `Relay-v3.0.0.zip` from the release page (see
   [Downloading Relay](#download)) and unzip it, or clone the project with
   git:

   ```bash
   git clone https://github.com/justin-small/Relay.git
   cd Relay
   ```

2. Run the installer:

   ```bash
   sudo tools/install-service.sh
   ```

   It creates a `relay` user, installs Relay in `/opt/relay`, keeps Relay's
   state folder in `/var/lib/relay`, and starts the service. At the end it
   prints the panel address, the certificate fingerprint and a one-time
   **setup code**.

3. On another computer on the same network, open the panel address it
   printed, for example `https://192.168.1.50/admin`. The browser warns once
   about the certificate: check that the fingerprint matches the one the
   installer printed, then continue.

4. On the **Set up Relay** page, type the setup code, then create the Owner as
   above. The code works once, and stops working when the Owner exists.

Lost the setup code? Print it again with
`sudo tools/install-service.sh setup-code`.

### Looking after the service

| Command | What it does |
|---|---|
| `sudo systemctl status relay` | Shows whether Relay is running. |
| `journalctl -u relay -f` | Shows Relay's log as it happens. Press `Ctrl+C` to stop watching. |
| `sudo systemctl restart relay` | Restarts Relay. |
| `sudo tools/install-service.sh update` | Installs a newer Relay. Run `git pull` (or unzip the new release) first. |
| `sudo tools/install-service.sh reset-owner <username>` | Gives that account a new password and makes it an Owner. See [If nobody can sign in](#lockout). |
| `sudo tools/install-service.sh uninstall` | Removes Relay but **keeps** `/var/lib/relay`, with your settings and accounts. |
| `sudo tools/install-service.sh uninstall --purge` | Removes Relay and all its data. |

The service is allowed to use ports 80 and 443 directly, so viewer links need
no port. Allow both in the firewall if phones cannot connect.

The service install does not update itself. Watch the release page and run
the `update` command when a new version is out.
