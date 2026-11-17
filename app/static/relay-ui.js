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

  root.relayUI = { can: can, api: api, client: client, el: el, say: say, confirm: confirm };
})(typeof window !== 'undefined' ? window : globalThis);
