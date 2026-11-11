/* A /screen opened with ?alert=<name> (#121): flashes red and white, once a second, over everything while an alert
   for that name is on, until someone taps, clicks or presses a key here, or the alert ends elsewhere. */
(function () {
  'use strict';

  const name = document.body.dataset.alert;
  if (!name) return;
  const overlay = document.getElementById('alertFlash');
  const fromEl = document.getElementById('alertFrom');
  const toEl = document.getElementById('alertTo');
  let current = null;   // the alert flashing now: {id, from, to}

  function show(msg) {
    current = msg;
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
    };
    // EventSource reconnects by itself; the stream opens with any alert still on.
  }
  connect();
})();
