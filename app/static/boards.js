/* The Boards page in the operator panel (#156). Down the side, the boards grouped by where they hang; beside it the
   chosen board:
     - its live preview, a tab per page, which page shows now and why, and the screens showing it (named here);
     - editing in place: the shared editor (layout-editor.js) with the widgets in a tray above the board and each
       widget's settings in a card beside it;
     - its pages and what decides the page shown: one page, a rotation, a schedule, or following ProPresenter;
     - a new board from a template; and Run: its buttons, and a page chosen by hand until the next change.
   The screen itself is /board/<slug> (board.js). Everything from the server is set as text. */
(function () {
  'use strict';
  if (!document.getElementById('page-boards')) return;
  const R = window.RelayLayout;
  const el = relayUI.el;
  const canManage = relayUI.can('boards.manage');
  const api = relayUI.client({ session: true, cache: 'no-store' });
  const showError = relayUI.note('bdErr');
  const button = relayUI.buttons(showError);
  const $ = function (id) { return document.getElementById(id); };
  const HINT = 'On a laptop: drag from the tray, arrows nudge. On a tablet: tap a tray item to drop it in the first ' +
    'free space, then drag; the settings card opens below the widget you tap.';
  const SHAPES = [['16:9', '16:9 TV'], ['4:3', '4:3 tablet'], ['9:16', '9:16 portrait']];
  const NEW_PLACE = '\u0000new';

  let F = null;            // the last frame from the server
  let sel = '';            // the chosen board's id
  let view = 'sel';        // sel | edit | pages | new | run
  let tab = '';            // the page shown in the preview
  let feeds = null;        // RelayBoardLive for what is drawn now
  let nodes = {};          // item id -> drawn widget
  const liveData = {};     // item id -> its last live data
  let ed = null, work = null, workPage = '';
  let rot = null, rotPage = '';
  let fresh = null;
  let showQr = false;
  let search = '';
  let pollTimer = 0;
  let offset = 0;

  /* ---------------------------------------------------------------- small helpers */
  function boardOf(id) { return F ? F.boards.filter(function (b) { return b.id === id; })[0] : null; }
  function cat() { const c = {}; (F ? F.catalogue : []).forEach(function (d) { c[d.type] = d; }); return c; }
  function liveOf(id) { return (F && F.live && F.live[id]) || { screens: [], page: '', rotate: null, why: '' }; }
  function nowPage(b) {
    const l = liveOf(b.id);
    return (l.rotate && RelayBoardLive.rotation(l.rotate, offset)) || l.page || b.pages[0].id;
  }
  function pageNo(b, pid) { return b.pages.map(function (p) { return p.id; }).indexOf(pid) + 1; }
  function pageLabel(b, p) { return pageNo(b, p.id) + ' · ' + p.name; }
  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }
  function allItems(b) { return b.pages.reduce(function (a, p) { return a.concat(p.items); }, []); }
  function offCount(b) { const c = cat(); return allItems(b).filter(function (it) { return !c[it.type]; }).length; }
  function places() {
    const out = [];
    (F ? F.boards : []).forEach(function (b) { if (b.place && out.indexOf(b.place) < 0) out.push(b.place); });
    return out.sort(function (a, b) { return a.localeCompare(b); });
  }
  function clock(t) { return new Date(t * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }); }
  function chip(text, cls) { return el('span', 'chip' + (cls ? ' ' + cls : ''), cls === 'ok' ? el('i') : null, text); }
  function slugFor(name) {
    const base = (name || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 32).replace(/-+$/, '') || 'board';
    const taken = (F ? F.boards : []).map(function (b) { return b.id; });
    let s = base, n = 2;
    while (taken.indexOf(s) >= 0) { s = base + '-' + n; n += 1; }
    return s;
  }
  function defaults(d) {
    const p = {};
    (d.fields || []).forEach(function (f) { if (f.type !== 'note' && f.default !== undefined && f.default !== null) p[f.key] = f.default; });
    return p;
  }
  // The same rule as boards.item_title: a matching preset's name, else the first list setting's choice, else the name.
  function titleOf(d, props) {
    props = props || {};
    const p = (d.presets || []).filter(function (p) {
      const keys = Object.keys(p.props || {});
      return keys.length && keys.every(function (k) { return props[k] === p.props[k]; });
    })[0];
    if (p) return p.name;
    const f = (d.fields || []).filter(function (f) { return f.type !== 'note'; })[0];
    if (f && f.type === 'select') { const o = f.options.filter(function (o) { return o[0] === props[f.key]; })[0]; if (o) return o[1]; }
    return d.name;
  }
  function forPage(items) {
    const c = cat();
    return items.map(function (it) {
      const d = c[it.type];
      return Object.assign({}, it, d ? { kind: d.kind, title: titleOf(d, it.props) } : { kind: 'text', title: '', off: 'This widget\'s module is off.' });
    });
  }
  function select(options, value, label) {
    const s = el('select');
    if (label) s.setAttribute('aria-label', label);
    options.forEach(function (o) { const op = el('option', '', o[1]); op.value = o[0]; if (o[0] === value) op.selected = true; s.appendChild(op); });
    return s;
  }
  function field(label, control) { return relayUI.field(label, control); }

  /* ---------------------------------------------------------------- a board drawn small */
  function thumb(items, b, extra) {
    const t = el('div', 'bd-tv bd-thumb' + (b.ratio === '9:16' ? ' p916' : (b.ratio === '4:3' ? ' p43' : '')) + (extra ? ' ' + extra : ''));
    t.style.aspectRatio = b.ratio.replace(':', '/');
    t.style.gridTemplateColumns = 'repeat(' + b.cols + ',minmax(0,1fr))';
    t.style.gridTemplateRows = 'repeat(' + b.rows + ',minmax(0,1fr))';
    items.forEach(function (it) {
      const i = el('i'); R.area(i, it); i.style.background = R.look(it).bg; t.appendChild(i);
    });
    return t;
  }
  function gapFor(box) { return Math.max(2, Math.round(box.clientWidth * 0.012)); }

  /* ---------------------------------------------------------------- live data for what is drawn */
  function apply(id, d) {
    liveData[id] = Object.assign(liveData[id] || {}, d);
    if (nodes[id]) R.update(nodes[id], liveData[id]);
  }
  function stopFeeds() { if (feeds) feeds.stop(); feeds = null; }
  function startFeeds(b, itemsFn) {
    stopFeeds();
    feeds = RelayBoardLive.start({ base: '/board/' + encodeURIComponent(b.id), items: itemsFn, apply: apply,
      onPoll: function (got) { if (typeof got.now === 'number') offset = got.now - Date.now() / 1000; } });
  }
  function wire(ns) {
    nodes = ns;
    Object.keys(ns).forEach(function (id) { if (liveData[id]) R.update(ns[id], liveData[id]); });
  }

  /* ---------------------------------------------------------------- the list down the side */
  function summary(b) {
    const l = liveOf(b.id), parts = [];
    if (b.public) parts.push('Public');
    parts.push(l.screens.length ? plural(l.screens.length, 'screen', 'screens') : 'Not open');
    if (b.pages.length > 1) parts.push(plural(b.pages.length, 'page', 'pages'));
    if (b.rotation.mode === 'follow') parts.push('follows ProPresenter');
    const off = offCount(b);
    if (off) parts.push(plural(off, 'widget', 'widgets') + ' off');
    return parts.join(' · ');
  }
  function renderList() {
    const nav = $('bdList');
    const q = nav.querySelector('input');
    const had = q && document.activeElement === q;
    nav.textContent = '';
    const hd = el('div', 'hd');
    const s = el('input'); s.placeholder = 'Search boards'; s.setAttribute('aria-label', 'Search boards'); s.value = search;
    s.oninput = function () { search = s.value; renderList(); };
    hd.appendChild(s);
    if (canManage) {
      const plus = button('+', function () { go('new'); }, 'primary icon'); plus.setAttribute('aria-label', 'New board');
      hd.appendChild(plus);
    }
    nav.appendChild(hd);
    const want = search.trim().toLowerCase();
    const list = F.boards.filter(function (b) { return !want || b.name.toLowerCase().indexOf(want) >= 0 || (b.place || '').toLowerCase().indexOf(want) >= 0; });
    const groups = places().concat(['']);
    let any = false;
    groups.forEach(function (pl) {
      const bs = list.filter(function (b) { return (b.place || '') === pl; });
      if (!bs.length) return;
      any = true;
      nav.appendChild(el('div', 'pl', pl || (places().length ? 'No place' : 'Boards')));
      bs.forEach(function (b) {
        const a = el('a', b.id === sel ? 'on' : '');
        a.href = '#boards'; if (b.id === sel) a.setAttribute('aria-current', 'true');
        a.onclick = function (e) { e.preventDefault(); choose(b.id); };
        const pg = b.pages.filter(function (p) { return p.id === nowPage(b); })[0] || b.pages[0];
        a.appendChild(thumb(pg.items, b));
        a.appendChild(el('span', '', el('b', '', b.name), el('small', '', summary(b))));
        nav.appendChild(a);
      });
    });
    if (!any) nav.appendChild(el('div', 'none', F.boards.length ? 'No board matches.' : 'No boards yet.'));
    if (canManage) {
      const ft = el('div', 'hd ft');
      ft.appendChild(button('Import…', function () { $('bdImport').click(); }));
      nav.appendChild(ft);
    }
    if (had) { const n = nav.querySelector('input'); n.focus(); n.setSelectionRange(n.value.length, n.value.length); }
  }

  /* ---------------------------------------------------------------- the chosen board */
  async function choose(id) {
    if (view === 'edit' && dirtyEdit() && !(await relayUI.confirm('Leave this board without saving your changes?'))) return;
    sel = id; tab = ''; view = 'sel'; showQr = false; ed = null; render();
  }
  function go(v) {
    if (v === 'new') fresh = { template: (F.templates[0] || {}).id || 'blank', name: '', place: '', ratio: '' };
    if (v === 'pages') { const b = boardOf(sel); rot = { pages: JSON.parse(JSON.stringify(b.pages)), rotation: JSON.parse(JSON.stringify(b.rotation)) }; rotPage = rot.pages[0].id; }
    view = v; render();
  }
  function header(b, main) {
    const h = el('div', 'bh');
    if (view !== 'sel') h.appendChild(button('← ' + b.name, function () { go('sel'); }, 'quiet'));
    else h.appendChild(el('h2', 'title', b.name));
    if (b.place) h.appendChild(chip(b.place));
    if (b.public) h.appendChild(chip('Public', 'warn'));
    h.appendChild(chip(b.ratio + ' · ' + plural(b.pages.length, 'page', 'pages')));
    h.appendChild(el('span', 'sp'));
    const url = location.protocol + '//' + location.host + '/board/' + b.id;
    const a = el('a', '', location.host + '/board/' + b.id); a.href = '/board/' + encodeURIComponent(b.id); a.target = '_blank'; a.rel = 'noopener';
    const addr = el('span', 'addr', a);
    addr.appendChild(button('Copy', async function () {
      try { await navigator.clipboard.writeText(url); } catch (e) { /* the address is still there to select */ }
    }));
    addr.appendChild(button('QR', function () { showQr = !showQr; render(); }));
    h.appendChild(addr);
    main.appendChild(h);
    if (showQr && typeof qrcode === 'function') {
      const box = el('div', 'qr'); box.setAttribute('role', 'img'); box.setAttribute('aria-label', 'QR code for ' + url);
      try {
        const qr = qrcode(0, 'M'); qr.addData(url); qr.make();
        const svg = new DOMParser().parseFromString(qr.createSvgTag({ cellSize: 2, margin: 1, scalable: true }), 'image/svg+xml').documentElement;
        box.appendChild(document.importNode(svg, true));
      } catch (e) { box.textContent = url; }
      main.appendChild(el('div', 'gap', box, el('span', 'hint', 'Scan with a TV or tablet to open the board.')));
    }
  }

  function render() {
    stopFeeds(); nodes = {};
    if (!F) return;
    if (sel && !boardOf(sel)) sel = '';
    if (!sel && F.boards.length) sel = F.boards[0].id;
    renderList();
    const main = $('bdMain'); main.textContent = '';
    if (view === 'new' && canManage) { renderNew(main); return; }
    const b = boardOf(sel);
    if (!b) {
      main.appendChild(el('div', 'card', el('h3', '', 'No boards yet'),
        el('p', 'lede', 'A board is a screen you build from widgets: captions, a clock, the livestream, lighting cues and more. ' +
          'Each has its own address for a TV or tablet.'),
        canManage ? button('New board', function () { go('new'); }, 'primary') : null));
      return;
    }
    header(b, main);
    if (view === 'edit' && canManage) renderEdit(main, b);
    else if (view === 'pages' && canManage) renderPages(main, b);
    else if (view === 'run') renderRun(main, b);
    else { view = 'sel'; renderSel(main, b); }
  }

  /* ---- the board: preview, status, screens, settings */
  function renderSel(main, b) {
    const grid = el('div', 'sel2');
    const left = el('div', 'stack');
    const showing = nowPage(b);
    if (!tab || !b.pages.some(function (p) { return p.id === tab; })) tab = showing;
    if (b.pages.length > 1) {
      const tabs = el('div', 'tabs'); tabs.setAttribute('role', 'tablist');
      b.pages.forEach(function (p) {
        const t = button(pageLabel(b, p), function () { tab = p.id; render(); }, p.id === tab ? 'on' : '');
        t.setAttribute('role', 'tab'); t.setAttribute('aria-selected', String(p.id === tab));
        tabs.appendChild(t);
      });
      left.appendChild(tabs);
    }
    const page = b.pages.filter(function (p) { return p.id === tab; })[0];
    const tv = el('div', 'bd-tv bd-live'); tv.style.aspectRatio = b.ratio.replace(':', '/');
    left.appendChild(tv);
    const status = el('div', 'gap'); status.id = 'bdStatus';
    left.appendChild(status);
    grid.appendChild(left);
    const right = el('div', 'stack');
    const sc = el('div', 'card'); sc.id = 'bdScreens'; right.appendChild(sc);
    right.appendChild(settingsCard(b));
    grid.appendChild(right);
    main.appendChild(grid);
    drawStatus(b); drawScreens(b);
    if (!page.items.length) tv.appendChild(el('div', 'empty', canManage ? 'This page is empty. Edit layout to add widgets.' : 'This page is empty.'));
    else {
      const g = el('div'); tv.appendChild(g);
      wire(R.draw(g, { cols: b.cols, rows: b.rows, items: forPage(page.items) }));
      requestAnimationFrame(function () { g.style.setProperty('--rl-gap', gapFor(tv) + 'px'); });
    }
    startFeeds(b, function () { return page.items; });
  }
  function drawStatus(b) {
    const box = $('bdStatus'); if (!box) return;
    box.textContent = '';
    const l = liveOf(b.id), showing = nowPage(b);
    box.appendChild(chip('Showing page ' + pageNo(b, showing) + ' now', 'ok'));
    if (l.why) box.appendChild(el('span', 'muted', l.why));
    box.appendChild(el('span', 'sp'));
    if (canManage) {
      box.appendChild(button('Edit layout', function () { go('edit'); }, 'primary'));
      box.appendChild(button('Pages and rotation', function () { go('pages'); }));
    }
    box.appendChild(button('Run', function () { go('run'); }));
  }
  function drawScreens(b) {
    const box = $('bdScreens'); if (!box) return;
    if (box.querySelector('input') && box.contains(document.activeElement)) return;   // someone is naming one
    box.textContent = '';
    box.appendChild(el('h3', '', 'Screens showing it'));
    const rows = liveOf(b.id).screens;
    if (!rows.length) box.appendChild(el('p', 'hint', 'No screen has this board open. Open its address on a TV or tablet.'));
    rows.forEach(function (s) {
      const row = el('div', 'screenrow');
      const since = chip('since ' + clock(s.since), 'ok');
      function naming() {
        row.textContent = '';
        const i = el('input'); i.value = s.name; i.maxLength = 40; i.placeholder = 'Foyer TV left'; i.setAttribute('aria-label', 'Screen name');
        const save = async function () {
          showError('');
          try { load(await api('PUT', '/api/admin/boards/' + encodeURIComponent(b.id) + '/screens/' + encodeURIComponent(s.id), { name: i.value })); }
          catch (e) { showError(e.message); }
        };
        i.onkeydown = function (e) { if (e.key === 'Enter') save(); if (e.key === 'Escape') render(); };
        row.appendChild(i); row.appendChild(button('Save', save, 'primary'));
        i.focus();
      }
      if (s.name) {
        const n = el('span', '', s.name);
        if (canManage) { n.tabIndex = 0; n.title = 'Rename'; n.onclick = naming; n.onkeydown = function (e) { if (e.key === 'Enter') naming(); }; }
        row.appendChild(n); row.appendChild(since);
      } else {
        row.appendChild(el('span', 'muted', 'Unnamed · ' + s.ip));
        row.appendChild(el('span', 'gap', since, canManage ? button('Name', naming) : null));
      }
      box.appendChild(row);
    });
    if (canManage) {
      box.appendChild(el('div', 'gap', button('Reload all screens', async function () {
        F.live = Object.assign(F.live || {}, (await api('POST', '/api/admin/boards/' + encodeURIComponent(b.id) + '/reload')).live);
      })));
    }
  }

  async function put(b, body) {
    showError('');
    try { load(await api('PUT', '/api/admin/boards/' + encodeURIComponent(b.id), body)); return true; }
    catch (e) { showError(e.message); render(); return false; }
  }
  function settingsCard(b) {
    const c = el('div', 'card', el('h3', '', 'Board'));
    const name = el('input'); name.value = b.name; name.maxLength = 60; name.disabled = !canManage;
    name.onchange = function () { put(b, { name: name.value }); };
    c.appendChild(field('Name', name));
    const opts = [['', 'No place']].concat(places().map(function (p) { return [p, p]; }));
    if (canManage) opts.push([NEW_PLACE, 'New place…']);
    const place = select(opts, b.place || ''); place.disabled = !canManage;
    const placeField = field('Place', place);
    place.onchange = function () {
      if (place.value !== NEW_PLACE) { put(b, { place: place.value }); return; }
      const i = el('input'); i.maxLength = 40; i.placeholder = 'Foyer'; i.setAttribute('aria-label', 'New place');
      i.onchange = function () { if (i.value.trim()) put(b, { place: i.value }); };
      place.replaceWith(i); i.focus();
    };
    c.appendChild(placeField);
    const shape = select(SHAPES, b.ratio); shape.disabled = !canManage;
    shape.onchange = async function () {
      if (!(await relayUI.confirm('Change "' + b.name + '" to ' + shape.value + '? Widgets that no longer fit are moved or taken off.'))) { shape.value = b.ratio; return; }
      const g = { '16:9': [12, 8], '4:3': [8, 6], '9:16': [6, 10] }[shape.value];
      put(b, { ratio: shape.value, cols: g[0], rows: g[1] });
    };
    c.appendChild(field('Shape', shape));
    const sw = el('label', 'switch'); const cb = el('input'); cb.type = 'checkbox'; cb.checked = !!b.public; cb.disabled = !canManage;
    cb.setAttribute('aria-label', b.name + ' is public');
    sw.appendChild(cb); sw.appendChild(el('span', 'slider'));
    cb.onchange = async function () {
      if (cb.checked && !(await relayUI.confirm('Anyone who can reach Relay, on the viewer network too, will be able to open "' + b.name + '". Continue?'))) { cb.checked = false; return; }
      put(b, { public: cb.checked });
    };
    c.appendChild(el('div', 'setrow', el('span', '', 'Public', el('span', 'hint', 'Anyone who can reach Relay, on the viewer network too.')), sw));
    const acts = el('div', 'gap'); acts.style.marginTop = 'var(--sp-3)';
    if (canManage) acts.appendChild(button('Duplicate', async function () { const out = await api('POST', '/api/admin/boards/' + encodeURIComponent(b.id) + '/duplicate'); sel = out.saved; load(out); }));
    acts.appendChild(button('Export', async function () {
      const data = await api('GET', '/api/admin/boards/' + encodeURIComponent(b.id) + '/export');
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
      const link = el('a'); link.href = url; link.download = b.id + '.relayboard.json'; document.body.appendChild(link); link.click(); link.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    }));
    if (canManage) acts.appendChild(button('Delete', async function () {
      if (!(await relayUI.confirm('Delete the board "' + b.name + '"? Its address stops working.'))) return;
      sel = ''; load(await api('DELETE', '/api/admin/boards/' + encodeURIComponent(b.id)));
    }, 'danger'));
    c.appendChild(acts);
    return c;
  }

  /* ---- editing in place */
  function palette() {
    const out = [];
    (F.catalogue || []).forEach(function (d) {
      const badge = d.module ? 'module' : '';
      if (d.presets && d.presets.length) {
        d.presets.forEach(function (p) {
          out.push({ group: d.group, badge: badge, name: p.name, title: p.name, kind: d.kind, type: d.type, w: d.w, h: d.h,
            props: Object.assign(defaults(d), p.props), style: Object.assign({}, d.style, p.style) });
        });
      } else {
        out.push({ group: d.group, badge: badge, name: d.name, title: d.name, kind: d.kind, type: d.type, w: d.w, h: d.h,
          props: defaults(d), style: Object.assign({}, d.style) });
      }
    });
    return out.sort(function (a, b) { return (a.badge ? 1 : 0) - (b.badge ? 1 : 0); });
  }
  function fieldsFor(it) {
    const d = cat()[it.type];
    if (!d) return [{ type: 'note', label: 'This widget\'s module is off. It comes back when the module is switched on.' }];
    return d.fields.map(function (f) { return { key: f.key, label: f.label, type: f.type, options: f.options, max: f.max }; });
  }
  function bare(items) {
    return items.map(function (it) { return { id: it.id, type: it.type, x: it.x, y: it.y, w: it.w, h: it.h, style: it.style, props: it.props }; });
  }
  function dirtyEdit() {
    const b = boardOf(sel);
    if (!ed || !work || !b) return false;
    keepWork();
    return JSON.stringify(work.map(function (p) { return bare(p.items); })) !== JSON.stringify(b.pages.map(function (p) { return bare(p.items); }))
      || work.length !== b.pages.length;
  }
  function keepWork() {
    if (!ed || !work) return;
    const pg = work.filter(function (p) { return p.id === workPage; })[0];
    if (pg) pg.items = ed.get().items;
  }
  function renderEdit(main, b) {
    work = b.pages.map(function (p) { return { id: p.id, name: p.name, items: forPage(p.items) }; });
    workPage = tab && work.some(function (p) { return p.id === tab; }) ? tab : work[0].id;
    const tabs = el('div', 'tabs'); tabs.setAttribute('role', 'tablist');
    function barFor() {
      const p = work.filter(function (x) { return x.id === workPage; })[0];
      return { title: 'Editing page ' + (work.indexOf(p) + 1) + ' · ' + p.name, note: 'Screens keep the saved version until you save.' };
    }
    function drawTabs() {
      tabs.textContent = '';
      work.forEach(function (p, n) {
        const t = button((n + 1) + ' · ' + p.name, function () {
          keepWork(); workPage = p.id; tab = p.id;
          ed.set({ cols: b.cols, rows: b.rows, items: p.items }); ed.setBar(barFor()); drawTabs(); feeds.refresh();
        }, p.id === workPage ? 'on' : '');
        t.setAttribute('role', 'tab'); t.setAttribute('aria-selected', String(p.id === workPage));
        tabs.appendChild(t);
      });
      if (work.length < (F.max_pages || 12)) {
        tabs.appendChild(button('+ Page', function () {
          keepWork();
          let n = work.length + 1, id = 'p' + n;
          while (work.some(function (p) { return p.id === id; })) { n += 1; id = 'p' + n; }
          work.push({ id: id, name: 'Page ' + (work.length + 1), items: [] });
          workPage = id; ed.set({ cols: b.cols, rows: b.rows, items: [] }); ed.setBar(barFor()); drawTabs();
        }));
      }
    }
    const holder = el('div'); main.appendChild(holder);
    const first = work.filter(function (p) { return p.id === workPage; })[0];
    ed = R.edit(holder, {
      title: b.name, noun: 'page', paletteName: 'Widgets', cols: b.cols, rows: b.rows, ratio: b.ratio.replace(':', '/'),
      items: first.items, palette: palette(), fields: fieldsFor, swatches: F.swatches || [],
      bar: barFor(), between: tabs, tray: true, card: true, canvasClass: 'bd-tv bd-edit', hintText: HINT,
      onSave: async function (st) {
        const pg = work.filter(function (p) { return p.id === workPage; })[0]; pg.items = st.items;
        const out = await api('PUT', '/api/admin/boards/' + encodeURIComponent(b.id), {
          pages: work.map(function (p) { return { id: p.id, name: p.name, items: bare(p.items) }; }) });
        ed = null; view = 'sel'; load(out);
      },
      onCancel: function () { ed = null; view = 'sel'; render(); },
      onDraw: function (ns) {
        if (!holder.isConnected) return;   // the editor that just saved or cancelled, already replaced
        wire(ns);
        const g = holder.querySelector('.bd-edit');
        if (g) g.style.setProperty('--rl-gap', gapFor(g) + 'px');
        if (feeds) feeds.refresh();
      }
    });
    drawTabs();
    ed.enter();
    startFeeds(b, function () { return ed ? ed.get().items : []; });
  }

  /* ---- pages and what decides the page shown */
  function renderPages(main, b) {
    const follow = (F.follow || {})[rot.rotation.follow.source];
    const pageOpts = function (extra) {
      return rot.pages.map(function (p, n) { return [p.id, (n + 1) + ' · ' + p.name]; }).concat(extra || []);
    };
    const wrap = el('div', 'stack');
    // Pages
    const pc = el('div', 'card', el('h3', '', 'Pages'), el('p', 'lede', 'A board can hold several pages. Each page is its own layout on the same grid.'));
    const strip = el('div', 'pages');
    const showing = nowPage(b);
    rot.pages.forEach(function (p, n) {
      const card = el('button', 'pg' + (p.id === rotPage ? ' on' : '')); card.type = 'button';
      card.appendChild(thumb(p.items, b));
      card.appendChild(el('b', '', (n + 1) + ' · ' + p.name));
      card.appendChild(el('small', '', plural(p.items.length, 'widget', 'widgets') + (p.id === showing ? ' · showing now' : '')));
      card.onclick = function () { rotPage = p.id; render(); };
      strip.appendChild(card);
    });
    if (rot.pages.length < (F.max_pages || 12)) {
      const add = el('button', 'pg add', '+ Add page'); add.type = 'button';
      add.onclick = function () {
        let n = rot.pages.length + 1, id = 'p' + n;
        while (rot.pages.some(function (p) { return p.id === id; })) { n += 1; id = 'p' + n; }
        rot.pages.push({ id: id, name: 'Page ' + (rot.pages.length + 1), items: [] }); rotPage = id; render();
      };
      strip.appendChild(add);
    }
    pc.appendChild(strip);
    const cur = rot.pages.filter(function (p) { return p.id === rotPage; })[0];
    if (cur) {
      const idx = rot.pages.indexOf(cur);
      const nm = el('input'); nm.value = cur.name; nm.maxLength = 40;
      nm.oninput = function () { cur.name = nm.value; };
      nm.onchange = function () { render(); };
      const ed2 = el('div', 'pgedit', field('Page ' + (idx + 1) + ' name', nm));
      ed2.appendChild(button('Move left', function () { rot.pages.splice(idx, 1); rot.pages.splice(idx - 1, 0, cur); render(); }));
      ed2.lastChild.disabled = idx === 0;
      ed2.appendChild(button('Move right', function () { rot.pages.splice(idx, 1); rot.pages.splice(idx + 1, 0, cur); render(); }));
      ed2.lastChild.disabled = idx === rot.pages.length - 1;
      ed2.appendChild(button('Delete page', async function () {
        if (cur.items.length && !(await relayUI.confirm('Delete page ' + (idx + 1) + ' and its ' + plural(cur.items.length, 'widget', 'widgets') + '?'))) return;
        rot.pages.splice(idx, 1); rotPage = rot.pages[0].id; render();
      }, 'danger'));
      ed2.lastChild.disabled = rot.pages.length === 1;
      pc.appendChild(ed2);
    }
    wrap.appendChild(pc);

    // What decides the page
    const r = rot.rotation;
    const dc = el('div', 'card', el('h3', '', 'What decides the page shown'));
    const modes = el('div', 'modes');
    [['one', 'One page', 'Always page 1.'], ['rotate', 'Rotate', 'Every N seconds, in order.'],
      ['schedule', 'Schedule', 'A page for each time of day and day of week.'],
      ['follow', 'Follow ProPresenter', follow ? 'Change page when a presentation or slide group comes up.' : 'Switch the ProPresenter module on to use this.']].forEach(function (m) {
      const bt = el('button', 'mode' + (r.mode === m[0] ? ' on' : ''), el('b', '', m[1]), el('span', '', m[2])); bt.type = 'button';
      bt.setAttribute('aria-pressed', String(r.mode === m[0]));
      bt.disabled = m[0] === 'follow' && !follow && r.mode !== 'follow';
      bt.onclick = function () { r.mode = m[0]; render(); };
      modes.appendChild(bt);
    });
    dc.appendChild(modes);
    function sec(title, mode) {
      const h = el('h4', 'rules-sec', title);
      if (r.mode !== mode && !(mode === 'rotate' && r.schedule.some(function (x) { return x.show === 'rotate'; }))) {
        h.appendChild(document.createTextNode(' ')); h.appendChild(chip('used when mode is ' + ({ rotate: 'Rotate', schedule: 'Schedule', follow: 'Follow ProPresenter' })[mode]));
      }
      return h;
    }
    // Rotation
    dc.appendChild(sec('Rotation', 'rotate'));
    const every = el('input'); every.type = 'number'; every.min = '5'; every.max = '3600'; every.value = r.every; every.style.width = '90px';
    every.setAttribute('aria-label', 'Seconds per page');
    every.onchange = function () { r.every = Math.max(5, Math.min(3600, +every.value || 20)); };
    const rotRow = el('div', 'gap', el('span', 'muted', 'Every'), every, el('span', 'muted', 'seconds, through'));
    rot.pages.forEach(function (p, n) {
      const cb = el('input'); cb.type = 'checkbox'; cb.checked = r.pages.indexOf(p.id) >= 0;
      cb.onchange = function () {
        r.pages = rot.pages.map(function (q) { return q.id; }).filter(function (id) { return id === p.id ? cb.checked : r.pages.indexOf(id) >= 0; });
      };
      rotRow.appendChild(el('label', 'check', cb, ' ' + (n + 1) + ' · ' + p.name));
    });
    dc.appendChild(rotRow);
    // Schedule
    dc.appendChild(sec('Schedule', 'schedule'));
    const days = Object.keys(F.days || {}).map(function (k) { return [k, F.days[k]]; });
    const rotLabel = function () {
      const nums = rot.pages.map(function (p, n) { return r.pages.indexOf(p.id) >= 0 ? String(n + 1) : null; }).filter(Boolean);
      return 'Rotate ' + (nums.length > 1 ? nums.slice(0, -1).join(', ') + ' and ' + nums[nums.length - 1] : nums[0] || '') + ', every ' + r.every + ' s';
    };
    const st = el('table', 'rules');
    st.appendChild(el('thead', '', el('tr', '', el('th', 'w-days', 'Days'), el('th', 'w-time', 'From'), el('th', 'w-time', 'To'), el('th', '', 'Show'), el('th', 'w-x', ''))));
    const sb = el('tbody');
    r.schedule.forEach(function (row, i) {
      const tr = el('tr', r.mode === 'schedule' && currentRow(r.schedule) === i ? 'now' : '');
      // A row takes its own days and times, or follows a named schedule from the Schedules page (#526).
      const named = (F.schedules || []).map(function (n) { return [n.ref, 'Schedule: ' + n.name]; });
      const d = select(days.concat(named), row.schedule || row.days, 'Days');
      d.onchange = function () {
        if (d.value.indexOf('schedule:') === 0) row.schedule = d.value; else { row.schedule = ''; row.days = d.value; }
        render();
      };
      const f = el('input'); f.type = 'time'; f.value = row.from; f.setAttribute('aria-label', 'From'); f.onchange = function () { row.from = f.value; };
      const t = el('input'); t.type = 'time'; t.value = row.to; t.setAttribute('aria-label', 'To'); t.onchange = function () { row.to = t.value; };
      f.disabled = t.disabled = !!row.schedule;
      const s = select(pageOpts([['rotate', rotLabel()]]), row.show, 'Show'); s.onchange = function () { row.show = s.value; render(); };
      const x = button('×', function () { r.schedule.splice(i, 1); render(); }, 'quiet'); x.setAttribute('aria-label', 'Remove');
      tr.appendChild(el('td', '', d)); tr.appendChild(el('td', '', f)); tr.appendChild(el('td', '', t)); tr.appendChild(el('td', '', s)); tr.appendChild(el('td', '', x));
      sb.appendChild(tr);
    });
    const other = select(pageOpts([['rotate', rotLabel()]]), r.other, 'Any other time'); other.onchange = function () { r.other = other.value; };
    const otd = el('td', 'muted', 'Any other time'); otd.colSpan = 2;
    sb.appendChild(el('tr', '', el('td', 'muted', 'Every day'), otd, el('td', '', other), el('td')));
    st.appendChild(sb);
    dc.appendChild(st);
    dc.appendChild(el('div', 'gap', button('+ Time', function () {
      r.schedule.push({ days: 'all', from: '09:00', to: '10:00', schedule: '', show: rot.pages[0].id }); render();
    }), el('span', 'hint', 'Rows are read from the top; the first that covers the time decides. A row that ends before it starts runs past midnight.')));
    // Follow ProPresenter
    dc.appendChild(sec('Follow ProPresenter', 'follow'));
    const ft = el('table', 'rules');
    ft.appendChild(el('thead', '', el('tr', '', el('th', '', 'When ProPresenter shows'), el('th', '', 'Matching'), el('th', '', 'Show'), el('th', 'w-x', ''))));
    const fb = el('tbody');
    const sug = (follow && follow.suggest) || {};
    r.follow.rules.forEach(function (row, i) {
      const dl = el('datalist'); dl.id = 'bdSug' + i;
      (sug[row.match] || []).forEach(function (v) { const o = el('option'); o.value = v; dl.appendChild(o); });
      const v = el('input'); v.value = row.value; v.maxLength = 80; v.setAttribute('list', dl.id); v.setAttribute('aria-label', 'When ProPresenter shows');
      v.placeholder = { presentation: 'Walk-in loop', group: 'Sermon', label: 'ANNOUNCE' }[row.match];
      v.oninput = function () { row.value = v.value; };
      const m = select(Object.keys(F.matches || {}).map(function (k) { return [k, F.matches[k]]; }), row.match, 'Matching');
      m.onchange = function () { row.match = m.value; render(); };
      const s = select(pageOpts(), row.page, 'Show'); s.onchange = function () { row.page = s.value; };
      const x = button('×', function () { r.follow.rules.splice(i, 1); render(); }, 'quiet'); x.setAttribute('aria-label', 'Remove');
      fb.appendChild(el('tr', '', el('td', '', v, dl), el('td', '', m), el('td', '', s), el('td', '', x)));
    });
    if (!r.follow.rules.length) { const td = el('td', 'muted', 'No rules yet.'); td.colSpan = 4; fb.appendChild(el('tr', '', td)); }
    ft.appendChild(fb);
    dc.appendChild(ft);
    const nowLine = follow ? (follow.connected ? 'ProPresenter shows now: ' + ['presentation', 'group', 'label'].map(function (k) {
      return follow.now[k] ? F.matches[k].toLowerCase() + ' ' + follow.now[k] : '';
    }).filter(Boolean).join(' · ') : 'ProPresenter is not connected.') : 'The ProPresenter module is off.';
    dc.appendChild(el('div', 'gap', button('+ Rule', function () {
      r.follow.rules.push({ match: 'group', value: (follow && follow.now.group) || '', page: rot.pages[0].id }); render();
    }), el('span', 'hint', nowLine)));
    const none = select([['keep', 'Keep the current page']].concat(pageOpts()), r.follow.none, 'When nothing matches');
    none.onchange = function () { r.follow.none = none.value; };
    none.style.width = '220px';
    const foot = el('div', 'gap', el('span', 'muted', 'Nothing matches:'), none, el('span', 'sp'),
      button('Cancel', function () { go('sel'); }),
      button('Save', async function () {
        const out = await api('PUT', '/api/admin/boards/' + encodeURIComponent(b.id), {
          pages: rot.pages.map(function (p) { return { id: p.id, name: p.name, items: bare(p.items) }; }), rotation: r });
        view = 'sel'; load(out);
      }, 'primary'));
    foot.style.marginTop = 'var(--sp-3)';
    dc.appendChild(foot);
    wrap.appendChild(dc);
    main.appendChild(wrap);
  }
  // Which schedule row covers now, by the same rule as boards.schedule_row (for the highlighted row only; the server
  // decides the page).
  function currentRow(rows) {
    // Days count from Sunday = 0 (app/timewin.py), as getDay() does.
    const now = new Date(), m = now.getHours() * 60 + now.getMinutes(), wd = now.getDay();
    const num = { sun: 0, mon: 1, tue: 2, wed: 3, thu: 4, fri: 5, sat: 6 };
    const mins = function (s) { const p = s.split(':'); return +p[0] * 60 + +p[1]; };
    for (let i = 0; i < rows.length; i++) {
      if (rows[i].schedule) continue;       // a named schedule is open or not by the server's reckoning
      const a = mins(rows[i].from), z = mins(rows[i].to);
      let hit, day = wd;
      if (a === z) hit = true; else if (a < z) hit = a <= m && m < z; else { hit = m >= a || m < z; day = m >= a ? wd : (wd + 6) % 7; }
      const dd = rows[i].days;
      const okDay = dd === 'all' || (dd === 'weekdays' && day >= 1 && day <= 5) || (dd === 'weekends' && (day === 0 || day === 6)) || num[dd] === day;
      if (hit && okDay) return i;
    }
    return -1;
  }

  /* ---- a new board */
  function renderNew(main) {
    const d = el('div', 'dlg stack');
    d.appendChild(el('h3', '', 'New board'));
    const tp = el('div', 'tpls');
    const c = cat();
    F.templates.forEach(function (t) {
      const g = { '16:9': [12, 8], '4:3': [8, 6], '9:16': [6, 10] }[t.ratio];
      const bt = el('button', 'tpl' + (fresh.template === t.id ? ' on' : '')); bt.type = 'button';
      bt.setAttribute('aria-pressed', String(fresh.template === t.id));
      const items = t.items.map(function (it) { return Object.assign({}, it, { style: Object.assign({}, (c[it.type] || {}).style, it.style) }); });
      bt.appendChild(thumb(items, { ratio: t.ratio, cols: g[0], rows: g[1] }, t.items.length ? '' : 'bd-blank'));
      bt.appendChild(el('b', '', t.name)); bt.appendChild(el('span', '', t.about));
      bt.onclick = function () { fresh.template = t.id; fresh.ratio = ''; render(); };
      tp.appendChild(bt);
    });
    d.appendChild(tp);
    const tpl = F.templates.filter(function (t) { return t.id === fresh.template; })[0] || {};
    const name = el('input'); name.value = fresh.name; name.maxLength = 60; name.placeholder = tpl.name || 'Lobby';
    const hint = el('span', 'hint');
    function addr() {
      hint.textContent = '';
      hint.appendChild(document.createTextNode('Address: ' + location.host + '/board/'));
      hint.appendChild(el('b', '', slugFor(name.value || name.placeholder)));
      hint.appendChild(document.createTextNode('. It never changes, so a TV keeps its link through a rename.'));
    }
    name.oninput = function () { fresh.name = name.value; addr(); };
    const nf = field('Name', name); nf.style.flex = '1'; nf.style.minWidth = '180px';
    const popts = [['', 'No place']].concat(places().map(function (p) { return [p, p]; })).concat([[NEW_PLACE, 'New place…']]);
    let place = select(popts, fresh.place);
    const pf = field('Place', place); pf.style.width = '150px';
    place.onchange = function () {
      if (place.value !== NEW_PLACE) { fresh.place = place.value; return; }
      const i = el('input'); i.maxLength = 40; i.placeholder = 'Foyer'; i.setAttribute('aria-label', 'New place');
      i.oninput = function () { fresh.place = i.value; };
      place.replaceWith(i); place = i; i.focus();
    };
    const shape = select(SHAPES, fresh.ratio || tpl.ratio || '16:9');
    shape.onchange = function () { fresh.ratio = shape.value; };
    const sf = field('Shape', shape); sf.style.width = '140px';
    const row = el('div', 'gap', nf, pf, sf); row.style.alignItems = 'flex-end';
    d.appendChild(row);
    addr(); d.appendChild(hint);
    d.appendChild(el('div', 'gap', el('span', 'sp'), button('Cancel', function () { go('sel'); }), button('Make board', async function () {
      const out = await api('POST', '/api/admin/boards', { name: name.value || name.placeholder, place: fresh.place,
        ratio: fresh.ratio || tpl.ratio || '16:9', template: fresh.template });
      sel = out.saved; tab = ''; view = 'edit'; load(out);
    }, 'primary')));
    main.appendChild(d);
    name.focus();
  }

  /* ---- run */
  function renderRun(main, b) {
    const box = el('div', 'stack');
    box.appendChild(el('p', 'lede', 'Press any button on this board from here. Displays are left out.' +
      (canManage ? ' A page chosen here holds until what decides the page changes.' : '')));
    if (canManage && b.pages.length > 1) {
      const l = liveOf(b.id), showing = nowPage(b);
      box.appendChild(el('h4', '', 'Page'));
      const row = el('div', 'gap');
      b.pages.forEach(function (p) {
        row.appendChild(button(pageLabel(b, p), async function () {
          const out = await api('POST', '/api/admin/boards/' + encodeURIComponent(b.id) + '/page', { page: p.id });
          F.live = out.live; offset = out.now - Date.now() / 1000; render();
        }, p.id === showing ? 'primary' : ''));
      });
      if (l.override) row.appendChild(button('Back to automatic', async function () {
        const out = await api('POST', '/api/admin/boards/' + encodeURIComponent(b.id) + '/page', { page: '' });
        F.live = out.live; render();
      }, 'quiet'));
      box.appendChild(row);
      if (l.why) box.appendChild(el('span', 'hint', l.why));
    }
    box.appendChild(el('h4', '', 'Buttons'));
    const c = cat();
    const items = forPage(allItems(b)).filter(function (it) { return it.kind === 'button'; });
    const g = el('div', 'ctrlg');
    const ns = {};
    items.forEach(function (it) {
      const d = c[it.type];
      if (it.type === 'link') {
        const to = boardOf((it.props || {}).board);
        g.appendChild(button('Board: ' + (to ? to.name : '?') + ' →', function () { if (to) choose(to.id); }));
        return;
      }
      const label = (it.style && it.style.label) || it.title || (d ? d.name : 'Off');
      const bt = button(label, async function () {
        try {
          const res = await fetch('/board/' + encodeURIComponent(b.id) + '/_/press/' + encodeURIComponent(it.id), { method: 'POST', credentials: 'same-origin' });
          if (!res.ok) { const out = await res.json().catch(function () { return {}; }); showError(out.error || 'That did not work.'); }
        } catch (e) { showError('Relay did not answer.'); }
      });
      bt.disabled = !d || (d.press_permission && !relayUI.can(d.press_permission));
      ns[it.id] = bt;   // lit while the widget is ({lit} from its module)
      g.appendChild(bt);
    });
    if (!g.childNodes.length) box.appendChild(el('p', 'hint', 'This board has no buttons.'));
    else box.appendChild(g);
    main.appendChild(box);
    Object.keys(ns).forEach(function (id) { if (liveData[id]) ns[id].classList.toggle('lit', !!liveData[id].lit); });
    const lit = function (id, d) {
      liveData[id] = Object.assign(liveData[id] || {}, d);
      if (ns[id] && d.lit !== undefined) ns[id].classList.toggle('lit', !!d.lit);
    };
    stopFeeds();
    feeds = RelayBoardLive.start({ base: '/board/' + encodeURIComponent(b.id), items: function () { return []; }, apply: lit });
  }

  /* ---------------------------------------------------------------- loading and keeping live */
  function load(d) {
    F = Object.assign(F || {}, d);
    if (typeof d.now === 'number') offset = d.now - Date.now() / 1000;
    render();
  }
  async function refresh() {
    try { load(await api('GET', '/api/admin/boards')); } catch (e) { showError(e.message); }
  }
  // The live parts (screens, which page shows and why) change without anyone here; redraw only those.
  async function poll() {
    clearTimeout(pollTimer);
    if (location.hash !== '#boards') return;
    if (!document.hidden && F) {
      try {
        const got = await api('GET', '/api/admin/boards/live');
        const before = JSON.stringify(F.live);
        F.live = got.live; F.follow = got.follow; offset = got.now - Date.now() / 1000;
        if (JSON.stringify(F.live) !== before) {
          renderList();
          const b = boardOf(sel);
          if (b && view === 'sel') { drawStatus(b); drawScreens(b); }
        }
      } catch (e) { /* the next tick tries again */ }
    }
    pollTimer = setTimeout(poll, 3000);
  }
  // A rotation turns on its own: keep the "showing now" line and the list's pictures in step, redrawing only when a
  // page turns (a redraw on every tick would lose a click that lands during it).
  let turned = '';
  setInterval(function () {
    if (location.hash !== '#boards' || !F) return;
    const now = F.boards.map(function (b) { return b.id + ':' + nowPage(b); }).join(',');
    if (now === turned) return;
    turned = now;
    renderList();
    const b = boardOf(sel);
    if (b && view === 'sel') drawStatus(b);
  }, 1000);

  $('bdImport').onchange = async function () {
    const f = $('bdImport').files[0]; $('bdImport').value = '';
    if (!f) return;
    showError('');
    try {
      if (f.size > 512 * 1024) throw new Error('That file is too big to be a board.');
      let data;
      try { data = JSON.parse(await f.text()); } catch (e) { throw new Error('That is not a Relay board file.'); }
      const out = await api('POST', '/api/admin/boards/import', data);
      sel = out.saved; view = 'sel'; load(out);
    } catch (e) { showError(e.message); }
  };
  window.addEventListener('beforeunload', function (e) { if (view === 'edit' && dirtyEdit()) { e.preventDefault(); e.returnValue = ''; } });
  window.addEventListener('hashchange', function () {
    if (location.hash === '#boards') { refresh(); poll(); } else { stopFeeds(); clearTimeout(pollTimer); }
  });
  if (location.hash === '#boards') { refresh(); poll(); }
})();
