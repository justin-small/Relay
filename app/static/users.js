/* My account (every role; API tokens for those who may make them) and the Users page: people, trusted devices,
   everyone's API tokens (Owners) and what each role can do. Loaded after
   admin.js and uses what it shares (window.RelayAdmin). Everything from the server is set as text. Passwords
   and PINs are read from the form, sent once and cleared; they are never stored, logged or put in an attribute. */
(function () {
  const A = window.RelayAdmin;
  if (!A || !window.RELAY_ME) return;
  const el = A.el;
  const g = function (id) { return document.getElementById(id); };
  const ME = window.RELAY_ME;
  const can = window.relayCan;

  function send(method, path, body) {
    const opts = { method: method };
    if (body !== undefined) {
      opts.headers = { 'Content-Type': 'application/json' };
      opts.body = JSON.stringify(body);
    }
    return A.api(path, opts);
  }

  function field(label, control, id) {
    const f = el('div', 'field');
    const l = el('label', '', label);
    if (id) { control.id = id; l.htmlFor = id; }
    f.appendChild(l);
    f.appendChild(control);
    return f;
  }

  function input(type, max) {
    const i = el('input');
    i.type = type;
    if (max) i.maxLength = max;
    i.autocomplete = 'off';
    return i;
  }

  function when(iso, withTime) {
    if (!iso) return 'never';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return 'never';
    return withTime
      ? d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
      : d.toLocaleDateString(undefined, { dateStyle: 'medium' });
  }

  function cap(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : ''; }

  /* ---- My account ---- */
  function paintAccount() {
    const roleLabel = cap(ME.user.role);
    g('acctWho').textContent = ME.user.name + ' · ' + ME.user.username + ' · ' + roleLabel;
    g('pinState').textContent = ME.pin_set ? 'You have a PIN set.' : 'You have no PIN set.';
    g('pinSave').textContent = ME.pin_set ? 'Change PIN' : 'Set PIN';
    g('pinRemove').hidden = !ME.pin_set;
    const d = ME.device || {};
    g('deviceState').textContent = d.trusted
      ? 'This browser is a trusted device' + (d.name ? ' (' + d.name + ')' : '') + '. A PIN works here.'
      : 'This browser is not a trusted device, so a PIN will not work here. '
        + (can('users.manage') ? 'You can trust it on the Users page.' : 'An administrator can trust it for you.');
  }

  function initAccount() {
    paintAccount();
    g('pwForm').addEventListener('submit', async function (ev) {
      ev.preventDefault();
      const cur = g('pwCurrent'), nw = g('pwNew'), again = g('pwConfirm');
      if (nw.value !== again.value) { A.alertMsg('The new passwords do not match.'); return; }
      try {
        await send('POST', '/api/admin/me/password', { current: cur.value, 'new': nw.value });
        cur.value = ''; nw.value = ''; again.value = '';
        A.okMsg('Password changed.');
      } catch (e) { A.alertMsg(e.message); }
    });
    g('pinForm').addEventListener('submit', async function (ev) {
      ev.preventDefault();
      const pw = g('pinPassword'), pin = g('pinNew');
      if (!/^\d{4,8}$/.test(pin.value)) { A.alertMsg('A PIN is 4 to 8 digits.'); return; }
      try {
        const r = await send('POST', '/api/admin/me/pin', { password: pw.value, pin: pin.value });
        ME.pin_set = !!r.pin_set;
        pw.value = ''; pin.value = '';
        paintAccount();
        A.okMsg('PIN saved.');
      } catch (e) { A.alertMsg(e.message); }
    });
    g('pinRemove').addEventListener('click', async function () {
      try {
        const r = await send('DELETE', '/api/admin/me/pin');
        ME.pin_set = !!r.pin_set;
        g('pinPassword').value = ''; g('pinNew').value = '';
        paintAccount();
        A.okMsg('PIN removed.');
      } catch (e) { A.alertMsg(e.message); }
    });
  }


  /* ---- My account: API tokens (#174) ---- */
  let myTokens = [], expiryDays = [30, 90, 365, null], defaultDays = 90;

  function expiryText(t) { return t.expires ? when(t.expires, false) : 'Never'; }

  function closeTokenPanel() {
    const p = g('tokenPanel');
    p.textContent = '';
    p.hidden = true;
  }

  function tokenRow(t, withPerson, onRevoke) {
    const tr = el('tr');
    const nameTd = el('td');
    nameTd.appendChild(el('strong', '', t.name));
    tr.appendChild(nameTd);
    if (withPerson) tr.appendChild(el('td', '', t.user_name + ' (' + t.username + ')'));
    tr.appendChild(el('td', 'mono', 'relay_' + t.hint + '…'));
    if (!withPerson) tr.appendChild(el('td', 'muted', when(t.created, false)));
    tr.appendChild(el('td', 'muted', expiryText(t)));
    tr.appendChild(el('td', 'muted', when(t.last_used, true)));
    const act = el('td', 'userrow-acts');
    const rm = el('button', '', 'Revoke');
    rm.type = 'button';
    rm.setAttribute('aria-label', 'Revoke ' + t.name);
    rm.addEventListener('click', async function () {
      const whose = withPerson ? t.user_name + '\'s token ' : 'the token ';
      if (!window.confirm('Revoke ' + whose + '"' + t.name + '"? Anything using it stops working at once.')) return;
      try { await onRevoke(t); A.okMsg('Revoked ' + t.name + '.'); } catch (e) { A.alertMsg(e.message); }
    });
    act.appendChild(rm);
    tr.appendChild(act);
    return tr;
  }

  function emptyRow(tb, cols, text) {
    const tr = el('tr');
    const td = el('td', 'muted', text);
    td.colSpan = cols;
    tr.appendChild(td);
    tb.appendChild(tr);
  }

  function renderMyTokens() {
    const tb = g('tokenList');
    tb.textContent = '';
    if (!myTokens.length) emptyRow(tb, 6, 'No tokens yet.');
    myTokens.forEach(function (t) {
      tb.appendChild(tokenRow(t, false, async function () {
        await send('DELETE', '/api/admin/me/tokens/' + encodeURIComponent(t.id));
        await loadMyTokens();
      }));
    });
  }

  async function loadMyTokens() {
    try {
      const r = await A.api('/api/admin/me/tokens');
      myTokens = r.tokens || [];
      if (r.expiry_days) expiryDays = r.expiry_days;
      if (r.default_days) defaultDays = r.default_days;
      renderMyTokens();
    } catch (e) { A.alertMsg(e.message); }
  }

  function openNewToken() {
    hideOnce(g('tokenSecret'));
    const p = g('tokenPanel');
    p.textContent = '';
    p.hidden = false;
    p.appendChild(el('h4', '', 'New API token'));
    const form = el('form');
    form.autocomplete = 'off';
    const name = input('text', 80);
    name.placeholder = 'Stream Deck in the booth';
    name.required = true;
    const exp = el('select');
    expiryDays.forEach(function (d) {
      const o = el('option', '', d ? d + ' days' : 'Never');
      o.value = d ? String(d) : '';
      exp.appendChild(o);
    });
    exp.value = String(defaultDays);
    form.appendChild(field('What it is for', name, 'tokenName'));
    form.appendChild(field('Expires after', exp, 'tokenDays'));
    form.appendChild(formButtons('Make token', closeTokenPanel));
    form.addEventListener('submit', async function (ev) {
      ev.preventDefault();
      try {
        const r = await send('POST', '/api/admin/me/tokens', { name: name.value.trim(), days: exp.value ? Number(exp.value) : null });
        closeTokenPanel();
        await loadMyTokens();
        showOnce(g('tokenSecret'), 'Copy this token now and keep it somewhere safe. It is shown once; if it is lost, make a new one.',
          r.secret, function () { hideOnce(g('tokenSecret')); });
      } catch (e) { A.alertMsg(e.message); }
    });
    p.appendChild(form);
    name.focus();
  }

  function initTokens() {
    g('tokenAdd').addEventListener('click', openNewToken);
    window.addEventListener('hashchange', function () {
      if (location.hash === '#account') loadMyTokens();
      else { hideOnce(g('tokenSecret')); closeTokenPanel(); }
    });
    if (location.hash === '#account') loadMyTokens();
  }

  /* ---- Users page: everyone's API tokens (Owners) ---- */
  let allTokens = [];

  function renderAllTokens() {
    const tb = g('allTokenList');
    if (!tb) return;
    tb.textContent = '';
    if (!allTokens.length) emptyRow(tb, 6, 'Nobody has an API token.');
    allTokens.forEach(function (t) {
      tb.appendChild(tokenRow(t, true, async function () {
        await send('DELETE', '/api/admin/tokens/' + encodeURIComponent(t.id));
        await load();
        if (t.user_id === ME.user.id && g('tokenList')) await loadMyTokens();
      }));
    });
  }

  /* ---- Users ---- */
  let users = [], roles = [], perms = [];
  let devices = [], thisDevice = null;
  let secretText = '';

  // 16 characters from an alphabet without look-alikes (no 0/O, 1/l/I), drawn without bias, in groups of four.
  const PASS_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789';
  function generatePassword() {
    const out = [];
    const limit = 256 - (256 % PASS_CHARS.length);
    const buf = new Uint8Array(32);
    while (out.length < 16) {
      window.crypto.getRandomValues(buf);
      for (let i = 0; i < buf.length && out.length < 16; i++) {
        if (buf[i] < limit) out.push(PASS_CHARS.charAt(buf[i] % PASS_CHARS.length));
      }
    }
    return [0, 4, 8, 12].map(function (i) { return out.slice(i, i + 4).join(''); }).join('-');
  }

  function rankOf(role) {
    const r = roles.filter(function (x) { return x.id === role; })[0];
    return r ? r.rank : 0;
  }
  function roleLabel(role) {
    const r = roles.filter(function (x) { return x.id === role; })[0];
    return r ? r.label : cap(role);
  }
  function myRank() { return rankOf(ME.user.role); }
  function activeOwners() {
    return users.filter(function (u) { return u.role === 'owner' && !u.disabled; }).length;
  }
  function isLastOwner(u) { return u.role === 'owner' && !u.disabled && activeOwners() === 1; }

  function statusOf(u) {
    if (u.disabled) return { cls: 'stat-off', text: 'Turned off' };
    if (u.must_change_password) return { cls: 'stat-warn', text: 'Must change password' };
    return { cls: '', text: 'Active' };
  }

  function clearSecret() {
    secretText = '';
    hideOnce(g('userSecret'));
  }

  /* A secret shown once in `box` (a .shown div), with Copy and Done, so it can be handed over or pasted. */
  function showOnce(box, intro, text, onDone) {
    box.textContent = '';
    box.appendChild(el('div', '', intro));
    const mono = el('div', 'mono', text);
    mono.style.margin = '8px 0';
    box.appendChild(mono);
    const ctl = el('div', 'ctl');
    const copy = el('button', '', 'Copy');
    copy.type = 'button';
    copy.addEventListener('click', async function () {
      try {
        await navigator.clipboard.writeText(text);
        copy.textContent = 'Copied';
      } catch (e) {
        // Plain HTTP on a LAN address has no clipboard: select the text instead.
        const r = document.createRange();
        r.selectNodeContents(mono);
        const sel = window.getSelection();
        sel.removeAllRanges(); sel.addRange(r);
        copy.textContent = 'Press Ctrl/⌘+C';
      }
      setTimeout(function () { copy.textContent = 'Copy'; }, 1600);
    });
    const done = el('button', '', 'Done');
    done.type = 'button';
    done.addEventListener('click', onDone);
    ctl.appendChild(copy);
    ctl.appendChild(done);
    box.appendChild(ctl);
    box.hidden = false;
    try { box.scrollIntoView({ block: 'nearest' }); } catch (e) {}
  }

  function hideOnce(box) {
    if (!box) return;
    box.textContent = '';
    box.hidden = true;
  }

  /* A generated password is shown here once, after the account is made or reset, so it can be handed over. */
  function showSecret(name, text) {
    secretText = text;
    showOnce(g('userSecret'), 'Give ' + name + ' this temporary password. It is shown once; they must change it when they sign in.',
      text, clearSecret);
  }

  /* A password box with a Generate button. get() is the text; generated() says whether it was made here. */
  function passField(label, id) {
    let generated = false;
    const wrap = el('div', 'field');
    const l = el('label', '', label);
    l.htmlFor = id;
    wrap.appendChild(l);
    const row = el('div', 'passbox');
    const box = input('password', 200);
    box.id = id;
    box.autocomplete = 'new-password';
    box.required = true;
    box.addEventListener('input', function () { generated = false; });
    const gen = el('button', '', 'Generate');
    gen.type = 'button';
    gen.addEventListener('click', function () {
      box.value = generatePassword();
      box.type = 'text';
      generated = true;
      box.focus();
      box.select();
    });
    row.appendChild(box);
    row.appendChild(gen);
    wrap.appendChild(row);
    return {
      node: wrap,
      get: function () { return box.value; },
      generated: function () { return generated; },
      clear: function () { box.value = ''; box.type = 'password'; generated = false; }
    };
  }

  function closePanel() {
    const p = g('userPanel');
    p.textContent = '';
    p.hidden = true;
  }

  function openPanel(title) {
    clearSecret();
    const p = g('userPanel');
    p.textContent = '';
    p.hidden = false;
    p.appendChild(el('h4', '', title));
    return p;
  }

  function roleSelect(value) {
    const sel = el('select');
    roles.filter(function (r) { return r.rank <= myRank(); }).forEach(function (r) {
      const o = el('option', '', r.label);
      o.value = r.id;
      sel.appendChild(o);
    });
    sel.value = value;
    return sel;
  }

  function formButtons(primaryText, onCancel) {
    const ctl = el('div', 'ctl');
    const ok = el('button', 'primary', primaryText);
    ok.type = 'submit';
    const cancel = el('button', '', 'Cancel');
    cancel.type = 'button';
    cancel.addEventListener('click', onCancel);
    ctl.appendChild(ok);
    ctl.appendChild(cancel);
    return ctl;
  }

  function openAdd() {
    const p = openPanel('Add a user');
    const form = el('form');
    form.autocomplete = 'off';
    const uname = input('text', 32);
    uname.autocapitalize = 'none';
    uname.spellcheck = false;
    uname.required = true;
    const name = input('text', 80);
    name.required = true;
    const role = roleSelect('viewer');
    const pass = passField('Temporary password', 'newUserPass');
    form.appendChild(field('Username', uname, 'newUserName'));
    form.appendChild(el('div', 'note', '3 to 32 letters, numbers, dots, dashes or underscores. They type this to sign in.'));
    form.appendChild(field('Name', name, 'newUserFull'));
    form.appendChild(field('Role', role, 'newUserRole'));
    form.appendChild(pass.node);
    form.appendChild(formButtons('Add user', closePanel));
    form.addEventListener('submit', async function (ev) {
      ev.preventDefault();
      const generated = pass.generated();
      const text = pass.get();
      try {
        const r = await send('POST', '/api/admin/users', {
          username: uname.value.trim(), name: name.value.trim(), role: role.value, password: text
        });
        closePanel();
        pass.clear();
        A.okMsg('Added ' + r.user.name + '.');
        await load();
        if (generated) showSecret(r.user.name, text);
      } catch (e) { A.alertMsg(e.message); }
    });
    p.appendChild(form);
    uname.focus();
  }

  function openEdit(u) {
    const self = u.id === ME.user.id;
    const lastOwner = isLastOwner(u);
    const p = openPanel('Edit ' + u.name);
    const form = el('form');
    form.autocomplete = 'off';
    const name = input('text', 80);
    name.value = u.name;
    name.required = true;
    const role = roleSelect(u.role);
    form.appendChild(field('Name', name, 'editUserName'));
    form.appendChild(field('Role', role, 'editUserRole'));
    if (self || lastOwner) {
      role.disabled = true;
      form.appendChild(el('div', 'note', self ? 'You cannot change your own role.'
        : 'This is the only Owner. Make someone else an Owner first.'));
    }
    form.appendChild(formButtons('Save', closePanel));
    form.addEventListener('submit', async function (ev) {
      ev.preventDefault();
      const body = {};
      if (name.value.trim() !== u.name) body.name = name.value.trim();
      if (!role.disabled && role.value !== u.role) body.role = role.value;
      if (!Object.keys(body).length) { closePanel(); return; }
      try {
        const r = await send('PUT', '/api/admin/users/' + encodeURIComponent(u.id), body);
        if (self && r.user) {
          ME.user.name = r.user.name;
          const nm = document.querySelector('#acctLink .acctname');
          if (nm) nm.textContent = r.user.name;
          paintAccount();
        }
        closePanel();
        A.okMsg('Saved.');
        await load();
      } catch (e) { A.alertMsg(e.message); }
    });
    p.appendChild(form);

    // The other things that can be done to this person.
    const acts = el('div', 'ctl');
    acts.style.marginTop = '16px';
    acts.style.paddingTop = '14px';
    acts.style.borderTop = '1px solid var(--line)';
    if (!self && !lastOwner) {
      const toggle = el('button', '', u.disabled ? 'Turn on' : 'Turn off');
      toggle.type = 'button';
      toggle.addEventListener('click', async function () {
        try {
          await send('PUT', '/api/admin/users/' + encodeURIComponent(u.id), { disabled: !u.disabled });
          closePanel();
          A.okMsg(u.disabled ? u.name + ' can sign in again.' : u.name + ' is turned off and signed out.');
          await load();
        } catch (e) { A.alertMsg(e.message); }
      });
      acts.appendChild(toggle);
    }
    const reset = el('button', '', 'Reset password');
    reset.type = 'button';
    reset.addEventListener('click', function () { openReset(u); });
    acts.appendChild(reset);
    const out = el('button', '', 'Sign out everywhere');
    out.type = 'button';
    out.addEventListener('click', async function () {
      try {
        await send('POST', '/api/admin/users/' + encodeURIComponent(u.id) + '/signout', {});
        A.okMsg(u.name + ' is signed out everywhere.');
      } catch (e) { A.alertMsg(e.message); }
    });
    acts.appendChild(out);
    if (!self && !lastOwner) {
      const del = el('button', 'danger', 'Delete');
      del.type = 'button';
      del.addEventListener('click', async function () {
        if (!window.confirm('Delete ' + u.name + ' (' + u.username + ')? They are signed out at once and the account cannot be brought back.')) return;
        try {
          await send('DELETE', '/api/admin/users/' + encodeURIComponent(u.id));
          closePanel();
          A.okMsg('Deleted ' + u.name + '.');
          await load();
        } catch (e) { A.alertMsg(e.message); }
      });
      acts.appendChild(del);
    }
    p.appendChild(acts);
    name.focus();
  }

  function openReset(u) {
    const p = openPanel('Reset password for ' + u.name);
    const form = el('form');
    form.autocomplete = 'off';
    const pass = passField('New temporary password', 'resetUserPass');
    form.appendChild(pass.node);
    form.appendChild(el('div', 'note', 'This signs them out everywhere and clears their PIN. They choose a new password the next time they sign in.'));
    form.appendChild(formButtons('Reset password', closePanel));
    form.addEventListener('submit', async function (ev) {
      ev.preventDefault();
      const generated = pass.generated();
      const text = pass.get();
      try {
        await send('POST', '/api/admin/users/' + encodeURIComponent(u.id) + '/password', { password: text });
        closePanel();
        pass.clear();
        A.okMsg('Password reset for ' + u.name + '.');
        await load();
        if (generated) showSecret(u.name, text);
      } catch (e) { A.alertMsg(e.message); }
    });
    p.appendChild(form);
    form.querySelector('input').focus();
  }

  function renderUsers() {
    const tb = g('userList');
    tb.textContent = '';
    g('usersSum').textContent = users.length ? '· ' + users.length + (users.length === 1 ? ' person' : ' people') : '';
    if (!users.length) {
      const tr = el('tr');
      const td = el('td', 'muted', 'No users yet.');
      td.colSpan = 7;
      tr.appendChild(td);
      tb.appendChild(tr);
      return;
    }
    const manage = can('users.manage');
    users.forEach(function (u) {
      const tr = el('tr');
      const nameTd = el('td');
      nameTd.appendChild(el('strong', '', u.name));
      if (u.id === ME.user.id) nameTd.appendChild(el('span', 'muted', ' (you)'));
      tr.appendChild(nameTd);
      tr.appendChild(el('td', 'mono', u.username));
      const roleTd = el('td');
      roleTd.appendChild(el('span', 'rolepill', roleLabel(u.role)));
      tr.appendChild(roleTd);
      const st = statusOf(u);
      tr.appendChild(el('td', st.cls, st.text));
      tr.appendChild(el('td', 'muted', u.pin_set ? 'Set' : '—'));
      tr.appendChild(el('td', 'muted', when(u.last_login, true)));
      const act = el('td', 'userrow-acts');
      // Nothing is offered for an account above your own: the server would refuse it.
      if (manage && rankOf(u.role) <= myRank()) {
        const edit = el('button', '', 'Edit');
        edit.type = 'button';
        edit.setAttribute('aria-label', 'Edit ' + u.name);
        edit.addEventListener('click', function () { openEdit(u); });
        act.appendChild(edit);
      }
      tr.appendChild(act);
      tb.appendChild(tr);
    });
  }

  /* ---- Trusted devices ---- */
  function closeDevicePanel() {
    const p = g('devicePanel');
    p.textContent = '';
    p.hidden = true;
  }

  function renderDevices() {
    const tb = g('deviceList');
    tb.textContent = '';
    if (!devices.length) {
      const tr = el('tr');
      const td = el('td', 'muted', 'No trusted devices yet.');
      td.colSpan = 5;
      tr.appendChild(td);
      tb.appendChild(tr);
    }
    const manage = can('users.manage');
    devices.forEach(function (d) {
      const tr = el('tr');
      const nameTd = el('td');
      nameTd.appendChild(el('strong', '', d.name));
      if (d.id === thisDevice) nameTd.appendChild(el('span', 'thisdev', 'This browser'));
      tr.appendChild(nameTd);
      tr.appendChild(el('td', 'muted', d.created_by || '—'));
      tr.appendChild(el('td', 'muted', when(d.created, false)));
      tr.appendChild(el('td', 'muted', when(d.last_used, true)));
      const act = el('td', 'userrow-acts');
      if (manage) {
        const rm = el('button', '', 'Remove');
        rm.type = 'button';
        rm.setAttribute('aria-label', 'Remove ' + d.name);
        rm.addEventListener('click', async function () {
          if (!window.confirm('Remove ' + d.name + '? A PIN will stop working there.')) return;
          try {
            await send('DELETE', '/api/admin/trusted-devices/' + encodeURIComponent(d.id));
            if (d.id === thisDevice) { ME.device = { trusted: false, name: null }; paintAccount(); }
            A.okMsg('Removed ' + d.name + '.');
            await load();
          } catch (e) { A.alertMsg(e.message); }
        });
        act.appendChild(rm);
      }
      tr.appendChild(act);
      tb.appendChild(tr);
    });
    const add = g('deviceAdd');
    if (add) add.hidden = thisDevice !== null && thisDevice !== undefined;
  }

  function openTrust() {
    const p = g('devicePanel');
    p.textContent = '';
    p.hidden = false;
    p.appendChild(el('h4', '', 'Trust this browser'));
    const form = el('form');
    form.autocomplete = 'off';
    const name = input('text', 60);
    name.placeholder = 'Sound booth iPad';
    name.required = true;
    form.appendChild(field('A name for this device', name, 'trustName'));
    form.appendChild(formButtons('Trust this browser', closeDevicePanel));
    form.addEventListener('submit', async function (ev) {
      ev.preventDefault();
      try {
        const r = await send('POST', '/api/admin/trusted-devices', { name: name.value.trim() });
        ME.device = { trusted: true, name: r.device && r.device.name ? r.device.name : name.value.trim() };
        paintAccount();
        closeDevicePanel();
        A.okMsg('This browser is now trusted.');
        await load();
      } catch (e) { A.alertMsg(e.message); }
    });
    p.appendChild(form);
    name.focus();
  }

  /* ---- What each role can do: built from what the server lists, so a module's permissions appear by themselves ---- */
  function renderRoleRef() {
    const box = g('roleRef');
    box.textContent = '';
    const cols = roles.slice().sort(function (a, b) { return a.rank - b.rank; });
    const table = el('table', 'rolegrid');
    const head = el('tr');
    head.appendChild(el('th', '', 'May…'));
    cols.forEach(function (r) { head.appendChild(el('th', '', r.label)); });
    const thead = el('thead');
    thead.appendChild(head);
    table.appendChild(thead);
    const tbody = el('tbody');
    const groups = [];
    perms.forEach(function (p) { if (groups.indexOf(p.group) === -1) groups.push(p.group); });
    groups.forEach(function (grp) {
      const gr = el('tr', 'grp');
      const gt = el('td', '', grp);
      gt.colSpan = cols.length + 1;
      gr.appendChild(gt);
      tbody.appendChild(gr);
      perms.filter(function (p) { return p.group === grp; }).forEach(function (p) {
        const tr = el('tr');
        tr.appendChild(el('td', '', p.label));
        const need = rankOf(p.min_role);
        cols.forEach(function (r) {
          const yes = r.rank >= need;
          const td = el('td', yes ? 'yes' : 'no');
          td.appendChild(el('span', '', yes ? '✓' : '–'));
          td.appendChild(el('span', 'sr', yes ? 'Yes' : 'No'));
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
    });
    table.appendChild(tbody);
    box.appendChild(table);
  }

  async function load() {
    try {
      const both = await Promise.all([A.api('/api/admin/users'), A.api('/api/admin/trusted-devices'),
        can('tokens.manage') ? A.api('/api/admin/tokens') : Promise.resolve({ tokens: [] })]);
      allTokens = both[2].tokens || [];
      users = both[0].users || [];
      roles = both[0].roles || [];
      perms = both[0].permissions || [];
      devices = both[1].devices || [];
      thisDevice = both[1].this === undefined ? null : both[1].this;
      renderUsers();
      renderDevices();
      renderAllTokens();
      renderRoleRef();
    } catch (e) { A.alertMsg(e.message); }
  }

  function initUsers() {
    const add = g('userAdd');
    if (add) add.addEventListener('click', function () { if (!roles.length) return; closeDevicePanel(); openAdd(); });
    const trust = g('deviceAdd');
    if (trust) trust.addEventListener('click', function () { closePanel(); openTrust(); });
    window.addEventListener('hashchange', function () {
      if (location.hash === '#users') load();
      else { clearSecret(); closePanel(); closeDevicePanel(); }
    });
    if (location.hash === '#users') load();
  }

  if (g('page-account')) initAccount();
  if (g('tokenList')) initTokens();
  if (g('page-users')) initUsers();
})();
