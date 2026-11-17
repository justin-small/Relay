# Relay's Google and Meta apps

Relay signs in to YouTube and Facebook as **Relay's own app**. Each service asks the person signing in to approve
that app, so the app needs a name, a logo, a privacy policy and the service's review. This page tells the owner of
the Relay project how to make each app, once. Churches never do this: they press **Connect**.

Relay signs in through **relay-cloud** (`app/oauth.py`, #304). The operator presses Connect and opens the
service's sign-in; the service sends the browser back to `https://cloud.relaysystems.dev/oauth/<service>/callback`,
the one redirect registered with each app. relay-cloud holds each app's secret, swaps the sign-in for tokens and
keeps them until the church's Relay collects them (at most 15 minutes); refreshing goes through it too. A church
that uses its own app instead signs in with the device flow (a code at google.com/device) and never touches
relay-cloud.

**What you need first, for both services:**
- A public **privacy policy** at a stable address on relaychurchsystems.com. It must say what Relay reads (channel
  or Page name, live broadcast status, viewer counts), that it stays on the church's own computer, and that the
  sign-in passes through Relay's cloud only to be exchanged and renewed.
- A **terms of service** page and a **data deletion** page. For data deletion, explain Disconnect, and how to
  remove Relay from the Google account's security page or Facebook's Business Integrations.
- An app **logo**, 120×120 px or larger.
- A **demo video** (unlisted YouTube is fine). Show pressing Connect in Relay, opening the sign-in, the consent
  screen with the permissions, and where Relay then uses them (the live status).

Both reviews take days to weeks, so start them early. Dashboard labels change often. If a step below is named
differently, follow its meaning.

## Google (YouTube)

1. Go to **console.cloud.google.com** and sign in with the account that will own Relay's app. Use an organization
   account, not a personal one, so the app outlives any one person. Create a project named **Relay**.
2. Open **APIs & Services → Library** and enable **YouTube Data API v3**.
3. Open **Google Auth Platform** (formerly *OAuth consent screen*):
   - **Branding:** set the app name "Relay", a support email, the logo, the home page, the privacy policy and the
     terms. Add the authorized domains `relaychurchsystems.com` and `relaysystems.dev`. Prove you own each in
     Google Search Console with the same account (`relaysystems.dev` is a DNS TXT record on its Cloudflare zone).
   - **Audience:** choose **External**.
   - **Data access:** add the scope `https://www.googleapis.com/auth/youtube.readonly`. This is the only scope the
     live status needs. Add more only when a feature that needs them ships. For example, looking up the caption
     ingestion URL (#140) would need `youtube.force-ssl`.
4. Open **Clients → Create client** and choose the application type **Web application**. Name it "Relay (web)" and
   add the authorized redirect URI `https://cloud.relaysystems.dev/oauth/google/callback`. Download the JSON and put
   its client ID and secret on relay-cloud (see *What goes where* below), never in chat or the repository.
5. **Publish:** on **Audience**, press **Publish app** to move it from *Testing* to *In production*. While it
   is in Testing, only listed test users can sign in, and **their refresh tokens expire after 7 days**. A church
   would have to sign in again every week.
6. **Verification:** `youtube.readonly` is a *sensitive* scope, so Google asks to verify the app. Submit from
   **Verification Center**. Include the scope justification ("show the church's own live broadcast status and
   concurrent viewers to its production team"), the demo video and the privacy policy. Until it is verified, the
   consent screen shows "Google hasn't verified this app" and the app is capped at 100 users. That is fine for
   testing with a few churches.
7. **Quota:** the YouTube Data API allows 10,000 units a day per project, shared by every Relay install. A
   `videos.list` or `liveBroadcasts.list` call costs 1 unit. The YouTube module must poll slowly (about once a
   minute while live, rarely otherwise). Request more quota before many churches use it.

To try it before the app exists: in the module's advanced settings, a church can use its own client (bring your
own app). Follow steps 1–3 in its own Google account, then create a **TVs and Limited Input devices** client (the
device flow needs no redirect), keep the app in Testing, and add itself as a test user.

## Meta (Facebook Pages)

1. Go to **developers.facebook.com**, sign in with the account that will own the app, and register as a developer.
   Under **Business settings**, make a Meta Business portfolio for Relay. Business verification needs it later.
2. **My Apps → Create app**. When asked for a use case, pick the one for managing Pages or for Facebook Login
   (**Other** if neither is listed). Pick the app type **Business**, and link the Relay business portfolio.
3. In **Facebook Login for Business → Settings**, keep **Client OAuth login**, **Web OAuth login**, **Enforce
   HTTPS** and **Strict Mode** on, and add the valid OAuth redirect URI
   `https://cloud.relaysystems.dev/oauth/facebook/callback`. Leave **Login from Devices** off.
4. **App settings → Basic:** fill in the display name "Relay", the app domains `relaysystems.dev` and
   `relaychurchsystems.com`, the icon, the privacy policy, the terms, the data deletion instructions URL, the
   category and the contact email. Copy the **App ID**.
5. **App settings → Basic → App secret:** relay-cloud needs it to swap the sign-in for a token. It goes only on
   relay-cloud, never into Relay or the repository.
6. **Permissions:** the live status needs `pages_show_list` (to choose the church's Page) and
   `pages_read_engagement` (its live videos and viewer counts). `public_profile` comes with login. Do not ask for
   anything that publishes.
7. **App Review:** for each of those two permissions, request **Advanced Access**. Give the justification, the demo
   video and step-by-step test instructions. Advanced Access also needs **Business verification** of the
   portfolio (legal name, address, a document), which can take a while. Until then, only people with a role on the
   app (admins, developers, testers) can connect, which is enough for testing.
8. Switch the app to **Live** mode once review passes.

relay-cloud swaps Facebook's short-lived user token for a long-lived one. The Facebook module (PR 3) exchanges it for a Page token,
which does not expire. Meta revokes that token when the person who connected loses their role on the Page or
changes their password. Relay then shows "Facebook needs signing in again".

## What goes where

| Service | relay-cloud Worker secrets | Relay |
|---|---|---|
| Google | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` (the *Web application* client) | nothing |
| Meta | `FACEBOOK_APP_ID`, `FACEBOOK_APP_SECRET` | nothing |

Relay itself carries no app credentials: a release, a fork's build and a copy run from source all sign in through
relay-cloud. Set or replace a secret from a terminal that is signed in to Cloudflare (`npx wrangler login`), piping
the value in so it never sits on a command line or in the shell history, for example from Google's downloaded JSON:

```bash
python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["web"]["client_secret"],end="")' client_secret_….json | npx wrangler secret put GOOGLE_CLIENT_SECRET --name relay-cloud
```

Until a service's pair is set, relay-cloud answers its Connect with "not configured" and Relay says its sign-in
service did not answer.

Never put an App secret, a personal access token, or any refresh or access token in the repository or in an
issue. gitleaks runs on every push (`tests/run_security.py`).
