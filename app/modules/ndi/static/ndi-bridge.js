/* The "Other sites (NDI Bridge)" card on the NDI page (#303): the booth's Bridge machines, whether a Host answers, and the
   start-up text for each. Relay only lists and checks Bridge; it never holds the encryption key. */
(function () {
  const N = window.RelayNdi;
  if (!N || !document.getElementById('ndiBridge')) return;
  const $ = function (id) { return document.getElementById(id); };
  const el = relayUI.el;
  let rows = [], shown = '';
  function editing() { return $('ndiBridge').contains(document.activeElement) && document.activeElement !== document.body; }

  function draw() {
    const s = window.RelayNdiState && window.RelayNdiState();
    if (!s || !s.bridge) return;
    const sig = JSON.stringify(s.bridge);
    if (editing() || sig === shown) return;
    shown = sig; rows = s.bridge.map(function (m) { return Object.assign({}, m); });
    build(s.bridge);
  }
  function build(live) {
    const manage = N.can('ndi.manage'), box = $('ndiBridgeList'); box.textContent = '';
    if (!rows.length) box.appendChild(el('p', 'note', 'No Bridge machines listed.'));
    rows.forEach(function (m, i) {
      const card = el('div', 'ndipreset'), row = el('div', 'row');
      const f = function (label, input) { const d = el('div', 'field'); d.appendChild(el('label', '', label)); d.appendChild(input); return d; };
      const name = el('input', { value: m.name, maxLength: 60, disabled: !manage }); name.oninput = function () { m.name = name.value; };
      const role = el('select', { disabled: !manage });
      [['host', 'Host (other site connects to it)'], ['join', 'Join (connects to a Host)']].forEach(function (o) { role.appendChild(el('option', { value: o[0], selected: o[0] === m.role }, o[1])); });
      role.onchange = function () { m.role = role.value; };
      const addr = el('input', { value: m.address, maxLength: 100, spellcheck: false, disabled: !manage }); addr.oninput = function () { m.address = addr.value.trim(); };
      const port = el('input', { type: 'number', min: 1, max: 65535, value: m.port, disabled: !manage }); port.oninput = function () { m.port = +port.value; };
      row.append(f('Name', name), f('Role', role), f('Address', addr), f('Port', port));
      card.appendChild(row);
      const l = live[i];
      if (l && l.role === 'host' && 'reachable' in l) card.appendChild(el('p', 'note ' + (l.reachable ? 'ok' : 'warn'), l.reachable ? 'Answers on port ' + l.port + '.' : l.error));
      if (l && l.start) {
        const d = el('details'); d.appendChild(el('summary', '', 'Start-up text'));
        if (l.start.windows) { d.appendChild(el('p', 'hint', 'Windows')); d.appendChild(el('pre', '', l.start.windows)); }
        if (l.start.linux) { d.appendChild(el('p', 'hint', 'Linux')); d.appendChild(el('pre', '', l.start.linux)); }
        (l.start.notes || []).forEach(function (n) { d.appendChild(el('p', 'hint', n)); });
        card.appendChild(d);
      }
      const rm = el('button', { type: 'button', className: 'sm quiet', disabled: !manage }, 'Remove');
      rm.onclick = function () { rows.splice(i, 1); build(live); };
      card.appendChild(rm);
      box.appendChild(card);
    });
    $('ndiBridgeAdd').disabled = !manage || rows.length >= 8; $('ndiBridgeSave').disabled = !manage;
  }
  $('ndiBridgeAdd').onclick = function () { rows.push({ name: '', role: 'join', address: '', port: 5990 }); build([]); };
  $('ndiBridgeSave').onclick = async function () {
    N.showError('');
    try { shown = ''; N.draw(await N.send('PUT', '/api/admin/ndi/bridge', { machines: rows.map(function (m) { return { id: m.id, name: m.name, role: m.role, address: m.address, port: m.port }; }) })); }
    catch (e) { N.showError(e.message); }
  };
  window.addEventListener('relay-ndi-state', draw);
  draw();
})();
