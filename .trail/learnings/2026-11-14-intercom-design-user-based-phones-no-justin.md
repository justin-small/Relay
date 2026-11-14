---
title: "Intercom design: user-based phones, no Redis, single tenant, remote crew via a separate Cloudflare module"
author: Justin
date: 2026-11-14
branch: main
cost: { minutes: 1, tokens: 2292 }
id: l-2a320952
scope: repo
status: active
src: agent
---

## Decided
Justin (2026-11-14, epic 237) settled the intercom design before the issues were rewritten:
- No Redis. Live state (presence, PTT leases) lives in Python asyncio memory; durable state goes in Relay's existing SQLite (WAL, foreign keys, aiosqlite, versioned migrations, backups).
- Single tenant, but keep a company_id column so tenancy can be added without a schema rewrite.
- Crew use the web crew page first; the device/credential model must let a native app join later.
- Everything follows the user, not a position: a person signs in on any phone and their channels come with them. Signing in issues a unique device credential that silently rotates every 30 days; LiveKit room tokens are short-lived; admins can revoke a phone.
- Channels are created by admins; users are assigned to one or more with talk/listen rights and pick which to talk/listen on. Routing profiles are per production and reusable; productions can optionally come from Planning Center.
- PTT priority is a per-channel setting with three modes: pre-empt, party line (all talk), one-at-a-time with a priority queue.
- Remote crew connect directly to Cloudflare Realtime SFU through a separate intercom-remote module that needs the intercom module; remote crew use their own account plus an admin "remote allowed" flag; remote sessions are separate from local ones.
- Encrypt secrets at rest and backups; device credentials hashed. Not whole-file SQLCipher.
- Watchdog covers LiveKit, the Python app, the database, the Cloudflare connection, and audio packet loss/jitter.
- Matrix/channel config is stored independently of LiveKit room structure.

## Tried and ruled out
- Redis: another binary to ship and supervise on desktop and Linux for state asyncio already holds.
- Full multi-tenancy: a Relay server is built to serve one organisation; the company_id column keeps the door open.
- Position-based phones and QR/pairing-code provisioning: Justin wants identity to follow the user.
- Remote bridge inside the intercom module: a separate module means turning it off can never touch local audio.
- Wi-Fi access point checks in the watchdog: dropped by Justin.
- Whole-file SQLCipher: needs native builds on three platforms and touches every existing table.

## Watch out
- iOS Safari is weak at background audio and lock-screen PTT; that is why a native app is planned to join later.

In the code: mockups go as HTML in docs/mockups/; the epic is issue 237 with children 238-241, plus new children.
