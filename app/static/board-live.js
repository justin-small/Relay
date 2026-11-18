/* Live data for a board's widgets (#156), shared by the board screen (/board/<slug>) and the board preview on the
   Boards page. Captions and the livestream come from the existing feeds (/stream); a module's widgets, which page
   to show, and the board's revision from /board/<slug>/_/data, asked every few seconds.

   const live = RelayBoardLive.start({
     base: '/board/lobby',             // the board's address
     items: function () { return [...]; },   // the items drawn now: their feeds are opened, others closed
     apply: function (id, data) {},    // feed one item live data (RelayLayout.update)
     screen: 'abc123…',                // optional: this screen's own id, so the Boards page lists it
     onPoll: function (answer) {},     // optional: the whole answer ({items, page, rotate, now, rev, reload})
   });
   live.refresh();                     // the items changed: open and close feeds to match
   live.stop();

   Every value from the server reaches the page as text (RelayLayout.update sets text only). */
(function () {
  'use strict';
  const POLL_MS = 3000;

  function start(opts) {
    const capFeeds = {};      // language -> {es, lines, open, retry}
    let statusEs = null, stopped = false, timer = 0;

    function items() { return opts.items() || []; }
    function capLines(f) { return f.lines.concat(f.open ? [f.open] : []); }
    function pushCaptions(lang) {
      const f = capFeeds[lang];
      items().forEach(function (it) { if (it.type === 'captions' && (it.props || {}).lang === lang) opts.apply(it.id, { lines: capLines(f) }); });
    }
    function wanted() {
      const langs = {}; let status = false;
      items().forEach(function (it) {
        if (it.type === 'captions') langs[(it.props || {}).lang || 'SOURCE'] = true;
        if (it.type === 'stream' || it.type === 'spl' || it.type === 'rta') status = true;
      });
      return { langs: langs, status: status };
    }
    function openCaptions(lang) {
      const f = capFeeds[lang] = capFeeds[lang] || { lines: [], open: '', retry: 1000, es: null };
      const qs = lang === 'SOURCE' ? 'stream=transcription' : 'stream=translation&lang=' + encodeURIComponent(lang);
      const es = new EventSource('/stream?' + qs);
      f.es = es;
      es.onopen = function () { f.retry = 1000; };
      es.onmessage = function (ev) {
        let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
        if (m.type === 'targets') return;
        if (m.type === 'snapshot') {
          f.lines = (m.lines || []).map(function (l) { return l.text; }).slice(-8); f.open = m.open || '';
        } else if (m.final) {
          f.lines = f.lines.concat([m.text]).slice(-8); f.open = '';
        } else {
          f.open = m.text !== undefined ? m.text : f.open + (m.delta || '');
        }
        pushCaptions(lang);
      };
      es.onerror = function () {
        es.close();
        if (f.es !== es) return;
        f.es = null;
        setTimeout(function () { if (!stopped && !f.es && wanted().langs[lang]) openCaptions(lang); }, f.retry);
        f.retry = Math.min(10000, f.retry * 2);
      };
    }
    function openStatus() {
      statusEs = new EventSource('/stream?stream=status&sound=1');
      statusEs.onmessage = function (ev) {
        let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
        const on = (m.streams || []).filter(function (s) { return s.on_air; });
        const since = on.map(function (s) { return s.live_since; }).filter(Boolean);
        const viewers = on.map(function (s) { return s.viewers; }).filter(function (v) { return typeof v === 'number'; });
        const d = { on_air: m.on_air, live_since: since.length ? Math.min.apply(null, since) : 0,
          viewers: viewers.length ? viewers.reduce(function (a, b) { return a + b; }, 0) : null };
        items().forEach(function (it) {
          if (it.type === 'stream') opts.apply(it.id, d);
          if (it.type === 'spl' || it.type === 'rta') opts.apply(it.id, { sound: m.sound || null });
        });
      };
    }
    function refresh() {
      if (stopped) return;
      const w = wanted();
      Object.keys(capFeeds).forEach(function (l) {
        if (!w.langs[l] && capFeeds[l].es) { capFeeds[l].es.close(); capFeeds[l].es = null; }
      });
      Object.keys(w.langs).forEach(function (l) { if (!capFeeds[l] || !capFeeds[l].es) openCaptions(l); else pushCaptions(l); });
      if (w.status && !statusEs) openStatus();
      if (!w.status && statusEs) { statusEs.close(); statusEs = null; }
    }
    async function poll() {
      if (stopped) return;
      if (!document.hidden) {
        try {
          const q = opts.screen ? '?screen=' + encodeURIComponent(opts.screen) : '';
          const res = await fetch(opts.base + '/_/data' + q, { cache: 'no-store', credentials: 'same-origin' });
          if (res.ok && !stopped) {
            const got = await res.json();
            const its = got.items || {};
            Object.keys(its).forEach(function (id) { opts.apply(id, its[id]); });
            if (opts.onPoll) opts.onPoll(got);
          }
        } catch (e) { /* the next tick tries again */ }
      }
      if (!stopped) timer = setTimeout(poll, POLL_MS);
    }
    function stop() {
      stopped = true; clearTimeout(timer);
      Object.keys(capFeeds).forEach(function (l) { if (capFeeds[l].es) capFeeds[l].es.close(); capFeeds[l].es = null; });
      if (statusEs) { statusEs.close(); statusEs = null; }
    }
    refresh(); poll();
    return { refresh: refresh, stop: stop };
  }

  // The page a rotation shows at a moment: every screen works it out from the clock, so they turn together.
  // `offset` is the server's clock less this one's, in seconds.
  function rotation(rotate, offset) {
    if (!rotate || !rotate.pages || !rotate.pages.length) return null;
    const t = Date.now() / 1000 + (offset || 0);
    return rotate.pages[Math.floor(t / Math.max(1, rotate.every)) % rotate.pages.length];
  }

  window.RelayBoardLive = { start: start, rotation: rotation };
})();
