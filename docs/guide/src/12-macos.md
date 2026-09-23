# Installing on macOS {#install-macos}

This chapter installs Relay on a Mac. You use four scripts, all in the Relay
folder:

| Script | What it does | How often |
|---|---|---|
| `setup.command` | Builds Relay and asks for your credentials | Once |
| `start.command` | Starts Relay | Before every event |
| `stop.command` | Stops Relay | After every event |
| `logs.command` | Shows what Relay is doing | When something looks wrong |

To run a script, double-click it in Finder. A **Terminal** window opens and
shows what the script is doing. Terminal is the Mac app for typing commands.
You do not need to type any commands for normal use.

## Requirements

- **A Mac with a recent version of macOS.** Docker Desktop supports the
  current version of macOS and the two versions before it. Both Apple silicon
  (M1 and later) and Intel Macs work.
- **8 GB of memory (RAM) or more** is recommended.
- **Free disk space.** Docker Desktop itself needs several gigabytes. Relay
  adds about 2 GB more: its image is about 750 MB, plus the files Docker keeps
  while it builds it. Have **about 10 GB free** in total to be comfortable.
- **Docker Desktop.** Docker is the tool that runs Relay in a sealed box,
  called a **container**, so you do not have to install anything else by hand.
  The next section installs it.
- **Homebrew and PulseAudio.** Docker on a Mac cannot see the microphone by
  itself. Relay uses a small program called **PulseAudio** to carry the
  microphone's sound into the container. You install PulseAudio with
  **Homebrew**, a free tool for installing programs on a Mac. See
  [Installing PulseAudio](#macos-pulseaudio).
- **A network connection.** The Mac must be on the same local network (LAN)
  as the phones and screens that show captions, and it must be able to reach
  the internet, so Relay can talk to OpenAI.
- **An OpenAI API key.** Part 2 explains how to get one.

## Installing Docker Desktop

1. Open <https://www.docker.com/products/docker-desktop/> in your browser.
2. Click **Download for Mac** and choose the version for your Mac:
   **Apple Silicon** or **Intel Chip**.

   To check which one you have, open the Apple menu and choose
   **About This Mac**. Next to **Chip** (or **Processor**) it says either
   "Apple M…" (Apple silicon) or "Intel".

   Your browser downloads a file called `Docker.dmg`.

3. Double-click `Docker.dmg`.

   A window opens showing the Docker whale icon and your **Applications**
   folder.

4. Drag the Docker icon onto the **Applications** folder.

   macOS copies Docker into Applications. This takes a minute.

5. Open **Applications** and double-click **Docker**.

   macOS may ask whether you are sure you want to open an app downloaded from
   the internet. Click **Open**.

6. Docker shows its service agreement. Read it and click **Accept**.
7. Docker asks how to finish the setup. Choose **Use recommended settings**
   and click **Finish**. Type your Mac password when asked.
8. Docker may ask you to sign in or answer a survey. You can skip both. You
   do not need a Docker account to run Relay.
9. Wait until Docker has started.

   You should see a small whale icon in the menu bar at the top of the screen.
   In the Docker Desktop window, the bottom-left corner should say
   **Engine running**.

10. Make Docker start by itself. In Docker Desktop, click the gear icon
    (**Settings**), then **General**, and tick
    **Start Docker Desktop when you sign in to your computer**. Click
    **Apply & restart**.

::: note
Docker Desktop is free for personal use, education, and small businesses.
Larger organisations may need a paid Docker subscription. If you are not sure,
ask your IT department.
:::

## Installing PulseAudio {#macos-pulseaudio}

You need to do this once. `start.command` refuses to start without it.

1. Open **Terminal**. It is in **Applications → Utilities**.
2. Check whether Homebrew is installed:

   ```bash
   brew --version
   ```

   If you see a line such as `Homebrew 4.4.0`, skip to step 4. If you see
   `command not found: brew`, go to step 3.

3. Install Homebrew. Go to <https://brew.sh>, copy the install command shown
   on that page, paste it into Terminal and press `Return`. Follow what it
   asks. It may ask for your Mac password and take several minutes. At the
   end, it prints **Next steps**: run the commands it lists there, then close
   Terminal and open it again.
4. Install PulseAudio:

   ```bash
   brew install pulseaudio
   ```

   Homebrew prints a lot of text while it works. When it finishes, you see
   your prompt again.

You never need to start PulseAudio yourself. `start.command` starts it for
you every time.

## Downloading Relay

Download Relay and put it in your home folder, as described in
[Downloading from GitHub](#download). You should end up with a folder called
`Relay` in your home folder, containing `setup.command` and `start.command`.

## Running setup

Setup builds Relay and asks for three things: your OpenAI API key, an admin
token, and (if you want one) a hostname. It takes a few minutes the first time.

Before you start, have your **OpenAI API key** ready (see Part 2), and decide
on an **admin token**. The admin token is the password for the operator panel.

1. Make sure Docker Desktop is running. You should see the whale in the menu
   bar.
2. In Finder, open the `Relay` folder and double-click `setup.command`.

   If macOS says it cannot open the file, see
   [Platform notes](#macos-notes) below.

   A Terminal window opens. You should see:

   ```
   Relay — setup
   =============

   Docker — ok
   Building the image (this can take a few minutes the first time)…
   ```

3. Wait while Docker builds Relay. Many lines scroll past. This can take
   several minutes the first time, because Docker downloads what Relay needs
   from the internet. When it finishes, you should see:

   ```
   Image — ok
   ```

### The API key

Next, setup asks for your API key:

```
OpenAI API key
  Create one at https://platform.openai.com/api-keys
  It is stored only in docker-config/config.json on this machine
  (permissions 0600). Input is hidden.
  API key:
```

4. Paste your API key and press `Return`.

   Nothing appears while you paste or type. This is normal: the key is hidden
   so nobody can read it over your shoulder. Paste once only, then press
   `Return`.

   If you press `Return` without pasting anything, setup says
   `The API key cannot be empty.` and asks again.

### The admin token

```
Admin token
  This is the password for the operator panel.
  Choose something only you know — anyone with it controls the session
  and can spend against your OpenAI account. Minimum 8 characters.
  Admin token:
```

5. Type your admin token and press `Return`. It is hidden too.
6. Setup asks you to type it again:

   ```
     Confirm admin token:
   ```

   Type the same token again and press `Return`.

Setup refuses some tokens and asks again:

- `Too short — use at least 8 characters.`
- `That token is guessable. Pick another.` (for `changeme`, `password`,
  `admin` or `relay`)
- `Tokens did not match — try again.` (the two entries were different)

::: warning
The admin token protects the operator panel, and the panel spends money on
your OpenAI account. Choose a real password, not a single word. Write it down
somewhere safe. You need it every time you sign in to the panel.
:::

### The hostname (optional)

```
Panel hostname (optional)
  The operator panel is served over HTTPS. Its certificate always covers
  this machine's LAN address. If you also reach this host by a hostname,
  type it now so the certificate covers that too.
  Leave blank to use the IP address only.
  Hostname (FQDN), or blank:
```

The operator panel is served over **HTTPS**, the secure form of web address
that starts with `https://`. HTTPS needs a **certificate**: a small file that
proves to the browser which computer it is talking to. The certificate must
name the address you type in the browser.

Every computer on a network has an **IP address**, a number such as
`192.168.1.50`. The certificate always includes this Mac's IP address, so most
people leave the hostname blank.

7. Press `Return` to leave it blank. Or, if you reach this Mac by a name, such
   as `relay.local`, type that name and press `Return`.

::: tip
Your Mac's local name is shown in **System Settings → General → Sharing**, at
the bottom, under **Local hostname**. It ends in `.local`.
:::

### The certificate fingerprint

Setup saves your answers and then creates the certificate. You should see
something like this. Your numbers will be different:

```
Wrote /app/config/config.json (permissions 0600).
Operator panel certificate
  Certifying this machine's LAN address, 192.168.1.50.

Wrote config/certs/admin.crt and config/certs/admin.key (key 0600).
Wrote config/Caddyfile.

  Certificate names : relay.local, localhost, 1c9e2b7a4f30, 1c9e2b7a4f30.local, 192.168.1.50, 172.18.0.2, 127.0.0.1
  SHA-256           : 3F:9A:C2:17:5B:E0:44:8D:…:6A:0F

  Panel   : https://relay.local/admin
  Viewers : http://192.168.1.50/

  The certificate is self-signed, so the browser will warn once. Check
  the fingerprint it shows against the SHA-256 above before you accept
  it -- that check is what makes this connection worth anything.

Setup complete.
  Next: double-click start.command to launch Relay.
  Write down the SHA-256 fingerprint above — you check it against the
  browser the first time you open the panel.
  To change the key, the token or the hostname later, open the operator
  panel or run this again.

Press return to close.
```

The line starting `SHA-256` is the certificate's **fingerprint**: a long code
that is unique to this certificate. The first time you open the panel, the
browser warns you that it does not know the certificate. You compare the
fingerprint the browser shows with this one. If they match, you are talking to
your own Mac, and it is safe to continue.

8. Write down the fingerprint, or take a photo of the screen. Keep it with
   your admin token.
9. Press `Return` to close the window.

::: note
The paths in this output, such as `/app/config/config.json`, are paths
*inside* the container. On your Mac, the same files are in the
`docker-config` folder inside your Relay folder.
:::

::: note
If the line says `Certifying this machine's LAN address, 127.0.0.1.`, the Mac
was not connected to a network when you ran setup. That is fine: start checks
the address again every time, and makes a new certificate if it needs one.
:::

## Starting Relay

Do this before every event.

1. Make sure Docker Desktop is running. You should see the whale in the menu
   bar.
2. Plug in your microphone or audio interface.
3. Double-click `start.command` in the Relay folder.

   A Terminal window opens. First, start starts PulseAudio and checks the
   audio. You should see something like this:

   ```
   Live Caption Relay - starting in Docker...

   Starting PulseAudio on port 4713...
   Checking audio...
   Live Caption Relay -- audio chain check
   image: live-caption-relay   PULSE_SERVER=tcp:host.docker.internal:4713

     PASS  image 'live-caption-relay' is built
     PASS  PulseAudio server reachable at tcp:host.docker.internal:4713
     PASS  PortAudio input devices: pulse default
     PASS  one second of audio captured (rms 0.00218342)
     PASS  signal is non-silent (mic permission is granted)

   All checks passed. Pick 'ALSA: pulse' (or your card) in the operator panel.
   ```

   If PulseAudio is already running, the first line says
   `PulseAudio already running on port 4713.` instead.

   The first time, macOS may ask whether **Terminal** may use the microphone.
   Click **Allow**. See [Platform notes](#macos-notes) if you clicked
   **Don't Allow** by mistake.

4. Wait while Relay starts. You should see:

   ```
   Building and starting the container...
   ```

   followed by some lines from Docker, and then:

   ```
   Waiting for the relay to come up.....
     Relay is up.
     Panel certificate SHA-256: 3F:9A:C2:17:5B:E0:44:8D:…:6A:0F

     Viewer link : http://192.168.1.50/            <- share this with the room
     Panel       : https://192.168.1.50/admin

     The panel's certificate is self-signed, so the browser warns the first
     time. Check the SHA-256 above against what the browser shows before
     accepting it.

     The relay keeps running after this window closes.
     Stop it with stop.command; follow its log with logs.command.

   This window closes in 30 seconds (press return to close now).
   ```

   There are two web addresses:

   - The **Viewer link** starts with `http://` and has no `/admin`. This is
     the link you give to the room. It works on any phone, tablet or laptop on
     the same network.
   - The **Panel** starts with `https://` and ends in `/admin`. This is the
     operator panel, where you control Relay. Keep it to yourself.

5. Your browser opens the panel by itself. The first time, it warns that the
   connection is not private. Compare the fingerprint, as described
   later in this guide, before you continue.

6. The Terminal window closes by itself after 30 seconds. Press `Return` to
   close it sooner.

The window closes because it is no longer needed. Relay runs in the
background, inside Docker, until you stop it. Closing the window does not stop
Relay.

::: note
The viewer and panel addresses use this Mac's current IP address. The address
can change when you move to a different venue or network. Always use the
addresses that start prints on the day.
:::

::: note
If the IP address has changed since you last started Relay, start makes a new
certificate for the new address, and the fingerprint changes. The line
`Panel certificate SHA-256` always shows the current one. Compare the browser
with that line.
:::

If something goes wrong, the window stays open and shows the error. Read it,
then see [Troubleshooting the installation](#install-troubleshooting).

## Stopping Relay

Stop Relay after every event.

1. Double-click `stop.command` in the Relay folder.

   A Terminal window opens. You should see:

   ```
   Live Caption Relay - stopping...

   [Docker's own lines, ending with the container being removed]
   Stopped PulseAudio.

   Stopped.
   This window closes in 5 seconds.
   ```

2. The window closes by itself after 5 seconds.

Stop closes the container and PulseAudio. Your settings and credentials stay
in `docker-config` and are there the next time you start.

::: tip
Stopping Relay is not the same as pressing **Stop** in the panel. The
panel's **Stop** button ends the paid OpenAI session but leaves Relay running.
`stop.command` shuts Relay down completely.
:::

## Viewing the logs

The **log** is the list of messages Relay writes while it runs. It is useful
when something is not working, or when someone helping you asks for it.

1. Double-click `logs.command` in the Relay folder.

   A Terminal window opens and shows:

   ```
   Following the relay's log. Close this window to stop following it.
   ```

   followed by the last 200 lines of the log. New lines appear as Relay
   writes them. For example:

   ```
   2026-09-23 18:02:11,512 INFO    relay  Viewer socket: 127.0.0.1:8000
   2026-09-23 18:02:11,513 INFO    relay  Panel socket: 127.0.0.1:8001 (loopback only, HTTPS via the front end)
   ```

2. Close the window when you have finished. This does not stop Relay.

If Relay is not running, the window shows an error such as
`No such container: live-caption-relay`, then `Press return to close.`

## Running setup again

Run setup again when you want to:

- change the API key or the admin token;
- add, change or remove the hostname;
- check or renew the panel certificate.

1. Stop Relay with `stop.command`.
2. Double-click `setup.command`.

   Setup builds the image again (much faster this time), then asks:

   ```
   docker-config/config.json already exists.
   Reconfigure the API key and admin token? [y/N]
   ```

3. Type `y` and press `Return` to enter a new API key, admin token and
   hostname. All three questions are asked again, and your other settings are
   kept.

   Or just press `Return` to keep your credentials. Setup then skips straight
   to the certificate.

4. Setup prints the certificate details and fingerprint. If the line about
   the certificate says `Reusing config/certs/admin.crt…`, your certificate is still good and
   the fingerprint has not changed. If it says `Wrote config/certs/admin.crt…`,
   setup made a new certificate. Write down the new fingerprint.
5. Start Relay again with `start.command`.

The certificate lasts 397 days. Setup and start only replace it when it has
fewer than 30 days left, or when it does not cover this Mac's current address
or hostname. You do not normally need to do anything to renew it.

::: tip
To force a brand-new certificate (for example, if you think someone copied
it), stop Relay, delete the `certs` folder inside `docker-config`, and run
setup again. Only delete `certs`, not the whole `docker-config` folder.
:::

You can also change the API key and admin token from the operator panel,
without running setup.

## Platform notes {#macos-notes}

### "Cannot be opened" or "Apple could not verify"

The first time you double-click a Relay script, macOS may refuse to open it,
because it was downloaded from the internet and is not signed by a known
developer. The message says something like
**"setup.command" cannot be opened because it is from an unidentified
developer**, or **Apple could not verify "setup.command" is free of malware**.

1. Click **Done** (or **OK**). Do not click **Move to Trash**.
2. Open the Apple menu, choose **System Settings**, then
   **Privacy & Security**.
3. Scroll down to the **Security** section. You should see a message saying
   that `setup.command` was blocked. Click **Open Anyway**.
4. Type your Mac password if asked, then click **Open** (or **Open Anyway**)
   in the box that appears.

On older versions of macOS, you can instead Control-click (or right-click) the
script in Finder, choose **Open**, then click **Open** again.

You need to do this once for each script. To do it for all of them at once,
open Terminal and remove the "downloaded from the internet" mark from the
whole Relay folder:

```bash
xattr -dr com.apple.quarantine ~/Relay
```

This mark is only added to downloaded ZIP files. A copy made with `git clone`
does not have it.

### Microphone permission for Terminal

macOS asks before any app can use the microphone. PulseAudio is started from
Terminal, so it is **Terminal** that needs permission.

If the audio check says `FAIL  signal is non-silent`, with
`digital silence` on the next line, Terminal does not have permission:

1. Open **System Settings → Privacy & Security → Microphone**.
2. Turn on the switch next to **Terminal**.
3. Double-click `stop.command`, then `start.command`, so PulseAudio starts
   again with the new permission.

### After a restart, run start.command again

Docker restarts Relay by itself when your Mac starts, unless you stopped it
with `stop.command` first. But PulseAudio does not restart, so Relay comes back
with no microphone.

After you restart or turn on the Mac, always double-click `start.command`,
even if Relay seems to be running. It starts PulseAudio and reconnects
everything.

### The Mac's IP address

Start finds the Mac's IP address from its Wi-Fi or built-in Ethernet port. If
the addresses it prints show `127.0.0.1`, it could not find one. Check that
the Mac is connected to the network. See
[Troubleshooting the installation](#install-troubleshooting) if you use a USB
or Thunderbolt Ethernet adapter.

## Rehearsal mode

**Rehearsal mode** shows made-up captions on the viewer pages. Use it to check
fonts, the projector and the link on every phone in the room. It does not
connect to OpenAI, so it costs nothing.

1. If Relay is running, stop it with `stop.command`.
2. Open **Terminal**.
3. Type these two lines, pressing `Return` after each:

   ```bash
   cd ~/Relay
   RELAY_DEMO=1 ./start.command
   ```

   Start runs as usual and prints the same addresses. The Terminal window
   closes by itself after 30 seconds.

4. Open the viewer link. You should see sample captions appear.

To check that rehearsal mode is on, open `logs.command`. You should see the
line:

```
REHEARSAL MODE: captions are canned. No OpenAI session is open.
```

To leave rehearsal mode, double-click `stop.command`, then double-click
`start.command`. A normal start never uses rehearsal mode.

::: warning
Relay stays in rehearsal mode until you stop it, even after a restart. Always
stop it after a rehearsal, then start it normally before the event.
:::
