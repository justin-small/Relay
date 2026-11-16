/* Module pages, audio/video and automation: ATEM, ProPresenter (control, slide view and lobby, settings), OBS Studio,
   YouTube, Tally Arbiter, Lighting, Planning Center. Every page follows "Module pages in the new shell" in
   docs/redesign/screens/modules-av.md: heading, status row, one card per section, settings as a form grid. */
(function(){

/* ---------- icons: the inner SVG of each AdminPage.icon, copied from the module's __init__.py ---------- */
Object.assign(I,{
  mod_atem:'<rect x="2" y="4" width="12" height="8" rx="1"/><path d="M5 7h2M9 7h2M5 9.5h6"/>',
  mod_ppcontrol:'<rect x="2" y="2.5" width="12" height="8" rx="1"/><path d="M6.5 5l3 1.5-3 1.5z" fill="currentColor"/><path d="M4 13.5h8"/>',
  mod_pplive:'<rect x="1.5" y="3" width="9" height="6.5" rx="1"/><rect x="8" y="7" width="6.5" height="5.5" rx="1" fill="var(--card)"/><rect x="8" y="7" width="6.5" height="5.5" rx="1"/>',
  mod_propresenter:'<rect x="2" y="2.5" width="12" height="8" rx="1"/><path d="M5 13.5h6M8 10.5v3"/>',
  mod_obs:'<circle cx="8" cy="8" r="5.5"/><circle cx="8" cy="8" r="1.5"/>',
  mod_youtube:'<rect x="1.5" y="3.5" width="13" height="9" rx="2.5"/><path d="M6.5 6v4l3.5-2z"/>',
  mod_tallyarbiter:'<circle cx="8" cy="8" r="5"/><circle cx="8" cy="8" r="1.6"/>',
  mod_lighting:'<circle cx="8" cy="6" r="3"/><path d="M6.5 10.5h3M7 13h2"/>',
  mod_planningcenter:'<path d="M3 13V6l5-4 5 4v7zM6.5 13V9h3v4"/>'
});

/* ---------- local CSS (prefix mv-) ---------- */
injectCSS('modules-av',`
.mv-status{display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:12px 16px;border:1px solid var(--border);border-radius:var(--r);background:var(--card);box-shadow:var(--shadow)}
.mv-status .mv-t{flex:1;min-width:220px;line-height:1.35}.mv-status .mv-t b{font-weight:600}.mv-status .mv-t small{display:block;color:var(--muted);font-size:12px}
.mv-status .mv-a{display:flex;gap:8px;flex-wrap:wrap;margin-left:auto}
.mv-note{border:1px solid var(--border);border-left:3px solid var(--muted);border-radius:calc(var(--r) - 2px);padding:8px 12px;font-size:13px;color:var(--muted);margin:0 0 12px;line-height:1.5}
.mv-stack>.mv-note{margin:0}
.mv-note.err{border-left-color:var(--err);color:var(--fg)}.mv-note.warn{border-left-color:var(--warn);color:var(--fg)}.mv-note.ok{border-left-color:var(--ok);color:var(--fg)}
.mv-note code,.mv-m,.mv-k{font:12px ui-monospace,SFMono-Regular,Menlo,monospace}
.mv-k{border:1px solid var(--border);border-radius:4px;padding:1px 5px;color:var(--fg);white-space:nowrap}
.mv-stack{display:flex;flex-direction:column;gap:14px}
.mv-demo{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:14px}.mv-demo .tabs{margin:0}.mv-demo small{color:var(--muted);font-size:12px}
.mv-card h3{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.mv-card h3 .mv-cnt{font-weight:400;font-size:12px;color:var(--muted)}
.mv-card .mv-hd{display:flex;gap:10px;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;margin-bottom:12px}.mv-card .mv-hd .d{margin:0;max-width:78ch}
[data-mvb] [hidden]{display:none!important}.mv-card .mv-hd>div{min-width:0}
.mv-form{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}.mv-form .fld{margin:0}
.fld .mv-h{font-size:12px;color:var(--muted);margin:4px 0 0}
.mv-foot{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:14px;padding-top:14px;border-top:1px solid var(--border)}.mv-foot .mv-sp{flex:1}.mv-foot small{color:var(--muted);font-size:12px}
.mv-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.mv-row select,.mv-row input:not([type=checkbox]){width:auto;min-width:0}
.mv-chk{display:flex;gap:12px;align-items:flex-start;padding:7px 0}.mv-chk .sw{margin-top:1px}.mv-chk b{display:block;font-weight:500}.mv-chk small{display:block;color:var(--muted);font-size:12px;margin-top:1px;line-height:1.45}
.mv-kv{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:7px 18px;margin:0}.mv-kv dt{color:var(--muted);font-size:13px}.mv-kv dd{margin:0;overflow-wrap:anywhere}
.mv-kv dd.err{color:var(--err)}.mv-kv dd.ok{color:var(--ok)}
.mv-list>*{padding:12px 0;border-top:1px solid color-mix(in srgb,var(--border) 70%,transparent)}.mv-list>*:first-child{border-top:0;padding-top:0}.mv-list>*:last-child{padding-bottom:0}
.mv-sub{font-size:13px;font-weight:600;margin:18px 0 6px}.mv-sub:first-child{margin-top:0}
.mv-d{margin:0}.mv-d>summary{cursor:pointer;display:flex;align-items:center;gap:8px;font-weight:500;padding:6px 0;list-style:none}.mv-d>summary::-webkit-details-marker{display:none}
.mv-d>summary::before{content:"";width:6px;height:6px;border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;transform:rotate(-45deg);transition:transform .15s;margin:0 4px 0 2px;color:var(--muted)}
.mv-d[open]>summary::before{transform:rotate(45deg)}.mv-d>.mv-in{padding:8px 0 4px 18px}
.mv-over{overflow-x:auto}.mv-over table{min-width:560px}
.mv-right{margin-left:auto}
.mv-muted{color:var(--muted)}.mv-ok{color:var(--ok)}.mv-err{color:var(--err)}.mv-warn{color:var(--warn)}
tr.mv-bad td{color:var(--err)}
.mv-me{display:flex;gap:8px 12px;align-items:center;flex-wrap:wrap;padding:10px 0;border-top:1px solid color-mix(in srgb,var(--border) 70%,transparent)}.mv-me:first-child{border-top:0}
.mv-me select{width:auto;min-width:150px}.mv-me strong{min-width:112px}
.mv-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.mv-cue{position:relative;text-align:left;padding:0;border:1px solid var(--border);border-top:4px solid var(--pp,var(--muted));border-radius:calc(var(--r) - 2px);background:var(--bg);overflow:hidden;cursor:pointer;color:var(--fg);display:flex;flex-direction:column;font:inherit;transition:background-color .15s}
.mv-cue:hover{background:var(--hover)}
.mv-cue .mv-img{aspect-ratio:16/9;display:grid;place-items:center;padding:6px;text-align:center;font-size:11px;line-height:1.25;background:color-mix(in srgb,var(--fg) 88%,var(--bg));color:var(--bg);overflow:hidden}
.mv-cue .mv-meta{padding:5px 8px 7px;font-size:11.5px;line-height:1.35}
.mv-cue .mv-gn{font-weight:600;font-size:11px;letter-spacing:.04em;text-transform:uppercase;color:var(--pp,var(--muted))}
.mv-cue.live{outline:2px solid var(--primary);outline-offset:-2px}.mv-cue.live .mv-gn::after{content:" \\00b7  LIVE";color:var(--primary)}
.mv-cue:disabled{opacity:.5;cursor:default}
.mv-now{border:1px solid var(--border);border-radius:calc(var(--r) - 2px);padding:10px 12px;background:var(--bg);margin-bottom:10px}.mv-now .t{white-space:pre-line}.mv-now .n{color:var(--muted);font-size:12.5px;margin-top:4px;white-space:pre-line}
.mv-now .mv-notes{color:var(--muted);font-size:12px;border-left:2px solid var(--border);padding-left:8px;margin-top:4px}
.mv-chip{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11.5px;font-weight:600;border:1px solid var(--pp,var(--muted));color:var(--pp,var(--muted));margin-right:6px}
.mv-item{display:flex;gap:10px;align-items:center;flex:1;text-align:left;min-width:0;justify-content:flex-start}
.mv-item .mv-k2{color:var(--muted);font-size:11px;min-width:2em}.mv-item .mv-tag{color:var(--muted);font-size:11px;margin-left:auto;white-space:nowrap}
.mv-item .mv-th{width:64px;aspect-ratio:16/9;border-radius:3px;background:color-mix(in srgb,var(--fg) 88%,var(--bg));flex:none}
.mv-item.live{border-color:var(--primary);color:var(--primary)}
.mv-irow{display:flex;gap:6px;align-items:center;margin-bottom:4px}.mv-irow.dim{opacity:.55}
.mv-mark{border-left:4px solid var(--pp,var(--muted));padding:4px 10px;margin:10px 0 4px;font-weight:600;font-size:12.5px;letter-spacing:.03em}
.mv-tm{font-variant-numeric:tabular-nums;font-size:17px;min-width:5.5em}.mv-tm.run{color:var(--primary)}
.mv-timer{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.mv-timer .nm{flex:1 1 140px;font-weight:600}
.mv-dock{position:sticky;bottom:0;margin-top:14px;z-index:4;background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:8px 12px;display:flex;gap:6px;flex-wrap:wrap;align-items:center;box-shadow:0 -8px 20px color-mix(in srgb,var(--bg) 85%,transparent)}
.mv-dock .dl{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-right:4px}
.mv-dock .btn.lit{background:color-mix(in srgb,var(--err) 18%,var(--card));border-color:var(--err);color:var(--err)}
.mv-big{padding:11px 22px;font-size:15px}
.mv-sv{display:grid;grid-template-columns:1fr 1fr;gap:12px}@media(max-width:700px){.mv-sv{grid-template-columns:1fr}}
.mv-sv .fr{position:relative;aspect-ratio:16/9;border-radius:calc(var(--r) - 2px);background:color-mix(in srgb,var(--fg) 90%,var(--bg));color:var(--bg);display:grid;place-items:center;text-align:center;padding:14px;font-size:15px;line-height:1.35;overflow:hidden}
.mv-sv .fr.nx{opacity:.7;font-size:13px}.mv-sv .lb{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px}
.mv-sv .tmr{position:absolute;top:8px;right:8px;background:color-mix(in srgb,var(--bg) 85%,transparent);color:var(--fg);padding:2px 9px;border-radius:99px;font:600 13px ui-monospace,monospace;display:flex;gap:6px;align-items:center}.mv-sv .tmr small{font:11px var(--font);color:var(--muted)}
.mv-sv .med{position:absolute;left:10px;right:10px;bottom:8px;display:flex;gap:8px;align-items:center;font:11px ui-monospace,monospace;color:var(--fg);background:color-mix(in srgb,var(--bg) 85%,transparent);border-radius:99px;padding:3px 10px}
.mv-sv .med .b{flex:1;height:4px;border-radius:2px;background:var(--border);overflow:hidden}.mv-sv .med .b i{display:block;height:100%;width:38%;background:var(--primary)}
.mv-pre{aspect-ratio:16/9;border-radius:calc(var(--r) - 2px);background:color-mix(in srgb,var(--fg) 92%,var(--bg));color:var(--bg);display:grid;place-items:center;font-size:14px;margin-top:10px;position:relative;overflow:hidden}
.mv-pre::after{content:"PGM";position:absolute;top:8px;left:8px;font:600 10px var(--font);letter-spacing:.08em;background:var(--err);color:var(--primary-fg);padding:2px 6px;border-radius:4px}
.mv-chart{display:block;width:100%;height:64px;margin-top:10px;color:var(--primary)}.mv-chart text{font-size:10px;fill:var(--muted)}
.mv-scenes{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}.mv-scenes .btn.pv{box-shadow:inset 0 0 0 2px var(--primary)}
.mv-aud{display:grid;grid-template-columns:150px minmax(0,1fr) auto;gap:8px 12px;align-items:center;margin-top:10px}
.mv-aud .nm{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mv-aud .mt{position:relative}.mv-aud .mt .meter{margin:0}.mv-aud .mt b{position:absolute;top:0;bottom:0;width:2px;background:var(--fg);opacity:.9}
.mv-aud .rd{display:flex;gap:10px;align-items:center;min-width:150px;justify-content:flex-end;font-variant-numeric:tabular-nums;color:var(--muted);font-size:13px}.mv-aud .rd.mu{color:var(--err);font-weight:700}
.mv-aud .meter i.hot{background:var(--warn)}
.mv-2{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px;align-items:start}@media(max-width:900px){.mv-2{grid-template-columns:1fr}}
.mv-box{border:1px solid var(--border);border-radius:calc(var(--r) - 2px);padding:12px 14px;background:var(--bg)}
.mv-box .mv-bh{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}.mv-box h4{margin:0;font-size:13px;font-weight:600}
.mv-code{font:600 28px/1.2 ui-monospace,Menlo,Consolas,monospace;letter-spacing:.12em;padding:10px 0}
.mv-lt{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;background:var(--bg);border:1px solid var(--border);padding:8px;border-radius:calc(var(--r) - 2px)}
.mv-lt .c{border:0;font:600 14px var(--font);padding:14px 8px;min-height:54px;cursor:pointer;color:var(--primary-fg);background:var(--primary);display:grid;place-items:center;text-align:center;transition:transform .1s}
.mv-lt .c:active{transform:scale(.97)}
.mv-lt .c[data-shape=rounded]{border-radius:10px}.mv-lt .c[data-shape=rect]{border-radius:0}.mv-lt .c[data-shape=pill]{border-radius:99px}.mv-lt .c[data-shape=circle]{border-radius:50%;aspect-ratio:1;min-height:0}
.mv-lt .c.on{box-shadow:0 0 0 2px var(--bg),0 0 0 4px var(--fg)}.mv-lt .c.ok{background:var(--ok)}.mv-lt .c.warn{background:var(--warn);color:var(--bg)}.mv-lt .c.err{background:var(--err)}.mv-lt .c.mu{background:var(--muted);color:var(--bg)}
.mv-lt.mv-free{display:block;position:relative;aspect-ratio:16/10;padding:0}.mv-lt.mv-free .c{position:absolute;min-height:0}
.mv-lt .c.pk{outline:2px dashed var(--fg);outline-offset:2px}
.mv-cuerow{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:6px 0;border-top:1px solid color-mix(in srgb,var(--border) 70%,transparent)}.mv-cuerow:first-child{border-top:0}
.mv-cuerow .mv-nm{flex:1;min-width:140px}.mv-cuerow input[type=range]{width:160px;padding:0}
.mv-plan tr.cur td{background:var(--hover)}.mv-plan tr.done td{color:var(--muted)}.mv-plan td.mv-m{white-space:nowrap}
.mv-plan td input{min-width:90px}
@media(max-width:640px){.mv-aud{grid-template-columns:1fr auto}.mv-aud .mt{grid-column:1/-1;order:3}.mv-kv{grid-template-columns:1fr}.mv-kv dt{margin-top:6px}}
`);

/* ---------- helpers ---------- */
const MVS={}, MVB={}, MVA={};
const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
/* button: label, toast message (no apostrophes), class */
const mvB=(label,msg,cls)=>`<button type="button" class="btn ${cls||''}" onclick="toast('${msg}')">${label}</button>`;
const mvHead=(t,sub)=>`<h2 class="t">${t}</h2><p class="sub">${sub}</p>`;
/* status row: lvl ok|warn|err|idle; the module's Health text; actions on the right */
const mvStatus=(lvl,label,text,acts)=>`<div class="mv-status" role="status"><span class="badge sbadge ${lvl=='idle'?'':lvl}"><i></i>${label}</span><div class="mv-t"><b>${text[0]}</b>${text[1]?`<small>${text[1]}</small>`:''}</div>${acts?`<div class="mv-a">${acts}</div>`:''}</div>`;
const mvNote=(cls,html)=>`<div class="mv-note ${cls||''}">${html}</div>`;
const mvCard=(title,desc,body,o)=>{o=o||{};return `<section class="card mv-card"${o.id?` id="${o.id}"`:''}><div class="mv-hd"><div><h3>${title}${o.cnt?`<span class="mv-cnt">${o.cnt}</span>`:''}</h3>${desc?`<p class="d">${desc}</p>`:''}</div>${o.right?`<div class="mv-row">${o.right}</div>`:''}</div>${body}</section>`};
const mvF=(label,input,help,cls)=>`<div class="fld ${cls||''}"><label>${label}</label>${input}${help?`<p class="mv-h">${help}</p>`:''}</div>`;
const mvIn=(v,ph,o)=>{o=o||{};return `<input${o.type?` type="${o.type}"`:''} value="${esc(v==null?'':v)}" placeholder="${esc(ph||'')}"${o.min!=null?` min="${o.min}"`:''}${o.max!=null?` max="${o.max}"`:''}${o.step?` step="${o.step}"`:''}${o.dis?' disabled':''}${o.w?` style="max-width:${o.w}px"`:''}${o.mono?' spellcheck="false" autocomplete="off"':''} aria-label="${esc(o.al||ph||'')}">`};
const mvSel=(opts,cur,o)=>{o=o||{};return `<select${o.dis?' disabled':''}${o.w?` style="max-width:${o.w}px"`:''}${o.al?` aria-label="${esc(o.al)}"`:''}>${opts.map(x=>{const v=Array.isArray(x)?x[0]:x,l=Array.isArray(x)?x[1]:x;return `<option value="${esc(v)}"${v==cur?' selected':''}>${l}</option>`}).join('')}</select>`};
const mvSw=(on,label,help,o)=>{o=o||{};return `<div class="mv-chk"><button type="button" class="sw${on?' on':''}" role="switch" aria-checked="${!!on}" aria-label="${esc(label.replace(/<[^>]*>/g,''))}"${o.dis?' disabled':''} onclick="mvTog(this)"></button><div><b>${label}</b>${help?`<small>${help}</small>`:''}</div></div>`};
window.mvTog=b=>{b.classList.toggle('on');b.setAttribute('aria-checked',b.classList.contains('on'))};
const mvKv=rows=>`<dl class="mv-kv">${rows.filter(Boolean).map(r=>`<dt>${r[0]}</dt><dd${r[2]?` class="${r[2]}"`:''}>${r[1]}</dd>`).join('')}</dl>`;
const mvFoot=(l,r)=>`<div class="mv-foot">${l||''}<span class="mv-sp"></span>${r||''}</div>`;
const mvD=(sum,body,open)=>`<details class="mv-d"${open?' open':''}><summary>${sum}</summary><div class="mv-in">${body}</div></details>`;
const mvDemo=(id,items,hint)=>`<div class="mv-demo"><div class="tabs" role="tablist" aria-label="Mockup state" onclick="mvPick('${id}',event)">${items.map(([v,l])=>`<button type="button" data-v="${v}" class="${v==MVS[id]?'on':''}">${l}</button>`).join('')}</div><small>${hint||'Mockup state'}</small></div>`;
window.mvPick=(id,e)=>{const b=e.target.closest('button');if(!b)return;[...e.currentTarget.children].forEach(x=>x.classList.toggle('on',x===b));MVS[id]=b.dataset.v;const el=document.querySelector(`[data-mvb="${id}"]`);if(el){el.innerHTML=MVB[id](MVS[id]);MVA[id]&&MVA[id]()}};
/* a page: heading, state demo tabs, body that depends on the state */
const mvPage=(id,title,sub,states,body,def,after)=>{MVB[id]=body;if(after)MVA[id]=after;P[id]=()=>{MVS[id]=def;return mvHead(title,sub)+mvDemo(id,states)+`<div data-mvb="${id}">${body(def)}</div>`};AFTER[id]=()=>{after&&after()}};
const VIEWNOTE=mvNote('','You can view this page but not change it.');
const mvMeter=(pct,hot)=>`<div class="meter"><i${hot?' class="hot"':''} style="width:${pct}%"></i></div>`;

/* =====================================================================================================
   ATEM  (app/modules/atem, atem.html, atem.js)
   ===================================================================================================== */
const AT_IN=['Cam 1 Wide','Cam 2 Pulpit','Cam 3 Choir','Cam 4 Piano','ProPresenter','Black'];
const atDev=(o)=>{
  const ctl=(o.macros||[]).length;
  const settings=o.manage?mvD('Settings',
    `<div class="mv-form">${mvF('Name',mvIn(o.name,'',{al:'Name'}))}${mvF('Address',mvIn(o.host,'',{mono:1,al:'Address'}))}${mvF('Port',mvIn(o.port,'',{type:'number',min:1,max:65535,al:'Port'}))}${mvF('First camera number',mvIn(o.off+1,'',{type:'number',min:1,max:901,al:'First camera number'}))}</div>`+
    mvSw(o.tally,'Drives the tally lights','')+
    `<p class="d" style="margin:0 0 10px">Camera 1 on this switcher shows as the first camera number on the tally screens. Give each switcher that drives tally its own range, like 1 and 101.</p>
     <div class="mv-row">${mvB('Save','Switcher saved','p')}${mvB('Remove','Removed from Relay. The switcher itself is not changed.','d')}</div>`):'';
  const control=o.connected?mvD('Control',
    `<div class="mv-me"><strong>M/E 1</strong><span>Program: ${o.prog} · Preview: ${o.prev}</span>${o.control?`${mvSel(AT_IN,o.prev,{al:'Source'})}${mvB('Set preview','Preview set')}${mvB('Set program','Program set')}${mvB('Cut','Cut on M/E 1','p')}${mvB('Auto','Auto transition')}${mvB('Fade to black','Fading to black')}`:''}</div>`+
    (o.control?Array.from({length:o.dsk},(_,k)=>`<div class="mv-me"><strong>Downstream key ${k+1}</strong>${mvB('Auto','Downstream key '+(k+1)+' auto')}${mvB('On air','Downstream key '+(k+1)+' on air')}${mvB('Off air','Downstream key '+(k+1)+' off air')}</div>`).join(''):'')+
    `<div class="mv-me"><strong>Macros</strong>${ctl?o.macros.map((m,i)=>o.control?mvB((i+1)+'. '+m,'Running macro '+m):`<span>${i+1}. ${m}</span>`).join(''):'<span class="mv-muted">None recorded on the switcher.</span>'}${ctl&&o.control?mvB('Stop macro','Macro stopped'):''}</div>`,true):'';
  return `<div class="acard" data-id="${o.id}"><div class="mv-row" style="padding:2px 0 6px"><strong>${o.name}</strong><span class="badge ${o.connected?'ok':o.err?'warn':''}">${o.state}</span>${o.manage?`<button type="button" class="sw${o.enabled?' on':''} mv-right" role="switch" aria-checked="${o.enabled}" aria-label="Connect to ${o.name}" title="Connect to this switcher" onclick="mvTog(this)"></button>`:''}</div>
   <div class="mv-m mv-muted" style="margin-bottom:8px">${o.facts}</div>
   ${o.old?mvNote('warn','This switcher runs firmware older than 8.0.1. Cuts and tally work; update it with ATEM Setup for full support.'):''}${o.refound?mvNote('ok',o.refound):''}
   ${settings}${control}</div>`;
};
const AT_FOUND=[
  ['ATEM Mini Extreme ISO','','ATEM switcher','192.168.1.240','9.4','added'],
  ['ATEM Mini Pro','','ATEM switcher','192.168.1.241','8.0','add'],
  ['ATEM Television Studio HD8','','ATEM switcher','','8.6.2','noaddr'],
  ['HyperDeck Studio Mini','','HyperDeck','192.168.1.250','8.2','no'],
  ['Videohub','Smart Videohub 20 x 20','Videohub','192.168.1.251','','no'],
  ['Studio Camera','Blackmagic Studio Camera 4K Pro','Camera','192.168.1.252','8.3','no']];
function atFound(probe,empty){
  if(empty)return `<p class="mv-note" style="margin:0">Nothing answered. ${probe?'Check the switcher is on this network, or add it by its address.':'Try again with "every address on this subnet", or add it by its address.'}</p>`;
  return `<p class="mv-note">Found ${AT_FOUND.length} devices.</p><div class="mv-over"><table class="dt"><thead><tr><th>Device</th><th>Kind</th><th>Address</th><th>Firmware</th><th></th></tr></thead><tbody>${AT_FOUND.map(f=>`<tr><td class="two"><b>${f[0]}</b>${f[1]?`<small>${f[1]}</small>`:''}</td><td>${f[2]}</td><td class="mv-m">${f[3]||'—'}</td><td class="mv-m">${f[4]||'—'}</td><td>${f[5]=='added'?'<span class="mv-ok">Added</span>':f[5]=='add'?mvB('Add','Switcher added','p sm'):f[5]=='noaddr'?'<button type="button" class="btn sm" disabled title="It did not say its address: add it by address below.">Add</button>':'<span class="mv-muted">Not controlled yet</span>'}</td></tr>`).join('')}</tbody></table></div>`;
}
function atemBody(s){
  const view=s=='view',none=s=='none',down=s=='down';
  const manage=!view,control=!view;
  const d1={id:'sw1',name:'Sanctuary',connected:true,state:'connected to ATEM Mini Extreme ISO',enabled:true,manage,control,off:0,tally:true,host:'192.168.1.240',port:9910,prog:'Cam 1 Wide',prev:'Cam 2 Pulpit',dsk:2,macros:['Walk-in','Worship','Message','Closing','Lower third'],
    facts:'192.168.1.240:9910 · protocol 2.30 · tally cameras 1–40 · followed by its network id'};
  const d2=down?{id:'sw2',name:'Youth Room',connected:false,err:true,state:'No answer from the switcher',enabled:true,manage,control,off:100,tally:true,host:'192.168.1.241',port:9910,facts:'192.168.1.241:9910 · tally cameras 101–140 · followed by its network id'}
   :{id:'sw2',name:'Youth Room',connected:true,state:'connected to ATEM Mini Pro',enabled:true,manage,control,off:100,tally:true,host:'192.168.1.241',port:9910,prog:'Cam 1 Wide',prev:'ProPresenter',dsk:1,macros:[],old:true,refound:'Followed to its new address 192.168.1.241 (it was 192.168.1.37).',facts:'192.168.1.241:9910 · ATEM Mini Pro · protocol 2.28 · tally cameras 101–140 · followed by its network id'};
  const devs=none?[]:[d1,d2];
  const status=none?mvStatus('warn','Not set up',['Not set up: add a switcher','Find one on the network below, or add it by its address.'])
    :down?mvStatus('err','Needs a look',['Youth Room: No answer from the switcher','Sanctuary is connected. Relay keeps trying the other by itself.'],mvB('Test connection','Sanctuary: connected to ATEM Mini Extreme ISO; Youth Room: No answer from the switcher.','sm'))
    :mvStatus('ok','Connected',['All 2 switchers connected','Tally for cameras 1–40 and 101–140 is live on /tally.'],mvB('Test connection','Sanctuary: connected; Youth Room: connected.','sm'));
  const warn=down?mvNote('warn','Sanctuary and Youth Room both drive tally with overlapping camera numbers: camera 1 means a camera on either one. Give one of them a different first camera number.'):'';
  const sw=`<section class="card mv-card"><h3>Switchers</h3><p class="d">Relay joins each switcher as one more control app, and reconnects by itself. A switcher added from the network list is followed to a new address if its DHCP lease changes.</p>
    <div class="mv-list" id="atDevices">${devs.map(atDev).join('')}</div>${none?`<p class="mv-note" style="margin:0" id="atNone">No switchers yet. Find one on the network below, or add it by its address.</p>`:''}</section>`;
  const find=manage?mvCard('Find on the network','Lists Blackmagic gear that announces itself on this network: switchers, HyperDecks, Videohubs, cameras and more. Only ATEM switchers can be added here for now.',
    `<div class="mv-row"><button type="button" class="btn p" id="atScan">Scan</button><label style="display:flex;gap:8px;align-items:center;margin:0;color:var(--fg);font-size:13px"><input type="checkbox" id="atProbe"> Also try every address on this subnet</label></div><div id="atScanOut" style="margin-top:12px">${none?'':atFound(false)}</div>`,{id:'atFind'}):'';
  const add=manage?mvCard('Add by address','For a switcher the scan does not find, for example on another VLAN.',
    `<div class="mv-form">${mvF('Name',mvIn('','Main room',{al:'Name'}))}${mvF('Address',mvIn('','192.168.1.240',{mono:1,al:'Address'}))}${mvF('Port',mvIn(9910,'',{type:'number',min:1,max:65535,al:'Port'}))}</div>${mvFoot('','<button type="button" class="btn p" onclick="toast(\'Switcher added\')">Add switcher</button>')}`,{id:'atAdd'}):'';
  return `<div class="mv-stack">${status}${warn}${view?VIEWNOTE:''}${sw}${find}${add}</div>`;
}
mvPage('atem','ATEM','Connect Relay to one or more Blackmagic ATEM switchers on the same network. Their program and preview tally shows on the camera screens at <code class="mv-k">/tally</code>, and the booth can cut and take from here.',
  [['ok','Connected'],['down','Not connected'],['none','No switchers'],['view','View only']],atemBody,'ok',()=>{
  const b=document.getElementById('atScan');if(!b)return;
  b.onclick=()=>{b.disabled=true;b.textContent='Scanning…';setTimeout(()=>{b.disabled=false;b.textContent='Scan';const out=document.getElementById('atScanOut');if(out)out.innerHTML=atFound(document.getElementById('atProbe').checked)},700)};
});
reg({kind:'page',id:'atem',t:'ATEM',g:'Output',ic:'mod_atem',module:'atem',perm:'atem.view'});

/* =====================================================================================================
   Tally Arbiter  (app/modules/tally_arbiter; AdminPage.id is tallyarbiter)
   ===================================================================================================== */
const TA_CAMS=[[1,'Camera 1 Wide','program'],[2,'Camera 2 Pulpit','preview'],[3,'Camera 3 Choir',''],[4,'Camera 4 Piano',''],[5,'Lobby stream',''],[6,'Youth room','']];
function taBody(s){
  const view=s=='view',off=s=='off',err=s=='err',none=s=='none',idle=none||err||off;
  const st=none?mvStatus('warn','Not set up',['Not set up: enter Tally Arbiter\'s address','Enter the server\'s address to begin.'])
    :err?mvStatus('err','Not connected',['Could not reach Tally Arbiter','Relay keeps trying. Check the server is running and the address and port are right.'],mvB('Test connection','Could not reach Tally Arbiter','sm'))
    :off?mvStatus('warn','Connecting',['Connecting…','Waiting for Tally Arbiter to answer.'],mvB('Test connection','Not connected yet.','sm'))
    :mvStatus('ok','Connected',['Connected, 6 cameras','Camera 1 is on program, camera 2 on preview.'],mvB('Test connection','Connected.','sm'));
  const cams=idle?'<p class="mv-note" style="margin:0" id="taEmpty">None yet.</p>':`<div class="mv-list" id="taList">${TA_CAMS.map(c=>`<div class="mv-row"><span>${c[0]}. ${c[1]}${c[2]=='program'?' · on program':c[2]=='preview'?' · on preview':''}</span>${c[2]=='program'?'<span class="badge err mv-right">PGM</span>':c[2]=='preview'?'<span class="badge ok mv-right">PVW</span>':''}</div>`).join('')}</div>`;
  return `<div class="mv-stack">${st}${view?VIEWNOTE:''}
  ${mvCard('Server','',`<div class="mv-form">${mvF('Address',mvIn(none?'':'192.168.1.50','192.168.1.50',{mono:1,dis:view,al:'Address'}))}${mvF('Port',mvIn(4455,'',{type:'number',min:1,max:65535,dis:view,al:'Port'}))}</div>
   ${mvFoot(`<small id="taStatus">${none?'Enter the server\'s address to begin.':err?'Could not reach Tally Arbiter':off?'Connecting…':'Connected.'}</small>`,`<button type="button" class="btn p"${view?' disabled':''} onclick="toast('Tally Arbiter address saved')">Save</button>`)}`)}
  ${mvCard('Cameras','Numbered in the order Tally Arbiter lists them.',cams,{cnt:idle?'':'6 cameras'})}</div>`;
}
mvPage('tallyarbiter','Tally Arbiter','Listen to a Tally Arbiter server, for switchers Relay does not speak itself (OBS, vMix and others). Each Tally Arbiter device becomes a camera, numbered in the order Tally Arbiter lists them, on the camera screens at <code class="mv-k">/tally</code>.',
  [['ok','Connected'],['off','Connecting'],['err','Error'],['none','Not set up'],['view','View only']],taBody,'ok');
reg({kind:'page',id:'tallyarbiter',t:'Tally Arbiter',g:'Output',ic:'mod_tallyarbiter',module:'tally_arbiter',perm:'tally_arbiter.view'});

/* =====================================================================================================
   OBS Studio  (app/modules/obs, obs.html, obs.js; the scene screen /obs is another file)
   ===================================================================================================== */
const obsChart=(on)=>{
  if(!on)return `<p class="mv-note" style="margin:10px 0 0">The bitrate chart shows here while OBS streams.</p>`;
  const pts=Array.from({length:60},(_,i)=>[(i/59*476+2).toFixed(1),(50-(0.78+0.1*Math.sin(i/4)+(i==38?-0.3:0))*46).toFixed(1)]).map(p=>p.join(',')).join(' ');
  return `<svg class="mv-chart" viewBox="0 0 480 64" preserveAspectRatio="none" role="img" aria-label="Bitrate over the last 15 minutes, 5900 kb/s now"><rect x="${38/59*476+1}" y="0" width="2" height="50" fill="var(--warn)" opacity=".55"/><polyline points="${pts}" fill="none" stroke="currentColor" stroke-width="1.5" vector-effect="non-scaling-stroke"/><text x="2" y="62">5900 kb/s now · peak 6500 kb/s · last 15 min</text></svg>`;
};
const OBS_SCENES=['Walk-in loop','Worship','Message','Lower third','Offering','Closing slide'];
function obsAudio(p){
  const rows=[['Lectern mic',-22,-14,0],['Worship mix',-14,-6,0],['Pastor headset',0,0,1],['Desktop Audio',-31,-20,0]];
  return `<div class="mv-box"><div class="mv-bh"><h4>Audio</h4></div>
   <div class="mv-aud">${rows.map(r=>{const w=d=>Math.max(0,Math.min(100,(d+60)/60*100));return `<span class="nm" title="${r[0]}">${r[0]}</span><div class="mt"><div class="meter"><i${r[2]>-12?' class="hot"':''} style="width:${r[3]?0:w(r[1])}%"></i></div>${r[3]?'':`<b style="left:${w(r[2])}%"></b>`}</div><div class="rd${r[3]?' mu':''}"><span>${r[3]?'MUTED':r[1]+' dB'}</span>${p.control?mvB(r[3]?'Unmute':'Mute',(r[3]?'Unmuted ':'Muted ')+r[0],'sm'):''}</div>`}).join('')}
   <span class="nm" title="Candle camera mic">Candle camera mic</span><div class="mt"><div class="meter"><i style="width:0%"></i></div></div><div class="rd"><span>not in OBS</span>${p.control?'<button type="button" class="btn sm" disabled>Mute</button>':''}</div></div></div>`;
}
function obsMachine(o){
  const c=o.control,m=o.manage;
  const lv=o.live;                    // null = never saved; else state connected|down
  let head=`<h3>${o.name}<span class="mv-cnt">${o.host}${o.ver?' · OBS '+o.ver:''}</span></h3>`;
  let live='';
  if(!lv)live=`<p class="mv-note" style="margin:0 0 12px">Save to connect.</p>`;
  else if(lv.state!='connected')live=`<div class="mv-box" style="margin-bottom:12px"><div class="mv-bh"><span class="badge sbadge err"><i></i>Off air</span><span class="badge err">Trouble</span></div><p class="mv-note" style="margin:0">${lv.detail}</p></div>`;
  else{
    const on=lv.stream,rec=lv.rec;
    const pills=`<span class="badge sbadge ${on?'err':''}"><i></i>${on?'LIVE '+lv.clock:'Off air'}</span>${rec?`<span class="badge sbadge err"><i></i>REC ${rec}</span>`:''}<span class="badge ${lv.health=='ok'?'ok':lv.health=='warn'?'warn':lv.health=='error'?'err':''} mv-right">${{ok:'Healthy',warn:'Needs a look',error:'Trouble',idle:'Standing by'}[lv.health]}</span>`;
    const dl=mvKv([
      ['Program',lv.program+(lv.studio?'  ·  preview: '+lv.pvw:'')],
      ['Streams to',`YouTube - Live streaming${o.yt?` · <a href="javascript:void(0)" onclick="toast('Opens YouTube Studio')" style="color:var(--fg)">Open in YouTube Studio</a> · <a href="javascript:void(0)" onclick="toast('Opens the watch page')" style="color:var(--fg)">Watch page</a>`:''}`],
      on?['Stream','2.31 GB sent · congestion 3%']:null,
      on?['Dropped','4 of 129,480 frames (0.00%)']:null,
      ['Last minute',lv.minute],
      ['Recording',rec?'Recording, '+rec+' · 3.4 GB':'Not recording'],
      ['Replay buffer',o.replay?'Running · last saved 10:51':'Off in OBS (Settings › Output › Replay Buffer)'],
      o.caps?['Captions',(on?'1,284':'0')+' lines sent · 2 s delay']:null,
      ['OBS '+o.ver,'30 fps · render 2.1 ms · CPU 14% · 412 GB free']]);
    const outs=c?`<div class="mv-row" style="margin-top:14px">${on?mvB('Stop stream','Stop the stream? Viewers will see it end.','d'):mvB('Start stream','Start streaming? It goes live on YouTube - Live streaming.','p')}${rec?mvB('Pause recording','Recording paused')+mvB('Stop recording','Recording stopped'):mvB('Start recording','Recording started')}${o.replay?mvB('Save replay','Replay saved')+mvB('Stop replay buffer','Replay buffer stopped'):''}</div>`:'';
    const side=o.preview?`<div class="mv-box"><div class="mv-bh"><h4>Program</h4></div><div class="mv-pre" role="img" aria-label="Preview of ${lv.program}">${lv.program}</div>${obsChart(on)}</div>`
      :`<div class="mv-box"><div class="mv-bh"><h4>Bitrate</h4></div>${obsChart(on)}</div>`;
    const scenesCard=`<div class="mv-box" style="margin-top:14px"><div class="mv-bh"><h4>Scenes</h4><span class="mv-right mv-row">${c?(lv.studio?mvB('Take: '+lv.pvw,'Took '+lv.pvw,'p sm')+mvB('Leave studio mode','Studio mode off','sm'):mvB('Studio mode','Studio mode on','sm')):''}</span></div>${lv.studio?`<p class="mv-note">Studio mode: tap a scene to put it in preview, then Take.</p>`:''}
      <div class="mv-scenes" style="margin-top:0">${OBS_SCENES.map(n=>`<button type="button" class="btn${n==lv.program?' p':n==lv.pvw&&lv.studio?' pv':''}"${c?'':' disabled'} title="${n==lv.program?'On air':n==lv.pvw&&lv.studio?'In preview':''}" onclick="toast('${n}${lv.studio?' in preview':' on air'}')">${n}</button>`).join('')}</div></div>`;
    live=`<div class="mv-2"><div class="mv-box"><div class="mv-bh">${pills}</div>${dl}${outs}</div>${side}</div>${scenesCard}${obsAudio(o).replace('<div class="mv-box">','<div class="mv-box" style="margin-top:14px">')}`;
  }
  const settings=m?mvD('Settings',`
   <div class="mv-form">${mvF('Name',mvIn(o.name,'Stream PC',{al:'Name'}))}${mvF('Address',mvIn(o.host,'192.168.1.60',{mono:1,al:'Address'}))}${mvF('Port',mvIn(4455,'',{type:'number',min:1,max:65535,al:'Port'}))}${mvF(o.pw?'Password (saved; leave blank to keep it)':'Password','<input type="password" autocomplete="new-password" maxlength="200" aria-label="Password">')}</div>
   ${mvSw(o.preview,'Show a preview of the program scene on this page')}
   <h4 class="mv-sub">Captions into the stream</h4>
   ${mvSw(o.caps,'Send Relay\'s captions into this stream (CEA-608)')}
   <div class="mv-form">${mvF('Language',mvSel([['source','Source language'],['en','English'],['es','Spanish']],'source',{al:'Language'}))}${mvF('Delay in seconds, to match a delayed stream',mvIn(o.caps?2:0,'',{type:'number',min:0,max:30,step:.5,al:'Delay in seconds'}))}</div>
   <h4 class="mv-sub">Alerts</h4>
   <div class="mv-form">${mvF('When this OBS is in trouble, raise',mvSel([['','None'],['a1','Stream down'],['a2','Booth help']],'a1'))}${mvF('When it needs a look, raise',mvSel([['','None'],['a1','Stream down'],['a2','Booth help']],''))}</div>
   <h4 class="mv-sub">Audio to watch</h4>
   ${lv&&lv.state=='connected'?`<div class="mv-list">${[['Lectern mic',1,0],['Worship mix',1,0],['Pastor headset',1,1],['Desktop Audio',1,0],['Candle camera mic',1,0],['Mic/Aux 2',0,0]].map(a=>`<div class="mv-row" style="gap:24px"><label style="display:flex;gap:8px;align-items:center;margin:0;color:var(--fg);font-size:13px"><input type="checkbox"${a[1]?' checked':''}> ${a[0]}</label><label style="display:flex;gap:8px;align-items:center;margin:0;color:var(--fg);font-size:13px"><input type="checkbox"${a[2]?' checked':''}${a[1]?'':' disabled'}> Warn if muted while live</label></div>`).join('')}</div>`:`<p class="mv-note" style="margin:0">Connect to OBS to list its audio inputs.</p>`}
   <h4 class="mv-sub">Tally</h4>
   <p class="d" style="margin:0 0 8px">Give a scene a camera number: that camera's tally shows red while the scene is on air, and green while it is in preview.</p>
   ${lv&&lv.state=='connected'?`<div class="mv-list">${[[1,'Worship'],[2,'Message'],[3,'Walk-in loop']].map(t=>`<div class="mv-row"><span>Camera</span><input type="number" min="1" max="999" value="${t[0]}" style="width:80px" aria-label="Camera number"><span>is scene</span>${mvSel(OBS_SCENES,t[1],{al:'Scene',w:220})}${mvB('Remove','Tally row removed','sm')}</div>`).join('')}</div><div class="mv-row" style="margin-top:8px">${mvB('Add camera','Tally row added','sm')}</div>`:`<p class="mv-note" style="margin:0">Connect to OBS to pick scenes.</p>`}
   ${o.id?`<h4 class="mv-sub">Scene screen for a tablet</h4><p class="d" style="margin:0 0 8px">A page of big scene buttons for this computer. The link is its only key: anyone with it can switch scenes, and a new link stops the old one.</p>
   <div class="mv-row">${mvB('Turn screen off','Scene screen off')}${mvB('New link','The old link stops working. Continue?')}<input class="mv-m" readonly value="https://relay.church.lan/obs/screen/k7Q2xW9mTf4e" aria-label="Scene screen link" style="flex:1;min-width:220px" onfocus="this.select()"></div>`:''}
   <h4 class="mv-sub">This computer</h4>
   <div class="mv-row">${mvB('Test connection',lv&&lv.state=='connected'?o.name+': connected to OBS '+o.ver+' (obs-websocket 5.5.4), 6 scenes.':o.name+': OBS did not answer.')}${o.pw?mvB('Forget password','Password forgotten'):''}${mvB('Remove computer','Computer removed','d')}</div>
   <p class="d" style="margin:10px 0 0">Changes take effect when you press Save at the bottom of the page.</p>`,!o.id):'';
  return `<section class="card mv-card obsmachine">${head}${live}${settings}</section>`;
}
function obsBody(s){
  const view=s=='view',none=s=='none',down=s=='down';
  const base={manage:!view,control:!view,preview:true,pw:true,id:'m1'};
  const m1={...base,name:'Stream PC',host:'192.168.1.60',ver:'30.2.3',yt:true,caps:true,replay:true};
  let ms=[],st;
  if(none){ms=[];st=mvStatus('warn','Not set up',['No OBS computer yet','Add the computer that runs OBS.'])}
  else if(down){ms=[{...m1,live:{state:'down',detail:'Stream PC: Could not reach OBS. Check OBS is open and Tools › WebSocket Server Settings is on.'}}];st=mvStatus('err','Trouble',['Stream PC: Could not reach OBS','Relay keeps trying by itself.'],mvB('Test connection','Stream PC: OBS did not answer.','sm'))}
  else if(s=='idle'){ms=[{...m1,preview:false,live:{state:'connected',stream:false,rec:'',health:'idle',program:'Walk-in loop',studio:false,minute:'0% dropped · 0% skipped · 0% lagged'}}];st=mvStatus('idle','Standing by',['Stream PC: standing by','OBS is open and not streaming or recording.'],mvB('Test connection','Stream PC: connected to OBS 30.2.3 (obs-websocket 5.5.4), 6 scenes.','sm'))}
  else{ms=[{...m1,live:{state:'connected',stream:true,rec:'1:12:08',clock:'1:12:08',health:'ok',program:'Message',studio:true,pvw:'Offering',minute:'0% dropped · 0% skipped · 0% lagged'}},
    {...m1,id:'m2',name:'Record PC',host:'192.168.1.61',preview:false,yt:false,caps:false,replay:false,live:{state:'connected',stream:false,rec:'1:12:09',health:'ok',program:'Message',studio:false,minute:'0% dropped · 0% skipped · 0% lagged'}}];
    st=mvStatus('ok','On air',['Stream PC is live for 1 h 12 min, Record PC is recording','Healthy on both computers.'],mvB('Test connection','Stream PC: connected to OBS 30.2.3; Record PC: connected to OBS 30.2.3.','sm'))}
  const foot=!view?`<div class="mv-row">${`<button type="button" class="btn" onclick="toast('OBS computer added')">Add OBS computer</button>`}<span class="mv-sp"></span><button type="button" class="btn p" onclick="toast('OBS settings saved')">Save</button></div>`:'';
  return `<div class="mv-stack">${st}${view?VIEWNOTE:''}${ms.map(obsMachine).join('')}${none?mvCard('No OBS computer yet','',mvNote('','No OBS computer yet. Add the computer that runs OBS.'),{}):''}${foot}${mvNote('','In OBS, open Tools &gt; WebSocket Server Settings, switch on the server and note the port (4455) and password. Captions reach viewers only on platforms that show CEA-608 captions from RTMP (YouTube, Twitch); they are sent only while OBS is streaming.')}</div>`;
}
mvPage('obs','OBS Studio','Follow OBS on one or more computers: the scene, whether it is streaming and recording, dropped frames, and a preview. Switch scenes from here or from a tablet, and send Relay\'s captions into the stream.',
  [['live','Streaming'],['idle','Standing by'],['down','Error'],['none','No computer'],['view','View only']],obsBody,'live');
reg({kind:'page',id:'obs',t:'OBS Studio',g:'Output',ic:'mod_obs',module:'obs',perm:'obs.view'});

/* =====================================================================================================
   YouTube  (app/modules/youtube, youtube.html, youtube.js; the Connect card is core's oauth.js)
   ===================================================================================================== */
function ytOauth(a,manage){
  const WORD={connected:'Connected',waiting:'Waiting for you',disconnected:'Not connected',sign_in:'Sign in again'};
  const CLS={connected:'ok',waiting:'',disconnected:'',sign_in:'warn'};
  const acts=a=='waiting'?mvB('Cancel','Sign-in cancelled'):a=='disconnected'?mvB('Connect','Sign-in started','p'):mvB('Connect again','Sign-in started',a=='sign_in'?'p':'')+mvB('Disconnect','Disconnect this account? Relay forgets it and asks YouTube to remove its access.');
  let body='';
  if(a=='waiting')body=`<p style="margin:0 0 6px">On any phone or computer, go to <a href="javascript:void(0)" style="color:var(--fg)">https://www.google.com/device</a> and enter this code:</p><div class="mv-code">WDJB-MZVR</div><p class="d" style="margin:0 0 10px">The code works for about 28 more minutes. This card updates by itself once you have signed in.</p>`;
  else if(a=='connected'||a=='sign_in')body=mvKv([['Account','Grace Community Church'],['Connected','Nov 13, 2026, 9:02 AM by Justin Small'],['Access','Read the channel (read only)']])+(a=='sign_in'?`<div class="mv-note err" style="margin-top:12px">YouTube stopped accepting Relay. Press Connect to sign in again.</div>`:'');
  else body=`<p class="d" style="margin:0 0 10px">Connect the church's YouTube channel. Relay only reads it: whether it is live and how many are watching.</p>`;
  return `<section class="card mv-card" id="ytAccount"><div class="mv-bh" style="display:flex;gap:8px;align-items:center;margin-bottom:10px"><h3 style="margin:0">YouTube</h3><span class="badge sbadge ${CLS[a]}"><i></i>${WORD[a]}</span></div>${body}${manage?`<div class="mv-row" style="margin-top:12px">${acts}</div>`:''}</section>`;
}
function ytBody(s){
  const view=s=='view',manage=!view;
  const a=s=='off'?'disconnected':s=='wait'?'waiting':s=='signin'?'sign_in':'connected';
  const live=s=='live'||s=='view';
  const hdPill=live?'<span class="badge sbadge ok"><i></i>On air</span>':s=='warn'||s=='signin'?'<span class="badge sbadge warn"><i></i>Needs a look</span>':'<span class="badge sbadge"><i></i>Standing by</span>';
  let st;
  if(live)st=mvStatus('ok','On air',['Live: 214 watching','Sunday Worship, 10:30 service, live for 1 h 12 min.'],manage?mvB('Check now','Checked just now','sm'):'');
  else if(s=='warn')st=mvStatus('warn','Needs a look',['YouTube did not answer','Relay tries again in 10 min.'],manage?mvB('Check now','Checking YouTube','sm'):'');
  else if(s=='off')st=mvStatus('warn','Not set up',['Not connected. Press Connect on the YouTube page.','Relay does not contact YouTube until a channel is connected.']);
  else if(s=='wait')st=mvStatus('warn','Waiting',['Waiting for you to sign in','Enter the code on the Connect card.']);
  else if(s=='signin')st=mvStatus('err','Sign in again',['YouTube stopped accepting Relay','Press Connect on the card to sign in again.']);
  else st=mvStatus('idle','Standing by',['Standing by','The channel is connected and nothing is live or being tested.'],manage?mvB('Check now','Checked just now','sm'):'');
  const note=(a!='connected'||s=='warn')?mvNote(s=='warn'||a!='connected'?'err':'',s=='warn'?'YouTube did not answer. Relay tries again in 10 min.':a=='disconnected'?'Not connected. Press Connect on the YouTube page.':a=='waiting'?'Waiting for you to sign in.':'YouTube stopped accepting Relay.'):'';
  let bcs='';
  if(live)bcs=`<div><h4 style="margin:0 0 8px;font-size:13px;font-weight:600">Sunday Worship - 10:30 Service</h4>${mvKv([['State','Live'],['Live for','1 h 12 min'],['Watching now','214'],['Privacy','public']])}<div class="mv-row" style="margin-top:10px"><a href="javascript:void(0)" onclick="toast('Opens the watch page')" style="color:var(--fg)">Watch page</a><a href="javascript:void(0)" onclick="toast('Opens YouTube Studio')" style="color:var(--fg)">Open in YouTube Studio</a></div></div>`;
  else if(a=='connected'&&s!='warn')bcs=`<p class="mv-note" style="margin:0">No broadcast is live or being tested.</p>`;
  const foot=`<p class="d" style="margin:12px 0 0">${a=='connected'?'Checked 12 s ago. ':''}Relay checks every 1 min while something is on air and every 10 min otherwise.</p>`;
  const liveCard=`<section class="card mv-card" id="ytLive"><div class="mv-bh" style="display:flex;gap:8px;align-items:center;margin-bottom:10px"><h3 style="margin:0">Live status</h3>${hdPill}</div>${note}${bcs}${foot}${manage&&a=='connected'?`<div class="mv-row" style="margin-top:12px">${mvB('Check now','Checked just now')}</div>`:''}</section>`;
  const settings=manage?mvCard('Settings','How often Relay asks YouTube.',`<div class="mv-form">${mvF('Seconds between checks while on air',mvIn(60,'',{type:'number',min:30,max:600,al:'Seconds between checks while on air'}),'YouTube or another stream Relay follows (OBS) is on air.')}${mvF('Seconds between checks otherwise',mvIn(600,'',{type:'number',min:120,max:3600,al:'Seconds between checks otherwise'}))}</div>
   <p class="d" style="margin:12px 0 0">Every Relay shares one daily YouTube allowance, so it checks rarely. A check costs 1 unit, or 2 while live.</p>
   <div style="margin-top:10px">${mvD('Your own Google app',`<p class="d" style="margin:0 0 10px">Relay signs in with its own Google app. To use your own instead, enter its client ID and secret ("TVs and Limited Input devices" client in Google Cloud).</p>
   <div class="mv-form">${mvF('Client ID',mvIn('','….apps.googleusercontent.com',{mono:1,al:'Client ID'}))}${mvF('Client secret','<input type="password" autocomplete="off" placeholder="Saved; leave blank to keep it" aria-label="Client secret">')}</div>
   <p class="d" style="margin:10px 0 0">Changing the app does not change an account already connected: press Connect again to sign in with it.</p>`)}</div>
   ${mvFoot('',`<button type="button" class="btn" disabled>Use Relay's app</button><button type="button" class="btn p" onclick="toast('YouTube settings saved.')">Save</button>`)}`,{id:'ytSettings'}):'';
  return `<div class="mv-stack">${st}${view?VIEWNOTE:''}<div class="mv-2">${liveCard}${ytOauth(a,manage)}</div>${settings}</div>`;
}
mvPage('youtube','YouTube','Whether the church\'s channel is live, for how long, and how many are watching. It shows on the status view beside OBS and the other streams.',
  [['live','On air'],['idle','Standing by'],['warn','Error'],['off','Not connected'],['wait','Waiting for code'],['signin','Sign in again'],['view','View only']],ytBody,'live');
reg({kind:'page',id:'youtube',t:'YouTube',g:'Output',ic:'mod_youtube',module:'youtube',perm:'youtube.view'});

/* =====================================================================================================
   Lighting  (app/modules/lighting, lighting.html, lighting.js, lighting-buttons.js; screen.html is another file)
   ===================================================================================================== */
const LT_CUES1=[['Walk-in','c1','',''],['Worship','c2','on','ok'],['Message','c3','',''],['Prayer','c4','','warn'],['Offering','c5','',''],['Closing','c6','','mu'],['Blackout','c7','','err']];
function ltCue(c,p,cueOn){
  return `<div class="mv-cuerow"><span class="mv-nm">${p.control?`<button type="button" class="btn${cueOn?' p':''}" onclick="toast('Fired ${c}')">${c}</button>`:c}</span>${p.manage?`${mvB('Look','Editing the look of '+c,'sm')}${mvB('Up','Moved up','sm')}${mvB('Down','Moved down','sm')}${mvB('Remove','Cue removed','sm')}`:''}</div>`;
}
function ltPage1(p){
  const rows=LT_CUES1.map(c=>ltCue(c[0],p,c[0]=='Worship')).join('')+`<div class="mv-cuerow"><span class="mv-nm">House lights</span><input type="range" min="0" max="100" value="70" ${p.control?'':'disabled'} aria-label="House lights" oninput="this.nextElementSibling.textContent=this.value+'%'"><span>70%</span>${p.manage?`${mvB('Look','Editing the look of House lights','sm')}${mvB('Up','Moved up','sm')}${mvB('Down','Moved down','sm')}${mvB('Remove','Cue removed','sm')}`:''}</div>`;
  const prev=`<div class="mv-lt" data-shape-grid>${LT_CUES1.map((c,i)=>`<button type="button" class="c ${c[3]}${c[0]=='Worship'?' on':''}${i==2?' pk':''}" data-shape="${i==5?'pill':'rounded'}" style="${i==0?'grid-column:span 2':''}" onclick="toast('Picked ${c[0]} to restyle')">${c[0]}</button>`).join('')}</div>`;
  return `<div class="mv-box" style="border:1px solid var(--border);border-radius:calc(var(--r) - 2px);padding:12px 14px;background:var(--bg)"><div class="fld" style="max-width:360px;margin-bottom:10px">${`<input value="Sunday service" maxlength="40" aria-label="Page name"${p.manage?'':' disabled'}>`}</div>${rows}
  ${p.manage?`<div class="mv-row" style="margin-top:10px"><input placeholder="Cue id" maxlength="64" style="width:140px" aria-label="Cue id"><input placeholder="Name" maxlength="60" style="width:180px" aria-label="Name">${mvB('Add cue','Cue added')}<span class="mv-sp" style="flex:1"></span>${mvB('Up','Page moved up','sm')}${mvB('Down','Page moved down','sm')}${mvB('Delete page','Page deleted','d sm')}</div>
   ${mvSw(false,'Place buttons freely (drag and resize)')}`:''}
  <h4 class="mv-sub">How it looks on the screen</h4>${prev}
  ${p.manage?`<div class="mv-form" style="margin-top:12px">${mvF('Text',mvIn('','Message',{al:'Text'}))}${mvF('Shape',mvSel([['rounded','Rounded'],['rect','Square corners'],['pill','Pill'],['circle','Circle']],'rounded',{al:'Shape'}))}${mvF('Colour','<input type="color" aria-label="Colour" style="padding:2px;height:34px">')}${mvF('Text colour','<input type="color" aria-label="Text colour" style="padding:2px;height:34px">')}${mvF('Wide (cells)',mvIn(1,'',{type:'number',min:1,max:6}))}${mvF('Tall (cells)',mvIn(1,'',{type:'number',min:1,max:6}))}</div>
  <div class="mv-row" style="margin-top:12px">${mvB('Turn screen off','Screen turned off')}${mvB('New key','The old link stops working. Continue?')}${mvB('Show link','Link shown')}<input class="mv-m" readonly value="https://relay.church.lan/lighting/screen/p4Gx7Rb2Ke9a" aria-label="Screen link" style="flex:1;min-width:220px" onfocus="this.select()"></div>`:''}</div>`;
}
function ltPage2(p){
  const cues=[['Fog','ok',0,0,30,40],['Strobe','err',35,0,30,40],['Blue wash','',70,0,30,40],['Spots on band','warn',0,52,45,40],['All off','mu',55,52,45,40]];
  return `<div style="border:1px solid var(--border);border-radius:calc(var(--r) - 2px);padding:12px 14px;background:var(--bg)"><div class="fld" style="max-width:360px;margin-bottom:10px"><input value="Youth night" maxlength="40" aria-label="Page name"${p.manage?'':' disabled'}></div>
  ${cues.map(c=>ltCue(c[0],p,false)).join('')}
  <h4 class="mv-sub">How it looks on the screen</h4><div class="mv-lt mv-free">${cues.map(c=>`<button type="button" class="c ${c[1]}" style="left:${c[2]}%;top:${c[3]}%;width:${c[4]}%;height:${c[5]}%" data-shape="rounded" onclick="toast('Picked ${c[0]} to restyle')">${c[0]}</button>`).join('')}</div>
  ${p.manage?`<p class="d" style="margin:8px 0 0">Free placement: drag a button to move it, drag the corner of the picked button to resize.</p>`:''}</div>`;
}
function ltBody(s){
  const view=s=='view',none=s=='none',err=s=='err';
  const p={control:!view,manage:!view};
  let st;
  if(none)st=mvStatus('warn','Not set up',['Not set up: choose the lighting program','Pick the program that runs your lights below.']);
  else if(err)st=mvStatus('warn','Needs a look',['QLC+ did not answer.','Relay tries again by itself. A cue is never retried.'],mvB('Test connection','QLC+ did not answer.','sm'));
  else st=mvStatus('ok','Ready',['Ready','QLC+ is answering. 2 cue pages on screens and panels.'],mvB('Test connection','Connected to QLC+. Found 14 buttons, 3 sliders.','sm'));
  const program=mvCard('Lighting program','',`<div class="mv-form">${mvF('Program',mvSel([['','Choose a program'],['qlcplus','QLC+ (web API)'],['lightkey','Lightkey (OSC, macOS)'],['vista','Chroma-Q Vista (OSC)'],['thelightingcontroller','ShowXpress / TheLightingController (Live API)']],none?'':'qlcplus',{dis:view,al:'Program'}))}${mvF('Address',mvIn(none?'':'192.168.1.52','192.168.1.50',{mono:1,dis:view,al:'Address'}))}${mvF('Port',mvIn(none?'':9999,'',{type:'number',min:0,max:65535,dis:view,al:'Port'}))}${mvF('Console type (Vista)',mvIn('EX','EX',{dis:view,al:'Console type (Vista)'}))}${mvF('Fade time, seconds (Lightkey)',mvIn('','cue\'s own',{type:'number',min:0,max:3600,step:.1,dis:view,al:'Fade time'}))}${mvF('Password','<input type="password" autocomplete="new-password" maxlength="200" placeholder="'+(none?'':'(saved)')+'"'+(view?' disabled':'')+' aria-label="Password">')}</div>
  ${mvSw(false,'Also list the program\'s functions (scenes and chases) that have no button','',{dis:view})}
  ${mvFoot(`<small id="ltStatus">${err?'QLC+ did not answer.':''}</small>`,`${mvB('Test connection',none?'Choose the lighting program first.':'Connected to QLC+. Found 14 buttons, 3 sliders.','')}<button type="button" class="btn p"${view?' disabled':''} onclick="toast('Lighting program saved')">Save</button>`)}
  <p class="d" style="margin:12px 0 0">A cue that is fired is sent once. If the connection drops, Relay does not try again, because the cue may already have happened.</p>`);
  const pages=mvCard('Cue pages','Group the cues you want into pages; only a cue on a page can be fired from a screen or panel.',`<div class="mv-stack">${ltPage1(p)}${ltPage2(p)}</div>
   ${p.manage?mvFoot('',`${mvB('Add page','Page added')}<button type="button" class="btn p" onclick="toast('Pages saved')">Save pages</button>`):''}
   <div id="ltAll" style="margin-top:16px"><h4 class="mv-sub">All</h4><p class="d" style="margin:0 0 6px">Cues the program lists that are not on any page.</p>${['Chorus wash','Haze','Moving heads reset'].map(n=>`<div class="mv-cuerow"><span class="mv-nm">${p.control?mvB(n,'Fired '+n):n}</span>${p.manage?mvB('Add to first page','Added to Sunday service','sm'):''}</div>`).join('')}</div>`,{id:'ltPages'});
  return `<div class="mv-stack">${st}${view?VIEWNOTE:''}${program}${none?'':pages}</div>`;
}
mvPage('lighting','Lighting','Fire your lighting program\'s cues from Relay. Group the cues you want into pages; only a cue on a page can be fired from a screen or panel.',
  [['ok','Ready'],['err','Error'],['none','Not set up'],['view','View only']],ltBody,'ok');
reg({kind:'page',id:'lighting',t:'Lighting',g:'Output',ic:'mod_lighting',module:'lighting',perm:'lighting.view'});

/* =====================================================================================================
   ProPresenter: three admin pages of one module, one sidebar parent.
   ppcontrol (control.html + ppcontrol.js), pplive (live.html + pplive.js + slideview.js), propresenter (settings.html +
   propresenter.js + pclink.js). stage.html is the public /stage screen, not an admin page.
   ===================================================================================================== */
window.mvShow=id=>{const e=document.getElementById(id);if(e)e.hidden=!e.hidden};
const PP_GRP={Verse:'var(--primary)',Chorus:'var(--warn)',Bridge:'var(--err)',Tag:'var(--muted)'};
const ppCue=(g,n,t,live,dis)=>`<button type="button" class="mv-cue${live?' live':''}" style="--pp:${PP_GRP[g]||'var(--muted)'}"${dis?' disabled':''} onclick="toast('Slide ${n} triggered')"><span class="mv-img">${t}</span><span class="mv-meta"><span class="mv-gn">${g} · ${n}</span></span></button>`;
const PP_SLIDES=[['Verse',1,'Great is Thy faithfulness,<br>O God my Father'],['Verse',2,'There is no shadow<br>of turning with Thee'],['Chorus',3,'Great is Thy faithfulness!<br>Great is Thy faithfulness!'],['Chorus',4,'Morning by morning<br>new mercies I see'],['Verse',5,'Summer and winter,<br>and springtime and harvest'],['Chorus',6,'Great is Thy faithfulness!'],['Bridge',7,'Pardon for sin<br>and a peace that endureth'],['Tag',8,'(blank)']];
const ppStatusRow=(s,view)=>s=='off'?mvStatus('warn','Not set up',['Not following ProPresenter','Enter the address and port under ProPresenter, then switch Follow ProPresenter on.'])
  :s=='wait'?mvStatus('err','Not working',['Waiting for ProPresenter','Relay cannot reach ProPresenter. Check its Network setting is on and the port is right.'])
  :mvStatus('ok',view?'View-only':'Connected',[view?'Connected, view-only':'Connected to ProPresenter on Studio-Mac','Relay reads ProPresenter'+(view?' but never changes it.':' and can step, trigger and clear.')]);

/* ---------- ProPresenter control ---------- */
function ppcBody(s){
  const view=s=='view'||s=='perm',off=s=='off',wait=s=='wait',dis=view||off||wait;
  const D=dis?' disabled':'';
  const btn=(label,msg,cls,title)=>`<button type="button" class="btn ${cls||''}"${D}${title?` title="${title}"`:''} onclick="toast('${msg}')">${label}</button>`;
  const now=off?`<div class="mv-now"><div class="t mv-muted">ProPresenter is not set up. Enter its address under ProPresenter.</div></div>`
    :wait?`<div class="mv-now"><div class="t mv-muted">No answer from ProPresenter (Studio-Mac.local). Check it is open and Network is switched on.</div></div>`
    :`<div class="mv-now"><div class="mv-muted">Great Is Thy Faithfulness &nbsp;<span class="mv-chip" style="--pp:var(--primary)">Verse</span>slide 1 of 14 · Sunday AM › Song 2</div><div class="t" style="margin-top:6px">Great is Thy faithfulness,\nO God my Father</div><div class="mv-notes">Notes: Key of G, capo 2. Dana leads.</div><div class="n">Next: There is no shadow\nof turning with Thee</div></div>`;
  const lit={slide:1,media:0,props:0,messages:0,audio:0,video_input:0,announcements:0};
  const dock=['Slide','Media','Props','Messages','Audio','Video input','Announcements'];
  const dockKeys=['slide','media','props','messages','audio','video_input','announcements'];
  const ok=!dis;
  return `<div class="mv-stack">${ppStatusRow(s,s=='view')}${s=='perm'?VIEWNOTE:''}
  ${mvCard('Slides','',`<div class="mv-row">${btn('&larr; Previous','Previous slide','mv-big','Left arrow, up arrow or Page Up')}${btn('Next &rarr;','Next slide','p mv-big','Right arrow, down arrow or Page Down')}${btn('&laquo; Previous item','Previous item','','Shift + left arrow or Shift + up arrow')}${btn('Next item &raquo;','Next item','','Shift + right arrow or Shift + down arrow')}</div><div style="margin-top:12px">${now}</div><p class="d" style="margin:0">Arrow keys and Page Up / Page Down move the slide while this page is open. Shift + arrow keys move to the previous or next playlist item.</p>`)}
  ${mvCard('Screens and announcements','',`<div class="mv-row" style="margin-bottom:10px">${btn('Audience screens: '+(ok?'On':'?'),'Audience screens off')}${btn('Stage screens: '+(ok?'On':'?'),'Stage screens off')}<span class="mv-muted">${ok?'Main Screen, Lobby TV, Stage Left, Stage Right':''}</span></div><div class="mv-now"><div class="t mv-muted">${ok?'No announcement is up.':''}</div></div><p class="d" style="margin:0">Turning screens on or off is a change to ProPresenter, so it is off in view-only mode. Announcements are shown here and cleared from the dock below.</p>`)}
  ${mvCard('Slide grid','',`<div class="mv-row" style="margin-bottom:10px">${btn('Reload slides','Slides reloaded')}</div>${ok?`<div class="mv-grid">${PP_SLIDES.map((c,i)=>ppCue(c[0],c[1],c[2],i==0,view)).join('')}</div>`:`<p class="mv-note" style="margin:0">No presentation is up. Trigger a slide or a playlist item.</p>`}`,{cnt:ok?'Great Is Thy Faithfulness':''})}
  ${mvCard('Playlists','',`<div class="mv-row" style="margin-bottom:8px">${mvSel(ok?['Sunday AM','Sunday PM','Youth Night','Announcements']:['—'],'Sunday AM',{al:'Playlist',w:320,dis:dis})}${btn('Reload','Playlists reloaded')}</div><div class="mv-row" style="margin-bottom:10px">${btn('Check this playlist','Checking...','','')}</div>
   ${ok?`<div class="mv-over" id="ppDiag" hidden style="margin-bottom:10px"><p class="d" style="margin:0 0 6px">8 items (6 presentation, 2 header). 1 with no matching presentation.</p><table class="dt"><thead><tr><th>#</th><th>Type</th><th>Name</th><th>Presentation</th><th>Item ID</th><th>Check</th></tr></thead><tbody>
   <tr><td>2</td><td>presentation</td><td>Welcome Loop</td><td class="mv-m">Welcome Loop</td><td class="mv-m">8F2A…C41D</td><td>found</td></tr><tr><td>3</td><td>presentation</td><td>Great Is Thy Faithfulness</td><td class="mv-m">Great Is Thy Faithfulness</td><td class="mv-m">1B7E…90AA</td><td>found</td></tr><tr class="mv-bad"><td>5</td><td>presentation</td><td>Sermon: Nov 16</td><td class="mv-m">Sermon Nov 16</td><td class="mv-m">D03C…77F2</td><td>NOT FOUND</td></tr></tbody></table></div>
   <div id="ppItems"><div class="mv-mark" style="--pp:var(--primary)">PRE-SERVICE</div>
    ${[[1,'Welcome Loop','Master',0],[2,'Announcements','media',0]].map(i=>`<div class="mv-irow"><button type="button" class="btn mv-item"${D} onclick="toast('Item ${i[0]} triggered')"><span class="mv-th"></span><span class="mv-k2">${i[0]}</span><span>${i[1]}</span><span class="mv-tag">${i[2]=='Master'?'':i[2]}</span></button><button type="button" class="btn" onclick="mvShow('ppStrip${i[0]}')" aria-expanded="false">Slides</button></div><div class="mv-grid" id="ppStrip${i[0]}" hidden style="margin:2px 0 8px 14px">${PP_SLIDES.slice(0,3).map(c=>ppCue(c[0],c[1],c[2],false,true)).join('')}</div>`).join('')}
    <div class="mv-mark" style="--pp:var(--warn)">WORSHIP</div>
    <div class="mv-irow"><button type="button" class="btn mv-item live"${D}><span class="mv-th"></span><span class="mv-k2">3</span><span>Great Is Thy Faithfulness</span><span class="mv-tag">Short</span></button><button type="button" class="btn">Slides</button></div>
    <div class="mv-irow"><button type="button" class="btn mv-item"${D}><span class="mv-th"></span><span class="mv-k2">4</span><span>Be Thou My Vision</span></button><button type="button" class="btn">Slides</button></div>
    <div class="mv-irow dim"><button type="button" class="btn mv-item"${D}><span class="mv-th"></span><span class="mv-k2">5</span><span>Sermon: Nov 16</span></button></div></div>`:`<p class="mv-note" style="margin:0">There are no playlists.</p>`}`)}
  ${mvCard('Looks','',`<div class="mv-row">${ok?['Worship','Message','Prayer','Baptism','Black'].map(n=>btn(n,'Look: '+n)).join(''):'<span class="mv-muted">No looks.</span>'}</div>`)}
  ${mvCard('Macros','',`<div class="mv-row">${ok?['Lower third on','Lower third off','Sermon timer','Walk-in'].map(n=>btn(n,'Macro: '+n)).join(''):''}</div>${ok?'':`<p class="d" style="margin:8px 0 0">There are no macros.</p>`}
   ${view?'':`<div class="mv-row" style="margin-top:12px"><button type="button" class="btn" onclick="mvShow('ppMacroPick')">Choose what operators see</button></div><div id="ppMacroPick" hidden style="margin-top:12px">${mvSw(false,'Show every macro','Otherwise only the ticked ones appear and can be triggered.')}<div class="mv-list" style="margin:8px 0">${['Lower third on','Lower third off','Sermon timer','Walk-in','Reset all screens (admin only)'].map((n,i)=>`<label style="display:flex;gap:8px;align-items:center;margin:0;color:var(--fg);font-size:13px"><input type="checkbox"${i<4?' checked':''}> ${n}</label>`).join('')}</div><button type="button" class="btn p" onclick="toast('Macro selection saved')">Save</button></div>`}`)}
  ${mvCard('Timers','',ok?`<div class="mv-list">${[['Countdown to service','-00:04:32',1],['Sermon','00:00:00',0],['Offering','--:--:--',0]].map(t=>`<div class="mv-timer"><span class="nm">${t[0]}</span><span class="mv-tm${t[2]?' run':''}">${t[1]}</span>${['Start','Stop','Reset'].map(o=>btn(o,o+' '+t[0],'sm')).join('')}</div>`).join('')}</div>`:`<p class="mv-note" style="margin:0">No timers.</p>`)}
  ${mvCard('Messages','',ok?`<div class="mv-list">${[['Nursery','Call: {Name} please see the nursery',['Name','Name'],0],['Parking','Please move the {Car}',['Car','Silver Honda'],1]].map(m=>`<div><div${m[3]?' style="color:var(--primary)"':''}><b style="font-weight:600">${m[0]}</b></div><div class="mv-muted" style="font-size:12.5px;margin-bottom:6px">${m[1]}</div><div class="mv-row" style="margin-bottom:6px"><label style="margin:0;width:90px">${m[2][0]}</label><input value="${m[2][1]=='Name'?'':m[2][1]}" maxlength="500" autocomplete="off" aria-label="${m[2][0]}" style="flex:1;min-width:160px"${D}></div><div class="mv-row">${btn('Show','Message shown','p')}${btn('Clear','Message cleared')}</div></div>`).join('')}</div>`:`<p class="mv-note" style="margin:0">No messages are prepared in ProPresenter.</p>`)}
  ${mvCard('Media position','',`<div class="mv-row"><input type="number" min="0" step="0.1" value="0" style="width:120px" aria-label="Seconds"${D}>${btn('Go to time (seconds)','Moved the media')}</div><p class="d" style="margin:10px 0 0">Moves the media playing on the presentation layer to that time.</p>`)}
  <div class="mv-dock" role="toolbar" aria-label="ProPresenter clear"><span class="dl">Clear</span>${dock.map((n,i)=>`<button type="button" class="btn${lit[dockKeys[i]]&&ok?' lit':''}"${D} title="${!ok&&view?'View-only mode is on':ok&&lit[dockKeys[i]]?'Live now: clear it':ok?'Nothing live here':''}" onclick="toast('Cleared ${n}')">${n}</button>`).join('')}</div></div>`;
}
mvPage('ppcontrol','ProPresenter control','Run the slides from here. Needs ProPresenter connected under ProPresenter; in view-only mode every button is off.',
  [['ok','Connected'],['wait','Waiting'],['off','Not set up'],['view','View-only mode'],['perm','Can view only']],ppcBody,'ok');
reg({kind:'page',id:'ppcontrol',t:'Control',g:'Output',ic:'mod_ppcontrol',module:'propresenter',perm:'propresenter.view',parent:'ProPresenter'});

/* ---------- Slide view and lobby ---------- */
function pplBody(s){
  const view=s=='view',wait=s=='wait',paused=s=='paused',idle=s=='idle',run=s=='ok'||paused;
  const man=!view;
  const D=view?' disabled':'';
  const svNote=wait?'Waiting for ProPresenter.':'';
  const sv=wait?`<p class="mv-note" style="margin:0 0 12px">Waiting for ProPresenter.</p>`
   :`<div style="display:flex;gap:10px;align-items:baseline;margin-bottom:8px;flex-wrap:wrap"><b>Great Is Thy Faithfulness</b><span class="mv-chip" style="--pp:var(--primary)">Verse</span><span class="mv-muted">slide 2 of 14</span></div>
   <div class="mv-sv"><div><div class="lb">Live</div><div class="fr">There is no shadow<br>of turning with Thee<span class="tmr"><small>Countdown</small>04:32</span><span class="med"><span>0:23 / 1:02</span><span class="b"><i></i></span></span></div></div><div><div class="lb">Next</div><div class="fr nx">Great is Thy faithfulness!<br>Great is Thy faithfulness!</div></div></div>`;
  const lbState=run?(paused?'Paused: something else is on the announcements layer. It carries on when you clear that.':'Running: Welcome to Grace (item 2 of 5, slide 1).'):'Not running.';
  const sg=s=='ok'||paused?'12 slides in the signage playlist "ProPresenter announcements", copied 10:41:08 AM.':'Off.';
  const sgOn=s=='ok'||paused;
  return `<div class="mv-stack">${wait?mvStatus('err','Not working',['Waiting for ProPresenter','Nothing in this page changes ProPresenter until it answers.']):mvStatus(run?'ok':'idle',run?(paused?'Paused':'Running'):'Standing by',[run?(paused?'Lobby rotation paused':'Lobby rotation running'):'Lobby rotation is not running','ProPresenter is connected.'])}
  ${view?mvNote('','You can view this page but not change it.'):''}
  ${view&&false?'':''}
  ${mvCard('Slide view','',`${sv}<div class="mv-form" style="margin-top:12px;max-width:420px">${mvF('Countdown on the slide',mvSel([['','No countdown'],['t1','Countdown to service'],['t2','Sermon']],'t1',{dis:view,al:'Countdown on the slide'}))}</div>
   ${mvSw(false,'Let the slide view be opened without the admin login, at <span class="mv-k">/stage</span> on the viewer address','Off by default. Slide images are licensed content, so they stay behind the admin login until you switch this on.',{dis:view})}
   ${mvFoot(`<small id="svMsg"></small>`,`<button type="button" class="btn p"${D} onclick="toast('Saved.')">Save</button>`)}`)}
  ${mvCard('Lobby announcements','Relay steps through a ProPresenter playlist by itself, one slide at a time. ProPresenter does not say which playlists are announcement playlists, so pick the one your lobby screens show. It does not start on its own: press Start. If you put something else on the announcements layer, the rotation waits and carries on when you clear it.',
   `${view?mvNote('','You can view this page but not change it.'):''}<div class="mv-form" style="max-width:520px">${mvF('Playlist',mvSel([['','Choose a playlist'],['a','Lobby Announcements'],['b','Sunday AM'],['c','Youth Night']],'a',{dis:view,al:'Playlist'}))}${mvF('Seconds per slide',mvIn(8,'',{type:'number',min:3,max:600,step:1,dis:view,al:'Seconds per slide'}))}</div>
   ${mvSw(true,'Start over when it reaches the end','',{dis:view})}
   <div class="mv-row" style="margin-top:8px"><button type="button" class="btn p"${D} onclick="toast('Saved.')">Save</button><button type="button" class="btn p"${D} onclick="toast('Rotation started')">${run?'Restart':'Start'}</button>${paused?`<button type="button" class="btn"${D} onclick="toast('Resumed')">Resume</button>`:''}<button type="button" class="btn"${D} onclick="toast('Stopped')">Stop</button><button type="button" class="btn"${D} onclick="toast('Stopped and cleared')">Stop and clear announcements</button></div>
   <div class="mv-now" style="margin-top:12px;margin-bottom:0"><div class="t${run?'':' mv-muted'}">${lbState}</div></div>
   <p class="d" style="margin:10px 0 0">${view?'View-only mode is on, so the rotation cannot change anything in ProPresenter.':''}</p>`,{cnt:run?(paused?'paused':'running'):''})}
  ${mvCard('Announcements on signage screens','Copies each slide of a ProPresenter playlist into the signage media library as a picture and keeps a signage playlist called <em>ProPresenter announcements</em> in step with it, so lobby TVs can show them without ProPresenter. Relay looks again every two minutes and after you change the playlist. Slides are licensed content, so this is off until you switch it on. The signage playlist is rewritten each time, but a slide that stays keeps its time and dates. Needs the Signage module.',
   `<div class="mv-form" style="max-width:520px">${mvF('Playlist',mvSel([['','Choose a playlist'],['a','Lobby Announcements'],['b','Sunday AM']],'a',{dis:view,al:'Playlist'}))}</div>
   ${mvSw(sgOn,'Keep the signage playlist up to date','',{dis:view})}
   <div class="mv-row" style="margin-top:8px"><button type="button" class="btn p"${D} onclick="toast('Saved.')">Save</button><button type="button" class="btn"${D} onclick="toast('Copied.')">Sync now</button></div>
   <div class="mv-now" style="margin-top:12px;margin-bottom:0"><div class="t${sgOn?'':' mv-muted'}">${sg}</div></div>`,{cnt:sgOn?'on':''})}</div>`;
}
mvPage('pplive','Slide view and lobby','The slides ProPresenter has up, as pictures with the countdown and video progress over them, and a rotation that steps a ProPresenter announcement playlist for lobby screens.',
  [['ok','Running'],['paused','Paused'],['idle','Not running'],['wait','Waiting'],['view','View only']],pplBody,'ok');
reg({kind:'page',id:'pplive',t:'Slide view and lobby',g:'Output',ic:'mod_pplive',module:'propresenter',perm:'propresenter.view',parent:'ProPresenter'});

/* ---------- ProPresenter settings ---------- */
const PL_ROWS=[['Welcome &amp; Announcements','','','',''],['Great Is Thy Faithfulness','matched','Great Is Thy Faithfulness','same title',''],['Be Thou My Vision','matched','Be Thou My Vision (Short)','similar title (88%)','unpair'],['Offering','matched','Offering Slide','paired by you','unpair'],['Sermon: Faith for the Long Haul','ambiguous','maybe: Sermon Nov 16, Sermon Nov 9','Several fit','pair'],['Communion','unmatched','','No match','pair'],['Closing Song','placeholder','','Not linked in ProPresenter','pair']];
const PL_STATUS={matched:'Matched',unmatched:'No match',ambiguous:'Several fit',placeholder:'Not linked in ProPresenter'};
function ppsBody(s){
  const perm=s=='perm',vo=s=='vo',view=perm,off=s=='off',wait=s=='wait',on=s=='ok'||perm||vo;
  const D=perm?' disabled':'';
  const man=!view;
  const stateNote=off?'Off. Enter the address and port, then switch Follow ProPresenter on.':wait?'Could not reach ProPresenter at Studio-Mac.local. Check Network is switched on in ProPresenter.':'Connected to ProPresenter 7.16 on Studio-Mac.';
  const tagLine=(t)=>`<span class="mv-k">${t}</span>`;
  const connection=mvCard('Connection','',`<div class="mv-form" style="max-width:640px">${mvF('Machine name or address',mvIn(off?'':'Studio-Mac.local','10.0.0.20 or Studio-Mac.local',{mono:1,dis:view,al:'Machine name or address'}))}${mvF('Port',mvIn(off?'':'50001','from ProPresenter',{type:'number',min:1,max:65535,dis:view,al:'Port'}))}</div>
   ${mvSw(!off,'Follow ProPresenter','',{dis:view})}
   <div class="mv-row" style="margin-top:6px"><button type="button" class="btn p"${D} onclick="toast('Saved.')">Save</button>${mvB('Test connection',wait?'Could not reach Studio-Mac.local':'Reached ProPresenter on Studio-Mac (API 7.16.0).')}<button type="button" class="btn"${D} onclick="mvShow('ppFound')">Find on this network</button></div>
   <div class="mv-row" id="ppFound" hidden style="margin-top:10px"><button type="button" class="btn sm" onclick="toast('Address filled in')">Studio-Mac · 10.0.0.20:50001</button><button type="button" class="btn sm" onclick="toast('Address filled in')">Sanctuary-iMac · 10.0.0.31:50001</button></div>
   <div class="mv-note ${on?'ok':wait?'err':''}" style="margin:12px 0 0" id="ppState">${stateNote}</div>
   <p class="d" style="margin:12px 0 0">In ProPresenter, open Settings &rarr; Network, switch <em>Enable Network</em> on, and copy the port it shows: the port is set per install, so Relay assumes none. ProPresenter's API has no password, so Relay only ever talks out to the address typed here, and anyone who can reach that port can control ProPresenter: keep it on the production network. The ProPresenter machine's firewall must let this computer reach that port.</p>`);
  const safety=mvCard('Safety','',mvSw(vo,'View-only','Relay reads ProPresenter but never changes anything in it: no stage message, no triggers, no clears. For a second computer, or a church that runs the slides from ProPresenter itself.',{dis:perm}));
  const nowC=mvCard('Now','',off?`<p class="mv-muted" style="margin:0">Not following ProPresenter.</p>`:wait?`<p class="mv-muted" style="margin:0">Waiting for ProPresenter.</p>`:
   mvKv([['Presentation','Great Is Thy Faithfulness (Songs), a song'],['Slide','Verse · #2'],['Look','Worship'],['Layers with content','slide, audio']])+
   `<div class="mv-now" style="margin-top:12px;margin-bottom:0"><div class="n" style="margin:0 0 2px">Current slide</div><div class="t" style="white-space:pre-line">There is no shadow\nof turning with Thee</div><div class="n" style="margin-top:10px">Next slide</div><div class="n" style="margin:0;color:var(--muted)">Great is Thy faithfulness!\nGreat is Thy faithfulness!</div></div>`);
  const lyrics=mvCard('Lyrics feed','',`${mvNote('','Song lyrics are licensed. The feed is off for the public phone viewer unless you switch it on, and anyone on the network who opens the link can read it when it is on.')}
   ${mvSw(true,'Screens (<span class="mv-k">/screen?lyrics=1</span>)','',{dis:view})}${mvSw(false,'Broadcast overlay (<span class="mv-k">/overlay?lyrics=1</span>)','',{dis:view})}${mvSw(true,'Caption panels','',{dis:view})}${mvSw(false,'Phone viewer (public)','',{dis:view})}${mvSw(true,'Songs only','Skip sermon and announcement slides.',{dis:view})}
   <div class="mv-form" style="max-width:360px;margin-top:8px">${mvF('Library that holds the songs',mvIn('Songs','Songs',{dis:view,al:'Library that holds the songs'}),'A presentation is a song when its file is in this library, or when its parts are named Verse, Chorus, Bridge and the like.')}</div>
   ${mvFoot('',`<button type="button" class="btn p"${D} onclick="toast('Saved.')">Save lyrics settings</button>`)}`);
  const timers=mvCard('Timers on panels','',`${mvNote('','A caption panel set to <em>Timers</em> shows ProPresenter timers large: white while one runs, amber and then red as a countdown nears zero, and red with a minus sign once it is over. The panel counts the seconds itself between Relay\'s updates. A panel can use its own settings instead: open it under Panels.')}
   <div class="mv-row" style="align-items:flex-end"><div class="fld" style="margin:0"><label>Amber at</label>${mvIn('2:00','2:00',{w:100,dis:view,al:'Amber at'})}</div><div class="fld" style="margin:0"><label>Red at</label>${mvIn('0:30','0:30',{w:100,dis:view,al:'Red at'})}</div></div>
   <p class="d" style="margin:8px 0 0">Time left, as minutes:seconds (<span class="mv-k">2:00</span>) or seconds (<span class="mv-k">90</span>). Red cannot be earlier than amber.</p>
   <div class="fld" style="margin-top:12px"><label>Timers to show</label>${on?`<div class="mv-list">${[['Countdown to service',1],['Sermon',1],['Offering',0],['Nursery check-in',0]].map(t=>`<label style="display:flex;gap:8px;align-items:center;margin:0;color:var(--fg);font-size:13px"><input type="checkbox"${t[1]?' checked':''}${D}> ${t[0]}</label>`).join('')}</div>`:`<p class="mv-muted" style="margin:0">No timers.</p>`}<p class="mv-h">Tick up to four. With none ticked, panels show the first four timers ProPresenter has.</p></div>
   ${mvFoot('',`<button type="button" class="btn p"${D} onclick="toast('Saved.')">Save timer settings</button>`)}`);
  const stage=mvCard('Stage message','',`<div class="mv-form" style="max-width:520px">${mvF('Message for the stage display',mvIn('','Sound desk needed',{dis:perm||vo||off||wait,al:'Message for the stage display'}))}</div>
   <div class="mv-row" style="margin-top:10px"><button type="button" class="btn p"${perm||vo||off||wait?' disabled':''} onclick="toast('Stage message shown')">Show</button><button type="button" class="btn" ${on&&!perm&&!vo?'':'disabled'} onclick="toast('Stage message cleared')">Clear</button></div>
   <p class="d" style="margin:10px 0 0">${vo?'View-only is on, so Relay will not change the stage display.':on?'On the stage display now: Wrap up in 2 minutes':''}</p>
   <p class="d" style="margin:8px 0 0">An alert can also appear here: tick <em>Also show on the ProPresenter stage display</em> on its button under Alerts. It is cleared when the alert is acknowledged.</p>`,{id:'ppStage'});
  const tags=mvCard('Slide-note tags','',`${mvNote('','A tag in a slide\'s notes in ProPresenter does something in Relay when that slide goes live. Relay only reads the notes, it never changes them. '+tagLine('[relay:lyrics]')+' on a slide, or '+tagLine('[relay-deck:lyrics]')+' in any slide of a presentation for every slide of it that has no tag of its own. The tags are '+['capture-start','capture-stop'].map(tagLine).join(', ')+' (Relay\'s own captioning, not ProPresenter\'s capture), '+['lyrics','captions','lang=es','alert=Button name'].map(tagLine).join(', ')+'. With the Signage module on, '+['signage-screen=Screen:Playlist','signage-group=Group:Playlist','signage-all=Playlist'].map(tagLine).join(', ')+' switch what screens play; '+['signage-push','signage-captions','signage-service','signage-resume'].map(tagLine).join(', ')+' (each also as <span class="mv-k">-screen</span> and <span class="mv-k">-group</span>) take a screen over and give it back. The guide has the full list.')}
   ${mvSw(on,'Run slide-note tags','Off until you switch it on. Needs Follow ProPresenter, above.',{dis:view})}
   <div class="mv-form" style="max-width:240px;margin-top:6px">${mvF('Settle time (seconds)',mvIn(1.5,'',{type:'number',min:.2,max:30,step:.1,dis:view,al:'Settle time (seconds)'}),'A slide\'s tags run only after it has stayed live this long, so a fast run through the slides runs one set, not all of them. Moving to a slide with the same tags runs nothing again.')}</div>
   <div class="mv-row" style="margin-top:6px"><button type="button" class="btn p"${D} onclick="toast('Saved.')">Save tag settings</button></div>
   <div style="margin-top:14px">${on?mvKv([['Slide up','Be Thou My Vision, slide 3'],['Tags on the slide','lyrics, alert=Nursery'],['Deck-wide tags','none'],['Runs for this slide','lyrics, alert=Nursery'],['Warning','Unknown tag "lang=xx" was ignored.','err']])+`<p class="d" style="margin:12px 0 6px">On signage screens because of tags</p>${mvKv([['Lobby TV','plays Welcome loop']])}<p class="d" style="margin:12px 0 6px">What ran</p>${mvKv([['10:42:07 AM  lyrics','done'],['10:41:30 AM  alert=Nursery','alert raised'],['10:36:12 AM  capture-start','Captioning started'],['10:35:50 AM  signage-screen=Lobby TV:Welcome','Screen not found','err']])}`:`<p class="mv-muted" style="margin:0">Slide-note tags are off.</p>`}</div>`);
  const pcOn=on;
  const link=mvCard('Planning Center link','',`${mvNote('','Matches the plan\'s items to ProPresenter\'s presentations, shows both sides while a service runs, and can let ProPresenter move Planning Center\'s LIVE item. Both switches are off until you turn them on. Needs ProPresenter and Planning Center connected.')}
   ${mvSw(on,'Follow the plan','Read ProPresenter\'s lists in the background, match them to the plan, and tell panels which item ProPresenter has up.',{dis:view})}
   ${mvSw(false,'ProPresenter drives Services LIVE','Once a presentation has been up for the time below, Relay moves Planning Center to the matching item. It never takes LIVE control: it moves LIVE only while Relay\'s own Planning Center token already holds it.',{dis:view})}
   <div class="mv-form" style="max-width:520px;margin:6px 0">${mvF('Stay up for (seconds)',mvIn(8,'',{type:'number',min:1,max:300,step:1,dis:view,al:'Stay up for (seconds)'}))}${mvF('Service type',mvSel([['','The first with a plan today'],['s1','Sunday Services'],['s2','Youth Night']],'',{dis:view,al:'Service type'}))}</div>
   ${mvSw(false,'Also go back','When ProPresenter shows an item before Planning Center\'s, step back to it (never further). Off: only forward.',{dis:view})}
   <div class="mv-row" style="margin-top:6px"><button type="button" class="btn p"${D} onclick="toast('Saved.')">Save link settings</button></div>
   ${pcOn?`<div style="margin-top:14px">${mvKv([['Plan','Sunday Worship, Nov 16'],['ProPresenter moving Planning Center','Off. Switch on ProPresenter drives Services LIVE to use it.'],['ProPresenter has up','Be Thou My Vision, which is "Be Thou My Vision" in the plan'],['Planning Center is on','Be Thou My Vision (LIVE held by Dana Reyes)'],['They agree','Both are on the same item.','ok']])}<p class="d" style="margin:12px 0 6px">Recent moves</p>${mvKv([['10:38:04 AM','Moved Planning Center to Be Thou My Vision'],['10:34:51 AM','Moved Planning Center to Great Is Thy Faithfulness']])}</div>`:`<div style="margin-top:14px">${mvKv([['Planning Center','not connected','err'],['ProPresenter','not connected','err']])}</div>`}
   <div class="mv-row" style="margin-top:12px"><button type="button" class="btn"${pcOn&&!perm?'':' disabled'} onclick="toast('Matched.')">Match the plan now</button><button type="button" class="btn"${pcOn&&!perm&&!vo?'':' disabled'} onclick="toast('Playlist built')">Build the Relay playlist</button></div>
   <p class="d" style="margin:10px 0 0">${vo?'View-only is on, so Relay will not create or change a playlist.':pcOn?'Makes (or remakes) one playlist named "Relay: Sunday Worship" in the plan\'s order. Relay only ever changes a playlist it made itself; your own playlists are never touched.':''}</p>
   <div class="mv-over mv-plan" style="margin-top:10px"><table class="dt"><thead><tr><th>Plan item</th><th>Status</th><th>ProPresenter presentation</th><th>How</th><th></th></tr></thead><tbody>
   ${pcOn?`<tr><td colspan="5" class="mv-muted">4 matched, 3 need attention. Read 212 presentations from 9 playlists; the synced playlist is "Sunday AM".</td></tr>`+PL_ROWS.slice(1).map((r,i)=>`<tr${r[1]=='unmatched'||r[1]=='ambiguous'||r[1]=='placeholder'?' class="mv-bad"':''}><td>${r[0]}${i==1?'  (live in Planning Center)':''}</td><td>${PL_STATUS[r[1]]}</td><td>${r[2]}</td><td>${r[3]}</td><td>${r[4]=='unpair'?`<button type="button" class="btn sm"${D} onclick="toast('Pairing removed.')">Unpair</button>`:r[4]=='pair'?`<span class="mv-row"><input list="plkNames" placeholder="Presentation name" style="width:170px" aria-label="Presentation for ${esc(r[0])}"${D}><button type="button" class="btn p sm"${D} onclick="toast('Paired.')">Pair</button></span>`:''}</td></tr>`).join(''):`<tr><td colspan="5" class="mv-muted">Not matched yet. Turn on Follow the plan, or press Match the plan now.</td></tr>`}</tbody></table></div>`,{id:'ppLink'});
  return `<div class="mv-stack">${ppStatusRow(s,vo)}${perm?VIEWNOTE:''}${connection}${safety}${nowC}${lyrics}${timers}${stage}${tags}${link}</div>`;
}
mvPage('propresenter','ProPresenter','Follow the slide that is up, show lyrics on screens and panels, and put a message on the stage display.',
  [['ok','Connected'],['wait','Not working'],['off','Not set up'],['vo','View-only mode'],['perm','Can view only']],ppsBody,'ok');
reg({kind:'page',id:'propresenter',t:'Settings',g:'Output',ic:'mod_propresenter',module:'propresenter',perm:'propresenter.view',parent:'ProPresenter'});

/* =====================================================================================================
   Planning Center  (app/modules/planning_center, admin.html, planningcenter.js, pcrundown.js, pcsignage.js;
   AdminPage.id is planningcenter)
   ===================================================================================================== */
const PC_ITEMS=[['9:55','9:55','Welcome and announcements','','','4:00','done'],['10:00','10:00','Great Is Thy Faithfulness (G)','Dana Reyes','03 Dana','4:30','done'],['10:05','10:05','Be Thou My Vision (D)','Dana Reyes','03 Dana, 04 Marcus','4:15','cur'],['10:10','10:11 ','Offering','','','3:00',''],['10:13','10:14','Children\'s blessing','','','2:00',''],['10:15','10:16','Sermon: Faith for the Long Haul','Pastor Elena Ortiz','07 Elena','30:00',''],['10:45','10:46','Communion','','','6:00',''],['10:51','10:52','Closing song: It Is Well (Bb)','Dana Reyes','03 Dana, 04 Marcus, 05 Priya','5:00','']];
const PC_MICS=[['Worship','Worship Leader','03','1','','Dana Reyes → 03'],['Worship','Vocals','04, 05, 06','1','','Marcus → 04 · Priya → 05 · spare'],['Teaching','Pastor','07','0','Headset','Elena Ortiz → 07'],['Any team','Host','08','0','Announcements','Sam Lee → 08']];
function pcBody(s){
  const view=s=='view',off=s=='off',err=s=='err',on=!off,perm=!view;
  const D=view?' disabled':'';
  const st=off?mvStatus('warn','Not set up',['No token saved','Until a token is saved, Relay does not contact Planning Center at all.'])
   :err?mvStatus('err','Not working',['Planning Center could not be read.','Check the token has not been revoked. Relay tries again by itself.'],mvB('Test connection','Planning Center could not be read.','sm'))
   :mvStatus('ok','Connected',['Connected, 2 service types followed','Sunday Services is LIVE. Dana Reyes is controlling.'],mvB('Test connection','Connected to Planning Center.','sm'));
  const conn=mvCard('Connection','',`<div class="mv-form" style="max-width:640px">${mvF('Application ID','<input type="password" placeholder="(leave blank to keep current)" autocomplete="off" aria-label="Application ID"'+D+'>')}${mvF('Secret','<input type="password" placeholder="(leave blank to keep current)" autocomplete="off" aria-label="Secret"'+D+'>')}</div>
   <div class="mv-row" style="margin-top:12px"><button type="button" class="btn p"${D} onclick="toast('Saved. Choose the service types to follow below.')">Save</button>${mvB('Test connection',err?'Planning Center could not be read.':on?'Connected to Planning Center.':'Enter an application ID and secret first.')}${on&&perm?mvB('Disconnect','Disconnected. Relay no longer contacts Planning Center.'):''}</div>
   <div class="mv-note ${err?'err':on?'ok':''}" style="margin:12px 0 0">${err?'Planning Center could not be read.':on?'Connected. The service type list refreshes from Planning Center every minute.':'Not connected.'}</div>
   <p class="d" style="margin:12px 0 0">Make a Personal Access Token at <span class="mv-k">api.planningcenteronline.com/personal_access_tokens</span> and paste its application ID and secret here. Everything Relay does in Planning Center, including Next and Previous from a panel, runs as the person who made the token and only within their permissions. The secret is stored server-side in <span class="mv-k">config.json</span>, never shown again, and never written to the log, an export or a recording. Until a token is saved, Relay does not contact Planning Center at all.</p>`);
  if(off)return `<div class="mv-stack">${st}${conn}</div>`;
  const types=mvCard('Service types Relay follows','',`<div class="mv-list">${[['Sunday Services',1],['Youth Night',1],['Wednesday Prayer',0]].map(t=>`<label style="display:flex;gap:10px;align-items:center;margin:0;color:var(--fg);font-size:14px"><input type="checkbox"${t[1]?' checked':''}${D}> ${t[0]}</label>`).join('')}</div>
   ${mvFoot('<small>2 followed.</small>',`<button type="button" class="btn p" disabled>Save selection</button>`)}`);
  const display=mvCard('Display','',mvSw(false,'Show item notes on panels','Off by default: notes can hold names and pastoral details, and anyone on the network can open the Service feed.',{dis:view}));
  const today=mvCard('Today','',`<div class="mv-list">${[['Sunday Services','Nov 16, 2026 · Live · Dana Reyes is controlling · Be Thou My Vision · 3:20 left · service 1:10 behind · next: Offering · checked 4 s ago',['Relay\'s pick for today','Nov 16, 2026 · Sunday Worship','Nov 23, 2026 · Sunday Worship']],['Youth Night','No plan with a service time today.',['Relay\'s pick for today','Nov 19, 2026 · Youth Night']]].map(t=>`<div><div class="mv-row" style="margin-bottom:6px"><b style="font-weight:600;min-width:140px">${t[0]}</b>${mvSel(t[2],t[2][0],{al:'Plan followed today for '+t[0],w:300,dis:view})}</div><div class="mv-muted" style="font-size:13px">${t[1]}</div></div>`).join('')}</div>
   <p class="d" style="margin:12px 0 0">Day boundary: America/Chicago (from Planning Center)</p>
   <h4 class="mv-sub" style="margin-top:20px">Presses from panels</h4>
   <div class="mv-list">${[['10:41:12 AM · Sound booth tablet · Next · done',''],['10:38:03 AM · Sound booth tablet · Next · done',''],['10:36:40 AM · Pastor\'s phone · Next · Relay does not have control','err'],['10:12:09 AM · Sound booth tablet · Take control · done','']].map(r=>`<div class="mv-note ${r[1]?'err':'ok'}" style="margin:0;padding:6px 10px">${r[0]}</div>`).join('')}</div>`,{id:'pcToday'});
  const rd=mvCard('Rundown','',`<div class="mv-row" style="align-items:flex-end;margin-bottom:12px"><div class="fld" style="margin:0"><label>Service type</label>${mvSel(['Sunday Services','Youth Night'],'Sunday Services',{dis:view,al:'Service type'})}</div><div class="fld" style="margin:0"><label>Service time</label>${mvSel(['Automatic (10:30)','9:00','10:30'],'Automatic (10:30)',{dis:view,al:'Service time'})}</div></div>
   <div class="mv-row"><span class="badge ok">LIVE · Be Thou My Vision · run by Dana Reyes</span>${mvB('Previous','Previous item','sm').replace('<button','<button'+D)}${mvB('Next','Next item','p sm').replace('<button','<button'+D)}${mvB('Take control','Took control','sm').replace('<button','<button'+D)}${mvB('Release','Released control','sm').replace('<button','<button'+D)}</div>
   <p class="d" style="margin:10px 0 12px">Same rules as a panel's press: Relay presses as the person who made the token, Next and Previous work only while that person has control, and every press is listed under "Presses".</p>
   <div class="mv-over mv-plan"><table class="dt"><thead><tr><th>Planned</th><th>Actual / proj.</th><th>Item</th><th>Leader</th><th>Mics</th><th>Length</th></tr></thead><tbody>${PC_ITEMS.map(i=>`<tr class="${i[6]}"><td class="mv-m">${i[0]}</td><td class="mv-m"${i[1].trim()!=i[0]&&i[6]!='done'?' style="color:var(--warn)"':''}>${i[1].trim()}${i[6]=='cur'?' ▶':''}</td><td>${i[2]}</td><td>${i[3]}</td><td class="mv-m">${i[4]}</td><td class="mv-m">${i[5]}</td></tr>`).join('')}</tbody></table></div>
   <p class="d" style="margin:10px 0 0">Planned times come from the service time and item lengths. Once LIVE starts, everything after the current item is re-projected from when it really started. Planned end 10:56, projected 10:57.</p>`,{id:'rdBox'});
  const mics=mvCard('Mics by position','Give a position its mics. A position with more than one confirmed person gets one mic per person, drawn at random once per plan and service time and then kept: it changes only when someone confirms or drops out, or when you press Redraw. People past the last mic get none. "Sings" lights the mic on /screen during songs; untick it for a speaking mic (a host or a pastor\'s headset), which is lit for everything else.',
   `<div class="mv-over mv-plan"><table class="dt"><thead><tr><th>Team</th><th>Position</th><th>Mics</th><th>Sings</th><th>Label</th><th>This service</th><th></th></tr></thead><tbody>${PC_MICS.map(m=>`<tr><td>${m[0]}</td><td>${m[1]}</td><td><input value="${m[2]}" placeholder="03, 04" style="width:130px" aria-label="Mics for ${m[1]}"${D}></td><td><input type="checkbox"${m[3]=='1'?' checked':''}${D} aria-label="Sings"></td><td><input value="${m[4]}" placeholder="e.g. Announcements" style="width:160px" aria-label="Label"${D}></td><td>${m[5]}</td><td>${perm?`<span class="mv-row">${mvB('Redraw','Drawn again for '+m[1],'sm')}${mvB('Remove','Position removed','sm')}</span>`:''}</td></tr>`).join('')}</tbody></table></div>
   ${perm?`<div class="mv-row" style="margin-top:12px">${mvSel(['Pick a team and position…','Worship · Keys','Worship · Drums','Tech · Sound'],'Pick a team and position…',{al:'Position'})}${mvB('Add position','Position added')}<button type="button" class="btn p" disabled>Save mics</button>${mvB('Redraw all','Drawn again.')}</div>`:''}`,{id:'rdMicBox'});
  const scr=mvCard('Rundown on /screen','',`${mvSw(true,'Show the rundown on /screen','Off by default. It shows volunteers\' names, photos, mics and sermon notes, on this network only: Relay refuses it through remote access, and the panels and audience caption pages never carry any of it.',{dis:view})}
   <div class="mv-form" style="margin-top:6px">${mvF('Song leader: item note',mvIn('Leader','Leader',{dis:view,al:'Song leader: item note'}))}${mvF('...or else this position',mvIn('Worship Leader','Worship Leader',{dis:view,al:'Or else this position'}))}${mvF('Sermon notes: item note',mvIn('Sermon Notes','Sermon Notes',{dis:view,al:'Sermon notes: item note'}))}</div>
   ${mvFoot('',`<button type="button" class="btn p"${D} onclick="toast('Saved. The rundown is on for /screen.')">Save</button>`)}
   <h4 class="mv-sub" style="margin-top:16px">Link for a screen</h4>
   <div class="mv-form">${mvF('View',mvSel([['full','Full rundown (replaces captions)'],['side','Beside the captions'],['micboard','Micboard']],'side',{al:'View'}))}${mvF('Order of service',mvSel([['fit','Every item, fitted to the screen'],['compact','Now and the next three']],'fit',{al:'Order of service'}))}${mvF('People',mvSel([['person','One per person'],['position','One per position']],'person',{al:'People'}))}${mvF('Teams',mvSel(['All teams','Worship','Tech'],'All teams',{al:'Teams'}))}${mvF('Service time',mvSel(['Automatic','9:00','10:30'],'Automatic',{al:'Service time'}))}</div>
   <div class="mv-row" style="margin-top:12px"><span class="mv-m" style="flex:1;min-width:240px">/screen?rundown=side&amp;layout=fit&amp;cards=person</span>${mvB('Copy','Copied.','sm')}</div>
   <p class="d" style="margin:10px 0 0">/screen is display-only: no scrolling and no buttons. Everything fits one screen; long notes and rosters shrink to fit. Move LIVE from this page or from a panel.</p>`,{id:'rdSetBox'});
  const sg=mvCard('Plan files on signage screens','Copies the pictures and videos attached to the current or next plan, and to its items, into the signage media library, and can keep a signage playlist called <em>Planning Center plan</em> in step with them. Relay looks again every five minutes and when you press Sync now. A file is downloaded once and again only when it changes in Planning Center. Off until you switch it on. Needs the Signage module.',
   `<div class="mv-form" style="max-width:640px">${mvF('Service type',mvSel([['','Pick a service type'],['s1','Sunday Services'],['s2','Youth Night']],'s1',{dis:view,al:'Service type'}))}${mvF('Only plan items whose title has',mvIn('Announcements','Announcements',{dis:view,al:'Only plan items whose title has'}))}</div>
   ${mvSw(true,'Files attached to plan items','',{dis:view})}${mvSw(false,'Files attached to the plan itself','Left out while a plan item word is set.',{dis:view})}${mvSw(true,'A text slide for each plan item','The item\'s title, with the item note in the category named below. Notes can be private, so name one only if it is meant for screens.',{dis:view})}
   <div class="mv-form" style="max-width:320px;margin:6px 0">${mvF('Note category for text slides',mvIn('Announcement','Announcement',{dis:view,al:'Note category for text slides'}))}</div>
   ${mvSw(true,'Keep the signage playlist up to date','Rewritten each time; an item that stays keeps its time and dates.',{dis:view})}${mvSw(true,'Copy plan files into signage','',{dis:view})}
   ${mvFoot('<small>Plan "Sunday Worship": 3 files, 4 text slides, copied 10:40:51 AM.</small>',`<button type="button" class="btn"${D} onclick="toast('Copied.')">Sync now</button><button type="button" class="btn p"${D} onclick="toast('Saved.')">Save</button>`)}`,{cnt:'on',id:'sgBox'});
  return `<div class="mv-stack">${st}${view?VIEWNOTE:''}${conn}${err?'':types+display+today+rd+mics+scr+sg}</div>`;
}
mvPage('planningcenter','Planning Center','Follow the service plan and show it on panels.',
  [['ok','Connected'],['err','Error'],['off','Not connected'],['view','View only']],pcBody,'ok');
reg({kind:'page',id:'planningcenter',t:'Planning Center',g:'Automation',ic:'mod_planningcenter',module:'planning_center',perm:'planning_center.view'});

})();
