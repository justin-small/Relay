/* The Crew app admin page (#226). Loaded by admin.html only while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-crew')) return;
  const $ = R.$, api = R.api;
  const can = relayUI.can;
  // -- tabs: Live board first, then Call times, Checklists, People and Setup. The sidebar can open one by name.
  function showTab(name) {
    const tabs = $('crTabs').querySelectorAll('[data-crtab]');
    if (!Array.prototype.some.call(tabs, function (b) { return b.dataset.crtab === name; })) name = 'live';
    tabs.forEach(function (b) { b.setAttribute('aria-selected', String(b.dataset.crtab === name)); });
    document.querySelectorAll('#page-crew [data-crpane]').forEach(function (p) { p.hidden = p.dataset.crpane !== name; });
  }
  $('crTabs').addEventListener('click', function (e) { const b = e.target.closest('[data-crtab]'); if (b) showTab(b.dataset.crtab); });
  $('page-crew').addEventListener('relay:tab', function (e) { showTab(e.detail || 'live'); });
  function fill(cfg) {
    $('crName').value = cfg.name || '';
    $('crPlan').checked = !!cfg.show_plan;
    const fence = cfg.geofence || {};
    $('crLat').value = fence.lat === undefined ? '' : fence.lat; $('crLon').value = fence.lon === undefined ? '' : fence.lon;
    $('crRad').value = fence.radius_m === undefined ? '' : fence.radius_m;
    const manage = can('crew.manage');
    ['crName', 'crPlan', 'crSave', 'crLat', 'crLon', 'crRad', 'crHosts'].forEach(function (id) { $(id).disabled = !manage; });
    $('crHosts').value = (cfg.embed_hosts || []).join('\n');
    $('crLink').textContent = location.origin + '/crew';
  }
  // Empty fields mean no geofence; anything typed is sent as numbers and checked by the server.
  function geofenceValue() {
    const lat = $('crLat').value.trim(), lon = $('crLon').value.trim(), rad = $('crRad').value.trim();
    if (!lat && !lon && !rad) return null;
    return { lat: lat === '' ? null : Number(lat), lon: lon === '' ? null : Number(lon), radius_m: rad === '' ? 150 : Number(rad) };
  }
  // The QR code (#236) is drawn here with the vendored generator; the addresses come from the server.
  async function join() {
    try {
      const j = await api('/api/admin/crew/join');
      $('crJoinUrl').textContent = j.join_url || '(no network address yet)';
      $('crCrewUrl').textContent = j.crew_url || '';
      const box = $('crQr'); box.textContent = '';
      if (j.join_url && typeof qrcode === 'function') {
        const q = qrcode(0, 'M'); q.addData(j.join_url); q.make();
        box.innerHTML = q.createSvgTag({ cellSize: 4, margin: 3, scalable: true });
      }
    } catch (e) { /* the addresses stay as they were */ }
  }
  async function refresh() { join(); try { fill(await api('/api/admin/crew')); } catch (e) { /* keeps what it had */ } }
  $('crSave').addEventListener('click', async function () {
    $('crErr').hidden = true; $('crMsg').textContent = '';
    const res = await fetch('/api/admin/crew', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: $('crName').value, show_plan: $('crPlan').checked, geofence: geofenceValue(),
        embed_hosts: $('crHosts').value.split('\n').map(function (h) { return h.trim(); }).filter(Boolean) }) });
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
  const button = relayUI.button;
  function drawPeople(data) {
    const manage = can('crew.manage'), body = document.querySelector('#crPeople tbody');
    $('crScan').hidden = !manage || !data.planning_center;
    body.textContent = '';
    data.people.forEach(function (p) {
      const tr = document.createElement('tr');
      tr.appendChild(cell(p.name + (p.disabled ? ' (off)' : '')));
      const rt = document.createElement('td'), inp = document.createElement('input');
      inp.maxLength = 40; inp.value = p.crew_role_set; inp.placeholder = p.usual_position; inp.disabled = !manage; inp.setAttribute('aria-label', 'Crew role for ' + p.name);
      inp.addEventListener('change', async function () { if (await send('PUT', '/api/admin/crew/people/' + p.id + '/role', { crew_role: inp.value })) peopleRefresh(); });
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
    const b = button(label, function () {
      if (set.has(key)) set.delete(key); else set.add(key);
      b.setAttribute('aria-pressed', String(set.has(key))); sendLabel();
    });
    b.classList.add('sm'); b.setAttribute('aria-pressed', String(set.has(key))); if (title) b.title = title; return b;
  }
  function sendLabel() {
    const n = picked.teams.size + picked.users.size;
    $('crPageSend').textContent = n ? 'Send page' + (picked.teams.size ? ' to ' + Array.from(picked.teams).join(', ') : '') +
      (picked.users.size ? (picked.teams.size ? ' and ' : ' to ') + picked.users.size + (picked.users.size === 1 ? ' person' : ' people') : '') : 'Send page';
  }
  function drawTargets(t) {
    const teams = $('crPageTeams'), ppl = $('crPagePeople');
    teams.textContent = ''; ppl.textContent = '';
    if (!t.teams.length) teams.textContent = 'None (Planning Center is off or nobody is scheduled).';
    t.teams.forEach(function (x) { teams.appendChild(chip(picked.teams, x.name, x.name + ' (' + x.reachable + '/' + x.people + ')', x.reachable + ' of ' + x.people + ' have a crew account')); });
    t.people.forEach(function (x) { ppl.appendChild(chip(picked.users, x.id, x.name + (x.role ? ' \u00b7 ' + x.role : '') + (x.phone ? '' : ' (no phone)'))); });
    sendLabel();
  }
  const LABEL = { confirmed: '\u2713', waiting: '\u2026 waiting', no_phone: 'no phone' };
  function clock(ts) { return ts ? new Date(ts * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : ''; }
  function drawPages(pages) {
    const box = $('crPages'); box.textContent = '';
    let live = false;
    pages.forEach(function (p) {
      const read = p.people.filter(function (w) { return w.state === 'confirmed'; }).length;
      const row = relayUI.el('div', '', relayUI.el('span', '', clock(p.sent_ts)),
        relayUI.el('span', '', p.message + (p.sender ? ' \u2014 ' + p.sender : '') + (p.buzzing ? ' (buzzing)' : '')),
        relayUI.el('span', 'badge ' + (read === p.people.length ? 'ok' : 'warn'), read + '/' + p.people.length));
      row.title = p.people.map(function (w) { return w.name + ' ' + LABEL[w.state]; }).join(', ');
      box.appendChild(row); live = live || p.buzzing;
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
    $('crPageText').value = ''; picked.users.clear(); picked.teams.clear(); drawn = false; sendLabel();
    pmsg('Sent to ' + data.people + (data.people === 1 ? ' person' : ' people') +
      (data.unmatched.length ? '. No crew account for: ' + data.unmatched.join(', ') : ''));
    pagesRefresh();
  });
  // -- call times (#229): scheduled pages for a Planning Center team. Everything shown is text.
  const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];   // Sunday = 0 (app/timewin.py)
  function ctMsg(text) { $('ctErr').textContent = text || ''; $('ctErr').hidden = !text; }
  function ctSend(method, path, body) { return send2(method, path, body, ctMsg); }
  async function send2(method, path, body, say) {
    const res = await fetch(path, { method: method, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    const data = await res.json().catch(function () { return {}; });
    if (!res.ok) { say(data.detail || data.error || 'That did not work.'); return null; }
    say(''); return data;
  }
  function whenText(r) { return r.kind === 'before' ? r.minutes + ' min before ' + (r.schedule_ref ? 'the start of \u201c' + r.schedule_name + '\u201d' : 'each service') : DAYS[r.weekday] + ' ' + r.clock; }
  function lastText(r) {
    if (!r.last_ts) return 'Not yet';
    return new Date(r.last_ts * 1000).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' }) +
      (r.last_people ? ' \u00b7 ' + r.last_people + (r.last_people === 1 ? ' person' : ' people') : ' \u00b7 nobody to page');
  }
  function drawCalls(d) {
    const manage = can('crew.manage'), body = document.querySelector('#ctRules tbody');
    ['ctTeam', 'ctKind', 'ctMin', 'ctSched', 'ctDay', 'ctClock', 'ctMsg', 'ctAdd'].forEach(function (id) { $(id).disabled = !manage; });
    const pick = $('ctTeam'), was = pick.value; pick.textContent = '';
    d.teams.forEach(function (t) { if (t.missing) return; const o = document.createElement('option'); o.value = t.ref; o.textContent = t.name; pick.appendChild(o); });
    if (was) pick.value = was;
    if (!pick.options.length) { const o = document.createElement('option'); o.value = ''; o.textContent = 'No teams yet: Planning Center is off or has not been read'; pick.appendChild(o); }
    const sp = $('ctSched'), sw = sp.value; sp.length = 1;
    (d.schedules || []).forEach(function (x) { const o = document.createElement('option'); o.value = x.ref; o.textContent = 'The start of \u201c' + x.name + '\u201d'; sp.appendChild(o); });
    sp.value = sw;
    body.textContent = '';
    d.rules.forEach(function (r) {
      const tr = document.createElement('tr');
      tr.appendChild(cell(r.team + (r.enabled ? '' : ' (off)') + (r.missing ? ' (no longer in Planning Center)' : '') + (r.unmatched ? ' (not matched to a Planning Center team: remove it and add it again from the list)' : ''))); tr.appendChild(cell(whenText(r)));
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
  function ctKind() { const b = $('ctKind').value === 'before'; $('ctBefore').hidden = !b; $('ctSchedBox').hidden = !b; $('ctFixed').hidden = b; }
  $('ctKind').addEventListener('change', ctKind);
  $('ctAdd').addEventListener('click', async function () {
    const before = $('ctKind').value === 'before';
    const rule = { team_ref: $('ctTeam').value, kind: $('ctKind').value, message: $('ctMsg').value, enabled: true };
    if (before) { rule.minutes = parseInt($('ctMin').value, 10); rule.schedule_ref = $('ctSched').value; } else { rule.weekday = parseInt($('ctDay').value, 10); rule.clock = $('ctClock').value; }
    const d = await ctSend('POST', '/api/admin/crew/calltimes', rule);
    if (d) { $('ctMsg').value = ''; drawCalls(d); }
  });
  async function callsRefresh() { try { drawCalls(await api('/api/admin/crew/calltimes')); } catch (e) { /* keeps what it had */ } }
  // -- arrivals (#231): the board is refreshed while the page is open.
  const STATE = { arrived: 'Arrived', late_arrival: 'Arrived late', waiting: 'Not yet', overdue: 'Overdue', no_account: 'No crew account' };
  // The live board: one column per Planning Center team (or per call time), a line per person with how far their
  // checklist is, and a Page button beside anyone overdue.
  let board = null, group = 'team', progress = {};
  const METHOD = { geofence: ' \u00b7 location', wifi: ' \u00b7 Wi-Fi', tap: ' \u00b7 tapped' };
  function initials(name) { return (name || '?').split(/\s+/).map(function (w) { return w.charAt(0); }).join('').slice(0, 2).toUpperCase(); }
  function since(ts) { return Math.max(1, Math.round((Date.now() / 1000 - ts) / 60)); }
  function personLine(p) {
    const late = p.state === 'overdue', el = relayUI.el;
    let sub = p.teams.join(', ');
    if (p.arrived_ts) sub = 'in ' + clock(p.arrived_ts) + (METHOD[p.method] || '') + (p.state === 'late_arrival' ? ' \u00b7 late' : '');
    else if (late) sub = 'late ' + since(p.call_ts) + ' min';
    else if (p.state === 'no_account') sub = 'no crew account';
    else if (p.call_ts) sub = 'due ' + clock(p.call_ts);
    const row = el('div', 'crp' + (late ? ' late' : '') + (p.arrived_ts || late ? '' : ' out'),
      el('span', 'crav', initials(p.name)), el('span', 'who', el('b', '', p.name), el('small', '', sub)));
    const pr = progress[p.name];
    if (late && can('crew.page') && p.user_id) {
      row.appendChild(button('Page', function () { picked.users.clear(); picked.teams.clear(); picked.users.add(p.user_id); drawn = false; pagesRefresh().then(function () { $('crPageText').focus(); }); }));
      row.lastChild.classList.add('sm');
    } else if (pr && pr.total) {
      const bar = el('span', 'crprog'), fill = el('i'); fill.style.width = Math.round(100 * pr.done / pr.total) + '%';
      bar.title = pr.done + ' of ' + pr.total + ' checklist items'; bar.appendChild(fill); row.appendChild(bar);
    }
    return row;
  }
  function drawBoard(d) {
    if (d) board = d;
    d = board; if (!d) return;
    const box = $('crBoard'); box.textContent = '';
    const late = d.people.filter(function (p) { return p.state === 'overdue'; }).length;
    const waiting = d.people.filter(function (p) { return p.state === 'waiting'; }).length;
    $('crBoardIn').textContent = d.arrived + ' of ' + d.total + ' in';
    $('crBoardLate').hidden = !late; $('crBoardLate').textContent = late + ' late';
    $('crBoardWait').hidden = !waiting; $('crBoardWait').textContent = waiting + ' not yet due';
    $('crBoardCount').textContent = d.people.length ? '' : 'Nobody is on today\u2019s plan, or Planning Center is off.';
    const groups = new Map();
    d.people.forEach(function (p) {
      const keys = group === 'team' ? (p.teams.length ? p.teams : ['No team']) : [p.call_ts ? 'Call ' + clock(p.call_ts) : 'No call time'];
      keys.forEach(function (k) { if (!groups.has(k)) groups.set(k, []); groups.get(k).push(p); });
    });
    groups.forEach(function (people, name) {
      const inn = people.filter(function (p) { return p.arrived_ts; }).length;
      const call = people.map(function (p) { return p.call_ts; }).filter(Boolean).sort()[0];
      const col = relayUI.el('section', 'crteam', relayUI.el('header', '', relayUI.el('b', '', name),
        relayUI.el('span', '', (group === 'team' && call ? 'call ' + clock(call) + ' \u00b7 ' : '') + inn + ' of ' + people.length + ' in')));
      people.forEach(function (p) { col.appendChild(personLine(p)); });
      if (group === 'team' && can('crew.page') && name !== 'No team') {
        const b = button('Page ' + name, function () { picked.users.clear(); picked.teams.clear(); picked.teams.add(name); drawn = false; pagesRefresh().then(function () { $('crPageText').focus(); }); });
        b.classList.add('sm', 'quiet', 'crfoot'); col.appendChild(b);
      }
      box.appendChild(col);
    });
  }
  document.querySelectorAll('[data-crgroup]').forEach(function (b) {
    b.addEventListener('click', function () {
      group = b.dataset.crgroup;
      document.querySelectorAll('[data-crgroup]').forEach(function (x) { x.setAttribute('aria-pressed', String(x === b)); });
      drawBoard();
    });
  });
  // -- activity beside the board: the last few things people did on the crew page.
  async function activityRefresh() {
    if (!can('crew.activity')) return;
    $('crActBox').hidden = false;
    try {
      const d = await api('/crew/api/activity?days=1');
      const box = $('crAct'); box.textContent = '';
      d.entries.slice(0, 8).forEach(function (e) {
        const at = new Date(e.at);
        box.appendChild(relayUI.el('div', '', relayUI.el('span', '', isNaN(at) ? '' : at.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })),
          relayUI.el('span', '', (e.actor_name || e.token_name || 'Relay') + ': ' + e.summary), relayUI.el('span')));
      });
      if (!d.entries.length) box.appendChild(relayUI.el('p', 'note', 'Nothing yet today.'));
    } catch (e) { /* keeps what it had */ }
  }
  // -- Wi-Fi check-in devices (#356)
  let devPeople = [];
  function devSay(t) { $('crDevErr').textContent = t || ''; $('crDevErr').hidden = !t; }
  function drawDevices(d) {
    const manage = can('crew.manage'), body = document.querySelector('#crDevs tbody');
    $('crDevBox').hidden = false;
    $('crDevOff').hidden = d.wifi;
    $('crDevAdd').hidden = !manage;
    body.textContent = '';
    d.devices.forEach(function (x) {
      const tr = document.createElement('tr');
      tr.appendChild(cell(x.name));
      tr.appendChild(cell((x.label ? x.label + ' \u00b7 ' : '') + x.value));
      tr.appendChild(cell(x.added_by === 'admin' ? 'by an admin' : 'by them'));
      const td = document.createElement('td');
      if (manage) td.appendChild(button('Remove', async function () {
        const r = await send2('DELETE', '/api/admin/crew/devices/' + x.id, null, devSay); if (r) drawDevices(r);
      }));
      tr.appendChild(td); body.appendChild(tr);
    });
    const sel = $('crDevWho'), keep = sel.value; sel.textContent = '';
    devPeople.forEach(function (p) { const o = document.createElement('option'); o.value = p.id; o.textContent = p.name; sel.appendChild(o); });
    if (keep) sel.value = keep;
  }
  async function devicesRefresh() {
    if (!can('crew.view')) return;
    try {
      devPeople = (await api('/api/admin/crew/people')).people || [];
      drawDevices(await api('/api/admin/crew/devices'));
    } catch (e) { /* keeps what it had */ }
  }
  $('crDevAddBtn').addEventListener('click', async function () {
    const r = await send2('POST', '/api/admin/crew/devices', { user_id: +$('crDevWho').value, kind: $('crDevKind').value,
      value: $('crDevValue').value, label: $('crDevLabel').value }, devSay);
    if (r) { $('crDevValue').value = ''; $('crDevLabel').value = ''; drawDevices(r); }
  });
  async function boardRefresh() {
    if (!can('crew.page')) { $('crBoardNone').hidden = false; return; }
    $('crBoardBox').hidden = false;
    try { drawBoard(await api('/api/admin/crew/arrivals')); } catch (e) { /* keeps what it had */ }
  }
  $('crBoardClear').addEventListener('click', async function () {
    const res = await fetch('/api/admin/crew/arrivals', { method: 'DELETE', credentials: 'same-origin' });
    if (res.ok) drawBoard(await res.json());
  });
  // -- checklists (#232): progress per person, refreshed while the page is open. Everything shown is text.
  let listPlan = '';
  function drawLists(d) {
    const body = document.querySelector('#crLists tbody'); body.textContent = '';
    $('crListCount').textContent = d.done + ' of ' + d.total + ' items done';
    if (!listPlan) {                       // today's progress, for the bars on the live board
      progress = {};
      d.people.forEach(function (p) {
        const t = { done: 0, total: 0 };
        p.lists.forEach(function (l) { t.done += l.done; t.total += l.total; });
        progress[p.name] = t;
      });
      drawBoard();
    }
    const sel = $('crListPlan'); sel.textContent = '';
    const add = function (v, t) { const o = document.createElement('option'); o.value = v; o.textContent = t; sel.appendChild(o); };
    add('', 'Today');
    d.plans.forEach(function (p) { if (p.plan !== d.plan || !d.today) add(p.plan, new Date(p.ts * 1000).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })); });
    sel.value = listPlan;
    d.people.forEach(function (p) {
      p.lists.forEach(function (l, n) {
        const tr = document.createElement('tr');
        tr.appendChild(cell(n ? '' : p.name)); tr.appendChild(cell(n ? '' : p.teams.join(', ')));
        tr.appendChild(cell(l.title));
        const td = cell(l.done + ' of ' + l.total + (l.done === l.total ? ' \u2713' : ''));
        if (l.open.length) td.title = 'Still open: ' + l.open.join('; ');
        tr.appendChild(td); body.appendChild(tr);
      });
    });
  }
  async function listsRefresh() {
    if (!can('crew.page')) return;
    $('crListBox').hidden = false;
    try { drawLists(await api('/api/admin/crew/checklists' + (listPlan ? '?plan=' + encodeURIComponent(listPlan) : ''))); } catch (e) { /* keeps what it had */ }
  }
  $('crListPlan').addEventListener('change', function () { listPlan = $('crListPlan').value; listsRefresh(); });
  function leadMsg(text) { $('crListErr').textContent = text || ''; $('crListErr').hidden = !text; }
  function drawLeads(d) {
    const body = document.querySelector('#crLeads tbody'); body.textContent = '';
    const sel = $('crLeadUser'); sel.textContent = '';
    d.people.forEach(function (p) { const o = document.createElement('option'); o.value = p.id; o.textContent = p.name; sel.appendChild(o); });
    const pos = $('crLeadPos'); pos.textContent = '';
    d.picks.forEach(function (p) { const o = document.createElement('option'); o.value = p.ref; o.textContent = p.label; pos.appendChild(o); });
    d.leads.forEach(function (l) {
      const tr = document.createElement('tr'); tr.appendChild(cell(l.name));
      tr.appendChild(cell(l.position + (l.missing ? ' (no longer in Planning Center)' : '') + (l.unmatched ? ' (not matched to Planning Center)' : '')));
      const td = document.createElement('td');
      td.appendChild(button('Remove', async function () {
        const r = await send2('DELETE', '/api/admin/crew/checklist-leads/' + l.user_id + '?position=' + encodeURIComponent(l.position) + '&position_ref=' + encodeURIComponent(l.position_ref), null, leadMsg);
        if (r) drawLeads(r);
      }));
      tr.appendChild(td); body.appendChild(tr);
    });
    $('crLeadBox').hidden = false;
  }
  async function leadsRefresh() {
    if (!can('crew.checklist_edit')) return;
    try { drawLeads(await api('/api/admin/crew/checklist-leads')); } catch (e) { /* keeps what it had */ }
  }
  $('crLeadAdd').addEventListener('click', async function () {
    const r = await send2('POST', '/api/admin/crew/checklist-leads', { user_id: Number($('crLeadUser').value), position_ref: $('crLeadPos').value }, leadMsg);
    if (r) drawLeads(r);
  });
  setInterval(function () { if (document.getElementById('page-crew') && !document.getElementById('page-crew').hidden) { boardRefresh(); listsRefresh(); activityRefresh(); } }, 15000);
  document.addEventListener('relay:config', function () { refresh(); peopleRefresh(); drawn = false; pagesRefresh(); callsRefresh(); boardRefresh(); listsRefresh(); leadsRefresh(); activityRefresh(); });
  refresh(); peopleRefresh(); pagesRefresh(); callsRefresh(); boardRefresh(); listsRefresh(); leadsRefresh(); devicesRefresh(); activityRefresh();
})();
