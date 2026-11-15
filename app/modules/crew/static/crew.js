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

  loadPlan();
  setInterval(loadPlan, 30000);
})();
