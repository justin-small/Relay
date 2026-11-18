/* Blackmagic drawings (the rack redesign): each device's front and back drawn from a model spec, live from the page's
   state. A spec lists buttons, screens and connectors with their places; a button Relay can press carries an action,
   the rest are drawn dimmed and say why. Drawing ids match DRAWINGS in service.py.
   Loaded before blackmagic.js; exposes window.BMDraw. */
(function () {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const el = relayUI.el;
  function svgEl(tag, attrs, parent, text) {
    const e = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { if (attrs[k] !== null && attrs[k] !== undefined) e.setAttribute(k, attrs[k]); });
    if (text !== undefined && text !== null) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  }
  function btn(id, x, y, w, h, label, act, extra) { return Object.assign({ t: 'btn', id: id, x: x, y: y, w: w, h: h, label: label, act: act || null }, extra || {}); }
  function port(id, kind, x, y, label, dir, extra) { return Object.assign({ t: 'port', id: id, kind: kind, x: x, y: y, label: label, dir: dir || '' }, extra || {}); }
  function cap(x, y, label, anchor) { return { t: 'text', x: x, y: y, label: label, anchor: anchor || 'start', cls: 'hw-cap' }; }
  const NOT_AUDIO = 'Audio is mixed on the switcher; Relay does not change it.';

  // ------------------------------------------------------------------------------------------- ATEM
  function sourceRows(items, x0, step, w, yP, yV, srcs) {
    items.push(cap(x0 - 10, yP + 21, 'PROGRAM', 'end'), cap(x0 - 10, yV + 21, 'PREVIEW', 'end'));
    srcs.forEach(function (src, i) {
      items.push(btn('pgm' + src.n, x0 + i * step, yP, w, 32, src.k, 'pgm:' + src.n, { src: src.n }));
      items.push(btn('pvw' + src.n, x0 + i * step, yV, w, 32, src.k, 'pvw:' + src.n, { src: src.n, name: 1 }));
    });
  }
  function atemSources(v, count, extra) {
    const out = [];
    for (let n = 1; n <= count; n++) out.push({ n: n, k: String(n) });
    out.push({ n: 0, k: 'BLK' });
    if (v.inputs[extra.id] !== undefined) out.push({ n: extra.id, k: extra.k });
    return out;
  }
  function dskButtons(items, v, x, y1, y2) {
    if (v.dsk >= 1) items.push(btn('dsk1', x, y1, 46, 32, 'DSK 1', 'dsk:0', { tipText: 'Downstream key 1: take it on or off with its own transition' }));
    else items.push(btn('dsk1', x, y1, 46, 32, 'DSK', null, { why: 'This switcher has no downstream key.' }));
    if (v.dsk >= 2) items.push(btn('dsk2', x, y2, 46, 32, 'DSK 2', 'dsk:1', { tipText: 'Downstream key 2: take it on or off with its own transition' }));
  }
  function macroButtons(items, v, x0, y, step, w, h, count) {
    for (let m = 0; m < count; m++) {
      const mac = v.macros[m];
      items.push(btn('mac' + m, x0 + m * step, y, w, h, String(m + 1), mac ? 'macro:' + mac.slot : null,
        mac ? { tipText: 'Run macro ' + (mac.slot + 1) + ': ' + (mac.name || 'Macro') } : { why: 'No macro recorded in this slot.' }));
    }
  }
  function tvs(v, inputs, title) {
    const f = [], b = [];
    const n = Math.min(inputs, 12);
    const step = n > 10 ? 36 : 44, bw = step - 6;
    f.push({ t: 'lcd', x: 40, y: 22, w: 168, h: 98, key: 'atem' });
    const srcs = atemSources(v, n, { id: 3010, k: 'MP1' });
    sourceRows(f, 300, step, bw, 26, 70, srcs);
    const xr = 300 + srcs.length * step + 14;
    dskButtons(f, v, xr, 26, 70);
    f.push(btn('cut', xr + 62, 26, 56, 32, 'CUT', 'cut', { big: 1 }));
    f.push(btn('auto', xr + 62, 70, 56, 32, 'AUTO', 'auto', { big: 1 }));
    f.push(cap(290, 142, 'MACRO', 'end'));
    macroButtons(f, v, 300, 124, 44, 38, 28, 6);
    f.push(btn('ftb', 588, 124, 56, 28, 'FTB', 'ftb'));
    f.push(btn('rec', 656, 124, 46, 28, 'REC', null, { why: 'Recording is started on the switcher or in ATEM Software Control.' }));
    f.push(btn('stream', 712, 124, 60, 28, 'STREAM', null, { why: 'Streaming is started on the switcher.' }));
    f.push({ t: 'text', x: 40, y: 150, label: title, cls: 'hw-brand' });
    const bstep = n > 8 ? 34 : 50;
    for (let i = 1; i <= n; i++) b.push(port('in' + i, 'sdi', 40 + i * bstep, 56, 'IN ' + i, 'in', { input: i }));
    ['PGM', 'AUX 1', 'AUX 2', 'AUX 3', 'AUX 4'].forEach(function (l, k) { b.push(port('out' + k, 'sdi', 90 + k * 50, 128, l, 'out', { out: k })); });
    const xa = Math.max(490, 40 + (n + 1) * bstep);
    b.push(port('mv', 'hdmi', 380, 128, 'MULTIVIEW', 'out'));
    b.push(port('ref', 'sdi', xa, 56, 'REF IN', 'in'));
    b.push(port('xlr1', 'xlr', xa + 70, 56, 'AUDIO 1', 'in'));
    b.push(port('xlr2', 'xlr', xa + 120, 56, 'AUDIO 2', 'in'));
    b.push(port('tb', 'eth', 560, 128, 'TALKBACK', ''));
    b.push(port('eth', 'eth', Math.max(680, xa + 190), 56, 'ETHERNET', '', { net: 1 }));
    b.push(port('usb', 'usb', Math.max(740, xa + 250), 56, 'USB-C', ''));
    b.push(port('pwr', 'iec', 870, 88, 'AC 100–240V', '', { power: 1 }));
    b.push({ t: 'zone', x: 52, y: 22, w: xa - 72, h: 152, label: 'VIDEO' });
    return { form: 'rack', u: 2, front: { w: 960, h: 176, items: f }, back: { w: 960, h: 176, items: b } };
  }
  function mini(v, inputs, title) {
    const f = [], b = [];
    f.push({ t: 'zone', x: 24, y: 28, w: 236, h: 230, label: 'AUDIO' });
    const ch = ['M1', 'M2'];
    for (let i = 1; i <= inputs; i++) ch.push(String(i));
    ch.forEach(function (c, i) {
      const col = i % 5, row = Math.floor(i / 5);
      f.push(btn('a' + c, 40 + col * 42, 56 + row * 96, 34, 22, c, null, { small: 1, why: NOT_AUDIO }));
      f.push(btn('afv' + c, 40 + col * 42, 84 + row * 96, 34, 22, 'AFV', null, { small: 1, why: NOT_AUDIO }));
    });
    f.push({ t: 'lcd', x: 300, y: 28, w: 140, h: 62, key: 'atem' });
    sourceRows(f, 370, 40, 34, 112, 160, atemSources(v, inputs, { id: 6000, k: 'SS' }));
    f.push(cap(360, 236, 'TRANSITION', 'end'));
    ['MIX', 'DIP', 'WIPE', 'DVE', 'STING'].forEach(function (l, i) {
      f.push(btn('tr' + l, 370 + i * 46, 216, 40, 28, l, null, { small: 1, why: 'Relay uses the transition already chosen on the switcher.' }));
    });
    dskButtons(f, v, 790, 112, 160);
    f.push(btn('cut', 850, 112, 58, 32, 'CUT', 'cut', { big: 1 }));
    f.push(btn('auto', 850, 160, 58, 32, 'AUTO', 'auto', { big: 1 }));
    f.push(btn('ftb', 850, 216, 58, 28, 'FTB', 'ftb'));
    const mac = v.macros[0];
    f.push(btn('macro', 790, 216, 46, 28, 'MACRO', mac ? 'macro:' + mac.slot : null,
      mac ? { tipText: 'Run macro ' + (mac.slot + 1) + ': ' + (mac.name || 'Macro') } : { why: 'No macro recorded on the switcher.' }));
    f.push({ t: 'knob', x: 660, y: 58, r: 30, why: 'Moves the DVE; not from Relay.' });
    f.push(btn('rec', 720, 34, 56, 26, 'REC', null, { why: 'Recording is started on the switcher.' }));
    f.push(btn('onair', 786, 34, 56, 26, 'ON AIR', null, { why: 'Streaming is started on the switcher.' }));
    f.push(btn('stream', 852, 34, 56, 26, 'STREAM', null, { why: 'Streaming is started on the switcher.' }));
    f.push({ t: 'text', x: 24, y: 300, label: title, cls: 'hw-brand' });
    const step = inputs > 4 ? 50 : 60;
    for (let i = 1; i <= inputs; i++) b.push(port('in' + i, 'hdmi', 14 + i * step, 54, 'HDMI ' + i, 'in', { input: i }));
    b.push(port('out0', 'hdmi', 490, 54, 'OUT 1', 'out', { out: 0 }));
    if (inputs > 4) b.push(port('out1', 'hdmi', 545, 54, 'OUT 2', 'out', { out: 1 }));
    b.push(port('mic1', 'mini', 610, 54, 'MIC 1', 'in'));
    b.push(port('mic2', 'mini', 660, 54, 'MIC 2', 'in'));
    if (inputs > 4) b.push(port('hs', 'mini', 714, 54, 'HEADSET', ''));
    b.push(port('eth', 'eth', 776, 54, 'ETHERNET', '', { net: 1 }));
    b.push(port('usb1', 'usb', 834, 54, 'USB-C 1', ''));
    if (inputs > 4) b.push(port('usb2', 'usb', 882, 54, 'USB-C 2', ''));
    b.push(port('pwr', 'barrel', 930, 54, '12V', '', { power: 1 }));
    return { form: 'desk', front: { w: 960, h: 320, items: f }, back: { w: 960, h: 110, items: b } };
  }

  // ------------------------------------------------------------------------------------------- HyperDeck
  function deck(v) {
    const f = [], b = [];
    const elsewhere = 'Relay records, stops and plays; pick clips on the deck.';
    f.push({ t: 'lcd', x: 40, y: 12, w: 168, h: 64, key: 'deck' });
    f.push({ t: 'slot', x: 228, y: 18, w: 128, h: 14, label: 'SLOT 1', slot: 1 });
    f.push({ t: 'slot', x: 228, y: 48, w: 128, h: 14, label: 'SLOT 2', slot: 2 });
    f.push(btn('prev', 420, 26, 46, 34, '⏮', null, { why: elsewhere }));
    f.push(btn('rec', 476, 26, 46, 34, 'REC', 'record', { tone: 'rec' }));
    f.push(btn('stop', 532, 26, 46, 34, 'STOP', 'stop'));
    f.push(btn('play', 588, 26, 46, 34, 'PLAY', 'play'));
    f.push(btn('next', 644, 26, 46, 34, '⏭', null, { why: elsewhere }));
    f.push(btn('rem', 712, 26, 46, 34, 'REM', null, { why: v.remote ? 'Remote is on, so Relay can control the deck.' : 'Remote is off on the deck: Relay can look but not control it.', lit: v.remote }));
    f.push({ t: 'knob', x: 860, y: 44, r: 30, why: 'Jog and shuttle are on the deck.' });
    f.push({ t: 'text', x: 228, y: 80, label: v.product || 'HyperDeck', cls: 'hw-brand' });
    b.push(port('sdiin', 'sdi', 90, 44, 'SDI IN', 'in', { rec: 1 }));
    b.push(port('sdiout', 'sdi', 140, 44, 'SDI OUT', 'out'));
    b.push(port('hdmiin', 'hdmi', 210, 44, 'HDMI IN', 'in'));
    b.push(port('hdmiout', 'hdmi', 270, 44, 'HDMI OUT', 'out'));
    b.push(port('ref', 'sdi', 340, 44, 'REF IN', 'in'));
    b.push(port('tcin', 'xlr', 400, 44, 'TC IN', 'in'));
    b.push(port('tcout', 'xlr', 450, 44, 'TC OUT', 'out'));
    b.push(port('eth', 'eth', 560, 44, 'ETHERNET', '', { net: 1 }));
    b.push(port('usb', 'usb', 620, 44, 'USB-C', ''));
    b.push(port('pwr', 'iec', 860, 44, 'AC', '', { power: 1 }));
    return { form: 'rack', u: 1, front: { w: 960, h: 88, items: f }, back: { w: 960, h: 88, items: b } };
  }

  // ------------------------------------------------------------------------------------------- Videohub
  function hub(v) {
    const f = [], b = [];
    const nin = Math.max(1, v.inputs.length), nout = Math.max(1, v.outputs.length);
    const cols = nout > 12 ? 10 : 12, step = nout > 12 ? 44 : 38, x0 = 250;
    const rows = Math.ceil(nout / cols);
    const u = Math.max(rows > 2 ? Math.ceil((rows * 40 + 24) / 88) : 1, Math.max(nin, nout) > 12 ? 2 : 1);
    const H = 88 * u;
    f.push({ t: 'lcd', x: 40, y: 12, w: 160, h: H - 24, key: 'hub' });
    f.push(cap(x0 - 8, 32, 'OUT', 'end'));
    for (let i = 1; i <= nout; i++) {
      const row = Math.floor((i - 1) / cols), col = (i - 1) % cols;
      f.push(btn('dst' + i, x0 + col * step, 12 + row * 40, step - 6, 30, String(i), 'dst:' + i, { dst: i }));
    }
    const x1 = x0 + cols * step + 18;
    f.push(btn('take', x1, 12, 56, 30, 'TAKE', 'take', { big: 1 }));
    f.push(btn('lock', x1, 52, 56, 30, 'LOCK', 'lock', { manage: 1 }));
    f.push({ t: 'knob', x: Math.min(x1 + 110, 900), y: H / 2, r: 26, why: 'Choose the input in Relay: click it on the back, or pick it in the side panel.' });
    const bx0 = 70, bstep = Math.min(40, 760 / Math.max(nin, nout)), compact = bstep < 30;
    for (let k = 1; k <= nin; k++) b.push(port('in' + k, 'sdi', bx0 + (k - 1) * bstep, H * 0.3, String(k), 'in', { input: k, compact: 1, tiny: compact }));
    for (let k = 1; k <= nout; k++) b.push(port('out' + k, 'sdi', bx0 + (k - 1) * bstep, H * 0.74, String(k), 'out', { out: k, compact: 1, tiny: compact }));
    b.push(cap(40, H * 0.3 + 4, 'IN', 'end'), cap(40, H * 0.74 + 4, 'OUT', 'end'));
    b.push(port('ref', 'sdi', 860, H * 0.3, 'REF', 'in', { compact: 1 }));
    b.push(port('eth', 'eth', 860, H * 0.74, 'ETH', '', { compact: 1, net: 1 }));
    b.push(port('pwr', 'iec', 920, H / 2, 'AC', '', { compact: 1, power: 1 }));
    return { form: 'rack', u: u, front: { w: 960, h: H, items: f }, back: { w: 960, h: H, items: b } };
  }

  // ------------------------------------------------------------------------------------------- picking a drawing
  const NAMES = { 'tvs-hd8': 'ATEM Television Studio HD8', 'atem-rack': 'ATEM rack switcher', 'mini-extreme': 'ATEM Mini Extreme',
    'mini-pro': 'ATEM Mini / Mini Pro', 'hyperdeck-studio': 'HyperDeck Studio', videohub: 'Videohub' };
  function auto(dev) {
    const text = ((dev.product || '') + ' ' + (dev.model || '')).toLowerCase();
    if (dev.kind === 'hyperdeck') return 'hyperdeck-studio';
    if (dev.kind === 'videohub') return 'videohub';
    if (/television studio hd8|tvs hd8/.test(text)) return 'tvs-hd8';
    if (/mini extreme/.test(text)) return 'mini-extreme';
    if (/atem mini/.test(text)) return 'mini-pro';
    return 'atem-rack';
  }
  function drawingOf(dev) { return dev.drawing && NAMES[dev.drawing] ? dev.drawing : auto(dev); }

  /* What the drawing needs to know about one device, from the page's state (GET /api/admin/blackmagic). */
  function view(dev, meIndex) {
    const v = { id: dev.id, name: dev.name, kind: dev.kind, connected: !!dev.connected, product: dev.product || dev.model || '',
      notes: dev.notes || {}, drawing: drawingOf(dev), dev: dev };
    if (dev.kind === 'atem') {
      const me = (dev.mix_effects || [])[meIndex || 0] || {};
      v.inputs = {};
      (dev.inputs || []).forEach(function (i) { v.inputs[i.id] = i.name; });
      v.pgm = me.program; v.pvw = me.preview; v.me = me.me || 0;
      v.dsk = dev.downstream_keyers || 0;
      v.macros = dev.macros || [];
      v.external = (dev.inputs || []).filter(function (i) { return i.id >= 1 && i.id <= 40; }).length;
    } else if (dev.kind === 'hyperdeck') {
      const d = dev.hyperdeck || {};
      v.status = d.status || ''; v.clip = d.clip && d.clip.name ? d.clip.name : ''; v.tc = d.timecode || '';
      v.format = d.video_format || ''; v.remote = d.remote_enabled !== false; v.slots = d.slots || []; v.slot = d.slot;
    } else {
      const h = dev.videohub || {};
      v.inputs = (h.inputs || []).map(function (p) { return p.label || 'Input ' + (p.n + 1); });
      v.outputs = (h.outputs || []).map(function (p) { return p.label || 'Output ' + (p.n + 1); });
      v.routes = (h.outputs || []).map(function (p) { return p.input === null || p.input === undefined ? null : p.input + 1; });
      v.locks = {};
      (h.outputs || []).forEach(function (p) { if (p.lock && p.lock !== 'U') v.locks[p.n + 1] = p.lock; });
    }
    return v;
  }
  const cache = {};
  function spec(v) {
    const key = v.drawing + '|' + (v.kind === 'atem' ? v.external + '|' + v.dsk + '|' + v.macros.length + '|' + (v.inputs[3010] !== undefined) + (v.inputs[6000] !== undefined)
      : v.kind === 'videohub' ? v.inputs.length + 'x' + v.outputs.length : v.product);
    if (!cache[v.id] || cache[v.id].key !== key) {
      let s;
      if (v.drawing === 'tvs-hd8') s = tvs(v, 8, 'ATEM Television Studio HD8');
      else if (v.drawing === 'atem-rack') s = tvs(v, Math.max(4, Math.min(v.external || 8, 12)), v.product || 'ATEM switcher');
      else if (v.drawing === 'mini-extreme') s = mini(v, 8, 'ATEM Mini Extreme');
      else if (v.drawing === 'mini-pro') s = mini(v, 4, v.product || 'ATEM Mini');
      else if (v.drawing === 'hyperdeck-studio') s = deck(v);
      else s = hub(v);
      cache[v.id] = { key: key, spec: s };
    }
    return cache[v.id].spec;
  }
  function findPort(v, pid) {
    return spec(v).back.items.find(function (i) { return i.t === 'port' && i.id === pid; }) || null;
  }

  // ------------------------------------------------------------------------------------------- live state
  function btnState(v, it, ui) {
    if (!it.act) return it.lit ? 'on' : 'dis';
    const a = it.act.split(':');
    if (v.kind === 'atem') {
      if (a[0] === 'pgm' && v.pgm === +a[1]) return 'pgm';
      if (a[0] === 'pvw' && v.pvw === +a[1]) return 'pvw';
    } else if (v.kind === 'hyperdeck') {
      if (a[0] === 'record' && v.status === 'record') return 'pgm';
      if (a[0] === 'play' && v.status === 'play') return 'pvw';
      if (a[0] === 'stop' && (v.status === 'stop' || v.status === 'stopped')) return 'on';
    } else if (v.kind === 'videohub') {
      if (a[0] === 'dst' && ui.out === +a[1]) return 'on';
      if (a[0] === 'dst' && v.locks[+a[1]]) return 'lock';
      if (a[0] === 'take' && ui.out && ui.inp) return 'pgm';
    }
    return '';
  }
  function tipFor(v, it) {
    if (!it.act) return it.why || 'Not controlled from Relay.';
    if (it.tipText) return it.tipText;
    const a = it.act.split(':');
    const name = function (n) { return v.inputs && v.inputs[n] !== undefined ? v.inputs[n] : a[1]; };
    switch (a[0]) {
      case 'pgm': return 'Cut ' + name(+a[1]) + ' straight to program';
      case 'pvw': return 'Put ' + name(+a[1]) + ' on preview';
      case 'cut': return 'Cut: preview goes to program';
      case 'auto': return 'Auto: transition preview to program';
      case 'ftb': return 'Fade to black, or back from black';
      case 'record': return 'Record'; case 'stop': return 'Stop'; case 'play': return 'Play';
      case 'dst': return 'Choose output ' + a[1] + ' (' + v.outputs[+a[1] - 1] + ') to route';
      case 'take': return 'Take the chosen route';
      case 'lock': return 'Lock or unlock the chosen output';
    }
    return '';
  }

  /* One connector: what it is, what Relay knows is on it now, its cable and its note. */
  function portInfo(v, p, ctx) {
    const info = { title: v.name + ' · ' + p.label, kind: KIND[p.kind] || p.kind,
      dir: p.dir === 'in' ? 'Input' : p.dir === 'out' ? 'Output' : 'Connection',
      label: '', format: '', now: '', cable: '', note: v.notes[p.id] || '', key: v.id + ':' + p.id, live: false, cabled: false };
    if (p.net || p.power) { info.live = v.connected; info.now = v.connected ? (p.net ? 'Relay is connected over this' : 'On') : 'Not connected'; }
    if (v.kind === 'atem') {
      if (p.input) {
        info.label = v.inputs[p.input] || '';
        if (v.pgm === p.input) { info.now = 'On program now'; info.live = true; }
        else if (v.pvw === p.input) { info.now = 'On preview'; info.live = true; }
      }
      if (p.out === 0) { info.label = 'Program'; info.live = v.connected; info.now = 'Program out'; }
    } else if (v.kind === 'hyperdeck') {
      if (p.rec) { info.format = v.format; info.live = v.status === 'record'; info.now = v.status === 'record' ? 'Recording: ' + (v.clip || 'a new clip') : ''; }
    } else if (v.kind === 'videohub') {
      if (p.input) {
        info.label = v.inputs[p.input - 1] || '';
        const to = [];
        v.routes.forEach(function (src, o) { if (src === p.input) to.push(v.outputs[o]); });
        info.live = to.length > 0;
        info.now = to.length ? 'Routed to ' + to.join(', ') : 'Not routed anywhere';
      } else if (p.out) {
        info.label = v.outputs[p.out - 1] || '';
        const src = v.routes[p.out - 1];
        info.now = src ? 'Shows input ' + src + ' · ' + v.inputs[src - 1] : '';
        if (v.locks[p.out]) info.now += v.locks[p.out] === 'O' ? ' · locked by Relay' : ' · locked by another panel';
      }
    }
    (ctx.cables || []).forEach(function (c) {
      const mine = c.a[0] === v.id && c.a[1] === p.id ? 'a' : c.b[0] === v.id && c.b[1] === p.id ? 'b' : '';
      if (!mine) return;
      const other = mine === 'a' ? c.b : c.a;
      const ov = ctx.views[other[0]], op = ov ? findPort(ov, other[1]) : null;
      info.cabled = true; info.cableId = c.id;
      info.cable = (mine === 'a' ? 'To ' : 'From ') + (ov ? ov.name : 'a removed device') + ' · ' + (op ? op.label : other[1]);
    });
    if (info.cabled && v.kind === 'videohub' && p.out) info.live = true;
    return info;
  }
  const KIND = { sdi: 'SDI (BNC)', hdmi: 'HDMI', eth: 'Ethernet (RJ45)', usb: 'USB-C', xlr: 'XLR', mini: '3.5 mm jack', iec: 'Mains (IEC)', barrel: '12V DC' };

  function lcdLines(v, ui) {
    if (v.kind === 'atem') return [['PGM', v.inputs[v.pgm] || '—'], ['PVW', v.inputs[v.pvw] || '—'], ['M/E', String(v.me + 1)]];
    if (v.kind === 'hyperdeck') {
      const st = { record: '● REC', play: '▶ PLAY', stop: '■ STOP', stopped: '■ STOP' }[v.status] || (v.status || '—').toUpperCase();
      const slot = (v.slots || []).find(function (s) { return +s.id === +v.slot; });
      const left = slot && slot.seconds_left ? Math.floor(slot.seconds_left / 3600) + ':' + String(Math.floor(slot.seconds_left % 3600 / 60)).padStart(2, '0') + ':' + String(slot.seconds_left % 60).padStart(2, '0') : '';
      return [[st, v.tc], ['', v.clip], left ? ['LEFT', left] : ['FMT', v.format]];
    }
    const o = ui.out || 1, src = v.routes[o - 1];
    const lines = [['OUT ' + o, v.outputs[o - 1] || ''], ['IN ' + (src || '—'), src ? v.inputs[src - 1] : '']];
    if (ui.inp) lines.push(['NEXT', ui.inp + ' · ' + v.inputs[ui.inp - 1]]);
    else lines.push(['', ui.out ? 'Pick an input, then TAKE' : 'Press an OUT button']);
    return lines;
  }

  // ------------------------------------------------------------------------------------------- drawing
  function drawPort(g, p, info) {
    const pg = svgEl('g', { class: 'hw-port ' + p.kind + (info.live ? ' live' : info.cabled ? ' used' : ''),
      transform: 'translate(' + p.x + ',' + p.y + ')', tabindex: '0', role: 'button', 'aria-label': info.title }, g);
    pg.dataset.port = p.id;
    const k = p.kind;
    if (k === 'sdi') { svgEl('circle', { r: p.tiny ? 7 : 10, class: 'hw-ring' }, pg); svgEl('circle', { r: p.tiny ? 4.5 : 6.5, class: 'hw-hole' }, pg); svgEl('circle', { r: 2.2, class: 'hw-pin' }, pg); }
    else if (k === 'hdmi') { svgEl('path', { d: 'M-15,-6 h30 v6 l-4,5 h-22 l-4,-5 z', class: 'hw-ring' }, pg); svgEl('rect', { x: -11, y: -3, width: 22, height: 3, class: 'hw-pin' }, pg); }
    else if (k === 'eth') { svgEl('rect', { x: -11, y: -10, width: 22, height: 18, rx: 1.5, class: 'hw-ring' }, pg); svgEl('rect', { x: -5, y: 5, width: 10, height: 5, class: 'hw-hole' }, pg); }
    else if (k === 'usb') { svgEl('rect', { x: -9, y: -4, width: 18, height: 8, rx: 4, class: 'hw-ring' }, pg); svgEl('rect', { x: -5, y: -1, width: 10, height: 2, class: 'hw-pin' }, pg); }
    else if (k === 'xlr') { svgEl('circle', { r: 13, class: 'hw-ring' }, pg); [[-4, -3], [4, -3], [0, 4]].forEach(function (q) { svgEl('circle', { cx: q[0], cy: q[1], r: 1.8, class: 'hw-pin' }, pg); }); }
    else if (k === 'mini') { svgEl('circle', { r: 7, class: 'hw-ring' }, pg); svgEl('circle', { r: 3, class: 'hw-hole' }, pg); }
    else if (k === 'iec') { svgEl('rect', { x: -16, y: -12, width: 32, height: 24, rx: 3, class: 'hw-ring' }, pg); [-7, 0, 7].forEach(function (x) { svgEl('rect', { x: x - 1.5, y: -4, width: 3, height: 8, class: 'hw-pin' }, pg); }); }
    else { svgEl('circle', { r: 9, class: 'hw-ring' }, pg); svgEl('circle', { r: 4, class: 'hw-hole' }, pg); }
    svgEl('circle', { r: 2.6, cx: k === 'iec' ? 20 : (p.tiny ? 9 : 13), cy: p.tiny ? -9 : -11, class: 'hw-led' }, pg);
    const y = p.compact ? 22 : (k === 'xlr' ? 28 : 24);
    svgEl('text', { y: y, 'text-anchor': 'middle', class: 'hw-plabel' }, pg, p.label);
    if (info.label && info.label !== p.label && !p.tiny) {
      const max = p.compact ? 6 : 8;
      svgEl('text', { y: y + 11, 'text-anchor': 'middle', class: 'hw-dlabel' }, pg, info.label.length > max ? info.label.slice(0, max - 1) + '…' : info.label);
    }
    if (info.note) svgEl('circle', { r: 2.6, cx: p.tiny ? -9 : -13, cy: p.tiny ? -9 : -11, class: 'hw-note' }, pg);
  }
  function drawButton(g, v, it, opts) {
    const live = it.act && opts.control && (!it.manage || opts.manage) && v.connected;
    const st = btnState(v, it, opts.ui || {});
    const bg = svgEl('g', { class: 'hw-btn' + (st ? ' ' + st : '') + (it.act && !live ? ' off' : '') + (it.big ? ' big' : '') + (it.small ? ' small' : '') + (it.tone ? ' tone-' + it.tone : ''),
      transform: 'translate(' + it.x + ',' + it.y + ')', tabindex: live ? '0' : null, role: live ? 'button' : null }, g);
    if (live) bg.dataset.act = it.act;
    svgEl('rect', { width: it.w, height: it.h, rx: 4, class: 'hw-key' }, bg);
    svgEl('text', { x: it.w / 2, y: it.h / 2 + 4, 'text-anchor': 'middle', class: 'hw-klabel' }, bg, it.label);
    svgEl('title', {}, bg, it.act && !opts.control ? 'Needs permission to control Blackmagic devices.' : tipFor(v, it));
    if (it.name) {
      const nm = v.inputs[it.src] || '';
      svgEl('text', { x: it.w / 2, y: it.h + 10, 'text-anchor': 'middle', class: 'hw-dlabel' }, bg, nm.length > 7 ? nm.slice(0, 6) + '…' : nm);
    }
  }

  /* An <svg> of one face ('front' or 'back') of a device. opts: control, manage (permissions), ui (the Videohub's
     chosen output and input), sel (the selected connector id), ctx ({views, cables}). */
  function draw(v, face, opts) {
    opts = opts || {};
    const s = spec(v), side = s[face];
    const svg = svgEl('svg', { viewBox: '0 0 ' + side.w + ' ' + side.h, class: 'hw ' + s.form + ' ' + face, role: 'img', 'aria-label': v.name + ', ' + face });
    svg.dataset.dev = v.id; svg.dataset.face = face;
    if (s.form === 'rack') {
      svgEl('rect', { x: 0, y: 0, width: side.w, height: side.h, rx: 3, class: 'hw-ear' }, svg);
      svgEl('rect', { x: 24, y: 2, width: side.w - 48, height: side.h - 4, rx: 3, class: 'hw-body' }, svg);
      for (let u = 0; u < (s.u || 1); u++) {
        [12, side.w - 12].forEach(function (x) {
          svgEl('ellipse', { cx: x, cy: 88 * u + 22, rx: 5, ry: 3.5, class: 'hw-screw' }, svg);
          svgEl('ellipse', { cx: x, cy: 88 * u + 66, rx: 5, ry: 3.5, class: 'hw-screw' }, svg);
        });
      }
    } else {
      svgEl('rect', { x: 0, y: 0, width: side.w, height: side.h, rx: face === 'front' ? 16 : 8, class: 'hw-body' }, svg);
    }
    const ctx = opts.ctx || { views: {}, cables: [] };
    side.items.forEach(function (it) {
      if (it.t === 'zone') {
        svgEl('rect', { x: it.x, y: it.y, width: it.w, height: it.h, rx: 6, class: 'hw-zone' }, svg);
        svgEl('text', { x: it.x + 8, y: it.y + 14, class: 'hw-cap' }, svg, it.label);
      } else if (it.t === 'text') {
        if (it.label) svgEl('text', { x: it.x, y: it.y, 'text-anchor': it.anchor || 'start', class: it.cls }, svg, it.label);
      } else if (it.t === 'lcd') {
        const lg = svgEl('g', { class: 'hw-lcd', transform: 'translate(' + it.x + ',' + it.y + ')' }, svg);
        svgEl('rect', { width: it.w, height: it.h, rx: 3 }, lg);
        const lines = v.connected ? lcdLines(v, opts.ui || {}) : [['', 'Not connected']];
        const lh = Math.min(20, (it.h - 8) / Math.max(lines.length, 1));
        lines.forEach(function (ln, i) {
          const y = 8 + lh * (i + 0.75), t = String(ln[1] || '');
          svgEl('text', { x: 8, y: y, class: 'hw-lcd-k' }, lg, ln[0]);
          svgEl('text', { x: 52, y: y, class: 'hw-lcd-v' }, lg, t.length > 16 ? t.slice(0, 15) + '…' : t);
        });
      } else if (it.t === 'slot') {
        const info = (v.slots || []).find(function (x) { return +x.id === it.slot; }) || {};
        const sg = svgEl('g', { transform: 'translate(' + it.x + ',' + it.y + ')', class: 'hw-slot' + (v.status === 'record' && +v.slot === it.slot ? ' rec' : '') }, svg);
        svgEl('rect', { width: it.w, height: it.h, rx: 2 }, sg);
        svgEl('text', { x: it.w + 6, y: it.h - 3, class: 'hw-cap' }, sg, it.label);
        svgEl('title', {}, sg, it.label + ': ' + (info.status === 'mounted' ? (info.volume || 'Disk') : info.status === 'empty' ? 'no disk' : info.status || 'unknown'));
      } else if (it.t === 'knob') {
        const kg = svgEl('g', { transform: 'translate(' + it.x + ',' + it.y + ')', class: 'hw-knob' }, svg);
        svgEl('circle', { r: it.r }, kg); svgEl('circle', { r: it.r * 0.35, class: 'hw-hole' }, kg);
        svgEl('title', {}, kg, it.why || '');
      } else if (it.t === 'btn') drawButton(svg, v, it, opts);
      else if (it.t === 'port') drawPort(svg, it, portInfo(v, it, ctx));
    });
    if (opts.sel) {
      const sel = Array.prototype.find.call(svg.querySelectorAll('[data-port]'), function (n) { return n.dataset.port === opts.sel; });
      if (sel) sel.classList.add('sel');
    }
    if (!v.connected) svg.classList.add('offline');
    return svg;
  }

  // ------------------------------------------------------------------------------------------- tooltip and handling
  let tip = null;
  function showTip(evt, info) {
    if (!tip) { tip = el('div', 'hw-tip'); document.body.appendChild(tip); }
    tip.textContent = '';
    const dl = el('dl');
    [['Type', info.kind + ' · ' + info.dir], ['Label', info.label], ['Format', info.format], ['Now', info.now], ['Cable', info.cable], ['Note', info.note]]
      .forEach(function (r) { if (r[1]) dl.append(el('dt', { text: r[0] }), el('dd', { text: r[1] })); });
    tip.append(el('b', { text: info.title }), dl);
    tip.hidden = false;
    const x = Math.min(evt.clientX + 14, window.innerWidth - 300);
    let y = evt.clientY + 16;
    if (y + 180 > window.innerHeight) y = evt.clientY - 180;
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  }
  function hideTip() { if (tip) tip.hidden = true; }

  /* Hover, click and keyboard on a drawn face. on.port(pid) when a connector is clicked; on.act(action) when a
     button is pressed; ctx as for draw(). */
  function wire(svg, v, ctx, on) {
    svg.addEventListener('mousemove', function (e) {
      const pg = e.target.closest('[data-port]');
      if (!pg) { hideTip(); return; }
      showTip(e, portInfo(v, findPort(v, pg.dataset.port), ctx));
    });
    svg.addEventListener('mouseleave', hideTip);
    function act(e) {
      const pg = e.target.closest('[data-port]');
      if (pg) { hideTip(); if (on.port) on.port(pg.dataset.port); return; }
      const b = e.target.closest('[data-act]');
      if (b && on.act) on.act(b.dataset.act);
    }
    svg.addEventListener('click', act);
    svg.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); act(e); } });
  }

  window.BMDraw = { NAMES: NAMES, auto: auto, view: view, spec: spec, draw: draw, wire: wire, portInfo: portInfo, findPort: findPort, hideTip: hideTip, svgEl: svgEl };
})();
