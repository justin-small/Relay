# The operator panel {#admin-panel}

The operator panel is one long page. Two sections are always open at the top:
**Session health** and **Audio input**. Below them are sections you can open
and close by clicking their headings. The panel remembers which ones you left
open. A bar along the bottom holds **Start capture** and **Stop**, and stays
on screen as you scroll.

## Overview

![The whole operator panel, with capture stopped and every section closed.](images/admin-overview.png){width=88%}

1. **Header.** The dot is green while capture runs and grey when stopped. On
   the right: how many people have a viewer page open, and **Sign out**.
2. **Session health.** One row per open connection to OpenAI. See
   [Session health](#session-health).
3. **Audio input.** Choose the sound source and watch the level. See
   [Audio input](#audio-input).
4. **Target languages.** Choose the languages to translate into. See
   [Target languages](#target-languages).
5. **Schedules.** Start and stop capture automatically. See
   [Schedules](#schedules).
6. **Viewer links.** The links and QR codes to give the room. See
   [Viewer links](#viewer-links).
7. **Blocked words.** Words to remove from the captions. See
   [Blocked words](#blocked-words).
8. **Recordings & export.** Save and download transcripts. See
   [Recordings & export](#recordings).
9. **Credentials.** Change the OpenAI API key or the admin token. See
   [Credentials](#credentials).
10. **Appearance & tuning.** Text size and when caption lines break. See
    [Appearance & tuning](#appearance).
11. **The bottom bar.** **Start capture**, **Stop**, and the current state.
    See [Starting and stopping](#start-stop).

A closed section can still show a summary in its heading. For example,
**Target languages · French, Spanish armed** tells you which languages are
ready, without opening the section.

When something goes wrong, a red message appears under the header. Green
messages, such as **Saved.**, confirm that a change worked and fade after a
couple of seconds.

### A session in progress

This is the panel during a session, with two languages live.

![The operator panel while capture is running (test data).](images/admin-running.png){width=100%}

1. The header dot is **green**: capture is running.
2. **37 viewers**: people with a viewer page open right now. This number can
   lag a little behind.
3. **Session health** shows one row per language, both **connected**.
4. The **level meter** moves with the sound.
5. **Target languages** shows **French, Spanish live**.
6. The bottom bar says **Running**.

## Starting and stopping {#start-stop}

![The bottom bar while capture is running.](images/admin-bottom-bar.png){width=100%}

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
3. You should see the header dot turn green and the state change to
   **Running**.
4. Within a few seconds, **Session health** should show one row per language,
   each **connected**.
5. Speak, or ask someone to. The level meter should move and captions should
   appear on the viewer pages.

### To end a session

1. Click **Stop**.
2. You should see the dot turn grey and the state change to **Stopped**.
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

This section chooses where the sound comes from, and shows how loud it is.

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

This section chooses which languages Relay translates into. There are 12:
Chinese, French, German, Hindi, Indonesian, Italian, Japanese, Korean,
Portuguese, Russian, Spanish and Vietnamese.

![Target languages with capture stopped: two languages armed.](images/admin-targets-stopped.png){width=90%}

1. **Summary.** Which languages are live or armed. It stays visible when the
   section is closed.
2. **State** of each language:
   - **off**: not used.
   - **armed — starts with capture**: switched on, but capture is stopped, so
     nothing is being translated yet. It goes live when you click **Start
     capture**.
   - **live**: being translated now, and billed.
3. **Switch.** Click to turn a language on (green) or off (grey). The change
   is saved at once.

![Target languages while capture runs: the same two languages are live.](images/admin-targets-live.png){width=90%}

You can switch languages while capture is running:

- **Turning one on** opens its connection straight away. It appears in the
  viewers' language picker within a few seconds.
- **Turning one off** closes it at once. Viewers reading that language see
  their captions stop.

### What each language costs

Each live language is its own connection to OpenAI, billed per minute for as
long as capture runs, speech or silence. Two languages cost twice as much as
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

This section lists the links to give out, each with a **Copy** button and a
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
7. **For your switcher**: **Overlay (OBS / ProPresenter)**, a transparent
   caption layer to put over live video, in a video switcher or streaming
   program.

::: note
The links use the Relay computer's LAN IP address. That address can change
when you move to a different network, so check the links (and reprint the QR
codes) at each new venue.
:::

### Changing a kiosk or overlay link

The **Screen** and **Overlay** views are set up by adding options to the end
of the link. Separate the first option with `?` and each further one with
`&`. For example:

`http://192.168.1.50/screen?lang=FRENCH&lines=2`

| Option | Works on | What it does | Example |
|---|---|---|---|
| `lang` | Screen, Overlay | Always show this language. | `lang=SPANISH` |
| `stream` | Screen, Overlay | `translation` (default), `transcription` or `both`. | `stream=transcription` |
| `lines` | Screen, Overlay | How many lines to show. | `lines=3` |
| `font` | Screen, Overlay | Text size in pixels. | `font=64` |
| `align` | Screen | `bottom` (default) or `center`. | `align=center` |
| `bg` | Overlay | Background colour. Transparent by default. | `bg=black` |

## Blocked words {#blocked-words}

Blocked words are removed from the captions before anyone sees them. There is
no asterisk and no gap, so nobody notices.

![Blocked words, with a short list.](images/admin-blocked-words.png){width=100%}

1. **The file.** The list is stored in a file called `blocklist.txt`. Here it
   shows the path inside Relay's container; on the Relay computer it is
   `docker-config/blocklist.txt` in the Relay folder.
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
   The path beside it is inside Relay's container; on the Relay computer it is
   `docker-config/recordings/` in the Relay folder.
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

This section changes the OpenAI API key and the admin token, without running
setup again.

![Credentials.](images/admin-credentials.png){width=100%}

1. **Key status.** **· set** followed by the last four characters of the key
   in use, so you can tell which key it is. **· not set** in red means there
   is no key and capture cannot start. The full key is never shown.
2. **OpenAI API key.** Paste a new key here. Leave it blank to keep the
   current one.
3. **Admin token.** Type a new admin token here. Leave it blank to keep the
   current one.
4. **Save credentials** saves whatever you filled in.

### To change the API key

1. Create a new key on the OpenAI website. See Part 2.
2. Open **Credentials** and paste the key into **OpenAI API key**.
3. Click **Save credentials**.
4. You should see **Saved. Restart capture to use the new key.** and the last
   four characters change.
5. If capture is running, click **Stop**, then **Start capture**. The new key
   is only used from the next start.

### To change the admin token

1. Type the new token into **Admin token**.
2. Click **Save credentials**.
3. You stay signed in. Every other browser is signed out and must sign in
   with the new token. Companion, if you use it, needs the new token too.

::: warning
Treat the API key like a credit card number. Never paste it into a chat,
an email or a screenshot. If it leaks, delete it on the OpenAI website and
make a new one. See Part 2, "Keeping the key safe".
:::

## Appearance & tuning {#appearance}

This section sets the default text size, and when caption lines break.

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
Saving this section restarts the connections to OpenAI if capture is
running, so captions pause for a few seconds. Change it between sessions if
you can.
:::

These settings change when a line *settles*. The words themselves already
appear as they are spoken. If captions arrive late, the cause is the internet
connection, not these settings.

::: note
Relay's colours are fixed: dark by default, with the light / dark button on
each viewer page. The **Overlay** link's background colour can be set with
the `bg` option. See [Viewer links](#viewer-links).
:::
