/* The Remote crew admin page (#277): who may connect from outside, the link to Cloudflare, recent remote connections.
   Loaded by admin.html only while the module is on, on every admin page, so each lookup is guarded. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-intercom-remote')) return;
  const $ = R.$, api = R.api, post = R.post;
  const can = relayUI.can;

  const STATE = { connected: 'ok', connecting: '', reconnecting: 'err', missing: 'err', off: '' };
  let timer = null;

  function showError(text) { $('irErr').textContent = text || ''; $('irErr').hidden = !text; }

  function cell(text) { const td = document.createElement('td'); td.textContent = text == null ? '' : String(text); return td; }

  function render(v) {
    const b = v.bridge || {};
    $('irSum').textContent = b.state === 'connected' ? 'live' : (b.state === 'off' ? '' : 'not connected');
    $('irState').textContent = b.detail || '';
    $('irState').className = 'note ' + (STATE[b.state] || '');
    const manage = can('intercom_remote.manage');
    const body = $('irPeople').tBodies[0];
    body.textContent = '';
    (v.people || []).forEach(function (p) {
      const tr = document.createElement('tr');
      tr.appendChild(cell(p.name || p.username));
      tr.appendChild(cell(p.role));
      tr.appendChild(cell(p.phones));
      const td = document.createElement('td');
      if (manage) {
        const allow = document.createElement('button');
        allow.type = 'button';
        allow.textContent = p.allowed ? 'Remove remote access' : 'Allow remote';
        allow.addEventListener('click', async function () {
          showError('');
          try { await post('/api/admin/intercom-remote/users/' + p.id + '/allow', { allowed: !p.allowed }); refresh(); }
          catch (e) { showError(e.message); }
        });
        td.appendChild(allow);
        if (p.phones) {
          const cut = document.createElement('button');
          cut.type = 'button';
          cut.textContent = 'Sign out remote phones';
          cut.addEventListener('click', async function () {
            showError('');
            try { await post('/api/admin/intercom-remote/users/' + p.id + '/cut', {}); refresh(); }
            catch (e) { showError(e.message); }
          });
          td.appendChild(cut);
        }
      }
      tr.appendChild(td);
      body.appendChild(tr);
    });
    const sb = $('irSessions').tBodies[0];
    sb.textContent = '';
    (v.sessions || []).forEach(function (s) {
      const tr = document.createElement('tr');
      [s.user_name, s.started_at, s.ended_at || 'now', s.end_reason || ''].forEach(function (t) { tr.appendChild(cell(t)); });
      sb.appendChild(tr);
    });
  }

  async function refresh() {
    try { render(await api('/api/admin/intercom-remote')); } catch (e) { /* the admin page reports lost sessions */ }
  }

  function visible() { return !document.getElementById('page-intercom-remote').closest('.page').hidden; }
  function tick() { if (visible()) refresh(); timer = setTimeout(tick, 5000); }

  document.addEventListener('relay:config', function () { refresh(); if (!timer) tick(); });
})();
