/* Network mockups: one made-up church network, and the drawing helpers the three options share.
   Shape follows /api/admin/network/map (app/network_map.py): devices with kind, ips, mac, ndi, dante,
   uplink_mac/uplink_port, speed, poe_w, errors, identified_by; switches with ports [{port, up, speed, poe_w,
   errors, client, warn, uplink}]; sources with label, adds, count, stale. */
(function () {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';
  function h(tag, cls, parent, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; if (parent) parent.appendChild(e); return e; }
  function s(tag, attrs, parent, text) { var e = document.createElementNS(NS, tag); for (var k in attrs) e.setAttribute(k, attrs[k]); if (text != null) e.textContent = text; if (parent) parent.appendChild(e); return e; }

  /* ---- the gear ---- */
  var GEAR = [
    { id: 'gw', name: 'Gateway', model: 'UniFi Dream Machine Pro', kind: 'gateway', ip: '10.10.1.1', mac: '74:ac:b9:01:00:01', place: 'Booth rack', ports: 8, sfp: 2, wan: 'Internet 940 / 41 Mbps' },
    { id: 'sw1', name: 'Booth switch', model: 'USW-Pro-24-PoE', kind: 'switch', ip: '10.10.1.2', mac: '74:ac:b9:01:00:02', place: 'Booth rack', ports: 24, sfp: 2, poe_budget: 400, poe_used: 142, up: 'gw', up_port: 9 },
    { id: 'sw2', name: 'Stage switch', model: 'USW-Lite-16-PoE', kind: 'switch', ip: '10.10.1.3', mac: '74:ac:b9:01:00:03', place: 'Stage left closet', ports: 16, sfp: 0, poe_budget: 45, poe_used: 41, up: 'sw1', up_port: 24 },
    { id: 'sw3', name: 'Lobby switch', model: 'USW-Flex-Mini', kind: 'switch', ip: '10.10.1.4', mac: '74:ac:b9:01:00:04', place: 'Lobby cabinet', ports: 5, sfp: 0, up: 'sw1', up_port: 23 },
    { id: 'ap1', name: 'Sanctuary AP', model: 'U6-Pro', kind: 'ap', ip: '10.10.1.5', mac: '74:ac:b9:01:00:05', place: 'Sanctuary ceiling', up: 'sw1', up_port: 20, clients: 38 },
    { id: 'ap2', name: 'Lobby AP', model: 'U6-Lite', kind: 'ap', ip: '10.10.1.6', mac: '74:ac:b9:01:00:06', place: 'Lobby ceiling', up: 'sw3', up_port: 3, clients: 11 }
  ];
  /* [id, name, what, switch, port, speed Mb, poe W, ndi, dante, ip, extra] */
  var C = [
    ['relay', 'This Relay computer', 'Mac mini · Relay', 'sw1', 1, 1000, 0, 0, 0, '10.10.1.20', { tags: ['Relay'] }],
    ['pp', 'ProPresenter', 'Mac Studio', 'sw1', 2, 1000, 0, 1, 0, '10.10.1.21', { tags: ['ProPresenter', 'NDI out'] }],
    ['obs', 'Stream PC', 'Windows · OBS Studio', 'sw1', 3, 1000, 0, 1, 0, '10.10.1.22', { tags: ['OBS', 'NDI in'] }],
    ['atem', 'ATEM Mini Extreme ISO', 'Blackmagic switcher', 'sw1', 4, 1000, 0, 0, 0, '10.10.1.30', { tags: ['Blackmagic'] }],
    ['deck', 'HyperDeck Studio HD Plus', 'Blackmagic recorder', 'sw1', 5, 1000, 0, 0, 0, '10.10.1.31', { tags: ['Blackmagic'] }],
    ['hub', 'Videohub 20x20', 'Blackmagic router', 'sw1', 6, 100, 0, 0, 0, '10.10.1.32', { tags: ['Blackmagic'] }],
    ['dm7', 'Yamaha DM7', 'Mixing console · Dante primary', 'sw1', 7, 1000, 0, 0, 1, '10.10.2.10', { tags: ['Dante', '64 ch'] }],
    ['reaper', 'Recording PC', 'Windows · REAPER · DVS', 'sw1', 8, 1000, 0, 0, 1, '10.10.2.11', { tags: ['REAPER', 'Dante VS'] }],
    ['cap1', 'Booth caption panel', 'Relay Display 7"', 'sw1', 9, 100, 3.1, 0, 0, '10.10.1.60', { tags: ['Panel'] }],
    ['light', 'Lighting node', 'ENTTEC ODE Mk3 · sACN', 'sw1', 10, 100, 4.2, 0, 0, '10.10.1.70', { tags: ['Lighting'] }],
    ['tally', 'Tally Arbiter', 'Raspberry Pi 4', 'sw1', 11, 1000, 0, 0, 0, '10.10.1.71', { tags: ['Tally'] }],
    ['boothtv', 'Booth multiview TV', 'BirdDog Play · NDI decoder', 'sw1', 12, 1000, 7.8, 1, 0, '10.10.1.50', { tags: ['NDI in'] }],
    ['rio', 'Yamaha Rio3224-D2', 'Stage box · Dante', 'sw2', 2, 1000, 0, 0, 1, '10.10.2.20', { tags: ['Dante', '32 in'] }],
    ['cam1', 'Center PTZ', 'BirdDog P200 · NDI', 'sw2', 3, 1000, 18.4, 1, 0, '10.10.1.40', { tags: ['NDI', 'Camera 1'] }],
    ['cam2', 'Stage Left', 'PTZOptics 30X · NDI HX', 'sw2', 4, 1000, 12.9, 1, 0, '10.10.1.41', { tags: ['NDI', 'Camera 2'] }],
    ['cam3', 'Wide Shot', 'PTZOptics 20X · NDI HX', 'sw2', 5, 100, 9.6, 1, 0, '10.10.1.42', { tags: ['NDI', 'Camera 3'], warn: 'Linked at 100 Mb/s: the cable or jack is likely damaged. NDI from this camera may stutter.', errors: 214 }],
    ['ulxd', 'Shure ULX-D receiver', 'ULXD4Q · Dante', 'sw2', 6, 1000, 0, 0, 1, '10.10.2.21', { tags: ['Dante', '4 mics'] }],
    ['cap2', 'Stage caption panel', 'Relay Display 10"', 'sw2', 7, 100, 3.4, 0, 0, '10.10.1.61', { tags: ['Panel'] }],
    ['conf', 'Confidence monitor', 'BirdDog Play · NDI decoder', 'sw2', 8, 1000, 0, 1, 0, '10.10.1.51', { tags: ['NDI in'] }],
    ['lobbytv', 'Lobby TV', 'Signage player', 'sw3', 2, 1000, 0, 0, 0, '10.10.1.80', { tags: ['Signage'] }],
    ['cap3', 'Lobby caption panel', 'Relay Display 7"', 'sw3', 4, 100, 0, 0, 0, '10.10.1.62', { tags: ['Panel'] }],
    ['cam4', 'Balcony Cam', 'PTZOptics 20X · NDI HX', '', 0, 0, 0, 1, 0, '10.10.1.43', { tags: ['NDI', 'Camera 4'], off: 'Last seen Sunday 11:52' }]
  ];
  var DEV = C.map(function (r, i) {
    var x = r[10] || {};
    return { id: r[0], name: r[1], what: r[2], sw: r[3], port: r[4], speed: r[5], poe: r[6], ndi: !!r[7], dante: !!r[8], ip: r[9],
      mac: '00:1c:' + (16 + i).toString(16) + ':4e:' + (40 + i).toString(16) + ':0' + (i % 9), tags: x.tags || [], warn: x.warn || '',
      errors: x.errors || 0, off: x.off || '', kind: 'client',
      seen: x.off ? ['Discovery (mDNS)'] : (r[7] || r[8] ? ['UniFi', 'Discovery (mDNS)'] : ['UniFi']) };
  });
  DEV.forEach(function (d) { if (d.id === 'atem' || d.id === 'deck' || d.id === 'hub') d.seen.push('Blackmagic'); if (d.id === 'relay') d.seen = ['Relay', 'UniFi']; });
  var NOTES = { 'sw1:7': 'Blue cable, labelled DANTE PRI. Do not unplug during service.', 'sw2:5': 'Jack in the wall plate behind the drum riser.', 'sw1:24': 'Runs to stage left closet' };
  var SOURCES = [
    { id: 'relay', label: 'Relay', kind: 'core', adds: 'This computer and its network ports.', count: 1, age: 'now' },
    { id: 'discovery', label: 'Discovery (mDNS)', kind: 'core', adds: 'NDI and Dante devices that announce themselves on Relay’s networks.', count: 14, age: '8 s ago' },
    { id: 'unifi', label: 'UniFi', kind: 'module', adds: 'Switches, access points and the gateway, which port each device is on, link speed, PoE and errors.', count: 27, age: '8 s ago' },
    { id: 'blackmagic', label: 'Blackmagic', kind: 'module', adds: 'ATEM, HyperDeck and Videohub units it controls.', count: 3, age: '8 s ago' },
    { id: 'ndi', label: 'NDI', kind: 'module', adds: 'Which NDI sources each receiver is watching.', count: 0, off: true }
  ];
  var PORTS = [
    { id: 'en0', name: 'Ethernet', addr: '10.10.1.20/24', link: 'Up', speed: '1 Gb/s', traffic: ['Caption and translation pages', 'Operator panel, sign-in and crew pages', 'NDI discovery and video', 'ProPresenter', 'OBS Studio', 'UniFi controller'] },
    { id: 'en7', name: 'USB 10/100/1000 LAN', addr: '10.10.2.20/24', link: 'Up', speed: '1 Gb/s', traffic: ['Dante control and discovery', 'REAPER (OSC)'] },
    { id: 'en1', name: 'Wi-Fi', addr: '192.168.8.41/24', link: 'Up', speed: '—', traffic: ['Translation service (over the internet)', 'Planning Center (over the internet)', 'YouTube'] }
  ];
  var TRAFFIC = [
    ['Caption and translation pages (plain HTTP)', 'Relay', 'en0', 'fallback'],
    ['Operator panel, sign-in and crew pages (HTTPS)', 'Relay', 'en0', 'fallback'],
    ['Relay’s announcement to caption panels (mDNS)', 'Relay', 'en0', 'fallback'],
    ['Translation service (OpenAI, over the internet)', 'Relay', 'en1', 'fallback'],
    ['NDI discovery (multicast DNS) and video', 'NDI', 'en0', 'fallback'],
    ['Dante control and discovery', 'Audio', 'en7', 'strict'],
    ['ProPresenter (its network API)', 'ProPresenter', 'en0', 'fallback'],
    ['UniFi controller', 'UniFi', '', 'fallback'],
    ['Planning Center (over the internet)', 'Planning Center', 'en1', 'fallback']
  ];
  /* NDI and Dante flows: who sends to whom (from the NDI module's receivers and Dante subscriptions). */
  var FLOWS = [
    ['ndi', 'cam1', 'obs', 132], ['ndi', 'cam2', 'obs', 96], ['ndi', 'cam3', 'obs', 41], ['ndi', 'pp', 'obs', 88], ['ndi', 'pp', 'conf', 88], ['ndi', 'obs', 'boothtv', 120],
    ['dante', 'rio', 'dm7', 32], ['dante', 'ulxd', 'dm7', 4], ['dante', 'dm7', 'reaper', 64], ['dante', 'dm7', 'rio', 16]
  ];

  function gear(id) { for (var i = 0; i < GEAR.length; i++) if (GEAR[i].id === id) return GEAR[i]; return null; }
  function dev(id) { for (var i = 0; i < DEV.length; i++) if (DEV[i].id === id) return DEV[i]; return gear(id); }
  function onPort(sw, p) {
    for (var i = 0; i < DEV.length; i++) if (DEV[i].sw === sw && DEV[i].port === p) return DEV[i];
    for (var j = 0; j < GEAR.length; j++) if (GEAR[j].up === sw && GEAR[j].up_port === p) return GEAR[j];
    var g = gear(sw); if (g && g.up) { var parent = gear(g.up); if (sw === 'sw1' && p === 26) return parent; if (sw !== 'sw1' && p === g.ports) return parent; }
    return null;
  }
  function portInfo(sw, p) {
    var d = onPort(sw, p), g = gear(sw);
    var uplink = !!(d && d.kind && d.kind !== 'client' && (d.id === g.up));
    return { sw: sw, port: p, dev: d, up: !!d && !d.off, speed: d ? (d.speed || 1000) : 0, poe: d ? (d.poe || (d.kind === 'ap' ? 9.8 : 0)) : 0,
      ndi: !!(d && d.ndi), dante: !!(d && d.dante), warn: d && d.warn, errors: d ? d.errors || 0 : 0, uplink: uplink, note: NOTES[sw + ':' + p] || '' };
  }

  /* ---- a switch's front panel ---- */
  function drawSwitch(g, opts) {
    opts = opts || {};
    var n = g.ports || 0, rows = n > 8 ? 2 : 1, cols = Math.ceil(n / rows), pw = 30, ph = 24, gx = 6;
    var left = 150, W = Math.max(520, left + cols * (pw + gx) + (g.sfp ? g.sfp * 44 + 30 : 0) + 40), H = rows === 2 ? 96 : 84;
    var svg = s('svg', { class: 'hw', viewBox: '0 0 ' + W + ' ' + H, role: 'group', 'aria-label': g.name + ' front', style: 'max-width:' + Math.round(W * 1.15) + 'px' });
    s('rect', { class: 'hw-ear', x: 0, y: 2, width: 18, height: H - 4, rx: 3 }, svg); s('rect', { class: 'hw-ear', x: W - 18, y: 2, width: 18, height: H - 4, rx: 3 }, svg);
    [9, W - 9].forEach(function (x) { s('circle', { class: 'hw-screw', cx: x, cy: 14, r: 3.5 }, svg); s('circle', { class: 'hw-screw', cx: x, cy: H - 14, r: 3.5 }, svg); });
    s('rect', { class: 'hw-body', x: 18, y: 2, width: W - 36, height: H - 4, rx: 4 }, svg);
    s('text', { class: 'hw-brand', x: 30, y: 22 }, svg, 'UniFi');
    s('text', { class: 'hw-name', x: 30, y: 40 }, svg, g.name);
    var lcd = s('g', { class: 'hw-lcd' }, svg); s('rect', { x: 30, y: H - 34, width: 104, height: 22, rx: 3 }, lcd);
    s('text', { x: 36, y: H - 19 }, lcd, g.kind === 'gateway' ? 'WAN ↑ 940M' : (g.poe_budget ? 'PoE ' + g.poe_used + '/' + g.poe_budget + 'W' : g.ip));
    var ports = [];
    function port(p, x, y, sfp) {
      var info = portInfo(g.id, p);
      var cls = 'hw-port' + (info.up ? (info.warn ? ' slow' : ' up') : '') + (info.errors > 100 ? ' err' : '') + (info.ndi ? ' ndi' : '') + (info.dante ? ' dante' : '') + (info.poe ? ' poe' : '') + (sfp ? ' sfp' : '');
      if (opts.sel && opts.sel.sw === g.id && opts.sel.port === p) cls += ' sel';
      if (opts.filter && !opts.filter(info)) cls += ' dim';
      var gp = s('g', { class: cls, tabindex: 0, 'data-sw': g.id, 'data-port': p, role: 'button', 'aria-label': 'Port ' + p + (info.dev ? ', ' + info.dev.name : ', empty') }, svg);
      var w = sfp ? 36 : pw;
      s('rect', { class: 'jack', x: x, y: y, width: w, height: ph, rx: 3 }, gp);
      if (!sfp) s('rect', { class: 'tab', x: x + w / 2 - 6, y: y + (y > 40 ? ph - 6 : 2), width: 12, height: 4, rx: 1 }, gp);
      s('circle', { class: 'led', cx: x + 5, cy: y + (y > 40 ? 5 : ph - 5), r: 2.6 }, gp);
      s('circle', { class: 'poe', cx: x + w - 5, cy: y + (y > 40 ? 5 : ph - 5), r: 2.6 }, gp);
      s('text', { class: 'hw-pn', x: x + w / 2, y: y > 40 ? y + ph + 9 : y - 3, 'text-anchor': 'middle' }, gp, sfp ? 'SFP' + (p - n) : p);
      ports.push(gp);
    }
    for (var i = 0; i < n; i++) {
      var r = rows === 2 ? i % 2 : 0, c = rows === 2 ? Math.floor(i / 2) : i;
      port(i + 1, left + c * (pw + gx), rows === 2 ? (r ? 54 : 16) : 30, false);
    }
    for (var k = 0; k < (g.sfp || 0); k++) port(n + k + 1, left + cols * (pw + gx) + 24 + k * 44, rows === 2 ? 35 : 30, true);
    if (g.kind === 'ap') { s('circle', { cx: W / 2, cy: H / 2, r: 20, class: 'hw-body' }, svg); }
    return svg;
  }

  function apCard(g) {
    var svg = s('svg', { class: 'hw', viewBox: '0 0 520 70', style: 'max-width:600px' });
    s('rect', { class: 'hw-body', x: 0, y: 4, width: 520, height: 62, rx: 31 }, svg);
    s('circle', { cx: 40, cy: 35, r: 6, fill: 'var(--info)' }, svg);
    s('text', { class: 'hw-brand', x: 60, y: 30 }, svg, 'UniFi ' + g.model);
    s('text', { class: 'hw-name', x: 60, y: 48 }, svg, g.name + ' · ' + g.clients + ' on Wi-Fi');
    return svg;
  }

  /* ---- hover tip ---- */
  var tip;
  function hoverTips(root, onPick) {
    if (!tip) { tip = h('div', 'hw-tip', document.body); }
    root.addEventListener('mouseover', function (e) {
      var p = e.target.closest && e.target.closest('.hw-port'); if (!p) return;
      var info = portInfo(p.dataset.sw, +p.dataset.port); tip.textContent = '';
      h('b', '', tip, gear(info.sw).name + ' · port ' + info.port);
      var dl = h('dl', '', tip);
      function row(k, v) { h('dt', '', dl, k); h('dd', '', dl, v); }
      if (info.dev) { row('Device', info.dev.name); if (info.dev.what || info.dev.model) row('What', info.dev.what || info.dev.model); row('Address', info.dev.ip || ''); row('Link', info.speed >= 1000 ? info.speed / 1000 + ' Gb/s' : info.speed + ' Mb/s'); if (info.poe) row('PoE', info.poe + ' W'); if (info.errors) row('Errors', info.errors + ' today'); if (info.warn) row('Check', info.warn); }
      else row('Device', 'Nothing plugged in');
      if (info.note) row('Note', info.note);
      tip.style.display = 'block';
    });
    root.addEventListener('mousemove', function (e) { if (tip.style.display === 'block') { tip.style.left = Math.min(window.innerWidth - 290, e.clientX + 14) + 'px'; tip.style.top = (e.clientY + 14) + 'px'; } });
    root.addEventListener('mouseout', function (e) { if (e.target.closest && e.target.closest('.hw-port')) tip.style.display = 'none'; });
    root.addEventListener('click', function (e) { var p = e.target.closest && e.target.closest('.hw-port'); if (p && onPick) onPick(p.dataset.sw, +p.dataset.port); });
    root.addEventListener('keydown', function (e) { var p = e.target.closest && e.target.closest('.hw-port'); if (p && onPick && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onPick(p.dataset.sw, +p.dataset.port); } });
  }

  /* ---- topology map ---- */
  function drawMap(frame, opts) {
    opts = opts || {};
    var NW = 168, NH = 44, GX = 14, GY = 70;
    frame.textContent = '';
    var tree = { gw: ['sw1'], sw1: ['sw2', 'sw3', 'ap1'], sw3: ['ap2'] };
    var levels = [['gw'], ['sw1'], ['sw2', 'ap1', 'sw3', 'ap2']];
    var groups = {};
    DEV.forEach(function (d) { var k = d.sw || 'off'; (groups[k] = groups[k] || []).push(d); });
    var pos = {}, W = 0, y0 = 20;
    /* clients laid out in columns under their switch */
    var colX = 20, blocks = [['sw1', 3], ['sw2', 2], ['sw3', 1], ['off', 1]];
    var clientY = y0 + 3 * (NH + GY);
    blocks.forEach(function (b) {
      var list = (groups[b[0]] || []).filter(function (d) { return d.id !== 'relay' || opts.showRelay !== false; });
      var cols = b[1];
      list.forEach(function (d, i) { pos[d.id] = { x: colX + (i % cols) * (NW + GX), y: clientY + Math.floor(i / cols) * (NH + 14) }; });
      var bw = cols * (NW + GX);
      pos['_' + b[0]] = { x: colX, w: bw - GX };
      colX += bw + 26;
    });
    W = colX;
    function center(id) { var b = pos['_' + id]; return b ? b.x + b.w / 2 - NW / 2 : W / 2 - NW / 2; }
    pos.gw = { x: center('sw1'), y: y0 };
    pos.sw1 = { x: center('sw1'), y: y0 + NH + GY };
    pos.sw2 = { x: center('sw2'), y: y0 + 2 * (NH + GY) };
    pos.sw3 = { x: center('sw3'), y: y0 + 2 * (NH + GY) };
    pos.ap1 = { x: pos.sw1.x + NW + 30, y: y0 + NH + GY + 40 };
    pos.ap2 = { x: center('off'), y: y0 + 2 * (NH + GY) };
    var H = 0; Object.keys(pos).forEach(function (k) { if (pos[k].y != null) H = Math.max(H, pos[k].y + NH + 20); });
    var svg = s('svg', { width: W, height: H, viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': 'Network map' }, frame);
    var lg = s('g', {}, svg), ng = s('g', {}, svg);
    function link(a, b, cls) { var p = pos[a], q = pos[b]; if (!p || !q) return; var x1 = p.x + NW / 2, y1 = p.y + NH, x2 = q.x + NW / 2, y2 = q.y; var my = (y1 + y2) / 2;
      s('path', { class: 'm-link ' + (cls || ''), d: 'M' + x1 + ',' + y1 + ' C' + x1 + ',' + my + ' ' + x2 + ',' + my + ' ' + x2 + ',' + y2 }, lg); }
    Object.keys(tree).forEach(function (p) { tree[p].forEach(function (c) { link(p, c); }); });
    DEV.forEach(function (d) { if (d.sw && pos[d.id]) link(d.sw, d.id, (d.warn ? 'slow ' : '') + (d.ndi ? 'ndi' : d.dante ? 'dante' : '')); });
    function node(d) {
      var p = pos[d.id]; if (!p) return;
      var infra = d.kind && d.kind !== 'client';
      var keep = !opts.filter || opts.filter(d);
      var g = s('g', { class: 'm-node' + (infra ? ' infra' : '') + (d.ndi ? ' ndi' : '') + (d.dante ? ' dante' : '') + (d.warn ? ' warn' : '') + (d.off ? ' off' : '') + (opts.sel === d.id ? ' sel' : '') + (keep ? '' : ' dim'), transform: 'translate(' + p.x + ',' + p.y + ')', tabindex: 0, role: 'button', 'data-id': d.id }, ng);
      s('rect', { width: NW, height: NH, rx: 8 }, g);
      s('text', { class: 'm-name', x: 10, y: 18 }, g, (d.warn ? '⚠ ' : '') + d.name);
      s('text', { class: 'm-sub', x: 10, y: 34 }, g, d.off ? d.off : infra ? d.model : (d.ip + (d.sw ? ' · port ' + d.port : '')));
      g.addEventListener('click', function () { if (opts.onPick) opts.onPick(d.id); });
    }
    GEAR.forEach(node); DEV.forEach(node);
    if (groups.off) { s('text', { class: 'm-sub', x: pos._off.x, y: clientY - 10 }, svg, 'Not seen on a switch'); }
    return svg;
  }

  function tabs(id, onChange) {
    var bar = document.getElementById(id);
    bar.addEventListener('click', function (e) {
      var b = e.target.closest('button'); if (!b || !b.dataset.p) return;
      Array.prototype.forEach.call(bar.children, function (x) { x.classList.toggle('on', x === b); x.setAttribute('aria-selected', x === b); });
      document.querySelectorAll('.pane').forEach(function (p) { p.classList.toggle('on', p.id === b.dataset.p); });
      if (onChange) onChange(b.dataset.p);
    });
  }
  function seg(el, onChange) {
    el.addEventListener('click', function (e) { var b = e.target.closest('button'); if (!b) return; Array.prototype.forEach.call(el.children, function (x) { x.classList.toggle('on', x === b); }); onChange(b.dataset.v); });
  }
  function speed(m) { return !m ? '—' : m >= 1000 ? m / 1000 + ' Gb/s' : m + ' Mb/s'; }

  /* one detail card for a device or switch */
  function detail(parent, id) {
    parent.textContent = '';
    var d = dev(id), c = h('div', 'card', parent); if (!d) return;
    var hd = h('div', 'between', c); h('b', '', hd, d.name);
    h('span', 'badge ' + (d.off ? 'err' : d.warn ? 'warn' : 'ok'), hd, d.off ? 'Offline' : d.warn ? 'Check' : 'Online');
    h('p', 'muted', c, d.what || d.model);
    var tg = h('div', 'gap', c); (d.tags || []).forEach(function (t) { h('span', 'tag' + (t === 'NDI' ? ' ndi' : t === 'Dante' ? ' dante' : ''), tg, t); });
    if (d.warn) h('p', 'hint', c, d.warn).style.color = 'var(--warn)';
    var kv = h('dl', 'kv', c);
    function row(k, v) { h('dt', '', kv, k); h('dd', '', kv, v); }
    row('Address', d.ip); row('MAC', d.mac);
    if (d.sw) { row('Plugged into', gear(d.sw).name + ', port ' + d.port); row('Link', speed(d.speed)); if (d.poe) row('PoE', d.poe + ' W'); if (d.errors) row('Errors', d.errors + ' today'); }
    if (d.place) row('Where', d.place);
    if (d.up) row('Uplink', gear(d.up).name + ', port ' + d.up_port);
    if (d.off) row('Last seen', d.off.replace('Last seen ', ''));
    if (d.seen) row('Seen by', d.seen.join(', '));
    var f = h('div', 'field', c); h('label', '', f, 'Note'); var ta = h('textarea', '', f); ta.rows = 2; ta.value = d.sw ? NOTES[d.sw + ':' + d.port] || '' : ''; ta.placeholder = 'Where the cable runs, who to ask…'; ta.style.width = '100%';
    var g = h('div', 'gap', c);
    if (d.ip && !d.off) { var o = h('button', 'sm', g, 'Open its web page'); o.type = 'button'; }
    var cp = h('button', 'sm', g, 'Copy address'); cp.type = 'button';
    if (d.off) { var fg = h('button', 'sm quiet', g, 'Forget'); fg.type = 'button'; }
  }

  window.NET = { h: h, s: s, GEAR: GEAR, DEV: DEV, SOURCES: SOURCES, PORTS: PORTS, TRAFFIC: TRAFFIC, FLOWS: FLOWS, NOTES: NOTES,
    gear: gear, dev: dev, portInfo: portInfo, drawSwitch: drawSwitch, apCard: apCard, hoverTips: hoverTips, drawMap: drawMap,
    tabs: tabs, seg: seg, speed: speed, detail: detail };
})();
