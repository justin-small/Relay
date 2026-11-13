/* Operator panel: device + key + target toggles + live health. */
(function () {
  /* A page the signed-in role may not see is not in the document at all (the server leaves it out), but the code
     that feeds every page still runs. For an element that is not there it hands back a detached stand-in, so
     nothing throws and nothing shows. */
  const $ = function (id) {
    return document.getElementById(id) || (function () {
      const n = document.createElement('div');
      n.innerHTML = '<i></i>';
      return n;
    })();
  };
  const can = function (perm) { return window.relayCan(perm); };
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

  /* The session ended (signed out elsewhere, turned off, or a password change is waiting): the sign-in page
     says what to do next. Returns true when it has sent the browser there. */
  function sessionEnded(res, body) {
    if (res.status === 401 || (res.status === 403 && body && body.detail === 'password_change_required')) {
      window.location.href = '/admin';
      return true;
    }
    return false;
  }

  async function api(path, opts) {
    const res = await fetch(path, Object.assign({ cache: 'no-store' }, opts || {}));
    const body = await res.json().catch(function () { return {}; });
    if (!res.ok) {
      sessionEnded(res, body);
      throw new Error(body.error || body.detail || ('HTTP ' + res.status));
    }
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
  function renderDevices(list, selected, notice) {
    devices = list;
    const nb = $('deviceNotice');
    nb.textContent = notice || '';
    nb.hidden = !notice;
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
      o.textContent = d.label + '  (' + d.channels + ' ch, ' + d.default_samplerate + ' Hz'
        + (d.default ? ', system default' : '') + ')';
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

  function renderMode() {
    const text = cfg.realtime.translation_mode === 'text';
    $('xmode').value = text ? 'text' : 'realtime';
    $('targetsNote').textContent = text
      ? 'Every live target is translated from the one transcription session. Turning one off takes effect from the next line.'
      : 'Each live target is a separate, continuously-billed session. Turning one off closes it immediately.';
  }

  $('xmode').addEventListener('change', async function () {
    try {
      const body = await post('/api/admin/config', { realtime: { translation_mode: $('xmode').value } });
      cfg = body.config;
      renderMode();
      lastTargetsKey = '';
      renderTargets(lastTargets);
      okMsg('Translation engine saved.' + (body.status && body.status.running ? ' Live sessions restarted.' : ''));
    } catch (e) { alertMsg(e.message); renderMode(); }
  });

  async function saveDevice() {
    try {
      const payload = {
        audio_device: $('device').value,
        input_channel: $('channel').value === 'mix' ? 'mix' : (parseInt($('channel').value, 10) || 0)
      };
      // Noise reduction is a realtime setting: a role that cannot change those must not send it (the whole
      // request would be refused).
      if (can('settings.manage')) payload.realtime = { noise_reduction: $('noise').value || null };
      const body = await post('/api/admin/config', payload);
      cfg = body.config;
      okMsg('Input saved.');
    } catch (e) { alertMsg(e.message); }
  }

  $('rescan').addEventListener('click', async function () {
    try {
      const body = await api('/api/admin/devices');
      renderDevices(body.devices, cfg && cfg.audio_device, body.device_notice);
      if (body.note) alertMsg(body.note);
      else okMsg('Devices rescanned.');
    } catch (e) { alertMsg(e.message); }
  });

  /* ---- targets ---- */
  function targetState(t) {
    if (t.live) return 'live';
    if (t.text_only && cfg && cfg.realtime.translation_mode !== 'text') {
      return t.enabled ? 'needs the Text engine' : 'Text engine only';
    }
    return t.enabled ? 'armed — starts with capture' : 'off';
  }

  /* The heading line: which targets are live or armed, so the operator can
     confirm them with the section collapsed. */
  function renderTargetsSummary(targets) {
    const names = function (list) {
      return list.map(function (t) { return t.language_label; }).join(', ');
    };
    const live = targets.filter(function (t) { return t.live; });
    const armed = targets.filter(function (t) { return !t.live && t.enabled; });
    const el = $('targetsSum');
    el.textContent = '· ' + (live.length || armed.length ? '' : 'none on');
    const add = function (text, cls) {
      const s = document.createElement('span');
      if (cls) s.className = cls;
      s.textContent = text;
      el.appendChild(s);
    };
    if (live.length) add(names(live) + ' live', 'live');
    if (live.length && armed.length) add(' · ');
    if (armed.length) add(names(armed) + ' armed');
  }

  let lastTargets = [];

  function renderTargets(targets) {
    lastTargets = targets;
    renderTargetsSummary(targets);
    feedTargets(targets);
    const on = targets.filter(function (t) { return t.enabled; }).length;
    $('targetsCount').textContent = on + ' enabled';
    const key = JSON.stringify(targets) + (cfg && cfg.realtime.translation_mode);
    if (key === lastTargetsKey) return;
    lastTargetsKey = key;

    const grid = $('targets');
    grid.innerHTML = '';
    const text = cfg && cfg.realtime.translation_mode === 'text';
    targets.forEach(function (t) {
      const needsText = t.text_only && !text;
      const lab = el('label', 'check' + (needsText && !t.enabled ? ' off' : ''));
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = !!t.enabled;
      box.disabled = needsText && !t.enabled;   // Text-only languages need the Text engine
      box.addEventListener('change', function (ev) { setTarget(t.target, ev.target.checked); });
      lab.appendChild(box);
      lab.appendChild(el('span', '', t.language_label));
      const st = el('span', 'tstate', targetState(t));
      st.id = 'tstate-' + t.target;
      lab.appendChild(st);
      grid.appendChild(lab);
    });
  }

  /* Select all / Clear apply one language at a time, the same call each tick box makes. */
  async function setAllTargets(enabled) {
    const text = cfg && cfg.realtime.translation_mode === 'text';
    const todo = lastTargets.filter(function (t) {
      return t.enabled !== enabled && (!enabled || text || !t.text_only);
    });
    if (!todo.length) return;
    if (enabled && !text && !window.confirm('Turn on ' + todo.length + ' languages? Each is a separate, continuously-billed session while capture runs.')) return;
    for (let i = 0; i < todo.length; i++) await setTarget(todo[i].target, enabled);
  }
  $('targetsAll').addEventListener('click', function () { setAllTargets(true); });
  $('targetsNone').addEventListener('click', function () { setAllTargets(false); });

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

  function renderBlocklist(terms, path, max) {
    if (path) $('blocklistPath').textContent = path;
    const joined = (terms || []).join('\n');
    let count = terms.length
      ? terms.length + (terms.length === 1 ? ' term active' : ' terms active')
      : 'none';
    if (max && terms.length >= max) count += ' (limit reached)';
    $('blocklistCount').textContent = count;
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
      renderBlocklist(body.status.blocklist, body.status.blocklist_file, body.status.blocklist_max);
      $('blocklist').blur();
      const rep = body.blocklist_report || {};
      if (rep.dropped) {
        // Not a failure -- the list is live -- but terms were left out, so
        // this must stay on screen rather than fade like a normal save.
        alertMsg('Saved, but the list is capped at ' + rep.max_terms + ' terms: ' +
          rep.dropped + (rep.dropped === 1 ? ' term was' : ' terms were') +
          ' left out. Remove some to make room.');
      } else if (rep.duplicates) {
        okMsg('Saved — ' + rep.duplicates +
          (rep.duplicates === 1 ? ' duplicate' : ' duplicates') + ' removed. Live now.');
      } else {
        okMsg('Saved to file — live now.');
      }
    } catch (e) { alertMsg(e.message); }
  });

  /* ---- credentials + appearance ---- */
  $('saveCreds').addEventListener('click', async function () {
    const payload = {};
    if ($('apikey').value.trim()) payload.openai_api_key = $('apikey').value.trim();
    if (!Object.keys(payload).length) { alertMsg('Nothing to save.'); return; }
    try {
      const body = await post('/api/admin/config', payload);
      cfg = body.config;
      $('apikey').value = '';
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

  /* ---- timer settings: minutes:seconds fields and a timer picker, for a panel's timers (and the ProPresenter
     module's page, through RelayAdmin) ---- */
  function clockText(sec) {
    sec = Math.max(0, Math.round(sec));
    return Math.floor(sec / 60) + ':' + String(sec % 60).padStart(2, '0');
  }

  function clockSeconds(text) {
    const m = /^\s*(?:(\d{1,4}):)?(\d{1,5})\s*$/.exec(String(text));
    if (!m) return null;
    const sec = m[1] === undefined ? Number(m[2]) : Number(m[1]) * 60 + Number(m[2]);
    return sec <= 86399 ? sec : null;
  }

  /* Fill `box` with a tick box per timer. `timers` is ProPresenter's list ({id: {uuid, name}}), `chosen` the UUIDs
     ticked. A chosen timer ProPresenter no longer has stays listed, so saving does not drop it unseen. */
  function timerPicker(box, timers, chosen) {
    box.innerHTML = '';
    const seen = {};
    const add = function (uuid, name, gone) {
      seen[uuid] = true;
      const lab = el('label', 'check');
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.value = uuid;
      cb.checked = chosen.indexOf(uuid) !== -1;
      lab.appendChild(cb);
      lab.appendChild(el('span', '', name + (gone ? ' (not in ProPresenter now)' : '')));
      box.appendChild(lab);
    };
    (timers || []).forEach(function (t) { if (t.id && t.id.uuid && !seen[t.id.uuid]) add(t.id.uuid, t.id.name || 'Timer'); });
    chosen.forEach(function (u) { if (!seen[u]) add(u, u, true); });
    if (!box.firstChild) box.appendChild(el('div', 'muted', 'ProPresenter has no timers to list yet.'));
  }

  function timerPickerValue(box) {
    return Array.prototype.map.call(box.querySelectorAll('input:checked'), function (c) { return c.value; });
  }

  /* The thresholds from two inputs, or a sentence for what is wrong. */
  function timerThresholds(amberEl, redEl) {
    const amber = clockSeconds(amberEl.value), red = clockSeconds(redEl.value);
    if (amber === null) return { error: 'Amber is a time left, like 2:00 or 90.' };
    if (red === null) return { error: 'Red is a time left, like 0:30 or 30.' };
    if (red > amber) return { error: 'Red cannot be earlier than amber: it is the shorter time left.' };
    return { amber_s: amber, red_s: red };
  }

  /* ---- recordings ----
     A run is one start -> stop. The list is refreshed on load, after a stop
     (a finished event is the moment an operator wants the file) and on demand;
     it is deliberately not on the 1 Hz status tick, which exists to keep the
     meter live and should not be re-reading a directory. */
  let recRunning = false;

  function fmtDur(secs) {
    if (!secs || secs < 0) return '—';
    const h = Math.floor(secs / 3600), m = Math.floor(secs % 3600 / 60), s = Math.floor(secs % 60);
    return (h ? h + 'h ' : '') + (h || m ? m + 'm ' : '') + s + 's';
  }

  function fmtWhen(ts) {
    if (!ts) return '—';
    const d = new Date(ts * 1000);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
      + ' ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  }

  function recLink(runId, name, label) {
    const a = document.createElement('a');
    a.href = '/api/admin/recordings/' + encodeURIComponent(runId)
      + (name ? '/file/' + encodeURIComponent(name) : '/export.zip');
    a.textContent = label;
    a.download = '';
    a.style.marginRight = '10px';
    return a;
  }

  function renderRuns(info) {
    $('recDir').textContent = info.dir;
    const tb = $('recRuns');
    tb.innerHTML = '';
    if (!info.runs.length) {
      tb.innerHTML = '<tr><td colspan="5" class="muted">'
        + (info.enabled
            ? 'Nothing recorded yet — the next run will be saved.'
            : 'Recording is off, so no run has been saved.')
        + '</td></tr>';
      return;
    }
    info.runs.forEach(function (r) {
      const tr = document.createElement('tr');

      const when = document.createElement('td');
      when.innerHTML = '<strong>' + fmtWhen(r.started) + '</strong>'
        + (r.recording ? ' <span class="ok">· recording</span>'
           : r.interrupted ? ' <span class="warn">· cut short</span>' : '');
      tr.appendChild(when);

      const dur = document.createElement('td');
      dur.className = 'muted';
      dur.textContent = r.recording ? 'in progress' : fmtDur((r.ended || 0) - (r.started || 0));
      tr.appendChild(dur);

      const lines = document.createElement('td');
      lines.className = 'muted';
      lines.textContent = r.lines;
      tr.appendChild(lines);

      const dl = document.createElement('td');
      dl.appendChild(recLink(r.run_id, '', 'All (.zip)'));
      const src = document.createElement('a');
      src.href = '#';
      src.textContent = 'Files…';
      src.addEventListener('click', function (ev) {
        ev.preventDefault();
        showRunFiles(r.run_id, dl, src);
      });
      dl.appendChild(src);
      tr.appendChild(dl);

      const del = document.createElement('td');
      del.style.width = '1%';
      const btn = document.createElement('button');
      btn.className = 'danger';
      btn.textContent = 'Delete';
      btn.disabled = !!r.recording;
      btn.title = r.recording ? 'This run is still being recorded.' : '';
      btn.addEventListener('click', function () { deleteRun(r.run_id, r.started); });
      if (can('recordings.manage')) del.appendChild(btn);
      tr.appendChild(del);

      tb.appendChild(tr);
    });
  }

  async function showRunFiles(runId, cell, trigger) {
    try {
      const info = await api('/api/admin/recordings/' + encodeURIComponent(runId));
      trigger.remove();
      info.files.forEach(function (f) {
        if (f.name === 'manifest.txt') return;  // it ships inside the zip
        cell.appendChild(recLink(runId, f.name, f.name));
      });
      if (info.files.length <= 1) {
        const none = document.createElement('span');
        none.className = 'muted';
        none.textContent = 'no lines recorded';
        cell.appendChild(none);
      }
    } catch (e) { alertMsg(e.message); }
  }

  async function deleteRun(runId, started) {
    if (!window.confirm('Delete the recording from ' + fmtWhen(started)
        + '? The transcript cannot be recovered.')) return;
    try {
      await api('/api/admin/recordings/' + encodeURIComponent(runId), { method: 'DELETE' });
      okMsg('Recording deleted.');
      loadRuns();
    } catch (e) { alertMsg(e.message); }
  }

  async function loadRuns() {
    if (!can('recordings.view')) return;
    try {
      const info = await api('/api/admin/recordings');
      $('recEnabled').checked = !!info.enabled;
      $('recKeep').value = info.keep_runs;
      renderRuns(info);
    } catch (e) { /* the panel is still usable without the run list */ }
  }

  $('saveRecording').addEventListener('click', async function () {
    try {
      await post('/api/admin/config', {
        recording: {
          enabled: $('recEnabled').checked,
          keep_runs: parseInt($('recKeep').value, 10)
        }
      });
      loadRuns();
      okMsg($('recEnabled').checked
        ? 'Saved. The next run you start will be recorded.'
        : 'Saved. Recording is off; runs already on disk are kept.');
    } catch (e) { alertMsg(e.message); }
  });

  /* ---- schedules ----
     Stored ISO (YYYY-MM-DD) and 24-hour (HH:MM) on the server; shown and typed
     here in US formats only -- MM/DD/YYYY and h:mm AM/PM -- whatever locale the
     browser is set to. The server formats every instant it reports, in the
     schedule's own zone, so the panel never does time-zone maths itself. */
  const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  let schedTimezones = [];
  let schedDefaultTz = 'America/New_York';
  let schedEditing = null;   // id being edited, '' for a new one, null when closed
  let schedItems = [];
  let schedSig = '';
  let schedStatus = null;

  function usToIso(text) {
    const m = /^\s*(\d{1,2})\/(\d{1,2})\/(\d{4})\s*$/.exec(text || '');
    if (!m) return null;
    const mo = +m[1], d = +m[2], y = +m[3];
    const dt = new Date(Date.UTC(y, mo - 1, d));
    if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) return null;
    return y + '-' + String(mo).padStart(2, '0') + '-' + String(d).padStart(2, '0');
  }

  function isoToUs(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
    return m ? m[2] + '/' + m[3] + '/' + m[1] : '';
  }

  // Three selects per time: hour 1-12, minute in 5s, AM/PM. A minute that is
  // not a multiple of five (a hand-edited config) is added so it round-trips.
  function buildTimePicker(box) {
    const opts = function (list) {
      const sel = document.createElement('select');
      list.forEach(function (o) {
        const el = document.createElement('option');
        el.value = o[0]; el.textContent = o[1];
        sel.appendChild(el);
      });
      return sel;
    };
    const hours = [];
    for (let h = 1; h <= 12; h++) hours.push([String(h), String(h)]);
    const mins = [];
    for (let m = 0; m < 60; m += 5) mins.push([String(m).padStart(2, '0'), ':' + String(m).padStart(2, '0')]);
    const h = opts(hours), m = opts(mins), ap = opts([['AM', 'AM'], ['PM', 'PM']]);
    h.setAttribute('aria-label', 'Hour');
    m.setAttribute('aria-label', 'Minute');
    ap.setAttribute('aria-label', 'AM or PM');
    box.appendChild(h); box.appendChild(m); box.appendChild(ap);
    return {
      set: function (hhmm) {
        const parts = (hhmm || '10:00').split(':');
        const hh = parseInt(parts[0], 10), mm = parts[1];
        h.value = String((hh % 12) || 12);
        if (![].some.call(m.options, function (o) { return o.value === mm; })) {
          const el = document.createElement('option');
          el.value = mm; el.textContent = ':' + mm;
          m.appendChild(el);
        }
        m.value = mm;
        ap.value = hh < 12 ? 'AM' : 'PM';
      },
      get: function () {
        let hh = parseInt(h.value, 10) % 12;
        if (ap.value === 'PM') hh += 12;
        return String(hh).padStart(2, '0') + ':' + m.value;
      },
      onChange: function (fn) { [h, m, ap].forEach(function (el) { el.addEventListener('change', fn); }); }
    };
  }

  const startPicker = buildTimePicker($('schedStart'));
  const stopPicker = buildTimePicker($('schedStop'));

  function checkOvernight() {
    $('schedOvernight').hidden = !(stopPicker.get() < startPicker.get());
  }
  startPicker.onChange(checkOvernight);
  stopPicker.onChange(checkOvernight);

  // The day chips.
  DAY_NAMES.forEach(function (name, i) {
    const lab = document.createElement('label');
    lab.innerHTML = '<input type="checkbox" value="' + i + '"><span>' + name + '</span>';
    $('schedDays').appendChild(lab);
  });

  // The calendar button opens the browser's own date picker and writes the
  // choice back in US format.
  document.querySelectorAll('.datepick').forEach(function (btn) {
    const text = $(btn.dataset.for);
    const native = btn.parentNode.querySelector('.nativedate');
    btn.addEventListener('click', function () {
      native.value = usToIso(text.value) || '';
      try {
        if (native.showPicker) native.showPicker(); else native.click();
      } catch (e) { text.focus(); }
    });
    native.addEventListener('change', function () {
      if (native.value) text.value = isoToUs(native.value);
    });
    // Tidy 1/5/2027 into 01/05/2027 once the operator leaves the field.
    text.addEventListener('blur', function () {
      const iso = usToIso(text.value);
      if (iso) text.value = isoToUs(iso);
    });
  });

  function syncRepeat() {
    const once = $('schedRepeat').value === 'once';
    $('schedDaysField').hidden = once;
    $('schedEndDateField').hidden = once;
    $('schedStartDateLabel').textContent = once ? 'Date' : 'Starting on (optional)';
  }
  $('schedRepeat').addEventListener('change', syncRepeat);

  function renderTzOptions() {
    const sel = $('schedTz');
    sel.innerHTML = '';
    schedTimezones.forEach(function (z) {
      const o = document.createElement('option');
      o.value = z.id; o.textContent = z.label;
      sel.appendChild(o);
    });
  }

  function openSchedForm(item) {
    schedEditing = item ? item.id : '';
    const s = item || {
      name: '', repeat: 'weekly', days: [0], start_date: null, end_date: null,
      start_time: '10:00', stop_time: '12:00', timezone: lastTz(), enabled: true
    };
    $('schedName').value = s.name || '';
    $('schedRepeat').value = s.repeat;
    $('schedDays').querySelectorAll('input').forEach(function (cb) {
      cb.checked = s.days.indexOf(parseInt(cb.value, 10)) !== -1;
    });
    $('schedStartDate').value = isoToUs(s.start_date);
    $('schedEndDate').value = isoToUs(s.end_date);
    startPicker.set(s.start_time);
    stopPicker.set(s.stop_time);
    $('schedTz').value = s.timezone;
    $('schedSave').textContent = item ? 'Save changes' : 'Add schedule';
    syncRepeat();
    checkOvernight();
    $('schedForm').hidden = false;
    $('schedAdd').hidden = true;
    $('schedName').focus();
  }

  function closeSchedForm() {
    schedEditing = null;
    $('schedForm').hidden = true;
    $('schedAdd').hidden = false;
  }

  // A new schedule starts on the zone the operator used last.
  function lastTz() {
    return schedItems.length ? schedItems[schedItems.length - 1].timezone : schedDefaultTz;
  }

  function readSchedForm() {
    const repeat = $('schedRepeat').value;
    const payload = {
      name: $('schedName').value.trim(),
      repeat: repeat,
      start_time: startPicker.get(),
      stop_time: stopPicker.get(),
      timezone: $('schedTz').value,
      days: [],
      start_date: null,
      end_date: null
    };
    const startText = $('schedStartDate').value.trim();
    const endText = $('schedEndDate').value.trim();
    if (startText) {
      payload.start_date = usToIso(startText);
      if (!payload.start_date) throw new Error('Enter the date as MM/DD/YYYY.');
    }
    if (repeat === 'once') {
      if (!payload.start_date) throw new Error('Pick the date this runs, as MM/DD/YYYY.');
    } else {
      $('schedDays').querySelectorAll('input:checked').forEach(function (cb) {
        payload.days.push(parseInt(cb.value, 10));
      });
      if (!payload.days.length) throw new Error('Pick at least one day.');
      if (endText) {
        payload.end_date = usToIso(endText);
        if (!payload.end_date) throw new Error('Enter the end date as MM/DD/YYYY.');
      }
    }
    if (payload.start_time === payload.stop_time) throw new Error('Start and stop time must differ.');
    return payload;
  }

  $('schedAdd').addEventListener('click', function () { openSchedForm(null); });
  $('schedCancel').addEventListener('click', closeSchedForm);

  $('schedForm').addEventListener('submit', async function (ev) {
    ev.preventDefault();
    let payload;
    try { payload = readSchedForm(); } catch (e) { alertMsg(e.message); return; }
    const editing = schedEditing;
    if (editing) {
      const prev = schedItems.filter(function (s) { return s.id === editing; })[0];
      payload.enabled = prev ? prev.enabled : true;
    } else {
      payload.enabled = true;
    }
    try {
      const body = await api(editing ? '/api/admin/schedules/' + encodeURIComponent(editing)
                                     : '/api/admin/schedules', {
        method: editing ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      closeSchedForm();
      renderSchedules(body);
      okMsg(editing ? 'Schedule saved.' : 'Schedule added.');
    } catch (e) { alertMsg(e.message); }
  });

  async function schedToggle(item, on) {
    try {
      const body = await api('/api/admin/schedules/' + encodeURIComponent(item.id), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(Object.assign({}, item, { enabled: on }))
      });
      renderSchedules(body);
    } catch (e) { alertMsg(e.message); loadSchedules(); }
  }

  async function schedDelete(item) {
    if (!window.confirm('Delete the schedule "' + (item.name || item.summary) + '"?')) return;
    try {
      const body = await api('/api/admin/schedules/' + encodeURIComponent(item.id), { method: 'DELETE' });
      if (schedEditing === item.id) closeSchedForm();
      renderSchedules(body);
      okMsg('Schedule deleted.');
    } catch (e) { alertMsg(e.message); }
  }

  function renderSchedules(info) {
    if (info.timezones && !schedTimezones.length) {
      schedTimezones = info.timezones;
      renderTzOptions();
    }
    if (info.default_timezone) schedDefaultTz = info.default_timezone;
    schedItems = info.schedules || [];
    if (info.status) renderSchedStatus(info.status);

    const tb = $('schedList');
    tb.innerHTML = '';
    if (!schedItems.length) {
      tb.innerHTML = '<tr><td colspan="4" class="muted">No schedules yet.</td></tr>';
      return;
    }
    schedItems.forEach(function (s) {
      const tr = document.createElement('tr');

      const what = document.createElement('td');
      const name = document.createElement('div');
      name.className = 'schedname';
      name.textContent = s.name || s.summary;
      if (s.live && s.enabled) name.insertAdjacentHTML('beforeend', ' <span class="ok">· live window</span>');
      else if (s.expired) name.insertAdjacentHTML('beforeend', ' <span class="muted">· finished</span>');
      what.appendChild(name);
      if (s.name) {
        const sum = document.createElement('div');
        sum.className = 'schedsum';
        sum.textContent = s.summary;
        what.appendChild(sum);
      }
      tr.appendChild(what);

      const next = document.createElement('td');
      next.className = 'muted';
      next.textContent = !s.enabled ? 'off' : (s.next_start || '—');
      tr.appendChild(next);

      const on = document.createElement('td');
      const sw = document.createElement('label');
      sw.className = 'switch';
      sw.title = s.enabled ? 'Turn this schedule off' : 'Turn this schedule on';
      sw.innerHTML = '<input type="checkbox"' + (s.enabled ? ' checked' : '') + '><span class="slider"></span>';
      sw.querySelector('input').setAttribute('aria-label', 'Schedule on');
      if (!can('schedules.manage')) sw.querySelector('input').disabled = true;
      sw.querySelector('input').addEventListener('change', function (ev) { schedToggle(s, ev.target.checked); });
      on.appendChild(sw);
      tr.appendChild(on);

      const act = document.createElement('td');
      act.className = 'schedactions';
      const edit = document.createElement('button');
      edit.type = 'button';
      edit.textContent = 'Edit';
      edit.addEventListener('click', function () { openSchedForm(s); });
      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'danger';
      del.textContent = 'Delete';
      del.addEventListener('click', function () { schedDelete(s); });
      if (can('schedules.manage')) { act.appendChild(edit); act.appendChild(del); }
      tr.appendChild(act);

      tb.appendChild(tr);
    });
  }

  function renderSchedStatus(sc) {
    schedStatus = sc;
    const el = $('schedStatus');
    if (!sc || !sc.count) {
      el.innerHTML = 'No schedules set. Capture only starts when you press <em>Start capture</em>.';
      return;
    }
    const bits = [];
    if (sc.active_schedule) {
      bits.push(sc.held
        ? 'Stopped by hand during “' + sc.active_schedule + '” — it stays off until the next scheduled start.'
        : 'Inside “' + sc.active_schedule + '”.');
    }
    if (sc.started_by_schedule && sc.next_scheduled_stop) {
      bits.push('Stops ' + sc.next_scheduled_stop.label + '.');
    }
    if (sc.next_scheduled_start) {
      bits.push('Next start: ' + sc.next_scheduled_start.label
        + ' (' + sc.next_scheduled_start.names.join(', ') + ').');
    } else if (!sc.active_schedule) {
      bits.push(sc.enabled ? 'Nothing scheduled in the days ahead.' : 'Every schedule is turned off.');
    }
    el.textContent = bits.join(' ');
    el.className = 'note' + (sc.held ? ' warn' : '');
  }

  function masterText(running) {
    const sc = schedStatus;
    if (running) {
      if (sc && sc.started_by_schedule) {
        return 'Running · ' + sc.started_by_schedule
          + (sc.next_scheduled_stop ? ' · until ' + sc.next_scheduled_stop.label : '');
      }
      return 'Running';
    }
    if (sc && sc.next_scheduled_start) return 'Stopped · next ' + sc.next_scheduled_start.label;
    return 'Stopped';
  }

  // The list's "next start" and "live window" marks move when a window opens or
  // closes; the status stream says when, so refetch then rather than on a timer.
  function schedStatusChanged(sc) {
    const sig = JSON.stringify(sc || null);
    if (sig === schedSig) return;
    schedSig = sig;
    renderSchedStatus(sc);
    loadSchedules();
  }

  async function loadSchedules() {
    if (!can('schedules.view')) return;
    try { renderSchedules(await api('/api/admin/schedules')); }
    catch (e) { /* the panel is still usable without the list */ }
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
    catch (e) { alertMsg(e.message); $('stopBtn').disabled = false; }
  });

  /* ---- alerts (#121) ----
     The operator's buttons are edited here and saved whole. The status stream's `alerts_admin` frame brings the
     saved settings, the browser screens open now and each button's state; while there are unsaved edits only the
     states and hints are repainted, so typing is never undone. */
  let alertCfg = { auto_stop_s: 120, buttons: [] };
  let alertScreens = [];
  let alertState = [];
  let alertDirty = false;
  let viewerHosts = [];
  const ALERT_WORDS = { on: 'Flashing', seen: 'Seen', missed: 'Not seen', cancelled: 'Stopped' };

  function alertSetDirty(on) {
    alertDirty = on;
    $('alertDirty').hidden = !on;
  }

  function applyAlerts(frame) {
    alertScreens = frame.screens || [];
    alertState = frame.state || [];
    if (!alertDirty) {
      alertCfg = JSON.parse(JSON.stringify(frame.alerts || alertCfg));
      $('alertAutoStop').value = String(alertCfg.auto_stop_s);
    }
    renderAlerts();
  }

  function alertStateText(id) {
    const row = alertState.find(function (r) { return r.id === id; });
    if (!row || row.state === 'idle') return { cls: '', text: '' };
    let text = ALERT_WORDS[row.state] || row.state;
    if (row.state === 'on' && row.from) text += ' · from ' + row.from;
    if (row.state === 'seen' && row.seen_on) text += ' on ' + row.seen_on;
    return { cls: row.state, text: text };
  }

  function renderAlertSum() {
    const on = alertState.filter(function (r) { return r.state === 'on'; }).length;
    const n = alertCfg.buttons.length;
    $('alertsSum').innerHTML = on ? '<span class="live">' + on + ' flashing</span>'
      : n ? n + (n === 1 ? ' button' : ' buttons') : '';
  }

  function renderAlerts() {
    renderAlertSum();
    const list = $('alertList');
    // Never rebuild under the operator's cursor, or while edits wait: repaint the states only.
    if (alertDirty || list.contains(document.activeElement)) {
      list.querySelectorAll('[data-astate]').forEach(function (n) {
        const st = alertStateText(n.dataset.astate);
        n.className = 'astate ' + st.cls;
        n.textContent = st.text;
      });
      list.querySelectorAll('[data-ahint]').forEach(function (n) { n.textContent = screenHint(); });
      renderAlertLinks();
      return;
    }
    list.innerHTML = '';
    if (!alertCfg.buttons.length) {
      list.appendChild(el('div', 'muted', 'No alert buttons yet. Add one for each place a musician may need to reach.'));
    }
    alertCfg.buttons.forEach(function (b, i) { list.appendChild(alertCard(b, i)); });
    renderAlertLinks();
  }

  function screenHint() {
    return alertScreens.length ? 'Open now: ' + alertScreens.join(', ') : 'No alert screen is open right now.';
  }

  function alertCard(b, i) {
    const editable = can('alerts.manage');
    const card = el('div', 'pcard acard');
    const head = el('div', 'phead');
    // The icon the /alert page shows for it (look C): a preview and a picker.
    const icons = window.RelayAlertIcons;
    const preview = el('span', 'aicon');
    const pick = el('select', 'aiconpick');
    pick.setAttribute('aria-label', 'Icon');
    if (!b.icon) b.icon = 'bell';
    if (icons) {
      icons.names.forEach(function (n) {
        const o = el('option', '', icons.label(n));
        o.value = n;
        pick.appendChild(o);
      });
      pick.value = b.icon;
      preview.innerHTML = icons.svg(b.icon);
    }
    pick.addEventListener('change', function () {
      b.icon = pick.value;
      if (icons) preview.innerHTML = icons.svg(b.icon);
      alertSetDirty(true);
    });
    head.appendChild(preview);
    const name = el('input');
    name.value = b.name;
    name.maxLength = 32;
    name.placeholder = 'FOH booth';
    name.setAttribute('aria-label', 'Button name');
    name.style.flex = '1 1 180px';
    name.addEventListener('input', function () { b.name = name.value; alertSetDirty(true); renderAlertLinks(); });
    head.appendChild(name);
    head.appendChild(pick);
    const st = alertStateText(b.id);
    const state = el('span', 'astate ' + st.cls, st.text);
    if (b.id) state.dataset.astate = b.id;
    head.appendChild(state);
    if (!editable) { name.disabled = true; pick.disabled = true; }
    const remove = el('button', '', 'Remove');
    remove.type = 'button';
    remove.addEventListener('click', function () {
      alertCfg.buttons.splice(i, 1);
      alertSetDirty(true);
      alertDirty = false;   // rebuild now; the flag is set again just below
      renderAlerts();
      alertSetDirty(true);
    });
    if (editable) head.appendChild(remove);
    card.appendChild(head);

    const pf = el('div', 'field');
    pf.appendChild(el('label', '', 'Panels it flashes'));
    const grid = el('div', 'atargets');
    const known = panelRows.slice();
    b.panels.forEach(function (pid) {
      if (!known.some(function (r) { return r.id === pid; })) known.push({ id: pid, name: 'Forgotten panel ' + pid.slice(0, 4), online: false });
    });
    if (!known.length) grid.appendChild(el('span', 'muted', 'No panels have checked in yet.'));
    known.forEach(function (r) {
      const lab = el('label', 'check');
      const box = el('input');
      box.type = 'checkbox';
      box.checked = b.panels.indexOf(r.id) !== -1;
      box.disabled = !editable;
      box.addEventListener('change', function () {
        b.panels = b.panels.filter(function (x) { return x !== r.id; });
        if (box.checked) b.panels.push(r.id);
        alertSetDirty(true);
      });
      lab.appendChild(box);
      lab.appendChild(el('span', '', r.name + (r.online ? '' : ' (offline)')));
      grid.appendChild(lab);
    });
    pf.appendChild(grid);
    card.appendChild(pf);

    const sf = el('div', 'field');
    const sid = 'alertScreens' + i;
    const sl = el('label', '', 'Browser screens it flashes (names, separated by commas)');
    sl.htmlFor = sid;
    sf.appendChild(sl);
    const screens = el('input');
    screens.id = sid;
    screens.placeholder = 'Booth TV';
    screens.value = b.screens.join(', ');
    screens.disabled = !editable;
    screens.addEventListener('input', function () {
      b.screens = screens.value.split(',').map(function (x) { return x.replace(/\s+/g, ' ').trim(); })
        .filter(Boolean);
      alertSetDirty(true);
      renderAlertLinks();
    });
    sf.appendChild(screens);
    const hint = el('div', 'note', screenHint());
    hint.dataset.ahint = '1';
    sf.appendChild(hint);
    card.appendChild(sf);

    const stageRow = el('label', 'check');
    const stageBox = el('input');
    stageBox.type = 'checkbox';
    stageBox.checked = !!b.stage;
    stageBox.disabled = !editable;
    stageBox.addEventListener('change', function () { b.stage = stageBox.checked; alertSetDirty(true); });
    stageRow.appendChild(stageBox);
    stageRow.appendChild(el('span', '', 'Also show on the ProPresenter stage display (cleared when acknowledged)'));
    // Only while the ProPresenter module is on (its script sets the hook); the saved choice is kept either way.
    if (window.RelayAdmin && window.RelayAdmin.panelHooks.stageDisplay) card.appendChild(stageRow);
    return card;
  }

  function renderAlertLinks() {
    const box = $('alertLinks');
    if (!box) return;
    // The viewer hosts end in "/" (they are joined to "translation" and the like); these paths start with one.
    const host = (viewerHosts[0] || location.origin).replace(/\/+$/, '');
    const rows = [['Sender page (phones)', '/alert']];
    const seen = {};
    alertCfg.buttons.forEach(function (b) {
      b.screens.forEach(function (sname) {
        const key = sname.toLowerCase();
        if (seen[key]) return;
        seen[key] = true;
        rows.push(['Screen “' + sname + '”', '/screen?alert=' + encodeURIComponent(sname)]);
      });
    });
    box.innerHTML = '';
    rows.forEach(function (r) {
      const line = el('div', 'ctl');
      line.style.marginBottom = '6px';
      line.appendChild(el('span', '', r[0] + ':'));
      const a = el('a', 'mono', host + r[1]);
      a.href = host + r[1];
      a.target = '_blank';
      a.rel = 'noopener';
      line.appendChild(a);
      box.appendChild(line);
    });
  }

  $('alertAdd').addEventListener('click', function () {
    alertCfg.buttons.push({ name: '', panels: [], screens: [] });
    alertDirty = false;
    renderAlerts();
    alertSetDirty(true);
    const inputs = $('alertList').querySelectorAll('.phead input');
    if (inputs.length) inputs[inputs.length - 1].focus();
  });
  $('alertAutoStop').addEventListener('change', function () {
    alertCfg.auto_stop_s = parseInt($('alertAutoStop').value, 10);
    alertSetDirty(true);
  });
  $('alertSave').addEventListener('click', async function () {
    try {
      const frame = await post('/api/admin/alerts', {
        auto_stop_s: parseInt($('alertAutoStop').value, 10),
        buttons: alertCfg.buttons.map(function (b) {
          const out = { name: b.name, icon: b.icon || 'bell', panels: b.panels, screens: b.screens };
          if (b.id) out.id = b.id;
          return out;
        })
      });
      alertSetDirty(false);
      if (document.activeElement && $('alertList').contains(document.activeElement)) document.activeElement.blur();
      applyAlerts(frame);
      okMsg('Alerts saved');
    } catch (e) { alertMsg(e.message); }
  });

  /* ---- panels ---- */
  let panelRows = [];
  let panelsAt = 0;          // when panelRows arrived: "last seen" counts on from here
  let panelDirty = false;
  let panelBoards = [];      // one row per kind of panel: {board, label, firmware, online, outdated}

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function agoText(sec) {
    if (sec === null || sec === undefined) return 'never';
    if (sec < 5) return 'just now';
    if (sec < 90) return Math.round(sec) + 's ago';
    if (sec < 5400) return Math.round(sec / 60) + 'm ago';
    if (sec < 172800) return Math.round(sec / 3600) + 'h ago';
    return Math.round(sec / 86400) + 'd ago';
  }

  // Followed Planning Center service types' names, for the panels' Service form and cards. Set by the Planning
  // Center module's script (RelayAdmin.setServiceNames); empty while that module is off.
  let serviceNames = {};

  function serviceLabel(id) {
    const several = Object.keys(serviceNames).length > 1;
    const name = serviceNames[id];
    return name && several ? 'Service: ' + name : name ? 'Service' : 'Service (type ' + id + ')';
  }

  function feedText(feeds, rows) {
    if (!feeds || !feeds.length) return 'none chosen';
    return feeds.map(function (f) {
      if (f.stream === 'transcription') return 'Transcription';
      if (f.stream === 'service') return serviceLabel(f.service_type);
      if (f.stream === 'alert') return 'Alert buttons';
      if (f.stream === 'lyrics') return 'Lyrics';
      if (f.stream === 'timers') return 'Timers';
      if (f.stream === 'status') return 'Status';
      const hit = (rows || []).find(function (l) { return l.id === f.lang; });
      return hit ? hit.label : (f.lang || '?');
    }).join(' + ');
  }

  function signalText(rssi) {
    if (rssi === null || rssi === undefined) return '—';
    const q = rssi >= -60 ? 'strong' : rssi >= -72 ? 'fair' : 'weak';
    return q + ' (' + rssi + ' dBm)';
  }

  function feedOptions(sel, row, value) {
    sel.innerHTML = '';
    const none = el('option', '', '— none —');
    none.value = '';
    sel.appendChild(none);
    const t = el('option', '', 'Transcription');
    t.value = 'transcription';
    sel.appendChild(t);
    // Only languages this panel can draw are offered.
    row.languages.forEach(function (l) {
      const o = el('option', '', l.label);
      o.value = 'translation:' + l.id;
      sel.appendChild(o);
    });
    // The Planning Center service, one entry per type Relay follows (#122). The panel shows it only while
    // its service mode shows the service; Relay says so if not.
    const ids = Object.keys(serviceNames);
    if (value && value.indexOf('service:') === 0 && ids.indexOf(value.slice(8)) === -1) ids.push(value.slice(8));
    ids.forEach(function (id) {
      const o = el('option', '', serviceLabel(id));
      o.value = 'service:' + id;
      sel.appendChild(o);
    });
    // The alert buttons (#121): the pane sends alerts, set up on the Alerts page.
    const a = el('option', '', 'Alert buttons');
    a.value = 'alert';
    sel.appendChild(a);
    // ProPresenter's lyrics and timers (#138). The panel shows them only while Relay has ProPresenter set up, and
    // says so itself if not.
    [['lyrics', 'Lyrics (ProPresenter)'], ['timers', 'Timers (ProPresenter)']].forEach(function (p) {
      const o = el('option', '', p[1]);
      o.value = p[0];
      sel.appendChild(o);
    });
    // The status view (#224): capture, input level, languages, viewers and the livestream.
    const st = el('option', '', 'Status');
    st.value = 'status';
    sel.appendChild(st);
    sel.value = value || '';
  }

  function feedValue(f) {
    if (!f) return '';
    if (f.stream === 'transcription') return 'transcription';
    if (f.stream === 'service') return 'service:' + f.service_type;
    if (['alert', 'lyrics', 'timers', 'status'].indexOf(f.stream) !== -1) return f.stream;
    return 'translation:' + f.lang;
  }

  function feedFromValue(v) {
    if (v === 'transcription') return { stream: 'transcription' };
    if (['alert', 'lyrics', 'timers', 'status'].indexOf(v) !== -1) return { stream: v };
    if (v.indexOf('service:') === 0) return { stream: 'service', service_type: v.slice('service:'.length) };
    return { stream: 'translation', lang: v.slice('translation:'.length) };
  }

  function boardFirmware(board) {
    const b = panelBoards.find(function (x) { return x.board === board; });
    return b ? b.firmware : null;
  }

  const UPDATE_WORDS = {
    queued: 'Waiting for the panel to start',
    downloading: 'Downloading',
    ready: 'Downloaded and verified; installs at a quiet moment',
    restarting: 'Restarting into the new version',
    installed: 'Updated',
    failed: 'Update failed',
    rolled_back: 'Rolled back'
  };

  function updateRow(row) {
    const u = row.update;
    if (!u && !row.update_available) return null;
    const box = el('div', 'pupdate');
    if (u) {
      const bad = u.state === 'failed' || u.state === 'rolled_back';
      let text = (UPDATE_WORDS[u.state] || u.state) + (u.version ? ' · ' + u.version : '');
      if (u.state === 'downloading') text += ' · ' + u.progress + '%';
      if (bad && u.error_text) text += ': ' + u.error_text;
      box.appendChild(el('div', bad ? 'bad' : '', text));
      if (u.state === 'downloading') {
        const p = el('progress');
        p.max = 100;
        p.value = u.progress;
        box.appendChild(p);
      }
    }
    if (row.update_available && !row.update_requested) {
      const fw = boardFirmware(row.board);
      box.appendChild(el('div', 'badge', 'Update available' + (fw ? ' · ' + fw.version : '')));
    }
    return box;
  }

  async function askUpdate(id, now) {
    try { await post('/api/admin/panels/firmware/update', { id: id, now: now }); alertMsg(''); }
    catch (e) { alertMsg(e.message); }
  }

  /* One row per kind of panel (#122): its image, how many of its panels are online and behind, and its own
     upload, update and remove. A board on hold is not listed: Relay keeps nothing for it. */
  function renderFirmware() {
    const box = $('fwTypes');
    if (box.contains(document.activeElement) && document.activeElement.type === 'file') return;
    box.innerHTML = '';
    panelBoards.forEach(function (b) {
      const f = b.firmware;
      const row = el('div', 'fwtype');
      row.appendChild(el('div', 'fwname', b.label));
      row.appendChild(el('div', 'fwmeta', f
        ? (f.filename || 'relay-display-app.bin') + ' · ' + f.version + ' · ' + Math.round(f.size / 1024) + ' KB · uploaded '
          + agoText(f.uploaded_ago_s + (Date.now() - panelsAt) / 1000) + ' · ' + b.online + ' online'
          + (b.outdated ? ', ' + b.outdated + ' behind' : '')
        : 'No firmware uploaded · ' + b.online + ' online'));
      const ctl = el('div', 'fwrow');
      const file = el('input');
      file.type = 'file';
      file.accept = '.bin,application/octet-stream';
      file.setAttribute('aria-label', 'Signed ' + b.label + ' image (relay-display-app.bin)');
      const up = el('button', '', 'Upload');
      up.type = 'button';
      up.addEventListener('click', async function () {
        const chosen = file.files[0];
        if (!chosen) { alertMsg('Choose the ' + b.label + ' relay-display-app.bin first.'); return; }
        try {
          await api('/api/admin/panels/firmware?board=' + encodeURIComponent(b.board) + '&filename='
            + encodeURIComponent(chosen.name), {
            method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: chosen });
          file.value = '';
          alertMsg('');
        } catch (e) { alertMsg(e.message); }
      });
      const all = el('button', '', 'Update all online');
      all.type = 'button';
      all.hidden = !(f && panelRows.some(function (r) {
        return r.board === b.board && r.update_available && r.online && !r.update_requested;
      }));
      all.addEventListener('click', async function () {
        try {
          const r = await post('/api/admin/panels/firmware/update', { now: false, board: b.board });
          okMsg(r.requested + ' panel(s) asked to update' + (r.offline ? '; ' + r.offline + ' offline, ask them one by one' : ''));
        } catch (e) { alertMsg(e.message); }
      });
      const rm = el('button', '', 'Remove');
      rm.type = 'button';
      rm.hidden = !f;
      rm.addEventListener('click', async function () {
        if (!confirm('Remove the ' + b.label + ' firmware held by Relay?')) return;
        try { await api('/api/admin/panels/firmware?board=' + encodeURIComponent(b.board), { method: 'DELETE' }); alertMsg(''); }
        catch (e) { alertMsg(e.message); }
      });
      [file, up, all, rm].forEach(function (n) { ctl.appendChild(n); });
      if (can('panels.manage')) row.appendChild(ctl);
      box.appendChild(row);
    });
  }

  const SERVICE_MODE_WORDS = {
    off: 'Off', show: 'Show only', control: 'Control only', both: 'Show and control'
  };
  const SERVICE_MODE_HELP = {
    off: 'No Service feed in the panel\'s menu.',
    show: 'Now and next, timers, the order of service, song details and who is controlling.',
    control: 'Next, Previous and Take control, with who is controlling. No order of service.',
    both: 'The order of service and the controls.'
  };

  function tickPanelSeen() {
    const dt = (Date.now() - panelsAt) / 1000;
    document.querySelectorAll('.panelseen').forEach(function (n) {
      const base = n.dataset.seen;
      n.textContent = base === '' ? 'never' : agoText(parseFloat(base) + dt);
    });
  }

  /* The list is one row per panel; the name opens that panel's full card (settings,
     feed, service, firmware) in the right-hand drawer. */
  let panelDrawer = null;
  let drawerTouched = false;   // the form has edits (or focus) that a refresh must not wipe

  function openDrawer(id) {
    panelDrawer = id;
    drawerTouched = false;
    renderPanels();
    const d = $('drawer');
    $('drawerBg').hidden = false;
    d.hidden = false;
    d.setAttribute('aria-hidden', 'false');
    const close = $('dClose');
    if (close) close.focus();
  }

  function closeDrawer() {
    panelDrawer = null;
    drawerTouched = false;
    $('drawerBg').hidden = true;
    $('drawer').hidden = true;
    $('drawer').setAttribute('aria-hidden', 'true');
    renderPanels();
  }

  function field(label, control, id) {
    const f = el('div', 'field');
    const l = el('label', '', label);
    if (id) { control.id = id; l.htmlFor = id; }
    f.appendChild(l);
    f.appendChild(control);
    return f;
  }

  function radioRow(name, options, value, onchange) {
    const wrap = el('div', 'radios');
    options.forEach(function (o) {
      const lab = el('label', 'check');
      const r = document.createElement('input');
      r.type = 'radio'; r.name = name; r.value = o[0];
      r.checked = String(o[0]) === String(value);
      r.addEventListener('change', function () { onchange(o[0]); });
      lab.appendChild(r);
      lab.appendChild(el('span', '', o[1]));
      wrap.appendChild(lab);
    });
    return wrap;
  }

  function kv(parent, k, v) {
    const r = el('div', 'kv');
    r.appendChild(el('span', 'muted', k));
    if (typeof v === 'string') r.appendChild(el('span', '', v)); else r.appendChild(v);
    parent.appendChild(r);
  }

  /* One form for everything about a panel. Save sends only what changed: re-sending an
     unchanged feed would overwrite a choice made at the panel's own screen. */
  function renderDrawer() {
    const row = panelDrawer && panelRows.find(function (r) { return r.id === panelDrawer; });
    if (!row) {
      if (panelDrawer) { panelDrawer = null; $('drawerBg').hidden = true; $('drawer').hidden = true; }
      return;
    }
    $('dTitle').textContent = row.name;
    const body = $('dBody');
    if (drawerTouched || body.contains(document.activeElement)) { refreshDrawerDevice(row); return; }
    body.innerHTML = '';
    const foot = $('dFoot');
    foot.innerHTML = '';

    const editable = can('panels.manage');
    const form = el('form');
    form.autocomplete = 'off';
    form.addEventListener('input', function () { drawerTouched = true; });
    form.addEventListener('change', function () { drawerTouched = true; });

    const name = el('input');
    name.type = 'text'; name.maxLength = 64; name.placeholder = 'Lobby left';
    name.value = row.named ? row.name : '';
    form.appendChild(field('Name', name, 'panelName-' + row.id));

    form.appendChild(el('h3', 'dhead', 'Display'));
    const current = row.set_feeds || row.feeds || [];
    let count = current.length > 1 ? 2 : 1;
    const feed1 = el('select'), feed2 = el('select');
    feedOptions(feed1, row, feedValue(current[0]));
    feedOptions(feed2, row, feedValue(current[1]));
    feed1.firstChild.textContent = row.set_feeds ? "The panel's own choice" : '— none —';
    feed2.firstChild.textContent = '— none —';
    const f2 = field('Feed 2 · right / bottom', feed2, 'panelFeed1-' + row.id);
    const f1label = el('span', 'muted', '');
    form.appendChild(field('Number of feeds', radioRow('nfeeds-' + row.id,
      [[1, 'One (full screen)'], [2, 'Two (split)']], count, function (n) {
        count = Number(n); f2.hidden = count !== 2; f1label.textContent = count === 2 ? ' · left / top' : ' · full screen';
      })));
    const f1 = field('Feed 1', feed1, 'panelFeed0-' + row.id);
    f1.firstChild.appendChild(f1label);
    f1label.textContent = count === 2 ? ' · left / top' : ' · full screen';
    f2.hidden = count !== 2;
    form.appendChild(f1);
    form.appendChild(f2);
    if (row.set_feeds) form.appendChild(el('div', 'note', 'Set from admin: ' + feedText(row.set_feeds, row.languages)
      + '. A change made at the panel\'s own screen afterwards wins until you set one again.'));

    // 0 or 180 (#122), starting from what the panel shows. The panel takes a new rotation by restarting, as its
    // screen is set up at boot. A turn made at the panel's own menu afterwards wins until one is set here again.
    const rotNow = row.rotation !== null && row.rotation !== undefined ? row.rotation
      : (row.set_rotation !== null && row.set_rotation !== undefined ? row.set_rotation : 0);
    let rotation = rotNow;
    const rfield = field('Screen rotation', radioRow('rot-' + row.id,
      [[0, '0°'], [90, '90°'], [180, '180°'], [270, '270°']], rotNow, function (d) { rotation = Number(d); }));
    let rnote = 'A change restarts the panel, which takes about half a minute. A turn made at the panel\'s own '
      + 'screen afterwards wins until you set one again.';
    if (row.rotation_changed_by === 'screen') rnote += ' Last turned at the screen.';
    else if (row.set_rotation !== null && row.set_rotation !== undefined && row.rotation !== null
             && row.rotation !== row.set_rotation) rnote += ' Waiting for the panel to restart at ' + row.set_rotation + '°.';
    rfield.appendChild(el('div', 'note', rnote));
    form.appendChild(rfield);

    // ProPresenter's timers (#138): this panel's own thresholds and timers, used instead of the global ones. Shown
    // while a timers feed is chosen here (or the panel already has settings of its own).
    const tmBox = el('div');
    tmBox.appendChild(el('h3', 'dhead', 'ProPresenter timers'));
    const tmOwn = document.createElement('input');
    tmOwn.type = 'checkbox';
    tmOwn.checked = !!row.set_timers;
    const tmOwnLab = el('label', 'check');
    tmOwnLab.appendChild(tmOwn);
    tmOwnLab.appendChild(el('span', '', 'Use settings of its own for this panel'));
    tmBox.appendChild(tmOwnLab);
    const tmFields = el('div');
    const tmAmber = el('input'), tmRed = el('input');
    [tmAmber, tmRed].forEach(function (i) { i.type = 'text'; i.autocomplete = 'off'; i.style.maxWidth = '90px'; });
    tmAmber.placeholder = '2:00';
    tmRed.placeholder = '0:30';
    tmFields.appendChild(field('Amber at', tmAmber, 'panelTmAmber-' + row.id));
    tmFields.appendChild(field('Red at', tmRed, 'panelTmRed-' + row.id));
    const tmPick = el('div', 'plist');
    tmFields.appendChild(field('Timers to show', tmPick));
    tmFields.appendChild(el('div', 'note', 'Time left, as minutes:seconds (2:00) or seconds (90). Tick up to four timers; '
      + 'with none ticked the panel shows the first four ProPresenter has.'));
    tmBox.appendChild(tmFields);
    const tmNote = el('div', 'note', 'Without settings of its own, this panel follows Timers on panels, on the ProPresenter page.');
    tmBox.appendChild(tmNote);
    let tmGlobal = null;
    const tmSync = function () {
      tmBox.hidden = !(feed1.value === 'timers' || (count === 2 && feed2.value === 'timers') || row.set_timers);
      tmFields.hidden = !tmOwn.checked;
      tmNote.hidden = tmOwn.checked;
    };
    [feed1, feed2, tmOwn].forEach(function (n) { n.addEventListener('change', tmSync); });
    form.querySelectorAll('input[type=radio]').forEach(function (n) { n.addEventListener('change', tmSync); });
    tmSync();
    form.appendChild(tmBox);
    // ProPresenter's timers and global thresholds, from its module's script while it is on.
    (window.RelayAdmin.panelHooks.timers ? window.RelayAdmin.panelHooks.timers() : Promise.reject()).then(function (b) {
      tmGlobal = b;
      const mine = row.set_timers || {}, g = b.settings.timers || { amber_s: 120, red_s: 30, show: [] };
      tmAmber.value = clockText(mine.amber_s !== undefined ? mine.amber_s : g.amber_s);
      tmRed.value = clockText(mine.red_s !== undefined ? mine.red_s : g.red_s);
      timerPicker(tmPick, b.state.timers || [], mine.show !== undefined ? mine.show : g.show);
      if (!editable) tmPick.querySelectorAll('input').forEach(function (n) { n.disabled = true; });
    }).catch(function () { /* the timers just are not listed */ });

    const pcOn = !!(window.RelayAdmin.panelHooks.serviceOn && window.RelayAdmin.panelHooks.serviceOn());
    let mode, type, tfield, help;
    if (pcOn || (row.service_mode && row.service_mode !== 'off')) {
      form.appendChild(el('h3', 'dhead', 'Planning Center'));
      mode = el('select');
      ['off', 'show', 'control', 'both'].forEach(function (m) {
        const o = el('option', '', SERVICE_MODE_WORDS[m]);
        o.value = m;
        mode.appendChild(o);
      });
      mode.value = row.service_mode || 'off';
      const mfield = field('Service mode', mode, 'panelSvc-' + row.id);
      help = el('div', 'note', '');
      mfield.appendChild(help);
      form.appendChild(mfield);
      const ids = Object.keys(serviceNames);
      type = el('select');
      const own = el('option', '', ids.length > 1 ? 'The panel picks' : 'The one Relay follows');
      own.value = '';
      type.appendChild(own);
      ids.forEach(function (id) {
        const o = el('option', '', serviceNames[id]);
        o.value = id;
        type.appendChild(o);
      });
      if (row.service_type && ids.indexOf(row.service_type) === -1) {
        const o = el('option', '', 'Service type ' + row.service_type + ' (no longer followed)');
        o.value = row.service_type;
        type.appendChild(o);
      }
      type.value = row.service_type || '';
      tfield = field('Planning Center service type', type, 'panelSvcType-' + row.id);
      form.appendChild(tfield);
      const sync = function () {
        help.textContent = SERVICE_MODE_HELP[mode.value]
          + (mode.value === 'control' || mode.value === 'both'
            ? ' Presses run as the person who made the Planning Center token. The panel gets a key, '
              + 'but it travels over plain HTTP: anyone who can read the network could use it.' : '');
        tfield.hidden = mode.value === 'off';
      };
      mode.addEventListener('change', sync);
      sync();
    }

    form.appendChild(el('h3', 'dhead', 'Device'));
    const dev = el('div', 'devinfo');
    dev.id = 'devInfo';
    form.appendChild(dev);
    body.appendChild(form);
    refreshDrawerDevice(row);

    if (!editable) {
      // A role that can see panels but not change them gets the settings as read-only text, not controls that
      // would only be refused.
      form.querySelectorAll('input, select, textarea').forEach(function (n) { n.disabled = true; });
      const closeBtn = el('button', '', 'Close');
      closeBtn.type = 'button';
      closeBtn.addEventListener('click', closeDrawer);
      foot.appendChild(closeBtn);
      return;
    }

    const save = el('button', 'primary', 'Save');
    save.type = 'button';
    const cancel = el('button', '', 'Cancel');
    cancel.type = 'button';
    const forget = el('button', 'forget', 'Forget');
    forget.type = 'button';
    foot.appendChild(save);
    foot.appendChild(cancel);
    foot.appendChild(el('span', 'spacer'));
    foot.appendChild(forget);

    cancel.addEventListener('click', closeDrawer);
    forget.addEventListener('click', async function () {
      if (!confirm('Forget ' + row.name + '? A panel that is still on will reappear, unnamed.')) return;
      try { await api('/api/admin/panels/' + row.id, { method: 'DELETE' }); alertMsg(''); closeDrawer(); }
      catch (e) { alertMsg(e.message); }
    });
    form.addEventListener('submit', function (ev) { ev.preventDefault(); save.click(); });
    save.addEventListener('click', async function () {
      const out = {};
      if (name.value.trim() !== (row.named ? row.name : '')) out.name = name.value;
      const picked = [feed1.value, count === 2 ? feed2.value : ''].filter(Boolean).map(feedFromValue);
      const before = JSON.stringify(current.map(function (f) { return feedFromValue(feedValue(f)); }));
      if (!picked.length) {
        if (row.set_feeds) out.feeds = null;                   // back to the panel's own choice
      } else if (JSON.stringify(picked) !== before) {
        out.feeds = picked;
      }
      if (rotation !== rotNow) out.rotation = rotation;
      if (!tmBox.hidden) {
        if (tmOwn.checked) {
          const th = timerThresholds(tmAmber, tmRed);
          if (th.error) { alertMsg(th.error); return; }
          const t = { amber_s: th.amber_s, red_s: th.red_s, show: timerPickerValue(tmPick) };
          if (tmGlobal && JSON.stringify(t) !== JSON.stringify(row.set_timers || null)) out.timers = t;
        } else if (row.set_timers) {
          out.timers = null;   // back to the global settings
        }
      }
      if (mode) {
        if (mode.value !== (row.service_mode || 'off')) out.service_mode = mode.value;
        if (mode.value !== 'off' && type.value !== (row.service_type || '')) out.service_type = type.value || null;
      }
      if (!Object.keys(out).length) { closeDrawer(); return; }
      save.disabled = true;
      if (out.rotation !== undefined && row.online
          && !confirm('Turning the screen restarts ' + row.name + '. Its captions stop for about half a minute.')) {
        return;
      }
      try {
        await post('/api/admin/panels/' + row.id, out);
        okMsg('Saved ' + (out.name !== undefined ? (out.name || row.name) : row.name) + '.');
        closeDrawer();
      } catch (e) { alertMsg(e.message); save.disabled = false; }
    });
  }

  /* The read-only device details can refresh while the form is being edited. */
  function refreshDrawerDevice(row) {
    const dev = $('devInfo');
    if (!dev) return;
    dev.innerHTML = '';
    kv(dev, 'Status', row.online ? 'Online' : 'Offline');
    const seen = el('span', 'panelseen', '');
    seen.dataset.seen = row.seen_ago_s === null ? '' : String(row.seen_ago_s);
    kv(dev, 'Last seen', seen);
    kv(dev, 'Showing', feedText(row.feeds, row.languages));
    if (row.feeds_changed_by) {
      kv(dev, 'Feed changed', (row.feeds_changed_by === 'screen' ? 'at the screen' : 'from admin')
        + ' · ' + agoText(row.feeds_changed_ago_s + (Date.now() - panelsAt) / 1000));
    }
    kv(dev, 'Board', row.board_label || row.board || '—');
    kv(dev, 'Rotation', row.rotation === null || row.rotation === undefined ? '—' : row.rotation + '°');
    kv(dev, 'Firmware', row.version || '—');
    kv(dev, 'Address', row.ip || '—');
    kv(dev, 'Wi-Fi', signalText(row.rssi));
    if (row.named && row.reported_name && row.reported_name !== row.name) kv(dev, 'Reports as', row.reported_name);
    const up = updateRow(row);
    if (up) dev.appendChild(up);
    if (can('panels.manage') && row.update_available && !row.update_requested && row.online) {
      const actions = el('div', 'pactions');
      const later = el('button', '', 'Update');
      later.type = 'button';
      later.title = 'Installs at a quiet moment: no new caption line for a while';
      later.addEventListener('click', function () { askUpdate(row.id, false); });
      const now = el('button', '', 'Update now');
      now.type = 'button';
      now.addEventListener('click', function () {
        if (confirm('Restart ' + row.name + ' now? Its captions stop for about half a minute.')) askUpdate(row.id, true);
      });
      actions.appendChild(later);
      actions.appendChild(now);
      dev.appendChild(actions);
    }
    if (can('panels.manage') && row.online) {
      const actions = el('div', 'pactions');
      const reboot = el('button', '', 'Reboot');
      reboot.type = 'button';
      reboot.disabled = !row.can_reboot;
      if (!row.can_reboot) reboot.title = 'The panel is not connected to Relay right now, or its firmware is too old to restart from here';
      reboot.addEventListener('click', async function () {
        if (!confirm('Restart ' + row.name + ' now? Its captions stop for about half a minute.')) return;
        try { await post('/api/admin/panels/' + row.id + '/reboot', {}); okMsg(row.name + ' is restarting.'); }
        catch (e) { alertMsg(e.message); }
      });
      actions.appendChild(reboot);
      dev.appendChild(actions);
    }
    tickPanelSeen();
  }

  function panelRow(row) {
    const r = el('div', 'prow' + (row.online ? '' : ' offline'));
    const name = el('button', 'plink', row.name);
    name.type = 'button';
    name.addEventListener('click', function () { openDrawer(row.id); });
    r.appendChild(name);
    r.appendChild(el('span', 'pboard muted', row.board_label || row.board || '—'));
    r.appendChild(el('span', 'pshow', feedText(row.feeds, row.languages)));
    r.appendChild(el('span', 'pfw muted', row.version || '—'));
    const stat = el('span', 'pstat');
    stat.appendChild(el('span', 'dot ' + (row.online ? 'ok' : '')));
    stat.appendChild(document.createTextNode(row.online ? 'Online' : 'Offline'));
    if (row.update_available && !row.update_requested) stat.appendChild(el('span', 'badge', ' · update'));
    r.appendChild(stat);
    return r;
  }

  function renderPanels() {
    panelDirty = false;
    const list = $('panelList');
    list.innerHTML = '';
    if (!panelRows.length) {
      list.appendChild(el('div', 'muted', 'No panels have checked in yet.'));
    }
    if (panelRows.length) {
      const head = el('div', 'prow head');
      ['Name', 'Board', 'Showing', 'Firmware', 'Status'].forEach(function (t, i) {
        const c = el('span', i === 4 ? 'pstat' : '', t);
        head.appendChild(c);
      });
      list.appendChild(head);
    }
    panelRows.forEach(function (row) { list.appendChild(panelRow(row)); });
    renderDrawer();
    const on = panelRows.filter(function (r) { return r.online; }).length;
    $('panelsSum').textContent = panelRows.length ? on + ' of ' + panelRows.length + ' online' : '';
    tickPanelSeen();
  }

  function applyPanels(frame) {
    panelRows = frame.panels || [];
    panelBoards = frame.boards || [];
    panelsAt = Date.now();
    renderFirmware();
    renderPanels();
  }

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
    paintPill(st.running);
    $('masterState').textContent = masterText(st.running);
    $('startBtn').disabled = st.running;
    $('stopBtn').disabled = !st.running;
    if (st.running) $('audioDot').className = 'dot ' + (st.speaking ? 'ok' : 'warn');
    paintMeter(st);
  }

  function applyStatus(st) {
    if (!st) return;
    if (st.type === 'panels') { applyPanels(st); renderAlerts(); return; }
    if (st.type === 'alerts_admin') { applyAlerts(st); return; }
    if (st.type === 'meter') { applyMeter(st); tickPanelSeen(); return; }
    if (!st.sessions) return;

    if (st.schedule) schedStatusChanged(st.schedule);
    $('masterDot').className = 'dot ' + (st.running ? 'ok' : '');
    paintPill(st.running);
    $('masterState').textContent = masterText(st.running);
    $('startBtn').disabled = st.running;
    $('stopBtn').disabled = !st.running;
    if (recRunning && !st.running) loadRuns();  // a run just ended: it is downloadable now
    recRunning = !!st.running;
    $('viewerCount').textContent = st.viewers + (st.viewers === 1 ? ' viewer' : ' viewers');

    const a = st.audio || {};
    $('audioDot').className = 'dot ' + (a.error ? 'err' : a.running ? (a.speaking ? 'ok' : 'warn') : '');
    $('audioState').textContent = a.reconnecting
      ? 'Waiting for input'
      : a.error
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

    if (st.blocklist) renderBlocklist(st.blocklist, st.blocklist_file, st.blocklist_max);
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
      // A browser source for OBS / vMix / ProPresenter, or a keyed output for an
      // ATEM. The defaults; the Broadcast overlay page builds and saves others.
      ['Overlay (OBS / vMix / ProPresenter)', 'overlay'],
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

  // The machine can change networks while the page is open (a laptop moving between Wi-Fi networks).
  // Re-read the viewer links every few seconds and redraw only when they differ.
  let urlsSeen = null, urlsTimer = null, secureSeen = null, secureNow = null;
  function watchUrls(initial) {
    urlsSeen = JSON.stringify(initial || []);
    secureSeen = JSON.stringify(secureNow);
    if (urlsTimer) return;
    urlsTimer = setInterval(async function () {
      if (document.hidden) return;
      try {
        const body = await api('/api/admin/urls');
        const now = body.urls || [];
        if (JSON.stringify(now) !== urlsSeen) { urlsSeen = JSON.stringify(now); renderUrls(now); }
        if (JSON.stringify(body.serving) !== secureSeen) { secureSeen = JSON.stringify(body.serving); renderSecure(body.serving); }
      } catch (e) { /* offline or signed out: the next tick tries again */ }
    }, 10000);
  }

  /* How the panel is reached: its HTTPS addresses, the certificate fingerprint to check against the browser's
     warning, a notice when Relay had to use other ports, and a warning when the fingerprint changed. Built with
     textContent: the host name and fingerprint come from the server, never from markup. */
  function renderSecure(info) {
    secureNow = info;
    const box = $('secure');
    if (!box || !info) return;
    box.textContent = '';
    const sec = document.createElement('section');
    const h = document.createElement('h3'); h.textContent = 'Operator panel (HTTPS)'; sec.appendChild(h);
    const add = function (cls, text) {
      const d = document.createElement('div'); if (cls) d.className = cls; d.textContent = text; sec.appendChild(d); return d;
    };
    (info.notices || []).forEach(function (n) { add('note', n); });
    const cert = info.certificate || {};
    if (cert.changed) {
      add('note', 'The panel certificate was replaced (' + (cert.changed.at || '') + '), so its fingerprint changed. ' +
        'Check the new one below against what your browser shows before you trust it again.');
    }
    (info.panel_urls || []).forEach(function (u) {
      const d = add('', ''); const a = document.createElement('a'); a.href = u; a.textContent = u; d.appendChild(a);
    });
    if (cert.fingerprint) {
      add('', 'Certificate SHA-256 fingerprint:');
      const fp = add('mono', cert.fingerprint); fp.style.wordBreak = 'break-all'; fp.style.userSelect = 'all';
      if (cert.expires) add('note', 'Valid until ' + cert.expires.slice(0, 10) + '. It is self-signed, so the browser warns once; ' +
        'compare this fingerprint with the one it shows before you accept.');
    }
    box.appendChild(sec);
  }

  function renderUrls(urls) {
    // Only the LAN address is worth handing to a room; 127.0.0.1 works only on
    // the host. Keep it as a fallback if the LAN address could not be resolved.
    let hosts = (urls || []).filter(function (u) { return u.indexOf('127.0.0.1') === -1; });
    if (!hosts.length) hosts = urls || [];
    viewerHosts = hosts;
    renderAlertLinks();
    $('urls').innerHTML = VIEW_GROUPS.map(function (group) {
      const rows = group[1].map(function (v) { return urlRow(v[0], v[1], hosts); }).join('');
      return '<section><h3>' + group[0] + '</h3>' + rows + '</section>';
    }).join('') +
      '<section><h3>Notes</h3><div class="note" style="margin-top:0">Screen defaults to translation plus the first live ' +
      'language; append <span class="mono">?stream=&amp;lang=</span> to pin a specific room display. The overlay link ' +
      'here uses the defaults; to choose a layout, language, size and colours, or save it by name, use ' +
      '<a href="#overlay">Broadcast overlay</a>. Only the LAN address is shown; 127.0.0.1 works only on the machine ' +
      'running Relay.</div></section>';
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

  /* ---- navigation: one settings area at a time, chosen from the sidebar ---- */
  const PAGES = Array.prototype.map.call(document.querySelectorAll('.page[data-page]'), function (n) { return n.dataset.page; });

  function showPage(name) {
    if (PAGES.indexOf(name) === -1) name = PAGES[0];
    document.querySelectorAll('.page[data-page]').forEach(function (n) { n.hidden = n.dataset.page !== name; });
    document.querySelectorAll('nav a[data-page]').forEach(function (a) {
      if (a.dataset.page === name) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
      a.classList.toggle('on', a.dataset.page === name);
    });
    $('acctLink').classList.toggle('on', name === 'account');
    if (name === 'account') $('acctLink').setAttribute('aria-current', 'page'); else $('acctLink').removeAttribute('aria-current');
    $('nav').classList.remove('open');
    $('navToggle').setAttribute('aria-expanded', 'false');
    if (panelDrawer && name !== 'panels') closeDrawer();
    feedSync(name === 'health');
    try { window.scrollTo(0, 0); } catch (e) {}
  }
  window.addEventListener('hashchange', function () { showPage(location.hash.slice(1)); });
  $('navToggle').addEventListener('click', function () {
    const open = $('nav').classList.toggle('open');
    $('navToggle').setAttribute('aria-expanded', open ? 'true' : 'false');
  });
  $('drawerBg').addEventListener('click', closeDrawer);
  $('dClose').addEventListener('click', closeDrawer);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && panelDrawer) closeDrawer(); });

  /* The stream indicator in the top bar is visible on every page. */
  function paintPill(running) {
    const pill = $('streamPill');
    pill.className = 'spill ' + (running ? 'on' : 'off');
    $('pillTxt').textContent = running ? 'STREAMING' : 'STOPPED';
  }

  /* ---- live transcript on Session health: the same /stream the viewers use ---- */
  const FEED_MAX = 200;
  let feedEs = null, feedKey = '', feedOpenEl = null, feedLast = 0, feedWanted = false, feedRetry = 500, feedTimer = null;

  function feedTargets(targets) {
    const sel = $('feedSel');
    const keep = sel.value;
    const live = (targets || []).filter(function (t) { return t.live || t.enabled; });
    sel.innerHTML = '';
    const src = el('option', '', 'Transcription (source)');
    src.value = '';
    sel.appendChild(src);
    live.forEach(function (t) {
      const o = el('option', '', t.language_label + ' (translation)');
      o.value = t.target;
      sel.appendChild(o);
    });
    sel.value = Array.prototype.some.call(sel.options, function (o) { return o.value === keep; }) ? keep : '';
    if (feedKey !== (sel.value || '') && feedWanted) feedConnect();
  }

  function feedLine(text, open) {
    const box = $('feedBox');
    const p = el('p', open ? 'open' : 'settled', text);
    box.appendChild(p);
    while (box.children.length > FEED_MAX) box.removeChild(box.firstChild);
    return p;
  }

  function feedStick() { if ($('feedScroll').checked) { const b = $('feedBox'); b.scrollTop = b.scrollHeight; } }

  function feedApply(msg) {
    const box = $('feedBox');
    if (msg.type === 'snapshot') {
      box.innerHTML = '';
      feedOpenEl = null;
      (msg.lines || []).forEach(function (l) { feedLine(l.text, false); });
      if (msg.open) feedOpenEl = feedLine(msg.open, true);
      if (!box.children.length) box.appendChild(el('div', 'empty', 'Waiting for speech…'));
      feedStick();
      return;
    }
    if (msg.seq && msg.seq <= feedLast) return;
    feedLast = msg.seq || feedLast;
    const empty = box.querySelector('.empty');
    if (empty) empty.remove();
    if (!feedOpenEl) feedOpenEl = feedLine('', true);
    feedOpenEl.textContent = msg.text !== undefined ? msg.text : feedOpenEl.textContent + (msg.delta || '');
    if (msg.final) { feedOpenEl.className = 'settled'; feedOpenEl = null; }
    feedStick();
  }

  function feedClose() {
    if (feedTimer) { clearTimeout(feedTimer); feedTimer = null; }
    if (feedEs) { feedEs.close(); feedEs = null; }
  }

  function feedConnect() {
    feedClose();
    const lang = $('feedSel').value;
    feedKey = lang;
    feedLast = 0;
    feedOpenEl = null;
    const qs = new URLSearchParams(lang ? { stream: 'translation', lang: lang } : { stream: 'transcription' });
    const es = new EventSource('/stream?' + qs.toString());
    feedEs = es;
    $('feedState').textContent = 'Connecting…';
    es.onopen = function () { feedRetry = 500; $('feedState').textContent = 'Live'; };
    es.onmessage = function (ev) {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.type === 'targets') return;
      feedApply(msg);
    };
    es.onerror = function () {
      $('feedState').textContent = 'Reconnecting…';
      es.close();
      if (feedEs === es) feedEs = null;
      if (!feedWanted) return;
      feedTimer = setTimeout(feedConnect, feedRetry);
      feedRetry = Math.min(5000, feedRetry * 2);
    };
  }

  /* Only listen while Session health is on screen. */
  function feedSync(visible) {
    feedWanted = visible;
    if (visible) { if (!feedEs) feedConnect(); } else { feedClose(); $('feedState').textContent = ''; }
  }
  $('feedSel').addEventListener('change', function () { feedConnect(); });

  /* ---- what module scripts may use (app/modules/<id>/static/), loaded after this file ---- */
  window.RelayAdmin = {
    $: $, el: el, api: api, post: post, okMsg: okMsg, alertMsg: alertMsg, agoText: agoText,
    // For the scripts that make their own requests: true when the session has ended and the browser is on its
    // way to the sign-in page.
    sessionEnded: sessionEnded,
    cfg: function () { return cfg; },
    setServiceNames: function (names) { serviceNames = names || {}; },
    clockText: clockText, timerPicker: timerPicker, timerPickerValue: timerPickerValue, timerThresholds: timerThresholds,
    // What module scripts tell the Panels and Alerts pages: timers() and stageDisplay (ProPresenter), serviceOn()
    // (Planning Center).
    panelHooks: {}
  };

  /* ---- boot ---- */
  (async function boot() {
    try {
      const state = await api('/api/admin/state');
      cfg = state.config;
      renderDevices(state.devices, cfg.audio_device, state.device_notice);
      renderKeyState();
      // Module scripts (app/modules/<id>/static/) start from here.
      document.dispatchEvent(new CustomEvent('relay:config'));
      $('font').value = cfg.default_font_px;
      $('history').value = cfg.history_lines;
      $('segIdle').value = cfg.realtime.segment_idle_s;
      $('segMaxIdle').value = cfg.realtime.segment_max_idle_s;
      $('noise').value = cfg.realtime.noise_reduction || '';
      renderMode();
      renderUrls(state.urls);
      renderSecure(state.serving);
      watchUrls(state.urls);
      applyStatus(state.status);
      loadRuns();
    } catch (e) { alertMsg(e.message); }

    showPage(location.hash.slice(1));

    const es = new EventSource('/api/admin/status/stream');
    es.onmessage = function (ev) {
      try { applyStatus(JSON.parse(ev.data)); } catch (e) {}
    };
    // A stream the server refuses (the session ended) is not retried by the browser: ask once, and the shared
    // helper sends the browser to the sign-in page if that is why.
    es.onerror = function () {
      if (es.readyState === 2) api('/api/admin/me').catch(function () {});
    };
  })();
})();
