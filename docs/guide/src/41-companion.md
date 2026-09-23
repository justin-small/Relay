# Controlling Relay from Companion {#companion}

::: note
Written in September 2026 and checked against Companion 5.0.6. The newest
Companion at that time was 5.0; many venues still run 4.x or 3.x. The
differences are pointed out where they matter. Menu names are given as they
appeared then.
:::

This chapter takes you from nothing to working Stream Deck buttons for Relay.
You build the Relay module, load it into Companion, connect it to Relay and
drag ready-made buttons onto a page. Set aside about 45 minutes the first
time.

A **module** is a plug-in that teaches Companion how to talk to one kind of
device or program. Companion includes hundreds of modules, but the Relay
module is not one of them yet. You add it yourself, once.

## What you need

- **A computer that runs Companion.** It can be the Relay computer or a
  different one. It must be on the same local network (**LAN**) as the Relay
  computer. The LAN is the network inside the venue: the same Wi-Fi or the
  same network switch.
- **Companion 3.x, 4.x or 5.x.** Download it free from
  <https://bitfocus.io/companion>. On macOS, Companion 5 needs macOS 13.5 or
  later.
- **Relay, installed and started**, with its **admin token** (the operator
  panel password you chose during setup). See Part 1.
- **The Relay computer's LAN IP address.** Start prints it on the `Panel`
  line, for example `https://192.168.1.50/admin`. The IP address is the part
  between `https://` and `/admin`: here, `192.168.1.50`.
- **The certificate fingerprint** (optional, but recommended). See
  [The certificate fingerprint](#companion-fingerprint) below.
- **Node.js**, to build the module. Node.js is a free program that runs
  JavaScript code outside a web browser. It includes **npm**, a tool that
  downloads the pieces a project needs and runs its build steps. You need
  Node.js only on the computer where you build the module. That can be any
  computer, not necessarily the Companion computer.

## Step 1: Get the module from GitHub

The module has its own page on GitHub:

<https://github.com/justin-small/companion-module-relay-translation>

There are no ready-made releases yet, so you download the source code and
build it yourself. Download it as a ZIP file (green **Code** button, then
**Download ZIP**) and unzip it, or clone it with git. For the details, see
[Downloading from GitHub](#download).

Put the folder somewhere simple, for example:

| Computer | Suggested folder |
|---|---|
| macOS | `/Users/yourname/companion-modules/companion-module-relay-translation` |
| Windows | `C:\companion-modules\companion-module-relay-translation` |
| Linux | `/home/yourname/companion-modules/companion-module-relay-translation` |

::: tip
Keep the module in a folder of its own, such as `companion-modules`. If you
choose the developer modules route in Step 3, Companion needs a folder that
holds module folders, and this one is ready for that.
:::

If you downloaded the ZIP file, the folder is called
`companion-module-relay-translation-main`. You can rename it, or leave it.

## Step 2: Install Node.js and build the module

### Install Node.js

The module needs Node.js version 22 (version 18.18 or later also works).

1. Open <https://nodejs.org> in a web browser.
2. Download the installer for **Node.js 22 LTS** for your system. **LTS**
   means long-term support: the stable version most people should use. If
   the front page offers a newer version, look for version 22 under
   **Download** or **Other downloads**.
3. Run the installer and accept the defaults.
4. Open a terminal. On macOS, open **Terminal**. On Windows, open
   **Command Prompt**. On Linux, open your terminal program.
5. Type this and press `Return`:

   ```bash
   node --version
   ```

   You should see a version number that starts with `v22`, for example
   `v22.20.0`.

::: note
A newer Node.js, such as version 24, usually builds the module too, but npm
prints a warning that the version is not supported. If the build fails, go
back to version 22.
:::

On Linux, your distribution's own Node.js package may be too old. Check with
`node --version`. If it is older than 18.18, install version 22 from
<https://nodejs.org> instead.

### Build the module

1. In the terminal, go to the module folder. For example, on macOS:

   ```bash
   cd ~/companion-modules/companion-module-relay-translation
   ```

   On Windows:

   ```bat
   cd C:\companion-modules\companion-module-relay-translation
   ```

2. Download the pieces the module needs:

   ```bash
   npm install
   ```

   This takes a minute or two and needs the internet. It ends with a line
   such as `added 250 packages`. Warnings are normal. A line that starts
   with `npm error` is not; see [Troubleshooting](#companion-troubleshooting).
3. Build the module package:

   ```bash
   npm run package
   ```

   This builds the module and packs it into one file. It ends with:

   ```
   Writing compressed package output to relay-translation-1.0.0.tgz
   ```

   The file `relay-translation-1.0.0.tgz` is now in the module folder. The
   number is the module's version and may be higher for you.

A file ending in `.tgz` is a **module package**: the whole module packed into
one compressed file, like a ZIP file. You can copy it to a USB stick and carry
it to the Companion computer. That computer does not need Node.js.

## Step 3: Load the module into Companion

There are two ways. Use the first one if you can.

| | Import a module package | Developer modules folder |
|---|---|---|
| Companion version | 4.0 or later | 3.x, 4.x and 5.x |
| Needs Node.js on the Companion computer | No | Yes, unless you build elsewhere and copy the whole folder |
| To update the module | Import the new `.tgz` | Rebuild in place |

### Route A: Import the module package (Companion 4 and later)

1. Open Companion's web page. On the Companion computer, click **Launch GUI**
   in the Companion window. From another computer, open the address the
   Companion window shows, for example `http://192.168.1.60:8000`.
2. Import the package:

   ![The Modules page, before the Relay module is imported.](images/companion-modules-import.png){width=100%}

   1. Click **Modules** in the menu on the left.
   2. Click **Import module package**, then choose the file
      `relay-translation-1.0.0.tgz` from Step 2.

   The file picker closes and nothing else seems to happen. That is normal.

3. Check that it worked:

   ![The Relay module installed, version 1.0.0.](images/companion-module-installed.png){width=100%}

   1. **Relay: Relay** now appears in the list of modules. Click it.
   2. The panel on the right is titled **Manage Relay: Relay (Connection)**.
   3. The version you imported, **1.0.0**, is listed under **Version**.

::: warning
**Companion 5 only accepts a package imported from the Companion computer
itself.** If you open Companion's web page from another computer and import
there, Companion refuses with a message that importing custom modules from a
remote computer is disabled. Either import on the Companion computer, or turn
on **Restricted modules** in the Companion window: click the cog, open
**Dangerous Features**, and switch it on. Companion restarts when you change
it. Companion 4 does not have this restriction.
:::

### Route B: Point Companion at a developer modules folder (any version)

Companion can load modules from a folder you choose, called the **developer
modules path**. Companion 3 has no **Import module package** button, so this
is the only way there. The folder is also watched: when the module changes,
Companion restarts it.

The module must be installed and built in place first. In the module folder,
run:

```bash
npm install
npm run build
```

`npm run build` ends without a message when it works.

::: warning
Point Companion at the folder that **contains** the module folder, not at
the module folder itself. With the suggested folders from Step 1, that is
`companion-modules`, not `companion-module-relay-translation`.
:::

**Desktop Companion (macOS, Windows, Linux with a screen):**

1. Open the Companion window: the small launcher window that has the
   **Launch GUI** button. If it is hidden, click the Companion icon in the
   menu bar (macOS) or the system tray (Windows) to bring it back.
2. Click the cog in the top right corner.
3. Set the folder:
   - **Companion 4.1 and later:** a settings window opens. In the
     **Developer** section, click **Select** and choose your
     `companion-modules` folder. Switch on **Enable Developer Modules**.
   - **Companion 3.x and 4.0:** a **Developer modules path** field appears
     in the Companion window. Choose your `companion-modules` folder there.
4. Companion restarts its server. Wait until **Launch GUI** is available
   again, then click it.

**Companion without a screen** (CompanionPi on a Raspberry Pi, or a Linux
server):

1. Copy the whole built module folder, including `node_modules` and `dist`,
   into `/opt/companion-module-dev/` on that machine. You should end up with
   `/opt/companion-module-dev/companion-module-relay-translation`.
2. Restart Companion, or restart the machine.

For other headless installs, the path can also be given with the start-up
option `--extra-module-path` or the setting `COMPANION_DEV_MODULES`. Ask
whoever set up that machine.

When you add the connection in Step 4, the module version is called
**Dev version**.

## Step 4: Add the connection

A **connection** is one device or program that Companion controls. You add
one connection for your Relay computer.

1. Click **Connections** in the menu on the left.
2. Find the Relay module:

   ![Adding a connection: search for Relay.](images/companion-add-connection.png){width=100%}

   1. In **Add New Connection** on the right, type `relay` in the search box.
      Several modules have "relay" in their name. The one you want is
      **Relay: Relay**. If you cannot see it, click **Installed Only**.
   2. Click **Add** next to **Relay: Relay**.

3. A window called **Add Relay: Relay** opens:

   ![Choosing the name and version for the new connection.](images/companion-add-connection-version.png){width=100%}

   1. **Label** is the connection's name in Companion. **Relay** is fine.
      If you control more than one Relay computer, give each a clear name,
      such as `Relay-Hall-A`.
   2. **Module Version**: choose **v1.0.0** (the package you imported), or
      **Dev version** if you used the developer modules folder.
   3. Click **Add**.

   The window closes. The new connection appears on the left with a yellow
   warning sign, and **Edit Connection: Relay: Relay** opens on the right.
   The warning is normal: the connection has no settings yet.

## Step 5: Fill in the connection settings

![The connection settings, filled in with example values.](images/companion-connection-config.png){width=100%}

1. **Host**: the Relay computer's LAN IP address, for example
   `192.168.1.50`. Type only the address: no `https://` and no `/admin`. If
   you typed a hostname during Relay setup, you can use that instead.
2. **Port**: type **`443`**. The box shows `8443` at first. Change it.
   A normal Relay install serves the operator panel on port 443, and
   Companion uses the same door. A **port** is a numbered door on a computer;
   one computer offers different services on different ports.
3. **Admin token**: the operator panel password you chose during Relay setup.
   The picture shows this box empty on purpose. Companion shows what you
   type in plain text, so check who can see your screen.
4. **Use HTTPS**: leave it on. **HTTPS** is the secure, encrypted form of the
   web. Relay's operator panel only speaks HTTPS.
5. **Accept self-signed certificate**: leave it on. Relay makes its own
   certificate, a **self-signed certificate**, because a venue network has no
   public name for a certificate company to check. Without this switch,
   Companion refuses to connect.
6. **Certificate SHA-256 fingerprint (optional)**: paste the fingerprint of
   Relay's certificate. See the next section. You can leave this empty, but
   filling it in is safer.
7. Click **Save**.

Within a second or two, the warning sign on the left turns into a green tick.
See [Reading the connection status](#companion-status).

::: note
**Why not 8443?** Inside the Relay container, the operator panel listens on
port 8443. A normal install publishes it to the network on port 443, which is
why the panel address has no port number in it. The module's default of 8443
only matches a Relay that was started with the setting
`RELAY_HTTPS_PORT=8443`. If your panel address has a port number after the IP
address, such as `https://192.168.1.50:8443/admin`, use that number instead.
:::

### The certificate fingerprint {#companion-fingerprint}

A **fingerprint** is a long code that identifies one certificate, like a
serial number. When you paste it here, Companion talks only to the Relay
computer with exactly that certificate, and refuses anything else, even with
**Accept self-signed certificate** switched on. That stops another computer
on the network from pretending to be Relay.

You can find it in three places:

- **Relay setup** prints it under the heading `Operator panel certificate`,
  on the line that starts `SHA-256`. You were asked to write it down then.
- **Relay start** prints it again on the line
  `Panel certificate SHA-256:`, in the start window (macOS and Windows) or
  the terminal (Linux).
- **The browser**, when you open the operator panel and view the
  certificate. See [Opening the operator panel](#operator-access).

It looks like this, with different characters (it is one long line; it is
split in two here to fit the page):

```
95:6E:3B:BF:4F:2C:BE:AD:22:DB:53:7A:8B:61:B6:34
:E1:FA:AF:B3:4C:72:33:CF:B3:B1:4D:BF:12:FB:3D:61
```

Paste it with or without the colons, in capitals or small letters. The module
accepts all of these. Copy and paste it rather than typing it: one wrong
character and Companion refuses to connect.

::: warning
If you run Relay setup again and it makes a new certificate, the fingerprint
changes. Paste the new one into Companion, or the connection stays red.
:::

## Reading the connection status {#companion-status}

The icon between the version number and the on/off switch shows how the
connection is doing. Point at it with the mouse to see the message.

![A working connection.](images/companion-connection-ok.png){width=100%}

1. A **green tick** means Companion is connected to Relay and receiving
   updates.
2. The **switch** turns the connection on and off. Turning it off and on
   again makes the module reconnect straight away.

When something is wrong, the icon changes to a warning sign. Point at it to
read what is wrong:

![A rejected admin token. Point at the warning sign to see the message.](images/companion-connection-error.png){width=100%}

1. Here the message is **Authentication Failure: Admin token rejected**.

These are the messages you can see, and what they mean:

| Message | What it means | What to do |
|---|---|---|
| **Bad Configuration: Set the Relay host** | The **Host** box is empty. | Fill in the Relay computer's IP address. |
| **Bad Configuration: Set the admin token** | The **Admin token** box is empty. | Fill in your admin token. |
| **Bad Configuration: The certificate fingerprint is not a SHA-256 hash (64 hex characters)** | The fingerprint is too short, too long or has a typing error. | Copy and paste it again. |
| **Authentication Failure: Admin token rejected** | Relay answered, but the token is wrong. | Type the token again. It must match the operator panel password exactly. |
| **Connection Failure: Nothing answering on** *address:port* | Nothing answered at that address and port. | Check the IP address and that **Port** is `443`. Check that Relay is running. |
| **Connection Failure: Relay's certificate was refused…** | **Accept self-signed certificate** is off and no fingerprint is set. | Switch **Accept self-signed certificate** on, or paste the fingerprint. |
| **Connection Failure: Certificate fingerprint does not match the one configured (server: …)** | The certificate Relay showed is not the one you pasted. | Compare the fingerprint after `server:` with the one Relay's start printed. If they match, paste it again. If they do not, you may be talking to the wrong computer. |

You do not need to restart anything after fixing a setting. Click **Save**
and the module tries again. If Relay stops during a show, the connection goes
to a warning within a few seconds, and comes back on its own when Relay
returns.

## Step 6: Put Relay buttons on the Stream Deck

A **preset** is a ready-made button: its text, what it does when pressed, and
how it changes colour. The Relay module comes with presets for everything
most operators need. Start with these rather than building buttons by hand.

1. Click **Buttons** in the menu on the left.
2. On the right, click **Presets**, then **Relay** (it says **Relay: Relay**
   and the number of presets underneath).
3. Click the **Relay** heading to open it. You should see the preset buttons:

   ![The Relay presets, ready to drag onto a page.](images/companion-presets.png){width=70%}

   1. **START** starts capture. It turns green while capture runs.
   2. **STOP** stops capture. It turns grey while capture is stopped.
   3. **Toggle** starts capture if it is stopped and stops it if it is
      running. Its label shows the current state: **Stopped** or
      **Running**. It turns green while capture runs.
   4. **SESSION** shows how long capture has been running, as hours,
      minutes and seconds. It shows `--:--` when stopped, and turns green
      while running. Pressing it does nothing, on purpose, so brushing it by
      accident cannot stop your show.
   5. **Audio meter** shows the microphone level in dBFS (decibels below the
      loudest possible sound; `-30.0 dB` is quieter than `-10.0 dB`). The
      colour gets brighter with more level, amber from `-6` dBFS, and red
      when the sound is clipping (too loud and distorting).
   6. **VIEWERS** shows how many people have the viewer page open. Pressing it
      does nothing.
   7. **Status at a glance** shows **Stopped** or **Running**, and how many
      languages are live. It turns green while running and red if Relay
      reports an error.
   8. and 9. **One button per target language**, here **Spanish** and
      **French**. Pressing it switches that language on or off. The second
      line shows `off`, `enabled` or `live`. See
      [Amber and green](#companion-amber-green).

4. Drag each preset you want onto a square in the grid on the left. The
   squares are the keys of your Stream Deck. The button appears on the Stream
   Deck at once.

The language presets are made from the languages Relay offers right now. If
you change Relay's source language, the list of language presets changes too.
Drag the new ones onto your page.

This is a finished page with capture stopped:

![A page of Relay buttons with capture stopped.](images/companion-buttons.png){width=100%}

1. **START** is not lit, and **STOP** is grey: capture is stopped.
2. **SESSION** shows `--:--`.
3. **Spanish** is amber and says `enabled`: switched on, but not sending
   anything yet, because capture is stopped.
4. **French** is dark and says `off`.

And the same page a few seconds after pressing **START**:

![The same page with capture running.](images/companion-buttons-live.png){width=100%}

1. **START** is green, and **Running** shows on the toggle and the status
   button.
2. **SESSION** is counting.
3. **Spanish** is green and says `live`: captions are being translated into
   Spanish right now.
4. **French** is still off. Press it to switch French on.

::: tip
You can also click a button in Companion's web page to try it, but clicking
a square in the **Buttons** grid opens its editor. To press buttons from the
web page, use **Interactive Buttons** in the left-hand menu.
:::

A button never lights up just because you pressed it. It changes only when
Relay reports back that something happened. If **START** stays dark after you
press it, capture did not start. See
[Troubleshooting](#companion-troubleshooting).

## Amber and green: enabled is not live {#companion-amber-green}

A target language in Relay can be **enabled** without being **live**:

- **Enabled** means the language is switched on in Relay's settings. It will
  be translated when capture runs.
- **Live** means a translation is actually running for it right now. That
  only happens while capture runs.

The language buttons show the difference:

| Button | Second line | Meaning |
|---|---|---|
| Dark | `off` | Switched off. Viewers cannot choose it. |
| **Amber** | `enabled` | Armed: switched on, but sending nothing. Usually because capture is stopped. |
| **Green** | `live` | On the air: translated captions are going out now. |

If a language button stays amber while capture is running, Relay is having
trouble with that language. Check **Session health** in the operator panel
(see [Session health](#session-health)).

## Everything the module can do

The presets use these building blocks. You can also use them yourself in any
button, when you edit a button in Companion.

### Actions (what a button does when pressed)

| Action | What it does |
|---|---|
| **Start capture** | Starts listening and captioning. |
| **Stop capture** | Stops it. |
| **Toggle capture** | Starts capture if it is stopped, stops it if it is running. |
| **Set target enabled** | Switches one language on, off, or to the opposite of what it is now. |
| **Enable only this target** | Switches one language on and every other language off. |

If Relay refuses an action, the reason appears on Companion's **Log** page, in
Relay's own words. The most common one is starting capture with no OpenAI API
key set.

### Feedbacks (how a button changes colour)

A **feedback** changes a button's look when something is true.

| Feedback | Lights up when | Default colour |
|---|---|---|
| **Capture running** | Relay is capturing. | Green |
| **Capture stopped** | Relay is not capturing. | Grey |
| **Relay error** | Relay reported an error, or a language failed for good. | Red |
| **Target live** | A language is being translated right now. | Green |
| **Target enabled but not live** | A language is switched on but sending nothing. | Amber |
| **Speaking** | Relay hears speech. | Green |
| **Audio clipping** | The microphone signal is too loud and distorting. | Red |
| **Session reconnecting** | A language has lost its connection to OpenAI while capture runs. | Amber |
| **Audio level (meter)** | Always. Colours the button by the microphone level. | Green to red |

You can change any of these colours. Companion keeps your choice.

### Variables (live values you can show on a button)

A **variable** is a value that changes, such as the session time. You put it
in a button's text and Companion keeps it up to date. Companion writes a
variable as `$(label:name)`, where *label* is the connection's **Label**. The
exact names for your connection are listed on the **Variables** page.

| Name | What it shows |
|---|---|
| `running` | `Running` or `Stopped` |
| `session_time` | How long capture has run, as `H:MM:SS`, or `--:--` when stopped |
| `session_time_seconds` | The same, as a number of seconds |
| `source_language` | The language Relay is listening for |
| `viewers` | How many people have the viewer page open |
| `targets_live` | How many languages are live |
| `targets_live_labels` | The names of the live languages |
| `level` | Microphone level, from 0 to 1 |
| `rms_dbfs`, `peak_dbfs` | Microphone level in dBFS: average and peak |
| `clipping`, `speaking` | `Yes` or `No` |
| `clipped_samples` | How many too-loud moments since capture started |
| `error` | Relay's last error message, empty when there is none |
| `sessions_error` | How many languages are in trouble |
| `target_spanish_state` (one per language) | `live`, `enabled` or `off` for that language |

The viewer count updates only when something else in Relay changes, so it can
lag behind. Relay's operator panel has the same delay.

## Keep the admin token safe

::: warning
Companion stores the admin token in its own settings file **in plain text**,
without encryption. Anyone who can open that file, or copy Companion's
settings, can read the token. With it, they control your Relay session and can
spend money on your OpenAI account.
:::

- Treat the Companion computer as carefully as the Relay computer.
- An export from Companion's **Import / Export** page contains the token too.
  Do not share exports without removing the Relay connection first.
- Companion's web page has no password by default. Anyone on the network who
  opens it can see the token in the connection settings. Set a password or
  lock the page in Companion's **Settings** if other people use the network.
- If you think the token has leaked, change it in Relay's operator panel (see
  [Credentials](#credentials)), then type the new token into Companion.

## Troubleshooting {#companion-troubleshooting}

| Problem | Likely cause | What to do |
|---|---|---|
| `npm` or `node` says "command not found" or "is not recognized" | Node.js is not installed, or the terminal was open before you installed it. | Close the terminal, open a new one, and try again. If it still fails, install Node.js again. |
| `npm install` fails with `npm error` and a network message | No internet, or a venue firewall. | Build on another network, then carry the `.tgz` file over. |
| `npm warn EBADENGINE` | Your Node.js version is not 18 or 22. | Usually harmless. If the build fails, install Node.js 22. |
| There is no **Import module package** button | Companion 3.x. | Use the developer modules folder (Route B), or update Companion. |
| Import says importing from a remote computer is disabled | Companion 5, importing from another computer. | Import on the Companion computer, or switch on **Restricted modules** under **Dangerous Features**. |
| **Relay: Relay** does not appear in **Add New Connection** | The import did not finish, or the developer path points at the wrong folder. | Check the **Modules** page. For Route B, check that the path is the folder that *contains* the module folder, that you ran `npm install` and `npm run build`, and that **Enable Developer Modules** is on. |
| The connection shows **Nothing answering** | Wrong IP address or port, Relay not running, or the two computers are on different networks. | Use port `443`. Open `https://<IP address>/admin` in a browser on the Companion computer. If that fails too, the problem is the network or Relay, not Companion. |
| The IP address worked yesterday but not today | The Relay computer got a new address at this venue. | Read the address from today's start output and update **Host**. |
| **Admin token rejected** | Token typed wrong, or changed in Relay. | Type it again. |
| Certificate message | Fingerprint changed after running setup again, or **Accept self-signed certificate** is off. | Paste the fingerprint that start prints now, or switch the setting on. |
| **START** stays dark after pressing it | Relay refused to start. | Open Companion's **Log** page. The message is Relay's own, often a missing OpenAI API key or no audio device. |
| A language button stays amber | Capture is stopped. | Press **START**. If capture runs and it is still amber, check **Session health** in the operator panel. |
| The language presets are missing or wrong | The connection was not connected when you opened the presets, or Relay's source language changed. | Wait for the green tick, then open **Presets** again. |
| **VIEWERS** seems stuck | Known delay: the count updates only when something else changes. | No action needed. |

## Updating the module later

When a new version of the module comes out:

1. Download it again from GitHub (ZIP), or run `git pull` in the module
   folder (git).
2. In the module folder, run `npm install` again.

**If you imported a package (Route A):**

3. Run `npm run package` to make the new `.tgz` file.
4. In Companion, open **Modules** and import it with
   **Import module package**. Both versions are now listed under
   **Relay: Relay**.
5. Open **Connections**, click your Relay connection, and click the pencil
   next to **Module Version**. Choose the new version.
6. When everything works, you can remove the old version: on the **Modules**
   page, click **Relay: Relay**, then the bin icon next to the old version.

**If you used the developer modules folder (Route B):**

3. Run `npm run build`. Companion notices the change and restarts the module.
   If it does not, switch the connection off and on again, or restart
   Companion.

Your buttons, colours and connection settings stay as they are.
