/*
 * The Intercom tab's plain helpers (#240): how a talk answer is worded, which rooms a phone joins, the backoff.
 * The page itself needs a browser and LiveKit; these have neither.
 *
 * Run: node --test tests/js/
 */

import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { describe, it } from "node:test";

const require = createRequire(import.meta.url);
const L = require("../../app/modules/intercom/static/crew.js");

describe("reconnect backoff", () => {
  it("is 1, 2, 4, 8 seconds and then stays at 8", () => {
    assert.deepEqual([0, 1, 2, 3, 4, 10].map(L.backoff), [1, 2, 4, 8, 8, 8]);
  });
});

describe("what a channel says", () => {
  it("names who is talking", () => {
    assert.equal(L.activityText({ talkers: [{ name: "Jen" }] }), "Jen talking");
    assert.equal(L.activityText({ talkers: [{ name: "Jen" }, { name: "Sam" }] }), "Jen, Sam talking");
  });
  it("is quiet, with the listeners when known", () => {
    assert.equal(L.activityText({ talkers: [], listening: 4 }), "Quiet · 4 listening");
    assert.equal(L.activityText(undefined), "Quiet");
  });
});

describe("a press across channels", () => {
  it("shows talking, waiting, cut off and refused side by side, each in words", () => {
    const r = {};
    L.applyTalkMessage(r, { type: "talk.granted", channel: 1 });
    L.applyTalkMessage(r, { type: "talk.queued", channel: 2, position: 2 });
    L.applyTalkMessage(r, { type: "talk.preempted", channel: 3, by: "Director" });
    L.applyTalkMessage(r, { type: "talk.denied", channel: 4, reason: "busy" });
    assert.equal(L.resultText(r[1]), "Talking");
    assert.equal(L.resultText(r[2]), "Waiting · you're 2nd in line");
    assert.equal(L.resultText(r[3]), "Cut off · Director has priority");
    assert.match(L.resultText(r[4]), /^Busy/);
    assert.equal(L.holding(r), true);
    assert.equal(L.busy(r), true);
  });
  it("is not holding once every lease ended", () => {
    const r = {};
    L.applyTalkMessage(r, { type: "talk.granted", channel: 1 });
    L.applyTalkMessage(r, { type: "talk.released", channel: 1, reason: "expired" });
    assert.equal(L.holding(r), false);
    assert.equal(L.busy(r), false);
    assert.equal(L.resultText(r[1]), "Lost the link");
  });
  it("ignores messages that are not about talking", () => {
    assert.equal(L.applyTalkMessage({}, { type: "pong" }), false);
  });
  it("says 1st, 2nd, 3rd, 11th and 22nd", () => {
    assert.deepEqual([1, 2, 3, 4, 11, 12, 13, 21, 22].map(L.ordinal), ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd"]);
  });
});

describe("which rooms the phone is in", () => {
  const chans = [
    { id: 1, can_listen: 1, can_talk: 1, listen_selected: 1, talk_selected: 0 },
    { id: 2, can_listen: 1, can_talk: 1, listen_selected: 0, talk_selected: 1 },   // talk only
    { id: 3, can_listen: 1, can_talk: 0, listen_selected: 0, talk_selected: 1 },   // no talk right: not armed
    { id: 4, can_listen: 0, can_talk: 1, listen_selected: 1, talk_selected: 0 },   // no listen right
  ];
  it("joins listened and talk-only channels, never ones it has no right to", () => {
    assert.deepEqual(L.wantedRooms(chans), [1, 2]);
  });
  it("talks only to channels with Talk selected and a talk right", () => {
    assert.deepEqual(L.talkTargets(chans), [2]);
  });
});

describe("the phone's name and headset", () => {
  it("is named from its browser", () => {
    assert.equal(L.guessName("Mozilla/5.0 (iPhone; CPU iPhone OS 17) AppleWebKit Safari/604"), "iPhone Safari");
    assert.equal(L.guessName("Mozilla/5.0 (Linux; Android 14) Chrome/120 Mobile Safari/537"), "Android phone Chrome");
  });
  it("tells a headset from the phone's own microphone", () => {
    assert.equal(L.isHeadset("AirPods Pro"), true);
    assert.equal(L.isHeadset("iPhone Microphone"), false);
  });
});
