/* The Open Sound Meter admin page (#149): where its UDP broadcast is heard, and what has arrived. Loaded by admin.html
   only while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-opensoundmeter')) return;
  const $ = R.$;
  const can = relayUI.can;
  function showError(text) { $('osmErr').textContent = text || ''; $('osmErr').hidden = !text; }

  const send = relayUI.api;

  let editing = false;
  function draw(s) {
    if (!editing) {
      $('osmGroup').value = s.group; $('osmPort').value = s.port; $('osmWeighting').value = s.weighting;
      $('osmResponse').value = s.response; $('osmWarn').value = s.warn_db; $('osmLimit').value = s.limit_db;
    }
    $('osmSum').textContent = s.error ? 'problem' : (s.heard ? 'receiving' : (s.listening ? 'listening' : 'not listening'));
    $('osmStatus').textContent = s.error || (s.heard ? 'Readings are arriving.' : (s.listening ? 'Listening. Nothing heard yet.' : 'Not listening.'));
    $('osmCounts').textContent = s.packets + ' packets heard, ' + s.unknown + ' not understood.';
    $('osmSamples').textContent = (s.samples || []).join('\n');
    ['osmGroup', 'osmPort', 'osmWeighting', 'osmResponse', 'osmWarn', 'osmLimit', 'osmSave'].forEach(function (id) {
      $(id).disabled = !can('open_sound_meter.manage');
    });
  }

  let last = '';
  async function refresh() {
    try {
      const s = await send('GET', '/api/admin/open-sound-meter');
      const sig = JSON.stringify(s);
      if (sig !== last) { last = sig; draw(s); }
    } catch (e) { /* the page keeps what it shows */ }
  }
  ['osmGroup', 'osmPort', 'osmWeighting', 'osmResponse', 'osmWarn', 'osmLimit'].forEach(function (id) {
    $(id).oninput = function () { editing = true; };
  });
  $('osmSave').onclick = async function () {
    showError('');
    try {
      editing = false;
      const s = await send('PUT', '/api/admin/open-sound-meter/settings', {
        group: $('osmGroup').value, port: +$('osmPort').value || 9999, weighting: $('osmWeighting').value,
        response: $('osmResponse').value, warn_db: +$('osmWarn').value, limit_db: +$('osmLimit').value });
      last = JSON.stringify(s); draw(s);
    } catch (e) { showError(e.message); }
  };
  refresh();
  setInterval(function () { if (!document.hidden && !document.getElementById('page-opensoundmeter').hidden) refresh(); }, 2000);
})();
