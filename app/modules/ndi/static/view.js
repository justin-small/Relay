/* The NDI single-source viewer page (#296): the MJPEG picture, a meter for each audio channel with the loudness, a Listen
   button that plays the sound in this browser (#301), and the tally border. */
(function () {
  const d = JSON.parse(document.getElementById('ndvData').textContent);
  const root = document.getElementById('ndv'), img = document.getElementById('ndvImg');
  const info = document.getElementById('ndvInfo'), msg = document.getElementById('ndvMsg');
  const sound = document.getElementById('ndvSound'), meters = document.getElementById('ndvMeters');
  const loud = document.getElementById('ndvLoud'), chans = document.getElementById('ndvChans');
  const listenBtn = document.getElementById('ndvListen'), only = document.getElementById('ndvAudioOnly');
  const enc = encodeURIComponent(d.source);
  const MAX_BARS = 8, FLOOR = -60;
  let retry = 1000, audioOnly = false, bars = [], held = [], count = 0;
  const chosen = new Set();               // channels to hear, 0-based; empty means all
  function play() {
    if (audioOnly) return;
    img.src = '/ndi/stream/' + enc + '?t=' + Date.now();
  }
  img.onload = function () { msg.textContent = ''; retry = 1000; };
  img.onerror = function () {
    if (audioOnly) return;
    msg.textContent = 'Waiting for the source...';
    setTimeout(play, retry);
    retry = Math.min(10000, retry * 2);
  };
  play();

  function frac(db) { return Math.max(0, Math.min(100, (db - FLOOR) / -FLOOR * 100)); }
  function build(n) {                     // one bar per channel, and a button for each when there are more than two
    count = n; meters.textContent = ''; chans.textContent = ''; bars = []; held = [];
    for (let c = 0; c < Math.min(n, MAX_BARS); c++) {
      const row = document.createElement('div'), bar = document.createElement('i'), hold = document.createElement('u');
      row.className = 'ch'; row.title = 'Channel ' + (c + 1); row.append(bar, hold); meters.appendChild(row);
      bars.push([bar, hold]); held.push({ db: FLOOR, at: 0 });
    }
    if (n > 2) for (let c = 0; c < n; c++) {
      const b = document.createElement('button');
      b.type = 'button'; b.textContent = String(c + 1); b.setAttribute('aria-pressed', String(chosen.has(c)));
      b.onclick = function () {
        if (chosen.has(c)) chosen.delete(c); else chosen.add(c);
        b.setAttribute('aria-pressed', String(chosen.has(c)));
        if (ctx) { stop(); start(); }
      };
      chans.appendChild(b);
    }
  }
  function draw(levels, now) {
    levels.slice(0, MAX_BARS).forEach(function (l, c) {
      const peak = typeof l.peak === 'number' ? l.peak : FLOOR, h = held[c], b = bars[c][0];
      b.style.width = frac(peak) + '%';
      b.className = peak > -1 ? 'clip' : peak > -9 ? 'hot' : '';
      if (peak >= h.db || now - h.at > 1500) { h.db = peak; h.at = now; }
      bars[c][1].style.left = 'calc(' + frac(h.db) + '% - 0.15em)';
    });
  }
  async function level() {
    try {
      const res = await fetch('/ndi/level/' + enc, { cache: 'no-store', credentials: 'same-origin' });
      if (res.ok) {
        const m = await res.json(), levels = m.channels || [];
        info.textContent = m.width ? m.width + 'x' + m.height + (m.fps ? ' ' + m.fps + ' fps' : '') : '';
        if (m.audio_only && !audioOnly) { audioOnly = true; img.hidden = true; only.hidden = false; msg.textContent = ''; }
        if (!m.audio_only && audioOnly) { audioOnly = false; img.hidden = false; only.hidden = true; play(); }
        sound.hidden = !levels.length && typeof m.db !== 'number';
        if (levels.length && levels.length !== count) build(levels.length);
        draw(levels, Date.now());
        loud.textContent = typeof m.momentary === 'number' ? m.momentary.toFixed(1) + ' LUFS' : '';
      }
    } catch (e) { /* the next tick tries again */ }
    setTimeout(level, 150);
  }
  level();

  // ---- listen: the sound as 16-bit stereo PCM, played through Web Audio
  let ctx = null, ctl = null;
  function stop() {
    if (ctl) ctl.abort();
    if (ctx) ctx.close();
    ctx = ctl = null; listenBtn.setAttribute('aria-pressed', 'false'); listenBtn.textContent = 'Listen';
  }
  async function start() {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) { msg.textContent = 'This browser cannot play the sound.'; return; }
    ctx = new Ctx(); ctl = new AbortController();
    const mine = ctx;
    listenBtn.setAttribute('aria-pressed', 'true'); listenBtn.textContent = 'Stop';
    const pick = Array.from(chosen).sort().map(function (c) { return c + 1; }).join(',');
    try {
      const res = await fetch('/ndi/listen/' + enc + (pick ? '?ch=' + pick : ''), { credentials: 'same-origin', signal: ctl.signal });
      if (!res.ok) { const e = await res.json().catch(function () { return {}; }); msg.textContent = e.error || 'Could not play the sound.'; if (ctx === mine) stop(); return; }
      msg.textContent = '';
      const rate = Number(res.headers.get('X-Rate')) || 48000, reader = res.body.getReader();
      let next = 0, rest = new Uint8Array(0);
      for (;;) {
        const got = await reader.read();
        if (got.done) break;
        const joined = new Uint8Array(rest.length + got.value.length);
        joined.set(rest); joined.set(got.value, rest.length);
        const frames = Math.floor(joined.length / 4);
        rest = joined.slice(frames * 4);
        if (!frames) continue;
        const pcm = new Int16Array(joined.buffer, 0, frames * 2), buf = mine.createBuffer(2, frames, rate);
        const l = buf.getChannelData(0), r = buf.getChannelData(1);
        for (let i = 0; i < frames; i++) { l[i] = pcm[i * 2] / 32768; r[i] = pcm[i * 2 + 1] / 32768; }
        const now = mine.currentTime;
        if (next - now > 0.6) continue;               // the browser fell behind: skip ahead to stay live
        if (next < now + 0.05) next = now + 0.1;
        const src = mine.createBufferSource();
        src.buffer = buf; src.connect(mine.destination); src.start(next); next += frames / rate;
      }
      if (ctx === mine) { msg.textContent = 'The sound stopped.'; stop(); }
    } catch (e) { if (ctx === mine && e.name !== 'AbortError') { msg.textContent = 'The sound stopped.'; stop(); } }
  }
  listenBtn.onclick = function () { if (ctx) stop(); else start(); };
  window.addEventListener('pagehide', stop);

  if (d.camera) {            // tally from the shared state the switcher modules fill (app/tally.py)
    let es = null;
    const open = function () {
      es = new EventSource('/stream?stream=tally');
      es.onmessage = function (ev) {
        let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
        const c = (m.cameras || []).filter(function (x) { return x.n === d.camera; })[0];
        root.dataset.tally = c ? (c.program ? 'program' : (c.preview ? 'preview' : '')) : '';
      };
      es.onerror = function () { es.close(); root.dataset.tally = ''; setTimeout(open, 3000); };
    };
    open();
  }
  img.addEventListener('dblclick', function () {
    if (document.fullscreenElement) document.exitFullscreen(); else root.requestFullscreen && root.requestFullscreen();
  });
})();
