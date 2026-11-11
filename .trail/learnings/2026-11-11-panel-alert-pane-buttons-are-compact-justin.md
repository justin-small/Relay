---
title: "Panel Alert pane buttons are compact rectangles sized to their text, with the status line in the smallest font"
author: Justin
date: 2026-11-11
branch: alerts-121
cost: { minutes: 6, tokens: 20398 }
id: l-a4257144
scope: repo
status: active
src: agent
---

## Decided
Justin tried it on the bench CrowPanel and asked for smaller rectangle buttons so more fit, with the "Seen on ..." line as small as possible. Each row is a flex column sized to its content (min height ui_px(64), radius ui_px(6)): the name in the medium font, the status line in the small font, hidden while empty. He approved it with six buttons on a split pane.

## Tried and ruled out
Fixed 84 px rows with the large font for the name and medium for the status, the two placed by alignment: at the 75% UI scale they overlapped vertically and looked squished.

## Watch out
Lay out new two-line rows as a flex column, not with top and bottom alignment inside a fixed height: ui_px scales the height but not the font metrics.

In the code: build_rows and draw_words in firmware/relay-display/main/alert_view.cpp.
