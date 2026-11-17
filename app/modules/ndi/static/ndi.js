/* The NDI admin page (#294): the sources found, live, and where to look for them. Loaded only while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-ndi')) return;
  const $ = R.$;
  let version = -1, polling = false;
  function can(perm) { return typeof window.relayCan !== 'function' || !!window.relayCan(perm); }
  function showError(t) { $('ndiErr').textContent = t || ''; $('ndiErr').hidden = !t; }
  async function send(method, path, body) {
    const res = await fetch(path, { method: method, headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), credentials: 'same-origin' });
    const data = await res.json().catch(function () { return {}; });
    if (!res.ok) throw new Error(data.error || data.detail || 'That did not work.');
    return data;
  }
  function cell(tr, text) { const td = document.createElement('td'); td.textContent = text; tr.appendChild(td); }
  function draw(s) {
    version = s.version;
    const warn = $('ndiNoRuntime');
    warn.textContent = s.available ? '' : s.error;
    warn.hidden = s.available;
    $('ndiSum').textContent = s.running ? s.sources.filter(function (x) { return x.online; }).length + ' online' : '';
    $('ndiNone').hidden = s.sources.length > 0;
    $('ndiTable').hidden = !s.sources.length;
    const body = $('ndiTable').querySelector('tbody');
    body.textContent = '';
    s.sources.forEach(function (x) {
      const tr = document.createElement('tr');
      cell(tr, x.source || x.name);
      cell(tr, x.machine);
      cell(tr, x.width ? x.width + 'x' + x.height + (x.fps ? ' ' + x.fps + ' fps' : '') : '—');
      cell(tr, x.groups.join(', '));
      cell(tr, x.address ? x.address + (x.port ? ':' + x.port : '') : '—');
      cell(tr, (x.online ? 'Online' : 'Offline') + (x.converter ? ' · converter set to ' + (x.converter.source || x.converter.source_ip) : ''));
      cell(tr, x.listeners ? String(x.listeners) : '—');
      body.appendChild(tr);
    });
    if (document.activeElement !== $('ndiGroups')) $('ndiGroups').value = s.settings.groups.join(', ');
    if (document.activeElement !== $('ndiIps')) $('ndiIps').value = s.settings.extra_ips.join(', ');
    $('ndiScan').disabled = !s.running || !can('ndi.manage');
    $('ndiSave').disabled = !can('ndi.manage');
  }
  async function poll() {
    if (polling) return;
    polling = true;
    try {
      // Waits on the server for a change, so the list is live; a quiet page costs one request every 20 s.
      draw(await send('GET', '/api/admin/ndi?since=' + version));
    } catch (e) { await new Promise(function (r) { setTimeout(r, 4000); }); }
    polling = false;
    if (document.getElementById('page-ndi')) setTimeout(poll, 50);
  }
  function list(text) { return text.split(',').map(function (x) { return x.trim(); }).filter(Boolean); }
  $('ndiScan').onclick = async function () {
    showError('');
    try { draw(await send('POST', '/api/admin/ndi/scan')); } catch (e) { showError(e.message); }
  };
  $('ndiSave').onclick = async function () {
    showError('');
    try { draw(await send('PUT', '/api/admin/ndi/settings', { groups: list($('ndiGroups').value), extra_ips: list($('ndiIps').value) })); }
    catch (e) { showError(e.message); }
  };
  poll();
})();
