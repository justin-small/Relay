/* The Intercom live page (#241): who is on, who is talking, and the operator's overrides. The server pushes the whole
   state over one event stream; every name and label from it is set as text, never as markup. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('icLive')) return;
  const $ = R.$;
  const BASE = '/api/admin/intercom/live';
  function can(perm) { return typeof window.relayCan !== 'function' || !!window.relayCan(perm); }
  const canLive = can('intercom.live');
  let es = null;
  let state = null;
  let editingLabel = false;

  function cell(tr, text, cls) {
    const td = document.createElement('td');
    td.textContent = text;
    if (cls) td.className = cls;
    tr.appendChild(td);
    return td;
  }

  function fail(e) {
    $('icLiveErr').textContent = e.message || String(e);
    $('icLiveErr').hidden = false;
  }

  async function act(path, body, method) {
    $('icLiveErr').hidden = true;
    try {
      await R.api(BASE + path, { method: method || 'POST', headers: { 'Content-Type': 'application/json' },
                                 body: JSON.stringify(body || {}) });
    } catch (e) { fail(e); }
  }

  function button(label, fn) {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = label;
    b.addEventListener('click', fn);
    return b;
  }

  const MODES = { preempt: 'Pre-empt', party: 'Party line', queue: 'One at a time' };

  function render(v) {
    state = v;
    $('icLiveSum').textContent = v.connected_count + (v.connected_count === 1 ? ' phone' : ' phones');
    if (!editingLabel && document.activeElement !== $('icLiveLabel')) $('icLiveLabel').value = v.label;
    $('icMuteAll').textContent = v.muted_all ? 'Unmute all' : 'Mute all';
    $('icMutedBanner').hidden = !v.muted_all;

    const ch = $('icLiveCh');
    ch.textContent = '';
    v.channels.forEach(function (c) {
      const tr = document.createElement('tr');
      const name = cell(tr, c.name);
      name.style.borderLeft = '4px solid ' + (/^#[0-9a-f]{6}$/i.test(c.colour || '') ? c.colour : 'transparent');
      cell(tr, MODES[c.mode] || c.mode);
      cell(tr, c.talking.join(', ') || '—');
      cell(tr, String(c.listening));
      cell(tr, String(c.waiting));
      const act_ = document.createElement('td');
      if (canLive) {
        act_.appendChild(button(c.muted ? 'Unmute' : 'Mute channel', function () {
          act('/channels/' + c.id + '/mute', { on: !c.muted });
        }));
      } else if (c.muted) act_.textContent = 'Muted';
      tr.appendChild(act_);
      ch.appendChild(tr);
    });
    $('icLiveChTable').hidden = !v.channels.length;
    $('icLiveChEmpty').hidden = !!v.channels.length;

    const con = $('icLiveCon');
    con.textContent = '';
    v.connected.forEach(function (p) {
      const tr = document.createElement('tr');
      cell(tr, p.user);
      cell(tr, p.role);
      cell(tr, p.priority == null ? '' : String(p.priority));
      cell(tr, p.phone);
      cell(tr, p.where);
      cell(tr, p.talking.join(', ') || '—');
      cell(tr, p.listening.join(', ') || '—');
      cell(tr, p.loss_pct == null ? '' : p.loss_pct.toFixed(1) + ' %', 'ic-' + p.loss_level);
      cell(tr, p.jitter_ms == null ? '' : Math.round(p.jitter_ms) + ' ms', 'ic-' + p.jitter_level);
      const a = document.createElement('td');
      if (canLive) a.appendChild(button(p.user_muted ? 'Unmute' : 'Mute', function () {
        act('/users/' + p.user_id + '/mute', { on: !p.user_muted });
      }));
      if (can('intercom.manage')) a.appendChild(button('Sign out', async function () {
        try { await R.api('/api/admin/intercom/phones/' + p.device_id, { method: 'DELETE' }); } catch (e) { fail(e); }
      }));
      tr.appendChild(a);
      con.appendChild(tr);
    });
    $('icLiveConTable').hidden = !v.connected.length;
    $('icLiveConEmpty').hidden = !!v.connected.length;
  }

  function open() {
    if (es || typeof EventSource === 'undefined') return;
    es = new EventSource(BASE + '/stream');
    es.onmessage = function (m) { try { render(JSON.parse(m.data)); } catch (e) { /* a bad frame is skipped */ } };
    es.onerror = function () { /* the browser reconnects; the admin page reports a lost sign-in */ };
  }

  function close() {
    if (es) { es.close(); es = null; }
  }

  function visible() { return !document.getElementById('page-intercom').closest('.page').hidden; }

  function sync() { if (visible()) open(); else close(); }

  document.querySelectorAll('#icLive [data-live]').forEach(function (b) { b.hidden = !canLive; });
  $('icLiveLabelSave').addEventListener('click', function () {
    act('/production', { label: $('icLiveLabel').value }, 'PUT');
  });
  $('icLiveLabel').addEventListener('input', function () { editingLabel = true; });
  $('icLiveLabel').addEventListener('change', function () { editingLabel = false; });
  $('icLiveLabelPlan').addEventListener('click', function () { act('/production', { use_plan: true }, 'PUT'); });
  $('icReleaseAll').addEventListener('click', function () { act('/release-all'); });
  $('icMuteAll').addEventListener('click', function () {
    act('/mute-all', { on: !(state && state.muted_all) });
  });

  R.api('/api/admin/intercom/production').then(function (r) {
    $('icLiveLabelPlan').hidden = !(canLive && r.plan_available);
  }).catch(function () { /* no plan button */ });

  window.addEventListener('hashchange', sync);
  document.addEventListener('relay:config', sync);
  setInterval(sync, 3000);
  sync();
})();
