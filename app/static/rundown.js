/* /screen?rundown=full|side|micboard (#148): lays out the "rundown" stream a module offers (Planning Center).
   A kiosk: nothing scrolls and nothing is pressed. The stage is 1920x1080, scaled to the screen.

   The order of service is drawn to scale (the "ruler"): an item is as tall as its minutes, beside a lane with the
   plan, and a slanted line joins the two wherever the service has slipped. On the micboard each mic carries its own
   strip of the service, lit where it is drawn (songs) or in use now. People show their Planning Center photo, over
   their initials for when there is none or it will not load.

   Everything is built with textContent: names and notes come from a church's own plan. */
(function () {
  'use strict';

  const body = document.body;
  const view = body.dataset.view, layout = body.dataset.layout, cards = body.dataset.cards;
  const wantTeam = (body.dataset.team || '').toLowerCase();
  const stage = document.getElementById('stage');
  const $ = (id) => document.getElementById(id);
  const SVG = 'http://www.w3.org/2000/svg';
  const COLOURS = ['#d9488f', '#8b6cf0', '#2bb3c4', '#ee8a4a', '#3fbf6f', '#4f8de8', '#a76ae0', '#e0b43a',
                   '#e85a6e', '#2fae9c', '#c98a3a', '#7c9a6a'];

  let msg = null, skew = 0, zone = undefined;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function colour(name) {
    let h = 0;
    for (const c of name || '') h = (h * 31 + c.charCodeAt(0)) >>> 0;
    return COLOURS[h % COLOURS.length];
  }
  function initials(name) {
    return (name || '?').split(/\s+/).filter(Boolean).slice(0, 2).map((s) => s[0]).join('').toUpperCase();
  }
  function first(name) { return (name || '').split(/\s+/)[0]; }
  function now() { return Date.now() / 1000 + skew; }
  function clock(ts, secs) {
    if (ts === null || ts === undefined) return '–';
    const o = { hour: 'numeric', minute: '2-digit', timeZone: zone };
    if (secs) o.second = '2-digit';
    return new Date(ts * 1000).toLocaleTimeString([], o).replace(/\s?[AP]M$/i, '');
  }
  function dur(s) {
    if (s === null || s === undefined) return '–';
    const neg = s < 0; s = Math.abs(Math.round(s));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    const t = h ? `${h}:${String(m).padStart(2, '0')}:${String(x).padStart(2, '0')}` : `${m}:${String(x).padStart(2, '0')}`;
    return (neg ? '-' : '') + t;
  }
  function kindOf(it) { return it.kind === 'song' ? 'song' : it.kind === 'media' ? 'media' : 'talk'; }
  // A person's Planning Center photo over their initials (shown when there is no photo or it will not load).
  function face(p, cls) {
    const box = el('span', 'face' + (cls ? ' ' + cls : ''));
    if (!p || !p.name) return box;
    box.style.background = colour(p.name);
    box.appendChild(el('span', 'ini', initials(p.name)));
    if (p.photo) {
      const img = el('img'); img.alt = ''; img.referrerPolicy = 'no-referrer';
      img.onerror = () => img.remove();
      img.src = p.photo;
      box.appendChild(img);
    }
    return box;
  }

  // -- fitting ---------------------------------------------------------------------------------------------------
  function scale() {
    const w = window.innerWidth, h = window.innerHeight, k = Math.min(w / 1920, h / 1080);
    stage.style.transform = `translate(${(w - 1920 * k) / 2}px, ${(h - 1080 * k) / 2}px) scale(${k})`;
  }
  function shrink(node, max, min) {
    if (!node) return;
    const box = node.parentElement;
    let size = max;
    node.style.fontSize = size + 'px';
    while (size > min && (node.scrollHeight > box.clientHeight || node.scrollWidth > box.clientWidth)) {
      size -= 0.5; node.style.fontSize = size + 'px';
    }
  }

  // -- times -----------------------------------------------------------------------------------------------------
  // Where each item really sits: its actual start (done, current), else the projection, else the plan; it ends where
  // the next item starts once that has happened, and the current item runs until now if it is over its length.
  function spans() {
    const items = msg.items, out = [];
    const startOf = (it) => it.actual || it.projected || it.planned;
    items.forEach((it, n) => {
      const s = startOf(it);
      if (s === null || s === undefined) return;
      let e = s + (it.length || 0);
      const nx = items.slice(n + 1).find((x) => x.kind !== 'header' && startOf(x));
      if (it.state === 'done' && nx && (nx.actual || nx.state === 'current')) e = Math.max(s, startOf(nx));
      if (it.state === 'current') e = Math.max(e, now());
      out.push({ it, s, e, p: it.planned, pe: it.planned !== null && it.planned !== undefined ? it.planned + (it.length || 0) : null });
    });
    return out;
  }
  function range(list) {
    let from = Infinity, to = -Infinity;
    for (const x of list) {
      from = Math.min(from, x.s, x.p ?? x.s); to = Math.max(to, x.e, x.pe ?? x.e);
    }
    return from < to ? [from, to] : null;
  }

  // -- header ----------------------------------------------------------------------------------------------------
  function header() {
    const h = (msg && msg.header) || {};
    const live = !!(msg && msg.live && h.current);
    if ($('hLive')) $('hLive').hidden = !live;
    $('hTitle').textContent = h.current || (msg && msg.plan ? (msg.plan.title || 'Today’s plan') : 'No plan today');
    $('hSvc').textContent = msg && msg.time ? `${clock(msg.time.starts_at)} service` : 'Service';
    const song = h.song;
    if ($('hKey')) { $('hKey').hidden = !(song && song.key); $('hKey').textContent = song && song.key ? song.key : ''; }
    if ($('hLead')) $('hLead').textContent = song && song.leader ? `${song.leader} leads` : '';
    if ($('hNext')) {
      const nx = (msg && msg.items || []).find((x) => x.state === 'next');
      $('hNextAt').textContent = nx ? `Then, at ${clock(nx.projected || nx.planned)}` : 'Then';
      $('hNext').textContent = h.next || '–';
    }
    if ($('hProg')) {
      $('hProg').hidden = !(h.item_started_at && h.item_length);
      $('hProg').className = 'prog' + (h.kind === 'song' ? ' song' : '');
      $('hFrom').textContent = clock(h.item_started_at);
      $('hTo').textContent = h.item_started_at ? clock(h.item_started_at + h.item_length) : '';
    }
    slip();
    tick();
  }
  // How the service is running, in words.
  function slip() {
    const box = $('hSlip'), h = (msg && msg.header) || {};
    box.replaceChildren();
    if (!h.planned_end) return;
    const ends = h.projected_end || h.planned_end, m = Math.round(Math.abs(h.over_s || 0) / 60);
    if (h.over_s === null || h.over_s === undefined) {
      box.appendChild(el('span', 'd', 'Planned to end at ')); box.append(clock(h.planned_end) + '.');
      return;
    }
    if (h.over_s >= 60) box.appendChild(el('span', 'w', `${m} minute${m === 1 ? '' : 's'} behind.`));
    else if (h.over_s <= -60) box.appendChild(el('span', 'ok', `${m} minute${m === 1 ? '' : 's'} ahead.`));
    else box.appendChild(el('span', 'ok', 'On time.'));
    box.appendChild(el('span', 'd', ' The service ends at '));
    box.append(clock(ends));
    if (Math.abs(ends - h.planned_end) >= 60) { box.appendChild(el('span', 'd', ` instead of ${clock(h.planned_end)}`)); }
    box.append('.');
  }
  function tick() {
    $('hClock').textContent = clock(now(), true);
    const h = (msg && msg.header) || {};
    const left = $('hLeft');
    if (h.item_started_at && h.item_length) {
      const s = h.item_started_at + h.item_length - now();
      left.textContent = dur(s);
      left.className = 'left' + (s < 0 ? ' over' : '');
      if ($('hBar')) $('hBar').style.width = Math.min(100, Math.max(0, (now() - h.item_started_at) / h.item_length * 100)) + '%';
    } else { left.textContent = '–'; left.className = 'left'; }
  }

  // -- the ruler -------------------------------------------------------------------------------------------------
  function ruler(mini) {
    const box = $('ruler');
    box.replaceChildren();
    const list = spans(), r = range(list);
    if (!r) return;
    let [from, to] = r;
    const cur = list.find((x) => x.it.state === 'current');
    // The side column and the compact layout start just before what is on now, so what is left gets the height.
    if (mini || layout === 'compact') from = Math.max(from, (cur ? cur.s : now()) - (mini ? 120 : 300));
    const H = box.clientHeight, pps = H / (to - from), y = (t) => (t - from) * pps;
    const mins = (to - from) / 60, step = (mins <= 40 ? 5 : mins <= 120 ? 15 : 30) * 60;
    for (let t = Math.ceil(from / step) * step; t <= to; t += step) {
      const tk = el('div', 'tick'); tk.style.top = y(t) + 'px'; tk.appendChild(el('span', '', clock(t))); box.appendChild(tk);
    }
    if (!mini) {
      const svg = document.createElementNS(SVG, 'svg');
      svg.setAttribute('class', 'drift'); svg.setAttribute('viewBox', `0 0 46 ${H}`); svg.setAttribute('preserveAspectRatio', 'none');
      for (const x of list) {
        if (x.p === null || x.p === undefined || x.it.kind === 'header' || x.pe <= from) continue;
        const ph = (x.pe - Math.max(x.p, from)) * pps - 2;
        const pl = el('div', 'plan', ph > 18 ? clock(x.p) : '');
        pl.style.top = Math.max(y(x.p), 0) + 'px'; pl.style.height = Math.max(ph, 1) + 'px';
        box.appendChild(pl);
        if (Math.abs(x.s - x.p) >= 30 && x.p >= from) {
          const ln = document.createElementNS(SVG, 'line');
          ln.setAttribute('x1', '0'); ln.setAttribute('y1', String(y(x.p)));
          ln.setAttribute('x2', '46'); ln.setAttribute('y2', String(y(x.s)));
          ln.setAttribute('stroke', '#ff4d3d'); ln.setAttribute('stroke-width', '1.5'); ln.setAttribute('opacity', '.7');
          svg.appendChild(ln);
        }
      }
      box.appendChild(svg);
    }
    const notesFor = msg.notes && msg.notes.item;
    for (const x of list) {
      const it = x.it;
      if (x.e <= from) continue;
      if (it.kind === 'header') {
        const sc = el('div', 'sect', it.title); sc.style.top = y(x.s) + 'px'; box.appendChild(sc); continue;
      }
      const top = Math.max(y(x.s), 0), hh = y(x.e) - top - 3;
      const tall = hh > 140 && notesFor === it.title && msg.notes;
      const b = el('div', `blk ${kindOf(it)} ${it.state || ''}${hh < 12 ? ' tiny' : hh < 26 ? ' small' : ''}${tall ? ' tall' : ''}`);
      b.style.top = top + 'px'; b.style.height = Math.max(hh, 1) + 'px';
      const late = it.planned && x.s - it.planned >= 60 && it.state !== 'done';
      b.appendChild(el('span', 'at' + (late ? ' late' : ''), clock(x.s)));
      b.appendChild(el('span', 't', it.title));
      if (it.song && it.song.key) b.appendChild(el('span', 'key k', it.song.key));
      if (it.song && it.song.leader && hh > 24) b.appendChild(el('span', 'ld', it.song.leader));
      if (mini && it.state === 'current' && hh > 60) {
        const fs = el('div', 'faces');
        for (const c of msg.mics) if (c.on && c.person) fs.appendChild(face(c.person));
        if (fs.childNodes.length) b.appendChild(fs);
      }
      if (tall) b.appendChild(el('div', 'peek', msg.notes.text.split(/\n\s*\n/).slice(0, mini ? 2 : 4).join('\n\n')));
      box.appendChild(b);
    }
    const t = now();
    if (t >= from && t <= to) {
      const ph = el('div', 'playhead'); ph.style.top = y(t) + 'px'; ph.appendChild(el('span', '', clock(t))); box.appendChild(ph);
    }
  }

  // -- mics ------------------------------------------------------------------------------------------------------
  function onMics() {
    const box = $('mics');
    box.replaceChildren();
    const h = msg.header || {};
    $('micsFor').textContent = h.kind === 'song' ? 'On mics for this song' : 'On mics now';
    const on = msg.mics.filter((c) => c.on && c.person);
    if (!on.length) {
      box.appendChild(el('div', 'none', msg.mics.length ? 'No mics in use for this item' : 'No mics mapped to positions'));
      return;
    }
    for (const c of on) {
      const d = el('div', 'om');
      d.appendChild(face(c.person));
      d.appendChild(el('span', 'mic', c.mic));
      d.appendChild(el('span', 'n', first(c.person.name)));
      box.appendChild(d);
    }
  }

  // Up to 8 mics in one row, 9 to 19 in two, 20 or more in three; columns share the width equally.
  function rowsFor(n) { return n >= 20 ? 3 : n > 8 ? 2 : 1; }
  function grid(box, n) {
    const rows = rowsFor(n), cols = Math.max(1, Math.ceil(n / rows));
    box.dataset.rows = String(rows);
    box.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;
    box.style.gridTemplateRows = `repeat(${rows}, minmax(0, 1fr))`;
  }
  function fitNames(box) {
    box.querySelectorAll('.ch .name').forEach((n) => {
      n.style.fontSize = '';
      let size = parseFloat(getComputedStyle(n).fontSize);
      while (size > 10 && n.scrollWidth > n.clientWidth) { size -= 0.5; n.style.fontSize = size + 'px'; }
    });
  }
  function hour() {
    const box = $('hour');
    box.replaceChildren();
    const list = spans().filter((x) => x.it.kind !== 'header'), r = range(list);
    if (!r) return null;
    const [from, to] = r, x = (t) => (t - from) / (to - from) * 100;
    for (const s of list) {
      const seg = el('div', `seg ${kindOf(s.it)} ${s.it.state || ''}`, (s.e - s.s) / (to - from) > 0.045 ? s.it.title : '');
      seg.style.left = x(s.s) + '%'; seg.style.width = `calc(${x(s.e) - x(s.s)}% - 3px)`;
      box.appendChild(seg);
    }
    const t = now();
    if (t >= from && t <= to) { const ph = el('div', 'ph'); ph.style.left = x(t) + '%'; box.appendChild(ph); }
    return { list, from, to, x };
  }
  function channel(c, leader, scale) {
    const p = c.person;
    const sings = msg.items.some((it) => it.song && it.song.mics.some((m) => m.mic === c.mic));
    const ch = el('div', 'ch' + (sings ? ' sings' : '') + (c.label && !p ? ' label' : '') + (!p && !c.label ? ' spare' : ''));
    const f = p ? face(p) : el('span', 'face');
    if (c.label && !p) f.appendChild(el('div', 'lt', c.label));
    f.appendChild(el('span', 'num', c.mic));
    ch.appendChild(f);
    const bd = el('div', 'bd');
    bd.appendChild(el('div', 'name', p ? p.name : 'Spare'));
    const pos = el('div', 'pos');
    if (p && leader && p.name === leader) { pos.appendChild(el('span', 'lead', 'Leading')); pos.append(' · '); }
    pos.append(p ? c.position : `${c.position} · unfilled`);
    bd.appendChild(pos);
    if (scale) {
      const lane = el('div', 'lane');
      for (const s of scale.list) {
        const it = s.it;
        const drawn = it.song && it.song.mics.some((m) => m.mic === c.mic);
        const spokenNow = it.state === 'current' && !it.song && c.on;
        if (!drawn && !spokenNow) continue;
        const i = el('i', (drawn ? '' : 'talk') + (it.state === 'done' ? ' done' : ''));
        i.style.left = scale.x(s.s) + '%'; i.style.width = (scale.x(s.e) - scale.x(s.s)) + '%';
        lane.appendChild(i);
      }
      const t = now();
      if (t >= scale.from && t <= scale.to) { const b = el('b'); b.style.left = scale.x(t) + '%'; lane.appendChild(b); }
      bd.appendChild(lane);
    }
    ch.appendChild(bd);
    return ch;
  }
  function micboard() {
    const box = $('board');
    box.replaceChildren();
    const h = msg.header || {}, sc = hour();
    if (!msg.mics.length) {
      box.style.gridTemplateColumns = '1fr'; box.style.gridTemplateRows = '1fr';
      box.appendChild(el('div', 'empty', 'No mics mapped to positions yet. Map them on the Planning Center admin page.'));
      return;
    }
    grid(box, msg.mics.length);
    for (const c of msg.mics) box.appendChild(channel(c, h.song && h.song.leader, sc));
    fitNames(box);
  }

  // -- notes and roster ------------------------------------------------------------------------------------------
  function notes() {
    const n = msg.notes, box = $('notes');
    $('notesH').textContent = n ? `${n.item} · at ${clock(n.projected || n.planned)}` : 'Notes';
    box.className = 'notes' + (n ? '' : ' none');
    box.textContent = n ? n.text : 'No sermon notes on this plan.';
    shrink(box, 18, 11);
  }
  // Everyone serving who is not on a mic (the mics are on the micboard and in the list beside the clock), and the
  // positions still to fill.
  function roster() {
    const box = $('roster');
    box.replaceChildren();
    const people = msg.people.filter((p) => !p.mic && (!wantTeam || p.team.toLowerCase() === wantTeam));
    $('teamNote').textContent = 'Serving without a mic' + (body.dataset.team ? ` · ${body.dataset.team}` : '');
    const teams = [];
    for (const p of people) if (!teams.includes(p.team)) teams.push(p.team);
    const person = (p, withPos) => {
      const d = el('div', `p ${p.status}`);
      const n = p.status === 'unfilled' ? 'No one yet' : p.name + (p.status === 'pending' ? ' · not confirmed' : '');
      d.appendChild(el('span', 'n', n));
      if (withPos) d.appendChild(el('span', 's', p.position));
      return d;
    };
    for (const t of teams) {
      box.appendChild(el('div', 'tm', t || 'Team'));
      const mine = people.filter((p) => p.team === t);
      if (cards === 'position') {
        const positions = [];
        for (const p of mine) if (!positions.includes(p.position)) positions.push(p.position);
        for (const pos of positions) {
          const pb = el('div', 'posbox');
          pb.appendChild(el('div', 'h', pos));
          for (const p of mine.filter((q) => q.position === pos)) pb.appendChild(person(p, false));
          box.appendChild(pb);
        }
      } else for (const p of mine) box.appendChild(person(p, true));
    }
    if (!people.length) box.appendChild(el('div', 'p', 'Everyone serving this time is on a mic.'));
    shrink(box, 18, 9);
  }

  // -- render ----------------------------------------------------------------------------------------------------
  function message(text) {
    const m = $('msg');
    m.hidden = !text;
    m.textContent = text || '';
    const b = $('body');
    if (b) b.hidden = !!text;
  }
  function render() {
    if (!msg) return;
    zone = msg.zone || undefined;
    if (!msg.plan) { message('No plan today for the service type this screen follows.'); return; }
    message('');
    if (view !== 'side') header();
    if (view === 'full') { ruler(false); onMics(); notes(); roster(); }
    else if (view === 'side') ruler(true);
    else micboard();
  }

  // -- the stream ------------------------------------------------------------------------------------------------
  let es = null;
  function connect() {
    if (es) es.close();
    const q = new URLSearchParams({ stream: 'rundown' });
    if (body.dataset.type) q.set('type', body.dataset.type);
    if (body.dataset.time) q.set('time', body.dataset.time);
    es = new EventSource('/stream?' + q.toString());
    es.onmessage = (ev) => {
      let m;
      try { m = JSON.parse(ev.data); } catch (e) { return; }
      if (m.type !== 'rundown') return;
      skew = m.now - Date.now() / 1000;
      msg = m;
      render();
    };
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) {
        // Refused (switched off in admin, the module is off, or a remote viewer): say so and ask again later.
        message('The rundown is switched off. Turn it on in Planning Center › Rundown on /screen.');
        setTimeout(connect, 15000);
      }
    };
  }

  window.addEventListener('resize', () => { scale(); render(); });
  scale();
  connect();
  setInterval(() => { if (msg && msg.plan && view !== 'side') tick(); }, 500);
  // The playhead and the current item's growing block move slowly; redraw them every 15 seconds.
  setInterval(render, 15000);
})();
