// Shared demo data for the three rundown redesign mockups. It matches the issue 148 demo (Sunday Gathering, a 9:00
// service running two minutes behind, Way Maker live). Static: nothing is fetched and nothing moves.
window.RD = {
  now: "9:13:09",
  service: "9:00 service",
  plan: "Sunday Gathering",
  current: { title: "Way Maker", key: "E", leader: "Tom Reyes", left: "1:51", lengthMin: 6 },
  next: { title: "Announcements", who: "unfilled" },
  plannedEnd: "10:01", projectedEnd: "10:03", over: 2,
  // planned / actual-or-projected start in minutes after 9:00; len in minutes
  items: [
    { t: "Countdown · walk-in loop", kind: "media", sec: "pre", p: -5, a: -5, len: 5, done: true },
    { t: "Welcome", kind: "item", sec: "service", p: 0, a: 1, len: 2, done: true },
    { t: "Build My Life", kind: "song", key: "G", p: 2, a: 3, len: 5, ran: 6, done: true },
    { t: "Way Maker", kind: "song", key: "E", lead: "Tom Reyes", p: 7, a: 9, len: 6, current: true, elapsed: 4.15 },
    { t: "Announcements", kind: "item", p: 13, a: 15, len: 4 },
    { t: "Offering · Goodness of God", kind: "song", key: "A", lead: "Ana Cruz", p: 17, a: 19, len: 5 },
    { t: "Sermon · Rooted", kind: "item", p: 22, a: 24, len: 32, notes: true },
    { t: "Response · Graves Into Gardens", kind: "song", key: "B", lead: "Sarah Lee", p: 54, a: 56, len: 5 },
    { t: "Benediction", kind: "item", p: 59, a: 61, len: 2 },
    { t: "Walk-out loop", kind: "media", sec: "post", p: 61, a: 63, len: 10 },
  ],
  // mic, name, position, team, sings (false = spoken mic, not drawn for songs), state
  mics: [
    ["01", "Sarah Lee", "Worship Leader", "Vocals", true],
    ["02", "Tom Reyes", "Acoustic Guitar", "Band", true, "leading"],
    ["03", "Priya Shah", "Vocals", "Vocals", true],
    ["04", "Ana Cruz", "Vocals", "Vocals", true],
    ["05", "Jo Lin", "Host", "Production", false],
    ["06", "", "Host", "Production", false, "spare"],
    ["09", "Pastor Dan", "Pastor", "Production", false],
    ["12", "", "Announcements", "Production", false, "unfilled"],
    ["20", "Hana Sato", "Choir", "Choir", true], ["21", "Zara Malik", "Choir", "Choir", true],
    ["22", "Caleb Stone", "Choir", "Choir", true], ["23", "Maria Gonzalez-Whitfield", "Choir", "Choir", true],
    ["24", "Theo Nash", "Choir", "Choir", true], ["25", "Lee Chen", "Choir", "Choir", true],
    ["26", "Grace Okafor", "Choir", "Choir", true], ["27", "Ivy Moreau", "Choir", "Choir", true],
    ["28", "Noah Brandt", "Choir", "Choir", true], ["29", "Abe Lin", "Choir", "Choir", true],
    ["30", "Ruth Adeyemi", "Choir", "Choir", true], ["31", "Lucy Park", "Choir", "Choir", true],
    ["32", "Owen Price", "Choir", "Choir", true], ["33", "Sam Ito", "Choir", "Choir", true],
  ],
  // people with no mic: name, position, team, status
  crew: [
    ["Ben Ford", "Keys", "Band"], ["Kim Alvarez", "Drums", "Band", "pending"], ["", "Bass", "Band", "unfilled"],
    ["Eli Brooks", "Vocals", "Vocals", "declined"],
    ["Jen Park", "Director", "Production"], ["Dev Patel", "FOH Audio", "Production"],
    ["Mo Hassan", "ProPresenter", "Production"], ["Luis Ortiz", "Camera 1", "Production"],
    ["", "Camera 2", "Production", "unfilled"],
  ],
  notes: "Week 3 · Colossians 2:6-7\n\n1. Received. We don't start the Christian life one way and finish it another. Slide: verse 6.\n\n2. Rooted and built up. A tree and a building: both grow from what's underneath. VIDEO at 14 min.\n\n3. Overflowing with thankfulness. Thankfulness is the evidence, not the cause.\n\nResponse. Prayer team at the front during Graves Into Gardens. Band in at the end of point 3.",
  captions: ["Si tienen preguntas, por favor guárdenlas para el final.", "Buenas tardes a todos, y gracias por venir.",
    "Tenemos mucho que cubrir hoy, así que empecemos."],
};
RD.fmt = (m) => { const t = 9 * 60 + Math.round(m); return Math.floor(t / 60) + ":" + String(t % 60).padStart(2, "0"); };
RD.first = (n) => n.split(" ")[0] === "Pastor" ? n : n.split(" ")[0];
RD.initials = (n) => n.split(/[\s-]+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("");
// Stand-in for a Planning Center photo_thumbnail: a portrait-shaped placeholder. Priya, Theo and Abe have no photo
// in this demo, so their tiles show the initials fallback the real board uses (also used when a photo will not load).
RD.noPhoto = new Set(["Priya Shah", "Theo Nash", "Abe Lin"]);
RD.photo = (name) => {
  if (!name || RD.noPhoto.has(name)) return "";
  let h = 0; for (const c of name) h = (h * 31 + c.charCodeAt(0)) % 360;
  const skin = ["#c99a74", "#8d5a3b", "#e2b896", "#6b4329", "#b07a55"][h % 5], hair = ["#2a1d15", "#4a3020", "#151515", "#7a5a3a"][h % 4];
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120"><rect width="120" height="120" fill="hsl(${h},18%,34%)"/>`
    + `<rect width="120" height="120" fill="hsl(${(h + 40) % 360},25%,22%)" opacity=".5" y="70"/>`
    + `<ellipse cx="60" cy="122" rx="46" ry="34" fill="hsl(${(h + 180) % 360},30%,40%)"/><rect x="51" y="70" width="18" height="18" fill="${skin}"/>`
    + `<ellipse cx="60" cy="52" rx="21" ry="25" fill="${skin}"/><path d="M38 50c0-20 10-29 22-29s23 9 23 29c-5-9-12-13-23-13s-17 4-22 13z" fill="${hair}"/></svg>`;
  return "data:image/svg+xml," + encodeURIComponent(svg);
};
RD.esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

// The board is a fixed 1920x1080 stage scaled to the window, like the real /screen. ?view=full|micboard|side
RD.view = new URLSearchParams(location.search).get("view") || "full";
RD.mount = (render) => {
  const stage = document.querySelector(".stage");
  stage.innerHTML = render[RD.view] ? render[RD.view]() : render.full();
  const fit = () => {
    const s = Math.min(innerWidth / 1920, innerHeight / 1080);
    stage.style.transform = `translate(${(innerWidth - 1920 * s) / 2}px,${(innerHeight - 1080 * s) / 2}px) scale(${s})`;
  };
  fit(); addEventListener("resize", fit);
  const nav = document.createElement("nav");
  nav.className = "mock-nav";
  nav.innerHTML = ["full", "micboard", "side"].map((v) =>
    `<a href="?view=${v}"${v === RD.view ? ' aria-current="page"' : ""}>${v}</a>`).join("") + '<a href="index.html">all options</a>';
  if (window.self === window.top) document.body.appendChild(nav);
};
