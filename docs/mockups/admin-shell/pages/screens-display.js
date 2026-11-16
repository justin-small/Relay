/* Viewer-facing and stage/room DISPLAY screens (kind 'full'). Spec twin: docs/redesign/screens/screens-display.md
   Every class inside a screen is scoped under .dv / .dvsv / .dvsg / .dvlt / .dvob (my prefix is "dv"); the viewer
   screens deliberately keep the REAL class names of viewer.css (.bar .panes .pane .feed .log .jump .empty
   .settled .open ...) under the .dv wrapper so the spec can say "same selectors, different tokens". */
(function () {
  'use strict';
  const H = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');

  /* ------------------------------------------------------------------ sample data: a sermon on John 15 */
  const TXT = {
    en: ['Good morning, church. It is so good to be together.',
      'Open your Bibles with me to John chapter fifteen.',
      'Jesus says, “I am the vine, and you are the branches.”',
      'Whoever abides in me and I in him, he it is that bears much fruit.',
      'Now notice what Jesus does not say.',
      'He does not say, try harder to produce fruit.',
      'He says, stay connected to the one who already has the life you need.',
      'A branch never strains to grow a grape.',
      'It simply stays where the sap is flowing.',
      'So this week, ask yourself one question: where am I trying to live on my own strength?'],
    es: ['Buenos días, iglesia. Qué bueno estar juntos.',
      'Abran sus Biblias conmigo en Juan capítulo quince.',
      'Jesús dice: «Yo soy la vid, y ustedes son los pámpanos».',
      'El que permanece en mí, y yo en él, este lleva mucho fruto.',
      'Ahora fíjense en lo que Jesús no dice.',
      'No dice: esfuércense más para dar fruto.',
      'Dice: permanezcan unidos a quien ya tiene la vida que ustedes necesitan.',
      'Una rama nunca se esfuerza por hacer crecer una uva.',
      'Simplemente permanece donde corre la savia.',
      'Así que esta semana, pregúntense una cosa: ¿en qué área trato de vivir con mis propias fuerzas?'],
    pt: ['Bom dia, igreja. Que bom estarmos juntos.',
      'Abram suas Bíblias comigo em João capítulo quinze.',
      'Jesus diz: «Eu sou a videira, e vocês são os ramos».',
      'Quem permanece em mim, e eu nele, esse dá muito fruto.',
      'Agora reparem no que Jesus não diz.',
      'Ele não diz: esforcem-se mais para dar fruto.',
      'Ele diz: permaneçam unidos àquele que já tem a vida de que vocês precisam.',
      'Um ramo nunca se esforça para produzir uma uva.',
      'Ele simplesmente permanece onde a seiva corre.',
      'Então, esta semana, façam a si mesmos uma pergunta: onde estou tentando viver com as minhas próprias forças?']
  };
  const LANGS = { es: { target: 'SPANISH', label: 'Spanish', native: 'Español' }, pt: { target: 'PORTUGUESE', label: 'Portuguese', native: 'Português' } };

  /* ------------------------------------------------------------------ CSS */
  const LOOK = [{ k: 'theme', l: 'Theme', o: [['dark', 'Dark'], ['light', 'Light']] },
    { k: 'look', l: 'Look', o: [['relay', 'Relay'], ['softpop', 'Soft pop'], ['brutalist', 'Brutalist'], ['tangerine', 'Tangerine']] }];
  const CSS = `
.dvhost{container:dvc/size;position:relative;width:100%;height:100vh;overflow:hidden;background:var(--bg);color:var(--fg)}
/* demo chip: the only non-product element on a screen */
.dvchip{position:absolute;right:14px;bottom:14px;z-index:300;width:min(310px,calc(100% - 28px));background:var(--card);color:var(--fg);border:1px solid var(--border);border-radius:var(--r);box-shadow:0 10px 34px #0007,var(--shadow);font:12px/1.35 var(--font);max-height:calc(100% - 28px);overflow:auto}
.dvchip.left{right:auto;left:14px;bottom:14px}.dvchip .dvs{position:absolute;top:6px;right:30px;font:inherit;padding:1px 7px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--muted);cursor:pointer}.dvchip>button.dvh{all:unset;box-sizing:border-box;display:flex;align-items:center;gap:8px;width:100%;padding:8px 12px;cursor:pointer;font-weight:600}
.dvchip>button.dvh::after{content:"\\25BE";margin-left:auto;color:var(--muted)}
.dvchip.shut>button.dvh::after{content:"\\25B4"}
.dvchip .dvb{padding:2px 12px 12px;display:grid;gap:9px}.dvchip.shut .dvb{display:none}
.dvchip .dvg>span{display:block;color:var(--muted);font-size:11px;margin-bottom:3px}
.dvchip .dvo{display:flex;flex-wrap:wrap;gap:4px}
.dvchip .dvo button{font:inherit;padding:3px 8px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--fg);cursor:pointer}
.dvchip .dvo button:hover{background:var(--hover);color:var(--hover-fg)}
.dvchip .dvo button[aria-pressed=true]{background:var(--hover);color:var(--hover-fg);border-color:var(--primary);font-weight:600}
.dvchip .dvn{color:var(--muted);font-size:11px;margin:0}

/* ---------- viewer (viewer.css, re-tokenised) ---------- */
.dv{--dv-settled:color-mix(in srgb,var(--fg) 78%,var(--bg));--dv-open:var(--fg);--font-px:40px;position:absolute;inset:0;display:flex;flex-direction:column;background:var(--bg);color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,system-ui,sans-serif;-webkit-text-size-adjust:100%;overflow:hidden}
.dv .bar{display:flex;align-items:center;gap:12px;padding:8px 14px;border-bottom:1px solid var(--border);background:var(--sb);flex:0 0 auto;font-size:13px;color:var(--muted);position:relative;z-index:5}
.dv .bar .spacer{flex:1}
.dv .bar a{color:var(--muted);text-decoration:none;padding:4px 8px;border-radius:calc(var(--r) - 4px);cursor:pointer}
.dv .bar a:hover,.dv .bar a.on{color:var(--fg);background:var(--card)}
.dv .bar select,.dv .bar button{background:var(--card);color:var(--fg);border:1px solid var(--border);border-radius:calc(var(--r) - 4px);padding:5px 9px;font:inherit;font-size:13px;cursor:pointer}
.dvhost:not(.dvin)>.dv .bar{padding-left:124px}.dv .bar select{width:auto}.dv .bar select:disabled{opacity:.6;cursor:default}
.dv .bar button:active{background:var(--hover)}
.dv .bar button.on{background:var(--hover);color:var(--hover-fg);border-color:var(--primary)}
.dv .dot{width:8px;height:8px;border-radius:50%;background:var(--muted);flex:0 0 auto;animation:none}
.dv .dot.ok{background:var(--ok)}.dv .dot.warn{background:var(--warn)}.dv .dot.err{background:var(--err)}
.dv #langPicker{max-width:42cqw;text-overflow:ellipsis}
.dv .panes{flex:1;display:flex;min-height:0}
.dv .panes.dual{flex-direction:column}
.dv .pane{flex:1;min-height:0;display:flex;flex-direction:column;position:relative}
.dv .panes.dual .pane+.pane{border-top:1px solid var(--border)}
.dv .pane-label{position:absolute;top:10px;right:14px;z-index:2;font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);background:color-mix(in srgb,var(--bg) 75%,transparent);padding:3px 8px;border-radius:4px;pointer-events:none}
.dv .feed{flex:1;overflow-y:auto;padding:28px 22px 45cqh 22px;font-size:var(--font-px);line-height:1.5;font-weight:500;letter-spacing:-.005em;scrollbar-width:thin;scrollbar-color:var(--border) transparent}
/* the reading ergonomics: 34ch measure, generous leading, settled vs open colour. Unchanged from viewer.css. */
.dv .feed p{margin:0 auto .55em auto;max-width:34ch}
.dv .feed p.settled{color:var(--dv-settled)}
.dv .feed p.open{color:var(--dv-open)}
.dv .feed p.open::after{content:"";display:inline-block;width:.5em;height:1.05em;vertical-align:-.16em;margin-left:.06em;background:var(--primary);animation:dvblink 1.05s steps(2,start) infinite}
@keyframes dvblink{to{visibility:hidden}}
.dv .empty{color:var(--muted);font-size:17px;font-weight:400;line-height:1.5;max-width:none!important;margin:0}
.dv .jump{position:absolute;left:50%;bottom:18px;transform:translateX(-50%);background:var(--card);color:var(--fg);border:1px solid var(--border);border-radius:999px;padding:8px 16px;font:inherit;font-size:14px;cursor:pointer;z-index:6;box-shadow:0 4px 14px #0006,var(--shadow)}
.dv .jump[hidden]{display:none}.dv .jump:active{background:var(--hover)}
@media (prefers-reduced-motion:reduce){.dv .feed p.open::after{animation:none}}
.dv.present .bar{display:none}
.dv.present .feed{display:flex;flex-direction:column;justify-content:flex-end;overflow:hidden;padding:4cqh 6cqw 6cqh 6cqw;font-size:calc(var(--font-px)*1.15)}
.dv.present .jump{display:none}
.dv #presentExit{position:absolute;top:14px;right:16px;z-index:30;width:40px;height:40px;border-radius:50%;background:var(--card);color:var(--fg);border:1px solid var(--border);font-size:18px;line-height:1;cursor:pointer;opacity:.5}
.dv #presentExit:hover{opacity:1}.dv #presentExit[hidden]{display:none}
.dv .toast{position:absolute;left:50%;bottom:22px;transform:translateX(-50%) translateY(160%);background:var(--card);border:1px solid var(--border);color:var(--fg);padding:9px 16px;border-radius:999px;font-size:14px;transition:transform .22s ease;z-index:20;pointer-events:none;white-space:nowrap}
.dv .toast.show{transform:translateX(-50%) translateY(0)}
.dv .panes.dual .feed{padding-bottom:16cqh}
@container dvc (min-width:900px){.dv .panes.dual{flex-direction:row}.dv .panes.dual .pane+.pane{border-top:0;border-left:1px solid var(--border)}.dv .panes.dual .feed{padding-bottom:45cqh}}
@container dvc (max-width:520px){.dv .bar{flex-wrap:wrap;gap:6px;padding:7px 9px;font-size:12px}.dv .bar a{padding:4px 6px}.dv .bar select,.dv .bar button{padding:5px 7px;font-size:12px}.dv .feed{padding:20px 16px 45cqh 16px}.dv .panes.dual .feed{padding-bottom:16cqh}}
/* screen mode (/screen): kiosk */
.dv.screen .bar{display:none}
.dv.screen .feed{display:flex;flex-direction:column;overflow:hidden;padding:4cqh 6cqw;font-size:clamp(30px,5.5cqw,110px)}
.dv.screen.align-bottom .feed{justify-content:flex-end}.dv.screen.align-center .feed{justify-content:center}
.dv.screen.screen-fixedfont .feed{font-size:var(--font-px)}
.dv.screen .jump,.dv.screen .toast{display:none}
.dv.screen .feed p.open::after{display:none}
.dv .screen-fault{position:absolute;right:14px;bottom:14px;z-index:30;width:12px;height:12px;border-radius:50%;background:var(--err);box-shadow:0 0 0 3px var(--bg)}
.dv .screen-fault[hidden]{display:none}
/* alerts (#121/#214/#215/#216). The flash is a safety spec (1 Hz, red/white): kept literal, not themed. */
@keyframes dvflash{0%,49.9%{background:var(--err);color:#fff}50%,100%{background:#fff;color:#b00000}}
@keyframes dvflashw{0%,49.9%{background:var(--warn);color:#111}50%,100%{background:#1a1204;color:var(--warn)}}
.dv{--dv-info:#1f52c2}
.dv .alert-flash{position:absolute;inset:0;z-index:100;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:5cqh 5cqw;cursor:pointer;animation:dvflash 1s steps(1,end) infinite;user-select:none}
.dv .alert-flash[hidden]{display:none}
.dv .alert-flash[data-level=attention],.dv .stage-message[data-level=attention],.dv .stage-banner[data-level=attention]{animation:dvflashw 1s steps(1,end) infinite}
.dv .alert-flash[data-level=info]{animation:none;background:var(--dv-info);color:#fff}
.dv .stage-message[data-level=urgent],.dv .stage-banner[data-level=urgent]{animation:dvflash 1s steps(1,end) infinite}
.dv .alert-flash-from{font-size:clamp(48px,14cqw,220px);font-weight:800;line-height:1.05}
.dv .alert-flash-to{font-size:clamp(20px,4cqw,64px);font-weight:600;margin-top:.4em}
.dv .alert-flash-replies{display:flex;flex-wrap:wrap;justify-content:center;gap:2cqh 2cqw;margin-top:5cqh}
.dv .alert-flash-replies[hidden]{display:none}
.dv .alert-flash-replies button{font:inherit;font-size:clamp(20px,3.4cqw,54px);font-weight:700;padding:.5em 1em;border-radius:.5em;border:.12em solid currentColor;background:rgba(0,0,0,.35);color:#fff;cursor:pointer}
.dv .stage-message,.dv .stage-banner{position:absolute;z-index:90;pointer-events:none;text-align:center}
.dv .stage-message[hidden],.dv .stage-banner[hidden]{display:none}
.dv .stage-message{left:50%;top:50%;transform:translate(-50%,-50%);width:min(90cqw,1400px);padding:4cqh 4cqw;border-radius:1.2cqw;background:var(--dv-info);color:#fff;box-shadow:0 1cqh 6cqh rgba(0,0,0,.5)}
.dv .stage-message-from{font-size:clamp(14px,2cqw,32px);opacity:.8;margin-bottom:.4em}.dv .stage-message-from:empty{display:none}
.dv .stage-message-text{font-size:clamp(32px,8cqw,150px);font-weight:800;line-height:1.1;overflow-wrap:anywhere}
.dv .stage-banner{left:0;right:0;top:0;padding:1.2cqh 3cqw;background:var(--dv-info);color:#fff;font-size:clamp(20px,3.6cqw,64px);font-weight:700;line-height:1.2;overflow-wrap:anywhere}
.dv .lyrics{position:absolute;inset:0;z-index:5;display:flex;align-items:center;justify-content:center;padding:5cqh 6cqw;text-align:center;background:var(--bg)}
.dv .lyrics[hidden]{display:none}
.dv .lyrics-text{white-space:pre-line;font-size:5.2cqw;line-height:1.25;font-weight:600;max-width:100%}

/* ---------- broadcast overlay (/overlay) ---------- */
.dvov{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;padding:16px}
.dvov .dvinfo{font:12px/1.4 ui-monospace,Menlo,monospace;color:var(--muted);text-align:center}
.dvov .dvinfo b{color:var(--fg)}
.dvf{container:dvf/size;position:relative;width:min(100cqw - 32px,(100cqh - 96px)*16/9);aspect-ratio:16/9;border:1px solid var(--border);border-radius:6px;overflow:hidden;box-shadow:0 10px 40px #0006}
.dvf.alpha{background:repeating-conic-gradient(color-mix(in srgb,var(--fg) 14%,var(--bg)) 0 25%,var(--bg) 0 50%) 0 0/24px 24px}
/* the programme video: a stand-in camera shot (backdrop only, the one place literal colour is allowed) */
.dvf.video{background:radial-gradient(60% 55% at 50% 38%,#6b5a3c 0,#3a2f22 45%,#15110d 100%)}
.dvf.video::before{content:"";position:absolute;inset:0;background:linear-gradient(180deg,#0000 55%,#0009 100%),radial-gradient(14% 30% at 50% 78%,#1b1612 0,#1b1612 60%,#0000 61%)}
.dvf.video .dvfig{position:absolute;left:50%;top:30%;width:9cqw;height:9cqw;margin-left:-4.5cqw;border-radius:50%;background:#2a221a;box-shadow:0 11cqw 0 3.2cqw #241d16}
.dvf.video .dvcross{position:absolute;left:12%;top:14%;width:1.2cqw;height:12cqw;background:#e6d9b8;opacity:.8;box-shadow:0 0 5cqw #e6d9b855}
.dvf.video .dvcross::after{content:"";position:absolute;left:-3cqw;top:3.5cqw;width:7.2cqw;height:1.2cqw;background:inherit}
.dv.overlay{background:transparent;--ov-color:#fff;--ov-edge:#000;--settled:var(--ov-color);--open:var(--ov-color)}
.dv.overlay .panes{position:absolute;top:0;left:0;width:100%;height:100%;background:var(--ov-bg,transparent);display:flex;flex-direction:column;justify-content:flex-end;align-items:center;gap:.3em;font-size:calc(var(--ovpx)/1920*100cqw);padding:5cqh 5cqw;box-sizing:border-box}
.dv.overlay.ov-pos-top .panes{justify-content:flex-start}.dv.overlay.ov-pos-middle .panes{justify-content:center}
.dv.overlay.ov-align-left .panes{align-items:flex-start;text-align:left}.dv.overlay.ov-align-right .panes{align-items:flex-end;text-align:right}.dv.overlay.ov-align-center .panes{text-align:center}
.dv.overlay .pane{position:static;flex:none;min-height:0;width:var(--ovmax);max-width:100%;display:flex;flex-direction:column;align-items:inherit;transition:opacity .4s ease}
.dv.overlay.ov-hidden .pane{opacity:0}
.dv.overlay .feed{display:flex;flex-direction:column;align-items:inherit;overflow:hidden;padding:0;height:auto;flex:none;font-size:1em;line-height:1.28;font-weight:inherit;letter-spacing:normal}
.dv.overlay .log{display:flex;flex-direction:column;align-items:inherit}
.dv.overlay .ov-label{font-size:.5em;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--ov-color);opacity:.8;margin:0 .15em .15em}
.dv.overlay .feed p{width:fit-content;max-width:100%;margin:0 0 .18em;padding:.06em .34em;border-radius:.1em;color:var(--ov-color);background:rgb(var(--ov-platec,0 0 0)/var(--ov-plate))}
.dv.overlay .feed p:last-child{margin-bottom:0}
.dv.overlay .feed p.open::after{display:none}
.dv.overlay.ov-edge-plate .feed p{text-shadow:0 1px 2px rgba(0,0,0,.55)}
.dv.overlay.ov-edge-outline .feed p,.dv.overlay.ov-edge-shadow .feed p,.dv.overlay.ov-edge-none .feed p{background:transparent}
.dv.overlay.ov-edge-outline .feed p{text-shadow:-.05em -.05em 0 var(--ov-edge),0 -.05em 0 var(--ov-edge),.05em -.05em 0 var(--ov-edge),-.05em 0 0 var(--ov-edge),.05em 0 0 var(--ov-edge),-.05em .05em 0 var(--ov-edge),0 .05em 0 var(--ov-edge),.05em .05em 0 var(--ov-edge),0 0 .12em var(--ov-edge)}
.dv.overlay.ov-edge-shadow .feed p{text-shadow:0 .05em .12em rgba(0,0,0,.95),0 0 .3em rgba(0,0,0,.7)}
.dv.overlay.ov-edge-none .feed p{text-shadow:none}
.dv.overlay.ov-font-sans .panes{font-family:ui-sans-serif,-apple-system,"Segoe UI",system-ui,Roboto,sans-serif;font-weight:600}
.dv.overlay.ov-font-hyper .panes{font-family:"Relay Hyperlegible",Verdana,system-ui,sans-serif;font-weight:700}
.dv.overlay.overlay-matte .panes{background:#000}
.dv.overlay.overlay-matte{--ov-edge:#fff}
.dv.overlay.overlay-matte .feed p{color:#fff}
.dv.overlay.overlay-matte.ov-edge-plate .feed p{text-shadow:none;background:rgba(255,255,255,var(--ov-plate))}
.dv.overlay.overlay-matte .ov-label{color:#fff}

/* ---------- ProPresenter slide view (/stage) ---------- */
.dvsv{position:absolute;inset:0;overflow:auto;padding:52px 16px 16px;background:var(--bg);color:var(--fg);font:14px/1.4 var(--font)}
.dvsv #sv{max-width:1400px;margin:0 auto}
.dvsv .sv{display:grid;grid-template-columns:minmax(0,2fr) minmax(0,1fr);gap:14px;align-items:start}
@container dvc (max-width:720px){.dvsv .sv{grid-template-columns:1fr}}
.dvsv .sv-head{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:8px 12px;align-items:baseline;min-height:22px}
.dvsv .sv-title{font-weight:600;font-size:16px}
.dvsv .sv-part{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11.5px;font-weight:600;border:1px solid var(--sv-part,var(--muted));color:var(--sv-part,var(--muted))}
.dvsv .sv-count{color:var(--muted);font-size:12.5px}
.dvsv .sv-label{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin:0 0 6px}
.dvsv .sv-frame{position:relative;aspect-ratio:16/9;background:color-mix(in srgb,var(--bg) 35%,#000);border:1px solid var(--border);border-radius:calc(var(--r) - 3px);overflow:hidden;display:flex;align-items:center;justify-content:center;container:svf/inline-size}
.dvsv .sv-pic{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;text-align:center;padding:6%;white-space:pre-line;line-height:1.3;font-weight:600;font-size:4.4cqw;color:#fff;background:linear-gradient(160deg,color-mix(in srgb,var(--primary) 38%,#000),#0a0a0c 80%)}
.dvsv .sv-next .sv-pic{font-size:4.2cqw}
.dvsv .sv-text{position:relative;padding:12px 16px;text-align:center;white-space:pre-line;color:#f2f2f2;line-height:1.35;font-size:4cqw;max-height:100%;overflow:hidden}
.dvsv .sv-next .sv-text{font-size:4.6cqw;color:#cfcfcf}
.dvsv .sv-timer{position:absolute;top:8px;right:8px;padding:3px 10px;border-radius:6px;background:rgba(0,0,0,.72);font:600 clamp(16px,3.6cqw,30px)/1.2 ui-monospace,SFMono-Regular,Menlo,monospace;color:#fff;font-variant-numeric:tabular-nums;z-index:2}
.dvsv .sv-timer small{font:500 11px/1 system-ui,sans-serif;opacity:.75;margin-right:8px;letter-spacing:.04em}
.dvsv .sv-timer.amber{color:var(--warn)}.dvsv .sv-timer.red{color:var(--err)}.dvsv .sv-timer.idle{opacity:.7}
.dvsv .sv-media{position:absolute;left:0;right:0;bottom:0;padding:14px 10px 6px;display:flex;gap:10px;align-items:center;background:linear-gradient(transparent,rgba(0,0,0,.78));color:#fff;font:500 12px/1 ui-monospace,SFMono-Regular,Menlo,monospace;z-index:2}
.dvsv .sv-media .bar{flex:1;height:5px;border-radius:3px;background:rgba(255,255,255,.28);overflow:hidden}
.dvsv .sv-media .bar>i{display:block;height:100%;width:0;background:var(--primary)}
.dvsv .sv-media.paused .bar>i{background:var(--muted)}
.dvsv .sv-note{grid-column:1/-1;color:var(--muted);font-size:12.5px;min-height:1.4em}
.dvsv .sv-media[hidden],.dvsv .sv-timer[hidden],.dvsv .sv-part[hidden],.dvsv .sv-next[hidden]{display:none}
.dvsv .nf{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font:15px ui-monospace,Menlo,monospace;color:var(--muted)}

/* ---------- signage player (/signage/<slug>): a TV, always black ---------- */
.dvsg{position:absolute;inset:0;background:#000;overflow:hidden;cursor:none;color:#fff;font-family:system-ui,sans-serif}
.dvsg .sg-layer{position:absolute;inset:0;opacity:0;background:#000;overflow:hidden;transition:opacity .8s ease}
.dvsg .sgs{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:center;padding:0 9cqw;gap:2.2cqh}
.dvsg .sgs-k{font-size:3.2cqh;letter-spacing:.18em;text-transform:uppercase;opacity:.8;font-weight:600}
.dvsg .sgs-t{font-size:12cqh;font-weight:800;line-height:1.02;max-width:20ch}
.dvsg .sgs-s{font-size:4.6cqh;font-weight:500;opacity:.92;line-height:1.3;max-width:34ch}
.dvsg .sgs-r{position:absolute;right:6cqw;bottom:6cqh;font-size:3.4cqh;opacity:.85;font-weight:600}
.dvsg .s0{background:linear-gradient(135deg,color-mix(in srgb,var(--primary) 60%,#000),#050a07)}
.dvsg .s1{background:linear-gradient(135deg,color-mix(in srgb,var(--warn) 55%,#000),#120c02)}
.dvsg .s2{background:linear-gradient(120deg,#0b2a4a,#3a1c5a,#0b2a4a);background-size:300% 300%;animation:dvsgmv 9s ease-in-out infinite}
.dvsg .s3{background:linear-gradient(135deg,color-mix(in srgb,var(--err) 50%,#000),#12060a)}
.dvsg .s4{background:linear-gradient(135deg,#102a2a,#04090a)}
@keyframes dvsgmv{50%{background-position:100% 50%}}
.dvsg .sg-over{position:absolute;inset:0;z-index:10;background:#000;overflow:hidden}
.dvsg .sg-bar{position:absolute;left:0;right:0;bottom:0;z-index:20;padding:2.2cqh 3cqw;background:var(--err);color:var(--bg);font:700 6cqh/1.15 system-ui,sans-serif;text-align:center;box-shadow:0 -.6cqh 3cqh rgba(0,0,0,.6)}
.dvsg .dvrd{position:absolute;inset:0;padding:7cqh 7cqw;background:#0a0a0c;display:flex;flex-direction:column;gap:2cqh}
.dvsg .dvrd h4{margin:0;font-size:5cqh;font-weight:700}
.dvsg .dvrd div{display:flex;gap:3cqw;font-size:4.2cqh;padding:1.4cqh 0;border-top:1px solid #ffffff22}
.dvsg .dvrd div b{width:9ch;color:#ffffffb0;font-weight:600;font-variant-numeric:tabular-nums}
.dvsg .dvrd div.now{background:#ffffff14;margin:0 -2cqw;padding:1.4cqh 2cqw;border-radius:1cqh}
.dvsg .dvblank{position:absolute;inset:0;background:#000}

/* ---------- touch panels on /screen (lighting, OBS) ---------- */
.dvlt,.dvob{position:absolute;inset:0;background:var(--bg);color:var(--fg);font:16px/1.2 system-ui,sans-serif;user-select:none;touch-action:manipulation}
.dvlt .ltcue,.dvob .obsscene{font:inherit}
.dvlt #ltScreen{box-sizing:border-box;padding:52px 12px 12px;height:100%}
.dvlt .ltgrid{box-sizing:border-box;display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));grid-auto-rows:110px;grid-auto-flow:dense;align-content:start;overflow:auto;height:100%}
.dvlt .ltfree{position:relative;height:100%}
.dvlt .ltcue{box-sizing:border-box;border:0;padding:8px;font-weight:600;font-size:clamp(16px,2.4cqw,28px);cursor:pointer;display:flex;align-items:center;justify-content:center;text-align:center;overflow:hidden;word-break:break-word;box-shadow:inset 0 -4px 0 rgba(0,0,0,.25);transition:filter .12s,transform .12s}
.dvlt .ltcue:active,.dvlt .ltcue.sent{filter:brightness(1.3);transform:scale(.98)}
.dvlt .ltcue.lit{outline:4px solid var(--fg);outline-offset:-4px}
.dvlt .ltcue[data-shape=rect]{border-radius:0}.dvlt .ltcue[data-shape=rounded]{border-radius:16px}.dvlt .ltcue[data-shape=pill]{border-radius:999px}.dvlt .ltcue[data-shape=circle]{border-radius:50%}
.dvlt .ltfree .ltcue{position:absolute}
.dvlt #ltToast,.dvob #obsToast{position:absolute;left:50%;bottom:20px;transform:translateX(-50%);background:var(--err);color:var(--bg);padding:10px 18px;border-radius:8px;font-size:18px;font-weight:600;z-index:5;max-width:90%}
.dvlt #ltToast[hidden],.dvob #obsToast[hidden]{display:none}
.dvob{display:flex;flex-direction:column}
.dvob #obsHead{padding:12px 16px 12px 128px;font-size:20px;font-weight:600;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.dvob .badge{font-size:14px;padding:3px 8px;border-radius:6px;background:var(--err);color:var(--bg);border:0;font-weight:600}
.dvob .badge.rec{background:var(--primary);color:var(--primary-fg)}
.dvob .badge.off{background:var(--muted);color:var(--bg)}
.dvob .badge[hidden]{display:none}
.dvob #obsScenes{flex:1;box-sizing:border-box;padding:0 12px 12px;display:grid;gap:12px;overflow:auto;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));grid-auto-rows:110px;align-content:start}
.dvob .obsscene{border:0;border-radius:14px;background:color-mix(in srgb,var(--primary) 26%,var(--card));color:var(--fg);font-weight:600;font-size:clamp(16px,2.4cqw,26px);padding:8px;cursor:pointer;word-break:break-word;box-shadow:inset 0 -4px 0 rgba(0,0,0,.25);transition:filter .12s,transform .12s}
.dvob .obsscene.program{background:var(--err);color:var(--bg);outline:4px solid var(--fg);outline-offset:-4px}
.dvob .obsscene:active,.dvob .obsscene.sent{filter:brightness(1.3);transform:scale(.98)}
.dvob.off .obsscene{opacity:.5}

/* ---------- phone frame ---------- */
.dvdesk{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:color-mix(in srgb,var(--fg) 6%,var(--bg))}
.dvphone{padding:12px;border-radius:44px;background:color-mix(in srgb,var(--fg) 16%,var(--bg));border:1px solid var(--border);box-shadow:0 24px 70px #0008}
.dvphone .dvin{width:375px;height:740px;border-radius:32px;overflow:hidden;position:relative}
.dvdesk .dvnote{position:absolute;left:24px;top:64px;max-width:260px;font:12px/1.5 var(--font);color:var(--muted)}
.dvdesk .dvnote b{color:var(--fg)}
`;
  injectCSS('dv-display', CSS);

  /* ------------------------------------------------------------------ demo machinery */
  let TM = [];
  const clearTM = () => { TM.forEach(clearInterval); TM = []; };

  function chip(groups, st, shut, left) {
    return `<div class="dvchip${shut ? ' shut' : ''}${left ? ' left' : ''}"><button class="dvh" type="button" data-dvt>Demo controls</button><button class="dvs" type="button" data-dvs title="Move to the other side">&#8644;</button><div class="dvb">` +
      groups.concat(LOOK).map(g => g.n ? `<p class="dvn">${g.n}</p>` : `<div class="dvg"><span>${g.l}</span><div class="dvo"${g.k ? ` data-k="${g.k}"` : ''}>` +
        (g.o ? g.o.map(([v, t]) => `<button type="button" data-v="${v}" aria-pressed="${String(st[g.k]) === v}">${t}</button>`).join('')
          : g.a.map(([v, t]) => `<button type="button" data-act="${v}">${t}</button>`).join('')) + '</div></div>').join('') + '</div></div>';
  }

  /* One screen = one def: {id, st, groups(st), render(st), post(root,st,draw), click(ev,st,draw,root), onset(k,st)} */
  function mount(def) {
    const host = document.getElementById('dvroot');
    if (!host) return;
    clearTM();
    const html = document.documentElement;
    const st = Object.assign({ theme: html.getAttribute('data-theme') || 'dark', look: html.getAttribute('data-preset') || 'relay' }, def.st);
    let shut = false, left = false;
    function draw() {
      clearTM();
      host.innerHTML = `<div class="dvhost">${def.render(st)}${chip(def.groups(st), st, shut, left)}</div>`;
      def.post && def.post(host.firstElementChild, st, draw);
    }
    host.onclick = ev => {
      if (ev.target.closest('[data-dvs]')) { left = !left; host.querySelector('.dvchip').classList.toggle('left', left); return; }
      if (ev.target.closest('[data-dvt]')) { shut = !shut; host.querySelector('.dvchip').classList.toggle('shut', shut); return; }
      const b = ev.target.closest('.dvo button[data-v]');
      if (b) {
        const k = b.parentNode.dataset.k;
        st[k] = b.dataset.v;
        if (k === 'theme') { html.setAttribute('data-theme', st.theme); }
        if (k === 'look') { html.setAttribute('data-preset', st.look); }
        if (k === 'theme' || k === 'look') { b.parentNode.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', x === b)); return; }
        def.onset && def.onset(k, st);
        draw();
        return;
      }
      def.click && def.click(ev, st, draw, host.firstElementChild);
    };
    host.onchange = ev => { def.change && def.change(ev, st, draw); };
    draw();
  }

  /* ------------------------------------------------------------------ the feed engine (viewer.js Pane, replayed on sample text) */
  function startFeeds(root, o) {
    o = o || {};
    const fpx = () => parseInt(getComputedStyle(root.querySelector('.dv') || root).getPropertyValue('--font-px'), 10) || 40;
    const feeds = [];
    root.querySelectorAll('.feed[data-stream]').forEach((el, n) => {
      const iso = el.dataset.iso;
      if (!iso || !TXT[iso]) return;
      const f = { el, iso, log: el.querySelector('.log'), idx: 0, w: 0, pause: 0, delay: n ? 4 : 0, open: null, pinned: true,
        cap: parseInt(el.dataset.lines, 10) || 0, finals: el.dataset.finals === '1', seq: 0 };
      const pane = el.closest('.pane');
      f.jump = pane && pane.querySelector('.jump');
      el.addEventListener('scroll', () => {
        f.pinned = el.scrollHeight - el.scrollTop - el.clientHeight < Math.max(120, 3 * fpx());
        if (f.jump) f.jump.hidden = f.pinned;
      }, { passive: true });
      if (f.jump) f.jump.onclick = () => { f.pinned = true; el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' }); f.jump.hidden = true; };
      feeds.push(f);
    });
    const dropEmpty = f => { const e = f.el.querySelector('.empty'); if (e) e.remove(); };
    const cap = f => { if (!f.cap) return; const b = Math.max(0, f.cap - (f.open ? 1 : 0)); while (f.log.children.length > b) f.log.firstElementChild.remove(); };
    const stick = f => { if (f.pinned) f.el.scrollTop = f.el.scrollHeight; };
    const ensureOpen = f => {
      if (!f.open) { f.open = document.createElement('p'); f.open.className = 'open'; f.open.setAttribute('aria-hidden', 'true'); f.el.appendChild(f.open); cap(f); }
      return f.open;
    };
    const settle = (f, text) => {
      if (o.popon && f.cap && f.log.children.length >= f.cap) f.log.innerHTML = '';
      const p = document.createElement('p'); p.className = 'settled'; p.textContent = text; f.log.appendChild(p);
      if (f.open) { f.open.remove(); f.open = null; }
      cap(f);
    };
    // seed: lines already said when this reader opened the page
    feeds.forEach(f => {
      if (o.live === false) return;
      const n = o.seed == null ? 4 : o.seed;
      for (let i = 0; i < n; i++) settle(f, TXT[f.iso][f.idx++ % TXT[f.iso].length]);
      if (n) dropEmpty(f);
      stick(f);
    });
    let lastCaption = Date.now();
    const ovEl = root.querySelector('.dv.overlay');
    const tick = () => {
      if (!document.body.contains(root)) return;
      if (o.live === false || o.frozen) return;
      if (o.silent) {
        if (ovEl && o.hold && Date.now() - lastCaption > o.hold) ovEl.classList.add('ov-hidden');
        return;
      }
      feeds.forEach(f => {
        if (f.delay > 0) { f.delay--; return; }
        if (f.pause > 0) { f.pause--; return; }
        const lines = TXT[f.iso];
        const parts = lines[f.idx % lines.length].split(/(?= )/);
        f.w++;
        dropEmpty(f);
        if (f.w >= parts.length) { settle(f, parts.join('')); f.idx++; f.w = 0; f.pause = o.gap == null ? 5 : o.gap; }
        else if (!f.finals) ensureOpen(f).textContent = parts.slice(0, f.w).join('');
        else return;
        lastCaption = Date.now();
        if (ovEl) ovEl.classList.remove('ov-hidden');
        stick(f);
      });
    };
    TM.push(setInterval(tick, 260));
    return feeds;
  }

  /* ------------------------------------------------------------------ shared builders */
  const SRC = { stream: 'transcription', lang: 'ENGLISH', iso: 'en' };
  function paneHTML(stream, p) {
    // p: {lang, iso, label (pane-label), aria, cap, dual, kiosk, empty, finals}
    return `<div class="pane">${p.dual ? `<div class="pane-label">${p.label}</div>` : ''}` +
      `<div class="feed" data-stream="${stream}" data-lang="${p.lang}" data-iso="${p.iso}" lang="${p.iso}"${p.cap ? ` data-lines="${p.cap}"` : ''}${p.finals ? ' data-finals="1"' : ''}>` +
      `<div class="log" role="log" aria-live="polite" aria-atomic="false" aria-label="${H(p.aria)}"></div>` +
      (p.kiosk ? '' : `<p class="empty">${p.empty || 'Waiting for audio&hellip;'}</p>`) + '</div>' +
      `<button class="jump" type="button" hidden>Jump to live &#8595;</button></div>`;
  }
  function langsOf(st) { return st.langs === 'none' ? [] : st.langs === 'one' ? ['es'] : ['es', 'pt']; }
  function langOf(st) { const l = langsOf(st); return l.includes(st.lang) ? st.lang : l[0] || ''; }

  function viewerBar(st, mode) {
    const dot = st.state === 'live' ? 'ok' : st.state === 'waiting' ? 'warn' : 'err';
    const ls = langsOf(st), cur = langOf(st);
    const picker = (mode === 'translation' || mode === 'both')
      ? `<select id="langPicker" aria-label="Translation language"${ls.length < 2 ? ' disabled' : ''}>` +
        (ls.length ? ls.map(l => `<option value="${LANGS[l].target}"${l === cur ? ' selected' : ''}>${LANGS[l].label}</option>`).join('') : '<option>No languages live</option>') + '</select>' : '';
    return `<div class="bar"><span class="dot ${dot}" id="statusDot"></span>` +
      `<a href="#s-viewer-transcription" class="${mode === 'transcription' ? 'on' : ''}">English</a>` +
      `<a href="#s-viewer-translation" class="${mode === 'translation' ? 'on' : ''}">Translation</a>` +
      `<a href="#s-viewer-both" class="${mode === 'both' ? 'on' : ''}">Both</a><span class="spacer"></span>${picker}` +
      `<button id="themeToggle" aria-label="Toggle light or dark theme" title="Light / dark">&#9681;</button>` +
      `<button id="wakeToggle" class="${st.wake === 'on' ? 'on' : ''}" aria-pressed="${st.wake === 'on'}" aria-label="Keep screen awake" title="Keep screen awake">&#9788;</button>` +
      `<button id="presentBtn" aria-label="Presentation mode" title="Presentation mode">&#9974;</button>` +
      `<button data-font="down" aria-label="Smaller text">A&minus;</button><button data-font="up" aria-label="Larger text">A+</button></div>`;
  }

  function viewerPanes(st, mode) {
    const cur = langOf(st), none = mode !== 'transcription' && !cur;
    const dual = mode === 'both';
    const src = paneHTML('transcription', { lang: SRC.lang, iso: 'en', label: 'English', aria: 'English transcript', dual });
    const tr = none
      ? `<div class="pane">${dual ? '<div class="pane-label">Translation</div>' : ''}<div class="feed" data-stream="translation" data-lang=""><div class="log" role="log" aria-live="polite" aria-atomic="false" aria-label="Translation"></div><p class="empty">No target language is live yet. Ask the operator to enable one.</p></div><button class="jump" type="button" hidden>Jump to live &#8595;</button></div>`
      : paneHTML('translation', { lang: cur && LANGS[cur].target, iso: cur, label: 'Translation', aria: cur && LANGS[cur].label + ' translation', dual });
    return `<div class="panes${dual ? ' dual' : ''}">${mode !== 'translation' ? src : ''}${mode !== 'transcription' ? tr : ''}</div>`;
  }

  function viewerRender(st, mode, wrapCls) {
    const present = st.present === 'on';
    return `<div class="dv${present ? ' present' : ''}" style="--font-px:${st.font}px" data-mode="${mode}">` +
      viewerBar(st, mode) + viewerPanes(st, mode) +
      `<button id="presentExit" type="button"${present ? '' : ' hidden'} aria-label="Exit presentation mode">&#10005;</button>` +
      `<div class="toast${st.state === 'down' ? ' show' : ''}" id="dvToast">Reconnecting&hellip;</div></div>`;
  }

  /* shared clicks of the viewer bar (viewer.js behaviours) */
  function viewerClick(ev, st, draw, root) {
    const dv = root.querySelector('.dv');
    const t = ev.target.closest('button');
    if (!t) return;
    if (t.id === 'themeToggle') {
      st.theme = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
      document.documentElement.setAttribute('data-theme', st.theme);
      draw(); return;
    }
    if (t.id === 'wakeToggle') { st.wake = st.wake === 'on' ? 'off' : 'on'; t.classList.toggle('on', st.wake === 'on'); t.setAttribute('aria-pressed', st.wake === 'on'); toast(st.wake === 'on' ? 'Screen will stay awake' : 'Screen may sleep'); return; }
    if (t.id === 'presentBtn') { st.present = 'on'; draw(); return; }
    if (t.id === 'presentExit') { st.present = 'off'; draw(); return; }
    if (t.dataset.font) {
      st.font = Math.max(18, Math.min(140, +st.font + (t.dataset.font === 'up' ? 4 : -4)));
      dv.style.setProperty('--font-px', st.font + 'px');
      dv.querySelectorAll('.feed').forEach(f => { f.scrollTop = f.scrollHeight; });
      return;
    }
    if (t.dataset.act === 'back') {
      dv.querySelectorAll('.feed').forEach(f => { f.scrollTop = Math.max(0, f.scrollTop - f.clientHeight * 0.8); });
    }
  }
  const viewerChange = (ev, st, draw) => { if (ev.target.id === 'langPicker') { st.lang = Object.keys(LANGS).find(k => LANGS[k].target === ev.target.value) || st.lang; draw(); } };
  const viewerPost = (root, st) => startFeeds(root, { live: st.state !== 'waiting', frozen: st.state === 'down', seed: 5 });
  const STATE_G = { k: 'state', l: 'Service', o: [['live', 'Live'], ['waiting', 'Waiting for audio'], ['down', 'Reconnecting']] };
  const LANG_G = { k: 'langs', l: 'Languages live', o: [['two', 'Spanish + Portuguese'], ['one', 'One'], ['none', 'None']] };
  const BACK_G = { l: 'Reader', a: [['back', 'Scroll back (shows Jump to live)']] };

  function reg1(id, t, desc, path, def) {
    reg({ kind: 'full', id, t, cat: def.cat || 'Viewer', desc, path });
    FULL[id] = () => '<div id="dvroot"></div>';
    AFTER[id] = () => mount(def);
  }

  /* ================================================================== VIEWER: three modes */
  function viewerDef(mode, extra) {
    return Object.assign({
      st: { state: 'live', langs: 'two', lang: 'es', font: 40, wake: 'off', present: 'off' },
      groups: st => [STATE_G].concat(mode === 'transcription' ? [] : [LANG_G]).concat([{ k: 'font', l: 'Text size (A− / A+ in the bar)', o: [['24', '24'], ['40', '40 (default)'], ['64', '64'], ['96', '96']] }, BACK_G]),
      render: st => viewerRender(st, mode),
      post: viewerPost, click: viewerClick, change: viewerChange
    }, extra || {});
  }
  reg1('s-viewer-translation', 'Viewer: translation', 'The congregation page in one language: top bar with the language picker, then the live caption feed in large type.', '/translation', viewerDef('translation'));
  reg1('s-viewer-both', 'Viewer: both', 'Two panes, the original speech beside its translation (stacked on a phone). Pane labels top right.', '/both', viewerDef('both'));
  reg1('s-viewer-transcription', 'Viewer: transcription', 'The original-language transcript only, for people who want to read along in English.', '/transcription', viewerDef('transcription'));

  /* ------------------------------------------------------------------ phone at 375 */
  (function () {
    const mode0 = 'both';
    const def = {
      st: { state: 'live', langs: 'two', lang: 'es', font: 28, wake: 'on', present: 'off', mode: 'translation', w: '375' },
      groups: st => [{ k: 'mode', l: 'Page', o: [['transcription', '/transcription'], ['translation', '/translation'], ['both', '/both']] },
        { k: 'w', l: 'Phone width', o: [['320', '320 (small)'], ['375', '375'], ['414', '414']] }, STATE_G, LANG_G,
        { k: 'font', l: 'Text size', o: [['20', '20'], ['28', '28'], ['40', '40 (default)']] }, BACK_G],
      render: st => `<div class="dvdesk"><div class="dvnote"><b>The same page at phone width.</b><br>No separate mobile template: the bar tightens (smaller padding and buttons, picker capped at 42% width) and /both stacks its panes. The wake lock button shows only where the browser supports it.</div>` +
        `<div class="dvphone"><div class="dvhost dvin" style="width:${st.w}px">${viewerRender(st, st.mode)}</div></div></div>`,
      post: viewerPost, click: viewerClick, change: viewerChange
    };
    reg1('s-viewer-phone', 'Viewer: phone (375px)', 'The viewer on a phone: the bar tightens, /both stacks vertically, picker never leaves the screen; width 320, 375 or 414.', '/translation (any mode)', def);
  })();

  /* ================================================================== /present */
  reg1('s-present', 'Present (projector)', 'Presentation mode: no chrome, bottom-anchored, 15% larger type, an exit button. Also reachable as ?present=1 on any viewer route.', '/present',
    {
      st: { state: 'live', langs: 'two', lang: 'es', font: 40, wake: 'off', present: 'on', pm: 'translation' },
      groups: st => [{ k: 'pm', l: '?mode=', o: [['translation', 'translation'], ['transcription', 'transcription'], ['both', 'both']] }, STATE_G, LANG_G,
        { k: 'font', l: 'Text size', o: [['40', '40'], ['64', '64'], ['96', '96']] }],
      render: st => viewerRender(st, st.pm),
      post: viewerPost,
      click: (ev, st, draw, root) => {
        const t = ev.target.closest('button');
        if (t && t.id === 'presentExit') { st.present = st.present === 'on' ? 'off' : 'on'; draw(); return; }
        viewerClick(ev, st, draw, root);
      },
      change: viewerChange
    });

  /* ================================================================== /screen (kiosk room display) */
  function screenRender(st) {
    const stream = st.stream, fixed = st.fontm === 'fixed';
    const lines = +st.lines, dual = stream === 'both';
    const cur = 'es';
    const fz = fixed ? 54 : 40;
    const src = paneHTML('transcription', { lang: SRC.lang, iso: 'en', label: 'English', aria: 'English transcript', dual, cap: lines, kiosk: true });
    const tr = paneHTML('translation', { lang: LANGS[cur].target, iso: cur, label: 'Translation', aria: 'Translation', dual, cap: lines, kiosk: true });
    const al = st.alert !== 'off', msg = st.msg;
    return `<div class="dv screen align-${st.align}${fixed ? ' screen-fixedfont' : ''}" style="--font-px:${fz}px" data-alert="Nursery">` +
      `<div class="panes${dual ? ' dual' : ''}">${stream !== 'translation' ? src : ''}${stream !== 'transcription' ? tr : ''}</div>` +
      (st.lyrics === 'on' ? `<div class="lyrics" id="lyrics" aria-live="polite"><div class="lyrics-text" id="lyricsText">Come, thou Fount of every blessing,\nTune my heart to sing thy grace;\nStreams of mercy, never ceasing,\nCall for songs of loudest praise.</div></div>` : '<div class="lyrics" id="lyrics" hidden></div>') +
      `<div class="alert-flash" id="alertFlash" role="alert" tabindex="-1" data-level="${st.alert}"${al ? '' : ' hidden'}>` +
      `<div class="alert-flash-from" id="alertFrom">Room 104</div><div class="alert-flash-to" id="alertTo">needs Nursery &middot; tap anywhere</div>` +
      `<div class="alert-flash-replies" id="alertReplies"${st.replies === 'on' ? '' : ' hidden'}><button type="button">On my way</button><button type="button">Two minutes</button><button type="button">Send someone else</button></div></div>` +
      `<div class="stage-banner" id="stageBanner" role="status" data-level="info"${msg === 'banner' ? '' : ' hidden'}>Prayer team to the front, please</div>` +
      `<div class="stage-message" id="stageMessage" role="status" data-level="${msg === 'attention' ? 'attention' : 'info'}"${msg === 'card' || msg === 'attention' ? '' : ' hidden'}><div class="stage-message-from" id="stageFrom">From Pastor Mike</div><div class="stage-message-text" id="stageText">${msg === 'attention' ? 'Wrap up in 2 minutes' : 'Welcome, Grace Community'}</div></div>` +
      `<span class="screen-fault" id="screenFault" title="Stream fault"${st.fault === 'on' ? '' : ' hidden'}></span></div>`;
  }
  reg1('s-screen', 'Screen (room display)', 'The kiosk captions display for a lobby or sanctuary TV: no chrome, fixed language, capped lines, burn-in safe, quiet fault dot, optional alert flash, stage messages and lyrics.', '/screen?lang=SPANISH&lines=3&align=bottom&alert=Nursery&lyrics=1', {
    cat: 'Viewer',
    st: { stream: 'translation', lines: '3', align: 'bottom', fontm: 'auto', alert: 'off', replies: 'on', msg: 'none', lyrics: 'off', fault: 'off' },
    groups: st => [{ k: 'stream', l: '?stream=', o: [['translation', 'translation'], ['transcription', 'transcription'], ['both', 'both']] },
      { k: 'lines', l: '?lines= (captions kept on screen)', o: [['1', '1'], ['2', '2'], ['3', '3'], ['5', '5']] },
      { k: 'align', l: '?align=', o: [['bottom', 'bottom'], ['center', 'center']] },
      { k: 'fontm', l: 'Size', o: [['auto', 'Scales with the screen'], ['fixed', '?font=54 (pinned)']] },
      { k: 'alert', l: '?alert=Nursery: alert flash', o: [['off', 'None'], ['urgent', 'Urgent'], ['attention', 'Attention'], ['info', 'Info']] },
      { k: 'replies', l: 'Alert reply buttons', o: [['on', 'Shown'], ['off', 'None']] },
      { k: 'msg', l: 'Stage message', o: [['none', 'None'], ['card', 'Card'], ['attention', 'Card, attention'], ['banner', 'Banner']] },
      { k: 'lyrics', l: '?lyrics=1: a song is up', o: [['off', 'No'], ['on', 'Yes']] },
      { k: 'fault', l: 'Stream fault (corner dot)', o: [['off', 'OK'], ['on', 'Down']] },
      { n: 'Tap the flashing alert to dismiss it, as on the real screen.' }],
    render: screenRender,
    post: (root, st) => startFeeds(root, { seed: +st.lines + 1 }),
    click: (ev, st, draw) => { if (ev.target.closest('#alertFlash') && !ev.target.closest('button')) { st.alert = 'off'; draw(); toast('Alert acknowledged'); } else if (ev.target.closest('#alertReplies button')) { st.alert = 'off'; draw(); toast('Reply sent: ' + ev.target.textContent); } }
  });

  /* ================================================================== /overlay */
  const PRE = {
    lower: { n: 'Lower third', pos: 'bottom', align: 'center', lines: 2, font: 54, maxw: 80, edge: 'plate', plate: .6, fam: 'sans' },
    top: { n: 'Top strip', pos: 'top', align: 'center', lines: 1, font: 46, maxw: 100, edge: 'plate', plate: .75, fam: 'sans' },
    slate: { n: 'Accessibility slate', pos: 'middle', align: 'left', lines: 6, font: 84, maxw: 100, edge: 'none', plate: 0, fam: 'hyper', bg: '#000', hold: 0 },
    dual: { n: 'Two languages', stream: 'both', pos: 'bottom', align: 'center', lines: 1, font: 44, maxw: 85, edge: 'plate', plate: .6, fam: 'sans' }
  };
  function overlayRender(st) {
    const p = PRE[st.preset], dual = p.stream === 'both', popon = st.mode === 'popon';
    const bg = p.bg || 'transparent';
    const cls = `dv overlay ov-pos-${st.pos} ov-align-${st.align} ov-font-${p.fam} ov-edge-${st.edge} ov-${st.mode}${st.matte === 'on' ? ' overlay-matte' : ''}`;
    const op = (stream, lang, iso, label) => {
      const h = paneHTML(stream, { lang, iso, label, aria: label || 'Captions', cap: p.lines, kiosk: true, finals: popon });
      return dual ? h.replace('<div class="feed"', `<div class="ov-label" aria-hidden="true">${label}</div><div class="feed"`) : h;
    };
    const feeds = dual ? op('translation', 'SPANISH', 'es', 'Espa\u00f1ol') + op('transcription', 'ENGLISH', 'en', 'English') : op('translation', 'SPANISH', 'es', '');
    const path = st.preset === 'dual' ? '/overlay/stream-dual' : '/overlay?lang=SPANISH&preset=' + st.preset;
    return `<div class="dvov"><div class="dvinfo"><b>${path}</b> &middot; browser source 1920 &times; 1080 &middot; ${st.backdrop === 'video' ? 'composited over programme video' : 'what the browser source itself draws: transparent'}</div>` +
      `<div class="dvf ${st.backdrop === 'video' ? 'video' : 'alpha'}">${st.backdrop === 'video' ? '<i class="dvcross"></i><i class="dvfig"></i>' : ''}` +
      `<div class="${cls}" style="--ovpx:${p.font};--ovmax:${p.maxw}%;--ov-plate:${p.plate};--ov-bg:${bg};--ov-platec:0 0 0">` +
      `<div class="panes ov-canvas">${feeds}</div></div></div></div>`;
  }
  reg1('s-overlay', 'Overlay (broadcast captions)', 'Captions keyed over live video for OBS, vMix, ProPresenter or an ATEM: transparent canvas, plate or outline text, hold-to-hide, optional matte.', '/overlay  and  /overlay/{id}', {
    cat: 'Viewer',
    st: { preset: 'lower', pos: 'bottom', align: 'center', edge: 'plate', mode: 'rollup', matte: 'off', backdrop: 'video', speech: 'speaking' },
    groups: st => [{ k: 'preset', l: 'Preset (?preset=)', o: Object.keys(PRE).map(k => [k, PRE[k].n]) },
      { k: 'pos', l: 'Position (?pos=)', o: [['bottom', 'bottom'], ['middle', 'middle'], ['top', 'top']] },
      { k: 'align', l: 'Align (?align=)', o: [['center', 'center'], ['left', 'left'], ['right', 'right']] },
      { k: 'edge', l: 'Text edge (?edge=)', o: [['plate', 'plate'], ['outline', 'outline'], ['shadow', 'shadow'], ['none', 'none']] },
      { k: 'mode', l: 'Mode (?mode=)', o: [['rollup', 'roll-up'], ['popon', 'pop-on (finished lines only)']] },
      { k: 'matte', l: 'ATEM key (?matte=1)', o: [['off', 'Fill'], ['on', 'Matte']] },
      { k: 'backdrop', l: 'Backdrop', o: [['video', 'Over programme video'], ['alpha', 'Transparent (checkerboard)']] },
      { k: 'speech', l: 'Speaker', o: [['speaking', 'Speaking'], ['silent', 'Silent (fades after ?hold=4000)']] }],
    onset: (k, st) => { if (k === 'preset') { const p = PRE[st.preset]; st.pos = p.pos; st.align = p.align; st.edge = p.edge; } },
    render: overlayRender,
    post: (root, st) => startFeeds(root, { seed: PRE[st.preset].lines, gap: 6, popon: st.mode === 'popon', silent: st.speech === 'silent', hold: st.preset === 'slate' ? 0 : 4000 })
  });

  /* ================================================================== ProPresenter slide view  /stage */
  const SV = {
    cur: 'Come, thou Fount of every blessing,\nTune my heart to sing thy grace;\nStreams of mercy, never ceasing,\nCall for songs of loudest praise.',
    nxt: 'Teach me some melodious sonnet,\nSung by flaming tongues above;\nPraise the mount, I’m fixed upon it,\nMount of thy redeeming love.'
  };
  function clock(sec) {
    const neg = sec < 0; let s = Math.floor(Math.abs(sec));
    const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60, two = n => (n < 10 ? '0' : '') + n;
    return (neg ? '-' : '') + (hh ? hh + ':' + two(mm) : mm) + ':' + two(ss);
  }
  function svRender(st) {
    if (st.view === 'off') return `<div class="dvsv"><div class="nf">{"detail":"Not found"}</div></div>`;
    const s = st.snap, live = s === 'live' || s === 'text';
    const note = { unset: 'ProPresenter is not set up.', wait: 'Waiting for ProPresenter.', none: 'Nothing is up.', err: 'Cannot reach Relay.' }[s] || '';
    const lvl = st.timer === 'amber' ? ' amber' : (st.timer === 'red' || st.timer === 'over') ? ' red' : '';
    const frame = (text, extra) => `<div class="sv-frame${s === 'live' ? ' sv-img' : ''}">` +
      (s === 'live' ? `<div class="sv-pic">${H(text)}</div>` : `<div class="sv-text">${H(live ? text : '')}</div>`) + (extra || '') + '</div>';
    const cur = frame(SV.cur, `<div class="sv-timer${lvl}${st.timer === 'paused' ? ' idle' : ''}" id="dvTimer"${st.timer === 'none' || !live ? ' hidden' : ''}><small>Sermon</small><span id="dvTv"></span></div>` +
      `<div class="sv-media${st.media === 'paused' ? ' paused' : ''}"${st.media === 'none' || !live ? ' hidden' : ''} title="Baptism testimonies.mp4"><div class="bar"><i id="dvMf"></i></div><span id="dvMt"></span></div>`);
    return `<div class="dvsv"><div id="sv"><div class="sv">` +
      `<div class="sv-head"><span class="sv-title">${live ? 'Come, Thou Fount of Every Blessing' : ''}</span><span class="sv-part"${live ? '' : ' hidden'} style="--sv-part:var(--ok)">Verse 1</span><span class="sv-count">${live ? 'slide 3 of 7' : ''}</span></div>` +
      `<div class="sv-current"><div class="sv-label">Live</div>${cur}</div>` +
      `<div class="sv-next"${live ? '' : ' hidden'}><div class="sv-label">Next</div>${frame(SV.nxt)}</div>` +
      `<div class="sv-note">${note}</div></div></div></div>`;
  }
  reg1('s-stage-slides', 'Slide view (/stage)', 'ProPresenter\'s live slide and the next one as pictures, with the countdown and video progress over them. Public only when the operator switches it on.', '/stage  (same widget on the admin Slide view page)', {
    cat: 'Displays & stage',
    st: { view: 'on', snap: 'live', timer: 'run', media: 'play' },
    groups: st => [{ k: 'view', l: 'Public viewing', o: [['on', 'On'], ['off', 'Off (404)']] },
      { k: 'snap', l: 'Snapshot', o: [['live', 'Slide image'], ['text', 'Text only (no image)'], ['none', 'Nothing is up'], ['wait', 'Waiting for ProPresenter'], ['unset', 'Not set up'], ['err', 'Cannot reach Relay']] },
      { k: 'timer', l: 'Countdown on the slide', o: [['none', 'None'], ['run', 'Running'], ['amber', 'Amber'], ['red', 'Red'], ['over', 'Overrun'], ['paused', 'Paused']] },
      { k: 'media', l: 'Video on the slide', o: [['none', 'None'], ['play', 'Playing'], ['paused', 'Paused']] }],
    render: svRender,
    post: (root, st) => {
      const base = { run: 612, amber: 241, red: 38, over: -42, paused: 600, none: 0 }[st.timer], t0 = Date.now(), m0 = 77;
      const paint = () => {
        const tv = root.querySelector('#dvTv'), mt = root.querySelector('#dvMt'), mf = root.querySelector('#dvMf');
        const age = (Date.now() - t0) / 1000, running = st.timer !== 'paused';
        if (tv) tv.textContent = clock(base + (running ? -age : 0));
        if (mt) { const at = Math.min(m0 + (st.media === 'play' ? age : 0), 214); mt.textContent = clock(at) + ' / ' + clock(214); mf.style.width = (100 * at / 214).toFixed(1) + '%'; }
      };
      paint();
      TM.push(setInterval(() => { if (document.body.contains(root)) paint(); }, 250));
    }
  });

  /* ================================================================== signage player */
  const SLIDES = [
    { c: 's0', k: 'Welcome', t: 'Welcome to Grace Community', s: 'Sunday services at 9:00 and 11:00 · Coffee in the lobby from 8:30', r: 'grace.example' },
    { c: 's1', k: 'Kids', t: 'Nursery & kids check-in', s: 'Opens 8:45 · Room 104 · Please bring your pickup tag', r: '' },
    { c: 's2', k: 'Baptism Sunday', t: 'December 7, 11:00', s: 'Sign up at the welcome desk. All ages.', r: 'video · loops' },
    { c: 's3', k: 'Youth', t: 'Youth Night every Friday, 7 PM', s: 'Pizza, games and a short talk · Grades 6–12', r: '' },
    { c: 's4', k: 'Give', t: 'Give online or at the kiosk', s: 'Every gift supports local families this winter', r: 'grace.example/give' }
  ];
  const slideHTML = i => { const s = SLIDES[i % SLIDES.length]; return `<div class="sgs ${s.c}"><div class="sgs-k">${s.k}</div><div class="sgs-t">${s.t}</div><div class="sgs-s">${s.s}</div>${s.r ? `<div class="sgs-r">${s.r}</div>` : ''}</div>`; };
  function sgRender(st) {
    const view = st.show;
    let over = '';
    if (view === 'file') over = `<div class="sg-over"><div class="sgs s0" style="position:absolute"><div class="sgs-k">Pushed to this screen</div><div class="sgs-t">Please silence your phones</div><div class="sgs-s">Service begins in five minutes</div></div></div>`;
    if (view === 'live') over = `<div class="sg-over"><div class="dv screen align-bottom" style="--font-px:40px;--bg:#000;--fg:#fff;--dv-settled:#fff"><div class="panes">${paneHTML('translation', { lang: 'SPANISH', iso: 'es', label: '', aria: 'Spanish translation', cap: 3, kiosk: true })}</div></div></div>`;
    if (view === 'rundown') over = `<div class="sg-over"><div class="dvrd"><h4>Sunday service · rundown</h4><div><b>9:00</b>Welcome and announcements</div><div class="now"><b>9:06</b>Worship set: three songs</div><div><b>9:25</b>Scripture: John 15:1–8</div><div><b>9:30</b>Sermon: Abide</div><div><b>10:05</b>Communion</div><div><b>10:15</b>Closing song and blessing</div></div></div>`;
    return `<div class="dvsg"><div class="sg-layer" id="dvL0"></div><div class="sg-layer" id="dvL1"></div>${view === 'blank' ? '<div class="dvblank"></div>' : ''}${over}` +
      `<div class="sg-bar" role="alert"${st.banner === 'on' ? '' : ' style="display:none"'}>Parent paging: Room 104, please come to the nursery &nbsp;(+2 more)</div></div>`;
  }
  reg1('s-signage-player', 'Signage player', 'The lobby TV: full-screen playlist of announcement slides and videos with a crossfade, and takeovers on top (alert banner, a pushed file, live captions, the service rundown).', '/signage/{slug}  (signage port)', {
    cat: 'Displays & stage',
    st: { show: 'playlist', banner: 'off' },
    groups: st => [{ k: 'show', l: 'On screen', o: [['playlist', 'Playlist'], ['file', 'Pushed file'], ['live', 'Live captions view'], ['rundown', 'Service rundown view'], ['blank', 'Nothing scheduled']] },
      { k: 'banner', l: 'Alert banner (always on top)', o: [['off', 'Off'], ['on', 'On']] },
      { n: 'A click or key press goes full screen (kiosk browsers already are). The cursor is hidden; items outside their dates are skipped.' }],
    render: sgRender,
    post: (root, st) => {
      if (st.show === 'live') startFeeds(root, { seed: 3 });
      if (st.show === 'blank') return;
      const L = [root.querySelector('#dvL0'), root.querySelector('#dvL1')];
      let front = 0, i = 0;
      const show = () => { const back = L[1 - front]; back.innerHTML = slideHTML(i); back.style.opacity = '1'; L[front].style.opacity = '0'; front = 1 - front; i++; };
      show();
      TM.push(setInterval(() => { if (document.body.contains(root) && st.show === 'playlist') show(); }, 4500));
    }
  });

  /* ================================================================== lighting touch panel */
  const CUES = [
    { l: 'House lights up', bg: 'var(--warn)', fg: 'var(--bg)', shape: 'rounded', w: 2, h: 1, x: 3, y: 4, fw: 30, fh: 18 },
    { l: 'Worship wash', bg: 'var(--primary)', fg: 'var(--primary-fg)', shape: 'rounded', w: 2, h: 2, x: 36, y: 4, fw: 30, fh: 38 },
    { l: 'Sermon', bg: 'color-mix(in srgb,var(--fg) 84%,var(--bg))', fg: 'var(--bg)', shape: 'rounded', w: 1, h: 1, x: 69, y: 4, fw: 28, fh: 18 },
    { l: 'Prayer (dim)', bg: 'color-mix(in srgb,var(--primary) 30%,var(--card))', fg: 'var(--fg)', shape: 'pill', w: 1, h: 1, x: 3, y: 26, fw: 30, fh: 16 },
    { l: 'Altar glow', bg: 'color-mix(in srgb,var(--primary) 55%,var(--card))', fg: 'var(--fg)', shape: 'circle', w: 1, h: 1, x: 69, y: 26, fw: 17, fh: 28 },
    { l: 'Baptism spot', bg: 'var(--ok)', fg: 'var(--bg)', shape: 'rounded', w: 1, h: 1, x: 3, y: 46, fw: 30, fh: 18 },
    { l: 'Offering', bg: 'color-mix(in srgb,var(--warn) 45%,var(--card))', fg: 'var(--fg)', shape: 'rect', w: 1, h: 1, x: 36, y: 46, fw: 30, fh: 18 },
    { l: 'Candles', bg: 'color-mix(in srgb,var(--warn) 25%,var(--card))', fg: 'var(--fg)', shape: 'rect', w: 1, h: 1, x: 69, y: 58, fw: 28, fh: 18 },
    { l: 'Welcome', bg: 'var(--card)', fg: 'var(--fg)', shape: 'rounded', w: 1, h: 1, x: 3, y: 68, fw: 30, fh: 18 },
    { l: 'Blackout', bg: 'var(--err)', fg: 'var(--bg)', shape: 'rounded', w: 1, h: 1, x: 36, y: 68, fw: 30, fh: 18 }
  ];
  const LT_SAYS = { unreachable: 'The lighting program did not answer.', refused: 'That button does not work.', slow: 'Too many taps. Wait a moment.', other: 'That did not work.' };
  function ltRender(st) {
    const free = st.layout === 'free';
    const btns = CUES.map((c, n) => {
      const pos = free ? `left:${c.x}%;top:${c.y}%;width:${c.fw}%;height:${c.fh}%;` : `grid-column:span ${c.w};grid-row:span ${c.h};`;
      return `<button type="button" class="ltcue${st.lit.includes(n) ? ' lit' : ''}" data-shape="${c.shape}" data-n="${n}" style="background:${c.bg};color:${c.fg};${pos}">${c.l}</button>`;
    }).join('');
    return `<div class="dvlt"><main id="ltScreen" class="${free ? 'ltfree' : 'ltgrid'}" data-page="sanctuary">${btns}</main><div id="ltToast" role="status" aria-live="polite"${st.err === 'none' ? ' hidden' : ''}>${LT_SAYS[st.err] || ''}</div></div>`;
  }
  reg1('s-lighting-screen', 'Lighting touch panel', 'A page of big cue buttons for a tablet in the booth. Tap fires the cue once; buttons that are active are outlined. Grid or free layout designed in the admin builder.', '/screen?lighting=<page>', {
    cat: 'Displays & stage',
    st: { layout: 'grid', err: 'none', lit: [1] },
    groups: st => [{ k: 'layout', l: 'Page layout (set in admin)', o: [['grid', 'Grid'], ['free', 'Free placement']] },
      { k: 'err', l: 'Error toast (4 s)', o: [['none', 'None'], ['unreachable', 'Program did not answer'], ['refused', 'Refused'], ['slow', 'Rate limited'], ['other', 'Other']] },
      { n: 'Tap a cue: it flashes, fires and lights. Page title (Sanctuary scenes) is the browser tab title only.' }],
    render: ltRender,
    click: (ev, st, draw) => {
      const b = ev.target.closest('.ltcue'); if (!b) return;
      const n = +b.dataset.n; b.classList.add('sent'); setTimeout(() => b.classList.remove('sent'), 250);
      st.lit = st.lit.includes(n) ? st.lit.filter(x => x !== n) : [n];
      setTimeout(() => { const root = document.querySelector('.dvlt'); if (root) root.querySelectorAll('.ltcue').forEach((x, k) => x.classList.toggle('lit', st.lit.includes(k))); }, 200);
    }
  });

  /* ================================================================== OBS touch panel */
  const SCENES = ['Wide', 'Pulpit', 'Worship band', 'Lyrics only', 'Baptistry', 'Countdown', 'Welcome loop', 'Offering', 'Camera 2 close', 'Announcements'];
  const OB_SAYS = { unreachable: 'OBS did not answer.', refused: 'That scene is not in OBS any more.', slow: 'Too many taps. Wait a moment.', other: 'That did not work.' };
  function obRender(st) {
    return `<div class="dvob${st.conn === 'off' ? ' off' : ''}"><header id="obsHead"><span id="obsName">Sanctuary stream PC</span> ` +
      `<span id="obsLive" class="badge"${st.live === 'on' ? '' : ' hidden'}>LIVE</span> <span id="obsRec" class="badge rec"${st.rec === 'on' ? '' : ' hidden'}>REC</span> <span id="obsOff" class="badge off"${st.conn === 'off' ? '' : ' hidden'}>OBS not connected</span></header>` +
      `<main id="obsScenes">${SCENES.map(s => `<button type="button" class="obsscene${s === st.prog ? ' program' : ''}" data-scene="${s}">${s}</button>`).join('')}</main>` +
      `<div id="obsToast" role="status" aria-live="polite"${st.err === 'none' ? ' hidden' : ''}>${OB_SAYS[st.err] || ''}</div></div>`;
  }
  reg1('s-obs-screen', 'OBS touch panel', 'One computer\'s OBS scenes as big buttons for a tablet. The scene on program is red; LIVE and REC badges show the stream and recording.', '/screen?obs=<computer>', {
    cat: 'Displays & stage',
    st: { prog: 'Wide', live: 'on', rec: 'off', conn: 'on', err: 'none' },
    groups: st => [{ k: 'conn', l: 'OBS connection', o: [['on', 'Connected'], ['off', 'Not connected']] },
      { k: 'live', l: 'Streaming', o: [['on', 'LIVE'], ['off', 'Off']] }, { k: 'rec', l: 'Recording', o: [['off', 'Off'], ['on', 'REC']] },
      { k: 'err', l: 'Error toast (4 s)', o: [['none', 'None'], ['unreachable', 'OBS did not answer'], ['refused', 'Scene gone'], ['slow', 'Rate limited'], ['other', 'Other']] },
      { n: 'Tap a scene to put it on program.' }],
    render: obRender,
    click: (ev, st, draw) => {
      const b = ev.target.closest('.obsscene'); if (!b) return;
      b.classList.add('sent'); setTimeout(() => b.classList.remove('sent'), 250);
      st.prog = b.dataset.scene;
      setTimeout(() => { const r = document.querySelector('.dvob'); if (r) r.querySelectorAll('.obsscene').forEach(x => x.classList.toggle('program', x.dataset.scene === st.prog)); }, 200);
    }
  });
})();
