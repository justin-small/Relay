/* The Network page (#437, the closets redesign: docs/mockups/network/b-closet.html). It asks /api/admin/network/map
   (app/network_map.py), which merges Relay's own ports, mDNS discovery of NDI and Dante, and every module that adds
   to the map, with the names, notes and closets the operator saved, and draws:

     Racks            every switch's front panel in the closet it hangs in; a port shows link, PoE, NDI or Dante.
     Map              the tree below; Ports and notes, every device with the name Relay shows and a note.
     Find and add     the sources and which closet each switch is in. This computer is network.js.

   The map's tree:

     with a switch source: gateway, then switches and access points, with their clients underneath;
     without one:          Relay, then its network ports, then the devices found on each port.

   Everything it shows comes from the network, so it is set as text (textContent) and drawn as SVG from CSS classes
   that read the panel's tokens; nothing is markup. The pure parts (filter, layout) are tested in
   tests/js/network_map.test.mjs. */
(function (root) {
  'use strict';

  var NODE_W = 168, NODE_H = 44, GAP_X = 14, GAP_Y = 60, COLS = 3;

  /* ---- pure helpers ---- */
  function matches(dev, filter, query) {
    if (filter === 'av' && !(dev.ndi || dev.dante)) return false;
    if (filter === 'ndi' && !dev.ndi) return false;
    if (filter === 'dante' && !dev.dante) return false;
    var q = String(query || '').trim().toLowerCase();
    if (!q) return true;
    return [dev.name, dev.host, dev.mac].concat(dev.ips || []).join(' ').toLowerCase().indexOf(q) !== -1;
  }

  function isInfra(d) { return d.kind === 'gateway' || d.kind === 'switch' || d.kind === 'ap'; }

  /* The devices a filter keeps: those that match, and every device above one (so a client never floats). */
  function visibleSet(devices, filter, query) {
    var byId = {}, keep = {};
    devices.forEach(function (d) { byId[d.id] = d; });
    devices.forEach(function (d) {
      if (!matches(d, filter, query)) return;
      for (var n = d, guard = 0; n && guard < 10; n = byId[n.parent], guard++) keep[n.id] = true;
    });
    return keep;
  }

  /* Lay the map out. Returns {nodes: [{id, x, y, w, h, label, sub, ref}], links: [{from, to, ndi, dante}], width, height}. */
  function layout(data, filter, query) {
    var devices = data.devices.filter(function (d) { return d.kind !== 'relay'; });
    var keep = visibleSet(devices, filter, query);
    var shown = devices.filter(function (d) { return keep[d.id]; });
    var nodes = [], links = [], kids = {}, y;
    function add(id, x, yy, label, sub, ref, cls) { nodes.push({ id: id, x: x, y: yy, w: NODE_W, h: NODE_H, label: label, sub: sub, ref: ref, cls: cls || '' }); }
    function subOf(d) { return isInfra(d) && d.model ? d.model : d.ips && d.ips.length ? d.ips[0] : (d.mac || ''); }
    function link(a, b, d) { links.push({ from: a, to: b, ndi: !!(d && d.ndi), dante: !!(d && d.dante) }); }

    if (data.has_switch_source) {
      var ids = {};
      shown.forEach(function (d) { ids[d.id] = d; });
      shown.forEach(function (d) { var p = d.parent && ids[d.parent] ? d.parent : ''; (kids[p] = kids[p] || []).push(d); });
      var roots = (kids[''] || []).filter(isInfra);
      var loose = (kids[''] || []).filter(function (d) { return !isInfra(d); });
      /* width of a subtree: infra children side by side, clients in blocks of up to COLS columns */
      var wcache = {};
      var width = function (d) {
        if (wcache[d.id]) return wcache[d.id];
        var c = kids[d.id] || [], infra = c.filter(isInfra), cl = c.filter(function (x) { return !isInfra(x); });
        var wc = infra.reduce(function (s, x) { return s + width(x) + GAP_X; }, 0);
        var wl = cl.length ? Math.min(COLS, cl.length) * (NODE_W + GAP_X) : 0;
        return (wcache[d.id] = Math.max(NODE_W, wc + wl - (wc && wl ? 0 : 0)));
      };
      var place = function (d, left, top) {
        var w = width(d), c = kids[d.id] || [], infra = c.filter(isInfra), cl = c.filter(function (x) { return !isInfra(x); });
        /* over its own devices when it has some, else over the first switch below it */
        var own = cl.length ? Math.min(COLS, cl.length) * (NODE_W + GAP_X) - GAP_X : (infra.length ? width(infra[0]) : NODE_W);
        var x = left, row = top + NODE_H + GAP_Y;
        cl.forEach(function (ch, i) {           // a switch's own devices first, under it, then the switches below it
          var col = i % COLS, rr = Math.floor(i / COLS);
          add(ch.id, x + col * (NODE_W + GAP_X), row + rr * (NODE_H + 10), ch.name, subOf(ch), ch, '');
          link(d.id, ch.id, ch);
        });
        if (cl.length) x += Math.min(COLS, cl.length) * (NODE_W + GAP_X);
        infra.forEach(function (ch) { place(ch, x, row); link(d.id, ch.id, ch); x += width(ch) + GAP_X; });
        var at = cl.length ? left + (own - NODE_W) / 2 : (infra.length ? nodes.filter(function (n) { return n.id === infra[0].id; })[0].x : left + (w - NODE_W) / 2);
        add(d.id, Math.max(left, at), top, d.name, subOf(d), d, isInfra(d) ? 'infra' : '');
        return w;
      };
      var left = 0;
      roots.forEach(function (r) { left += place(r, left, 0) + GAP_X * 2; });
      y = nodes.reduce(function (m, n) { return Math.max(m, n.y + n.h); }, 0) + GAP_Y;
      loose.forEach(function (d, i) { add(d.id, (i % 5) * (NODE_W + GAP_X), y + Math.floor(i / 5) * (NODE_H + 10), d.name, subOf(d), d, ''); });
    } else {
      var ports = data.relay_ports || [], per = {}, other = [];
      shown.forEach(function (d) { if (d.via) (per[d.via] = per[d.via] || []).push(d); else other.push(d); });
      var cols = ports.map(function (p) { return { id: 'port:' + p.id, name: p.friendly_name || p.name, sub: (p.ipv4 && p.ipv4[0]) || '', list: per[p.id] || [] }; });
      if (other.length) cols.push({ id: 'port:other', name: 'Not placed', sub: 'No network port matches', list: other });
      var x0 = 0, colW = [];
      cols.forEach(function (c) { var n = Math.max(1, Math.min(COLS, c.list.length)); colW.push(n * (NODE_W + GAP_X)); });
      var total = colW.reduce(function (s, v) { return s + v + GAP_X; }, 0) || NODE_W;
      add('relay', Math.max(0, (total - NODE_W) / 2), 0, 'This Relay computer', '', null, 'infra');
      cols.forEach(function (c, i) {
        var cx = x0 + (colW[i] - NODE_W) / 2 - GAP_X / 2;
        add(c.id, cx, NODE_H + GAP_Y, c.name, c.sub, null, 'infra');
        link('relay', c.id, null);
        c.list.forEach(function (d, k) {
          add(d.id, x0 + (k % COLS) * (NODE_W + GAP_X), NODE_H * 2 + GAP_Y * 2 + Math.floor(k / COLS) * (NODE_H + 10), d.name, subOf(d), d, '');
          link(c.id, d.id, d);
        });
        x0 += colW[i] + GAP_X;
      });
    }
    var w = nodes.reduce(function (m, n) { return Math.max(m, n.x + n.w); }, 0) + 8;
    var h = nodes.reduce(function (m, n) { return Math.max(m, n.y + n.h); }, 0) + 8;
    return { nodes: nodes, links: links, width: Math.max(w, 320), height: Math.max(h, 120) };
  }

  /* "12 s ago", "3 min ago": how old a read is. */
  function age(seconds) {
    if (seconds == null) return '';
    if (seconds < 5) return 'just now';
    if (seconds < 90) return Math.round(seconds) + ' s ago';
    if (seconds < 5400) return Math.round(seconds / 60) + ' min ago';
    return Math.round(seconds / 3600) + ' h ago';
  }

  /* ---- the racks: pure parts ---- */
  var GEAR_ORDER = { gateway: 0, switch: 1, ap: 2 };

  /* The closets to draw: each saved place with its switches in order, then what is in none ("Not placed"). With no
     places saved, one closet holds all the gear: gateway, switches, access points. */
  function closets(devices, places) {
    var gear = devices.filter(isInfra).slice().sort(function (a, b) {
      return (GEAR_ORDER[a.kind] - GEAR_ORDER[b.kind]) || String(a.name).localeCompare(String(b.name));
    });
    if (!gear.length) return [];
    if (!places || !places.length) return [{ name: 'Network gear', gear: gear, unplaced: false }];
    var byId = {}, used = {};
    gear.forEach(function (g) { byId[g.id] = g; });
    var out = places.map(function (p) {
      var list = (p.gear || []).filter(function (id) { return byId[id] && !used[id]; }).map(function (id) { used[id] = true; return byId[id]; });
      return { name: p.name, gear: list, unplaced: false };
    });
    var rest = gear.filter(function (g) { return !used[g.id]; });
    if (rest.length) out.push({ name: 'Not placed', gear: rest, unplaced: true });
    return out;
  }

  /* What one switch port shows: the device on it (by MAC), link, PoE, NDI or Dante, and a problem. */
  function portInfo(port, byMac) {
    var dev = port.client_mac ? byMac[port.client_mac] || null : null;
    var info = { port: port.port, dev: dev, up: !!port.up, speed: port.speed, poe: port.poe_w || 0, errors: port.errors || 0,
      warn: port.warn || '', uplink: !!port.uplink, ndi: !!(dev && dev.ndi), dante: !!(dev && dev.dante),
      client: dev ? dev.name : (port.client || ''), name: port.name || '' };
    info.cls = 'nmport' + (info.up ? (info.warn ? ' slow' : ' up') : '') + (info.ndi ? ' ndi' : '') + (info.dante ? ' dante' : '') +
      (info.poe ? ' poe' : '') + (info.uplink ? ' uplink' : '');
    return info;
  }

  function keepPort(info, hl) {
    if (hl === 'ndi') return info.ndi;
    if (hl === 'dante') return info.dante;
    if (hl === 'poe') return info.poe > 0;
    if (hl === 'check') return !!info.warn;
    return true;
  }

  function portCount(g) {
    return (g.ports || []).reduce(function (m, p) { return Math.max(m, p.port || 0); }, 0);
  }

  function speedText(m) { return !m ? '' : m >= 1000 ? (m / 1000) + ' Gb/s' : m + ' Mb/s'; }

  var api = { matches: matches, visibleSet: visibleSet, layout: layout, age: age, closets: closets, portInfo: portInfo,
              keepPort: keepPort, portCount: portCount, speedText: speedText };
  if (typeof module !== 'undefined' && module.exports) { module.exports = api; return; }
  if (!root.document || !root.document.getElementById('nmClosets')) return;

  /* ---- the page ---- */
  var doc = root.document;
  function $(id) { return doc.getElementById(id); }
  var el = root.relayUI.el;
  var send = root.relayUI.api;
  var canManage = root.relayUI.can('network.manage');
  var SVG = 'http://www.w3.org/2000/svg';
  function sv(tag, attrs, parent, text) {
    var e = doc.createElementNS(SVG, tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text != null) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  }

  var data = null, tab = 'racks', hl = 'all', filter = 'all', pfilter = 'all', query = '', timer = 0, loading = false;
  var pick = { sw: '', port: null };     // the selected switch and port on Racks
  var mapSel = '';                        // the selected device on Map
  var notesDirty = {};                    // notes typed on Ports and notes, not saved yet
  var places = null;                      // the places being edited on Find and add
  var byMac = {}, byId = {};

  function load(force) {
    if (loading) return;
    loading = true;
    $('nmRefresh').disabled = true;
    root.fetch('/api/admin/network/map' + (force ? '?refresh=1' : ''), { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error('refused'); return r.json(); })
      .then(function (d) { data = d; paint(); if (canManage) loadPicks(); })
      .catch(function () { $('nmStatus').textContent = 'The map could not be read. Try Look again now.'; })
      .then(function () { loading = false; $('nmRefresh').disabled = false; });
  }

  function editing() {
    var a = doc.activeElement;
    return a && $('page-network').contains(a) && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.tagName === 'SELECT');
  }

  function paint() {
    if (!data) return;
    byMac = {}; byId = {};
    data.devices.forEach(function (d) { byId[d.id] = d; if (d.mac) byMac[d.mac] = d; });
    summary();
    paintSources();
    if (editing()) return;                 // do not redraw under the operator's typing; the next read draws
    paintRacks();
    paintMap();
    paintRows();
    paintCables();
    if (!places) paintPlaces();
  }

  function summary() {
    var sum = $('nmSum'), gear = data.devices.filter(isInfra);
    var up = gear.filter(function (g) { return !(g.stale_by && g.stale_by.length); }).length;
    sum.textContent = '';
    if (gear.length) sum.appendChild(el('span', 'spill ' + (up === gear.length ? 'on' : 'warn'), el('i', 'dot ' + (up === gear.length ? 'ok' : 'warn')),
      up + ' of ' + gear.length + ' switches up'));
    else sum.appendChild(el('span', 'spill', data.devices.length + (data.devices.length === 1 ? ' device' : ' devices')));
    var sources = data.sources.filter(function (s) { return s.on; });
    $('nmStatus').textContent = data.devices.length + (data.devices.length === 1 ? ' device' : ' devices') + ' from ' + sources.length +
      (sources.length === 1 ? ' source' : ' sources') + ' · read ' + age(Math.max(0, Date.now() / 1000 - data.generated_at)) +
      '. Relay looks again every 30 seconds.';
  }

  /* ---------------------------------------------------------------- Racks */
  function drawSwitch(g) {
    var n = portCount(g), rows = n > 8 ? 2 : 1, cols = Math.ceil(n / rows) || 0, pw = 30, ph = 24, gx = 6, left = 150;
    var W = Math.max(520, left + cols * (pw + gx) + 40), H = rows === 2 ? 96 : 84;
    var svg = sv('svg', { 'class': 'nmhw', viewBox: '0 0 ' + W + ' ' + H, role: 'group', 'aria-label': g.name + ', front' });
    svg.style.maxWidth = Math.round(W * 1.15) + 'px';
    sv('rect', { 'class': 'nmhw-ear', x: 0, y: 2, width: 18, height: H - 4, rx: 3 }, svg);
    sv('rect', { 'class': 'nmhw-ear', x: W - 18, y: 2, width: 18, height: H - 4, rx: 3 }, svg);
    [9, W - 9].forEach(function (x) { sv('circle', { 'class': 'nmhw-screw', cx: x, cy: 14, r: 3.5 }, svg); sv('circle', { 'class': 'nmhw-screw', cx: x, cy: H - 14, r: 3.5 }, svg); });
    sv('rect', { 'class': 'nmhw-body', x: 18, y: 2, width: W - 36, height: H - 4, rx: 4 }, svg);
    sv('text', { 'class': 'nmhw-brand', x: 30, y: 22 }, svg, clip(g.model || ({ gateway: 'Gateway', switch: 'Switch', ap: 'Access point' }[g.kind] || ''), 17));
    sv('text', { 'class': 'nmhw-name', x: 30, y: 40 }, svg, clip(g.name, 18));
    var lcd = sv('g', { 'class': 'nmhw-lcd' }, svg);
    sv('rect', { x: 30, y: H - 34, width: 104, height: 22, rx: 3 }, lcd);
    sv('text', { x: 36, y: H - 19 }, lcd, g.poe_budget_w ? 'PoE ' + Math.round(g.poe_w || 0) + '/' + Math.round(g.poe_budget_w) + 'W' : ((g.ips || [])[0] || ''));
    var list = {};
    (g.ports || []).forEach(function (p) { list[p.port] = p; });
    for (var i = 0; i < n; i++) {
      var num = i + 1, r = rows === 2 ? i % 2 : 0, c = rows === 2 ? Math.floor(i / 2) : i;
      var x = left + c * (pw + gx), y = rows === 2 ? (r ? 54 : 16) : 30, low = y > 40;
      var info = list[num] ? portInfo(list[num], byMac) : { port: num, cls: 'nmport', up: false };
      var cls = info.cls + (pick.sw === g.id && pick.port === num ? ' sel' : '') + (list[num] && !keepPort(info, hl) ? ' dim' : '') + (!list[num] && hl !== 'all' ? ' dim' : '');
      var gp = sv('g', { 'class': cls, tabindex: '0', role: 'button', 'data-sw': g.id, 'data-port': String(num),
        'aria-label': 'Port ' + num + (info.client ? ', ' + info.client : info.up ? ', link up' : ', empty') }, svg);
      sv('rect', { 'class': 'jack', x: x, y: y, width: pw, height: ph, rx: 3 }, gp);
      sv('rect', { 'class': 'tab', x: x + pw / 2 - 6, y: y + (low ? ph - 6 : 2), width: 12, height: 4, rx: 1 }, gp);
      sv('circle', { 'class': 'led', cx: x + 5, cy: y + (low ? 5 : ph - 5), r: 2.6 }, gp);
      sv('circle', { 'class': 'poe', cx: x + pw - 5, cy: y + (low ? 5 : ph - 5), r: 2.6 }, gp);
      sv('text', { 'class': 'nmhw-pn', x: x + pw / 2, y: low ? y + ph + 9 : y - 3, 'text-anchor': 'middle' }, gp, String(num));
    }
    return svg;
  }

  function drawAp(g) {
    var svg = sv('svg', { 'class': 'nmhw nmhw-ap', viewBox: '0 0 520 70', role: 'img', 'aria-label': g.name });
    svg.style.maxWidth = '600px';
    sv('rect', { 'class': 'nmhw-body', x: 0, y: 4, width: 520, height: 62, rx: 31 }, svg);
    sv('circle', { 'class': 'nmhw-ring', cx: 40, cy: 35, r: 6 }, svg);
    sv('text', { 'class': 'nmhw-brand', x: 60, y: 30 }, svg, clip(g.model || 'Access point', 30));
    sv('text', { 'class': 'nmhw-name', x: 60, y: 48 }, svg, clip(g.name, 40));
    return svg;
  }

  function paintRacks() {
    var root2 = $('nmClosets');
    root2.textContent = '';
    var list = closets(data.devices, data.places);
    $('nmNoGear').hidden = list.length > 0;
    if (!pick.sw && list.length && list[0].gear.length) pick = { sw: list[0].gear[0].id, port: null };
    list.forEach(function (c) {
      var box = el('div', 'nmstack');
      box.appendChild(el('b', '', c.name));
      var rack = el('div', 'nmrack');
      c.gear.forEach(function (g) {
        var u = el('div', 'nmunit' + (pick.sw === g.id ? ' on' : ''));
        u.appendChild(el('span', 'cap', [g.model, (g.ips || [])[0]].filter(Boolean).join(' · ') || g.name));
        u.appendChild((g.ports && g.ports.length) ? drawSwitch(g) : drawAp(g));
        u.addEventListener('click', function (e) { if (!e.target.closest('.nmport')) { pick = { sw: g.id, port: null }; paintRacks(); } });
        rack.appendChild(u);
      });
      box.appendChild(rack);
      root2.appendChild(box);
    });
    paintIssues();
    paintDrawer();
  }

  function paintIssues() {
    var list = data.issues || [];
    $('nmLook').hidden = !list.length;
    $('nmChecked').textContent = 'Checked ' + age(Math.max(0, Date.now() / 1000 - data.generated_at));
    var box = $('nmIssues');
    box.textContent = '';
    list.slice(0, 8).forEach(function (i) {
      var row = el('div', 'nmissue');
      row.appendChild(el('span', 'sev ' + i.level));
      var t = el('div', '');
      t.appendChild(el('b', '', i.title));
      t.appendChild(el('p', '', i.detail));
      row.appendChild(t);
      if (i.device || i.switch) {
        var b = el('button', 'sm', 'Show');
        b.type = 'button';
        b.addEventListener('click', function () {
          if (i.switch && i.port) pick = { sw: i.switch, port: i.port };
          else if (byId[i.device] && isInfra(byId[i.device])) pick = { sw: i.device, port: null };
          else { mapSel = i.device; choose('map'); return; }
          paintRacks();
        });
        row.appendChild(b);
      } else row.appendChild(el('span'));
      box.appendChild(row);
    });
    if (list.length > 8) box.appendChild(el('p', 'note', (list.length - 8) + ' more. Use Problems above to see them on the switches.'));
  }

  function paintDrawer() {
    var dr = $('nmDrawer');
    dr.textContent = '';
    var g = byId[pick.sw];
    if (!g) return;
    if (pick.port) {
      var p = (g.ports || []).filter(function (x) { return x.port === pick.port; })[0];
      var info = p ? portInfo(p, byMac) : null;
      if (info && info.dev) detail(dr, info.dev, { sw: g, info: info });
      else {
        var c = el('section', 'nmcard');
        c.appendChild(el('b', '', g.name + ' · port ' + pick.port));
        c.appendChild(el('p', 'muted', info && info.up ? (info.client ? info.client + ' is on it; Relay does not know it by address.' : 'A link is up but Relay does not know what is on it.') : 'Nothing plugged in.'));
        if (info) { var kv = el('dl', 'nmkv'); kvRow(kv, 'Link', info.up ? speedText(info.speed) || 'Up' : 'Down'); kvRow(kv, 'PoE', info.poe ? info.poe + ' W' : ''); kvRow(kv, 'Errors', info.errors ? String(info.errors) : ''); kvRow(kv, 'Check', info.warn); kvRow(kv, 'Name on the switch', info.name); c.appendChild(kv); }
        if (p) portNameField(c, p);
        noteField(c, g.id + '#' + pick.port);
        dr.appendChild(c);
      }
      if (info && info.dev && p) { var pn = el('section', 'nmcard', el('b', '', g.name + ' · port ' + pick.port)); portNameField(pn, p); dr.appendChild(pn); }
    } else detail(dr, g, null);
    if (g.poe_budget_w) {
      var share = Math.min(1, (g.poe_w || 0) / g.poe_budget_w);
      var pc = el('section', 'nmcard');
      pc.appendChild(el('h4', '', 'Power over Ethernet'));
      var m = el('div', 'nmmeter' + (share >= 0.85 ? ' warn' : ''));
      var fill = el('i'); fill.style.width = Math.round(share * 100) + '%'; m.appendChild(fill);
      pc.appendChild(m);
      pc.appendChild(el('p', 'note', Math.round(g.poe_w || 0) + ' of ' + Math.round(g.poe_budget_w) + ' W in use' + (share >= 0.85 ? '. Another powered device here may not start.' : '.')));
      dr.appendChild(pc);
    }
  }

  function kvRow(dl, k, v) { if (v == null || v === '') return; dl.appendChild(el('dt', '', k)); dl.appendChild(el('dd', '', v)); }

  function where(d) {
    var up = d.parent ? byId[d.parent] : null;
    if (up) return up.name + (d.uplink_port != null ? ', port ' + d.uplink_port : '');
    var p = d.via ? (data.relay_ports || []).filter(function (x) { return x.id === d.via; })[0] : null;
    return p ? 'This computer’s ' + (p.friendly_name || p.name) : '';
  }
  function placeOf(g) {
    var c = closets(data.devices, data.places).filter(function (x) { return x.gear.indexOf(g) !== -1; })[0];
    return c && !c.unplaced && data.places.length ? c.name : '';
  }
  function what(d) {
    return d.model || (d.tags || []).filter(function (t) { return t !== d.model; }).join(' · ') ||
      ({ gateway: 'Gateway', switch: 'Switch', ap: 'Access point', relay: 'Relay', av: 'AV device' }[d.kind] || '');
  }
  function label(id) { var s = data.sources.filter(function (x) { return x.id === id; })[0]; return s ? s.label : id; }
  function stale(d) { return d.seen_by && d.seen_by.length && d.seen_by.every(function (s) { return d.stale_by.indexOf(s) !== -1; }); }
  function portOf(d) {
    var up = d.parent ? byId[d.parent] : null;
    return up && d.uplink_port != null ? (up.ports || []).filter(function (p) { return p.port === d.uplink_port; })[0] : null;
  }

  /* One device or switch: what Relay knows, its note, and a way to reach it. */
  function detail(parent, d, at) {
    var c = el('section', 'nmcard');
    var port = at ? at.info : (portOf(d) ? portInfo(portOf(d), byMac) : null);
    var warn = port && port.warn;
    var hd = el('div', 'nmbetween');
    hd.appendChild(el('b', '', d.name));
    hd.appendChild(el('span', 'badge ' + (stale(d) ? 'err' : warn ? 'warn' : 'ok'), stale(d) ? 'Not answering' : warn ? 'Check' : 'Online'));
    c.appendChild(hd);
    if (d.own_name && d.own_name !== d.name) c.appendChild(el('p', 'note', 'Calls itself ' + d.own_name));
    if (at) c.appendChild(el('p', 'note', 'On ' + at.sw.name + ', port ' + at.info.port + '.'));
    var w = what(d);
    if (w) c.appendChild(el('p', 'muted', w));
    var tags = el('div', 'nmtags');
    if (d.ndi) tags.appendChild(el('span', 'nmtag ndi', 'NDI'));
    if (d.dante) tags.appendChild(el('span', 'nmtag dante', 'Dante'));
    (d.tags || []).forEach(function (t) { if (t !== d.model) tags.appendChild(el('span', 'nmtag', t)); });
    if (tags.children.length) c.appendChild(tags);
    if (warn) c.appendChild(el('p', 'note warn', warn));
    (d.notes || []).forEach(function (n) { c.appendChild(el('p', 'note warn', n)); });
    var kv = el('dl', 'nmkv');
    kvRow(kv, 'Address', (d.ips || []).join(', '));
    kvRow(kv, 'MAC', d.mac);
    if (!isInfra(d)) {
      kvRow(kv, 'Plugged into', where(d));
      if (port) { kvRow(kv, 'Link', speedText(port.speed)); kvRow(kv, 'PoE', port.poe ? port.poe + ' W' : ''); kvRow(kv, 'Errors', port.errors ? port.errors + '' : ''); }
    } else {
      kvRow(kv, 'Where', placeOf(d));
      if (d.parent && byId[d.parent]) kvRow(kv, 'Uplink', byId[d.parent].name + (d.uplink_port != null ? ', port ' + d.uplink_port : ''));
      kvRow(kv, 'Ports', portCount(d) ? portCount(d) + ', ' + (d.ports || []).filter(function (p) { return p.up; }).length + ' in use' : '');
    }
    kvRow(kv, 'Seen by', (d.seen_by || []).map(label).join(', '));
    c.appendChild(kv);
    if (stale(d)) c.appendChild(el('p', 'note', 'From an older read: ' + d.stale_by.map(label).join(', ') + '.'));
    noteField(c, d.id);
    var g = el('div', 'nmtoolbar');
    var ip = (d.ips || [])[0];
    if (ip && d.kind !== 'relay') { var a = el('a', 'btn sm', 'Open its web page'); a.href = 'http://' + (ip.indexOf(':') !== -1 ? '[' + ip + ']' : ip) + '/'; a.target = '_blank'; a.rel = 'noopener'; g.appendChild(a); }
    if (ip) {
      var cp = el('button', 'sm', 'Copy address'); cp.type = 'button';
      cp.addEventListener('click', function () { try { root.navigator.clipboard.writeText(ip); cp.textContent = 'Copied'; } catch (e) { /* no clipboard: the address is shown above */ } });
      g.appendChild(cp);
    }
    if (g.children.length) c.appendChild(g);
    parent.appendChild(c);
  }

  /* A port's name (#543): the switch's own name is kept, and a name typed here shows in Relay only, beside it. Empty
     text shows the switch's name again. A switch port cannot be renamed on the switch from Relay. */
  function portNameField(card, p) {
    var pid = String(p.ref || '').split(':')[2];
    if (!pid) return;
    var f = el('div', 'field');
    var id = 'nmPortName-' + pid;
    var lab = el('label', '', 'Name in Relay'); lab.htmlFor = id;
    var t = el('input'); t.id = id; t.maxLength = 60; t.value = p.relay_name || '';
    t.placeholder = p.gear_name || ('Port ' + p.port);
    t.disabled = !canManage;
    var said = el('span', 'hint', p.gear_name ? 'The switch calls it ' + p.gear_name + '. A name here changes Relay only.' : 'A name here changes Relay only.');
    t.addEventListener('change', function () {
      send('POST', '/api/admin/network/ports/relay/' + pid + '/rename', { name: t.value.trim() }).then(function (r) {
        p.relay_name = r.port.name; p.name = r.port.name || p.gear_name; said.textContent = 'Saved. Relay only: the switch keeps its own name.';
        paintRacks();
      }).catch(function (e) { said.textContent = e.message; });
    });
    f.appendChild(lab); f.appendChild(t); f.appendChild(said);
    card.appendChild(f);
  }

  /* A note field that saves when the operator leaves it. */
  function noteField(card, key) {
    var f = el('div', 'field');
    var id = 'nmNote-' + key.replace(/[^a-z0-9]/gi, '');
    var lab = el('label', '', 'Note'); lab.htmlFor = id;
    var t = el('textarea'); t.id = id; t.rows = 2; t.maxLength = 300;
    t.value = (data.notes || {})[key] || '';
    t.placeholder = 'Where the cable runs, who to ask…';
    t.disabled = !canManage;
    var said = el('span', 'hint', '');
    t.addEventListener('change', function () {
      var body = { notes: {} }; body.notes[key] = t.value.trim();
      send('POST', '/api/admin/network/map/labels', body).then(function (st) { data.notes = st.notes; said.textContent = 'Saved.'; })
        .catch(function (e) { said.textContent = e.message; });
    });
    f.appendChild(lab); f.appendChild(t); f.appendChild(said);
    card.appendChild(f);
  }

  /* hover tip and picking a port */
  var tip = null;
  function tips(rootEl) {
    if (!tip) { tip = el('div', 'nmtip'); doc.body.appendChild(tip); }
    function info(gp) {
      var g = byId[gp.dataset.sw], num = +gp.dataset.port;
      var p = g && (g.ports || []).filter(function (x) { return x.port === num; })[0];
      return { g: g, num: num, info: p ? portInfo(p, byMac) : null };
    }
    rootEl.addEventListener('mouseover', function (e) {
      var gp = e.target.closest && e.target.closest('.nmport'); if (!gp) return;
      var x = info(gp); if (!x.g) return;
      tip.textContent = '';
      tip.appendChild(el('b', '', x.g.name + ' · port ' + x.num));
      var dl = el('dl');
      var i = x.info;
      if (i && (i.dev || i.client)) {
        kvRow(dl, 'Device', i.client);
        if (i.dev) { kvRow(dl, 'What', what(i.dev)); kvRow(dl, 'Address', (i.dev.ips || [])[0]); }
      } else kvRow(dl, 'Device', i && i.up ? 'Link up, not known' : 'Nothing plugged in');
      if (i && i.up) kvRow(dl, 'Link', speedText(i.speed) || 'Up');
      if (i && i.poe) kvRow(dl, 'PoE', i.poe + ' W');
      if (i && i.errors) kvRow(dl, 'Errors', String(i.errors));
      if (i && i.warn) kvRow(dl, 'Check', i.warn);
      var note = (data.notes || {})[i && i.dev ? i.dev.id : x.g.id + '#' + x.num];
      kvRow(dl, 'Note', note);
      tip.appendChild(dl);
      tip.style.display = 'block';
    });
    rootEl.addEventListener('mousemove', function (e) {
      if (tip.style.display === 'block') { tip.style.left = Math.min(root.innerWidth - 290, e.clientX + 14) + 'px'; tip.style.top = (e.clientY + 14) + 'px'; }
    });
    rootEl.addEventListener('mouseout', function (e) { if (e.target.closest && e.target.closest('.nmport')) tip.style.display = 'none'; });
    function choosePort(gp) { pick = { sw: gp.dataset.sw, port: +gp.dataset.port }; tip.style.display = 'none'; paintRacks(); }
    rootEl.addEventListener('click', function (e) { var gp = e.target.closest && e.target.closest('.nmport'); if (gp) choosePort(gp); });
    rootEl.addEventListener('keydown', function (e) {
      var gp = e.target.closest && e.target.closest('.nmport');
      if (gp && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); choosePort(gp); }
    });
  }

  /* ---------------------------------------------------------------- Map */
  function paintMap() {
    var frame = $('nmMap');
    frame.textContent = '';
    var lay = layout(data, filter, '');
    if (!lay.nodes.length) { frame.appendChild(el('p', 'note', 'No devices match.')); }
    else {
      var svg = sv('svg', { viewBox: '0 0 ' + lay.width + ' ' + lay.height, width: lay.width, height: lay.height, role: 'img',
                            'aria-label': 'Network map: ' + lay.nodes.length + ' items' });
      var at = {};
      lay.nodes.forEach(function (n) { at[n.id] = n; });
      lay.links.forEach(function (l) {
        var a = at[l.from], b = at[l.to];
        if (!a || !b) return;
        var slow = b.ref && portOf(b.ref) && portOf(b.ref).warn;
        var x1 = a.x + a.w / 2, y1 = a.y + a.h, x2 = b.x + b.w / 2, y2 = b.y, ym = (y1 + y2) / 2;
        sv('path', { d: 'M' + x1 + ' ' + y1 + 'C' + x1 + ' ' + ym + ' ' + x2 + ' ' + ym + ' ' + x2 + ' ' + y2,
                     'class': 'nm-link' + (l.ndi ? ' ndi' : '') + (l.dante ? ' dante' : '') + (slow ? ' slow' : '') }, svg);
      });
      (data.cables || []).forEach(function (c) {              // a cable between two devices drawn here, apart from the tree
        var a = at[c.a.device], b = at[c.b.device];
        if (!a || !b || a === b) return;
        var tree = lay.links.some(function (l) { return (l.from === a.id && l.to === b.id) || (l.from === b.id && l.to === a.id); });
        if (tree && c.reported) return;                         // a reported uplink is the line already drawn
        var p = sv('path', { d: 'M' + (a.x + a.w / 2) + ' ' + (a.y + a.h / 2) + 'L' + (b.x + b.w / 2) + ' ' + (b.y + b.h / 2),
                             'class': 'nm-cable' + (c.missing ? ' missing' : '') }, svg);
        sv('title', {}, p, cableText(c));
      });
      lay.nodes.forEach(function (n) {
        var d = n.ref, warn = d && portOf(d) && portOf(d).warn;
        var cls = 'nm-node ' + n.cls + (d && d.ndi ? ' ndi' : '') + (d && d.dante ? ' dante' : '') + (warn ? ' warn' : '') +
          (d && d.stale_by && d.stale_by.length ? ' stale' : '') + (d && d.id === mapSel ? ' sel' : '');
        var g = sv('g', { 'class': cls, tabindex: d ? '0' : '-1', role: d ? 'button' : 'presentation', transform: 'translate(' + n.x + ' ' + n.y + ')' }, svg);
        if (d) {
          g.setAttribute('aria-label', n.label + (n.sub ? ', ' + n.sub : ''));
          g.addEventListener('click', function () { mapSel = d.id; paintMap(); });
          g.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); mapSel = d.id; paintMap(); } });
        }
        sv('rect', { width: n.w, height: n.h, rx: 8 }, g);
        sv('text', { x: 10, y: 17, 'class': 'nm-name' }, g, (warn ? '⚠ ' : '') + clip(n.label, 21));
        sv('text', { x: 10, y: 32, 'class': 'nm-sub' }, g, clip(d && !isInfra(d) && d.uplink_port != null ? n.sub + ' · port ' + d.uplink_port : n.sub, 26));
      });
      frame.appendChild(svg);
    }
    var det = $('nmMapDetail');
    det.textContent = '';
    if (!mapSel || !byId[mapSel]) {
      var first = data.devices.filter(function (d) { return d.kind !== 'relay' && !isInfra(d); })[0];
      mapSel = first ? first.id : '';
    }
    if (byId[mapSel]) detail(det, byId[mapSel], null);
  }

  /* ---------------------------------------------------------------- Ports and notes */
  function rowMatches(d) {
    if (pfilter === 'check') { var p = portOf(d); if (!(p && p.warn) && !stale(d)) return false; }
    else if (!matches(d, pfilter, '')) return false;
    var sw = $('nmSwitch').value;
    if (sw && d.parent !== sw) return false;
    var q = query.trim().toLowerCase();
    return !q || [d.name, d.own_name, d.mac].concat(d.ips || []).join(' ').toLowerCase().indexOf(q) !== -1;
  }

  function nameCell(td, d) {
    td.textContent = '';
    var p = portOf(d);
    if (!canManage) { td.appendChild(el('b', '', d.name)); }
    else {
      var b = el('button', 'nmname', (p && p.warn ? '⚠ ' : '') + d.name);
      b.type = 'button'; b.title = 'Click to rename'; b.setAttribute('aria-label', 'Rename ' + d.name);
      b.addEventListener('click', function () {
        td.textContent = '';
        var inp = el('input', 'nmnameedit'); inp.value = d.name; inp.maxLength = 60; inp.placeholder = d.own_name;
        inp.setAttribute('aria-label', 'Name for ' + d.own_name);
        var done = false;
        function finish(keep) {
          if (done) return; done = true;
          var v = inp.value.trim().replace(/\s+/g, ' ');
          if (!keep || v === d.name) { nameCell(td, d); return; }
          var body = { names: {} }; body.names[d.id] = v === d.own_name ? '' : v;
          send('POST', '/api/admin/network/map/labels', body).then(function (st) {
            data.names = st.names; d.name = st.names[d.id] || d.own_name; nameCell(td, d); paintRacks();
          }).catch(function (e) { showErr(e.message); nameCell(td, d); });
        }
        inp.addEventListener('keydown', function (k) { if (k.key === 'Enter') { k.preventDefault(); finish(true); } if (k.key === 'Escape') finish(false); });
        inp.addEventListener('blur', function () { finish(true); });
        td.appendChild(inp);
        inp.focus(); inp.select();
      });
      td.appendChild(b);
    }
    if (d.own_name && d.own_name !== d.name) td.appendChild(el('small', 'hint', 'Device calls itself ' + d.own_name));
    if (stale(d)) td.appendChild(el('span', 'badge err', 'Not answering'));
  }

  function paintRows() {
    var sel = $('nmSwitch'), cur = sel.value;
    sel.textContent = '';
    sel.appendChild(el('option', { value: '', text: 'Every switch' }));
    data.devices.filter(isInfra).forEach(function (g) { if (g.ports && g.ports.length) sel.appendChild(el('option', { value: g.id, text: g.name })); });
    sel.value = cur;
    var body = $('nmRows');
    body.textContent = '';
    data.devices.filter(function (d) { return !isInfra(d) && rowMatches(d); }).forEach(function (d) {
      var tr = el('tr'), p = portOf(d), info = p ? portInfo(p, byMac) : null;
      var n = el('td'); nameCell(n, d); tr.appendChild(n);
      tr.appendChild(el('td', 'muted', what(d)));
      tr.appendChild(el('td', 'mono', (d.ips || [])[0] || ''));
      tr.appendChild(el('td', '', where(d) || '—'));
      var link = el('td', info && info.warn ? 'warn' : '', info ? speedText(info.speed) : (d.speed ? speedText(d.speed) : ''));
      tr.appendChild(link);
      tr.appendChild(el('td', '', info && info.poe ? info.poe + ' W' : ''));
      var carries = el('td');
      if (d.ndi) carries.appendChild(el('span', 'nmtag ndi', 'NDI'));
      if (d.dante) carries.appendChild(el('span', 'nmtag dante', 'Dante'));
      tr.appendChild(carries);
      var nt = el('td'), inp = el('input', 'nmnote');
      inp.value = notesDirty.hasOwnProperty(d.id) ? notesDirty[d.id] : (data.notes || {})[d.id] || '';
      inp.placeholder = 'Add a note'; inp.maxLength = 300; inp.disabled = !canManage;
      inp.setAttribute('aria-label', 'Note for ' + d.name);
      inp.addEventListener('input', function () { notesDirty[d.id] = inp.value; });
      nt.appendChild(inp); tr.appendChild(nt);
      body.appendChild(tr);
    });
    if (!body.children.length) { var r = el('tr'), c = el('td', 'muted', 'No devices match.'); c.colSpan = 8; r.appendChild(c); body.appendChild(r); }
  }

  /* ---------------------------------------------------------------- Cables */
  var cableA = null, cableB = null;       // the shared port pickers (relay-ui.js) for the ends of a new cable
  function cableEnd(e) { return (e.gear ? e.gear + ' · ' : '') + e.shown; }
  function cableText(c) { return cableEnd(c.a) + ' to ' + cableEnd(c.b) + (c.note ? ' (' + c.note + ')' : ''); }
  function sourceLabel(id) {
    var s = (data.sources || []).filter(function (x) { return x.id === id; })[0];
    return s ? s.label : id;
  }
  function cableErr(t) { $('nmCableErr').textContent = t || ''; $('nmCableErr').hidden = !t; }
  function paintCables() {
    var body = $('nmCableRows');
    if (!body) return;
    body.textContent = '';
    (data.cables || []).forEach(function (c) {
      var tr = el('tr');
      tr.appendChild(el('td', '', cableEnd(c.a)));
      tr.appendChild(el('td', '', cableEnd(c.b)));
      var by = el('td', 'muted', c.reported ? sourceLabel(c.by) : 'An operator');
      if (c.missing) by.appendChild(el('span', 'badge err', 'Not reported now'));
      tr.appendChild(by);
      var nt = el('td'), inp = el('input', 'nmnote');
      inp.value = c.note || ''; inp.placeholder = 'Add a note'; inp.maxLength = 300; inp.disabled = !canManage;
      inp.setAttribute('aria-label', 'Note for the cable ' + cableText({ a: c.a, b: c.b }));
      inp.addEventListener('change', function () {
        cableErr('');
        send('POST', '/api/admin/network/cables/' + c.id + '/note', { text: inp.value.trim() })
          .then(function (r) { data.cables = r.cables; paintCables(); }).catch(function (e) { cableErr(e.message); });
      });
      nt.appendChild(inp); tr.appendChild(nt);
      var act = el('td');
      if (canManage && !c.reported) {
        var rm = el('button', '', 'Remove'); rm.type = 'button';
        rm.addEventListener('click', function () {
          cableErr('');
          send('DELETE', '/api/admin/network/cables/' + c.id).then(function (r) { data.cables = r.cables; paintCables(); paintMap(); })
            .catch(function (e) { cableErr(e.message); });
        });
        act.appendChild(rm);
      }
      tr.appendChild(act);
      body.appendChild(tr);
    });
    if (!body.children.length) { var r = el('tr'), c0 = el('td', 'muted', 'No cables recorded yet.'); c0.colSpan = 5; r.appendChild(c0); body.appendChild(r); }
  }
  function paintPicks() {
    // Both ends choose with the shared port picker; once one end is set the other offers only ports of its kind (#546).
    var a = $('nmCableA'), b = $('nmCableB');
    a.textContent = ''; b.textContent = '';
    var off = !canManage;
    cableB = relayUI.portPicker(b, { label: 'The other end of the cable', disabled: off });
    cableA = relayUI.portPicker(a, { label: 'One end of the cable', disabled: off, onChange: function (ref, item) { cableB.lock(item ? item.kind : null); } });
  }
  function loadPicks() {
    send('GET', '/api/admin/network/cables').then(function (r) { data.cables = r.cables; paintPicks(); paintCables(); paintMap(); })
      .catch(function () { /* the list on the page still shows what the last read had */ });
  }

  function showErr(t) { $('nmErr').textContent = t || ''; $('nmErr').hidden = !t; }

  /* ---------------------------------------------------------------- Find and add */
  function paintSources() {
    var box = $('nmSources');
    box.textContent = '';
    data.sources.slice().sort(function (a, b) {        // built in first, then modules that are on, then the rest
      return (a.kind === 'core' ? 0 : a.on ? 1 : 2) - (b.kind === 'core' ? 0 : b.on ? 1 : 2);
    }).forEach(function (s) {
      var row = el('div', 'nmsrc');
      row.appendChild(el('i', 'dot ' + (!s.on ? '' : s.error ? 'warn' : 'ok')));
      var mid = el('div', '');
      mid.appendChild(el('b', '', s.label));
      mid.appendChild(el('small', '', s.adds || ''));
      row.appendChild(mid);
      row.appendChild(el('span', 'muted', !s.on ? 'Off' : s.error ? (s.stale != null ? 'Older read, ' + age(s.stale) : 'Not answering')
        : s.count + (s.count === 1 ? ' device' : ' devices') + ' · ' + age(Math.max(0, Date.now() / 1000 - s.at))));
      if (s.kind === 'core') row.appendChild(el('span', 'muted', 'Built in'));
      else { var a = el('a', '', s.on ? 'Module settings' : 'Turn on in Modules'); a.href = '#modules'; row.appendChild(a); }
      box.appendChild(row);
    });
  }

  function editPlaces() {
    if (places) return places;
    var cl = closets(data.devices, data.places);
    places = (data.places || []).map(function (p) { return { name: p.name, gear: [] }; });
    cl.forEach(function (c) {
      var into = c.unplaced || !data.places.length ? null : places.filter(function (p) { return p.name === c.name; })[0];
      c.gear.forEach(function (g) { if (into) into.gear.push(g.id); });
    });
    return places;
  }

  function paintPlaces() {
    var body = $('nmPlaceRows');
    body.textContent = '';
    var gear = data.devices.filter(isInfra);
    if (!gear.length) { var r0 = el('tr'), c0 = el('td', 'muted', 'No switches found yet.'); c0.colSpan = 5; r0.appendChild(c0); body.appendChild(r0); return; }
    var ps = places || (data.places || []);
    var order = [];
    ps.forEach(function (p) { p.gear.forEach(function (id) { var g = gear.filter(function (x) { return x.id === id; })[0]; if (g && order.indexOf(g) === -1) order.push(g); }); });
    closets(data.devices, []).forEach(function (c) { c.gear.forEach(function (g) { if (order.indexOf(g) === -1) order.push(g); }); });
    order.forEach(function (g, i) {
      var tr = el('tr');
      var o = el('td', 'nmorder');
      [['↑', 'Move up', -1], ['↓', 'Move down', 1]].forEach(function (m) {
        var b = el('button', 'sm quiet', m[0]); b.type = 'button'; b.setAttribute('aria-label', m[1] + ' ' + g.name); b.disabled = !canManage;
        b.addEventListener('click', function () { move(g.id, m[2]); });
        o.appendChild(b);
      });
      tr.appendChild(o);
      tr.appendChild(el('td', '', el('b', '', g.name)));
      tr.appendChild(el('td', 'muted', g.model || what(g)));
      tr.appendChild(el('td', 'mono', (g.ips || [])[0] || ''));
      var s = el('select'); s.setAttribute('aria-label', 'Where ' + g.name + ' is'); s.disabled = !canManage;
      s.appendChild(el('option', { value: '', text: 'Not placed' }));
      ps.forEach(function (p) { s.appendChild(el('option', { value: p.name, text: p.name })); });
      var inPlace = ps.filter(function (p) { return p.gear.indexOf(g.id) !== -1; })[0];
      s.value = inPlace ? inPlace.name : '';
      s.addEventListener('change', function () {
        var ed = editPlaces();
        ed.forEach(function (p) { p.gear = p.gear.filter(function (x) { return x !== g.id; }); });
        var to = ed.filter(function (p) { return p.name === s.value; })[0];
        if (to) to.gear.push(g.id);
        paintPlaces();
      });
      tr.appendChild(el('td', '', s));
      body.appendChild(tr);
    });
  }

  function move(id, by) {
    var ed = editPlaces();
    var p = ed.filter(function (x) { return x.gear.indexOf(id) !== -1; })[0];
    if (!p) return;
    var i = p.gear.indexOf(id), j = i + by;
    if (j < 0 || j >= p.gear.length) return;
    p.gear.splice(i, 1); p.gear.splice(j, 0, id);
    paintPlaces();
  }

  /* ---------------------------------------------------------------- tabs and start */
  function choose(name) {
    var btn = $('nmTabs').querySelector('[data-tab="' + name + '"]');
    if (!btn) { btn = $('nmTabs').querySelector('[data-tab]'); name = btn.dataset.tab; }
    tab = name;
    $('nmTabs').querySelectorAll('[data-tab]').forEach(function (b) { b.setAttribute('aria-selected', String(b === btn)); });
    $('page-network').querySelectorAll('.nmpane').forEach(function (p) { p.classList.toggle('on', p.dataset.pane === name); });
    if (data && name === 'map') paintMap();
  }

  function segment(boxId, current, set) {
    Array.prototype.forEach.call($(boxId).querySelectorAll('button'), function (b) {
      b.setAttribute('aria-pressed', String(b.dataset.v === current));
      b.onclick = function () { set(b.dataset.v); segment(boxId, b.dataset.v, set); };
    });
  }

  function start() {
    $('nmTabs').addEventListener('click', function (e) {
      var b = e.target.closest('[data-tab]');
      if (b) { choose(b.dataset.tab); root.history.replaceState(null, '', '#network/' + b.dataset.tab); }
    });
    $('page-network').addEventListener('relay:tab', function (e) { choose(e.detail || tab); shown(); });
    segment('nmHl', hl, function (v) { hl = v; if (data) paintRacks(); });
    segment('nmFilter', filter, function (v) { filter = v; if (data) paintMap(); });
    segment('nmPortFilter', pfilter, function (v) { pfilter = v; if (data) paintRows(); });
    $('nmSearch').addEventListener('input', function () { query = $('nmSearch').value; if (data) paintRows(); });
    $('nmSwitch').addEventListener('change', function () { if (data) paintRows(); });
    $('nmRefresh').addEventListener('click', function () { load(true); });
    tips($('nmClosets'));
    $('nmSaveNotes').disabled = !canManage;
    $('nmSaveNotes').addEventListener('click', function () {
      showErr('');
      var body = { notes: {} };
      Object.keys(notesDirty).forEach(function (k) { body.notes[k] = notesDirty[k].trim(); });
      if (!Object.keys(body.notes).length) return;
      send('POST', '/api/admin/network/map/labels', body).then(function (st) { data.notes = st.notes; notesDirty = {}; paintRows(); })
        .catch(function (e) { showErr(e.message); });
    });
    ['nmCableNote', 'nmAddCable'].forEach(function (id) { $(id).disabled = !canManage; });
    $('nmAddCable').addEventListener('click', function () {
      cableErr('');
      var a = cableA && cableA.value(), b = cableB && cableB.value();
      if (!a || !b) { cableErr('Pick a port for each end of the cable.'); return; }
      send('POST', '/api/admin/network/cables', { a: a, b: b, note: $('nmCableNote').value.trim() })
        .then(function (r) { data.cables = r.cables; $('nmCableNote').value = ''; paintPicks(); paintCables(); paintMap(); })
        .catch(function (e) { cableErr(e.message); });
    });
    $('nmAddPlace').disabled = $('nmSavePlaces').disabled = $('nmNewPlace').disabled = !canManage;
    $('nmAddPlace').addEventListener('click', function () {
      var n = $('nmNewPlace').value.trim().replace(/\s+/g, ' ');
      if (!n || !data) return;
      var ed = editPlaces();
      if (!ed.some(function (p) { return p.name.toLowerCase() === n.toLowerCase(); })) ed.push({ name: n, gear: [] });
      $('nmNewPlace').value = '';
      paintPlaces();
    });
    $('nmSavePlaces').addEventListener('click', function () {
      var ed = editPlaces().filter(function (p) { return p.gear.length; });
      send('POST', '/api/admin/network/map/places', { places: ed }).then(function (st) { data.places = st.places; places = null; paintPlaces(); paintRacks(); })
        .catch(function (e) { $('nmStatus').textContent = e.message; });
    });
    function shown() {
      root.clearInterval(timer);
      if (!$('page-network').hidden) { load(false); timer = root.setInterval(function () { if (!doc.hidden && !$('page-network').hidden) load(false); }, 30000); }
    }
    root.addEventListener('hashchange', shown);
    var h0 = root.location.hash.slice(1).split('/');      // opened at #network/map, or at an old page's hash
    var first = Array.prototype.filter.call($('nmTabs').querySelectorAll('[data-tab]'), function (b) {
      return h0[0] === 'network' ? b.dataset.tab === h0[1] : (b.dataset.tab === h0[0] || b.dataset.alias === h0[0]);
    })[0];
    if (first) choose(first.dataset.tab);
    shown();
  }
  function clip(s, n) { s = String(s || ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; }
  start();
})(typeof window !== 'undefined' ? window : globalThis);
