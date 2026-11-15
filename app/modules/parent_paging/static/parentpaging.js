/* The Parent paging admin page (#220): the pages that are up (Show / Clear), the ProPresenter message and the
   rooms with their links. Loaded by admin.html only while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-parentpaging')) return;
  const $ = R.$, api = R.api, post = R.post;
  const BASE = '/api/admin/parent-paging';
  const STATUS = { waiting: 'Waiting for you', shown: 'On screen', sent: 'In the ProPresenter message' };
  function can(perm) { return typeof window.relayCan !== 'function' || !!window.relayCan(perm); }
  function showError(text) { $('ppgErr').textContent = text || ''; $('ppgErr').hidden = !text; }
  function ago(s) { return s < 60 ? s + ' s ago' : Math.floor(s / 60) + ' min ago'; }

  async function send(method, path, body) {
    return api(path, { method: method, headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body) });
  }

  let filled = false, loaded = false, seen = {}, drawn = '', serverErr = false;

  function button(text, cls, fn) {
    const b = document.createElement('button');
    b.type = 'button'; b.textContent = text;
    if (cls) b.className = cls;
    b.onclick = async function () {
      b.disabled = true; showError('');
      try { await fn(); } catch (e) { showError(e.message); }
      b.disabled = false;
    };
    return b;
  }

  function pageRow(p, mode, control) {
    const d = document.createElement('div');
    d.style.cssText = 'display:flex;align-items:center;gap:12px;padding:8px 0;border-top:1px solid var(--line, #8883)';
    const code = document.createElement('div');
    code.textContent = p.code;
    code.style.cssText = 'font-size:1.6em;font-weight:700;letter-spacing:.08em;min-width:5em';
    const info = document.createElement('div');
    info.style.cssText = 'flex:1;min-width:8em';
    const room = document.createElement('div');
    room.textContent = p.room;
    const meta = document.createElement('div');
    meta.className = 'note' + (p.status === 'waiting' ? ' err' : '');
    meta.textContent = STATUS[p.status] + ' · ' + ago(p.age_s);
    meta.dataset.age = p.id;
    info.append(room, meta);
    d.append(code, info);
    if (control) {
      if (mode === 'queue' && p.status === 'waiting') {
        d.appendChild(button('Show', 'primary', async function () { render(await post(BASE + '/pages/' + p.id + '/show')); }));
      }
      d.appendChild(button('Clear', 'forget', async function () { render(await post(BASE + '/pages/' + p.id + '/clear')); }));
    }
    return d;
  }

  function roomRow(r, manage) {
    const d = document.createElement('div');
    d.style.cssText = 'padding:10px 0;border-top:1px solid var(--line, #8883)';
    const top = document.createElement('div');
    top.style.cssText = 'display:flex;align-items:center;gap:8px;flex-wrap:wrap';
    const info = document.createElement('div');
    info.style.cssText = 'flex:1;min-width:0';
    const name = document.createElement('div');
    name.textContent = r.name; name.style.fontWeight = '600';
    const meta = document.createElement('div');
    meta.className = 'note';
    meta.textContent = r.event ? 'Check-Ins: ' + r.event : 'Check-Ins room ' + r.location_id;
    info.append(name, meta);
    top.appendChild(info);
    const linkBox = document.createElement('div');
    linkBox.hidden = true;
    linkBox.style.cssText = 'margin-top:8px;display:flex;gap:12px;align-items:center;flex-wrap:wrap';
    function showLink(data) {
      linkBox.textContent = '';
      const qr = document.createElement('div');
      qr.style.cssText = 'width:120px;height:120px;background:#fff';
      try {
        if (typeof qrcode === 'function') {
          const q = qrcode(0, 'M'); q.addData(data.url); q.make();
          qr.innerHTML = q.createSvgTag({ cellSize: 3, margin: 2, scalable: true });
        }
      } catch (e) { /* the link text is still there */ }
      const a = document.createElement('a');
      a.href = data.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.textContent = data.url;
      a.style.wordBreak = 'break-all';
      const copy = button('Copy', '', async function () { await navigator.clipboard.writeText(data.url); });
      linkBox.append(qr, a, copy);
      linkBox.hidden = false;
    }
    if (manage) {
      top.appendChild(button('Link', '', async function () {
        if (!linkBox.hidden) { linkBox.hidden = true; return; }
        showLink(await api(BASE + '/rooms/' + r.id + '/link'));
      }));
      top.appendChild(button('New link', '', async function () {
        if (!confirm('Make a new link for ' + r.name + '? The old link stops working at once.')) return;
        showLink(await post(BASE + '/rooms/' + r.id + '/new-link'));
      }));
      top.appendChild(button('Rename', '', async function () {
        const n = prompt('Name for this room', r.name);
        if (n === null) return;
        render(await send('PUT', BASE + '/rooms/' + r.id, { name: n }));
      }));
      top.appendChild(button('Remove', 'forget', async function () {
        if (!confirm('Remove ' + r.name + '? Its link stops working and its pages come down.')) return;
        render(await send('DELETE', BASE + '/rooms/' + r.id));
      }));
    }
    d.append(top, linkBox);
    return d;
  }

  function render(data) {
    const s = data.settings, control = can('parent_paging.control'), manage = can('parent_paging.manage');
    $('ppgNeeds').hidden = !data.needs.length;
    $('ppgNeeds').textContent = data.needs.length ? 'Switch on ' + data.needs.join(' and ') +
      ' on the Modules page: parent paging needs both.' : '';
    // ProPresenter's last refusal shows until a write works again.
    if (data.error || serverErr) showError(data.error);
    serverErr = !!data.error;
    // Rebuilt only when what it shows changes (the ages are updated in place), so a poll never swaps a button
    // out from under the operator's click.
    const sig = JSON.stringify([s.mode, control, manage, s.rooms, data.pages.map(function (p) { return [p.id, p.status]; })]);
    const list = $('ppgPages');
    if (sig === drawn) {
      data.pages.forEach(function (p) {
        const m = list.querySelector('[data-age="' + p.id + '"]');
        if (m) m.textContent = STATUS[p.status] + ' · ' + ago(p.age_s);
      });
    } else {
      list.textContent = '';
      data.pages.forEach(function (p) { list.appendChild(pageRow(p, s.mode, control)); });
    }
    $('ppgNone').hidden = data.pages.length > 0;
    $('ppgClearAll').hidden = !(control && data.pages.length > 1);
    $('ppgScreen').hidden = !data.on_screen;
    $('ppgScreen').textContent = data.on_screen ? 'ProPresenter message token: ' + data.on_screen : '';
    const waiting = data.pages.filter(function (p) { return p.status === 'waiting'; }).length;
    $('ppgSum').textContent = data.pages.length ? (waiting ? waiting + ' waiting' : data.pages.length + ' up') : '';
    // A page that arrived since the last look: a short tone, so the operator notices without watching this page.
    let fresh = false;
    data.pages.forEach(function (p) { if (!seen[p.id]) { fresh = fresh || loaded; seen[p.id] = 1; } });
    loaded = true;
    if (fresh) chime();
    if (!filled) {
      filled = true;
      $('ppgMode').value = s.mode; $('ppgMessage').value = s.message; $('ppgToken').value = s.token;
      $('ppgTimeout').value = s.timeout_min;
    }
    ['ppgMode', 'ppgMessage', 'ppgToken', 'ppgTimeout', 'ppgSave'].forEach(function (id) { $(id).disabled = !manage; });
    $('ppgAddRow').hidden = $('ppgAddCtl').hidden = !manage;
    if (sig !== drawn) {
      const rooms = $('ppgRooms');
      rooms.textContent = '';
      s.rooms.forEach(function (r) { rooms.appendChild(roomRow(r, manage)); });
      drawn = sig;
    }
    $('ppgNoRooms').hidden = s.rooms.length > 0;
  }

  let audio = null, primed = false;
  function chime() {
    if (!primed) return;   // browsers only play sound after the page has been clicked once
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      const o = audio.createOscillator(), g = audio.createGain();
      o.frequency.value = 880; g.gain.value = 0.08;
      o.connect(g); g.connect(audio.destination);
      o.start(); o.stop(audio.currentTime + 0.25);
    } catch (e) { /* no sound */ }
  }
  document.addEventListener('click', function () { primed = true; }, { once: true });

  async function refresh() {
    try { render(await api(BASE)); } catch (e) { /* signed out or off: the next round tries again */ }
  }

  $('ppgSave').onclick = async function () {
    showError('');
    try {
      render(await send('PUT', BASE + '/settings', { mode: $('ppgMode').value, message: $('ppgMessage').value,
        token: $('ppgToken').value, timeout_min: parseInt($('ppgTimeout').value, 10) || 0 }));
      if (R.okMsg) R.okMsg('Saved.');
    } catch (e) { showError(e.message); }
  };
  $('ppgClearAll').onclick = async function () {
    if (!confirm('Take every parent page down?')) return;
    try { render(await post(BASE + '/pages/clear')); } catch (e) { showError(e.message); }
  };
  $('ppgLoad').onclick = async function () {
    showError('');
    $('ppgLoad').disabled = true;
    try {
      const data = await api(BASE + '/locations');
      const sel = $('ppgLoc');
      sel.textContent = '';
      const first = document.createElement('option');
      first.value = ''; first.textContent = data.locations.length ? 'Choose a room…' : 'Check-Ins has no rooms';
      sel.appendChild(first);
      data.locations.forEach(function (l) {
        const o = document.createElement('option');
        o.value = l.id; o.textContent = (l.event ? l.event + ' · ' : '') + l.name;
        o.dataset.name = l.name; o.dataset.event = l.event;
        sel.appendChild(o);
      });
    } catch (e) { showError(e.message); }
    $('ppgLoad').disabled = false;
  };
  $('ppgAdd').onclick = async function () {
    const o = $('ppgLoc').selectedOptions[0];
    if (!o || !o.value) { showError('Load the rooms and choose one first.'); return; }
    showError('');
    try {
      render(await post(BASE + '/rooms', { location_id: o.value, name: $('ppgRoomName').value || o.dataset.name,
        event: o.dataset.event }));
      $('ppgRoomName').value = '';
    } catch (e) { showError(e.message); }
  };

  // Polled whichever admin page is open, so a new page still chimes while the operator works elsewhere.
  setInterval(refresh, 3000);
  refresh();
})();
