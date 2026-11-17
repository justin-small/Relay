/* The shared layout renderer (#421). One drawing for the editor's canvas, boards (/board/<slug>, #156) and the
   lighting module's cue pages on /screen, so what the operator lays out is what the screen shows.

   window.RelayLayout.grid(root, {cols, rows, gap})      root becomes the grid
   window.RelayLayout.item(it)                          -> element for one item (kind, style, props, title)
   window.RelayLayout.draw(root, page, onPress)         -> {id: element}; page = {cols, rows, items}
   window.RelayLayout.update(element, data)             feed live data to an item (captions, lit, status...)

   An item's look (app/layout.py) is a shape, three #rrggbb colours, a label and an icon from ICONS; anything else
   falls back. Everything is set as text or a style property, never as markup. */
(function () {
  'use strict';
  const ICONS = {
    light: 'M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5c.7.7 1 1.5 1 2.5h6c0-1 .3-1.8 1-2.5A6 6 0 0 0 12 3z',
    mic: 'M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3',
    cam: 'M3 7h12v10H3zM15 10l6-3v10l-6-3',
    star: 'M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z',
    music: 'M9 18V5l11-2v13M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0zM20 16a3 3 0 1 1-6 0 3 3 0 0 1 6 0z',
    cc: 'M3 5h18v14H3zM10 10a2 2 0 1 0 0 4M17 10a2 2 0 1 0 0 4',
    clock: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v5l3 2',
    arrow: 'M5 12h14M13 6l6 6-6 6',
    bell: 'M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15zM10 21h4',
    play: 'M7 4l13 8-13 8z',
    stop: 'M6 6h12v12H6z'
  };
  const ICON_NAMES = { '': 'None', light: 'Light', mic: 'Mic', cam: 'Camera', star: 'Star', music: 'Music', cc: 'Captions',
    clock: 'Clock', arrow: 'Arrow', bell: 'Bell', play: 'Play', stop: 'Stop' };
  const SHAPES = ['rounded', 'rect', 'pill', 'circle'];
  const HEX = /^#[0-9a-fA-F]{6}$/;

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function icon(name) {
    if (!Object.prototype.hasOwnProperty.call(ICONS, name)) return null;
    const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('class', 'rl-ico'); s.setAttribute('aria-hidden', 'true');
    const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    p.setAttribute('d', ICONS[name]); s.appendChild(p);
    return s;
  }
  function look(it) {
    const s = it.style || {};
    return { label: typeof s.label === 'string' ? s.label : '', shape: SHAPES.indexOf(s.shape) >= 0 ? s.shape : 'rounded',
      bg: HEX.test(s.bg) ? s.bg : '#18181b', fg: HEX.test(s.fg) ? s.fg : '#fafafa',
      pressed: HEX.test(s.pressed) ? s.pressed : '#3f3f46', icon: ICONS[s.icon] ? s.icon : '' };
  }
  function head(s, fallback) {
    const h = el('div', 'rl-hd');
    const i = icon(s.icon); if (i) h.appendChild(i);
    h.appendChild(el('span', '', s.label || fallback || ''));
    return h;
  }
  function two(n) { return (n < 10 ? '0' : '') + n; }
  function clockText(h24) { return new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', hour12: !h24 }); }
  function untilText(at) {
    const m = /^(\d{1,2}):(\d{2})$/.exec(at || '');
    if (!m) return '--:--';
    const now = new Date(); const t = new Date(now); t.setHours(+m[1], +m[2], 0, 0);
    let s = Math.round((t - now) / 1000);
    if (s < 0) s = 0;
    const h = Math.floor(s / 3600);
    return (h ? h + ':' + two(Math.floor(s / 60) % 60) : Math.floor(s / 60)) + ':' + two(s % 60);
  }
  function since(t) {
    if (!t) return '';
    let s = Math.max(0, Math.round(Date.now() / 1000 - t));
    return Math.floor(s / 3600) + ':' + two(Math.floor(s / 60) % 60) + ':' + two(s % 60);
  }

  // Each kind draws its body once; update() changes only the parts that move.
  const KINDS = {
    button: function (w, it, s) {
      w.classList.add('rl-press'); w.setAttribute('role', 'button'); w.tabIndex = 0;
      const b = el('div', 'rl-big'); const i = icon(s.icon);
      if (i) b.appendChild(i);
      b.appendChild(el('span', '', s.label || it.title || ''));
      w.appendChild(b);
    },
    captions: function (w, it, s) {
      w.appendChild(head(s, it.title));
      const c = el('div', 'rl-cap'); c.dataset.n = String(Math.max(1, Math.min(6, +(it.props || {}).lines || 3)));
      w.appendChild(c); w._rl.cap = c;
    },
    clock: function (w, it, s) {
      w.appendChild(head(s, it.title));
      const b = el('div', 'rl-bigx'); b.dataset.rlClock = (it.props || {}).h24 ? '24' : '12'; b.textContent = clockText((it.props || {}).h24);
      w.appendChild(b);
    },
    countdown: function (w, it, s) {
      w.appendChild(head(s, it.title));
      const b = el('div', 'rl-bigx'); b.dataset.rlUntil = (it.props || {}).at || ''; b.textContent = untilText(b.dataset.rlUntil);
      w.appendChild(b);
    },
    text: function (w, it) {
      w.classList.add('rl-textonly'); w.appendChild(el('div', 'rl-text', (it.props || {}).text || ''));
    },
    status: function (w, it, s) {
      w.appendChild(head(s, it.title));
      const a = el('div', 'rl-air', 'Off air'); const v = el('div', 'rl-ln');
      w.appendChild(a); w.appendChild(v); w._rl.air = a; w._rl.airText = v;
    },
    lines: function (w, it, s) {
      w.appendChild(head(s, it.title));
      const c = el('div', 'rl-cap rl-center'); w.appendChild(c); w._rl.lines = c;
    },
    list: function (w, it, s) {
      w.appendChild(head(s, it.title));
      const c = el('div', 'rl-list'); w.appendChild(c); w._rl.list = c;
    }
  };

  function item(it) {
    const s = look(it);
    const w = el('div', 'rl-w');
    w._rl = {};
    w.dataset.shape = s.shape; w.dataset.kind = it.kind || 'text';
    w.style.background = s.bg; w.style.color = s.fg; w.style.setProperty('--rl-pressed', s.pressed);
    (KINDS[it.kind] || KINDS.text)(w, it, s);
    if (it.off) { w.classList.add('rl-off'); w.appendChild(el('div', 'rl-offnote', it.off)); }
    return w;
  }

  function update(w, d) {
    if (!w || !w._rl || !d) return;
    if (d.lit !== undefined) w.classList.toggle('rl-lit', !!d.lit);
    if (w._rl.cap && Array.isArray(d.lines)) {
      const n = +w._rl.cap.dataset.n || 3; w._rl.cap.textContent = '';
      d.lines.slice(-n).forEach(function (t) { w._rl.cap.appendChild(el('div', '', String(t))); });
    }
    if (w._rl.lines && Array.isArray(d.lines)) {
      w._rl.lines.textContent = ''; d.lines.slice(0, 6).forEach(function (t) { w._rl.lines.appendChild(el('div', '', String(t))); });
    }
    if (w._rl.list && Array.isArray(d.rows)) {
      w._rl.list.textContent = '';
      d.rows.slice(0, 12).forEach(function (r) { w._rl.list.appendChild(el('div', r && r.current ? 'rl-cur' : '', String(r && r.text || ''))); });
    }
    if (w._rl.air && d.on_air !== undefined) {
      w._rl.air.textContent = d.on_air ? 'On air' : (d.on_air === false ? 'Off air' : 'No stream set up');
      w._rl.air.classList.toggle('rl-on', !!d.on_air);
      w._rl.airSince = d.on_air ? d.live_since : 0;
      w._rl.airViewers = d.viewers;
      w._rl.airText.textContent = airLine(w);
    }
  }
  function airLine(w) {
    const parts = [];
    if (w._rl.airSince) parts.push(since(w._rl.airSince));
    if (typeof w._rl.airViewers === 'number') parts.push(w._rl.airViewers + ' watching');
    return parts.join(' · ');
  }

  function grid(root, g) {
    root.classList.add('rl-grid');
    root.style.gridTemplateColumns = 'repeat(' + g.cols + ',minmax(0,1fr))';
    root.style.gridTemplateRows = 'repeat(' + g.rows + ',minmax(0,1fr))';
    if (g.gap !== undefined) root.style.setProperty('--rl-gap', g.gap + 'px');
  }
  function area(node, it) { node.style.gridArea = (it.y + 1) + '/' + (it.x + 1) + '/span ' + it.h + '/span ' + it.w; }

  function draw(root, page, onPress) {
    root.textContent = '';
    grid(root, page);
    const out = {};
    (page.items || []).forEach(function (it) {
      const cell = el('div', 'rl-cell'); area(cell, it);
      const w = item(it); cell.appendChild(w); root.appendChild(cell);
      if (onPress && it.kind === 'button' && !it.off) {
        const fire = function () {
          w.classList.add('rl-down'); setTimeout(function () { w.classList.remove('rl-down'); }, 220);
          onPress(it, w);
        };
        w.addEventListener('click', fire);
        w.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fire(); } });
      }
      out[it.id] = w;
    });
    return out;
  }

  // Clocks, countdowns and on-air timers move by themselves, on every page that draws items.
  setInterval(function () {
    document.querySelectorAll('[data-rl-clock]').forEach(function (n) { n.textContent = clockText(n.dataset.rlClock === '24'); });
    document.querySelectorAll('[data-rl-until]').forEach(function (n) { n.textContent = untilText(n.dataset.rlUntil); });
    document.querySelectorAll('.rl-w[data-kind=status]').forEach(function (w) { if (w._rl && w._rl.airText && w._rl.airSince) w._rl.airText.textContent = airLine(w); });
  }, 1000);

  window.RelayLayout = Object.assign(window.RelayLayout || {}, {
    ICON_NAMES: ICON_NAMES, SHAPES: SHAPES, icon: icon, look: look, item: item, update: update, grid: grid, area: area, draw: draw
  });
})();
