# Relay user guide: source

The operator guide, published as `Relay-User-Guide.pdf` and
`Relay-User-Guide.docx` from one Markdown source. It is written for people
who run Relay at a venue, not for developers; see [STYLE.md](STYLE.md) before
editing.

```
metadata.yaml        title, subtitle, cover text
src/NN-*.md          one file per chapter, built in filename order
images/              screenshots (retaken by tools/capture*.mjs)
build/guide.typst    PDF layout: cover, contents, page numbers, callouts
build/callouts.lua   ::: note / tip / warning boxes, for both formats
build/make_reference.py  Word styles for the DOCX (callouts, footer)
build.sh             builds both into out/
tools/               screenshot capture (Playwright)
```

## Building

```bash
docs/guide/build.sh          # both
docs/guide/build.sh pdf
docs/guide/build.sh docx
```

Needs [Pandoc](https://pandoc.org) 3.x and [Typst](https://typst.app)
(`brew install pandoc typst`), and `python3`. The version on the
cover comes from `git describe`; set `GUIDE_VERSION` (and optionally
`GUIDE_DATE`) to override it for a release:

```bash
GUIDE_VERSION=1.0 docs/guide/build.sh
```

Word asks to update fields when the DOCX is first opened. Say yes: that fills
in the table of contents and page numbers.

## Retaking the Relay screenshots

Every Relay screenshot is produced by `tools/capture.mjs`, annotated, from a
rehearsal instance with throwaway credentials. After a UI change, retake them
rather than editing images by hand.

1. Start a throwaway rehearsal instance from the checkout, on its own ports and
   state folder, so your real state folder is never touched:

   ```bash
   S=$(mktemp -d) && chmod 700 "$S"
   cat > "$S/config.json" <<'JSON'
   {"openai_api_key": "sk-guide-rehearsal-key-DEMO", "admin_token": "guide-demo-token",
    "admin_fqdn": "relay.local", "host": "127.0.0.1", "admin_host": "127.0.0.1",
    "port": 18080, "admin_port": 18443, "modules": {"planning_center": {"enabled": true}}}
   JSON
   RELAY_HOME="$S" RELAY_DEMO=1 RELAY_ANNOUNCE=0 python run.py
   ```

   The key is fake on purpose: nothing here talks to OpenAI. The Planning Center
   module is switched on because its page is captured. `RELAY_ANNOUNCE=0`
   keeps this copy from announcing itself to caption panels, and the ports keep
   it clear of a real Relay on the same machine.

2. Capture:

   ```bash
   cd docs/guide/tools && npm install && npx playwright install chromium
   RELAY_GUIDE_USER=guide RELAY_GUIDE_PASSWORD=guide-demo-password \
     RELAY_GUIDE_TOKEN=guide-demo-token RELAY_GUIDE_STATE="$S" node capture.mjs
   node capture.mjs admin-schedules viewer-both   # or just some of them
   ```

   The instance starts with no users, so the first run uses the admin token to
   create the Owner `guide` from the first-run form. Later runs sign in with
   the username and password, and `RELAY_GUIDE_TOKEN` can be left out.

   The script seeds the instance (targets, schedules, a clean blocklist,
   three test recordings), then writes `images/*.png`. The certificate warning
   needs a visible browser window for a few seconds.

3. Tear down: press `Ctrl+C` in the Relay terminal, then delete `$S`.

What keeps secrets and addresses out of the images:

- the viewer links, this machine's real LAN addresses and the throwaway
  instance's ports are rewritten to the example address `192.168.1.50`, and the
  state folder's path to `~/Library/Application Support/Relay`;
- "running" panels are fed test status through request interception, since
  rehearsal mode never opens a session;
- the key hint shows the last four characters of the fake key (`DEMO`).

Check new images for anything that looks like a real key, token or address
before committing them.

The script also checks three panels in and sets up three alert buttons, for the
Panels and Alerts pages. The two flashing screenshots (`flash-port-picker.png`,
`flash-progress.png`) are taken by hand from a real flash and are not retaken.

Companion screenshots come from `tools/capture-companion.mjs`; see the
comments at its top.

## Screens that are not captured

Relay's installers, the tray menu, macOS Gatekeeper, Windows SmartScreen, the browsers'
certificate viewers and OpenAI's dashboard are described in words rather than
shown. They are outside Relay, change often, and (for OpenAI) cannot be
captured without a real account. The OpenAI chapter is dated for that reason.

## Publishing

The release workflow builds both files and attaches them to every tagged
release, with the version on the cover taken from the tag, so the README's
`releases/latest` links always point at the newest guide. See **Releasing** in
the top-level README. CI builds on Linux with pinned pandoc and typst, where
the template's sans-serif fallback is Liberation Sans.
