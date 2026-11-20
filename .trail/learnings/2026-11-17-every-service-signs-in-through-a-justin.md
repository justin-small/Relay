---
title: "Every service signs in through a relay-cloud redirect at cloud.relaysystems.dev, replacing the device-code flow"
author: Justin
date: 2026-11-17
branch: blackmagic-432-433
cost: { minutes: 1, tokens: 2138 }
id: l-18cc9bfc
scope: repo
status: active
replaces: l-1cab3009
src: agent
touches:
  - app/oauth.py
  - app/modules/youtube/service.py
  - docs/reference/oauth-apps.md
---

## Decided
Justin, 2026-11-17: every OAuth sign-in (Google/YouTube, Facebook, later Restream) uses a redirect back to relay-cloud at cloud.relaysystems.dev, which passes the one-time code back to the install. This replaces the device-code flow chosen on 2026-11-16. Restream is on hold.

Registrations:
- Google: relaysystems.dev verified (DNS TXT on the Cloudflare zone, owner app@relaychurchsystems.com) and an authorized domain on project Relay (hazel-strand-514918-k3). "Web application" client "Relay (web)", redirect https://cloud.relaysystems.dev/oauth/google/callback.
- Meta: app "Relay" (ID 1141075621620827), use case "Manage everything on your Page", app domains relaysystems.dev and relaychurchsystems.com, legal pages on relaychurchsystems.com, Web OAuth with strict mode, redirect https://cloud.relaysystems.dev/oauth/facebook/callback. Login from devices off.

## Tried and ruled out
- meta_devtools' devtools_app_create: its confirmation never reached the user in the desktop app and returned "declined" twice; the app was made in Chrome.
- No API creates a Google consumer OAuth client or edits authorized domains; both are console-only.

## Watch out
- The install must hold the PKCE verifier and exchange the code itself, so relay-cloud only ever sees an unusable one-time code (the reason the broker was ruled out before). The privacy policy must say the cloud carries it.
- The relaysystems.dev WAF allow-list blocks new relay-cloud paths until a rule allows them.
- Meta Advanced Access needs a Relay business portfolio; the account's only portfolio belongs to a church.
- Site Verification API with ADC needs a quota project: send x-goog-user-project.

In the code: app/oauth.py, app/modules/youtube/service.py, docs/reference/oauth-apps.md.
