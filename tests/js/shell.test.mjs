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

describe("pageNums", () => {
  it("shows every page when there are three or fewer", () => {
    assert.deepEqual(S.pageNums(1, 1), [1]);
    assert.deepEqual(S.pageNums(2, 3), [1, 2, 3]);
  });
  it("is a window of three that follows the current page", () => {
    assert.deepEqual(S.pageNums(1, 9), [1, 2, 3]);
    assert.deepEqual(S.pageNums(2, 9), [1, 2, 3]);
    assert.deepEqual(S.pageNums(5, 9), [4, 5, 6]);
    assert.deepEqual(S.pageNums(8, 9), [7, 8, 9]);
    assert.deepEqual(S.pageNums(9, 9), [7, 8, 9]);
  });
});

describe("sortRows", () => {
  const rows = [{ n: "bob", a: 3 }, { n: "Alice", a: 10 }, { n: "carol", a: 3 }];
  it("sorts text without regard to case", () => assert.deepEqual(S.sortRows(rows, "n", 1).map(r => r.n), ["Alice", "bob", "carol"]));
  it("sorts descending", () => assert.deepEqual(S.sortRows(rows, "n", -1).map(r => r.n), ["carol", "bob", "Alice"]));
  it("sorts numbers as numbers", () => assert.deepEqual(S.sortRows(rows, "a", 1).map(r => r.a), [3, 3, 10]));
  it("keeps equal rows in their order, both ways", () => {
    assert.deepEqual(S.sortRows(rows, "a", 1).map(r => r.n), ["bob", "carol", "Alice"]);
    assert.deepEqual(S.sortRows(rows, "a", -1).map(r => r.n), ["Alice", "bob", "carol"]);
  });
  it("puts a missing value first", () => assert.deepEqual(S.sortRows([{ n: "b" }, {}], "n", 1).map(r => r.n), [undefined, "b"]));
  it("does not change the list it is given", () => { const c = rows.slice(); S.sortRows(rows, "n", 1); assert.deepEqual(rows, c); });
});
