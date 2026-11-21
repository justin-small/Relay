# Wireless mics (#153)

> **Unproven on hardware.** The Shure part was written from Shure's published command strings and the MIT-licensed
> notes in stoatworks-labs/Dante-BabelBox, and tested only against a stand-in receiver on loopback
> (`tests/test_wireless_mics.py`). No Shure receiver has been available. Check each reading against the receiver's own
> display before relying on it, and prove it on a real QLX-D, ULX-D or SLX-D before building more on it.

The `wireless_mics` module (Sound section, off until switched on) shows battery, signal, audio level and mute for Shure
receivers, lists mics and in-ear packs that have no network, and ties each one to a Planning Center position so its card
shows who is scheduled there.

## What is in the first slice

- **Shure receivers** over TCP 2202 (`app/modules/wireless_mics/shure.py`): `< GET 1 BATT_CHARGE >`, `< SET 1 METER_RATE 00500 >`,
  replies and unprompted reports `< REP ... >`, meters `< SAMPLE 1 ALL AX 090 030 >` (RF is the number minus 128 in dBm, audio is
  0-50). Up to four channels per receiver, sixteen receivers.
- **Manual mics and in-ear packs**: a name, a kind and a position, with no readings and no made-up telemetry.
- **Positions**: each card picks a Planning Center position (`position:pco:<team>/<position>`, kept with its saved name and
  renamed when Planning Center renames it). The scheduled person is found by position name on the followed plan, so it
  needs the Planning Center module and a followed plan.
- **Warnings** on the page: low battery (default 20%, 10% is shown as bad), weak signal (default below -85 dBm), muted,
  receiver offline.

## Not yet

Sennheiser EW-DX, raising an Alert on a warning, cards on `/screen`, the gear list on the Network page, and Axient Digital
(its channels map to several transmitter slots). Volunteer names and photos stay on the admin page; no audience page shows them.

## Things to check on a real receiver

- That `SET <n> METER_RATE` is accepted per channel (BabelBox used channel 0, which Shure's bulletin does not show for SET).
- That the keys `TX_TYPE`, `BATT_CHARGE`, `BATT_RUN_TIME`, `AUDIO_MUTE` and `CHAN_NAME` are spelled this way on every model.
- What a receiver sends for a channel with no transmitter, and for an unknown battery (the code reads 255 and 65535 as unknown).
- Whether the 0-50 audio meter should be shown as 0-100 straight.
