/* The REAPER admin page (#413): each REAPER computer's recording state, transport, markers, actions, tracks (arm,
   mute, level) and its settings. Loaded by admin.html only while the module is on. Built from the admin page's own
   pieces (section, .pcard, dl, .spill, .meter, details.sect) so it looks like the rest of Relay. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-reaper')) return;
  const $ = R.$;
  (function () {
    const s = document.createElement('style');
    s.textContent = [
      '#page-reaper .rpcards{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px;align-items:start}',
      '@media (max-width:900px){#page-reaper .rpcards{grid-template-columns:minmax(0,1fr)}}',
      '#page-reaper .pcard h4{margin:0 0 8px;font-size:13px;font-weight:600}',
      '#page-reaper .pcard .phead .spill{padding:3px 9px;font-size:11px}',
      '#page-reaper .spill .dot{border-radius:50%;display:inline-block;background:var(--dim)}',
      '#page-reaper .spill.idle{color:var(--dim)}',
      '#page-reaper .rpbtns{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px;align-items:center}',
      '#page-reaper .rpbtns input{width:auto;flex:1 1 140px;min-width:0}',
      '#page-reaper .rptrack{display:grid;grid-template-columns:minmax(0,120px) minmax(40px,1fr) auto auto;gap:8px;align-items:center;margin:4px 0}',
      '#page-reaper .rptrack .rpname{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
      '#page-reaper .rpmeter{height:8px;background:#1d1f24;border-radius:4px;overflow:hidden}',
      '#page-reaper .rpmeter i{display:block;height:100%;background:var(--ok)}',
      '#page-reaper .rptrack button{width:auto;padding:3px 10px;font-size:12px;min-width:64px}',
      '#page-reaper .rptrack button.on{background:var(--err);color:#fff}',
      '#page-reaper .rpmachine > h3 .count{font-weight:400;font-size:13px;color:var(--dim);margin-left:6px}',
      '#page-reaper details.sect{margin-top:12px}',
      '#page-reaper details.sect h4{font-size:13px;font-weight:600;margin:18px 0 8px}',
      '#page-reaper .rpprobs{margin:8px 0 0;padding-left:18px}',
    ].join('');
    document.head.appendChild(s);
  })();
  function can(perm) { return typeof window.relayCan !== 'function' || !!window.relayCan(perm); }
  function showError(text) { $('rpErr').textContent = text || ''; $('rpErr').hidden = !text; }
  let data = null;
  let machines = [];
  let dirty = false;
  let shownSettings = '';
  const HEALTH = { ok: 'Healthy', warn: 'Needs a look', error: 'Trouble', idle: 'Standing by' };

  async function send(method, path, body) {
    const res = await fetch(path, { method: method, headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), credentials: 'same-origin' });
    const out = await res.json().catch(function () { return {}; });
    if (!res.ok) throw new Error(out.error || out.detail || 'That did not work.');
    return out;
  }
  function el(tag, text, cls) {
    const e = document.createElement(tag);
    if (text) e.textContent = text;
    if (cls) e.className = cls;
    return e;
  }
  function button(text, fn, cls) {
    const b = el('button', text, cls);
    b.type = 'button';
    b.onclick = async function () {
      showError(''); b.disabled = true;
      try { await fn(); } catch (e) { showError(e.message); }
      b.disabled = false;
    };
    return b;
  }
  function field(label, control) {
    const f = el('div', '', 'field');
    const l = el('label', label);
    if (control.id) l.htmlFor = control.id;
    f.appendChild(l); f.appendChild(control);
    return f;
  }
  let uid = 0;
  function input(m, key, attrs) {
    const i = el('input');
    i.id = 'rpF' + (++uid);
    Object.keys(attrs || {}).forEach(function (k) { i.setAttribute(k, attrs[k]); });
    if (i.type === 'checkbox') {
      i.checked = !!m[key];
      i.onchange = function () { m[key] = i.checked; dirty = true; };
    } else {
      i.value = m[key] === undefined || m[key] === null ? '' : m[key];
      i.oninput = function () { m[key] = i.type === 'number' ? parseInt(i.value || '0', 10) : i.value; dirty = true; };
    }
    return i;
  }
  function check(m, key, label) {
    const l = el('label', '', 'check');
    l.appendChild(input(m, key, { type: 'checkbox' }));
    l.appendChild(document.createTextNode(label));
    return l;
  }
  function select(options, value, onchange) {
    const s = el('select');
    s.id = 'rpF' + (++uid);
    options.forEach(function (o) {
      const opt = el('option', o.label); opt.value = o.id; opt.selected = o.id === value; s.appendChild(opt);
    });
    s.onchange = function () { onchange(s.value); dirty = true; };
    return s;
  }
  function clock(s) {
    s = Math.max(0, Math.floor(s));
    const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, x = s % 60;
    return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(x).padStart(2, '0');
  }
  function pill(text, cls) {
    const p = el('span', '', 'spill ' + cls);
    p.appendChild(el('span', '', 'dot'));
    p.appendChild(document.createTextNode(text));
    return p;
  }
  function live(id) { return (((data && data.machines) || []).filter(function (x) { return x.id === id; })[0] || {}).state; }
  function act(path, body) { return send('POST', '/api/admin/reaper/' + path, body).then(refresh); }

  // ------------------------------------------------------------------------------------------ the live part
  function liveBox(m) {
    const box = el('div', '', 'rpcards rplive');
    const st = live(m.id);
    const card = el('div', '', 'pcard mcard rpstatus');
    const head = el('div', '', 'phead');
    if (!st) {
      card.appendChild(el('p', 'Save this computer to connect to it.', 'note'));
      box.appendChild(card);
      return box;
    }
    const since = st.record_since ? (data.now - st.record_since) : 0;
    head.appendChild(pill(st.recording ? 'REC ' + clock(since) : (st.playing ? 'Playing' : 'Stopped'), st.recording ? 'off' : 'idle'));
    if (st.auto) head.appendChild(pill('Auto', 'idle'));
    head.appendChild(el('span', HEALTH[st.health] || st.health, 'pstate mhealth ' + st.health));
    card.appendChild(head);
    if (st.health !== 'ok') card.appendChild(el('p', st.detail, 'note'));
    const dl = el('dl');
    function row(name, value) { dl.appendChild(el('dt', name)); dl.appendChild(el('dd', value)); }
    row('Position', clock(st.time));
    row('Last marker', st.marker || 'None');
    const armed = st.tracks.filter(function (t) { return t.armed; }).length;
    row('Tracks', st.tracks.length + ' · ' + armed + ' armed');
    row('Auto-record', (m.follow_stream || m.follow_schedule)
      ? [m.follow_stream ? 'with the stream' : '', m.follow_schedule ? 'in schedule windows' : ''].filter(Boolean).join(' and ')
        + (st.wanted ? ' (now)' : '')
      : 'Off');
    card.appendChild(dl);
    if (can('reaper.control') && st.reached) {
      const b = el('div', '', 'rpbtns');
      b.appendChild(button('Record', function () { return act('record', { machine: m.id }); }, st.recording ? 'danger' : 'primary'));
      b.appendChild(button('Stop', function () { return act('stop', { machine: m.id }); }));
      card.appendChild(b);
      const mk = el('div', '', 'rpbtns');
      const name = el('input'); name.placeholder = 'Marker name'; name.maxLength = 60; name.setAttribute('aria-label', 'Marker name');
      mk.appendChild(name);
      mk.appendChild(button('Add marker', async function () { await act('marker', { machine: m.id, name: name.value }); name.value = ''; }));
      card.appendChild(mk);
      const ac = el('div', '', 'rpbtns');
      const num = el('input'); num.placeholder = 'Action number, like 40157'; num.inputMode = 'numeric'; num.setAttribute('aria-label', 'REAPER action number');
      ac.appendChild(num);
      ac.appendChild(button('Run action', function () { return act('action', { machine: m.id, action: num.value.trim() }); }));
      card.appendChild(ac);
    }
    box.appendChild(card);

    const tc = el('div', '', 'pcard mcard rptracks');
    tc.appendChild(el('h4', 'Tracks'));
    if (!st.tracks.length) tc.appendChild(el('p', st.reached ? 'REAPER reports no tracks.' : 'Waiting for REAPER.', 'note'));
    st.tracks.forEach(function (t) {
      const r = el('div', '', 'rptrack');
      r.dataset.track = t.n;
      r.appendChild(el('span', t.n + '  ' + t.name, 'rpname'));
      const meter = el('div', '', 'rpmeter'); const bar = el('i'); bar.style.width = Math.round(t.vu * 100) + '%';
      meter.appendChild(bar); r.appendChild(meter);
      if (can('reaper.control')) {
        r.appendChild(button(t.armed ? 'Armed' : 'Arm', function () { return act('track', { machine: m.id, track: t.n, arm: !t.armed }); }, t.armed ? 'on' : ''));
        r.appendChild(button(t.muted ? 'Muted' : 'Mute', function () { return act('track', { machine: m.id, track: t.n, mute: !t.muted }); }, t.muted ? 'on' : ''));
      } else {
        r.appendChild(el('span', (t.armed ? 'armed' : '') + (t.muted ? ' muted' : ''), 'note'));
      }
      tc.appendChild(r);
    });
    box.appendChild(tc);
    return box;
  }

  function settings(m, i) {
    const set = el('details', '', 'sect');
    set.open = !m.id;
    set.appendChild(el('summary', 'Settings'));
    const row = el('div', '', 'row');
    row.appendChild(field('Name', input(m, 'name', { maxlength: 24, placeholder: 'Record PC' })));
    row.appendChild(field('Address', input(m, 'host', { maxlength: 253, placeholder: '127.0.0.1', autocomplete: 'off', spellcheck: 'false' })));
    set.appendChild(row);
    const row2 = el('div', '', 'row');
    row2.appendChild(field('REAPER listens on (local listen port)', input(m, 'port', { type: 'number', min: 1, max: 65535 })));
    row2.appendChild(field('Relay listens on (REAPER\'s device port)', input(m, 'listen_port', { type: 'number', min: 1024, max: 65535 })));
    row2.appendChild(field('Tracks to follow (1 to 64; more makes REAPER slower to answer)', input(m, 'tracks', { type: 'number', min: 1, max: 64 })));
    set.appendChild(row2);

    set.appendChild(el('h4', 'Auto-record'));
    set.appendChild(check(m, 'follow_stream', 'Record while the stream is on air (OBS, YouTube and other stream sources)'));
    set.appendChild(check(m, 'follow_schedule', 'Record during schedule windows'));
    const d = el('div', '', 'row');
    d.appendChild(field('Stop this many seconds after it ends', input(m, 'stop_delay', { type: 'number', min: 0, max: 3600 })));
    set.appendChild(d);

    set.appendChild(el('h4', 'Markers'));
    set.appendChild(check(m, 'song_markers', 'Add a marker for each new ProPresenter presentation, named after it'));
    set.appendChild(check(m, 'message_markers', 'Add a marker for each stage message'));

    set.appendChild(el('h4', 'Checks'));
    set.appendChild(check(m, 'check_armed', 'Warn when no track is armed'));
    const ex = el('div', '', 'row');
    const exp = el('input'); exp.id = 'rpF' + (++uid); exp.placeholder = 'Pastor, Band, Room';
    exp.value = (m.expect || []).join(', ');
    exp.oninput = function () { m.expect = exp.value.split(',').map(function (x) { return x.trim(); }).filter(Boolean); dirty = true; };
    ex.appendChild(field('Tracks that must be armed (names, comma separated)', exp));
    ex.appendChild(field('Warn when an armed track is silent for this many seconds while recording (0: never)', input(m, 'silent_s', { type: 'number', min: 0, max: 600 })));
    set.appendChild(ex);

    set.appendChild(el('h4', 'Alerts'));
    const buttons = [{ id: '', label: 'None' }].concat((data.alert_buttons || []).map(function (b) { return { id: b.id, label: b.name }; }));
    const al = el('div', '', 'row');
    al.appendChild(field('When this REAPER is in trouble, raise', select(buttons, m.alert_error, function (v) { m.alert_error = v; })));
    al.appendChild(field('When it needs a look, raise', select(buttons, m.alert_warn, function (v) { m.alert_warn = v; })));
    set.appendChild(al);
    if (!(data.alert_buttons || []).length) set.appendChild(el('p', 'Add alert buttons on the Alerts page to choose them here.', 'note'));

    set.appendChild(el('h4', 'This computer'));
    const ctl = el('div', '', 'ctl');
    const status = el('div', '', 'note');
    ctl.appendChild(button('Test connection', async function () {
      const r = await send('POST', '/api/admin/reaper/test', { machine: m.id });
      status.textContent = r.detail;
    }));
    ctl.appendChild(button('Remove computer', async function () { machines.splice(i, 1); dirty = true; render(); }, 'danger'));
    set.appendChild(ctl);
    set.appendChild(status);
    set.appendChild(el('p', 'Changes take effect when you press Save at the bottom of the page.', 'note'));
    return set;
  }

  function card(m, i) {
    const c = el('section', '', 'rpmachine');
    const h = el('h3', m.name || 'REAPER');
    if (m.host) h.appendChild(el('span', m.host + ':' + m.port, 'count'));
    c.appendChild(h);
    c.appendChild(liveBox(m));
    if (can('reaper.manage')) c.appendChild(settings(m, i));
    return c;
  }

  function render() {
    const open = {};
    document.querySelectorAll('#rpMachines details.sect').forEach(function (d, i) { open[i] = d.open; });
    const box = $('rpMachines');
    box.textContent = '';
    machines.forEach(function (m, i) {
      const c = card(m, i);
      const d = c.querySelector('details.sect');
      if (d && open[i] !== undefined) d.open = open[i];
      box.appendChild(c);
    });
    if (!machines.length) {
      const s = el('section');
      s.appendChild(el('p', 'No REAPER computer yet. Add the computer that records.', 'note'));
      box.appendChild(s);
    }
    $('rpAdd').hidden = $('rpSave').hidden = !can('reaper.manage');
    $('rpAdd').disabled = machines.length >= 4;
  }

  // The live part redraws without touching the settings form, which may be half typed. It is rebuilt only when what
  // its buttons show changed, so a click is never lost to a redraw; otherwise the meters and clock move in place.
  function redrawLive() {
    document.querySelectorAll('#rpMachines .rpmachine').forEach(function (c, i) {
      const m = machines[i];
      if (!m) return;
      const st = live(m.id) || {};
      const sig = JSON.stringify([st.reached, st.recording, st.playing, st.health, st.detail, st.marker, st.auto, st.wanted,
        (st.tracks || []).map(function (t) { return [t.n, t.name, t.armed, t.muted]; })]);
      const old = c.querySelector('.rplive');
      if (old && old.dataset.sig === sig) {
        (st.tracks || []).forEach(function (t) {
          const bar = old.querySelector('.rptracks .rptrack[data-track="' + t.n + '"] .rpmeter i');
          if (bar) bar.style.width = Math.round(t.vu * 100) + '%';
        });
        const fresh = liveBox(m);
        const a = old.querySelector('.rpstatus .phead'), b = fresh.querySelector('.rpstatus .phead');
        if (a && b) a.replaceWith(b);
        const da = old.querySelector('.rpstatus dl'), db = fresh.querySelector('.rpstatus dl');
        if (da && db) da.replaceWith(db);
        return;
      }
      const fresh = liveBox(m);
      fresh.dataset.sig = sig;
      if (old) old.replaceWith(fresh); else c.insertBefore(fresh, c.children[1] || null);
    });
  }

  async function refresh() {
    try {
      data = await send('GET', '/api/admin/reaper');
    } catch (e) { showError(e.message); return; }
    const sts = data.machines.map(function (m) { return m.state; }).filter(Boolean);
    const worst = ['error', 'warn', 'ok', 'idle'].filter(function (h) { return sts.some(function (s) { return s.health === h; }); })[0];
    const sum = $('rpSum');
    sum.textContent = '';
    if (sts.some(function (s) { return s.recording; })) sum.appendChild(el('span', 'recording', 'live'));
    else if (worst) sum.textContent = (HEALTH[worst] || worst).toLowerCase();
    const settingsSig = JSON.stringify(data.machines.map(function (m) { const x = Object.assign({}, m); delete x.state; return x; }));
    if (!dirty && settingsSig !== shownSettings) {
      shownSettings = settingsSig;
      machines = data.machines.map(function (m) { const x = JSON.parse(JSON.stringify(m)); delete x.state; return x; });
      render();
    }
    redrawLive();
  }

  $('rpAdd').onclick = function () {
    const used = machines.map(function (m) { return m.listen_port; });
    let lp = 9001; while (used.indexOf(lp) >= 0) lp++;
    machines.push({ id: '', name: 'REAPER', host: '127.0.0.1', port: 8000, listen_port: lp, tracks: 16, follow_stream: false,
                    follow_schedule: false, stop_delay: 60, song_markers: true, message_markers: false,
                    check_armed: true, expect: [], silent_s: 30, alert_error: '', alert_warn: '' });
    dirty = true; render();
  };
  $('rpSave').onclick = async function () {
    showError('');
    try {
      data = await send('PUT', '/api/admin/reaper/settings', { machines: machines.map(function (m) {
        return { id: m.id, name: m.name, host: m.host, port: m.port, listen_port: m.listen_port, tracks: m.tracks || 16,
                 follow_stream: !!m.follow_stream, follow_schedule: !!m.follow_schedule, stop_delay: m.stop_delay || 0,
                 song_markers: !!m.song_markers, message_markers: !!m.message_markers, check_armed: !!m.check_armed,
                 expect: m.expect || [], silent_s: m.silent_s || 0,
                 alert_error: m.alert_error || '', alert_warn: m.alert_warn || '' };
      }) });
      dirty = false;
      shownSettings = '';
      refresh();
    } catch (e) { showError(e.message); }
  };

  function visible() { return !document.hidden && document.getElementById('page-reaper').offsetParent !== null; }
  setInterval(function () { if (visible()) refresh(); }, 1000);
  refresh();
})();
