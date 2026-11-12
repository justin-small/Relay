/* The ProPresenter slide view: the live slide and the next one as images (as text when no image can be drawn),
   with a countdown over the live slide and the progress of a video playing on it. RelaySlideView.create(root,
   {base, active}) polls `base` (the snapshot) once a second while `active()` is true and keeps the clock and the
   progress bar moving between polls from the reading's own age. The pictures are fetched from Relay, never from
   ProPresenter. Everything from the server is set as text or as an image URL under `base`, never as markup. */
(function () {
  'use strict';
  function h(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function clock(sec) {
    const neg = sec < 0;
    let s = Math.floor(Math.abs(sec));
    const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
    const two = function (n) { return (n < 10 ? '0' : '') + n; };
    return (neg ? '-' : '') + (hh ? hh + ':' + two(mm) : mm) + ':' + two(ss);
  }
  const RUNNING = { running: 1, overrunning: 1 };

  function pane(cls, label) {
    const wrap = h('div', 'sv-' + cls);
    wrap.appendChild(h('div', 'sv-label', label));
    const frame = h('div', 'sv-frame');
    const img = document.createElement('img');
    img.alt = '';
    img.hidden = true;
    const text = h('div', 'sv-text');
    frame.appendChild(img);
    frame.appendChild(text);
    wrap.appendChild(frame);
    img.addEventListener('load', function () { img.hidden = false; frame.classList.add('sv-img'); });
    img.addEventListener('error', function () { img.hidden = true; frame.classList.remove('sv-img'); });
    return { wrap: wrap, frame: frame, img: img, text: text, src: '' };
  }
  function setPane(p, side, base) {
    p.text.textContent = side && side.text ? side.text : '';
    const url = side && side.image && side.image.indexOf(base + '/') === 0 ? side.image : '';
    if (url !== p.src) {
      p.src = url;
      p.img.hidden = true;
      p.frame.classList.remove('sv-img');
      if (url) p.img.src = url; else p.img.removeAttribute('src');
    }
  }

  function create(root, opts) {
    const base = opts.base;
    const active = opts.active || function () { return true; };
    root.textContent = '';
    root.classList.add('sv');
    const head = h('div', 'sv-head');
    const title = h('span', 'sv-title'), part = h('span', 'sv-part'), count = h('span', 'sv-count');
    head.appendChild(title); head.appendChild(part); head.appendChild(count);
    const cur = pane('current', 'Live'), nxt = pane('next', 'Next');
    const timer = h('div', 'sv-timer'), tName = h('small'), tVal = h('span');
    timer.appendChild(tName); timer.appendChild(tVal); timer.hidden = true;
    cur.frame.appendChild(timer);
    const media = h('div', 'sv-media'), bar = h('div', 'bar'), fill = document.createElement('i'), mTxt = h('span');
    bar.appendChild(fill); media.appendChild(bar); media.appendChild(mTxt); media.hidden = true;
    cur.frame.appendChild(media);
    const note = h('div', 'sv-note');
    root.appendChild(head); root.appendChild(cur.wrap); root.appendChild(nxt.wrap); root.appendChild(note);

    let snap = null, got = 0, pollT = null, tickT = null, stopped = false;

    function paint() {
      if (!snap) return;
      const age = Math.min(Math.max((performance.now() - got) / 1000, 0), 5);
      const t = snap.timer;
      timer.hidden = !t;
      if (t) {
        const v = t.value + (RUNNING[t.state] ? (t.dir === 'up' ? age : -age) : 0);
        tName.textContent = t.name;
        tVal.textContent = clock(v);
        timer.className = 'sv-timer' + (t.level === 'amber' || t.level === 'red' ? ' ' + t.level : '')
          + (RUNNING[t.state] ? '' : ' idle');
      }
      const m = snap.media;
      media.hidden = !m;
      if (m) {
        const at = Math.min(Math.max(m.time + (m.playing ? age : 0), 0), m.duration);
        fill.style.width = (m.duration > 0 ? (100 * at / m.duration) : 0).toFixed(1) + '%';
        mTxt.textContent = clock(at) + ' / ' + clock(m.duration);
        media.classList.toggle('paused', !m.playing);
        media.title = m.name || '';
      }
    }
    function render() {
      const s = snap;
      if (!s.configured) { note.textContent = 'ProPresenter is not set up.'; }
      else if (!s.connected) { note.textContent = 'Waiting for ProPresenter.'; }
      else if (!s.live) { note.textContent = 'Nothing is up.'; }
      else { note.textContent = ''; }
      title.textContent = s.live ? s.title : '';
      part.textContent = s.live ? s.part : '';
      part.hidden = !(s.live && s.part);
      if (s.part_color) part.style.setProperty('--sv-part', s.part_color); else part.style.removeProperty('--sv-part');
      count.textContent = s.live && s.slide_number ? 'slide ' + s.slide_number + (s.slide_count ? ' of ' + s.slide_count : '') : '';
      setPane(cur, s.live ? s.current : null, base);
      setPane(nxt, s.live ? s.next : null, base);
      nxt.wrap.hidden = !s.live;
      paint();
    }
    async function poll() {
      clearTimeout(pollT);
      if (stopped) return;
      if (active() && !document.hidden) {
        try {
          const res = await fetch(base, { cache: 'no-store' });
          if (!res.ok) throw new Error('HTTP ' + res.status);
          snap = await res.json();
          got = performance.now();
          render();
        } catch (e) {
          note.textContent = 'Cannot reach Relay.';
        }
      }
      pollT = setTimeout(poll, 1000);
    }
    tickT = setInterval(function () { if (!stopped && active() && !document.hidden) paint(); }, 250);
    poll();
    return {
      refresh: poll,
      stop: function () { stopped = true; clearTimeout(pollT); clearInterval(tickT); },
    };
  }
  window.RelaySlideView = { create: create, clock: clock };
})();
