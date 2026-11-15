/* The Resources tab of the crew page (#233): open the files, links and embedded pages for your position, and, for an
   operator or a team lead, add them. Everything from the server is put on the page as text; an address is used only
   when it starts with http:// or https://. */
(function () {
  'use strict';
  const root = document.getElementById('rs');
  if (!root) return;
  const $ = function (id) { return document.getElementById(id); };
  const note = $('rsNote');
  let editing = null, loaded = false, hosts = [], limit = 0;

  async function api(method, path, body) {
    const res = await fetch(path, { method: method, credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    if (res.status === 401) { location.reload(); return null; }
    const data = await res.json().catch(function () { return {}; });
    if (!res.ok) { note.textContent = data.detail || 'That did not work.'; return null; }
    note.textContent = '';
    return data;
  }
  function safe(url) { return typeof url === 'string' && /^https?:\/\//i.test(url) ? url : ''; }
  function mb(n) { return n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB'; }

  function item(r) {
    const li = document.createElement('li'); li.className = 'rs-item';
    const a = document.createElement('a'); a.textContent = r.title;
    if (r.kind === 'file') { a.href = '/crew/api/resources/' + r.id + '/file'; if (r.inline) a.target = '_blank'; a.rel = 'noopener'; }
    else { a.href = safe(r.url) || '#'; a.target = '_blank'; a.rel = 'noopener noreferrer'; }
    li.appendChild(a);
    const meta = document.createElement('span'); meta.className = 'cr-note';
    meta.textContent = r.kind === 'file' ? ' ' + r.ext.toUpperCase() + ' \u00b7 ' + mb(r.size) : (r.kind === 'embed' ? ' page' : ' link');
    li.appendChild(meta);
    if (r.kind === 'embed' && safe(r.url)) {
      const b = document.createElement('button'); b.type = 'button'; b.textContent = 'Show here';
      let frame = null;
      b.addEventListener('click', function () {
        if (frame) { frame.remove(); frame = null; b.textContent = 'Show here'; return; }
        frame = document.createElement('iframe'); frame.className = 'rs-frame'; frame.loading = 'lazy';
        frame.referrerPolicy = 'no-referrer'; frame.title = r.title;
        frame.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-popups allow-presentation');
        frame.src = r.url; li.appendChild(frame); b.textContent = 'Hide';
      });
      li.appendChild(b);
    }
    return li;
  }
  function draw(d) {
    const box = $('rsLists'); box.textContent = '';
    if (!d.resources.length) note.textContent = 'No resources for you yet.';
    const groups = {};
    d.resources.forEach(function (r) { (groups[r.position || ''] = groups[r.position || ''] || []).push(r); });
    Object.keys(groups).forEach(function (pos) {
      const card = document.createElement('section'); card.className = 'cr-card';
      const h = document.createElement('h2'); h.textContent = pos || 'Everyone'; card.appendChild(h);
      const ul = document.createElement('ul'); ul.className = 'cl-items';
      groups[pos].forEach(function (r) { ul.appendChild(item(r)); });
      card.appendChild(ul); box.appendChild(card);
    });
    $('rsEditBox').hidden = !d.editor;
    if (d.editor && !loaded) loadManage();
  }
  async function load() { const d = await api('GET', '/crew/api/resources'); if (d) draw(d); }

  function kindChanged() {
    const k = $('rsKind').value, file = k === 'file';
    $('rsUrl').hidden = file; $('rsUrlLbl').hidden = file; $('rsFile').hidden = !file;
    $('rsHint').textContent = file ? 'PDF, picture, text, Word, Excel or PowerPoint, up to ' + (limit ? mb(limit) : '25 MB') + '.'
      : k === 'embed' ? 'Pages from these sites can be shown here: ' + (hosts.join(', ') || 'none allowed') + '. YouTube watch addresses work.'
      : 'Opens in a new tab.';
  }
  function reset() {
    editing = null; $('rsTitle').value = ''; $('rsPos').value = ''; $('rsUrl').value = ''; $('rsFile').value = '';
    $('rsKind').disabled = false; $('rsSave').textContent = 'Add'; $('rsCancel').hidden = true; kindChanged();
  }
  function drawManage(d) {
    loaded = true; hosts = d.embed_hosts; limit = d.limit;
    const out = $('rsManage'); out.textContent = '';
    const dl = $('rsPositions'); dl.textContent = '';
    (d.all ? d.positions : d.leads).forEach(function (p) { const o = document.createElement('option'); o.value = p; dl.appendChild(o); });
    d.resources.forEach(function (r) {
      const row = document.createElement('div'); row.className = 'cl-tpl';
      const name = document.createElement('span'); name.textContent = r.title + ' \u00b7 ' + (r.position || 'Everyone') + ' \u00b7 ' + r.kind;
      const edit = document.createElement('button'); edit.type = 'button'; edit.textContent = 'Edit';
      edit.addEventListener('click', function () {
        editing = r.id; $('rsKind').value = r.kind; $('rsKind').disabled = true; kindChanged();
        $('rsTitle').value = r.title; $('rsPos').value = r.position; $('rsUrl').value = r.url || '';
        $('rsFile').hidden = true; $('rsSave').textContent = 'Save'; $('rsCancel').hidden = false;
      });
      const del = document.createElement('button'); del.type = 'button'; del.textContent = 'Delete';
      del.addEventListener('click', async function () {
        if (!confirm('Delete \u201c' + r.title + '\u201d?')) return;
        const x = await api('DELETE', '/crew/api/resources/' + r.id);
        if (x) { if (editing === r.id) reset(); drawManage(x); load(); }
      });
      row.appendChild(name); row.appendChild(edit); row.appendChild(del); out.appendChild(row);
    });
    kindChanged();
  }
  async function loadManage() { const d = await api('GET', '/crew/api/resources/manage'); if (d) drawManage(d); }

  $('rsKind').addEventListener('change', kindChanged);
  $('rsSave').addEventListener('click', async function () {
    const kind = $('rsKind').value, title = $('rsTitle').value, position = $('rsPos').value;
    let r;
    if (editing !== null) r = await api('PUT', '/crew/api/resources/' + editing, { title: title, position: position, url: $('rsUrl').value });
    else if (kind === 'file') {
      const f = $('rsFile').files[0];
      if (!f) { note.textContent = 'Choose a file first.'; return; }
      if (limit && f.size > limit) { note.textContent = 'That file is too large. Files can be up to ' + mb(limit) + '.'; return; }
      $('rsSave').disabled = true; note.textContent = 'Uploading\u2026';
      const res = await fetch('/crew/api/resources/file?title=' + encodeURIComponent(title) + '&position=' + encodeURIComponent(position),
        { method: 'POST', credentials: 'same-origin', body: f });
      $('rsSave').disabled = false;
      const data = await res.json().catch(function () { return {}; });
      if (!res.ok) { note.textContent = data.detail || 'The upload did not work.'; return; }
      note.textContent = ''; r = data;
    } else r = await api('POST', '/crew/api/resources/link', { kind: kind, title: title, position: position, url: $('rsUrl').value });
    if (r) { reset(); drawManage(r); load(); }
  });
  $('rsCancel').addEventListener('click', reset);
  reset();
  function active() { return !root.closest('[role=tabpanel]').hidden && document.visibilityState === 'visible'; }
  function wake() { if (active()) load(); }
  document.addEventListener('crew:tab', wake);
  document.addEventListener('visibilitychange', wake);
  wake();
})();
