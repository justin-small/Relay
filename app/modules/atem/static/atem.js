/* The ATEM admin page (#243, #386): the switchers, their connections and basic control, and finding Blackmagic gear
   on the network. Loaded by admin.html only while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-atem')) return;
  const $ = R.$;
  const LINE = 'border-top:1px solid var(--line, #8883)';
  function can(perm) { return typeof window.relayCan !== 'function' || !!window.relayCan(perm); }
  function showError(text) { $('atErr').textContent = text || ''; $('atErr').hidden = !text; }

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
    const id = 'at' + Math.random().toString(36).slice(2, 9);
    input.id = id;
    return el('div', { className: 'field' }, [el('label', { htmlFor: id, textContent: label }), input]);
  }
  function row(style) { return el('div', { className: 'row', style: 'align-items:center;gap:12px;padding:8px 0;flex-wrap:wrap;' + (style || '') }); }

  async function post(dev, action, body) {
    await send('POST', '/api/admin/atem/' + action, Object.assign({ device: dev.id }, body));
    setTimeout(refresh, 250);
  }

  // ------------------------------------------------------------------------------------------- control
  function drawControl(dev, box) {
    const control = can('atem.control');
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

  // ------------------------------------------------------------------------------------------- one switcher
  function status(dev) {
    if (!dev.enabled) return 'switched off';
    if (dev.connected) return 'connected' + (dev.product ? ' to ' + dev.product : '');
    return dev.error || 'connecting…';
  }
  function drawDevice(dev) {
    const manage = can('atem.manage');
    const card = el('div', { className: 'acard', style: 'padding:12px 0;' + LINE });
    card.dataset.id = dev.id;
    const head = row('padding:0');
    const state = el('span', { className: 'pill' + (dev.connected ? ' ok' : (dev.enabled && dev.error ? ' warn' : '')),
      textContent: status(dev) });
    head.append(el('strong', { textContent: dev.name }), state);
    if (manage) {
      const sw = el('label', { className: 'switch', title: 'Connect to this switcher', style: 'margin-left:auto' });
      const box = el('input', { type: 'checkbox', checked: dev.enabled });
      box.setAttribute('aria-label', 'Connect to ' + dev.name);
      box.onchange = async function () {
        try { await save(dev, { enabled: box.checked }); } catch (e) { box.checked = !box.checked; showError(e.message); }
      };
      sw.append(box, el('span', { className: 'slider' }));
      head.append(sw);
    }
    card.append(head);

    const facts = [dev.host + ':' + dev.port];
    if (dev.model && dev.model !== dev.product) facts.push(dev.model);
    if (dev.version) facts.push('protocol ' + dev.version);
    if (dev.tally) facts.push('tally cameras ' + (dev.tally_offset + 1) + '–' + (dev.tally_offset + 40));
    else facts.push('does not drive tally');
    if (dev.uid) facts.push('followed by its network id');
    card.append(el('div', { className: 'note mono', textContent: facts.join(' · ') }));
    if (dev.old_firmware) {
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
      const first = el('input', { type: 'number', min: 1, max: 901, value: dev.tally_offset + 1 });
      const tally = el('input', { type: 'checkbox', checked: dev.tally });
      const tallyLabel = el('label', { className: 'chk chk-inline', style: 'margin:8px 0' }, [tally, document.createTextNode(' Drives the tally lights')]);
      const fields = row();
      fields.append(field('Name', name), field('Address', host), field('Port', port), field('First camera number', first));
      details.append(fields, tallyLabel, el('div', { className: 'note', textContent: 'Camera 1 on this switcher shows ' +
        'as the first camera number on the tally screens. Give each switcher that drives tally its own range, ' +
        'like 1 and 101.' }));
      const acts = row();
      acts.append(
        button('Save', function () {
          return save(dev, { name: name.value, host: host.value, port: +port.value || 9910, tally: tally.checked,
            tally_offset: Math.max(0, (+first.value || 1) - 1) });
        }, 'primary'),
        button('Remove', async function () {
          if (!window.confirm('Remove ' + dev.name + ' from Relay? The switcher itself is not changed.')) return;
          draw(await send('DELETE', '/api/admin/atem/devices/' + encodeURIComponent(dev.id)));
        }));
      details.append(acts);
      card.append(details);
    }
    if (dev.connected) {
      const ctl = el('details', { open: true });
      ctl.append(el('summary', { textContent: 'Control' }));
      drawControl(dev, ctl);
      card.append(ctl);
    }
    return card;
  }
  async function save(dev, changes) {
    showError('');
    draw(await send('PUT', '/api/admin/atem/devices/' + encodeURIComponent(dev.id), changes));
  }

  // ------------------------------------------------------------------------------------------- the page
  let last = '';
  const openState = {};
  function draw(state) {
    last = JSON.stringify(state);
    const box = $('atDevices');
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
    $('atNone').hidden = state.devices.length > 0;
    const on = state.devices.filter(function (d) { return d.enabled && d.host; });
    $('atSum').textContent = !on.length ? 'not set up'
      : (state.connected === on.length ? (on.length > 1 ? 'all ' + on.length + ' connected' : 'connected')
        : state.connected + ' of ' + on.length + ' connected');
    $('atWarn').textContent = (state.warnings || []).join(' ');
    $('atWarn').hidden = !(state.warnings || []).length;
    const manage = can('atem.manage');
    $('atFind').hidden = $('atAdd').hidden = !manage;
  }
  function busy() {
    // Never redraw under the operator's hands: a field being typed in, or a picked input.
    const a = document.activeElement;
    return !!(a && $('atDevices').contains(a) && (a.tagName === 'INPUT' || a.tagName === 'SELECT'));
  }
  async function refresh() {
    try {
      const state = await send('GET', '/api/admin/atem');
      if (JSON.stringify(state) !== last && !busy()) draw(state);
    } catch (e) { /* the page keeps what it shows */ }
  }

  // ------------------------------------------------------------------------------------------- finding
  function drawFound(result) {
    const table = $('atFound');
    const body = table.querySelector('tbody');
    body.textContent = '';
    const found = result.found || [];
    $('atScanNote').hidden = false;
    $('atScanNote').textContent = found.length
      ? 'Found ' + found.length + (found.length === 1 ? ' device.' : ' devices.')
      : 'Nothing answered. ' + (result.probed ? 'Check the switcher is on this network, or add it by its address.'
        : 'Try again with "every address on this subnet", or add it by its address.');
    table.hidden = !found.length;
    found.forEach(function (f) {
      let act;
      if (!f.atem) act = el('span', { className: 'muted', textContent: 'Not controlled yet' });
      else if (f.added) act = el('span', { className: 'ok', textContent: 'Added' });
      else {
        act = button('Add', async function () {
          draw(await send('POST', '/api/admin/atem/devices', { name: f.name, host: f.address, port: 9910,
            uid: f.uid, model: f.model }));
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
  $('atScan').onclick = async function () {
    const b = $('atScan');
    showError(''); b.disabled = true; b.textContent = 'Scanning…';
    try { drawFound(await send('POST', '/api/admin/atem/scan', { probe: $('atProbe').checked })); }
    catch (e) { showError(e.message); }
    b.disabled = false; b.textContent = 'Scan';
  };
  $('atAddBtn').onclick = async function () {
    showError('');
    try {
      const body = { host: $('atHost').value, port: +$('atPort').value || 9910 };
      if ($('atName').value.trim()) body.name = $('atName').value;
      draw(await send('POST', '/api/admin/atem/devices', body));
      $('atHost').value = ''; $('atName').value = '';
    } catch (e) { showError(e.message); }
  };

  refresh();
  setInterval(function () { if (!document.hidden && !document.getElementById('page-atem').hidden) refresh(); }, 2000);
})();
