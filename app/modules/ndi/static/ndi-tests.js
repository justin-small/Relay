/* NDI test patterns (#302): the editor for the test sources Relay sends (bars, grid, tone, your own picture). The server
   (testpatterns.py) draws and sends them; this edits the list. Edits stay in `draft` until Save, so a live update never
   throws away what is being typed. Loaded only while the module is on, after ndi-router.js. */
(function () {
  const N = window.RelayNdi, R = window.RelayAdmin;
  if (!N || !R || !document.getElementById('ndiTests')) return;
  const $ = R.$, el = relayUI.el;
  const PATTERNS = [['bars', 'Colour bars'], ['grid', 'Grid and crosshair'], ['ramp', 'Greyscale ramp'], ['white', 'White'],
    ['black', 'Black'], ['red', 'Red'], ['green', 'Green'], ['blue', 'Blue'], ['motion', 'Moving element (shows dropped frames)'],
    ['alpha', 'Alpha checker'], ['image', 'Your own picture (PNG)']];
  const AUDIO = [['', 'No audio'], ['tone', '1 kHz tone'], ['id', 'Left, then right'], ['silence', 'Silence']];
  const LEVELS = [0, -3, -9, -12, -18, -20, -30, -40];
  const SIZES = ['640x360', '1280x720', '1920x1080', '3840x2160'];
  let draft = null, serverSig = '', dirty = false, rows = [];

  function blank() { return { id: '', name: '', pattern: 'bars', burn: true, audio: '', level: -18, size: '1280x720', fps: 30, on: false }; }
  function pick(list, value, onchange, label, disabled) {
    const s = el('select', { disabled: disabled, 'aria-label': label });
    list.forEach(function (o) { const v = Array.isArray(o) ? o[0] : o, t = Array.isArray(o) ? o[1] : o; s.appendChild(el('option', { value: String(v) }, t)); });
    s.value = String(value); s.onchange = function () { onchange(s.value); };
    return s;
  }
  function field(label, node) { const f = el('div', 'field'); f.appendChild(el('label', '', label)); f.appendChild(node); return f; }
  function touch() { dirty = true; }
  function stateText(t) {
    if (t.state === 'on') return ['ok', 'Sending, ' + t.fps_now + ' fps, ' + t.cpu + '% of one core'];
    if (t.state === 'error') return ['err', t.error];
    if (t.state === 'starting') return ['warn', 'Starting'];
    return ['', t.on ? 'Not running' : 'Off'];
  }

  function row(t, i, can) {
    const box = el('div', 'ndioutrow ndiTest');
    const sw = el('label', 'switch');
    const on = el('input', { type: 'checkbox', checked: !!t.on, disabled: !can }); on.setAttribute('aria-label', 'Send ' + (t.name || 'this test source'));
    on.onchange = function () { t.on = on.checked; touch(); };
    sw.appendChild(on); sw.appendChild(el('span', 'slider'));
    box.appendChild(sw);
    const body = el('div', 'ndiTestBody');
    const name = el('input', { value: t.name, maxlength: 60, autocomplete: 'off', placeholder: 'Lobby bars', disabled: !can, 'aria-label': 'Name' });
    name.oninput = function () { t.name = name.value; touch(); };
    const grid = el('div', 'row');
    grid.appendChild(field('Name', name));
    grid.appendChild(field('Picture', pick(PATTERNS, t.pattern, function (v) { t.pattern = v; touch(); draw(); }, 'Picture', !can)));
    grid.appendChild(field('Size', pick(SIZES, t.size, function (v) { t.size = v; touch(); }, 'Size', !can)));
    const fps = el('input', { type: 'number', min: 5, max: 60, step: 1, value: t.fps, disabled: !can, 'aria-label': 'Frames per second' });
    fps.oninput = function () { t.fps = parseInt(fps.value, 10) || 30; touch(); };
    grid.appendChild(field('Frames per second', fps));
    grid.appendChild(field('Sound', pick(AUDIO, t.audio, function (v) { t.audio = v; touch(); draw(); }, 'Sound', !can)));
    if (t.audio === 'tone' || t.audio === 'id') {
      grid.appendChild(field('Level', pick(LEVELS.map(function (l) { return [l, l + ' dBFS']; }), t.level, function (v) { t.level = +v; touch(); }, 'Tone level', !can)));
    }
    body.appendChild(grid);
    const burn = el('label', 'check'); const bc = el('input', { type: 'checkbox', checked: !!t.burn, disabled: !can });
    bc.onchange = function () { t.burn = bc.checked; touch(); };
    burn.appendChild(bc); burn.appendChild(document.createTextNode(' Write the name, size and a running clock on the picture'));
    body.appendChild(burn);
    if (t.pattern === 'image') body.appendChild(imageControl(t, can));
    const st = el('span', 'ndistate'); st.dataset.test = t.id;
    body.appendChild(st);
    if (t.id) { const code = el('code', 'hint'); code.dataset.ndi = t.id; body.appendChild(code); }
    box.appendChild(body);
    const rm = el('button', { type: 'button', disabled: !can }, 'Remove'); rm.onclick = function () { draft.splice(i, 1); touch(); draw(); };
    box.appendChild(rm);
    return box;
  }

  function imageControl(t, can) {
    const wrap = el('div', 'ctl');
    if (!t.id) { wrap.appendChild(el('span', 'hint', 'Save this source first, then choose its picture.')); return wrap; }
    const file = el('input', { type: 'file', accept: 'image/png', disabled: !can, 'aria-label': 'PNG picture' });
    file.onchange = async function () {
      const f = file.files[0]; if (!f) return;
      N.showError('');
      try {
        const res = await window.fetch('/api/admin/ndi/tests/' + encodeURIComponent(t.id) + '/image',
          { method: 'PUT', credentials: 'same-origin', headers: { 'Content-Type': 'image/png' }, body: f });
        const out = await res.json().catch(function () { return {}; });
        if (!res.ok) throw new Error(out.error || 'That did not work.');
        N.draw(out);
      } catch (e) { N.showError(e.message); }
    };
    wrap.appendChild(file);
    wrap.appendChild(el('span', 'hint', t.has_image ? 'A picture is set. Choose another to replace it. A PNG keeps its transparency.' : 'No picture yet. Choose a PNG.'));
    return wrap;
  }

  function draw() {
    const box = $('ndiTests'), can = N.can('ndi.manage');
    box.textContent = '';
    rows = [];
    if (!draft.length) box.appendChild(el('p', 'note', 'No test sources yet. Add one.'));
    draft.forEach(function (t, i) { const r = row(t, i, can); rows.push(r); box.appendChild(r); });
    $('ndiTestAdd').disabled = $('ndiTestSave').disabled = !can;
    status(window.RelayNdiState && window.RelayNdiState());
  }

  function status(s) {          // the running state of each saved source, updated in place
    if (!s) return;
    (s.tests || []).forEach(function (t) {
      const st = $('ndiTests').querySelector('[data-test="' + t.id + '"]'); if (!st) return;
      const x = stateText(t); st.className = 'ndistate ' + x[0]; st.textContent = x[1];
      const code = $('ndiTests').querySelector('[data-ndi="' + t.id + '"]'); if (code) code.textContent = t.ndi;
    });
  }

  function update(s) {
    const sig = JSON.stringify((s.settings.tests || []).map(function (t) { return [t, s.settings.tests_on.indexOf(t.id) >= 0]; }));
    if (draft === null || (sig !== serverSig && !dirty)) {
      serverSig = sig;
      const byId = {}; (s.tests || []).forEach(function (t) { byId[t.id] = t; });
      draft = (s.settings.tests || []).map(function (t) { return Object.assign({}, t, { on: s.settings.tests_on.indexOf(t.id) >= 0, has_image: !!(byId[t.id] && byId[t.id].has_image) }); });
      if (!$('ndiTests').contains(document.activeElement)) draw();
    } else {
      const imgs = {}; (s.tests || []).forEach(function (t) { imgs[t.id] = !!t.has_image; });
      let redraw = false;
      draft.forEach(function (t) { if (t.id && t.has_image !== imgs[t.id]) { t.has_image = imgs[t.id]; redraw = true; } });
      if (redraw && !$('ndiTests').contains(document.activeElement)) draw();
      status(s);
    }
  }

  async function save() {
    const tests = draft.map(function (t) { return { id: t.id, name: t.name.trim(), pattern: t.pattern, burn: t.burn, audio: t.audio, level: t.level, size: t.size, fps: t.fps }; });
    const names = tests.map(function (t) { return t.name; });
    if (names.some(function (n) { return !n; })) { show('Give every test source a name.'); return; }
    const on = draft.filter(function (t) { return t.on && t.id; }).map(function (t) { return t.id; });
    show('');
    try {
      let out = await relayUI.api('PUT', '/api/admin/ndi/tests', { tests: tests, on: on });
      // New sources got their ids on that save; switch on the ones that were asked for, matching them by name.
      const want = draft.filter(function (t) { return t.on && !t.id; }).map(function (t) { return t.name.trim().toLowerCase(); });
      if (want.length) {
        const ids = out.settings.tests.filter(function (t) { return want.indexOf(t.name.toLowerCase()) >= 0; }).map(function (t) { return t.id; });
        out = await relayUI.api('PUT', '/api/admin/ndi/tests', { tests: out.settings.tests, on: on.concat(ids) });
      }
      dirty = false; draft = null;
      N.draw(out);
    } catch (e) { show(e.message); }
  }
  function show(t) { $('ndiTestErr').textContent = t || ''; $('ndiTestErr').hidden = !t; }

  $('ndiTestAdd').onclick = function () { if (draft.length >= 12) { show('Keep at most 12 test sources.'); return; } draft.push(blank()); touch(); draw(); };
  $('ndiTestSave').onclick = save;
  const previous = window.RelayNdiMore;
  window.RelayNdiMore = function (s) { if (previous) previous(s); update(s); };
})();
