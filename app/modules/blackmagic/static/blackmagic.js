/* The Blackmagic admin page (#243, #386, #432, #433): the devices (ATEM switchers, HyperDecks, Videohubs), their
   connections and control, and finding Blackmagic gear on the network. Loaded by admin.html only while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-blackmagic')) return;
  const $ = R.$;
  const KINDS = { atem: 'ATEM switcher', hyperdeck: 'HyperDeck', videohub: 'Videohub' };
  const DEFAULT_PORT = { atem: 9910, hyperdeck: 9993, videohub: 9990 };
  const LINE = 'border-top:1px solid var(--line, #8883)';
  function can(perm) { return typeof window.relayCan !== 'function' || !!window.relayCan(perm); }
  function showError(text) { $('bmErr').textContent = text || ''; $('bmErr').hidden = !text; }

  async function send(method, path, body) {
    const res = await fetch(path, { method: method, headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), credentials: 'same-origin' });
    const data = await res.json().catch(function () { return {}; });
    if (!res.ok) throw new Error(data.error || data.detail || 'That did not work.');
    return data;
  }
  function el(tag, props, kids) {
    const e = document.createElement(tag);
    Object.assign(e, props || {});
    (kids || []).forEach(function (k) { if (k) e.append(k); });
    return e;
  }
  function button(text, fn, cls) {
    const b = el('button', { type: 'button', textContent: text, className: cls || '' });
    b.onclick = async function () {
      showError(''); b.disabled = true;
      try { await fn(); } catch (e) { showError(e.message); }
      b.disabled = false;
    };
    return b;
  }
  function select(inputs, value) {
    const s = el('select');
    inputs.forEach(function (i) {
      const o = el('option', { value: i.id, textContent: i.name });
      if (i.id === value) o.selected = true;
      s.appendChild(o);
    });
    return s;
  }
  function inputName(inputs, id) {
    const found = inputs.find(function (i) { return i.id === id; });
    return found ? found.name : '—';
  }
  function field(label, input) {
    const id = 'bm' + Math.random().toString(36).slice(2, 9);
    input.id = id;
    return el('div', { className: 'field' }, [el('label', { htmlFor: id, textContent: label }), input]);
  }
  function row(style) { return el('div', { className: 'row', style: 'align-items:center;gap:12px;padding:8px 0;flex-wrap:wrap;' + (style || '') }); }

  async function post(dev, action, body) {
    await send('POST', '/api/admin/blackmagic/' + action, Object.assign({ device: dev.id }, body));
    setTimeout(refresh, 250);
  }

  // ------------------------------------------------------------------------------------------- control
  function drawControl(dev, box) {
    const control = can('blackmagic.control');
    dev.mix_effects.forEach(function (m) {
      const r = row(LINE);
      r.append(el('strong', { textContent: 'M/E ' + (m.me + 1) }),
        el('span', { textContent: 'Program: ' + inputName(dev.inputs, m.program) + ' · Preview: ' + inputName(dev.inputs, m.preview) }));
      if (control) {
        const pick = select(dev.inputs, m.preview);
        r.append(pick,
          button('Set preview', function () { return post(dev, 'preview', { me: m.me, source: +pick.value }); }),
          button('Set program', function () { return post(dev, 'program', { me: m.me, source: +pick.value }); }),
          button('Cut', function () { return post(dev, 'cut', { me: m.me }); }, 'primary'),
          button('Auto', function () { return post(dev, 'auto', { me: m.me }); }),
          button('Fade to black', function () { return post(dev, 'fade-to-black', { me: m.me }); }));
      }
      box.appendChild(r);
    });
    if (control) {
      for (let k = 0; k < dev.downstream_keyers; k++) {
        const r = row(LINE);
        r.append(el('strong', { textContent: 'Downstream key ' + (k + 1) }),
          button('Auto', function () { return post(dev, 'downstream-key', { keyer: k, action: 'auto' }); }),
          button('On air', function () { return post(dev, 'downstream-key', { keyer: k, action: 'on' }); }),
          button('Off air', function () { return post(dev, 'downstream-key', { keyer: k, action: 'off' }); }));
        box.appendChild(r);
      }
    }
    const macros = row(LINE);
    macros.append(el('strong', { textContent: 'Macros' }));
    if (!dev.macros.length) macros.append(el('span', { className: 'muted', textContent: 'None recorded on the switcher.' }));
    dev.macros.forEach(function (m) {
      const label = (m.slot + 1) + '. ' + (m.name || 'Macro');
      macros.append(control ? button(label, function () { return post(dev, 'macro', { slot: m.slot, action: 'run' }); })
        : el('span', { textContent: label }));
    });
    if (dev.macros.length && control) macros.append(button('Stop macro', function () { return post(dev, 'macro', { slot: 0, action: 'stop' }); }));
    box.appendChild(macros);
  }

  // ------------------------------------------------------------------------------------------- HyperDeck
  const DECK = { record: 'Recording', stop: 'Stopped', stopped: 'Stopped', play: 'Playing', preroll: 'Getting ready',
    forward: 'Fast forward', rewind: 'Rewinding', jog: 'Jogging', shuttle: 'Shuttling' };
  function minutes(seconds) {
    if (!seconds) return 'full or unknown';
    const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60);
    return h ? h + ' h ' + m + ' min left' : Math.max(1, m) + ' min left';
  }
  function drawDeck(dev, box) {
    const d = dev.hyperdeck || {};
    const control = can('blackmagic.control');
    const r = row(LINE);
    const recording = d.status === 'record';
    r.append(el('strong', { textContent: DECK[d.status] || 'Unknown' }));
    if (d.clip && d.clip.name) r.append(el('span', { textContent: 'Clip ' + d.clip.id + ': ' + d.clip.name }));
    if (d.timecode) r.append(el('span', { className: 'mono', textContent: d.timecode }));
    if (d.video_format) r.append(el('span', { className: 'muted', textContent: d.video_format }));
    if (control) {
      r.append(button('Record', function () { return post(dev, 'hyperdeck/record'); }, recording ? '' : 'primary'),
        button('Stop', function () { return post(dev, 'hyperdeck/stop'); }),
        button('Play', function () { return post(dev, 'hyperdeck/play'); }));
    }
    box.appendChild(r);
    if (d.notice) box.appendChild(el('div', { className: 'note warn', textContent: d.notice }));
    if (d.remote_enabled === false) {
      box.appendChild(el('div', { className: 'note warn', textContent: 'Remote control is turned off on the HyperDeck, so Relay can look but not control it.' }));
    }
    (d.slots || []).forEach(function (s) {
      const line = row(LINE);
      line.append(el('strong', { textContent: 'Slot ' + s.id }),
        el('span', { textContent: s.status === 'mounted' ? (s.volume || 'Disk') : (s.status === 'empty' ? 'No disk' : s.status || '—') }),
        el('span', { className: d.slot === s.id && recording ? 'pill warn' : 'muted',
          textContent: s.status === 'mounted' ? 'recording time ' + minutes(s.seconds_left) : '' }));
      box.appendChild(line);
    });
    const clips = (d.clips || []).slice(-8);
    if (clips.length) {
      const list = el('div', { className: 'note mono' });
      list.append(el('div', { textContent: 'Last clips on the current disk' }));
      clips.forEach(function (c) { list.append(el('div', { textContent: c.id + '. ' + c.name + '  ' + c.duration })); });
      box.appendChild(list);
    }
  }

  // ------------------------------------------------------------------------------------------- Videohub
  function portName(p, kind) { return p.label || (kind + ' ' + (p.n + 1)); }
  function drawHub(dev, box) {
    const h = dev.videohub || {};
    const control = can('blackmagic.control'), manage = can('blackmagic.manage');
    if (h.notice) box.appendChild(el('div', { className: 'note warn', textContent: h.notice }));
    if (h.present === false) box.appendChild(el('div', { className: 'note warn', textContent: 'The router says its video hardware is not present.' }));
    const table = el('table', {});
    table.append(el('thead', {}, [el('tr', {}, [el('th', { textContent: 'Output' }), el('th', { textContent: 'Takes' }), el('th', { textContent: 'Lock' })])]));
    const body = el('tbody');
    (h.outputs || []).forEach(function (o) {
      const pick = el('select');
      (h.inputs || []).forEach(function (i) {
        const opt = el('option', { value: i.n, textContent: (i.n + 1) + '. ' + portName(i, 'Input') });
        if (i.n === o.input) opt.selected = true;
        pick.appendChild(opt);
      });
      pick.setAttribute('aria-label', 'Input for ' + portName(o, 'Output'));
      pick.disabled = !control || o.lock === 'L';
      pick.onchange = async function () {
        showError('');
        try { await post(dev, 'videohub/route', { output: o.n, input: +pick.value }); } catch (e) { showError(e.message); refresh(); }
      };
      const lockCell = el('td');
      if (o.lock === 'L') lockCell.append(el('span', { className: 'pill warn', textContent: 'Locked by another surface' }));
      else {
        if (o.lock === 'O') lockCell.append(el('span', { className: 'pill', textContent: 'Locked by Relay' }), ' ');
        if (manage) {
          lockCell.append(button(o.lock === 'O' ? 'Unlock' : 'Lock', function () {
            return post(dev, 'videohub/lock', { output: o.n, action: o.lock === 'O' ? 'unlock' : 'lock' });
          }));
        }
      }
      body.appendChild(el('tr', {}, [el('td', { textContent: (o.n + 1) + '. ' + portName(o, 'Output') }), el('td', {}, [pick]), lockCell]));
    });
    table.append(body);
    box.appendChild(el('div', { className: 'tablescroll' }, [table]));
    drawPresets(dev, h, box, control, manage);
    if (manage) drawLabels(dev, h, box);
  }
  function drawPresets(dev, h, box, control, manage) {
    const section = el('div', { style: 'padding:8px 0;' + LINE });
    section.append(el('strong', { textContent: 'Route presets' }));
    const presets = dev.presets || [];
    if (!presets.length) section.append(el('div', { className: 'note', textContent: 'None saved. Set the router the way you want it, then save it here.' }));
    presets.forEach(function (p) {
      const line = row();
      line.append(el('span', { textContent: p.name }), el('span', { className: 'muted', textContent: Object.keys(p.routes).length + ' outputs' }));
      if (control) {
        line.append(button('Recall', async function () {
          const res = await send('POST', '/api/admin/blackmagic/videohub/presets/apply', { device: dev.id, preset: p.id });
          if (res.left_out) showError(res.left_out + ' output(s) were left out: locked by another surface or gone.');
          setTimeout(refresh, 250);
        }, 'primary'));
      }
      if (manage) {
        line.append(button('Delete', async function () {
          if (!window.confirm('Delete the preset "' + p.name + '"?')) return;
          draw(await send('DELETE', '/api/admin/blackmagic/videohub/presets/' + encodeURIComponent(dev.id) + '/' + encodeURIComponent(p.id)));
        }));
      }
      section.append(line);
    });
    if (manage) {
      const name = el('input', { maxLength: 60, placeholder: 'Program to overflow', autocomplete: 'off' });
      const line = row();
      line.append(field('Preset name', name), button('Save current routing', async function () {
        draw(await send('POST', '/api/admin/blackmagic/videohub/presets', { device: dev.id, name: name.value }));
      }));
      section.append(line);
    }
    box.appendChild(section);
  }
  function drawLabels(dev, h, box) {
    const details = el('details');
    details.append(el('summary', { textContent: 'Rename inputs and outputs' }));
    const edits = { input: {}, output: {} };
    function column(kind, ports) {
      const col = el('div', { style: 'flex:1;min-width:220px' }, [el('strong', { textContent: kind === 'input' ? 'Inputs' : 'Outputs' })]);
      ports.forEach(function (p) {
        const box = el('input', { value: p.label, maxLength: 60, autocomplete: 'off' });
        box.setAttribute('aria-label', (kind === 'input' ? 'Input ' : 'Output ') + (p.n + 1) + ' label');
        box.oninput = function () { edits[kind][p.n] = box.value; };
        col.append(el('div', { style: 'display:flex;gap:6px;align-items:center;margin:4px 0' }, [el('span', { className: 'mono', textContent: (p.n + 1) + '.', style: 'width:3ch' }), box]));
      });
      return col;
    }
    const cols = row();
    cols.append(column('input', h.inputs || []), column('output', h.outputs || []));
    details.append(cols, el('div', { className: 'ctl' }, [button('Save labels', async function () {
      for (const kind of ['input', 'output']) {
        const labels = {};
        Object.keys(edits[kind]).forEach(function (n) { if (edits[kind][n].trim()) labels[n] = edits[kind][n]; });
        if (Object.keys(labels).length) await send('POST', '/api/admin/blackmagic/videohub/labels', { device: dev.id, kind: kind, labels: labels });
      }
      setTimeout(refresh, 400);
    }, 'primary')]));
    box.appendChild(details);
  }

  // ------------------------------------------------------------------------------------------- one device
  function status(dev) {
    if (!dev.enabled) return 'switched off';
    if (dev.connected) return 'connected' + (dev.product ? ' to ' + dev.product : '');
    return dev.error || 'connecting…';
  }
  function drawDevice(dev) {
    const manage = can('blackmagic.manage');
    const card = el('div', { className: 'acard', style: 'padding:12px 0;' + LINE });
    card.dataset.id = dev.id;
    const head = row('padding:0');
    const state = el('span', { className: 'pill' + (dev.connected ? ' ok' : (dev.enabled && dev.error ? ' warn' : '')),
      textContent: status(dev) });
    head.append(el('strong', { textContent: dev.name }), state);
    if (manage) {
      const sw = el('label', { className: 'switch', title: 'Connect to this device', style: 'margin-left:auto' });
      const box = el('input', { type: 'checkbox', checked: dev.enabled });
      box.setAttribute('aria-label', 'Connect to ' + dev.name);
      box.onchange = async function () {
        try { await save(dev, { enabled: box.checked }); } catch (e) { box.checked = !box.checked; showError(e.message); }
      };
      sw.append(box, el('span', { className: 'slider' }));
      head.append(sw);
    }
    card.append(head);

    const facts = [KINDS[dev.kind] || 'Device', dev.host + ':' + dev.port];
    if (dev.model && dev.model !== dev.product) facts.push(dev.model);
    if (dev.version) facts.push('protocol ' + dev.version);
    if (dev.kind === 'atem') {
      if (dev.tally) facts.push('tally cameras ' + (dev.tally_offset + 1) + '–' + (dev.tally_offset + 40));
      else facts.push('does not drive tally');
    }
    if (dev.uid) facts.push('followed by its network id');
    card.append(el('div', { className: 'note mono', textContent: facts.join(' · ') }));
    if (dev.kind === 'atem' && dev.old_firmware) {
      card.append(el('div', { className: 'note warn', textContent: 'This switcher runs firmware older than 8.0.1. ' +
        'Cuts and tally work; update it with ATEM Setup for full support.' }));
    }
    if (dev.refound) card.append(el('div', { className: 'note ok', textContent: dev.refound }));

    if (manage) {
      const details = el('details');
      details.append(el('summary', { textContent: 'Settings' }));
      const name = el('input', { value: dev.name, maxLength: 40 });
      const host = el('input', { value: dev.host, maxLength: 253, spellcheck: false, autocomplete: 'off' });
      const port = el('input', { type: 'number', min: 1, max: 65535, value: dev.port });
      const first = el('input', { type: 'number', min: 1, max: 901, value: (dev.tally_offset || 0) + 1 });
      const tally = el('input', { type: 'checkbox', checked: !!dev.tally });
      const tallyLabel = el('label', { className: 'chk chk-inline', style: 'margin:8px 0' }, [tally, document.createTextNode(' Drives the tally lights')]);
      const fields = row();
      fields.append(field('Name', name), field('Address', host), field('Port', port));
      if (dev.kind === 'atem') fields.append(field('First camera number', first));
      details.append(fields);
      if (dev.kind === 'atem') {
        details.append(tallyLabel, el('div', { className: 'note', textContent: 'Camera 1 on this switcher shows ' +
          'as the first camera number on the tally screens. Give each switcher that drives tally its own range, ' +
          'like 1 and 101.' }));
      }
      const acts = row();
      acts.append(
        button('Save', function () {
          const changes = { name: name.value, host: host.value, port: +port.value || DEFAULT_PORT[dev.kind] };
          if (dev.kind === 'atem') { changes.tally = tally.checked; changes.tally_offset = Math.max(0, (+first.value || 1) - 1); }
          return save(dev, changes);
        }, 'primary'),
        button('Remove', async function () {
          if (!window.confirm('Remove ' + dev.name + ' from Relay? The device itself is not changed.')) return;
          draw(await send('DELETE', '/api/admin/blackmagic/devices/' + encodeURIComponent(dev.id)));
        }));
      details.append(acts);
      card.append(details);
    }
    if (dev.connected) {
      const ctl = el('details', { open: true });
      ctl.append(el('summary', { textContent: 'Control' }));
      if (dev.kind === 'atem') drawControl(dev, ctl);
      else if (dev.kind === 'hyperdeck') drawDeck(dev, ctl);
      else drawHub(dev, ctl);
      card.append(ctl);
    }
    return card;
  }
  async function save(dev, changes) {
    showError('');
    draw(await send('PUT', '/api/admin/blackmagic/devices/' + encodeURIComponent(dev.id), changes));
  }

  // ------------------------------------------------------------------------------------------- the page
  let last = '';
  const openState = {};
  function draw(state) {
    last = JSON.stringify(state);
    const box = $('bmDevices');
    // Keep which panels the operator opened across redraws.
    box.querySelectorAll('.acard').forEach(function (c) {
      openState[c.dataset.id] = Array.from(c.querySelectorAll('details')).map(function (d) { return d.open; });
    });
    box.textContent = '';
    state.devices.forEach(function (dev) {
      const card = drawDevice(dev);
      const was = openState[dev.id];
      if (was) card.querySelectorAll('details').forEach(function (d, i) { if (i < was.length) d.open = was[i]; });
      box.appendChild(card);
    });
    $('bmNone').hidden = state.devices.length > 0;
    const on = state.devices.filter(function (d) { return d.enabled && d.host; });
    $('bmSum').textContent = !on.length ? 'not set up'
      : (state.connected === on.length ? (on.length > 1 ? 'all ' + on.length + ' connected' : 'connected')
        : state.connected + ' of ' + on.length + ' connected');
    $('bmWarn').textContent = (state.warnings || []).join(' ');
    $('bmWarn').hidden = !(state.warnings || []).length;
    const manage = can('blackmagic.manage');
    $('bmFind').hidden = $('bmAdd').hidden = !manage;
  }
  function busy() {
    // Never redraw under the operator's hands: a field being typed in, or a picked input.
    const a = document.activeElement;
    return !!(a && $('bmDevices').contains(a) && (a.tagName === 'INPUT' || a.tagName === 'SELECT'));
  }
  async function refresh() {
    try {
      const state = await send('GET', '/api/admin/blackmagic');
      if (JSON.stringify(state) !== last && !busy()) draw(state);
    } catch (e) { /* the page keeps what it shows */ }
  }

  // ------------------------------------------------------------------------------------------- finding
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
      if (!f.addable) act = el('span', { className: 'muted', textContent: 'Not controlled by Relay' });
      else if (f.added) act = el('span', { className: 'ok', textContent: 'Added' });
      else {
        act = button('Add', async function () {
          const body = { kind: f.family, name: f.name, host: f.address, uid: f.uid, model: f.model };
          if (f.port && f.family === 'atem') body.port = f.port;
          draw(await send('POST', '/api/admin/blackmagic/devices', body));
          f.added = true; drawFound(result);
        }, 'primary');
        if (!f.address) { act.disabled = true; act.title = 'It did not say its address: add it by address below.'; }
      }
      const name = el('td', {}, [el('div', { textContent: f.name }),
        f.model && f.model !== f.name ? el('div', { className: 'muted', textContent: f.model }) : null]);
      body.appendChild(el('tr', {}, [name, el('td', { textContent: f.kind }),
        el('td', { className: 'mono', textContent: f.address || f.host || '—' }),
        el('td', { className: 'mono', textContent: f.firmware || '—' }), el('td', {}, [act])]));
    });
  }
  $('bmScan').onclick = async function () {
    const b = $('bmScan');
    showError(''); b.disabled = true; b.textContent = 'Scanning…';
    try { drawFound(await send('POST', '/api/admin/blackmagic/scan', { probe: $('bmProbe').checked })); }
    catch (e) { showError(e.message); }
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

  $('bmKind').onchange = function () { $('bmPort').value = DEFAULT_PORT[$('bmKind').value]; };
  refresh();
  setInterval(function () { if (!document.hidden && !document.getElementById('page-blackmagic').hidden) refresh(); }, 2000);
})();
