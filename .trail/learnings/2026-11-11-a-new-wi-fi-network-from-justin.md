---
title: "A new Wi-Fi network from the setup page was lost because the save overflowed the Wi-Fi event task's stack"
author: Justin
date: 2026-11-11
branch: panels-122-crowpanel
cost: { minutes: 7, tokens: 371022 }
id: l-0eebf1d4
scope: repo
status: active
src: agent
touches:
  - firmware/relay-display/main/provision.cpp
---

## Decided
- The setup page's save of a new network runs from the address callback on the system event task. With the 2304-byte default, loading and saving the settings blob there overflowed the stack, the panel restarted before the save, and it came back on the old network. Fixed by raising CONFIG_ESP_SYSTEM_EVENT_TASK_STACK_SIZE to 4096 and making the candidate blob static. Confirmed on the CrowPanel bench (2026-11-11): new network saved and joined.
- Closing the setup network after a failed attempt now rejoins the stored network; before, the station stayed off with the refused configuration.

## Watch out
- The symptom looked like "the old Wi-Fi is kept"; only the serial log showed "stack overflow in task sys_evt". Anything new run from net callbacks (on_address, on_associated, on_disconnected) runs on that task.

In the code: firmware/relay-display/main/provision.cpp (attempt_succeeded, close_setup), sdkconfig.defaults.
