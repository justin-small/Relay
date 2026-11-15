---
title: "Crew chat is a team channel plus role channels named from crew roles, delivered over server-sent events, with quiet push off until chosen"
author: Justin
date: 2026-11-15
branch: main
cost: { minutes: 7, tokens: 60301 }
id: l-4f5b7259
scope: repo
status: active
src: agent
touches:
  - app/modules/crew/__init__.py
  - app/modules/crew/calltimes.py
  - app/modules/crew/migrations/0003_calltimes.sql
  - app/modules/crew/paging.py
  - app/modules/crew/routes.py
  - app/modules/crew/static/crewadmin.js
  - app/modules/crew/templates/admin.html
  - tests/test_auth.py
  - tests/test_crew_calltimes.py
---

## Decided
Justin chose these for issue 230:
- New messages reach open chats over server-sent events; posting is a normal request. The phone reloads the newest 100 whenever the stream connects, so nothing is replayed.
- Channels are `team` and `role:<role>`, the role being the free text an admin set for the person (#227), lowercased with spaces collapsed. Only people holding that role read it; moderators (Operator and above, `crew.moderate`) read every role in use and delete any message. Authors delete their own.
- The host keeps 30 days per channel and shows the newest 100.
- Quiet notifications reuse the web push from pages, are off until a person turns a channel on, and skip anyone with the chat open. The chat tab closes its stream when it or the page is hidden, which is what makes a person "not looking".

## Tried and ruled out
- WebSocket and polling for delivery, admin-defined role channels, a team channel only, 7-day or forever history, and a push for every message: offered, not chosen.

## Watch out
Changing a person's role ends their open streams so they reconnect into the right channel. A stream more than 200 events behind is closed, not buffered. A person is limited to 5 messages in 10 seconds. Authors are stored by name at posting time, and a deleted account leaves its messages unattributed. A service worker version bump (`crew-v3`) carries the quiet push. Not tried on a real phone or in a browser.

In the code:
- `app/modules/crew/chat.py` (`can_read`, `Hub`, `post`, `notify`)
- `app/modules/crew/migrations/0004_chat.sql`
- `app/modules/crew/routes.py` (`/crew/api/chat/*`)
- `app/modules/crew/static/chat.js`
- `tests/test_crew_chat.py`
