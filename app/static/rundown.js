/* /screen?rundown=full|side|micboard (#148): lays out the "rundown" stream a module offers (Planning Center).
   A kiosk: nothing scrolls and nothing is pressed. The stage is 1920x1080, scaled to the screen; text that would not
   fit its panel is shrunk until it does, and the side column drops items rather than cut one in half. Everything is
   built with textContent: names and notes come from a church's own plan. */
(function () {
  'use strict';

  const body = document.body;
  const view = body.dataset.view, layout = body.dataset.layout, cards = body.dataset.cards;
  const wantTeam = (body.dataset.team || '').toLowerCase();
  const stage = document.getElementById('stage');
  const $ = (id) => document.getElementById(id);
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
  function len(s) { return s ? dur(s) : ''; }
  function face(box, p) {
    box.style.background = p && p.name ? colour(p.name) : '';
    if (p && p.name) box.appendChild(el('span', 'ini', initials(p.name)));
    if (p && p.photo) {
      const img = el('img'); img.alt = ''; img.referrerPolicy = 'no-referrer';
      img.onerror = () => img.remove();
      img.src = p.photo;
      box.appendChild(img);
    }
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

  // -- header ----------------------------------------------------------------------------------------------------
  function header() {
    const h = (msg && msg.header) || {};
    const live = !!(msg && msg.live && h.current);
    $('hLive').hidden = !live;
    $('hTitle').textContent = h.current || (msg && msg.plan ? (msg.plan.title || 'Today’s plan') : 'No plan today');
    const song = h.song;
    $('hKey').hidden = !(song && song.key);
    $('hKey').textContent = song && song.key ? song.key : '';
    $('hLead').textContent = song && song.leader ? `led by ${song.leader}` : '';
    $('hNext').textContent = h.next || '–';
    $('hPlanned').textContent = clock(h.planned_end);
    const proj = $('hProj');
    proj.textContent = clock(h.projected_end);
    proj.className = 'big' + (h.projected_end && h.planned_end && h.projected_end - h.planned_end >= 60 ? ' warn' : '');
    const over = $('hOver');
    if (h.over_s === null || h.over_s === undefined) { over.textContent = '–'; over.className = 'big'; }
    else {
      over.textContent = (h.over_s >= 0 ? '+' : '') + dur(h.over_s);
      over.className = 'big' + (h.over_s >= 60 ? ' warn' : '');
    }
    $('hSvc').textContent = msg && msg.time ? `${clock(msg.time.starts_at)} service` : 'Service';
    tick();
  }
  function tick() {
    $('hClock').textContent = clock(now(), true);
    const h = (msg && msg.header) || {};
    const left = $('hLeft');
    if (h.item_started_at && h.item_length) {
      const s = h.item_started_at + h.item_length - now();
      left.textContent = dur(s);
      left.className = 'big' + (s < 0 ? ' over' : '');
    } else { left.textContent = '–'; left.className = 'big'; }
    if ($('sLeft')) $('sLeft').textContent = left.textContent === '–' ? '' : left.textContent;
  }

  // -- order of service ------------------------------------------------------------------------------------------
  const SECTIONS = { pre: 'Pre-service', during: 'Service', post: 'Post-service' };
  function songMeta(song, withMics) {
    const m = el('div', 'm');
    if (song.key) m.appendChild(el('span', 'key', song.key));
    if (song.leader) m.appendChild(el('span', '', `${song.leader} leads`));
    if (withMics) for (const x of song.mics) {
      const s = el('span'); s.appendChild(el('span', 'mic', x.mic)); s.append(' ' + first(x.name)); m.appendChild(s);
    }
    return m;
  }
  function row(it, side) {
    const r = el('div', 'row ' + (it.state || '') + (it.kind === 'header' ? ' header' : ''));
    r.appendChild(el('span', 'pt', clock(it.planned)));
    const shown = it.actual || it.projected || it.planned;
    const late = shown && it.planned && shown - it.planned >= 60, early = shown && it.planned && it.planned - shown >= 60;
    r.appendChild(el('span', 'at' + (late ? ' late' : early ? ' early' : ''), clock(shown)));
    const mid = el('div'); mid.style.minWidth = '0';
    mid.appendChild(el('div', 't', it.title));
    if (it.song) mid.appendChild(songMeta(it.song, !side));
    r.appendChild(mid);
    r.appendChild(el('span', 'len', len(it.length)));
    return r;
  }
  function rows() {
    const box = $('rows');
    box.replaceChildren();
    let items = msg.items;
    if (layout === 'compact') {
      const i = items.findIndex((x) => x.state === 'current');
      const from = i >= 0 ? i : items.findIndex((x) => x.state !== 'done');
      items = items.slice(Math.max(0, from), Math.max(0, from) + 4);
    }
    let sect = '';
    for (const it of items) {
      if (it.section !== sect && layout !== 'compact') { sect = it.section; box.appendChild(el('div', 'sect', SECTIONS[sect] || '')); }
      box.appendChild(row(it, false));
    }
  }
  function rail() {
    const box = $('rail'), fit = box.parentElement;
    box.replaceChildren();
    const cur = msg.items.find((x) => x.state === 'current');
    $('sTitle').textContent = cur ? cur.title : (($('hTitle') || {}).textContent || '–');
    const meta = $('sMeta'); meta.replaceChildren();
    if (cur && cur.song) meta.appendChild(songMeta(cur.song, true));
    const i = msg.items.findIndex((x) => x.state === 'current');
    const upcoming = msg.items.slice(i >= 0 ? i + 1 : 0).filter((x) => x.state !== 'done' && x.kind !== 'header');
    for (const it of upcoming) {
      box.appendChild(row(it, true));
      if (box.scrollHeight > fit.clientHeight) { box.lastChild.remove(); break; }
    }
  }

  // -- mics ------------------------------------------------------------------------------------------------------
  function channel(c, leader) {
    const p = c.person;
    const ch = el('div', 'ch ' + (c.label ? 'label ' : '') + (!p ? 'spare' : c.on ? 'on' : 'idle'));
    const f = el('div', 'face');
    if (c.label) f.appendChild(el('div', 'lt', c.label)); else if (p) face(f, p);
    f.appendChild(el('span', 'num', c.mic));
    ch.appendChild(f);
    ch.appendChild(el('div', 'name', p ? p.name : 'Spare'));
    const pos = el('div', 'pos');
    if (p && leader && p.name === leader) { pos.appendChild(el('span', 'lead', 'Leading')); pos.append(' · '); }
    pos.append(p ? c.position : `${c.position} · unfilled`);
    ch.appendChild(pos);
    return ch;
  }
  function platform() {
    const box = $('mics');
    box.replaceChildren();
    const h = msg.header || {};
    const on = msg.mics.filter((c) => c.on);
    $('micsFor').textContent = h.current ? `${h.current} · mics` : 'mics';
    if (!on.length) {
      box.appendChild(el('div', 'empty', msg.mics.length ? 'No mics in use for this item' : 'No mics mapped to positions'));
      return;
    }
    for (const c of on) box.appendChild(channel(c, h.song && h.song.leader));
  }
  function micboard() {
    const box = $('board');
    box.replaceChildren();
    const h = msg.header || {};
    // Every column the same width and the same distance apart, in mic order.
    for (const c of msg.mics) box.appendChild(channel(c, h.song && h.song.leader));
    if (!msg.mics.length) box.appendChild(el('div', 'msg', 'No mics mapped to positions yet. Map them on the Planning Center admin page.'));
  }

  // -- notes and roster ------------------------------------------------------------------------------------------
  function notes() {
    const n = msg.notes, box = $('notes');
    $('notesH').textContent = n ? `Notes · ${n.item}` : 'Notes';
    $('notesAt').textContent = n ? clock(n.projected || n.planned) : '';
    box.className = 'notes' + (n ? '' : ' none');
    box.textContent = n ? n.text : 'No sermon notes on this plan.';
    shrink(box, 24, 11);
  }
  function roster() {
    const box = $('roster');
    box.replaceChildren();
    const people = msg.people.filter((p) => !wantTeam || p.team.toLowerCase() === wantTeam);
    $('teamNote').textContent = (msg.time ? clock(msg.time.starts_at) : '') + ' · ' + (body.dataset.team || 'all teams');
    const teams = [];
    for (const p of people) if (!teams.includes(p.team)) teams.push(p.team);
    const avatar = (p) => {
      const a = el('span', 'pa' + (p.status === 'unfilled' ? ' empty' : ''));
      if (p.status === 'unfilled') a.textContent = '?'; else face(a, p);
      return a;
    };
    const person = (p, withPos) => {
      const d = el('div', `p ${p.status}${p.singing ? ' singing' : ''}`);
      d.appendChild(avatar(p));
      const x = el('div', 'x');
      x.appendChild(el('div', 'n', p.status === 'unfilled' ? 'Unfilled' : p.name));
      if (withPos) x.appendChild(el('div', 's', p.position));
      d.appendChild(x);
      if (p.status === 'pending' || p.status === 'declined') d.appendChild(el('span', 'tag ' + p.status, p.status));
      if (p.mic) d.appendChild(el('span', 'mic', p.mic));
      return d;
    };
    for (const t of teams) {
      box.appendChild(el('div', 'tm', t || 'Team'));
      const grid = el('div', 'grid');
      const mine = people.filter((p) => p.team === t);
      if (cards === 'position') {
        const positions = [];
        for (const p of mine) if (!positions.includes(p.position)) positions.push(p.position);
        for (const pos of positions) {
          const pb = el('div', 'posbox');
          const hd = el('div', 'h'); hd.appendChild(el('span', '', pos));
          const mics = mine.filter((p) => p.position === pos && p.mic).map((p) => p.mic);
          if (mics.length) hd.appendChild(el('span', 'mic', mics.join(' / ')));
          pb.appendChild(hd);
          for (const p of mine.filter((q) => q.position === pos)) pb.appendChild(person(p, false));
          grid.appendChild(pb);
        }
      } else for (const p of mine) grid.appendChild(person(p, true));
      box.appendChild(grid);
    }
    if (!people.length) box.appendChild(el('div', 'p', 'Nobody scheduled for this service time.'));
    shrink(box, 20, 9);
  }

  // -- render ----------------------------------------------------------------------------------------------------
  function message(text) {
    const m = $('msg');
    m.hidden = !text;
    m.textContent = text || '';
    const b = $('body') || $('board');
    if (b) b.hidden = !!text;
  }
  function render() {
    if (!msg) return;
    zone = msg.zone || undefined;
    header();
    if (!msg.plan) { message('No plan today for the service type this screen follows.'); return; }
    message('');
    if (view === 'full') { rows(); platform(); notes(); roster(); }
    else if (view === 'side') rail();
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
  setInterval(tick, 500);
})();
