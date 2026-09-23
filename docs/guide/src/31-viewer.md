# The viewer pages {#viewer}

The viewer pages are what you give the room. They show the captions as they
arrive, word by word. They work in any modern browser on a phone, tablet or
computer. There is nothing to install and no sign-in.

The address is the Relay computer's LAN IP address, for example
`http://192.168.1.50/`. The **Viewer links** section of the operator panel
shows the exact links, with a QR code for each. See
[Viewer links](#viewer-links).

::: note
Captions only appear while capture is running **and** at least one target
language is switched on. This is true even for the English transcript: the
English text comes from the same OpenAI connection as the translation. With
every language off, all the viewer pages stay empty.
:::

## The chooser page (Live Captions)

This is the page at the plain address, `http://192.168.1.50/`. It lets each
person pick how they want to read.

![The chooser page, titled Live Captions.](images/viewer-chooser.png){width=85%}

1. **Translation**: captions in one translated language. The reader picks the
   language. Most people who need a translation should tap this.
2. **Transcription**: the words as spoken, in the speaker's language
   (English). Useful for people who are hard of hearing.
3. **Both**: the English text and a translation side by side. Useful for
   bilingual readers and for checking the translation.

::: tip
You can skip the chooser. Give the room a direct link, such as
`http://192.168.1.50/translation`, and they land on the captions straight away.
:::

## The Transcription page

The address is `/transcription`. It shows what is being said, in English.

![The Transcription page on a desktop browser.](images/viewer-transcription.png){width=100%}

1. **Status dot.** Green means captions are live. Yellow means the page is
   connected, but capture is not running yet. Red means the page has lost its
   connection to Relay. It reconnects by itself when it can.
2. **View tabs.** Switch between the English transcript (**English**),
   **Translation** and **Both** without going back to the chooser.
3. **Light / dark.** Switches between white text on black and black text on
   white. Dark is easier to read in a dim room; light is better in daylight.
4. **Keep screen awake.** Stops the phone or tablet from going to sleep while
   the reader watches. It only appears on devices that support it. It turns on
   when you tap it, and it is outlined when on.
5. **Presentation mode.** Hides the top bar and makes the text bigger and
   fills the screen. Good for a projector or a confidence monitor. Press the
   **×** in the corner, or `Esc`, to leave.
6. **A− and A+.** Make the text smaller or larger. Each device remembers its
   own size.
7. **The captions.** New words appear at the bottom. The line being spoken
   right now is brighter. Earlier lines move up.

::: note
If you scroll up to reread something, the page stops following the new
captions. A **Jump to live** button appears at the bottom. Tap it to catch up.
:::

## The Translation page

The address is `/translation`. It shows the captions translated into one
language.

![The Translation page with the language picker.](images/viewer-translation.png){width=100%}

1. The **Translation** tab is highlighted.
2. **Language picker.** Choose the language to read. It lists only the
   languages that are live right now. When only one language is live, the
   picker is greyed out, because there is nothing to choose.

### Picking a language

1. Tap the language picker (2).
2. Tap your language. The captions switch at once. The page remembers the
   choice next time.

If the picker says **No languages live**, capture is stopped or no target
language is switched on. The operator needs to fix that in the panel. See
[Target languages](#target-languages).

## The Both page

The address is `/both`. It shows the English text and one translation side
by side.

![The Both page: English on the left, the translation on the right.](images/viewer-both.png){width=100%}

1. The **English** column: the words as spoken.
2. The **Translation** column: the same words in the chosen language.
3. The **language picker** chooses the translation language, as on the
   Translation page.

The two columns are not lined up sentence by sentence. The translation often
arrives a moment after the English, so it may sit a line lower.

## Presentation mode

Presentation mode is for a big screen. The bar at the top is hidden and the
text is larger. You can also open it directly at `/present`.

![Presentation mode: large captions, no controls.](images/viewer-present.png){width=100%}

To leave presentation mode, press `Esc` or move the mouse and click the **×**
in the corner.

::: tip
For a screen that nobody will touch, such as a TV in the lobby, use the
**Screen (kiosk display)** link from the panel instead. It has no controls at
all and picks the language by itself. See [Viewer links](#viewer-links).
:::

## On a phone

The viewer pages are made for phones held upright (portrait). The pictures
below are from a phone-sized screen.

![The chooser page on a phone.](images/viewer-phone-chooser.png){width=38%}
![The Translation page on a phone.](images/viewer-phone-translation.png){width=38%}

On the Translation page:

1. **Language picker.** Tap it to choose a language.
2. **Keep screen awake.** Tap it so the phone does not go dark during a
   long session. Worth telling the room about.
3. **Presentation mode.** Fills the screen with the captions.

::: note
On a narrow phone, the **A−** and **A+** buttons can end up past the right
edge of the screen. Turn the phone sideways (landscape) to reach them, set the
size, then turn it back. The phone remembers the size.
:::

![The Both page on a phone: the two columns stack.](images/viewer-phone-both.png){width=38%}

On a phone, the Both page puts the English above the translation instead of
side by side.

## What to tell the room

A short announcement works well:

> Live captions are on your phone. Join the Wi-Fi, then scan the QR code or
> open the link. Tap **Translation** and pick your language. Tap the sun
> button to keep your screen awake.

Put the QR code from the **Viewer links** section on a slide or a printed
card.

::: warning
The viewer pages are plain `http://` and have no password. That is fine on a
venue network: they carry no secrets, only the captions. But never make the
Relay computer reachable from the public internet.
:::
