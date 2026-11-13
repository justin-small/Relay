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

  // With sign-in required (#175) the server names the sender by its account, and there is no name form.
  const account = document.body.dataset.account || '';
  let sender = account;
  if (!account) {
    try { sender = localStorage.getItem(NAME_KEY) || ''; } catch (e) { /* private window: ask each visit */ }
  }
  let rows = [];
  let receivedAt = 0;   // when the last state arrived, so ages keep counting between messages

  try {
    if (localStorage.getItem('relay.theme') === 'light') document.documentElement.dataset.theme = 'light';
  } catch (e) { /* default dark */ }

  function showName() {
    if (account) return;
    nameForm.hidden = !!sender;
    sendingAs.hidden = !sender;
    senderName.textContent = sender;
    if (!sender) nameInput.focus();
  }

  if (!account) nameForm.addEventListener('submit', function (ev) {
    ev.preventDefault();
    const v = nameInput.value.replace(/\s+/g, ' ').trim().slice(0, 32);
    if (!v) return;
    sender = v;
    try { localStorage.setItem(NAME_KEY, v); } catch (e) { /* kept for this visit */ }
    showName();
  });
  if (!account) document.getElementById('changeName').addEventListener('click', function () {
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

  function left(s) {
    s = Math.max(0, Math.round(s));
    return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  }

  function status(row) {
    const since = (Date.now() - receivedAt) / 1000;
    const age = (row.age_s || 0) + since;
    switch (row.state) {
      case 'on': {
        // The sender's own alert says only how long it has left; someone else's says who sent it.
        const mine = row.from && row.from === sender;
        const parts = ['Flashing'];
        if (!mine && row.from) parts.push('from ' + row.from);
        if (typeof row.ends_in_s === 'number') parts.push(left(row.ends_in_s - since) + ' left');
        parts.push('tap to stop');
        return { cls: 'on', text: parts.join(' · ') };
      }
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
      const st = status(row);
      b.className = 'alert-btn' + (row.state === 'on' ? ' flashing' : '') + (st ? ' was-' + st.cls : '');
      b.dataset.id = row.id;
      const icon = document.createElement('span');
      icon.className = 'alert-btn-icon';
      icon.innerHTML = window.RelayAlertIcons ? window.RelayAlertIcons.svg(row.icon) : '';
      b.appendChild(icon);
      const body = document.createElement('span');
      body.className = 'alert-btn-body';
      const name = document.createElement('span');
      name.className = 'alert-btn-name';
      name.textContent = row.name;
      body.appendChild(name);
      if (st) {
        const tag = document.createElement('span');
        tag.className = 'alert-btn-state ' + st.cls;
        tag.textContent = st.text;
        body.appendChild(tag);
      }
      b.appendChild(body);
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

  // The countdown ticks every second while an alert flashes; ages every ten seconds otherwise.
  let ticks = 0;
  setInterval(function () {
    ticks += 1;
    const on = rows.some(function (r) { return r.state === 'on'; });
    if (on || (ticks % 10 === 0 && rows.some(function (r) { return r.state !== 'idle'; }))) render();
  }, 1000);

  showName();
  connect();
})();
