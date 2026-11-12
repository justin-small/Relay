/* The Broadcast overlay page (#139): build an overlay against a live preview, copy its link, or save it by name.
   The preview is the real /overlay page in an iframe with ?demo=1 (sample captions played through the same
   renderer), so what the operator sees here is what the switcher gets. Option names, defaults and presets come
   from the server (app/overlays.py); the server checks every value again whichever way it arrives. Everything
   from the server is set as text. */
(function () {
  const $ = function (id) { return document.getElementById(id); };
  if (!$('page-overlay')) return;

  async function call(path, opts) {
    const res = await fetch(path, Object.assign({ cache: 'no-store' }, opts || {}));
    const data = await res.json().catch(function () { return {}; });
    if (!res.ok) throw new Error(data.error || data.detail || ('HTTP ' + res.status));
    return data;
  }
  function send(method, path, body) {
    return call(path, { method: method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  }
  function h(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  let info = null;          // GET /api/admin/overlays
  let S = null;             // the overlay being built: every option, resolved
  let editing = null;       // id of the saved overlay being edited, or null
  let host = location.origin;
  let loaded = false;

  const GREEN = '#00B140';
  const HINTS = {
    mode: { rollup: 'Lines scroll up as new words arrive, like TV live captions.',
            popon: 'Finished sentences appear as a block and the next block replaces it. Calmer, a little later.' },
    out: { transparent: 'For OBS, vMix and ProPresenter. Nothing behind the text.',
           green: 'For a switcher input keyed on green.',
           matte: 'White-on-black key signal for an ATEM luma key. Pair it with a Black copy as the fill.',
           black: 'Solid black: a full-screen slate on its own output, or the fill for a key matte.' }
  };

  /* The settings a preset starts from: defaults, then the preset's own. */
  function base(preset) {
    const out = Object.assign({}, info.defaults);
    const p = info.presets[preset] || {};
    Object.keys(p).forEach(function (k) { if (k !== 'label') out[k] = p[k]; });
    return out;
  }
  function resolved(raw) {
    const out = base(raw.preset || '');
    Object.keys(raw).forEach(function (k) { if (k in out) out[k] = raw[k]; });
    return out;
  }
  /* What differs from the preset: the short form for the link and for saving. */
  function diff(s) {
    const b = base(s.preset);
    const out = {};
    if (s.preset) out.preset = s.preset;
    Object.keys(info.defaults).forEach(function (k) {
      if (k === 'preset') return;
      if (String(s[k]) !== String(b[k])) out[k] = s[k];
    });
    return out;
  }
  function outputOf(s) {
    if (s.matte) return 'matte';
    const bg = String(s.bg).toUpperCase();
    if (bg === GREEN) return 'green';
    if (bg === '#000000' || bg === '#000' || bg === 'BLACK') return 'black';
    return 'transparent';
  }
  function setOutput(v) {
    S.matte = v === 'matte';
    S.bg = v === 'green' ? GREEN : v === 'black' ? '#000000' : 'transparent';
  }

  function query(obj) {
    const q = new URLSearchParams();
    Object.keys(obj).forEach(function (k) {
      let v = obj[k];
      if (typeof v === 'boolean') v = v ? '1' : '0';
      if (k === 'bg' || k === 'color' || k === 'platec') v = String(v).replace(/^#/, '');
      q.set(k, String(v));
    });
    return q.toString();
  }
  function link() {
    if (editing) return host + '/overlay/' + editing;
    return host + '/overlay?' + query(diff(S));
  }

  /* ---- controls ---- */
  function langOptions(sel, withOriginal) {
    sel.innerHTML = '';
    if (withOriginal) sel.appendChild(new Option('Original (' + info.source_label + ')', ''));
    else sel.appendChild(new Option('First live language', ''));
    info.targets.forEach(function (t) {
      const live = info.live.indexOf(t.target) !== -1;
      sel.appendChild(new Option(t.label + (live ? ' · live' : ''), t.target));
    });
  }

  function renderPresets() {
    const box = $('ovPresets');
    box.innerHTML = '';
    const bars = { lower: [[76, '']], top: [[7, '']], slate: [[20, ''], [34, ''], [48, ''], [62, '']], dual: [[64, ''], [78, 'b']] };
    Object.keys(info.presets).forEach(function (k) {
      const p = info.presets[k];
      const b = h('button', 'ovpreset');
      b.type = 'button';
      b.dataset.p = k;
      const th = h('div', 'th' + (k === 'slate' ? ' dark' : ''));
      (bars[k] || []).forEach(function (bar) {
        const i = h('i', bar[1]);
        i.style.top = bar[0] + '%';
        th.appendChild(i);
      });
      b.appendChild(th);
      b.appendChild(h('span', '', p.label));
      const sub = { lower: 'Bottom, two lines', top: 'Top, one wide line', slate: 'Full screen, large type', dual: 'Two languages, labelled' }[k];
      if (sub) b.appendChild(h('small', '', sub));
      b.addEventListener('click', function () {
        const keep = { lang: S.lang, lang2: S.lang2, w: S.w, h: S.h };
        S = Object.assign(base(k), keep, { preset: k });
        if (k === 'slate') setBackdrop('black'); else setBackdrop('program');
        sync();
      });
      box.appendChild(b);
    });
  }

  function fmt(k, v) {
    if (k === 'hold') return v ? (v / 1000) + ' s' : 'never';
    if (k === 'font') return v + ' px';
    if (k === 'plate') return Math.round(v * 100) + '%';
    if (k === 'lines') return String(v);
    return v + '%';
  }

  function wire() {
    document.querySelectorAll('#ovBuild [data-k]').forEach(function (el) {
      const k = el.dataset.k;
      if (el.classList.contains('seg')) {
        el.querySelectorAll('button').forEach(function (b) {
          b.addEventListener('click', function () {
            if (k === 'out') setOutput(b.dataset.v); else S[k] = b.dataset.v;
            sync();
          });
        });
        return;
      }
      el.addEventListener('input', function () {
        if (k === 'res') {
          const wh = el.value.split('x');
          S.w = +wh[0]; S.h = +wh[1];
        } else if (el.type === 'checkbox') S[k] = el.checked;
        else if (el.type === 'range') S[k] = +el.value;
        else if (el.type === 'color') S[k] = el.value.slice(1).toUpperCase();
        else S[k] = el.value;
        sync();
      });
    });
    document.querySelectorAll('#ovBackdrop button').forEach(function (b) {
      b.addEventListener('click', function () { setBackdrop(b.dataset.v); });
    });
    $('ovGuideLines').addEventListener('change', guide);
    $('ovSample').addEventListener('change', function () { lastSrc = ''; preview(); });
    $('ovCopy').addEventListener('click', copy);
    $('ovSaveForm').addEventListener('submit', function (ev) { ev.preventDefault(); save(); });
    $('ovEditDone').addEventListener('click', function () { stopEditing(); sync(); });
    document.querySelectorAll('[data-ovtab]').forEach(function (b) {
      b.addEventListener('click', function () {
        document.querySelectorAll('[data-ovtab]').forEach(function (x) {
          x.classList.toggle('on', x === b);
          x.setAttribute('aria-selected', x === b ? 'true' : 'false');
        });
        $('ovBuild').hidden = b.dataset.ovtab !== 'build';
        $('ovGuides').hidden = b.dataset.ovtab !== 'guides';
      });
    });
    window.addEventListener('resize', guide);
  }

  function setBackdrop(v) {
    document.querySelectorAll('#ovBackdrop button').forEach(function (x) { x.classList.toggle('on', x.dataset.v === v); });
    $('ovStage').className = 'ovstage bk-' + v;
  }

  /* Reflect S into every control, then the link and the preview. */
  function sync() {
    document.querySelectorAll('#ovBuild [data-k]').forEach(function (el) {
      const k = el.dataset.k;
      const v = k === 'out' ? outputOf(S) : k === 'res' ? S.w + 'x' + S.h : S[k];
      if (el.classList.contains('seg')) {
        el.querySelectorAll('button').forEach(function (b) { b.classList.toggle('on', b.dataset.v === v); });
      } else if (el.type === 'checkbox') el.checked = !!v;
      else if (el.type === 'color') el.value = '#' + String(v).replace(/^#/, '');
      else if (k === 'res') {
        if (![].some.call(el.options, function (o) { return o.value === v; })) el.appendChild(new Option(S.w + ' × ' + S.h, v));
        el.value = v;
      } else el.value = v;
      const out = el.parentElement && el.parentElement.querySelector('output');
      if (out) out.textContent = fmt(k, S[k]);
    });
    document.querySelectorAll('.ovpreset').forEach(function (b) { b.classList.toggle('on', b.dataset.p === S.preset); });
    const both = S.stream === 'both';
    $('ovLang2F').hidden = !both;
    $('ovLabelsF').hidden = !both;
    $('ovLangF').hidden = S.stream === 'transcription';
    $('ovLangLbl').textContent = both ? 'First language' : 'Language';
    $('ovPlatecF').hidden = $('ovPlateF').hidden = S.edge !== 'plate';
    $('ovModeHint').textContent = HINTS.mode[S.mode] || '';
    $('ovBgHint').textContent = HINTS.out[outputOf(S)] || '';
    const finals = document.querySelector('#ovBuild [data-k=finals]');
    finals.disabled = S.mode === 'popon';      // pop-on is finished lines by definition
    if (S.mode === 'popon') finals.checked = true;
    $('ovStage').style.aspectRatio = S.w + ' / ' + S.h;
    const url = link();
    $('ovUrl').textContent = url;
    $('ovOpen').href = url;
    guide();
    preview();
  }

  /* The title-safe guide over the preview, in the stage's own pixels. */
  function guide() {
    const g = $('ovSafe-guide');
    g.hidden = !$('ovGuideLines').checked;
    const box = $('ovStage').getBoundingClientRect();
    const x = box.width * S.safe / 100, y = box.height * S.safe / 100;
    g.style.left = g.style.right = x + 'px';
    g.style.top = g.style.bottom = y + 'px';
  }

  let lastSrc = '';
  let previewTimer = null;
  function preview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(function () {
      // The preview always uses the long form, so an edit shows before it is saved.
      const q = diff(S);
      if ($('ovSample').checked) q.demo = '1';
      const src = '/overlay?' + query(q);
      if (src !== lastSrc) { lastSrc = src; $('ovFrame').src = src; }
    }, 250);
  }

  async function copy() {
    const btn = $('ovCopy');
    try {
      await navigator.clipboard.writeText(link());
      btn.textContent = 'Copied';
    } catch (e) {
      // Plain HTTP on a LAN address has no clipboard API: select the text instead.
      const r = document.createRange();
      r.selectNodeContents($('ovUrl'));
      const sel = window.getSelection();
      sel.removeAllRanges(); sel.addRange(r);
      btn.textContent = 'Press Ctrl/⌘+C';
    }
    setTimeout(function () { btn.textContent = 'Copy link'; }, 1600);
  }

  /* ---- saved overlays ---- */
  function startEditing(o) {
    editing = o.id;
    S = resolved(o.settings);
    $('ovName').value = o.name;
    $('ovSave').textContent = 'Save changes';
    $('ovEditing').hidden = false;
    $('ovEditingName').textContent = o.name;
    renderList();
    sync();
    try { $('page-overlay').scrollIntoView({ block: 'start' }); } catch (e) {}
  }
  function stopEditing() {
    editing = null;
    $('ovName').value = '';
    $('ovSave').textContent = 'Save';
    $('ovEditing').hidden = true;
    renderList();
  }

  async function save() {
    const name = $('ovName').value.trim();
    const body = { name: name, settings: diff(S) };
    try {
      const res = editing ? await send('PUT', '/api/admin/overlays/' + encodeURIComponent(editing), body)
                          : await send('POST', '/api/admin/overlays', body);
      info = res;
      const saved = info.overlays.filter(function (o) { return o.id === res.saved; })[0];
      if (saved) startEditing(saved);
      flash('Saved. The link is ' + host + '/overlay/' + res.saved);
    } catch (e) {
      flash(e.message, true);
    }
  }

  async function remove(o) {
    if (!confirm('Delete the overlay “' + o.name + '”? Any browser source using /overlay/' + o.id + ' will stop showing captions.')) return;
    try {
      info = await send('DELETE', '/api/admin/overlays/' + encodeURIComponent(o.id));
      if (editing === o.id) stopEditing();
      renderList();
      sync();
    } catch (e) {
      flash(e.message, true);
    }
  }

  function flash(msg, bad) {
    // The page-wide message line admin.js uses (#alert); fall back to an alert if it is not there.
    const box = $('alert');
    if (box) {
      box.className = 'field ' + (bad ? 'err' : 'ok');
      box.textContent = msg;
      box.hidden = false;
      if (!bad) setTimeout(function () { box.hidden = true; }, 3500);
    } else if (bad) alert(msg);
  }

  function renderList() {
    const body = $('ovList');
    body.innerHTML = '';
    if (!info.overlays.length) {
      const tr = h('tr');
      const td = h('td', 'muted', 'None saved yet.');
      td.colSpan = 3;
      tr.appendChild(td);
      body.appendChild(tr);
      return;
    }
    info.overlays.forEach(function (o) {
      const tr = h('tr', o.id === editing ? 'on' : '');
      tr.appendChild(h('td', '', o.name));
      tr.appendChild(h('td', 'mono', '/overlay/' + o.id));
      const td = h('td');
      const acts = h('div', 'rowacts');
      const cp = h('button', '', 'Copy link');
      cp.type = 'button';
      cp.addEventListener('click', function () {
        navigator.clipboard.writeText(host + '/overlay/' + o.id).then(function () {
          cp.textContent = 'Copied';
          setTimeout(function () { cp.textContent = 'Copy link'; }, 1600);
        }).catch(function () { startEditing(o); });
      });
      const ed = h('button', '', 'Edit');
      ed.type = 'button';
      ed.addEventListener('click', function () { startEditing(o); });
      const del = h('button', '', 'Delete');
      del.type = 'button';
      del.addEventListener('click', function () { remove(o); });
      acts.appendChild(cp); acts.appendChild(ed); acts.appendChild(del);
      td.appendChild(acts);
      tr.appendChild(td);
      body.appendChild(tr);
    });
  }

  async function load() {
    if (loaded) { guide(); return; }
    try {
      const both = await Promise.all([call('/api/admin/overlays'), call('/api/admin/state').catch(function () { return {}; })]);
      info = both[0];
      // The LAN address, as on Viewer links: 127.0.0.1 works only on this machine.
      const urls = (both[1].urls || []).filter(function (u) { return u.indexOf('127.0.0.1') === -1; });
      if (urls.length) host = urls[0].replace(/\/+$/, '');
    } catch (e) {
      flash(e.message, true);
      return;
    }
    loaded = true;
    S = base('lower');
    S.preset = 'lower';
    if (info.live.length) S.lang = info.live[0];
    langOptions($('ovLang'), false);
    langOptions($('ovLang2'), true);
    renderPresets();
    wire();
    renderList();
    sync();
  }

  if (location.hash === '#overlay') load();
  window.addEventListener('hashchange', function () { if (location.hash === '#overlay') load(); });
})();
