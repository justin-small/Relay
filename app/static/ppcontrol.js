/* ProPresenter control (#138): next/previous, the slide grid, the clear dock, playlists, looks, macros, timers,
   messages and the media position. Runs inside the admin page, so every call carries the admin session.
   Every control is off while ProPresenter is down or in view-only mode; the server refuses writes then as well. */
(function () {
  const $ = function (id) { return document.getElementById(id); };
  if (!$('page-ppcontrol')) return;
  const BASE = '/api/admin/propresenter';
  let body = null;            // the last /api/admin/propresenter answer: {settings, state}
  let slides = null;          // {uuid, name, rev, cues}
  let slidesFor = '';         // the presentation uuid the grid was built for
  let gridCards = [];
  let playlistId = '';
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
  function pageOpen() { return !$('page-ppcontrol').hidden; }
  function state() { return (body && body.state) || {}; }
  function enabled() { const s = state(); return !!(s.configured && s.connected && !s.view_only); }

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
      b.title = s.view_only ? 'View-only mode is on' : (s.layers && s.layers[b.dataset.layer] ? 'Live now: clear it' : 'Nothing live here');
    });
    $('pcSum').textContent = !s.configured ? 'not set up' : !s.connected ? 'waiting for ProPresenter'
      : s.view_only ? 'connected, view-only' : 'connected';
    ['pcPrev', 'pcNext', 'pcNextPres', 'pcGoTo', 'pcMacroSave'].forEach(function (id) { $(id).disabled = !on; });
    const now = $('pcNow');
    now.textContent = '';
    if (!s.configured) now.appendChild(h('div', 'muted', 'ProPresenter is not set up. Enter its address under ProPresenter.'));
    else if (!s.connected) now.appendChild(h('div', 'muted', s.error || 'Waiting for ProPresenter.'));
    else if (!s.presentation) now.appendChild(h('div', 'muted', 'Nothing is up.'));
    else {
      now.appendChild(h('div', 'muted', s.presentation.name + (s.presentation.part ? ' · ' + s.presentation.part : '')
        + (s.slide_index === null || s.slide_index === undefined ? '' : ' · #' + (s.slide_index + 1))));
      now.appendChild(h('div', 't', (s.current && s.current.text) || '(blank)'));
      if (s.next && s.next.text) now.appendChild(h('div', 'n', 'Next: ' + s.next.text));
    }
    // The grid follows the presentation that is up.
    const uuid = s.presentation ? s.presentation.uuid : '';
    if (pageOpen() && uuid !== slidesFor) loadSlides();
    markLive();
    renderLists();
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
    if (!playlistId) return;
    try {
      const r = await call(BASE + '/playlists/' + encodeURIComponent(playlistId));
      box.dataset.pid = playlistId;
      r.items.forEach(function (it) {
        if (it.type === 'header') { box.appendChild(h('div', 'note', it.name)); return; }
        const b = h('button', 'ppitem');
        b.type = 'button';
        b.dataset.index = it.index;
        b.appendChild(h('span', 'k', String(it.index + 1)));
        b.appendChild(h('span', '', it.name + (it.type !== 'presentation' ? ' (' + it.type + ')' : '')));
        b.addEventListener('click', function () { act(function () { return send('/playlists/' + encodeURIComponent(playlistId) + '/trigger', { index: it.index }); }); });
        box.appendChild(b);
      });
      if (!r.items.length) box.appendChild(h('div', 'muted', 'This playlist is empty.'));
      renderLists();
    } catch (e) { $('pcPlaylistNote').textContent = e.message; }
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
  $('pcNextPres').addEventListener('click', function () { act(function () { return send('/next-presentation'); }); });
  $('pcGridRefresh').addEventListener('click', function () { slidesFor = ''; loadSlides(); });
  $('pcPlaylist').addEventListener('change', function () { playlistId = $('pcPlaylist').value; loadItems(); });
  $('pcPlaylistRefresh').addEventListener('click', loadPlaylists);
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

  /* Arrow keys and Page Up / Page Down step the slide, while this page is open and nothing is being typed. */
  document.addEventListener('keydown', function (ev) {
    if (!pageOpen() || ev.altKey || ev.ctrlKey || ev.metaKey || ev.shiftKey) return;
    const t = ev.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
    const next = ['ArrowRight', 'ArrowDown', 'PageDown'].indexOf(ev.key) !== -1;
    const prev = ['ArrowLeft', 'ArrowUp', 'PageUp'].indexOf(ev.key) !== -1;
    if (!next && !prev) return;
    ev.preventDefault();
    if (!enabled()) return;
    act(function () { return send('/step', { direction: next ? 'next' : 'previous' }); });
  });

  poll();
})();
