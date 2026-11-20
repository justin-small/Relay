/* Standalone operator/user screens (no sidebar): sign-in, password, database down, landing, home, alerts,
   panel flasher, rundown, status, tally. Everything is scoped under the "ox-" (shared) and "xr-" (rundown) prefixes.
   Nothing needs a server: buttons call toast() or change local state. Floating "Demo state" bars switch states. */
(function () {
  'use strict';

  /* ------------------------------------------------------------------ shared plumbing */
  const ST = {};                    // per-screen demo state, cleared when the hash changes
  let ticker = 0;                   // one interval at a time; every tick checks its screen is still on the page
  const stage = () => document.getElementById('auth');
  const alive = (sel) => !!(stage() && stage().querySelector(sel));
  function stopTick() { clearInterval(ticker); ticker = 0; }
  function every(ms, sel, fn) { stopTick(); ticker = setInterval(() => { if (!alive(sel)) return stopTick(); fn(); }, ms); }
  function redraw(id) {
    const a = stage();
    a.innerHTML = FULL[id]() + '<a class="backchip" href="#screens">← All screens</a>';
    AFTER[id] && AFTER[id]();
  }
  addEventListener('hashchange', () => { Object.keys(ST).forEach((k) => delete ST[k]); stopTick(); });
  const S = (id, d) => ST[id] || (ST[id] = Object.assign({}, d));

  window.OPX = {
    set(id, k, v) { ST[id][k] = v; redraw(id); },
    t(e, m) { if (e) e.stopPropagation(); toast(m); },      // stopPropagation: the shell toasts "Saved" for any button that starts with Save/Send/Change…
    go(e, h) { if (e) e.stopPropagation(); location.hash = h; },
  };

  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  // the floating demo-state bar: groups of [key, label, [[value,label],...]]
  const demo = (id, groups) => `<div class="ox-demo" role="group" aria-label="Demo state"><span class="ox-dl">Demo state</span>${groups.map((g) =>
    `<span class="ox-dg">${g[1] ? `<small>${g[1]}</small>` : ''}<span class="tabs">${g[2].map((o) =>
      `<button type="button" class="${String(ST[id][g[0]]) === String(o[0]) ? 'on' : ''}" onclick="OPX.set('${id}','${g[0]}','${o[0]}')">${o[1]}</button>`).join('')}</span></span>`).join('')}</div>`;

  /* alert-button icons: the real ten line drawings from app/static/alert-icons.js (24 x 24 grid) */
  const AI = {
    bell: '<path d="M6 16v-5a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5h4"/>',
    mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M9 21h6"/>',
    headphones: '<path d="M4 16v-4a8 8 0 0 1 16 0v4"/><rect x="3" y="14" width="4" height="6" rx="1.5"/><rect x="17" y="14" width="4" height="6" rx="1.5"/>',
    keys: '<rect x="3" y="6" width="18" height="12" rx="1.5"/><path d="M8 6v7M12 6v7M16 6v7M8 13v5M12 13v5M16 13v5"/>',
    drums: '<ellipse cx="12" cy="10" rx="8" ry="3"/><path d="M4 10v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7M6 3l4 5M18 3l-4 5"/>',
    guitar: '<circle cx="8.5" cy="15.5" r="4.5"/><path d="M11.5 12.5L19 5M17 3l4 4"/><circle cx="8.5" cy="15.5" r="1"/>',
    person: '<circle cx="12" cy="8" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/>',
    people: '<circle cx="9" cy="8" r="3"/><circle cx="17" cy="9" r="2.5"/><path d="M3 19a6 6 0 0 1 12 0M15.5 19a5 5 0 0 1 6.5-4.6"/>',
    video: '<rect x="3" y="7" width="12" height="10" rx="2"/><path d="M15 11l6-3v8l-6-3z"/>',
    sliders: '<path d="M6 4v16M12 4v16M18 4v16"/><rect x="4" y="12" width="4" height="3" rx="1"/><rect x="10" y="7" width="4" height="3" rx="1"/><rect x="16" y="14" width="4" height="3" rx="1"/>',
    // not alert icons: used by the other screens
    chev: '<path d="M9 6l6 6-6 6"/>', warn: '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17.5v.1"/>', ok: '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l3 3 5-6"/>',
    usb: '<circle cx="12" cy="18" r="2"/><path d="M12 16V4M12 4l-2 2.5M12 4l2 2.5M12 11l5-3v3M12 13l-5-3"/>', lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  };
  const sv = (n, cls) => `<svg class="ox-ic${cls ? ' ' + cls : ''}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${AI[n]}</svg>`;

  injectCSS('ox', `
.ox-ic{width:100%;height:100%;display:block}
.ox-demo{position:fixed;left:50%;transform:translateX(-50%);bottom:12px;z-index:85;display:flex;gap:10px 14px;align-items:center;max-width:calc(100vw - 24px);overflow-x:auto;padding:6px 10px;background:var(--card);color:var(--fg);border:1px solid var(--border);border-radius:calc(var(--r) + 4px);box-shadow:0 8px 28px #0006}
.ox-demo .ox-dl{font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);white-space:nowrap}
.ox-demo .ox-dg{display:flex;align-items:center;gap:6px;white-space:nowrap}.ox-demo .ox-dg small{font-size:11px;color:var(--muted)}
.ox-demo .tabs{margin:0;padding:2px}.ox-demo .tabs button{padding:3px 9px;font-size:12px;white-space:nowrap}
.ox-pad{padding-bottom:110px}
.ox-link{display:block;margin:0 auto;padding:0;background:none;border:0;color:var(--muted);font-size:13px;text-decoration:underline;cursor:pointer;text-align:center}.ox-link:hover{color:var(--fg)}
.ox-err{color:var(--err);font-size:13px;margin:0;text-align:center}
.ox-note{font-size:13px;margin:0;padding:10px 12px;border:1px solid var(--border);border-radius:calc(var(--r) - 2px);line-height:1.5;overflow-wrap:anywhere}
.ox-note.warn{border-color:color-mix(in srgb,var(--warn) 55%,var(--border))}.ox-note.err{border-color:color-mix(in srgb,var(--err) 55%,var(--border));color:var(--err)}.ox-note.ok{border-color:color-mix(in srgb,var(--ok) 55%,var(--border))}
.ox-fields{display:grid;gap:14px}.ox-fields>div>label{display:block;margin:0 0 5px}
.ox-opt{color:var(--muted);font-weight:400}.ox-fields code,.ox-note code{font:12px ui-monospace,monospace}
.ox-f{padding-top:72px;padding-bottom:72px}
.ox-import{border-top:1px solid var(--border);padding-top:20px;display:grid;gap:12px}.ox-import h3{margin:0;font-size:16px;font-weight:600}.ox-import p{margin:0;color:var(--muted);font-size:13px}
.cent.ox-stack{align-content:center;gap:16px;padding:72px 24px}
.ox-dot{width:10px;height:10px;border-radius:50%;background:var(--muted);flex:none}.ox-dot.ok{background:var(--ok)}.ox-dot.err{background:var(--err)}
.ox-page{min-height:100vh;background:var(--bg);color:var(--fg);padding:56px 16px 40px}
.ox-col{max-width:560px;margin:0 auto}
/* landing */
.ox-land{max-width:440px;margin:0 auto;padding:12vh 20px 40px;text-align:center}.ox-land .lk{display:inline-block;margin-bottom:18px}
.ox-land h1{font-size:28px;font-weight:600;margin:0 0 6px}.ox-land .sub{margin:0 0 26px}
.ox-choices{display:grid;gap:10px;text-align:left}
.ox-choice{display:block;padding:15px 18px;border:1px solid var(--border);border-radius:var(--r);background:var(--card);color:var(--fg);text-decoration:none;cursor:pointer;box-shadow:var(--shadow);transition:border-color .15s,background-color .15s;font:inherit;width:100%;text-align:left}
.ox-choice:hover,.ox-choice:focus-visible{border-color:var(--primary);background:var(--hover)}
.ox-choice strong{display:block;font-size:16px;font-weight:600}.ox-choice span{display:block;color:var(--muted);font-size:14px;margin-top:2px}
/* home */
.ox-tools{list-style:none;margin:0;padding:0;display:grid;gap:10px}
.ox-tools .ox-choice{display:flex;align-items:center;gap:12px}.ox-tools .ox-choice>div{flex:1;min-width:0}.ox-tools .ox-choice .ox-ic{width:18px;height:18px;color:var(--muted)}
.ox-ft{display:flex;justify-content:center;gap:20px;flex-wrap:wrap;margin-top:18px}
/* alerts */
@keyframes ox-flash{0%,49.9%{background:var(--err);color:var(--bg)}50%,100%{background:var(--bg);color:var(--err)}}
@keyframes ox-flash-att{0%,49.9%{background:var(--warn);color:var(--bg)}50%,100%{background:var(--bg);color:var(--warn)}}
.ox-flashing{animation:ox-flash 1s steps(1,end) infinite!important}
.ox-al-head{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:14px}
.ox-al-head h1{font-size:26px;font-weight:600;margin:0 0 2px}.ox-al-head .ox-dot{margin-top:10px}
.ox-as{font-size:14px;color:var(--muted);margin:0}.ox-as strong{color:var(--fg);font-weight:600}
.ox-ll{background:none;border:0;padding:0;font:inherit;color:var(--fg);text-decoration:underline;cursor:pointer}
.ox-nameform{margin:0 0 16px}.ox-nameform label{font-size:15px;color:var(--fg);margin-bottom:6px}.ox-nr{display:flex;gap:8px}.ox-nr input{flex:1;min-width:0;font-size:16px;padding:10px 12px}
.ox-nr .btn{padding:10px 16px}.ox-small{font-size:13px;color:var(--muted);margin:8px 0 0}
.ox-level{display:flex;align-items:center;gap:8px;margin:0 0 14px;flex-wrap:wrap;font-size:14px;color:var(--muted)}.ox-level label{margin:0}.ox-level select{width:auto;padding:6px 8px}
.ox-albs{display:grid;gap:8px}
.ox-alb{display:flex;align-items:center;gap:14px;width:100%;min-height:64px;padding:12px 16px;text-align:left;font:inherit;color:var(--fg);background:var(--card);border:1px solid var(--border);border-radius:calc(var(--r) + 4px);cursor:pointer;box-shadow:var(--shadow);touch-action:manipulation;-webkit-tap-highlight-color:transparent;transition:background-color .15s}
.ox-alb:hover{background:var(--hover)}.ox-alb:active{transform:scale(.99)}
.ox-alb-i{flex:none;width:30px;height:30px;color:var(--muted)}.ox-alb.was-seen .ox-alb-i{color:var(--ok)}.ox-alb.was-missed .ox-alb-i{color:var(--warn)}
.ox-alb-b{display:flex;flex-direction:column;gap:2px;min-width:0}.ox-alb-n{font-size:18px;font-weight:600;overflow-wrap:anywhere}.ox-alb-s{font-size:13px}
.ox-alb-s.seen{color:var(--ok)}.ox-alb-s.missed{color:var(--warn)}.ox-alb-s.cancelled{color:var(--muted)}
.ox-alb.ox-flashing .ox-alb-s,.ox-alb.ox-flashing .ox-alb-i{color:inherit}
.ox-sec{margin:24px 0 0}.ox-sec h2{font-size:16px;font-weight:600;margin:0 0 8px}
.ox-presets{display:grid;gap:8px}
.ox-mb{font:inherit;font-size:17px;font-weight:600;color:var(--fg);cursor:pointer;text-align:left;min-height:52px;padding:10px 14px;background:var(--card);border:1px solid var(--border);border-radius:calc(var(--r) + 4px);touch-action:manipulation}
.ox-mb:hover{background:var(--hover)}.ox-mb small{display:block;font-weight:400;font-size:12px;color:var(--muted);text-transform:capitalize}
.ox-mb.showing{border-color:var(--primary)}.ox-mb.lvl-attention{box-shadow:inset 4px 0 0 var(--warn)}.ox-mb.lvl-urgent{box-shadow:inset 4px 0 0 var(--err)}.ox-mb.lvl-info{box-shadow:inset 4px 0 0 var(--primary)}
.ox-free{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap;align-items:center}.ox-free input[type=text]{flex:1 1 200px;min-width:0;font-size:16px;padding:10px 12px}
.ox-free label{display:flex;align-items:center;gap:6px;margin:0;font-size:14px}.ox-free .btn{padding:10px 16px}
.ox-mlive{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:8px;padding:8px 12px;background:var(--card);border:1px solid var(--border);border-radius:var(--r);font-size:14px}
/* the receiving /screen?alert=<name> */
.ox-recv{position:relative;min-height:100vh;display:flex;flex-direction:column;overflow:hidden;background:var(--bg);color:var(--fg)}
.ox-caps{flex:1;padding:12vh 8vw 24vh;font-size:clamp(22px,3.4vw,48px);line-height:1.35;color:var(--muted)}.ox-caps b{color:var(--fg);font-weight:600}
.ox-fl{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:5vh 5vw;cursor:pointer;background:var(--err);color:var(--bg)}
.ox-fl[data-level=urgent]{animation:ox-flash 1s steps(1,end) infinite!important}.ox-fl[data-level=attention]{animation:ox-flash-att 1s steps(1,end) infinite!important}
.ox-fl[data-level=info]{background:var(--primary);color:var(--primary-fg)}
.ox-fl-from{font-size:clamp(48px,14vw,200px);font-weight:800;line-height:1.05}.ox-fl-to{font-size:clamp(20px,3.6vw,56px);font-weight:600;margin-top:.4em}
.ox-fl-rep{display:flex;flex-wrap:wrap;justify-content:center;gap:2vh 2vw;margin-top:5vh}
.ox-fl-rep button{font:inherit;font-size:clamp(18px,3vw,44px);font-weight:700;padding:.5em 1em;border-radius:.5em;border:.12em solid currentColor;background:color-mix(in srgb,var(--fg) 25%,transparent);color:inherit;cursor:pointer}
.ox-sm{position:absolute;left:50%;top:46%;transform:translate(-50%,-50%);width:min(90%,1200px);padding:4vh 4vw;border-radius:16px;text-align:center;background:var(--primary);color:var(--primary-fg);box-shadow:0 1vh 6vh #0008}
.ox-sm[data-level=urgent]{animation:ox-flash 1s steps(1,end) infinite!important}.ox-sm[data-level=attention]{animation:ox-flash-att 1s steps(1,end) infinite!important}
.ox-sm small{display:block;font-size:clamp(14px,2vw,28px);opacity:.85;margin-bottom:.4em}.ox-sm div{font-size:clamp(30px,7vw,110px);font-weight:800;line-height:1.1}
.ox-bn{position:absolute;left:0;right:0;top:0;padding:1.2vh 3vw;text-align:center;font-size:clamp(20px,3.4vw,52px);font-weight:700;background:var(--primary);color:var(--primary-fg)}
.ox-bn[data-level=urgent]{animation:ox-flash 1s steps(1,end) infinite!important}.ox-bn[data-level=attention]{animation:ox-flash-att 1s steps(1,end) infinite!important}
/* flasher */
.ox-fx{max-width:760px;margin:0 auto;padding:56px 20px 120px}
.ox-fx header{display:flex;align-items:center;gap:12px;margin-bottom:22px}.ox-fx header .sp{flex:1}.ox-fx header a{color:var(--muted);font-size:13px;cursor:pointer;text-decoration:underline}
.ox-fx h2.title{font-size:22px;margin:0 0 6px;font-weight:600}.ox-fx .sub{line-height:1.5}
.ox-fx section{margin-bottom:14px}.ox-fx section h3{font-size:15px;font-weight:600;margin:0 0 12px;display:flex;align-items:center;gap:9px}
.ox-num{display:inline-grid;place-items:center;width:22px;height:22px;border-radius:50%;background:var(--hover);color:var(--hover-fg);border:1px solid var(--border);font-size:12px}
.ox-fx .stack>*+*{margin-top:12px}.ox-fx p{margin:0;line-height:1.5}.ox-fx .muted{color:var(--muted)}.ox-mono{font:13px ui-monospace,SFMono-Regular,Menlo,monospace}
.ox-fx .rw{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.ox-fx [hidden]{display:none!important}
.ox-fx input[type=file]{padding:5px 8px;width:auto;max-width:100%}
.ox-boards{display:flex;gap:10px;flex-wrap:wrap;list-style:none;margin:0;padding:0}
.ox-boards a{display:block;padding:10px 14px;border:1px solid var(--border);border-radius:var(--r);text-decoration:none;color:var(--fg);cursor:pointer;background:var(--bg)}.ox-boards a:hover{background:var(--hover)}
.ox-boards a[aria-current]{border-color:var(--primary)}.ox-boards small{display:block;color:var(--muted);margin-top:2px}
.ox-fx ol.list{margin:0;padding-inline-start:1.25rem;display:grid;gap:6px;line-height:1.5}
.ox-steps{margin:0;padding:0;list-style:none;display:grid;gap:6px;color:var(--muted)}
.ox-steps li{display:flex;align-items:center;gap:9px}.ox-steps li::before{content:"";width:14px;height:14px;border-radius:50%;border:1.5px solid var(--border);flex:none}
.ox-steps li.done::before{background:var(--ok);border-color:var(--ok)}.ox-steps li.on{color:var(--fg);font-weight:600}.ox-steps li.on::before{border-color:var(--primary);background:radial-gradient(circle,var(--primary) 45%,transparent 50%)}
.ox-fx progress{width:100%;height:10px;accent-color:var(--primary)}
.ox-fx .btn.p.big{padding:9px 16px}.ox-fx .btn:disabled{opacity:.45;cursor:not-allowed}
/* status */
.xs{min-height:100vh;background:var(--bg);color:var(--fg);padding:52px 16px 110px}
.xs-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px;max-width:1500px;margin:0 auto}
.xs-card{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:18px 20px;box-shadow:var(--shadow);min-width:0}
.xs-card h2{margin:0 0 6px;font-size:14px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.xs-big{font-size:clamp(34px,6vw,64px);font-weight:700;line-height:1.1;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.xs-sub{margin-top:6px;font-size:18px;color:var(--muted);font-variant-numeric:tabular-nums;min-height:1.3em;overflow-wrap:anywhere}
.xs-card.ok{border-left-color:var(--ok)}.xs-card.warn{border-left-color:var(--warn)}.xs-card.warn .xs-big{color:var(--warn)}.xs-card.err{border-left-color:var(--err)}.xs-card.err .xs-big{color:var(--err)}
.xs-card.live{border-left-color:var(--err);background:color-mix(in srgb,var(--err) 14%,var(--card))}.xs-card.live .xs-big{color:var(--err)}
.xs-air{grid-column:1/-1}
.xs-meter{margin-top:12px;height:10px;border-radius:5px;background:var(--border);overflow:hidden}.xs-meter i{display:block;height:100%;background:var(--ok);transition:width .4s}
.xs-card.err .xs-meter i{background:var(--err)}.xs-card.warn .xs-meter i{background:var(--warn)}
.xs-streams{list-style:none;margin:12px 0 0;padding:0;display:grid;gap:6px}
.xs-streams li{display:flex;justify-content:space-between;gap:12px;font-size:17px;padding:6px 0;border-top:1px solid var(--border);flex-wrap:wrap}.xs-streams li span:last-child{color:var(--muted);font-variant-numeric:tabular-nums}
.xs-streams li.warn span:last-child{color:var(--warn)}.xs-streams li.error span:last-child{color:var(--err)}
.xs-foot{display:flex;align-items:center;gap:8px;max-width:1500px;margin:16px auto 0;color:var(--muted);font-size:14px}
/* tally */
.xt{min-height:100vh;display:flex;flex-direction:column;background:var(--bg);color:var(--fg);font-variant-numeric:tabular-nums}
.xt-cam,.xt-main{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;background:var(--card);transition:background .08s}
.xt-cam{padding:4vmin 4vmin 14vmin}
.xt-live{background:var(--err)!important;color:var(--bg)}.xt-next{background:var(--ok)!important;color:var(--bg)}.xt-lost{background:var(--hover)!important;color:var(--muted)}
.xt-num{font-size:36vmin;font-weight:800;line-height:1}.xt-name{font-size:7vmin;font-weight:600;overflow-wrap:anywhere}
.xt-state{margin-top:2vmin;font-size:8vmin;font-weight:800;letter-spacing:.08em;text-transform:uppercase;min-height:1.2em}
.xt-op{flex:1;display:grid;grid-template-rows:1fr auto}.xt-op .xt-main{padding:3vmin}.xt-op .xt-num{font-size:26vmin}
.xt-vid{width:100%;max-width:60vmin;aspect-ratio:16/9;margin-top:2vmin;border:1px dashed currentColor;border-radius:10px;display:grid;place-items:center;font-size:14px;opacity:.8;background:color-mix(in srgb,var(--bg) 40%,transparent)}
.xt-pick{margin-top:2vmin;font-size:16px;display:flex;gap:8px;align-items:center;color:inherit;margin-bottom:0}.xt-pick select{width:auto;max-width:100%;color:var(--fg)}
.xt-bar{display:grid;grid-template-columns:1fr 1fr;background:var(--bg);border-top:1px solid var(--border);padding-bottom:84px}
.xt-slot{padding:14px 18px;min-width:0}.xt-slot span{display:block;font-size:14px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}.xt-slot strong{font-size:28px;overflow-wrap:anywhere}
.xt-board{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;padding:56px 12px 12px;align-content:start;flex:1}
.xt-tile{background:var(--card);border:4px solid var(--border);border-radius:12px;padding:12px;min-width:0;display:grid;gap:4px;align-content:start}
.xt-tile.xt-live{border-color:var(--err);background:color-mix(in srgb,var(--err) 28%,var(--card))!important;color:var(--fg)}
.xt-tile.xt-next{border-color:var(--ok);background:color-mix(in srgb,var(--ok) 24%,var(--card))!important;color:var(--fg)}
.xt-tile.xt-lost{opacity:.55;background:var(--card)!important}
.xt-tnum{font-size:44px;font-weight:800;line-height:1}.xt-tname{font-size:18px;overflow-wrap:anywhere}.xt-tst{font-size:20px;font-weight:800;letter-spacing:.08em;min-height:1.2em}
.xt-tile .xt-vid{max-width:none;margin-top:0}.xt-tile select{font-size:13px;padding:4px 6px}
.xt-empty{grid-column:1/-1;text-align:center;font-size:28px;color:var(--muted);padding:10vh 0}
.xt-foot{display:flex;align-items:center;gap:8px;padding:8px 12px 84px;color:var(--muted);font-size:14px}.xt-foot .btn{margin-left:auto}
@media(max-width:600px){.ox-demo{bottom:8px}.ox-alb-n{font-size:17px}}
`);

  /* ================================================================== SIGN-IN: login (two layouts) */
  const LOGIN = { form: 'password', msg: 'none' };
  const PINS = ['Dana Reyes', 'Marcus Webb', 'Priya Nair', 'Tom Ellis'];
  const fieldIn = (id, label, attrs, hint) => `<div><label for="${id}">${label}${hint ? ` <span class="ox-opt">${hint}</span>` : ''}</label><input id="${id}" ${attrs}></div>`;

  function loginParts(st) {
    const f = st.form, m = st.msg;
    const ERR = { password: 'Wrong username or password.', pin: 'That PIN is not right.', tok: 'That admin token is not right.', code: 'That setup code is not right.', local: 'The two passwords do not match.', remote: 'Finish setting up Relay on the computer it runs on: open this page there.' };
    let msg = '';
    if (m === 'error') msg = `<p class="ox-err" role="alert">${ERR[f]}</p>`;
    else if (m === 'locked') msg = '<p class="ox-err" role="alert">Too many attempts. Try again in 5 min.</p>';
    else if (m === 'notice' && /^(tok|code|local|remote)$/.test(f)) msg = '<p class="ox-note ok" role="status">Imported: settings, 3 languages, 12 alert buttons, 2 panels. New certificate fingerprint (SHA-256): 9F:2C:41:B7:0A:5D:E3:18:6C:D0:77:AB:34:F1:92:5E.</p>';
    const re = m === 'error' || m === 'locked';
    if (f === 'password') return { title: 'Operator panel', lead: 'Sign in with your username and password.', body:
      `<div class="ox-fields">${fieldIn('username', 'Username', `type="text" name="username" value="${re ? 'dana.reyes' : ''}" autocomplete="username" autocapitalize="none" spellcheck="false" required`)}${fieldIn('password', 'Password', 'type="password" name="password" value="' + (re ? 'wrongpassword' : '') + '" autocomplete="current-password" required')}</div>` +
      `<button class="btn p" type="submit" onclick="OPX.t(event,'Signed in · landing on /admin')">Sign in</button>${msg}<button type="button" class="ox-link" onclick="OPX.set('s-login'+(window.__oxc?'-centered':''),'form','pin')">Use a PIN instead</button>` };
    if (f === 'pin') return { title: 'Operator panel', lead: 'This browser is trusted (Front desk iPad). Pick your name and type your PIN.', body:
      `<div class="ox-fields"><div><label for="user_id">Name</label><select id="user_id" name="user_id" required>${PINS.map((n, i) => `<option value="${i}">${n}</option>`).join('')}</select></div>${fieldIn('pin', 'PIN', `type="password" name="pin" inputmode="numeric" pattern="[0-9]*" maxlength="8" value="${re ? '4821' : ''}" autocomplete="off" required`)}</div>` +
      `<button class="btn p" type="submit" onclick="OPX.t(event,'Signed in · landing on /admin')">Sign in</button>${msg}<button type="button" class="ox-link" onclick="OPX.set('s-login'+(window.__oxc?'-centered':''),'form','password')">Use my password instead</button>` };
    // setup (first run): token_set -> admin token; code_set -> setup code; neither -> only reachable from this computer; remote -> go to the computer
    const lead = f === 'remote' ? 'Relay has not been set up yet. Open this page in a browser on the computer Relay runs on to create the first account.'
      : 'Create the first account, the Owner.' + (f === 'tok' ? ' Enter the admin token from <code>config.json</code> once; after this it is never used again.' : f === 'code' ? ' Enter the one-time setup code; it stops working once the account exists.' : ' This page is only reachable from this computer until the account exists.');
    let fields = '';
    if (f === 'tok') fields += fieldIn('token', 'Admin token', 'type="password" name="token" autocomplete="off" required');
    if (f === 'code') fields += fieldIn('token', 'Setup code', 'type="text" name="token" autocomplete="off" autocapitalize="characters" spellcheck="false" required', '(printed by the installer, or in the journal: <code>journalctl -u relay</code>)');
    let body = '';
    if (f !== 'remote') {
      fields += fieldIn('api_key', 'OpenAI API key', 'type="password" name="api_key" autocomplete="off" spellcheck="false"', '(optional, can be added later in Settings)') +
        fieldIn('susername', 'Username', `type="text" name="username" value="${re ? 'dana.reyes' : ''}" autocomplete="username" autocapitalize="none" spellcheck="false" required`) +
        fieldIn('name', 'Your name', `type="text" name="name" value="${re ? 'Dana Reyes' : ''}" autocomplete="name" required`) +
        fieldIn('spassword', 'Password', 'type="password" name="password" autocomplete="new-password" minlength="8" required') +
        fieldIn('confirm', 'Confirm password', 'type="password" name="confirm" autocomplete="new-password" minlength="8" required');
      body = `<div class="ox-fields">${fields}</div><button class="btn p" type="submit" onclick="OPX.t(event,'Owner created · signed in')">Create the Owner</button>`;
    }
    const imp = (f === 'code' || f === 'remote') ? '' : { title: 'Coming from Relay 2.0?', html: `<p>Import your settings from the old Docker install. Type the path of the old Relay folder (or the <code>docker-config</code> folder inside it). Nothing in it is changed.</p>${fieldIn('folder', 'Old Relay folder', 'type="text" name="folder" autocomplete="off" spellcheck="false" placeholder="/Users/you/relay-docker" required')}<button class="btn" type="button" onclick="OPX.t(event,\'Importing…\')">Import from a previous Relay</button>` };
    return { title: 'Set up Relay', lead, body: body + msg, imp };
  }
  const loginDemo = (id) => demo(id, [['form', 'Screen', [['password', 'Password'], ['pin', 'PIN'], ['tok', 'Setup · token'], ['code', 'Setup · code'], ['local', 'Setup · this computer'], ['remote', 'Setup · remote']]], ['msg', 'Message', [['none', 'None'], ['error', 'Error'], ['locked', 'Locked'], ['notice', 'Import notice']]]]);

  FULL['s-login'] = () => {
    const st = S('s-login', LOGIN); window.__oxc = 0;
    const p = loginParts(st);
    const body = p.body + (p.imp ? `<div class="ox-import"><h3>${p.imp.title}</h3>${p.imp.html}</div>` : '');
    return authLayout({ kind: 'split', title: p.title, lead: p.lead, body, top: '', foot: '© 2026 Relay' }).replace('class="formp"', 'class="formp ox-f"') + loginDemo('s-login');
  };
  FULL['s-login-centered'] = () => {
    const st = S('s-login-centered', LOGIN); window.__oxc = 1;
    const p = loginParts(st);
    let out = authLayout({ kind: 'center', title: p.title, lead: p.lead, body: p.body });
    if (p.imp) out = out.replace('<div class="cent">', '<div class="cent ox-stack">') + '';
    if (p.imp) out = out.replace(/<\/div><\/div>$/, `</div><div class="card"><form class="fbox" onsubmit="return false"><h1 style="font-size:22px">${p.imp.title}</h1>${p.imp.html}</form></div></div>`);
    return out + loginDemo('s-login-centered');
  };
  reg({ kind: 'full', id: 's-login', t: 'Sign in (split)', cat: 'Sign-in', path: '/admin', desc: 'Operator sign in, the trusted-device PIN picker and first-run setup with the Relay 2.0 import. Split layout: brand panel and form.' });
  reg({ kind: 'full', id: 's-login-centered', t: 'Sign in (centered)', cat: 'Sign-in', path: '/admin', desc: 'The same three forms on a single centered card. Switch password, PIN and setup states with the demo bar.' });

  /* ================================================================== SIGN-IN: password change */
  const PW = { v: 'forced', e: 'none' };
  FULL['s-password-change'] = () => {
    const st = S('s-password-change', PW);
    const E = { wrong: 'The password is wrong.', mismatch: 'The two passwords do not match.', same: 'Choose a different password from the current one.', short: 'A password is 8 to 200 characters.', locked: 'Too many attempts. Try again in 3 min.' };
    const forced = st.v === 'forced';
    const body = `<div class="ox-fields">${fieldIn('current', 'Current password', 'type="password" name="current" autocomplete="current-password" required')}${fieldIn('new', 'New password', 'type="password" name="new" autocomplete="new-password" minlength="8" required')}${fieldIn('confirm', 'Confirm new password', 'type="password" name="confirm" autocomplete="new-password" minlength="8" required')}</div>` +
      `<button class="btn p" type="submit" onclick="OPX.t(event,'Password changed')">Change password</button>` +
      (st.e !== 'none' ? `<p class="ox-err" role="alert">${E[st.e]}</p>` : '') +
      (forced ? '<button type="button" class="ox-link" onclick="OPX.go(event,\'s-login\')">Sign out</button>' : '<button type="button" class="ox-link" onclick="OPX.go(event,\'s-home\')">Back</button>');
    return authLayout({ kind: 'center', title: 'Change your password', lead: forced ? 'You are signed in with a temporary password. Choose your own to continue.' : 'Choose a new password. You stay signed in here; your other sign-ins end.', body }) +
      demo('s-password-change', [['v', 'Opened', [['forced', 'Temporary password'], ['voluntary', 'From Home']]], ['e', 'Error', [['none', 'None'], ['wrong', 'Wrong'], ['mismatch', 'Mismatch'], ['same', 'Same'], ['short', 'Short'], ['locked', 'Locked']]]]);
  };
  reg({ kind: 'full', id: 's-password-change', t: 'Change password', cat: 'Sign-in', path: '/admin/password · /home?password=1', desc: 'Forced change after a temporary password, or the voluntary form opened from Home.' });

  /* ================================================================== SIGN-IN: database down / not allowed */
  FULL['s-db-down'] = () => {
    const st = S('s-db-down', { v: 'down' });
    const down = st.v === 'down';
    const body = down
      ? '<button class="btn p" type="button" onclick="OPX.t(event,\'Still unavailable · try again in a moment\')">Try again</button><p class="ox-note warn" style="text-align:center">Captions keep running while the user database is down. Only sign-in and the operator panel wait for it.</p>'
      : '<button class="btn" type="button" onclick="OPX.go(event,\'s-home\')">Go to Home</button>';
    return authLayout({ kind: 'center', title: down ? 'Operator panel unavailable' : 'Not allowed', lead: down ? 'The user database is not available. Relay is still captioning; try again in a moment.' : 'Your account does not have the role this page needs. Ask an admin to change it.', body }) +
      demo('s-db-down', [['v', 'Page', [['down', 'Database unavailable (503)'], ['denied', 'Not allowed (403)']]]]);
  };
  reg({ kind: 'full', id: 's-db-down', t: 'Database unavailable', cat: 'Sign-in', path: '(any non-API page, 503) · 403 page', desc: 'The page every non-API route shows while the user database is down, plus the bare 403 "Not allowed." page.' });

  /* ================================================================== SIGN-IN: landing (index.html) */
  FULL['s-landing'] = () => {
    const st = S('s-landing', { admin: '1', src: 'English' });
    const ch = (href, t, d) => `<button class="ox-choice" type="button" onclick="OPX.t(event,'Opens ${href}')"><strong>${t}</strong><span>${d}</span></button>`;
    return `<div class="ox-page ox-pad"><div class="ox-land"><span class="lk">${LOCK}</span><h1>Live Captions</h1><p class="sub">Pick a view. Nothing to install — the page updates itself.</p>
<div class="ox-choices">${ch('/translation', 'Translation', 'Read along in your language')}${ch('/transcription', 'Transcription', `Live ${st.src} of what is being said`)}${ch('/both', 'Both', `${st.src} and translation, side by side`)}${st.admin == '1' ? ch('/admin', 'Operator panel', 'Token required') : ''}</div></div></div>` +
      demo('s-landing', [['admin', 'Port', [['1', 'Admin port'], ['0', 'Public port']]], ['src', 'Source', [['English', 'English'], ['Spanish', 'Spanish']]]]);
  };
  reg({ kind: 'full', id: 's-landing', t: 'Landing', cat: 'Sign-in', path: '/', desc: 'The public front door: Translation, Transcription, Both, and the Operator panel link only on the admin port.' });

  /* ================================================================== OPERATOR TOOLS: home */
  FULL['s-home'] = () => {
    const st = S('s-home', { v: 'ops' });
    const tool = (t, n, ic) => `<li><button class="ox-choice" type="button" onclick="OPX.t(event,'Opens ${t}')">${sv(ic)}<div><strong>${t}</strong>${n ? `<span>${n}</span>` : ''}</div>${sv('chev')}</button></li>`;
    const tools = st.v === 'none' ? '' :
      tool('Alerts', "Flash a screen to get someone's attention", 'bell') + tool('Crew', 'Your schedule, availability and the rundown', 'people') + (st.v === 'ops' ? tool('Tally', 'A phone on a camera: red on program, green on preview', 'video') : '');
    const adminRow = st.v === 'ops' ? `<li><button class="ox-choice" type="button" onclick="OPX.go(event,'health')">${sv('sliders')}<div><strong>Operator panel</strong><span>Control Relay</span></div>${sv('chev')}</button></li>` : '';
    const inner = st.v === 'none'
      ? '<p class="lead" style="margin:0">Your account has nothing to use in Relay yet. Ask an admin to give it a role that can.</p>'
      : `<p class="lead" style="margin:0 0 4px">What you can use in Relay.</p><ul class="ox-tools">${tools}${adminRow}</ul>`;
    const who = st.v === 'ops' ? 'Dana Reyes' : st.v === 'vol' ? 'Priya Nair' : 'Sam Okafor';
    return `<div class="cent" style="align-content:center;gap:16px"><div class="card" style="width:min(440px,100%)"><div class="fbox" style="width:100%"><span class="lk" style="align-self:center">${LOCK}</span><h1 style="font-size:24px">Hello, ${who}</h1>${inner}</div></div>
<div class="ox-ft"><button class="ox-link" style="margin:0" onclick="OPX.go(event,'s-password-change')">Change my password</button><button class="ox-link" style="margin:0" onclick="OPX.go(event,'s-login')">Sign out</button></div></div>` +
      demo('s-home', [['v', 'Account', [['ops', 'Operator'], ['vol', 'Volunteer'], ['none', 'No role']]]]);
  };
  // the cent container is a grid; keep the footer links under the card
  injectCSS('ox-home', '.cent .ox-ft{margin-top:0}');
  reg({ kind: 'full', id: 's-home', t: 'Home', cat: 'Operator tools', path: '/home', desc: "Where an account that never opens the operator panel lands: the tools it may use, its password and sign-out." });

  /* ================================================================== OPERATOR TOOLS: alerts (+ receiving screen) */
  const NOW = () => Date.now();
  function alertRows() {
    const n = NOW();
    return [
      { id: 'sound', name: 'Sound booth', icon: 'sliders', state: 'seen', seenOn: 'Sound booth', reply: 'On my way', at: n - 12000 },
      { id: 'cam2', name: 'Camera 2', icon: 'video', state: 'idle' },
      { id: 'wl', name: 'Worship leader', icon: 'mic', state: 'on', from: 'Drums', endsAt: n + 42000, at: n - 18000 },
      { id: 'nur', name: 'Nursery team', icon: 'people', state: 'missed', at: n - 130000 },
      { id: 'pd', name: 'Pastor Dave', icon: 'person', state: 'cancelled', at: n - 30000 },
      { id: 'all', name: 'Everyone', icon: 'bell', state: 'idle' },
    ];
  }
  const ago = (s) => s < 10 ? 'just now' : s < 60 ? Math.round(s) + ' s ago' : Math.round(s / 60) + ' min ago';
  const mmss = (s) => { s = Math.max(0, Math.round(s)); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); };
  function rowStatus(r, sender) {
    const age = (NOW() - (r.at || NOW())) / 1000;
    if (r.state === 'on') { const p = ['Flashing']; if (!(r.from && r.from === sender) && r.from) p.push('from ' + r.from); p.push(mmss((r.endsAt - NOW()) / 1000) + ' left'); p.push('tap to stop'); return { cls: 'on', text: p.join(' · ') }; }
    if (r.state === 'seen') return { cls: 'seen', text: 'Seen on ' + (r.seenOn || 'a screen') + (r.reply ? ' · “' + r.reply + '”' : '') + ' · ' + ago(age) };
    if (r.state === 'missed') return { cls: 'missed', text: 'Not seen · ' + ago(age) };
    if (r.state === 'cancelled') return { cls: 'cancelled', text: 'Stopped · ' + ago(age) };
    return null;
  }
  const alSender = (st) => st.v === 'acct' ? 'Dana Reyes' : (st.v === 'anon' || st.name) ? (st.name || 'Drums') : '';
  function paintAlerts() {
    const st = ST['s-alert']; if (!st || st.v === 'screen') return;
    const box = document.getElementById('oxal-btns'); if (!box) return;
    const sender = alSender(st);
    box.innerHTML = st.rows.map((r) => {
      const s = rowStatus(r, sender);
      return `<button type="button" class="ox-alb${r.state === 'on' ? ' ox-flashing' : ''}${s ? ' was-' + s.cls : ''}" data-id="${r.id}" aria-pressed="${r.state === 'on'}" onclick="OPX.alPress(event,'${r.id}')"><span class="ox-alb-i">${sv(r.icon)}</span><span class="ox-alb-b"><span class="ox-alb-n">${esc(r.name)}</span>${s ? `<span class="ox-alb-s ${s.cls}">${esc(s.text)}</span>` : ''}</span></button>`;
    }).join('');
  }
  function paintMsgs() {
    const st = ST['s-alert']; if (!st || st.v === 'screen') return;
    const pre = document.getElementById('oxal-presets'); if (!pre) return;
    const showing = {}; st.live.forEach((l) => { showing[l.key] = 1; });
    pre.innerHTML = st.presets.map((p) => {
      const kind = [p.banner ? 'Banner' : '', p.level && p.level !== 'info' ? p.level : ''].filter(Boolean).join(' · ');
      return `<button type="button" class="ox-mb lvl-${p.level || 'info'}${showing[p.id] ? ' showing' : ''}" onclick="OPX.alMsg(event,'${p.id}')">${esc(p.text)}${kind ? `<small>${kind}</small>` : ''}</button>`;
    }).join('');
    document.getElementById('oxal-live').innerHTML = st.live.map((l, i) => `<div class="ox-mlive"><span>${l.banner ? 'Banner: ' : 'Showing: '}${esc(l.text)}</span><button type="button" class="ox-ll" onclick="OPX.alClear(event,${i})">Clear</button></div>`).join('');
  }
  Object.assign(window.OPX, {
    alPress(e, id) {
      e.stopPropagation(); const st = ST['s-alert'], r = st.rows.find((x) => x.id === id), sender = alSender(st);
      if (!sender && r.state !== 'on') { toast('Say who you are first'); return; }
      if (r.state === 'on') { r.state = 'cancelled'; r.at = NOW(); toast('Stopped ' + r.name); }
      else { r.state = 'on'; r.from = sender; r.endsAt = NOW() + 60000; r.at = NOW(); toast('Flashing ' + r.name + (st.level ? ' · ' + st.level : '')); }
      paintAlerts();
    },
    alSave(e) {
      e.preventDefault(); e.stopPropagation(); const v = document.getElementById('oxal-name').value.replace(/\s+/g, ' ').trim().slice(0, 32); if (!v) return;
      const st = ST['s-alert']; st.name = v; st.v = 'anon'; redraw('s-alert');
    },
    alChange(e) { e.stopPropagation(); const st = ST['s-alert']; st.name = ''; st.v = 'name'; redraw('s-alert'); },
    alLevel(v) { ST['s-alert'].level = v; },
    alMsg(e, id) { e.stopPropagation(); const st = ST['s-alert']; if (!alSender(st)) { toast('Say who you are first'); return; } const p = st.presets.find((x) => x.id === id); st.live.push({ key: p.id, text: p.text, banner: p.banner }); paintMsgs(); },
    alFree(e) { e.preventDefault(); e.stopPropagation(); const st = ST['s-alert'], i = document.getElementById('oxal-text'); if (!alSender(st)) { toast('Say who you are first'); return; } const t = i.value.replace(/\s+/g, ' ').trim(); if (!t) return; st.live.push({ key: 'free', text: t, banner: document.getElementById('oxal-banner').checked }); i.value = ''; paintMsgs(); },
    alClear(e, i) { e.stopPropagation(); ST['s-alert'].live.splice(i, 1); paintMsgs(); },
  });

  FULL['s-alert'] = () => {
    const st = S('s-alert', { v: 'acct', level: '', rk: 'alert', rl: 'urgent' });
    if (!st.rows) { st.rows = alertRows(); st.live = [{ key: 'p1', text: 'Wrap up', banner: false }]; st.presets = [{ id: 'p1', text: 'Wrap up', level: 'attention' }, { id: 'p2', text: 'Two minutes', level: 'info', banner: true }, { id: 'p3', text: 'Stand and greet', level: 'info' }, { id: 'p4', text: 'Stop the music', level: 'urgent', banner: true }]; }
    const grp = [['v', 'Page', [['acct', 'Signed in'], ['anon', 'Named'], ['name', 'Ask name'], ['empty', 'No buttons'], ['offline', 'Offline'], ['screen', 'Receiving screen']]]];
    if (st.v === 'screen') {
      grp.push(['rk', 'Showing', [['alert', 'Alert'], ['msg', 'Message'], ['banner', 'Banner']]], ['rl', 'Level', [['urgent', 'Urgent'], ['attention', 'Attention'], ['info', 'Info']]]);
      const lv = st.rl, L = { urgent: 'Urgent', attention: 'Attention', info: 'Info' };
      const layer = st.rk === 'alert'
        ? `<div class="ox-fl" data-level="${lv}" id="alertFlash" tabindex="-1" onclick="OPX.t(event,'Acknowledged · alert ends everywhere')"><div class="ox-fl-from" id="alertFrom">Drums</div><div class="ox-fl-to" id="alertTo">needs Worship leader · tap anywhere</div><div class="ox-fl-rep" id="alertReplies">${['On my way', 'One minute', "Can't right now"].map((t) => `<button type="button" onclick="OPX.t(event,'Replied: ${t}')">${t}</button>`).join('')}</div></div>`
        : st.rk === 'msg' ? `<div class="ox-sm" data-level="${lv}" id="stageMessage"><small id="stageFrom">From Dana Reyes</small><div id="stageText">Wrap up</div></div>`
          : `<div class="ox-bn" data-level="${lv}" id="stageBanner">Two minutes to the offering</div>`;
      return `<div class="ox-recv"><div class="ox-caps"><b>…and that is why the harbour matters.</b> A ship at anchor is not stuck. It is held. When the storm comes, the anchor does not move the sea, it keeps the ship from moving with it. <span style="opacity:.6">Hebrews calls our hope an anchor for the soul, firm and secure.</span></div>${layer}</div>` +
        `<div class="ox-demo" style="left:12px;transform:none;top:12px;bottom:auto;max-width:calc(100vw - 24px);margin-left:150px"><span class="ox-dl">/screen?alert=Worship leader · ${L[lv]}</span></div>` + demo('s-alert', grp);
    }
    const sender = alSender(st), dot = st.v === 'offline' ? 'err' : 'ok';
    const asLine = st.v === 'acct'
      ? `<p class="ox-as" id="sendingAs">as <strong id="senderName">Dana Reyes</strong> &middot; <a class="ox-ll" style="cursor:pointer" onclick="OPX.go(event,'s-home')">home</a></p>`
      : sender ? `<p class="ox-as" id="sendingAs">as <strong id="senderName">${esc(sender)}</strong> &middot; <button type="button" class="ox-ll" id="changeName" onclick="OPX.alChange(event)">change</button></p>` : '<p class="ox-as" id="sendingAs" hidden></p>';
    const nameForm = (st.v === 'name') ? `<form class="ox-nameform" id="nameForm" onsubmit="OPX.alSave(event)"><label for="oxal-name">Who are you?</label><div class="ox-nr"><input id="oxal-name" maxlength="32" autocomplete="nickname" placeholder="Drums" required><button class="btn" type="submit">Save</button></div><p class="ox-small">Shown on the screen you flash, so they know who needs them.</p></form>` : '';
    const empty = st.v === 'empty';
    if (empty) { st.rows = []; } else if (!st.rows.length) st.rows = alertRows();
    return `<div class="ox-page ox-pad"><div class="ox-col" id="oxal">
<header class="ox-al-head"><div><h1>Need someone?</h1>${asLine}</div><span class="ox-dot ${dot}" id="statusDot" title="Connection"></span></header>
${nameForm}
<div class="ox-level" id="levelRow"><label for="levelPick">Level</label><select id="levelPick" onchange="OPX.alLevel(this.value)"><option value="">Each button's own</option><option value="info">Info (blue)</option><option value="attention">Attention (amber, flashes)</option><option value="urgent">Urgent (red, flashes)</option></select></div>
<main class="ox-albs" id="oxal-btns" aria-live="polite"></main>
<p class="ox-small" id="empty" style="font-size:15px" ${empty ? '' : 'hidden'}>No alert buttons yet. The operator sets them up in Relay admin, under Alerts.</p>
<p class="ox-small" style="margin-top:18px">Tap a card to flash its screens. Tap it again to stop.</p>
<section class="ox-sec" id="msgSection"${empty ? ' hidden' : ''}><h2>Message the stage</h2><div class="ox-presets" id="oxal-presets"></div>
<form class="ox-free" id="msgForm" onsubmit="OPX.alFree(event)"><input type="text" id="oxal-text" maxlength="80" placeholder="Type a message" aria-label="Message"><label><input type="checkbox" id="oxal-banner"> Banner</label><button type="submit" class="btn">Send</button></form><div id="oxal-live"></div></section>
</div></div>` + demo('s-alert', grp);
  };
  AFTER['s-alert'] = () => {
    const st = ST['s-alert'];
    if (st.v === 'screen') return;
    if (st.v === 'offline') toast('Not hearing from Relay');
    const sel = document.getElementById('levelPick'); if (sel) sel.value = st.level || '';
    paintAlerts(); paintMsgs();
    every(1000, '#oxal', () => { if (st.rows.some((r) => r.state === 'on')) { st.rows.forEach((r) => { if (r.state === 'on' && r.endsAt < NOW()) { r.state = 'missed'; r.at = NOW(); } }); paintAlerts(); } });
  };
  reg({ kind: 'full', id: 's-alert', t: 'Alerts', cat: 'Operator tools', path: '/alert · /screen?alert=<name>', desc: 'The phone and tablet alert sender: one big button per alert, a level picker, stage messages. Includes the full-screen flash a /screen shows when an alert reaches it.' });

  /* ================================================================== OPERATOR TOOLS: panel flasher */
  const FLASH = { fw: '1', ser: '1', board: 'crow', res: 'none', run: 'idle', err: '0' };
  const RESULTS = {
    done: ['', 'Firmware <span data-slot="version">1.4.2</span> is written and checked. Press <strong>RESET</strong> on the panel to start it. With no Wi-Fi saved it opens its setup network; once it joins, it appears under <a onclick="OPX.go(event,\'panels\')" style="cursor:pointer;text-decoration:underline">Panels</a>.'],
    cancelled: ['', 'Cancelled. Nothing was written.'],
    'image-missing': ['err', 'Relay could not send the firmware. Nothing was written.'],
    'manifest-wrong': ['err', 'The firmware on this Relay is not the CrowPanel Advance 7&quot;&rsquo;s. Nothing was written.'],
    'image-size': ['err', 'The firmware Relay sent is not the size it should be. Nothing was written.'],
    'image-sha': ['err', 'The firmware Relay sent does not match its checksum. Nothing was written.'],
    'image-project': ['err', 'The firmware on this Relay is not a Relay panel&rsquo;s. Nothing was written.'],
    'connect-failed': ['err', 'The panel did not answer. Hold BOOT, press RESET, release BOOT, then try again. Nothing was written.'],
    'wrong-chip': ['err', 'This is not an ESP32-S3 (<span data-slot="chip">ESP32-P4, 16 MB</span>), so it is not a CrowPanel Advance 7&quot;. Nothing was written.'],
    'wrong-size': ['err', 'This board does not have 16 MB of flash (<span data-slot="chip">ESP32-S3, 8 MB</span>). Nothing was written.'],
    'read-failed': ['err', 'The panel stopped answering before anything was written. Try again.'],
    'write-failed': ['err', 'The write did not finish. Connect again and flash once more. The panel keeps its settings.'],
  };
  const STEPS = [['image', 'Fetch and check the image'], ['connect', 'Connect to the panel'], ['check', 'Read the firmware it runs'], ['write', 'Write <span data-slot="percent"></span>']];
  FULL['s-flash'] = () => {
    const st = S('s-flash', FLASH);
    const crow = st.board === 'crow', has = st.fw === '1';
    const label = crow ? 'CrowPanel Advance 7&quot;' : 'Waveshare ESP32-P4 10.1&quot;';
    const running = st.run !== 'idle';
    const stepIdx = { fetch: 0, connect: 1, check: 2, confirm: 2, write: 3 }[st.run];
    const result = (code) => `<p class="ox-note ${RESULTS[code][0]}" data-result="${code}" tabindex="-1" role="${RESULTS[code][0] ? 'status' : 'status'}" ${st.res === code ? '' : 'hidden'}>${RESULTS[code][1]}</p>`;
    return `<div class="ox-page" style="padding:0"><div class="ox-fx"><header><span class="lk">${LOCK}</span><span class="sp"></span><a onclick="OPX.go(event,'panels')">Back to Panels</a></header>
<h2 class="t title">Flash a panel</h2><p class="sub">Write a new or recovered panel&rsquo;s firmware over USB. A panel keeps its Wi-Fi and its settings.</p>
<section class="card" aria-labelledby="board-title"><h3 id="board-title">Which panel?</h3><ul class="ox-boards">
<li><a ${crow ? 'aria-current="page"' : ''} onclick="OPX.set('s-flash','board','crow')">CrowPanel Advance 7&quot;<small>Firmware 1.4.2</small></a></li>
<li><a ${crow ? '' : 'aria-current="page"'} onclick="OPX.set('s-flash','board','wave')">Waveshare ESP32-P4 10.1&quot;<small>No USB image</small></a></li></ul></section>
<section class="card" aria-labelledby="image-title"><h3 id="image-title"><span class="ox-num" aria-hidden="true">1</span>The firmware</h3><div class="stack" data-flash-upload data-board="${crow ? 'crowpanel-adv7' : 'waveshare-p4-10.1'}">
${has ? `<p>${label} firmware <strong>1.4.2</strong> is on this Relay (<span class="ox-mono">relay-display.bin</span>).</p>` : `<p>No USB image for the ${label} is on this Relay yet.</p>`}
<p class="muted">Upload <span class="ox-mono">relay-display.bin</span> from a release: the whole image, bootloader included. The <span class="ox-mono">relay-display-app.bin</span> uploaded under Panels updates a running panel and cannot start a blank one.</p>
<div class="rw"><input type="file" accept=".bin,application/octet-stream" aria-label="relay-display.bin" data-upload-file><button type="button" class="btn" data-upload onclick="OPX.flUp(event)">${has ? 'Replace' : 'Upload'}</button>${has ? '<button type="button" class="btn" data-upload-remove onclick="OPX.flRemove(event)">Remove</button>' : ''}</div>
<p class="ox-note err" data-upload-error role="alert" ${st.err === '1' ? '' : 'hidden'}>Choose relay-display.bin first.</p></div></section>
${has ? `<section class="card" aria-labelledby="connect-title"><h3 id="connect-title"><span class="ox-num" aria-hidden="true">2</span>Connect the panel</h3><ol class="list"><li>Use Chrome or Edge on a Windows, macOS or Linux computer.</li><li>Connect the panel&rsquo;s USB-C port to the computer with a cable that carries data.</li><li>If the computer does not see the panel, hold <strong>BOOT</strong>, press <strong>RESET</strong>, then release <strong>BOOT</strong>.</li></ol></section>
<section class="card" aria-labelledby="flash-title"><h3 id="flash-title"><span class="ox-num" aria-hidden="true">3</span>Write firmware 1.4.2</h3><div class="stack" data-flasher data-board="${crow ? 'crowpanel-adv7' : 'waveshare-p4-10.1'}" data-project="relay-display" data-chip="${crow ? 'ESP32-S3' : 'ESP32-P4'}" data-flash-mb="16">
<p class="ox-note warn" data-flash-unsupported ${st.ser === '1' ? 'hidden' : ''}>This page needs Chrome or Edge on a desktop, with scripting on. In any other browser, flash <span class="ox-mono">relay-display.bin</span> at 0x0 with esptool, as the firmware README describes.</p>
<div><button type="button" class="btn p big" data-flash-start ${st.ser === '1' ? '' : 'hidden'} ${running ? 'disabled' : ''} onclick="OPX.flRun(event)">Choose the panel&rsquo;s port and flash</button></div>
<ol class="ox-steps" data-flash-steps ${running || st.res !== 'none' && st.res !== 'cancelled' && false ? '' : 'hidden'}>${STEPS.map((s, i) => `<li data-step="${s[0]}" class="${running && i < stepIdx ? 'done' : running && i === stepIdx ? 'on' : ''}" ${running && i === stepIdx ? 'aria-current="step"' : ''}>${s[1]}</li>`).join('')}</ol>
<progress class="ox-fx-progress" data-flash-progress max="100" value="${st.pct || 0}" ${st.run === 'write' ? '' : 'hidden'}></progress>
<div class="stack" data-flash-confirm ${st.run === 'confirm' ? '' : 'hidden'}><p>This board does not run Relay panel firmware. Write the ${label} firmware to it?</p><div class="rw"><button type="button" class="btn p" data-flash-yes onclick="OPX.flAnswer(event,1)">Write it</button><button type="button" class="btn" data-flash-no onclick="OPX.flAnswer(event,0)">Cancel</button></div></div>
<div role="status">${Object.keys(RESULTS).map(result).join('')}</div></div></section>` : ''}
</div></div>` + demo('s-flash', [['fw', 'Firmware', [['1', 'Uploaded'], ['0', 'None']]], ['ser', 'Browser', [['1', 'Chrome'], ['0', 'No Web Serial']]], ['res', 'Result', [['none', 'None'], ['done', 'Done'], ['cancelled', 'Cancelled'], ['connect-failed', 'No answer'], ['wrong-chip', 'Wrong chip'], ['write-failed', 'Write failed'], ['image-sha', 'Bad checksum']]], ['err', 'Upload error', [['0', 'Off'], ['1', 'On']]]]);
  };
  const FL_T = [];
  const flClear = () => { while (FL_T.length) clearTimeout(FL_T.pop()); };
  function flGo(next, res, ms) { FL_T.push(setTimeout(() => { if (!alive('[data-flasher]')) return; const st = ST['s-flash']; st.run = next; if (res) { st.res = res; st.run = 'idle'; } redraw('s-flash'); }, ms)); }
  Object.assign(window.OPX, {
    flUp(e) { e.stopPropagation(); const st = ST['s-flash']; st.err = '1'; redraw('s-flash'); },
    flRemove(e) { e.stopPropagation(); ST['s-flash'].fw = '0'; ST['s-flash'].res = 'none'; redraw('s-flash'); toast('Firmware removed'); },
    flRun(e) {
      e.stopPropagation(); flClear(); const st = ST['s-flash']; st.res = 'none'; st.pct = 0; st.run = 'fetch'; redraw('s-flash');
      flGo('connect', 0, 900); flGo('check', 0, 1900); flGo('confirm', 0, 2800);   // a blank board asks first, as a new one does
    },
    flAnswer(e, yes) {
      e.stopPropagation(); flClear(); const st = ST['s-flash'];
      if (!yes) { st.run = 'idle'; st.res = 'cancelled'; redraw('s-flash'); return; }
      st.run = 'write'; st.pct = 0; redraw('s-flash');
      let p = 0; const iv = setInterval(() => {
        if (!alive('[data-flasher]')) return clearInterval(iv);
        p += 7; st.pct = Math.min(100, p); const pr = stage().querySelector('[data-flash-progress]'); if (pr) pr.value = st.pct; const sl = stage().querySelector('[data-slot="percent"]'); if (sl) sl.textContent = st.pct + '%';
        if (p >= 100) { clearInterval(iv); st.run = 'idle'; st.res = 'done'; redraw('s-flash'); }
      }, 300);
    },
  });
  reg({ kind: 'full', id: 's-flash', t: 'Flash a panel', cat: 'Operator tools', path: '/admin/flash', desc: 'Write a new or recovered panel\'s whole firmware over USB from the browser (Web Serial). Press the flash button to run the simulated step list.' });

  /* ================================================================== OPERATOR TOOLS: rundown (/screen?rundown=) */
  const RD = { view: 'full', layout: 'fit', cards: 'person', data: 'live' };
  const hue = (n) => { let h = 0; for (const c of n || '') h = (h * 31 + c.charCodeAt(0)) >>> 0; return h % 360; };
  const ini = (n) => n.split(/\s+/).slice(0, 2).map((s) => s[0]).join('').toUpperCase();
  const face = (n) => `style="background:hsl(${hue(n)} 45% 48%)"`;
  const R_ITEMS = [
    { s: 'pre', t: 'Walk-in music', pt: '10:15', at: '10:15', len: '15:00', st: 'done' },
    { s: 'pre', t: 'Countdown video', pt: '10:29', at: '10:29', len: '1:00', st: 'done' },
    { s: 'during', t: 'Welcome & prayer', pt: '10:30', at: '10:30', len: '3:00', st: 'done' },
    { s: 'during', t: 'Amazing Grace (My Chains Are Gone)', pt: '10:33', at: '10:33', len: '4:30', st: 'done', key: 'G', lead: 'Dana Reyes' },
    { s: 'during', t: 'Goodness of God', pt: '10:38', at: '10:39', len: '5:30', st: 'current', key: 'A', lead: 'Dana Reyes', mics: [['1', 'Dana'], ['2', 'Priya'], ['3', 'Sam']] },
    { s: 'during', t: 'Announcements video', pt: '10:44', at: '10:45', len: '2:00', st: 'next', late: 1 },
    { s: 'during', t: 'Offering', pt: '10:46', at: '10:47', len: '4:00', st: '', late: 1, key: 'D', lead: 'Priya Nair', mics: [['2', 'Priya']] },
    { s: 'during', t: 'Message: Anchored in Hope', pt: '10:50', at: '10:51', len: '32:00', st: '', late: 1 },
    { s: 'during', t: 'Response song: Great Are You Lord', pt: '11:22', at: '11:23', len: '4:00', st: '', late: 1, key: 'E', lead: 'Dana Reyes', mics: [['1', 'Dana'], ['2', 'Priya']] },
    { s: 'during', t: 'Benediction', pt: '11:26', at: '11:27', len: '2:00', st: '', late: 1 },
    { s: 'post', t: 'Walk-out music', pt: '11:28', at: '11:29', len: '6:00', st: '', late: 1 },
  ];
  const R_MICS = [
    { m: '1', n: 'Dana Reyes', p: 'Worship leader', on: 1 }, { m: '2', n: 'Priya Nair', p: 'Vocals', on: 1 }, { m: '3', n: 'Sam Okafor', p: 'Vocals', on: 1 },
    { m: '4', n: 'Luis Ortega', p: 'Acoustic guitar', on: 0 }, { m: '5', n: '', p: 'Bass', on: 0 }, { m: '6', lt: 'Pulpit', n: 'Pastor Dave Lindqvist', p: 'Speaker', on: 0 },
    { m: '7', lt: 'Lectern', n: 'Hannah Cole', p: 'Announcements', on: 0 }, { m: '8', n: 'Marcus Webb', p: 'Drums', on: 0 },
  ];
  const R_PEOPLE = [
    { team: 'Worship', n: 'Dana Reyes', p: 'Worship leader', status: 'confirmed', mic: '1', singing: 1 }, { team: 'Worship', n: 'Priya Nair', p: 'Vocals', status: 'confirmed', mic: '2', singing: 1 },
    { team: 'Worship', n: 'Sam Okafor', p: 'Vocals', status: 'confirmed', mic: '3', singing: 1 }, { team: 'Worship', n: 'Luis Ortega', p: 'Acoustic guitar', status: 'confirmed', mic: '4' },
    { team: 'Worship', n: 'Marcus Webb', p: 'Drums', status: 'pending', mic: '8' }, { team: 'Worship', n: 'Hannah Cole', p: 'Keys', status: 'declined' },
    { team: 'Worship', n: '', p: 'Bass', status: 'unfilled', mic: '5' },
    { team: 'Production', n: 'Tom Ellis', p: 'Sound', status: 'confirmed' }, { team: 'Production', n: 'Ana Silva', p: 'Cameras', status: 'confirmed' }, { team: 'Production', n: 'Jo Park', p: 'Slides', status: 'confirmed' },
  ];
  function xrRow(it, side) {
    const meta = (it.key || it.lead) ? `<div class="m">${it.key ? `<span class="key">${it.key}</span>` : ''}${it.lead ? `<span>${it.lead} leads</span>` : ''}${!side && it.mics ? it.mics.map((x) => `<span><span class="mic">${x[0]}</span> ${x[1]}</span>`).join('') : ''}</div>` : '';
    return `<div class="rr ${it.st}"><span class="pt">${it.pt}</span><span class="at${it.late ? ' late' : ''}">${it.at}</span><div style="min-width:0"><div class="t">${esc(it.t)}</div>${it.st === 'done' ? '' : meta}</div><span class="len">${it.len}</span></div>`;
  }
  function xrChannel(c, leader, inStrip) {
    const cls = c.lt ? 'label ' : ''; const state = !c.n ? 'spare' : c.on ? 'on' : 'idle';
    return `<div class="ch ${cls}${state}"><div class="face"${c.n && !c.lt ? ' ' + face(c.n) : ''}>${c.lt ? `<div class="lt">${c.lt}</div>` : c.n ? `<span class="ini">${ini(c.n)}</span>` : ''}<span class="num">${c.m}</span></div><div class="name">${c.n ? esc(c.n) : 'Spare'}</div><div class="pos">${c.n && c.n === leader ? '<span class="lead">Leading</span> · ' : ''}${c.n ? c.p : c.p + ' · unfilled'}</div></div>`;
  }
  function xrRoster(cards) {
    const teams = [...new Set(R_PEOPLE.map((p) => p.team))];
    const pr = (p, withPos) => `<div class="p ${p.status}${p.singing ? ' singing' : ''}"><span class="pa${p.status === 'unfilled' ? ' empty' : ''}" ${p.status === 'unfilled' ? '' : face(p.n)}>${p.status === 'unfilled' ? '?' : ini(p.n)}</span><div class="x"><div class="n">${p.status === 'unfilled' ? 'Unfilled' : esc(p.n)}</div>${withPos ? `<div class="s">${p.p}</div>` : ''}</div>${p.status === 'pending' || p.status === 'declined' ? `<span class="tag ${p.status}">${p.status}</span>` : ''}${p.mic ? `<span class="mic">${p.mic}</span>` : ''}</div>`;
    return teams.map((t) => {
      const mine = R_PEOPLE.filter((p) => p.team === t);
      let g;
      if (cards === 'position') { const pos = [...new Set(mine.map((p) => p.p))]; g = pos.map((x) => { const ps = mine.filter((p) => p.p === x), mics = ps.filter((p) => p.mic).map((p) => p.mic); return `<div class="posbox"><div class="h"><span>${x}</span>${mics.length ? `<span class="mic">${mics.join(' / ')}</span>` : ''}</div>${ps.map((p) => pr(p, false)).join('')}</div>`; }).join(''); }
      else g = mine.map((p) => pr(p, true)).join('');
      return `<div class="tm">${t}</div><div class="g">${g}</div>`;
    }).join('');
  }
  function xrStage(st) {
    const compact = st.layout === 'compact', live = st.data === 'live';
    const hTitle = live ? 'Goodness of God' : 'No plan today';
    const bar = `<div class="bar"><div class="now">${live ? '<span class="livetag">LIVE</span>' : ''}<span class="t">${hTitle}</span>${live ? '<span class="key">A</span><span class="s">led by Dana Reyes</span>' : ''}</div>
<div class="left"><span class="lbl">Left</span><span class="big" id="xr-left">${live ? '2:41' : '–'}</span></div><div><span class="lbl">Next</span><span class="nx">${live ? 'Announcements video' : '–'}</span></div>
<div><span class="lbl">Planned end</span><span class="big">${live ? '11:30' : '–'}</span></div><div><span class="lbl">Projected</span><span class="big${live ? ' warn' : ''}">${live ? '11:32' : '–'}</span></div><div><span class="lbl">Running</span><span class="big${live ? ' warn' : ''}">${live ? '+1:12' : '–'}</span></div><div><span class="lbl">${live ? '10:30 service' : 'Service'}</span><span class="big" id="xr-clock">–</span></div></div>`;
    if (st.data === 'noplan') return bar + '<div class="msg">No plan today for the service type this screen follows.</div>';
    if (st.data === 'off') return bar + '<div class="msg">The rundown is switched off. Turn it on in Planning Center › Rundown on /screen.</div>';
    if (st.data === 'none') return bar + '<div class="msg">No plan today for the service type this screen follows.</div>';
    const leader = 'Dana Reyes';
    if (st.view === 'full') {
      let items = R_ITEMS;
      if (compact) { const i = items.findIndex((x) => x.st === 'current'); items = items.slice(i, i + 4); }
      let sect = '', rows = '';
      const SEC = { pre: 'Pre-service', during: 'Service', post: 'Post-service' };
      items.forEach((it) => { if (it.s !== sect && !compact) { sect = it.s; rows += `<div class="sect">${SEC[sect]}</div>`; } rows += xrRow(it, false); });
      const on = R_MICS.filter((c) => c.on);
      return bar + `<div class="full"><div class="panel"><div class="ph"><h2>Order of service</h2><span class="r">planned · actual / projected</span></div><div class="fit"><div class="rows">${rows}</div></div></div>
<div class="mid"><div class="panel"><div class="ph"><h2>On the platform</h2><span class="r">Goodness of God · mics</span></div><div class="fit"><div class="chs" style="grid-template-columns:repeat(${on.length},minmax(0,240px));grid-template-rows:minmax(0,1fr)" data-rows="1">${on.map((c) => xrChannel(c, leader)).join('')}</div></div></div>
<div class="panel"><div class="ph"><h2>Notes · Message: Anchored in Hope</h2><span class="r">10:51</span></div><div class="fit"><div class="notes">Hebrews 6:13–20. Open with the harbour story.\nThree anchors: the promise, the oath, the priest.\nClose with the communion invitation; band returns on the last line.</div></div></div></div>
<div class="panel"><div class="ph"><h2>Scheduled</h2><span class="r">10:30 · all teams</span></div><div class="fit"><div class="roster">${xrRoster(st.cards)}</div></div></div></div>`;
    }
    if (st.view === 'side') {
      const up = R_ITEMS.filter((x) => x.st !== 'done' && x.st !== 'current').slice(0, 9);
      return bar + `<div class="side"><div class="caps"><div class="capsin"><p><b>Thank you, Lord, that You are good.</b></p><p>All my life You have been faithful,</p><p>and all my life You have been so, so good.</p><p class="o">Toda mi vida has sido fiel, toda mi vida has sido bueno.</p></div></div>
<div class="panel"><div class="nowbox"><span class="lbl">Now</span><div class="tt"><span>Goodness of God</span><span id="xr-sleft">2:41</span></div><div class="m"><span class="key">A</span><span>Dana Reyes leads</span><span><span class="mic">1</span> Dana</span><span><span class="mic">2</span> Priya</span><span><span class="mic">3</span> Sam</span></div></div><div class="fit"><div class="rows" style="height:auto">${up.map((x) => xrRow(x, true)).join('')}</div></div></div></div>`;
    }
    return bar + `<div class="board" style="grid-template-columns:repeat(4,minmax(0,1fr));grid-template-rows:repeat(2,minmax(0,1fr))" data-rows="2">${R_MICS.map((c) => xrChannel(c, leader)).join('')}</div>`;
  }
  injectCSS('xr', `
.xr-wrap{position:relative;overflow:hidden;background:var(--bg);color:var(--fg);height:100vh}
.xr{position:absolute;top:0;left:0;width:1920px;height:1080px;display:flex;flex-direction:column;padding:24px 28px 28px;gap:18px;background:var(--bg);transform-origin:0 0;font-variant-numeric:tabular-nums;overflow:hidden;--faint:color-mix(in srgb,var(--muted) 55%,var(--bg));--pn:var(--card);--mic:var(--primary)}
.xr *{box-sizing:border-box}.xr [hidden]{display:none!important}
.xr .bar{height:84px;flex:none;display:flex;align-items:stretch;border:1px solid var(--border);background:var(--pn)}
.xr .bar>div{display:flex;flex-direction:column;justify-content:center;padding:0 24px;border-right:1px solid var(--border);min-width:0}.xr .bar>div:last-child{border-right:0}
.xr .lbl{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);font-weight:500}
.xr .bar .now{flex:1;flex-direction:row;align-items:center;justify-content:flex-start;gap:18px}
.xr .now .t{font-size:38px;font-weight:700;letter-spacing:-.02em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin:0}.xr .now .s{font-size:18px;color:var(--muted);white-space:nowrap}
.xr .livetag{background:var(--err);color:var(--bg);font-weight:700;font-size:14px;letter-spacing:.1em;padding:6px 10px}
.xr .big{font:500 34px/1.05 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:nowrap}.xr .big.warn{color:var(--warn)}.xr .big.over{color:var(--err)}
.xr .left{background:color-mix(in srgb,var(--err) 12%,var(--pn))}.xr .left .big{font-size:44px}
.xr .nx{font-size:22px;font-weight:600;max-width:300px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.xr .key{display:inline-block;font-weight:500;color:var(--primary);border:1px solid color-mix(in srgb,var(--primary) 45%,var(--border));padding:1px 7px;font-family:ui-monospace,monospace}
.xr .msg{flex:1;display:grid;place-items:center;font-size:34px;color:var(--muted);border:1px solid var(--border);text-align:center;padding:40px;background:transparent}
.xr .panel{border:1px solid var(--border);background:var(--pn);display:flex;flex-direction:column;min-height:0;overflow:hidden}
.xr .ph{flex:none;display:flex;align-items:baseline;gap:12px;padding:14px 18px 10px;border-bottom:1px solid var(--border)}
.xr .ph h2{margin:0;font-size:20px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.xr .ph .r{margin-left:auto;color:var(--muted);font-size:14px;white-space:nowrap}
.xr .fit{flex:1;min-height:0;overflow:hidden}
.xr .full{flex:1;min-height:0;display:grid;grid-template-columns:740px 1fr 520px;gap:18px}.xr .mid{display:grid;grid-template-rows:1.15fr 1fr;gap:18px;min-height:0}
.xr .rows{display:flex;flex-direction:column;height:100%}
.xr .sect{flex:none;font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--faint);padding:8px 18px 4px}
.xr .rr{flex:1 1 0;min-height:0;max-height:110px;display:grid;grid-template-columns:70px 78px 1fr 70px;gap:12px;align-items:center;padding:0 18px;border-top:1px solid var(--border);overflow:hidden}
.xr .sect+.rr{border-top:0}
.xr .rr .pt{font:18px ui-monospace,monospace;color:var(--faint)}.xr .rr .at{font:22px ui-monospace,monospace}.xr .rr .at.late{color:var(--warn)}
.xr .rr .t{font-size:22px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.xr .rr .m{display:flex;gap:8px;margin-top:4px;font-size:15px;color:var(--muted);white-space:nowrap;overflow:hidden;align-items:center}
.xr .rr .len{font:16px ui-monospace,monospace;color:var(--muted);text-align:right}
.xr .rr.done,.xr .rr.done .at,.xr .rr.done .t{color:var(--faint)}
.xr .rr.current{background:color-mix(in srgb,var(--err) 12%,var(--pn));box-shadow:inset 6px 0 0 var(--err)}.xr .rr.current .t{font-weight:700}
.xr .rr.next{box-shadow:inset 6px 0 0 var(--border)}
.xr .mic{font-family:ui-monospace,monospace;color:var(--mic)}
.xr .chs{display:grid;gap:10px;padding:12px;height:100%;justify-content:center}
.xr .ch{min-width:0;min-height:0;display:flex;flex-direction:column;border:1px solid var(--border);background:var(--bg)}
.xr .ch .face{flex:1;min-height:0;position:relative;display:grid;place-items:center;background:var(--hover);overflow:hidden}
.xr .ch .ini{font-size:72px;font-weight:600;color:color-mix(in srgb,var(--bg) 40%,transparent);letter-spacing:-.04em;line-height:1}
.xr .ch .num{position:absolute;top:8px;left:10px;font:500 20px ui-monospace,monospace;color:var(--fg);background:color-mix(in srgb,var(--bg) 70%,transparent);padding:0 6px}
.xr .ch .lt{position:relative;font-size:26px;font-weight:700;color:var(--primary-fg);text-align:center;padding:0 10px}
.xr .ch .name{flex:none;padding:8px 10px 2px;font-size:22px;font-weight:700;white-space:nowrap;overflow:hidden}.xr .ch .pos{flex:none;padding:0 10px 10px;font-size:13px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.xr .ch .lead{color:var(--mic);font-weight:600}.xr .ch.idle .face{filter:grayscale(1) brightness(.6)}.xr .ch.idle .name{opacity:.45}.xr .ch.on{border-color:var(--mic)}
.xr .ch.spare .face{background:repeating-linear-gradient(135deg,var(--pn) 0 12px,var(--bg) 12px 24px)}.xr .ch.spare .name{color:var(--faint);font-weight:500}
.xr .ch.label .face{background:var(--primary)}
.xr .notes{padding:14px 20px;font-size:22px;line-height:1.45;color:var(--fg);white-space:pre-line;opacity:.9}
.xr .roster{padding:10px 14px;font-size:17px}.xr .tm{font-size:.62em;letter-spacing:.14em;text-transform:uppercase;color:var(--faint);padding:.5em 0 .35em}
.xr .g{display:grid;grid-template-columns:1fr 1fr;gap:.35em .8em}.xr .p{display:flex;gap:.55em;align-items:center;min-width:0}
.xr .pa{width:2.2em;height:2.2em;flex:none;display:grid;place-items:center;font-size:.8em;font-weight:700;color:var(--bg);overflow:hidden}
.xr .pa.empty{background:none;border:1px dashed var(--faint);color:var(--faint)}
.xr .p .x{min-width:0;flex:1}.xr .p .n{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.xr .p .s{font-size:.7em;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.xr .tag{font-size:.6em;font-weight:700;letter-spacing:.08em;text-transform:uppercase;padding:1px 5px;flex:none}.xr .tag.pending{background:var(--warn);color:var(--bg)}.xr .tag.declined{border:1px solid var(--faint);color:var(--muted)}
.xr .p.declined{opacity:.45}.xr .p.declined .n{text-decoration:line-through}.xr .p.unfilled .n{color:var(--faint);font-weight:500}.xr .p.singing .n{color:var(--mic)}
.xr .posbox{border:1px solid var(--border);padding:.4em .5em;display:flex;flex-direction:column;gap:.3em;min-width:0}.xr .posbox .h{display:flex;justify-content:space-between;gap:.5em;font-size:.65em;color:var(--muted);text-transform:uppercase;letter-spacing:.1em}
.xr .side{flex:1;min-height:0;display:grid;grid-template-columns:1fr 560px;gap:18px}
.xr .caps{min-height:0;border:1px solid var(--border);background:var(--bg);display:flex;align-items:flex-end;padding:60px}.xr .capsin{font-size:64px;line-height:1.3;font-weight:600}.xr .capsin p{margin:0 0 12px}.xr .capsin .o{color:var(--muted);font-weight:400}
.xr .nowbox{flex:none;padding:16px 18px;background:color-mix(in srgb,var(--err) 12%,var(--pn));border-bottom:1px solid var(--border);box-shadow:inset 6px 0 0 var(--err)}
.xr .nowbox .tt{display:flex;align-items:baseline;gap:12px;font-size:32px;font-weight:700;margin:4px 0 8px}.xr .nowbox .tt span:last-child{margin-left:auto;font:500 32px ui-monospace,monospace}
.xr .nowbox .m{font-size:17px;color:var(--muted);display:flex;gap:12px;flex-wrap:wrap;align-items:center}
.xr .side .rr{flex:none;height:76px;grid-template-columns:86px 1fr}.xr .side .rr .pt,.xr .side .rr .len{display:none}
.xr .board{flex:1;min-height:0;display:grid;gap:12px}.xr .board .ch .name{font-size:28px;padding:12px 14px 2px}.xr .board .ch .pos{font-size:15px;padding:0 14px 14px}.xr .board .ch .num{font-size:24px;top:12px;left:14px}.xr .board .ch .ini{font-size:110px}
.xr.cmp .rr{max-height:none}.xr.cmp .rr .t{font-size:30px}.xr.cmp .rr .m{font-size:18px}.xr.cmp .rr .at{font-size:28px}
`);
  FULL['s-rundown'] = () => {
    const st = S('s-rundown', RD);
    return `<div class="xr-wrap" id="xr-wrap"><div class="xr${st.layout === 'compact' ? ' cmp' : ''}" id="xr-stage">${xrStage(st)}</div></div>` +
      demo('s-rundown', [['view', 'View', [['full', 'Full'], ['side', 'Side'], ['micboard', 'Micboard']]], ['layout', 'Layout', [['fit', 'Fit'], ['compact', 'Compact']]], ['cards', 'Cards', [['person', 'Person'], ['position', 'Position']]], ['data', 'Plan', [['live', 'Live'], ['noplan', 'No plan'], ['off', 'Switched off']]]]);
  };
  AFTER['s-rundown'] = () => {
    const fit = () => { const w = stage().clientWidth, h = stage().clientHeight - 100, k = Math.min(w / 1920, h / 1080), s = document.getElementById('xr-stage'); if (!s) return; s.style.transform = `translate(${(w - 1920 * k) / 2}px,${36 + (h - 1080 * k) / 2}px) scale(${k})`; document.getElementById('xr-wrap').style.height = (h + 100) + 'px'; };
    fit(); window.__xrfit = fit; if (!window.__xrbound) { window.__xrbound = 1; addEventListener('resize', () => { if (alive('#xr-stage')) window.__xrfit(); }); }
    let left = 161, t0 = Date.now();
    const tick = () => {
      const d = new Date(), c = document.getElementById('xr-clock'); if (c) c.textContent = '10:41:' + String(10 + Math.floor((Date.now() - t0) / 1000) % 50).padStart(2, '0');
      const s = Math.max(0, left - Math.floor((Date.now() - t0) / 1000)), t = Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
      ['xr-left', 'xr-sleft'].forEach((id) => { const e = document.getElementById(id); if (e) e.textContent = ST['s-rundown'].data === 'live' ? t : '–'; });
    };
    tick(); every(1000, '#xr-stage', tick);
  };
  reg({ kind: 'full', id: 's-rundown', t: 'Rundown', cat: 'Operator tools', path: '/screen?rundown=full|side|micboard', desc: "The production team's service rundown: a fixed 1920x1080 kiosk board with Full, Side (captions plus rail) and Micboard views, fed by Planning Center." });

  /* ================================================================== OPERATOR TOOLS: status */
  FULL['s-status'] = () => { S('s-status', { v: 'ok' }); return `<div class="xs ox-pad"><main class="xs-grid" id="xs-grid" aria-live="polite"></main><p class="xs-foot"><span class="ox-dot" id="connDot" title="Connection to Relay"></span> <span id="connText">Connecting…</span></p></div>` +
    demo('s-status', [['v', 'State', [['ok', 'Healthy'], ['off', 'Off air'], ['trouble', 'Trouble'], ['stopped', 'Stopped'], ['nomod', 'No livestream module'], ['lost', 'Connection lost']]]]); };
  AFTER['s-status'] = () => {
    const t0 = Date.now();
    const el = (s) => { s = Math.max(0, Math.floor(s)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60, two = (n) => (n < 10 ? '0' : '') + n; return h ? h + ':' + two(m) + ':' + two(x) : m + ':' + two(x); };
    const card = (id, h, tone, big, sub, extra, cls) => `<section class="xs-card ${cls || ''} ${tone}" id="${id}"><h2>${h}</h2><div class="xs-big">${big}</div><div class="xs-sub">${sub}</div>${extra || ''}</section>`;
    const paint = () => {
      const g = document.getElementById('xs-grid'); if (!g) return; const v = ST['s-status'].v, d = (Date.now() - t0) / 1000;
      const live = v === 'ok' || v === 'trouble' || v === 'lost';
      const streams = v === 'nomod' ? [] : [
        { l: 'YouTube', on: live, since: 4364, h: v === 'trouble' ? 'error' : 'ok', w: 214 },
        { l: 'Facebook', on: live, since: 4360, h: v === 'ok' ? 'warn' : 'ok', w: 38 },
        { l: 'Church Online Platform', on: live, since: 4352, h: 'ok', w: 61 }];
      const HEALTH = { ok: 'healthy', warn: 'needs a look', error: 'trouble', idle: 'standing by' };
      let air;
      if (!streams.length) air = card('airCard', 'Livestream', '', 'Not watched', 'No livestream module is on', '', 'xs-air');
      else if (live) air = card('airCard', 'Livestream', 'live', 'On air', el(4364 + d), `<ul class="xs-streams" id="streams">${streams.map((s) => `<li class="${s.h}"><span>${s.l}</span><span>live ${el(s.since + d)} · ${HEALTH[s.h]} · ${s.w} watching</span></li>`).join('')}</ul>`, 'xs-air');
      else air = card('airCard', 'Livestream', '', 'Off air', '', `<ul class="xs-streams" id="streams">${streams.map((s) => `<li class="idle"><span>${s.l}</span><span>off air · standing by</span></li>`).join('')}</ul>`, 'xs-air');
      const run = v !== 'stopped';
      const cap = run ? card('capCard', 'Captions', v === 'trouble' ? 'warn' : 'ok', 'Running', el(4805 + d) + ' · ' + (v === 'trouble' ? 'no language live' : 'English, Spanish, Korean')) : card('capCard', 'Captions', '', 'Stopped', '');
      const inp = v === 'stopped' ? ['Off', '', 0, ''] : v === 'trouble' ? ['Clipping', 'err', 96, '-2 dB'] : ['OK', 'ok', 66 + Math.sin(d) * 4, '-21 dB'];
      const input = card('inCard', 'Input', inp[1], inp[0], inp[3], `<div class="xs-meter"><i id="inFill" style="width:${inp[2]}%"></i></div>`);
      const view = card('viewCard', 'Watching', '', v === 'stopped' ? '0' : '37', "on Relay's caption pages");
      g.innerHTML = air + cap + input + view;
      const bad = v === 'lost';
      document.getElementById('connDot').className = 'ox-dot ' + (bad ? 'err' : 'ok');
      document.getElementById('connText').textContent = bad ? 'Not hearing from Relay' : 'Live from Relay';
    };
    paint(); every(1000, '#xs-grid', paint);
  };
  reg({ kind: 'full', id: 's-status', t: 'Status', cat: 'Operator tools', path: '/status', desc: 'Read-only booth and sound desk screen: livestream, captions, input level and viewers on four large cards.' });

  /* ================================================================== OPERATOR TOOLS: tally */
  const CAMS = [{ n: 1, name: 'Wide' }, { n: 2, name: 'Pulpit' }, { n: 3, name: 'Choir' }, { n: 4, name: 'Handheld' }];
  FULL['s-tally'] = () => {
    const st = S('s-tally', { view: 'camera', me: 2, pgm: 1, pvw: 2, link: 'live', usb: '0' });
    const me = Number(st.me), pgm = Number(st.pgm), pvw = Number(st.pvw);
    const lost = st.link === 'lost', none = st.link === 'none';
    const cam = CAMS.find((c) => c.n === me);
    const tone = (n) => lost ? 'lost' : n === pgm ? 'live' : n === pvw ? 'next' : '';
    const label = (c) => c.name || 'Camera ' + c.n;
    const names = (ns) => ns.map((n) => label(CAMS.find((c) => c.n === n))).join(', ') || '—';
    const stateText = (t, idle) => lost ? 'Connection lost' : !me ? 'Add ?camera=1 to the address' : none ? 'No switcher connected' : !cam ? 'No camera ' + me : t === 'live' ? 'LIVE' : t === 'next' ? 'NEXT' : idle;
    const demoBar = demo('s-tally', [['view', 'View', [['camera', 'Camera'], ['operator', 'Operator'], ['board', 'Board']]], ['me', 'My camera', [[1, '1'], [2, '2'], [3, '3'], [4, '4'], [9, '9'], [0, 'none']]], ['pgm', 'Program', [[1, '1'], [2, '2'], [3, '3'], [4, '4']]], ['pvw', 'Preview', [[1, '1'], [2, '2'], [3, '3'], [4, '4']]], ['link', 'Link', [['live', 'Live'], ['lost', 'Lost'], ['none', 'No switcher']]], ['usb', 'USB camera', [['0', 'Off'], ['1', 'On']]]]);
    if (st.view === 'camera') {
      const t = (me && cam && !none) ? tone(me) : lost ? 'lost' : '';
      return `<div class="xt"><main class="xt-cam${t ? ' xt-' + t : ''}" id="cam" aria-live="polite"><div class="xt-num" id="camNum">${me || '—'}</div><div class="xt-name" id="camName">${cam && !none && !lost ? label(cam) : ''}</div><div class="xt-state" id="camState">${stateText(t, '')}</div></main></div>` + demoBar;
    }
    if (st.view === 'operator') {
      const t = (me && cam && !none) ? tone(me) : lost ? 'lost' : '';
      const live = [pgm], next = [pvw];
      return `<div class="xt"><main class="xt-op" id="op" aria-live="polite"><section class="xt-main${t ? ' xt-' + t : ''}" id="opMain"><div class="xt-num" id="opNum">${me || '—'}</div><div class="xt-name" id="opName">${cam && !none && !lost ? label(cam) : ''}</div><div class="xt-state" id="opState">${stateText(t, 'Standby')}</div>
${st.usb === '1' ? `<div class="xt-vid" id="opVideo">USB camera preview · Logitech C920</div>` : ''}<label class="xt-pick" id="opPick">USB camera <select id="opSel"><option>${st.usb === '1' ? 'Logitech C920' : 'Choose USB camera…'}</option><option>No USB camera</option><option>Logitech C920</option><option>Elgato Cam Link 4K</option></select></label></section>
<section class="xt-bar"><div class="xt-slot" id="opLive"><span>Live</span><strong>${lost || none ? '—' : names(live)}</strong></div><div class="xt-slot" id="opNext"><span>Next</span><strong>${lost || none ? '—' : names(next)}</strong></div></section></main></div>` + demoBar;
    }
    const tiles = none || lost && false ? '' : CAMS.map((c) => { const tn = tone(c.n); return `<div class="xt-tile${tn ? ' xt-' + tn : ''}"><div class="xt-tnum">${c.n}</div><div class="xt-tname">${label(c)}</div><div class="xt-tst">${tn === 'live' ? 'LIVE' : tn === 'next' ? 'NEXT' : ''}</div>${st.usb === '1' && c.n === 2 ? '<div class="xt-vid">USB camera</div>' : ''}${st.usb === '1' ? `<select aria-label="USB camera for camera ${c.n}"><option>${c.n === 2 ? 'Logitech C920' : 'No USB camera'}</option></select>` : ''}</div>`; }).join('');
    return `<div class="xt"><main class="xt-board" id="board" aria-live="polite">${none ? '<p class="xt-empty">No switcher connected</p>' : tiles}</main>
<p class="xt-foot"><span class="ox-dot ${lost ? '' : 'ok'}" id="connDot" title="Connection to Relay"></span> <span id="connText">${lost ? 'Connection lost' : 'Live'}</span>${st.usb === '1' ? '' : '<button type="button" class="btn sm" id="usbAll" onclick="OPX.set(\'s-tally\',\'usb\',\'1\')">Show USB cameras</button>'}</p></div>` + demoBar;
  };
  reg({ kind: 'full', id: 's-tally', t: 'Tally', cat: 'Operator tools', path: '/tally?camera=<n> · ?view=operator · ?view=board', desc: 'A phone on a camera (red on program, green on preview), the camera operator view with a USB preview, and the director board.' });
})();
