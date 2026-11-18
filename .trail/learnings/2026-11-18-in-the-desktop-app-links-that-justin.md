---
title: "In the desktop app, links that open a new window go to a second Relay window for panel pages and to the default browser for anything else"
author: Justin
date: 2026-11-18
branch: alerts-patchbay
cost: { minutes: 2, tokens: 77869 }
id: l-28f8640c
scope: repo
status: active
src: agent
touches:
  - desktop/src-tauri/src/main.rs
---

## Decided
The system webviews drop target="_blank" and window.open unless the shell answers, so every "opens in a new tab" link in the panel (the board Edit button, viewer links) did nothing in the desktop app. The shell now answers new-window requests: a page on the loopback proxy opens in another Relay window built from the opener's webview configuration (so it shares the sign-in); http/https/mailto anywhere else opens in the default browser. Only the main window hides to the tray on close; extra panel windows close.

## Tried and ruled out
An on_navigation handler to send plain same-window external links to the browser: wry calls it for iframe navigations too, so it would break the crew resources iframes and the overlay preview. Not added.

## Watch out
The main window is no longer created from the config alone ("create": false); it is built in setup after the proxy starts, because the handler needs the proxy port. Not yet tried in a running app as of 2026-11-18.

In the code: desktop/src-tauri/src/main.rs on_new_window / panel_window, tauri.conf.json main window "create": false.
