/* The Wireless mics admin page (#153): receivers, manual mics and packs, their positions and live cards. Loaded by
   admin.html only while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-wirelessmics')) return;
  const $ = R.$;
  const ui = window.relayUI;
  const send = ui.api;
  const showError = ui.note('wmErr');

  let state = null;      // what the server last said
  let draft = null;      // receivers and manual gear being edited
  let editing = false;

  function newId() { return Math.random().toString(36).slice(2, 8); }

  function input(value, placeholder, attrs) {
    const i = ui.el('input', Object.assign({ value: value, placeholder: placeholder || '', autocomplete: 'off' }, attrs || {}));
    i.oninput = function () { editing = true; };
    return i;
  }

  function drawForm() {
    const rbox = $('wmReceivers');
    rbox.textContent = '';
    draft.receivers.forEach(function (r, at) {
      const name = input(r.name, 'Name', { maxLength: 40 });
      const host = input(r.host, '192.168.1.60', { maxLength: 253, spellcheck: false });
      const port = input(r.port, '2202', { type: 'number', min: 1, max: 65535 });
      const chans = input(r.channels, '4', { type: 'number', min: 1, max: 4 });
      name.oninput = function () { editing = true; r.name = name.value; };
      host.oninput = function () { editing = true; r.host = host.value; };
      port.oninput = function () { editing = true; r.port = +port.value || 2202; };
      chans.oninput = function () { editing = true; r.channels = +chans.value || 4; };
      const live = (state.receivers.find(function (x) { return x.id === r.id; }) || {});
      rbox.appendChild(ui.el('div', 'wmrow',
        ui.field('Name', name), ui.field('Address', host), ui.field('Port', port), ui.field('Channels', chans),
        ui.pill(live.connected ? 'connected' : (live.error || 'not connected'), live.connected ? 'on' : 'warn'),
        ui.button('Remove', function () { draft.receivers.splice(at, 1); editing = true; drawForm(); }, 'sm')));
    });
    const mbox = $('wmManual');
    mbox.textContent = '';
    draft.manual.forEach(function (m, at) {
      const name = input(m.name, 'Name', { maxLength: 40 });
      name.oninput = function () { editing = true; m.name = name.value; };
      const kind = ui.el('select', '', ui.el('option', { value: 'mic', text: 'Microphone' }),
                         ui.el('option', { value: 'iem', text: 'In-ear pack' }));
      kind.value = m.kind;
      kind.onchange = function () { editing = true; m.kind = kind.value; };
      mbox.appendChild(ui.el('div', 'wmrow', ui.field('Name', name), ui.field('Kind', kind),
        ui.button('Remove', function () { draft.manual.splice(at, 1); editing = true; drawForm(); }, 'sm')));
    });
    const can = ui.can('wireless_mics.manage');
    ['wmAddReceiver', 'wmAddManual', 'wmSave', 'wmLow', 'wmWeak'].forEach(function (id) { $(id).disabled = !can; });
    $('page-wirelessmics').querySelectorAll('.wmrow input, .wmrow select, .wmrow button').forEach(function (n) { n.disabled = !can; });
  }

  function meter(label, value, low, high) {
    if (value === null || value === undefined) return [];
    return [ui.el('span', '', label), ui.el('meter', { min: 0, max: 100, low: low, high: high, optimum: 100, value: value })];
  }

  function drawCards() {
    const box = $('wmCards');
    box.textContent = '';
    $('wmEmpty').hidden = state.cards.length > 0;
    const picks = state.positions.map(function (p) { return { ref: p.ref, label: p.team ? p.team + ': ' + p.name : p.name, name: p.name }; });
    state.cards.forEach(function (c) {
      const card = ui.el('div', 'wmcard ' + c.level + (c.source === 'shure' && !c.present ? ' off' : ''));
      const left = ui.el('div', '', ui.el('h4', '', c.name + (c.kind === 'iem' ? ' (in-ear)' : '')));
      if (c.person) {
        left.appendChild(ui.el('div', 'wmwho', c.person.photo ? ui.el('img', { src: c.person.photo, alt: '' }) : null, c.person.name));
      } else if (c.position) {
        left.appendChild(ui.el('div', 'wmwho', 'Nobody scheduled for ' + c.position));
      }
      if (c.source === 'shure') {
        left.appendChild(ui.el('div', 'wmwho', c.online ? (c.present ? 'Transmitter ' + c.tx : 'No transmitter') : 'Receiver offline'));
      } else {
        left.appendChild(ui.el('div', 'wmwho', 'Manual: no readings'));
      }
      const sel = ui.el('select');
      ui.picker(sel, picks, c.position_ref ? { ref: c.position_ref, name: c.position } : null, 'No position');
      sel.disabled = !ui.can('wireless_mics.manage');
      sel.onchange = async function () {
        showError('');
        const assign = {};
        state.cards.forEach(function (x) { if (x.position_ref) assign[x.key] = { position_ref: x.position_ref, position_name: x.position }; });
        if (sel.value) assign[c.key] = { position_ref: sel.value, position_name: ui.pickedName(sel) || '' }; else delete assign[c.key];
        try { await save(assign); } catch (e) { showError(e.message); }
      };
      left.appendChild(ui.field('Position', sel));
      const meters = ui.el('div', 'wmmeters');
      meter('Battery', c.battery, 25, 60).concat(meter('Signal', c.rf, 25, 50), meter('Audio', c.audio, 100, 100))
        .forEach(function (n) { meters.appendChild(n); });
      if (c.muted) meters.appendChild(ui.el('span', 'wmwarn', 'Muted'));
      card.appendChild(left);
      card.appendChild(meters);
      c.warnings.forEach(function (w) { card.appendChild(ui.el('div', 'wmwarn ' + w.level, w.text)); });
      box.appendChild(card);
    });
  }

  function draw() {
    const s = state;
    const up = s.receivers.filter(function (r) { return r.connected; }).length;
    $('wmSum').textContent = s.receivers.length ? up + ' of ' + s.receivers.length + ' receivers' : 'not set up';
    if (!editing) {
      draft = { receivers: s.receivers.map(function (r) { return { id: r.id, name: r.name, host: r.host, port: r.port, channels: r.channels }; }),
                manual: s.manual.map(function (m) { return Object.assign({}, m); }) };
      $('wmLow').value = s.low_battery; $('wmWeak').value = s.weak_rf_dbm;
      drawForm();
    }
    drawCards();
  }

  async function save(assign) {
    const body = { receivers: draft.receivers, manual: draft.manual.filter(function (m) { return m.name.trim(); }),
                   low_battery: +$('wmLow').value || 20, weak_rf_dbm: +$('wmWeak').value || -85, assign: assign };
    if (!assign) {
      assign = {};
      state.cards.forEach(function (x) { if (x.position_ref) assign[x.key] = { position_ref: x.position_ref, position_name: x.position }; });
      body.assign = assign;
    }
    editing = false;
    state = await send('PUT', '/api/admin/wireless-mics/settings', body);
    draw();
  }

  $('wmAddReceiver').onclick = function () { draft.receivers.push({ id: newId(), name: '', host: '', port: 2202, channels: 4 }); editing = true; drawForm(); };
  $('wmAddManual').onclick = function () { draft.manual.push({ id: newId(), name: '', kind: 'mic' }); editing = true; drawForm(); };
  $('wmSave').onclick = async function () { showError(''); try { await save(null); } catch (e) { showError(e.message); } };
  $('wmLow').oninput = $('wmWeak').oninput = function () { editing = true; };

  let last = '';
  async function refresh() {
    try {
      const next = await send('GET', '/api/admin/wireless-mics');
      const sig = JSON.stringify(next);
      if (sig !== last) { last = sig; state = next; draw(); }
    } catch (e) { /* the page keeps what it shows */ }
  }
  refresh();
  setInterval(function () { if (!document.hidden && !document.getElementById('page-wirelessmics').hidden) refresh(); }, 2000);
})();
