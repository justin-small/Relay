/* The lighting admin page (#377): choose the program, group cues into pages, fire them. Loaded by admin.html only
   while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-lighting')) return;
  const $ = R.$;
  function can(perm) { return typeof window.relayCan !== 'function' || !!window.relayCan(perm); }
  function showError(text) { $('ltErr').textContent = text || ''; $('ltErr').hidden = !text; }
  let data = null;
  let pages = [];

  async function send(method, path, body) {
    const res = await fetch(path, { method: method, headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), credentials: 'same-origin' });
    const out = await res.json().catch(function () { return {}; });
    if (!res.ok) throw new Error(out.error || out.detail || 'That did not work.');
    return out;
  }
  function el(tag, text, cls) {
    const e = document.createElement(tag);
    if (text) e.textContent = text;
    if (cls) e.className = cls;
    return e;
  }
  function button(text, fn, cls) {
    const b = el('button', text, cls);
    b.type = 'button';
    b.onclick = async function () {
      showError(''); b.disabled = true;
      try { await fn(); } catch (e) { showError(e.message); }
      b.disabled = false;
    };
    return b;
  }
  function kindOf(id) {
    const c = ((data && data.cues) || []).filter(function (x) { return x.id === id; })[0];
    return c ? c.kind : /^s\d+$/.test(id) ? 'slider' : 'button';
  }
  function levelControl(cue) {
    const box = el('span');
    box.appendChild(el('span', cue.name + ' '));
    const slider = el('input');
    slider.type = 'range'; slider.min = 0; slider.max = 100; slider.disabled = !can('lighting.control');
    slider.value = data && data.levels && data.levels[cue.id] !== undefined ? data.levels[cue.id] : 0;
    const out = el('span', slider.value + '%');
    slider.oninput = function () { out.textContent = slider.value + '%'; };
    slider.onchange = async function () {
      showError('');
      try { await send('POST', '/api/admin/lighting/level', { cue: cue.id, level: parseInt(slider.value, 10) }); }
      catch (e) { showError(e.message); }
    };
    box.appendChild(slider); box.appendChild(out);
    return box;
  }
  function fireButtons(cue) {
    if (kindOf(cue.id) === 'slider') return levelControl(cue);
    const box = el('span');
    if (can('lighting.control')) {
      box.appendChild(button(cue.name, function () { return send('POST', '/api/admin/lighting/press', { cue: cue.id }); },
        data && data.state && data.state[cue.id] ? 'primary' : ''));
    } else {
      box.appendChild(el('span', cue.name));
    }
    return box;
  }

  function renderPages() {
    const root = $('ltPages');
    root.textContent = '';
    const manage = can('lighting.manage');
    pages.forEach(function (page, pi) {
      const card = el('div', '', 'card');
      const name = el('input');
      name.value = page.name; name.maxLength = 40; name.disabled = !manage;
      name.oninput = function () { page.name = name.value; };
      card.appendChild(name);
      const list = el('div');
      page.cues.forEach(function (cue, ci) {
        const row = el('div', '', 'row');
        row.appendChild(fireButtons(cue));
        if (manage) {
          row.appendChild(button('Up', function () { move(page.cues, ci, -1); renderPages(); }));
          row.appendChild(button('Down', function () { move(page.cues, ci, 1); renderPages(); }));
          row.appendChild(button('Remove', function () { page.cues.splice(ci, 1); renderPages(); }));
        }
        list.appendChild(row);
      });
      card.appendChild(list);
      if (manage) {
        const id = el('input'); id.placeholder = 'Cue id'; id.maxLength = 64;
        const nm = el('input'); nm.placeholder = 'Name'; nm.maxLength = 60;
        card.appendChild(id); card.appendChild(nm);
        card.appendChild(button('Add cue', function () {
          if (!id.value.trim()) throw new Error('Enter the cue id from your lighting program.');
          page.cues.push({ id: id.value.trim(), name: nm.value.trim() || id.value.trim() }); renderPages();
        }));
        card.appendChild(button('Up', function () { move(pages, pi, -1); renderPages(); }));
        card.appendChild(button('Down', function () { move(pages, pi, 1); renderPages(); }));
        card.appendChild(button('Delete page', function () { pages.splice(pi, 1); renderPages(); }));
      }
      root.appendChild(card);
    });
    $('ltPageCtl').hidden = !manage;
    renderAll();
  }
  function move(list, i, d) {
    const j = i + d;
    if (j < 0 || j >= list.length) return;
    const t = list[i]; list[i] = list[j]; list[j] = t;
  }
  function renderAll() {
    const used = {};
    pages.forEach(function (p) { p.cues.forEach(function (c) { used[c.id] = true; }); });
    const rest = ((data && data.cues) || []).filter(function (c) { return !used[c.id]; });
    $('ltAll').hidden = !(data && data.can_list);
    const root = $('ltAllCues');
    root.textContent = '';
    rest.forEach(function (c) {
      const row = el('div', '', 'row');
      row.appendChild(fireButtons(c));
      if (can('lighting.manage')) {
        row.appendChild(button('Add to first page', function () {
          if (!pages.length) throw new Error('Add a page first.');
          pages[0].cues.push({ id: c.id, name: c.name }); renderPages();
        }));
      }
      root.appendChild(row);
    });
  }

  function render(d) {
    data = d; pages = JSON.parse(JSON.stringify(d.pages || []));
    const sel = $('ltBackend');
    sel.textContent = '';
    const none = el('option', 'Choose a program'); none.value = ''; sel.appendChild(none);
    (d.backends || []).forEach(function (b) {
      const o = el('option', b.label); o.value = b.id; if (b.id === d.backend) o.selected = true; sel.appendChild(o);
    });
    $('ltHost').value = d.host || ''; $('ltPort').value = d.port || ''; $('ltPass').value = '';
    $('ltFunctions').checked = !!d.functions; $('ltConsole').value = d.console || 'EX'; $('ltFade').value = d.fade === '' || d.fade == null ? '' : d.fade;
    $('ltPass').placeholder = d.has_password ? '(saved)' : '';
    const manage = can('lighting.manage');
    ['ltBackend', 'ltHost', 'ltPort', 'ltPass', 'ltFunctions', 'ltConsole', 'ltFade', 'ltSave', 'ltTest'].forEach(function (id) { $(id).disabled = !manage; });
    $('ltSum').textContent = d.active ? 'ready' : 'not set up';
    $('ltStatus').textContent = d.error || '';
    renderPages();
  }
  async function load() { render(await send('GET', '/api/admin/lighting')); }

  $('ltSave').onclick = async function () {
    showError('');
    try {
      render(await send('PUT', '/api/admin/lighting/settings', { backend: $('ltBackend').value,
        host: $('ltHost').value, functions: $('ltFunctions').checked, console: $('ltConsole').value, fade: $('ltFade').value === '' ? '' : parseFloat($('ltFade').value), port: parseInt($('ltPort').value || '0', 10), password: $('ltPass').value }));
    } catch (e) { showError(e.message); }
  };
  $('ltTest').onclick = async function () {
    showError('');
    try { const r = await send('POST', '/api/admin/lighting/test'); $('ltStatus').textContent = r.detail || ''; }
    catch (e) { showError(e.message); }
  };
  $('ltAddPage').onclick = function () { pages.push({ id: '', name: 'New page', cues: [] }); renderPages(); };
  $('ltSavePages').onclick = async function () {
    showError('');
    try { render(await send('PUT', '/api/admin/lighting/pages', { pages: pages })); } catch (e) { showError(e.message); }
  };
  window.addEventListener('hashchange', function () { if (location.hash === '#lighting') load().catch(function (e) { showError(e.message); }); });
  if (location.hash === '#lighting') load().catch(function (e) { showError(e.message); });
})();
