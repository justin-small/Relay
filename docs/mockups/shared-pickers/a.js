/* Option A: a searchable dropdown. The select you have today, but it searches, groups by gear and shows kind. */
var V=(function(){
 function combo(host,o){
  var st={value:o.value||null,items:o.items,open:false};
  var btn=el("button",{type:"button",class:"sp-cbtn","aria-haspopup":"listbox","aria-expanded":"false","aria-label":o.label});
  var panel=el("div",{class:"sp-pop",hidden:""});
  var q=el("input",{type:"search",class:"sm",placeholder:"Search","aria-label":"Search"});
  var tools=el("div",{class:"sp-tools"},q),list=el("ul",{class:"sp-list",role:"listbox"});
  var fl={};(o.filters||[]).forEach(function(f){fl[f.key]="";if(f.locked){tools.appendChild(el("span",{class:"badge muted",text:f.label+": "+f.locked}));fl[f.key]=f.value;return;}
   var seg=el("div",{class:"seg","aria-label":f.label,role:"group"});[["","Any"]].concat(f.options).forEach(function(op){var b=el("button",{type:"button",text:op[1],"aria-pressed":op[0]===""?"true":"false"});b.onclick=function(){fl[f.key]=op[0];Array.prototype.forEach.call(seg.children,function(x){x.setAttribute("aria-pressed",x===b?"true":"false")});draw()};seg.appendChild(b)});tools.appendChild(seg);});
  panel.appendChild(tools);panel.appendChild(list);document.body.appendChild(panel);host.appendChild(btn);
  function cur(){return st.items.filter(function(x){return x.id===st.value})[0]}
  function show(){btn.textContent="";var c=cur();btn.appendChild(el("span",{class:"sp-cval"+(c?"":" muted"),text:c?c.short:o.empty}));if(c&&c.badge)btn.appendChild(el("span",{class:"badge muted",text:c.badge}));btn.appendChild(el("span",{class:"muted",text:"▾"}));}
  function draw(){var s=q.value.trim().toLowerCase(),g={},ord=[];
   st.items.forEach(function(x){for(var k in fl)if(fl[k]&&x[k]!==fl[k])return;if(s&&x.text.toLowerCase().indexOf(s)<0)return;(g[x.group]=g[x.group]||(ord.push(x.group),[])).push(x)});
   list.textContent="";if(!ord.length)list.appendChild(el("li",{class:"sp-none",text:"Nothing matches."}));
   ord.forEach(function(k){list.appendChild(el("li",{class:"sp-grp",text:k}));g[k].forEach(function(x){
    var li=el("li",{class:"sp-opt",role:"option","aria-selected":x.id===st.value?"true":"false"},x.dot!=null&&x.dot!==undefined&&x.dotTip?el("span",{class:"dot "+x.dot,title:x.dotTip}):null,
     el("span",{class:"sp-ot"},x.title,x.was?el("span",{class:"sp-sub",text:"  ✎ "+x.was}):null,x.sub?el("span",{class:"sp-sub",text:"  "+x.sub}):null),x.missing?el("span",{class:"badge warn",text:"missing"}):null,el("span",{class:"badge muted",text:x.badge}));
    li.onclick=function(){st.value=x.id;close();show();o.onChange&&o.onChange(x.id)};list.appendChild(li)})});}
  function place(){var r=btn.getBoundingClientRect();panel.style.left=Math.min(r.left,innerWidth-Math.max(r.width,340)-8)+"px";panel.style.top=(r.bottom+4)+"px";panel.style.width=Math.max(r.width,340)+"px";}
  function open(){st.open=true;panel.hidden=false;btn.setAttribute("aria-expanded","true");place();draw();q.focus();}
  function close(){st.open=false;panel.hidden=true;btn.setAttribute("aria-expanded","false");}
  btn.onclick=function(e){e.stopPropagation();st.open?close():open()};q.oninput=draw;
  document.addEventListener("click",function(e){if(st.open&&!panel.contains(e.target))close()});
  document.addEventListener("keydown",function(e){if(e.key==="Escape"&&st.open){close();btn.focus()}});
  addEventListener("scroll",function(){if(st.open)place()},true);
  show();return {lock:function(kind){st.items=o.items.filter(function(x){return !kind||x.kind===kind});if(st.value&&!cur())st.value=null;show()}};
 }
 var kindOpts=Object.keys(KINDN).map(function(k){return [k,KINDN[k]]});
 var V={
  port:function(host,o){return combo(host,{label:o.label,empty:"Choose a port",items:portItems(),onChange:o.onChange,filters:[{key:"kind",label:"Kind",options:kindOpts}]})},
  channel:function(host,o){return combo(host,{label:o.label,empty:o.empty||"Choose a channel",value:o.value,items:chanItems({}),
   filters:[{key:"kind",label:"Kind",locked:o.kind,value:o.kind,options:[["audio","Audio"],["video","Video"],["talk","Talk"]]},{key:"dir",label:"Direction",locked:o.direction,value:o.direction,options:[["source","Source"],["destination","Destination"]]}]})},
  routes:function(host,o){
   var box=el("div",{class:"sp-routes"});host.appendChild(box);
   function draw(){box.textContent="";o.dests.forEach(function(id){var d=chan(id),r=ROUTES[id],slot=el("div",{class:"sp-slot"}),stt=STATUS[r.st];
    var row=el("div",{class:"sp-rt"},el("div",{},el("div",{class:"sp-nm",text:d.name}),el("div",{class:"sp-sub",text:d.gear})),el("span",{class:"muted",text:"←"}),slot,
     o.gain?el("label",{class:"sp-gain"},"Gain",el("input",{type:"number",class:"sm",value:r.gain==null?0:r.gain,"aria-label":"Gain in dB"}),"dB"):el("span"),
     el("span",{class:"badge "+stt[0],text:stt[1]}));box.appendChild(row);
    combo(slot,{label:"Source for "+d.name,empty:"No source",value:r.src,items:chanItems({kind:o.kind,direction:"source"}),onChange:function(v){setRoute(id,v);draw()}});
    if(r.st==="differs")box.appendChild(el("p",{class:"notice warn"},"Asked for "+chan(r.src).name+", but the gear is routing "+r.real+". ",el("button",{class:"sm",text:"Apply again",onclick:"void 0"})));});}
   draw();}
 };
 return V;})();
