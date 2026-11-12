# ProPresenter: lyrics and the stage display {#propresenter}

If your church runs **ProPresenter** (version 7.9 or newer), Relay can watch
it. Relay then knows which slide is up, can show the **real lyrics** on its
screens and panels while a song is playing, and can put a short message on
ProPresenter's **stage display**, for example when someone presses an alert.

This is optional. Relay does not contact ProPresenter until you enter an
address and switch it on.

## What Relay does, and what it does not

- **Reads** the current and next slide, the presentation's name and the
  part (Verse 1, Chorus), the active look, and which layers have content.
- **Shows** the lyrics on `/screen` and `/overlay` pages, and offers them to
  caption panels.
- **Controls** ProPresenter from **ProPresenter control** in admin: next and
  previous, a grid of the slides with thumbnails, a clear dock, playlists,
  looks, macros, timers, prepared messages and the media position (see
  [Running ProPresenter from Relay](#propresenter-control)).
- **Writes** the stage message, and everything the control page does.
- **View-only** mode turns all of that off. Relay then only reads.

::: warning
ProPresenter's network control has **no password**. Anyone who can reach its
port can drive your slides. Relay only ever talks out to the address you type
here. Keep the ProPresenter machine on your production network, not on the
guest Wi-Fi.
:::

## Step 1: Switch on Network in ProPresenter

1. In ProPresenter, open **Settings**, then **Network**.
2. Switch **Enable Network** on.
3. Note the **port** it shows. The port is different on every install, so
   Relay does not guess one.

## Step 2: Connect Relay

1. In the operator panel, open **ProPresenter**.
2. Under **Connection**, type the ProPresenter machine's name or IP address and
   the port. Or click **Find on this network** and pick it from the list.
3. Click **Test connection**. You should see the ProPresenter version.
4. Tick **Follow ProPresenter** and click **Save**.

The line under the buttons should turn green and say **Connected**. Under
**Now** you will see the presentation, the part, and the current and next slide
as soon as a slide is live.

::: note
**Find on this network** cannot see ProPresenter from inside Docker on macOS
or Windows. Type the address instead.
:::

## If Relay runs in Docker

The Relay container must be able to reach the ProPresenter machine's address
and port. If it cannot, **Test connection** says it could not reach it. Allow
the port in the firewall of the ProPresenter machine (on a Mac, under
**System Settings**, **Network**, **Firewall**, allow ProPresenter to accept
incoming connections), and make sure both machines are on the same network.

## Lyrics

Song lyrics are licensed. Relay therefore keeps the lyrics **off the public
phone viewer** unless you switch it on. Under **Lyrics feed** you choose where
they may appear:

- **Screens.** Open `/screen?lyrics=1`. While a song is up, the lyrics replace
  the captions. When no slide is up, the captions come back.
- **Broadcast overlay.** Open `/overlay?lyrics=1`. Off by default.
- **Caption panels.** Offered to panels, which can be set to the **Lyrics**
  feed (see [Lyrics and timers on a panel](#propresenter-panels)).
- **Phone viewer.** Off by default.

**Songs only** keeps sermon and announcement slides off the lyrics feed. A
presentation counts as a song when its file is in the library you name under
**Library that holds the songs** (the default is `Songs`), or when its parts
are named Verse, Chorus, Bridge and so on.

Nothing is shown when ProPresenter has no presentation on screen, even if an
old slide's text is still remembered.

## Lyrics and timers on a panel {#propresenter-panels}

A caption panel can show ProPresenter on its own screen, instead of captions.
Open the panel's settings (**Panels**, then the panel's name) and set its feed
to **Lyrics (ProPresenter)** or **Timers (ProPresenter)**. The choice is in
the panel's own menu too. Both appear only while ProPresenter is set up in
Relay, and **Lyrics** only while **Caption panels** is ticked under **Lyrics
feed**.

**Lyrics.** The current slide's lines are large and in the middle of the
screen. The first words of the next slide are under them, dimmed, and the part
(**Verse 1**, **Chorus**) and the song's title are small along the bottom.
With no song on screen, or a blank slide, the panel shows nothing but a few dim
words: never the last slide. The size follows the words: a short line is very
large, a verse is smaller. A slide with accents (Spanish and French lyrics) is
drawn in the panel's caption font, which is smaller but keeps them.

**Timers.** Each timer is large, with its name and whether it is running under
it. The panel counts the seconds itself, so it ticks smoothly. A countdown is
white while it runs, **amber** from the time you set under **Timers on panels**
(2:00 by default) and **red** from the next (0:30), and red with a minus sign
once it is past zero. A timer that is stopped is grey, and a timer that counts
up (an elapsed timer) has no colours. If the panel loses Relay, the digits go
grey and say **not updating**.

Under **Timers on panels** you choose the two times, and which timers panels
show (up to four; with none ticked, the first four ProPresenter has). A panel
can use its own: in its settings, under **ProPresenter timers**, tick **Use
settings of its own for this panel**.

## Stage message

Type a message under **Stage message** and click **Show**. It appears on
ProPresenter's stage display. **Clear** takes it down.

An alert can also appear there. Under **Alerts**, tick **Also show on the
ProPresenter stage display** on a button. When that button is pressed, its name
and the sender show on the stage display, and they disappear when someone
acknowledges the alert or it times out.

## Running ProPresenter from Relay {#propresenter-control}

Open **ProPresenter control** in admin. Everything on it needs the admin
sign-in, and every button is off while ProPresenter is down or view-only is on.

- **Next, Previous, Previous item, Next item.** The arrow keys and Page Up /
  Page Down move a slide while the page is open (not while you are typing in a
  box). Hold **Shift** with the same keys, or use the two item buttons, to go to
  the previous or next **item** of the active playlist (the next song, rather
  than the next slide). If no playlist is active or focused in ProPresenter,
  Relay says so.
- **What is up.** Under the buttons: the item's title, the part it is in (Verse,
  Chorus) with ProPresenter's colour and the slide's label, the slide number
  ("slide 3 of 8"), the playlist and item it came from, and the notes of this
  slide and the next.
- **Screens and announcements.** Whether the audience and stage screens are on
  or off, with a button to switch each (off in view-only mode), and the
  announcement that is up on the announcements layer: its title, slide number
  and text. Clear announcements from the dock.
- **Slide grid.** The slides of the presentation that is up, with the part
  name and colour from ProPresenter. Tap one to trigger it; the one that is
  live is outlined. A repeated chorus appears once for every time it is
  played. Thumbnails come through Relay, which keeps a copy, so phones and
  tablets never talk to ProPresenter directly.
- **Clear dock.** A bar along the bottom of every admin page: Slide, Media,
  Props, Messages, Audio, Video input and Announcements. A button is red while
  that layer has something live.
- **Playlists.** Pick a playlist and tap an item to trigger it. The active
  item is highlighted. Section markers (headers) show with their colour, hidden
  items are dimmed, and each presentation has a small thumbnail. **Slides** opens
  an item's slides in the order its own arrangement plays them, so a chorus
  that comes round twice has two slides, each with its own picture.
  **Check this playlist** counts the items by kind and lists each one with its
  ProPresenter ID, marking presentations that ProPresenter no longer has
  ("NOT FOUND") or that were never linked to one. A lookup that failed because
  ProPresenter was slow says "could not check", not "not found".
- **Looks, macros, timers.** A timer is started, stopped or reset one at a
  time; Relay never starts all timers at once. Macros show only an
  operator-safe selection: use **Choose what operators see** to tick the
  ones to offer, or show them all. A macro outside the selection is refused
  by Relay even if someone asks for it directly.
- **Messages.** Fill in a prepared message's text and tap Show; Clear hides it.
- **Media position.** Type a time in seconds and tap Go to time.

## View-only mode

Tick **View-only** when Relay should never change anything in ProPresenter, for
example on a second computer, or when your team runs all slides from
ProPresenter. Relay still reads and shows lyrics and slides, but the stage
message, alert-on-stage and every control on the control page do nothing.

## If something is wrong

- **Could not reach ProPresenter.** Check the address and port, that **Enable
  Network** is on, and the firewall.
- **Not found.** Network is probably switched off in ProPresenter, or the port
  is wrong.
- **Relay says it is not answering.** ProPresenter sometimes stops answering
  for a few minutes. Relay waits, reconnects by itself, and says once when it
  is back.
