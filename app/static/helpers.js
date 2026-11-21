/* The Helpers page (#497): the small programs on show computers that connect to Relay. Make a pairing code, see which
   helpers are connected and what each can do, rename one, remove one. Loaded after admin.js; uses what it shares
   (window.RelayAdmin) and the shared helpers (window.relayUI). Everything from the server is set as text. */
(function () {
  const A = window.RelayAdmin;
  const U = window.relayUI;
  const g = function (id) { return document.getElementById(id); };
  if (!A || !U || !g('page-helpers')) return;
  const el = U.el;
  const manage = g('page-helpers').getAttribute('data-manage') === 'yes';
  let timer = 0;

  const SYSTEMS = { macos: 'macOS', windows: 'Windows', linux: 'Linux' };

  function can(h) {
    if (!h.online) return 'Offline';
    return h.capabilities.length ? h.capabilities.map(function (c) { return c.id; }).join(', ') : 'Nothing yet';
  }

  function row(h) {
    const tr = el('tr', '');
    tr.appendChild(el('td', '', h.name));
    tr.appendChild(el('td', '', SYSTEMS[h.os] || h.os || ''));
    tr.appendChild(el('td', 'mono', h.version || ''));
    const st = el('td', '');
    st.appendChild(el('span', 'badge' + (h.online ? ' ok' : ''), h.online ? 'Connected' : 'Offline'));
    tr.appendChild(st);
    tr.appendChild(el('td', 'muted', can(h)));
    const act = el('td', '');
    if (manage) {
      const rename = el('button', 'sm', 'Rename');
      rename.type = 'button';
      rename.addEventListener('click', async function () {
        const name = window.prompt('Name for this computer', h.name);
        if (name === null || !name.trim()) return;
        try { await A.post('/api/admin/helpers/' + h.id, { name: name.trim() }); load(); }
        catch (e) { A.alertMsg(e.message); }
      });
      const remove = el('button', 'sm danger', 'Remove');
      remove.type = 'button';
      remove.addEventListener('click', async function () {
        if (!(await U.confirm('Remove ' + h.name + '? It will be disconnected and must be paired again to come back.'))) return;
        try { await A.api('/api/admin/helpers/' + h.id, { method: 'DELETE' }); load(); }
        catch (e) { A.alertMsg(e.message); }
      });
      act.appendChild(rename);
      act.appendChild(remove);
    }
    tr.appendChild(act);
    return tr;
  }

  async function load() {
    const tb = g('helperList');
    try {
      const body = await A.api('/api/admin/helpers');
      tb.textContent = '';
      body.helpers.forEach(function (h) { tb.appendChild(row(h)); });
      if (!body.helpers.length) {
        const tr = el('tr', '');
        const td = el('td', 'muted', 'No computer is paired yet.');
        td.colSpan = 6;
        tr.appendChild(td);
        tb.appendChild(tr);
      }
    } catch (e) {
      A.alertMsg(e.message);
    }
  }

  const codeBtn = g('helperCodeBtn');
  if (!manage) codeBtn.hidden = true;
  codeBtn.addEventListener('click', async function () {
    try {
      const made = await A.post('/api/admin/helpers/code', {});
      g('helperCode').textContent = made.code;
      g('helperCodeNote').textContent = 'Use it within ' + Math.round(made.expires_in / 60) + ' minutes. It works once.';
      g('helperCodeBox').hidden = false;
      const cmd = g('helperCmd');
      cmd.textContent = 'relay-helper pair ws://' + location.host + ' ' + made.code;
      cmd.hidden = false;
    } catch (e) {
      A.alertMsg(e.message);
    }
  });

  function start() {
    load();
    clearInterval(timer);
    timer = setInterval(function () { if (location.hash === '#helpers') load(); }, 5000);
  }
  window.addEventListener('hashchange', function () { if (location.hash === '#helpers') load(); });
  if (location.hash === '#helpers') start();
  else timer = setInterval(function () { if (location.hash === '#helpers') load(); }, 5000);
})();
