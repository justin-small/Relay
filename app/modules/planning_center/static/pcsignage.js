/* Plan files as signage items in admin (#209): which service type and plan items, what to copy, and Sync now.
   Loaded by admin.html only while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('sgBox')) return;
  const $ = R.$, el = R.el, api = R.api, post = R.post;
  function can(perm) { return typeof window.relayCan !== 'function' || !!window.relayCan(perm); }
  function C() { return R.cfg(); }
  function say(id, text, bad) { const n = $(id); n.textContent = text || ''; n.className = 'note' + (bad ? ' err' : text ? ' ok' : ''); }

  const FIELDS = ['sgType', 'sgMatch', 'sgItemFiles', 'sgPlanFiles', 'sgText', 'sgNote', 'sgPlaylist', 'sgOn'];
  let filled = false;       // the form shows the saved settings once, so polling never overwrites an edit

  function fill(g) {
    const s = g.settings, sel = $('sgType');
    sel.innerHTML = '';
    [{ id: '', name: 'Pick a service type' }].concat(g.service_types).forEach(function (t) {
      const o = el('option', '', t.name); o.value = t.id; sel.appendChild(o);
    });
    sel.value = s.service_type;
    $('sgMatch').value = s.item_match; $('sgNote').value = s.note_category;
    $('sgItemFiles').checked = s.item_files; $('sgPlanFiles').checked = s.plan_files;
    $('sgText').checked = s.text_slides; $('sgPlaylist').checked = s.playlist; $('sgOn').checked = s.enabled;
    filled = true;
  }
  function render(g) {
    if (!filled) fill(g);
    let line = '';
    if (!g.enabled) line = 'Off.';
    else if (g.error) line = g.error;
    else if (!g.synced_at) line = 'Waiting for the first copy.';
    else line = 'Plan "' + g.plan + '": ' + g.files + ' file' + (g.files === 1 ? '' : 's') + ', ' + g.texts + ' text slide'
      + (g.texts === 1 ? '' : 's') + ', copied ' + new Date(g.synced_at * 1000).toLocaleTimeString() + '.';
    say('sgState', line, g.enabled && !!g.error);
    $('sgSum').textContent = g.enabled ? (g.error ? 'needs attention' : 'on') : '';
    const manage = can('planning_center.manage');
    FIELDS.forEach(function (id) { $(id).disabled = !manage; });
    $('sgSave').hidden = !manage;
    $('sgSync').hidden = !manage;
    $('sgSync').disabled = !g.enabled;
  }
  async function load() {
    const pc = C() && C().planning_center;
    const on = pc && pc.connected && pc.service_types.length && !$('page-planningcenter').hidden;
    $('sgBox').hidden = !on;
    if (!on) { filled = false; return; }
    try { render(await api('/api/admin/planning-center/signage')); } catch (e) { /* the next poll tries again */ }
  }
  $('sgSave').addEventListener('click', async function () {
    say('sgMsg', '');
    try {
      render(await post('/api/admin/planning-center/signage/settings', {
        enabled: $('sgOn').checked, service_type: $('sgType').value, item_match: $('sgMatch').value,
        item_files: $('sgItemFiles').checked, plan_files: $('sgPlanFiles').checked, text_slides: $('sgText').checked,
        note_category: $('sgNote').value, playlist: $('sgPlaylist').checked }));
      say('sgMsg', 'Saved.');
    } catch (e) { say('sgMsg', e.message, true); }
  });
  $('sgSync').addEventListener('click', async function () {
    say('sgMsg', '');
    try { render(await post('/api/admin/planning-center/signage/sync', {})); say('sgMsg', 'Copied.'); }
    catch (e) { say('sgMsg', e.message, true); }
  });
  window.addEventListener('hashchange', load);
  document.addEventListener('relay:config', load);
  setInterval(load, 5000);
})();
