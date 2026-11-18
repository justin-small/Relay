/* Going out on the NDI admin page (#298, #299): the router as a click grid (a row per feed Relay sends, a column per
   source), its presets, and Relay's own pages sent as NDI. Drawn by ndi.js on every update; a part is rebuilt only
   when what it shows changes, so a click during a live update is not lost. */
(function () {
  const N = window.RelayNdi, R = window.RelayAdmin;
  if (!N || !R || !document.getElementById('ndiRtTable')) return;
  const $ = R.$;
  const el = relayUI.el;
  const changed = N.changed;
  async function act(method, path, body) {
    N.showError('');
    try { N.draw(await N.send(method, path, body)); } catch (e) { N.showError(e.message); }
  }
  function btn(text, fn, cls, disabled) { const b = el('button', { type: 'button', className: cls || '', disabled: !!disabled }, text); b.onclick = fn; return b; }

  function drawRouter(s) {
    const rt = s.router.routes, can = N.can('ndi.manage');
    $('ndiRtNone').hidden = rt.length > 0; $('ndiRtTable').hidden = !rt.length;
    // The columns: every input except Relay's own feeds (a feed cannot show itself), plus any source a feed is on
    // that is not seen now, then Nothing.
    const own = {}; rt.forEach(function (r) { own[r.ndi] = 1; });
    const cols = s.inputs.filter(function (i) {
      return !Object.keys(own).some(function (n) { return i.name === n || i.name.indexOf('(' + n + ')') >= 0; });
    }).map(function (i) {
      const src = s.sources.filter(function (x) { return x.name === i.name; })[0];   // the short name reads best in a column
      return [i.name, src && src.source ? src.source : i.label, i.online];
    });
    rt.forEach(function (r) { if (r.source && !cols.some(function (c) { return c[0] === r.source; })) cols.push([r.source, r.source + ' (not seen)', false]); });
    const rows = rt.map(function (r) { return [r.id, r.name, r.ndi, r.source, r.error]; });
    if (changed('rt.router', [cols, rows, can])) {
      const t = $('ndiRtTable'); t.textContent = '';
      const head = el('tr'); head.appendChild(el('th', '', ''));
      cols.forEach(function (c) { head.appendChild(el('th', { scope: 'col', title: c[1], className: c[2] ? '' : 'off' }, el('span', '', c[1]))); });
      head.appendChild(el('th', { scope: 'col' }, el('span', '', 'Nothing')));
      t.appendChild(el('thead', '', head));
      const body = el('tbody');
      rows.forEach(function (r) {
        const tr = el('tr');
        const th = el('th', { scope: 'row' }); th.appendChild(el('b', '', r[1])); th.appendChild(el('code', 'hint', r[2]));
        if (r[4]) th.appendChild(el('span', 'hint err', r[4]));
        tr.appendChild(th);
        cols.concat([['', 'Nothing', true]]).forEach(function (c) {
          const on = r[3] === c[0];
          const td = el('td');
          const b = btn('', function () { if (!on) act('PUT', '/api/admin/ndi/router/outputs/' + r[0] + '/source', { source: c[0] }); },
            'ndixp' + (on ? ' on' : ''), !can);
          b.setAttribute('aria-pressed', String(on));
          b.setAttribute('aria-label', (c[0] ? 'Show ' + c[1] : 'Show nothing') + ' on ' + r[1]);
          b.title = c[1] + ' → ' + r[1];
          td.appendChild(b); tr.appendChild(td);
        });
        const rm = el('td'); rm.appendChild(btn('Remove', async function () {
          if (await relayUI.confirm('Remove the feed "' + r[1] + '"? A TV set to it goes black.')) act('DELETE', '/api/admin/ndi/router/outputs/' + r[0]);
        }, 'sm quiet', !can));
        tr.appendChild(rm);
        body.appendChild(tr);
      });
      t.appendChild(body);
    }
    const names = {}; rt.forEach(function (r) { names[r.id] = r.name; });
    const salvos = s.router.salvos.map(function (v) {
      return [v.id, v.name, Object.keys(v.routes).map(function (k) { return (names[k] || '?') + ' → ' + (v.routes[k] || 'nothing'); }).join(', ')];
    });
    if (changed('rt.salvos', [salvos, can])) {
      const list = $('ndiSalvos'); list.textContent = '';
      if (!salvos.length) list.appendChild(el('p', 'note', 'No presets yet.'));
      salvos.forEach(function (v) {
        const card = el('div', 'ndipreset');
        const t = el('div'); t.appendChild(el('b', '', v[1])); t.appendChild(el('span', 'hint', v[2])); card.appendChild(t);
        const ctl = el('div', 'ctl');
        ctl.appendChild(btn('Take', function () { act('POST', '/api/admin/ndi/router/salvos/' + v[0] + '/take'); }, 'sm primary', !can));
        ctl.appendChild(btn('Update to current', function () { act('POST', '/api/admin/ndi/router/salvos', { id: v[0], name: v[1] }); }, 'sm', !can));
        ctl.appendChild(btn('Delete', async function () {
          if (await relayUI.confirm('Delete the preset "' + v[1] + '"?')) act('DELETE', '/api/admin/ndi/router/salvos/' + v[0]);
        }, 'sm quiet', !can));
        card.appendChild(ctl); list.appendChild(card);
      });
    }
    $('ndiRtAdd').disabled = $('ndiRtName').disabled = $('ndiSalvoSave').disabled = $('ndiSalvoName').disabled = !can;
    $('ndiSalvoSave').hidden = $('ndiSalvoName').hidden = !rt.length;
  }

  function pageState(d) {
    if (d.state === 'on') return ['ok', 'Sending, ' + d.fps + ' fps, ' + d.cpu + '% of one core'];
    if (d.state === 'error') return ['err', d.error];
    if (d.state === 'starting') return ['warn', 'Starting'];
    return ['', 'Off'];
  }
  function drawPages(s) {
    const warn = $('ndiPagesWarn'); warn.textContent = s.pages_error || ''; warn.hidden = !s.pages_error;
    const can = N.can('ndi.manage');
    const rows = s.pages.map(function (d) { return [d.id, d.name, d.group, d.alpha, d.ndi, d.on, pageState(d)]; });
    const box = $('ndiPages');
    if (!box.contains(document.activeElement) && changed('rt.pages', [rows, can])) {
      // The pages being sent first; every other page Relay could send (one per language) folded away below them.
      const wasOpen = !!box.querySelector('details[open]');
      box.textContent = '';
      const on = rows.filter(function (d) { return d[5]; }), off = rows.filter(function (d) { return !d[5]; });
      if (!on.length) box.appendChild(el('p', 'note', 'Nothing is being sent. Turn on a page below.'));
      on.forEach(function (d) { box.appendChild(pageRow(d, can)); });
      if (off.length) {
        const more = el('details', 'sect'); more.open = wasOpen || !on.length;
        more.appendChild(el('summary', '', 'Other pages you can send (' + off.length + ')'));
        let group = '';
        off.forEach(function (d) {
          if (d[2] !== group) { group = d[2]; more.appendChild(el('h4', '', group)); }
          more.appendChild(pageRow(d, can));
        });
        box.appendChild(more);
      }
    }
    if (document.activeElement !== $('ndiOutFps')) $('ndiOutFps').value = s.settings.out_fps;
    if (document.activeElement !== $('ndiOutSize')) $('ndiOutSize').value = s.settings.out_size;
    $('ndiOutFps').disabled = $('ndiOutSize').disabled = $('ndiPagesSave').disabled = !can;
  }
  function pageRow(d, can) {
    const row = el('div', 'ndioutrow');
    const sw = el('label', 'switch');
    const box2 = el('input', { type: 'checkbox', checked: !!d[5], disabled: !can }); box2.dataset.id = d[0];
    box2.setAttribute('aria-label', 'Send ' + d[1] + ' as NDI');
    box2.onchange = savePages;
    sw.appendChild(box2); sw.appendChild(el('span', 'slider'));
    row.appendChild(sw);
    const nm = el('div'); nm.appendChild(el('b', '', d[1]));
    if (d[3]) { nm.appendChild(document.createTextNode(' ')); nm.appendChild(el('span', 'badge muted', 'transparent')); }
    nm.appendChild(el('code', 'hint', d[4]));
    row.appendChild(nm);
    row.appendChild(el('span', 'ndistate ' + d[6][0], d[6][1]));
    return row;
  }
  function savePages() {
    const on = Array.prototype.filter.call($('ndiPages').querySelectorAll('input[type=checkbox]'), function (b) { return b.checked; })
      .map(function (b) { return b.dataset.id; });
    act('PUT', '/api/admin/ndi/outputs', { on: on, size: $('ndiOutSize').value, fps: +$('ndiOutFps').value });
  }
  window.RelayNdiMore = function (s) { drawRouter(s); drawPages(s); };
  $('ndiRtAdd').onclick = function () {
    const n = $('ndiRtName').value.trim(); if (!n) { $('ndiRtName').focus(); return; }
    $('ndiRtName').value = '';
    act('POST', '/api/admin/ndi/router/outputs', { name: n });
  };
  $('ndiSalvoSave').onclick = function () {
    const n = $('ndiSalvoName').value.trim(); if (!n) { $('ndiSalvoName').focus(); return; }
    $('ndiSalvoName').value = '';
    act('POST', '/api/admin/ndi/router/salvos', { name: n });
  };
  $('ndiPagesSave').onclick = savePages;
})();
