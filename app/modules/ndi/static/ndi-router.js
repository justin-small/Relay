/* Going out on the NDI admin page (#298, #299): the router as core's route editor (a row per feed Relay sends, a column
   per source, #546), its presets, and Relay's own pages sent as NDI. Drawn by ndi.js on every update; a part is rebuilt only
   when what it shows changes, so a click during a live update is not lost. */
(function () {
  const N = window.RelayNdi, R = window.RelayAdmin;
  if (!N || !R || !document.getElementById('ndiRtGrid')) return;
  const $ = R.$;
  const el = relayUI.el;
  const changed = N.changed;
  async function act(method, path, body) {
    N.showError('');
    try { N.draw(await N.send(method, path, body)); } catch (e) { N.showError(e.message); }
  }
  function btn(text, fn, cls, disabled) { const b = el('button', { type: 'button', className: cls || '', disabled: !!disabled }, text); b.onclick = fn; return b; }

  // The grid is core's route editor (relayUI.routeEditor, #546): a row per feed, a column per source. Core stores the
  // route and this module applies it. A feed's own line (its NDI name, an error, Remove) is drawn under its name.
  let editor = null, feeds = {}, own = [], may = false;
  function feedOf(c) {
    const e = c.ends.filter(function (x) { return x.provider === 'ndi' && x.key.indexOf('route:') === 0; })[0];
    return e ? feeds[e.key.slice(6)] : null;
  }
  function isOwn(c) {
    return c.ends.some(function (e) {
      if (e.provider !== 'ndi' || e.key.indexOf('src:') !== 0) return false;
      const name = e.key.slice(4);
      return own.some(function (n) { return name === n || name.indexOf('(' + n + ')') >= 0; });
    });
  }
  function feedLine(c) {
    const r = feedOf(c);
    if (!r) return null;
    const box = el('div', 'note');
    if (r.error) box.appendChild(el('div', 'hint err', r.error));
    box.appendChild(btn('Remove', async function () {
      if (await relayUI.confirm('Remove the feed "' + r.name + '"? A TV set to it goes black.')) act('DELETE', '/api/admin/ndi/router/outputs/' + r.id);
    }, 'sm quiet', !may));
    return box;
  }

  function drawRouter(s) {
    const rt = s.router.routes, can = N.can('ndi.manage');
    $('ndiRtNone').hidden = rt.length > 0; $('ndiRtGrid').hidden = !rt.length;
    const rows = rt.map(function (r) { return [r.id, r.name, r.ndi, r.source, r.asked, r.state, r.error]; });
    const seen = s.sources.map(function (x) { return [x.name, x.online]; });
    if (changed('rt.router', [rows, seen, can])) {
      feeds = {}; rt.forEach(function (r) { feeds[r.id] = r; });
      own = rt.map(function (r) { return r.ndi; });
      may = can;
      if (!editor) {
        editor = relayUI.routeEditor($('ndiRtGrid'), { kind: 'video', provider: 'ndi', perm: 'ndi.manage',
          source: function (c) { return !isOwn(c); }, extra: feedLine,
          empty: 'Your feeds show here once NDI has started them.' });
      } else editor.reload();
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
