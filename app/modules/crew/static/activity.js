/* The Activity tab of the crew page (#235): the crew entries of the audit log, for admins and editors. Everything
   from the server is put on the page as text. Loads when the tab is first opened. */
(function () {
  'use strict';
  const root = document.getElementById('ac');
  if (!root) return;
  const $ = function (id) { return document.getElementById(id); };
  const note = $('acNote'), list = $('acList'), more = $('acMore');
  let last = 0, started = false;

  function when(at) {
    const d = new Date(at);
    return isNaN(d) ? '' : d.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  }
  async function load(append) {
    const q = new URLSearchParams();
    if ($('acWho').value) q.set('actor', $('acWho').value);
    if ($('acKind').value) q.set('kind', $('acKind').value);
    if ($('acDays').value) q.set('days', $('acDays').value);
    if (append && last) q.set('before', last);
    const res = await fetch('/crew/api/activity?' + q, { credentials: 'same-origin', cache: 'no-store' });
    if (res.status === 401) { location.reload(); return; }
    const data = await res.json().catch(function () { return {}; });
    if (!res.ok) { note.textContent = data.detail || 'That did not work.'; return; }
    note.textContent = '';
    if (!append) list.textContent = '';
    if (!started) {
      started = true;
      data.people.forEach(function (p) {
        const o = document.createElement('option'); o.value = String(p.id); o.textContent = p.name || ('#' + p.id);
        $('acWho').appendChild(o);
      });
    }
    data.entries.forEach(function (e) {
      const li = document.createElement('li'); li.className = 'rs-item';
      const who = e.actor_name || e.token_name || 'Relay';
      li.textContent = who + ' · ' + e.summary;
      const t = document.createElement('span'); t.className = 'cr-note'; t.textContent = ' ' + when(e.at);
      li.appendChild(t);
      list.appendChild(li);
      last = e.id;
    });
    if (!append && !data.entries.length) note.textContent = 'Nothing yet for that filter.';
    more.hidden = !data.more;
  }
  ['acWho', 'acKind', 'acDays'].forEach(function (id) { $(id).addEventListener('change', function () { last = 0; load(false); }); });
  more.addEventListener('click', function () { load(true); });
  load(false);
})();
