# Allen & Heath Avantis: live meters over AH-Net (findings, 2026-11-22)

Status: **proved on a real Avantis (firmware V2.03, 96-input update), read-only.** The stream and its frame format
are decoded. Which meter point is which, and the exact dB zero point, are not yet confirmed. Nothing here is in the
app yet; it belongs in the Audio Mixers module (#150), behind the same `ahnet` switch that already reads faders.

Method: a packet capture of Avantis Director talking to the desk, then the subscription replayed from a script
(hello + meter subscribe only, never a write). Prototype viewer: `docs/avantis-meters/` (`live_server.py`, `live.html`).

## Where the data comes from

| Port | What | Meters? |
|---|---|---|
| 51325 | A&H's published MIDI protocol | No. It pushes fader, mute, name and colour changes, answers name/colour asks, and has no meter message. |
| 51321 | AH-Net, Director's own protocol (what `ah.py` already uses for fader subscribe and scene names) | **Yes.** |

## Subscribe (what Director sends, replayed)

1. TCP 51321, send `ahnet_hello()` (already in `ah.py`).
2. Send the manager frame `F0 00 01 00 00 00 03 00 04 00 <len> "Metering Sources" 00 F7`
   (`<len>` = name length + 1).
3. Send 11 subscribe frames, k = 0..10:
   `F0 00 01 03 50 00 03 00 06 00 0A 00 00 01 72 10 k 10 k+1 00 00 F7`.

No keep-alive was needed in a 3-minute run (the desk streams continuously). Opcodes `0x1009`/`0x100A` carry one-shot
configuration, not meters; the meter groups are `0x1000`-`0x1008`.

## Frame format

`F0 | w0 | w1 | w2 | w3 | len16 | payload | F7`, each of `w0..w3` a 16-bit big-endian word. A meter frame is
`w1 = 0x0172` (subscriber object), `w2 = 0x0350` (Metering Sources), `w3 = 0x1000 + group`. `w0` is the session number
(it differed between sessions, so do not match on it). Payload: 16-bit big-endian values.

**Frame them by the length field, not by the first `F7`.** Values are raw 16-bit numbers and a byte can equal `0xF7`.
`ahnet_frames` in `app/modules/audio_mixers/ah.py` ends a frame at the first `F7`; that is fine for the small control
frames it reads now but cuts meter frames short (about 4% of frames in a 3-minute log). Cut frames showed up as cells
flashing to "no data".

## Values

- 16-bit signed, **divided by 256 = dB** (256 steps per dB; consistent with the fader law in the Director gain table).
  `0xA000` (-96 dB) is the silent floor. The zero point is inferred, not confirmed.
- `0x0000` and `0xFFFF` are filler/separator slots, not levels.
- Rate: about 50 frames a second per group, about 4 KB per frame set.

## Groups

| Group | Values | Layout | Meaning |
|---|---|---|---|
| 0 | 480 | 96 blocks of 5 | **Inputs 1-96**, block n = input n+1. Five points per input (identical on an input with no processing). |
| 1 | 384 | 96 blocks of 4 | The same 96 inputs: two level points, a filler slot, and one other measure (unidentified). |
| 2 | 504 | 56 blocks of 8 + separator | **Mix buses** (inferred, see below). |
| 4, 5, 6, 7, 8 | 320, 256, 64, 112, 48 | blocks of 8 | Not identified (FX / UFX / monitor candidates). Group 3 does not stream. |

Proved on input 12 (named "Minister"): with only that channel carrying audio, block 11 of groups 0 and 1 was the
strongest, and 18 blocks of group 2 followed it (correlation 0.96-0.99). Channel 61 (block 60) showed real audio that
**did not follow the fader**, so groups 0 and 1 are **pre-fader** input meters. A post-fader input meter was not found.

### Group 2 mix map (inferred, not confirmed)

Names read over MIDI (SysEx name ask, MIDI channel 13: groups 1-10; MIDI channel 14: aux 1-10 at numbers 0-9 and
stereo aux 1-4 at 64-67). Mapping:

- blocks 0-9: groups 1-10
- blocks 10-19: aux 1-10, mono (In MON, Out MON, Minister, Sub, TB&Prod, TB, TB Bus, Min Mics, Vox1 bus, Vox2 bus)
- blocks 20-43: stereo aux 1-12 as L/R pairs from block 20 (FrntFill, LDR IEM, WIEM 1, WIEM 2, then unnamed)
- blocks 44-45: main L/R; blocks 46-55 unmapped

Evidence: the blocks carrying input 12 were 10, 11, 12 and 17 (Min Mics, the closest to a mic mix for it) plus
identical even-aligned pairs 20/21, 22/23, 24/25, 26/27, 28/29, 30/31. Desk structure per the operator: 10 groups,
3 mono aux, 12 stereo aux, stereo L/R main; FX has 8 channels, UFX 16 (their meter blocks are not found).

## Addresses on the desk (MIDI, base channel 12)

Inputs 1-96 are MIDI channel 12, numbers 0-95 (the current `AH.address` caps inputs at the model's 64).

## Still to do

1. Confirm the input map with a mute or fader test on one channel while the viewer runs (pre-fader points do not
   move; post-mute and bus points do).
2. Find the FX (8) and UFX (16) meter blocks, and a post-fader input point if one exists.
3. Confirm the dB zero point against a known level.
4. Build it as part of the Audio Mixers module: length-based framing, the subscribe above behind the `ahnet`
   setting, and a meters source for the signal map. Note the 96-input update when sizing `Model`.
5. Do not use Director on the live desk to probe (it was connected to the live mixer during this work, and one
   click on a strip went out before it was stopped).

Reference material used: `Documents/avantis/API_PROTOCOL.md`, `gain_db_table.txt` and `avantis_decode.py` (a static
analysis of Director). Its port (51325) and level scale do not match the real desk, but its frame layout does.
