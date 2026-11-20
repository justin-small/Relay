/* The Network page's This computer tab (#347): this computer's network ports, and which port each kind of traffic uses. Routes are
   chosen from the listed ports only (never typed), and every change goes through the server, which refuses what it
   must and applies the rest at once. The page re-reads every 5 seconds while open, so a pulled cable shows.
   Everything from the server is set as text. */
(function () {
  const $ = function (id) { return document.getElementById(id); };
  if (!$('netPorts')) return;

  const canManage = window.relayCan('network.manage');
  const CHIP = { ok: 'OK', default: 'Default', down: 'Port down', missing: 'Port missing', fallback: 'Using default',
                 changed: 'Address changed' };
  const POLICY = { strict: 'Do not run it', fallback: 'Use the default network' };
  let state = null;
  let busy = false;
  const results = {};   // the last Test result per class, kept across the 5-second redraws
  let timer = null;

  const h = relayUI.el;
  const request = relayUI.client({ cache: 'no-store', status: true, session: true });
  function call(path) { return request('GET', path); }
  function post(path, body) { return request('POST', path, body || {}); }
  function showError(text) {
    const e = $('netError');
    e.textContent = text || '';
    e.hidden = !text;
  }
  function portName(p) { return p.friendly_name || p.name || p.id; }
  function classLabel(id) {
    const c = (state.classes || []).find(function (x) { return x.id === id; });
    return c ? c.label : id;
  }

  function renderWarnings() {
    const box = $('netWarnings');
    box.replaceChildren();
    const all = (state.warnings || []).concat(state.notices || []);
    all.forEach(function (w) { box.appendChild(h('p', '', w)); });
    box.hidden = !all.length;
  }

  function renderPorts() {
    const body = $('netPorts');
    body.replaceChildren();
    if (!state.ports.length) {
      const tr = h('tr');
      const td = h('td', 'muted', 'No network ports found.');
      td.colSpan = 6;
      tr.appendChild(td);
      body.appendChild(tr);
      return;
    }
    state.ports.forEach(function (p) {
      const tr = h('tr', p.present ? '' : 'gone');
      const name = h('td');
      name.appendChild(document.createTextNode(portName(p)));
      const sub = [p.name, p.kind, p.default_route ? 'default route' : '', p.vpn ? 'VPN' : (p.virtual ? 'virtual' : '')]
        .filter(Boolean).join(' · ');
      name.appendChild(h('small', '', sub));
      tr.appendChild(name);
      const addr = h('td', 'mono', (p.ipv4 || []).join(', ') || (p.present ? 'No address' : '—'));
      if (p.link_local_only) addr.appendChild(h('small', '', 'self-assigned (169.254)'));
      tr.appendChild(addr);
      const link = h('td');
      link.appendChild(h('span', 'badge ' + (!p.present ? '' : p.up === true ? 'ok' : p.up === false ? 'err' : ''),
        !p.present ? 'Not connected' : p.up === true ? 'Up' : p.up === false ? 'Down' : 'Unknown'));
      tr.appendChild(link);
      tr.appendChild(h('td', '', p.speed_mbps ? (p.speed_mbps >= 1000 ? (p.speed_mbps / 1000) + ' Gbit/s' : p.speed_mbps + ' Mbit/s') : '—'));
      const on = h('td');
      const tags = h('div', 'nmtags');
      (p.classes || []).forEach(function (id) { tags.appendChild(h('span', 'nmtag', classLabel(id))); });
      on.appendChild((p.classes || []).length ? tags : document.createTextNode('—'));
      tr.appendChild(on);
      const act = h('td');
      if (canManage && !p.present && p.known && !(p.classes || []).length) {
        const b = h('button', '', 'Forget');
        b.type = 'button';
        b.addEventListener('click', function () { act_(function () {
          return post('/api/admin/network/ports/' + encodeURIComponent(p.id) + '/forget');
        }); });
        act.appendChild(b);
      }
      tr.appendChild(act);
      body.appendChild(tr);
    });
  }

  function portSelect(c) {
    const sel = h('select');
    sel.setAttribute('aria-label', 'Network port for ' + c.label);
    const def = h('option', '', 'System default');
    def.value = '';
    sel.appendChild(def);
    state.ports.forEach(function (p) {
      if (p.present || p.id === c.port_id || p.known) {
        const o = h('option', '', portName(p) + ((p.ipv4 || []).length ? ' (' + p.ipv4[0].split('/')[0] + ')' : p.present ? '' : ' (not connected)'));
        o.value = p.id;
        sel.appendChild(o);
      }
    });
    sel.value = c.port_id || '';
    sel.disabled = !canManage;
    sel.addEventListener('change', function () {
      act_(function () { return post('/api/admin/network/classes/' + encodeURIComponent(c.id), { port_id: sel.value || null }); },
        function () { sel.value = c.port_id || ''; });
    });
    return sel;
  }

  function policySelect(c) {
    const sel = h('select');
    sel.setAttribute('aria-label', 'If the port for ' + c.label + ' is missing');
    (state.policies || ['strict', 'fallback']).forEach(function (p) {
      const o = h('option', '', POLICY[p] + (p === c.default_policy ? ' (default)' : ''));
      o.value = p;
      sel.appendChild(o);
    });
    sel.value = c.policy;
    sel.disabled = !canManage;
    sel.addEventListener('change', function () {
      act_(function () { return post('/api/admin/network/classes/' + encodeURIComponent(c.id), { port_id: c.port_id || null, policy: sel.value }); },
        function () { sel.value = c.policy; });
    });
    return sel;
  }

  function renderClasses() {
    const body = $('netClasses');
    body.replaceChildren();
    state.classes.forEach(function (c) {
      const tr = h('tr');
      const name = h('td');
      name.appendChild(document.createTextNode(c.label));
      name.appendChild(h('small', '', c.owner + (c.media ? ' · audio/video' : '')));
      tr.appendChild(name);
      const port = h('td');
      port.appendChild(portSelect(c));
      tr.appendChild(port);
      const pol = h('td');
      pol.appendChild(policySelect(c));
      tr.appendChild(pol);
      const st = h('td');
      st.appendChild(h('span', 'netchip ' + c.status, CHIP[c.status] || c.status));
      if (c.detail) st.appendChild(h('small', '', c.detail));
      else if (c.address) st.appendChild(h('small', 'mono', c.address));
      const out = h('div', 'nettest muted');   // the Test result, under the status
      st.appendChild(out);
      tr.appendChild(st);
      const act = h('td');
      if (canManage) {
        const b = h('button', '', 'Test');
        b.type = 'button';
        function show(r) {
          out.className = r.cls;
          out.replaceChildren();
          r.lines.forEach(function (l) { out.appendChild(h('div', '', l)); });
        }
        if (results[c.id]) show(results[c.id]);
        b.addEventListener('click', async function () {
          b.disabled = true;
          out.className = 'nettest muted';
          out.textContent = 'Testing…';
          try {
            const r = await post('/api/admin/network/classes/' + encodeURIComponent(c.id) + '/test');
            const lines = [r.detail].concat(r.notes || []);
            if (r.module) lines.push(c.owner + ': ' + r.module.detail);
            results[c.id] = { cls: 'nettest ' + (r.ok ? 'ok' : 'err'), lines: lines.filter(Boolean) };
          } catch (e) {
            results[c.id] = { cls: 'nettest err', lines: [e.message] };
          } finally {
            show(results[c.id]);
            b.disabled = false;
          }
        });
        act.appendChild(b);
      }
      tr.appendChild(act);
      body.appendChild(tr);
    });
  }

  function render() {
    renderWarnings();
    renderPorts();
    renderClasses();
  }

  async function act_(fn, undo) {
    busy = true;
    showError('');
    try {
      state = await fn();
      render();
    } catch (e) {
      showError(e.message);
      if (undo) undo();
    } finally {
      busy = false;
    }
  }

  async function load() {
    if (busy) return;
    try {
      const got = await call('/api/admin/network');
      if (busy) return;           // a change finished meanwhile and drew newer state
      state = got;
      // Do not redraw under an open dropdown: the admin is choosing.
      const open = document.activeElement && document.activeElement.tagName === 'SELECT'
        && $('page-network').contains(document.activeElement);
      if (!open) render();
    } catch (e) {
      showError(e.message);
    }
  }

  function onPage() { const pg = $('page-network'); return !!pg && !pg.hidden; }
  function sync() {
    if (onPage()) {
      load();
      if (!timer) timer = setInterval(function () { if (onPage() && !document.hidden) load(); }, 5000);
    } else if (timer) {
      clearInterval(timer);
      timer = null;
    }
  }
  window.addEventListener('hashchange', sync);
  $('page-network').addEventListener('relay:tab', sync);   // the page opened (admin.js showPage)
  sync();
})();
