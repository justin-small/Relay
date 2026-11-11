/* The /alert page (#121): one button per alert, from /stream?stream=alert, which carries every button's state
   whole in each message. A tap sends (or stops) the alert; the stream says what happened. */
(function () {
  'use strict';

  const NAME_KEY = 'relay.alert.from';
  const buttonsEl = document.getElementById('buttons');
  const emptyEl = document.getElementById('empty');
  const dot = document.getElementById('statusDot');
  const nameForm = document.getElementById('nameForm');
  const nameInput = document.getElementById('nameInput');
  const sendingAs = document.getElementById('sendingAs');
  const senderName = document.getElementById('senderName');
  const toastEl = document.getElementById('toast');

  let sender = '';
  try { sender = localStorage.getItem(NAME_KEY) || ''; } catch (e) { /* private window: ask each visit */ }
  let rows = [];
  let receivedAt = 0;   // when the last state arrived, so ages keep counting between messages

  try {
    if (localStorage.getItem('relay.theme') === 'light') document.documentElement.dataset.theme = 'light';
  } catch (e) { /* default dark */ }

  function showName() {
    nameForm.hidden = !!sender;
    sendingAs.hidden = !sender;
    senderName.textContent = sender;
    if (!sender) nameInput.focus();
  }

  nameForm.addEventListener('submit', function (ev) {
    ev.preventDefault();
    const v = nameInput.value.replace(/\s+/g, ' ').trim().slice(0, 32);
    if (!v) return;
    sender = v;
    try { localStorage.setItem(NAME_KEY, v); } catch (e) { /* kept for this visit */ }
    showName();
  });
  document.getElementById('changeName').addEventListener('click', function () {
    nameInput.value = sender;
    sender = '';
    showName();
  });

  let toastTimer = 0;
  function toast(text) {
    toastEl.textContent = text;
    toastEl.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove('show'); }, 3000);
  }

  function ago(s) {
    if (s < 10) return 'just now';
    if (s < 60) return Math.round(s) + ' s ago';
    return Math.round(s / 60) + ' min ago';
  }

  function status(row) {
    const age = (row.age_s || 0) + (Date.now() - receivedAt) / 1000;
    switch (row.state) {
      case 'on': return { cls: 'on', text: 'Flashing' + (row.from ? ' · from ' + row.from : '') + ' · tap to stop' };
      case 'seen': return { cls: 'seen', text: 'Seen on ' + (row.seen_on || 'a screen') + ' · ' + ago(age) };
      case 'missed': return { cls: 'missed', text: 'Not seen · ' + ago(age) };
      case 'cancelled': return { cls: 'cancelled', text: 'Stopped · ' + ago(age) };
      default: return null;
    }
  }

  function render() {
    emptyEl.hidden = rows.length > 0;
    // Rebuilt whole: at most 16 buttons, and the state arrives whole.
    buttonsEl.textContent = '';
    rows.forEach(function (row) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'alert-btn' + (row.state === 'on' ? ' flashing' : '');
      b.dataset.id = row.id;
      const name = document.createElement('span');
      name.className = 'alert-btn-name';
      name.textContent = row.name;
      b.appendChild(name);
      const st = status(row);
      if (st) {
        const tag = document.createElement('span');
        tag.className = 'alert-btn-state ' + st.cls;
        tag.textContent = st.text;
        b.appendChild(tag);
      }
      b.setAttribute('aria-pressed', row.state === 'on' ? 'true' : 'false');
      b.addEventListener('click', function () { press(row); });
      buttonsEl.appendChild(b);
    });
  }

  function press(row) {
    if (!sender && row.state !== 'on') {
      showName();
      toast('Say who you are first');
      return;
    }
    fetch('/api/alerts/' + encodeURIComponent(row.id) + '/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from: sender }),
    }).then(function (r) {
      return r.json().then(function (body) {
        if (!r.ok) throw new Error(body.error || body.detail || 'Relay refused it');
        if (body.buttons) { rows = body.buttons; receivedAt = Date.now(); render(); }
      });
    }).catch(function (err) {
      toast(err && err.message ? err.message : "Couldn't reach Relay");
    });
  }

  let es = null;
  function connect() {
    if (es) es.close();
    es = new EventSource('/stream?stream=alert');
    es.onopen = function () { dot.className = 'dot ok'; };
    es.onmessage = function (ev) {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.type !== 'alerts') return;
      rows = msg.buttons || [];
      receivedAt = Date.now();
      render();
    };
    es.onerror = function () { dot.className = 'dot err'; };
  }

  // Ages tick on between messages.
  setInterval(function () { if (rows.some(function (r) { return r.state !== 'idle'; })) render(); }, 10000);

  showName();
  connect();
})();
