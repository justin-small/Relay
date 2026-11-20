/* Blackmagic drawings (the rack redesign): each device's front and back, live from the page's state. The server builds
   each face from the device's model (app/modules/blackmagic/drawings.py), so it knows every connector, which are core
   ports (#540); this file only draws a face it is given. A button Relay can press carries an action, the rest are drawn
   dimmed and say why. Loaded before blackmagic.js; exposes window.BMDraw. */
(function () {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const el = relayUI.el;
  function svgEl(tag, attrs, parent, text) {
    const e = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { if (attrs[k] !== null && attrs[k] !== undefined) e.setAttribute(k, attrs[k]); });
    if (text !== undefined && text !== null) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  }
  /* The drawing names, from the server (GET /api/admin/blackmagic `drawings`); blackmagic.js fills them in. */
  const NAMES = {};
  function auto(dev) { return dev.auto_drawing || 'atem-rack'; }
  function drawingOf(dev) { return dev.drawing && NAMES[dev.drawing] ? dev.drawing : auto(dev); }

  /* What the drawing needs to know about one device, from the page's state (GET /api/admin/blackmagic). */
  function view(dev, meIndex) {
    const v = { id: dev.id, name: dev.name, kind: dev.kind, connected: !!dev.connected, product: dev.product || dev.model || '',
      notes: dev.notes || {}, drawing: drawingOf(dev), dev: dev };
    if (dev.kind === 'atem') {
      const me = (dev.mix_effects || [])[meIndex || 0] || {};
      v.inputs = {};
      (dev.inputs || []).forEach(function (i) { v.inputs[i.id] = i.name; });
      v.pgm = me.program; v.pvw = me.preview; v.me = me.me || 0;
      v.dsk = dev.downstream_keyers || 0;
      v.macros = dev.macros || [];
      v.external = (dev.inputs || []).filter(function (i) { return i.id >= 1 && i.id <= 40; }).length;
    } else if (dev.kind === 'hyperdeck') {
      const d = dev.hyperdeck || {};
      v.status = d.status || ''; v.clip = d.clip && d.clip.name ? d.clip.name : ''; v.tc = d.timecode || '';
      v.format = d.video_format || ''; v.remote = d.remote_enabled !== false; v.slots = d.slots || []; v.slot = d.slot;
    } else {
      const h = dev.videohub || {};
      v.inputs = (h.inputs || []).map(function (p) { return p.label || 'Input ' + (p.n + 1); });
      v.outputs = (h.outputs || []).map(function (p) { return p.label || 'Output ' + (p.n + 1); });
      v.routes = (h.outputs || []).map(function (p) { return p.input === null || p.input === undefined ? null : p.input + 1; });
      v.locks = {};
      (h.outputs || []).forEach(function (p) { if (p.lock && p.lock !== 'U') v.locks[p.n + 1] = p.lock; });
    }
    return v;
  }
  const BLANK = { form: 'rack', u: 1, front: { w: 960, h: 88, items: [] }, back: { w: 960, h: 88, items: [] } };
  /* Both faces of a device, as the server drew them from what the device reports (drawings.py). */
  function spec(v) { return (v.dev && v.dev.face) || BLANK; }
  function findPort(v, pid) {
    return spec(v).back.items.find(function (i) { return i.t === 'port' && i.id === pid; }) || null;
  }

  // ------------------------------------------------------------------------------------------- live state
  function btnState(v, it, ui) {
    if (!it.act) return it.lit ? 'on' : 'dis';
    const a = it.act.split(':');
    if (v.kind === 'atem') {
      if (a[0] === 'pgm' && v.pgm === +a[1]) return 'pgm';
      if (a[0] === 'pvw' && v.pvw === +a[1]) return 'pvw';
    } else if (v.kind === 'hyperdeck') {
      if (a[0] === 'record' && v.status === 'record') return 'pgm';
      if (a[0] === 'play' && v.status === 'play') return 'pvw';
      if (a[0] === 'stop' && (v.status === 'stop' || v.status === 'stopped')) return 'on';
    } else if (v.kind === 'videohub') {
      if (a[0] === 'dst' && ui.out === +a[1]) return 'on';
      if (a[0] === 'dst' && v.locks[+a[1]]) return 'lock';
      if (a[0] === 'take' && ui.out && ui.inp) return 'pgm';
    }
    return '';
  }
  function tipFor(v, it) {
    if (!it.act) return it.why || 'Not controlled from Relay.';
    if (it.tipText) return it.tipText;
    const a = it.act.split(':');
    const name = function (n) { return v.inputs && v.inputs[n] !== undefined ? v.inputs[n] : a[1]; };
    switch (a[0]) {
      case 'pgm': return 'Cut ' + name(+a[1]) + ' straight to program';
      case 'pvw': return 'Put ' + name(+a[1]) + ' on preview';
      case 'cut': return 'Cut: preview goes to program';
      case 'auto': return 'Auto: transition preview to program';
      case 'ftb': return 'Fade to black, or back from black';
      case 'record': return 'Record'; case 'stop': return 'Stop'; case 'play': return 'Play';
      case 'dst': return 'Choose output ' + a[1] + ' (' + v.outputs[+a[1] - 1] + ') to route';
      case 'take': return 'Take the chosen route';
      case 'lock': return 'Lock or unlock the chosen output';
    }
    return '';
  }

  /* One connector: what it is, what Relay knows is on it now, its cable and its note. */
  function portInfo(v, p, ctx) {
    const info = { title: v.name + ' · ' + p.label, kind: KIND[p.kind] || p.kind,
      dir: p.dir === 'in' ? 'Input' : p.dir === 'out' ? 'Output' : 'Connection',
      label: '', format: '', now: '', cable: '', note: v.notes[p.id] || '', key: v.id + ':' + p.id, live: false, cabled: false };
    if (p.net || p.power) { info.live = v.connected; info.now = v.connected ? (p.net ? 'Relay is connected over this' : 'On') : 'Not connected'; }
    if (v.kind === 'atem') {
      if (p.input) {
        info.label = v.inputs[p.input] || '';
        if (v.pgm === p.input) { info.now = 'On program now'; info.live = true; }
        else if (v.pvw === p.input) { info.now = 'On preview'; info.live = true; }
      }
      if (p.out === 0) { info.label = 'Program'; info.live = v.connected; info.now = 'Program out'; }
    } else if (v.kind === 'hyperdeck') {
      if (p.rec) { info.format = v.format; info.live = v.status === 'record'; info.now = v.status === 'record' ? 'Recording: ' + (v.clip || 'a new clip') : ''; }
    } else if (v.kind === 'videohub') {
      if (p.input) {
        info.label = v.inputs[p.input - 1] || '';
        const to = [];
        v.routes.forEach(function (src, o) { if (src === p.input) to.push(v.outputs[o]); });
        info.live = to.length > 0;
        info.now = to.length ? 'Routed to ' + to.join(', ') : 'Not routed anywhere';
      } else if (p.out) {
        info.label = v.outputs[p.out - 1] || '';
        const src = v.routes[p.out - 1];
        info.now = src ? 'Shows input ' + src + ' · ' + v.inputs[src - 1] : '';
        if (v.locks[p.out]) info.now += v.locks[p.out] === 'O' ? ' · locked by Relay' : ' · locked by another panel';
      }
    }
    (ctx.cables || []).forEach(function (c) {
      const mine = c.a[0] === v.id && c.a[1] === p.id ? 'a' : c.b[0] === v.id && c.b[1] === p.id ? 'b' : '';
      if (!mine) return;
      const other = mine === 'a' ? c.b : c.a;
      const ov = ctx.views[other[0]], op = ov ? findPort(ov, other[1]) : null;
      info.cabled = true; info.cableId = c.id;
      info.cable = (mine === 'a' ? 'To ' : 'From ') + (ov ? ov.name : 'a removed device') + ' · ' + (op ? op.label : other[1]);
    });
    if (info.cabled && v.kind === 'videohub' && p.out) info.live = true;
    return info;
  }
  const KIND = { sdi: 'SDI (BNC)', hdmi: 'HDMI', eth: 'Ethernet (RJ45)', usb: 'USB-C', xlr: 'XLR', mini: '3.5 mm jack', iec: 'Mains (IEC)', barrel: '12V DC' };

  function lcdLines(v, ui) {
    if (v.kind === 'atem') return [['PGM', v.inputs[v.pgm] || '—'], ['PVW', v.inputs[v.pvw] || '—'], ['M/E', String(v.me + 1)]];
    if (v.kind === 'hyperdeck') {
      const st = { record: '● REC', play: '▶ PLAY', stop: '■ STOP', stopped: '■ STOP' }[v.status] || (v.status || '—').toUpperCase();
      const slot = (v.slots || []).find(function (s) { return +s.id === +v.slot; });
      const left = slot && slot.seconds_left ? Math.floor(slot.seconds_left / 3600) + ':' + String(Math.floor(slot.seconds_left % 3600 / 60)).padStart(2, '0') + ':' + String(slot.seconds_left % 60).padStart(2, '0') : '';
      return [[st, v.tc], ['', v.clip], left ? ['LEFT', left] : ['FMT', v.format]];
    }
    const o = ui.out || 1, src = v.routes[o - 1];
    const lines = [['OUT ' + o, v.outputs[o - 1] || ''], ['IN ' + (src || '—'), src ? v.inputs[src - 1] : '']];
    if (ui.inp) lines.push(['NEXT', ui.inp + ' · ' + v.inputs[ui.inp - 1]]);
    else lines.push(['', ui.out ? 'Pick an input, then TAKE' : 'Press an OUT button']);
    return lines;
  }

  // ------------------------------------------------------------------------------------------- drawing
  function drawPort(g, p, info) {
    const pg = svgEl('g', { class: 'hw-port ' + p.kind + (info.live ? ' live' : info.cabled ? ' used' : ''),
      transform: 'translate(' + p.x + ',' + p.y + ')', tabindex: '0', role: 'button', 'aria-label': info.title }, g);
    pg.dataset.port = p.id;
    const k = p.kind;
    if (k === 'sdi') { svgEl('circle', { r: p.tiny ? 7 : 10, class: 'hw-ring' }, pg); svgEl('circle', { r: p.tiny ? 4.5 : 6.5, class: 'hw-hole' }, pg); svgEl('circle', { r: 2.2, class: 'hw-pin' }, pg); }
    else if (k === 'hdmi') { svgEl('path', { d: 'M-15,-6 h30 v6 l-4,5 h-22 l-4,-5 z', class: 'hw-ring' }, pg); svgEl('rect', { x: -11, y: -3, width: 22, height: 3, class: 'hw-pin' }, pg); }
    else if (k === 'eth') { svgEl('rect', { x: -11, y: -10, width: 22, height: 18, rx: 1.5, class: 'hw-ring' }, pg); svgEl('rect', { x: -5, y: 5, width: 10, height: 5, class: 'hw-hole' }, pg); }
    else if (k === 'usb') { svgEl('rect', { x: -9, y: -4, width: 18, height: 8, rx: 4, class: 'hw-ring' }, pg); svgEl('rect', { x: -5, y: -1, width: 10, height: 2, class: 'hw-pin' }, pg); }
    else if (k === 'xlr') { svgEl('circle', { r: 13, class: 'hw-ring' }, pg); [[-4, -3], [4, -3], [0, 4]].forEach(function (q) { svgEl('circle', { cx: q[0], cy: q[1], r: 1.8, class: 'hw-pin' }, pg); }); }
    else if (k === 'mini') { svgEl('circle', { r: 7, class: 'hw-ring' }, pg); svgEl('circle', { r: 3, class: 'hw-hole' }, pg); }
    else if (k === 'iec') { svgEl('rect', { x: -16, y: -12, width: 32, height: 24, rx: 3, class: 'hw-ring' }, pg); [-7, 0, 7].forEach(function (x) { svgEl('rect', { x: x - 1.5, y: -4, width: 3, height: 8, class: 'hw-pin' }, pg); }); }
    else { svgEl('circle', { r: 9, class: 'hw-ring' }, pg); svgEl('circle', { r: 4, class: 'hw-hole' }, pg); }
    svgEl('circle', { r: 2.6, cx: k === 'iec' ? 20 : (p.tiny ? 9 : 13), cy: p.tiny ? -9 : -11, class: 'hw-led' }, pg);
    const y = p.compact ? 22 : (k === 'xlr' ? 28 : 24);
    svgEl('text', { y: y, 'text-anchor': 'middle', class: 'hw-plabel' }, pg, p.label);
    if (info.label && info.label !== p.label && !p.tiny) {
      const max = p.compact ? 6 : 8;
      svgEl('text', { y: y + 11, 'text-anchor': 'middle', class: 'hw-dlabel' }, pg, info.label.length > max ? info.label.slice(0, max - 1) + '…' : info.label);
    }
    if (info.note) svgEl('circle', { r: 2.6, cx: p.tiny ? -9 : -13, cy: p.tiny ? -9 : -11, class: 'hw-note' }, pg);
  }
  function drawButton(g, v, it, opts) {
    const live = it.act && opts.control && (!it.manage || opts.manage) && v.connected;
    const st = btnState(v, it, opts.ui || {});
    const bg = svgEl('g', { class: 'hw-btn' + (st ? ' ' + st : '') + (it.act && !live ? ' off' : '') + (it.big ? ' big' : '') + (it.small ? ' small' : '') + (it.tone ? ' tone-' + it.tone : ''),
      transform: 'translate(' + it.x + ',' + it.y + ')', tabindex: live ? '0' : null, role: live ? 'button' : null }, g);
    if (live) bg.dataset.act = it.act;
    svgEl('rect', { width: it.w, height: it.h, rx: 4, class: 'hw-key' }, bg);
    svgEl('text', { x: it.w / 2, y: it.h / 2 + 4, 'text-anchor': 'middle', class: 'hw-klabel' }, bg, it.label);
    svgEl('title', {}, bg, it.act && !opts.control ? 'Needs permission to control Blackmagic devices.' : tipFor(v, it));
    if (it.name) {
      const nm = v.inputs[it.src] || '';
      svgEl('text', { x: it.w / 2, y: it.h + 10, 'text-anchor': 'middle', class: 'hw-dlabel' }, bg, nm.length > 7 ? nm.slice(0, 6) + '…' : nm);
    }
  }

  /* An <svg> of one face ('front' or 'back') of a device. opts: control, manage (permissions), ui (the Videohub's
     chosen output and input), sel (the selected connector id), ctx ({views, cables}). */
  function draw(v, face, opts) {
    opts = opts || {};
    const s = spec(v), side = s[face];
    const svg = svgEl('svg', { viewBox: '0 0 ' + side.w + ' ' + side.h, class: 'hw ' + s.form + ' ' + face, role: 'img', 'aria-label': v.name + ', ' + face });
    svg.dataset.dev = v.id; svg.dataset.face = face;
    if (s.form === 'rack') {
      svgEl('rect', { x: 0, y: 0, width: side.w, height: side.h, rx: 3, class: 'hw-ear' }, svg);
      svgEl('rect', { x: 24, y: 2, width: side.w - 48, height: side.h - 4, rx: 3, class: 'hw-body' }, svg);
      for (let u = 0; u < (s.u || 1); u++) {
        [12, side.w - 12].forEach(function (x) {
          svgEl('ellipse', { cx: x, cy: 88 * u + 22, rx: 5, ry: 3.5, class: 'hw-screw' }, svg);
          svgEl('ellipse', { cx: x, cy: 88 * u + 66, rx: 5, ry: 3.5, class: 'hw-screw' }, svg);
        });
      }
    } else {
      svgEl('rect', { x: 0, y: 0, width: side.w, height: side.h, rx: face === 'front' ? 16 : 8, class: 'hw-body' }, svg);
    }
    const ctx = opts.ctx || { views: {}, cables: [] };
    side.items.forEach(function (it) {
      if (it.t === 'zone') {
        svgEl('rect', { x: it.x, y: it.y, width: it.w, height: it.h, rx: 6, class: 'hw-zone' }, svg);
        svgEl('text', { x: it.x + 8, y: it.y + 14, class: 'hw-cap' }, svg, it.label);
      } else if (it.t === 'text') {
        if (it.label) svgEl('text', { x: it.x, y: it.y, 'text-anchor': it.anchor || 'start', class: it.cls }, svg, it.label);
      } else if (it.t === 'lcd') {
        const lg = svgEl('g', { class: 'hw-lcd', transform: 'translate(' + it.x + ',' + it.y + ')' }, svg);
        svgEl('rect', { width: it.w, height: it.h, rx: 3 }, lg);
        const lines = v.connected ? lcdLines(v, opts.ui || {}) : [['', 'Not connected']];
        const lh = Math.min(20, (it.h - 8) / Math.max(lines.length, 1));
        lines.forEach(function (ln, i) {
          const y = 8 + lh * (i + 0.75), t = String(ln[1] || '');
          svgEl('text', { x: 8, y: y, class: 'hw-lcd-k' }, lg, ln[0]);
          svgEl('text', { x: 52, y: y, class: 'hw-lcd-v' }, lg, t.length > 16 ? t.slice(0, 15) + '…' : t);
        });
      } else if (it.t === 'slot') {
        const info = (v.slots || []).find(function (x) { return +x.id === it.slot; }) || {};
        const sg = svgEl('g', { transform: 'translate(' + it.x + ',' + it.y + ')', class: 'hw-slot' + (v.status === 'record' && +v.slot === it.slot ? ' rec' : '') }, svg);
        svgEl('rect', { width: it.w, height: it.h, rx: 2 }, sg);
        svgEl('text', { x: it.w + 6, y: it.h - 3, class: 'hw-cap' }, sg, it.label);
        svgEl('title', {}, sg, it.label + ': ' + (info.status === 'mounted' ? (info.volume || 'Disk') : info.status === 'empty' ? 'no disk' : info.status || 'unknown'));
      } else if (it.t === 'knob') {
        const kg = svgEl('g', { transform: 'translate(' + it.x + ',' + it.y + ')', class: 'hw-knob' }, svg);
        svgEl('circle', { r: it.r }, kg); svgEl('circle', { r: it.r * 0.35, class: 'hw-hole' }, kg);
        svgEl('title', {}, kg, it.why || '');
      } else if (it.t === 'btn') drawButton(svg, v, it, opts);
      else if (it.t === 'port') drawPort(svg, it, portInfo(v, it, ctx));
    });
    if (opts.sel) {
      const sel = Array.prototype.find.call(svg.querySelectorAll('[data-port]'), function (n) { return n.dataset.port === opts.sel; });
      if (sel) sel.classList.add('sel');
    }
    if (!v.connected) svg.classList.add('offline');
    return svg;
  }

  // ------------------------------------------------------------------------------------------- tooltip and handling
  let tip = null;
  function showTip(evt, info) {
    if (!tip) { tip = el('div', 'hw-tip'); document.body.appendChild(tip); }
    tip.textContent = '';
    const dl = el('dl');
    [['Type', info.kind + ' · ' + info.dir], ['Label', info.label], ['Format', info.format], ['Now', info.now], ['Cable', info.cable], ['Note', info.note]]
      .forEach(function (r) { if (r[1]) dl.append(el('dt', { text: r[0] }), el('dd', { text: r[1] })); });
    tip.append(el('b', { text: info.title }), dl);
    tip.hidden = false;
    const x = Math.min(evt.clientX + 14, window.innerWidth - 300);
    let y = evt.clientY + 16;
    if (y + 180 > window.innerHeight) y = evt.clientY - 180;
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  }
  function hideTip() { if (tip) tip.hidden = true; }

  /* Hover, click and keyboard on a drawn face. on.port(pid) when a connector is clicked; on.act(action) when a
     button is pressed; ctx as for draw(). */
  function wire(svg, v, ctx, on) {
    svg.addEventListener('mousemove', function (e) {
      const pg = e.target.closest('[data-port]');
      if (!pg) { hideTip(); return; }
      showTip(e, portInfo(v, findPort(v, pg.dataset.port), ctx));
    });
    svg.addEventListener('mouseleave', hideTip);
    function act(e) {
      const pg = e.target.closest('[data-port]');
      if (pg) { hideTip(); if (on.port) on.port(pg.dataset.port); return; }
      const b = e.target.closest('[data-act]');
      if (b && on.act) on.act(b.dataset.act);
    }
    svg.addEventListener('click', act);
    svg.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); act(e); } });
  }

  window.BMDraw = { NAMES: NAMES, auto: auto, view: view, spec: spec, draw: draw, wire: wire, portInfo: portInfo, findPort: findPort, hideTip: hideTip, svgEl: svgEl };
})();
