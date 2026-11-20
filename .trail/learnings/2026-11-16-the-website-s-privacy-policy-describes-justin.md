---
title: "The website's privacy policy describes exactly what Relay sends off the church's computer, so it must change when that does"
author: Justin
date: 2026-11-16
branch: site-legal-pages
cost: { minutes: 5, tokens: 62221 }
id: l-01943029
scope: repo
status: active
src: agent
touches:
  - site/public/data-deletion.html
  - site/public/index.html
  - site/public/legal.css
  - site/public/privacy.html
  - site/public/terms.html
  - ".github/workflows/guide.yml"
  - ".github/workflows/release.yml"
  - ".github/workflows/tests.yml"
  - app/modules/youtube/service.py
  - app/oauth.py
---

## Decided
- Justin, 2026-11-16: publish privacy, terms and data-deletion pages on relaychurchsystems.com for Google's consent screen (YouTube, #252) and, later, Meta's review. Written by the assistant, not a lawyer; Justin approved deploying them as drafted.
- The privacy policy names every place Relay sends data (OpenAI, Planning Center, YouTube, Facebook "when available", Relay's cloud for remote access and remote intercom, the updater) and says the YouTube sign-in stays encrypted on the church's computer. It carries Google's Limited Use sentence and the YouTube ToS / Google Privacy links, which Google's review looks for.

## Tried and ruled out
- A governing-law clause in the terms: left out because the legal entity (named "Relay Church Systems") isn't settled.

## Watch out
- A new outbound service, a new Google or Meta scope, or anything new sent to relay-cloud makes the privacy policy wrong until it is updated. Google reviewers compare the scopes requested with this page.
- The data-deletion page quotes app labels ("Disconnect", "Release this address"). Rename those and the page goes stale.
- The Facebook sections describe a module that doesn't exist yet; reword them when it ships.
- The pages are live before the PR merges: the site deploys from a local checkout, not from main.

In the code: site/public/privacy.html, terms.html, data-deletion.html, legal.css; deploy from site/ with `cf deploy public`; claims checked against app/modules/youtube/service.py (SCOPE, liveBroadcasts, videos liveStreamingDetails), app/oauth.py (disconnect revokes), app/users.py (scrypt), app/modules/remote_access/cloud.py (enroll payload).
