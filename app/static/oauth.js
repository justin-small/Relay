/* The Connect card a module puts on its admin page for an account it signs in to (#304, app/oauth.py):
   who is connected, a Connect button that shows the code to enter at the service's address, and Disconnect.

     RelayOAuth.mount(container, '/api/admin/<module>/account', { manage: '<module>.manage', onChange: fn })

   The module's page names its own route; this script knows no service. `scopeNames` maps a scope to the words
   shown for it. Built from the admin page's own pieces
   (.pcard, .phead, dl, .pactions, .spill, .note) and everything from the server is set as text. While a code is
   waiting the card asks again every three seconds; otherwise it reads once and when told to (`refresh()`). */
(function () {
  const R = window.RelayAdmin;
  if (!R) return;
  const el = R.el;

  const WORD = { connected: 'Connected', waiting: 'Waiting for you', disconnected: 'Not connected', sign_in: 'Sign in again' };
  const PILL = { connected: 'on', waiting: '', disconnected: 'idle', sign_in: 'warn' };

  function when(sec) {
    if (!sec) return '';
    try { return new Date(sec * 1000).toLocaleString(); } catch (e) { return ''; }
  }

  function mount(container, base, opts) {
    opts = opts || {};
    const canManage = !opts.manage || window.relayCan(opts.manage);
    const card = el('div', 'pcard oauthcard');
    container.replaceChildren(card);
    let timer = null;
    let last = '';
    let busy = false;

    function schedule(s) {
      clearTimeout(timer);
      timer = s && s.state === 'waiting' ? setTimeout(refresh, 3000) : null;
    }

    function render(s, error) {
      // Rebuild only when what the card shows changes, so a click is never lost to a redraw.
      const key = JSON.stringify([s, error || '']);
      if (key === last) return;
      last = key;
      card.replaceChildren();
      const head = el('div', 'phead');
      head.appendChild(el('span', 'pname', s.service || 'Account'));
      const pill = el('span', 'spill ' + (PILL[s.state] || ''));
      pill.appendChild(el('span', 'dot'));
      pill.appendChild(document.createTextNode(WORD[s.state] || s.state));
      head.appendChild(pill);
      card.appendChild(head);

      if (s.state === 'waiting') {
        const p = el('p', 'oauthsteps');
        p.appendChild(document.createTextNode('On any phone or computer, go to '));
        const a = el('a', '', s.url || '');
        a.href = /^https:\/\//.test(s.url || '') ? s.url : '#';
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        p.appendChild(a);
        p.appendChild(document.createTextNode(' and enter this code:'));
        card.appendChild(p);
        card.appendChild(el('div', 'oauthcode', s.code || ''));
        if (s.expires_at) {
          const mins = Math.max(0, Math.round((s.expires_at * 1000 - Date.now()) / 60000));
          card.appendChild(el('div', 'note', 'The code works for about ' + mins + ' more minute' + (mins === 1 ? '' : 's') + '. This card updates by itself once you have signed in.'));
        }
      } else if (s.state === 'connected' || s.state === 'sign_in') {
        const dl = el('dl');
        function row(k, v) { if (!v) return; dl.appendChild(el('dt', '', k)); dl.appendChild(el('dd', '', v)); }
        row('Account', s.account);
        row('Connected', when(s.connected_at) + (s.connected_by ? ' by ' + s.connected_by : ''));
        row('Access', (s.scopes || []).map(function (x) { return (opts.scopeNames || {})[x] || x; }).join(', '));
        card.appendChild(dl);
        if (s.state === 'sign_in') {
          card.appendChild(el('div', 'note err', s.service + ' stopped accepting Relay. Press Connect to sign in again.'));
        }
      } else if (opts.intro) {
        card.appendChild(el('div', 'note', opts.intro));
      }
      if (s.message) card.appendChild(el('div', 'note', s.message));
      if (error) card.appendChild(el('div', 'note err', error));

      if (!canManage) return;
      const acts = el('div', 'pactions');
      function button(label, path, cls) {
        const b = el('button', cls || '', label);
        b.type = 'button';
        b.addEventListener('click', function () { act(path, b); });
        acts.appendChild(b);
      }
      if (s.state === 'waiting') button('Cancel', '/cancel');
      else button(s.state === 'disconnected' ? 'Connect' : 'Connect again', '/connect', s.state === 'connected' ? '' : 'primary');
      if (s.state === 'connected' || s.state === 'sign_in') button('Disconnect', '/disconnect');
      card.appendChild(acts);
    }

    async function act(path, btn) {
      if (busy) return;
      if (path === '/disconnect' && !window.confirm('Disconnect this account? Relay forgets it and asks the service to remove its access.')) return;
      busy = true;
      btn.disabled = true;
      try {
        const s = await R.post(base + path, {});
        render(s, s.ok === false ? s.error : '');
        schedule(s);
        if (opts.onChange) opts.onChange(s);
      } catch (e) {
        btn.disabled = false;
        render({ state: 'disconnected', service: '' }, e.message);
      } finally {
        busy = false;
      }
    }

    async function refresh() {
      try {
        const s = await R.api(base);
        const was = last;
        render(s, '');
        schedule(s);
        if (opts.onChange && was !== last) opts.onChange(s);
      } catch (e) {
        render({ state: 'disconnected', service: '' }, e.message);
      }
    }

    refresh();
    return { refresh: refresh, stop: function () { clearTimeout(timer); } };
  }

  (function () {
    const s = document.createElement('style');
    s.textContent = '.oauthcard .oauthcode{font:600 28px/1.2 ui-monospace,Menlo,Consolas,monospace;letter-spacing:.12em;' +
      'margin:8px 0;user-select:all}.oauthcard .oauthsteps{margin:10px 0 0}.oauthcard a{color:var(--fg)}' +
      '.oauthcard .phead .spill{padding:3px 9px;font-size:11px}.oauthcard .spill .dot{border-radius:50%;display:inline-block}.oauthcard .spill:not(.on) .dot{background:var(--dim)}' +
      '.oauthcard .spill.idle{color:var(--dim)}.oauthcard .spill.warn{color:#fcd34d;border-color:#5b4a14}.oauthcard .spill.warn .dot{background:#fcd34d}';
    document.head.appendChild(s);
  })();

  window.RelayOAuth = { mount: mount };
})();
