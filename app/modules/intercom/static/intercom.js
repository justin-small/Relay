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

  function day(iso) { return iso ? iso.slice(0, 10) : ''; }

  function renews(iso) {
    if (!iso) return '';
    const d = Math.round((Date.parse(iso) - Date.now()) / 86400000);
    return d <= 0 ? 'today' : 'in ' + d + ' d';
  }

  function renderPhones(list) {
    const body = $('icPhones');
    body.textContent = '';
    list.forEach(function (p) {
      const tr = document.createElement('tr');
      [p.user, p.name, day(p.signed_in), day(p.last_seen), renews(p.renews_at), p.status].forEach(function (v) {
        const td = document.createElement('td');
        td.textContent = v;
        tr.appendChild(td);
      });
      const act = document.createElement('td');
      if (can('intercom.manage')) {
        const out = document.createElement('button');
        out.type = 'button';
        out.textContent = 'Sign out';
        out.addEventListener('click', async function () {
          try { await R.api('/api/admin/intercom/phones/' + p.id, { method: 'DELETE' }); } catch (e) { fail(e); }
          refreshPhones();
        });
        act.appendChild(out);
      }
      tr.appendChild(act);
      body.appendChild(tr);
    });
    $('icPhonesTable').hidden = !list.length;
    $('icPhonesEmpty').hidden = !!list.length;
    $('icSignOutAll').hidden = !list.length || !can('intercom.manage');
  }

  function fail(e) {
    $('icErr').textContent = e.message;
    $('icErr').hidden = false;
  }

  async function refreshPhones() {
    if (!can('intercom.manage')) { $('icPhonesSection').hidden = true; return; }
    try { renderPhones((await api('/api/admin/intercom/phones')).phones); } catch (e) { /* as above */ }
  }

  async function refresh() {
    try { render(await api('/api/admin/intercom')); } catch (e) { /* the admin page reports lost sessions */ }
    refreshPhones();
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

  $('icSignOutAll').addEventListener('click', async function () {
    if (!window.confirm('Sign out every phone? Each person will have to sign in again.')) return;
    try { await post('/api/admin/intercom/phones/signout-all', {}); } catch (e) { fail(e); }
    refreshPhones();
  });

  document.addEventListener('relay:config', function () { refresh(); if (!timer) tick(); });
})();
