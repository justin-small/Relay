/* The Dante admin page (#257): the devices Relay found, whether their control port answers, and a button to look again.
   Loaded by admin.html only while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-dante')) return;
  const $ = R.$;
  const el = relayUI.el;
  const can = relayUI.can;
  const send = relayUI.client({ cache: 'no-store' });
  const showError = relayUI.note('danteErr');

  function reach(d) {
    if (d.reachable === true) return relayUI.pill('answers', 'on');
    if (d.reachable === false && d.heartbeat) return relayUI.pill('alive, control port silent', 'warn');
    if (d.reachable === false) return relayUI.pill('no answer', 'warn');
    return relayUI.pill('not checked', 'idle');
  }

  function draw(s) {
    $('danteSum').textContent = s.error ? 'problem' : (s.devices.length ? s.devices.length + ' found' : 'looking');
    $('danteStatus').textContent = s.error || (s.devices.length ? '' : 'No Dante devices found yet.');
    const rows = $('danteRows');
    rows.replaceChildren();
    s.devices.forEach(function (d) {
      const model = [d.manufacturer, d.model].filter(Boolean).join(' ') + (d.via ? ' (Dante Via)' : '');
      const info = d.info || {};
      const facts = [info.sample_rate ? info.sample_rate / 1000 + ' kHz' : '',
        info.tx !== undefined ? info.tx + ' out, ' + info.rx + ' in' : ''].filter(Boolean).join(', ');
      rows.appendChild(el('tr', '',
        el('td', '', d.name),
        el('td', '', d.ips.join(', ') || d.host),
        el('td', '', model),
        el('td', '', reach(d), ' ' + (d.arc_port || '')),
        el('td', '', (facts ? facts + '. ' : '') + (d.transmit.length ? d.transmit.length + ' named: ' + d.transmit.join(', ') : 'none announced'))));
    });
    $('danteScan').disabled = !can('dante.manage');
    $('danteProblems').textContent = s.problems.length ? s.problems.map(function (p) { return p.text; }).join('. ') : 'None.';
    $('danteClock').textContent = s.leader ? 'Clock leader: ' + s.leader : '';
    const lv = $('danteLevels');
    lv.replaceChildren();
    s.levels.forEach(function (d) {
      d.channels.forEach(function (c) {
        lv.appendChild(el('tr', '', el('td', '', d.device), el('td', '', c.channel), el('td', '', String(c.level))));
      });
    });
    const stars = $('danteStars');
    stars.replaceChildren();
    s.devices.forEach(function (d) {
      d.transmit.forEach(function (c) {
        const box = el('input');
        box.type = 'checkbox';
        box.checked = starred.has(d.name + '\n' + c);
        box.disabled = !can('dante.manage');
        box.onchange = async function () {
          showError('');
          try {
            const r = await send('POST', '/api/admin/dante/star', { device: d.name, channel: c, on: box.checked });
            starred = new Set(r.starred.map(function (x) { return x[0] + '\n' + x[1]; }));
          } catch (e) { showError(e.message); box.checked = !box.checked; }
        };
        stars.appendChild(el('label', '', box, ' ' + d.name + ': ' + c));
      });
    });
  }

  let starred = new Set();
  async function history() {
    try {
      const h = await send('GET', '/api/admin/dante/history');
      const rows = $('danteHistory');
      rows.replaceChildren();
      h.history.forEach(function (e) {
        rows.appendChild(el('tr', '', el('td', '', e.at), el('td', '', e.device), el('td', '', e.kind), el('td', '', e.detail)));
      });
    } catch (e) { /* keeps what it shows */ }
  }


  // ------------------------------------------------------------------------------------------------ routing (#259)
  const showRouteError = relayUI.note('danteErr');
  let routeState = null;
  let routeSig = '';

  function button(text, onclick, disabled) {
    const b = el('button', '', text);
    b.type = 'button';
    b.disabled = !!disabled;
    b.onclick = onclick;
    return b;
  }

  function fillFilter(select, names, keep) {
    select.replaceChildren(el('option', '', 'All devices'));
    select.firstChild.value = '';
    names.forEach(function (n) { const o = el('option', '', n); o.value = n; select.appendChild(o); });
    select.value = names.indexOf(keep) >= 0 ? keep : '';
  }

  function src(c, device) { return c + '@' + device; }

  async function change(rxDevice, channel, source, was) {
    const where = rxDevice + ' receive channel ' + channel;
    const text = source
      ? 'Subscribe ' + where + ' to ' + source + '? ' + (was ? 'It replaces ' + was + '.' : 'It has no subscription now.')
      : 'Clear ' + where + '? It now listens to ' + was + '.';
    if (!(await relayUI.confirm(text))) return;
    showRouteError('');
    try {
      const r = await send('POST', '/api/admin/dante/routing/subscribe',
        { device: rxDevice, channel: channel, source: source, confirm: true });
      const row = r.result;
      $('danteRouteStatus').textContent = row.result === 'ok' ? where + ' now listens to ' + (source || 'nothing') + '.'
        : where + ': ' + row.message;
    } catch (e) { showRouteError(e.message); }
    last = ''; refresh();
  }

  async function lock(device, channel, on) {
    showRouteError('');
    try { await send('POST', '/api/admin/dante/routing/lock', { device: device, channel: channel, on: on }); }
    catch (e) { showRouteError(e.message); }
    last = ''; refresh();
  }

  function drawRouting(r) {
    const rxNames = r.devices.filter(function (d) { return d.receive && d.receive.length; }).map(function (d) { return d.name; });
    const txNames = r.devices.filter(function (d) { return d.transmit.length; }).map(function (d) { return d.name; });
    const rxSel = $('danteRxFilter'), txSel = $('danteTxFilter');
    if (rxSel.options.length !== rxNames.length + 1) fillFilter(rxSel, rxNames, rxSel.value);
    if (txSel.options.length !== txNames.length + 1) fillFilter(txSel, txNames, txSel.value);
    const control = can('dante.control');
    const locked = new Set(r.locks.map(function (l) { return l[0] + '\n' + l[1]; }));
    const cols = [];
    r.devices.forEach(function (d) {
      if (txSel.value && d.name !== txSel.value) return;
      d.transmit.forEach(function (c) { cols.push({ device: d.name, channel: c, source: src(c, d.name) }); });
    });
    const head = $('danteMatrixHead');
    head.replaceChildren(el('tr', '', el('th', '', 'Receive channel'), el('th', '', 'Listens to'), el('th', '', ''),
      cols.map(function (c) { return el('th', '', c.device + ': ' + c.channel); })));
    const body = $('danteMatrix');
    body.replaceChildren();
    r.devices.forEach(function (d) {
      if ((rxSel.value && d.name !== rxSel.value) || !d.receive) return;
      d.receive.forEach(function (rc) {
        const was = rc.status === 'none' ? '' : rc.source;
        const isLocked = locked.has(d.name + '\n' + rc.channel);
        const cells = cols.map(function (c) {
          const on = was.toLowerCase() === c.source.toLowerCase();
          const b = button(on ? '●' : '·', function () { change(d.name, rc.channel, on ? '' : c.source, was); },
            !control || isLocked || !!d.read_only);
          b.title = (on ? 'Clear ' : 'Subscribe to ') + c.source;
          return el('td', '', b);
        });
        const lockBtn = button(isLocked ? 'Locked' : 'Lock', function () { lock(d.name, rc.channel, !isLocked); },
          !can('dante.manage'));
        const status = (rc.status === 'unresolved' ? ' (cannot find it)' : '') + (d.read_only ? ' (read-only: ' + d.read_only + ')' : '');
        body.appendChild(el('tr', '', el('td', '', d.name + ' ' + rc.channel), el('td', '', (was || 'nothing') + status),
          el('td', '', lockBtn), cells));
      });
    });
    $('danteRouteStatus').textContent = body.children.length ? '' :
      'Relay has not read any receive channels yet. A device must answer on its control port first.';
    const u = r.undo;
    $('danteUndo').disabled = !u || !control;
    $('danteUndoNote').textContent = u ? 'Last change: ' + u.label + ' by ' + u.by + ' (' + u.count + ' subscription' +
      (u.count === 1 ? '' : 's') + '). Undo restores what it replaced.' : '';
    drawPresets(r.presets);
  }

  $('danteUndo').onclick = async function () {
    if (!(await relayUI.confirm('Undo the last routing change? ' + (routeState.undo ? routeState.undo.label : '')))) return;
    showRouteError('');
    try {
      const r = await send('POST', '/api/admin/dante/routing/undo', { confirm: true });
      $('danteRouteStatus').textContent = 'Undone: ' + r.results.map(function (x) {
        return x.device + ' ' + x.channel + ' ' + x.result; }).join(', ') + '.';
    } catch (e) { showRouteError(e.message); }
    last = ''; refresh();
  };
  $('danteRxFilter').onchange = $('danteTxFilter').onchange = function () { if (routeState) drawRouting(routeState); };

  async function routing() {
    try {
      const r = await send('GET', '/api/admin/dante/routing');
      const sig = JSON.stringify(r);
      if (sig !== routeSig) { routeSig = sig; routeState = r; drawRouting(r); }
    } catch (e) { /* keeps what it shows */ }
  }

  // ------------------------------------------------------------------------------------------------ presets (#260)
  function line(e) { return e.device + ' channel ' + e.channel + ': ' + (e.from || 'nothing') + ' → ' + (e.to || 'nothing'); }

  function drawPresets(list) {
    const rows = $('dantePresets');
    rows.replaceChildren();
    list.forEach(function (p) {
      rows.appendChild(el('tr', '', el('td', '', p.name), el('td', '', p.saved_at + (p.saved_by ? ' by ' + p.saved_by : '')),
        el('td', '', String(p.count)),
        el('td', '', button('Compare', function () { compare(p); }),
          button('Delete', function () { removePreset(p); }, !can('dante.manage')))));
    });
    $('dantePresetSave').disabled = !can('dante.manage');
  }

  async function compare(p) {
    showRouteError('');
    const box = $('dantePresetDiff');
    try {
      const c = await send('GET', '/api/admin/dante/presets/' + p.id + '/compare');
      box.replaceChildren(el('h4', '', 'Preset ' + p.name + ' against the live network'));
      box.appendChild(el('p', 'note', c.changes.length ? 'Applying would change ' + c.changes.length + ' subscription' +
        (c.changes.length === 1 ? '' : 's') + '; ' + c.same + ' already match.' : 'Nothing would change: ' + c.same + ' already match.'));
      const ul = el('ul');
      c.changes.forEach(function (e) { ul.appendChild(el('li', '', line(e))); });
      c.locked.forEach(function (e) { ul.appendChild(el('li', '', 'Skipped (locked): ' + line(e))); });
      c.missing.forEach(function (e) { ul.appendChild(el('li', '', 'Missing: ' + line(e) + ' (' + e.why + ')')); });
      box.appendChild(ul);
      box.appendChild(button('Apply preset', function () { apply(p, c); }, !can('dante.control') || !c.changes.length));
    } catch (e) { showRouteError(e.message); }
  }

  async function apply(p, c) {
    if (!(await relayUI.confirm('Apply preset ' + p.name + '? It changes ' + c.changes.length + ' subscription' +
      (c.changes.length === 1 ? '' : 's') + (c.locked.length ? ' and skips ' + c.locked.length + ' locked' : '') +
      '. Undo restores the routing from before.'))) return;
    showRouteError('');
    try {
      const r = await send('POST', '/api/admin/dante/presets/' + p.id + '/apply', { confirm: true });
      const box = $('dantePresetDiff');
      box.replaceChildren(el('h4', '', 'Applied ' + p.name + (r.failed ? ': ' + r.failed + ' failed' : '')));
      const ul = el('ul');
      r.results.forEach(function (x) { ul.appendChild(el('li', '', line(x) + ': ' + (x.result === 'ok' ? 'done' : x.message))); });
      r.locked.forEach(function (e) { ul.appendChild(el('li', '', 'Skipped (locked): ' + line(e))); });
      r.missing.forEach(function (e) { ul.appendChild(el('li', '', 'Missing: ' + line(e) + ' (' + e.why + ')')); });
      box.appendChild(ul);
    } catch (e) { showRouteError(e.message); }
    last = ''; refresh();
  }

  async function removePreset(p) {
    if (!(await relayUI.confirm('Delete the preset ' + p.name + '?'))) return;
    try { await send('DELETE', '/api/admin/dante/presets/' + p.id); $('dantePresetDiff').replaceChildren(); }
    catch (e) { showRouteError(e.message); }
    routeSig = ''; routing();
  }

  $('dantePresetSave').onclick = async function () {
    const name = $('dantePresetName').value.trim();
    if (!name) { showRouteError('Give the preset a name.'); return; }
    const only = $('danteRxFilter').value;
    showRouteError('');
    try {
      const r = await send('POST', '/api/admin/dante/presets', { name: name, devices: only ? [only] : [] });
      $('danteRouteStatus').textContent = 'Saved ' + r.saved.name + ' (' + r.saved.count + ' receive channels).';
      $('dantePresetName').value = '';
    } catch (e) { showRouteError(e.message); }
    routeSig = ''; routing();
  };

  let last = '';
  async function refresh() {
    routing();
    try {
      const s = await send('GET', '/api/admin/dante');
      const sig = JSON.stringify(s);
      if (sig !== last) { last = sig; draw(s); history(); }
    } catch (e) { /* the page keeps what it shows */ }
  }
  $('danteScan').onclick = async function () {
    showError('');
    $('danteScan').disabled = true;
    try {
      const s = await send('POST', '/api/admin/dante/scan');
      last = JSON.stringify(s); draw(s);
    } catch (e) { showError(e.message); }
    $('danteScan').disabled = !can('dante.manage');
  };
  refresh();
  setInterval(function () { if (relayUI.shown($('page-dante'))) refresh(); }, 5000);
})();
