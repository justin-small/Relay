// Captures every Relay screenshot in the user guide, annotated.
//
// Run it against a rehearsal instance (RELAY_DEMO=1) with throwaway
// credentials -- see ../README.md for the exact commands. Nothing here talks
// to OpenAI, and nothing it captures shows a real key, token or address:
//
//  - the viewer links are rewritten to the example address 192.168.1.50;
//  - the "running" panel shots are fed test data through request
//    interception, because rehearsal mode never opens a real session;
//  - the key hint shows the last four characters of the throwaway key.
//
//   RELAY_GUIDE_TOKEN=... RELAY_GUIDE_STATE=/path/to/docker-config node capture.mjs [name ...]

import { chromium, request } from 'playwright';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(HERE, '..', 'images');
const VIEW = process.env.RELAY_VIEW_URL || 'http://localhost:18080';
const ADMIN = process.env.RELAY_ADMIN_URL || 'https://localhost:18443';
const TOKEN = process.env.RELAY_GUIDE_TOKEN;
const STATE = process.env.RELAY_GUIDE_STATE; // the instance's docker-config/, for test recordings
const EXAMPLE = 'http://192.168.1.50/';
const ONLY = process.argv.slice(2);

if (!TOKEN) {
  console.error('Set RELAY_GUIDE_TOKEN to the rehearsal instance\'s admin token.');
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

async function seed() {
  const api = await request.newContext({
    baseURL: ADMIN, ignoreHTTPSErrors: true, extraHTTPHeaders: { 'x-admin-token': TOKEN },
  });
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
  await api.dispose();
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
  await page.route('**/api/admin/state', async (route) => {
    const res = await route.fetch();
    const body = await res.json();
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
    const real = await (await route.fetch({ url: ADMIN + '/api/admin/status' })).json();
    const st = { ...real, ...status(real) };
    await route.fulfill({
      status: 200, headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
      body: 'retry: 3000\ndata: ' + JSON.stringify(st) + '\n\n',
    });
  });

  await page.goto(ADMIN + '/admin');
  if (await page.locator('input[name=token]').count()) {
    await page.fill('input[name=token]', TOKEN);
    await page.click('button[type=submit]');
    await page.waitForURL('**/admin');
  }
  await page.waitForSelector('#urls .urlgroup', { state: 'attached' });
  await page.waitForTimeout(800);
  return { ctx, page };
}

async function openSections(page, ids, open = true) {
  await page.evaluate(({ ids, open }) => ids.forEach((id) => { document.getElementById(id).open = open; }), { ids, open });
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
      { sel: 'input[name=token]', n: 1 },
      { sel: 'button[type=submit]', n: 2 },
    ]);
    await shotOf(p, 'form', 'operator-signin.png', 70);
  }
  if (want('operator-signin-error')) {
    await p.evaluate(() => document.querySelectorAll('.guide-mark').forEach((n) => n.remove()));
    await p.fill('input[name=token]', 'not-the-token');
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
    const { ctx, page } = await adminPage(browser, { status: clean, height: 1000 });
    if (want('admin-overview')) {
      // Every section collapsed, so the whole panel fits on one page; each
      // section has its own close-up further on.
      await openSections(page, ['sec-targets', 'sec-schedules', 'sec-urls', 'sec-blocklist', 'sec-recordings',
        'sec-credentials', 'sec-appearance'], false);
      await page.addStyleTag({ content: '.wrap{padding-left:56px!important}' });
      const h = await page.evaluate(() => document.documentElement.scrollHeight);
      await page.setViewportSize({ width: page.viewportSize().width, height: h });
      await page.waitForTimeout(300);
      await annotate(page, [{ sel: 'header .dot', n: 1, pos: 'left' }, { sel: '#startBtn', n: 11, pos: 'top' }]
        .concat(['section:nth-of-type(1) h2', 'section:nth-of-type(2) h2', '#sec-targets summary',
          '#sec-schedules summary', '#sec-urls summary', '#sec-blocklist summary', '#sec-recordings summary',
          '#sec-credentials summary', '#sec-appearance summary'].map((sel, i) => ({ sel, n: i + 2, pos: 'left' })))
        .map((m) => ({ ...m, box: false })));
      await shot(page, 'admin-overview.png');
    }
    await page.setViewportSize({ width: 1100, height: 1000 });
    await page.evaluate(() => { try { localStorage.clear(); } catch (e) {} });
    if (want('admin-targets-stopped')) {
      await page.reload();
      await page.waitForSelector('#targets tr');
      await annotate(page, [
        { sel: '#targetsSum', n: 1, pos: 'right' },
        { sel: '#tstate-SPANISH', n: 2, pos: 'left' },
        { sel: '#targets tr:nth-child(1) .switch', n: 3, pos: 'right' },
      ]);
      await shotOf(page, '#sec-targets', 'admin-targets-stopped.png');
    }
    if (want('admin-schedules')) {
      await page.reload();
      await page.waitForSelector('#schedList .schedname');
      await annotate(page, [
        { sel: '#schedStatus', n: 1, pos: 'right', dx: -40 },
        { sel: '#schedList tr:nth-child(1) .schedname', n: 2, pos: 'left' },
        { sel: '#schedList tr:nth-child(1) td:nth-child(2)', n: 3, pos: 'top' },
        { sel: '#schedList tr:nth-child(1) .switch', n: 4, pos: 'top' },
        { sel: '#schedList tr:nth-child(1) .schedactions', n: 5, pos: 'right' },
        { sel: '#schedAdd', n: 6, pos: 'right' },
      ]);
      await shotOf(page, '#sec-schedules', 'admin-schedules.png');
    }
    if (want('admin-schedule-form')) {
      await page.reload();
      await page.waitForSelector('#schedList .schedname');
      await page.click('#schedAdd');
      await page.fill('#schedName', 'Sunday evening');
      await page.check('#schedDays input[value="0"]').catch(() => {});
      await page.evaluate(() => {
        // Pick 5:00 PM – 7:00 PM through the pickers' own selects.
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
      await shotOf(page, '#sec-urls', 'admin-viewer-links.png');
    }
    if (want('admin-blocked-words')) {
      await openSections(page, ['sec-blocklist']);
      await page.waitForTimeout(300);
      await annotate(page, [
        { sel: '#blocklistPath', n: 1, pos: 'top' },
        { sel: '#blocklist', n: 2, pos: 'left' },
        { sel: '#saveBlocklist', n: 3, pos: 'left' },
        { sel: '#blocklistCount', n: 4, pos: 'right' },
      ]);
      await shotOf(page, '#sec-blocklist', 'admin-blocked-words.png');
    }
    if (want('admin-recordings')) {
      await page.reload();
      await openSections(page, ['sec-recordings']);
      await page.waitForSelector('#recRuns tr td strong');
      await annotate(page, [
        { sel: '#sec-recordings .switch', n: 1, pos: 'left' },
        { sel: '#recKeep', n: 2, pos: 'top' },
        { sel: '#saveRecording', n: 3, pos: 'left' },
        { sel: '#recRuns tr:nth-child(1) td:nth-child(1)', n: 4, pos: 'left' },
        { sel: '#recRuns tr:nth-child(1) td:nth-child(4)', n: 5, pos: 'top' },
        { sel: '#recRuns tr:nth-child(1) button.danger', n: 6, pos: 'right' },
      ]);
      await shotOf(page, '#sec-recordings', 'admin-recordings.png');
    }
    if (want('admin-credentials')) {
      await page.reload();
      await openSections(page, ['sec-credentials']);
      await page.waitForTimeout(300);
      await annotate(page, [
        { sel: '#keyState', n: 1, pos: 'top' },
        { sel: '#apikey', n: 2, pos: 'bottom', dx: 180 },
        { sel: '#admintoken', n: 3, pos: 'bottom' },
        { sel: '#saveCreds', n: 4, pos: 'left' },
      ]);
      await shotOf(page, '#sec-credentials', 'admin-credentials.png');
    }
    if (want('admin-appearance')) {
      await page.reload();
      await openSections(page, ['sec-appearance']);
      await page.waitForTimeout(300);
      await annotate(page, [
        { sel: 'label[for=font]', n: 1, pos: 'right' },
        { sel: 'label[for=history]', n: 2, pos: 'right' },
        { sel: 'label[for=segIdle]', n: 3, pos: 'right' },
        { sel: 'label[for=segMaxIdle]', n: 4, pos: 'right' },
        { sel: '#saveAppearance', n: 5, pos: 'left' },
      ]);
      await shotOf(page, '#sec-appearance', 'admin-appearance.png');
    }
    await ctx.close();
  }

  // Running: test data, since rehearsal mode never opens a real session.
  {
    const { ctx, page } = await adminPage(browser, { status: RUNNING });
    if (want('admin-running')) {
      await openSections(page, ['sec-schedules', 'sec-urls'], false);
      await annotate(page, [
        { sel: 'header .dot', n: 1, pos: 'left', box: false },
        { sel: '#viewerCount', n: 2, pos: 'left' },
        { sel: '#sessions', n: 3, pos: 'right', dx: -44 },
        { sel: '.meterwrap', n: 4, pos: 'top' },
        { sel: '#targetsSum', n: 5, pos: 'right' },
        { sel: '#masterState', n: 6, pos: 'left' },
      ]);
      await shot(page, 'admin-running.png');
    }
    if (want('admin-audio-input')) {
      await page.reload();
      await page.waitForSelector('#urls .urlgroup', { state: 'attached' });
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
      await shotOf(page, 'section:nth-of-type(2)', 'admin-audio-input.png');
    }
    if (want('admin-session-health')) {
      await page.reload();
      await page.waitForSelector('#sessions td');
      await page.waitForTimeout(800);
      await annotate(page, [
        { sel: '#sessions tr:nth-child(1) td:nth-child(1)', n: 1, pos: 'top' },
        { sel: '#sessions tr:nth-child(1) td:nth-child(2)', n: 2, pos: 'top' },
        { sel: '#sessions tr:nth-child(1) td:nth-child(3)', n: 3, pos: 'top' },
        { sel: '#sessions tr:nth-child(1) td:nth-child(4)', n: 4, pos: 'top' },
        { sel: '#sessions tr:nth-child(1) td:nth-child(5)', n: 5, pos: 'top' },
      ]);
      await shotOf(page, 'section:nth-of-type(1)', 'admin-session-health.png');
    }
    if (want('admin-targets-live')) {
      await page.reload();
      await page.waitForSelector('#targets tr');
      await page.waitForTimeout(800);
      await shotOf(page, '#sec-targets', 'admin-targets-live.png');
    }
    if (want('admin-bottom-bar')) {
      await annotate(page, [
        { sel: '#startBtn', n: 1, pos: 'top' },
        { sel: '#stopBtn', n: 2, pos: 'top' },
        { sel: '#masterState', n: 3, pos: 'top' },
      ]);
      await page.screenshot({
        path: join(OUT, 'admin-bottom-bar.png'),
        clip: { x: 0, y: page.viewportSize().height - 130, width: page.viewportSize().width, height: 130 },
      });
      console.log('wrote admin-bottom-bar.png');
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
    await page.waitForSelector('#sessions td');
    await annotate(page, [
      { sel: '#sessions tr:nth-child(1) td:nth-child(2)', n: 1, pos: 'top' },
      { sel: '#sessions tr:nth-child(2) td:nth-child(2)', n: 2, pos: 'bottom' },
    ]);
    await shotOf(page, 'section:nth-of-type(1)', 'admin-session-health-problem.png', 50);
    await ctx.close();
  }

  if (want('admin-audio-clipping')) {
    const { ctx, page } = await adminPage(browser, {
      status: (real) => ({
        ...RUNNING(real),
        audio: audio({ rms_dbfs: -4.2, peak_dbfs: 0, clipping: true, clipped_samples: 18342 }),
      }),
    });
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
    await page.evaluate(() => document.getElementById('startBtn').click());
    await page.waitForSelector('#alert:not([hidden])', { timeout: 8000 }).catch(() => {});
    await annotate(page, [{ sel: '#alert', n: 1, pos: 'left' }]);
    await shotOf(page, '#alert', 'admin-start-error.png', 50);
    await ctx.close();
  }
}

/* ---------- main ---------- */

await seed();
const browser = await chromium.launch();
try {
  await viewerShots(browser);
  await signinShot(browser);
  await adminShots(browser);
} finally {
  await browser.close();
}
await certShot();
// Leave the instance stopped: nothing here should keep a session open.
const api = await request.newContext({ baseURL: ADMIN, ignoreHTTPSErrors: true, extraHTTPHeaders: { 'x-admin-token': TOKEN } });
await api.post('/api/admin/stop');
await api.dispose();
