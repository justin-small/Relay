/* The signage player (#205). Plain JavaScript, no build step, for smart-TV browsers.

   Used two ways:
   - the full-screen page /signage/<slug> on the signage port, which follows the screen's event stream;
   - the admin page's preview, which hands it the state itself and passes `preview: true`.

   It shows one item while the next one loads in a hidden layer, so a change is a cut or a fade with no blank
   frame. Images it has shown are kept decoded in memory and media URLs are cached by the browser for good, so when
   Relay restarts the screen keeps looping what it already has until the stream comes back. Items outside their
   dates are skipped here too, by Relay's clock, in case Relay is down at midnight. */
(function (global) {
  'use strict';

  var FADE_MS = 800;
  var RETRY_MS = 5000;
  var REPORT_MS = 30000;

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
    var order = this._playable();
    if (!order.length) { this._blank(); return; }
    var pos = 0;
    for (var i = 0; i < order.length; i++) { if (order[i] > this.index) { pos = i; break; } }
    if (order.length && order[order.length - 1] <= this.index) pos = 0;
    this.index = order[pos];
    this._show(this.items[this.index]);
  };

  SignagePlayer.prototype._element = function (item, done) {
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
      var f = document.createElement('iframe');
      f.style.cssText = css;
      f.setAttribute('sandbox', 'allow-scripts allow-same-origin');
      f.setAttribute('allow', 'autoplay');
      f.onload = function () { done(f); };
      f.src = src;
      setTimeout(function () { done(f); }, 8000);   // a page that never says it loaded still gets shown
    } else {
      done(null);
    }
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
      back.appendChild(el);
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
    });
  };

  SignagePlayer.prototype._schedule = function (item, el) {
    var self = this;
    clearTimeout(this.timer);
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
    if (only) return;   // one picture: just leave it up
    var secs = item.duration_s > 0 ? item.duration_s : 10;
    this.timer = setTimeout(function () { self.next(); }, secs * 1000);
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
    clearInterval(this._reportTimer);
    if (this.source) this.source.close();
    for (var i = 0; i < this.layers.length; i++) this._clear(this.layers[i]);
    this.root.innerHTML = '';
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
