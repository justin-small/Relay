/* NDI analysis (#300): stream health, frame timing, picture scopes and sound levels for one source. The server
   (analysis.py) does the measuring; this draws it. The page asks every second while a source is open, which also keeps
   the server's receiver open (it lets go 30 seconds after the last ask). Loaded only while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-ndi') || !document.getElementById('ndiAnSource')) return;
  const $ = R.$, send = relayUI.api, el = relayUI.el, can = relayUI.can;
  const EVERY_MS = 1000;
  let open = null, timer = 0, scope = 'waveform', last = null, busy = false, sources = [];

  function color(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || 'gray'; }
  function fmt(v, unit, digits) { return v === null || v === undefined ? '—' : (digits === undefined ? v : Number(v).toFixed(digits)) + (unit || ''); }
  function showErr(t) { $('ndiAnErr').textContent = t || ''; $('ndiAnErr').hidden = !t; }
  function query(extra) { return '?source=' + encodeURIComponent(open.source) + '&low=' + open.low + (extra || ''); }
  function label(x) { return x.source ? x.machine + ' (' + x.source + ')' : x.name; }

  // ---- the source list
  function fillSources() {
    const state = window.RelayNdiState && window.RelayNdiState();
    const list = (state ? state.sources : []).filter(function (x) { return x.online; });
    const key = JSON.stringify(list.map(function (x) { return x.name; }));
    if (key === JSON.stringify(sources) || ($('ndiAnSource') === document.activeElement)) return;
    sources = list.map(function (x) { return x.name; });
    const sel = $('ndiAnSource'), cur = sel.value;
    sel.textContent = '';
    list.forEach(function (x) { sel.appendChild(el('option', { value: x.name }, label(x))); });
    if (cur && sources.indexOf(cur) >= 0) sel.value = cur;
    $('ndiAnGo').disabled = !list.length;
  }

  // ---- start and stop
  async function begin() {
    showErr('');
    const source = $('ndiAnSource').value, low = $('ndiAnLow').checked;
    if (!source) return;
    if (open) await end(true);
    try {
      const snap = await send('POST', '/api/admin/ndi/analysis/open', { source: source, low: low });
      open = { source: source, low: low };
      $('ndiAnStop').hidden = false; $('ndiAnNone').hidden = true; $('ndiAnBody').hidden = false;
      $('ndiAnRec').disabled = !can('ndi.manage');
      draw(snap);
      schedule();
    } catch (e) { showErr(e.message); }
  }
  async function end(keepPage) {
    clearTimeout(timer); timer = 0;
    const was = open; open = null; last = null;
    if (was) { try { await send('DELETE', '/api/admin/ndi/analysis?source=' + encodeURIComponent(was.source) + '&low=' + was.low); } catch (e) { /* it lets go on its own */ } }
    if (!keepPage) { $('ndiAnBody').hidden = true; $('ndiAnStop').hidden = true; $('ndiAnNone').hidden = false; }
  }
  function schedule() { clearTimeout(timer); timer = setTimeout(poll, EVERY_MS); }
  async function poll() {
    if (!open) return;
    if (document.hidden || $('ndiAnBody').closest('[data-ndipane]').hidden) { schedule(); return; }   // only while looked at
    if (busy) { schedule(); return; }
    busy = true;
    try { draw(await send('GET', '/api/admin/ndi/analysis' + query())); showErr(''); }
    catch (e) { showErr(e.message); if (/not open/.test(e.message)) { open = null; $('ndiAnStop').hidden = true; return; } }
    finally { busy = false; }
    schedule();
  }
  $('ndiAnGo').onclick = begin;
  $('ndiAnStop').onclick = function () { end(false); };

  // ---- readouts
  function kv(box, rows) {
    box.textContent = '';
    rows.forEach(function (r) {
      const d = el('div'); d.appendChild(el('span', '', r[0]));
      d.appendChild(el('b', r[2] || '', r[1])); box.appendChild(d);
    });
  }
  const HEALTH = { ok: ['ok', 'Healthy'], warn: ['warn', 'Uneven'], bad: ['err', 'Trouble'], quiet: ['', 'Quiet'], unknown: ['', 'Measuring'] };
  function drawHealth(s) {
    const h = HEALTH[s.health] || HEALTH.unknown;
    $('ndiAnDot').className = 'dot ' + h[0]; $('ndiAnState').textContent = h[1]; $('ndiAnWhy').textContent = s.why || '';
    const box = $('ndiAnAlarms'); box.textContent = '';
    ((s.picture && s.picture.alarms) || []).concat((s.audio && s.audio.alarms) || []).forEach(function (a) {
      box.appendChild(el('span', 'badge ' + (a[1] === 'bad' ? 'err' : 'warn'), a[0]));
    });
    if (s.dropped) box.appendChild(el('span', 'badge err', 'DROPS ' + s.dropped));
    const y = s.sync;
    if (y && y.valid && !y.synthesized && (y.state === 'warn' || y.state === 'bad'))
      box.appendChild(el('span', 'badge ' + (y.state === 'bad' ? 'err' : 'warn'), 'A/V SYNC ' + (y.offset_ms > 0 ? '+' : '') + y.offset_ms + ' ms'));
  }
  function syncRow(y) {
    if (!y || !y.valid) return ['A/V sync', 'Measuring'];
    if (y.synthesized) return ['A/V sync', 'Not told by this sender'];
    return ['A/V sync', (y.offset_ms > 0 ? '+' : '') + y.offset_ms + ' ms ' + (y.offset_ms > 0 ? '(audio ahead)' : y.offset_ms < 0 ? '(audio behind)' : ''), y.state === 'bad' ? 'bad' : y.state === 'warn' ? 'warn' : ''];
  }
  function drawInfo(s) {
    const i = s.info || {}, f = i.format || {};
    const caps = [i.ptz ? 'PTZ' : '', i.recordable ? 'Recordable' : '', i.kvm ? 'KVM' : ''].filter(Boolean).join(', ');
    kv($('ndiAnInfo'), [
      ['Type', i.type + (s.low ? ' (low bandwidth)' : '')],
      ['Host', i.host ? i.host + (i.port ? ':' + i.port : '') : '—'], ['Group', (i.groups || []).join(', ') || '—'],
      ['Picture', f.width ? f.width + ' x ' + f.height + ', ' + fmt(f.fps, ' fps') : '—'],
      ['Sound', f.audio_rate ? f.audio_rate / 1000 + ' kHz, ' + f.audio_channels + ' ch' : 'none yet'], syncRow(s.sync),
      ['Sender', [i.name, i.model, i.version].filter(Boolean).join(' · ') || '—'], ['Capabilities', caps || '—']]);
  }
  function drawTiming(s) {
    const t = s.timing || {}, iv = t.interval_ms, j = t.jitter_ms, b = t.frame_bytes;
    const off = t.declared_fps && t.measured_fps ? Math.abs(t.measured_fps - t.declared_fps) / t.declared_fps : 0;
    kv($('ndiAnTiming'), [
      ['Frames', t.frames], ['Frame rate', fmt(t.measured_fps, ' fps', 2) + (t.declared_fps ? ' of ' + t.declared_fps : ''), off > 0.01 ? 'bad' : off > 0.002 ? 'warn' : ''],
      ['Interval min / avg / max', iv ? iv.min + ' / ' + iv.avg + ' / ' + iv.max + ' ms' : '—'], ['Interval deviation', iv ? iv.sd + ' ms' : '—'],
      ['Jitter mean / peak', j ? j.mean + ' / ' + j.peak + ' ms' : '—'],
      ['Late frames', t.late, t.recent_late >= 3 ? 'bad' : t.recent_late ? 'warn' : ''], ['Gaps over 150 ms', t.long_gaps, t.long_gaps ? 'warn' : ''],
      ['Data rate', fmt(t.rate_mbps, ' Mbit/s')], ['Frame size min / avg / max', b ? [b.min, b.avg, b.max].map(kb).join(' / ') : '—']]);
    const rec = s.recording;
    $('ndiAnRec').hidden = !!(rec && rec.active); $('ndiAnRecStop').hidden = !(rec && rec.active);
    $('ndiAnDl').hidden = !(rec || last_rec);
    if (rec) last_rec = true;
    $('ndiAnRecState').textContent = rec ? (rec.active ? 'Recording ' + rec.elapsed + ' of ' + rec.seconds + ' s, ' + rec.rows + ' frames' : 'Saved ' + rec.rows + ' frames') : '';
  }
  let last_rec = false;
  function kb(n) { return n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.round(n / 1024) + ' KB'; }

  $('ndiAnRec').onclick = async function () {
    showErr('');
    try { await send('POST', '/api/admin/ndi/analysis/record', { source: open.source, low: open.low, seconds: Number($('ndiAnSecs').value) || 60 }); } catch (e) { showErr(e.message); }
  };
  $('ndiAnRecStop').onclick = async function () { try { await send('POST', '/api/admin/ndi/analysis/record/stop', { source: open.source, low: open.low }); } catch (e) { showErr(e.message); } };
  $('ndiAnDl').onclick = function () { if (open) window.location.href = '/api/admin/ndi/analysis/recording' + query(); };
  $('ndiAnReset').onclick = async function () { try { await send('POST', '/api/admin/ndi/analysis/reset', { source: open.source, low: open.low }); } catch (e) { showErr(e.message); } };

  // ---- scopes
  $('ndiAnScopeTabs').addEventListener('click', function (e) {
    const b = e.target.closest('[data-scope]'); if (!b) return;
    scope = b.dataset.scope;
    $('ndiAnScopeTabs').querySelectorAll('[data-scope]').forEach(function (x) { x.setAttribute('aria-selected', String(x === b)); });
    if (last) drawPicture(last);
  });
  function bytes(p) { const raw = atob(p.b64), a = new Uint8Array(raw.length); for (let i = 0; i < raw.length; i++) a[i] = raw.charCodeAt(i); return a; }
  function paint(cv, p, tint, x, w) {          // a packed density grid drawn into the canvas rectangle
    const ctx = cv.getContext('2d'), a = bytes(p), img = ctx.createImageData(p.w, p.h);
    for (let i = 0; i < a.length; i++) {
      const v = a[i]; img.data[i * 4] = tint[0] * v / 255; img.data[i * 4 + 1] = tint[1] * v / 255; img.data[i * 4 + 2] = tint[2] * v / 255; img.data[i * 4 + 3] = 255;
    }
    const tmp = document.createElement('canvas'); tmp.width = p.w; tmp.height = p.h; tmp.getContext('2d').putImageData(img, 0, 0);
    ctx.imageSmoothingEnabled = false; ctx.drawImage(tmp, x, 0, w, cv.height);
  }
  function lines(cv, levels, fromX, toX) {     // the 0, 16, 128, 235 and 255 code lines; the legal rails are 16 and 235
    const ctx = cv.getContext('2d'); ctx.lineWidth = 1; ctx.fillStyle = color('--muted'); ctx.font = '10px ' + getComputedStyle(document.body).fontFamily;
    levels.forEach(function (c) {
      const y = Math.round((255 - c) / 255 * (cv.height - 1)) + 0.5;
      ctx.strokeStyle = (c === 16 || c === 235) ? color('--warn') : color('--border');
      ctx.beginPath(); ctx.moveTo(fromX, y); ctx.lineTo(toX, y); ctx.stroke(); ctx.fillText(String(c), fromX + 2, Math.max(10, y - 2));
    });
  }
  function drawPicture(s) {
    const cv = $('ndiAnCanvas'), ctx = cv.getContext('2d'), pic = s.picture;
    ctx.clearRect(0, 0, cv.width, cv.height);
    $('ndiAnPicNote').textContent = pic ? '' : 'No picture yet, or a pixel format the scopes do not read.';
    if (!pic) { $('ndiAnRead').textContent = ''; return; }
    const green = [80, 255, 120];
    if (scope === 'waveform') { paint(cv, pic.waveform, green, 0, cv.width); lines(cv, [0, 16, 128, 235, 255], 0, cv.width); }
    else if (scope === 'parade') {
      const w = cv.width / 3, tints = [[255, 70, 70], [70, 255, 70], [90, 120, 255]];
      pic.parade.forEach(function (g, i) { paint(cv, g, tints[i], i * w, w); });
      lines(cv, [0, 16, 128, 235, 255], 0, cv.width);
    } else if (scope === 'vectorscope') {
      const n = Math.min(cv.width, cv.height), ox = (cv.width - n) / 2, tmp = document.createElement('canvas'); tmp.width = tmp.height = n;
      paint(tmp, pic.vectorscope, green, 0, n); ctx.drawImage(tmp, ox, 0);
      ctx.strokeStyle = color('--border'); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(cv.width / 2, cv.height / 2, n / 2 - 1, 0, 7); ctx.moveTo(ox, cv.height / 2); ctx.lineTo(ox + n, cv.height / 2);
      ctx.moveTo(cv.width / 2, 0); ctx.lineTo(cv.width / 2, cv.height); ctx.stroke();
      // 75% Rec.709 bars: Cb = 128 + 224 (B' - Y') / 1.8556 and Cr = 128 + 224 (R' - Y') / 1.5748, each primary at 0.75
      const bars = [['R', 0.75, 0, 0], ['MG', 0.75, 0, 0.75], ['B', 0, 0, 0.75], ['CY', 0, 0.75, 0.75], ['G', 0, 0.75, 0], ['YL', 0.75, 0.75, 0]];
      ctx.fillStyle = color('--muted'); ctx.strokeStyle = color('--muted'); ctx.font = '10px ' + getComputedStyle(document.body).fontFamily;
      bars.forEach(function (c) {
        const y = 0.2126 * c[1] + 0.7152 * c[2] + 0.0722 * c[3], cb = 128 + 224 * (c[3] - y) / 1.8556, cr = 128 + 224 * (c[1] - y) / 1.5748;
        const x = ox + cb / 255 * n, yy = (255 - cr) / 255 * cv.height;
        ctx.strokeRect(x - 4, yy - 4, 8, 8); ctx.fillText(c[0], x + 6, yy - 6);
      });
    } else {
      const h = pic.histogram, max = Math.log1p(Math.max.apply(null, h)) || 1, bw = cv.width / 256;
      h.forEach(function (v, i) {
        const bh = Math.log1p(v) / max * (cv.height - 14);
        ctx.fillStyle = (i < 16 || i > 235) ? color('--err') : color('--ok'); ctx.fillRect(i * bw, cv.height - 14 - bh, Math.ceil(bw), bh);
      });
      ctx.fillStyle = color('--muted'); ctx.font = '10px ' + getComputedStyle(document.body).fontFamily;
      [0, 16, 128, 235, 255].forEach(function (c) { ctx.fillText(String(c), Math.min(c * bw, cv.width - 20), cv.height - 2); });
    }
    const r = pic.read;
    kv($('ndiAnRead'), [
      ['Luma min / max', r.luma_min + ' / ' + r.luma_max], ['Luma mean', r.luma_mean],
      ['Below black (< 16)', r.sub_black.toLocaleString(), r.sub_black ? 'warn' : ''], ['Above white (> 235)', r.super_white.toLocaleString(), r.super_white ? 'warn' : ''],
      ['Illegal RGB pixels', r.illegal_rgb.toLocaleString(), r.illegal_rgb ? 'warn' : ''], ['Change between frames', fmt(r.frame_diff)],
      ['Sharpness', r.sharpness], ['Letterbox top / bottom', r.letterbox.join(' / ') + ' rows'], ['Pillarbox left / right', r.pillarbox.join(' / ') + ' columns'],
      ['Aspect', r.aspect], ['Colour matrix', r.matrix + ' (guessed from the size)']]);
  }

  // ---- sound
  function barPct(db) { return Math.max(0, Math.min(100, (db + 60) / 60 * 100)); }      // 0 to -60 dBFS
  function drawSound(s) {
    const a = s.audio; $('ndiAnNoSound').hidden = !!a; $('ndiAnSoundBody').hidden = !a;
    if (!a) return;
    const box = $('ndiAnMeters');
    if (box.children.length !== a.channels.length) {
      box.textContent = '';
      a.channels.forEach(function (c, i) {
        const row = el('div', 'ndimeter'), m = el('div', 'meter');
        m.appendChild(el('i')); m.appendChild(el('u'));
        row.appendChild(el('b', '', 'Ch ' + (i + 1))); row.appendChild(m); row.appendChild(el('span', 'read'));
        box.appendChild(row);
      });
    }
    a.channels.forEach(function (c, i) {
      const row = box.children[i], fill = row.querySelector('i'), tick = row.querySelector('u');
      fill.style.width = barPct(c.rms) + '%';
      fill.className = c.rms > -6 ? 'red' : c.rms > -18 ? 'amber' : '';
      tick.style.left = barPct(c.hold) + '%';
      row.querySelector('.read').textContent = c.mute ? 'MUTE' : 'peak ' + (c.peak <= -120 ? '—' : c.peak) + ' · true ' + (c.true_peak <= -120 ? '—' : c.true_peak) + ' dB';
      row.firstChild.className = Math.abs(c.dc) > 0.01 ? 'warn' : '';
      row.firstChild.title = Math.abs(c.dc) > 0.01 ? 'DC offset ' + c.dc : '';
    });
    const near = function (v) { return v === null ? '' : Math.abs(v + 23) <= 1 ? '' : Math.abs(v + 23) <= 3 ? 'warn' : 'bad'; };
    kv($('ndiAnLoud'), [
      ['Momentary (400 ms)', fmt(a.momentary, ' LUFS'), near(a.momentary)], ['Short-term (3 s)', fmt(a.short, ' LUFS'), near(a.short)],
      ['Integrated', fmt(a.integrated, ' LUFS'), near(a.integrated)], ['Loudness range', fmt(a.lra, ' LU')],
      ['Clips', a.clips, a.clip_now ? 'bad' : a.clips ? 'warn' : ''],
      ['Correlation', a.correlation === null ? '—' : a.correlation + (a.correlation < -0.2 ? ' OUT OF PHASE' : a.correlation > 0.98 ? ' MONO' : ''), a.correlation !== null && a.correlation < -0.2 ? 'bad' : '']]);
    drawSpectrum(a.spectrum, a.rate);
  }
  function drawSpectrum(sp, rate) {                    // 20 Hz to 22 kHz on a log axis, 0 to -100 dB
    const cv = $('ndiAnSpec'), ctx = cv.getContext('2d'); ctx.clearRect(0, 0, cv.width, cv.height);
    if (!sp || !sp.length) return;
    const top = Math.min(rate / 2, 22000), lo = Math.log10(20), hi = Math.log10(top), cols = new Array(cv.width).fill(-100);
    sp.forEach(function (db, i) {
      const f = (i + 1) * rate / 1024; if (f < 20 || f > top) return;
      const x = Math.min(cv.width - 1, Math.floor((Math.log10(f) - lo) / (hi - lo) * cv.width)); cols[x] = Math.max(cols[x], db);
    });
    ctx.strokeStyle = color('--border'); ctx.fillStyle = color('--muted'); ctx.font = '10px ' + getComputedStyle(document.body).fontFamily;
    [100, 1000, 10000].forEach(function (f) { const x = (Math.log10(f) - lo) / (hi - lo) * cv.width; ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, cv.height); ctx.stroke(); ctx.fillText(f >= 1000 ? f / 1000 + 'k' : String(f), x + 2, cv.height - 2); });
    ctx.fillStyle = color('--ok');
    cols.forEach(function (db, x) { const h = Math.max(0, (db + 100) / 100 * cv.height); ctx.fillRect(x, cv.height - h, 1, h); });
  }

  function draw(s) {
    if (!s || !s.live) { showErr('That source is not being received.'); return; }
    last = s; drawHealth(s); drawInfo(s); drawTiming(s); drawPicture(s); drawSound(s);
  }

  // the NDI page hands its source list over (see ndi.js) and tells us when to look
  window.addEventListener('relay-ndi-state', fillSources);
  fillSources();
})();
