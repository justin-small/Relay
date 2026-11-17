/* The YouTube page's schedule and Planning Center Publishing cards (#411): upcoming broadcasts, what Relay creates
   (weekly rules and Planning Center plans), and the Church Center episodes that get each stream's link.
   Built from the admin page's own pieces; everything from the server is set as text. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-youtube')) return;
  const $ = R.$;
  (function () {
    const s = document.createElement('style');
    s.textContent = [
      '#page-youtube .ytlist{list-style:none;margin:8px 0 0;padding:0}',
      '#page-youtube .ytlist li{padding:8px 0;border-top:1px solid var(--line);display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline}',
      '#page-youtube .ytlist li .t{font-weight:600;flex:1 1 200px;overflow-wrap:anywhere}',
      '#page-youtube .ytlist li .m{color:var(--dim);font-size:12px}',
      '#page-youtube .ytrule{display:flex;flex-wrap:wrap;gap:8px;align-items:end;margin-top:6px}',
      '#page-youtube .ytrule .field{flex:1 1 120px;margin:0}',
      '#page-youtube .yttypes label{display:flex;gap:6px;align-items:center;font-weight:400;margin:4px 0}',
      '#page-youtube .ytlink + .ytlink{margin-top:12px;padding-top:12px;border-top:1px solid var(--line)}',
      '#page-youtube .ytlink h4{margin:0;font-size:13px;font-weight:600;overflow-wrap:anywhere}',
      '#page-youtube .ytsub{margin:14px 0 4px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--dim)}',
    ].join('');
    document.head.appendChild(s);
  })();

  const canManage = window.relayCan('youtube.manage');
  const canSend = window.relayCan('youtube.publish');
  const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
  const MATCH = { date: 'By the episode\'s day and time', next: 'The next broadcast', manual: 'One I pick' };
  const PRIVACY = { public: 'Public', unlisted: 'Unlisted', private: 'Private' };
  let data = null, shown = '', formShown = false;
  let pub = null;              // {channels, service_types} from Planning Center, loaded once for managers
  let streams = null;

  function el(tag, text, cls) {
    const e = document.createElement(tag);
    if (text) e.textContent = text;
    if (cls) e.className = cls;
    return e;
  }
  function err(text) { $('ytErr').textContent = text || ''; $('ytErr').hidden = !text; }
  function when(t) {
    if (!t) return '';
    return new Date(t * 1000).toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  }
  function link(text, href) {
    const a = el('a', text); a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer';
    return a;
  }
  function field(label, input, help) {
    const f = el('div', '', 'field');
    const l = el('label', label);
    if (!input.id) input.id = 'yt' + Math.random().toString(36).slice(2, 8);
    l.htmlFor = input.id;
    f.appendChild(l); f.appendChild(input);
    if (help) f.appendChild(el('div', help, 'note'));
    return f;
  }
  function select(options, value) {
    const s = el('select');
    options.forEach(function (o) {
      const op = el('option', o[1]); op.value = o[0];
      if (String(o[0]) === String(value)) op.selected = true;
      s.appendChild(op);
    });
    return s;
  }
  function button(text, cls, fn) {
    const b = el('button', text, cls || ''); b.type = 'button';
    b.onclick = async function () {
      b.disabled = true; err('');
      try { await fn(); } catch (e) { err(e.message); }
      b.disabled = false;
    };
    return b;
  }
  async function send(method, path, body) {
    const res = await fetch(path, { method: method, headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), credentials: 'same-origin', cache: 'no-store' });
    const out = await res.json().catch(function () { return {}; });
    if (!res.ok) {
      if (R.sessionEnded) R.sessionEnded(res, out);
      throw new Error(out.error || out.detail || 'That did not work.');
    }
    return out;
  }

  // ---------------------------------------------------------------- scheduled streams
  function scheduleCard() {
    const c = el('div', '', 'pcard');
    const head = el('div', '', 'phead');
    head.appendChild(el('span', 'Scheduled streams', 'pname'));
    const on = data.schedule.on;
    const p = el('span', '', 'spill ' + (on ? 'on' : 'idle'));
    p.appendChild(el('span', '', 'dot'));
    p.appendChild(document.createTextNode(on ? 'Relay schedules' : 'Off'));
    head.appendChild(p);
    c.appendChild(head);
    if (data.problem) c.appendChild(el('p', data.problem, 'note err'));
    if (!data.can_write) c.appendChild(el('p', 'To let Relay schedule streams, press Connect again and allow it to manage the channel.', 'note'));
    if (data.made && data.made.length) c.appendChild(el('p', 'Just scheduled: ' + data.made.join(', ') + '.', 'note'));
    c.appendChild(el('div', 'Upcoming on YouTube', 'ytsub'));
    const ul = el('ul', '', 'ytlist');
    (data.upcoming || []).forEach(function (b) {
      const li = el('li');
      li.appendChild(el('span', b.title || 'Untitled broadcast', 't'));
      li.appendChild(el('span', [when(b.start), PRIVACY[b.privacy] || b.privacy, b.relay ? 'scheduled by Relay' : '', b.bound ? '' : 'no stream key'].filter(Boolean).join(' · '), 'm'));
      li.appendChild(link('Watch page', b.watch));
      ul.appendChild(li);
    });
    if (!(data.upcoming || []).length) ul.appendChild(el('li', data.checked_at ? 'Nothing scheduled.' : 'Relay checks once streams are scheduled here or an episode is linked. Press Check now to look.', 'm'));
    c.appendChild(ul);
    if (canManage) {
      const acts = el('div', '', 'pactions');
      acts.appendChild(button('Check now', '', async function () { data = await send('POST', '/api/admin/youtube/schedule/check'); render(); }));
      c.appendChild(acts);
    }
    return c;
  }

  function scheduleForm(box) {
    const s = data.schedule;
    box.appendChild(el('div', 'What Relay schedules', 'ytsub'));
    const onBox = el('input'); onBox.type = 'checkbox'; onBox.checked = s.on;
    const onL = el('label'); onL.appendChild(onBox); onL.appendChild(document.createTextNode(' Create the live streams ahead of time'));
    box.appendChild(onL);
    const row = el('div', '', 'ytrow');
    const priv = select(Object.keys(PRIVACY).map(function (k) { return [k, PRIVACY[k]]; }), s.privacy);
    row.appendChild(field('Privacy', priv));
    const days = el('input'); days.type = 'number'; days.min = 1; days.max = 14; days.value = s.days_ahead;
    row.appendChild(field('Days ahead', days, 'Each one costs YouTube allowance, so Relay makes them a week out by default.'));
    box.appendChild(row);

    const keyRow = el('div', '', 'ytrow');
    const keyHolder = el('div', '', 'field');
    function keySelect() {
      keyHolder.replaceChildren();
      const opts = [['', 'None (bind it in YouTube Studio)']].concat((streams || []).map(function (k) {
        return [k.id, k.title || k.id];
      }));
      if (s.stream_id && !(streams || []).some(function (k) { return k.id === s.stream_id; })) opts.push([s.stream_id, s.stream_name || 'The saved stream key']);
      const sel = select(opts, s.stream_id);
      keyHolder.appendChild(field('Stream key', sel, 'The key your encoder streams to. Bound broadcasts start and stop with the encoder.'));
      if (!streams) keyHolder.appendChild(button('Load stream keys', '', async function () {
        streams = (await send('GET', '/api/admin/youtube/streams')).streams; keySelect();
      }));
      keyHolder.sel = sel;
    }
    keySelect();
    keyRow.appendChild(keyHolder);
    box.appendChild(keyRow);

    const title = el('input'); title.value = s.title; title.maxLength = 100;
    box.appendChild(field('Title', title, 'Use {name} (the rule or service type), {title} (the plan\'s title), {date} and {time}.'));
    const desc = el('textarea'); desc.value = s.description; desc.rows = 3; desc.maxLength = 1000;
    box.appendChild(field('Description', desc));

    box.appendChild(el('div', 'From Planning Center plans', 'ytsub'));
    const types = el('div', '', 'yttypes');
    const picked = new Set(s.plan_types);
    if (!data.pc) types.appendChild(el('p', 'Switch on and connect Planning Center to schedule from its plans.', 'note'));
    else if (!pub) types.appendChild(el('p', 'Loading service types…', 'note'));
    else if (!pub.service_types.length) types.appendChild(el('p', 'Follow a service type on the Planning Center page first.', 'note'));
    else pub.service_types.forEach(function (t) {
      const cb = el('input'); cb.type = 'checkbox'; cb.value = t.id; cb.checked = picked.has(t.id);
      const l = el('label'); l.appendChild(cb); l.appendChild(document.createTextNode(t.name));
      types.appendChild(l);
    });
    box.appendChild(types);

    box.appendChild(el('div', 'Weekly', 'ytsub'));
    const rules = el('div');
    function ruleRow(r) {
      const d = el('div', '', 'ytrule');
      const name = el('input'); name.value = r.name || ''; name.maxLength = 80; name.placeholder = 'Sunday Morning';
      const day = select(DAYS.map(function (n, i) { return [i, n]; }), r.day === undefined ? 6 : r.day);
      const t = el('input'); t.type = 'time'; t.value = r.time || '10:00';
      d.appendChild(field('Name', name)); d.appendChild(field('Day', day)); d.appendChild(field('Time (' + data.zone + ')', t));
      d.appendChild(button('Remove', '', async function () { d.remove(); }));
      d.read = function () { return { id: r.id, name: name.value.trim(), day: parseInt(day.value, 10), time: t.value }; };
      rules.appendChild(d);
    }
    s.rules.forEach(ruleRow);
    box.appendChild(rules);
    box.appendChild(button('Add a weekly stream', '', async function () { ruleRow({}); }));

    const ctl = el('div', '', 'ctl');
    ctl.appendChild(button('Save schedule', 'primary', async function () {
      const body = {
        on: onBox.checked, privacy: priv.value, days_ahead: parseInt(days.value, 10), title: title.value,
        description: desc.value, stream_id: keyHolder.sel ? keyHolder.sel.value : s.stream_id,
        stream_name: keyHolder.sel && keyHolder.sel.value ? keyHolder.sel.options[keyHolder.sel.selectedIndex].textContent : '',
        plan_types: Array.prototype.map.call(types.querySelectorAll('input:checked'), function (x) { return x.value; }),
        rules: Array.prototype.map.call(rules.children, function (d) { return d.read(); }),
      };
      if (!pub) delete body.plan_types;
      data = await send('PUT', '/api/admin/youtube/schedule', body);
      formShown = false; render(); R.okMsg('Schedule saved.');
    }));
    box.appendChild(ctl);
  }

  // ---------------------------------------------------------------- Planning Center Publishing
  function saveLinks(list) {
    return send('PUT', '/api/admin/youtube/links', { links: list }).then(function (d) { data = d; formShown = false; render(); });
  }
  function plain(x) {
    return { id: x.id, channel_id: x.channel_id, channel: x.channel, episode_id: x.episode_id, episode: x.episode,
             slot: x.slot, match: x.match, video: x.video, auto: x.auto };
  }
  function broadcastOptions(value) {
    return [['', 'Pick a broadcast']].concat((data.upcoming || []).map(function (b) {
      return [b.id, (b.title || 'Untitled') + ' · ' + when(b.start)];
    })).concat(value && !(data.upcoming || []).some(function (b) { return b.id === value; }) ? [[value, 'The one picked before']] : []);
  }

  function linksCard() {
    const c = el('div', '', 'pcard');
    const head = el('div', '', 'phead');
    head.appendChild(el('span', 'Planning Center Publishing', 'pname'));
    c.appendChild(head);
    if (!data.pc) c.appendChild(el('p', 'Switch on and connect Planning Center to put the stream link in Church Center.', 'note'));
    if (!data.links.length) c.appendChild(el('p', 'No episode gets the link yet.' + (canManage ? ' Add one below.' : ''), 'note'));
    data.links.forEach(function (x) {
      const box = el('div', '', 'ytlink');
      box.appendChild(el('h4', (x.episode || 'Episode') + (x.channel ? ' · ' + x.channel : '')));
      const dl = el('dl');
      function row(k, v) { if (!v) return; dl.appendChild(el('dt', k)); dl.appendChild(el('dd', v)); }
      row('Broadcast', MATCH[x.match]);
      row('Now', x.target ? (x.target.title || x.target.id) + (x.target.start ? ' · ' + when(x.target.start) : '') : 'No broadcast matches yet');
      row('Sent', x.pushed ? (x.target && x.target.id === x.pushed ? 'Yes, ' : 'An earlier one, ') + R.agoText(Date.now() / 1000 - x.pushed_at) : 'Not yet');
      row('Sends', x.auto ? 'As soon as the broadcast is scheduled' : 'Only when you press Send');
      box.appendChild(dl);
      if (x.error) box.appendChild(el('p', x.error, 'note err'));
      if (canManage) {
        const r = el('div', '', 'ytrow');
        const m = select(Object.keys(MATCH).map(function (k) { return [k, MATCH[k]]; }), x.match);
        r.appendChild(field('Which broadcast', m));
        const v = select(broadcastOptions(x.video), x.video);
        const vf = field('Broadcast', v); vf.hidden = x.match !== 'manual';
        r.appendChild(vf);
        const auto = el('input'); auto.type = 'checkbox'; auto.checked = x.auto;
        const al = el('label'); al.appendChild(auto); al.appendChild(document.createTextNode(' Send by itself'));
        r.appendChild(al);
        function save() {
          err('');
          saveLinks(data.links.map(function (y) { return y.id === x.id ? Object.assign(plain(y), { match: m.value, video: v.value, auto: auto.checked }) : plain(y); }))
            .catch(function (e) { err(e.message); });
        }
        m.onchange = function () { vf.hidden = m.value !== 'manual'; if (m.value !== 'manual') save(); };
        v.onchange = save; auto.onchange = save;
        box.appendChild(r);
      }
      const acts = el('div', '', 'pactions');
      if (x.target) acts.appendChild(link('Watch page', x.target.watch));
      if (canSend) acts.appendChild(button('Send now', 'primary', async function () {
        const out = await send('POST', '/api/admin/youtube/links/' + x.id + '/send');
        data = out; render(); R.okMsg('Sent to Planning Center.');
      }));
      if (canManage) acts.appendChild(button('Remove', '', async function () {
        await saveLinks(data.links.filter(function (y) { return y.id !== x.id; }).map(plain));
      }));
      box.appendChild(acts);
      c.appendChild(box);
    });
    if (canManage && data.pc) c.appendChild(addLink());
    return c;
  }

  function addLink() {
    const box = el('div');
    box.appendChild(el('div', 'Add an episode', 'ytsub'));
    if (!pub) { box.appendChild(el('p', 'Loading channels…', 'note')); return box; }
    const row = el('div', '', 'ytrow');
    const ch = select([['', 'Pick a channel']].concat(pub.channels.map(function (c) { return [c.id, c.name + (c.watch_live ? ' (watch live)' : '')]; })), '');
    const ep = select([['', 'Pick a channel first']], '');
    let episodes = [];
    ch.onchange = async function () {
      ep.replaceChildren(el('option', 'Loading…'));
      if (!ch.value) return;
      try {
        episodes = (await send('GET', '/api/admin/youtube/publishing/' + ch.value + '/episodes')).episodes;
        ep.replaceChildren();
        [['', episodes.length ? 'Pick an episode' : 'This channel has no episodes']].concat(episodes.map(function (e) {
          const t = e.times[0];
          return [e.id, (e.title || 'Untitled') + (t && t.starts_at ? ' · ' + when(t.starts_at) : '')];
        })).forEach(function (o) { const op = el('option', o[1]); op.value = o[0]; ep.appendChild(op); });
      } catch (e) { err(e.message); }
    };
    row.appendChild(field('Channel', ch));
    row.appendChild(field('Episode', ep));
    const m = select(Object.keys(MATCH).map(function (k) { return [k, MATCH[k]]; }), 'date');
    row.appendChild(field('Which broadcast', m));
    const v = select(broadcastOptions(''), '');
    const vf = field('Broadcast', v); vf.hidden = true;
    m.onchange = function () { vf.hidden = m.value !== 'manual'; };
    row.appendChild(vf);
    box.appendChild(row);
    const auto = el('input'); auto.type = 'checkbox'; auto.checked = true;
    const al = el('label'); al.appendChild(auto); al.appendChild(document.createTextNode(' Send as soon as the broadcast is scheduled'));
    box.appendChild(al);
    box.appendChild(el('p', 'Relay writes the broadcast\'s watch link into each of the episode\'s times; Church Center shows the stream there.', 'note'));
    const ctl = el('div', '', 'ctl');
    ctl.appendChild(button('Add', 'primary', async function () {
      const e = episodes.find(function (x) { return x.id === ep.value; });
      if (!e) throw new Error('Pick a channel and an episode.');
      if (m.value === 'manual' && !v.value) throw new Error('Pick the broadcast.');
      const c = pub.channels.find(function (x) { return x.id === ch.value; });
      const t = e.times[0];
      await saveLinks(data.links.map(plain).concat([{ channel_id: c.id, channel: c.name, episode_id: e.id, episode: e.title,
        slot: t && t.starts_at ? t.starts_at : 0, match: m.value, video: v.value, auto: auto.checked }]));
      R.okMsg('Episode added.');
    }));
    box.appendChild(ctl);
    return box;
  }

  // ---------------------------------------------------------------- page
  function render() {
    if (!data) return;
    const sig = JSON.stringify([data, !!pub, !!streams]);
    if (sig === shown) return;
    shown = sig;
    const sc = scheduleCard();
    if (canManage) {
      const d = el('details', '', 'sect');
      d.appendChild(el('summary', 'Schedule settings'));
      if (formShown) d.open = true;
      d.ontoggle = function () { formShown = d.open; };
      scheduleForm(d);
      sc.appendChild(d);
    }
    $('ytSchedule').replaceChildren(sc);
    $('ytLinks').replaceChildren(linksCard());
  }

  async function refresh() {
    try { data = await send('GET', '/api/admin/youtube/schedule'); render(); } catch (e) { err(e.message); }
    if (canManage && data && data.pc && !pub) {
      try { pub = await send('GET', '/api/admin/youtube/publishing'); render(); } catch (e) { err(e.message); }
    }
  }
  refresh();
  setInterval(function () {
    const page = document.getElementById('page-youtube');
    const editing = document.activeElement && page && page.contains(document.activeElement) && /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName);
    if (!document.hidden && page && !page.hidden && !editing && !formShown) refresh();
  }, 30000);
})();
