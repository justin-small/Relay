# Downloading Relay {#download}

**GitHub** is a website where software projects keep their files. Relay lives
there, and you can download it for free without an account. Each tested
version of Relay is called a **release**.

1. Open <https://github.com/justin-small/Relay/releases/latest> in your web
   browser.

   You should see a page titled with the newest version, for example
   **Relay v3.0.0**, and a list of files under **Assets**.

2. Under **Assets**, click the installer for your computer. The number in the
   name is the version and may be higher for you.

   | Computer | File |
   |---|---|
   | Windows 10 or 11 | `Relay_3.0.0_x64-setup.exe` |
   | Mac with Apple silicon (M1, M2, M3, M4 or later) | `Relay_3.0.0_aarch64.dmg` |
   | Mac with an Intel processor | `Relay_3.0.0_x64.dmg` |
   | Linux desktop (Ubuntu, Debian, Fedora and others) | `Relay_3.0.0_amd64.AppImage` |
   | Ubuntu or Debian desktop, as a package | `Relay_3.0.0_amd64.deb` |
   | Linux with no screen (a server, a Raspberry Pi) | `Relay-v3.0.0.zip` |

   Your browser saves the file in your Downloads folder.

   The same list has the user guide you are reading, as a PDF and a Word file,
   always for the same version. Ignore the files that end in `.sig` and
   `latest.json`: Relay uses them to check its own updates.

3. Go on to the chapter for your computer: [macOS](#install-macos),
   [Windows](#install-windows) or [Linux](#install-linux).

::: tip
Not sure which Mac you have? Open the Apple menu and choose **About This Mac**.
If the **Chip** line says **Apple**, take `aarch64`. If there is a
**Processor** line that says **Intel**, take `x64`.
:::

## Where Relay keeps your settings

Relay keeps everything that is yours in one folder, called the **state
folder**: your OpenAI API key, settings, user accounts, blocked words,
recordings, backups and logs. It is in your own user folder, never next to the
app, so updating or reinstalling Relay does not touch it.

| Computer | State folder |
|---|---|
| macOS | `~/Library/Application Support/Relay` |
| Windows | `%APPDATA%\Relay` |
| Linux | `~/.local/share/relay` |

You can open it at any time from the Relay menu: **Open state folder**.

::: warning
Do not copy the state folder into a folder that syncs to the cloud, such as
OneDrive, iCloud Drive, Dropbox or Google Drive. It holds your OpenAI API key,
which should never leave this computer.
:::

## Updating Relay later

Relay tells you when a new version is out. See
[Updating the desktop app](#updating). You never need to download the
installer again for an update.

## Getting the Companion module

You need this only if you control Relay from **Bitfocus Companion**, the
software that runs Stream Deck buttons. Part 4 covers Companion. If you do not
use Companion, skip this section.

The module goes on the computer that runs Companion. That may be a different
computer from the one that runs Relay.

The module has its own GitHub page:

<https://github.com/justin-small/companion-module-relay-translation>

Each release of the module is one ready-made file, the **module package**.
Download it from the latest release:

<https://github.com/justin-small/companion-module-relay-translation/releases/latest>

Under **Assets**, click `relay-translation-1.0.0.tgz` (the number may be
higher). Do not unzip it: Companion takes the file as it is.

Companion 3 cannot load a module package, so there you need the source code
and must build the module yourself. Part 4 explains both.
