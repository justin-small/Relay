/* The status screen (#224): reads /stream?stream=status (app/streamstatus.py). Each message is the whole state;
   elapsed times are counted here from `server_time`, so they move between messages. */
(function () {
  'use strict';
  const $ = function (id) { return document.getElementById(id); };
  let state = null;
  let skew = 0;          // Relay's clock minus this browser's, in seconds
  let lastMsg = 0;
  const STALE_MS = 20000; // a message comes at least every 5 s

  function elapsed(since) {
    if (since === null || since === undefined) return '';
    let s = Math.max(0, Math.floor(Date.now() / 1000 + skew - since));
    const h = Math.floor(s / 3600); s -= h * 3600;
    const m = Math.floor(s / 60); s -= m * 60;
    const two = function (n) { return (n < 10 ? '0' : '') + n; };
    return h ? h + ':' + two(m) + ':' + two(s) : m + ':' + two(s);
  }

  function setCard(card, tone) {
    card.className = 'st-card' + (card.id === 'airCard' ? ' st-air' : '') + (tone ? ' st-' + tone : '');
  }

  const INPUT = {
    off: ['Off', ''], lost: ['Lost', 'err'], clipping: ['Clipping', 'err'], silent: ['Silent', 'warn'], ok: ['OK', 'ok'],
  };
  const HEALTH = { ok: 'healthy', warn: 'needs a look', error: 'trouble', idle: 'not connected' };

  function paint() {
    if (!state) return;
    const st = state;
    // Livestream
    const streams = st.streams || [];
    if (!streams.length) {
      $('airText').textContent = 'Not watched';
      $('airSub').textContent = 'No livestream module is on';
      setCard($('airCard'), '');
    } else if (st.on_air === true) {
      const live = streams.find(function (e) { return e.on_air; });
      $('airText').textContent = 'On air';
      $('airSub').textContent = live && live.live_since ? elapsed(live.live_since) : '';
      // On air is always red; a source that needs a look says so on its own row.
      setCard($('airCard'), 'live');
    } else {
      $('airText').textContent = st.on_air === false ? 'Off air' : 'Unknown';
      $('airSub').textContent = '';
      setCard($('airCard'), streams.some(function (e) { return e.health === 'error'; }) ? 'err' : '');
    }
    const list = $('streams');
    list.replaceChildren();
    streams.forEach(function (e) {
      const li = document.createElement('li');
      li.className = 'st-stream st-h-' + e.health;
      const name = document.createElement('span');
      name.textContent = e.label;
      const what = document.createElement('span');
      const bits = [e.on_air === true ? 'live ' + elapsed(e.live_since) : e.on_air === false ? 'off air' : '—',
                    HEALTH[e.health] || e.health];
      if (e.viewers !== null && e.viewers !== undefined) bits.push(e.viewers + ' watching');
      what.textContent = bits.join(' · ');
      li.appendChild(name); li.appendChild(what);
      list.appendChild(li);
    });
    // Captions
    if (st.running) {
      $('capText').textContent = 'Running';
      $('capSub').textContent = (st.started_at ? elapsed(st.started_at) + ' · ' : '') +
        (st.languages.length ? st.languages.join(', ') : 'no language live');
      setCard($('capCard'), st.languages.length ? 'ok' : 'warn');
    } else {
      $('capText').textContent = 'Stopped';
      $('capSub').textContent = '';
      setCard($('capCard'), '');
    }
    // Input
    const inp = INPUT[st.input.state] || ['—', ''];
    $('inText').textContent = inp[0];
    $('inSub').textContent = st.input.state === 'off' ? '' : st.input.dbfs + ' dB';
    $('inFill').style.width = (st.input.state === 'off' ? 0 : Math.max(0, Math.min(100, (st.input.dbfs + 60) / 60 * 100))) + '%';
    setCard($('inCard'), inp[1]);
    // Viewers
    $('viewText').textContent = String(st.viewers);
  }

  function connected(ok) {
    $('connDot').className = 'dot ' + (ok ? 'ok' : 'err');
    $('connText').textContent = ok ? 'Live from Relay' : 'Not hearing from Relay';
  }

  function open() {
    const es = new EventSource('/stream?stream=status');
    es.onmessage = function (ev) {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (!msg || msg.type !== 'status') return;
      state = msg;
      skew = msg.server_time - Date.now() / 1000;
      lastMsg = Date.now();
      connected(true);
      paint();
    };
    es.onerror = function () { connected(false); };
  }

  setInterval(function () {
    if (lastMsg && Date.now() - lastMsg > STALE_MS) connected(false);
    paint();
  }, 1000);
  open();
})();
