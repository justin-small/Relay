# Letting people watch from anywhere {#remote-access}

Normally, only phones on your venue's Wi-Fi can open the captions. **Remote
access** gives the viewer pages an internet address too, so people at home, in
an overflow room or on mobile data can follow along.

This is optional. Relay works fully without it.

## What you need

- **An enrollment code.** It looks like `ABCD-EFGH-JKMN`, and you get it from
  whoever set up Relay for you. Each code works once.
- **An operator account** with the Admin role.
- **An internet connection** on the computer running Relay. You don't need to
  change anything on your router or firewall.

## Step 1: Switch it on

1. In the admin panel, open **Modules** and switch on **Remote access**.
2. **Remote access** now appears in the menu, under **Output**. Open it.

## Step 2: Enroll

1. Type the enrollment code into **Enrollment code**. Capitals and dashes don't
   matter.
2. Press **Enroll**.

After a few seconds the page shows your link, for example
`https://k7m2p9qa.relaychurchsystems.com`, with a QR code. When it says
**Connected**, the link works from anywhere.

Put the link or the QR code on a slide, in your livestream description or in
your church app, just as you would a livestream link.

::: note
The link opens the same pages as the room's link: captions, translations and
the presentation screens. The admin panel is **never** reachable through it.
Your audio and your OpenAI key never leave this computer.
:::

## Switching it off, and releasing the address

- **Switching Remote access off** on the Modules page stops the link. Your
  address is kept: switch it on again and the same link works.
- **Release this address** gives the address up for good. Old links stop
  working. To get a link again you need a **new** code, and you get a
  **different** link.

## If it doesn't connect

| What the page says | What to do |
|---|---|
| "That enrollment code isn't right" | Check the code and type it again. |
| "That enrollment code has already been used" | Each code works once. Ask for a new one. |
| "Couldn't reach Relay's cloud" | Check that this computer is online. |
| "Lost the connection to Cloudflare. Trying again…" | Relay keeps trying on its own. If it lasts, check the internet connection and any firewall that blocks outgoing connections. |
| "cloudflared is missing from this install" | Reinstall Relay. |

While Remote access is off, or while the internet is down, the room's own link
keeps working as normal.
