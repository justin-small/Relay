/* The Blackmagic admin page (#243, #386, #432, #433, the rack redesign): every device drawn front and back
   (bmdraw.js), in four tabs: the rack, the wiring between devices, every connector with its note, and finding and
   adding gear. Loaded by admin.html only while the module is on, after bmdraw.js. */
(function () {
  const R = window.RelayAdmin;
  const D = window.BMDraw;
  if (!R || !D || !document.getElementById('page-blackmagic')) return;
  const $ = R.$;
  const el = relayUI.el;
  const can = relayUI.can;
  const send = relayUI.api;
  const KINDS = { atem: 'ATEM switcher', hyperdeck: 'HyperDeck', videohub: 'Videohub' };
  const DEFAULT_PORT = { atem: 9910, hyperdeck: 9993, videohub: 9990 };
  function showError(text) { $('bmErr').textContent = text || ''; $('bmErr').hidden = !text; }
  const button = relayUI.buttons(showError);
  const field = relayUI.field;

  let state = { devices: [], cables: [] };
  let last = '';
  let face = 'front';
  let cur = null;             // the selected device id
  let sel = null;             // the selected connector on the rack: [device, port]
  let wsel = null;            // the selected connector on the Wiring tab
  let picking = null;         // adding a cable: null, [] (waiting for the first end) or [device, port]
  let hot = null;             // the connector under the pointer on the Wiring tab
  const meOf = {};            // which M/E each switcher's drawing shows
  const hubUI = {};           // each Videohub's chosen output and input, both from 1

  // ------------------------------------------------------------------------------------------- what is drawn
  function views() {
    const out = {};
    state.devices.forEach(function (d) { out[d.id] = D.view(d, meOf[d.id] || 0); });
    return out;
  }
  function ctx(vs) { return { views: vs, cables: state.cables || [] }; }
  function placeOf(d, v) { return d.place || (D.spec(v).form === 'desk' ? 'shelf' : 'rack'); }
  function device(id) { return state.devices.find(function (d) { return d.id === id; }) || null; }
  function hubState(id) { return hubUI[id] || (hubUI[id] = { out: null, inp: null }); }
  function legend(box, which) {
    box.textContent = '';
    const items = which === 'front' ? [['pgm', 'Program / recording'], ['pvw', 'Preview / playing'], ['dis', 'Not from Relay']]
      : [['ok', 'In use now'], ['warn', 'Cabled'], ['info', 'Has a note']];
    items.forEach(function (x) { box.append(el('span', {}, el('i', x[0]), x[1])); });
  }
  const BADGE = { on: 'ok', idle: 'muted', err: 'err', warn: 'warn' };
  function status(d) {
    if (!d.enabled) return ['switched off', 'idle'];
    if (d.connected) return ['Connected', 'on'];
    return [d.error || 'Connecting…', d.error ? 'err' : 'warn'];
  }

  // ------------------------------------------------------------------------------------------- pressing the front
  async function post(path, body) {
    showError('');
    try { await send('POST', '/api/admin/blackmagic/' + path, body); } catch (e) { showError(e.message); }
    setTimeout(refresh, 250);
  }
  function press(d, v, act) {
    const a = act.split(':');
    if (d.kind === 'atem') {
      const me = v.me;
      if (a[0] === 'pgm') return post('program', { device: d.id, me: me, source: +a[1] });
      if (a[0] === 'pvw') return post('preview', { device: d.id, me: me, source: +a[1] });
      if (a[0] === 'cut') return post('cut', { device: d.id, me: me });
      if (a[0] === 'auto') return post('auto', { device: d.id, me: me });
      if (a[0] === 'ftb') return post('fade-to-black', { device: d.id, me: me });
      if (a[0] === 'dsk') return post('downstream-key', { device: d.id, keyer: +a[1], action: 'auto' });
      if (a[0] === 'macro') return post('macro', { device: d.id, slot: +a[1], action: 'run' });
    } else if (d.kind === 'hyperdeck') {
      return post('hyperdeck/' + a[0], { device: d.id });
    } else {
      const ui = hubState(d.id);
      if (a[0] === 'dst') { ui.out = ui.out === +a[1] ? null : +a[1]; ui.inp = null; cur = d.id; return all(); }
      if (a[0] === 'take') {
        if (!ui.out || !ui.inp) { showError('Press an output button, pick an input, then TAKE.'); return; }
        if (v.locks[ui.out] === 'L') { showError('Output ' + ui.out + ' is locked by another control panel.'); return; }
        const o = ui.out, i = ui.inp;
        ui.inp = null;
        return post('videohub/route', { device: d.id, output: o - 1, input: i - 1 });
      }
      if (a[0] === 'lock') {
        if (!ui.out) { showError('Press an output button first.'); return; }
        if (v.locks[ui.out] === 'L') { showError('Another control panel holds that lock.'); return; }
        return post('videohub/lock', { device: d.id, output: ui.out - 1, action: v.locks[ui.out] === 'O' ? 'unlock' : 'lock' });
      }
    }
  }
  function chooseInput(d, n) {
    const ui = hubState(d.id);
    if (!ui.out) return false;
    ui.inp = n;
    return true;
  }

  // ------------------------------------------------------------------------------------------- the rack
  function drawRack(vs) {
    const box = $('bmRack');
    box.textContent = '';
    legend($('bmLegend'), face);
    if (!state.devices.length) { box.append(el('div', { className: 'bmempty', text: 'No devices yet. Add one in Find and add.' })); return; }
    const control = can('blackmagic.control'), manage = can('blackmagic.manage');
    const shelf = el('div', 'bmshelf', el('span', { className: 'bmcap', text: 'Shelf' }));
    state.devices.forEach(function (d) {
      const v = vs[d.id];
      const unit = el('div', 'bmunit' + (d.id === cur ? ' on' : ''), el('span', { className: 'bmcap', text: d.name + ' · ' + (d.product || D.NAMES[v.drawing]) }));
      const svg = D.draw(v, face, { control: control && d.id === cur, manage: manage, ui: hubState(d.id), ctx: ctx(vs),
        sel: sel && sel[0] === d.id ? sel[1] : null });
      unit.append(svg);
      D.wire(svg, v, ctx(vs), {
        port: function (pid) {
          const p = D.findPort(v, pid);
          if (d.kind === 'videohub' && p && p.input && d.id === cur) chooseInput(d, p.input);
          cur = d.id; sel = [d.id, pid]; all();
        },
        act: function (act) { if (d.id === cur) press(d, v, act); }
      });
      unit.addEventListener('click', function () { if (cur !== d.id) { cur = d.id; sel = null; all(); } });
      (placeOf(d, v) === 'shelf' ? shelf : box).append(unit);
    });
    if (shelf.children.length > 1) box.append(shelf);
  }

  function inspector(box, vs, end, opts) {
    const d = end && device(end[0]);
    const v = d && vs[d.id];
    const p = v && D.findPort(v, end[1]);
    if (!p) {
      box.append(el('p', { className: 'muted', text: 'Hover a connector for its details. Click one to pin it here and add a note about what is plugged in.' }));
      return;
    }
    const info = D.portInfo(v, p, ctx(vs));
    box.append(el('div', 'bmbetween', el('b', { text: info.title }), el('span', { className: 'badge ' + (info.live ? 'ok' : 'muted'), text: info.live ? 'In use' : 'Idle' })));
    const dl = el('dl', 'bmkv');
    [['Connector', info.kind + ' · ' + info.dir], ['Label on the device', info.label || '—'], ['Format', info.format], ['Now', info.now || '—'],
      ['Cable', info.cable || 'Not recorded']].forEach(function (r) { relayUI.kv(dl, r[0], r[1]); });
    box.append(dl);
    const manage = can('blackmagic.manage');
    if (manage) {
      const ta = el('textarea', { rows: 2, value: info.note, maxLength: 120,
        placeholder: 'What is plugged in here, e.g. "Cam 2 cable, stage left wall plate B3"' });
      box.append(field('Note for the crew', ta), el('span', { className: 'hint', text: 'Kept with the device in Relay. Shows on hover and in Ports and notes.' }));
      const acts = el('div', 'ctl');
      acts.append(button('Save note', function () { return saveNote(d.id, p.id, ta.value); }, 'primary sm'));
      if (info.cableId) acts.append(button('Remove cable', function () { return removeCable(info.cableId); }, 'sm'));
      box.append(acts);
    } else if (info.note) box.append(el('p', { className: 'note', text: info.note }));
    if (opts && opts.route && d.kind === 'videohub' && p.input && can('blackmagic.control')) {
      const ui = hubState(d.id);
      box.append(el('div', 'ctl', button(ui.out ? 'Route to output ' + ui.out : 'Press an output first', function () {
        if (chooseInput(d, p.input)) return press(d, v, 'take');
      }, 'sm')));
    }
  }
  async function saveNote(did, pid, text) {
    draw(await send('POST', '/api/admin/blackmagic/notes', { device: did, port: pid, text: text.trim() }));
  }
  async function removeCable(id) {
    draw(await send('DELETE', '/api/admin/blackmagic/cables/' + encodeURIComponent(id)));
  }

  function drawDrawer(vs) {
    const box = $('bmDrawer');
    box.textContent = '';
    const d = device(cur);
    if (!d) return;
    const v = vs[d.id];
    const top = el('section');
    const st = status(d);
    top.append(el('div', 'bmbetween bmhead', el('b', { text: d.name }), el('span', { className: 'badge ' + BADGE[st[1]], text: st[0] })));
    top.append(el('p', { className: 'muted', text: (d.product || KINDS[d.kind]) + ' · ' + d.host }));
    if (d.refound) top.append(el('div', { className: 'note ok', text: d.refound }));
    if (face === 'back') top.append(el('p', { className: 'hint', text: 'The back of the rack: each connector shows what Relay knows is on it, the label the device reports, its cable and your note. Click one to read it here.' }));
    else if (d.connected) {
      if (d.kind === 'atem') atemPanel(d, v, top);
      else if (d.kind === 'hyperdeck') deckPanel(d, v, top);
      else hubPanel(d, v, top);
    }
    if (can('blackmagic.manage')) top.append(settings(d));
    box.append(top);
    const insp = el('section');
    inspector(insp, vs, sel && sel[0] === d.id ? sel : null, { route: true });
    box.append(insp);
  }

  function atemPanel(d, v, box) {
    const control = can('blackmagic.control');
    box.append(el('p', { className: 'hint', text: 'Press PROGRAM to cut a source on air, PREVIEW to line it up, then CUT or AUTO. Dimmed buttons are done on the switcher.' }));
    if (d.old_firmware) box.append(el('div', { className: 'note warn', text: 'This switcher runs firmware older than 8.0.1. Cuts and tally work; update it with ATEM Setup for full support.' }));
    if ((d.mix_effects || []).length > 1) {
      const pick = el('select');
      d.mix_effects.forEach(function (m) { pick.append(el('option', { value: m.me, text: 'M/E ' + (m.me + 1), selected: m.me === v.me })); });
      pick.onchange = function () { meOf[d.id] = +pick.value; all(); };
      box.append(field('The panel shows', pick));
    }
    if (control) {
      box.append(el('div', 'bmgap', button('Cut', function () { return press(d, v, 'cut'); }, 'primary'),
        button('Auto', function () { return press(d, v, 'auto'); }), button('Fade to black', function () { return press(d, v, 'ftb'); })));
    }
    const macros = el('div', 'bmgap');
    if (!(d.macros || []).length) macros.append(el('span', { className: 'muted', text: 'None recorded on the switcher.' }));
    (d.macros || []).forEach(function (m) {
      const label = m.name || 'Macro ' + (m.slot + 1);
      macros.append(control ? button(label, function () { return post('macro', { device: d.id, slot: m.slot, action: 'run' }); }, 'sm')
        : el('span', { className: 'badge', text: label }));
    });
    if ((d.macros || []).length && control) macros.append(button('Stop macro', function () { return post('macro', { device: d.id, slot: 0, action: 'stop' }); }, 'sm quiet'));
    box.append(el('div', 'field', el('label', { text: 'Macros' }), macros));
    // DSK 1 and 2 on the front take a key with its transition; putting one straight on or off air is here.
    if (control && d.downstream_keyers) {
      const keys = el('details', {}, el('summary', { text: 'Downstream keys' }));
      for (let k = 0; k < d.downstream_keyers; k++) {
        keys.append(el('div', 'bmgap', el('span', { className: 'muted', text: 'Key ' + (k + 1) }),
          button('On air', function () { return post('downstream-key', { device: d.id, keyer: k, action: 'on' }); }, 'sm'),
          button('Off air', function () { return post('downstream-key', { device: d.id, keyer: k, action: 'off' }); }, 'sm')));
      }
      box.append(keys);
    }
  }

  const DECK = { record: 'Recording', stop: 'Stopped', stopped: 'Stopped', play: 'Playing', preroll: 'Getting ready',
    forward: 'Fast forward', rewind: 'Rewinding', jog: 'Jogging', shuttle: 'Shuttling' };
  function minutes(seconds) {
    if (!seconds) return 'full or unknown';
    const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60);
    return h ? h + ' h ' + m + ' min left' : Math.max(1, m) + ' min left';
  }
  function deckPanel(d, v, box) {
    const h = d.hyperdeck || {};
    const dl = el('dl', 'bmkv');
    relayUI.kv(dl, 'State', DECK[h.status] || 'Unknown');
    relayUI.kv(dl, 'Clip', h.clip && h.clip.name ? h.clip.id + '. ' + h.clip.name : '');
    relayUI.kv(dl, 'Timecode', h.timecode);
    relayUI.kv(dl, 'Format', h.video_format);
    (h.slots || []).forEach(function (s) {
      relayUI.kv(dl, 'Slot ' + s.id, s.status === 'mounted' ? (s.volume || 'Disk') + ', recording time ' + minutes(s.seconds_left) : (s.status === 'empty' ? 'No disk' : s.status || '—'));
    });
    box.append(dl);
    if (h.notice) box.append(el('div', { className: 'note warn', text: h.notice }));
    if (h.remote_enabled === false) box.append(el('div', { className: 'note warn', text: 'Remote control is turned off on the HyperDeck, so Relay can look but not control it.' }));
    if (can('blackmagic.control')) {
      box.append(el('div', 'bmgap', button('Record', function () { return post('hyperdeck/record', { device: d.id }); }, h.status === 'record' ? '' : 'primary'),
        button('Stop', function () { return post('hyperdeck/stop', { device: d.id }); }), button('Play', function () { return post('hyperdeck/play', { device: d.id }); })));
    }
    const clips = (h.clips || []).slice(-8);
    if (clips.length) {
      const list = el('details', {}, el('summary', { text: 'Last clips on the current disk' }));
      clips.forEach(function (c) { list.append(el('div', { className: 'mono', text: c.id + '. ' + c.name + '  ' + c.duration })); });
      box.append(list);
    }
  }

  function portName(p, kind) { return p.label || (kind + ' ' + (p.n + 1)); }
  function hubPanel(d, v, box) {
    const h = d.videohub || {};
    const control = can('blackmagic.control'), manage = can('blackmagic.manage');
    const ui = hubState(d.id);
    if (h.notice) box.append(el('div', { className: 'note warn', text: h.notice }));
    if (h.present === false) box.append(el('div', { className: 'note warn', text: 'The router says its video hardware is not present.' }));
    box.append(el('p', { className: 'hint', text: 'Press an output on the front, choose an input here or on the back, then TAKE.' }));
    if (control) {
      const pick = el('select', { disabled: !ui.out });
      pick.append(el('option', { value: '', text: ui.out ? 'Choose an input for output ' + ui.out : 'Press an output first' }));
      (h.inputs || []).forEach(function (i) { pick.append(el('option', { value: i.n + 1, text: (i.n + 1) + '. ' + portName(i, 'Input'), selected: ui.inp === i.n + 1 })); });
      pick.onchange = function () { if (pick.value) { chooseInput(d, +pick.value); all(); } };
      box.append(field('Input', pick));
    }
    const presets = el('div', 'bmgap');
    (d.presets || []).forEach(function (p) {
      if (control) {
        presets.append(button(p.name, async function () {
          const res = await send('POST', '/api/admin/blackmagic/videohub/presets/apply', { device: d.id, preset: p.id });
          if (res.left_out) showError(res.left_out + ' output(s) were left out: locked by another surface or gone.');
          setTimeout(refresh, 250);
        }, 'sm'));
      } else presets.append(el('span', { className: 'badge', text: p.name }));
    });
    if (!(d.presets || []).length) presets.append(el('span', { className: 'muted', text: 'None saved yet.' }));
    box.append(el('div', 'field', el('label', { text: 'Presets' }), presets));
    const more = el('details', {}, el('summary', { text: 'All routes, presets and labels' }));
    allRoutes(d, h, more, control, manage);
    box.append(more);
  }
  function allRoutes(d, h, box, control, manage) {
    const body = el('tbody');
    (h.outputs || []).forEach(function (o) {
      const pick = el('select', { disabled: !control || o.lock === 'L' });
      (h.inputs || []).forEach(function (i) { pick.append(el('option', { value: i.n, text: (i.n + 1) + '. ' + portName(i, 'Input'), selected: i.n === o.input })); });
      pick.setAttribute('aria-label', 'Input for ' + portName(o, 'Output'));
      pick.onchange = function () { return post('videohub/route', { device: d.id, output: o.n, input: +pick.value }); };
      const lock = el('td');
      if (o.lock === 'L') lock.append(el('span', { className: 'badge warn', text: 'Another panel' }));
      else if (manage) lock.append(button(o.lock === 'O' ? 'Unlock' : 'Lock', function () {
        return post('videohub/lock', { device: d.id, output: o.n, action: o.lock === 'O' ? 'unlock' : 'lock' });
      }, 'sm'));
      body.append(el('tr', {}, el('td', { text: (o.n + 1) + '. ' + portName(o, 'Output') }), el('td', {}, pick), lock));
    });
    box.append(el('div', 'tablescroll', el('table', 'dt', el('thead', {}, el('tr', {}, el('th', { text: 'Output' }), el('th', { text: 'Takes' }), el('th', { text: 'Lock' }))), body)));
    if (manage) {
      (d.presets || []).forEach(function (p) {
        box.append(el('div', 'bmgap', el('span', { text: p.name }), el('span', { className: 'muted', text: Object.keys(p.routes).length + ' outputs' }),
          button('Delete', async function () {
            if (!(await relayUI.confirm('Delete the preset "' + p.name + '"?'))) return;
            draw(await send('DELETE', '/api/admin/blackmagic/videohub/presets/' + encodeURIComponent(d.id) + '/' + encodeURIComponent(p.id)));
          }, 'sm quiet')));
      });
      const name = el('input', { maxLength: 60, placeholder: 'Program to overflow', autocomplete: 'off' });
      box.append(field('Save the current routing as', name), el('div', 'ctl', button('Save preset', async function () {
        draw(await send('POST', '/api/admin/blackmagic/videohub/presets', { device: d.id, name: name.value }));
      }, 'sm')));
      const edits = { input: {}, output: {} };
      const labels = el('details', {}, el('summary', { text: 'Rename inputs and outputs' }));
      ['input', 'output'].forEach(function (kind) {
        (kind === 'input' ? h.inputs || [] : h.outputs || []).forEach(function (p) {
          const inp = el('input', { value: p.label, maxLength: 60, autocomplete: 'off' });
          inp.setAttribute('aria-label', (kind === 'input' ? 'Input ' : 'Output ') + (p.n + 1) + ' label');
          inp.oninput = function () { edits[kind][p.n] = inp.value; };
          labels.append(el('div', 'bmgap', el('span', { className: 'mono', text: (kind === 'input' ? 'In ' : 'Out ') + (p.n + 1) }), inp));
        });
      });
      labels.append(el('div', 'ctl', button('Save labels', async function () {
        for (const kind of ['input', 'output']) {
          const out = {};
          Object.keys(edits[kind]).forEach(function (n) { if (edits[kind][n].trim()) out[n] = edits[kind][n]; });
          if (Object.keys(out).length) await send('POST', '/api/admin/blackmagic/videohub/labels', { device: d.id, kind: kind, labels: out });
        }
        setTimeout(refresh, 400);
      }, 'primary sm')));
      box.append(labels);
    }
  }

  function settings(d) {
    const box = el('details', 'bmsettings', el('summary', { text: 'Settings' }));
    const name = el('input', { value: d.name, maxLength: 40 });
    const host = el('input', { value: d.host, maxLength: 253, spellcheck: false, autocomplete: 'off' });
    const port = el('input', { type: 'number', min: 1, max: 65535, value: d.port });
    const drawing = el('select');
    drawing.append(el('option', { value: '', text: 'Automatic (' + D.NAMES[D.auto(d)] + ')' }));
    Object.keys(D.NAMES).filter(function (k) { return sameKind(d.kind, k); })
      .forEach(function (k) { drawing.append(el('option', { value: k, text: D.NAMES[k], selected: d.drawing === k })); });
    const place = el('select');
    [['', 'Automatic'], ['rack', 'In the rack'], ['shelf', 'On the shelf']].forEach(function (o) { place.append(el('option', { value: o[0], text: o[1], selected: (d.place || '') === o[0] })); });
    box.append(field('Name', name), field('Address', host), field('Port', port), field('Drawing', drawing), field('Where it is drawn', place));
    if (d.version) box.append(el('p', { className: 'hint', text: 'Protocol ' + d.version + (d.uid ? ' · followed by its network id' : '') }));
    const on = el('input', { type: 'checkbox', checked: d.enabled });
    box.append(el('label', 'chk chk-inline', on, ' Connect to this device'));
    let first = null, tally = null;
    if (d.kind === 'atem') {
      first = el('input', { type: 'number', min: 1, max: 901, value: (d.tally_offset || 0) + 1 });
      tally = el('input', { type: 'checkbox', checked: !!d.tally });
      box.append(el('label', 'chk chk-inline', tally, ' Drives the tally lights'), field('First camera number', first),
        el('span', { className: 'hint', text: 'Camera 1 on this switcher shows as this number on the tally screens. Give each switcher that drives tally its own range, like 1 and 101.' }));
    }
    box.append(el('div', 'ctl',
      button('Save', function () {
        const changes = { name: name.value, host: host.value, port: +port.value || DEFAULT_PORT[d.kind], drawing: drawing.value, place: place.value, enabled: on.checked };
        if (d.kind === 'atem') { changes.tally = tally.checked; changes.tally_offset = Math.max(0, (+first.value || 1) - 1); }
        return save(d, changes);
      }, 'primary sm'),
      button('Remove', async function () {
        if (!(await relayUI.confirm('Remove ' + d.name + ' from Relay? The device itself is not changed.'))) return;
        cur = null; sel = null;
        draw(await send('DELETE', '/api/admin/blackmagic/devices/' + encodeURIComponent(d.id)));
      }, 'sm danger')));
    return box;
  }
  function sameKind(kind, drawingId) {
    if (kind === 'hyperdeck') return drawingId === 'hyperdeck-studio';
    if (kind === 'videohub') return drawingId === 'videohub';
    return drawingId !== 'hyperdeck-studio' && drawingId !== 'videohub';
  }
  async function save(d, changes) {
    showError('');
    draw(await send('PUT', '/api/admin/blackmagic/devices/' + encodeURIComponent(d.id), changes));
  }

  // ------------------------------------------------------------------------------------------- wiring
  function drawWiring(vs) {
    const w = $('bmWire');
    w.textContent = '';
    if (!state.devices.length) { w.append(el('div', { className: 'bmempty', text: 'No devices yet.' })); return; }
    const racked = state.devices.filter(function (d) { return placeOf(d, vs[d.id]) !== 'shelf'; })
      .concat(state.devices.filter(function (d) { return placeOf(d, vs[d.id]) === 'shelf'; }));
    racked.forEach(function (d) {
      const v = vs[d.id];
      const row = el('div', 'bmrow', el('div', {}, el('b', { text: d.name }), el('small', { text: (d.product || D.NAMES[v.drawing]) + (placeOf(d, v) === 'shelf' ? ' · shelf' : '') })));
      const svg = D.draw(v, 'back', { ctx: ctx(vs), sel: wsel && wsel[0] === d.id ? wsel[1] : null });
      if (picking && picking.length) {
        const p = Array.prototype.find.call(svg.querySelectorAll('[data-port]'), function (n) { return picking[0] === d.id && n.dataset.port === picking[1]; });
        if (p) p.classList.add('pick');
      }
      row.append(svg);
      D.wire(svg, v, ctx(vs), { port: function (pid) { pickPort(d.id, pid); } });
      svg.addEventListener('mouseover', function (e) {
        const p = e.target.closest('[data-port]');
        const k = p ? d.id + ':' + p.dataset.port : null;
        if (k !== hot) { hot = k; highlight(); }
      });
      svg.addEventListener('mouseleave', function () { hot = null; highlight(); });
      w.append(row);
    });
    w.append(D.svgEl('svg', { class: 'bmcables' }));
    cables(vs);
    const insp = $('bmWireInsp');
    insp.textContent = '';
    inspector(insp, vs, wsel);
    chain(vs);
    suggestions(vs);
  }
  async function pickPort(did, pid) {
    if (picking) {
      if (!picking.length) { picking = [did, pid]; cableNote('Now click the other end of the cable.'); drawWiring(views()); return; }
      const a = picking;
      picking = null;
      cableNote('');
      if (a[0] === did && a[1] === pid) { drawWiring(views()); return; }
      try { draw(await send('POST', '/api/admin/blackmagic/cables', { a: a, b: [did, pid] })); } catch (e) { showError(e.message); drawWiring(views()); }
      return;
    }
    wsel = [did, pid];
    drawWiring(views());
  }
  function cableNote(text) { relayUI.say('bmCableNote', text, false, { plain: true, hide: true }); }
  function centre(did, pid, box) {
    const svg = Array.prototype.find.call(document.querySelectorAll('#bmWire svg.hw'), function (s) { return s.dataset.dev === did; });
    const g = svg && Array.prototype.find.call(svg.querySelectorAll('[data-port]'), function (n) { return n.dataset.port === pid; });
    if (!g) return null;
    const r = g.getBoundingClientRect();
    return { x: r.left + r.width / 2 - box.left, y: r.top + r.height / 2 - box.top - 4 };
  }
  function onProgram(c, vs) {
    return [c.a, c.b].some(function (end) {
      const v = vs[end[0]];
      if (!v) return false;
      if (v.kind === 'atem') return end[1] === 'in' + v.pgm || end[1] === 'out0';
      return v.kind === 'hyperdeck' && v.status === 'record' && end[1] === 'sdiin';
    });
  }
  function cables(vs) {
    const w = $('bmWire'), cs = w.querySelector('svg.bmcables');
    if (!cs || !w.offsetParent) return;
    cs.textContent = '';
    const box = w.getBoundingClientRect();
    (state.cables || []).forEach(function (c) {
      const a = centre(c.a[0], c.a[1], box), b = centre(c.b[0], c.b[1], box);
      if (!a || !b) return;
      const dy = Math.max(40, Math.abs(b.y - a.y) / 2);
      const path = D.svgEl('path', { d: 'M' + a.x + ',' + a.y + ' C' + a.x + ',' + (a.y + dy) + ' ' + b.x + ',' + (b.y - dy) + ' ' + b.x + ',' + b.y }, cs);
      const va = vs[c.a[0]], pa = va && D.findPort(va, c.a[1]);
      const live = pa ? D.portInfo(va, pa, ctx(vs)).live : false;
      path.setAttribute('class', (live ? 'live' : '') + (onProgram(c, vs) ? ' pgm' : ''));
      path.dataset.k = c.a.join(':') + '|' + c.b.join(':');
    });
    highlight();
  }
  function highlight() {
    document.querySelectorAll('#bmWire .bmcables path').forEach(function (p) { p.classList.toggle('hot', !!hot && p.dataset.k.split('|').indexOf(hot) >= 0); });
  }
  /* Camera to recording, following recorded cables back from the switcher's program input and on from its program out. */
  function chain(vs) {
    const box = $('bmChain');
    box.textContent = '';
    const sw = state.devices.find(function (d) { return d.kind === 'atem' && d.connected; });
    if (!sw) { box.append(el('span', { className: 'muted', text: 'No switcher connected.' })); return; }
    const v = vs[sw.id], parts = [];
    const from = (state.cables || []).find(function (c) { return (c.b[0] === sw.id && c.b[1] === 'in' + v.pgm) || (c.a[0] === sw.id && c.a[1] === 'in' + v.pgm); });
    if (from) {
      const other = from.b[0] === sw.id ? from.a : from.b, ov = vs[other[0]];
      if (ov && ov.kind === 'videohub') {
        const o = +other[1].replace('out', ''), src = ov.routes[o - 1];
        if (src) parts.push([ov.inputs[src - 1], '']);
        parts.push([ov.name + ' ' + (src || '?') + ' → ' + o, '']);
      } else if (ov) parts.push([ov.name, '']);
    }
    parts.push([sw.name + ': ' + (v.inputs[v.pgm] || '—'), 'pgm']);
    (state.cables || []).forEach(function (c) {
      const mine = c.a[0] === sw.id && c.a[1] === 'out0' ? c.b : c.b[0] === sw.id && c.b[1] === 'out0' ? c.a : null;
      if (!mine || !vs[mine[0]]) return;
      const ov = vs[mine[0]];
      if (ov.kind === 'videohub') {
        const n = +mine[1].replace('in', '');
        parts.push(['PGM → ' + ov.name + ' in ' + n, '']);
        ov.routes.forEach(function (src, o) {
          if (src !== n) return;
          const down = (state.cables || []).find(function (k) { return (k.a[0] === ov.id && k.a[1] === 'out' + (o + 1)) || (k.b[0] === ov.id && k.b[1] === 'out' + (o + 1)); });
          const end = down && (down.a[0] === ov.id ? down.b : down.a);
          const dv = end && vs[end[0]];
          if (dv) parts.push([dv.name + (dv.kind === 'hyperdeck' && dv.status === 'record' ? ' ● recording' : ''), dv.kind === 'hyperdeck' && dv.status === 'record' ? 'pgm' : '']);
          else parts.push([ov.outputs[o], '']);
        });
      } else parts.push([ov.name + (ov.kind === 'hyperdeck' && ov.status === 'record' ? ' ● recording' : ''), ov.kind === 'hyperdeck' && ov.status === 'record' ? 'pgm' : '']);
    });
    parts.forEach(function (x, i) { if (i) box.append(el('i', { text: '→' })); box.append(el('span', { className: x[1], text: x[0] })); });
  }
  /* A Videohub output whose label names a switcher input ("ATEM 3", or that input's own name), not yet cabled. */
  function suggest(vs) {
    const out = [], taken = {};
    (state.cables || []).forEach(function (c) { taken[c.a.join(':')] = taken[c.b.join(':')] = true; });
    const switchers = state.devices.filter(function (d) { return d.kind === 'atem'; });
    state.devices.filter(function (d) { return d.kind === 'videohub'; }).forEach(function (h) {
      vs[h.id].outputs.forEach(function (label, o) {
        if (taken[h.id + ':out' + (o + 1)]) return;
        const m = /^(?:atem|switcher|sw)\s*(?:in(?:put)?\s*)?(\d{1,2})$/i.exec(label.trim());
        switchers.forEach(function (s) {
          const v = vs[s.id];
          let n = m && switchers.length === 1 ? +m[1] : 0;
          if (!n) Object.keys(v.inputs).forEach(function (k) { if (+k >= 1 && +k <= 40 && v.inputs[k].toLowerCase() === label.trim().toLowerCase()) n = +k; });
          if (n && D.findPort(v, 'in' + n) && !taken[s.id + ':in' + n]) out.push({ a: [h.id, 'out' + (o + 1)], b: [s.id, 'in' + n], text: h.name + ' ' + label + ' → ' + s.name + ' IN ' + n });
        });
      });
    });
    return out;
  }
  function suggestions(vs) {
    const box = $('bmSuggest');
    box.textContent = '';
    const list = suggest(vs);
    if (!list.length) { box.append(el('span', { className: 'muted', text: 'No suggestions right now.' })); return; }
    if (!can('blackmagic.manage')) { box.append(el('span', { className: 'muted', text: list.length + ' suggested; someone who manages Blackmagic can confirm them.' })); return; }
    box.append(button('Confirm ' + list.length + (list.length === 1 ? ' suggestion' : ' suggestions'), async function () {
      let res = null;
      for (const s of list) res = await send('POST', '/api/admin/blackmagic/cables', { a: s.a, b: s.b });
      if (res) draw(res);
    }, 'primary sm'));
    const more = el('details', {}, el('summary', { text: 'Show them' }));
    list.forEach(function (s) { more.append(el('div', { className: 'hint', text: s.text })); });
    box.append(more);
  }

  // ------------------------------------------------------------------------------------------- ports and notes
  let dirty = {};
  function drawPorts(vs) {
    const pick = $('bmPortDev');
    const want = pick.value;
    pick.textContent = '';
    pick.append(el('option', { value: '', text: 'All devices' }));
    state.devices.forEach(function (d) { pick.append(el('option', { value: d.id, text: d.name, selected: d.id === want })); });
    const only = $('bmPortUsed').checked, manage = can('blackmagic.manage');
    const body = $('bmPortRows');
    body.textContent = '';
    state.devices.forEach(function (d) {
      if (want && want !== d.id) return;
      const v = vs[d.id];
      D.spec(v).back.items.forEach(function (p) {
        if (p.t !== 'port') return;
        const i = D.portInfo(v, p, ctx(vs));
        if (only && !i.live && !i.cabled && !i.note) return;
        const note = el('td');
        if (manage) {
          const inp = el('input', { value: i.note, placeholder: 'Add a note', maxLength: 120 });
          inp.setAttribute('aria-label', 'Note for ' + i.title);
          inp.addEventListener('input', function () { dirty[d.id + ':' + p.id] = [d.id, p.id, inp.value]; });
          note.append(inp);
        } else note.textContent = i.note;
        body.append(el('tr', {}, el('td', { text: d.name }), el('td', { className: 'mono', text: p.label }), el('td', { text: i.label || '—' }),
          el('td', {}, el('span', { className: 'badge ' + (i.live ? 'ok' : 'muted'), text: i.format || (i.live ? 'In use' : 'None') })),
          el('td', { className: 'muted', text: i.now || i.cable }), note));
      });
    });
    if (!body.children.length) body.append(el('tr', {}, el('td', { colSpan: 6, className: 'muted', text: 'Nothing to show.' })));
  }

  // ------------------------------------------------------------------------------------------- find and add
  function drawOrder(vs) {
    const body = $('bmOrderRows');
    body.textContent = '';
    const manage = can('blackmagic.manage');
    let n = 0;
    state.devices.forEach(function (d, i) {
      const v = vs[d.id];
      const shelf = placeOf(d, v) === 'shelf';
      if (!shelf) n += 1;
      const handle = el('td', 'bmhandle');
      const acts = el('td');
      if (manage) {
        const up = button('↑', function () { return move(i, -1); }, 'sm quiet'); up.disabled = i === 0; up.setAttribute('aria-label', 'Move ' + d.name + ' up');
        const down = button('↓', function () { return move(i, 1); }, 'sm quiet'); down.disabled = i === state.devices.length - 1; down.setAttribute('aria-label', 'Move ' + d.name + ' down');
        handle.append(up, down);
        acts.append(button('Edit', function () { cur = d.id; sel = null; tab('rack'); all(); const s = $('bmDrawer').querySelector('details.bmsettings'); if (s) s.open = true; }, 'sm'));
      }
      body.append(el('tr', {}, handle, el('td', { text: d.name }), el('td', { text: d.product || D.NAMES[v.drawing] }),
        el('td', { className: 'mono', text: d.host }), el('td', { text: shelf ? 'Shelf' : 'Rack, ' + n }), acts));
    });
    $('bmNone').hidden = state.devices.length > 0;
  }
  /* One card per drawing, drawn from a made-up device of that model. */
  function drawModels() {
    const box = $('bmModels');
    if (box.children.length) return;
    const ins = []; for (let k = 1; k <= 8; k++) ins.push({ id: k, name: 'In ' + k });
    const hubPorts = function (n) { const a = []; for (let k = 0; k < n; k++) a.push({ n: k, label: '', input: k, lock: 'U' }); return a; };
    const samples = [
      { drawing: 'tvs-hd8', kind: 'atem', product: 'ATEM Television Studio HD8' },
      { drawing: 'mini-extreme', kind: 'atem', product: 'ATEM Mini Extreme' },
      { drawing: 'mini-pro', kind: 'atem', product: 'ATEM Mini Pro' },
      { drawing: 'atem-rack', kind: 'atem', product: 'Other ATEM switchers' },
      { drawing: 'hyperdeck-studio', kind: 'hyperdeck', product: 'HyperDeck Studio' },
      { drawing: 'videohub', kind: 'videohub', product: 'Videohub 20x20', size: 20 },
      { drawing: 'videohub', kind: 'videohub', product: 'Smart Videohub 12x12', size: 12 }];
    samples.forEach(function (s, i) {
      const dev = { id: 'sample' + i, name: s.product, kind: s.kind, drawing: s.drawing, product: s.product, connected: true,
        inputs: ins, mix_effects: [{ me: 0, program: 1, preview: 2 }], macros: [], downstream_keyers: 1,
        hyperdeck: { status: 'stopped' }, videohub: { inputs: hubPorts(s.size || 0), outputs: hubPorts(s.size || 0) } };
      const v = D.view(dev, 0), spec = D.spec(v);
      const ports = spec.back.items.filter(function (x) { return x.t === 'port'; }).length;
      const card = el('div', 'bmmodel', el('b', { text: s.product }), D.draw(v, 'front', {}), D.draw(v, 'back', {}),
        el('span', { className: 'muted', text: (spec.form === 'rack' ? spec.u + 'U rack' : 'Desktop') + ' · ' + ports + ' connectors' }));
      box.append(card);
    });
  }
  async function move(i, by) {
    const ids = state.devices.map(function (d) { return d.id; });
    const t = ids[i]; ids[i] = ids[i + by]; ids[i + by] = t;
    draw(await send('POST', '/api/admin/blackmagic/order', { ids: ids }));
  }
  function drawFound(result) {
    const table = $('bmFound');
    const body = table.querySelector('tbody');
    body.textContent = '';
    const found = result.found || [];
    $('bmScanNote').hidden = false;
    $('bmScanNote').textContent = found.length
      ? 'Found ' + found.length + (found.length === 1 ? ' device.' : ' devices.')
      : 'Nothing answered. ' + (result.probed ? 'Check the device is on this network, or add it by its address.'
        : 'Try again with "every address on this subnet", or add it by its address.');
    table.hidden = !found.length;
    found.forEach(function (f) {
      let act;
      if (!f.addable) act = el('span', { className: 'muted', text: 'Not controlled by Relay' });
      else if (f.added) act = el('span', { className: 'ok', text: 'Added' });
      else {
        act = button('Add', async function () {
          const body = { kind: f.family, name: f.name, host: f.address, uid: f.uid, model: f.model };
          if (f.port && f.family === 'atem') body.port = f.port;
          draw(await send('POST', '/api/admin/blackmagic/devices', body));
          f.added = true; drawFound(result);
        }, 'primary');
        if (!f.address) { act.disabled = true; act.title = 'It did not say its address: add it by address below.'; }
      }
      const name = el('td', {}, el('div', { text: f.name }), f.model && f.model !== f.name ? el('div', { className: 'muted', text: f.model }) : null);
      body.append(el('tr', {}, name, el('td', { text: f.kind }), el('td', { className: 'mono', text: f.address || f.host || '—' }),
        el('td', { className: 'mono', text: f.firmware || '—' }), el('td', {}, act)));
    });
  }
  $('bmScan').onclick = async function () {
    const b = $('bmScan');
    showError(''); b.disabled = true; b.textContent = 'Scanning…';
    try { drawFound(await send('POST', '/api/admin/blackmagic/scan', { probe: $('bmProbe').checked })); } catch (e) { showError(e.message); }
    b.disabled = false; b.textContent = 'Scan';
  };
  $('bmAddBtn').onclick = async function () {
    showError('');
    try {
      const kind = $('bmKind').value;
      const body = { kind: kind, host: $('bmHost').value, port: +$('bmPort').value || DEFAULT_PORT[kind] };
      if ($('bmName').value.trim()) body.name = $('bmName').value;
      draw(await send('POST', '/api/admin/blackmagic/devices', body));
      $('bmHost').value = ''; $('bmName').value = '';
    } catch (e) { showError(e.message); }
  };
  $('bmShowFind').onclick = function () { $('bmFind').hidden = !$('bmFind').hidden; if (!$('bmFind').hidden) $('bmScan').click(); };
  $('bmShowAdd').onclick = function () { $('bmAdd').hidden = !$('bmAdd').hidden; if (!$('bmAdd').hidden) $('bmHost').focus(); };
  $('bmKind').onchange = function () { $('bmPort').value = DEFAULT_PORT[$('bmKind').value]; };

  // ------------------------------------------------------------------------------------------- tabs, face, the page
  const PANES = { rack: 'bmPaneRack', wiring: 'bmPaneWiring', ports: 'bmPanePorts', find: 'bmPaneFind' };
  function tab(name) {
    if (!Object.prototype.hasOwnProperty.call(PANES, name)) return;
    $('bmTabs').querySelectorAll('button').forEach(function (b) { b.setAttribute('aria-selected', String(b.dataset.bmtab === name)); });
    Object.keys(PANES).forEach(function (k) { $(PANES[k]).classList.toggle('on', k === name); });
    if (name === 'wiring') cables(views());
  }
  $('bmTabs').addEventListener('click', function (e) { const b = e.target.closest('button[data-bmtab]'); if (b) tab(b.dataset.bmtab); });
  $('bmFace').addEventListener('click', function (e) {
    const b = e.target.closest('button[data-face]');
    if (!b || b.dataset.face === face) return;
    $('bmFace').querySelectorAll('button').forEach(function (x) { x.setAttribute('aria-pressed', String(x === b)); });
    const flip = $('bmFlip');
    flip.classList.add('turning');
    setTimeout(function () { face = b.dataset.face; sel = null; all(); flip.classList.remove('turning'); }, 350);
  });
  $('bmAddCable').onclick = function () {
    if (!can('blackmagic.manage')) { showError('Adding a cable needs permission to manage Blackmagic devices.'); return; }
    picking = picking ? null : [];
    cableNote(picking ? 'Click the first end of the cable on a back panel.' : '');
    drawWiring(views());
  };
  $('bmAddCable').hidden = !can('blackmagic.manage');
  $('bmSaveNotes').onclick = async function () {
    showError('');
    const todo = Object.keys(dirty).map(function (k) { return dirty[k]; });
    dirty = {};
    let res = null;
    try { for (const n of todo) res = await send('POST', '/api/admin/blackmagic/notes', { device: n[0], port: n[1], text: n[2].trim() }); }
    catch (e) { showError(e.message); }
    if (res) draw(res);
  };
  $('bmSaveNotes').hidden = !can('blackmagic.manage');
  $('bmPortDev').onchange = function () { drawPorts(views()); };
  $('bmPortUsed').onchange = function () { drawPorts(views()); };
  window.addEventListener('resize', function () { cables(views()); });

  function all() {
    const vs = views();
    if (!device(cur)) {
      const sw = state.devices.find(function (d) { return d.kind === 'atem' && d.connected; });
      cur = sw ? sw.id : state.devices.length ? state.devices[0].id : null;
    }
    D.hideTip();
    drawRack(vs);
    drawDrawer(vs);
    drawWiring(vs);
    drawPorts(vs);
    drawOrder(vs);
    drawModels();
  }
  function draw(next) {
    state = next;
    last = JSON.stringify(next);
    const on = state.devices.filter(function (d) { return d.enabled && d.host; });
    const text = !on.length ? 'not set up' : state.connected + ' of ' + on.length + ' connected';
    $('bmSum').textContent = '';
    $('bmSum').append(relayUI.pill(text, !on.length ? 'idle' : state.connected === on.length ? 'on' : 'warn'));
    $('bmWarn').textContent = (state.warnings || []).join(' ');
    $('bmWarn').hidden = !(state.warnings || []).length;
    $('bmAddBtns').hidden = !can('blackmagic.manage');
    all();
  }
  function busy() {
    // Never redraw under the operator's hands: a field being typed in, a picked input, or a cable half added.
    const a = document.activeElement;
    return !!picking || Object.keys(dirty).length > 0 || !!(a && $('page-blackmagic').contains(a) && (a.tagName === 'INPUT' || a.tagName === 'SELECT' || a.tagName === 'TEXTAREA'));
  }
  async function refresh() {
    try {
      const next = await send('GET', '/api/admin/blackmagic');
      if (JSON.stringify(next) !== last && !busy()) draw(next);
    } catch (e) { /* the page keeps what it shows */ }
  }
  refresh();
  setInterval(function () { if (!document.hidden && !document.getElementById('page-blackmagic').hidden) refresh(); }, 2000);
})();
