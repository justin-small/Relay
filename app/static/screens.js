/* The Screens page in the operator panel (#527): every physical display once, grouped by place, with what each shows
   now. A screen is a browser (a TV, a tablet) or a hardware panel; boards, signage and alerts attach to it, so the lobby
   TV is one tile here and one name everywhere. A tile shows what is on the screen; Edit sets its name, place and what it
   shows (a board or a signage playlist, switched by a named schedule), holds something on it by hand, or removes it.
   Everything from the server is set as text. */
(function () {
  'use strict';
  const page = document.getElementById('page-screens');
  if (!page) return;
  const el = relayUI.el;
  const canManage = relayUI.can('screens.manage');
  const api = relayUI.client({ session: true, cache: 'no-store' });
  const showError = relayUI.note('scErr');
  const button = relayUI.buttons(showError);
  const POLL_MS = 5000;

  let F = null;          // the last frame from the server
  let editing = 0;       // the id of the screen being edited
  let timer = 0;

  const $ = function (id) { return document.getElementById(id); };
  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }

  /* ---------------------------------------------------------------- what a screen shows */
  function contentLabel(kind, ref) {
    if (!kind) return '';
    const list = kind === 'board' ? F.boards : F.playlists;
    const hit = list.filter(function (x) { return x.id === ref; })[0];
    return (kind === 'board' ? 'Board: ' : 'Signage: ') + (hit ? hit.name : '(gone)');
  }

  /* A <select> of everything a screen can show; the value is "board:<id>" or "signage:<id>", empty for nothing. */
  function contentSelect(kind, ref, emptyLabel) {
    const sel = el('select');
    const none = el('option', '', emptyLabel); none.value = ''; sel.appendChild(none);
    const boards = el('optgroup'); boards.label = 'Boards';
    F.boards.forEach(function (b) { const o = el('option', '', b.name); o.value = 'board:' + b.id; boards.appendChild(o); });
    if (boards.children.length) sel.appendChild(boards);
    if (F.signage_on) {
      const lists = el('optgroup'); lists.label = 'Signage playlists';
      F.playlists.forEach(function (p) { const o = el('option', '', p.name); o.value = 'signage:' + p.id; lists.appendChild(o); });
      if (lists.children.length) sel.appendChild(lists);
    }
    sel.value = kind ? kind + ':' + ref : '';
    if (sel.value !== (kind ? kind + ':' + ref : '')) sel.value = '';
    return sel;
  }

  function picked(sel) {
    const at = sel.value.indexOf(':');
    return at < 0 ? { kind: '', ref: '' } : { kind: sel.value.slice(0, at), ref: sel.value.slice(at + 1) };
  }

  function seen(s) {
    if (s.online) return relayUI.pill('now', 'on');
    if (!s.last_seen) return relayUI.pill('never seen', 'idle');
    return relayUI.pill('last seen ' + new Date(s.last_seen).toLocaleString([], { month: 'short', day: 'numeric',
      hour: 'numeric', minute: '2-digit' }), 'idle');
  }

  /* ---------------------------------------------------------------- tiles */
  function tile(s) {
    const now = s.now;
    const what = s.kind === 'panel' ? 'Alerts only' : (now.label ? contentLabel(now.kind, now.ref) : 'Whatever it was opened on');
    const pic = el('div', 'sc-pic', el('strong', '', what));
    if (now.why) pic.appendChild(el('small', 'muted', now.why));
    const acts = el('div', 'ctl');
    if (canManage) {
      acts.appendChild(button('Edit', function () { edit(s.id); }, 'sm'));
      acts.appendChild(button('Flash', async function () {
        const got = await api('POST', '/api/admin/screens/' + s.id + '/flash');
        relayUI.say('scMsg', got.took ? 'Showed a test on ' + s.name + '.' : s.name + ' is not connected.', !got.took, { ms: 4000 });
      }, 'sm'));
      if (s.hold.kind) acts.appendChild(button('Back to schedule', function () { return send('DELETE', '/api/admin/screens/' + s.id + '/hold'); }, 'sm'));
    }
    return el('div', 'sc-tile' + (s.id === editing ? ' on' : ''), pic,
      el('div', 'sc-cap', el('strong', '', s.name), el('div', 'ctl', seen(s), el('span', 'muted', s.kind === 'panel' ? 'Panel' : 'Browser')),
        s.address ? el('small', 'muted mono', s.address) : null, acts));
  }

  function section(place, rows) {
    const head = el('div', 'ctl sc-head', el('h3', 'grow', place ? place.name : 'No place'));
    if (place && canManage) {
      head.appendChild(button('Flash all', async function () {
        const got = await api('POST', '/api/admin/places/' + place.id + '/flash');
        relayUI.say('scMsg', plural(got.took, 'screen showed', 'screens showed') + ' a test.', !got.took, { ms: 4000 });
      }, 'sm'));
      head.appendChild(button('Rename', function () { return renamePlace(place); }, 'sm'));
      head.appendChild(button('Delete', function () { return deletePlace(place); }, 'sm danger'));
    }
    const grid = el('div', 'sc-tiles');
    if (rows.length) rows.forEach(function (s) { grid.appendChild(tile(s)); });
    else grid.appendChild(el('p', 'muted', 'No screens here yet. Set a screen’s place under Edit.'));
    return el('section', 'sc-place', head, grid);
  }

  function draw() {
    const main = $('scMain');
    main.textContent = '';
    if (!F.screens.length && !F.places.length) {
      main.appendChild(el('p', 'muted', 'No screens yet. A TV that opens a Relay board, a signage address or an alert link, and every panel that checks in, shows up here by itself.'));
      return;
    }
    F.places.forEach(function (p) {
      main.appendChild(section(p, F.screens.filter(function (s) { return s.place_id === p.id; })));
    });
    const loose = F.screens.filter(function (s) { return !s.place_id; });
    if (loose.length) main.appendChild(section(null, loose));
  }

  /* ---------------------------------------------------------------- editing one screen */
  function edit(id) {
    editing = id;
    const s = F.screens.filter(function (x) { return x.id === id; })[0];
    const box = $('scEdit');
    box.textContent = '';
    if (!s) { box.hidden = true; return draw(); }
    box.hidden = false;
    const name = el('input'); name.value = s.name; name.maxLength = F.limits.name;
    name.disabled = s.kind === 'panel';
    const place = el('select');
    const none = el('option', '', 'No place'); none.value = ''; place.appendChild(none);
    F.places.forEach(function (p) { const o = el('option', '', p.name); o.value = String(p.id); place.appendChild(o); });
    place.value = s.place_id ? String(s.place_id) : '';
    const form = el('div');
    form.appendChild(relayUI.field('Name', name));
    if (s.kind === 'panel') form.appendChild(el('p', 'note', 'A panel is renamed on the Panels page.'));
    else form.appendChild(el('p', 'note', 'Give this the name of another screen to join the two: it is one TV that two pages knew by different names.'));
    form.appendChild(relayUI.field('Place', place));

    let normal, during, sched, hold;
    if (s.kind === 'browser') {
      normal = contentSelect(s.normal.kind, s.normal.ref, 'Whatever it was opened on');
      during = contentSelect(s.during.kind, s.during.ref, 'Nothing different');
      sched = el('select');
      const nosched = el('option', '', 'No schedule'); nosched.value = ''; sched.appendChild(nosched);
      F.schedules.forEach(function (x) { const o = el('option', '', x.name); o.value = x.ref; sched.appendChild(o); });
      sched.value = s.schedule;
      form.appendChild(el('h3', '', 'Shows'));
      form.appendChild(relayUI.field('Normally', normal));
      form.appendChild(relayUI.field('During this schedule', sched));
      form.appendChild(relayUI.field('Then shows', during));
      if (!F.signage_on) form.appendChild(el('p', 'note', 'Signage is off, so a playlist falls back to the screen’s board.'));
      hold = contentSelect(s.hold.kind, s.hold.ref, 'Nothing held');
      form.appendChild(el('h3', '', 'Show now'));
      form.appendChild(relayUI.field('Hold on the screen until you return it to its schedule', hold));
    }
    if (s.links.length) form.appendChild(el('p', 'note mono', 'Addresses that still work: ' + s.links.join('  ')));

    const bar = el('div', 'ctl');
    bar.appendChild(button('Save', async function () {
      const body = { place_id: place.value ? Number(place.value) : null };
      if (s.kind === 'browser') {
        body.name = name.value; body.normal = picked(normal); body.during = picked(during); body.schedule = sched.value;
      }
      if (s.kind === 'panel') delete body.name;
      F = await api('PUT', '/api/admin/screens/' + s.id, body);
      const h = hold ? picked(hold) : null;
      if (h && (h.kind !== s.hold.kind || h.ref !== s.hold.ref)) {
        F = h.kind ? await api('PUT', '/api/admin/screens/' + s.id + '/hold', h) : await api('DELETE', '/api/admin/screens/' + s.id + '/hold');
      }
      edit(F.screens.some(function (x) { return x.id === s.id; }) ? s.id : 0);
      draw();
    }, 'primary'));
    bar.appendChild(button('Close', function () { edit(0); }));
    bar.appendChild(button('Delete screen', async function () {
      if (!(await relayUI.confirm('Remove ' + s.name + ' from Relay? It comes back by itself the next time it opens a link or checks in.', { danger: true }))) return;
      F = await api('DELETE', '/api/admin/screens/' + s.id);
      edit(0);
    }, 'danger'));
    box.appendChild(el('h3', '', s.name));
    box.appendChild(form);
    box.appendChild(bar);
    draw();
    box.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  /* ---------------------------------------------------------------- places */
  async function addPlace() {
    const name = window.prompt('Name this place (Foyer, Stage, Kids)');
    if (!name) return;
    F = await api('POST', '/api/admin/places', { name: name });
    draw();
  }

  async function renamePlace(p) {
    const name = window.prompt('Rename this place', p.name);
    if (!name || name === p.name) return;
    F = await api('PUT', '/api/admin/places/' + p.id, { name: name });
    draw();
  }

  async function deletePlace(p) {
    if (!(await relayUI.confirm('Delete ' + p.name + '? Its screens stay, with no place.', { danger: true }))) return;
    F = await api('DELETE', '/api/admin/places/' + p.id);
    draw();
  }

  /* ---------------------------------------------------------------- loading */
  async function send(method, path, body) {
    F = await api(method, path, body);
    draw();
  }

  async function load() {
    try {
      F = await api('GET', '/api/admin/screens');
      showError('');
      draw();   // the open form is a separate card, so it keeps what is typed in it while the tiles move
    } catch (e) { showError(e.message); }
  }

  function tick() {
    clearTimeout(timer);
    if (relayUI.shown(page)) load();
    timer = setTimeout(tick, POLL_MS);
  }

  if (canManage) $('scAddPlace').addEventListener('click', function () { addPlace().catch(function (e) { showError(e.message); }); });
  else $('scAddPlace').hidden = true;
  page.addEventListener('relay:tab', load);
  tick();
})();
