/* The Checklists tab of the crew page (#232): tick your position's items, and, for an operator or a team lead,
   write the templates. Everything from the server is put on the page as text. */
(function () {
  'use strict';
  const root = document.getElementById('cl');
  if (!root) return;
  const $ = function (id) { return document.getElementById(id); };
  const note = $('clNote'), box = $('clLists');
  let editing = null, timer = null;

  const request = relayUI.client({ cache: 'no-store', note: note, reload: [401] });
  function draw(d) {
    box.textContent = '';
    if (!d.lists.length) note.textContent = 'No checklists for you at this service.';
    d.lists.forEach(function (l) {
      const card = document.createElement('section'); card.className = 'cr-card';
      const h = document.createElement('h2'); h.textContent = l.title + (l.position ? ' · ' + l.position : ''); card.appendChild(h);
      const ul = document.createElement('ul'); ul.className = 'cl-items';
      l.items.forEach(function (i) {
        const li = document.createElement('li'), lab = document.createElement('label'), c = document.createElement('input');
        c.type = 'checkbox'; c.checked = !!i.done_ts;
        c.addEventListener('change', async function () {
          c.disabled = true;
          const r = await request('PUT', '/crew/api/checklists/item/' + i.id, { done: c.checked });
          if (r) draw(r); else { c.checked = !c.checked; c.disabled = false; }
        });
        lab.appendChild(c); lab.appendChild(document.createTextNode(' ' + i.text));
        if (i.done_ts) li.className = 'cr-done';
        li.appendChild(lab); ul.appendChild(li);
      });
      card.appendChild(ul); box.appendChild(card);
    });
    $('clEditBox').hidden = !d.editor;
    if (d.editor && !loadedTemplates) loadTemplates();
  }
  let loadedTemplates = false;
  async function load() { const d = await request('GET', '/crew/api/checklists'); if (d) draw(d); }

  function reset() { editing = null; $('clTitle').value = ''; $('clPos').value = ''; $('clItems').value = ''; $('clCancel').hidden = true; }
  function drawTemplates(d) {
    loadedTemplates = true;
    const out = $('clTemplates'); out.textContent = '';
    const dl = $('clPositions'); dl.textContent = '';
    (d.all ? d.positions : d.leads).forEach(function (p) { const o = document.createElement('option'); o.value = p; dl.appendChild(o); });
    d.templates.forEach(function (t) {
      const row = document.createElement('div'); row.className = 'cl-tpl';
      const name = document.createElement('span'); name.textContent = t.title + ' · ' + (t.position || 'Everyone') + ' (' + t.items.length + ')';
      const edit = document.createElement('button'); edit.type = 'button'; edit.textContent = 'Edit';
      edit.addEventListener('click', function () {
        editing = t.id; $('clTitle').value = t.title; $('clPos').value = t.position;
        $('clItems').value = t.items.map(function (i) { return i.text; }).join('\n'); $('clCancel').hidden = false;
      });
      const del = document.createElement('button'); del.type = 'button'; del.textContent = 'Delete';
      del.addEventListener('click', async function () {
        if (!(await relayUI.confirm('Delete the checklist “' + t.title + '”?'))) return;
        const r = await request('DELETE', '/crew/api/checklists/templates/' + t.id);
        if (r) { if (editing === t.id) reset(); drawTemplates(r); load(); }
      });
      row.appendChild(name); row.appendChild(edit); row.appendChild(del); out.appendChild(row);
    });
  }
  async function loadTemplates() { const d = await request('GET', '/crew/api/checklists/templates'); if (d) drawTemplates(d); }

  $('clSave').addEventListener('click', async function () {
    const body = { title: $('clTitle').value, position: $('clPos').value, items: $('clItems').value.split('\n') };
    const r = editing === null ? await request('POST', '/crew/api/checklists/templates', body)
      : await request('PUT', '/crew/api/checklists/templates/' + editing, body);
    if (r) { reset(); drawTemplates(r); load(); }
  });
  $('clCancel').addEventListener('click', reset);

  function active() { return relayUI.shown(root); }
  function wake() { clearInterval(timer); if (active()) { load(); timer = setInterval(load, 30000); } }
  document.addEventListener('crew:tab', wake);
  document.addEventListener('visibilitychange', wake);
  wake();
})();
