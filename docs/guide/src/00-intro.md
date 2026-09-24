# About this guide {#intro}

Relay puts live captions on the phones in your room. Someone speaks into a
microphone. Relay sends the sound to OpenAI. The words come back as text, in
English and in the languages you choose, a second or two later. People in the
room open a web page on their phone and read along. They do not install
anything and they do not need an account.

This guide is for the person who sets Relay up and runs it. You do not need to
be a developer. If you can install an app and type into a window, you can
follow it.

## What you need

- **A computer to run Relay on.** A Mac, a Windows 10 or 11 PC, or a Linux PC. It
  stays at the venue during the event.
- **A sound source.** A feed from the sound desk is best. A USB microphone or
  the computer's own microphone also works.
- **A network.** The Relay computer and the phones in the room must be on the
  same network, usually the venue's Wi-Fi. The Relay computer also needs an
  internet connection, to reach OpenAI.
- **An OpenAI account with an API key.** An API key is a secret code that lets
  Relay use OpenAI and bills your account. Part 2 shows how to get one.
- **About an hour** the first time. After that, starting Relay takes a minute.

## How the pieces fit

Relay has two web addresses. Both are on the Relay computer.

| Address | Who uses it | What it is |
|---|---|---|
| `http://192.168.1.50/` | Everyone in the room | The **viewer pages**: captions to read. |
| `https://192.168.1.50/admin` | You, the operator | The **operator panel**: where you control Relay. |

`192.168.1.50` is an example. Your computer has its own address on the
network, called its **LAN IP address**. Relay prints the real addresses when
it starts.

The viewer pages are open to anyone on the network. The operator panel needs a
password, called the **admin token**, which you choose during setup.

::: note
The operator panel address starts with `https://`. The `s` means the
connection is encrypted, so nobody else on the Wi-Fi can read your admin token.
The first time you open it, your browser shows a warning. Chapter
[Opening the operator panel](#operator-access) explains why, and how to check
that it is safe.
:::

## How to use this guide

The guide has four parts. Read them in order the first time.

1. **Part 1: Installing Relay.** Download Relay, install Docker, run setup,
   and start Relay. There is one chapter for each of macOS, Windows and Linux.
   Read only the one for your computer.
2. **Part 2: Your OpenAI API key.** Create an account, add credit, set a
   spending limit and make the key that setup asks for. You can do this
   before Part 1, so the key is ready when setup asks.
3. **Part 3: Using Relay.** Every screen, with a picture and an explanation
   of each control: the viewer pages, the operator panel, and rehearsal mode.
4. **Part 4: Bitfocus Companion and Stream Deck.** Optional. Control Relay
   from buttons on a Stream Deck.

At the back you will find a one-page **quick reference card**, and a
**glossary** that explains every technical word used in the guide.

::: tip
New to Relay? The fastest safe path is: get your API key (Part 2), install
(Part 1), then try **rehearsal mode** (Part 3). Rehearsal mode shows made-up
captions without connecting to OpenAI, so you can check every screen for free.
:::

## Signs used in this guide

::: note
A **Note** gives useful background.
:::

::: tip
A **Tip** saves you time.
:::

::: warning
A **Warning** tells you how to avoid losing money, data or security.
:::

Buttons and labels on screen are in **bold**, exactly as they appear. Things
you type, and file names, look like `this`.

## The golden rule

::: warning
**Press Stop between sessions.** OpenAI bills Relay by the minute for each
language that is switched on, for as long as capture runs, even when nobody
is speaking. Leaving capture running overnight costs money for nothing.
:::

## About the screenshots

The screenshots were taken from a test copy of Relay in rehearsal mode, with
test data. The addresses in them, such as `192.168.1.50`, are examples. Your
screens show your own addresses. OpenAI and Docker change their screens often,
so their menus may look a little different from the descriptions here.
