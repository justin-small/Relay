/* The Planning Center rundown in admin (#148): today's order of service with LIVE buttons, the note fields the
   rundown reads, and the link a screen opens. The channel assignments are in pcchannels.js, which reads this poll. Loaded by admin.html only while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('rdBox')) return;
  const $ = R.$, el = R.el, api = R.api, post = R.post;
  const can = relayUI.can;
  function C() { return R.cfg(); }

  let data = null;          // the last GET /rundown
  let busy = false;

  function clock(ts, zone) {
    if (!ts) return '–';
    return new Date(ts * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', timeZone: zone || undefined });
  }
  function len(s) { return s ? Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0') : ''; }
  const say = relayUI.say;
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
      if (shown && it.planned && shown - it.planned >= 60) at.style.color = 'var(--warn)';
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
  // The view, order and people picks are drawn as cards and segmented buttons; the selects behind them hold the value.
  const VIEWS = {
    full: ['Order of service and people. Replaces the captions on that screen.', ['', '', '']],
    side: ['A narrow rundown next to the live captions.', ['a', '']],
    micboard: ['Only who holds which mic, as big cards.', ['', '', '', '']]
  };
  function drawChoices() {
    const cards = $('rdViewCards');
    [...$('rdLView').options].forEach(function (o) {
      const b = el('button', 'pcview'); b.type = 'button'; b.dataset.v = o.value;
      const thumb = el('div', 'pcthumb');
      (VIEWS[o.value] ? VIEWS[o.value][1] : ['']).forEach(function (c) { thumb.appendChild(el('i', c)); });
      b.appendChild(thumb);
      b.appendChild(el('b', '', o.textContent));
      b.appendChild(el('span', '', VIEWS[o.value] ? VIEWS[o.value][0] : ''));
      b.addEventListener('click', function () { $('rdLView').value = o.value; $('rdLView').dispatchEvent(new Event('change')); });
      cards.appendChild(b);
    });
    [['rdLLayout', 'rdLLayoutSeg'], ['rdLCards', 'rdLCardsSeg']].forEach(function (p) {
      [...$(p[0]).options].forEach(function (o) {
        const b = el('button', '', o.textContent); b.type = 'button'; b.dataset.v = o.value;
        b.addEventListener('click', function () { $(p[0]).value = o.value; $(p[0]).dispatchEvent(new Event('change')); });
        $(p[1]).appendChild(b);
      });
    });
  }
  function paintChoices() {
    [['rdLView', 'rdViewCards', 'on'], ['rdLLayout', 'rdLLayoutSeg', 'on'], ['rdLCards', 'rdLCardsSeg', 'on']].forEach(function (p) {
      $(p[1]).querySelectorAll('[data-v]').forEach(function (b) { b.classList.toggle(p[2], b.dataset.v === $(p[0]).value); });
    });
    const full = $('rdLView').value === 'full';      // order and people apply to the full rundown only
    $('rdLLayoutSeg').closest('.field').hidden = !full;
    $('rdLCardsSeg').closest('.field').hidden = !full;
    $('rdLTeam').closest('.field').hidden = !full;
  }
  drawChoices();

  function link() {
    paintChoices();
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
  $('rdOpen').addEventListener('click', function () {
    const base = C() && C().viewer_url;
    if (base) window.open(base + $('rdLink').textContent, '_blank', 'noopener');
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
    ['rdBox', 'pcChBox', 'rdSetBox'].forEach(function (id) { $(id).hidden = !on; });
    if (!on) return;
    try {
      data = await api('/api/admin/planning-center/rundown?' + query().toString());
    } catch (e) { return; }
    renderRundown();
    renderSettings();
    document.dispatchEvent(new CustomEvent('pc:rundown', { detail: data }));
  }
  $('rdType').addEventListener('change', function () { $('rdTime').value = ''; load(); });
  $('rdTime').addEventListener('change', load);
  window.addEventListener('hashchange', load);
  document.addEventListener('relay:config', load);
  document.addEventListener('pc:poll', load);
  setInterval(load, 3000);
})();
