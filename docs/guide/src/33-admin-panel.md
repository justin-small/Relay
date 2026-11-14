# The operator panel {#admin-panel}

The operator panel has a **menu down the left side**. Click an item to open
its page. The panel remembers the page you were on. At the top, a coloured
pill shows whether capture is running, and **Start capture** and **Stop** are
on the **Session health** page. On a phone or a narrow window, the menu is
behind a **Menu** button.

## Overview

![The operator panel on its first page, Session health, with capture stopped.](images/admin-overview.png){width=100%}

1. **Status pill.** Red and **STOPPED** while capture is off, green and
   **RUNNING** while it runs. Click it to go to **Session health**.
2. **Live.** The pages you use during a session: **Session health**,
   **Audio input** and **Target languages**.
3. **Output.** Where the captions go: **Panels**, **Alerts**, **Viewer
   links** and **Recordings & export**.
4. **Automation.** **Schedules** and **Planning Center**.
5. **Settings.** **Blocked words**, **Credentials**, **Appearance &
   tuning** and **Users**.
6. **Start capture.** See [Starting and stopping](#start-stop).
7. **Viewers.** How many people have a viewer page open, next to **Sign
   out**.

Each page is described in this chapter, except **Panels**, **Alerts** and
**Planning Center**, which are in Part 5.

When something goes wrong, a red message appears at the top of the page.
Green messages, such as **Saved.**, confirm that a change worked and fade after
a couple of seconds.

### A session in progress

This is the panel during a session, with two languages live.

![The operator panel while capture is running (test data).](images/admin-running.png){width=100%}

1. The status pill is **green** and says **RUNNING**.
2. **37 viewers**: people with a viewer page open right now. This number can
   lag a little behind.
3. **Sessions** shows one row per language, both **connected**.
4. The stream control says **Running** and shows when it started.

## Starting and stopping {#start-stop}

![Stream control while capture is running.](images/admin-stream-control.png){width=100%}

1. **Start capture** begins listening and opens one connection to OpenAI for
   each target language that is switched on. It is greyed out while capture
   runs.
2. **Stop** ends capture and closes every connection. Billing stops at once.
   It is greyed out while capture is stopped.
3. **The state.** **Stopped** or **Running**. When schedules are set up, it
   also says what happens next, for example **Stopped · next Wed 09/23/2026
   8:00 PM CDT**, or which schedule started the session and when it will
   stop.

### To start a session

1. Check that at least one language is switched on in **Target languages**.
   If none is, nothing appears on the viewer pages.
2. Click **Start capture**.
3. You should see the status pill turn green and the state change to
   **Running**.
4. Within a few seconds, **Session health** should show one row per language,
   each **connected**.
5. Speak, or ask someone to. The level meter should move and captions should
   appear on the viewer pages.

### To end a session

1. Click **Stop**.
2. You should see the pill turn red and the state change to **Stopped**.
   **Session health** says **Not started.**

::: warning
**Press Stop between sessions.** Each language is billed per minute for as
long as capture runs, including silence. A session left running over lunch
costs the same as one full of speech.
:::

### If Start capture shows an error

![A start that failed because the chosen audio device is missing.](images/admin-start-error.png){width=100%}

1. The red line says why capture did not start. Common messages:

| Message | What to do |
|---|---|
| **No OpenAI API key set. Add one in the admin panel.** | Add the key in [Credentials](#credentials), then click **Start capture** again. |
| **No input device matched the saved selection.** | The sound device was unplugged or renamed. Plug it in, click **Rescan** in **Audio input**, choose it again, and click **Start capture**. |

## Session health {#session-health}

Session health shows how each connection to OpenAI is doing. There is one row
for each target language while capture runs. When capture is stopped it says
**Not started.**

![Session health with two healthy sessions (test data).](images/admin-session-health.png){width=100%}

1. **Session**: which language this connection is for, for example
   `translate:SPANISH`. Each one carries both the English transcript and that
   language's translation.
2. **State**: how the connection is doing. See the table below.
3. **Last delta**: how long ago the last piece of text arrived. **now** or a
   few seconds during speech is healthy. During silence it grows, and that is
   normal.
4. **Retries**: how many times the connection has dropped and reconnected in
   this session. Zero is ideal. A few over a long event is normal.
5. **Audio dropped**: pieces of sound that could not be sent because the
   connection was too slow. It should stay at **0**. It turns red if not.

### What each state means

| State | Colour | Meaning | What to do |
|---|---|---|---|
| **connecting** | Yellow | Opening the connection. | Wait a few seconds. |
| **connected** | Green | Working. | Nothing. |
| **reconnecting** | Yellow | The connection dropped. Relay is reopening it by itself. | Wait. If it keeps happening, check the internet connection. |
| **error** | Red | The connection failed and Relay has stopped trying. The reason is shown next to it. | Read the reason, fix it, then **Stop** and **Start capture**. |

### When a state turns bad

![Session health with one session reconnecting and one failed (test data).](images/admin-session-health-problem.png){width=100%}

1. **reconnecting**, with **Retries** climbing and **Last delta** growing:
   the internet connection is unreliable. Relay keeps retrying, waiting a
   little longer each time. Check the Relay computer's network. A wired
   connection is more reliable than Wi-Fi. If the network is fine and every
   session keeps reconnecting from the moment you start, check the OpenAI
   billing page: a spending limit you set may have been reached.
2. **error** with a reason. Relay does not retry some errors, because
   retrying cannot fix them. The most common:

| Reason shown | What it means | What to do |
|---|---|---|
| **Incorrect API key provided** | The key is wrong or was deleted. | Put the right key in [Credentials](#credentials), then **Stop** and **Start capture**. |
| **You exceeded your current quota** or a billing message | The OpenAI account is out of credit, or hit its spending limit. | Add credit or raise the limit on the OpenAI website. See Part 2. |
| A message about **model** access | The account cannot use the model Relay needs. | See Part 2, "Check the account can use Relay's model". |

::: tip
If **Audio dropped** climbs, the upload from the Relay computer is too slow.
Close other programs that use the internet, such as video calls or cloud
backups, or move to a wired connection.
:::

## Audio input {#audio-input}

This page chooses where the sound comes from, and shows how loud it is.

![Audio input while capture is running (test data).](images/admin-audio-input.png){width=100%}

1. **Capture device.** The sound input to use: an audio interface, a USB
   microphone, or the computer's own microphone. Each entry shows how many
   channels it has and its sample rate. If the list is wrong or empty, click
   **Rescan** (3).
2. **Channel.** Which input on the device to use. **Channel 1**,
   **Channel 2** and so on pick one input. **Mix** combines all of them. For a
   stereo feed from the sound desk, **Mix** is usually right. If the meter
   stays flat while someone talks, you have picked the wrong channel: try the
   others.
3. **Rescan.** Looks for sound devices again. Use it after you plug
   something in. While capture runs, it cannot see new devices: click **Stop**
   first, then **Rescan**.
4. **Noise reduction.** Cleans up background noise before the sound goes to
   OpenAI. Match it to your source:
   - **Board feed / line in**: a feed from the sound desk. The default.
     The desk has already cleaned the sound, so Relay adds nothing.
   - **Room or laptop mic**: a microphone some distance from the speaker.
   - **Headset / lavalier**: a microphone worn by the speaker.

   Choosing the wrong one can cut off the start of words.
5. **Status.** **Idle** when stopped. **Speaking** (green dot) when Relay
   hears speech. **Signal quiet** (yellow dot) when the input is silent.
   **Error** (red dot) when the device failed.
6. **Level meter.** How loud the input is, from −60 (silent, left) to 0
   (maximum, right). The bar shows the average level. The thin white line
   shows the recent peak. The shaded band near **−18** is the target: aim the
   loudest speech there.
7. **CLIP.** Lights red for three seconds whenever the sound is too loud and
   gets cut off. Watch this light, not only the bar: short, loud sounds are
   easy to miss on the bar.
8. **Reading.** The average and peak level in numbers, and how many samples
   have clipped since you last pressed **Start capture**.

Under the meter, a line names the device and channel in use.

Changing the device, the channel or noise reduction saves at once and shows
**Input saved.** If capture is running, the connections restart, and
captions pause for a few seconds.

### Setting the level

1. Choose the device and channel.
2. Click **Start capture**. Ask someone to speak at their normal loudest.
3. You should see the bar move and the status say **Speaking**.
4. Adjust the gain on the sound desk or audio interface, not in Relay, until
   the loudest speech reaches the **−18** band and **CLIP** stays dark.

![The meter when the input is too loud: the bar turns red and CLIP lights.](images/admin-audio-clipping.png){width=85%}

1. The bar turns amber above −6 and red when clipping.
2. **CLIP** is lit.
3. The reading counts clipped samples.

If you see this, turn the gain **down** at the desk. Clipped sound
transcribes badly, and no setting in Relay can repair it. If the bar sits far
to the left, below about −40, the input is too quiet: turn the gain up.

::: note
The meter only moves while capture is running. When capture is stopped, it
shows nothing even if a microphone is connected.
:::

## Target languages {#target-languages}

This page chooses which languages Relay translates into. There are 17:
Arabic, Chinese, French, German, Hindi, Indonesian, Italian, Japanese,
Korean, Nepali, Portuguese, Russian, Spanish, Telugu, Vietnamese, Hmong and
English (English only when the speaker is not speaking English). Arabic,
Nepali, Telugu and Hmong need the **Text** engine, described below.

![Target languages with capture stopped: two languages armed.](images/admin-targets-stopped.png){width=90%}

1. **Translation engine.** **Realtime** translates word by word and bills
   each language per minute. **Text** transcribes once and translates each
   finished line, so extra languages cost almost nothing. In Text mode,
   captions arrive a line at a time, a second or two behind. Changing it
   restarts live sessions.
2. **Select all** and **Clear** switch every language on or off at once.
3. **Count.** How many languages are enabled.
4. **Tick box.** Click to turn a language on or off. The change is saved at
   once.
5. **State** of each language:
   - **off**: not used.
   - **armed — starts with capture**: switched on, but capture is stopped, so
     nothing is being translated yet. It goes live when you click **Start
     capture**.
   - **live**: being translated now.

![Target languages while capture runs: the same two languages are live.](images/admin-targets-live.png){width=90%}

You can switch languages while capture is running:

- **Turning one on** opens its connection straight away. It appears in the
  viewers' language picker within a few seconds.
- **Turning one off** closes it at once. Viewers reading that language see
  their captions stop.

### What each language costs

In **Realtime** mode, each live language is its own connection to OpenAI,
billed per minute for as long as capture runs, speech or silence. (In **Text**
mode there is one connection and extra languages add only a little.) Two languages cost twice as much as
one. The English transcript comes with each connection. Viewers cost nothing:
a hundred phones cost the same as one.

As a rough guide: at the price published in September 2026, one language
costs about **$2 an hour**. Three languages for a two-hour event cost about
**$12**, possibly a little more. Part 2, "Understanding the cost", shows how
to work it out and where to check current prices.

::: warning
At least one language must be on for **anything** to appear, including the
English transcript. With every language off, the panel says so and the viewer
pages stay empty.
:::

::: note
Spanish comes out in a neutral Latin American style. There is no setting for
dialect or formality: the OpenAI model does not offer one.
:::

## Schedules {#schedules}

Schedules start and stop capture automatically, for example every Sunday from
10:00 AM to 12:00 PM.

![Schedules with three entries.](images/admin-schedules.png){width=100%}

1. **Status line.** What happens next, for example **Next start: Wed
   09/23/2026 8:00 PM CDT (Wednesday night).** With no schedules, it says
   capture only starts when you press **Start capture**.
2. **Schedule.** Its name, and under it a summary: which days, the times and
   the time zone.
3. **Next start.** When it will next start capture. **off** if the schedule
   is switched off.
4. **On.** Switch a schedule off to skip it without deleting it, for example
   during a holiday.
5. **Edit** changes a schedule. **Delete** removes it.
6. **Add schedule** opens the form to create one.

### To add a schedule

![The form for a new schedule.](images/admin-schedule-form.png){width=100%}

1. **Name (optional).** A label such as *Sunday evening*.
2. **Repeats.** **Every week**, or **One time only** for a single date.
3. **Days.** For a weekly schedule, click each day it runs. Selected days turn
   green.
4. **Starting on / Ending on (optional).** For a weekly schedule, limit it to
   a date range, such as a term or a season. Type the date as MM/DD/YYYY, or
   click the calendar button. For a one-time schedule, this is the date it
   runs.
5. **Start time** and **Stop time.** Pick the hour, the minutes and AM or PM.
   If the stop time is earlier than the start time, it stops the next day, so
   11:00 PM to 1:00 AM works. The form tells you when that happens.
6. **Time zone.** Eastern, Central, Mountain, Arizona, Pacific, Alaska or
   Hawaii. Times follow this zone, including daylight saving: 10:00 AM stays
   10:00 AM all year.
7. Click **Add schedule** (or **Save schedule** when editing). **Cancel**
   closes the form without saving.

You should see the new schedule in the list, with its next start time.

### How schedules behave

- **Already running when a schedule starts:** capture keeps running and stops
  at the schedule's stop time.
- **You press Stop during a scheduled time:** capture stays off until the
  next scheduled start. Pressing **Start capture** again undoes that, and
  capture then stops at the schedule's stop time.
- **You start capture by hand outside any schedule:** no schedule stops it.
  You must press **Stop**.
- **Two schedules that overlap or touch** run as one session, without
  stopping in between.
- **Changing or deleting a schedule while it is running** does not stop
  capture. Press **Stop** yourself.
- **If a scheduled start fails**, for example because the microphone is
  unplugged, the error shows in the panel and Relay tries again every few
  seconds until the stop time.

::: warning
The Relay computer must be on, awake and running Relay for a schedule to
work. If the computer is asleep or Relay is stopped, the schedule is skipped,
not run late. Set the computer never to sleep while it is plugged in.
:::

## Viewer links {#viewer-links}

This page lists the links to give out, each with a **Copy** button and a
QR code.

![Viewer links, with a QR code for each.](images/admin-viewer-links.png){width=90%}

1. **For the room**: links for the audience.
2. **The view**: **Translation**, **Transcription**, **Both**, **Chooser
   page** or **Screen (kiosk display)**.
3. **The link.** Click it to open that view in a new tab.
4. **Copy** puts the link on the clipboard. The button says **Copied** for a
   moment.
5. **QR code.** People point their phone camera at it to open the link. Show
   it on a slide or print it.
6. **Screen (kiosk display)**: for a screen that nobody touches, such as a
   lobby TV. No buttons, no scroll bar, just the last few lines of captions.
   It follows the first live language by itself.
7. **For your switcher**: **Overlay (OBS / vMix / ProPresenter)**, a
   transparent caption layer to put over live video, in a video switcher or
   streaming program. This link uses the default look. To choose the layout,
   language, size and colours, use [Broadcast overlay](#broadcast-overlay).

::: note
The links use the Relay computer's LAN IP address. That address can change
when you move to a different network, so check the links (and reprint the QR
codes) at each new venue.
:::

### Changing a kiosk link

The **Screen** view is set up by adding options to the end of the link. (The
overlay has a page of its own that writes its link for you; see
[Broadcast overlay](#broadcast-overlay).) Separate the first option with `?` and each further one with
`&`. For example:

`http://192.168.1.50/screen?lang=FRENCH&lines=2`

| Option | Works on | What it does | Example |
|---|---|---|---|
| `lang` | Screen | Always show this language. | `lang=SPANISH` |
| `stream` | Screen | `translation` (default), `transcription` or `both`. | `stream=transcription` |
| `lines` | Screen | How many lines to show. | `lines=3` |
| `font` | Screen | Text size in pixels. | `font=64` |
| `align` | Screen | `bottom` (default) or `center`. | `align=center` |

## Broadcast overlay {#broadcast-overlay}

The broadcast overlay puts captions over your livestream. It is a web page
with a transparent background. OBS, vMix and ProPresenter show it as a
*browser source* (a layer that displays a web page). A Blackmagic ATEM
switcher can key it in.

This page builds the overlay. Choose a layout, adjust it while you watch the
preview, then copy the link into your streaming program.

1. **Layout.** Pick a starting point:
   - **Lower third**: two lines at the bottom of the picture.
   - **Top strip**: one wide line at the top, out of the way of name captions.
   - **Accessibility slate**: large, easy-to-read text on black, filling the
     screen. Use it on a screen or stream of its own.
   - **Two languages**: two languages stacked, each with its name above it.
2. **Captions.**
   - **Show**: **Translation**, **Original** (what is being said) or **Two,
     stacked**. With two, pick a **Second language**, or leave it on
     **Original**.
   - **Lines on screen**: how many lines to show at once.
   - **Finished lines only**: waits until each sentence is complete, so the
     words never change on air. Captions appear about a second later.
   - **Style**: **Roll-up** scrolls lines up as words arrive, like TV
     captions. **Pop-on** shows finished sentences as a block, then replaces
     the whole block.
   - **Hide after silence**: the captions fade out when nobody has spoken for
     this long. **never** keeps them up.
3. **Position & size.** Top, middle or bottom; left, centre or right; text
   size; how wide a line may get; the *title-safe margin* (a border kept clear
   so a TV does not crop the text); and the **Canvas**, which must match your
   streaming program's resolution.
4. **Look.** The font, and the **Text edge**: a **Plate** (a dark box behind
   each line), an **Outline**, a **Shadow**, or **None**. Pick the text and
   plate colours here. **Atkinson Hyperlegible** is the easiest font to read;
   it is designed for people with low vision.
5. **Output.** **Transparent** for OBS, vMix and ProPresenter. **Chroma
   green** for a switcher that keys out green. **Key matte** and **Black** for
   an ATEM luma key (see below).
6. **Preview.** Shows the overlay over a stand-in video picture, with sample
   captions. **Transparency** shows a checkerboard where the overlay is see-
   through. Untick **Sample captions** to see the live captions instead.
7. **Browser source link.** **Copy link** puts it on the clipboard. **Open**
   opens it in a new tab.
8. **Save as a named overlay.** Type a name and click **Save**. The overlay
   gets a short link, such as `/overlay/stream-spanish`. Change it here later
   and click **Save changes**: the streaming program picks up the change the
   next time it loads the page, without pasting a new link.
9. **Saved overlays.** Each one with **Copy link**, **Edit** and **Delete**.

::: tip
Save the overlay before you paste its link. A saved link never has to be
pasted again, however often you change the look.
:::

::: warning
Deleting a saved overlay stops captions in every program that uses its link.
:::

### Adding the overlay to your streaming program

The **Setup guides** tab on the page has the same steps.

**OBS Studio**

1. Under **Sources**, click **+**, then **Browser**. Name it "Relay
   captions".
2. Paste the link into **URL**. Set **Width** and **Height** to your canvas
   (**Settings** → **Video** → **Base (Canvas) Resolution**, usually
   1920 × 1080).
3. Untick **Shutdown source when not visible**, so captions do not restart
   when you change scenes.
4. Drag the source above your cameras. To caption every scene, put it in one
   scene and add that scene to the others.

The captions should appear over the picture when someone speaks.

**vMix**

1. Click **Add Input** → **Web Browser**. Paste the link and set the size to
   your output resolution.
2. Put the input on an **Overlay** channel (1 to 4) and press it on.

**ProPresenter**

1. Make a **Prop** with a **Web Page** object that fills the slide. Paste the
   link.
2. In **Looks**, turn **Props** on for the stream screen and off for the
   screens in the room.
3. Trigger the prop at the start of the service. It stays up while the slides
   change.

**Blackmagic ATEM**

An ATEM cannot open a web page, so a computer shows the overlay full screen
and its video output goes into the switcher.

- **Chroma green** (one input): choose **Chroma green**, open the link full
  screen on the computer, and set a **Chroma** key on that input.
- **Key matte** (two inputs, cleaner edges): save two overlays with the same
  settings, one with **Key matte** and one with **Black**. Show each full
  screen on its own output. In the ATEM, set a **Luma** key with the **Black**
  one as the fill source and the **Key matte** one as the key source.

Turn the key on for the program output. Switch off the computer's screen saver
and notifications.

::: note
The overlay link also takes `?lyrics=1`, to show ProPresenter's lyrics in
place of the captions. See the ProPresenter chapter.
:::

## Blocked words {#blocked-words}

Blocked words are removed from the captions before anyone sees them. There is
no asterisk and no gap, so nobody notices.

![Blocked words, with a short list.](images/admin-blocked-words.png){width=100%}

1. **The file.** The list is stored in a file called `blocklist.txt`, in
   Relay's state folder (**Open state folder** in the Relay menu).
2. **The list.** One word or phrase per line.
3. **Save to file** saves the list. It takes effect within about two
   seconds. You do not need to restart anything, even mid-event.
4. **How many terms** are active.

### To add or remove a word

1. Open **Blocked words**.
2. Click in the list. Type the new word or phrase on a new line, or delete a
   line to remove it.
3. Click **Save to file**.
4. You should see **Saved to file — live now.** and the term count change.

### How matching works

- Capitals and accents do not matter: `nino` blocks `Niño`.
- Only whole words match: `ass` does not touch `class`.
- Phrases work: `off the record` removes all three words together.
- Lines starting with `#` are notes to yourself and are ignored.
- **English and the translation are filtered separately.** Blocking an
  English word does not block its Spanish translation. To remove a word from
  both, list both: `damn` and `maldito`.

::: tip
Removing a word can leave an odd sentence, such as "the first item on is the
review". For a word you expect often, blocking the whole phrase around it
reads better.
:::

## Recordings & export {#recordings}

Relay can save a written transcript of each session. It saves the text only,
never the sound. **Recording is off until you turn it on.**

![Recordings & export, with three saved runs (test data).](images/admin-recordings.png){width=100%}

1. **Record each run to disk.** Turn this on to save every session. A "run"
   is everything from **Start capture** to **Stop**.
2. **Runs kept.** How many runs to keep. When a new run starts and there are
   more than this, the oldest are deleted. The default is 20.
3. **Save** stores both settings. Recording starts with the **next** run.
   The path beside it is the `recordings` folder inside Relay's state folder
   (**Open state folder** in the Relay menu).
4. **Run.** When it started. **· recording** marks the run in progress.
   **· cut short** marks a run that ended without **Stop**, for example after a
   power cut. Everything up to that point is still saved.
5. **Download.** **All (.zip)** downloads everything from that run in one
   file. **Files…** lists the files so you can download one.
6. **Delete** removes a run for good. You cannot delete the run that is being
   recorded.

### To download a transcript

1. Open **Recordings & export**.
2. Find the run by its date and time.
3. Click **All (.zip)**. Your browser saves a ZIP file.
4. Open the ZIP. The file `transcript-ENGLISH.txt` is the English transcript,
   with the time of each line. There is one `transcript-` file for each
   language.

The ZIP also holds files for specialists: `pairs-` files match each English
line to its translation, and `finetune-` files are ready for training an AI
model. Most people can ignore them.

### How retention works

- Only the number of runs in **Runs kept** is kept. The oldest are deleted
  automatically when a new run starts, so the disk never fills up.
- Deleting a run from the panel deletes it at once.
- Blocked words are removed before anything is saved.

::: warning
A recording is a written record of everything said in the room. Tell the
speakers if you record, follow your organisation's privacy rules, and delete
runs you no longer need.
:::

## Credentials {#credentials}

This page changes the OpenAI API key. Only an Owner sees it.

![Credentials.](images/admin-credentials.png){width=100%}

1. **Key status.** **· set** followed by the last four characters of the key
   in use, so you can tell which key it is. **· not set** in red means there
   is no key and capture cannot start. The full key is never shown.
2. **OpenAI API key.** Paste a new key here. Leave it blank to keep the
   current one.
3. **Save credentials** saves the key.

### To change the API key

1. Create a new key on the OpenAI website. See Part 2.
2. Open **Credentials** and paste the key into **OpenAI API key**.
3. Click **Save credentials**.
4. You should see **Saved. Restart capture to use the new key.** and the last
   four characters change.
5. If capture is running, click **Stop**, then **Start capture**. The new key
   is only used from the next start.

::: warning
Treat the API key like a credit card number. Never paste it into a chat,
an email or a screenshot. If it leaks, delete it on the OpenAI website and
make a new one. See Part 2, "Keeping the key safe".
:::

## Users {#users}

Everyone signs in to the operator panel as themselves. This page, which
Admins and Owners see, lists the people who can sign in and what each may do.
Someone with a lower role does not see it.

### Roles

Relay has five roles. Each can do everything the roles before it can, and more.

| Role | What it is for |
|---|---|
| **Member** | People who never open the operator panel, such as musicians and volunteers. They sign in to use what they are allowed, like sending alerts when the sender page needs a sign-in, and land on a page that lists it. |
| **Viewer** | Looking: session health, panels, alerts, overlays, schedules and settings, but changing nothing. |
| **Operator** | Running a service: start and stop captions, choose target languages, make overlays, list and download recordings, and control ProPresenter and the Planning Center plan. |
| **Admin** | Setting Relay up: audio, panels, alerts, schedules, blocked words, appearance, recordings, modules, and the people on this page. |
| **Owner** | Everything an Admin can do, plus the OpenAI API key under **Credentials**. |

The **What each role can do** table at the bottom of the page shows the exact
list.

### The first account

The first time anyone opens the panel on a new Relay, it shows **Set up
Relay** instead of a sign-in page. Until the first account exists, that page
opens only on the Relay computer itself (a service install asks for its
one-time setup code instead). Type a username, your name and a password (at
least 8 characters), and click **Create the Owner**. You are signed in. A username is 3 to 32 letters, digits, dots, dashes or
underscores.

### To add a person

1. Open **Users** and click **Add user**.
2. Fill in their username, name and role, and a temporary password.
3. Give them the username and the temporary password. At their first
   sign-in, Relay makes them choose their own password.

There is no email. You can only give someone a role up to your own: an
Admin cannot make an Owner.

### Forgotten passwords and leaving people

Each person in the list has buttons to change their details, reset their
password, sign them out, turn them off and delete them.

- **Reset a password.** Choose a new temporary password and give it to the
  person. Resetting signs them out everywhere and clears their PIN, and they
  must choose a new password at their next sign-in.
- **Turn off or delete.** The person is signed out at once and cannot sign in.
- Relay always keeps one active Owner: the last one cannot be turned off,
  deleted or given a lower role. You cannot turn off or delete yourself, and
  you cannot change someone with a higher role than yours.

### My account, and PINs

Click your name at the top of the panel to open **My account**. There you can
**Change password**, which signs you out of other browsers, and set a PIN.

A PIN is 4 to 8 digits and is a quicker way to sign in on a **trusted
device**, such as the sound booth computer. To trust a computer, an Admin
signs in on it, opens **Users** and clicks **Trust this browser**, giving it a
name. On a trusted browser, the sign-in page lists the people who have a PIN:
pick your name and type your PIN. A PIN never works on any other browser.
After five wrong PINs in a row, the PIN is cleared and you sign in with your
password and set a new one. Removing a trusted device from the list stops PIN
sign-ins there.

### API tokens {#api-tokens}

An **API token** lets a script or another program, such as a Stream Deck
button, use Relay's API as you without your password. Admins and Owners can
make them.

1. Open **My account** and find **API tokens**.
2. Click **New token**. Say what it is for, and choose when it expires: 30,
   90 or 365 days, or never. 90 days is chosen for you.
3. Click **Make token**. Relay shows the token once. Copy it now and keep it
   somewhere safe. If you lose it, revoke it and make a new one.

The program sends it in a header: `Authorization: Bearer relay_…`. A token
can do what your role can, except manage users, passwords, trusted devices,
tokens or the OpenAI key. It works only on the operator panel's address, not
the viewer address.

- **Revoke** a token in the list to stop it at once. The list shows the first
  characters of each token, so you can tell which one a program holds.
- Turning a person off stops their tokens at once. Deleting them, or giving
  them a role below Admin, deletes their tokens.
- After an Owner resets your password, your tokens wait until you choose a new
  one.
- Owners see everyone's tokens under **Users**, **API tokens**, and can revoke
  any of them.

::: note
An API token is not the same as a panel's or a trusted device's sign-in.
Those are unchanged.
:::

### How long you stay signed in

Signing in with a password lasts 30 days on that browser. Signing in with a
PIN lasts 12 hours. **Sign out** at the top right ends it now.

### If nobody can sign in {#lockout}

If the only Owner has forgotten their password, run one of these on the Relay
computer, in a terminal (**Terminal** on a Mac, **Command Prompt** on
Windows). Replace `YOURNAME` with the username.

| Computer | Command |
|---|---|
| macOS | `/Applications/Relay.app/Contents/Resources/relay-backend/relay --reset-owner YOURNAME` |
| Windows | `"%LOCALAPPDATA%\Relay\relay-backend\relay.exe" --reset-owner YOURNAME` |
| Linux, service install | `sudo tools/install-service.sh reset-owner YOURNAME` (from the Relay folder) |
| From a source checkout | `python tools/reset_owner.py YOURNAME` |

Relay asks you to type a new password twice (it does not show as you type),
makes that person an Owner (or creates them), ends their sessions and clears
their PIN. Sign in with that password.

### The user database {#user-database}

The accounts are kept in one file, `relay.db`, in Relay's state folder. If
it cannot be opened (a corrupt file, a full disk), captions and the viewer pages
keep working, but the operator panel says **The user database is not available**
until it can be. Once a day Relay saves a copy into the `backups` folder beside
it, and keeps the newest 7. Copying the state folder copies the accounts too. Deleting
`relay.db` deletes all accounts, and Relay asks for the first Owner again.

## Appearance & tuning {#appearance}

This page sets the default text size, and when caption lines break.

![Appearance & tuning.](images/admin-appearance.png){width=100%}

1. **Default viewer font size (px).** The text size a viewer page starts
   with, from 16 to 160. The default is 40. Each viewer can still change it
   with **A−** and **A+**. A device remembers the size from its first visit,
   so a new default only reaches devices that have not opened the viewer page
   before. Set it before you hand out the link.
2. **History lines kept per stream.** How many earlier lines a viewer can
   scroll back through, and how many a latecomer sees when they join. The
   default is 40.
3. **Caption line break after silence (s).** After this many seconds of
   quiet, at the end of a sentence, the current line is finished and a new one
   begins. The default is 1. Lower it for shorter, snappier lines.
4. **Force a line break after (s).** A backstop for speakers who never seem
   to finish a sentence: after this many seconds, the line is broken at the
   next word anyway. The default is 10. Raise it if you see one sentence split
   across two lines.
5. **Save** stores all four.

::: note
Saving this page restarts the connections to OpenAI if capture is
running, so captions pause for a few seconds. Change it between sessions if
you can.
:::

These settings change when a line *settles*. The words themselves already
appear as they are spoken. If captions arrive late, the cause is the internet
connection, not these settings.

::: note
Relay's colours are fixed: dark by default, with the light / dark button on
each viewer page. The overlay's colours, font and background are set on
the [Broadcast overlay](#broadcast-overlay) page.
:::

## Modules {#modules}

Relay's links to other products (Planning Center, ProPresenter, and the ones
still to come) are **modules**. Each is switched on and set up on its own. A
module that is off contacts nothing, and its pages do not appear in the
sidebar.

### To switch a module on or off

1. Open **Modules**, under **Settings**.
2. Click the switch on the module's card. The page reloads, and the module's
   own pages appear in the sidebar, or go.
3. Set it up on its own page. **Setup guide** on the card and at the foot of
   the module's page opens the instructions for it.

Each card says whether the module is working. **Test connection** tries the
saved settings without changing anything.

::: note
After the update that added modules, a module you had already set up starts
switched on, so nothing changes for you. One you had not set up starts off.
:::
