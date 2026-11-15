/* The crew page (#226): tabs, today's plan, a banner when the connection is not secure or Relay is out of reach,
   and the service worker. Everything from the server is shown as text. */
(function () {
  const $ = function (id) { return document.getElementById(id); };

  // ---- tabs (arrow keys move, the chosen tab is remembered in the #hash)
  const tabs = Array.prototype.slice.call(document.querySelectorAll('[role=tab]'));
  function select(tab, focus) {
    tabs.forEach(function (t) {
      const on = t === tab;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
      $(t.getAttribute('aria-controls')).hidden = !on;
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

  // ---- connection and plan
  if (!window.isSecureContext) $('crewInsecure').hidden = false;

  async function refresh() {
    try {
      const res = await fetch('/crew/api/state', { cache: 'no-store', credentials: 'same-origin' });
      if (res.status === 401) { location.reload(); return; }
      const s = await res.json();
      $('crewOffline').hidden = true;
      $('crewPlan').hidden = s.plan === null;
      $('crewNoPlan').hidden = s.plan !== null;
      if (s.plan !== null) $('crewPlan').textContent = s.plan || 'No plan today';
    } catch (e) {
      $('crewOffline').hidden = false;
    }
  }
  setInterval(refresh, 30000);
  window.addEventListener('online', refresh);
  window.addEventListener('offline', function () { $('crewOffline').hidden = false; });

  // ---- installable: the service worker keeps the page's own files, never the signed-in page itself
  if ('serviceWorker' in navigator && window.isSecureContext) {
    navigator.serviceWorker.register('/crew/sw.js', { scope: '/crew' }).catch(function () { /* works without it */ });
  }
})();
