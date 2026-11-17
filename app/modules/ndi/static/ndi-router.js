/* The NDI router and page outputs on the NDI admin page (#298, #299). Drawn by ndi.js on every update. */
(function () {
  const N = window.RelayNdi, R = window.RelayAdmin;
  if (!N || !R || !document.getElementById('ndiRtTable')) return;
  const $ = R.$;
  let state = null;
  function el(tag, text, attrs) {
    const e = document.createElement(tag); if (text) e.textContent = text;
    Object.keys(attrs || {}).forEach(function (k) { e[k] = attrs[k]; }); return e;
  }
  async function act(method, path, body) {
    N.showError('');
    try { window.RelayNdiMore(await N.send(method, path, body)); } catch (e) { N.showError(e.message); }
  }
  function drawRouter(s) {
    const rt = s.router.routes, can = N.can('ndi.manage');
    $('ndiRtNone').hidden = rt.length > 0; $('ndiRtTable').hidden = !rt.length;
    const body = $('ndiRtTable').querySelector('tbody');
    if (!body.contains(document.activeElement)) {
      body.textContent = '';
      rt.forEach(function (r) {
        const tr = el('tr'); tr.appendChild(el('td', r.ndi));
        const sel = el('select', '', { disabled: !can }); sel.setAttribute('aria-label', 'Source for ' + r.name);
        sel.appendChild(el('option', 'Nothing', { value: '' }));
        const have = {};
        s.inputs.forEach(function (i) {
          if (i.name.indexOf('(' + r.ndi + ')') >= 0 || i.name === r.ndi) return;   // not itself
          have[i.name] = 1; sel.appendChild(el('option', i.label + (i.online ? '' : ' (offline)'), { value: i.name }));
        });
        if (r.source && !have[r.source]) sel.appendChild(el('option', r.source + ' (not seen)', { value: r.source }));
        sel.value = r.source;
        sel.onchange = function () { act('PUT', '/api/admin/ndi/router/outputs/' + r.id + '/source', { source: sel.value }); };
        const td = el('td'); td.appendChild(sel);
        if (r.error) td.appendChild(el('span', ' ' + r.error));
        tr.appendChild(td);
        const rm = el('button', 'Remove', { type: 'button', disabled: !can });
        rm.onclick = function () { act('DELETE', '/api/admin/ndi/router/outputs/' + r.id); };
        const t3 = el('td'); t3.appendChild(rm); tr.appendChild(t3); body.appendChild(tr);
      });
    }
    const names = {}; rt.forEach(function (r) { names[r.id] = r.name; });
    const list = $('ndiSalvos'); list.textContent = '';
    s.router.salvos.forEach(function (v) {
      const p = el('p'); p.className = 'note';
      p.appendChild(el('strong', v.name + ' '));
      p.appendChild(document.createTextNode(Object.keys(v.routes).map(function (k) { return (names[k] || '?') + ' → ' + (v.routes[k] || 'nothing'); }).join(', ') + ' '));
      const take = el('button', 'Take', { type: 'button', disabled: !can }); take.className = 'primary';
      take.onclick = function () { act('POST', '/api/admin/ndi/router/salvos/' + v.id + '/take'); };
      const upd = el('button', 'Update to current', { type: 'button', disabled: !can });
      upd.onclick = function () { act('POST', '/api/admin/ndi/router/salvos', { id: v.id, name: v.name }); };
      const del = el('button', 'Delete', { type: 'button', disabled: !can });
      del.onclick = function () { act('DELETE', '/api/admin/ndi/router/salvos/' + v.id); };
      [take, upd, del].forEach(function (b) { p.appendChild(b); });
      list.appendChild(p);
    });
    $('ndiRtAdd').disabled = $('ndiRtName').disabled = $('ndiSalvoSave').disabled = $('ndiSalvoName').disabled = !can;
  }
  function drawPages(s) {
    const warn = $('ndiPagesWarn'); warn.textContent = s.pages_error || ''; warn.hidden = !s.pages_error;
    const can = N.can('ndi.manage');
    const body = $('ndiPages').querySelector('tbody');
    if (!body.contains(document.activeElement)) {
      const on = {}; s.pages.forEach(function (d) { if (d.on) on[d.id] = 1; });
      body.textContent = '';
      let group = '';
      s.pages.forEach(function (d) {
        if (d.group !== group) { group = d.group; const g = el('tr'); const c = el('th', group, { colSpan: 4 }); g.appendChild(c); body.appendChild(g); }
        const tr = el('tr');
        const box = el('input', '', { type: 'checkbox', checked: !!d.on, disabled: !can }); box.dataset.id = d.id;
        box.setAttribute('aria-label', 'Send ' + d.name + ' as NDI');
        const c1 = el('td'); c1.appendChild(box); tr.appendChild(c1);
        tr.appendChild(el('td', d.name + (d.alpha ? ' (with transparency)' : '')));
        tr.appendChild(el('td', d.ndi));
        tr.appendChild(el('td', d.state === 'on' ? 'Sending, ' + d.fps + ' fps, ' + d.cpu + '% of a core' :
          d.state === 'error' ? d.error : d.state === 'starting' ? 'Starting' : 'Off'));
        body.appendChild(tr);
      });
    }
    if (document.activeElement !== $('ndiOutFps')) $('ndiOutFps').value = s.settings.out_fps;
    if (document.activeElement !== $('ndiOutSize')) $('ndiOutSize').value = s.settings.out_size;
    $('ndiOutFps').disabled = $('ndiOutSize').disabled = $('ndiPagesSave').disabled = !can;
  }
  window.RelayNdiMore = function (s) { state = s; drawRouter(s); drawPages(s); };
  $('ndiRtAdd').onclick = function () {
    const n = $('ndiRtName').value; $('ndiRtName').value = '';
    act('POST', '/api/admin/ndi/router/outputs', { name: n });
  };
  $('ndiSalvoSave').onclick = function () {
    const n = $('ndiSalvoName').value; $('ndiSalvoName').value = '';
    act('POST', '/api/admin/ndi/router/salvos', { name: n });
  };
  $('ndiPagesSave').onclick = function () {
    const on = Array.prototype.filter.call($('ndiPages').querySelectorAll('input[type=checkbox]'), function (b) { return b.checked; })
      .map(function (b) { return b.dataset.id; });
    act('PUT', '/api/admin/ndi/outputs', { on: on, size: $('ndiOutSize').value, fps: +$('ndiOutFps').value });
  };
})();
