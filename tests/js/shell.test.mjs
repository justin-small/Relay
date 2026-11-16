/*
 * The panel shell's plain helpers (#434): the look allow-list, where a popup goes, the palette filter.
 * The shell itself needs a browser; these do not.
 *
 * Run: node --test tests/js/
 */

import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { describe, it } from "node:test";

const require = createRequire(import.meta.url);
const S = require("../../app/static/shell.js");

describe("cleanLook", () => {
  it("gives the defaults for nothing", () => {
    assert.deepEqual(S.cleanLook(null), S.DEFAULTS);
    assert.deepEqual(S.cleanLook("x"), S.DEFAULTS);
  });
  it("keeps every allowed value", () => {
    const want = { ui_preset: "softpop", ui_mode: "system", ui_sidebar: "floating", ui_collapse: "offcanvas", ui_layout: "full",
                   ui_navbar: "scroll", ui_font: "serif", ui_collapsed: true };
    assert.deepEqual(S.cleanLook(want), want);
  });
  it("drops anything else", () => {
    const got = S.cleanLook({ ui_preset: "<b>x</b>", ui_mode: 1, ui_sidebar: ["inset"], ui_font: "comic", ui_collapsed: "true" });
    assert.deepEqual(got, S.DEFAULTS);
  });
});

describe("placePop", () => {
  const win = { w: 1000, h: 700 }, size = { w: 200, h: 100 };
  it("opens under the anchor, aligned to its end", () => {
    const at = S.placePop({ left: 700, right: 740, top: 10, bottom: 42 }, size, win, "bottom", "end");
    assert.deepEqual([at.x, at.y, at.origin], [540, 46, "top right"]);
  });
  it("opens to the right of a rail item", () => {
    const at = S.placePop({ left: 8, right: 56, top: 100, bottom: 132 }, size, win, "right", "start");
    assert.deepEqual([at.x, at.y, at.origin], [64, 100, "left center"]);
  });
  it("opens above for the user button", () => {
    const at = S.placePop({ left: 10, right: 100, top: 600, bottom: 650 }, size, win, "top", "start");
    assert.deepEqual([at.x, at.y], [10, 496]);
  });
  it("stays 8px inside the window", () => {
    const at = S.placePop({ left: 950, right: 990, top: 680, bottom: 700 }, size, win, "bottom", "start");
    assert.deepEqual([at.x, at.y], [792, 592]);
    const left = S.placePop({ left: -50, right: -10, top: -20, bottom: 0 }, size, win, "bottom", "end");
    assert.equal(left.x, 8);
  });
});

describe("filterItems", () => {
  const items = [{ g: "Live", t: "Session health" }, { g: "Settings", t: "Network" }, { g: "Actions", t: "Cycle theme" }];
  it("shows everything for an empty query", () => assert.equal(S.filterItems(items, "  ").length, 3));
  it("matches the title or the group, ignoring case", () => {
    assert.deepEqual(S.filterItems(items, "NET").map(i => i.t), ["Network"]);
    assert.deepEqual(S.filterItems(items, "settings").map(i => i.t), ["Network"]);
  });
  it("finds nothing for nonsense", () => assert.equal(S.filterItems(items, "zzz").length, 0));
});
