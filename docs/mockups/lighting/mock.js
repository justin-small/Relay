/* Sample data and a stand-in cue-grid drawing for the lighting mockups. Not app code. */
(function () {
  var C = {
    walk: { n: 'Walk-in', bg: '#7c3aed' }, house: { n: 'House half', bg: '#475569' }, worship: { n: 'Worship full', bg: '#2b5fd9' },
    soft: { n: 'Worship soft', bg: '#0e7490' }, sermon: { n: 'Sermon', bg: '#b45309' }, bapt: { n: 'Baptism', bg: '#0891b2' },
    bo: { n: 'Blackout', bg: '#18181b' }, haze: { n: 'Haze on', bg: '#4b5563' }, chase: { n: 'Chase', bg: '#be185d' },
    post: { n: 'Postlude', bg: '#15803d' }, alt: { n: 'Altar call', bg: '#9a3412' }, cnt: { n: 'Countdown', bg: '#1d4ed8' },
    yt1: { n: 'Youth walk-in', bg: '#a21caf' }, yt2: { n: 'Game time', bg: '#ca8a04' }, yt3: { n: 'Band', bg: '#dc2626' }, yt4: { n: 'Talk', bg: '#b45309' }
  };
  var PAGES = [
    { id: 'sun', name: 'Sunday service', cols: 4, rows: 3, screen: true, presses: 31, last: 'Sermon · 9:11', where: 'Booth iPad',
      cues: [['walk', 0, 0, 1, 1], ['house', 1, 0, 1, 1], ['cnt', 2, 0, 1, 1], ['bo', 3, 0, 1, 1, 'rect'],
        ['worship', 0, 1, 2, 1], ['soft', 2, 1, 1, 1], ['sermon', 3, 1, 1, 1, '', 1],
        ['bapt', 0, 2, 1, 1], ['alt', 1, 2, 1, 1], ['post', 2, 2, 2, 1, 'pill']], levels: [['House level', 60], ['Stage wash', 85]] },
    { id: 'youth', name: 'Youth night', cols: 3, rows: 2, screen: false, presses: 0, last: 'never', where: '',
      cues: [['yt1', 0, 0, 1, 1], ['yt2', 1, 0, 1, 1], ['yt3', 2, 0, 1, 1], ['yt4', 0, 1, 1, 1], ['chase', 1, 1, 1, 1], ['bo', 2, 1, 1, 1, 'rect']], levels: [] },
    { id: 'sm', name: 'Stage manager', cols: 4, rows: 2, screen: true, presses: 4, last: 'Haze on · 8:40', where: 'Side-stage tablet',
      cues: [['haze', 0, 0, 2, 1], ['house', 2, 0, 2, 1], ['bo', 0, 1, 4, 1, 'rect']], levels: [['Haze output', 40]] }
  ];
  var UNPLACED = ['Strobe hit', 'Fog burst', 'Cross-fade 3s', 'Pastor special', 'Choir wash', 'Front only', 'Back only', 'Rainbow'];

  function draw(target, page, opts) {
    opts = opts || {};
    var g = document.createElement('div');
    g.className = 'cg' + (opts.edit ? ' edit' : '') + (opts.wide ? ' wide' : '');
    g.style.gridTemplateColumns = 'repeat(' + page.cols + ',1fr)';
    g.style.gridTemplateRows = 'repeat(' + page.rows + ',1fr)';
    var used = {};
    page.cues.forEach(function (c, i) {
      var d = C[c[0]];
      var b = document.createElement(opts.mini ? 'div' : 'button');
      if (!opts.mini) b.type = 'button';
      b.className = 'cue' + (c[5] ? ' ' + c[5] : '') + (c[6] ? ' lit' : '') + (opts.edit && i === opts.sel ? ' sel' : '');
      b.style.background = d.bg; b.style.gridColumn = (c[1] + 1) + ' / span ' + c[3]; b.style.gridRow = (c[2] + 1) + ' / span ' + c[4];
      b.textContent = d.n;
      if (!opts.mini && !opts.edit) b.addEventListener('click', function () {
        g.querySelectorAll('.cue').forEach(function (x) { x.classList.remove('lit'); }); b.classList.add('lit');
        if (opts.onPress) opts.onPress(d.n);
      });
      g.appendChild(b);
      for (var x = 0; x < c[3]; x++) for (var y = 0; y < c[4]; y++) used[(c[1] + x) + ',' + (c[2] + y)] = 1;
    });
    if (opts.edit) for (var y = 0; y < page.rows; y++) for (var x = 0; x < page.cols; x++) if (!used[x + ',' + y]) {
      var s = document.createElement('div'); s.className = 'slot'; s.style.gridColumn = x + 1; s.style.gridRow = y + 1; g.appendChild(s);
    }
    var st = document.createElement('div'); st.className = 'stage'; st.appendChild(g);
    target.textContent = ''; target.appendChild(st);
    return g;
  }
  function faders(target, page) {
    target.textContent = '';
    page.levels.forEach(function (l) {
      var f = document.createElement('label'); f.className = 'fader';
      var n = document.createElement('span'); n.textContent = l[0];
      var r = document.createElement('input'); r.type = 'range'; r.min = 0; r.max = 100; r.value = l[1];
      var o = document.createElement('output'); o.textContent = l[1] + '%';
      r.oninput = function () { o.textContent = r.value + '%'; };
      f.appendChild(n); f.appendChild(r); f.appendChild(o); target.appendChild(f);
    });
  }
  function pageById(id) { return PAGES.filter(function (p) { return p.id === id; })[0] || PAGES[0]; }
  function seg(root, on) {
    root.addEventListener('click', function (e) {
      var b = e.target.closest('button[data-p]'); if (!b || !root.contains(b)) return;
      root.querySelectorAll('button[data-p]').forEach(function (x) { x.classList.toggle('on', x === b); });
      on(b.dataset.p);
    });
  }
  window.Mock = { C: C, PAGES: PAGES, UNPLACED: UNPLACED, draw: draw, faders: faders, pageById: pageById, seg: seg };
})();
