/* The slide view and lobby announcements pages (#138 stage 7), inside the admin page: the live and next slide
   as pictures with the countdown and video progress (slideview.js), and the lobby rotation's controls. Runs
   inside the admin page, so every call carries the admin session. Everything from the server is set as text. */
(function () {
  const $ = function (id) { return document.getElementById(id); };
  if (!$('page-pplive')) return;
  const BASE = '/api/admin/propresenter';
  let view = null, body = null, filled = false, timer = null, busy = false, actionErr = '';

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
  function post(path, payload) {
    return call(BASE + path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                               body: JSON.stringify(payload || {}) });
  }
  function pageOpen() { return !$('page-pplive').hidden; }
  function say(id, msg, bad) {
    const n = $(id);
    n.textContent = msg || '';
    n.className = 'note' + (bad ? ' err' : msg ? ' ok' : '');
    if (id === 'lbErr') n.hidden = !msg;
  }

  function flatten(items, out) {
    (items || []).forEach(function (p) {
      if (p.type === 'playlist') out.push(p);
      flatten(p.children, out);
    });
    return out;
  }
  async function fill() {
    const settings = (body && body.settings) || {};
    const lobby = settings.lobby || {}, view_ = settings.view || {};
    $('lbInterval').value = lobby.interval_s || 8;
    $('lbLoop').checked = lobby.loop !== false;
    $('svPublic').checked = !!view_.public;
    const pick = $('lbPlaylist');
    pick.textContent = '';
    const none = h('option', '', 'Choose a playlist');
    none.value = '';
    pick.appendChild(none);
    const tsel = $('svTimer');
    tsel.textContent = '';
    const nt = h('option', '', 'No countdown');
    nt.value = '';
    tsel.appendChild(nt);
    try {
      const pl = flatten((await call(BASE + '/playlists')).playlists, []);
      pl.forEach(function (p) { const o = h('option', '', p.name); o.value = p.uuid; pick.appendChild(o); });
    } catch (e) { /* ProPresenter not answering: the list stays empty */ }
    try {
      ((await call(BASE + '/timers')).timers || []).forEach(function (t) { const o = h('option', '', t.name); o.value = t.uuid; tsel.appendChild(o); });
    } catch (e) { /* the same */ }
    // A saved choice that ProPresenter no longer lists is kept visible rather than silently dropped.
    [[pick, lobby.playlist], [tsel, view_.timer]].forEach(function (pair) {
      if (pair[1] && !Array.prototype.some.call(pair[0].options, function (o) { return o.value === pair[1]; })) {
        const o = h('option', '', 'Saved choice (not found in ProPresenter)');
        o.value = pair[1];
        pair[0].appendChild(o);
      }
      pair[0].value = pair[1] || '';
    });
    filled = true;
  }

  function renderLobby(l) {
    const box = $('lbState');
    box.textContent = '';
    let line;
    if (l.finished) line = 'Finished: the last slide is still up.';
    else if (!l.running) line = 'Not running.';
    else if (l.waiting) line = 'Waiting for ProPresenter to come back. Nothing is changed meanwhile.';
    else if (l.paused === 'operator') line = 'Paused: something else is on the announcements layer. It carries on when you clear that.';
    else if (l.paused === 'cleared') line = 'Paused: announcements were cleared. Press Resume to start again, or Stop.';
    else line = 'Running: ' + l.item + ' (item ' + l.item_number + ' of ' + l.item_count + ', slide ' + l.cue + ').'
      + (l.next_in_s !== null ? ' Next step in ' + Math.ceil(l.next_in_s) + ' s.' : '');
    box.appendChild(h('div', 't' + (l.running ? '' : ' muted'), line));
    $('lbSum').textContent = l.running ? (l.paused ? 'paused' : 'running') : '';
    const on = l.connected && !l.view_only;
    $('lbStart').disabled = !on || !$('lbPlaylist').value;
    $('lbStart').textContent = l.running ? 'Restart' : 'Start';
    $('lbResume').hidden = !(l.running && l.paused);
    $('lbResume').disabled = !on;
    $('lbStop').disabled = !l.running;
    $('lbStopClear').disabled = !on;
    $('lbMsg').textContent = l.view_only ? 'View-only mode is on, so the rotation cannot change anything in ProPresenter.' : '';
    say('lbErr', actionErr || l.error, true);
  }

  async function poll() {
    clearTimeout(timer);
    if (pageOpen()) {
      try {
        body = await call(BASE);
        if (!filled) await fill();
        renderLobby(body.lobby || {});
        const s = body.state || {};
        $('svSum').textContent = !s.configured ? 'not set up' : !s.connected ? 'waiting for ProPresenter' : '';
      } catch (e) { say('lbErr', e.message, true); }
    }
    timer = setTimeout(poll, pageOpen() ? 1000 : 4000);
  }
  async function run(fn) {
    if (busy) return;
    busy = true;
    actionErr = '';
    try { await fn(); } catch (e) { actionErr = e.message; }
    busy = false;
    poll();
  }
  async function save(id, payload) {
    say(id, '');
    try {
      body = await post('', payload);
      say(id, 'Saved.');
    } catch (e) { say(id, e.message, true); }
    poll();
  }

  $('lbSave').addEventListener('click', function () {
    save('lbMsg', { lobby: { playlist: $('lbPlaylist').value, interval_s: Number($('lbInterval').value), loop: $('lbLoop').checked } });
  });
  $('svSave').addEventListener('click', function () {
    save('svMsg', { view: { timer: $('svTimer').value, public: $('svPublic').checked } });
  });
  // Start uses what is on screen: it saves the playlist, time and loop first, so there is no unsaved choice.
  $('lbStart').addEventListener('click', function () {
    run(async function () {
      body = await post('', { lobby: { playlist: $('lbPlaylist').value, interval_s: Number($('lbInterval').value), loop: $('lbLoop').checked } });
      await post('/lobby/start');
    });
  });
  $('lbResume').addEventListener('click', function () { run(function () { return post('/lobby/resume'); }); });
  $('lbStop').addEventListener('click', function () { run(function () { return post('/lobby/stop', { clear: false }); }); });
  $('lbStopClear').addEventListener('click', function () { run(function () { return post('/lobby/stop', { clear: true }); }); });
  window.addEventListener('hashchange', function () { if (pageOpen()) { filled = false; poll(); } });
  view = window.RelaySlideView.create($('svRoot'), { base: BASE + '/live', active: pageOpen });
  poll();
})();
