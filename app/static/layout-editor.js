/* The shared layout editor (#421): lay items out on a grid. Boards (#156) and the lighting module's cue pages use it;
   so should every later surface (panel buttons #223, crew pages). Needs layout-render.js and layout.css.

   const ed = RelayLayout.edit(host, {
     title: 'Lobby', noun: 'board', paletteName: 'Widgets',
     cols: 12, rows: 8, ratio: '16/9', sizes: [[12, 8], [16, 9]],      // grid sizes the toolbar offers
     items: [...],                     // {id, kind, type, title, x, y, w, h, style, props}
     palette: [...],                   // {group, name, kind, type, w, h, props, style, badge, disabled}
     fields: function (item) {...},    // the owner's settings for an item: [{key, label, type, options, max}]
     inline: false,                    // true: always editing, palette beside the grid (an admin page)
     addById: {label, placeholder, make: function (id) { return paletteEntry; }},
     onSave: async function (state) {}, // state = {cols, rows, items}; throw to keep editing
     onCancel: function () {},
   });
   ed.get() -> {cols, rows, items}; ed.set(state); ed.enter(); ed.redraw();

   Places are whole cells and items never overlap: a move or resize onto another item is refused, as app/layout.py
   refuses it on save. Drag to move, drag an edge or corner to resize, double-click or Enter for settings, arrows move,
   Shift+arrows resize, Delete removes, Ctrl/Cmd+Z undoes. Every value is set as text; the server checks it again. */
(function () {
  'use strict';
  const R = window.RelayLayout;
  if (!R) return;
  let uid = 0;
  const el = relayUI.el;
  function btn(text, cls, fn) {
    const b = el('button', cls, text); b.type = 'button';
    if (fn) b.onclick = fn;
    return b;
  }
  function copy(v) { return JSON.parse(JSON.stringify(v)); }
  function newId() { uid += 1; return 'n' + Date.now().toString(36) + uid; }

  function edit(host, opts) {
    const st = { cols: opts.cols, rows: opts.rows, items: copy(opts.items || []), sel: null, lines: true };
    let undo = [], redo = [], editing = !!opts.inline, saved = null, busy = false;
    const root = el('div', 'rl-ed' + (opts.inline ? ' rl-inline' : ''));
    const tools = el('div', 'rl-tools');
    const body = el('div', 'rl-body');
    const stage = el('div', 'rl-stage');
    const grid = el('div', 'rl-grid rl-canvas');
    const pal = el('div', 'rl-pal'); const palIn = el('div', 'rl-palin'); pal.appendChild(palIn);
    const msg = el('div', 'rl-msg'); msg.setAttribute('role', 'status'); msg.setAttribute('aria-live', 'polite');
    const hint = el('div', 'rl-hint');
    stage.appendChild(grid); body.appendChild(stage); body.appendChild(pal);
    root.appendChild(tools); root.appendChild(body); root.appendChild(msg); root.appendChild(hint);
    host.appendChild(root);

    function snap() { return JSON.stringify({ c: st.cols, r: st.rows, i: st.items }); }
    function load(s) { const o = JSON.parse(s); st.cols = o.c; st.rows = o.r; st.items = o.i; }
    function push(before) { if (before !== snap()) { undo.push(before); redo = []; } drawTools(); }
    function hits(a, x, y, w, h) {
      return st.items.some(function (b) { return b !== a && x < b.x + b.w && b.x < x + w && y < b.y + b.h && b.y < y + h; });
    }
    function freeSlot(w, h) {
      for (let y = 0; y + h <= st.rows; y++) for (let x = 0; x + w <= st.cols; x++) if (!hits(null, x, y, w, h)) return { x: x, y: y };
      return null;
    }
    function cellAt(ev) {
      const r = grid.getBoundingClientRect();
      return { x: Math.max(0, Math.min(st.cols - 1, Math.floor((ev.clientX - r.left) / r.width * st.cols))),
               y: Math.max(0, Math.min(st.rows - 1, Math.floor((ev.clientY - r.top) / r.height * st.rows))) };
    }
    function say(text) { msg.textContent = text || ''; msg.hidden = !text; }
    function find(id) { return st.items.filter(function (i) { return i.id === id; })[0]; }

    // On a phone the list would cover the board, so it starts closed there; the toolbar opens it.
    function enter() { editing = true; saved = snap(); undo = []; redo = []; say(''); pal.classList.toggle('rl-open', window.innerWidth > 760); draw(); }
    function leave() { editing = !!opts.inline; st.sel = null; pal.classList.remove('rl-open'); draw(); }
    async function save() {
      if (busy) return;
      busy = true; say(''); drawTools();
      try {
        if (opts.onSave) await opts.onSave({ cols: st.cols, rows: st.rows, items: copy(st.items) });
        saved = snap(); undo = []; redo = [];
        if (!opts.inline) leave(); else say('Saved.');
      } catch (e) { say(e && e.message ? e.message : 'That did not save.'); }
      busy = false; drawTools();
    }
    function cancel() {
      if (saved) load(saved);
      leave();
      if (opts.onCancel) opts.onCancel();
    }

    function drawTools() {
      tools.textContent = '';
      tools.hidden = !editing; hint.hidden = !editing;
      if (!editing) return;
      tools.appendChild(el('span', 'rl-name', opts.title || ''));
      if (opts.sizes && opts.sizes.length) {
        const sz = el('select'); sz.setAttribute('aria-label', 'Grid size');
        let found = false;
        opts.sizes.concat([[st.cols, st.rows]]).forEach(function (s) {
          const v = s[0] + 'x' + s[1];
          if (sz.querySelector('option[value="' + v + '"]')) return;
          const o = el('option', '', s[0] + ' × ' + s[1] + ' cells'); o.value = v;
          if (s[0] === st.cols && s[1] === st.rows && !found) { o.selected = true; found = true; }
          sz.appendChild(o);
        });
        sz.onchange = function () { resize(sz.value.split('x').map(Number)); };
        tools.appendChild(sz);
      }
      tools.appendChild(btn(st.lines ? 'Hide grid' : 'Show grid', 'rl-ghost', function () { st.lines = !st.lines; draw(); }));
      const u = btn('Undo', 'rl-ghost', doUndo); u.disabled = !undo.length; tools.appendChild(u);
      const r = btn('Redo', 'rl-ghost', doRedo); r.disabled = !redo.length; tools.appendChild(r);
      tools.appendChild(el('span', 'rl-spacer'));
      if (!opts.inline) {
        tools.appendChild(btn(pal.classList.contains('rl-open') ? 'Hide ' + opts.paletteName.toLowerCase() : opts.paletteName, '',
          function () { pal.classList.toggle('rl-open'); drawTools(); }));
        tools.appendChild(btn('Cancel', '', cancel));
      }
      const s = btn(busy ? 'Saving…' : (opts.saveLabel || 'Save'), 'primary', save); s.disabled = busy; tools.appendChild(s);
    }
    function resize(v) {
      const before = snap();
      // Shrinking keeps every item that still fits, moving those that would hang over the edge.
      const items = st.items; st.cols = v[0]; st.rows = v[1]; st.items = [];
      items.forEach(function (i) {
        i.w = Math.min(i.w, st.cols); i.h = Math.min(i.h, st.rows);
        i.x = Math.min(i.x, st.cols - i.w); i.y = Math.min(i.y, st.rows - i.h);
        if (hits(null, i.x, i.y, i.w, i.h)) { const p = freeSlot(i.w, i.h) || (i.w = i.h = 1, freeSlot(1, 1)); if (!p) return; i.x = p.x; i.y = p.y; }
        st.items.push(i);
      });
      if (st.items.length < items.length) say((items.length - st.items.length) + ' did not fit and were taken off. Undo brings them back.');
      push(before); draw();
    }
    function doUndo() { if (!undo.length) return; redo.push(snap()); load(undo.pop()); st.sel = null; draw(); }
    function doRedo() { if (!redo.length) return; undo.push(snap()); load(redo.pop()); st.sel = null; draw(); }

    function drawPalette() {
      palIn.textContent = '';
      palIn.appendChild(el('div', 'rl-pcat', 'Click to add, or drag onto the ' + (opts.noun || 'page')));
      const q = el('input'); q.type = 'search'; q.placeholder = 'Search ' + opts.paletteName.toLowerCase();
      q.setAttribute('aria-label', 'Search ' + opts.paletteName.toLowerCase());
      palIn.appendChild(q);
      const list = el('div'); palIn.appendChild(list);
      function fill() {
        list.textContent = ''; let group = null;
        const want = q.value.trim().toLowerCase();
        (opts.palette || []).forEach(function (p, idx) {
          if (want && p.name.toLowerCase().indexOf(want) < 0) return;
          if (p.group !== group) {
            group = p.group; const c = el('div', 'rl-pcat', group || '');
            if (p.badge) c.appendChild(el('span', 'rl-badge', p.badge));
            list.appendChild(c);
          }
          const b = btn('', 'rl-pitem', function () { add(p); });
          const sw = el('span', 'rl-sw'); sw.style.background = R.look(p).bg; b.appendChild(sw);
          b.appendChild(el('span', 'rl-pname', p.name));
          b.appendChild(el('small', '', p.w + '×' + p.h));
          b.disabled = !!p.disabled; b.draggable = !p.disabled;
          b.ondragstart = function (e) { e.dataTransfer.setData('text/plain', 'rl:' + idx); };
          list.appendChild(b);
        });
        if (!list.childNodes.length) list.appendChild(el('div', 'rl-note', want ? 'Nothing matches.' : 'Nothing to add.'));
        if (opts.addById) {
          list.appendChild(el('div', 'rl-pcat', opts.addById.label));
          const row = el('div', 'rl-row'); const i = el('input'); i.placeholder = opts.addById.placeholder || ''; i.maxLength = 64;
          i.setAttribute('aria-label', opts.addById.label);
          row.appendChild(i);
          row.appendChild(btn('Add', '', function () { const v = i.value.trim(); if (v) { add(opts.addById.make(v)); i.value = ''; } }));
          list.appendChild(row);
        }
      }
      q.oninput = fill; fill();
    }
    function add(p, at) {
      let w = Math.min(p.w || 1, st.cols), h = Math.min(p.h || 1, st.rows);
      let pos = null;
      if (at) { const x = Math.min(at.x, st.cols - w), y = Math.min(at.y, st.rows - h); if (!hits(null, x, y, w, h)) pos = { x: x, y: y }; }
      pos = pos || freeSlot(w, h);
      let note = '';
      if (!pos) { w = 1; h = 1; pos = freeSlot(1, 1); note = 'There was no room for its full size, so it was added as one cell. Drag its corner to make it bigger.'; }
      if (!pos) { say('The ' + (opts.noun || 'page') + ' is full: make something smaller or the grid bigger.'); return; }
      const before = snap();
      const it = { id: newId(), kind: p.kind, type: p.type, title: p.title || p.name, x: pos.x, y: pos.y, w: w, h: h,
        style: copy(p.style || {}), props: copy(p.props || {}) };
      st.items.push(it); st.sel = it.id; say(note); push(before); draw(); focusSel();
    }
    grid.addEventListener('dragover', function (e) { if (editing) e.preventDefault(); });
    grid.addEventListener('drop', function (e) {
      e.preventDefault();
      const m = /^rl:(\d+)$/.exec(e.dataTransfer.getData('text/plain') || '');
      const p = m && (opts.palette || [])[+m[1]];
      if (p && !p.disabled) add(p, cellAt(e));
    });

    function draw() {
      root.classList.toggle('rl-editing', editing);
      grid.classList.toggle('rl-nolines', !st.lines);
      R.grid(grid, { cols: st.cols, rows: st.rows, gap: opts.gap });
      grid.style.aspectRatio = opts.ratio || '';
      grid.textContent = '';
      if (editing) {
        for (let y = 0; y < st.rows; y++) for (let x = 0; x < st.cols; x++) {
          const c = el('div', 'rl-gcell'); c.style.gridArea = (y + 1) + '/' + (x + 1); grid.appendChild(c);
        }
      }
      const nodes = {};
      st.items.forEach(function (it) {
        const cell = el('div', 'rl-cell'); cell.dataset.id = it.id; R.area(cell, it);
        const w = R.item(it); cell.appendChild(w); nodes[it.id] = w;
        if (editing) {
          w.tabIndex = -1;
          cell.tabIndex = 0;
          cell.setAttribute('aria-label', (it.title || it.kind) + ', ' + it.w + ' by ' + it.h + ' cells');
          cell.onpointerdown = function (ev) {
            if (ev.button !== 0 || ev.target.classList.contains('rl-h') || ev.target.closest('.rl-mini')) return;
            startMove(ev, it, cell);
          };
          cell.ondblclick = function () { settings(it); };
          cell.onkeydown = function (ev) { key(ev, it); };
          cell.onfocus = function () { if (st.sel !== it.id) { st.sel = it.id; draw(); focusSel(); } };
          if (st.sel === it.id) decorate(cell, it);
        }
        grid.appendChild(cell);
      });
      drawTools();
      if (editing) drawPalette();
      hint.textContent = '';
      if (editing) {
        [['Drag', 'to move'], ['Edges and corners', 'resize'], ['Double-click', 'settings'], ['Arrows', 'move'],
         ['Shift+arrows', 'resize'], ['Delete', 'removes'], ['Ctrl+Z', 'undo']].forEach(function (k, i) {
          if (i) hint.appendChild(document.createTextNode(' · '));
          hint.appendChild(el('kbd', '', k[0])); hint.appendChild(document.createTextNode(' ' + k[1]));
        });
      }
      if (opts.onDraw) opts.onDraw(nodes, editing);
    }
    function decorate(cell, it) {
      cell.classList.add('rl-sel');
      if (it.y === 0) cell.classList.add('rl-top');
      ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'].forEach(function (d) {
        const h = el('span', 'rl-h'); h.dataset.d = d; h.onpointerdown = function (ev) { startResize(ev, it, cell, d); };
        cell.appendChild(h);
      });
      const mb = el('div', 'rl-mini');
      [['Settings', function () { settings(it); }], ['Duplicate', function () { dup(it); }], ['Delete', function () { del(it); }]].forEach(function (a) {
        const b = btn(a[0], '', function (e) { e.stopPropagation(); a[1](); }); mb.appendChild(b);
      });
      cell.appendChild(mb);
    }
    function focusSel() {
      const n = st.sel && grid.querySelector('.rl-cell[data-id="' + CSS.escape(st.sel) + '"]');
      if (n) n.focus({ preventScroll: true });
    }
    function startMove(ev, it, cell) {
      ev.preventDefault();
      if (st.sel !== it.id) { st.sel = it.id; draw(); cell = grid.querySelector('.rl-cell[data-id="' + CSS.escape(it.id) + '"]'); }
      cell.focus({ preventScroll: true });
      const before = snap(); const c0 = cellAt(ev); const ox = c0.x - it.x, oy = c0.y - it.y;
      try { cell.setPointerCapture(ev.pointerId); } catch (e) { /* a synthetic event has no pointer to capture */ }
      cell.classList.add('rl-drag');
      cell.onpointermove = function (e) {
        const c = cellAt(e);
        const nx = Math.max(0, Math.min(st.cols - it.w, c.x - ox)), ny = Math.max(0, Math.min(st.rows - it.h, c.y - oy));
        const bad = hits(it, nx, ny, it.w, it.h); cell.classList.toggle('rl-bad', bad);
        if (!bad && (nx !== it.x || ny !== it.y)) { it.x = nx; it.y = ny; R.area(cell, it); }
      };
      cell.onpointerup = cell.onpointercancel = function () {
        cell.onpointermove = null; cell.onpointerup = null; cell.onpointercancel = null; push(before); draw(); focusSel();
      };
    }
    function startResize(ev, it, cell, d) {
      ev.preventDefault(); ev.stopPropagation();
      const before = snap(); const h = ev.target;
      try { h.setPointerCapture(ev.pointerId); } catch (e) { /* see startMove */ }
      const x2 = it.x + it.w, y2 = it.y + it.h;
      h.onpointermove = function (e) {
        const c = cellAt(e); let x = it.x, y = it.y, w = it.w, hh = it.h;
        if (d.indexOf('e') >= 0) w = Math.max(1, c.x - x + 1);
        if (d.indexOf('s') >= 0) hh = Math.max(1, c.y - y + 1);
        if (d.indexOf('w') >= 0) { x = Math.min(c.x, x2 - 1); w = x2 - x; }
        if (d.indexOf('n') >= 0) { y = Math.min(c.y, y2 - 1); hh = y2 - y; }
        if (!hits(it, x, y, w, hh)) { it.x = x; it.y = y; it.w = w; it.h = hh; R.area(cell, it); }
      };
      h.onpointerup = h.onpointercancel = function () { h.onpointermove = null; h.onpointerup = null; push(before); draw(); focusSel(); };
    }
    function key(ev, it) {
      if (ev.target !== ev.currentTarget) return;
      const mod = ev.metaKey || ev.ctrlKey;
      if (mod && ev.key.toLowerCase() === 'z') { ev.preventDefault(); if (ev.shiftKey) doRedo(); else doUndo(); return; }
      if (mod && ev.key.toLowerCase() === 'y') { ev.preventDefault(); doRedo(); return; }
      if (ev.key === 'Delete' || ev.key === 'Backspace') { ev.preventDefault(); del(it); return; }
      if (ev.key === 'Enter') { ev.preventDefault(); settings(it); return; }
      if (ev.key === 'Escape') { st.sel = null; draw(); return; }
      const dx = { ArrowLeft: -1, ArrowRight: 1 }[ev.key] || 0, dy = { ArrowUp: -1, ArrowDown: 1 }[ev.key] || 0;
      if (!dx && !dy) return;
      ev.preventDefault();
      const before = snap();
      if (ev.shiftKey) {
        const w = it.w + dx, h = it.h + dy;
        if (w >= 1 && h >= 1 && it.x + w <= st.cols && it.y + h <= st.rows && !hits(it, it.x, it.y, w, h)) { it.w = w; it.h = h; }
      } else {
        const x = it.x + dx, y = it.y + dy;
        if (x >= 0 && y >= 0 && x + it.w <= st.cols && y + it.h <= st.rows && !hits(it, x, y, it.w, it.h)) { it.x = x; it.y = y; }
      }
      push(before); draw(); focusSel();
    }
    function dup(it) {
      let w = it.w, h = it.h, pos = freeSlot(w, h);
      if (!pos) { w = h = 1; pos = freeSlot(1, 1); }
      if (!pos) { say('The ' + (opts.noun || 'page') + ' is full.'); return; }
      const before = snap(); const c = copy(it); c.id = newId(); c.x = pos.x; c.y = pos.y; c.w = w; c.h = h;
      st.items.push(c); st.sel = c.id; push(before); draw(); focusSel();
    }
    function del(it) {
      const before = snap(); st.items = st.items.filter(function (i) { return i !== it; }); st.sel = null; push(before); draw();
    }
    function settings(it) {
      const before = snap();
      dialog(it, opts.fields ? opts.fields(it) : [], function () { draw(); }, function (ok) {
        if (!ok) load(before); else push(before);
        draw(); focusSel();
      });
    }

    drawTools();
    draw();
    return {
      get: function () { return { cols: st.cols, rows: st.rows, items: copy(st.items) }; },
      set: function (s) { st.cols = s.cols; st.rows = s.rows; st.items = copy(s.items || []); st.sel = null; undo = []; redo = []; saved = snap(); draw(); },
      enter: enter,
      editing: function () { return editing; },
      redraw: draw,
      dirty: function () { return saved !== null && saved !== snap(); }
    };
  }

  /* ------------------------------------------------------------- the settings dialog: Widget + Look */
  let dlg = null;
  function dialog(it, fields, live, done) {
    if (!dlg) { dlg = el('dialog', 'rl-dlg'); document.body.appendChild(dlg); }
    dlg.textContent = '';
    const before = JSON.stringify(it);
    const head = el('div', 'rl-dh'); head.appendChild(el('b', '', 'Settings'));
    head.appendChild(el('span', 'rl-badge', it.title || '')); dlg.appendChild(head);
    const tabs = el('div', 'rl-dtabs'); dlg.appendChild(tabs);
    const body = el('div', 'rl-db'); const left = el('div', 'rl-fields'); const prev = el('div', 'rl-prev');
    body.appendChild(left); body.appendChild(prev); dlg.appendChild(body);
    let tab = fields.length ? 'widget' : 'look';
    it.style = it.style || {}; it.props = it.props || {};
    function fld(label, input, id) {
      const f = el('div', 'rl-fld'); const l = el('label', '', label); input.id = id; l.htmlFor = id;
      f.appendChild(l); f.appendChild(input); return f;
    }
    function changed() { drawPrev(); live(); }
    function drawPrev() {
      prev.textContent = ''; prev.appendChild(el('div', 'rl-note', 'Preview'));
      const pv = el('div', 'rl-pv'); const cell = el('div', 'rl-cell'); cell.appendChild(R.item(it)); pv.appendChild(cell);
      prev.appendChild(pv); prev.appendChild(el('div', 'rl-note', it.w + ' × ' + it.h + ' cells'));
    }
    function drawTabs() {
      tabs.textContent = '';
      (fields.length ? [['widget', 'Settings'], ['look', 'Look']] : [['look', 'Look']]).forEach(function (t) {
        const b = btn(t[1], '', function () { tab = t[0]; drawTabs(); drawFields(); });
        b.setAttribute('aria-selected', String(tab === t[0])); tabs.appendChild(b);
      });
    }
    function drawFields() {
      left.textContent = '';
      if (tab === 'look') {
        const s = it.style; const L = R.look(it);
        const lab = el('input'); lab.maxLength = 60; lab.value = L.label; lab.placeholder = it.title || '';
        lab.oninput = function () { s.label = lab.value; changed(); };
        left.appendChild(fld('Label text', lab, 'rlLabel'));
        const seg = el('div', 'rl-seg'); seg.setAttribute('role', 'group'); seg.setAttribute('aria-label', 'Shape');
        [['rect', 'Square'], ['rounded', 'Rounded'], ['pill', 'Pill'], ['circle', 'Circle']].forEach(function (o) {
          const b = btn(o[1], '', function () { s.shape = o[0]; drawFields(); changed(); });
          b.setAttribute('aria-pressed', String(L.shape === o[0])); seg.appendChild(b);
        });
        const sf = el('div', 'rl-fld'); sf.appendChild(el('label', '', 'Shape')); sf.appendChild(seg); left.appendChild(sf);
        const cols = el('div', 'rl-colors');
        [['bg', 'Background'], ['fg', 'Text']].concat(it.kind === 'button' ? [['pressed', 'Pressed']] : []).forEach(function (k) {
          const i = el('input'); i.type = 'color'; i.value = L[k[0]]; i.oninput = function () { s[k[0]] = i.value; changed(); };
          cols.appendChild(fld(k[1], i, 'rlC' + k[0]));
        });
        left.appendChild(cols);
        const ic = el('select');
        Object.keys(R.ICON_NAMES).forEach(function (k) { const o = el('option', '', R.ICON_NAMES[k]); o.value = k; if (L.icon === k) o.selected = true; ic.appendChild(o); });
        ic.onchange = function () { s.icon = ic.value; changed(); };
        left.appendChild(fld('Icon', ic, 'rlIcon'));
      } else {
        fields.forEach(function (f, n) {
          const id = 'rlF' + n; let input;
          if (f.type === 'note') { left.appendChild(el('div', 'rl-note', f.label)); return; }
          if (f.type === 'select') {
            input = el('select');
            (f.options || []).forEach(function (o) { const opt = el('option', '', o[1]); opt.value = o[0]; if (String(it.props[f.key]) === String(o[0])) opt.selected = true; input.appendChild(opt); });
            input.onchange = function () { it.props[f.key] = input.value; changed(); };
          } else if (f.type === 'check') {
            input = el('input'); input.type = 'checkbox'; input.checked = !!it.props[f.key];
            input.onchange = function () { it.props[f.key] = input.checked; changed(); };
          } else if (f.type === 'area') {
            input = el('textarea'); input.maxLength = f.max || 200; input.value = it.props[f.key] || '';
            input.oninput = function () { it.props[f.key] = input.value; changed(); };
          } else {
            input = el('input'); input.type = f.type === 'time' ? 'time' : (f.type === 'number' ? 'number' : 'text');
            if (f.max) input.maxLength = f.max;
            input.value = it.props[f.key] === undefined ? '' : it.props[f.key]; input.readOnly = f.type === 'ro';
            input.oninput = function () { it.props[f.key] = f.type === 'number' ? +input.value : input.value; changed(); };
          }
          left.appendChild(fld(f.label, input, id));
        });
      }
    }
    const foot = el('div', 'rl-df');
    let ok = false;
    foot.appendChild(btn('Cancel', '', function () { dlg.close(); }));
    foot.appendChild(btn('Done', 'primary', function () { ok = true; dlg.close(); }));
    dlg.appendChild(foot);
    dlg.onclose = function () {
      dlg.onclose = null;
      if (!ok) { const o = JSON.parse(before); Object.keys(it).forEach(function (k) { delete it[k]; }); Object.assign(it, o); }
      done(ok);
    };
    drawTabs(); drawFields(); drawPrev(); dlg.showModal();
  }

  window.RelayLayout.edit = edit;
})();
