/* The Tally Arbiter admin page (#244): the server's address, its connection and the cameras it serves. Loaded by
   admin.html only while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-tallyarbiter')) return;
  const $ = R.$;
  function can(perm) { return typeof window.relayCan !== 'function' || !!window.relayCan(perm); }
  function showError(text) { $('taErr').textContent = text || ''; $('taErr').hidden = !text; }

  async function send(method, path, body) {
    const res = await fetch(path, { method: method, headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), credentials: 'same-origin' });
    const data = await res.json().catch(function () { return {}; });
    if (!res.ok) throw new Error(data.error || data.detail || 'That did not work.');
    return data;
  }

  let editing = false;
  function draw(state) {
    if (!editing) { $('taHost').value = state.host; $('taPort').value = state.port; }
    $('taSum').textContent = state.connected ? 'connected' : (state.configured ? 'not connected' : 'not set up');
    $('taStatus').textContent = state.connected ? 'Connected.'
      : (state.configured ? (state.error || 'Connecting…') : 'Enter the server\'s address to begin.');
    const list = $('taList');
    list.textContent = '';
    $('taEmpty').hidden = state.cameras.length > 0;
    state.cameras.forEach(function (c) {
      const row = document.createElement('div');
      row.className = 'row';
      row.style.cssText = 'gap:12px;padding:6px 0;border-top:1px solid var(--line, #8883)';
      const t = document.createElement('span');
      t.textContent = c.n + '. ' + c.name + (c.program ? ' · on program' : (c.preview ? ' · on preview' : ''));
      row.appendChild(t);
      list.appendChild(row);
    });
    $('taHost').disabled = $('taPort').disabled = $('taSave').disabled = !can('tally_arbiter.manage');
  }

  let last = '';
  async function refresh() {
    try {
      const state = await send('GET', '/api/admin/tally-arbiter');
      const sig = JSON.stringify(state);
      if (sig !== last) { last = sig; draw(state); }
    } catch (e) { /* the page keeps what it shows */ }
  }
  $('taHost').oninput = $('taPort').oninput = function () { editing = true; };
  $('taSave').onclick = async function () {
    showError('');
    try {
      editing = false;
      const state = await send('PUT', '/api/admin/tally-arbiter/settings',
        { host: $('taHost').value, port: +$('taPort').value || 4455 });
      last = JSON.stringify(state); draw(state);
    } catch (e) { showError(e.message); }
  };
  refresh();
  setInterval(function () { if (!document.hidden && !document.getElementById('page-tallyarbiter').hidden) refresh(); }, 2000);
})();
