---
title: "Relay is marketed as one app for the whole production: Hear, Know, Tell, Act, volunteer friendly, and useful beyond the booth"
author: Justin
date: 2026-11-15
branch: site-hear-know-tell-act
id: l-e7577e3b
scope: repo
status: active
src: agent
touches:
  - site/public/index.html
  - README.md
  - docs/guide/src/00-intro.md
---

## Decided
- Justin (2026-11-14) moved the marketing from "live captions and translation" to one app that runs the whole production. The headline is "Relay hears the room, knows what's live, and tells everyone who needs to know", with "Hear. Know. Tell. Act." as the short form and the four pillars.
- Three themes Justin asked for by name: volunteer friendly ("built for the volunteer on their second Sunday"), one app to control everything ("one app in the booth instead of nine tabs", modules switched on as needed), and help for other areas of a church or venue (signage, parent paging, intercom for the safety team, ushers, multi-campus).
- Marketing may name a feature before it is built, but must label it: "Soon" on the site, "planned" in the README. Justin approved the mockup that did this, rather than leaving unbuilt features out.
- The copy says "safety team", not "security", and makes no emergency-use promise: an intercom on phones and Wi-Fi is not a life-safety system. This wording was the assistant's suggestion; Justin approved the mockup containing it without commenting on it.

## Tried and ruled out
- "The brain of your production" as the headline: it promises monitoring and control that the product does not have. It suits a product with tally, stream status and a control module.
- "Communications hub" alone: too small for the roadmap (tally, NDI, Dante, soundboard and lighting control).

## Watch out
- The site's pillar cards, module switchboard and "Not just for the booth" rows, and the README opening, are hand-written lists of what ships. Shipping or dropping a feature means editing them by hand.
- GitHub accepted auto-merge on the pull request for this change (2026-11-14), although an earlier learning records the repository refusing it.
- Justin asked for a mockup before the live page changed. The mockup was a copy of the page outside the deployed folder, moved over the live page once approved.

In the code:
- site/public/index.html (sections #what, #one, #how, #building), README.md opening
- Deploy from site/: cf deploy public
- docs/guide/src/00-intro.md carries the older captions-only pitch
