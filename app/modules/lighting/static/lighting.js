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

  const picked = {};           // page index -> index of the cue whose look is being edited
  function field(label, input) {
    const l = el('label', label + ' ');
    l.appendChild(input);
    return l;
  }
  function styleForm(page, pi) {
    const c = page.cues[picked[pi]];
    const box = el('div', '', 'row');
    if (!c) return box;
    function bind(input, key, num) {
      input.oninput = function () { c[key] = num ? parseInt(input.value || '0', 10) : input.value; renderPreview(page, pi); };
      return input;
    }
    const label = el('input'); label.maxLength = 60; label.value = c.label || ''; label.placeholder = c.name;
    box.appendChild(field('Text', bind(label, 'label')));
    const shape = el('select');
    [['rounded', 'Rounded'], ['rect', 'Square corners'], ['pill', 'Pill'], ['circle', 'Circle']].forEach(function (s) {
      const o = el('option', s[1]); o.value = s[0]; if (c.shape === s[0]) o.selected = true; shape.appendChild(o);
    });
    shape.onchange = function () { c.shape = shape.value; renderPreview(page, pi); };
    box.appendChild(field('Shape', shape));
    [['color', 'Colour'], ['text_color', 'Text colour']].forEach(function (k) {
      const i = el('input'); i.type = 'color'; i.value = c[k[0]] || '#2b5fd9'; box.appendChild(field(k[1], bind(i, k[0])));
    });
    if (!page.free) {
      [['w', 'Wide'], ['h', 'Tall']].forEach(function (k) {
        const i = el('input'); i.type = 'number'; i.min = 1; i.max = 6; i.value = c[k[0]] || 1; i.style.width = '4em';
        box.appendChild(field(k[1] + ' (cells)', bind(i, k[0], true)));
      });
    }
    return box;
  }
  // The preview draws with the same code as /screen. In a free page a button drags and its corner resizes (percent
  // of the page, kept inside it); in a grid page tapping picks the cue whose look the form below edits.
  function renderPreview(page, pi) {
    const root = page._preview;
    root.className = page.free ? 'ltfree' : 'ltgrid';
    root.style.cssText = page.free ? 'position:relative;aspect-ratio:16/10;background:#111;border-radius:8px' : 'background:#111;padding:8px;border-radius:8px';
    const drawn = window.LightingButtons.draw(root, page, function (c) { picked[pi] = page.cues.indexOf(c); renderPreview(page, pi); });
    page.cues.forEach(function (c, ci) {
      const b = drawn.buttons[c.id];
      if (ci === picked[pi]) b.classList.add('picked');
      if (!page.free || !can('lighting.manage')) return;
      b.style.touchAction = 'none';
      b.onpointerdown = function (ev) {
        if (ev.target !== b) return;
        picked[pi] = ci; b.setPointerCapture(ev.pointerId);
        const r = root.getBoundingClientRect(), sx = ev.clientX, sy = ev.clientY, ox = c.x, oy = c.y;
        b.onpointermove = function (e) {
          c.x = Math.max(0, Math.min(100 - c.fw, Math.round(ox + (e.clientX - sx) / r.width * 100)));
          c.y = Math.max(0, Math.min(100 - c.fh, Math.round(oy + (e.clientY - sy) / r.height * 100)));
          drawn.place(b, c, true);
        };
        b.onpointerup = function () { b.onpointermove = null; b.onpointerup = null; renderPreview(page, pi); };
      };
      if (ci === picked[pi]) {
        const h = el('span', '', 'ltresize');
        h.onpointerdown = function (ev) {
          ev.stopPropagation(); h.setPointerCapture(ev.pointerId);
          const r = root.getBoundingClientRect(), sx = ev.clientX, sy = ev.clientY, ow = c.fw, oh = c.fh;
          h.onpointermove = function (e) {
            c.fw = Math.max(4, Math.min(100 - c.x, Math.round(ow + (e.clientX - sx) / r.width * 100)));
            c.fh = Math.max(4, Math.min(100 - c.y, Math.round(oh + (e.clientY - sy) / r.height * 100)));
            drawn.place(b, c, true);
          };
          h.onpointerup = function () { h.onpointermove = null; h.onpointerup = null; renderPreview(page, pi); };
        };
        b.appendChild(h);
      }
    });
    const f = page._form;
    if (f) { f.textContent = ''; if (picked[pi] !== undefined) f.appendChild(styleForm(page, pi)); }
  }
  async function screenControls(page) {
    const box = el('div', '', 'row');
    const on = !!page.key_on;
    const link = el('input'); link.readOnly = true; link.size = 40; link.hidden = true;
    async function show() {
      const r = await send('GET', '/api/admin/lighting/pages/' + encodeURIComponent(page.id) + '/link');
      link.value = r.link; link.hidden = !r.link;
    }
    if (!page.id) { box.appendChild(el('span', 'Save the pages first to get this screen\'s link.')); return box; }
    box.appendChild(button(on ? 'Turn screen off' : 'Turn screen on', async function () {
      await send('PUT', '/api/admin/lighting/pages/' + encodeURIComponent(page.id) + '/screen', { on: !on });
      await load();
    }));
    if (on) {
      box.appendChild(button('New key', async function () {
        if (!window.confirm('The old link stops working. Continue?')) return;
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
          row.appendChild(button('Look', function () { picked[pi] = ci; renderPreview(page, pi); }));
          row.appendChild(button('Up', function () { move(page.cues, ci, -1); renderPages(); }));
          row.appendChild(button('Down', function () { move(page.cues, ci, 1); renderPages(); }));
          row.appendChild(button('Remove', function () { page.cues.splice(ci, 1); delete picked[pi]; renderPages(); }));
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
          page.cues.push(newCue(id.value.trim(), nm.value.trim() || id.value.trim(), page)); renderPages();
        }));
        card.appendChild(button('Up', function () { move(pages, pi, -1); renderPages(); }));
        card.appendChild(button('Down', function () { move(pages, pi, 1); renderPages(); }));
        card.appendChild(button('Delete page', function () { pages.splice(pi, 1); renderPages(); }));
        const free = el('input'); free.type = 'checkbox'; free.checked = !!page.free;
        free.onchange = function () { page.free = free.checked; if (page.free) arrange(page); renderPages(); };
        card.appendChild(field('Place buttons freely (drag and resize)', free));
      }
      page._preview = el('div');
      page._form = el('div');
      card.appendChild(el('h4', 'How it looks on the screen'));
      card.appendChild(page._preview);
      card.appendChild(page._form);
      if (manage) {
        const holder = el('div');
        card.appendChild(holder);
        screenControls(page).then(function (c) { holder.appendChild(c); });
      }
      root.appendChild(card);
      renderPreview(page, pi);
    });
    $('ltPageCtl').hidden = !manage;
    renderAll();
  }
  function newCue(id, name, page) {
    const c = { id: id, name: name, label: '', shape: 'rounded', color: '#2b5fd9', text_color: '#ffffff', w: 1, h: 1, x: 0, y: 0, fw: 25, fh: 20 };
    if (page.free) arrange(page, c);
    return c;
  }
  // Lay free buttons out in rows of four the first time a page is made free, or when a cue is added to one.
  function arrange(page, only) {
    page.cues.forEach(function (c, i) {
      if (only && c !== only) return;
      if (!only && (c.x || c.y)) return;
      c.fw = c.fw || 25; c.fh = c.fh || 20;
      c.x = (i % 4) * 25; c.y = Math.floor(i / 4) * 22 % 80;
    });
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
          pages[0].cues.push(newCue(c.id, c.name, pages[0])); renderPages();
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
  (function () { const l = document.createElement('link'); l.rel = 'stylesheet'; l.href = '/static/modules/lighting/screen.css'; document.head.appendChild(l); })();
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
  $('ltAddPage').onclick = function () { pages.push({ id: '', name: 'New page', free: false, key_on: false, cues: [] }); renderPages(); };
  $('ltSavePages').onclick = async function () {
    showError('');
    try { render(await send('PUT', '/api/admin/lighting/pages', { pages: pages.map(function (p) { const o = {}; Object.keys(p).forEach(function (k) { if (k[0] !== '_') o[k] = p[k]; }); return o; }) })); } catch (e) { showError(e.message); }
  };
  window.addEventListener('hashchange', function () { if (location.hash === '#lighting') load().catch(function (e) { showError(e.message); }); });
  if (location.hash === '#lighting') load().catch(function (e) { showError(e.message); });
})();
