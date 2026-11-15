/* The crew page (#226): registers the service worker and shows the plan. */
(function () {
  'use strict';
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/crew/_/sw.js', { scope: '/crew' }).catch(function () { /* not installable here */ });
  }
  const net = document.getElementById('crNet');
  function online() { if (net) net.hidden = navigator.onLine; }
  window.addEventListener('online', function () { online(); loadPlan(); loadToday(); });
  window.addEventListener('offline', online);
  online();

  const KINDS = { song: 'Song', header: '', item: '' };
  function time(ts, zone) {
    if (!ts) return '';
    try { return new Date(ts * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', timeZone: zone || undefined }); }
    catch (e) { return new Date(ts * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }); }
  }
  async function loadPlan() {
    const note = document.getElementById('crPlanNote'), list = document.getElementById('crItems');
    if (!window.RELAY_CREW.plan || !note) return;
    try {
      const res = await fetch('/crew/api/plan', { credentials: 'same-origin', cache: 'no-store' });
      if (res.status === 401 || res.status === 403) { location.reload(); return; }
      const data = await res.json();
      list.textContent = '';
      if (!data.plan) { note.textContent = data.available ? 'There is no plan for today.' : 'No plan is connected.'; return; }
      note.textContent = data.plan.title + (data.plan.time ? ' · ' + data.plan.time : '');
      data.plan.items.forEach(function (it) {
        const li = document.createElement('li');
        if (it.state) li.className = 'cr-' + it.state;
        const t = document.createElement('span'); t.className = 'cr-time'; t.textContent = time(it.planned, data.plan.zone);
        const n = document.createElement('span'); n.className = 'cr-title';
        n.textContent = (KINDS[it.kind] ? KINDS[it.kind] + ': ' : '') + it.title;
        li.append(t, n); list.append(li);
      });
    } catch (e) { note.textContent = 'The plan could not be loaded.'; }
  }
  // Check in (#231): arrival is automatic on the church network; elsewhere a tap sends this phone's position, which
  // Relay only compares with the church's and then drops.
  const inCard = document.getElementById('crIn'), inNote = document.getElementById('crInNote'),
    inBtn = document.getElementById('crInBtn');
  function arrivedText(st) {
    return 'You are checked in' + (st.ts ? ' at ' + new Date(st.ts * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '') + '.';
  }
  async function checkinCall(method, body) {
    const res = await fetch('/crew/api/checkin', { method: method, credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    return { ok: res.ok, status: res.status, data: await res.json().catch(function () { return {}; }) };
  }
  function showCheckin(st) {
    inCard.hidden = false; inBtn.hidden = true;
    if (st.arrived) { inNote.textContent = arrivedText(st); return; }
    if (st.geofence) {
      inNote.textContent = 'Tap to check in when you are at the church.'; inBtn.hidden = false; return;
    }
    inNote.textContent = 'Check-in happens by itself when your phone is on the church network.';
  }
  async function loadCheckin() {
    if (!inCard) return;
    try {
      const got = await checkinCall('GET');
      if (got.status === 403 || got.status === 404) return;          // this account may not check in
      if (!got.ok) return;
      let st = got.data;
      if (st.lan && !st.arrived) { const r = await checkinCall('POST', {}); if (r.ok) st = r.data; }
      showCheckin(st);
    } catch (e) { /* offline: the card stays hidden */ }
  }
  if (inBtn) inBtn.addEventListener('click', function () {
    if (!navigator.geolocation) { inNote.textContent = 'This browser cannot share a location.'; return; }
    inBtn.disabled = true; inNote.textContent = 'Finding you\u2026';
    navigator.geolocation.getCurrentPosition(async function (pos) {
      const r = await checkinCall('POST', { lat: pos.coords.latitude, lon: pos.coords.longitude, accuracy: pos.coords.accuracy });
      inBtn.disabled = false;
      if (r.ok) showCheckin(r.data);
      else inNote.textContent = r.data.detail || 'Check-in did not work.';
    }, function () { inBtn.disabled = false; inNote.textContent = 'Allow location for this site to check in, then try again.'; },
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 });
  });
  loadCheckin();

  // Pages (#228): turn on web push for this phone. It needs HTTPS and a service worker, which this page has.
  const card = document.getElementById('crAlerts'), btn = document.getElementById('crAlertsBtn'),
    anote = document.getElementById('crAlertsNote');
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
    if (!card || !('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) return;
    card.hidden = false;
    if (Notification.permission === 'denied') {
      anote.textContent = 'Pages are blocked for this site. Allow notifications in the browser settings to use them.';
      btn.hidden = true; return;
    }
    const reg = await navigator.serviceWorker.ready, sub = await reg.pushManager.getSubscription();
    const on = !!sub && Notification.permission === 'granted';
    btn.textContent = on ? 'Turn off pages' : 'Turn on pages';
    anote.textContent = on ? 'This phone buzzes when the booth pages you.' : 'Get a buzz on this phone when the booth pages you.';
    btn.onclick = on ? turnOff : turnOn;
  }
  async function turnOn() {
    try {
      if (await Notification.requestPermission() !== 'granted') { alertsState(); return; }
      const key = await (await fetch('/crew/api/push/key', { credentials: 'same-origin' })).json();
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64(key.key) });
      if (!await pushJson('POST', sub.toJSON())) { await sub.unsubscribe(); anote.textContent = 'Relay could not save this phone.'; return; }
    } catch (e) { anote.textContent = 'This browser could not turn on pages.'; return; }
    alertsState();
  }
  async function turnOff() {
    try {
      const reg = await navigator.serviceWorker.ready, sub = await reg.pushManager.getSubscription();
      if (sub) { await pushJson('DELETE', { endpoint: sub.endpoint }); await sub.unsubscribe(); }
    } catch (e) { /* stays as it was */ }
    alertsState();
  }
  alertsState();

  // Tabs a module adds (arrow keys move; the chosen tab is kept in the #hash). `crew:tab` tells the module's script.
  const tabs = Array.prototype.slice.call(document.querySelectorAll('[role=tab]'));
  function select(tab, focus) {
    tabs.forEach(function (t) {
      const on = t === tab;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute('aria-controls')).hidden = !on;
    });
    if (tab && focus) tab.focus();
    if (tab && history.replaceState) history.replaceState(null, '', '#' + tab.dataset.tab);
    document.dispatchEvent(new CustomEvent('crew:tab', { detail: tab ? tab.dataset.tab : '' }));
  }
  tabs.forEach(function (t, i) {
    t.addEventListener('click', function () { select(t, false); });
    t.addEventListener('keydown', function (e) {
      const to = e.key === 'ArrowRight' ? i + 1 : e.key === 'ArrowLeft' ? i - 1 : e.key === 'Home' ? 0 :
        e.key === 'End' ? tabs.length - 1 : null;
      if (to === null) return;
      e.preventDefault();
      select(tabs[(to + tabs.length) % tabs.length], true);
    });
  });
  if (tabs.length) {
    const want = location.hash.slice(1);
    select(tabs.find(function (t) { return t.dataset.tab === want; }) || tabs[0], false);
  }

  // Booth-off screen (#234): while capture is not running, the call time and position come first, then the plan,
  // checklist and chat. While it runs the page is as it always was.
  const main = document.getElementById('crMain'), today = document.getElementById('crToday');
  async function loadToday() {
    if (!today) return;
    try {
      const res = await fetch('/crew/api/today', { credentials: 'same-origin', cache: 'no-store' });
      if (!res.ok) return;
      const d = await res.json(), off = !d.running;
      main.classList.toggle('cr-off', off);
      today.hidden = !off;
      if (!off) return;
      const pos = d.teams.length ? d.teams.join(', ') : d.role;
      document.getElementById('crPos').textContent = pos || 'No position today';
      document.getElementById('crCall').textContent = d.call
        ? 'Call time ' + time(d.call.ts, d.zone) + (d.teams.length > 1 ? ' · ' + d.call.team : '')
        : 'No call time today.';
      if (!location.hash) {
        const lists = tabs.find(function (t) { return t.dataset.tab === 'checklists'; });
        if (lists && lists.getAttribute('aria-selected') !== 'true' && !tabs.some(function (t) { return t.dataset.touched; })) select(lists, false);
      }
    } catch (e) { /* offline: the page stays as it was */ }
  }
  tabs.forEach(function (t) { t.addEventListener('click', function () { t.dataset.touched = '1'; }); });
  loadToday();
  setInterval(loadToday, 30000);

  loadPlan();
  setInterval(loadPlan, 30000);
})();
