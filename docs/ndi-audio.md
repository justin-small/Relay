# NDI sound (#301)

NDI sound is first-class in Relay: you can see it, hear it, route it, patch it onto the audio bus and send it out. The engine
work (levels, mixing, the audio-only sender, the picture-plus-sound combiner) is Rust (`native/relay-ndi`).

## See it

- **Viewer** (`/ndi/view/<source>`): one bar per channel (up to eight) with a peak hold and the momentary loudness in LUFS.
  Loudness is only measured while a viewer page is open.
- **Multiview** boxes with the meter switched on draw one bar per channel.
- **Sources list**: shows the channel count and marks a source that sends sound but no picture **Audio only**. A moving
  meter would flood the live updates, so levels live on the viewer and multiview.
- A source is called audio only after three seconds of sound with no picture. It is routed, watched and patched like any
  other.

## Hear it

The viewer's **Listen** button plays the sound in that browser (`/ndi/listen/<source>?ch=1,2`): 16-bit stereo PCM over an
ordinary HTTP response, mixed by Rust from the channels you pick (one channel plays on both sides; more than two are shared
between the sides). At most four people listen at once. It uses the low-bandwidth stream, so it costs little. Nothing opens a
sound device on the Relay computer. The browser keeps about 100 to 600 ms of buffer and skips ahead rather than fall behind.

## Route it

A routed output (Going out) takes the sound with the picture by default, as an NDI route does. Pick **Sound from** another
source and the output becomes a Rust combiner: Relay receives the picture of one source and the sound of another and sends
them again as one NDI source. That decodes and re-encodes the picture (the NDI route does not), and sound at another rate than
48 kHz is resampled simply. Proved on real NDI with `native/relay-ndi/python/proof_audio.py`.

## Put it on the bus, send it out

Everything audio goes through the 32-lane bus, so NDI registers an input and an output there (`docs/modules.md`, "The 32-lane
audio bus"):

- **Input `ndi`**: on the Audio bus page patch a lane to an NDI source by its name (as the NDI page lists it); all its
  channels (up to 32) become lanes.
- **Output `ndi`**: route lanes or a destination (a spoken-translation language, alert tones, any module's destination) to an
  output of this kind named, say, `Spanish`; it appears on the network as the audio-only NDI source `Relay - Spanish`, at
  48 kHz, for a mixer, recorder or another Relay to pick up.

Proved against the real NDI runtime with `tools/prove_ndi_bus.py` (a real NDI source arrived as lane blocks, and the output was
received back at the level sent). **Not built:** sound from a page output (signage video or an overlay): Relay's pages are
drawn by a headless browser that captures pictures, not sound, so route the sound from the audio bus instead.

## Alerts

Silence and clipping are warned about in Analysis, and, for the sources you watch, raise the "NDI" stage alert (see
`docs/ndi-analysis.md`, "Watching sources you rely on").
