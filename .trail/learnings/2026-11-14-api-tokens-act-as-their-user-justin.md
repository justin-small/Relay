---
title: "API tokens act as their user minus account administration, only Admins and Owners make them, and only Owners revoke other people's"
author: Justin
date: 2026-11-14
branch: user-api-tokens-174
cost: { minutes: 7, tokens: 182557 }
id: l-58caa0ef
scope: repo
status: active
src: agent
touches:
  - app/users.py
  - app/main.py
  - app/static/users.js
  - tests/test_auth.py
---

## Decided
Justin chose (2026-11-14, issue 174):
- Only Admin and Owner hold tokens.own (make and list their own tokens). Owners alone hold tokens.manage (see and revoke everyone's).
- A token acts as its user's current role minus TOKEN_BARRED: users.view, users.manage, credentials.manage, tokens.own, tokens.manage. The own password, PIN and token routes (permission None) refuse tokens with require(..., tokens=False). A leaked token can use Relay but cannot take over an account or mint more tokens.
- Expiry is optional: 30, 90 or 365 days, or never; 90 preselected.
- Anyone may revoke their own tokens whatever their role.
- Disabling a user stops tokens at once (checked per request); deleting deletes them (cascade); moving a user below Admin deletes their tokens; a password reset makes them wait (password_change_required) until the user picks a new one.
- A bad Bearer header is a 401 and never falls back to the cookie. Tokens work only on /api/admin routes on the admin port; pages still need a cookie.

## Tried and ruled out
- Any role making tokens, and a "pick permissions per token" scope: offered, not chosen.
- Admins revoking others' tokens by rank: Justin chose Owners only.

## Watch out
- The token UI (My account, Users) was not seen in a browser: the browser pane refuses the panel's self-signed certificate. The API was run live with curl.
- Test labels must not include anything derived from a token (CodeQL clear-text logging).

In the code: app/users.py (TokenUser, TOKEN_BARRED, create_token, token_user, revoke_token), app/main.py require() and /api/admin/me/tokens, /api/admin/tokens, app/migrations/0002_api_tokens.sql, app/static/users.js, tests/test_auth.py "API tokens".
