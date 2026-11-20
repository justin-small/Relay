/* Core Live + Output pages: health, audio, targets, panels, alerts, urls, overlay, recordings.
   Mockup only: nothing here needs a server. Real labels, ids and notes come from app/templates/admin.html and
   app/static/{admin,overlay-builder}.js; the spec twin is docs/redesign/screens/core-live-output.md.
   Extra CSS is scoped with the clo- prefix. The only literal colours are inside the overlay preview stage (it draws
   real output colours: chroma green, key matte, black) and are named there. */
(function () {
'use strict';

reg({kind:'page', id:'health',     t:'Session health',      g:'Live',   ic:'pulse', perm:'session.view'});
reg({kind:'page', id:'audio',      t:'Audio input',         g:'Live',   ic:'mic',   perm:'audio.manage'});
reg({kind:'page', id:'targets',    t:'Target languages',    g:'Live',   ic:'lang',  perm:'targets.manage'});
reg({kind:'page', id:'panels',     t:'Panels',              g:'Output', ic:'panel', perm:'panels.view'});
reg({kind:'page', id:'alerts',     t:'Alerts',              g:'Output', ic:'bell',  perm:'alerts.view'});
reg({kind:'page', id:'urls',       t:'Viewer links',        g:'Output', ic:'link',  perm:'session.view'});
reg({kind:'page', id:'overlay',    t:'Broadcast overlay',   g:'Output', ic:'ovl',   perm:'overlays.view'});
reg({kind:'page', id:'recordings', t:'Recordings & export', g:'Output', ic:'rec',   perm:'recordings.view'});

Object.assign(I, {
  cloCopy:'<rect x="5.5" y="5.5" width="8" height="8" rx="1.5"/><path d="M10.5 5.5v-2a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2"/>',
  cloRefresh:'<path d="M13 3.5V7H9.5M3 12.5V9h3.5"/><path d="M12.5 7A5 5 0 0 0 4 5.5M3.5 9A5 5 0 0 0 12 10.5"/>',
  cloDl:'<path d="M8 1.5v8M5 6.5l3 3 3-3M2.5 11v2a1.5 1.5 0 0 0 1.5 1.5h8a1.5 1.5 0 0 0 1.5-1.5v-2"/>',
  cloUp:'<path d="M8 10.5v-8M5 5.5l3-3 3 3M2.5 11v2a1.5 1.5 0 0 0 1.5 1.5h8a1.5 1.5 0 0 0 1.5-1.5v-2"/>',
  cloInfo:'<circle cx="8" cy="8" r="6"/><path d="M8 7.2V11M8 5v.2"/>',
  cloTrash:'<path d="M3 4.5h10M6.5 4.5v-2h3v2M4.5 4.5l.5 9h6l.5-9"/>',
  cloOpen:'<path d="M9 2.5h4.5V7M13.5 2.5L7.5 8.5M12 9.5v3a1 1 0 0 1-1 1h-7a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1h3"/>'
});

/* ------------------------------------------------------------------ shared state and helpers */
const C = {
  vo: false,                 // demo: the signed-in role may view but not change
  tab: {},                   // remembered tab per page
  mode: 'text',              // text translation is the only engine (#496)
  on: new Set(['SPANISH', 'PORTUGUESE', 'KOREAN']),
  feed: '', aud: null, audDev: 'x32', audNone: false, noise: '',
  hist: 'rows', cert: false, rec: null, ov: null, al: null, drawer: null
};
const HOST = 'http://192.168.1.40';
const SECURE = 'https://relay.local';
const LANGS = [['SPANISH','Spanish','es'],['PORTUGUESE','Portuguese','pt'],['FRENCH','French','fr'],['GERMAN','German','de'],['ITALIAN','Italian','it'],['RUSSIAN','Russian','ru'],['CHINESE','Chinese','zh'],['JAPANESE','Japanese','ja'],['KOREAN','Korean','ko'],['HINDI','Hindi','hi'],['INDONESIAN','Indonesian','id'],['VIETNAMESE','Vietnamese','vi'],['ARABIC','Arabic','ar',1],['NEPALI','Nepali','ne',1],['TELUGU','Telugu','te',1],['HMONG','Hmong','hmn',1]];
const lname = t => (LANGS.find(l => l[0] === t) || [0, t])[1];
const NATIVE = {en:'English', es:'Español', pt:'Português', fr:'Français', de:'Deutsch', ko:'한국어', zh:'中文', vi:'Tiếng Việt'};
const SAMP = {
  en:['Good morning, church. It is so good to be together.','Open your Bibles with me to John chapter fifteen.','Jesus says, I am the vine and you are the branches.','Whoever abides in me bears much fruit.'],
  es:['Buenos días, iglesia. Qué bueno estar juntos.','Abran sus Biblias conmigo en Juan capítulo quince.','Jesús dice: yo soy la vid y ustedes los pámpanos.','El que permanece en mí da mucho fruto.'],
  pt:['Bom dia, igreja. Que bom estarmos juntos.','Abram suas Bíblias comigo em João capítulo quinze.','Jesus diz: eu sou a videira, vocês são os ramos.','Quem permanece em mim dá muito fruto.'],
  fr:['Bonjour, église. Quelle joie d’être ensemble.','Ouvrez vos Bibles avec moi à Jean chapitre quinze.','Jésus dit : je suis le cep, vous êtes les sarments.','Celui qui demeure en moi porte beaucoup de fruit.'],
  de:['Guten Morgen, Gemeinde. Wie schön, dass wir zusammen sind.','Schlagt mit mir Johannes Kapitel fünfzehn auf.','Jesus sagt: Ich bin der Weinstock, ihr seid die Reben.','Wer in mir bleibt, bringt viel Frucht.'],
  ko:['좋은 아침입니다, 여러분. 함께하게 되어 기쁩니다.','요한복음 15장을 함께 펴 주십시오.','예수님은 나는 포도나무요 너희는 가지라고 하십니다.','내 안에 거하는 자는 열매를 많이 맺습니다.'],
  zh:['弟兄姊妹早上好，很高兴我们聚在一起。','请和我一起翻开约翰福音第十五章。','耶稣说：我是葡萄树，你们是枝子。','常在我里面的，就多结果子。'],
  vi:['Chào buổi sáng, Hội Thánh. Thật vui khi được ở bên nhau.','Hãy mở Kinh Thánh cùng tôi đến Giăng chương mười lăm.','Chúa Giê-xu phán: Ta là cây nho, các ngươi là nhánh.','Ai ở trong Ta thì kết nhiều quả.']
};
const samp = iso => SAMP[iso] || SAMP.en;
const isoOf = t => (LANGS.find(l => l[0] === t) || [0, 0, 'en'])[2];

const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const q = s => document.querySelector(s);
const D = () => C.vo ? ' disabled' : '';
const textOnly = t => (LANGS.find(l => l[0] === t) || [])[3];
const isLive = t => live && C.on.has(t) && (C.mode === 'text' || !textOnly(t));

/* The page head: title, one line, and the small demo switch for the view-only role. */
function head(title, sub, o) {
  o = o || {};
  const demo = o.demo || 'Demo: view-only role';
  return `<h2 class="t">${title}${o.sum ? ` <span class="clo-sum" id="${o.sumId}">${o.sum}</span>` : ''}</h2>
  <p class="sub clo-sr"><span>${sub}</span>${o.nodemo ? '' : `<label class="clo-demo">${demo}<button type="button" class="sw${C.vo ? ' on' : ''}" role="switch" aria-checked="${C.vo}" data-clo-vo aria-label="${demo}"></button></label>`}</p>
  ${o.vo === false ? '' : (C.vo ? `<p class="clo-vo" role="note">${ic('cloInfo')}You can view this page but not change it.</p>` : '')}`;
}
const card = (h, d, body, o) => { o = o || {}; return `<div class="card${o.cls ? ' ' + o.cls : ''}"${o.id ? ` id="${o.id}"` : ''}><div class="clo-ch"><div><h3>${h}</h3>${d ? `<p class="d">${d}</p>` : ''}</div>${o.right || ''}</div>${body}</div>`; };
const note = (t, cls, id) => `<p class="clo-n${cls ? ' ' + cls : ''}"${id ? ` id="${id}"` : ''}>${t}</p>`;
const st = (cls, t) => `<span class="clo-st ${cls}"><i></i>${t}</span>`;
const tabsBar = (page, list, extra) => `<div class="clo-tabrow"><div class="tabs" data-clo-tabs="${page}" role="tablist">${list.map(([k, l, n]) => `<button type="button" role="tab" data-t="${k}" class="${(C.tab[page] || list[0][0]) === k ? 'on' : ''}">${l}${n != null ? `<span class="clo-cnt" data-cnt="${k}">${n}</span>` : ''}</button>`).join('')}</div>${extra || ''}</div>`;
const pane = (page, k, body, first) => `<div data-clo-pane="${page}" data-t="${k}"${(C.tab[page] || first) === k ? '' : ' hidden'}>${body}</div>`;
const copyBtn = (text, label) => `<button type="button" class="btn sm" data-clo-act="copy" data-v="${esc(text)}">${ic('cloCopy')}${label || 'Copy'}</button>`;

/* ------------------------------------------------------------------ delegated handlers (one set for every page) */
const ACT = {}, IN = {};
function closeDrawer() { const d = $('drawer'); if (!d) return; C.drawer = null; d.classList.remove('in'); const b = $('drawerBg'); b && b.remove(); setTimeout(() => d.remove(), 180); }
document.addEventListener('click', e => {
  const a = e.target.closest('[data-clo-act]');
  if (a) { e.stopPropagation(); if (a.tagName === 'A') e.preventDefault(); if (!a.disabled && ACT[a.dataset.cloAct]) ACT[a.dataset.cloAct](a, e); return; }
  const v = e.target.closest('[data-clo-vo]');
  if (v) { C.vo = !C.vo; closeDrawer(); route(); return; }
  const t = e.target.closest('[data-clo-tabs] button');
  if (t) {
    const page = t.parentElement.dataset.cloTabs; C.tab[page] = t.dataset.t;
    [...t.parentElement.children].forEach(b => { b.classList.toggle('on', b === t); b.setAttribute('aria-selected', b === t); });
    document.querySelectorAll(`[data-clo-pane="${page}"]`).forEach(p => p.hidden = p.dataset.t !== t.dataset.t);
    if (page === 'overlay') ovPaint();
  }
}, false);
['input', 'change'].forEach(ev => document.addEventListener(ev, e => { const el = e.target.closest && e.target.closest('[data-clo-in]'); if (el && IN[el.dataset.cloIn]) IN[el.dataset.cloIn](el, e); }));
ACT.copy = (b) => {
  try { navigator.clipboard && navigator.clipboard.writeText(b.dataset.v); } catch (x) { /* the text stays selectable */ }
  const was = b.innerHTML; b.textContent = 'Copied'; b.classList.add('ok'); setTimeout(() => { b.innerHTML = was; b.classList.remove('ok'); }, 1200);
};
ACT.toast = b => toast(b.dataset.msg);
ACT.sw = b => { const on = !b.classList.contains('on'); b.classList.toggle('on', on); b.setAttribute('aria-checked', on); if (b.dataset.key) C.rec && (C.rec[b.dataset.key] = on); };
addEventListener('keydown', e => { if (e.key === 'Escape' && C.drawer) closeDrawer(); });
addEventListener('hashchange', () => { if (location.hash !== '#panels') closeDrawer(); });

/* ------------------------------------------------------------------ scoped CSS */
injectCSS('clo', `
#pages [hidden],#drawer [hidden]{display:none!important}
#pages .btn,#drawer .btn{display:inline-flex;align-items:center;justify-content:center;gap:6px}#pages .btn:disabled,#drawer .btn:disabled{opacity:.45;pointer-events:none}
.clo-sr{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}
.clo-demo{display:inline-flex;align-items:center;gap:8px;margin:0;font-size:12px;color:var(--muted);cursor:pointer}
.clo-vo{display:flex;gap:8px;align-items:center;margin:-6px 0 14px;padding:8px 12px;border:1px solid var(--border);border-radius:calc(var(--r) - 2px);background:var(--hover);color:var(--hover-fg);font-size:13px}
.clo-sum{font-size:13px;font-weight:500;color:var(--muted);margin-left:6px;letter-spacing:0}.clo-sum .live{color:var(--ok)}
.clo-ch{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}.clo-ch .d{margin-bottom:12px}
.clo-n{color:var(--muted);font-size:12px;line-height:1.55;margin:8px 0 0}.clo-n.err{color:var(--err)}.clo-ch+.clo-n,.clo-n:first-child{margin-top:0}.clo-n b,.clo-n strong{color:var(--fg);font-weight:600}
.clo-mut{color:var(--muted)}.clo-mono{font:12px ui-monospace,SFMono-Regular,Menlo,monospace}.clo-ok{color:var(--ok)}.clo-warn{color:var(--warn)}.clo-err{color:var(--err)}
.clo-st{display:inline-flex;align-items:center;gap:7px;white-space:nowrap}.clo-st i{width:8px;height:8px;border-radius:50%;background:var(--muted)}
.clo-st.ok i{background:var(--ok)}.clo-st.warn i{background:var(--warn)}.clo-st.err i{background:var(--err)}
.clo-f{margin-bottom:14px}.clo-f>label,.clo-l{display:block;font-size:12px;color:var(--muted);margin:0 0 5px}
.clo-f select,.clo-f input[type=text],.clo-f input[type=number]{max-width:100%}
.clo-chk{display:flex;gap:8px;align-items:flex-start;margin:0 0 8px;color:var(--fg);font-size:13px;cursor:pointer}
.clo-chk input{width:auto;margin:3px 0 0;flex:none;accent-color:var(--primary)}.clo-chk small{display:block;color:var(--muted);font-size:12px;margin-top:1px}
.clo-chk.dim{color:var(--muted)}
input[type=radio],input[type=file]{width:auto;padding:0;border:0;background:none}
.clo-tabrow{display:flex;align-items:flex-start;gap:12px;flex-wrap:wrap;justify-content:space-between}
.clo-tabrow .tabs{max-width:100%;overflow-x:auto}
.clo-cnt{display:inline-block;margin-left:7px;min-width:18px;padding:0 5px;border-radius:99px;background:var(--hover);color:var(--hover-fg);font-size:11px;line-height:17px}
.clo-sg{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:14px}
@media(max-width:1000px){.clo-sg{grid-template-columns:repeat(2,1fr)}}
.clo-g{display:grid;gap:14px;align-items:start;margin-bottom:14px}.clo-g.a{grid-template-columns:minmax(0,1fr) minmax(0,2fr)}.clo-g.b{grid-template-columns:minmax(0,2fr) minmax(0,1fr)}.clo-g.c{grid-template-columns:minmax(330px,430px) minmax(0,1fr)}
@media(max-width:1000px){.clo-g.a,.clo-g.b,.clo-g.c{grid-template-columns:1fr}}
.clo-sc{overflow:auto}
.clo-t td,.clo-t th{white-space:nowrap}.clo-t td.w{white-space:normal}.clo-tw td{white-space:normal;word-break:break-word}
/* health */
.clo-big{display:flex;align-items:center;gap:14px;flex-wrap:wrap}.clo-big .v{font-size:20px;font-weight:700;letter-spacing:-.01em}
.clo-feedbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px}.clo-feedbar select{flex:1;min-width:180px;width:auto}
.clo-feedbar .clo-demo{color:var(--fg);font-size:13px}.clo-feedbar input{width:auto}
.clo-feed{height:300px;overflow:auto;border:1px solid var(--border);border-radius:calc(var(--r) - 2px);background:var(--bg);padding:12px 14px;font-size:14px;line-height:1.5}
.clo-feed p{margin:0 0 8px}.clo-feed .open{color:var(--muted)}.clo-feed .empty{color:var(--muted);text-align:center;padding:90px 0}
/* audio */
.clo-pill{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--border);border-radius:99px;padding:4px 12px;font-size:12.5px;font-weight:600;background:var(--bg)}
.clo-dot{width:8px;height:8px;border-radius:50%;background:var(--muted)}.clo-dot.ok{background:var(--ok)}.clo-dot.warn{background:var(--warn)}.clo-dot.err{background:var(--err)}
.clo-mw{display:flex;gap:16px;align-items:flex-start;margin-top:14px}.clo-mc{flex:1;min-width:0}
.clo-meter{position:relative;height:18px;border-radius:9px;background:var(--hover);overflow:hidden;margin:0}
.clo-meter i{display:block;height:100%;width:0;background:var(--primary);transition:width .2s}.clo-meter.hot i{background:var(--warn)}.clo-meter.clipping i{background:var(--err)}
.clo-meter b{position:absolute;top:0;bottom:0;width:2px;background:var(--fg);transition:left .2s}
.clo-scale{position:relative;display:block;height:16px;margin-top:6px;font-size:11px;color:var(--muted)}.clo-scale span{position:absolute;white-space:nowrap}
.clo-ms{display:flex;flex-direction:column;align-items:flex-end;gap:6px;min-width:150px}
.clo-clip{font-size:11px;font-weight:700;letter-spacing:.04em;padding:2px 8px;border-radius:5px;border:1px solid var(--border);color:var(--muted)}.clo-clip.on{background:var(--err);border-color:var(--err);color:var(--bg)}
.clo-read{font:13px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--fg);white-space:nowrap}.clo-read.clipped{color:var(--err)}
/* targets */
.clo-lg{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:8px}
.clo-lg label{display:flex;align-items:center;gap:10px;margin:0;padding:10px 12px;border:1px solid var(--border);border-radius:calc(var(--r) - 2px);color:var(--fg);font-size:14px;cursor:pointer;background:var(--bg);transition:border-color .15s,background-color .15s}
.clo-lg label:hover{border-color:color-mix(in srgb,var(--primary) 40%,var(--border))}
.clo-lg label.on{border-color:color-mix(in srgb,var(--primary) 55%,var(--border));background:var(--hover)}.clo-lg label.off{opacity:.55;cursor:not-allowed}
.clo-lg input{width:auto;flex:none}.clo-lg .nm{flex:1;min-width:0}.clo-lg small{display:block;color:var(--muted);font-size:11.5px;line-height:1.3}.clo-lg small.live{color:var(--ok);font-weight:600}
.clo-opt{display:grid;gap:8px;margin-top:12px}.clo-opt>div{border:1px solid var(--border);border-radius:calc(var(--r) - 2px);padding:10px 12px;font-size:12.5px;color:var(--muted)}.clo-opt>div.on{border-color:var(--primary);color:var(--fg);background:var(--hover)}.clo-opt b{display:block;color:var(--fg);font-size:13px}
/* panels */
.clo-cta{display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding:14px 18px;margin-bottom:14px;border:1px solid var(--border);border-radius:var(--r);background:var(--card);color:var(--fg);text-decoration:none;box-shadow:var(--shadow);transition:border-color .15s}
.clo-cta:hover{border-color:var(--primary)}.clo-cta .ico{width:38px;height:38px;border-radius:9px;background:var(--hover);color:var(--hover-fg);display:grid;place-items:center;flex:none}.clo-cta .ico svg{width:18px;height:18px}
.clo-cta .tx{flex:1;min-width:220px;font-size:13px;color:var(--muted)}.clo-cta .tx strong{display:block;color:var(--fg);font-size:14px;margin-bottom:2px}.clo-cta .go{font-weight:600;color:var(--primary);white-space:nowrap}
.clo-plink{background:none;border:0;padding:0;font:inherit;font-weight:600;color:var(--fg);cursor:pointer;text-decoration:underline;text-decoration-color:var(--border);text-underline-offset:3px}.clo-plink:hover{text-decoration-color:var(--primary);color:var(--primary)}
.clo-off td:not(:first-child){color:var(--muted)}
.clo-fw{display:grid;gap:6px;padding:12px 14px;border:1px solid var(--border);border-radius:calc(var(--r) - 2px);background:var(--bg)}.clo-fw b{font-size:14px}.clo-fw .meta{font-size:12.5px;color:var(--muted)}
.clo-fw .ctl{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:6px}.clo-fw input[type=file]{flex:1;min-width:200px;font-size:12px;color:var(--muted)}
#drawerBg{position:fixed;inset:0;z-index:30;background:color-mix(in srgb,var(--bg) 55%,transparent);-webkit-backdrop-filter:blur(2px);backdrop-filter:blur(2px)}
#drawer{position:fixed;top:0;right:0;bottom:0;z-index:31;width:min(460px,100vw);background:var(--card);color:var(--fg);border-left:1px solid var(--border);box-shadow:-14px 0 40px color-mix(in srgb,var(--fg) 14%,transparent);display:flex;flex-direction:column;transform:translateX(100%);transition:transform .2s ease-out}
#drawer.in{transform:none}
#drawer>header{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:14px 18px;border-bottom:1px solid var(--border)}#drawer>header h2{margin:0;font-size:16px;letter-spacing:-.01em}
#drawer .dbody{flex:1;overflow:auto;padding:16px 18px 24px}#drawer .dfoot{display:flex;gap:8px;align-items:center;padding:12px 18px;border-top:1px solid var(--border);background:var(--card)}
.clo-dh{font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:22px 0 10px;padding-top:14px;border-top:1px solid var(--border)}
.clo-rads{display:flex;gap:16px;flex-wrap:wrap}.clo-rads label{display:flex;gap:6px;align-items:center;margin:0;color:var(--fg);font-size:13px;cursor:pointer}.clo-rads input{margin:0}
.clo-kv{display:grid;grid-template-columns:110px 1fr;gap:6px 12px;font-size:13px}.clo-kv>span:nth-child(odd){color:var(--muted)}
.clo-upd{margin-top:12px;padding:10px 12px;border:1px solid var(--border);border-radius:calc(var(--r) - 2px);font-size:13px;background:var(--bg)}.clo-upd progress{display:block;width:100%;height:6px;margin-top:8px;accent-color:var(--primary)}
.clo-upd.bad{color:var(--err)}
.clo-timers{display:grid;gap:4px}
/* alerts */
.clo-ac{border:1px solid var(--border);border-radius:calc(var(--r) - 2px);padding:14px;margin-bottom:10px;background:var(--bg)}
.clo-ah{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px}.clo-ah input[type=text]{flex:1 1 200px;width:auto}.clo-ah select{width:auto;max-width:100%}
.clo-ai{width:34px;height:34px;border-radius:9px;background:var(--hover);color:var(--hover-fg);display:grid;place-items:center;flex:none}.clo-ai svg{width:20px;height:20px}
.clo-as{font-size:12.5px;font-weight:600;color:var(--muted);margin-left:auto}.clo-as.on{color:var(--err)}.clo-as.seen{color:var(--ok)}.clo-as.missed{color:var(--warn)}
.clo-at{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:2px 14px;margin-bottom:6px}
.clo-empty{padding:22px;text-align:center;color:var(--muted);border:1px dashed var(--border);border-radius:calc(var(--r) - 2px);font-size:13px}
.clo-save{display:flex;align-items:center;gap:10px;margin-left:auto}
.clo-lv{display:inline-flex;align-items:center;gap:6px;font-size:12px}
.clo-link{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:10px 0;border-bottom:1px solid color-mix(in srgb,var(--border) 70%,transparent)}.clo-link:last-child{border:0}
.clo-link>span:first-child{min-width:170px;font-size:13px;color:var(--muted)}.clo-link a{color:var(--fg);flex:1;min-width:0;word-break:break-all}
.clo-hd td{vertical-align:top}.clo-hd small{display:block;color:var(--muted);font-size:12px;margin-top:2px}
/* urls */
.clo-ur{display:grid;grid-template-columns:200px 1fr;gap:10px 18px;padding:14px 0;border-bottom:1px solid color-mix(in srgb,var(--border) 70%,transparent)}.clo-ur:last-child{border:0;padding-bottom:0}
.clo-ur>b{font-weight:500;font-size:13.5px}.clo-ul{display:grid;gap:10px}
.clo-ul .it{display:flex;gap:12px;align-items:center}.clo-ul .ln{flex:1;min-width:0;display:flex;gap:8px;align-items:center;flex-wrap:wrap}.clo-ul a{color:var(--fg);word-break:break-all;font-size:13px}
.clo-qr{width:46px;height:46px;flex:none;padding:3px;border-radius:6px;border:1px solid var(--border);background:var(--card);color:var(--fg)}.clo-qr svg{display:block;width:100%;height:100%}
@media(max-width:700px){.clo-ur{grid-template-columns:1fr}}
.clo-fp{word-break:break-all;user-select:all;padding:10px 12px;border:1px solid var(--border);border-radius:calc(var(--r) - 2px);background:var(--bg);margin:6px 0}
.clo-warnbox{padding:10px 12px;border:1px solid color-mix(in srgb,var(--warn) 45%,var(--border));border-radius:calc(var(--r) - 2px);color:var(--warn);font-size:13px;margin:8px 0}
.clo-tg{width:auto;display:inline-flex}.clo-tg button{flex:none;padding:0 12px}
/* overlay */
.clo-ctl{display:grid;grid-template-columns:minmax(0,1fr);gap:14px;min-width:0}.clo-ctl .card{padding:14px 16px}.clo-ctl h3{margin-bottom:10px}
.clo-rng{display:flex;gap:10px;align-items:center}.clo-rng input{flex:1}.clo-rng output{min-width:52px;text-align:right;font-size:12.5px;color:var(--fg);font-variant-numeric:tabular-nums}
.clo-two{display:grid;grid-template-columns:1fr 1fr;gap:10px}.clo-col{display:grid;grid-template-columns:1fr 1fr 1.4fr;gap:10px;align-items:end}
input[type=color]{height:34px;padding:2px 3px;cursor:pointer}
.clo-pre{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.clo-pt{display:flex;flex-direction:column;gap:5px;padding:8px;border:1px solid var(--border);border-radius:calc(var(--r) - 2px);background:var(--bg);color:var(--fg);cursor:pointer;text-align:left;font:inherit;font-size:12.5px;font-weight:500;transition:border-color .15s,background-color .15s}
.clo-pt:hover{border-color:color-mix(in srgb,var(--primary) 45%,var(--border))}.clo-pt.on{border-color:var(--primary);background:var(--hover)}
.clo-pt small{font-weight:400;color:var(--muted);font-size:11px;line-height:1.3}
.clo-pt .th{position:relative;height:46px;border-radius:5px;background:var(--hover);overflow:hidden}.clo-pt .th.dark{background:var(--fg)}
.clo-pt .th i{position:absolute;left:12%;right:12%;height:5px;border-radius:3px;background:var(--fg)}.clo-pt .th.dark i{background:var(--bg)}.clo-pt .th i.b{background:var(--primary);left:22%;right:22%}
@media(max-width:1300px){.clo-pre{grid-template-columns:repeat(2,1fr)}}
.clo-ed{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:10px 12px;border:1px solid color-mix(in srgb,var(--primary) 50%,var(--border));border-radius:calc(var(--r) - 2px);background:var(--hover);font-size:13px}
.clo-prev{position:sticky;top:64px;display:grid;grid-template-columns:minmax(0,1fr);gap:12px;min-width:0}
.clo-bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.clo-bar .clo-l{margin:0}
.clo-stage{--clo-green:#00b140;--clo-black:#000;--clo-white:#fff;position:relative;width:100%;margin:0 auto;border:1px solid var(--border);border-radius:calc(var(--r) - 2px);overflow:hidden}
.clo-stage.bk-program{background:radial-gradient(ellipse at 50% 30%,color-mix(in srgb,var(--primary) 26%,var(--card)),color-mix(in srgb,var(--muted) 35%,var(--bg)) 70%)}
.clo-stage.bk-checker{background:repeating-conic-gradient(var(--hover) 0 25%,var(--card) 0 50%) 0 0/20px 20px}
.clo-stage.bk-black{background:var(--clo-black)}
.clo-stage .out,.clo-stage .cap,.clo-stage .sf{position:absolute}.clo-stage .out{inset:0}
.clo-stage .cap{display:flex;flex-direction:column;line-height:1.28;pointer-events:none}.clo-stage .grp{display:flex;flex-direction:column;align-items:inherit;width:100%;gap:.12em}
.clo-stage .grp+.grp{margin-top:.4em}.clo-stage .gl{font-size:.42em;opacity:.8;letter-spacing:.04em}.clo-stage .ln{padding:.04em .35em;border-radius:.14em;box-decoration-break:clone;-webkit-box-decoration-break:clone}
.clo-stage .sf{border:1px dashed color-mix(in srgb,var(--fg) 55%,transparent);pointer-events:none}
.clo-stage .none{margin:auto;font-size:12px;color:var(--muted)}
.clo-url{padding:10px 12px;border:1px solid var(--border);border-radius:calc(var(--r) - 2px);background:var(--bg);word-break:break-all;user-select:all}
.clo-sv{display:flex;gap:8px}.clo-sv input{flex:1}
.clo-rec{display:grid;grid-template-columns:repeat(2,1fr);gap:14px}@media(max-width:1000px){.clo-rec{grid-template-columns:1fr}}
.clo-rec ol{margin:10px 0 0;padding-left:18px;font-size:13px;display:grid;gap:7px;color:var(--fg)}.clo-rec li::marker{color:var(--muted)}
.clo-rec em{font-style:normal;font-weight:600}
.clo-files{display:flex;gap:6px 10px;flex-wrap:wrap;margin-top:6px;font-size:12.5px}
.clo-ex{display:grid;gap:8px;margin:12px 0 4px}.clo-ex>div{display:flex;gap:10px;align-items:center;padding:8px 10px;border:1px solid var(--border);border-radius:calc(var(--r) - 2px);background:var(--bg);font-size:12.5px}
a.clo-a{color:var(--fg);text-decoration:underline;text-decoration-color:var(--border);text-underline-offset:3px}a.clo-a:hover{text-decoration-color:var(--primary)}
`);

/* ================================================================== SESSION HEALTH */
const sessionsOf = () => {
  if (!live) return [];
  const r = [['Transcription', 'connected', '', '0.4s ago', 0, 0]];
  if (C.mode === 'text') { r.push(['Text translation', 'connected', '', '0.9s ago', 0, 0]); return r; }
  [...C.on].filter(t => !textOnly(t)).forEach((t, i) => r.push(i === 1
    ? [lname(t), 'reconnecting', 'socket closed by peer; retrying in 2 s', '3.1s ago', 2, 14]
    : [lname(t), 'connected', '', (0.5 + i * 0.3).toFixed(1) + 's ago', 0, 0]));
  return r;
};
P.health = () => {
  const ss = sessionsOf(), ok = ss.filter(s => s[1] === 'connected').length;
  const masterText = live ? 'Running · Sunday services · until 12:30 PM' : 'Stopped · next Sun 8:30 AM';
  const opts = ['<option value="">Transcription (source)</option>'].concat([...C.on].map(t => `<option value="${t}"${C.feed === t ? ' selected' : ''}>${lname(t)} (translation)</option>`)).join('');
  return head('Session health', 'Start and stop the stream, watch the live transcript and check each session.', {vo: 'session.control'}) +
  `<div class="clo-sg">${stat('Capture', live ? 'Running' : 'Stopped', live ? 'Started by the Sunday schedule' : 'Next start Sun 8:30 AM', live ? 'up' : '')}
    ${stat('Sessions', live ? ok + ' of ' + ss.length : '—', live ? (ok < ss.length ? 'One is reconnecting' : 'All connected') : 'Not started', live && ok < ss.length ? 'dn' : '')}
    ${stat('Viewers', live ? 24 : 0, live ? '19 phones, 5 screens' : 'Nobody watching')}
    ${stat('Audio input', live ? 'Speaking' : 'Idle', live ? 'Behringer X32 USB · −24 dBFS' : 'No signal being read', live ? 'up' : '')}</div>` +
  card('Stream control', '', `<div class="clo-big"><span class="clo-st ${live ? 'ok' : ''}"><i></i><span class="v clo-mut" id="masterState" style="color:var(--fg)">${masterText}</span></span><span class="sp" style="flex:1"></span>
      ${C.vo ? '' : `<button class="btn p" id="startBtn" data-clo-act="start"${live ? ' disabled' : ''}>${ic('play')}Start capture</button><button class="btn d" id="stopBtn" data-clo-act="stop"${live ? '' : ' disabled'}>${ic('stop')}Stop</button>`}</div>
    ${note(C.vo ? 'Shows whether transcription and the translation sessions are running.' : 'Starts or stops transcription and every translation session together. Stopping ends billed API usage.')}`, {cls: 'clo-mb'}) +
  `<div style="height:14px"></div><div class="clo-g b">` +
  card('Sessions', 'One line per transcription or translation session.', `<div class="clo-sc"><table class="clo-t"><thead><tr><th>Session</th><th>State</th><th>Last delta</th><th>Retries</th><th>Audio dropped</th></tr></thead><tbody id="sessions">${
    ss.length ? ss.map(s => `<tr><td>${s[0]}</td><td><span class="badge ${s[1] === 'connected' ? 'ok' : s[1] === 'error' ? 'err' : 'warn'}">${s[1]}</span>${s[2] ? ` <span class="clo-mono clo-mut">${s[2]}</span>` : ''}</td><td class="clo-mut">${s[3]}</td><td class="clo-mut">${s[4]}</td><td class="${s[5] ? 'clo-err' : 'clo-mut'}">${s[5]}</td></tr>`).join('') : '<tr><td colspan="5" class="clo-mut">Not started.</td></tr>'}</tbody></table></div>`) +
  card('Live transcript', '', `<div class="clo-feedbar"><select id="feedSel" aria-label="Feed to watch" data-clo-in="feed">${opts}</select>
      <label class="clo-demo"><input type="checkbox" id="feedScroll" checked> Auto-scroll</label><span class="clo-mut" id="feedState" style="font-size:12px">Live</span></div>
    <div class="clo-feed" id="feedBox" tabindex="0" role="log" aria-label="Live transcript"></div>`) + `</div>`;
};
ACT.start = () => { if (!live) { stream(); route(); } };
ACT.stop = () => { if (live) { stream(); route(); } };
IN.feed = el => { C.feed = el.value; feedStart(); };
function feedStart() {
  clearInterval(window.cloFeedT);
  const box = $('feedBox'); if (!box) return;
  const arr = samp(isoOf(C.feed) === 'en' && !C.feed ? 'en' : isoOf(C.feed));
  box.innerHTML = '';
  if (!live) { box.innerHTML = '<div class="empty">Waiting for speech…</div>'; return; }
  let n = 0, open = null, words = [];
  const add = (t, cls) => { const p = document.createElement('p'); p.className = cls; p.textContent = t; box.appendChild(p); return p; };
  for (let i = 0; i < 3; i++) add(arr[(i + 1) % 4], 'settled');
  const stick = () => { const s = $('feedScroll'); if (s && s.checked) box.scrollTop = box.scrollHeight; };
  stick();
  window.cloFeedT = setInterval(() => {
    if (!$('feedBox')) { clearInterval(window.cloFeedT); return; }
    if (!open) { open = add('', 'open'); words = arr[n++ % 4].split(/(?<=\s)/); }
    open.textContent += words.shift() || '';
    if (!words.length) { open.className = 'settled'; open = null; if (box.children.length > 40) box.firstChild.remove(); }
    stick();
  }, 380);
}
AFTER.health = feedStart;

/* ================================================================== AUDIO INPUT */
const DEVS = [['x32', 'Behringer X32 USB', 32, 48000, 0], ['mbp', 'MacBook Pro Microphone', 1, 48000, 1], ['scar', 'Focusrite Scarlett 2i2', 2, 44100, 0]];
const AUD = {
  speaking: {dot: 'ok', txt: 'Speaking · 48000 Hz → 24000 Hz', w: null, pk: 78, read: '', cls: ''},
  quiet: {dot: 'warn', txt: 'Signal quiet · 48000 Hz → 24000 Hz', w: 9, pk: 14, read: '-52.0 · pk -41.3', cls: ''},
  clipping: {dot: 'ok', txt: 'Speaking · 48000 Hz → 24000 Hz', w: 96, pk: 100, read: '-3.1 · pk 0.0 · 1.2k clipped', cls: 'clipping', clip: 1},
  waiting: {dot: 'warn', txt: 'Waiting for input', w: 0, pk: 0, read: '—', cls: ''},
  error: {dot: 'err', txt: 'Error', w: 0, pk: 0, read: '—', cls: '', err: 'Could not open “Behringer X32 USB”: the device is in use by another program.'},
  idle: {dot: '', txt: 'Idle', w: 0, pk: 0, read: '—', cls: ''}
};
P.audio = () => {
  const key = C.aud || (live ? 'speaking' : 'idle'), a = AUD[key], dev = DEVS.find(d => d[0] === C.audDev) || DEVS[0];
  const devOpts = DEVS.map(d => `<option value="${d[0]}"${d[0] === dev[0] ? ' selected' : ''}>${d[1]}  (${d[2]} ch, ${d[3]} Hz${d[4] ? ', system default' : ''})</option>`).join('');
  const chOpts = Array.from({length: dev[2]}, (_, i) => `<option>Channel ${i + 1}</option>`).join('') + (dev[2] > 1 ? `<option value="mix">Mix (${dev[2]} ch)</option>` : '');
  const ch = dev[2] > 1 ? 'mix' : 'ch 1', dropNote = key === 'quiet' ? '  ·  2 capture frame(s) dropped' : '';
  const aNote = a.err || (dev[1] + '  ·  ' + ch + dropNote);
  const demoTabs = `<div class="tabs" data-clo-tabs-x style="margin:0;max-width:100%;overflow:auto">${Object.keys(AUD).map(k => `<button type="button" data-clo-act="audstate" data-k="${k}" class="${k === key ? 'on' : ''}">${k[0].toUpperCase() + k.slice(1)}</button>`).join('')}</div>`;
  return head('Audio input', 'Choose the capture device and watch the input level.', {demo: 'Demo: no settings.manage', vo: false}) +
  `<div class="clo-g a">` +
  card('Capture device', 'What Relay listens to.', `
    <div class="clo-f"><label for="device">Capture device</label>
      <select id="device" data-clo-in="audev"${C.audNone ? ' disabled' : ''}>${C.audNone ? '<option>No input devices found</option>' : devOpts}</select></div>
    <div class="clo-f"><label for="channel">Channel</label><div class="row" style="flex-wrap:nowrap"><select id="channel"${C.audNone ? ' disabled' : ''}>${C.audNone ? '' : chOpts}</select>
      <button class="btn" id="rescan" data-clo-act="rescan" style="flex:none">${ic('cloRefresh')}Rescan</button></div>
      <p class="clo-n" id="deviceNotice" role="status"${C.audNone ? '' : ' hidden'}>Relay cannot see an audio input. Allow it under System Settings, Privacy &amp; Security, Microphone, then press Rescan.</p></div>
    <div class="clo-f" style="margin-bottom:0"><label for="noise">Noise reduction</label>
      <select id="noise" data-clo-in="noise"${C.vo ? ' disabled' : ''}><option value=""${C.noise === '' ? ' selected' : ''}>Board feed / line in</option><option value="far_field"${C.noise === 'far_field' ? ' selected' : ''}>Room or laptop mic</option><option value="near_field"${C.noise === 'near_field' ? ' selected' : ''}>Headset / lavalier</option></select>
      ${note('Runs before the model’s own phrase detection. Leave off for a house console feed — it is already gated, and reduction on top can chew word onsets. Changing it restarts live sessions.')}</div>
    <div style="margin-top:14px;padding-top:12px;border-top:1px solid var(--border)" class="row"><span class="clo-l" style="margin:0">Demo devices</span><div class="tg clo-tg" role="group"><button type="button" data-clo-act="audnone" data-v="0" aria-pressed="${!C.audNone}">Found</button><button type="button" data-clo-act="audnone" data-v="1" aria-pressed="${C.audNone}">None found</button></div></div>`) +
  card('Input level', 'The bar is RMS, the thin marker is the decaying peak. Amber from −6 dBFS, red when clipping.', `
    <div class="row" style="justify-content:space-between"><span class="clo-pill" id="audioPill"><span class="clo-dot ${a.dot}" id="audioDot"></span><span id="audioState">${a.txt}</span></span>${demoTabs}</div>
    <div class="clo-mw"><div class="clo-mc"><div class="clo-meter ${a.cls}" id="meter"><i${a.w == null ? ' id="mt"' : ` style="width:${a.w}%"`}></i><b id="meterPeak" style="left:${a.pk}%;opacity:${a.pk ? 1 : 0}"></b></div>
      <span class="clo-scale" aria-hidden="true"><span style="left:0">−60</span><span style="left:33.3%;transform:translateX(-50%)">−40</span><span style="left:70%;transform:translateX(-50%)">−18</span><span style="left:100%;transform:translateX(-100%)">0 dBFS</span></span></div>
      <div class="clo-ms"><span class="clo-clip${a.clip ? ' on' : ''}" id="clipLed" title="Input clipping — reduce gain at the console">CLIP</span><span class="clo-read${a.clip ? ' clipped' : ''}" id="meterRead">${a.w == null ? '<span id="mtv">−24 dBFS</span>' : a.read}</span></div></div>
    <p class="clo-n${a.err || dropNote ? ' err' : ''}" id="audioNote">${aNote}</p>`) + `</div>`;
};
ACT.audstate = b => { C.aud = b.dataset.k; route(); };
ACT.audnone = b => { C.audNone = b.dataset.v === '1'; route(); };
ACT.rescan = () => toast('Devices rescanned.');
IN.audev = el => { C.audDev = el.value; route(); toast('Input saved.'); };
IN.noise = el => { C.noise = el.value; toast('Input saved.'); };

/* ================================================================== TARGET LANGUAGES */
function tstate(l) {
  const [t, , , to] = l, en = C.on.has(t);
  if (isLive(t)) return 'live';
  if (to && C.mode !== 'text') return en ? 'needs the Text engine' : 'Text engine only';
  return en ? 'armed — starts with capture' : 'off';
}
const targetsSum = () => {
  const lv = LANGS.filter(l => isLive(l[0])), ar = LANGS.filter(l => !isLive(l[0]) && C.on.has(l[0]));
  const nm = a => a.map(l => l[1]).join(', ');
  return '· ' + (lv.length || ar.length ? '' : 'none on') + (lv.length ? `<span class="live">${nm(lv)} live</span>` : '') + (lv.length && ar.length ? ' · ' : '') + (ar.length ? nm(ar) + ' armed' : '');
};
function langGrid() {
  return LANGS.map(l => {
    const needs = l[3] && C.mode !== 'text', en = C.on.has(l[0]), s = tstate(l);
    return `<label class="${en ? 'on' : ''}${needs && !en ? ' off' : ''}"><input type="checkbox" data-clo-in="tgt" data-t="${l[0]}"${en ? ' checked' : ''}${needs && !en ? ' disabled' : ''}><span class="nm">${l[1]}<small id="tstate-${l[0]}" class="${s === 'live' ? 'live' : ''}">${s}</small></span></label>`;
  }).join('');
}
function targetsRefresh() {
  $('targets').innerHTML = langGrid();
  $('targetsSum').innerHTML = targetsSum();
  $('targetsCount').textContent = C.on.size + ' enabled';
  $('targetsNote').textContent = C.mode === 'text' ? 'Every live target is translated from the one transcription session. Turning one off takes effect from the next line.' : 'Each live target is a separate, continuously-billed session. Turning one off closes it immediately.';
  document.querySelectorAll('[data-clo-eng]').forEach(d => d.classList.toggle('on', d.dataset.cloEng === C.mode));
}
P.targets = () => head('Target languages', 'Choose which languages are translated and offered to viewers.', {sum: targetsSum(), sumId: 'targetsSum', vo: false, demo: 'Demo: no settings.manage'}) +
  `<div class="clo-g a">` +
  card('Enabled languages <span class="clo-cnt" id="targetsCount">' + C.on.size + ' enabled</span>', 'Each tick is one language viewers can pick.', `
    <div class="row" style="margin-bottom:12px"><button type="button" class="btn sm" id="targetsAll" data-clo-act="tall">Select all</button><button type="button" class="btn sm" id="targetsNone" data-clo-act="tnone">Clear</button></div>
    <div class="clo-lg" id="targets">${langGrid()}</div>
    ${note(C.mode === 'text' ? 'Every live target is translated from the one transcription session. Turning one off takes effect from the next line.' : 'Each live target is a separate, continuously-billed session. Turning one off closes it immediately.', '', 'targetsNote')}`) + `</div>`;
IN.tgt = el => { el.checked ? C.on.add(el.dataset.t) : C.on.delete(el.dataset.t); targetsRefresh(); };
ACT.tall = () => { const todo = LANGS.filter(l => !C.on.has(l[0]) && (C.mode === 'text' || !l[3])); todo.forEach(l => C.on.add(l[0])); targetsRefresh(); toast(C.mode === 'text' ? 'All languages on' : 'Turn on ' + todo.length + ' languages? Each is a separate, continuously-billed session while capture runs.'); };
ACT.tnone = () => { C.on.clear(); targetsRefresh(); };
/* the head demo toggle must not leave the select disabled in the wrong state; route() redraws it */

/* ================================================================== PANELS */
C.panels = [
  {id: 'p1', name: 'Lobby left', named: 1, online: 1, feeds: ['translation:SPANISH'], set: 1, ver: '1.4.2', avail: '1.5.0', ip: '192.168.1.52', rssi: -58, seen: 'just now', rot: 0, rep: 'relay-display-a4f2', changed: 'from admin · 3h ago', touched: '4m ago', reboot: 1},
  {id: 'p2', name: 'Lobby right', named: 1, online: 1, feeds: ['translation:PORTUGUESE'], set: 1, ver: '1.4.2', upd: {state: 'downloading', ver: '1.5.0', p: 64}, ip: '192.168.1.53', rssi: -66, seen: 'just now', rot: 0, changed: 'from admin · 3h ago', touched: '11m ago', reboot: 1},
  {id: 'p3', name: 'Nursery', named: 1, online: 1, feeds: ['transcription', 'timers'], set: 1, ver: '1.5.0', ip: '192.168.1.58', rssi: -49, seen: 'just now', rot: 180, lock: 1, pin: 1, unlock: 5, ret: 10, timers: 1, changed: 'at the screen · 25m ago', touched: '25m ago', reboot: 1, svc: 'show'},
  {id: 'p4', name: 'Youth room', named: 1, online: 0, feeds: [], set: 0, ver: '1.4.2', ip: '192.168.2.31', rssi: null, seen: '2d ago', rot: 0},
  {id: 'p5', name: 'Booth alerts', named: 1, online: 1, feeds: ['alert'], set: 1, ver: '1.5.0', ip: '192.168.1.60', rssi: -72, seen: 'just now', rot: 0, changed: 'from admin · 6d ago', touched: '2h ago', reboot: 1}
];
const BOARD = 'CrowPanel Advance 7"';
const SVC = ['Sunday Worship', 'Youth Wednesday'];
const PANEL_LANGS = ['SPANISH', 'PORTUGUESE', 'FRENCH', 'GERMAN', 'ITALIAN', 'INDONESIAN', 'VIETNAMESE'];
function feedLabel(v) {
  if (v === 'transcription') return 'Transcription';
  if (v === 'alert') return 'Alert buttons';
  if (v === 'timers') return 'Timers';
  if (v === 'lyrics') return 'Lyrics';
  if (v === 'status') return 'Status';
  if (v.indexOf('service:') === 0) return 'Service: ' + v.slice(8);
  return lname(v.replace('translation:', ''));
}
const feedText = fs => fs && fs.length ? fs.map(feedLabel).join(' + ') : 'none chosen';
const signal = r => r == null ? '—' : (r >= -60 ? 'strong' : r >= -72 ? 'fair' : 'weak') + ' (' + r + ' dBm)';
const panelsSum = () => { const on = C.panels.filter(p => p.online).length; return C.panels.length ? on + ' of ' + C.panels.length + ' online' : ''; };
function panelRows() {
  if (!C.panels.length) return '<div class="clo-empty" style="margin-top:0">No panels have checked in yet.</div>';
  return `<div class="clo-sc"><table class="clo-t dt"><thead><tr><th>Name</th><th>Board</th><th>Showing</th><th>Firmware</th><th>Status</th></tr></thead><tbody>${C.panels.map(p => `<tr class="${p.online ? '' : 'clo-off'}"><td><button type="button" class="clo-plink" data-clo-act="opendrawer" data-id="${p.id}">${esc(p.name)}</button></td><td>${BOARD}</td><td>${esc(feedText(p.feeds))}</td><td>${p.ver}</td><td>${st(p.online ? 'ok' : '', p.online ? 'Online' : 'Offline')}${p.avail && !p.upd ? ' <span class="badge warn">update</span>' : ''}</td></tr>`).join('')}</tbody></table></div>`;
}
function fwBox() {
  const on = C.panels.filter(p => p.online).length, behind = C.panels.filter(p => p.ver !== '1.5.0').length;
  const any = C.panels.some(p => p.avail && p.online && !p.upd);
  return `<div id="fwTypes"><div class="clo-fw"><b>${BOARD}</b><div class="meta">relay-display-app.bin · 1.5.0 · 1412 KB · uploaded 3d ago · ${on} online${behind ? ', ' + behind + ' behind' : ''}</div>
    ${C.vo ? '' : `<div class="ctl"><input type="file" accept=".bin,application/octet-stream" aria-label="Signed ${BOARD} image (relay-display-app.bin)"><button type="button" class="btn sm" data-clo-act="toast" data-msg="Choose the ${BOARD} relay-display-app.bin first.">${ic('cloUp')}Upload</button>${any ? `<button type="button" class="btn sm" data-clo-act="updall">Update all online</button>` : ''}<button type="button" class="btn sm" data-clo-act="toast" data-msg="Firmware removed">Remove</button></div>`}</div></div>
    ${C.vo ? '' : note('One image per kind of panel. Upload that panel\'s <span class="clo-mono">relay-display-app.bin</span> from a release; Relay checks it is a panel image for that board, but the panel decides whether the signature is right and refuses anything not signed with its release key. An update installs at a quiet moment (no new caption line for a while) unless you choose “Update now”.')}`;
}
P.panels = () => head('Panels', 'Caption panels on the venue network. Click a panel name to see ' + (C.vo ? '' : 'and change ') + 'its settings.', {sum: panelsSum(), sumId: 'panelsSum', demo: 'Demo: no panels.manage'}) +
  (C.vo ? '' : `<a class="clo-cta" href="#panels" data-clo-act="toast" data-msg="Opens /admin/flash"><span class="ico" aria-hidden="true">${ic('cloDl')}</span><span class="tx"><strong>Set up a new panel over USB</strong>Flash a new or recovered panel from Chrome or Edge with the release’s <span class="clo-mono">relay-display.bin</span>.</span><span class="go">Flash a panel →</span></a>`) +
  `<div class="clo-g b">` +
  card('Paired panels', 'Panels appear here by themselves once they join the venue network.', `<div id="panelList">${panelRows()}</div>`) +
  `<div style="display:grid;gap:14px">` +
  card('Panel firmware', 'Held by Relay, installed on the panel.', fwBox()) +
  card('How panels work', '', note('Caption panels on the venue network appear here by themselves. A feed you set is sent to the panel at once; a change made at the panel’s own screen afterwards wins until you set one again. Forgetting a panel that is still switched on brings it straight back, unnamed.')) +
  `</div></div>`;
ACT.updall = () => { C.panels.forEach(p => { if (p.avail && p.online && !p.upd) p.upd = {state: 'queued', ver: p.avail}; }); toast('1 panel(s) asked to update; 1 offline, ask them one by one'); route(); };
const panelRefresh = () => { const l = $('panelList'); if (l) l.innerHTML = panelRows(); const s = $('panelsSum'); if (s) s.textContent = panelsSum(); };

ACT.opendrawer = b => openDrawer(b.dataset.id);
function openDrawer(id) {
  closeDrawer();
  const p = C.panels.find(x => x.id === id); if (!p) return;
  C.drawer = id;
  const bg = document.createElement('div'); bg.id = 'drawerBg'; bg.onclick = closeDrawer;
  const d = document.createElement('aside'); d.id = 'drawer'; d.setAttribute('aria-labelledby', 'dTitle');
  d.innerHTML = `<header><h2 id="dTitle">${esc(p.name)}</h2><button type="button" class="ib" id="dClose" aria-label="Close">${ic('x')}</button></header><div class="dbody" id="dBody">${drawerBody(p)}</div><div class="dfoot" id="dFoot">${
    C.vo ? '<button type="button" class="btn" data-clo-act="dclose">Close</button>'
         : `<button type="button" class="btn p" data-clo-act="dsave" data-id="${p.id}">Save</button><button type="button" class="btn" data-clo-act="dclose">Cancel</button><span style="flex:1"></span><button type="button" class="btn d" data-clo-act="dforget" data-id="${p.id}">Forget</button>`}</div>`;
  document.body.appendChild(bg); document.body.appendChild(d);
  d.querySelector('#dClose').onclick = closeDrawer;
  d.addEventListener('change', drawerSync); d.addEventListener('input', drawerSync);
  drawerSync();
  requestAnimationFrame(() => { d.classList.add('in'); d.querySelector('#dClose').focus(); });
}
ACT.dclose = closeDrawer;
ACT.dsave = b => { const p = C.panels.find(x => x.id === b.dataset.id); const n = ($('dName') || {}).value; if (n) p.name = n; toast('Saved ' + p.name + '.'); closeDrawer(); panelRefresh(); };
ACT.dforget = b => { const p = C.panels.find(x => x.id === b.dataset.id); if (!confirm('Forget ' + p.name + '? A panel that is still on will reappear, unnamed.')) return; C.panels = C.panels.filter(x => x !== p); closeDrawer(); panelRefresh(); };
ACT.dupdate = b => toast(b.dataset.now ? 'Restarting into the new version' : 'Update queued for a quiet moment');
ACT.dreboot = b => toast(b.dataset.n + ' is restarting.');
const SERVICE_HELP = {off: 'No Service feed in the panel\'s menu.', show: 'Now and next, timers, the order of service, song details and who is controlling.', control: 'Next, Previous and Take control, with who is controlling. No order of service.', both: 'The order of service and the controls.'};
function drawerBody(p) {
  const dd = D(), fs = p.feeds || [], count = fs.length > 1 ? 2 : 1;
  const feedOpts = (cur, first) => `<option value=""${cur ? '' : ' selected'}>${first}</option><option value="transcription"${cur === 'transcription' ? ' selected' : ''}>Transcription</option>` +
    PANEL_LANGS.map(t => `<option value="translation:${t}"${cur === 'translation:' + t ? ' selected' : ''}>${lname(t)}</option>`).join('') +
    SVC.map(s => `<option value="service:${s}"${cur === 'service:' + s ? ' selected' : ''}>Service: ${s}</option>`).join('') +
    [['alert', 'Alert buttons'], ['lyrics', 'Lyrics (ProPresenter)'], ['timers', 'Timers (ProPresenter)'], ['status', 'Status']].map(o => `<option value="${o[0]}"${cur === o[0] ? ' selected' : ''}>${o[1]}</option>`).join('');
  const svc = p.svc || 'off';
  const rad = (nm, list, val) => `<div class="clo-rads">${list.map(o => `<label><input type="radio" name="${nm}" value="${o[0]}"${String(o[0]) === String(val) ? ' checked' : ''}${dd}>${o[1]}</label>`).join('')}</div>`;
  const dev = [['Status', p.online ? 'Online' : 'Offline'], ['Last seen', p.seen], ['Showing', feedText(fs)]];
  if (p.changed) dev.push(['Feed changed', p.changed]);
  if (p.lock) dev.push(['Screen', 'Locked' + (p.pin ? ', PIN to unlock' : '')]);
  if (p.touched) dev.push(['Last touched', p.touched]);
  dev.push(['Board', BOARD], ['Rotation', p.rot + '°'], ['Firmware', p.ver], ['Address', p.ip], ['Wi-Fi', signal(p.rssi)]);
  if (p.rep) dev.push(['Reports as', p.rep]);
  const u = p.upd;
  const updRow = u ? `<div class="clo-upd${u.state === 'failed' ? ' bad' : ''}"><div>${{queued: 'Waiting for the panel to start', downloading: 'Downloading'}[u.state]} · ${u.ver}${u.state === 'downloading' ? ' · ' + u.p + '%' : ''}</div>${u.state === 'downloading' ? `<progress max="100" value="${u.p}"></progress>` : ''}</div>`
    : p.avail ? `<div class="clo-upd"><span class="badge warn">Update available · ${p.avail}</span></div>` : '';
  return `<form autocomplete="off" onsubmit="return false">
  <div class="clo-f"><label for="dName">Name</label><input type="text" id="dName" maxlength="64" placeholder="Lobby left" value="${esc(p.name)}"${dd}></div>
  <h3 class="clo-dh" style="margin-top:6px">Display</h3>
  <div class="clo-f"><label>Number of feeds</label>${rad('nfeeds', [[1, 'One (full screen)'], [2, 'Two (split)']], count)}</div>
  <div class="clo-f"><label for="dFeed0">Feed 1<span class="clo-mut" data-f1l></span></label><select id="dFeed0"${dd}>${feedOpts(fs[0], p.set ? 'The panel\'s own choice' : '— none —')}</select></div>
  <div class="clo-f" data-f2><label for="dFeed1">Feed 2 · right / bottom</label><select id="dFeed1"${dd}>${feedOpts(fs[1], '— none —')}</select></div>
  ${p.set ? note('Set from admin: ' + esc(feedText(fs)) + '. A change made at the panel\'s own screen afterwards wins until you set one again.') : ''}
  <div class="clo-f" style="margin-top:14px"><label>Screen rotation</label>${rad('rot', [[0, '0°'], [90, '90°'], [180, '180°'], [270, '270°']], p.rot)}
    ${note('A change restarts the panel, which takes about half a minute. A turn made at the panel\'s own screen afterwards wins until you set one again.' + (p.id === 'p3' ? ' Last turned at the screen.' : ''))}</div>
  <h3 class="clo-dh">At the screen</h3>
  <label class="clo-chk"><input type="checkbox" id="dLock"${p.lock ? ' checked' : ''}${dd}><span>Lock: nobody at the screen can change what it shows</span></label>
  <div data-lock>
    <div class="clo-f"><label for="dPin">PIN to unlock at the screen</label><input type="password" id="dPin" inputmode="numeric" maxlength="8" autocomplete="new-password" style="max-width:150px" placeholder="${p.pin ? 'PIN set (unchanged)' : 'No PIN'}"${dd}>
      ${p.pin ? `<label class="clo-chk" style="margin:8px 0 0"><input type="checkbox" id="dPinClr"${dd}><span>Remove the PIN</span></label>` : ''}
      ${note('4 to 8 digits. Without one, only you can unlock it, from here. The PIN reaches the panel over plain HTTP, like the Planning Center key: it stops someone at the screen, not someone reading the network.')}</div>
    <div class="clo-f"><label for="dUnlock">Locks again after this many minutes with no touch</label><input type="number" id="dUnlock" min="1" max="60" step="1" style="max-width:100px" value="${p.unlock || 5}"${dd}></div></div>
  <label class="clo-chk"><input type="checkbox" id="dBack"${p.ret ? ' checked' : ''}${dd}><span>Go back to the feeds set here after a while with no touch</span></label>
  <div data-back class="clo-f"><label for="dRet">Minutes with no touch</label><input type="number" id="dRet" min="1" max="60" step="1" style="max-width:100px" value="${p.ret || 10}"${dd}>${note('Puts back Feed 1 and Feed 2 above, one feed or two, after a change made at the screen. Needs feeds set here.')}</div>
  <div data-tm data-own="${p.timers ? 1 : 0}"><h3 class="clo-dh">ProPresenter timers</h3>
    <label class="clo-chk"><input type="checkbox" id="dTmOwn"${p.timers ? ' checked' : ''}${dd}><span>Use settings of its own for this panel</span></label>
    <div data-tmf><div class="row" style="flex-wrap:nowrap"><div class="clo-f" style="flex:1"><label for="dAmber">Amber at</label><input type="text" id="dAmber" value="2:00" placeholder="2:00" style="max-width:90px"${dd}></div><div class="clo-f" style="flex:1"><label for="dRed">Red at</label><input type="text" id="dRed" value="0:30" placeholder="0:30" style="max-width:90px"${dd}></div></div>
      <div class="clo-f"><label>Timers to show</label><div class="clo-timers">${['Countdown to service', 'Sermon', 'Worship set', 'Stage message'].map((t, i) => `<label class="clo-chk" style="margin:0"><input type="checkbox"${i < 2 ? ' checked' : ''}${dd}><span>${t}</span></label>`).join('')}</div></div>
      ${note('Time left, as minutes:seconds (2:00) or seconds (90). Tick up to four timers; with none ticked the panel shows the first four ProPresenter has.')}</div>
    <div data-tmn>${note('Without settings of its own, this panel follows Timers on panels, on the ProPresenter page.')}</div></div>
  <h3 class="clo-dh">Planning Center</h3>
  <div class="clo-f"><label for="dSvc">Service mode</label><select id="dSvc"${dd}>${[['off', 'Off'], ['show', 'Show only'], ['control', 'Control only'], ['both', 'Show and control']].map(o => `<option value="${o[0]}"${o[0] === svc ? ' selected' : ''}>${o[1]}</option>`).join('')}</select><div class="clo-n" id="dSvcHelp"></div></div>
  <div class="clo-f" data-svt><label for="dSvt">Planning Center service type</label><select id="dSvt"${dd}><option>The panel picks</option>${SVC.map(s => `<option>${s}</option>`).join('')}</select></div>
  <h3 class="clo-dh">Device</h3>
  <div class="clo-kv" id="devInfo">${dev.map(r => `<span>${r[0]}</span><span>${esc(r[1])}</span>`).join('')}</div>
  ${updRow}
  ${C.vo ? '' : `<div class="row" style="margin-top:12px">${p.avail && !p.upd && p.online ? `<button type="button" class="btn sm" data-clo-act="dupdate" title="Installs at a quiet moment: no new caption line for a while">Update</button><button type="button" class="btn sm" data-clo-act="dupdate" data-now="1">Update now</button>` : ''}${p.online ? `<button type="button" class="btn sm" data-clo-act="dreboot" data-n="${esc(p.name)}"${p.reboot ? '' : ' disabled title="The panel is not connected to Relay right now, or its firmware is too old to restart from here"'}>${ic('cloRefresh')}Reboot</button>` : ''}</div>`}
  </form>`;
}
function drawerSync() {
  const r = $('drawer'); if (!r) return; const g = s => r.querySelector(s);
  const cnt = +(g('input[name=nfeeds]:checked') || {value: 1}).value;
  g('[data-f2]').hidden = cnt !== 2; g('[data-f1l]').textContent = cnt === 2 ? ' · left / top' : ' · full screen';
  g('[data-lock]').hidden = !g('#dLock').checked; g('[data-back]').hidden = !g('#dBack').checked;
  const pc = g('#dPinClr'); if (pc) g('#dPin').disabled = pc.checked || C.vo;
  const tmOn = g('#dFeed0').value === 'timers' || (cnt === 2 && g('#dFeed1').value === 'timers') || g('[data-tm]').dataset.own === '1';
  g('[data-tm]').hidden = !tmOn; g('[data-tmf]').hidden = !g('#dTmOwn').checked; g('[data-tmn]').hidden = g('#dTmOwn').checked;
  const m = g('#dSvc').value; g('[data-svt]').hidden = m === 'off';
  g('#dSvcHelp').textContent = SERVICE_HELP[m] + (m === 'control' || m === 'both' ? ' Presses run as the person who made the Planning Center token. The panel gets a key, but it travels over plain HTTP: anyone who can read the network could use it.' : '');
}

/* ================================================================== ALERTS */
const ALERT_ICONS = {
  bell: ['Bell', '<path d="M6 16v-5a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5h4"/>'],
  mic: ['Microphone', '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/>'],
  headphones: ['Headphones', '<path d="M4 16v-4a8 8 0 0 1 16 0v4"/><rect x="3" y="14" width="4" height="6" rx="1.5"/><rect x="17" y="14" width="4" height="6" rx="1.5"/>'],
  keys: ['Keys', '<rect x="3" y="6" width="18" height="12" rx="1.5"/><path d="M8 6v7M12 6v7M16 6v7"/>'],
  drums: ['Drums', '<ellipse cx="12" cy="10" rx="8" ry="3"/><path d="M4 10v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7M6 3l4 5M18 3l-4 5"/>'],
  guitar: ['Guitar', '<circle cx="8.5" cy="15.5" r="4.5"/><path d="M11.5 12.5L19 5M17 3l4 4"/>'],
  person: ['Person', '<circle cx="12" cy="8" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/>'],
  people: ['Group', '<circle cx="9" cy="8" r="3"/><circle cx="17" cy="9" r="2.5"/><path d="M3 19a6 6 0 0 1 12 0M15.5 14.5A5 5 0 0 1 21 19"/>'],
  video: ['Video', '<rect x="3" y="7" width="12" height="10" rx="2"/><path d="M15 11l6-3v8l-6-3z"/>'],
  sliders: ['Mixer', '<path d="M6 4v16M12 4v16M18 4v16"/><rect x="4" y="12" width="4" height="3" rx="1"/><rect x="10" y="7" width="4" height="3" rx="1"/><rect x="16" y="14" width="4" height="3" rx="1"/>']
};
const aSvg = n => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${(ALERT_ICONS[n] || ALERT_ICONS.bell)[1]}</svg>`;
const LEVELS = {info: 'Info (blue)', attention: 'Attention (amber, flashes)', urgent: 'Urgent (red, flashes)'};
const ASTATE = {on: 'Flashing', seen: 'Seen', missed: 'Not seen', cancelled: 'Stopped'};
C.al = {
  dirty: false, autoStop: 120, signin: false, msgS: 15, banS: 120, screensOpen: ['Booth TV'],
  replies: 'On my way\nCan\'t right now\nGive me 2 minutes',
  buttons: [
    {name: 'FOH booth', icon: 'sliders', level: 'urgent', panels: ['p1', 'p2', 'p5'], screens: ['Booth TV'], stage: true, state: 'on', from: 'Sam Lee'},
    {name: 'Worship leader', icon: 'mic', level: 'attention', panels: ['p5'], screens: ['Booth TV'], stage: false, state: 'seen', seenOn: 'Booth TV', reply: 'On my way'},
    {name: 'Drum riser', icon: 'drums', level: 'urgent', panels: ['p3'], screens: [], stage: false, state: 'idle'}
  ],
  presets: [
    {text: '5 minutes', level: 'info', banner: false, panels: ['p1', 'p2', 'p3'], screens: ['Booth TV']},
    {text: 'Wrap up', level: 'attention', banner: false, panels: ['p1', 'p2'], screens: []},
    {text: 'Service starts in 10, please take your seats', level: 'info', banner: true, panels: ['p3'], screens: ['Nursery TV']}
  ],
  free: {panels: ['p5'], screens: ['Booth TV'], level: 'info'}
};
const aItem = (o, i) => o === 'b' ? C.al.buttons[i] : o === 'm' ? C.al.presets[i] : C.al.free;
const aDirty = () => { C.al.dirty = true; const d = $('alertDirty'); if (d) d.hidden = false; };
const aSum = () => { const on = C.al.buttons.filter(b => b.state === 'on').length, n = C.al.buttons.length; return on ? `<span class="live">${on} flashing</span>` : n ? n + (n === 1 ? ' button' : ' buttons') : ''; };
function aTarget(o, i, label, noScr) {
  const it = aItem(o, i), dd = D();
  return `<div class="clo-f"><label>${label}</label><div class="clo-at">${C.panels.length ? C.panels.map(p => `<label class="clo-chk" style="margin:0"><input type="checkbox" data-clo-in="alchk" data-o="${o}" data-i="${i}" data-p="${p.id}"${it.panels.includes(p.id) ? ' checked' : ''}${dd}><span>${esc(p.name)}${p.online ? '' : ' (offline)'}</span></label>`).join('') : '<span class="clo-mut">No panels have checked in yet.</span>'}</div>
    ${noScr ? '' : `<input type="text" data-clo-in="alscr" data-o="${o}" data-i="${i}" placeholder="Browser screens, separated by commas" aria-label="${label}: browser screens" value="${esc(it.screens.join(', '))}"${dd}>`}</div>`;
}
const aLevel = (o, i, fb, lbl) => `<select data-clo-in="allv" data-o="${o}" data-i="${i}" aria-label="${lbl}"${D()}>${Object.keys(LEVELS).map(k => `<option value="${k}"${(aItem(o, i).level || fb) === k ? ' selected' : ''}>${LEVELS[k]}</option>`).join('')}</select>`;
function aStateText(b) {
  if (!b.state || b.state === 'idle') return ['', ''];
  let t = ASTATE[b.state];
  if (b.state === 'on' && b.from) t += ' · from ' + b.from;
  if (b.state === 'seen' && b.seenOn) t += ' on ' + b.seenOn;
  if (b.state === 'seen' && b.reply) t += ' — “' + b.reply + '”';
  return [b.state, t];
}
function renderBtns() {
  const dd = D(), l = $('alertList'); if (!l) return;
  l.innerHTML = C.al.buttons.length ? C.al.buttons.map((b, i) => {
    const s = aStateText(b);
    return `<div class="clo-ac"><div class="clo-ah"><span class="clo-ai">${aSvg(b.icon)}</span>
      <input type="text" data-clo-in="alname" data-i="${i}" maxlength="32" placeholder="FOH booth" aria-label="Button name" value="${esc(b.name)}"${dd}>
      <select data-clo-in="alicon" data-i="${i}" aria-label="Icon"${dd}>${Object.keys(ALERT_ICONS).map(k => `<option value="${k}"${b.icon === k ? ' selected' : ''}>${ALERT_ICONS[k][0]}</option>`).join('')}</select>
      ${aLevel('b', i, 'urgent', 'Alert level')}<span class="clo-as ${s[0]}" data-astate="${i}">${s[1]}</span>
      ${C.vo ? '' : `<button type="button" class="btn sm" data-clo-act="alrm" data-o="b" data-i="${i}">Remove</button>`}</div>
      ${aTarget('b', i, 'Panels it flashes', 1)}
      <div class="clo-f" style="margin-bottom:6px"><label>Browser screens it flashes (names, separated by commas)</label><input type="text" data-clo-in="alscr" data-o="b" data-i="${i}" placeholder="Booth TV" value="${esc(b.screens.join(', '))}"${dd}>${note(C.al.screensOpen.length ? 'Open now: ' + C.al.screensOpen.join(', ') : 'No alert screen is open right now.')}</div>
      <label class="clo-chk" style="margin:0"><input type="checkbox" data-clo-in="alstage" data-i="${i}"${b.stage ? ' checked' : ''}${dd}><span>Also show on the ProPresenter stage display (cleared when acknowledged)</span></label></div>`;
  }).join(''): '<div class="clo-empty">No alert buttons yet. Add one for each place a musician may need to reach.</div>';
  const s = $('alertsSum'); if (s) s.innerHTML = aSum();
  const c = document.querySelector('[data-cnt=buttons]'); if (c) c.textContent = C.al.buttons.length;
}
function renderMsgs() {
  const dd = D(), l = $('msgList'); if (!l) return;
  l.innerHTML = C.al.presets.length ? C.al.presets.map((m, i) => `<div class="clo-ac"><div class="clo-ah"><input type="text" data-clo-in="almsg" data-i="${i}" maxlength="80" placeholder="5 minutes" aria-label="Message" value="${esc(m.text)}"${dd}>
      ${aLevel('m', i, 'info', 'Message level')}<label class="clo-chk" style="margin:0"><input type="checkbox" data-clo-in="albanner" data-i="${i}"${m.banner ? ' checked' : ''}${dd}><span>Banner</span></label>
      ${C.vo ? '' : `<button type="button" class="btn sm" data-clo-act="alrm" data-o="m" data-i="${i}">Remove</button>`}</div>${aTarget('m', i, 'Shown on')}</div>`).join('') : '<div class="clo-empty">No preset messages yet. Add the ones you send often: “Wrap up”, “5 minutes”.</div>';
  const f = $('msgFree'); if (f) f.innerHTML = aTarget('f', 0, 'Panels and screens') + `<div class="row" style="margin-top:2px"><span>Typed messages are</span>${aLevel('f', 0, 'info', 'Typed message level')}</div>`;
}
function renderLinks() {
  const b = $('alertLinks'); if (!b) return;
  const base = C.al.signin ? SECURE : HOST, rows = [['Sender page (phones)', '/alert', base]], seen = {};
  C.al.buttons.forEach(x => x.screens.forEach(n => { if (!seen[n.toLowerCase()]) { seen[n.toLowerCase()] = 1; rows.push(['Screen “' + n + '”', '/screen?alert=' + encodeURIComponent(n), HOST]); } }));
  b.innerHTML = rows.map(r => `<div class="clo-link"><span>${esc(r[0])}:</span><a class="clo-mono" href="#alerts" data-clo-act="toast" data-msg="Opens ${esc(r[2] + r[1])}">${esc(r[2] + r[1])}</a>${copyBtn(r[2] + r[1])}</div>`).join('');
}
const HIST = [
  ['Sun 15 Nov, 10:41', 'Sam Lee', 'FOH booth', 'Alert · urgent · to Lobby left, Lobby right, Booth TV', 'Seen on Lobby left after 4 s — “On my way”'],
  ['Sun 15 Nov, 10:22', 'Dana Reyes', '5 minutes', 'Message · info · to Lobby left, Lobby right, Nursery', ''],
  ['Sun 15 Nov, 10:05', 'Priya Nair', 'Worship leader', 'Alert · attention · to Booth TV', 'Not seen'],
  ['Sun 15 Nov, 09:48', 'Sam Lee', 'Drum riser', 'Alert · urgent · to Nursery', 'Stopped by the sender'],
  ['Wed 11 Nov, 19:12', 'Someone', 'Wrap up', 'Message · attention · to Youth room', '']
];
function histBody() {
  if (C.hist === 'error') return '<tr><td colspan="4" class="clo-mut">The history could not be read.</td></tr>';
  if (C.hist === 'empty') return '<tr><td colspan="4" class="clo-mut">Nothing sent yet.</td></tr>';
  if (C.hist === 'loading') return '<tr><td colspan="4" class="clo-mut">Loading…</td></tr>';
  return HIST.map(h => `<tr class="clo-hd"><td class="clo-mut">${h[0]}</td><td>${h[1]}</td><td class="w">${h[2]}<small>${h[3]}</small></td><td class="w">${h[4]}</td></tr>`).join('');
}
P.alerts = () => {
  const dd = D(), A = C.al;
  const tabs = tabsBar('alerts', [['buttons', 'Buttons', A.buttons.length], ['messages', 'Stage messages', A.presets.length], ['replies', 'Replies'], ['notap', 'When nobody taps'], ['history', 'History'], ['links', 'Where to send from']],
    C.vo ? '' : `<div class="clo-save"><span class="clo-mut" style="font-size:12.5px" id="alertDirty"${A.dirty ? '' : ' hidden'}>Unsaved changes</span><button type="button" class="btn p" id="alertSave" data-clo-act="alsave">Save alerts</button></div>`);
  const addBtn = id => C.vo ? '' : `<div class="row" style="margin-top:12px"><button type="button" class="btn" id="${id}" data-clo-act="${id}">${ic('plus')}${id === 'alertAdd' ? 'Add button' : 'Add message'}</button></div>`;
  return head('Alerts', 'Buttons a musician taps to flash panels and screens red and white until someone there taps one.', {sum: aSum(), sumId: 'alertsSum', demo: 'Demo: no alerts.manage'}) + tabs +
  pane('alerts', 'buttons', card('Buttons', 'One button per place a musician may need to reach. Each flashes its panels and screens until someone there taps.', `<div id="alertList"></div>${addBtn('alertAdd')}`), 'buttons') +
  pane('alerts', 'messages', card('Stage messages', '', `${note('Words from the booth to the stage, with nothing to tap. Each has a level: info is a steady blue, attention flashes amber, urgent flashes red and white. A message sits in the middle of the screen; a banner is a strip across the top that stays up longer. They show over whatever a panel or screen is already showing, and end on their own or when someone clears them on the sender page.')}
      <div id="msgList"></div>${addBtn('msgAdd')}
      <div class="clo-f" style="margin-top:16px"><label>Typed messages go to</label><div id="msgFree"></div>${note('The sender page shows a box to type a message once you pick at least one panel or screen here.')}</div>
      <div class="row"><label for="msgSeconds" style="margin:0">A message stays</label><input id="msgSeconds" type="number" min="3" max="120" style="width:5.5em" value="${A.msgS}" data-clo-in="alsec"${dd}><span class="clo-mut">seconds</span>
      <label for="bannerSeconds" style="margin:0 0 0 16px">A banner stays</label><input id="bannerSeconds" type="number" min="30" max="3600" style="width:5.5em" value="${A.banS}" data-clo-in="alsec"${dd}><span class="clo-mut">seconds</span></div>`)) +
  pane('alerts', 'replies', card('Replies', '', `${note('A short list a panel or screen can tap when it acknowledges, so nobody needs a keyboard. The sender sees the reply next to “Seen”. One reply per line, up to 6 of 24 characters. Leave it empty for a plain acknowledge. Tapping anywhere else on the flashing screen still acknowledges with no reply.')}
      <div class="clo-f"><label for="alertReplies">Replies</label><textarea id="alertReplies" rows="4" style="max-width:20em" placeholder="On my way&#10;Can't right now" data-clo-in="alsec"${dd}>${esc(A.replies)}</textarea></div>
      <div class="clo-n" style="margin-top:0">Preview on the flashing screen:</div><div class="row" style="margin-top:8px">${A.replies.split('\n').filter(Boolean).slice(0, 6).map(r => `<span class="badge">${esc(r.slice(0, 24))}</span>`).join('') || '<span class="clo-mut" style="font-size:12px">Plain acknowledge</span>'}</div>`)) +
  pane('alerts', 'notap', card('When nobody taps', '', `
    <div class="clo-f"><label for="alertAutoStop">Stop flashing after</label><select id="alertAutoStop" style="width:auto" data-clo-in="alsec"${dd}>${[[30, '30 seconds'], [60, '1 minute'], [120, '2 minutes'], [180, '3 minutes'], [300, '5 minutes'], [600, '10 minutes']].map(o => `<option value="${o[0]}"${A.autoStop === o[0] ? ' selected' : ''}>${o[1]}</option>`).join('')}</select>
      ${note('The sender then sees “Not seen” and can send again. The flash is once a second, fixed: well under the rate where flashing becomes a seizure risk.')}</div>
    <div class="clo-f" style="margin-bottom:0"><label class="clo-chk" style="font-size:14px"><input type="checkbox" id="alertSignin" data-clo-in="alsign"${A.signin ? ' checked' : ''}${dd}> Only signed-in people can send</label>
      ${note('The sender page then moves to the operator panel\'s <span class="clo-mono">https://</span> address and asks for a sign-in. Anyone whose role can send alerts may use it, including a Member, who cannot open this panel. Their account name is shown on the screen they flash. Panels send as they always have.')}</div>`)) +
  pane('alerts', 'history', card('History', '', `${note('Who sent what, to which screens, and how long until someone saw it. Kept for <span id="alertHistKeep">30</span> days.')}
    <div class="row" style="margin-bottom:12px"><span class="clo-l" style="margin:0">Demo state</span><div class="tg clo-tg" role="group">${[['rows', 'Rows'], ['empty', 'Empty'], ['loading', 'Loading'], ['error', 'Error']].map(o => `<button type="button" data-clo-act="alhist" data-v="${o[0]}" aria-pressed="${C.hist === o[0]}">${o[1]}</button>`).join('')}</div></div>
    <div class="clo-sc"><table class="clo-t"><thead><tr><th>When</th><th>Who</th><th>What</th><th>Seen</th></tr></thead><tbody id="alertHistList">${histBody()}</tbody></table></div>
    <div class="row" style="margin-top:12px" id="alertHistMoreRow"${C.hist === 'rows' ? '' : ' hidden'}><button type="button" class="btn" id="alertHistMore" data-clo-act="toast" data-msg="Showing older entries">Show older</button></div>`)) +
  pane('alerts', 'links', card('Where to send from, and what flashes', 'Hand the sender link to the band, not the room.', `<div id="alertLinks"></div>${note('Unless sending needs a sign-in, anyone with the sender link can send, so hand it to the band, not the room. A panel sends from its <em>Alert buttons</em> feed, set on its card under Panels. A browser screen flashes when it is opened as <span class="clo-mono">/screen?alert=&lt;name&gt;</span> with a name a button lists; a TV with no touch needs a mouse click or key press to acknowledge, or it flashes until the auto-stop.')}`));
};
AFTER.alerts = () => { renderBtns(); renderMsgs(); renderLinks(); };
ACT.alertAdd = () => { C.al.buttons.push({name: '', icon: 'bell', level: 'urgent', panels: [], screens: [], state: 'idle'}); renderBtns(); renderLinks(); aDirty(); const i = document.querySelectorAll('#alertList [data-clo-in=alname]'); i.length && i[i.length - 1].focus(); };
ACT.msgAdd = () => { C.al.presets.push({text: '', level: 'info', banner: false, panels: [], screens: []}); renderMsgs(); const c = document.querySelector('[data-cnt=messages]'); c && (c.textContent = C.al.presets.length); aDirty(); const i = document.querySelectorAll('#msgList [data-clo-in=almsg]'); i.length && i[i.length - 1].focus(); };
ACT.alrm = b => { (b.dataset.o === 'b' ? C.al.buttons : C.al.presets).splice(+b.dataset.i, 1); renderBtns(); renderMsgs(); renderLinks(); const c = document.querySelector('[data-cnt=messages]'); c && (c.textContent = C.al.presets.length); aDirty(); };
ACT.alsave = () => { C.al.dirty = false; const d = $('alertDirty'); d && (d.hidden = true); toast('Alerts saved'); };
ACT.alhist = b => { C.hist = b.dataset.v; const t = $('alertHistList'); t.innerHTML = histBody(); $('alertHistMoreRow').hidden = C.hist !== 'rows'; document.querySelectorAll('[data-clo-act=alhist]').forEach(x => x.setAttribute('aria-pressed', x === b)); };
IN.alname = el => { C.al.buttons[+el.dataset.i].name = el.value; aDirty(); };
IN.alicon = el => { C.al.buttons[+el.dataset.i].icon = el.value; renderBtns(); aDirty(); };
IN.allv = el => { aItem(el.dataset.o, +el.dataset.i).level = el.value; aDirty(); };
IN.alchk = el => { const it = aItem(el.dataset.o, +el.dataset.i); it.panels = it.panels.filter(x => x !== el.dataset.p); if (el.checked) it.panels.push(el.dataset.p); aDirty(); };
IN.alscr = el => { aItem(el.dataset.o, +el.dataset.i).screens = el.value.split(',').map(x => x.replace(/\s+/g, ' ').trim()).filter(Boolean); aDirty(); if (el.dataset.o === 'b') renderLinks(); };
IN.alstage = el => { C.al.buttons[+el.dataset.i].stage = el.checked; aDirty(); };
IN.almsg = el => { C.al.presets[+el.dataset.i].text = el.value; aDirty(); };
IN.albanner = el => { C.al.presets[+el.dataset.i].banner = el.checked; aDirty(); };
IN.alsign = el => { C.al.signin = el.checked; renderLinks(); aDirty(); };
IN.alsec = el => { if (el.id === 'alertAutoStop') C.al.autoStop = +el.value; if (el.id === 'alertReplies') C.al.replies = el.value; if (el.id === 'msgSeconds') C.al.msgS = +el.value; if (el.id === 'bannerSeconds') C.al.banS = +el.value; aDirty(); };

/* ================================================================== VIEWER LINKS */
function qrSvg(seed) {
  const n = 25; let h = 2166136261;
  for (const c of seed) h = Math.imul(h ^ c.charCodeAt(0), 16777619) >>> 0;
  const rnd = () => { h ^= h << 13; h >>>= 0; h ^= h >>> 17; h ^= h << 5; h >>>= 0; return h / 4294967296; };
  const fin = (x, y) => `<path fill-rule="evenodd" d="M${x} ${y}h7v7h-7zM${x + 1} ${y + 1}v5h5v-5z"/><path d="M${x + 2} ${y + 2}h3v3h-3z"/>`;
  const inF = (x, y) => (x < 8 && y < 8) || (x > n - 9 && y < 8) || (x < 8 && y > n - 9);
  let d = '';
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) if (!inF(x, y) && rnd() > .52) d += `M${x} ${y}h1v1h-1z`;
  return `<svg viewBox="0 0 ${n} ${n}" fill="currentColor" shape-rendering="crispEdges" aria-hidden="true">${fin(0, 0)}${fin(n - 7, 0)}${fin(0, n - 7)}<path d="${d}"/></svg>`;
}
const HOSTS = ['http://192.168.1.40/', 'http://10.20.0.14/'];
const VIEW_GROUPS = [
  ['For the room', [['Translation', 'translation'], ['Transcription', 'transcription'], ['Both', 'both'], ['Chooser page', ''], ['Screen (kiosk display)', 'screen']]],
  ['For your switcher', [['Overlay (OBS / vMix / ProPresenter)', 'overlay']]]
];
const urlRow = (label, path) => `<div class="clo-ur"><b>${label}</b><div class="clo-ul">${HOSTS.map(h => { const href = h + path; return `<div class="it"><span class="clo-qr" data-href="${href}">${qrSvg(href)}</span><span class="ln"><a href="#urls" class="clo-mono" data-clo-act="toast" data-msg="Opens ${href}">${href}</a>${copyBtn(href)}</span></div>`; }).join('')}</div></div>`;
const FP = 'A4:1F:9C:7E:03:B8:52:D1:6A:F0:2C:8E:47:B9:15:DD:3E:70:A6:C2:88:19:FB:64:0D:E5:93:2A:71:CC:3B:5F';
function secureBox() {
  return card('Operator panel (HTTPS)', 'How the panel itself is reached.', `<div id="secure">
    ${note('Relay is using port 8443 for the operator panel because 443 is already taken on this machine.')}
    ${C.cert ? `<div class="clo-warnbox">The panel certificate was replaced (2026-11-12 08:14), so its fingerprint changed. Check the new one below against what your browser shows before you trust it again.</div>` : ''}
    <div class="clo-ul" style="gap:8px">${['https://relay.local:8443/', 'https://192.168.1.40:8443/'].map(u => `<div class="ln" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><a class="clo-mono" style="color:var(--fg)" href="#urls" data-clo-act="toast" data-msg="Opens ${u}">${u}</a>${copyBtn(u)}</div>`).join('')}</div>
    <div style="margin-top:14px">Certificate SHA-256 fingerprint:</div><div class="clo-fp clo-mono">${FP}</div>
    ${note('Valid until 2036-11-14. It is self-signed, so the browser warns once; compare this fingerprint with the one it shows before you accept.', '', '')}
    <div class="row" style="margin-top:12px"><span class="clo-l" style="margin:0">Demo state</span><div class="tg clo-tg" role="group"><button type="button" data-clo-act="cert" data-v="0" aria-pressed="${!C.cert}">Normal</button><button type="button" data-clo-act="cert" data-v="1" aria-pressed="${C.cert}">Certificate changed</button></div></div></div>`);
}
P.urls = () => head('Viewer links', 'Open these on a screen or paste them into another app.', {vo: false, nodemo: true}) +
  `<div class="clo-g b"><div id="urls" style="display:grid;gap:14px">` +
  VIEW_GROUPS.map(g => card(g[0], g[0] === 'For the room' ? 'Audience-facing views the room opens directly. One link per network the machine is on.' : 'Setup feeds you paste into another application.', g[1].map(v => urlRow(v[0], v[1])).join('') + (g[0] === 'For your switcher' ? '' : ''))).join('') + `</div>
  <div style="display:grid;gap:14px">${secureBox()}${card('Notes', '', note('Screen defaults to translation plus the first live language; append <span class="clo-mono">?stream=&amp;lang=</span> to pin a specific room display. The overlay link here uses the defaults; to choose a layout, language, size and colours, or save it by name, use <a class="clo-a" href="#overlay">Broadcast overlay</a>. Only the LAN address is shown; 127.0.0.1 works only on the machine running Relay.'))}</div></div>`;
ACT.cert = b => { C.cert = b.dataset.v === '1'; route(); };

/* ================================================================== BROADCAST OVERLAY */
const OVD = {preset: '', stream: 'translation', lang: '', lang2: '', lines: 2, font: 54, w: 1920, h: 1080, hold: 4000, safe: 5, plate: .55, matte: false, bg: 'transparent', pos: 'bottom', align: 'center', maxw: 88, family: 'sans', edge: 'plate', color: 'FFFFFF', platec: '000000', mode: 'rollup', finals: false, labels: true};
const OVP = {
  lower: {label: 'Lower third', pos: 'bottom', align: 'center', lines: 2, font: 54, maxw: 80, edge: 'plate', plate: .6},
  top: {label: 'Top strip', pos: 'top', align: 'center', lines: 1, font: 46, maxw: 100, edge: 'plate', plate: .75},
  slate: {label: 'Accessibility slate', pos: 'middle', align: 'left', lines: 6, font: 84, maxw: 100, edge: 'none', plate: 0, family: 'hyper', bg: '#000000', hold: 0},
  dual: {label: 'Two languages', stream: 'both', pos: 'bottom', align: 'center', lines: 1, font: 44, maxw: 85, edge: 'plate', plate: .6}
};
const OVSUB = {lower: 'Bottom, two lines', top: 'Top, one wide line', slate: 'Full screen, large type', dual: 'Two languages, labelled'};
const OVBARS = {lower: [[76, '']], top: [[7, '']], slate: [[20, ''], [34, ''], [48, ''], [62, '']], dual: [[64, ''], [78, 'b']]};
const HINTS = {
  mode: {rollup: 'Lines scroll up as new words arrive, like TV live captions.', popon: 'Finished sentences appear as a block and the next block replaces it. Calmer, a little later.'},
  out: {transparent: 'For OBS, vMix and ProPresenter. Nothing behind the text.', green: 'For a switcher input keyed on green.', matte: 'White-on-black key signal for an ATEM luma key. Pair it with a Black copy as the fill.', black: 'Solid black: a full-screen slate on its own output, or the fill for a key matte.'}
};
const ovBase = p => Object.assign({}, OVD, (OVP[p] || {}), {});
const ovResolved = raw => { const o = ovBase(raw.preset || ''); Object.keys(raw).forEach(k => { if (k in o) o[k] = raw[k]; }); delete o.label; return o; };
function ovDiff(s) {
  const b = ovBase(s.preset), out = {}; if (s.preset) out.preset = s.preset;
  Object.keys(OVD).forEach(k => { if (k !== 'preset' && String(s[k]) !== String(b[k])) out[k] = s[k]; });
  return out;
}
const ovOutput = s => s.matte ? 'matte' : String(s.bg).toUpperCase() === '#00B140' ? 'green' : ['#000000', '#000', 'BLACK'].includes(String(s.bg).toUpperCase()) ? 'black' : 'transparent';
function ovSetOut(v) { const S = C.ov.S; S.matte = v === 'matte'; S.bg = v === 'green' ? '#00B140' : v === 'black' ? '#000000' : 'transparent'; }
function ovQuery(o) { const p = new URLSearchParams(); Object.keys(o).forEach(k => { let v = o[k]; if (typeof v === 'boolean') v = v ? '1' : '0'; if (['bg', 'color', 'platec'].includes(k)) v = String(v).replace(/^#/, ''); p.set(k, String(v)); }); return p.toString(); }
const ovLink = () => C.ov.editing ? HOST + '/overlay/' + C.ov.editing : HOST + '/overlay?' + ovQuery(ovDiff(C.ov.S));
function ovInit() {
  const S = ovBase('lower'); S.preset = 'lower'; delete S.label; if (live) S.lang = 'SPANISH';
  C.ov = {S, editing: null, backdrop: 'program', guide: true, sample: true, tick: 2,
    saved: [{id: 'stream-spanish', name: 'Stream – Spanish', settings: {preset: 'lower', lang: 'SPANISH'}}, {id: 'sanctuary-slate', name: 'Sanctuary slate', settings: {preset: 'slate'}}, {id: 'hall-es-pt', name: 'Fellowship hall – Spanish and Portuguese', settings: {preset: 'dual', lang: 'SPANISH', lang2: 'PORTUGUESE'}}]};
}
const seg = (k, opts, lbl, hint) => `<div class="clo-f"><span class="clo-l">${lbl}</span><div class="tg" data-k="${k}" role="group">${opts.map(o => `<button type="button" data-v="${o[0]}" aria-pressed="false"${C.vo ? ' disabled' : ''}>${o[1]}</button>`).join('')}</div>${hint ? `<p class="clo-n" id="${hint}"></p>` : ''}</div>`;
const rng = (id, k, mn, mx, stp, lbl) => `<div class="clo-f"><label for="${id}">${lbl}</label><div class="clo-rng"><input id="${id}" type="range" min="${mn}" max="${mx}"${stp ? ` step="${stp}"` : ''} data-k="${k}"${D()}><output></output></div></div>`;
const optSel = (id, k, lbl, opts, extra) => `<div class="clo-f"${extra ? ` id="${extra}"` : ''}><label for="${id}"${id === 'ovLang' ? ' id="ovLangLbl"' : ''}>${lbl}</label><select id="${id}" data-k="${k}"${D()}>${opts}</select></div>`;
function ovLangOpts(orig) {
  return `<option value="">${orig ? 'Original (English)' : 'First live language'}</option>` + LANGS.map(l => `<option value="${l[0]}">${l[1]}${isLive(l[0]) ? ' · live' : ''}</option>`).join('');
}
P.overlay = () => {
  if (!C.ov) ovInit();
  const tab = C.tab.overlay || 'build';
  const ctl = `<div class="clo-ctl">
    ${C.vo ? '' : `<div class="clo-ed" id="ovEditing" hidden>Editing <strong id="ovEditingName"></strong>. Changes reach the browser source the next time it loads. <button type="button" class="btn sm" id="ovEditDone" data-clo-act="ovdone">Start a new overlay</button></div>`}
    <div class="card"><h3>Layout</h3><div class="clo-pre" id="ovPresets">${Object.keys(OVP).map(k => `<button type="button" class="clo-pt" data-p="${k}" data-clo-act="ovpreset"${D()}><div class="th${k === 'slate' ? ' dark' : ''}">${OVBARS[k].map(b => `<i class="${b[1]}" style="top:${b[0]}%"></i>`).join('')}</div><span>${OVP[k].label}</span><small>${OVSUB[k]}</small></button>`).join('')}</div></div>
    <div class="card"><h3>Captions</h3>
      ${seg('stream', [['translation', 'Translation'], ['transcription', 'Original'], ['both', 'Two, stacked']], 'Show')}
      <div class="clo-two">${optSel('ovLang', 'lang', 'Language', ovLangOpts(false), 'ovLangF')}${optSel('ovLang2', 'lang2', 'Second language', ovLangOpts(true), 'ovLang2F')}</div>
      ${rng('ovLines', 'lines', 1, 8, 0, 'Lines on screen')}
      <label class="clo-chk"><input type="checkbox" data-k="finals"${D()}><span>Finished lines only<small>Waits for each sentence to settle, so words never change on air. About a second later than live.</small></span></label>
      ${seg('mode', [['rollup', 'Roll-up'], ['popon', 'Pop-on']], 'Style', 'ovModeHint')}
      ${rng('ovHold', 'hold', 0, 30000, 1000, 'Hide after silence')}
      <label class="clo-chk" id="ovLabelsF" style="margin:0"><input type="checkbox" data-k="labels"${D()}><span>Language names over each feed</span></label></div>
    <div class="card"><h3>Position &amp; size</h3>
      ${seg('pos', [['top', 'Top'], ['middle', 'Middle'], ['bottom', 'Bottom']], 'Position')}${seg('align', [['left', 'Left'], ['center', 'Centre'], ['right', 'Right']], 'Alignment')}
      ${rng('ovFont', 'font', 20, 160, 0, 'Text size')}${rng('ovMaxw', 'maxw', 20, 100, 0, 'Max width')}${rng('ovSafe', 'safe', 0, 15, .5, 'Title-safe margin')}
      ${optSel('ovRes', 'res', 'Canvas', '<option value="1920x1080">1920 × 1080 (1080p)</option><option value="1280x720">1280 × 720 (720p)</option><option value="3840x2160">3840 × 2160 (4K)</option><option value="1080x1920">1080 × 1920 (vertical)</option>')}
      <p class="clo-n" style="margin-top:-8px">Match your OBS or vMix canvas. The overlay scales if the source is another size.</p></div>
    <div class="card"><h3>Look</h3>
      ${optSel('ovFamily', 'family', 'Font', '<option value="sans">Sans (default)</option><option value="serif">Serif (Source Serif)</option><option value="rounded">Rounded</option><option value="hyper">Atkinson Hyperlegible (easiest to read)</option><option value="mono">Monospace (caption-box look)</option>')}
      ${seg('edge', [['plate', 'Plate'], ['outline', 'Outline'], ['shadow', 'Shadow'], ['none', 'None']], 'Text edge')}
      <div class="clo-col"><div class="clo-f" style="margin:0"><label for="ovColor">Text colour</label><input id="ovColor" type="color" data-k="color"${D()}></div><div class="clo-f" id="ovPlatecF" style="margin:0"><label for="ovPlatec">Plate colour</label><input id="ovPlatec" type="color" data-k="platec"${D()}></div>
      <div class="clo-f" id="ovPlateF" style="margin:0"><label for="ovPlate">Plate opacity</label><div class="clo-rng"><input id="ovPlate" type="range" min="0" max="1" step="0.05" data-k="plate"${D()}><output></output></div></div></div></div>
    <div class="card"><h3>Output</h3>${seg('out', [['transparent', 'Transparent'], ['green', 'Chroma green'], ['matte', 'Key matte'], ['black', 'Black']], 'Background', 'ovBgHint')}</div></div>`;
  const prev = `<div class="clo-prev">
    <div class="card"><div class="clo-bar" style="margin-bottom:10px"><span class="clo-l">Preview</span><div class="tg clo-tg" id="ovBackdrop">${[['program', 'Over video'], ['checker', 'Transparency'], ['black', 'Black']].map(b => `<button type="button" data-v="${b[0]}" data-clo-act="ovback" aria-pressed="${C.ov.backdrop === b[0]}">${b[1]}</button>`).join('')}</div>
        <label class="clo-chk" style="margin:0 0 0 auto"><input type="checkbox" id="ovGuideLines" data-clo-in="ovguide"${C.ov.guide ? ' checked' : ''}><span>Title-safe guide</span></label></div>
      <div class="clo-stage bk-${C.ov.backdrop}" id="ovStage"><div class="out" id="ovOut"></div><div class="cap" id="ovCap"></div><div class="sf" id="ovSafe-guide" aria-hidden="true"></div></div>
      <label class="clo-chk" style="margin:10px 0 0"><input type="checkbox" id="ovSample" data-clo-in="ovsample"${C.ov.sample ? ' checked' : ''}><span>Sample captions <span class="clo-mut">(off shows the live feed)</span></span></label></div>
    <div class="card"><span class="clo-l">Browser source link</span><div class="clo-url clo-mono" id="ovUrl"></div>
      <div class="row" style="margin-top:10px"><button type="button" class="btn p" id="ovCopy" data-clo-act="ovcopy">Copy link</button><a class="btn" id="ovOpen" href="#overlay" data-clo-act="toast" data-msg="Opens the overlay in a new tab">${ic('cloOpen')}Open</a></div>
      ${C.vo ? '' : `<form class="ovsave" id="ovSaveForm" autocomplete="off" onsubmit="return false" style="margin-top:14px;padding-top:14px;border-top:1px solid var(--border)"><label for="ovName">Save as a named overlay: a short link that keeps working when you change it here</label><div class="clo-sv"><input id="ovName" type="text" maxlength="60" placeholder="Stream – Spanish"><button type="submit" class="btn p" id="ovSave" data-clo-act="ovsave">Save</button></div></form>`}</div>
    <div class="card"><h3>Saved overlays</h3><div class="clo-sc"><table class="clo-tw"><thead><tr><th>Name</th><th>Link</th><th></th></tr></thead><tbody id="ovList"></tbody></table></div></div></div>`;
  const rec = [['OBS Studio', 'Browser source · your canvas size · transparent', ['Sources → + → <strong>Browser</strong>. Name it “Relay captions”.', 'Paste the link into URL. Set width and height to your canvas (Settings → Video → Base resolution).', 'Untick <em>Shutdown source when not visible</em> so captions do not restart when you change scenes.', 'Drag it above your camera sources in every scene that needs captions, or put it in a nested scene shared by all of them.']],
    ['vMix', 'Web Browser input · 1920 × 1080 · transparent', ['Add Input → <strong>Web Browser</strong>, paste the link, and set the size to your output resolution.', 'Put it on an Overlay channel (1–4) and press it on.', 'To caption the stream only, take it on the stream output’s overlay channel and leave the projector output clean.']],
    ['ProPresenter', 'Web Page object on a Prop · stream Look only', ['Create a Prop with a <strong>Web Page</strong> object filling the slide; paste the link.', 'In <strong>Looks</strong>, turn Props on for the stream screen and off for the room screens.', 'Trigger the Prop at the start of the service; it stays up while slides change.']],
    ['Blackmagic ATEM', 'Upstream key · switcher format', ['<strong>Chroma green</strong>: open the link full-screen on a computer whose HDMI feeds one ATEM input, then use a Chroma key on that input.', '<strong>Key matte</strong>: save two overlays, one with <em>Key matte</em> and one with <em>Black</em>; feed each to its own input and set a Luma key with the black one as fill and the matte as key.', 'Turn the key on for program. Keep the computer’s screen saver and notifications off.']]];
  return head('Broadcast overlay', 'Captions for your livestream as a browser source. Pick a layout, adjust it against the preview, then copy the link into OBS, vMix, ProPresenter or your switcher.', {demo: 'Demo: no overlays.manage'}) +
    tabsBar('overlay', [['build', 'Build'], ['guides', 'Setup guides']]) +
    `<div id="ovBuild" data-clo-pane="overlay" data-t="build"${tab === 'build' ? '' : ' hidden'}><div class="clo-g c">${ctl}${prev}</div></div>
    <div id="ovGuides" data-clo-pane="overlay" data-t="guides"${tab === 'guides' ? '' : ' hidden'}><p class="sub" style="margin-top:-4px">Copy the link from the Build tab first. Use the canvas size your switcher runs at; the overlay is transparent everywhere except the captions.</p>
    <div class="clo-rec">${rec.map(r => card(r[0], r[1], `<ol>${r[2].map(s => `<li>${s}</li>`).join('')}</ol>`)).join('')}</div></div>`;
};
function ovSync() {
  const S = C.ov.S, root = $('ovBuild'); if (!root) return;
  const val = k => k === 'out' ? ovOutput(S) : k === 'res' ? S.w + 'x' + S.h : S[k];
  const fmt = (k, v) => k === 'hold' ? (v ? v / 1000 + ' s' : 'never') : k === 'font' ? v + ' px' : k === 'plate' ? Math.round(v * 100) + '%' : k === 'lines' ? String(v) : v + '%';
  root.querySelectorAll('[data-k]').forEach(el => {
    const k = el.dataset.k, v = val(k);
    if (el.classList.contains('tg')) el.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', b.dataset.v === v));
    else if (el.type === 'checkbox') el.checked = !!v;
    else if (el.type === 'color') el.value = '#' + String(v).replace(/^#/, '');
    else el.value = v;
    const o = el.parentElement && el.parentElement.querySelector('output'); if (o) o.textContent = fmt(k, S[k]);
  });
  root.querySelectorAll('.clo-pt').forEach(b => b.classList.toggle('on', b.dataset.p === S.preset));
  const both = S.stream === 'both';
  $('ovLang2F').hidden = !both; $('ovLabelsF').hidden = !both; $('ovLangF').hidden = S.stream === 'transcription';
  $('ovLangLbl').textContent = both ? 'First language' : 'Language';
  $('ovPlatecF').hidden = $('ovPlateF').hidden = S.edge !== 'plate';
  $('ovModeHint').textContent = HINTS.mode[S.mode] || ''; $('ovBgHint').textContent = HINTS.out[ovOutput(S)] || '';
  const fin = root.querySelector('[data-k=finals]'); fin.disabled = C.vo || S.mode === 'popon'; if (S.mode === 'popon') fin.checked = true;
  $('ovUrl').textContent = ovLink();
  ovPaint();
}
function ovPaint() {
  const S = C.ov && C.ov.S, stg = $('ovStage'); if (!S || !stg) return;
  stg.style.aspectRatio = S.w + ' / ' + S.h; stg.style.maxWidth = S.h > S.w ? '240px' : '';
  const W = stg.clientWidth || 560, k = W / S.w, o = ovOutput(S), matte = o === 'matte';
  $('ovOut').style.background = o === 'green' ? 'var(--clo-green)' : (o === 'black' || matte) ? 'var(--clo-black)' : 'transparent';
  const sf = $('ovSafe-guide'); sf.hidden = !C.ov.guide; sf.style.inset = S.safe + '%';
  const cap = $('ovCap');
  const fam = {sans: 'var(--font)', serif: '"Source Serif 4",ui-serif,Georgia,serif', rounded: 'ui-rounded,"SF Pro Rounded",system-ui,sans-serif', hyper: '"Atkinson Hyperlegible",system-ui,sans-serif', mono: 'ui-monospace,SFMono-Regular,Menlo,monospace'}[S.family];
  const jc = {top: 'flex-start', middle: 'center', bottom: 'flex-end'}[S.pos], ai = {left: 'flex-start', center: 'center', right: 'flex-end'}[S.align];
  cap.style.cssText = `inset:${S.safe}%;justify-content:${jc};align-items:${ai};text-align:${S.align};font-family:${fam};font-size:${(S.font * k).toFixed(1)}px;color:${matte ? 'var(--clo-white)' : '#' + S.color}`;
  const edge = matte ? 'none' : S.edge, pc = '#' + S.platec;
  const lnStyle = edge === 'plate' ? `background:color-mix(in srgb,${pc} ${Math.round(S.plate * 100)}%,transparent)` : edge === 'outline' ? `-webkit-text-stroke:.04em ${pc};paint-order:stroke fill` : edge === 'shadow' ? `text-shadow:.05em .05em .12em ${pc}` : '';
  if (!C.ov.sample) { cap.innerHTML = '<div class="none">Live feed: waiting for speech…</div>'; return; }
  const group = (iso, label) => {
    const arr = samp(iso), N = S.lines, t = C.ov.tick; let idx = [];
    if (S.mode === 'popon') { const s = Math.floor(t / N) * N; for (let j = 0; j < N; j++) idx.push(s + j); }
    else for (let j = Math.max(0, t - N + 1); j <= t; j++) idx.push(j);
    return `<div class="grp">${label ? `<span class="gl">${label}</span>` : ''}${idx.map(i => `<span class="ln" style="${lnStyle}">${esc(arr[i % arr.length])}</span>`).join('')}</div>`;
  };
  const first = S.lang || (live ? 'SPANISH' : 'SPANISH');
  let html;
  if (S.stream === 'transcription') html = group('en');
  else if (S.stream === 'both') html = group(isoOf(first), S.labels ? NATIVE[isoOf(first)] : '') + group(S.lang2 ? isoOf(S.lang2) : 'en', S.labels ? NATIVE[S.lang2 ? isoOf(S.lang2) : 'en'] : '');
  else html = group(isoOf(first));
  cap.innerHTML = `<div style="width:${S.maxw}%;display:flex;flex-direction:column;align-items:${ai}">${html}</div>`;
}
function ovList() {
  const b = $('ovList'); if (!b) return;
  b.innerHTML = C.ov.saved.length ? C.ov.saved.map(o => `<tr${o.id === C.ov.editing ? ' style="background:var(--hover)"' : ''}><td>${esc(o.name)}</td><td class="clo-mono">/overlay/${o.id}</td><td><div class="row" style="gap:6px;justify-content:flex-end">${copyBtn(HOST + '/overlay/' + o.id, 'Copy link')}${C.vo ? '' : `<button type="button" class="btn sm" data-clo-act="ovedit" data-id="${o.id}">Edit</button><button type="button" class="btn sm" data-clo-act="ovdel" data-id="${o.id}">Delete</button>`}</div></td></tr>`).join('')
    : '<tr><td colspan="3" class="clo-mut">None saved yet.</td></tr>';
}
function ovEditing(on, name) {
  const e = $('ovEditing'), s = $('ovSave'); if (e) { e.hidden = !on; $('ovEditingName').textContent = name || ''; }
  if (s) s.textContent = on ? 'Save changes' : 'Save'; const n = $('ovName'); if (n) n.value = on ? name : '';
}
AFTER.overlay = () => {
  const root = $('ovBuild'), O = C.ov;
  root.addEventListener('click', e => {
    const b = e.target.closest('.tg[data-k] button'); if (!b || b.disabled) return;
    const k = b.parentElement.dataset.k; if (k === 'out') ovSetOut(b.dataset.v); else O.S[k] = b.dataset.v; ovSync();
  });
  root.addEventListener('input', e => {
    const el = e.target.closest('[data-k]'); if (!el || el.classList.contains('tg')) return; const k = el.dataset.k;
    if (k === 'res') { const wh = el.value.split('x'); O.S.w = +wh[0]; O.S.h = +wh[1]; }
    else if (el.type === 'checkbox') O.S[k] = el.checked; else if (el.type === 'range') O.S[k] = +el.value;
    else if (el.type === 'color') O.S[k] = el.value.slice(1).toUpperCase(); else O.S[k] = el.value;
    ovSync();
  });
  ovList(); ovEditing(!!O.editing, O.editing ? (O.saved.find(x => x.id === O.editing) || {}).name : ''); ovSync();
  clearInterval(window.cloOvT);
  window.cloOvT = setInterval(() => { if (!$('ovStage')) { clearInterval(window.cloOvT); return; } O.tick++; ovPaint(); }, 2600);
};
addEventListener('resize', () => ovPaint());
ACT.ovpreset = b => { const O = C.ov, k = b.dataset.p, keep = {lang: O.S.lang, lang2: O.S.lang2, w: O.S.w, h: O.S.h}; O.S = Object.assign(ovBase(k), keep, {preset: k}); delete O.S.label; O.backdrop = k === 'slate' ? 'black' : 'program'; const st = $('ovStage'); st.className = 'clo-stage bk-' + O.backdrop; document.querySelectorAll('#ovBackdrop button').forEach(x => x.setAttribute('aria-pressed', x.dataset.v === O.backdrop)); ovSync(); };
ACT.ovback = b => { C.ov.backdrop = b.dataset.v; $('ovStage').className = 'clo-stage bk-' + b.dataset.v; document.querySelectorAll('#ovBackdrop button').forEach(x => x.setAttribute('aria-pressed', x === b)); };
IN.ovguide = el => { C.ov.guide = el.checked; ovPaint(); };
IN.ovsample = el => { C.ov.sample = el.checked; ovPaint(); };
ACT.ovcopy = b => { try { navigator.clipboard && navigator.clipboard.writeText(ovLink()); } catch (x) { /* select instead */ } b.textContent = 'Copied'; setTimeout(() => { b.textContent = 'Copy link'; }, 1600); };
ACT.ovdone = () => { C.ov.editing = null; ovEditing(false); ovList(); ovSync(); };
ACT.ovedit = b => { const o = C.ov.saved.find(x => x.id === b.dataset.id); C.ov.editing = o.id; C.ov.S = ovResolved(o.settings); ovEditing(true, o.name); ovList(); ovSync(); document.querySelector('#inset').scrollTop = 0; };
ACT.ovdel = b => { const o = C.ov.saved.find(x => x.id === b.dataset.id); if (!confirm('Delete the overlay “' + o.name + '”? Any browser source using /overlay/' + o.id + ' will stop showing captions.')) return; C.ov.saved = C.ov.saved.filter(x => x !== o); if (C.ov.editing === o.id) ACT.ovdone(); ovList(); };
ACT.ovsave = () => {
  const name = ($('ovName').value || '').trim(); if (!name) { toast('Give the overlay a name.'); return; }
  const id = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'overlay', O = C.ov;
  const ex = O.editing && O.saved.find(x => x.id === O.editing);
  if (ex) { ex.name = name; ex.settings = ovDiff(O.S); }
  else { if (O.saved.some(x => x.id === id)) { toast('Another overlay already uses that link name.'); return; } O.saved.push({id, name, settings: ovDiff(O.S)}); O.editing = id; }
  ovEditing(true, name); ovList(); ovSync(); toast('Saved. The link is ' + HOST + '/overlay/' + O.editing);
};

/* ================================================================== RECORDINGS */
C.rec = {
  enabled: true, keep: 20, view: 'runs', open: {},
  runs: [
    {id: 'r1', when: 'Sun 15 Nov 2026, 10:00', dur: '1 h 12 min', lines: 842, files: ['transcript-ENGLISH.txt', 'transcript-SPANISH.txt', 'transcript-PORTUGUESE.txt', 'transcript-KOREAN.txt', 'pairs-SPANISH.jsonl', 'finetune-SPANISH.jsonl', 'pairs-PORTUGUESE.jsonl', 'finetune-PORTUGUESE.jsonl']},
    {id: 'r2', when: 'Sun 15 Nov 2026, 08:30', dur: '1 h 04 min', lines: 765, cut: 1, files: ['transcript-ENGLISH.txt', 'transcript-SPANISH.txt', 'pairs-SPANISH.jsonl', 'finetune-SPANISH.jsonl']},
    {id: 'r3', when: 'Wed 11 Nov 2026, 19:00', dur: '58 min', lines: 603, files: ['transcript-ENGLISH.txt', 'transcript-SPANISH.txt', 'pairs-SPANISH.jsonl', 'finetune-SPANISH.jsonl']},
    {id: 'r4', when: 'Sun 8 Nov 2026, 10:00', dur: '1 h 09 min', lines: 811, files: ['transcript-ENGLISH.txt', 'transcript-SPANISH.txt', 'pairs-SPANISH.jsonl', 'finetune-SPANISH.jsonl']},
    {id: 'r5', when: 'Sun 8 Nov 2026, 07:55', dur: '2 min', lines: 0, files: []}
  ]
};
function recRows() {
  const R = C.rec, runs = (live && R.enabled ? [{id: 'now', when: 'Mon 16 Nov 2026, 09:12', rec: 1, lines: 118, files: []}] : []).concat(R.view === 'none' ? [] : R.runs);
  if (!runs.length) return `<tr><td colspan="5" class="clo-mut">${R.enabled ? 'Nothing recorded yet — the next run will be saved.' : 'Recording is off, so no run has been saved.'}</td></tr>`;
  return runs.map(r => `<tr><td class="w"><strong>${r.when}</strong>${r.rec ? ' <span class="clo-ok">· recording</span>' : r.cut ? ' <span class="clo-warn">· cut short</span>' : ''}</td><td class="clo-mut">${r.rec ? 'in progress' : r.dur}</td><td class="clo-mut">${r.lines}</td>
    <td class="w">${r.rec ? '<span class="clo-mut">after Stop</span>' : `<a class="clo-a" href="#recordings" data-clo-act="toast" data-msg="Downloading recording-${r.id}.zip">All (.zip)</a> &nbsp; ${R.open[r.id] ? '' : `<a class="clo-a" href="#recordings" data-clo-act="recfiles" data-id="${r.id}">Files…</a>`}${R.open[r.id] ? (r.files.length ? `<div class="clo-files">${r.files.map(f => `<a class="clo-a clo-mono" href="#recordings" data-clo-act="toast" data-msg="Downloading ${f}">${f}</a>`).join('')}</div>` : '<span class="clo-mut">no lines recorded</span>') : ''}`}</td>
    <td style="width:1%">${C.vo ? '' : `<button type="button" class="btn sm d" data-clo-act="recdel" data-id="${r.id}" data-when="${r.when}"${r.rec ? ' disabled title="This run is still being recorded."' : ''}>Delete</button>`}</td></tr>`).join('');
}
P.recordings = () => {
  const R = C.rec, dd = D();
  return head('Recordings & export', 'Save each run\'s transcript to disk and export it.', {demo: 'Demo: no recordings.manage'}) +
  card('Recording', 'Off by default: nothing is recorded until you turn this on.', `
    ${note('A recording covers exactly one run — from <em>Start capture</em> to <em>Stop</em>. Every committed caption line is written to disk as it happens, so a crash mid-event still leaves you the transcript up to that point. Blocked words are removed before anything is written. <strong>Off by default</strong>: nothing is recorded until you turn this on.')}
    <div class="row" style="align-items:flex-end;gap:24px">
      <div class="clo-f" style="margin:0"><label for="recEnabled">Record each run to disk</label><button type="button" id="recEnabled" class="sw${R.enabled ? ' on' : ''}" role="switch" aria-checked="${R.enabled}" data-clo-act="recsw"${dd}></button></div>
      <div class="clo-f" style="margin:0;min-width:240px"><label for="recKeep">Runs kept (oldest pruned at the next start)</label><input id="recKeep" type="number" min="1" max="500" step="1" value="${R.keep}" style="max-width:120px"${dd}></div>
      ${C.vo ? '' : '<button class="btn p" id="saveRecording" data-clo-act="recsave">Save</button>'}
      <span class="clo-mut" style="margin-left:auto;font-size:13px">Stored in <span class="clo-mono" id="recDir">recordings/</span></span></div>`) +
  `<div style="height:14px"></div><div class="clo-g b">` +
  card('Runs', 'One per Start capture to Stop. Download the zip or a single file.', `
    <div class="row" style="margin-bottom:12px"><span class="clo-l" style="margin:0">Demo state</span><div class="tg clo-tg" role="group"><button type="button" data-clo-act="recview" data-v="runs" aria-pressed="${R.view === 'runs'}">Runs</button><button type="button" data-clo-act="recview" data-v="none" aria-pressed="${R.view === 'none'}">No runs</button></div></div>
    <div class="clo-sc"><table class="clo-t"><thead><tr><th>Run</th><th>Duration</th><th>Lines</th><th>Download</th><th></th></tr></thead><tbody id="recRuns">${recRows()}</tbody></table></div>`) +
  card('What’s in the export', '', `${note('The zip holds a timestamped <span class="clo-mono">transcript-&lt;LANGUAGE&gt;.txt</span> per language, plus, per target language, a <span class="clo-mono">pairs-&lt;TARGET&gt;.jsonl</span> (each source line matched to its translation, with a confidence field) and a <span class="clo-mono">finetune-&lt;TARGET&gt;.jsonl</span> in OpenAI chat format, ready to upload to a fine-tuning job.')}
    <div class="clo-ex"><div>${ic('doc')}<span class="clo-mono">transcript-&lt;LANGUAGE&gt;.txt</span></div><div>${ic('doc')}<span class="clo-mono">pairs-&lt;TARGET&gt;.jsonl</span></div><div>${ic('doc')}<span class="clo-mono">finetune-&lt;TARGET&gt;.jsonl</span></div></div>
    ${note('The two feeds carry no shared segment id and are broken into lines independently, so pairs are matched on overlapping time span and every row says how sure the match is. Only the <span class="clo-mono">exact</span> ones are carried into the fine-tuning file — the rest stay in the pairs file to be reviewed.')}`) + `</div>`;
};
const recRefresh = () => { const t = $('recRuns'); if (t) t.innerHTML = recRows(); };
ACT.recsw = b => { C.rec.enabled = !C.rec.enabled; b.classList.toggle('on', C.rec.enabled); b.setAttribute('aria-checked', C.rec.enabled); recRefresh(); };
ACT.recsave = () => { C.rec.keep = +$('recKeep').value || C.rec.keep; toast('Saved'); };
ACT.recfiles = b => { C.rec.open[b.dataset.id] = 1; recRefresh(); };
ACT.recdel = b => { if (!confirm('Delete the recording from ' + b.dataset.when + '? The transcript cannot be recovered.')) return; C.rec.runs = C.rec.runs.filter(r => r.id !== b.dataset.id); recRefresh(); toast('Recording deleted.'); };
ACT.recview = b => { C.rec.view = b.dataset.v; document.querySelectorAll('[data-clo-act=recview]').forEach(x => x.setAttribute('aria-pressed', x === b)); recRefresh(); };
})();
