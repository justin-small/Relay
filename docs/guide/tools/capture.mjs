// Captures every Relay screenshot in the user guide, annotated.
//
// Run it against a rehearsal instance (RELAY_DEMO=1) with throwaway
// credentials -- see ../README.md for the exact commands. Nothing here talks
// to OpenAI, and nothing it captures shows a real key, token or address:
//
//  - the viewer links, and this machine's own LAN addresses and the throwaway state folder's path
//    anywhere in the panel, are rewritten to 192.168.1.50 and ~/Library/Application Support/Relay;
//  - the "running" panel shots are fed test data through request
//    interception, because rehearsal mode never opens a real session;
//  - the key hint shows the last four characters of the throwaway key.
//
//   RELAY_GUIDE_USER=... RELAY_GUIDE_PASSWORD=... RELAY_GUIDE_STATE=/path/to/state-folder \
//     node capture.mjs [name ...]
//
// Sign-in: Relay has per-person accounts, so the script signs in through
// POST /admin/login with RELAY_GUIDE_USER and RELAY_GUIDE_PASSWORD (an Owner
// or Admin of the rehearsal instance; the account must not still have a
// temporary password). On a brand-new instance with no users yet, also set
// RELAY_GUIDE_TOKEN to its admin token: the script then creates that Owner
// from the first-run form, and the token header works until it exists.
// Variables: RELAY_GUIDE_USER, RELAY_GUIDE_PASSWORD, RELAY_GUIDE_TOKEN
// (first run only), RELAY_GUIDE_STATE, RELAY_VIEW_URL, RELAY_ADMIN_URL.

import { chromium, request } from 'playwright';
import { mkdirSync, realpathSync, writeFileSync } from 'node:fs';
import { networkInterfaces } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(HERE, '..', 'images');
const VIEW = process.env.RELAY_VIEW_URL || 'http://localhost:18080';
const ADMIN = process.env.RELAY_ADMIN_URL || 'https://localhost:18443';
const TOKEN = process.env.RELAY_GUIDE_TOKEN;
const USER = process.env.RELAY_GUIDE_USER;
const PASSWORD = process.env.RELAY_GUIDE_PASSWORD;
const STATE = process.env.RELAY_GUIDE_STATE; // the instance's state folder (RELAY_HOME), for test recordings
const EXAMPLE = 'http://192.168.1.50/';

// The instance runs on this machine, so its real LAN addresses and the throwaway state folder's path (which
// holds the user name) would show in the panel. Every admin API answer is rewritten to the example address
// and to the macOS state folder the guide names.
const REAL_IPS = Object.values(networkInterfaces()).flat()
  .filter((a) => a && !a.internal && a.address !== '192.168.1.50').map((a) => a.address);
const STATE_PATHS = STATE ? [...new Set([realpathSync(STATE), STATE])].sort((a, b) => b.length - a.length) : [];
const EXAMPLE_STATE = '~/Library/Application Support/Relay';
function scrub(text) {
  for (const p of STATE_PATHS) text = text.split(p).join(EXAMPLE_STATE);
  for (const ip of REAL_IPS) text = text.split(ip).join('192.168.1.50');
  // The throwaway instance's own ports: a normal install uses 80 and 443, which links do not show.
  for (const port of [new URL(VIEW).port, new URL(ADMIN).port].filter(Boolean)) text = text.split(':' + port + '/').join('/');
  return text;
}
const ONLY = process.argv.slice(2);

if (!(USER && PASSWORD) && !TOKEN) {
  console.error('Set RELAY_GUIDE_USER and RELAY_GUIDE_PASSWORD to an account on the rehearsal instance '
    + '(or RELAY_GUIDE_TOKEN, its admin token, while it has no users yet).');
  process.exit(2);
}
mkdirSync(OUT, { recursive: true });

/* ---------- test data ---------- */

const now = () => Date.now() / 1000;

const DEVICES = [
  { label: 'USB Audio CODEC', channels: 2, default_samplerate: 48000, default: false },
  { label: 'Built-in Microphone', channels: 1, default_samplerate: 48000, default: true },
];

function audio(over = {}) {
  return {
    running: true, device: 'USB Audio CODEC', native_rate: 48000, target_rate: 24000,
    channel: 'mix', level: 0.12, peak: 0.3, peak_hold: 0.3, rms_dbfs: -21.4, peak_dbfs: -11.8,
    clipping: false, clipped_samples: 0, speaking: true, error: null,
    last_audio_ts: now(), consumers: 2, dropped_capture: 0, ...over,
  };
}

function session(target, over = {}) {
  return {
    name: 'translate:' + target, kind: 'translate', state: 'connected', error: null,
    model: 'gpt-realtime-translate', last_delta_ts: now() - 0.4, connected_at: now() - 1520,
    reconnects: 0, fatal: false, dropped_audio: 0, ...over,
  };
}

// Recorded runs, written to disk so the panel lists real files and the
// download links work. Timestamps are fixed so the shot does not change
// every time it is retaken.
function seedRecordings() {
  if (!STATE) return;
  const runs = [
    { id: '20260920-100002', start: 1789912802, mins: 94, lines: 612 },
    { id: '20260920-170011', start: 1789938011, mins: 71, lines: 455 },
    { id: '20260923-200005', start: 1790193605, mins: 38, lines: 198 },
  ];
  const script = [
    ['Good evening, and welcome.', 'Buenas noches y bienvenidos.'],
    ['Please find a seat; we will begin in a moment.', 'Por favor, tomen asiento; empezaremos en un momento.'],
  ];
  for (const r of runs) {
    const dir = join(STATE, 'recordings', r.id);
    mkdirSync(dir, { recursive: true });
    const rows = [];
    for (let i = 0; i < r.lines / 2; i++) {
      const [en, es] = script[i % script.length];
      const t0 = r.start + i * 6;
      rows.push({ stream: 'transcription', lang: 'ENGLISH', seq: i, t0, t1: t0 + 2.5, text: en });
      rows.push({ stream: 'translation', lang: 'SPANISH', seq: i, t0: t0 + 0.8, t1: t0 + 3.4, text: es });
    }
    writeFileSync(join(dir, 'lines.jsonl'), rows.map((x) => JSON.stringify(x)).join('\n') + '\n');
    writeFileSync(join(dir, 'meta.json'), JSON.stringify({
      run_id: r.id, started: r.start, ended: r.start + r.mins * 60,
      source_language: 'ENGLISH', targets: ['SPANISH'], lines: rows.length,
    }, null, 2));
  }
}

const CLEAN_BLOCKLIST = [
  '# One word or phrase per line. Lines starting with # are comments.',
  'damn', 'hell', 'off the record', 'mierda', 'hijo de puta',
].join('\n');

const SCHEDULES = [
  { name: 'Sunday morning', enabled: true, repeat: 'weekly', days: [0], start_date: null, end_date: null,
    start_time: '10:00', stop_time: '12:00', timezone: 'America/Chicago' },
  { name: 'Wednesday night', enabled: true, repeat: 'weekly', days: [3], start_date: null, end_date: null,
    start_time: '20:00', stop_time: '21:30', timezone: 'America/Chicago' },
  { name: 'Christmas Eve', enabled: true, repeat: 'once', days: [], start_date: '2026-12-24', end_date: null,
    start_time: '19:00', stop_time: '20:30', timezone: 'America/Chicago' },
];

// An API client for the panel: signed in with the account (the session cookie is kept), or, with only the
// token, sending it as a header (accepted only while Relay has no users).
async function apiContext() {
  if (USER && PASSWORD) {
    const api = await request.newContext({ baseURL: ADMIN, ignoreHTTPSErrors: true });
    const res = await api.post('/admin/login', { form: { username: USER, password: PASSWORD } });
    const me = await api.get('/api/admin/me');
    if (!res.ok() || !me.ok()) {
      // First run: no users yet, so make the Owner the way the first-run form does.
      if (TOKEN) {
        await api.post('/admin/setup', { form: { token: TOKEN, username: USER, name: USER, password: PASSWORD, confirm: PASSWORD } });
        if ((await api.get('/api/admin/me')).ok()) return api;
      }
      throw new Error('Could not sign in as ' + USER + ' (wrong password, or a temporary password not yet changed).');
    }
    return api;
  }
  return request.newContext({ baseURL: ADMIN, ignoreHTTPSErrors: true, extraHTTPHeaders: { 'x-admin-token': TOKEN } });
}

async function seed() {
  const api = await apiContext();
  const ok = async (res) => {
    if (!res.ok()) throw new Error(res.url() + ' -> ' + res.status() + ' ' + (await res.text()));
    return res.json();
  };
  await ok(await api.post('/api/admin/stop'));
  await ok(await api.post('/api/admin/config', {
    data: {
      blocklist: CLEAN_BLOCKLIST, default_font_px: 40, history_lines: 40,
      recording: { enabled: true, keep_runs: 20 },
      realtime: { segment_idle_s: 1.0, segment_max_idle_s: 10.0 },
    },
  }));
  for (const t of ['SPANISH', 'FRENCH']) {
    await ok(await api.post('/api/admin/target/' + t, { data: { enabled: true } }));
  }
  const existing = await ok(await api.get('/api/admin/schedules'));
  for (const s of existing.schedules) await api.delete('/api/admin/schedules/' + s.id);
  for (const s of SCHEDULES) await ok(await api.post('/api/admin/schedules', { data: s }));
  seedRecordings();
  await seedPanels(api);
  await api.dispose();
}

// Three panels check in as real ones would (the viewer port, no login); the operator then names two and
// sets up the alert buttons. Panel ids are made up.
const PANEL_IDS = ['a1b2c3d4e5f60718293a4b5c6d7e8f90', '0123456789abcdef0123456789abcdef',
  'fedcba9876543210fedcba9876543210'];
const ALERT_BUTTONS = [
  { name: 'Front of house', icon: 'sliders', panels: [PANEL_IDS[1]], screens: ['Stage TV'] },
  { name: 'Stage', icon: 'people', panels: [PANEL_IDS[2]], screens: [] },
  { name: 'Video', icon: 'video', panels: [PANEL_IDS[0]], screens: [] },
];

async function seedPanels(admin) {
  const view = await request.newContext({ baseURL: VIEW });
  const rows = [
    [0, [{ stream: 'translation', lang: 'SPANISH' }], 0, 1],
    [1, [{ stream: 'transcription' }], 0, 2],
    [2, [{ stream: 'transcription' }, { stream: 'translation', lang: 'FRENCH' }], 180, 3],
  ];
  for (const [i, feeds, rotation] of rows) {
    const res = await view.post('/api/panels/checkin', {
      data: { id: PANEL_IDS[i], name: '', version: '1.4.0', board: 'crowpanel-adv7', rotation, rotation_rev: 0,
        scripts: ['latin'], feeds, ip: '192.168.1.' + (60 + i), uptime_s: 172800 + i * 3600, rssi: -52 - i * 7,
        feed_rev: 0 },
    });
    if (!res.ok()) throw new Error('panel check-in -> ' + res.status());
  }
  await view.dispose();
  const names = ['Lobby', 'Front of house', 'Green room'];
  for (let i = 0; i < 3; i++) {
    const res = await admin.post('/api/admin/panels/' + PANEL_IDS[i], { data: { name: names[i] } });
    if (!res.ok()) throw new Error('panel rename -> ' + res.status() + ' ' + (await res.text()));
  }
  const res = await admin.post('/api/admin/alerts', { data: { auto_stop_s: 120, buttons: ALERT_BUTTONS } });
  if (!res.ok()) throw new Error('alerts -> ' + res.status() + ' ' + (await res.text()));
}

/* ---------- page helpers ---------- */

// Numbered callouts: a red disc per mark, placed beside the element's box,
// plus an optional outline around the element itself.
async function annotate(page, marks) {
  await page.evaluate((marks) => {
    document.querySelectorAll('.guide-mark').forEach((n) => n.remove());
    for (const m of marks) {
      const el = document.querySelectorAll(m.sel)[m.nth || 0];
      if (!el) { console.warn('annotate: no element for', m.sel); continue; }
      const r = el.getBoundingClientRect();
      const sx = window.scrollX, sy = window.scrollY;
      if (m.box !== false) {
        const box = document.createElement('div');
        box.className = 'guide-mark';
        Object.assign(box.style, {
          position: 'absolute', left: (r.left + sx - 4) + 'px', top: (r.top + sy - 4) + 'px',
          width: (r.width + 8) + 'px', height: (r.height + 8) + 'px',
          border: '3px solid #e5372b', borderRadius: '8px', zIndex: 99998, pointerEvents: 'none',
          boxSizing: 'border-box',
        });
        document.body.appendChild(box);
      }
      const d = 30;
      const pos = m.pos || 'left';
      let x, y;
      if (pos === 'left') { x = r.left - d - 10; y = r.top + r.height / 2 - d / 2; }
      if (pos === 'right') { x = r.right + 10; y = r.top + r.height / 2 - d / 2; }
      if (pos === 'top') { x = r.left + r.width / 2 - d / 2; y = r.top - d - 10; }
      if (pos === 'bottom') { x = r.left + r.width / 2 - d / 2; y = r.bottom + 10; }
      if (pos === 'inside-left') { x = r.left + 6; y = r.top + r.height / 2 - d / 2; }
      if (pos === 'topleft') { x = r.left - d / 2 - 4; y = r.top - d / 2 - 4; }
      x += m.dx || 0; y += m.dy || 0;
      x = Math.max(2, Math.min(x, document.documentElement.clientWidth - d - 2));
      y = Math.max(2, y);
      const dot = document.createElement('div');
      dot.className = 'guide-mark';
      dot.textContent = String(m.n);
      Object.assign(dot.style, {
        position: 'absolute', left: (x + sx) + 'px', top: (y + sy) + 'px', width: d + 'px', height: d + 'px',
        borderRadius: '50%', background: '#e5372b', color: '#fff', font: '700 16px/30px system-ui, sans-serif',
        textAlign: 'center', zIndex: 99999, boxShadow: '0 0 0 2px #fff, 0 2px 6px rgba(0,0,0,.5)',
        pointerEvents: 'none',
      });
      document.body.appendChild(dot);
    }
  }, marks);
}

// Screenshot one element with some breathing room, so callouts placed just
// outside it stay in frame.
async function shotOf(page, sel, file, pad = 44) {
  // The start/stop bar is fixed to the viewport, so a full-page crop would
  // paint it across whatever section happens to be under it.
  await page.addStyleTag({ content: '.bannerbar{display:none!important}' });
  const box = await page.locator(sel).first().boundingBox();
  if (!box) throw new Error('no box for ' + sel);
  const scroll = await page.evaluate(() => ({ x: window.scrollX, y: window.scrollY }));
  const vw = page.viewportSize().width;
  const clip = {
    x: Math.max(0, box.x - pad),
    y: Math.max(0, box.y + scroll.y - pad),
    width: Math.min(vw, box.width + pad * 2),
    height: box.height + pad * 2,
  };
  clip.width = Math.min(clip.width, vw - clip.x);
  await page.screenshot({ path: join(OUT, file), clip, fullPage: true });
  console.log('wrote', file);
}

async function shot(page, file, opts = {}) {
  await page.screenshot({ path: join(OUT, file), ...opts });
  console.log('wrote', file);
}

function want(name) { return !ONLY.length || ONLY.includes(name); }

/* ---------- admin session with optional test data ---------- */

async function adminPage(browser, { status = null, width = 1100, height = 900 } = {}) {
  const ctx = await browser.newContext({
    ignoreHTTPSErrors: true, viewport: { width, height }, deviceScaleFactor: 2,
    colorScheme: 'dark', locale: 'en-US', timezoneId: 'America/Chicago',
  });
  const page = await ctx.newPage();

  // Viewer links: the example address instead of the container's own.
  // Devices: the container has no sound card, so offer two plausible ones.
  // Status: when given, replaces the real (stopped) status everywhere.
  await page.route('**/api/admin/**', async (route) => {
    if (route.request().url().includes('stream')) return route.continue();   // endless; cannot be buffered
    const res = await route.fetch();
    const type = res.headers()['content-type'] || '';
    if (!type.includes('json')) return route.fulfill({ response: res });
    await route.fulfill({ response: res, body: scrub(await res.text()) });
  });
  await page.route('**/api/admin/state', async (route) => {
    const res = await route.fetch();
    const body = JSON.parse(scrub(await res.text()));
    body.urls = [EXAMPLE];
    body.devices = DEVICES;
    body.config.audio_device = 'USB Audio CODEC';
    body.config.input_channel = 'mix';
    if (status) body.status = { ...body.status, ...status(body.status) };
    await route.fulfill({ response: res, json: body });
  });
  await page.route('**/api/admin/status/stream', async (route) => {
    if (!status) return route.continue();
    // A one-shot stream: EventSource re-opens it every few seconds, which
    // is enough to keep the panel painted with the same test status.
    const real = JSON.parse(scrub(await (await route.fetch({ url: ADMIN + '/api/admin/status' })).text()));
    const st = { ...real, ...status(real) };
    await route.fulfill({
      status: 200, headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
      body: 'retry: 3000\ndata: ' + JSON.stringify(st) + '\n\n',
    });
  });

  await page.goto(ADMIN + '/admin');
  if (await page.locator('input[name=token]').count()) {          // first run: create the Owner
    await page.fill('input[name=token]', TOKEN || '');
    await page.fill('input[name=username]', USER);
    await page.fill('input[name=name]', USER);
    await page.fill('input[name=password]', PASSWORD);
    await page.fill('input[name=confirm]', PASSWORD);
    await page.click('button[type=submit]');
    await page.waitForURL('**/admin');
  } else if (await page.locator('input[name=username]').count()) { // sign in
    await page.fill('input[name=username]', USER);
    await page.fill('input[name=password]', PASSWORD);
    await page.click('button[type=submit]');
    await page.waitForURL('**/admin');
  }
  await page.waitForSelector('#urls .urlgroup', { state: 'attached' });
  await page.waitForTimeout(800);
  return { ctx, page };
}

// The operator panel shows one page at a time, picked in the sidebar (#health, #panels, ...).
async function openPage(page, name) {
  await page.evaluate((n) => { location.hash = '#' + n; }, name);
  await page.waitForSelector('#page-' + name + ':not([hidden])');
  await page.waitForTimeout(400);
}

const RUNNING = (real) => ({
  running: true, started_at: now() - 1520, error: null, viewers: 37,
  audio: audio(),
  sessions: [session('SPANISH'), session('FRENCH', { last_delta_ts: now() - 1.2 })],
  targets: real.targets.map((t) => ({ ...t, live: t.enabled })),
});

/* ---------- viewer pages ---------- */

async function viewerShots(browser) {
  const desk = await browser.newContext({
    viewport: { width: 1280, height: 760 }, deviceScaleFactor: 2, colorScheme: 'dark', locale: 'en-US',
  });
  const phone = await browser.newContext({
    viewport: { width: 390, height: 780 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
    colorScheme: 'dark', locale: 'en-US',
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1',
  });
  const p = await desk.newPage();

  if (want('viewer-chooser')) {
    await p.goto(VIEW + '/');
    await annotate(p, [
      { sel: '.choice[href="/translation"]', n: 1 },
      { sel: '.choice[href="/transcription"]', n: 2 },
      { sel: '.choice[href="/both"]', n: 3 },
    ]);
    await shot(p, 'viewer-chooser.png');
  }

  const captions = async (page) => {
    await page.waitForFunction(() => document.querySelectorAll('.log > *').length >= 3, null, { timeout: 60000 });
    await page.waitForTimeout(1500);
  };

  if (want('viewer-transcription')) {
    await p.goto(VIEW + '/transcription');
    await captions(p);
    await annotate(p, [
      { sel: '#statusDot', n: 1, pos: 'bottom', box: false },
      { sel: '.bar a[href="/transcription"]', n: 2, pos: 'bottom' },
      { sel: '#themeToggle', n: 3, pos: 'bottom' },
      { sel: '#wakeToggle', n: 4, pos: 'bottom' },
      { sel: '#presentBtn', n: 5, pos: 'bottom' },
      { sel: 'button[data-font="down"]', n: 6, pos: 'bottom', dx: 20 },
      { sel: '.feed .log > *:last-child', n: 7, pos: 'left', box: false },
    ]);
    await shot(p, 'viewer-transcription.png');
  }

  if (want('viewer-translation')) {
    await p.goto(VIEW + '/translation');
    await captions(p);
    await annotate(p, [
      { sel: '.bar a[href="/translation"]', n: 1, pos: 'bottom' },
      { sel: '#langPicker', n: 2, pos: 'bottom' },
    ]);
    await shot(p, 'viewer-translation.png');
  }

  if (want('viewer-both')) {
    await p.goto(VIEW + '/both');
    await captions(p);
    await annotate(p, [
      { sel: '.pane:nth-child(1) .pane-label', n: 1, pos: 'left' },
      { sel: '.pane:nth-child(2) .pane-label', n: 2, pos: 'left' },
      { sel: '#langPicker', n: 3, pos: 'bottom' },
    ]);
    await shot(p, 'viewer-both.png');
  }

  if (want('viewer-present')) {
    await p.goto(VIEW + '/present');
    await captions(p);
    await shot(p, 'viewer-present.png');
  }

  const m = await phone.newPage();
  if (want('viewer-phone-chooser')) {
    await m.goto(VIEW + '/');
    await shot(m, 'viewer-phone-chooser.png');
  }
  if (want('viewer-phone-translation')) {
    await m.goto(VIEW + '/translation');
    await captions(m);
    await annotate(m, [
      { sel: '#langPicker', n: 1, pos: 'bottom' },
      { sel: '#wakeToggle:not([hidden])', n: 2, pos: 'bottom' },
      { sel: '#presentBtn', n: 3, pos: 'bottom' },
    ]);
    await shot(m, 'viewer-phone-translation.png');
  }
  if (want('viewer-phone-both')) {
    await m.goto(VIEW + '/both');
    await captions(m);
    await shot(m, 'viewer-phone-both.png');
  }
  await desk.close();
  await phone.close();
}

/* ---------- operator side ---------- */

async function signinShot(browser) {
  if (!want('operator-signin') && !want('operator-signin-error')) return;
  const ctx = await browser.newContext({
    ignoreHTTPSErrors: true, viewport: { width: 1000, height: 640 }, deviceScaleFactor: 2, colorScheme: 'dark',
  });
  const p = await ctx.newPage();
  await p.goto(ADMIN + '/admin');
  if (want('operator-signin')) {
    await annotate(p, [
      { sel: 'input[name=username]', n: 1 },
      { sel: 'input[name=password]', n: 2 },
      { sel: 'button[type=submit]', n: 3 },
    ]);
    await shotOf(p, 'form', 'operator-signin.png', 70);
  }
  if (want('operator-signin-error')) {
    await p.evaluate(() => document.querySelectorAll('.guide-mark').forEach((n) => n.remove()));
    await p.fill('input[name=username]', 'sam');
    await p.fill('input[name=password]', 'not-the-password');
    await p.click('button[type=submit]');
    await p.waitForSelector('.err');
    await annotate(p, [{ sel: '.err', n: 1 }]);
    await shotOf(p, 'form', 'operator-signin-error.png', 70);
  }
  await ctx.close();
}

// The browser's own certificate interstitial. Headless Chromium does not
// paint it, so this one runs a visible window for a moment.
async function certShot() {
  if (!want('cert-warning-chrome')) return;
  const b = await chromium.launch({ headless: false });
  const ctx = await b.newContext({ viewport: { width: 1100, height: 720 }, deviceScaleFactor: 2, locale: 'en-US' });
  const p = await ctx.newPage();
  await p.goto(ADMIN + '/admin').catch(() => {});
  await p.waitForTimeout(1500);
  const adv = p.locator('#details-button');
  if (await adv.count()) {
    await adv.click();
    await p.waitForTimeout(400);
    await annotate(p, [
      { sel: '#details-button', n: 1, pos: 'left' },
      { sel: '#proceed-link', n: 2, pos: 'right' },
    ]);
    await shot(p, 'cert-warning-chrome.png');
  } else {
    console.warn('certificate interstitial not shown; skipped cert-warning-chrome.png');
  }
  await b.close();
}

async function adminShots(browser) {
  // Stopped: the panel exactly as it looks after signing in.
  {
    // Clear any error left from an earlier Start in the soundless container.
    const clean = (real) => ({ error: null, audio: { ...real.audio, error: null } });
    const { ctx, page } = await adminPage(browser, { status: clean, height: 860 });
    if (want('admin-overview')) {
      await openPage(page, 'health');
      await annotate(page, [
        { sel: '#streamPill', n: 1, pos: 'right' },
        { sel: '#nav .grp:nth-of-type(1)', n: 2, pos: 'right', dx: -90, box: false },
        { sel: '#nav .grp:nth-of-type(2)', n: 3, pos: 'right', dx: -90, box: false },
        { sel: '#nav .grp:nth-of-type(3)', n: 4, pos: 'right', dx: -90, box: false },
        { sel: '#nav .grp:nth-of-type(4)', n: 5, pos: 'right', dx: -90, box: false },
        { sel: '#startBtn', n: 6, pos: 'top' },
        { sel: '#viewerCount', n: 7, pos: 'left' },
      ]);
      await shot(page, 'admin-overview.png');
    }
    await page.evaluate(() => { try { localStorage.clear(); } catch (e) {} });
    if (want('admin-targets-stopped')) {
      await page.reload();
      await openPage(page, 'targets');
      await page.waitForSelector('#targets label');
      await annotate(page, [
        { sel: '#xmode', n: 1, pos: 'top' },
        { sel: '#targetsAll', n: 2, pos: 'top' },
        { sel: '#targetsCount', n: 3, pos: 'right' },
        { sel: '#targets label:has(#tstate-SPANISH) input', n: 4, pos: 'left' },
        { sel: '#tstate-SPANISH', n: 5, pos: 'right' },
      ]);
      await shotOf(page, '#page-targets', 'admin-targets-stopped.png');
    }
    if (want('admin-schedules')) {
      await page.reload();
      await openPage(page, 'schedules');
      await page.waitForSelector('#schedList .schedname');
      await annotate(page, [
        { sel: '#schedStatus', n: 1, pos: 'right', dx: -40 },
        { sel: '#schedList tr:nth-child(1) .schedname', n: 2, pos: 'left' },
        { sel: '#schedList tr:nth-child(1) td:nth-child(2)', n: 3, pos: 'top' },
        { sel: '#schedList tr:nth-child(1) .switch', n: 4, pos: 'top' },
        { sel: '#schedList tr:nth-child(1) .schedactions', n: 5, pos: 'right' },
        { sel: '#schedAdd', n: 6, pos: 'right' },
      ]);
      await shotOf(page, '#page-schedules', 'admin-schedules.png');
    }
    if (want('admin-schedule-form')) {
      await page.reload();
      await openPage(page, 'schedules');
      await page.waitForSelector('#schedList .schedname');
      await page.click('#schedAdd');
      await page.fill('#schedName', 'Sunday evening');
      await page.check('#schedDays input[value="0"]').catch(() => {});
      await page.evaluate(() => {
        // Pick 5:00 PM - 7:00 PM through the pickers' own selects.
        const set = (box, h, m, ap) => {
          const s = document.querySelectorAll('#' + box + ' select');
          s[0].value = h; s[1].value = m; s[2].value = ap;
          s.forEach((x) => x.dispatchEvent(new Event('change')));
        };
        set('schedStart', '5', '00', 'PM');
        set('schedStop', '7', '00', 'PM');
        const tz = document.getElementById('schedTz'); tz.value = 'America/Chicago';
      });
      await annotate(page, [
        { sel: '#schedName', n: 1, pos: 'top' },
        { sel: '#schedRepeat', n: 2, pos: 'top' },
        { sel: '#schedDays', n: 3, pos: 'left' },
        { sel: '#schedStartDateField .datefield', n: 4, pos: 'left' },
        { sel: '#schedStart', n: 5, pos: 'left' },
        { sel: '#schedTz', n: 6, pos: 'top' },
        { sel: '#schedSave', n: 7, pos: 'left' },
      ]);
      await shotOf(page, '#schedForm', 'admin-schedule-form.png', 50);
    }
    if (want('admin-viewer-links')) {
      await page.reload();
      await openPage(page, 'urls');
      await page.waitForSelector('#urls .urlgroup', { state: 'attached' });
      await annotate(page, [
        { sel: '#urls .urlheading', n: 1, pos: 'left', box: false },
        { sel: '#urls .urlgroup .urllabel', n: 2, pos: 'left' },
        { sel: '#urls .urlgroup a', n: 3, pos: 'top' },
        { sel: '#urls .urlgroup .urlcopy', n: 4, pos: 'right' },
        { sel: '#urls .urlgroup .urlqr', n: 5, pos: 'right' },
        { sel: '#urls .urlgroup .urllabel', nth: 4, n: 6, pos: 'left' },
        { sel: '#urls .urlgroup .urllabel', nth: 5, n: 7, pos: 'left' },
      ]);
      await shotOf(page, '#page-urls', 'admin-viewer-links.png');
    }
    if (want('admin-blocked-words')) {
      await page.reload();
      await openPage(page, 'blocklist');
      await annotate(page, [
        { sel: '#blocklistPath', n: 1, pos: 'top' },
        { sel: '#blocklist', n: 2, pos: 'left' },
        { sel: '#saveBlocklist', n: 3, pos: 'left' },
        { sel: '#blocklistCount', n: 4, pos: 'right' },
      ]);
      await shotOf(page, '#page-blocklist', 'admin-blocked-words.png');
    }
    if (want('admin-recordings')) {
      await page.reload();
      await openPage(page, 'recordings');
      await page.waitForSelector('#recRuns tr td strong');
      await annotate(page, [
        { sel: '#page-recordings .switch', n: 1, pos: 'left' },
        { sel: '#recKeep', n: 2, pos: 'top' },
        { sel: '#saveRecording', n: 3, pos: 'left' },
        { sel: '#recRuns tr:nth-child(1) td:nth-child(1)', n: 4, pos: 'left' },
        { sel: '#recRuns tr:nth-child(1) td:nth-child(4)', n: 5, pos: 'top' },
        { sel: '#recRuns tr:nth-child(1) button.danger', n: 6, pos: 'right' },
      ]);
      await shotOf(page, '#page-recordings', 'admin-recordings.png');
    }
    if (want('admin-credentials')) {
      await page.reload();
      await openPage(page, 'credentials');
      await annotate(page, [
        { sel: '#keyState', n: 1, pos: 'top' },
        { sel: '#apikey', n: 2, pos: 'bottom', dx: 180 },
        { sel: '#saveCreds', n: 3, pos: 'left' },
      ]);
      await shotOf(page, '#page-credentials', 'admin-credentials.png');
    }
    if (want('admin-appearance')) {
      await page.reload();
      await openPage(page, 'appearance');
      await annotate(page, [
        { sel: 'label[for=font]', n: 1, pos: 'right' },
        { sel: 'label[for=history]', n: 2, pos: 'right' },
        { sel: 'label[for=segIdle]', n: 3, pos: 'right' },
        { sel: 'label[for=segMaxIdle]', n: 4, pos: 'right' },
        { sel: '#saveAppearance', n: 5, pos: 'left' },
      ]);
      await shotOf(page, '#page-appearance', 'admin-appearance.png');
    }
    await ctx.close();
  }

  // Panels and alerts arrive over the live status stream, so these pages use the real one.
  {
    const { ctx, page } = await adminPage(browser, { height: 860 });
    if (want('admin-panels')) {
      await page.reload();
      await openPage(page, 'panels');
      await page.waitForSelector('#panelList .plink');
      await annotate(page, [
        { sel: '#page-panels .flashcta', n: 1, pos: 'top', dx: -300 },
        { sel: '#panelList .prow:nth-child(2) .plink', n: 2, pos: 'left' },
        { sel: '#panelList .prow:nth-child(2) .pshow', n: 3, pos: 'top' },
        { sel: '#panelList .prow:nth-child(2) .pfw', n: 4, pos: 'top' },
        { sel: '#panelList .prow:nth-child(2) .pstat', n: 5, pos: 'top' },
        { sel: '#fwBox', n: 6, pos: 'left' },
      ]);
      await shotOf(page, '#page-panels', 'admin-panels.png');
    }
    if (want('admin-panel-settings')) {
      await page.reload();
      await openPage(page, 'panels');
      await page.waitForSelector('#panelList .plink');
      await page.click('#panelList .prow:nth-child(2) .plink');
      await page.waitForSelector('#drawer:not([hidden])');
      await page.waitForTimeout(500);
      await page.setViewportSize({ width: 1280, height: 1200 });
      await page.waitForTimeout(300);
      await annotate(page, [
        { sel: '#drawer input[type=text]', n: 1, pos: 'left' },
        { sel: '#drawer select', n: 2, pos: 'left' },
        { sel: '#drawer .radios', nth: 1, n: 3, pos: 'left' },
        { sel: '#devInfo', n: 4, pos: 'left' },
        { sel: '#dFoot button', n: 5, pos: 'top' },
      ]);
      await shotOf(page, '#drawer', 'admin-panel-settings.png', 30);
      await page.setViewportSize({ width: 1280, height: 860 });
    }
    if (want('admin-alerts')) {
      await page.reload();
      await openPage(page, 'alerts');
      await page.waitForSelector('#alertList > *');
      await annotate(page, [
        { sel: '#alertList > *:nth-child(1)', n: 1, pos: 'left' },
        { sel: '#alertAdd', n: 2, pos: 'right' },
        { sel: '#alertAutoStop', n: 3, pos: 'right' },
        { sel: '#alertSave', n: 4, pos: 'right' },
        { sel: '#alertLinks', n: 5, pos: 'left' },
      ]);
      await shotOf(page, '#page-alerts', 'admin-alerts.png');
    }
    if (want('admin-planning-center')) {
      await page.reload();
      await openPage(page, 'planningcenter');
      await shotOf(page, '#page-planningcenter', 'admin-planning-center.png');
    }
    await ctx.close();
  }

  // The USB flasher is its own page.
  if (want('admin-flash')) {
    const { ctx, page } = await adminPage(browser, { height: 900 });
    await page.goto(ADMIN + '/admin/flash');
    await page.waitForTimeout(800);
    await shot(page, 'admin-flash.png', { fullPage: true });
    await ctx.close();
  }

  // Running: test data, since rehearsal mode never opens a real session.
  {
    const { ctx, page } = await adminPage(browser, { status: RUNNING });
    if (want('admin-running')) {
      await openPage(page, 'health');
      await annotate(page, [
        { sel: '#streamPill', n: 1, pos: 'right' },
        { sel: '#viewerCount', n: 2, pos: 'left' },
        { sel: '#sessions', n: 3, pos: 'top' },
        { sel: '#masterState', n: 4, pos: 'top' },
      ]);
      await shot(page, 'admin-running.png');
    }
    if (want('admin-audio-input')) {
      await page.reload();
      await openPage(page, 'audio');
      await page.waitForTimeout(800);
      await annotate(page, [
        { sel: '#device', n: 1, pos: 'top' },
        { sel: '#channel', n: 2, pos: 'top' },
        { sel: '#rescan', n: 3, pos: 'top' },
        { sel: '#noise', n: 4, pos: 'right', dx: -44 },
        { sel: '.pill', n: 5, pos: 'right' },
        { sel: '#meter', n: 6, pos: 'left' },
        { sel: '#clipLed', n: 7, pos: 'top' },
        { sel: '#meterRead', n: 8, pos: 'bottom' },
      ]);
      await shotOf(page, '#page-audio', 'admin-audio-input.png');
    }
    if (want('admin-session-health')) {
      await page.reload();
      await openPage(page, 'health');
      await page.waitForSelector('#sessions td');
      await page.waitForTimeout(800);
      await annotate(page, [
        { sel: '#sessions tr:nth-child(1) td:nth-child(1)', n: 1, pos: 'top' },
        { sel: '#sessions tr:nth-child(1) td:nth-child(2)', n: 2, pos: 'top' },
        { sel: '#sessions tr:nth-child(1) td:nth-child(3)', n: 3, pos: 'top' },
        { sel: '#sessions tr:nth-child(1) td:nth-child(4)', n: 4, pos: 'top' },
        { sel: '#sessions tr:nth-child(1) td:nth-child(5)', n: 5, pos: 'top' },
      ]);
      await shotOf(page, '#page-health', 'admin-session-health.png');
    }
    if (want('admin-targets-live')) {
      await page.reload();
      await openPage(page, 'targets');
      await page.waitForSelector('#targets label');
      await page.waitForTimeout(800);
      await annotate(page, [{ sel: '#tstate-SPANISH', n: 1, pos: 'right' }, { sel: '#tstate-FRENCH', n: 2, pos: 'right' }]);
      await shotOf(page, '#page-targets', 'admin-targets-live.png');
    }
    if (want('admin-stream-control')) {
      await page.reload();
      await openPage(page, 'health');
      await annotate(page, [
        { sel: '#startBtn', n: 1, pos: 'top' },
        { sel: '#stopBtn', n: 2, pos: 'top' },
        { sel: '#masterState', n: 3, pos: 'top' },
      ]);
      await shotOf(page, '#page-health > section:first-of-type', 'admin-stream-control.png', 50);
    }
    await ctx.close();
  }

  // Trouble: what the operator sees when something is wrong.
  if (want('admin-session-health-problem')) {
    const { ctx, page } = await adminPage(browser, {
      status: (real) => ({
        ...RUNNING(real),
        sessions: [
          session('SPANISH', { state: 'reconnecting', reconnects: 3, last_delta_ts: now() - 14 }),
          session('FRENCH', {
            state: 'error', fatal: true, last_delta_ts: null, reconnects: 0,
            error: 'Incorrect API key provided.',
          }),
        ],
      }),
    });
    await openPage(page, 'health');
    await page.waitForSelector('#sessions td');
    await annotate(page, [
      { sel: '#sessions tr:nth-child(1) td:nth-child(2)', n: 1, pos: 'top' },
      { sel: '#sessions tr:nth-child(2) td:nth-child(2)', n: 2, pos: 'bottom' },
    ]);
    await shotOf(page, '#page-health', 'admin-session-health-problem.png', 50);
    await ctx.close();
  }

  if (want('admin-audio-clipping')) {
    const { ctx, page } = await adminPage(browser, {
      status: (real) => ({
        ...RUNNING(real),
        audio: audio({ rms_dbfs: -4.2, peak_dbfs: 0, clipping: true, clipped_samples: 18342 }),
      }),
    });
    await openPage(page, 'audio');
    await page.waitForTimeout(800);
    await annotate(page, [
      { sel: '#meter', n: 1, pos: 'left' },
      { sel: '#clipLed', n: 2, pos: 'top' },
      { sel: '#meterRead', n: 3, pos: 'bottom' },
    ]);
    await shotOf(page, '.meterwrap', 'admin-audio-clipping.png', 50);
    await ctx.close();
  }

  if (want('admin-no-device')) {
    // The real error from the container, which has no sound card.
    const { ctx, page } = await adminPage(browser);
    await openPage(page, 'health');
    await page.evaluate(() => document.getElementById('startBtn').click());
    await page.waitForSelector('#alert:not([hidden])', { timeout: 8000 }).catch(() => {});
    await annotate(page, [{ sel: '#alert', n: 1, pos: 'left' }]);
    await shotOf(page, '#alert', 'admin-start-error.png', 50);
    await ctx.close();
  }
}

/* ---------- alerts: the sender page and a screen that flashes ---------- */

async function alertShots(browser) {
  if (!want('alert-sender') && !want('alert-screen')) return;
  const admin = await apiContext();
  const saved = await (await admin.get('/api/admin/alerts')).json();
  const list = (saved.alerts && saved.alerts.buttons) || saved.buttons || [];
  const booth = list.find((b) => b.name === ALERT_BUTTONS[0].name);
  if (!booth) { console.warn('alert buttons not seeded; skipped alert shots'); await admin.dispose(); return; }
  const view = await request.newContext({ baseURL: VIEW });

  if (want('alert-sender')) {
    const phone = await browser.newContext({
      viewport: { width: 390, height: 780 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
      colorScheme: 'dark', locale: 'en-US',
      userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1',
    });
    await phone.addInitScript(() => { try { localStorage.setItem('relay.alert.from', 'Drums'); } catch (e) {} });
    const m = await phone.newPage();
    await m.goto(VIEW + '/alert');
    await m.waitForSelector('#buttons > *');
    await m.waitForTimeout(600);
    await annotate(m, [
      { sel: '#sendingAs', n: 1, pos: 'right', dx: -60 },
      { sel: '#buttons > *:nth-child(1)', n: 2, pos: 'right', dx: -50 },
      { sel: '#statusDot', n: 3, pos: 'left' },
    ]);
    await shot(m, 'alert-sender.png');
    await phone.close();
  }

  if (want('alert-screen')) {
    // A TV opened as /screen?alert=Stage%20TV, flashing for the Front of house button. The flash alternates
    // red and white once a second, so wait for a red frame.
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1,
      colorScheme: 'dark', locale: 'en-US' });
    const p = await ctx.newPage();
    await p.goto(VIEW + '/screen?alert=Stage%20TV');
    await p.waitForTimeout(800);
    const sent = await view.post('/api/alerts/' + booth.id + '/send', { data: { from: 'Drums' } });
    if (!sent.ok()) console.warn('alert send -> ' + sent.status());
    await p.waitForSelector('#alertFlash:not([hidden])', { timeout: 5000 }).catch(() => {});
    for (let i = 0; i < 20; i++) {
      const bg = await p.evaluate(() => getComputedStyle(document.getElementById('alertFlash')).backgroundColor);
      const m = bg.match(/\d+/g) || [];
      if (m.length >= 3 && Number(m[0]) > 150 && Number(m[1]) < 80) break;
      await p.waitForTimeout(150);
    }
    await shot(p, 'alert-screen.png');
    await view.post('/api/alerts/' + booth.id + '/cancel', { data: { from: 'Drums' } });
    await ctx.close();
  }
  await view.dispose();
  await admin.dispose();
}

/* ---------- main ---------- */

await seed();
const browser = await chromium.launch();
try {
  await viewerShots(browser);
  await signinShot(browser);
  await adminShots(browser);
  await alertShots(browser);
} finally {
  await browser.close();
}
await certShot();
// Leave the instance stopped: nothing here should keep a session open.
const api = await apiContext();
await api.post('/api/admin/stop');
await api.dispose();
