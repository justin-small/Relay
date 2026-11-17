/* /board/<slug> (#156): draw the board with the shared renderer, keep its widgets live, and (for an admin) lay it
   out with the shared editor over the live board. Captions and the livestream come from the existing feeds (/stream);
   a module's widgets from /board/<slug>/_/data. Every value from the server is set as text. */
(function () {
  'use strict';
  const R = window.RelayLayout;
  const data = JSON.parse(document.getElementById('boardData').textContent);
  const board = data.board;
  const host = document.getElementById('board');
  const toast = document.getElementById('toast');
  const base = '/board/' + encodeURIComponent(board.id);
  const cat = {};
  (data.catalogue || []).forEach(function (d) { cat[d.type] = d; });
  const press = {};
  (data.press || []).forEach(function (t) { press[t] = true; });
  const live = {};          // item id -> the last data for it, re-applied whenever the board is drawn again
  let nodes = {};
  let items = board.items;
  let toastTimer = 0;

  function say(text) {
    toast.textContent = text; toast.hidden = !text;
    clearTimeout(toastTimer);
    if (text) toastTimer = setTimeout(function () { toast.hidden = true; }, 4000);
  }
  function apply(id, d) {
    live[id] = Object.assign(live[id] || {}, d);
    if (nodes[id]) R.update(nodes[id], live[id]);
  }
  function wire(ns) {
    nodes = ns;
    Object.keys(ns).forEach(function (id) { if (live[id]) R.update(ns[id], live[id]); });
  }

  async function onPress(it) {
    if (it.type === 'link') {
      const to = (it.props || {}).board;
      if (to) location.href = '/board/' + encodeURIComponent(to);
      return;
    }
    if (!press[it.type]) { say('Sign in with an account that may press this.'); return; }
    try {
      const res = await fetch(base + '/_/press/' + encodeURIComponent(it.id), { method: 'POST', credentials: 'same-origin' });
      if (!res.ok) { const out = await res.json().catch(function () { return {}; }); say(out.error || 'That did not work.'); }
    } catch (e) { say('Relay did not answer.'); }
  }

  /* ---------------------------------------------------------------- feeds */
  const capFeeds = {};      // language -> {es, lines, open}
  let statusEs = null;
  function capLines(f) { return f.lines.concat(f.open ? [f.open] : []); }
  function pushCaptions(lang) {
    const f = capFeeds[lang];
    items.forEach(function (it) { if (it.type === 'captions' && (it.props || {}).lang === lang) apply(it.id, { lines: capLines(f) }); });
  }
  function openCaptions(lang) {
    const f = capFeeds[lang] = capFeeds[lang] || { lines: [], open: '', retry: 1000, es: null };
    const qs = lang === 'SOURCE' ? 'stream=transcription' : 'stream=translation&lang=' + encodeURIComponent(lang);
    const es = new EventSource('/stream?' + qs);
    f.es = es;
    es.onopen = function () { f.retry = 1000; };
    es.onmessage = function (ev) {
      let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
      if (m.type === 'targets') return;
      if (m.type === 'snapshot') {
        f.lines = (m.lines || []).map(function (l) { return l.text; }).slice(-8); f.open = m.open || '';
      } else if (m.final) {
        f.lines = f.lines.concat([m.text]).slice(-8); f.open = '';
      } else {
        f.open = m.text !== undefined ? m.text : f.open + (m.delta || '');
      }
      pushCaptions(lang);
    };
    es.onerror = function () {
      es.close();
      if (f.es !== es) return;
      f.es = null;
      setTimeout(function () { if (!f.es && wanted().langs[lang]) openCaptions(lang); }, f.retry);
      f.retry = Math.min(10000, f.retry * 2);
    };
  }
  function openStatus() {
    statusEs = new EventSource('/stream?stream=status&sound=1');
    statusEs.onmessage = function (ev) {
      let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
      const on = (m.streams || []).filter(function (s) { return s.on_air; });
      const since = on.map(function (s) { return s.live_since; }).filter(Boolean);
      const viewers = on.map(function (s) { return s.viewers; }).filter(function (v) { return typeof v === 'number'; });
      const d = { on_air: m.on_air, live_since: since.length ? Math.min.apply(null, since) : 0,
        viewers: viewers.length ? viewers.reduce(function (a, b) { return a + b; }, 0) : null };
      items.forEach(function (it) {
        if (it.type === 'stream') apply(it.id, d);
        if (it.type === 'spl' || it.type === 'rta') apply(it.id, { sound: m.sound || null });
      });
    };
  }
  function wanted() {
    const langs = {}; let status = false, mod = false;
    items.forEach(function (it) {
      if (it.type === 'captions') langs[(it.props || {}).lang || 'SOURCE'] = true;
      if (it.type === 'stream' || it.type === 'spl' || it.type === 'rta') status = true;
      if (it.type.indexOf('.') > 0) mod = true;
    });
    return { langs: langs, status: status, mod: mod };
  }
  function feeds() {
    const w = wanted();
    Object.keys(capFeeds).forEach(function (l) {
      if (!w.langs[l] && capFeeds[l].es) { capFeeds[l].es.close(); capFeeds[l].es = null; }
    });
    Object.keys(w.langs).forEach(function (l) { if (!capFeeds[l] || !capFeeds[l].es) openCaptions(l); else pushCaptions(l); });
    if (w.status && !statusEs) openStatus();
    if (!w.status && statusEs) { statusEs.close(); statusEs = null; }
  }
  async function poll() {
    if (wanted().mod && !document.hidden) {
      try {
        const res = await fetch(base + '/_/data', { cache: 'no-store', credentials: 'same-origin' });
        if (res.ok) {
          const got = (await res.json()).items || {};
          Object.keys(got).forEach(function (id) { apply(id, got[id]); });
        }
      } catch (e) { /* the next tick tries again */ }
    }
    setTimeout(poll, 3000);
  }

  /* ---------------------------------------------------------------- drawing */
  function ratio() { return board.ratio.replace(':', '/'); }
  // The board keeps its shape (16:9, 4:3, 9:16) whatever the window: the largest box of that shape that fits.
  function fit() {
    const g = host.querySelector('.rl-canvas, #boardView > .rl-grid');
    if (!g || !g.parentElement) return;
    const r = board.ratio.split(':').map(Number), box = g.parentElement, cs = getComputedStyle(box);
    const w = box.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    const h = box.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
    const s = Math.max(0, Math.min(w / r[0], h / r[1]));
    g.style.width = Math.floor(s * r[0]) + 'px'; g.style.height = Math.floor(s * r[1]) + 'px';
  }
  window.addEventListener('resize', fit);
  function view() {
    host.textContent = '';
    const v = document.createElement('div'); v.id = 'boardView'; host.appendChild(v);
    const g = document.createElement('div'); g.style.aspectRatio = ratio(); v.appendChild(g);
    if (!items.length) { const e = document.createElement('div'); e.className = 'empty'; e.textContent = 'This board has nothing on it yet.'; v.textContent = ''; v.appendChild(e); return; }
    wire(R.draw(g, { cols: board.cols, rows: board.rows, items: items }, onPress));
    fit();
  }

  if (!data.can_edit || !R.edit) { view(); feeds(); poll(); return; }

  // ------------------------------------------------------------------ the editor (admins, operator port)
  const SIZES = { '16:9': [[12, 8], [16, 9], [8, 6], [24, 16]], '4:3': [[8, 6], [12, 9], [4, 3], [16, 12]],
    '9:16': [[6, 10], [9, 16], [4, 7], [12, 20]] };
  function defaults(d) {
    const p = {};
    (d.fields || []).forEach(function (f) { if (f.type !== 'note' && f.default !== undefined && f.default !== null) p[f.key] = f.default; });
    return p;
  }
  const palette = [];
  (data.catalogue || []).forEach(function (d) {
    const badge = d.module ? 'module' : '';
    if (d.presets && d.presets.length) {
      d.presets.forEach(function (p) {
        palette.push({ group: d.group, badge: badge, name: p.name, title: p.name, kind: d.kind, type: d.type, w: d.w, h: d.h,
          props: Object.assign(defaults(d), p.props), style: Object.assign({}, d.style, p.style) });
      });
    } else {
      palette.push({ group: d.group, badge: badge, name: d.name, title: d.name, kind: d.kind, type: d.type, w: d.w, h: d.h,
        props: defaults(d), style: Object.assign({}, d.style) });
    }
  });
  palette.sort(function (a, b) { return (a.badge ? 1 : 0) - (b.badge ? 1 : 0); });
  function fields(it) {
    const d = cat[it.type];
    if (!d) return [{ type: 'note', label: 'This widget\'s module is off. It comes back when the module is switched on.' }];
    return d.fields.map(function (f) { return { key: f.key, label: f.label, type: f.type, options: f.options, max: f.max }; });
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
  function forPage(list) {
    return list.map(function (it) {
      const d = cat[it.type];
      return Object.assign({}, it, d ? { kind: d.kind, title: titleOf(d, it.props) } : { kind: 'text', title: '', off: 'This widget\'s module is off.' });
    });
  }
  const btn = document.createElement('button');
  btn.id = 'editBtn'; btn.type = 'button'; btn.textContent = 'Edit board';
  let ed = null;
  ed = R.edit(host, {
    title: board.name, noun: 'board', paletteName: 'Widgets', cols: board.cols, rows: board.rows, ratio: ratio(),
    sizes: SIZES[board.ratio] || SIZES['16:9'], items: items, palette: palette, fields: fields,
    onSave: async function (st) {
      const res = await fetch('/api/admin/boards/' + encodeURIComponent(board.id), {
        method: 'PUT', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cols: st.cols, rows: st.rows, items: st.items.map(function (it) {
          return { id: it.id, type: it.type, x: it.x, y: it.y, w: it.w, h: it.h, style: it.style, props: it.props };
        }) }) });
      const out = await res.json().catch(function () { return {}; });
      if (!res.ok) throw new Error(out.error || out.detail || 'That did not save.');
      board.cols = out.board.cols; board.rows = out.board.rows;
      items = forPage(out.board.items);
      ed.set({ cols: board.cols, rows: board.rows, items: items });
      if (history.replaceState) history.replaceState(null, '', base);
    },
    onCancel: function () { if (history.replaceState) history.replaceState(null, '', base); },
    onDraw: function (ns, editing) {
      btn.hidden = editing;
      // While editing, the items are the editor's copies: keep feeding them so the board stays live underneath.
      if (ed) items = ed.get().items;
      wire(ns);
      if (!editing) {
        Object.keys(ns).forEach(function (id) {
          const it = items.filter(function (i) { return i.id === id; })[0];
          const w = ns[id];
          if (!it || it.kind !== 'button' || it.off) return;
          w.addEventListener('click', function () { w.classList.add('rl-down'); setTimeout(function () { w.classList.remove('rl-down'); }, 220); onPress(it); });
          w.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onPress(it); } });
        });
      }
      feeds();
      requestAnimationFrame(fit);
    }
  });
  btn.onclick = function () { ed.enter(); if (history.replaceState) history.replaceState(null, '', base + '?edit=1'); };
  document.body.appendChild(btn);
  window.addEventListener('beforeunload', function (e) { if (ed.editing() && ed.dirty()) { e.preventDefault(); e.returnValue = ''; } });
  if (data.edit) ed.enter();
  feeds(); poll();
})();
