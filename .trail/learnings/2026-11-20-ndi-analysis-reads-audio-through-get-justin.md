---
title: "NDI analysis reads audio through get_all_read_data, and a test rig that sends and receives in one process measures wrong frame rates"
author: Justin
date: 2026-11-20
branch: shared-pickers-546
cost: { minutes: 21, tokens: 300570 }
id: l-82a96be1
scope: repo
status: active
src: agent
touches:
  - app/modules/ndi/analysis.py
  - tests/test_ndi_analysis.py
  - app/modules/ndi/runtime.py
---

## Decided
Issue 300, Justin (2026-11-19): include the CloudCast Scope scopes (waveform, parade, vectorscope, histogram, meters, R128), not just stream health. Data rate is the decoded picture size, because cyndilib reports no network bytes; dropped frames come from the SDK's own get_performance_data. SCTE-104 and A/V sync were left out: they need VANC and timestamps the receiver does not pass on.

## Tried and ruled out
Audio meter via AudioRecvFrame.get_array(): it does not exist, so the old viewer meter silently never worked. The real call is get_all_read_data()[0], shaped (channels, samples).

## Watch out
Sending and receiving NDI in one Python process starves both (cyndilib holds the GIL in receive), giving 1 to 5 fps. Run the test sender as a separate process. Even then Playwright plus the server on one Mac read about 10 to 17 fps of a 30 fps sender, so a low reading from that rig is not proof of a fault. Sender product metadata (recv_metadata) never arrived from Relay's own sender: unproved on a real camera.
In the code: app/modules/ndi/runtime.py (_audio_level, _metadata), app/modules/ndi/analysis.py.
