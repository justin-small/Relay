/* The YouTube admin page (#252): the channel's live broadcast, the Connect card (core's oauth.js) and settings.
   Loaded by admin.html only while the module is on. Built from the admin page's own pieces (.pcard, dl, .spill,
   .pactions, details.sect, .field, .note); everything from the server is set as text. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-youtube')) return;
  const $ = R.$;
  (function () {
    const s = document.createElement('style');
    s.textContent = [
      '#page-youtube a{color:var(--fg)}',
      '#page-youtube .ytcards{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px;align-items:start;margin-bottom:12px}',
      '@media (max-width:900px){#page-youtube .ytcards{grid-template-columns:minmax(0,1fr)}}',
      '#page-youtube .pcard .phead .spill{padding:3px 9px;font-size:11px}',
      '#page-youtube .spill .dot{border-radius:50%;display:inline-block}',
      '#page-youtube .spill:not(.on) .dot{background:var(--dim)}',
      '#page-youtube .spill.idle{color:var(--dim)}',
      '#page-youtube .spill.warn{color:#fcd34d;border-color:#5b4a14}',
      '#page-youtube .spill.warn .dot{background:#fcd34d}',
      '#page-youtube .ytbc + .ytbc{margin-top:12px;padding-top:12px;border-top:1px solid var(--line)}',
      '#page-youtube .ytbc h4{margin:10px 0 0;font-size:13px;font-weight:600;overflow-wrap:anywhere}',
      '#page-youtube details.sect{margin-top:12px}',
      '#page-youtube .ytrow{display:flex;flex-wrap:wrap;gap:12px}',
      '#page-youtube .ytrow .field{flex:1 1 220px}',
    ].join('');
    document.head.appendChild(s);
  })();

  const canManage = window.relayCan('youtube.manage');
  const STATE = { live: 'Live', testing: 'Testing', liveStarting: 'Starting', testStarting: 'Starting test', ready: 'Ready', created: 'Created', complete: 'Ended' };
  const HEALTH = { ok: 'Live', warn: 'Needs a look', error: 'Trouble', idle: 'Standing by' };
  let data = null;
  let shown = '';
  let formShown = false;

  function el(tag, text, cls) {
    const e = document.createElement(tag);
    if (text) e.textContent = text;
    if (cls) e.className = cls;
    return e;
  }
  function showError(text) { $('ytErr').textContent = text || ''; $('ytErr').hidden = !text; }
  function pill(text, cls) {
    const p = el('span', '', 'spill ' + cls);
    p.appendChild(el('span', '', 'dot'));
    p.appendChild(document.createTextNode(text));
    return p;
  }
  function link(text, href) {
    const a = el('a', text); a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer';
    return a;
  }
  function since(t) {
    if (!t) return '';
    const s = Math.max(0, Math.floor(Date.now() / 1000 - t));
    const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60;
    return (h ? h + ' h ' : '') + m + ' min';
  }
  async function send(method, path, body) {
    const res = await fetch(path, { method: method, headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), credentials: 'same-origin', cache: 'no-store' });
    const out = await res.json().catch(function () { return {}; });
    if (!res.ok) {
      if (R.sessionEnded) R.sessionEnded(res, out);
      throw new Error(out.error || out.detail || 'That did not work.');
    }
    return out;
  }

  function liveCard() {
    const c = el('div', '', 'pcard');
    const head = el('div', '', 'phead');
    head.appendChild(el('span', 'Live status', 'pname'));
    const h = data.health || {};
    head.appendChild(pill(data.live ? 'On air' : (HEALTH[h.level] || h.level), data.live ? 'on' : (h.level === 'warn' || h.level === 'error' ? 'warn' : 'idle')));
    c.appendChild(head);
    if (h.level === 'warn' || h.level === 'error' || !data.account || data.account.state !== 'connected') {
      c.appendChild(el('p', h.detail || '', 'note' + (h.level === 'warn' ? ' err' : '')));
    }
    (data.broadcasts || []).forEach(function (b) {
      const box = el('div', '', 'ytbc');
      box.appendChild(el('h4', b.title || 'Untitled broadcast'));
      const dl = el('dl');
      function row(k, v) { if (v === '' || v === null || v === undefined) return; dl.appendChild(el('dt', k)); dl.appendChild(el('dd', String(v))); }
      row('State', STATE[b.state] || b.state);
      row('Live for', b.live ? since(b.started) : '');
      row('Watching now', typeof b.viewers === 'number' ? b.viewers.toLocaleString() : (b.live ? 'not shown by YouTube' : ''));
      row('Privacy', b.privacy);
      box.appendChild(dl);
      const acts = el('div', '', 'pactions');
      acts.appendChild(link('Watch page', b.watch));
      acts.appendChild(link('Open in YouTube Studio', b.studio));
      box.appendChild(acts);
      c.appendChild(box);
    });
    if (data.account && data.account.state === 'connected' && !(data.broadcasts || []).length && h.level !== 'warn') {
      c.appendChild(el('p', 'No broadcast is live or being tested.', 'note'));
    }
    const foot = el('p', '', 'note');
    const s = data.settings || {};
    foot.textContent = (data.checked_at ? 'Checked ' + R.agoText(Date.now() / 1000 - data.checked_at) + '. ' : '') +
      'Relay checks every ' + Math.round(s.live_s / 60 * 10) / 10 + ' min while something is on air and every ' +
      Math.round(s.idle_s / 60) + ' min otherwise.';
    c.appendChild(foot);
    if (canManage && data.account && data.account.state === 'connected') {
      const acts = el('div', '', 'pactions');
      const b = el('button', 'Check now'); b.type = 'button';
      b.onclick = async function () {
        b.disabled = true; showError('');
        try { data = await send('POST', '/api/admin/youtube/check'); render(); } catch (e) { showError(e.message); }
        b.disabled = false;
      };
      acts.appendChild(b);
      c.appendChild(acts);
    }
    return c;
  }

  function form() {
    const box = $('ytForm');
    box.replaceChildren();
    const s = data.settings || {};
    function num(id, label, value, min, max, help) {
      const f = el('div', '', 'field');
      const l = el('label', label); l.htmlFor = id;
      const i = el('input'); i.id = id; i.type = 'number'; i.min = min; i.max = max; i.value = value;
      f.appendChild(l); f.appendChild(i);
      if (help) f.appendChild(el('div', help, 'note'));
      return f;
    }
    const row = el('div', '', 'ytrow');
    row.appendChild(num('ytLiveS', 'Seconds between checks while on air', s.live_s, 30, 600, 'YouTube or another stream Relay follows (OBS) is on air.'));
    row.appendChild(num('ytIdleS', 'Seconds between checks otherwise', s.idle_s, 120, 3600, ''));
    box.appendChild(row);
    box.appendChild(el('p', 'Every Relay shares one daily YouTube allowance, so it checks rarely. A check costs 1 unit, or 2 while live.', 'note'));

    box.appendChild(el('h4', 'Your own Google app'));
    box.appendChild(el('p', data.builtin
      ? 'Relay signs in with its own Google app. To use your own instead, enter its client ID and secret ("TVs and Limited Input devices" client in Google Cloud).'
      : 'This copy of Relay has no Google app of its own yet. Make one in Google Cloud ("TVs and Limited Input devices" client, YouTube Data API v3 switched on) and enter its client ID and secret, then press Connect.', 'note'));
    const row2 = el('div', '', 'ytrow');
    const fid = el('div', '', 'field');
    const lid = el('label', 'Client ID'); lid.htmlFor = 'ytCid';
    const iid = el('input'); iid.id = 'ytCid'; iid.value = s.client_id || ''; iid.autocomplete = 'off'; iid.spellcheck = false;
    iid.placeholder = '….apps.googleusercontent.com';
    fid.appendChild(lid); fid.appendChild(iid); row2.appendChild(fid);
    const fs = el('div', '', 'field');
    const ls = el('label', 'Client secret'); ls.htmlFor = 'ytSecret';
    const is = el('input'); is.id = 'ytSecret'; is.type = 'password'; is.autocomplete = 'off';
    is.placeholder = s.client_secret_set ? 'Saved; leave blank to keep it' : '';
    fs.appendChild(ls); fs.appendChild(is); row2.appendChild(fs);
    box.appendChild(row2);

    const ctl = el('div', '', 'ctl');
    if (s.client_id) {
      const clear = el('button', 'Use Relay\'s app'); clear.type = 'button';
      clear.disabled = !data.builtin;
      clear.onclick = function () { save({ clear_client: true }); };
      ctl.appendChild(clear);
    }
    const b = el('button', 'Save', 'primary'); b.type = 'button';
    b.onclick = function () {
      save({ live_s: parseInt($('ytLiveS').value, 10), idle_s: parseInt($('ytIdleS').value, 10),
             client_id: iid.value.trim(), client_secret: is.value });
    };
    ctl.appendChild(b);
    box.appendChild(ctl);
    box.appendChild(el('p', 'Changing the app does not change an account already connected: press Connect again to sign in with it.', 'note'));
  }

  async function save(body) {
    showError('');
    try {
      data = await send('PUT', '/api/admin/youtube/settings', body);
      formShown = false;
      render();
      R.okMsg('YouTube settings saved.');
    } catch (e) { showError(e.message); }
  }

  function render() {
    if (!data) return;
    const h = data.health || {};
    $('ytSum').textContent = data.live ? '· on air' : '';
    const sig = JSON.stringify([data.broadcasts, h, data.live, data.account && data.account.state, data.checked_at, data.settings]);
    if (sig !== shown) {
      shown = sig;
      $('ytLive').replaceChildren(liveCard());
    }
    if (canManage && !formShown) { formShown = true; form(); }
    $('ytSettings').hidden = !canManage;
  }

  async function refresh() {
    try {
      data = await send('GET', '/api/admin/youtube');
      render();
    } catch (e) { showError(e.message); }
  }

  window.RelayOAuth.mount($('ytAccount'), '/api/admin/youtube/account', {
    manage: 'youtube.manage',
    intro: 'Connect the church\'s YouTube channel. Relay only reads it: whether it is live and how many are watching.',
    scopeNames: { 'https://www.googleapis.com/auth/youtube.readonly': 'Read the channel (read only)' },
    onChange: refresh,
  });
  refresh();
  setInterval(function () {
    const page = document.getElementById('page-youtube');
    if (!document.hidden && page && !page.hidden) refresh();
  }, 15000);
})();
