/* The Planning Center link (#138 stage 6), in the admin ProPresenter page: the switches, what ProPresenter and
   Planning Center each have up, the match of the plan to presentations (with pairing for the ones that need it) and
   the button that builds the Relay-owned playlist. Runs inside the admin page, so every call carries the admin
   session. Everything shown from the server is set as text, never as markup. */
(function () {
  const $ = function (id) { return document.getElementById(id); };
  if (!$('ppLink')) return;
  const BASE = '/api/admin/propresenter/link';
  // What this user may do here. Without the admin page's relayCan the server still refuses, so assume yes.
  function can(perm) { return typeof window.relayCan !== 'function' || !!window.relayCan(perm); }
  let last = null, filled = false, pairing = null, names = null, timer = null, busy = false;

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
  function post(path, body) {
    return call(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  }
  function say(msg, bad) {
    const n = $('plkMsg');
    n.textContent = msg || '';
    n.className = 'note' + (bad ? ' err' : msg ? ' ok' : '');
  }
  function kv(label, value, cls) {
    const row = h('div', 'note' + (cls ? ' ' + cls : ''));
    row.style.marginTop = '0';
    row.appendChild(h('strong', '', label + ': '));
    row.appendChild(document.createTextNode(value));
    return row;
  }
  function page() { return $('page-propresenter'); }

  const METHOD = { mapping: 'paired by you', synced: "ProPresenter's synced playlist", exact: 'same title', fuzzy: 'similar title' };
  const STATUS = { matched: 'Matched', unmatched: 'No match', ambiguous: 'Several fit', placeholder: 'Not linked in ProPresenter',
                   skipped: 'Header' };

  function fill(s) {
    if (filled) return;
    filled = true;
    $('plkDwell').value = s.dwell_s;
  }

  function render(st) {
    last = st;
    const s = st.settings;
    fill(s);
    [['plkFollow', s.follow], ['plkDrive', s.drive], ['plkBack', s.allow_back]].forEach(function (p) {
      if (document.activeElement !== $(p[0])) $(p[0]).checked = !!p[1];
    });
    const sel = $('plkSt');
    $('plkStField').hidden = st.service_types.length < 2;
    if (document.activeElement !== sel) {
      sel.innerHTML = '';
      const auto = h('option', '', 'The first with a plan today');
      auto.value = '';
      sel.appendChild(auto);
      st.service_types.forEach(function (t) { const o = h('option', '', t.name); o.value = t.id; sel.appendChild(o); });
      sel.value = s.service_type || '';
      sel.disabled = !can('propresenter.manage');
    }
    const box = $('plkStatus');
    box.innerHTML = '';
    if (!st.pc_connected) box.appendChild(kv('Planning Center', 'not connected', 'err'));
    if (!st.pp_connected) box.appendChild(kv('ProPresenter', 'not connected', 'err'));
    if (st.plan) box.appendChild(kv('Plan', (st.plan.title || 'Untitled') + (st.plan.dates ? ', ' + st.plan.dates : '')));
    else if (st.pc_connected) box.appendChild(kv('Plan', 'none followed for today'));
    const d = st.drive;
    const bad = ['nobody', 'someone_else', 'behind', 'not_in_plan', 'overshot', 'too_far', 'lost_control', 'unknown_item', 'no_plan',
                 'not_connected', 'unreachable', 'refused', 'no_match', 'unread', 'unavailable', 'pp_down'].indexOf(d.code) !== -1;
    box.appendChild(kv('ProPresenter moving Planning Center', d.text, bad ? 'err' : ''));
    const f = st.follow;
    if (f) {
      box.appendChild(kv('ProPresenter has up', f.pp ? f.pp.name + (f.pp_item ? ', which is "' + f.pp_item.title + '" in the plan' : ', which is not in the plan') : 'nothing'));
      box.appendChild(kv('Planning Center is on', f.pc_item ? f.pc_item.title + (f.controller ? ' (LIVE held by ' + f.controller + ')' : '') : 'no item'));
      if (f.pp && f.pc_item && f.agree === false) box.appendChild(kv('They disagree', 'ProPresenter and Planning Center are not on the same item.', 'err'));
      else if (f.agree) box.appendChild(kv('They agree', 'Both are on the same item.', 'ok'));
    }
    if (d.log && d.log.length) {
      box.appendChild(h('div', 'note', 'Recent moves'));
      d.log.slice(0, 5).forEach(function (r) {
        box.appendChild(kv(new Date(r.at * 1000).toLocaleTimeString(), r.text, r.result === 'advanced' || r.result === 'went_back' || r.result === 'already' ? '' : 'err'));
      });
    }
    const ld = st.loaded;
    $('plkMatch').disabled = busy || !st.pp_connected;
    $('plkBuild').disabled = busy || !st.pp_connected || st.view_only || !st.plan || !(st.counts.matched > 0);
    $('plkBuildNote').textContent = st.view_only
      ? 'View-only is on, so Relay will not create or change a playlist.'
      : (st.plan ? 'Makes (or remakes) one playlist named "' + st.playlist_name + '" in the plan\'s order. Relay only ever changes a playlist it made itself; your own playlists are never touched.' : '');
    renderTable(st, ld);
  }

  function renderTable(st, ld) {
    const t = $('plkTable');
    t.innerHTML = '';
    if (!ld.at) {
      const r = t.insertRow();
      r.insertCell().textContent = ld.error || 'Not matched yet. Turn on Follow the plan, or press Match the plan now.';
      return;
    }
    const head = t.createTHead().insertRow();
    ['Plan item', 'Status', 'ProPresenter presentation', 'How', ''].forEach(function (x) { head.appendChild(h('th', '', x)); });
    const body = t.createTBody();
    const c = st.counts || {};
    const sum = body.insertRow();
    const sc = sum.insertCell();
    sc.colSpan = 5;
    sc.textContent = (c.matched || 0) + ' matched, ' + ((c.unmatched || 0) + (c.ambiguous || 0) + (c.placeholder || 0)) + ' need attention. Read '
      + ld.presentations + ' presentations from ' + ld.playlists + ' playlists' + (st.synced_playlist ? '; the synced playlist is "' + st.synced_playlist + '"' : '') + '.'
      + (ld.error ? ' ' + ld.error : '');
    st.rows.forEach(function (r) {
      const tr = body.insertRow();
      if (r.status === 'unmatched' || r.status === 'ambiguous' || r.status === 'placeholder') tr.className = 'bad';
      tr.insertCell().textContent = r.title + (r.current ? '  (live in Planning Center)' : '');
      tr.insertCell().textContent = STATUS[r.status] || r.status;
      tr.insertCell().textContent = r.presentation ? r.presentation.name : (r.options && r.options.length ? 'maybe: ' + r.options.map(function (o) { return o.name; }).join(', ') : '');
      tr.insertCell().textContent = r.status === 'matched' ? (METHOD[r.method] || r.method) + (r.method === 'fuzzy' ? ' (' + Math.round(r.score * 100) + '%)' : '') : r.note;
      const act = tr.insertCell();
      // Pairing is a change to the link: only who holds propresenter.manage sees the buttons.
      if (r.status === 'skipped' || !can('propresenter.manage')) return;
      if (r.method === 'mapping') {
        const b = h('button', '', 'Unpair');
        b.type = 'button';
        b.addEventListener('click', function () { pair(r.title, '', ''); });
        act.appendChild(b);
      } else {
        const b = h('button', '', r.status === 'matched' ? 'Pair other' : 'Pair');
        b.type = 'button';
        b.addEventListener('click', function () { startPair(r.title, act); });
        act.appendChild(b);
      }
    });
  }

  async function loadNames() {
    if (names) return names;
    names = (await call(BASE + '/presentations')).presentations;
    const dl = $('plkNames');
    dl.innerHTML = '';
    names.slice(0, 3000).forEach(function (p) { const o = document.createElement('option'); o.value = p.name; dl.appendChild(o); });
    return names;
  }

  async function startPair(title, cell) {
    try { await loadNames(); } catch (e) { say(e.message, true); return; }
    pairing = title;
    cell.innerHTML = '';
    const inp = h('input');
    inp.setAttribute('list', 'plkNames');
    inp.placeholder = 'Presentation name';
    inp.style.width = '14em';
    inp.setAttribute('aria-label', 'Presentation for ' + title);
    const ok = h('button', 'primary', 'Pair');
    ok.type = 'button';
    ok.addEventListener('click', function () {
      const hit = names.filter(function (p) { return p.name === inp.value; });
      if (hit.length === 0) { say('Pick a presentation name from the list.', true); return; }
      pair(title, hit[0].uuid, hit[0].name);
    });
    cell.appendChild(inp);
    cell.appendChild(ok);
    inp.focus();
  }

  async function pair(title, uuid, name) {
    pairing = null;
    try { render(await post(BASE + '/mapping', { item: title, uuid: uuid, name: name })); say(uuid ? 'Paired.' : 'Pairing removed.'); }
    catch (e) { say(e.message, true); }
  }

  async function poll() {
    if (!page() || page().hidden || busy || pairing) return;
    try { render(await call(BASE)); } catch (e) { /* next tick */ }
  }

  $('plkSave').addEventListener('click', async function () {
    const dwell = parseFloat($('plkDwell').value);
    if (!(dwell >= 1 && dwell <= 300)) { say('The time is a number of seconds from 1 to 300.', true); return; }
    try {
      await post('/api/admin/propresenter', { link: { follow: $('plkFollow').checked, drive: $('plkDrive').checked,
        allow_back: $('plkBack').checked, dwell_s: dwell, service_type: $('plkSt').value } });
      filled = false;
      render(await call(BASE));
      say('Saved.');
    } catch (e) { say(e.message, true); }
  });

  $('plkMatch').addEventListener('click', async function () {
    busy = true;
    $('plkMatch').disabled = true;
    say('Reading ProPresenter...');
    try { names = null; render(await post(BASE + '/refresh')); say('Matched.'); }
    catch (e) { say(e.message, true); }
    busy = false;
  });

  $('plkBuild').addEventListener('click', async function () {
    if (!last || !window.confirm('Make or remake the playlist "' + last.playlist_name + '" in ProPresenter? Only that playlist, which Relay owns, is changed.')) return;
    busy = true;
    $('plkBuild').disabled = true;
    say('Building the playlist...');
    try {
      const r = await post(BASE + '/playlist');
      render(r.state);
      say('Playlist "' + r.playlist.name + '": ' + r.presentations + ' presentations' + (r.left_out.length ? '; left out: ' + r.left_out.join(', ') : '')
        + (r.verified ? '. Read back and it matches.' : '. ProPresenter did not give back the same list: check it in ProPresenter.'), !r.verified);
    } catch (e) { say(e.message, true); }
    busy = false;
  });

  window.addEventListener('hashchange', poll);
  timer = setInterval(poll, 2500);
  setTimeout(poll, 600);
})();
