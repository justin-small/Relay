/*
 * The shared UI helpers (#461): can, api, client, el, say and confirm, against a small stand-in for the browser.
 *
 * Run: node --test tests/js/
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";
import vm from "node:vm";

class Node_ {
  constructor(tag) { this.tagName = tag; this.childNodes = []; this.className = ""; this.hidden = false; this._text = ""; }
  get nodeType() { return 1; }
  appendChild(n) { this.childNodes.push(n); return n; }
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
