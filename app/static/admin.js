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

  function renderTargets(targets) {
    renderTargetsSummary(targets);
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
      del.appendChild(btn);
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
      act.appendChild(edit); act.appendChild(del);
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
    $('masterState').textContent = masterText(st.running);
    $('startBtn').disabled = st.running;
    $('stopBtn').disabled = !st.running;
    if (st.running) $('audioDot').className = 'dot ' + (st.speaking ? 'ok' : 'warn');
    paintMeter(st);
  }

  function applyStatus(st) {
    if (!st) return;
    if (st.type === 'meter') { applyMeter(st); return; }
    if (!st.sessions) return;

    if (st.schedule) schedStatusChanged(st.schedule);
    $('masterDot').className = 'dot ' + (st.running ? 'ok' : '');
    $('masterState').textContent = masterText(st.running);
    $('startBtn').disabled = st.running;
    $('stopBtn').disabled = !st.running;
    if (recRunning && !st.running) loadRuns();  // a run just ended: it is downloadable now
    recRunning = !!st.running;
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
      loadRuns();
    } catch (e) { alertMsg(e.message); }

    const es = new EventSource('/api/admin/status/stream');
    es.onmessage = function (ev) {
      try { applyStatus(JSON.parse(ev.data)); } catch (e) {}
    };
  })();
})();
