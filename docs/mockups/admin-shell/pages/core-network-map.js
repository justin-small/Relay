/* Core page: Network map (issue 358). A core page that modules feed: Relay's own ports and discovery are always
   there, and each module that is on (UniFi, ATEM, ProPresenter, OBS...) adds what it knows. With UniFi off the map
   still works, without switches and ports. The UniFi module's own pages (Internet health, Settings) are listed as
   coming soon. Spec: docs/redesign/screens/core-network-map.md. */
(function(){

Object.assign(I,{
  mod_unifi:'<circle cx="8" cy="3" r="1.6"/><circle cx="3" cy="12.5" r="1.6"/><circle cx="13" cy="12.5" r="1.6"/><circle cx="8" cy="12.5" r="1.6"/><path d="M8 4.6v6.3M7 4.3l-3.2 6.8M9 4.3l3.2 6.8"/>',
  nm_gw:'<rect x="1.5" y="5" width="13" height="6" rx="1.5"/><path d="M4 8h.01M6.5 8h.01M10 8h2.5"/>',
  nm_sw:'<rect x="1.5" y="5" width="13" height="6" rx="1"/><path d="M4 9.5v-1M6 9.5v-1M8 9.5v-1M10 9.5v-1M12 9.5v-1"/>',
  nm_ap:'<circle cx="8" cy="8" r="5.5"/><circle cx="8" cy="8" r="1.4"/>',
  nm_cam:'<rect x="1.5" y="4.5" width="9" height="7" rx="1.2"/><path d="M10.5 7l4-2v6l-4-2"/>',
  nm_aud:'<path d="M2 6v4M5 4v8M8 2v12M11 5v6M14 7v2"/>',
  nm_pc:'<rect x="1.5" y="2.5" width="13" height="8.5" rx="1.2"/><path d="M5.5 14h5M8 11v3"/>'
});

injectCSS('modules-network',`
.nm{--nm-ndi:var(--info);--nm-dante:color-mix(in srgb,var(--info) 45%,var(--err))}
.nm-status{display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:12px 16px;border:1px solid var(--border);border-radius:var(--r);background:var(--card);box-shadow:var(--shadow)}
.nm-status .nm-t{flex:1;min-width:220px;line-height:1.35}.nm-status .nm-t b{font-weight:600}.nm-status .nm-t small{display:block;color:var(--muted);font-size:12px}
.nm-stack{display:flex;flex-direction:column;gap:14px}.nm .grid>*{min-width:0}
.nm-note{border:1px solid var(--border);border-left:3px solid var(--muted);border-radius:calc(var(--r) - 2px);padding:8px 12px;font-size:13px;color:var(--muted);line-height:1.5}
.nm-note.warn{border-left-color:var(--warn);color:var(--fg)}.nm-note.err{border-left-color:var(--err);color:var(--fg)}
.nm-demo{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:14px}.nm-demo .tabs{margin:0}.nm-demo small{color:var(--muted);font-size:12px}
.nm-m{font:12px ui-monospace,SFMono-Regular,Menlo,monospace}
.nm-muted{color:var(--muted)}.nm-ok{color:var(--ok)}.nm-warn{color:var(--warn)}.nm-err{color:var(--err)}
.nm-hd{display:flex;gap:10px;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;margin-bottom:12px}.nm-hd .d{margin:0!important}
.nm-bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.nm-bar input{width:200px}.nm-bar .tabs{margin:0}
.nm-tg{display:inline-flex}.nm-tg button{height:30px;padding:0 11px;font-size:12px;border:1px solid var(--border);margin-left:-1px;background:var(--bg);color:var(--fg);cursor:pointer;display:inline-flex;align-items:center;gap:6px}
.nm-tg button:first-child{margin-left:0;border-radius:calc(var(--r) - 2px) 0 0 calc(var(--r) - 2px)}.nm-tg button:last-child{border-radius:0 calc(var(--r) - 2px) calc(var(--r) - 2px) 0}
.nm-tg button[aria-pressed=true]{background:var(--hover);color:var(--hover-fg);font-weight:600;border-color:var(--primary);position:relative;z-index:1}
.nm-sw{width:8px;height:8px;border-radius:50%;display:inline-block;flex:none}
.nm-tag{display:inline-flex;align-items:center;gap:5px;border-radius:99px;padding:1px 8px;font-size:11px;font-weight:600;border:1px solid}
.nm-tag.ndi{color:var(--nm-ndi);border-color:color-mix(in srgb,var(--nm-ndi) 40%,transparent)}
.nm-tag.dante{color:var(--nm-dante);border-color:color-mix(in srgb,var(--nm-dante) 40%,transparent)}
.nm-tag.ptp{color:var(--fg);border-color:var(--border)}
.nm-map{position:relative;overflow:auto;border:1px solid var(--border);border-radius:calc(var(--r) - 2px);background:color-mix(in srgb,var(--fg) 2%,var(--bg))}
.nm-map svg{display:block;width:100%;height:auto}
.nm-map .lk.dimk{opacity:.2}
.nm-map .lk{fill:none;stroke:color-mix(in srgb,var(--fg) 25%,transparent)}
.nm-map .lk.hot{stroke:var(--warn)}.nm-map .lk.slow{stroke:var(--err);stroke-dasharray:4 4}
.nm-map .lk.ndi{stroke:var(--nm-ndi)}.nm-map .lk.dante{stroke:var(--nm-dante)}
.nm-map .ll{font:10.5px ui-monospace,SFMono-Regular,Menlo,monospace;fill:var(--muted)}
.nm-map .ll.hot{fill:var(--warn)}.nm-map .ll.slow{fill:var(--err)}
.nm-map .nd{cursor:pointer}.nm-map .nd rect.b{fill:var(--card);stroke:var(--border);transition:stroke .15s}
.nm-map .nd:hover rect.b{stroke:color-mix(in srgb,var(--primary) 60%,var(--border))}
.nm-map .nd.sel rect.b{stroke:var(--primary);stroke-width:2}
.nm-map .nd.off rect.b{stroke-dasharray:3 3}.nm-map .nd.off{opacity:.55}
.nm-map .nd.dim{opacity:.25}
.nm-map .nd text{fill:var(--fg);font-size:12px}.nm-map .nd text.s{fill:var(--muted);font-size:10.5px}
.nm-map .nd .ic{color:var(--muted)}.nm-map .nd.ndi .ic{color:var(--nm-ndi)}.nm-map .nd.dante .ic{color:var(--nm-dante)}
.nm-map .nd .bar{fill:var(--nm-ndi)}.nm-map .nd.dante .bar{fill:var(--nm-dante)}
.nm-map .nd .stat{fill:var(--ok)}.nm-map .nd.off .stat{fill:var(--err)}.nm-map .nd.warn .stat{fill:var(--warn)}
.nm-map.stale svg{filter:grayscale(1);opacity:.7}
.nm-legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--muted);margin-top:10px}.nm-legend span{display:inline-flex;gap:6px;align-items:center}
.nm-legend i{display:inline-block;width:18px;height:0;border-top:2px solid color-mix(in srgb,var(--fg) 25%,transparent)}
.nm-legend i.hot{border-color:var(--warn)}.nm-legend i.slow{border-top:2px dashed var(--err)}
.nm-kv{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:6px 16px;margin:0 0 12px}.nm-kv dt{color:var(--muted);font-size:13px}.nm-kv dd{margin:0;overflow-wrap:anywhere}
.nm-side h3{display:flex;align-items:center;gap:8px}
.nm-over{overflow-x:auto}.nm-over table{min-width:640px}.nm-over table.nm-ports{min-width:560px}.nm-ports td,.nm-ports th{padding-left:8px;padding-right:8px}
.nm-ports tr.cur td{background:var(--hover)}
.nm-ports td{white-space:nowrap}.nm-ports tr.warn td{background:color-mix(in srgb,var(--warn) 8%,transparent)}
.nm-ports tr.up td.nm-port{font-weight:600}
.nm-spark{display:inline-flex;align-items:flex-end;gap:2px;height:16px;vertical-align:middle;margin-right:6px}.nm-spark i{width:3px;background:var(--nm-ndi);border-radius:1px;opacity:.8}.nm-spark.dante i{background:var(--nm-dante)}.nm-spark.plain i{background:var(--muted)}
.nm-src{display:inline-flex;gap:4px;flex-wrap:wrap}.nm-src span{font-size:11px;border:1px solid var(--border);border-radius:4px;padding:0 5px;color:var(--muted)}.nm-src span.y{color:var(--fg);border-color:color-mix(in srgb,var(--ok) 45%,var(--border))}.nm-src span.n{text-decoration:line-through;opacity:.6}
.nm-srcrow{display:flex;gap:10px;align-items:flex-start;justify-content:space-between;padding:8px 0;border-top:1px solid color-mix(in srgb,var(--border) 70%,transparent)}.nm-srcrow:first-of-type{border-top:0}.nm-srcrow small{display:block;color:var(--muted);font-size:12px;line-height:1.4}.nm-srcrow .badge{flex:none}
.nm-poe{display:flex;justify-content:space-between;font-size:12px;color:var(--muted)}
@media(max-width:900px){.nm-bar input{width:100%}.nm-kv{grid-template-columns:1fr}.nm-kv dt{margin-top:6px}}
`);

/* ---------- local helpers ---------- */
const S={state:'ok',view:'map',filter:'all',sel:'sw-sanct',q:''};
const B=(label,msg,cls)=>`<button type="button" class="btn ${cls||''}" onclick="toast('${msg}')">${label}</button>`;
const tag=t=>t=='ndi'?'<span class="nm-tag ndi">NDI</span>':t=='dante'?'<span class="nm-tag dante">Dante</span>':t=='ptp'?'<span class="nm-tag ptp">PTP leader</span>':'';
const spark=(seed,kind)=>`<span class="nm-spark ${kind||'plain'}">${Array.from({length:12},(_,i)=>`<i style="height:${25+Math.abs(Math.sin(seed*1.7+i*.9))*75}%"></i>`).join('')}</span>`;
const sic=(n,x,y,s)=>`<svg x="${x}" y="${y}" width="${s||16}" height="${s||16}" viewBox="0 0 16 16" class="ic"><g fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${I[n]}</g></svg>`;

/* ---------- sample network: a church with a server-room core, a sanctuary AV switch, a booth switch, three APs ---------- */
const N=[
  {id:'gw',kind:'gw',name:'Gateway',model:'UniFi Cloud Gateway Ultra',ip:'192.168.1.1',fw:'4.1.13',parent:null},
  {id:'sw-core',kind:'sw',name:'Core · Server room',model:'USW Pro 24 PoE',ip:'192.168.1.2',fw:'7.1.26',parent:'gw',link:{sp:'1G',use:22,port:'Port 1 → WAN 2'},poe:[118,400]},
  {id:'sw-sanct',kind:'sw',name:'Sanctuary AV',model:'USW Pro 24 PoE',ip:'192.168.1.3',fw:'7.1.26',parent:'sw-core',link:{sp:'10G',use:31,port:'SFP+ 25 → SFP+ 25'},poe:[262,400]},
  {id:'sw-booth',kind:'sw',name:'Booth',model:'USW Flex',ip:'192.168.1.4',fw:'2.1.6',parent:'sw-core',link:{sp:'1G',use:71,hot:1,port:'Port 1 → Port 18'},poe:[31,46]},
  {id:'ap-sanct',kind:'ap',name:'Sanctuary AP',model:'U6 Pro',ip:'192.168.1.21',fw:'6.6.77',parent:'sw-core',link:{sp:'1G',use:9,port:'Port 1 → Port 9'},clients:61},
  {id:'ap-youth',kind:'ap',name:'Youth AP',model:'U6 Lite',ip:'192.168.1.22',fw:'6.6.77',parent:'sw-core',link:{sp:'100M',use:44,slow:1,port:'Port 1 → Port 10'},clients:28},
  {id:'ap-lobby',kind:'ap',name:'Lobby AP',model:'U6 Lite',ip:'192.168.1.23',fw:'6.6.77',parent:'sw-core',link:{sp:'1G',use:6,port:'Port 1 → Port 11'},clients:22},
  /* clients on the Sanctuary AV switch */
  {leaf:1,id:'c-cam1',by:['unifi','disc'],kind:'cam',name:'PTZ Cam 1 Wide',parent:'sw-sanct',port:3,t:'ndi',mbps:132,ip:'192.168.20.11',vlan:'AV (20)',mac:'d8:3a:dd:41:0c:11',src:['mdns','ports','flows'],link:{sp:'1G'}},
  {leaf:1,id:'c-cam2',by:['unifi','disc'],kind:'cam',name:'PTZ Cam 2 Pulpit',parent:'sw-sanct',port:4,t:'ndi',mbps:128,ip:'192.168.20.12',vlan:'AV (20)',mac:'d8:3a:dd:41:0c:12',src:['mdns','ports','flows'],link:{sp:'1G'}},
  {leaf:1,id:'c-cam3',by:['unifi','disc'],kind:'cam',name:'PTZ Cam 3 Choir',parent:'sw-sanct',port:5,t:'ndi',mbps:0,off:1,ip:'192.168.20.13',vlan:'AV (20)',mac:'d8:3a:dd:41:0c:13',src:['mdns'],link:{sp:'—'}},
  {leaf:1,id:'c-stage',by:['unifi','disc'],kind:'aud',name:'Stagebox Rio3224',parent:'sw-sanct',port:9,t:'dante',ptp:1,mbps:38,ip:'192.168.30.21',vlan:'Audio (30)',mac:'00:1d:c1:0a:44:21',src:['mdns','ports','flows'],link:{sp:'1G'}},
  {leaf:1,id:'c-cl5',by:['unifi','disc'],kind:'aud',name:'Yamaha CL5',parent:'sw-sanct',port:10,t:'dante',mbps:41,ip:'192.168.30.22',vlan:'Audio (30)',mac:'00:a0:de:9b:12:22',src:['mdns','ports','flows'],link:{sp:'1G'}},
  {leaf:1,id:'c-avio',by:['unifi','disc'],kind:'aud',name:'Dante AVIO · Pastor mic',parent:'sw-sanct',port:11,t:'dante',mbps:3,ip:'192.168.30.31',vlan:'Audio (30)',mac:'00:1d:c1:77:0e:31',src:['mdns','ports'],link:{sp:'100M',slow:1},warn:'Negotiated at 100 Mbps. Fine for a 2-channel AVIO; check the cable if it was 1 Gbps before.'},
  {leaf:1,id:'c-pp',by:['unifi','disc','propresenter'],kind:'pc',name:'ProPresenter Mac',parent:'sw-sanct',port:12,t:'ndi',mbps:96,ip:'192.168.20.40',vlan:'AV (20)',mac:'3c:22:fb:5e:90:40',src:['mdns','ports','flows'],link:{sp:'1G'}},
  /* clients on the Booth switch */
  {leaf:1,id:'c-atem',by:['unifi','atem'],kind:'pc',name:'ATEM Mini Extreme ISO',parent:'sw-booth',port:2,mbps:12,ip:'192.168.20.240',vlan:'AV (20)',mac:'7c:2e:0d:11:a0:f0',src:[],link:{sp:'1G'}},
  {leaf:1,id:'c-obs',by:['unifi','disc','obs'],kind:'pc',name:'Stream PC (OBS)',parent:'sw-booth',port:3,t:'ndi',mbps:356,ip:'192.168.20.50',vlan:'AV (20)',mac:'a8:a1:59:c3:20:50',src:['mdns','ports','flows'],rx:1,link:{sp:'1G'}},
  {leaf:1,id:'c-relay',by:['core','unifi','disc'],kind:'pc',name:'Relay server',parent:'sw-booth',port:4,t:'dante',mbps:6,ip:'192.168.20.24',vlan:'AV (20)',mac:'f4:d4:88:6a:10:24',src:['mdns','ports'],link:{sp:'1G'},note:'Dante Virtual Soundcard: mDNS and ports agree; no flow data for this port yet.'}
];
/* the same network with UniFi switched off: Relay's own ports, and what discovery and the other modules found on them */
const N2=[
  {id:'relay',kind:'pc',name:'Relay server',model:'This computer',ip:'2 network ports',parent:null},
  {id:'p-video',kind:'port',name:'Ethernet (Video network)',model:'en5 · 192.168.20.5',ip:'192.168.20.5',parent:'relay',link:{sp:'1G',use:18,port:'en5'}},
  {id:'p-audio',kind:'port',name:'Ethernet (Audio network)',model:'en6 · 192.168.30.5',ip:'192.168.30.5',parent:'relay',link:{sp:'1G',use:4,port:'en6'}}
].concat(N.filter(n=>n.leaf&&n.id!='c-relay').map(n=>Object.assign({},n,{parent:n.vlan.startsWith('Audio')?'p-audio':'p-video',port:null,mbps:null,link:{sp:'?'},by:n.by.filter(b=>b!='unifi'),src:n.src.filter(x=>x!='flows'),warn:null})).filter(n=>n.by.length));
const D=()=>S.state=='nounifi'?N2:N;
const byId=id=>D().find(n=>n.id==id);
const kids=id=>D().filter(n=>n.parent==id);
const SRC={core:'Relay',disc:'Discovery (mDNS)',unifi:'UniFi',atem:'ATEM',propresenter:'ProPresenter',obs:'OBS Studio'};
const isAV=n=>!!n.t;
const matches=n=>{
  if(S.q){const q=S.q.toLowerCase();if(![n.name,n.ip,n.mac,n.model].some(v=>v&&v.toLowerCase().includes(q)))return false}
  if(S.filter=='all')return true; if(!n.leaf)return true;
  if(S.filter=='av')return isAV(n); return n.t==S.filter;
};
const fmt=m=>m==null?'—':m>=1000?(m/1000).toFixed(2)+' Gb/s':m+' Mb/s';

/* ---------- topology: gateway, core, then each switch/AP with its wired clients in a block of up to 3 columns ---------- */
function topo(){
  const NW=150,NH=46,CW=124,CH=50,GX=10,GY=16,COLS=3;
  const uni=S.state!='nounifi', tops=uni?['gw','sw-core']:['relay'], hub=tops[tops.length-1];
  const Y=tops.map((_,i)=>20+i*88).concat([20+tops.length*88+10]); Y.push(Y[Y.length-1]+94);
  const L2=Y.length-2, L3=Y.length-1;
  const pos={},block={}; let x=20;
  const mid=kids(hub);
  const cols=n=>Math.min(COLS,kids(n.id).length);
  const slot=n=>Math.max(NW,cols(n)*(CW+GX)-GX);
  mid.forEach(n=>{
    const w=slot(n),c=kids(n.id);
    pos[n.id]=[x+w/2-NW/2,Y[L2]]; block[n.id]=x;
    c.forEach((k,i)=>pos[k.id]=[x+(i%COLS)*(CW+GX),Y[L3]+Math.floor(i/COLS)*(CH+GY+8)]);
    x+=w+28;
  });
  const W=x-8;
  tops.forEach((t,i)=>pos[t]=[W/2-NW/2,Y[i]]);
  const rows=Math.max(...mid.map(n=>Math.ceil(kids(n.id).length/COLS)),1);
  const H=Y[L3]+rows*(CH+GY+8)+8;
  let links='',nodes='';
  D().forEach(n=>{
    if(!pos[n.id]||!n.parent)return; const p=pos[n.parent]; if(!p)return;
    const leaf=!!n.leaf, w=leaf?CW:NW;
    const x1=p[0]+NW/2,y1=p[1]+NH,x2=pos[n.id][0]+w/2,y2=pos[n.id][1];
    const L=n.link||{}; const flow=leaf&&n.t&&!n.off?' '+n.t:'';
    const cls=(L.slow?' slow':L.hot?' hot':'')+(L.slow||L.hot?'':flow)+(leaf&&!matches(n)?' dimk':'');
    const sw=L.sp=='10G'?3.5:leaf&&n.mbps>100?2.2:1.5;
    let d;
    if(leaf){const b0=Y[L3]-12,by=y2-12,xl=block[n.parent]-7;
      d=by==b0?`M${x1} ${y1}V${b0}H${x2}V${y2}`:`M${x1} ${y1}V${b0}H${xl}V${by}H${x2}V${y2}`}
    else{const my=(y1+y2)/2;d=`M${x1} ${y1}C${x1} ${my} ${x2} ${my} ${x2} ${y2}`}
    links+=`<path class="lk${cls}" stroke-width="${sw}" d="${d}"/>`;
    if(!leaf) links+=`<text class="ll${L.slow?' slow':L.hot?' hot':''}" x="${x2}" y="${y2-7}" text-anchor="middle">${L.sp} · ${L.use}%</text>`;
  });
  D().forEach(n=>{
    if(!pos[n.id])return; const [x,y]=pos[n.id], leaf=!!n.leaf, w=leaf?CW:NW, h=leaf?CH:NH;
    const dim=leaf&&!matches(n);
    const icn=n.kind=='gw'?'nm_gw':n.kind=='sw'||n.kind=='port'?'nm_sw':n.kind=='ap'?'nm_ap':'nm_'+n.kind;
    const cls=['nd',n.t||'',n.off?'off':'',n.warn?'warn':'',S.sel==n.id?'sel':'',dim?'dim':''].join(' ');
    const sub=leaf?(n.port==null?(n.off?'offline · ':'')+n.ip:n.off?'offline · port '+n.port:'port '+n.port+' · '+fmt(n.mbps)):n.kind=='ap'?n.clients+' clients · '+n.model:n.model;
    const nm=n.name.length>(leaf?15:20)?n.name.slice(0,leaf?14:19)+'…':n.name;
    const bar=leaf&&n.t&&!n.off&&n.mbps!=null?`<rect class="bar" x="${x+8}" y="${y+h-6}" width="${Math.max(4,Math.min(w-16,(w-16)*n.mbps/400))}" height="3" rx="1.5"/>`:'';
    nodes+=`<g class="${cls}" data-node="${n.id}" tabindex="0" role="button" aria-label="${n.name}"><rect class="b" x="${x}" y="${y}" width="${w}" height="${h}" rx="8"/>${sic(icn,x+9,y+(leaf?9:15))}<circle class="stat" cx="${x+w-10}" cy="${y+10}" r="3.5"/><text x="${x+30}" y="${y+(leaf?20:21)}">${nm}</text><text class="s" x="${leaf?x+30:x+30}" y="${y+(leaf?35:36)}">${sub}</text>${bar}</g>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" style="min-width:${Math.round(W*.72)}px" role="img" aria-label="Network topology">${links}${nodes}</svg>`;
}

/* ---------- table view: every device and client in one list ---------- */
function table(){
  const rows=D().filter(n=>n.leaf&&matches(n)).map(n=>{
    const sw=byId(n.parent);
    return `<tr data-node="${n.id}" style="cursor:pointer"${S.sel==n.id?' data-sel="true"':''}><td class="two"><b>${n.name}</b><small class="nm-m">${n.mac}</small></td><td>${tag(n.t)} ${n.ptp?tag('ptp'):''}</td><td>${n.port==null?sw.name:sw.name+' · port '+n.port}</td><td class="nm-m">${n.ip}</td><td>${n.vlan}</td><td class="${n.link.slow?'nm-err':''}">${n.link.sp}</td><td>${n.off?'<span class="nm-err">Offline</span>':n.mbps==null?'<span class="nm-muted">Needs a switch source</span>':spark(n.port,n.t)+fmt(n.mbps)}</td></tr>`;
  }).join('');
  return `<div class="nm-over"><table class="dt"><thead><tr><th>Device</th><th>Carries</th><th>${S.state=='nounifi'?'Relay port':'Switch · port'}</th><th>Address</th><th>VLAN</th><th>Link</th><th>Traffic now</th></tr></thead><tbody>${rows||'<tr><td colspan="7" class="nm-muted">Nothing matches that filter.</td></tr>'}</tbody></table></div>`;
}

/* ---------- side card: the selected device ---------- */
function side(){
  const n=byId(S.sel)||byId('sw-sanct');
  const srcs=['mdns','ports','flows'], lbl={mdns:'mDNS',ports:'Port numbers',flows:'Traffic flows'};
  const flowsOff=S.state=='noflows';
  if(n.leaf){
    const sw=byId(n.parent); const seen=`<div class="nm-src">${n.by.map(b=>`<span class="y">${SRC[b]}</span>`).join('')}</div>`;
    const found=n.t?`<div class="nm-src">${srcs.map(s=>{const y=n.src.includes(s)&&!(s=='flows'&&flowsOff);return `<span class="${y?'y':s=='flows'&&flowsOff?'n':''}">${lbl[s]}</span>`}).join('')}</div>`:'<span class="nm-muted">Not NDI or Dante</span>';
    return `<section class="card nm-side"><h3>${ic(n.kind=='cam'?'nm_cam':n.kind=='aud'?'nm_aud':'nm_pc')}${n.name}</h3><p class="d">${n.off?'Last seen 14 minutes ago.':'Online for 2 h 41 min.'}</p>
      <dl class="nm-kv"><dt>Carries</dt><dd>${n.t?tag(n.t)+' '+(n.ptp?tag('ptp'):''):'—'}</dd><dt>${n.port==null?'Reached on':'Plugged into'}</dt><dd>${n.port==null?sw.name:sw.name+', port '+n.port}</dd><dt>Link</dt><dd class="${n.link.slow?'nm-err':''}">${n.link.sp=='?'?'<span class="nm-muted">Unknown without a switch source</span>':n.link.sp=='—'?'Down':({'1G':'1 Gbps','100M':'100 Mbps','10G':'10 Gbps'}[n.link.sp])+' full duplex'}</dd><dt>Address</dt><dd class="nm-m">${n.ip}</dd><dt>MAC</dt><dd class="nm-m">${n.mac}</dd><dt>VLAN</dt><dd>${n.vlan}</dd><dt>Seen by</dt><dd>${seen}</dd><dt>Identified by</dt><dd>${found}</dd></dl>
      ${n.off||n.mbps==null?'':`<div class="nm-poe"><span>${n.rx?'Receiving':'Sending'} now</span><b style="color:var(--fg)">${fmt(n.mbps)}</b></div><div style="margin:6px 0 12px">${spark(n.port+3,n.t).replace('height:16px','height:40px').replace(/width:3px/g,'width:6px')}</div>`}
      ${n.warn?`<div class="nm-note warn" style="margin-bottom:10px">${n.warn}</div>`:''}${n.note?`<div class="nm-note" style="margin-bottom:10px">${n.note}</div>`:''}
      ${n.off&&n.port?'<div class="nm-note err" style="margin-bottom:10px">Was on Sanctuary AV port 5 and announced an NDI source over mDNS. The port shows no link now: check its power over Ethernet and cable.</div>':''}
      ${n.port==null?'':`<div class="row">${B('Show its port','Showing port '+n.port+' on '+sw.name,'sm')}</div>`}</section>`;
  }
  const ks=kids(n.id), av=ks.filter(isAV).length;
  return `<section class="card nm-side"><h3>${ic(n.kind=='gw'?'nm_gw':n.kind=='ap'?'nm_ap':n.kind=='pc'?'nm_pc':'nm_sw')}${n.name}</h3><p class="d">${n.model}${n.fw?' · firmware '+n.fw:''}</p>
    <dl class="nm-kv"><dt>Address</dt><dd class="nm-m">${n.ip}</dd>${n.link?`<dt>Uplink</dt><dd class="${n.link.slow?'nm-err':n.link.hot?'nm-warn':''}">${n.link.port} · ${n.link.sp} · ${n.link.use}% used</dd>`:(n.kind=='gw'?'<dt>Internet</dt><dd>WAN 1 up · 940 / 41 Mb/s</dd>':'<dt>Network ports</dt><dd>en5 Video network · en6 Audio network</dd><dt>Found</dt><dd>'+kids('p-video').length+' devices on video, '+kids('p-audio').length+' on audio</dd>')}${n.kind=='ap'?`<dt>Clients</dt><dd>${n.clients} on Wi-Fi</dd><dt>Channels</dt><dd>2.4 GHz ch 6 · 5 GHz ch 44 (80 MHz)</dd>`:''}${n.kind=='port'?`<dt>Devices found</dt><dd>${ks.length} · ${ks.filter(k=>k.t=='ndi').length} NDI · ${ks.filter(k=>k.t=='dante').length} Dante</dd>`:''}${n.kind=='sw'?`<dt>Ports in use</dt><dd>${n.id=='sw-sanct'?'14 of 24':n.id=='sw-booth'?'4 of 5':'11 of 24'}</dd><dt>NDI · Dante</dt><dd>${av?`${ks.filter(k=>k.t=='ndi').length} NDI · ${ks.filter(k=>k.t=='dante').length} Dante`:'—'}</dd>`:''}</dl>
    ${n.poe?`<div class="nm-poe"><span>PoE budget</span><span>${n.poe[0]} of ${n.poe[1]} W</span></div><div class="meter"><i style="width:${Math.round(100*n.poe[0]/n.poe[1])}%"></i></div>`:''}
    ${n.link&&n.link.slow?'<div class="nm-note err" style="margin-top:10px">The uplink negotiated at 100 Mbps. It is usually 1 Gbps: check the cable and the switch port.</div>':''}
    ${n.link&&n.link.hot?'<div class="nm-note warn" style="margin-top:10px">The uplink is 71% used, mostly NDI to the Stream PC. Another NDI camera to the booth would fill it.</div>':''}</section>`;
}

/* ---------- port table for the selected switch ---------- */
function ports(){
  if(S.state=='nounifi')return `<section class="card"><h3>Switch ports</h3><p class="d" style="margin:0 0 12px">Which switch port each device is on, link speeds, PoE and traffic come from a module that reads the switches. None is on.</p><div class="row"><a class="btn sm" href="#modules">Modules</a><span class="nm-muted" style="font-size:12px">Turn on UniFi to fill this in.</span></div></section>`;
  const n=byId(S.sel); const sw=n&&n.kind=='sw'?n:n&&n.port?byId(n.parent):byId('sw-sanct');
  if(sw.kind!='sw')return '';
  const ks=kids(sw.id);
  const count=sw.id=='sw-booth'?5:12;
  const up=sw.id=='sw-booth'?1:25;
  const rows=[];
  for(let p=1;p<=count;p++){
    const c=ks.find(k=>k.port==p);
    if(p==up&&sw.id=='sw-booth'){rows.push(`<tr class="up"><td class="nm-port">${p}</td><td>Uplink to Core · Server room</td><td>1G</td><td>—</td><td>${spark(p)}712 Mb/s</td><td>0</td><td></td></tr>`);continue}
    if(!c){rows.push(`<tr><td class="nm-port">${p}</td><td class="nm-muted">${[1,2,6,7,8].includes(p)&&sw.id=='sw-sanct'?['Choir monitor mix','Wireless mic rack','Hearing loop','Projector left','Projector right'][[1,2,6,7,8].indexOf(p)]:'Empty'}</td><td>${[1,2,6,7,8].includes(p)&&sw.id=='sw-sanct'?'1G':'—'}</td><td>—</td><td class="nm-muted">${[1,2,6,7,8].includes(p)&&sw.id=='sw-sanct'?'1 Mb/s':'—'}</td><td>0</td><td></td></tr>`);continue}
    rows.push(`<tr class="${S.sel==c.id?'cur':c.warn||c.off?'warn':''}"><td class="nm-port">${p}</td><td>${c.name}</td><td class="${c.link.slow?'nm-err':''}">${c.link.sp}</td><td>${c.kind=='cam'?(c.off?'0 W':'12.4 W'):c.kind=='aud'&&c.id=='c-avio'?'4.1 W':'—'}</td><td>${c.off?'<span class="nm-err">No link</span>':spark(p,c.t)+fmt(c.mbps)}</td><td class="${c.id=='c-avio'?'nm-warn':''}">${c.id=='c-avio'?'214':'0'}</td><td>${tag(c.t)}</td></tr>`);
  }
  if(sw.id=='sw-sanct')rows.push(`<tr class="up"><td class="nm-port">SFP+ 25</td><td>Uplink to Core · Server room</td><td>10G</td><td>—</td><td>${spark(25)}3.1 Gb/s</td><td>0</td><td></td></tr>`);
  return `<section class="card"><div class="nm-hd"><div><h3>Ports on ${sw.name}</h3><p class="d">Speed, power, traffic and errors for each port, from the controller's port table. Traffic is the last minute.</p></div>${sw.id=='sw-sanct'?'<span class="nm-muted" style="font-size:12px">Ports 13–24 empty</span>':''}</div>
    <div class="nm-over"><table class="dt nm-ports"><thead><tr><th>Port</th><th>Connected</th><th>Link</th><th>PoE</th><th>Traffic</th><th>Errors (24 h)</th><th>Carries</th></tr></thead><tbody>${rows.join('')}</tbody></table></div></section>`;
}

/* ---------- sources: Relay's own, then every module that adds to the map ---------- */
function sources(){
  const uni=S.state!='nounifi', down=S.state=='down';
  const rows=[
    ['Relay','core','This computer and its network ports.',['ok','Always on']],
    ['Discovery (mDNS)','core','NDI sources and Dante devices that announce themselves on Relay’s networks.',['ok','Always on']],
    ['UniFi','unifi','Switches, access points, which port each device is on, link speed, PoE and traffic.',uni?(down?['err','No answer']:['ok','On']):['','Off']],
    ['ATEM','atem','Names its switchers and shows them as video gear.',['ok','On']],
    ['ProPresenter','propresenter','Marks the machines it controls and their NDI output.',['ok','On']],
    ['OBS Studio','obs','Marks the stream machine and which NDI sources it receives.',['ok','On']]
  ];
  return `<section class="card"><h3>Sources</h3><p class="d">The map is put together from Relay and every module that is on. Turning a module off removes only what it added.</p>
    ${rows.map(r=>`<div class="nm-srcrow"><div><b>${r[0]}</b> ${r[1]=='core'?'<span class="nm-muted" style="font-size:11px">core</span>':''}<small>${r[2]}</small></div><span class="badge ${r[3][0]}">${r[3][1]}</span></div>`).join('')}
    <div style="margin-top:10px"><a class="btn sm" href="#modules">Modules</a></div></section>`;
}

/* ---------- the page ---------- */
function body(){
  const st=S.state, view=st=='view', down=st=='down', noflows=st=='noflows', nouni=st=='nounifi';
  const status=down
    ?`<div class="nm-status" role="status"><span class="badge sbadge warn"><i></i>Partial</span><div class="nm-t"><b>UniFi: the controller at 192.168.1.1 did not answer</b><small>Switches and ports are from 3 minutes ago. Discovery, ATEM, ProPresenter and OBS are live.</small></div><div class="row">${B('Refresh','UniFi still not answering; the rest refreshed','sm')}</div></div>`
    :nouni?`<div class="nm-status" role="status"><span class="badge sbadge ok"><i></i>Live</span><div class="nm-t"><b>9 devices found by Relay and 3 modules</b><small>Relay’s own discovery, ATEM, ProPresenter and OBS · updated 4 seconds ago. No module reads the switches, so ports and traffic are not shown.</small></div><div class="row">${B('Refresh','Map refreshed','sm')}</div></div>`
    :`<div class="nm-status" role="status"><span class="badge sbadge ok"><i></i>Live</span><div class="nm-t"><b>6 network devices and 142 clients from Relay and 4 modules</b><small>Discovery, UniFi, ATEM, ProPresenter, OBS · updated 8 seconds ago</small></div><div class="row">${B('Refresh','Map refreshed','sm')}</div></div>`;
  const stats=nouni
    ?`<div class="grid g4">${stat('Switches and APs','—','Needs a module that reads switches')}${stat('Devices found','9','On 2 of Relay’s network ports')}${stat('NDI sources','3 <span style="font-size:13px;font-weight:500;color:var(--muted)">of 4</span>','PTZ Cam 3 stopped announcing','dn')}${stat('Dante devices','3','PTP leader: Stagebox Rio3224')}</div>`
    :`<div class="grid g4">${stat('Switches and APs','6',down?'From UniFi, 3 minutes ago':'All online · firmware current','up')}${stat('Clients','142','31 wired · 111 on Wi-Fi')}${stat('NDI sources','3 <span style="font-size:13px;font-weight:500;color:var(--muted)">of 4</span>','PTZ Cam 3 is offline','dn')}${stat('Dante devices','4','PTP leader: Stagebox Rio3224')}</div>`;
  const notes=[
    view?'<div class="nm-note">You can view this page but not change it.</div>':'',
    noflows?'<div class="nm-note warn">The UniFi controller does not report traffic flows (firmware older than Network 8.4). NDI and Dante are found by mDNS and port numbers, and traffic is per port, not per stream.</div>':'',
    !down&&!nouni?'<div class="nm-note warn"><b>2 things to check:</b> Youth AP uplink is running at 100 Mbps · the Booth uplink is 71% used during the service.</div>':''
  ].join('');
  const filt=[['all','All'],['av','AV only'],['ndi','NDI'],['dante','Dante']];
  const map=`<section class="card"><div class="nm-hd"><div><h3>Network map</h3><p class="d">${nouni?'Grouped by the Relay network port each device was found on. ':''}Click a device for details. Line colour shows what the link carries${nouni?'':'; a thick line is 10 Gbps'}.</p></div>
    <div class="nm-bar"><div class="tabs" role="tablist" aria-label="View" id="nmView"><button type="button" data-v="map" class="${S.view=='map'?'on':''}">Map</button><button type="button" data-v="table" class="${S.view=='table'?'on':''}">Table</button></div>
    <div class="nm-tg" role="group" aria-label="Show" id="nmFilt">${filt.map(([v,l])=>`<button type="button" data-v="${v}" aria-pressed="${S.filter==v}">${v=='ndi'?'<span class="nm-sw" style="background:var(--nm-ndi)"></span>':v=='dante'?'<span class="nm-sw" style="background:var(--nm-dante)"></span>':''}${l}</button>`).join('')}</div>
    <input id="nmQ" placeholder="Find name, IP or MAC" value="${S.q.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;')}" aria-label="Find a device"></div></div>
    <div id="nmBody">${S.view=='map'?`<div class="nm-map${down?' stale':''}">${topo()}</div><div class="nm-legend"><span><i></i>Idle or light</span><span><i style="border-color:var(--nm-ndi)"></i>NDI</span><span><i style="border-color:var(--nm-dante)"></i>Dante</span>${nouni?'':'<span><i class="hot"></i>Over 70% used</span><span><i class="slow"></i>Slower than expected</span>'}</div>`:table()}</div></section>`;
  return `<div class="nm nm-stack">${status}${notes}${stats}${map}<div class="grid g2" style="align-items:start"><div class="nm-stack"><div id="nmPorts">${ports()}</div></div><div class="nm-stack"><div id="nmSide">${side()}</div>${sources()}</div></div></div>`;
}

const STATES=[['ok','UniFi on'],['noflows','UniFi, no flow data'],['down','UniFi not answering'],['nounifi','UniFi off'],['view','View only']];
function render(){const el=document.querySelector('[data-nm]');if(el){el.innerHTML=body();wire()}}
function wire(){
  const root=document.querySelector('[data-nm]'); if(!root)return;
  const pick=e=>{const g=e.target.closest('[data-node]');if(!g)return;S.sel=g.dataset.node;render()};
  root.querySelectorAll('[data-node]').forEach(g=>{g.addEventListener('click',pick);g.addEventListener('keydown',e=>{if(e.key=='Enter'||e.key==' '){e.preventDefault();pick(e)}})});
  const v=document.getElementById('nmView'); if(v)v.onclick=e=>{const b=e.target.closest('button');if(!b)return;S.view=b.dataset.v;render()};
  const f=document.getElementById('nmFilt'); if(f)f.onclick=e=>{const b=e.target.closest('button');if(!b)return;S.filter=b.dataset.v;render()};
  const q=document.getElementById('nmQ'); if(q)q.oninput=()=>{S.q=q.value;const b=document.getElementById('nmBody');
    b.innerHTML=S.view=='map'?b.innerHTML.replace(/<svg[\s\S]*<\/svg>/,topo()):table();
    b.querySelectorAll('[data-node]').forEach(g=>g.addEventListener('click',pick))};
}
P.network_map=()=>{S.state='ok';S.sel='sw-sanct';return `<h2 class="t">Network map</h2><p class="sub">Every device Relay can see on the church network and which carry NDI video or Dante audio, put together from Relay’s own discovery and the modules that are on.</p>
  <div class="nm-demo"><div class="tabs" role="tablist" aria-label="Mockup state" id="nmState">${STATES.map(([v,l])=>`<button type="button" data-v="${v}" class="${v==S.state?'on':''}">${l}</button>`).join('')}</div><small>Mockup state</small></div><div data-nm>${body()}</div>`};
AFTER.network_map=()=>{
  const t=document.getElementById('nmState'); if(t)t.onclick=e=>{const b=e.target.closest('button');if(!b)return;[...t.children].forEach(x=>x.classList.toggle('on',x===b));S.state=b.dataset.v;S.sel=S.state=='nounifi'?'relay':'sw-sanct';render()};
  wire();
};

reg({kind:'page',id:'network_map',t:'Network map',g:'Live',ic:'net',badge:'new',perm:'network.view'});
/* the UniFi module's own pages: it feeds the map but does not own it */
reg({kind:'page',id:'unifi_internet',t:'Internet health',g:'Live',ic:'mod_unifi',module:'unifi',parent:'UniFi',picon:'mod_unifi',pbadge:'soon',badge:'soon',dis:true,perm:'unifi.view'});
reg({kind:'page',id:'unifi_settings',t:'Settings',g:'Live',ic:'mod_unifi',module:'unifi',parent:'UniFi',badge:'soon',dis:true,perm:'unifi.manage'});

})();
