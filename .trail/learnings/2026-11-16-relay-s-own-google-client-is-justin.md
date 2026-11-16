---
title: "Relay's own Google client is added to release builds from Actions secrets, and Relay's HTTPS trusts certifi's roots"
author: Justin
date: 2026-11-16
branch: youtube-builtin-client-and-certs
cost: { minutes: 20, tokens: 153169 }
id: l-240eaf8a
scope: repo
status: active
src: agent
touches:
  - site/public/data-deletion.html
  - site/public/index.html
  - site/public/legal.css
  - site/public/privacy.html
  - site/public/terms.html
  - app/netsock.py
  - app/modules/youtube/service.py
  - tools/release_tools.py
  - ".github/workflows/release.yml"
  - tests/test_release_tools.py
---

## Decided
- Justin, 2026-11-16: the YouTube client ("TVs and Limited Input devices") stays out of the public repository and goes into release builds from Actions secrets, as OBS Studio does. A copy run from source, a fork's build and a headless install from a checkout use the church's own app from the module's Settings.
- Relay's own HTTPS (netsock: urllib openers, http.client, open_tcp ssl=True, wss websockets) verifies with the system roots plus certifi's when the caller passes no context. Verification stays on.
- Tested end to end against real Google and OBS on 2026-11-16 with a throwaway Relay: device sign-in, channel name, live state, start time, viewers (1), and Disconnect revoking at Google all worked.

## Tried and ruled out
- Committing the client in the source (gcloud and rclone do): Google allows it for this client type, but it would be lifted from the public repo and keep tripping gitleaks.
- A relay-cloud broker holding the secret: Relay would then see every church's sign-in, against the privacy policy; still deferred to the Restream work.

## Watch out
- Without certifi, python.org's macOS Python has no root store: every Google request failed with CERTIFICATE_VERIFY_FAILED, shown as "Could not reach YouTube".
- OBS linked to a YouTube account took about a minute after StartStream before it streamed; Relay reads "not streaming" meanwhile.
- The in-app browser refuses the panel's self-signed certificate; run the same RELAY_HOME with uvicorn on plain http://localhost to see it.

In the code: app/netsock.py tls_context(); app/modules/youtube/service.py BUILTIN from ._client; tools/release_tools.py oauth-clients; .github/workflows/release.yml "Add Relay's Google client"; secrets YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET; docs/reference/oauth-apps.md.
