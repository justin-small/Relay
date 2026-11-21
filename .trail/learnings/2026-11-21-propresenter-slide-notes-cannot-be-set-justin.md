---
title: "ProPresenter slide notes cannot be set over the API, macros cannot be given actions, and re-importing a song makes a copy"
author: Justin
date: 2026-11-21
branch: propresenter-files-497
cost: { minutes: 6, tokens: 38020 }
id: l-c0d71840
scope: repo
status: active
src: agent
touches:
  - app/modules/propresenter/ppfile.py
  - tests/test_ppfile.py
---

## Decided
Justin, 2026-11-21, issue 497: tested on ProPresenter 22.9 what the network API can and cannot do, so Relay uses the API where it can and the file import only for songs. Relay's own notes tags (slidetags) reach a song only by writing the song file; the API has no way to write slide notes.

## Tried and ruled out
Setting slide notes, a macro's actions, or deleting a song over the API: no route (macro PUT only renames, recolours or changes the icon; DELETE /presentation/{uuid} gave 404 for library songs).
Updating a song in place by importing it again: ProPresenter makes a copy named "<name>-1" with a new id, so an update is import under a new name, then the operator deletes the old one.

## Watch out
In the code (live on 22.9): create bodies differ from the object shapes. POST /v1/macro_collections {name}, /v1/prop_collections {name}, /v1/timers {name, allows_overrun, countdown|count_down_to_time|elapsed}, /v1/messages (full message with theme {uuid,name,index}), /v1/looks (full look with id.uuid), /v1/playlists {name,type}. Playlist items: PUT /v1/playlist/{id} with an array; a song is {type:"presentation", id.uuid = the song's library uuid, presentation_info.presentation_uuid = same}; header and placeholder items work too. target_uuid gave 404.
Playlists have no DELETE route; a timer deleted over the API (204) still showed in GET /v1/timers. No macro can be created over the API (PUT on an unknown id is 404): macros are made in the app.
The test playlist "Relay test playlist" was left in Justin's workspace.
