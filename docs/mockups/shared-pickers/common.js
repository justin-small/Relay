/* Made-up data and the five real pages, drawn with whichever picker set the variant file puts in window.V. */
function el(tag,attrs){var e=document.createElement(tag);attrs=attrs||{};for(var k in attrs){if(k==="class")e.className=attrs[k];else if(k==="text")e.textContent=attrs[k];else e.setAttribute(k,attrs[k]);}
 for(var i=2;i<arguments.length;i++){var c=arguments[i];(Array.isArray(c)?c:[c]).forEach(function(n){if(n!=null)e.appendChild(typeof n==="string"?document.createTextNode(n):n);});}return e;}
var KINDN={eth:"Ethernet",sfp:"SFP",sdi:"SDI",hdmi:"HDMI",xlr:"XLR"};
var PORTS=[
 {id:"p1",place:"Booth",gear:"Core switch",name:"12",kind:"eth",up:true},
 {id:"p2",place:"Booth",gear:"Core switch",name:"13",kind:"eth",up:false},
 {id:"p3",place:"Booth",gear:"Core switch",name:"SFP 1",kind:"sfp",up:true},
 {id:"p4",place:"Booth",gear:"ATEM Mini Extreme",name:"HDMI 1",kind:"hdmi",up:true},
 {id:"p5",place:"Booth",gear:"ATEM Mini Extreme",name:"HDMI 2",kind:"hdmi",up:null},
 {id:"p6",place:"Booth",gear:"ATEM Mini Extreme",name:"Ethernet",kind:"eth",up:true},
 {id:"p7",place:"Stage",gear:"Videohub 20x20",name:"SDI in 3",kind:"sdi",up:true,renamed:"Cam 2 SDI"},
 {id:"p8",place:"Stage",gear:"Videohub 20x20",name:"SDI out 7",kind:"sdi",up:true},
 {id:"p9",place:"Stage",gear:"Stage box",name:"XLR 4",kind:"xlr",up:null,missing:true},
 {id:"p10",place:"Stage",gear:"Stage switch",name:"3",kind:"eth",up:true}
];
var CHANS=[
 {id:"c1",kind:"video",dir:"source",name:"Camera 1",gear:"Videohub 20x20",ends:3},
 {id:"c2",kind:"video",dir:"source",name:"Camera 2",gear:"Videohub 20x20",ends:3},
 {id:"c3",kind:"video",dir:"source",name:"Lyrics",gear:"ProPresenter (NDI)",ends:1},
 {id:"c4",kind:"video",dir:"source",name:"Slides",gear:"ProPresenter (NDI)",ends:1,missing:true},
 {id:"c5",kind:"video",dir:"destination",name:"Projector",gear:"Videohub 20x20",ends:2},
 {id:"c6",kind:"video",dir:"destination",name:"Stage confidence",gear:"Videohub 20x20",ends:1},
 {id:"c7",kind:"video",dir:"destination",name:"Stream encoder",gear:"ATEM Mini Extreme",ends:1},
 {id:"c8",kind:"audio",dir:"source",name:"Pastor mic",gear:"X32",ends:2,renamed:true},
 {id:"c9",kind:"audio",dir:"source",name:"Vocal 1",gear:"X32",ends:1},
 {id:"c10",kind:"audio",dir:"source",name:"Vocal 2",gear:"X32",ends:1},
 {id:"c11",kind:"audio",dir:"source",name:"Band mix",gear:"X32",ends:1},
 {id:"c12",kind:"audio",dir:"destination",name:"Main mix",gear:"Audio bus",ends:1},
 {id:"c13",kind:"audio",dir:"destination",name:"Stream mix",gear:"Audio bus",ends:1},
 {id:"c14",kind:"audio",dir:"destination",name:"Captions feed",gear:"Audio bus",ends:1},
 {id:"c15",kind:"talk",dir:"destination",name:"Stage",gear:"Intercom",ends:1},
 {id:"c16",kind:"talk",dir:"destination",name:"Booth",gear:"Intercom",ends:1},
 {id:"c17",kind:"talk",dir:"destination",name:"Lobby",gear:"Intercom",ends:1},
 {id:"c18",kind:"audio",dir:"source",name:"Handheld 1",gear:"X32",ends:1},
 {id:"c19",kind:"audio",dir:"source",name:"Handheld 2",gear:"X32",ends:1},
 {id:"c20",kind:"audio",dir:"source",name:"Handheld 3",gear:"X32",ends:1},
 {id:"c21",kind:"audio",dir:"source",name:"Handheld 4",gear:"X32",ends:1}
];
var ROUTES={c5:{src:"c2",st:"applied"},c6:{src:"c3",st:"applied"},c7:{src:"c1",st:"differs",real:"Camera 2"},
 c12:{src:"c8",st:"applied",gain:-3},c13:{src:"c11",st:"applied",gain:0},c14:{src:null,st:"none"}};
function chan(id){return CHANS.filter(function(c){return c.id===id})[0]}
function port(id){return PORTS.filter(function(c){return c.id===id})[0]}
var STATUS={applied:["ok","Applied by the gear"],differs:["warn","Gear differs"],pending:["info","Waiting for the gear"],none:["muted","No source"]};
function setRoute(dest,src){ROUTES[dest]={src:src,st:src?"pending":"none",gain:ROUTES[dest].gain};}

var CABLES=[{a:"p1",b:"p10",by:"You",note:"Uplink to stage"},{a:"p6",b:"p3",by:"UniFi",note:""},{a:"p8",b:"p4",by:"Blackmagic",note:"Program out"}];
/* Planning Center position -> one or more input slots (names the operator makes up) -> input channels. Each person on a position is drawn one of its slots at random. */
var GROUPS=[{id:"g1",name:"Slot 1",ch:["c18"]},{id:"g2",name:"Slot 2",ch:["c19"]},{id:"g3",name:"Slot 3",ch:["c20"]},{id:"g4",name:"Slot 4",ch:["c21"]},{id:"g5",name:"Slot 5",ch:["c9"]},{id:"g6",name:"Slot 6",ch:["c10"]}];
var POSITIONS=[{id:"wl",name:"Worship leader",people:["Jordan Hale"],slots:["g5"]},{id:"so",name:"Soprano",people:["Ana Reyes","Mia Cole","Jo Park"],slots:["g1","g2","g3"]},
 {id:"al",name:"Alto",people:["Beth Kim","Sara Doe"],slots:["g2","g4"]},{id:"te",name:"Tenor",people:["Chris Lee","Sam Ortiz","Dev Rao"],slots:["g3","g4"]},{id:"sp",name:"Speaker",people:["Pastor Dan"],slots:[]}];
function grp(id){return GROUPS.filter(function(g){return g.id===id})[0]}

var ROOMS=[{room:"Stage monitors",ch:"c15"},{room:"Booth",ch:"c16"},{room:"Lobby",ch:null}];

function tbl(heads,rows){return el("div",{class:"tablescroll"},el("table",{class:"dt"},el("thead",{},el("tr",{},heads.map(function(h){return el("th",{text:h})}))),el("tbody",{},rows)));}
function endText(id){var p=port(id);return el("span",{},el("span",{class:"sp-nm",text:p.gear}),el("span",{class:"sp-sub",text:"  "+(p.renamed||p.name)+" · "+KINDN[p.kind]}));}

var PAGES={
 network:{group:"Core",title:"Network",tag:"Ports and notes › Cables",build:function(host){
  host.appendChild(el("p",{class:"note",text:"A cable joins any two ports on any gear. Pick both ends with the shared port picker."}));
  var rows=CABLES.map(function(c){return el("tr",{},el("td",{},endText(c.a)),el("td",{},endText(c.b)),el("td",{},el("span",{class:"badge muted",text:c.by})),el("td",{text:c.note}),el("td",{},el("button",{class:"sm quiet",text:"Remove"})));});
  host.appendChild(tbl(["One end","The other end","Recorded by","Note",""],rows));
  var a=el("div",{class:"sp-slot"}),b=el("div",{class:"sp-slot"});
  var add=el("div",{class:"sp-add"},a,b,el("input",{type:"text",placeholder:"Note (optional)","aria-label":"Note"}),el("button",{class:"primary",text:"Add cable"}));
  host.appendChild(add);
  var pb,pa=V.port(a,{label:"One end",onChange:function(id){pb.lock(id?port(id).kind:null)}});pb=V.port(b,{label:"The other end"});
  host.appendChild(el("p",{class:"note",text:"Once one end is chosen the other end only offers ports of the same kind."}));
 }},
 ndi:{group:"Modules",title:"NDI",tag:"Router › Outputs",build:function(host){
  host.appendChild(el("p",{class:"note",text:"Each output shows one video source. Core stores the route; this module applies it."}));
  V.routes(host,{kind:"video",dests:["c5","c6","c7"]});
 }},
 audio:{group:"Core",title:"Audio bus",tag:"Matrix",build:function(host){
  host.appendChild(el("p",{class:"note",text:"Each destination takes one audio source, with a gain."}));
  V.routes(host,{kind:"audio",dests:["c12","c13","c14"],gain:true});
 }},
 intercom:{group:"Modules",title:"Intercom",tag:"Rooms › Talk channels",build:function(host){
  host.appendChild(el("p",{class:"note",text:"Each room listens on one talk channel."}));
  var rows=ROOMS.map(function(r){var s=el("div",{class:"sp-slot"});var tr=el("tr",{},el("td",{class:"sp-nm",text:r.room}),el("td",{},s),el("td",{},el("button",{class:"sm",text:"Test tone"})));
   setTimeout(function(){V.channel(s,{kind:"talk",direction:"destination",value:r.ch,label:"Talk channel"});});return tr;});
  host.appendChild(tbl(["Room","Talk channel",""],rows));
 }},
 pco:{group:"Modules",title:"Planning Center",tag:"Team and mics",build:function(host){
  var box=el("div",{});host.appendChild(box);var n=7;
  function draw(){box.textContent="";
   box.appendChild(el("h3",{class:"sp-h",text:"1. Positions to input slots"}));
   box.appendChild(el("p",{class:"note",text:"A position can use several input slots. Each person who accepts the position is given one of its slots at random, and no two people get the same slot while one is free. The draw is kept for the service time and only changes if someone accepts or drops out, or you press Redraw."}));
   box.appendChild(tbl(["Planning Center position","On this service","Input slots"],POSITIONS.map(function(p){
    var seg=el("div",{class:"sp-slots",role:"group","aria-label":"Input slots for "+p.name});
    GROUPS.forEach(function(g){var on=p.slots.indexOf(g.id)>=0;var b=el("button",{type:"button",class:"sp-tog","aria-pressed":on?"true":"false",text:g.name});b.onclick=function(){if(on)p.slots.splice(p.slots.indexOf(g.id),1);else p.slots.push(g.id);draw()};seg.appendChild(b)});
    var msg=!p.slots.length?el("div",{class:"note warn",text:"No slots: nobody in this position is assigned an input."}):p.people.length>p.slots.length?el("div",{class:"note warn",text:p.people.length+" people but "+p.slots.length+" slot"+(p.slots.length===1?"":"s")+": "+(p.people.length-p.slots.length)+" will share a slot."}):el("div",{class:"note",text:p.people.length+(p.people.length===1?" person draws":" people draw")+" from "+p.slots.length+" slot"+(p.slots.length===1?"":"s")+"."});
    return el("tr",{},el("td",{class:"sp-nm",text:p.name}),el("td",{},p.people.map(function(nm){return el("span",{class:"sp-person",title:nm},el("span",{class:"sp-avatar",text:nm.split(" ").map(function(w){return w[0]}).join("")}),el("span",{text:nm}))})),
     el("td",{},seg,msg));})));
   box.appendChild(el("h3",{class:"sp-h",text:"2. Input slots to input channels"}));
   box.appendChild(el("p",{class:"note",text:"Name a slot anything you like. A slot is one place a person can be patched: it uses one or more input channels."}));
   box.appendChild(tbl(["Input slot","Used by","Input channels",""],GROUPS.map(function(g){var slot=el("div",{class:"sp-slot sp-addch"});
    var chips=el("div",{class:"sp-chips"},g.ch.map(function(id,k){var x=el("button",{class:"sp-chipx",type:"button","aria-label":"Remove "+chan(id).name,text:"\u00D7"});x.onclick=function(){g.ch.splice(k,1);draw()};return el("span",{class:"sp-chipc"},chan(id).name,el("small",{class:"sp-sub",text:" "+chan(id).gear}),x)}));
    var used=POSITIONS.filter(function(p){return p.slots.indexOf(g.id)>=0});
    var nm=el("input",{type:"text",class:"sm",value:g.name,"aria-label":"Slot name",maxlength:"40"});nm.onchange=function(){g.name=nm.value.trim()||g.name;draw()};
    var del=el("button",{class:"sm quiet",type:"button",text:"Delete"});del.onclick=function(){GROUPS.splice(GROUPS.indexOf(g),1);POSITIONS.forEach(function(p){var k=p.slots.indexOf(g.id);if(k>=0)p.slots.splice(k,1)});draw()};
    var tr=el("tr",{},el("td",{},nm),el("td",{},used.length?used.map(function(p){return el("span",{class:"badge muted",text:p.name})}):el("span",{class:"sp-sub",text:"No position yet"})),el("td",{},chips,slot),el("td",{},del));
    setTimeout(function(){V.channel(slot,{kind:"audio",direction:"source",label:"Add an input channel to "+g.name,empty:"Add an input channel",onChange:function(id){if(id&&g.ch.indexOf(id)<0)g.ch.push(id);draw()}})});return tr;})));
   var add=el("button",{class:"sm",type:"button",text:"Add input slot"});add.onclick=function(){GROUPS.push({id:"g"+(++n),name:"Slot "+(GROUPS.length+1),ch:[]});draw()};
   box.appendChild(el("div",{style:"margin-top:12px"},add));
  }
  draw();
 }}
};
function start(variant){
 var nav=el("nav",{class:"sp-nav","aria-label":"Pages"},el("h1",{text:"Relay"})),main=el("main",{class:"sp-main"});
 var last="";Object.keys(PAGES).forEach(function(k){var p=PAGES[k];if(p.group!==last){nav.appendChild(el("div",{class:"grp",text:p.group}));last=p.group;}
  var b=el("button",{text:p.title});b.onclick=function(){show(k)};b.dataset.k=k;nav.appendChild(b);});
 nav.appendChild(el("div",{class:"grp",text:"Mockup"}));
 var ps=el("select",{class:"sm","aria-label":"Preset"},["relay","softpop","tangerine","brutalist"].map(function(x){return el("option",{text:x})}));
 var th=el("select",{class:"sm","aria-label":"Theme"},["dark","light"].map(function(x){return el("option",{text:x})}));
 ps.onchange=th.onchange=function(){document.documentElement.dataset.preset=ps.value;document.documentElement.dataset.theme=th.value};
 nav.appendChild(ps);nav.appendChild(th);
 nav.appendChild(el("a",{class:"btnlink sm",href:"index.html",text:"All three options"}));
 document.body.appendChild(el("div",{class:"sp-app"},nav,main));
 function show(k){Array.prototype.forEach.call(nav.querySelectorAll("button"),function(b){b.classList.toggle("on",b.dataset.k===k)});
  main.textContent="";var p=PAGES[k];main.appendChild(el("div",{class:"sp-bar"},el("h2",{text:p.title}),el("span",{class:"sp-tag",text:p.tag+"  ·  option "+variant})));p.build(main);
  try{history.replaceState(null,"","#"+k)}catch(e){}}
 show(location.hash.slice(1)&&PAGES[location.hash.slice(1)]?location.hash.slice(1):"network");
}

/* What every variant draws: one normal shape for a port and a channel, so the three options differ only in looks. */
function portItem(p){return {id:p.id,kind:p.kind,group:p.place+" \u203A "+p.gear,gear:p.gear,title:p.renamed||p.name,was:p.renamed?p.name:"",badge:KINDN[p.kind],dot:p.up===true?"ok":p.up===false?"err":"",dotTip:p.up===true?"Link up":p.up===false?"No link":"Link unknown",missing:p.missing,text:[p.place,p.gear,p.name,p.renamed||""].join(" "),short:p.gear+" \u00B7 "+(p.renamed||p.name)};}
function chanItem(c){return {id:c.id,kind:c.kind,dir:c.dir,group:c.gear,gear:c.gear,title:c.name,sub:c.ends+(c.ends===1?" end":" ends"),badge:c.kind+" "+c.dir,missing:c.missing,text:[c.gear,c.name,c.kind].join(" "),short:c.name};}
function portItems(kind){return PORTS.filter(function(p){return !kind||p.kind===kind}).map(portItem)}
function chanItems(o){return CHANS.filter(function(c){return (!o.kind||c.kind===o.kind)&&(!o.direction||c.dir===o.direction)}).map(chanItem)}
