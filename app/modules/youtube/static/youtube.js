/* The YouTube admin page (#252): the channel's live broadcast, the Connect card (core's oauth.js) and settings.
   Loaded by admin.html only while the module is on. Built from the admin page's own pieces (.pcard, dl, .spill,
   .pactions, details.sect, .field, .note); everything from the server is set as text. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-youtube')) return;
  const $ = R.$;

  const canManage = window.relayCan('youtube.manage');
  const STATE = { live: 'Live', testing: 'Testing', liveStarting: 'Starting', testStarting: 'Starting test', ready: 'Ready', created: 'Created', complete: 'Ended' };
  const HEALTH = { ok: 'Live', warn: 'Needs a look', error: 'Trouble', idle: 'Standing by' };
  let data = null;
  let shown = '';
  let formShown = false;

  const el = relayUI.el;
  const showError = relayUI.note('ytErr');
  const pill = relayUI.pill;
  const link = relayUI.link;
  function since(t) {
    if (!t) return '';
    const s = Math.max(0, Math.floor(Date.now() / 1000 - t));
    const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60;
    return (h ? h + ' h ' : '') + m + ' min';
  }
  const send = relayUI.client({ cache: 'no-store', session: true });

  function liveCard() {
    const c = el('div', 'pcard');
    const head = el('div', 'phead');
    head.appendChild(el('span', 'pname', 'Live status'));
    const h = data.health || {};
    head.appendChild(pill(data.live ? 'On air' : (HEALTH[h.level] || h.level), data.live ? 'on' : (h.level === 'warn' || h.level === 'error' ? 'warn' : 'idle')));
    c.appendChild(head);
    if (h.level === 'warn' || h.level === 'error' || !data.account || data.account.state !== 'connected') {
      c.appendChild(el('p', 'note' + (h.level === 'warn' ? ' err' : ''), h.detail || ''));
    }
    (data.broadcasts || []).forEach(function (b) {
      const box = el('div', 'ytbc');
      box.appendChild(el('h4', '', b.title || 'Untitled broadcast'));
      const dl = el('dl');
      function row(k, v) { if (v === '' || v === null || v === undefined) return; dl.appendChild(el('dt', '', k)); dl.appendChild(el('dd', '', String(v))); }
      row('State', STATE[b.state] || b.state);
      row('Live for', b.live ? since(b.started) : '');
      row('Watching now', typeof b.viewers === 'number' ? b.viewers.toLocaleString() : (b.live ? 'not shown by YouTube' : ''));
      row('Privacy', b.privacy);
      box.appendChild(dl);
      const acts = el('div', 'pactions');
      acts.appendChild(link('Watch page', b.watch));
      acts.appendChild(link('Open in YouTube Studio', b.studio));
      box.appendChild(acts);
      c.appendChild(box);
    });
    if (data.account && data.account.state === 'connected' && !(data.broadcasts || []).length && h.level !== 'warn') {
      c.appendChild(el('p', 'note', 'No broadcast is live or being tested.'));
    }
    const foot = el('p', 'note');
    const s = data.settings || {};
    foot.textContent = (data.checked_at ? 'Checked ' + R.agoText(Date.now() / 1000 - data.checked_at) + '. ' : '') +
      'Relay checks every ' + Math.round(s.live_s / 60 * 10) / 10 + ' min while something is on air and every ' +
      Math.round(s.idle_s / 60) + ' min otherwise.';
    c.appendChild(foot);
    if (canManage && data.account && data.account.state === 'connected') {
      const acts = el('div', 'pactions');
      const b = el('button', '', 'Check now'); b.type = 'button';
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
      const f = el('div', 'field');
      const l = el('label', '', label); l.htmlFor = id;
      const i = el('input'); i.id = id; i.type = 'number'; i.min = min; i.max = max; i.value = value;
      f.appendChild(l); f.appendChild(i);
      if (help) f.appendChild(el('div', 'note', help));
      return f;
    }
    const row = el('div', 'ytrow');
    row.appendChild(num('ytLiveS', 'Seconds between checks while on air', s.live_s, 30, 600, 'YouTube or another stream Relay follows (OBS) is on air.'));
    row.appendChild(num('ytIdleS', 'Seconds between checks otherwise', s.idle_s, 120, 3600, ''));
    box.appendChild(row);
    box.appendChild(el('p', 'note', 'Every Relay shares one daily YouTube allowance, so it checks rarely. A check costs 1 unit, or 2 while live.'));

    box.appendChild(el('h4', '', 'Your own Google app'));
    box.appendChild(el('p', 'note', 'Relay signs in with its own Google app, through Relay\'s cloud. To use your own app instead, so the sign-in goes straight between this computer and Google, enter its client ID and secret ("TVs and Limited Input devices" client in Google Cloud, YouTube Data API v3 switched on); Connect then shows a code to enter at google.com/device.'));
    const row2 = el('div', 'ytrow');
    const fid = el('div', 'field');
    const lid = el('label', '', 'Client ID'); lid.htmlFor = 'ytCid';
    const iid = el('input'); iid.id = 'ytCid'; iid.value = s.client_id || ''; iid.autocomplete = 'off'; iid.spellcheck = false;
    iid.placeholder = '….apps.googleusercontent.com';
    fid.appendChild(lid); fid.appendChild(iid); row2.appendChild(fid);
    const fs = el('div', 'field');
    const ls = el('label', '', 'Client secret'); ls.htmlFor = 'ytSecret';
    const is = el('input'); is.id = 'ytSecret'; is.type = 'password'; is.autocomplete = 'off';
    is.placeholder = s.client_secret_set ? 'Saved; leave blank to keep it' : '';
    fs.appendChild(ls); fs.appendChild(is); row2.appendChild(fs);
    box.appendChild(row2);

    const ctl = el('div', 'ctl');
    if (s.client_id) {
      const clear = el('button', '', 'Use Relay\'s app'); clear.type = 'button';
      clear.onclick = function () { save({ clear_client: true }); };
      ctl.appendChild(clear);
    }
    const b = el('button', 'primary', 'Save'); b.type = 'button';
    b.onclick = function () {
      save({ live_s: parseInt($('ytLiveS').value, 10), idle_s: parseInt($('ytIdleS').value, 10),
             client_id: iid.value.trim(), client_secret: is.value });
    };
    ctl.appendChild(b);
    box.appendChild(ctl);
    box.appendChild(el('p', 'note', 'After changing the app, press Connect again to sign in with it.'));
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
    scopeNames: {
      'https://www.googleapis.com/auth/youtube.readonly': 'Read the channel (read only)',
      'https://www.googleapis.com/auth/youtube.force-ssl': 'Read the channel and schedule its live streams',
      'https://www.googleapis.com/auth/youtube': 'Read the channel and schedule its live streams',
    },
    onChange: refresh,
  });
  refresh();
  setInterval(function () {
    const page = document.getElementById('page-youtube');
    if (!document.hidden && page && !page.hidden) refresh();
  }, 15000);
})();
