# Alerts: call someone's attention {#alerts}

Sometimes a musician needs the sound engineer, or the person on stage needs
the video desk, and cannot wave. **Alerts** let them tap a button on their
phone. The screens you choose then **flash red and white** and show who is
asking, until someone there taps the screen.

This is optional. Nothing flashes until you set up at least one button.

## How it works

- You make **buttons** in the operator panel, for example **Front of house**.
  Each button says which panels and browser screens flash.
- A musician opens the **sender page** on their phone and taps a button.
- The chosen screens flash once a second and show the sender's name.
- **The first tap on any of those screens stops the flashing on all of them.**
  The sender sees that it was seen, and where.
- If nobody taps, the flashing stops by itself after a time you choose, and
  the sender sees **Not seen**.

::: note
The flash is once a second. That is well below the rate at which flashing
lights can be a risk to people who are sensitive to them.
:::

## Step 1: Make the buttons

1. In the operator panel, open **Alerts**.
2. Click **Add button**. Give it a name and choose an icon. Under **Panels it
   flashes**, tick the panels. Under **Browser screens it flashes**, type the
   screen names, separated by commas.
3. Choose **Stop flashing after** (30 seconds to 10 minutes).
4. Click **Save alerts**.

![The Alerts page with three buttons.](images/admin-alerts.png){width=100%}

1. **A button.** Its name, icon, and the panels and screens it flashes. You
   can have up to 16 buttons. **Remove** deletes it.
2. **Add button.**
3. **Stop flashing after.** How long it flashes if nobody taps.
4. **Save alerts.**
5. **The links.** The sender page for phones, and the address for each
   browser screen. Give the sender link to the band, not to the room.

## Step 2: Choose what flashes

**A caption panel.** A panel flashes whatever it is showing. Tick it under
**Panels it flashes** on a button. A panel can also *send* alerts:
set one of its feeds to **Alert buttons** in the panel's settings (see
[Setting up a caption panel](#panels)), and it shows your buttons as large
touch targets.

**A browser screen.** A TV or computer on the network flashes when you open it
as `http://192.168.1.50/screen?alert=Stage TV`, with a name that one of your
buttons lists. A screen with no touch needs a mouse click or a key press to
stop the flashing.

![A screen flashing for an alert.](images/alert-screen.png){width=70%}

## Step 3: Send an alert

1. On a phone on the same network, open the sender link from the **Alerts**
   page. It ends in `/alert`.
2. The first time, type who you are, for example `Drums`. The phone remembers
   it. The screens show this name.
3. Tap a button. It flashes the screens for that button.
4. Tap the same button again to cancel.

![The sender page on a phone.](images/alert-sender.png){width=45%}

1. **Who is sending.** Tap **change** to use another name.
2. **A button.** Tap to send, tap again to stop. It shows when it was seen.
3. **Connection.** Green when the phone can reach Relay.

::: warning
Anyone who has the sender link can send alerts, with no sign-in. Hand it to
the band, not to the room.
:::

### Requiring a sign-in

To control who can send, tick **Only signed-in people can send** under
**When nobody taps** and click **Save alerts**. Then:

- The sender link becomes the operator panel's `https://` address. Old
  `http://` links forward there.
- Each musician signs in with their own account. Give them the **Member**
  role (see Users): they can send alerts but cannot open the operator panel.
  After signing in they land on a page that lists **Alerts**.
- The screen they flash shows their account name, so there is no
  **Who are you?** box.
- Panels with an Alert buttons feed keep sending as before.

## Stage messages

Not everything needs a red flash. Under **Stage messages** you can write
the short words you send often ("Wrap up", "5 minutes", "Mic 2 is off") and
choose which panels and browser screens each one appears on. They show over
whatever the panel or screen is already showing, with nothing to tap. How loud
one is depends on its level (see Levels, below): a message is a steady blue
unless you say otherwise.

- A **message** is a card in the middle of the screen and stays for the time
  you set (10 seconds by default).
- Tick **Banner** and it becomes a strip across the top that stays up longer
  (5 minutes by default), for something like "Mic 2 is off".
- Pick panels and screens under **Typed messages go to** and the sender page
  also shows a box to type a message, with a **Banner** tick.
- The sender page lists what is showing, with **Clear** beside each. A message
  also ends on its own when its time is up.

Anyone who may send alerts may send messages; with **Only signed-in people can
send** ticked, messages need the same sign-in.

## Levels

Every alert button and every message has a level. It sets the colour and
whether the screen flashes:

| Level | Looks like | Flashes |
|---|---|---|
| **Info** | Solid blue | No |
| **Attention** | Amber and dark | Once a second |
| **Urgent** | Red and white | Once a second |

An alert button is **Urgent** and a message is **Info** until you change it:
pick the level beside the button or message under **Alerts**, and for typed
messages under **Typed messages go to**, then **Save alerts**. Buttons you set
up before levels existed stay Urgent, so nothing changes for them.

On the sender page, **Level** above the buttons lets the sender pick another
level for the next alert or message they send. Leave it on **Each button's
own** to use the one you set. It is not remembered between visits.

Whatever the level, the first tap on a flashing screen still stops the alert
everywhere, and **When nobody taps** still applies. The flash is fixed at
once a second, under the three a second where flashing becomes a seizure
risk.

## Replies

Under **Replies** on the Alerts page you can list up to six short answers,
such as "On my way" and "Can't right now" (24 characters each, one per
line). A browser screen that is flashing then shows them as big buttons, and
tapping one acknowledges the alert and sends that answer to the sender, who
sees it next to "Seen". Tapping anywhere else still acknowledges with no
reply, and with the list empty nothing changes. Panels are sent the list too;
showing the buttons on a panel needs updated panel firmware.

## History

Under **History** on the Alerts page (Admins and Owners) you can see who sent
each alert or stage message, when, to which panels and screens, how long it
took until it was seen, and the reply. Relay keeps 30 days and then deletes
older rows.

## Telling other products

Switch on the **Alert events** module (Modules page) to have Relay send a
webhook (a JSON POST) or OSC messages when an alert or message is sent, an
alert is acknowledged (with the reply) or one ends unseen or stopped. It also
offers the alert state to Bitfocus Companion for variables and feedbacks.
