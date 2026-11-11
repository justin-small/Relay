# ProPresenter API reference

Public API for controlling and retrieving information from ProPresenter.

Generated on 2026-11-11 from the OpenAPI 3.0.2 description published at <https://openapi.propresenter.com/> (API version 1.0). 231 operations on 194 paths in 22 groups. The machine-readable description is the `swagger.json` file that page loads.

## Connecting

- **Base URL:** `http://localhost:<port>/` on the machine running ProPresenter. Port set in ProPresenter's Network Preferences; the description's default is `50001`.
- **Authentication:** the description defines none (no security schemes).
- **Bodies:** JSON unless an operation says otherwise. A JSON string body is sent quoted, for example `"Hello"`.
- Every example below uses `localhost:50001`; replace host and port with your own.

## Streaming updates (`chunked`)

45 GET operations take a `chunked` query parameter (boolean):

> Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one.

They are marked **streams** in the index. To watch several at once over one connection, use [`POST /v1/status/updates`](#statusupdatesget).

## Groups

- [Announcement](#announcement) (9)
- [Audio](#audio) (21)
- [Capture](#capture) (4)
- [Clear](#clear) (9)
- [Global Groups](#global-groups) (1)
- [Library](#library) (4)
- [Looks](#looks) (8)
- [Macro](#macro) (12)
- [Masks](#masks) (3)
- [Media](#media) (22)
- [Message](#message) (7)
- [Miscellaneous](#miscellaneous) (1)
- [Playlist](#playlist) (30)
- [Presentation](#presentation) (31)
- [Prop](#prop) (14)
- [Stage](#stage) (11)
- [Status](#status) (9)
- [Theme](#theme) (5)
- [Timer](#timer) (12)
- [Transport](#transport) (10)
- [Trigger](#trigger) (6)
- [Video Input](#video-input) (2)

## Index of operations

| Method | Path | Group | What it does |
|---|---|---|---|
| GET | [`/v1/announcement/active`](#announcementactiveget) | Announcement | Requests the currently active announcement presentation. **streams** |
| GET | [`/v1/announcement/active/focus`](#announcementactivefocus) | Announcement | Focuses the currently active announcement presentation. |
| GET | [`/v1/announcement/active/next/trigger`](#announcementactivenexttrigger) | Announcement | Triggers the next cue in the active announcement presentation (if there is one). |
| GET | [`/v1/announcement/active/previous/trigger`](#announcementactiveprevioustrigger) | Announcement | Triggers the previous cue in the currently active announcement presentation (if there is one). |
| GET | [`/v1/announcement/active/timeline`](#announcementactivetimelinestatus) | Announcement | Requests the current state of the active announcement timeline. **streams** |
| GET | [`/v1/announcement/active/timeline/{operation}`](#annoucementactivetimelineoperation) | Announcement | Performs the requested timeline operation for the active announcment presentation. |
| GET | [`/v1/announcement/active/trigger`](#announcementactivetrigger) | Announcement | Retriggers the currently active announcement presentation (starts from the beginning). |
| GET | [`/v1/announcement/active/{index}/trigger`](#announcementactivecuetrigger) | Announcement | Triggers the specified cue in the currently active announcement presentation. |
| GET | [`/v1/announcement/slide_index`](#announcementgetslideindex) | Announcement | Requests the index of the current slide/cue within the currently active announcement. **streams** |
| GET | [`/v1/audio/playlist/active`](#audioplaylistactiveget) | Audio | Requests the currently active audio playlist **streams** |
| GET | [`/v1/audio/playlist/active/focus`](#audioplaylistactivefocus) | Audio | Focuses the active audio playlist. |
| GET | [`/v1/audio/playlist/active/next/trigger`](#audioplaylistactivenexttrigger) | Audio | Triggers the next item in the active audio playlist. |
| GET | [`/v1/audio/playlist/active/previous/trigger`](#audioplaylistactiveprevioustrigger) | Audio | Triggers the previous item in the active audio playlist. |
| GET | [`/v1/audio/playlist/active/trigger`](#audioplaylistactivetrigger) | Audio | Triggers the active audio playlist (restarts from the beginning). |
| GET | [`/v1/audio/playlist/active/{id}/trigger`](#audioplaylistactiveitemtrigger) | Audio | Triggers the specified item in the active audio playlist. |
| GET | [`/v1/audio/playlist/focused`](#audioplaylistfocusedget) | Audio | Requests the currently focused audio playlist **streams** |
| GET | [`/v1/audio/playlist/focused/next/trigger`](#audioplaylistfocusednexttrigger) | Audio | Triggers the next item in the focused audio playlist. |
| GET | [`/v1/audio/playlist/focused/previous/trigger`](#audioplaylistfocusedprevioustrigger) | Audio | Triggers the previous item in the focused audio playlist. |
| GET | [`/v1/audio/playlist/focused/trigger`](#audioplaylistfocusedtrigger) | Audio | Triggers the focused audio playlist. |
| GET | [`/v1/audio/playlist/focused/{id}/trigger`](#audioplaylistfocuseditemtrigger) | Audio | Triggers the specified item in the focused audio playlist. |
| GET | [`/v1/audio/playlist/next/focus`](#audioplaylistnextfocus) | Audio | Focuses the next audio playlist. |
| GET | [`/v1/audio/playlist/previous/focus`](#audioplaylistpreviousfocus) | Audio | Focuses the previous audio playlist. |
| GET | [`/v1/audio/playlist/{playlist_id}`](#audioplaylistget) | Audio | Requests a list of all the audio items in the specified audio playlist. |
| GET | [`/v1/audio/playlist/{playlist_id}/focus`](#audioplaylistfocus) | Audio | Focuses the specified audio playlist. |
| GET | [`/v1/audio/playlist/{playlist_id}/next/trigger`](#audioplaylistnexttrigger) | Audio | Triggers the next item in the specified audio playlist. |
| GET | [`/v1/audio/playlist/{playlist_id}/previous/trigger`](#audioplaylistprevioustrigger) | Audio | Triggers the previous item in the specified audio playlist. |
| GET | [`/v1/audio/playlist/{playlist_id}/trigger`](#audioplaylisttrigger) | Audio | Triggers the specified audio playlist. |
| GET | [`/v1/audio/playlist/{playlist_id}/updates`](#audioplaylistupdateget) | Audio | Requests a chunked data update every time the specified audio playlist changes. **streams** |
| GET | [`/v1/audio/playlist/{playlist_id}/{id}/trigger`](#audioplaylistitemtrigger) | Audio | Triggers the specified item in the specified audio playlist. |
| GET | [`/v1/audio/playlists`](#audioplaylistsget) | Audio | Requests a list with all the configured audio playlists. **streams** |
| GET | [`/v1/capture/encodings/{type}`](#capturemodesget) | Capture | Requests a list of all available capture modes for the capture type (disk, rtmp, resi). |
| GET | [`/v1/capture/settings`](#capturesettingsget) | Capture | Requests the current capture settings. |
| GET | [`/v1/capture/status`](#capturestatusget) | Capture | Requests the current capture status and capture time. **streams** |
| GET | [`/v1/capture/{operation}`](#captureoperation) | Capture | Performs the requested capture operation (start, stop). |
| GET | [`/v1/clear/group/{id}`](#cleargroupget) | Clear | Requests the details of the specified clear group. |
| PUT | [`/v1/clear/group/{id}`](#cleargroupset) | Clear | Sets the details of the specified clear group. |
| DELETE | [`/v1/clear/group/{id}`](#cleargroupdelete) | Clear | Deletes the specified clear group. |
| GET | [`/v1/clear/group/{id}/icon`](#cleargroupiconget) | Clear | Requests the image data for the icon of the specified clear group. |
| PUT | [`/v1/clear/group/{id}/icon`](#cleargroupiconset) | Clear | Sets the custom icon of the specified clear group. |
| GET | [`/v1/clear/group/{id}/trigger`](#cleargrouptrigger) | Clear | Triggers the specified clear group. |
| GET | [`/v1/clear/groups`](#cleargroupsget) | Clear | Requests a list of all the configured clear groups. |
| POST | [`/v1/clear/groups`](#cleargroupcreate) | Clear | Create a clear group with the details specified. |
| GET | [`/v1/clear/layer/{layer}`](#clearlayer) | Clear | Clears the specified layer (audio, props, messages, announcements, slide, media, video_input). |
| GET | [`/v1/find_my_mouse`](#findmymouse) | Miscellaneous | Executes the "Find My Mouse" operation. |
| GET | [`/v1/groups`](#globalgroupsget) | Global Groups | Requests a list of all the configured global groups. |
| GET | [`/v1/libraries`](#librarylistget) | Library | Requests a list of all the configured libraries. |
| GET | [`/v1/library/{library_id}`](#libraryget) | Library | Requests an array of all items in the specified library. **streams** |
| GET | [`/v1/library/{library_id}/{presentation_id}/trigger`](#librarytrigger) | Library | Triggers the first cue of the specified presentation in the specified library. |
| GET | [`/v1/library/{library_id}/{presentation_id}/{index}/trigger`](#librarytriggercue) | Library | Triggers the specified cue of the specified presentation in the specified library. |
| GET | [`/v1/look/current`](#lookgetcurrent) | Looks | Requests the details of the currently live audience look. **streams** |
| PUT | [`/v1/look/current`](#looksetcurrent) | Looks | Sets the details of the currently live audience look. |
| GET | [`/v1/look/{id}`](#lookget) | Looks | Requests the details of the specified audience look. |
| PUT | [`/v1/look/{id}`](#lookput) | Looks | Sets the details of the specified audience look. |
| DELETE | [`/v1/look/{id}`](#lookdelete) | Looks | Deletes the specified audience look from the saved looks. |
| GET | [`/v1/look/{id}/trigger`](#looktrigger) | Looks | Triggers the specified audience look to make it the live/current look. |
| GET | [`/v1/looks`](#lookgetall) | Looks | Requests a list of all configured audience looks, except the live look. **streams** |
| POST | [`/v1/looks`](#lookcreate) | Looks | Creates a new audience look with the specified details. |
| GET | [`/v1/macro/{id}`](#macroget) | Macro | Requests the details of the specified macro. |
| PUT | [`/v1/macro/{id}`](#macroset) | Macro | Sets the details of the specified macro. |
| DELETE | [`/v1/macro/{id}`](#macrodelete) | Macro | Deletes the specified macro. |
| GET | [`/v1/macro/{id}/icon`](#macroiconget) | Macro | Requests the image data for the icon of the specified macro. |
| PUT | [`/v1/macro/{id}/icon`](#macroiconset) | Macro | Sets the custom icon of the specified macro. |
| GET | [`/v1/macro/{id}/trigger`](#macrotrigger) | Macro | Triggers the specified macro. |
| GET | [`/v1/macro_collection/{id}`](#macrocollectionget) | Macro | Requests a specific collection of macros |
| PUT | [`/v1/macro_collection/{id}`](#macrocollectionset) | Macro | Sets the details of the specified macro collection. |
| DELETE | [`/v1/macro_collection/{id}`](#macrocollectiondelete) | Macro | Deletes the specified macro collection and any macros contained within it. |
| GET | [`/v1/macro_collections`](#macrocollectiongetall) | Macro | Requests a list of all collections and their contents **streams** |
| POST | [`/v1/macro_collections`](#macrocollectioncreate) | Macro | Creates a new macro collection with the specified name |
| GET | [`/v1/macros`](#macrogetall) | Macro | Requests a list of all the configured macros. **streams** |
| GET | [`/v1/mask/{id}`](#maskget) | Masks | Requests the details of the specified mask. |
| GET | [`/v1/mask/{id}/thumbnail`](#maskthumbnailget) | Masks | Requests a thumbnail image of the specified mask at the given quality value. |
| GET | [`/v1/masks`](#maskgetall) | Masks | Requests a list of all configured masks. |
| GET | [`/v1/media/playlist/active`](#mediaplaylistactive) | Media | Returns the identifier of the currently active media playlist. **streams** |
| GET | [`/v1/media/playlist/active/focus`](#mediaplaylistactivefocus) | Media | Sets the focus to the active media playlist. |
| GET | [`/v1/media/playlist/active/next/trigger`](#mediaplaylistactivenexttrigger) | Media | Triggers the next item in the active media playlist. |
| GET | [`/v1/media/playlist/active/previous/trigger`](#mediaplaylistactiveprevioustrigger) | Media | Triggers the previous item in the active media playlist. |
| GET | [`/v1/media/playlist/active/trigger`](#mediaplaylistactivetrigger) | Media | Triggers the first item in the active media playlist. |
| GET | [`/v1/media/playlist/active/{media_id}/trigger`](#mediaplaylistactiveitemtrigger) | Media | Triggers the specified item in the active media playlist. |
| GET | [`/v1/media/playlist/focused`](#mediaplaylistfocused) | Media | Returns the identifier of the currently focused media playlist **streams** |
| GET | [`/v1/media/playlist/focused/next/trigger`](#mediaplaylistfocusednexttrigger) | Media | Triggers the next item in the focused media playlist. |
| GET | [`/v1/media/playlist/focused/previous/trigger`](#mediaplaylistfocusedprevioustrigger) | Media | Triggers the previous item in the focused media playlist. |
| GET | [`/v1/media/playlist/focused/trigger`](#mediaplaylistfocusedtrigger) | Media | Triggers the first item in the focused media playlist. |
| GET | [`/v1/media/playlist/focused/{media_id}/trigger`](#mediaplaylistfocuseditemtrigger) | Media | Triggers the specified item in the focused media playlist. |
| GET | [`/v1/media/playlist/next/focus`](#mediaplaylistnextfocus) | Media | Sets the focus to the next media playlist. |
| GET | [`/v1/media/playlist/previous/focus`](#mediaplaylistpreviousfocus) | Media | Sets the focus to the previous media playlist. |
| GET | [`/v1/media/playlist/{playlist_id}`](#mediaplaylistget) | Media | Requests a list of all the media items in the specified media playlist. |
| GET | [`/v1/media/playlist/{playlist_id}/focus`](#mediaplaylistfocus) | Media | Sets the focus to the specified media playlist. |
| GET | [`/v1/media/playlist/{playlist_id}/next/trigger`](#mediaplaylistnexttrigger) | Media | Triggers the next item in the specified media playlist. |
| GET | [`/v1/media/playlist/{playlist_id}/previous/trigger`](#mediaplaylistprevioustrigger) | Media | Triggers the previous item in the specified media playlist. |
| GET | [`/v1/media/playlist/{playlist_id}/trigger`](#mediaplaylisttrigger) | Media | Triggers the first item in the specified media playlist. |
| GET | [`/v1/media/playlist/{playlist_id}/updates`](#mediaplaylistupdateget) | Media | Requests a chunked data update every time the specified media playlist changes. **streams** |
| GET | [`/v1/media/playlist/{playlist_id}/{media_id}/trigger`](#mediaplaylistitemtrigger) | Media | Triggers the specified item in the specified media playlist. |
| GET | [`/v1/media/playlists`](#mediaplaylistsget) | Media | Requests a list of all the configured media playlists. **streams** |
| GET | [`/v1/media/{uuid}/thumbnail`](#mediathumbnailget) | Media | Requests a thumbnail image of the specified media item at the given quality value. |
| GET | [`/v1/message/{id}`](#messageget) | Message | Requests the details of the specified message. |
| PUT | [`/v1/message/{id}`](#messageset) | Message | Sets the details of the specified message. |
| DELETE | [`/v1/message/{id}`](#messaagedelete) | Message | Deletes the specified message. |
| GET | [`/v1/message/{id}/clear`](#messageclear) | Message | Clears / Hides the specified message. |
| POST | [`/v1/message/{id}/trigger`](#messagetrigger) | Message | Triggers / Shows the specified message. |
| GET | [`/v1/messages`](#messagegetall) | Message | Requests a list of all configured messages. **streams** |
| POST | [`/v1/messages`](#messagecreate) | Message | Creates a new message with specified details. |
| GET | [`/v1/playlist/active`](#playlistactiveget) | Playlist | Requests the details of the active playlist. **streams** |
| GET | [`/v1/playlist/active/announcement/focus`](#playlistactiveannouncementfocus) | Playlist | Moves the focus to the currently active playlist for the announcement destination. |
| GET | [`/v1/playlist/active/announcement/trigger`](#playlistactiveannouncementtrigger) | Playlist | Triggers the first item in the currently active playlist for the announcement destination. |
| GET | [`/v1/playlist/active/announcement/{index}/thumbnail/{cue_index}`](#playlistactiveannouncementitemthumbnail) | Playlist | Requests a thumbnail image for the specified item in the currently active playlist for the announcement destination |
| GET | [`/v1/playlist/active/announcement/{index}/trigger`](#playlistactiveannouncementitemtrigger) | Playlist | Triggers the specified item in the currently active playlist for the announcement destination. |
| GET | [`/v1/playlist/active/presentation/focus`](#playlistactivepresentationfocus) | Playlist | Moves the focus to the currently active playlist for the presentation destination. |
| GET | [`/v1/playlist/active/presentation/trigger`](#playlistactivepresentationtrigger) | Playlist | Triggers the first item in the currently active playlist for the presentation destination. |
| GET | [`/v1/playlist/active/presentation/{index}/thumbnail/{cue_index}`](#playlistactivepresentationitemthumbnail) | Playlist | Requests a thumbnail image for the specified item in the currently active playlist for the presentation destination |
| GET | [`/v1/playlist/active/presentation/{index}/trigger`](#playlistactivepresentationitemtrigger) | Playlist | Triggers the specified item in the currently active playlist for the presentation destination. |
| GET | [`/v1/playlist/focused`](#playlistfocusedget) | Playlist | Requests the details of the currently focused playlist. **streams** |
| GET | [`/v1/playlist/focused/next`](#playlistfocusednext) | Playlist | Returns the playlist item that will be triggered by /v1/playlist/focused/next/trigger. |
| GET | [`/v1/playlist/focused/next/trigger`](#playlistfocusednexttrigger) | Playlist | Triggers the next item in the currently focused playlist. |
| GET | [`/v1/playlist/focused/previous`](#playlistfocusedprevious) | Playlist | Returns the playlist item that will be triggered by /v1/playlist/focused/previous/trigger. |
| GET | [`/v1/playlist/focused/previous/trigger`](#playlistfocusedprevioustrigger) | Playlist | Triggers the previous item in the currently focused playlist. |
| GET | [`/v1/playlist/focused/trigger`](#playlistfocusedtrigger) | Playlist | Triggers the first item in the currently focused playlist. |
| GET | [`/v1/playlist/focused/{index}/trigger`](#playlistfocuseditemtrigger) | Playlist | Triggers the specified item in the focused playlist. |
| GET | [`/v1/playlist/next/focus`](#playlistnextfocus) | Playlist | Moves the focus to the next playlist. |
| GET | [`/v1/playlist/previous/focus`](#playlistpreviousfocus) | Playlist | Moves the focus to the previous playlist. |
| GET | [`/v1/playlist/{playlist_id}`](#playlistget) | Playlist | Requests a list of the items in the specified playlist. |
| PUT | [`/v1/playlist/{playlist_id}`](#playlistput) | Playlist | Sets the contents of the specified playlist. |
| POST | [`/v1/playlist/{playlist_id}`](#playlistcreate) | Playlist | Creates a playlist with the specified details underneath the specified playlist or playlist folder. |
| GET | [`/v1/playlist/{playlist_id}/focus`](#playlistfocus) | Playlist | Moves the focus to the specified playlist. |
| GET | [`/v1/playlist/{playlist_id}/next/trigger`](#playlistnexttrigger) | Playlist | Triggers the next item in the specified playlist. |
| GET | [`/v1/playlist/{playlist_id}/previous/trigger`](#playlistprevioustrigger) | Playlist | Triggers the previous item in the specified playlist. |
| GET | [`/v1/playlist/{playlist_id}/trigger`](#playlisttrigger) | Playlist | Triggers the first item in the specified playlist. |
| GET | [`/v1/playlist/{playlist_id}/updates`](#playlistupdates) | Playlist | Requests a chunked data update every time the specified playlist changes. **streams** |
| GET | [`/v1/playlist/{playlist_id}/{index}/thumbnail/{cue_index}`](#playlistitemthumbnail) | Playlist | Requests a thumbnail image for the specified item in the specified playlist |
| GET | [`/v1/playlist/{playlist_id}/{index}/trigger`](#playlistitemtrigger) | Playlist | Triggers the specified item in the specified playlist. |
| GET | [`/v1/playlists`](#playlistgetall) | Playlist | Requests a list of all configured playlists. **streams** |
| POST | [`/v1/playlists`](#playlistcreatetoplevel) | Playlist | Creates a playlist with the specified details. |
| GET | [`/v1/presentation/active`](#presentationgetactive) | Presentation | Requests the details of the currently active presentation. **streams** |
| GET | [`/v1/presentation/active/focus`](#presentationactivefocus) | Presentation | Sets the focus to the active presentation. |
| GET | [`/v1/presentation/active/group/{group_id}/trigger`](#presentationactivegrouptrigger) | Presentation | Triggers the specified group of the active presentation. |
| GET | [`/v1/presentation/active/next/trigger`](#presentationactivenexttrigger) | Presentation | Triggers the next cue of the active presentation. |
| GET | [`/v1/presentation/active/previous/trigger`](#presentationactiveprevioustrigger) | Presentation | Triggers the previous cue of the active presentation. |
| GET | [`/v1/presentation/active/timeline`](#presentationactivetimelinestatus) | Presentation | Requests the current state of the timeline for the currently active presentation. **streams** |
| GET | [`/v1/presentation/active/timeline/{operation}`](#presentationactivetimelineoperation) | Presentation | Performs the requested timeline operation (play, pause, rewind) for the currently active presentation. |
| GET | [`/v1/presentation/active/trigger`](#presentationactivetrigger) | Presentation | Retriggers the active presentation from the start. |
| GET | [`/v1/presentation/active/{index}/trigger`](#presentationactivecuetrigger) | Presentation | Triggers the specified cue of the active presentation. |
| GET | [`/v1/presentation/chord_chart`](#presentationgetchordchart) | Presentation | Requests the current chord chart image (if available) at the given quality value. |
| GET | [`/v1/presentation/chord_chart/updates`](#presentationgetchordchartupdates) | Presentation | Requests a chunked data update every time the chord chart changes. **streams** |
| GET | [`/v1/presentation/focused`](#presentationfocused) | Presentation | Gets the currently focused presentation in the main UI. **streams** |
| GET | [`/v1/presentation/focused/group/{group_id}/trigger`](#presentationfocusedgrouptrigger) | Presentation | Triggers the specified group of the currently focused presentation in the main UI. |
| GET | [`/v1/presentation/focused/next/trigger`](#presentationfocusednexttrigger) | Presentation | Triggers the next cue of the currently focused presentation in the main UI. |
| GET | [`/v1/presentation/focused/previous/trigger`](#presentationfocusedprevioustrigger) | Presentation | Triggers the previous cue of the currently focused presentation in the main UI. |
| GET | [`/v1/presentation/focused/timeline`](#presentationfocusedtimelinestatus) | Presentation | Requests the current state of the timeline for the currently focused presentation in the main UI. **streams** |
| GET | [`/v1/presentation/focused/timeline/{operation}`](#presentationfocusedtimelineoperation) | Presentation | Performs the requested timeline operation (play, pause, rewind) for the currently focused presentation in the main UI. |
| GET | [`/v1/presentation/focused/trigger`](#presentationfocusedtrigger) | Presentation | Triggers the currently focused presentation in the main UI. |
| GET | [`/v1/presentation/focused/{index}/trigger`](#presentationfocusedcuetrigger) | Presentation | Triggers the specified cue of the currently focused presentation in the main UI. |
| GET | [`/v1/presentation/next/focus`](#presentationnextfocus) | Presentation | Sets the focus to the next presentation. |
| GET | [`/v1/presentation/previous/focus`](#presentationpreviousfocus) | Presentation | Sets the focus to the previous presentation. |
| GET | [`/v1/presentation/slide_index`](#presentationgetslideindex) | Presentation | Requests the index of the current slide/cue within the currently active presentation. **streams** |
| GET | [`/v1/presentation/{uuid}`](#presentationget) | Presentation | Requests the details of the specified presentation. |
| GET | [`/v1/presentation/{uuid}/focus`](#presentationfocus) | Presentation | Sets the focus to the specified presentation. |
| GET | [`/v1/presentation/{uuid}/group/{group_id}/trigger`](#presentationgrouptrigger) | Presentation | Triggers the specified group of the specified presentation. |
| GET | [`/v1/presentation/{uuid}/next/trigger`](#presentationnexttrigger) | Presentation | Triggers the next cue of the specified presentation. |
| GET | [`/v1/presentation/{uuid}/previous/trigger`](#presentationprevioustrigger) | Presentation | Triggers the previous cue of the specified presentation. |
| GET | [`/v1/presentation/{uuid}/thumbnail/{index}`](#presentationthumbnailget) | Presentation | Requests a thumbnail image of the specified cue inside the specified presentation at the given quality value. |
| GET | [`/v1/presentation/{uuid}/timeline/{operation}`](#presentationtimelineoperation) | Presentation | Performs the requested timeline operation (play, pause, rewind) for the specified presentation. |
| GET | [`/v1/presentation/{uuid}/trigger`](#presentationtrigger) | Presentation | Triggers the specified presentation. |
| GET | [`/v1/presentation/{uuid}/{index}/trigger`](#presentationcuetrigger) | Presentation | Triggers the specified cue of the specified presentation. |
| GET | [`/v1/prop/{id}`](#propget) | Prop | Requests the details of the specified prop. |
| PUT | [`/v1/prop/{id}`](#propput) | Prop | Sets the details of the specified prop. |
| DELETE | [`/v1/prop/{id}`](#propdelete) | Prop | Deletes the specified prop. |
| GET | [`/v1/prop/{id}/auto_clear/pause`](#proppause) | Prop | Pauses auto-clear timer for the specified prop. |
| GET | [`/v1/prop/{id}/auto_clear/resume`](#propresume) | Prop | Resumes auto-clear timer for the specified prop. |
| GET | [`/v1/prop/{id}/clear`](#propclear) | Prop | Clears the specified prop. |
| GET | [`/v1/prop/{id}/thumbnail`](#propthumbnailget) | Prop | Requests a thumbnail image of the specified prop at the given quality value. |
| GET | [`/v1/prop/{id}/trigger`](#proptrigger) | Prop | Triggers the specified prop. |
| GET | [`/v1/prop_collection/{id}`](#propcollectionget) | Prop | Requests a specific collection of props |
| PUT | [`/v1/prop_collection/{id}`](#propcollectionset) | Prop | Sets the details of the specified prop collection. |
| DELETE | [`/v1/prop_collection/{id}`](#propcollectiondelete) | Prop | Deletes the specified prop collection and any props contained within it. |
| GET | [`/v1/prop_collections`](#propcollectiongetall) | Prop | Requests a list of all collections and their contents **streams** |
| POST | [`/v1/prop_collections`](#propcollectioncreate) | Prop | Creates a new prop collection with the specified name |
| GET | [`/v1/props`](#propgetall) | Prop | Gets a list of all the props. **streams** |
| DELETE | [`/v1/stage/layout/{id}`](#stagelayoutdelete) | Stage | Deletes the specified stage layout. |
| GET | [`/v1/stage/layout/{id}/thumbnail`](#stagelayoutthumbnailget) | Stage | Requests a thumbnail image of the specified stage layout at the given quality value. |
| GET | [`/v1/stage/layout_map`](#stagelayoutmapget) | Stage | Requests the currently selected stage layout for each configured stage screen. **streams** |
| PUT | [`/v1/stage/layout_map`](#stagelayoutmapset) | Stage | Sets the specified stage message to the corresponding stage screens. |
| GET | [`/v1/stage/layouts`](#stagelayoutsget) | Stage | Requests a list of the configured stage layouts. **streams** |
| GET | [`/v1/stage/message`](#stagemessageget) | Stage | Requests the currently active stage message. **streams** |
| PUT | [`/v1/stage/message`](#stagemessageshow) | Stage | Shows the specified stage message on the configured stage screen. |
| DELETE | [`/v1/stage/message`](#stagemessagehide) | Stage | Hides the currently displayed stage message from the configured stage screen. |
| GET | [`/v1/stage/screen/{id}/layout`](#stagescreenlayoutget) | Stage | Requests the current stage layout for the specified stage screen. |
| GET | [`/v1/stage/screen/{id}/layout/{layout_id}`](#stagescreenlayoutset) | Stage | Sets the specified stage layout for the specified stage screen. |
| GET | [`/v1/stage/screens`](#stagescreensget) | Stage | Requests a list of the configured stage screens. **streams** |
| GET | [`/v1/status/audience_screens`](#audiencescreensstatusget) | Status | Requests the status of the audience screens. **streams** |
| PUT | [`/v1/status/audience_screens`](#audiencescreensstatusset) | Status | Sets the status of the audience screens. |
| GET | [`/v1/status/layers`](#layerstatusget) | Status | Requests the status of all available layers. **streams** |
| GET | [`/v1/status/screens`](#statusscreensget) | Status | Requests the details of all configured screens. **streams** |
| GET | [`/v1/status/slide`](#slidestatusget) | Status | Requests the current/next slide text and image UUIDs. **streams** |
| GET | [`/v1/status/stage_screens`](#stagescreensstatusget) | Status | Requests the status of the stage screens. **streams** |
| PUT | [`/v1/status/stage_screens`](#stagescreensstatusset) | Status | Sets the status of the stage screens. |
| POST | [`/v1/status/updates`](#statusupdatesget) | Status | Aggregates the data from one or more streaming endpoints into a single streaming endpoint. |
| GET | [`/v1/theme/{id}`](#themeget) | Theme | Requests the details of the theme and theme slides. |
| GET | [`/v1/theme/{id}/slides/{theme_slide}`](#themeslideget) | Theme | Requests the details of the specified theme slide within the specified theme. |
| PUT | [`/v1/theme/{id}/slides/{theme_slide}`](#themeslideset) | Theme | Sets the details of the specified theme slide within the specified theme. |
| GET | [`/v1/theme/{id}/slides/{theme_slide}/thumbnail`](#themeslidethumbnailget) | Theme | Requests a thumbnail image of the specified theme slide at the given quality value. |
| GET | [`/v1/themes`](#themegetall) | Theme | Requests a list of all configured themes and theme slides. |
| GET | [`/v1/timer/system_time`](#timergetsystemtime) | Timer | Requests the current system time. **streams** |
| GET | [`/v1/timer/video_countdown`](#timergetvideocountdown) | Timer | Requests the current value of the video countdown timer. **streams** |
| GET | [`/v1/timer/{id}`](#timerget) | Timer | Requests the details of the specified timer. |
| PUT | [`/v1/timer/{id}`](#timerset) | Timer | Sets the details of the specified timer. |
| DELETE | [`/v1/timer/{id}`](#timerdelete) | Timer | Deletes the specified timer. |
| GET | [`/v1/timer/{id}/increment/{time}`](#timerincrement) | Timer | Modifies the time on the specified running timer. |
| GET | [`/v1/timer/{id}/{operation}`](#timeroperation) | Timer | Performs the requested operation on the specified timer (start, stop, reset). |
| PUT | [`/v1/timer/{id}/{operation}`](#timersetandoperation) | Timer | Sets the details of the specified timer and performs the requested operation (start, stop, reset). |
| GET | [`/v1/timers`](#timergetall) | Timer | Requests the details for all configured timers. **streams** |
| POST | [`/v1/timers`](#timercreate) | Timer | Creates a new timer with the specified details. |
| GET | [`/v1/timers/current`](#timergetcurrenttimes) | Timer | Requests the current time for all configured timers. **streams** |
| GET | [`/v1/timers/{operation}`](#timeroperationall) | Timer | Performs the requested operation for all configured timers (start, stop, reset). |
| GET | [`/v1/transport/{layer}/auto_advance`](#transportautoadvanceget) | Transport | Requests the auto-advance status for the specified layer (presentation, announcement). |
| DELETE | [`/v1/transport/{layer}/auto_advance`](#transportautoadvancecancel) | Transport | Cancels the auto-advance for the specified layer (presentation, announcement). |
| GET | [`/v1/transport/{layer}/current`](#transportcurrentmediaget) | Transport | Requests the details of the currently playing content for the specified layer (presentation, announcement, audio). **streams** |
| GET | [`/v1/transport/{layer}/go_to_end`](#transportgotoend) | Transport | Moves to the end on a certain layer |
| GET | [`/v1/transport/{layer}/pause`](#transportpause) | Transport | Pauses the content on the specified layer (presentation, announcement, audio). |
| GET | [`/v1/transport/{layer}/play`](#transportplay) | Transport | Plays the content on the specified layer (presentation, announcement, audio). |
| GET | [`/v1/transport/{layer}/skip_backward/{time}`](#transportskipbackward) | Transport | Moves backward in the content on the specified layer by the specified number of seconds (presentation, announcement, audio). |
| GET | [`/v1/transport/{layer}/skip_forward/{time}`](#transportskipforward) | Transport | Moves forward in the content on the specified layer by the specified number of seconds (presentation, announcement, audio). |
| GET | [`/v1/transport/{layer}/time`](#transporttimeget) | Transport | Requests the current transport time for the specified layer (presentation, announcement, audio). **streams** |
| PUT | [`/v1/transport/{layer}/time`](#transporttimeset) | Transport | Moves to the specified time for the specified layer (presentation, announcement, audio). |
| GET | [`/v1/trigger/audio/next`](#triggeraudionext) | Trigger | Triggers the next item in the currently active audio playlist. |
| GET | [`/v1/trigger/audio/previous`](#triggeraudioprevious) | Trigger | Triggers the previous item in the currently active audio playlist. |
| GET | [`/v1/trigger/media/next`](#triggermedianext) | Trigger | Triggers the next item in the currently active media playlist. |
| GET | [`/v1/trigger/media/previous`](#triggermediaprevious) | Trigger | Triggers the previous item in the currently active media playlist. |
| GET | [`/v1/trigger/next`](#triggernext) | Trigger | Triggers the next cue or item in the currently active playlist or library. |
| GET | [`/v1/trigger/previous`](#triggerprevious) | Trigger | Triggers the previous cue or item in the currently active playlist or library. |
| GET | [`/v1/video_inputs`](#videoinputsget) | Video Input | Requests the contents of the video inputs playlist. |
| GET | [`/v1/video_inputs/{id}/trigger`](#videoinputstrigger) | Video Input | Triggers a video input from the video inputs playlist. |
| GET | [`/version`](#versionget) | Status | Requests the general information about the currently active ProPresenter instance. |

## Announcement

<a id="announcementactiveget"></a>
### GET `/v1/announcement/active`

Requests the currently active announcement presentation.

Operation ID: `announcementActiveGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `announcement` (object)
  - `id` (allOf of object)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `groups` (array of object, required)
    - `name` (string, required)
    - `color` (object, required)
      - `red` (number, required) — The red component of the color, between 0 and 1
      - `green` (number, required) — The green component of the color, between 0 and 1
      - `blue` (number, required) — The blue component of the color, between 0 and 1
      - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.
    - `slides` (array of object, required)
      - `enabled` (boolean, required) — Is this slide enabled
      - `notes` (string, required) — Notes to be showed along with the slide
      - `text` (string, required) — Slide text
      - `label` (string, required) — The label for the slide
      - `color` (object, required)
        - `red` (number, required) — The red component of the color, between 0 and 1
        - `green` (number, required) — The green component of the color, between 0 and 1
        - `blue` (number, required) — The blue component of the color, between 0 and 1
        - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.
      - `size` (object)
        - `width` (integer, required) — The width of the slide, in pixels
        - `height` (integer, required) — The height of the slide, in pixels
  - `has_timeline` (boolean, required) — True if this presentation has an associated timeline
  - `presentation_path` (string) — The path to the presentation file on disk
  - `destination` (enum: `presentation`, `announcements`, required) — The presentation destination: * `presentation` - Presentation layer * `announcements` - Announcements layer Default `"presentation"`.
  - `has_timeline_items` (bool) — true if the presentation has items in its timeline, false otherwise

Example: built from the schema's field examples

```json
{
  "announcement": {
    "id": {
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "name": "Amazing Grace",
      "index": 2
    },
    "groups": [
      {
        "name": "Verse 1",
        "color": {
          "red": 0,
          "green": 0.54,
          "blue": 0.87,
          "alpha": 1
        },
        "slides": [
          {
            "notes": "",
            "text": "Amazing Grace\nHow sweet the sound",
            "label": "Acoustic",
            "color": {
              "red": 0,
              "green": 0.54,
              "blue": 0.87,
              "alpha": 1
            },
            "size": {
              "width": 1920,
              "height": 1080
            }
          }
        ]
      }
    ],
    "presentation_path": "/Users/someuser/Documents/ProPresenter/Libraries/Default/Amazing Grace.pro",
    "destination": "announcements",
    "has_timeline_items": false
  }
}
```

```bash
curl 'http://localhost:50001/v1/announcement/active'
```

<a id="announcementgetslideindex"></a>
### GET `/v1/announcement/slide_index`

Requests the index of the current slide/cue within the currently active announcement.

Operation ID: `announcementGetSlideIndex`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `announcement_index` (object)
  - `index` (integer, required)
  - `presentation_id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item

Example: built from the schema's field examples

```json
{
  "announcement_index": {
    "presentation_id": {
      "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
      "name": "Amazing Grace",
      "index": 2
    }
  }
}
```

```bash
curl 'http://localhost:50001/v1/announcement/slide_index'
```

<a id="announcementactivefocus"></a>
### GET `/v1/announcement/active/focus`

Focuses the currently active announcement presentation.

Operation ID: `announcementActiveFocus`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/announcement/active/focus'
```

<a id="announcementactivetrigger"></a>
### GET `/v1/announcement/active/trigger`

Retriggers the currently active announcement presentation (starts from the beginning).

Operation ID: `announcementActiveTrigger`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/announcement/active/trigger'
```

<a id="announcementactivenexttrigger"></a>
### GET `/v1/announcement/active/next/trigger`

Triggers the next cue in the active announcement presentation (if there is one).

Operation ID: `announcementActiveNextTrigger`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/announcement/active/next/trigger'
```

<a id="announcementactiveprevioustrigger"></a>
### GET `/v1/announcement/active/previous/trigger`

Triggers the previous cue in the currently active announcement presentation (if there is one).

Operation ID: `announcementActivePreviousTrigger`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/announcement/active/previous/trigger'
```

<a id="announcementactivecuetrigger"></a>
### GET `/v1/announcement/active/{index}/trigger`

Triggers the specified cue in the currently active announcement presentation.

Operation ID: `announcementActiveCueTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `index` | path | integer | yes | The index of the cue to use. Respects the selected arrangement of cues. Example `0`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/announcement/active/{index}/trigger'
```

<a id="annoucementactivetimelineoperation"></a>
### GET `/v1/announcement/active/timeline/{operation}`

Performs the requested timeline operation for the active announcment presentation.

Operation ID: `annoucementActiveTimelineOperation`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `operation` | path | enum: `play`, `pause`, `rewind` | yes | The operation to perform on the timeline |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/announcement/active/timeline/{operation}'
```

<a id="announcementactivetimelinestatus"></a>
### GET `/v1/announcement/active/timeline`

Requests the current state of the active announcement timeline.

Operation ID: `announcementActiveTimelineStatus`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `is_running` (boolean) — `true` if the timeline is running.
- `current_time` (number) — The current position of the timeline's playhead.

```bash
curl 'http://localhost:50001/v1/announcement/active/timeline'
```

## Audio

<a id="audioplaylistsget"></a>
### GET `/v1/audio/playlists`

Requests a list with all the configured audio playlists.

Operation ID: `audioPlaylistsGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): array of object

- `id` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item
- `type` (enum: `group`, `playlist`) — Whether this is a playlist folder or an actual playlist.
- `children` (array of object) — The list of child playlists inside this playlist.

Example

```json
{
  "id": {
    "uuid": "ED59503B-394D-4D5D-B0B0-5AE80D0BA025",
    "name": "Playlist Folder",
    "index": 0
  },
  "type": "group",
  "children": [
    {
      "id": {
        "uuid": "C49A250A-3E32-4C26-A220-46D6596426B5",
        "name": "Playlist 1",
        "index": 0
      },
      "type": "playlist",
      "children": []
    },
    {
      "id": {
        "uuid": "D8C5D151-45C9-4CFF-B1F9-74F3B4B5173B",
        "name": "Playlist 2",
        "index": 1
      },
      "type": "playlist",
      "children": []
    }
  ]
}
```

```bash
curl 'http://localhost:50001/v1/audio/playlists'
```

<a id="audioplaylistget"></a>
### GET `/v1/audio/playlist/{playlist_id}`

Requests a list of all the audio items in the specified audio playlist.

A maximum of 100 items will be returned per call. The `start` query parameter can
be used to retrieve multiple pages of items, by setting it to the index of the first
item to return.

Operation ID: `audioPlaylistGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `playlist_id` | path | string | yes | The ID of the playlist, either UUID, name, or index, in order of interpretation and search priority. A path can also be specified to identify playlists within folders. UUID and name matches are searched through the entire playlist tree. Because UUIDs are unique a path is not necessary. A name path is not necessary unless there are multiple playlists with the same name in different folders. A partial path of names can be used as well. Indices only index into the immediate children at each level of the tree so an index path is requred to access playlists within folders if only using indices. A mix of UUIDs, names, and indices can be used for the path if desired. Example (Match playlist by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match playlist by name): `Playlist Name`. Example (Match playlist with a name path): `Folder1/Folder2/Playlist Name`. Example (Match playlist by index): `3`. Example (Match playlist by index path): `2/0/1`. Example (Match playlist by a mixed path): `3C39C433-5C18-4F51-B357-55BB870227C4/Folder Name/0`. |
| `start` | query | integer | no | The starting index in the list. If the index is out of range, the returned items array will be empty. Example `100`. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): array of object

- `id` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item
- `items` (array of any, required)

Example

```json
{
  "id": {
    "uuid": "B238C071-3628-4204-BB19-DDE76DC57CE7",
    "name": "Playlist 3",
    "index": 2
  },
  "items": [
    {
      "id": {
        "uuid": "94A2D4EA-EEA5-48A4-AF6F-850D700C1830",
        "name": "Song 1",
        "index": 0
      },
      "type": "audio",
      "artist": "unknown",
      "duration": 123
    },
    {
      "id": {
        "uuid": "8F40D44E-4018-4552-8803-60DC788DB1CF",
        "name": "Amazing Grace",
        "index": 1
      },
      "type": "audio",
      "artist": "John Newton",
      "duration": 110
    }
  ]
}
```

```bash
curl 'http://localhost:50001/v1/audio/playlist/{playlist_id}'
```

<a id="audioplaylistupdateget"></a>
### GET `/v1/audio/playlist/{playlist_id}/updates`

Requests a chunked data update every time the specified audio playlist changes.

This endpoint can be used to subscribe to changes to a particular playlist.
The response is chunked, where each chunk is a simple string "change".
This string will be sent whenever the audio playlist changes in regards to any of the
data returned by `GET /audio/playlist/{playlist_id}`. The updated playlist data can be
queried by recalling `GET /audio/playlist/{playlist_id}`.

Note that this endpoint will return nothing unless the `chunked` parameter is set to `true`.

Operation ID: `audioPlaylistUpdateGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `playlist_id` | path | string | yes | The ID of the playlist, either UUID, name, or index, in order of interpretation and search priority. A path can also be specified to identify playlists within folders. UUID and name matches are searched through the entire playlist tree. Because UUIDs are unique a path is not necessary. A name path is not necessary unless there are multiple playlists with the same name in different folders. A partial path of names can be used as well. Indices only index into the immediate children at each level of the tree so an index path is requred to access playlists within folders if only using indices. A mix of UUIDs, names, and indices can be used for the path if desired. Example (Match playlist by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match playlist by name): `Playlist Name`. Example (Match playlist with a name path): `Folder1/Folder2/Playlist Name`. Example (Match playlist by index): `3`. Example (Match playlist by index path): `2/0/1`. Example (Match playlist by a mixed path): `3C39C433-5C18-4F51-B357-55BB870227C4/Folder Name/0`. |
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): enum: `change`

Example: built from the schema's field examples

```json
"change"
```

```bash
curl 'http://localhost:50001/v1/audio/playlist/{playlist_id}/updates'
```

<a id="audioplaylistfocusedget"></a>
### GET `/v1/audio/playlist/focused`

Requests the currently focused audio playlist

Operation ID: `audioPlaylistFocusedGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `uuid` (string, required) — The UUID of the item
- `name` (string, required) — The user-defined name of the item
- `index` (integer, required) — The index of the item

Example

```json
{
  "uuid": "ad19b83b-ee4d-4cfe-9280-220cc9330264",
  "name": "Playlist 1",
  "index": 0
}
```

```bash
curl 'http://localhost:50001/v1/audio/playlist/focused'
```

<a id="audioplaylistactiveget"></a>
### GET `/v1/audio/playlist/active`

Requests the currently active audio playlist

Operation ID: `audioPlaylistActiveGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `playlist` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item
- `item` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item

Example

```json
{
  "playlist": {
    "uuid": "ad19b83b-ee4d-4cfe-9280-220cc9330264",
    "name": "Playlist 1",
    "index": 0
  },
  "item": {
    "uuid": "8F40D44E-4018-4552-8803-60DC788DB1CF",
    "name": "Amazing Grace",
    "index": 1
  }
}
```

```bash
curl 'http://localhost:50001/v1/audio/playlist/active'
```

<a id="audioplaylistnextfocus"></a>
### GET `/v1/audio/playlist/next/focus`

Focuses the next audio playlist.

Operation ID: `audioPlaylistNextFocus`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/audio/playlist/next/focus'
```

<a id="audioplaylistpreviousfocus"></a>
### GET `/v1/audio/playlist/previous/focus`

Focuses the previous audio playlist.

Operation ID: `audioPlaylistPreviousFocus`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/audio/playlist/previous/focus'
```

<a id="audioplaylistactivefocus"></a>
### GET `/v1/audio/playlist/active/focus`

Focuses the active audio playlist.

Operation ID: `audioPlaylistActiveFocus`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/audio/playlist/active/focus'
```

<a id="audioplaylistfocus"></a>
### GET `/v1/audio/playlist/{playlist_id}/focus`

Focuses the specified audio playlist.

Operation ID: `audioPlaylistFocus`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `playlist_id` | path | string | yes | The ID of the playlist, either UUID, name, or index, in order of interpretation and search priority. A path can also be specified to identify playlists within folders. UUID and name matches are searched through the entire playlist tree. Because UUIDs are unique a path is not necessary. A name path is not necessary unless there are multiple playlists with the same name in different folders. A partial path of names can be used as well. Indices only index into the immediate children at each level of the tree so an index path is requred to access playlists within folders if only using indices. A mix of UUIDs, names, and indices can be used for the path if desired. Example (Match playlist by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match playlist by name): `Playlist Name`. Example (Match playlist with a name path): `Folder1/Folder2/Playlist Name`. Example (Match playlist by index): `3`. Example (Match playlist by index path): `2/0/1`. Example (Match playlist by a mixed path): `3C39C433-5C18-4F51-B357-55BB870227C4/Folder Name/0`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/audio/playlist/{playlist_id}/focus'
```

<a id="audioplaylistfocusedtrigger"></a>
### GET `/v1/audio/playlist/focused/trigger`

Triggers the focused audio playlist.

Operation ID: `audioPlaylistFocusedTrigger`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/audio/playlist/focused/trigger'
```

<a id="audioplaylistactivetrigger"></a>
### GET `/v1/audio/playlist/active/trigger`

Triggers the active audio playlist (restarts from the beginning).

Operation ID: `audioPlaylistActiveTrigger`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/audio/playlist/active/trigger'
```

<a id="audioplaylisttrigger"></a>
### GET `/v1/audio/playlist/{playlist_id}/trigger`

Triggers the specified audio playlist.

Operation ID: `audioPlaylistTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `playlist_id` | path | string | yes | The ID of the playlist, either UUID, name, or index, in order of interpretation and search priority. A path can also be specified to identify playlists within folders. UUID and name matches are searched through the entire playlist tree. Because UUIDs are unique a path is not necessary. A name path is not necessary unless there are multiple playlists with the same name in different folders. A partial path of names can be used as well. Indices only index into the immediate children at each level of the tree so an index path is requred to access playlists within folders if only using indices. A mix of UUIDs, names, and indices can be used for the path if desired. Example (Match playlist by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match playlist by name): `Playlist Name`. Example (Match playlist with a name path): `Folder1/Folder2/Playlist Name`. Example (Match playlist by index): `3`. Example (Match playlist by index path): `2/0/1`. Example (Match playlist by a mixed path): `3C39C433-5C18-4F51-B357-55BB870227C4/Folder Name/0`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/audio/playlist/{playlist_id}/trigger'
```

<a id="audioplaylistitemtrigger"></a>
### GET `/v1/audio/playlist/{playlist_id}/{id}/trigger`

Triggers the specified item in the specified audio playlist.

Operation ID: `audioPlaylistItemTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `playlist_id` | path | string | yes | The ID of the playlist, either UUID, name, or index, in order of interpretation and search priority. A path can also be specified to identify playlists within folders. UUID and name matches are searched through the entire playlist tree. Because UUIDs are unique a path is not necessary. A name path is not necessary unless there are multiple playlists with the same name in different folders. A partial path of names can be used as well. Indices only index into the immediate children at each level of the tree so an index path is requred to access playlists within folders if only using indices. A mix of UUIDs, names, and indices can be used for the path if desired. Example (Match playlist by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match playlist by name): `Playlist Name`. Example (Match playlist with a name path): `Folder1/Folder2/Playlist Name`. Example (Match playlist by index): `3`. Example (Match playlist by index path): `2/0/1`. Example (Match playlist by a mixed path): `3C39C433-5C18-4F51-B357-55BB870227C4/Folder Name/0`. |
| `id` | path | string | yes | The ID of the required item, either name, index or UUID. Example (Match item by name): `Item Name`. Example (Match item by index): `3`. Example (Match item by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/audio/playlist/{playlist_id}/{id}/trigger'
```

<a id="audioplaylistfocusednexttrigger"></a>
### GET `/v1/audio/playlist/focused/next/trigger`

Triggers the next item in the focused audio playlist.

Operation ID: `audioPlaylistFocusedNextTrigger`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/audio/playlist/focused/next/trigger'
```

<a id="audioplaylistfocusedprevioustrigger"></a>
### GET `/v1/audio/playlist/focused/previous/trigger`

Triggers the previous item in the focused audio playlist.

Operation ID: `audioPlaylistFocusedPreviousTrigger`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/audio/playlist/focused/previous/trigger'
```

<a id="audioplaylistfocuseditemtrigger"></a>
### GET `/v1/audio/playlist/focused/{id}/trigger`

Triggers the specified item in the focused audio playlist.

Operation ID: `audioPlaylistFocusedItemTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the required item, either name, index or UUID. Example (Match item by name): `Item Name`. Example (Match item by index): `3`. Example (Match item by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/audio/playlist/focused/{id}/trigger'
```

<a id="audioplaylistactivenexttrigger"></a>
### GET `/v1/audio/playlist/active/next/trigger`

Triggers the next item in the active audio playlist.

Operation ID: `audioPlaylistActiveNextTrigger`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/audio/playlist/active/next/trigger'
```

<a id="audioplaylistactiveprevioustrigger"></a>
### GET `/v1/audio/playlist/active/previous/trigger`

Triggers the previous item in the active audio playlist.

Operation ID: `audioPlaylistActivePreviousTrigger`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/audio/playlist/active/previous/trigger'
```

<a id="audioplaylistactiveitemtrigger"></a>
### GET `/v1/audio/playlist/active/{id}/trigger`

Triggers the specified item in the active audio playlist.

Operation ID: `audioPlaylistActiveItemTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the required item, either name, index or UUID. Example (Match item by name): `Item Name`. Example (Match item by index): `3`. Example (Match item by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/audio/playlist/active/{id}/trigger'
```

<a id="audioplaylistnexttrigger"></a>
### GET `/v1/audio/playlist/{playlist_id}/next/trigger`

Triggers the next item in the specified audio playlist.

Operation ID: `audioPlaylistNextTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `playlist_id` | path | string | yes | The ID of the playlist, either UUID, name, or index, in order of interpretation and search priority. A path can also be specified to identify playlists within folders. UUID and name matches are searched through the entire playlist tree. Because UUIDs are unique a path is not necessary. A name path is not necessary unless there are multiple playlists with the same name in different folders. A partial path of names can be used as well. Indices only index into the immediate children at each level of the tree so an index path is requred to access playlists within folders if only using indices. A mix of UUIDs, names, and indices can be used for the path if desired. Example (Match playlist by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match playlist by name): `Playlist Name`. Example (Match playlist with a name path): `Folder1/Folder2/Playlist Name`. Example (Match playlist by index): `3`. Example (Match playlist by index path): `2/0/1`. Example (Match playlist by a mixed path): `3C39C433-5C18-4F51-B357-55BB870227C4/Folder Name/0`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/audio/playlist/{playlist_id}/next/trigger'
```

<a id="audioplaylistprevioustrigger"></a>
### GET `/v1/audio/playlist/{playlist_id}/previous/trigger`

Triggers the previous item in the specified audio playlist.

Operation ID: `audioPlaylistPreviousTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `playlist_id` | path | string | yes | The ID of the playlist, either UUID, name, or index, in order of interpretation and search priority. A path can also be specified to identify playlists within folders. UUID and name matches are searched through the entire playlist tree. Because UUIDs are unique a path is not necessary. A name path is not necessary unless there are multiple playlists with the same name in different folders. A partial path of names can be used as well. Indices only index into the immediate children at each level of the tree so an index path is requred to access playlists within folders if only using indices. A mix of UUIDs, names, and indices can be used for the path if desired. Example (Match playlist by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match playlist by name): `Playlist Name`. Example (Match playlist with a name path): `Folder1/Folder2/Playlist Name`. Example (Match playlist by index): `3`. Example (Match playlist by index path): `2/0/1`. Example (Match playlist by a mixed path): `3C39C433-5C18-4F51-B357-55BB870227C4/Folder Name/0`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/audio/playlist/{playlist_id}/previous/trigger'
```

## Capture

<a id="capturestatusget"></a>
### GET `/v1/capture/status`

Requests the current capture status and capture time.

Operation ID: `captureStatusGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `status` (enum: `active`, `inactive`, `caution`, `error`, required)
- `capture_time` (string) — The length of time the capture has been running for, hh:mm:ss format
- `status_description` (string, required) — A human-readable string containing the current capture status, can be empty

Example: built from the schema's field examples

```json
{
  "status": "active",
  "capture_time": "00:42:27",
  "status_description": ""
}
```

```bash
curl 'http://localhost:50001/v1/capture/status'
```

<a id="captureoperation"></a>
### GET `/v1/capture/{operation}`

Performs the requested capture operation (start, stop).

Operation ID: `captureOperation`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `operation` | path | enum: `start`, `stop` | yes | The capture operation to perform |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/capture/{operation}'
```

<a id="capturesettingsget"></a>
### GET `/v1/capture/settings`

Requests the current capture settings.

Operation ID: `captureSettingsGet`

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `source` (string, required) — The UUID of the source screen.
- `audio_routing` (array of array of integer, required) — Audio routing for the capture stream.
- `disk` (object)
  - `file_location` (string, required) — The location of the file on disk (folder only, no filename)
  - `encoding` (string) — The encoding used for encoding the captured video. This must match one of the encodings returned by the /encoding endpoint
  - `resolution` (object, required)
    - `width` (integer, required)
    - `height` (integer, required)
  - `frame_rate` (number, required) — The capture frame rate.
- `rtmp` (object)
  - `url` (string, required) — The URL of the RTMP server.
  - `key` (string, required) — The stream key to use with the URL.
  - `encoding` (string, required) — The encoding used for encoding the captured video. This must match one of the encodings returned by the /encoding endpoint
  - `save_local` (boolean, required) — `true` to save a local copy of the stream, `false` otherwise
  - `file_location` (string) — The location of the file on disk (folder only, no filename)
- `resi` (object)
  - `event_name` (string) — Optional event name.
  - `event_description` (string) — Optional event description.
  - `destination_group` (string, required)
  - `encoding` (string, required) — The encoding used for encoding the captured video. This must match one of the encodings returned by the /encoding endpoint

Example: Capture to disk

```json
{
  "source": "3C39C433-5C18-4F51-B357-55BB870227C4",
  "audio_routing": [
    [
      1
    ],
    [
      2
    ]
  ],
  "disk": {
    "file_location": "/Users/someuser/Movies",
    "codec": "h_264",
    "resolution": {
      "width": 1920,
      "height": 1080
    },
    "frame_rate": 29.97
  }
}
```

Example: Stream to RTMP (no local copy)

```json
{
  "source": "3C39C433-5C18-4F51-B357-55BB870227C4",
  "audio_routing": [
    [
      1
    ],
    [
      2
    ]
  ],
  "rtmp": {
    "server": "rtmps://server.com/live",
    "key": "g5sr2hz9",
    "encoding": "720p30_2_5",
    "save_local": false
  }
}
```

Example: Stream to RTMP (with local copy)

```json
{
  "source": "3C39C433-5C18-4F51-B357-55BB870227C4",
  "audio_routing": [
    [
      1
    ],
    [
      2
    ]
  ],
  "rtmp": {
    "server": "rtmps://server.com/live",
    "key": "g5sr2hz9",
    "encoding": "720p30_2_5",
    "save_local": true,
    "file_location": "/Users/someuser/Movies"
  }
}
```

Example: Stream to Resi

```json
{
  "source": "3C39C433-5C18-4F51-B357-55BB870227C4",
  "audio_routing": [
    [
      1
    ],
    [
      2
    ]
  ],
  "resi": {
    "event_name": "My Event",
    "event_description": "This is a short description of the event.",
    "destination_group": "Default Destination Group",
    "encoding": "720p30_4"
  }
}
```

```bash
curl 'http://localhost:50001/v1/capture/settings'
```

<a id="capturemodesget"></a>
### GET `/v1/capture/encodings/{type}`

Requests a list of all available capture modes for the capture type (disk, rtmp, resi).

Operation ID: `captureModesGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `type` | path | enum: `disk`, `rtmp`, `resi` | yes | The required capture type. |

**Responses**

- `200`: The request was completed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): array of string

Example

```json
[
  "h_264",
  "h_265",
  "hap",
  "hap_alpha",
  "hap_q",
  "prores_422",
  "prores_422_hq",
  "prores_422_lt",
  "prores_422_proxy",
  "prores_4444",
  "prores_4444_xq"
]
```

```bash
curl 'http://localhost:50001/v1/capture/encodings/{type}'
```

## Clear

<a id="clearlayer"></a>
### GET `/v1/clear/layer/{layer}`

Clears the specified layer (audio, props, messages, announcements, slide, media, video_input).

Operation ID: `clearLayer`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `layer` | path | enum: `audio`, `props`, `messages`, `announcements`, `slide`, `media`, `video_input` | yes | The name of layer that is to be cleared. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/clear/layer/{layer}'
```

<a id="cleargroupget"></a>
### GET `/v1/clear/group/{id}`

Requests the details of the specified clear group.

Operation ID: `clearGroupGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the required clear group, either name, index or UUID. Example (Match clear group by name): `Clear Group`. Example (Match clear group by index): `3`. Example (Match clear group by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `icon` (enum: `Zero`, `One`, `Two`, `Three`, `Four`, `Five`, `Six`, `Seven`, `Eight`, `Nine`, `All`, `Megahorn`, `Play`, `Bulb`, `Sunglasses`, `Arrow`, `Target`, `Star`, `Sun`, `Bell`, `Paperclip`, `Flask`, `Eyeglasses`, `Cupcake`, `Slide`, `Hat`, `Flower`, `Heart`, `Message`, `Audio`, `Cloud`, `Exclamation`, `Custom`, required) — The name of the icon to show for this clear group.
- `tint` (allOf of object, required) — The color tint to apply to the icon for this clear group.
  - *allOf option 1*: object
    - `red` (number, required) — The red component of the color, between 0 and 1
    - `green` (number, required) — The green component of the color, between 0 and 1
    - `blue` (number, required) — The blue component of the color, between 0 and 1
    - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.
- `layers` (array of enum: `music`, `audio_effects`, `props`, `messages`, `announcements`, `presentation`, `presentation_media`, `video_input`, required) — The list of all the layers cleared by this clear group
- `stop_timeline_announcements` (boolean, required) — Stop the timeline on the announcements layer Default `true`.
- `stop_timeline_presentation` (boolean, required) — Stop the timeline on the presentation layer Default `true`.
- `clear_next_presentation` (boolean, required) — Clear the next presentation when clearing the slide layer.

Example: built from the schema's field examples

```json
{
  "id": {
    "id": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "name": "Clear Group",
    "index": 3
  },
  "icon": "All",
  "tint": {
    "red": 0,
    "green": 0.54,
    "blue": 0.87,
    "alpha": 1
  },
  "layers": [
    "music"
  ]
}
```

```bash
curl 'http://localhost:50001/v1/clear/group/{id}'
```

<a id="cleargroupset"></a>
### PUT `/v1/clear/group/{id}`

Sets the details of the specified clear group.

Operation ID: `clearGroupSet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the required clear group, either name, index or UUID. Example (Match clear group by name): `Clear Group`. Example (Match clear group by index): `3`. Example (Match clear group by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. |

**Request body** (`application/json`, required): object

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `icon` (enum: `Zero`, `One`, `Two`, `Three`, `Four`, `Five`, `Six`, `Seven`, `Eight`, `Nine`, `All`, `Megahorn`, `Play`, `Bulb`, `Sunglasses`, `Arrow`, `Target`, `Star`, `Sun`, `Bell`, `Paperclip`, `Flask`, `Eyeglasses`, `Cupcake`, `Slide`, `Hat`, `Flower`, `Heart`, `Message`, `Audio`, `Cloud`, `Exclamation`, `Custom`, required) — The name of the icon to show for this clear group.
- `tint` (allOf of object, required) — The color tint to apply to the icon for this clear group.
  - *allOf option 1*: object
    - `red` (number, required) — The red component of the color, between 0 and 1
    - `green` (number, required) — The green component of the color, between 0 and 1
    - `blue` (number, required) — The blue component of the color, between 0 and 1
    - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.
- `layers` (array of enum: `music`, `audio_effects`, `props`, `messages`, `announcements`, `presentation`, `presentation_media`, `video_input`, required) — The list of all the layers cleared by this clear group
- `stop_timeline_announcements` (boolean, required) — Stop the timeline on the announcements layer Default `true`.
- `stop_timeline_presentation` (boolean, required) — Stop the timeline on the presentation layer Default `true`.
- `clear_next_presentation` (boolean, required) — Clear the next presentation when clearing the slide layer.

Example: built from the schema's field examples

```json
{
  "id": {
    "id": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "name": "Clear Group",
    "index": 3
  },
  "icon": "All",
  "tint": {
    "red": 0,
    "green": 0.54,
    "blue": 0.87,
    "alpha": 1
  },
  "layers": [
    "music"
  ]
}
```

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `icon` (enum: `Zero`, `One`, `Two`, `Three`, `Four`, `Five`, `Six`, `Seven`, `Eight`, `Nine`, `All`, `Megahorn`, `Play`, `Bulb`, `Sunglasses`, `Arrow`, `Target`, `Star`, `Sun`, `Bell`, `Paperclip`, `Flask`, `Eyeglasses`, `Cupcake`, `Slide`, `Hat`, `Flower`, `Heart`, `Message`, `Audio`, `Cloud`, `Exclamation`, `Custom`, required) — The name of the icon to show for this clear group.
- `tint` (allOf of object, required) — The color tint to apply to the icon for this clear group.
  - *allOf option 1*: object
    - `red` (number, required) — The red component of the color, between 0 and 1
    - `green` (number, required) — The green component of the color, between 0 and 1
    - `blue` (number, required) — The blue component of the color, between 0 and 1
    - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.
- `layers` (array of enum: `music`, `audio_effects`, `props`, `messages`, `announcements`, `presentation`, `presentation_media`, `video_input`, required) — The list of all the layers cleared by this clear group
- `stop_timeline_announcements` (boolean, required) — Stop the timeline on the announcements layer Default `true`.
- `stop_timeline_presentation` (boolean, required) — Stop the timeline on the presentation layer Default `true`.
- `clear_next_presentation` (boolean, required) — Clear the next presentation when clearing the slide layer.

Example: built from the schema's field examples

```json
{
  "id": {
    "id": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "name": "Clear Group",
    "index": 3
  },
  "icon": "All",
  "tint": {
    "red": 0,
    "green": 0.54,
    "blue": 0.87,
    "alpha": 1
  },
  "layers": [
    "music"
  ]
}
```

```bash
curl -X PUT 'http://localhost:50001/v1/clear/group/{id}' -H 'Content-Type: application/json' -d '{"id": {"id": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D", "name": "Clear Group", "index": 3}, "icon": "All", "tint": {"red": 0, "green": 0.54, "blue": 0.87, "alpha": 1}, "layers": ["music"]}'
```

<a id="cleargroupdelete"></a>
### DELETE `/v1/clear/group/{id}`

Deletes the specified clear group.

Operation ID: `clearGroupDelete`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the required clear group, either name, index or UUID. Example (Match clear group by name): `Clear Group`. Example (Match clear group by index): `3`. Example (Match clear group by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl -X DELETE 'http://localhost:50001/v1/clear/group/{id}'
```

<a id="cleargroupiconget"></a>
### GET `/v1/clear/group/{id}/icon`

Requests the image data for the icon of the specified clear group.

Operation ID: `clearGroupIconGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the required clear group, either name, index or UUID. Example (Match clear group by name): `Clear Group`. Example (Match clear group by index): `3`. Example (Match clear group by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. |

**Responses**

- `200`: The request was processed successfully. Content: `image/png`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`image/png`): string (binary)

```bash
curl 'http://localhost:50001/v1/clear/group/{id}/icon'
```

<a id="cleargroupiconset"></a>
### PUT `/v1/clear/group/{id}/icon`

Sets the custom icon of the specified clear group.

Operation ID: `clearGroupIconSet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the required clear group, either name, index or UUID. Example (Match clear group by name): `Clear Group`. Example (Match clear group by index): `3`. Example (Match clear group by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. |

**Request body** (`image/*`, required): string (binary)

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl -X PUT 'http://localhost:50001/v1/clear/group/{id}/icon' -H 'Content-Type: image/png' --data-binary @file
```

<a id="cleargrouptrigger"></a>
### GET `/v1/clear/group/{id}/trigger`

Triggers the specified clear group.

Operation ID: `clearGroupTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the required clear group, either name, index or UUID. Example (Match clear group by name): `Clear Group`. Example (Match clear group by index): `3`. Example (Match clear group by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/clear/group/{id}/trigger'
```

<a id="cleargroupsget"></a>
### GET `/v1/clear/groups`

Requests a list of all the configured clear groups.

Operation ID: `clearGroupsGet`

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): array of object

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `icon` (enum: `Zero`, `One`, `Two`, `Three`, `Four`, `Five`, `Six`, `Seven`, `Eight`, `Nine`, `All`, `Megahorn`, `Play`, `Bulb`, `Sunglasses`, `Arrow`, `Target`, `Star`, `Sun`, `Bell`, `Paperclip`, `Flask`, `Eyeglasses`, `Cupcake`, `Slide`, `Hat`, `Flower`, `Heart`, `Message`, `Audio`, `Cloud`, `Exclamation`, `Custom`, required) — The name of the icon to show for this clear group.
- `tint` (allOf of object, required) — The color tint to apply to the icon for this clear group.
  - *allOf option 1*: object
    - `red` (number, required) — The red component of the color, between 0 and 1
    - `green` (number, required) — The green component of the color, between 0 and 1
    - `blue` (number, required) — The blue component of the color, between 0 and 1
    - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.
- `layers` (array of enum: `music`, `audio_effects`, `props`, `messages`, `announcements`, `presentation`, `presentation_media`, `video_input`, required) — The list of all the layers cleared by this clear group
- `stop_timeline_announcements` (boolean, required) — Stop the timeline on the announcements layer Default `true`.
- `stop_timeline_presentation` (boolean, required) — Stop the timeline on the presentation layer Default `true`.
- `clear_next_presentation` (boolean, required) — Clear the next presentation when clearing the slide layer.

Example: built from the schema's field examples

```json
[
  {
    "id": {
      "id": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "name": "Clear Group",
      "index": 3
    },
    "icon": "All",
    "tint": {
      "red": 0,
      "green": 0.54,
      "blue": 0.87,
      "alpha": 1
    },
    "layers": [
      "music"
    ]
  }
]
```

```bash
curl 'http://localhost:50001/v1/clear/groups'
```

<a id="cleargroupcreate"></a>
### POST `/v1/clear/groups`

Create a clear group with the details specified.

The UUID and index in the request body are ignored. They will be automatically
assigned and returned in the response body.

Operation ID: `clearGroupCreate`

**Request body** (`application/json`, required): object

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `icon` (enum: `Zero`, `One`, `Two`, `Three`, `Four`, `Five`, `Six`, `Seven`, `Eight`, `Nine`, `All`, `Megahorn`, `Play`, `Bulb`, `Sunglasses`, `Arrow`, `Target`, `Star`, `Sun`, `Bell`, `Paperclip`, `Flask`, `Eyeglasses`, `Cupcake`, `Slide`, `Hat`, `Flower`, `Heart`, `Message`, `Audio`, `Cloud`, `Exclamation`, `Custom`, required) — The name of the icon to show for this clear group.
- `tint` (allOf of object, required) — The color tint to apply to the icon for this clear group.
  - *allOf option 1*: object
    - `red` (number, required) — The red component of the color, between 0 and 1
    - `green` (number, required) — The green component of the color, between 0 and 1
    - `blue` (number, required) — The blue component of the color, between 0 and 1
    - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.
- `layers` (array of enum: `music`, `audio_effects`, `props`, `messages`, `announcements`, `presentation`, `presentation_media`, `video_input`, required) — The list of all the layers cleared by this clear group
- `stop_timeline_announcements` (boolean, required) — Stop the timeline on the announcements layer Default `true`.
- `stop_timeline_presentation` (boolean, required) — Stop the timeline on the presentation layer Default `true`.
- `clear_next_presentation` (boolean, required) — Clear the next presentation when clearing the slide layer.

Example: built from the schema's field examples

```json
{
  "id": {
    "id": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "name": "Clear Group",
    "index": 3
  },
  "icon": "All",
  "tint": {
    "red": 0,
    "green": 0.54,
    "blue": 0.87,
    "alpha": 1
  },
  "layers": [
    "music"
  ]
}
```

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `icon` (enum: `Zero`, `One`, `Two`, `Three`, `Four`, `Five`, `Six`, `Seven`, `Eight`, `Nine`, `All`, `Megahorn`, `Play`, `Bulb`, `Sunglasses`, `Arrow`, `Target`, `Star`, `Sun`, `Bell`, `Paperclip`, `Flask`, `Eyeglasses`, `Cupcake`, `Slide`, `Hat`, `Flower`, `Heart`, `Message`, `Audio`, `Cloud`, `Exclamation`, `Custom`, required) — The name of the icon to show for this clear group.
- `tint` (allOf of object, required) — The color tint to apply to the icon for this clear group.
  - *allOf option 1*: object
    - `red` (number, required) — The red component of the color, between 0 and 1
    - `green` (number, required) — The green component of the color, between 0 and 1
    - `blue` (number, required) — The blue component of the color, between 0 and 1
    - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.
- `layers` (array of enum: `music`, `audio_effects`, `props`, `messages`, `announcements`, `presentation`, `presentation_media`, `video_input`, required) — The list of all the layers cleared by this clear group
- `stop_timeline_announcements` (boolean, required) — Stop the timeline on the announcements layer Default `true`.
- `stop_timeline_presentation` (boolean, required) — Stop the timeline on the presentation layer Default `true`.
- `clear_next_presentation` (boolean, required) — Clear the next presentation when clearing the slide layer.

Example: built from the schema's field examples

```json
{
  "id": {
    "id": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "name": "Clear Group",
    "index": 3
  },
  "icon": "All",
  "tint": {
    "red": 0,
    "green": 0.54,
    "blue": 0.87,
    "alpha": 1
  },
  "layers": [
    "music"
  ]
}
```

```bash
curl -X POST 'http://localhost:50001/v1/clear/groups' -H 'Content-Type: application/json' -d '{"id": {"id": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D", "name": "Clear Group", "index": 3}, "icon": "All", "tint": {"red": 0, "green": 0.54, "blue": 0.87, "alpha": 1}, "layers": ["music"]}'
```

## Global Groups

<a id="globalgroupsget"></a>
### GET `/v1/groups`

Requests a list of all the configured global groups.

Operation ID: `globalGroupsGet`

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): array of object

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `color` (object, required)
  - `red` (number, required) — The red component of the color, between 0 and 1
  - `green` (number, required) — The green component of the color, between 0 and 1
  - `blue` (number, required) — The blue component of the color, between 0 and 1
  - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.

Example: built from the schema's field examples

```json
[
  {
    "id": {
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "name": "Verse 1",
      "index": 0
    },
    "color": {
      "red": 0,
      "green": 0.54,
      "blue": 0.87,
      "alpha": 1
    }
  }
]
```

```bash
curl 'http://localhost:50001/v1/groups'
```

## Library

<a id="librarylistget"></a>
### GET `/v1/libraries`

Requests a list of all the configured libraries.

Operation ID: `libraryListGet`

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): array of object

- `id` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item

Example

```json
[
  {
    "id": {
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "name": "Library Name",
      "index": 0
    }
  }
]
```

```bash
curl 'http://localhost:50001/v1/libraries'
```

<a id="libraryget"></a>
### GET `/v1/library/{library_id}`

Requests an array of all items in the specified library.

Operation ID: `libraryGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `library_id` | path | string | yes | The ID of the library, either UUID, name, or index, in order of interpretation and search priority. Note - Matching by library UUID is deprecated and will be removed in a future version. Use name instead to request a library using a stable identifier. Example (Match library by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match library by name): `Library Name`. Example (Match library by index): `3`. |
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `updateType` (enum: `all`, `add`, `remove`, required) — The type of update this message represents: * `all` - The entire library, only sent as the initial response. * `add` - One or more items has been added to the library * `remove` - One or more items has been removed from the library
- `items` (array of object, required) — The items included in this update.
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item

Example: Initial response

```json
{
  "updateType": "all",
  "items": [
    {
      "index": 0,
      "name": "I Surrender All",
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D"
    },
    {
      "index": 1,
      "name": "It Is Well With My Soul",
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5E"
    },
    {
      "index": 2,
      "name": "Amazing Grace",
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5F"
    }
  ]
}
```

Example: Items added

```json
{
  "updateType": "add",
  "items": [
    {
      "index": 3,
      "name": "This I Believe",
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5A"
    }
  ]
}
```

Example: Items removed

```json
{
  "updateType": "remove",
  "items": [
    {
      "index": 1,
      "name": "It Is Well With My Soul",
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5E"
    },
    {
      "index": 2,
      "name": "Amazing Grace",
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5F"
    }
  ]
}
```

```bash
curl 'http://localhost:50001/v1/library/{library_id}'
```

<a id="librarytrigger"></a>
### GET `/v1/library/{library_id}/{presentation_id}/trigger`

Triggers the first cue of the specified presentation in the specified library.

Operation ID: `libraryTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `library_id` | path | string | yes | The ID of the library, either UUID, name, or index, in order of interpretation and search priority. Note - Matching by library UUID is deprecated and will be removed in a future version. Use name instead to request a library using a stable identifier. Example (Match library by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match library by name): `Library Name`. Example (Match library by index): `3`. |
| `presentation_id` | path | string | yes | The ID of the presentation, either UUID, name, or index, in order of interpretation and search priority. Example (Match presentation by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match presentation by name): `Presentation Name`. Example (Match presentation by index): `3`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/library/{library_id}/{presentation_id}/trigger'
```

<a id="librarytriggercue"></a>
### GET `/v1/library/{library_id}/{presentation_id}/{index}/trigger`

Triggers the specified cue of the specified presentation in the specified library.

Operation ID: `libraryTriggerCue`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `library_id` | path | string | yes | The ID of the library, either UUID, name, or index, in order of interpretation and search priority. Note - Matching by library UUID is deprecated and will be removed in a future version. Use name instead to request a library using a stable identifier. Example (Match library by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match library by name): `Library Name`. Example (Match library by index): `3`. |
| `presentation_id` | path | string | yes | The ID of the presentation, either UUID, name, or index, in order of interpretation and search priority. Example (Match presentation by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match presentation by name): `Presentation Name`. Example (Match presentation by index): `3`. |
| `index` | path | integer | yes | The index of the cue to use. Respects the selected arrangement of cues. Example `0`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/library/{library_id}/{presentation_id}/{index}/trigger'
```

## Looks

<a id="lookgetall"></a>
### GET `/v1/looks`

Requests a list of all configured audience looks, except the live look.

The current / live look is NOT included. The current look can be retrieved by GET /v1/look/current. While the current look may have the same attributes as one of the looks returned by this endpoint, the current look will have a unique UUID which is not returned by this endpoint.

Operation ID: `lookGetAll`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): array of object

- `id` (allOf of object)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `screens` (array of object, required) — The list of screens in the look
  - `video_input` (boolean, required) — Specifies whether the video input is enabled for this screen
  - `media` (boolean, required) — Specifies whether background media is enabled for this screen
  - `slide` (boolean, required) — Specifies whether the slide data is enabled for this screen
  - `announcements` (boolean) — Specifies whether the announcements layer is enabled for this screen
  - `props` (boolean, required) — Specifies whether props are enabled for this screen
  - `messages` (boolean, required) — Specifies whether messages are enabled for this screen
  - `presentation` (string) — The UUID of the theme slide to apply to slide data on the presentation layer. Not present or a null or empty string if no template is to be applied. If a UUID is provided but a matching theme slide is not found, then no change is made to the currently assigned theme slide.
  - `mask` (string) — The UUID of the mask to apply to this screen. Not present or a null or empty string if no mask is to be applied. If a UUID is provided but a matching mask is not found, then no change is made to the currently assigned mask.

Example: built from the schema's field examples

```json
[
  {
    "id": {
      "uuid": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5C",
      "name": "Look",
      "index": 0
    },
    "screens": [
      {
        "video_input": true,
        "media": true,
        "slide": true,
        "announcements": true,
        "props": true,
        "messages": true,
        "presentation": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
        "mask": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5E"
      },
      {
        "video_input": true,
        "media": true,
        "slide": true,
        "announcements": true,
        "props": true,
        "messages": true,
        "presentation": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
        "mask": null
      },
      {
        "video_input": false,
        "media": false,
        "slide": false,
        "announcements": true,
        "props": false,
        "messages": false,
        "presentation": null,
        "mask": null
      }
    ]
  }
]
```

```bash
curl 'http://localhost:50001/v1/looks'
```

<a id="lookcreate"></a>
### POST `/v1/looks`

Creates a new audience look with the specified details.

The UUID and index are ignored in the request body. They are automatically
assigned and returned as part of the response.

Operation ID: `lookCreate`

**Request body** (`application/json`, required): object

- `id` (allOf of object)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `screens` (array of object, required) — The list of screens in the look
  - `video_input` (boolean, required) — Specifies whether the video input is enabled for this screen
  - `media` (boolean, required) — Specifies whether background media is enabled for this screen
  - `slide` (boolean, required) — Specifies whether the slide data is enabled for this screen
  - `announcements` (boolean) — Specifies whether the announcements layer is enabled for this screen
  - `props` (boolean, required) — Specifies whether props are enabled for this screen
  - `messages` (boolean, required) — Specifies whether messages are enabled for this screen
  - `presentation` (string) — The UUID of the theme slide to apply to slide data on the presentation layer. Not present or a null or empty string if no template is to be applied. If a UUID is provided but a matching theme slide is not found, then no change is made to the currently assigned theme slide.
  - `mask` (string) — The UUID of the mask to apply to this screen. Not present or a null or empty string if no mask is to be applied. If a UUID is provided but a matching mask is not found, then no change is made to the currently assigned mask.

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5C",
    "name": "Look",
    "index": 0
  },
  "screens": [
    {
      "video_input": true,
      "media": true,
      "slide": true,
      "announcements": true,
      "props": true,
      "messages": true,
      "presentation": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "mask": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5E"
    },
    {
      "video_input": true,
      "media": true,
      "slide": true,
      "announcements": true,
      "props": true,
      "messages": true,
      "presentation": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "mask": null
    },
    {
      "video_input": false,
      "media": false,
      "slide": false,
      "announcements": true,
      "props": false,
      "messages": false,
      "presentation": null,
      "mask": null
    }
  ]
}
```

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `id` (allOf of object)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `screens` (array of object, required) — The list of screens in the look
  - `video_input` (boolean, required) — Specifies whether the video input is enabled for this screen
  - `media` (boolean, required) — Specifies whether background media is enabled for this screen
  - `slide` (boolean, required) — Specifies whether the slide data is enabled for this screen
  - `announcements` (boolean) — Specifies whether the announcements layer is enabled for this screen
  - `props` (boolean, required) — Specifies whether props are enabled for this screen
  - `messages` (boolean, required) — Specifies whether messages are enabled for this screen
  - `presentation` (string) — The UUID of the theme slide to apply to slide data on the presentation layer. Not present or a null or empty string if no template is to be applied. If a UUID is provided but a matching theme slide is not found, then no change is made to the currently assigned theme slide.
  - `mask` (string) — The UUID of the mask to apply to this screen. Not present or a null or empty string if no mask is to be applied. If a UUID is provided but a matching mask is not found, then no change is made to the currently assigned mask.

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5C",
    "name": "Look",
    "index": 0
  },
  "screens": [
    {
      "video_input": true,
      "media": true,
      "slide": true,
      "announcements": true,
      "props": true,
      "messages": true,
      "presentation": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "mask": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5E"
    },
    {
      "video_input": true,
      "media": true,
      "slide": true,
      "announcements": true,
      "props": true,
      "messages": true,
      "presentation": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "mask": null
    },
    {
      "video_input": false,
      "media": false,
      "slide": false,
      "announcements": true,
      "props": false,
      "messages": false,
      "presentation": null,
      "mask": null
    }
  ]
}
```

```bash
curl -X POST 'http://localhost:50001/v1/looks' -H 'Content-Type: application/json' -d '@body.json'
```

<a id="lookgetcurrent"></a>
### GET `/v1/look/current`

Requests the details of the currently live audience look.

NOTE: The current look is not contained in the looks returned by `GET /v1/looks` as
the current look gets special treatment in ProPresenter and cannot be deleted.
The current look may have the same attributes as one of the other not current saved looks, but it will have a
unique UUID not contained in the response of `GET /v1/looks`. If the current look's attributes match
those of one of the saved looks, the index returned is the index of the matching look in the
array returned by `GET /v1/looks`. If the current look has been modified and its attributes don't
match any of the saved looks, the index returned is the size of the saved looks array.

Operation ID: `lookGetCurrent`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.

`200` body (`application/json`): object

- `id` (allOf of object)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `screens` (array of object, required) — The list of screens in the look
  - `video_input` (boolean, required) — Specifies whether the video input is enabled for this screen
  - `media` (boolean, required) — Specifies whether background media is enabled for this screen
  - `slide` (boolean, required) — Specifies whether the slide data is enabled for this screen
  - `announcements` (boolean) — Specifies whether the announcements layer is enabled for this screen
  - `props` (boolean, required) — Specifies whether props are enabled for this screen
  - `messages` (boolean, required) — Specifies whether messages are enabled for this screen
  - `presentation` (string) — The UUID of the theme slide to apply to slide data on the presentation layer. Not present or a null or empty string if no template is to be applied. If a UUID is provided but a matching theme slide is not found, then no change is made to the currently assigned theme slide.
  - `mask` (string) — The UUID of the mask to apply to this screen. Not present or a null or empty string if no mask is to be applied. If a UUID is provided but a matching mask is not found, then no change is made to the currently assigned mask.

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5C",
    "name": "Look",
    "index": 0
  },
  "screens": [
    {
      "video_input": true,
      "media": true,
      "slide": true,
      "announcements": true,
      "props": true,
      "messages": true,
      "presentation": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "mask": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5E"
    },
    {
      "video_input": true,
      "media": true,
      "slide": true,
      "announcements": true,
      "props": true,
      "messages": true,
      "presentation": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "mask": null
    },
    {
      "video_input": false,
      "media": false,
      "slide": false,
      "announcements": true,
      "props": false,
      "messages": false,
      "presentation": null,
      "mask": null
    }
  ]
}
```

```bash
curl 'http://localhost:50001/v1/look/current'
```

<a id="looksetcurrent"></a>
### PUT `/v1/look/current`

Sets the details of the currently live audience look.

The UUID and index in the request body are ignored and not applied to the specified look.
For multiple screens, the screens array is applied in order by index to the array of existing screens in the look.
Updates to the screens are done until either the end of the screens update array is reached or the end of the existing screens array is reached.
If the current look is not found in the saved looks (because it is modified but not saved), returns the size of the saved looks array for the index.

Operation ID: `lookSetCurrent`

**Request body** (`application/json`): object

- `id` (allOf of object)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `screens` (array of object, required) — The list of screens in the look
  - `video_input` (boolean, required) — Specifies whether the video input is enabled for this screen
  - `media` (boolean, required) — Specifies whether background media is enabled for this screen
  - `slide` (boolean, required) — Specifies whether the slide data is enabled for this screen
  - `announcements` (boolean) — Specifies whether the announcements layer is enabled for this screen
  - `props` (boolean, required) — Specifies whether props are enabled for this screen
  - `messages` (boolean, required) — Specifies whether messages are enabled for this screen
  - `presentation` (string) — The UUID of the theme slide to apply to slide data on the presentation layer. Not present or a null or empty string if no template is to be applied. If a UUID is provided but a matching theme slide is not found, then no change is made to the currently assigned theme slide.
  - `mask` (string) — The UUID of the mask to apply to this screen. Not present or a null or empty string if no mask is to be applied. If a UUID is provided but a matching mask is not found, then no change is made to the currently assigned mask.

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5C",
    "name": "Look",
    "index": 0
  },
  "screens": [
    {
      "video_input": true,
      "media": true,
      "slide": true,
      "announcements": true,
      "props": true,
      "messages": true,
      "presentation": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "mask": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5E"
    },
    {
      "video_input": true,
      "media": true,
      "slide": true,
      "announcements": true,
      "props": true,
      "messages": true,
      "presentation": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "mask": null
    },
    {
      "video_input": false,
      "media": false,
      "slide": false,
      "announcements": true,
      "props": false,
      "messages": false,
      "presentation": null,
      "mask": null
    }
  ]
}
```

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl -X PUT 'http://localhost:50001/v1/look/current' -H 'Content-Type: application/json' -d '@body.json'
```

<a id="lookget"></a>
### GET `/v1/look/{id}`

Requests the details of the specified audience look.

NOTE: The currently live look is not found by this endpoint. To get the live look use GET /v1/look/current.

Operation ID: `lookGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the audience look, either UUID, name, or index. Example (Match audience look by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match audience look by name): `Look`. Example (Match audience look by index): `3`. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: not described (the published file points at a missing definition).

`200` body (`application/json`): object

- `id` (allOf of object)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `screens` (array of object, required) — The list of screens in the look
  - `video_input` (boolean, required) — Specifies whether the video input is enabled for this screen
  - `media` (boolean, required) — Specifies whether background media is enabled for this screen
  - `slide` (boolean, required) — Specifies whether the slide data is enabled for this screen
  - `announcements` (boolean) — Specifies whether the announcements layer is enabled for this screen
  - `props` (boolean, required) — Specifies whether props are enabled for this screen
  - `messages` (boolean, required) — Specifies whether messages are enabled for this screen
  - `presentation` (string) — The UUID of the theme slide to apply to slide data on the presentation layer. Not present or a null or empty string if no template is to be applied. If a UUID is provided but a matching theme slide is not found, then no change is made to the currently assigned theme slide.
  - `mask` (string) — The UUID of the mask to apply to this screen. Not present or a null or empty string if no mask is to be applied. If a UUID is provided but a matching mask is not found, then no change is made to the currently assigned mask.

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5C",
    "name": "Look",
    "index": 0
  },
  "screens": [
    {
      "video_input": true,
      "media": true,
      "slide": true,
      "announcements": true,
      "props": true,
      "messages": true,
      "presentation": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "mask": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5E"
    },
    {
      "video_input": true,
      "media": true,
      "slide": true,
      "announcements": true,
      "props": true,
      "messages": true,
      "presentation": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "mask": null
    },
    {
      "video_input": false,
      "media": false,
      "slide": false,
      "announcements": true,
      "props": false,
      "messages": false,
      "presentation": null,
      "mask": null
    }
  ]
}
```

```bash
curl 'http://localhost:50001/v1/look/{id}'
```

<a id="lookput"></a>
### PUT `/v1/look/{id}`

Sets the details of the specified audience look.

The UUID and index are ignored in the request. They are not modified by this operation.
For multiple screens, the screens array is applied in order by index to the array of existing screens in the look.
Updates to the screens are done until either the end of the screens update array is reached or the end of the existing screens array is reached.
NOTE: The currently live look is not settable by this endpoint. To set the live look use PUT /v1/look/current.

Operation ID: `lookPut`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the audience look, either UUID, name, or index. Example (Match audience look by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match audience look by name): `Look`. Example (Match audience look by index): `3`. |

**Request body** (`application/json`, required): object

- `id` (allOf of object)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `screens` (array of object, required) — The list of screens in the look
  - `video_input` (boolean, required) — Specifies whether the video input is enabled for this screen
  - `media` (boolean, required) — Specifies whether background media is enabled for this screen
  - `slide` (boolean, required) — Specifies whether the slide data is enabled for this screen
  - `announcements` (boolean) — Specifies whether the announcements layer is enabled for this screen
  - `props` (boolean, required) — Specifies whether props are enabled for this screen
  - `messages` (boolean, required) — Specifies whether messages are enabled for this screen
  - `presentation` (string) — The UUID of the theme slide to apply to slide data on the presentation layer. Not present or a null or empty string if no template is to be applied. If a UUID is provided but a matching theme slide is not found, then no change is made to the currently assigned theme slide.
  - `mask` (string) — The UUID of the mask to apply to this screen. Not present or a null or empty string if no mask is to be applied. If a UUID is provided but a matching mask is not found, then no change is made to the currently assigned mask.

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5C",
    "name": "Look",
    "index": 0
  },
  "screens": [
    {
      "video_input": true,
      "media": true,
      "slide": true,
      "announcements": true,
      "props": true,
      "messages": true,
      "presentation": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "mask": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5E"
    },
    {
      "video_input": true,
      "media": true,
      "slide": true,
      "announcements": true,
      "props": true,
      "messages": true,
      "presentation": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "mask": null
    },
    {
      "video_input": false,
      "media": false,
      "slide": false,
      "announcements": true,
      "props": false,
      "messages": false,
      "presentation": null,
      "mask": null
    }
  ]
}
```

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `id` (allOf of object)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `screens` (array of object, required) — The list of screens in the look
  - `video_input` (boolean, required) — Specifies whether the video input is enabled for this screen
  - `media` (boolean, required) — Specifies whether background media is enabled for this screen
  - `slide` (boolean, required) — Specifies whether the slide data is enabled for this screen
  - `announcements` (boolean) — Specifies whether the announcements layer is enabled for this screen
  - `props` (boolean, required) — Specifies whether props are enabled for this screen
  - `messages` (boolean, required) — Specifies whether messages are enabled for this screen
  - `presentation` (string) — The UUID of the theme slide to apply to slide data on the presentation layer. Not present or a null or empty string if no template is to be applied. If a UUID is provided but a matching theme slide is not found, then no change is made to the currently assigned theme slide.
  - `mask` (string) — The UUID of the mask to apply to this screen. Not present or a null or empty string if no mask is to be applied. If a UUID is provided but a matching mask is not found, then no change is made to the currently assigned mask.

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5C",
    "name": "Look",
    "index": 0
  },
  "screens": [
    {
      "video_input": true,
      "media": true,
      "slide": true,
      "announcements": true,
      "props": true,
      "messages": true,
      "presentation": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "mask": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5E"
    },
    {
      "video_input": true,
      "media": true,
      "slide": true,
      "announcements": true,
      "props": true,
      "messages": true,
      "presentation": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "mask": null
    },
    {
      "video_input": false,
      "media": false,
      "slide": false,
      "announcements": true,
      "props": false,
      "messages": false,
      "presentation": null,
      "mask": null
    }
  ]
}
```

```bash
curl -X PUT 'http://localhost:50001/v1/look/{id}' -H 'Content-Type: application/json' -d '@body.json'
```

<a id="lookdelete"></a>
### DELETE `/v1/look/{id}`

Deletes the specified audience look from the saved looks.

NOTE: The currently live look may not be deleted.

Operation ID: `lookDelete`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the audience look, either UUID, name, or index. Example (Match audience look by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match audience look by name): `Look`. Example (Match audience look by index): `3`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl -X DELETE 'http://localhost:50001/v1/look/{id}'
```

<a id="looktrigger"></a>
### GET `/v1/look/{id}/trigger`

Triggers the specified audience look to make it the live/current look.

Operation ID: `lookTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the audience look, either UUID, name, or index. Example (Match audience look by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match audience look by name): `Look`. Example (Match audience look by index): `3`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/look/{id}/trigger'
```

## Macro

<a id="macrogetall"></a>
### GET `/v1/macros`

Requests a list of all the configured macros.

Operation ID: `macroGetAll`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): array of object. The list of all defined macros.

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `color` (object, required)
  - `red` (number, required) — The red component of the color, between 0 and 1
  - `green` (number, required) — The green component of the color, between 0 and 1
  - `blue` (number, required) — The blue component of the color, between 0 and 1
  - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.
- `image_type` (string) — The selected image for the macro
- `actions` (array of object) — The actions belonging to the macro
  - `type` (enum: `unknown`, `stage_layout`, `media`, `timer`, `communication`, `clear`, `prop`, `mask`, `message`, `social_media`, `multiscreen`, `presentation_slide`, `foreground_media`, `background_media`, `presentation_document`, `prop_slide`, `external_presentation`, `audience_look`, `audio_input`, `audio_bin_playlist`, `media_bin_playlist`, `slide_destination`, `macro`, `clear_group`, `capture`, `library_playlist`, required) — The name of the type of action

Example: built from the schema's field examples

```json
[
  {
    "id": {
      "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
      "name": "Macro",
      "index": 3
    },
    "color": {
      "red": 0,
      "green": 0.54,
      "blue": 0.87,
      "alpha": 1
    },
    "image_type": "Cloud",
    "actions": [
      {
        "type": "media"
      }
    ]
  }
]
```

```bash
curl 'http://localhost:50001/v1/macros'
```

<a id="macroget"></a>
### GET `/v1/macro/{id}`

Requests the details of the specified macro.

Operation ID: `macroGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the macro, either UUID, name, or index. Example (Match macro by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match macro by name): `Macro`. Example (Match macro by index): `3`. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `color` (object, required)
  - `red` (number, required) — The red component of the color, between 0 and 1
  - `green` (number, required) — The green component of the color, between 0 and 1
  - `blue` (number, required) — The blue component of the color, between 0 and 1
  - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.
- `image_type` (string) — The selected image for the macro
- `actions` (array of object) — The actions belonging to the macro
  - `type` (enum: `unknown`, `stage_layout`, `media`, `timer`, `communication`, `clear`, `prop`, `mask`, `message`, `social_media`, `multiscreen`, `presentation_slide`, `foreground_media`, `background_media`, `presentation_document`, `prop_slide`, `external_presentation`, `audience_look`, `audio_input`, `audio_bin_playlist`, `media_bin_playlist`, `slide_destination`, `macro`, `clear_group`, `capture`, `library_playlist`, required) — The name of the type of action

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
    "name": "Macro",
    "index": 3
  },
  "color": {
    "red": 0,
    "green": 0.54,
    "blue": 0.87,
    "alpha": 1
  },
  "image_type": "Cloud",
  "actions": [
    {
      "type": "media"
    }
  ]
}
```

```bash
curl 'http://localhost:50001/v1/macro/{id}'
```

<a id="macroset"></a>
### PUT `/v1/macro/{id}`

Sets the details of the specified macro.

The UUID and index in the request body are ignored. They are not updated.

Operation ID: `macroSet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the macro, either UUID, name, or index. Example (Match macro by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match macro by name): `Macro`. Example (Match macro by index): `3`. |

**Request body** (`application/json`, required): object

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `color` (object, required)
  - `red` (number, required) — The red component of the color, between 0 and 1
  - `green` (number, required) — The green component of the color, between 0 and 1
  - `blue` (number, required) — The blue component of the color, between 0 and 1
  - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.
- `image_type` (string) — The selected image for the macro
- `actions` (array of object) — The actions belonging to the macro
  - `type` (enum: `unknown`, `stage_layout`, `media`, `timer`, `communication`, `clear`, `prop`, `mask`, `message`, `social_media`, `multiscreen`, `presentation_slide`, `foreground_media`, `background_media`, `presentation_document`, `prop_slide`, `external_presentation`, `audience_look`, `audio_input`, `audio_bin_playlist`, `media_bin_playlist`, `slide_destination`, `macro`, `clear_group`, `capture`, `library_playlist`, required) — The name of the type of action

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
    "name": "Macro",
    "index": 3
  },
  "color": {
    "red": 0,
    "green": 0.54,
    "blue": 0.87,
    "alpha": 1
  },
  "image_type": "Cloud",
  "actions": [
    {
      "type": "media"
    }
  ]
}
```

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `color` (object, required)
  - `red` (number, required) — The red component of the color, between 0 and 1
  - `green` (number, required) — The green component of the color, between 0 and 1
  - `blue` (number, required) — The blue component of the color, between 0 and 1
  - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.
- `image_type` (string) — The selected image for the macro
- `actions` (array of object) — The actions belonging to the macro
  - `type` (enum: `unknown`, `stage_layout`, `media`, `timer`, `communication`, `clear`, `prop`, `mask`, `message`, `social_media`, `multiscreen`, `presentation_slide`, `foreground_media`, `background_media`, `presentation_document`, `prop_slide`, `external_presentation`, `audience_look`, `audio_input`, `audio_bin_playlist`, `media_bin_playlist`, `slide_destination`, `macro`, `clear_group`, `capture`, `library_playlist`, required) — The name of the type of action

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
    "name": "Macro",
    "index": 3
  },
  "color": {
    "red": 0,
    "green": 0.54,
    "blue": 0.87,
    "alpha": 1
  },
  "image_type": "Cloud",
  "actions": [
    {
      "type": "media"
    }
  ]
}
```

```bash
curl -X PUT 'http://localhost:50001/v1/macro/{id}' -H 'Content-Type: application/json' -d '{"id": {"uuid": "3C39C433-5C18-4F51-B357-55BB870227C4", "name": "Macro", "index": 3}, "color": {"red": 0, "green": 0.54, "blue": 0.87, "alpha": 1}, "image_type": "Cloud", "actions": [{"type": "media"}]}'
```

<a id="macrodelete"></a>
### DELETE `/v1/macro/{id}`

Deletes the specified macro.

Operation ID: `macroDelete`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the macro, either UUID, name, or index. Example (Match macro by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match macro by name): `Macro`. Example (Match macro by index): `3`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl -X DELETE 'http://localhost:50001/v1/macro/{id}'
```

<a id="macrotrigger"></a>
### GET `/v1/macro/{id}/trigger`

Triggers the specified macro.

Operation ID: `macroTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the macro, either UUID, name, or index. Example (Match macro by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match macro by name): `Macro`. Example (Match macro by index): `3`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/macro/{id}/trigger'
```

<a id="macroiconget"></a>
### GET `/v1/macro/{id}/icon`

Requests the image data for the icon of the specified macro.

Operation ID: `macroIconGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `quality` | query | integer | no | The desired quality of the thumbnail. The value is the number of pixels in the _largest_ dimension of the image, i.e. if the image is landscape (wider than it is tall), it is the _width_ of the image. If the image is portrait (taller than it is wide), it is the _height_ of the image. Default `400`. Example `400`. |
| `id` | path | string | yes | The ID of the macro, either UUID, name, or index. Example (Match macro by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match macro by name): `Macro`. Example (Match macro by index): `3`. |

**Responses**

- `200`: The request was processed successfully. Content: `image/png`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`image/png`): string (binary)

```bash
curl 'http://localhost:50001/v1/macro/{id}/icon'
```

<a id="macroiconset"></a>
### PUT `/v1/macro/{id}/icon`

Sets the custom icon of the specified macro.

Operation ID: `macroIconSet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the macro, either UUID, name, or index. Example (Match macro by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match macro by name): `Macro`. Example (Match macro by index): `3`. |

**Request body** (`image/*`, required): string (binary)

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl -X PUT 'http://localhost:50001/v1/macro/{id}/icon' -H 'Content-Type: image/png' --data-binary @file
```

<a id="macrocollectiongetall"></a>
### GET `/v1/macro_collections`

Requests a list of all collections and their contents

Operation ID: `macroCollectionGetAll`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `collections` (array of object, required)
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `macros` (array of object) — The macros belonging to this collection
    - `id` (allOf of object, required)
      - *allOf option 1*: object
        - `uuid` (string, required) — The UUID of the item
        - `name` (string, required) — The user-defined name of the item
        - `index` (integer, required) — The index of the item
    - `color` (object, required)
      - `red` (number, required) — The red component of the color, between 0 and 1
      - `green` (number, required) — The green component of the color, between 0 and 1
      - `blue` (number, required) — The blue component of the color, between 0 and 1
      - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.
    - `image_type` (string) — The selected image for the macro
    - `actions` (array of object) — The actions belonging to the macro
      - `type` (enum: `unknown`, `stage_layout`, `media`, `timer`, `communication`, `clear`, `prop`, `mask`, `message`, `social_media`, `multiscreen`, `presentation_slide`, `foreground_media`, `background_media`, `presentation_document`, `prop_slide`, `external_presentation`, `audience_look`, `audio_input`, `audio_bin_playlist`, `media_bin_playlist`, `slide_destination`, `macro`, `clear_group`, `capture`, `library_playlist`, required) — The name of the type of action

Example: built from the schema's field examples

```json
{
  "collections": [
    {
      "id": {
        "uuid": "9131E88E-2B0A-4C08-A722-7FDC448901A9",
        "name": "Collection",
        "index": 1
      },
      "macros": [
        {
          "id": {
            "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
            "name": "Macro",
            "index": 3
          },
          "color": {
            "red": 0,
            "green": 0.54,
            "blue": 0.87,
            "alpha": 1
          },
          "image_type": "Cloud",
          "actions": [
            {
              "type": "media"
            }
          ]
        }
      ]
    }
  ]
}
```

```bash
curl 'http://localhost:50001/v1/macro_collections'
```

<a id="macrocollectioncreate"></a>
### POST `/v1/macro_collections`

Creates a new macro collection with the specified name

Operation ID: `macroCollectionCreate`

**Request body** (`application/json`, required): object

- `name` (string, required) — The name of the new macro

Example: built from the schema's field examples

```json
{
  "name": "My Macros"
}
```

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `macros` (array of object) — The macros belonging to this collection
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `color` (object, required)
    - `red` (number, required) — The red component of the color, between 0 and 1
    - `green` (number, required) — The green component of the color, between 0 and 1
    - `blue` (number, required) — The blue component of the color, between 0 and 1
    - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.
  - `image_type` (string) — The selected image for the macro
  - `actions` (array of object) — The actions belonging to the macro
    - `type` (enum: `unknown`, `stage_layout`, `media`, `timer`, `communication`, `clear`, `prop`, `mask`, `message`, `social_media`, `multiscreen`, `presentation_slide`, `foreground_media`, `background_media`, `presentation_document`, `prop_slide`, `external_presentation`, `audience_look`, `audio_input`, `audio_bin_playlist`, `media_bin_playlist`, `slide_destination`, `macro`, `clear_group`, `capture`, `library_playlist`, required) — The name of the type of action

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "9131E88E-2B0A-4C08-A722-7FDC448901A9",
    "name": "Collection",
    "index": 1
  },
  "macros": [
    {
      "id": {
        "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
        "name": "Macro",
        "index": 3
      },
      "color": {
        "red": 0,
        "green": 0.54,
        "blue": 0.87,
        "alpha": 1
      },
      "image_type": "Cloud",
      "actions": [
        {
          "type": "media"
        }
      ]
    }
  ]
}
```

```bash
curl -X POST 'http://localhost:50001/v1/macro_collections' -H 'Content-Type: application/json' -d '{"name": "My Macros"}'
```

<a id="macrocollectionget"></a>
### GET `/v1/macro_collection/{id}`

Requests a specific collection of macros

Operation ID: `macroCollectionGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the macro collection, either UUID, name, or index. Example (Match macro collection by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match macro collection by name): `Macro`. Example (Match macro collection by index): `3`. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `macros` (array of object) — The macros belonging to this collection
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `color` (object, required)
    - `red` (number, required) — The red component of the color, between 0 and 1
    - `green` (number, required) — The green component of the color, between 0 and 1
    - `blue` (number, required) — The blue component of the color, between 0 and 1
    - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.
  - `image_type` (string) — The selected image for the macro
  - `actions` (array of object) — The actions belonging to the macro
    - `type` (enum: `unknown`, `stage_layout`, `media`, `timer`, `communication`, `clear`, `prop`, `mask`, `message`, `social_media`, `multiscreen`, `presentation_slide`, `foreground_media`, `background_media`, `presentation_document`, `prop_slide`, `external_presentation`, `audience_look`, `audio_input`, `audio_bin_playlist`, `media_bin_playlist`, `slide_destination`, `macro`, `clear_group`, `capture`, `library_playlist`, required) — The name of the type of action

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "9131E88E-2B0A-4C08-A722-7FDC448901A9",
    "name": "Collection",
    "index": 1
  },
  "macros": [
    {
      "id": {
        "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
        "name": "Macro",
        "index": 3
      },
      "color": {
        "red": 0,
        "green": 0.54,
        "blue": 0.87,
        "alpha": 1
      },
      "image_type": "Cloud",
      "actions": [
        {
          "type": "media"
        }
      ]
    }
  ]
}
```

```bash
curl 'http://localhost:50001/v1/macro_collection/{id}'
```

<a id="macrocollectionset"></a>
### PUT `/v1/macro_collection/{id}`

Sets the details of the specified macro collection.

The UUID and index in the request body are ignored. They are not updated.

Operation ID: `macroCollectionSet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the macro collection, either UUID, name, or index. Example (Match macro collection by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match macro collection by name): `Macro`. Example (Match macro collection by index): `3`. |

**Request body** (`application/json`, required): object

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `macros` (array of object) — The macros belonging to this collection
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `color` (object, required)
    - `red` (number, required) — The red component of the color, between 0 and 1
    - `green` (number, required) — The green component of the color, between 0 and 1
    - `blue` (number, required) — The blue component of the color, between 0 and 1
    - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.
  - `image_type` (string) — The selected image for the macro
  - `actions` (array of object) — The actions belonging to the macro
    - `type` (enum: `unknown`, `stage_layout`, `media`, `timer`, `communication`, `clear`, `prop`, `mask`, `message`, `social_media`, `multiscreen`, `presentation_slide`, `foreground_media`, `background_media`, `presentation_document`, `prop_slide`, `external_presentation`, `audience_look`, `audio_input`, `audio_bin_playlist`, `media_bin_playlist`, `slide_destination`, `macro`, `clear_group`, `capture`, `library_playlist`, required) — The name of the type of action

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "9131E88E-2B0A-4C08-A722-7FDC448901A9",
    "name": "Collection",
    "index": 1
  },
  "macros": [
    {
      "id": {
        "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
        "name": "Macro",
        "index": 3
      },
      "color": {
        "red": 0,
        "green": 0.54,
        "blue": 0.87,
        "alpha": 1
      },
      "image_type": "Cloud",
      "actions": [
        {
          "type": "media"
        }
      ]
    }
  ]
}
```

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `macros` (array of object) — The macros belonging to this collection
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `color` (object, required)
    - `red` (number, required) — The red component of the color, between 0 and 1
    - `green` (number, required) — The green component of the color, between 0 and 1
    - `blue` (number, required) — The blue component of the color, between 0 and 1
    - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.
  - `image_type` (string) — The selected image for the macro
  - `actions` (array of object) — The actions belonging to the macro
    - `type` (enum: `unknown`, `stage_layout`, `media`, `timer`, `communication`, `clear`, `prop`, `mask`, `message`, `social_media`, `multiscreen`, `presentation_slide`, `foreground_media`, `background_media`, `presentation_document`, `prop_slide`, `external_presentation`, `audience_look`, `audio_input`, `audio_bin_playlist`, `media_bin_playlist`, `slide_destination`, `macro`, `clear_group`, `capture`, `library_playlist`, required) — The name of the type of action

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "9131E88E-2B0A-4C08-A722-7FDC448901A9",
    "name": "Collection",
    "index": 1
  },
  "macros": [
    {
      "id": {
        "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
        "name": "Macro",
        "index": 3
      },
      "color": {
        "red": 0,
        "green": 0.54,
        "blue": 0.87,
        "alpha": 1
      },
      "image_type": "Cloud",
      "actions": [
        {
          "type": "media"
        }
      ]
    }
  ]
}
```

```bash
curl -X PUT 'http://localhost:50001/v1/macro_collection/{id}' -H 'Content-Type: application/json' -d '@body.json'
```

<a id="macrocollectiondelete"></a>
### DELETE `/v1/macro_collection/{id}`

Deletes the specified macro collection and any macros contained within it.

Operation ID: `macroCollectionDelete`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the macro collection, either UUID, name, or index. Example (Match macro collection by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match macro collection by name): `Macro`. Example (Match macro collection by index): `3`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl -X DELETE 'http://localhost:50001/v1/macro_collection/{id}'
```

## Masks

<a id="maskgetall"></a>
### GET `/v1/masks`

Requests a list of all configured masks.

Operation ID: `maskGetAll`

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): array of object

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item

Example

```json
[
  {
    "id": {
      "uuid": "42C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "index": 0,
      "name": "Mask 1"
    }
  },
  {
    "id": {
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "index": 1,
      "name": "Mask 2"
    }
  }
]
```

```bash
curl 'http://localhost:50001/v1/masks'
```

<a id="maskget"></a>
### GET `/v1/mask/{id}`

Requests the details of the specified mask.

Operation ID: `maskGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the mask, either UUID, name, or index. Example (Match mask by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match mask by name): `Mask`. Example (Match mask by index): `3`. |

**Responses**

- `200`:  Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
    "name": "Mask",
    "index": 3
  }
}
```

```bash
curl 'http://localhost:50001/v1/mask/{id}'
```

<a id="maskthumbnailget"></a>
### GET `/v1/mask/{id}/thumbnail`

Requests a thumbnail image of the specified mask at the given quality value.

Operation ID: `maskThumbnailGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the mask, either UUID, name, or index. Example (Match mask by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match mask by name): `Mask`. Example (Match mask by index): `3`. |
| `quality` | query | integer | no | The desired quality of the thumbnail. The value is the number of pixels in the _largest_ dimension of the image, i.e. if the image is landscape (wider than it is tall), it is the _width_ of the image. If the image is portrait (taller than it is wide), it is the _height_ of the image. Default `400`. Example `400`. |

**Responses**

- `200`: The request was processed successfully. Content: `image/jpeg`, `image/png`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`image/jpeg`): string (binary)

`200` body (`image/png`): string (binary)

```bash
curl 'http://localhost:50001/v1/mask/{id}/thumbnail'
```

## Media

<a id="mediaplaylistsget"></a>
### GET `/v1/media/playlists`

Requests a list of all the configured media playlists.

Operation ID: `mediaPlaylistsGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.

`200` body (`application/json`): array of object

- `id` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item
- `type` (enum: `group`, `playlist`) — Whether this is a playlist folder or an actual playlist.
- `children` (array of object) — The list of child playlists inside this playlist.

Example

```json
{
  "id": {
    "uuid": "ED59503B-394D-4D5D-B0B0-5AE80D0BA025",
    "name": "Playlist Folder",
    "index": 0
  },
  "type": "group",
  "children": [
    {
      "id": {
        "uuid": "C49A250A-3E32-4C26-A220-46D6596426B5",
        "name": "Playlist 1",
        "index": 0
      },
      "type": "playlist",
      "children": []
    },
    {
      "id": {
        "uuid": "D8C5D151-45C9-4CFF-B1F9-74F3B4B5173B",
        "name": "Playlist 2",
        "index": 1
      },
      "type": "playlist",
      "children": []
    }
  ]
}
```

```bash
curl 'http://localhost:50001/v1/media/playlists'
```

<a id="mediaplaylistget"></a>
### GET `/v1/media/playlist/{playlist_id}`

Requests a list of all the media items in the specified media playlist.

A maximum of 100 items will be returned per call. The `start` query parameter can
be used to retrieve multiple pages of items, by setting it to the index of the first
item to return.

Operation ID: `mediaPlaylistGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `playlist_id` | path | string | yes | The ID of the playlist, either UUID, name, or index, in order of interpretation and search priority. A path can also be specified to identify playlists within folders. UUID and name matches are searched through the entire playlist tree. Because UUIDs are unique a path is not necessary. A name path is not necessary unless there are multiple playlists with the same name in different folders. A partial path of names can be used as well. Indices only index into the immediate children at each level of the tree so an index path is requred to access playlists within folders if only using indices. A mix of UUIDs, names, and indices can be used for the path if desired. Example (Match playlist by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match playlist by name): `Playlist Name`. Example (Match playlist with a name path): `Folder1/Folder2/Playlist Name`. Example (Match playlist by index): `3`. Example (Match playlist by index path): `2/0/1`. Example (Match playlist by a mixed path): `3C39C433-5C18-4F51-B357-55BB870227C4/Folder Name/0`. |
| `start` | query | integer | no | The starting index in the list. If the index is out of range, the returned items array will be empty. Example `100`. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `id` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item
- `items` (array of any, required)

Example

```json
{
  "id": {
    "uuid": "B238C071-3628-4204-BB19-DDE76DC57CE7",
    "name": "Playlist 3",
    "index": 2
  },
  "items": [
    {
      "id": {
        "uuid": "94A2D4EA-EEA5-48A4-AF6F-850D700C1830",
        "name": "Photo 1",
        "index": 0
      },
      "type": "image",
      "artist": "unknown",
      "duration": 0
    },
    {
      "id": {
        "uuid": "8F40D44E-4018-4552-8803-60DC788DB1CF",
        "name": "Amazing Grace",
        "index": 1
      },
      "type": "video",
      "artist": "John Newton",
      "duration": 110
    }
  ]
}
```

```bash
curl 'http://localhost:50001/v1/media/playlist/{playlist_id}'
```

<a id="mediaplaylistupdateget"></a>
### GET `/v1/media/playlist/{playlist_id}/updates`

Requests a chunked data update every time the specified media playlist changes.

This endpoint can be used to subscribe to changes to a particular playlist.
The response is chunked, where each chunk is a simple string "change".
This string will be sent whenever the media playlist changes in regards to any of the
data returned by `GET /media/playlist/{playlist_id}`. The updated playlist data can be
queried by recalling `GET /media/playlist/{playlist_id}`.

Note that this endpoint will return nothing unless the `chunked` parameter is set to `true`.

Operation ID: `mediaPlaylistUpdateGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `playlist_id` | path | string | yes | The ID of the playlist, either UUID, name, or index, in order of interpretation and search priority. A path can also be specified to identify playlists within folders. UUID and name matches are searched through the entire playlist tree. Because UUIDs are unique a path is not necessary. A name path is not necessary unless there are multiple playlists with the same name in different folders. A partial path of names can be used as well. Indices only index into the immediate children at each level of the tree so an index path is requred to access playlists within folders if only using indices. A mix of UUIDs, names, and indices can be used for the path if desired. Example (Match playlist by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match playlist by name): `Playlist Name`. Example (Match playlist with a name path): `Folder1/Folder2/Playlist Name`. Example (Match playlist by index): `3`. Example (Match playlist by index path): `2/0/1`. Example (Match playlist by a mixed path): `3C39C433-5C18-4F51-B357-55BB870227C4/Folder Name/0`. |
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): enum: `change`

Example: built from the schema's field examples

```json
"change"
```

```bash
curl 'http://localhost:50001/v1/media/playlist/{playlist_id}/updates'
```

<a id="mediathumbnailget"></a>
### GET `/v1/media/{uuid}/thumbnail`

Requests a thumbnail image of the specified media item at the given quality value.

Operation ID: `mediaThumbnailGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `uuid` | path | string | yes | The UUID of the item to look up. Example `3C39C433-5C18-4F51-B357-55BB870227C4`. |
| `quality` | query | integer | no | The desired quality of the thumbnail. The value is the number of pixels in the _largest_ dimension of the image, i.e. if the image is landscape (wider than it is tall), it is the _width_ of the image. If the image is portrait (taller than it is wide), it is the _height_ of the image. Default `400`. Example `400`. |

**Responses**

- `200`: The request was processed successfully. Content: `image/jpeg`, `image/png`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`image/jpeg`): string (binary)

`200` body (`image/png`): string (binary)

```bash
curl 'http://localhost:50001/v1/media/{uuid}/thumbnail'
```

<a id="mediaplaylistfocused"></a>
### GET `/v1/media/playlist/focused`

Returns the identifier of the currently focused media playlist

Operation ID: `mediaPlaylistFocused`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `uuid` (string, required) — The UUID of the item
- `name` (string, required) — The user-defined name of the item
- `index` (integer, required) — The index of the item

Example

```json
{
  "uuid": "ad19b83b-ee4d-4cfe-9280-220cc9330264",
  "name": "Playlist 1",
  "index": 0
}
```

```bash
curl 'http://localhost:50001/v1/media/playlist/focused'
```

<a id="mediaplaylistactive"></a>
### GET `/v1/media/playlist/active`

Returns the identifier of the currently active media playlist.

Operation ID: `mediaPlaylistActive`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `playlist` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item
- `item` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item

Example

```json
{
  "playlist": {
    "uuid": "ad19b83b-ee4d-4cfe-9280-220cc9330264",
    "name": "Playlist 1",
    "index": 0
  },
  "item": {
    "uuid": "8F40D44E-4018-4552-8803-60DC788DB1CF",
    "name": "Amazing Grace",
    "index": 1
  }
}
```

```bash
curl 'http://localhost:50001/v1/media/playlist/active'
```

<a id="mediaplaylistnextfocus"></a>
### GET `/v1/media/playlist/next/focus`

Sets the focus to the next media playlist.

Operation ID: `mediaPlaylistNextFocus`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/media/playlist/next/focus'
```

<a id="mediaplaylistpreviousfocus"></a>
### GET `/v1/media/playlist/previous/focus`

Sets the focus to the previous media playlist.

Operation ID: `mediaPlaylistPreviousFocus`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/media/playlist/previous/focus'
```

<a id="mediaplaylistactivefocus"></a>
### GET `/v1/media/playlist/active/focus`

Sets the focus to the active media playlist.

Operation ID: `mediaPlaylistActiveFocus`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/media/playlist/active/focus'
```

<a id="mediaplaylistfocus"></a>
### GET `/v1/media/playlist/{playlist_id}/focus`

Sets the focus to the specified media playlist.

Operation ID: `mediaPlaylistFocus`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `playlist_id` | path | string | yes | The ID of the playlist, either UUID, name, or index, in order of interpretation and search priority. A path can also be specified to identify playlists within folders. UUID and name matches are searched through the entire playlist tree. Because UUIDs are unique a path is not necessary. A name path is not necessary unless there are multiple playlists with the same name in different folders. A partial path of names can be used as well. Indices only index into the immediate children at each level of the tree so an index path is requred to access playlists within folders if only using indices. A mix of UUIDs, names, and indices can be used for the path if desired. Example (Match playlist by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match playlist by name): `Playlist Name`. Example (Match playlist with a name path): `Folder1/Folder2/Playlist Name`. Example (Match playlist by index): `3`. Example (Match playlist by index path): `2/0/1`. Example (Match playlist by a mixed path): `3C39C433-5C18-4F51-B357-55BB870227C4/Folder Name/0`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/media/playlist/{playlist_id}/focus'
```

<a id="mediaplaylistfocusedtrigger"></a>
### GET `/v1/media/playlist/focused/trigger`

Triggers the first item in the focused media playlist.

Operation ID: `mediaPlaylistFocusedTrigger`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/media/playlist/focused/trigger'
```

<a id="mediaplaylistactivetrigger"></a>
### GET `/v1/media/playlist/active/trigger`

Triggers the first item in the active media playlist.

Operation ID: `mediaPlaylistActiveTrigger`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/media/playlist/active/trigger'
```

<a id="mediaplaylisttrigger"></a>
### GET `/v1/media/playlist/{playlist_id}/trigger`

Triggers the first item in the specified media playlist.

Operation ID: `mediaPlaylistTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `playlist_id` | path | string | yes | The ID of the playlist, either UUID, name, or index, in order of interpretation and search priority. A path can also be specified to identify playlists within folders. UUID and name matches are searched through the entire playlist tree. Because UUIDs are unique a path is not necessary. A name path is not necessary unless there are multiple playlists with the same name in different folders. A partial path of names can be used as well. Indices only index into the immediate children at each level of the tree so an index path is requred to access playlists within folders if only using indices. A mix of UUIDs, names, and indices can be used for the path if desired. Example (Match playlist by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match playlist by name): `Playlist Name`. Example (Match playlist with a name path): `Folder1/Folder2/Playlist Name`. Example (Match playlist by index): `3`. Example (Match playlist by index path): `2/0/1`. Example (Match playlist by a mixed path): `3C39C433-5C18-4F51-B357-55BB870227C4/Folder Name/0`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/media/playlist/{playlist_id}/trigger'
```

<a id="mediaplaylistfocusednexttrigger"></a>
### GET `/v1/media/playlist/focused/next/trigger`

Triggers the next item in the focused media playlist.

Operation ID: `mediaPlaylistFocusedNextTrigger`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/media/playlist/focused/next/trigger'
```

<a id="mediaplaylistfocusedprevioustrigger"></a>
### GET `/v1/media/playlist/focused/previous/trigger`

Triggers the previous item in the focused media playlist.

Operation ID: `mediaPlaylistFocusedPreviousTrigger`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/media/playlist/focused/previous/trigger'
```

<a id="mediaplaylistfocuseditemtrigger"></a>
### GET `/v1/media/playlist/focused/{media_id}/trigger`

Triggers the specified item in the focused media playlist.

Operation ID: `mediaPlaylistFocusedItemTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `media_id` | path | string | yes | The ID of the media, either UUID, name, or index. Example (Match media by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match media by name): `Mask`. Example (Match media by index): `3`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/media/playlist/focused/{media_id}/trigger'
```

<a id="mediaplaylistactivenexttrigger"></a>
### GET `/v1/media/playlist/active/next/trigger`

Triggers the next item in the active media playlist.

Operation ID: `mediaPlaylistActiveNextTrigger`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/media/playlist/active/next/trigger'
```

<a id="mediaplaylistactiveprevioustrigger"></a>
### GET `/v1/media/playlist/active/previous/trigger`

Triggers the previous item in the active media playlist.

Operation ID: `mediaPlaylistActivePreviousTrigger`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/media/playlist/active/previous/trigger'
```

<a id="mediaplaylistactiveitemtrigger"></a>
### GET `/v1/media/playlist/active/{media_id}/trigger`

Triggers the specified item in the active media playlist.

Operation ID: `mediaPlaylistActiveItemTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `media_id` | path | string | yes | The ID of the media, either UUID, name, or index. Example (Match media by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match media by name): `Mask`. Example (Match media by index): `3`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/media/playlist/active/{media_id}/trigger'
```

<a id="mediaplaylistnexttrigger"></a>
### GET `/v1/media/playlist/{playlist_id}/next/trigger`

Triggers the next item in the specified media playlist.

Operation ID: `mediaPlaylistNextTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `playlist_id` | path | string | yes | The ID of the playlist, either UUID, name, or index, in order of interpretation and search priority. A path can also be specified to identify playlists within folders. UUID and name matches are searched through the entire playlist tree. Because UUIDs are unique a path is not necessary. A name path is not necessary unless there are multiple playlists with the same name in different folders. A partial path of names can be used as well. Indices only index into the immediate children at each level of the tree so an index path is requred to access playlists within folders if only using indices. A mix of UUIDs, names, and indices can be used for the path if desired. Example (Match playlist by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match playlist by name): `Playlist Name`. Example (Match playlist with a name path): `Folder1/Folder2/Playlist Name`. Example (Match playlist by index): `3`. Example (Match playlist by index path): `2/0/1`. Example (Match playlist by a mixed path): `3C39C433-5C18-4F51-B357-55BB870227C4/Folder Name/0`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/media/playlist/{playlist_id}/next/trigger'
```

<a id="mediaplaylistprevioustrigger"></a>
### GET `/v1/media/playlist/{playlist_id}/previous/trigger`

Triggers the previous item in the specified media playlist.

Operation ID: `mediaPlaylistPreviousTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `playlist_id` | path | string | yes | The ID of the playlist, either UUID, name, or index, in order of interpretation and search priority. A path can also be specified to identify playlists within folders. UUID and name matches are searched through the entire playlist tree. Because UUIDs are unique a path is not necessary. A name path is not necessary unless there are multiple playlists with the same name in different folders. A partial path of names can be used as well. Indices only index into the immediate children at each level of the tree so an index path is requred to access playlists within folders if only using indices. A mix of UUIDs, names, and indices can be used for the path if desired. Example (Match playlist by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match playlist by name): `Playlist Name`. Example (Match playlist with a name path): `Folder1/Folder2/Playlist Name`. Example (Match playlist by index): `3`. Example (Match playlist by index path): `2/0/1`. Example (Match playlist by a mixed path): `3C39C433-5C18-4F51-B357-55BB870227C4/Folder Name/0`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/media/playlist/{playlist_id}/previous/trigger'
```

<a id="mediaplaylistitemtrigger"></a>
### GET `/v1/media/playlist/{playlist_id}/{media_id}/trigger`

Triggers the specified item in the specified media playlist.

Operation ID: `mediaPlaylistItemTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `playlist_id` | path | string | yes | The ID of the playlist, either UUID, name, or index, in order of interpretation and search priority. A path can also be specified to identify playlists within folders. UUID and name matches are searched through the entire playlist tree. Because UUIDs are unique a path is not necessary. A name path is not necessary unless there are multiple playlists with the same name in different folders. A partial path of names can be used as well. Indices only index into the immediate children at each level of the tree so an index path is requred to access playlists within folders if only using indices. A mix of UUIDs, names, and indices can be used for the path if desired. Example (Match playlist by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match playlist by name): `Playlist Name`. Example (Match playlist with a name path): `Folder1/Folder2/Playlist Name`. Example (Match playlist by index): `3`. Example (Match playlist by index path): `2/0/1`. Example (Match playlist by a mixed path): `3C39C433-5C18-4F51-B357-55BB870227C4/Folder Name/0`. |
| `media_id` | path | string | yes | The ID of the media, either UUID, name, or index. Example (Match media by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match media by name): `Mask`. Example (Match media by index): `3`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/media/playlist/{playlist_id}/{media_id}/trigger'
```

## Message

<a id="messagegetall"></a>
### GET `/v1/messages`

Requests a list of all configured messages.

Operation ID: `messageGetAll`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): array of object. The list of all defined messages.

- `id` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item
- `message` (string, required) — The base string for the message. Any instance of `{<name>}` is replaced with the value of the token identified by the name `<name>`.
- `tokens` (array of oneOf of object | object | object) — An array of the substrings and tokens that make up the message detail.
  - *oneOf option 1*: object
    - `name` (string, required)
    - `text` (object)
      - `text` (string, required)
  - *oneOf option 2*: object
    - `name` (string, required)
    - `timer` (object)
      - `configuration` (oneOf of object | object | object, required)
        - *oneOf option 1*: object
          - `id` (allOf of object, required)
            - *allOf option 1*: object
          - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
          - `countdown` (object)
            - `duration` (integer, required) — Time in seconds.
        - *oneOf option 2*: object
          - `id` (allOf of object, required)
            - *allOf option 1*: object
          - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
          - `count_down_to_time` (object)
            - `time_of_day` (integer, required) — Time in seconds.
            - `period` (enum: `am`, `pm`, `24_hour`, required)
        - *oneOf option 3*: object
          - `id` (allOf of object, required)
            - *allOf option 1*: object
          - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
          - `elapsed` (object)
            - `start_time` (integer, required) — Time in seconds.
            - `end_time` (integer) — Time in seconds. Omit to specify unlimited end time.
      - `format` (object, required)
        - `time_format` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`)
        - `hour` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
        - `minute` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
        - `second` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
        - `millisecond` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
  - *oneOf option 3*: object
    - `name` (string, required)
    - `clock` (object)
      - `format` (object, required)
        - `date_format` (enum: `none`, `short`, `medium`, `long`, `full`)
        - `date` (enum: `none`, `short`, `medium`, `long`, `full`, required)
        - `time` (enum: `none`, `short`, `medium`, `long`, `full`, required)
        - `is_24_hours` (boolean, required)
- `theme` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item
- `visible_on_network` (bool, required) — "true" if the message can be triggered by the message web page, "false" if not
- `is_active` (bool, required) — "true" if the message is currently showing on screen, "false" otherwise

Example: built from the schema's field examples

```json
[
  {
    "id": {
      "name": "Test Message",
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "index": 0
    },
    "message": "Text {text}, Clock {System Clock}, Countdown {Timer 1}, Countdown to time {Timer 2}, Elapsed {Timer 3}",
    "tokens": [
      {
        "name": "text",
        "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5E",
        "text": {
          "text": "142"
        }
      },
      {
        "name": "System Clock",
        "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5F",
        "clock": {
          "date": "short",
          "time": "long",
          "is_24_hours": true
        }
      },
      {
        "name": "Timer 1",
        "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A50",
        "timer": {
          "configuration": {
            "id": {
              "name": "Timer 1",
              "uuid": "43b4c5c6-145a-4b19-9968-7b78e6980e82",
              "index": 0
            },
            "allows_overrun": true,
            "countdown": {
              "duration": 300
            }
          },
          "format": {
            "hour": "remove_long",
            "minute": "remove_long",
            "second": "short",
            "millisecond": "none"
          }
        }
      },
      {
        "name": "Timer 2",
        "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A51",
        "timer": {
          "configuration": {
            "id": {
              "name": "Timer 2",
              "uuid": "1355b458-5f99-4e6e-8ac7-8522061f6bdb",
              "index": 0
            },
            "allows_overrun": true,
            "count_down_to_time": {
              "time_of_day": "09:30",
              "period": "am"
            }
          },
          "format": {
            "hour": "remove_long",
            "minute": "remove_long",
            "second": "short",
            "millisecond": "none"
          }
… (35 more lines; see the published description)
```

```bash
curl 'http://localhost:50001/v1/messages'
```

<a id="messagecreate"></a>
### POST `/v1/messages`

Creates a new message with specified details.

The UUID and index are ignored in the request body. They are automatically
assigned and returned in the response body.

Operation ID: `messageCreate`

**Request body** (`application/json`, required): object

- `id` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item
- `message` (string, required) — The base string for the message. Any instance of `{<name>}` is replaced with the value of the token identified by the name `<name>`.
- `tokens` (array of oneOf of object | object | object) — An array of the substrings and tokens that make up the message detail.
  - *oneOf option 1*: object
    - `name` (string, required)
    - `text` (object)
      - `text` (string, required)
  - *oneOf option 2*: object
    - `name` (string, required)
    - `timer` (object)
      - `configuration` (oneOf of object | object | object, required)
        - *oneOf option 1*: object
          - `id` (allOf of object, required)
            - *allOf option 1*: object
          - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
          - `countdown` (object)
            - `duration` (integer, required) — Time in seconds.
        - *oneOf option 2*: object
          - `id` (allOf of object, required)
            - *allOf option 1*: object
          - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
          - `count_down_to_time` (object)
            - `time_of_day` (integer, required) — Time in seconds.
            - `period` (enum: `am`, `pm`, `24_hour`, required)
        - *oneOf option 3*: object
          - `id` (allOf of object, required)
            - *allOf option 1*: object
          - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
          - `elapsed` (object)
            - `start_time` (integer, required) — Time in seconds.
            - `end_time` (integer) — Time in seconds. Omit to specify unlimited end time.
      - `format` (object, required)
        - `time_format` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`)
        - `hour` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
        - `minute` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
        - `second` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
        - `millisecond` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
  - *oneOf option 3*: object
    - `name` (string, required)
    - `clock` (object)
      - `format` (object, required)
        - `date_format` (enum: `none`, `short`, `medium`, `long`, `full`)
        - `date` (enum: `none`, `short`, `medium`, `long`, `full`, required)
        - `time` (enum: `none`, `short`, `medium`, `long`, `full`, required)
        - `is_24_hours` (boolean, required)
- `theme` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item
- `visible_on_network` (bool, required) — "true" if the message can be triggered by the message web page, "false" if not
- `is_active` (bool, required) — "true" if the message is currently showing on screen, "false" otherwise

Example: built from the schema's field examples

```json
{
  "id": {
    "name": "Test Message",
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "index": 0
  },
  "message": "Text {text}, Clock {System Clock}, Countdown {Timer 1}, Countdown to time {Timer 2}, Elapsed {Timer 3}",
  "tokens": [
    {
      "name": "text",
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5E",
      "text": {
        "text": "142"
      }
    },
    {
      "name": "System Clock",
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5F",
      "clock": {
        "date": "short",
        "time": "long",
        "is_24_hours": true
      }
    },
    {
      "name": "Timer 1",
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A50",
      "timer": {
        "configuration": {
          "id": {
            "name": "Timer 1",
            "uuid": "43b4c5c6-145a-4b19-9968-7b78e6980e82",
            "index": 0
          },
          "allows_overrun": true,
          "countdown": {
            "duration": 300
          }
        },
        "format": {
          "hour": "remove_long",
          "minute": "remove_long",
          "second": "short",
          "millisecond": "none"
        }
      }
    },
    {
      "name": "Timer 2",
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A51",
      "timer": {
        "configuration": {
          "id": {
            "name": "Timer 2",
            "uuid": "1355b458-5f99-4e6e-8ac7-8522061f6bdb",
            "index": 0
          },
          "allows_overrun": true,
          "count_down_to_time": {
            "time_of_day": "09:30",
            "period": "am"
          }
        },
        "format": {
          "hour": "remove_long",
          "minute": "remove_long",
          "second": "short",
          "millisecond": "none"
        }
      }
… (33 more lines; see the published description)
```

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `id` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item
- `message` (string, required) — The base string for the message. Any instance of `{<name>}` is replaced with the value of the token identified by the name `<name>`.
- `tokens` (array of oneOf of object | object | object) — An array of the substrings and tokens that make up the message detail.
  - *oneOf option 1*: object
    - `name` (string, required)
    - `text` (object)
      - `text` (string, required)
  - *oneOf option 2*: object
    - `name` (string, required)
    - `timer` (object)
      - `configuration` (oneOf of object | object | object, required)
        - *oneOf option 1*: object
          - `id` (allOf of object, required)
            - *allOf option 1*: object
          - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
          - `countdown` (object)
            - `duration` (integer, required) — Time in seconds.
        - *oneOf option 2*: object
          - `id` (allOf of object, required)
            - *allOf option 1*: object
          - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
          - `count_down_to_time` (object)
            - `time_of_day` (integer, required) — Time in seconds.
            - `period` (enum: `am`, `pm`, `24_hour`, required)
        - *oneOf option 3*: object
          - `id` (allOf of object, required)
            - *allOf option 1*: object
          - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
          - `elapsed` (object)
            - `start_time` (integer, required) — Time in seconds.
            - `end_time` (integer) — Time in seconds. Omit to specify unlimited end time.
      - `format` (object, required)
        - `time_format` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`)
        - `hour` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
        - `minute` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
        - `second` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
        - `millisecond` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
  - *oneOf option 3*: object
    - `name` (string, required)
    - `clock` (object)
      - `format` (object, required)
        - `date_format` (enum: `none`, `short`, `medium`, `long`, `full`)
        - `date` (enum: `none`, `short`, `medium`, `long`, `full`, required)
        - `time` (enum: `none`, `short`, `medium`, `long`, `full`, required)
        - `is_24_hours` (boolean, required)
- `theme` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item
- `visible_on_network` (bool, required) — "true" if the message can be triggered by the message web page, "false" if not
- `is_active` (bool, required) — "true" if the message is currently showing on screen, "false" otherwise

Example: built from the schema's field examples

```json
{
  "id": {
    "name": "Test Message",
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "index": 0
  },
  "message": "Text {text}, Clock {System Clock}, Countdown {Timer 1}, Countdown to time {Timer 2}, Elapsed {Timer 3}",
  "tokens": [
    {
      "name": "text",
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5E",
      "text": {
        "text": "142"
      }
    },
    {
      "name": "System Clock",
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5F",
      "clock": {
        "date": "short",
        "time": "long",
        "is_24_hours": true
      }
    },
    {
      "name": "Timer 1",
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A50",
      "timer": {
        "configuration": {
          "id": {
            "name": "Timer 1",
            "uuid": "43b4c5c6-145a-4b19-9968-7b78e6980e82",
            "index": 0
          },
          "allows_overrun": true,
          "countdown": {
            "duration": 300
          }
        },
        "format": {
          "hour": "remove_long",
          "minute": "remove_long",
          "second": "short",
          "millisecond": "none"
        }
      }
    },
    {
      "name": "Timer 2",
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A51",
      "timer": {
        "configuration": {
          "id": {
            "name": "Timer 2",
            "uuid": "1355b458-5f99-4e6e-8ac7-8522061f6bdb",
            "index": 0
          },
          "allows_overrun": true,
          "count_down_to_time": {
            "time_of_day": "09:30",
            "period": "am"
          }
        },
        "format": {
          "hour": "remove_long",
          "minute": "remove_long",
          "second": "short",
          "millisecond": "none"
        }
      }
… (33 more lines; see the published description)
```

```bash
curl -X POST 'http://localhost:50001/v1/messages' -H 'Content-Type: application/json' -d '@body.json'
```

<a id="messageget"></a>
### GET `/v1/message/{id}`

Requests the details of the specified message.

Operation ID: `messageGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the message, either UUID, name, or index. Example (Match message by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match message by name): `Mask`. Example (Match message by index): `3`. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `id` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item
- `message` (string, required) — The base string for the message. Any instance of `{<name>}` is replaced with the value of the token identified by the name `<name>`.
- `tokens` (array of oneOf of object | object | object) — An array of the substrings and tokens that make up the message detail.
  - *oneOf option 1*: object
    - `name` (string, required)
    - `text` (object)
      - `text` (string, required)
  - *oneOf option 2*: object
    - `name` (string, required)
    - `timer` (object)
      - `configuration` (oneOf of object | object | object, required)
        - *oneOf option 1*: object
          - `id` (allOf of object, required)
            - *allOf option 1*: object
          - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
          - `countdown` (object)
            - `duration` (integer, required) — Time in seconds.
        - *oneOf option 2*: object
          - `id` (allOf of object, required)
            - *allOf option 1*: object
          - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
          - `count_down_to_time` (object)
            - `time_of_day` (integer, required) — Time in seconds.
            - `period` (enum: `am`, `pm`, `24_hour`, required)
        - *oneOf option 3*: object
          - `id` (allOf of object, required)
            - *allOf option 1*: object
          - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
          - `elapsed` (object)
            - `start_time` (integer, required) — Time in seconds.
            - `end_time` (integer) — Time in seconds. Omit to specify unlimited end time.
      - `format` (object, required)
        - `time_format` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`)
        - `hour` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
        - `minute` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
        - `second` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
        - `millisecond` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
  - *oneOf option 3*: object
    - `name` (string, required)
    - `clock` (object)
      - `format` (object, required)
        - `date_format` (enum: `none`, `short`, `medium`, `long`, `full`)
        - `date` (enum: `none`, `short`, `medium`, `long`, `full`, required)
        - `time` (enum: `none`, `short`, `medium`, `long`, `full`, required)
        - `is_24_hours` (boolean, required)
- `theme` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item
- `visible_on_network` (bool, required) — "true" if the message can be triggered by the message web page, "false" if not
- `is_active` (bool, required) — "true" if the message is currently showing on screen, "false" otherwise

Example: built from the schema's field examples

```json
{
  "id": {
    "name": "Test Message",
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "index": 0
  },
  "message": "Text {text}, Clock {System Clock}, Countdown {Timer 1}, Countdown to time {Timer 2}, Elapsed {Timer 3}",
  "tokens": [
    {
      "name": "text",
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5E",
      "text": {
        "text": "142"
      }
    },
    {
      "name": "System Clock",
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5F",
      "clock": {
        "date": "short",
        "time": "long",
        "is_24_hours": true
      }
    },
    {
      "name": "Timer 1",
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A50",
      "timer": {
        "configuration": {
          "id": {
            "name": "Timer 1",
            "uuid": "43b4c5c6-145a-4b19-9968-7b78e6980e82",
            "index": 0
          },
          "allows_overrun": true,
          "countdown": {
            "duration": 300
          }
        },
        "format": {
          "hour": "remove_long",
          "minute": "remove_long",
          "second": "short",
          "millisecond": "none"
        }
      }
    },
    {
      "name": "Timer 2",
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A51",
      "timer": {
        "configuration": {
          "id": {
            "name": "Timer 2",
            "uuid": "1355b458-5f99-4e6e-8ac7-8522061f6bdb",
            "index": 0
          },
          "allows_overrun": true,
          "count_down_to_time": {
            "time_of_day": "09:30",
            "period": "am"
          }
        },
        "format": {
          "hour": "remove_long",
          "minute": "remove_long",
          "second": "short",
          "millisecond": "none"
        }
      }
… (33 more lines; see the published description)
```

```bash
curl 'http://localhost:50001/v1/message/{id}'
```

<a id="messageset"></a>
### PUT `/v1/message/{id}`

Sets the details of the specified message.

The UUID and index in the request body are ignored. They are not updated.

Operation ID: `messageSet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the message, either UUID, name, or index. Example (Match message by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match message by name): `Mask`. Example (Match message by index): `3`. |

**Request body** (`application/json`, required): object

- `id` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item
- `message` (string, required) — The base string for the message. Any instance of `{<name>}` is replaced with the value of the token identified by the name `<name>`.
- `tokens` (array of oneOf of object | object | object) — An array of the substrings and tokens that make up the message detail.
  - *oneOf option 1*: object
    - `name` (string, required)
    - `text` (object)
      - `text` (string, required)
  - *oneOf option 2*: object
    - `name` (string, required)
    - `timer` (object)
      - `configuration` (oneOf of object | object | object, required)
        - *oneOf option 1*: object
          - `id` (allOf of object, required)
            - *allOf option 1*: object
          - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
          - `countdown` (object)
            - `duration` (integer, required) — Time in seconds.
        - *oneOf option 2*: object
          - `id` (allOf of object, required)
            - *allOf option 1*: object
          - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
          - `count_down_to_time` (object)
            - `time_of_day` (integer, required) — Time in seconds.
            - `period` (enum: `am`, `pm`, `24_hour`, required)
        - *oneOf option 3*: object
          - `id` (allOf of object, required)
            - *allOf option 1*: object
          - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
          - `elapsed` (object)
            - `start_time` (integer, required) — Time in seconds.
            - `end_time` (integer) — Time in seconds. Omit to specify unlimited end time.
      - `format` (object, required)
        - `time_format` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`)
        - `hour` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
        - `minute` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
        - `second` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
        - `millisecond` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
  - *oneOf option 3*: object
    - `name` (string, required)
    - `clock` (object)
      - `format` (object, required)
        - `date_format` (enum: `none`, `short`, `medium`, `long`, `full`)
        - `date` (enum: `none`, `short`, `medium`, `long`, `full`, required)
        - `time` (enum: `none`, `short`, `medium`, `long`, `full`, required)
        - `is_24_hours` (boolean, required)
- `theme` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item
- `visible_on_network` (bool, required) — "true" if the message can be triggered by the message web page, "false" if not
- `is_active` (bool, required) — "true" if the message is currently showing on screen, "false" otherwise

Example: Text token

```json
{
  "id": {
    "name": "Message with text token",
    "uuid": "",
    "index": 0
  },
  "message": "Please come to room {room}",
  "tokens": [
    {
      "name": "room",
      "text": {
        "text": "142"
      }
    }
  ],
  "theme": {
    "name": "",
    "uuid": "",
    "index": 0
  },
  "visible_on_network": true
}
```

Example: Clock Token

```json
{
  "id": {
    "name": "Message with clock token",
    "uuid": "",
    "index": 0
  },
  "message": "The current time is {time}",
  "tokens": [
    {
      "name": "time",
      "clock": {
        "date": "short",
        "time": "long",
        "is_24_hours": true
      }
    }
  ],
  "theme": {
    "name": "",
    "uuid": "",
    "index": 0
  },
  "visible_on_network": true
}
```

Example: Timer Token - Countdown

```json
{
  "id": {
    "name": "Message with countdown timer token",
    "uuid": "",
    "index": 0
  },
  "message": "Time until start: {Timer Token}",
  "tokens": [
    {
      "name": "Timer Token",
      "timer": {
        "id": {
          "name": "Countdown Timer",
          "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
          "index": 1
        },
        "allows_overrun": true,
        "format": {
          "hour": "remove_long",
          "minute": "remove_long",
          "second": "short",
          "millisecond": "none"
        },
        "countdown": {
          "duration": 300
        }
      }
    }
  ],
  "theme": {
    "name": "",
    "uuid": "",
    "index": 0
  },
  "visible_on_network": true
}
```

Example: Timer Token - Countdown To Time

```json
{
  "id": {
    "name": "Message with countdown to time timer token",
    "uuid": "",
    "index": 0
  },
  "message": "Time until start: {Timer Token}",
  "tokens": [
    {
      "name": "Timer Token",
      "timer": {
        "id": {
          "name": "Count Down to Time Timer",
          "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
          "index": 1
        },
        "allows_overrun": true,
        "format": {
          "hour": "remove_long",
          "minute": "remove_long",
          "second": "short",
          "millisecond": "none"
        },
        "count_down_to_time": {
          "time_of_day": 3600,
          "period": "pm"
        }
      }
    }
  ],
  "visible_on_network": true,
  "theme": {
    "name": "",
    "uuid": "",
    "index": 0
  }
}
```

Example: Timer Token - Elapsed

```json
{
  "id": {
    "name": "Message with elapsed timer token",
    "uuid": "",
    "index": 0
  },
  "message": "Time since start: {Timer Token}",
  "tokens": [
    {
      "name": "Timer Token",
      "timer": {
        "id": {
          "name": "Elapsed Timer",
          "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
          "index": 1
        },
        "allows_overrun": true,
        "format": {
          "hour": "remove_long",
          "minute": "remove_long",
          "second": "short",
          "millisecond": "none"
        },
        "elapsed": {
          "start_time": 0,
          "end_time": 60
        }
      }
    }
  ],
  "theme": {
    "name": "",
    "uuid": "",
    "index": 0
  },
  "visible_on_network": true
}
```

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `id` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item
- `message` (string, required) — The base string for the message. Any instance of `{<name>}` is replaced with the value of the token identified by the name `<name>`.
- `tokens` (array of oneOf of object | object | object) — An array of the substrings and tokens that make up the message detail.
  - *oneOf option 1*: object
    - `name` (string, required)
    - `text` (object)
      - `text` (string, required)
  - *oneOf option 2*: object
    - `name` (string, required)
    - `timer` (object)
      - `configuration` (oneOf of object | object | object, required)
        - *oneOf option 1*: object
          - `id` (allOf of object, required)
            - *allOf option 1*: object
          - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
          - `countdown` (object)
            - `duration` (integer, required) — Time in seconds.
        - *oneOf option 2*: object
          - `id` (allOf of object, required)
            - *allOf option 1*: object
          - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
          - `count_down_to_time` (object)
            - `time_of_day` (integer, required) — Time in seconds.
            - `period` (enum: `am`, `pm`, `24_hour`, required)
        - *oneOf option 3*: object
          - `id` (allOf of object, required)
            - *allOf option 1*: object
          - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
          - `elapsed` (object)
            - `start_time` (integer, required) — Time in seconds.
            - `end_time` (integer) — Time in seconds. Omit to specify unlimited end time.
      - `format` (object, required)
        - `time_format` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`)
        - `hour` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
        - `minute` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
        - `second` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
        - `millisecond` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
  - *oneOf option 3*: object
    - `name` (string, required)
    - `clock` (object)
      - `format` (object, required)
        - `date_format` (enum: `none`, `short`, `medium`, `long`, `full`)
        - `date` (enum: `none`, `short`, `medium`, `long`, `full`, required)
        - `time` (enum: `none`, `short`, `medium`, `long`, `full`, required)
        - `is_24_hours` (boolean, required)
- `theme` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item
- `visible_on_network` (bool, required) — "true" if the message can be triggered by the message web page, "false" if not
- `is_active` (bool, required) — "true" if the message is currently showing on screen, "false" otherwise

Example: built from the schema's field examples

```json
{
  "id": {
    "name": "Test Message",
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "index": 0
  },
  "message": "Text {text}, Clock {System Clock}, Countdown {Timer 1}, Countdown to time {Timer 2}, Elapsed {Timer 3}",
  "tokens": [
    {
      "name": "text",
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5E",
      "text": {
        "text": "142"
      }
    },
    {
      "name": "System Clock",
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5F",
      "clock": {
        "date": "short",
        "time": "long",
        "is_24_hours": true
      }
    },
    {
      "name": "Timer 1",
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A50",
      "timer": {
        "configuration": {
          "id": {
            "name": "Timer 1",
            "uuid": "43b4c5c6-145a-4b19-9968-7b78e6980e82",
            "index": 0
          },
          "allows_overrun": true,
          "countdown": {
            "duration": 300
          }
        },
        "format": {
          "hour": "remove_long",
          "minute": "remove_long",
          "second": "short",
          "millisecond": "none"
        }
      }
    },
    {
      "name": "Timer 2",
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A51",
      "timer": {
        "configuration": {
          "id": {
            "name": "Timer 2",
            "uuid": "1355b458-5f99-4e6e-8ac7-8522061f6bdb",
            "index": 0
          },
          "allows_overrun": true,
          "count_down_to_time": {
            "time_of_day": "09:30",
            "period": "am"
          }
        },
        "format": {
          "hour": "remove_long",
          "minute": "remove_long",
          "second": "short",
          "millisecond": "none"
        }
      }
… (33 more lines; see the published description)
```

```bash
curl -X PUT 'http://localhost:50001/v1/message/{id}' -H 'Content-Type: application/json' -d '{"id": {"name": "Message with text token", "uuid": "", "index": 0}, "message": "Please come to room {room}", "tokens": [{"name": "room", "text": {"text": "142"}}], "theme": {"name": "", "uuid": "", "index": 0}, "visible_on_network": true}'
```

<a id="messaagedelete"></a>
### DELETE `/v1/message/{id}`

Deletes the specified message.

Operation ID: `messaageDelete`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the message, either UUID, name, or index. Example (Match message by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match message by name): `Mask`. Example (Match message by index): `3`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl -X DELETE 'http://localhost:50001/v1/message/{id}'
```

<a id="messagetrigger"></a>
### POST `/v1/message/{id}/trigger`

Triggers / Shows the specified message.

Operation ID: `messageTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the message, either UUID, name, or index. Example (Match message by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match message by name): `Mask`. Example (Match message by index): `3`. |

**Request body** (`application/json`): array of oneOf of object | object | object

- *oneOf option 1*: object
  - `name` (string, required)
  - `text` (object)
    - `text` (string, required)
- *oneOf option 2*: object
  - `name` (string, required)
  - `timer` (object)
    - `configuration` (oneOf of object | object | object, required)
      - *oneOf option 1*: object
        - `id` (allOf of object, required)
          - *allOf option 1*: object
            - `uuid` (string, required) — The UUID of the item
            - `name` (string, required) — The user-defined name of the item
            - `index` (integer, required) — The index of the item
        - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
        - `countdown` (object)
          - `duration` (integer, required) — Time in seconds.
      - *oneOf option 2*: object
        - `id` (allOf of object, required)
          - *allOf option 1*: object
            - `uuid` (string, required) — The UUID of the item
            - `name` (string, required) — The user-defined name of the item
            - `index` (integer, required) — The index of the item
        - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
        - `count_down_to_time` (object)
          - `time_of_day` (integer, required) — Time in seconds.
          - `period` (enum: `am`, `pm`, `24_hour`, required)
      - *oneOf option 3*: object
        - `id` (allOf of object, required)
          - *allOf option 1*: object
            - `uuid` (string, required) — The UUID of the item
            - `name` (string, required) — The user-defined name of the item
            - `index` (integer, required) — The index of the item
        - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
        - `elapsed` (object)
          - `start_time` (integer, required) — Time in seconds.
          - `end_time` (integer) — Time in seconds. Omit to specify unlimited end time.
    - `format` (object, required)
      - `time_format` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`)
      - `hour` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
      - `minute` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
      - `second` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
      - `millisecond` (enum: `none`, `short`, `long`, `remove_short`, `remove_long`, required)
- *oneOf option 3*: object
  - `name` (string, required)
  - `clock` (object)
    - `format` (object, required)
      - `date_format` (enum: `none`, `short`, `medium`, `long`, `full`)
      - `date` (enum: `none`, `short`, `medium`, `long`, `full`, required)
      - `time` (enum: `none`, `short`, `medium`, `long`, `full`, required)
      - `is_24_hours` (boolean, required)

Example: Triggering a message with multiple tokens

```json
[
  {
    "name": "My Text Token",
    "text": {
      "text": "Hello, world!"
    }
  },
  {
    "name": "Countdown Timer",
    "timer": {
      "configuration": {
        "allows_overrun": true,
        "countdown": {
          "duration": 3600
        },
        "format": {
          "hour": "none",
          "minute": "short",
          "second": "long",
          "millisecond": "remove_short"
        }
      }
    }
  },
  {
    "name": "Countdown To Time",
    "timer": {
      "configuration": {
        "allows_overrun": true,
        "count_down_to_time": {
          "time_of_day": 1800,
          "period": "pm"
        },
        "format": {
          "hour": "long",
          "minute": "long",
          "second": "none",
          "millisecond": "none"
        }
      }
    }
  },
  {
    "name": "Elapsed Timer",
    "timer": {
      "configuration": {
        "allows_overrun": false,
        "elapsed": {
          "start_time": 0,
          "end_time": 300
        },
        "format": {
          "hour": "none",
          "minute": "short",
          "second": "long",
          "millisecond": "none"
        }
      }
    }
  },
  {
    "name": "My Clock Token",
    "clock": {
      "date": "full",
      "time": "short",
      "is_24_hours": false
    }
  }
]
```

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl -X POST 'http://localhost:50001/v1/message/{id}/trigger' -H 'Content-Type: application/json' -d '@body.json'
```

<a id="messageclear"></a>
### GET `/v1/message/{id}/clear`

Clears / Hides the specified message.

Operation ID: `messageClear`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the message, either UUID, name, or index. Example (Match message by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match message by name): `Mask`. Example (Match message by index): `3`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/message/{id}/clear'
```

## Miscellaneous

<a id="findmymouse"></a>
### GET `/v1/find_my_mouse`

Executes the "Find My Mouse" operation.

Operation ID: `findMyMouse`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/find_my_mouse'
```

## Playlist

<a id="playlistgetall"></a>
### GET `/v1/playlists`

Requests a list of all configured playlists.

Operation ID: `playlistGetAll`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): array of oneOf of object | object

- *oneOf option 1*: object
  - `id` (object, required)
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
  - `type` (enum: `playlist`, `group`, required) — The type of playlist item: * `playlist` - A standard playlist * `group` - A playlist folder, which can contain other playlists
  - `playlists` (array of oneOf of object | object, required)
- *oneOf option 2*: object
  - `id` (object, required)
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
  - `type` (enum: `playlist`, `group`, required) — The type of playlist item: * `playlist` - A standard playlist * `group` - A playlist folder, which can contain other playlists

Example

```json
[
  {
    "id": {
      "index": 0,
      "name": "Service 2022-01-16",
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D"
    },
    "type": "playlist"
  },
  {
    "id": {
      "index": 1,
      "name": "Evening Services",
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5E"
    },
    "type": "group",
    "playlists": [
      {
        "id": {
          "index": 0,
          "name": "Service 2022-01-16 Evening",
          "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5F"
        },
        "type": "playlist"
      }
    ]
  }
]
```

```bash
curl 'http://localhost:50001/v1/playlists'
```

<a id="playlistcreatetoplevel"></a>
### POST `/v1/playlists`

Creates a playlist with the specified details.

Operation ID: `playlistCreateTopLevel`

**Request body** (`application/json`, required): object

- `name` (string, required) — The name of the new playlist.
- `type` (enum: `playlist`, `group`, required) — The type of playlist item: * `playlist` - A standard playlist * `group` - A playlist folder, which can contain other playlists

Example: Create a regular playlist

```json
{
  "name": "Sunday Service",
  "type": "playlist"
}
```

Example: Create a playlist group

```json
{
  "name": "Evening Services",
  "type": "group"
}
```

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): oneOf of object | object

- *oneOf option 1*: object
  - `id` (object, required)
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
  - `type` (enum: `playlist`, `group`, required) — The type of playlist item: * `playlist` - A standard playlist * `group` - A playlist folder, which can contain other playlists
  - `playlists` (array of oneOf of object | object, required)
- *oneOf option 2*: object
  - `id` (object, required)
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
  - `type` (enum: `playlist`, `group`, required) — The type of playlist item: * `playlist` - A standard playlist * `group` - A playlist folder, which can contain other playlists

Example

```json
{
  "id": {
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "index": 3,
    "name": "Sunday Service"
  },
  "type": "playlist"
}
```

```bash
curl -X POST 'http://localhost:50001/v1/playlists' -H 'Content-Type: application/json' -d '{"name": "Sunday Service", "type": "playlist"}'
```

<a id="playlistget"></a>
### GET `/v1/playlist/{playlist_id}`

Requests a list of the items in the specified playlist.

Operation ID: `playlistGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `playlist_id` | path | string | yes | The ID of the playlist, either UUID, name, or index, in order of interpretation and search priority. A path can also be specified to identify playlists within folders. UUID and name matches are searched through the entire playlist tree. Because UUIDs are unique a path is not necessary. A name path is not necessary unless there are multiple playlists with the same name in different folders. A partial path of names can be used as well. Indices only index into the immediate children at each level of the tree so an index path is requred to access playlists within folders if only using indices. A mix of UUIDs, names, and indices can be used for the path if desired. Example (Match playlist by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match playlist by name): `Playlist Name`. Example (Match playlist with a name path): `Folder1/Folder2/Playlist Name`. Example (Match playlist by index): `3`. Example (Match playlist by index path): `2/0/1`. Example (Match playlist by a mixed path): `3C39C433-5C18-4F51-B357-55BB870227C4/Folder Name/0`. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `id` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item
- `items` (array of object, required)
  - `id` (object, required)
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
  - `type` (enum: `presentation`, `placeholder`, `header`, `media`, `audio`, `livevideo`, required) — The type of playlist item: * `presentation` - A presentation playlist item. * `placeholder` - A placeholder item, used for Planning Center items that have not been linked. * `header` - A header playlist item. * `media` - A media playlist item. * `audio` - An audio playlist item. * `livevideo` a live video playlist item.
  - `is_hidden` (boolean, required) — `true` if the item is hidden, `false` if the item is visible.
  - `is_pco` (boolean, required) — `true` if the item is a Planning Center Online item, `false` otherwise.
  - `header_color` (allOf of object)
    - *allOf option 1*: object
      - `red` (number, required) — The red component of the color, between 0 and 1
      - `green` (number, required) — The green component of the color, between 0 and 1
      - `blue` (number, required) — The blue component of the color, between 0 and 1
      - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.
  - `duration` (integer) — The duration of the item in seconds; only valid for a video, audio or looping presentation item.
  - `presentation_info` (allOf of object)
    - *allOf option 1*: object
      - `presentation_uuid` (string, required) — The UUID of the presentation referenced by a playlist item. This can be used to obtain presentation information from [v1/presentation/{uuid}](#/Presentation/presentationGet).
      - `arrangement_name` (string) — The name of the selected arrangement for the presentation.
  - `target_uuid` (string) — UUID of the target object for this item. It will contain the real UUID of the presentation, audio or media item, which can then be used with the appropriate endpoints for interacting with the real object.

Example: built from the schema's field examples

```json
{
  "id": {
    "index": 0,
    "name": "Sunday Service",
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5A"
  },
  "items": [
    {
      "id": {
        "index": 0,
        "name": "Songs",
        "uuid": ""
      },
      "type": "header",
      "is_hidden": true,
      "is_pco": false,
      "header_color": {
        "red": 0,
        "green": 0.54,
        "blue": 0.87,
        "alpha": 1
      }
    },
    {
      "id": {
        "index": 1,
        "name": "Amazing Grace",
        "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5B"
      },
      "type": "presentation",
      "is_hidden": false,
      "is_pco": false,
      "presentation_info": {
        "presentation_uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
        "arrangement_name": "My Chains"
      }
    },
    {
      "id": {
        "index": 2,
        "name": "Graves Into Gardens",
        "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5C"
      },
      "type": "presentation",
      "is_hidden": false,
      "is_pco": false,
      "presentation_info": {
        "presentation_uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
        "arrangement_name": "Chorus Only"
      }
    },
    {
      "id": {
        "index": 3,
        "name": "Sermon",
        "uuid": ""
      },
      "type": "header",
      "is_hidden": false,
      "is_pco": false,
      "header_color": {
        "red": 0.87,
        "green": 0,
        "blue": 0.54,
        "alpha": 1
      }
    },
    {
      "id": {
        "index": 4,
… (13 more lines; see the published description)
```

```bash
curl 'http://localhost:50001/v1/playlist/{playlist_id}'
```

<a id="playlistput"></a>
### PUT `/v1/playlist/{playlist_id}`

Sets the contents of the specified playlist.

Operation ID: `playlistPut`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `playlist_id` | path | string | yes | The ID of the playlist, either UUID, name, or index, in order of interpretation and search priority. A path can also be specified to identify playlists within folders. UUID and name matches are searched through the entire playlist tree. Because UUIDs are unique a path is not necessary. A name path is not necessary unless there are multiple playlists with the same name in different folders. A partial path of names can be used as well. Indices only index into the immediate children at each level of the tree so an index path is requred to access playlists within folders if only using indices. A mix of UUIDs, names, and indices can be used for the path if desired. Example (Match playlist by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match playlist by name): `Playlist Name`. Example (Match playlist with a name path): `Folder1/Folder2/Playlist Name`. Example (Match playlist by index): `3`. Example (Match playlist by index path): `2/0/1`. Example (Match playlist by a mixed path): `3C39C433-5C18-4F51-B357-55BB870227C4/Folder Name/0`. |

**Request body** (`application/json`, required): array of object

- `id` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item
- `type` (enum: `presentation`, `placeholder`, `header`, `media`, `audio`, `livevideo`, required) — The type of playlist item: * `presentation` - A presentation playlist item. * `placeholder` - A placeholder item, used for Planning Center items that have not been linked. * `header` - A header playlist item. * `media` - A media playlist item. * `audio` - An audio playlist item. * `livevideo` a live video playlist item.
- `is_hidden` (boolean, required) — `true` if the item is hidden, `false` if the item is visible.
- `is_pco` (boolean, required) — `true` if the item is a Planning Center Online item, `false` otherwise.
- `header_color` (allOf of object)
  - *allOf option 1*: object
    - `red` (number, required) — The red component of the color, between 0 and 1
    - `green` (number, required) — The green component of the color, between 0 and 1
    - `blue` (number, required) — The blue component of the color, between 0 and 1
    - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.
- `duration` (integer) — The duration of the item in seconds; only valid for a video, audio or looping presentation item.
- `presentation_info` (allOf of object)
  - *allOf option 1*: object
    - `presentation_uuid` (string, required) — The UUID of the presentation referenced by a playlist item. This can be used to obtain presentation information from [v1/presentation/{uuid}](#/Presentation/presentationGet).
    - `arrangement_name` (string) — The name of the selected arrangement for the presentation.
- `target_uuid` (string) — UUID of the target object for this item. It will contain the real UUID of the presentation, audio or media item, which can then be used with the appropriate endpoints for interacting with the real object.

Example

```json
[
  {
    "id": {
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5C",
      "name": "Announcements",
      "index": 0
    },
    "type": "media",
    "is_hidden": true,
    "is_pco": false
  },
  {
    "id": {
      "uuid": null,
      "name": "Songs",
      "index": 1
    },
    "type": "header",
    "header_color": {
      "red": 0,
      "green": 0.54,
      "blue": 0.87,
      "alpha": 1
    },
    "is_hidden": false,
    "is_pco": false
  },
  {
    "id": {
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "name": "Amazing Grace",
      "index": 2
    },
    "type": "presentation",
    "is_hidden": false,
    "is_pco": false
  }
]
```

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl -X PUT 'http://localhost:50001/v1/playlist/{playlist_id}' -H 'Content-Type: application/json' -d '@body.json'
```

<a id="playlistcreate"></a>
### POST `/v1/playlist/{playlist_id}`

Creates a playlist with the specified details underneath the specified playlist or playlist folder.

An 400 error will be returned if the specified playlist group is not
actually a playlist group (i.e. it is a regular playlist).

Operation ID: `playlistCreate`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `playlist_id` | path | string | yes | The ID of the playlist, either UUID, name, or index, in order of interpretation and search priority. A path can also be specified to identify playlists within folders. UUID and name matches are searched through the entire playlist tree. Because UUIDs are unique a path is not necessary. A name path is not necessary unless there are multiple playlists with the same name in different folders. A partial path of names can be used as well. Indices only index into the immediate children at each level of the tree so an index path is requred to access playlists within folders if only using indices. A mix of UUIDs, names, and indices can be used for the path if desired. Example (Match playlist by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match playlist by name): `Playlist Name`. Example (Match playlist with a name path): `Folder1/Folder2/Playlist Name`. Example (Match playlist by index): `3`. Example (Match playlist by index path): `2/0/1`. Example (Match playlist by a mixed path): `3C39C433-5C18-4F51-B357-55BB870227C4/Folder Name/0`. |

**Request body** (`application/json`, required): object

- `name` (string, required) — The name of the new playlist.
- `type` (enum: `playlist`, `group`, required) — The type of playlist item: * `playlist` - A standard playlist * `group` - A playlist folder, which can contain other playlists

Example: Create a regular playlist

```json
{
  "name": "Sunday Service",
  "type": "playlist"
}
```

Example: Create a playlist group

```json
{
  "name": "Evening Services",
  "type": "group"
}
```

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): oneOf of object | object

- *oneOf option 1*: object
  - `id` (object, required)
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
  - `type` (enum: `playlist`, `group`, required) — The type of playlist item: * `playlist` - A standard playlist * `group` - A playlist folder, which can contain other playlists
  - `playlists` (array of oneOf of object | object, required)
- *oneOf option 2*: object
  - `id` (object, required)
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
  - `type` (enum: `playlist`, `group`, required) — The type of playlist item: * `playlist` - A standard playlist * `group` - A playlist folder, which can contain other playlists

Example

```json
{
  "id": {
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "index": 3,
    "name": "Sunday Services"
  },
  "type": "playlist"
}
```

```bash
curl -X POST 'http://localhost:50001/v1/playlist/{playlist_id}' -H 'Content-Type: application/json' -d '{"name": "Sunday Service", "type": "playlist"}'
```

<a id="playlistactiveget"></a>
### GET `/v1/playlist/active`

Requests the details of the active playlist.

The active playlist and playlist item are returned for both the presentation and announcements layers. Active means the most recently triggered presentation or media cue belonging to a library playlist.

Operation ID: `playlistActiveGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The response was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `presentation` (object, required)
  - `playlist` (object, required)
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
  - `item` (object, required)
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `announcements` (object, required)
  - `playlist` (object, required)
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
  - `item` (object, required)
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item

Example: built from the schema's field examples

```json
{
  "presentation": {
    "playlist": {
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "name": "My Item",
      "index": 3
    },
    "item": {
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "name": "My Item",
      "index": 3
    }
  },
  "announcements": {
    "playlist": {
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "name": "My Item",
      "index": 3
    },
    "item": {
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "name": "My Item",
      "index": 3
    }
  }
}
```

```bash
curl 'http://localhost:50001/v1/playlist/active'
```

<a id="playlistfocusedget"></a>
### GET `/v1/playlist/focused`

Requests the details of the currently focused playlist.

Operation ID: `playlistFocusedGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The response was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `playlist` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item
- `item` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item

Example: built from the schema's field examples

```json
{
  "playlist": {
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "name": "My Item",
    "index": 3
  },
  "item": {
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "name": "My Item",
    "index": 3
  }
}
```

```bash
curl 'http://localhost:50001/v1/playlist/focused'
```

<a id="playlistnextfocus"></a>
### GET `/v1/playlist/next/focus`

Moves the focus to the next playlist.

Operation ID: `playlistNextFocus`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/playlist/next/focus'
```

<a id="playlistpreviousfocus"></a>
### GET `/v1/playlist/previous/focus`

Moves the focus to the previous playlist.

Operation ID: `playlistPreviousFocus`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/playlist/previous/focus'
```

<a id="playlistactivepresentationfocus"></a>
### GET `/v1/playlist/active/presentation/focus`

Moves the focus to the currently active playlist for the presentation destination.

Operation ID: `playlistActivePresentationFocus`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/playlist/active/presentation/focus'
```

<a id="playlistactiveannouncementfocus"></a>
### GET `/v1/playlist/active/announcement/focus`

Moves the focus to the currently active playlist for the announcement destination.

Operation ID: `playlistActiveAnnouncementFocus`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/playlist/active/announcement/focus'
```

<a id="playlistactivepresentationitemthumbnail"></a>
### GET `/v1/playlist/active/presentation/{index}/thumbnail/{cue_index}`

Requests a thumbnail image for the specified item in the currently active playlist for the presentation destination

Operation ID: `playlistActivePresentationItemThumbnail`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `index` | path | integer | yes | The index of the playlist item to use. Example `0`. |
| `cue_index` | path | integer | yes | The index of the cue (slide) to use. Respects the selected arrangement of cues. For media playlist items pass 0 here. Example `0`. |
| `quality` | query | integer | no | The desired quality of the thumbnail. The value is the number of pixels in the _largest_ dimension of the image, i.e. if the image is landscape (wider than it is tall), it is the _width_ of the image. If the image is portrait (taller than it is wide), it is the _height_ of the image. Default `400`. Example `400`. |
| `thumbnail_type` | query | enum: `jpeg`, `png` | no | The desired file format of the thumbnail. It is important to note that this query parameter takes precedence over the accept header. If you leave this blank and do not specify the image format in the accept header, this will return a JPEG image by default. |

**Responses**

- `200`: The request was processed successfully. Content: `image/jpeg`, `image/png`.
- `403`: The request was not authorized.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`image/jpeg`): string (binary)

`200` body (`image/png`): string (binary)

```bash
curl 'http://localhost:50001/v1/playlist/active/presentation/{index}/thumbnail/{cue_index}'
```

<a id="playlistactiveannouncementitemthumbnail"></a>
### GET `/v1/playlist/active/announcement/{index}/thumbnail/{cue_index}`

Requests a thumbnail image for the specified item in the currently active playlist for the announcement destination

Operation ID: `playlistActiveAnnouncementItemThumbnail`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `index` | path | integer | yes | The index of the playlist item to use. Example `0`. |
| `cue_index` | path | integer | yes | The index of the cue (slide) to use. Respects the selected arrangement of cues. For media playlist items pass 0 here. Example `0`. |
| `quality` | query | integer | no | The desired quality of the thumbnail. The value is the number of pixels in the _largest_ dimension of the image, i.e. if the image is landscape (wider than it is tall), it is the _width_ of the image. If the image is portrait (taller than it is wide), it is the _height_ of the image. Default `400`. Example `400`. |
| `thumbnail_type` | query | enum: `jpeg`, `png` | no | The desired file format of the thumbnail. It is important to note that this query parameter takes precedence over the accept header. If you leave this blank and do not specify the image format in the accept header, this will return a JPEG image by default. |

**Responses**

- `200`: The request was processed successfully. Content: `image/jpeg`, `image/png`.
- `403`: The request was not authorized.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`image/jpeg`): string (binary)

`200` body (`image/png`): string (binary)

```bash
curl 'http://localhost:50001/v1/playlist/active/announcement/{index}/thumbnail/{cue_index}'
```

<a id="playlistitemthumbnail"></a>
### GET `/v1/playlist/{playlist_id}/{index}/thumbnail/{cue_index}`

Requests a thumbnail image for the specified item in the specified playlist

Operation ID: `playlistItemThumbnail`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `playlist_id` | path | string | yes | The ID of the playlist, either UUID, name, or index, in order of interpretation and search priority. A path can also be specified to identify playlists within folders. UUID and name matches are searched through the entire playlist tree. Because UUIDs are unique a path is not necessary. A name path is not necessary unless there are multiple playlists with the same name in different folders. A partial path of names can be used as well. Indices only index into the immediate children at each level of the tree so an index path is requred to access playlists within folders if only using indices. A mix of UUIDs, names, and indices can be used for the path if desired. Example (Match playlist by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match playlist by name): `Playlist Name`. Example (Match playlist with a name path): `Folder1/Folder2/Playlist Name`. Example (Match playlist by index): `3`. Example (Match playlist by index path): `2/0/1`. Example (Match playlist by a mixed path): `3C39C433-5C18-4F51-B357-55BB870227C4/Folder Name/0`. |
| `index` | path | integer | yes | The index of the playlist item to use. Example `0`. |
| `cue_index` | path | integer | yes | The index of the cue (slide) to use. Respects the selected arrangement of cues. For media playlist items pass 0 here. Example `0`. |
| `quality` | query | integer | no | The desired quality of the thumbnail. The value is the number of pixels in the _largest_ dimension of the image, i.e. if the image is landscape (wider than it is tall), it is the _width_ of the image. If the image is portrait (taller than it is wide), it is the _height_ of the image. Default `400`. Example `400`. |
| `thumbnail_type` | query | enum: `jpeg`, `png` | no | The desired file format of the thumbnail. It is important to note that this query parameter takes precedence over the accept header. If you leave this blank and do not specify the image format in the accept header, this will return a JPEG image by default. |

**Responses**

- `200`: The request was processed successfully. Content: `image/jpeg`, `image/png`.
- `403`: The request was not authorized.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`image/jpeg`): string (binary)

`200` body (`image/png`): string (binary)

```bash
curl 'http://localhost:50001/v1/playlist/{playlist_id}/{index}/thumbnail/{cue_index}'
```

<a id="playlistfocusedtrigger"></a>
### GET `/v1/playlist/focused/trigger`

Triggers the first item in the currently focused playlist.

Operation ID: `playlistFocusedTrigger`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/playlist/focused/trigger'
```

<a id="playlistactivepresentationtrigger"></a>
### GET `/v1/playlist/active/presentation/trigger`

Triggers the first item in the currently active playlist for the presentation destination.

Operation ID: `playlistActivePresentationTrigger`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/playlist/active/presentation/trigger'
```

<a id="playlistactiveannouncementtrigger"></a>
### GET `/v1/playlist/active/announcement/trigger`

Triggers the first item in the currently active playlist for the announcement destination.

Operation ID: `playlistActiveAnnouncementTrigger`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/playlist/active/announcement/trigger'
```

<a id="playlistfocusednext"></a>
### GET `/v1/playlist/focused/next`

Returns the playlist item that will be triggered by /v1/playlist/focused/next/trigger.

Operation ID: `playlistFocusedNext`

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `id` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item
- `type` (enum: `presentation`, `placeholder`, `header`, `media`, `audio`, `livevideo`, required) — The type of playlist item: * `presentation` - A presentation playlist item. * `placeholder` - A placeholder item, used for Planning Center items that have not been linked. * `header` - A header playlist item. * `media` - A media playlist item. * `audio` - An audio playlist item. * `livevideo` a live video playlist item.
- `is_hidden` (boolean, required) — `true` if the item is hidden, `false` if the item is visible.
- `is_pco` (boolean, required) — `true` if the item is a Planning Center Online item, `false` otherwise.
- `header_color` (allOf of object)
  - *allOf option 1*: object
    - `red` (number, required) — The red component of the color, between 0 and 1
    - `green` (number, required) — The green component of the color, between 0 and 1
    - `blue` (number, required) — The blue component of the color, between 0 and 1
    - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.
- `duration` (integer) — The duration of the item in seconds; only valid for a video, audio or looping presentation item.
- `presentation_info` (allOf of object)
  - *allOf option 1*: object
    - `presentation_uuid` (string, required) — The UUID of the presentation referenced by a playlist item. This can be used to obtain presentation information from [v1/presentation/{uuid}](#/Presentation/presentationGet).
    - `arrangement_name` (string) — The name of the selected arrangement for the presentation.
- `target_uuid` (string) — UUID of the target object for this item. It will contain the real UUID of the presentation, audio or media item, which can then be used with the appropriate endpoints for interacting with the real object.

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "name": "Amazing Grace",
    "index": 2
  },
  "type": "presentation",
  "is_hidden": false,
  "is_pco": false
}
```

```bash
curl 'http://localhost:50001/v1/playlist/focused/next'
```

<a id="playlistfocusedprevious"></a>
### GET `/v1/playlist/focused/previous`

Returns the playlist item that will be triggered by /v1/playlist/focused/previous/trigger.

Operation ID: `playlistFocusedPrevious`

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `id` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item
- `type` (enum: `presentation`, `placeholder`, `header`, `media`, `audio`, `livevideo`, required) — The type of playlist item: * `presentation` - A presentation playlist item. * `placeholder` - A placeholder item, used for Planning Center items that have not been linked. * `header` - A header playlist item. * `media` - A media playlist item. * `audio` - An audio playlist item. * `livevideo` a live video playlist item.
- `is_hidden` (boolean, required) — `true` if the item is hidden, `false` if the item is visible.
- `is_pco` (boolean, required) — `true` if the item is a Planning Center Online item, `false` otherwise.
- `header_color` (allOf of object)
  - *allOf option 1*: object
    - `red` (number, required) — The red component of the color, between 0 and 1
    - `green` (number, required) — The green component of the color, between 0 and 1
    - `blue` (number, required) — The blue component of the color, between 0 and 1
    - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.
- `duration` (integer) — The duration of the item in seconds; only valid for a video, audio or looping presentation item.
- `presentation_info` (allOf of object)
  - *allOf option 1*: object
    - `presentation_uuid` (string, required) — The UUID of the presentation referenced by a playlist item. This can be used to obtain presentation information from [v1/presentation/{uuid}](#/Presentation/presentationGet).
    - `arrangement_name` (string) — The name of the selected arrangement for the presentation.
- `target_uuid` (string) — UUID of the target object for this item. It will contain the real UUID of the presentation, audio or media item, which can then be used with the appropriate endpoints for interacting with the real object.

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "name": "Amazing Grace",
    "index": 2
  },
  "type": "presentation",
  "is_hidden": false,
  "is_pco": false
}
```

```bash
curl 'http://localhost:50001/v1/playlist/focused/previous'
```

<a id="playlistfocusednexttrigger"></a>
### GET `/v1/playlist/focused/next/trigger`

Triggers the next item in the currently focused playlist.

Operation ID: `playlistFocusedNextTrigger`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/playlist/focused/next/trigger'
```

<a id="playlistfocusedprevioustrigger"></a>
### GET `/v1/playlist/focused/previous/trigger`

Triggers the previous item in the currently focused playlist.

Operation ID: `playlistFocusedPreviousTrigger`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/playlist/focused/previous/trigger'
```

<a id="playlistupdates"></a>
### GET `/v1/playlist/{playlist_id}/updates`

Requests a chunked data update every time the specified playlist changes.

This endpoint can be used to subscribe to changes to a particular playlist.
The response is chunked, where each chunk is a simple string "change".
This string will be sent whenever the playlist changes in regards to any of the
data returned by `GET /playlist/{playlist_id}`. The updated playlist data can be
queried by recalling `GET /playlist/{playlist_id}`.

Note that this endpoint will return nothing unless the `chunked` parameter is set to `true`.

Operation ID: `playlistUpdates`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `playlist_id` | path | string | yes | The ID of the playlist, either UUID, name, or index, in order of interpretation and search priority. A path can also be specified to identify playlists within folders. UUID and name matches are searched through the entire playlist tree. Because UUIDs are unique a path is not necessary. A name path is not necessary unless there are multiple playlists with the same name in different folders. A partial path of names can be used as well. Indices only index into the immediate children at each level of the tree so an index path is requred to access playlists within folders if only using indices. A mix of UUIDs, names, and indices can be used for the path if desired. Example (Match playlist by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match playlist by name): `Playlist Name`. Example (Match playlist with a name path): `Folder1/Folder2/Playlist Name`. Example (Match playlist by index): `3`. Example (Match playlist by index path): `2/0/1`. Example (Match playlist by a mixed path): `3C39C433-5C18-4F51-B357-55BB870227C4/Folder Name/0`. |
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): enum: `change`

Example: built from the schema's field examples

```json
"change"
```

```bash
curl 'http://localhost:50001/v1/playlist/{playlist_id}/updates'
```

<a id="playlistfocus"></a>
### GET `/v1/playlist/{playlist_id}/focus`

Moves the focus to the specified playlist.

Operation ID: `playlistFocus`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `playlist_id` | path | string | yes | The ID of the playlist, either UUID, name, or index, in order of interpretation and search priority. A path can also be specified to identify playlists within folders. UUID and name matches are searched through the entire playlist tree. Because UUIDs are unique a path is not necessary. A name path is not necessary unless there are multiple playlists with the same name in different folders. A partial path of names can be used as well. Indices only index into the immediate children at each level of the tree so an index path is requred to access playlists within folders if only using indices. A mix of UUIDs, names, and indices can be used for the path if desired. Example (Match playlist by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match playlist by name): `Playlist Name`. Example (Match playlist with a name path): `Folder1/Folder2/Playlist Name`. Example (Match playlist by index): `3`. Example (Match playlist by index path): `2/0/1`. Example (Match playlist by a mixed path): `3C39C433-5C18-4F51-B357-55BB870227C4/Folder Name/0`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/playlist/{playlist_id}/focus'
```

<a id="playlisttrigger"></a>
### GET `/v1/playlist/{playlist_id}/trigger`

Triggers the first item in the specified playlist.

Operation ID: `playlistTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `playlist_id` | path | string | yes | The ID of the playlist, either UUID, name, or index, in order of interpretation and search priority. A path can also be specified to identify playlists within folders. UUID and name matches are searched through the entire playlist tree. Because UUIDs are unique a path is not necessary. A name path is not necessary unless there are multiple playlists with the same name in different folders. A partial path of names can be used as well. Indices only index into the immediate children at each level of the tree so an index path is requred to access playlists within folders if only using indices. A mix of UUIDs, names, and indices can be used for the path if desired. Example (Match playlist by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match playlist by name): `Playlist Name`. Example (Match playlist with a name path): `Folder1/Folder2/Playlist Name`. Example (Match playlist by index): `3`. Example (Match playlist by index path): `2/0/1`. Example (Match playlist by a mixed path): `3C39C433-5C18-4F51-B357-55BB870227C4/Folder Name/0`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/playlist/{playlist_id}/trigger'
```

<a id="playlistnexttrigger"></a>
### GET `/v1/playlist/{playlist_id}/next/trigger`

Triggers the next item in the specified playlist.

Operation ID: `playlistNextTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `playlist_id` | path | string | yes | The ID of the playlist, either UUID, name, or index, in order of interpretation and search priority. A path can also be specified to identify playlists within folders. UUID and name matches are searched through the entire playlist tree. Because UUIDs are unique a path is not necessary. A name path is not necessary unless there are multiple playlists with the same name in different folders. A partial path of names can be used as well. Indices only index into the immediate children at each level of the tree so an index path is requred to access playlists within folders if only using indices. A mix of UUIDs, names, and indices can be used for the path if desired. Example (Match playlist by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match playlist by name): `Playlist Name`. Example (Match playlist with a name path): `Folder1/Folder2/Playlist Name`. Example (Match playlist by index): `3`. Example (Match playlist by index path): `2/0/1`. Example (Match playlist by a mixed path): `3C39C433-5C18-4F51-B357-55BB870227C4/Folder Name/0`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/playlist/{playlist_id}/next/trigger'
```

<a id="playlistprevioustrigger"></a>
### GET `/v1/playlist/{playlist_id}/previous/trigger`

Triggers the previous item in the specified playlist.

Operation ID: `playlistPreviousTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `playlist_id` | path | string | yes | The ID of the playlist, either UUID, name, or index, in order of interpretation and search priority. A path can also be specified to identify playlists within folders. UUID and name matches are searched through the entire playlist tree. Because UUIDs are unique a path is not necessary. A name path is not necessary unless there are multiple playlists with the same name in different folders. A partial path of names can be used as well. Indices only index into the immediate children at each level of the tree so an index path is requred to access playlists within folders if only using indices. A mix of UUIDs, names, and indices can be used for the path if desired. Example (Match playlist by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match playlist by name): `Playlist Name`. Example (Match playlist with a name path): `Folder1/Folder2/Playlist Name`. Example (Match playlist by index): `3`. Example (Match playlist by index path): `2/0/1`. Example (Match playlist by a mixed path): `3C39C433-5C18-4F51-B357-55BB870227C4/Folder Name/0`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/playlist/{playlist_id}/previous/trigger'
```

<a id="playlistitemtrigger"></a>
### GET `/v1/playlist/{playlist_id}/{index}/trigger`

Triggers the specified item in the specified playlist.

Operation ID: `playlistItemTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `playlist_id` | path | string | yes | The ID of the playlist, either UUID, name, or index, in order of interpretation and search priority. A path can also be specified to identify playlists within folders. UUID and name matches are searched through the entire playlist tree. Because UUIDs are unique a path is not necessary. A name path is not necessary unless there are multiple playlists with the same name in different folders. A partial path of names can be used as well. Indices only index into the immediate children at each level of the tree so an index path is requred to access playlists within folders if only using indices. A mix of UUIDs, names, and indices can be used for the path if desired. Example (Match playlist by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match playlist by name): `Playlist Name`. Example (Match playlist with a name path): `Folder1/Folder2/Playlist Name`. Example (Match playlist by index): `3`. Example (Match playlist by index path): `2/0/1`. Example (Match playlist by a mixed path): `3C39C433-5C18-4F51-B357-55BB870227C4/Folder Name/0`. |
| `index` | path | integer | yes | The index of the playlist item to use. Example `0`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/playlist/{playlist_id}/{index}/trigger'
```

<a id="playlistfocuseditemtrigger"></a>
### GET `/v1/playlist/focused/{index}/trigger`

Triggers the specified item in the focused playlist.

Operation ID: `playlistFocusedItemTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `index` | path | integer | yes | The index of the playlist item to use. Example `0`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/playlist/focused/{index}/trigger'
```

<a id="playlistactivepresentationitemtrigger"></a>
### GET `/v1/playlist/active/presentation/{index}/trigger`

Triggers the specified item in the currently active playlist for the presentation destination.

Operation ID: `playlistActivePresentationItemTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `index` | path | integer | yes | The index of the playlist item to use. Example `0`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/playlist/active/presentation/{index}/trigger'
```

<a id="playlistactiveannouncementitemtrigger"></a>
### GET `/v1/playlist/active/announcement/{index}/trigger`

Triggers the specified item in the currently active playlist for the announcement destination.

Operation ID: `playlistActiveAnnouncementItemTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `index` | path | integer | yes | The index of the playlist item to use. Example `0`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/playlist/active/announcement/{index}/trigger'
```

## Presentation

<a id="presentationgetactive"></a>
### GET `/v1/presentation/active`

Requests the details of the currently active presentation.

Operation ID: `presentationGetActive`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `presentation` (object)
  - `id` (allOf of object)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `groups` (array of object, required)
    - `name` (string, required)
    - `color` (object, required)
      - `red` (number, required) — The red component of the color, between 0 and 1
      - `green` (number, required) — The green component of the color, between 0 and 1
      - `blue` (number, required) — The blue component of the color, between 0 and 1
      - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.
    - `slides` (array of object, required)
      - `enabled` (boolean, required) — Is this slide enabled
      - `notes` (string, required) — Notes to be showed along with the slide
      - `text` (string, required) — Slide text
      - `label` (string, required) — The label for the slide
      - `color` (object, required)
        - `red` (number, required) — The red component of the color, between 0 and 1
        - `green` (number, required) — The green component of the color, between 0 and 1
        - `blue` (number, required) — The blue component of the color, between 0 and 1
        - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.
      - `size` (object)
        - `width` (integer, required) — The width of the slide, in pixels
        - `height` (integer, required) — The height of the slide, in pixels
  - `has_timeline` (boolean, required) — True if this presentation has an associated timeline
  - `presentation_path` (string) — The path to the presentation file on disk
  - `destination` (enum: `presentation`, `announcements`, required) — The presentation destination: * `presentation` - Presentation layer * `announcements` - Announcements layer Default `"presentation"`.
  - `has_timeline_items` (bool) — true if the presentation has items in its timeline, false otherwise

Example: built from the schema's field examples

```json
{
  "presentation": {
    "id": {
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "name": "Amazing Grace",
      "index": 2
    },
    "groups": [
      {
        "name": "Verse 1",
        "color": {
          "red": 0,
          "green": 0.54,
          "blue": 0.87,
          "alpha": 1
        },
        "slides": [
          {
            "notes": "",
            "text": "Amazing Grace\nHow sweet the sound",
            "label": "Acoustic",
            "color": {
              "red": 0,
              "green": 0.54,
              "blue": 0.87,
              "alpha": 1
            },
            "size": {
              "width": 1920,
              "height": 1080
            }
          }
        ]
      }
    ],
    "presentation_path": "/Users/someuser/Documents/ProPresenter/Libraries/Default/Amazing Grace.pro",
    "destination": "announcements",
    "has_timeline_items": false
  }
}
```

```bash
curl 'http://localhost:50001/v1/presentation/active'
```

<a id="presentationfocused"></a>
### GET `/v1/presentation/focused`

Gets the currently focused presentation in the main UI.

Operation ID: `presentationFocused`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `uuid` (string, required) — The UUID of the item
- `name` (string, required) — The user-defined name of the item
- `index` (integer, required) — The index of the item

Example: built from the schema's field examples

```json
{
  "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
  "name": "My Item",
  "index": 3
}
```

```bash
curl 'http://localhost:50001/v1/presentation/focused'
```

<a id="presentationgetslideindex"></a>
### GET `/v1/presentation/slide_index`

Requests the index of the current slide/cue within the currently active presentation.

Operation ID: `presentationGetSlideIndex`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `presentation_index` (object)
  - `index` (integer, required)
  - `presentation_id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item

Example: built from the schema's field examples

```json
{
  "presentation_index": {
    "presentation_id": {
      "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
      "name": "Amazing Grace",
      "index": 2
    }
  }
}
```

```bash
curl 'http://localhost:50001/v1/presentation/slide_index'
```

<a id="presentationgetchordchart"></a>
### GET `/v1/presentation/chord_chart`

Requests the current chord chart image (if available) at the given quality value.

Operation ID: `presentationGetChordChart`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `quality` | query | integer | no | The desired quality of the thumbnail. The value is the number of pixels in the _largest_ dimension of the image, i.e. if the image is landscape (wider than it is tall), it is the _width_ of the image. If the image is portrait (taller than it is wide), it is the _height_ of the image. Default `400`. Example `400`. |

**Responses**

- `200`: The request was processed successfully. Content: `image/jpeg`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`image/jpeg`): string (binary)

```bash
curl 'http://localhost:50001/v1/presentation/chord_chart'
```

<a id="presentationgetchordchartupdates"></a>
### GET `/v1/presentation/chord_chart/updates`

Requests a chunked data update every time the chord chart changes.

This endpoint can be used to subscribe to changes to the chord chart.
The response is chunked, where each chunk is a simple string "change".
This string will be sent whenever the chord chart changes.
The updated chord chart can be queried by recalling `GET /presentation/chord_chart`.

Note that this endpoint will return nothing unless the `chunked` parameter is set to `true`.

Operation ID: `presentationGetChordChartUpdates`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): enum: `change`

Example: built from the schema's field examples

```json
"change"
```

```bash
curl 'http://localhost:50001/v1/presentation/chord_chart/updates'
```

<a id="presentationget"></a>
### GET `/v1/presentation/{uuid}`

Requests the details of the specified presentation.

Operation ID: `presentationGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `uuid` | path | string | yes | The UUID of the presentation to look up. Example `3C39C433-5C18-4F51-B357-55BB870227C4`. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `id` (allOf of object)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `groups` (array of object, required)
  - `name` (string, required)
  - `color` (object, required)
    - `red` (number, required) — The red component of the color, between 0 and 1
    - `green` (number, required) — The green component of the color, between 0 and 1
    - `blue` (number, required) — The blue component of the color, between 0 and 1
    - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.
  - `slides` (array of object, required)
    - `enabled` (boolean, required) — Is this slide enabled
    - `notes` (string, required) — Notes to be showed along with the slide
    - `text` (string, required) — Slide text
    - `label` (string, required) — The label for the slide
    - `color` (object, required)
      - `red` (number, required) — The red component of the color, between 0 and 1
      - `green` (number, required) — The green component of the color, between 0 and 1
      - `blue` (number, required) — The blue component of the color, between 0 and 1
      - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.
    - `size` (object)
      - `width` (integer, required) — The width of the slide, in pixels
      - `height` (integer, required) — The height of the slide, in pixels
- `has_timeline` (boolean, required) — True if this presentation has an associated timeline
- `presentation_path` (string) — The path to the presentation file on disk
- `destination` (enum: `presentation`, `announcements`, required) — The presentation destination: * `presentation` - Presentation layer * `announcements` - Announcements layer Default `"presentation"`.
- `has_timeline_items` (bool) — true if the presentation has items in its timeline, false otherwise

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "name": "Amazing Grace",
    "index": 2
  },
  "groups": [
    {
      "name": "Verse 1",
      "color": {
        "red": 0,
        "green": 0.54,
        "blue": 0.87,
        "alpha": 1
      },
      "slides": [
        {
          "notes": "",
          "text": "Amazing Grace\nHow sweet the sound",
          "label": "Acoustic",
          "color": {
            "red": 0,
            "green": 0.54,
            "blue": 0.87,
            "alpha": 1
          },
          "size": {
            "width": 1920,
            "height": 1080
          }
        }
      ]
    }
  ],
  "presentation_path": "/Users/someuser/Documents/ProPresenter/Libraries/Default/Amazing Grace.pro",
  "destination": "announcements",
  "has_timeline_items": false
}
```

```bash
curl 'http://localhost:50001/v1/presentation/{uuid}'
```

<a id="presentationactivetimelinestatus"></a>
### GET `/v1/presentation/active/timeline`

Requests the current state of the timeline for the currently active presentation.

Operation ID: `presentationActiveTimelineStatus`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `is_running` (boolean) — `true` if the timeline is running.
- `current_time` (number) — The current position of the timeline's playhead.

```bash
curl 'http://localhost:50001/v1/presentation/active/timeline'
```

<a id="presentationfocusedtimelinestatus"></a>
### GET `/v1/presentation/focused/timeline`

Requests the current state of the timeline for the currently focused presentation in the main UI.

Operation ID: `presentationFocusedTimelineStatus`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `is_running` (boolean) — `true` if the timeline is running.
- `current_time` (number) — The current position of the timeline's playhead.

```bash
curl 'http://localhost:50001/v1/presentation/focused/timeline'
```

<a id="presentationactivetimelineoperation"></a>
### GET `/v1/presentation/active/timeline/{operation}`

Performs the requested timeline operation (play, pause, rewind) for the currently active presentation.

Operation ID: `presentationActiveTimelineOperation`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `operation` | path | enum: `play`, `pause`, `rewind` | yes | The operation to perform on the timeline |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/presentation/active/timeline/{operation}'
```

<a id="presentationfocusedtimelineoperation"></a>
### GET `/v1/presentation/focused/timeline/{operation}`

Performs the requested timeline operation (play, pause, rewind) for the currently focused presentation in the main UI.

Operation ID: `presentationFocusedTimelineOperation`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `operation` | path | enum: `play`, `pause`, `rewind` | yes | The operation to perform on the timeline |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/presentation/focused/timeline/{operation}'
```

<a id="presentationtimelineoperation"></a>
### GET `/v1/presentation/{uuid}/timeline/{operation}`

Performs the requested timeline operation (play, pause, rewind) for the specified presentation.

Operation ID: `presentationTimelineOperation`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `uuid` | path | string | yes | The UUID of the presentation to look up. Example `3C39C433-5C18-4F51-B357-55BB870227C4`. |
| `operation` | path | enum: `play`, `pause`, `rewind` | yes | The operation to perform on the timeline |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/presentation/{uuid}/timeline/{operation}'
```

<a id="presentationthumbnailget"></a>
### GET `/v1/presentation/{uuid}/thumbnail/{index}`

Requests a thumbnail image of the specified cue inside the specified presentation at the given quality value.

Operation ID: `presentationThumbnailGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `uuid` | path | string | yes | The UUID of the presentation to look up. Example `3C39C433-5C18-4F51-B357-55BB870227C4`. |
| `index` | path | integer | yes | The index of the cue to use. Respects the selected arrangement of cues. Example `0`. |
| `quality` | query | integer | no | The desired quality of the thumbnail. The value is the number of pixels in the _largest_ dimension of the image, i.e. if the image is landscape (wider than it is tall), it is the _width_ of the image. If the image is portrait (taller than it is wide), it is the _height_ of the image. Default `400`. Example `400`. |
| `thumbnail_type` | query | enum: `jpeg`, `png` | no | The desired file format of the thumbnail. It is important to note that this query parameter takes precedence over the accept header. If you leave this blank and do not specify the image format in the accept header, this will return a JPEG image by default. |

**Responses**

- `200`: The request was processed successfully. Content: `image/jpeg`, `image/png`.
- `403`: The request was not authorized.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`image/jpeg`): string (binary)

`200` body (`image/png`): string (binary)

```bash
curl 'http://localhost:50001/v1/presentation/{uuid}/thumbnail/{index}'
```

<a id="presentationnextfocus"></a>
### GET `/v1/presentation/next/focus`

Sets the focus to the next presentation.

Operation ID: `presentationNextFocus`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/presentation/next/focus'
```

<a id="presentationpreviousfocus"></a>
### GET `/v1/presentation/previous/focus`

Sets the focus to the previous presentation.

Operation ID: `presentationPreviousFocus`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/presentation/previous/focus'
```

<a id="presentationactivefocus"></a>
### GET `/v1/presentation/active/focus`

Sets the focus to the active presentation.

Operation ID: `presentationActiveFocus`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/presentation/active/focus'
```

<a id="presentationfocus"></a>
### GET `/v1/presentation/{uuid}/focus`

Sets the focus to the specified presentation.

Operation ID: `presentationFocus`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `uuid` | path | string | yes | The UUID of the presentation to look up. Example `3C39C433-5C18-4F51-B357-55BB870227C4`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/presentation/{uuid}/focus'
```

<a id="presentationfocusedtrigger"></a>
### GET `/v1/presentation/focused/trigger`

Triggers the currently focused presentation in the main UI.

Operation ID: `presentationFocusedTrigger`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/presentation/focused/trigger'
```

<a id="presentationfocusednexttrigger"></a>
### GET `/v1/presentation/focused/next/trigger`

Triggers the next cue of the currently focused presentation in the main UI.

Operation ID: `presentationFocusedNextTrigger`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/presentation/focused/next/trigger'
```

<a id="presentationfocusedprevioustrigger"></a>
### GET `/v1/presentation/focused/previous/trigger`

Triggers the previous cue of the currently focused presentation in the main UI.

Operation ID: `presentationFocusedPreviousTrigger`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/presentation/focused/previous/trigger'
```

<a id="presentationfocusedcuetrigger"></a>
### GET `/v1/presentation/focused/{index}/trigger`

Triggers the specified cue of the currently focused presentation in the main UI.

Operation ID: `presentationFocusedCueTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `index` | path | integer | yes | The index of the cue to use. Respects the selected arrangement of cues. Example `0`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/presentation/focused/{index}/trigger'
```

<a id="presentationactivetrigger"></a>
### GET `/v1/presentation/active/trigger`

Retriggers the active presentation from the start.

Operation ID: `presentationActiveTrigger`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/presentation/active/trigger'
```

<a id="presentationactivenexttrigger"></a>
### GET `/v1/presentation/active/next/trigger`

Triggers the next cue of the active presentation.

Operation ID: `presentationActiveNextTrigger`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/presentation/active/next/trigger'
```

<a id="presentationactiveprevioustrigger"></a>
### GET `/v1/presentation/active/previous/trigger`

Triggers the previous cue of the active presentation.

Operation ID: `presentationActivePreviousTrigger`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/presentation/active/previous/trigger'
```

<a id="presentationactivecuetrigger"></a>
### GET `/v1/presentation/active/{index}/trigger`

Triggers the specified cue of the active presentation.

Operation ID: `presentationActiveCueTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `index` | path | integer | yes | The index of the cue to use. Respects the selected arrangement of cues. Example `0`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/presentation/active/{index}/trigger'
```

<a id="presentationtrigger"></a>
### GET `/v1/presentation/{uuid}/trigger`

Triggers the specified presentation.

Operation ID: `presentationTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `uuid` | path | string | yes | The UUID of the presentation to look up. Example `3C39C433-5C18-4F51-B357-55BB870227C4`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/presentation/{uuid}/trigger'
```

<a id="presentationnexttrigger"></a>
### GET `/v1/presentation/{uuid}/next/trigger`

Triggers the next cue of the specified presentation.

Operation ID: `presentationNextTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `uuid` | path | string | yes | The UUID of the presentation to look up. Example `3C39C433-5C18-4F51-B357-55BB870227C4`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/presentation/{uuid}/next/trigger'
```

<a id="presentationprevioustrigger"></a>
### GET `/v1/presentation/{uuid}/previous/trigger`

Triggers the previous cue of the specified presentation.

Operation ID: `presentationPreviousTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `uuid` | path | string | yes | The UUID of the presentation to look up. Example `3C39C433-5C18-4F51-B357-55BB870227C4`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/presentation/{uuid}/previous/trigger'
```

<a id="presentationcuetrigger"></a>
### GET `/v1/presentation/{uuid}/{index}/trigger`

Triggers the specified cue of the specified presentation.

Operation ID: `presentationCueTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `uuid` | path | string | yes | The UUID of the presentation to look up. Example `3C39C433-5C18-4F51-B357-55BB870227C4`. |
| `index` | path | integer | yes | The index of the cue to use. Respects the selected arrangement of cues. Example `0`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/presentation/{uuid}/{index}/trigger'
```

<a id="presentationfocusedgrouptrigger"></a>
### GET `/v1/presentation/focused/group/{group_id}/trigger`

Triggers the specified group of the currently focused presentation in the main UI.

Operation ID: `presentationFocusedGroupTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `group_id` | path | string | yes | The ID of the group, either UUID, name, or index, in order of interpretation and search priority. Example (Match group by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match group by name): `Library Name`. Example (Match group by index): `3`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/presentation/focused/group/{group_id}/trigger'
```

<a id="presentationactivegrouptrigger"></a>
### GET `/v1/presentation/active/group/{group_id}/trigger`

Triggers the specified group of the active presentation.

Operation ID: `presentationActiveGroupTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `group_id` | path | string | yes | The ID of the group, either UUID, name, or index, in order of interpretation and search priority. Example (Match group by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match group by name): `Library Name`. Example (Match group by index): `3`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/presentation/active/group/{group_id}/trigger'
```

<a id="presentationgrouptrigger"></a>
### GET `/v1/presentation/{uuid}/group/{group_id}/trigger`

Triggers the specified group of the specified presentation.

Operation ID: `presentationGroupTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `uuid` | path | string | yes | The UUID of the presentation to look up. Example `3C39C433-5C18-4F51-B357-55BB870227C4`. |
| `group_id` | path | string | yes | The ID of the group, either UUID, name, or index, in order of interpretation and search priority. Example (Match group by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match group by name): `Library Name`. Example (Match group by index): `3`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/presentation/{uuid}/group/{group_id}/trigger'
```

## Prop

<a id="propgetall"></a>
### GET `/v1/props`

Gets a list of all the props.

Operation ID: `propGetAll`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was completed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): array of object. The list of all the defined props.

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `is_active` (boolean) — `true` if the prop is currently active (triggered), `false` otherwise
- `auto_clear_enabled` (boolean) — `true` if the prop automatically clears after a specified duration, `false` otherwise
- `auto_clear_duration` (number) — The duration in seconds after which the prop will automatically clear. If `auto_clear_enabled` is false, this value is ignored.
- `transition` (allOf of object)
  - *allOf option 1*: object
    - `uuid` (string, required) — UUID of the transition type
    - `name` (string, required) — Name of the transition type, localized according to ProPresenter's current configuration
    - `duration` (number, required) — Duration of the transition in seconds

Example: built from the schema's field examples

```json
[
  {
    "id": {
      "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
      "name": "Prop",
      "index": 1
    },
    "is_active": false,
    "auto_clear_enabled": true,
    "auto_clear_duration": 5,
    "transition": {
      "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
      "name": "Dissolve",
      "duration": 0.6
    }
  }
]
```

```bash
curl 'http://localhost:50001/v1/props'
```

<a id="propget"></a>
### GET `/v1/prop/{id}`

Requests the details of the specified prop.

Operation ID: `propGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the prop, either UUID, name, or index. Example (Match prop by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match prop by name): `Prop Name`. Example (Match prop by index): `3`. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `is_active` (boolean) — `true` if the prop is currently active (triggered), `false` otherwise
- `auto_clear_enabled` (boolean) — `true` if the prop automatically clears after a specified duration, `false` otherwise
- `auto_clear_duration` (number) — The duration in seconds after which the prop will automatically clear. If `auto_clear_enabled` is false, this value is ignored.
- `transition` (allOf of object)
  - *allOf option 1*: object
    - `uuid` (string, required) — UUID of the transition type
    - `name` (string, required) — Name of the transition type, localized according to ProPresenter's current configuration
    - `duration` (number, required) — Duration of the transition in seconds

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
    "name": "Prop",
    "index": 1
  },
  "is_active": false,
  "auto_clear_enabled": true,
  "auto_clear_duration": 5,
  "transition": {
    "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
    "name": "Dissolve",
    "duration": 0.6
  }
}
```

```bash
curl 'http://localhost:50001/v1/prop/{id}'
```

<a id="propput"></a>
### PUT `/v1/prop/{id}`

Sets the details of the specified prop.

Operation ID: `propPut`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the prop, either UUID, name, or index. Example (Match prop by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match prop by name): `Prop Name`. Example (Match prop by index): `3`. |

**Request body** (`application/json`, required): object

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `is_active` (boolean) — `true` if the prop is currently active (triggered), `false` otherwise
- `auto_clear_enabled` (boolean) — `true` if the prop automatically clears after a specified duration, `false` otherwise
- `auto_clear_duration` (number) — The duration in seconds after which the prop will automatically clear. If `auto_clear_enabled` is false, this value is ignored.
- `transition` (allOf of object)
  - *allOf option 1*: object
    - `uuid` (string, required) — UUID of the transition type
    - `name` (string, required) — Name of the transition type, localized according to ProPresenter's current configuration
    - `duration` (number, required) — Duration of the transition in seconds

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
    "name": "Prop",
    "index": 1
  },
  "is_active": false,
  "auto_clear_enabled": true,
  "auto_clear_duration": 5,
  "transition": {
    "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
    "name": "Dissolve",
    "duration": 0.6
  }
}
```

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `is_active` (boolean) — `true` if the prop is currently active (triggered), `false` otherwise
- `auto_clear_enabled` (boolean) — `true` if the prop automatically clears after a specified duration, `false` otherwise
- `auto_clear_duration` (number) — The duration in seconds after which the prop will automatically clear. If `auto_clear_enabled` is false, this value is ignored.
- `transition` (allOf of object)
  - *allOf option 1*: object
    - `uuid` (string, required) — UUID of the transition type
    - `name` (string, required) — Name of the transition type, localized according to ProPresenter's current configuration
    - `duration` (number, required) — Duration of the transition in seconds

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
    "name": "Prop",
    "index": 1
  },
  "is_active": false,
  "auto_clear_enabled": true,
  "auto_clear_duration": 5,
  "transition": {
    "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
    "name": "Dissolve",
    "duration": 0.6
  }
}
```

```bash
curl -X PUT 'http://localhost:50001/v1/prop/{id}' -H 'Content-Type: application/json' -d '{"id": {"uuid": "3C39C433-5C18-4F51-B357-55BB870227C4", "name": "Prop", "index": 1}, "is_active": false, "auto_clear_enabled": true, "auto_clear_duration": 5, "transition": {"uuid": "3C39C433-5C18-4F51-B357-55BB870227C4", "name": "Dissolve", "duration": 0.6}}'
```

<a id="propdelete"></a>
### DELETE `/v1/prop/{id}`

Deletes the specified prop.

Operation ID: `propDelete`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the prop, either UUID, name, or index. Example (Match prop by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match prop by name): `Prop Name`. Example (Match prop by index): `3`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl -X DELETE 'http://localhost:50001/v1/prop/{id}'
```

<a id="proptrigger"></a>
### GET `/v1/prop/{id}/trigger`

Triggers the specified prop.

Operation ID: `propTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the prop, either UUID, name, or index. Example (Match prop by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match prop by name): `Prop Name`. Example (Match prop by index): `3`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/prop/{id}/trigger'
```

<a id="propclear"></a>
### GET `/v1/prop/{id}/clear`

Clears the specified prop.

Operation ID: `propClear`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the prop, either UUID, name, or index. Example (Match prop by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match prop by name): `Prop Name`. Example (Match prop by index): `3`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/prop/{id}/clear'
```

<a id="proppause"></a>
### GET `/v1/prop/{id}/auto_clear/pause`

Pauses auto-clear timer for the specified prop.

Operation ID: `propPause`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the prop, either UUID, name, or index. Example (Match prop by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match prop by name): `Prop Name`. Example (Match prop by index): `3`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/prop/{id}/auto_clear/pause'
```

<a id="propresume"></a>
### GET `/v1/prop/{id}/auto_clear/resume`

Resumes auto-clear timer for the specified prop.

Operation ID: `propResume`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the prop, either UUID, name, or index. Example (Match prop by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match prop by name): `Prop Name`. Example (Match prop by index): `3`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/prop/{id}/auto_clear/resume'
```

<a id="propthumbnailget"></a>
### GET `/v1/prop/{id}/thumbnail`

Requests a thumbnail image of the specified prop at the given quality value.

Operation ID: `propThumbnailGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the prop, either UUID, name, or index. Example (Match prop by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match prop by name): `Prop Name`. Example (Match prop by index): `3`. |
| `quality` | query | integer | no | The desired quality of the thumbnail. The value is the number of pixels in the _largest_ dimension of the image, i.e. if the image is landscape (wider than it is tall), it is the _width_ of the image. If the image is portrait (taller than it is wide), it is the _height_ of the image. Default `400`. Example `400`. |

**Responses**

- `200`: The request was processed successfully. Content: `image/jpeg`, `image/png`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`image/jpeg`): string (binary)

`200` body (`image/png`): string (binary)

```bash
curl 'http://localhost:50001/v1/prop/{id}/thumbnail'
```

<a id="propcollectiongetall"></a>
### GET `/v1/prop_collections`

Requests a list of all collections and their contents

Operation ID: `propCollectionGetAll`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `collections` (array of object, required)
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `props` (array of object) — The props belonging to this collection
    - `id` (allOf of object, required)
      - *allOf option 1*: object
        - `uuid` (string, required) — The UUID of the item
        - `name` (string, required) — The user-defined name of the item
        - `index` (integer, required) — The index of the item
    - `is_active` (boolean) — `true` if the prop is currently active (triggered), `false` otherwise
    - `auto_clear_enabled` (boolean) — `true` if the prop automatically clears after a specified duration, `false` otherwise
    - `auto_clear_duration` (number) — The duration in seconds after which the prop will automatically clear. If `auto_clear_enabled` is false, this value is ignored.
    - `transition` (allOf of object)
      - *allOf option 1*: object
        - `uuid` (string, required) — UUID of the transition type
        - `name` (string, required) — Name of the transition type, localized according to ProPresenter's current configuration
        - `duration` (number, required) — Duration of the transition in seconds
  - `single_prop_enabled` (boolean) — `true` if single prop mode is enabled for this collection, `false` otherwise

Example: built from the schema's field examples

```json
{
  "collections": [
    {
      "id": {
        "uuid": "9131E88E-2B0A-4C08-A722-7FDC448901A9",
        "name": "Collection",
        "index": 1
      },
      "props": [
        {
          "id": {
            "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
            "name": "Prop",
            "index": 1
          },
          "is_active": false,
          "auto_clear_enabled": true,
          "auto_clear_duration": 5,
          "transition": {
            "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
            "name": "Dissolve",
            "duration": 0.6
          }
        }
      ],
      "single_prop_enabled": true
    }
  ]
}
```

```bash
curl 'http://localhost:50001/v1/prop_collections'
```

<a id="propcollectioncreate"></a>
### POST `/v1/prop_collections`

Creates a new prop collection with the specified name

Operation ID: `propCollectionCreate`

**Request body** (`application/json`, required): object

- `name` (string, required) — The name of the new prop

Example: built from the schema's field examples

```json
{
  "name": "My Props"
}
```

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `props` (array of object) — The props belonging to this collection
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `is_active` (boolean) — `true` if the prop is currently active (triggered), `false` otherwise
  - `auto_clear_enabled` (boolean) — `true` if the prop automatically clears after a specified duration, `false` otherwise
  - `auto_clear_duration` (number) — The duration in seconds after which the prop will automatically clear. If `auto_clear_enabled` is false, this value is ignored.
  - `transition` (allOf of object)
    - *allOf option 1*: object
      - `uuid` (string, required) — UUID of the transition type
      - `name` (string, required) — Name of the transition type, localized according to ProPresenter's current configuration
      - `duration` (number, required) — Duration of the transition in seconds
- `single_prop_enabled` (boolean) — `true` if single prop mode is enabled for this collection, `false` otherwise

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "9131E88E-2B0A-4C08-A722-7FDC448901A9",
    "name": "Collection",
    "index": 1
  },
  "props": [
    {
      "id": {
        "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
        "name": "Prop",
        "index": 1
      },
      "is_active": false,
      "auto_clear_enabled": true,
      "auto_clear_duration": 5,
      "transition": {
        "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
        "name": "Dissolve",
        "duration": 0.6
      }
    }
  ],
  "single_prop_enabled": true
}
```

```bash
curl -X POST 'http://localhost:50001/v1/prop_collections' -H 'Content-Type: application/json' -d '{"name": "My Props"}'
```

<a id="propcollectionget"></a>
### GET `/v1/prop_collection/{id}`

Requests a specific collection of props

Operation ID: `propCollectionGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the prop collection, either UUID, name, or index. Example (Match prop collection by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match prop collection by name): `Prop`. Example (Match prop collection by index): `3`. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `props` (array of object) — The props belonging to this collection
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `is_active` (boolean) — `true` if the prop is currently active (triggered), `false` otherwise
  - `auto_clear_enabled` (boolean) — `true` if the prop automatically clears after a specified duration, `false` otherwise
  - `auto_clear_duration` (number) — The duration in seconds after which the prop will automatically clear. If `auto_clear_enabled` is false, this value is ignored.
  - `transition` (allOf of object)
    - *allOf option 1*: object
      - `uuid` (string, required) — UUID of the transition type
      - `name` (string, required) — Name of the transition type, localized according to ProPresenter's current configuration
      - `duration` (number, required) — Duration of the transition in seconds
- `single_prop_enabled` (boolean) — `true` if single prop mode is enabled for this collection, `false` otherwise

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "9131E88E-2B0A-4C08-A722-7FDC448901A9",
    "name": "Collection",
    "index": 1
  },
  "props": [
    {
      "id": {
        "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
        "name": "Prop",
        "index": 1
      },
      "is_active": false,
      "auto_clear_enabled": true,
      "auto_clear_duration": 5,
      "transition": {
        "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
        "name": "Dissolve",
        "duration": 0.6
      }
    }
  ],
  "single_prop_enabled": true
}
```

```bash
curl 'http://localhost:50001/v1/prop_collection/{id}'
```

<a id="propcollectionset"></a>
### PUT `/v1/prop_collection/{id}`

Sets the details of the specified prop collection.

The UUID and index in the request body are ignored. They are not updated.

Operation ID: `propCollectionSet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the prop collection, either UUID, name, or index. Example (Match prop collection by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match prop collection by name): `Prop`. Example (Match prop collection by index): `3`. |

**Request body** (`application/json`, required): object

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `props` (array of object) — The props belonging to this collection
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `is_active` (boolean) — `true` if the prop is currently active (triggered), `false` otherwise
  - `auto_clear_enabled` (boolean) — `true` if the prop automatically clears after a specified duration, `false` otherwise
  - `auto_clear_duration` (number) — The duration in seconds after which the prop will automatically clear. If `auto_clear_enabled` is false, this value is ignored.
  - `transition` (allOf of object)
    - *allOf option 1*: object
      - `uuid` (string, required) — UUID of the transition type
      - `name` (string, required) — Name of the transition type, localized according to ProPresenter's current configuration
      - `duration` (number, required) — Duration of the transition in seconds
- `single_prop_enabled` (boolean) — `true` if single prop mode is enabled for this collection, `false` otherwise

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "9131E88E-2B0A-4C08-A722-7FDC448901A9",
    "name": "Collection",
    "index": 1
  },
  "props": [
    {
      "id": {
        "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
        "name": "Prop",
        "index": 1
      },
      "is_active": false,
      "auto_clear_enabled": true,
      "auto_clear_duration": 5,
      "transition": {
        "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
        "name": "Dissolve",
        "duration": 0.6
      }
    }
  ],
  "single_prop_enabled": true
}
```

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `props` (array of object) — The props belonging to this collection
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `is_active` (boolean) — `true` if the prop is currently active (triggered), `false` otherwise
  - `auto_clear_enabled` (boolean) — `true` if the prop automatically clears after a specified duration, `false` otherwise
  - `auto_clear_duration` (number) — The duration in seconds after which the prop will automatically clear. If `auto_clear_enabled` is false, this value is ignored.
  - `transition` (allOf of object)
    - *allOf option 1*: object
      - `uuid` (string, required) — UUID of the transition type
      - `name` (string, required) — Name of the transition type, localized according to ProPresenter's current configuration
      - `duration` (number, required) — Duration of the transition in seconds
- `single_prop_enabled` (boolean) — `true` if single prop mode is enabled for this collection, `false` otherwise

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "9131E88E-2B0A-4C08-A722-7FDC448901A9",
    "name": "Collection",
    "index": 1
  },
  "props": [
    {
      "id": {
        "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
        "name": "Prop",
        "index": 1
      },
      "is_active": false,
      "auto_clear_enabled": true,
      "auto_clear_duration": 5,
      "transition": {
        "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
        "name": "Dissolve",
        "duration": 0.6
      }
    }
  ],
  "single_prop_enabled": true
}
```

```bash
curl -X PUT 'http://localhost:50001/v1/prop_collection/{id}' -H 'Content-Type: application/json' -d '@body.json'
```

<a id="propcollectiondelete"></a>
### DELETE `/v1/prop_collection/{id}`

Deletes the specified prop collection and any props contained within it.

Operation ID: `propCollectionDelete`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the prop collection, either UUID, name, or index. Example (Match prop collection by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match prop collection by name): `Prop`. Example (Match prop collection by index): `3`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl -X DELETE 'http://localhost:50001/v1/prop_collection/{id}'
```

## Stage

<a id="stagemessageget"></a>
### GET `/v1/stage/message`

Requests the currently active stage message.

Operation ID: `stageMessageGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The response was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): string. The current stage message

Example: No stage message active

```json
""
```

Example: Stage message active

```json
"Hello World"
```

```bash
curl 'http://localhost:50001/v1/stage/message'
```

<a id="stagemessageshow"></a>
### PUT `/v1/stage/message`

Shows the specified stage message on the configured stage screen.

Operation ID: `stageMessageShow`

**Request body** (`application/json`, required): string. The message to show on the stage screens

Example: built from the schema's field examples

```json
"Your message here"
```

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl -X PUT 'http://localhost:50001/v1/stage/message' -H 'Content-Type: application/json' -d '"Your message here"'
```

<a id="stagemessagehide"></a>
### DELETE `/v1/stage/message`

Hides the currently displayed stage message from the configured stage screen.

Operation ID: `stageMessageHide`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl -X DELETE 'http://localhost:50001/v1/stage/message'
```

<a id="stagelayoutmapget"></a>
### GET `/v1/stage/layout_map`

Requests the currently selected stage layout for each configured stage screen.

Operation ID: `stageLayoutMapGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): array of object

- `screen` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `layout` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item

Example: built from the schema's field examples

```json
[
  {
    "screen": {
      "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
      "name": "Screen 1",
      "index": 0
    },
    "layout": {
      "uuid": "3C39C433-5C18-4F51-B357-55BB870227C5",
      "name": "Current/Next Lyrics + Chords",
      "index": 1
    }
  }
]
```

```bash
curl 'http://localhost:50001/v1/stage/layout_map'
```

<a id="stagelayoutmapset"></a>
### PUT `/v1/stage/layout_map`

Sets the specified stage message to the corresponding stage screens.

Operation ID: `stageLayoutMapSet`

**Request body** (`application/json`, required): array of object

- `screen` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `layout` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item

Example: Setting layouts by name

```json
[
  {
    "screen": {
      "name": "Side Screen"
    },
    "layout": {
      "name": "Sermon Notes"
    }
  }
]
```

Example: Setting layouts by index

```json
[
  {
    "screen": {
      "index": 3
    },
    "layout": {
      "index": 7
    }
  }
]
```

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl -X PUT 'http://localhost:50001/v1/stage/layout_map' -H 'Content-Type: application/json' -d '[{"screen": {"name": "Side Screen"}, "layout": {"name": "Sermon Notes"}}]'
```

<a id="stagescreensget"></a>
### GET `/v1/stage/screens`

Requests a list of the configured stage screens.

Operation ID: `stageScreensGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): array of object

- `uuid` (string, required) — The UUID of the item
- `name` (string, required) — The user-defined name of the item
- `index` (integer, required) — The index of the item

Example: built from the schema's field examples

```json
[
  {
    "id": {
      "name": "Stage Left",
      "index": 0,
      "uuid": "BB6E8C3A-8182-4818-8D57-38B1D85333A9"
    }
  },
  {
    "id": {
      "name": "Stage Right",
      "index": 1,
      "uuid": "D2BAE32A-CDE2-4166-BA5C-2276D51EEFB8"
    }
  }
]
```

```bash
curl 'http://localhost:50001/v1/stage/screens'
```

<a id="stagescreenlayoutget"></a>
### GET `/v1/stage/screen/{id}/layout`

Requests the current stage layout for the specified stage screen.

Operation ID: `stageScreenLayoutGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the screen, either UUID, name, or index. Example (Match screen by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match screen by name): `Screen Name`. Example (Match screen by index): `3`. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `uuid` (string, required) — The UUID of the item
- `name` (string, required) — The user-defined name of the item
- `index` (integer, required) — The index of the item

Example

```json
{
  "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
  "name": "Current/Next Lyrics + Chords",
  "index": 1
}
```

```bash
curl 'http://localhost:50001/v1/stage/screen/{id}/layout'
```

<a id="stagescreenlayoutset"></a>
### GET `/v1/stage/screen/{id}/layout/{layout_id}`

Sets the specified stage layout for the specified stage screen.

Operation ID: `stageScreenLayoutSet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the screen, either UUID, name, or index. Example (Match screen by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. Example (Match screen by name): `Screen Name`. Example (Match screen by index): `3`. |
| `layout_id` | path | string | yes | The ID of the stage layout, either name, index or UUID. Example (Match layout by name): `Layout Name`. Example (Match layout by index): `3`. Example (Match layout by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/stage/screen/{id}/layout/{layout_id}'
```

<a id="stagelayoutsget"></a>
### GET `/v1/stage/layouts`

Requests a list of the configured stage layouts.

Operation ID: `stageLayoutsGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): array of object

- `id` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item

Example: built from the schema's field examples

```json
[
  {
    "id": {
      "name": "Current/Next Stacked - Music",
      "index": 0,
      "uuid": "4D542943-6CF2-4BEE-A625-7C37B79D8EEF"
    }
  },
  {
    "id": {
      "name": "Main Screen Preview",
      "index": 1,
      "uiuid": "D2BAE32A-CDE2-4166-BA5C-2276D51EEFB8"
    }
  }
]
```

```bash
curl 'http://localhost:50001/v1/stage/layouts'
```

<a id="stagelayoutdelete"></a>
### DELETE `/v1/stage/layout/{id}`

Deletes the specified stage layout.

Operation ID: `stageLayoutDelete`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `layout_id` | path | string | yes | The ID of the stage layout, either name, index or UUID. Example (Match layout by name): `Layout Name`. Example (Match layout by index): `3`. Example (Match layout by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl -X DELETE 'http://localhost:50001/v1/stage/layout/{id}'
```

<a id="stagelayoutthumbnailget"></a>
### GET `/v1/stage/layout/{id}/thumbnail`

Requests a thumbnail image of the specified stage layout at the given quality value.

Operation ID: `stageLayoutThumbnailGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `layout_id` | path | string | yes | The ID of the stage layout, either name, index or UUID. Example (Match layout by name): `Layout Name`. Example (Match layout by index): `3`. Example (Match layout by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. |
| `quality` | query | integer | no | The desired quality of the thumbnail. The value is the number of pixels in the _largest_ dimension of the image, i.e. if the image is landscape (wider than it is tall), it is the _width_ of the image. If the image is portrait (taller than it is wide), it is the _height_ of the image. Default `400`. Example `400`. |

**Responses**

- `200`: The request was processed successfully. Content: `image/jpeg`, `image/png`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`image/jpeg`): string (binary)

`200` body (`image/png`): string (binary)

```bash
curl 'http://localhost:50001/v1/stage/layout/{id}/thumbnail'
```

## Status

<a id="versionget"></a>
### GET `/version`

Requests the general information about the currently active ProPresenter instance.

Note that this endpoint is not versioned, and will always reside at `/version` regardless of the current
API version. The structure of this response may change, but the fields described below will always exist in
their current form, maintaining backward compatibility.

Operation ID: `versionGet`

**Responses**

- `200`: The response was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `name` (string, required) — Human-readable name representing the machine
- `platform` (enum: `win`, `mac`, `unknown`, required) — The platform that ProPresenter is running on * `win`- Windows * `mac` - macOS * `unknown` - Unknown platform
- `os_version` (string, required) — Version of the operating system.
- `host_description` (string, required) — Description of the host machine
- `api_version` (string, required) — Version of the API supported by this machine

Example: built from the schema's field examples

```json
{
  "name": "Main sanctuary Pro7 machine",
  "platform": "win",
  "os_version": "10.0.19043",
  "host_description": "ProPresenter 7.8.1",
  "api_version": "v1"
}
```

```bash
curl 'http://localhost:50001/version'
```

<a id="layerstatusget"></a>
### GET `/v1/status/layers`

Requests the status of all available layers.

Operation ID: `layerStatusGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The response was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `video_input` (boolean, required) — `true` if the layer is currently active, `false` otherwise
- `media` (boolean, required) — `true` if the layer is currently active, `false` otherwise
- `slide` (boolean, required) — `true` if the layer is currently active, `false` otherwise
- `announcements` (boolean, required) — `true` if the layer is currently active, `false` otherwise
- `props` (boolean, required) — `true` if the layer is currently active, `false` otherwise
- `messages` (boolean) — `true` if the layer is currently active, `false` otherwise
- `audio` (boolean, required) — `true` if the layer is currently active, `false` otherwise

```bash
curl 'http://localhost:50001/v1/status/layers'
```

<a id="stagescreensstatusget"></a>
### GET `/v1/status/stage_screens`

Requests the status of the stage screens.

Operation ID: `stageScreensStatusGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The response was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): boolean. `true` if the stage screens are enabled, `false` otherwise.

```bash
curl 'http://localhost:50001/v1/status/stage_screens'
```

<a id="stagescreensstatusset"></a>
### PUT `/v1/status/stage_screens`

Sets the status of the stage screens.

Operation ID: `stageScreensStatusSet`

**Request body** (`application/json`, required): boolean. `true` to enable the stage screens, `false` otherwise.

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl -X PUT 'http://localhost:50001/v1/status/stage_screens' -H 'Content-Type: application/json' -d '...'
```

<a id="audiencescreensstatusget"></a>
### GET `/v1/status/audience_screens`

Requests the status of the audience screens.

Operation ID: `audienceScreensStatusGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The response was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): boolean. `true` if the audience screens are enabled, `false` otherwise.

```bash
curl 'http://localhost:50001/v1/status/audience_screens'
```

<a id="audiencescreensstatusset"></a>
### PUT `/v1/status/audience_screens`

Sets the status of the audience screens.

Operation ID: `audienceScreensStatusSet`

**Request body** (`application/json`, required): boolean. `true` to enable the audience screens, `false` otherwise.

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl -X PUT 'http://localhost:50001/v1/status/audience_screens' -H 'Content-Type: application/json' -d '...'
```

<a id="statusscreensget"></a>
### GET `/v1/status/screens`

Requests the details of all configured screens.

Operation ID: `statusScreensGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): array of object

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `size` (object, required)
  - `width` (integer, required) — The width of the slide, in pixels
  - `height` (integer, required) — The height of the slide, in pixels
- `screen_type` (enum: `audience`, `stage`, required) — The type of screen this item represents, audience or stage display.

Example: built from the schema's field examples

```json
[
  {
    "id": {
      "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
      "name": "Audience Screen",
      "index": 0
    },
    "size": {
      "width": 1920,
      "height": 1080
    },
    "screen_type": "audience"
  }
]
```

```bash
curl 'http://localhost:50001/v1/status/screens'
```

<a id="slidestatusget"></a>
### GET `/v1/status/slide`

Requests the current/next slide text and image UUIDs.

Operation ID: `slideStatusGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The response was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `current` (object)
  - `text` (string, required) — The text of the slide.
  - `notes` (string, required) — Any notes associated with the slide.
  - `uuid` (string, required) — The UUID of the slide, used when requesting slide images.
- `next` (object)
  - `text` (string, required) — The text of the slide.
  - `notes` (string, required) — Any notes associated with the slide.
  - `uuid` (string, required) — The UUID of the slide, used when requesting slide images.

Example

```json
{
  "current": {
    "text": "Amazing Grace, how sweet the sound\nThat saved a wretch like me.",
    "notes": "Start quiet, leave open",
    "uuid": "1C43092A-B254-45F0-8F8A-9A8FEE26F4DE"
  },
  "next": {
    "text": "I once was lost, but now I'm found\nWas blind, but now I see.",
    "notes": "Build from last chord",
    "uuid": "242BAEBB-FAC5-4B7B-A19D-F4C09A66D711"
  }
}
```

```bash
curl 'http://localhost:50001/v1/status/slide'
```

<a id="statusupdatesget"></a>
### POST `/v1/status/updates`

Aggregates the data from one or more streaming endpoints into a single streaming endpoint.

This endpoint is used to overcome a browser limit on the number of concurrent connections allowed. The following
endpoints are permitted here:
  * [announcement/active/timeline](#announcementactivetimelinestatus)
  * [audio/playlist/{id}/updates](#audioplaylistupdateget)
  * [capture/status](#capturestatusget)
  * [library/{id}](#libraryget)
  * [look/current](#lookgetcurrent)
  * [media/playlists](#mediaplaylistsget)
  * [media/playlist/{id}/updates](#mediaplaylistupdateget)
  * [media/playlist/active](#mediaplaylistactive)
  * [media/playlist/focused](#mediaplaylistfocused)
  * [messages](#messagegetall)
  * [playlist/active](#playlistactiveget)
  * [presentation/current](#presentationgetactive)
  * [presentation/slide_index](#presentationgetslideindex)
  * [presentation/active/timeline](#presentationactivetimelinestatus)
  * [presentation/focused/timeline](#presentationfocusedtimelinestatus)
  * [stage/layouts](#stagelayoutsget)
  * [stage/message](#stagemessageget)
  * [stage/layout_map](#stagelayoutmapget)
  * [stage/screens](#stagescreensget)
  * [status/layers](#layerstatusget)
  * [status/stage_screens](#stagescreensstatusget)
  * [status/audience_screens](#audiencescreensstatusget)
  * [status/screens](#statusscreensget)
  * [status/slide](#slidestatusget)
  * [timers](#timergetall)
  * [timers/current](#timergetcurrenttimes)
  * [timer/system_time](#timergetsystemtime)
  * [timer/video_countdown](#timergetvideocountdown)
  * [transport/{layer}/time](#transporttimeget)
  * [transport/{layer}/current](#transportcurrentmediaget)

In particular, all (and only) GET requests that return a chunked JSON response are legal URLs.

Operation ID: `statusUpdatesGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Request body** (`application/json`, required): array of string

Example: One URL

```json
[
  "status/slide"
]
```

Example: Multiple URLs

```json
[
  "status/slide",
  "timer/video_countdown",
  "presentation/current"
]
```

**Responses**

- `200`: The request was processed successfully. A stream of updates will be returned to the client as they become available. Each update is separated by a CRLF pair, so a section of data followed by a CR-LF-CR-LF sequence is one update. Each update is either a JSON object or SSE event, as below. Content: `application/json`, `text/event-stream`.
- `400`: The given request was invalid. This can be caused by a number of things: * The given URL was not found. * The given URL is not a streaming URL (i.e. it does not return a chunked response). * The given URL does not return a JSON data type. The response body contains a list of URLs that were not valid. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `url` (string) — The URL that caused this chunk of data to be sent
- `data` (any) — The data object that was returned by the given URL. This is in the same data format as the given URL returns natively.

Example: built from the schema's field examples

```json
{
  "url": "/v1/timer/video_countdown",
  "data": "00:00:00"
}
```

`200` body (`text/event-stream`): object

- `event` (string) — The URL that caused this chunk of data to be sent
- `data` (any) — The data object that was returned by the given URL. This is in the same data format as the given URL returns natively.

Example: built from the schema's field examples

```json
{
  "event": "/v1/timer/video_countdown",
  "data": "00:00:00"
}
```

`400` body (`application/json`): array of string

Example

```json
[
  "/this_is_not_a_valid_url",
  "/v1/audio/playlist/0",
  "/v1/presentation/chord_chart"
]
```

```bash
curl -X POST 'http://localhost:50001/v1/status/updates' -H 'Content-Type: application/json' -d '["status/slide"]'
```

## Theme

<a id="themegetall"></a>
### GET `/v1/themes`

Requests a list of all configured themes and theme slides.

Operation ID: `themeGetAll`

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): array of object

- `id` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item
- `themes` (array of object) — The list of themes in this group.
  - `id` (object, required)
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
  - `slides` (array of object, required) — The list of theme slides in this theme.
    - `id` (allOf of object, required)
      - *allOf option 1*: object
        - `uuid` (string, required) — The UUID of the item
        - `name` (string, required) — The user-defined name of the item
        - `index` (integer, required) — The index of the item
    - `size` (object)
      - `width` (integer, required) — The width of the slide, in pixels
      - `height` (integer, required) — The height of the slide, in pixels
    - `background` (object)
      - `red` (number, required) — The red component of the color, between 0 and 1
      - `green` (number, required) — The green component of the color, between 0 and 1
      - `blue` (number, required) — The blue component of the color, between 0 and 1
      - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.
- `groups` (array of object) — The list of child groups in this group.

Example

```json
[
  {
    "id": {
      "uuid": "",
      "name": "My Theme",
      "index": 0
    },
    "groups": [],
    "themes": [
      {
        "id": {
          "uuid": "",
          "name": "My Theme",
          "index": 0
        },
        "slides": [
          {
            "id": {
              "uuid": "ad19b83b-ee4d-4cfe-9280-220cc9330262",
              "name": "New Theme Slide",
              "index": 0
            },
            "size": {
              "width": 1920,
              "height": 1080
            },
            "background": {
              "red": 0,
              "green": 0,
              "blue": 0,
              "alpha": 1
            }
          }
        ]
      }
    ]
  },
  {
    "id": {
      "uuid": "",
      "name": "My Theme Folder",
      "index": 1
    },
    "groups": [
      {
        "id": {
          "uuid": "",
          "name": "Theme 2",
          "index": 0
        },
        "groups": [],
        "themes": [
          {
            "id": {
              "uuid": "",
              "name": "Theme 2",
              "index": 0
            },
            "slides": [
              {
                "id": {
                  "uuid": "b86f287a-1692-4574-a739-5451da814249",
                  "name": "New Theme Slide",
                  "index": 0
                },
                "size": {
                  "width": 1920,
                  "height": 1080
                },
                "background": {
… (14 more lines; see the published description)
```

```bash
curl 'http://localhost:50001/v1/themes'
```

<a id="themeget"></a>
### GET `/v1/theme/{id}`

Requests the details of the theme and theme slides.

Operation ID: `themeGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the theme, either name or index. Example (Match theme by name): `Theme Name`. Example (Match theme by index): `3`. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `id` (object, required)
  - `uuid` (string, required) — The UUID of the item
  - `name` (string, required) — The user-defined name of the item
  - `index` (integer, required) — The index of the item
- `slides` (array of object, required) — The list of theme slides in this theme.
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `size` (object)
    - `width` (integer, required) — The width of the slide, in pixels
    - `height` (integer, required) — The height of the slide, in pixels
  - `background` (object)
    - `red` (number, required) — The red component of the color, between 0 and 1
    - `green` (number, required) — The green component of the color, between 0 and 1
    - `blue` (number, required) — The blue component of the color, between 0 and 1
    - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.

Example

```json
{
  "id": {
    "uuid": "",
    "name": "My Theme",
    "index": 0
  },
  "slides": [
    {
      "id": {
        "uuid": "ad19b83b-ee4d-4cfe-9280-220cc9330262",
        "name": "New Theme Slide",
        "index": 0
      },
      "size": {
        "width": 1920,
        "height": 1080
      },
      "background": {
        "red": 0,
        "green": 0,
        "blue": 0,
        "alpha": 1
      }
    }
  ]
}
```

```bash
curl 'http://localhost:50001/v1/theme/{id}'
```

<a id="themeslideget"></a>
### GET `/v1/theme/{id}/slides/{theme_slide}`

Requests the details of the specified theme slide within the specified theme.

Operation ID: `themeSlideGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the theme, either name or index. Example (Match theme by name): `Theme Name`. Example (Match theme by index): `3`. |
| `theme_slide` | path | string | yes | The ID of the theme slide, either name, index or UUID. Example (Match theme slide by name): `Theme Slide Name`. Example (Match theme slide by index): `3`. Example (Match theme slide by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `size` (object)
  - `width` (integer, required) — The width of the slide, in pixels
  - `height` (integer, required) — The height of the slide, in pixels
- `background` (object)
  - `red` (number, required) — The red component of the color, between 0 and 1
  - `green` (number, required) — The green component of the color, between 0 and 1
  - `blue` (number, required) — The blue component of the color, between 0 and 1
  - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "ad19b83b-ee4d-4cfe-9280-220cc9330262",
    "name": "Theme Slide Name",
    "index": 0
  },
  "size": {
    "width": 1920,
    "height": 1080
  },
  "background": {
    "red": 0,
    "green": 0.54,
    "blue": 0.87,
    "alpha": 1
  }
}
```

```bash
curl 'http://localhost:50001/v1/theme/{id}/slides/{theme_slide}'
```

<a id="themeslideset"></a>
### PUT `/v1/theme/{id}/slides/{theme_slide}`

Sets the details of the specified theme slide within the specified theme.

The UUID and index in the request body are ignored, and are not changed by this request.

Operation ID: `themeSlideSet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the theme, either name or index. Example (Match theme by name): `Theme Name`. Example (Match theme by index): `3`. |
| `theme_slide` | path | string | yes | The ID of the theme slide, either name, index or UUID. Example (Match theme slide by name): `Theme Slide Name`. Example (Match theme slide by index): `3`. Example (Match theme slide by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. |

**Request body** (`application/json`): object

- `id` (allOf of object, required)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `size` (object)
  - `width` (integer, required) — The width of the slide, in pixels
  - `height` (integer, required) — The height of the slide, in pixels
- `background` (object)
  - `red` (number, required) — The red component of the color, between 0 and 1
  - `green` (number, required) — The green component of the color, between 0 and 1
  - `blue` (number, required) — The blue component of the color, between 0 and 1
  - `alpha` (number, required) — The alpha component of the color, between 0 and 1 Default `1`.

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "ad19b83b-ee4d-4cfe-9280-220cc9330262",
    "name": "Theme Slide Name",
    "index": 0
  },
  "size": {
    "width": 1920,
    "height": 1080
  },
  "background": {
    "red": 0,
    "green": 0.54,
    "blue": 0.87,
    "alpha": 1
  }
}
```

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl -X PUT 'http://localhost:50001/v1/theme/{id}/slides/{theme_slide}' -H 'Content-Type: application/json' -d '{"id": {"uuid": "ad19b83b-ee4d-4cfe-9280-220cc9330262", "name": "Theme Slide Name", "index": 0}, "size": {"width": 1920, "height": 1080}, "background": {"red": 0, "green": 0.54, "blue": 0.87, "alpha": 1}}'
```

<a id="themeslidethumbnailget"></a>
### GET `/v1/theme/{id}/slides/{theme_slide}/thumbnail`

Requests a thumbnail image of the specified theme slide at the given quality value.

Operation ID: `themeSlideThumbnailGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the theme, either name or index. Example (Match theme by name): `Theme Name`. Example (Match theme by index): `3`. |
| `theme_slide` | path | string | yes | The ID of the theme slide, either name, index or UUID. Example (Match theme slide by name): `Theme Slide Name`. Example (Match theme slide by index): `3`. Example (Match theme slide by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. |
| `quality` | query | integer | no | The desired quality of the thumbnail. The value is the number of pixels in the _largest_ dimension of the image, i.e. if the image is landscape (wider than it is tall), it is the _width_ of the image. If the image is portrait (taller than it is wide), it is the _height_ of the image. Default `400`. Example `400`. |

**Responses**

- `200`: The request was processed successfully. Content: `image/jpeg`, `image/png`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`image/jpeg`): string (binary)

`200` body (`image/png`): string (binary)

```bash
curl 'http://localhost:50001/v1/theme/{id}/slides/{theme_slide}/thumbnail'
```

## Timer

<a id="timergetall"></a>
### GET `/v1/timers`

Requests the details for all configured timers.

Operation ID: `timerGetAll`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): array of oneOf of object | object | object

- *oneOf option 1*: object
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
  - `countdown` (object)
    - `duration` (integer, required) — Time in seconds.
- *oneOf option 2*: object
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
  - `count_down_to_time` (object)
    - `time_of_day` (integer, required) — Time in seconds.
    - `period` (enum: `am`, `pm`, `24_hour`, required)
- *oneOf option 3*: object
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
  - `elapsed` (object)
    - `start_time` (integer, required) — Time in seconds.
    - `end_time` (integer) — Time in seconds. Omit to specify unlimited end time.

Example

```json
[
  {
    "id": {
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
      "name": "Countdown Timer",
      "index": 0
    },
    "allows_overrun": true,
    "countdown": {
      "duration": 300
    }
  },
  {
    "id": {
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5E",
      "name": "Countdown To Time",
      "index": 0
    },
    "allows_overrun": true,
    "count_down_to_time": {
      "time_of_day": 3600,
      "period": "pm"
    }
  },
  {
    "id": {
      "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5F",
      "name": "Elapsed Timer",
      "index": 0
    },
    "allows_overrun": true,
    "elapsed": {
      "start_time": 0,
      "end_time": 120
    }
  }
]
```

```bash
curl 'http://localhost:50001/v1/timers'
```

<a id="timercreate"></a>
### POST `/v1/timers`

Creates a new timer with the specified details.

The newly created timer's UUID and index are returned in the response body.

Operation ID: `timerCreate`

**Request body** (`application/json`): oneOf of object | object | object

- *oneOf option 1*: object
  - `name` (string) — The user-defined name of the timer
  - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
  - `countdown` (object)
    - `duration` (integer, required) — Time in seconds.
- *oneOf option 2*: object
  - `name` (string) — The user-defined name of the timer
  - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
  - `count_down_to_time` (object)
    - `time_of_day` (integer, required) — Time in seconds.
    - `period` (enum: `am`, `pm`, `24_hour`, required)
- *oneOf option 3*: object
  - `name` (string) — The user-defined name of the timer
  - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
  - `elapsed` (object)
    - `start_time` (integer, required) — Time in seconds.
    - `end_time` (integer) — Time in seconds. Omit to specify unlimited end time.

Example: Countdown configuration

```json
{
  "name": "Timer 1",
  "allows_overrun": true,
  "countdown": {
    "duration": 300
  }
}
```

Example: Countdown to time configuration

```json
{
  "name": "Timer 1",
  "allows_overrun": true,
  "count_down_to_time": {
    "time_of_day": 3600,
    "period": "pm"
  }
}
```

Example: Elapsed configuration

```json
{
  "name": "Timer 1",
  "allows_overrun": true,
  "elapsed": {
    "start_time": 0,
    "end_time": 120
  }
}
```

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): oneOf of object | object | object

- *oneOf option 1*: object
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
  - `countdown` (object)
    - `duration` (integer, required) — Time in seconds.
- *oneOf option 2*: object
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
  - `count_down_to_time` (object)
    - `time_of_day` (integer, required) — Time in seconds.
    - `period` (enum: `am`, `pm`, `24_hour`, required)
- *oneOf option 3*: object
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
  - `elapsed` (object)
    - `start_time` (integer, required) — Time in seconds.
    - `end_time` (integer) — Time in seconds. Omit to specify unlimited end time.

Example: Countdown configuration

```json
{
  "id": {
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "name": "Timer 1",
    "index": 0
  },
  "allows_overrun": true,
  "countdown": {
    "duration": 300
  }
}
```

Example: Countdown to time configuration

```json
{
  "id": {
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "name": "Timer 1",
    "index": 0
  },
  "allows_overrun": true,
  "count_down_to_time": {
    "time_of_day": 3600,
    "period": "pm"
  }
}
```

Example: Elapsed configuration

```json
{
  "id": {
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "name": "Timer 1",
    "index": 0
  },
  "allows_overrun": true,
  "elapsed": {
    "start_time": 0,
    "end_time": 120
  }
}
```

```bash
curl -X POST 'http://localhost:50001/v1/timers' -H 'Content-Type: application/json' -d '{"name": "Timer 1", "allows_overrun": true, "countdown": {"duration": 300}}'
```

<a id="timergetcurrenttimes"></a>
### GET `/v1/timers/current`

Requests the current time for all configured timers.

Operation ID: `timerGetCurrentTimes`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): array of object

- `id` (allOf of object)
  - *allOf option 1*: object
    - `uuid` (string, required) — The UUID of the item
    - `name` (string, required) — The user-defined name of the item
    - `index` (integer, required) — The index of the item
- `time` (string) — The current time for the timer
- `state` (enum: `stopped`, `running`, `complete`, `overrunning`, `overran`)

Example

```json
[
  {
    "id": {
      "uuid": "ad19b83b-ee4d-4cfe-9280-220cc9330262",
      "name": "Countdown Timer",
      "index": 0
    },
    "time": "00:00:01",
    "state": "stopped"
  },
  {
    "id": {
      "uuid": "ad19b83b-ee4d-4cfe-9280-220cc9330262",
      "name": "Countdown To Time",
      "index": 0
    },
    "time": "-00:00:02",
    "state": "overrun"
  },
  {
    "id": {
      "uuid": "ad19b83b-ee4d-4cfe-9280-220cc9330262",
      "name": "Elapsed Timer",
      "index": 0
    },
    "time": "00:21:43",
    "state": "running"
  }
]
```

```bash
curl 'http://localhost:50001/v1/timers/current'
```

<a id="timeroperationall"></a>
### GET `/v1/timers/{operation}`

Performs the requested operation for all configured timers (start, stop, reset).

Operation ID: `timerOperationAll`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `operation` | path | enum: `start`, `stop`, `reset` | yes | The operation to perform on the timer |

**Responses**

- `200`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/timers/{operation}'
```

<a id="timerget"></a>
### GET `/v1/timer/{id}`

Requests the details of the specified timer.

Operation ID: `timerGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the timer, either name, index or UUID. Example (Match timer by name): `Timer Name`. Example (Match timer by index): `3`. Example (Match timer by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): oneOf of object | object | object

- *oneOf option 1*: object
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
  - `countdown` (object)
    - `duration` (integer, required) — Time in seconds.
- *oneOf option 2*: object
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
  - `count_down_to_time` (object)
    - `time_of_day` (integer, required) — Time in seconds.
    - `period` (enum: `am`, `pm`, `24_hour`, required)
- *oneOf option 3*: object
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
  - `elapsed` (object)
    - `start_time` (integer, required) — Time in seconds.
    - `end_time` (integer) — Time in seconds. Omit to specify unlimited end time.

Example: built from the schema's field examples

```json
{
  "id": {
    "uuid": "ad19b83b-ee4d-4cfe-9280-220cc9330262",
    "name": "Countdown Timer",
    "index": 0
  }
}
```

```bash
curl 'http://localhost:50001/v1/timer/{id}'
```

<a id="timerset"></a>
### PUT `/v1/timer/{id}`

Sets the details of the specified timer.

Operation ID: `timerSet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the timer, either name, index or UUID. Example (Match timer by name): `Timer Name`. Example (Match timer by index): `3`. Example (Match timer by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. |

**Request body** (`application/json`, required): oneOf of object | object | object

- *oneOf option 1*: object
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
  - `countdown` (object)
    - `duration` (integer, required) — Time in seconds.
- *oneOf option 2*: object
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
  - `count_down_to_time` (object)
    - `time_of_day` (integer, required) — Time in seconds.
    - `period` (enum: `am`, `pm`, `24_hour`, required)
- *oneOf option 3*: object
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
  - `elapsed` (object)
    - `start_time` (integer, required) — Time in seconds.
    - `end_time` (integer) — Time in seconds. Omit to specify unlimited end time.

Example: Countdown configuration

```json
{
  "id": {
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "name": "Timer 1",
    "index": 0
  },
  "allows_overrun": true,
  "countdown": {
    "duration": 300
  }
}
```

Example: Countdown to time configuration

```json
{
  "id": {
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "name": "Timer 1",
    "index": 0
  },
  "allows_overrun": true,
  "count_down_to_time": {
    "time_of_day": 3600,
    "period": "pm"
  }
}
```

Example: Elapsed configuration

```json
{
  "id": {
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "name": "Timer 1",
    "index": 0
  },
  "allows_overrun": true,
  "elapsed": {
    "start_time": 0,
    "end_time": 120
  }
}
```

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): oneOf of object | object | object

- *oneOf option 1*: object
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
  - `countdown` (object)
    - `duration` (integer, required) — Time in seconds.
- *oneOf option 2*: object
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
  - `count_down_to_time` (object)
    - `time_of_day` (integer, required) — Time in seconds.
    - `period` (enum: `am`, `pm`, `24_hour`, required)
- *oneOf option 3*: object
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
  - `elapsed` (object)
    - `start_time` (integer, required) — Time in seconds.
    - `end_time` (integer) — Time in seconds. Omit to specify unlimited end time.

Example: Countdown configuration

```json
{
  "id": {
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "name": "Timer 1",
    "index": 0
  },
  "allows_overrun": true,
  "countdown": {
    "duration": 300
  }
}
```

Example: Countdown to time configuration

```json
{
  "id": {
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "name": "Timer 1",
    "index": 0
  },
  "allows_overrun": true,
  "count_down_to_time": {
    "time_of_day": 3600,
    "period": "pm"
  }
}
```

Example: Elapsed configuration

```json
{
  "id": {
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "name": "Timer 1",
    "index": 0
  },
  "allows_overrun": true,
  "elapsed": {
    "start_time": 0,
    "end_time": 120
  }
}
```

```bash
curl -X PUT 'http://localhost:50001/v1/timer/{id}' -H 'Content-Type: application/json' -d '{"id": {"uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D", "name": "Timer 1", "index": 0}, "allows_overrun": true, "countdown": {"duration": 300}}'
```

<a id="timerdelete"></a>
### DELETE `/v1/timer/{id}`

Deletes the specified timer.

Operation ID: `timerDelete`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the timer, either name, index or UUID. Example (Match timer by name): `Timer Name`. Example (Match timer by index): `3`. Example (Match timer by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl -X DELETE 'http://localhost:50001/v1/timer/{id}'
```

<a id="timeroperation"></a>
### GET `/v1/timer/{id}/{operation}`

Performs the requested operation on the specified timer (start, stop, reset).

Operation ID: `timerOperation`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the timer, either name, index or UUID. Example (Match timer by name): `Timer Name`. Example (Match timer by index): `3`. Example (Match timer by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. |
| `operation` | path | enum: `start`, `stop`, `reset` | yes | The operation to perform on the timer |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/timer/{id}/{operation}'
```

<a id="timersetandoperation"></a>
### PUT `/v1/timer/{id}/{operation}`

Sets the details of the specified timer and performs the requested operation (start, stop, reset).

Operation ID: `timerSetAndOperation`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the timer, either name, index or UUID. Example (Match timer by name): `Timer Name`. Example (Match timer by index): `3`. Example (Match timer by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. |
| `operation` | path | enum: `start`, `stop`, `reset` | yes | The operation to perform on the timer |

**Request body** (`application/json`, required): oneOf of object | object | object

- *oneOf option 1*: object
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
  - `countdown` (object)
    - `duration` (integer, required) — Time in seconds.
- *oneOf option 2*: object
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
  - `count_down_to_time` (object)
    - `time_of_day` (integer, required) — Time in seconds.
    - `period` (enum: `am`, `pm`, `24_hour`, required)
- *oneOf option 3*: object
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
  - `elapsed` (object)
    - `start_time` (integer, required) — Time in seconds.
    - `end_time` (integer) — Time in seconds. Omit to specify unlimited end time.

Example: Countdown configuration

```json
{
  "id": {
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "name": "Timer 1",
    "index": 0
  },
  "allows_overrun": true,
  "countdown": {
    "duration": 300
  }
}
```

Example: Countdown to time configuration

```json
{
  "id": {
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "name": "Timer 1",
    "index": 0
  },
  "allows_overrun": true,
  "count_down_to_time": {
    "time_of_day": 3600,
    "period": "pm"
  }
}
```

Example: Elapsed configuration

```json
{
  "id": {
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "name": "Timer 1",
    "index": 0
  },
  "allows_overrun": true,
  "elapsed": {
    "start_time": 0,
    "end_time": 120
  }
}
```

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): oneOf of object | object | object

- *oneOf option 1*: object
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
  - `countdown` (object)
    - `duration` (integer, required) — Time in seconds.
- *oneOf option 2*: object
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
  - `count_down_to_time` (object)
    - `time_of_day` (integer, required) — Time in seconds.
    - `period` (enum: `am`, `pm`, `24_hour`, required)
- *oneOf option 3*: object
  - `id` (allOf of object, required)
    - *allOf option 1*: object
      - `uuid` (string, required) — The UUID of the item
      - `name` (string, required) — The user-defined name of the item
      - `index` (integer, required) — The index of the item
  - `allows_overrun` (boolean, required) — Whether the timer is allowed to overrun.
  - `elapsed` (object)
    - `start_time` (integer, required) — Time in seconds.
    - `end_time` (integer) — Time in seconds. Omit to specify unlimited end time.

Example: Countdown configuration

```json
{
  "id": {
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "name": "Timer 1",
    "index": 0
  },
  "allows_overrun": true,
  "countdown": {
    "duration": 300
  }
}
```

Example: Countdown to time configuration

```json
{
  "id": {
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "name": "Timer 1",
    "index": 0
  },
  "allows_overrun": true,
  "count_down_to_time": {
    "time_of_day": 3600,
    "period": "pm"
  }
}
```

Example: Elapsed configuration

```json
{
  "id": {
    "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
    "name": "Timer 1",
    "index": 0
  },
  "allows_overrun": true,
  "elapsed": {
    "start_time": 0,
    "end_time": 120
  }
}
```

```bash
curl -X PUT 'http://localhost:50001/v1/timer/{id}/{operation}' -H 'Content-Type: application/json' -d '{"id": {"uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D", "name": "Timer 1", "index": 0}, "allows_overrun": true, "countdown": {"duration": 300}}'
```

<a id="timerincrement"></a>
### GET `/v1/timer/{id}/increment/{time}`

Modifies the time on the specified running timer.

Operation ID: `timerIncrement`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the timer, either name, index or UUID. Example (Match timer by name): `Timer Name`. Example (Match timer by index): `3`. Example (Match timer by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. |
| `time` | path | integer | yes | The number of seconds to add to this currently running timer. A negative number will subtract time from this timer. Default `15`. Example `15`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/timer/{id}/increment/{time}'
```

<a id="timergetsystemtime"></a>
### GET `/v1/timer/system_time`

Requests the current system time.

Operation ID: `timerGetSystemTime`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): integer. The current system time defined as seconds since midnight, 1st January 1970.

Example: built from the schema's field examples

```json
1628029541
```

```bash
curl 'http://localhost:50001/v1/timer/system_time'
```

<a id="timergetvideocountdown"></a>
### GET `/v1/timer/video_countdown`

Requests the current value of the video countdown timer.

Operation ID: `timerGetVideoCountdown`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): string

Example: built from the schema's field examples

```json
"00:00:01"
```

```bash
curl 'http://localhost:50001/v1/timer/video_countdown'
```

## Transport

<a id="transportplay"></a>
### GET `/v1/transport/{layer}/play`

Plays the content on the specified layer (presentation, announcement, audio).

Operation ID: `transportPlay`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `layer` | path | enum: `presentation`, `announcement`, `audio` | yes | The layer to perform the transport operation on |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/transport/{layer}/play'
```

<a id="transportpause"></a>
### GET `/v1/transport/{layer}/pause`

Pauses the content on the specified layer (presentation, announcement, audio).

Operation ID: `transportPause`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `layer` | path | enum: `presentation`, `announcement`, `audio` | yes | The layer to perform the transport operation on |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/transport/{layer}/pause'
```

<a id="transportskipbackward"></a>
### GET `/v1/transport/{layer}/skip_backward/{time}`

Moves backward in the content on the specified layer by the specified number of seconds (presentation, announcement, audio).

Operation ID: `transportSkipBackward`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `layer` | path | enum: `presentation`, `announcement`, `audio` | yes | The layer to perform the transport operation on |
| `time` | path | integer | yes | The number of seconds to skip Example `15`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/transport/{layer}/skip_backward/{time}'
```

<a id="transportskipforward"></a>
### GET `/v1/transport/{layer}/skip_forward/{time}`

Moves forward in the content on the specified layer by the specified number of seconds (presentation, announcement, audio).

Operation ID: `transportSkipForward`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `layer` | path | enum: `presentation`, `announcement`, `audio` | yes | The layer to perform the transport operation on |
| `time` | path | integer | yes | The number of seconds to skip Example `15`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/transport/{layer}/skip_forward/{time}'
```

<a id="transportgotoend"></a>
### GET `/v1/transport/{layer}/go_to_end`

Moves to the end on a certain layer

Moves to the end in the content on the specified layer or moves the specified number of seconds away from the end in the content on the specified layer (presentation, announcement, audio).

Operation ID: `transportGoToEnd`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `layer` | path | enum: `presentation`, `announcement`, `audio` | yes | The layer to perform the transport operation on |
| `time` | query | integer | no | The number of seconds back from the end to move to, 0 if not specified Example `15`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/transport/{layer}/go_to_end'
```

<a id="transporttimeget"></a>
### GET `/v1/transport/{layer}/time`

Requests the current transport time for the specified layer (presentation, announcement, audio).

Operation ID: `transportTimeGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `layer` | path | enum: `presentation`, `announcement`, `audio` | yes | The layer to perform the transport operation on |
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): number. The current transport time in seconds.

Example: built from the schema's field examples

```json
15.2
```

```bash
curl 'http://localhost:50001/v1/transport/{layer}/time'
```

<a id="transporttimeset"></a>
### PUT `/v1/transport/{layer}/time`

Moves to the specified time for the specified layer (presentation, announcement, audio).

Operation ID: `transportTimeSet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `layer` | path | enum: `presentation`, `announcement`, `audio` | yes | The layer to perform the transport operation on |

**Request body** (`application/json`, required): number. The time in seconds to move to.

Example: built from the schema's field examples

```json
15.2
```

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `400`: The request was not valid.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl -X PUT 'http://localhost:50001/v1/transport/{layer}/time' -H 'Content-Type: application/json' -d '15.2'
```

<a id="transportautoadvanceget"></a>
### GET `/v1/transport/{layer}/auto_advance`

Requests the auto-advance status for the specified layer (presentation, announcement).

Operation ID: `transportAutoAdvanceGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `layer` | path | enum: `presentation`, `announcement` | yes | The layer to perform the transport operation on |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): boolean. `true` if auto-advance is enabled, `false` otherwise.

```bash
curl 'http://localhost:50001/v1/transport/{layer}/auto_advance'
```

<a id="transportautoadvancecancel"></a>
### DELETE `/v1/transport/{layer}/auto_advance`

Cancels the auto-advance for the specified layer (presentation, announcement).

Operation ID: `transportAutoAdvanceCancel`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `layer` | path | enum: `presentation`, `announcement` | yes | The layer to perform the transport operation on |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl -X DELETE 'http://localhost:50001/v1/transport/{layer}/auto_advance'
```

<a id="transportcurrentmediaget"></a>
### GET `/v1/transport/{layer}/current`

Requests the details of the currently playing content for the specified layer (presentation, announcement, audio).

Operation ID: `transportCurrentMediaGet`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `layer` | path | enum: `presentation`, `announcement`, `audio` | yes | The layer to perform the transport operation on |
| `chunked` | query | boolean | no | Turns this request into a streaming chunked response. Each chunk returned by the endpoint is equal to the standard response. Each time the data updates, a chunk will be sent, until the connection is closed. Most browsers can only handle 5-6 open connections per page. If more connections than this are required, use the [/v1/status/updates](#/Status/statusUpdatesGet) endpoint, which functions as an aggregator allowing the data from multiple endpoints to be consolidated into one. |
| `sse` | query | any | no | Changes the output format of a chunked request to meet the server-side-event [specification](https://html.spec.whatwg.org/multipage/server-sent-events.html). The value of this parameter is irrelevant; only the presence of the parameter matters. |

**Responses**

- `200`: The request was processed successfully. Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): object

- `is_playing` (boolean, required) — `true` if media is currently playing, `false` otherwise.
- `uuid` (string, required) — The UUID of the media item
- `name` (string, required) — The media name.
- `artist` (string, required) — The media artist.
- `audio_only` (boolean, required) — `true` if the item is audio-only, `false` otherwise
- `duration` (number) — The length of the media in seconds

Example: built from the schema's field examples

```json
{
  "uuid": "942C3FC3-C4B2-44F7-A55D-4CC913BB8A5D",
  "name": "Amazing Grace",
  "artist": "John Newton",
  "duration": 112.5
}
```

```bash
curl 'http://localhost:50001/v1/transport/{layer}/current'
```

## Trigger

<a id="triggermedianext"></a>
### GET `/v1/trigger/media/next`

Triggers the next item in the currently active media playlist.

Operation ID: `triggerMediaNext`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/trigger/media/next'
```

<a id="triggermediaprevious"></a>
### GET `/v1/trigger/media/previous`

Triggers the previous item in the currently active media playlist.

Operation ID: `triggerMediaPrevious`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/trigger/media/previous'
```

<a id="triggeraudionext"></a>
### GET `/v1/trigger/audio/next`

Triggers the next item in the currently active audio playlist.

Operation ID: `triggerAudioNext`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/trigger/audio/next'
```

<a id="triggeraudioprevious"></a>
### GET `/v1/trigger/audio/previous`

Triggers the previous item in the currently active audio playlist.

Operation ID: `triggerAudioPrevious`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/trigger/audio/previous'
```

<a id="triggernext"></a>
### GET `/v1/trigger/next`

Triggers the next cue or item in the currently active playlist or library.

Operation ID: `triggerNext`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/trigger/next'
```

<a id="triggerprevious"></a>
### GET `/v1/trigger/previous`

Triggers the previous cue or item in the currently active playlist or library.

Operation ID: `triggerPrevious`

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/trigger/previous'
```

## Video Input

<a id="videoinputsget"></a>
### GET `/v1/video_inputs`

Requests the contents of the video inputs playlist.

Triggerable video inputs come from the Video Inputs playlist in the Media Bin. Once the video input is
added there it can be triggered with [GET /v1/video_inputs/{id}/trigger](#videoinputstrigger)

Operation ID: `videoInputsGet`

**Responses**

- `200`:  Content: `application/json`.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

`200` body (`application/json`): array of object

- `uuid` (string, required) — The UUID of the item
- `name` (string, required) — The user-defined name of the item
- `index` (integer, required) — The index of the item

Example

```json
[
  {
    "id": {
      "uuid": "3C39C433-5C18-4F51-B357-55BB870227C4",
      "name": "Input 1",
      "index": 0
    }
  },
  {
    "id": {
      "uuid": "3C39C433-5C18-4F51-B357-55BB870227C5",
      "name": "Input 2",
      "index": 1
    }
  }
]
```

```bash
curl 'http://localhost:50001/v1/video_inputs'
```

<a id="videoinputstrigger"></a>
### GET `/v1/video_inputs/{id}/trigger`

Triggers a video input from the video inputs playlist.

Operation ID: `videoInputsTrigger`

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The ID of the required item, either name, index or UUID. Example (Match item by name): `Item Name`. Example (Match item by index): `3`. Example (Match item by UUID): `3C39C433-5C18-4F51-B357-55BB870227C4`. |

**Responses**

- `204`: The request was processed successfully. There is no response body.
- `404`: The receiving machine does not have Network enabled or the requested path was not found.

```bash
curl 'http://localhost:50001/v1/video_inputs/{id}/trigger'
```
