/* The crew page (#226): registers the service worker and shows the plan. */
(function () {
  'use strict';
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/crew/_/sw.js', { scope: '/crew' }).catch(function () { /* not installable here */ });
  }
  const net = document.getElementById('crNet');
  function online() { if (net) net.hidden = navigator.onLine; }
  window.addEventListener('online', function () { online(); loadPlan(); });
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
  loadPlan();
  setInterval(loadPlan, 30000);
})();
