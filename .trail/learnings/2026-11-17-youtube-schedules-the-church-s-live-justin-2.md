---
title: "YouTube schedules the church's live streams and writes each one's link into a Planning Center Publishing episode the operator picks"
author: Justin
date: 2026-11-17
branch: youtube-pco-publishing
cost: { minutes: 13, tokens: 192477 }
id: l-e5414e27
scope: repo
status: active
replaces: l-cd52f104
src: agent
touches:
  - app/modules/youtube/schedule.py
  - app/modules/youtube/static/ytschedule.js
  - tests/test_youtube_schedule.py
---

## Decided
Justin, 2026-11-17, issue 411:
- Relay creates YouTube broadcasts from weekly rules and from the plans of followed service types, binds them to a stream key, and has an operator setting for privacy.
- The operator picks a Publishing channel and episode. The broadcast is chosen by the episode's day and time, as the next broadcast, or by hand. The link is sent as soon as the broadcast is scheduled, and again on Send.
- This lives in the YouTube module, which uses planning_center.

## Tried and ruled out
- Keeping the read-only scope: Justin wanted creation in the same piece of work.
- The channel's default_video_url: Church Center reads the link from each episode time.

## Watch out
- Checked on FAC's real accounts: the link lives on the EpisodeTime's video_url. A PATCH returns 200 and Planning Center builds the embed itself. Episodes on the "Live" channel recur, and their time keeps last week's date, so match on weekday and time of day.
- liveStreams with part=cdn returns the stream key itself, so never ask for that part.
- liveBroadcasts?broadcastStatus=upcoming still lists abandoned broadcasts from years ago (FAC has one from 2019), so drop anything more than a day past.
- Google's device flow refuses youtube.force-ssl, so it asks for youtube instead. Relay's Google app needs force-ssl under Data access.
- Creating a broadcast costs 50 quota units and binding it another 50.

In the code: app/modules/youtube/schedule.py; Client.channels/episodes/set_episode_video in planning_center/planningcenter.py; netsock.fetch json_body/method.
