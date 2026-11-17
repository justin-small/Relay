/* The Network map page (#437). It asks /api/admin/network/map (app/network_map.py), which merges Relay's own ports,
   mDNS discovery of NDI and Dante, and every module that adds to the map, and draws the result:

     with a switch source: gateway, then switches and access points, with their clients underneath;
     without one:          Relay, then its network ports, then the devices found on each port.

   Everything it shows comes from the network, so it is set as text (textContent) and drawn as SVG from CSS classes
   that read the panel's tokens; nothing is markup. The pure parts (filter, layout) are tested in
   tests/js/network_map.test.mjs. */
(function (root) {
  'use strict';

  var NODE_W = 156, NODE_H = 40, GAP_X = 14, GAP_Y = 46, COLS = 3;

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
    function subOf(d) { return d.ips && d.ips.length ? d.ips[0] : (d.mac || ''); }
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
        add(d.id, left + (w - NODE_W) / 2, top, d.name, subOf(d), d, isInfra(d) ? 'infra' : '');
        var x = left, row = top + NODE_H + GAP_Y;
        infra.forEach(function (ch) { place(ch, x, row); link(d.id, ch.id, ch); x += width(ch) + GAP_X; });
        cl.forEach(function (ch, i) {
          var col = i % COLS, rr = Math.floor(i / COLS);
          add(ch.id, x + col * (NODE_W + GAP_X), row + rr * (NODE_H + 10), ch.name, subOf(ch), ch, '');
          link(d.id, ch.id, ch);
        });
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

  var api = { matches: matches, visibleSet: visibleSet, layout: layout, age: age };
  if (typeof module !== 'undefined' && module.exports) { module.exports = api; return; }
  if (!root.document || !root.document.getElementById('page-network_map')) return;

  /* ---- the page ---- */
  var doc = root.document;
  function $(id) { return doc.getElementById(id); }
  function el(tag, cls, text) { var e = doc.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  var SVG = 'http://www.w3.org/2000/svg';
  function sv(tag, attrs, text) {
    var e = doc.createElementNS(SVG, tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text != null) e.textContent = text;
    return e;
  }

  var data = null, view = 'map', filter = 'all', query = '', selected = '', timer = 0, loading = false;

  function load(force) {
    if (loading) return;
    loading = true;
    $('nmRefresh').disabled = true;
    root.fetch('/api/admin/network/map' + (force ? '?refresh=1' : ''), { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error('refused'); return r.json(); })
      .then(function (d) { data = d; paint(); })
      .catch(function () { $('nmStatus').textContent = 'The map could not be read. Try Refresh.'; })
      .then(function () { loading = false; $('nmRefresh').disabled = false; });
  }

  function paint() {
    if (!data) return;
    var sources = data.sources.filter(function (s) { return s.on; });
    var failed = sources.filter(function (s) { return s.error; });
    var st = $('nmStatus');
    st.textContent = '';
    st.appendChild(el('span', '', data.devices.length + (data.devices.length === 1 ? ' device' : ' devices') + ' from '
      + sources.length + (sources.length === 1 ? ' source' : ' sources') + ' · updated ' + age(Math.max(0, Date.now() / 1000 - data.generated_at))));
    if (failed.length) st.appendChild(el('span', 'badge warn', 'Partial'));
    stats();
    paintMap();
    paintTable();
    paintSources();
    paintDetail();
  }

  function count(f) { return data.devices.filter(f).length; }
  function stats() {
    var infra = count(isInfra);
    var vals = [['Switches and APs', data.has_switch_source ? String(infra) : '—', data.has_switch_source ? '' : 'Needs a module that reads switches'],
                ['Devices found', String(data.devices.length), ''],
                ['NDI sources', String(count(function (d) { return d.ndi; })), ''],
                ['Dante devices', String(count(function (d) { return d.dante; })), '']];
    var box = $('nmStats');
    box.textContent = '';
    vals.forEach(function (v) {
      var c = el('div', 'card stat');
      c.appendChild(el('div', 'k', v[0])); c.appendChild(el('div', 'v', v[1])); c.appendChild(el('div', 's', v[2]));
      box.appendChild(c);
    });
  }

  function paintMap() {
    var frame = $('nmMap');
    frame.hidden = view !== 'map';
    if (view !== 'map') return;
    frame.textContent = '';
    var lay = layout(data, filter, query);
    if (!lay.nodes.length) { frame.appendChild(el('p', 'note', 'No devices match.')); return; }
    var svg = sv('svg', { viewBox: '0 0 ' + lay.width + ' ' + lay.height, width: lay.width, height: lay.height, role: 'img',
                          'aria-label': 'Network map: ' + lay.nodes.length + ' items' });
    var at = {};
    lay.nodes.forEach(function (n) { at[n.id] = n; });
    lay.links.forEach(function (l) {
      var a = at[l.from], b = at[l.to];
      if (!a || !b) return;
      var x1 = a.x + a.w / 2, y1 = a.y + a.h, x2 = b.x + b.w / 2, y2 = b.y, ym = (y1 + y2) / 2;
      svg.appendChild(sv('path', { d: 'M' + x1 + ' ' + y1 + 'C' + x1 + ' ' + ym + ' ' + x2 + ' ' + ym + ' ' + x2 + ' ' + y2,
                                   'class': 'nm-link' + (l.ndi ? ' ndi' : '') + (l.dante ? ' dante' : '') }));
    });
    lay.nodes.forEach(function (n) {
      var d = n.ref, cls = 'nm-node ' + n.cls + (d && d.ndi ? ' ndi' : '') + (d && d.dante ? ' dante' : '') +
        (d && d.stale_by && d.stale_by.length ? ' stale' : '') + (d && d.id === selected ? ' sel' : '');
      var g = sv('g', { 'class': cls, tabindex: d ? '0' : '-1', role: d ? 'button' : 'presentation',
                        transform: 'translate(' + n.x + ' ' + n.y + ')' });
      if (d) {
        g.setAttribute('aria-label', n.label + (n.sub ? ', ' + n.sub : ''));
        g.addEventListener('click', function () { select(d.id); });
        g.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(d.id); } });
      }
      g.appendChild(sv('rect', { width: n.w, height: n.h, rx: 6 }));
      g.appendChild(sv('text', { x: 8, y: 16, 'class': 'nm-name' }, clip(n.label, 22)));
      g.appendChild(sv('text', { x: 8, y: 31, 'class': 'nm-sub' }, clip(n.sub, 26)));
      svg.appendChild(g);
    });
    frame.appendChild(svg);
  }

  function clip(s, n) { s = String(s || ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; }

  function badgeFor(d) {
    var out = [];
    if (d.ndi) out.push('NDI');
    if (d.dante) out.push('Dante');
    return out.join(' + ');
  }

  function paintTable() {
    var box = $('nmTable');
    box.hidden = view !== 'table';
    if (view !== 'table') return;
    box.textContent = '';
    var t = el('table', 'dt'), head = el('tr');
    ['Name', 'Address', 'MAC', 'AV', 'Where', 'Seen by'].forEach(function (h) { head.appendChild(el('th', '', h)); });
    t.appendChild(el('thead')).appendChild(head);
    var body = el('tbody');
    data.devices.filter(function (d) { return d.kind !== 'relay' && matches(d, filter, query); }).forEach(function (d) {
      var tr = el('tr');
      tr.tabIndex = 0;
      tr.setAttribute('role', 'button');
      tr.appendChild(el('td', '', d.name));
      tr.appendChild(el('td', 'mono', (d.ips || []).join(', ')));
      tr.appendChild(el('td', 'mono', d.mac || ''));
      tr.appendChild(el('td', '', badgeFor(d)));
      tr.appendChild(el('td', '', where(d)));
      tr.appendChild(el('td', 'muted', (d.seen_by || []).join(', ')));
      tr.addEventListener('click', function () { select(d.id); });
      tr.addEventListener('keydown', function (e) { if (e.key === 'Enter') select(d.id); });
      body.appendChild(tr);
    });
    if (!body.children.length) { var r = el('tr'), c = el('td', 'muted', 'No devices match.'); c.colSpan = 6; r.appendChild(c); body.appendChild(r); }
    t.appendChild(body);
    box.appendChild(t);
  }

  function byId(id) { return data.devices.filter(function (d) { return d.id === id; })[0]; }
  function where(d) {
    var up = d.parent ? byId(d.parent) : null;
    if (up) return up.name + (d.uplink_port != null ? ' port ' + d.uplink_port : '') + (d.port_name ? ' (' + d.port_name + ')' : '');
    return d.via ? (data.relay_ports.filter(function (p) { return p.id === d.via; })[0] || {}).friendly_name || '' : '';
  }

  function select(id) { selected = id; paintMap(); paintDetail(); }

  function kv(box, k, v) {
    if (v == null || v === '') return;
    var row = el('div', 'kv'); row.appendChild(el('span', 'muted', k)); row.appendChild(el('span', '', v)); box.appendChild(row);
  }

  function paintDetail() {
    var box = $('nmDetail'), ports = $('nmPorts');
    box.textContent = ''; ports.textContent = '';
    var d = selected ? byId(selected) : null;
    if (!d) {
      box.appendChild(el('p', 'note', 'Select a device on the map or in the table to see what Relay knows about it.'));
      ports.appendChild(el('p', 'note', data.has_switch_source ? 'Select a switch to see its ports.'
        : 'Port tables come from a module that reads switches, such as UniFi. Turn one on in Modules.'));
      return;
    }
    box.appendChild(el('h4', '', d.name));
    kv(box, 'Address', (d.ips || []).join(', '));
    kv(box, 'MAC', d.mac);
    kv(box, 'Host name', d.host);
    kv(box, 'Carries', badgeFor(d) || 'Nothing known');
    kv(box, 'Where', where(d));
    if (d.speed) kv(box, 'Link speed', d.speed + ' Mbps');
    if (d.poe_w) kv(box, 'PoE', d.poe_w + ' W');
    if (d.errors) kv(box, 'Port errors', String(d.errors));
    kv(box, 'Seen by', (d.seen_by || []).map(function (s) { return label(s); }).join(', '));
    kv(box, 'Identified by', (d.identified_by || []).join(', ') || 'Not identified as NDI or Dante');
    kv(box, 'Also', (d.tags || []).join(', '));
    (d.notes || []).forEach(function (n) { box.appendChild(el('p', 'note warn', n)); });
    if (d.stale_by && d.stale_by.length) box.appendChild(el('p', 'note', 'Part of this comes from an older read: ' + d.stale_by.map(label).join(', ') + '.'));
    if (!(d.ports && d.ports.length)) { ports.appendChild(el('p', 'note', 'This device has no port table.')); return; }
    var t = el('table', 'dt'), head = el('tr');
    ['Port', 'Name', 'Link', 'PoE', 'Errors', 'Device'].forEach(function (h) { head.appendChild(el('th', '', h)); });
    t.appendChild(el('thead')).appendChild(head);
    var body = el('tbody');
    d.ports.forEach(function (p) {
      var tr = el('tr');
      tr.appendChild(el('td', '', String(p.port)));
      tr.appendChild(el('td', '', p.name));
      tr.appendChild(el('td', p.warn ? 'warn' : '', p.up ? (p.speed ? p.speed + ' Mbps' : 'Up') : 'Down'));
      tr.appendChild(el('td', '', p.poe_w ? p.poe_w + ' W' : ''));
      tr.appendChild(el('td', p.errors ? 'err' : '', p.errors ? String(p.errors) : ''));
      tr.appendChild(el('td', '', p.client));
      body.appendChild(tr);
    });
    t.appendChild(body);
    var wrap = el('div', 'tablescroll'); wrap.appendChild(t); ports.appendChild(wrap);
  }

  function label(id) {
    var s = data.sources.filter(function (x) { return x.id === id; })[0];
    return s ? s.label : id;
  }

  function paintSources() {
    var box = $('nmSources');
    box.textContent = '';
    data.sources.forEach(function (s) {
      var row = el('div', 'kv src');
      var left = el('span');
      left.appendChild(el('strong', '', s.label));
      left.appendChild(el('span', 'muted', ' · ' + (s.kind === 'core' ? 'Core, always on' : 'Module')));
      left.appendChild(el('div', 'note', s.adds || ''));
      var right = el('span', 'srcstate');
      if (!s.on) right.appendChild(el('span', 'badge', 'Off'));
      else if (s.error) right.appendChild(el('span', 'badge warn', s.stale != null ? 'Older read, ' + age(s.stale) : 'Not answering'));
      else right.appendChild(el('span', 'badge ok', s.count + (s.count === 1 ? ' device' : ' devices')));
      row.appendChild(left); row.appendChild(right); box.appendChild(row);
    });
  }

  function segment(boxId, current, set) {
    Array.prototype.forEach.call($(boxId).querySelectorAll('button'), function (b) {
      b.classList.toggle('on', b.dataset.v === current);
      b.setAttribute('aria-pressed', String(b.dataset.v === current));
      b.onclick = function () { set(b.dataset.v); segment(boxId, b.dataset.v, set); };
    });
  }

  function start() {
    segment('nmTabs', view, function (v) { view = v; paintMap(); paintTable(); });
    segment('nmFilter', filter, function (v) { filter = v; if (data) { paintMap(); paintTable(); } });
    $('nmSearch').addEventListener('input', function () { query = $('nmSearch').value; if (data) { paintMap(); paintTable(); } });
    $('nmRefresh').addEventListener('click', function () { load(true); });
    function shown() {
      root.clearInterval(timer);
      if (root.location.hash === '#network_map') { load(false); timer = root.setInterval(function () { if (!doc.hidden) load(false); }, 30000); }
    }
    root.addEventListener('hashchange', shown);
    shown();
  }
  start();
})(typeof window !== 'undefined' ? window : globalThis);
