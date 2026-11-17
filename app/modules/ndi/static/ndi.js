/* The NDI admin page (#294): the sources found, live, and where to look for them. Loaded only while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-ndi')) return;
  const $ = R.$;
  let version = -1, polling = false;
  function can(perm) { return typeof window.relayCan !== 'function' || !!window.relayCan(perm); }
  function showError(t) { $('ndiErr').textContent = t || ''; $('ndiErr').hidden = !t; }
  async function send(method, path, body) {
    const res = await fetch(path, { method: method, headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), credentials: 'same-origin' });
    const data = await res.json().catch(function () { return {}; });
    if (!res.ok) throw new Error(data.error || data.detail || 'That did not work.');
    return data;
  }
  function cell(tr, text) { const td = document.createElement('td'); td.textContent = text; tr.appendChild(td); }
  function draw(s) {
    version = s.version;
    const warn = $('ndiNoRuntime');
    warn.textContent = s.available ? '' : s.error;
    warn.hidden = s.available;
    $('ndiSum').textContent = s.running ? s.sources.filter(function (x) { return x.online; }).length + ' online' : '';
    $('ndiNone').hidden = s.sources.length > 0;
    $('ndiTable').hidden = !s.sources.length;
    const body = $('ndiTable').querySelector('tbody');
    body.textContent = '';
    s.sources.forEach(function (x) {
      const tr = document.createElement('tr');
      cell(tr, x.source || x.name);
      cell(tr, x.machine);
      cell(tr, x.width ? x.width + 'x' + x.height + (x.fps ? ' ' + x.fps + ' fps' : '') : '—');
      cell(tr, x.groups.join(', '));
      cell(tr, x.address ? x.address + (x.port ? ':' + x.port : '') : '—');
      cell(tr, (x.online ? 'Online' : 'Offline') + (x.converter ? ' · converter set to ' + (x.converter.source || x.converter.source_ip) : ''));
      cell(tr, x.listeners ? String(x.listeners) : '—');
      const w = document.createElement('td');
      if (x.online) {
        const link = document.createElement('a');
        link.href = '/ndi/view/' + encodeURIComponent(x.name);
        link.target = '_blank'; link.rel = 'noopener'; link.textContent = 'Watch';
        w.appendChild(link);
      }
      tr.appendChild(w);
      body.appendChild(tr);
    });
    if (document.activeElement !== $('ndiGroups')) $('ndiGroups').value = s.settings.groups.join(', ');
    if (document.activeElement !== $('ndiIps')) $('ndiIps').value = s.settings.extra_ips.join(', ');
    const st = $('ndiServerStatus');
    st.textContent = '';
    (s.servers || []).forEach(function (x) {
      const p = document.createElement('p');
      p.className = 'note' + (x.reachable ? '' : ' warn');
      p.textContent = x.server + ': ' + (x.reachable ? 'connected, ' + s.via_server + ' source' + (s.via_server === 1 ? '' : 's') + ' reported' : (x.error || 'not answering'));
      st.appendChild(p);
    });
    $('ndiRestart').hidden = !s.restart_needed;
    if (document.activeElement !== $('ndiServers')) $('ndiServers').value = s.settings.servers.join(', ');
    $('ndiMdns').checked = s.settings.mdns;
    [['ndiFps', 'view_fps'], ['ndiQuality', 'view_quality'], ['ndiWidth', 'view_width'], ['ndiMax', 'view_max']].forEach(function (p) {
      if (document.activeElement !== $(p[0])) $(p[0]).value = s.settings[p[1]];
      $(p[0]).disabled = !can('ndi.manage');
    });
    if (document.activeElement !== $('ndiTally')) {
      $('ndiTally').value = Object.keys(s.settings.tally || {}).map(function (k) { return k + ' = ' + s.settings.tally[k]; }).join('\n');
    }
    $('ndiTally').disabled = $('ndiViewSave').disabled = !can('ndi.manage');
    drawMvList(s);
    if (window.RelayNdiMore) window.RelayNdiMore(s);
    $('ndiServers').disabled = $('ndiMdns').disabled = !can('ndi.manage');
    $('ndiScan').disabled = !s.running || !can('ndi.manage');
    $('ndiSave').disabled = !can('ndi.manage');
  }
  async function poll() {
    if (polling) return;
    polling = true;
    try {
      // Waits on the server for a change, so the list is live; a quiet page costs one request every 20 s.
      draw(await send('GET', '/api/admin/ndi?since=' + version));
    } catch (e) { await new Promise(function (r) { setTimeout(r, 4000); }); }
    polling = false;
    if (document.getElementById('page-ndi')) setTimeout(poll, 50);
  }
  function list(text) { return text.split(',').map(function (x) { return x.trim(); }).filter(Boolean); }
  $('ndiScan').onclick = async function () {
    showError('');
    try { draw(await send('POST', '/api/admin/ndi/scan')); } catch (e) { showError(e.message); }
  };
  $('ndiSave').onclick = async function () {
    showError('');
    try { draw(await send('PUT', '/api/admin/ndi/settings', { servers: list($('ndiServers').value), mdns: $('ndiMdns').checked, groups: list($('ndiGroups').value), extra_ips: list($('ndiIps').value) })); }
    catch (e) { showError(e.message); }
  };
  $('ndiViewSave').onclick = async function () {
    showError('');
    const tally = {};
    $('ndiTally').value.split('\n').forEach(function (line) {
      const i = line.lastIndexOf('=');
      if (i > 0) tally[line.slice(0, i).trim()] = parseInt(line.slice(i + 1), 10);
    });
    try {
      draw(await send('PUT', '/api/admin/ndi/settings', { view_fps: +$('ndiFps').value, view_quality: +$('ndiQuality').value,
        view_width: +$('ndiWidth').value, view_max: +$('ndiMax').value, tally: tally }));
    } catch (e) { showError(e.message); }
  };
  window.RelayNdi = { send: send, showError: showError, can: can };
  // ---- multiviews (#297)
  const LAYOUTS = { '2x2': [4, '2 x 2'], '3x3': [9, '3 x 3'], '4x4': [16, '4 x 4'], '1+5': [6, '1 big, 5 small'], '1+7': [8, '1 big, 7 small'] };
  let mvs = [], cur = -1, lastSources = [];
  function el(tag, text, attrs) {
    const e = document.createElement(tag); if (text) e.textContent = text;
    Object.keys(attrs || {}).forEach(function (k) { e[k] = attrs[k]; }); return e;
  }
  function drawMvList(s) {
    lastSources = s.sources;
    if ($('ndiMvEdit').hidden) mvs = JSON.parse(JSON.stringify(s.settings.multiviews || []));
    const list = $('ndiMvList'); list.textContent = '';
    mvs.forEach(function (m, i) {
      const row = el('p', ''); row.className = 'note';
      const a = el('a', m.name, { href: '/ndi/multiview/' + encodeURIComponent(m.id), target: '_blank', rel: 'noopener' });
      row.appendChild(a); row.appendChild(document.createTextNode(' (' + (LAYOUTS[m.layout] || ['', m.layout])[1] + ') '));
      const b = el('button', 'Edit', { type: 'button' }); b.onclick = function () { openMv(i); }; row.appendChild(b);
      list.appendChild(row);
    });
    $('ndiMvNew').disabled = !can('ndi.manage');
  }
  function boxRow(i, b) {
    const tr = el('tr'); tr.dataset.i = i;
    tr.appendChild(el('td', String(i + 1)));
    const kind = el('select'); [['empty', 'Nothing'], ['source', 'NDI source'], ['clock', 'Clock'], ['timer', 'Countdown']].forEach(function (k) { kind.appendChild(el('option', k[1], { value: k[0] })); });
    kind.value = b.kind; kind.className = 'mvKind';
    const td = el('td'); td.appendChild(kind); tr.appendChild(td);
    const what = el('td'); what.className = 'mvWhat';
    const src = el('select'); src.className = 'mvSrc'; src.appendChild(el('option', 'Choose a source', { value: '' }));
    const names = lastSources.map(function (x) { return x.name; }); if (b.source && names.indexOf(b.source) < 0) names.push(b.source);
    names.forEach(function (n) { src.appendChild(el('option', n, { value: n })); }); src.value = b.source;
    const mode = el('select'); mode.className = 'mvMode'; [['time', 'Time of day'], ['service', 'Next service']].forEach(function (k) { mode.appendChild(el('option', k[1], { value: k[0] })); }); mode.value = b.mode;
    const at = el('input', '', { type: 'time', value: b.at }); at.className = 'mvAt';
    what.appendChild(src); what.appendChild(mode); what.appendChild(at); tr.appendChild(what);
    const lab = el('input', '', { value: b.label, maxLength: 40 }); lab.className = 'mvLabel';
    const t3 = el('td'); t3.appendChild(lab); tr.appendChild(t3);
    const meter = el('input', '', { type: 'checkbox', checked: !!b.meter }); meter.className = 'mvMeter';
    const t4 = el('td'); t4.appendChild(meter); tr.appendChild(t4);
    const sync = function () {
      src.hidden = kind.value !== 'source'; meter.hidden = kind.value !== 'source';
      mode.hidden = at.hidden = kind.value !== 'timer'; at.hidden = kind.value !== 'timer' || mode.value !== 'time';
      lab.hidden = kind.value === 'empty';
    };
    kind.onchange = mode.onchange = sync; sync();
    return tr;
  }
  function openMv(i) {
    cur = i; const m = mvs[i];
    $('ndiMvEdit').hidden = false; $('ndiMvName').value = m.name;
    const sel = $('ndiMvLayout'); sel.textContent = '';
    Object.keys(LAYOUTS).forEach(function (k) { sel.appendChild(el('option', LAYOUTS[k][1], { value: k })); });
    sel.value = m.layout; sel.onchange = function () { m.boxes = readBoxes(sel.value); paintBoxes(m.boxes, sel.value); };
    paintBoxes(m.boxes, m.layout);
    $('ndiMvDelete').hidden = !m.id;
  }
  function paintBoxes(boxes, layout) {
    const body = $('ndiMvBoxes'); body.textContent = '';
    for (let i = 0; i < LAYOUTS[layout][0]; i++) body.appendChild(boxRow(i, boxes[i] || { kind: 'empty', source: '', label: '', meter: false, mode: 'time', at: '10:30' }));
  }
  function readBoxes() {
    return Array.prototype.map.call($('ndiMvBoxes').children, function (tr) {
      const q = function (c) { return tr.querySelector(c); };
      return { kind: q('.mvKind').value, source: q('.mvSrc').value, label: q('.mvLabel').value, meter: q('.mvMeter').checked, mode: q('.mvMode').value, at: q('.mvAt').value || '10:30' };
    });
  }
  async function saveMvs(list) {
    showError('');
    try { const s = await send('PUT', '/api/admin/ndi/settings', { multiviews: list }); cur = -1; $('ndiMvEdit').hidden = true; draw(s); }
    catch (e) { showError(e.message); }
  }
  $('ndiMvNew').onclick = function () {
    mvs.push({ id: '', name: 'Multiview ' + (mvs.length + 1), layout: '2x2', boxes: [] }); openMv(mvs.length - 1);
  };
  $('ndiMvSave').onclick = function () {
    const m = mvs[cur]; if (!m) return;
    m.name = $('ndiMvName').value; m.layout = $('ndiMvLayout').value; m.boxes = readBoxes();
    saveMvs(mvs);
  };
  $('ndiMvDelete').onclick = function () { mvs.splice(cur, 1); saveMvs(mvs); };
  poll();
})();
