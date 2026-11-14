/* The Planning Center rundown in admin (#148): today's rundown with LIVE buttons, mics by position, the note fields
   the rundown reads, and the link a screen opens. Loaded by admin.html only while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('rdBox')) return;
  const $ = R.$, el = R.el, api = R.api, post = R.post;
  function can(perm) { return typeof window.relayCan !== 'function' || !!window.relayCan(perm); }
  function C() { return R.cfg(); }

  let data = null;          // the last GET /rundown
  let mics = null;          // the mic map being edited (null: not touched since the last save)
  let busy = false;

  function clock(ts, zone) {
    if (!ts) return '–';
    return new Date(ts * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', timeZone: zone || undefined });
  }
  function len(s) { return s ? Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0') : ''; }
  function say(id, text, bad) { const n = $(id); n.textContent = text || ''; n.className = 'note' + (bad ? ' err' : text ? ' ok' : ''); }
  function query() {
    const q = new URLSearchParams();
    if ($('rdType').value) q.set('type', $('rdType').value);
    if ($('rdTime').value) q.set('time', $('rdTime').value);
    return q;
  }

  // -- the rundown ---------------------------------------------------------------------------------------------
  function fillSelect(sel, options, keep) {
    const was = keep !== undefined ? keep : sel.value;
    const sig = JSON.stringify(options);
    if (sel.dataset.sig !== sig) {
      sel.dataset.sig = sig;
      sel.innerHTML = '';
      options.forEach(function (o) { const opt = el('option', '', o[1]); opt.value = o[0]; sel.appendChild(opt); });
    }
    if ([...sel.options].some(function (o) { return o.value === was; })) sel.value = was;
  }
  function renderRundown() {
    const r = data.rundown, zone = r.zone;
    fillSelect($('rdType'), data.service_types.map(function (t) { return [t.id, t.name]; }));
    fillSelect($('rdTime'), [['', 'Automatic' + (r.time && !r.time.chosen ? ' (' + clock(r.time.starts_at, zone) + ')' : '')]]
      .concat(r.times.map(function (t) { return [t.hhmm, clock(t.starts_at, zone)]; })));
    const h = r.header || {};
    $('rdNow').textContent = !r.plan ? 'No plan today'
      : (r.live ? 'LIVE · ' + (h.current || '–') + (r.controller ? ' · run by ' + r.controller : '') : 'LIVE is not running');
    const control = can('planning_center.control');
    $('rdLive').querySelectorAll('button').forEach(function (b) { b.disabled = !control || !r.plan || busy; });
    const t = $('rdItems');
    t.innerHTML = '';
    const hd = el('tr');
    ['Planned', 'Actual / proj.', 'Item', 'Leader', 'Mics', 'Length'].forEach(function (x) { hd.appendChild(el('th', '', x)); });
    t.appendChild(hd);
    r.items.forEach(function (it) {
      const tr = el('tr', it.state === 'current' ? 'cur' : it.state === 'done' ? 'done' : '');
      const shown = it.actual || it.projected || it.planned;
      tr.appendChild(el('td', 'mono', clock(it.planned, zone)));
      const at = el('td', 'mono', clock(shown, zone) + (it.state === 'current' ? ' ▶' : ''));
      if (shown && it.planned && shown - it.planned >= 60) at.style.color = 'var(--warn, #fbbf24)';
      tr.appendChild(at);
      tr.appendChild(el('td', '', it.title + (it.song && it.song.key ? '  (' + it.song.key + ')' : '')));
      tr.appendChild(el('td', '', it.song ? it.song.leader : ''));
      tr.appendChild(el('td', 'mono', it.song ? it.song.mics.map(function (m) { return m.mic + ' ' + m.name.split(' ')[0]; }).join(', ') : ''));
      tr.appendChild(el('td', 'mono', len(it.length)));
      t.appendChild(tr);
    });
    $('rdTimes').textContent = !r.plan ? 'Nothing planned today for this service type.'
      : 'Planned times come from the service time and item lengths. Once LIVE starts, everything after the current '
        + 'item is re-projected from when it really started. Planned end ' + clock(h.planned_end, zone)
        + ', projected ' + clock(h.projected_end, zone) + '.';
  }

  // -- mics by position -------------------------------------------------------------------------------------------
  function currentMics() { return mics || (data && data.settings.mics) || []; }
  function holders() {
    const out = {};
    ((data && data.rundown.mics) || []).forEach(function (c) {
      out[c.team + '|' + c.position + '|' + c.mic] = c.person ? c.person.name : '';
    });
    return out;
  }
  function renderMics() {
    const manage = can('planning_center.manage'), control = can('planning_center.control');
    const t = $('rdMics');
    t.innerHTML = '';
    const hd = el('tr');
    ['Team', 'Position', 'Mics', 'Sings', 'Label', 'This service', ''].forEach(function (x) { hd.appendChild(el('th', '', x)); });
    t.appendChild(hd);
    const who = holders();
    currentMics().forEach(function (m, i) {
      const tr = el('tr');
      tr.appendChild(el('td', '', m.team || 'Any team'));
      tr.appendChild(el('td', '', m.position));
      const micIn = el('input'); micIn.value = m.mics.join(', '); micIn.placeholder = '03, 04'; micIn.disabled = !manage;
      micIn.addEventListener('input', function () {
        edit()[i].mics = micIn.value.split(/[,\s]+/).filter(Boolean);
        $('rdMicSave').disabled = false;
      });
      const c1 = el('td'); c1.appendChild(micIn); tr.appendChild(c1);
      const sing = el('input'); sing.type = 'checkbox'; sing.checked = m.sings !== false; sing.disabled = !manage;
      sing.addEventListener('change', function () { edit()[i].sings = sing.checked; $('rdMicSave').disabled = false; });
      const c2 = el('td'); c2.appendChild(sing); tr.appendChild(c2);
      const lab = el('input'); lab.value = m.label || ''; lab.placeholder = 'e.g. Announcements'; lab.disabled = !manage;
      lab.addEventListener('input', function () { edit()[i].label = lab.value; $('rdMicSave').disabled = false; });
      const c3 = el('td'); c3.appendChild(lab); tr.appendChild(c3);
      tr.appendChild(el('td', '', m.mics.map(function (x) {
        const k = m.team + '|' + m.position + '|' + x;
        return x + ' → ' + (who[k] || 'spare');
      }).join(' · ')));
      const act = el('td');
      if (control) {
        const rd = el('button', 'sm', 'Redraw'); rd.type = 'button';
        rd.addEventListener('click', function () { redraw(m.team, m.position); });
        act.appendChild(rd);
      }
      if (manage) {
        const rm = el('button', 'sm forget', 'Remove'); rm.type = 'button';
        rm.addEventListener('click', function () { edit().splice(i, 1); $('rdMicSave').disabled = false; renderMics(); });
        act.appendChild(rm);
      }
      tr.appendChild(act);
      t.appendChild(tr);
    });
    if (!currentMics().length) {
      const tr = el('tr'); const td = el('td', 'muted', 'No positions have mics yet. Add one below.');
      td.colSpan = 7; tr.appendChild(td); t.appendChild(tr);
    }
    const mapped = currentMics().map(function (m) { return m.team + '|' + m.position; });
    fillSelect($('rdAddPos'), [['', 'Pick a team and position…']].concat((data ? data.positions : [])
      .filter(function (p) { return mapped.indexOf(p.team + '|' + p.position) < 0; })
      .map(function (p) { return [p.team + '|' + p.position, (p.team ? p.team + ' · ' : '') + p.position]; })));
    $('rdMicCtl').querySelectorAll('select, #rdAdd, #rdMicSave').forEach(function (n) { n.hidden = !manage; });
    $('rdRedrawAll').hidden = !control;
  }
  function edit() {
    if (mics === null) mics = JSON.parse(JSON.stringify((data && data.settings.mics) || []));
    return mics;
  }
  $('rdAdd').addEventListener('click', function () {
    const v = $('rdAddPos').value;
    if (!v) return;
    const parts = v.split('|');
    edit().push({ team: parts[0], position: parts.slice(1).join('|'), mics: [], sings: true, label: '' });
    $('rdMicSave').disabled = false;
    renderMics();
  });
  $('rdMicSave').addEventListener('click', async function () {
    const empty = edit().filter(function (m) { return !m.mics.length; });
    if (empty.length) { say('rdMicMsg', 'Give ' + empty[0].position + ' at least one mic, or remove it.', true); return; }
    try {
      const res = await post('/api/admin/planning-center/rundown/settings', { mics: edit() });
      data.settings = res.settings; mics = null;
      $('rdMicSave').disabled = true;
      say('rdMicMsg', 'Saved.');
      load();
    } catch (e) { say('rdMicMsg', e.message, true); }
  });
  async function redraw(team, position) {
    try {
      const q = query();
      const res = await post('/api/admin/planning-center/rundown/redraw',
        { type: q.get('type') || '', time: q.get('time') || '', team: team || '', position: position || '' });
      data.rundown = res.rundown;
      renderRundown(); renderMics();
      say('rdMicMsg', 'Drawn again' + (position ? ' for ' + position : '') + '.');
    } catch (e) { say('rdMicMsg', e.message, true); }
  }
  $('rdRedrawAll').addEventListener('click', function () { redraw('', ''); });

  // -- LIVE ----------------------------------------------------------------------------------------------------
  $('rdLive').querySelectorAll('button').forEach(function (b) {
    b.addEventListener('click', async function () {
      busy = true; renderRundown();
      try {
        const res = await post('/api/admin/planning-center/live/' + b.dataset.live, { type: $('rdType').value });
        data.rundown = res.rundown;
        say('rdLiveMsg', '');
      } catch (e) { say('rdLiveMsg', e.message, true); }
      busy = false; renderRundown();
    });
  });

  // -- settings and the link ---------------------------------------------------------------------------------------
  let settingsShown = '';
  function renderSettings() {
    const s = data.settings, sig = JSON.stringify([s.screen, s.leader_note, s.leader_position, s.sermon_note]);
    if (sig !== settingsShown) {
      settingsShown = sig;
      $('rdScreen').checked = !!s.screen;
      $('rdLeaderNote').value = s.leader_note; $('rdLeaderPos').value = s.leader_position; $('rdSermonNote').value = s.sermon_note;
    }
    const manage = can('planning_center.manage');
    ['rdScreen', 'rdLeaderNote', 'rdLeaderPos', 'rdSermonNote', 'rdSetSave'].forEach(function (id) { $(id).disabled = !manage; });
    const teams = [];
    (data.positions || []).forEach(function (p) { if (p.team && teams.indexOf(p.team) < 0) teams.push(p.team); });
    fillSelect($('rdLTeam'), [['', 'All teams']].concat(teams.map(function (t) { return [t, t]; })));
    fillSelect($('rdLTime'), [['', 'Automatic']].concat(data.rundown.times.map(function (t) {
      return [t.hhmm, clock(t.starts_at, data.rundown.zone)];
    })));
    link();
  }
  function link() {
    const q = new URLSearchParams({ rundown: $('rdLView').value });
    if ($('rdLView').value === 'full') { q.set('layout', $('rdLLayout').value); q.set('cards', $('rdLCards').value); }
    if ($('rdLView').value === 'full' && $('rdLTeam').value) q.set('team', $('rdLTeam').value);
    if ($('rdLTime').value) q.set('time', $('rdLTime').value);
    if ($('rdType').value && data && data.service_types.length > 1) q.set('type', $('rdType').value);
    $('rdLink').textContent = '/screen?' + q.toString();
  }
  ['rdLView', 'rdLLayout', 'rdLCards', 'rdLTeam', 'rdLTime'].forEach(function (id) { $(id).addEventListener('change', link); });
  $('rdCopy').addEventListener('click', function () {
    const pc = C() && C().viewer_url;
    const text = (pc || '') + $('rdLink').textContent;
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(function () { say('rdSetMsg', 'Copied.'); });
  });
  $('rdSetSave').addEventListener('click', async function () {
    try {
      const res = await post('/api/admin/planning-center/rundown/settings', {
        screen: $('rdScreen').checked, leader_note: $('rdLeaderNote').value,
        leader_position: $('rdLeaderPos').value, sermon_note: $('rdSermonNote').value });
      data.settings = res.settings; settingsShown = '';
      say('rdSetMsg', res.settings.screen ? 'Saved. The rundown is on for /screen.' : 'Saved. The rundown is off for /screen.');
      load();
    } catch (e) { say('rdSetMsg', e.message, true); }
  });

  // -- polling ----------------------------------------------------------------------------------------------------
  async function load() {
    const pc = C() && C().planning_center;
    const on = pc && pc.connected && pc.service_types.length && !$('page-planningcenter').hidden;
    ['rdBox', 'rdMicBox', 'rdSetBox'].forEach(function (id) { $(id).hidden = !on; });
    if (!on) return;
    try {
      data = await api('/api/admin/planning-center/rundown?' + query().toString());
    } catch (e) { return; }
    renderRundown();
    if (mics === null) renderMics();
    renderSettings();
  }
  $('rdType').addEventListener('change', function () { $('rdTime').value = ''; load(); });
  $('rdTime').addEventListener('change', load);
  window.addEventListener('hashchange', load);
  document.addEventListener('relay:config', load);
  setInterval(load, 3000);
})();
