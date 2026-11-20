/* The NDI admin page (#294): tabs for the overview, the sources coming in (with camera numbers), the screens that show
   them (multiviews, single-source pages) and setup. Going out (router, page outputs) is ndi-router.js. Loaded only while
   the module is on. A part that holds buttons is rebuilt only when what it shows changes, so a click during a live
   update is not lost. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-ndi')) return;
  const $ = R.$;
  const can = relayUI.can, send = relayUI.api, el = relayUI.el;
  let version = -1, polling = false, state = null, tab = 'home';
  const drawn = {};                       // part -> the signature it was last drawn from
  function changed(part, data) { const k = JSON.stringify(data); if (drawn[part] === k) return false; drawn[part] = k; return true; }
  function showError(t) { $('ndiErr').textContent = t || ''; $('ndiErr').hidden = !t; }
  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : (many || one + 's')); }
  function btn(text, fn, cls) { const b = el('button', { type: 'button', className: cls || '' }, text); b.onclick = fn; return b; }
  function label(x) { return x.source || x.name; }

  // ---- tabs
  function showTab(name) {
    tab = name;
    $('ndiTabs').querySelectorAll('[data-nditab]').forEach(function (b) { b.setAttribute('aria-selected', String(b.dataset.nditab === name)); });
    document.querySelectorAll('#page-ndi [data-ndipane]').forEach(function (p) { p.hidden = p.dataset.ndipane !== name; });
  }
  $('ndiTabs').addEventListener('click', function (e) { const b = e.target.closest('[data-nditab]'); if (b) showTab(b.dataset.nditab); });
  document.getElementById('page-ndi').addEventListener('click', function (e) {
    const g = e.target.closest('[data-ndigo]'); if (g) showTab(g.dataset.ndigo);
  });

  // ---- overview
  function drawOverview(s) {
    const on = s.sources.filter(function (x) { return x.online; }).length, off = s.sources.length - on;
    const mvs = s.settings.multiviews || [], routes = s.router.routes, pagesOn = s.pages.filter(function (p) { return p.on; });
    $('ndiOvIn').textContent = s.running ? on + ' online' + (off ? ' · ' + off + ' offline' : '') : 'Off';
    $('ndiOvScreens').textContent = plural(mvs.length, 'multiview');
    $('ndiOvOut').textContent = plural(routes.length, 'switched feed') + ' · ' + plural(pagesOn.length, 'page');
    const steps = [
      [s.available && s.running && s.sources.length > 0, 'Relay can see NDI',
        s.available ? (s.sources.length ? plural(s.sources.length, 'source') + ' found.' : 'No sources found yet. If you expect some, check Setup.') : 'Install the NDI runtime first (see the message above).', 'setup'],
      [Object.keys(s.settings.tally || {}).length > 0, 'Cameras have numbers',
        'Give each camera its switcher number for the on-air border.', 'in'],
      [mvs.length > 0, 'A multiview for the booth', 'Several sources on one screen.', 'screens'],
      [routes.length > 0, 'TVs you can switch from here', 'Add a feed for each TV and set the TV to it once.', 'out'],
      [pagesOn.length > 0, 'Captions sent to your switcher', 'Turn on a screen or overlay as an NDI source.', 'out'],
    ];
    if (changed('steps', steps)) {
      const ol = $('ndiSteps'); ol.textContent = '';
      steps.forEach(function (st, i) {
        const li = el('li', st[0] ? 'done' : '');
        li.appendChild(el('span', 'n', st[0] ? '✓' : String(i + 1)));
        const body = el('div'); body.appendChild(el('b', '', st[1])); body.appendChild(el('span', 'hint', st[2]));
        li.appendChild(body);
        if (!st[0]) { const go = btn('Go there', null, 'sm'); go.dataset.ndigo = st[3]; li.appendChild(go); }
        ol.appendChild(li);
      });
    }
    const issues = [];
    (s.servers || []).forEach(function (x) { if (!x.reachable) issues.push(['Discovery server ' + x.server + ' is not answering', x.error || 'Check that it is on and its address is right.', 'setup']); });
    if (s.restart_needed) issues.push(['Restart Relay', 'The NDI runtime reads the discovery settings when Relay starts.', 'setup']);
    s.sources.forEach(function (x) { if (!x.online) issues.push([label(x) + ' is offline', 'On ' + (x.machine || 'an unknown machine') + '. Check its power and network cable.', 'in']); });
    routes.forEach(function (r) {
      if (r.error) issues.push([r.name + ': ' + r.error, '', 'out']);
      else if (!r.source) issues.push([r.name + ' shows nothing', 'A feed with no source sends black.', 'out']);
    });
    s.pages.forEach(function (p) { if (p.on && p.state === 'error') issues.push([p.name + ' is not sending', p.error, 'out']); });
    if (changed('issues', issues)) {
      const box = $('ndiIssues'); box.textContent = '';
      if (!issues.length) box.appendChild(el('p', 'note ok', 'Nothing needs a look.'));
      issues.forEach(function (it) {
        const row = el('div', 'ndiissue'); const t = el('div'); t.appendChild(el('b', '', it[0]));
        if (it[1]) t.appendChild(el('span', 'hint', it[1]));
        row.appendChild(t); const go = btn('Open', null, 'sm'); go.dataset.ndigo = it[2]; row.appendChild(go);
        box.appendChild(row);
      });
    }
  }

  // ---- coming in
  function usedBy(name, s) {
    const out = [];
    (s.settings.multiviews || []).forEach(function (m) { if (m.boxes.some(function (b) { return b.kind === 'source' && b.source === name; })) out.push(m.name); });
    s.router.routes.forEach(function (r) { if (r.source === name) out.push(r.name); });
    return out;
  }
  const HEALTH_DOT = { ok: 'ok', warn: 'warn', bad: 'err', quiet: '' };      // the dot beside a source being received (#300)
  function drawSources(s) {
    $('ndiNone').hidden = s.sources.length > 0;
    $('ndiTable').hidden = !s.sources.length;
    $('ndiLast').textContent = s.scanning ? 'Looking…' : '';
    const manage = can('ndi.manage'), tally = s.settings.tally || {};
    const rows = s.sources.map(function (x) {
      return [x.name, label(x), x.machine, x.online, x.width ? x.width + ' x ' + x.height + (x.fps ? ', ' + x.fps + ' fps' : '') : '',
        tally[x.name] || '', usedBy(x.name, s), x.converter ? (x.converter.source || x.converter.source_ip) : '',
        (s.outputs || []).indexOf(x.source) >= 0, x.health, x.health_why];
    });
    const body = $('ndiTable').querySelector('tbody');
    if (body.contains(document.activeElement) || !changed('sources', [rows, manage])) return;
    body.textContent = '';
    rows.forEach(function (r) {
      const tr = el('tr');
      const c1 = el('td');
      if (r[3] && r[9] && r[9] !== 'unknown') { const d = el('span', 'dot ' + HEALTH_DOT[r[9]] + ' ndihealth'); d.title = r[10] || ''; d.setAttribute('role', 'img'); d.setAttribute('aria-label', 'Stream health: ' + r[9]); c1.appendChild(d); }
      c1.appendChild(el('b', '', r[1]));
      if (!r[3]) { c1.appendChild(document.createTextNode(' ')); c1.appendChild(el('span', 'badge muted', 'Offline')); }
      if (r[8]) { c1.appendChild(document.createTextNode(' ')); c1.appendChild(el('span', 'badge info', 'Sent by Relay')); }
      c1.appendChild(el('span', 'hint', r[2] + (r[7] ? ' · converter set to ' + r[7] : '')));
      tr.appendChild(c1);
      tr.appendChild(el('td', 'muted', r[4] || '—'));
      const c3 = el('td');
      const num = el('input', { type: 'number', min: 1, max: 999, step: 1, value: r[5], disabled: !manage, className: 'ndicam' });
      num.dataset.name = r[0]; num.setAttribute('aria-label', 'Camera number for ' + r[1]); num.placeholder = 'None';
      c3.appendChild(num); tr.appendChild(c3);
      const c4 = el('td'); r[6].forEach(function (u) { c4.appendChild(el('span', 'badge muted', u)); c4.appendChild(document.createTextNode(' ')); });
      tr.appendChild(c4);
      const c5 = el('td');
      if (r[3]) c5.appendChild(btn('Watch', function () { window.open('/ndi/view/' + encodeURIComponent(r[0]), '_blank', 'noopener'); }, 'sm'));
      tr.appendChild(c5);
      body.appendChild(tr);
    });
  }
  $('ndiTallySave').onclick = async function () {
    showError('');
    const tally = Object.assign({}, state.settings.tally || {});
    let bad = '';
    $('ndiTable').querySelectorAll('.ndicam').forEach(function (i) {
      const v = i.value.trim();
      if (!v) { delete tally[i.dataset.name]; return; }
      const n = Number(v);
      if (!Number.isInteger(n) || n < 1 || n > 999) bad = 'A camera number is a whole number from 1 to 999.';
      else tally[i.dataset.name] = n;
    });
    if (bad) { showError(bad); return; }
    try { draw(await send('PUT', '/api/admin/ndi/settings', { tally: tally })); } catch (e) { showError(e.message); }
  };
  $('ndiScan').onclick = async function () {
    showError('');
    try { draw(await send('POST', '/api/admin/ndi/scan')); } catch (e) { showError(e.message); }
  };

  // ---- screens: multiviews (#297) and single-source pages
  // Each layout as columns, rows and the big box (column, row, columns, rows), as multiview.py draws it.
  const LAYOUTS = { '2x2': [2, 2, null, '2 x 2'], '3x3': [3, 3, null, '3 x 3'], '4x4': [4, 4, null, '4 x 4'],
    '1+5': [3, 3, [0, 0, 2, 2], '1 big, 5 small'], '1+7': [4, 4, [0, 0, 3, 3], '1 big, 7 small'] };
  function cells(layout) {
    const L = LAYOUTS[layout], big = L[2], out = big ? [big] : [], taken = {};
    if (big) for (let c = big[0]; c < big[0] + big[2]; c++) for (let r = big[1]; r < big[1] + big[3]; r++) taken[c + ',' + r] = 1;
    for (let r = 0; r < L[1]; r++) for (let c = 0; c < L[0]; c++) if (!taken[c + ',' + r]) out.push([c, r, 1, 1]);
    return out;
  }
  const EMPTY = function () { return { kind: 'empty', source: '', label: '', meter: false, mode: 'time', at: '10:30' }; };
  let edit = null, sel = 0;               // the multiview being edited (a copy) and its selected box
  function boxText(b) {
    if (b.kind === 'source') {
      const x = state && state.sources.filter(function (s) { return s.name === b.source; })[0];
      return x ? label(x) : (b.source || 'Choose a source');
    }
    if (b.kind === 'clock') return 'Clock';
    if (b.kind === 'timer') return b.mode === 'service' ? 'Countdown to the next service' : 'Countdown to ' + b.at;
    return 'Empty';
  }
  function drawMvList(s) {
    const mvs = s.settings.multiviews || [], manage = can('ndi.manage');
    $('ndiMvNew').disabled = !manage || !!edit;
    if (!changed('mvlist', [mvs, manage])) return;
    const list = $('ndiMvList'); list.textContent = '';
    if (!mvs.length) list.appendChild(el('p', 'note', 'No multiviews yet.'));
    mvs.forEach(function (m) {
      const card = el('div', 'ndimvcard');
      card.appendChild(thumb(m.layout));
      const head = el('div', 'ndihead'); head.appendChild(el('b', '', m.name)); head.appendChild(el('span', 'muted', LAYOUTS[m.layout] ? LAYOUTS[m.layout][3] : m.layout));
      card.appendChild(head);
      const url = '/ndi/multiview/' + encodeURIComponent(m.id);
      const ctl = el('div', 'ctl');
      ctl.appendChild(btn('Edit', function () { openMv(m.id); }, 'sm primary')).disabled = !manage;
      ctl.appendChild(btn('Open', function () { window.open(url, '_blank', 'noopener'); }, 'sm'));
      ctl.appendChild(btn('Copy link', function () { copy(location.origin + url, this); }, 'sm'));
      card.appendChild(ctl);
      list.appendChild(card);
    });
  }
  function thumb(layout) {
    const t = el('div', 'ndithumb'); const L = LAYOUTS[layout] || LAYOUTS['2x2'];
    t.style.gridTemplateColumns = 'repeat(' + L[0] + ',1fr)'; t.style.gridTemplateRows = 'repeat(' + L[1] + ',1fr)';
    cells(layout).forEach(function (c) { const i = el('i'); place(i, c); t.appendChild(i); });
    return t;
  }
  function place(node, c) { node.style.gridColumn = (c[0] + 1) + ' / span ' + c[2]; node.style.gridRow = (c[1] + 1) + ' / span ' + c[3]; }
  function copy(text, b) {
    const done = function () { if (b) { const t = b.textContent; b.textContent = 'Copied'; setTimeout(function () { b.textContent = t; }, 1500); } };
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, function () { window.prompt('Copy this link', text); });
    else window.prompt('Copy this link', text);
  }
  function openMv(id) {
    const m = (state.settings.multiviews || []).filter(function (x) { return x.id === id; })[0];
    edit = m ? JSON.parse(JSON.stringify(m)) : { id: '', name: 'Multiview ' + ((state.settings.multiviews || []).length + 1), layout: '2x2', boxes: [] };
    sel = 0;
    $('ndiMvEdit').hidden = false; $('ndiMvTitle').textContent = edit.id ? 'Editing ' + edit.name : 'New multiview';
    $('ndiMvName').value = edit.name; $('ndiMvDelete').hidden = !edit.id;
    const ls = $('ndiMvLayout'); ls.textContent = '';
    Object.keys(LAYOUTS).forEach(function (k) { ls.appendChild(el('option', { value: k }, LAYOUTS[k][3])); });
    ls.value = edit.layout;
    drawEditor();
    $('ndiMvNew').disabled = true;
    $('ndiMvEdit').scrollIntoView({ block: 'start', behavior: 'smooth' });
  }
  function closeMv() { edit = null; $('ndiMvEdit').hidden = true; drawn.mvlist = ''; if (state) drawMvList(state); }
  function fit() { const n = cells(edit.layout).length; while (edit.boxes.length < n) edit.boxes.push(EMPTY()); }
  function drawEditor() {
    fit();
    const L = LAYOUTS[edit.layout], cv = $('ndiMvCanvas'); cv.textContent = '';
    cv.style.gridTemplateColumns = 'repeat(' + L[0] + ',minmax(0,1fr))'; cv.style.gridTemplateRows = 'repeat(' + L[1] + ',minmax(0,1fr))';
    cells(edit.layout).forEach(function (c, i) {
      const b = edit.boxes[i];
      const box = el('button', { type: 'button', className: 'ndibox ' + b.kind + (i === sel ? ' sel' : '') });
      box.setAttribute('aria-pressed', String(i === sel));
      box.setAttribute('aria-label', 'Box ' + (i + 1) + ': ' + boxText(b));
      place(box, c);
      box.appendChild(el('span', 'ndibn', String(i + 1)));
      box.appendChild(el('span', 'ndibw', boxText(b)));
      if (b.label && b.kind !== 'empty' && b.label !== boxText(b)) box.appendChild(el('span', 'ndibl', b.label));
      box.onclick = function () { sel = i; drawEditor(); };
      box.ondragover = function (e) { e.preventDefault(); box.classList.add('over'); };
      box.ondragleave = function () { box.classList.remove('over'); };
      box.ondrop = function (e) {
        e.preventDefault();
        try { assign(i, JSON.parse(e.dataTransfer.getData('text/plain'))); } catch (err) { /* not one of ours */ }
      };
      cv.appendChild(box);
    });
    drawPalette(); drawBox();
  }
  function paletteItems() {
    const out = state.sources.map(function (x) { return { kind: 'source', source: x.name, text: label(x) + (x.online ? '' : ' (offline)') }; });
    edit.boxes.forEach(function (b) {   // a source the multiview uses that is not seen now stays choosable
      if (b.kind === 'source' && b.source && !out.some(function (o) { return o.source === b.source; })) out.push({ kind: 'source', source: b.source, text: b.source + ' (not seen)' });
    });
    return out.concat([{ kind: 'clock', text: 'Clock' }, { kind: 'timer', mode: 'time', text: 'Countdown to a time' },
      { kind: 'timer', mode: 'service', text: 'Countdown to the next service' }, { kind: 'empty', text: 'Empty' }]);
  }
  function drawPalette() {
    const p = $('ndiMvPalette'); p.textContent = '';
    p.appendChild(el('b', '', 'Drag onto a box'));
    paletteItems().forEach(function (it) {
      const c = el('button', { type: 'button', className: 'ndichip ' + it.kind, draggable: true }, it.text);
      c.setAttribute('aria-label', 'Put ' + it.text + ' in box ' + (sel + 1));
      c.ondragstart = function (e) { e.dataTransfer.setData('text/plain', JSON.stringify(it)); e.dataTransfer.effectAllowed = 'copy'; };
      c.onclick = function () { assign(sel, it); };
      p.appendChild(c);
    });
  }
  function assign(i, it) {
    const b = edit.boxes[i], was = b.kind === 'source' ? b.source : '';
    const auto = !b.label || b.label === autoLabel(b);
    b.kind = it.kind;
    if (it.kind === 'source') { b.source = it.source; if (!was) b.meter = true; }
    if (it.kind === 'timer') b.mode = it.mode;
    if (auto) b.label = autoLabel(b);
    sel = i;
    const n = cells(edit.layout).length;      // move on to the next empty box, so filling a layout is click, click, click
    for (let k = 1; k < n; k++) { const j = (i + k) % n; if (edit.boxes[j].kind === 'empty') { sel = j; break; } }
    drawEditor();
  }
  function autoLabel(b) {
    if (b.kind === 'source') { const x = state.sources.filter(function (s) { return s.name === b.source; })[0]; return x ? label(x) : b.source; }
    if (b.kind === 'timer') return b.mode === 'service' ? 'Service starts' : 'Countdown';
    return '';
  }
  function drawBox() {
    const b = edit.boxes[sel], box = $('ndiMvBox'); box.textContent = '';
    box.appendChild(el('b', '', 'Box ' + (sel + 1) + ': ' + boxText(b)));
    if (b.kind === 'empty') { box.appendChild(el('p', 'hint', 'Shows nothing. Drag something onto it or click an item above.')); return; }
    const f = el('div', 'field'); f.appendChild(el('label', { htmlFor: 'ndiMvLabel' }, 'Label'));
    const lab = el('input', { id: 'ndiMvLabel', value: b.label, maxLength: 40 });
    lab.oninput = function () { b.label = lab.value; };
    lab.onchange = drawEditor;
    f.appendChild(lab); f.appendChild(el('span', 'hint', 'Shown along the bottom of the box. Empty shows none.'));
    box.appendChild(f);
    if (b.kind === 'source') {
      const ck = el('label', 'check'); const m = el('input', { type: 'checkbox', checked: !!b.meter });
      m.onchange = function () { b.meter = m.checked; };
      ck.appendChild(m); ck.appendChild(document.createTextNode(' Audio meter')); box.appendChild(ck);
    }
    if (b.kind === 'timer' && b.mode === 'time') {
      const t = el('div', 'field'); t.appendChild(el('label', { htmlFor: 'ndiMvAt' }, 'Counts down to'));
      const at = el('input', { id: 'ndiMvAt', type: 'time', value: b.at });
      at.onchange = function () { b.at = at.value || '10:30'; drawEditor(); };
      t.appendChild(at); box.appendChild(t);
    }
    if (b.kind === 'timer' && b.mode === 'service') box.appendChild(el('p', 'hint', 'Uses the next service time from Planning Center.'));
  }
  $('ndiMvLayout').onchange = function () { edit.layout = $('ndiMvLayout').value; sel = 0; drawEditor(); };
  $('ndiMvName').oninput = function () { edit.name = $('ndiMvName').value; };
  $('ndiMvNew').onclick = function () { openMv(''); };
  $('ndiMvCancel').onclick = closeMv;
  async function saveMvs(list) {
    showError('');
    try { const s = await send('PUT', '/api/admin/ndi/settings', { multiviews: list }); closeMv(); draw(s); }
    catch (e) { showError(e.message); }
  }
  $('ndiMvSave').onclick = function () {
    const n = cells(edit.layout).length, mv = { id: edit.id, name: edit.name, layout: edit.layout, boxes: edit.boxes.slice(0, n) };
    const list = (state.settings.multiviews || []).slice();
    const at = list.findIndex(function (x) { return edit.id && x.id === edit.id; });
    if (at >= 0) list[at] = mv; else list.push(mv);
    saveMvs(list);
  };
  $('ndiMvDelete').onclick = async function () {
    if (!(await relayUI.confirm('Delete the multiview "' + edit.name + '"? Screens showing it go blank.'))) return;
    saveMvs((state.settings.multiviews || []).filter(function (x) { return x.id !== edit.id; }));
  };
  function drawViewLinks(s) {
    const on = s.sources.filter(function (x) { return x.online; }).map(function (x) { return [x.name, label(x)]; });
    if (!changed('links', on)) return;
    const box = $('ndiViewLinks'); box.textContent = '';
    if (!on.length) box.appendChild(el('p', 'note', 'No sources online.'));
    on.forEach(function (x) {
      const url = '/ndi/view/' + encodeURIComponent(x[0]);
      const row = el('div', 'ndilink'); row.appendChild(el('b', '', x[1])); row.appendChild(el('code', '', url));
      row.appendChild(btn('Copy', function () { copy(location.origin + url, this); }, 'sm'));
      row.appendChild(btn('Open', function () { window.open(url, '_blank', 'noopener'); }, 'sm'));
      box.appendChild(row);
    });
  }

  // ---- setup
  function drawSetup(s) {
    const manage = can('ndi.manage');
    if (document.activeElement !== $('ndiGroups')) $('ndiGroups').value = s.settings.groups.join(', ');
    if (document.activeElement !== $('ndiIps')) $('ndiIps').value = s.settings.extra_ips.join(', ');
    if (document.activeElement !== $('ndiServers')) $('ndiServers').value = s.settings.servers.join(', ');
    $('ndiMdns').checked = s.settings.mdns;
    const st = $('ndiServerStatus'); st.textContent = '';
    (s.servers || []).forEach(function (x) {
      st.appendChild(el('p', 'note' + (x.reachable ? ' ok' : ' warn'), x.server + ': ' + (x.reachable
        ? 'connected, ' + plural(s.via_server, 'source') + ' reported' : (x.error || 'not answering'))));
    });
    $('ndiRestart').hidden = !s.restart_needed;
    [['ndiFps', 'view_fps'], ['ndiQuality', 'view_quality'], ['ndiWidth', 'view_width'], ['ndiMax', 'view_max']].forEach(function (p) {
      if (document.activeElement !== $(p[0])) $(p[0]).value = s.settings[p[1]];
      $(p[0]).disabled = !manage;
    });
    ['ndiServers', 'ndiMdns', 'ndiGroups', 'ndiIps', 'ndiSave', 'ndiViewSave', 'ndiTallySave'].forEach(function (id) { $(id).disabled = !manage; });
    $('ndiScan').disabled = !s.running || !manage;
  }
  function list(text) { return text.split(',').map(function (x) { return x.trim(); }).filter(Boolean); }
  $('ndiSave').onclick = async function () {
    showError('');
    try { draw(await send('PUT', '/api/admin/ndi/settings', { servers: list($('ndiServers').value), mdns: $('ndiMdns').checked, groups: list($('ndiGroups').value), extra_ips: list($('ndiIps').value) })); }
    catch (e) { showError(e.message); }
  };
  $('ndiViewSave').onclick = async function () {
    showError('');
    try {
      draw(await send('PUT', '/api/admin/ndi/settings', { view_fps: +$('ndiFps').value, view_quality: +$('ndiQuality').value,
        view_width: +$('ndiWidth').value, view_max: +$('ndiMax').value }));
    } catch (e) { showError(e.message); }
  };

  // ---- the whole page
  function draw(s) {
    state = s; version = s.version;
    const warn = $('ndiNoRuntime'); warn.textContent = s.available ? '' : s.error; warn.hidden = s.available;
    const on = s.sources.filter(function (x) { return x.online; }).length;
    $('ndiSum').textContent = s.running ? on + ' online' : 'Off';
    drawOverview(s); drawSources(s); drawMvList(s); drawViewLinks(s); drawSetup(s);
    if (edit && !$('ndiMvEdit').contains(document.activeElement) && changed('palette', s.sources.map(function (x) { return [x.name, x.online]; }))) drawPalette();
    if (window.RelayNdiMore) window.RelayNdiMore(s);
    window.dispatchEvent(new Event('relay-ndi-state'));        // the Analysis tab (ndi-analysis.js) reads the source list
  }
  window.RelayNdiState = function () { return state; };
  async function poll() {
    if (polling) return;
    polling = true;
    try {
      // Waits on the server for a change, so the page is live; a quiet page costs one request every 20 s.
      draw(await send('GET', '/api/admin/ndi?since=' + version));
    } catch (e) { await new Promise(function (r) { setTimeout(r, 4000); }); }
    polling = false;
    if (document.getElementById('page-ndi')) setTimeout(poll, 50);
  }
  window.RelayNdi = { send: send, showError: showError, can: can, draw: function (s) { draw(s); }, copy: copy, changed: changed };
  showTab(tab);
  poll();
})();
