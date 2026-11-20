/*
 * The Network map page's plain helpers (#437): the filter, the layout, how old a read is.
 * The page itself needs a browser and the API; these have neither.
 *
 * Run: node --test tests/js/
 */

import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { describe, it } from "node:test";

const require = createRequire(import.meta.url);
const M = require("../../app/static/network_map.js");

const dev = (id, over = {}) => ({ id, name: id, kind: "client", ips: [], mac: "", host: "", parent: "", via: "", ndi: false, dante: false, ...over });

describe("matches", () => {
  const box = dev("box", { name: "Stage box 1", ips: ["10.0.0.20"], mac: "aa:bb:cc:dd:ee:02", dante: true });
  it("filters by kind of AV", () => {
    assert.equal(M.matches(box, "all", ""), true);
    assert.equal(M.matches(box, "av", ""), true);
    assert.equal(M.matches(box, "dante", ""), true);
    assert.equal(M.matches(box, "ndi", ""), false);
    assert.equal(M.matches(dev("pc"), "av", ""), false);
  });
  it("finds by name, address or MAC, ignoring case", () => {
    assert.equal(M.matches(box, "all", "STAGE"), true);
    assert.equal(M.matches(box, "all", "10.0.0.2"), true);
    assert.equal(M.matches(box, "all", "ee:02"), true);
    assert.equal(M.matches(box, "all", "nope"), false);
  });
});

describe("visibleSet", () => {
  const list = [dev("core", { kind: "switch" }), dev("box", { parent: "core", dante: true }), dev("pc", { parent: "core" })];
  it("keeps a match and every device above it", () => {
    const keep = M.visibleSet(list, "dante", "");
    assert.deepEqual(Object.keys(keep).sort(), ["box", "core"]);
  });
  it("keeps everything for All", () => assert.equal(Object.keys(M.visibleSet(list, "all", "")).length, 3));
});

describe("layout with a switch source", () => {
  const data = {
    has_switch_source: true, relay_ports: [],
    devices: [dev("gw", { kind: "gateway" }), dev("sw", { kind: "switch", parent: "gw" }),
              ...[1, 2, 3, 4].map(i => dev("c" + i, { parent: "sw", ips: ["10.0.0." + i] }))],
  };
  const lay = M.layout(data, "all", "");
  const at = Object.fromEntries(lay.nodes.map(n => [n.id, n]));
  it("puts the gateway above the switch above its clients", () => {
    assert.ok(at.gw.y < at.sw.y && at.sw.y < at.c1.y);
  });
  it("links each device to the one above it", () => {
    assert.deepEqual(lay.links.map(l => l.from + ">" + l.to).sort(), ["gw>sw", "sw>c1", "sw>c2", "sw>c3", "sw>c4"]);
  });
  it("puts clients in blocks of up to three columns", () => {
    assert.equal(at.c1.y, at.c2.y);
    assert.equal(at.c1.y, at.c3.y);
    assert.ok(at.c4.y > at.c1.y && at.c4.x === at.c1.x);
  });
  it("never draws two nodes on top of each other", () => {
    const seen = new Set(lay.nodes.map(n => n.x + "," + n.y));
    assert.equal(seen.size, lay.nodes.length);
  });
  it("drops devices a filter removes", () => {
    const only = M.layout(data, "dante", "");
    assert.deepEqual(only.nodes.map(n => n.id), []);
    const d2 = { ...data, devices: data.devices.map(d => d.id === "c2" ? { ...d, dante: true } : d) };
    assert.deepEqual(M.layout(d2, "dante", "").nodes.map(n => n.id).sort(), ["c2", "gw", "sw"]);
  });
});

describe("layout without a switch source", () => {
  const data = {
    has_switch_source: false,
    relay_ports: [{ id: "p1", name: "en0", friendly_name: "Ethernet", ipv4: ["10.0.0.5/24"] }, { id: "p2", name: "en5", friendly_name: "", ipv4: [] }],
    devices: [dev("relay", { kind: "relay" }), dev("cam", { via: "p1", ndi: true }), dev("lost")],
  };
  const lay = M.layout(data, "all", "");
  const ids = lay.nodes.map(n => n.id);
  it("draws Relay, its ports, then the devices on each port", () => {
    assert.deepEqual(ids.filter(i => i === "relay" || i.startsWith("port:")).sort(), ["port:other", "port:p1", "port:p2", "relay"]);
    assert.ok(ids.includes("cam") && ids.includes("lost"));
  });
  it("puts a device the ports do not explain under Not placed", () => {
    assert.ok(lay.links.some(l => l.from === "port:other" && l.to === "lost"));
    assert.ok(lay.links.some(l => l.from === "port:p1" && l.to === "cam" && l.ndi));
  });
  it("is never smaller than a phone-sized frame needs", () => assert.ok(lay.width >= 320 && lay.height >= 120));
});

describe("age", () => {
  it("says how old a read is", () => {
    assert.equal(M.age(null), "");
    assert.equal(M.age(2), "just now");
    assert.equal(M.age(30), "30 s ago");
    assert.equal(M.age(600), "10 min ago");
    assert.equal(M.age(7200), "2 h ago");
  });
});

describe("closets", () => {
  const gear = [dev("sw2", { kind: "switch", name: "Stage" }), dev("ap1", { kind: "ap", name: "AP" }), dev("gw", { kind: "gateway", name: "Gateway" }),
                dev("sw1", { kind: "switch", name: "Booth" }), dev("cam")];
  it("with no places, one closet: gateway, switches by name, access points", () => {
    assert.deepEqual(M.closets(gear, []).map((c) => [c.name, c.gear.map((g) => g.id)]), [["Network gear", ["gw", "sw1", "sw2", "ap1"]]]);
  });
  it("draws saved places in order and puts the rest in Not placed", () => {
    const got = M.closets(gear, [{ name: "Stage closet", gear: ["sw2", "gone"] }, { name: "Booth rack", gear: ["gw", "sw1", "sw2"] }]);
    assert.deepEqual(got.map((c) => [c.name, c.gear.map((g) => g.id), c.unplaced]),
      [["Stage closet", ["sw2"], false], ["Booth rack", ["gw", "sw1"], false], ["Not placed", ["ap1"], true]]);
  });
  it("draws nothing without gear", () => assert.deepEqual(M.closets([dev("cam")], []), []));
});

describe("portInfo and keepPort", () => {
  const cam = dev("cam", { mac: "aa:aa:aa:aa:aa:31", name: "Wide", ndi: true });
  const by = { "aa:aa:aa:aa:aa:31": cam };
  it("names the device on a port by its MAC and marks what it carries", () => {
    const i = M.portInfo({ port: 5, up: true, speed: 100, poe_w: 9.6, warn: "Slow", client: "old name", client_mac: cam.mac }, by);
    assert.equal(i.client, "Wide");
    assert.equal(i.cls, "nmport slow ndi poe");
    assert.equal(M.keepPort(i, "ndi"), true);
    assert.equal(M.keepPort(i, "dante"), false);
    assert.equal(M.keepPort(i, "check"), true);
  });
  it("an unknown client keeps the switch's name for it", () => {
    const i = M.portInfo({ port: 2, up: true, client: "Phone" }, by);
    assert.equal(i.client, "Phone");
    assert.equal(i.cls, "nmport up");
    assert.equal(M.keepPort(i, "poe"), false);
  });
  it("counts ports by the highest number", () => assert.equal(M.portCount({ ports: [{ port: 3 }, { port: 16 }] }), 16));
  it("says speeds plainly", () => { assert.equal(M.speedText(1000), "1 Gb/s"); assert.equal(M.speedText(100), "100 Mb/s"); assert.equal(M.speedText(null), ""); });
});
