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
- **Writes** one thing: the stage message. Nothing else in ProPresenter is
  changed by this chapter's features.
- **View-only** mode turns even that off. Relay then only reads.

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
- **Caption panels.** Offered to panels that are set to show lyrics.
- **Phone viewer.** Off by default.

**Songs only** keeps sermon and announcement slides off the lyrics feed. A
presentation counts as a song when its file is in the library you name under
**Library that holds the songs** (the default is `Songs`), or when its parts
are named Verse, Chorus, Bridge and so on.

Nothing is shown when ProPresenter has no presentation on screen, even if an
old slide's text is still remembered.

## Stage message

Type a message under **Stage message** and click **Show**. It appears on
ProPresenter's stage display. **Clear** takes it down.

An alert can also appear there. Under **Alerts**, tick **Also show on the
ProPresenter stage display** on a button. When that button is pressed, its name
and the sender show on the stage display, and they disappear when someone
acknowledges the alert or it times out.

## View-only mode

Tick **View-only** when Relay should never change anything in ProPresenter, for
example on a second computer, or when your team runs all slides from
ProPresenter. Relay still reads and shows lyrics, but the stage message and
alert-on-stage do nothing.

## If something is wrong

- **Could not reach ProPresenter.** Check the address and port, that **Enable
  Network** is on, and the firewall.
- **Not found.** Network is probably switched off in ProPresenter, or the port
  is wrong.
- **Relay says it is not answering.** ProPresenter sometimes stops answering
  for a few minutes. Relay waits, reconnects by itself, and says once when it
  is back.
