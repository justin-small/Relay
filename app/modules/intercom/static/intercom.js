/* The Intercom admin page (#238): LiveKit's state, the LAN address and ports it uses, a restart, and the log tail.
   Loaded by admin.html only while the module is on. Everything from the server is shown as text. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-intercom')) return;
  const $ = R.$, api = R.api, post = R.post;
  function can(perm) { return typeof window.relayCan !== 'function' || !!window.relayCan(perm); }

  let last = null;
  let timer = null;

  function uptime(s) {
    if (s < 90) return s + ' s';
    if (s < 5400) return Math.round(s / 60) + ' min';
    return Math.round(s / 3600) + ' h';
  }

  function render(v) {
    last = v;
    $('icSum').textContent = v.state === 'running' ? 'running' : v.state;
    $('icState').textContent = v.detail || (v.state === 'running' ? 'Running for ' + uptime(v.uptime_s) + '.' :
      v.state === 'off' ? 'Not running.' : '');
    $('icState').className = 'note ' + (v.state === 'running' ? 'ok' : (v.state === 'error' || v.state === 'missing' ? 'err' : ''));
    $('icFacts').textContent = 'LiveKit ' + v.version + ', media on ' + v.node_ip + '.';
    $('icPorts').textContent = v.ports.tcp + '/tcp, ' + v.ports.udp + '/udp';
    $('icLog').textContent = v.log.join('\n');
    $('icRestart').hidden = !can('intercom.manage');
  }

  async function refresh() {
    try { render(await api('/api/admin/intercom')); } catch (e) { /* the admin page reports lost sessions */ }
  }

  function visible() { return !document.getElementById('page-intercom').closest('.page').hidden; }

  function tick() {
    if (visible()) refresh();
    timer = setTimeout(tick, last && last.state !== 'running' && last.state !== 'off' ? 2000 : 8000);
  }

  $('icRestart').addEventListener('click', async function () {
    $('icErr').hidden = true;
    try { render(await post('/api/admin/intercom/restart', {})); } catch (e) {
      $('icErr').textContent = e.message;
      $('icErr').hidden = false;
    }
  });

  document.addEventListener('relay:config', function () { refresh(); if (!timer) tick(); });
})();
