/*
 * The shared UI helpers (#461, #463): can, api, client, el, say, confirm and the control builders, against a small
 * stand-in for the browser.
 *
 * Run: node --test tests/js/
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";
import vm from "node:vm";

class Node_ {
  constructor(tag) { this.tagName = tag; this.childNodes = []; this.className = ""; this.hidden = false; this._text = ""; this.dataset = {}; }
  get nodeType() { return 1; }
  appendChild(n) { this.childNodes.push(n); return n; }
  addEventListener(type, fn) { (this.listeners ||= {})[type] = fn; }
  closest(sel) { return this.panel && sel === "[role=tabpanel]" ? this.panel : null; }
  get textContent() { return this._text + this.childNodes.map((c) => c.textContent).join(""); }
  set textContent(v) { this._text = String(v); this.childNodes = []; }
  get classList() {
    const self = this;
    return { toggle(c, on) { const s = new Set(self.className.split(" ").filter(Boolean)); on ? s.add(c) : s.delete(c); self.className = [...s].join(" "); } };
  }
}
class Text_ { constructor(t) { this.textContent = t; } get nodeType() { return 3; } }

function load(extra) {
  const byId = {};
  const win = Object.assign({
    document: { createElement: (t) => new Node_(t), createTextNode: (t) => new Text_(t), getElementById: (id) => byId[id] || null },
    setTimeout, clearTimeout,
  }, extra || {});
  win.window = win;
  vm.runInNewContext(readFileSync(new URL("../../app/static/relay-ui.js", import.meta.url), "utf8"), win);
  return { ui: win.relayUI, byId, win };
}

function reply(status, body) {
  return async (path, init) => { reply.last = { path, init }; return { ok: status < 400, status, json: async () => body }; };
}

describe("can", () => {
  it("allows everything when the page has no permission check", () => {
    assert.equal(load().ui.can("x.manage"), true);
  });
  it("asks relayCan when there is one", () => {
    const { ui } = load({ relayCan: (p) => p === "a" });
    assert.equal(ui.can("a"), true);
    assert.equal(ui.can("b"), false);
  });
});

describe("el", () => {
  const { ui } = load();
  it("takes a class name and text, the panel's usual shape", () => {
    const n = ui.el("td", "muted", "None yet");
    assert.equal(n.className, "muted");
    assert.equal(n.textContent, "None yet");
  });
  it("takes properties, with text as text", () => {
    const n = ui.el("button", { type: "button", disabled: true, text: "<b>Go</b>" });
    assert.equal(n.type, "button");
    assert.equal(n.disabled, true);
    assert.equal(n.textContent, "<b>Go</b>");
  });
  it("adds children, skipping empty ones, flattening lists", () => {
    const n = ui.el("div", "", [ui.el("span", "a"), null, false, "", "x"], 0);
    assert.equal(n.className, "");
    assert.equal(n.childNodes.length, 3);
    assert.equal(n.textContent, "x0");
  });
});

describe("api", () => {
  it("sends JSON and answers the server's body", async () => {
    const { ui } = load({ fetch: reply(200, { ok: true }) });
    assert.deepEqual(await ui.api("POST", "/x", { a: 1 }), { ok: true });
    assert.equal(reply.last.init.method, "POST");
    assert.equal(reply.last.init.body, '{"a":1}');
    assert.equal(reply.last.init.credentials, "same-origin");
  });
  it("sends no body without one", async () => {
    const { ui } = load({ fetch: reply(200, {}) });
    await ui.api("GET", "/x");
    assert.equal(reply.last.init.body, undefined);
    await ui.api("GET", "/x", null);
    assert.equal(reply.last.init.body, undefined);
  });
  it("throws the server's sentence, or a fixed one", async () => {
    const a = load({ fetch: reply(400, { detail: "Name is taken." }) }).ui;
    await assert.rejects(a.api("POST", "/x", {}), (e) => e.message === "Name is taken." && e.status === 400);
    const b = load({ fetch: reply(500, {}) }).ui;
    await assert.rejects(b.api("GET", "/x"), /That did not work\./);
    await assert.rejects(b.client({ status: true })("GET", "/x"), /HTTP 500/);
  });
  it("tells the panel when the session has ended, only when asked", async () => {
    const seen = [];
    const { ui } = load({ fetch: reply(401, {}), RelayAdmin: { sessionEnded: (res) => seen.push(res.status) } });
    await assert.rejects(ui.api("GET", "/x"));
    assert.deepEqual(seen, []);
    await assert.rejects(ui.api("GET", "/x", undefined, { session: true }));
    assert.deepEqual(seen, [401]);
  });
  it("writes the error into a note and answers null", async () => {
    const note = new Node_("div");
    const bad = load({ fetch: reply(403, { detail: "Not yours." }) }).ui;
    assert.equal(await bad.api("PUT", "/x", {}, { note }), null);
    assert.equal(note.textContent, "Not yours.");
    const good = load({ fetch: reply(200, { a: 1 }) }).ui;
    assert.deepEqual(await good.api("GET", "/x", undefined, { note }), { a: 1 });
    assert.equal(note.textContent, "");
  });
  it("reloads the page for the statuses it is given", async () => {
    let reloaded = 0;
    const { ui } = load({ fetch: reply(401, {}), location: { reload: () => { reloaded++; } } });
    assert.equal(await ui.api("GET", "/x", undefined, { reload: [401] }), null);
    assert.equal(reloaded, 1);
  });
});

describe("say", () => {
  it("marks an error, a success and nothing", () => {
    const { ui, byId } = load();
    byId.m = new Node_("div"); byId.m.className = "note";
    ui.say("m", "Broke.", true);
    assert.equal(byId.m.className, "note err");
    ui.say("m", "Saved.");
    assert.equal(byId.m.className, "note ok");
    ui.say("m", "");
    assert.equal(byId.m.className, "note");
    assert.equal(byId.m.textContent, "");
  });
  it("hides an empty line when asked, and leaves classes alone when plain", () => {
    const { ui } = load();
    const n = new Node_("div"); n.className = "toast";
    ui.say(n, "", false, { hide: true });
    assert.equal(n.hidden, true);
    ui.say(n, "Hi", true, { plain: true, hide: true });
    assert.equal(n.className, "toast");
    assert.equal(n.hidden, false);
  });
  it("ignores an element that is not on the page", () => {
    assert.doesNotThrow(() => load().ui.say("missing", "x"));
  });
});

describe("confirm", () => {
  it("answers the native dialog as a promise", async () => {
    const asked = [];
    const { ui } = load({ confirm: (t) => { asked.push(t); return t === "yes?"; } });
    assert.equal(await ui.confirm("yes?"), true);
    assert.equal(await ui.confirm("no?"), false);
    assert.deepEqual(asked, ["yes?", "no?"]);
  });
});

describe("button", () => {
  it("is a type=button with the class and a plain click listener", () => {
    const { ui } = load();
    let n = 0;
    const b = ui.button("Save", () => { n++; }, "primary sm");
    assert.equal(b.type, "button");
    assert.equal(b.className, "primary sm");
    assert.equal(b.textContent, "Save");
    b.listeners.click();
    assert.equal(n, 1);
  });
  it("with an error line, clears it, waits for the work and shows what it threw", async () => {
    const { ui } = load();
    const said = [];
    const b = ui.buttons((t) => said.push(t))("Go", async () => { assert.equal(b.disabled, true); throw new Error("Nope."); }, "danger");
    assert.equal(b.className, "danger");
    await b.onclick();
    assert.deepEqual(said, ["", "Nope."]);
    assert.equal(b.disabled, false);
  });
});

describe("field, pill, link, kv", () => {
  it("joins a label to its control", () => {
    const { ui } = load();
    const input = new Node_("input");
    const f = ui.field("Name", input, "nm");
    assert.equal(f.className, "field");
    assert.equal(input.id, "nm");
    assert.equal(f.childNodes[0].htmlFor, "nm");
  });
  it("draws a pill with its dot", () => {
    const p = load().ui.pill("Live", "on");
    assert.equal(p.className, "spill on");
    assert.equal(p.childNodes[0].className, "dot");
    assert.equal(p.textContent, "Live");
  });
  it("opens a link in a new tab without handing the page over", () => {
    const a = load().ui.link("Docs", "https://example.com/");
    assert.equal(a.target, "_blank");
    assert.equal(a.rel, "noopener noreferrer");
  });
  it("adds a term only when it has a value", () => {
    const { ui } = load();
    const dl = new Node_("dl");
    ui.kv(dl, "Code", "ABCD");
    ui.kv(dl, "Empty", "");
    assert.equal(dl.childNodes.length, 2);
  });
});

describe("note and shown", () => {
  it("shows an error line and hides it when empty", () => {
    const { ui, byId } = load();
    byId.e = new Node_("div"); byId.e.className = "note err";
    const show = ui.note("e");
    show("Broken.");
    assert.equal(byId.e.hidden, false);
    assert.equal(byId.e.className, "note err");
    show("");
    assert.equal(byId.e.hidden, true);
  });
  it("is false while the tab panel is closed or the page is behind", () => {
    const { ui, win } = load();
    win.document.visibilityState = "visible";
    const n = new Node_("div"); n.panel = { hidden: false };
    assert.equal(ui.shown(n), true);
    n.panel.hidden = true;
    assert.equal(ui.shown(n), false);
    n.panel.hidden = false; win.document.visibilityState = "hidden";
    assert.equal(ui.shown(n), false);
  });
});

describe("picker and pickedName", () => {
  // A <select> as far as the helpers use one: its options, its chosen index and its value.
  function select(ui) {
    const sel = new Node_("select");
    Object.defineProperty(sel, "options", { get() { return this.childNodes; } });
    sel.selectedIndex = 0;
    Object.defineProperty(sel, "value", {
      get() { return (this.childNodes[this.selectedIndex] || {}).value || ""; },
      set(v) { const i = this.childNodes.findIndex((o) => o.value === v); this.selectedIndex = i < 0 ? 0 : i; },
    });
    return sel;
  }
  const picks = [{ ref: "team:pco:1", label: "Camera" }, { ref: "position:pco:1/2", label: "Camera \u00b7 Camera 1" }];

  it("offers the none option, then each pick, and selects the saved ref", () => {
    const { ui } = load();
    const sel = select(ui);
    ui.picker(sel, picks, { ref: "position:pco:1/2", name: "Camera 1" }, "Everyone serving");
    assert.deepEqual(sel.childNodes.map((o) => o.textContent), ["Everyone serving", "Camera", "Camera \u00b7 Camera 1"]);
    assert.equal(sel.value, "position:pco:1/2");
    assert.equal(ui.pickedName(sel), "");
  });
  it("starts on none when nothing is saved", () => {
    const { ui } = load();
    const sel = select(ui);
    ui.picker(sel, picks, null);
    assert.equal(sel.value, "");
    assert.equal(sel.childNodes[0].textContent, "None");
  });
  it("gives a saved name with no ref an option of its own, kept and chosen", () => {
    const { ui } = load();
    const sel = select(ui);
    ui.picker(sel, picks, { ref: "", name: "Lights" }, "Everyone serving");
    assert.equal(sel.childNodes.length, 4);
    assert.equal(sel.childNodes[3].textContent, "Lights (not matched to Planning Center)");
    sel.selectedIndex = 3;
    assert.equal(ui.pickedName(sel), "Lights");
  });
});
