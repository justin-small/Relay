/* The "Watch the sources you rely on" card on the NDI page (#562): which sources, when, how long a fault must last, and
   what is wrong now. Drawn from the page's state on each update; the form is left alone while it is being edited. */
(function () {
  const N = window.RelayNdi;
  if (!N || !document.getElementById('ndiWatch')) return;
  const $ = function (id) { return document.getElementById(id); };
  const el = relayUI.el;
  let picked = null;                       // the sources ticked in the form, until it is saved or the page reloads its state
  function editing() { return $('ndiWatch').contains(document.activeElement) && document.activeElement !== document.body; }

  function draw() {
    const s = window.RelayNdiState && window.RelayNdiState();
    if (!s || !s.watch) return;
    const w = s.watch, manage = N.can('ndi.manage');
    const box = $('ndiWatchNow');
    box.textContent = w.active.map(function (a) { return a.text; }).join(' · ');
    box.hidden = !w.active.length;
    $('ndiWatchState').textContent = w.on ? (w.in_service ? 'Watching ' + w.relied.length + ' source' + (w.relied.length === 1 ? '' : 's') + ' now.' : 'Waiting for the schedule to open.') : '';
    if (editing()) return;
    $('ndiWatchOn').checked = w.on; $('ndiWatchAuto').checked = w.auto; $('ndiWatchSound').checked = w.sound;
    $('ndiWatchHold').value = w.hold;
    const sel = $('ndiWatchSched'); sel.textContent = '';
    sel.appendChild(el('option', { value: '' }, 'Always'));
    (s.schedules || []).forEach(function (x) { sel.appendChild(el('option', { value: x.ref, selected: x.ref === w.schedule }, x.name)); });
    if (w.schedule && !(s.schedules || []).some(function (x) { return x.ref === w.schedule; })) sel.appendChild(el('option', { value: w.schedule, selected: true }, 'A schedule that was deleted'));
    picked = new Set(w.sources);
    const pick = $('ndiWatchPick'); pick.textContent = '';
    const names = s.sources.map(function (x) { return x.name; });
    w.sources.forEach(function (n) { if (names.indexOf(n) < 0) names.push(n); });
    if (!names.length) pick.appendChild(el('p', 'note', 'No sources found yet.'));
    names.forEach(function (n) {
      const lab = el('label', 'check'), box = el('input', { type: 'checkbox', checked: picked.has(n), disabled: !manage });
      box.onchange = function () { if (box.checked) picked.add(n); else picked.delete(n); };
      lab.appendChild(box); lab.appendChild(document.createTextNode(' ' + n)); pick.appendChild(lab);
    });
    ['ndiWatchOn', 'ndiWatchAuto', 'ndiWatchSound', 'ndiWatchHold', 'ndiWatchSched', 'ndiWatchSave'].forEach(function (id) { $(id).disabled = !manage; });
  }
  $('ndiWatchSave').onclick = async function () {
    N.showError('');
    try {
      N.draw(await N.send('PUT', '/api/admin/ndi/settings', { watch: { on: $('ndiWatchOn').checked, auto: $('ndiWatchAuto').checked,
        sound: $('ndiWatchSound').checked, hold: +$('ndiWatchHold').value, schedule: $('ndiWatchSched').value, sources: Array.from(picked || []) } }));
    } catch (e) { N.showError(e.message); }
  };
  window.addEventListener('relay-ndi-state', draw);
  draw();
})();
