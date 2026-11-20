/* Core admin pages: Schedules, Blocked words, Credentials, Appearance & tuning, Users, Audit log, Network, Modules,
   My account. Real labels from app/templates/admin.html and app/static/{admin,users,audit,network,modules}.js.
   Everything is local state; nothing needs a server. Page-specific CSS is scoped under the .cs- prefix. */
(function () {
'use strict';
const E = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const q = id => document.getElementById(id);
const cap = s => s ? s.charAt(0).toUpperCase() + s.slice(1) : '';

injectCSS('cs', `
.cs-demo{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:-6px 0 14px;padding:5px 10px;border:1px dashed var(--border);border-radius:calc(var(--r) - 2px);color:var(--muted);font-size:12px}
.cs-demo .tabs{margin:0;padding:2px}.cs-demo .tabs button{padding:3px 10px;font-size:12px}
.cs-stack{display:flex;flex-direction:column;gap:14px}
.cs-note{border:1px solid var(--border);background:color-mix(in srgb,var(--fg) 4%,transparent);border-radius:calc(var(--r) - 2px);padding:10px 12px;font-size:13px;color:var(--muted);line-height:1.55;margin:12px 0 0}
.cs-note.first{margin-top:0}
.cs-note em,.cs-note b,.cs-note strong{color:var(--fg);font-style:normal;font-weight:600}
.cs-note.warn{border-color:color-mix(in srgb,var(--warn) 45%,var(--border));background:color-mix(in srgb,var(--warn) 8%,transparent);color:var(--fg)}
.cs-note.err{border-color:color-mix(in srgb,var(--err) 45%,var(--border));background:color-mix(in srgb,var(--err) 8%,transparent);color:var(--fg)}
.cs-note.vo{margin:-6px 0 14px}
.cs-mono{font:12.5px ui-monospace,SFMono-Regular,Menlo,monospace}
.cs-fl{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:12px}
.cs-f{flex:1 1 200px;min-width:0;margin-bottom:12px}.cs-fl>.cs-f{margin-bottom:0}
.cs-f label{font-weight:500;color:var(--fg)}
.cs-f label .cs-st{font-weight:400}
.cs-f small.h{display:block;color:var(--muted);font-size:12px;margin-top:4px}
.cs-w{max-width:520px}
.cs-acts{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.cs-msg{font-size:12.5px;margin-top:10px;min-height:0}.cs-msg:empty{display:none}.cs-msg.ok{color:var(--ok)}.cs-msg.err{color:var(--err)}
.cs-ok{color:var(--ok)}.cs-err{color:var(--err)}.cs-warn{color:var(--warn)}.cs-mut{color:var(--muted)}
.cs-tbl small{display:block;color:var(--muted);font-size:12px;margin-top:2px}
.cs-tbl td.m{color:var(--muted)}
.cs-tbl .acts{text-align:right;white-space:nowrap}.cs-tbl .acts .btn+.btn{margin-left:6px}
.cs-tbl tr.gone td{color:var(--muted)}
.cs-tbl tr.failed td:first-child{box-shadow:inset 3px 0 0 var(--err)}
.cs-tbl tr.failed{background:color-mix(in srgb,var(--err) 7%,transparent)}
.cs-scroll{overflow:auto;margin:0 -18px;padding:0 18px}
.cs-empty{padding:26px 10px;text-align:center;color:var(--muted)}
.cs-empty b{display:block;color:var(--fg);font-weight:500;margin-bottom:2px}
.cs-panel{border:1px solid color-mix(in srgb,var(--primary) 35%,var(--border));border-radius:calc(var(--r) - 2px);background:color-mix(in srgb,var(--primary) 6%,var(--bg));padding:14px 16px;margin-top:14px}
.cs-panel h4{margin:0 0 12px;font-size:14px}
.cs-panel .cs-foot{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px;padding-top:14px;border-top:1px solid var(--border)}
.cs-secret{border:1px solid color-mix(in srgb,var(--warn) 50%,var(--border));background:color-mix(in srgb,var(--warn) 7%,transparent);border-radius:calc(var(--r) - 2px);padding:12px 14px;margin-top:14px;font-size:13px}
.cs-secret .cs-mono{display:block;margin:8px 0 10px;padding:8px 10px;border-radius:calc(var(--r) - 4px);background:var(--bg);border:1px solid var(--border);font-size:14px;letter-spacing:.02em;user-select:all;word-break:break-all}
.cs-pass{display:flex;gap:8px}.cs-pass input{flex:1}
.cs-add{margin-top:14px}
.cs-chip{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;font-weight:600;padding:1px 9px;border-radius:99px;border:1px solid var(--border);color:var(--muted);white-space:nowrap}
.cs-chip i{width:6px;height:6px;border-radius:50%;background:currentColor}
.cs-chip.ok{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 35%,transparent);background:color-mix(in srgb,var(--ok) 8%,transparent)}
.cs-chip.warn{color:var(--warn);border-color:color-mix(in srgb,var(--warn) 35%,transparent);background:color-mix(in srgb,var(--warn) 8%,transparent)}
.cs-chip.err{color:var(--err);border-color:color-mix(in srgb,var(--err) 35%,transparent);background:color-mix(in srgb,var(--err) 8%,transparent)}
.cs-chip.you{color:var(--primary);border-color:color-mix(in srgb,var(--primary) 40%,transparent)}
.cs-sw{display:inline-flex;align-items:center}
.cs-sw[disabled]{opacity:.5;cursor:not-allowed}
.btn[disabled],button[disabled]:not(.sw){opacity:.5;cursor:not-allowed;pointer-events:none}
.cs-head{display:flex;align-items:flex-start;gap:12px;flex-wrap:wrap}.cs-head>div:first-child{flex:1;min-width:0}
.cs-sum{color:var(--muted);font-weight:400;font-size:15px;margin-left:4px}
/* modal confirm */
.cs-mbg{position:fixed;inset:0;z-index:65;display:grid;place-items:center;background:color-mix(in srgb,var(--bg) 62%,transparent);-webkit-backdrop-filter:blur(2px);backdrop-filter:blur(2px)}
.cs-modal{width:min(430px,92vw);background:var(--card);color:var(--fg);border:1px solid var(--border);border-radius:calc(var(--r) + 2px);padding:18px 20px;box-shadow:0 20px 50px color-mix(in srgb,var(--fg) 25%,transparent)}
.cs-modal h4{margin:0 0 6px;font-size:15px}.cs-modal p{margin:0 0 16px;color:var(--muted);font-size:13px;line-height:1.5}
.cs-modal .cs-acts{justify-content:flex-end}
/* schedules */
.cs-days{display:flex;flex-wrap:wrap;gap:6px}
.cs-day{display:inline-flex;margin:0;cursor:pointer;position:relative}
.cs-day input{position:absolute;opacity:0;width:0;height:0}
.cs-day span{min-width:48px;text-align:center;padding:6px 10px;border:1px solid var(--border);border-radius:calc(var(--r) - 2px);background:var(--bg);color:var(--fg);font-size:13px;transition:background-color .15s,color .15s}
.cs-day:hover span{background:var(--hover);color:var(--hover-fg)}
.cs-day input:checked+span{background:var(--primary);border-color:var(--primary);color:var(--primary-fg);font-weight:600}
.cs-day input:focus-visible+span{box-shadow:0 0 0 3px color-mix(in srgb,var(--primary) 50%,transparent)}
.cs-date{display:flex;gap:6px}.cs-date .ib{border-color:var(--border);width:34px;height:34px;flex:none}
.cs-time{display:flex;gap:6px}.cs-time select{flex:1;min-width:0;padding-left:8px;padding-right:4px}
.cs-sname{font-weight:500}.cs-sname .cs-ok,.cs-sname .cs-mut{font-weight:400;font-size:12.5px}
.cs-cal{width:100%}.cs-cal .hd{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;font-weight:500}
.cs-cal .gr{display:grid;grid-template-columns:repeat(7,1fr);gap:2px;text-align:center;font-size:12px}
.cs-cal .gr b{color:var(--muted);font-weight:400;padding:3px 0}
.cs-cal .gr button{border:0;background:none;color:var(--fg);height:30px;border-radius:calc(var(--r) - 3px);cursor:pointer}
.cs-cal .gr button:hover{background:var(--hover);color:var(--hover-fg)}.cs-cal .gr button.on{background:var(--primary);color:var(--primary-fg)}
.cs-cal .gr button.td{box-shadow:inset 0 0 0 1px var(--border)}
/* blocklist */
.cs-ta{font:13.5px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;resize:vertical;min-height:180px}
.cs-ex{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px;margin-top:12px}
.cs-ex div{border:1px solid var(--border);border-radius:calc(var(--r) - 2px);padding:9px 12px;font-size:13px;background:var(--bg)}
.cs-ex .cs-mono{color:var(--fg)}.cs-ex small{display:block;color:var(--muted);margin-top:3px;font-size:12px}
/* appearance */
.cs-prev{border:1px solid var(--border);border-radius:calc(var(--r) - 2px);background:var(--bg);padding:14px 16px;overflow:hidden;min-height:96px;display:flex;align-items:center}
.cs-prev p{margin:0;line-height:1.25;font-weight:500}.cs-prev p.o{color:var(--muted)}
.cs-flow{display:flex;align-items:center;gap:0;margin:2px 0 14px;font-size:12px;color:var(--muted);overflow:auto}
.cs-flow span{flex:none;border:1px solid var(--border);border-radius:99px;padding:3px 10px;background:var(--bg)}
.cs-flow i{flex:1;min-width:24px;height:1px;background:var(--border);position:relative}
/* modules */
.cs-mods{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:14px}
.cs-mod{display:flex;flex-direction:column;gap:8px}
.cs-mod .hd{display:flex;align-items:center;gap:10px}
.cs-mod .ico{width:34px;height:34px;border-radius:9px;background:var(--hover);color:var(--hover-fg);display:grid;place-items:center;flex:none;font-weight:700;font-size:13px}
.cs-mod.off .ico{background:color-mix(in srgb,var(--fg) 7%,transparent);color:var(--muted)}
.cs-mod .nm{font-weight:600;font-size:15px}.cs-mod .vr{font-size:12px;color:var(--muted);margin-left:6px;font-weight:400}
.cs-mod .sum{margin:0;color:var(--muted);font-size:13px;line-height:1.5}
.cs-mod .ft{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px;font-size:12.5px;margin-top:auto;padding-top:10px;border-top:1px solid var(--border)}
.cs-mod .ft a{color:var(--fg);text-decoration:underline;text-underline-offset:2px;cursor:pointer}
.cs-mod .ft .btn{margin-left:auto}
.cs-mod .use{font-size:12px;color:var(--muted)}
.cs-hl{display:inline-flex;align-items:center;gap:6px}.cs-hl::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--muted)}
.cs-hl.ok::before{background:var(--ok)}.cs-hl.warn::before{background:var(--warn)}.cs-hl.error::before{background:var(--err)}
/* network */
.cs-netwarn{border:1px solid color-mix(in srgb,var(--warn) 50%,var(--border));background:color-mix(in srgb,var(--warn) 8%,transparent);border-radius:calc(var(--r) - 2px);padding:10px 14px;margin:0 0 14px;font-size:13px}
.cs-netwarn p{margin:3px 0}
.cs-tbl select{width:auto;min-width:170px;max-width:260px}
.cs-test{font-size:12px;margin-top:5px}.cs-test div+div{color:var(--muted)}
/* users */
.cs-role{display:inline-block;font-size:12px;padding:1px 9px;border:1px solid var(--border);border-radius:99px;color:var(--fg)}
.cs-grid{min-width:640px}.cs-grid th:not(:first-child),.cs-grid td:not(:first-child){text-align:center;width:84px}
.cs-grid tr.grp td{background:color-mix(in srgb,var(--fg) 4%,transparent);font-size:12px;font-weight:600;color:var(--muted);text-align:left;padding:6px 10px}
.cs-grid td.yes{color:var(--ok)}.cs-grid td.no{color:color-mix(in srgb,var(--muted) 55%,transparent)}
.cs-grid th.me,.cs-grid td.me{background:color-mix(in srgb,var(--primary) 7%,transparent)}
.cs-diff{font-size:12.5px;color:var(--muted);margin-top:6px}.cs-diff b{color:var(--fg);font-weight:600}
.cs-two{display:grid;grid-template-columns:1fr 1fr;gap:12px}@media(max-width:720px){.cs-two{grid-template-columns:1fr}}
.cs-thisdev{margin-left:8px}
/* audit */
.cs-af{display:flex;gap:8px;flex-wrap:wrap}.cs-af select{width:auto;min-width:150px;flex:0 1 220px}.cs-af input{flex:1 1 180px;max-width:300px}
`);

/* ---------- helpers ---------- */
const demo = (key, cur, opts) => `<div class="cs-demo"><span>Mockup state</span><div class="tabs" data-demo="${key}">${opts.map(([v, l]) => `<button data-v="${v}" class="${v == cur ? 'on' : ''}">${l}</button>`).join('')}</div></div>`;
const note = (h, c) => `<div class="cs-note ${c || ''}">${h}</div>`;
const VO = '<div class="cs-note vo">You can view this page but not change it.</div>';
const sw = (on, a, d, label, extra) => `<button class="sw${on ? ' on' : ''}" role="switch" aria-checked="${!!on}" aria-label="${E(label)}" title="${E(label)}" data-a="${a}" ${extra || ''}${d ? ' disabled style="opacity:.5;cursor:not-allowed"' : ''}></button>`;
function wire(o) {
  const root = q('pages');
  root.onclick = e => {
    const d = e.target.closest('.tabs[data-demo] button');
    if (d) { e.stopPropagation(); root.querySelectorAll('.tabs[data-demo] button').forEach(b => b.classList.toggle('on', b === d)); o.demo && o.demo(d.parentNode.dataset.demo, d.dataset.v); return; }
    const b = e.target.closest('[data-a]');
    if (b && !b.disabled) { e.stopPropagation(); const f = o.act[b.dataset.a]; f && f(b, e); }
  };
  root.oninput = o.input || null;
  root.onchange = o.change || null;
  root.onsubmit = e => { e.preventDefault(); const f = o.sub && o.sub[e.target.dataset.f]; f && f(e.target); };
}
function modal(o) {
  const bg = document.createElement('div');
  bg.className = 'cs-mbg';
  bg.innerHTML = `<div class="cs-modal" role="alertdialog" aria-modal="true" aria-label="${E(o.t)}"><h4>${E(o.t)}</h4><p>${o.body}</p><div class="cs-acts"><button class="btn" data-x="0">${o.cancel || 'Cancel'}</button><button class="btn ${o.danger ? 'd' : 'p'}" data-x="1">${E(o.ok)}</button></div></div>`;
  document.body.appendChild(bg);
  const done = ok => { document.removeEventListener('keydown', key, true); bg.remove(); if (ok && o.fn) o.fn(); };
  const key = e => { if (e.key == 'Escape') { e.stopPropagation(); done(0); } };
  document.addEventListener('keydown', key, true);
  bg.onclick = e => { e.stopPropagation(); if (e.target === bg) done(0); const b = e.target.closest('[data-x]'); if (b) done(b.dataset.x == '1'); };
  bg.querySelector('[data-x="0"]').focus();
}
const msg = (id, text, cls) => { const m = q(id); if (m) { m.textContent = text || ''; m.className = 'cs-msg ' + (cls || ''); } };
const rel = m => new Date(Date.now() - m * 60000);
const dtLong = d => d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'medium' });
const dtShort = d => d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
const dShort = d => d.toLocaleDateString(undefined, { dateStyle: 'medium' });
const hlabel = (title, d, extra) => `<h3>${title}</h3>${d ? `<p class="d">${d}</p>` : ''}`;
const loadingRow = (cols, t) => `<tr><td colspan="${cols}" class="cs-mut">${t || 'Loading&hellip;'}</td></tr>`;

/* =====================================================================================================
   SCHEDULES   data-page="schedules"   schedules.view / schedules.manage
   ===================================================================================================== */
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const TZ = [['America/New_York', 'Eastern'], ['America/Chicago', 'Central'], ['America/Denver', 'Mountain'], ['America/Phoenix', 'Mountain – Arizona (no DST)'], ['America/Los_Angeles', 'Pacific'], ['America/Anchorage', 'Alaska'], ['Pacific/Honolulu', 'Hawaii']];
const tzl = id => (TZ.find(z => z[0] == id) || [0, id])[1];
const t12 = hm => { const [h, m] = hm.split(':').map(Number); return ((h % 12) || 12) + ':' + String(m).padStart(2, '0') + ' ' + (h < 12 ? 'AM' : 'PM'); };
const us = d => String(d.getMonth() + 1).padStart(2, '0') + '/' + String(d.getDate()).padStart(2, '0') + '/' + d.getFullYear();
const parseUs = t => { const m = /^\s*(\d{1,2})\/(\d{1,2})\/(\d{4})\s*$/.exec(t || ''); if (!m) return null; const d = new Date(+m[3], +m[1] - 1, +m[2]); return d.getMonth() == +m[1] - 1 && d.getDate() == +m[2] ? d : null; };
const dayLbl = d => d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
const startOfToday = () => { const d = new Date(); d.setHours(0, 0, 0, 0); return d; };
function xmas() { const t = startOfToday(); let d = new Date(t.getFullYear(), 11, 24); if (d < t) d = new Date(t.getFullYear() + 1, 11, 24); return d; }
const SC = {
  mode: 'list', form: null, err: '',
  items: [
    { id: 1, name: 'Sunday morning service', repeat: 'weekly', days: [0], start: '08:45', stop: '12:15', tz: 'America/New_York', sd: '', ed: '', on: true },
    { id: 2, name: 'Wednesday night youth', repeat: 'weekly', days: [3], start: '18:30', stop: '20:30', tz: 'America/New_York', sd: '', ed: '', on: true },
    { id: 3, name: 'Christmas Eve candlelight', repeat: 'once', days: [], start: '18:00', stop: '20:00', tz: 'America/New_York', sd: us(xmas()), ed: '', on: true },
    { id: 4, name: 'Easter sunrise', repeat: 'once', days: [], start: '06:30', stop: '07:45', tz: 'America/New_York', sd: '04/05/2026', ed: '', on: false }
  ], nid: 5
};
function schedSummary(s) {
  const when = t12(s.start) + ' – ' + t12(s.stop) + (s.stop < s.start ? ' (next day)' : '') + ' · ' + tzl(s.tz);
  if (s.repeat == 'once') return s.sd + ' · ' + when;
  const days = s.days.length == 7 ? 'Every day' : 'Every ' + s.days.slice().sort().map(i => DAYS[i]).join(', ');
  return days + ' · ' + when + (s.sd ? ' · from ' + s.sd : '') + (s.ed ? ' · until ' + s.ed : '');
}
function schedNext(s) {
  const t = startOfToday();
  if (s.repeat == 'once') { const d = parseUs(s.sd); return d && d >= t ? dayLbl(d) + ', ' + t12(s.start) : null; }
  for (let i = 1; i <= 14; i++) { const d = new Date(t); d.setDate(t.getDate() + i); if (s.days.includes(d.getDay())) return dayLbl(d) + ', ' + t12(s.start); }
  return null;
}
const schedExpired = s => s.repeat == 'once' && !schedNext(s);
function schedStatus(items) {
  if (SC.mode == 'empty' || !items.length) return 'No schedules set. Capture only starts when you press <em>Start capture</em>.';
  const bits = [], first = items[0];
  const live = (SC.mode == 'live' || SC.mode == 'held') && first && first.on;
  if (live) bits.push(SC.mode == 'held' ? 'Stopped by hand during “' + E(first.name) + '” — it stays off until the next scheduled start.' : 'Inside “' + E(first.name) + '”.');
  if (SC.mode == 'live' && live) bits.push('Stops today, ' + t12(first.stop) + '.');
  const nx = items.filter(s => s.on && schedNext(s)).sort((a, b) => 0).slice(0, 1);
  const soonest = items.filter(s => s.on && schedNext(s) && !(live && s === first)).map(s => ({ s, d: schedNextDate(s) })).sort((a, b) => a.d - b.d)[0];
  if (soonest) bits.push('Next start: ' + schedNext(soonest.s) + ' (' + E(soonest.s.name || soonest.s.repeat) + ').');
  else if (!live) bits.push(items.some(s => s.on) ? 'Nothing scheduled in the days ahead.' : 'Every schedule is turned off.');
  return bits.join(' ');
}
function schedNextDate(s) {
  const t = startOfToday();
  if (s.repeat == 'once') return parseUs(s.sd) || t;
  for (let i = 1; i <= 14; i++) { const d = new Date(t); d.setDate(t.getDate() + i); if (s.days.includes(d.getDay())) return d; }
  return t;
}
const tpHTML = (id, hm) => {
  const [h, m] = hm.split(':').map(Number);
  const hs = Array.from({ length: 12 }, (_, i) => `<option value="${i + 1}"${((h % 12) || 12) == i + 1 ? ' selected' : ''}>${i + 1}</option>`).join('');
  const ms = Array.from({ length: 12 }, (_, i) => `<option value="${String(i * 5).padStart(2, '0')}"${m == i * 5 ? ' selected' : ''}>:${String(i * 5).padStart(2, '0')}</option>`).join('');
  return `<div class="cs-time" id="${id}" role="group"><select aria-label="Hour">${hs}</select><select aria-label="Minute">${ms}</select><select aria-label="AM or PM"><option${h < 12 ? ' selected' : ''}>AM</option><option${h >= 12 ? ' selected' : ''}>PM</option></select></div>`;
};
const tpGet = id => { const s = q(id).querySelectorAll('select'); let h = (+s[0].value) % 12; if (s[2].value == 'PM') h += 12; return String(h).padStart(2, '0') + ':' + s[1].value; };
function schedForm() {
  const f = SC.form, once = f.repeat == 'once';
  return `<form class="card" id="schedForm" data-f="sched" autocomplete="off">
  <h3>${f.id ? 'Edit schedule' : 'New schedule'}</h3><p class="d">${f.id ? 'Changes apply at once.' : 'Capture starts and stops on its own inside the window you set.'}</p>
  <div class="cs-fl"><div class="cs-f"><label for="schedName">Name (optional)</label><input id="schedName" type="text" maxlength="60" placeholder="Sunday morning service" value="${E(f.name)}"></div>
  <div class="cs-f" style="flex:0 1 200px"><label for="schedRepeat">Repeats</label><select id="schedRepeat"><option value="weekly"${once ? '' : ' selected'}>Every week</option><option value="once"${once ? ' selected' : ''}>One time only</option></select></div></div>
  <div class="cs-f" id="schedDaysField"${once ? ' hidden' : ''}><label>Days</label><div class="cs-days" id="schedDays">${DAYS.map((d, i) => `<label class="cs-day"><input type="checkbox" value="${i}"${f.days.includes(i) ? ' checked' : ''}><span>${d}</span></label>`).join('')}</div></div>
  <div class="cs-fl">
    <div class="cs-f" id="schedStartDateField"><label for="schedStartDate" id="schedStartDateLabel">${once ? 'Date' : 'Starting on (optional)'}</label><div class="cs-date"><input id="schedStartDate" type="text" inputmode="numeric" maxlength="10" placeholder="MM/DD/YYYY" value="${E(f.sd)}"><button type="button" class="ib" data-a="cal" data-for="schedStartDate" aria-label="Pick a date" title="Pick a date">${ic('cal')}</button></div></div>
    <div class="cs-f" id="schedEndDateField"${once ? ' hidden' : ''}><label for="schedEndDate">Ending on (optional)</label><div class="cs-date"><input id="schedEndDate" type="text" inputmode="numeric" maxlength="10" placeholder="MM/DD/YYYY" value="${E(f.ed)}"><button type="button" class="ib" data-a="cal" data-for="schedEndDate" aria-label="Pick a date" title="Pick a date">${ic('cal')}</button></div></div>
  </div>
  <div class="cs-fl"><div class="cs-f"><label id="schedStartLabel">Start time</label>${tpHTML('schedStart', f.start)}</div><div class="cs-f"><label id="schedStopLabel">Stop time</label>${tpHTML('schedStop', f.stop)}</div>
  <div class="cs-f"><label for="schedTz">Time zone</label><select id="schedTz">${TZ.map(z => `<option value="${z[0]}"${z[0] == f.tz ? ' selected' : ''}>${z[1]}</option>`).join('')}</select></div></div>
  <div class="cs-note" id="schedOvernight" hidden style="margin:-4px 0 12px">The stop time is earlier than the start time, so it stops the next day.</div>
  <div class="cs-acts"><button class="btn p" type="submit" id="schedSave" data-a="nop">${f.id ? 'Save changes' : 'Save schedule'}</button><button class="btn" type="button" id="schedCancel" data-a="schedCancel">Cancel</button></div>
  <div class="cs-msg err" id="schedErr">${E(SC.err)}</div></form>`;
}
function calPop(anchor, input) {
  const base = parseUs(input.value) || startOfToday(); let y = base.getFullYear(), m = base.getMonth();
  toggle(anchor, { side: 'bottom', align: 'end', cls: 'pp', render(el, p) {
    const draw = () => {
      const first = new Date(y, m, 1), n = new Date(y, m + 1, 0).getDate(), sel = parseUs(input.value), t = startOfToday();
      el.innerHTML = `<div class="cs-cal"><div class="hd"><button class="ib" data-n="-1" aria-label="Previous month">${ic('chev', 'transform:rotate(180deg)')}</button><span>${first.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })}</span><button class="ib" data-n="1" aria-label="Next month">${ic('chev')}</button></div><div class="gr">${DAYS.map(d => `<b>${d[0]}</b>`).join('')}${'<span></span>'.repeat(first.getDay())}${Array.from({ length: n }, (_, i) => { const d = new Date(y, m, i + 1); return `<button data-d="${us(d)}" class="${sel && +sel == +d ? 'on' : ''}${+d == +t ? ' td' : ''}">${i + 1}</button>`; }).join('')}</div></div>`;
    };
    draw();
    el.onclick = e => { const nb = e.target.closest('[data-n]'); if (nb) { m += +nb.dataset.n; if (m < 0) { m = 11; y--; } if (m > 11) { m = 0; y++; } draw(); return; } const db = e.target.closest('[data-d]'); if (db) { input.value = db.dataset.d; closePop(p); } };
  } });
}
function schedDraw() {
  const ro = SC.mode == 'ro', items = SC.mode == 'empty' ? [] : SC.items;
  const rows = items.length ? items.map(s => {
    const live = (SC.mode == 'live' || SC.mode == 'held') && s === SC.items[0] && s.on, fin = schedExpired(s);
    return `<tr><td><div class="cs-sname">${E(s.name || schedSummary(s))}${live ? ' <span class="cs-ok">· live window</span>' : fin ? ' <span class="cs-mut">· finished</span>' : ''}</div>${s.name ? `<small>${E(schedSummary(s))}</small>` : ''}</td>
    <td class="m">${!s.on ? 'off' : (schedNext(s) || '—')}</td><td>${sw(s.on, 'schedTog', ro, s.on ? 'Turn this schedule off' : 'Turn this schedule on', `data-id="${s.id}"`)}</td>
    <td class="acts">${ro ? '' : `<button class="btn sm" data-a="schedEdit" data-id="${s.id}" aria-label="Edit ${E(s.name || 'schedule')}">Edit</button><button class="btn sm d" data-a="schedDel" data-id="${s.id}" aria-label="Delete ${E(s.name || 'schedule')}">Delete</button>`}</td></tr>`;
  }).join('') : `<tr><td colspan="4"><div class="cs-empty"><b>No schedules yet.</b>Add one and capture starts and stops by itself.</div></td></tr>`;
  const status = schedStatus(items);
  q('cs-sched').innerHTML = `${ro ? VO : ''}
  <div class="cs-stack"><div class="card"><h3>Schedules</h3><p class="d">Each one is a window: capture starts at the start time and stops at the stop time.</p>
    <div class="cs-note first${SC.mode == 'held' ? ' warn' : ''}" id="schedStatus" style="margin-bottom:12px">${status}</div>
    <div class="cs-scroll"><table class="dt cs-tbl"><thead><tr><th>Schedule</th><th>Next start</th><th style="width:60px">On</th><th></th></tr></thead><tbody id="schedList">${rows}</tbody></table></div>
    ${ro || SC.form ? '' : `<button class="btn p cs-add" id="schedAdd" data-a="schedAdd">${ic('plus', 'vertical-align:-3px;margin-right:6px')}Add schedule</button>`}</div>
  ${!ro && SC.form ? schedForm() : ''}
  <div class="card"><h3>How schedules work</h3><div class="cs-note first">Capture starts and stops on its own inside each window. If it is already running when a window opens, it keeps running and stops at the window’s end. Pressing <em>Stop</em> during a window holds it until the next scheduled start. Starting by hand outside a window is never stopped by a schedule. Windows that overlap or touch run as one session.<br><br>Times follow the time zone you pick, including daylight saving. The machine and the relay must be running for a schedule to fire; a window already under way when the relay starts is picked up, and one missed entirely is skipped.</div></div></div>`;
  if (SC.form) { schedOvernight(); }
}
function schedOvernight() { const o = q('schedOvernight'); if (o && q('schedStart')) o.hidden = !(tpGet('schedStop') < tpGet('schedStart')); }
const schedBlank = () => ({ id: 0, name: '', repeat: 'weekly', days: [0], start: '10:00', stop: '12:00', tz: SC.items.length ? SC.items[SC.items.length - 1].tz : 'America/New_York', sd: '', ed: '' });
reg({ kind: 'page', id: 'schedules', t: 'Schedules', g: 'Automation', ic: 'cal', perm: 'schedules.view' });
P.schedules = () => `<h2 class="t">Schedules</h2><p class="sub">Start and stop capture automatically.</p>${demo('sched', SC.mode, [['list', 'Schedules'], ['live', 'In a window'], ['held', 'Stopped by hand'], ['empty', 'Empty'], ['ro', 'View-only']])}<div id="cs-sched"></div>`;
AFTER.schedules = () => {
  SC.form = null; SC.err = ''; schedDraw();
  wire({
    demo: (k, v) => { SC.mode = v; SC.form = null; schedDraw(); },
    act: {
      nop() {},
      schedAdd() { SC.form = schedBlank(); SC.err = ''; schedDraw(); q('schedName').focus(); },
      schedCancel() { SC.form = null; schedDraw(); },
      schedEdit(b) { const s = SC.items.find(x => x.id == b.dataset.id); SC.form = Object.assign({}, s, { days: s.days.slice() }); SC.err = ''; schedDraw(); q('schedForm').scrollIntoView({ block: 'nearest' }); q('schedName').focus(); },
      schedTog(b) { const s = SC.items.find(x => x.id == b.dataset.id); s.on = !s.on; schedDraw(); toast(s.on ? 'Schedule turned on' : 'Schedule turned off'); },
      schedDel(b) { const s = SC.items.find(x => x.id == b.dataset.id); modal({ t: 'Delete this schedule?', body: 'Delete the schedule “' + E(s.name || schedSummary(s)) + '”? Capture will no longer start or stop on its own for it.', ok: 'Delete schedule', danger: 1, fn() { SC.items = SC.items.filter(x => x !== s); SC.form = null; schedDraw(); toast('Schedule deleted.'); } }); },
      cal(b) { calPop(b, q(b.dataset.for)); }
    },
    change(e) {
      if (e.target.id == 'schedRepeat') { const once = e.target.value == 'once'; q('schedDaysField').hidden = once; q('schedEndDateField').hidden = once; q('schedStartDateLabel').textContent = once ? 'Date' : 'Starting on (optional)'; }
      if (e.target.closest('#schedStart,#schedStop')) schedOvernight();
      if (/^sched(Start|End)Date$/.test(e.target.id)) { const d = parseUs(e.target.value); if (d) e.target.value = us(d); }
    },
    sub: {
      sched() {
        const once = q('schedRepeat').value == 'once', days = [...q('schedDays').querySelectorAll('input:checked')].map(c => +c.value);
        const sdT = q('schedStartDate').value.trim(), edT = q('schedEndDate').value.trim(), sd = parseUs(sdT), ed = parseUs(edT);
        let err = '';
        if (once && !sd) err = 'A one-time schedule needs a date, as MM/DD/YYYY.';
        else if (!once && !days.length) err = 'Pick at least one day.';
        else if (sdT && !sd) err = 'Enter the starting date as MM/DD/YYYY.';
        else if (!once && edT && !ed) err = 'Enter the ending date as MM/DD/YYYY.';
        else if (!once && sd && ed && ed < sd) err = 'The ending date is before the starting date.';
        else if (tpGet('schedStart') == tpGet('schedStop')) err = 'The start and stop times are the same.';
        if (err) { msg('schedErr', err, 'err'); return; }
        const v = { name: q('schedName').value.trim(), repeat: once ? 'once' : 'weekly', days: once ? [] : days, start: tpGet('schedStart'), stop: tpGet('schedStop'), tz: q('schedTz').value, sd: sdT ? us(sd) : '', ed: once ? '' : (edT ? us(ed) : '') };
        const id = SC.form.id; if (id) Object.assign(SC.items.find(x => x.id == id), v); else SC.items.push(Object.assign({ id: SC.nid++, on: true }, v));
        SC.form = null; schedDraw(); toast(id ? 'Schedule saved.' : 'Schedule added.');
      }
    }
  });
};

/* =====================================================================================================
   BLOCKED WORDS   data-page="blocklist"   blocklist.manage
   ===================================================================================================== */
const BL = { mode: 'edit', saved: ['damn', 'crap', 'bloody hell', 'piss', 'darn it'], text: null, max: 500 };
BL.text = BL.saved.join('\n');
const blTerms = t => t.split('\n').map(s => s.trim()).filter(Boolean);
function blCount(n) { return n ? n + (n == 1 ? ' term active' : ' terms active') : 'none'; }
reg({ kind: 'page', id: 'blocklist', t: 'Blocked words', g: 'Settings', ic: 'ban', perm: 'blocklist.manage' });
P.blocklist = () => `<h2 class="t">Blocked words</h2><p class="sub">Removed from the transcript and every translation.</p>${demo('bl', BL.mode, [['edit', 'Editing'], ['empty', 'Empty list'], ['cap', 'Limit reached']])}
<div class="cs-stack"><div class="card"><h3>Blocklist</h3>
<div class="cs-note first" style="margin-bottom:12px">Edited in <span class="cs-mono" id="blocklistPath">blocklist.txt</span> with one word or phrase per line. Changes to the file are picked up within about two seconds, no restart. You can also edit it here.</div>
<div class="cs-f"><label for="blocklist">Blocked words, removed from the transcript and every translation</label><textarea class="cs-ta" id="blocklist" rows="8" spellcheck="false" placeholder="bad word&#10;another bad word&#10;a phrase to drop"></textarea></div>
<div class="cs-acts"><button class="btn p" id="saveBlocklist" data-a="blSave">Save to file</button><span id="blocklistCount" class="cs-mut"></span><span id="blDirty" class="cs-mut" style="margin-left:auto"></span></div><div class="cs-msg" id="blMsg"></div></div>
<div class="card"><h3>How matching works</h3><div class="cs-note first">Matches are removed outright: no asterisks, no gap, nothing on screen to notice. Case and accents are ignored (<span class="cs-mono">nino</span> matches <span class="cs-mono">niño</span>) and only whole words match, so <span class="cs-mono">ass</span> will not touch <span class="cs-mono">class</span>.<br>Each feed is filtered on its own, so to remove a word everywhere, list it in each language.</div>
<div class="cs-ex"><div><span class="cs-mono">nino</span> &rarr; <span class="cs-mono">niño</span><small>Case and accents are ignored</small></div><div><span class="cs-mono">ass</span> &nrarr; <span class="cs-mono">class</span><small>Whole words only</small></div><div>One list per language<small>List a word in each language to remove it everywhere</small></div></div></div></div>`;
AFTER.blocklist = () => {
  const ta = q('blocklist');
  const upd = () => { const n = blTerms(ta.value).length; q('blocklistCount').textContent = blCount(n) + (n >= BL.max ? ' (limit reached)' : ''); const dirty = ta.value !== BL.text; q('blDirty').textContent = dirty ? 'Edited, not saved yet' : ''; };
  const load = () => { BL.text = BL.mode == 'empty' ? '' : BL.mode == 'cap' ? Array.from({ length: BL.max }, (_, i) => 'term ' + (i + 1)).join('\n') : BL.saved.join('\n'); ta.value = BL.text; msg('blMsg', ''); upd(); };
  load();
  wire({
    demo: (k, v) => { BL.mode = v; load(); },
    input: e => { if (e.target === ta) upd(); },
    act: { blSave() {
      const t = blTerms(ta.value), seen = new Set(), uniq = [];
      t.forEach(w => { const k = w.toLowerCase(); if (!seen.has(k)) { seen.add(k); uniq.push(w); } });
      const dup = t.length - uniq.length, over = Math.max(0, uniq.length - BL.max), kept = uniq.slice(0, BL.max);
      BL.saved = kept; BL.text = kept.join('\n'); ta.value = BL.text; ta.blur(); upd();
      if (over) { msg('blMsg', 'Saved, but the list is capped at ' + BL.max + ' terms: ' + over + (over == 1 ? ' term was' : ' terms were') + ' left out. Remove some to make room.', 'err'); }
      else if (dup) { msg('blMsg', 'Saved — ' + dup + (dup == 1 ? ' duplicate' : ' duplicates') + ' removed. Live now.', 'ok'); toast('Saved — live now'); }
      else { msg('blMsg', 'Saved to file — live now.', 'ok'); toast('Saved to file — live now'); }
    } }
  });
};

/* =====================================================================================================
   CREDENTIALS   data-page="credentials"   credentials.manage (Owner)
   ===================================================================================================== */
const CR = { mode: 'set', pass: true };
reg({ kind: 'page', id: 'credentials', t: 'Credentials', g: 'Settings', ic: 'key', perm: 'credentials.manage' });
function crState() {
  const set = CR.mode == 'set';
  q('keyState').textContent = set ? '· set sk-…x9Zq' : '· not set'; q('keyState').className = 'cs-st ' + (set ? 'cs-ok' : 'cs-err');
  q('backupPassState').textContent = CR.pass ? '· set' : '· not set'; q('backupPassState').className = 'cs-st cs-mut';
  q('clearBackupPass').disabled = !CR.pass;
}
P.credentials = () => `<h2 class="t">Credentials</h2><p class="sub">Keys used by this relay.</p>${demo('cr', CR.mode, [['set', 'Key set'], ['none', 'Nothing set yet']])}
<div class="grid g2" style="align-items:start"><div class="cs-stack"><div class="card"><h3>Keys</h3><p class="d">Used for live captioning and translation.</p>
<div class="cs-f cs-w"><label for="apikey">OpenAI API key <span id="keyState" class="cs-st"></span></label><input id="apikey" type="password" placeholder="sk-… (leave blank to keep current)" autocomplete="off"></div>
<div class="cs-acts"><button class="btn p" id="saveCreds" data-a="crKey">Save credentials</button></div><div class="cs-msg" id="crMsg"></div>
<div class="cs-note">Stored server-side in <span class="cs-mono">config.json</span> (0600), encrypted with a key kept in this computer's keychain (<span id="keyStore" class="cs-mono">macOS Keychain</span>). Never sent to viewer pages.</div></div>
<div class="card"><h3>Backup passphrase</h3><p class="d" style="max-width:62ch;line-height:1.5">Relay backs up its database every day, encrypted with this computer's key. With a passphrase, a copy can also be restored on another computer. Relay keeps only a key made from it, never the passphrase, so write it down somewhere safe.</p>
<div class="cs-f cs-w"><label for="backupPass">Passphrase <span id="backupPassState" class="cs-st"></span></label><input id="backupPass" type="password" placeholder="10 characters or more" autocomplete="new-password" minlength="10"></div>
<div class="cs-acts"><button class="btn p" id="saveBackupPass" data-a="crPass">Set passphrase</button><button class="btn" id="clearBackupPass" data-a="crClear">Remove passphrase</button></div><div class="cs-msg" id="crMsg2"></div>
<div class="cs-note">Applies from the next backup. Restore with <span class="cs-mono">relay --restore-backup &lt;file&gt; --passphrase</span> with Relay stopped.</div></div></div>
<div class="card"><h3>Who can change these</h3><p class="d">Owner only (credentials.manage).</p><div class="cs-note first">Other roles never see this page. Neither key is ever shown again after it is saved: the page shows only whether each is set, and the last four characters of the OpenAI key.</div></div></div>`;
AFTER.credentials = () => {
  crState();
  wire({
    demo: (k, v) => { CR.mode = v; crState(); msg('crMsg', ''); },
    act: {
      crKey() { const v = q('apikey').value.trim(); if (!v) { msg('crMsg', 'Nothing to save.', 'err'); return; } CR.mode = 'set'; q('apikey').value = ''; crState(); msg('crMsg', 'Saved. Restart capture to use the new key.', 'ok'); toast('Saved'); },
      crPass() { const v = q('backupPass').value; if (v.length < 10) { msg('crMsg2', 'The passphrase needs at least 10 characters.', 'err'); return; } CR.pass = true; q('backupPass').value = ''; crState(); msg('crMsg2', 'Passphrase set. The next backup opens with it too.', 'ok'); },
      crClear() { modal({ t: 'Remove the backup passphrase?', body: 'Backups will open only on this computer. A copy made earlier with the passphrase still needs it to be restored elsewhere.', ok: 'Remove passphrase', danger: 1, fn() { CR.pass = false; crState(); msg('crMsg2', 'Passphrase removed. Backups open only on this computer.', 'ok'); } }); }
    }
  });
};

/* =====================================================================================================
   APPEARANCE & TUNING   data-page="appearance"   settings.manage
   ===================================================================================================== */
const AP = { font: 48, hist: 40, idle: 1.2, max: 8, live: false, bf: 48, bi: 1.2, bm: 8 };
reg({ kind: 'page', id: 'appearance', t: 'Appearance & tuning', g: 'Settings', ic: 'tune', perm: 'settings.manage' });
const apRange = (id, v, lo, hi) => { const n = parseFloat(v); return !isNaN(n) && n >= lo && n <= hi; };
function apPrev() { const px = Math.max(16, Math.min(160, parseInt(q('font').value, 10) || 48)); const p = q('apPrev'); if (p) { const f = Math.round(px * 0.34); p.innerHTML = `<div><p style="font-size:${f}px">We are glad you are here this morning.</p><p class="o" style="font-size:${Math.round(f * .8)}px">Estamos contentos de que estén aquí esta mañana.</p></div>`; q('apPx').textContent = px + ' px'; } }
P.appearance = () => `<h2 class="t">Appearance &amp; tuning</h2><p class="sub">Viewer text size and how caption lines are broken.</p>${demo('ap', AP.live ? 'live' : 'idle', [['idle', 'No live session'], ['live', 'Session is live']])}
<div class="grid g2" style="align-items:start"><div class="cs-stack"><div class="card"><h3>Viewer display</h3><p class="d">What people see on their phones and the screens.</p>
<div class="cs-fl"><div class="cs-f"><label for="font">Default viewer font size (px)</label><input id="font" type="number" min="16" max="160" step="2" value="${AP.font}"><small class="h">16 to 160</small></div>
<div class="cs-f"><label for="history">History lines kept per stream</label><input id="history" type="number" min="1" max="500" step="1" value="${AP.hist}"><small class="h">1 to 500</small></div></div></div>
<div class="card"><h3>Caption line breaks</h3><p class="d">When a line of caption is finished and a new one starts.</p>
<div class="cs-flow" aria-hidden="true"><span>words stream in</span><i></i><span>quiet for <em style="font-style:normal;color:var(--fg)" id="apF1">${AP.idle}</em> s and a sentence ends</span><i></i><span>or after <em style="font-style:normal;color:var(--fg)" id="apF2">${AP.max}</em> s, at a word</span></div>
<div class="cs-fl"><div class="cs-f"><label for="segIdle">Caption line break after silence (s)</label><input id="segIdle" type="number" min="0.2" max="10" step="0.1" value="${AP.idle}"><small class="h">0.2 to 10</small></div>
<div class="cs-f"><label for="segMaxIdle">Force a line break after (s)</label><input id="segMaxIdle" type="number" min="1" max="60" step="0.5" value="${AP.max}"><small class="h">1 to 60</small></div></div>
<div class="cs-acts"><button class="btn p" id="saveAppearance" data-a="apSave">Save</button></div><div class="cs-msg" id="apMsg"></div>
<div class="cs-note">A caption line is committed once its text has been quiet for <em>line break after silence</em> <strong>and</strong> it ends on a sentence boundary. A line that never reaches one is committed by the <em>force a line break</em> backstop, always at a word boundary so a word is never cut in half. Lower the first for snappier lines; raise the second if you see a sentence split across two lines. In-progress text is already on screen as it streams, so a generous backstop costs nothing. Changing these restarts live sessions. <span class="cs-mono">gpt-realtime-translate</span> fixes phrase boundaries server-side, so these two fields are the only line-break controls.</div></div></div>
<div class="card"><h3>Preview</h3><p class="d">Default viewer text at <b id="apPx"></b>, scaled down.</p><div class="cs-prev" id="apPrev"></div></div></div>`;
AFTER.appearance = () => {
  apPrev();
  wire({
    demo: (k, v) => { AP.live = v == 'live'; },
    input: e => { if (e.target.id == 'font') apPrev(); if (e.target.id == 'segIdle') q('apF1').textContent = e.target.value; if (e.target.id == 'segMaxIdle') q('apF2').textContent = e.target.value; },
    act: { apSave() {
      const bad = !apRange(0, q('font').value, 16, 160) ? 'Default viewer font size must be 16 to 160.' : !apRange(0, q('history').value, 1, 500) ? 'History lines must be 1 to 500.' : !apRange(0, q('segIdle').value, .2, 10) ? 'Line break after silence must be 0.2 to 10 seconds.' : !apRange(0, q('segMaxIdle').value, 1, 60) ? 'Force a line break must be 1 to 60 seconds.' : '';
      if (bad) { msg('apMsg', bad, 'err'); return; }
      const i = parseFloat(q('segIdle').value), m = parseFloat(q('segMaxIdle').value);
      const done = () => { AP.font = +q('font').value; AP.hist = +q('history').value; AP.bi = AP.idle = i; AP.bm = AP.max = m; msg('apMsg', 'Saved. Viewers pick up the new font size on reload; caption timing restarts live sessions.', 'ok'); toast('Saved'); };
      if (AP.live && (i != AP.idle || m != AP.max)) modal({ t: 'Restart the live session?', body: 'Changing the caption timing restarts live sessions. Captions pause for a few seconds while it does.', ok: 'Save and restart', fn: done });
      else done();
    } }
  });
};

/* =====================================================================================================
   MODULES   data-page="modules"   modules.view / modules.manage
   ===================================================================================================== */
const HEALTH = { ok: 'Working', warn: 'Needs attention', error: 'Not working', idle: 'Idle' };
const MODS = [
  { id: 'propresenter', name: 'ProPresenter', v: '1.6.2', on: 1, h: ['ok', 'Connected to ProPresenter 21 at 192.168.10.40'], test: 1, guide: 1, page: 'propresenter', uses: ['Planning Center', 'Tally Arbiter'], offs: [], sum: 'Follows ProPresenter’s slides: lyrics and timers on panels and screens, control from this page, and the service plan’s cues.' },
  { id: 'planning_center', name: 'Planning Center', v: '1.3.0', on: 1, h: ['ok', 'Signed in as Sanctuary Services'], test: 1, guide: 1, page: 'planning_center', uses: ['ProPresenter'], offs: [], sum: 'Shows today’s service plan and its LIVE item on caption panels, and lets chosen panels move the plan along.' },
  { id: 'atem', name: 'ATEM', v: '1.2.1', on: 1, h: ['ok', '2 switchers: Main Switcher, Chapel Mini'], test: 1, guide: 1, page: 'atem', uses: ['Tally Arbiter'], offs: ['Tally Arbiter'], sum: 'Shows the switchers’ program and preview tally on camera screens, and lets the booth cut, take, and change inputs.' },
  { id: 'obs', name: 'OBS', v: '1.1.0', on: 1, h: ['warn', 'Connected, but not recording'], test: 1, guide: 1, page: 'obs', uses: [], offs: [], sum: 'Shows whether OBS is streaming and recording and how healthy it is, switches scenes, and sends a message when it drops.' },
  { id: 'youtube', name: 'YouTube', v: '1.0.4', on: 1, h: ['error', 'Signed out of Google. Sign in again on the YouTube page.'], test: 1, guide: 1, page: 'youtube', uses: [], offs: [], sum: 'Shows whether the church’s YouTube channel is live, for how long, and how many are watching.' },
  { id: 'lighting', name: 'Lighting', v: '0.9.3', on: 0, h: ['idle', ''], test: 1, guide: 1, page: 'lighting', uses: ['ProPresenter'], offs: [], sum: 'Fires a lighting program’s cues (walk-in, worship, message) from cue pages, without the lighting PC.' },
  { id: 'signage', name: 'Signage', v: '1.4.0', on: 1, h: ['ok', '6 screens online'], test: 0, guide: 1, page: 'signage', uses: ['Signage web'], offs: ['Signage web'], sum: 'Plays your images and videos on any TV with a web browser, each screen at its own address.' },
  { id: 'intercom', name: 'Crew intercom', v: '1.0.0', on: 1, h: ['ok', 'Media server running, 4 phones connected'], test: 1, guide: 1, page: 'intercom', uses: ['Remote access'], offs: ['Remote access'], sum: 'Crew intercom on phones over the church network, using a media server Relay runs itself.' },
  { id: 'remote_access', name: 'Remote access', v: '1.1.0', on: 0, h: ['idle', ''], test: 0, guide: 1, page: 'remote_access', uses: [], offs: [], sum: 'Gives the viewer links an internet address, so people off the venue’s Wi-Fi can follow along.' },
  { id: 'parent_paging', name: 'Parent paging', v: '1.0.1', on: 1, h: ['ok', 'Check-Ins connected'], test: 1, guide: 1, page: 'parent_paging', uses: ['ProPresenter'], offs: [], sum: 'Kids workers page a parent from their room’s page: the child’s Check-Ins security code goes to a screen.' },
  { id: 'tally_arbiter', name: 'Tally Arbiter', v: '1.0.2', on: 0, h: ['idle', ''], test: 1, guide: 1, page: 'tally_arbiter', uses: [], offs: [], sum: 'Shows the tally Tally Arbiter serves (ATEM, OBS, vMix and others) on Relay’s camera screens.' }
];
const MD = { mode: 'list', res: {} };
reg({ kind: 'page', id: 'modules', t: 'Modules', g: 'Settings', ic: 'mod', perm: 'modules.view' });
const ini2 = n => n.replace(/[^A-Za-z ]/g, '').split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();
function modCard(m, ro) {
  const st = m.on ? m.h[0] : 'idle', txt = m.on ? (m.h[1] || HEALTH[m.h[0]]) : 'Off';
  const useTxt = m.uses.length ? (m.offs.length ? 'Does more with ' + m.uses.join(', ') + ' on too (' + m.offs.join(', ') + ' is off).' : 'Works with ' + m.uses.join(', ') + '.') : '';
  const r = MD.res[m.id];
  return `<div class="card cs-mod${m.on ? '' : ' off'}" data-mod="${m.id}"><div class="hd"><span class="ico">${E(ini2(m.name))}</span><span class="nm">${E(m.name)}<span class="vr">v${m.v}</span></span><span style="margin-left:auto">${sw(m.on, 'modTog', ro, m.on ? 'Switch ' + m.name + ' off' : 'Switch ' + m.name + ' on', `data-id="${m.id}"`)}</span></div>
  <p class="sum">${E(m.sum)}</p>
  <div class="ft"><span class="cs-hl ${st}">${E(txt)}</span>${m.on ? `<a href="#${m.page}">Settings</a>` : ''}${m.guide ? `<a data-a="guide" data-id="${m.id}">Setup guide</a>` : ''}
  ${!ro && m.on && m.test ? `<button class="btn sm" data-a="modTest" data-id="${m.id}"${r && r.t == 'run' ? ' disabled' : ''}>Test connection</button>` : ''}</div>
  ${r ? `<div class="cs-test ${r.t == 'ok' ? 'cs-ok' : r.t == 'err' ? 'cs-err' : 'cs-mut'}">${E(r.s)}</div>` : ''}
  ${useTxt ? `<div class="use">${E(useTxt)}</div>` : ''}</div>`;
}
function modDraw() {
  const ro = MD.mode == 'ro';
  let body;
  if (MD.mode == 'load') body = '<div class="cs-mut">Loading&hellip;</div>';
  else if (MD.mode == 'err') body = '<div class="cs-err">Could not load the modules: HTTP 500</div>';
  else if (MD.mode == 'empty') body = '<div class="cs-empty"><b>No modules are installed.</b></div>';
  else body = `<div class="cs-mods">${MODS.map(m => modCard(m, ro)).join('')}</div>`;
  q('cs-mods').innerHTML = `${ro ? VO : ''}<div class="card"><h3>Installed modules</h3><p class="d">${MD.mode == 'list' || ro ? MODS.filter(m => m.on).length + ' of ' + MODS.length + ' are on.' : ''}</p>${body}<div class="cs-note">Modules ship inside Relay’s signed release and update with it. Switching one on or off reloads this page so its settings pages appear or go.</div></div>`;
}
P.modules = () => `<h2 class="t">Modules</h2><p class="sub">Integrations with other products. Each is switched on and set up on its own; one that is off runs nothing and adds nothing to Relay.</p>${demo('md', MD.mode, [['list', 'Installed'], ['ro', 'View-only'], ['load', 'Loading'], ['err', 'Load error'], ['empty', 'None installed']])}<div id="cs-mods"></div>`;
AFTER.modules = () => {
  modDraw();
  wire({
    demo: (k, v) => { MD.mode = v; modDraw(); },
    act: {
      guide(b) { toast('Opens the ' + MODS.find(m => m.id == b.dataset.id).name + ' setup guide in a new tab'); },
      modTog(b) {
        const m = MODS.find(x => x.id == b.dataset.id);
        const go = () => { m.on = m.on ? 0 : 1; if (m.on && m.h[0] == 'idle') m.h = ['ok', 'Started']; delete MD.res[m.id]; modDraw(); toast(m.name + (m.on ? ' is on. Reloading so its settings page appears…' : ' is off. Reloading…')); };
        if (m.on) modal({ t: 'Switch ' + E(m.name) + ' off?', body: 'It stops running and its settings page goes away. Its settings are kept and come back when you switch it on again.', ok: 'Switch off', danger: 1, fn: go }); else go();
      },
      modTest(b) {
        const m = MODS.find(x => x.id == b.dataset.id); MD.res[m.id] = { t: 'run', s: 'Testing…' }; modDraw();
        setTimeout(() => { MD.res[m.id] = m.h[0] == 'error' ? { t: 'err', s: 'Not connected: ' + m.h[1] } : { t: 'ok', s: m.h[1] ? 'Connected. ' + m.h[1] : 'Connected.' }; if (q('cs-mods')) modDraw(); }, 900);
      }
    }
  });
};

/* =====================================================================================================
   NETWORK   data-page="network"   network.view / network.manage
   ===================================================================================================== */
const NET_CHIP = { ok: 'OK', default: 'Default', down: 'Port down', missing: 'Port missing', fallback: 'Using default', changed: 'Address changed' };
const NET_CHIPC = { ok: 'ok', default: '', down: 'err', missing: 'err', fallback: 'warn', changed: 'warn' };
const NET_POL = { strict: 'Do not run it', fallback: 'Use the default network' };
const PORTS = [
  { id: 'en0', fn: 'Ethernet (Sanctuary AV)', name: 'en0', kind: 'Ethernet', dr: 1, ip: '192.168.10.24', up: 1, mbps: 1000, present: 1, known: 1 },
  { id: 'en5', fn: 'Ethernet (Video network)', name: 'en5', kind: 'Ethernet', ip: '192.168.20.5', up: 1, mbps: 1000, present: 1, known: 1 },
  { id: 'en1', fn: 'Wi-Fi', name: 'en1', kind: 'Wi-Fi', ip: '10.0.4.77', up: 1, mbps: 866, present: 1, known: 1 },
  { id: 'en6', fn: 'Encoder link', name: 'en6', kind: 'Ethernet', ip: '169.254.12.8', ll: 1, up: 1, mbps: 100, present: 1, known: 1 },
  { id: 'utun3', fn: 'Tailscale', name: 'utun3', kind: 'Tunnel', vpn: 1, ip: '100.64.0.7', up: 1, present: 1, known: 1 },
  { id: 'en7', fn: 'USB Ethernet (Booth)', name: 'en7', kind: 'Ethernet', ip: '', up: null, present: 0, known: 1 }
];
const NCLS0 = () => [
  { id: 'viewers', label: 'Caption and translation pages (plain HTTP)', owner: 'Relay', port: 'en0', pol: 'strict', dp: 'strict' },
  { id: 'crew', label: 'Operator panel, sign-in and crew pages (HTTPS)', owner: 'Relay', port: 'en0', pol: 'strict', dp: 'strict' },
  { id: 'discovery', label: 'Relay\'s announcement to caption panels (mDNS)', owner: 'Relay', port: '', pol: 'fallback', dp: 'fallback' },
  { id: 'translation', label: 'Translation service (OpenAI, over the internet)', owner: 'Relay', port: '', pol: 'fallback', dp: 'fallback' },
  { id: 'atem', label: 'ATEM switcher control (UDP)', owner: 'ATEM', port: 'en5', pol: 'strict', dp: 'strict' },
  { id: 'intercom', label: 'Intercom audio (LiveKit media)', owner: 'Crew intercom', media: 1, port: '', pol: 'strict', dp: 'strict' },
  { id: 'tally_arbiter', label: 'Tally Arbiter (WebSocket)', owner: 'Tally Arbiter', port: '', pol: 'fallback', dp: 'fallback' },
  { id: 'crew.push', label: 'Crew notifications (web push, over the internet)', owner: 'Crew', port: '', pol: 'fallback', dp: 'fallback' }
];
const NS = { mode: 'ok', cls: NCLS0(), res: {}, err: '', ports: PORTS.map(p => Object.assign({}, p)) };
const BROWSING = '192.168.10.24';
const nport = id => NS.ports.find(p => p.id == id);
const pname = p => p.fn || p.name;
function nstat(c) {
  const p = c.port && nport(c.port); if (!c.port) return { s: 'default', d: '' };
  if (!p.present) return { s: c.pol == 'fallback' ? 'fallback' : 'missing', d: c.pol == 'fallback' ? 'Port not connected; using the default network' : 'Port not connected; not running' };
  if (p.up === false) return { s: 'down', d: 'No link on ' + pname(p) };
  return { s: 'ok', a: p.ip };
}
function nsetup() {
  NS.cls = NCLS0(); NS.res = {}; NS.err = '';
  NS.ports = PORTS.map(p => Object.assign({}, p));
  if (NS.mode == 'warn') { NS.cls.find(c => c.id == 'intercom').port = 'en7'; NS.cls.find(c => c.id == 'discovery').port = 'en7'; nport('en6').up = 0; NS.cls.find(c => c.id == 'tally_arbiter').port = 'en6'; }
  if (NS.mode == 'none') NS.ports = [];
}
function nwarnings() {
  const w = [];
  NS.cls.forEach(c => { const s = nstat(c), p = c.port && nport(c.port); if (s.s == 'missing') w.push(c.label + ' is set to “' + pname(p) + '”, which is not connected, so it is not running.'); if (s.s == 'fallback') w.push(c.label + ' is set to “' + pname(p) + '”, which is not connected; it is using the default network for now.'); if (s.s == 'down') w.push('The link on “' + pname(p) + '” is down, so ' + c.label.split(' (')[0].toLowerCase() + ' cannot reach anything.'); });
  const ll = NS.ports.find(p => p.present && p.ll && NS.cls.some(c => c.port == p.id)); if (ll) w.push('“' + pname(ll) + '” has a self-assigned address (169.254): its DHCP server did not answer.');
  return w;
}
function netDraw() {
  const ro = NS.mode == 'ro', m = NS.mode;
  const warns = nwarnings();
  const portRows = m == 'load' ? loadingRow(6) : !NS.ports.length ? `<tr><td colspan="6" class="cs-mut">No network ports found.</td></tr>` : NS.ports.map(p => {
    const sub = [p.name, p.kind, p.dr ? 'default route' : '', p.vpn ? 'VPN' : ''].filter(Boolean).join(' · ');
    const used = NS.cls.filter(c => c.port == p.id).map(c => c.label.split(' (')[0]).join(', ') || '—';
    return `<tr class="${p.present ? '' : 'gone'}"><td>${E(pname(p))}<small>${E(sub)}</small></td><td class="cs-mono">${p.ip ? E(p.ip) + '/24' : (p.present ? 'No address' : '—')}${p.ll ? '<small>self-assigned (169.254)</small>' : ''}</td><td>${!p.present ? 'Not connected' : p.up === true ? 'Up' : p.up === false ? 'Down' : 'Unknown'}</td><td>${p.mbps ? (p.mbps >= 1000 ? p.mbps / 1000 + ' Gbit/s' : p.mbps + ' Mbit/s') : '—'}</td><td>${E(used)}</td>
    <td class="acts">${!ro && !p.present && p.known && !NS.cls.some(c => c.port == p.id) ? `<button class="btn sm" data-a="netForget" data-id="${p.id}">Forget</button>` : ''}</td></tr>`;
  }).join('');
  const opts = c => `<option value=""${c.port == '' ? ' selected' : ''}>System default</option>` + NS.ports.filter(p => p.present || p.id == c.port || p.known).map(p => `<option value="${p.id}"${c.port == p.id ? ' selected' : ''}>${E(pname(p) + (p.ip ? ' (' + p.ip + ')' : p.present ? '' : ' (not connected)'))}</option>`).join('');
  const clsRows = m == 'load' ? loadingRow(5) : NS.cls.map(c => {
    const s = nstat(c), r = NS.res[c.id];
    return `<tr><td>${E(c.label)}<small>${E(c.owner + (c.media ? ' · audio/video' : ''))}</small></td>
    <td><select data-c="${c.id}" data-k="port" aria-label="Network port for ${E(c.label)}"${ro ? ' disabled' : ''}>${opts(c)}</select></td>
    <td><select data-c="${c.id}" data-k="pol" aria-label="If the port for ${E(c.label)} is missing"${ro ? ' disabled' : ''}>${['strict', 'fallback'].map(p => `<option value="${p}"${c.pol == p ? ' selected' : ''}>${NET_POL[p]}${p == c.dp ? ' (default)' : ''}</option>`).join('')}</select></td>
    <td><span class="cs-chip ${NET_CHIPC[s.s]}"><i></i>${NET_CHIP[s.s]}</span>${s.d ? `<small>${E(s.d)}</small>` : s.a ? `<small class="cs-mono">${E(s.a)}</small>` : ''}${r ? `<div class="cs-test ${r.cls}">${r.lines.map(l => `<div>${E(l)}</div>`).join('')}</div>` : ''}</td>
    <td class="acts">${ro ? '' : `<button class="btn sm" data-a="netTest" data-id="${c.id}"${r && r.cls == 'cs-mut' ? ' disabled' : ''}>Test</button>`}</td></tr>`;
  }).join('');
  q('cs-net').innerHTML = `${ro ? VO : ''}${warns.length && m != 'load' ? `<div class="cs-netwarn" id="netWarnings">${warns.map(w => `<p>${E(w)}</p>`).join('')}</div>` : ''}
  <div class="cs-stack"><div class="card"><h3>Ports</h3><p class="d">Updates by itself when a cable is pulled or an adapter plugged in.</p><div class="cs-scroll"><table class="dt cs-tbl"><thead><tr><th>Port</th><th>Address</th><th>Link</th><th>Speed</th><th>Traffic on it</th><th></th></tr></thead><tbody id="netPorts">${portRows}</tbody></table></div></div>
  <div class="card"><h3>Traffic</h3><p class="d">Which port each kind of Relay traffic uses, and what happens when that port goes missing.</p><div class="cs-scroll"><table class="dt cs-tbl"><thead><tr><th>Traffic</th><th>Network port</th><th>If the port is missing</th><th>Status</th><th></th></tr></thead><tbody id="netClasses">${clsRows}</tbody></table></div>
  <div class="cs-note">A change applies at once: Relay moves its pages and tells each module. The operator panel always answers on this computer too (127.0.0.1), and a change that would take it off the address you are using is refused; make it on this computer instead.</div>
  <div class="cs-note err" id="netError"${NS.err ? '' : ' hidden'}>${E(NS.err)}</div></div></div>`;
}
reg({ kind: 'page', id: 'network', t: 'Network', g: 'Settings', ic: 'net', perm: 'network.view' });
P.network = () => `<h2 class="t">Network</h2><p class="sub">This computer’s network ports, and which one each kind of Relay traffic uses. Traffic with no port chosen uses the system’s default network, as Relay always has.</p>${demo('net', NS.mode, [['ok', 'All good'], ['warn', 'Needs attention'], ['ro', 'View-only'], ['load', 'Loading'], ['none', 'No ports']])}<div id="cs-net"></div>`;
AFTER.network = () => {
  nsetup(); netDraw();
  wire({
    demo: (k, v) => { NS.mode = v; nsetup(); netDraw(); },
    change(e) {
      const sel = e.target.closest('select[data-c]'); if (!sel) return;
      const c = NS.cls.find(x => x.id == sel.dataset.c);
      if (sel.dataset.k == 'port') {
        const np = sel.value && nport(sel.value);
        if (c.id == 'crew' && np && np.ip != BROWSING && np.present) { NS.err = 'That would take the operator panel off ' + BROWSING + ', the address you are using. Make this change on this computer instead (127.0.0.1).'; netDraw(); return; }
        c.port = sel.value; delete NS.res[c.id];
      } else c.pol = sel.value;
      NS.err = ''; netDraw(); toast('Applied. Relay moved the traffic.');
    },
    act: {
      netForget(b) { const p = nport(b.dataset.id); modal({ t: 'Forget this port?', body: '“' + E(pname(p)) + '” is not connected and carries no traffic. It comes back by itself if it is plugged in again.', ok: 'Forget port', fn() { NS.ports = NS.ports.filter(x => x !== p); netDraw(); toast('Port forgotten'); } }); },
      netTest(b) {
        const c = NS.cls.find(x => x.id == b.dataset.id); NS.res[c.id] = { cls: 'cs-mut', lines: ['Testing…'] }; netDraw();
        setTimeout(() => {
          const s = nstat(c), p = c.port && nport(c.port);
          NS.res[c.id] = s.s == 'ok' ? { cls: 'cs-ok', lines: ['Routed through ' + pname(p) + ': source address ' + p.ip + '.', 'Nothing was sent.'].concat(c.owner != 'Relay' ? [c.owner + ': connection test passed.'] : []) }
            : s.s == 'default' ? { cls: 'cs-ok', lines: ['No port chosen: the system default network is used (source address ' + BROWSING + ').', 'Nothing was sent.'] }
            : { cls: 'cs-err', lines: [s.d + '.'] };
          if (q('cs-net')) netDraw();
        }, 800);
      }
    }
  });
};

/* =====================================================================================================
   USERS   data-page="users"   users.view / users.manage / tokens.manage (the real page has no per-person
   permission editing: a person has one of five roles and a role has fixed permissions)
   ===================================================================================================== */
const ROLES = [{ id: 'member', label: 'Member', rank: 1 }, { id: 'viewer', label: 'Viewer', rank: 2 }, { id: 'operator', label: 'Operator', rank: 3 }, { id: 'admin', label: 'Admin', rank: 4 }, { id: 'owner', label: 'Owner', rank: 5 }];
const PERMS = [
  ['admin.open', 'Open the operator panel', 'Operator panel', 'viewer'], ['session.view', 'See session health and status', 'Session', 'viewer'], ['session.control', 'Start and stop captions', 'Session', 'operator'], ['targets.manage', 'Change target languages', 'Session', 'operator'],
  ['audio.manage', 'Change audio device, input channel and source language', 'Audio', 'admin'],
  ['panels.view', 'See panels', 'Panels', 'viewer'], ['panels.manage', 'Change panels, firmware, flash, reboot, forget', 'Panels', 'admin'],
  ['alerts.view', 'See alert setup', 'Alerts', 'viewer'], ['alerts.manage', 'Change alert setup', 'Alerts', 'admin'], ['alerts.history', 'Read who sent which alerts, and when', 'Alerts', 'admin'], ['alerts.send', 'Send alerts from /alert when it needs a sign-in', 'Alerts', 'member'],
  ['overlays.view', 'See overlays', 'Overlays', 'viewer'], ['overlays.manage', 'Create, change and delete overlays', 'Overlays', 'operator'],
  ['recordings.view', 'List and download recordings', 'Recordings', 'operator'], ['recordings.manage', 'Delete recordings and change recording settings', 'Recordings', 'admin'],
  ['schedules.view', 'See schedules', 'Schedules', 'viewer'], ['schedules.manage', 'Change schedules', 'Schedules', 'admin'],
  ['blocklist.manage', 'Edit blocked words', 'Settings', 'admin'], ['settings.view', 'Read the configuration (secrets hidden)', 'Settings', 'viewer'], ['settings.manage', 'Change appearance, tuning and realtime settings', 'Settings', 'admin'], ['credentials.manage', 'Change the OpenAI API key and the backup passphrase', 'Settings', 'owner'],
  ['modules.view', 'See which modules exist and their health', 'Modules', 'viewer'], ['modules.manage', 'Switch modules on and off, run a module\'s test', 'Modules', 'admin'],
  ['network.view', 'See this computer\'s network ports and which traffic uses each', 'Network', 'viewer'], ['network.manage', 'Choose which network port each kind of traffic uses', 'Network', 'admin'],
  ['users.view', 'See users and trusted devices', 'Users', 'admin'], ['users.manage', 'Create and change users, reset passwords, trust devices', 'Users', 'admin'], ['tokens.own', 'Create API tokens for your own scripts', 'Users', 'admin'], ['tokens.manage', 'See and revoke everyone\'s API tokens', 'Users', 'owner'], ['audit.view', 'Read the audit log of sign-ins and changes', 'Users', 'admin']
];
const rank = r => (ROLES.find(x => x.id == r) || { rank: 0 }).rank;
const rlabel = r => (ROLES.find(x => x.id == r) || { label: cap(r) }).label;
const can = (role, p) => rank(role) >= rank(PERMS.find(x => x[0] == p)[3]);
const U = {
  as: 'owner', panel: null, secret: null, trust: false, flt: '',
  users: [
    { id: 1, name: 'Justin Small', un: 'justin', role: 'owner', off: 0, must: 0, pin: 1, last: 12 },
    { id: 2, name: 'Dana Reyes', un: 'dana', role: 'admin', off: 0, must: 0, pin: 1, last: 95 },
    { id: 3, name: 'Sam Lee', un: 'sam', role: 'operator', off: 0, must: 0, pin: 0, last: 60 * 26 },
    { id: 4, name: 'Priya Nair', un: 'priya', role: 'operator', off: 0, must: 0, pin: 1, last: 60 * 24 * 3 },
    { id: 5, name: 'Elena Petrova', un: 'elena', role: 'viewer', off: 0, must: 0, pin: 0, last: 60 * 24 * 9 },
    { id: 6, name: 'Marcus Cole', un: 'marcus', role: 'member', off: 0, must: 1, pin: 0, last: null },
    { id: 7, name: 'Hannah Brooks', un: 'hannah', role: 'viewer', off: 1, must: 0, pin: 0, last: 60 * 24 * 71 }
  ], nid: 8,
  devices: [{ id: 1, name: 'Sound booth iPad', by: 'Dana Reyes', added: 60 * 24 * 120, last: 60 * 3 }, { id: 2, name: 'Nursery check-in kiosk', by: 'Justin Small', added: 60 * 24 * 40, last: 60 * 24 * 2 }, { id: 3, name: 'Pastor\'s office Mac', by: 'Justin Small', added: 60 * 24 * 12, last: 12 }], thisDev: 3, did: 4,
  tokens: [{ id: 1, name: 'Stream Deck in the booth', user: 'Dana Reyes', un: 'dana', hint: 'k3f9', exp: 60 * 24 * 60, last: 60 * 5 }, { id: 2, name: 'Church app lyric sync', user: 'Justin Small', un: 'justin', hint: 'p82q', exp: null, last: 60 * 24 * 2 }, { id: 3, name: 'Bitfocus Companion', user: 'Priya Nair', un: 'priya', hint: 'zr1m', exp: -60 * 24 * 2, last: 60 * 24 * 40 }]
};
const meU = () => U.users.find(u => u.id == (U.as == 'owner' ? 1 : 2));
const myRole = () => meU().role;
const canManage = () => U.as != 'view';
const lastOwner = u => u.role == 'owner' && !u.off && U.users.filter(x => x.role == 'owner' && !x.off).length == 1;
const whenIn = m => m == null ? 'never' : dtShort(rel(m));
const tokExp = t => t.exp == null ? 'Never' : t.exp < 0 ? 'Expired' : dShort(new Date(Date.now() + t.exp * 60000));
const PC = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789';
function genPass() { const out = [], b = new Uint8Array(32), lim = 256 - (256 % PC.length); while (out.length < 16) { crypto.getRandomValues(b); for (let i = 0; i < b.length && out.length < 16; i++) if (b[i] < lim) out.push(PC[b[i] % PC.length]); } return [0, 4, 8, 12].map(i => out.slice(i, i + 4).join('')).join('-'); }
const passField = (label, id) => `<div class="cs-f"><label for="${id}">${label}</label><div class="cs-pass"><input id="${id}" type="password" autocomplete="new-password" required><button class="btn" type="button" data-a="gen" data-for="${id}">Generate</button></div></div>`;
const secretBox = (id, intro, text) => `<div class="cs-secret" id="${id}"><div>${intro}</div><span class="cs-mono">${E(text)}</span><div class="cs-acts"><button class="btn sm" data-a="copy" data-t="${E(text)}">Copy</button><button class="btn sm" data-a="secretDone" data-id="${id}">Done</button></div></div>`;
function roleDiff(from, to) {
  if (from == to) return 'No change to what they may do.';
  const add = PERMS.filter(p => !can(from, p[0]) && can(to, p[0])), rm = PERMS.filter(p => can(from, p[0]) && !can(to, p[0]));
  const lbl = a => a.slice(0, 3).map(p => p[1].toLowerCase()).join('; ') + (a.length > 3 ? '; and ' + (a.length - 3) + ' more' : '');
  return (add.length ? `<b>Adds ${add.length}:</b> ${E(lbl(add))}. ` : '') + (rm.length ? `<b>Removes ${rm.length}:</b> ${E(lbl(rm))}.` : '');
}
const roleSel = (value, dis) => `<select id="${dis}" data-k="role">${ROLES.filter(r => r.rank <= rank(myRole())).map(r => `<option value="${r.id}"${r.id == value ? ' selected' : ''}>${r.label}</option>`).join('')}</select>`;
function userPanel() {
  const p = U.panel; if (!p) return '';
  if (p.t == 'add') return `<div class="cs-panel" id="userPanel"><h4>Add a user</h4><form data-f="uadd" autocomplete="off"><div class="cs-fl"><div class="cs-f"><label for="newUserName">Username</label><input id="newUserName" type="text" maxlength="32" autocapitalize="none" spellcheck="false" required><small class="h">3 to 32 letters, numbers, dots, dashes or underscores. They type this to sign in.</small></div><div class="cs-f"><label for="newUserFull">Name</label><input id="newUserFull" type="text" maxlength="80" required></div></div>
  <div class="cs-fl"><div class="cs-f"><label for="newUserRole">Role</label>${roleSel('viewer', 'newUserRole')}<div class="cs-diff" id="roleDiff">${roleDiff('member', 'viewer')}</div></div>${passField('Temporary password', 'newUserPass')}</div>
  <div class="cs-acts"><button class="btn p" type="submit" data-a="nop">Add user</button><button class="btn" type="button" data-a="uclose">Cancel</button></div><div class="cs-msg err" id="uErr"></div></form></div>`;
  const u = U.users.find(x => x.id == p.id), self = u.id == meU().id, lo = lastOwner(u);
  if (p.t == 'reset') return `<div class="cs-panel" id="userPanel"><h4>Reset password for ${E(u.name)}</h4><form data-f="ureset" autocomplete="off">${passField('New temporary password', 'resetUserPass')}<div class="cs-note first" style="margin:-4px 0 12px">This signs them out everywhere and clears their PIN. They choose a new password the next time they sign in.</div><div class="cs-acts"><button class="btn p" type="submit" data-a="nop">Reset password</button><button class="btn" type="button" data-a="uclose">Cancel</button></div></form></div>`;
  return `<div class="cs-panel" id="userPanel"><h4>Edit ${E(u.name)}</h4><form data-f="uedit" autocomplete="off"><div class="cs-fl"><div class="cs-f"><label for="editUserName">Name</label><input id="editUserName" type="text" maxlength="80" value="${E(u.name)}" required></div>
  <div class="cs-f"><label for="editUserRole">Role</label>${roleSel(u.role, 'editUserRole').replace('<select', (self || lo) ? '<select disabled' : '<select')}<div class="cs-diff" id="roleDiff">${self || lo ? '' : roleDiff(u.role, u.role)}</div></div></div>
  ${self || lo ? `<div class="cs-note first" style="margin:-4px 0 12px">${self ? 'You cannot change your own role.' : 'This is the only Owner. Make someone else an Owner first.'}</div>` : ''}
  <div class="cs-acts"><button class="btn p" type="submit" data-a="nop">Save</button><button class="btn" type="button" data-a="uclose">Cancel</button></div></form>
  <div class="cs-foot">${!self && !lo ? `<button class="btn" data-a="utoggle" data-id="${u.id}">${u.off ? 'Turn on' : 'Turn off'}</button>` : ''}<button class="btn" data-a="ureset" data-id="${u.id}">Reset password</button><button class="btn" data-a="usignout" data-id="${u.id}">Sign out everywhere</button>${!self && !lo ? `<button class="btn d" data-a="udel" data-id="${u.id}">Delete</button>` : ''}</div></div>`;
}
const ustatus = u => u.off ? '<span class="cs-chip"><i></i>Turned off</span>' : u.must ? '<span class="cs-chip warn"><i></i>Must change password</span>' : '<span class="cs-chip ok"><i></i>Active</span>';
function usersDraw() {
  const manage = canManage(), mine = meU(), ro = U.as == 'view', owner = U.as == 'owner';
  const list = U.users.filter(u => !U.flt || (u.name + ' ' + u.un).toLowerCase().includes(U.flt.toLowerCase()));
  const rows = list.length ? list.map(u => `<tr><td><div class="row" style="flex-wrap:nowrap;gap:10px"><span class="av" style="${tone(u.name)}">${ini(u.name)}</span><span><strong>${E(u.name)}</strong>${u.id == mine.id ? '<span class="cs-mut"> (you)</span>' : ''}</span></div></td><td class="cs-mono">${E(u.un)}</td><td><span class="cs-role">${rlabel(u.role)}</span></td><td>${ustatus(u)}</td><td class="m">${u.pin ? 'Set' : '—'}</td><td class="m">${whenIn(u.last)}</td>
  <td class="acts">${manage && rank(u.role) <= rank(mine.role) ? `<button class="btn sm" data-a="uedit" data-id="${u.id}" aria-label="Edit ${E(u.name)}">Edit</button>` : ''}</td></tr>`).join('') : `<tr><td colspan="7"><div class="cs-empty"><b>${U.users.length ? 'Nobody matches that.' : 'No users yet.'}</b></div></td></tr>`;
  const devRows = U.devices.length ? U.devices.map(d => `<tr><td><strong>${E(d.name)}</strong>${d.id == U.thisDev ? '<span class="cs-chip you cs-thisdev">This browser</span>' : ''}</td><td class="m">${E(d.by || '—')}</td><td class="m">${dShort(rel(d.added))}</td><td class="m">${whenIn(d.last)}</td><td class="acts">${manage ? `<button class="btn sm" data-a="devrm" data-id="${d.id}" aria-label="Remove ${E(d.name)}">Remove</button>` : ''}</td></tr>`).join('') : `<tr><td colspan="5"><div class="cs-empty"><b>No trusted devices yet.</b></div></td></tr>`;
  const tokRows = U.tokens.length ? U.tokens.map(t => `<tr><td><strong>${E(t.name)}</strong></td><td>${E(t.user)} (${E(t.un)})</td><td class="cs-mono">relay_${t.hint}…</td><td class="${t.exp != null && t.exp < 0 ? 'cs-err' : 'm'}">${tokExp(t)}</td><td class="m">${whenIn(t.last)}</td><td class="acts"><button class="btn sm" data-a="tokrm" data-id="${t.id}" aria-label="Revoke ${E(t.name)}">Revoke</button></td></tr>`).join('') : `<tr><td colspan="6"><div class="cs-empty"><b>Nobody has an API token.</b></div></td></tr>`;
  const cols = ROLES.slice(), gr = [...new Set(PERMS.map(p => p[2]))];
  const matrix = `<table class="dt cs-tbl cs-grid"><thead><tr><th>May…</th>${cols.map(r => `<th class="${r.id == mine.role ? 'me' : ''}">${r.label}${r.id == mine.role ? '<small>you</small>' : ''}</th>`).join('')}</tr></thead><tbody>${gr.map(g => `<tr class="grp"><td colspan="${cols.length + 1}">${g}</td></tr>` + PERMS.filter(p => p[2] == g).map(p => `<tr><td>${E(p[1])}</td>${cols.map(r => { const y = rank(r.id) >= rank(p[3]); return `<td class="${y ? 'yes' : 'no'}${r.id == mine.role ? ' me' : ''}"><span aria-hidden="true">${y ? '✓' : '–'}</span><span class="sr" style="position:absolute;left:-9999px">${y ? 'Yes' : 'No'}</span></td>`; }).join('')}</tr>`).join('')).join('')}</tbody></table>`;
  q('cs-users').innerHTML = `${ro ? VO : ''}<div class="cs-stack">
  <div class="card"><div class="cs-head"><div><h3>People</h3><p class="d">${U.users.filter(u => !u.off).length} can sign in, ${U.users.filter(u => u.off).length} turned off.</p></div><input type="search" id="uFlt" placeholder="Filter people" aria-label="Filter people" value="${E(U.flt)}" style="max-width:200px"></div>
    <div class="cs-scroll"><table class="dt cs-tbl"><thead><tr><th>Name</th><th>Username</th><th>Role</th><th>Status</th><th>PIN</th><th>Last sign-in</th><th><span class="sr" style="position:absolute;left:-9999px">Actions</span></th></tr></thead><tbody id="userList">${rows}</tbody></table></div>
    ${U.secret && U.secret.box == 'userSecret' ? secretBox('userSecret', 'Give ' + E(U.secret.name) + ' this temporary password. It is shown once; they must change it when they sign in.', U.secret.text) : ''}
    ${userPanel()}
    ${manage && !U.panel ? `<button class="btn p cs-add" id="userAdd" data-a="uadd">${ic('plus', 'vertical-align:-3px;margin-right:6px')}Add user</button>` : ''}
    <div class="cs-note">A new user signs in with the temporary password you give them and chooses their own at once. Forgotten passwords are reset here: there is no email.</div></div>
  <div class="card"><h3>Trusted devices</h3><div class="cs-note first" style="margin-bottom:12px">People who have set a PIN can sign in with it on a trusted device only, such as the sound booth iPad. Everywhere else they use their password.</div>
    <div class="cs-scroll"><table class="dt cs-tbl"><thead><tr><th>Device</th><th>Added by</th><th>Added</th><th>Last used</th><th><span class="sr" style="position:absolute;left:-9999px">Actions</span></th></tr></thead><tbody id="deviceList">${devRows}</tbody></table></div>
    ${U.trust ? `<div class="cs-panel" id="devicePanel"><h4>Trust this browser</h4><form data-f="trust" autocomplete="off"><div class="cs-f cs-w"><label for="trustName">A name for this device</label><input id="trustName" type="text" maxlength="60" placeholder="Sound booth iPad" required></div><div class="cs-acts"><button class="btn p" type="submit" data-a="nop">Trust this browser</button><button class="btn" type="button" data-a="trustX">Cancel</button></div></form></div>` : ''}
    ${manage && U.thisDev == null && !U.trust ? `<button class="btn cs-add" id="deviceAdd" data-a="trustOpen">Trust this browser</button>` : ''}</div>
  ${owner ? `<div class="card"><h3>API tokens</h3><div class="cs-note first" style="margin-bottom:12px">Every API token anyone has made. Revoking one stops the script that uses it at once. Turning a person off stops their tokens too.</div><div class="cs-scroll"><table class="dt cs-tbl"><thead><tr><th>Token</th><th>Person</th><th>Starts with</th><th>Expires</th><th>Last used</th><th><span class="sr" style="position:absolute;left:-9999px">Actions</span></th></tr></thead><tbody id="allTokenList">${tokRows}</tbody></table></div></div>` : ''}
  <div class="card"><h3>What each role can do</h3><p class="d">Built from what the server lists, so a module’s permissions appear by themselves. A person has exactly one role.</p><div class="cs-scroll" id="roleRef">${matrix}</div></div></div>`;
  q('usersSum').textContent = '· ' + U.users.length + (U.users.length == 1 ? ' person' : ' people');
}
reg({ kind: 'page', id: 'users', t: 'Users', g: 'Settings', ic: 'users', perm: 'users.view' });
P.users = () => `<h2 class="t">Users <span class="cs-sum" id="usersSum"></span></h2><p class="sub">The people who can sign in to this page, and what each of them may do.</p>${demo('us', U.as, [['owner', 'Owner (Justin)'], ['admin', 'Admin (Dana)'], ['view', 'View-only']])}<div id="cs-users"></div>`;
const uClose = () => { U.panel = null; usersDraw(); };
AFTER.users = () => {
  U.panel = null; U.secret = null; U.trust = false; usersDraw();
  const focus = id => { const e = q(id); e && (e.scrollIntoView({ block: 'nearest' }), e.focus()); };
  wire({
    demo: (k, v) => { U.as = v; U.panel = null; U.secret = null; U.trust = false; usersDraw(); },
    input: e => { if (e.target.id == 'uFlt') { U.flt = e.target.value; usersDraw(); const f = q('uFlt'); f.focus(); f.setSelectionRange(f.value.length, f.value.length); } },
    change: e => { if (e.target.id == 'newUserRole') q('roleDiff').innerHTML = roleDiff('member', e.target.value); if (e.target.id == 'editUserRole') { const u = U.users.find(x => x.id == U.panel.id); q('roleDiff').innerHTML = roleDiff(u.role, e.target.value); } },
    act: {
      nop() {},
      gen(b) { const i = q(b.dataset.for); i.value = genPass(); i.type = 'text'; i.dataset.gen = '1'; i.focus(); i.select(); },
      copy(b) { try { navigator.clipboard.writeText(b.dataset.t); } catch (e) { } b.textContent = 'Copied'; setTimeout(() => { b.textContent = 'Copy'; }, 1600); },
      secretDone() { U.secret = null; usersDraw(); },
      uadd() { U.trust = false; U.secret = null; U.panel = { t: 'add' }; usersDraw(); focus('newUserName'); },
      uedit(b) { U.trust = false; U.secret = null; U.panel = { t: 'edit', id: +b.dataset.id }; usersDraw(); focus('editUserName'); },
      ureset(b) { U.panel = { t: 'reset', id: +b.dataset.id }; usersDraw(); focus('resetUserPass'); },
      uclose: uClose,
      utoggle(b) { const u = U.users.find(x => x.id == +b.dataset.id); u.off = u.off ? 0 : 1; U.panel = null; usersDraw(); toast(u.off ? u.name + ' is turned off and signed out.' : u.name + ' can sign in again.'); },
      usignout(b) { toast(U.users.find(x => x.id == +b.dataset.id).name + ' is signed out everywhere.'); },
      udel(b) { const u = U.users.find(x => x.id == +b.dataset.id); modal({ t: 'Delete ' + E(u.name) + '?', body: 'Delete ' + E(u.name) + ' (' + E(u.un) + ')? They are signed out at once and the account cannot be brought back.', ok: 'Delete user', danger: 1, fn() { U.users = U.users.filter(x => x !== u); U.tokens = U.tokens.filter(t => t.un != u.un); U.panel = null; usersDraw(); toast('Deleted ' + u.name + '.'); } }); },
      devrm(b) { const d = U.devices.find(x => x.id == +b.dataset.id); modal({ t: 'Remove ' + E(d.name) + '?', body: 'Remove ' + E(d.name) + '? A PIN will stop working there.', ok: 'Remove device', danger: 1, fn() { U.devices = U.devices.filter(x => x !== d); if (U.thisDev == d.id) U.thisDev = null; usersDraw(); toast('Removed ' + d.name + '.'); } }); },
      trustOpen() { U.panel = null; U.trust = true; usersDraw(); focus('trustName'); },
      trustX() { U.trust = false; usersDraw(); },
      tokrm(b) { const t = U.tokens.find(x => x.id == +b.dataset.id); modal({ t: 'Revoke this token?', body: 'Revoke ' + E(t.user) + '’s token “' + E(t.name) + '”? Anything using it stops working at once.', ok: 'Revoke token', danger: 1, fn() { U.tokens = U.tokens.filter(x => x !== t); usersDraw(); toast('Revoked ' + t.name + '.'); } }); }
    },
    sub: {
      uadd() {
        const un = q('newUserName').value.trim(), nm = q('newUserFull').value.trim(), pw = q('newUserPass').value, gen = q('newUserPass').dataset.gen == '1';
        if (!/^[A-Za-z0-9._-]{3,32}$/.test(un)) { msg('uErr', 'The username must be 3 to 32 letters, numbers, dots, dashes or underscores.', 'err'); return; }
        if (U.users.some(u => u.un == un.toLowerCase())) { msg('uErr', 'That username is already taken.', 'err'); return; }
        if (pw.length < 8) { msg('uErr', 'The temporary password must be at least 8 characters.', 'err'); return; }
        U.users.push({ id: U.nid++, name: nm, un: un.toLowerCase(), role: q('newUserRole').value, off: 0, must: 1, pin: 0, last: null });
        U.panel = null; U.secret = gen ? { box: 'userSecret', name: nm, text: pw } : null; usersDraw(); toast('Added ' + nm + '.');
      },
      uedit() { const u = U.users.find(x => x.id == U.panel.id), r = q('editUserRole'); u.name = q('editUserName').value.trim() || u.name; if (!r.disabled) u.role = r.value; U.panel = null; usersDraw(); toast('Saved.'); },
      ureset() { const u = U.users.find(x => x.id == U.panel.id), i = q('resetUserPass'), gen = i.dataset.gen == '1', pw = i.value; if (pw.length < 8) { toast('The password must be at least 8 characters.'); return; } u.must = 1; u.pin = 0; U.panel = null; U.secret = gen ? { box: 'userSecret', name: u.name, text: pw } : null; usersDraw(); toast('Password reset for ' + u.name + '.'); },
      trust() { const n = q('trustName').value.trim(); U.devices.push({ id: U.did, name: n, by: meU().name, added: 0, last: 0 }); U.thisDev = U.did++; U.trust = false; usersDraw(); toast('This browser is now trusted.'); }
    }
  });
};

/* =====================================================================================================
   AUDIT LOG   data-page="audit"   audit.view (Admin and Owner)
   ===================================================================================================== */
const KINDS = { auth: 'Sign-ins', setup: 'First-run setup', account: 'Own account', user: 'Users', device: 'Trusted devices', token: 'API tokens', settings: 'Settings', session: 'Captions', alerts: 'Alerts', panels: 'Panels', schedules: 'Schedules', overlays: 'Overlays', recordings: 'Recordings', module: 'Modules', api: 'Other changes' };
const kindName = k => KINDS[k] || cap(k.replace(/_/g, ' '));
const J = ['Justin Small', 'justin'], D = ['Dana Reyes', 'dana'], S = ['Sam Lee', 'sam'], PN = ['Priya Nair', 'priya'];
const AUD = [
  [2, 'auth.login', D, 'Signed in', '', { device: 'Sound booth iPad' }, '192.168.10.31'],
  [9, 'session.start', S, 'Started captions', 'Sunday morning service', {}, '192.168.10.52'],
  [14, 'alerts.send', PN, 'Sent an alert', 'Nursery: “Parent in room 3”', {}, '192.168.10.77'],
  [31, 'auth.failed', null, 'Failed sign-in', 'Account: Tom Okafor', {}, '203.0.113.48'],
  [32, 'auth.failed', null, 'Failed sign-in', 'Account: Tom Okafor', {}, '203.0.113.48'],
  [33, 'auth.locked', null, 'Sign-in locked after 5 failed attempts', 'Account: Tom Okafor', {}, '203.0.113.48'],
  [55, 'settings.update', J, 'Changed appearance and tuning', 'Default viewer font size 44 → 48', {}, '192.168.10.24'],
  [78, 'token.create', D, 'Made an API token', 'Stream Deck in the booth', {}, '192.168.10.31'],
  [96, 'api.post', D, 'Sent a stage message', 'Stage display', { with: 'API token: Stream Deck in the booth' }, '192.168.10.31', 'Stream Deck in the booth'],
  [140, 'schedules.update', J, 'Changed schedule', 'Wednesday night youth', {}, '192.168.10.24'],
  [215, 'module.enable', J, 'Switched Tally Arbiter off', '', {}, '192.168.10.24'],
  [380, 'auth.login', S, 'Signed in', '', { device: 'Booth laptop' }, '192.168.10.52'],
  [610, 'user.password_reset', J, 'Reset a password', 'Marcus Cole', {}, '192.168.10.24'],
  [612, 'user.create', J, 'Added a user', 'Marcus Cole · Member', {}, '192.168.10.24'],
  [1300, 'device.trust', D, 'Trusted a device', 'Sound booth iPad', {}, '192.168.10.31'],
  [1510, 'recordings.delete', D, 'Deleted a recording', 'Sunday 8:45 service', {}, '192.168.10.31'],
  [2100, 'account.password', PN, 'Changed own password', '', {}, '192.168.10.77'],
  [2900, 'panels.update', D, 'Renamed a panel', 'Nursery caption panel', {}, '192.168.10.31'],
  [4300, 'user.disable', J, 'Turned off a user', 'Hannah Brooks', {}, '192.168.10.24'],
  [6100, 'auth.login', J, 'Signed in', '', { device: 'Pastor’s office Mac' }, '192.168.10.24'],
  [9800, 'overlays.update', S, 'Changed an overlay', 'Lower third', {}, '192.168.10.52'],
  [20000, 'user.role', J, 'Changed a role', 'Priya Nair: Viewer → Operator', {}, '192.168.10.24'],
  [60000, 'auth.failed', null, 'Failed sign-in', '', {}, '198.51.100.9'],
  [95000, 'credentials.update', J, 'Changed the OpenAI API key', '', {}, '192.168.10.24'],
  [120000, 'setup.done', null, 'First-run setup finished', '', {}, '192.168.10.24']
].map((e, i) => ({ id: 1000 - i, m: e[0], a: e[1], actor: e[2], s: e[3], t: e[4], d: e[5], ip: e[6], tok: e[7] }));
const AU = { who: '', kind: '', days: '', q: '', shown: 10, mode: 'list' };
const FAILED = { 'auth.failed': 1, 'auth.locked': 1, 'setup.failed': 1 };
function auditFiltered() {
  const ql = AU.q.trim().toLowerCase();
  return AUD.filter(e => (!AU.who || (AU.who == 'system' ? !e.actor : e.actor && e.actor[1] == AU.who)) && (!AU.kind || e.a.split('.')[0] == AU.kind) && (!AU.days || e.m <= AU.days * 1440) && (!ql || (e.s + ' ' + e.t + ' ' + (e.actor ? e.actor.join(' ') : '') + ' ' + e.ip).toLowerCase().includes(ql)));
}
function auditDraw() {
  const m = AU.mode, all = m == 'empty' ? [] : auditFiltered(), part = all.slice(0, AU.shown);
  const rows = m == 'load' ? loadingRow(4) : !part.length ? `<tr><td colspan="4" class="cs-mut">Nothing matches.</td></tr>` : part.map(e => {
    const who = e.actor ? e.actor[0] : (/^(auth|setup)\./.test(e.a) ? 'Nobody signed in' : 'Relay');
    const small = (a) => a.filter(Boolean).map(t => `<small>${E(t)}</small>`).join('');
    return `<tr class="${FAILED[e.a] ? 'failed' : ''}"><td class="m" style="white-space:nowrap">${E(dtLong(rel(e.m)))}</td><td>${E(who)}${small([e.actor ? e.actor[1] : '', e.tok ? 'API token: ' + e.tok : ''])}</td><td>${E(e.s)}${small([e.t, e.d.device ? 'Device: ' + e.d.device : '', e.d.with ? 'With ' + e.d.with : ''])}</td><td class="cs-mono">${E(e.ip)}</td></tr>`;
  }).join('');
  q('auditList').innerHTML = rows;
  q('auditMoreRow').hidden = !(m == 'list' && all.length > AU.shown);
}
reg({ kind: 'page', id: 'audit', t: 'Audit log', g: 'Settings', ic: 'doc', perm: 'audit.view' });
P.audit = () => {
  const people = [[D[1], D[0] + ' (' + D[1] + ')'], [J[1], J[0] + ' (' + J[1] + ')'], [PN[1], PN[0] + ' (' + PN[1] + ')'], [S[1], S[0] + ' (' + S[1] + ')'], ['system', 'Relay itself, or nobody signed in']];
  const kinds = [...new Set(AUD.map(e => e.a.split('.')[0]))];
  return `<h2 class="t">Audit log</h2><p class="sub">Who signed in, who tried and failed, and every change made on this page or through the API, newest first. Entries are kept for <span id="auditKeep">90</span> days.</p>${demo('au', AU.mode, [['list', 'Entries'], ['load', 'Loading'], ['empty', 'Empty']])}
<div class="card"><div class="cs-af"><label class="sr" for="auditWho" style="position:absolute;left:-9999px">Person</label><select id="auditWho"><option value="">Everyone</option>${people.map(p => `<option value="${p[0]}">${E(p[1])}</option>`).join('')}</select>
<label class="sr" for="auditKind" style="position:absolute;left:-9999px">Kind</label><select id="auditKind"><option value="">Everything</option>${kinds.map(k => `<option value="${k}">${kindName(k)}</option>`).join('')}</select>
<label class="sr" for="auditDays" style="position:absolute;left:-9999px">When</label><select id="auditDays"><option value="1">Last 24 hours</option><option value="7">Last 7 days</option><option value="30">Last 30 days</option><option value="" selected>All kept</option></select>
<label class="sr" for="auditQ" style="position:absolute;left:-9999px">Search</label><input id="auditQ" type="search" maxlength="100" placeholder="Search" autocomplete="off"></div>
<div class="cs-scroll" style="margin-top:12px"><table class="dt cs-tbl audit"><thead><tr><th>When</th><th>Who</th><th>What</th><th>From</th></tr></thead><tbody id="auditList"></tbody></table></div>
<div class="cs-acts" style="margin-top:12px" id="auditMoreRow" hidden><button class="btn" id="auditMore" data-a="auMore">Show older</button></div></div>`;
};
AFTER.audit = () => {
  AU.who = AU.kind = AU.days = AU.q = ''; AU.shown = 10; auditDraw();
  wire({
    demo: (k, v) => { AU.mode = v; auditDraw(); },
    change: e => { const k = { auditWho: 'who', auditKind: 'kind', auditDays: 'days' }[e.target.id]; if (k) { AU[k] = e.target.value; AU.shown = 10; auditDraw(); } },
    input: e => { if (e.target.id == 'auditQ') { AU.q = e.target.value; AU.shown = 10; clearTimeout(AU.t); AU.t = setTimeout(auditDraw, 200); } },
    act: { auMore() { AU.shown += 10; auditDraw(); } }
  });
};

/* =====================================================================================================
   MY ACCOUNT   data-page="account"   every signed-in role (API tokens need tokens.own); reached from the header
   ===================================================================================================== */
const AC = { as: 'owner', pin: true, trusted: true, tokens: [{ id: 1, name: 'Church app lyric sync', hint: 'p82q', created: 60 * 24 * 30, exp: null, last: 60 * 24 * 2 }, { id: 2, name: 'Stream Deck at home', hint: 'w7ds', created: 60 * 24 * 100, exp: 60 * 24 * 265, last: 60 * 24 * 11 }], panel: false, secret: null, nid: 3 };
const ACU = { owner: { n: 'Justin Small', un: 'justin', role: 'owner' }, operator: { n: 'Sam Lee', un: 'sam', role: 'operator' } };
reg({ kind: 'page', id: 'account', t: 'My account', g: 'Settings', ic: 'user', perm: 'any signed-in role', hidden: true });
function accDraw() {
  const u = ACU[AC.as], tok = can(u.role, 'tokens.own'), man = can(u.role, 'users.manage');
  q('acctWho').textContent = u.n + ' · ' + u.un + ' · ' + cap(u.role);
  const rows = AC.tokens.length ? AC.tokens.map(t => `<tr><td><strong>${E(t.name)}</strong></td><td class="cs-mono">relay_${t.hint}…</td><td class="m">${dShort(rel(t.created))}</td><td class="m">${t.exp == null ? 'Never' : dShort(new Date(Date.now() + t.exp * 60000))}</td><td class="m">${whenIn(t.last)}</td><td class="acts"><button class="btn sm" data-a="atRm" data-id="${t.id}" aria-label="Revoke ${E(t.name)}">Revoke</button></td></tr>`).join('') : `<tr><td colspan="6"><div class="cs-empty"><b>No tokens yet.</b>Make one for a script that should use Relay as you.</div></td></tr>`;
  q('cs-acct').innerHTML = `<div class="grid g2" style="align-items:start"><div class="cs-stack">
  <div class="card"><h3>Password</h3><p class="d">Choose a password only you know.</p><form id="pwForm" data-f="pw" autocomplete="off" class="cs-w">
    <div class="cs-f"><label for="pwCurrent">Current password</label><input id="pwCurrent" type="password" autocomplete="current-password" required></div>
    <div class="cs-f"><label for="pwNew">New password</label><input id="pwNew" type="password" autocomplete="new-password" minlength="8" required></div>
    <div class="cs-f"><label for="pwConfirm">New password again</label><input id="pwConfirm" type="password" autocomplete="new-password" minlength="8" required></div>
    <button class="btn p" type="submit" data-a="nop">Change password</button><div class="cs-msg" id="pwMsg"></div><div class="cs-note">At least 8 characters. Changing it signs you out of other browsers.</div></form></div>
  <div class="card"><h3>PIN</h3><div class="cs-note first" style="margin-bottom:12px">A PIN is 4 to 8 digits, for signing in quickly. It only works on a trusted device. <span id="pinState">${AC.pin ? 'You have a PIN set.' : 'You have no PIN set.'}</span></div>
    <form id="pinForm" data-f="pin" autocomplete="off" class="cs-w"><div class="cs-fl"><div class="cs-f"><label for="pinPassword">Your password</label><input id="pinPassword" type="password" autocomplete="current-password" required></div><div class="cs-f"><label for="pinNew">PIN</label><input id="pinNew" type="password" inputmode="numeric" pattern="[0-9]*" minlength="4" maxlength="8" autocomplete="off" required></div></div>
    <div class="cs-acts"><button class="btn p" type="submit" id="pinSave" data-a="nop">${AC.pin ? 'Change PIN' : 'Set PIN'}</button>${AC.pin ? '<button class="btn" type="button" id="pinRemove" data-a="pinRm">Remove PIN</button>' : ''}</div><div class="cs-msg" id="pinMsg"></div></form></div>
  ${tok ? `<div class="card"><h3>API tokens</h3><div class="cs-note first" style="margin-bottom:12px">For scripts and other programs that use Relay's API as you. A token can do what your role can, except manage users, passwords, devices, tokens or the OpenAI key. Send it as <span class="cs-mono">Authorization: Bearer &lt;token&gt;</span>.</div>
    <div class="cs-scroll"><table class="dt cs-tbl"><thead><tr><th>Name</th><th>Starts with</th><th>Created</th><th>Expires</th><th>Last used</th><th><span class="sr" style="position:absolute;left:-9999px">Actions</span></th></tr></thead><tbody id="tokenList">${rows}</tbody></table></div>
    ${AC.secret ? secretBox('tokenSecret', 'Copy this token now and keep it somewhere safe. It is shown once; if it is lost, make a new one.', AC.secret) : ''}
    ${AC.panel ? `<div class="cs-panel" id="tokenPanel"><h4>New API token</h4><form data-f="tok" autocomplete="off"><div class="cs-fl"><div class="cs-f"><label for="tokenName">What it is for</label><input id="tokenName" type="text" maxlength="80" placeholder="Stream Deck in the booth" required></div><div class="cs-f" style="flex:0 1 180px"><label for="tokenDays">Expires after</label><select id="tokenDays"><option value="30">30 days</option><option value="90" selected>90 days</option><option value="365">365 days</option><option value="">Never</option></select></div></div><div class="cs-acts"><button class="btn p" type="submit" data-a="nop">Make token</button><button class="btn" type="button" data-a="atX">Cancel</button></div></form></div>` : `<button class="btn cs-add" id="tokenAdd" data-a="atNew">New token</button>`}</div>` : ''}
  </div><div class="cs-stack">
  <div class="card"><h3>This browser</h3><div class="cs-note first" id="deviceState">${AC.trusted ? 'This browser is a trusted device (Pastor’s office Mac). A PIN works here.' : 'This browser is not a trusted device, so a PIN will not work here. ' + (man ? 'You can trust it on the Users page.' : 'An administrator can trust it for you.')}</div></div>
  <div class="card"><h3>Preferences</h3><p class="d">New in the redesign. Theme and layout belong to this browser, not to your account.</p><button class="btn" data-a="prefs">${ic('tune', 'vertical-align:-3px;margin-right:6px')}Open layout preferences</button></div></div></div>`;
}
P.account = () => `<h2 class="t">My account</h2><p class="sub" id="acctWho"></p>${demo('ac', AC.as, [['owner', 'Owner (PIN set, trusted browser)'], ['operator', 'Operator (no tokens)']])}<div id="cs-acct"></div>`;
AFTER.account = () => {
  AC.panel = false; AC.secret = null; AC.pin = AC.as == 'owner'; AC.trusted = AC.as == 'owner'; accDraw();
  wire({
    demo: (k, v) => { AC.as = v; AC.pin = v == 'owner'; AC.trusted = v == 'owner'; AC.panel = false; AC.secret = null; accDraw(); },
    act: {
      nop() {},
      pinRm() { modal({ t: 'Remove your PIN?', body: 'You will sign in with your password everywhere, including on trusted devices.', ok: 'Remove PIN', danger: 1, fn() { AC.pin = false; accDraw(); toast('PIN removed.'); } }); },
      atNew() { AC.panel = true; AC.secret = null; accDraw(); q('tokenName').focus(); },
      atX() { AC.panel = false; accDraw(); },
      atRm(b) { const t = AC.tokens.find(x => x.id == +b.dataset.id); modal({ t: 'Revoke this token?', body: 'Revoke the token “' + E(t.name) + '”? Anything using it stops working at once.', ok: 'Revoke token', danger: 1, fn() { AC.tokens = AC.tokens.filter(x => x !== t); accDraw(); toast('Revoked ' + t.name + '.'); } }); },
      copy(b) { try { navigator.clipboard.writeText(b.dataset.t); } catch (e) { } b.textContent = 'Copied'; setTimeout(() => { b.textContent = 'Copy'; }, 1600); },
      secretDone() { AC.secret = null; accDraw(); },
      prefs(b) { toggle(b, { side: 'bottom', align: 'start', cls: 'pp', render: renderLayout }); }
    },
    sub: {
      pw() { const n = q('pwNew').value, c = q('pwConfirm').value; if (n !== c) { msg('pwMsg', 'The new passwords do not match.', 'err'); return; } ['pwCurrent', 'pwNew', 'pwConfirm'].forEach(i => q(i).value = ''); msg('pwMsg', 'Password changed.', 'ok'); toast('Password changed.'); },
      pin() { const p = q('pinNew').value; if (!/^\d{4,8}$/.test(p)) { msg('pinMsg', 'A PIN is 4 to 8 digits.', 'err'); return; } AC.pin = true; accDraw(); msg('pinMsg', 'PIN saved.', 'ok'); toast('PIN saved.'); },
      tok() { const t = { id: AC.nid++, name: q('tokenName').value.trim(), hint: Math.random().toString(36).slice(2, 6), created: 0, exp: q('tokenDays').value ? +q('tokenDays').value * 1440 : null, last: null }; AC.tokens.push(t); AC.panel = false; AC.secret = 'relay_' + t.hint + Array.from({ length: 5 }, () => Math.random().toString(36).slice(2, 8)).join(''); accDraw(); }
    }
  });
};
})();
