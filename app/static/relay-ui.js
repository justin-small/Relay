/* Shared helpers for the panel's scripts and every module script (#461), as window.relayUI:

     can(perm)                     may the signed-in user do this? Only tidies the screen: the server still checks.
     api(method, path, body, opts) one JSON request. Throws an Error with the server's sentence (or "That did not
                                   work."), its .status and its .data. opts: cache ('no-store'), session (true: a
                                   session that has ended sends the panel to the sign-in page), note (an element:
                                   the error is written there and the call answers null), reload (statuses that
                                   reload the page instead), status (true: with no sentence from the server the
                                   error is "HTTP <status>").
     client(opts)                  api with those opts fixed: client(opts)(method, path, body).
     el(tag, attrs, ...children)   an element. attrs is a class name, or an object of properties ({ type, title,
                                   text }); children are nodes or text (null, false and '' are skipped). Everything
                                   becomes text, never markup.
     say(target, text, bad, opts)  a status line: target is an element or its id. Marked "err" when bad, else "ok"
                                   when there is text. opts: plain (leave the classes alone), hide (hidden while
                                   empty), ms (show it, then hide it after that many milliseconds).
     confirm(text, opts)           a promise for the operator's yes or no. The native dialog for now, so a styled
                                   one can replace it here once. opts.danger marks a destructive question.

   The shared controls (#463), drawn by the classes in components.css so a script never styles one itself:

     button(text, fn, opts)        a type=button button. opts is a class name ('primary', 'danger', 'quiet', 'sm',
                                   or several) or { cls, error }. With error (a function given the message, or '' to
                                   clear it), a click clears it, disables the button while fn runs, and shows what
                                   fn threw; without, fn is a plain click listener.
     field(label, control, id)     a .field: the label above the control, joined to it (the id is set when given).
     pill(text, cls)               a status pill with its dot: cls is 'on', 'warn', 'err', 'idle' or ''.
     link(text, href)              a link that opens in a new tab, without handing the page to it.
     buttons(error)                button with { error } fixed: buttons(showError)(text, fn, cls).
     note(target)                  a function that shows an error line in target (an element or its id), and
                                   hides it while there is none.
     kv(dl, term, value)           a dt/dd pair added to a <dl>, skipped when the value is empty.
     shown(node)                   is node on screen: its tab panel (if any) open and the page in front.
     picker(select, picks, saved, noneLabel)
                                   fills a <select> with Planning Center teams or positions to choose from (#525):
                                   picks are { ref, label, name? }, saved is { ref, name } or null, and an empty first
                                   option says noneLabel. A saved name with no ref gets an option of its own.
     pickedName(select)            the name kept with the chosen option, "" for a listed one (the ref is the value).

   Do not copy one of these into a module script: use it from here, and add what is missing here
   (tests/test_ui_contract.py enforces this). Loaded before every script that uses it. */
(function (root) {
  'use strict';

  function can(perm) { return typeof root.relayCan !== 'function' || !!root.relayCan(perm); }

  async function api(method, path, body, opts) {
    const o = opts || {};
    const init = { method: method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
      body: body === undefined || body === null ? undefined : JSON.stringify(body) };
    if (o.cache) init.cache = o.cache;
    const res = await root.fetch(path, init);
    if (o.reload && o.reload.indexOf(res.status) !== -1) { root.location.reload(); return null; }
    const out = await res.json().catch(function () { return {}; });
    if (!res.ok) {
      if (o.session && root.RelayAdmin && root.RelayAdmin.sessionEnded) root.RelayAdmin.sessionEnded(res, out);
      const text = out.error || out.detail || (o.status ? 'HTTP ' + res.status : 'That did not work.');
      if (o.note) { o.note.textContent = text; return null; }
      const err = new Error(text);
      err.status = res.status; err.data = out;
      throw err;
    }
    if (o.note) o.note.textContent = '';
    return out;
  }

  function client(opts) { return function (method, path, body) { return api(method, path, body, opts); }; }

  function add(node, child) {
    if (child === null || child === undefined || child === false || child === '') return;
    if (Array.isArray(child)) { child.forEach(function (c) { add(node, c); }); return; }
    node.appendChild(typeof child === 'object' && child.nodeType ? child : root.document.createTextNode(String(child)));
  }

  function el(tag, attrs) {
    const node = root.document.createElement(tag);
    if (typeof attrs === 'string') { if (attrs) node.className = attrs; }
    else if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === 'text') { if (attrs.text !== undefined && attrs.text !== null) node.textContent = attrs.text; }
        else node[k] = attrs[k];
      });
    }
    for (let i = 2; i < arguments.length; i++) add(node, arguments[i]);
    return node;
  }

  function say(target, text, bad, opts) {
    const node = typeof target === 'string' ? root.document.getElementById(target) : target;
    if (!node) return;
    const o = opts || {};
    const t = text === undefined || text === null ? '' : String(text);
    if (node.textContent !== t) node.textContent = t;   // the same words are not announced again
    if (!o.plain) {
      node.classList.toggle('err', !!bad);
      node.classList.toggle('ok', !bad && !!t);
    }
    if (o.hide) node.hidden = !t;
    if (o.ms) {
      node.hidden = false;
      clearTimeout(node.relaySayTimer);
      node.relaySayTimer = setTimeout(function () { node.hidden = true; }, o.ms);
    }
  }

  function confirm(text) { return Promise.resolve(!!root.confirm(text)); }

  function button(text, fn, opts) {
    const o = typeof opts === 'string' || !opts ? { cls: opts || '' } : opts;
    const b = el('button', o.cls || '', text);
    b.type = 'button';
    if (typeof o.error === 'function') {
      b.onclick = async function () {
        o.error(''); b.disabled = true;
        try { await fn(); } catch (e) { o.error(e.message); }
        b.disabled = false;
      };
    } else if (fn) b.addEventListener('click', fn);
    return b;
  }

  function buttons(error) { return function (text, fn, cls) { return button(text, fn, { cls: cls, error: error }); }; }

  function note(target) { return function (text) { say(target, text, true, { plain: true, hide: true }); }; }

  function field(label, control, id) {
    const l = el('label', '', label);
    if (id) control.id = id;
    if (control.id) l.htmlFor = control.id;
    return el('div', 'field', l, control);
  }

  function pill(text, cls) { return el('span', 'spill ' + (cls || ''), el('span', 'dot'), text); }

  function link(text, href) {
    const a = el('a', '', text);
    a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer';
    return a;
  }

  function kv(dl, term, value) {
    if (!value) return;
    dl.appendChild(el('dt', '', term));
    dl.appendChild(el('dd', '', value));
  }

  function shown(node) {
    const panel = node.closest('[role=tabpanel]');
    return !(panel && panel.hidden) && root.document.visibilityState === 'visible';
  }

  function picker(select, picks, saved, noneLabel) {
    select.textContent = '';
    const none = el('option', '', noneLabel || 'None'); none.value = ''; select.appendChild(none);
    picks.forEach(function (p) {
      const o = el('option', '', p.label); o.value = p.ref; o.dataset.name = p.name || ''; select.appendChild(o);
    });
    if (saved && !saved.ref && saved.name) {
      const o = el('option', '', saved.name + ' (not matched to Planning Center)');
      o.value = ''; o.dataset.name = saved.name; select.appendChild(o); o.selected = true;
    } else select.value = saved ? saved.ref : '';
  }

  function pickedName(select) {
    const o = select.options[select.selectedIndex];
    return o ? (o.dataset.name || '') : '';
  }

  root.relayUI = { can: can, api: api, client: client, el: el, say: say, confirm: confirm,
    button: button, buttons: buttons, note: note, field: field, pill: pill, link: link, kv: kv, shown: shown,
    picker: picker, pickedName: pickedName };
})(typeof window !== 'undefined' ? window : globalThis);
