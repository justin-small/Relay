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

  let view = 'table';
  const EDGE = { ok: 'ok', unsubscribed: 'warn', broken: 'err', offline: 'err', missing: 'err', unknown: '' };

  function node(cls, title, sub) { return el('div', 'smnode ' + cls, el('b', '', title), sub ? el('small', '', sub) : ''); }
  function edge(cls, text) { return el('div', 'smedge ' + cls, text ? el('small', '', text) : ''); }

  function via(live) {
    const v = live.via;
    return v ? v.switch + ' port ' + v.port + (v.label ? ' (' + v.label + ')' : '') : '';
  }

  /* One line per patched channel: patch -> Dante channel -> receivers. A receive channel that is set to the
     channel but cannot find it gets a red edge, so the break shows on the link where it is. */
  function graph(s) {
    const box = $('smGraph');
    box.replaceChildren();
    (s.rows || []).forEach(function (r) {
      const live = r.live;
      const st = STATE[live.state] || STATE.unknown;
      const src = node('', r.name, [r.ch ? 'CH ' + r.ch : '', r.port, r.socket, r.upstream].filter(Boolean).join(' / '));
      const tx = live.source ? node(EDGE[live.state] || '', live.source, via(live)) : node('err', st[0], live.why);
      const out = [src, edge(live.source ? 'ok' : 'err'), tx];
      if (live.receivers.length) {
        const col = el('div', 'smcol');
        live.receivers.forEach(function (x) {
          const bad = x.status === 'unresolved';
          col.appendChild(el('div', 'smrow', edge(bad ? 'err' : 'ok', bad ? 'cannot find it' : ''),
            node((bad ? 'err' : 'ok') + (x.relay ? ' smrelay' : ''), x.device + ' ' + x.channel, x.relay ? 'Relay input' : '')));
        });
        out.push(col);
      } else if (live.source) {
        out.push(edge('warn', 'nobody receives it'), node('warn', 'No receiver', ''));
      }
      box.appendChild(el('div', 'smline', out));
    });
  }

  function draw(s) {
    const bad = (s.rows || []).filter(function (r) { return r.live.state !== 'ok' && r.live.state !== 'unknown'; }).length;
    $('smSum').textContent = !s.saved ? 'no list' : s.dante ? (bad ? bad + ' to look at' : 'all received') : 'Dante off';
    const box = $('smText');
    if (document.activeElement !== box && s.saved) box.value = s.saved.text;
    $('smSaved').textContent = (s.saved ? 'Saved ' + s.saved.at.replace('T', ' ').replace('Z', ' UTC') + (s.saved.by ? ' by ' + s.saved.by : '') + '. ' : '')
      + (s.problems || []).join(' ');
    $('smStatus').textContent = !s.saved ? 'Paste a patch list above.'
      : !s.dante ? 'The Dante module is off, so there is no live routing to compare.' : '';
    $('smImport').hidden = !(s.desks || []).length;
    const pick = $('smDesk');
    if (!pick.options.length || pick.dataset.n !== String(s.desks.length)) {
      pick.replaceChildren();
      (s.desks || []).forEach(function (d) { pick.appendChild(el('option', '', d.name + ' (' + d.channels + ' channels' + (d.file ? ', ' + d.file : '') + ')')).value = d.id; });
      pick.dataset.n = String((s.desks || []).length);
    }
    $('smUse').disabled = !can('signalmap.manage');
    $('smGraph').hidden = view !== 'graph';
    $('smTable').hidden = view !== 'table';
    graph(s);
    const rows = $('smRows');
    rows.replaceChildren();
    (s.rows || []).forEach(function (r) {
      const st = STATE[r.live.state] || STATE.unknown;
      rows.appendChild(el('tr', '',
        el('td', '', r.ch), el('td', '', r.name), el('td', '', r.port), el('td', '', r.socket), el('td', '', r.upstream),
        el('td', '', relayUI.pill(st[0], st[1]), r.live.source ? ' ' + r.live.source : '', r.live.why ? el('small', '', r.live.why) : ''),
        el('td', '', via(r.live)),
        el('td', '', r.live.receivers.map(function (x) { return x.device + ' ' + x.channel; }).join(', '))));
    });
    $('smUnpatched').textContent = !s.dante ? '' : (s.unpatched.length ? s.unpatched.join(', ') : 'Nothing: every Dante channel is in the patch list.');
    $('smSave').disabled = !can('signalmap.manage');
    $('smClear').disabled = !can('signalmap.manage') || !s.saved;
  }

  async function refresh() {
    try { draw(await send('GET', '/api/admin/signalmap')); showError(''); } catch (e) { showError(e.message); }
  }

  $('smView').addEventListener('click', function (e) {
    const b = e.target.closest('button[data-view]');
    if (!b) return;
    view = b.dataset.view;
    $('smView').querySelectorAll('button').forEach(function (x) { x.setAttribute('aria-pressed', String(x === b)); });
    $('smGraph').hidden = view !== 'graph';
    $('smTable').hidden = view !== 'table';
  });
  const use = button('Use this patch', async function () {
    if (!(await relayUI.confirm('Replace the patch list with the one read from this desk\'s show file?'))) return;
    $('smText').value = '';
    draw(await send('POST', '/api/admin/signalmap/import', { desk: $('smDesk').value }));
  });
  use.id = 'smUse';
  $('smUse').replaceWith(use);
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
