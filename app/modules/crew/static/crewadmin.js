/* The Crew app admin page (#226). Loaded by admin.html only while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-crew')) return;
  const $ = R.$, api = R.api;
  function can(perm) { return typeof window.relayCan !== 'function' || !!window.relayCan(perm); }
  function fill(cfg) {
    $('crName').value = cfg.name || '';
    $('crPlan').checked = !!cfg.show_plan;
    const manage = can('crew.manage');
    ['crName', 'crPlan', 'crSave'].forEach(function (id) { $(id).disabled = !manage; });
    $('crLink').textContent = location.origin + '/crew';
  }
  async function refresh() { try { fill(await api('/api/admin/crew')); } catch (e) { /* keeps what it had */ } }
  $('crSave').addEventListener('click', async function () {
    $('crErr').hidden = true; $('crMsg').textContent = '';
    const res = await fetch('/api/admin/crew', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: $('crName').value, show_plan: $('crPlan').checked }) });
    const data = await res.json().catch(function () { return {}; });
    if (!res.ok) { $('crErr').textContent = data.detail || data.error || 'That did not work.'; $('crErr').hidden = false; return; }
    fill(data); $('crMsg').textContent = 'Saved';
  });
  document.addEventListener('relay:config', refresh);
  refresh();
})();
