/* Blackmagic mockups: the model library and the drawing code shared by the three options.
   Each model is drawn from a spec (buttons, screens, ports with their positions), not from a picture, so a new model is
   one more spec. Buttons Relay can press carry an action; the rest are drawn but dimmed ("not from Relay"). */
(function () {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';
  function s(tag, attrs, parent, text) {
    var e = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { if (attrs[k] != null) e.setAttribute(k, attrs[k]); });
    if (text != null) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  }
  function h(tag, cls, parent, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  }

  /* ---------- model specs ---------- */
  function btn(id, x, y, w, hh, label, act, extra) {
    var b = { t: 'btn', id: id, x: x, y: y, w: w, h: hh, label: label, act: act || null };
    if (extra) Object.keys(extra).forEach(function (k) { b[k] = extra[k]; });
    return b;
  }
  function port(id, kind, x, y, label, dir, extra) {
    var p = { t: 'port', id: id, kind: kind, x: x, y: y, label: label, dir: dir || '' };
    if (extra) Object.keys(extra).forEach(function (k) { p[k] = extra[k]; });
    return p;
  }

  function atemSourceRows(x0, step, w, yP, yV, srcs, items) {
    items.push({ t: 'text', x: x0 - 10, y: yP + 21, label: 'PROGRAM', anchor: 'end', cls: 'hw-cap' });
    items.push({ t: 'text', x: x0 - 10, y: yV + 21, label: 'PREVIEW', anchor: 'end', cls: 'hw-cap' });
    srcs.forEach(function (src, i) {
      items.push(btn('pgm' + src.n, x0 + i * step, yP, w, 32, src.k, 'pgm:' + src.n, { src: src.n }));
      items.push(btn('pvw' + src.n, x0 + i * step, yV, w, 32, src.k, 'pvw:' + src.n, { src: src.n }));
    });
  }
  var SRC10 = [1, 2, 3, 4, 5, 6, 7, 8].map(function (n) { return { n: n, k: String(n) }; })
    .concat([{ n: 0, k: 'BLK' }, { n: 3010, k: 'MP1' }]);

  var MODELS = {};

  MODELS['tvs-hd8'] = (function () {
    var f = [], b = [];
    f.push({ t: 'lcd', x: 40, y: 22, w: 168, h: 98, key: 'atem' });
    atemSourceRows(300, 44, 38, 26, 70, SRC10, f);
    f.push(btn('dsktie', 758, 26, 46, 32, 'DSK TIE', 'dsktie'));
    f.push(btn('dskon', 758, 70, 46, 32, 'DSK ON', 'dskon'));
    f.push(btn('cut', 820, 26, 56, 32, 'CUT', 'cut', { big: 1 }));
    f.push(btn('auto', 820, 70, 56, 32, 'AUTO', 'auto', { big: 1 }));
    f.push({ t: 'text', x: 290, y: 142, label: 'MACRO', anchor: 'end', cls: 'hw-cap' });
    for (var m = 1; m <= 6; m++) f.push(btn('mac' + m, 256 + m * 44, 124, 38, 28, String(m), 'macro:' + m));
    f.push(btn('ftb', 588, 124, 56, 28, 'FTB', 'ftb'));
    f.push(btn('rec', 656, 124, 46, 28, 'REC', null, { why: 'ISO recording is started on the switcher or in ATEM Software Control.' }));
    f.push(btn('stream', 712, 124, 60, 28, 'STREAM', null, { why: 'Streaming is started on the switcher.' }));
    f.push({ t: 'text', x: 40, y: 150, label: 'ATEM Television Studio HD8 ISO', cls: 'hw-brand' });
    for (var i = 1; i <= 8; i++) b.push(port('in' + i, 'sdi', 40 + i * 50, 56, 'IN ' + i, 'in', { input: i }));
    ['PGM', 'AUX 1', 'AUX 2', 'AUX 3', 'AUX 4'].forEach(function (l, k) {
      b.push(port('out' + k, 'sdi', 90 + k * 50, 128, l, 'out', { out: k }));
    });
    b.push(port('mv', 'hdmi', 380, 128, 'MULTIVIEW', 'out'));
    b.push(port('ref', 'sdi', 490, 56, 'REF IN', 'in'));
    b.push(port('xlr1', 'xlr', 560, 56, 'AUDIO 1', 'in'));
    b.push(port('xlr2', 'xlr', 610, 56, 'AUDIO 2', 'in'));
    b.push(port('tb', 'eth', 560, 128, 'TALKBACK', ''));
    b.push(port('eth', 'eth', 680, 56, 'ETHERNET', ''));
    b.push(port('usb', 'usb', 740, 56, 'USB-C', ''));
    b.push(port('pwr', 'iec', 870, 88, 'AC 100–240V', ''));
    b.push({ t: 'zone', x: 52, y: 22, w: 418, h: 152, label: 'VIDEO' });
    return { id: 'tvs-hd8', name: 'ATEM Television Studio HD8 ISO', family: 'atem', form: 'rack', u: 2,
      front: { w: 960, h: 176, items: f }, back: { w: 960, h: 176, items: b } };
  })();

  MODELS['mini-extreme'] = (function () {
    var f = [], b = [];
    f.push({ t: 'zone', x: 24, y: 28, w: 236, h: 230, label: 'AUDIO' });
    var ch = ['M1', 'M2', '1', '2', '3', '4', '5', '6', '7', '8'];
    ch.forEach(function (c, i) {
      var col = i % 5, row = Math.floor(i / 5);
      f.push(btn('a' + c, 40 + col * 42, 56 + row * 96, 34, 22, c, null, { small: 1, why: 'Audio is mixed on the switcher; Relay does not change it.' }));
      f.push(btn('afv' + c, 40 + col * 42, 84 + row * 96, 34, 22, 'AFV', null, { small: 1, why: 'Audio is mixed on the switcher; Relay does not change it.' }));
    });
    f.push({ t: 'lcd', x: 300, y: 28, w: 140, h: 62, key: 'atemmini' });
    var srcs = [1, 2, 3, 4, 5, 6, 7, 8].map(function (n) { return { n: n, k: String(n) }; })
      .concat([{ n: 0, k: 'BLK' }, { n: 6000, k: 'SS' }]);
    atemSourceRows(370, 40, 34, 112, 160, srcs, f);
    f.push({ t: 'text', x: 360, y: 236, label: 'TRANSITION', anchor: 'end', cls: 'hw-cap' });
    ['MIX', 'DIP', 'WIPE', 'DVE', 'STING'].forEach(function (l, i) {
      f.push(btn('tr' + l, 370 + i * 46, 216, 40, 28, l, null, { small: 1, why: 'Relay uses the transition already chosen on the switcher.' }));
    });
    f.push(btn('dsktie', 790, 112, 46, 32, 'DSK TIE', 'dsktie'));
    f.push(btn('dskon', 790, 160, 46, 32, 'DSK ON', 'dskon'));
    f.push(btn('cut', 850, 112, 58, 32, 'CUT', 'cut', { big: 1 }));
    f.push(btn('auto', 850, 160, 58, 32, 'AUTO', 'auto', { big: 1 }));
    f.push(btn('ftb', 850, 216, 58, 28, 'FTB', 'ftb'));
    f.push(btn('macro', 790, 216, 46, 28, 'MACRO', 'macro:1'));
    f.push({ t: 'knob', x: 660, y: 58, r: 30, label: 'JOYSTICK', why: 'Moves the DVE; not from Relay.' });
    f.push(btn('rec', 720, 34, 56, 26, 'REC', null, { why: 'Recording is started on the switcher.' }));
    f.push(btn('onair', 786, 34, 56, 26, 'ON AIR', null, { why: 'Streaming is started on the switcher.' }));
    f.push(btn('stream', 852, 34, 56, 26, 'STREAM', null, { why: 'Streaming is started on the switcher.' }));
    f.push({ t: 'text', x: 24, y: 300, label: 'ATEM Mini Extreme ISO', cls: 'hw-brand' });
    for (var i = 1; i <= 8; i++) b.push(port('in' + i, 'hdmi', 14 + i * 50, 54, 'HDMI ' + i, 'in', { input: i }));
    b.push(port('out1', 'hdmi', 490, 54, 'OUT 1', 'out', { out: 0 }));
    b.push(port('out2', 'hdmi', 545, 54, 'OUT 2', 'out', { out: 1 }));
    b.push(port('mic1', 'mini', 610, 54, 'MIC 1', 'in'));
    b.push(port('mic2', 'mini', 660, 54, 'MIC 2', 'in'));
    b.push(port('hs', 'mini', 714, 54, 'HEADSET', ''));
    b.push(port('eth', 'eth', 776, 54, 'ETHERNET', ''));
    b.push(port('usb1', 'usb', 834, 54, 'USB-C 1', ''));
    b.push(port('usb2', 'usb', 882, 54, 'USB-C 2', ''));
    b.push(port('pwr', 'barrel', 930, 54, '12V', ''));
    return { id: 'mini-extreme', name: 'ATEM Mini Extreme ISO', family: 'atem', form: 'desk',
      front: { w: 960, h: 320, items: f }, back: { w: 960, h: 110, items: b } };
  })();

  MODELS['hyperdeck-hd-plus'] = (function () {
    var f = [], b = [];
    f.push({ t: 'lcd', x: 40, y: 12, w: 168, h: 64, key: 'deck' });
    f.push({ t: 'slot', x: 228, y: 18, w: 128, h: 14, label: 'SD 1', key: 'sd1' });
    f.push({ t: 'slot', x: 228, y: 48, w: 128, h: 14, label: 'SD 2', key: 'sd2' });
    f.push(btn('prev', 420, 26, 46, 34, '⏮', null, { why: 'Relay records, stops and plays; pick clips on the deck.' }));
    f.push(btn('rec', 476, 26, 46, 34, 'REC', 'rec', { tone: 'rec' }));
    f.push(btn('stop', 532, 26, 46, 34, 'STOP', 'stop'));
    f.push(btn('play', 588, 26, 46, 34, 'PLAY', 'play'));
    f.push(btn('next', 644, 26, 46, 34, '⏭', null, { why: 'Relay records, stops and plays; pick clips on the deck.' }));
    f.push(btn('rem', 712, 26, 46, 34, 'REM', null, { why: 'Remote is on so Relay can control the deck.', lit: 1 }));
    f.push({ t: 'knob', x: 860, y: 44, r: 30, label: 'SEARCH', why: 'Jog and shuttle are on the deck.' });
    f.push({ t: 'text', x: 228, y: 80, label: 'HyperDeck Studio HD Plus', cls: 'hw-brand' });
    b.push(port('sdiin', 'sdi', 90, 44, 'SDI IN', 'in'));
    b.push(port('sdiout', 'sdi', 140, 44, 'SDI OUT', 'out'));
    b.push(port('hdmiin', 'hdmi', 210, 44, 'HDMI IN', 'in'));
    b.push(port('hdmiout', 'hdmi', 270, 44, 'HDMI OUT', 'out'));
    b.push(port('ref', 'sdi', 340, 44, 'REF IN', 'in'));
    b.push(port('tcin', 'xlr', 400, 44, 'TC IN', 'in'));
    b.push(port('tcout', 'xlr', 450, 44, 'TC OUT', 'out'));
    b.push(port('eth', 'eth', 560, 44, 'ETHERNET', ''));
    b.push(port('usb', 'usb', 620, 44, 'USB-C', ''));
    b.push(port('pwr', 'iec', 860, 44, 'AC', ''));
    return { id: 'hyperdeck-hd-plus', name: 'HyperDeck Studio HD Plus', family: 'hyperdeck', form: 'rack', u: 1,
      front: { w: 960, h: 88, items: f }, back: { w: 960, h: 88, items: b } };
  })();

  function videohub(n) {
    var f = [], b = [];
    var u = n > 12 ? 2 : 1, H = 88 * u;
    f.push({ t: 'lcd', x: 40, y: 12, w: 160, h: H - 24, key: 'hub' });
    var cols = n > 12 ? 10 : 12, x0 = 250, step = n > 12 ? 44 : 38, bw = step - 6;
    f.push({ t: 'text', x: x0 - 8, y: 24, label: n > 12 ? 'OUT' : 'OUT', anchor: 'end', cls: 'hw-cap' });
    for (var i = 1; i <= n; i++) {
      var row = Math.floor((i - 1) / cols), col = (i - 1) % cols;
      f.push(btn('dst' + i, x0 + col * step, 12 + row * 40, bw, 30, String(i), 'dst:' + i, { dst: i }));
    }
    var rows = Math.ceil(n / cols);
    var x1 = x0 + cols * step + 18;
    f.push(btn('take', x1, 12, 56, 30, 'TAKE', 'take', { big: 1 }));
    f.push(btn('lock', x1, 12 + 40, 56, 30, 'LOCK', 'lock'));
    f.push({ t: 'knob', x: x1 + 110, y: H / 2, r: 26, label: 'SOURCE', why: 'Choose the source in Relay by clicking an input on the back, or from the list.' });
    f.push({ t: 'text', x: 40, y: H - 2, label: n > 12 ? '' : '', cls: 'hw-brand' });
    var bx0 = 70, bstep = Math.min(40, (820 - bx0) / n);
    for (var k = 1; k <= n; k++) {
      b.push(port('in' + k, 'sdi', bx0 + (k - 1) * bstep, H * 0.3, String(k), 'in', { input: k, compact: 1 }));
      b.push(port('out' + k, 'sdi', bx0 + (k - 1) * bstep, H * 0.74, String(k), 'out', { out: k, compact: 1 }));
    }
    b.push({ t: 'text', x: 40, y: H * 0.3 + 4, label: 'IN', anchor: 'end', cls: 'hw-cap' });
    b.push({ t: 'text', x: 40, y: H * 0.74 + 4, label: 'OUT', anchor: 'end', cls: 'hw-cap' });
    b.push(port('ref', 'sdi', 860, H * 0.3, 'REF', 'in', { compact: 1 }));
    b.push(port('eth', 'eth', 860, H * 0.74, 'ETH', '', { compact: 1 }));
    b.push(port('pwr', 'iec', 920, H / 2, 'AC', '', { compact: 1 }));
    var name = (n > 12 ? 'Videohub 20x20 12G' : 'Smart Videohub 12x12 12G');
    return { id: 'videohub-' + n, name: name, family: 'videohub', form: 'rack', u: u, size: n,
      front: { w: 960, h: H, items: f }, back: { w: 960, h: H, items: b } };
  }
  MODELS['videohub-20'] = videohub(20);
  MODELS['videohub-12'] = videohub(12);

  /* ---------- demo state: what Relay would read from the devices ---------- */
  var DEVICES = [
    { id: 'main', name: 'Main room switcher', model: 'tvs-hd8', host: '10.0.20.40', connected: true, firmware: '9.6',
      state: { pgm: 2, pvw: 1, dsk: true, dsktie: false, ftb: false, inputs: { 1: 'Cam 1 Wide', 2: 'Cam 2 Pulpit', 3: 'Cam 3 Stage L', 4: 'Cam 4 Stage R', 5: 'ProPresenter', 6: 'Lyrics key', 7: 'Playback', 8: 'Walk-in loop', 0: 'Black', 3010: 'Media 1' },
        signal: { in1: '1080p59.94', in2: '1080p59.94', in3: '1080p59.94', in4: '1080p59.94', in5: '1080p59.94', in6: '1080p59.94', in7: '1080p59.94', in8: '', out0: '1080p59.94', out1: '1080p59.94', out2: '1080p59.94', mv: '1080p59.94', eth: '1 Gb/s', pwr: 'On', xlr1: 'Line' },
        outs: ['Program', 'Cam 2 Pulpit (Aux 1)', 'ProPresenter (Aux 2)', 'Program (Aux 3)', 'Black (Aux 4)'],
        macros: ['Walk-in', 'Sermon', 'Song', 'Baptism', 'End', 'Reset'] } },
    { id: 'youth', name: 'Youth room switcher', model: 'mini-extreme', host: '10.0.20.41', connected: true, firmware: '9.6',
      state: { pgm: 1, pvw: 3, dsk: false, dsktie: false, ftb: false, inputs: { 1: 'Wide', 2: 'Stage', 3: 'Lyrics', 4: 'Laptop', 5: 'In 5', 6: 'In 6', 7: 'In 7', 8: 'In 8', 0: 'Black', 6000: 'SuperSource' },
        signal: { in1: '1080p29.97', in2: '1080p29.97', in3: '1080p29.97', in4: '', out1: '1080p29.97', out2: '1080p29.97', eth: '1 Gb/s', usb1: 'Webcam out', pwr: 'On', mic1: 'Mic' },
        outs: ['Program', 'Multiview'], macros: ['Start'] } },
    { id: 'deck', name: 'Program recorder', model: 'hyperdeck-hd-plus', host: '10.0.20.45', connected: true, firmware: '8.3',
      state: { mode: 'rec', clip: 'Sunday 2026-11-18 0900', left: '3:42:10', tc: '00:38:12:04', sd1: 'Recording · 61% free', sd2: 'Ready · 98% free',
        signal: { sdiin: '1080p59.94', sdiout: '1080p59.94', ref: '', eth: '1 Gb/s', pwr: 'On' } } },
    { id: 'hub', name: 'Main router', model: 'videohub-20', host: '10.0.20.50', connected: true, firmware: '9.1',
      state: { inputs: ['Cam 1', 'Cam 2', 'Cam 3', 'Cam 4', 'ProPres', 'ProP Key', 'Playback', 'Loop', 'ATEM PGM', 'ATEM Aux1', 'Lobby cam', 'Kids cam', 'In 13', 'In 14', 'In 15', 'In 16', 'In 17', 'In 18', 'In 19', 'Bars'],
        outputs: ['ATEM 1', 'ATEM 2', 'ATEM 3', 'ATEM 4', 'ATEM 5', 'ATEM 6', 'ATEM 7', 'ATEM 8', 'Deck', 'Lobby TV', 'Kids TV', 'Stream PC', 'Confidence', 'Out 14', 'Out 15', 'Out 16', 'Out 17', 'Out 18', 'Out 19', 'Scope'],
        routes: [1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 12, 9, 10, 20, 20, 20, 20, 20, 20, 20],
        locks: { 9: 'O', 12: 'L' }, live: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 20], sel: null } }
  ];

  /* Cables Relay cannot see, written once by the operator (or inferred from labels), so a port can say what it feeds. */
  var CABLES = [
    { a: ['hub', 'out1'], b: ['main', 'in1'] }, { a: ['hub', 'out2'], b: ['main', 'in2'] },
    { a: ['hub', 'out3'], b: ['main', 'in3'] }, { a: ['hub', 'out4'], b: ['main', 'in4'] },
    { a: ['hub', 'out5'], b: ['main', 'in5'] }, { a: ['hub', 'out6'], b: ['main', 'in6'] },
    { a: ['hub', 'out7'], b: ['main', 'in7'] }, { a: ['hub', 'out8'], b: ['main', 'in8'] },
    { a: ['main', 'out0'], b: ['hub', 'in9'] }, { a: ['main', 'out1'], b: ['hub', 'in10'] },
    { a: ['hub', 'out9'], b: ['deck', 'sdiin'] }
  ];
  var NOTES = {
    'hub:in1': 'Cam 1 tie line, booth wall plate A1',
    'hub:in2': 'Cam 2 tie line, A2 (pulpit cam on the balcony)',
    'main:xlr1': 'Board matrix 1 (program mix) via DI',
    'deck:sdiin': '3 m SDI from Videohub out 9, rack rear',
    'youth:in4': 'Floor box HDMI for guest laptops; check the adapter is here',
    'hub:out12': 'Stream PC capture card'
  };

  function dev(id) { for (var i = 0; i < DEVICES.length; i++) if (DEVICES[i].id === id) return DEVICES[i]; return null; }
  function model(d) { return MODELS[d.model]; }

  /* ---------- live state of each drawn thing ---------- */
  function btnState(d, it) {
    var st = d.state, f = model(d).family;
    if (!it.act) return it.lit ? 'on' : 'dis';
    var a = it.act.split(':');
    if (f === 'atem') {
      if (a[0] === 'pgm' && st.pgm === +a[1]) return 'pgm';
      if (a[0] === 'pvw' && st.pvw === +a[1]) return 'pvw';
      if (a[0] === 'dskon' && st.dsk) return 'pgm';
      if (a[0] === 'dsktie' && st.dsktie) return 'on';
      if (a[0] === 'ftb' && st.ftb) return 'pgm blink';
      return '';
    }
    if (f === 'hyperdeck') {
      if (a[0] === 'rec' && st.mode === 'rec') return 'pgm';
      if (a[0] === 'play' && st.mode === 'play') return 'pvw';
      if (a[0] === 'stop' && st.mode === 'stop') return 'on';
      return '';
    }
    if (f === 'videohub') {
      if (a[0] === 'dst' && st.sel === +a[1]) return 'on';
      if (a[0] === 'dst' && st.locks[+a[1]]) return 'lock';
      if (a[0] === 'take' && st.sel && st.pending) return 'pgm';
      return '';
    }
    return '';
  }

  function portInfo(d, p) {
    var st = d.state, f = model(d).family, key = d.id + ':' + p.id;
    var info = { title: d.name + ' · ' + p.label, kind: KIND_NAME[p.kind] || p.kind, dir: p.dir === 'in' ? 'Input' : p.dir === 'out' ? 'Output' : 'Connection',
      signal: '', label: '', routed: '', feeds: '', note: NOTES[key] || '', key: key, live: false, used: false };
    if (f === 'atem') {
      if (p.input) { info.label = st.inputs[p.input] || ''; info.signal = (st.signal || {})['in' + p.input] || ''; }
      if (p.out != null) { info.label = st.outs[p.out] || ''; info.signal = (st.signal || {})['out' + p.out] || (st.signal || {})['out' + (p.out + 1)] || ''; }
      if (!p.input && p.out == null) info.signal = (st.signal || {})[p.id] || '';
      if (p.input && st.pgm === p.input) info.routed = 'On program now';
      else if (p.input && st.pvw === p.input) info.routed = 'On preview';
    } else if (f === 'hyperdeck') {
      info.signal = st.signal[p.id] || '';
      if (p.id === 'sdiin') info.routed = st.mode === 'rec' ? 'Recording this input: ' + st.clip : 'Selected record input';
    } else if (f === 'videohub') {
      if (p.input) {
        info.label = st.inputs[p.input - 1];
        info.signal = st.live.indexOf(p.input) >= 0 ? '1080p59.94' : '';
        var to = [];
        st.routes.forEach(function (src, o) { if (src === p.input) to.push(st.outputs[o]); });
        info.routed = to.length ? 'Routed to ' + to.join(', ') : 'Not routed anywhere';
      } else if (p.out) {
        info.label = st.outputs[p.out - 1];
        var src = st.routes[p.out - 1];
        info.signal = st.live.indexOf(src) >= 0 ? '1080p59.94' : '';
        info.routed = 'Showing in ' + src + ' · ' + st.inputs[src - 1] + (st.locks[p.out] ? (st.locks[p.out] === 'O' ? ' · locked by Relay' : ' · locked by another panel') : '');
      } else info.signal = p.id === 'eth' ? '1 Gb/s' : p.id === 'pwr' ? 'On' : '';
    }
    CABLES.forEach(function (c) {
      var me = c.a[0] === d.id && c.a[1] === p.id ? c.a : c.b[0] === d.id && c.b[1] === p.id ? c.b : null;
      if (!me) return;
      var other = me === c.a ? c.b : c.a, od = dev(other[0]), op = findPort(od, other[1]);
      info.feeds = (p.dir === 'out' ? 'Cabled to ' : 'Cabled from ') + od.name + ' · ' + (op ? op.label : other[1]);
    });
    info.live = !!info.signal;
    info.used = info.live || !!info.feeds || !!info.note;
    return info;
  }
  var KIND_NAME = { sdi: 'SDI (BNC)', hdmi: 'HDMI', eth: 'Ethernet (RJ45)', usb: 'USB-C', xlr: 'XLR', mini: '3.5 mm jack', iec: 'Mains (IEC)', barrel: '12V DC' };
  function findPort(d, pid) {
    var items = model(d).back.items;
    for (var i = 0; i < items.length; i++) if (items[i].t === 'port' && items[i].id === pid) return items[i];
    return null;
  }

  function lcdLines(d, key) {
    var st = d.state;
    if (key === 'atem' || key === 'atemmini') return [['PGM', st.inputs[st.pgm] || '-'], ['PVW', st.inputs[st.pvw] || '-'], ['DSK', st.dsk ? 'On air' : 'Off'], ['FTB', st.ftb ? 'BLACK' : '']];
    if (key === 'deck') return [[st.mode === 'rec' ? '● REC' : st.mode === 'play' ? '▶ PLAY' : '■ STOP', st.tc], ['', st.clip], ['LEFT', st.left]];
    if (key === 'hub') {
      var o = st.sel || 9;
      var lines = [['OUT ' + o, st.outputs[o - 1]], ['IN ' + st.routes[o - 1], st.inputs[st.routes[o - 1] - 1]]];
      if (st.pending) lines.push(['NEXT', st.pending + ' · ' + st.inputs[st.pending - 1]]);
      else lines.push(['', st.sel ? 'Click an input, then TAKE' : 'Press an OUT button']);
      return lines;
    }
    return [];
  }

  /* ---------- drawing ---------- */
  function drawPort(g, p, info, compact) {
    var cls = 'hw-port ' + p.kind + (info.live ? ' live' : info.used ? ' used' : '');
    var pg = s('g', { 'class': cls, transform: 'translate(' + p.x + ',' + p.y + ')', tabindex: '0', role: 'button', 'aria-label': info.title }, g);
    pg.dataset.port = p.id;
    var k = p.kind;
    if (k === 'sdi') { s('circle', { r: 10, 'class': 'hw-ring' }, pg); s('circle', { r: 6.5, 'class': 'hw-hole' }, pg); s('circle', { r: 2.2, 'class': 'hw-pin' }, pg); }
    else if (k === 'hdmi') { s('path', { d: 'M-15,-6 h30 v6 l-4,5 h-22 l-4,-5 z', 'class': 'hw-ring' }, pg); s('rect', { x: -11, y: -3, width: 22, height: 3, 'class': 'hw-pin' }, pg); }
    else if (k === 'eth') { s('rect', { x: -11, y: -10, width: 22, height: 18, rx: 1.5, 'class': 'hw-ring' }, pg); s('rect', { x: -5, y: 5, width: 10, height: 5, 'class': 'hw-hole' }, pg); }
    else if (k === 'usb') { s('rect', { x: -9, y: -4, width: 18, height: 8, rx: 4, 'class': 'hw-ring' }, pg); s('rect', { x: -5, y: -1, width: 10, height: 2, 'class': 'hw-pin' }, pg); }
    else if (k === 'xlr') { s('circle', { r: 13, 'class': 'hw-ring' }, pg); [[-4, -3], [4, -3], [0, 4]].forEach(function (q) { s('circle', { cx: q[0], cy: q[1], r: 1.8, 'class': 'hw-pin' }, pg); }); }
    else if (k === 'mini') { s('circle', { r: 7, 'class': 'hw-ring' }, pg); s('circle', { r: 3, 'class': 'hw-hole' }, pg); }
    else if (k === 'iec') { s('rect', { x: -16, y: -12, width: 32, height: 24, rx: 3, 'class': 'hw-ring' }, pg); [-7, 0, 7].forEach(function (x) { s('rect', { x: x - 1.5, y: -4, width: 3, height: 8, 'class': 'hw-pin' }, pg); }); }
    else if (k === 'barrel') { s('circle', { r: 9, 'class': 'hw-ring' }, pg); s('circle', { r: 4, 'class': 'hw-hole' }, pg); }
    s('circle', { r: 2.6, cx: k === 'iec' ? 20 : 13, cy: -11, 'class': 'hw-led' }, pg);
    var y = compact ? 22 : (k === 'xlr' ? 28 : 24);
    s('text', { y: y, 'text-anchor': 'middle', 'class': 'hw-plabel' }, pg, p.label);
    if (info.label && info.label !== p.label) {
      var short = info.label.length > (compact ? 6 : 8) ? info.label.slice(0, compact ? 6 : 7) + '…' : info.label;
      s('text', { y: y + 11, 'text-anchor': 'middle', 'class': 'hw-dlabel' }, pg, short);
    }
    if (info.note) s('circle', { r: 2.6, cx: -13, cy: -11, 'class': 'hw-note' }, pg);
    return pg;
  }

  function drawButton(g, d, it) {
    var st = btnState(d, it);
    var cls = 'hw-btn' + (st ? ' ' + st : '') + (it.big ? ' big' : '') + (it.small ? ' small' : '') + (it.tone ? ' tone-' + it.tone : '');
    var bg = s('g', { 'class': cls, transform: 'translate(' + it.x + ',' + it.y + ')', tabindex: it.act ? '0' : null, role: it.act ? 'button' : null }, g);
    bg.dataset.btn = it.id;
    if (it.act) bg.dataset.act = it.act;
    s('rect', { width: it.w, height: it.h, rx: 4, 'class': 'hw-key' }, bg);
    s('text', { x: it.w / 2, y: it.h / 2 + 4, 'text-anchor': 'middle', 'class': 'hw-klabel' }, bg, it.label);
    var tip = it.act ? actionText(d, it) : (it.why || 'Not controlled from Relay.');
    s('title', {}, bg, tip);
    if (it.src != null && model(d).family === 'atem' && it.id.indexOf('pvw') === 0) {
      var nm = d.state.inputs[it.src] || '';
      s('text', { x: it.w / 2, y: it.h + 10, 'text-anchor': 'middle', 'class': 'hw-dlabel' }, bg, nm.length > 7 ? nm.slice(0, 6) + '…' : nm);
    }
  }
  function actionText(d, it) {
    var a = it.act.split(':'), st = d.state;
    switch (a[0]) {
      case 'pgm': return 'Cut ' + (st.inputs[+a[1]] || a[1]) + ' straight to program';
      case 'pvw': return 'Put ' + (st.inputs[+a[1]] || a[1]) + ' on preview';
      case 'cut': return 'Cut: preview goes to program';
      case 'auto': return 'Auto: transition preview to program';
      case 'ftb': return 'Fade to black';
      case 'dskon': return 'Downstream key on or off air';
      case 'dsktie': return 'Tie the downstream key to the next transition';
      case 'macro': return 'Run macro ' + a[1] + (st.macros && st.macros[+a[1] - 1] ? ': ' + st.macros[+a[1] - 1] : '');
      case 'rec': return 'Record'; case 'stop': return 'Stop'; case 'play': return 'Play';
      case 'dst': return 'Choose output ' + a[1] + ' (' + st.outputs[+a[1] - 1] + ') to route';
      case 'take': return 'Take the chosen route';
      case 'lock': return 'Lock or unlock the chosen output';
    }
    return '';
  }

  function draw(d, face, opts) {
    opts = opts || {};
    var m = model(d), side = m[face];
    var pad = m.form === 'rack' ? 0 : 0;
    var svg = s('svg', { viewBox: '0 0 ' + side.w + ' ' + side.h, 'class': 'hw ' + m.form + ' ' + face, role: 'img', 'aria-label': m.name + ', ' + face });
    svg.dataset.dev = d.id; svg.dataset.face = face;
    if (m.form === 'rack') {
      s('rect', { x: 0, y: 0, width: side.w, height: side.h, rx: 3, 'class': 'hw-ear' }, svg);
      s('rect', { x: 24, y: 2, width: side.w - 48, height: side.h - 4, rx: 3, 'class': 'hw-body' }, svg);
      for (var u = 0; u < (m.u || 1); u++) {
        [12, side.w - 12].forEach(function (x) { s('ellipse', { cx: x, cy: 88 * u + 44 - 22, rx: 5, ry: 3.5, 'class': 'hw-screw' }, svg); s('ellipse', { cx: x, cy: 88 * u + 44 + 22, rx: 5, ry: 3.5, 'class': 'hw-screw' }, svg); });
      }
    } else {
      s('rect', { x: pad, y: pad, width: side.w, height: side.h, rx: face === 'front' ? 16 : 8, 'class': 'hw-body' }, svg);
    }
    side.items.forEach(function (it) {
      if (it.t === 'zone') {
        s('rect', { x: it.x, y: it.y, width: it.w, height: it.h, rx: 6, 'class': 'hw-zone' }, svg);
        s('text', { x: it.x + 8, y: it.y + 14, 'class': 'hw-cap' }, svg, it.label);
      } else if (it.t === 'text') {
        if (it.label) s('text', { x: it.x, y: it.y, 'text-anchor': it.anchor || 'start', 'class': it.cls }, svg, it.label);
      } else if (it.t === 'lcd') {
        var lg = s('g', { 'class': 'hw-lcd', transform: 'translate(' + it.x + ',' + it.y + ')' }, svg);
        s('rect', { width: it.w, height: it.h, rx: 3 }, lg);
        var lines = lcdLines(d, it.key), lh = Math.min(20, (it.h - 8) / Math.max(lines.length, 1));
        lines.forEach(function (ln, i) {
          var y = 8 + lh * (i + 0.75);
          s('text', { x: 8, y: y, 'class': 'hw-lcd-k' }, lg, ln[0]);
          var v = String(ln[1] || '');
          s('text', { x: 52, y: y, 'class': 'hw-lcd-v' }, lg, v.length > 16 ? v.slice(0, 15) + '…' : v);
        });
      } else if (it.t === 'slot') {
        var sg = s('g', { transform: 'translate(' + it.x + ',' + it.y + ')', 'class': 'hw-slot' + (d.state.mode === 'rec' && it.key === 'sd1' ? ' rec' : '') }, svg);
        s('rect', { width: it.w, height: it.h, rx: 2 }, sg);
        s('text', { x: it.w + 6, y: it.h - 3, 'class': 'hw-cap' }, sg, it.label);
        s('title', {}, sg, it.label + ': ' + (d.state[it.key] || ''));
      } else if (it.t === 'knob') {
        var kg = s('g', { transform: 'translate(' + it.x + ',' + it.y + ')', 'class': 'hw-knob' }, svg);
        s('circle', { r: it.r }, kg); s('circle', { r: it.r * 0.35, 'class': 'hw-hole' }, kg);
        s('title', {}, kg, it.why || '');
      } else if (it.t === 'btn') drawButton(svg, d, it);
      else if (it.t === 'port') drawPort(svg, it, portInfo(d, it), it.compact);
    });
    if (opts.selPort) {
      var sel = svg.querySelector('[data-port="' + cssEsc(opts.selPort) + '"]');
      if (sel) sel.classList.add('sel');
    }
    if (!d.connected) svg.classList.add('offline');
    return svg;
  }
  function cssEsc(v) { return String(v).replace(/[^a-z0-9_-]/gi, ''); }

  /* ---------- acting on a press (the mockup changes its own state; the page would call the API) ---------- */
  var listeners = [];
  function changed(d, what) { listeners.forEach(function (fn) { fn(d, what); }); }
  function press(d, act) {
    var a = act.split(':'), st = d.state, said = '';
    switch (a[0]) {
      case 'pgm': st.pgm = +a[1]; said = 'Program: ' + st.inputs[st.pgm]; break;
      case 'pvw': st.pvw = +a[1]; said = 'Preview: ' + st.inputs[st.pvw]; break;
      case 'cut': case 'auto': var t = st.pgm; st.pgm = st.pvw; st.pvw = t; if (st.dsktie) st.dsk = !st.dsk; said = (a[0] === 'cut' ? 'Cut' : 'Auto') + ' to ' + st.inputs[st.pgm]; break;
      case 'ftb': st.ftb = !st.ftb; said = st.ftb ? 'Faded to black' : 'Back from black'; break;
      case 'dskon': st.dsk = !st.dsk; said = 'Downstream key ' + (st.dsk ? 'on air' : 'off'); break;
      case 'dsktie': st.dsktie = !st.dsktie; said = 'DSK tie ' + (st.dsktie ? 'on' : 'off'); break;
      case 'macro': said = 'Ran macro ' + a[1] + (st.macros && st.macros[+a[1] - 1] ? ': ' + st.macros[+a[1] - 1] : ''); break;
      case 'rec': st.mode = 'rec'; said = 'Recording'; break;
      case 'stop': st.mode = 'stop'; said = 'Stopped'; break;
      case 'play': st.mode = 'play'; said = 'Playing'; break;
      case 'dst': st.sel = st.sel === +a[1] ? null : +a[1]; st.pending = null; said = st.sel ? 'Output ' + st.sel + ' chosen: click an input on the back or below' : ''; break;
      case 'src': if (st.sel) { st.pending = +a[1]; said = 'Next: ' + st.inputs[st.pending - 1] + ' to ' + st.outputs[st.sel - 1] + '. Press TAKE.'; } break;
      case 'take':
        if (st.sel && st.pending) {
          if (st.locks[st.sel] === 'L') { said = 'Output ' + st.sel + ' is locked by another panel'; break; }
          st.routes[st.sel - 1] = st.pending; said = st.outputs[st.sel - 1] + ' now shows ' + st.inputs[st.pending - 1]; st.pending = null;
        } else said = 'Choose an output, then an input'; break;
      case 'lock': if (st.sel) { st.locks[st.sel] = st.locks[st.sel] ? undefined : 'O'; if (!st.locks[st.sel]) delete st.locks[st.sel]; said = 'Output ' + st.sel + (st.locks[st.sel] ? ' locked' : ' unlocked'); } break;
    }
    changed(d, said);
    return said;
  }

  /* ---------- the tooltip and inspector ---------- */
  var tip;
  function showTip(evt, info) {
    if (!tip) tip = h('div', 'hw-tip', document.body);
    tip.textContent = '';
    h('b', '', tip, info.title);
    var dl = h('dl', '', tip);
    [['Type', info.kind + ' · ' + info.dir], ['Label', info.label], ['Signal', info.signal || 'No signal'], ['Now', info.routed], ['Cable', info.feeds], ['Note', info.note]]
      .forEach(function (r) { if (!r[1]) return; h('dt', '', dl, r[0]); h('dd', r[0] === 'Signal' && !info.signal ? 'muted' : '', dl, r[1]); });
    tip.style.display = 'block';
    var x = Math.min(evt.clientX + 14, window.innerWidth - 300), y = evt.clientY + 16;
    if (y + 180 > window.innerHeight) y = evt.clientY - 180;
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  }
  function hideTip() { if (tip) tip.style.display = 'none'; }

  /* Fills a container with the details of one port and a note field. onNote(text) saves. */
  function inspector(box, d, pid) {
    box.textContent = '';
    if (!d || !pid) { h('p', 'muted', box, 'Hover a connector for its details. Click one to pin it here and add a note about what is plugged in.'); return; }
    var p = findPort(d, pid); if (!p) return;
    var info = portInfo(d, p);
    var hd = h('div', 'between', box); h('b', '', hd, info.title);
    h('span', 'badge ' + (info.live ? 'ok' : 'muted'), hd, info.live ? 'Signal' : 'No signal');
    var dl = h('dl', 'kv', box);
    [['Connector', info.kind + ' · ' + info.dir], ['Label on the device', info.label || '—'], ['Signal', info.signal || 'Nothing detected'], ['Now', info.routed || '—'], ['Cable', info.feeds || 'Not recorded']]
      .forEach(function (r) { h('dt', '', dl, r[0]); h('dd', '', dl, r[1]); });
    var f = h('div', 'field', box);
    var lab = h('label', '', f, 'Note for the crew'); lab.htmlFor = 'note-' + info.key.replace(/[^a-z0-9]/gi, '-');
    var ta = h('textarea', '', f); ta.id = lab.htmlFor; ta.rows = 2; ta.value = info.note; ta.placeholder = 'What is plugged in here, e.g. "Cam 2 cable, stage left wall plate B3"';
    h('span', 'hint', f, 'Saved with the device in Relay. Shows on hover and on the back-panel view.');
    var c = h('div', 'ctl', box);
    var save = h('button', 'primary sm', c, 'Save note');
    save.type = 'button';
    save.addEventListener('click', function () { if (ta.value.trim()) NOTES[info.key] = ta.value.trim(); else delete NOTES[info.key]; changed(d, 'Note saved for ' + info.title); });
    if (model(d).family === 'videohub' && p.input) {
      var r = h('button', 'sm', c, d.state.sel ? 'Route to output ' + d.state.sel : 'Choose an output first');
      r.type = 'button'; r.disabled = !d.state.sel;
      r.addEventListener('click', function () { press(d, 'src:' + p.input); });
    }
  }

  /* Wires hover, click and press handling onto an svg drawn by draw(). onPort(dev, portId) is called on click. */
  function wire(svg, onPort) {
    var d = dev(svg.dataset.dev);
    svg.addEventListener('mousemove', function (e) {
      var pg = e.target.closest('[data-port]');
      if (!pg) { hideTip(); return; }
      showTip(e, portInfo(d, findPort(d, pg.dataset.port)));
    });
    svg.addEventListener('mouseleave', hideTip);
    function act(e) {
      var pg = e.target.closest('[data-port]');
      if (pg) { hideTip(); if (model(d).family === 'videohub' && d.state.sel) { var p = findPort(d, pg.dataset.port); if (p && p.input) press(d, 'src:' + p.input); } if (onPort) onPort(d, pg.dataset.port); return; }
      var b = e.target.closest('[data-act]');
      if (b && svg.dataset.canControl !== 'no') press(d, b.dataset.act);
    }
    svg.addEventListener('click', act);
    svg.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); act(e); } });
  }

  function toast(text) {
    if (!text) return;
    var t = document.querySelector('.hw-toast') || h('div', 'hw-toast', document.body);
    t.textContent = text; t.classList.add('on');
    clearTimeout(toast.t); toast.t = setTimeout(function () { t.classList.remove('on'); }, 2200);
  }

  /* Top bar and tab switching shared by the three pages, like the Planning Center page. */
  function tabs(barId, onShow) {
    var bar = document.getElementById(barId);
    bar.addEventListener('click', function (e) {
      var b = e.target.closest('button[data-p]'); if (!b) return;
      bar.querySelectorAll('button').forEach(function (x) { x.classList.toggle('on', x === b); x.setAttribute('aria-selected', x === b ? 'true' : 'false'); });
      document.querySelectorAll('.pane').forEach(function (p) { p.classList.toggle('on', p.id === b.dataset.p); });
      if (onShow) onShow(b.dataset.p);
    });
  }

  window.BM = { MODELS: MODELS, DEVICES: DEVICES, CABLES: CABLES, NOTES: NOTES, dev: dev, model: model, draw: draw, wire: wire,
    press: press, portInfo: portInfo, findPort: findPort, inspector: inspector, toast: toast, tabs: tabs, h: h, s: s,
    onChange: function (fn) { listeners.push(fn); } };
})();
