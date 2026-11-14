/* A /screen opened with ?alert=<name> (#121): flashes red and white (by level, #215: steady blue for info, amber for
   attention), once a second, over everything while an alert for that name is on, until someone taps, clicks or presses a key here, or the alert ends elsewhere. */
(function () {
  'use strict';

  const name = document.body.dataset.alert;
  if (!name) return;
  const overlay = document.getElementById('alertFlash');
  const fromEl = document.getElementById('alertFrom');
  const toEl = document.getElementById('alertTo');
  let current = null;   // the alert flashing now: {id, from, to}

  const LEVELS = { info: 1, attention: 1, urgent: 1 };
  // Info is steady blue, attention flashes amber, urgent flashes red and white (#215). An alert with none is urgent, a message info.
  function levelOf(msg, fallback) { return LEVELS[msg.level] ? msg.level : fallback; }

  function show(msg) {
    current = msg;
    overlay.dataset.level = levelOf(msg, 'urgent');
    fromEl.textContent = msg.from || 'Someone';
    toEl.textContent = 'needs ' + (msg.to || name) + ' · tap anywhere';
    overlay.hidden = false;
    overlay.focus();
  }

  function hide(id) {
    if (current && id && current.id !== id) return;   // an older alert's end
    current = null;
    overlay.hidden = true;
  }

  function ack() {
    if (!current) return;
    const id = current.id;
    hide(id);   // at once: the tap was the point; the stream's alert_end follows
    fetch('/api/alerts/ack', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id, screen: name }),
    }).catch(function () { /* it ends at the auto-stop anyway */ });
  }

  overlay.addEventListener('pointerdown', function (ev) { ev.preventDefault(); ack(); });
  document.addEventListener('keydown', function () { if (current) ack(); });

  let es = null;
  function connect() {
    if (es) es.close();
    es = new EventSource('/api/alerts/screen?name=' + encodeURIComponent(name));
    es.onmessage = function (ev) {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.type === 'alert') show(msg);
      else if (msg.type === 'alert_end') hide(msg.id);
      else if (msg.type === 'message') showMessage(msg);
      else if (msg.type === 'message_end') dropMessage(msg.id);
    };
    // EventSource reconnects by itself; the stream opens with any alert still on.
  }
  /* Stage messages (#214): words over the captions, no flash, nothing to tap. They end on their own timer, or when
     Relay says so. */
  const banner = document.getElementById('stageBanner');
  const card = document.getElementById('stageMessage');
  const cardFrom = document.getElementById('stageFrom');
  const cardText = document.getElementById('stageText');
  const live = {};   // id -> {banner, text, from, level, timer}

  function paintMessages() {
    let b = null;
    let m = null;
    Object.keys(live).forEach(function (id) {
      if (live[id].banner) b = live[id]; else m = live[id];
    });
    banner.hidden = !b;
    if (b) { banner.textContent = b.text; banner.dataset.level = b.level; }
    card.hidden = !m;
    if (m) {
      card.dataset.level = m.level;
      cardFrom.textContent = m.from ? 'From ' + m.from : '';
      cardText.textContent = m.text;
    }
  }

  function dropMessage(id) {
    if (!live[id]) return;
    clearTimeout(live[id].timer);
    delete live[id];
    paintMessages();
  }

  function showMessage(msg) {
    dropMessage(msg.id);
    live[msg.id] = {
      banner: !!msg.banner, text: String(msg.text || ''), from: String(msg.from || ''), level: levelOf(msg, 'info'),
      // The timer is a backstop if Relay's end never arrives.
      timer: setTimeout(function () { dropMessage(msg.id); }, ((msg.ends_in_s || 10) + 5) * 1000),
    };
    paintMessages();
  }

  connect();
})();
