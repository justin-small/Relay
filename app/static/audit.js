/* The Audit log page (#172): sign-ins, failed sign-ins and every change, newest first, filtered by person,
   kind, time and text, a page at a time. Read-only: nothing here changes the log. Loaded after admin.js and
   uses what it shares (window.RelayAdmin). Everything from the server is set as text. */
(function () {
  const A = window.RelayAdmin;
  const g = function (id) { return document.getElementById(id); };
  if (!A || !g('page-audit')) return;
  const el = A.el;

  // The name of each kind of entry; a module's own kind is its id, shown as words.
  const KINDS = {
    auth: 'Sign-ins', setup: 'First-run setup', account: 'Own account', user: 'Users',
    device: 'Trusted devices', token: 'API tokens', settings: 'Settings', session: 'Captions',
    alerts: 'Alerts', panels: 'Panels', schedules: 'Schedules', overlays: 'Overlays',
    recordings: 'Recordings', module: 'Modules', api: 'Other changes'
  };
  const FAILED = { 'auth.failed': 1, 'auth.locked': 1, 'setup.failed': 1 };

  function kindName(k) {
    if (KINDS[k]) return KINDS[k];
    const w = k.replace(/_/g, ' ');
    return w.charAt(0).toUpperCase() + w.slice(1);
  }

  function when(iso) {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso || '';
    return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'medium' });
  }

  let oldest = 0, loading = false, timer = 0, filled = false;

  function query(before) {
    const p = new URLSearchParams();
    if (g('auditWho').value) p.set('who', g('auditWho').value);
    if (g('auditKind').value) p.set('kind', g('auditKind').value);
    if (g('auditDays').value) p.set('days', g('auditDays').value);
    const q = g('auditQ').value.trim();
    if (q) p.set('q', q);
    if (before) p.set('before', String(before));
    return '/api/admin/audit?' + p.toString();
  }

  function fillSelect(sel, items, first) {
    const keep = sel.value;
    sel.textContent = '';
    sel.appendChild(new Option(first, ''));
    items.forEach(function (it) { sel.appendChild(new Option(it[1], it[0])); });
    sel.value = Array.prototype.some.call(sel.options, function (o) { return o.value === keep; }) ? keep : '';
  }

  function fillFilters(body) {
    const people = body.people.map(function (p) { return [String(p.id), p.name + ' (' + p.username + ')']; });
    people.push(['system', 'Relay itself, or nobody signed in']);
    fillSelect(g('auditWho'), people, 'Everyone');
    fillSelect(g('auditKind'), body.kinds.map(function (k) { return [k, kindName(k)]; }), 'Everything');
    g('auditKeep').textContent = String(body.keep_days);
  }

  function cell(cls, main, extra) {
    const td = el('td', cls, main);
    extra.filter(Boolean).forEach(function (t) { td.appendChild(el('small', '', t)); });
    return td;
  }

  function row(e) {
    const tr = el('tr', FAILED[e.action] ? 'failed' : '');
    tr.appendChild(cell('when', when(e.at), []));
    let who, whoExtra = [];
    if (e.actor_name) {
      who = e.actor_name;
      whoExtra.push(e.actor_username);
      if (e.token_name) whoExtra.push('API token: ' + e.token_name);
    } else {
      who = e.action.indexOf('auth.') === 0 || e.action.indexOf('setup.') === 0 ? 'Nobody signed in' : 'Relay';
    }
    tr.appendChild(cell('who', who, whoExtra));
    const d = e.detail || {};
    tr.appendChild(cell('what', e.summary, [
      e.target, d.device ? 'Device: ' + d.device : '', d.with ? 'With ' + d.with : ''
    ]));
    tr.appendChild(cell('mono', e.ip || '', []));
    return tr;
  }

  async function load(more) {
    if (loading) return;
    loading = true;
    const tb = g('auditList');
    try {
      const body = await A.api(query(more ? oldest : 0));
      if (!more) {
        tb.textContent = '';
        fillFilters(body);
      }
      body.entries.forEach(function (e) { tb.appendChild(row(e)); });
      if (!tb.children.length) {
        const tr = el('tr');
        const td = el('td', 'muted', 'Nothing matches.');
        td.colSpan = 4;
        tr.appendChild(td);
        tb.appendChild(tr);
      }
      if (body.entries.length) oldest = body.entries[body.entries.length - 1].id;
      g('auditMoreRow').hidden = !body.more;
      filled = true;
    } catch (e) {
      A.alertMsg(e.message);
    } finally {
      loading = false;
    }
  }

  ['auditWho', 'auditKind', 'auditDays'].forEach(function (id) {
    g(id).addEventListener('change', function () { load(false); });
  });
  g('auditQ').addEventListener('input', function () {
    clearTimeout(timer);
    timer = setTimeout(function () { load(false); }, 300);
  });
  g('auditMore').addEventListener('click', function () { load(true); });
  window.addEventListener('hashchange', function () { if (location.hash === '#audit') load(false); });
  if (location.hash === '#audit' && !filled) load(false);
})();
