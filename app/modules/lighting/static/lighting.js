/* The lighting admin page (#377): choose the program, group cues into pages, fire them. Loaded by admin.html only
   while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-lighting')) return;
  const $ = R.$;
  const can = relayUI.can;
  function showError(text) { $('ltErr').textContent = text || ''; $('ltErr').hidden = !text; }
  let data = null;
  let pages = [];

  const send = relayUI.api;
  const el = relayUI.el;
  const button = relayUI.buttons(showError);
  function kindOf(id) {
    const c = ((data && data.cues) || []).filter(function (x) { return x.id === id; })[0];
    return c ? c.kind : /^s\d+$/.test(id) ? 'slider' : 'button';
  }
  function levelControl(cue) {
    const box = el('span');
    box.appendChild(el('span', '', cue.name + ' '));
    const slider = el('input');
    slider.type = 'range'; slider.min = 0; slider.max = 100; slider.disabled = !can('lighting.control');
    slider.value = data && data.levels && data.levels[cue.id] !== undefined ? data.levels[cue.id] : 0;
    const out = el('span', '', slider.value + '%');
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
      box.appendChild(el('span', '', cue.name));
    }
    return box;
  }

  // Each page is laid out with core's shared editor (#421), the same drawing as its tablet screen. A person who may
  // not edit pages sees the drawing only, and presses fire cues when they may fire them.
  const editors = [];
  function toItem(c) {
    return { id: c.id, kind: 'button', type: 'cue', title: c.name, x: c.x, y: c.y, w: c.w, h: c.h, style: c.style || {}, props: { cue: c.id } };
  }
  function fromItem(it, page) {
    const id = (it.props && it.props.cue) || it.id;
    const old = page.cues.filter(function (c) { return c.id === id; })[0];
    return { id: id, name: old ? old.name : (it.title || id), x: it.x, y: it.y, w: it.w, h: it.h, style: it.style };
  }
  const CUE_LOOK = { shape: 'rounded', bg: '#2b5fd9', fg: '#ffffff', pressed: '#5b8bff', icon: '' };
  function palette(page) {
    const on = {};
    page.cues.forEach(function (c) { on[c.id] = true; });
    return ((data && data.cues) || []).filter(function (c) { return !on[c.id] && kindOf(c.id) !== 'slider'; }).map(function (c) {
      return { group: 'From the lighting program', name: c.name, title: c.name, kind: 'button', type: 'cue', w: 1, h: 1,
        props: { cue: c.id }, style: CUE_LOOK };
    });
  }
  function collect() {
    // What every page's editor holds now, so a save never loses a layout not yet saved on its own.
    editors.forEach(function (ed, pi) {
      if (!ed || !pages[pi]) return;
      const st = ed.get();
      pages[pi].cols = st.cols; pages[pi].rows = st.rows;
      pages[pi].cues = st.items.map(function (it) { return fromItem(it, pages[pi]); });
    });
  }
  async function savePages() {
    collect();
    render(await send('PUT', '/api/admin/lighting/pages', { pages: pages.map(function (p) {
      return { id: p.id, name: p.name, key_on: p.key_on, cols: p.cols, rows: p.rows, cues: p.cues };
    }) }));
  }
  function layoutFor(page, pi, holder) {
    if (!can('lighting.manage')) {
      const box = el('div', 'cuebox'); const g = el('div', 'cuegrid'); box.appendChild(g); holder.appendChild(box);
      const drawn = window.RelayLayout.draw(g, { cols: page.cols, rows: page.rows, items: page.cues.map(toItem) },
        can('lighting.control') ? function (it) { send('POST', '/api/admin/lighting/press', { cue: it.props.cue }).catch(function (e) { showError(e.message); }); } : null);
      Object.keys(drawn).forEach(function (id) { window.RelayLayout.update(drawn[id], { lit: !!(data && data.state && data.state[id]) }); });
      return;
    }
    editors[pi] = window.RelayLayout.edit(holder, {
      title: page.name, noun: 'page', paletteName: 'Cues', inline: true, gap: 8, ratio: '4/3', saveLabel: 'Save page',
      cols: page.cols, rows: page.rows, sizes: [[4, 3], [6, 4], [3, 2], [8, 6], [5, 4]],
      items: page.cues.map(toItem), palette: palette(page),
      fields: function (it) { return [{ key: 'cue', label: 'Cue id in the lighting program', type: 'ro' },
        { type: 'note', label: 'A press is sent once and never retried.' }]; },
      addById: { label: 'Add by id', placeholder: 'Cue id (Vista: EX.1.0.1.0.0)',
        make: function (id) { return { name: id, title: id, kind: 'button', type: 'cue', w: 1, h: 1, props: { cue: id }, style: CUE_LOOK }; } },
      onSave: function () { return savePages(); },
      onDraw: function (nodes) {
        Object.keys(nodes).forEach(function (id) {
          const it = editors[pi] ? editors[pi].get().items.filter(function (i) { return i.id === id; })[0] : null;
          const cue = it ? it.props.cue : id;
          window.RelayLayout.update(nodes[id], { lit: !!(data && data.state && data.state[cue]) });
        });
      }
    });
  }
  async function screenControls(page) {
    const box = el('div', 'ctl');
    const on = !!page.key_on;
    const link = el('input'); link.readOnly = true; link.size = 40; link.hidden = true;
    async function show() {
      const r = await send('GET', '/api/admin/lighting/pages/' + encodeURIComponent(page.id) + '/link');
      link.value = r.link; link.hidden = !r.link;
    }
    if (!page.id) { box.appendChild(el('span', '', 'Save the pages first to get this screen\'s link.')); return box; }
    box.appendChild(button(on ? 'Turn screen off' : 'Turn screen on', async function () {
      await send('PUT', '/api/admin/lighting/pages/' + encodeURIComponent(page.id) + '/screen', { on: !on });
      await load();
    }));
    if (on) {
      box.appendChild(button('New key', async function () {
        if (!(await relayUI.confirm('The old link stops working. Continue?'))) return;
        await send('PUT', '/api/admin/lighting/pages/' + encodeURIComponent(page.id) + '/screen', { on: true, rotate: true });
        await load();
      }));
      box.appendChild(button('Show link', show));
      box.appendChild(link);
    }
    return box;
  }

  function renderPages() {
    const root = $('ltPages');
    root.textContent = '';
    editors.length = 0;
    const manage = can('lighting.manage');
    pages.forEach(function (page, pi) {
      const card = el('div', 'card');
      const head = el('div', 'ctl');
      const name = el('input');
      name.value = page.name; name.maxLength = 40; name.disabled = !manage; name.setAttribute('aria-label', 'Page name'); name.style.maxWidth = '260px';
      name.oninput = function () { page.name = name.value; };
      head.appendChild(name);
      if (manage) {
        head.appendChild(button('Up', function () { collect(); move(pages, pi, -1); renderPages(); }));
        head.appendChild(button('Down', function () { collect(); move(pages, pi, 1); renderPages(); }));
        head.appendChild(button('Delete page', async function () {
          if (!(await relayUI.confirm('Delete the page "' + page.name + '"? Its screen link stops working once you save.'))) return;
          collect(); pages.splice(pi, 1); renderPages();
        }));
      }
      card.appendChild(head);
      const sliders = page.cues.filter(function (c) { return kindOf(c.id) === 'slider'; });
      if (sliders.length) { const sl = el('div', 'row'); sliders.forEach(function (c) { sl.appendChild(fireButtons(c)); }); card.appendChild(sl); }
      const holder = el('div'); holder.style.marginTop = '10px';
      card.appendChild(holder);
      layoutFor(page, pi, holder);
      if (manage) {
        const sc = el('div');
        card.appendChild(sc);
        screenControls(page).then(function (c) { sc.appendChild(c); });
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
      const row = el('div', 'row');
      row.appendChild(fireButtons(c));
      if (can('lighting.manage')) {
        row.appendChild(button('Add to first page', function () {
          if (!pages.length) throw new Error('Add a page first.');
          collect(); pages[0].cues.push({ id: c.id, name: c.name, x: 0, y: 0, w: 1, h: 1, style: CUE_LOOK }); renderPages();
        }));
      }
      root.appendChild(row);
    });
  }

  function render(d) {
    data = d; pages = JSON.parse(JSON.stringify(d.pages || []));
    const sel = $('ltBackend');
    sel.textContent = '';
    const none = el('option', '', 'Choose a program'); none.value = ''; sel.appendChild(none);
    (d.backends || []).forEach(function (b) {
      const o = el('option', '', b.label); o.value = b.id; if (b.id === d.backend) o.selected = true; sel.appendChild(o);
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
  $('ltAddPage').onclick = function () { collect(); pages.push({ id: '', name: 'New page', key_on: false, cols: 4, rows: 3, cues: [] }); renderPages(); };
  $('ltSavePages').onclick = async function () {
    showError('');
    try { await savePages(); } catch (e) { showError(e.message); }
  };
  window.addEventListener('hashchange', function () { if (location.hash === '#lighting') load().catch(function (e) { showError(e.message); }); });
  if (location.hash === '#lighting') load().catch(function (e) { showError(e.message); });
})();
