/* The Intercom tab of the crew page (#240): talk and listen on the channels the signed-in user is on.

   The phone never decides who hears what. It signs in to /intercom/ws with a device credential, asks for a LiveKit
   token per channel it joins, and asks the server for a talk lease before its microphone is let through: the server
   unmutes the phone's armed track on a grant, and this page also keeps the microphone track disabled except while a
   lease is held. Names and text from the server are set as text, never as HTML. The plain helpers at the top have no
   browser in them and are tested on their own (tests/js/intercom_crew.test.mjs). */
(function (root) {
  'use strict';

  // ------------------------------------------------------------------ plain helpers
  const BACKOFF_S = [1, 2, 4, 8];
  function backoff(n) { return BACKOFF_S[Math.min(n, BACKOFF_S.length - 1)]; }

  function activityText(a) {
    const names = ((a && a.talkers) || []).map(function (t) { return t.name; });
    if (names.length) return names.join(', ') + ' talking';
    if (a && typeof a.listening === 'number') return 'Quiet · ' + a.listening + ' listening';
    return 'Quiet';
  }

  // What one channel's answer to a talk request looks like on screen.
  function resultText(r) {
    if (!r) return '';
    switch (r.state) {
      case 'talking': return 'Talking';
      case 'waiting': return "Waiting · you're " + ordinal(r.position) + ' in line';
      case 'cut': return 'Cut off' + (r.by ? ' · ' + r.by + ' has priority' : '');
      case 'denied': return DENIED[r.reason] || 'Not allowed';
      case 'ended': return ENDED[r.reason] || 'Stopped';
      default: return '';
    }
  }
  const DENIED = { busy: 'Busy · someone with priority is talking', full: 'Channel is full', muted: 'Muted by an operator',
    not_allowed: 'Not allowed to talk here' };
  const ENDED = { expired: 'Lost the link', latch_limit: 'Latch limit reached', muted: 'Muted by an operator',
    rights: 'Your talk right was removed', replaced: 'Talking from another phone', released_by_operator: 'Released by an operator',
    channel_removed: 'Channel removed' };
  function ordinal(n) {
    const v = n % 100;
    return n + (['th', 'st', 'nd', 'rd'][(v - 20) % 10] || ['th', 'st', 'nd', 'rd'][v] || 'th');
  }

  // The state of one press across channels: which channels hold a lease, are waiting, or were refused.
  function applyTalkMessage(results, m) {
    const ch = m.channel;
    switch (m.type) {
      case 'talk.granted': results[ch] = { state: 'talking' }; break;
      case 'talk.queued': results[ch] = { state: 'waiting', position: m.position }; break;
      case 'talk.denied': results[ch] = { state: 'denied', reason: m.reason }; break;
      case 'talk.preempted': results[ch] = { state: 'cut', by: m.by }; break;
      case 'talk.released': results[ch] = { state: 'ended', reason: m.reason }; break;
      default: return false;
    }
    return true;
  }

  function holding(results) {
    return Object.keys(results).some(function (c) { return results[c].state === 'talking'; });
  }
  function busy(results) {
    return Object.keys(results).some(function (c) { return results[c].state === 'talking' || results[c].state === 'waiting'; });
  }

  // Which channels the phone must be in a room for: listening, or talking only (subscribe off on the server side).
  function wantedRooms(channels) {
    return channels.filter(function (c) {
      return (c.can_listen && c.listen_selected) || (c.can_talk && c.talk_selected);
    }).map(function (c) { return c.id; });
  }
  function talkTargets(channels) {
    return channels.filter(function (c) { return c.can_talk && c.talk_selected; }).map(function (c) { return c.id; });
  }

  function guessName(ua) {
    ua = ua || '';
    const dev = /iPhone/.test(ua) ? 'iPhone' : /iPad/.test(ua) ? 'iPad' : /Android/.test(ua) ? 'Android phone' :
      /Mac/.test(ua) ? 'Mac' : /Windows/.test(ua) ? 'Windows PC' : 'Phone';
    const br = /Edg\//.test(ua) ? 'Edge' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' :
      /Firefox\//.test(ua) ? 'Firefox' : 'browser';
    return dev + ' ' + br;
  }

  const HEADSET = /(airpods|headset|headphone|bluetooth|beats|bose|jabra|buds|earbuds|wired)/i;
  function isHeadset(label) { return HEADSET.test(label || ''); }

  const logic = { backoff: backoff, activityText: activityText, resultText: resultText, applyTalkMessage: applyTalkMessage,
    holding: holding, busy: busy, wantedRooms: wantedRooms, talkTargets: talkTargets, guessName: guessName,
    isHeadset: isHeadset, ordinal: ordinal };
  if (typeof module !== 'undefined' && module.exports) { module.exports = logic; return; }
  root.RelayIntercomLogic = logic;

  // ------------------------------------------------------------------ the page
  const doc = root.document;
  const $ = function (id) { return doc.getElementById(id); };
  const LK = root.LivekitClient;
  const box = $('ic');
  if (!box) return;
  if (!LK || !root.WebSocket || !(root.navigator.mediaDevices && root.navigator.mediaDevices.getUserMedia)) {
    $('icUnsupported').hidden = false;
    return;
  }
  const KEY = 'relay-intercom-credential';
  const secure = root.location.protocol === 'https:';
  const WS_URL = (secure ? 'wss://' : 'ws://') + root.location.host + '/intercom/ws';
  const RTC_URL = (secure ? 'wss://' : 'ws://') + root.location.host + '/intercom';   // livekit-client adds /rtc

  const st = {
    ws: null, online: false, tries: 0, retry: null, rtt: null, pingAt: 0, channels: [], production: '', activity: {},
    rooms: new Map(),                 // channel id -> { room, joining, pub: LocalAudioTrack|null }
    results: {}, seq: 0, latched: false, pressed: false, beat: null, mic: null, muteAll: false, wake: null,
    lastCut: ''
  };

  function store() { return $('icKeep').checked ? root.localStorage : root.sessionStorage; }
  function getCred() {
    try { return root.localStorage.getItem(KEY) || root.sessionStorage.getItem(KEY) || ''; } catch (e) { return ''; }
  }
  function setCred(v) {
    try {
      root.localStorage.removeItem(KEY); root.sessionStorage.removeItem(KEY);
      if (v) store().setItem(KEY, v);
    } catch (e) { /* private browsing: the credential then lasts only until the next page load */ }
  }
  function announce(text) { relayUI.say($('icLive'), text, false, { plain: true }); }

  // ---- credential: made once from the signed-in crew session
  async function ensureCredential() {
    if (getCred()) return getCred();
    const res = await fetch('/intercom/api/phones', {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: guessName(root.navigator.userAgent), platform: /iPhone|iPad/.test(root.navigator.userAgent) ? 'ios' :
        /Android/.test(root.navigator.userAgent) ? 'android' : 'web' })
    });
    if (res.status === 401) { root.location.reload(); throw new Error('signed out'); }
    if (!res.ok) throw new Error('could not sign this phone in');
    setCred((await res.json()).credential);
    return getCred();
  }

  // ---- the socket
  function send(m) { if (st.ws && st.ws.readyState === 1) st.ws.send(JSON.stringify(m)); }

  async function connect() {
    clearTimeout(st.retry);
    let cred;
    try { cred = await ensureCredential(); } catch (e) { return later(); }
    const ws = new root.WebSocket(WS_URL);
    st.ws = ws;
    ws.onopen = function () { ws.send(JSON.stringify({ type: 'hello', credential: cred })); };
    ws.onmessage = function (ev) { let m; try { m = JSON.parse(ev.data); } catch (e) { return; } onMessage(m); };
    ws.onclose = function (ev) {
      if (st.ws !== ws) return;
      st.ws = null;
      setOnline(false);
      if (ev.code === 4401 || ev.code === 4403) setCred('');      // signed out or revoked: make a new credential
      later();
    };
  }
  function later() {
    const s = backoff(st.tries++);
    let left = s;
    $('icDown').hidden = false;
    (function tick() {
      $('icDown').textContent = 'Reconnecting in ' + left + ' s';
      if (left-- > 0) { st.retry = root.setTimeout(tick, 1000); } else { connect(); }
    })();
  }

  function setOnline(on) {
    st.online = on;
    box.classList.toggle('offline', !on);
    $('icLocal').textContent = on ? 'Local: connected' + (st.rtt === null ? '' : ' · ' + st.rtt + ' ms') : 'Local: offline';
    $('icLocal').className = 'chip ' + (on ? 'ok' : 'bad');
    if (!on) {
      st.results = {}; st.pressed = false; st.latched = false; stopBeat(); muteLocal();
      $('icDown').hidden = false;
      announce('Intercom disconnected');
    } else {
      $('icDown').hidden = true;
      announce('Intercom connected');
    }
    render();
  }

  function onMessage(m) {
    switch (m.type) {
      case 'welcome':
        st.tries = 0;
        if (m.credential) setCred(m.credential);
        st.channels = m.channels || []; st.production = m.production || ''; st.activity = m.activity || {};
        setOnline(true); startPing(); syncRooms(); render();
        break;
      case 'config':
        st.channels = m.channels || []; st.production = m.production || '';
        syncRooms(); render();
        break;
      case 'activity': st.activity[m.channel] = m; render(); break;
      case 'pong': st.rtt = Math.round(root.performance.now() - st.pingAt); setOnline(true); break;
      case 'token': joinWith(m); break;
      case 'error': if (m.for === 'talk') announce(m.text); break;
      case 'talk.warning': {
        const c = chan(m.channel);
        $('icLatchWarn').textContent = 'Latch ends in ' + m.seconds_left + ' s' + (c ? ' on ' + c.name : '') + '.';
        $('icLatchWarn').hidden = false; announce($('icLatchWarn').textContent);
        break;
      }
      default:
        if (applyTalkMessage(st.results, m)) onTalk(m);
    }
  }

  function onTalk(m) {
    const ch = chan(m.channel);
    const name = ch ? ch.name : 'a channel';
    if (m.type === 'talk.granted') setTrack(m.channel, true);
    else setTrack(m.channel, false);
    if (m.type === 'talk.preempted') {
      st.lastCut = 'Cut off on ' + name + '. ' + m.by + ' has priority.';
      $('icCut').textContent = st.lastCut; $('icCut').hidden = false; announce(st.lastCut);
    }
    if (m.type === 'talk.released' && m.reason === 'latch_limit') { st.latched = false; }
    if (!busy(st.results) && st.pressed === false) stopBeat();
    render();
  }
  root.addEventListener('beforeunload', function () { send({ type: 'talk.release' }); });

  function startPing() {
    clearInterval(st.pinger);
    st.pinger = root.setInterval(function () { st.pingAt = root.performance.now(); send({ type: 'ping' }); }, 25000);
    st.pingAt = root.performance.now(); send({ type: 'ping' });
  }

  // ---- rooms
  function chan(id) { return st.channels.find(function (c) { return c.id === id; }); }

  function syncRooms() {
    const want = wantedRooms(st.channels);
    want.forEach(function (id) { if (!st.rooms.has(id)) join(id); else publishIfArmed(id); });
    Array.from(st.rooms.keys()).forEach(function (id) { if (want.indexOf(id) === -1) leave(id); });
    applyVolumes();
  }
  function join(id) {
    st.rooms.set(id, { room: null, joining: true, pub: null });
    send({ type: 'token', channel: id });
  }
  async function joinWith(m) {
    const slot = st.rooms.get(m.channel);
    if (!slot) return;
    const room = new LK.Room({ adaptiveStream: false, dynacast: false });
    slot.room = room;
    room.on(LK.RoomEvent.TrackSubscribed, function (track) {
      if (track.kind !== 'audio') return;
      const el = track.attach();
      el.dataset.channel = m.channel; el.style.display = 'none';
      doc.body.appendChild(el);
      applyVolumes();
    });
    room.on(LK.RoomEvent.TrackUnsubscribed, function (track) { track.detach().forEach(function (e) { e.remove(); }); });
    room.on(LK.RoomEvent.Disconnected, function () {
      if (st.rooms.get(m.channel) === slot) {
        st.rooms.delete(m.channel);
        if (st.online) root.setTimeout(syncRooms, 1000 * backoff(st.tries++));
      }
    });
    try {
      await room.connect(RTC_URL, m.token, { autoSubscribe: true });
      slot.joining = false;
      applyVolumes();
      publishIfArmed(m.channel);
    } catch (e) {
      st.rooms.delete(m.channel);
      if (st.online) root.setTimeout(syncRooms, 1000 * backoff(st.tries++));
    }
  }
  function leave(id) {
    const slot = st.rooms.get(id);
    st.rooms.delete(id);
    if (slot && slot.room) slot.room.disconnect();
    doc.querySelectorAll('audio[data-channel="' + id + '"]').forEach(function (e) { e.remove(); });
  }

  // ---- the microphone: captured once, a clone published to each channel that has Talk selected, disabled between presses
  async function ensureMic() {
    if (st.mic && st.mic.readyState === 'live') return st.mic;
    try {
      const stream = await root.navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
      st.mic = stream.getAudioTracks()[0];
      st.mic.enabled = false;
      st.mic.onended = function () { st.mic = null; };
      $('icMic').hidden = true;
      routeCheck();
      return st.mic;
    } catch (e) {
      $('icMic').textContent = 'The microphone is blocked. Allow it for this site in the browser settings, then try again.';
      $('icMic').hidden = false;
      return null;
    }
  }
  async function publishIfArmed(id) {
    const slot = st.rooms.get(id), ch = chan(id);
    if (!slot || !slot.room || slot.joining || slot.pub || !(ch && ch.can_talk && ch.talk_selected)) return;
    const mic = await ensureMic();
    if (!mic || slot.pub || !st.rooms.has(id)) return;
    const clone = mic.clone();
    clone.enabled = !!(st.results[id] && st.results[id].state === 'talking');
    const track = new LK.LocalAudioTrack(clone, undefined, true);
    slot.pub = track;
    try {
      await slot.room.localParticipant.publishTrack(track, { dtx: true, red: false, source: LK.Track.Source.Microphone,
        name: 'mic', audioPreset: { maxBitrate: 24000 } });
    } catch (e) { slot.pub = null; }
  }
  function setTrack(id, on) {
    const slot = st.rooms.get(id);
    if (slot && slot.pub) slot.pub.mediaStreamTrack.enabled = !!on;
  }
  function muteLocal() { st.rooms.forEach(function (_s, id) { setTrack(id, false); }); }

  function applyVolumes() {
    doc.querySelectorAll('audio[data-channel]').forEach(function (el) {
      const c = chan(Number(el.dataset.channel));
      el.volume = st.muteAll || !c ? 0 : Math.max(0, Math.min(1, c.volume / 100));
    });
  }

  // ---- pressing to talk
  async function press(latched) {
    if (!st.online || st.pressed) return;
    const targets = talkTargets(st.channels);
    if (!targets.length) return;
    st.pressed = true; st.latched = !!latched; st.results = {}; $('icCut').hidden = true;
    st.rooms.forEach(function (s) { if (s.room && s.room.startAudio) s.room.startAudio().catch(function () {}); });
    render();
    if (!(await ensureMic())) { st.pressed = false; st.latched = false; render(); return; }
    await Promise.all(targets.map(publishIfArmed));
    if (!st.pressed) return;               // let go while the microphone was starting
    send({ type: 'talk.request', channels: targets, seq: ++st.seq, latched: !!latched });
    startBeat();
    render();
  }
  function release() {
    if (!st.pressed) return;
    st.pressed = false; st.latched = false;
    send({ type: 'talk.release' });
    muteLocal(); stopBeat();
    $('icLatchWarn').hidden = true;
    root.setTimeout(function () { if (!st.pressed) { st.results = {}; render(); } }, 1500);
    render();
  }
  function startBeat() { stopBeat(); st.beat = root.setInterval(function () { send({ type: 'talk.beat' }); }, 2000); }
  function stopBeat() { clearInterval(st.beat); st.beat = null; }

  // ---- drawing
  const el = relayUI.el;
  function choose(c, patch) {
    Object.assign(c, patch);
    send({ type: 'choice', channel: c.id, talk_selected: !!c.talk_selected, listen_selected: !!c.listen_selected, volume: c.volume });
    syncRooms(); render();
  }

  function render() {
    const up = st.online;
    box.hidden = false;
    $('icProd').textContent = st.production || 'Intercom';
    $('icProd').hidden = false;
    $('icNoChannels').hidden = !(up && !st.channels.length);
    const list = $('icChannels');
    list.textContent = '';
    st.channels.forEach(function (c) {
      const card = el('div', { className: 'ch' + (up ? '' : ' off') });
      card.style.borderLeftColor = /^#[0-9a-fA-F]{3,8}$/.test(c.colour) ? c.colour : 'var(--line)';
      card.appendChild(el('h3', { text: c.name }));
      const res = st.results[c.id];
      card.appendChild(el('div', { className: 'act', text: activityText(st.activity[c.id]) }));
      const row = el('div', { className: 'row' });
      const talk = el('button', { type: 'button', className: 'tog', text: 'Talk', disabled: !up || !c.can_talk });
      talk.setAttribute('aria-pressed', c.talk_selected ? 'true' : 'false');
      talk.addEventListener('click', function () { ensureMic(); choose(c, { talk_selected: c.talk_selected ? 0 : 1 }); });
      const listen = el('button', { type: 'button', className: 'tog', text: 'Listen', disabled: !up || !c.can_listen });
      listen.setAttribute('aria-pressed', c.listen_selected ? 'true' : 'false');
      listen.addEventListener('click', function () { choose(c, { listen_selected: c.listen_selected ? 0 : 1 }); });
      const vol = el('input', { type: 'range', min: 0, max: 100, value: c.volume, disabled: !c.can_listen });
      vol.setAttribute('aria-label', c.name + ' listen volume');
      vol.addEventListener('input', function () { c.volume = Number(vol.value); applyVolumes(); });
      vol.addEventListener('change', function () { choose(c, { volume: Number(vol.value) }); });
      row.appendChild(talk); row.appendChild(listen); row.appendChild(vol);
      if (res) {
        const t = el('span', { className: 'res ' + res.state, text: resultText(res) });
        row.appendChild(t);
      }
      card.appendChild(row);
      list.appendChild(card);
    });
    const targets = talkTargets(st.channels);
    const names = st.channels.filter(function (c) { return targets.indexOf(c.id) !== -1; }).map(function (c) { return c.name; });
    $('icTo').textContent = names.length ? 'Talks to: ' + names.join(', ') : 'Pick Talk on a channel to use the button.';
    const talkBtn = $('icTalk');
    talkBtn.disabled = !up || !targets.length;
    talkBtn.setAttribute('aria-pressed', st.pressed ? 'true' : 'false');
    talkBtn.textContent = st.pressed ? (holding(st.results) ? 'Talking' + (st.latched ? ' (latched)' : '') :
      busy(st.results) ? 'Waiting' : 'Talking…') : 'Hold to talk';
    const latch = $('icLatch');
    latch.disabled = !up || !targets.length;
    latch.setAttribute('aria-pressed', st.latched ? 'true' : 'false');
    $('icMuteAll').setAttribute('aria-pressed', st.muteAll ? 'true' : 'false');
    const states = Object.keys(st.results).map(function (id) { const c = chan(Number(id)); return (c ? c.name + ': ' : '') + resultText(st.results[id]); });
    if (states.length) announce(states.join('. '));
  }

  $('icTalk').addEventListener('pointerdown', function (e) { e.preventDefault(); press(false); });
  ['pointerup', 'pointercancel', 'pointerleave'].forEach(function (n) { $('icTalk').addEventListener(n, release); });
  $('icTalk').addEventListener('contextmenu', function (e) { e.preventDefault(); });
  $('icTalk').addEventListener('keydown', function (e) { if (e.key === 'Enter' && !e.repeat) { press(false); } });
  $('icTalk').addEventListener('keyup', function (e) { if (e.key === 'Enter') release(); });
  $('icLatch').addEventListener('click', function () { if (st.latched) release(); else press(true); });
  $('icMuteAll').addEventListener('click', function () { st.muteAll = !st.muteAll; applyVolumes(); render(); });
  $('icKeep').addEventListener('change', function () { const c = getCred(); if (c) setCred(c); });

  // The space bar on a laptop, while this tab is showing and no field has focus.
  function tabShowing() { return !box.closest('[role=tabpanel]').hidden; }
  doc.addEventListener('keydown', function (e) {
    if (e.code !== 'Space' || e.repeat || !tabShowing() || /^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test((doc.activeElement || {}).tagName)) return;
    e.preventDefault(); press(false);
  });
  doc.addEventListener('keyup', function (e) { if (e.code === 'Space' && st.pressed && !st.latched) release(); });

  // A headset's play/pause button toggles Latch where the browser lets a page see it.
  if ('mediaSession' in root.navigator) {
    ['play', 'pause'].forEach(function (a) {
      try { root.navigator.mediaSession.setActionHandler(a, function () { if (st.latched) release(); else press(true); }); } catch (e) { /* not offered */ }
    });
  }

  // ---- the audio route: a headset name, and a warning when the microphone is open on the loudspeaker
  async function routeCheck() {
    try {
      const label = st.mic ? st.mic.label : '';
      const chip = $('icHeadset');
      chip.hidden = !isHeadset(label);
      chip.textContent = label;
      const loud = !!st.mic && !isHeadset(label);
      $('icRoute').hidden = !loud;
      $('icRoute').textContent = loud ? "Using the phone's own microphone and speaker. Use a headset to avoid feedback." : '';
    } catch (e) { /* labels are not always readable */ }
  }
  if (root.navigator.mediaDevices.addEventListener) root.navigator.mediaDevices.addEventListener('devicechange', routeCheck);

  // ---- keep the screen on while this tab is showing; the browser releases the lock when it is hidden
  async function wake() {
    if (!('wakeLock' in root.navigator) || doc.hidden || !tabShowing()) return;
    try { st.wake = await root.navigator.wakeLock.request('screen'); } catch (e) { /* low battery or not allowed */ }
  }
  doc.addEventListener('visibilitychange', wake);
  doc.addEventListener('crew:tab', wake);
  if (/iPhone|iPad/.test(root.navigator.userAgent)) $('icIos').hidden = false;

  // ---- receive statistics for the watchdog (#276), every 5 s while connected
  root.setInterval(async function () {
    if (!st.online) return;
    let lost = 0, received = 0, jitter = 0, n = 0;
    for (const slot of st.rooms.values()) {
      if (!slot.room) continue;
      for (const p of slot.room.remoteParticipants.values()) {
        for (const pub of p.audioTrackPublications.values()) {
          if (!pub.track || !pub.track.getReceiverStats) continue;
          const s = await pub.track.getReceiverStats().catch(function () { return null; });
          if (s) { lost += s.packetsLost || 0; received += s.packetsReceived || 0; jitter = Math.max(jitter, Math.round((s.jitter || 0) * 1000)); n++; }
        }
      }
    }
    send({ type: 'stats', packets_lost: lost, packets_received: received, jitter_ms: jitter, rtt_ms: st.rtt, tracks: n });
  }, 5000);

  // ---- my phones (#273): every phone signed in as me, each with a sign-out. Text from the server is set as text.
  async function myPhones() {
    const list = $('icMyList');
    let phones;
    try {
      const res = await fetch('/intercom/api/my-phones', { credentials: 'same-origin' });
      if (!res.ok) return;
      phones = (await res.json()).phones;
    } catch (e) { return; }
    list.textContent = '';
    phones.forEach(function (p) {
      const li = doc.createElement('li');
      li.className = 'lrow tight';
      const label = doc.createElement('span');
      label.textContent = p.name + (p.last_seen ? ' · last seen ' + p.last_seen.slice(0, 10) : '');
      const out = doc.createElement('button');
      out.type = 'button';
      out.textContent = 'Sign out';
      out.addEventListener('click', async function () {
        out.disabled = true;
        try { await fetch('/intercom/api/my-phones/' + p.id, { method: 'DELETE', credentials: 'same-origin' }); } catch (e) { /* listed again below */ }
        myPhones();
      });
      li.appendChild(label);
      li.appendChild(out);
      list.appendChild(li);
    });
    $('icMyEmpty').hidden = !!phones.length;
  }
  $('icMine').addEventListener('toggle', function () { if ($('icMine').open) myPhones(); });

  connect();
  wake();
})(typeof window !== 'undefined' ? window : globalThis);
