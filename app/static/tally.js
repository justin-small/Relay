/* The tally screens (#245) and the USB camera tile (#248): read /stream?stream=tally (app/tally.py). Each message is
   the whole state. A camera screen turns red on program and green on preview, and also says LIVE or NEXT, so it can
   be read without telling the colours apart. */
(function () {
  'use strict';
  const body = document.body;
  const view = body.dataset.view;
  const mine = parseInt(body.dataset.camera, 10) || 0;
  const $ = function (id) { return document.getElementById(id); };
  const STALE_MS = 15000;      // a message comes at least every 5 s
  let state = null;
  let lastMsg = 0;

  function find(n) {
    return state ? state.cameras.find(function (c) { return c.n === n; }) : null;
  }
  function tone(c) { return !c ? '' : c.program ? 'live' : c.preview ? 'next' : ''; }
  function stale() { return !state || Date.now() - lastMsg > STALE_MS; }
  function label(c) { return c.name || ('Camera ' + c.n); }

  function paintCamera() {
    const cam = $('cam');
    const c = find(mine);
    const t = stale() ? 'lost' : tone(c);
    cam.className = 'ty-cam' + (t ? ' ty-' + t : '');
    $('camNum').textContent = mine || '—';
    $('camName').textContent = c ? label(c) : '';
    $('camState').textContent = stale() ? (state ? 'Connection lost' : 'Connecting…')
      : !mine ? 'Add ?camera=1 to the address'
      : !state.active ? 'No switcher connected'
      : !c ? 'No camera ' + mine
      : t === 'live' ? 'LIVE' : t === 'next' ? 'NEXT' : '';
  }

  function paintOperator() {
    const c = find(mine);
    const t = stale() ? 'lost' : tone(c);
    $('opMain').className = 'ty-op-main' + (t ? ' ty-' + t : '');
    $('opName').textContent = c ? label(c) : '';
    $('opState').textContent = stale() ? (state ? 'Connection lost' : 'Connecting…')
      : !mine ? 'Add &camera=1 to the address'
      : !state.active ? 'No switcher connected'
      : !c ? 'No camera ' + mine
      : t === 'live' ? 'LIVE' : t === 'next' ? 'NEXT' : 'Standby';
    const names = function (list) {
      return list.map(function (n) { const x = find(n); return x ? label(x) : n; }).join(', ') || '—';
    };
    $('opLive').querySelector('strong').textContent = state ? names(state.live) : '—';
    $('opNext').querySelector('strong').textContent = state ? names(state.next) : '—';
  }

  const tiles = {};
  function paintBoard() {
    const board = $('board');
    const list = state ? state.cameras : [];
    if (!list.length) {
      board.replaceChildren();
      Object.keys(tiles).forEach(function (k) { delete tiles[k]; });
      const p = document.createElement('p');
      p.className = 'ty-empty';
      p.textContent = stale() ? (state ? 'Connection lost' : 'Connecting…') : 'No switcher connected';
      board.appendChild(p);
      return;
    }
    const keep = {};
    list.forEach(function (c) {
      let t = tiles[c.n];
      if (!t) {
        const el = document.createElement('div');
        const num = document.createElement('div'); num.className = 'ty-tnum';
        const nm = document.createElement('div'); nm.className = 'ty-tname';
        const st = document.createElement('div'); st.className = 'ty-tstate';
        const video = document.createElement('video');
        video.className = 'ty-video'; video.autoplay = true; video.muted = true; video.playsInline = true; video.hidden = true;
        const sel = document.createElement('select'); sel.className = 'ty-sel'; sel.hidden = true;
        sel.setAttribute('aria-label', 'USB camera for camera ' + c.n);
        el.append(num, nm, st, video, sel);
        t = tiles[c.n] = { el: el, num: num, nm: nm, st: st, video: video, sel: sel };
        sel.onchange = function () { chooseDevice(c.n, sel.value); };
        usbTile(c.n, t);
      }
      keep[c.n] = true;
      const tn = stale() ? 'lost' : tone(c);
      t.el.className = 'ty-tile' + (tn ? ' ty-' + tn : '');
      t.num.textContent = c.n;
      t.nm.textContent = label(c);
      t.st.textContent = tn === 'live' ? 'LIVE' : tn === 'next' ? 'NEXT' : '';
    });
    Object.keys(tiles).forEach(function (k) { if (!keep[k]) { stopStream(tiles[k]); delete tiles[k]; } });
    board.replaceChildren.apply(board, list.map(function (c) { return tiles[c.n].el; }));
  }

  // ---- the USB camera tile (#248): a camera plugged into this computer, shown beside its tally -------------------
  // A browser lets a page open a camera only on https or localhost, so on plain http the choice is not offered.
  let devices = null;
  const KEY = 'relay.tally.usb.';
  function store(k, v) { try { if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) { /* private window */ } }
  function load(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function canUsb() { return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.isSecureContext); }
  function stopStream(t) {
    const s = t.video && t.video.srcObject;
    if (s) s.getTracks().forEach(function (x) { x.stop(); });
    if (t.video) t.video.srcObject = null;
  }
  async function listDevices() {
    const all = await navigator.mediaDevices.enumerateDevices();
    devices = all.filter(function (d) { return d.kind === 'videoinput'; });
    return devices;
  }
  function fillSelect(sel, current) {
    sel.replaceChildren();
    const none = document.createElement('option'); none.value = ''; none.textContent = 'No USB camera';
    sel.appendChild(none);
    (devices || []).forEach(function (d, i) {
      const o = document.createElement('option'); o.value = d.deviceId; o.textContent = d.label || ('Camera ' + (i + 1));
      sel.appendChild(o);
    });
    sel.value = current || '';
    if (sel.value !== (current || '')) sel.value = '';
  }
  async function chooseDevice(n, id) {
    const t = n === 'op' ? opTile : tiles[n];
    if (!t) return;
    stopStream(t);
    store(KEY + n, id || null);
    if (!id) { t.video.hidden = true; return; }
    try {
      t.video.srcObject = await navigator.mediaDevices.getUserMedia({ video: { deviceId: { exact: id } }, audio: false });
      t.video.hidden = false;
    } catch (e) {
      t.video.hidden = true;
      store(KEY + n, null);
      t.sel.value = '';
    }
  }
  function usbTile(n, t) {
    if (!canUsb() || !devices) return;
    t.sel.hidden = false;
    const saved = load(KEY + n) || '';
    fillSelect(t.sel, saved);
    if (saved && t.sel.value === saved) chooseDevice(n, saved);
  }
  async function enableUsb() {
    try {
      const probe = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
      probe.getTracks().forEach(function (x) { x.stop(); });   // permission given; the device names show now
      await listDevices();
    } catch (e) { return; }
    $('usbAll').hidden = true;
    Object.keys(tiles).forEach(function (k) { usbTile(+k, tiles[k]); });
    if (opTile) usbTile('op', opTile);
  }

  let opTile = null;
  if (view === 'operator' && mine) {
    opTile = { video: $('opVideo'), sel: $('opSel') };
    $('opSel').onchange = function () { chooseDevice('op', $('opSel').value); };
    if (canUsb()) {
      const btn = $('opPick');
      btn.hidden = false;
      $('opSel').replaceChildren(new Option('Choose USB camera…', ''));
      $('opSel').addEventListener('focus', function once() {
        $('opSel').removeEventListener('focus', once);
        enableUsb();
      });
    }
  }
  if (view === 'board' && canUsb()) {
    $('usbAll').hidden = false;
    $('usbAll').onclick = enableUsb;
  }

  function paint() {
    if (view === 'board') paintBoard(); else if (view === 'operator') paintOperator(); else paintCamera();
    const dot = $('connDot');
    if (dot) {
      dot.className = 'dot' + (stale() ? '' : ' ok');
      $('connText').textContent = stale() ? (state ? 'Connection lost' : 'Connecting…') : 'Live';
    }
  }

  let source = null;
  function connect() {
    if (source) source.close();
    source = new EventSource('/stream?stream=tally');
    source.onmessage = function (e) {
      try { state = JSON.parse(e.data); } catch (err) { return; }
      lastMsg = Date.now();
      paint();
    };
    source.onerror = function () { paint(); };
  }
  connect();
  setInterval(paint, 2000);   // a silent connection turns the screen grey
  document.addEventListener('visibilitychange', function () { if (!document.hidden) paint(); });
})();
