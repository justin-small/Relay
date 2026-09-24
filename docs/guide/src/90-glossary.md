# Glossary {#glossary}

**Admin token**
: The password for the operator panel. You choose it during setup. Companion
  uses it too.

**API key**
: A secret code, like a password, that lets Relay use OpenAI. Everything done
  with it is billed to your OpenAI account.

**Armed**
: A target language that is switched on while capture is stopped. It goes
  live when you press **Start capture**.

**Auto-recharge**
: An OpenAI setting that charges your card automatically when your credit
  runs low.

**Bitfocus Companion**
: Free software that turns a Stream Deck or similar button panel into a
  control surface for many kinds of equipment, including Relay.

**Capture**
: Relay listening to the sound source and sending it to OpenAI. It runs from
  **Start capture** to **Stop**, and is billed for that whole time.

**Certificate**
: A small file that proves to a browser which computer it is talking to.
  It makes the `https://` connection to the operator panel possible.

**Clipping**
: Sound so loud that its peaks are cut off. It makes speech hard to
  recognise. The **CLIP** light in the panel shows it.

**Clone**
: A copy of a project made with git. You can update it later with one
  command.

**Command Prompt**
: The Windows window where you type commands.

**Connection (Companion)**
: One device or program that Companion controls, with its own settings.

**Container**
: A sealed box that Relay runs in, managed by Docker. It keeps Relay separate
  from the rest of the computer.

**dBFS**
: Decibels below full scale: how loud a digital sound is. 0 is the loudest
  possible. Lower numbers, such as −18 or −40, are quieter.

**Developer modules path**
: A Companion setting that points to a folder of modules you add yourself,
  such as the Relay module.

**Docker**
: Free software that runs programs inside containers. Relay needs it.

**Docker Desktop**
: The Docker app for macOS and Windows.

**Docker Engine**
: Docker for Linux, without the desktop app.

**`docker-config`**
: The folder inside the Relay folder that holds all your settings: API key,
  admin token, certificate, blocked words and recordings.

**docker group**
: The Linux user group allowed to run Docker without typing `sudo`.

**Enabled**
: A target language whose switch is on. It is **armed** while capture is
  stopped and **live** while capture runs.

**Environment variable**
: A named setting, such as `RELAY_DEMO`, given to a program when it starts.

**Feedback**
: In Companion, a rule that changes how a button looks, for example green
  while capture runs.

**Fingerprint (SHA-256)**
: A long code that is unique to one certificate, such as `D0:63:EE:F2:…`.
  Comparing it with the one Relay printed proves you reached your own Relay
  computer.

**GitHub**
: The website where Relay's files are published.

**Hard spend limit**
: An OpenAI setting that stops requests once your monthly spending reaches
  an amount you choose.

**Homebrew**
: A free tool for installing programs on a Mac.

**Hostname**
: A name for a computer, such as `relay.local`, that can be used instead of
  its IP address.

**HTTPS**
: The secure, encrypted kind of web address, starting `https://`. The
  operator panel uses it.

**IP address, LAN IP address**
: The number that identifies a computer on a network, such as
  `192.168.1.50`. The LAN IP address is the one on the local network.

**Kiosk display**
: A screen nobody touches, such as a lobby TV. Relay's **Screen** link is
  made for it.

**LAN**
: Local area network: the network at the venue, wired or Wi-Fi.

**Live**
: A target language with an open connection to OpenAI, being translated and
  billed right now.

**Log**
: The messages Relay writes while it runs. Open them with `logs.*`.

**LTS (long-term support)**
: The stable version of Node.js that most people should install.

**Module**
: An add-on that teaches Companion how to control one kind of equipment.

**Module package (.tgz)**
: A whole Companion module packed into one compressed file, added with
  **Import module package**.

**Node.js, npm**
: Node.js runs JavaScript programs outside a browser. npm installs the parts
  they need. Needed only to build the Companion module yourself, for
  Companion 3.

**OpenAI Platform**
: OpenAI's pay-as-you-go service for programs, at platform.openai.com. It is
  separate from a ChatGPT subscription.

**Operator panel**
: The web page where you control Relay, at `https://<address>/admin`.

**Organization**
: The OpenAI account that owns the billing, projects and keys.

**Organization verification**
: A one-time identity check that OpenAI requires for some models.

**Port**
: A numbered door on a computer that a program listens on. Relay uses 80 for
  viewers and 443 for the operator panel.

**Prepaid credit**
: Money paid to OpenAI in advance and used up as Relay runs.

**Preset**
: In Companion, a ready-made button you drag onto a page.

**Project**
: A folder inside an OpenAI organization with its own keys, usage and limits.

**PulseAudio**
: A sound program. On a Mac or a Windows PC, it carries the microphone into
  Relay's container.

**QR code**
: A square barcode that a phone camera turns into a link.

**Rate limit, usage tier**
: How much you may use OpenAI per minute. The tier rises with the total you
  have paid, and sets the rate limit.

**Rehearsal mode**
: Relay showing made-up captions, without connecting to OpenAI, at no cost.

**Release**
: A tested version of Relay or of the Companion module, published on GitHub
  as ready-made files. The newest one is called the latest release.

**Restricted modules**
: A Companion 5 setting, under **Dangerous Features**, that allows importing
  module packages from another computer's browser.

**Revoke, rotate**
: Revoking a key deletes it so it stops working. Rotating means making a new
  key and revoking the old one.

**Run**
: One recording: everything from **Start capture** to **Stop**.

**Self-signed certificate**
: A certificate made on your own computer rather than bought from a company
  that browsers trust. Browsers warn about it the first time.

**Session**
: One open connection to OpenAI. Relay opens one per live target language.

**SmartScreen, Gatekeeper**
: The Windows and macOS warnings about files downloaded from the internet.

**Source language**
: The language being spoken. Relay's is English.

**Spend alert**
: An OpenAI email sent when spending reaches an amount. It does not stop
  anything.

**Stream Deck**
: A small panel of buttons with screens on them, made by Elgato, often used
  with Companion.

**Target language**
: A language Relay translates into.

**Terminal**
: The macOS and Linux window where you type commands.

**Transcription**
: The words as spoken, written out in the same language.

**Variable**
: In Companion, a value from Relay, such as the session clock, that can be
  shown on a button.

**Viewer link, viewer pages**
: The plain `http://` address you give the room, and the caption pages it
  opens.

**WSL**
: Windows Subsystem for Linux, which lets Windows run Linux programs. Docker
  Desktop runs on it.

**ZIP file**
: One compressed file holding a whole folder of files.
