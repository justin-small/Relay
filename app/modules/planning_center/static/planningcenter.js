/* The Planning Center admin page (a module, #157): the token, the service types Relay follows, today's plan and
   LIVE state, and the presses made from panels. Loaded by admin.html only while the module is on; uses the admin
   page's helpers through window.RelayAdmin. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-planningcenter')) return;
  const $ = R.$, el = R.el, api = R.api, post = R.post, okMsg = R.okMsg, alertMsg = R.alertMsg, agoText = R.agoText;
  function C() { return R.cfg(); }

  /* ---- Planning Center ----
     The ID and secret go up once and never come back: C().planning_center only
     says whether a token is saved and which service types are followed. */
  let pcTypes = null;          // the account's service types, as last read from Planning Center
  let pcPending = [];          // ids ticked in the list, applied by "Save selection"
  let pcDirty = false;         // ticks the operator has made but not saved
  let pcLoading = false;
  let pcError = '';            // why the last read from Planning Center failed, "" when it worked

  function pcFollowed() { return ((C() && C().planning_center && C().planning_center.service_types) || []).slice(); }

  function renderPc() {
    const pc = (C() && C().planning_center) || { connected: false, service_types: [] };
    // A saved token only means a token is saved. "connected" is said only while reads work.
    const failing = pc.connected && !!pcError;
    $('pcConnSum').textContent = failing ? 'not working' : (pc.connected ? 'connected' : '');
    $('pcDisconnect').hidden = !pc.connected;
    $('pcTest').disabled = !pc.connected && !($('pcAppId').value.trim() && $('pcSecret').value.trim());
    $('pcState').textContent = failing
      ? pcError
      : pc.connected
        ? (pcTypes ? 'Connected. The service type list refreshes from Planning Center every minute.'
                   : 'A token is saved. Reading service types from Planning Center…')
        : 'Not connected.';
    $('pcState').className = 'note ' + (failing ? 'err' : (pc.connected ? 'ok' : ''));
    $('pcNotesRow').hidden = !pc.connected;
    $('pcDisplay').hidden = !pc.connected;
    $('pcNotes').checked = !!pc.show_notes;
    renderPcTypes();
  }

  function renderPcTypes() {
    const pc = (C() && C().planning_center) || { connected: false };
    const list = $('pcTypeList');
    if (!pc.connected || !pcTypes) { $('pcTypes').hidden = true; return; }
    $('pcTypes').hidden = false;
    list.innerHTML = '';
    if (!pcTypes.length) {
      list.appendChild(el('div', 'muted', 'This account has no service types.'));
    }
    pcTypes.forEach(function (t) {
      const label = document.createElement('label');
      label.className = 'check';
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.value = t.id;
      box.checked = pcPending.indexOf(t.id) !== -1;
      box.addEventListener('change', function () {
        pcPending = Array.prototype.slice.call(list.querySelectorAll('input:checked'))
          .map(function (n) { return n.value; });
        pcDirty = JSON.stringify(pcPending.slice().sort()) !== JSON.stringify(pcFollowed().sort());
        paintPcSelection();
      });
      label.appendChild(box);
      label.appendChild(el('span', '', t.name));
      list.appendChild(label);
    });
    paintPcSelection();
  }

  function paintPcSelection() {
    $('pcTypesSave').disabled = !pcDirty;
    $('pcTypesNote').textContent = pcDirty
      ? 'Unsaved changes. Save selection to apply them.'
      : (pcFollowed().length ? pcFollowed().length + ' followed.' : 'None followed yet.');
  }

  /* Read the service types from Planning Center. Quiet reads (the periodic refresh)
     never interrupt with an error and never overwrite ticks that are not saved yet. */
  async function loadPcTypes(quiet) {
    if (pcLoading || !(C() && C().planning_center && C().planning_center.connected)) return;
    pcLoading = true;
    try {
      const body = await post('/api/admin/planning-center/service-types/refresh');
      C().planning_center = body.planning_center;
      pcTypes = body.service_types || [];
      pcError = '';
      if (!pcDirty) pcPending = pcFollowed();
      renderPc();
    } catch (e) {
      // The server says whether the token was refused or Planning Center could not be reached.
      // Show that on the page; a quiet read never pops up, and unsaved ticks are left alone.
      pcError = e.message || 'Planning Center could not be read.';
      if (!quiet) alertMsg(pcError);
      renderPc();
    } finally { pcLoading = false; }
  }

  async function pcSaveFields() {
    const id = $('pcAppId').value.trim(), secret = $('pcSecret').value.trim();
    if (!id && !secret) return;
    const body = await post('/api/admin/planning-center', { app_id: id, secret: secret });
    C().planning_center = body.planning_center;
    $('pcAppId').value = ''; $('pcSecret').value = '';
    // A different token means a different account: its types and ticks start over.
    pcTypes = null; pcPending = pcFollowed(); pcDirty = false; pcError = '';
  }

  $('pcSave').addEventListener('click', async function () {
    try {
      await pcSaveFields();
      renderPc();
      await loadPcTypes(false);
      if (!pcError) okMsg('Saved. Choose the service types to follow below.');
    } catch (e) { alertMsg(e.message); }
  });

  $('pcTest').addEventListener('click', async function () {
    try {
      await pcSaveFields();
      renderPc();
      await loadPcTypes(false);
      if (!pcError) okMsg('Connected to Planning Center.');
    } catch (e) { alertMsg(e.message); }
  });

  $('pcTypesSave').addEventListener('click', async function () {
    $('pcTypesSave').disabled = true;
    try {
      const body = await post('/api/admin/planning-center/service-types', { ids: pcPending });
      C().planning_center = body.planning_center;
      pcPending = pcFollowed();
      pcDirty = false;
      renderPcTypes();
      okMsg('Service types saved. Relay now follows ' + pcPending.length + '.');
    } catch (e) { alertMsg(e.message); paintPcSelection(); }
  });

  // Keep the list current while it is on screen.
  setInterval(function () {
    if (!$('page-planningcenter').hidden) loadPcTypes(true);
  }, 60000);
  window.addEventListener('hashchange', function () {
    if (!$('page-planningcenter').hidden && !pcTypes) loadPcTypes(true);
  });

  $('pcNotes').addEventListener('change', async function () {
    const box = $('pcNotes');
    try {
      const body = await post('/api/admin/planning-center/notes', { show: box.checked });
      C().planning_center = body.planning_center;
      okMsg(box.checked ? 'Item notes are shown on panels.' : 'Item notes are hidden from panels.');
    } catch (e) { box.checked = !box.checked; alertMsg(e.message); }
  });

  $('pcDisconnect').addEventListener('click', async function () {
    try {
      const body = await api('/api/admin/planning-center', { method: 'DELETE' });
      C().planning_center = body.planning_center;
      pcTypes = null; pcPending = []; pcDirty = false; pcError = '';
      renderPc();
      okMsg('Disconnected. Relay no longer contacts Planning Center.');
    } catch (e) { alertMsg(e.message); }
  });
  ['pcAppId', 'pcSecret'].forEach(function (id) {
    $(id).addEventListener('input', function () { renderPc(); });
  });

  /* ---- Planning Center: today's plan and LIVE ----
     Read every few seconds while the section is open and a token is saved.
     Relay picks the plan with a service time today; the select overrides that
     pick for the rest of the day. */
  let pcLiveTimer = null, pcLiveSig = '';

  function mmss(sec) {
    const s = Math.abs(Math.round(sec));
    return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  }

  function pcLiveLine(t) {
    if (t.error) return t.error;
    if (!t.plan) return 'No plan with a service time today.';
    const tm = t.timers || {};
    const item = (t.items || []).filter(function (i) { return i.id === tm.current_item; })[0];
    const parts = [t.live ? 'Live · ' + t.controller.name + ' is controlling' : 'Not live'];
    if (item && tm.item_left_s !== null) {
      parts.push(item.title + ' · ' + mmss(tm.item_left_s) + (tm.item_left_s < 0 ? ' over' : ' left'));
    }
    if (tm.service_over_s !== null && tm.service_over_s !== undefined) {
      parts.push('service ' + mmss(tm.service_over_s) + (tm.service_over_s > 0 ? ' behind' : ' ahead'));
    }
    const next = (t.items || []).filter(function (i) { return i.id === tm.next_item; })[0];
    if (next) parts.push('next: ' + next.title);
    if (t.updated_at) {
      parts.push('checked ' + agoText(Math.max(0, Date.now() / 1000 - t.updated_at)));
    }
    return parts.join(' · ');
  }

    function notePcNames(types) {
    const names = {};
    types.forEach(function (t) { names[t.service_type] = t.name; });
    R.setServiceNames(names);
  }

  function renderPcLive(snap) {
    const types = snap.service_types || [];
    notePcNames(types);
    $('pcToday').hidden = !types.length;
    $('pcTodayNote').textContent = types.length
      ? 'Day boundary: ' + snap.time_zone + (snap.time_zone_guessed
        ? ' (Relay\'s own zone; Planning Center\'s was not readable)' : ' (from Planning Center)')
      : '';
    const list = $('pcTodayList');
    // Rebuild the rows only when the plans change, so an open select is not
    // pulled out from under the operator; the status line updates in place.
    const sig = JSON.stringify(types.map(function (t) {
      return [t.service_type, t.name, t.plan && t.plan.id, t.override, t.plans.map(function (p) { return p.id; })];
    }));
    if (sig !== pcLiveSig) {
      pcLiveSig = sig;
      list.innerHTML = '';
      types.forEach(function (t) {
        const row = el('div', 'pctoday', '');
        row.appendChild(el('strong', '', t.name + ' '));
        const sel = document.createElement('select');
        sel.setAttribute('aria-label', 'Plan followed today for ' + t.name);
        const auto = document.createElement('option');
        auto.value = ''; auto.textContent = 'Relay\'s pick for today';
        sel.appendChild(auto);
        const plans = t.plans.slice();
        if (t.plan && !plans.some(function (p) { return p.id === t.plan.id; })) plans.unshift(t.plan);
        plans.forEach(function (p) {
          const o = document.createElement('option');
          o.value = p.id;
          o.textContent = (p.dates || 'No date') + (p.title ? ' · ' + p.title : '');
          sel.appendChild(o);
        });
        sel.value = t.override && t.plan ? t.plan.id : '';
        sel.addEventListener('change', async function () {
          try {
            renderPcLive(await post('/api/admin/planning-center/plan',
              { service_type: t.service_type, plan: sel.value }));
            alertMsg('');
          } catch (e) { alertMsg(e.message); }
        });
        row.appendChild(sel);
        const line = el('div', 'note', '');
        line.id = 'pcLive-' + t.service_type;
        row.appendChild(line);
        list.appendChild(row);
      });
    }
    types.forEach(function (t) {
      const line = $('pcLive-' + t.service_type);
      if (!line) return;
      const picked = !t.override && t.plan ? (t.plan.dates || 'Plan ' + t.plan.id) + ' · ' : '';
      line.textContent = picked + pcLiveLine(t);
      line.className = 'note ' + (t.error ? 'err' : t.live ? 'ok' : '');
    });
    renderPcActions(snap.actions || []);
  }

  const PC_ACTION_WORDS = { next: 'Next', previous: 'Previous', take_control: 'Take control', release: 'Release' };
  const PC_RESULT_WORDS = {
    ok: 'done', unchanged: 'nothing to do', too_fast: 'too fast', not_followed: 'service type not followed',
    no_plan: 'no plan today', not_controlling: 'Relay does not have control', not_connected: 'not connected',
    unreachable: 'Planning Center unreachable', refused: 'Planning Center refused'
  };
  let pcActionsSig = '';
  function renderPcActions(actions) {
    const sig = JSON.stringify(actions.slice(0, 20));
    if (sig === pcActionsSig) return;
    pcActionsSig = sig;
    $('pcActions').hidden = !actions.length;
    const list = $('pcActionList');
    list.innerHTML = '';
    actions.slice(0, 20).forEach(function (a) {
      const when = new Date(a.at * 1000).toLocaleTimeString();
      const row = el('div', 'note ' + (a.result === 'ok' ? 'ok' : a.result === 'unchanged' ? '' : 'err'),
        when + ' · ' + a.panel_name + ' · ' + (PC_ACTION_WORDS[a.action] || a.action) + ' · '
        + (PC_RESULT_WORDS[a.result] || a.result));
      list.appendChild(row);
    });
  }

  async function pcLivePoll() {
    const on = C() && C().planning_center && C().planning_center.connected
      && C().planning_center.service_types.length && !$('page-planningcenter').hidden;
    if (!on) { if (!(C() && C().planning_center && C().planning_center.connected)) $('pcToday').hidden = true; return; }
    try { renderPcLive(await api('/api/admin/planning-center/live')); } catch (e) { /* next tick */ }
  }
  window.addEventListener('hashchange', pcLivePoll);
  pcLiveTimer = setInterval(pcLivePoll, 3000);

  document.addEventListener('relay:config', function () {
    renderPc();
    loadPcTypes(true);
  });
})();
