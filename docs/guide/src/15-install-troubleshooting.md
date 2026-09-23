# Troubleshooting the installation {#install-troubleshooting}

This chapter lists the problems people meet while installing and starting
Relay, and what to do about each one. Most messages below are exactly what the
scripts print. Find the message you see, then follow the advice next to it.

Two tools help with almost every problem:

- **The log.** Run `logs.command`, `logs.bat` or `./logs.sh`. It shows what
  Relay is doing and why it stopped.
- **Stop, then start.** Run the stop script, then the start script. This
  clears many problems, especially after a restart or a change of network.

## Downloading and opening the scripts

| Symptom | What to do |
|---|---|
| macOS: **"setup.command" cannot be opened because it is from an unidentified developer**, or **Apple could not verify "setup.command" is free of malware** | macOS blocks scripts downloaded from the internet. Open **System Settings → Privacy & Security** and click **Open Anyway**. See the macOS chapter's platform notes. |
| Windows: **Windows protected your PC** | This is SmartScreen. Click **More info**, then **Run anyway**. |
| Windows: setup fails with `The build failed - see the output above.`, and the output mentions `no such file or directory` | You probably ran the script from inside the ZIP file. Extract the ZIP first (**Extract All…**), then run the script from the extracted folder. |
| Linux: `Permission denied` when you run `./setup.sh` or `./start.sh` | The scripts lost their executable mark. Run `chmod +x *.sh tools/*.sh` in the Relay folder. |
| Windows: errors such as `$'\r': command not found`, `/usr/bin/env: 'bash\r': No such file or directory`, or the relay not coming up with `exec /usr/local/bin/docker-entrypoint.sh: no such file or directory` in the log | Git for Windows changed the line endings in Relay's Linux scripts. Delete the Relay folder (keep a copy of `docker-config` if you have one) and download it again as a ZIP, or clone again with `git clone --config core.autocrlf=false …`. |
| Windows: `The system cannot find the batch label specified` | The `.bat` file may have the wrong line endings. Download a fresh ZIP. If it happens again, report it on the Relay GitHub page. |
| Windows: a script fails in an odd way, and your Relay folder path contains an apostrophe or one of `!` `&` `%` `^` | Move the Relay folder to `C:\Relay` and try again. |

## Docker

| Symptom | What to do |
|---|---|
| `Docker is not installed.` | Install Docker Desktop (macOS, Windows) or Docker Engine (Linux), then run setup again. |
| `Docker is installed but not running.` or `Docker is not running. Start Docker Desktop and try again.` | Open Docker Desktop and wait until it says **Engine running**. Then run the script again. |
| Linux: `Cannot talk to Docker.` | Start Docker with `sudo systemctl start docker`. If you have just installed Docker, add yourself to the `docker` group with `sudo usermod -aG docker $USER`, then log out and back in. |
| Windows: `Docker is not available inside WSL.` | Open Docker Desktop → **Settings → Resources → WSL integration**. Tick **Enable integration with my default WSL distro** and turn on **Ubuntu**. Click **Apply & restart**. Check that Ubuntu is your default WSL system (`wsl -l -v` shows a `*` next to it). |
| Windows: `Cannot talk to WSL.` | WSL is missing or broken. Open Terminal as administrator and run `wsl --install`, then `wsl --update`. Restart, open Docker Desktop, and try again. |
| Windows: Docker Desktop says virtualisation is not enabled or not supported | Virtualisation is turned off in the PC's firmware (BIOS or UEFI). Ask your IT department to turn on **Intel VT-x** or **AMD-V** (sometimes called **SVM**). |
| Linux: `docker: 'compose' is not a docker command` | The Compose plugin is missing. Install the `docker-compose-plugin` package, following Docker's instructions for your distribution. |
| `The build failed — see the output above.` | Scroll up and read the first error. The usual causes are no internet connection, a full disk, or Docker not running. Fix the cause and run setup again. |
| Docker says `no space left on device` | The disk is full. Free some space. In Docker Desktop you can also remove old images under **Images**. |

## Setup

| Symptom | What to do |
|---|---|
| `The API key cannot be empty.` | You pressed `Return` without pasting the key. Paste it and press `Return`. Nothing appears on screen while you paste; that is normal. |
| `Too short — use at least 8 characters.` | Choose a longer admin token. |
| `That token is guessable. Pick another.` | `changeme`, `password`, `admin` and `relay` are refused. Choose a real password. |
| `Tokens did not match — try again.` | The token and its confirmation were different. Type it carefully twice. |
| `Could not write docker-config/config.json — see the output above.` | Read the lines above it. Check that the Relay folder is not read-only and not in a cloud-synced folder, then run setup again. |
| `Could not generate the certificate — see the output above.` | Read the lines above it. Run setup again. If it keeps failing, send the output to whoever supports your Relay installation. |
| Windows: the admin token does not work in the panel, even though you typed it correctly | The token probably contains a symbol that Command Prompt changed, such as `!` or `%`. Run `setup.bat` again, answer `y`, and choose a token made of letters, numbers, `-`, `_` and `.` only. |
| Windows: you answered `yes` to **Reconfigure the API key and admin token?** but setup skipped the questions | On Windows only a single `y` counts. Run setup again and type `y`. |
| `Setup did not finish.` (Windows) | One of the steps above failed. Read the message just above this line, and look it up in this chapter. |
| Setup says `Certifying this machine's LAN address, 127.0.0.1.` | The computer was not connected to a network. That is fine for now: start checks the address again every time. |

## Starting Relay

| Symptom | What to do |
|---|---|
| `Not set up yet — docker-config/config.json is missing.` | Run setup first. If you have just updated Relay from a ZIP file, you forgot to copy the old `docker-config` folder into the new Relay folder. Copy it across, or run setup again. |
| macOS: `PulseAudio is not installed.` | Install it with `brew install pulseaudio`. See the macOS chapter. |
| macOS: `PulseAudio did not come up. Log: …` | The lines below the message show why. Run `stop.command`, then `start.command`. If it still fails, restart the Mac and try again. |
| Windows: `WSLg is not available (no /mnt/wslg/PulseServer).` | Relay needs Windows 11. On Windows 11, run `wsl --update` in Terminal, then `wsl --shutdown`, and try again. |
| Windows: `Could not map "C:\…" to a WSL path.` | WSL could not reach the Relay folder. Check that Ubuntu is installed and is your default WSL system, and that the folder is on a local drive, such as `C:\Relay`. |
| Linux: `/dev/snd does not exist, so there is no sound card to hand in.` | Linux cannot see a sound card. Plug in your audio interface. Check with `arecord -l`. |
| `Audio checks failed -- see the FAIL lines above.` | Read the `FAIL` lines, and look them up in the next table. |
| `The relay did not come up (container state: …).` followed by `Last lines of its log:` | Relay started but then stopped. The log lines below the message show why. Common causes are in this table; if you cannot tell, send those lines to whoever supports your Relay installation. |
| Docker says `port is already allocated` or `address already in use` | Another program is already using port 80 or 443 on this computer, such as another web server. Stop that program, then run start again. |
| The start window closed before you could read it | It closes after 30 seconds only when Relay started correctly. Run the logs script to see what Relay printed. |
| The addresses start prints use `127.0.0.1` | Start could not find this computer's network address. Check that it is connected to the venue network, then stop and start again. On a Mac, start only looks at the Wi-Fi and built-in Ethernet ports; a USB or Thunderbolt Ethernet adapter is not found. Use Wi-Fi, or ask for help setting `RELAY_ADMIN_IPS`. |

## Audio check failures

These lines come from the audio check that start runs. Each `FAIL` line is
followed by a line starting `->` with a hint.

| Symptom | What to do |
|---|---|
| `FAIL  image 'live-caption-relay' is built` | Relay has not been built. Run setup first. |
| `FAIL  PulseAudio server reachable at …` (macOS, Windows) | The sound bridge is not running. On a Mac, run `stop.command` and then `start.command`. On Windows, run `wsl --shutdown` in Terminal, then `start.bat` again. |
| `FAIL  PortAudio input devices` | Relay cannot see any microphone. Check that the microphone is plugged in. On Linux, see the note about the audio group in the Linux chapter. |
| `FAIL  one second of audio captured` | Relay sees a device but cannot record from it. Plug the microphone in again, close other programs that use it, and run start again. On a Mac, make sure no other copy of PulseAudio is running: run `stop.command` first. |
| `FAIL  signal is non-silent`, with `digital silence` on the next line | Relay records, but the sound is pure silence. This is almost always microphone permission. **macOS:** turn on **Terminal** in **System Settings → Privacy & Security → Microphone**, then run `stop.command` and `start.command`. **Windows:** in **Settings → Privacy & security → Microphone**, turn on **Let apps access your microphone** and **Let desktop apps access your microphone**. |
| macOS: audio worked yesterday, but not after a restart | Run `start.command` again. After a restart, Docker brings Relay back but not PulseAudio. |

## Opening the panel and the viewer link

| Symptom | What to do |
|---|---|
| The browser warns **Your connection is not private** (or similar) the first time you open the panel | This is expected. The certificate was made on your own computer. Compare the fingerprint the browser shows with the `SHA-256` line from start. If they match, continue. If they do not, stop and find out why. |
| The fingerprint in the browser does not match what setup printed | If this computer's IP address has changed, start made a new certificate. Compare with the `Panel certificate SHA-256` line that start printed today. If that does not match either, do not continue. |
| `http://192.168.1.50/admin` shows **404 Not Found** | The panel only works over HTTPS. Type `https://`, not `http://`. |
| Phones cannot open the viewer link | Check that the phones are on the same Wi-Fi network as the Relay computer, not a guest network or mobile data. Guest networks often stop devices from seeing each other. Check the computer's firewall (on Windows, allow Docker on **Private networks**). Make sure people type `http://`, not `https://`. |
| The panel will not accept your admin token | Check for extra spaces and the right capital letters. If you have forgotten it, run setup again and answer `y` to choose a new one. |

## Once Relay is running

These problems appear in the operator panel rather than during installation.
Later chapters explain the panel in detail.

| Symptom | What to do |
|---|---|
| `Incorrect API key provided` | The API key is wrong. Relay stops rather than retrying. Paste the right key in the panel (or run setup again) and press **Start capture** again. |
| Sessions say `reconnecting`, and the count climbs | A network problem. Relay reconnects by itself, waiting a little longer each time. Check the computer's internet connection. |
| The meter is flat while someone speaks | Wrong device or wrong channel. Try each channel, or **Mix** on a stereo feed. Press **Rescan** if you plugged the interface in after starting Relay. |
| `No input device matched` | The audio interface was unplugged. Plug it in, choose it again and press **Start capture**. |
| Viewers see **No target language is live** | The language is turned off, or capture is stopped. |
| The CLIP indicator keeps lighting | The sound is too loud. Turn the gain down at the mixing desk or interface. |
| The meter moves but stays far to the left | The sound is too quiet. Turn the gain up until the loudest speech reaches the marked band. |
