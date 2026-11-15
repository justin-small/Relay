/* The ATEM admin page (#243): the switcher's address, its connection, and basic control. Loaded by admin.html only
   while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-atem')) return;
  const $ = R.$;
  function can(perm) { return typeof window.relayCan !== 'function' || !!window.relayCan(perm); }
  function showError(text) { $('atErr').textContent = text || ''; $('atErr').hidden = !text; }

  async function send(method, path, body) {
    const res = await fetch(path, { method: method, headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), credentials: 'same-origin' });
    const data = await res.json().catch(function () { return {}; });
    if (!res.ok) throw new Error(data.error || data.detail || 'That did not work.');
    return data;
  }
  function button(text, fn, cls) {
    const b = document.createElement('button');
    b.type = 'button'; b.textContent = text;
    if (cls) b.className = cls;
    b.onclick = async function () {
      showError(''); b.disabled = true;
      try { await fn(); } catch (e) { showError(e.message); }
      b.disabled = false;
    };
    return b;
  }
  function select(inputs, value) {
    const s = document.createElement('select');
    inputs.forEach(function (i) {
      const o = document.createElement('option');
      o.value = i.id; o.textContent = i.name;
      if (i.id === value) o.selected = true;
      s.appendChild(o);
    });
    return s;
  }
  function name(inputs, id) {
    const found = inputs.find(function (i) { return i.id === id; });
    return found ? found.name : '—';
  }

  function drawMEs(state, control) {
    const box = $('atMEs');
    box.textContent = '';
    state.mix_effects.forEach(function (m) {
      const row = document.createElement('div');
      row.className = 'row';
      row.style.cssText = 'align-items:center;gap:12px;padding:8px 0;border-top:1px solid var(--line, #8883);flex-wrap:wrap';
      const title = document.createElement('strong');
      title.textContent = 'M/E ' + (m.me + 1);
      const live = document.createElement('span');
      live.textContent = 'Program: ' + name(state.inputs, m.program) + ' · Preview: ' + name(state.inputs, m.preview);
      row.append(title, live);
      if (control) {
        const pick = select(state.inputs, m.preview);
        const pv = button('Set preview', function () { return post('preview', { me: m.me, source: +pick.value }); });
        const pg = button('Set program', function () { return post('program', { me: m.me, source: +pick.value }); });
        row.append(pick, pv, pg,
          button('Cut', function () { return post('cut', { me: m.me }); }, 'primary'),
          button('Auto', function () { return post('auto', { me: m.me }); }),
          button('Fade to black', function () { return post('fade-to-black', { me: m.me }); }));
      }
      box.appendChild(row);
    });
  }
  function drawKeys(state, control) {
    const box = $('atKeys');
    box.textContent = '';
    if (!control) return;
    for (let k = 0; k < state.downstream_keyers; k++) {
      const row = document.createElement('div');
      row.className = 'row';
      row.style.cssText = 'align-items:center;gap:12px;padding:8px 0;border-top:1px solid var(--line, #8883)';
      const t = document.createElement('strong');
      t.textContent = 'Downstream key ' + (k + 1);
      row.append(t,
        button('Auto', function () { return post('downstream-key', { keyer: k, action: 'auto' }); }),
        button('On air', function () { return post('downstream-key', { keyer: k, action: 'on' }); }),
        button('Off air', function () { return post('downstream-key', { keyer: k, action: 'off' }); }));
      box.appendChild(row);
    }
  }
  function drawMacros(state, control) {
    const box = $('atMacros');
    box.textContent = '';
    $('atNoMacros').hidden = state.macros.length > 0;
    state.macros.forEach(function (m) {
      const row = document.createElement('div');
      row.className = 'row';
      row.style.cssText = 'align-items:center;gap:12px;padding:6px 0';
      const t = document.createElement('span');
      t.textContent = (m.slot + 1) + '. ' + (m.name || 'Macro');
      row.appendChild(t);
      if (control) row.appendChild(button('Run', function () { return post('macro', { slot: m.slot, action: 'run' }); }));
      box.appendChild(row);
    });
    if (state.macros.length && control) {
      const stop = button('Stop macro', function () { return post('macro', { slot: 0, action: 'stop' }); });
      box.appendChild(stop);
    }
  }

  async function post(action, body) {
    await send('POST', '/api/admin/atem/' + action, body);
    setTimeout(refresh, 250);
  }

  let editing = false;
  function draw(state) {
    if (!editing) { $('atHost').value = state.host; $('atPort').value = state.port; }
    $('atSum').textContent = state.connected ? 'connected' : (state.configured ? 'not connected' : 'not set up');
    $('atStatus').textContent = state.connected
      ? 'Connected' + (state.product ? ' to ' + state.product : '') + '.'
      : (state.configured ? (state.error || 'Connecting…') : 'Enter the switcher\'s address to begin.');
    const control = can('atem.control');
    $('atControl').hidden = !state.connected;
    if (state.connected) { drawMEs(state, control); drawKeys(state, control); drawMacros(state, control); }
    $('atHost').disabled = $('atPort').disabled = $('atSave').disabled = !can('atem.manage');
  }

  let last = '';
  async function refresh() {
    try {
      const state = await send('GET', '/api/admin/atem');
      const sig = JSON.stringify(state);
      if (sig !== last) { last = sig; draw(state); }   // redraw only on a change, so a picked input stays picked
    } catch (e) { /* the page keeps what it shows */ }
  }
  $('atHost').oninput = $('atPort').oninput = function () { editing = true; };
  $('atSave').onclick = async function () {
    showError('');
    try {
      editing = false;
      const state = await send('PUT', '/api/admin/atem/settings', { host: $('atHost').value, port: +$('atPort').value || 9910 });
      last = JSON.stringify(state); draw(state);
    } catch (e) { showError(e.message); }
  };
  refresh();
  setInterval(function () { if (!document.hidden && !document.getElementById('page-atem').hidden) refresh(); }, 2000);
})();
