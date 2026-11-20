---
title: Services sign in to Relay with the device-code flow through one core helper; the cloud broker waits for Restream
author: Justin
date: 2026-11-16
branch: oauth-device-signin
id: l-1cab3009
scope: repo
status: active
replaces: l-6341bb1d
src: agent
touches:
  - app/netsock.py
  - app/templates/admin.html
  - docs/modules.md
  - tests/test_modules.py
---

## Decided
- Justin, 2026-11-16, issues 252 and 304: YouTube and Facebook connect with OAuth device sign-in (the operator enters a code at google.com/device or facebook.com/device). No relay-cloud broker yet. It gets built with Restream (253), which has no device flow. The broker would cost about nothing on Cloudflare (a few requests per sign-in, well inside the free tier); it was deferred because nothing needs it yet.
- One core helper that knows no service. Each module describes its service as a Provider and keeps an Account. Core can't import modules and modules can't share code unless named in `uses`, so a shared helper had to be core.
- Tokens are one JSON value in the module's own secret setting, sealed in config.json like the OpenAI key. A refreshed access token stays in memory, so config.json isn't rewritten hourly. It is written only on connect, disconnect and a rotated refresh token.
- Stacked PRs: helper (304) first, then the YouTube module, then Facebook Pages.
- Relay's Google and Meta apps don't exist yet. Justin asked for a setup walkthrough.

## Tried and ruled out
- OS keychain for tokens: no headless/Pi fallback.
- Loopback redirect: needs a browser on the Relay computer itself.

## Watch out
- Google refresh tokens expire after 7 days while the app is in Testing; publish it.
- Facebook gives no refresh token. Its Page token never expires, but it is revoked when the person loses the Page role or changes their password.
- The YouTube Data API has 10,000 units a day per project, shared by every install. Poll slowly.

In the code: app/oauth.py (Provider, Account, add_routes), netsock.fetch, app/static/oauth.js (RelayOAuth.mount), docs/reference/oauth-apps.md, tests/test_oauth.py. oauth.py is in CORE_FILES in tests/test_modules.py.
