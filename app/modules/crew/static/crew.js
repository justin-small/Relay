/* The crew page (#226): registers the service worker, draws the home screen (what is on now, this person's day, the
   plan), runs the bottom bar of tabs and the person's own sheet (pages, phones, other tools). Every word from the
   server is put on the page as text. */
(function () {
  'use strict';
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/crew/_/sw.js', { scope: '/crew' }).catch(function () { /* not installable here */ });
  }
  const $ = function (id) { return document.getElementById(id); };
  const net = $('crNet');
  function online() { if (net) net.hidden = navigator.onLine; }
  window.addEventListener('online', function () { online(); loadPlan(); loadToday(); loadCheckin(); });
  window.addEventListener('offline', online);
  online();

  const el = relayUI.el;                                   // el(tag, className, ...text or nodes)
  function time(ts, zone) {
    if (!ts) return '';
    try { return new Date(ts * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', timeZone: zone || undefined }); }
    catch (e) { return new Date(ts * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }); }
  }
  // Planning Center gives a service time as "1715": shown in this phone's own style, "5:15 PM".
  function hhmm(t) {
    const m = /^(\d{1,2}):?(\d{2})$/.exec(String(t || ''));
    if (!m) return t || '';
    const d = new Date(); d.setHours(+m[1], +m[2], 0, 0);
    return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  }
  async function getJson(path) {
    const res = await fetch(path, { credentials: 'same-origin', cache: 'no-store' });
    if (res.status === 401) { location.reload(); return null; }
    return res.ok ? res.json() : null;
  }

  // What the home screen knows; each part fills in as its answer comes back, then the day is drawn again.
  const day = { today: null, checkin: null, lists: null, plan: null, zone: null };
  // The last day seen, kept on this phone so the offline page can still show the position and times.
  function remember() {
    try {
      const t = day.today || {};
      localStorage.setItem('relay-crew-last', JSON.stringify({
        saved: Date.now(), role: $('crPos').textContent, call: t.call ? time(t.call.ts, t.zone) : '',
        plan: day.plan ? day.plan.title : '', service: day.plan && day.plan.time ? hhmm(day.plan.time) : '' }));
    } catch (e) { /* private mode: nothing kept */ }
  }

  // -- tabs: Home and the tabs modules add. Arrow keys move; the chosen tab is kept in the #hash.
  const tabs = Array.prototype.slice.call(document.querySelectorAll('.cr-tabs [role=tab]'));
  function select(tab, focus) {
    if (!tab) return;
    tabs.forEach(function (t) {
      const on = t === tab;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
      $(t.getAttribute('aria-controls')).hidden = !on;
    });
    if (focus) tab.focus();
    if (history.replaceState) history.replaceState(null, '', tab.dataset.tab === 'home' ? location.pathname : '#' + tab.dataset.tab);
    window.scrollTo(0, 0);
    document.dispatchEvent(new CustomEvent('crew:tab', { detail: tab.dataset.tab }));
  }
  function tabOf(id) { return tabs.find(function (t) { return t.dataset.tab === id; }); }
  const shown = tabs.filter(function (t) { return !t.hidden; });
  shown.forEach(function (t, i) {
    t.addEventListener('click', function () { select(t, false); });
    t.addEventListener('keydown', function (e) {
      const to = e.key === 'ArrowRight' ? i + 1 : e.key === 'ArrowLeft' ? i - 1 : e.key === 'Home' ? 0 :
        e.key === 'End' ? shown.length - 1 : null;
      if (to === null) return;
      e.preventDefault();
      select(shown[(to + shown.length) % shown.length], true);
    });
  });
  select(tabOf(location.hash.slice(1)) || tabs[0], false);

  // -- the person's sheet
  const sheet = $('crMe');
  $('crMeBtn').addEventListener('click', function () { if (sheet.showModal) sheet.showModal(); else sheet.setAttribute('open', ''); });
  $('crMeClose').addEventListener('click', function () { sheet.close(); });
  sheet.addEventListener('click', function (e) {
    if (e.target === sheet) sheet.close();                    // a tap on the backdrop
    const open = e.target.closest('[data-open]');
    if (open) { sheet.close(); select(tabOf(open.dataset.open), false); }
  });

  // -- the plan (Planning Center): the card on stage now, and the whole plan folded under the day.
  async function loadPlan() {
    const note = $('crPlanNote'), list = $('crItems');
    if (!window.RELAY_CREW.plan) return;
    let data;
    try { data = await getJson('/crew/api/plan'); } catch (e) { return; }
    if (!data) return;
    day.plan = data.plan; day.zone = data.plan ? data.plan.zone : null;
    if (list) list.textContent = '';
    if (!data.plan) {
      if (note) note.textContent = data.available ? 'No plan for today' : 'No plan is connected';
      $('crNow').hidden = true; drawDay(); return;
    }
    const items = data.plan.items.filter(function (it) { return it.kind !== 'header'; });
    if (note) note.textContent = 'Full plan · ' + items.length + (items.length === 1 ? ' item' : ' items');
    data.plan.items.forEach(function (it) {
      const li = el('li', it.state ? 'cr-' + it.state : '');
      li.append(el('span', 'cr-time', time(it.planned, data.plan.zone)), el('span', '', it.title));
      if (list) list.append(li);
    });
    const at = items.findIndex(function (it) { return it.state === 'current'; });
    const now = at >= 0 ? items[at] : null, next = at >= 0 ? items[at + 1] : items.find(function (it) { return it.state !== 'done'; });
    $('crNow').hidden = !now && !next;
    $('crNowWhen').textContent = data.plan.title + (data.plan.time ? ' · ' + hhmm(data.plan.time) : '');
    if (now) {
      $('crNowTitle').textContent = now.title;
      $('crNowSub').textContent = next && next.planned ? 'Until about ' + time(next.planned, data.plan.zone) : '';
    } else {
      $('crNowTitle').textContent = 'Not started';
      $('crNowSub').textContent = next && next.planned ? 'Starts at ' + time(next.planned, data.plan.zone) : '';
    }
    $('crNext').hidden = !(now && next);
    if (now && next) { $('crNextTitle').textContent = next.title; $('crNextAt').textContent = time(next.planned, data.plan.zone); }
    drawDay();
  }

  // -- this person's day: call time and check-in, checklist, the service.
  function line(label, title, end, endCls) {
    const li = el('li');
    li.append(el('span', 'cr-time', label), el('span', '', title));
    if (end) { if (typeof end === 'string') li.append(el('span', 'cr-end' + (endCls ? ' ' + endCls : ''), end)); else { end.classList.add('cr-end'); li.append(end); } }
    return li;
  }
  function inText(st) { return st && st.arrived ? 'In' + (st.ts ? ' ' + new Date(st.ts * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '') : ''; }
  function drawDay() {
    const list = $('crDayList'); list.textContent = '';
    const t = day.today, st = day.checkin;
    if (t) {
      const pos = t.teams.length ? t.teams.join(', ') : t.role;
      $('crPos').textContent = pos || '';
      $('crMeRole').textContent = [t.role, t.teams.join(', ')].filter(Boolean).join(' · ');
    }
    let end = null, cls = '';
    if (st && st.arrived) { end = inText(st); cls = 'ok'; }
    else if (st && st.geofence) { end = inBtn; }
    else if (st) { end = 'Not in yet'; }
    if (t && t.call) list.append(line(time(t.call.ts, t.zone), 'Call time' + (t.teams.length > 1 ? ' · ' + t.call.team : ''), end, cls));
    else if (end) list.append(line('', 'Check in', end, cls));
    if (day.lists) {
      let open = 0, all = 0;
      day.lists.forEach(function (l) { l.items.forEach(function (i) { all += 1; if (!i.done_ts) open += 1; }); });
      if (all) {
        const go = el('button', 'sm', 'Open'); go.type = 'button';
        go.addEventListener('click', function () { select(tabOf('checklists'), false); });
        list.append(line(open ? 'Now' : '✓', open ? 'Checklist, ' + open + ' left' : 'Checklist done', go));
      }
    }
    if (day.plan && day.plan.time) list.append(line(hhmm(day.plan.time), 'Service'));
    $('crDayHead').textContent = 'Your day';
    if (!list.children.length) list.append(line('', t && !t.call ? 'No call time today' : 'Nothing planned for you today'));
    const badge = $('crMeIn');
    badge.hidden = !(st && st.arrived); badge.className = 'badge ok';
    badge.textContent = st && st.arrived ? 'Checked in' + inText(st).slice(2) : '';
    remember();
  }
  async function loadToday() {
    try { const d = await getJson('/crew/api/today'); if (d) { day.today = d; drawDay(); } } catch (e) { /* offline */ }
  }
  async function loadLists() {
    if (!tabOf('checklists')) return;
    try { const d = await getJson('/crew/api/checklists'); if (d) { day.lists = d.lists; drawDay(); } } catch (e) { /* offline */ }
  }

  // Check in (#231): arrival is automatic on the church network; elsewhere a tap sends this phone's position, which
  // Relay only compares with the church's and then drops.
  const inNote = $('crInNote');
  const inBtn = el('button', 'sm', 'Check in'); inBtn.type = 'button';
  async function checkinCall(method, body) {
    const res = await fetch('/crew/api/checkin', { method: method, credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    return { ok: res.ok, status: res.status, data: await res.json().catch(function () { return {}; }) };
  }
  async function loadCheckin() {
    try {
      const got = await checkinCall('GET');
      if (!got.ok) return;                                         // this account may not check in
      let st = got.data;
      if (st.lan && !st.arrived) { const r = await checkinCall('POST', {}); if (r.ok) st = r.data; }
      day.checkin = st;
      inNote.textContent = st.arrived || st.geofence ? '' : 'You are checked in by yourself when your phone is on the church Wi-Fi.';
      drawDay();
    } catch (e) { /* offline */ }
  }
  inBtn.addEventListener('click', function () {
    if (!navigator.geolocation) { inNote.textContent = 'This browser cannot share a location.'; return; }
    inBtn.disabled = true; inNote.textContent = 'Finding you…';
    navigator.geolocation.getCurrentPosition(async function (pos) {
      const r = await checkinCall('POST', { lat: pos.coords.latitude, lon: pos.coords.longitude, accuracy: pos.coords.accuracy });
      inBtn.disabled = false;
      if (r.ok) { day.checkin = r.data; inNote.textContent = ''; drawDay(); }
      else inNote.textContent = r.data.detail || 'Check-in did not work.';
    }, function () { inBtn.disabled = false; inNote.textContent = 'Allow location for this site to check in, then try again.'; },
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 });
  });

  // Wi-Fi check-in (#356): this person's devices, seen by the UniFi module. Shown only while UniFi is on.
  const devBox = $('crDev'), devList = $('crDevList'), devThis = $('crDevThis'), devPrivate = $('crDevPrivate'),
    devNote = $('crDevNote');
  async function devCall(method, path, body) {
    const res = await fetch(path, { method: method, credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    return { ok: res.ok, status: res.status, data: await res.json().catch(function () { return {}; }) };
  }
  function showDevices(d) {
    devBox.hidden = !d.wifi && !d.devices.length;
    devList.textContent = '';
    d.devices.forEach(function (x) {
      const li = el('li');
      const name = el('span', '', (x.label || (x.kind === 'mac' ? 'Phone' : x.value)) + (x.kind === 'mac' ? ' · ' + x.value : ''));
      const rm = el('button', 'sm', 'Remove'); rm.type = 'button';
      rm.addEventListener('click', async function () {
        rm.disabled = true;
        const r = await devCall('DELETE', '/crew/api/devices/' + x.id);
        if (r.ok) showDevices(r.data); else { rm.disabled = false; devNote.textContent = r.data.detail || 'That did not work.'; }
      });
      li.append(name, rm);
      devList.appendChild(li);
    });
    devThis.hidden = !(d.this && !d.this.yours);
    devPrivate.hidden = !(d.this && d.this.private);
    if (!d.wifi) devNote.textContent = 'Wi-Fi check-in is off at the moment.';
  }
  async function loadDevices() {
    try { const r = await devCall('GET', '/crew/api/devices'); if (r.ok) showDevices(r.data); } catch (e) { /* offline */ }
  }
  devThis.addEventListener('click', async function () {
    devThis.disabled = true;
    const r = await devCall('POST', '/crew/api/devices/this', {});
    devThis.disabled = false;
    if (r.ok) { showDevices(r.data); loadCheckin(); }
    else devNote.textContent = r.data.detail || 'That did not work.';
  });

  // Pages (#228): turn on web push for this phone. It needs HTTPS and a service worker, which this page has.
  const card = $('crAlerts'), sw = $('crAlertsBtn'), anote = $('crAlertsNote');
  function b64(text) {
    const pad = '='.repeat((4 - text.length % 4) % 4), raw = atob((text + pad).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, function (c) { return c.charCodeAt(0); });
  }
  async function pushJson(method, body) {
    const res = await fetch('/crew/api/push', { method: method, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    return res.ok ? res.json() : null;
  }
  async function alertsState() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) return;
    card.hidden = false;
    if (Notification.permission === 'denied') {
      anote.textContent = 'Blocked for this site. Allow notifications in the browser settings.';
      sw.checked = false; sw.disabled = true; return;
    }
    const reg = await navigator.serviceWorker.ready, sub = await reg.pushManager.getSubscription();
    const on = !!sub && Notification.permission === 'granted';
    sw.checked = on; sw.disabled = false;
    anote.textContent = on ? 'This phone buzzes until you confirm.' : 'Off. Turn on to get a buzz when the booth pages you.';
  }
  async function turnOn() {
    try {
      if (await Notification.requestPermission() !== 'granted') { alertsState(); return; }
      const key = await (await fetch('/crew/api/push/key', { credentials: 'same-origin' })).json();
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64(key.key) });
      if (!await pushJson('POST', sub.toJSON())) { await sub.unsubscribe(); anote.textContent = 'Relay could not save this phone.'; sw.checked = false; return; }
    } catch (e) { anote.textContent = 'This browser could not turn on pages.'; sw.checked = false; return; }
    alertsState();
  }
  async function turnOff() {
    try {
      const reg = await navigator.serviceWorker.ready, sub = await reg.pushManager.getSubscription();
      if (sub) { await pushJson('DELETE', { endpoint: sub.endpoint }); await sub.unsubscribe(); }
    } catch (e) { /* stays as it was */ }
    alertsState();
  }
  sw.addEventListener('change', function () { sw.disabled = true; (sw.checked ? turnOn : turnOff)(); });

  // A count on a tab (a module's script sends crew:count with {tab, n}), e.g. unread chat messages.
  document.addEventListener('crew:count', function (e) {
    const t = e.detail && tabOf(e.detail.tab), ct = t && t.querySelector('.cr-ct');
    if (!ct) return;
    ct.hidden = !e.detail.n; ct.textContent = e.detail.n > 9 ? '9+' : String(e.detail.n || '');
  });

  loadPlan(); loadToday(); loadCheckin(); loadLists(); loadDevices(); alertsState();
  setInterval(function () { loadPlan(); loadToday(); loadLists(); }, 30000);
})();
