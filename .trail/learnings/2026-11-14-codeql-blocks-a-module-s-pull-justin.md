---
title: "CodeQL blocks a module's pull request over refusal text, served file paths and upload previews; use fixed messages, resolved paths and blob-only addresses"
author: Justin
date: 2026-11-14
branch: signage-205
cost: { minutes: 1, tokens: 15437 }
id: l-f66e6909
scope: repo
status: active
src: agent
touches:
  - app/modules/signage/media.py
  - app/modules/signage/routes.py
  - app/modules/signage/static/signage.js
  - app/modules/signage/store.py
---

## Decided
- A refusal sent to the browser comes from a `.message` attribute holding a fixed sentence (as users.Refused does), never `str(exc)`: CodeQL flags any exception text reaching a response as information exposure.
- A file served by name is found by resolving the joined path (realpath) and checking it is directly inside its folder. A regex check on the name alone does not satisfy CodeQL's path-injection rule.
- An upload preview made in the browser only uses an address that starts with `blob:`, made there from the chosen file.

## Watch out
- The CodeQL check's alerts are not in the code-scanning alerts API for the PR ref; read them from the check run's annotations instead.
- These fixes were pushed without seeing CodeQL's next run; if it still flags the upload preview, the alert may need dismissing as a false positive (that is Justin's call).

In the code: app/modules/signage/media.py (_inside, Refused.message), store.Invalid.message, routes._refuse, static/signage.js describeFile. Annotations: gh api repos/justin-small/Relay/check-runs/<id>/annotations
