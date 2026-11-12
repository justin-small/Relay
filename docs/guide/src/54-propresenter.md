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
- **Links** the plan to ProPresenter: matches Planning Center's items to presentations,
  can build one Relay-owned playlist, shows both sides, and (if you switch it on)
  lets ProPresenter move Planning Center's LIVE item (see
  [Linking to Planning Center](#propresenter-link)).
- **Shows the slides** as pictures, with a countdown and video progress over
  them, and **runs a lobby announcement rotation** on its own (see
  [Slide view and lobby announcements](#propresenter-lobby)).
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

## Slide-note tags {#propresenter-tags}

A tag typed into a slide's **notes** in ProPresenter makes Relay do something
when that slide goes live: start captioning, put the lyrics on the panels,
change the language. Relay only reads the notes; it never writes to them.

Tags are **off** until you tick **Run slide-note tags** under **Slide-note
tags** on the ProPresenter page (and Follow ProPresenter is on).

Write a tag as `[relay:action]`, or `[relay:action=value]` for an action that
takes a value. Put as many as you like on a slide. Capital letters do not
matter, and anything else in the notes is ignored.

| Tag | What Relay does |
|---|---|
| `[relay:capture-start]` | Starts Relay's own captioning (the same as **Start** in admin). Nothing if it is already running. |
| `[relay:capture-stop]` | Stops it (the same as **Stop**). Nothing if it is already stopped. |
| `[relay:lyrics]` | Caption panels that show captions switch to the ProPresenter lyrics. Panels showing the service, the alert buttons or timers are left alone. Needs **Caption panels** ticked under Lyrics. |
| `[relay:captions]` | Panels that a tag switched to the lyrics go back to what they showed. A panel someone changed at its own screen since is left as it is. |
| `[relay:lang=es]` | Panels showing captions show them in this language. Write a code (`es`) or a name (`Spanish`) from the languages Relay offers. The language must be switched on under Languages. |
| `[relay:alert=Sound desk]` | Raises the alert on the Alerts button with that name (not case-sensitive), like a tap on it, and does nothing if it is already flashing. The text must match a button. |
| `[relay-deck:action]` | Any of the above, written in **any** slide's notes, as a default for every slide of that presentation that has no `[relay:...]` tag of its own. |

**`capture-start` and `capture-stop` are Relay's captioning, not ProPresenter's
capture.** Relay never starts or stops ProPresenter's capture.

A value runs to the end of its line and may hold spaces. To put a `]` in it,
write `[...]` as a pair (`[relay:alert=Check [mic] now]`) or put a backslash in
front of it (`\]`).

**How they run.**

- **A slide has to settle.** A tag runs only after its slide has stayed live
  for the settle time (1.5 seconds; change it on the ProPresenter page, 0.2 to
  30). Clicking quickly through a section runs the tags of the slide you stop on,
  not of every slide you passed.
- **The same tags run once.** What runs is the slide's whole set of tags. When
  the next slide settles on the same set (every slide of a song with a
  `[relay-deck:lyrics]`), nothing runs again. A slide with no tags at all is a
  set of its own, so a tag that comes back after it runs again. Stepping back to
  a slide you left a moment ago does not re-run tags you have already run.
- **A slide's own tag beats the deck's.** If a slide has any valid `[relay:...]`
  tag, the deck-wide ones do not apply to it. A mistyped tag does not count as
  one, so that slide falls back to the deck's.
- **Deck-wide tags are read once.** Relay reads the whole presentation's notes
  once, when the presentation goes live, not for every slide. A
  `[relay-deck:...]` you add to another slide while the presentation is up is
  not seen until it goes live again.
- **View-only does not stop them.** View-only is about writing to ProPresenter,
  and tags only change Relay.

The admin section shows the slide that is up, the tags on it and the deck's,
what runs for it, what ran lately with the time and the result, and a warning
for every tag Relay could not use: an action it does not know, a missing value,
a language it does not offer, or a tag with no closing bracket.

A tag that cannot run says so in the list (captioning could not start without
an OpenAI key, no alert button has that name, a language is switched off) and
the others on the slide still run.

Panels that connect after a `[relay:lyrics]` are not switched; the next change
of tags does it.

## Linking to Planning Center {#propresenter-link}

If you also use **Planning Center Services** (see [Showing the service from
Planning Center](#planning-center)), Relay can compare the plan with what is in
ProPresenter. Open **ProPresenter** in admin and find **Planning Center link**.
Both switches there start **off**.

**Matching.** Press **Match the plan now**. Relay reads ProPresenter's playlists
and libraries (it only reads) and pairs each plan item with a presentation:

1. **A pairing you made.** For an item whose presentation has a different
   name, like "Message" and "Sermon: Grace", press **Pair** on its row and pick
   the presentation. The pairing is remembered by the item's title, so it still
   works next week. **Unpair** removes it.
2. **ProPresenter's playlist synced from Planning Center**, in its own order.
   An item ProPresenter has not linked to a presentation shows as **Not linked
   in ProPresenter**; Relay does not guess for it.
3. **The same title**, ignoring capitals, punctuation, "(Live)", the key
   ("- G", "in G") and an arrangement name.
4. **A similar title**, only if it is clearly the closest. If two presentations
   fit equally well the row says **Several fit** and you choose.

Headers have no presentation. Every row says how it was matched, so you can see
why. Anything unmatched is listed with a red row.

**Build the Relay playlist.** Press **Build the Relay playlist** to put the
matched presentations in the plan's order into one ProPresenter playlist named
**Relay – 2026-11-15** (the plan's date). Relay makes that playlist the first
time and remakes only that one after. It **never changes any other playlist**,
yours included. If a playlist with that name already exists and Relay did not make
it, Relay stops and tells you. In **View-only** mode the button is off.
After writing, Relay reads the playlist back and says whether it matches.

**Follow the plan.** With **Follow the plan** on, Relay keeps the match up to date and
shows, side by side, the item ProPresenter has up and the item Planning Center
is on, with a note when they disagree. Caption panels are told which plan item
ProPresenter has up; the real lyrics reach panels through the Lyrics view as
before.

**ProPresenter drives Services LIVE.** Switch this on and, once a presentation
has been up for **Stay up for** seconds (10 by default), Relay moves Planning
Center's LIVE to the matching item with the same Next and Previous presses a
panel uses.

- It **never takes control.** It moves LIVE only when Relay's own Planning Center
  token already holds control. If someone else holds it, or nobody does, nothing
  moves and the page says which.
- It tries **once** each time a presentation comes up. Skipping quickly through
  several songs starts the wait again, so only the one you stay on counts.
- It goes **forward** by default. Tick **Also go back** and it will step back to
  the matching item when ProPresenter shows an earlier one, and never further.
- A presentation that matches no plan item moves nothing.

Recent moves, and why a move did not happen, are listed under the switches.

## Slide view and lobby announcements {#propresenter-lobby}

Open **Slide view and lobby** in the admin menu.

### Slide view

The live slide and the next one, as pictures drawn by ProPresenter, with the
slide's text instead when no picture can be had. Over the live slide:

- **A countdown**, for the timer you pick under *Countdown on the slide*. It
  turns amber and red at the same times as the timers on the panels.
- **Video progress**, a bar with the time played and the length, while a video
  is playing on the slide. A paused video stays where it is.

Your browser never talks to ProPresenter: Relay fetches the pictures and keeps
them, so a slide is fetched once.

Slide pictures are licensed content, like lyrics, so this page is only for
people signed in to admin. If you want a stage-style screen without the login,
for example a tablet on the platform, tick *Let the slide view be opened without
the admin login*. It is then at **/stage** on the viewer address, and shows
only the slide that is up and the one after it. It is off until you tick it.

### Lobby announcements

Relay can step a ProPresenter announcement playlist along by itself, so the
lobby screens keep moving without anyone at the computer.

1. Pick the **playlist** your lobby screens show. ProPresenter does not tell
   Relay which playlists are announcement playlists, so you choose.
2. Set **seconds per slide** (8 to start with) and whether it should **start
   over** at the end.
3. Press **Start**. Nothing runs until you do, and a restart of Relay does not
   start it again.

Relay shows the first slide, then moves on a slide at a time, and on to the next
playlist item after a presentation's last slide. Headers and hidden items are
skipped.

If you or someone else puts **something else** on the announcements layer, the
rotation **waits** and changes nothing. It carries on, from the top, once that is
cleared. If you move to another slide of the same playlist, it carries on from
there. If you press **Clear announcements** while it runs, it stays paused until
you press **Resume** (or **Stop**), because a clear is usually on purpose.

If ProPresenter goes away for a while the rotation waits, and starts the
playlist again when ProPresenter comes back with nothing on screen. **Stop**
ends it and leaves the last slide up; **Stop and clear announcements** also
clears the layer. In view-only mode Relay cannot change anything, so it cannot
start.

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
