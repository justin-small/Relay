/* The lighting admin page (#377): four tabs (docs/mockups/lighting/a-tabs.html).
   Fire cues    one page at a time, its levels, and the cues on no page.
   Pages        a list of pages; New page asks a name, a size and a starting point; Edit opens one page in core's
                shared layout editor (#421) with its own Save, and Page settings for its screen, a copy or deleting it.
   Screens      each page's tablet link and key.
   Program      which lighting program, then only the fields that program needs (the backend's `fields`).
   Loaded by admin.html only while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-lighting')) return;
  const $ = R.$;
  const can = relayUI.can;
  const send = relayUI.api;
  const el = relayUI.el;
  function showError(text) { $('ltErr').textContent = text || ''; $('ltErr').hidden = !text; }
  const button = relayUI.buttons(showError);

  let data = null;
  let pages = [];
  let tab = 'fire';
  let firstLoad = true;
  let firePage = '';                       // the page id on the Fire cues tab
  let editing = -1;                        // the index in `pages` open in the editor, or -1
  let editor = null;
  let baseline = '';                       // what the open page looked like when last saved
  const CUE_LOOK = { shape: 'rounded', bg: '#2b5fd9', fg: '#ffffff', pressed: '#5b8bff', icon: '' };
  const SIZES = [[3, 2], [4, 3], [6, 4], [8, 6]];

  function manage() { return can('lighting.manage'); }
  function kindOf(id) {
    const c = ((data && data.cues) || []).filter(function (x) { return x.id === id; })[0];
    return c ? c.kind : /^s\d+$/.test(id) ? 'slider' : 'button';
  }
  function copy(v) { return JSON.parse(JSON.stringify(v)); }
  function plural(n, one) { return n + ' ' + one + (n === 1 ? '' : 's'); }
  function buttonsOf(page) { return page.cues.filter(function (c) { return kindOf(c.id) !== 'slider'; }); }
  function slidersOf(page) { return page.cues.filter(function (c) { return kindOf(c.id) === 'slider'; }); }
  function describe(page) {
    const s = slidersOf(page).length;
    return plural(buttonsOf(page).length, 'cue') + (s ? ', ' + plural(s, 'level') : '') + ' · ' + page.cols + ' × ' + page.rows;
  }
  function toItem(c) {
    return { id: c.id, kind: 'button', type: 'cue', title: c.name, x: c.x, y: c.y, w: c.w, h: c.h, style: c.style || {}, props: { cue: c.id } };
  }
  function fromItem(it, page) {
    const id = (it.props && it.props.cue) || it.id;
    const old = page.cues.filter(function (c) { return c.id === id; })[0];
    return { id: id, name: old ? old.name : (it.title || id), x: it.x, y: it.y, w: it.w, h: it.h, style: it.style };
  }
  function press(cue) {
    showError('');
    return send('POST', '/api/admin/lighting/press', { cue: cue }).then(load).catch(function (e) { showError(e.message); });
  }
  function drawPage(holder, page, onPress) {
    holder.textContent = '';
    const drawn = window.RelayLayout.draw(holder, { cols: page.cols, rows: page.rows, items: page.cues.map(toItem) }, onPress || null);
    Object.keys(drawn).forEach(function (id) { window.RelayLayout.update(drawn[id], { lit: !!(data && data.state && data.state[id]) }); });
  }
  function move(list, i, d) {
    const j = i + d;
    if (j < 0 || j >= list.length) return false;
    const t = list[i]; list[i] = list[j]; list[j] = t;
    return true;
  }
  // Whole cells, no overlap, as app/layout.py places them on save: a cue that does not fit where it asks goes to the
  // first free cell, and is left off when the page is full.
  function place(cues, cols, rows) {
    const taken = {};
    function free(x, y, w, h) {
      for (let i = x; i < x + w; i++) for (let j = y; j < y + h; j++) if (taken[i + ',' + j]) return false;
      return true;
    }
    const out = [];
    cues.forEach(function (c) {
      let w = Math.min(c.w || 1, cols), h = Math.min(c.h || 1, rows);
      let x = Math.min(c.x || 0, cols - w), y = Math.min(c.y || 0, rows - h);
      if (!free(x, y, w, h)) {
        let spot = null;
        for (let j = 0; j + h <= rows && !spot; j++) for (let i = 0; i + w <= cols && !spot; i++) if (free(i, j, w, h)) spot = [i, j];
        if (!spot) { w = h = 1; for (let j = 0; j < rows && !spot; j++) for (let i = 0; i < cols && !spot; i++) if (free(i, j, 1, 1)) spot = [i, j]; }
        if (!spot) return;
        x = spot[0]; y = spot[1];
      }
      for (let i = x; i < x + w; i++) for (let j = y; j < y + h; j++) taken[i + ',' + j] = true;
      out.push(Object.assign({}, c, { x: x, y: y, w: w, h: h }));
    });
    return out;
  }

  /* ---------------------------------------------------------------- tabs */
  function showTab(name) {
    if (!manage() && (name === 'pages' || name === 'screens')) name = 'fire';
    tab = name;
    $('ltTabs').querySelectorAll('[data-lttab]').forEach(function (b) {
      b.setAttribute('aria-selected', String(b.dataset.lttab === name));
      b.hidden = !manage() && (b.dataset.lttab === 'pages' || b.dataset.lttab === 'screens');
    });
    document.querySelectorAll('#page-lighting [data-ltpane]').forEach(function (p) { p.hidden = p.dataset.ltpane !== name; });
    if (name === 'screens') renderScreens();
  }
  $('ltTabs').addEventListener('click', function (e) {
    const b = e.target.closest('[data-lttab]');
    if (b) showTab(b.dataset.lttab);
  });

  /* ---------------------------------------------------------------- fire cues */
  function renderFire() {
    const shown = pages.filter(function (p) { return p.id; });
    if (!shown.some(function (p) { return p.id === firePage; })) firePage = shown.length ? shown[0].id : '';
    const page = shown.filter(function (p) { return p.id === firePage; })[0];
    $('ltFireEmpty').hidden = !!page;
    $('ltFireEmpty').textContent = !(data.programs || []).length ? 'Choose your lighting program on the Program tab first.'
      : manage() ? 'No cue pages yet. Make one on the Pages tab.' : 'No cue pages yet.';
    $('ltFireBar').hidden = shown.length < 2;
    $('ltFireBox').hidden = !page;
    const pick = $('ltFirePick');
    pick.textContent = '';
    shown.forEach(function (p) {
      const b = el('button', p.id === firePage ? 'on' : '', p.name);
      b.type = 'button'; b.setAttribute('aria-pressed', String(p.id === firePage));
      b.onclick = function () { firePage = p.id; renderFire(); };
      pick.appendChild(b);
    });
    if (page) {
      $('ltFireBox').style.aspectRatio = page.cols + ' / ' + page.rows;
      const onPress = can('lighting.control') ? function (it) { press(it.props.cue); } : null;
      drawPage($('ltFireGrid'), { cols: page.cols, rows: page.rows, cues: buttonsOf(page) }, onPress);
    }
    const sliders = page ? slidersOf(page) : [];
    $('ltLevels').hidden = !sliders.length;
    $('ltFaders').textContent = '';
    sliders.forEach(function (c) { $('ltFaders').appendChild(fader(c)); });
    renderLoose();
  }
  function fader(cue) {
    const slider = el('input');
    slider.type = 'range'; slider.min = 0; slider.max = 100; slider.disabled = !can('lighting.control');
    slider.value = data && data.levels && data.levels[cue.id] !== undefined ? data.levels[cue.id] : 0;
    slider.setAttribute('aria-label', cue.name);
    const out = el('output', '', slider.value + '%');
    slider.oninput = function () { out.textContent = slider.value + '%'; };
    slider.onchange = async function () {
      showError('');
      try { await send('POST', '/api/admin/lighting/level', { cue: cue.id, level: parseInt(slider.value, 10) }); }
      catch (e) { showError(e.message); }
    };
    return el('label', 'ltfader', el('span', '', cue.name), slider, out);
  }
  function renderLoose() {
    const used = {};
    pages.forEach(function (p) { p.cues.forEach(function (c) { used[c.id] = true; }); });
    const rest = ((data && data.cues) || []).filter(function (c) { return !used[c.id]; });
    $('ltAll').hidden = !(data && data.can_list) || !rest.length;
    $('ltAllCount').textContent = String(rest.length);
    const root = $('ltAllCues');
    root.textContent = '';
    rest.forEach(function (c) {
      if (c.kind === 'slider') { root.appendChild(fader(c)); return; }
      if (!can('lighting.control')) { root.appendChild(el('span', 'ltchip', c.name)); return; }
      root.appendChild(button(c.name, function () { return press(c.id); }, 'sm' + (data.state && data.state[c.id] ? ' primary' : '')));
    });
  }

  /* ---------------------------------------------------------------- pages: list */
  function pageView(which) {
    $('ltPgList').hidden = which !== 'list';
    $('ltPgNew').hidden = which !== 'new';
    $('ltPgEdit').hidden = which !== 'edit';
  }
  let dragFrom = -1;
  function renderList() {
    const root = $('ltRows');
    root.textContent = '';
    $('ltRowsEmpty').hidden = !!pages.length;
    pages.forEach(function (page, pi) {
      const prev = el('div', 'cuebox ltmini'); const g = el('div', 'cuegrid'); prev.appendChild(g);
      prev.style.aspectRatio = page.cols + ' / ' + page.rows;
      const up = button('↑', async function () { if (move(pages, pi, -1)) await savePages(); }, 'sm icon');
      up.setAttribute('aria-label', 'Move ' + page.name + ' up'); up.disabled = pi === 0;
      const down = button('↓', async function () { if (move(pages, pi, 1)) await savePages(); }, 'sm icon');
      down.setAttribute('aria-label', 'Move ' + page.name + ' down'); down.disabled = pi === pages.length - 1;
      const edit = el('button', 'sm', 'Edit'); edit.type = 'button';
      edit.onclick = function (e) { e.stopPropagation(); openEditor(pi); };
      const row = el('div', 'ltrow',
        el('span', 'ltgrip', '⋮⋮'), prev,
        el('div', 'ltname', el('b', '', page.name), el('small', '', describe(page))),
        el('span', page.key_on ? 'badge ok' : 'badge muted', page.key_on ? 'Screen on' : 'No screen'),
        el('span', 'ltmove', up, down), edit);
      row.draggable = true;
      row.ondragstart = function (e) { dragFrom = pi; e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', 'ltpage'); row.classList.add('ltdrag'); };
      row.ondragend = function () { row.classList.remove('ltdrag'); };
      row.ondragover = function (e) { if (dragFrom >= 0) { e.preventDefault(); row.classList.add('ltover'); } };
      row.ondragleave = function () { row.classList.remove('ltover'); };
      row.ondrop = async function (e) {
        e.preventDefault(); row.classList.remove('ltover');
        const from = dragFrom; dragFrom = -1;
        if (from < 0 || from === pi) return;
        const moved = pages.splice(from, 1)[0]; pages.splice(pi, 0, moved);
        showError('');
        try { await savePages(); } catch (err) { showError(err.message); }
      };
      row.onclick = function (e) { if (!e.target.closest('button')) openEditor(pi); };
      root.appendChild(row);
      drawPage(g, { cols: page.cols, rows: page.rows, cues: buttonsOf(page) }, null);
    });
  }

  /* ---------------------------------------------------------------- pages: new */
  const np = { size: [4, 3], start: 'blank' };
  function newCues() {
    const cols = np.size[0], rows = np.size[1];
    if (np.start === 'copy') {
      const src = pages[parseInt($('ltNpCopy').value, 10)];
      return src ? place(copy(src.cues), cols, rows) : [];
    }
    if (np.start === 'pick') {
      const picked = [];
      $('ltNpTicks').querySelectorAll('input:checked').forEach(function (box) {
        const c = data.cues.filter(function (x) { return x.id === box.value; })[0];
        if (c) picked.push({ id: c.id, name: c.name, x: 0, y: 0, w: 1, h: 1, style: copy(CUE_LOOK) });
      });
      return place(picked, cols, rows);
    }
    return [];
  }
  function drawNew() {
    $('ltNpSize').querySelectorAll('button').forEach(function (b) {
      const on = b.dataset.size === np.size.join('x');
      b.setAttribute('aria-pressed', String(on));
    });
    $('ltNpStart').querySelectorAll('[data-start]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.dataset.start === np.start)); });
    $('ltNpCopyBox').hidden = np.start !== 'copy';
    $('ltNpPickBox').hidden = np.start !== 'pick';
    const cues = newCues();
    const box = $('ltNpPrev').parentNode; box.style.aspectRatio = np.size[0] + ' / ' + np.size[1];
    drawPage($('ltNpPrev'), { cols: np.size[0], rows: np.size[1], cues: cues.filter(function (c) { return kindOf(c.id) !== 'slider'; }) }, null);
    const room = np.size[0] * np.size[1];
    let fit = np.size[0] + ' × ' + np.size[1] + ' cells: room for ' + plural(room, 'button') + '.';
    if (np.start === 'pick') {
      const n = $('ltNpTicks').querySelectorAll('input:checked').length;
      $('ltNpCount').textContent = n + ' picked';
      if (n > cues.length) fit += ' ' + (n - cues.length) + ' picked will not fit: choose a bigger size.';
    }
    if (np.start === 'copy' && pages[parseInt($('ltNpCopy').value, 10)] && cues.length < pages[parseInt($('ltNpCopy').value, 10)].cues.length) {
      fit += ' Some buttons of that page do not fit this size.';
    }
    $('ltNpFit').textContent = fit;
  }
  function openNew() {
    np.size = [4, 3]; np.start = 'blank';
    $('ltNpName').value = '';
    const sizes = $('ltNpSize'); sizes.textContent = '';
    SIZES.forEach(function (s) {
      const pic = el('span', 'ltsizepic'); pic.style.gridTemplateColumns = 'repeat(' + s[0] + ', 1fr)';
      pic.style.aspectRatio = s[0] + ' / ' + s[1];
      for (let k = 0; k < s[0] * s[1]; k++) pic.appendChild(el('i'));
      const b = el('button', 'ltsize', pic, el('span', '', s[0] + ' × ' + s[1]));
      b.type = 'button'; b.dataset.size = s.join('x');
      b.onclick = function () { np.size = s; drawNew(); };
      sizes.appendChild(b);
    });
    const sel = $('ltNpCopy'); sel.textContent = '';
    pages.forEach(function (p, i) { const o = el('option', '', p.name); o.value = String(i); sel.appendChild(o); });
    $('ltNpStart').querySelector('[data-start=copy]').disabled = !pages.length;
    const ticks = $('ltNpTicks'); ticks.textContent = '';
    const listed = ((data && data.cues) || []).filter(function (c) { return c.kind !== 'slider'; });
    listed.forEach(function (c) {
      const box = el('input'); box.type = 'checkbox'; box.value = c.id; box.onchange = drawNew;
      ticks.appendChild(el('label', 'check', box, ' ' + c.name));
    });
    if (!listed.length) ticks.appendChild(el('span', 'muted', data && data.can_list ? 'The lighting program lists no cues.'
      : 'This lighting program does not list its cues: start with an empty page and add cues by id.'));
    $('ltNpStart').querySelector('[data-start=pick]').disabled = !listed.length;
    drawNew();
    pageView('new');
    $('ltNpName').focus();
  }
  $('ltNewBtn').onclick = openNew;
  $('ltNpStart').addEventListener('click', function (e) {
    const b = e.target.closest('[data-start]');
    if (b && !b.disabled) { np.start = b.dataset.start; drawNew(); }
  });
  $('ltNpCopy').onchange = drawNew;
  $('ltNpCreate').onclick = async function () {
    showError('');
    const name = $('ltNpName').value.trim();
    if (!name) { showError('Give the page a name.'); $('ltNpName').focus(); return; }
    const page = { id: '', name: name, key_on: false, cols: np.size[0], rows: np.size[1], cues: newCues() };
    pages.push(page);
    try { await savePages(); } catch (e) { pages.pop(); showError(e.message); return; }
    openEditor(pages.length - 1);
  };

  /* ---------------------------------------------------------------- pages: edit */
  function groupOf(c) {
    const all = (data && data.programs) || [];
    const p = all.filter(function (x) { return x.id === c.program; })[0];
    return all.length > 1 && p ? 'From ' + p.name : 'From the lighting program';
  }
  function palette(page, current) {
    const on = {};
    (current || page.cues.map(toItem)).forEach(function (it) { on[(it.props && it.props.cue) || it.id] = true; });
    return ((data && data.cues) || []).filter(function (c) { return !on[c.id] && c.kind !== 'slider'; }).map(function (c) {
      return { group: groupOf(c), name: c.name, title: c.name, kind: 'button', type: 'cue', w: 1, h: 1,
        props: { cue: c.id }, style: CUE_LOOK };
    });
  }
  function signature() {
    if (!editor) return '';
    const st = editor.get();
    return JSON.stringify({ n: $('ltEdName').value.trim(), c: st.cols, r: st.rows,
      i: st.items.map(function (it) { return [(it.props && it.props.cue) || it.id, it.x, it.y, it.w, it.h, it.title, it.style]; }) });
  }
  function dirty() { return editing >= 0 && !!editor && signature() !== baseline; }
  function showDirty() { $('ltDirty').hidden = !dirty(); }
  function openEditor(pi) {
    const page = pages[pi];
    if (!page) return;
    editing = pi;
    $('ltEdName').value = page.name;
    $('ltEdScreen').checked = !!page.key_on;
    $('ltEdSum').textContent = '· ' + (page.key_on ? 'screen on' : 'no screen') + ' · ' + plural(page.cues.length, 'cue');
    $('ltEdMore').open = false;
    const holder = $('ltEditor'); holder.textContent = '';
    editor = window.RelayLayout.edit(holder, {
      title: '', noun: 'page', paletteName: 'Cues', inline: true, gap: 8, ratio: page.cols + '/' + page.rows, saveLabel: 'Save page',
      cols: page.cols, rows: page.rows, sizes: SIZES.concat([[5, 4]]),
      items: page.cues.map(toItem), palette: palette(page),
      fields: function () { return [{ key: 'cue', label: 'Cue id in the lighting program', type: 'ro' },
        { type: 'note', label: 'A press is sent once and never retried.' }]; },
      addById: { label: 'Add by id', placeholder: 'Cue id (Vista: EX.1.0.1.0.0)',
        make: function (id) { return { name: id, title: id, kind: 'button', type: 'cue', w: 1, h: 1, props: { cue: id }, style: CUE_LOOK }; } },
      onSave: function () { return saveEditor(); },
      onDraw: function (nodes) {
        Object.keys(nodes).forEach(function (id) {
          const it = editor ? editor.get().items.filter(function (i) { return i.id === id; })[0] : null;
          window.RelayLayout.update(nodes[id], { lit: !!(data && data.state && data.state[it ? it.props.cue : id]) });
        });
        showDirty();
      }
    });
    baseline = signature();
    showDirty();
    pageView('edit');
  }
  async function saveEditor() {
    const page = pages[editing];
    const name = $('ltEdName').value.trim();
    if (!name) throw new Error('Give the page a name.');
    const st = editor.get();
    page.name = name; page.cols = st.cols; page.rows = st.rows;
    page.cues = st.items.map(function (it) { return fromItem(it, page); });
    await savePages();
    baseline = signature();
    showDirty();
  }
  async function leaveEditor() {
    if (dirty() && !(await relayUI.confirm('This page has changes that are not saved. Leave without saving them?'))) return false;
    editing = -1; editor = null; baseline = '';
    $('ltEditor').textContent = '';
    renderList();
    pageView('list');
    return true;
  }
  $('ltEdName').oninput = showDirty;
  document.querySelectorAll('#page-lighting [data-ltback]').forEach(function (b) {
    b.onclick = function () { showError(''); if (editing >= 0) leaveEditor(); else pageView('list'); };
  });
  $('ltEdScreen').onchange = async function () {
    showError('');
    const page = pages[editing];
    try {
      await send('PUT', '/api/admin/lighting/pages/' + encodeURIComponent(page.id) + '/screen', { on: $('ltEdScreen').checked });
      await load();
    } catch (e) { $('ltEdScreen').checked = !$('ltEdScreen').checked; showError(e.message); }
  };
  $('ltEdDup').onclick = async function () {
    showError('');
    if (dirty() && !(await relayUI.confirm('Duplicate the page as it was last saved? Changes not yet saved are left out.'))) return;
    const src = pages[editing];
    const dup = { id: '', name: (src.name + ' copy').slice(0, 40), key_on: false, cols: src.cols, rows: src.rows, cues: copy(src.cues) };
    pages.splice(editing + 1, 0, dup);
    try { await savePages(); } catch (e) { pages.splice(editing + 1, 1); showError(e.message); return; }
    baseline = ''; openEditor(editing + 1);
  };
  $('ltEdDel').onclick = async function () {
    showError('');
    const page = pages[editing];
    if (!(await relayUI.confirm('Delete the page "' + page.name + '"? Its screen link stops working.'))) return;
    const at = editing;
    pages.splice(at, 1);
    try { await savePages(); } catch (e) { pages.splice(at, 0, page); showError(e.message); return; }
    baseline = signature();
    editing = -1; editor = null; $('ltEditor').textContent = '';
    renderList(); pageView('list');
  };

  async function savePages() {
    render(await send('PUT', '/api/admin/lighting/pages', { pages: pages.map(function (p) {
      return { id: p.id, name: p.name, key_on: p.key_on, cols: p.cols, rows: p.rows, cues: p.cues };
    }) }));
  }

  /* ---------------------------------------------------------------- screens */
  async function renderScreens() {
    const root = $('ltScreenRows');
    root.textContent = '';
    $('ltScreensEmpty').hidden = !!pages.length;
    for (const page of pages) {
      const sw = el('input'); sw.type = 'checkbox'; sw.checked = !!page.key_on; sw.setAttribute('aria-label', 'Screen for ' + page.name);
      sw.onchange = async function () {
        showError('');
        try { await send('PUT', '/api/admin/lighting/pages/' + encodeURIComponent(page.id) + '/screen', { on: sw.checked }); await load(); }
        catch (e) { sw.checked = !sw.checked; showError(e.message); }
      };
      const linkCell = el('td');
      const actions = el('td');
      root.appendChild(el('tr', '',
        el('td', '', el('b', '', page.name), el('br'), el('span', 'muted', describe(page))),
        el('td', '', el('label', 'switch', sw, el('span', 'slider'))), linkCell, actions));
      if (!page.key_on) { linkCell.appendChild(el('span', 'muted', 'Off. Turn it on to make a link.')); continue; }
      const text = el('span', 'ltlinktext', '…');
      const box = el('div', 'ltlink', text);
      linkCell.appendChild(box);
      let link = '';
      box.appendChild(button('Copy', async function () {
        if (!link) return;
        try { await navigator.clipboard.writeText(link); relayUI.say(text, 'Copied'); setTimeout(function () { text.textContent = link; }, 1200); }
        catch (e) { throw new Error('Copying is blocked here: select the link and copy it.'); }
      }, 'sm'));
      actions.appendChild(button('New key', async function () {
        if (!(await relayUI.confirm('The old link for "' + page.name + '" stops working. Continue?'))) return;
        await send('PUT', '/api/admin/lighting/pages/' + encodeURIComponent(page.id) + '/screen', { on: true, rotate: true });
        await load();
      }, 'sm'));
      send('GET', '/api/admin/lighting/pages/' + encodeURIComponent(page.id) + '/link')
        .then(function (r) { link = r.link || ''; text.textContent = link || 'No link yet.'; text.title = link; })
        .catch(function (e) { text.textContent = ''; showError(e.message); });
    }
  }

  /* ---------------------------------------------------------------- program */
  let progs = [];                          // the programs being edited (a copy of data.programs)
  let cur = 0;                             // the index in `progs` open in the form
  function backendOf(id) { return ((data && data.backends) || []).filter(function (b) { return b.id === id; })[0]; }
  function readForm() {
    const p = progs[cur];
    if (!p) return;
    p.name = $('ltName').value; p.host = $('ltHost').value; p.console = $('ltConsole').value;
    p.functions = $('ltFunctions').checked;
    p.fade = $('ltFade').value === '' ? '' : parseFloat($('ltFade').value);
    p.port = parseInt($('ltPort').value || '0', 10);
    if ($('ltPass').value) p.password = $('ltPass').value;
  }
  function fillForm() {
    const p = progs[cur] || {};
    $('ltName').value = p.name || ''; $('ltHost').value = p.host || ''; $('ltPort').value = p.port || '';
    $('ltPass').value = '';
    $('ltFunctions').checked = !!p.functions; $('ltConsole').value = p.console || 'EX';
    $('ltFade').value = p.fade === '' || p.fade == null ? '' : p.fade;
    $('ltPass').placeholder = p.has_password ? '(saved)' : '';
    ['ltName', 'ltHost', 'ltPort', 'ltPass', 'ltFunctions', 'ltConsole', 'ltFade', 'ltSave', 'ltTest', 'ltAdd', 'ltRemove']
      .forEach(function (id) { $(id).disabled = !manage(); });
    $('ltStatus').textContent = p.error || (p.active ? 'Connected.' : '');
  }
  function renderProgram() {
    const list = $('ltList');
    list.textContent = '';
    progs.forEach(function (p, i) {
      const b = el('button', 'ltopt', el('b', '', p.name || 'Lighting program'),
        el('span', '', (backendOf(p.backend) || {}).label || 'Choose a kind below'));
      b.type = 'button'; b.setAttribute('aria-pressed', String(i === cur));
      b.onclick = function () { readForm(); cur = i; fillForm(); renderProgram(); };
      list.appendChild(b);
    });
    const root = $('ltProgs');
    root.textContent = '';
    const chosen = (progs[cur] || {}).backend || '';
    (data.backends || []).forEach(function (b) {
      const o = el('button', 'ltopt', el('b', '', b.label), b.about ? el('span', '', b.about) : null);
      o.type = 'button'; o.setAttribute('aria-pressed', String(b.id === chosen)); o.disabled = !manage() || !progs[cur];
      o.onclick = function () { readForm(); progs[cur].backend = b.id; renderProgram(); };
      root.appendChild(o);
    });
    const b = backendOf(chosen);
    $('ltConn').hidden = !progs[cur];
    $('ltProgs').hidden = !progs[cur];
    if (!progs[cur]) return;
    const fields = b ? b.fields || ['host', 'port'] : [];
    $('ltConnTitle').textContent = (b ? b.label : 'Program') + ' connection';
    document.querySelectorAll('#page-lighting [data-ltfield]').forEach(function (f) { f.hidden = fields.indexOf(f.dataset.ltfield) < 0; });
    $('ltPort').placeholder = b && b.default_port ? String(b.default_port) : '';
    $('ltPortHint').textContent = b && b.default_port ? 'Empty uses ' + b.default_port + ', the program\'s own.' : '';
  }
  function fillProgram(d) {
    progs = copy(d.programs || []);
    if (cur >= progs.length) cur = Math.max(0, progs.length - 1);
    fillForm();
    renderProgram();
  }
  $('ltAdd').onclick = function () {
    readForm();
    progs.push({ id: '', name: '', backend: '', host: '', port: 0, functions: false, console: 'EX', fade: '' });
    cur = progs.length - 1; fillForm(); renderProgram();
  };
  $('ltRemove').onclick = async function () {
    if (!progs[cur] || !(await window.relayUI.confirm('Remove ' + (progs[cur].name || 'this program') + '? Cues on pages that use it stop working.'))) return;
    progs.splice(cur, 1); cur = Math.max(0, cur - 1); fillForm(); renderProgram();
    $('ltSave').click();
  };
  $('ltSave').onclick = async function () {
    showError('');
    readForm();
    try {
      const d = await send('PUT', '/api/admin/lighting/settings', { programs: progs });
      fillProgram(d); render(d);
    } catch (e) { showError(e.message); }
  };
  $('ltTest').onclick = async function () {
    showError('');
    try {
      const r = await send('POST', '/api/admin/lighting/test', { program: (progs[cur] || {}).id || '' });
      $('ltStatus').textContent = r.detail || '';
    } catch (e) { showError(e.message); }
  };

  /* ---------------------------------------------------------------- load */
  function render(d) {
    data = d;
    pages = copy(d.pages || []);
    $('ltSum').textContent = d.active ? 'ready' : 'not set up';
    renderFire();
    if (editing >= 0 && pages[editing]) {
      const page = pages[editing];
      $('ltEdScreen').checked = !!page.key_on;
      $('ltEdSum').textContent = '· ' + (page.key_on ? 'screen on' : 'no screen') + ' · ' + plural(page.cues.length, 'cue');
    } else if (editing >= 0) {
      editing = -1; editor = null; $('ltEditor').textContent = ''; pageView('list');
    }
    renderList();
    if (tab === 'screens') renderScreens();
  }
  async function load() {
    const d = await send('GET', '/api/admin/lighting');
    render(d);
    if (firstLoad) {
      firstLoad = false;
      fillProgram(d);
      showTab(!(d.programs || []).length && manage() ? 'program' : tab);
      pageView('list');
    } else {
      $('ltStatus').textContent = d.error || $('ltStatus').textContent;
    }
  }
  window.addEventListener('hashchange', function () { if (location.hash === '#lighting') load().catch(function (e) { showError(e.message); }); });
  if (location.hash === '#lighting') load().catch(function (e) { showError(e.message); });
})();
