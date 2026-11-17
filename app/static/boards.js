/* The Boards page in the operator panel (#156): list, make, rename, make public, duplicate, export, import and delete
   boards. Laying a board out happens on the board itself (/board/<slug>?edit=1), with the shared editor over the live
   board. Everything from the server is set as text. */
(function () {
  'use strict';
  if (!document.getElementById('page-boards')) return;
  const $ = function (id) { return document.getElementById(id) || document.createElement('div'); };
  const canManage = typeof window.relayCan === 'function' && window.relayCan('boards.manage');
  let list = [];

  function showError(t) { $('bdErr').textContent = t || ''; $('bdErr').hidden = !t; }
  async function call(method, path, body) {
    const res = await fetch(path, { method: method, cache: 'no-store', credentials: 'same-origin',
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body) });
    const out = await res.json().catch(function () { return {}; });
    if (!res.ok) {
      if (window.RelayAdmin) window.RelayAdmin.sessionEnded(res, out);
      throw new Error(out.error || out.detail || 'That did not work.');
    }
    return out;
  }
  const el = relayUI.el;
  function button(text, fn, cls) {
    const b = el('button', cls || '', text); b.type = 'button';
    b.onclick = async function () { showError(''); b.disabled = true; try { await fn(); } catch (e) { showError(e.message); } b.disabled = false; };
    return b;
  }

  function render() {
    const rows = $('bdRows'); rows.textContent = '';
    if (!list.length) {
      const tr = el('tr'); const td = el('td', 'muted', canManage ? 'No boards yet. Name one above to make it.' : 'No boards yet.');
      td.colSpan = 5; tr.appendChild(td); rows.appendChild(tr); return;
    }
    list.forEach(function (b) {
      const tr = el('tr');
      const name = el('td');
      if (canManage) {
        const i = el('input'); i.value = b.name; i.maxLength = 60; i.setAttribute('aria-label', 'Name of ' + b.name);
        i.onchange = async function () { showError(''); try { load(await call('PUT', '/api/admin/boards/' + encodeURIComponent(b.id), { name: i.value })); } catch (e) { showError(e.message); i.value = b.name; } };
        name.appendChild(i);
      } else name.textContent = b.name;
      tr.appendChild(name);
      const addr = el('td'); const a = el('a', '', '/board/' + b.id); a.href = '/board/' + encodeURIComponent(b.id); a.target = '_blank'; a.rel = 'noopener';
      addr.appendChild(a); tr.appendChild(addr);
      const pub = el('td'); const cb = el('input'); cb.type = 'checkbox'; cb.checked = !!b.public; cb.disabled = !canManage;
      cb.setAttribute('aria-label', b.name + ' is public');
      cb.onchange = async function () {
        if (cb.checked && !(await relayUI.confirm('Anyone who can reach Relay, on the viewer network too, will be able to open "' + b.name + '". Continue?'))) { cb.checked = false; return; }
        showError(''); try { load(await call('PUT', '/api/admin/boards/' + encodeURIComponent(b.id), { public: cb.checked })); } catch (e) { showError(e.message); cb.checked = !cb.checked; }
      };
      pub.appendChild(cb); tr.appendChild(pub);
      tr.appendChild(el('td', 'muted', b.cols + ' × ' + b.rows + ' · ' + b.ratio + ' · ' + b.items.length + (b.items.length === 1 ? ' widget' : ' widgets')));
      const act = el('td'); const box = el('div', 'ctl'); box.style.justifyContent = 'flex-end';
      const open = el('a', 'btnlink', canManage ? 'Edit' : 'Open'); open.href = '/board/' + encodeURIComponent(b.id) + (canManage ? '?edit=1' : '');
      open.target = '_blank'; open.rel = 'noopener'; box.appendChild(open);
      if (canManage) box.appendChild(button('Duplicate', async function () { load(await call('POST', '/api/admin/boards/' + encodeURIComponent(b.id) + '/duplicate')); }));
      box.appendChild(button('Export', async function () {
        const data = await call('GET', '/api/admin/boards/' + encodeURIComponent(b.id) + '/export');
        const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
        const link = el('a'); link.href = url; link.download = b.id + '.relayboard.json'; document.body.appendChild(link); link.click(); link.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      }));
      if (canManage) box.appendChild(button('Delete', async function () {
        if (!(await relayUI.confirm('Delete the board "' + b.name + '"? Its address stops working.'))) return;
        load(await call('DELETE', '/api/admin/boards/' + encodeURIComponent(b.id)));
      }, 'danger'));
      act.appendChild(box); tr.appendChild(act);
      rows.appendChild(tr);
    });
  }
  function load(d) { list = d.boards || []; render(); }
  async function refresh() { try { load(await call('GET', '/api/admin/boards')); } catch (e) { showError(e.message); } }

  if (canManage) {
    $('bdNew').onclick = async function () {
      showError('');
      try {
        const d = await call('POST', '/api/admin/boards', { name: $('bdName').value, ratio: $('bdRatio').value });
        $('bdName').value = ''; load(d);
      } catch (e) { showError(e.message); }
    };
    $('bdName').onkeydown = function (e) { if (e.key === 'Enter') $('bdNew').click(); };
    $('bdImport').onchange = async function () {
      const f = $('bdImport').files[0]; $('bdImport').value = '';
      if (!f) return;
      showError('');
      try {
        if (f.size > 512 * 1024) throw new Error('That file is too big to be a board.');
        let data;
        try { data = JSON.parse(await f.text()); } catch (e) { throw new Error('That is not a Relay board file.'); }
        load(await call('POST', '/api/admin/boards/import', data));
      } catch (e) { showError(e.message); }
    };
  }
  window.addEventListener('hashchange', function () { if (location.hash === '#boards') refresh(); });
  if (location.hash === '#boards') refresh();
})();
