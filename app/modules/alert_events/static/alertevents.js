/* The Alert events admin page (#219): where alert events are sent. Loaded by admin.html only while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-alertevents')) return;
  const $ = R.$, api = R.api;
  function can(perm) { return typeof window.relayCan !== 'function' || !!window.relayCan(perm); }
  function showError(text) { $('aeErr').textContent = text || ''; $('aeErr').hidden = !text; }

  async function send(path, body) {
    const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}), credentials: 'same-origin' });
    const data = await res.json().catch(function () { return {}; });
    if (!res.ok) throw new Error(data.error || data.detail || 'That did not work.');
    return data;
  }

  function fill(cfg) {
    $('aeHookOn').checked = !!cfg.webhook.enabled;
    $('aeHookUrl').value = cfg.webhook.url || '';
    $('aeOscOn').checked = !!cfg.osc.enabled;
    $('aeOscHost').value = cfg.osc.host || '';
    $('aeOscPort').value = String(cfg.osc.port || 8000);
    const n = (cfg.webhook.enabled ? 1 : 0) + (cfg.osc.enabled ? 1 : 0);
    $('aeSum').textContent = n ? n + (n === 1 ? ' output on' : ' outputs on') : '';
    const manage = can('alert_events.manage');
    ['aeHookOn', 'aeHookUrl', 'aeOscOn', 'aeOscHost', 'aeOscPort', 'aeSave', 'aeTest'].forEach(function (id) {
      $(id).disabled = !manage;
    });
  }

  async function refresh() {
    try { fill(await api('/api/admin/alert-events')); } catch (e) { /* the page keeps what it had */ }
  }

  function form() {
    return {
      webhook: { enabled: $('aeHookOn').checked, url: $('aeHookUrl').value.trim() },
      osc: { enabled: $('aeOscOn').checked, host: $('aeOscHost').value.trim(), port: parseInt($('aeOscPort').value, 10) || 0 },
    };
  }

  $('aeSave').addEventListener('click', async function () {
    showError(''); $('aeMsg').textContent = '';
    try { fill(await send('/api/admin/alert-events', form())); $('aeMsg').textContent = 'Saved'; }
    catch (e) { showError(e.message); }
  });
  $('aeTest').addEventListener('click', async function () {
    showError(''); $('aeMsg').textContent = '';
    try {
      const r = await send('/api/admin/alert-events/test');
      if (r.ok) $('aeMsg').textContent = r.detail; else showError(r.detail);
    } catch (e) { showError(e.message); }
  });

  document.addEventListener('relay:config', refresh);
  refresh();
})();
