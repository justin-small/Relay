/* ProPresenter control (#138): next/previous, the slide grid, the clear dock, playlists, looks, macros, timers,
   messages and the media position. Runs inside the admin page, so every call carries the admin session.
   Every control is off while ProPresenter is down or in view-only mode; the server refuses writes then as well. */
(function () {
  const $ = function (id) { return document.getElementById(id); };
  if (!$('page-ppcontrol')) return;
  const BASE = '/api/admin/propresenter';
  // What this user may do here. Without the admin page's relayCan the server still refuses, so assume yes.
  function can(perm) { return typeof window.relayCan !== 'function' || !!window.relayCan(perm); }
  let body = null;            // the last /api/admin/propresenter answer: {settings, state}
  let slides = null;          // {uuid, name, rev, cues}
  let slidesFor = '';         // the presentation uuid the grid was built for
  let gridCards = [];
  let playlistId = '';
  let thumbEpoch = Date.now();   // playlist row thumbnails are cached per page load; Reload asks ProPresenter again
  let timerDefs = [], timerEls = {};
  let busy = false;
  let loadedOnce = false;
  let pollTimer = null, ticks = 0;

  function h(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  async function call(path, opts) {
    const res = await fetch(path, Object.assign({ cache: 'no-store' }, opts || {}));
    const data = await res.json().catch(function () { return {}; });
    if (!res.ok) throw new Error(data.error || data.detail || ('HTTP ' + res.status));
    return data;
  }
  function send(path, payload) {
    return call(BASE + path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                               body: JSON.stringify(payload || {}) });
  }
  function showErr(msg) {
    const n = $('pcErr');
    n.textContent = msg || '';
    n.hidden = !msg;
  }
  function chip(part, color, label) {
    if (!part && !label) return null;
    const c = h('span', 'ppchip', part + (label ? (part ? ' · ' : '') + label : ''));
    if (color) c.style.setProperty('--pp', color);
    return c;
  }
  function pageOpen() { return !$('page-ppcontrol').hidden; }
  function state() { return (body && body.state) || {}; }
  // Every button here changes something in ProPresenter, so each needs propresenter.control as well as a live,
  // not view-only ProPresenter. The page itself (what is up, the grid, the lists) only needs propresenter.view.
  function enabled() { const s = state(); return !!(s.configured && s.connected && !s.view_only && can('propresenter.control')); }
  // Choosing which macros operators see changes Relay's setup.
  function canChoose() { const s = state(); return can('propresenter.manage') && !!(s.configured && s.connected && !s.view_only); }

  /* Run one control. The poll refreshes what changed a moment later. */
  async function act(fn, then) {
    showErr('');
    try { await fn(); if (then) then(); } catch (e) { showErr(e.message); }
    setTimeout(poll, 250);
  }

  /* ---- state and the clear dock ---- */
  function renderState() {
    const s = state();
    const on = enabled();
    const dock = $('ppDock');
    dock.hidden = !(s.configured && s.connected);
    dock.querySelectorAll('button[data-layer]').forEach(function (b) {
      b.disabled = !on;
      b.classList.toggle('lit', !!(s.layers && s.layers[b.dataset.layer]));
      b.title = !can('propresenter.control') ? 'You can view this page but not change it' : s.view_only ? 'View-only mode is on' : (s.layers && s.layers[b.dataset.layer] ? 'Live now: clear it' : 'Nothing live here');
    });
    $('pcSum').textContent = !s.configured ? 'not set up' : !s.connected ? 'waiting for ProPresenter'
      : s.view_only ? 'connected, view-only' : 'connected';
    ['pcPrev', 'pcNext', 'pcPrevPres', 'pcNextPres', 'pcGoTo', 'pcAud', 'pcStageScr'].forEach(function (id) { $(id).disabled = !on; });
    $('pcMacroSave').disabled = !canChoose();
    const choose = can('propresenter.manage');
    $('pcMacroChoose').hidden = !choose;
    if (!choose) $('pcMacroPick').hidden = true;
    $('ppCtlAccessNote').hidden = can('propresenter.control');
    const now = $('pcNow');
    now.textContent = '';
    if (!s.configured) now.appendChild(h('div', 'muted', 'ProPresenter is not set up. Enter its address under ProPresenter.'));
    else if (!s.connected) now.appendChild(h('div', 'muted', s.error || 'Waiting for ProPresenter.'));
    else if (!s.presentation) now.appendChild(h('div', 'muted', 'Nothing is up.'));
    else {
      const it = s.item || {};
      const head = h('div', 'muted');
      head.appendChild(document.createTextNode((it.title || s.presentation.name) + '  '));
      const c = chip(it.part || s.presentation.part || '', it.part_color, it.label);
      if (c) head.appendChild(c);
      if (it.slide_number) head.appendChild(document.createTextNode('slide ' + it.slide_number + (it.slide_count ? ' of ' + it.slide_count : '')));
      if (it.playlist) head.appendChild(document.createTextNode(' · ' + it.playlist + (it.playlist_item && it.playlist_item.name ? ' › ' + it.playlist_item.name : '')));
      now.appendChild(head);
      now.appendChild(h('div', 't', (s.current && s.current.text) || '(blank)'));
      if (it.notes) now.appendChild(h('div', 'ppnotes', 'Notes: ' + it.notes));
      if (s.next && (s.next.text || (it.next && it.next.part))) {
        const nx = h('div', 'n');
        nx.appendChild(document.createTextNode('Next: '));
        const nc = it.next && chip(it.next.part, it.next.part_color, it.next.label);
        if (nc) nx.appendChild(nc);
        nx.appendChild(document.createTextNode(s.next.text || '(blank)'));
        now.appendChild(nx);
        if (it.next && it.next.notes) now.appendChild(h('div', 'ppnotes', 'Next notes: ' + it.next.notes));
      }
    }
    renderScreens();
    // The grid follows the presentation that is up.
    const uuid = s.presentation ? s.presentation.uuid : '';
    if (pageOpen() && uuid !== slidesFor) loadSlides();
    markLive();
    renderLists();
  }

  /* Screens (on/off from status/screens) and the announcement layer, both followed by the server. */
  function renderScreens() {
    const s = state();
    const sc = s.screens || {};
    [['pcAud', 'Audience screens', sc.audience], ['pcStageScr', 'Stage screens', sc.stage]].forEach(function (r) {
      const b = $(r[0]);
      b.textContent = r[1] + ': ' + (r[2] === true ? 'On' : r[2] === false ? 'Off' : '?');
      b.classList.toggle('primary', r[2] === true);
      b.disabled = !enabled() || typeof r[2] !== 'boolean';
      b.title = s.view_only ? 'View-only mode is on' : 'Click to turn ' + (r[2] ? 'off' : 'on');
    });
    $('pcScreenList').textContent = (sc.list || []).map(function (x) {
      return x.name + ' (' + x.type + (x.width ? ' ' + x.width + 'x' + x.height : '') + ')';
    }).join(', ');
    const box = $('pcAnn');
    box.textContent = '';
    const a = s.announcement;
    if (!a) { box.appendChild(h('div', 't muted', s.connected ? 'No announcement is up.' : '')); return; }
    const head = h('div', 'muted');
    head.appendChild(document.createTextNode('Announcement: ' + a.title + '  '));
    const c = chip(a.part, a.part_color, a.label);
    if (c) head.appendChild(c);
    if (a.slide_number) head.appendChild(document.createTextNode('slide ' + a.slide_number + (a.slide_count ? ' of ' + a.slide_count : '')));
    box.appendChild(head);
    box.appendChild(h('div', 't', a.text || '(blank)'));
    if (a.notes) box.appendChild(h('div', 'ppnotes', 'Notes: ' + a.notes));
  }

  function markLive() {
    const s = state();
    const idx = s.presentation && slides && slides.uuid === s.presentation.uuid ? s.slide_index : null;
    gridCards.forEach(function (c, i) { c.classList.toggle('live', i === idx); });
    $('pcGrid').querySelectorAll('button').forEach(function (b) { b.disabled = !enabled(); });
  }

  /* ---- slide grid ---- */
  async function loadSlides() {
    const s = state();
    slidesFor = s.presentation ? s.presentation.uuid : '';
    const grid = $('pcGrid');
    if (!slidesFor) { slides = null; grid.textContent = ''; gridCards = []; $('pcGridName').textContent = ''; $('pcGridNote').textContent = 'No presentation is up. Trigger a slide or a playlist item.'; return; }
    try {
      slides = await call(BASE + '/slides?uuid=' + encodeURIComponent(slidesFor));
    } catch (e) { $('pcGridNote').textContent = e.message; slidesFor = ''; return; }
    $('pcGridName').textContent = slides.name;
    $('pcGridNote').textContent = slides.cues.length ? '' : 'This presentation has no slides.';
    grid.textContent = '';
    gridCards = slides.cues.map(function (c) {
      const b = h('button', 'ppcue');
      b.type = 'button';
      if (c.color) b.style.setProperty('--pp', c.color);
      const img = h('img');
      img.loading = 'lazy';
      img.alt = '';
      img.src = BASE + '/thumbnail/' + encodeURIComponent(slides.uuid) + '/' + c.index + '?v=' + encodeURIComponent(slides.rev);
      b.appendChild(img);
      const meta = h('div', 'meta');
      meta.appendChild(h('div', 'gn', (c.group || 'Slide') + ' · ' + (c.index + 1)));
      meta.appendChild(h('div', 'tx', c.text || c.label || '(blank)'));
      b.appendChild(meta);
      b.addEventListener('click', function () { act(function () { return send('/slide', { uuid: slides.uuid, index: c.index }); }); });
      grid.appendChild(b);
      return b;
    });
    markLive();
  }

  /* ---- playlists ---- */
  async function loadPlaylists() {
    try {
      const r = await call(BASE + '/playlists');
      const sel = $('pcPlaylist');
      const keep = playlistId || (r.active && r.active.presentation && r.active.presentation.playlist && r.active.presentation.playlist.uuid) || '';
      sel.textContent = '';
      (function walk(list, prefix) {
        list.forEach(function (p) {
          if (p.type === 'group') { walk(p.children, prefix + p.name + ' / '); return; }
          const o = h('option', '', prefix + p.name);
          o.value = p.uuid;
          sel.appendChild(o);
        });
      })(r.playlists, '');
      if (keep && Array.prototype.some.call(sel.options, function (o) { return o.value === keep; })) sel.value = keep;
      playlistId = sel.value;
      $('pcPlaylistNote').textContent = sel.options.length ? '' : 'There are no playlists.';
      await loadItems();
    } catch (e) { $('pcPlaylistNote').textContent = e.message; }
  }
  async function loadItems() {
    const box = $('pcItems');
    box.textContent = '';
    $('pcDiagOut').hidden = true;
    if (!playlistId) return;
    try {
      const r = await call(BASE + '/playlists/' + encodeURIComponent(playlistId));
      box.dataset.pid = playlistId;
      r.items.forEach(function (it) {
        if (it.type === 'header') {
          const m = h('div', 'ppmark' + (it.hidden ? ' dim' : ''), it.name || '(section)');
          if (it.color) m.style.setProperty('--pp', it.color);
          box.appendChild(m);
          return;
        }
        const row = h('div', 'ppitemrow' + (it.hidden ? ' dim' : ''));
        const b = h('button', 'ppitem');
        b.type = 'button';
        b.dataset.index = it.index;
        if (it.presentation) {
          const img = h('img');
          img.loading = 'lazy';
          img.alt = '';
          img.src = BASE + '/playlists/' + encodeURIComponent(playlistId) + '/thumbnail/' + it.index + '/0?v=' + encodeURIComponent((it.arrangement_uuid || it.arrangement || '') + '-' + thumbEpoch);
          img.addEventListener('error', function () { img.remove(); });
          b.appendChild(img);
        }
        b.appendChild(h('span', 'k', String(it.index + 1)));
        b.appendChild(h('span', '', it.name));
        const tag = it.type !== 'presentation' ? it.type : (it.arrangement && it.arrangement !== 'Master' ? it.arrangement : '');
        if (tag) b.appendChild(h('span', 'tag', tag));
        b.addEventListener('click', function () { act(function () { return send('/playlists/' + encodeURIComponent(playlistId) + '/trigger', { index: it.index }); }); });
        row.appendChild(b);
        const strip = h('div', 'ppstrip ppgrid');
        strip.hidden = true;
        if (it.presentation) {
          const more = h('button', '', 'Slides');
          more.type = 'button';
          more.setAttribute('aria-expanded', 'false');
          more.addEventListener('click', function () {
            strip.hidden = !strip.hidden;
            more.setAttribute('aria-expanded', String(!strip.hidden));
            if (!strip.hidden && !strip.dataset.loaded) loadStrip(strip, it);
          });
          row.appendChild(more);
        }
        box.appendChild(row);
        box.appendChild(strip);
      });
      if (!r.items.length) box.appendChild(h('div', 'muted', 'This playlist is empty.'));
      renderLists();
    } catch (e) { $('pcPlaylistNote').textContent = e.message; }
  }
  /* One item's cues in ITS arrangement: a chorus the arrangement repeats shows at each place, each with its own thumbnail. */
  async function loadStrip(strip, it) {
    strip.dataset.loaded = '1';
    strip.textContent = '';
    try {
      const r = await call(BASE + '/playlists/' + encodeURIComponent(playlistId) + '/items/' + it.index + '/cues');
      r.cues.forEach(function (c) {
        const card = h('div', 'ppcue');
        if (c.color) card.style.setProperty('--pp', c.color);
        const img = h('img');
        img.loading = 'lazy';
        img.alt = '';
        img.src = BASE + '/playlists/' + encodeURIComponent(playlistId) + '/thumbnail/' + it.index + '/' + c.index + '?v=' + encodeURIComponent(r.rev);
        card.appendChild(img);
        const meta = h('div', 'meta');
        meta.appendChild(h('div', 'gn', (c.group || 'Slide') + ' · ' + (c.index + 1)));
        meta.appendChild(h('div', 'tx', c.text || c.label || '(blank)'));
        card.appendChild(meta);
        strip.appendChild(card);
      });
      if (!r.cues.length) strip.appendChild(h('div', 'muted', 'No slides.'));
    } catch (e) { strip.dataset.loaded = ''; strip.appendChild(h('div', 'muted', e.message)); }
  }
  /* What is in the playlist and what is wrong with it. */
  async function runDiag() {
    const out = $('pcDiagOut');
    if (!playlistId) return;
    out.hidden = false;
    out.textContent = 'Checking...';
    try {
      const r = await call(BASE + '/playlists/' + encodeURIComponent(playlistId) + '/diagnostics');
      out.textContent = '';
      const types = Object.keys(r.types).map(function (k) { return r.types[k] + ' ' + k; }).join(', ');
      out.appendChild(h('div', '', r.count + ' items (' + (types || 'none') + ')' + (r.hidden ? ', ' + r.hidden + ' hidden' : '')
        + '. ' + (r.problems ? r.problems + ' with no matching presentation.' : 'Every presentation was found.')));
      const t = h('table');
      const head = h('tr');
      ['#', 'Type', 'Name', 'Presentation', 'Item ID', 'Check'].forEach(function (x) { head.appendChild(h('th', '', x)); });
      t.appendChild(head);
      const words = { ok: 'found', missing: 'NOT FOUND', none: 'NO PRESENTATION', unknown: 'could not check', unchecked: 'not checked', '': '' };
      r.items.forEach(function (i) {
        const tr = h('tr', i.status === 'missing' || i.status === 'none' ? 'bad' : '');
        [String(i.index + 1), i.type + (i.hidden ? ' (hidden)' : ''), i.name, i.presentation, i.uuid, words[i.status] || i.status].forEach(function (x, n) {
          tr.appendChild(h('td', n === 3 || n === 4 ? 'id' : '', x));
        });
        t.appendChild(tr);
      });
      out.appendChild(t);
    } catch (e) { out.textContent = e.message; }
  }

  /* ---- looks, macros, timers, messages ---- */
  async function loadLooks() {
    const box = $('pcLooks');
    try {
      const r = await call(BASE + '/looks');
      box.textContent = '';
      r.looks.forEach(function (l) {
        const b = h('button', '', l.name);
        b.type = 'button';
        b.dataset.uuid = l.uuid;
        b.addEventListener('click', function () { act(function () { return send('/looks/' + encodeURIComponent(l.uuid) + '/trigger'); }); });
        box.appendChild(b);
      });
      if (!r.looks.length) box.appendChild(h('span', 'muted', 'No looks.'));
      renderLists();
    } catch (e) { box.textContent = e.message; }
  }
  async function loadMacros() {
    const box = $('pcMacros');
    try {
      const r = await call(BASE + '/macros');
      box.textContent = '';
      r.macros.forEach(function (m) {
        const b = h('button', '', m.name);
        b.type = 'button';
        b.dataset.macro = '1';
        if (m.color) b.style.borderColor = m.color;
        b.addEventListener('click', function () { act(function () { return send('/macros/' + encodeURIComponent(m.uuid) + '/trigger'); }); });
        box.appendChild(b);
      });
      $('pcMacroNote').textContent = r.macros.length ? '' : (r.mode === 'all' ? 'There are no macros.' : 'No macros are in the operator-safe selection yet.');
      renderLists();
    } catch (e) { box.textContent = ''; $('pcMacroNote').textContent = e.message; }
  }
  async function loadMacroPicker() {
    const list = $('pcMacroList');
    try {
      const r = await call(BASE + '/macros?all=true');
      $('pcMacroAll').checked = r.mode === 'all';
      list.textContent = '';
      r.macros.forEach(function (m) {
        const l = h('label', 'check');
        const c = h('input');
        c.type = 'checkbox';
        c.value = m.uuid;
        c.checked = m.selected;
        l.appendChild(c);
        l.appendChild(h('span', '', ' ' + m.name));
        list.appendChild(l);
      });
      if (!r.macros.length) list.appendChild(h('div', 'muted', 'ProPresenter has no macros.'));
    } catch (e) { showErr(e.message); }
  }
  async function loadTimers() {
    const box = $('pcTimers');
    try {
      timerDefs = (await call(BASE + '/timers')).timers;
      box.textContent = '';
      timerEls = {};
      timerDefs.forEach(function (t) {
        const row = h('div', 'pptimer');
        row.appendChild(h('span', 'nm', t.name));
        const tm = h('span', 'tm', '--:--:--');
        row.appendChild(tm);
        ['start', 'stop', 'reset'].forEach(function (op) {
          const b = h('button', '', op.charAt(0).toUpperCase() + op.slice(1));
          b.type = 'button';
          b.dataset.timerOp = '1';
          b.addEventListener('click', function () { act(function () { return send('/timers/' + encodeURIComponent(t.uuid) + '/' + op); }); });
          row.appendChild(b);
        });
        box.appendChild(row);
        timerEls[t.uuid] = tm;
      });
      if (!timerDefs.length) box.appendChild(h('div', 'muted', 'No timers.'));
      renderLists();
    } catch (e) { box.textContent = e.message; }
  }
  function refreshSoon() { setTimeout(refreshMessageState, 600); }
  async function loadMessages() {
    const box = $('pcMessages');
    try {
      const r = await call(BASE + '/messages');
      box.textContent = '';
      r.messages.forEach(function (m) {
        const card = h('div', 'ppmsg' + (m.active ? ' active' : ''));
        card.appendChild(h('div', 'mn', m.name));
        card.appendChild(h('div', 'mt', m.message));
        const inputs = [];
        m.tokens.forEach(function (t) {
          if (t.kind !== 'text') return;
          const row = h('div', 'tokrow');
          const id = 'pcTok' + m.uuid + t.name;
          const lab = h('label', '', t.name);
          lab.htmlFor = id;
          const inp = h('input');
          inp.id = id;
          inp.value = t.text;
          inp.maxLength = 500;
          inp.autocomplete = 'off';
          row.appendChild(lab);
          row.appendChild(inp);
          card.appendChild(row);
          inputs.push([t.name, inp]);
        });
        const ctl = h('div', 'ctl');
        const show = h('button', 'primary', 'Show');
        show.type = 'button';
        show.dataset.msgOp = '1';
        show.addEventListener('click', function () {
          act(function () {
            return send('/messages/' + encodeURIComponent(m.uuid) + '/trigger',
              { tokens: inputs.map(function (p) { return { name: p[0], text: p[1].value }; }) });
          }, refreshSoon);
        });
        const clr = h('button', '', 'Clear');
        clr.type = 'button';
        clr.dataset.msgOp = '1';
        clr.addEventListener('click', function () { act(function () { return send('/messages/' + encodeURIComponent(m.uuid) + '/clear'); }, refreshSoon); });
        ctl.appendChild(show);
        ctl.appendChild(clr);
        card.appendChild(ctl);
        box.appendChild(card);
      });
      if (!r.messages.length) box.appendChild(h('div', 'muted', 'No messages are prepared in ProPresenter.'));
      renderLists();
    } catch (e) { box.textContent = e.message; }
  }

  /* What changes with every poll: enabled state, the running timers, which look and playlist item are live. */
  function renderLists() {
    const s = state();
    const on = enabled();
    const live = {};
    (s.timers || []).forEach(function (t) { live[t.id.uuid] = t; });
    Object.keys(timerEls).forEach(function (u) {
      const t = live[u];
      timerEls[u].textContent = t ? t.time : '--:--:--';
      timerEls[u].classList.toggle('running', !!(t && t.state === 'running'));
    });
    ['pcLooks', 'pcMacros', 'pcTimers', 'pcMessages', 'pcItems'].forEach(function (id) {
      $(id).querySelectorAll('button').forEach(function (b) { b.disabled = !on; });
    });
    $('pcLooks').querySelectorAll('button').forEach(function (b) { b.classList.toggle('primary', b.dataset.uuid === s.look_id); });
    const a = s.playlist_active && s.playlist_active.presentation;
    const items = $('pcItems');
    items.querySelectorAll('.ppitem').forEach(function (b) {
      b.classList.toggle('live', !!(a && a.playlist && a.item && a.playlist.uuid === items.dataset.pid && String(a.item.index) === b.dataset.index));
    });
    $('pcGoTo').disabled = !on;
  }

  /* Which message is showing, without rebuilding the cards (that would wipe what is being typed). */
  async function refreshMessageState() {
    if (!$('pcMessages').children.length) return;
    try {
      const r = await call(BASE + '/messages');
      const cards = $('pcMessages').querySelectorAll('.ppmsg');
      r.messages.forEach(function (m, i) { if (cards[i]) cards[i].classList.toggle('active', m.active); });
    } catch (e) { /* the next tick */ }
  }

  function loadAll() {
    if (!enabled() && !(state().configured && state().connected)) return;
    loadedOnce = true;
    loadPlaylists(); loadLooks(); loadMacros(); loadTimers(); loadMessages();
  }

  /* ---- polling: the page twice as fast while open; the dock needs the layers on every page ---- */
  async function poll() {
    clearTimeout(pollTimer);
    try {
      body = await call(BASE);
      const s = state();
      if (pageOpen() && s.configured && s.connected && !loadedOnce) loadAll();
      if (!s.connected) loadedOnce = false;
      renderState();
      if (pageOpen() && ++ticks % 3 === 0) refreshMessageState();
    } catch (e) { /* the next tick */ }
    pollTimer = setTimeout(poll, pageOpen() ? 1000 : 3000);
  }

  /* ---- wiring ---- */
  $('pcPrev').addEventListener('click', function () { act(function () { return send('/step', { direction: 'previous' }); }); });
  $('pcNext').addEventListener('click', function () { act(function () { return send('/step', { direction: 'next' }); }); });
  $('pcNextPres').addEventListener('click', function () { act(function () { return send('/item', { direction: 'next' }); }); });
  $('pcPrevPres').addEventListener('click', function () { act(function () { return send('/item', { direction: 'previous' }); }); });
  $('pcDiag').addEventListener('click', runDiag);
  [['pcAud', 'audience'], ['pcStageScr', 'stage']].forEach(function (r) {
    $(r[0]).addEventListener('click', function () {
      const cur = (state().screens || {})[r[1]];
      if (typeof cur !== 'boolean') return;
      act(function () { return send('/screens', { kind: r[1], on: !cur }); });
    });
  });
  $('pcGridRefresh').addEventListener('click', function () { slidesFor = ''; loadSlides(); });
  $('pcPlaylist').addEventListener('change', function () { playlistId = $('pcPlaylist').value; loadItems(); });
  $('pcPlaylistRefresh').addEventListener('click', function () { thumbEpoch = Date.now(); loadPlaylists(); });
  $('pcGoTo').addEventListener('click', function () {
    act(function () { return send('/transport', { seconds: parseFloat($('pcSeconds').value) }); });
  });
  $('ppDock').addEventListener('click', function (ev) {
    const b = ev.target.closest('button[data-layer]');
    if (b && !b.disabled) act(function () { return send('/clear', { layer: b.dataset.layer }); });
  });
  $('pcMacroChoose').addEventListener('click', function () {
    const box = $('pcMacroPick');
    box.hidden = !box.hidden;
    if (!box.hidden) loadMacroPicker();
  });
  $('pcMacroSave').addEventListener('click', function () {
    const ids = Array.prototype.map.call($('pcMacroList').querySelectorAll('input:checked'), function (c) { return c.value; });
    act(function () { return send('', { macros_mode: $('pcMacroAll').checked ? 'all' : 'selected', macros_allowed: ids }); },
      function () { $('pcMacroPick').hidden = true; loadMacros(); });
  });
  window.addEventListener('hashchange', function () {
    if (pageOpen()) { loadedOnce = false; slidesFor = ''; poll(); }
  });

  /* Arrow keys and Page Up / Page Down step the slide; Shift + the same keys step the playlist item. Only while
     this page is open and nothing is being typed. */
  document.addEventListener('keydown', function (ev) {
    if (!pageOpen() || ev.altKey || ev.ctrlKey || ev.metaKey) return;
    const t = ev.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
    const next = ['ArrowRight', 'ArrowDown', 'PageDown'].indexOf(ev.key) !== -1;
    const prev = ['ArrowLeft', 'ArrowUp', 'PageUp'].indexOf(ev.key) !== -1;
    if (!next && !prev) return;
    ev.preventDefault();
    if (!enabled()) return;
    const dir = next ? 'next' : 'previous';
    act(function () { return ev.shiftKey ? send('/item', { direction: dir }) : send('/step', { direction: dir }); });
  });

  poll();
})();
