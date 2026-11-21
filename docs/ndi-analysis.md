# NDI analysis

The **Analysis** tab on the NDI admin page shows why a feed is stuttering without a separate tool. Choose a source and
press **Analyse**: Relay receives it (through the shared receiver pool, so a viewer or multiview on the same source costs
nothing extra) for as long as the tab is open, and lets go 30 seconds after the page stops asking. The scopes follow the
CloudCast Scope manual, which the issue (#300) names. NDI Analysis is not bundled: its licence does not allow it.

## What it shows

- **Health.** A dot on the source in **Coming in** (while something receives it) and a verdict on the tab: *Healthy*,
  *Uneven*, *Trouble* or *Quiet*. Frame rate more than 0.2% off what the sender declares is amber and more than 1% is red;
  jitter above a quarter of a frame is amber and above half a frame is red; three late frames or gaps in ten seconds, or
  any frame the NDI library itself reports dropped, is red. **Quiet** means no frames for two seconds: a still slide sends
  none, so that is not always a fault.
- **Source.** Type (NDI or NDI|HX, read from the name the sender advertises), host and port, group, picture format,
  sound format, and the sender's own product name, model, version and capabilities (PTZ, recordable, KVM) when it reports them.
- **Frame timing.** Receive interval min, average, max and deviation, measured frame rate against declared, jitter, late
  frames (a gap over twice the frame time), gaps over 150 ms, data rate and frame size. The data rate is the *decoded*
  picture: the NDI library does not report the bytes that crossed the network.
- **Low bandwidth** tests the sender's low-bandwidth stream, as Analysis's `/lowQ` does.
- **Picture** (8-bit code values, legal range 16 to 235): waveform, RGB parade, vectorscope with 75% Rec.709 targets and a
  log luma histogram, plus the manual's readouts (below black, above white, illegal RGB, frame difference, sharpness,
  letterbox and pillarbox, aspect, colour matrix) and the BLACK, FROZEN and GAMUT alarms. The matrix is guessed from the
  height (BT.601 at 576 lines or fewer, else BT.709), as the manual does.
- **Sound**: per-channel RMS (50 ms) with a 1.5 s peak hold, sample peak and true peak (BS.1770-4), clip count, DC offset
  and mute; EBU R128 momentary, short-term and integrated loudness and loudness range; stereo correlation; and a spectrum
  of channel 1. SILENCE, CLIP and PHASE alarms. **Reset loudness** starts the integrated figures again.

## Record a session

**Record frame timing** writes one row per video frame (time, interval, size, flag) to a CSV for 1 to 3600 seconds.
**Download CSV** saves the newest one, to send to whoever runs the network. It needs the `ndi.manage` permission to
start; `ndi.view` can download. Files stay in the state folder under `ndi/analysis/` until the next recording of the same
source replaces the entry in the list.

## Receive rate (#566, fixed by #606)

Relay's receiver used to get about 20 of a 30 fps source's frames, because the Python binding held the GIL inside the
receive call, so the frame rate, late-frame counts and health verdict read worse than the stream really was. The receiver
is now a Rust thread (`native/relay-ndi`) that queues what arrives: a 1080p30 UYVY source on this Mac read 29.99 fps, no late
frames and *Healthy* ("Steady"); the old binding read 13 fps in the same test. The numbers can be trusted again. A reader that
falls eight pictures behind loses the oldest, and `queue_overflow` in the receiver's counts says how many.

## Not done

- SCTE-104 (#560) and audio/video sync (#561): they need the frames' ancillary data and timestamps, which Relay's receiver does not pass on yet.
- The optional alert (#562), and health dots on the viewer and multiview boxes (#563).
- Sender product info comes from NDI's connection metadata. It has not been seen from a real camera yet (Relay's own sender
  does not send it), so check **Sender** against a real device.

## Proved on real NDI

Against Relay's own test sender on this Mac, in another process (no cameras were on the network; 1080p30 UYVY with a
-23 dBFS stereo tone, 2026-11-21: 29.99 fps, integrated -23.0 LUFS, scopes and viewer JPEG all working): frame arrival, the decoded size, the audio
samples (a -23 dBFS tone read -23.0 LUFS integrated and -23.0 momentary), the SDK's dropped-frame counts, and the low-bandwidth
receiver opening. Not yet proved: an HX or hardware sender, a sender's metadata, and a busy network.
