/* The Audio Mixers admin page (#150): each desk live (strips, scene, sends into each mix), the mic-name push from
   Planning Center, the watchdog and the desk setup. Loaded by admin.html only while the module is on. Strip rows are
   built once per desk and then updated in place, so a name being typed or a fader being dragged is not redrawn under
   the operator. Every value from the server is set as text. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-audio_mixers')) return;
  const $ = R.$;
  const el = relayUI.el;
  const can = relayUI.can;
  const say = relayUI.note($('amErr'));
  const button = relayUI.buttons(say);
  const api = relayUI.api;
  const KIND = { in: 'Inputs', mix: 'Mixes', main: 'Main', dca: 'DCAs' };

  let data = null;
  let draft = null;          // the desks being edited, until Save
  let draftDirty = false;
  let watchDirty = false;
  let people = null;
  const views = {};          // desk id -> {root, rows: {key: {...}}, sig, tab}
  const touched = {};        // "desk:key" -> when the operator last touched its fader

  // One shared fader law with the boards (models.py): position 0..1 <-> dB.
  function posToDb(p) {
    p = Math.max(0, Math.min(1, p));
    if (p <= 0) return -90;
    const db = p >= 0.5 ? p * 40 - 30 : p >= 0.25 ? p * 80 - 50 : p >= 0.0625 ? p * 160 - 70 : p * 480 - 90;
    return db <= -90 ? -90 : Math.round(db * 10) / 10;
  }
  function dbToPos(db) {
    if (db === null || db === undefined || db <= -90) return 0;
    const d = Math.min(10, db);
    const p = d >= -10 ? (d + 30) / 40 : d >= -30 ? (d + 50) / 80 : d >= -60 ? (d + 70) / 160 : (d + 90) / 480;
    return Math.max(0, Math.min(1, p));
  }
  function dbText(db) {
    if (db === null || db === undefined) return '?';
    return db <= -90 ? '-∞' : (db > 0 ? '+' : '') + db.toFixed(1);
  }
  function control(desk, body) {
    return api('POST', '/api/admin/audio_mixers/' + encodeURIComponent(desk.id) + '/control', body)
      .catch(function (e) { say(e.message); });
  }

  // ------------------------------------------------------------------------------------------ one desk, live
  function deskView(d) {
    let v = views[d.id];
    const sig = d.model + '|' + d.can_name + '|' + d.colours + '|' + can('audio_mixers.control');
    if (v && v.sig === sig) return v;
    const root = el('section', 'amdesk');
    v = views[d.id] = { root: root, rows: {}, sig: sig, tab: (v && v.tab) || 'in' };
    const head = el('div', 'amhead');
    v.title = el('h3', '', d.name);
    v.pills = el('span', 'ampills');
    head.appendChild(v.title); head.appendChild(v.pills);
    root.appendChild(head);
    v.error = el('p', 'note err'); v.error.hidden = true; root.appendChild(v.error);
    v.diffs = el('div', 'notice'); v.diffs.hidden = true; root.appendChild(v.diffs);

    const scene = el('div', 'amscene');
    v.sceneText = el('span', 'amscenetext');
    scene.appendChild(v.sceneText);
    if (can('audio_mixers.control')) {
      const n = el('input', { type: 'number', min: '1', className: 'sm', ariaLabel: 'Scene number' });
      n.max = String(d.scenes);
      scene.appendChild(n);
      scene.appendChild(button('Recall scene', async function () {
        const s = parseInt(n.value, 10);
        if (!s) throw new Error('Type the scene number to recall.');
        if (!(await relayUI.confirm('Recall scene ' + s + ' on ' + d.name + '? The desk changes at once.'))) return;
        await api('POST', '/api/admin/audio_mixers/' + encodeURIComponent(d.id) + '/control', { action: 'scene', value: s });
      }, 'sm'));
    }
    root.appendChild(scene);

    const tabs = el('div', 'tabs');
    v.tabs = {};
    Object.keys(KIND).forEach(function (k) {
      const t = el('button', { type: 'button', text: KIND[k] });
      t.onclick = function () { v.tab = k; showTab(v); };
      v.tabs[k] = t; tabs.appendChild(t);
    });
    root.appendChild(tabs);
    v.table = el('div', 'amstrips'); root.appendChild(v.table);
    d.strips.forEach(function (s) { v.table.appendChild(stripRow(d, v, s)); });

    v.routes = el('details', 'sect amroutes');
    v.routes.appendChild(el('summary', '', 'Sends into each mix'));
    v.routeHost = el('div'); v.routes.appendChild(v.routeHost);
    v.routes.ontoggle = function () {
      if (!v.routes.open || v.editor || !d.gear_id) return;
      v.editor = relayUI.routeEditor(v.routeHost, { kind: 'audio', provider: 'audio_mixers', gear: d.gear_id,
        perm: 'audio_mixers.control', empty: 'Connect the desk to see its mixes.' });
    };
    root.appendChild(v.routes);
    showTab(v);
    return v;
  }
  function showTab(v) {
    Object.keys(v.tabs).forEach(function (k) { v.tabs[k].setAttribute('aria-selected', String(k === v.tab)); });
    Object.keys(v.rows).forEach(function (key) { v.rows[key].row.hidden = key.replace(/\d+$/, '') !== v.tab; });
  }
  function stripRow(d, v, s) {
    const key = s.key, id = d.id + ':' + key;
    const r = { row: el('div', 'amstrip') };
    r.num = el('span', 'amnum', key.replace(/^\D+/, ''));
    r.row.appendChild(r.num);
    const ctl = can('audio_mixers.control');
    if (ctl && d.can_name) {
      r.name = el('input', { type: 'text', className: 'sm', maxLength: d.name_len, ariaLabel: 'Name of ' + key });
      r.name.onchange = function () { control(d, { action: 'name', strip: key, value: r.name.value }); };
    } else {
      r.name = el('span', 'amname');
    }
    r.row.appendChild(r.name);
    if (ctl && d.colours) {
      r.colour = el('select', { className: 'sm', ariaLabel: 'Colour of ' + key });
      (data.colours || []).forEach(function (c) { r.colour.appendChild(el('option', { value: c, text: c })); });
      r.colour.onchange = function () { control(d, { action: 'colour', strip: key, value: r.colour.value }); };
      r.row.appendChild(r.colour);
    } else {
      r.colour = el('span', 'amcolour'); r.row.appendChild(r.colour);
    }
    r.mute = el('button', { type: 'button', className: 'sm ammute', text: 'Mute' });
    r.mute.disabled = !ctl;
    r.mute.onclick = function () { control(d, { action: 'mute', strip: key, value: r.mute.dataset.on !== '1' }); };
    r.row.appendChild(r.mute);
    r.fader = el('input', { type: 'range', min: '0', max: '1000', className: 'amfader', ariaLabel: 'Fader of ' + key });
    r.fader.disabled = !ctl;
    r.fader.oninput = function () { touched[id] = Date.now(); r.db.textContent = dbText(posToDb(+r.fader.value / 1000)); };
    r.fader.onchange = function () { touched[id] = Date.now(); control(d, { action: 'level', strip: key, value: posToDb(+r.fader.value / 1000) }); };
    r.row.appendChild(r.fader);
    r.db = el('span', 'amdb'); r.row.appendChild(r.db);
    r.meter = el('span', 'ammeter'); r.meterBar = el('i'); r.meter.appendChild(r.meterBar);
    r.meter.hidden = !d.meters; r.row.appendChild(r.meter);
    v.rows[key] = r;
    return r.row;
  }
  function paintDesk(d) {
    const v = deskView(d);
    v.title.textContent = d.name;
    v.pills.textContent = '';
    v.pills.appendChild(relayUI.pill(d.connected ? 'Connected' : 'Not connected', d.connected ? 'on' : 'err'));
    v.pills.appendChild(el('span', 'badge', d.label));
    if (!d.tested) v.pills.appendChild(el('span', 'badge', 'Untested'));
    if (d.ahnet !== 'off') v.pills.appendChild(el('span', 'badge', 'AH-Net ' + d.ahnet));
    v.error.textContent = d.connected ? '' : (d.error || (d.host ? 'Connecting…' : 'Set the desk’s address below.'));
    v.error.hidden = !v.error.textContent;
    v.diffs.textContent = '';
    (d.differences || []).forEach(function (x) { v.diffs.appendChild(el('div', '', x.text)); });
    v.diffs.hidden = !(d.differences || []).length;
    v.sceneText.textContent = d.scene ? 'Scene ' + d.scene + (d.scene_name ? ': ' + d.scene_name : '') : 'Scene not known yet';
    d.strips.forEach(function (s) {
      const r = v.rows[s.key];
      if (!r) return;
      if (r.name.tagName === 'INPUT') {
        if (document.activeElement !== r.name) r.name.value = s.name || '';
        r.name.placeholder = s.shown;
      } else {
        r.name.textContent = s.shown;
      }
      if (r.colour.tagName === 'SELECT') { if (document.activeElement !== r.colour) r.colour.value = s.colour || 'off'; }
      else r.colour.textContent = s.colour || '';
      r.mute.dataset.on = s.muted === true ? '1' : (s.muted === false ? '0' : '');
      r.mute.textContent = s.muted === true ? 'Muted' : (s.muted === false ? 'Mute' : 'Mute ?');
      r.mute.classList.toggle('danger', s.muted === true);
      if (Date.now() - (touched[d.id + ':' + s.key] || 0) > 2500) {
        r.fader.value = String(Math.round(dbToPos(s.db) * 1000));
        r.db.textContent = dbText(s.db);
      }
      if (d.meters) {
        const m = s.meter === null || s.meter === undefined ? 0 : Math.max(0, Math.min(1, (s.meter + 60) / 60));
        r.meterBar.style.width = Math.round(m * 100) + '%';
        r.meter.title = s.meter === null || s.meter === undefined ? 'No signal' : s.meter.toFixed(0) + ' dBFS';
      }
    });
    if (v.editor && v.routes.open) v.editor.reload();
    return v.root;
  }

  // ------------------------------------------------------------------------------------------ mic names
  async function preview() {
    const host = $('amNameRows');
    host.textContent = '';
    const got = await api('GET', '/api/admin/audio_mixers/names');
    if (got.problem) { host.appendChild(el('p', 'note', got.problem)); return; }
    const table = el('table');
    const hr = el('tr');
    ['', 'Mic', 'Desk channel', 'Now', 'Will be', 'Person'].forEach(function (t) { hr.appendChild(el('th', '', t)); });
    table.appendChild(hr);
    const picks = [];
    got.rows.forEach(function (row) {
      const tr = el('tr');
      const box = el('input', { type: 'checkbox', ariaLabel: 'Send ' + row.proposed });
      const change = row.can && row.proposed && row.proposed !== row.now;
      box.checked = !!change; box.disabled = !change;
      const td = el('td'); td.appendChild(box); tr.appendChild(td);
      [row.mic, row.desk + ' ' + row.strip, row.now || '', row.proposed || '(nobody)', row.person || ''].forEach(function (t) {
        tr.appendChild(el('td', '', t));
      });
      picks.push({ box: box, row: row });
      table.appendChild(tr);
    });
    host.appendChild(el('p', 'note', got.plan ? 'Plan: ' + got.plan : ''));
    host.appendChild(table);
    host.appendChild(button('Send ticked names', async function () {
      const rows = picks.filter(function (p) { return p.box.checked; }).map(function (p) { return { key: p.row.key, name: p.row.proposed }; });
      if (!rows.length) throw new Error('Tick the names to send.');
      if (!(await relayUI.confirm('Send ' + rows.length + ' name' + (rows.length === 1 ? '' : 's') + ' to the desk now?'))) return;
      const out = await api('POST', '/api/admin/audio_mixers/names', { rows: rows, confirm: true });
      say(out.problems.length ? out.problems.join(' ') : '');
      host.appendChild(el('p', 'note ok', 'Sent ' + out.sent + ' name' + (out.sent === 1 ? '' : 's') + '.'));
    }, 'primary'));
  }

  // ------------------------------------------------------------------------------------------ watchdog
  function watchForm() {
    const host = $('amWatchForm');
    if (watchDirty && host.firstChild) return;
    const w = JSON.parse(JSON.stringify(data.settings.watchdog));
    host.textContent = '';
    const on = el('input', { type: 'checkbox', checked: w.on });
    on.onchange = function () { w.on = on.checked; watchDirty = true; };
    const l1 = el('label', 'check'); l1.appendChild(on); l1.appendChild(document.createTextNode('Notify when a desk drifts'));
    host.appendChild(l1);
    const who = el('select', { id: 'amWho' });
    who.appendChild(el('option', { value: '0', text: 'Nobody' }));
    (people || []).forEach(function (p) { who.appendChild(el('option', { value: String(p.id), text: p.name })); });
    who.value = String(w.user_id || 0);
    who.onchange = function () { w.user_id = parseInt(who.value, 10) || 0; watchDirty = true; };
    host.appendChild(relayUI.field('Who to notify', who, 'amWho'));
    [['names', 'Channel names'], ['scene', 'The scene']].forEach(function (p) {
      const b = el('input', { type: 'checkbox', checked: w[p[0]] });
      b.onchange = function () { w[p[0]] = b.checked; watchDirty = true; };
      const l = el('label', 'check'); l.appendChild(b); l.appendChild(document.createTextNode(p[1])); host.appendChild(l);
    });
    const mutes = el('input', { type: 'text', id: 'amMutes', value: (w.mutes || []).join(', '),
      placeholder: 'desk:mix1, desk:main1' });
    mutes.oninput = function () { watchDirty = true; };
    host.appendChild(relayUI.field('Watch the mutes of (desk id:strip, like ' + ((data.desks[0] || {}).id || 'desk1') + ':main1)', mutes, 'amMutes'));
    const row = el('div', 'ctl');
    row.appendChild(button('Save watchdog', async function () {
      w.mutes = mutes.value.split(',').map(function (x) { return x.trim(); }).filter(Boolean);
      data = await api('PUT', '/api/admin/audio_mixers/settings', { watchdog: w });
      watchDirty = false; paint();
    }, 'primary'));
    data.desks.forEach(function (d) {
      row.appendChild(button('Save ' + d.name + ' as it is now', async function () {
        data = await api('POST', '/api/admin/audio_mixers/' + encodeURIComponent(d.id) + '/baseline');
        paint();
      }));
    });
    host.appendChild(row);
    data.desks.forEach(function (d) {
      host.appendChild(el('p', 'note', d.name + ': ' + (d.baseline_at ? 'baseline saved ' + new Date(d.baseline_at * 1000).toLocaleString() : 'no baseline saved')));
    });
  }

  // ------------------------------------------------------------------------------------------ I/O patch from a show file
  let patchSig = '';
  function patchTable(heads, rows) {
    const table = el('table');
    const hr = el('tr');
    heads.forEach(function (t) { hr.appendChild(el('th', '', t)); });
    table.appendChild(hr);
    rows.forEach(function (cells) {
      const tr = el('tr');
      cells.forEach(function (t) { tr.appendChild(el('td', '', t)); });
      table.appendChild(tr);
    });
    return table;
  }
  async function sendShow(d, file) {
    const res = await fetch('/api/admin/audio_mixers/' + encodeURIComponent(d.id) + '/patch?name=' + encodeURIComponent(file.name),
      { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/octet-stream' }, body: file });
    const out = await res.json().catch(function () { return {}; });
    if (!res.ok) throw new Error(out.error || out.detail || 'That did not work.');
  }
  function patchForm() {
    const host = $('amPatchRows');
    const list = data.desks.filter(function (d) { return d.model === 'avantis' || d.model === 'dlive' || /^qu/.test(d.model); });
    const kept = (data.settings && data.settings.patches) || {};
    const sig = JSON.stringify(list.map(function (d) { return [d.id, d.name, d.model, (kept[d.id] || {}).read_at || 0]; }));
    $('amPatch').hidden = !list.length;
    if (sig === patchSig) return;
    patchSig = sig;
    host.textContent = '';
    list.forEach(function (d) {
      const card = el('div', 'pcard amform');
      card.appendChild(el('h4', '', d.name));
      const got = kept[d.id];
      if (/^qu/.test(d.model)) {
        card.appendChild(el('p', 'note', 'Qu show files are not supported yet.'));
        host.appendChild(card);
        return;
      }
      const file = el('input', { type: 'file', accept: '.gz,.tgz,application/gzip', id: 'amPatchFile' + d.id, 'aria-label': 'Show file for ' + d.name });
      file.onchange = async function () {
        const f = file.files[0];
        file.value = '';
        if (!f) return;
        say('');
        try { await sendShow(d, f); data = await api('GET', '/api/admin/audio_mixers', null, { cache: 'no-store' }); patchSig = ''; paint(); }
        catch (e) { say(e.message); }
      };
      card.appendChild(relayUI.field(got ? 'Read a newer show' : 'Choose the exported show', file, 'amPatchFile' + d.id));
      if (got) {
        card.appendChild(el('p', 'note', got.file + ' (' + (got.family === 'avantis' ? 'Avantis' : 'dLive') + ' show format ' + got.firmware +
          ', read ' + new Date(got.read_at * 1000).toLocaleString() + '): ' + got.inputs.length + ' inputs, ' + got.outputs.length + ' outputs patched.' +
          (got.verified ? '' : ' This layout has not been checked against a real export yet.')));
        card.appendChild(patchTable(['Ch', 'Name', 'Source'], got.inputs.map(function (i) {
          return [String(i.channel), i.named ? i.name : '', i.source.label];
        })));
        if (got.outputs.length) {
          const more = el('details');
          more.appendChild(el('summary', '', 'Outputs (' + got.outputs.length + ')'));
          more.appendChild(patchTable(['Output', 'Carries'], got.outputs.map(function (o) { return [o.destination, o.carries]; })));
          card.appendChild(more);
        }
        const row = el('div', 'ctl');
        row.appendChild(button('Clear the patch', async function () {
          if (!(await relayUI.confirm('Forget the patch read for ' + d.name + '? Nothing changes on the desk.'))) return;
          await api('DELETE', '/api/admin/audio_mixers/' + encodeURIComponent(d.id) + '/patch');
          data = await api('GET', '/api/admin/audio_mixers', null, { cache: 'no-store' }); patchSig = ''; paint();
        }, 'danger sm'));
        card.appendChild(row);
      }
      host.appendChild(card);
    });
  }

  // ------------------------------------------------------------------------------------------ setup
  function modelOf(id) { return (data.models || []).filter(function (m) { return m.id === id; })[0] || {}; }
  function deskForm() {
    const host = $('amDeskForm');
    if (draftDirty && host.firstChild) return;
    if (!draftDirty) draft = JSON.parse(JSON.stringify(data.settings.desks));
    host.textContent = '';
    if (!draft.length) host.appendChild(el('p', 'note', 'No desk yet. Add one, then Save.'));
    draft.forEach(function (d, i) {
      const card = el('div', 'pcard amform');
      const m = modelOf(d.model);
      const mark = function () { draftDirty = true; };
      const inp = function (key, attrs, label) {
        const fid = 'amD' + i + key;
        const x = el('input', Object.assign({ id: fid, value: d[key] === undefined || d[key] === null ? '' : String(d[key]) }, attrs || {}));
        x.oninput = function () { d[key] = x.type === 'number' ? (parseInt(x.value, 10) || 0) : x.value; mark(); };
        card.appendChild(relayUI.field(label, x, fid));
      };
      inp('name', { type: 'text', maxLength: 40 }, 'Name');
      const sel = el('select', { id: 'amD' + i + 'model' });
      (data.models || []).forEach(function (o) {
        sel.appendChild(el('option', { value: o.id, text: o.label + (o.tested ? '' : ' (untested)') }));
      });
      sel.value = d.model;
      sel.onchange = function () { d.model = sel.value; mark(); draftDirty = true; host.textContent = ''; deskForm(); };
      card.appendChild(relayUI.field('Model', sel, 'amD' + i + 'model'));
      inp('host', { type: 'text', placeholder: '192.168.1.70' }, 'Address');
      inp('port', { type: 'number', min: '0', max: '65535', placeholder: String(m.port || '') }, 'Port (0: the model’s own, ' + (m.port || '') + ')');
      if (m.family && /Allen/.test(m.family)) inp('midi_channel', { type: 'number', min: '1', max: '12' }, 'MIDI channel (as set on the desk)');
      if (m.ahnet) {
        const b = el('input', { type: 'checkbox', checked: !!d.ahnet });
        b.onchange = function () { d.ahnet = b.checked; mark(); };
        const l = el('label', 'check'); l.appendChild(b); l.appendChild(document.createTextNode('Also read faders and scene names over AH-Net'));
        card.appendChild(l);
        card.appendChild(el('p', 'note', 'AH-Net (port 51321) is Allen & Heath’s own protocol, not published for others: it may stop working with new firmware, and it does not follow the console’s user profiles. Off unless you need fader levels and scene names in Relay.'));
        inp('scene_names', { type: 'number', min: '0', max: '500' }, 'Scene names to read (0: none)');
      }
      if (m.note) card.appendChild(el('p', 'note', m.note));
      const row = el('div', 'ctl');
      row.appendChild(button('Remove', async function () {
        if (!(await relayUI.confirm('Remove ' + (d.name || 'this desk') + ' from Relay? Nothing changes on the desk.'))) return;
        draft.splice(i, 1); draftDirty = true; host.textContent = ''; deskForm();
      }, 'danger sm'));
      card.appendChild(row);
      host.appendChild(card);
    });
  }

  // ------------------------------------------------------------------------------------------ the page
  function paint() {
    if (!data) return;
    const host = $('amDesks');
    const ids = data.desks.map(function (d) { return d.id; });
    Object.keys(views).forEach(function (id) { if (ids.indexOf(id) < 0) delete views[id]; });
    const roots = data.desks.map(paintDesk);
    if (roots.length !== host.children.length || roots.some(function (r, i) { return host.children[i] !== r; })) {
      host.textContent = '';
      roots.forEach(function (r) { host.appendChild(r); });
    }
    if (!data.desks.length) host.appendChild(el('p', 'note', 'No desk set up yet.'));
    const up = data.desks.filter(function (d) { return d.connected; }).length;
    $('amSum').textContent = data.desks.length ? up + ' of ' + data.desks.length + ' connected' : '';
    $('amNames').hidden = !can('audio_mixers.control');
    const manage = can('audio_mixers.manage');
    $('amWatch').hidden = !manage; $('amSetup').hidden = !manage;
    if (manage) { watchForm(); deskForm(); patchForm(); } else { $('amPatch').hidden = true; }
  }

  $('amPreview').onclick = function () { say(''); preview().catch(function (e) { say(e.message); }); };
  $('amAdd').onclick = function () {
    if (!draft) draft = [];
    if (draft.length >= (data.max_desks || 8)) { say('Relay follows up to ' + data.max_desks + ' desks.'); return; }
    draft.push({ name: '', model: 'x32', host: '', port: 0, midi_channel: 1, ahnet: false, scene_names: 0 });
    draftDirty = true; $('amDeskForm').textContent = ''; deskForm();
  };
  $('amSave').onclick = function () {
    say('');
    api('PUT', '/api/admin/audio_mixers/settings', { desks: draft || [] }).then(function (got) {
      data = got; draftDirty = false; paint();
    }).catch(function (e) { say(e.message); });
  };

  let running = false;
  async function follow() {
    if (running) return;
    running = true;
    while (relayUI.shown($('page-audio_mixers')) || !data) {
      try {
        data = await api('GET', '/api/admin/audio_mixers/wait?since=' + (data ? data.version : -1), null, { cache: 'no-store' });
        paint();
      } catch (e) {
        say(e.message);
        await new Promise(function (ok) { setTimeout(ok, 3000); });
      }
    }
    running = false;
  }
  async function open() {
    if (can('audio_mixers.manage') && people === null) {
      people = [];
      api('GET', '/api/admin/audio_mixers/people').then(function (got) { people = got.people; watchDirty = false; paint(); })
        .catch(function () { people = []; });
    }
    follow();
  }
  $('page-audio_mixers').addEventListener('relay:tab', open);
  document.addEventListener('visibilitychange', function () { if (!document.hidden && relayUI.shown($('page-audio_mixers'))) open(); });
  if (relayUI.shown($('page-audio_mixers'))) open();
})();
