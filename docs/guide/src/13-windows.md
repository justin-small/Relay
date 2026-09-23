# Installing on Windows {#install-windows}

This chapter installs Relay on a Windows PC. You use four scripts, all in the
Relay folder:

| Script | What it does | How often |
|---|---|---|
| `setup.bat` | Builds Relay and asks for your credentials | Once |
| `start.bat` | Starts Relay | Before every event |
| `stop.bat` | Stops Relay | After every event |
| `logs.bat` | Shows what Relay is doing | When something looks wrong |

To run a script, double-click it in File Explorer. A black **Command Prompt**
window opens and shows what the script is doing. You do not need to type any
commands for normal use.

## Requirements

- **Windows 11.** Windows 10 does not work. Relay reaches the microphone
  through a part of Windows 11 called **WSLg**, and start checks for it.
- **WSL 2 with Ubuntu.** **WSL** (Windows Subsystem for Linux) lets Windows
  run Linux programs. Docker Desktop runs on it, and Relay's scripts use it to
  reach the microphone. You install WSL and **Ubuntu**, a popular version of
  Linux, in [Installing WSL and Ubuntu](#windows-wsl).
- **Virtualisation turned on.** Most PCs have it on already. If Docker says
  it is off, see [Troubleshooting the installation](#install-troubleshooting).
- **8 GB of memory (RAM) or more** is recommended.
- **Free disk space.** Docker Desktop and WSL need several gigabytes. Relay
  adds about 2 GB more: its image is about 750 MB, plus the files Docker keeps
  while it builds it. Have **about 10 GB free** on drive C: to be comfortable.
- **Docker Desktop.** Docker is the tool that runs Relay in a sealed box,
  called a **container**, so you do not have to install anything else by hand.
- **A network connection.** The PC must be on the same local network (LAN)
  as the phones and screens that show captions, and it must be able to reach
  the internet, so Relay can talk to OpenAI.
- **An OpenAI API key.** Part 2 explains how to get one.

## Installing WSL and Ubuntu {#windows-wsl}

You do this once.

1. Click **Start**, type `Terminal`, right-click **Terminal** and choose
   **Run as administrator**. Click **Yes** when Windows asks for permission.

   A window opens with a prompt such as `PS C:\Windows\system32>`.

2. Type this and press `Enter`:

   ```bat
   wsl --install
   ```

   Windows downloads and installs WSL and Ubuntu. This takes several minutes.
   When it finishes, it asks you to restart.

   If WSL is already installed, this command prints its help text or says
   that Ubuntu is already installed. That is fine.

3. Restart the PC.
4. After the restart, an **Ubuntu** window opens by itself. (If it does not,
   click **Start** and open **Ubuntu**.) It says it is installing, then asks:

   ```
   Enter new UNIX username:
   ```

   Type a short user name in lower case, such as `relay`, and press `Enter`.
   Then type a password, press `Enter`, and type it again. The password is
   hidden as you type.

   This user name and password are only for Ubuntu on this PC. Write them
   down; you rarely need them.

5. Close the Ubuntu window.
6. Open **Terminal** again (it does not need to be as administrator) and type:

   ```bat
   wsl --update
   wsl -l -v
   ```

   The second command lists your Linux systems. You should see `Ubuntu` with
   a `*` next to it (that means it is the default) and `2` in the **VERSION**
   column:

   ```
     NAME              STATE           VERSION
   * Ubuntu            Running         2
   ```

   If the `*` is next to something else, such as `docker-desktop`, make
   Ubuntu the default:

   ```bat
   wsl --set-default Ubuntu
   ```

::: note
Relay's scripts run their work inside your default Linux system. That is why
Ubuntu must be the default, with the `*` next to it. You never need to open
Ubuntu yourself for normal use.
:::

## Installing Docker Desktop

1. Open <https://www.docker.com/products/docker-desktop/> in your browser.
2. Click **Download for Windows**. If there is a choice, pick the one for
   your processor: most PCs need the ordinary one (AMD64), not ARM64.

   Your browser downloads a file called `Docker Desktop Installer.exe`.

3. Double-click `Docker Desktop Installer.exe`. Click **Yes** when Windows
   asks for permission.

   The installer shows a **Configuration** screen with some checkboxes.

4. Make sure **Use WSL 2 instead of Hyper-V (recommended)** is ticked. (Some
   versions do not show this option; they use WSL 2 automatically.) Click
   **OK**.

   The installer unpacks files. This takes a few minutes.

5. When it says **Installation succeeded**, click **Close and restart** (or
   **Close and log out**). Save your work first: the PC restarts or signs you
   out.
6. After you sign in again, Docker Desktop opens by itself. If it does not,
   open it from the **Start** menu.
7. Docker shows its service agreement. Read it and click **Accept**.
8. Docker may ask you to sign in or answer a survey. You can skip both. You
   do not need a Docker account to run Relay.
9. Wait until Docker has started.

   You should see a small whale icon in the notification area, near the clock
   (you may need to click the `^` arrow to see it). In the Docker Desktop
   window, the bottom-left corner should say **Engine running**.

10. Check Docker's settings. In Docker Desktop, click the gear icon
    (**Settings**):

    - Under **General**, make sure **Use the WSL 2 based engine** is ticked,
      and tick **Start Docker Desktop when you sign in to your computer**.
    - Under **Resources → WSL integration**, make sure
      **Enable integration with my default WSL distro** is ticked, and turn on
      the switch next to **Ubuntu**.

    Click **Apply & restart**.

::: note
Docker Desktop is free for personal use, education, and small businesses.
Larger organisations may need a paid Docker subscription. If you are not sure,
ask your IT department.
:::

## Downloading Relay

Download Relay and put it in `C:\Relay`, as described in
[Downloading from GitHub](#download). Remember to extract the ZIP file first.
You should end up with a folder `C:\Relay` containing `setup.bat` and
`start.bat`.

## Running setup

Setup builds Relay and asks for three things: your OpenAI API key, an admin
token, and (if you want one) a hostname. It takes a few minutes the first time.

Before you start, have your **OpenAI API key** ready (see Part 2), and decide
on an **admin token**. The admin token is the password for the operator panel.

1. Make sure Docker Desktop is running. You should see the whale near the
   clock.
2. In File Explorer, open `C:\Relay` and double-click `setup.bat`.

   If Windows shows **Windows protected your PC**, see
   [Platform notes](#windows-notes) below.

   A Command Prompt window opens. You should see:

   ```
   Relay - setup
   =============

   Docker - ok
   Building the image (this can take a few minutes the first time)...
   ```

3. Wait while Docker builds Relay. Many lines scroll past. This can take
   several minutes the first time, because Docker downloads what Relay needs
   from the internet. When it finishes, you should see:

   ```
   Image - ok
   ```

### The API key

Next, setup asks for your API key:

```
OpenAI API key
  Create one at https://platform.openai.com/api-keys
  It is stored only in docker-config\config.json on this machine.
  Input is hidden.
  API key:
```

4. Paste your API key and press `Enter`. To paste in the Command Prompt
   window, right-click inside it, or press `Ctrl`+`V`.

   A star (`*`) appears for each character, so nobody can read the key over
   your shoulder. Paste once only, then press `Enter`.

   If you press `Enter` without pasting anything, setup says
   `The API key cannot be empty.` and asks again.

### The admin token

```
Admin token
  This is the password for the operator panel.
  Choose something only you know - anyone with it controls the session
  and can spend against your OpenAI account. Minimum 8 characters.
  Admin token:
```

5. Type your admin token and press `Enter`. It shows as stars too.
6. Setup asks you to type it again:

   ```
     Confirm admin token:
   ```

   Type the same token again and press `Enter`.

Setup refuses some tokens and asks again:

- `Too short - use at least 8 characters.`
- `That token is guessable. Pick another.` (for `changeme`, `password`,
  `admin` or `relay`)
- `Tokens did not match - try again.` (the two entries were different)

::: warning
On Windows, use only letters, numbers, and the symbols `-`, `_` and `.` in
your admin token. Other symbols, such as `!`, `'`, `"`, `&`, `%` and `^`, can
be changed or lost on the way from Command Prompt into Relay, and then your
token does not work in the panel. A long token made of several words joined
with `-` is both safe and easy to type.
:::

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
`192.168.1.50`. The certificate always includes this PC's IP address, so most
people leave the hostname blank.

7. Press `Enter` to leave it blank. Or, if you reach this PC by a name, such
   as `relay.local`, type that name and press `Enter`.

### The certificate fingerprint

Setup saves your answers and then creates the certificate. You should see
something like this. Your numbers will be different:

```
Wrote /app/config/config.json (permissions 0600).
Operator panel certificate
  Certifying this machine's LAN address, 192.168.1.50.

Wrote config/certs/admin.crt and config/certs/admin.key (key 0600).
Wrote config/Caddyfile.

  Certificate names : localhost, 1c9e2b7a4f30, 1c9e2b7a4f30.local, 192.168.1.50, 172.18.0.2, 127.0.0.1
  SHA-256           : 3F:9A:C2:17:5B:E0:44:8D:…:6A:0F

  Panel   : https://192.168.1.50/admin
  Viewers : http://192.168.1.50/

  The certificate is self-signed, so the browser will warn once. Check
  the fingerprint it shows against the SHA-256 above before you accept
  it -- that check is what makes this connection worth anything.

Setup complete.
  Next: double-click start.bat to launch Relay.
  Write down the SHA-256 fingerprint above - you check it against the
  browser the first time you open the panel.
  To change the key, the token or the hostname later, open the operator
  panel or run this again.

Press any key to continue . . .
```

The line starting `SHA-256` is the certificate's **fingerprint**: a long code
that is unique to this certificate. The first time you open the panel, the
browser warns you that it does not know the certificate. You compare the
fingerprint the browser shows with this one. If they match, you are talking to
your own PC, and it is safe to continue.

8. Write down the fingerprint, or take a photo of the screen. Keep it with
   your admin token.
9. Press any key to close the window.

::: note
The paths in this output, such as `/app/config/config.json`, are paths
*inside* the container. On your PC, the same files are in
`C:\Relay\docker-config`.
:::

If anything fails, setup prints what went wrong, then `Setup did not finish.`
and waits for a key press. See
[Troubleshooting the installation](#install-troubleshooting).

## Starting Relay

Do this before every event.

1. Make sure Docker Desktop is running. You should see the whale near the
   clock.
2. Plug in your microphone or audio interface, and make it the default
   recording device in Windows. See [Choosing the microphone](#windows-mic).
3. Double-click `start.bat` in `C:\Relay`.

   A Command Prompt window opens. First, start checks the audio. You should
   see something like this:

   ```
   Live Caption Relay - starting...

   Checking audio...
   Live Caption Relay -- audio chain check
   image: live-caption-relay   PULSE_SERVER=unix:/mnt/wslg/PulseServer

     PASS  image 'live-caption-relay' is built
     PASS  PulseAudio server reachable at unix:/mnt/wslg/PulseServer
     PASS  PortAudio input devices: pulse default
     PASS  one second of audio captured (rms 0.00218342)
     PASS  signal is non-silent (mic permission is granted)

   All checks passed. Pick 'ALSA: pulse' (or your card) in the operator panel.
   ```

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
     Stop it with stop.bat; follow its log with logs.bat.

   Waiting for 30 seconds, press a key to continue ...
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

6. The Command Prompt window counts down and closes by itself after 30
   seconds. Press any key to close it sooner.

The window closes because it is no longer needed. Relay runs in the
background, inside Docker, until you stop it. Closing the window does not stop
Relay.

::: note
The viewer and panel addresses use this PC's current IP address. The address
can change when you move to a different venue or network. Always use the
addresses that start prints on the day.
:::

::: note
If the IP address has changed since you last started Relay, start makes a new
certificate for the new address, and the fingerprint changes. The line
`Panel certificate SHA-256` always shows the current one. Compare the browser
with that line.
:::

If the audio check fails, start shows the `FAIL` lines and asks:

```
  Audio checks failed - see the FAIL lines above.

  The usual cause is microphone permission: open Settings - Privacy &
  Security - Microphone and turn on BOTH "Let apps access your
  microphone" and "Let desktop apps access your microphone".

Start anyway [Y,N]?
```

Press `N` to stop and fix the problem (see [Platform notes](#windows-notes)).
Press `Y` only if you want to start without a working microphone, for example
to test the viewer pages.

If something else goes wrong, the window stays open and shows the error. Read
it, then see [Troubleshooting the installation](#install-troubleshooting).

## Stopping Relay

Stop Relay after every event.

1. Double-click `stop.bat` in `C:\Relay`.

   A Command Prompt window opens. You should see:

   ```
   Live Caption Relay - stopping...

   [Docker's own lines, ending with the container being removed]

   Stopped.

   Waiting for 5 seconds, press a key to continue ...
   ```

2. The window closes by itself after 5 seconds.

Your settings and credentials stay in `C:\Relay\docker-config` and are there
the next time you start.

::: tip
Stopping Relay is not the same as pressing **Stop** in the panel. The
panel's **Stop** button ends the paid OpenAI session but leaves Relay running.
`stop.bat` shuts Relay down completely.
:::

## Viewing the logs

The **log** is the list of messages Relay writes while it runs. It is useful
when something is not working, or when someone helping you asks for it.

1. Double-click `logs.bat` in `C:\Relay`.

   A Command Prompt window opens and shows:

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
`No such container: live-caption-relay`, then `Press any key to continue . . .`

## Running setup again

Run setup again when you want to:

- change the API key or the admin token;
- add, change or remove the hostname;
- check or renew the panel certificate.

1. Stop Relay with `stop.bat`.
2. Double-click `setup.bat`.

   Setup builds the image again (much faster this time), then asks:

   ```
   docker-config/config.json already exists.
   Reconfigure the API key and admin token? [y/N]
   ```

3. Type `y` and press `Enter` to enter a new API key, admin token and
   hostname. All three questions are asked again, and your other settings are
   kept. Type just the letter `y`: on Windows, `yes` counts as no.

   Or just press `Enter` to keep your credentials. Setup then skips straight
   to the certificate.

4. Setup prints the certificate details and fingerprint. If the line about
   the certificate says `Reusing config/certs/admin.crt…`, your certificate is still good and
   the fingerprint has not changed. If it says `Wrote config/certs/admin.crt…`,
   setup made a new certificate. Write down the new fingerprint.
5. Start Relay again with `start.bat`.

The certificate lasts 397 days. Setup and start only replace it when it has
fewer than 30 days left, or when it does not cover this PC's current address
or hostname. You do not normally need to do anything to renew it.

::: tip
To force a brand-new certificate (for example, if you think someone copied
it), stop Relay, delete the `certs` folder inside `C:\Relay\docker-config`,
and run setup again. Only delete `certs`, not the whole `docker-config`
folder.
:::

You can also change the API key and admin token from the operator panel,
without running setup.

## Platform notes {#windows-notes}

### "Windows protected your PC"

The first time you double-click a Relay script, Windows may show a blue box
titled **Windows protected your PC**. This is **SmartScreen**, which warns
about files downloaded from the internet.

1. Click **More info**.

   The box shows the file name and **Publisher: Unknown publisher**, and a new
   button appears.

2. Click **Run anyway**.

You need to do this once for each script. To avoid it, tick **Unblock** in
the ZIP file's **Properties** before you extract it (see
[Downloading from GitHub](#download)).

### Docker Desktop settings

Relay needs these Docker Desktop settings. They are usually on already. To
check, open Docker Desktop and click the gear icon (**Settings**):

- **General → Use the WSL 2 based engine**: ticked.
- **General → Start Docker Desktop when you sign in to your computer**:
  ticked, so Docker is ready when you arrive at the venue.
- **Resources → WSL integration → Enable integration with my default WSL
  distro**: ticked, with **Ubuntu** switched on.

If you change anything, click **Apply & restart**.

### Choosing the microphone {#windows-mic}

Relay hears whatever Windows uses as its default recording device. Windows
passes that device into WSL, where it appears as a source called
**RDPSource**.

1. Open **Settings → System → Sound**.
2. Under **Input**, choose your microphone or audio interface in
   **Choose a device for speaking or recording**.
3. Speak into it. The **Input volume** bar should move.

If you change the default device while Relay is running, run `stop.bat` and
`start.bat` so Relay picks it up.

### Microphone privacy settings

Windows blocks the microphone unless you allow it. If the audio check says
`FAIL  signal is non-silent` with `digital silence` on the next line, turn on
access:

1. Open **Settings → Privacy & security → Microphone**.
2. Turn on **Microphone access**.
3. Turn on **Let apps access your microphone**.
4. Turn on **Let desktop apps access your microphone**. This one is further
   down the page, and it is the one people most often miss.
5. Run `start.bat` again.

### Windows Firewall

The first time Relay starts, Windows Defender Firewall may ask whether to
allow Docker to communicate on networks. Tick **Private networks** and click
**Allow access**. Without this, phones in the room cannot open the viewer
link.

At a venue, Windows may treat the network as **Public**. If phones cannot
connect, open **Settings → Network & internet**, click your connection, and
set **Network profile type** to **Private network**.

### After a restart

Docker restarts Relay by itself when Docker Desktop starts, unless you stopped
it with `stop.bat` first. It is still best to double-click `start.bat` after a
restart: it checks the audio and prints the current addresses.

## Rehearsal mode

**Rehearsal mode** shows made-up captions on the viewer pages. Use it to check
fonts, the projector and the link on every phone in the room. It does not
connect to OpenAI, so it costs nothing.

On Windows, you start rehearsal mode by typing a few lines in Command Prompt.

1. If Relay is running, stop it with `stop.bat`.
2. Click **Start**, type `cmd`, and open **Command Prompt**.
3. Type these lines, pressing `Enter` after each one:

   ```bat
   cd /d C:\Relay
   set RELAY_DEMO=1
   set WSLENV=RELAY_DEMO
   start.bat
   ```

   Type them all in the **same** Command Prompt window. `set` only lasts in
   the window where you typed it.

   The second line turns on rehearsal mode. The third line tells Windows to
   pass that setting on to WSL, where Relay's scripts do their work. Without
   it, Relay starts normally.

   Start runs as usual and prints the same addresses. When it finishes, you
   are back at the prompt.

4. Open the viewer link. You should see sample captions appear.
5. Close the Command Prompt window.

To check that rehearsal mode is on, double-click `logs.bat`. You should see
the line:

```
REHEARSAL MODE: captions are canned. No OpenAI session is open.
```

If you do not see that line, Relay started normally. Stop it, and check that
you typed all four lines in the same window.

To leave rehearsal mode, double-click `stop.bat`, then double-click
`start.bat`. A normal start never uses rehearsal mode.

::: warning
Relay stays in rehearsal mode until you stop it, even after a restart. Always
stop it after a rehearsal, then start it normally before the event.
:::
