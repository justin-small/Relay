/* Option B: a browse drawer. The field opens a side drawer: gear on the left, its ports or channels on the right.
   Routes are a crosspoint matrix: destinations down, sources across, one dot per route. */
var V=(function(){
 var drawer,veil;
 function ensure(){if(drawer)return;veil=el("div",{class:"sp-veil",hidden:""});drawer=el("aside",{class:"sp-drawer",hidden:"",role:"dialog","aria-modal":"true"});document.body.appendChild(veil);document.body.appendChild(drawer);
  veil.onclick=closeD;document.addEventListener("keydown",function(e){if(e.key==="Escape")closeD()});}
 function closeD(){if(drawer){drawer.hidden=true;veil.hidden=true}}
 function browse(host,o){
  ensure();var st={value:o.value||null,items:o.items};
  var btn=el("button",{type:"button",class:"sp-cbtn","aria-label":o.label});host.appendChild(btn);
  function cur(){return st.items.filter(function(x){return x.id===st.value})[0]}
  function show(){btn.textContent="";var c=cur();btn.appendChild(el("span",{class:"sp-cval"+(c?"":" muted"),text:c?c.short:o.empty}));if(c&&c.badge)btn.appendChild(el("span",{class:"badge muted",text:c.badge}));btn.appendChild(el("span",{class:"muted",text:"…"}));}
  btn.onclick=function(){
   var fl={},gearSel="",q=el("input",{type:"search",placeholder:"Search everything",class:"sm","aria-label":"Search"});
   var side=el("div",{class:"sp-side"}),body=el("div",{class:"sp-body"}),head=el("div",{class:"sp-dh"},el("strong",{text:o.title}),el("button",{class:"sm quiet",text:"Close",onclick:"void 0"}));
   head.lastChild.onclick=closeD;
   var tools=el("div",{class:"sp-tools"},q);
   (o.filters||[]).forEach(function(f){fl[f.key]="";if(f.locked){fl[f.key]=f.value;tools.appendChild(el("span",{class:"badge muted",text:f.label+": "+f.locked}));return;}
    var seg=el("div",{class:"seg","aria-label":f.label,role:"group"});[["","Any"]].concat(f.options).forEach(function(op){var b=el("button",{type:"button",text:op[1],"aria-pressed":op[0]===""?"true":"false"});b.onclick=function(){fl[f.key]=op[0];Array.prototype.forEach.call(seg.children,function(x){x.setAttribute("aria-pressed",x===b?"true":"false")});draw()};seg.appendChild(b)});tools.appendChild(seg)});
   var foot=el("div",{class:"sp-df"},el("button",{class:"sm quiet",text:"Clear choice"}),el("span",{class:"sp-sub",text:"Names are the gear's own. A pencil marks a Relay-only name."}));
   foot.firstChild.onclick=function(){st.value=null;show();closeD();o.onChange&&o.onChange(null)};
   drawer.textContent="";drawer.appendChild(head);drawer.appendChild(tools);drawer.appendChild(el("div",{class:"sp-split"},side,body));drawer.appendChild(foot);
   function draw(){var s=q.value.trim().toLowerCase();
    var vis=st.items.filter(function(x){for(var k in fl)if(fl[k]&&x[k]!==fl[k])return false;return !s||x.text.toLowerCase().indexOf(s)>=0});
    var gears={},ord=[];vis.forEach(function(x){(gears[x.group]=gears[x.group]||(ord.push(x.group),[])).push(x)});
    if(gearSel&&!gears[gearSel])gearSel="";
    side.textContent="";var all=el("button",{class:"sp-gb",text:"All gear  ",type:"button","aria-pressed":gearSel===""?"true":"false"},el("span",{class:"sp-sub",text:String(vis.length)}));all.onclick=function(){gearSel="";draw()};side.appendChild(all);
    ord.forEach(function(g){var b=el("button",{class:"sp-gb",type:"button","aria-pressed":gearSel===g?"true":"false"},g+"  ",el("span",{class:"sp-sub",text:String(gears[g].length)}));b.onclick=function(){gearSel=g;draw()};side.appendChild(b)});
    body.textContent="";var shown=gearSel?gears[gearSel]:vis;if(!shown.length)body.appendChild(el("p",{class:"sp-none",text:"Nothing matches."}));
    shown.forEach(function(x){var r=el("button",{type:"button",class:"sp-row","aria-pressed":x.id===st.value?"true":"false"},
     x.dotTip?el("span",{class:"dot "+x.dot,title:x.dotTip}):null,el("span",{class:"sp-ot"},el("span",{class:"sp-nm",text:x.title}),x.was?el("span",{class:"sp-sub",text:"  ✎ "+x.was}):null,el("div",{class:"sp-sub",text:x.group+(x.sub?" · "+x.sub:"")})),
     x.missing?el("span",{class:"badge warn",text:"missing"}):null,el("span",{class:"badge muted",text:x.badge}));
     r.onclick=function(){st.value=x.id;show();closeD();o.onChange&&o.onChange(x.id)};body.appendChild(r)});}
   q.oninput=draw;draw();drawer.hidden=false;veil.hidden=false;q.focus();};
  show();return {lock:function(kind){st.items=o.items.filter(function(x){return !kind||x.kind===kind});if(st.value&&!cur())st.value=null;show()}};
 }
 var kindOpts=Object.keys(KINDN).map(function(k){return [k,KINDN[k]]});
 return {
  port:function(host,o){return browse(host,{title:o.label+": choose a port",label:o.label,empty:"Choose a port",items:portItems(),onChange:o.onChange,filters:[{key:"kind",label:"Kind",options:kindOpts}]})},
  channel:function(host,o){return browse(host,{title:o.label+": choose a channel",label:o.label,empty:o.empty||"Choose a channel",value:o.value,items:chanItems({}),
   filters:[{key:"kind",label:"Kind",locked:o.kind,value:o.kind,options:[["audio","Audio"],["video","Video"],["talk","Talk"]]},{key:"dir",label:"Direction",locked:o.direction,value:o.direction,options:[["source","Source"],["destination","Destination"]]}]})},
  routes:function(host,o){
   var box=el("div",{class:"tablescroll"});host.appendChild(box);
   function draw(){box.textContent="";var srcs=chanItems({kind:o.kind,direction:"source"});
    var head=el("tr",{},el("th",{text:"Destination"}),srcs.map(function(s){return el("th",{class:"sp-xh"},el("span",{text:s.title}),el("small",{text:s.gear}))}),o.gain?el("th",{text:"Gain"}):null,el("th",{text:"State"}));
    var rows=o.dests.map(function(id){var d=chan(id),r=ROUTES[id],stt=STATUS[r.st];
     return el("tr",{},el("td",{},el("div",{class:"sp-nm",text:d.name}),el("div",{class:"sp-sub",text:d.gear})),
      srcs.map(function(s){var on=r.src===s.id;var b=el("button",{type:"button",class:"sp-x"+(on?" on":""),"aria-pressed":on?"true":"false","aria-label":s.title+" to "+d.name,title:s.title+" → "+d.name});b.onclick=function(){setRoute(id,on?null:s.id);draw()};return el("td",{class:"sp-xc"},b)}),
      o.gain?el("td",{},el("input",{type:"number",class:"sm sp-g",value:r.gain==null?0:r.gain,"aria-label":"Gain in dB"})):null,
      el("td",{},el("span",{class:"badge "+stt[0],text:stt[1]}),r.st==="differs"?el("div",{class:"sp-sub",text:"gear: "+r.real}):null));});
    box.appendChild(el("table",{class:"sp-mx"},el("thead",{},head),el("tbody",{},rows)));}
   draw();}
 };})();
