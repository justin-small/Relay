/* Option C: tiles. A field expands in place into a grid of tiles (no popup, works on a phone).
   Routes are a patch board: arm a source on the left, then tap the destination to patch it. */
var V=(function(){
 function tiles(host,o){
  var st={value:o.value||null,items:o.items,open:false},fl={};
  var btn=el("button",{type:"button",class:"sp-cbtn","aria-expanded":"false","aria-label":o.label});
  var panel=el("div",{class:"sp-tp",hidden:""});host.appendChild(btn);host.appendChild(panel);
  function cur(){return st.items.filter(function(x){return x.id===st.value})[0]}
  function show(){btn.textContent="";var c=cur();btn.appendChild(el("span",{class:"sp-cval"+(c?"":" muted"),text:c?c.short:o.empty}));if(c&&c.badge)btn.appendChild(el("span",{class:"badge muted",text:c.badge}));btn.appendChild(el("span",{class:"muted",text:st.open?"▴":"▾"}));}
  function draw(){panel.textContent="";var q=el("input",{type:"search",class:"sm",placeholder:"Search","aria-label":"Search"});var tools=el("div",{class:"sp-tools"},q);
   (o.filters||[]).forEach(function(f){if(f.locked){fl[f.key]=f.value;return;}fl[f.key]=fl[f.key]||"";var seg=el("div",{class:"seg","aria-label":f.label,role:"group"});[["","Any"]].concat(f.options).forEach(function(op){var b=el("button",{type:"button",text:op[1],"aria-pressed":(fl[f.key]||"")===op[0]?"true":"false"});b.onclick=function(){fl[f.key]=op[0];draw()};seg.appendChild(b)});tools.appendChild(seg)});
   var grid=el("div",{class:"sp-grid"});panel.appendChild(tools);panel.appendChild(grid);
   function fill(){var s=q.value.trim().toLowerCase();grid.textContent="";var n=0;
    st.items.forEach(function(x){for(var k in fl)if(fl[k]&&x[k]!==fl[k])return;if(s&&x.text.toLowerCase().indexOf(s)<0)return;n++;
     var t=el("button",{type:"button",class:"sp-tile","aria-pressed":x.id===st.value?"true":"false"},el("div",{class:"sp-th"},x.dotTip?el("span",{class:"dot "+x.dot,title:x.dotTip}):null,el("span",{class:"sp-nm",text:x.title}),x.missing?el("span",{class:"badge warn",text:"missing"}):null),
      el("div",{class:"sp-sub",text:x.gear+(x.was?" · ✎ "+x.was:"")+(x.sub?" · "+x.sub:"")}),el("span",{class:"badge muted",text:x.badge}));
     t.onclick=function(){st.value=x.id;st.open=false;panel.hidden=true;btn.setAttribute("aria-expanded","false");show();o.onChange&&o.onChange(x.id)};grid.appendChild(t)});
    if(!n)grid.appendChild(el("p",{class:"muted",text:"Nothing matches."}));}
   q.oninput=fill;fill();
   if(st.value){var c=el("button",{class:"sm quiet",type:"button",text:"Clear choice"});c.onclick=function(){st.value=null;st.open=false;panel.hidden=true;show();o.onChange&&o.onChange(null)};panel.appendChild(c)}}
  btn.onclick=function(){st.open=!st.open;panel.hidden=!st.open;btn.setAttribute("aria-expanded",String(st.open));if(st.open)draw();show();};
  show();return {lock:function(kind){st.items=o.items.filter(function(x){return !kind||x.kind===kind});if(st.value&&!cur())st.value=null;show()}};
 }
 var kindOpts=Object.keys(KINDN).map(function(k){return [k,KINDN[k]]});
 return {
  port:function(host,o){return tiles(host,{label:o.label,empty:"Choose a port",items:portItems(),onChange:o.onChange,filters:[{key:"kind",label:"Kind",options:kindOpts}]})},
  channel:function(host,o){return tiles(host,{label:o.label,empty:o.empty||"Choose a channel",value:o.value,items:chanItems({}),
   filters:[{key:"kind",label:"Kind",locked:o.kind,value:o.kind,options:[["audio","Audio"],["video","Video"],["talk","Talk"]]},{key:"dir",label:"Direction",locked:o.direction,value:o.direction,options:[["source","Source"],["destination","Destination"]]}]})},
  routes:function(host,o){
   var armed=null,box=el("div",{class:"sp-board"});host.appendChild(box);
   function draw(){box.textContent="";var srcs=chanItems({kind:o.kind,direction:"source"});
    var left=el("div",{class:"sp-col"},el("div",{class:"sp-ch",text:"1. Pick a source"}));
    srcs.forEach(function(s){var b=el("button",{type:"button",class:"sp-tile","aria-pressed":armed===s.id?"true":"false"},el("div",{class:"sp-nm",text:s.title}),el("div",{class:"sp-sub",text:s.gear}),s.missing?el("span",{class:"badge warn",text:"missing"}):null);b.onclick=function(){armed=armed===s.id?null:s.id;draw()};left.appendChild(b)});
    var right=el("div",{class:"sp-col"},el("div",{class:"sp-ch",text:armed?"2. Tap a destination to patch "+chan(armed).name:"2. Then tap a destination"}));
    o.dests.forEach(function(id){var d=chan(id),r=ROUTES[id],stt=STATUS[r.st];
     var t=el("div",{class:"sp-dest"+(armed?" armed":"")},el("div",{class:"sp-dn"},el("div",{class:"sp-nm",text:d.name}),el("div",{class:"sp-sub",text:d.gear})),
      r.src?el("span",{class:"sp-chip"},"← "+chan(r.src).name):el("span",{class:"sp-sub",text:"no source"}),
      o.gain?el("label",{class:"sp-gain"},el("input",{type:"number",class:"sm",value:r.gain==null?0:r.gain,"aria-label":"Gain in dB"}),"dB"):null,
      el("span",{class:"badge "+stt[0],text:stt[1]}),r.src?el("button",{class:"sm quiet",type:"button",text:"Clear"}):null);
     t.onclick=function(e){if(e.target.tagName==="INPUT"||e.target.tagName==="LABEL")return;if(e.target.tagName==="BUTTON"){setRoute(id,null);draw();return}if(armed){setRoute(id,armed);armed=null;draw()}};
     right.appendChild(t);
     if(r.st==="differs")right.appendChild(el("p",{class:"notice warn"},"Asked for "+chan(r.src).name+", but the gear is routing "+r.real+"."));});
    box.appendChild(left);box.appendChild(right);}
   draw();}
 };})();
