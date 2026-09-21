/* Operator panel: device + key + target toggles + live health. */
(function () {
  const $ = function (id) { return document.getElementById(id); };
  let devices = [];
  let cfg = null;
  let lastTargetsKey = '';

  function alertMsg(msg) {
    const el = $('alert');
    el.className = 'field err';
    el.textContent = msg || '';
    el.hidden = !msg;
  }

  let okTimer = null;
  function okMsg(msg) {
    const el = $('alert');
    el.className = 'field ok';
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(okTimer);
    okTimer = setTimeout(function () { el.hidden = true; }, 2600);
  }

  async function api(path, opts) {
    const res = await fetch(path, Object.assign({ cache: 'no-store' }, opts || {}));
    const body = await res.json().catch(function () { return {}; });
    if (!res.ok) throw new Error(body.error || body.detail || ('HTTP ' + res.status));
    return body;
  }

  function post(path, payload) {
    return api(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {})
    });
  }

  /* ---- devices ---- */
  function renderDevices(list, selected) {
    devices = list;
    const sel = $('device');
    sel.innerHTML = '';
    if (!list.length) {
      const o = document.createElement('option');
      o.textContent = 'No input devices found';
      sel.appendChild(o);
      sel.disabled = true;
      return;
    }
    sel.disabled = false;
    list.forEach(function (d) {
      const o = document.createElement('option');
      o.value = d.label;
      o.textContent = d.label + '  (' + d.channels + ' ch, ' + d.default_samplerate + ' Hz)';
      sel.appendChild(o);
    });
    if (selected) sel.value = selected;
    if (!sel.value) sel.selectedIndex = 0;
    renderChannels();
  }

  function renderChannels() {
    const dev = devices.filter(function (d) { return d.label === $('device').value; })[0];
    const sel = $('channel');
    const want = cfg ? cfg.input_channel : 0;
    sel.innerHTML = '';
    const n = dev ? dev.channels : 1;
    for (let i = 0; i < n; i++) {
      const o = document.createElement('option');
      o.value = String(i);
      o.textContent = 'Channel ' + (i + 1);
      sel.appendChild(o);
    }
    // A stereo board feed is usually better summed than halved.
    if (n > 1) {
      const o = document.createElement('option');
      o.value = 'mix';
      o.textContent = 'Mix (' + n + ' ch)';
      sel.appendChild(o);
    }
    sel.value = (want === 'mix' && n > 1)
      ? 'mix'
      : String(Math.min(parseInt(want, 10) || 0, n - 1));
  }

  $('device').addEventListener('change', function () {
    renderChannels();
    saveDevice();
  });
  $('channel').addEventListener('change', saveDevice);
  $('noise').addEventListener('change', saveDevice);

  async function saveDevice() {
    try {
      const body = await post('/api/admin/config', {
        audio_device: $('device').value,
        input_channel: $('channel').value === 'mix' ? 'mix' : (parseInt($('channel').value, 10) || 0),
        realtime: { noise_reduction: $('noise').value || null }
      });
      cfg = body.config;
      okMsg('Input saved.');
    } catch (e) { alertMsg(e.message); }
  }

  $('rescan').addEventListener('click', async function () {
    try {
      const body = await api('/api/admin/devices');
      renderDevices(body.devices, cfg && cfg.audio_device);
      if (body.note) alertMsg(body.note);
      else okMsg('Devices rescanned.');
    } catch (e) { alertMsg(e.message); }
  });

  /* ---- targets ---- */
  function targetState(t) {
    if (t.live) return 'live';
    return t.enabled ? 'armed — starts with capture' : 'off';
  }

  function renderTargets(targets) {
    const key = JSON.stringify(targets);
    if (key === lastTargetsKey) return;
    lastTargetsKey = key;

    const tb = $('targets');
    tb.innerHTML = '';
    targets.forEach(function (t) {
      const tr = document.createElement('tr');

      const nameTd = document.createElement('td');
      nameTd.innerHTML = '<strong>' + t.language_label + '</strong>';
      tr.appendChild(nameTd);

      const stateTd = document.createElement('td');
      stateTd.className = 'muted';
      stateTd.id = 'tstate-' + t.target;
      stateTd.textContent = targetState(t);
      tr.appendChild(stateTd);

      const toggleTd = document.createElement('td');
      toggleTd.style.width = '52px';
      const sw = document.createElement('label');
      sw.className = 'switch';
      sw.innerHTML = '<input type="checkbox"' + (t.enabled ? ' checked' : '') + '><span class="slider"></span>';
      sw.querySelector('input').addEventListener('change', function (ev) {
        setTarget(t.target, ev.target.checked);
      });
      toggleTd.appendChild(sw);
      tr.appendChild(toggleTd);

      tb.appendChild(tr);
    });
  }

  async function setTarget(target, enabled) {
    try {
      lastTargetsKey = '';
      const status = await post('/api/admin/target/' + target, { enabled: enabled });
      applyStatus(status);
      alertMsg('');
    } catch (e) { alertMsg(e.message); }
  }

  /* ---- blocked words ---- */
  let blocklistShown = null;

  function renderBlocklist(terms, path) {
    if (path) $('blocklistPath').textContent = path;
    const joined = (terms || []).join('\n');
    $('blocklistCount').textContent = terms.length
      ? terms.length + (terms.length === 1 ? ' term active' : ' terms active')
      : 'none';
    // Never overwrite what the operator is in the middle of typing; otherwise
    // keep the box in step with edits made to the file directly.
    if (joined !== blocklistShown && document.activeElement !== $('blocklist')) {
      $('blocklist').value = joined;
      blocklistShown = joined;
    }
  }

  $('saveBlocklist').addEventListener('click', async function () {
    try {
      const body = await post('/api/admin/config', {
        blocklist: $('blocklist').value
      });
      blocklistShown = null;
      renderBlocklist(body.status.blocklist, body.status.blocklist_file);
      $('blocklist').blur();
      okMsg('Saved to file — live now.');
    } catch (e) { alertMsg(e.message); }
  });

  /* ---- credentials + appearance ---- */
  $('saveCreds').addEventListener('click', async function () {
    const payload = {};
    if ($('apikey').value.trim()) payload.openai_api_key = $('apikey').value.trim();
    if ($('admintoken').value.trim()) payload.admin_token = $('admintoken').value.trim();
    if (!Object.keys(payload).length) { alertMsg('Nothing to save.'); return; }
    try {
      const body = await post('/api/admin/config', payload);
      cfg = body.config;
      $('apikey').value = ''; $('admintoken').value = '';
      renderKeyState();
      okMsg('Saved.' + (payload.openai_api_key ? ' Restart capture to use the new key.' : ''));
    } catch (e) { alertMsg(e.message); }
  });

  $('saveAppearance').addEventListener('click', async function () {
    try {
      const body = await post('/api/admin/config', {
        default_font_px: parseInt($('font').value, 10),
        history_lines: parseInt($('history').value, 10),
        realtime: {
          segment_idle_s: parseFloat($('segIdle').value),
          segment_max_idle_s: parseFloat($('segMaxIdle').value)
        }
      });
      cfg = body.config;
      okMsg('Saved. Viewers pick up the new font size on reload; caption timing restarts live sessions.');
    } catch (e) { alertMsg(e.message); }
  });

  function renderKeyState() {
    $('keyState').textContent = cfg && cfg.openai_api_key_set
      ? '· set ' + cfg.openai_api_key_hint
      : '· not set';
    $('keyState').className = cfg && cfg.openai_api_key_set ? 'ok' : 'err';
  }

  /* ---- master ---- */
  $('startBtn').addEventListener('click', async function () {
    $('startBtn').disabled = true;
    try { applyStatus(await post('/api/admin/start')); alertMsg(''); }
    catch (e) { alertMsg(e.message); $('startBtn').disabled = false; }
  });
  $('stopBtn').addEventListener('click', async function () {
    $('stopBtn').disabled = true;
    try { applyStatus(await post('/api/admin/stop')); alertMsg(''); }
    catch (e) { alertMsg(e.message); }
  });

  /* ---- status rendering ---- */
  function ago(ts) {
    if (!ts) return '—';
    const d = Date.now() / 1000 - ts;
    if (d < 1) return 'now';
    if (d < 60) return d.toFixed(1) + 's ago';
    return Math.floor(d / 60) + 'm ago';
  }

  function stateClass(s) {
    if (s === 'connected') return 'ok';
    if (s === 'error') return 'err';
    if (s === 'reconnecting' || s === 'connecting') return 'warn';
    return '';
  }

  // dBFS meter. -60 dBFS is the left edge, 0 the right; the bar is RMS and the
  // thin marker is the decaying peak, so short transients stay readable at the
  // 1 Hz poll rate. Fed by both the meter frame and full status, which carry
  // the same field names.
  const DB_FLOOR = -60;

  function dbPct(db) {
    if (db === null || db === undefined) return 0;
    return Math.max(0, Math.min(100, ((db - DB_FLOOR) / -DB_FLOOR) * 100));
  }

  function fmtDb(db) {
    if (db === null || db === undefined || db <= DB_FLOOR) return '-\u221e';
    return (db > 0 ? '+' : '') + db.toFixed(1);
  }

  // Clipped-sample counts run to six figures in seconds; an unbounded number
  // would widen the readout and shove the bar around under it.
  function fmtCount(n) {
    if (n < 10000) return String(n);
    if (n < 1000000) return Math.round(n / 1000) + 'k';
    return (n / 1000000).toFixed(1) + 'M';
  }

  function paintMeter(a) {
    const rms = (a.rms_dbfs === undefined || a.rms_dbfs === null) ? DB_FLOOR : a.rms_dbfs;
    const peak = (a.peak_dbfs === undefined || a.peak_dbfs === null) ? DB_FLOOR : a.peak_dbfs;
    const clipping = !!a.clipping;
    $('meter').firstElementChild.style.width = dbPct(rms) + '%';
    const marker = $('meterPeak');
    marker.style.left = dbPct(peak) + '%';
    marker.style.opacity = peak <= DB_FLOOR ? '0' : '1';
    // Amber from -6 dBFS: headroom is gone before the rail is reached.
    $('meter').className = 'meter' + (clipping ? ' clipping' : (peak > -6 ? ' hot' : ''));
    $('clipLed').className = 'clip' + (clipping ? ' on' : '');
    const dropped = a.clipped_samples || 0;
    const read = $('meterRead');
    read.textContent = peak <= DB_FLOOR && rms <= DB_FLOOR
      ? '—'
      : fmtDb(rms) + ' · pk ' + fmtDb(peak)
        + (dropped ? ' · ' + fmtCount(dropped) + ' clipped' : '');
    read.className = 'meterread' + (dropped ? ' clipped' : '');
  }

  // The 1 Hz tick sends a small meter frame; full status arrives only on a real
  // change. Update the live bits in place and leave the table to full status.
  function applyMeter(st) {
    $('masterDot').className = 'dot ' + (st.running ? 'ok' : '');
    $('masterState').textContent = st.running ? 'Running' : 'Stopped';
    $('startBtn').disabled = st.running;
    $('stopBtn').disabled = !st.running;
    if (st.running) $('audioDot').className = 'dot ' + (st.speaking ? 'ok' : 'warn');
    paintMeter(st);
  }

  function applyStatus(st) {
    if (!st) return;
    if (st.type === 'meter') { applyMeter(st); return; }
    if (!st.sessions) return;

    $('masterDot').className = 'dot ' + (st.running ? 'ok' : '');
    $('masterState').textContent = st.running ? 'Running' : 'Stopped';
    $('startBtn').disabled = st.running;
    $('stopBtn').disabled = !st.running;
    $('viewerCount').textContent = st.viewers + (st.viewers === 1 ? ' viewer' : ' viewers');

    const a = st.audio || {};
    $('audioDot').className = 'dot ' + (a.error ? 'err' : a.running ? (a.speaking ? 'ok' : 'warn') : '');
    $('audioState').textContent = a.error
      ? 'Error'
      : a.running
        ? (a.speaking ? 'Speaking' : 'Signal quiet') + ' · ' + a.native_rate + ' Hz → ' + a.target_rate + ' Hz'
        : 'Idle';
    paintMeter(a);
    const drops = a.dropped_capture || 0;
    const ch = a.channel === 'mix'
      ? 'mix'
      : (a.channel === undefined || a.channel === null ? '' : 'ch ' + (a.channel + 1));
    $('audioNote').textContent = a.error
      || ((a.device ? a.device : '')
          + (a.device && ch ? '  ·  ' + ch : '')
          + (drops ? '  ·  ' + drops + ' capture frame(s) dropped' : ''));
    $('audioNote').className = 'note' + (a.error || drops ? ' err' : '');

    if (st.blocklist) renderBlocklist(st.blocklist, st.blocklist_file);
    if (st.targets) renderTargets(st.targets);
    st.targets && st.targets.forEach(function (t) {
      const el = document.getElementById('tstate-' + t.target);
      if (el) el.textContent = targetState(t);
    });

    const tb = $('sessions');
    tb.innerHTML = '';
    if (!st.sessions.length) {
      tb.innerHTML = '<tr><td colspan="5" class="muted">Not started.</td></tr>';
    } else {
      st.sessions.forEach(function (s) {
        const dropped = s.dropped_audio || 0;
        const tr = document.createElement('tr');
        tr.innerHTML =
          '<td>' + s.name + '</td>' +
          '<td class="' + stateClass(s.state) + '">' + s.state +
            (s.error ? ' <span class="muted mono">' + s.error.slice(0, 90) + '</span>' : '') + '</td>' +
          '<td class="muted">' + ago(s.last_delta_ts) + '</td>' +
          '<td class="muted">' + s.reconnects + '</td>' +
          '<td class="' + (dropped ? 'err' : 'muted') + '">' + dropped + '</td>';
        tb.appendChild(tr);
      });
    }
    if (st.error) alertMsg(st.error);
  }

  /* ---- viewer links: one row per view style, all host origins on that row ----
     Grouped: audience-facing views the room opens directly, and the two setup
     feeds that get pasted into another application (a kiosk browser, a switcher's
     browser source). Neither setup feed is on the chooser page. */
  const VIEW_GROUPS = [
    ['For the room', [
      ['Translation', 'translation'],
      ['Transcription', 'transcription'],
      ['Both', 'both'],
      ['Chooser page', ''],
      // Defaults to translation + first live language; append ?stream=&lang= to
      // pin a specific room display.
      ['Screen (kiosk display)', 'screen'],
    ]],
    ['For your switcher', [
      // A browser source for OBS / ProPresenter, or a keyed output for an ATEM.
      // Transparent by default; see the /overlay route in app/main.py for the
      // ?bg=, ?matte= and sizing parameters.
      ['Overlay (OBS / ProPresenter)', 'overlay'],
    ]],
  ];

  function urlRow(label, path, hosts) {
    const links = hosts.map(function (u) {
      const href = u + path;
      // The destination for the setup feeds is another app's URL field, so a
      // copy button matters more than a clickable link.
      return '<span class="urllink">' +
             '<span class="urlrow">' +
               '<a href="' + href + '" target="_blank" rel="noopener">' + href + '</a>' +
               '<button type="button" class="urlcopy" data-copy="' + href + '" ' +
                 'aria-label="Copy link">Copy</button>' +
             '</span>' +
             '<span class="urlqr" data-href="' + href + '"></span></span>';
    }).join('');
    return '<div class="urlgroup"><span class="urllabel">' + label + '</span>' +
           '<span class="urllinks">' + links + '</span></div>';
  }

  function renderUrls(urls) {
    // Only the LAN address is worth handing to a room; 127.0.0.1 works only on
    // the host. Keep it as a fallback if the LAN address could not be resolved.
    let hosts = (urls || []).filter(function (u) { return u.indexOf('127.0.0.1') === -1; });
    if (!hosts.length) hosts = urls || [];
    $('urls').innerHTML = VIEW_GROUPS.map(function (group) {
      const rows = group[1].map(function (v) { return urlRow(v[0], v[1], hosts); }).join('');
      return '<div class="urlheading">' + group[0] + '</div>' + rows;
    }).join('');
    wireCopies();
    paintQrs();
  }

  /* Copy-to-clipboard for each link, with a brief "Copied" acknowledgement. */
  function wireCopies() {
    document.querySelectorAll('.urlcopy[data-copy]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        const text = btn.dataset.copy;
        const done = function () {
          const was = btn.textContent;
          btn.textContent = 'Copied';
          btn.classList.add('ok');
          setTimeout(function () { btn.textContent = was; btn.classList.remove('ok'); }, 1200);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text, done); });
        } else {
          fallbackCopy(text, done);
        }
      });
    });
  }

  function fallbackCopy(text, done) {
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      done();
    } catch (e) { /* clipboard unavailable -- the link text is still selectable */ }
  }

  /* Each link gets a small QR so the operator can hold the panel up to scan or
     put it on a slide. Generated locally -- no third-party image service. */
  function paintQrs() {
    if (typeof qrcode !== 'function') return;
    document.querySelectorAll('.urlqr[data-href]').forEach(function (box) {
      if (box.dataset.done) return;
      try {
        const qr = qrcode(0, 'M');
        qr.addData(box.dataset.href);
        qr.make();
        box.innerHTML = qr.createSvgTag({ cellSize: 2, margin: 1, scalable: true });
        box.dataset.done = '1';
      } catch (e) { /* leave the link text as the fallback */ }
    });
  }

  /* ---- collapsible sections: remember open/closed across reloads ---- */
  function restoreSections() {
    document.querySelectorAll('details.sect[id]').forEach(function (d) {
      let saved = null;
      try { saved = localStorage.getItem('relay.section.' + d.id); } catch (e) {}
      if (saved !== null) d.open = saved === '1';
      d.addEventListener('toggle', function () {
        try { localStorage.setItem('relay.section.' + d.id, d.open ? '1' : '0'); } catch (e) {}
      });
    });
  }

  /* ---- boot ---- */
  (async function boot() {
    restoreSections();
    try {
      const state = await api('/api/admin/state');
      cfg = state.config;
      renderDevices(state.devices, cfg.audio_device);
      renderKeyState();
      $('font').value = cfg.default_font_px;
      $('history').value = cfg.history_lines;
      $('segIdle').value = cfg.realtime.segment_idle_s;
      $('segMaxIdle').value = cfg.realtime.segment_max_idle_s;
      $('noise').value = cfg.realtime.noise_reduction || '';
      renderUrls(state.urls);
      applyStatus(state.status);
    } catch (e) { alertMsg(e.message); }

    const es = new EventSource('/api/admin/status/stream');
    es.onmessage = function (ev) {
      try { applyStatus(JSON.parse(ev.data)); } catch (e) {}
    };
  })();
})();
