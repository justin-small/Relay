/* The Web & YouTube admin page (#210): save web page and YouTube links, with Relay's check of each. Loaded by
   admin.html only while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-signageweb')) return;
  const $ = R.$, api = R.api, post = R.post;
  function can(perm) { return typeof window.relayCan !== 'function' || !!window.relayCan(perm); }
  function showError(text) { $('swErr').textContent = text || ''; $('swErr').hidden = !text; }

  const KIND = { web: 'Web page', youtube: 'YouTube' };

  async function send(method, path, body) {
    const res = await fetch(path, { method: method, headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), credentials: 'same-origin' });
    const data = await res.json().catch(function () { return {}; });
    if (!res.ok) throw new Error(data.error || data.detail || 'That did not work.');
    return data;
  }

  function row(link, manage) {
    const d = document.createElement('div');
    d.className = 'row';
    d.style.cssText = 'align-items:center;gap:12px;padding:8px 0;border-top:1px solid var(--line, #8883)';
    const info = document.createElement('div');
    info.style.cssText = 'flex:1;min-width:0';
    const name = document.createElement('div');
    name.textContent = link.name;
    name.style.fontWeight = '600';
    const meta = document.createElement('div');
    meta.className = 'note';
    meta.textContent = KIND[link.kind] + ' · ' + link.url;
    meta.style.cssText = 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap';
    const c = link.check || {};
    const state = document.createElement('div');
    state.className = 'note ' + (c.state === 'ok' ? 'ok' : (c.state === 'blocked' || c.state === 'gone' ? 'err' : ''));
    state.textContent = c.note || 'Not checked yet.';
    info.append(name, meta, state);
    d.appendChild(info);
    if (manage) {
      const check = document.createElement('button');
      check.type = 'button'; check.textContent = 'Check again';
      check.onclick = async function () {
        check.disabled = true; showError('');
        try { await send('POST', '/api/admin/signage-web/links/' + link.id + '/check'); await refresh(); }
        catch (e) { showError(e.message); check.disabled = false; }
      };
      const rename = document.createElement('button');
      rename.type = 'button'; rename.textContent = 'Rename';
      rename.onclick = async function () {
        const n = prompt('Name for this link', link.name);
        if (n === null) return;
        showError('');
        try { await send('PUT', '/api/admin/signage-web/links/' + link.id, { name: n }); await refresh(); }
        catch (e) { showError(e.message); }
      };
      const del = document.createElement('button');
      del.type = 'button'; del.className = 'forget'; del.textContent = 'Remove';
      del.onclick = async function () {
        if (!confirm('Remove "' + link.name + '"? Playlists that use it will skip it.')) return;
        showError('');
        try { await send('DELETE', '/api/admin/signage-web/links/' + link.id); await refresh(); }
        catch (e) { showError(e.message); }
      };
      d.append(check, rename, del);
    }
    return d;
  }

  async function refresh() {
    let data;
    try { data = await api('/api/admin/signage-web'); } catch (e) { return; }
    const manage = can('signage_web.manage');
    const list = $('swList');
    list.textContent = '';
    for (const l of data.links) list.appendChild(row(l, manage));
    $('swEmpty').hidden = data.links.length > 0;
    $('swSum').textContent = data.links.length ? data.links.length + ' saved' : '';
    $('swAdd').disabled = !manage;
  }

  $('swAdd').addEventListener('click', async function () {
    const url = $('swUrl').value.trim();
    if (!url) { showError('Enter a web address or a YouTube link.'); return; }
    showError('');
    $('swAdd').disabled = true; $('swAdd').textContent = 'Checking…';
    try {
      await send('POST', '/api/admin/signage-web/links', { url: url, name: $('swName').value.trim() });
      $('swUrl').value = ''; $('swName').value = '';
      await refresh();
    } catch (e) { showError(e.message); }
    finally { $('swAdd').disabled = !can('signage_web.manage'); $('swAdd').textContent = 'Add'; }
  });

  document.addEventListener('relay:config', refresh);
  refresh();
})();
