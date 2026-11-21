/* The AES67 admin page (#266): the streams Relay hears announced, a button to use one as the sound input, and how the
   chosen one is arriving. Loaded by admin.html only while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-aes67')) return;
  const $ = R.$;
  const el = relayUI.el;
  const can = relayUI.can;
  const send = relayUI.client({ cache: 'no-store' });
  const showError = relayUI.note('aes67Err');

  function format(s) {
    return s.channels + ' ch, ' + (s.rate / 1000) + ' kHz, ' + s.encoding;
  }

  function channelPicker(s) {
    const sel = el('select');
    const mix = el('option', '', 'Mix of all');
    mix.value = 'mix';
    if (s.channels > 1) sel.appendChild(mix);
    for (let i = 0; i < s.channels; i++) {
      const o = el('option', '', 'Channel ' + (i + 1) + (s.labels[i] ? ': ' + s.labels[i] : ''));
      o.value = String(i);
      sel.appendChild(o);
    }
    return sel;
  }

  async function use(s, channel) {
    if (!(await relayUI.confirm('Use ' + s.name + ' as the sound input? A running session restarts.'))) return;
    showError('');
    try {
      await send('POST', '/api/admin/aes67/remember', { id: s.id });
      await send('POST', '/api/admin/config', {
        audio_source: 'aes67', audio_device: s.id, input_channel: channel === 'mix' ? 'mix' : Number(channel),
      });
    } catch (e) { showError(e.message); }
    last = ''; refresh();
  }

  function row(s, state) {
    const sel = channelPicker(s);
    const btn = el('button', '', state.selected === s.id ? 'In use' : 'Use as input');
    btn.type = 'button';
    btn.disabled = !!s.problem || state.selected === s.id || !can('aes67.manage') || !can('audio.manage');
    btn.onclick = function () { use(s, sel.value); };
    return el('tr', '',
      el('td', '', s.name, s.dante ? ' ' : '', s.dante ? relayUI.pill('Dante', 'idle') : ''),
      el('td', '', s.problem ? relayUI.pill(s.problem, 'warn') : format(s)),
      el('td', '', s.group + ':' + s.port),
      el('td', '', s.clock || 'not stated'),
      el('td', '', s.live ? 'announced' : 'not announced now'),
      el('td', '', s.problem ? '' : sel, ' ', btn));
  }

  function now(state) {
    const r = state.receiving;
    if (state.selected === null || state.selected === undefined) return 'No AES67 stream is the input.';
    if (!r) return 'The input is ' + state.selected + '. It is not running: start a session to receive it.';
    const drift = r.drift_ppm === null ? 'measuring' : r.drift_ppm + ' ppm';
    return (r.receiving ? 'Receiving ' : 'Silent: ') + state.selected + '. Packets ' + r.packets + ', lost ' + r.lost
      + ', late ' + r.late + ', drift ' + drift + '.';
  }

  function draw(s) {
    $('aes67Sum').textContent = s.error ? 'problem' : (s.streams.length ? s.streams.length + ' streams' : 'looking');
    $('aes67Status').textContent = s.error || (s.streams.length ? '' : 'No AES67 streams announced yet.');
    const rows = $('aes67Rows');
    rows.replaceChildren();
    s.streams.forEach(function (x) { rows.appendChild(row(x, s)); });
    $('aes67Now').textContent = now(s);
  }

  // ------------------------------------------------------------------------------------------ output (#267)
  const button = relayUI.buttons(relayUI.note('aes67OutErr'));
  let draft = null;
  let dirty = false;

  function langText(l) {
    return Object.keys(l).map(function (k) { return k + ':' + l[k]; }).join(', ');
  }
  function parseLangs(text) {
    const out = {};
    text.split(',').forEach(function (part) {
      const m = part.trim().match(/^([A-Za-z-]{1,12})\s*[:=]\s*(\d+)$/);
      if (m) out[m[1]] = Number(m[2]);
    });
    return out;
  }

  function outForm(o) {
    const host = $('aes67OutForm');
    if (dirty && host.firstChild) return;
    if (!dirty) draft = JSON.parse(JSON.stringify(o.settings));
    host.replaceChildren();
    const mark = function () { dirty = true; };
    const text = function (key, label, attrs, id) {
      const x = el('input', Object.assign({ id: id, value: String(draft[key]) }, attrs));
      x.oninput = function () { draft[key] = x.type === 'number' ? (parseInt(x.value, 10) || 0) : x.value; mark(); };
      host.appendChild(relayUI.field(label, x, id));
    };
    const on = el('input', { type: 'checkbox', checked: draft.enabled });
    on.onchange = function () { draft.enabled = on.checked; mark(); };
    host.appendChild(el('label', 'check', on, ' Send the AES67 output'));
    text('name', 'Stream name', { type: 'text', maxLength: 60 }, 'aes67OutName');
    text('group', 'Multicast address', { type: 'text', placeholder: '239.69.67.1' }, 'aes67OutGroup');
    text('port', 'Port', { type: 'number', min: '1024', max: '65535' }, 'aes67OutPort');
    const n = el('select', { id: 'aes67OutN' });
    for (let i = 1; i <= 8; i++) n.appendChild(el('option', { value: String(i), text: i + (i === 1 ? ' channel' : ' channels') }));
    n.value = String(draft.channels);
    n.onchange = function () { draft.channels = Number(n.value); mark(); dirty = true; host.replaceChildren(); outForm(o); };
    host.appendChild(relayUI.field('Channels', n, 'aes67OutN'));
    const enc = el('select', { id: 'aes67OutEnc' });
    [['L24', '24-bit (L24)'], ['L16', '16-bit (L16)']].forEach(function (e) { enc.appendChild(el('option', { value: e[0], text: e[1] })); });
    enc.value = draft.encoding;
    enc.onchange = function () { draft.encoding = enc.value; mark(); };
    host.appendChild(relayUI.field('Format', enc, 'aes67OutEnc'));
    const alertCh = el('select', { id: 'aes67OutAlert' });
    alertCh.appendChild(el('option', { value: '0', text: 'No alert tone' }));
    for (let i = 1; i <= draft.channels; i++) alertCh.appendChild(el('option', { value: String(i), text: 'Channel ' + i }));
    alertCh.value = String(Math.min(draft.alert_channel, draft.channels));
    alertCh.onchange = function () { draft.alert_channel = Number(alertCh.value); mark(); };
    host.appendChild(relayUI.field('Stage alert tone goes to', alertCh, 'aes67OutAlert'));
    const langs = el('input', { id: 'aes67OutLangs', type: 'text', value: langText(draft.languages), placeholder: 'es:2, fr:3' });
    langs.oninput = function () { draft.languages = parseLangs(langs.value); mark(); };
    host.appendChild(relayUI.field('Spoken languages, as code:channel', langs, 'aes67OutLangs'));
    const table = el('table');
    table.appendChild(el('thead', '', el('tr', '', el('th', '', 'Channel'), el('th', '', 'Name'), el('th', '', 'Gain (dB)'), el('th', '', 'Level'), el('th', '', ''))));
    const body = el('tbody');
    for (let i = 0; i < draft.channels; i++) {
      const label = el('input', { type: 'text', maxLength: 30, value: draft.labels[i] || '', 'aria-label': 'Name of channel ' + (i + 1) });
      label.oninput = function () { draft.labels[i] = label.value; mark(); };
      const gain = el('input', { type: 'number', min: '-60', max: '12', step: '0.5', value: String(draft.gain_db[i] || 0), 'aria-label': 'Gain of channel ' + (i + 1) });
      gain.oninput = function () { draft.gain_db[i] = parseFloat(gain.value) || 0; mark(); };
      const test = button('Test tone', async function () {
        await send('POST', '/api/admin/aes67/output/test', { channel: i + 1 });
      }, 'sm');
      test.disabled = !can('aes67.manage') || !o.running;
      body.appendChild(el('tr', '', el('td', '', String(i + 1)), el('td', '', label), el('td', '', gain),
        el('td', '', el('span', { id: 'aes67OutLvl' + i, text: '' })), el('td', '', test)));
    }
    table.appendChild(body);
    host.appendChild(el('div', 'tablescroll', table));
    const save = button('Save', async function () {
      const saved = await send('POST', '/api/admin/aes67/output', draft);
      dirty = false; draw0(saved);
    }, 'primary');
    save.disabled = !can('aes67.manage');
    host.appendChild(el('div', 'ctl', save));
  }

  function draw0(o) {
    outForm(o);
    $('aes67OutNow').textContent = o.error || (o.running
      ? 'Sending ' + o.settings.name + ' to ' + o.settings.group + ':' + o.settings.port + ' from ' + o.address
        + '. Packets ' + o.packets + '.'
      : (o.settings.enabled ? 'Not sending.' : 'Off.'));
    (o.levels || []).forEach(function (db, i) {
      const e = $('aes67OutLvl' + i);
      if (e) e.textContent = db === null ? '' : db + ' dBFS';
    });
  }

  let last = '';
  async function refresh() {
    try {
      const s = await send('GET', '/api/admin/aes67');
      $('aes67Now').textContent = now(s);
      draw0(s.output);
      const sig = JSON.stringify([s.streams.map(function (x) { return [x.id, x.live, x.problem]; }), s.selected, s.error]);
      if (sig !== last) { last = sig; draw(s); }
    } catch (e) { /* the page keeps what it shows */ }
  }
  refresh();
  setInterval(function () { if (relayUI.shown($('page-aes67'))) refresh(); }, 3000);
})();
