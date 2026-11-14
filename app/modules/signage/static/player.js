/* The signage player (#205). Plain JavaScript, no build step, for smart-TV browsers.

   Used two ways:
   - the full-screen page /signage/<slug> on the signage port, which follows the screen's event stream;
   - the admin page's preview, which hands it the state itself and passes `preview: true`.

   It shows one item while the next one loads in a hidden layer, so a change is a cut or a fade with no blank
   frame. Images it has shown are kept decoded in memory and media URLs are cached by the browser for good, so when
   Relay restarts the screen keeps looping what it already has until the stream comes back. Items outside their
   dates are skipped here too, by Relay's clock, in case Relay is down at midnight.

   Takeovers (#207) sit over the playlist: an alert banner, a file pushed to the screen, or a framed Relay view
   (live captions, the service rundown). The playlist stops where it is while a push or view is up and carries on
   from that item when it ends. */
(function (global) {
  'use strict';

  var FADE_MS = 800;
  var RETRY_MS = 5000;
  var REPORT_MS = 30000;
  var PROBE_MS = 5000;
  var SILENT_MS = 20000;

  function SignagePlayer(root, opts) {
    this.root = root;
    this.opts = opts || {};
    this.items = [];
    this.today = '';
    this.offset = 0;            // Relay's clock minus this device's
    this.sound = false;
    this.index = -1;
    this.current = null;        // {item, el} on screen
    this.showing = null;        // the item on screen or loading to replace it
    this.timer = null;
    this.failures = 0;
    this.images = {};           // url -> decoded <img>, kept so they show without the network
    this.layers = [this._layer(), this._layer()];
    this.front = 0;
    this.stopped = false;
    this.tk = null;             // the takeover on screen: {id, kind, el, timer}
    this.endedId = null;        // the last takeover this player finished, so a state still naming it is ignored
    this.over = document.createElement('div');
    this.over.style.cssText = 'position:absolute;inset:0;z-index:10;background:#000;display:none;overflow:hidden';
    this.root.appendChild(this.over);
    this.bar = document.createElement('div');
    this.bar.setAttribute('role', 'alert');
    this.bar.style.cssText = 'position:absolute;left:0;right:0;bottom:0;z-index:20;display:none;padding:2.2vh 3vw;' +
      'background:#c62828;color:#fff;font:700 6vh/1.15 system-ui,sans-serif;text-align:center;' +
      'box-shadow:0 -0.6vh 3vh rgba(0,0,0,.6)';
    this.root.appendChild(this.bar);
  }

  SignagePlayer.prototype._layer = function () {
    var d = document.createElement('div');
    d.className = 'sg-layer';
    d.style.cssText = 'position:absolute;inset:0;opacity:0;background:#000;overflow:hidden';
    this.root.appendChild(d);
    return d;
  };

  SignagePlayer.prototype.url = function (u) {
    return this.opts.mapUrl ? this.opts.mapUrl(u) : u;
  };

  /* A new state from Relay: {screen, playlist, items, today, server_time}. */
  SignagePlayer.prototype.load = function (state) {
    if (!state || state.gone) { this.items = []; this._blank(); return; }
    if (typeof state.server_time === 'number') this.offset = state.server_time - Date.now();
    if (!this.opts.preview) { this._banner(state.banner); this._takeover(state.takeover); }
    this.today = state.today || '';
    this.sound = !!(state.screen && state.screen.sound) && !this.opts.preview;
    // The item on screen, or the one loading to take its place: a new state that still has it carries on.
    var keepId = this.showing && this.showing.id;
    this.items = (state.items || []).slice();
    // Drop decoded images nothing plays any more.
    var wanted = {};
    for (var i = 0; i < this.items.length; i++) wanted[this.url(this.items[i].url)] = true;
    for (var k in this.images) if (!wanted[k]) delete this.images[k];
    var stillThere = false;
    for (var j = 0; j < this.items.length; j++) {
      if (this.items[j].id === keepId) { this.index = j; stillThere = true; break; }
    }
    if (!this.showing || !stillThere) this.next();
  };

  SignagePlayer.prototype._day = function () {
    var d = new Date(Date.now() + this.offset);
    function two(n) { return (n < 10 ? '0' : '') + n; }
    var local = d.getFullYear() + '-' + two(d.getMonth() + 1) + '-' + two(d.getDate());
    // Relay's own date wins while it is fresh; the device's corrected clock covers a Relay that is down.
    return local > this.today ? local : (this.today || local);
  };

  SignagePlayer.prototype._playable = function () {
    var day = this._day(), out = [];
    for (var i = 0; i < this.items.length; i++) {
      var it = this.items[i];
      if ((!it.start_date || it.start_date <= day) && (!it.end_date || day <= it.end_date)) out.push(i);
    }
    return out;
  };

  SignagePlayer.prototype._blank = function () {
    clearTimeout(this.timer);
    this._unwatch();
    for (var i = 0; i < this.layers.length; i++) { this.layers[i].style.opacity = '0'; this._clear(this.layers[i]); }
    this.current = null;
    this.showing = null;
    this.index = -1;
    this._report(null);
  };

  SignagePlayer.prototype._clear = function (layer) {
    var v = layer.querySelector('video');
    if (v) { try { v.pause(); v.removeAttribute('src'); v.load(); } catch (e) { /* gone */ } }
    layer.innerHTML = '';
  };

  SignagePlayer.prototype.next = function () {
    if (this.stopped) return;
    clearTimeout(this.timer);
    this._unwatch();
    var order = this._playable();
    if (!order.length) { this._blank(); return; }
    var pos = 0;
    for (var i = 0; i < order.length; i++) { if (order[i] > this.index) { pos = i; break; } }
    if (order.length && order[order.length - 1] <= this.index) pos = 0;
    this.index = order[pos];
    this._show(this.items[this.index]);
  };

  SignagePlayer.prototype._element = function (item, done, host) {
    var self = this, src = this.url(item.url), fit = item.fit === 'fill' ? 'cover' : 'contain';
    var css = 'position:absolute;inset:0;width:100%;height:100%;object-fit:' + fit + ';border:0;background:#000';
    if (item.type === 'image') {
      var cached = this.images[src];
      var img = document.createElement('img');
      img.alt = '';
      img.style.cssText = css;
      if (cached && cached.complete && cached.naturalWidth) { img.src = cached.src; done(img); return; }
      img.onload = function () {
        var keep = new Image(); keep.src = src; self.images[src] = keep;
        done(img);
      };
      img.onerror = function () { done(null); };
      img.src = src;
    } else if (item.type === 'video') {
      var v = document.createElement('video');
      v.style.cssText = css;
      v.muted = !this.sound;
      v.playsInline = true;
      v.setAttribute('playsinline', '');
      v.preload = 'auto';
      var settled = false;
      v.oncanplay = function () { if (!settled) { settled = true; done(v); } };
      v.onerror = function () { if (!settled) { settled = true; done(null); } };
      v.src = src;
      v.load();
    } else if (item.type === 'page') {
      var go = function () {
        var f = document.createElement('iframe');
        f.style.cssText = css;
        f.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-presentation');
        f.setAttribute('allow', 'autoplay; encrypted-media; fullscreen');
        f.setAttribute('referrerpolicy', 'strict-origin-when-cross-origin');
        f.onload = function () { done(f); };
        f.src = self.url(self.sound && item.url_sound ? item.url_sound : item.url);
        if (host) host.appendChild(f);   // a frame only loads once it is in the page; the hidden layer lets it
        setTimeout(function () { done(f); }, 8000);   // a page that never says it loaded still gets shown
      };
      // A page from the internet is skipped while the screen cannot reach it, and tried again on its next turn.
      if (item.probe) this._reachable(item.probe, function (ok) { if (ok) go(); else done(null); });
      else go();
    } else {
      done(null);
    }
  };

  /* Can this device reach `url`? A no-cors request settles on any answer and fails only with no connection. */
  SignagePlayer.prototype._reachable = function (url, cb) {
    if (navigator.onLine === false) { cb(false); return; }
    if (typeof fetch !== 'function') { cb(true); return; }
    var settled = false, ctl = typeof AbortController === 'function' ? new AbortController() : null;
    function end(ok) { if (!settled) { settled = true; cb(ok); } }
    setTimeout(function () { if (ctl) ctl.abort(); end(false); }, PROBE_MS);
    fetch(url, { mode: 'no-cors', cache: 'no-store', signal: ctl ? ctl.signal : undefined })
      .then(function () { end(true); }, function () { end(false); });
  };

  /* A frame that can say when it is finished (a YouTube video): `post_message` asks it to report, and a message
     from it matching one `end_when` entry ends the item. No message at all for a while counts as a failure. */
  SignagePlayer.prototype._watchEnd = function (item, el, only) {
    var self = this, origin;
    try { origin = new URL(el.src).origin; } catch (e) { return; }
    var heard = false;
    function get(o, path) {
      var parts = path.split('.');
      for (var i = 0; i < parts.length; i++) { if (o === null || typeof o !== 'object') return undefined; o = o[parts[i]]; }
      return o;
    }
    function matches(m) {
      for (var i = 0; i < item.end_when.length; i++) {
        var cond = item.end_when[i], all = true;
        for (var k in cond) if (get(m, k) !== cond[k]) { all = false; break; }
        if (all) return true;
      }
      return false;
    }
    function onMsg(ev) {
      if (ev.origin !== origin || ev.source !== el.contentWindow) return;
      var m = ev.data;
      if (typeof m === 'string') { try { m = JSON.parse(m); } catch (e) { return; } }
      if (!m || typeof m !== 'object') return;
      heard = true;
      if (!matches(m) || self.tk) return;
      if (only) { try { el.src = el.src; } catch (e) { /* gone */ } self._unwatch(); self._watchEnd(item, el, only); }
      else self.next();
    }
    function tell() {
      if (!item.post_message) return;
      try { el.contentWindow.postMessage(JSON.stringify(item.post_message), origin); } catch (e) { /* not ready */ }
    }
    window.addEventListener('message', onMsg);
    tell();
    var poke = setInterval(function () { if (!heard) tell(); }, 1000);
    var quiet = setTimeout(function () { if (!heard && !self.tk) self.next(); }, SILENT_MS);
    this._endWatch = function () { window.removeEventListener('message', onMsg); clearInterval(poke); clearTimeout(quiet); };
  };

  SignagePlayer.prototype._unwatch = function () {
    if (this._endWatch) { this._endWatch(); this._endWatch = null; }
  };

  SignagePlayer.prototype._show = function (item) {
    var self = this, token = {};
    this._pending = token;
    this.showing = item;
    var back = this.layers[1 - this.front];
    this._clear(back);
    var shown = false;
    this._element(item, function (el) {
      if (shown || self._pending !== token || self.stopped) return;
      shown = true;
      if (!el) {
        self.failures += 1;
        // Every item failing (Relay down and nothing cached): wait before trying again, never spin.
        var wait = self.failures >= Math.max(1, self._playable().length) ? RETRY_MS : 500;
        self.timer = setTimeout(function () { self.next(); }, wait);
        return;
      }
      self.failures = 0;
      if (el.parentNode !== back) back.appendChild(el);   // moving a loaded frame would reload it
      var fade = item.transition === 'fade' && self.current;
      back.style.transition = fade ? 'opacity ' + FADE_MS + 'ms ease' : 'none';
      var old = self.layers[self.front];
      back.style.zIndex = '2';
      old.style.zIndex = '1';
      // Force the start state before the transition begins.
      void back.offsetWidth;
      back.style.opacity = '1';
      setTimeout(function () { if (self.layers[self.front] !== old) { old.style.opacity = '0'; self._clear(old); } },
        fade ? FADE_MS + 50 : 0);
      self.front = 1 - self.front;
      self.current = { item: item, el: el };
      self._report(item);
      self._schedule(item, el);
    }, back);
  };

  SignagePlayer.prototype._schedule = function (item, el) {
    var self = this;
    clearTimeout(this.timer);
    this._unwatch();
    if (this.tk) return;        // a takeover is up: the playlist waits, and carries on when it ends
    var only = this._playable().length === 1;
    if (item.type === 'video') {
      el.loop = only && !(item.duration_s > 0);
      el.onended = function () { if (!only) self.next(); else { el.currentTime = 0; el.play(); } };
      var p = el.play();
      if (p && p.catch) {
        p.catch(function () { el.muted = true; el.play().catch(function () { self.timer = setTimeout(function () { self.next(); }, 1000); }); });
      }
      if (item.duration_s > 0) this.timer = setTimeout(function () { self.next(); }, item.duration_s * 1000);
      return;
    }
    if (item.type === 'page' && item.end_when && !(item.duration_s > 0)) { this._watchEnd(item, el, only); return; }
    if (only) return;   // one picture: just leave it up
    var secs = item.duration_s > 0 ? item.duration_s : 10;
    this.timer = setTimeout(function () { self.next(); }, secs * 1000);
  };

  /* ---------------------------------------------------------------- takeovers (#207) */
  SignagePlayer.prototype._banner = function (b) {
    if (!b || !b.text) { this.bar.style.display = 'none'; this.bar.textContent = ''; return; }
    this.bar.textContent = b.text + (b.count > 1 ? '  (+' + (b.count - 1) + ' more)' : '');
    this.bar.style.display = 'block';
  };

  SignagePlayer.prototype._takeover = function (t) {
    if (!t) { this._endTakeover(false); return; }
    if (this.tk && this.tk.id === t.id) return;      // the one already up
    if (this.endedId === t.id) return;               // already played and finished on this screen
    this._endTakeover(false);
    this._startTakeover(t);
  };

  SignagePlayer.prototype._relayMs = function (iso) {
    return iso ? Date.parse(iso) - (Date.now() + this.offset) : 0;
  };

  SignagePlayer.prototype._startTakeover = function (t) {
    var self = this, tk = { id: t.id, kind: t.kind, el: null, timer: null, t: t };
    this.tk = tk;
    clearTimeout(this.timer);
    this._unwatch();
    if (this.current && this.current.item.type === 'video') { try { this.current.el.pause(); } catch (e) { /* gone */ } }
    function finish(playedToEnd) { if (self.tk === tk) self._endTakeover(true, playedToEnd); }
    function untilEnd() {
      var ms = self._relayMs(t.until);
      if (t.until && ms <= 0) { finish(false); return; }
      if (t.until) tk.timer = setTimeout(function () { finish(false); }, ms);
    }
    if (t.kind === 'live') {
      var f = document.createElement('iframe');
      f.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;border:0;background:#000';
      f.setAttribute('sandbox', 'allow-scripts allow-same-origin');
      f.src = location.protocol + '//' + location.hostname + ':' + t.port + t.path;
      tk.el = f;
      this.over.appendChild(f);
      this.over.style.display = 'block';
      return;
    }
    var item = t.item;
    this._element(item, function (el) {
      if (self.tk !== tk) return;
      if (!el) { finish(false); return; }
      tk.el = el;
      self.over.appendChild(el);
      self.over.style.display = 'block';
      if (item.type === 'video') {
        var once = t.mode === 'once';
        el.loop = !once;
        el.onended = function () { if (once) finish(true); };
        var p = el.play();
        if (p && p.catch) p.catch(function () { el.muted = true; el.play().catch(function () { /* blocked */ }); });
        if (!once) untilEnd();
      } else if (t.mode === 'once') {
        tk.timer = setTimeout(function () { finish(true); }, (t.seconds || 10) * 1000);
      } else {
        untilEnd();
      }
    });
  };

  SignagePlayer.prototype._endTakeover = function (remember, playedToEnd) {
    var tk = this.tk;
    if (!tk) return;
    this.tk = null;
    clearTimeout(tk.timer);
    if (remember) this.endedId = tk.id;
    var v = this.over.querySelector('video');
    if (v) { try { v.pause(); v.removeAttribute('src'); v.load(); } catch (e) { /* gone */ } }
    this.over.innerHTML = '';
    this.over.style.display = 'none';
    if (playedToEnd && tk.t && tk.t.mode === 'once' && this.opts.doneUrl) {
      try {
        fetch(this.opts.doneUrl, { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: tk.id }), keepalive: true }).catch(function () { /* the next state says it */ });
      } catch (e) { /* old browser */ }
    }
    // The playlist picks up where it was.
    if (this.current) this._schedule(this.current.item, this.current.el);
    else this.next();
  };

  SignagePlayer.prototype._report = function (item) {
    if (!this.opts.nowUrl) return;
    var self = this;
    this._lastReport = item;
    var body = JSON.stringify({ item: item ? item.id : null, label: item ? (item.label || '') : '' });
    try {
      fetch(this.opts.nowUrl, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body,
        keepalive: true }).catch(function () { /* Relay is down; the next report says it */ });
    } catch (e) { /* old browser */ }
    clearInterval(this._reportTimer);
    this._reportTimer = setInterval(function () { self._report(self._lastReport); }, REPORT_MS);
  };

  SignagePlayer.prototype.stop = function () {
    this.stopped = true;
    clearTimeout(this.timer);
    this._unwatch();
    if (this.tk) clearTimeout(this.tk.timer);
    clearInterval(this._reportTimer);
    if (this.source) this.source.close();
    for (var i = 0; i < this.layers.length; i++) this._clear(this.layers[i]);
    this.root.innerHTML = '';
    this.tk = null;
  };

  /* Follow a screen: the state now, then every change Relay pushes. EventSource reconnects by itself. */
  SignagePlayer.prototype.follow = function (stateUrl, eventsUrl) {
    var self = this;
    fetch(stateUrl, { cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) { if (s) self.load(s); }).catch(function () { /* the stream will bring it */ });
    if (typeof EventSource === 'function') {
      this.source = new EventSource(eventsUrl);
      this.source.onmessage = function (ev) {
        try { self.load(JSON.parse(ev.data)); } catch (e) { /* a bad message changes nothing */ }
      };
    } else {
      setInterval(function () {
        fetch(stateUrl, { cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; })
          .then(function (s) { if (s) self.load(s); }).catch(function () {});
      }, 10000);
    }
  };

  global.SignagePlayer = SignagePlayer;
})(window);
