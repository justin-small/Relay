# Downloading from GitHub {#download}

**GitHub** is a website where software projects keep their files. Relay lives
there, and you can download it for free without an account:

<https://github.com/justin-small/Relay>

There are two ways to get it:

- **Download a ZIP file.** This is the easiest way. Choose it if you are not
  sure.
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

## Option A: Download the ZIP file

1. Open <https://github.com/justin-small/Relay> in your web browser.

   You should see the project page, with a list of files and folders such as
   `app`, `docker` and `tools`, and a README below them.

2. Click the green **Code** button above the list of files.

   A small menu opens.

3. Click **Download ZIP**.

   Your browser downloads a file called `Relay-main.zip` to your Downloads
   folder.

4. Unzip the file. The steps are different on each system. See the sections
   below.

5. Rename the unzipped folder from `Relay-main` to `Relay`, and move it to the
   place you chose in [Where to put the Relay folder](#where-to-put-the-relay-folder).

   Inside the folder you should see files such as `setup.command`,
   `setup.bat`, `setup.sh`, `start.command`, `start.bat` and `start.sh`.

### Unzipping on macOS

1. Open your **Downloads** folder in Finder.
2. Double-click `Relay-main.zip`.

   **Archive Utility** unzips it. A folder called `Relay-main` appears next to
   the ZIP file.

macOS marks files that come from the internet. The first time you open a
Relay script, macOS may refuse to open it. The macOS chapter explains what to
do.

### Unzipping on Windows

1. Open your **Downloads** folder in File Explorer.
2. Right-click `Relay-main.zip` and choose **Properties**.
3. If you see a checkbox called **Unblock** at the bottom of the **General**
   tab, tick it and click **OK**.

   This tells Windows that you trust the file. It means fewer warnings later.

4. Right-click `Relay-main.zip` again and choose **Extract All…**.
5. In the box that opens, type `C:\` as the destination and click **Extract**.

   You should see a new folder, `C:\Relay-main`. Rename it to `C:\Relay`.

::: warning
Always extract the ZIP first. If you double-click the ZIP file, Windows shows
the files inside it as if it were a folder. Scripts started from there do not
work, because Windows copies only that one file to a temporary place and
leaves the rest of Relay behind. Setup then fails with **The build failed**.
:::

### Unzipping on Linux

1. Open a terminal.
2. Unzip the file into your home folder and rename it:

   ```bash
   cd ~
   unzip ~/Downloads/Relay-main.zip
   mv Relay-main Relay
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

::: warning
On Windows, do not leave out `--config core.autocrlf=false`. Without it, Git
for Windows changes the line endings in Relay's Linux scripts, and start then
fails with errors such as `$'\r': command not found`.
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

**If you used the ZIP file:**

1. Stop Relay.
2. Download a fresh ZIP and unzip it, as in [Option A](#option-a-download-the-zip-file).
3. Copy the whole `docker-config` folder from your old Relay folder into the
   new one.
4. Rename the old folder (for example to `Relay-old`), and give the new folder
   the old name.
5. Start Relay. When everything works, you can delete `Relay-old`.

## Getting the Companion module

You need this only if you control Relay from **Bitfocus Companion**, the
software that runs Stream Deck buttons. Part 4 covers Companion. If you do not
use Companion, skip this section.

The module goes on the computer that runs Companion. That may be a different
computer from the one that runs Relay.

The module has its own GitHub page:

<https://github.com/justin-small/companion-module-relay-translation>

There are no ready-made releases yet, so you download the source code, in the
same two ways as Relay itself:

- **ZIP file:** open the page, click the green **Code** button, then
  **Download ZIP**. You get a file called
  `companion-module-relay-translation-main.zip`. Unzip it as described above.
- **git:**

  ```bash
  git clone https://github.com/justin-small/companion-module-relay-translation.git
  ```

Part 4 explains how to load the module into Companion.
