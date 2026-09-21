/* Viewer runtime: subscribe, render deltas the instant they land, auto-scroll.
   Never buffers to a sentence boundary -- that is the whole latency point. */
(function () {
  const cfg = window.RELAY;
  const root = document.documentElement;
  const LS = { font: 'relay.font', lang: 'relay.lang', theme: 'relay.theme', wake: 'relay.wake' };

  const store = {
    get(k, d) { try { return localStorage.getItem(k) ?? d; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  };

  // Headless displays (a house screen, a broadcast overlay) have no picker and
  // no A+/A- -- the URL fixes everything and nobody is standing there to tap.
  const kiosk = cfg.screen || cfg.overlay;
  const fixedLang = cfg.screen ? cfg.screenLang : (cfg.overlay ? cfg.overlayLang : '');

  // Overlay sizes from --ov-font (CSS); a screen with a pinned ?font= ignores
  // the per-device px a phone remembers; auto (viewport) sizing ignores px
  // entirely (see CSS). fontPx still feeds the scroll-slack maths below.
  let fontPx = cfg.overlay ? cfg.overlayFont
    : (cfg.screen && cfg.screenFont) ? cfg.screenFont
    : (parseInt(store.get(LS.font, cfg.fontPx), 10) || cfg.fontPx);
  function applyFont() {
    root.style.setProperty('--font-px', fontPx + 'px');
    if (!kiosk) store.set(LS.font, String(fontPx));  // never clobber a phone's pref
  }
  applyFont();

  document.querySelectorAll('[data-font]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      const step = btn.dataset.font === 'up' ? 4 : -4;
      fontPx = Math.max(18, Math.min(140, fontPx + step));
      applyFont();
      panes.forEach(function (p) { p.stick(); });
    });
  });

  /* ---- theme: dark wall by default, light for daylit rooms ---- */
  const themeBtn = document.getElementById('themeToggle');
  const themeMeta = document.querySelector('meta[name="theme-color"]');
  function applyTheme(t) {
    root.setAttribute('data-theme', t);
    store.set(LS.theme, t);
    if (themeMeta) themeMeta.setAttribute('content', t === 'light' ? '#ffffff' : '#000000');
  }
  applyTheme(store.get(LS.theme, 'dark'));
  if (themeBtn) themeBtn.addEventListener('click', function () {
    applyTheme(root.getAttribute('data-theme') === 'light' ? 'dark' : 'light');
  });

  /* ---- wake lock: hold the screen on through a long read ---- */
  const wakeBtn = document.getElementById('wakeToggle');
  const wakeSupported = 'wakeLock' in navigator;
  let wakeLock = null;
  let wakeWanted = store.get(LS.wake, '0') === '1';
  async function acquireWake() {
    if (!wakeSupported || !wakeWanted || wakeLock) return;
    try {
      wakeLock = await navigator.wakeLock.request('screen');
      wakeLock.addEventListener('release', function () { wakeLock = null; });
    } catch (e) { /* unsupported or denied -- fail quiet */ }
  }
  function releaseWake() {
    if (wakeLock) { wakeLock.release().catch(function () {}); wakeLock = null; }
  }
  function reflectWake() {
    if (!wakeBtn) return;
    wakeBtn.setAttribute('aria-pressed', wakeWanted ? 'true' : 'false');
    wakeBtn.classList.toggle('on', wakeWanted);
  }
  if (wakeBtn && wakeSupported) {
    wakeBtn.hidden = false;
    reflectWake();
    wakeBtn.addEventListener('click', function () {
      wakeWanted = !wakeWanted;
      store.set(LS.wake, wakeWanted ? '1' : '0');
      reflectWake();
      if (wakeWanted) acquireWake(); else releaseWake();
    });
    acquireWake();
  }

  /* ---- presentation mode: projector / confidence monitor ---- */
  const presentBtn = document.getElementById('presentBtn');
  const presentExit = document.getElementById('presentExit');
  function setPresent(on) {
    document.body.classList.toggle('present', on);
    if (presentExit) presentExit.hidden = !on;
    panes.forEach(function (p) { p.stick(); });
  }
  if (presentBtn) presentBtn.addEventListener('click', function () {
    setPresent(true);
    if (root.requestFullscreen) root.requestFullscreen().catch(function () {});
  });
  if (presentExit) presentExit.addEventListener('click', function () {
    setPresent(false);
    if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen().catch(function () {});
  });
  if (cfg.present && presentExit) presentExit.hidden = false;  // template set the body class
  document.addEventListener('fullscreenchange', function () {
    // Leaving fullscreen with Esc should drop present too -- but not when the
    // page was loaded straight into present (/present, ?present=1).
    if (!document.fullscreenElement && !cfg.present) setPresent(false);
  });

  const toast = document.getElementById('toast');
  let toastTimer = null;
  function showToast(msg) {
    if (!toast || kiosk) return;   // no audience-facing text on a house display or overlay
    toast.textContent = msg;
    toast.classList.add('show');
    clearTimeout(toastTimer);
  }
  function hideToast() {
    if (!toast || kiosk) return;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toast.classList.remove('show'); }, 900);
  }

  /* ---- screen mode: a quiet corner dot, lit only while a stream is down ---- */
  const screenFault = document.getElementById('screenFault');
  function setFault(down) {
    if (screenFault) screenFault.hidden = !down;
  }

  /* ---- overlay mode: hold-to-hide, and fit the fixed canvas to the source ----
     Nothing is on screen when nobody is speaking: every caption resets a timer,
     and after ?hold= ms of silence the captions fade to fully transparent. The
     background (transparent, a chroma fill, or the matte's black) is left alone
     so a keyer downstream never sees it flicker. */
  let holdTimer = null;
  function overlayShow() {
    if (!cfg.overlay) return;
    document.body.classList.remove('ov-hidden');
    clearTimeout(holdTimer);
    if (cfg.overlayHold > 0) {
      holdTimer = setTimeout(function () { document.body.classList.add('ov-hidden'); }, cfg.overlayHold);
    }
  }
  if (cfg.overlay) {
    // The page is a fixed pixel canvas; scale it to whatever the browser source
    // window is (in OBS at the canvas size this is a no-op scale of 1).
    const stage = document.querySelector('.panes');
    const fit = function () {
      if (!stage) return;
      const s = Math.min(window.innerWidth / cfg.overlayW, window.innerHeight / cfg.overlayH);
      const x = (window.innerWidth - cfg.overlayW * s) / 2;
      const y = (window.innerHeight - cfg.overlayH * s) / 2;
      stage.style.transform = 'translate(' + x + 'px,' + y + 'px) scale(' + s + ')';
    };
    fit();
    window.addEventListener('resize', fit);
  }

  /* ---- one pane = one subscription ---- */
  function Pane(el, stream, lang) {
    this.feed = el;
    this.log = el.querySelector('.log');   // aria-live region: settled lines only
    this.stream = stream;
    this.lang = lang;
    this.es = null;
    this.openEl = null;
    this.lastSeq = 0;
    this.pinned = true;
    this.retry = 500;
    // Screen/overlay want a bounded feed: keep the last N settled lines, drop
    // the rest. 0 (the phone default) means unbounded.
    this.lineCap = parseInt(el.dataset.lines, 10) || 0;
    const ph = el.querySelector('.empty');
    this.emptyText = ph ? ph.textContent : 'Waiting for audio…';
    const pane = el.closest('.pane');
    this.jumpBtn = pane ? pane.querySelector('.jump') : null;

    const self = this;
    el.addEventListener('scroll', function () {
      const slack = el.scrollHeight - el.scrollTop - el.clientHeight;
      // Slack scales with type size: a single 140px line is taller than a fixed
      // 260px, which would unpin the pane permanently at large font sizes.
      self.pinned = slack < Math.max(120, 3 * fontPx);
      self.reflectJump();
    }, { passive: true });

    if (this.jumpBtn) this.jumpBtn.addEventListener('click', function () {
      self.pinned = true;
      // The one place a smooth scroll is welcome -- a deliberate jump, not the
      // continuous live tracking. Honour reduced-motion.
      const smooth = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      self.feed.scrollTo({ top: self.feed.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
      self.reflectJump();
    });
  }

  Pane.prototype.reflectJump = function () {
    if (this.jumpBtn) this.jumpBtn.hidden = this.pinned;
  };

  Pane.prototype.stick = function () {
    if (this.pinned) this.feed.scrollTop = this.feed.scrollHeight;
    this.reflectJump();
  };

  Pane.prototype.clear = function () {
    this.log.innerHTML = '';
    if (this.openEl) { this.openEl.remove(); this.openEl = null; }
    this.dropPlaceholder();
    this.lastSeq = 0;
  };

  Pane.prototype.placeholder = function (text) {
    this.clear();
    const p = document.createElement('p');
    p.className = 'empty';
    p.textContent = text;
    this.feed.appendChild(p);
  };

  Pane.prototype.dropPlaceholder = function () {
    const ph = this.feed.querySelector('.empty');
    if (ph) ph.remove();
  };

  Pane.prototype.settle = function () {
    if (this.openEl) {
      // Move the committed line into the live region: assistive tech announces
      // the settled text once, never the partial deltas that built it.
      this.openEl.className = 'settled';
      this.openEl.removeAttribute('aria-hidden');
      this.log.appendChild(this.openEl);   // moves the node out of .feed into .log
      this.openEl = null;
      this.cap();
    }
  };

  // Keep at most lineCap lines on screen, oldest falling off. The in-progress
  // open line counts against the budget, so `lines=2` on an overlay means two
  // paragraphs visible, never three. Shared by the capped display modes
  // (screen, overlay). 0 (the phone default) means unbounded.
  Pane.prototype.cap = function () {
    if (!this.lineCap) return;
    const budget = Math.max(0, this.lineCap - (this.openEl ? 1 : 0));
    while (this.log.children.length > budget) this.log.firstElementChild.remove();
  };

  Pane.prototype.ensureOpen = function () {
    if (!this.openEl) {
      this.openEl = document.createElement('p');
      this.openEl.className = 'open';
      // In progress: kept outside the live region and hidden from AT until settle.
      this.openEl.setAttribute('aria-hidden', 'true');
      this.feed.appendChild(this.openEl);
      this.cap();   // a new open line evicts an old settled one to hold the budget
    }
    return this.openEl;
  };

  Pane.prototype.setLangAttr = function (iso, label) {
    if (iso) {
      this.feed.setAttribute('lang', iso);
      // On a translation-only page the document language should track the pane
      // so a screen reader voices the whole page correctly.
      if (cfg.mode === 'translation') root.setAttribute('lang', iso);
    }
    if (label && this.log) this.log.setAttribute('aria-label', label);
  };

  Pane.prototype.apply = function (msg) {
    this.dropPlaceholder();
    if (msg.type === 'snapshot') {
      this.clear();
      (msg.lines || []).forEach(function (line) {
        const p = document.createElement('p');
        p.className = 'settled';
        p.textContent = line.text;
        this.log.appendChild(p);
      }, this);
      this.cap();
      if (msg.open) this.ensureOpen().textContent = msg.open;
      if (!this.log.childNodes.length && !this.openEl && !kiosk) this.placeholder(this.emptyText);
      if (this.log.childNodes.length || this.openEl) overlayShow();
      this.stick();
      return;
    }
    if (msg.seq && msg.seq <= this.lastSeq) return;   // ordered + deduped
    this.lastSeq = msg.seq || this.lastSeq;

    if (msg.final) {
      const p = this.ensureOpen();
      p.textContent = msg.text;
      this.settle();
    } else {
      const p = this.ensureOpen();
      p.textContent = msg.text !== undefined ? msg.text : p.textContent + msg.delta;
    }
    overlayShow();   // any caption wakes the overlay and restarts the hold timer
    this.stick();
  };

  Pane.prototype.connect = function (lang) {
    const self = this;
    // An explicit (re)target is a fresh channel -- take the full snapshot.
    // A no-arg reconnect keeps lastSeq so the server can replay only the gap.
    if (lang !== undefined) { this.lang = lang; this.lastSeq = 0; }
    this.disconnect();
    if (this.stream === 'translation' && !this.lang) {
      // A headless display stays blank rather than showing operator-facing text.
      if (!kiosk) this.placeholder('No target language is live yet. Ask the operator to enable one.');
      return;
    }
    const qs = new URLSearchParams({ stream: this.stream });
    if (this.lang) qs.set('lang', this.lang);
    if (this.lastSeq) qs.set('last_id', this.lastSeq);
    const es = new EventSource('/stream?' + qs.toString());
    this.es = es;

    es.onopen = function () { self.retry = 500; hideToast(); setFault(false); };
    es.onmessage = function (ev) {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      // The server pushes target-set changes on the same connection; they are
      // control messages, not captions, so they never reach the pane renderer.
      if (msg.type === 'targets') { applyTargets(msg); return; }
      self.apply(msg);
    };
    es.onerror = function () {
      showToast('Reconnecting…');
      setFault(true);
      es.close();
      self.es = null;
      setTimeout(function () { self.connect(); }, self.retry);
      self.retry = Math.min(5000, self.retry * 2);
    };
  };

  Pane.prototype.disconnect = function () {
    if (this.es) { this.es.close(); this.es = null; }
  };

  /* ---- wire up the panes on this page ---- */
  const panes = [];
  document.querySelectorAll('[data-stream]').forEach(function (el) {
    panes.push(new Pane(el, el.dataset.stream, el.dataset.lang || ''));
  });

  const picker = document.getElementById('langPicker');
  const statusDot = document.getElementById('statusDot');
  const isoByTarget = {};   // target name -> ISO code, filled from /api/targets

  function translationPane() {
    return panes.filter(function (p) { return p.stream === 'translation'; })[0];
  }

  async function refreshTargets() {
    // First paint and visibilitychange resync. Steady-state target changes
    // arrive over the SSE connection (applyTargets), so there is no poll timer.
    let data;
    try {
      data = await fetch('/api/targets', { cache: 'no-store' }).then(function (r) { return r.json(); });
    } catch (e) {
      if (statusDot) statusDot.className = 'dot err';
      return;
    }
    applyTargets(data);
  }

  function applyTargets(data) {
    // Headless modes (screen, overlay) have no picker and no status bar. With a
    // pinned ?lang= the pane is already connected; without one, follow the first
    // live target so a display brought up before capture starts fills in itself.
    if (kiosk) {
      if (fixedLang) return;
      const live = data.live || [];
      const pane = translationPane();
      if (pane && live.length && pane.lang !== live[0].target) {
        pane.setLangAttr(live[0].iso, live[0].label + ' translation');
        pane.connect(live[0].target);
      }
      return;
    }
    if (statusDot) statusDot.className = 'dot ' + (data.running ? 'ok' : 'warn');

    if (!picker) return;
    const live = data.live || [];
    const signature = live.map(function (t) { return t.target + ':' + t.label; }).join('|');
    if (picker.dataset.signature === signature) return;
    picker.dataset.signature = signature;

    const wanted = store.get(LS.lang, '');
    picker.innerHTML = '';
    live.forEach(function (t) {
      isoByTarget[t.target] = t.iso || '';
      const o = document.createElement('option');
      o.value = t.target;
      o.textContent = t.label;
      picker.appendChild(o);
    });

    const pane = translationPane();
    if (!live.length) {
      picker.disabled = true;
      const o = document.createElement('option');
      o.textContent = 'No languages live';
      picker.appendChild(o);
      if (pane) pane.connect('');
      return;
    }
    picker.disabled = live.length === 1;
    const pick = live.some(function (t) { return t.target === wanted; }) ? wanted : live[0].target;
    picker.value = pick;
    const chosen = live.filter(function (t) { return t.target === pick; })[0];
    if (pane) {
      if (chosen) pane.setLangAttr(chosen.iso, chosen.label + ' translation');
      if (pane.lang !== pick) pane.connect(pick);
    }
  }

  if (picker) {
    picker.addEventListener('change', function () {
      // Target is already streaming server-side, so this is a resubscribe, not a spin-up.
      store.set(LS.lang, picker.value);
      const pane = translationPane();
      const chosen = (picker.selectedOptions[0] || {});
      if (pane) {
        pane.connect(picker.value);
        pane.setLangAttr(isoByTarget[picker.value] || '', (chosen.textContent || '') + ' translation');
      }
    });
  }

  panes.forEach(function (p) {
    if (p.stream === 'transcription') p.connect(cfg.sourceLanguage);
    // A headless mode with a pinned ?lang= connects the translation pane straight
    // to its fixed target -- there is no picker to drive it.
    else if (kiosk && p.lang) p.connect(p.lang);
  });
  refreshTargets();

  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') {
      panes.forEach(function (p) { if (!p.es && (p.stream !== 'translation' || p.lang)) p.connect(); });
      refreshTargets();
      acquireWake();  // the OS drops the lock when the tab hides
    }
  });
})();
