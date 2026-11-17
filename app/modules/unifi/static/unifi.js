/* The UniFi admin page (#358): the console connection, internet health and its history, the gear and its ports,
   Wi-Fi, NDI and Dante, who is on the network, the console's change log and speed tests. Loaded by admin.html only
   while the module is on. Built from the panel's own pieces; the rules below only lay them out. Every value from the
   console goes on the page as text. The live parts hold no buttons, so a redraw never eats a click. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-unifi')) return;
  const $ = R.$;

  let last = null;
  let filled = false;
  let hours = 24;
  let histAt = 0;
  const can = relayUI.can;
  function showError(text) { $('ufErr').textContent = text || ''; $('ufErr').hidden = !text; }
  const send = relayUI.api;
  const el = relayUI.el;
  function td(text, cls) { return el('td', cls, text === undefined || text === null || text === '' ? '—' : String(text)); }
  function ago(ts) {
    if (!ts) return '';
    const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
    if (s < 60) return s + ' s ago';
    if (s < 3600) return Math.round(s / 60) + ' min ago';
    if (s < 86400) return new Date(ts * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    return new Date(ts * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' +
      new Date(ts * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  }
  function rate(bps) {
    if (bps === null || bps === undefined) return '—';
    if (bps >= 1e9) return (bps / 1e9).toFixed(2) + ' Gb/s';
    if (bps >= 1e6) return (bps / 1e6).toFixed(1) + ' Mb/s';
    return Math.round(bps / 1e3) + ' kb/s';
  }
  function num(v, unit) { return v === null || v === undefined ? '—' : Math.round(v * 10) / 10 + (unit || ''); }
  function label(c) { return c.name || c.hostname || c.mac; }
  function dl(rows) {
    const d = el('dl');
    rows.forEach(function (r) { if (!r) return; d.appendChild(el('dt', '', r[0])); d.appendChild(el('dd', '', r[1] || '—')); });
    return d;
  }
  function head(card, title, health, state) {
    const h = el('div', 'phead');
    h.appendChild(el('h4', '', title));
    if (state) h.appendChild(el('span', 'pstate mhealth ' + health, state));
    card.appendChild(h);
  }
  function rows(id, items, make) {
    const tb = $(id).querySelector('tbody');
    tb.replaceChildren();
    items.forEach(function (x) { tb.appendChild(make(x)); });
  }

  // ------------------------------------------------------------------------------- connection and internet
  function drawConn(d) {
    const c = $('ufConn'); c.replaceChildren();
    const s = d.settings;
    head(c, 'Console', d.level, { ok: 'Connected', warn: 'Needs a look', error: 'Not connected', idle: 'Connecting' }[d.level]);
    const st = d.stream || {};
    c.appendChild(dl([
      ['Console', s.host + (s.port !== 443 ? ':' + s.port : '')],
      ['Site', d.site && d.site.name],
      ['Read', (d.api === 'v2' ? 'API key' : d.api ? 'API key (' + d.api + ')' : '') + (d.checked_at ? ' · ' + ago(d.checked_at) : '')],
      ['Live events', st.state === 'live' ? 'On, as the local user · ' + st.events + ' client events'
        : st.state === 'error' ? st.detail : s.username ? 'Waiting to sign in' : 'Off (add a local user in Settings)'],
      ['Clients', String(d.clients.length)],
    ]));
  }
  function drawNet(d) {
    const c = $('ufNet'); c.replaceChildren();
    const n = d.internet;
    const prob = d.part_problems.internet;
    if (!n) { head(c, 'Internet', 'idle', prob ? 'Not read' : 'Reading…'); if (prob) c.appendChild(el('p', 'note', prob)); return; }
    const p = n.probe || {}, gw = p.gateway || {}, ext = p.outside || {};
    const bad = n.status !== 'ok' || (ext.loss || 0) >= 34 || (n.latency_ms || 0) >= 150;
    head(c, 'Internet', bad ? 'error' : 'ok', n.status === 'ok' ? (bad ? 'Slow' : 'Up') : 'Down');
    const sp = n.speedtest || {};
    c.appendChild(dl([
      ['Now', rate(n.down_bps) + ' down · ' + rate(n.up_bps) + ' up'],
      ['Latency', num(n.latency_ms, ' ms') + (n.drops !== null ? ' · ' + n.drops + ' dropped' : '')],
      ['Available', num(n.availability, '%') + ' (the console’s checks to ' + n.monitors.map(function (m) { return m.target; }).join(', ') + ')'],
      ['Relay to the gateway', num(gw.ms, ' ms') + ' · ' + num(gw.loss, '% lost')],
      ['Relay to ' + (ext.target || 'outside'), num(ext.ms, ' ms') + ' · ' + num(ext.loss, '% lost')],
      n.isp ? ['Provider', n.isp] : null,
      sp.down_mbps ? ['Last speed test', sp.down_mbps + ' / ' + sp.up_mbps + ' Mb/s · ' + ago(sp.at)] : null,
    ]));
  }
  function drawWarn(d) {
    const w = $('ufWarn'); w.replaceChildren();
    w.hidden = !d.warnings.length;
    if (!d.warnings.length) return;
    w.appendChild(el('b', '', d.warnings.length === 1 ? 'One thing to check' : d.warnings.length + ' things to check'));
    const ul = el('ul');
    d.warnings.forEach(function (t) { ul.appendChild(el('li', '', t)); });
    w.appendChild(ul);
  }

  // ------------------------------------------------------------------------------- history charts
  const NS = 'http://www.w3.org/2000/svg';
  function chart(rowsIn, series, unitText, fmt) {
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('class', 'ufchart'); svg.setAttribute('viewBox', '0 0 600 90'); svg.setAttribute('preserveAspectRatio', 'none');
    const t0 = rowsIn.length ? rowsIn[0].ts : 0, t1 = rowsIn.length ? rowsIn[rowsIn.length - 1].ts : 1;
    let top = 0;
    series.forEach(function (s) { rowsIn.forEach(function (r) { if (r[s.key] !== null && r[s.key] > top) top = r[s.key]; }); });
    top = top * 1.1 || 1;
    series.forEach(function (s) {
      let dpath = '', gap = true;
      rowsIn.forEach(function (r) {
        if (r[s.key] === null || r[s.key] === undefined) { gap = true; return; }
        const x = (r.ts - t0) / Math.max(1, t1 - t0) * 600, y = 78 - r[s.key] / top * 72;
        dpath += (gap ? 'M' : 'L') + x.toFixed(1) + ' ' + y.toFixed(1);
        gap = false;
      });
      const p = document.createElementNS(NS, 'path');
      p.setAttribute('d', dpath || 'M0 78'); p.setAttribute('fill', 'none'); p.setAttribute('class', s.cls);
      p.setAttribute('stroke-width', '1.5'); p.setAttribute('vector-effect', 'non-scaling-stroke');
      svg.appendChild(p);
    });
    const t = document.createElementNS(NS, 'text'); t.setAttribute('x', 2); t.setAttribute('y', 89);
    t.textContent = 'top ' + fmt(top / 1.1) + unitText;
    svg.appendChild(t);
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', series.map(function (s) { return s.name; }).join(', ') + ' over the last ' + hours + ' hours');
    return svg;
  }
  function legend(items) {
    const p = el('div', 'uflegend');
    items.forEach(function (it) {
      const i = el('i'); i.style.background = it[1]; p.appendChild(i); p.appendChild(document.createTextNode(it[0]));
    });
    return p;
  }
  async function drawHistory(force) {
    if (!force && Date.now() - histAt < 60000) return;
    histAt = Date.now();
    let h;
    try { h = await send('GET', '/api/admin/unifi/history?hours=' + hours); } catch (e) { return; }
    const box = $('ufCharts'); box.replaceChildren();
    if (h.rows.length < 2) { box.appendChild(el('p', 'note', 'Relay keeps a reading every minute from when it first reads the console; the chart draws once there are two.')); $('ufPeaks').textContent = ''; return; }
    box.appendChild(el('h4', '', 'Throughput'));
    box.appendChild(chart(h.rows, [{ key: 'down_bps', cls: 'a', name: 'download' }, { key: 'up_bps', cls: 'b', name: 'upload' }], '', rate));
    box.appendChild(legend([['Download', 'var(--accent)'], ['Upload', 'var(--dim)']]));
    box.appendChild(el('h4', '', 'Latency and loss'));
    box.appendChild(chart(h.rows, [{ key: 'latency_ms', cls: 'a', name: 'latency' }, { key: 'ext_ms', cls: 'b', name: 'Relay check' }], ' ms', function (v) { return Math.round(v); }));
    box.appendChild(chart(h.rows, [{ key: 'ext_loss', cls: 'c', name: 'lost' }, { key: 'gw_loss', cls: 'b', name: 'lost to gateway' }], '% lost', function (v) { return Math.round(v); }));
    box.appendChild(legend([['Console latency', 'var(--accent)'], ['Relay’s check', 'var(--dim)'], ['Lost (outside)', 'var(--err)']]));
    const pk = h.peaks;
    $('ufPeaks').textContent = 'Over this time: busiest download ' + rate(pk.down_bps) + ', upload ' + rate(pk.up_bps) +
      ', worst latency ' + num(pk.latency_ms, ' ms') + ', most lost ' + num(pk.ext_loss, '%') +
      ', lowest availability ' + num(pk.availability, '%') + '.';
  }
  $('ufRange').addEventListener('click', function (e) {
    const b = e.target.closest('button'); if (!b) return;
    hours = +b.dataset.h;
    $('ufRange').querySelectorAll('button').forEach(function (x) { x.classList.toggle('primary', x === b); });
    drawHistory(true);
  });

  // ------------------------------------------------------------------------------- gear, Wi-Fi, NDI and Dante
  function drawGear(d) {
    const box = $('ufGear'); box.replaceChildren();
    box.className = 'ufgear';
    if (d.part_problems.gear) box.appendChild(el('p', 'note err', d.part_problems.gear));
    d.gear.forEach(function (g) {
      const c = el('div', 'pcard mcard');
      head(c, g.name, g.online ? (g.warnings.length ? 'warn' : 'ok') : 'error', g.online ? (g.warnings.length ? 'Needs a look' : 'Online') : 'Offline');
      const up = g.uplink;
      c.appendChild(dl([
        ['Model', g.model + ' · ' + g.kind],
        ['Firmware', g.version + (g.update ? ' · update available' + (g.update !== 'available' ? ': ' + g.update : '') + '. Apply it outside service times.' : ' · current')],
        ['Address', g.ip],
        up && up.to ? ['Uplink', up.to + (up.port ? ' port ' + up.port : '') + (up.speed ? ' · ' + up.speed + ' Mbps' : '')] : null,
        g.poe_w !== null ? ['Power over Ethernet', g.poe_w + ' W' + (g.poe_budget_w ? ' of ' + g.poe_budget_w + ' W' : '')] : null,
      ]));
      if (g.ports.length) {
        const wrap = el('div', 'tablescroll');
        const t = el('table', 'tbl');
        const th = el('thead'); const tr = el('tr');
        ['Port', 'Connected', 'Link', 'PoE', 'Traffic', 'Errors', ''].forEach(function (x) { tr.appendChild(el('th', '', x)); });
        th.appendChild(tr); t.appendChild(th);
        const tb = el('tbody');
        g.ports.forEach(function (p) {
          const r = el('tr');
          r.appendChild(td(p.port + (p.uplink ? ' (uplink)' : '')));
          r.appendChild(td(p.client || p.name));
          r.appendChild(td(p.up ? (p.speed ? p.speed + ' Mbps' : 'up') + (p.duplex === false ? ' half' : '') : 'down', p.warn && p.up ? 'warn' : ''));
          r.appendChild(td(p.poe === null ? '' : p.poe_w ? p.poe_w + ' W' : p.poe ? 'on' : 'off'));
          r.appendChild(td(p.up ? rate(p.bps) + (p.use !== null && p.use >= 0.01 ? ' (' + Math.round(p.use * 100) + '%)' : '') : ''));
          r.appendChild(td(p.errors ? p.errors + (p.new_errors ? ' (+' + p.new_errors + ')' : '') : '0'));
          r.appendChild(td(p.warn, p.warn ? 'warn' : ''));
          tb.appendChild(r);
        });
        t.appendChild(tb); wrap.appendChild(t); c.appendChild(wrap);
      }
      box.appendChild(c);
    });
  }
  function drawWifi(d) {
    const w = d.wifi;
    $('ufWifiSect').hidden = !w;
    if (!w) return;
    rows('ufRadios', w.radios, function (r) {
      const t = el('tr');
      [r.ap, r.band, r.channel, r.clients, num(r.busy_pct, '%'), num(r.retries_pct, '%'), r.neighbours].forEach(function (v) { t.appendChild(td(v)); });
      if (r.warn) t.lastChild.className = 'warn';
      return t;
    });
    $('ufWeak').textContent = w.weak.length ? 'Weakest signals: ' + w.weak.slice(0, 5).map(function (c) {
      return c.name + ' ' + c.signal + ' dBm' + (c.ap ? ' on ' + c.ap : '');
    }).join(', ') + '. Below −70 dBm a phone or tablet may drop out.' + ' The access points hear ' + w.neighbours + ' other networks.' : '';
  }
  function drawAv(d) {
    const a = d.av;
    $('ufAvSect').hidden = !a && !d.part_problems.av;
    if (!a) { $('ufAvNone').textContent = d.part_problems.av || ''; return; }
    const names = { ndi: 'NDI', dante: 'Dante', ptp: 'PTP clock' };
    rows('ufAv', a.devices, function (x) {
      const t = el('tr');
      const c = d.clients.find(function (y) { return y.mac === x.mac; });
      [x.name, x.kinds.map(function (k) { return names[k] || k; }).join(', '), c ? c.where : '', rate(x.bps), x.peers.join(', ')].forEach(function (v) { t.appendChild(td(v)); });
      return t;
    });
    $('ufAvNone').textContent = a.devices.length ? '' : 'No NDI or Dante traffic in the last five minutes (' + a.flows + ' flows read).';
    $('ufAv').hidden = !a.devices.length;
  }

  // ------------------------------------------------------------------------------- clients, events, changes, speed tests
  function drawClients(d) {
    const q = ($('ufFilter').value || '').toLowerCase();
    rows('ufClients', d.clients.filter(function (c) {
      return !q || [c.name, c.hostname, c.ip, c.mac, c.model].some(function (v) { return v && v.toLowerCase().indexOf(q) >= 0; });
    }), function (c) {
      const who = el('td'); who.appendChild(el('div', '', label(c)));
      const tags = [];
      if (c.model) tags.push(c.model);
      if (c.hostname && c.hostname !== c.name) tags.push(c.hostname);
      if (c.guest) tags.push('guest');
      if (c.private) tags.push('private address');
      if (c.signal !== null && c.signal !== undefined) tags.push(c.signal + ' dBm');
      if (c.missing) tags.push('not seen for ' + ago(c.missing).replace(' ago', ''));
      if (tags.length) who.appendChild(el('div', 'muted', tags.join(' · ')));
      const t = el('tr');
      t.append(who, td(c.ip, 'mono'), td(c.mac, 'mono'), td(c.where || (c.wired ? 'wired' : 'Wi-Fi')), td(ago(c.since)));
      return t;
    });
    $('ufCount').textContent = d.clients.length ? '(' + d.clients.length + ')' : '';
  }
  function drawEvents(d) {
    const words = { join: 'Joined', leave: 'Left', roam: 'Moved' };
    rows('ufEvents', d.events, function (e) {
      const t = el('tr');
      const pill = el('td'); pill.appendChild(el('span', 'spill' + (e.kind === 'join' ? ' ok' : ''), words[e.kind] || e.kind));
      t.append(td(ago(e.ts)), pill, td(e.name || e.hostname || e.mac), td(e.where));
      return t;
    });
    $('ufNoEvents').hidden = d.events.length > 0;
  }
  function drawChanges(d) {
    $('ufChangesSect').hidden = !d.changes.length && !d.signins;
    $('ufSignins').textContent = d.signins ? d.signins + ' sign-in' + (d.signins === 1 ? '' : 's') + ' to the console in 30 days (Relay\u2019s own local user counts too).' : '';
    $('ufChanges').hidden = !d.changes.length;
    rows('ufChanges', d.changes, function (c) {
      const t = el('tr');
      t.append(td(ago(c.at)), td(c.who), td(c.text + (c.properties.length ? ' (' + c.properties.join(', ') + ')' : '')));
      return t;
    });
  }
  function drawSpeed(d) {
    $('ufSpeedSect').hidden = !d.speedtests.length;
    rows('ufSpeed', d.speedtests.slice(0, 15), function (s) {
      const t = el('tr');
      t.append(td(ago(s.at)), td(num(s.down_mbps, ' Mb/s')), td(num(s.up_mbps, ' Mb/s')), td(num(s.ping_ms, ' ms')));
      return t;
    });
  }

  // ------------------------------------------------------------------------------- settings
  function fill(s) {
    $('ufHost').value = s.host; $('ufPort').value = s.port; $('ufSite').value = s.site;
    $('ufPoll').value = s.poll_s; $('ufAway').value = s.away_s; $('ufTarget').value = s.probe_target;
    $('ufUser').value = s.username || '';
    $('ufKey').value = ''; $('ufPass').value = '';
    $('ufKey').placeholder = s.api_key_set ? 'Saved. Paste a new key to change it.' : 'Paste the read-only API key';
    $('ufPass').placeholder = s.password_set ? 'Saved. Type a new one to change it.' : '';
    $('ufClearUser').hidden = !s.username;
  }

  function draw(d) {
    last = d;
    const s = d.settings;
    if (!filled) { fill(s); filled = true; }
    const sum = $('ufSum');
    sum.textContent = { ok: 'connected', warn: 'needs a look', error: 'not connected', idle: d.configured ? 'connecting' : 'not set up' }[d.level] || '';
    sum.className = 'sumstate ' + ({ ok: 'ok', warn: 'warn', error: 'err' }[d.level] || '');
    showError(d.problem && !d.untrusted ? d.problem : '');
    $('ufTrust').hidden = !d.untrusted;
    $('ufFp').textContent = d.untrusted || '';
    $('ufTrustBtn').hidden = !can('unifi.manage');
    $('ufFpSaved').hidden = !s.fingerprint;
    $('ufFpSaved').textContent = s.fingerprint ? 'Trusted certificate: ' + s.fingerprint : '';
    $('ufForget').hidden = !s.fingerprint;
    const live = !!d.checked_at && !d.problem;
    ['ufLive', 'ufHist', 'ufGearSect', 'ufClientsSect', 'ufEventsSect'].forEach(function (id) { $(id).hidden = !live; });
    if (!d.configured && !$('ufSettings').open) $('ufSettings').open = true;
    if (!live) { $('ufWifiSect').hidden = true; $('ufAvSect').hidden = true; $('ufChangesSect').hidden = true; $('ufSpeedSect').hidden = true; return; }
    drawConn(d); drawNet(d); drawWarn(d); drawGear(d); drawWifi(d); drawAv(d);
    drawClients(d); drawEvents(d); drawChanges(d); drawSpeed(d);
    drawHistory(false);
  }

  async function refresh() {
    try { draw(await send('GET', '/api/admin/unifi')); } catch (e) { /* keeps what it had */ }
  }
  function busy(btn, fn) {
    btn.onclick = async function () {
      showError(''); btn.disabled = true;
      try { draw(await fn()); } catch (e) { showError(e.message); }
      btn.disabled = false;
    };
  }
  busy($('ufSave'), async function () {
    const body = { host: $('ufHost').value, port: +$('ufPort').value, site: $('ufSite').value,
      poll_s: +$('ufPoll').value, away_s: +$('ufAway').value, probe_target: $('ufTarget').value, username: $('ufUser').value };
    if ($('ufKey').value.trim()) body.api_key = $('ufKey').value;
    if ($('ufPass').value) body.password = $('ufPass').value;
    const d = await send('PUT', '/api/admin/unifi/settings', body);
    fill(d.settings);
    return d;
  });
  busy($('ufCheck'), function () { histAt = 0; return send('POST', '/api/admin/unifi/refresh'); });
  busy($('ufTrustBtn'), function () { return send('POST', '/api/admin/unifi/trust', { fingerprint: last ? last.untrusted : '' }); });
  busy($('ufForget'), function () { return send('DELETE', '/api/admin/unifi/trust'); });
  busy($('ufClearUser'), async function () {
    const d = await send('PUT', '/api/admin/unifi/settings', { clear_user: true });
    fill(d.settings);
    return d;
  });
  $('ufFilter').addEventListener('input', function () { if (last) drawClients(last); });
  if (!can('unifi.manage')) $('ufSettings').hidden = true;

  refresh();
  setInterval(function () { if (!document.hidden && !document.getElementById('page-unifi').hidden) refresh(); }, 5000);
})();
