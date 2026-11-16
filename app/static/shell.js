/* The operator panel's shell (#434): sidebar collapse and drawer, breadcrumb, the Preferences popover, menus,
   the rail tooltips and the command palette. Plain JavaScript, no build. admin.js still owns the pages and the
   stream; it calls RelayShell.page(name) when the page changes.

   The look is a set of attributes on <html> (data-theme, data-preset, data-sb, data-collapse, data-layout,
   data-navbar, class cl). The server writes them on first paint from the person's stored choices; this file
   changes them live and saves each change through /api/admin/me/prefs/core/<name>. A stored value is untrusted:
   every one goes through the allow-list below and is only ever set as an attribute or a CSS value. */
(function (root) {
  'use strict';

  /* ---- pure helpers (unit-tested in tests/js/shell.test.mjs) ---- */
  var CHOICES = {
    ui_preset: ['relay', 'softpop', 'tangerine', 'brutalist'],
    ui_mode: ['light', 'dark', 'system'],
    ui_sidebar: ['inset', 'sidebar', 'floating'],
    ui_collapse: ['icon', 'offcanvas'],
    ui_layout: ['centered', 'full'],
    ui_navbar: ['sticky', 'scroll'],
    ui_font: ['system', 'rounded', 'serif', 'mono']
  };
  var DEFAULTS = { ui_preset: 'relay', ui_mode: 'dark', ui_sidebar: 'inset', ui_collapse: 'icon', ui_layout: 'centered',
                   ui_navbar: 'sticky', ui_font: 'system', ui_collapsed: false };
  var FONTS = {
    system: 'ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif',
    rounded: 'ui-rounded,"SF Pro Rounded","Hiragino Maru Gothic ProN",system-ui,sans-serif',
    serif: 'ui-serif,Georgia,"Times New Roman",serif',
    mono: 'ui-monospace,SFMono-Regular,Menlo,monospace'
  };

  /* Anything not on the list becomes the default. */
  function cleanLook(raw) {
    var out = {}, k;
    raw = raw && typeof raw === 'object' ? raw : {};
    for (k in DEFAULTS) out[k] = DEFAULTS[k];
    for (k in CHOICES) if (typeof raw[k] === 'string' && CHOICES[k].indexOf(raw[k]) !== -1) out[k] = raw[k];
    if (raw.ui_collapsed === true) out.ui_collapsed = true;
    return out;
  }

  /* Where a popup goes: next to its anchor on a side, kept 8px inside the window. */
  function placePop(anchor, size, win, side, align) {
    var m = 8, x, y, origin;
    if (side === 'right') { x = anchor.right + 8; y = align === 'end' ? anchor.bottom - size.h : anchor.top; origin = 'left center'; }
    else if (side === 'top') { x = align === 'end' ? anchor.right - size.w : anchor.left; y = anchor.top - size.h - 4; origin = 'bottom ' + (align === 'end' ? 'right' : 'left'); }
    else { x = align === 'end' ? anchor.right - size.w : anchor.left; y = anchor.bottom + 4; origin = 'top ' + (align === 'end' ? 'right' : 'left'); }
    return { x: Math.max(m, Math.min(x, win.w - size.w - m)), y: Math.max(m, Math.min(y, win.h - size.h - m)), origin: origin };
  }

  /* The command palette's list: pages first (as the sidebar groups them), then actions; a query filters both. */
  function filterItems(items, query) {
    var q = String(query || '').trim().toLowerCase();
    return items.filter(function (i) { return !q || (i.t + ' ' + i.g).toLowerCase().indexOf(q) !== -1; });
  }

  var api = { CHOICES: CHOICES, DEFAULTS: DEFAULTS, FONTS: FONTS, cleanLook: cleanLook, placePop: placePop, filterItems: filterItems };
  if (typeof module !== 'undefined' && module.exports) { module.exports = api; return; }
  if (!root.document) return;

  /* ---- the shell in a browser ---- */
  var doc = root.document, html = doc.documentElement;
  function $(id) { return doc.getElementById(id); }
  var mq = root.matchMedia('(max-width:900px)'), sysDark = root.matchMedia('(prefers-color-scheme: dark)');
  function isMobile() { return mq.matches; }

  var ICONS = {
    chev: '<path d="M6 3l5 5-5 5"/>', down: '<path d="M4 6l4 4 4-4"/>', check: '<path d="M3 8.5l3.2 3L13 4.5"/>',
    sun: '<circle cx="8" cy="8" r="3"/><path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4"/>',
    moon: '<path d="M13 9.5A5.5 5.5 0 0 1 6.5 3a5.5 5.5 0 1 0 6.5 6.5z"/>',
    monitor: '<rect x="1.5" y="2.5" width="13" height="9" rx="1.5"/><path d="M5.5 14h5M8 11.5V14"/>',
    user: '<circle cx="8" cy="5.5" r="2.8"/><path d="M2.5 14c.5-3 2.7-4.5 5.5-4.5s5 1.5 5.5 4.5"/>',
    out: '<path d="M6 2.5H3.5a1 1 0 0 0-1 1v9a1 1 0 0 0 1 1H6M10 5l3 3-3 3M13 8H6"/>',
    play: '<path d="M4.5 3l8 5-8 5z"/>', stop: '<rect x="3.5" y="3.5" width="9" height="9" rx="1.5"/>',
    tune: '<circle cx="8" cy="8" r="2.2"/><path d="M8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M3.4 12.6l1.1-1.1M11.5 4.5l1.1-1.1"/>',
    panel: '<rect x="2" y="3" width="12" height="10" rx="2"/><path d="M6 3v10"/>', page: '<path d="M4 1.5h6l3 3v10H4z"/>'
  };
  function icon(n) {
    var s = doc.createElementNS('http://www.w3.org/2000/svg', 'svg');
    s.setAttribute('class', 'i'); s.setAttribute('viewBox', '0 0 16 16'); s.setAttribute('aria-hidden', 'true');
    s.innerHTML = ICONS[n] || '';   // a fixed table above, never page or stored data
    return s;
  }
  function el(tag, cls, text) {
    var e = doc.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  /* ---- the look ---- */
  var S = cleanLook(null);
  (function readStart() {
    var d = html.dataset;
    S = cleanLook({ ui_preset: d.preset, ui_mode: d.mode, ui_sidebar: d.sb, ui_collapse: d.collapse, ui_layout: d.layout,
                    ui_navbar: d.navbar, ui_font: d.font, ui_collapsed: html.classList.contains('cl') });
  })();
  function resolved() { return S.ui_mode === 'system' ? (sysDark.matches ? 'dark' : 'light') : S.ui_mode; }
  function apply() {
    var d = html.dataset;
    d.theme = resolved(); d.mode = S.ui_mode; d.preset = S.ui_preset; d.layout = S.ui_layout; d.navbar = S.ui_navbar;
    d.sb = S.ui_sidebar; d.collapse = S.ui_collapse; d.font = S.ui_font;
    html.classList.toggle('cl', !!S.ui_collapsed && !isMobile());
    html.style.setProperty('--font', FONTS[S.ui_font] || FONTS.system);
    var t = $('themeBtn');
    if (t) {
      t.textContent = '';
      t.appendChild(icon(S.ui_mode === 'system' ? 'monitor' : resolved() === 'dark' ? 'sun' : 'moon'));
      t.setAttribute('aria-label', 'Theme: ' + S.ui_mode + '. Click to change');
    }
  }
  /* Saved in the background; a failure leaves the choice applied for this page. */
  function save(name, value) {
    var p = DEFAULTS[name] === value ? root.fetch('/api/admin/me/prefs/core/' + name, { method: 'DELETE' })
      : root.fetch('/api/admin/me/prefs/core/' + name, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value: value }) });
    p.catch(function () {});
  }
  function set(name, value) { S[name] = value; apply(); save(name, value); }
  function cycleTheme() { var c = ['light', 'dark', 'system']; set('ui_mode', c[(c.indexOf(S.ui_mode) + 1) % 3]); }
  function toggleSidebar() {
    if (isMobile()) { doc.body.classList.toggle('open'); return; }
    set('ui_collapsed', !S.ui_collapsed);
  }
  sysDark.addEventListener('change', apply); mq.addEventListener('change', apply);

  /* ---- popups: one implementation for menus, the popover and select lists ---- */
  var pops = [];
  function closePop(p) {
    var i = pops.indexOf(p);
    if (i === -1) return;
    pops.splice(i, 1);
    p.el.classList.remove('in');
    if (p.anchor && p.anchor.setAttribute) p.anchor.setAttribute('aria-expanded', 'false');
    root.setTimeout(function () { p.el.remove(); }, 100);
  }
  function closeAll() { pops.slice().forEach(closePop); }
  function place(pop, anchor, side, align) {
    var r = anchor.getBoundingClientRect();
    var at = placePop({ left: r.left, right: r.right, top: r.top, bottom: r.bottom }, { w: pop.offsetWidth, h: pop.offsetHeight },
                      { w: root.innerWidth, h: root.innerHeight }, side, align);
    pop.style.left = at.x + 'px'; pop.style.top = at.y + 'px'; pop.style.transformOrigin = at.origin;
  }
  function menuItem(it, n) {
    if (it.sep) { var s = el('div', 'msep'); s.setAttribute('role', 'separator'); return s; }
    if (it.head) { var h = el('div', 'mhead'); h.appendChild(it.head); return h; }
    var b = el('button', 'mi' + (it.sel ? ' sel' : '') + (it.dis ? ' dis' : ''));
    b.type = 'button'; b.setAttribute('role', 'menuitem'); b.dataset.i = String(n);
    if (it.ic) b.appendChild(icon(it.ic));
    if (it.dot) { var d = el('span', 'sdot'); d.style.background = it.dot; b.appendChild(d); }
    b.appendChild(el('span', '', it.t));
    if (it.ck) { var c = icon('check'); c.setAttribute('class', 'i ck'); b.appendChild(c); }
    return b;
  }
  function popup(anchor, o) {
    var pop = el('div', 'pop ' + (o.cls || ''));
    pop.setAttribute('role', o.items ? 'menu' : 'dialog');
    if (o.w) pop.style.minWidth = o.w + 'px';
    var p = { el: pop, anchor: anchor };
    if (o.items) {
      o.items.forEach(function (it, n) { pop.appendChild(menuItem(it, n)); });
      pop.addEventListener('click', function (e) {
        var b = e.target.closest('[data-i]');
        if (!b) return;
        var it = o.items[+b.dataset.i];
        closePop(p);
        if (it.go) root.location.hash = it.go;
        if (it.fn) it.fn();
      });
    }
    doc.body.appendChild(pop);
    pops.push(p);
    if (anchor.setAttribute) anchor.setAttribute('aria-expanded', 'true');
    if (o.render) o.render(pop, p);
    place(pop, anchor, o.side || 'bottom', o.align || 'end');
    root.requestAnimationFrame(function () { pop.classList.add('in'); });
    var first = pop.querySelector('.mi,[data-k]');
    if (first && o.items) first.focus();
    return p;
  }
  function toggle(anchor, o) {
    for (var i = 0; i < pops.length; i++) if (pops[i].anchor === anchor) { closePop(pops[i]); return; }
    popup(anchor, o);
  }
  root.addEventListener('pointerdown', function (e) {
    if (!pops.length) return;
    for (var i = 0; i < pops.length; i++) if (pops[i].el.contains(e.target) || (pops[i].anchor.contains && pops[i].anchor.contains(e.target))) return;
    closeAll();
  });
  var lastW = root.innerWidth;   // a phone's address bar changes only the height: that must not close a menu
  root.addEventListener('resize', function () { if (root.innerWidth !== lastW) { lastW = root.innerWidth; closeAll(); } });

  /* ---- toasts ---- */
  var toastTimer = 0;
  function toast(m) {
    var t = $('toast');
    if (!t) return;
    t.textContent = m; t.classList.add('on');
    root.clearTimeout(toastTimer); toastTimer = root.setTimeout(function () { t.classList.remove('on'); }, 1800);
  }

  /* ---- rail tooltips: only while the sidebar is the icon rail ---- */
  var tip = el('div', 'tip'); tip.setAttribute('role', 'tooltip'); doc.body.appendChild(tip);
  doc.addEventListener('mouseover', function (e) {
    var t = e.target.closest && e.target.closest('[data-tip]');
    if (!t || !html.classList.contains('cl') || html.dataset.collapse !== 'icon') { tip.classList.remove('in'); return; }
    tip.textContent = t.dataset.tip;
    var r = t.getBoundingClientRect();
    tip.style.left = (r.right + 8) + 'px'; tip.style.top = (r.top + (r.height - tip.offsetHeight) / 2) + 'px';
    tip.classList.add('in');
  });
  doc.addEventListener('mouseout', function (e) { if (e.target.closest && e.target.closest('[data-tip]')) tip.classList.remove('in'); });

  /* ---- the Preferences popover ---- */
  var PRESETS = [{ k: 'relay', l: 'Relay', d: '#4ade80', li: '#15803d' },
                 { k: 'softpop', l: 'Soft Pop', d: 'oklch(.6801 .1583 276.93)', li: 'oklch(.5106 .2301 276.97)' },
                 { k: 'tangerine', l: 'Tangerine', d: 'oklch(.64 .17 36.44)', li: 'oklch(.64 .17 36.44)' },
                 { k: 'brutalist', l: 'Brutalist', d: 'oklch(.7044 .1872 23.19)', li: 'oklch(.6489 .237 26.97)' }];
  var FONT_NAMES = { system: 'System UI', rounded: 'Rounded', serif: 'Serif', mono: 'Monospace' };
  function toggleGroup(label, key, opts) {
    var f = el('div', 'fld'), g = el('div', 'tg');
    f.appendChild(el('label', '', label)); g.setAttribute('role', 'group'); g.setAttribute('aria-label', label);
    opts.forEach(function (o) {
      var b = el('button', '', o[1]); b.type = 'button'; b.dataset.k = key; b.dataset.v = o[0];
      b.setAttribute('aria-pressed', String(S[key] === o[0])); g.appendChild(b);
    });
    f.appendChild(g);
    return f;
  }
  function selectButton(label, id, text, dot) {
    var f = el('div', 'fld'), b = el('button', 'sel'), d;
    f.appendChild(el('label', '', label));
    b.type = 'button'; b.id = id; b.setAttribute('aria-haspopup', 'menu'); b.setAttribute('aria-expanded', 'false');
    if (dot) { d = el('span', 'sdot'); d.style.background = dot; b.appendChild(d); }
    b.appendChild(el('span', 'nl', text)); b.appendChild(icon('down'));
    f.appendChild(b);
    return f;
  }
  function renderLook(box) {
    var pr = PRESETS.filter(function (p) { return p.k === S.ui_preset; })[0] || PRESETS[0];
    box.textContent = '';
    box.appendChild(el('h4', '', 'Preferences'));
    box.appendChild(el('p', 'dd', 'Customize how the panel looks. Saved for you on every device.'));
    box.appendChild(selectButton('Theme Preset', 'selP', pr.l, resolved() === 'dark' ? pr.d : pr.li));
    box.appendChild(selectButton('Fonts', 'selF', FONT_NAMES[S.ui_font]));
    box.appendChild(toggleGroup('Theme Mode', 'ui_mode', [['light', 'Light'], ['dark', 'Dark'], ['system', 'System']]));
    box.appendChild(toggleGroup('Page Layout', 'ui_layout', [['centered', 'Centered'], ['full', 'Full Width']]));
    box.appendChild(toggleGroup('Navbar Behavior', 'ui_navbar', [['sticky', 'Sticky'], ['scroll', 'Scroll']]));
    box.appendChild(toggleGroup('Sidebar Style', 'ui_sidebar', [['inset', 'Inset'], ['sidebar', 'Sidebar'], ['floating', 'Floating']]));
    box.appendChild(toggleGroup('Sidebar Collapse Mode', 'ui_collapse', [['icon', 'Icon'], ['offcanvas', 'OffCanvas']]));
    var r = el('button', 'rst', 'Restore Defaults'); r.type = 'button'; r.id = 'rst'; box.appendChild(r);
    box.onclick = function (e) {
      var b = e.target.closest('[data-k]');
      if (b) { set(b.dataset.k, b.dataset.v); renderLook(box); return; }
      if (e.target.closest('#rst')) {
        Object.keys(CHOICES).forEach(function (k) { S[k] = DEFAULTS[k]; save(k, DEFAULTS[k]); });
        S.ui_collapsed = false; save('ui_collapsed', false); apply(); renderLook(box); return;
      }
      var sp = e.target.closest('#selP'), sf = e.target.closest('#selF');
      if (sp) toggle(sp, { side: 'bottom', align: 'start', w: sp.offsetWidth, items: PRESETS.map(function (p) {
        return { t: p.l, dot: resolved() === 'dark' ? p.d : p.li, ck: 1, sel: p.k === S.ui_preset, fn: function () { set('ui_preset', p.k); renderLook(box); } }; }) });
      if (sf) toggle(sf, { side: 'bottom', align: 'start', w: sf.offsetWidth, items: Object.keys(FONT_NAMES).map(function (k) {
        return { t: FONT_NAMES[k], ck: 1, sel: k === S.ui_font, fn: function () { set('ui_font', k); renderLook(box); } }; }) });
    };
  }
  function openLook() {
    var b = $('lookBtn');
    if (b) toggle(b, { side: 'bottom', align: 'end', cls: 'pp', render: renderLook });
  }

  /* ---- account menu ---- */
  function accountItems() {
    var me = (root.RELAY_ME && root.RELAY_ME.user) || {};
    var head = el('span', 'uinfo'); head.appendChild(el('b', '', me.name || '')); head.appendChild(el('small', '', me.role || ''));
    var av = el('span', 'av', initials(me.name));
    var h = doc.createDocumentFragment(); h.appendChild(av); h.appendChild(head);
    return [{ head: h }, { sep: 1 }, { ic: 'user', t: 'Account', go: '#account' }, { sep: 1 },
            { ic: 'out', t: 'Sign out', fn: function () { var f = $('logoutForm'); if (f) f.submit(); } }];
  }
  function initials(name) {
    return String(name || '?').trim().split(/\s+/).slice(0, 2).map(function (w) { return w.charAt(0); }).join('') || '?';
  }

  /* ---- navigation: breadcrumb, active parent, drawer ---- */
  function navLinks() { return Array.prototype.slice.call(doc.querySelectorAll('#nav a[data-page]')); }
  function groupOf(a) {
    var n = a.closest('.has') || a;
    while (n && n.previousElementSibling) { n = n.previousElementSibling; if (n.classList.contains('grp')) return n.textContent; }
    return '';
  }
  function pageChanged(name) {
    var link = navLinks().filter(function (a) { return a.dataset.page === name; })[0];
    var crumb = $('crumb');
    if (crumb) {
      crumb.textContent = '';
      if (link) {
        var g = groupOf(link);
        if (g) { crumb.appendChild(doc.createTextNode(g + ' / ')); }
        crumb.appendChild(el('b', '', link.textContent.trim()));
      } else if (name === 'account') crumb.appendChild(el('b', '', 'My account'));
    }
    doc.querySelectorAll('#nav .has').forEach(function (p) {
      var on = !!p.querySelector('a[aria-current=page]');
      var nb = p.querySelector('.nb');
      if (nb) nb.classList.toggle('act', on);
      if (on) { p.classList.add('open'); if (nb) nb.setAttribute('aria-expanded', 'true'); }
    });
    doc.body.classList.remove('open');
    var inset = $('inset');
    if (inset) inset.scrollTop = 0;
    closeAll();
  }

  /* ---- command palette (Ctrl/Cmd K), built from the sidebar so module pages appear and hidden ones do not ---- */
  var palIdx = 0, palItems = [];
  function pageItems() {
    return navLinks().filter(function (a) { return !a.classList.contains('dis'); }).map(function (a) {
      var ic = a.querySelector('svg');
      return { g: groupOf(a) || 'Pages', t: a.textContent.trim(), svg: ic ? ic.innerHTML : ICONS.page, go: '#' + a.dataset.page };
    });
  }
  function actionItems() {
    var acts = [], go = $('goBtn');
    if (go && !go.disabled) acts.push({ g: 'Actions', t: go.dataset.tip || 'Start or stop the stream', svg: ICONS.play, fn: function () { go.click(); } });
    acts.push({ g: 'Actions', t: 'Cycle theme (light, dark, system)', svg: ICONS.sun, fn: cycleTheme });
    acts.push({ g: 'Actions', t: 'Open preferences', svg: ICONS.tune, fn: function () { root.setTimeout(openLook, 120); } });
    acts.push({ g: 'Actions', t: 'Collapse or expand the sidebar', svg: ICONS.panel, fn: toggleSidebar });
    return acts;
  }
  function palBuild() {
    var res = $('res'), last = '';
    palItems = filterItems(pageItems().concat(actionItems()), $('q').value);
    res.textContent = '';
    if (!palItems.length) { res.appendChild(el('div', 'none', 'No results found.')); return; }
    palItems.forEach(function (i, n) {
      if (i.g !== last) { res.appendChild(el('h5', '', i.g)); last = i.g; }
      var a = el('a', n === palIdx ? 'h' : ''), s = doc.createElementNS('http://www.w3.org/2000/svg', 'svg');
      a.dataset.n = String(n); a.setAttribute('role', 'option'); a.tabIndex = -1;
      s.setAttribute('class', 'i'); s.setAttribute('viewBox', '0 0 16 16'); s.setAttribute('aria-hidden', 'true');
      s.innerHTML = i.svg;   // copied from a sidebar icon or the fixed table above
      a.appendChild(s); a.appendChild(el('span', '', i.t)); res.appendChild(a);
    });
    var h = res.querySelector('.h');
    if (h && h.scrollIntoView) h.scrollIntoView({ block: 'nearest' });
  }
  var palFrom = null;
  function pal(open) {
    var p = $('pal');
    p.classList.toggle('on', !!open);
    if (open) { palFrom = doc.activeElement; closeAll(); $('q').value = ''; palIdx = 0; palBuild(); $('q').focus(); }
    else if (palFrom && palFrom.focus) { palFrom.focus(); palFrom = null; }
  }
  function palRun(i) {
    var it = palItems[i];
    if (!it) return;
    pal(0);
    if (it.go) root.location.hash = it.go;
    if (it.fn) it.fn();
  }

  /* ---- the sidebar's stream button follows the pill admin.js paints ---- */
  function syncGo() {
    var go = $('goBtn'), pill = $('streamPill');
    if (!go || !pill) return;
    var on = pill.classList.contains('on');
    go.className = 'go' + (on ? ' live' : '');
    go.textContent = '';
    go.appendChild(icon(on ? 'stop' : 'play'));
    go.appendChild(el('span', 'nl', on ? 'Stop stream' : 'Start stream'));
    go.dataset.tip = on ? 'Stop stream' : 'Start stream';
    var target = $(on ? 'stopBtn' : 'startBtn');
    go.disabled = !target || target.disabled;
  }

  function init() {
    apply();
    var t = $('sbToggle'); if (t) t.addEventListener('click', toggleSidebar);
    var sc = $('scrim'); if (sc) sc.addEventListener('click', function () { doc.body.classList.remove('open'); });
    var sb = $('searchBtn'); if (sb) sb.addEventListener('click', function () { pal(1); });
    var th = $('themeBtn'); if (th) th.addEventListener('click', cycleTheme);
    var lb = $('lookBtn'); if (lb) lb.addEventListener('click', openLook);
    var ab = $('acctBtn'); if (ab) ab.addEventListener('click', function (e) { toggle(e.currentTarget, { side: 'bottom', align: 'end', items: accountItems(), w: 224 }); });
    var ub = $('userBtn'); if (ub) ub.addEventListener('click', function (e) { toggle(e.currentTarget, { side: isMobile() ? 'top' : 'right', align: 'end', items: accountItems(), w: 224 }); });
    var go = $('goBtn');
    if (go) go.addEventListener('click', function () {
      var on = $('streamPill').classList.contains('on'), target = $(on ? 'stopBtn' : 'startBtn');
      if (target && !target.disabled) target.click();
    });
    var pill = $('streamPill');
    if (pill && root.MutationObserver) new root.MutationObserver(syncGo).observe(pill, { attributes: true, attributeFilter: ['class'] });
    ['startBtn', 'stopBtn'].forEach(function (id) {
      var b = $(id);
      if (b && root.MutationObserver) new root.MutationObserver(syncGo).observe(b, { attributes: true, attributeFilter: ['disabled'] });
    });
    syncGo();
    var nav = $('nav');
    if (nav) nav.addEventListener('click', function (e) {
      var nb = e.target.closest('.nb');
      if (!nb) return;
      var p = nb.parentElement;
      if (html.classList.contains('cl') && html.dataset.collapse === 'icon') {
        toggle(nb, { side: 'right', align: 'start', w: 192, items: Array.prototype.map.call(p.querySelectorAll('.subm a'), function (a) {
          return { t: a.textContent.trim(), go: a.getAttribute('href'), dis: a.classList.contains('dis') }; }) });
        return;
      }
      nb.setAttribute('aria-expanded', String(p.classList.toggle('open')));
    });
    /* A link in the drawer closes it. */
    if (nav) nav.addEventListener('click', function (e) { if (e.target.closest('a')) doc.body.classList.remove('open'); });
    var q = $('q');
    q.addEventListener('input', function () { palIdx = 0; palBuild(); });
    q.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); palIdx = Math.min(palIdx + 1, palItems.length - 1); palBuild(); }
      if (e.key === 'ArrowUp') { e.preventDefault(); palIdx = Math.max(palIdx - 1, 0); palBuild(); }
      if (e.key === 'Enter') palRun(palIdx);
    });
    $('res').addEventListener('click', function (e) { var a = e.target.closest('[data-n]'); if (a) palRun(+a.dataset.n); });
    $('res').addEventListener('mousemove', function (e) {
      var a = e.target.closest('[data-n]');
      if (a && +a.dataset.n !== palIdx) {
        palIdx = +a.dataset.n;
        Array.prototype.forEach.call($('res').querySelectorAll('a'), function (x) { x.classList.toggle('h', x === a); });
      }
    });
    $('pal').addEventListener('click', function (e) { if (e.target.id === 'pal') pal(0); });
    doc.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        if ($('pal').classList.contains('on')) { pal(0); return; }
        var p = pops[pops.length - 1];
        if (p) { closePop(p); if (p.anchor.focus) p.anchor.focus(); return; }
        if (doc.body.classList.contains('open')) doc.body.classList.remove('open');
      }
      if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && pops.length) {
        var items = Array.prototype.slice.call(pops[pops.length - 1].el.querySelectorAll('.mi'));
        if (!items.length) return;
        e.preventDefault();
        items[(items.indexOf(doc.activeElement) + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length].focus();
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); pal(!$('pal').classList.contains('on')); }
    });
    var hint = doc.querySelector('.search kbd');
    if (hint && !/Mac|iPhone|iPad/.test(root.navigator.platform || '')) hint.textContent = 'Ctrl K';
  }

  root.RelayShell = { page: pageChanged, toast: toast, closeAll: closeAll, state: function () { return cleanLook(S); } };
  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', init); else init();
})(typeof window !== 'undefined' ? window : globalThis);
