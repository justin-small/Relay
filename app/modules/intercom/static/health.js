/* The Intercom page's Health section (#276): the watchdog's cards, its thresholds and the connection log.
   Polls every 5 s while the page is showing. Everything from the server is set as text, never as markup. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('icHealth')) return;
  const $ = R.$;
  const BASE = '/api/admin/intercom';
  function can(perm) { return typeof window.relayCan !== 'function' || !!window.relayCan(perm); }
  let timer = null;
  let thDrawn = false;
  let last = null;

  function fail(e) {
    $('icHealthErr').textContent = e.message || String(e);
    $('icHealthErr').hidden = false;
  }

  function el(tag, text, cls) {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  }

  const WORD = { ok: 'OK', warn: 'Warning', error: 'Problem', idle: 'Idle' };

  function drawCards(v) {
    const box = $('icCards');
    box.textContent = '';
    v.cards.forEach(function (c) {
      const card = el('div', undefined, 'ic-card ' + c.state);
      card.appendChild(el('b', c.title));
      card.appendChild(el('div', WORD[c.state] || c.state, 'st'));
      card.appendChild(el('div', c.detail, 'note'));
      box.appendChild(card);
    });
    $('icHealthSum').textContent = v.cards.length ? (WORD[v.state] || v.state).toLowerCase() : 'starting';
  }

  function drawThresholds(v) {
    if (thDrawn) return;           // never overwrite what the admin is typing
    thDrawn = true;
    const box = $('icTh');
    box.textContent = '';
    const edit = can('intercom.manage');
    Object.keys(v.labels).forEach(function (k) {
      const lab = el('label');
      lab.appendChild(el('span', v.labels[k]));
      const inp = document.createElement('input');
      inp.type = 'number';
      inp.step = 'any';
      inp.dataset.key = k;
      inp.value = v.thresholds[k];
      inp.placeholder = v.defaults[k];
      inp.disabled = !edit;
      lab.appendChild(inp);
      box.appendChild(lab);
    });
    $('icThSave').hidden = $('icThReset').hidden = !edit;
  }

  async function saveThresholds(values) {
    $('icHealthErr').hidden = true;
    try {
      const v = await R.api(BASE + '/health/thresholds', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ thresholds: values }) });
      thDrawn = false;
      drawThresholds(v);
      drawCards(v);
      R.okMsg && R.okMsg('Thresholds saved.');
    } catch (e) { fail(e); }
  }

  $('icThSave').addEventListener('click', function () {
    const out = {};
    $('icTh').querySelectorAll('input[data-key]').forEach(function (i) {
      if (i.value !== '') out[i.dataset.key] = Number(i.value);
    });
    saveThresholds(out);
  });
  $('icThReset').addEventListener('click', function () { saveThresholds({}); });

  function detailText(d) {
    return Object.keys(d || {}).map(function (k) {
      const v = d[k];
      return k + ' ' + (v !== null && typeof v === 'object' ? JSON.stringify(v) : String(v));
    }).join(', ');
  }

  function when(iso) {
    const t = Date.parse(iso);
    return isNaN(t) ? iso : new Date(t).toLocaleString();
  }

  async function refreshLog() {
    const hours = $('icLogHours').value;
    const kind = $('icLogKind').value.trim();
    try {
      const got = await R.api(BASE + '/events?hours=' + encodeURIComponent(hours) + '&kind=' + encodeURIComponent(kind));
      const body = $('icEvents');
      body.textContent = '';
      got.events.forEach(function (e) {
        const tr = document.createElement('tr');
        [when(e.at), e.kind, e.user, e.device, e.channel, e.actor, detailText(e.detail)].forEach(function (v) {
          tr.appendChild(el('td', v));
        });
        body.appendChild(tr);
      });
      $('icEventsTable').hidden = !got.events.length;
      $('icEventsEmpty').hidden = !!got.events.length;
    } catch (e) { /* the cards show what is wrong */ }
  }

  $('icLogHours').addEventListener('change', refreshLog);
  $('icLogKind').addEventListener('change', refreshLog);
  $('icLogCsv').addEventListener('click', function () {
    window.location.href = BASE + '/events.csv?hours=' + encodeURIComponent($('icLogHours').value);
  });

  async function refresh() {
    const page = document.getElementById('page-intercom');
    if (page && page.offsetParent === null && last) return;     // not showing: skip until it is
    try {
      last = await R.api(BASE + '/health');
      $('icHealthErr').hidden = true;
      drawCards(last);
      drawThresholds(last);
    } catch (e) { fail(e); }
  }

  refresh();
  refreshLog();
  timer = setInterval(function () { refresh(); }, 5000);
  setInterval(function () { const p = document.getElementById('page-intercom'); if (!p || p.offsetParent !== null) refreshLog(); }, 30000);
  window.addEventListener('beforeunload', function () { clearInterval(timer); });
})();
