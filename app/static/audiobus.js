/* The Audio bus page (#546, epic #539): the bus switch, its lanes (an input channel patched to each) and its outputs,
   saved together; and the crosspoints, which are core routes drawn by the shared route editor (relayUI.routeEditor), so
   the same routes show on the Network page and anywhere else a lane or an output channel is picked.
   Saving lanes and outputs sends no matrix, so the server leaves the routes alone. Everything from the server is text. */
(function () {
  'use strict';
  const $ = function (id) { return document.getElementById(id); };
  const page = $('page-audiobus');
  if (!page) return;
  const UI = relayUI, el = UI.el;
  const request = UI.client({ cache: 'no-store', session: true });
  const fail = UI.note('abErr');
  const MAX_LANES = 32;
  const STATE = { open: ['on', 'Running'], running: ['on', 'Running'], waiting: ['warn', 'Waiting for the device'],
    error: ['err', 'Stopped'], closed: ['idle', 'Closed'], unpatched: ['idle', 'Not patched'] };
  let info = null, draft = null, editor = null, timer = null;

  function option(value, text, picked) { const o = el('option', '', text); o.value = value; o.selected = value === picked; return o; }
  function select(label, options, value, onChange) {
    const s = el('select'); s.setAttribute('aria-label', label);
    options.forEach(function (o) { s.appendChild(option(o[0], o[1], value)); });
    if (value && !options.some(function (o) { return o[0] === value; })) s.appendChild(option(value, value + ' (not found now)', value));
    s.onchange = function () { onChange(s.value); };
    return s;
  }
  function text(label, value, max, onChange) {
    const i = el('input'); i.value = value || ''; i.maxLength = max; i.setAttribute('aria-label', label);
    i.oninput = function () { onChange(i.value); };
    return i;
  }
  function number(label, value, lo, hi, onChange) {
    const i = el('input'); i.type = 'number'; i.min = lo; i.max = hi; i.value = value; i.setAttribute('aria-label', label);
    i.onchange = function () { onChange(Math.max(lo, Math.min(hi, parseInt(i.value, 10) || lo))); };
    return i;
  }
  function state(s) {
    const st = STATE[s && s.state] || ['idle', s && s.state ? s.state : '—'];
    return el('div', '', UI.pill(st[1], st[0]), s && s.error ? el('div', 'note err', s.error) : null);
  }

  // ---- lanes
  function inputDevices(source) {
    return source === 'portaudio' ? (info.input_devices || []).map(function (d) { return [d.label, d.label]; }) : null;
  }
  function channelsOf(lane) {
    const dev = (info.input_devices || []).filter(function (d) { return d.label === lane.device; })[0];
    const n = Math.min(MAX_LANES, dev ? dev.channels : 2), out = [['mix', 'Mix of all']];
    for (let c = 0; c < n; c++) out.push([String(c), 'Channel ' + (c + 1)]);
    return out;
  }
  // One line per lane: its controls side by side, wrapping on a narrow screen (.row), each with its label above.
  function line(parts) {
    const r = el('div', 'row notice');
    parts.forEach(function (p) { r.appendChild(p[0] === null ? p[1] : UI.field(p[0], p[1])); });
    return r;
  }
  function drawLanes() {
    const box = $('abLanes'); box.textContent = '';
    if (!draft.lanes.length) { box.appendChild(el('p', 'note', 'No lanes yet.')); return; }
    const live = {}; ((info.status && info.status.lanes) || []).forEach(function (l) { live[l.lane] = l; });
    draft.lanes.forEach(function (l, k) {
      const used = draft.lanes.map(function (x) { return x.lane; });
      const nums = []; for (let n = 1; n <= MAX_LANES; n++) if (n === l.lane || used.indexOf(n) < 0) nums.push([String(n), String(n)]);
      const devs = inputDevices(l.source);
      box.appendChild(line([
        ['Lane', select('Lane number', nums, String(l.lane), function (v) { l.lane = +v; drawLanes(); })],
        ['Name', text('Lane ' + l.lane + ' name', l.name, 40, function (v) { l.name = v; })],
        ['Input from', select('Input for lane ' + l.lane, (info.sources || []).map(function (s) { return [s.id, s.label]; }), l.source,
          function (v) { l.source = v; l.device = null; drawLanes(); })],
        ['Device', devs ? select('Device for lane ' + l.lane, [['', 'Default input']].concat(devs), l.device || '', function (v) { l.device = v || null; drawLanes(); })
          : text('Device for lane ' + l.lane, l.device, 200, function (v) { l.device = v || null; })],
        ['Channel', select('Channel for lane ' + l.lane, channelsOf(l), String(l.channel), function (v) { l.channel = v === 'mix' ? 'mix' : +v; })],
        ['State', state(live[l.lane])],
        [null, UI.button('Remove', function () { draft.lanes.splice(k, 1); drawLanes(); }, 'sm quiet')]]));
    });
  }

  // ---- outputs
  function drawOuts() {
    const box = $('abOuts'); box.textContent = '';
    if (!draft.outputs.length) { box.appendChild(el('p', 'note', 'No outputs yet.')); return; }
    const live = {}; ((info.status && info.status.outputs) || []).forEach(function (o) { live[o.id] = o; });
    draft.outputs.forEach(function (o, k) {
      const devs = o.output === 'portaudio' ? (info.output_devices || []).map(function (d) { return [d.label, d.label + ' (' + d.channels + ' ch)']; }) : null;
      const who = o.name || o.id || 'the output';
      box.appendChild(line([
        ['Id', text('Output id', o.id, 24, function (v) { o.id = v; })],
        ['Name', text('Name for ' + who, o.name, 40, function (v) { o.name = v; })],
        ['Output to', select('Output for ' + who, (info.outputs || []).filter(function (x) { return x.id !== 'bus'; })
          .map(function (x) { return [x.id, x.label]; }), o.output, function (v) { o.output = v; o.device = null; drawOuts(); })],
        ['Device', devs ? select('Device for ' + who, [['', 'Default output']].concat(devs), o.device || '', function (v) { o.device = v || null; })
          : text('Device for ' + who, o.device, 200, function (v) { o.device = v || null; })],
        ['Channels', number('Channels of ' + who, o.channels, 1, MAX_LANES, function (v) { o.channels = v; })],
        ['Latency (ms)', number('Latency in ms of ' + who, o.latency_ms, 20, 500, function (v) { o.latency_ms = v; })],
        ['State', state(live[o.id])],
        [null, UI.button('Remove', function () { draft.outputs.splice(k, 1); drawOuts(); }, 'sm quiet')]]));
    });
  }

  function drawState() {
    const st = info.status || {};
    const box = $('abState'); box.textContent = '';
    box.replaceWith(Object.assign(UI.pill(st.running ? 'Running' : 'Off', st.running ? 'on' : 'idle'), { id: 'abState' }));
  }
  function drawAll() {
    drawState(); drawLanes(); drawOuts();
    if (!editor) editor = UI.routeEditor($('abMatrix'), { kind: 'audio', provider: 'audiobus', perm: 'audio.manage',
      empty: 'Add an output and save to route lanes into it.' });
    else editor.reload();
  }
  function fromConfig(cfg) {
    return { lanes: (cfg.lanes || []).map(function (l) { return Object.assign({}, l); }),
      outputs: (cfg.outputs || []).map(function (o) { return Object.assign({}, o); }) };
  }

  async function load() {
    try { info = await request('GET', '/api/admin/audiobus'); fail(''); }
    catch (e) { fail(e.message); return; }
    draft = fromConfig(info.config);
    drawAll();
  }
  async function refreshState() {
    // The lanes' and outputs' states only: the rows being edited are left as they are.
    if (page.hidden || document.hidden || !info) return;
    try {
      const next = await request('GET', '/api/admin/audiobus');
      info.status = next.status; drawState();
      const a = document.activeElement;
      if (!(a && page.contains(a) && (a.tagName === 'INPUT' || a.tagName === 'SELECT'))) { drawLanes(); drawOuts(); }
    } catch (e) { /* the page keeps what it shows */ }
  }

  $('abAddLane').onclick = function () {
    const used = draft.lanes.map(function (l) { return l.lane; });
    let n = 1; while (n <= MAX_LANES && used.indexOf(n) >= 0) n++;
    if (n > MAX_LANES) { fail('All 32 lanes are patched.'); return; }
    draft.lanes.push({ lane: n, name: '', source: 'portaudio', device: null, channel: 0 });
    drawLanes();
  };
  $('abRescan').onclick = async function () {
    fail(''); UI.say('abSaved', '');
    try {
      const body = await request('GET', '/api/admin/devices');      // probes the sound devices again (not while a capture holds them)
      await load();
      drawLanes(); drawOuts();
      UI.say('abSaved', body.note || 'Devices rescanned.', false, { ms: 4000 });
    } catch (e) { fail(e.message); }
  };
  $('abAddOut').onclick = function () {
    let n = draft.outputs.length + 1; while (draft.outputs.some(function (o) { return o.id === 'out' + n; })) n++;
    draft.outputs.push({ id: 'out' + n, name: '', output: 'portaudio', device: null, channels: 2, latency_ms: 60 });
    drawOuts();
  };
  $('abSave').onclick = async function () {
    fail(''); UI.say('abSaved', '');
    try {
      const got = await request('POST', '/api/admin/audiobus', { lanes: draft.lanes, outputs: draft.outputs });
      info.config = got.config; info.status = got.status;
      draft = fromConfig(got.config);
      drawAll();
      UI.say('abSaved', 'Saved.', false, { ms: 3000 });
    } catch (e) { fail(e.message); }
  };

  page.addEventListener('relay:tab', function () {
    load();
    if (!timer) timer = setInterval(refreshState, 2000);
  });
  if (!page.hidden) page.dispatchEvent(new CustomEvent('relay:tab', { detail: '' }));
})();
