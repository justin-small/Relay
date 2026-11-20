---
title: "YouTube schedules the church's live streams and writes each one's link into a Planning Center Publishing episode the operator picks"
author: Justin
date: 2026-11-17
branch: youtube-pco-publishing
cost: { minutes: 10, tokens: 114756 }
id: l-cd52f104
scope: repo
status: active
src: agent
touches:
  - app/modules/youtube/static/youtube.js
  - app/modules/youtube/service.py
  - app/modules/planning_center/planningcenter.py
---

## Decided
Justin, 2026-11-17, issue 411:
- Relay creates YouTube broadcasts as well as reading them, so the YouTube sign-in now asks for a scope that can write. Installs connected before this have read-only access and must press Connect again before they can schedule.
- The dates come from Planning Center plans in followed service types and also from weekly rules set in Relay, such as every Sunday at 10:00.
- The privacy of created broadcasts is an operator setting, Public by default.
- Publishing: Relay lists every channel; the operator picks a channel, then one of its episodes, and Relay writes the YouTube link into that episode.
- Matching a broadcast to an episode: the operator chooses. Offer by scheduled date, the next upcoming broadcast, and a manual pick.
- Relay pushes the link as soon as the broadcast is scheduled, and also on a button.
- The work lives in the YouTube module with uses=("planning_center",). The Publishing API calls go on the Planning Center client.

## Tried and ruled out
- Link only, keeping the read-only scope: Justin wanted creation in the same piece of work.
- Writing the channel's default_video_url: the church's "Live" channel leaves it empty. Church Center reads the link from each episode's time.

## Watch out
- Checked against FAC's real account: the link lives on the EpisodeTime, at /publishing/v2/episodes/{id}/episode_times/{time id}, attribute video_url, not on the Episode. A PATCH of that one attribute returned 200, and Planning Center filled in streaming_service "youtube" and video_embed_code by itself.
- The "Live" channel's episodes ("Sunday Morning at FACTallmadge", "MDWK Service") are recurring, each with one episode time that rolls forward, so Relay rewrites the same time every week.
- Google's device sign-in (a church's own app) does not allow youtube.force-ssl, so that path asks for youtube. relay-cloud's allow-list has only youtube.readonly and youtube.force-ssl. Relay's Google app also needs force-ssl under Data access in Google Cloud, and the privacy policy must mention the new scope.
- Creating a broadcast with liveBroadcasts.insert costs 50 units from the daily quota every install shares.
- YouTube creation has not been run against the real channel yet.

In the code: app/modules/youtube/service.py scopes(), can_write(), CLOUD_SCOPE/DEVICE_SCOPE; app/modules/planning_center/planningcenter.py Client (needs a patch method; use _SSL context).
