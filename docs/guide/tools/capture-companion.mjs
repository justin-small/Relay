// Captures the Bitfocus Companion screenshots for the guide's Companion
// chapter (src/41-companion.md), annotated with numbered callouts.
//
// It drives a throwaway Companion in Docker against the module's own stand-in
// for Relay (dev/fake-relay.cjs in the module repository), so nothing here
// needs a real Relay, a real admin token or a real address. From a clone of
// https://github.com/justin-small/companion-module-relay-translation:
//
//   npm install && npm run package            # builds relay-translation-<ver>.tgz
//   mkdir -p dev/certs
//   openssl req -x509 -newkey rsa:2048 -nodes -days 30 \
//     -keyout dev/certs/key.pem -out dev/certs/cert.pem \
//     -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost"
//   node dev/fake-relay.cjs &                 # listens on host port 8443
//   docker compose -f dev/compose.yaml down -v   # start from an empty Companion
//   docker compose -f dev/compose.yaml up -d     # Companion on http://localhost:8000
//
// Then, from this folder:
//
//   COMPANION_MODULE_TGZ=/path/to/clone/relay-translation-1.0.0.tgz \
//   COMPANION_FINGERPRINT="$(openssl x509 -in /path/to/clone/dev/certs/cert.pem -noout -fingerprint -sha256 | cut -d= -f2)" \
//     node capture-companion.mjs
//
// Tear down afterwards with `docker compose -f dev/compose.yaml down -v` and
// stop the fake relay.
//
// What the pictures show, and what they do not:
//  - the connection form is photographed with the example address
//    192.168.1.50, port 443 and an empty token field, then refilled with the
//    stand-in's real values (host.docker.internal, 8443, good-token) before it
//    is saved. No real address or token appears in any image.
//  - the fingerprint shown is the stand-in's throwaway test certificate.
//  - Companion must be empty (fresh volume) so the import and "add
//    connection" steps look like a first-time install.

import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(HERE, '..', 'images');
const BASE = process.env.COMPANION_URL || 'http://localhost:8000';
const TGZ = process.env.COMPANION_MODULE_TGZ;
const FINGERPRINT = (process.env.COMPANION_FINGERPRINT || '').trim();
// Where the Companion container reaches the stand-in Relay.
const RELAY_HOST = process.env.COMPANION_RELAY_HOST || 'host.docker.internal';
const RELAY_PORT = process.env.COMPANION_RELAY_PORT || '8443';
const RELAY_TOKEN = process.env.COMPANION_RELAY_TOKEN || 'good-token';
// What the pictures show instead.
const EXAMPLE_HOST = '192.168.1.50';
const EXAMPLE_PORT = '443';

if (!TGZ) {
  console.error('Set COMPANION_MODULE_TGZ to the .tgz built by `npm run package` in the module repository.');
  process.exit(2);
}
mkdirSync(OUT, { recursive: true });

/* ---------- helpers ---------- */

const pause = (p, ms) => p.waitForTimeout(ms);

async function go(page, path) {
  await page.goto(BASE + path);
  await pause(page, 2000);
  // First-run setup wizard (Companion 5): accept the defaults.
  for (let i = 0; i < 12; i++) {
    const wizard = page.locator('[data-base-ui-portal]').filter({ hasText: 'Welcome to Companion' });
    if (!(await wizard.count())) break;
    const next = page.getByRole('button', { name: /^(Next|Apply|Finish)$/ }).last();
    if (!(await next.count())) break;
    await next.click();
    await pause(page, 600);
  }
  if (await page.getByText("What's New in Companion").count()) {
    await page.keyboard.press('Escape');
    await pause(page, 500);
  }
}

// Numbered callouts: a red disc per mark beside the element, and an optional
// outline around it. Same look as capture.mjs.
async function annotate(page, marks) {
  const rects = [];
  for (const m of marks) {
    const box = await m.loc.first().boundingBox();
    if (!box) { console.warn('annotate: no box for mark', m.n); continue; }
    rects.push({ ...m, loc: undefined, r: box });
  }
  await page.evaluate((rects) => {
    document.querySelectorAll('.guide-mark').forEach((n) => n.remove());
    for (const m of rects) {
      const r = { left: m.r.x, top: m.r.y, width: m.r.width, height: m.r.height,
        right: m.r.x + m.r.width, bottom: m.r.y + m.r.height };
      if (m.box !== false) {
        const box = document.createElement('div');
        box.className = 'guide-mark';
        Object.assign(box.style, {
          position: 'fixed', left: (r.left - 4) + 'px', top: (r.top - 4) + 'px',
          width: (r.width + 8) + 'px', height: (r.height + 8) + 'px',
          border: '3px solid #e5372b', borderRadius: '8px', zIndex: 99998, pointerEvents: 'none',
          boxSizing: 'border-box',
        });
        document.body.appendChild(box);
      }
      if (m.n == null) continue;
      const d = 28;
      const pos = m.pos || 'left';
      let x, y;
      if (pos === 'left') { x = r.left - d - 10; y = r.top + r.height / 2 - d / 2; }
      if (pos === 'right') { x = r.right + 10; y = r.top + r.height / 2 - d / 2; }
      if (pos === 'top') { x = r.left + r.width / 2 - d / 2; y = r.top - d - 10; }
      if (pos === 'bottom') { x = r.left + r.width / 2 - d / 2; y = r.bottom + 10; }
      if (pos === 'topleft') { x = r.left - d / 2 - 2; y = r.top - d / 2 - 2; }
      x += m.dx || 0; y += m.dy || 0;
      x = Math.max(2, Math.min(x, document.documentElement.clientWidth - d - 2));
      y = Math.max(2, y);
      const dot = document.createElement('div');
      dot.className = 'guide-mark';
      dot.textContent = String(m.n);
      Object.assign(dot.style, {
        position: 'fixed', left: x + 'px', top: y + 'px', width: d + 'px', height: d + 'px',
        borderRadius: '50%', background: '#e5372b', color: '#fff', font: '700 15px/28px system-ui, sans-serif',
        textAlign: 'center', zIndex: 99999, boxShadow: '0 0 0 2px #fff, 0 2px 6px rgba(0,0,0,.5)',
        pointerEvents: 'none',
      });
      document.body.appendChild(dot);
    }
  }, rects);
}

async function clearMarks(page) {
  await page.evaluate(() => document.querySelectorAll('.guide-mark').forEach((n) => n.remove()));
}

async function shot(page, file, opts = {}) {
  await page.screenshot({ path: join(OUT, file), ...opts });
  await clearMarks(page);
  console.log('wrote', file);
}

// Screenshot a region around one element, with room for callouts.
async function shotAround(page, loc, file, pad = 48) {
  const box = await loc.first().boundingBox();
  const vp = page.viewportSize();
  const x = Math.max(0, box.x - pad), y = Math.max(0, box.y - pad);
  const clip = { x, y, width: Math.min(vp.width - x, box.width + pad * 2), height: Math.min(vp.height - y, box.height + pad * 2) };
  await shot(page, file, { clip });
}

async function hoverStatus(page) {
  const row = page.locator('.collections-nesting-table-row-item').filter({ hasText: 'Relay: Relay' }).first();
  const icon = row.locator('.hand > .ms-2 svg').first();
  const portals = () => page.locator('[data-base-ui-portal]').allInnerTexts();
  await page.mouse.move(10, 10);
  await pause(page, 400);
  const before = new Set(await portals());
  await icon.hover();
  await pause(page, 900);
  const role = page.locator('[role="tooltip"]');
  let text = (await role.count()) ? (await role.last().innerText()) : '';
  if (!text) text = (await portals()).filter((t) => !before.has(t)).join(' ');
  return { icon, text: text.trim() };
}

async function openConnection(page) {
  await go(page, '/connections');
  await page.locator('.collections-nesting-table-row-item').filter({ hasText: 'Relay: Relay' })
    .locator('.hand').first().click();
  await pause(page, 1200);
}

async function setField(page, id, value) {
  const input = page.locator(`input[id$="_${id}"]`).first();
  // The port box clamps as you type, so select everything and type over it.
  await input.click();
  await page.keyboard.press(process.platform === 'darwin' ? 'Meta+A' : 'Control+A');
  await page.keyboard.press('Backspace');
  if (value) await page.keyboard.type(String(value));
  await page.keyboard.press('Tab');
  const got = await input.inputValue();
  if (got !== String(value)) console.warn(`setField ${id}: wanted ${value}, got ${got}`);
}

async function setSwitch(page, id, on) {
  const input = page.locator(`input[id$="_${id}"]`).first();
  if ((await input.isChecked()) !== on) {
    await input.locator('xpath=preceding-sibling::*[@role="switch"][1]').click();
  }
}

async function save(page) {
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await pause(page, 3500);
}

async function press(page, row, col) {
  const res = await page.request.post(`${BASE}/api/location/1/${row}/${col}/press`);
  if (!res.ok()) console.warn('press', row, col, res.status());
}

/* ---------- run ---------- */

const browser = await chromium.launch();
const ctx = await browser.newContext({
  viewport: { width: 1400, height: 900 }, deviceScaleFactor: 2, locale: 'en-US', colorScheme: 'light',
});
const page = await ctx.newPage();

// 1. Modules page, before importing.
await go(page, '/modules');
const importBtn = page.locator('label', { hasText: 'Import module package' });
await annotate(page, [
  { n: 1, loc: page.locator('a', { hasText: 'Modules' }).first(), pos: 'right', dx: -60 },
  { n: 2, loc: importBtn, pos: 'left', dx: 8 },
]);
await shot(page, 'companion-modules-import.png');

// 2. Import the package and show it installed.
await importBtn.locator('input[type=file]').setInputFiles(TGZ);
await pause(page, 4000);
await go(page, '/modules');
await page.getByText('Relay: Relay', { exact: true }).click();
await pause(page, 1500);
await annotate(page, [
  { n: 1, loc: page.getByText('Relay: Relay', { exact: true }).last(), pos: 'left', dx: -30 },
  { n: 2, loc: page.getByText('Manage Relay: Relay (Connection)'), pos: 'left', box: false },
  { n: 3, loc: page.getByText('1.0.0', { exact: true }).last(), pos: 'right' },
]);
await shot(page, 'companion-module-installed.png');

// 3. Add New Connection, filtered to installed modules.
await go(page, '/connections');
const installedOnly = page.getByRole('button', { name: 'Installed Only' });
if (await installedOnly.isEnabled()) await installedOnly.click();
const search = page.locator('input[placeholder*="Search"]').last();
await search.fill('relay');
await pause(page, 1000);
const addRow = page.locator(':text-is("Relay: Relay")').locator('xpath=..');
const addBtn = addRow.getByRole('button', { name: 'Add' });
await annotate(page, [
  { n: 1, loc: search, pos: 'left', dx: 0 },
  { n: 2, loc: addBtn, pos: 'left' },
]);
await shot(page, 'companion-add-connection.png');

// 4. The add dialog, choosing the imported version.
await addBtn.click();
await pause(page, 1000);
const version = page.getByRole('combobox', { name: 'Module Version' });
await version.click();
await pause(page, 600);
await page.getByRole('option', { name: /1\.0\.0/ }).click();
await pause(page, 500);
const dialog = page.locator('[role="dialog"]').filter({ hasText: 'Add Relay: Relay' });
const labelInput = dialog.locator('input[type="text"]').first();
const dialogAdd = dialog.getByRole('button', { name: 'Add', exact: true });
await annotate(page, [
  { n: 1, loc: labelInput, pos: 'left', dx: -150 },
  { n: 2, loc: version, pos: 'left', dx: -150 },
  { n: 3, loc: dialogAdd, pos: 'bottom' },
]);
await shot(page, 'companion-add-connection-version.png');
await dialogAdd.click();
await pause(page, 2500);

// 5. The connection form, photographed with example values and no token.
await setField(page, 'host', EXAMPLE_HOST);
await setField(page, 'port', EXAMPLE_PORT);
await setField(page, 'token', '');
if (FINGERPRINT) await setField(page, 'fingerprint', FINGERPRINT);
await pause(page, 500);
const field = (id) => page.locator(`[id$="_${id}"]`).first();
const label = (text) => page.locator('label', { hasText: text }).first();
await annotate(page, [
  { n: 1, loc: label('Host'), pos: 'left', box: false, dx: 20 },
  { n: null, loc: field('host') },
  { n: 2, loc: label('Port'), pos: 'left', box: false, dx: 20 },
  { n: null, loc: field('port') },
  { n: 3, loc: label('Admin token'), pos: 'left', box: false, dx: 20 },
  { n: null, loc: field('token') },
  { n: 4, loc: label('Use HTTPS'), pos: 'left', box: false, dx: 20 },
  { n: 5, loc: label('Accept self-signed'), pos: 'left', box: false, dx: 20 },
  { n: 6, loc: label('Certificate SHA-256'), pos: 'left', box: false, dx: 20 },
  { n: null, loc: field('fingerprint') },
  { n: 7, loc: page.getByRole('button', { name: 'Save', exact: true }), pos: 'right', dx: 80 },
]);
await shot(page, 'companion-connection-config.png');

// Now the stand-in's real values, and save.
await setField(page, 'host', RELAY_HOST);
await setField(page, 'port', RELAY_PORT);
await setField(page, 'token', RELAY_TOKEN);
await save(page);

// 6. Connected: green status.
await go(page, '/connections');
await pause(page, 1500);
const ok = await hoverStatus(page);
console.log('status (good config):', JSON.stringify(ok.text));
await annotate(page, [
  { n: 1, loc: ok.icon, pos: 'bottom', box: false },
  { n: 2, loc: page.locator('.collections-nesting-table-row-item').filter({ hasText: 'Relay: Relay' }).locator('[role="switch"]').first(), pos: 'bottom', box: false },
]);
await shot(page, 'companion-connection-ok.png');

// 7. Failure messages. Only the token one is photographed; the others are
// logged so the chapter can quote them exactly.
async function tryConfig(name, fields, file) {
  await openConnection(page);
  for (const [k, v] of Object.entries(fields)) {
    if (typeof v === 'boolean') await setSwitch(page, k, v);
    else await setField(page, k, v);
  }
  await save(page);
  await pause(page, 2500);
  await go(page, '/connections');
  await pause(page, 1500);
  const st = await hoverStatus(page);
  console.log(`status (${name}):`, JSON.stringify(st.text));
  if (file) {
    await annotate(page, [{ n: 1, loc: st.icon, pos: 'left', box: false, dx: -56 }]);
    await shot(page, file);
  }
}
await tryConfig('wrong token', { token: 'wrong-token' }, 'companion-connection-error.png');
await tryConfig('wrong fingerprint', { token: RELAY_TOKEN, fingerprint: '00'.repeat(32) });
await tryConfig('self-signed refused', { fingerprint: '', acceptSelfSigned: false });
await tryConfig('nothing answering', { port: '9' });
await tryConfig('restored', { port: RELAY_PORT, fingerprint: FINGERPRINT, acceptSelfSigned: true });

// 8. Presets. The button pages get a wider window so all eight columns of
// the grid fit.
await page.setViewportSize({ width: 1900, height: 900 });
await go(page, '/buttons');
await page.getByText('Presets', { exact: true }).first().click();
await pause(page, 800);
await page.getByRole('button', { name: /Relay: Relay.*presets/ }).click();
await pause(page, 800);
if (await page.locator('.presets-section-row[aria-expanded="false"]').count()) {
  await page.locator('.presets-section-row').first().click();
}
await pause(page, 4000);
const preset = (title) => page.locator(`.presets-icon-grid .button-border[title="${title}"]`).first();
const PRESETS = [
  'Start capture', 'Stop capture', 'Toggle capture', 'Session clock', 'Audio meter',
  'Viewers', 'Status at a glance', 'Toggle Spanish', 'Toggle French',
];
await annotate(page, PRESETS.map((t, i) => ({ n: i + 1, loc: preset(t), pos: 'topleft', box: false })));
await shotAround(page, page.locator('.secondary-panel'), 'companion-presets.png', 0);

// 9. Drag them onto page 1: row 1 = transport and clock, row 2 = languages and meters.
const cell = (row, col) => page.locator('.button-grid-canvas .button-control').filter({ hasText: new RegExp(`^${row}/${col}$`) }).first();
const LAYOUT = [
  ['Start capture', 1, 1], ['Stop capture', 1, 2], ['Toggle capture', 1, 3], ['Session clock', 1, 4],
  ['Status at a glance', 1, 5], ['Toggle Spanish', 2, 1], ['Toggle French', 2, 2],
  ['Audio meter', 2, 3], ['Viewers', 2, 4],
];
for (const [title, row, col] of LAYOUT) {
  const from = await preset(title).boundingBox();
  const target = await cell(row, col).boundingBox();
  if (!from || !target) { console.warn('drag: missing', title); continue; }
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(700, 450, { steps: 5 });
  await page.mouse.move(target.x + target.width / 2, target.y + target.height / 2, { steps: 10 });
  await page.mouse.up();
  await pause(page, 700);
}
await go(page, '/buttons');
await pause(page, 2500);
const grid = page.locator('.button-grid-canvas').first();
const gridCell = (row, col) => page.locator('.button-grid-canvas .button-control').nth(row * 8 + col);
await annotate(page, [
  { n: 1, loc: gridCell(1, 1), pos: 'topleft', box: false },
  { n: 2, loc: gridCell(1, 4), pos: 'topleft', box: false },
  { n: 3, loc: gridCell(2, 1), pos: 'topleft', box: false },
  { n: 4, loc: gridCell(2, 2), pos: 'topleft', box: false },
]);
await shotAround(page, grid, 'companion-buttons.png', 24);

// 10. Press Start through Companion itself, and photograph the same page live.
await press(page, 1, 1);
await pause(page, 1500);
// The stand-in only sends target state when the stream opens (a real Relay
// pushes it on every change), so switch the connection off and on to make
// the module re-read it. What the buttons then show is the stand-in's state.
await go(page, '/connections');
const sw = page.locator('.collections-nesting-table-row-item').filter({ hasText: 'Relay: Relay' }).locator('[role="switch"]').first();
await sw.click(); await pause(page, 1500);
await sw.click(); await pause(page, 3000);
await go(page, '/buttons');
await pause(page, 3000);
await annotate(page, [
  { n: 1, loc: gridCell(1, 1), pos: 'topleft', box: false },
  { n: 2, loc: gridCell(1, 4), pos: 'topleft', box: false },
  { n: 3, loc: gridCell(2, 1), pos: 'topleft', box: false },
  { n: 4, loc: gridCell(2, 2), pos: 'topleft', box: false },
]);
await shotAround(page, grid, 'companion-buttons-live.png', 24);
await press(page, 1, 2); // stop again

await browser.close();
