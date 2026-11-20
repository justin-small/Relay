---
title: "Relay is marketed as the piece that connects every production system on one pane of glass; captions are one part of it, not the headline"
author: Justin
date: 2026-11-17
branch: site-connecting-piece
cost: { minutes: 1, tokens: 2229 }
id: l-b4f589ff
scope: repo
status: active
src: agent
touches:
  - site/public/index.html
---

## Decided
- Justin, 2026-11-16: Relay's focus has moved away from being solely live translation and transcription. Its vision is to be the connecting piece for all production systems, so the operator has a single pane of glass. He asked for the website to say so and to list the features that have shipped.
- The home page leads with a hub diagram because it shows the connecting role directly: systems on the left, the Relay mark in the middle, people and screens on the right, under "Relay connects everything in your production." Justin chose it over two other heroes.
- Justin asked to keep the caption animations. They stay, under "It still starts by hearing the room": the scrolling feeds, the language marquee, the six-language welcome and the flashing alert.
- The rules from the earlier marketing learning still hold: unbuilt features are named but marked Soon, "soundboard" not "sound desk", "safety team" with no emergency promise.

## Tried and ruled out
- A status-screen hero ("Every system in your production, on one screen"): the assistant recommended it; Justin picked the hub. The status screen is a section lower down instead.
- Keeping the captions hero and headline with new copy: the smallest change, not chosen, because captions are no longer the headline.

## Watch out
- What is marked as working was read from the folders in app/modules by the assistant; Justin has not confirmed each one is ready to advertise. Marked Soon: soundboard control, mic batteries, spoken translation, speaker names, Facebook Live, more than one campus.
- The numbers and names inside the status screen (214 watching, 18 ms, Sam on Production) are invented for the picture.
- The README opening is a separate hand-written list of what ships and what is planned; it does not follow the site.
- Deploying from a checkout publishes every file in site/public as that branch has it, so a deploy can put out a changed privacy.html along with the page you meant to ship.
- The page was built from mockups by a throwaway script, so edit the HTML directly.

In the code:
- site/public/index.html: sections #top (hub: .hub, .hc, .hw), #glass (.gl, .gg), #connects (grouped switchboard), #what, #hear, #how, #building
- Deploy from site/: cf deploy public
