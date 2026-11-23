// Shared sample song and helpers for the three mockups. No real data.
const SONG = {
  title: "Goodness of God", key: "G", ccli: "7117038", bpm: 63,
  sections: [
    { id: "v1", name: "Verse 1", kind: "verse", text: "[G]I love You, Lord\nFor Your mercy never fails me\n[C]All my days, I've been held in Your hands\nFrom the moment that I wake up\nUntil I lay my head", tr: "Te amo, Señor\nTu misericordia nunca me falla\nTodos mis días, en Tus manos\nDesde que despierto\nHasta que me acuesto" },
    { id: "ch", name: "Chorus", kind: "chorus", text: "[G]Cause You are good, good, oh\nAll the time You are good\nAnd all of my life You are faithful\nAnd all of my life You have been good", tr: "Porque Tú eres bueno, bueno\nTodo el tiempo eres bueno\nToda mi vida fiel has sido\nToda mi vida bueno has sido" },
    { id: "v2", name: "Verse 2", kind: "verse", text: "[G]I love Your voice\nYou have led me through the fire\nIn darkest night You are close like a shadow", tr: "" },
    { id: "br", name: "Bridge", kind: "bridge", text: "[Em]Your goodness is running after, it's running after me", tr: "" },
  ],
  arrangements: [
    { name: "Sunday", order: ["v1", "ch", "v2", "ch", "br", "br", "ch"] },
    { name: "Short", order: ["v1", "ch", "ch"] },
    { name: "Acoustic", order: ["v1", "v2", "br", "ch"] },
  ],
};
const strip = (s) => s.replace(/\[[^\]]*\]/g, "");
// Cut a section into slides of N lines. Lyric and translation lines stay paired by index.
function slidesOf(sec, n) {
  const L = strip(sec.text).split("\n"), T = sec.tr ? sec.tr.split("\n") : [], out = [];
  for (let i = 0; i < L.length; i += n) out.push({ sec, lines: L.slice(i, i + n), tr: T.slice(i, i + n) });
  return out;
}
function slideHTML(s, o = {}) {
  const chars = s.lines.join("").length, over = s.lines.length > (o.maxLines || 4) || chars > (o.maxChars || 120);
  const wn = over ? `<span class="wn">${s.lines.length > (o.maxLines || 4) ? "Too many lines" : "Too many characters"}</span>` : "";
  return `<div class="sl${over ? " over" : ""}"><span class="ch"><i class="sw c-${s.sec.kind}"></i>${s.sec.name}</span>${o.hot ? `<span class="ho">${o.hot}</span>` : ""}
    <div class="l">${s.lines.join("<br>")}</div>${o.tr && s.tr.length ? `<div class="t">${s.tr.join("<br>")}</div>` : ""}${wn}<span class="cc">${chars}</span></div>`;
}
function chordHTML(t) { return t.replace(/\[([^\]]*)\]/g, '<span class="chord">[$1]</span>'); }
function nav(cur) {
  const n = [["a-workbench.html", "A · Workbench"], ["b-guided.html", "B · Guided steps"], ["c-sorter.html", "C · Slide sorter"]];
  return `<div class="nav">${n.map(([h, t]) => `<a href="${h}"${h === cur ? " aria-current=page" : ""}>${t}</a>`).join("")}<a href="index.html">All three</a></div>`;
}
function helperPill(on) {
  return on ? `<span class="spill on"><i class="dot ok"></i>Helper connected · Studio Mac</span>`
    : `<span class="spill off err"><i class="dot err"></i>No helper connected</span>`;
}
function noHelperNote() {
  return `<div class="notice warn"><b>No helper is connected.</b> ProPresenter can only import a song by opening its file, and the Relay Helper does that on the ProPresenter computer. Install it there, sign in to this Relay, and this page will send. <a href="#">How to install the helper</a></div>`;
}
