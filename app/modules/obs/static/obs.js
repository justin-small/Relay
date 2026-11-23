/* The OBS Studio admin page (#250): the OBS computers, their live state, outputs, studio mode, audio, scene
   switching, a preview, the bitrate history and the scene screen links. Loaded by admin.html only while the module
   is on. Built from the admin page's own pieces (section, .pcard, dl, .pactions, .spill, .meter, details.sect) so it
   looks like the rest of Relay; the few rules below only lay those pieces out. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-obs')) return;
  const $ = R.$;
  const can = relayUI.can;
  function showError(text) { $('obsErr').textContent = text || ''; $('obsErr').hidden = !text; }
  let data = null;
  let machines = [];          // the form's copy; live state comes from `data`
  let dirty = false;
  let shownSettings = '';

  const send = relayUI.api;
  const el = relayUI.el;
  const button = relayUI.buttons(showError);
  const field = relayUI.field;
  let uid = 0;
  function input(m, key, attrs) {
    const i = el('input');
    i.id = 'obsF' + (++uid);
    Object.keys(attrs || {}).forEach(function (k) { i.setAttribute(k, attrs[k]); });
    if (i.type === 'checkbox') {
      i.checked = !!m[key];
      i.onchange = function () { m[key] = i.checked; dirty = true; };
    } else {
      i.value = m[key] === undefined || m[key] === null ? '' : m[key];
      i.oninput = function () { m[key] = i.type === 'number' ? parseFloat(i.value || '0') : i.value; dirty = true; };
    }
    return i;
  }
  function check(m, key, label) {
    const l = el('label', 'check');
    l.appendChild(input(m, key, { type: 'checkbox' }));
    l.appendChild(document.createTextNode(label));
    return l;
  }
  function select(options, value, onchange) {
    const s = el('select');
    s.id = 'obsF' + (++uid);
    options.forEach(function (o) {
      const opt = el('option', '', o.label); opt.value = o.id; opt.selected = o.id === value; s.appendChild(opt);
    });
    s.onchange = function () { onchange(s.value); dirty = true; };
    return s;
  }
  function clock(ms) {
    const s = Math.floor(ms / 1000);
    const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, x = s % 60;
    return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(x).padStart(2, '0');
  }
  function mb(bytes) { return bytes > 1e9 ? (bytes / 1e9).toFixed(2) + ' GB' : (bytes / 1e6).toFixed(1) + ' MB'; }
  function pct(a, b) { return b ? (a * 100 / b).toFixed(2) + '%' : '0%'; }
  function live(id) { return ((data && data.machines) || []).filter(function (x) { return x.id === id; })[0] || {}; }
  function act(path, body) { return send('POST', '/api/admin/obs/' + path, body).then(refresh); }
  const pill = relayUI.pill;
  const link = relayUI.link;
  const HEALTH = { ok: 'Healthy', warn: 'Needs a look', error: 'Trouble', idle: 'Standing by' };

  // ------------------------------------------------------------------------------------------ the live part
  function chart(history) {
    // Bitrate (line) and intervals that dropped frames (bars) over the last 15 minutes.
    if (!history || history.length < 2) return el('p', 'note', 'The bitrate chart shows here while OBS streams.');
    const NS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('class', 'obschart'); svg.setAttribute('viewBox', '0 0 480 64'); svg.setAttribute('preserveAspectRatio', 'none');
    svg.setAttribute('role', 'img');
    const t0 = history[0][0], t1 = history[history.length - 1][0], span = Math.max(1, t1 - t0);
    const top = Math.max(1000, Math.max.apply(null, history.map(function (h) { return h[1]; })) * 1.1);
    history.forEach(function (h) {
      if (h[3] > 0) {
        const r = document.createElementNS(NS, 'rect');
        r.setAttribute('x', (h[0] - t0) / span * 476 + 1); r.setAttribute('width', 2); r.setAttribute('y', 0); r.setAttribute('height', 50);
        r.setAttribute('fill', h[3] > 1 ? 'var(--err)' : 'var(--warn)'); r.setAttribute('opacity', '.55'); svg.appendChild(r);
      }
    });
    const line = document.createElementNS(NS, 'polyline');
    line.setAttribute('points', history.map(function (h) {
      return ((h[0] - t0) / span * 476 + 2).toFixed(1) + ',' + (50 - h[1] / top * 46).toFixed(1);
    }).join(' '));
    line.setAttribute('fill', 'none'); line.setAttribute('stroke', 'currentColor');
    line.setAttribute('stroke-width', '1.5'); line.setAttribute('vector-effect', 'non-scaling-stroke'); svg.appendChild(line);
    const last = history[history.length - 1];
    const t = document.createElementNS(NS, 'text'); t.setAttribute('x', 2); t.setAttribute('y', 62);
    t.textContent = last[1] + ' kb/s now · peak ' + Math.round(top / 1.1) + ' kb/s · last ' + Math.max(1, Math.round(span / 60)) + ' min';
    svg.setAttribute('aria-label', 'Bitrate over the last ' + Math.max(1, Math.round(span / 60)) + ' minutes, ' + last[1] + ' kb/s now');
    svg.appendChild(t);
    return svg;
  }

  function statusCard(m, lv) {
    const card = el('div', 'pcard mcard obsstatus');
    const head = el('div', 'phead');
    const s = lv.stream || {}, r = lv.record || {};
    head.appendChild(pill(s.active ? 'LIVE ' + clock(s.duration_ms) : 'Off air', s.active ? 'off' : 'idle'));
    if (r.active) head.appendChild(pill((r.paused ? 'Paused ' : 'REC ') + clock(r.duration_ms), r.paused ? 'idle' : 'off'));
    const state = el('span', 'pstate mhealth ' + lv.health, HEALTH[lv.health] || lv.health);
    head.appendChild(state);
    card.appendChild(head);
    if (lv.health !== 'ok' && lv.health !== 'idle' || lv.state !== 'connected') card.appendChild(el('p', 'note', lv.detail));
    if (lv.state !== 'connected') return card;
    const st = lv.stats;
    const dl = el('dl');
    function row(name, value) {
      dl.appendChild(el('dt', '', name));
      const dd = el('dd');
      if (typeof value === 'string') dd.textContent = value; else dd.appendChild(value);
      dl.appendChild(dd);
    }
    row('Program', lv.program + (lv.studio && lv.preview_scene ? '  ·  preview: ' + lv.preview_scene : ''));
    const to = el('span', '', lv.service || 'Not set in OBS');
    if (lv.youtube) {
      to.appendChild(document.createTextNode(' · '));
      to.appendChild(link('Open in YouTube Studio', lv.youtube.studio));
      to.appendChild(document.createTextNode(' · '));
      to.appendChild(link('Watch page', lv.youtube.watch));
    }
    row('Streams to', to);
    if (s.active) {
      row('Stream', mb(s.bytes) + ' sent · congestion ' + Math.round(s.congestion * 100) + '%' + (s.reconnecting ? ' · reconnecting' : ''));
      row('Dropped', s.skipped + ' of ' + s.total + ' frames (' + pct(s.skipped, s.total) + ')');
    }
    row('Last minute', lv.minute.network + '% dropped · ' + lv.minute.encode + '% skipped · ' + lv.minute.render + '% lagged');
    row('Recording', r.active ? (r.paused ? 'Paused at ' : 'Recording, ') + clock(r.duration_ms) + ' · ' + mb(r.bytes) : 'Not recording');
    row('Replay buffer', !lv.replay.available ? 'Off in OBS (Settings › Output › Replay Buffer)'
      : (lv.replay.active ? 'Running' : 'Stopped') + (lv.replay.last ? ' · last saved ' + lv.replay.last : ''));
    if (m.captions) row('Captions', lv.captions_sent + ' lines sent' + (m.caption_delay ? ' · ' + m.caption_delay + ' s delay' : '')
      + (lv.caption_error ? ' · ' + lv.caption_error : ''));
    row('OBS ' + lv.version, st.fps + ' fps · render ' + st.render_ms + ' ms · CPU ' + st.cpu + '% · '
      + (st.disk_mb / 1024).toFixed(0) + ' GB free');
    card.appendChild(dl);
    if (can('obs.control')) card.appendChild(outputs(m, lv));
    return card;
  }

  function outputs(m, lv) {
    const box = el('div', 'pactions');
    const s = lv.stream, r = lv.record, rp = lv.replay;
    const out = function (which, action) { return act('output', { machine: m.id, output: which, action: action }); };
    box.appendChild(s.active
      ? button('Stop stream', async function () {
          if (!(await relayUI.confirm('Stop the stream on ' + m.name + '? Viewers will see it end.'))) return Promise.resolve();
          return out('stream', 'stop');
        }, 'danger')
      : button('Start stream', async function () {
          if (!(await relayUI.confirm('Start streaming from ' + m.name + '? It goes live wherever OBS is set to stream'
            + (lv.service ? ' (' + lv.service + ')' : '') + '.'))) return Promise.resolve();
          return out('stream', 'start');
        }, 'primary'));
    if (r.active) {
      box.appendChild(button(r.paused ? 'Resume recording' : 'Pause recording', function () { return out('record', r.paused ? 'resume' : 'pause'); }));
      box.appendChild(button('Stop recording', async function () {
        if (!(await relayUI.confirm('Stop the recording on ' + m.name + '?'))) return Promise.resolve();
        return out('record', 'stop');
      }));
    } else {
      box.appendChild(button('Start recording', function () { return out('record', 'start'); }));
    }
    if (rp.available) {
      if (rp.active) {
        box.appendChild(button('Save replay', function () { return out('replay', 'save'); }));
        box.appendChild(button('Stop replay buffer', function () { return out('replay', 'stop'); }));
      } else {
        box.appendChild(button('Start replay buffer', function () { return out('replay', 'start'); }));
      }
    }
    return box;
  }

  function sideCard(m, lv) {
    const card = el('div', 'pcard obsside');
    const head = el('div', 'phead');
    head.appendChild(el('h4', '', m.preview ? 'Program' : 'Bitrate'));
    card.appendChild(head);
    if (m.preview) {
      const img = el('img', 'obspreview');
      img.alt = 'Preview of ' + lv.program;
      img.dataset.machine = m.id;
      card.appendChild(img);
    }
    card.appendChild(chart(lv.history));
    return card;
  }

  function scenesCard(m, lv) {
    const card = el('div', 'pcard obsscenecard');
    const head = el('div', 'phead');
    head.appendChild(el('h4', '', 'Scenes'));
    if (can('obs.control')) {
      const right = el('span', 'pstate');
      if (lv.studio) {
        right.appendChild(button('Take' + (lv.preview_scene ? ': ' + lv.preview_scene : ''), function () {
          return act('studio', { machine: m.id, action: 'take' });
        }, 'primary'));
        right.appendChild(document.createTextNode(' '));
      }
      right.appendChild(button(lv.studio ? 'Leave studio mode' : 'Studio mode', function () {
        return act('studio', { machine: m.id, action: lv.studio ? 'off' : 'on' });
      }));
      head.appendChild(right);
    }
    card.appendChild(head);
    if (lv.studio) card.appendChild(el('p', 'note', 'Studio mode: tap a scene to put it in preview, then Take.'));
    const row = el('div', 'obsscenes');
    lv.scenes.forEach(function (name) {
      const cls = name === lv.program ? 'primary' : name === lv.preview_scene ? 'preview' : '';
      const b = button(name, function () {
        return lv.studio ? act('studio', { machine: m.id, action: 'preview', scene: name })
                         : act('scene', { machine: m.id, scene: name });
      }, cls);
      b.title = name === lv.program ? 'On air' : name === lv.preview_scene ? 'In preview' : '';
      if (!can('obs.control')) b.disabled = true;
      row.appendChild(b);
    });
    card.appendChild(row);
    return card;
  }

  function audioCard(m) {
    const card = el('div', 'pcard obsaudio');
    card.dataset.machine = m.id;
    const head = el('div', 'phead');
    head.appendChild(el('h4', '', 'Audio'));
    card.appendChild(head);
    (m.audio || []).forEach(function (a) {
      const wrap = el('div', 'meterwrap');
      wrap.dataset.input = a.name;
      const name = el('span', 'obsname', a.name); name.title = a.name;
      wrap.appendChild(name);
      const col = el('div', 'metercol');
      const meter = el('div', 'meter'); meter.appendChild(el('i')); meter.appendChild(el('b'));
      col.appendChild(meter);
      wrap.appendChild(col);
      const side = el('div', 'meterside');
      side.appendChild(el('span', 'meterread'));
      if (can('obs.control')) {
        const b = el('button', '', 'Mute'); b.type = 'button'; b.className = 'obsmute';
        b.onclick = async function () {
          showError('');
          try { await send('POST', '/api/admin/obs/mute', { machine: m.id, input: a.name, muted: b.dataset.muted !== '1' }); }
          catch (e) { showError(e.message); }
        };
        side.appendChild(b);
      }
      wrap.appendChild(side);
      card.appendChild(wrap);
    });
    return card;
  }

  function liveBox(m) {
    const box = el('div', 'obslive');
    const lv = live(m.id).live;
    if (!lv) { box.appendChild(el('p', 'note', 'Save to connect.')); return box; }
    if (lv.state !== 'connected') { box.appendChild(statusCard(m, lv)); return box; }
    const grid = el('div', 'obscards');
    grid.appendChild(statusCard(m, lv));
    grid.appendChild(sideCard(m, lv));
    box.appendChild(grid);
    const stack = el('div', 'obsstack');
    stack.style.marginTop = '12px';
    stack.appendChild(scenesCard(m, lv));
    if ((m.audio || []).length) stack.appendChild(audioCard(m));
    box.appendChild(stack);
    return box;
  }

  // ------------------------------------------------------------------------------------------ the settings part
  async function screenControls(m) {
    const box = el('div');
    if (!m.id || !can('obs.manage')) return box;
    const r = await send('GET', '/api/admin/obs/screen/' + encodeURIComponent(m.id));
    box.appendChild(el('h4', '', 'Scene screen for a tablet'));
    box.appendChild(el('p', 'note', 'A page of big scene buttons for this computer. The link is its only key: anyone with it '
      + 'can switch scenes, and a new link stops the old one.'));
    const ctl = el('div', 'ctl');
    ctl.appendChild(button(r.on ? 'Turn screen off' : 'Turn screen on', async function () {
      await send('PUT', '/api/admin/obs/screen/' + encodeURIComponent(m.id), { on: !r.on });
      render(true);
    }));
    if (r.on) {
      ctl.appendChild(button('New link', async function () {
        if (!(await relayUI.confirm('The old link stops working. Continue?'))) return;
        await send('PUT', '/api/admin/obs/screen/' + encodeURIComponent(m.id), { on: true, rotate: true });
        render(true);
      }));
      const linkBox = el('input', 'grow mono'); linkBox.readOnly = true; linkBox.value = r.link;
      linkBox.setAttribute('aria-label', 'Scene screen link');
      linkBox.onfocus = function () { linkBox.select(); };
      const g = el('span', 'grow'); g.appendChild(linkBox);
      ctl.appendChild(g);
    }
    box.appendChild(ctl);
    return box;
  }

  function audioSettings(m) {
    const box = el('div');
    box.appendChild(el('h4', '', 'Audio to watch'));
    const inputs = ((live(m.id).live || {}).inputs || []).slice();
    (m.audio || []).forEach(function (a) { if (inputs.indexOf(a.name) < 0) inputs.push(a.name); });
    if (!inputs.length) { box.appendChild(el('p', 'note', 'Connect to OBS to list its audio inputs.')); return box; }
    const list = el('div', 'obslist');
    inputs.forEach(function (name) {
      const cur = (m.audio || []).filter(function (a) { return a.name === name; })[0];
      const row = el('div', 'row');
      const on = el('input'); on.type = 'checkbox'; on.checked = !!cur;
      const warn = el('input'); warn.type = 'checkbox'; warn.checked = !!(cur && cur.warn_muted); warn.disabled = !cur;
      on.onchange = function () {
        m.audio = (m.audio || []).filter(function (a) { return a.name !== name; });
        if (on.checked) m.audio.push({ name: name, warn_muted: warn.checked });
        warn.disabled = !on.checked; dirty = true;
      };
      warn.onchange = function () {
        (m.audio || []).forEach(function (a) { if (a.name === name) a.warn_muted = warn.checked; }); dirty = true;
      };
      const l1 = el('label', 'check'); l1.appendChild(on); l1.appendChild(document.createTextNode(name));
      const l2 = el('label', 'check'); l2.appendChild(warn); l2.appendChild(document.createTextNode('Warn if muted while live'));
      row.appendChild(l1); row.appendChild(l2);
      list.appendChild(row);
    });
    box.appendChild(list);
    return box;
  }

  function tallySettings(m) {
    const box = el('div');
    box.appendChild(el('h4', '', 'Tally'));
    box.appendChild(el('p', 'note', 'Give a scene a camera number: that camera\'s tally shows red while the scene is on air, '
      + 'and green while it is in preview.'));
    const scenesList = ((live(m.id).live || {}).scenes || []);
    m.tally = m.tally || [];
    const list = el('div', 'obslist');
    m.tally.forEach(function (t, i) {
      const row = el('div', 'row');
      const n = el('input'); n.type = 'number'; n.min = 1; n.max = 999; n.value = t.n;
      n.setAttribute('aria-label', 'Camera number');
      n.oninput = function () { t.n = parseInt(n.value || '0', 10); dirty = true; };
      row.appendChild(el('span', '', 'Camera'));
      row.appendChild(n);
      row.appendChild(el('span', '', 'is scene'));
      const opts = scenesList.map(function (s) { return { id: s, label: s }; });
      if (t.scene && scenesList.indexOf(t.scene) < 0) opts.push({ id: t.scene, label: t.scene + ' (not in OBS now)' });
      const sel = select(opts, t.scene, function (v) { t.scene = v; });
      sel.setAttribute('aria-label', 'Scene');
      row.appendChild(sel);
      row.appendChild(button('Remove', async function () { m.tally.splice(i, 1); dirty = true; render(); }));
      list.appendChild(row);
    });
    box.appendChild(list);
    if (scenesList.length && m.tally.length < 16) {
      const ctl = el('div', 'ctl');
      ctl.style.marginTop = '8px';
      ctl.appendChild(button('Add camera', async function () {
        const used = m.tally.map(function (t) { return t.n; });
        let n = 1; while (used.indexOf(n) >= 0) n++;
        m.tally.push({ n: n, scene: scenesList[0] }); dirty = true; render();
      }));
      box.appendChild(ctl);
    }
    return box;
  }

  function settings(m, i) {
    const set = el('details', 'sect');
    set.open = !m.id;
    const sum = el('summary', '', 'Settings');
    set.appendChild(sum);
    const row = el('div', 'row');
    row.appendChild(field('Name', input(m, 'name', { maxlength: 24, placeholder: 'Stream PC' })));
    row.appendChild(field('Address', input(m, 'host', { maxlength: 253, placeholder: '192.168.1.60', autocomplete: 'off', spellcheck: 'false' })));
    set.appendChild(row);
    const row2 = el('div', 'row');
    row2.appendChild(field('Port', input(m, 'port', { type: 'number', min: 1, max: 65535 })));
    row2.appendChild(field(m.has_password ? 'Password (saved; leave blank to keep it)' : 'Password',
      input(m, 'password', { type: 'password', maxlength: 200, autocomplete: 'new-password' })));
    set.appendChild(row2);
    set.appendChild(check(m, 'preview', 'Show a preview of the program scene on this page'));
    set.appendChild(el('h4', '', 'With Relay\'s Start and Stop'));
    set.appendChild(check(m, 'start_record', 'Start recording when Start is pressed, and stop it with Stop'));
    set.appendChild(check(m, 'start_stream', 'Go live when Start is pressed, and end the stream with Stop'));

    set.appendChild(el('h4', '', 'Captions into the stream'));
    set.appendChild(check(m, 'captions', 'Send Relay\'s captions into this stream (CEA-608)'));
    const cap = el('div', 'row');
    cap.appendChild(field('Language', select(data.languages || [], m.caption_lang, function (v) { m.caption_lang = v; })));
    cap.appendChild(field('Delay in seconds, to match a delayed stream', input(m, 'caption_delay', { type: 'number', min: 0, max: 30, step: 0.5 })));
    set.appendChild(cap);

    set.appendChild(el('h4', '', 'Alerts'));
    const buttons = [{ id: '', label: 'None' }].concat((data.alert_buttons || []).map(function (b) { return { id: b.id, label: b.name }; }));
    const al = el('div', 'row');
    al.appendChild(field('When this OBS is in trouble, raise', select(buttons, m.alert_error, function (v) { m.alert_error = v; })));
    al.appendChild(field('When it needs a look, raise', select(buttons, m.alert_warn, function (v) { m.alert_warn = v; })));
    set.appendChild(al);
    if (!(data.alert_buttons || []).length) set.appendChild(el('p', 'note', 'Add alert buttons on the Alerts page to choose them here.'));

    set.appendChild(audioSettings(m));
    set.appendChild(tallySettings(m));
    const holder = el('div');
    set.appendChild(holder);
    if (m.id && data.machines.some(function (x) { return x.id === m.id; })) {
      screenControls(m).then(function (x) { holder.appendChild(x); }).catch(function () {});
    }
    set.appendChild(el('h4', '', 'This computer'));
    const ctl = el('div', 'ctl');
    const status = el('div', 'note');
    ctl.appendChild(button('Test connection', async function () {
      const r = await send('POST', '/api/admin/obs/test', { machine: m.id });
      status.textContent = r.detail;
    }));
    if (m.has_password) ctl.appendChild(button('Forget password', async function () { m.clear_password = true; m.has_password = false; dirty = true; render(); }));
    ctl.appendChild(button('Remove computer', async function () { machines.splice(i, 1); dirty = true; render(); }, 'danger'));
    set.appendChild(ctl);
    set.appendChild(status);
    set.appendChild(el('p', 'note', 'Changes take effect when you press Save at the bottom of the page.'));
    return set;
  }

  function card(m, i) {
    const c = el('section', 'obsmachine');
    const h = el('h3', '', m.name || 'OBS');
    const lv = live(m.id).live;
    if (lv && lv.version) h.appendChild(el('span', 'count', m.host + ' · OBS ' + lv.version));
    else if (m.host) h.appendChild(el('span', 'count', m.host));
    c.appendChild(h);
    c.appendChild(liveBox(m));
    if (can('obs.manage')) c.appendChild(settings(m, i));
    return c;
  }

  function render(reload) {
    if (reload) { dirty = false; shownSettings = ''; return refresh(); }
    const open = {};
    document.querySelectorAll('#obsMachines details.sect').forEach(function (d, i) { open[i] = d.open; });
    const box = $('obsMachines');
    box.textContent = '';
    machines.forEach(function (m, i) {
      const c = card(m, i);
      const d = c.querySelector('details.sect');
      if (d && open[i] !== undefined) d.open = open[i];
      box.appendChild(c);
    });
    if (!machines.length) {
      const s = el('section');
      s.appendChild(el('p', 'note', 'No OBS computer yet. Add the computer that runs OBS.'));
      box.appendChild(s);
    }
    $('obsAdd').hidden = $('obsSave').hidden = !can('obs.manage');
    $('obsAdd').disabled = machines.length >= 4;
  }

  // Live parts redraw without touching the settings form, which may be half typed. The buttons are rebuilt only
  // when what they show changed, so a click is never lost to a redraw; otherwise the numbers are swapped in place.
  function redrawLive() {
    document.querySelectorAll('#obsMachines .obsmachine').forEach(function (c, i) {
      const m = machines[i];
      if (!m) return;
      const saved = live(m.id);
      const view = Object.assign({}, saved, { id: m.id, name: m.name });
      const old = c.querySelector('.obslive');
      const lv = saved.live || {};
      const sig = JSON.stringify([lv.state, lv.scenes, lv.program, lv.preview_scene, lv.studio, lv.youtube, lv.health,
        (lv.stream || {}).active, (lv.record || {}).active, (lv.record || {}).paused, lv.replay, saved.audio, saved.preview]);
      const fresh = liveBox(view);
      if (old && old.dataset.sig === sig) {
        // Same buttons: take the new status lines, pills and chart, keep the rest.
        [['.obsstatus .phead', '.obsstatus .phead'], ['.obsstatus dl', '.obsstatus dl'],
         ['.obsside .obschart, .obsside p.note', '.obsside .obschart, .obsside p.note']].forEach(function (q) {
          const a = old.querySelector(q[0]), b = fresh.querySelector(q[1]);
          if (a && b) a.replaceWith(b);
        });
        return;
      }
      const img = old && old.querySelector('img.obspreview');
      const newImg = fresh.querySelector('img.obspreview');
      if (img && newImg) newImg.replaceWith(img);   // keep the picture already loaded
      const oldMeters = old && old.querySelector('.obsaudio');
      const newMeters = fresh.querySelector('.obsaudio');
      if (oldMeters && newMeters && oldMeters.children.length === newMeters.children.length) newMeters.replaceWith(oldMeters);
      fresh.dataset.sig = sig;
      if (old) old.replaceWith(fresh); else c.insertBefore(fresh, c.children[1] || null);
    });
  }

  async function refresh() {
    try {
      data = await send('GET', '/api/admin/obs');
    } catch (e) { showError(e.message); return; }
    const ups = data.machines.filter(function (m) { return m.live; });
    const worst = ['error', 'warn', 'ok', 'idle'].filter(function (h) { return ups.some(function (m) { return m.live.health === h; }); })[0];
    const onAir = ups.some(function (m) { return m.live.stream && m.live.stream.active; });
    const sum = $('obsSum');
    sum.textContent = '';
    if (onAir) sum.appendChild(el('span', 'live', 'on air'));
    else if (worst) sum.textContent = (HEALTH[worst] || worst).toLowerCase();
    // The settings part is rebuilt only when the saved settings changed; otherwise only the live part is.
    const settingsSig = JSON.stringify(data.machines.map(function (m) { const x = Object.assign({}, m); delete x.live; return x; }));
    if (!dirty && settingsSig !== shownSettings) {
      shownSettings = settingsSig;
      machines = data.machines.map(function (m) { return Object.assign({ password: '' }, JSON.parse(JSON.stringify(m))); });
      render();
    }
    redrawLive();
  }

  $('obsAdd').onclick = function () {
    machines.push({ id: '', name: 'OBS', host: '', port: 4455, password: '', preview: false, captions: false, start_record: false, start_stream: false,
                    caption_lang: 'source', caption_delay: 0, alert_error: '', alert_warn: '', audio: [], tally: [] });
    dirty = true; render();
  };
  $('obsSave').onclick = async function () {
    showError('');
    try {
      data = await send('PUT', '/api/admin/obs/settings', { machines: machines.map(function (m) {
        return { id: m.id, name: m.name, host: m.host, port: m.port, password: m.password || '',
                 clear_password: !!m.clear_password, preview: !!m.preview, captions: !!m.captions, start_record: !!m.start_record, start_stream: !!m.start_stream,
                 caption_lang: m.caption_lang, caption_delay: m.caption_delay || 0,
                 alert_error: m.alert_error || '', alert_warn: m.alert_warn || '',
                 audio: m.audio || [], tally: (m.tally || []).filter(function (t) { return t.scene; }) };
      }) });
      dirty = false;
      shownSettings = '';
      refresh();
    } catch (e) { showError(e.message); }
  };

  function visible() { return !document.hidden && document.getElementById('page-obs').offsetParent !== null; }

  // Meters: a few times a second while the page is on screen. -60 dBFS at the left, 0 at the right, as on Session
  // health; the bar is the level now, the marker the peak held for a second.
  async function pollAudio() {
    const cards = document.querySelectorAll('#obsMachines .obsaudio');
    for (let i = 0; i < cards.length; i++) {
      const card = cards[i];
      try {
        const r = await fetch('/api/admin/obs/audio/' + encodeURIComponent(card.dataset.machine), { credentials: 'same-origin', cache: 'no-store' });
        if (!r.ok) continue;
        (await r.json()).audio.forEach(function (a) {
          const row = Array.prototype.filter.call(card.querySelectorAll('.meterwrap'), function (x) { return x.dataset.input === a.name; })[0];
          if (!row) return;
          const w = function (db) { return Math.max(0, Math.min(100, (db + 60) / 60 * 100)); };
          const meter = row.querySelector('.meter');
          meter.querySelector('i').style.width = (a.muted ? 0 : w(a.db)) + '%';
          const peak = meter.querySelector('b');
          peak.style.left = w(a.peak) + '%'; peak.style.opacity = a.muted || a.peak <= -59 ? 0 : 0.9;
          meter.classList.toggle('clipping', a.peak > -3);
          meter.classList.toggle('hot', a.peak > -12 && a.peak <= -3);
          const read = row.querySelector('.meterread');
          read.textContent = !a.present ? 'not in OBS' : a.muted ? 'MUTED' : a.db.toFixed(0) + ' dB';
          read.classList.toggle('mutedread', a.muted);
          const mute = row.querySelector('.obsmute');
          if (mute) { mute.textContent = a.muted ? 'Unmute' : 'Mute'; mute.dataset.muted = a.muted ? '1' : '0'; mute.disabled = !a.present; }
        });
      } catch (e) { /* next tick */ }
    }
  }
  let audioBusy = false;
  setInterval(function () {
    if (!visible() || audioBusy) return;
    audioBusy = true;
    pollAudio().finally(function () { audioBusy = false; });
  }, 300);

  // The preview: about once a second while this page is on screen, one picture at a time per computer.
  const loading = {};
  setInterval(function () {
    if (!visible()) return;
    document.querySelectorAll('#obsMachines img.obspreview').forEach(function (img) {
      const id = img.dataset.machine;
      if (loading[id]) return;
      loading[id] = true;
      const next = new Image();
      next.onload = next.onerror = function () {
        loading[id] = false;
        if (next.naturalWidth) {
          const prev = img.dataset.url;
          img.src = next.src; img.dataset.url = next.src;
          if (prev) setTimeout(function () { URL.revokeObjectURL(prev); }, 1000);
        } else { URL.revokeObjectURL(next.src); }
      };
      fetch('/api/admin/obs/preview/' + encodeURIComponent(id), { credentials: 'same-origin', cache: 'no-store' })
        .then(function (r) { return r.ok ? r.blob() : null; })
        .then(function (b) { if (b) next.src = URL.createObjectURL(b); else loading[id] = false; })
        .catch(function () { loading[id] = false; });
    });
  }, 1000);
  setInterval(function () { if (visible()) refresh(); }, 2000);
  refresh();
})();
