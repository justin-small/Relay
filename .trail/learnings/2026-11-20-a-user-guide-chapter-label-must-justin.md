---
title: "A user guide chapter label must not equal another heading's automatic anchor, or the PDF build fails"
author: Justin
date: 2026-11-20
branch: dante-input-264
id: l-53c58629
scope: repo
status: active
src: agent
touches:
  - docs/guide/src/55-dante.md
---

## Decided
The Dante input chapter uses the label dante-input, not dante.

## Tried and ruled out
Labelling the chapter {#dante}: grep of docs/guide/src found no other explicit {#dante}, yet the build failed with "label dante occurs multiple times", because another chapter's "Dante" heading gets the automatic anchor dante.

## Watch out
Search for the heading text as well as explicit labels before choosing one. The fonts warnings in the CI log are noise; the line starting "error:" is the cause. The failure only shows in CI's User guide check, which runs Pandoc and Typst.

In the code: `docs/guide/src/55-dante.md` (first line).
