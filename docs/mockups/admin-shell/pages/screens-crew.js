/* Crew & phone screens: the crew web app (/crew and its tabs, join, page, offline), the intercom tab, and the parent
   paging room page. Everything is scoped under .kc (own prefix). Phone-first: one 430px column, centred on a wide window,
   with the app's own top bar and (new) a bottom tab bar. State demos live in the floating "Demo states" chip. */
(function () {
  'use strict';
  const CAT = 'Crew & phones';
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const MARK = (document.querySelector('.mk') || {}).innerHTML || '';
  const KI = {
    chat: '<path d="M2.5 3.5h11v7.5h-6l-3 2.5V11h-2z"/>',
    list: '<path d="M2.5 4.5l1.5 1.5 2.5-3M2.5 10.5l1.5 1.5 2.5-3M9 5h5M9 11h5"/>'
  };
  const kic = n => `<svg class="i" viewBox="0 0 16 16" aria-hidden="true">${KI[n] || I[n] || ''}</svg>`;
  const APP = 'Grace Church Crew', ME = 'Maya Okafor';

  // ---------------------------------------------------------------- CSS
  injectCSS('kc', `
  .kc{height:100vh;display:flex;justify-content:center;background:var(--bg);color:var(--fg);padding-top:44px;box-sizing:border-box}
  .kc *{box-sizing:border-box}
  .kc-phone{position:relative;width:100%;max-width:430px;height:100%;display:flex;flex-direction:column;background:var(--bg);overflow:hidden;font-size:16px;line-height:1.4}
  @media(min-width:700px){.kc{background:var(--sb);padding:20px}.kc-phone{border:1px solid var(--border);border-radius:30px;box-shadow:0 0 0 8px var(--card)}}
  .kc-bar{display:flex;align-items:center;gap:10px;padding:12px 16px;border-bottom:1px solid var(--border);flex:none;background:var(--bg)}
  .kc-bar svg{width:28px;height:28px;flex:none;color:var(--fg)}
  .kc-bar h1{font-size:17px;font-weight:600;margin:0;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .kc-who{color:var(--muted);font-size:13px;white-space:nowrap;max-width:55%;overflow:hidden;text-overflow:ellipsis}
  .kc-off{flex:none;padding:9px 16px;font-size:13px;color:var(--warn);background:color-mix(in srgb,var(--warn) 12%,var(--bg));border-bottom:1px solid color-mix(in srgb,var(--warn) 35%,transparent)}
  .kc-scroll{flex:1;min-height:0;overflow-y:auto;padding:16px;-webkit-overflow-scrolling:touch;display:flex;flex-direction:column}
  .kc-scroll>[role=tabpanel]{flex:1;display:flex;flex-direction:column;min-width:0}.kc-scroll>[role=tabpanel]>*{flex:none}.kc-scroll>[role=tabpanel]>.kc-chat{flex:1}
  .kc-card{background:var(--card);border:1px solid var(--border);border-radius:calc(var(--r) + 2px);padding:14px 16px;margin:0 0 14px;box-shadow:var(--shadow)}
  .kc-card h2{display:flex;justify-content:space-between;align-items:baseline;font-size:12px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);font-weight:600;margin:0 0 8px}
  .kc-card h2 small{font-size:12px;letter-spacing:0;text-transform:none;font-weight:500}
  .kc-note{color:var(--muted);font-size:14px;margin:0 0 8px}
  .kc-note.err,.kc-err{color:var(--err)}
  .kc-note a{color:var(--fg)}
  .kc-today{border-color:var(--primary)}
  .kc-pos{font-size:26px;font-weight:700;line-height:1.2;overflow-wrap:anywhere;letter-spacing:-.01em}
  .kc-btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:48px;padding:0 18px;border-radius:calc(var(--r) + 2px);border:1px solid var(--border);background:var(--card);color:var(--fg);font:inherit;font-weight:600;cursor:pointer;text-decoration:none;transition:background-color .15s,color .15s,transform .1s}
  .kc-btn:hover{background:var(--hover);color:var(--hover-fg)}.kc-btn:active{transform:scale(.98)}
  .kc-btn.p{background:var(--primary);color:var(--primary-fg);border-color:var(--primary)}.kc-btn.p:hover{background:color-mix(in srgb,var(--primary) 88%,var(--fg));color:var(--primary-fg)}
  .kc-btn.w{width:100%}.kc-btn.sm{min-height:44px;padding:0 14px;font-size:14px}
  .kc-btn.link{border:0;background:none;color:var(--muted);text-decoration:underline;font-weight:400;min-height:44px}
  .kc-btn.big{min-height:60px;font-size:18px;font-weight:700;width:100%}
  .kc-btn:disabled{opacity:.5;cursor:not-allowed;transform:none}
  .kc input,.kc select,.kc textarea{min-height:44px;font-size:16px}
  .kc input[type=checkbox]{width:22px;height:22px;min-height:0;flex:none}
  .kc input[type=range]{min-height:0}
  .kc input[type=file]{padding:8px}
  .kc-lbl{display:block;color:var(--muted);font-size:13px;font-weight:400;margin:12px 0 4px}
  .kc-form{display:grid;gap:0;margin-top:6px}
  .kc-btns{display:flex;gap:8px;margin-top:14px}.kc-btns .kc-btn{flex:1}
  .kc-plan{list-style:none;margin:0;padding:0}
  .kc-plan li{display:flex;gap:12px;padding:10px 0;border-top:1px solid var(--border);font-size:15px}
  .kc-plan .kc-time{color:var(--muted);min-width:5.2em;font-variant-numeric:tabular-nums;flex:none}
  .kc-plan .hd{font-weight:700;font-size:13px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);padding-top:14px}
  .kc-plan .done{opacity:.5}.kc-plan .cur{color:var(--primary);font-weight:600;position:relative}
  .kc-plan .cur::after{content:"Now";margin-left:auto;font-size:11px;padding:1px 8px;border-radius:99px;border:1px solid var(--primary);align-self:center;font-weight:600}
  .kc-tools{list-style:none;margin:0;padding:0}
  .kc-tools a{display:block;padding:12px 0;min-height:44px;color:var(--fg);text-decoration:none;border-top:1px solid var(--border)}
  .kc-tools li:first-child a{border-top:0}.kc-tools strong{display:block}.kc-tools span{display:block;color:var(--muted);font-size:14px}
  .kc-tabs{flex:none;display:flex;border-top:1px solid var(--border);background:var(--card);padding-bottom:env(safe-area-inset-bottom)}
  .kc-tabs button{flex:1;min-width:0;min-height:58px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;background:none;border:0;border-top:2px solid transparent;margin-top:-1px;color:var(--muted);font:inherit;font-size:11px;font-weight:500;cursor:pointer;padding:6px 2px;position:relative}
  .kc-tabs button svg{width:22px;height:22px}
  .kc-tabs button:hover{background:var(--hover);color:var(--hover-fg)}
  .kc-tabs button[aria-selected=true]{color:var(--primary);border-top-color:var(--primary)}
  .kc-tabs .ct{position:absolute;top:5px;left:calc(50% + 6px);min-width:16px;height:16px;border-radius:99px;background:var(--err);color:var(--bg);font-size:10px;font-weight:700;display:grid;place-items:center;padding:0 4px}
  .kc-foot{text-align:center;padding:4px 0 8px}
  /* chat */
  .kc-chat{display:flex;flex-direction:column;min-height:100%}
  .chat-top{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;margin:0 0 10px}
  .chat-pick{display:flex;align-items:center;gap:8px;margin:0}.chat-pick select{width:auto;min-width:9em}
  .chat-lbl{color:var(--muted);font-size:13px}
  .chat-push{display:flex;gap:10px;align-items:center;font-size:14px;color:var(--muted);font-weight:400;min-height:44px}
  .chat-list{list-style:none;margin:0 0 10px;padding:0;flex:1;display:flex;flex-direction:column;gap:8px}
  .chat-msg{max-width:86%;align-self:flex-start;background:var(--card);border:1px solid var(--border);border-radius:calc(var(--r) + 4px);border-bottom-left-radius:4px;padding:8px 12px}
  .chat-msg.mine{align-self:flex-end;background:var(--hover);border-color:color-mix(in srgb,var(--primary) 45%,var(--border));border-radius:calc(var(--r) + 4px);border-bottom-right-radius:4px}
  .chat-who{color:var(--muted);font-size:12px}.chat-msg.mine .chat-who{color:var(--primary)}
  .chat-body{white-space:pre-wrap;overflow-wrap:anywhere}
  .chat-del{background:none;border:0;color:var(--muted);text-decoration:underline;font:inherit;font-size:13px;min-height:32px;padding:0;cursor:pointer}
  .chat-form{position:sticky;bottom:-16px;margin:0 -16px -16px;padding:10px 16px 16px;background:var(--bg);border-top:1px solid var(--border);display:flex;gap:8px;align-items:flex-end}
  .chat-form textarea{flex:1;resize:none;min-height:48px}
  .chat-form .kc-btn{min-height:48px}
  /* checklists, resources */
  .cl-items{list-style:none;margin:0;padding:0}
  .cl-items li{border-top:1px solid var(--border)}.cl-items li:first-child{border-top:0}
  .cl-items label{display:flex;gap:12px;align-items:flex-start;padding:12px 0;min-height:48px;margin:0;font-weight:400;font-size:16px;cursor:pointer}
  .cl-items input[type=checkbox]{width:24px;height:24px;margin-top:0}
  .cl-items .cr-done label{opacity:.5}.cl-items .cr-done span{text-decoration:line-through}
  .kc-prog{height:4px;border-radius:2px;background:var(--hover);overflow:hidden;margin:0 0 4px}.kc-prog i{display:block;height:100%;background:var(--primary)}
  .cl-tpl{display:flex;gap:8px;align-items:center;padding:6px 0;border-top:1px solid var(--border);font-size:14px}.cl-tpl:first-child{border-top:0}.cl-tpl span{flex:1;min-width:0}
  .rs-item{padding:12px 0;border-top:1px solid var(--border)}.cl-items .rs-item:first-child{border-top:0}
  .rs-item a{font-weight:600;word-break:break-word;color:var(--fg);text-decoration:underline;text-decoration-color:var(--border);text-underline-offset:3px;display:inline-block;min-height:24px}
  .rs-item .cr-note{display:inline;margin-left:4px}
  .rs-frame{display:grid;place-items:center;text-align:center;width:100%;height:220px;margin-top:8px;border:1px solid var(--border);border-radius:var(--r);background:var(--bg);color:var(--muted);font-size:13px;padding:12px}
  .rs-list{list-style:none;margin:0;padding:0}
  .rs-list .rs-item{font-size:15px}
  .kc-skel{height:14px;border-radius:7px;background:var(--hover);margin:14px 0;animation:kcp 1.2s ease-in-out infinite}
  @keyframes kcp{50%{opacity:.4}}
  /* intercom */
  #ic .chips{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 12px}
  #ic .chip{font-size:13px;padding:5px 12px;border:1px solid var(--border);border-radius:99px;color:var(--muted)}
  #ic .chip.ok{color:var(--fg);border-color:var(--primary)}#ic .chip.bad{color:var(--err);border-color:var(--err)}
  #ic .banner{border:1px solid var(--border);border-radius:var(--r);padding:10px 14px;margin:0 0 12px;font-size:14px;background:var(--card)}
  #ic .banner.warn{border-color:var(--err);color:var(--err)}
  #ic .ch{border:1px solid var(--border);border-radius:calc(var(--r) + 2px);padding:12px 14px;margin:0 0 10px;background:var(--card)}
  #ic .ch.off{opacity:.45}
  #ic .ch h3{margin:0;font-size:16px}#ic .ch .act{color:var(--muted);font-size:14px;margin:2px 0 8px}
  #ic .ch .row{display:flex;flex-wrap:wrap;align-items:center;gap:10px}
  #ic .tog{min-height:44px;padding:0 16px;font:inherit;font-weight:600;border-radius:var(--r);border:1px solid var(--border);background:none;color:var(--fg);cursor:pointer}
  #ic .tog[aria-pressed=true]{background:var(--primary);color:var(--primary-fg);border-color:var(--primary)}
  #ic .tog:disabled{text-decoration:line-through;cursor:not-allowed;opacity:.6}
  #ic .ch input[type=range]{flex:1 1 110px;min-width:90px;height:44px}
  #ic .ch .res{font-size:14px;font-weight:600;flex-basis:100%}
  #ic .ch .res.cut,#ic .ch .res.denied{color:var(--err)}#ic .ch .res.talking{color:var(--ok)}
  #ic .foot{position:sticky;bottom:-16px;margin:0 -16px;padding:12px 16px 16px;background:var(--bg);border-top:1px solid var(--border);z-index:2}
  #ic .to{font-size:14px;color:var(--muted);margin:0 0 10px}
  #ic #icTalk{width:100%;min-height:92px;font:inherit;font-size:22px;font-weight:700;border-radius:20px;border:0;background:var(--primary);color:var(--primary-fg);touch-action:none;user-select:none;-webkit-user-select:none;-webkit-touch-callout:none;cursor:pointer;transition:transform .08s}
  #ic #icTalk[aria-pressed=true]{background:var(--err);color:var(--bg);transform:scale(.985)}
  #ic #icTalk:disabled{opacity:.45;cursor:not-allowed}
  #ic .foot .row2{display:flex;gap:8px;margin-top:8px}#ic .foot .row2 button{flex:1}
  #ic .note{color:var(--muted);font-size:13px;margin:10px 0 0}
  #ic .keep{display:flex;gap:10px;align-items:center;font-weight:400;min-height:44px;margin:0}
  #ic details summary{min-height:44px;display:flex;align-items:center;cursor:pointer}
  #ic details ul{list-style:none;padding:0;margin:0}#ic details li{display:flex;gap:8px;align-items:center;justify-content:space-between;padding:4px 0;font-size:14px}
  /* demo chip */
  .kc-demo{position:fixed;top:8px;right:12px;z-index:91;font-size:12px;display:flex;flex-direction:column;align-items:flex-end}
  .kc-demo>button{height:28px;padding:0 12px;border-radius:99px;border:1px solid var(--border);background:var(--card);color:var(--fg);font:inherit;font-size:12px;font-weight:600;cursor:pointer;box-shadow:0 2px 10px color-mix(in srgb,var(--fg) 18%,transparent)}
  .kc-demo .pn{margin-top:8px;width:min(290px,calc(100vw - 24px));max-height:calc(100vh - 60px);overflow:auto;background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:12px;box-shadow:0 10px 30px color-mix(in srgb,var(--fg) 25%,transparent)}
  .kc-demo .g{margin-bottom:10px}.kc-demo .g:last-child{margin-bottom:0}
  .kc-demo .g b{display:block;color:var(--muted);font-weight:500;margin-bottom:4px}
  .kc-demo .seg{display:flex;flex-wrap:wrap;gap:4px}
  .kc-demo .seg button{min-height:28px;padding:0 9px;border-radius:calc(var(--r) - 3px);border:1px solid var(--border);background:var(--bg);color:var(--fg);font:inherit;font-size:12px;cursor:pointer}
  .kc-demo .seg button[aria-pressed=true]{background:var(--hover);color:var(--hover-fg);border-color:var(--primary);font-weight:600}
  .kc-demo p{margin:0 0 8px;color:var(--muted)}
  /* standalone pages: join, page, offline */
  .kc-page{padding:20px 20px 32px}
  .kc-page h1{font-size:26px;margin:14px 0 8px;letter-spacing:-.01em}.kc-page h2.st{font-size:17px;margin:28px 0 8px}
  .kc-page p,.kc-page li{color:var(--muted)}.kc-page li{margin:0 0 10px}.kc-page li b{color:var(--fg)}
  .kc-page ul{padding-left:20px;margin:12px 0}
  .kc-page .kc-btn{margin:4px 8px 4px 0}
  .kc-page .lk{display:block;color:var(--fg)}
  .kc-msg{font-size:24px;line-height:1.3;margin:4px 0 18px;overflow-wrap:anywhere;font-weight:600}
  .kc-ok{color:var(--ok);font-weight:600;margin:0}
  .kc-center{flex:1;display:grid;place-items:center;text-align:center;padding:28px}
  .kc-center h1{font-size:26px;margin:0 0 8px}.kc-center p{color:var(--muted);max-width:28em;margin:0 auto 18px}
  /* paging room */
  .kc-pg{padding:16px 16px 40px}
  .kc-pg h1{font-size:24px;margin:6px 0 2px;letter-spacing:-.01em}.kc-pg .sub{color:var(--muted);margin:0 0 14px;font-size:15px}
  .kc-pg h2{font-size:12px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin:24px 0 8px;font-weight:600}
  .kc-pg .list{background:var(--card);border:1px solid var(--border);border-radius:calc(var(--r) + 2px);overflow:hidden;box-shadow:var(--shadow)}
  .kc-pg .kid,.kc-pg .page{display:flex;align-items:center;gap:10px;padding:10px 14px;border-top:1px solid var(--border);min-height:60px}
  .kc-pg .list>div:first-child{border-top:0}
  .kc-pg .name{flex:1;min-width:0;font-size:17px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .kc-pg .code{font-weight:700;letter-spacing:.08em;font-variant-numeric:tabular-nums;color:var(--muted)}
  .kc-pg .page .code{font-size:24px;min-width:3.6em;color:var(--fg)}
  .kc-pg .state{flex:1;color:var(--muted);font-size:14px}.kc-pg .state.on{color:var(--ok)}.kc-pg .state.wait{color:var(--warn)}
  .kc-pg .paged{color:var(--ok);font-weight:600}
  .kc-pg .empty{padding:18px 14px;color:var(--muted)}
  .kc-pg .note{color:var(--warn);margin:8px 0}
  .kc-pg input[type=search]{margin-bottom:8px}
  `);

  // ---------------------------------------------------------------- sample data
  const PLAN = [
    { t: '9:45 AM', k: 'item', n: 'Doors open, walk-in loop', s: 'done' },
    { t: '9:55 AM', k: 'item', n: 'Countdown video', s: 'done' },
    { t: '', k: 'header', n: 'Welcome' },
    { t: '10:00 AM', k: 'item', n: 'Welcome and announcements', s: 'current' },
    { t: '10:06 AM', k: 'song', n: 'Great Are You Lord' },
    { t: '10:11 AM', k: 'song', n: 'Build My Life' },
    { t: '10:17 AM', k: 'song', n: 'Goodness of God' },
    { t: '10:24 AM', k: 'item', n: 'Offering and prayer' },
    { t: '', k: 'header', n: 'Message' },
    { t: '10:30 AM', k: 'item', n: 'Sermon: Rooted in Hope (Pastor Daniel Reyes)' },
    { t: '11:05 AM', k: 'song', n: 'Do It Again' },
    { t: '11:10 AM', k: 'item', n: 'Benediction and dismissal' }
  ];
  const PEOPLE = ['Maya Okafor', 'Dana Whitfield', 'Ben Alvarez', 'Priya Nair', 'Tomás Rivera'];
  const ROLES = { team: 'Team', camera: 'Camera', sound: 'Sound', lyrics: 'Lyrics', producer: 'Producer' };
  const msgs0 = () => ({
    team: [
      { id: 1, a: 'Dana Whitfield', r: 'Producer', t: '8:02 AM', b: 'Morning team. Run sheet is updated; the sermon is 35 minutes today.' },
      { id: 2, a: 'Ben Alvarez', r: 'Sound', t: '8:05 AM', b: 'Wireless pack 4 had a dead battery, swapped it. All 8 channels checked.' },
      { id: 3, a: ME, r: 'Camera', t: '8:11 AM', b: 'Cam 2 is on the long lens for the choir. Cam 3, can you take the wide?', mine: 1 },
      { id: 4, a: 'Priya Nair', r: 'Lyrics', t: '8:12 AM', b: 'Lyrics loaded for all four songs. Build My Life has a new bridge slide.' },
      { id: 5, a: 'Dana Whitfield', r: 'Producer', t: '8:14 AM', b: 'Doors in 15. Checklists please!' }
    ],
    camera: [
      { id: 6, a: 'Tomás Rivera', r: 'Camera', t: '8:09 AM', b: 'Tripod on cam 3 is loose, using the spare.' },
      { id: 7, a: ME, r: 'Camera', t: '8:10 AM', b: 'Got it. I will tighten it after the sound check.', mine: 1 }
    ],
    sound: [{ id: 8, a: 'Ben Alvarez', r: 'Sound', t: '7:58 AM', b: 'Console loaded from last Sunday. Ringing out the vocal wedges now.' }],
    lyrics: [], producer: []
  });
  const lists0 = () => [
    { title: 'Pre-service', position: 'Camera 2', items: [['Power on cameras and check batteries', 1], ['White balance on the wide shot', 1], ['Tally lights lit on cams 1 to 3', 1], ['Intercom check with the booth', 0], ['Cable runs taped down', 0], ['Lens caps off and lenses wiped', 0]] },
    { title: 'Strike', position: 'Camera 2', items: [['Cap lenses and power down', 0], ['Coil and stow cables', 0], ['Batteries to the charging shelf', 0]] },
    { title: 'Safety walk-through', position: '', items: [['Exits clear and doors unlocked', 1], ['Stage steps and cable covers checked', 0], ['First aid kit in the booth', 0]] }
  ].map(l => ({ title: l.title, position: l.position, items: l.items.map(i => ({ text: i[0], done: !!i[1] })) }));
  const tpls0 = () => [
    { id: 1, title: 'Pre-service', position: 'Camera 2', n: 6 }, { id: 2, title: 'Strike', position: 'Camera 2', n: 3 },
    { id: 3, title: 'Safety walk-through', position: '', n: 3 }, { id: 4, title: 'Sound check', position: 'Sound', n: 7 },
    { id: 5, title: 'Lyrics prep', position: 'Lyrics', n: 5 }
  ];
  const res0 = () => [
    { id: 1, kind: 'file', title: 'Sunday service run sheet', position: '', ext: 'pdf', size: '214 KB' },
    { id: 2, kind: 'file', title: 'Parking and entrances map', position: '', ext: 'png', size: '1.2 MB' },
    { id: 3, kind: 'file', title: 'Emergency procedures', position: '', ext: 'pdf', size: '96 KB' },
    { id: 4, kind: 'file', title: 'Camera shot list', position: 'Camera 2', ext: 'pdf', size: '380 KB' },
    { id: 5, kind: 'link', title: 'Camera 2 quick start', position: 'Camera 2', url: 'https://wiki.gracechurch.example/media/camera-2' },
    { id: 6, kind: 'embed', title: 'Livestream health dashboard', position: 'Camera 2', url: 'https://stream.gracechurch.example/health' }
  ];
  const ACT = [
    ['Maya Okafor', 'checkin', 0, 'checked in', 'Nov 16, 8:12 AM'],
    ['Maya Okafor', 'checklist', 0, 'ticked “Power on cameras and check batteries” on Pre-service', 'Nov 16, 8:14 AM'],
    ['Dana Whitfield', 'page', 0, 'paged Camera 2', 'Nov 16, 8:20 AM'],
    ['Priya Nair', 'chat', 0, 'posted in Team chat', 'Nov 16, 8:12 AM'],
    ['Ben Alvarez', 'checkin', 0, 'checked in', 'Nov 16, 8:05 AM'],
    ['Relay', 'calltime', 0, 'sent the call time to Cameras (8:15 AM)', 'Nov 16, 7:15 AM'],
    ['Dana Whitfield', 'resource', 1, 'added the resource “Parking and entrances map”', 'Nov 15, 4:40 PM'],
    ['Dana Whitfield', 'checklist', 2, 'changed the checklist “Pre-service” for Camera 2', 'Nov 14, 7:02 PM'],
    ['Companion button', 'page', 6, 'paged Sound', 'Nov 10, 10:31 AM'],
    ['Tomás Rivera', 'checkin', 6, 'checked in', 'Nov 10, 8:09 AM'],
    ['Ben Alvarez', 'chat', 6, 'posted in Sound chat', 'Nov 10, 8:01 AM'],
    ['Dana Whitfield', 'calltime', 20, 'added a call time rule for Lyrics', 'Oct 27, 6:44 PM'],
    ['Priya Nair', 'checklist', 33, 'ticked “Lyrics loaded” on Lyrics prep', 'Oct 13, 8:20 AM'],
    ['Dana Whitfield', 'resource', 52, 'removed the resource “Old run sheet”', 'Sep 24, 9:10 AM']
  ].map((a, i) => ({ id: i + 1, who: a[0], kind: a[1], days: a[2], sum: a[3], at: a[4] }));
  const CHS = () => [
    { id: 1, name: 'Production', colour: '#3b82f6', can_talk: 1, can_listen: 1, talk_selected: 1, listen_selected: 1, volume: 80, act: 'Quiet · 9 listening' },
    { id: 2, name: 'Cameras', colour: '#f59e0b', can_talk: 1, can_listen: 1, talk_selected: 1, listen_selected: 1, volume: 65, act: 'Tomás Rivera talking' },
    { id: 3, name: 'Audio', colour: '#a855f7', can_talk: 0, can_listen: 1, talk_selected: 0, listen_selected: 1, volume: 70, act: 'Quiet · 4 listening' },
    { id: 4, name: 'Stage', colour: '#10b981', can_talk: 1, can_listen: 1, talk_selected: 0, listen_selected: 0, volume: 50, act: 'Quiet' }
  ];
  const KIDS = [['Ella M.', 'K7M2'], ['Noah T.', 'R3Q9'], ['Ava R.', 'T8D4'], ['Liam K.', 'B2X6'], ['Mia S.', 'M5N7'], ['Lucas P.', 'H9W3'], ['Zoe H.', 'C4L8'],
    ['Owen B.', 'F6V2'], ['Ivy C.', 'J3P5'], ['Eli D.', 'Z8Y1'], ['Nora F.', 'D7S4'], ['Jack W.', 'N2G6'], ['Ruby L.', 'L5R9'], ['Theo G.', 'W4T3']].map((k, i) => ({ id: 'k' + i, name: k[0], code: k[1] }));

  // ---------------------------------------------------------------- state
  let K = null, timers = [], bound = false;
  const later = (fn, ms) => { timers.push(setTimeout(fn, ms)); };
  function init(kind, tab, o) {
    return Object.assign({
      kind, tab, demo: innerWidth >= 900, net: 'on', booth: 'off', sched: 'one', role: 'member', plan: 'ok', showPlan: '1', tools: '1',
      checkin: 'arrived', push: 'off', data: 'full', unread: 2, chan: 'team', chatPush: {}, msgs: msgs0(), lists: lists0(), tpls: tpls0(),
      clForm: { title: '', pos: '', items: '', edit: null }, res: res0(), rsForm: { kind: 'file', title: '', pos: '', url: '', edit: null }, rsOpen: {},
      acF: { who: '', kind: '', days: '7' }, acShown: 8,
      ic: { link: 'on', outcome: 'talking', audio: 'speaker', notice: 'none', chans: 'some', pressed: false, latched: false, muteAll: false, keep: true,
        chs: CHS(), results: {}, cut: '', left: 4, phones: 'two' },
      pg: { list: 'full', note: 'none', pages: 'some', status: 'waiting', q: '', confirm: '', err: '', arr: [] },
      join: { cert: '1', url: '1' }, page: { state: 'open' }
    }, o || {});
  }
  const get = p => p.split('.').reduce((o, k) => o && o[k], K);
  function setp(p, v) {
    const ks = p.split('.'), last = ks.pop(), o = ks.reduce((a, k) => a[k], K);
    o[last] = v === 'true' ? true : v === 'false' ? false : v;
  }

  // ---------------------------------------------------------------- demo chip
  const seg = (label, key, opts) => `<div class="g"><b>${label}</b><div class="seg">${opts.map(o => `<button type="button" data-kc="set:${key}=${o[0]}" aria-pressed="${String(get(key)) === String(o[0])}">${o[1]}</button>`).join('')}</div></div>`;
  const DATA3 = [['full', 'Populated'], ['empty', 'Empty'], ['loading', 'Loading'], ['error', 'Error']];
  function demo() {
    let g = '';
    const crew = K.kind === 'crew';
    if (crew) {
      g += seg('Role', 'role', [['member', 'Member'], ['lead', 'Team lead'], ['operator', 'Operator']]);
      g += seg('Network', 'net', [['on', 'Online'], ['off', 'Offline']]);
      const t = K.tab;
      if (t === 'today') {
        g += seg('Booth (capture)', 'booth', [['running', 'Running'], ['off', 'Not running']]);
        g += seg('Call time', 'sched', [['one', 'One team'], ['multi', 'Two teams'], ['none', 'Not scheduled']]);
        g += seg('Plan', 'plan', [['ok', 'Loaded'], ['loading', 'Loading'], ['none', 'No plan today'], ['noplan', 'Not connected'], ['error', 'Error']]);
        g += seg('Plan card (show_plan)', 'showPlan', [['1', 'Shown'], ['0', 'Hidden']]);
        g += seg('Check in', 'checkin', [['hidden', 'Hidden'], ['lan', 'On network'], ['tap', 'Tap'], ['arrived', 'Arrived'], ['blocked', 'Location blocked']]);
        g += seg('Pages (web push)', 'push', [['off', 'Off'], ['on', 'On'], ['blocked', 'Blocked'], ['none', 'Unsupported']]);
        g += seg('Other tools', 'tools', [['1', 'Shown'], ['0', 'None']]);
      } else if (t === 'intercom') {
        g += intercomDemo();
      } else {
        g += seg('List state', 'data', DATA3);
      }
    } else if (K.kind === 'paging') {
      g += seg('Children', 'pg.list', [['full', '14 checked in'], ['few', '5 (no search)'], ['empty', 'Nobody'], ['loading', 'Loading']]);
      g += seg('Paged now', 'pg.pages', [['none', 'None'], ['some', 'Two pages']]);
      g += seg('Page status', 'pg.status', [['waiting', 'Waiting for booth'], ['shown', 'On screen'], ['sent', 'Sent to ProPresenter']]);
      g += seg('Note', 'pg.note', [['none', 'None'], ['pc', 'Planning Center not set up'], ['err', 'Request failed']]);
    } else if (K.kind === 'join') {
      g += seg('Certificate', 'join.cert', [['1', 'Relay has one'], ['0', 'None yet']]);
      g += seg('Crew address', 'join.url', [['1', 'Known'], ['0', 'Unknown']]);
    } else if (K.kind === 'page') {
      g += seg('Page link', 'page.state', [['open', 'Unread'], ['done', 'Confirmed'], ['bad', 'Not valid']]);
    } else g += '<p>No states: this page is static.</p>';
    return `<div class="kc-demo"><button type="button" data-kc="demo" aria-expanded="${K.demo}">Demo states ${K.demo ? '▴' : '▾'}</button>${K.demo ? `<div class="pn">${g}</div>` : ''}</div>`;
  }
  function intercomDemo() {
    return seg('Link', 'ic.link', [['on', 'Connected'], ['down', 'Reconnecting'], ['connecting', 'Connecting']]) +
      seg('Channels', 'ic.chans', [['some', 'Four channels'], ['none', 'None yet'], ['unsup', 'Browser unsupported']]) +
      seg('When you press Talk', 'ic.outcome', [['talking', 'Talking'], ['waiting', 'Waiting in line'], ['busy', 'Busy'], ['cut', 'Cut off'], ['muted', 'Muted by operator']]) +
      seg('Audio route', 'ic.audio', [['speaker', 'Phone speaker'], ['headset', 'Headset'], ['blocked', 'Mic blocked']]) +
      seg('Notice', 'ic.notice', [['none', 'None'], ['latch', 'Latch ends soon'], ['ios', 'iPhone note']]) +
      seg('My phones', 'ic.phones', [['two', 'Two'], ['none', 'None']]) +
      '<p>Hold the Talk button (or the space bar) to try a press.</p>';
  }

  // ---------------------------------------------------------------- crew app
  function tabsFor() {
    const t = [['today', 'Today', 'cal'], ['chat', 'Chat', 'chat'], ['checklists', 'Checklists', 'list'], ['resources', 'Resources', 'doc']];
    if (K.role === 'operator') t.push(['activity', 'Activity', 'pulse']);
    t.push(['intercom', 'Intercom', 'mic']);
    return t;
  }
  function bar(who) {
    return `<header class="kc-bar"><span aria-hidden="true">${MARK}</span><h1>${APP}</h1>${who ? `<span class="kc-who">${ME} · Camera</span>` : ''}</header>`;
  }
  function crew() {
    if (!tabsFor().some(t => t[0] === K.tab)) K.tab = 'today';
    const panels = { today, chat, checklists, resources, activity, intercom };
    return `<div class="kc-phone">${bar(1)}${K.net === 'off' ? '<p class="kc-off" role="status" id="crNet">You are offline. The plan will update when you are back on the network.</p>' : ''}
      <main class="kc-scroll" id="crMain"><div role="tabpanel" id="panel-${K.tab}" aria-labelledby="tab-${K.tab}">${panels[K.tab]()}</div></main>
      <nav class="kc-tabs" role="tablist" aria-label="Crew tools">${tabsFor().map(t => `<button role="tab" id="tab-${t[0]}" aria-controls="panel-${t[0]}" aria-selected="${t[0] === K.tab}" data-tab="${t[0]}" data-kc="tab:${t[0]}" type="button">${kic(t[2])}<span>${t[1]}</span>${t[0] === 'chat' && K.tab !== 'chat' && K.unread ? `<i class="ct" aria-label="${K.unread} new messages">${K.unread}</i>` : ''}</button>`).join('')}</nav></div>`;
  }

  function today() {
    let h = '';
    if (K.booth === 'off') {
      const one = K.sched === 'one', multi = K.sched === 'multi';
      h += `<section class="kc-card kc-today" id="crToday" aria-live="polite"><h2>Today</h2><div class="kc-pos" id="crPos">${K.sched === 'none' ? 'No position today' : multi ? 'Camera 2, Livestream' : 'Camera 2'}</div>
        <div class="kc-note" id="crCall" style="margin-top:6px">${K.sched === 'none' ? 'No call time today.' : 'Call time 8:15 AM' + (multi ? ' · Camera 2' : '')}</div></section>`;
    }
    if (K.showPlan === '1') {
      let note, list = '';
      if (K.plan === 'ok') {
        note = 'Sunday Service · 10:00 AM';
        list = `<ol class="kc-plan" id="crItems">${PLAN.map(p => `<li class="${p.k === 'header' ? 'hd ' : ''}${p.s === 'done' ? 'done cr-done' : p.s === 'current' ? 'cur cr-current' : ''}">${p.t ? `<span class="kc-time cr-time">${p.t}</span>` : ''}<span class="cr-title">${p.k === 'song' ? 'Song: ' : ''}${esc(p.n)}</span></li>`).join('')}</ol>`;
      } else note = { loading: 'Loading…', none: 'There is no plan for today.', noplan: 'No plan is connected.', error: 'The plan could not be loaded.' }[K.plan];
      h += `<section class="kc-card" id="crPlan" aria-live="polite"><h2>Plan</h2><div class="kc-note${K.plan === 'error' ? ' err' : ''}" id="crPlanNote">${note}</div>${list}</section>`;
    }
    if (K.checkin !== 'hidden') {
      const ci = {
        lan: ['Check-in happens by itself when your phone is on the church network.', 0], tap: ['Tap to check in when you are at the church.', 1],
        arrived: ['You are checked in at 8:12 AM.', 0], blocked: ['Allow location for this site to check in, then try again.', 1], finding: ['Finding you…', 1]
      }[K.checkin];
      h += `<section class="kc-card" id="crIn"><h2>Check in</h2><div class="kc-note${K.checkin === 'blocked' ? ' err' : ''}" id="crInNote" role="status" aria-live="polite">${ci[0]}</div>${ci[1] ? `<button type="button" class="kc-btn p big cr-confirm" id="crInBtn" data-kc="checkin"${K.checkin === 'finding' ? ' disabled' : ''}>Check in</button>` : ''}</section>`;
    }
    if (K.push !== 'none') {
      const on = K.push === 'on';
      h += `<section class="kc-card cr-alerts" id="crAlerts"><h2>Pages</h2><div class="kc-note" id="crAlertsNote">${K.push === 'blocked' ? 'Pages are blocked for this site. Allow notifications in the browser settings to use them.' : on ? 'This phone buzzes when the booth pages you.' : 'Get a buzz on this phone when the booth pages you.'}</div>${K.push === 'blocked' ? '' : `<button type="button" class="kc-btn sm" id="crAlertsBtn" data-kc="push">${on ? 'Turn off pages' : 'Turn on pages'}</button>`}</section>`;
    }
    if (K.tools === '1') {
      h += `<section class="kc-card"><h2>Tools</h2><ul class="kc-tools cr-tools"><li><a href="#" data-kc="toast:Opens the operator panel"><strong>Operator panel</strong><span>Run the service from the control room</span></a></li><li><a href="#" data-kc="toast:Opens the viewer page"><strong>Viewer page</strong><span>See what the congregation sees</span></a></li></ul></section>`;
    }
    h += `<form class="kc-foot cr-out" onsubmit="return false"><button type="button" class="kc-btn link" data-kc="toast:Signed out">Sign out</button></form>`;
    return h;
  }

  const dataGate = (inner, note) => K.data === 'loading' ? '<div class="kc-skel"></div><div class="kc-skel" style="width:70%"></div><div class="kc-skel" style="width:85%"></div>' : K.data === 'error' ? `<div class="kc-note err" id="${note}" role="status">That did not work.</div>` : inner;
  const chansFor = () => { const c = [['team', 'Team'], ['camera', 'Camera']]; if (K.role === 'operator') c.push(['sound', 'Sound'], ['lyrics', 'Lyrics'], ['producer', 'Producer']); return c; };

  function chat() {
    if (!chansFor().some(c => c[0] === K.chan)) K.chan = 'team';
    const list = K.data === 'empty' ? [] : (K.msgs[K.chan] || []), mod = K.role === 'operator';
    const body = K.data === 'loading' ? '<li class="kc-skel" style="width:60%"></li><li class="kc-skel" style="width:80%;align-self:flex-end"></li><li class="kc-skel" style="width:50%"></li>' :
      list.map(m => `<li class="chat-msg${m.mine ? ' mine' : ''}" data-id="${m.id}"><div class="chat-who">${esc(m.a)}${m.r ? ' · ' + esc(m.r) : ''} · ${m.t}</div><div class="chat-body">${esc(m.b)}</div>${m.mine || mod ? `<button type="button" class="chat-del" data-kc="chatDel:${m.id}" aria-label="Delete message from ${esc(m.a)}">Delete</button>` : ''}</li>`).join('');
    const note = K.data === 'error' ? 'That did not work.' : K.data === 'empty' || (K.data === 'full' && !list.length) ? 'No messages yet.' : '';
    return `<div id="chat" class="chat kc-chat"><div class="chat-top"><label class="chat-pick"><span class="chat-lbl">Channel</span> <select id="chatChannel" aria-label="Channel" data-kc-change="chan">${chansFor().map(c => `<option value="${c[0]}"${c[0] === K.chan ? ' selected' : ''}>${c[1]}</option>`).join('')}</select></label>
      <label class="chat-push"><input type="checkbox" id="chatPush" data-kc-change="chatPush"${K.chatPush[K.chan] ? ' checked' : ''}> Quiet notification when I am not looking</label></div>
      <div class="kc-note${K.data === 'error' ? ' err' : ''}" id="chatNote" role="status" aria-live="polite">${note}</div>
      <ol class="chat-list" id="chatList" aria-live="polite" aria-label="Messages">${body}</ol>
      <form class="chat-form" id="chatForm" autocomplete="off" onsubmit="return false"><textarea id="chatText" rows="2" maxlength="500" placeholder="Message" aria-label="Message"></textarea><button type="submit" class="kc-btn p" id="chatSend" data-kc="chatSend"${K.net === 'off' ? ' disabled' : ''}>Send</button></form></div>`;
  }

  function checklists() {
    const ed = K.role !== 'member', lists = K.data === 'empty' ? [] : K.lists;
    let h = `<div id="cl" class="cl"><div class="kc-note${K.data === 'error' ? ' err' : ''}" id="clNote" role="status" aria-live="polite">${K.data === 'error' ? 'That did not work.' : (K.data === 'empty' || !lists.length) && K.data !== 'loading' ? 'No checklists for you at this service.' : ''}</div><div id="clLists">`;
    if (K.data === 'loading') h += '<div class="kc-skel"></div><div class="kc-skel" style="width:75%"></div><div class="kc-skel" style="width:88%"></div>';
    else if (K.data !== 'error') lists.forEach((l, i) => {
      const done = l.items.filter(x => x.done).length;
      h += `<section class="kc-card"><h2><span>${esc(l.title)}${l.position ? ' · ' + esc(l.position) : ''}</span><small>${done} of ${l.items.length}</small></h2><div class="kc-prog"><i style="width:${done / l.items.length * 100}%"></i></div><ul class="cl-items">${l.items.map((it, j) => `<li${it.done ? ' class="cr-done"' : ''}><label><input type="checkbox" data-kc-change="cl:${i}:${j}"${it.done ? ' checked' : ''}${K.net === 'off' ? ' disabled' : ''}><span>${esc(it.text)}</span></label></li>`).join('')}</ul></section>`;
    });
    h += '</div>';
    if (ed) {
      const f = K.clForm, tp = K.role === 'lead' ? K.tpls.filter(t => t.position === 'Camera 2') : K.tpls;
      h += `<section class="kc-card" id="clEditBox"><h2>Edit checklists</h2><div id="clTemplates">${tp.map(t => `<div class="cl-tpl"><span>${esc(t.title)} · ${esc(t.position || 'Everyone')} (${t.n})</span><button type="button" class="kc-btn sm" data-kc="clEdit:${t.id}">Edit</button><button type="button" class="kc-btn sm" data-kc="clDel:${t.id}">Delete</button></div>`).join('')}</div>
        <div class="kc-form cl-form"><label class="kc-lbl chat-lbl" for="clTitle">Title</label><input id="clTitle" maxlength="60" autocomplete="off" value="${esc(f.title)}">
        <label class="kc-lbl chat-lbl" for="clPos">Position</label><input id="clPos" list="clPositions" maxlength="80" autocomplete="off" placeholder="A Planning Center team, or blank for everyone" value="${esc(f.pos)}"><datalist id="clPositions">${(K.role === 'lead' ? ['Camera 2'] : ['Camera 2', 'Sound', 'Lyrics', 'Livestream', 'Stage']).map(p => `<option value="${p}">`).join('')}</datalist>
        <label class="kc-lbl chat-lbl" for="clItems">Items, one per line</label><textarea id="clItems" rows="6">${esc(f.items)}</textarea>
        <div class="kc-btns cl-btns"><button type="button" class="kc-btn p" id="clSave" data-kc="clSave">Save checklist</button>${f.edit !== null ? '<button type="button" class="kc-btn" id="clCancel" data-kc="clCancel">Cancel</button>' : ''}</div></div></section>`;
    }
    return h + '</div>';
  }

  const mbHint = { file: 'PDF, picture, text, Word, Excel or PowerPoint, up to 25 MB.', embed: 'Pages from these sites can be shown here: youtube.com, vimeo.com, docs.google.com. YouTube watch addresses work.', link: 'Opens in a new tab.' };
  function resources() {
    const ed = K.role !== 'member', all = K.data === 'empty' ? [] : K.res, groups = {};
    all.forEach(r => { (groups[r.position] = groups[r.position] || []).push(r); });
    let h = `<div id="rs" class="rs"><div class="kc-note${K.data === 'error' ? ' err' : ''}" id="rsNote" role="status" aria-live="polite">${K.data === 'error' ? 'That did not work.' : K.data === 'empty' ? 'No resources for you yet.' : ''}</div><div id="rsLists">`;
    if (K.data === 'loading') h += '<div class="kc-skel"></div><div class="kc-skel" style="width:75%"></div>';
    else if (K.data !== 'error') Object.keys(groups).sort((a, b) => a ? 1 : -1).forEach(pos => {
      h += `<section class="kc-card"><h2>${esc(pos || 'Everyone')}</h2><ul class="cl-items">${groups[pos].map(r => `<li class="rs-item"><a href="#" data-kc="toast:${r.kind === 'file' ? 'Opens the file' : 'Opens in a new tab'}">${esc(r.title)}</a><span class="cr-note">${r.kind === 'file' ? r.ext.toUpperCase() + ' · ' + r.size : r.kind === 'embed' ? 'page' : 'link'}</span>${r.kind === 'embed' ? ` <button type="button" class="kc-btn sm" data-kc="rsShow:${r.id}">${K.rsOpen[r.id] ? 'Hide' : 'Show here'}</button>${K.rsOpen[r.id] ? `<div class="rs-frame" role="img" aria-label="${esc(r.title)}">Embedded page: ${esc(r.url.replace('https://', ''))}<br>Sandboxed frame, no referrer</div>` : ''}` : ''}</li>`).join('')}</ul></section>`;
    });
    h += '</div>';
    if (ed) {
      const f = K.rsForm, file = f.kind === 'file';
      h += `<section class="kc-card" id="rsEditBox"><h2>Add or change resources</h2><div id="rsManage">${K.res.filter(r => K.role === 'operator' || r.position === 'Camera 2').map(r => `<div class="cl-tpl"><span>${esc(r.title)} · ${esc(r.position || 'Everyone')} · ${r.kind}</span><button type="button" class="kc-btn sm" data-kc="rsEdit:${r.id}">Edit</button><button type="button" class="kc-btn sm" data-kc="rsDel:${r.id}">Delete</button></div>`).join('')}</div>
        <div class="kc-form cl-form"><label class="kc-lbl chat-lbl" for="rsKind">Kind</label><select id="rsKind" data-kc-change="rsKind"${f.edit !== null ? ' disabled' : ''}><option value="file"${f.kind === 'file' ? ' selected' : ''}>File from this phone</option><option value="link"${f.kind === 'link' ? ' selected' : ''}>Link</option><option value="embed"${f.kind === 'embed' ? ' selected' : ''}>Embedded page</option></select>
        <label class="kc-lbl chat-lbl" for="rsTitle">Title</label><input id="rsTitle" maxlength="80" autocomplete="off" value="${esc(f.title)}">
        <label class="kc-lbl chat-lbl" for="rsPos">Position</label><input id="rsPos" list="rsPositions" maxlength="80" autocomplete="off" placeholder="A Planning Center team, or blank for everyone" value="${esc(f.pos)}"><datalist id="rsPositions">${['Camera 2', 'Sound', 'Lyrics', 'Livestream'].map(p => `<option value="${p}">`).join('')}</datalist>
        ${file ? '' : `<label class="kc-lbl chat-lbl" for="rsUrl" id="rsUrlLbl">Web address</label><input id="rsUrl" type="url" maxlength="500" autocomplete="off" inputmode="url" value="${esc(f.url)}">`}
        ${file && f.edit === null ? '<input id="rsFile" type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,.gif,.txt,.docx,.xlsx,.pptx" style="margin-top:12px">' : ''}
        <div class="kc-note" id="rsHint" style="margin-top:8px">${mbHint[f.kind]}</div>
        <div class="kc-btns cl-btns"><button type="button" class="kc-btn p" id="rsSave" data-kc="rsSave">${f.edit !== null ? 'Save' : 'Add'}</button>${f.edit !== null ? '<button type="button" class="kc-btn" id="rsCancel" data-kc="rsCancel">Cancel</button>' : ''}</div></div></section>`;
    }
    return h + '</div>';
  }

  function activity() {
    const f = K.acF, all = K.data === 'empty' ? [] : ACT.filter(a => (!f.who || a.who === f.who) && (!f.kind || a.kind === f.kind) && (!f.days || a.days < +f.days));
    const shown = all.slice(0, K.acShown);
    return `<div id="ac" class="ac"><div class="kc-note${K.data === 'error' ? ' err' : ''}" id="acNote" role="status" aria-live="polite">${K.data === 'error' ? 'That did not work.' : !all.length && K.data !== 'loading' ? 'Nothing yet for that filter.' : ''}</div>
      <section class="kc-card"><h2>Crew activity</h2><div class="kc-form cl-form">
      <label class="kc-lbl chat-lbl" for="acWho">Person</label><select id="acWho" data-kc-change="acWho"><option value="">Everyone</option>${PEOPLE.map(p => `<option${p === f.who ? ' selected' : ''}>${p}</option>`).join('')}</select>
      <label class="kc-lbl chat-lbl" for="acKind">What</label><select id="acKind" data-kc-change="acKind">${[['', 'Everything'], ['chat', 'Chat'], ['checkin', 'Check-ins'], ['checklist', 'Checklists'], ['resource', 'Resources'], ['page', 'Paging'], ['calltime', 'Call times']].map(o => `<option value="${o[0]}"${o[0] === f.kind ? ' selected' : ''}>${o[1]}</option>`).join('')}</select>
      <label class="kc-lbl chat-lbl" for="acDays">When</label><select id="acDays" data-kc-change="acDays">${[['1', 'Last day'], ['7', 'Last week'], ['', 'All kept (90 days)']].map(o => `<option value="${o[0]}"${o[0] === f.days ? ' selected' : ''}>${o[1]}</option>`).join('')}</select></div>
      ${K.data === 'loading' ? '<div class="kc-skel"></div><div class="kc-skel" style="width:80%"></div><div class="kc-skel" style="width:90%"></div>' : K.data === 'error' ? '' : `<ul id="acList" class="rs-list" style="margin-top:10px">${shown.map(e => `<li class="rs-item">${esc(e.who)} · ${esc(e.sum)} <span class="cr-note">${e.at}</span></li>`).join('')}</ul>
      <div class="kc-btns cl-btns">${all.length > shown.length ? '<button type="button" class="kc-btn" id="acMore" data-kc="acMore">Older</button>' : ''}</div>`}</section></div>`;
  }

  // ---------------------------------------------------------------- intercom tab
  const ordinal = n => n + (['th', 'st', 'nd', 'rd'][(n % 100 - 20) % 10] || ['th', 'st', 'nd', 'rd'][n % 100] || 'th');
  const DEN = { busy: 'Busy · someone with priority is talking', muted: 'Muted by an operator' };
  function resText(r) {
    return r.state === 'talking' ? 'Talking' : r.state === 'waiting' ? `Waiting · you're ${ordinal(r.position)} in line` : r.state === 'cut' ? 'Cut off · ' + r.by + ' has priority' : r.state === 'denied' ? DEN[r.reason] : '';
  }
  const icChs = () => K.ic.chans === 'none' ? [] : K.ic.chs;
  const icTargets = () => icChs().filter(c => c.can_talk && c.talk_selected);
  const icUp = () => K.ic.link === 'on';
  function icTop() {
    const ic0 = K.ic, up = icUp(), conn = ic0.link === 'connecting';
    const local = up ? 'Local: connected · 23 ms' : conn ? 'Local: connecting' : 'Local: offline';
    let h = `<div class="chips" aria-label="Connection">${conn ? '' : '<span class="chip" id="icProd">Sunday 10:30 Service</span>'}<span class="chip ${up ? 'ok' : conn ? '' : 'bad'}" id="icLocal">${local}</span>${ic0.audio === 'headset' && up ? '<span class="chip" id="icHeadset">AirPods Pro</span>' : ''}</div>`;
    if (ic0.link === 'down') h += `<div class="banner warn" id="icDown" role="alert">Reconnecting in ${ic0.left} s</div>`;
    if (ic0.cut) h += `<div class="banner warn" id="icCut" role="alert">${esc(ic0.cut)}</div>`;
    if (ic0.audio === 'blocked') h += '<div class="banner warn" id="icMic" role="alert">The microphone is blocked. Allow it for this site in the browser settings, then try again.</div>';
    if (ic0.notice === 'latch') h += '<div class="banner" id="icLatchWarn" role="status">Latch ends in 10 s on Production.</div>';
    if (ic0.audio === 'speaker' && up) h += '<div class="banner" id="icRoute" role="status">Using the phone\'s own microphone and speaker. Use a headset to avoid feedback.</div>';
    if (up && K.ic.chans === 'none') h += '<p class="note" id="icNoChannels">You are not on any channel yet. Ask an admin to add you.</p>';
    return h;
  }
  function icChannels() {
    const up = icUp();
    return icChs().map(c => {
      const r = K.ic.results[c.id];
      return `<div class="ch${up ? '' : ' off'}" style="border-left-color:${c.colour}"><h3>${c.name}</h3><div class="act">${c.act}</div><div class="row">
        <button type="button" class="tog" aria-pressed="${!!c.talk_selected}"${!up || !c.can_talk ? ' disabled' : ''} data-kc="icSel:${c.id}:talk_selected">Talk</button>
        <button type="button" class="tog" aria-pressed="${!!c.listen_selected}"${!up || !c.can_listen ? ' disabled' : ''} data-kc="icSel:${c.id}:listen_selected">Listen</button>
        <input type="range" min="0" max="100" value="${c.volume}" aria-label="${c.name} listen volume"${!c.can_listen ? ' disabled' : ''} data-kc-change="icVol:${c.id}">
        ${r ? `<span class="res ${r.state}">${resText(r)}</span>` : ''}</div></div>`;
    }).join('');
  }
  const holding = () => Object.values(K.ic.results).some(r => r.state === 'talking');
  const busyNow = () => Object.values(K.ic.results).some(r => r.state === 'talking' || r.state === 'waiting');
  function talkLabel() {
    const i = K.ic;
    return i.pressed ? (holding() ? 'Talking' + (i.latched ? ' (latched)' : '') : busyNow() ? 'Waiting' : 'Talking…') : 'Hold to talk';
  }
  function intercom() {
    if (K.ic.chans === 'unsup') return '<p class="kc-note" id="icUnsupported">This browser cannot do the intercom. Use a current Safari or Chrome over https.</p>';
    const i = K.ic, up = icUp(), t = icTargets();
    const phones = i.phones === 'two' ? '<li><span>iPhone Safari · last seen 2026-11-16</span><button type="button" class="kc-btn sm" data-kc="toast:Phone signed out">Sign out</button></li><li><span>Android phone Chrome · last seen 2026-11-09</span><button type="button" class="kc-btn sm" data-kc="toast:Phone signed out">Sign out</button></li>' : '';
    return `<div id="ic"><div id="kcIcTop">${icTop()}</div><div id="icChannels">${icChannels()}</div>
      <p class="note">Space bar talks on a laptop. A headset's play/pause button toggles Latch where the browser allows it.</p>
      <label class="note keep"><input type="checkbox" id="icKeep"${i.keep ? ' checked' : ''} data-kc-change="icKeep"> Keep this phone signed in to the intercom</label>
      <details class="note" id="icMine"><summary>My phones</summary><ul id="icMyList">${phones}</ul><p class="note" id="icMyEmpty"${i.phones === 'two' ? ' hidden' : ''}>No phones are signed in to the intercom.</p></details>
      ${i.notice === 'ios' ? '<p class="note" id="icIos">On iPhone the intercom only works while this tab is in front. Keep the screen on.</p>' : ''}
      <div class="foot"><p class="to" id="icTo">${t.length ? 'Talks to: ' + t.map(c => c.name).join(', ') : 'Pick Talk on a channel to use the button.'}</p>
      <button type="button" id="icTalk" aria-pressed="${i.pressed}"${!up || !t.length ? ' disabled' : ''}>${talkLabel()}</button>
      <div class="row2"><button type="button" class="tog" id="icLatch" aria-pressed="${i.latched}"${!up || !t.length ? ' disabled' : ''}>Latch</button><button type="button" class="tog" id="icMuteAll" aria-pressed="${i.muteAll}" data-kc="icMute">Mute all listen</button></div></div>
      <div class="sr" id="icLive" aria-live="polite" style="position:absolute;left:-9999px"></div></div>`;
  }
  function outcomes(t) {
    const r = {};
    t.forEach((c, n) => { r[c.id] = { state: 'talking' }; });
    const first = t[0].id, o = K.ic.outcome;
    if (o === 'waiting') r[first] = { state: 'waiting', position: 2 };
    if (o === 'busy') t.forEach(c => { r[c.id] = { state: 'denied', reason: 'busy' }; });
    if (o === 'muted') t.forEach(c => { r[c.id] = { state: 'denied', reason: 'muted' }; });
    if (o === 'cut') { r[first] = { state: 'cut', by: 'Dana Whitfield' }; K.ic.cut = 'Cut off on ' + t[0].name + '. Dana Whitfield has priority.'; }
    return r;
  }
  function icRefresh() {
    if (K.tab !== 'intercom' || !document.getElementById('kcIcTop')) return render();
    const i = K.ic, t = icTargets(), b = document.getElementById('icTalk');
    document.getElementById('kcIcTop').innerHTML = icTop();
    document.getElementById('icChannels').innerHTML = icChannels();
    b.setAttribute('aria-pressed', i.pressed); b.textContent = talkLabel();
    document.getElementById('icLatch').setAttribute('aria-pressed', i.latched);
    document.getElementById('icMuteAll').setAttribute('aria-pressed', i.muteAll);
  }
  function press(latched) {
    const i = K.ic, t = icTargets();
    if (K.kind !== 'crew' || K.tab !== 'intercom' || !icUp() || i.pressed || !t.length) return;
    i.pressed = true; i.latched = !!latched; i.cut = ''; i.results = outcomes(t); icRefresh();
  }
  function release() {
    const i = K.ic;
    if (!K || K.kind !== 'crew' || !i.pressed) return;
    i.pressed = false; i.latched = false; icRefresh();
    later(() => { if (!i.pressed) { i.results = {}; icRefresh(); } }, 1500);
  }
  function ticker() {
    clearInterval(ticker.id);
    ticker.id = setInterval(() => {
      if (!K || K.kind !== 'crew' || K.ic.link !== 'down') return;
      K.ic.left = K.ic.left > 0 ? K.ic.left - 1 : 4;
      const d = document.getElementById('icDown'); if (d) d.textContent = 'Reconnecting in ' + K.ic.left + ' s';
    }, 1000);
  }

  // ---------------------------------------------------------------- other pages
  function join() {
    const j = K.join, name = APP;
    return `<div class="kc-phone"><main class="kc-scroll kc-page"><span class="lk">${LOCK}</span><h1>Join ${name}</h1>
      <p>This page is only on the church network. ${name} is a secure page, and your phone has not been told to trust it yet, so it would show a warning on its own. That is expected: nobody outside this network can read it. Two steps, once per phone.</p>
      <h2 class="st">1. Let your phone trust Relay</h2>
      ${j.cert === '1' ? `<p><a class="kc-btn" href="#" data-kc="toast:Downloads relay.crt">Download the certificate</a></p><ul>
        <li><b>iPhone or iPad:</b> tap Allow, then open Settings, tap "Profile Downloaded" and Install. Then go to Settings, General, About, Certificate Trust Settings and turn on trust for Relay.</li>
        <li><b>Android:</b> open Settings, search for "CA certificate" (or Security, Encryption and credentials, Install a certificate), choose CA certificate, and pick the downloaded file.</li></ul>` :
        '<p>Relay has no certificate yet. Ask whoever runs the booth to open Relay once, then scan again.</p>'}
      <h2 class="st">2. Open the crew page</h2>
      ${j.url === '1' ? `<p><a class="kc-btn p" href="#" data-kc="toast:Opens https://relay.local:8443/crew">Open ${name}</a></p><p>If your phone still shows a warning, you can tap Advanced and continue; that works for this page too, but the app will not install to your home screen until the certificate is trusted.</p>` :
        '<p>Relay cannot tell what its address is right now. Ask whoever runs the booth.</p>'}</main></div>`;
  }
  function pagePage() {
    const s = K.page.state;
    return `<div class="kc-phone">${bar(0)}<main class="kc-scroll">${s === 'bad' ? '<section class="kc-card"><h2>Page</h2><p class="kc-note">This page link is not valid any more.</p></section>' :
      `<section class="kc-card cr-page"><h2>Page</h2><p class="kc-msg cr-msg">Camera 2, please come to the booth. Quick note about the second song.</p>${s === 'done' ? '<p class="kc-ok cr-ok" role="status">Confirmed. The buzzing has stopped.</p>' : '<form onsubmit="return false"><button type="submit" class="kc-btn p big cr-confirm" data-kc="pageOk">I have read this</button></form>'}</section>`}
      <p class="kc-note"><a href="#" data-kc="toast:Opens /crew">Open the crew page</a></p></main></div>`;
  }
  function offline() {
    return `<div class="kc-phone"><main class="kc-center"><div><h1>Can't reach Relay</h1><p>Your phone is not on the network Relay is on. Join the same Wi-Fi as the booth and try again.</p><button type="button" class="kc-btn p" data-kc="toast:Reloading the page">Try again</button></div></main></div>`;
  }

  // ---------------------------------------------------------------- parent paging room
  const STATE = { waiting: ['Waiting for the booth', 'wait'], shown: ['On screen', 'on'], sent: ['Sent to ProPresenter', 'on'] };
  function paging() {
    const p = K.pg;
    const kids = p.list === 'empty' ? [] : p.list === 'few' ? KIDS.slice(0, 5) : KIDS;
    const pages = (p.pages === 'some' ? [{ id: 'p1', code: 'K7M2', kid: 'k0' }, { id: 'p2', code: 'T8D4', kid: 'k2' }] : []).concat(p.arr).filter(x => !p.gone || p.gone.indexOf(x.id) < 0);
    const paged = {}; pages.forEach(x => { paged[x.kid] = 1; });
    const q = p.q.trim().toLowerCase(), search = kids.length >= 12;
    const shown = kids.filter(k => !q || k.name.toLowerCase().indexOf(q) >= 0 || k.code.toLowerCase().indexOf(q) >= 0);
    const note = p.err || (p.note === 'pc' ? 'Planning Center is not set up. Save its application ID and secret first.' : p.note === 'err' ? 'That did not work. Try again.' : '');
    return `<div class="kc-phone"><main class="kc-scroll kc-pg"><h1 id="room">Nursery · Ages 0 to 2</h1><p class="sub">Tap a child to page their parent. The screen shows only the code on their label.</p>
      <p class="note" id="note"${note ? '' : ' hidden'}>${note}</p>
      <section id="upBox"${pages.length ? '' : ' hidden'}><h2>Paged now</h2><div class="list" id="pages">${pages.map(x => { const st = STATE[p.status]; return `<div class="page"><span class="code">${x.code}</span><span class="state ${st[1]}">${st[0]}</span><button type="button" class="kc-btn sm" data-kc="pgArr:${x.id}">Parent arrived</button></div>`; }).join('')}</div></section>
      <h2>Checked in</h2><input type="search" id="find" placeholder="Find a name or code" autocomplete="off" value="${esc(p.q)}" data-kc-input="pgQ"${search ? '' : ' hidden'}>
      <div class="list" id="kids">${p.list === 'loading' ? '<div class="empty">Loading…</div>' : !shown.length ? `<div class="empty">${kids.length ? 'Nobody matches.' : 'Nobody is checked in to this room right now.'}</div>` :
        shown.map(k => `<div class="kid"><span class="name">${esc(k.name)}</span><span class="code">${k.code}</span>${paged[k.id] ? '<span class="paged">Paged</span>' : p.confirm === k.id ? `<button type="button" class="kc-btn sm" data-kc="pgCancel">Cancel</button><button type="button" class="kc-btn sm p" data-kc="pgGo:${k.id}">Page ${k.code}</button>` : `<button type="button" class="kc-btn sm" data-kc="pgAsk:${k.id}">Page parent</button>`}</div>`).join('')}</div></main></div>`;
  }

  // ---------------------------------------------------------------- render + events
  function render() {
    const root = document.getElementById('kcRoot'); if (!root) return;
    const sc = root.querySelector('.kc-scroll'), y = sc ? sc.scrollTop : 0, tab = K.tab;
    const main = { crew, join, page: pagePage, offline, paging }[K.kind]();
    root.innerHTML = main + (K.kind === 'offline' ? '' : demo());
    const s = root.querySelector('.kc-scroll');
    if (s) s.scrollTop = K._top ? 0 : (K.kind === 'crew' && tab === 'chat' && K.stick ? s.scrollHeight : y);
    K._top = 0; K.stick = 0;
  }
  const clockNow = () => new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  const ACTS = {
    tab: a => { K.tab = a; if (a === 'chat') { K.unread = 0; K.stick = 1; } K._top = a !== 'chat'; render(); },
    set: a => { const [p, v] = a.split('='); setp(p, v); if (p === 'ic.link' || p === 'ic.chans' || p === 'ic.outcome' || p === 'ic.audio' || p === 'ic.notice') { K.ic.results = {}; K.ic.pressed = false; K.ic.cut = ''; } render(); },
    demo: () => { K.demo = !K.demo; render(); },
    toast: a => toast(a),
    checkin: () => { K.checkin = 'finding'; render(); later(() => { K.checkin = 'arrived'; render(); }, 900); },
    push: () => { K.push = K.push === 'on' ? 'off' : 'on'; render(); },
    chatSend: () => {
      const t = document.getElementById('chatText'), v = t.value.trim(); if (!v) return;
      K.msgs[K.chan] = (K.msgs[K.chan] || []).concat([{ id: Date.now(), a: ME, r: 'Camera', t: clockNow(), b: v, mine: 1 }]); K.data = 'full'; K.stick = 1; render();
    },
    chatDel: a => { K.msgs[K.chan] = K.msgs[K.chan].filter(m => String(m.id) !== a); render(); },
    clEdit: a => { const t = K.tpls.find(x => String(x.id) === a), l = K.lists.find(x => x.title === t.title); K.clForm = { title: t.title, pos: t.position, items: l ? l.items.map(i => i.text).join('\n') : 'Item one\nItem two', edit: t.id }; render(); },
    clDel: a => { const t = K.tpls.find(x => String(x.id) === a); if (confirm('Delete the checklist “' + t.title + '”?')) { K.tpls = K.tpls.filter(x => x !== t); K.lists = K.lists.filter(l => l.title !== t.title); render(); } },
    clSave: () => {
      const title = document.getElementById('clTitle').value.trim(), pos = document.getElementById('clPos').value.trim(), items = document.getElementById('clItems').value.split('\n').map(s => s.trim()).filter(Boolean);
      if (!title || !items.length) return toast('Give the checklist a title and at least one item.');
      const f = K.clForm;
      if (f.edit !== null) { const t = K.tpls.find(x => x.id === f.edit); t.title = title; t.position = pos; t.n = items.length; } else K.tpls.push({ id: Date.now(), title, position: pos, n: items.length });
      if (!pos || pos === 'Camera 2') { const l = K.lists.find(x => x.title === title); if (l) l.items = items.map(t => ({ text: t, done: false })); else K.lists.push({ title, position: pos, items: items.map(t => ({ text: t, done: false })) }); }
      K.clForm = { title: '', pos: '', items: '', edit: null }; toast('Checklist saved'); render();
    },
    clCancel: () => { K.clForm = { title: '', pos: '', items: '', edit: null }; render(); },
    rsShow: a => { K.rsOpen[a] = !K.rsOpen[a]; render(); },
    rsEdit: a => { const r = K.res.find(x => String(x.id) === a); K.rsForm = { kind: r.kind, title: r.title, pos: r.position, url: r.url || '', edit: r.id }; render(); },
    rsDel: a => { const r = K.res.find(x => String(x.id) === a); if (confirm('Delete “' + r.title + '”?')) { K.res = K.res.filter(x => x !== r); render(); } },
    rsSave: () => {
      const f = K.rsForm, title = document.getElementById('rsTitle').value.trim(), pos = document.getElementById('rsPos').value.trim(), u = document.getElementById('rsUrl');
      if (!title) return toast('Give it a title.');
      if (f.edit === null && f.kind === 'file' && !document.getElementById('rsFile').files[0]) return toast('Choose a file first.');
      if (f.edit !== null) { const r = K.res.find(x => x.id === f.edit); r.title = title; r.position = pos; if (u) r.url = u.value; }
      else K.res.push(f.kind === 'file' ? { id: Date.now(), kind: 'file', title, position: pos, ext: 'pdf', size: '120 KB' } : { id: Date.now(), kind: f.kind, title, position: pos, url: u ? u.value : '' });
      K.rsForm = { kind: 'file', title: '', pos: '', url: '', edit: null }; toast('Resource added'); render();
    },
    rsCancel: () => { K.rsForm = { kind: 'file', title: '', pos: '', url: '', edit: null }; render(); },
    acMore: () => { K.acShown += 8; render(); },
    icSel: a => { const [id, k] = a.split(':'), c = K.ic.chs.find(x => String(x.id) === id); c[k] = c[k] ? 0 : 1; render(); },
    icMute: () => { K.ic.muteAll = !K.ic.muteAll; icRefresh(); },
    pgAsk: a => { K.pg.confirm = a; K.pg.err = ''; render(); },
    pgCancel: () => { K.pg.confirm = ''; render(); },
    pgGo: a => { K.pg.arr.push({ id: 'n' + a, code: KIDS.find(k => k.id === a).code, kid: a }); K.pg.confirm = ''; render(); },
    pgArr: a => { K.pg.gone = (K.pg.gone || []).concat([a]); render(); },
    pageOk: () => { K.page.state = 'done'; render(); }
  };
  const CHG = {
    chan: (a, el) => { K.chan = el.value; render(); },
    chatPush: (a, el) => { K.chatPush[K.chan] = el.checked; toast(el.checked ? 'Quiet notifications on for this channel' : 'Quiet notifications off'); },
    cl: (a, el) => { const [i, j] = a.split(':'); K.lists[+i].items[+j].done = el.checked; render(); },
    rsKind: (a, el) => { K.rsForm.kind = el.value; render(); },
    acWho: (a, el) => { K.acF.who = el.value; K.acShown = 8; render(); }, acKind: (a, el) => { K.acF.kind = el.value; K.acShown = 8; render(); }, acDays: (a, el) => { K.acF.days = el.value; K.acShown = 8; render(); },
    icVol: (a, el) => { K.ic.chs.find(c => String(c.id) === a).volume = +el.value; },
    icKeep: (a, el) => { K.ic.keep = el.checked; }
  };
  function bind() {
    if (bound) return; bound = true;
    const inRoot = e => e.target.closest && e.target.closest('#kcRoot') && K;
    document.addEventListener('click', e => {
      if (!inRoot(e)) return;
      const t = e.target.closest('[data-kc]'); if (!t) return;
      const k = t.dataset.kc, i = k.indexOf(':'), a = i < 0 ? k : k.slice(0, i), arg = i < 0 ? '' : k.slice(i + 1);
      if (t.tagName === 'A') e.preventDefault();
      if (ACTS[a]) ACTS[a](arg, t, e);
    });
    document.addEventListener('change', e => {
      if (!inRoot(e)) return;
      const k = e.target.dataset && e.target.dataset.kcChange; if (!k) return;
      const i = k.indexOf(':'), a = i < 0 ? k : k.slice(0, i); CHG[a](i < 0 ? '' : k.slice(i + 1), e.target);
    });
    document.addEventListener('input', e => {
      if (!inRoot(e) || !e.target.dataset || e.target.dataset.kcInput !== 'pgQ') return;
      K.pg.q = e.target.value; const pos = e.target.selectionStart; render(); const f = document.getElementById('find'); if (f) { f.focus(); f.setSelectionRange(pos, pos); }
    });
    document.addEventListener('pointerdown', e => { if (inRoot(e) && e.target.closest('#icTalk')) { e.preventDefault(); press(false); } });
    ['pointerup', 'pointercancel'].forEach(n => document.addEventListener(n, () => { if (K) release(); }));
    document.addEventListener('contextmenu', e => { if (inRoot(e) && e.target.closest('#icTalk')) e.preventDefault(); });
    document.addEventListener('click', e => { if (inRoot(e) && e.target.closest('#icLatch') && K.ic) { if (K.ic.latched) release(); else press(true); } });
    document.addEventListener('keydown', e => {
      if (!K || !document.getElementById('kcRoot')) return;
      if (e.target.id === 'chatText' && e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); ACTS.chatSend(); return; }
      if (e.code === 'Space' && !e.repeat && K.kind === 'crew' && K.tab === 'intercom' && !/^(INPUT|TEXTAREA|SELECT|BUTTON|SUMMARY)$/.test((document.activeElement || {}).tagName)) { e.preventDefault(); press(false); }
    });
    document.addEventListener('keyup', e => { if (K && e.code === 'Space' && K.kind === 'crew' && K.ic.pressed && !K.ic.latched) release(); });
  }
  function mount(kind, tab, o) {
    timers.forEach(clearTimeout); timers = [];
    K = init(kind, tab, o); K.stick = tab === 'chat'; bind(); ticker(); render();
  }
  const root = '<div class="kc" id="kcRoot"></div>';
  const def = (id, t, desc, path, kind, tab, o) => {
    reg({ kind: 'full', id, t, cat: CAT, desc, path, ic: 'user' });
    FULL[id] = () => root; AFTER[id] = () => mount(kind, tab, o);
  };

  // ---------------------------------------------------------------- registrations
  def('s-crew-home', 'Crew app: Today', 'The crew web app home on a phone: call time and position while the booth is off, the plan, check in, page alerts and the tools you may open. Tabs at the bottom.', '/crew', 'crew', 'today');
  def('s-crew-chat', 'Crew app: Chat', 'Team and role channels, quiet notifications and a message box.', '/crew#chat', 'crew', 'chat', { unread: 0 });
  def('s-crew-checklists', 'Crew app: Checklists', 'Tick your position\'s items; team leads and operators also write the checklists.', '/crew#checklists', 'crew', 'checklists', { role: 'lead' });
  def('s-crew-resources', 'Crew app: Resources', 'Files, links and embedded pages for your position; leads and operators add them.', '/crew#resources', 'crew', 'resources', { role: 'lead' });
  def('s-crew-activity', 'Crew app: Activity', 'Operator-only log of who did what, filtered by person, kind and time.', '/crew#activity', 'crew', 'activity', { role: 'operator' });
  def('s-crew-intercom', 'Crew app: Intercom', 'Talk and listen on your channels: hold to talk, latch, mute all, connection and microphone states.', '/crew#intercom', 'crew', 'intercom');
  def('s-crew-join', 'Join the crew app', 'Plain-HTTP page the QR code opens: install the certificate, then open the crew page.', '/crew-join', 'join', '');
  def('s-crew-page', 'Page confirmation', 'The link in a page notification: read the message, confirm and stop the buzzing.', '/crew/notification/{token}', 'page', '');
  def('s-crew-offline', 'Crew app offline', 'What the service worker shows when the phone cannot reach Relay.', '/crew/_/offline', 'offline', '');
  def('s-paging-room', 'Parent paging room', 'A kids room\'s page: tap a checked-in child to page their parent; no sign-in, the link is the key.', '/paging/{secret}', 'paging', '');
})();
