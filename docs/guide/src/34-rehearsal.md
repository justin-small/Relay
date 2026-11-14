# Rehearsal mode {#rehearsal}

Rehearsal mode lets you check the screens without paying for a session. The
viewer pages show a short made-up script, over and over, as if someone were
speaking. Nothing is sent to OpenAI.

Use it to:

- check that the viewer link works on the phones in the room and on the
  venue Wi-Fi;
- choose a text size that is readable on the projector from the back row;
- print and test the QR codes;
- show a new operator around the screens.

All the viewer screenshots in this guide were taken in rehearsal mode.

## Turning it on

Rehearsal mode is switched on when you start Relay, with a setting called
`RELAY_DEMO`. You quit Relay, then start it once from a terminal with that
setting. The exact command is different on each computer:

- **macOS**: see [Installing on macOS](#install-macos), "Rehearsal mode".
- **Windows**: see [Installing on Windows](#install-windows), "Rehearsal
  mode".
- **Linux**: see [Installing on Linux](#install-linux), "Rehearsal mode".

On a Mac, for example, choose **Quit Relay** from the Relay menu, then type
this in **Terminal**:

```bash
RELAY_DEMO=1 /Applications/Relay.app/Contents/MacOS/relay-desktop
```

## What you should see

1. Open a viewer link, such as `http://192.168.1.50/translation`.
2. Within a few seconds, English sentences appear word by word on the
   **Transcription** page, and their Spanish translation on the
   **Translation** page.
3. The script is five sentences long and repeats every 15 seconds or so.

The rehearsal script is always English with a Spanish translation. It is
filed under one language that was switched on in **Target languages** when
Relay started, so switch Spanish on before you start Relay in rehearsal mode
to see it under the right name. The other viewer controls,
such as **A−**, **A+**, light / dark and presentation mode, all work as normal.

The operator panel works too, so you can practise with every page. The
panel shows capture as **Stopped** and **Session health** says **Not
started.**, because no real session is open.

## Turning it off

Choose **Quit Relay** from the Relay menu, then open Relay again the normal
way (from **Applications**, the Start menu or the applications menu).

To check which mode Relay is in, choose **Open logs folder** from the Relay
menu and open `relay.log`. In rehearsal mode you see this line near the start:

```
REHEARSAL MODE: captions are canned. No OpenAI session is open.
```

::: note
**Start capture is switched off in rehearsal mode.** If you press it, the
panel says so and nothing starts. **Schedules** are skipped too: a schedule
that comes due during a rehearsal does not start a session. Neither can open
a billed session with your API key. To go live, stop Relay and start it again
the normal way.
:::

::: warning
Rehearsal mode stays on until you quit Relay. Always quit and open Relay the
normal way after a rehearsal, and check the viewer
page before the event: real captions follow what is said in the room, not
the sample script.
:::
