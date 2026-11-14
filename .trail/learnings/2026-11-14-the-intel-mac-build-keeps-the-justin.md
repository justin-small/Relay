---
title: "The Intel Mac build keeps the old cryptography 48.0.1 and its Dependabot alerts are dismissed, not fixed"
author: Justin
date: 2026-11-14
branch: intercom-specs-237
cost: { minutes: 1, tokens: 14350 }
id: l-f14cafb6
scope: repo
status: active
src: agent
touches:
  - requirements.txt
  - ".github/workflows/spike-freeze.yml"
---

## Decided
- Justin (2026-11-14): leave the Intel Mac desktop build on cryptography 48.0.1 and dismiss the six open cryptography Dependabot alerts (13, 18-22) as tolerable risk. Every other platform already pins 50.0.0.

## Tried and ruled out
- Building cryptography 50 from source for Intel Mac in the release job: no 49+ Intel wheel exists, so it means a slower, riskier CI build.
- Dropping the Intel Mac desktop target: Intel Mac users would lose the app.

## Watch out
- New cryptography advisories will reopen alerts against the Intel pin; revisit if a macosx_x86_64 wheel ever ships.
- The glib alert (17) comes from Tauri's Linux GTK3 stack (glib 0.18.5); the fix needs glib 0.20+, which Tauri does not support. It was left open pending the owner's call.

In the code: requirements.txt and requirements-release.txt pins; .github/workflows/spike-freeze.yml notes the missing wheel (#179).
