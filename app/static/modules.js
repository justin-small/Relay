/* The Modules page (#157): every integration Relay ships, with its switch, its health, its setup guide, a link
   to its settings and a connection test. Switching one on or off reloads the admin page, because a module's
   pages and scripts are rendered by the server only while it is on. Everything from the server is set as text. */
(function () {
  const $ = function (id) { return document.getElementById(id); };
  if (!$('moduleList')) return;

  const canManage = window.relayCan('modules.manage');

  function h(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  async function call(path, opts) {
    const res = await fetch(path, Object.assign({ cache: 'no-store' }, opts || {}));
    const data = await res.json().catch(function () { return {}; });
    if (!res.ok && !('ok' in data)) {
      if (window.RelayAdmin) window.RelayAdmin.sessionEnded(res, data);
      throw new Error(data.error || data.detail || ('HTTP ' + res.status));
    }
    return data;
  }
  function post(path, body) {
    return call(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  }

  const HEALTH = { ok: 'Working', warn: 'Needs attention', error: 'Not working', idle: 'Idle' };

  function card(m) {
    const c = h('div', 'pcard mcard');
    const head = h('div', 'phead');
    const title = h('span', 'pname', m.name);
    head.appendChild(title);
    head.appendChild(h('span', 'mver', 'v' + m.version));
    const sw = h('label', 'switch');
    if (canManage) sw.title = m.enabled ? 'Switch ' + m.name + ' off' : 'Switch ' + m.name + ' on';
    const box = h('input');
    box.type = 'checkbox';
    box.checked = !!m.enabled;
    box.setAttribute('aria-label', m.name);
    box.disabled = !canManage;   // a role that may only look sees the state, not a switch that would be refused
    sw.appendChild(box);
    sw.appendChild(h('span', 'slider'));
    head.appendChild(sw);
    c.appendChild(head);
    c.appendChild(h('p', 'msum muted', m.summary));

    const foot = h('div', 'mfoot');
    const health = h('span', 'mhealth ' + (m.enabled ? m.health.state : 'idle'),
      m.enabled ? (m.health.detail || HEALTH[m.health.state] || m.health.state) : 'Off');
    foot.appendChild(health);
    if (m.enabled && m.pages.length) {
      const a = h('a', '', 'Settings');
      a.href = '#' + m.pages[m.pages.length - 1].id;
      foot.appendChild(a);
    }
    if (m.guide) {
      const g = h('a', '', 'Setup guide');
      g.href = m.guide;
      g.target = '_blank';
      g.rel = 'noopener';
      foot.appendChild(g);
    }
    let result = null;
    if (canManage && m.enabled && m.has_test) {
      const t = h('button', '', 'Test connection');
      t.type = 'button';
      result = h('span', 'muted');
      t.addEventListener('click', async function () {
        t.disabled = true;
        result.className = 'muted';
        result.textContent = 'Testing…';
        try {
          const r = await post('/api/admin/modules/' + encodeURIComponent(m.id) + '/test');
          result.className = r.ok ? 'ok' : 'err';
          result.textContent = r.detail || (r.ok ? 'Connected.' : 'Not connected.');
        } catch (e) {
          result.className = 'err';
          result.textContent = e.message;
        }
        t.disabled = false;
      });
      foot.appendChild(t);
      foot.appendChild(result);
    }
    c.appendChild(foot);
    if (m.uses.length) {
      const off = m.uses.filter(function (u) { return !u.enabled; }).map(function (u) { return u.name; });
      const all = m.uses.map(function (u) { return u.name; }).join(', ');
      c.appendChild(h('div', 'note', off.length
        ? 'Does more with ' + all + ' on too (' + off.join(', ') + ' is off).'
        : 'Works with ' + all + '.'));
    }

    box.addEventListener('change', async function () {
      box.disabled = true;
      try {
        await post('/api/admin/modules/' + encodeURIComponent(m.id), { enabled: box.checked });
        location.hash = box.checked && m.pages.length ? m.pages[m.pages.length - 1].id : 'modules';
        location.reload();
      } catch (e) {
        box.checked = !box.checked;
        box.disabled = false;
        foot.appendChild(h('span', 'err', e.message));
      }
    });
    return c;
  }

  async function load() {
    const list = $('moduleList');
    try {
      const data = await call('/api/admin/modules');
      list.textContent = '';
      if (!data.modules.length) list.appendChild(h('div', 'muted', 'No modules are installed.'));
      data.modules.forEach(function (m) { list.appendChild(card(m)); });
    } catch (e) {
      list.textContent = '';
      list.appendChild(h('div', 'err', 'Could not load the modules: ' + e.message));
    }
  }
  load();
  window.addEventListener('hashchange', function () { if (location.hash === '#modules') load(); });
})();
