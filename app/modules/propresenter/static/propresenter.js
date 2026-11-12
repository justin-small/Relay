/* The ProPresenter settings page (a module, #138/#157): the connection, the lyrics and timers switches, slide-note
   tags and the lobby. Loaded by admin.html only while the module is on; uses the admin page's helpers through
   window.RelayAdmin, and tells the Panels page about ProPresenter's timers through RelayAdmin.panelHooks. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-propresenter')) return;
  const $ = R.$, el = R.el, api = R.api, post = R.post, okMsg = R.okMsg, alertMsg = R.alertMsg;
  const clockText = R.clockText, timerPicker = R.timerPicker, timerPickerValue = R.timerPickerValue,
    timerThresholds = R.timerThresholds;

  // A panel's timers form lists ProPresenter's timers and its global thresholds.
  R.panelHooks.timers = function () { return api('/api/admin/propresenter'); };

  /* ---- ProPresenter (#138) ----
     The connection fields are filled from the server once and then left alone while the operator types; the
     status below them is refreshed every couple of seconds while the page is open. */
  let ppSettings = null, ppFilled = false, ppTimer = null;

  function ppFill(s) {
    ppSettings = s;
    if (!ppFilled) {
      ppFilled = true;
      $('ppHost').value = s.host || '';
      $('ppPort').value = s.port || '';
      $('ppLibrary').value = s.songs_library || 'Songs';
      const tm = s.timers || { amber_s: 120, red_s: 30 };
      $('ppTmAmber').value = clockText(tm.amber_s);
      $('ppTmRed').value = clockText(tm.red_s);
    }
    [['ppEnabled', s.enabled], ['ppViewOnly', s.view_only], ['ppSongsOnly', s.songs_only],
      ['ppLyScreen', s.lyrics.screen], ['ppLyOverlay', s.lyrics.overlay], ['ppLyPanels', s.lyrics.panels],
      ['ppLyViewer', s.lyrics.viewer], ['ppTagsOn', (s.tags || {}).enabled]].forEach(function (p) {
      if (document.activeElement !== $(p[0])) $(p[0]).checked = !!p[1];
    });
    if (!ppTagsFilled) { ppTagsFilled = true; $('ppTagsSettle').value = (s.tags || {}).settle_s || 1.5; }
  }

  let ppTagsFilled = false;

  /* Slide-note tags: the slide that is up and what its notes say, what ran and when, and what Relay could not read. */
  function ppTagsRender(body) {
    const box = $('ppTagsSeen');
    box.innerHTML = '';
    const tg = body.tags || {}, on = !!(body.settings.tags || {}).enabled;
    if (!on) { box.appendChild(el('div', 'muted', 'Slide-note tags are off.')); return; }
    const seen = tg.seen;
    if (!seen) { box.appendChild(el('div', 'muted', 'Waiting for a slide. Tags are read once ProPresenter has one up.')); }
    else {
      const list = function (a) { return a && a.length ? a.join(', ') : 'none'; };
      box.appendChild(ppKv('Slide up', (seen.presentation || 'nothing') + (seen.slide ? ', slide ' + seen.slide : '')));
      box.appendChild(ppKv('Tags on the slide', list(seen.slide_tags)));
      box.appendChild(ppKv('Deck-wide tags', list(seen.deck_tags)));
      box.appendChild(ppKv('Runs for this slide', list(seen.resolved) + (seen.settling && seen.resolved.length ? ' (once it has settled)' : '')));
      (seen.warnings || []).forEach(function (w) { box.appendChild(ppKv('Warning', w, 'err')); });
    }
    const fired = tg.fired || [];
    box.appendChild(el('div', 'note', 'What ran'));
    if (!fired.length) box.appendChild(el('div', 'muted', 'Nothing yet.'));
    fired.slice(0, 8).forEach(function (r) {
      const when = new Date(r.at * 1000).toLocaleTimeString();
      box.appendChild(ppKv(when + '  ' + r.action + (r.value ? '=' + r.value : ''), r.result, r.ok ? '' : 'err'));
    });
  }

  /* Timer settings for panels (#138): thresholds as "m:ss" or seconds, and which timers. Used by the ProPresenter page
     (the global ones) and by a panel's card (its own). */
  let ppTimersKey = '';

  function ppTimersPick(body) {
    const list = body.state.timers || [];
    const key = JSON.stringify(list.map(function (t) { return [t.id && t.id.uuid, t.id && t.id.name]; }))
      + JSON.stringify((body.settings.timers || {}).show);
    const box = $('ppTmPick');
    if (key === ppTimersKey || box.contains(document.activeElement)) return;
    ppTimersKey = key;
    timerPicker(box, list, (body.settings.timers || {}).show || []);
  }

  function ppKv(label, value, cls) {
    const row = el('div', 'note' + (cls ? ' ' + cls : ''));
    row.style.marginTop = '0';
    row.appendChild(el('strong', '', label + ': '));
    row.appendChild(document.createTextNode(value));
    return row;
  }

  function ppRender(body) {
    ppFill(body.settings);
    ppTagsRender(body);
    ppTimersPick(body);
    const st = body.state;
    const failing = st.configured && !st.connected && !!st.error;
    $('ppSum').textContent = !st.configured ? '' : st.connected ? (st.view_only ? 'connected, view-only' : 'connected')
      : (failing ? 'not working' : 'connecting');
    $('ppState').textContent = !st.configured ? 'Off. Enter the address and port, then switch Follow ProPresenter on.'
      : st.connected ? 'Connected to ' + ((st.version && (st.version.host_description || st.version.name)) || 'ProPresenter')
        + (st.version && st.version.name ? ' on ' + st.version.name : '') + '.'
      : (st.error || 'Connecting...');
    $('ppState').className = 'note ' + (st.connected ? 'ok' : failing ? 'err' : '');
    const now = $('ppNow');
    now.innerHTML = '';
    if (!st.configured) { now.appendChild(el('div', 'muted', 'Not following ProPresenter.')); }
    else if (!st.connected) { now.appendChild(el('div', 'muted', 'Waiting for ProPresenter.')); }
    else {
      const p = st.presentation;
      now.appendChild(ppKv('Presentation', p ? p.name + (p.library ? ' (' + p.library + ')' : '') + (p.song ? ', a song' : '')
        : 'none on screen'));
      if (p) {
        now.appendChild(ppKv('Slide', (p.part ? p.part + ' · ' : '') + (st.slide_index === null ? '' : '#' + (st.slide_index + 1))));
        const cur = el('div', 'pcard');
        cur.appendChild(el('div', 'note', 'Current slide'));
        cur.appendChild(el('div', '', st.current.text || '(blank)')).style.whiteSpace = 'pre-line';
        cur.appendChild(el('div', 'note', 'Next slide'));
        const nx = cur.appendChild(el('div', 'muted', st.next.text || '(none)'));
        nx.style.whiteSpace = 'pre-line';
        now.appendChild(cur);
      }
      if (st.look) now.appendChild(ppKv('Look', st.look));
      if (st.layers) {
        const on = Object.keys(st.layers).filter(function (k) { return st.layers[k]; });
        now.appendChild(ppKv('Layers with content', on.length ? on.join(', ') : 'none'));
      }
    }
    $('ppStageNote').textContent = st.stage_message ? 'On the stage display now: ' + st.stage_message : '';
    $('ppStageClear').disabled = !st.stage_message;
    const blocked = st.view_only;
    $('ppStageSet').disabled = blocked || !st.connected;
    if (blocked) $('ppStageNote').textContent = 'View-only is on, so Relay will not change the stage display.';
  }

  async function ppPoll() {
    if ($('page-propresenter').hidden) return;
    try { ppRender(await api('/api/admin/propresenter')); } catch (e) { /* next tick */ }
  }
  window.addEventListener('hashchange', function () { ppPoll(); });
  ppTimer = setInterval(ppPoll, 2000);
  setTimeout(ppPoll, 400);

  async function ppSave(extra) {
    const body = await post('/api/admin/propresenter', Object.assign({
      host: $('ppHost').value.trim(), port: parseInt($('ppPort').value, 10) || 0, enabled: $('ppEnabled').checked
    }, extra || {}));
    ppRender(body);
    return body;
  }

  $('ppSave').addEventListener('click', async function () {
    try { await ppSave(); okMsg('Saved.'); } catch (e) { alertMsg(e.message); }
  });
  $('ppEnabled').addEventListener('change', async function () {
    try { await ppSave(); alertMsg(''); } catch (e) { alertMsg(e.message); $('ppEnabled').checked = false; }
  });
  $('ppViewOnly').addEventListener('change', async function () {
    try { ppRender(await post('/api/admin/propresenter', { view_only: $('ppViewOnly').checked })); alertMsg(''); }
    catch (e) { alertMsg(e.message); }
  });
  $('ppTest').addEventListener('click', async function () {
    try {
      const r = await post('/api/admin/propresenter/test', { host: $('ppHost').value.trim(), port: parseInt($('ppPort').value, 10) || 0 });
      okMsg('Reached ' + (r.version.host_description || 'ProPresenter') + ' on ' + (r.version.name || 'that machine')
        + ' (API ' + (r.version.api_version || '?') + ').');
    } catch (e) { alertMsg(e.message); }
  });
  $('ppFind').addEventListener('click', async function () {
    const btn = $('ppFind'), box = $('ppFound');
    btn.disabled = true; btn.textContent = 'Looking...';
    try {
      const r = await post('/api/admin/propresenter/discover');
      box.innerHTML = '';
      box.hidden = false;
      if (!r.found.length) box.appendChild(el('div', 'muted', 'Nothing answered. Check ProPresenter\'s Network setting is on, or type the address.'));
      r.found.forEach(function (f) {
        const b = el('button', '', f.name + ' · ' + (f.address || f.host) + ':' + f.port);
        b.type = 'button';
        b.addEventListener('click', function () {
          $('ppHost').value = f.address || f.host; $('ppPort').value = f.port; box.hidden = true;
        });
        box.appendChild(b);
      });
    } catch (e) { alertMsg(e.message); }
    btn.disabled = false; btn.textContent = 'Find on this network';
  });
  $('ppLyricsSave').addEventListener('click', async function () {
    try {
      ppRender(await post('/api/admin/propresenter', {
        songs_library: $('ppLibrary').value.trim() || 'Songs', songs_only: $('ppSongsOnly').checked,
        lyrics: { screen: $('ppLyScreen').checked, overlay: $('ppLyOverlay').checked,
                  panels: $('ppLyPanels').checked, viewer: $('ppLyViewer').checked }
      }));
      okMsg('Saved.');
    } catch (e) { alertMsg(e.message); }
  });
  $('ppTagsSave').addEventListener('click', async function () {
    const settle = parseFloat($('ppTagsSettle').value);
    if (!(settle >= 0.2 && settle <= 30)) { alertMsg('The settle time is a number of seconds from 0.2 to 30.'); return; }
    try {
      ppRender(await post('/api/admin/propresenter', { tags: { enabled: $('ppTagsOn').checked, settle_s: settle } }));
      okMsg('Saved.');
    } catch (e) { alertMsg(e.message); }
  });
  $('ppTimersSave').addEventListener('click', async function () {
    const th = timerThresholds($('ppTmAmber'), $('ppTmRed'));
    if (th.error) { alertMsg(th.error); return; }
    try {
      ppTimersKey = '';
      ppRender(await post('/api/admin/propresenter', {
        timers: { amber_s: th.amber_s, red_s: th.red_s, show: timerPickerValue($('ppTmPick')) }
      }));
      okMsg('Saved.');
    } catch (e) { alertMsg(e.message); }
  });
  $('ppStageSet').addEventListener('click', async function () {
    try {
      ppRender(await post('/api/admin/propresenter/stage', { text: $('ppStageText').value }));
      alertMsg('');
    } catch (e) { alertMsg(e.message); }
  });
  $('ppStageClear').addEventListener('click', async function () {
    try { ppRender(await api('/api/admin/propresenter/stage', { method: 'DELETE' })); alertMsg(''); }
    catch (e) { alertMsg(e.message); }
  });
})();
