---
title: "The operator panel redesign keeps FastAPI and Jinja and takes the reference's design method, not its Next.js stack"
author: Justin
date: 2026-11-16
branch: main
cost: { minutes: 24, tokens: 295787 }
id: l-445f8362
scope: repo
status: active
src: agent
touches:
  - docs/mockups/admin-shell/index.html
  - docs/redesign/admin-shell.md
---

## Decided
Justin, 2026-11-16: restyle the operator panel to look like the shadcn admin template (inset collapsible sidebar, header with search and breadcrumb, cards, preferences popover, four presets with Relay's green as the default) by lifting its design method into plain CSS and a small vanilla script. No move to Next.js, React or Tailwind. The approved mockup and the full application spec are the source of truth for the work.
In the code: docs/mockups/admin-shell/index.html (mockup), docs/redesign/admin-shell.md (spec, rollout in 7 pull requests, acceptance checklist).

## Tried and ruled out
Porting to Next.js/shadcn: it would rewrite about 3,800 lines of server-rendered admin templates and scripts, break the module system that builds the nav per user (module_registry.admin_nav), and add a Node build to the Tauri and Raspberry Pi packaging, for no look the CSS does not already give.

## Watch out
The mockup's Integrations parent, Register page, account switcher and fake data are demonstrations only; modules keep their pages under the existing nav groups and core names no module. Preferences must go through the existing per-user store (/api/admin/me/prefs, scope core) with allow-listed values. Nothing was applied to the real app yet.
