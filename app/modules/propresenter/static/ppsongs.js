/* ProPresenter songs page (#633): build a song (paste, PDF or an opened .pro file), see the slides as ProPresenter draws
   them, arrange it and send it through the Relay Helper. Nothing is kept on the server: the draft and the templates live in
   this browser, and every import, split and template change is shown first and applied only on the operator's yes. */
(function () {
  const $ = function (id) { return document.getElementById(id); };
  if (!$('page-ppsongs')) return;
  const BASE = '/api/admin/propresenter/songs';
  const h = relayUI.el;
  const request = relayUI.client({ cache: 'no-store', status: true, session: true });
  const MAX_LINES = 4, MAX_CHARS = 120;
  const DRAFT = 'relay.pp.songs.draft', TPLS = 'relay.pp.songs.templates';

  let song = blank();
  let cur = 0;            // arrangement tab
  let pick = [0, 0];      // chosen slide: [section, slide]
  let pending = null;     // an arrangement change waiting for Keep or Undo: { before, was }
  let helper = { connected: false };
  let canSend = relayUI.can('propresenter.manage');

  function blank() { return { name: '', music_key: '', ccli_number: '', extra: {}, sections: [section('Verse 1')], arrangements: [] }; }
  function section(name) { return { name: name, slides: [slide('')] }; }
  function slide(text) { return { text: text, translation: '', label: '', hot_key: '', advance_after: 0, completion: '', notes: '' }; }
  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function plain(t) { return String(t || '').replace(/\[[^\[\]\n]{1,20}\]/g, ''); }
  function slideCount(s) { return s.sections.reduce(function (n, x) { return n + x.slides.length; }, 0); }
  function secByName(name) { return song.sections.filter(function (x) { return x.name === name; })[0]; }
  function store(key, val) { try { if (val === undefined) return JSON.parse(localStorage.getItem(key) || 'null'); localStorage.setItem(key, JSON.stringify(val)); } catch (e) { return null; } return val; }

  /* ---- draft: kept in this browser so a refresh does not lose the song ---- */
  let draftTimer = null;
  function saveDraft() {
    clearTimeout(draftTimer);
    draftTimer = setTimeout(function () {
      store(DRAFT, { song: song, cur: cur, lps: $('sngLps').value, tr: $('sngTr').checked, at: Date.now() });
      relayUI.say('sngDraft', 'Draft saved in this browser', false, { plain: true });
    }, 400);
  }

  /* ---- templates: default hot keys, auto-advance and labels, kept in this browser ---- */
  function templates() { return store(TPLS) || []; }
  function fillTemplates(sel) {
    const s = $('sngTpl'); s.textContent = '';
    s.appendChild(h('option', { value: '', text: 'None' }));
    templates().forEach(function (t, i) { s.appendChild(h('option', { value: String(i), text: t.name })); });
    if (sel !== undefined) s.value = sel;
  }
  function useTemplate(s, t) {
    if (!t) return s;
    s.sections.forEach(function (sec, i) {
      sec.slides.forEach(function (sl, j) {
        if (t.labels) sl.label = sec.name;
        if (t.hot_keys && j === 0 && i < 9) sl.hot_key = String(i + 1);
        if (t.advance > 0) { sl.advance_after = t.advance; sl.completion = 'next'; }
      });
    });
    return s;
  }
  function chosenTemplate() { const v = $('sngTpl').value; return v === '' ? null : templates()[+v] || null; }

  /* ---- a change is shown first and kept only on a yes ---- */
  function propose(title, rows, buttons, extra) {
    const box = $('sngProposal'); box.textContent = '';
    const dl = h('div', 'sngdl');
    rows.forEach(function (r) { dl.appendChild(h('span', 'muted', r[0])); dl.appendChild(h('span', r[2] || '', r[1])); });
    const bar = h('div', 'ctl');
    buttons.forEach(function (b) { bar.appendChild(h('button', b.cls || '', b.text)); bar.lastChild.type = 'button'; bar.lastChild.onclick = function () { if (b.fn) b.fn(); close(); }; });
    const no = h('button', '', 'Cancel'); no.type = 'button'; no.onclick = close; bar.appendChild(no);
    box.appendChild(h('section', 'notice info', h('h3', '', title), dl, extra || null, bar));
    box.hidden = false; box.scrollIntoView({ block: 'nearest' });
    function close() { box.hidden = true; box.textContent = ''; }
  }
  function adopt(next) { $('sngImport').hidden = true; song = next; cur = 0; pick = [0, 0]; pending = null; render(); saveDraft(); }

  /* ---- what the slides look like, and what is wrong with them ---- */
  function problems(sl) {
    const t = plain(sl.text), out = [];
    if (t.split('\n').length > MAX_LINES) out.push('Too many lines');
    if (t.replace(/\n/g, '').length > MAX_CHARS) out.push('Too many characters');
    return out;
  }
  function slideBox(sl) {
    const bad = problems(sl), tr = $('sngTr').checked && sl.translation;
    const box = h('div', 'sngsl' + (bad.length ? ' over' : ''), h('div', 'l', plain(sl.text)));
    if (tr) box.appendChild(h('div', 't', sl.translation));
    if (sl.hot_key) box.appendChild(h('span', 'hk', sl.hot_key));
    if (bad.length) box.appendChild(h('span', 'wn', bad[0]));
    box.appendChild(h('span', 'cc', String(plain(sl.text).replace(/\n/g, '').length)));
    return box;
  }
  function validate() {
    const out = [];
    if (!song.name.trim()) out.push('Add a title before sending.');
    const names = {};
    song.sections.forEach(function (s) {
      if (!s.name.trim()) out.push('A section has no name.');
      else if (names[s.name.toLowerCase()]) out.push('Two sections are called “' + s.name + '”.');
      names[s.name.toLowerCase()] = 1;
      if (!s.slides.some(function (x) { return plain(x.text).trim(); })) out.push('“' + (s.name || 'A section') + '” is empty. Fill it or delete it.');
    });
    if (!song.sections.length) out.push('Add a section before sending.');
    return out;
  }

  /* ---- render ---- */
  function render() {
    $('sngTitle').value = song.name; $('sngKey').value = song.music_key; $('sngCcli').value = song.ccli_number || '';
    renderGrid(); renderInspector(); renderArrangement(); renderState();
    $('sngSum').textContent = song.sections.length + ' sections · ' + slideCount(song) + ' slides';
  }
  function renderGrid() {
    const g = $('sngGrid'); g.textContent = '';
    song.sections.forEach(function (sec, si) {
      const grid = h('div', 'snggrid');
      sec.slides.forEach(function (sl, i) {
        const t = h('button', 'sngtile' + (pick[0] === si && pick[1] === i ? ' sel' : ''), slideBox(sl),
          h('div', 'm', h('span', '', String(i + 1)), h('span', '', sl.label || '')));
        t.type = 'button';
        t.onclick = function () { pick = [si, i]; renderGrid(); renderInspector(); };
        grid.appendChild(t);
      });
      const add = h('button', 'sngadd', '+ Slide'); add.type = 'button';
      add.onclick = function () { sec.slides.push(slide('')); pick = [si, sec.slides.length - 1]; render(); saveDraft(); };
      grid.appendChild(add);
      const name = h('input', 'sm'); name.value = sec.name; name.setAttribute('aria-label', 'Section name');
      name.onchange = function () { renameSection(si, name.value.trim()); };
      const del = relayUI.button('Delete', async function () {
        if (!(await relayUI.confirm('Delete “' + sec.name + '” and its ' + sec.slides.length + ' slide(s)? It also leaves every arrangement.'))) return;
        removeSection(si);
      }, 'sm quiet');
      g.appendChild(h('div', 'snggrp', h('div', 'snggh', name, h('span', 'badge muted', sec.slides.length + (sec.slides.length === 1 ? ' slide' : ' slides')), h('span', 'grow'), del), grid));
    });
  }
  function renameSection(si, to) {
    const from = song.sections[si].name;
    if (!to || to === from) { renderGrid(); return; }
    song.sections[si].name = to;
    song.arrangements.forEach(function (a) { a.sections = a.sections.map(function (n) { return n === from ? to : n; }); });
    render(); saveDraft();
  }
  function removeSection(si) {
    const gone = song.sections.splice(si, 1)[0].name;
    song.arrangements.forEach(function (a) { a.sections = a.sections.filter(function (n) { return n !== gone; }); });
    pick = [0, 0]; render(); saveDraft();
  }
  function renderInspector() {
    const box = $('sngInsp'); box.textContent = '';
    const sec = song.sections[pick[0]], sl = sec && sec.slides[pick[1]];
    if (!sl) { box.appendChild(h('p', 'note', 'Pick a slide to edit it.')); return; }
    const mk = function (key, el0, parse) { el0.value = sl[key] === 0 && key === 'advance_after' ? '' : (sl[key] || ''); el0.oninput = function () { sl[key] = parse ? parse(el0.value) : el0.value; renderGrid(); renderState(); saveDraft(); }; return el0; };
    const hot = h('select'); hot.appendChild(h('option', { value: '', text: 'None' }));
    '123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ0'.split('').forEach(function (k) { hot.appendChild(h('option', { value: k, text: k })); });
    const adv = h('input'); adv.type = 'number'; adv.min = '0'; adv.step = '0.5'; adv.placeholder = 'off';
    const lines = plain(sl.text).split('\n').length;
    box.appendChild(h('h3', '', 'Slide ' + (pick[1] + 1) + ' of ' + sec.name));
    box.appendChild(slideBox(sl));
    box.appendChild(relayUI.field('Lyric, chords as [G]', mk('text', h('textarea')), 'sngInText'));
    box.appendChild(relayUI.field('Translation line (optional)', mk('translation', h('textarea'))));
    box.appendChild(h('div', 'grid2', relayUI.field('Label', mk('label', h('input'))), relayUI.field('Hot key', mk('hot_key', hot)),
      relayUI.field('Auto-advance (s)', mk('advance_after', adv, function (v) { return Math.max(0, parseFloat(v) || 0); })),
      relayUI.field('Then', mk('completion', (function () { const s = h('select'); [['', 'Stop'], ['next', 'Next slide'], ['first', 'Loop'], ['random', 'Random']].forEach(function (o) { s.appendChild(h('option', { value: o[0], text: o[1] })); }); return s; })()))));
    box.appendChild(relayUI.field('Notes', mk('notes', h('input'))));
    box.appendChild(h('p', 'note', lines + ' line' + (lines === 1 ? '' : 's') + '. Warns above ' + MAX_LINES + ' lines or ' + MAX_CHARS + ' characters.'));
    box.appendChild(h('div', 'ctl',
      relayUI.button('Split here', function () { splitHere(); }, 'sm'),
      relayUI.button('Merge with next', function () { mergeNext(); }, 'sm'),
      relayUI.button('Delete slide', function () { sec.slides.splice(pick[1], 1); if (!sec.slides.length) sec.slides.push(slide('')); pick[1] = Math.min(pick[1], sec.slides.length - 1); render(); saveDraft(); }, 'sm danger')));
  }
  function splitHere() {
    const sec = song.sections[pick[0]], sl = sec.slides[pick[1]], ta = $('sngInText');
    const at = ta ? ta.selectionStart : sl.text.length;
    const a = slide(sl.text.slice(0, at).replace(/\n$/, '')), b = slide(sl.text.slice(at).replace(/^\n/, ''));
    a.label = sl.label; a.hot_key = sl.hot_key; a.translation = sl.translation;
    sec.slides.splice(pick[1], 1, a, b); render(); saveDraft();
  }
  function mergeNext() {
    const sec = song.sections[pick[0]], a = sec.slides[pick[1]], b = sec.slides[pick[1] + 1];
    if (!b) return;
    a.text += '\n' + b.text; a.translation = [a.translation, b.translation].filter(Boolean).join('\n');
    sec.slides.splice(pick[1] + 1, 1); render(); saveDraft();
  }

  /* ---- arrangements: reorder, repeat, name; the slide order is shown under the chips ---- */
  function renderArrangement() {
    const arr = song.arrangements, tabs = $('sngArr'); tabs.textContent = '';
    if (cur >= arr.length) cur = Math.max(0, arr.length - 1);
    arr.forEach(function (a, i) {
      const b = h('button', i === cur ? 'on' : '', a.name); b.type = 'button'; b.setAttribute('role', 'tab'); b.setAttribute('aria-selected', i === cur ? 'true' : 'false');
      b.onclick = function () { cur = i; pending = null; renderArrangement(); }; tabs.appendChild(b);
    });
    if (!arr.length) tabs.appendChild(h('span', 'note', 'No arrangement: ProPresenter plays the sections in order.'));
    const order = $('sngOrder'); order.textContent = '';
    const a = arr[cur];
    $('sngArrDel').disabled = !a;
    (a ? a.sections : song.sections.map(function (s) { return s.name; })).forEach(function (n, i) {
      const sec = secByName(n);
      const chip = h('span', 'sngchip', n);
      if (a) {
        chip.draggable = true;
        chip.ondragstart = function (e) { e.dataTransfer.setData('text/plain', String(i)); };
        chip.ondragover = function (e) { e.preventDefault(); chip.classList.add('drop'); };
        chip.ondragleave = function () { chip.classList.remove('drop'); };
        chip.ondrop = function (e) { e.preventDefault(); move(+e.dataTransfer.getData('text/plain'), i); };
        [['‹', 'Earlier', function () { move(i, i - 1); }], ['›', 'Later', function () { move(i, i + 1); }], ['×', 'Remove', function () { change(function () { a.sections.splice(i, 1); }); }]].forEach(function (x) {
          const b = h('button', '', x[0]); b.type = 'button'; b.title = x[1]; b.setAttribute('aria-label', x[1] + ' ' + n); b.onclick = x[2]; chip.appendChild(b);
        });
      }
      if (!sec) chip.classList.add('err');
      order.appendChild(chip);
    });
    const add = $('sngArrAdd'); add.textContent = '';
    song.sections.forEach(function (s) { add.appendChild(h('option', { value: s.name, text: s.name })); });
    $('sngArrAddBtn').disabled = !a; add.disabled = !a;
    const names = a ? a.sections : song.sections.map(function (s) { return s.name; });
    const total = names.reduce(function (n, x) { return n + (secByName(x) ? secByName(x).slides.length : 0); }, 0);
    $('sngSlideOrder').textContent = total + ' slides in this order: ' + names.map(function (x) { const s = secByName(x); return x + (s ? ' (' + s.slides.length + ')' : ''); }).join(' · ');
    const p = $('sngArrPend'); p.hidden = !pending; p.textContent = '';
    if (pending) {
      p.appendChild(h('span', '', 'Order changed: ' + pending.was + ' slides before, ' + total + ' now. '));
      p.appendChild(relayUI.button('Keep', function () { pending = null; saveDraft(); renderArrangement(); }, 'sm primary'));
      p.appendChild(relayUI.button('Undo', function () { song.arrangements[cur].sections = pending.before; pending = null; renderArrangement(); saveDraft(); }, 'sm'));
    }
  }
  function slidesIn(names) { return names.reduce(function (n, x) { return n + (secByName(x) ? secByName(x).slides.length : 0); }, 0); }
  function change(fn) {
    const a = song.arrangements[cur]; if (!a) return;
    if (!pending) pending = { before: a.sections.slice(), was: slidesIn(a.sections) };
    fn(); renderArrangement(); saveDraft();
  }
  function move(from, to) {
    const a = song.arrangements[cur];
    if (!a || to < 0 || to >= a.sections.length || from === to) return;
    change(function () { a.sections.splice(to, 0, a.sections.splice(from, 1)[0]); });
  }
  $('sngArrAddBtn').onclick = function () { const a = song.arrangements[cur]; if (a) change(function () { a.sections.push($('sngArrAdd').value); }); };
  $('sngArrNew').onclick = function () {
    const box = $('sngProposal'); const name = h('input'); name.placeholder = 'Sunday short';
    propose('New arrangement', [['Starts as', 'every section once, in order']], [{ text: 'Create', cls: 'primary', fn: function () {
      const n = name.value.trim() || 'Arrangement ' + (song.arrangements.length + 1);
      song.arrangements.push({ name: n, sections: song.sections.map(function (s) { return s.name; }) }); cur = song.arrangements.length - 1; pending = null; render(); saveDraft();
    } }], relayUI.field('Name', name));
    name.focus(); void box;
  };
  $('sngArrDel').onclick = async function () {
    const a = song.arrangements[cur]; if (!a || !(await relayUI.confirm('Delete the arrangement “' + a.name + '”?'))) return;
    song.arrangements.splice(cur, 1); pending = null; render(); saveDraft();
  };

  /* ---- getting a song in ---- */
  const imp = { blank_splits: true, headers: true, translation: true, chords: true };
  function renderImportOptions() {
    const box = $('sngImpOpts'); box.textContent = '';
    [['headers', 'Lines like “Verse 1” start a section', 'Off: the whole paste is one section.'],
     ['blank_splits', 'A blank line starts a new slide', 'Off: only the lines-per-slide setting cuts slides.'],
     ['translation', 'A line starting with / is the translation', 'Off: it is read as lyrics.'],
     ['chords', 'Read chords written above the words', 'They become [G] markers in the text.']].forEach(function (o) {
      const cb = h('input'); cb.type = 'checkbox'; cb.checked = imp[o[0]]; cb.onchange = function () { imp[o[0]] = cb.checked; };
      box.appendChild(h('div', 'lrow', h('span', 'grow', h('span', 'strong', o[1]), h('div', 'note', o[2])), h('label', 'switch', cb, h('span', 'slider'))));
    });
  }
  function readText(fromPdf) {
    const body = Object.assign({ lines: +$('sngLps').value }, imp);
    if (fromPdf) body.pdf_b64 = fromPdf; else body.text = $('sngText').value;
    return request('POST', BASE + '/parse', body).then(function (r) {
      if (r.text) { $('sngText').value = r.text; $('sngImport').hidden = false; }
      proposeImport(r);
    }, function (e) { relayUI.say('sngImpErr', e.message, true); $('sngImpErr').hidden = false; $('sngImport').hidden = false; });
  }
  function proposeImport(r) {
    $('sngImpErr').hidden = true;
    const n = r.sections.reduce(function (a, s) { return a + s.slides.length; }, 0);
    const long = r.sections.reduce(function (a, s) { return a + s.slides.filter(function (x) { return problems(x).length; }).length; }, 0);
    const rows = [['Found', r.sections.length + ' sections, ' + n + ' slides'], ['Sections', r.sections.map(function (s) { return s.name + ' (' + s.slides.length + ')'; }).join(', ')]];
    if (long) rows.push(['Check', long + ' slide(s) are long; you can fix them after', 'warn']);
    r.warnings.forEach(function (w) { rows.push(['Note', w]); });
    const fresh = function (keep) {
      const next = keep ? clone(song) : blank(); if (!keep) { next.name = song.name; next.music_key = song.music_key; next.ccli_number = song.ccli_number; next.extra = song.extra; next.sections = []; }
      const have = {}; next.sections.forEach(function (s) { have[s.name] = 1; });
      const names = {};
      r.sections.forEach(function (s) { let nm = s.name, k = 2; while (have[nm]) nm = s.name + ' ' + k++; names[s.name] = nm; have[nm] = 1;
        next.sections.push({ name: nm, slides: s.slides.map(function (x) { return Object.assign(slide(''), x); }) }); });
      const order = r.arrangement.map(function (x) { return names[x] || x; });
      if (order.length > r.sections.length) next.arrangements.push({ name: 'As pasted', sections: order });
      useTemplate(next, chosenTemplate());
      $('sngImport').hidden = true; adopt(next);
    };
    const empty = song.sections.length === 1 && !plain(song.sections[0].slides[0].text).trim() && song.sections[0].slides.length === 1;
    const btns = empty ? [{ text: 'Keep these', cls: 'primary', fn: function () { fresh(false); } }]
      : [{ text: 'Replace the song’s sections', cls: 'primary', fn: function () { fresh(false); } }, { text: 'Add after the existing ones', fn: function () { fresh(true); } }];
    propose('Use what was read?', rows, btns);
  }
  function fileB64(file) {
    return new Promise(function (ok, no) { const f = new FileReader(); f.onload = function () { ok(String(f.result).split(',')[1] || ''); }; f.onerror = no; f.readAsDataURL(file); });
  }
  $('sngPaste').onclick = function () { renderImportOptions(); $('sngImport').hidden = false; $('sngText').focus(); };
  $('sngImpCancel').onclick = function () { $('sngImport').hidden = true; };
  $('sngRead').onclick = function () { readText(null); };
  $('sngPdfBtn').onclick = function () { renderImportOptions(); $('sngPdf').click(); };
  $('sngPdf').onchange = async function () {
    const f = this.files[0]; this.value = ''; if (!f) return;
    try { await readText(await fileB64(f)); } catch (e) { relayUI.say('sngSendMsg', 'That file could not be read.', true); $('sngSendMsg').hidden = false; }
  };
  $('sngProBtn').onclick = function () { $('sngPro').click(); };
  $('sngPro').onchange = async function () {
    const f = this.files[0]; this.value = ''; if (!f) return;
    try {
      const r = await request('POST', BASE + '/read', { data_b64: await fileB64(f), inline: true });
      const extra = {};
      ['author', 'publisher', 'copyright_year', 'album', 'artist_credits', 'category', 'original_key', 'notes', 'show_ccli', 'bible_reference'].forEach(function (k) { if (r[k]) extra[k] = r[k]; });
      const next = { name: r.name || '', music_key: r.music_key || '', ccli_number: r.ccli_number || '', extra: extra,
        sections: r.sections.map(function (s) { return { name: s.name, slides: (s.slides.length ? s.slides : [{}]).map(function (x) { return Object.assign(slide(''), x); }) }; }),
        arrangements: (r.arrangements || []).map(function (a) { return { name: a.name, sections: a.sections }; }) };
      propose('Load this song into the builder?', [['Song', next.name || '(no title)'], ['CCLI', String(next.ccli_number || 'none')], ['Key', next.music_key || '—'],
        ['Sections', next.sections.map(function (s) { return s.name + ' (' + s.slides.length + ')'; }).join(', ')], ['Arrangements', next.arrangements.map(function (a) { return a.name; }).join(', ') || 'none'],
        ['Replaces', 'the song you are building now', 'warn']], [{ text: 'Load it', cls: 'primary', fn: function () { adopt(next); } }]);
    } catch (e) { relayUI.say('sngSendMsg', e.message, true); $('sngSendMsg').hidden = false; }
  };

  /* ---- auto-split ---- */
  $('sngSplit').onclick = async function () {
    const n = +$('sngLps').value;
    try {
      const r = await request('POST', BASE + '/split', { sections: song.sections, lines: n });
      const rows = [['Lines per slide', String(n)], ['Slides', slideCount(song) + ' now, ' + r.sections.reduce(function (a, s) { return a + s.slides.length; }, 0) + ' after']];
      const changed = r.sections.filter(function (s, i) { return s.slides.length !== song.sections[i].slides.length; });
      rows.push(['Changed', changed.length ? changed.map(function (s) { return s.name; }).join(', ') : 'nothing: every section already fits']);
      propose('Cut the sections into slides of ' + n + ' lines?', rows, changed.length ? [{ text: 'Split', cls: 'primary', fn: function () {
        song.sections = r.sections.map(function (s) { return { name: s.name, slides: s.slides.map(function (x) { return Object.assign(slide(''), x); }) }; }); pick = [0, 0]; pending = null; render(); saveDraft(); } }] : []);
    } catch (e) { relayUI.say('sngSendMsg', e.message, true); $('sngSendMsg').hidden = false; }
  };
  $('sngAddSec').onclick = function () { let k = song.sections.length + 1, nm = 'Section ' + k; while (secByName(nm)) nm = 'Section ' + (++k); song.sections.push(section(nm)); pick = [song.sections.length - 1, 0]; render(); saveDraft(); };

  /* ---- templates ---- */
  $('sngTpl').onchange = function () {
    const t = chosenTemplate(); if (!t) { saveDraft(); return; }
    const count = slideCount(song);
    propose('Apply the template “' + t.name + '” to this song?', [['Slides', count + ' will be changed'], ['Labels', t.labels ? 'each slide is named after its section' : 'unchanged'],
      ['Hot keys', t.hot_keys ? '1, 2, 3… on the first slide of each section' : 'unchanged'], ['Auto-advance', t.advance > 0 ? t.advance + ' s, then the next slide' : 'unchanged']],
      [{ text: 'Apply', cls: 'primary', fn: function () { useTemplate(song, t); render(); saveDraft(); } }, { text: 'Delete template', cls: 'danger', fn: function () {
        const all = templates(); all.splice(+$('sngTpl').value, 1); store(TPLS, all); fillTemplates(''); } }]);
  };
  $('sngSaveTpl').onclick = function () {
    const name = h('input'), lab = h('input'), hk = h('input'), adv = h('input');
    lab.type = hk.type = 'checkbox'; adv.type = 'number'; adv.min = '0'; adv.value = '0'; lab.checked = true;
    propose('Save a template', [], [{ text: 'Save', cls: 'primary', fn: function () {
      const t = { name: name.value.trim() || 'My template', labels: lab.checked, hot_keys: hk.checked, advance: Math.max(0, parseFloat(adv.value) || 0) };
      const all = templates().filter(function (x) { return x.name !== t.name; }); all.push(t); store(TPLS, all); fillTemplates(String(all.length - 1)); } }],
      h('div', '', relayUI.field('Name', name), h('label', 'check', lab, ' Label each slide with its section name'), h('label', 'check', hk, ' Hot keys 1, 2, 3… on each section’s first slide'), relayUI.field('Auto-advance seconds (0 is off)', adv)));
    name.focus();
  };

  /* ---- sending and the helper ---- */
  function renderState() {
    const bad = validate(), box = $('sngVal');
    box.textContent = bad.length ? bad[0] + (bad.length > 1 ? ' (' + (bad.length - 1) + ' more)' : '') : (helper.connected ? 'Ready to send' : 'Ready, but no helper is connected');
    box.className = bad.length ? 'err' : helper.connected ? 'ok' : 'warn';
    $('sngSend').disabled = !!bad.length || !helper.connected || !canSend;
    $('sngSend').title = !canSend ? 'You can read songs but not send them' : bad.length ? bad[0] : !helper.connected ? 'No helper is connected' : '';
    $('sngAccessNote').hidden = canSend;
    const hb = $('sngHelper'); hb.textContent = '';
    if (helper.connected) hb.appendChild(h('p', '', relayUI.pill('Helper connected' + (helper.helper ? ' · ' + helper.helper : ''), 'on')));
    else hb.appendChild(h('div', 'notice warn', h('p', '', h('b', '', 'No helper is connected. '), 'ProPresenter can only import a song by opening its file, and the Relay Helper does that on the ProPresenter computer. ' +
      (helper.paired ? 'A helper is paired but not running: start it there.' : 'Install the helper on that computer and pair it with this Relay (Settings, Helpers), then this page can send.'))));
  }
  async function pollHelper() {
    try { helper = await request('GET', BASE + '/helper'); } catch (e) { helper = { connected: false }; }
    renderState();
  }
  function payload() {
    const arr = song.arrangements[cur];
    return Object.assign({}, song.extra, { name: song.name.trim(), music_key: song.music_key.trim(), ccli_number: parseInt(song.ccli_number, 10) || 0,
      sections: song.sections.map(function (s) { return { name: s.name.trim(), slides: s.slides.map(function (x) { return { text: x.text, translation: x.translation, label: x.label, hot_key: x.hot_key,
        advance_after: x.advance_after || 0, completion: x.completion, notes: x.notes }; }) }; }),
      arrangements: song.arrangements.map(function (a) { return { name: a.name, sections: a.sections }; }), selected_arrangement: arr ? arr.name : '' });
  }
  $('sngSend').onclick = async function () {
    const msg = $('sngSendMsg'); msg.hidden = false; relayUI.say(msg, 'Sending…', false, { plain: true });
    try {
      const r = await request('POST', BASE + '/send', { songs: [payload()] });
      relayUI.say(msg, 'Sent “' + song.name.trim() + '” to the ProPresenter computer (' + r.opened + ' opened). ProPresenter adds it to the library.', false);
    } catch (e) { relayUI.say(msg, e.message, true); }
  };
  $('sngTitle').oninput = function () { song.name = this.value; renderState(); saveDraft(); };
  $('sngKey').oninput = function () { song.music_key = this.value; saveDraft(); };
  $('sngCcli').oninput = function () { song.ccli_number = this.value.replace(/\D/g, ''); saveDraft(); };
  $('sngTr').onchange = function () { renderGrid(); renderInspector(); saveDraft(); };

  /* ---- start ---- */
  [2, 3, 4, 5, 6, 8].forEach(function (n) { $('sngLps').appendChild(h('option', { value: String(n), text: String(n) })); });
  $('sngLps').value = '4';
  const saved = store(DRAFT);
  if (saved && saved.song && saved.song.sections) { song = saved.song; cur = saved.cur || 0; $('sngLps').value = saved.lps || '4'; $('sngTr').checked = !!saved.tr; relayUI.say('sngDraft', 'Draft restored from this browser', false, { plain: true }); }
  $('sngLps').onchange = saveDraft;
  fillTemplates(''); render(); pollHelper();
  setInterval(function () { if (!$('page-ppsongs').hidden) pollHelper(); }, 5000);
})();
