---
title: "CodeQL fails the pull request if a password can reach any print, even in tests and tools"
author: Justin
date: 2026-11-12
branch: users-rbac
cost: { minutes: 1, tokens: 27919 }
id: l-8c8b8638
scope: repo
status: active
src: agent
touches:
  - tools/reset_owner.py
  - tests/test_auth.py
  - tests/test_users.py
---

## Decided
- The lockout tool asks for the new Owner password twice without echo; it never generates or prints one.
- Test files that compare passwords, PINs or tokens do not print the compared values when a check fails.

## Tried and ruled out
- Generating a temporary password and printing it once: CodeQL's clear-text logging rule failed the required check.

## Watch out
- CodeQL is a required check here, and it treats any value named like a password or token as sensitive, including inside a shared test helper that prints got and want.

In the code:
- tools/reset_owner.py (getpass), eq() in tests/test_auth.py and tests/test_users.py.
