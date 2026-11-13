---
title: "The marketing site is one static page deployed to Cloudflare for free, with price and download hidden until Justin supplies them"
author: Justin
date: 2026-11-13
branch: main
cost: { minutes: 1, tokens: 2075 }
id: l-e5372207
scope: repo
status: active
src: agent
---

## Decided
The public site at relaychurchsystems.com is a single static page served by Cloudflare as static assets only, so it stays on the free plan. Justin chose to hide the unfinished parts rather than publish placeholders: the price reads "Pricing on request" with a Get in touch button, and there is no download button until a real link exists. The contact address is hello@relaychurchsystems.com, which reaches Justin through the domain's catch-all Email Routing rule. The look uses the Relay brand (black, the green mark), not the VXP design system, and the wording is "soundboard", never "sound desk".

## Tried and ruled out
- Publishing with the bracketed placeholders visible: Justin declined.
- A contact form: it would need Email Sending, which is on the Workers Paid plan. The page uses mailto links instead.
- Installing cf as a dev dependency for the config file: not needed, a plain exported object works.

## Watch out
- The page was generated once from a design canvas mockup by a throwaway script. Edit the HTML directly from now on; the mockup and the live page will drift.
- Plain http is not redirected to https, and www is not set up. Both were left for Justin to decide.
- The Spanish, Portuguese, French, Korean and Chinese lines were written by the assistant and not checked by a native reader.
- The page promises help getting set up, while the README says there is no support commitment and no licence is granted.
- Right after the first deploy the local resolver still cached "no such host"; verify with curl --resolve against a Cloudflare address, or wait.
- The site folder was created on the headless-service branch and left uncommitted.

In the code:
- site/public/index.html, site/public/favicon.svg, site/public/favicon.ico
- site/cloudflare.config.ts: export default { worker: { name: "relay-site", compatibilityDate, domains: ["relaychurchsystems.com"] } }
- Deploy from site/: cf deploy public
- Catch-all rule: cf email-routing rules catch-all update -z relaychurchsystems.com
