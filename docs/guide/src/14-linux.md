# Installing on Linux {#install-linux}

This chapter installs Relay on a Linux computer. You use four scripts, all in
the Relay folder:

| Script | What it does | How often |
|---|---|---|
| `./setup.sh` | Builds Relay and asks for your credentials | Once |
| `./start.sh` | Starts Relay | Before every event |
| `./stop.sh` | Stops Relay | After every event |
| `./logs.sh` | Shows what Relay is doing | When something looks wrong |

On Linux you run the scripts from a **terminal**, the window where you type
commands. Open a terminal, go to the Relay folder, and type the script name:

```bash
cd ~/Relay
./start.sh
```

The `./` at the start means "the script in this folder".

## Requirements

- **A 64-bit Linux system** that Docker Engine supports, such as Ubuntu,
  Debian or Fedora.
- **Docker Engine with the Compose plugin.** Docker is the tool that runs
  Relay in a sealed box, called a **container**, so you do not have to install
  anything else by hand. The next section installs it.
- **A sound card that Linux can see.** Relay hands the sound card straight to
  the container. Linux lists sound devices in a folder called `/dev/snd`, and
  start checks that it exists.
- **Free disk space.** Relay's image is about 750 MB, plus the files Docker
  keeps while it builds it. Have **about 3 GB free** to be comfortable.
- **4 GB of memory (RAM) or more.** Relay itself is limited to 1 GB.
- **A network connection.** The computer must be on the same local network
  (LAN) as the phones and screens that show captions, and it must be able to
  reach the internet, so Relay can talk to OpenAI.
- **An OpenAI API key.** Part 2 explains how to get one.

::: warning
Use **Docker Engine**, not Docker Desktop for Linux. Docker Desktop runs
containers inside a virtual machine, so it cannot hand your sound card to
Relay.
:::

## Installing Docker Engine

1. Open <https://docs.docker.com/engine/install/> and click your
   distribution (for example **Ubuntu**).
2. Follow the steps under **Install using the apt repository** (or the
   equivalent for your distribution). They install these packages:
   `docker-ce`, `docker-ce-cli`, `containerd.io`, `docker-buildx-plugin` and
   `docker-compose-plugin`.

   The last one, `docker-compose-plugin`, matters: Relay's scripts use the
   `docker compose` command.

3. Make Docker start when the computer starts:

   ```bash
   sudo systemctl enable --now docker
   ```

4. Let your own user run Docker without `sudo`, by adding yourself to the
   `docker` **group** (a list of users who share a permission):

   ```bash
   sudo usermod -aG docker $USER
   ```

5. **Log out and log back in** (or restart the computer). The group change
   only takes effect in a new login.
6. Check that Docker works:

   ```bash
   docker run hello-world
   docker compose version
   ```

   The first command should print `Hello from Docker!` and a short
   explanation. The second should print a line such as
   `Docker Compose version v2.29.7`.

::: warning
Members of the `docker` group can control the whole computer through Docker.
Only add people you trust.
:::

## Downloading Relay

Download Relay and put it in your home folder, as described in
[Downloading from GitHub](#download). If you used the ZIP file, remember to
make the scripts executable:

```bash
cd ~/Relay
chmod +x *.sh tools/*.sh
```

## Running setup

Setup builds Relay and asks for three things: your OpenAI API key, an admin
token, and (if you want one) a hostname. It takes a few minutes the first time.

Before you start, have your **OpenAI API key** ready (see Part 2), and decide
on an **admin token**. The admin token is the password for the operator panel.

1. Open a terminal and type:

   ```bash
   cd ~/Relay
   ./setup.sh
   ```

   You should see:

   ```
   Relay — setup
   =============

   Docker — ok
   Building the image (this can take a few minutes the first time)…
   ```

2. Wait while Docker builds Relay. Many lines scroll past. This can take
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

3. Paste your API key and press `Enter`. In most terminals, paste with
   `Ctrl`+`Shift`+`V`.

   Nothing appears while you paste or type. This is normal: the key is hidden
   so nobody can read it over your shoulder. Paste once only, then press
   `Enter`.

   If you press `Enter` without pasting anything, setup says
   `The API key cannot be empty.` and asks again.

### The admin token

```
Admin token
  This is the password for the operator panel.
  Choose something only you know — anyone with it controls the session
  and can spend against your OpenAI account. Minimum 8 characters.
  Admin token:
```

4. Type your admin token and press `Enter`. It is hidden too.
5. Setup asks you to type it again:

   ```
     Confirm admin token:
   ```

   Type the same token again and press `Enter`.

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
`192.168.1.50`. The certificate always includes this computer's IP address, so
most people leave the hostname blank.

6. Press `Enter` to leave it blank. Or, if you reach this computer by a name,
   such as `relay.local`, type that name and press `Enter`.

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
  Next: run ./start.sh to launch Relay.
  Write down the SHA-256 fingerprint above — you check it against the
  browser the first time you open the panel.
  To change the key, the token or the hostname later, open the operator
  panel or run this again.
```

The line starting `SHA-256` is the certificate's **fingerprint**: a long code
that is unique to this certificate. The first time you open the panel, the
browser warns you that it does not know the certificate. You compare the
fingerprint the browser shows with this one. If they match, you are talking to
your own computer, and it is safe to continue.

7. Write down the fingerprint, or take a photo of the screen. Keep it with
   your admin token.

::: note
The paths in this output, such as `/app/config/config.json`, are paths
*inside* the container. On your computer, the same files are in
`~/Relay/docker-config`.
:::

## Starting Relay

Do this before every event.

1. Plug in your microphone or audio interface.
2. Open a terminal and type:

   ```bash
   cd ~/Relay
   ./start.sh
   ```

   First, start checks the audio. You should see something like this:

   ```
   Live Caption Relay - starting...

   Checking audio...
   Live Caption Relay -- audio chain check
   image: live-caption-relay   PULSE_SERVER=<unset, using /dev/snd>

     PASS  image 'live-caption-relay' is built
     PASS  PortAudio input devices: USB Audio CODEC: - (hw:1,0) default
     PASS  one second of audio captured (rms 0.00218342)
     PASS  signal is non-silent (mic permission is granted)

   All checks passed. Pick 'ALSA: pulse' (or your card) in the operator panel.
   ```

   If a check fails, start shows the `FAIL` lines and asks
   `Start anyway? [y/N]`. Type `n` and press `Enter` to stop and fix the
   problem. See [Platform notes](#linux-notes).

3. Wait while Relay starts. You should see:

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

     The relay runs in the background. Stop it with ./stop.sh; follow its
     log with ./logs.sh.
   ```

   There are two web addresses:

   - The **Viewer link** starts with `http://` and has no `/admin`. This is
     the link you give to the room. It works on any phone, tablet or laptop on
     the same network.
   - The **Panel** starts with `https://` and ends in `/admin`. This is the
     operator panel, where you control Relay. Keep it to yourself.

4. Open the panel address in your browser. On Linux, start does not open it
   for you. The first time, the browser warns that the connection is not
   private. Compare the fingerprint, as described later in this
   guide, before you continue.

Start then returns you to the prompt. Relay runs in the background, inside
Docker, until you stop it. You can close the terminal; Relay keeps running.

::: note
The viewer and panel addresses use this computer's current IP address. The
address can change when you move to a different venue or network. Always use
the addresses that start prints on the day.
:::

::: note
If the IP address has changed since you last started Relay, start makes a new
certificate for the new address, and the fingerprint changes. The line
`Panel certificate SHA-256` always shows the current one. Compare the browser
with that line.
:::

If something goes wrong, start prints the error and stops. See
[Troubleshooting the installation](#install-troubleshooting).

## Stopping Relay

Stop Relay after every event.

1. In a terminal, type:

   ```bash
   cd ~/Relay
   ./stop.sh
   ```

   Docker prints a few lines as it stops and removes the container, and then
   you are back at the prompt.

Your settings and credentials stay in `~/Relay/docker-config` and are there
the next time you start.

::: tip
Stopping Relay is not the same as pressing **Stop** in the panel. The
panel's **Stop** button ends the paid OpenAI session but leaves Relay running.
`./stop.sh` shuts Relay down completely.
:::

## Viewing the logs

The **log** is the list of messages Relay writes while it runs. It is useful
when something is not working, or when someone helping you asks for it.

1. In a terminal, type:

   ```bash
   cd ~/Relay
   ./logs.sh
   ```

   You see the last 200 lines of the log. New lines appear as Relay writes
   them. For example:

   ```
   2026-09-23 18:02:11,512 INFO    relay  Viewer socket: 127.0.0.1:8000
   2026-09-23 18:02:11,513 INFO    relay  Panel socket: 127.0.0.1:8001 (loopback only, HTTPS via the front end)
   ```

2. Press `Ctrl`+`C` when you have finished. This stops showing the log. It
   does not stop Relay.

If Relay is not running, you see an error such as
`No such container: live-caption-relay`.

## Running setup again

Run setup again when you want to:

- change the API key or the admin token;
- add, change or remove the hostname;
- check or renew the panel certificate.

1. Stop Relay with `./stop.sh`.
2. Run `./setup.sh`.

   Setup builds the image again (much faster this time), then asks:

   ```
   docker-config/config.json already exists.
   Reconfigure the API key and admin token? [y/N]
   ```

3. Type `y` and press `Enter` to enter a new API key, admin token and
   hostname. All three questions are asked again, and your other settings are
   kept.

   Or just press `Enter` to keep your credentials. Setup then skips straight
   to the certificate.

4. Setup prints the certificate details and fingerprint. If the line about
   the certificate says `Reusing config/certs/admin.crt…`, your certificate
   is still good and the fingerprint has not changed. If it says
   `Wrote config/certs/admin.crt…`, setup made a new certificate. Write down
   the new fingerprint.
5. Start Relay again with `./start.sh`.

The certificate lasts 397 days. Setup and start only replace it when it has
fewer than 30 days left, or when it does not cover this computer's current
address or hostname. You do not normally need to do anything to renew it.

::: tip
To force a brand-new certificate (for example, if you think someone copied
it), stop Relay, delete the `certs` folder inside `~/Relay/docker-config`,
and run setup again. Only delete `certs`, not the whole `docker-config`
folder.
:::

You can also change the API key and admin token from the operator panel,
without running setup.

## Platform notes {#linux-notes}

### The docker group

If setup or start says:

```
  Cannot talk to Docker.
  Start the daemon (sudo systemctl start docker), and if this is a
  fresh install add yourself to the docker group:
      sudo usermod -aG docker "yourname"   # then log out and back in
```

then either Docker is not running, or your user is not in the `docker` group
yet.

1. Start Docker: `sudo systemctl start docker`.
2. Check your groups by typing `groups`. If `docker` is not in the list, run
   `sudo usermod -aG docker $USER`, then log out and back in.

Do not run the Relay scripts with `sudo`. They are meant to run as your own
user, so the files in `docker-config` stay yours.

### "Permission denied" when running a script

If `./setup.sh` or `./start.sh` says `Permission denied`, the scripts have
lost their "executable" mark. This often happens with ZIP files. Fix it with:

```bash
cd ~/Relay
chmod +x *.sh tools/*.sh
```

### The sound card and the audio group

Relay hands the whole `/dev/snd` folder to the container. You can list the
capture devices Linux sees with `arecord -l` (from the `alsa-utils` package).

Inside the container, Relay joins the `audio` group so it may open the sound
card. On Debian and Ubuntu that group has the number 29, which is what Relay
expects. Some other distributions use a different number. If the audio check
fails with `FAIL  PortAudio input devices` or `FAIL  one second of audio
captured` even though `arecord -l` lists your device:

1. Find the group number that owns your sound card:

   ```bash
   stat -c %g /dev/snd/controlC0
   ```

   It prints a number, such as `63`.

2. Open `docker/docker-compose.linux.yml` in a text editor, and replace the
   word `audio` under `group_add:` with that number.
3. Run `./start.sh` again.

If another program, such as a video-call app or a recording program, is using
the sound card, Relay may not be able to open it. Close other audio programs
and try again.

### Firewall

If phones cannot open the viewer link, check that your firewall allows
incoming connections on ports 80 and 443.

## Rehearsal mode

**Rehearsal mode** shows made-up captions on the viewer pages. Use it to check
fonts, the projector and the link on every phone in the room. It does not
connect to OpenAI, so it costs nothing.

1. If Relay is running, stop it with `./stop.sh`.
2. Start Relay with `RELAY_DEMO=1` in front of the command:

   ```bash
   cd ~/Relay
   RELAY_DEMO=1 ./start.sh
   ```

   Start runs as usual and prints the same addresses.

3. Open the viewer link. You should see sample captions appear.

To check that rehearsal mode is on, run `./logs.sh`. You should see the line:

```
REHEARSAL MODE: captions are canned. No OpenAI session is open.
```

To leave rehearsal mode, run `./stop.sh`, then `./start.sh`. A normal start
never uses rehearsal mode.

::: warning
Relay stays in rehearsal mode until you stop it, even after a restart. Always
stop it after a rehearsal, then start it normally before the event.
:::
