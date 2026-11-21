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
  const can = relayUI.can;
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

  /* ---- the lane the captions listen to ---- */
  function renderLanes(lanes, selected) {
    const sel = $('lane');
    const note = $('laneNote');
    sel.innerHTML = '';
    if (!lanes.length) {
      const o = document.createElement('option');
      o.textContent = 'No lane is patched';
      sel.appendChild(o);
      sel.disabled = true;
      note.textContent = 'Nothing is patched to a lane yet. Patch an input to a lane on the Audio bus page, then pick it here.';
      note.hidden = false;
      return;
    }
    sel.disabled = false;
    note.hidden = true;
    lanes.forEach(function (l) {
      const o = document.createElement('option');
      o.value = String(l.lane);
      o.textContent = 'Lane ' + l.lane + (l.name ? ': ' + l.name : '');
      sel.appendChild(o);
    });
    sel.value = String(selected);
    if (!sel.value) {
      // The chosen lane is not patched (any more): say so instead of silently showing another one.
      const o = document.createElement('option');
      o.value = String(selected);
      o.textContent = 'Lane ' + selected + ' (not patched)';
      sel.appendChild(o);
      sel.value = String(selected);
    }
  }

  // The lanes are patched on the Audio bus page: read them again each time this page is shown.
  $('page-audio').addEventListener('relay:tab', async function () {
    try {
      const state = await api('/api/admin/state');
      cfg = state.config;
      renderLanes(state.lanes, cfg.audio_lane);
    } catch (e) { alertMsg(e.message); }
  });
  $('lane').addEventListener('change', saveLane);
  $('noise').addEventListener('change', saveLane);

  async function saveLane() {
    try {
      const payload = { audio_lane: parseInt($('lane').value, 10) || 1 };
      // Noise reduction is a realtime setting: a role that cannot change those must not send it (the whole
      // request would be refused).
      if (can('settings.manage')) payload.realtime = { noise_reduction: $('noise').value || null };
      const body = await post('/api/admin/config', payload);
      cfg = body.config;
      okMsg('Input saved.');
    } catch (e) { alertMsg(e.message); }
  }

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

  let lastTargets = [];

  function renderTargets(targets) {
    lastTargets = targets;
    renderTargetsSummary(targets);
    feedTargets(targets);
    const on = targets.filter(function (t) { return t.enabled; }).length;
    $('targetsCount').textContent = on + ' enabled';
    const key = JSON.stringify(targets);
    if (key === lastTargetsKey) return;
    lastTargetsKey = key;

    const grid = $('targets');
    grid.innerHTML = '';
    targets.forEach(function (t) {
      const lab = el('label', 'check');
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = !!t.enabled;
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
    const todo = lastTargets.filter(function (t) { return t.enabled !== enabled; });
    if (!todo.length) return;
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
    if ($('vkey').value.trim()) payload.vercel_api_key = $('vkey').value.trim();
    if (!Object.keys(payload).length) { alertMsg('Nothing to save.'); return; }
    try {
      const body = await post('/api/admin/config', payload);
      cfg = body.config;
      $('apikey').value = '';
      $('vkey').value = '';
      renderKeyState();
      okMsg('Saved.' + (payload.openai_api_key || payload.vercel_api_key ? ' Restart capture to use the new key.' : ''));
    } catch (e) { alertMsg(e.message); }
  });

  async function saveBackupPass(value) {
    try {
      const body = await post('/api/admin/config', { backup_passphrase: value });
      cfg = body.config;
      $('backupPass').value = '';
      renderKeyState();
      okMsg(value ? 'Passphrase set. The next backup opens with it too.' : 'Passphrase removed. Backups open only on this computer.');
    } catch (e) { alertMsg(e.message); }
  }
  $('saveBackupPass').addEventListener('click', function () {
    const v = $('backupPass').value;
    if (v.length < 10) { alertMsg('The passphrase needs at least 10 characters.'); return; }
    saveBackupPass(v);
  });
  $('clearBackupPass').addEventListener('click', function () { saveBackupPass(null); });

  // The one Relay time zone (#526), set under System.
  function relayZoneOptions(zones) {
    const sel = $('relayZone');
    sel.textContent = '';
    zones.forEach(function (z) {
      const o = document.createElement('option');
      o.value = z.id; o.textContent = z.label;
      sel.appendChild(o);
    });
    sel.value = cfg.timezone;
  }

  $('saveAppearance').addEventListener('click', async function () {
    try {
      const body = await post('/api/admin/config', {
        default_font_px: parseInt($('font').value, 10),
        history_lines: parseInt($('history').value, 10),
        timezone: $('relayZone').value,
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
    $('vkeyState').textContent = cfg && cfg.vercel_api_key_set ? '· set ' + cfg.vercel_api_key_hint : '· not set';
    $('vkeyState').className = cfg && cfg.vercel_api_key_set ? 'ok' : 'err';
    if ($('backupPassState')) {
      $('backupPassState').textContent = cfg && cfg.backup_passphrase_set ? '· set' : '· not set';
      $('keyStore').textContent = (cfg && cfg.secret_store) || '';
    }
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
    if (!(await relayUI.confirm('Delete the recording from ' + fmtWhen(started)
        + '? The transcript cannot be recovered.'))) return;
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

  /* ---- schedules (#526) ----
     Named schedules: each a set of weekly windows and one-off dates, optionally also open around the Planning Center
     plan times. Stored ISO (YYYY-MM-DD) and 24-hour (HH:MM) on the server; shown and typed here in US formats only --
     MM/DD/YYYY and h:mm AM/PM -- whatever locale the browser is set to. The server formats every instant it reports,
     in Relay's one time zone, so the panel never does time-zone maths itself. */
  const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  let schedEditing = null;   // id being edited, '' for a new one, null when closed
  let schedItems = [];
  let schedSig = '';
  let schedStatus = null;
  let schedWindows = [];     // the editor's window rows: { read(), box }
  let schedPlanOn = true;    // whether the Planning Center module can supply plan times

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

  // A US-format time ("9:30 AM") typed in a text box, with the browser's own clock picker behind a button, like the
  // date fields: the format never follows the browser's locale. The value is "HH:MM", 24-hour.
  function timeToText(hhmm) {
    const m = /^(\d{2}):(\d{2})$/.exec(hhmm || '');
    if (!m) return '';
    const h = parseInt(m[1], 10);
    return ((h % 12) || 12) + ':' + m[2] + ' ' + (h < 12 ? 'AM' : 'PM');
  }
  function textToTime(text) {
    const m = /^\s*(\d{1,2})(?::(\d{2}))?\s*([ap])?\.?m?\.?\s*$/i.exec(text || '');
    if (!m) return null;
    let h = parseInt(m[1], 10);
    const min = m[2] === undefined ? 0 : parseInt(m[2], 10);
    if (min > 59) return null;
    if (m[3]) {
      if (h < 1 || h > 12) return null;
      h = (h % 12) + (m[3].toLowerCase() === 'p' ? 12 : 0);
    } else if (h > 23) return null;
    return String(h).padStart(2, '0') + ':' + String(min).padStart(2, '0');
  }
  function buildTimeField(label) {
    const el = relayUI.el;
    const text = el('input'); text.type = 'text'; text.maxLength = 8; text.placeholder = 'h:mm AM';
    text.setAttribute('aria-label', label);
    const native = el('input', 'nativedate'); native.type = 'time'; native.tabIndex = -1; native.setAttribute('aria-hidden', 'true');
    const btn = el('button', 'datepick', '\u{1F552}'); btn.type = 'button'; btn.setAttribute('aria-label', 'Pick a time');
    btn.addEventListener('click', function () {
      native.value = textToTime(text.value) || '';
      try { if (native.showPicker) native.showPicker(); else native.click(); } catch (e) { text.focus(); }
    });
    const listeners = [];
    const changed = function () { listeners.forEach(function (fn) { fn(); }); };
    native.addEventListener('change', function () { if (native.value) { text.value = timeToText(native.value); changed(); } });
    // Tidy "930a" or "21:30" into "9:30 AM" once the operator leaves the field.
    text.addEventListener('blur', function () { const t = textToTime(text.value); if (t) text.value = timeToText(t); changed(); });
    return {
      box: el('div', 'datefield', text, btn, native),
      set: function (hhmm) { text.value = timeToText(hhmm || '10:00'); },
      get: function () { return textToTime(text.value); },
      onChange: function (fn) { listeners.push(fn); }
    };
  }

  // A US-format text date with the browser's own calendar behind a button, so the format never follows the locale.
  function buildDateField(label, optional) {
    const el = relayUI.el;
    const text = el('input'); text.type = 'text'; text.inputMode = 'numeric'; text.maxLength = 10; text.placeholder = 'MM/DD/YYYY';
    const native = el('input', 'nativedate'); native.type = 'date'; native.tabIndex = -1; native.setAttribute('aria-hidden', 'true');
    const btn = el('button', 'datepick', '\u{1F4C5}'); btn.type = 'button'; btn.setAttribute('aria-label', 'Pick a date');
    btn.addEventListener('click', function () {
      native.value = usToIso(text.value) || '';
      try { if (native.showPicker) native.showPicker(); else native.click(); } catch (e) { text.focus(); }
    });
    native.addEventListener('change', function () { if (native.value) text.value = isoToUs(native.value); });
    // Tidy 1/5/2027 into 01/05/2027 once the operator leaves the field.
    text.addEventListener('blur', function () { const iso = usToIso(text.value); if (iso) text.value = isoToUs(iso); });
    text.setAttribute('aria-label', label + (optional ? ' (optional)' : ''));
    return { box: el('div', 'datefield', text, btn, native), text: text };
  }

  // One window of the editor: weekly (days, optional first and last date) or one date, and its start and stop time.
  function buildWindowRow(w) {
    const el = relayUI.el;
    const repeat = el('select');
    [['weekly', 'Every week'], ['once', 'One date']].forEach(function (o) {
      const op = el('option', '', o[1]); op.value = o[0]; repeat.appendChild(op);
    });
    repeat.value = w.repeat; repeat.setAttribute('aria-label', 'Repeats');
    const days = el('div', 'days');
    DAY_NAMES.forEach(function (name, i) {
      const lab = el('label'), cb = el('input'); cb.type = 'checkbox'; cb.value = String(i);
      cb.checked = (w.days || []).indexOf(i) !== -1;
      lab.appendChild(cb); lab.appendChild(el('span', '', name)); days.appendChild(lab);
    });
    const from = buildDateField('Starting on', true), until = buildDateField('Ending on', true);
    from.text.value = isoToUs(w.start_date); until.text.value = isoToUs(w.end_date);
    const start = buildTimeField('Start time'), stop = buildTimeField('Stop time');
    start.set(w.start_time); stop.set(w.stop_time);
    const overnight = el('span', 'hint');
    const sync = function () {
      const once = repeat.value === 'once';
      days.hidden = once; untilField.hidden = once;
      fromField.firstChild.textContent = once ? 'Date' : 'Starting on (optional)';
      // The words are added and taken away (a hidden .hint would still be drawn: components.css sets its display).
      overnight.textContent = start.get() && stop.get() && stop.get() < start.get() ? 'The stop time is earlier than the start time, so it stops the next day.' : '';
    };
    repeat.addEventListener('change', sync); start.onChange(sync); stop.onChange(sync);
    const remove = el('button', 'quiet danger', 'Remove this window'); remove.type = 'button';
    const f = relayUI.field;
    const fromField = f('Starting on', from.box), untilField = f('Ending on', until.box);
    const box = el('div', 'schedwin',
      f('Repeats', repeat), fromField, untilField,
      days,
      f('Start time', start.box), f('Stop time', stop.box), overnight,
      remove);
    const row = {
      box: box,
      read: function () {
        const once = repeat.value === 'once';
        const out = { repeat: repeat.value, start_time: start.get(), stop_time: stop.get(), days: [], start_date: null, end_date: null };
        if (!out.start_time || !out.stop_time) throw new Error('Enter the times like 9:30 AM.');
        const a = from.text.value.trim(), z = until.text.value.trim();
        if (a) { out.start_date = usToIso(a); if (!out.start_date) throw new Error('Enter the date as MM/DD/YYYY.'); }
        if (once) {
          if (!out.start_date) throw new Error('Pick the date this runs, as MM/DD/YYYY.');
        } else {
          days.querySelectorAll('input:checked').forEach(function (cb) { out.days.push(parseInt(cb.value, 10)); });
          if (!out.days.length) throw new Error('Pick at least one day.');
          if (z) { out.end_date = usToIso(z); if (!out.end_date) throw new Error('Enter the end date as MM/DD/YYYY.'); }
        }
        if (out.start_time === out.stop_time) throw new Error('Start and stop time must differ.');
        return out;
      }
    };
    remove.addEventListener('click', function () {
      schedWindows = schedWindows.filter(function (r) { return r !== row; });
      box.remove();
    });
    sync();
    return row;
  }

  function addWindowRow(w) {
    const row = buildWindowRow(w || { repeat: 'weekly', days: [0], start_date: null, end_date: null, start_time: '10:00', stop_time: '12:00' });
    schedWindows.push(row);
    $('schedWindows').appendChild(row.box);
  }

  function syncPlan() {
    $('schedPlanTimes').hidden = !$('schedPlan').checked;
    $('schedPlanOff').hidden = !($('schedPlan').checked && !schedPlanOn);
  }
  $('schedPlan').addEventListener('change', syncPlan);
  $('schedAddWindow').addEventListener('click', function () { addWindowRow(); });

  function openSchedForm(item) {
    schedEditing = item ? item.id : '';
    $('schedName').value = item ? item.name : '';
    $('schedWindows').textContent = '';
    schedWindows = [];
    (item ? item.windows : [null]).forEach(function (w) { addWindowRow(w); });
    $('schedPlan').checked = !!(item && item.plan_times);
    $('schedPlanBefore').value = item ? item.plan_before : 30;
    $('schedPlanAfter').value = item ? item.plan_after : 120;
    $('schedEngine').checked = item ? !!item.engine : true;
    const used = $('schedUsed');
    used.textContent = '';
    ((item && item.used_by) || []).forEach(function (u) {
      const li = document.createElement('li'); li.textContent = u.where + ': ' + u.what; used.appendChild(li);
    });
    $('schedUsedBox').hidden = !(item && item.used_by && item.used_by.length);
    $('schedFormTitle').textContent = item ? 'Edit ' + item.name : 'New schedule';
    $('schedSave').textContent = item ? 'Save changes' : 'Add schedule';
    syncPlan();
    $('schedForm').hidden = false;
    $('schedAdd').hidden = true;
    $('schedName').focus();
  }

  function closeSchedForm() {
    schedEditing = null;
    $('schedForm').hidden = true;
    $('schedAdd').hidden = false;
  }

  function readSchedForm() {
    const name = $('schedName').value.trim();
    if (!name) throw new Error('Give the schedule a name.');
    return {
      name: name,
      windows: schedWindows.map(function (r) { return r.read(); }),
      plan_times: $('schedPlan').checked,
      plan_before: parseInt($('schedPlanBefore').value, 10),
      plan_after: parseInt($('schedPlanAfter').value, 10),
      engine: $('schedEngine').checked
    };
  }

  $('schedAdd').addEventListener('click', function () { openSchedForm(null); });
  $('schedCancel').addEventListener('click', closeSchedForm);

  $('schedForm').addEventListener('submit', async function (ev) {
    ev.preventDefault();
    let payload;
    try { payload = readSchedForm(); } catch (e) { alertMsg(e.message); return; }
    const editing = schedEditing;
    const prev = editing ? schedItems.filter(function (s) { return s.id === editing; })[0] : null;
    payload.enabled = prev ? prev.enabled : true;
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
    if (!(await relayUI.confirm('Delete the schedule "' + item.name + '"?'))) return;
    try {
      const body = await api('/api/admin/schedules/' + encodeURIComponent(item.id), { method: 'DELETE' });
      if (schedEditing === item.id) closeSchedForm();
      renderSchedules(body);
      okMsg('Schedule deleted.');
    } catch (e) { alertMsg(e.message); }
  }

  function usedText(list) {
    const by = {};
    list.forEach(function (u) { by[u.where] = (by[u.where] || 0) + 1; });
    return Object.keys(by).map(function (k) { return by[k] > 1 ? k + ' (' + by[k] + ')' : k; }).join(', ');
  }

  function renderSchedules(info) {
    schedItems = info.schedules || [];
    schedPlanOn = !!info.plan_times;
    const zone = (info.timezones || []).filter(function (z) { return z.id === info.timezone; })[0];
    $('schedZone').textContent = zone ? zone.label : (info.timezone || '');
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
      name.textContent = s.name;
      if (s.live && s.enabled) name.insertAdjacentHTML('beforeend', ' <span class="ok">· live window</span>');
      else if (s.expired) name.insertAdjacentHTML('beforeend', ' <span class="muted">· finished</span>');
      what.appendChild(name);
      const sum = document.createElement('div');
      sum.className = 'schedsum';
      sum.textContent = s.summary;
      what.appendChild(sum);
      const use = document.createElement('div');
      use.className = 'schedsum';
      use.textContent = s.used_by.length ? 'Used by: ' + usedText(s.used_by) : 'Not used anywhere yet';
      what.appendChild(use);
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
     One routing grid: each row is something the band can send (an alert button, a stage message, or words typed on
     the sender page), each column a panel or browser screen, and a ticked square sends that row there. Edits go
     straight into alertCfg and are saved whole. The status stream's `alerts_admin` frame brings the saved settings,
     the browser screens open now and each button's state; while there are unsaved edits it never replaces alertCfg.
     The grid is rebuilt only when its rows or columns change, never under a field being typed in, so a click or a
     keystroke is never lost to a redraw. */
  let alertCfg = { auto_stop_s: 120, buttons: [], messages: { presets: [], free: { panels: [], screens: [] }, message_s: 10, banner_s: 300 } };
  let alertScreens = [];
  let alertState = [];
  let alertDirty = false;
  let viewerHosts = [];
  let alertExtraScreens = [];   // screens added or unticked here, kept as columns until the page reloads
  let alertOpenRow = '';        // the row whose options are open: 'b<i>', 'm<i>' or 'free'
  let alertSig = '';
  const ALERT_WORDS = { on: 'Flashing', seen: 'Seen', missed: 'Not seen', cancelled: 'Stopped' };
  const LEVEL_LABELS = { info: 'Info (blue)', attention: 'Attention (amber, flashes)', urgent: 'Urgent (red, flashes)' };
  const LEVEL_SHORT = { info: 'Info', attention: 'Attention', urgent: 'Urgent' };
  const AUTOSTOP_WORDS = { 30: '30 s', 60: '1 min', 120: '2 min', 180: '3 min', 300: '5 min', 600: '10 min' };

  function alertSetDirty(on) {
    alertDirty = on;
    $('alertDirty').hidden = !on;
    renderAlertSetSum();
  }

  function applyAlerts(frame) {
    alertScreens = frame.screens || [];
    alertState = frame.state || [];
    if (!alertDirty) {
      alertCfg = JSON.parse(JSON.stringify(frame.alerts || alertCfg));
      if (!alertCfg.messages) alertCfg.messages = { presets: [], free: { panels: [], screens: [] }, message_s: 10, banner_s: 300 };
      $('alertAutoStop').value = String(alertCfg.auto_stop_s);
      $('alertSignin').checked = !!alertCfg.require_signin;
      $('alertReplies').value = (alertCfg.replies || []).join('\n');
      $('msgSeconds').value = String(alertCfg.messages.message_s);
      $('bannerSeconds').value = String(alertCfg.messages.banner_s);
      renderAlertSetSum();
    }
    renderAlerts();
  }

  function alertStateText(id) {
    const row = alertState.find(function (r) { return r.id === id; });
    if (!row || row.state === 'idle') return { cls: '', text: '' };
    let text = ALERT_WORDS[row.state] || row.state;
    if (row.state === 'on' && row.from) text += ' · from ' + row.from;
    if (row.state === 'seen' && row.seen_on) text += ' on ' + row.seen_on;
    if (row.state === 'seen' && row.reply) text += ' — “' + row.reply + '”';
    return { cls: row.state, text: text };
  }

  function renderAlertSum() {
    const on = alertState.filter(function (r) { return r.state === 'on'; }).length;
    const n = alertCfg.buttons.length;
    const m = alertCfg.messages.presets.length;
    $('alertsSum').innerHTML = '';
    if (on) { $('alertsSum').appendChild(el('span', 'live', on + ' flashing')); return; }
    const parts = [];
    if (n) parts.push(n + (n === 1 ? ' button' : ' buttons'));
    if (m) parts.push(m + (m === 1 ? ' message' : ' messages'));
    $('alertsSum').textContent = parts.join(' · ');
  }

  function renderAlertSetSum() {
    const replies = $('alertReplies').value.split('\n').filter(function (r) { return r.trim(); }).length;
    $('alertSetSum').textContent = [
      'stops after ' + (AUTOSTOP_WORDS[$('alertAutoStop').value] || $('alertAutoStop').value + ' s'),
      replies ? replies + (replies === 1 ? ' reply' : ' replies') : 'no replies',
      $('alertSignin').checked ? 'sign-in to send' : 'anyone with the link sends'
    ].join(' · ');
  }

  // Every row of the grid, in order, with how to show and edit it.
  function alertRows() {
    const rows = [];
    alertCfg.buttons.forEach(function (b, i) { rows.push({ key: 'b' + i, kind: 'button', item: b, index: i }); });
    alertCfg.messages.presets.forEach(function (m, i) { rows.push({ key: 'm' + i, kind: 'message', item: m, index: i }); });
    rows.push({ key: 'free', kind: 'free', item: alertCfg.messages.free });
    return rows;
  }

  // Every column: the panels that have checked in (and any a row still names), then the browser screens a row
  // names, that are open now, or that were added here.
  function alertColumns() {
    const items = alertRows().map(function (r) { return r.item; });
    const cols = panelRows.map(function (r) { return { kind: 'panel', id: r.id, name: r.name, online: !!r.online }; });
    items.forEach(function (it) {
      (it.panels || []).forEach(function (pid) {
        if (!cols.some(function (c) { return c.kind === 'panel' && c.id === pid; })) {
          cols.push({ kind: 'panel', id: pid, name: 'Forgotten panel ' + pid.slice(0, 4), online: false });
        }
      });
    });
    const seen = {};
    const screens = [];
    function addScreen(name) {
      const key = name.toLowerCase();
      if (!name || seen[key]) return;
      seen[key] = true;
      screens.push(name);
    }
    items.forEach(function (it) { (it.screens || []).forEach(addScreen); });
    alertScreens.forEach(addScreen);
    alertExtraScreens.forEach(addScreen);
    screens.sort(function (a, b) { return a.toLowerCase() < b.toLowerCase() ? -1 : 1; });
    const open = alertScreens.map(function (s) { return s.toLowerCase(); });
    screens.forEach(function (name) {
      cols.push({ kind: 'screen', id: name, name: name, online: open.indexOf(name.toLowerCase()) !== -1 });
    });
    return cols;
  }

  function alertHas(item, col) {
    if (col.kind === 'panel') return (item.panels || []).indexOf(col.id) !== -1;
    const key = col.id.toLowerCase();
    return (item.screens || []).some(function (s) { return s.toLowerCase() === key; });
  }

  function alertSetHas(item, col, on) {
    if (col.kind === 'panel') {
      item.panels = (item.panels || []).filter(function (x) { return x !== col.id; });
      if (on) item.panels.push(col.id);
      return;
    }
    const key = col.id.toLowerCase();
    item.screens = (item.screens || []).filter(function (s) { return s.toLowerCase() !== key; });
    if (on) item.screens.push(col.id);
    else if (alertExtraScreens.indexOf(col.id) === -1) alertExtraScreens.push(col.id);   // keep its column
  }

  function viewerBase() {
    // The viewer hosts end in "/" (they are joined to "translation" and the like); these paths start with one.
    return (viewerHosts[0] || location.origin).replace(/\/+$/, '');
  }

  function renderAlerts() {
    renderAlertSum();
    renderAlertLinks();
    const table = $('alertList');
    const cols = alertColumns();
    const rows = alertRows();
    const sig = JSON.stringify([cols, rows.map(function (r) { return r.key; }), alertOpenRow,
      !!(window.RelayAdmin && window.RelayAdmin.panelHooks.stageDisplay)]);
    const typing = table.contains(document.activeElement) &&
      /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName) && document.activeElement.type !== 'checkbox';
    if (sig === alertSig || typing) {
      // Only the live parts: each button's state.
      table.querySelectorAll('[data-astate]').forEach(function (n) {
        const st = alertStateText(n.dataset.astate);
        n.className = 'astate ' + st.cls;
        n.textContent = st.text;
      });
      return;
    }
    alertSig = sig;
    buildAlertGrid(table, rows, cols);
  }

  function forceAlerts() {
    alertSig = '';
    renderAlerts();
  }

  function levelSwatch(level) {
    const sw = el('span', 'alv ' + level);
    sw.title = LEVEL_LABELS[level];
    sw.setAttribute('role', 'img');
    sw.setAttribute('aria-label', LEVEL_SHORT[level]);
    return sw;
  }

  function levelPicker(item, editable, fallback, label) {
    const pick = el('select', 'alevelpick');
    pick.setAttribute('aria-label', label || 'Level');
    ['info', 'attention', 'urgent'].forEach(function (lv) {
      const o = el('option', '', LEVEL_LABELS[lv]);
      o.value = lv;
      pick.appendChild(o);
    });
    pick.value = item.level || fallback;
    pick.disabled = !editable;
    pick.addEventListener('change', function () { item.level = pick.value; alertSetDirty(true); forceAlerts(); });
    return pick;
  }

  function buildAlertGrid(table, rows, cols) {
    const editable = can('alerts.manage');
    const span = Math.max(cols.length, 1) + 2;
    table.innerHTML = '';

    const head = el('thead');
    const hr = el('tr');
    hr.appendChild(el('th', 'aname', 'Send'));
    if (!cols.length) {
      hr.appendChild(el('th', 'acolnone', 'No panels or screens yet. A panel appears here when it checks in; add a browser screen below for a TV or laptop.'));
    }
    cols.forEach(function (c) {
      const th = el('th', 'acol');
      th.scope = 'col';
      const name = el('span', 'acolname');
      const dot = el('span', 'dot' + (c.online ? ' ok' : ''));
      dot.setAttribute('role', 'img');
      dot.setAttribute('aria-label', c.online ? 'Connected' : 'Not connected');
      dot.title = c.online ? 'Connected now' : 'Not connected now';
      name.append(dot, el('span', '', c.name));
      th.appendChild(name);
      if (c.kind === 'panel') th.appendChild(el('span', 'acolkind', 'panel'));
      else {
        const link = relayUI.link('screen link', viewerBase() + '/screen?alert=' + encodeURIComponent(c.name));
        link.className = 'acolkind';
        link.title = 'Open this on the TV or laptop, so it joins as “' + c.name + '”';
        th.appendChild(link);
      }
      hr.appendChild(th);
    });
    hr.appendChild(el('th', 'aopt'));
    head.appendChild(hr);
    table.appendChild(head);

    const body = el('tbody');
    function group(text, note) {
      const tr = el('tr', 'agroup');
      const td = el('th');
      td.colSpan = span;
      const words = el('span', '', text);
      if (note) words.appendChild(el('span', 'muted', ' — ' + note));
      td.appendChild(words);
      tr.appendChild(td);
      body.appendChild(tr);
    }
    let lastKind = '';
    rows.forEach(function (r) {
      if (r.kind !== lastKind) {
        if (r.kind === 'button') group('Alert buttons', 'flash until someone there taps the screen');
        if (r.kind === 'message') group('Stage messages', 'words over the screen, nothing to tap');
        if (r.kind === 'free' && lastKind !== 'message') group('Stage messages', 'words over the screen, nothing to tap');
        lastKind = r.kind;
      }
      const tr = el('tr', 'arow');
      const nameCell = el('th', 'aname');
      nameCell.scope = 'row';
      let label = '';
      if (r.kind === 'button') {
        const b = r.item;
        if (!b.icon) b.icon = 'bell';
        const icons = window.RelayAlertIcons;
        const ic = el('span', 'aicon');
        if (icons) ic.innerHTML = icons.svg(b.icon);
        const name = el('input');
        name.value = b.name;
        name.maxLength = 32;
        name.placeholder = 'FOH booth';
        name.setAttribute('aria-label', 'Alert button name');
        name.disabled = !editable;
        name.addEventListener('input', function () { b.name = name.value; alertSetDirty(true); });
        const st = alertStateText(b.id);
        const state = el('span', 'astate ' + st.cls, st.text);
        if (b.id) state.dataset.astate = b.id;
        nameCell.append(ic, name, levelSwatch(b.level || 'urgent'), state);
        label = b.name || 'New alert button';
      } else if (r.kind === 'message') {
        const m = r.item;
        const text = el('input');
        text.value = m.text;
        text.maxLength = 80;
        text.placeholder = '5 minutes';
        text.setAttribute('aria-label', 'Stage message');
        text.disabled = !editable;
        text.addEventListener('input', function () { m.text = text.value; alertSetDirty(true); });
        nameCell.append(text, levelSwatch(m.level || 'info'));
        if (m.banner) nameCell.appendChild(el('span', 'atag', 'banner'));
        label = m.text || 'New message';
      } else {
        nameCell.append(el('span', 'afree', 'Anything typed on the sender page'), levelSwatch(r.item.level || 'info'));
        label = 'Typed messages';
      }
      tr.appendChild(nameCell);
      if (!cols.length) tr.appendChild(el('td', 'acell'));
      cols.forEach(function (c) {
        const td = el('td', 'acell');
        const box = el('input');
        box.type = 'checkbox';
        box.checked = alertHas(r.item, c);
        box.disabled = !editable;
        box.setAttribute('aria-label', label + ' on ' + c.name);
        box.addEventListener('change', function () { alertSetHas(r.item, c, box.checked); alertSetDirty(true); });
        td.appendChild(box);
        tr.appendChild(td);
      });
      const opt = el('td', 'aopt');
      const open = alertOpenRow === r.key;
      const ob = relayUI.button(open ? 'Close' : 'Options', function () {
        alertOpenRow = open ? '' : r.key;
        forceAlerts();
      }, 'sm quiet');
      ob.setAttribute('aria-expanded', open ? 'true' : 'false');
      opt.appendChild(ob);
      tr.appendChild(opt);
      body.appendChild(tr);
      if (open) body.appendChild(alertOptions(r, span, editable));
    });
    table.appendChild(body);

    const foot = el('tfoot');
    const fr = el('tr');
    fr.appendChild(el('th', 'aname muted', cols.length ? 'Check a screen' : ''));
    if (!cols.length) fr.appendChild(el('td'));
    cols.forEach(function (c) {
      const td = el('td', 'acell');
      const tb = relayUI.button('Test', function () { testAlertTarget(c, tb); }, 'sm');
      tb.disabled = !editable;
      tb.setAttribute('aria-label', 'Test ' + c.name);
      td.appendChild(tb);
      fr.appendChild(td);
    });
    fr.appendChild(el('td'));
    foot.appendChild(fr);
    table.appendChild(foot);
  }

  // The options under a row: what does not fit in the grid itself.
  function alertOptions(r, span, editable) {
    const tr = el('tr', 'aoptrow');
    const td = el('td');
    td.colSpan = span;
    const box = el('div', 'ctl');
    if (r.kind === 'button') {
      const b = r.item;
      const icons = window.RelayAlertIcons;
      if (icons) {
        const pick = el('select', 'aiconpick');
        pick.setAttribute('aria-label', 'Icon on the sender page');
        icons.names.forEach(function (n) {
          const o = el('option', '', icons.label(n));
          o.value = n;
          pick.appendChild(o);
        });
        pick.value = b.icon || 'bell';
        pick.disabled = !editable;
        pick.addEventListener('change', function () { b.icon = pick.value; alertSetDirty(true); forceAlerts(); });
        box.append(el('span', 'muted', 'Icon'), pick);
      }
      box.append(el('span', 'muted', 'Level'), levelPicker(b, editable, 'urgent', 'Alert level'));
      if (window.RelayAdmin && window.RelayAdmin.panelHooks.stageDisplay) {
        const lab = el('label', 'check');
        const cb = el('input');
        cb.type = 'checkbox';
        cb.checked = !!b.stage;
        cb.disabled = !editable;
        cb.addEventListener('change', function () { b.stage = cb.checked; alertSetDirty(true); });
        lab.append(cb, el('span', '', 'Also on the ProPresenter stage display'));
        box.appendChild(lab);
      }
    } else if (r.kind === 'message') {
      const m = r.item;
      box.append(el('span', 'muted', 'Level'), levelPicker(m, editable, 'info', 'Message level'));
      const lab = el('label', 'check');
      const cb = el('input');
      cb.type = 'checkbox';
      cb.checked = !!m.banner;
      cb.disabled = !editable;
      cb.addEventListener('change', function () { m.banner = cb.checked; alertSetDirty(true); forceAlerts(); });
      lab.append(cb, el('span', '', 'Banner (a strip across the top that stays longer)'));
      box.appendChild(lab);
    } else {
      box.append(el('span', 'muted', 'Typed messages are'), levelPicker(r.item, editable, 'info', 'Typed message level'));
      td.appendChild(box);
      td.appendChild(el('div', 'note', 'The sender page shows a box to type in once this row goes to at least one panel or screen.'));
      tr.appendChild(td);
      return tr;
    }
    if (editable) {
      box.appendChild(relayUI.button('Remove', function () {
        if (r.kind === 'button') alertCfg.buttons.splice(r.index, 1);
        else alertCfg.messages.presets.splice(r.index, 1);
        alertOpenRow = '';
        alertSetDirty(true);
        forceAlerts();
      }, 'sm danger'));
    }
    td.appendChild(box);
    tr.appendChild(td);
    return tr;
  }

  async function testAlertTarget(c, btn) {
    btn.disabled = true;
    try {
      const res = await post('/api/admin/alerts/test', c.kind === 'panel' ? { panel: c.id } : { screen: c.name });
      if (res.delivered) okMsg('Test sent to ' + c.name + '. It shows for a few seconds.');
      else alertMsg(c.name + ' is not connected, so the test showed nowhere. ' +
        (c.kind === 'panel' ? 'Check that the panel is on and on the network.' : 'Open its screen link on that TV or laptop first.'));
    } catch (e) { alertMsg(e.message); }
    btn.disabled = false;
  }

  function renderAlertLinks() {
    const box = $('alertLinks');
    if (!box) return;
    // With sign-in required the sender page lives on the operator panel's own https:// address, this one.
    const url = (alertCfg.require_signin ? location.origin : viewerBase()) + '/alert';
    if (box.dataset.url === url) return;
    box.dataset.url = url;
    box.innerHTML = '';
    box.append(el('span', 'muted', 'Sender page for the band’s phones'), relayUI.link(url, url));
  }

  $('alertAdd').addEventListener('click', function () {
    alertCfg.buttons.push({ name: '', icon: 'bell', level: 'urgent', panels: [], screens: [] });
    alertSetDirty(true);
    forceAlerts();
    const inputs = $('alertList').querySelectorAll('input[aria-label="Alert button name"]');
    if (inputs.length) inputs[inputs.length - 1].focus();
  });
  $('msgAdd').addEventListener('click', function () {
    alertCfg.messages.presets.push({ text: '', banner: false, level: 'info', panels: [], screens: [] });
    alertSetDirty(true);
    forceAlerts();
    const inputs = $('alertList').querySelectorAll('input[aria-label="Stage message"]');
    if (inputs.length) inputs[inputs.length - 1].focus();
  });
  function addAlertScreen() {
    const name = $('alertScreenName').value.replace(/\s+/g, ' ').trim();
    if (!name) { $('alertScreenName').focus(); return; }
    const taken = alertColumns().some(function (c) { return c.kind === 'screen' && c.name.toLowerCase() === name.toLowerCase(); });
    if (!taken) alertExtraScreens.push(name);
    $('alertScreenName').value = '';
    forceAlerts();
    okMsg(taken ? 'There is already a screen called ' + name + '.' : 'Added ' + name + '. Tick its squares, then open its screen link on that TV or laptop.');
  }
  $('alertScreenAdd').addEventListener('click', addAlertScreen);
  $('alertScreenName').addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); addAlertScreen(); } });
  ['msgSeconds', 'bannerSeconds'].forEach(function (id) {
    $(id).addEventListener('input', function () { alertSetDirty(true); });
  });
  $('alertAutoStop').addEventListener('change', function () {
    alertCfg.auto_stop_s = parseInt($('alertAutoStop').value, 10);
    alertSetDirty(true);
  });
  $('alertReplies').addEventListener('input', function () { alertSetDirty(true); });
  $('alertSignin').addEventListener('change', function () {
    alertCfg.require_signin = $('alertSignin').checked;
    alertSetDirty(true);
    renderAlertLinks();
  });
  $('alertSave').addEventListener('click', async function () {
    try {
      const frame = await post('/api/admin/alerts', {
        auto_stop_s: parseInt($('alertAutoStop').value, 10),
        require_signin: $('alertSignin').checked,
        replies: $('alertReplies').value.split('\n').map(function (r) { return r.trim(); }).filter(Boolean),
        messages: {
          message_s: parseInt($('msgSeconds').value, 10), banner_s: parseInt($('bannerSeconds').value, 10),
          free: alertCfg.messages.free,
          presets: alertCfg.messages.presets.map(function (m) {
            const out = { text: m.text, banner: !!m.banner, level: m.level || 'info', panels: m.panels,
                          screens: m.screens };
            if (m.id) out.id = m.id;
            return out;
          })
        },
        buttons: alertCfg.buttons.map(function (b) {
          const out = { name: b.name, icon: b.icon || 'bell', panels: b.panels, screens: b.screens,
                        stage: !!b.stage, level: b.level || 'urgent' };
          if (b.id) out.id = b.id;
          return out;
        })
      });
      alertSetDirty(false);
      if (document.activeElement && $('alertList').contains(document.activeElement)) document.activeElement.blur();
      applyAlerts(frame);
      forceAlerts();
      okMsg('Alerts saved');
    } catch (e) { alertMsg(e.message); }
  });

  /* ---- alert history (#217) ---- */
  let alertHistLast = 0;     // the oldest row shown, to page from
  function alertHistRow(e) {
    const tr = el('tr');
    const when = el('td', 'when', e.at ? new Date(e.at).toLocaleString() : '');
    const who = el('td', 'who', e.sender || 'Someone');
    const what = el('td', 'what', e.kind === 'message' ? e.text : e.name);
    const kind = e.kind === 'message' ? 'Message' : 'Alert';
    what.appendChild(el('small', '', [kind, e.level, e.targets.length ? 'to ' + e.targets.join(', ') : ''].filter(Boolean).join(' · ')));
    let seen = '';
    if (e.outcome === 'seen') {
      seen = 'Seen on ' + e.seen_on + ' after ' + e.seen_after_s + ' s' + (e.reply ? ' \u2014 \u201c' + e.reply + '\u201d' : '');
    } else if (e.outcome === 'missed') seen = 'Not seen';
    else if (e.outcome === 'cancelled') seen = 'Stopped by the sender';
    tr.append(when, who, what, el('td', 'what', seen));
    return tr;
  }
  async function loadAlertHistory(more) {
    // $() never returns null (it hands back a stand-in), so ask the document: the section is only there
    // for people with alerts.history.
    const list = document.getElementById('alertHistList');
    if (!list) return;
    try {
      const r = await fetch('/api/admin/alerts/history' + (more && alertHistLast ? '?before=' + alertHistLast : ''));
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      if (!more) list.textContent = '';
      data.entries.forEach(function (e) { list.appendChild(alertHistRow(e)); alertHistLast = e.id; });
      if (!list.childElementCount) {
        const tr = el('tr'); const td = el('td', 'muted', 'Nothing sent yet.'); td.colSpan = 4; tr.appendChild(td); list.appendChild(tr);
      }
      $('alertHistKeep').textContent = String(data.keep_days);
      $('alertHistMoreRow').hidden = !data.more;
    } catch (e) {
      list.textContent = '';
      const tr = el('tr'); const td = el('td', 'muted', 'The history could not be read.'); td.colSpan = 4; tr.appendChild(td); list.appendChild(tr);
    }
  }
  if (document.getElementById('alertHistList')) {
    $('alertHistMore').addEventListener('click', function () { loadAlertHistory(true); });
    window.addEventListener('hashchange', function () { if (location.hash === '#alerts') loadAlertHistory(false); });
    if (location.hash === '#alerts') loadAlertHistory(false);
  }

  /* ---- panels ---- */
  let panelRows = [];
  let panelsAt = 0;          // when panelRows arrived: "last seen" counts on from here
  let panelDirty = false;
  let panelBoards = [];      // one row per kind of panel: {board, label, firmware, online, outdated}

  const el = relayUI.el;

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
        if (!(await relayUI.confirm('Remove the ' + b.label + ' firmware held by Relay?'))) return;
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

  const field = relayUI.field;

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

    // The lock and the return to the operator's choice (#126). The PIN is never sent back to the page: it shows only
    // whether there is one, and an empty field leaves it as it is.
    form.appendChild(el('h3', 'dhead', 'At the screen'));
    const lockBox = document.createElement('input');
    lockBox.type = 'checkbox';
    lockBox.checked = !!row.lock;
    const lockLab = el('label', 'check');
    lockLab.appendChild(lockBox);
    lockLab.appendChild(el('span', '', 'Lock: nobody at the screen can change what it shows'));
    form.appendChild(lockLab);
    const lockFields = el('div');
    const pin = el('input');
    pin.type = 'password'; pin.inputMode = 'numeric'; pin.maxLength = 8; pin.autocomplete = 'new-password';
    pin.pattern = '[0-9]{4,8}'; pin.style.maxWidth = '140px';
    pin.placeholder = row.pin_set ? 'PIN set (unchanged)' : 'No PIN';
    const pfield = field('PIN to unlock at the screen', pin, 'panelPin-' + row.id);
    let clearPin = null;
    if (row.pin_set) {
      clearPin = document.createElement('input');
      clearPin.type = 'checkbox';
      const cl = el('label', 'check');
      cl.appendChild(clearPin);
      cl.appendChild(el('span', '', 'Remove the PIN'));
      pfield.appendChild(cl);
    }
    pfield.appendChild(el('div', 'note', '4 to 8 digits. Without one, only you can unlock it, from here. The PIN reaches '
      + 'the panel over plain HTTP, like the Planning Center key: it stops someone at the screen, not someone reading the network.'));
    lockFields.appendChild(pfield);
    const unlockMin = el('input');
    unlockMin.type = 'number'; unlockMin.min = 1; unlockMin.max = 60; unlockMin.step = 1; unlockMin.style.maxWidth = '90px';
    unlockMin.value = row.unlock_min || 5;
    lockFields.appendChild(field('Locks again after this many minutes with no touch', unlockMin, 'panelUnlock-' + row.id));
    form.appendChild(lockFields);
    const backBox = document.createElement('input');
    backBox.type = 'checkbox';
    backBox.checked = !!row.return_min;
    const backLab = el('label', 'check');
    backLab.appendChild(backBox);
    backLab.appendChild(el('span', '', 'Go back to the feeds set here after a while with no touch'));
    form.appendChild(backLab);
    const backFields = el('div');
    const backMin = el('input');
    backMin.type = 'number'; backMin.min = 1; backMin.max = 60; backMin.step = 1; backMin.style.maxWidth = '90px';
    backMin.value = row.return_min || 10;
    const bfield = field('Minutes with no touch', backMin, 'panelReturn-' + row.id);
    bfield.appendChild(el('div', 'note', 'Puts back Feed 1 and Feed 2 above, one feed or two, after a change made at the '
      + 'screen. Needs feeds set here' + (row.idle_s === null || row.idle_s === undefined
        ? ', and panel firmware that says how long it has gone untouched.' : '.')));
    backFields.appendChild(bfield);
    form.appendChild(backFields);
    const lockSync = function () {
      lockFields.hidden = !lockBox.checked;
      backFields.hidden = !backBox.checked;
      if (clearPin) pin.disabled = clearPin.checked;
    };
    [lockBox, backBox].concat(clearPin ? [clearPin] : []).forEach(function (n) { n.addEventListener('change', lockSync); });
    lockSync();

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
      if (!(await relayUI.confirm('Forget ' + row.name + '? A panel that is still on will reappear, unnamed.'))) return;
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
      if (lockBox.checked !== !!row.lock) out.lock = lockBox.checked;
      if (lockBox.checked) {
        if (clearPin && clearPin.checked) out.pin = null;
        else if (pin.value) {
          if (!/^[0-9]{4,8}$/.test(pin.value)) { alertMsg('The PIN must be 4 to 8 digits.'); return; }
          out.pin = pin.value;
        }
        const um = Number(unlockMin.value);
        if (!Number.isInteger(um) || um < 1 || um > 60) { alertMsg('Locks again after 1 to 60 minutes.'); return; }
        if (um !== (row.unlock_min || 5)) out.unlock_min = um;
      }
      if (backBox.checked) {
        const bm = Number(backMin.value);
        if (!Number.isInteger(bm) || bm < 1 || bm > 60) { alertMsg('Go back after 1 to 60 minutes.'); return; }
        if (bm !== row.return_min) out.return_min = bm;
      } else if (row.return_min) {
        out.return_min = null;
      }
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
          && !(await relayUI.confirm('Turning the screen restarts ' + row.name + '. Its captions stop for about half a minute.'))) {
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
    if (row.lock) kv(dev, 'Screen', 'Locked' + (row.pin_set ? ', PIN to unlock' : ''));
    if (row.idle_s !== null && row.idle_s !== undefined) kv(dev, 'Last touched', agoText(row.idle_s));
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
      now.addEventListener('click', async function () {
        if (await relayUI.confirm('Restart ' + row.name + ' now? Its captions stop for about half a minute.')) askUpdate(row.id, true);
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
        if (!(await relayUI.confirm('Restart ' + row.name + ' now? Its captions stop for about half a minute.'))) return;
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
    paintModulePills(st.pills);
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
      tb.innerHTML = '<tr><td colspan="6" class="muted">Not started.</td></tr>';
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
          '<td class="' + (dropped ? 'err' : 'muted') + '">' + dropped + '</td>' +
          '<td class="muted">' + (s.cost_usd == null ? '' : '$' + s.cost_usd.toFixed(2)) + '</td>';
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
  /* ---- sound level and spectrum (#149): settings, a live reading and the RTA, on the Audio input page ---- */
  (function metering() {
    if (!$('mtList')) return;
    const el = relayUI.el;
    let meters = [], busy = false;
    const num = function (v) { return typeof v === 'number' ? v.toFixed(1) : '—'; };
    function input(type, value, attrs) { return el('input', Object.assign({ type: type, value: value }, attrs || {})); }
    function lbl(text, control) { return el('label', '', text + ' ', control); }
    function drawBands(c, st) {
      const g = c.getContext('2d'); g.clearRect(0, 0, c.width, c.height);
      if (st.state !== 'ok' || !Array.isArray(st.bands)) return;
      const bw = c.width / 31;
      for (let i = 0; i < 31; i++) {
        const db = +st.bands[i], h = Math.round(Math.max(0, Math.min(1, (db - 20) / 100)) * (c.height - 14));
        g.fillStyle = db >= st.limit_db ? '#dc2626' : (db >= st.warn_db ? '#f59e0b' : '#22c55e');
        g.fillRect(i * bw + 1, c.height - 14 - h, bw - 2, h);
      }
      g.fillStyle = getComputedStyle(document.body).color; g.font = '11px system-ui,sans-serif';
      (st.labels || []).forEach(function (t, i) { if (i % 3 === 1) g.fillText(t, i * bw, c.height - 2); });
    }
    function card(m) {
      const f = {
        on: input('checkbox', ''), name: input('text', m.name, { maxLength: 40 }), lane: input('number', m.lane, { min: 1, max: 32 }),
        kind: el('select', '', el('option', { value: 'room', text: 'A measurement mic in the room' }),
                 el('option', { value: 'mix', text: "The desk's mix (not room SPL)" })),
        warn: input('number', m.warn_db, { min: 40, max: 140, step: 1 }), limit: input('number', m.limit_db, { min: 40, max: 140, step: 1 }),
        cal: input('number', m.calibration_db, { min: 60, max: 160, step: 0.1 }), ref: input('number', 94, { min: 30, max: 140, step: 0.1 })
      };
      f.on.checked = !!m.enabled; f.kind.value = m.kind;
      const dot = el('span', 'dot'), state = el('span', '', 'Off'), read = el('div', 'meterread', '—');
      const canvas = el('canvas'); canvas.width = 620; canvas.height = 140; canvas.className = 'mtrta';
      canvas.setAttribute('role', 'img'); canvas.setAttribute('aria-label', 'Spectrum in third-octave bands');
      const note = el('div', 'note');
      const node = el('div', 'mtcard', el('h4', '', m.name),
        el('div', 'row', lbl('On', f.on), lbl('Name', f.name), lbl('Lane', f.lane), lbl('Input is', f.kind)),
        el('div', 'row', lbl('Warn at (dB)', f.warn), lbl('Limit at (dB)', f.limit), lbl('Calibration: 0 dBFS = (dB SPL)', f.cal)),
        el('div', 'row', lbl('Calibrator reads (dB)', f.ref),
          relayUI.button('Set calibration from the tone now playing', async function () {
            try {
              const r = await post('/api/admin/metering/' + encodeURIComponent(m.id) + '/calibrate', { reference_db: +f.ref.value });
              meters = r.config.meters; render(r.status);
            } catch (e) { alertMsg(e.message); }
          }),
          relayUI.button('Remove', function () { meters = collect().filter(function (x) { return x.id !== m.id; }); render([]); }, 'sm')),
        el('div', 'pill', dot, state), read, canvas, note);
      node._get = function () {
        return { id: m.id, name: f.name.value, enabled: f.on.checked, lane: +f.lane.value, kind: f.kind.value,
          warn_db: +f.warn.value, limit_db: +f.limit.value, calibration_db: +f.cal.value };
      };
      node._draw = function (st) {
        const on = st.state === 'ok';
        dot.className = 'dot' + (on ? ' ok' : '');
        state.textContent = st.state === 'off' ? 'Off' : (on ? 'Measuring' : 'No signal on lane ' + st.lane);
        read.textContent = on ? 'A ' + num(st.a_slow) + ' slow · ' + num(st.a_fast) + ' fast   C ' + num(st.c_slow) + ' slow   Leq(A) 1 min ' + num(st.leq_a) + ' dB' : '—';
        const notes = [];
        if (st.state !== 'off' && st.kind === 'mix') notes.push('This is the mix level, not the SPL in the room.');
        if (st.state !== 'off' && !st.calibrated) notes.push('Not calibrated: levels are relative until you set the calibration.');
        if (st.dropped) notes.push(st.dropped + ' blocks of audio were skipped because the analysis fell behind.');
        note.textContent = notes.join(' ');
        drawBands(canvas, st);
      };
      node._id = m.id;
      return node;
    }
    function collect() { return Array.prototype.map.call($('mtList').children, function (n) { return n._get(); }); }
    function render(status) {
      const list = $('mtList'); list.textContent = '';
      meters.forEach(function (m) { list.appendChild(card(m)); });
      if (!meters.length) list.appendChild(el('p', 'note', 'No meters yet. Add one to measure a lane.'));
      draw(status || []);
    }
    function draw(status) {
      Array.prototype.forEach.call($('mtList').children, function (n) {
        const st = (status || []).filter(function (s) { return s.id === n._id; })[0];
        if (n._draw) n._draw(st || { state: 'off' });
      });
    }
    async function load() { const r = await api('/api/admin/metering'); meters = r.config.meters; render(r.status); }
    $('mtAdd').addEventListener('click', function () {
      const taken = collect().map(function (m) { return m.id; });
      let n = 1; while (taken.indexOf('m' + n) >= 0) n++;
      meters = collect(); meters.push({ id: 'm' + n, name: n === 1 ? 'Room' : 'Meter ' + n, enabled: true, lane: 1, kind: 'room', warn_db: 85, limit_db: 95, calibration_db: 110 });
      render([]);
    });
    $('mtSave').addEventListener('click', async function () {
      try { const r = await post('/api/admin/metering', { meters: collect() }); meters = r.config.meters; render(r.status); $('mtNote').textContent = 'Saved.'; }
      catch (e) { alertMsg(e.message); }
    });
    load().catch(function () {});
    setInterval(async function () {
      const pg = $('page-audio');
      if (busy || document.hidden || !pg || pg.hidden) return;
      busy = true;
      try { draw((await api('/api/admin/metering')).status); } catch (e) { /* the next tick tries again */ } finally { busy = false; }
    }, 1000);
  })();

  /* ---- stream mix report (#149): the setting, the saved sessions and each one's verdicts ---- */
  (function mixreport() {
    if (!$('mrOn')) return;
    const IDS = ['mrOn', 'mrLeft', 'mrRight', 'mrRehearsal', 'mrTarget', 'mrTol', 'mrPeak', 'mrSilence'];
    function fill(c) {
      $('mrOn').checked = !!c.enabled; $('mrLeft').value = c.left; $('mrRight').value = c.right;
      $('mrRehearsal').checked = !!c.rehearsal; $('mrTarget').value = c.target_lufs; $('mrTol').value = c.tolerance_lu;
      $('mrPeak').value = c.peak_limit_dbfs; $('mrSilence').value = c.silence_dbfs;
    }
    function state(st) {
      const l = st.latest;
      $('mrState').textContent = !st.running ? 'Not recording.' : (st.error || (l
        ? 'Recording: ' + (l.m === null ? '—' : l.m) + ' LUFS momentary, peak ' + l.pk + ' dBFS, ' + st.rows + ' seconds saved.'
        : 'Recording, waiting for sound on the stream mix.'));
    }
    function sessions(list, keep) {
      const sel = $('mrSessions'); sel.textContent = '';
      list.forEach(function (s) {
        const o = document.createElement('option'); o.value = s.id;
        o.textContent = (s.started ? new Date(s.started * 1000).toLocaleString() : s.id) + ' · ' + s.seconds + ' s';
        sel.appendChild(o);
      });
      if (keep && list.some(function (s) { return s.id === keep; })) sel.value = keep;
    }
    async function load() {
      const r = await api('/api/admin/mixreport');
      fill(r.config); state(r.status); sessions(r.sessions, $('mrSessions').value);
    }
    $('mrSave').addEventListener('click', async function () {
      try {
        const r = await post('/api/admin/mixreport', {
          enabled: $('mrOn').checked, left: +$('mrLeft').value, right: +$('mrRight').value,
          rehearsal: $('mrRehearsal').checked, target_lufs: +$('mrTarget').value, tolerance_lu: +$('mrTol').value,
          peak_limit_dbfs: +$('mrPeak').value, silence_dbfs: +$('mrSilence').value });
        fill(r.config); state(r.status);
      } catch (e) { alertMsg(e.message); }
    });
    function graph(series) {
      const c = $('mrGraph'), g = c.getContext('2d'); g.clearRect(0, 0, c.width, c.height);
      const pts = series.filter(function (p) { return typeof p.m === 'number'; });
      c.hidden = pts.length < 2;
      if (c.hidden) return;
      const lo = -40, hi = 0, x = function (i) { return i / (pts.length - 1) * (c.width - 2) + 1; };
      const y = function (v) { return c.height - 4 - Math.max(0, Math.min(1, (v - lo) / (hi - lo))) * (c.height - 8); };
      g.strokeStyle = getComputedStyle(document.body).color; g.globalAlpha = 0.5; g.lineWidth = 1;
      g.beginPath(); pts.forEach(function (p, i) { i ? g.lineTo(x(i), y(p.m)) : g.moveTo(x(i), y(p.m)); }); g.stroke();
      g.globalAlpha = 1; g.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--ok').trim(); g.lineWidth = 2;
      g.beginPath(); pts.forEach(function (p, i) { const v = typeof p.s === 'number' ? p.s : p.m; i ? g.lineTo(x(i), y(v)) : g.moveTo(x(i), y(v)); }); g.stroke();
    }
    $('mrShow').addEventListener('click', async function () {
      const id = $('mrSessions').value; if (!id) return;
      const box = $('mrReport'); box.textContent = '';
      try {
        const r = await api('/api/admin/mixreport/sessions/' + encodeURIComponent(id) + '?rehearsal=' + ($('mrIncl').checked ? 1 : 0));
        const rep = r.report, num = function (v, u) { return typeof v === 'number' ? v + ' ' + u : '—'; };
        const head = document.createElement('div'); head.className = 'meterread';
        head.textContent = 'Overall: ' + rep.overall.toUpperCase() + ' · ' + rep.seconds + ' s of sound · integrated ' + num(rep.integrated_lufs, 'LUFS') +
          ' · peak ' + num(rep.peak_dbfs, 'dBFS') + (r.rehearsal_seconds && !$('mrIncl').checked ? ' · ' + r.rehearsal_seconds + ' s of rehearsal left out' : '');
        box.appendChild(head);
        rep.verdicts.forEach(function (v) {
          const d = document.createElement('div'); d.className = 'note';
          d.textContent = (v.status === 'pass' ? '✓ ' : (v.status === 'warn' ? '! ' : '✗ ')) + v.check + ': ' + v.text;
          box.appendChild(d);
        });
        graph(r.series);
        $('mrCsv').href = '/api/admin/mixreport/sessions/' + encodeURIComponent(id) + '/csv'; $('mrCsv').hidden = false;
      } catch (e) { box.textContent = e.message; }
    });
    load().catch(function () {});
    setInterval(async function () {
      const pg = $('page-audio');
      if (document.hidden || !pg || pg.hidden) return;
      try { state((await api('/api/admin/mixreport')).status); } catch (e) { /* the next tick tries again */ }
    }, 3000);
  })();

  const PAGES = Array.prototype.map.call(document.querySelectorAll('.page[data-page]'), function (n) { return n.dataset.page; });

  /* A hash can name a tab of a page: "#network/map". A tab's own id or its old page id ("#unifi", "#network_map")
     opens the page it is on. The page hears which tab through a "relay:tab" event; '' means its first. */
  function where(hash) {
    const parts = String(hash || '').split('/');
    let name = parts[0], tab = parts[1] || '';
    if (PAGES.indexOf(name) === -1 && name) {
      const t = Array.prototype.find.call(document.querySelectorAll('.page[data-page] [role=tab][data-tab]'), function (b) {
        return b.dataset.tab === name || b.dataset.alias === name;
      });
      if (t) { tab = t.dataset.tab; name = t.closest('.page[data-page]').dataset.page; }
    }
    return { name: name, tab: tab };
  }

  function showPage(hash) {
    const w = where(hash);
    let name = w.name;
    if (PAGES.indexOf(name) === -1) name = PAGES[0];
    document.querySelectorAll('.page[data-page]').forEach(function (n) { n.hidden = n.dataset.page !== name; });
    document.querySelectorAll('nav a[data-page]').forEach(function (a) {
      const here = a.dataset.page === name || (a.dataset.pages || '').split(' ').indexOf(name) !== -1;   // an entry whose pages are tabs holds them all
      if (here) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
      a.classList.toggle('on', here);
    });
    $('acctLink').classList.toggle('on', name === 'account');
    if (name === 'account') $('acctLink').setAttribute('aria-current', 'page'); else $('acctLink').removeAttribute('aria-current');
    if (window.RelayShell) window.RelayShell.page(name);   // breadcrumb, open parent, close the phone drawer (shell.js)
    if (panelDrawer && name !== 'panels') closeDrawer();
    feedSync(name === 'health');
    const pg = $('page-' + name);
    if (pg) pg.dispatchEvent(new CustomEvent('relay:tab', { detail: name === w.name ? w.tab : '' }));
    try { window.scrollTo(0, 0); $('inset').scrollTop = 0; } catch (e) {}
  }
  window.addEventListener('hashchange', function () { showPage(location.hash.slice(1)); });
  $('drawerBg').addEventListener('click', closeDrawer);
  $('dClose').addEventListener('click', closeDrawer);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && panelDrawer) closeDrawer(); });

  /* The stream indicator in the top bar is visible on every page. */
  // Pills the modules that are on put in the top bar. Everything is set as text.
  function paintModulePills(list) {
    const box = $('modulePills');
    box.textContent = '';
    (Array.isArray(list) ? list : []).forEach(function (p) {
      const a = document.createElement('a');
      a.className = 'spill ' + (p.level === 'error' ? 'off' : 'on');   // a module's problem shows red
      a.href = typeof p.href === 'string' && /^#[a-z0-9_-]+$/i.test(p.href) ? p.href : '#';
      const dot = document.createElement('span');
      dot.className = 'dot';
      const txt = document.createElement('span');
      txt.textContent = String(p.text || '') + (typeof p.count === 'number' ? ' · ' + p.count : '');
      a.appendChild(dot);
      a.appendChild(txt);
      box.appendChild(a);
    });
  }

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
      renderLanes(state.lanes, cfg.audio_lane);
      renderKeyState();
      // Module scripts (app/modules/<id>/static/) start from here.
      document.dispatchEvent(new CustomEvent('relay:config'));
      $('font').value = cfg.default_font_px;
      $('history').value = cfg.history_lines;
      relayZoneOptions(state.timezones || []);
      $('segIdle').value = cfg.realtime.segment_idle_s;
      $('segMaxIdle').value = cfg.realtime.segment_max_idle_s;
      $('noise').value = cfg.realtime.noise_reduction || '';
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
