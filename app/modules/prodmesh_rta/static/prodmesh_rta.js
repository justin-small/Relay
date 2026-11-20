/* The ProdMesh RTA admin page (#149): the analyser's address, its connection and what has arrived. Loaded by
   admin.html only while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-prodmeshrta')) return;
  const $ = R.$;
  const can = relayUI.can;
  function showError(text) { $('pmErr').textContent = text || ''; $('pmErr').hidden = !text; }

  const send = relayUI.api;

  let editing = false;
  function draw(s) {
    if (!editing) { $('pmUrl').value = s.url; $('pmWarn').value = s.warn_db; $('pmLimit').value = s.limit_db; }
    $('pmSum').textContent = s.connected ? 'connected' : (s.configured ? 'not connected' : 'not set up');
    $('pmStatus').textContent = s.connected ? 'Connected' + (s.signal ? ', signal ' + s.signal : '') + (typeof s.lufs === 'number' ? ', ' + s.lufs.toFixed(1) + ' LUFS' : '') + '.'
      : (s.configured ? (s.error || 'Connecting…') : 'Enter the analyser\'s address to begin.');
    $('pmCounts').textContent = s.messages + ' messages heard, ' + s.unknown + ' not understood.';
    $('pmSamples').textContent = (s.samples || []).join('\n');
    ['pmUrl', 'pmWarn', 'pmLimit', 'pmSave'].forEach(function (id) { $(id).disabled = !can('prodmesh_rta.manage'); });
  }

  let last = '';
  async function refresh() {
    try {
      const s = await send('GET', '/api/admin/prodmesh-rta');
      const sig = JSON.stringify(s);
      if (sig !== last) { last = sig; draw(s); }
    } catch (e) { /* the page keeps what it shows */ }
  }
  ['pmUrl', 'pmWarn', 'pmLimit'].forEach(function (id) { $(id).oninput = function () { editing = true; }; });
  $('pmSave').onclick = async function () {
    showError('');
    try {
      editing = false;
      const s = await send('PUT', '/api/admin/prodmesh-rta/settings',
        { url: $('pmUrl').value, warn_db: +$('pmWarn').value, limit_db: +$('pmLimit').value });
      last = JSON.stringify(s); draw(s);
    } catch (e) { showError(e.message); }
  };
  refresh();
  setInterval(function () { if (!document.hidden && !document.getElementById('page-prodmeshrta').hidden) refresh(); }, 2000);
})();
