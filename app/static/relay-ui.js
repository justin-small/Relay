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

   The shared pickers (#546), drawn by the pk- classes in components.css. Each takes a host element and returns a
   handle; none styles itself and none reads a page's own list: the ports and channels are core's (the same names on
   every page).

     portPicker(host, opts)        choose one port, by its ref. opts: value, kind (fixes the connector kind), label, disabled,
                                   onChange(ref or null, item). Opens a drawer: gear on the left, its ports on the right.
                                   Handle: { value(), set(ref), lock(kind) }.
     channelPicker(host, opts)     choose one channel, by its ref. opts: value, kind ('audio', 'video' or 'talk'),
                                   direction ('source' or 'destination'), label, empty, onChange(ref or null).
                                   A fixed kind or direction shows as text, not a control. Handle: { value(), set(ref) }.
     routeEditor(host, opts)       destinations down, sources across, one dot per route; core stores the route and the
                                   module that owns the gear applies it. opts: kind, provider (only that module's ends),
                                   gear (only ends on that gear id), anySource (sources from every module), perm (a
                                   module permission that may route, besides network.manage), source(c) and
                                   destination(c) (keep a channel), extra(c) (a node under a destination's name),
                                   empty (the sentence when there is nothing to route), onChange(). Audio destinations
                                   mix several sources and take a gain; video takes one. Handle: { reload() }.

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


  // ---- the shared pickers (#546) -------------------------------------------------------------------------------
  const KIND_NAMES = { eth: 'Ethernet', sfp: 'SFP', sdi: 'SDI', hdmi: 'HDMI', xlr: 'XLR', trs: 'Jack', usb: 'USB', iec: 'Mains',
    dc: 'DC power', other: 'Connector', audio: 'Audio', video: 'Video', talk: 'Talk' };
  const SEND = client({ cache: 'no-store' });
  let drawer = null;
  // Pickers drawn together (a row each) share one read of a list: a read is reused for two seconds.
  const lists = {};
  function list(path) {
    const hit = lists[path], now = Date.now();
    if (hit && now - hit.at < 2000) return hit.p;
    const p = SEND('GET', path);
    lists[path] = { at: now, p: p };
    p.catch(function () { delete lists[path]; });
    return p;
  }

  function portItems(r) {
    const out = [];
    (r.gear || []).forEach(function (g) {
      g.ports.forEach(function (p) {
        out.push({ id: p.ref, kind: p.kind, group: g.name, title: p.shown, badge: KIND_NAMES[p.kind] || p.kind, missing: p.missing,
          text: g.name + ' ' + p.shown + ' ' + p.key });
      });
    });
    return out;
  }

  function channelItems(r) {
    return (r.channels || []).map(function (c) {
      const dirs = c.ends.map(function (e) { return e.direction; });
      const group = c.gear_name || (c.providers || []).join(', ') || 'Other';
      return { id: c.ref, kind: c.kind, dir: c.direction, dirs: dirs, group: group, title: c.shown, number: c.number,
        sub: c.ends.length + (c.ends.length === 1 ? ' end' : ' ends'), badge: (KIND_NAMES[c.kind] || c.kind) + ' ' + c.direction,
        missing: c.missing, text: group + ' ' + c.shown + ' ' + c.kind };
    });
  }

  function closeDrawer() {
    if (!drawer) return;
    drawer.veil.hidden = true; drawer.box.hidden = true;
    if (drawer.opener && drawer.opener.focus) drawer.opener.focus();
  }

  function ensureDrawer() {
    if (drawer) return drawer;
    const veil = el('div', 'pk-veil'), box = el('aside', 'pk-drawer');
    veil.hidden = box.hidden = true;
    box.setAttribute('role', 'dialog'); box.setAttribute('aria-modal', 'true');
    veil.onclick = closeDrawer;
    root.document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !box.hidden) closeDrawer(); });
    root.document.body.appendChild(veil); root.document.body.appendChild(box);
    drawer = { veil: veil, box: box, opener: null };
    return drawer;
  }

  function segment(label, options, value, onPick) {
    const seg = el('div', 'seg');
    seg.setAttribute('role', 'group'); seg.setAttribute('aria-label', label);
    [['', 'Any']].concat(options).forEach(function (o) {
      const b = el('button', '', o[1]); b.type = 'button';
      b.setAttribute('aria-pressed', String(o[0] === value));
      b.onclick = function () { onPick(o[0]); };
      seg.appendChild(b);
    });
    return seg;
  }

  // One browse drawer for both pickers; they differ in the items, the filters and the words.
  function browse(host, o) {
    const state = { value: o.value || null, items: [], fixed: o.fixed || {}, filter: {}, gear: '', ready: false, error: '' };
    const btn = el('button', 'pk-btn'); btn.type = 'button'; btn.disabled = !!o.disabled;
    btn.setAttribute('aria-haspopup', 'dialog'); btn.setAttribute('aria-label', o.label);
    host.appendChild(btn);

    function current() { return state.items.filter(function (x) { return x.id === state.value; })[0]; }
    function show() {
      btn.textContent = '';
      const c = current();
      btn.appendChild(el('span', 'pk-val' + (c ? '' : ' muted'), c ? c.title : (state.error || o.empty)));
      if (c) btn.appendChild(el('span', 'badge muted', c.badge));
      btn.appendChild(el('span', 'muted', '…'));
    }
    function load() {
      return o.load().then(function (items) { state.items = items; state.ready = true; state.error = ''; show(); })
        .catch(function (e) { state.error = e.message; show(); });
    }
    function visible(q) {
      return state.items.filter(function (x) {
        const fixed = state.fixed;
        for (const k in fixed) if (fixed[k] && x[k] !== fixed[k]) return false;
        for (const k in state.filter) if (state.filter[k] && x[k] !== state.filter[k]) return false;
        return !q || x.text.toLowerCase().indexOf(q) >= 0;
      });
    }
    function open() {
      const d = ensureDrawer();
      d.opener = btn; state.gear = '';
      const q = el('input', 'sm'); q.type = 'search'; q.placeholder = 'Search everything'; q.setAttribute('aria-label', 'Search');
      const tools = el('div', 'pk-tools', q);
      const side = el('div', 'pk-side'), body = el('div', 'pk-body');
      const close = el('button', 'sm quiet', 'Close'); close.type = 'button'; close.onclick = closeDrawer;
      const clear = el('button', 'sm quiet', 'Clear choice'); clear.type = 'button';
      clear.onclick = function () { state.value = null; show(); closeDrawer(); if (o.onChange) o.onChange(null); };
      function draw() {
        tools.textContent = ''; tools.appendChild(q);
        (o.filters || []).forEach(function (f) {
          if (state.fixed[f.key]) { tools.appendChild(el('span', 'badge muted', f.label + ': ' + (KIND_NAMES[state.fixed[f.key]] || state.fixed[f.key]))); return; }
          tools.appendChild(segment(f.label, f.options, state.filter[f.key] || '', function (v) { state.filter[f.key] = v; draw(); }));
        });
        const vis = visible(q.value.trim().toLowerCase()), groups = {}, order = [];
        vis.forEach(function (x) { (groups[x.group] = groups[x.group] || (order.push(x.group), [])).push(x); });
        if (state.gear && !groups[state.gear]) state.gear = '';
        side.textContent = '';
        function gbtn(name, label, count) {
          const b = el('button', 'pk-gb', label + ' ', el('span', 'note', String(count))); b.type = 'button';
          b.setAttribute('aria-pressed', String(state.gear === name));
          b.onclick = function () { state.gear = name; draw(); };
          side.appendChild(b);
        }
        gbtn('', 'All gear', vis.length);
        order.forEach(function (g) { gbtn(g, g, groups[g].length); });
        body.textContent = '';
        const shown = state.gear ? groups[state.gear] : vis;
        if (!shown.length) body.appendChild(el('p', 'note', state.ready ? o.none : 'Loading…'));
        shown.forEach(function (x) {
          const r = el('button', 'pk-row', el('span', 'pk-ot', el('span', 'strong', x.title), el('div', 'note', x.group + (x.sub ? ' · ' + x.sub : ''))),
            x.missing ? el('span', 'badge warn', 'missing') : null, el('span', 'badge muted', x.badge));
          r.type = 'button'; r.setAttribute('aria-pressed', String(x.id === state.value));
          r.onclick = function () { state.value = x.id; show(); closeDrawer(); if (o.onChange) o.onChange(x.id, x); };
          body.appendChild(r);
        });
      }
      q.oninput = draw;
      d.box.textContent = '';
      d.box.appendChild(el('div', 'pk-dh', el('strong', '', o.title), close));
      d.box.appendChild(tools);
      d.box.appendChild(el('div', 'pk-split', side, body));
      d.box.appendChild(el('div', 'pk-df', clear, el('span', 'note', 'Names are the gear\'s own. A Relay-only name shows with the gear\'s name beside it.')));
      draw(); d.veil.hidden = d.box.hidden = false; q.focus();
      if (!state.ready) load().then(draw);
    }
    btn.onclick = open;
    show(); load();
    return { value: function () { return state.value; }, set: function (v) { state.value = v || null; show(); },
      lock: function (k) { state.fixed = Object.assign({}, state.fixed, { kind: k || '' }); if (state.value && !current()) state.value = null; show(); } };
  }

  function portPicker(host, opts) {
    const o = opts || {};
    const kinds = Object.keys(KIND_NAMES).slice(0, 10).map(function (k) { return [k, KIND_NAMES[k]]; });
    return browse(host, { label: o.label || 'Port', title: (o.label || 'Port') + ': choose a port', empty: 'Choose a port', none: 'No port matches.',
      value: o.value, disabled: o.disabled, fixed: { kind: o.kind || '' }, onChange: o.onChange, filters: [{ key: 'kind', label: 'Kind', options: kinds }],
      load: function () { return list('/api/admin/network/cables').then(portItems); } });
  }

  function channelPicker(host, opts) {
    const o = opts || {};
    return browse(host, { label: o.label || 'Channel', title: (o.label || 'Channel') + ': choose a channel', empty: o.empty || 'Choose a channel',
      none: 'No channel matches.', value: o.value, disabled: o.disabled, fixed: { kind: o.kind || '', dir: o.direction || '' }, onChange: o.onChange,
      filters: [{ key: 'kind', label: 'Kind', options: [['audio', 'Audio'], ['video', 'Video'], ['talk', 'Talk']] },
        { key: 'dir', label: 'Direction', options: [['source', 'Source'], ['destination', 'Destination']] }],
      load: function () { return list('/api/admin/network/channels').then(channelItems); } });
  }

  const STATE_TEXT = { same: ['ok', 'Applied by the gear'], differs: ['warn', 'Gear differs'], wanted: ['info', 'Waiting for the gear'],
    actual: ['muted', 'Set on the gear'], none: ['muted', 'No source'] };

  function routeEditor(host, opts) {
    const o = opts || {};
    const box = el('div', 'tablescroll'), msg = el('div', 'note err'); msg.hidden = true;
    host.appendChild(box); host.appendChild(msg);
    function fail(e) { say(msg, e.message, true, { hide: true }); }
    function change(path, body) {
      return SEND('POST', path, body).then(function () { if (o.onChange) o.onChange(); return load(); }).catch(fail);
    }
    function draw(chans, routes) {
      const byDest = {}; routes.forEach(function (r) { byDest[r.destination] = r; });
      const kind = o.kind || '', may = can('network.manage') || (!!o.perm && can(o.perm));
      // A channel counts here by its own ends: an end of this direction, on this module and gear, that is reported now.
      function has(c, dir, scoped) {
        return c.ends.some(function (e) {
          return e.direction === dir && !e.missing && (!scoped || ((!o.provider || e.provider === o.provider) && (o.gear == null || e.gear === o.gear)));
        });
      }
      const live = function (c) { return !c.missing && (!kind || c.kind === kind); };
      const srcs = chans.filter(function (c) { return live(c) && has(c, 'source', !o.anySource) && (!o.source || o.source(c)); });
      const dests = chans.filter(function (c) { return live(c) && has(c, 'destination', true) && (!o.destination || o.destination(c)); });
      box.textContent = '';
      if (!dests.length) { box.appendChild(el('p', 'note', o.empty || 'Nothing to route yet: a module has to report a source and a destination.')); return; }
      if (!srcs.length) { box.appendChild(el('p', 'note', 'No source to route from yet.')); }
      const head = el('tr', '', el('th', '', 'Destination'));
      srcs.forEach(function (s) { head.appendChild(el('th', 'pk-xh', el('span', '', s.shown), s.gear_name !== s.shown ? el('small', '', s.gear_name) : null)); });
      head.appendChild(el('th', '', 'State'));
      const body = el('tbody');
      dests.forEach(function (d) {
        const v = byDest[d.ref] || { sources: [], state: 'none', gain: false };
        const row = el('tr', '', el('td', '', el('div', 'strong', d.shown), d.gear_name !== d.shown ? el('div', 'note', d.gear_name) : null,
          o.extra ? o.extra(d) : null));
        srcs.forEach(function (s) {
          const on = v.sources.filter(function (x) { return x.ref === s.ref; })[0];
          const b = el('button', 'pk-x' + (on ? ' on' : '')); b.type = 'button';
          b.setAttribute('aria-pressed', String(!!on)); b.setAttribute('aria-label', s.shown + ' to ' + d.shown);
          b.disabled = !may;
          b.onclick = function () { change(on ? '/api/admin/routes/clear' : '/api/admin/routes', { destination: d.ref, source: s.ref }); };
          const cell = el('td', 'pk-xc', b);
          if (on && v.gain) {
            const g = el('input', 'sm pk-g'); g.type = 'number'; g.value = on.gain_db === null || on.gain_db === undefined ? 0 : on.gain_db;
            g.setAttribute('aria-label', 'Gain in dB for ' + s.shown); g.disabled = !may;
            g.onchange = function () { change('/api/admin/routes', { destination: d.ref, source: s.ref, gain_db: Number(g.value) }); };
            cell.appendChild(g);
          }
          row.appendChild(cell);
        });
        const st = STATE_TEXT[v.state] || STATE_TEXT.none;
        row.appendChild(el('td', '', el('span', 'badge ' + st[0], st[1]),
          v.state === 'differs' ? el('div', 'note', 'Gear: ' + ((v.actual || []).map(function (a) { return a.name; }).join(', ') || 'nothing')) : null));
        body.appendChild(row);
      });
      box.appendChild(el('table', 'pk-mx', el('thead', '', head), body));
    }
    function load() {
      return Promise.all([SEND('GET', '/api/admin/network/channels'), SEND('GET', '/api/admin/routes' + (o.provider ? '?provider=' + encodeURIComponent(o.provider) : ''))])
        .then(function (r) { say(msg, '', false, { hide: true }); draw(r[0].channels || [], r[1].routes || []); }).catch(fail);
    }
    load();
    return { reload: load };
  }

  root.relayUI = { can: can, api: api, client: client, el: el, say: say, confirm: confirm,
    button: button, buttons: buttons, note: note, field: field, pill: pill, link: link, kv: kv, shown: shown,
    picker: picker, pickedName: pickedName,
    portPicker: portPicker, channelPicker: channelPicker, routeEditor: routeEditor };
})(typeof window !== 'undefined' ? window : globalThis);
