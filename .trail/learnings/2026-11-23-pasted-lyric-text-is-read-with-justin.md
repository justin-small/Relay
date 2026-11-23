---
title: "Pasted lyric text is read with plain string steps, not patterns, and mockups keep input numeric before it goes into HTML"
author: Justin
date: 2026-11-23
branch: songs-page-633
id: l-c5fe7249
scope: repo
status: active
src: agent
touches:
  - app/modules/propresenter/ppsongparse.py
  - docs/mockups/propresenter-songs/a-workbench.html
---

## Decided
Issue 633, 2026-11-23: CodeQL failed PR 646 with "polynomial regular expression on uncontrolled data" for the section-heading pattern run on pasted lyrics, and "DOM text reinterpreted as HTML" in two mockups. Headings are now recognised with string steps and a 24-character cap; mockup values written into HTML are coerced to numbers.

## Tried and ruled out
Keeping the heading regex and tightening it: optional groups next to \s* stayed flagged. The chord-name pattern was not flagged (no ambiguous repeats), so it stayed.

## Watch out
CodeQL runs on GitHub only and scans docs/mockups too; a mockup that puts an input's value into innerHTML fails the PR. Local tests do not catch either.

In the code: app/modules/propresenter/ppsongparse.py (is_header), docs/mockups/propresenter-songs/a-workbench.html and c-sorter.html.
