# Showing the service from Planning Center {#planning-center}

If your church plans its services in **Planning Center Services**, Relay can
show the service that is running on the caption panels: what is happening now,
what comes next, how long is left, and the whole order of service. You can also
let a panel move the service on, with **Next**, **Previous** and **Take
control**.

This is optional. Relay works fully without it. Nothing is sent to Planning
Center until you connect it.

## What you need

- **Planning Center Services**, with your services planned in it.
- **A person who can sign in to Planning Center** and has access to Services.
  Relay acts as this person. Choose someone whose access you are happy for the
  panels to use.
- **Your admin token.** See Part 1.
- **At least one caption panel**, set up as in the previous chapter.

::: warning
Everything Relay does in Planning Center, it does **as the person who made the
token**. When someone taps **Next** on a panel, Planning Center sees that
person press Next.
:::

## Step 1: Make a Personal Access Token

A **Personal Access Token** is a pair of codes, an application ID and a secret,
that lets Relay read Planning Center as you. It works like a password.

1. Sign in to Planning Center as the person Relay will act as.
2. Open <https://api.planningcenteronline.com/personal_access_tokens>.
3. Click **New Personal Access Token**, give it a name such as `Relay`, and
   create it.
4. Copy the **Application ID** and the **Secret**. You will paste them into
   Relay in the next step.

::: warning
Keep the secret private, like a password. If it is ever shared by mistake,
delete the token in Planning Center and make a new one.
:::

## Step 2: Connect Relay

1. Open the operator panel and sign in.
2. Open **Modules** and switch **Planning Center** on (see
   [Modules](#modules)). If Relay was already connected before the update
   that added modules, it is on already.
3. Open the **Planning Center** section.
4. Paste the **Application ID** and the **Secret**.
5. Click **Test connection**. The section should say it is connected, and list
   your **service types** (for example "Sunday AM").
6. Tick the service types Relay should follow.

Relay never shows the ID or the secret again. The section only says a token is
saved. To use a different token, paste the new one. To stop, click
**Disconnect**.

## Step 3: Check today's service

Under **Today**, Relay shows the plan it follows for each service type: the one
with a service time today. Once the service starts, it shows who is controlling
LIVE, the item that is running and how long is left on it.

- If Relay picked the wrong plan, choose another in the list. Your choice lasts
  until midnight. Choose **Relay's pick for today** to go back.
- "No plan with a service time today." means what it says: there is nothing to
  show today.
- A message that Planning Center **did not accept** the ID and secret means the
  token was deleted or mistyped. Make a new one (Step 1).
- A message that Planning Center **is rate limiting** clears by itself in a
  few seconds.

## Step 4: Choose what each panel does

Each panel has its own setting. In **Panels**, click the panel's name. Under
**Planning Center**, choose its **Service mode**:

- **Off** (the default): the panel does not offer the service at all.
- **Show only**: the panel shows the service. Nobody can change it from the
  panel.
- **Control only**: large **Previous** and **Next** buttons and **Take
  control**, with the current and next item. No order of service.
- **Show and control**: both.

If Relay follows more than one service type, also choose the **Service type**
the panel shows, or let the panel pick. Click **Save**. The panel changes
within a second. You can also pick **Service** as one of the panel's feeds
under **Display**.

Relay re-reads the plan every 30 seconds near a service, so a title, length or
order you change in Planning Center reaches the panels within about half a
minute. Away from a service it checks every five minutes.

::: warning
Only give control to panels in places you trust. Anyone who can reach your
network could in principle copy what a controlling panel sends. Relay limits
this, but a panel is not as safe as a signed-in operator.
:::

## Step 5: Show the service on a panel

At the panel:

1. Tap the menu button at the top right.
2. Tap **Service**. (With several service types it reads "Service: Sunday AM".)
3. Tap **Done**.

The panel shows:

- **At the top:** the item that is running, in large letters, with the time
  left on it. The time turns red and shows a **+** when the item runs over.
  Below it, the next item.
- **Below:** the whole order of service. Drag it to read ahead. It goes back to
  the current item after a minute, or when you tap **Jump to now**.
- **Songs:** the key and the order of the song (Verse 1, Chorus 1 and so on).
- **At the bottom:** whether the service is ahead or behind, and who is
  controlling LIVE.

Song titles keep their accents. The smaller text drops them: "José" reads
"Jose".

## Controlling the service from a panel

On a panel set to **Control only** or **Show and control**:

1. Tap **Take control**. If someone else is controlling LIVE, the panel asks
   "Take control from *their name*?" first. Tap **Take control** again to
   confirm.
2. Tap **Next** or **Previous**. The item on the screen changes when Planning
   Center has moved on, usually within a second.
3. Tap **Release** when you are done, so someone else can control.

The buttons act the moment you touch them, and **Next** and **Previous** move
the highlight on the panel at once. If you tap again while the first tap is
still on its way, the second tap waits and is sent right after, so two quick
taps move two items. The bottom of the screen shows the result of your last tap
and whether Relay has control. If a tap does not work, the panel says why:

- **Take control first**: Relay is not controlling LIVE. Tap **Take control**.
- **Not allowed**: the panel is not set to control, or its setting changed.
  Check its **Service mode**.
- **Planning Center unreachable** or **Relay not reachable**: check the
  network, then try again.

Every press is listed in the operator panel under **Presses from panels**, with
the panel's name and the time.

## Moving the service from ProPresenter

If your church also runs ProPresenter, Relay can move Planning Center's LIVE item
when a song comes up there, once it has stayed up for a few seconds. It is **off**
unless you switch it on, and it never takes control: it moves LIVE only while
Relay's token already has control, which you give it with **Take control** on a
panel. See [Linking to Planning Center](#propresenter-link) in the ProPresenter
chapter.

## Item notes

Planning Center items can carry **notes**. Notes sometimes hold private things,
such as names or prayer requests, so panels do not show them unless you switch
**Show item notes on panels** on in the **Planning Center** section. When it is
on, an item with notes shows a small page mark. Tap the item to read them.

::: warning
Anyone on your network can open what panels show, without signing in. Leave
item notes off unless you are sure nothing private is in them.
:::

## When it says "Not live"

"Not live" means nobody is running the plan in **Services LIVE** in Planning
Center. Open LIVE for today's plan in Planning Center (or tap **Take control** on
a controlling panel). The panel shows the order of service either way.

"Relay does not follow this service" means the service type was unticked in
the operator panel. Tick it again in **Planning Center**, or choose another
feed on the panel.
