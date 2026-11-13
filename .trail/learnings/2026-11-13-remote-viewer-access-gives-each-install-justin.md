---
title: "Remote viewer access gives each install its own Cloudflare tunnel and a random subdomain of relaychurchsystems.com, made by a Worker when the module is switche…"
author: Justin
date: 2026-11-13
branch: status-view-224
cost: { minutes: 1, tokens: 3111 }
id: l-0d9a32fd
scope: repo
status: active
src: agent
touches:
  - app/modules/__init__.py
  - docs/modules.md
  - desktop/src-tauri/tauri.conf.json
---

## Decided
Justin (2026-11-13, issue 50): remote viewer access is a switchable module. Switching it on asks a small provisioning Worker (holds the Cloudflare API token; installs never do) to create a named tunnel, point it at the viewer port only, add a CNAME <random-slug>.relaychurchsystems.com and return the tunnel token. Creating a subdomain needs an enrollment code Justin hands out. Switching the module off stops cloudflared but keeps the subdomain, so the link printed for a church stays the same.

## Tried and ruled out
- Quick tunnels (trycloudflare.com): they buffer text/event-stream, so captions over /stream never arrive; also a 200 in-flight request cap. Dropped.
- Shipping the Cloudflare API token in installs: anyone could unpack it and take over the zone.
- A Worker + Durable Object relay instead of tunnels (box pushes captions out, cloud fans out to viewers): avoids one DNS record per install and spares the venue's uplink, but transcripts would pass through our cloud, against the fleet epic's rule that the cloud never receives transcripts. Revisit only if venue upload or the DNS quota becomes the problem.

## Watch out
- Cost limits: 1,000 tunnels per account free (inactive ones count; $4.50/tunnel/month after). DNS records per zone on Free: 200 for zones made after 2024-09-01, 1,000 before; Pro gives 3,500. Kept subdomains hold both forever unless cleaned up.
- Use one level of subdomain: Universal SSL covers *.relaychurchsystems.com, not deeper names.
- Modules download nothing at run time, so cloudflared is bundled with the desktop app.

In the code: the module goes in app/modules/remote_access/ (registry app/modules/__init__.py, rules docs/modules.md); cloudflared ships as a Tauri resource via desktop/src-tauri/tauri.conf.json; the tunnel targets the viewer port Relay serves itself (no Caddy).
