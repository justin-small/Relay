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

  let last = '';
  async function refresh() {
    try {
      const s = await send('GET', '/api/admin/aes67');
      $('aes67Now').textContent = now(s);
      const sig = JSON.stringify([s.streams.map(function (x) { return [x.id, x.live, x.problem]; }), s.selected, s.error]);
      if (sig !== last) { last = sig; draw(s); }
    } catch (e) { /* the page keeps what it shows */ }
  }
  refresh();
  setInterval(function () { if (relayUI.shown($('page-aes67'))) refresh(); }, 3000);
})();
