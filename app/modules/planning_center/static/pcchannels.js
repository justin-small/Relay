/* The Team and mics tab of the Planning Center admin page: the soundboard channels on the board, each assigned to
   positions of any service type. The positions and who is on each channel come from the rundown poll in pcrundown.js
   (the 'pc:rundown' event); the channel list a soundboard module offers is data.catalog. Loaded by admin.html only
   while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('pcChBox')) return;
  const $ = R.$, el = R.el, post = R.post;
  const can = relayUI.can;
  const say = relayUI.say;

  let data = null;        // the last rundown poll
  let board = null;       // the channels being edited; null while nothing is unsaved
  let listSig = '';

  function saved() { return (data && data.settings.channels) || []; }
  function edit() {
    if (board === null) board = JSON.parse(JSON.stringify(saved()));
    return board;
  }
  function current() { return board || saved(); }
  function dirty(on) { $('pcChSave').disabled = !on; }

  // The short label /screen shows for a channel; the same rule as rundown.channel_label on the server.
  function labelOf(ch) {
    const texts = [ch.number, ch.name];
    for (let i = 0; i < texts.length; i++) {
      const t = (texts[i] || '').replace(/[^\w .#/+-]/g, '').trim().slice(0, 12).trim();
      if (t) return t;
    }
    return '';
  }
  function typeName(id) {
    const t = ((data && data.service_types) || []).filter(function (x) { return x.id === id; })[0];
    return t ? t.name : 'Service type ' + id;
  }
  function holder(ch) {
    const label = labelOf(ch);
    const hit = ((data && data.rundown.mics) || []).filter(function (m) { return m.mic === label; })[0];
    return hit;
  }

  function addChannel(number, name, id, ref) {
    if (!number && !name) return;
    edit().push({ id: id || 'c' + Date.now().toString(36), number: number, name: name, channel_ref: ref || '', positions: [] });
    dirty(true);
    render();
  }

  // Choosing from the console uses the shared channel picker (#546): core's audio sources, named as on every page.
  let pick = null;
  function renderSource() {
    if (pick || !$('pcChPick')) return;
    pick = relayUI.channelPicker($('pcChPick'), { label: 'Add an input channel', empty: 'Add an input channel', kind: 'audio', direction: 'source',
      onChange: function (ref, item) {
        if (!ref || !item) return;
        pick.set(null);
        if (current().some(function (c) { return c.channel_ref === ref; })) { say('pcChMsg', item.title + ' is already on the board.', true); return; }
        say('pcChMsg', '');
        addChannel(item.number === null || item.number === undefined ? '' : String(item.number), item.title, 'c' + Date.now().toString(36), ref);
      } });
  }

  function chip(ch, a, i) {
    const c = el('span', 'pcchip');
    c.appendChild(el('i', '', typeName(a.service_type) + ' ·'));
    c.appendChild(document.createTextNode(' ' + a.position + (a.missing ? ' (no longer in Planning Center)' : '') + (a.unmatched ? ' (not matched to Planning Center)' : '')));
    if (can('planning_center.manage')) {
      const x = el('button', '', '×'); x.type = 'button';
      x.setAttribute('aria-label', 'Remove ' + a.position + ' from this input channel');
      x.addEventListener('click', function () {
        edit().filter(function (b) { return b.id === ch.id; })[0].positions.splice(i, 1);
        dirty(true); render();
      });
      c.appendChild(x);
    }
    return c;
  }

  function assignSelect(ch) {
    const sel = document.createElement('select');
    sel.setAttribute('aria-label', 'Assign ' + (ch.name || ch.number) + ' to a position');
    const first = el('option', '', 'Assign to a position…'); first.value = '';
    sel.appendChild(first);
    // A position is assigned by Planning Center's ids when it has them, so renaming it there changes nothing here;
    // a saved one is the same position when the refs match, else when the names do.
    function same(a, p) {
      return a.service_type === p.service_type &&
        (a.position_ref && p.position_ref ? a.position_ref === p.position_ref : a.team === p.team && a.position === p.position);
    }
    const choices = (data && data.type_positions) || [];
    const groups = {};
    choices.forEach(function (p, i) {
      if (ch.positions.some(function (a) { return same(a, p); })) return;
      const g = groups[p.service_type] || (groups[p.service_type] = el('optgroup'));
      g.label = typeName(p.service_type);
      const o = el('option', '', (p.team ? p.team + ' · ' : '') + p.position);
      o.value = String(i);
      g.appendChild(o);
    });
    Object.keys(groups).forEach(function (k) { sel.appendChild(groups[k]); });
    sel.addEventListener('change', function () {
      if (!sel.value) return;
      const p = choices[Number(sel.value)];
      edit().filter(function (b) { return b.id === ch.id; })[0].positions.push(
        { service_type: p.service_type, team: p.team, team_ref: p.team_ref, position: p.position, position_ref: p.position_ref });
      dirty(true); render();
    });
    return sel;
  }

  function row(ch) {
    const manage = can('planning_center.manage');
    const r = el('div', 'pcchan');
    const nm = el('div', 'nm');
    if (ch.number) nm.appendChild(el('span', 'num', ch.number));
    if (ch.name) nm.appendChild(el('span', 'name', ch.name));
    if (ch.channel_missing) nm.appendChild(el('span', 'note', ' (its channel is no longer reported)'));
    else if (ch.name_only) nm.appendChild(el('span', 'note', ' (name only: not tied to a Relay input channel)'));
    r.appendChild(nm);
    const asg = el('div', 'asg');
    ch.positions.forEach(function (a, i) { asg.appendChild(chip(ch, a, i)); });
    if (!ch.positions.length && !manage) asg.appendChild(el('span', 'muted', 'No positions yet'));
    if (manage) asg.appendChild(assignSelect(ch));
    r.appendChild(asg);
    const now = el('div', 'now');
    const h = holder(ch);
    if (!ch.positions.length) now.textContent = 'Not assigned';
    else if (h && h.person) { now.appendChild(document.createTextNode('Now: ')); now.appendChild(el('b', '', h.person.name)); }
    else if (h) { now.appendChild(document.createTextNode('Now: ')); now.appendChild(el('span', 'spare', 'nobody (spare)')); }
    else now.textContent = 'Not used in this service';
    r.appendChild(now);
    const act = el('div');
    if (manage) {
      const rm = el('button', 'sm quiet', 'Remove'); rm.type = 'button';
      rm.addEventListener('click', function () {
        const list = edit(); list.splice(list.map(function (b) { return b.id; }).indexOf(ch.id), 1);
        dirty(true); render();
      });
      act.appendChild(rm);
    }
    r.appendChild(act);
    return r;
  }

  // Mics kept by position from before channels existed: each mic becomes a channel on the board, assigned to that
  // position for every service type Relay follows.
  function legacy() { return (data && data.settings.mics) || []; }
  function renderLegacy() {
    const old = legacy();
    $('pcChLegacy').hidden = !old.length || !can('planning_center.manage');
    $('pcChLegacyText').textContent = old.map(function (m) { return (m.position || '') + ' ' + m.mics.join(', '); }).join(' · ');
  }
  $('pcChMove').addEventListener('click', async function () {
    const types = ((data && data.service_types) || []).map(function (t) { return t.id; });
    const list = edit();
    legacy().forEach(function (m) {
      m.mics.forEach(function (label) {
        let ch = list.filter(function (c) { return c.number === label && !c.name; })[0];
        if (!ch) { ch = { id: 'c' + Date.now().toString(36) + list.length, number: label, name: '', positions: [] }; list.push(ch); }
        types.forEach(function (st) {
          if (!ch.positions.some(function (a) { return a.service_type === st && (m.position_ref && a.position_ref ? a.position_ref === m.position_ref : a.team === m.team && a.position === m.position); })) {
            ch.positions.push({ service_type: st, team: m.team, team_ref: m.team_ref || '', position: m.position, position_ref: m.position_ref || '' });
          }
        });
      });
    });
    try {
      const res = await post('/api/admin/planning-center/rundown/settings', { channels: list, mics: [] });
      data.settings = res.settings; board = null; listSig = '';
      dirty(false); render();
      say('pcChMsg', 'Moved to the board.');
    } catch (e) { say('pcChMsg', e.message, true); }
  });

  function render() {
    if (!data) return;
    renderLegacy();
    const manage = can('planning_center.manage'), control = can('planning_center.control');
    renderSource();
    $('pcChPickBox').hidden = !manage;
    $('pcChManual').hidden = !manage;
    $('pcChSave').hidden = !manage;
    $('pcChShuffle').hidden = !control;
    const sig = JSON.stringify([current(), data.type_positions, data.rundown.mics, data.service_types]);
    if (sig === listSig) return;       // rebuilt only on a change, so an open dropdown is not pulled away
    listSig = sig;
    const list = $('pcChList');
    list.innerHTML = '';
    current().forEach(function (ch) { list.appendChild(row(ch)); });
    if (!current().length) list.appendChild(el('p', 'note', 'No input channels on the board yet. Add one above.'));
  }

  $('pcChAddManual').addEventListener('click', function () {
    const number = $('pcChNum').value.trim(), name = $('pcChName').value.trim();
    if (!number && !name) { say('pcChMsg', 'Give the input channel a number or a name.', true); return; }
    say('pcChMsg', '');
    addChannel(number, name);
    $('pcChNum').value = ''; $('pcChName').value = '';
  });
  $('pcChSave').addEventListener('click', async function () {
    try {
      const res = await post('/api/admin/planning-center/rundown/settings', { channels: edit() });
      data.settings = res.settings; board = null; listSig = '';
      dirty(false); render();
      say('pcChMsg', 'Saved.');
    } catch (e) { say('pcChMsg', e.message, true); }
  });
  $('pcChShuffle').addEventListener('click', async function () {
    try {
      const res = await post('/api/admin/planning-center/rundown/redraw',
        { type: $('rdType').value || '', time: $('rdTime').value || '', team: '', position: '' });
      data.rundown = res.rundown; listSig = '';
      render();
      say('pcChMsg', 'Drawn again.');
    } catch (e) { say('pcChMsg', e.message, true); }
  });

  document.addEventListener('pc:rundown', function (e) {
    data = e.detail;
    render();
  });
})();
