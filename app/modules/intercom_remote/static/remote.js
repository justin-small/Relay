/* The Remote crew admin page (#277): who may connect from outside, the link to Cloudflare, recent remote connections.
   Loaded by admin.html only while the module is on, on every admin page, so each lookup is guarded. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-intercom-remote')) return;
  const $ = R.$, api = R.api, post = R.post;
  const can = relayUI.can, el = relayUI.el, say = relayUI.say;
  let last = {};

  const STATE = { connected: 'ok', connecting: '', reconnecting: 'err', missing: 'err', off: '' };
  let timer = null;

  function showError(text) { $('irErr').textContent = text || ''; $('irErr').hidden = !text; }

  function cell(text) { const td = document.createElement('td'); td.textContent = text == null ? '' : String(text); return td; }

  function render(v) {
    last = v;
    renderFeeds(v);
    renderMixer(v);
    const b = v.bridge || {};
    $('irSum').textContent = b.state === 'connected' ? 'live' : (b.state === 'off' ? '' : 'not connected');
    $('irState').textContent = b.detail || '';
    $('irState').className = 'note ' + (STATE[b.state] || '');
    const manage = can('intercom_remote.manage');
    const body = $('irPeople').tBodies[0];
    body.textContent = '';
    (v.people || []).forEach(function (p) {
      const tr = document.createElement('tr');
      tr.appendChild(cell(p.name || p.username));
      tr.appendChild(cell(p.role));
      tr.appendChild(cell(p.phones));
      const td = document.createElement('td');
      if (manage) {
        const allow = document.createElement('button');
        allow.type = 'button';
        allow.textContent = p.allowed ? 'Remove remote access' : 'Allow remote';
        allow.addEventListener('click', async function () {
          showError('');
          try { await post('/api/admin/intercom-remote/users/' + p.id + '/allow', { allowed: !p.allowed }); refresh(); }
          catch (e) { showError(e.message); }
        });
        td.appendChild(allow);
        if (p.phones) {
          const cut = document.createElement('button');
          cut.type = 'button';
          cut.textContent = 'Sign out remote phones';
          cut.addEventListener('click', async function () {
            showError('');
            try { await post('/api/admin/intercom-remote/users/' + p.id + '/cut', {}); refresh(); }
            catch (e) { showError(e.message); }
          });
          td.appendChild(cut);
        }
      }
      tr.appendChild(td);
      body.appendChild(tr);
    });
    const sb = $('irSessions').tBodies[0];
    sb.textContent = '';
    (v.sessions || []).forEach(function (s) {
      const tr = document.createElement('tr');
      [s.user_name, s.started_at, s.ended_at || 'now', s.end_reason || ''].forEach(function (t) { tr.appendChild(cell(t)); });
      sb.appendChild(tr);
    });
  }

  const channelName = function (v, id) {
    const c = (v.channels || []).find(function (x) { return x.id === id; });
    return c ? c.name : 'Channel ' + id;
  };
  const laneName = function (v, n) {
    const l = (v.main_lanes || []).find(function (x) { return x.lane === n; });
    return 'Lane ' + n + (l && l.name ? ' (' + l.name + ')' : '');
  };

  function feedState(f) {
    if (f.state === 'carried') return f.carried + ' carried, ' + f.refused + ' refused, ' + f.dropped + ' dropped';
    if (f.state === 'refused') return 'Refused: all 32 mixer lanes are in use, so this feed is left out.';
    return 'Waiting for a remote phone to listen to this channel';
  }

  async function saveFeeds(list) {
    const v = await relayUI.api('PUT', '/api/admin/intercom-remote/feeds', { feeds: list });
    render(Object.assign({}, last, v));
  }

  function select(items, value) {
    const s = el('select');
    items.forEach(function (i) { s.appendChild(el('option', { value: i[0], text: i[1] })); });
    s.value = String(value);
    return s;
  }

  function renderFeeds(v) {
    const manage = can('intercom_remote.manage');
    const feeds = v.feeds || [];
    const body = $('irFeeds').tBodies[0];
    body.textContent = '';
    feeds.forEach(function (f) {
      const tr = el('tr');
      tr.appendChild(cell(channelName(v, f.channel)));
      tr.appendChild(cell(laneName(v, f.lane)));
      const gain = el('input', { type: 'number', min: -60, max: 12, step: 0.5, value: f.gain_db, disabled: !manage });
      gain.setAttribute('aria-label', 'Gain in dB');
      gain.addEventListener('change', function () {
        saveFeeds(feeds.map(function (g) { return g === f ? Object.assign({}, g, { gain_db: Number(gain.value) }) : g; }))
          .then(function () { say('irFeedMsg', ''); }, function (e) { say('irFeedMsg', e.message, true, { hide: true }); });
      });
      const tdGain = el('td'); tdGain.appendChild(gain); tr.appendChild(tdGain);
      const st = cell(feedState(f));
      if (f.state === 'refused') st.className = 'err';
      tr.appendChild(st);
      const tdX = el('td');
      if (manage) {
        tdX.appendChild(relayUI.button('Remove', function () {
          return saveFeeds(feeds.filter(function (g) { return g !== f; }));
        }, { cls: 'sm', error: function (m) { say('irFeedMsg', m, !!m, { hide: true }); } }));
      }
      tr.appendChild(tdX);
      body.appendChild(tr);
    });
    const add = $('irFeedAdd');
    add.textContent = '';
    if (!manage) return;
    if (!(v.channels || []).length) { add.appendChild(el('div', { className: 'note', text: 'Make an intercom channel first.' })); return; }
    const ch = select((v.channels || []).map(function (c) { return [c.id, c.name]; }), v.channels[0].id);
    const lane = select((v.main_lanes || []).map(function (l) { return [l.lane, laneName(v, l.lane)]; }), 1);
    const gain = el('input', { type: 'number', min: -60, max: 12, step: 0.5, value: 0 });
    add.appendChild(relayUI.field('Channel', ch));
    add.appendChild(relayUI.field('Main-bus lane', lane));
    add.appendChild(relayUI.field('Gain (dB)', gain));
    add.appendChild(relayUI.button('Add feed', function () {
      return saveFeeds(feeds.concat([{ channel: Number(ch.value), lane: Number(lane.value), gain_db: Number(gain.value) }]));
    }, { cls: 'primary', error: function (m) { say('irFeedMsg', m, !!m, { hide: true }); } }));
  }

  function renderMixer(v) {
    const m = v.mixer || { lanes_used: 0, lanes_total: 32, outputs: [] };
    const refused = (v.feeds || []).filter(function (f) { return f.state === 'refused'; }).length;
    $('irMixer').textContent = m.lanes_used + ' of ' + m.lanes_total + ' mixer lanes in use.' +
      (refused ? ' ' + refused + ' feed' + (refused === 1 ? ' was' : 's were') + ' refused because no lane was left.' : '');
    $('irMixer').className = 'note ' + (refused ? 'err' : '');
    const body = $('irMixOut').tBodies[0];
    body.textContent = '';
    (m.outputs || []).forEach(function (o) {
      const tr = el('tr');
      [channelName(v, o.channel), o.underruns, o.resyncs, o.queued_ms].forEach(function (t) { tr.appendChild(cell(t)); });
      body.appendChild(tr);
    });
    if (!(m.outputs || []).length) {
      const tr = el('tr'); const td = cell('No remote phone is listening, so no channel is being mixed.'); td.colSpan = 4;
      tr.appendChild(td); body.appendChild(tr);
    }
  }

  async function refresh() {
    try { render(await api('/api/admin/intercom-remote')); } catch (e) { /* the admin page reports lost sessions */ }
  }

  function visible() { return !document.getElementById('page-intercom-remote').closest('.page').hidden; }
  function tick() { if (visible()) refresh(); timer = setTimeout(tick, 5000); }

  document.addEventListener('relay:config', function () { refresh(); if (!timer) tick(); });
})();
