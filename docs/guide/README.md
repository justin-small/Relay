# Relay user guide — source

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
(`brew install pandoc typst`). Without them, `build.sh` runs the same build in
the `pandoc/typst` container, so Docker alone is enough. The version on the
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

1. Start a throwaway rehearsal instance, on its own ports and state folder, so
   your real `docker-config/` is never touched:

   ```bash
   S=$(mktemp -d) && git archive HEAD | tar -x -C "$S" && cd "$S"
   mkdir -p docker-config && chmod 700 docker-config
   c() { docker compose -p relayguide -f docker/docker-compose.yml "$@"; }
   c build
   OPENAI_KEY=sk-guide-rehearsal-key-DEMO ADMIN_TOKEN=guide-demo-token RELAY_ADMIN_FQDN=relay.local \
     c run --rm --no-deps -e OPENAI_KEY -e ADMIN_TOKEN -e RELAY_ADMIN_FQDN relay python tools/write_config.py
   RELAY_ADMIN_IPS=127.0.0.1 c run --rm --no-deps -e RELAY_ADMIN_IPS relay python tools/setup_caddy.py
   RELAY_DEMO=1 RELAY_ADMIN_IPS=127.0.0.1 RELAY_HTTP_PORT=18080 RELAY_HTTPS_PORT=18443 c up -d
   ```

   The key is fake on purpose: nothing here talks to OpenAI.

2. Capture:

   ```bash
   cd docs/guide/tools && npm install && npx playwright install chromium
   RELAY_GUIDE_TOKEN=guide-demo-token RELAY_GUIDE_STATE="$S/docker-config" node capture.mjs
   node capture.mjs admin-schedules viewer-both   # or just some of them
   ```

   The script seeds the instance (targets, schedules, a clean blocklist,
   three test recordings), then writes `images/*.png`. The certificate warning
   needs a visible browser window for a few seconds.

3. Tear down: `docker compose -p relayguide -f docker/docker-compose.yml down`
   from `$S`, then delete `$S`.

What keeps secrets and addresses out of the images:

- the viewer links are rewritten to the example address `192.168.1.50`;
- "running" panels are fed test status through request interception, since
  rehearsal mode never opens a session;
- the key hint shows the last four characters of the fake key (`DEMO`).

Check new images for anything that looks like a real key, token or address
before committing them.

Companion screenshots come from `tools/capture-companion.mjs`; see the
comments at its top.

## Screens that are not captured

Docker's installers, macOS Gatekeeper, Windows SmartScreen, the browsers'
certificate viewers and OpenAI's dashboard are described in words rather than
shown. They are outside Relay, change often, and (for OpenAI) cannot be
captured without a real account. The OpenAI chapter is dated for that reason.

## Publishing

Build with a release version, attach both files to the GitHub release, and
the README's link picks them up:

```bash
GUIDE_VERSION=1.0 docs/guide/build.sh
gh release create guide-v1.0 docs/guide/out/Relay-User-Guide.pdf docs/guide/out/Relay-User-Guide.docx \
  --title "Relay User Guide 1.0" --notes "Operator guide, PDF and Word."
```
