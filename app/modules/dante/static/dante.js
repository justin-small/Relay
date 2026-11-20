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
      rows.appendChild(el('tr', '',
        el('td', '', d.name),
        el('td', '', d.ips.join(', ') || d.host),
        el('td', '', model),
        el('td', '', reach(d), ' ' + (d.arc_port || '')),
        el('td', '', d.transmit.length ? d.transmit.length + ': ' + d.transmit.join(', ') : 'none announced')));
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

  let last = '';
  async function refresh() {
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
