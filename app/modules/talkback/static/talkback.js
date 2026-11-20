/* The Talkback admin page (#93): choose the lane, backend and language, run the session, and read what it heard.
   Loaded by admin.html only while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-talkback')) return;
  const $ = R.$;
  const can = relayUI.can;
  const el = relayUI.el;
  const send = relayUI.api;
  function showError(text) { $('tbErr').textContent = text || ''; $('tbErr').hidden = !text; }

  function minutes(s) { return (s / 60).toFixed(1) + ' min'; }
  function billed(b) { return minutes(b.audio_s) + (b.cost_usd == null ? '' : ' ($' + b.cost_usd.toFixed(2) + ')'); }
  function fill(select, rows, value) {
    select.replaceChildren.apply(select, rows.map(function (r) { return el('option', { value: r[0], text: r[1] }); }));
    select.value = value;
  }

  let editing = false;
  function draw(s) {
    const st = s.status, me = s.settings;
    if (!editing) {
      const lanes = s.lanes.map(function (l) { return [String(l.lane), 'Lane ' + l.lane + (l.name ? ': ' + l.name : '')]; });
      if (me.lane && !s.lanes.some(function (l) { return l.lane === me.lane; })) lanes.push([String(me.lane), 'Lane ' + me.lane]);
      fill($('tbLane'), [['0', 'Choose a lane']].concat(lanes), String(me.lane));
      fill($('tbBackend'), [['', 'Same as the captions']].concat(s.backends.map(function (b) {
        return [b.id, b.label + (b.available ? '' : ' (no key set)')];
      })), me.backend);
      fill($('tbLanguage'), [['', 'Same as the captions']].concat(s.languages.map(function (l) { return [l.id, l.label]; })),
        me.source_language);
    }
    $('tbSum').textContent = st.error ? 'problem' : (st.running ? 'listening' : 'stopped');
    $('tbStatus').textContent = st.error || (st.running
      ? 'Listening on lane ' + st.lane + ' through ' + (st.backend ? st.backend.label : '') + '.'
        + (st.backend && st.backend.warning ? ' ' + st.backend.warning : '')
      : 'Stopped.');
    $('tbBusNote').textContent = s.bus_running ? '' : 'The audio bus is off. Switch it on and patch the talkback mic to a lane.';
    $('tbBilled').textContent = 'Talkback has streamed ' + billed(st.billed) + '. The caption feed has streamed '
      + billed(st.main_billed) + '.';
    $('tbPartial').textContent = st.partial || '';
    $('tbLines').replaceChildren.apply($('tbLines'), s.lines.slice().reverse().map(function (l) {
      return el('li', { text: new Date(l.ts * 1000).toLocaleTimeString() + '  ' + l.text });
    }));
    $('tbStart').disabled = !can('talkback.control') || st.running;
    $('tbStop').disabled = !can('talkback.control') || !st.running;
    $('tbRestart').disabled = !can('talkback.control') || !st.running;
    ['tbLane', 'tbBackend', 'tbLanguage', 'tbSave'].forEach(function (id) { $(id).disabled = !can('talkback.manage'); });
  }

  let last = '';
  async function refresh() {
    try {
      const s = await send('GET', '/api/admin/talkback');
      const sig = JSON.stringify(s);
      if (sig !== last) { last = sig; draw(s); }
    } catch (e) { /* the page keeps what it shows */ }
  }
  ['tbLane', 'tbBackend', 'tbLanguage'].forEach(function (id) { $(id).onchange = function () { editing = true; }; });
  $('tbSave').onclick = async function () {
    showError('');
    try {
      editing = false;
      const s = await send('PUT', '/api/admin/talkback/settings', {
        lane: +$('tbLane').value, backend: $('tbBackend').value, source_language: $('tbLanguage').value });
      last = JSON.stringify(s); draw(s);
    } catch (e) { showError(e.message); }
  };
  ['start', 'stop', 'restart'].forEach(function (action) {
    $('tb' + action[0].toUpperCase() + action.slice(1)).onclick = async function () {
      showError('');
      try {
        const s = await send('POST', '/api/admin/talkback/' + action);
        last = JSON.stringify(s); draw(s);
      } catch (e) { showError(e.message); }
    };
  });
  refresh();
  setInterval(function () { if (!document.hidden && !document.getElementById('page-talkback').hidden) refresh(); }, 2000);
})();
