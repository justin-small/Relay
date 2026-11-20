---
title: "OAuth sign-in for YouTube and Facebook keeps tokens like the OpenAI key and lands as stacked PRs, helper first"
author: Justin
date: 2026-11-16
branch: obs-studio-module
cost: { minutes: 1, tokens: 2148 }
id: l-6341bb1d
scope: repo
status: active
src: agent
touches:
  - docs/modules.md
---

## Decided
- Issue 252 (YouTube/Facebook live status) is built together with issue 304 (shared OAuth sign-in), decided with Justin on 2026-11-16.
- Tokens are stored in config.json the same way as the OpenAI key: redacted from config.redacted(), never logged, never sent to a browser or the viewer port.
- It lands as stacked PRs: 1) shared sign-in helper (304), 2) YouTube module, 3) Facebook Pages module.
- Justin wants a written walkthrough for creating Relay's Google Cloud and Meta developer apps (none exist yet), including Google OAuth verification and Meta App Review needs: privacy policy and demo video.
- Justin accepts device sign-in plus the relay-cloud broker. The open choice is whether the broker waits for Restream (253), since device sign-in covers YouTube and Facebook with zero Cloudflare load.

## Tried and ruled out
- OS keychain for tokens: no headless/Pi fallback, and config.json already holds secrets safely.
- One big PR: harder to review. Stacked PRs match how the ProPresenter work landed.

## Watch out
- Broker cost estimate given to Justin: about 3–4 Worker requests per sign-in. Refresh through the cloud only for providers whose secret must stay server-side, about 1 request per install per hour per service. That stays inside the Workers free tier (100k requests a day). A new relay-cloud route needs a WAF allow rule.
- Google refresh tokens expire after 7 days while the app is in "Testing", so the app must be published.

In the code: relay-cloud Worker lives in ../relay-cloud-gh (wrangler.jsonc, D1 binding DB). Modules go in app/modules/<id>/ per docs/modules.md.
