/* The Crew app admin page (#226). Loaded by admin.html only while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-crew')) return;
  const $ = R.$, api = R.api;
  function can(perm) { return typeof window.relayCan !== 'function' || !!window.relayCan(perm); }
  function fill(cfg) {
    $('crName').value = cfg.name || '';
    $('crPlan').checked = !!cfg.show_plan;
    const manage = can('crew.manage');
    ['crName', 'crPlan', 'crSave'].forEach(function (id) { $(id).disabled = !manage; });
    $('crLink').textContent = location.origin + '/crew';
  }
  async function refresh() { try { fill(await api('/api/admin/crew')); } catch (e) { /* keeps what it had */ } }
  $('crSave').addEventListener('click', async function () {
    $('crErr').hidden = true; $('crMsg').textContent = '';
    const res = await fetch('/api/admin/crew', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: $('crName').value, show_plan: $('crPlan').checked }) });
    const data = await res.json().catch(function () { return {}; });
    if (!res.ok) { $('crErr').textContent = data.detail || data.error || 'That did not work.'; $('crErr').hidden = false; return; }
    fill(data); $('crMsg').textContent = 'Saved';
  });
  // -- people: roles and Planning Center names (#227). Every value is put on the page as text.
  function cell(text, cls) { const td = document.createElement('td'); if (cls) td.className = cls; td.textContent = text || ''; return td; }
  function msg(text) { $('crPeopleErr').textContent = text || ''; $('crPeopleErr').hidden = !text; }
  async function send(method, path, body) {
    const res = await fetch(path, { method: method, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    const data = await res.json().catch(function () { return {}; });
    if (!res.ok) { msg(data.detail || data.error || 'That did not work.'); return null; }
    msg(''); return data;
  }
  function button(label, fn) { const b = document.createElement('button'); b.type = 'button'; b.textContent = label; b.addEventListener('click', fn); return b; }
  function drawPeople(data) {
    const manage = can('crew.manage'), body = document.querySelector('#crPeople tbody');
    $('crScan').hidden = !manage || !data.planning_center;
    body.textContent = '';
    data.people.forEach(function (p) {
      const tr = document.createElement('tr');
      tr.appendChild(cell(p.name + (p.disabled ? ' (off)' : '')));
      const rt = document.createElement('td'), inp = document.createElement('input');
      inp.maxLength = 40; inp.value = p.role; inp.disabled = !manage; inp.setAttribute('aria-label', 'Role for ' + p.name);
      inp.addEventListener('change', async function () { if (await send('PUT', '/api/admin/crew/people/' + p.id + '/role', { role: inp.value })) peopleRefresh(); });
      rt.appendChild(inp); tr.appendChild(rt);
      const pc = document.createElement('td');
      if (p.suggested_name) {
        pc.appendChild(document.createTextNode('Planning Center spells it \u201c' + p.suggested_name + '\u201d '));
        if (manage) {
          pc.appendChild(button('Use it', async function () { if (await send('POST', '/api/admin/crew/people/' + p.id + '/name', { action: 'accept' })) peopleRefresh(); }));
          pc.appendChild(button('Keep mine', async function () { if (await send('POST', '/api/admin/crew/people/' + p.id + '/name', { action: 'dismiss' })) peopleRefresh(); }));
        }
      } else if (p.linked) {
        pc.appendChild(document.createTextNode('Matched' + (p.pc_name ? ' to ' + p.pc_name : '') + ' '));
        if (manage) pc.appendChild(button('Wrong person', async function () { if (await send('DELETE', '/api/admin/crew/people/' + p.id + '/match')) peopleRefresh(); }));
      } else { pc.textContent = '\u2014'; }
      tr.appendChild(pc); body.appendChild(tr);
    });
  }
  async function peopleRefresh() { try { drawPeople(await api('/api/admin/crew/people')); } catch (e) { /* keeps what it had */ } }
  $('crScan').addEventListener('click', async function () { const d = await send('POST', '/api/admin/crew/people/scan'); if (d) drawPeople(d); });
  // -- pages (#228): who is chosen is kept in two sets; everything shown is text.
  const picked = { users: new Set(), teams: new Set() };
  let pageTimer = null;
  function pmsg(text, bad) { $('crPageErr').textContent = bad ? text : ''; $('crPageErr').hidden = !bad; $('crPageMsg').textContent = bad ? '' : (text || ''); }
  function chip(set, key, label, title) {
    const l = document.createElement('label'); l.className = 'chk chk-inline'; l.style.marginRight = '12px';
    const c = document.createElement('input'); c.type = 'checkbox'; c.checked = set.has(key);
    c.addEventListener('change', function () { if (c.checked) set.add(key); else set.delete(key); });
    l.appendChild(c); l.appendChild(document.createTextNode(' ' + label)); if (title) l.title = title; return l;
  }
  function drawTargets(t) {
    const teams = $('crPageTeams'), ppl = $('crPagePeople');
    teams.textContent = ''; ppl.textContent = '';
    if (!t.teams.length) teams.textContent = 'None (Planning Center is off or nobody is scheduled).';
    t.teams.forEach(function (x) { teams.appendChild(chip(picked.teams, x.name, x.name + ' (' + x.reachable + '/' + x.people + ')', x.reachable + ' of ' + x.people + ' have a crew account')); });
    t.people.forEach(function (x) { ppl.appendChild(chip(picked.users, x.id, x.name + (x.role ? ' \u00b7 ' + x.role : '') + (x.phone ? '' : ' (no phone)'))); });
  }
  const LABEL = { confirmed: '\u2713', waiting: '\u2026 waiting', no_phone: 'no phone' };
  function drawPages(pages) {
    const body = document.querySelector('#crPages tbody'); body.textContent = '';
    let live = false;
    pages.forEach(function (p) {
      const tr = document.createElement('tr');
      tr.appendChild(cell(p.message + (p.sender ? ' \u2014 ' + p.sender : '') + (p.buzzing ? ' (buzzing)' : '')));
      tr.appendChild(cell(p.people.map(function (w) { return w.name + ' ' + LABEL[w.state]; }).join(', ')));
      body.appendChild(tr); live = live || p.buzzing;
    });
    clearTimeout(pageTimer);
    if (live) pageTimer = setTimeout(pagesRefresh, 3000);   // watch the receipts come in while it is buzzing
  }
  async function pagesRefresh() {
    if (!can('crew.page')) return;
    $('crPageBox').hidden = false;
    try { const d = await api('/api/admin/crew/pages'); drawPages(d.pages); drawTargetsOnce(d.targets); } catch (e) { /* keeps what it had */ }
  }
  let drawn = false;
  function drawTargetsOnce(t) { if (!drawn) { drawn = true; drawTargets(t); } }
  $('crPageSend').addEventListener('click', async function () {
    pmsg('');
    const res = await fetch('/api/admin/crew/pages', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: $('crPageText').value, users: Array.from(picked.users), teams: Array.from(picked.teams) }) });
    const data = await res.json().catch(function () { return {}; });
    if (!res.ok) { pmsg(data.detail || data.error || 'That did not work.', true); return; }
    $('crPageText').value = '';
    pmsg('Sent to ' + data.people + (data.people === 1 ? ' person' : ' people') +
      (data.unmatched.length ? '. No crew account for: ' + data.unmatched.join(', ') : ''));
    pagesRefresh();
  });
  // -- call times (#229): scheduled pages for a Planning Center team. Everything shown is text.
  const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
  function ctMsg(text) { $('ctErr').textContent = text || ''; $('ctErr').hidden = !text; }
  function ctSend(method, path, body) { return send2(method, path, body, ctMsg); }
  async function send2(method, path, body, say) {
    const res = await fetch(path, { method: method, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    const data = await res.json().catch(function () { return {}; });
    if (!res.ok) { say(data.detail || data.error || 'That did not work.'); return null; }
    say(''); return data;
  }
  function whenText(r) { return r.kind === 'before' ? r.minutes + ' min before each service' : DAYS[r.weekday] + ' ' + r.clock; }
  function lastText(r) {
    if (!r.last_ts) return 'Not yet';
    return new Date(r.last_ts * 1000).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' }) +
      (r.last_people ? ' \u00b7 ' + r.last_people + (r.last_people === 1 ? ' person' : ' people') : ' \u00b7 nobody to page');
  }
  function drawCalls(d) {
    const manage = can('crew.manage'), body = document.querySelector('#ctRules tbody');
    ['ctTeam', 'ctKind', 'ctMin', 'ctDay', 'ctClock', 'ctMsg', 'ctAdd'].forEach(function (id) { $(id).disabled = !manage; });
    const list = $('ctTeams'); list.textContent = '';
    d.teams.forEach(function (t) { const o = document.createElement('option'); o.value = t; list.appendChild(o); });
    body.textContent = '';
    d.rules.forEach(function (r) {
      const tr = document.createElement('tr');
      tr.appendChild(cell(r.team + (r.enabled ? '' : ' (off)'))); tr.appendChild(cell(whenText(r)));
      tr.appendChild(cell(r.message)); tr.appendChild(cell(lastText(r)));
      const act = document.createElement('td');
      if (manage) {
        act.appendChild(button(r.enabled ? 'Turn off' : 'Turn on', async function () {
          const next = await ctSend('PUT', '/api/admin/crew/calltimes/' + r.id, Object.assign({}, r, { enabled: !r.enabled }));
          if (next) drawCalls(next);
        }));
        act.appendChild(button('Remove', async function () { const next = await ctSend('DELETE', '/api/admin/crew/calltimes/' + r.id); if (next) drawCalls(next); }));
      }
      tr.appendChild(act); body.appendChild(tr);
    });
  }
  function ctKind() { const b = $('ctKind').value === 'before'; $('ctBefore').hidden = !b; $('ctFixed').hidden = b; }
  $('ctKind').addEventListener('change', ctKind);
  $('ctAdd').addEventListener('click', async function () {
    const before = $('ctKind').value === 'before';
    const rule = { team: $('ctTeam').value, kind: $('ctKind').value, message: $('ctMsg').value, enabled: true };
    if (before) rule.minutes = parseInt($('ctMin').value, 10); else { rule.weekday = parseInt($('ctDay').value, 10); rule.clock = $('ctClock').value; }
    const d = await ctSend('POST', '/api/admin/crew/calltimes', rule);
    if (d) { $('ctMsg').value = ''; drawCalls(d); }
  });
  async function callsRefresh() { try { drawCalls(await api('/api/admin/crew/calltimes')); } catch (e) { /* keeps what it had */ } }
  document.addEventListener('relay:config', function () { refresh(); peopleRefresh(); drawn = false; pagesRefresh(); callsRefresh(); });
  refresh(); peopleRefresh(); pagesRefresh(); callsRefresh();
})();
