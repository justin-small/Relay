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
  }

  let last = '';
  async function refresh() {
    try {
      const s = await send('GET', '/api/admin/dante');
      const sig = JSON.stringify(s);
      if (sig !== last) { last = sig; draw(s); }
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
