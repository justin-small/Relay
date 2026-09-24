# Getting and managing your OpenAI API key

::: note
Written in September 2026. OpenAI's dashboard changes often, so the layout you
see may differ. Menu names are given as they appeared then.
:::

This chapter takes you from nothing to a working key in Relay. Set aside about
30 minutes. You need an email address, a mobile phone and a payment card.

## What an API key is

An **API key** is a long secret code, a bit like a password, that lets a
program use an online service. OpenAI's keys start with `sk-`. Relay puts the
key on every request it sends to OpenAI. OpenAI uses the key to decide whose
account pays for the translation.

Anyone who has your key can use OpenAI and bill it to you. Treat it like a
bank card PIN.

## The OpenAI Platform is not ChatGPT

OpenAI sells two different things:

- **ChatGPT** (chatgpt.com) is the chat app. ChatGPT Plus, Pro and Team are
  monthly subscriptions for that app.
- The **OpenAI Platform** (platform.openai.com) is for programs such as Relay.
  You pay for what you use, from a balance of prepaid credit.

The two are billed separately. **A ChatGPT Plus subscription does not pay for
Relay.** Even if you already pay for ChatGPT, you still need to add credit on
the OpenAI Platform. You can use the same login for both, but the money does
not move between them.

## Step 1: Create an OpenAI Platform account

1. Go to <https://platform.openai.com> and click **Sign up**.
   You should see a sign-up page. You can use an email address, or a Google,
   Microsoft or Apple account.
2. If you signed up with an email address, open the email from OpenAI and
   click the link in it.
   The browser should come back to OpenAI and say your email is verified.
3. Enter your name and date of birth when asked. If asked for a phone number,
   enter your mobile number, then type the code OpenAI texts to you.
   You should reach a welcome screen.
4. If OpenAI asks for an **organization** name, type your venue or company
   name. An organization is the account that owns the billing, the projects
   and the keys. You can rename it later.
   You should now see the Platform dashboard, with menus along the top or the
   left side.

::: tip
If the account is for your venue, sign up with a shared work address (for
example `av@yourvenue.org`), not a personal one. Then the account does not
leave with one person. You can invite other people to the organization later
from **Settings**.
:::

## Step 2: Add a payment method and buy credit

New accounts are **prepaid**. You buy credit first, and each session uses some
of it. Relay's translation model does not work on the free tier, so you must
buy some credit before Relay can connect.

1. Click the gear icon (**Settings**), then **Billing**.
   You should see the billing overview with a credit balance of $0.00.
2. Click **Add payment details** (or **Add payment method**) and enter your
   card. Choose **Individual** or **Company** if asked.
   The card should now appear under **Payment methods**.
3. Click **Buy credits** (on some accounts it is called **Add to credit
   balance**). Enter an amount. The minimum is $5. $20 to $50 is a sensible
   start for testing and a first event.
   Your credit balance should go up by that amount. It can take a few minutes.

::: note
Purchased credit expires one year after you buy it, and OpenAI does not
normally refund it. Do not buy far more than you expect to use in a year.
:::

### Auto-recharge: on or off?

**Auto-recharge** tops your balance up automatically. When the balance drops
below an amount you choose, OpenAI charges your card and adds more credit. It
may already be switched on when you set up billing.

- **Turn it off** if nobody checks the account between events. When the
  credit runs out, Relay stops translating instead of charging your card
  again. This is the safest choice while you are learning.
- **Turn it on** if running out of credit in the middle of an event would be
  worse than a surprise charge. If you do, also set the **monthly recharge
  limit** so it cannot charge more than a fixed amount each month.

To change it, go to **Settings** > **Billing** and look for **Auto recharge**.

::: warning
If the credit runs out during a session, the captions stop. Check the balance
on the **Billing** page before every event.
:::

## Step 3: Create a project for Relay

A **project** is a folder inside your organization. Keys, usage and spending
limits can all belong to one project. If Relay has its own project, you can
see exactly what Relay costs and cap it on its own, apart from anything else
the organization uses OpenAI for.

1. At the top left of the dashboard, click the project name (it may say
   **Default project**).
   A menu should open, listing your projects.
2. Click **Create project**.
3. Name it `Relay` and click **Create**.
   The project menu should now show **Relay** as the current project.

## Step 4: Create the API key

1. Go to <https://platform.openai.com/api-keys>, or click **API keys** in the
   dashboard menu.
   You should see a list of keys. It is empty on a new account.
2. Click **Create new secret key**.
   A form should open.
3. Fill in the form:
   - **Owned by**: choose **You**. (The other choice, **Service account**, is
     a key that belongs to no person. It is useful for larger teams, but not
     needed here.)
   - **Name**: type something that says where the key lives, for example
     `Relay main hall laptop`. This helps you find the right key to delete
     later.
   - **Project**: choose **Relay**.
   - **Permissions**: choose **All**. See the box below.
4. Click **Create secret key**.
   A box should appear showing the full key, with a **Copy** button.
5. Click **Copy**, then go straight on to [Adding the key to
   Relay](#adding-the-key-to-relay). Keep this box open until the key is saved
   in Relay.

::: warning
OpenAI shows the full key **only once**. After you close the box, nobody can
see it again, not even you. If you lose it, delete the key and make a new one.
:::

### Which permissions does Relay need?

When you create a key you choose one of three permission settings:

- **All**: the key can use everything the project allows. This is the
  default.
- **Restricted**: you choose what the key can do, area by area.
- **Read only**: the key can look at things but cannot make requests. **Relay
  does not work with a read-only key.**

We recommend **All**, with the key limited to the **Relay** project. The
project boundary and the spending limit in Step 5 are what protect you.

If your organization requires restricted keys, set **Model capabilities** to
**Request** (OpenAI's documentation lists live audio, `/v1/realtime`, under
this permission) and leave everything else at **None**. If Relay then reports
that the key does not have access, change the key to **All**.

## Step 5: Set a spending limit

OpenAI has two kinds of spending control. They behave very differently.

- A **spend alert** sends an email when spending reaches an amount. It does
  **not** stop anything. Translation carries on and you keep paying.
- A **hard spend limit** stops requests once the month's spending reaches the
  amount. Relay's captions stop until the next month, or until you raise the
  limit.

Even a hard limit is not exact. OpenAI's documentation says enforcement is
not instant, so spending can go slightly over the limit before requests stop.

::: note
A "budget" or "threshold" on its own is only an alert. Look for a switch
called **Enforce a hard limit**. If you do not turn it on, nothing stops.
:::

### A limit for the Relay project

1. Make sure **Relay** is the current project (top left).
2. Click **Settings**, then **Project settings** (or the project name under
   **Project**), then **Limits**.
   You should see a **Spend** section.
3. In **Spend**, click **Edit spend limit**.
4. Enter a **Monthly spend limit**, for example `50`.
5. Turn on **Enforce a hard limit**.
6. Click **Save**.
   The **Spend** section should show your limit.

### A limit for the whole organization

1. Click **Settings**, then **Limits** under **Organization** (it may be
   called **Organization limits**).
2. In **Spend**, click **Edit spend limit**.
3. Enter a **Monthly spend limit** and turn on **Enforce a hard limit**.
4. Click **Save**.

### Email alerts

On the same **Limits** pages, add spend alerts at amounts below your hard
limit, for example at half and at three quarters of it. The alert emails go
to the organization's owners. An alert gives you time to top up, raise the
limit or find out why spending is high before the hard limit cuts the
captions off.

::: tip
Set the hard limit a little above what a normal month costs. Too low, and
it can stop the captions in the middle of an event. Too high, and a session
left running over a weekend can cost a lot before it stops.
:::

::: note
OpenAI also gives each organization its own monthly usage limit, based on
its usage tier (see the next step). That limit is separate from the limits
you set, and you cannot use it as your own cap.
:::

## Step 6: Check the account can use Relay's model

Relay uses OpenAI's live translation model, called `gpt-realtime-translate`.
Relay opens one connection, called a **session**, for each target language
you switch on.

### Usage tier

OpenAI puts every organization in a **usage tier**. The tier depends on how
much you have paid in total, and it sets your **rate limits**: how much you
can use each minute. In September 2026:

- The **Free** tier cannot use `gpt-realtime-translate` at all.
- **Tier 1** starts once you have paid $5. Tier 1 allows 50 minutes of audio
  per minute, which is far more than Relay's 12 languages need.

So buying credit in Step 2 is enough. To check your tier:

1. Click **Settings**, then **Limits** under **Organization**.
   Near the top you should see your current usage tier, for example
   **Tier 1**.
2. Scroll the list of models, or search it, for `gpt-realtime-translate`.
   It should show a limit, not "not supported".

OpenAI moves you up a tier automatically as you spend more.

### Organization verification

Some OpenAI models need **organization verification**: a one-time identity
check with a government photo ID and sometimes a selfie. In September 2026,
OpenAI's page for `gpt-realtime-translate` did not list verification as a
requirement.

If Relay shows an error saying your organization **must be verified**, go to
**Settings** > **General** (under **Organization**) and click **Verify
Organization**. Follow the steps. It can take a while to be approved.

## Adding the key to Relay

There are two ways to give Relay the key. Setup asks for it the first time.
After that, you can change it in the admin panel.

### During setup

When you run setup (`setup.command` on a Mac, `setup.bat` on Windows,
`./setup.sh` on Linux), it asks for the key. The window, called a **terminal**,
shows:

```
OpenAI API key
  Create one at https://platform.openai.com/api-keys
  It is stored only in docker-config/config.json on this machine
  (permissions 0600). Input is hidden.
  API key:
```

(On Windows the middle lines may differ slightly.)

1. Click in the terminal window.
2. Paste the key: `Cmd`+`V` on a Mac, right-click or `Ctrl`+`V` on Windows.
   **Nothing appears as you paste.** This is normal: the key is hidden so
   that nobody can read it over your shoulder.
3. Press `Return` (or `Enter`).
   Setup should go on to ask for the admin token. If you pressed `Return`
   without pasting, it says `The API key cannot be empty.` and asks again.

If you run setup again later, it first asks
`Reconfigure the API key and admin token? [y/N]`. Type `y` and press `Return`
to enter a new key. Press `Return` alone to keep the current one.

### In the admin panel

1. Open the admin panel and sign in.
2. Press **Stop** if a session is running.
3. Click **Credentials** to open that section.
   Next to **OpenAI API key** you should see either `· set …` followed by the
   last four characters of the current key, or `· not set` in red.
4. Click in the **OpenAI API key** box and paste the new key. The box shows
   dots instead of the key.
5. Leave **Admin token (blank = unchanged)** empty, unless you also want to
   change the token.
6. Click **Save credentials**.
   You should see `Saved. Restart capture to use the new key.` The label next
   to **OpenAI API key** should now end in the last four characters of the new
   key.
7. Press **Start capture**.
   Session health should show one `connected` session for each target
   language you switched on.

::: note
Leaving the **OpenAI API key** box empty keeps the current key. Clicking
**Save credentials** with both boxes empty does nothing and shows
`Nothing to save.`
:::

The panel never shows the whole key again, only its last four characters.
Compare those four characters with the key list on the OpenAI **API keys**
page to see which key Relay is using.

### If there is no key

If you press **Start capture** before a key is set, Relay does not start and
shows:

```
No OpenAI API key set. Add one in the admin panel.
```

Add the key as described above, then press **Start capture** again.

### Other errors from OpenAI

Some problems cannot be fixed by trying again. For these, Relay stops the
session instead of retrying, and shows OpenAI's message next to it in the
**Session health** table:

| The message mentions | What it means | What to do |
|---|---|---|
| `incorrect api key` or `invalid api key` | The key is wrong, or was deleted. | Make a new key and save it in **Credentials**. |
| `insufficient_quota` or `exceeded your current quota` | The account has run out of credit. | Buy credit on the **Billing** page. |
| `must be verified` | The organization needs verification. | See [Organization verification](#organization-verification). |
| `does not have access` | The key or project cannot use the model. | Check the key's permissions (use **All**) and your usage tier. |

## Keeping the key safe

The key is stored only on the Relay machine, in a file that only that machine's
user can read. Viewers never see it, and the admin panel shows only its last
four characters. Keeping it safe outside Relay is up to you.

- **Never share the key.** Each machine or person that needs one should get
  their own key.
- **Never paste it into a chat, email, ticket or document.** Chat messages
  and emails are copied to many places and kept for years.
- **Never show it in a screenshot or on a projector.** Close the key box on
  the OpenAI page before you share your screen.
- **Do not write it on the machine.** Relay already has it. You do not need a
  copy in a text file or a sticky note.
- **Delete keys you no longer use**, for example when a laptop is retired.

::: warning
Never share your API key. If you think anyone else has seen it, treat it as
stolen and replace it straight away.
:::

### If the key leaks: replace it

Replacing a key with a new one is called **rotating** it. Deleting the old
key so that it stops working is called **revoking** it.

1. Create a new key for the **Relay** project, as in
   [Step 4](#step-4-create-the-api-key).
2. Save the new key in Relay's **Credentials** section, as in
   [In the admin panel](#in-the-admin-panel).
3. Go to <https://platform.openai.com/api-keys>. Find the old key by its name
   and the last characters shown in the list.
4. Click the delete (bin) icon next to the old key, then **Revoke key**.
   The old key should disappear from the list. It stops working at once.
5. Press **Start capture** in Relay to check that the new key works.
6. Check the **Usage** page for any use you do not recognise.

## Understanding the cost

OpenAI charges Relay's translation **by the minute of audio**, not by the
word. Three things decide the bill:

- **How many target languages are switched on.** Each language is its own
  session, and each session is billed on its own. Three languages cost three
  times as much as one.
- **How long the sessions are open.** Budget for every minute between
  **Start capture** and **Stop**, including quiet stretches when nobody is
  speaking.
- **Nothing else.** The number of viewers does not matter. A room of 500
  phones costs the same as a room of one.

The formula is:

> cost = languages switched on × minutes running × price per minute

### Worked example

::: note
**Price assumption.** OpenAI's pricing page
(<https://developers.openai.com/api/docs/pricing>) listed
`gpt-realtime-translate` at **$0.034 per minute** when this was written, in
September 2026. Prices change. Check that page for the current figure before
you plan a budget.
:::

A two-hour event, with Spanish, French and Korean switched on:

> 3 languages × 120 minutes × $0.034 = **$12.24**

The same three languages left running over a weekend by mistake (48 hours,
2,880 minutes):

> 3 × 2,880 × $0.034 = **$293.76**

Relay also asks OpenAI for the English transcript on each session. The
pricing page lists live transcription (`gpt-live-transcribe`) at $0.017 per
minute. OpenAI may bill this on top of the translation. If it does, the
two-hour example above would cost up to $6.12 more, $18.36 in total.

::: tip
After your first real session, open the **Usage** page on the OpenAI
Platform, choose the **Relay** project, and compare the real cost with your
estimate. Use that figure to plan future events.
:::

::: warning
**Press Stop between sessions.** Relay keeps its sessions open, and OpenAI
keeps billing, until you press **Stop**, even when the room is empty. Press
**Stop** at every break longer than a few minutes and at the end of every
event. This is the most important cost control you have.
:::
