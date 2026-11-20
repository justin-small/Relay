---
title: "relay-cloud swaps the sign-in code for tokens: every service redirects to cloud.relaysystems.dev, which holds the client secrets"
author: Justin
date: 2026-11-17
branch: blackmagic-432-433
cost: { minutes: 6, tokens: 24773 }
id: l-26e65eda
scope: repo
status: active
replaces: l-18cc9bfc
src: agent
touches:
  - app/oauth.py
  - app/modules/youtube/service.py
  - docs/reference/oauth-apps.md
---

## Decided
Justin, 2026-11-17: every OAuth sign-in (Google/YouTube, Facebook, later Restream) redirects back to relay-cloud at cloud.relaysystems.dev, and relay-cloud itself exchanges the code for tokens using the client secret it keeps as a Worker secret. This replaces the device-code flow (2026-11-16) and reverses the earlier "no broker, Relay never sees a church's sign-in" choice. Restream is on hold.

Registrations:
- Google: relaysystems.dev verified (DNS TXT on the Cloudflare zone, owner app@relaychurchsystems.com), authorized domain on project Relay (hazel-strand-514918-k3). "Web application" client "Relay (web)", redirect https://cloud.relaysystems.dev/oauth/google/callback. Its ID and secret are relay-cloud Worker secrets GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET, not GitHub secrets.
- Meta: app "Relay" (ID 1141075621620827), use case "Manage everything on your Page", app domains relaysystems.dev and relaychurchsystems.com, legal pages on relaychurchsystems.com, Web OAuth with strict mode, redirect https://cloud.relaysystems.dev/oauth/facebook/callback. Login from devices off.

## Tried and ruled out
- Keeping the secret in release builds and having each install exchange the code with PKCE (so the cloud never sees tokens): offered, Justin chose the cloud exchange.
- meta_devtools' devtools_app_create: its confirmation never reached the user in the desktop app and returned "declined" twice; the app was made in Chrome.
- No API creates a Google consumer OAuth client or edits authorized domains; both are console-only.
- wrangler from Claude's Bash or the `!` prompt: non-interactive, so `wrangler login` cannot run there; run it in the Terminal panel.

## Watch out
- Tokens now pass through relay-cloud: the privacy policy on relaychurchsystems.com must say so, and relay-cloud must hand tokens to the install without storing or logging them.
- The relaysystems.dev WAF allow-list blocks new relay-cloud paths until a rule allows them.
- Meta Advanced Access needs a Relay business portfolio; the account's only portfolio belongs to a church.
- Site Verification API with ADC needs a quota project: send x-goog-user-project.

In the code: app/oauth.py, app/modules/youtube/service.py, docs/reference/oauth-apps.md.
