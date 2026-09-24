# Downloading from GitHub {#download}

**GitHub** is a website where software projects keep their files. Relay lives
there, and you can download it for free without an account:

<https://github.com/justin-small/Relay>

There are two ways to get it:

- **Download the latest release.** This is the easiest way. Choose it if you
  are not sure. A **release** is a tested version of Relay, packed into one
  ZIP file.
- **Clone it with git.** Git is a tool that copies the project and can update
  it later with one command. Choose it if you already use git, or if you will
  update Relay often.

Both ways give you the same folder of files.

## Where to put the Relay folder

Put the Relay folder somewhere simple, in your own user folder, and keep it
there. Good choices are:

| Computer | Suggested folder |
|---|---|
| macOS | `/Users/yourname/Relay` (your home folder, then `Relay`) |
| Windows | `C:\Relay` |
| Linux | `/home/yourname/Relay` |

A path without spaces or symbols is the safest. The Relay scripts do cope with
spaces in the folder path on all three systems. On Windows, though, some
symbols in the path break the scripts: an apostrophe (as in `O'Brien`), and
the characters `!`, `&`, `%` and `^`. If your Windows user name has one of
these, `C:\Relay` avoids the problem.

::: warning
Do not put the Relay folder in a folder that syncs to the cloud, such as
OneDrive, iCloud Drive, Dropbox or Google Drive. On many computers the Desktop
and Documents folders sync to the cloud without you noticing. Relay keeps your
OpenAI API key and admin token in its folder, and they should never leave this
computer.
:::

## Option A: Download the latest release

1. Open <https://github.com/justin-small/Relay/releases/latest> in your web
   browser.

   You should see a page titled with the newest version, for example
   **Relay v1.0.0**, and a list of files under **Assets**.

2. Under **Assets**, click `Relay-v1.0.0.zip`. The number is the version and
   may be higher for you.

   Your browser downloads the file to your Downloads folder. Ignore the two
   **Source code** files: they unzip to a folder with a different name.

   The same list has the user guide you are reading, as a PDF and a Word
   file, always for the same version.

3. Unzip the file. The steps are different on each system. See the sections
   below.

4. Move the unzipped `Relay` folder to the place you chose in
   [Where to put the Relay folder](#where-to-put-the-relay-folder).

   Inside the folder you should see files such as `setup.command`,
   `setup.bat`, `setup.sh`, `start.command`, `start.bat` and `start.sh`.

### Unzipping on macOS

1. Open your **Downloads** folder in Finder.
2. Double-click `Relay-v1.0.0.zip`.

   **Archive Utility** unzips it. A folder called `Relay` appears next to the
   ZIP file.

macOS marks files that come from the internet. The first time you open a
Relay script, macOS may refuse to open it. The macOS chapter explains what to
do.

### Unzipping on Windows

1. Open your **Downloads** folder in File Explorer.
2. Right-click `Relay-v1.0.0.zip` and choose **Properties**.
3. If you see a checkbox called **Unblock** at the bottom of the **General**
   tab, tick it and click **OK**.

   This tells Windows that you trust the file. It means fewer warnings later.

4. Right-click `Relay-v1.0.0.zip` again and choose **Extract All…**.
5. In the box that opens, type `C:\` as the destination and click **Extract**.

   You should see a new folder, `C:\Relay`.

::: warning
Always extract the ZIP first. If you double-click the ZIP file, Windows shows
the files inside it as if it were a folder. Scripts started from there do not
work, because Windows copies only that one file to a temporary place and
leaves the rest of Relay behind. Setup then fails with **The build failed**.
:::

### Unzipping on Linux

1. Open a terminal.
2. Unzip the file into your home folder:

   ```bash
   cd ~
   unzip ~/Downloads/Relay-v1.0.0.zip
   cd Relay
   ```

3. Make the scripts executable:

   ```bash
   chmod +x *.sh tools/*.sh
   ```

   Some unzip tools drop the "executable" mark on script files. Without it,
   the scripts fail with `Permission denied`. Running `chmod` again is always
   safe.

## Option B: Clone with git

### Install git

**macOS.** Open **Terminal** (in **Applications → Utilities**) and type:

```bash
git --version
```

If git is not installed, macOS asks whether you want to install the
**command line developer tools**. Click **Install** and wait for it to finish.
Then run `git --version` again. You should see a line such as
`git version 2.39.5`.

**Windows.** Download **Git for Windows** from <https://git-scm.com/downloads>
and run the installer. The default choices are fine. When it finishes, open
**Command Prompt** and type `git --version`. You should see a line such as
`git version 2.47.0.windows.1`.

**Linux.** Install git with your package manager:

```bash
sudo apt install git     # Debian, Ubuntu
sudo dnf install git     # Fedora
```

### Clone Relay

On **macOS and Linux**, open a terminal and type:

```bash
cd ~
git clone https://github.com/justin-small/Relay.git
cd Relay
```

On **Windows**, open **Command Prompt** and type:

```bat
cd /d C:\
git clone --config core.autocrlf=false https://github.com/justin-small/Relay.git
cd Relay
```

You should see git print a few lines ending in `done.` A new folder called
`Relay` now holds the project.

::: note
On Windows, `--config core.autocrlf=false` stops Git for Windows from changing
the line endings in Relay's files. Relay pins the line endings of its scripts
itself, so this is a second safeguard. Keep it in.
:::

## Updating Relay later

Your settings, API key, admin token, certificate and recordings are all kept
in one folder inside Relay: `docker-config`. Nothing else in the Relay folder
holds anything of yours. Updating Relay means replacing everything **except**
that folder.

You do not need to run setup again after an update. The next time you start
Relay, the start script rebuilds it with the new files. This takes a few
minutes longer than usual.

::: warning
An update keeps your credentials only if you keep the `docker-config` folder.
If you delete it, or download a fresh copy and forget to copy it across, you
have to run setup again and type your API key and admin token again. The
certificate fingerprint also changes.
:::

**If you used git:**

1. Stop Relay.
2. Open a terminal (or Command Prompt on Windows) in the Relay folder.
3. Type:

   ```bash
   git pull
   ```

   You should see a list of changed files, or `Already up to date.`
   Git never touches `docker-config`.

4. Start Relay.

**If you downloaded a release:**

1. Stop Relay.
2. Rename the old Relay folder, for example to `Relay-old`, so the new one
   does not unzip on top of it.
3. Download the latest release and unzip it, as in
   [Option A](#option-a-download-the-latest-release). You now have a new
   `Relay` folder next to `Relay-old`.
4. Copy the whole `docker-config` folder from `Relay-old` into the new
   `Relay` folder.
5. Start Relay. When everything works, you can delete `Relay-old`.

With git, `git pull` gets the newest changes, which can be newer than the
latest release.

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
