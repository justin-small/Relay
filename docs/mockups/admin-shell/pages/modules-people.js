/* People and screens modules, as pages inside the shell: Crew app, Intercom (two pages), Remote crew, Parent paging,
   Remote access, Signage, Web & YouTube and Alert events. Each page is one function (P.<id>); the shared bits are
   the small helpers below. Behaviour is delegated from one listener at the bottom (tabs, toggles, open/close,
   read-only preview), so a page needs no AFTER unless it does something of its own. Nothing here needs a server. */
(function () {
'use strict';

/* ---------- icons: the inner SVG of each module's AdminPage.icon ---------- */
Object.assign(I, {
  mod_crew: '<circle cx="6" cy="5" r="2.5"/><circle cx="11.5" cy="6" r="2"/><path d="M1.5 13c0-2.5 2-4 4.5-4s4.5 1.5 4.5 4M10 9.2c2.5-.3 4.5.9 4.5 3.8"/>',
  mod_intercom: '<path d="M2.5 9.5v-2a5.5 5.5 0 0 1 11 0v2"/><rect x="1.5" y="9" width="3" height="4.5" rx="1"/><rect x="11.5" y="9" width="3" height="4.5" rx="1"/>',
  mod_intercom_phones: '<rect x="4.5" y="1.5" width="7" height="13" rx="1.5"/><path d="M7 12.5h2"/>',
  mod_intercom_remote: '<circle cx="8" cy="8" r="6"/><path d="M2 8h12M8 2c2 2 2.5 4 2.5 6S10 12 8 14M8 2C6 4 5.5 6 5.5 8S6 12 8 14"/>',
  mod_parent_paging: '<path d="M8 2a3 3 0 0 1 3 3v2l1.5 3h-9L5 7V5a3 3 0 0 1 3-3zM6.5 12a1.5 1.5 0 0 0 3 0"/>',
  mod_remote_access: '<circle cx="8" cy="8" r="6"/><path d="M2 8h12M8 2c2 2 2.5 4 2.5 6S10 12 8 14M8 2C6 4 5.5 6 5.5 8S6 12 8 14"/>',
  mod_signage: '<rect x="1.5" y="2.5" width="13" height="9" rx="1"/><path d="M5.5 14h5M8 11.5V14"/>',
  mod_signage_web: '<circle cx="8" cy="8" r="6"/><path d="M2 8h12M8 2c2 2 2 10 0 12M8 2c-2 2-2 10 0 12"/>',
  mod_alert_events: '<path d="M2 8h3l2-5 2 10 2-5h3"/>',
  mp_grip: '<circle cx="6" cy="4" r=".7"/><circle cx="10" cy="4" r=".7"/><circle cx="6" cy="8" r=".7"/><circle cx="10" cy="8" r=".7"/><circle cx="6" cy="12" r=".7"/><circle cx="10" cy="12" r=".7"/>',
  mp_up: '<path d="M4 10l4-4 4 4"/>', mp_dn: '<path d="M4 6l4 4 4-4"/>', mp_up_file: '<path d="M8 11V3M4.5 6.5L8 3l3.5 3.5M3 13h10"/>'
});

/* ---------- css (every selector under .mp-) ---------- */
injectCSS('modules-people', `
#pages [data-g][hidden],.mp-page [hidden]{display:none!important}
.mp-page{display:block}
.mp-stack{display:grid;gap:14px}
.mp-sum{margin-left:6px;vertical-align:middle;font-size:11.5px;font-weight:600}
.mp-note{color:var(--muted);font-size:12px;margin:8px 0 0;line-height:1.5}
.mp-note.err{color:var(--err)}.mp-note.ok{color:var(--ok)}.mp-note.warn{color:var(--warn)}
.mp-note code,.mp-m{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--fg)}
.mp-banner{border:1px solid color-mix(in srgb,var(--err) 40%,transparent);background:color-mix(in srgb,var(--err) 9%,transparent);color:var(--err);border-radius:calc(var(--r) - 2px);padding:9px 12px;font-size:13px;line-height:1.45}
.mp-banner.warn{border-color:color-mix(in srgb,var(--warn) 40%,transparent);background:color-mix(in srgb,var(--warn) 9%,transparent);color:var(--warn)}
.mp-bar{display:flex;gap:10px 18px;align-items:center;justify-content:space-between;flex-wrap:wrap;margin:0 0 14px}
.mp-bar .tabs{margin:0;flex-wrap:wrap}
.mp-demos{display:flex;gap:8px 16px;align-items:center;flex-wrap:wrap}
.mp-demo{display:inline-flex;align-items:center;gap:8px}
.mp-lbl{font-size:12px;color:var(--muted);white-space:nowrap}
.mp-demo .tg{width:auto}.mp-demo .tg button{flex:none;padding:0 12px}
.mp-sub{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:0 0 12px}
.mp-sub .tabs{margin:0}
.mp-row{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end;margin:0 0 12px}
.mp-row .fld{flex:1 1 180px;margin:0;min-width:0}.mp-row .fld.s{flex:0 0 auto}.mp-row .fld.n{flex:0 0 7em}
.mp-row .fld.m{flex:0 1 20em}.mp-row .fld.l{flex:0 1 34em}
.mp-fld{margin:0 0 12px}.mp-fld label{color:var(--fg);font-weight:500}
.mp-w20{max-width:20em}.mp-w24{max-width:24em}.mp-w32{max-width:32em}
.mp-ctl{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:12px}
.mp-msg{font-size:12px;color:var(--muted)}
.mp-chk{display:inline-flex;gap:7px;align-items:center;margin:0 16px 6px 0;font-size:13px;color:var(--fg);cursor:pointer}
.mp-chk input{width:auto}
.mp-switch{display:flex;gap:10px;align-items:center;margin:0 0 12px;font-size:13px}
.mp-tw{overflow-x:auto}.mp-tw table{min-width:560px}
.mp-page .dt th{padding:10px 12px;font-size:12px}.mp-page .dt td{padding:10px 12px;vertical-align:middle}
.mp-act{display:flex;gap:6px;justify-content:flex-end;flex-wrap:wrap}
.mp-act{align-items:center}.mp-tw td:last-child .mp-act{flex-wrap:nowrap}.mp-list span[data-g]{display:contents}
/* the shell's .card .d (a description line) also matches the destructive button .btn.d */
.mp-page .btn.d{margin:0;color:#fff}.mp-page .btn.d.sm{font-size:12px}
.mp-page td input,.mp-page td select{padding:4px 8px}
.mp-page td input[type=checkbox]{width:auto}
.mp-sc{max-height:320px;overflow:auto;border:1px solid var(--border);border-radius:calc(var(--r) - 2px)}
.mp-sc table{min-width:760px}.mp-sc th{position:sticky;top:0;background:var(--card)}
.mp-page .btn:disabled,.mp-page .sw:disabled,.mp-page input:disabled,.mp-page select:disabled,.mp-page textarea:disabled{opacity:.55;cursor:not-allowed}
.mp-ro .mp-manage,.mp-ro [data-live]{display:none!important}.mp-page .tabs{flex-wrap:wrap}.mp-ro .mp-pick{pointer-events:none;opacity:.6}
.mp-onlyro{display:none}.mp-ro .mp-onlyro{display:block}
.mp-warn{color:var(--warn);font-weight:600}.mp-bad{color:var(--err);font-weight:700}.mp-dim{color:var(--muted)}
.mp-qr{background:#fff;color:#000;padding:8px;border-radius:8px;flex:none}
.mp-qr svg{display:block;width:100%;height:100%}
.mp-flex{display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.mp-grid2{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:14px;align-items:start}
.mp-ch{display:inline-block;width:11px;height:11px;border-radius:50%;background:var(--ch,var(--muted));vertical-align:-1px;margin-right:8px;flex:none}
.mp-chcell{border-left:4px solid var(--ch,transparent)!important}
.mp-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px;margin:4px 0 14px}
.mp-hc{border:1px solid var(--border);border-radius:var(--r);padding:11px 13px;background:var(--bg)}
.mp-hc b{display:block;margin-bottom:3px;font-weight:600}
.mp-hc .st{font-size:11px;text-transform:uppercase;letter-spacing:.05em;font-weight:700}
.mp-hc.ok .st{color:var(--ok)}.mp-hc.warn{border-color:color-mix(in srgb,var(--warn) 55%,var(--border))}.mp-hc.warn .st{color:var(--warn)}
.mp-hc.error{border-color:color-mix(in srgb,var(--err) 55%,var(--border))}.mp-hc.error .st{color:var(--err)}.mp-hc.idle .st{color:var(--muted)}
.mp-hc .mp-note{margin-top:4px}
.mp-th{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:8px 18px;margin:10px 0}
.mp-th label{display:flex;justify-content:space-between;gap:10px;align-items:center;margin:0;color:var(--fg)}.mp-th input{width:6.5em}
.mp-det{border:1px solid var(--border);border-radius:calc(var(--r) - 2px);padding:8px 12px;margin:0 0 14px}
.mp-det summary{cursor:pointer;font-weight:500}
.mp-edit{margin-top:14px;padding:14px 16px;border:1px solid var(--border);border-radius:var(--r);background:var(--bg)}
.mp-edit h4{margin:0 0 10px;font-size:14px}
.mp-pre{margin:0;max-height:240px;overflow:auto;white-space:pre-wrap;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--bg);border:1px solid var(--border);border-radius:calc(var(--r) - 2px);padding:10px 12px;color:var(--muted)}
.mp-list>div{display:flex;align-items:center;gap:12px;padding:10px 0;border-top:1px solid var(--border);flex-wrap:wrap}
.mp-list>div:first-child{border-top:0;padding-top:0}
.mp-list .grow{flex:1;min-width:9em}.mp-list b{font-weight:600}
.mp-code{font-size:26px;font-weight:700;letter-spacing:.08em;min-width:4.2em;font-variant-numeric:tabular-nums}
.mp-link{display:flex;gap:14px;align-items:center;flex-wrap:wrap;width:100%;padding:6px 0 4px}
.mp-link a{color:var(--fg);word-break:break-all}
.mp-thumb{width:96px;aspect-ratio:16/9;border-radius:6px;border:1px solid var(--border);display:grid;place-items:center;font-size:11px;color:var(--muted);flex:none;background:linear-gradient(135deg,color-mix(in srgb,var(--primary) var(--a,22%),var(--card)),var(--hover))}
.mp-thumb.off{background:var(--hover);background-image:repeating-linear-gradient(45deg,transparent 0 6px,color-mix(in srgb,var(--fg) 6%,transparent) 6px 12px)}
td .mp-thumb{width:72px;margin-bottom:4px}
.mp-mc{border:1px solid var(--border);border-radius:var(--r);background:var(--bg);padding:10px;min-width:0}
.mp-mc .mp-thumb{width:100%}.mp-mc .nm{font-size:13px;margin:8px 0 2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mp-mc.mp-pick{cursor:pointer}.mp-mc.mp-pick:hover{border-color:var(--primary)}
.mp-mc .mp-act{justify-content:flex-start;margin-top:8px}
.mp-mgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px}
.mp-items{display:flex;flex-direction:column;gap:8px}
.mp-item{display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;border:1px solid var(--border);border-radius:var(--r);background:var(--bg);padding:9px 11px}
.mp-item.off{opacity:.6}
.mp-item .nm{flex:1 1 170px;min-width:0;align-self:center}.mp-item .nm div:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mp-item .fld{margin:0}.mp-item input[type=number]{width:76px}.mp-item input[type=date]{width:142px}
.mp-item .mp-act{align-self:center}
.mp-drop{border:1px dashed var(--border);border-radius:var(--r);padding:18px;text-align:center;color:var(--muted)}
.mp-drop a{color:var(--primary);cursor:pointer;text-decoration:underline}
.mp-pills{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}
.mp-modal{position:fixed;inset:0;z-index:55;background:color-mix(in srgb,var(--bg) 70%,transparent);-webkit-backdrop-filter:blur(3px);backdrop-filter:blur(3px);display:grid;place-items:center;padding:16px}
.mp-modal .card{width:min(860px,100%)}
.mp-stage{aspect-ratio:16/9;border-radius:8px;border:1px solid var(--border);display:grid;place-items:center;text-align:center;font-size:24px;font-weight:600;padding:20px;background:linear-gradient(135deg,color-mix(in srgb,var(--primary) 30%,var(--card)),var(--hover))}
.mp-sep{height:1px;background:var(--border);margin:16px 0}
.mp-h4{font-size:13px;font-weight:600;margin:16px 0 4px}
@media(max-width:900px){.mp-grid2{grid-template-columns:1fr}.mp-item .fld{flex:1 1 120px}}
`);

/* ---------- small builders ---------- */
const q = (s) => [...document.querySelectorAll('#pages ' + s)];
const btn = (t, c, a) => { let cls = c || ''; a = (a || '').replace(/class="([^"]*)"/, (_, k) => { cls += ' ' + k; return ''; }); return `<button type="button" class="btn ${cls}" ${a}>${t}</button>`; };
const sm = (t, a, c) => btn(t, 'sm ' + (c || ''), a);
const bd = (k, t) => `<span class="badge ${k}">${t}</span>`;
const sbd = (k, t) => `<span class="badge sbadge ${k}"><i></i>${t}</span>`;
const note = (t, c, a) => `<p class="mp-note ${c || ''}" ${a || ''}>${t}</p>`;
const m = (t) => `<span class="mp-m">${t}</span>`;
const card = (h, d, body, a) => `<div class="card" ${a || ''}>${h ? `<h3>${h}</h3>` : ''}${d ? `<p class="d">${d}</p>` : ''}${body || ''}</div>`;
const fld = (id, label, inner, nt, cls) => `<div class="fld ${cls || ''}"><label${id ? ` for="${id}"` : ''}>${label}</label>${inner}${nt ? `<div class="mp-note">${nt}</div>` : ''}</div>`;
const mf = (id, label, inner, nt) => `<div class="mp-fld"><label for="${id}">${label}</label>${inner}${nt ? `<div class="mp-note">${nt}</div>` : ''}</div>`;
const sel = (id, opts, cur, attrs) => `<select${id ? ` id="${id}"` : ''} ${attrs || ''}>${opts.map(o => { const [v, l] = Array.isArray(o) ? o : [o, o]; return `<option value="${v}"${String(v) === String(cur) ? ' selected' : ''}>${l}</option>`; }).join('')}</select>`;
const sw = (id, on) => `<button type="button" class="sw${on ? ' on' : ''}" id="${id}" role="switch" aria-checked="${!!on}" onclick="this.classList.toggle('on');this.setAttribute('aria-checked',this.classList.contains('on'))"></button>`;
const swrow = (id, on, label) => `<div class="mp-switch">${sw(id, on)}<label for="${id}" style="margin:0;color:var(--fg);font-size:13px;cursor:pointer" onclick="document.getElementById('${id}').click()">${label}</label></div>`;
const chk = (label, on, extra) => `<label class="mp-chk"><input type="checkbox"${on ? ' checked' : ''} ${extra || ''}> ${label}</label>`;
const cell = (c, tag) => Array.isArray(c) ? `<${tag || 'td'} ${c[1]}>${c[0]}</${tag || 'td'}>` : `<${tag || 'td'}>${c}</${tag || 'td'}>`;
const tbl = (head, rows, id, bodyAttrs) => `<div class="mp-tw"><table class="dt"${id ? ` id="${id}"` : ''}><thead><tr>${head.map(c => cell(c, 'th')).join('')}</tr></thead><tbody${bodyAttrs ? ' ' + bodyAttrs : ''}>${rows.map(r => `<tr${r.a ? ' ' + r.a : ''}>${(r.c || r).map(c => cell(c)).join('')}</tr>`).join('')}</tbody></table></div>`;
const act = (...b) => `<div class="mp-act">${b.join('')}</div>`;
const pn = (g, v, cur, body, tag, cls) => `<${tag || 'div'} data-g="${g}" data-v="${v}"${v.split(' ').includes(cur) ? '' : ' hidden'}${cls ? ` class="${cls}"` : ''}>${body}</${tag || 'div'}>`;
const tabs = (g, items, cur) => `<div class="tabs" data-mp="${g}" role="tablist">${items.map(([v, l]) => `<button type="button" role="tab" data-v="${v}"${v === cur ? ' class="on"' : ''}>${l}</button>`).join('')}</div>`;
const sub = (g, items, cur) => `<div class="mp-sub">${tabs(g, items, cur)}</div>`;
const tgl = (g, label, items, cur) => `<span class="mp-demo"><span class="mp-lbl">${label}</span><div class="tg" data-mp="${g}" role="group">${items.map(([v, l]) => `<button type="button" data-v="${v}" aria-pressed="${v === cur}">${l}</button>`).join('')}</div></span>`;
const viewAs = `<span class="mp-demo"><span class="mp-lbl">Preview as</span><div class="tg" data-ro role="group"><button type="button" data-v="0" aria-pressed="true">Admin</button><button type="button" data-v="1" aria-pressed="false">Viewer</button></div></span>`;
const bar = (left, demos) => `<div class="mp-bar"><div>${left || ''}</div><div class="mp-demos">${(demos || []).join('')}${viewAs}</div></div>`;
const head = (t, s, sb) => `<h2 class="t">${t}${s ? ' ' + s : ''}</h2><p class="sub">${sb}</p>`;
const sum = (k, t, a) => `<span class="badge ${k} mp-sum"${a || ''}>${t}</span>`;
const page = (inner) => `<div class="mp-page"><p class="mp-note mp-vo" hidden style="margin:0 0 12px">You can view this page but not change it.</p>${inner}</div>`;
const errn = (id) => `<p class="mp-note err" id="${id}" hidden></p>`;
const inp = (id, a) => `<input${id ? ` id="${id}"` : ''} autocomplete="off" ${a || ''}>`;
const person = (n, small) => `<div class="row" style="flex-wrap:nowrap;gap:10px"><span class="av" style="width:28px;height:28px;font-size:11px;${tone(n)}">${ini(n)}</span><span class="two"><b>${n}</b>${small ? `<small>${small}</small>` : ''}</span></div>`;
const thumb = (lbl, a, off) => `<div class="mp-thumb${off ? ' off' : ''}" style="--a:${a || 22}%">${lbl || ''}</div>`;

/* a stand-in QR code: finder squares plus a fixed pattern (a real one comes from the vendored generator) */
function qr(n, seed) {
  let s = seed || 11; const r = () => (s = (s * 48271) % 2147483647) / 2147483647; let d = '';
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) { const f = (x < 8 && y < 8) || (x >= n - 8 && y < 8) || (x < 8 && y >= n - 8); if (!f && r() > .5) d += `M${x} ${y}h1v1h-1z`; }
  const fd = (x, y) => `<path fill-rule="evenodd" d="M${x} ${y}h7v7h-7zM${x + 1} ${y + 1}v5h5v-5z"/><path d="M${x + 2} ${y + 2}h3v3h-3z"/>`;
  return `<svg viewBox="-1 -1 ${n + 2} ${n + 2}" shape-rendering="crispEdges" role="img" aria-label="QR code" fill="currentColor"><path d="${d}"/>${fd(0, 0)}${fd(n - 7, 0)}${fd(0, n - 7)}</svg>`;
}
const qrBox = (px, seed) => `<div class="mp-qr" style="width:${px}px;height:${px}px">${qr(25, seed)}</div>`;

/* ======================================================================================== Crew app */
reg({ kind: 'page', id: 'crew', t: 'Crew app', g: 'Settings', ic: 'mod_crew', module: 'crew', perm: 'crew.view' });
P.crew = () => {
  const setup = `<div class="mp-grid2"><div class="mp-stack">
    ${card('The page', '', `
      ${mf('crName', 'Name on the home screen', inp('crName', 'maxlength="24" value="Grace Crew" class="mp-w20"'))}
      ${swrow('crPlan', true, "Show today's plan (from Planning Center, when it is on)")}
      <div class="mp-fld"><label>Church location for check-in <span class="mp-dim">(optional)</span></label>
        <div class="row" style="gap:8px">${inp('crLat', 'inputmode="decimal" placeholder="Latitude" value="39.7392" style="width:9em"')}${inp('crLon', 'inputmode="decimal" placeholder="Longitude" value="-104.9903" style="width:9em"')}${inp('crRad', 'inputmode="numeric" placeholder="Metres" value="150" style="width:6em"')}</div>
        ${note('A phone on the church network checks in by itself. Where a location is set, a person away from the network can tap Check in inside this radius (20 to 5000 metres, 150 if blank). Relay only compares the position with this one and keeps no coordinates.')}</div>
      ${mf('crHosts', 'Sites whose pages may be shown inside the crew page', `<textarea id="crHosts" rows="4" autocomplete="off" class="mp-w24">youtube.com\ndocs.google.com\ndrive.google.com\nvimeo.com</textarea>`, 'One site per line (its subdomains count too). Resources can embed a page only from these; any other address is a plain link. Leave it as it is to allow YouTube, Google Docs and Drive, and Vimeo.')}
      ${errn('crErr')}
      <div class="mp-ctl">${btn('Save', 'p', 'id="crSave" data-toast="Saved"')}<span class="mp-msg" id="crMsg"></span></div>`)}
    ${card('Opening it on a phone', '', `<p class="mp-note" style="margin:0">Crew open ${m('<span id="crLink">https://192.168.1.40:8443/crew</span>')} on this machine's HTTPS address (the one the operator panel uses). The phone has to trust Relay's certificate first, or the browser will not install the page or allow the microphone. The page is never reachable through remote viewer access.</p>`)}
    </div>
    ${card('Join by QR code', 'Put this on a screen or print it. A phone that scans it lands on a plain page that explains how to trust Relay\'s certificate, then opens the crew page at Relay\'s current address on the HTTPS port. The join page has its own plain-HTTP port (Relay restarts to change it).', `
      <div id="crJoinBox" class="mp-flex">${qrBox(180, 5).replace('class="mp-qr"', 'class="mp-qr" id="crQr"')}<div><div class="mp-note" style="margin:0">Join page: <span id="crJoinUrl" class="mp-m">http://192.168.1.40:8090/crew-join</span></div><div class="mp-note">Crew page: <span id="crCrewUrl" class="mp-m">https://192.168.1.40:8443/crew</span></div></div></div>`)}
    </div>`;

  const arrivals = [
    ['Maya Okafor', 'Camera', '7:45 AM', 'ok', 'Arrived 7:38 AM'], ['Daniel Park', 'Audio', '7:30 AM', 'warn', 'Arrived late 7:41 AM (location)'],
    ['Dana Reyes', 'Director', '7:30 AM', 'ok', 'Arrived 7:22 AM'], ['Priya Nair', 'Lyrics', '8:00 AM', '', 'Not yet'],
    ['Tom Reyes', 'Stage', '7:30 AM', 'err', 'Overdue'], ['Grace Liu', 'Video', '—', '', 'No crew account']];
  const today = `<div class="mp-stack">
    <div id="crBoardBox">${card('Arrivals', 'Who on today\'s Planning Center plan has arrived, against their team\'s earliest call time. Arrivals clear by themselves when a new plan takes over.', `
      ${errn('crBoardErr')}
      <div class="mp-ctl" style="margin:0 0 10px"><span id="crBoardCount" class="mp-msg">3 of 6 arrived</span>${btn('Clear arrivals', 'sm', 'id="crBoardClear" data-toast="Arrivals cleared"')}</div>
      ${tbl(['Name', 'Team', 'Call time', 'Status'], arrivals.map(a => [a[0], a[1], a[2], sbd(a[3], a[4])]), 'crBoard')}`)}</div>
    <div id="crListBox">${card('Checklists', 'Progress on the checklists written on the crew page\'s Checklists tab, for the people on today\'s plan. Everyone sees the lists for their Planning Center team plus the ones for everyone. Each service starts fresh; earlier ones stay here to look back on.', `
      ${errn('crListErr')}
      <div class="row" style="margin:0 0 10px;gap:10px"><label for="crListPlan" style="margin:0">Service</label><select id="crListPlan" style="width:auto"><option>Today</option><option>Sun, Nov 15</option><option>Sun, Nov 8</option></select><span id="crListCount" class="mp-msg">9 of 14 items done</span></div>
      ${tbl(['Name', 'Team', 'Checklist', 'Done'], [
        ['Maya Okafor', 'Camera', 'Camera setup', '5 of 5 ✓'], ['', '', 'Everyone: before you leave', ['1 of 3', 'title="Still open: Return headset; Sign out; Lights off"']],
        ['Daniel Park', 'Audio', 'Mixer scenes', ['3 of 6', 'title="Still open: Check wireless batteries; Mute choir mics; Save show file"']], ['', '', 'Everyone: before you leave', '0 of 3'],
        ['Priya Nair', 'Lyrics', 'Lyrics ready', '0 of 3']], 'crLists')}
      <div id="crLeadBox"><div class="mp-h4">Team leads</div>
        ${note('A team lead writes and edits the checklists and resources for their own position from the crew page. Operators write any.', '', 'style="margin-top:0"')}
        <div class="row" style="margin:10px 0;gap:8px"><select id="crLeadUser" aria-label="Person" style="width:auto"><option>Maya Okafor</option><option>Daniel Park</option><option>Priya Nair</option></select>${inp('crLeadPos', 'maxlength="80" placeholder="Planning Center team" style="width:14em"')}${btn('Make lead', '', 'id="crLeadAdd" data-toast="Saved"')}</div>
        ${tbl(['Name', 'Position', ''], [['Dana Reyes', 'Director', act(sm('Remove', 'data-toast="Removed"'))], ['Daniel Park', 'Audio', act(sm('Remove', 'data-toast="Removed"'))]], 'crLeads')}</div>`)}</div>
    <div id="crPageBox">${card('Page the crew', 'A page buzzes each person\'s phone, and buzzes again for up to two minutes until they confirm on their own link. People turn pages on from the crew page on their phone.', `
      ${mf('crPageText', 'What the page says', inp('crPageText', 'maxlength="140" class="mp-w32" placeholder="Cameras to the green room"'))}
      <div class="mp-fld"><span class="mp-dim">Teams serving today</span> <span id="crPageTeams" style="margin-left:8px">
        ${pn('pc', 'on', 'on', chk('Camera (3/4)') + chk('Audio (2/2)') + chk('Stage (2/3)') + chk('Lyrics (1/1)'), 'span')}
        ${pn('pc', 'off', 'on', 'None (Planning Center is off or nobody is scheduled).', 'span')}</span></div>
      <div class="mp-fld"><span class="mp-dim">People</span> <span id="crPagePeople" style="margin-left:8px">${chk('Maya Okafor · Camera 1')}${chk('Luis Ortega · Camera 2')}${chk('Daniel Park · Audio A1')}${chk('Tom Reyes · Stage manager')}${chk('Grace Liu · Video (no phone)')}</span></div>
      ${errn('crPageErr')}
      <div class="mp-ctl">${btn('Send page', 'p', 'id="crPageSend" data-req="#crPageText" data-fail="Write what the page says first." data-toast="Sent to 3 people"')}<span class="mp-msg" id="crPageMsg"></span></div>
      ${tbl(['Page', 'Who'], [['Cameras to the green room — Dana Reyes <span class="mp-warn">(buzzing)</span>', 'Maya Okafor … waiting, Luis Ortega ✓, Grace Liu no phone'], ['Audio check at 7:15 — Dana Reyes', 'Daniel Park ✓, Tom Reyes ✓']], 'crPages')}`)}</div>
    </div>`;

  const calls = card('Call times', 'Page a Planning Center team on a schedule: minutes before each of today\'s service times, or at a set day and time. It reaches the people on that team serving today who have a crew account, and buzzes like any page. A call time that Relay misses by a minute is skipped, never sent late.', `
    <div class="mp-row">
      ${fld('ctTeam', 'Team', `<input id="ctTeam" list="ctTeams" maxlength="80" autocomplete="off" placeholder="Camera"><datalist id="ctTeams"><option value="Camera"><option value="Audio"><option value="Stage"><option value="Lyrics"></datalist>`, '', 'm')}
      ${fld('ctKind', 'When', sel('ctKind', [['before', 'Before the service'], ['fixed', 'Day and time']], 'before', 'data-mp="ctk"'), '', 's')}
      ${pn('ctk', 'before', 'before', `<div class="fld"><label for="ctMin">Minutes before</label><input id="ctMin" type="number" min="0" max="1440" value="60" style="width:6em"></div>`, 'div', 's')}
      ${pn('ctk', 'fixed', 'before', `<div class="row" style="align-items:flex-end;gap:8px"><div class="fld"><label for="ctDay">Day</label>${sel('ctDay', [[6, 'Sunday'], [0, 'Monday'], [1, 'Tuesday'], [2, 'Wednesday'], [3, 'Thursday'], [4, 'Friday'], [5, 'Saturday']], 6)}</div><div class="fld"><label for="ctClock">Time</label><input id="ctClock" type="time" value="08:00"></div></div>`)}
    </div>
    ${mf('ctMsg', 'What the page says', inp('ctMsg', 'maxlength="140" class="mp-w32" placeholder="Call time: please head to the stage"'))}
    ${errn('ctErr')}
    <div class="mp-ctl" style="margin-bottom:14px">${btn('Add call time', 'p', 'id="ctAdd" data-req="#ctTeam" data-fail="Choose or type a team." data-toast="Saved"')}</div>
    ${tbl(['Team', 'When', 'Says', 'Last', ''], [
      ['Camera', '60 min before each service', 'Camera team: call time, please head to the stage', 'Sun 7:00 AM · 4 people', act(sm('Turn off', 'data-toast="Turned off"'), sm('Remove', 'data-toast="Removed"'))],
      ['Audio', '90 min before each service', 'Audio: soundcheck in 90 minutes', 'Sun 6:30 AM · 2 people', act(sm('Turn off', 'data-toast="Turned off"'), sm('Remove', 'data-toast="Removed"'))],
      ['Lyrics (off)', 'Saturday 19:00', 'Lyrics: songs are in ProPresenter', 'Not yet', act(sm('Turn on', 'data-toast="Turned on"'), sm('Remove', 'data-toast="Removed"'))],
      ['Stage', '45 min before each service', 'Stage: doors open soon', 'Sun 8:15 AM · nobody to page', act(sm('Turn off', 'data-toast="Turned off"'), sm('Remove', 'data-toast="Removed"'))]], 'ctRules')}`);

  const people = card('People', 'Give each person the role they fill ("Camera 1"). With Planning Center on, Relay matches a name to the people scheduled there once it is sure, remembers the match, and offers you Planning Center\'s spelling.', `
    ${errn('crPeopleErr')}
    ${pn('pc', 'on', 'on', `<div class="mp-ctl" style="margin:0 0 10px">${btn('Match names now', '', 'id="crScan" data-toast="Matched 2 names"')}</div>`)}
    ${tbl(['Name', 'Role', 'Planning Center'], [
      ['Dana Reyes', inp('', 'maxlength="40" value="Director" aria-label="Role for Dana Reyes"'), pn('pc', 'on', 'on', 'Matched ' + sm('Wrong person', 'data-toast="Match removed"'), 'span') + pn('pc', 'off', 'on', '—', 'span')],
      ['Maya Okafor', inp('', 'maxlength="40" value="Camera 1" aria-label="Role for Maya Okafor"'), pn('pc', 'on', 'on', 'Planning Center spells it “Maya Okafor-Lee” ' + sm('Use it', 'data-toast="Name updated"') + ' ' + sm('Keep mine', 'data-toast="Kept"'), 'span') + pn('pc', 'off', 'on', '—', 'span')],
      ['Daniel Park', inp('', 'maxlength="40" value="Audio A1" aria-label="Role for Daniel Park"'), pn('pc', 'on', 'on', 'Matched to Daniel J. Park ' + sm('Wrong person', 'data-toast="Match removed"'), 'span') + pn('pc', 'off', 'on', '—', 'span')],
      ['Priya Nair', inp('', 'maxlength="40" value="Lyrics" aria-label="Role for Priya Nair"'), '—'],
      ['Tom Reyes (off)', inp('', 'maxlength="40" value="Stage manager" aria-label="Role for Tom Reyes"'), '—']], 'crPeople')}`);

  return head('Crew app', '', 'An installable page for the people who serve. They sign in with a Relay account made on the Users page (the Member role is enough) and see what their role may use.') +
    page(bar(tabs('ct', [['setup', 'Setup'], ['today', 'Today'], ['calls', 'Call times'], ['people', 'People']], 'setup'), [tgl('pc', 'Planning Center', [['on', 'On'], ['off', 'Off']], 'on')]) +
      pn('ct', 'setup', 'setup', setup) + pn('ct', 'today', 'setup', today) + pn('ct', 'calls', 'setup', calls) + pn('ct', 'people', 'setup', people));
};

/* ======================================================================================== Intercom */
reg({ kind: 'page', id: 'intercom', t: 'Intercom', g: 'Automation', ic: 'mod_intercom', module: 'intercom', perm: 'intercom.view', parent: 'Intercom' });
reg({ kind: 'page', id: 'intercom-phones', t: 'Signed-in phones', g: 'Automation', ic: 'mod_intercom_phones', module: 'intercom', perm: 'intercom.manage', parent: 'Intercom' });

const CH = { Production: 'hsl(210 80% 60%)', Cameras: 'hsl(150 60% 48%)', Band: 'hsl(35 90% 55%)', Stage: 'hsl(285 60% 62%)', Lighting: 'hsl(350 75% 60%)' };
const chTone = (n) => `style="--ch:${CH[n]}"`;
const lvl = (v, l) => l === 'bad' ? `<span class="mp-bad">${v}</span>` : l === 'warn' ? `<span class="mp-warn">${v}</span>` : v;

P.intercom = () => {
  const liveTab = () => {
    const chRows = [['Production', 'Pre-empt', 'Dana Reyes', 9, 0], ['Cameras', 'Pre-empt', '—', 5, 1], ['Band', 'Party line', 'Daniel Park, Anna Beck', 6, 0], ['Stage', 'One at a time', '—', 4, 0], ['Lighting', 'Pre-empt', '—', 2, 0]]
      .map(r => ({ c: [[r[0], `class="mp-chcell" ${chTone(r[0])}`], r[1], r[2], r[3], r[4], act(sm('Mute channel', 'data-live data-toast="Channel muted"'))] }));
    const people = [
      ['Dana Reyes', 'Director', 1, 'Dana\'s iPhone', 'Production', 'Production, Cameras', ['0.2 %', ''], ['8 ms', '']],
      ['Maya Okafor', 'Camera 1', 3, 'Pixel 8', '—', 'Cameras, Production', ['0.4 %', ''], ['12 ms', '']],
      ['Luis Ortega', 'Camera 2', 3, 'iPhone 13', '—', 'Cameras, Production', ['3.1 %', 'warn'], ['41 ms', 'warn']],
      ['Daniel Park', 'Audio A1', 2, 'Galaxy S22', 'Band', 'Band, Production', ['0.1 %', ''], ['6 ms', '']],
      ['Tom Reyes', 'Stage manager', 2, 'iPhone SE', '—', 'Stage, Production', ['0.3 %', ''], ['9 ms', '']],
      ['Priya Nair', 'Lyrics', 4, 'Church iPad', '—', 'Production', ['7.8 %', 'bad'], ['72 ms', 'bad']]]
      .map(p => [p[0], p[1], p[2], p[3], 'Local', p[4], p[5], lvl(...p[6]), lvl(...p[7]), act(sm('Mute', 'data-live data-toast="Muted"'), sm('Sign out', 'class="mp-manage" data-toast="Signed out"'))]);
    return `<div class="mp-stack">${card('Live ' + pn('ls', 'pop', 'pop', sum('', '6 phones', 'id="icLiveSum"'), 'span') + pn('ls', 'none', 'pop', sum('', '0 phones'), 'span'), '', `
      <div class="mp-row" id="icLiveBar" style="align-items:flex-end">
        ${fld('icLiveLabel', 'Production label', inp('icLiveLabel', 'type="text" maxlength="80" placeholder="Sunday 9:00" value="Sunday 9:00"'), '', 'm')}
        ${btn('Set label', '', 'id="icLiveLabelSave" data-live data-toast="Label set"')}${btn('Use Planning Center plan name', '', 'id="icLiveLabelPlan" data-live data-toast="Label set from the plan"')}
        ${btn('Release all talk', '', 'id="icReleaseAll" data-live data-toast="All talk released"')}${btn('Mute all', 'd', 'id="icMuteAll" data-live data-muteall')}
      </div>
      <div class="mp-banner" id="icMutedBanner" hidden>Everything is muted by the operator. Nobody can talk until you unmute.</div>
      ${pn('ls', 'pop', 'pop', `<h3 style="margin:14px 0 8px">Channels now</h3>${tbl(['Channel', 'Mode', 'Talking', 'Listening', 'Waiting', ''], chRows, 'icLiveChTable')}
        <h3 style="margin:18px 0 8px">Connected</h3>${tbl(['Person', 'Role', 'Priority', 'Phone', 'Where', 'Talking', 'Listening', 'Loss', 'Jitter', ''], people, 'icLiveConTable')}`)}
      ${pn('ls', 'none', 'pop', `<h3 style="margin:14px 0 6px">Channels now</h3>${note('No channels yet.', '', 'id="icLiveChEmpty"')}<h3 style="margin:18px 0 6px">Connected</h3>${note('Nobody is on the intercom.', '', 'id="icLiveConEmpty"')}`)}
      ${note('Changes here apply at once and are logged. Mutes are live state: they clear when Relay restarts, and they never change who is on a channel.')}
      ${errn('icLiveErr')}`)}</div>`;
  };
  const health = () => {
    const cards = [['LiveKit', 'ok', 'Running: 5 rooms, 9 on'], ['Relay app', 'ok', 'Event loop 4 ms behind; 612 MB'], ['Database', 'ok', 'Write 3 ms'], ['Cloudflare bridge', 'idle', 'Not running'],
      ['Audio quality', 'error', 'Priya Nair: 7.8 % loss, 72 ms jitter (2 phones)'], ['Local latency', 'ok', 'Round trip p50 18 ms, p95 41 ms'], ['Talk grant time', 'warn', 'Talk grant p95 120 ms (p50 34 ms)']];
    const W = { ok: 'OK', warn: 'Warning', error: 'Problem', idle: 'Idle' };
    const th = [['lag_warn_ms', 'Event-loop lag, warn above (ms)', 50], ['lag_error_ms', 'Event-loop lag, error above (ms)', 250], ['lag_error_s', '… held for (s)', 15], ['rss_warn_mb', 'Relay memory, warn above (MB)', 1024], ['db_write_warn_ms', 'Database write, warn above (ms)', 50], ['wal_warn_mb', 'Database WAL file, warn above (MB)', 64], ['backup_warn_h', 'Last backup, warn when older than (h)', 26], ['bridge_error_s', 'Cloudflare bridge down, error after (s)', 120], ['loss_warn_pct', 'Packet loss, warn above (%)', 2], ['loss_error_pct', 'Packet loss, error above (%)', 5], ['jitter_warn_ms', 'Jitter, warn above (ms)', 30], ['jitter_error_ms', 'Jitter, error above (ms)', 60], ['latency_warn_ms', 'Round trip p95, warn above (ms)', 150], ['grant_warn_ms', 'Talk grant p95, warn above (ms)', 100]];
    const ev = [
      ['11/15/2026, 8:51:12 AM', 'mute_channel', '', '', 'Cameras', 'Dana Reyes', ''], ['11/15/2026, 8:47:40 AM', 'audio_warning', 'Priya Nair', 'Church iPad', '', '', 'loss_pct 7.8, jitter_ms 72'],
      ['11/15/2026, 8:30:02 AM', 'production_label', '', '', '', 'Dana Reyes', 'label Sunday 9:00'], ['11/15/2026, 8:12:55 AM', 'device_connected', 'Daniel Park', 'Galaxy S22', '', '', ''],
      ['11/15/2026, 8:09:31 AM', 'device_connected', 'Maya Okafor', 'Pixel 8', '', '', ''], ['11/15/2026, 7:58:20 AM', 'member_set', 'Luis Ortega', '', 'Cameras', 'Dana Reyes', 'can_talk true, can_listen true, priority 3'],
      ['11/15/2026, 7:41:09 AM', 'device_signed_in', 'Tom Reyes', 'iPhone SE', '', '', 'platform ios'], ['11/15/2026, 7:02:44 AM', 'device_disconnected', 'Anna Beck', 'Pixel 7', '', '', 'reason closed'],
      ['11/14/2026, 6:20:15 PM', 'thresholds_changed', '', '', '', 'Justin Small', 'loss_warn_pct 2']];
    return `<div class="mp-stack">${card('Health ' + sum('error', 'problem', 'id="icHealthSum"'), 'Checked every 5 seconds. Relay restarts LiveKit when it stops answering; nothing else is restarted.', `
      <div class="mp-cards" id="icCards">${cards.map(c => `<div class="mp-hc ${c[1]}"><b>${c[0]}</b><div class="st">${W[c[1]]}</div><p class="mp-note">${c[2]}</p></div>`).join('')}</div>
      <details class="mp-det" id="icThBox"><summary>Thresholds</summary>
        <div class="mp-th" id="icTh">${th.map(t => `<label><span>${t[1]}</span><input type="number" step="any" data-key="${t[0]}" placeholder="${t[2]}"${t[0] === 'loss_warn_pct' ? ' value="2"' : ''}></label>`).join('')}</div>
        <div class="mp-ctl" style="margin-top:8px">${btn('Save thresholds', 'p', 'id="icThSave" data-toast="Thresholds saved."')}${btn('Back to defaults', '', 'id="icThReset" data-toast="Thresholds reset"')}</div></details>
      ${errn('icHealthErr')}`)}
    ${card('Connection log', '', `
      <div class="row" style="gap:10px;margin:0 0 12px;align-items:flex-end">
        ${fld('icLogHours', 'Show', sel('icLogHours', [[1, 'last hour'], [24, 'last 24 hours'], [168, 'last 7 days'], [720, 'last 30 days']], 24), '', 's')}
        ${fld('icLogKind', 'Kind', inp('icLogKind', 'type="text" maxlength="40" placeholder="e.g. device_" style="width:11em" data-filt="#icEvents"'), '', 's')}
        ${btn('Export CSV', '', 'id="icLogCsv" data-toast="Exporting the log as CSV"')}</div>
      ${note('Nothing logged in this period.', '', 'id="icEventsEmpty" hidden')}
      <div class="mp-sc" id="icEventsTable"><table class="dt"><thead><tr>${['Time', 'Event', 'Person', 'Phone', 'Channel', 'By', 'Detail'].map(h => `<th>${h}</th>`).join('')}</tr></thead>
        <tbody id="icEvents">${ev.map(e => `<tr data-k="${e[1]}">${e.map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>
      ${note('Connections, sign-ins and permission changes are logged and kept for 30 days. Audio is never recorded.')}`)}</div>`;
  };
  const livekit = () => `<div class="mp-stack">${card('LiveKit ' + pn('lk', 'run', 'run', sum('ok', 'running'), 'span') + pn('lk', 'err', 'run', sum('err', 'error'), 'span'), 'The media server behind the crew intercom. Relay runs it on this computer; nothing leaves your network.', `
    ${sub('lk', [['run', 'Running'], ['err', 'Stopped']], 'run')}
    ${pn('lk', 'run', 'run', note('Running for 3 h.', 'ok', 'id="icState" style="margin-top:0"'))}
    ${pn('lk', 'err', 'run', note('LiveKit stopped after 5 restarts in 10 minutes. See the log below.', 'err', 'id="icState" style="margin-top:0"'))}
    ${note('LiveKit 1.8.0, media on 192.168.1.40.', '', 'id="icFacts"')}
    <div class="mp-ctl">${btn('Restart LiveKit', 'mp-manage', 'id="icRestart" data-toast="Restarting LiveKit…"')}</div>
    ${note('Phones on the network need these ports open to this computer: ' + m('7880/tcp, 7882/udp') + '. On Linux, allow them in the firewall; macOS and Windows ask the first time LiveKit starts.', '', 'id="icPorts"')}`)}
    ${card('LiveKit log', '', `<pre class="mp-pre" id="icLog">2026-11-15 06:58:02 INF starting LiveKit server version=1.8.0 nodeIP=192.168.1.40
2026-11-15 06:58:02 INF using single-node routing
2026-11-15 07:41:10 INF participant active room=production participant=tom-reyes
2026-11-15 08:09:32 INF participant active room=cameras participant=maya-okafor
2026-11-15 08:47:40 WRN high packet loss participant=priya-nair loss=7.8
2026-11-15 08:51:12 INF room updated room=cameras</pre>`)}
    ${errn('icErr')}</div>`;
  const channels = () => {
    const rows = [['Production', 'Pre-empt', 1, 'none', 9], ['Cameras', 'Pre-empt', 1, 'none', 5], ['Band', 'Party line', 8, 'none', 6], ['Stage', 'One at a time', 1, 'none', 4], ['Lighting', 'Pre-empt', 1, '30 s', 2]]
      .map(r => ({ a: 'draggable="true"', c: [[`<svg class="i" viewBox="0 0 16 16" style="color:var(--muted);cursor:grab">${I.mp_grip}</svg>`, 'style="width:28px"'], `<span class="mp-ch" ${chTone(r[0])}></span>${r[0]}`, r[1], r[2], r[3], r[4], act(sm('Edit', `data-open="#icEdit" data-toast="Editing ${r[0]}"`))] }));
    const mem = [['Dana Reyes', 1, 1, 1], ['Maya Okafor', 1, 1, 3], ['Luis Ortega', 1, 1, 3], ['Anna Beck', 0, 1, 5], ['Tom Reyes', 1, 1, 2]];
    return `<div class="mp-stack">${card('Channels', 'Standing channels with a standing member list. A production never changes who is on which channel. Drag rows to set the order phones show.', `
      <div class="mp-row" style="align-items:flex-end">${fld('icLabel', 'Production label', inp('icLabel', 'type="text" maxlength="80" placeholder="Sunday 9:00" value="Sunday 9:00"'), '', 'm')}${btn('Save label', '', 'id="icLabelSave" data-toast="Saved"')}${btn('Use Planning Center plan name', '', 'id="icLabelPlan" data-toast="Label set from the plan"')}</div>
      ${sub('chs', [['list', 'Channels set up'], ['empty', 'No channels yet']], 'list')}
      ${pn('chs', 'list', 'list', tbl(['', 'Name', 'When two people press talk', 'Max talkers', 'Latch limit', 'Members', ''], rows, 'icChannelsTable', 'id="icChannels"'))}
      ${pn('chs', 'empty', 'list', note('No channels yet.', '', 'id="icChannelsEmpty" style="margin-top:0"'))}
      <div class="mp-ctl">${inp('icNewName', 'type="text" maxlength="60" placeholder="New channel name" style="width:16em"')}${btn('New channel', '', 'id="icNewChannel" data-req="#icNewName" data-fail="Give the channel a name." data-toast="Channel created"')}</div>
      <div class="mp-edit" id="icEdit"><h4 id="icEditTitle">Edit Cameras</h4>
        <div class="mp-row">
          ${fld('icEName', 'Name', inp('icEName', 'type="text" maxlength="60" value="Cameras"'), '', 'm')}
          ${fld('icEColour', 'Colour', '<input type="color" id="icEColour" value="#3b82f6" style="width:3.5em;padding:2px">', '', 's')}
          ${fld('icEMode', 'When two people press talk', sel('icEMode', [['preempt', 'Pre-empt'], ['party', 'Party line'], ['queue', 'One at a time']], 'preempt', 'data-mp="icmode"'), '', 's')}
          ${fld('icEMax', 'Max talkers', '<input type="number" id="icEMax" min="1" max="16" value="1" style="width:5.5em">', '', 'n')}
          ${fld('icELatch', 'Latch limit (s, 0 = none)', '<input type="number" id="icELatch" min="0" max="86400" value="0" style="width:7em">', '', 's')}
        </div>
        <h4>Members</h4>
        ${tbl(['Person', 'Talk', 'Listen', [`Priority`, 'class="icPrio" data-g="icmode" data-v="preempt queue"'], ''], mem.map(p => [p[0], `<input type="checkbox"${p[1] ? ' checked' : ''} aria-label="${p[0]} can talk">`, `<input type="checkbox"${p[2] ? ' checked' : ''} aria-label="${p[0]} can listen">`, [`<input type="number" min="1" max="9" value="${p[3]}" style="width:4.5em" aria-label="${p[0]} priority">`, 'class="icPrio" data-g="icmode" data-v="preempt queue"'], act(sm('Remove', 'data-toast="Removed from the channel"'))]), '', 'id="icMembers"')}
        <div class="mp-ctl"><input type="text" id="icAddQ" placeholder="Add a user…" list="icAddList" autocomplete="off" style="width:16em"><datalist id="icAddList"><option value="Priya Nair (priya)"><option value="Grace Liu (grace)"></datalist>${btn('Add', '', 'id="icAdd" data-req="#icAddQ" data-fail="Pick a person from the list." data-toast="Added"')}</div>
        <div class="mp-ctl" style="margin-top:14px">${btn('Save', 'p', 'id="icESave" data-toast="Saved" data-close="#icEdit"')}${btn('Cancel', '', 'id="icECancel" data-close="#icEdit"')}${btn('Delete channel', 'd', 'id="icEDelete" data-toast="Deleted Cameras. Maya Okafor, Luis Ortega and 3 more lost it."')}</div>
        ${errn('icEErr')}</div>`)}</div>`;
  };
  return head('Intercom', sum('ok', 'running', 'id="icSum"'), 'The media server behind the crew intercom. Relay runs it on this computer; nothing leaves your network.') +
    page(bar(tabs('ict', [['live', 'Live'], ['channels', 'Channels'], ['health', 'Health'], ['livekit', 'LiveKit']], 'live'), []) +
      pn('ict', 'live', 'live', sub('ls', [['pop', 'People on'], ['none', 'Nobody on']], 'pop') + liveTab()) +
      pn('ict', 'channels', 'live', channels()) + pn('ict', 'health', 'live', health()) + pn('ict', 'livekit', 'live', livekit()));
};

P['intercom-phones'] = () => {
  const rows = [['Dana Reyes', 'Dana\'s iPhone', '2026-11-02', '2026-11-15', 'in 21 d', 'On intercom'], ['Maya Okafor', 'Pixel 8', '2026-11-04', '2026-11-15', 'in 23 d', 'On intercom'],
    ['Luis Ortega', 'iPhone 13', '2026-10-28', '2026-11-15', 'in 17 d', 'On intercom'], ['Daniel Park', 'Galaxy S22', '2026-11-09', '2026-11-15', 'in 28 d', 'On intercom'],
    ['Anna Beck', 'Pixel 7', '2026-10-19', '2026-11-08', 'in 8 d', 'Idle'], ['Sam Lee', 'Old iPhone 8', '2026-09-14', '2026-10-05', 'today', 'Expired']]
    .map(r => [r[0], r[1], r[2], r[3], r[4], sbd(r[5] === 'On intercom' ? 'ok' : r[5] === 'Idle' ? 'warn' : 'err', r[5]), act(sm('Sign out', 'class="mp-manage" data-toast="Signed out"'))]);
  return head('Signed-in phones', '', 'Each crew member signs in on their own phone with their own account. Signing a phone out ends its intercom at once.') +
    page(bar(sub('ph', [['full', 'Phones signed in'], ['empty', 'None signed in']], 'full').replace('class="mp-sub"', 'style="margin:0"'), []) +
      card('', '', `<div id="icPhonesSection">        ${pn('ph', 'full', 'full', tbl(['Person', 'Phone', 'Signed in', 'Last seen', 'Key renews', 'Status', ''], rows, 'icPhonesTable', 'id="icPhones"') + `<div class="mp-ctl">${btn('Sign out all phones', 'd mp-manage', 'id="icSignOutAll" data-toast="Every phone signed out"')}</div>`)}
        ${pn('ph', 'empty', 'full', note('No phones are signed in.', '', 'id="icPhonesEmpty" style="margin-top:0"'))}</div>${errn('icPhonesErr')}`));
};

/* ======================================================================================== Remote crew */
reg({ kind: 'page', id: 'intercom-remote', t: 'Remote crew', g: 'Automation', ic: 'mod_intercom_remote', module: 'intercom_remote', perm: 'intercom_remote.view' });
P['intercom-remote'] = () => {
  const ppl = [['Dana Reyes', 'Director', 1, true], ['Maya Okafor', 'Camera 1', 0, true], ['Luis Ortega', 'Camera 2', 0, false], ['Anna Beck', 'Band', 2, true], ['Pastor Mark Hale', 'Speaker', 0, false]];
  const sess = [['Dana Reyes', '2026-11-15 08:44', 'now', ''], ['Anna Beck', '2026-11-15 08:50', 'now', ''], ['Anna Beck', '2026-11-14 19:02', '2026-11-14 20:31', 'closed'], ['Dana Reyes', '2026-11-13 12:15', '2026-11-13 12:16', 'link_lost'], ['Maya Okafor', '2026-11-08 07:30', '2026-11-08 07:59', 'signed_out']];
  const st = (v, cls, t) => pn('br', v, 'connected', note(t, cls, 'id="irState" style="margin:0 0 14px"'));
  return head('Remote crew', pn('br', 'connected', 'connected', sum('ok', 'live', 'id="irSum"'), 'span') + pn('br', 'reconnecting missing', 'connected', sum('err', 'not connected'), 'span'), 'Crew who may use the intercom from outside the building, through Cloudflare. Local phones and local audio never depend on this.') +
    page(bar(sub('br', [['connected', 'Connected'], ['reconnecting', 'Reconnecting'], ['missing', 'Not on this computer'], ['off', 'Off']], 'connected').replace('class="mp-sub"', 'style="margin:0"'), []) +
      st('connected', 'ok', 'Connected to Cloudflare') + st('reconnecting', 'err', 'Lost the link to Cloudflare. Trying again.') + st('missing', 'err', 'The Cloudflare bridge can\'t run on this computer.') + st('off', '', '') +
      `<div class="mp-stack">${card('Who may connect remotely', 'Only people marked here can connect from outside, even with a valid account. They sign in at the install\'s remote address with their own Relay login and get the same channels and rights as on site.',
        tbl(['Person', 'Role', 'Remote phones', ''], ppl.map(p => [`${person(p[0])}`, p[1], p[2], act(
          `<span class="badge ${p[3] ? 'ok' : ''}" data-allowbadge>${p[3] ? 'Allowed' : 'Not allowed'}</span>`,
          `<button type="button" class="btn sm mp-manage" data-allow>${p[3] ? 'Remove remote access' : 'Allow remote'}</button>`,
          p[2] ? sm('Sign out remote phones', 'class="mp-manage" data-toast="Remote phones signed out"') : '')]), 'irPeople'))}
        ${card('Remote connections', '', tbl(['Person', 'Started', 'Ended', 'Why'], sess, 'irSessions'))}
        ${errn('irErr')}</div>`);
};

/* ======================================================================================== Parent paging */
reg({ kind: 'page', id: 'parentpaging', t: 'Parent paging', g: 'Live', ic: 'mod_parent_paging', module: 'parent_paging', perm: 'parent_paging.view' });
P.parentpaging = () => {
  const pg = (code, room, st, meta, age, btns) => `<div><div class="mp-code">${code}</div><div class="grow"><div>${room}</div><div class="mp-note ${st === 'waiting' ? 'err' : ''}" style="margin:0" data-age>${meta} · ${age}</div></div>${btns}</div>`;
  const pages = `<div class="mp-list" id="ppgPages">
      ${pg('B4K2', 'Nursery', 'waiting', 'Waiting for you', '45 s ago', pn('ppm', 'queue', 'queue', btn('Show', 'p sm', 'data-toast="Showing B4K2 on screen"'), 'span') + btn('Clear', 'sm d', 'data-toast="Page cleared"'))}
      ${pg('M7X9', 'Preschool', 'shown', 'On screen', '2 min ago', btn('Clear', 'sm d', 'data-toast="Page cleared"'))}
      ${pg('T3Q8', 'Toddlers', 'waiting', 'Waiting for you', '12 s ago', pn('ppm', 'queue', 'queue', btn('Show', 'p sm', 'data-toast="Showing T3Q8 on screen"'), 'span') + btn('Clear', 'sm d', 'data-toast="Page cleared"'))}</div>`;
  const rooms = [['Nursery', 'Check-Ins: Sunday 9:00 service', true], ['Toddlers', 'Check-Ins: Sunday 9:00 service'], ['Preschool', 'Check-Ins: Sunday 9:00 service'], ['Elementary', 'Check-Ins room 4172']];
  const roomRow = (r) => `<div><div class="grow"><b>${r[0]}</b><div class="mp-note" style="margin:0">${r[1]}</div></div>
      <span class="mp-act mp-manage">${sm('Link', `data-tog="#ppLink${r[0]}"`)}${sm('New link', 'data-toast="New link made. The old link stopped working."')}${sm('Rename', 'data-toast="Renamed"')}${sm('Remove', 'class="d" data-toast="Room removed"')}</span>
      <div class="mp-link" id="ppLink${r[0]}"${r[2] ? '' : ' hidden'}>${qrBox(120, r[0].length + 3)}<a href="#parentpaging" onclick="return false">https://192.168.1.40:8443/paging/${r[0].toLowerCase()}/k9v2x7q4m1</a>${btn('Copy', 'sm', 'data-copy="Copy"')}</div></div>`;
  return head('Parent paging', pn('pp', 'busy', 'busy', sum('warn', '2 waiting', 'id="ppgSum"'), 'span') + pn('pp', 'quiet setup', 'busy', '', 'span'), 'Kids workers page a parent from their room\'s page. The child\'s Check-Ins security code goes to a ProPresenter message; the child\'s name never leaves the room\'s page.') +
    page(bar(sub('pp', [['busy', 'Pages up'], ['quiet', 'Quiet'], ['setup', 'Needs setup']], 'busy').replace('class="mp-sub"', 'style="margin:0"'), []) +
      pn('pp', 'setup', 'busy', `<div class="mp-banner" id="ppgNeeds" style="margin-bottom:12px">Switch on ProPresenter and Planning Center on the Modules page: parent paging needs both.</div><div class="mp-banner" id="ppgErr" style="margin-bottom:12px">ProPresenter refused the message: no message named “Parent Page”.</div>`) +
      `<div class="mp-stack">${card('Pages up now', '', `
        ${pn('pp', 'busy', 'busy', pages + note('ProPresenter message token: B4K2 M7X9 T3Q8', '', 'id="ppgScreen"') + `<div class="mp-ctl">${btn('Clear all', 'd mp-manage', 'id="ppgClearAll" data-toast="Every parent page taken down"')}</div>`)}
        ${pn('pp', 'quiet setup', 'busy', note('No parents are being paged.', '', 'id="ppgNone" style="margin-top:0"'))}`)}
      ${card('ProPresenter', '', `
        <div class="mp-row">${fld('ppgMode', 'When a kids worker pages a parent', sel('ppgMode', [['queue', 'Wait here until the operator presses Show'], ['fill', 'Put the code in the message; the operator shows it from ProPresenter'], ['auto', 'Show it on screen straight away']], 'queue', 'data-mp="ppm"'), '', 'l')}</div>
        <div class="mp-row">${fld('ppgMessage', 'ProPresenter message', inp('ppgMessage', 'maxlength="100" placeholder="Parent Page" value="Parent Page"'))}${fld('ppgToken', 'Its text token', inp('ppgToken', 'maxlength="60" placeholder="code" value="code"'))}${fld('ppgTimeout', 'Take a page down after (minutes, 0 for never)', '<input id="ppgTimeout" type="number" min="0" max="120" step="1" value="10">')}</div>
        <div class="mp-ctl" style="margin-top:0">${btn('Save', 'p', 'id="ppgSave" data-toast="Saved."')}</div>
        ${note('In ProPresenter, make a message (for example "Parent of {code}, please come to the kids area") with a text token, and type its name and the token\'s name here. When several parents are paged at once, the token holds every code. The Test button on the Modules page checks both.')}`)}
      ${card('Rooms', '', `
        <div class="mp-list" id="ppgRooms">${rooms.map(roomRow).join('')}</div>
        ${note('No rooms yet.', '', 'id="ppgNoRooms" hidden')}
        <div class="mp-manage" id="ppgAddRow" style="margin-top:14px"><div class="mp-row">${fld('ppgLoc', 'Check-Ins room', `<select id="ppgLoc"><option value="">Load rooms from Planning Center…</option><option>Sunday 9:00 service · Nursery</option><option>Sunday 9:00 service · Kindergarten</option></select>`)}${fld('ppgRoomName', 'Name on the page (optional)', inp('ppgRoomName', 'maxlength="80"'))}</div>
        <div class="mp-ctl" id="ppgAddCtl">${btn('Load rooms', '', 'id="ppgLoad" data-toast="Loaded 6 rooms"')}${btn('Add room', 'p', 'id="ppgAdd" data-req="#ppgRoomName" data-fail="Load the rooms and choose one first." data-toast="Room added"')}</div></div>
        ${note('Each room gets its own link. Anyone with the link can page parents for that room only, with no sign-in, so hand it to that room\'s workers (a QR code on the wall works). It opens only on Relay\'s HTTPS address on this network, never through remote access. "New link" stops the old one at once.')}`)}</div>`);
};

/* ======================================================================================== Remote access */
reg({ kind: 'page', id: 'remoteaccess', t: 'Remote access', g: 'Output', ic: 'mod_remote_access', module: 'remote_access', perm: 'remote_access.view' });
P.remoteaccess = () => {
  const linkBox = () => card('Remote viewer link', '', `
    <div class="mp-flex" style="margin-bottom:4px"><div class="mp-qr" id="raQr" style="width:120px;height:120px;padding:6px">${qr(25, 9)}</div>
      <div><a class="mp-m" id="raUrl" href="#remoteaccess" onclick="return false" style="text-decoration:underline">https://q7k2m9x4.cloud.relaysystems.dev</a>
      <div class="mp-ctl" style="margin-top:8px">${btn('Copy link', '', 'id="raCopy" data-copy="Copy link"')}</div></div></div>
    ${pn('ra', 'live', 'live', note('Connected. Viewers anywhere can open the link.', 'ok', 'id="raState"'))}${pn('ra', 'retry', 'live', note('Lost the connection to Cloudflare. Trying again…', 'err', 'id="raState"'))}${pn('ra', 'missing', 'live', note('cloudflared is missing from this install of Relay, so the tunnel can\'t run. Reinstall Relay.', 'err', 'id="raState"'))}
    ${note('This link opens the same viewer pages as the room\'s link: the captions, the translations and the presentation screens. The operator panel is never reachable through it. Anyone who has the link can watch, so share it the way you\'d share a livestream link.')}
    <div class="mp-ctl mp-manage">${btn('Release this address', 'd', 'id="raRelease" data-toast="Address released. Enrolling again needs a new code."')}</div>
    ${note('Switching Remote access off on the Modules page stops the link and keeps the address, so the same link works again when you switch it back on. Releasing gives the address up for good; enrolling again needs a new code and gives a different link.')}`);
  const enroll = `<div class="mp-manage" id="raEnrollBox">${card('Enroll this install', '', `
      <div class="mp-row">${fld('raCode', 'Enrollment code', inp('raCode', 'class="mp-m" placeholder="XXXX-XXXX-XXXX" spellcheck="false" maxlength="40" style="font-family:ui-monospace,monospace"'), '', 'm')}</div>
      <div class="mp-ctl" style="margin-top:0">${btn('Enroll', 'p', 'id="raEnroll" data-req="#raCode" data-fail="Enter the enrollment code you were given." data-toast="Enrolled. Connecting to Cloudflare…"')}</div>
      ${note('Enrolling gives this install its own address on ' + m('cloud.relaysystems.dev') + '. Relay connects out to Cloudflare, so nothing changes on your router or firewall. The audio never leaves this computer; only the viewer pages are served through the link.')}`)}</div>`;
  return head('Remote access', pn('ra', 'live', 'live', sum('ok', 'live', 'id="raSum"'), 'span') + pn('ra', 'retry missing', 'live', sum('err', 'not connected'), 'span'), 'An internet link to the captions and translations, for people who aren\'t on this network.') +
    page(bar(sub('ra', [['live', 'Connected'], ['retry', 'Reconnecting'], ['missing', 'cloudflared missing'], ['enroll', 'Not enrolled']], 'live').replace('class="mp-sub"', 'style="margin:0"'), []) +
      `<div class="mp-stack">${pn('ra', 'enroll', 'live', note('An admin enrolls this install to give it a remote link.', 'mp-onlyro', 'id="raAccessNote" style="margin:0"'))}
      ${errn('raErr')}
      ${pn('ra', 'live retry missing', 'live', linkBox())}
      ${pn('ra', 'enroll', 'live', enroll)}</div>`);
};

/* ======================================================================================== Signage */
reg({ kind: 'page', id: 'signage', t: 'Signage', g: 'Output', ic: 'mod_signage', module: 'signage', perm: 'signage.view' });
P.signage = () => {
  const PL = [['Sunday lobby', 4], ['Weekday loop', 3], ['Easter 2027', 0]];
  const plOpts = (none, cur) => sel('', [['', none], ...PL.map((p, i) => [i + 1, p[0]])], cur);
  const target = '<option>All screens</option><option>Group: Lobby</option><option>Screen: Lobby left</option><option>Screen: Lobby right</option><option>Screen: Cafe TV</option><option>Screen: Youth room</option>';
  const tsel = (id, extra) => `<select id="${id}" ${extra || ''}>${target}</select>`;
  const urls = (s) => `<a class="mp-m" href="#signage" onclick="return false" style="text-decoration:underline">http://192.168.1.40:8081/signage/${s}</a><br><a class="mp-m" href="#signage" onclick="return false" style="text-decoration:underline">http://relay.local:8081/signage/${s}</a>`;
  const scr = [
    ['Welcome loop', 26, 'Lobby left', 'Group: Lobby', '', 'Sunday lobby', 'Schedule: Sun 8:00 AM-12:30 PM', '', 'lobby-left', false],
    ['Welcome loop', 26, 'Lobby right', 'Group: Lobby', '', 'Sunday lobby', 'Schedule: Sun 8:00 AM-12:30 PM', '', 'lobby-right', false],
    ['Coffee menu', 34, 'Cafe TV', '', 'Sound on', 'Weekday loop', ['Taken over: Giving-QR.png until 8:35:00 AM', 'Alert banner showing'], '', 'cafe-tv', false],
    ['Offline', 0, 'Youth room', '', '', 'Nothing assigned', '', 'Last seen 3 h ago', 'youth-room', true]];
  const screens = tbl(['Now', 'Screen', 'Playing', 'Address', ''], scr.map(s => [
    thumb(s[8] === 'youth-room' ? 'Offline' : '', s[1], s[9]) + `<div class="mp-dim" style="font-size:12px">${s[7] || s[0]}</div>`,
    `<div>${s[2]}</div>${s[3] ? `<div class="mp-dim" style="font-size:12px">${s[3]}</div>` : ''}${s[4] ? `<div class="mp-dim" style="font-size:12px">${s[4]}</div>` : ''}`,
    (s[5] === 'Nothing assigned' ? '<span class="mp-dim">Nothing assigned</span>' : s[5]) + [].concat(s[6] || []).map(l => `<div class="mp-dim" style="font-size:12px">${l}</div>`).join(''),
    urls(s[8]), act(sm('Preview', 'data-open="#sgPreview"'), sm('Edit', 'class="mp-manage" data-open="#sgScreenForm"'), sm('Delete', 'class="mp-manage d" data-toast="Deleted. Its address stops working."'))]), 'sgScreens');
  const screensTab = `<div class="mp-stack">${card('Screens', '', `
      ${pn('sgd', 'pop', 'pop', screens)}
      ${pn('sgd', 'none', 'pop', note('No screens yet. Add one, then open its address in the TV\'s web browser.', '', 'id="sgNoScreens" style="margin-top:0"'))}
      <div class="mp-ctl sg-manage mp-manage">${btn('Add a screen', 'p', 'id="sgScreenAdd" data-open="#sgScreenForm"')}</div>
      <form id="sgScreenForm" hidden class="mp-edit" onsubmit="return false">
        <div class="mp-row">${fld('sgScreenName', 'Name', inp('sgScreenName', 'maxlength="80" placeholder="Lobby left"'))}${fld('sgScreenSlug', 'Address name', inp('sgScreenSlug', 'maxlength="48" placeholder="lobby-left" style="font-family:ui-monospace,monospace"'))}</div>
        <div class="mp-row">${fld('sgScreenGroup', 'Group', `<select id="sgScreenGroup"><option>No group</option><option>Lobby</option><option>Cafe</option></select>`)}${fld('sgScreenPlaylist', 'Default playlist', plOpts('None', ''))}</div>
        ${chk('Play video sound on this screen')}
        ${note('A group\'s playlist takes the place of the screen\'s default while the group has one.')}
        <div class="mp-ctl">${btn('Save screen', 'p', 'id="sgScreenSave" data-close="#sgScreenForm" data-toast="Saved"')}${btn('Cancel', '', 'id="sgScreenCancel" data-close="#sgScreenForm"')}</div></form>`)}
    ${card('Screen groups', '', `
      ${pn('sgd', 'pop', 'pop', tbl(['Group', 'Playlist for the whole group', 'Screens', ''], [['Lobby', sel('', [['', 'None (each screen plays its own)'], [1, 'Sunday lobby'], [2, 'Weekday loop']], 1), 'Lobby left, Lobby right', act(sm('Delete', 'class="mp-manage d" data-toast="Group deleted"'))], ['Cafe', sel('', [['', 'None (each screen plays its own)'], [1, 'Sunday lobby'], [2, 'Weekday loop']], ''), 'Cafe TV', act(sm('Delete', 'class="mp-manage d" data-toast="Group deleted"'))], ['Youth', sel('', [['', 'None (each screen plays its own)'], [1, 'Sunday lobby']], ''), '<span class="mp-dim">None yet</span>', act(sm('Delete', 'class="mp-manage d" data-toast="Group deleted"'))]], 'sgGroups'))}
      ${pn('sgd', 'none', 'pop', note('No groups. A group, like "Lobby", plays one playlist on all its screens.', '', 'id="sgNoGroups" style="margin-top:0"'))}
      <div class="mp-row mp-manage" style="margin-top:6px">${fld('sgGroupName', 'New group', inp('sgGroupName', 'maxlength="80" placeholder="Lobby"'), '', 'm')}${btn('Add group', '', 'id="sgGroupAdd" data-req="#sgGroupName" data-fail="Give the group a name." data-toast="Group added"')}</div>`)}</div>`;

  const item = (nm, src, secs, from, until, tr, fit, flag, off) => `<div class="mp-item${off ? ' off' : ''}">${thumb(off ? 'Off' : '', 26 + nm.length % 30, off)}
      <div class="nm"><div>${nm}</div><div class="mp-dim" style="font-size:12px">${off ? 'Not available: ' : ''}${src}${flag ? ' · ' + flag : ''}</div></div>
      ${fld('', 'Seconds', `<input type="number" min="0" max="86400" step="1" value="${secs}">`)}${fld('', 'From', `<input type="date" value="${from}">`)}${fld('', 'Until', `<input type="date" value="${until}">`)}
      ${fld('', 'Transition', sel('', [['fade', 'Fade'], ['cut', 'Cut']], tr))}${fld('', 'Fit', sel('', [['fit', 'Fit (letterbox)'], ['fill', 'Fill (crop)']], fit))}
      <div class="mp-act"><button type="button" class="btn sm" data-dirty>Up</button><button type="button" class="btn sm" data-dirty>Down</button><button type="button" class="btn sm d" data-dirty>Remove</button></div></div>`;
  const editor = (i) => {
    const items = [
      ['Welcome-loop.mp4', 'Uploaded image or video', 0, '', '', 'fade', 'fit', ''], ['Easter-banner.jpg', 'Uploaded image or video', 12, '2026-11-01', '2026-11-22', 'fade', 'fill', ''],
      ['Nursery check-in reminder', 'ProPresenter announcement', 10, '', '', 'cut', 'fit', ''], ['Fall festival', 'Web page', 20, '2026-10-01', '2026-11-01', 'fade', 'fit', 'Ended']];
    const wk = [['Coffee-menu.png', 'Uploaded image or video', 15, '', '', 'fade', 'fit', ''], ['Giving-QR.png', 'Uploaded image or video', 10, '', '', 'fade', 'fit', ''], ['Weather - Denver', 'Web page', 20, '', '', 'cut', 'fit', ''] ];
    const list = [items, wk, [], ][i];
    const p = PL[i];
    return `<div class="mp-stack">${card('', '', `
      <h3 id="sgEditorTitle" style="margin:0 0 10px">${p[0]}</h3>
      <div class="mp-row mp-manage">${fld('sgRename', 'Name', inp('sgRename', `maxlength="80" value="${p[0]}"`), '', 'm')}<div class="mp-act">${btn('Rename', '', 'id="sgRenameSave" data-toast="Renamed"')}${btn('Duplicate', '', 'id="sgDuplicate" data-toast="Duplicated"')}${btn('Preview', '', 'id="sgPreviewList" data-open="#sgPreview"')}${btn('Delete', 'd', 'id="sgPlaylistDelete" data-toast="Playlist deleted"')}</div></div>
      <div class="mp-items" id="sgItems">${list.map((x, k) => item(x[0], x[1], x[2], x[3], x[4], x[5], x[6], x[7], false)).join('')}</div>
      ${list.length ? '' : note('This playlist is empty. Add images or videos from the library below.', '', 'id="sgNoItems" style="margin-top:0"')}
      ${note('Images show for their duration. Videos play to the end; a duration above 0 stops them sooner. Dates turn an item on and off by themselves (both are included).')}
      <div class="mp-ctl mp-manage">${btn('Save playlist', 'p', 'id="sgItemsSave" disabled data-off data-toast="Saved"')}<span class="mp-msg" id="sgItemsState"></span></div>`)}
    <div class="mp-manage">${card('Add to this playlist', '', `
      <div class="mp-row" id="sgSourcePick"><div class="fld s"><label for="sgSource">From</label>${sel('sgSource', [['media', 'Uploaded images and videos'], ['propresenter_slide', 'ProPresenter announcement'], ['web', 'Web page'], ['youtube', 'YouTube video or playlist'], ['plan', 'Planning Center plan item']], 'media')}</div></div>
      <div class="mp-mgrid" id="sgPicker">${MEDIA.slice(0, 5).map(x => `<div class="mp-mc mp-pick" title="Add ${x[0]}" data-dirty-add>${thumb(x[1], x[3])}<div class="nm">${x[0]}</div><div class="mp-dim" style="font-size:12px">${x[1]}</div></div>`).join('')}</div>`)}</div></div>`;
  };
  const playlistsTab = `<div class="mp-stack">${card('Playlists', '', `
      <div class="mp-sub" id="sgPlaylists">${tabs('pls', PL.map((p, i) => ['p' + i, `${p[0]} (${p[1]})`]), 'p0')}</div>
      <div class="mp-row mp-manage" style="margin-bottom:0">${fld('sgPlaylistName', 'New playlist', inp('sgPlaylistName', 'maxlength="80" placeholder="Sunday lobby"'), '', 'm')}${btn('Add playlist', '', 'id="sgPlaylistAdd" data-req="#sgPlaylistName" data-fail="Give the playlist a name." data-toast="Playlist added"')}</div>`)}
    <div id="sgEditor">${PL.map((p, i) => pn('pls', 'p' + i, 'p0', editor(i))).join('')}</div></div>`;

  const slotForm = `<form id="sgSlotForm" hidden class="mp-edit" onsubmit="return false">
      <div class="mp-row">${fld('sgSlotTarget', 'Applies to', tsel('sgSlotTarget'))}${fld('sgSlotPlaylist', 'Plays', plOpts('Choose a playlist', ''))}</div>
      <div class="mp-row">${fld('sgSlotKind', 'Repeats', sel('sgSlotKind', [['weekly', 'Every week'], ['range', 'Only on certain dates']], 'weekly', 'data-mp="slk"'), '', 's')}
        ${pn('slk', 'range', 'weekly', `<label for="sgSlotFrom">First day</label><input id="sgSlotFrom" type="date" value="2026-12-20">`, 'div', 'fld s')}${pn('slk', 'range', 'weekly', `<label for="sgSlotTo">Last day</label><input id="sgSlotTo" type="date" value="2027-01-03">`, 'div', 'fld s')}</div>
      <fieldset id="sgSlotDays" style="border:0;padding:0;margin:0 0 8px"><legend class="mp-dim" style="font-size:12px;padding:0 0 6px">Days (none ticked on a date range means every day)</legend>${['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((d, i) => chk(d, i === 0)).join('')}</fieldset>
      ${chk('All day', true, 'id="sgSlotAllDay" data-mp="slt"')}
      ${pn('slt', 'off', 'on', `<div class="mp-row" id="sgSlotTimes" style="margin-top:8px">${fld('sgSlotStart', 'From', '<input id="sgSlotStart" type="time" value="09:00">', '', 's')}${fld('sgSlotEnd', 'Until', '<input id="sgSlotEnd" type="time" value="11:30">', '', 's')}</div>`)}
      ${note('An end time earlier than the start time runs into the next day.')}
      <div class="mp-ctl">${btn('Save slot', 'p', 'data-close="#sgSlotForm" data-toast="Saved"')}${btn('Cancel', '', 'id="sgSlotCancel" data-close="#sgSlotForm"')}</div></form>`;
  const slots = [['Group: Lobby', 'Sun 8:00 AM-12:30 PM', 'Sunday lobby'], ['All screens', 'Mon, Tue, Wed, Thu, Fri 7:00 AM-5:00 PM', 'Weekday loop'], ['Screen: Cafe TV', 'Dec 20 to Jan 3, all day', 'Weekday loop'], ['Group: Lobby', 'Apr 4 to Apr 4, 6:00 AM-1:00 PM', 'Easter 2027']];
  const scheduleTab = `<div class="mp-stack">${card('Schedule', 'Schedules are optional. A screen with no matching slot plays its default playlist. A screen\'s own slots come first, then its group\'s, then the ones for all screens. A date range beats a weekly slot, and when two slots overlap the one that starts later wins. Times are in <span id="sgZoneName">Mountain Time (Denver)</span>.', `
      ${pn('sgd', 'pop', 'pop', tbl(['Applies to', 'When', 'Plays', ''], slots.map(s => [s[0], s[1], s[2], act(sm('Edit', 'class="mp-manage" data-open="#sgSlotForm"'), sm('Delete', 'class="mp-manage d" data-toast="Slot deleted"'))]), 'sgSlots'))}
      ${pn('sgd', 'none', 'pop', note('No time slots yet.', '', 'id="sgNoSlots" style="margin-top:0"'))}
      <div class="mp-ctl mp-manage">${btn('Add a time slot', 'p', 'id="sgSlotAdd" data-open="#sgSlotForm"')}</div>${slotForm}`)}
    ${card('Put a playlist on by hand', '', `
      <div class="mp-row mp-manage">${fld('sgOvTarget', 'On', tsel('sgOvTarget'))}${fld('sgOvPlaylist', 'Playlist', plOpts('Choose a playlist', ''))}${btn('Play now', '', 'id="sgOvSet" data-toast="Playing now"')}</div>
      ${pn('sgd', 'pop', 'pop', `<div id="sgOverrides" class="mp-list"><div><span class="grow">Lobby right plays Easter 2027 until 11/22/2026, 12:30:00 PM</span><span class="mp-manage">${sm('Clear', 'data-toast="Override cleared"')}</span></div><div><span class="grow">Cafe TV plays Weekday loop until cleared</span><span class="mp-manage">${sm('Clear', 'data-toast="Override cleared"')}</span></div></div>`)}
      ${note('A playlist chosen here beats the schedule and the default. How long it lasts is set on the Settings tab.')}`)}</div>`;

  const takeoverTab = `<div class="mp-stack">${card('Taken over now', '', `
      ${pn('sgd', 'pop', 'pop', `<div id="sgTaken" class="mp-list"><div><span class="grow">Group: Lobby: Live captions (translation) until cleared</span><span class="mp-manage">${sm('Return to playlist', 'data-toast="Returned to playlist"')}</span></div><div><span class="grow">Screen: Cafe TV: Giving-QR.png until 8:35:00 AM</span><span class="mp-manage">${sm('Return to playlist', 'data-toast="Returned to playlist"')}</span></div><div><span class="grow">Screen: Youth room: Welcome-loop.mp4 (once) (no screen shows it now)</span><span class="mp-manage">${sm('Return to playlist', 'data-toast="Returned to playlist"')}</span></div></div>`)}
      ${pn('sgd', 'none', 'pop', note('No screen is taken over. Every screen is playing its playlist.', '', 'id="sgNoTaken" style="margin-top:0"'))}
      <div class="mp-ctl mp-manage">${btn('Return all to playlist', 'p', 'id="sgReturnAll" data-toast="Every screen returned to its playlist"')}</div>`)}
    <div class="mp-manage">${card('Push a file now', '', `
      <div class="mp-row">${fld('sgPushTarget', 'To', tsel('sgPushTarget'))}${fld('sgPushMedia', 'File', sel('sgPushMedia', [['', 'Choose a file'], ...MEDIA.map((x, i) => [i, x[0]])], ''))}${fld('sgPushMode', 'Plays', sel('sgPushMode', [['once', 'Once'], ['seconds', 'For a set time'], ['cleared', 'Until cleared']], 'once', 'data-mp="pm"'), '', 's')}${pn('pm', 'seconds', 'once', `<label for="sgPushSecs">Seconds</label><input id="sgPushSecs" type="number" min="1" max="86400" value="60">`, 'div', 'fld n')}${btn('Push', 'p', 'id="sgPushGo" data-toast="Pushed"')}</div>
      ${note('Once: a video plays to its end and a picture shows for 10 seconds. The screen then goes back to its playlist where it was.')}`)}</div>
    <div class="mp-manage">${card('Live captions or service view', '', `
      <div class="mp-row">${fld('sgLiveTarget', 'On', tsel('sgLiveTarget'))}${fld('sgLiveView', 'Show', sel('sgLiveView', [['captions', 'Live captions (translation)'], ['transcription', 'Live captions (original language)'], ['service', 'Live service rundown']], 'captions'))}${btn('Switch', 'p', 'id="sgLiveGo" data-toast="Switched"')}</div>
      ${note('The screen shows Relay\'s own page until you return it to its playlist. The service rundown needs Planning Center; without it the screen shows the captions.')}`)}</div>
    ${card('Alerts', '', pn('sgd', 'pop', 'pop', note('An alert is showing on 2 screens.', 'warn', 'id="sgAlertNow" style="margin:0"'), 'div') + pn('sgd', 'none', 'pop', note('No alert is showing on a screen.', '', 'id="sgAlertNow" style="margin:0"')))}</div>`;

  const mediaTab = `<div class="mp-stack">${card('Media library', '', `
      <div class="mp-pills"><span class="pill" id="sgStorage">1.2 GB used · 148 GB free on this disk</span>${pn('md', 'low', 'ok', '<span class="pill" id="sgLowDisk" style="color:var(--err);border-color:var(--err)">Less than 2 GB free on this disk.</span>', 'span')}</div>
            <div class="mp-manage" style="margin:0 0 14px"><div class="mp-drop" id="sgDrop">Drop images or videos here, or <a>choose files</a>.
        <div class="mp-note">JPEG, PNG, WebP or GIF up to 25 MB; MP4 or WebM up to 500 MB.</div></div>
        <div id="sgUploads" class="mp-note"><div>Easter-banner.jpg: 64%</div><div class="err" style="color:var(--err)">Sermon-bumper.mov: too large (612 MB, the limit is 500 MB)</div></div></div>
      ${pn('sgd', 'pop', 'pop', `<div class="mp-mgrid" id="sgMedia">${MEDIA.map(x => `<div class="mp-mc">${thumb(x[1], x[3])}<div class="nm">${x[0]}</div><div class="mp-dim" style="font-size:12px">${x[2]}</div><div class="mp-dim" style="font-size:12px">${x[4]}</div><div class="mp-act mp-manage">${sm('Rename', 'data-toast="Renamed"')}${sm('Delete', 'class="d" data-toast="Deleted from the library"')}</div></div>`).join('')}</div>`)}
      ${pn('sgd', 'none', 'pop', note('Nothing uploaded yet.', '', 'id="sgNoMedia" style="margin-top:0"'))}
      ${note('Files are kept in Relay\'s state folder and copied into its backups folder.')}`)}</div>`;

  const settingsTab = `<div class="mp-stack">${card('Schedule', '', `
      <div class="mp-row">${fld('sgZone', 'Time zone', sel('sgZone', ['Mountain Time (Denver)', 'Central Time (Chicago)', 'Eastern Time (New York)', 'Pacific Time (Los Angeles)'], 'Mountain Time (Denver)'))}${fld('sgOvUntil', 'A playlist put on by hand lasts', sel('sgOvUntil', [['next_change', 'Until the next scheduled change'], ['cleared', 'Until it is cleared']], 'next_change'))}${btn('Save', 'p mp-manage', 'id="sgSchedSave" data-toast="Saved"')}</div>
      ${note('Slots and dates follow this zone, and keep the same clock time through daylight saving changes.')}`)}
    ${card('Alerts on screens', '', `
      <div class="mp-row">${fld('sgAlertMode', 'Show Relay\'s alerts on', sel('sgAlertMode', [['all', 'All screens'], ['groups', 'Chosen groups'], ['none', 'No screens']], 'groups', 'data-mp="am"'), '', 'm')}${btn('Save', 'p mp-manage', 'id="sgAlertSave" data-toast="Saved"')}</div>
      ${pn('am', 'groups', 'groups', `<fieldset id="sgAlertGroups" style="border:0;padding:0;margin:8px 0"><legend class="mp-dim" style="font-size:12px;padding:0 0 6px">Groups</legend>${chk('Lobby', true)}${chk('Cafe', false)}${chk('Youth', false)}</fieldset>`)}
      ${note('An alert shows as a red banner over whatever the screen is playing and goes away when the alert ends. The playlist is not interrupted.')}`)}
    ${card('Screen port', '', `
      <div class="mp-row">${fld('sgPort', 'Port', '<input id="sgPort" type="number" min="1024" max="65535" value="8081">', '', 'n')}${btn('Save', 'p mp-manage', 'id="sgPortSave" data-toast="Saved"')}</div>
      ${note('Screens are served over plain HTTP on this port, so a TV needs no login and no certificate. Only the signage pages answer on it. A change takes effect when Relay restarts.')}
      ${note('Serving screens on port 8081 now.', '', 'id="sgPortState"')}`)}</div>`;

  const tabsList = [['screens', 'Screens'], ['playlists', 'Playlists'], ['schedule', 'Schedule'], ['takeover', 'Takeovers'], ['media', 'Media'], ['settings', 'Settings']];
  return head('Signage', sum('ok', '4 screens', 'id="sgSum"'), 'Images and videos on any TV with a web browser. Each screen has its own address.') +
    page(pn('rs', 'yes', 'no', '<div class="mp-banner warn" id="sgRestart" style="margin-bottom:12px">Screens are on port 8081. Restart Relay to move them to port 8090.</div>') +
      note('An admin sets up screens, playlists and media.', 'mp-onlyro', 'id="sgAccess" style="margin:0 0 12px"') +
      bar(tabs('sgt', tabsList, 'screens'), [tgl('sgd', 'Data', [['pop', 'Populated'], ['none', 'Empty']], 'pop'), tgl('md', 'Disk', [['ok', 'Free space'], ['low', 'Low disk']], 'ok'), tgl('rs', 'Port', [['no', 'In sync'], ['yes', 'Restart pending']], 'no')]) +
      pn('sgt', 'screens', 'screens', screensTab) + pn('sgt', 'playlists', 'screens', playlistsTab) + pn('sgt', 'schedule', 'screens', scheduleTab) +
      pn('sgt', 'takeover', 'screens', takeoverTab) + pn('sgt', 'media', 'screens', mediaTab) + pn('sgt', 'settings', 'screens', settingsTab) +
      errn('sgErr') +
      `<div class="mp-modal" id="sgPreview" hidden data-close="#sgPreview"><div class="card"><div class="mp-stage" id="sgStage">Welcome to Grace Fellowship<br><span style="font-size:14px;font-weight:400;color:var(--muted)">Sunday services 9:00 and 11:00</span></div>
        <div class="mp-ctl"><span class="mp-msg" id="sgPreviewTitle">Sunday lobby</span><span style="flex:1"></span>${btn('Close', '', 'id="sgPreviewClose" data-close="#sgPreview"')}</div></div></div>`);
};

/* media sample shared by the library, the picker and the push list: name, kind label, size line, thumb tint, uses */
const MEDIA = [
  ['Welcome-loop.mp4', 'Video', 'Video · 48 MB · 1920×1080 · 62s', 30, 'In 2 playlist items'],
  ['Easter-banner.jpg', 'Image', 'Image · 1.2 MB · 1920×1080', 42, 'In 1 playlist item'],
  ['Coffee-menu.png', 'Image', 'Image · 640 KB · 1920×1080', 24, 'In 1 playlist item'],
  ['Giving-QR.png', 'Image', 'Image · 210 KB · 1080×1080', 18, 'In 1 playlist item'],
  ['Youth-night-promo.mp4', 'Video', 'Video · 31 MB · 1280×720 · 24s', 36, 'Not in a playlist'],
  ['Nursery-reminder.png', 'Image', 'Image · 380 KB · 1920×1080', 28, 'Not in a playlist']];

/* ======================================================================================== Web & YouTube */
reg({ kind: 'page', id: 'signageweb', t: 'Web & YouTube', g: 'Output', ic: 'mod_signage_web', module: 'signage_web', perm: 'signage_web.view' });
P.signageweb = () => {
  const links = [
    ['Sunday welcome loop', 'YouTube', 'https://www.youtube.com/watch?v=Zq3tFk8sLmE', 'ok', 'YouTube allows this to be embedded.'],
    ['Weather - Denver', 'Web page', 'https://forecast.weather.gov/MapClick.php?lat=39.74&lon=-104.99', 'ok', 'The site allows being shown in a frame.'],
    ['Easter 2027 countdown', 'YouTube', 'https://www.youtube.com/playlist?list=PLx0sYbCqOb8QwE2', 'ok', 'YouTube allows this to be embedded.'],
    ['Online giving', 'Web page', 'https://give.gracefellowship.org', 'err', 'This site refuses to be shown inside another page (frame-ancestors). Screens will stay blank.'],
    ['Old announcements video', 'YouTube', 'https://www.youtube.com/watch?v=Hh81nLp0aaA', 'err', 'YouTube has no such video or playlist, or it is private.'],
    ['Youth calendar (office network)', 'Web page', 'http://192.168.1.60/calendar', '', 'This address is on a private network, so Relay doesn\'t check it. Add it to a playlist and look at a screen.'],
    ['Missions map', 'Web page', 'https://gracefellowship.org/missions', '', 'Not checked yet.']];
  const row = (l) => `<div><div class="grow" style="min-width:0"><b>${l[0]}</b><div class="mp-note" style="margin:1px 0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${l[1]} · ${l[2]}</div><div class="mp-note ${l[3]}" style="margin:0">${l[4]}</div></div>
      <span class="mp-act mp-manage">${sm('Check again', 'data-toast="Checking…"')}${sm('Rename', 'data-toast="Renamed"')}${sm('Remove', 'class="d" data-toast="Removed. Playlists that use it will skip it."')}</span></div>`;
  return head('Web &amp; YouTube', pn('sw', 'full', 'full', sum('', '7 saved', 'id="swSum"'), 'span'), 'Save a web page or a YouTube video or playlist here, then add it to a signage playlist like any other item.') +
    page(note('These items need the internet. A screen skips one while it can\'t reach the site and shows it again when it can. A YouTube video plays muted unless the screen has Sound on.', '', 'style="margin:0 0 14px"') +
      bar(sub('sw', [['full', 'Saved links'], ['empty', 'Nothing saved']], 'full').replace('class="mp-sub"', 'style="margin:0"'), []) +
      `<div class="mp-stack">${card('Add a link', '', `
        <div class="mp-row">${fld('swUrl', 'Address', inp('swUrl', 'placeholder="https://… or a YouTube link" spellcheck="false" maxlength="500"'))}${fld('swName', 'Name (optional)', inp('swName', 'placeholder="Shown in playlists" maxlength="100"'))}</div>
        <div class="mp-ctl" style="margin-top:0">${btn('Add', 'p', 'id="swAdd" data-req="#swUrl" data-fail="Enter a web address or a YouTube link." data-toast="Checking… Added."')}</div>
        ${note('Relay checks the address when you add it: a site that refuses to be shown inside another page stays blank on screens, and a YouTube video whose owner turned embedding off can\'t play.')}
        ${errn('swErr')}`)}
      ${card('Saved links', '', pn('sw', 'full', 'full', `<div class="mp-list" id="swList">${links.map(row).join('')}</div>`) + pn('sw', 'empty', 'full', note('Nothing saved yet.', '', 'id="swEmpty" style="margin-top:0"')) +
        note('Set a playlist item\'s seconds to choose how long a web page or a playlist shows. A single YouTube video added with 0 seconds plays to its end; a number cuts it short. Removing a link here makes every playlist that uses it skip it.'))}</div>`);
};

/* ======================================================================================== Alert events */
reg({ kind: 'page', id: 'alertevents', t: 'Alert events', g: 'Automation', ic: 'mod_alert_events', module: 'alert_events', perm: 'alert_events.view' });
P.alertevents = () => `${head('Alert events', sum('ok', '1 output on', 'id="aeSum"'), 'Tell other products when an alert or stage message is sent, and when an alert is acknowledged (with the reply, if one was picked).')}
  ${page(bar('', []) + `<div class="mp-stack">
    ${card('Webhook', '', `${swrow('aeHookOn', true, 'Send a webhook')}
      ${mf('aeHookUrl', 'Address', inp('aeHookUrl', 'placeholder="https://…" spellcheck="false" maxlength="500" value="https://hooks.gracefellowship.org/relay/alerts"'), 'Relay POSTs a JSON body: ' + m('event') + ' (' + m('alert.sent') + ', ' + m('alert.acknowledged') + ', ' + m('alert.ended') + ' or ' + m('message.sent') + '), ' + m('button') + ', ' + m('from') + ', ' + m('level') + ', ' + m('targets') + ', and on an acknowledge ' + m('seen_on') + ', ' + m('seen_after_s') + ' and ' + m('reply') + '.')}`)}
    ${card('OSC', '', `${swrow('aeOscOn', false, 'Send OSC messages')}
      <div class="mp-row">${fld('aeOscHost', 'Address', inp('aeOscHost', 'placeholder="192.168.1.20" spellcheck="false" maxlength="253" value="192.168.1.20"'), '', 'm')}${fld('aeOscPort', 'Port', '<input id="aeOscPort" type="number" min="1" max="65535" value="8000" style="width:7em">', '', 's')}</div>
      ${note('UDP. ' + m('/relay/alert/sent') + ' button, from, level &middot; ' + m('/relay/alert/acknowledged') + ' button, seen on, reply &middot; ' + m('/relay/alert/ended') + ' button, state (missed or cancelled) &middot; ' + m('/relay/message/sent') + ' text, from, level.', '', 'style="margin-top:0"')}`)}
    ${errn('aeErr')}
    <div class="mp-ctl" style="margin:0">${btn('Save', 'p', 'id="aeSave" data-toast="Saved"')}${btn('Send a test event', '', 'id="aeTest" data-testev')}<span class="mp-msg" id="aeMsg"></span></div>
    ${card('Bitfocus Companion', '', `<p class="mp-note" style="margin:0">Companion reads alert state from ${m('GET /api/admin/alert-events/state')} with an API token. It returns ${m('variables')} (${m('alerts_on')}, ${m('last_event')}, ${m('last_reply')}, and ${m('&lt;button&gt;_state')}, ${m('_from')}, ${m('_seen_on')}, ${m('_reply')} for each alert button) and ${m('feedbacks')} (the buttons that are flashing, seen, not seen or replied to), for a Companion button to light on.</p>`)}</div>`)}`;
AFTER.alertevents = () => {
  const count = () => { const n = ['aeHookOn', 'aeOscOn'].filter(i => $(i) && $(i).classList.contains('on')).length; const s = document.getElementById('aeSum'); if (s) { s.textContent = n ? n + (n === 1 ? ' output on' : ' outputs on') : ''; s.style.display = n ? '' : 'none'; } };
  ['aeHookOn', 'aeOscOn'].forEach(i => $(i) && $(i).addEventListener('click', () => setTimeout(count, 0)));
};

/* ======================================================================================== behaviour (one delegated set) */
const show = (g, v) => q(`[data-g="${g}"]`).forEach(n => { n.hidden = !n.dataset.v.split(' ').includes(v); });
function setRO(on) {
  const root = document.querySelector('#pages .mp-page'); if (!root) return;
  root.classList.toggle('mp-ro', on);
  root.querySelectorAll('.mp-vo').forEach(n => { n.hidden = !on; });
  root.querySelectorAll('input,select,textarea,.btn,.sw').forEach(n => { if (n.closest('[data-keep]') || n.hasAttribute('data-off')) return; n.disabled = on; });
}
document.addEventListener('click', (e) => {
  const t = e.target, root = t.closest && t.closest('.mp-page'); if (!root) return;
  const done = (fn) => { e.stopPropagation(); fn && fn(); };
  let x;
  if ((x = t.closest('.tabs[data-mp] button'))) return done(() => { [...x.parentElement.children].forEach(b => b.classList.toggle('on', b === x)); show(x.parentElement.dataset.mp, x.dataset.v); });
  if ((x = t.closest('.tg[data-mp] button'))) return done(() => { [...x.parentElement.children].forEach(b => b.setAttribute('aria-pressed', String(b === x))); show(x.parentElement.dataset.mp, x.dataset.v); });
  if ((x = t.closest('.tg[data-ro] button'))) return done(() => { [...x.parentElement.children].forEach(b => b.setAttribute('aria-pressed', String(b === x))); setRO(x.dataset.v === '1'); });
  if ((x = t.closest('[data-open]'))) return done(() => { q(x.dataset.open).forEach(n => { n.hidden = false; }); if (x.dataset.toast) toast(x.dataset.toast); });
  if ((x = t.closest('[data-close]'))) { if (x.classList.contains('mp-modal') && t !== x) return; return done(() => { q(x.dataset.close).forEach(n => { n.hidden = true; }); if (x.dataset.toast) toast(x.dataset.toast); }); }
  if ((x = t.closest('[data-tog]'))) return done(() => q(x.dataset.tog).forEach(n => { n.hidden = !n.hidden; }));
  if ((x = t.closest('[data-copy]'))) return done(() => { const o = x.dataset.copy; x.textContent = 'Copied'; setTimeout(() => { x.textContent = o; }, 1500); });
  if ((x = t.closest('[data-muteall]'))) return done(() => { const on = x.textContent.trim() === 'Mute all'; x.textContent = on ? 'Unmute all' : 'Mute all'; x.classList.toggle('d', !on); q('#icMutedBanner').forEach(n => { n.hidden = !on; }); toast(on ? 'Everything muted' : 'Unmuted'); });
  if ((x = t.closest('[data-allow]'))) return done(() => { const tr = x.closest('tr'), b = tr.querySelector('[data-allowbadge]'), was = x.textContent.trim() === 'Remove remote access'; x.textContent = was ? 'Allow remote' : 'Remove remote access'; b.textContent = was ? 'Not allowed' : 'Allowed'; b.className = 'badge' + (was ? '' : ' ok'); toast(was ? 'Remote access removed' : 'Remote access allowed'); });
  if ((x = t.closest('[data-testev]'))) return done(() => { const s = document.getElementById('aeMsg'); if (s) { s.textContent = 'A test event was sent.'; s.style.color = 'var(--ok)'; } });
  if ((x = t.closest('[data-dirty],[data-dirty-add]'))) return done(() => { const b = document.getElementById('sgItemsSave'), s = document.getElementById('sgItemsState'); if (b) { b.disabled = false; b.removeAttribute('data-off'); } if (s) s.textContent = 'Not saved yet'; });
  if ((x = t.closest('[data-req]'))) {
    const inpt = q(x.dataset.req)[0], card = x.closest('.card') || root, err = card.querySelector('.mp-note.err');
    if (inpt && !inpt.value.trim() && x.dataset.fail) return done(() => { if (err) { err.textContent = x.dataset.fail; err.hidden = false; } else toast(x.dataset.fail); });
    return done(() => { if (err) err.hidden = true; toast(x.dataset.toast || 'Saved'); });
  }
  if ((x = t.closest('[data-toast]'))) return done(() => toast(x.dataset.toast));
  if ((x = t.closest('.btn'))) { const s = x.textContent.trim(); return done(() => toast(/^Save/.test(s) ? 'Saved' : s)); }
});
document.addEventListener('change', (e) => {
  const t = e.target; if (!t.closest || !t.closest('.mp-page') || !t.dataset.mp) return;
  show(t.dataset.mp, t.type === 'checkbox' ? (t.checked ? 'on' : 'off') : t.value);
});
document.addEventListener('input', (e) => {
  const t = e.target; if (!t.dataset || !t.dataset.filt) return;
  const v = t.value.trim().toLowerCase(); let n = 0;
  q(t.dataset.filt + ' tr').forEach(r => { const ok = !v || (r.dataset.k || '').toLowerCase().includes(v); r.hidden = !ok; if (ok) n++; });
  const e0 = document.getElementById('icEventsEmpty'), tb = document.getElementById('icEventsTable');
  if (e0) e0.hidden = n > 0; if (tb) tb.hidden = n === 0;
});
})();
