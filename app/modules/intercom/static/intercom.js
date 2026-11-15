/* The Intercom admin page (#238): LiveKit's state, the LAN address and ports it uses, a restart, and the log tail.
   Loaded by admin.html only while the module is on. Everything from the server is shown as text. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-intercom')) return;
  const $ = R.$, api = R.api, post = R.post;
  function can(perm) { return typeof window.relayCan !== 'function' || !!window.relayCan(perm); }

  let last = null;
  let timer = null;

  function uptime(s) {
    if (s < 90) return s + ' s';
    if (s < 5400) return Math.round(s / 60) + ' min';
    return Math.round(s / 3600) + ' h';
  }

  function render(v) {
    last = v;
    $('icSum').textContent = v.state === 'running' ? 'running' : v.state;
    $('icState').textContent = v.detail || (v.state === 'running' ? 'Running for ' + uptime(v.uptime_s) + '.' :
      v.state === 'off' ? 'Not running.' : '');
    $('icState').className = 'note ' + (v.state === 'running' ? 'ok' : (v.state === 'error' || v.state === 'missing' ? 'err' : ''));
    $('icFacts').textContent = 'LiveKit ' + v.version + ', media on ' + v.node_ip + '.';
    $('icPorts').textContent = v.ports.tcp + '/tcp, ' + v.ports.udp + '/udp';
    $('icLog').textContent = v.log.join('\n');
    $('icRestart').hidden = !can('intercom.manage');
  }

  function day(iso) { return iso ? iso.slice(0, 10) : ''; }

  function renews(iso) {
    if (!iso) return '';
    const d = Math.round((Date.parse(iso) - Date.now()) / 86400000);
    return d <= 0 ? 'today' : 'in ' + d + ' d';
  }

  function renderPhones(list) {
    const body = $('icPhones');
    body.textContent = '';
    list.forEach(function (p) {
      const tr = document.createElement('tr');
      [p.user, p.name, day(p.signed_in), day(p.last_seen), renews(p.renews_at), p.status].forEach(function (v) {
        const td = document.createElement('td');
        td.textContent = v;
        tr.appendChild(td);
      });
      const act = document.createElement('td');
      if (can('intercom.manage')) {
        const out = document.createElement('button');
        out.type = 'button';
        out.textContent = 'Sign out';
        out.addEventListener('click', async function () {
          try { await R.api('/api/admin/intercom/phones/' + p.id, { method: 'DELETE' }); } catch (e) { fail(e); }
          refreshPhones();
        });
        act.appendChild(out);
      }
      tr.appendChild(act);
      body.appendChild(tr);
    });
    $('icPhonesTable').hidden = !list.length;
    $('icPhonesEmpty').hidden = !!list.length;
    $('icSignOutAll').hidden = !list.length || !can('intercom.manage');
  }

  function fail(e) {
    const box = $('icErr') || $('icPhonesErr');
    if (!box) return;
    box.textContent = e.message;
    box.hidden = false;
  }

  async function refreshPhones() {
    if (!$('icPhonesSection')) return;
    if (!can('intercom.manage')) { $('icPhonesSection').hidden = true; return; }
    try { renderPhones((await api('/api/admin/intercom/phones')).phones); } catch (e) { /* as above */ }
  }


  /* ---- channels, members and the production label (#275). Names and text from the server are set as text. ---- */
  const MODES = { preempt: 'Pre-empt', party: 'Party line', queue: 'One at a time' };
  let editing = null;      // the channel being edited: { id, version, members: [...] }
  let people = [];

  function cell(text) { const td = document.createElement('td'); td.textContent = text; return td; }

  function btn(label, fn) {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = label;
    b.addEventListener('click', fn);
    return b;
  }

  function editError(msg) { $('icEErr').textContent = msg || ''; $('icEErr').hidden = !msg; }

  function renderChannels(list) {
    const body = $('icChannels');
    body.textContent = '';
    let dragged = null;
    list.forEach(function (c) {
      const tr = document.createElement('tr');
      tr.draggable = can('intercom.manage');
      tr.dataset.id = c.id;
      const dot = document.createElement('td');
      const sw = document.createElement('span');
      sw.style.cssText = 'display:inline-block;width:12px;height:12px;border-radius:50%;background:' + (/^#[0-9a-fA-F]{3,8}$/.test(c.colour) ? c.colour : '#888');
      dot.appendChild(sw);
      tr.appendChild(dot);
      [c.name, MODES[c.mode] || c.mode, String(c.max_talkers), c.latch_seconds ? c.latch_seconds + ' s' : 'none',
        String(c.members)].forEach(function (v) { tr.appendChild(cell(v)); });
      const act = document.createElement('td');
      act.appendChild(btn('Edit', function () { openEditor(c.id); }));
      tr.appendChild(act);
      tr.addEventListener('dragstart', function () { dragged = tr; });
      tr.addEventListener('dragover', function (e) { e.preventDefault(); });
      tr.addEventListener('drop', async function (e) {
        e.preventDefault();
        if (!dragged || dragged === tr) return;
        const rows = Array.prototype.slice.call(body.children);
        body.insertBefore(dragged, rows.indexOf(dragged) < rows.indexOf(tr) ? tr.nextSibling : tr);
        const ids = Array.prototype.map.call(body.children, function (r) { return Number(r.dataset.id); });
        try { await post('/api/admin/intercom/channels/reorder', { ids: ids }); } catch (err) { fail(err); }
        refreshChannels();
      });
      body.appendChild(tr);
    });
    $('icChannelsTable').hidden = !list.length;
    $('icChannelsEmpty').hidden = !!list.length;
  }

  async function refreshChannels() {
    if (!can('intercom.manage')) { $('icChannelsSection').hidden = true; return; }
    try { renderChannels((await api('/api/admin/intercom/channels')).channels); } catch (e) { /* as above */ }
    try {
      const p = await api('/api/admin/intercom/production');
      if (document.activeElement !== $('icLabel')) $('icLabel').value = p.label;
      $('icLabelPlan').hidden = !p.plan_available;
    } catch (e) { /* as above */ }
  }

  function syncPriority() {
    const show = $('icEMode').value !== 'party';
    Array.prototype.forEach.call(document.querySelectorAll('#icEdit .icPrio'), function (el) { el.hidden = !show; });
  }

  function renderMembers() {
    const body = $('icMembers');
    body.textContent = '';
    editing.members.forEach(function (m) {
      const tr = document.createElement('tr');
      tr.appendChild(cell(m.name));
      ['can_talk', 'can_listen'].forEach(function (k) {
        const td = document.createElement('td');
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = !!m[k];
        cb.setAttribute('aria-label', m.name + (k === 'can_talk' ? ' can talk' : ' can listen'));
        cb.addEventListener('change', function () { m[k] = cb.checked ? 1 : 0; });
        td.appendChild(cb);
        tr.appendChild(td);
      });
      const pt = document.createElement('td');
      pt.className = 'icPrio';
      const num = document.createElement('input');
      num.type = 'number'; num.min = 1; num.max = 9; num.value = m.priority; num.style.width = '4em';
      num.setAttribute('aria-label', m.name + ' priority');
      num.addEventListener('change', function () { m.priority = Number(num.value) || 5; });
      pt.appendChild(num);
      tr.appendChild(pt);
      const rm = document.createElement('td');
      rm.appendChild(btn('Remove', function () {
        editing.members = editing.members.filter(function (x) { return x.user_id !== m.user_id; });
        renderMembers();
      }));
      tr.appendChild(rm);
      body.appendChild(tr);
    });
    syncPriority();
  }

  async function openEditor(id, fresh) {
    editError('');
    try {
      const c = (await api('/api/admin/intercom/channels/' + id)).channel;
      editing = { id: c.id, version: c.version, members: c.members.map(function (m) { return Object.assign({}, m); }), fresh: !!fresh };
      $('icEditTitle').textContent = 'Edit ' + c.name;
      $('icEName').value = c.name;
      $('icEColour').value = /^#[0-9a-fA-F]{6}$/.test(c.colour) ? c.colour : '#3b82f6';
      $('icEMode').value = c.mode;
      $('icEMax').value = c.max_talkers;
      $('icELatch').value = c.latch_seconds;
      $('icEdit').hidden = false;
      renderMembers();
      searchPeople('');
    } catch (e) { fail(e); }
  }

  async function searchPeople(q) {
    try { people = (await api('/api/admin/intercom/people?q=' + encodeURIComponent(q))).people; } catch (e) { return; }
    const list = $('icAddList');
    list.textContent = '';
    people.forEach(function (p) {
      const o = document.createElement('option');
      o.value = p.name + ' (' + p.username + ')';
      list.appendChild(o);
    });
  }

  $('icAddQ').addEventListener('input', function () { searchPeople($('icAddQ').value.split(' (')[0]); });
  $('icAdd').addEventListener('click', function () {
    const typed = $('icAddQ').value.trim().toLowerCase();
    const p = people.find(function (x) { return (x.name + ' (' + x.username + ')').toLowerCase() === typed ||
      x.username.toLowerCase() === typed; });
    if (!p) { editError('Pick a person from the list.'); return; }
    if (editing.members.some(function (m) { return m.user_id === p.id; })) { editError(p.name + ' is already on this channel.'); return; }
    editError('');
    editing.members.push({ user_id: p.id, name: p.name, username: p.username, can_talk: 1, can_listen: 1, priority: 5 });
    $('icAddQ').value = '';
    renderMembers();
  });
  $('icEMode').addEventListener('change', function () {
    if ($('icEMode').value === 'queue' && Number($('icEMax').value) > 1 && editing && editing.fresh) $('icEMax').value = 1;
    syncPriority();
  });
  $('icECancel').addEventListener('click', function () { $('icEdit').hidden = true; editing = null; });

  $('icESave').addEventListener('click', async function () {
    if (!editing) return;
    editError('');
    try {
      const c = (await api('/api/admin/intercom/channels/' + editing.id, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          version: editing.version, name: $('icEName').value, colour: $('icEColour').value, mode: $('icEMode').value,
          max_talkers: Number($('icEMax').value), latch_seconds: Number($('icELatch').value),
          members: editing.members.map(function (m) {
            return { user_id: m.user_id, can_talk: !!m.can_talk, can_listen: !!m.can_listen, priority: m.priority };
          })
        })
      })).channel;
      editing.version = c.version;
      $('icEdit').hidden = true;
      editing = null;
      refreshChannels();
    } catch (e) { editError(e.message); }
  });

  $('icEDelete').addEventListener('click', async function () {
    if (!editing) return;
    const names = editing.members.map(function (m) { return m.name; });
    const who = names.length ? ' These people will lose it: ' + names.join(', ') + '.' : '';
    if (!window.confirm('Delete ' + $('icEName').value + '?' + who + ' This cannot be undone.')) return;
    try {
      await api('/api/admin/intercom/channels/' + editing.id, { method: 'DELETE' });
      $('icEdit').hidden = true;
      editing = null;
      refreshChannels();
    } catch (e) { editError(e.message); }
  });

  $('icNewChannel').addEventListener('click', async function () {
    try {
      const c = (await post('/api/admin/intercom/channels', { name: $('icNewName').value })).channel;
      $('icNewName').value = '';
      await refreshChannels();
      openEditor(c.id, true);
    } catch (e) { fail(e); }
  });

  $('icLabelSave').addEventListener('click', async function () {
    try {
      const r = await api('/api/admin/intercom/production', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ label: $('icLabel').value })
      });
      $('icLabel').value = r.label;
    } catch (e) { fail(e); }
  });

  $('icLabelPlan').addEventListener('click', async function () {
    try { $('icLabel').value = (await api('/api/admin/intercom/production/plan')).label; } catch (e) { fail(e); }
  });

  async function refresh() {
    try { render(await api('/api/admin/intercom')); } catch (e) { /* the admin page reports lost sessions */ }
    refreshPhones();
    refreshChannels();
  }

  function visible() {
    return ['page-intercom', 'page-intercom-phones'].some(function (id) {
      const el = document.getElementById(id);
      return el && !el.closest('.page').hidden;
    });
  }

  function tick() {
    if (visible()) refresh();
    timer = setTimeout(tick, last && last.state !== 'running' && last.state !== 'off' ? 2000 : 8000);
  }

  $('icRestart').addEventListener('click', async function () {
    $('icErr').hidden = true;
    try { render(await post('/api/admin/intercom/restart', {})); } catch (e) {
      $('icErr').textContent = e.message;
      $('icErr').hidden = false;
    }
  });

  if ($('icSignOutAll')) $('icSignOutAll').addEventListener('click', async function () {
    if (!window.confirm('Sign out every phone? Each person will have to sign in again.')) return;
    try { await post('/api/admin/intercom/phones/signout-all', {}); } catch (e) { fail(e); }
    refreshPhones();
  });

  document.addEventListener('relay:config', function () { refresh(); if (!timer) tick(); });
})();
