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
  document.addEventListener('relay:config', function () { refresh(); peopleRefresh(); });
  refresh(); peopleRefresh();
})();
