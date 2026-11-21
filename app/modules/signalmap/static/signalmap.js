/* The Signal map admin page (#262): the pasted patch list against the live Dante routing.
   Loaded by admin.html only while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-signalmap')) return;
  const $ = R.$;
  const el = relayUI.el;
  const can = relayUI.can;
  const send = relayUI.client({ cache: 'no-store' });
  const showError = relayUI.note('smErr');
  const button = relayUI.buttons(showError);

  const STATE = {
    ok: ['received', 'on'], unsubscribed: ['nobody receives it', 'warn'], broken: ['cannot resolve', 'err'],
    offline: ['device silent', 'err'], missing: ['not on Dante', 'err'], unknown: ['not checked', 'idle']
  };

  function draw(s) {
    const bad = (s.rows || []).filter(function (r) { return r.live.state !== 'ok' && r.live.state !== 'unknown'; }).length;
    $('smSum').textContent = !s.saved ? 'no list' : s.dante ? (bad ? bad + ' to look at' : 'all received') : 'Dante off';
    const box = $('smText');
    if (document.activeElement !== box && s.saved) box.value = s.saved.text;
    $('smSaved').textContent = (s.saved ? 'Saved ' + s.saved.at.replace('T', ' ').replace('Z', ' UTC') + (s.saved.by ? ' by ' + s.saved.by : '') + '. ' : '')
      + (s.problems || []).join(' ');
    $('smStatus').textContent = !s.saved ? 'Paste a patch list above.'
      : !s.dante ? 'The Dante module is off, so there is no live routing to compare.' : '';
    const rows = $('smRows');
    rows.replaceChildren();
    (s.rows || []).forEach(function (r) {
      const st = STATE[r.live.state] || STATE.unknown;
      rows.appendChild(el('tr', '',
        el('td', '', r.ch), el('td', '', r.name), el('td', '', r.port), el('td', '', r.socket), el('td', '', r.upstream),
        el('td', '', relayUI.pill(st[0], st[1]), r.live.source ? ' ' + r.live.source : '', r.live.why ? el('small', '', r.live.why) : ''),
        el('td', '', r.live.receivers.map(function (x) { return x.device + ' ' + x.channel; }).join(', '))));
    });
    $('smUnpatched').textContent = !s.dante ? '' : (s.unpatched.length ? s.unpatched.join(', ') : 'Nothing: every Dante channel is in the patch list.');
    $('smSave').disabled = !can('signalmap.manage');
    $('smClear').disabled = !can('signalmap.manage') || !s.saved;
  }

  async function refresh() {
    try { draw(await send('GET', '/api/admin/signalmap')); showError(''); } catch (e) { showError(e.message); }
  }

  const save = button('Save patch list', async function () {
    draw(await send('PUT', '/api/admin/signalmap/patch', { text: $('smText').value }));
  }, 'primary');
  save.id = 'smSave';
  $('smSave').replaceWith(save);
  const clear = button('Remove', async function () {
    if (!(await relayUI.confirm('Remove the patch list?'))) return;
    $('smText').value = '';
    draw(await send('DELETE', '/api/admin/signalmap/patch'));
  }, 'danger');
  clear.id = 'smClear';
  $('smClear').replaceWith(clear);
  refresh();
  setInterval(function () { if (relayUI.shown($('page-signalmap'))) refresh(); }, 5000);
})();
