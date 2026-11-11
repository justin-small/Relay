/*
 * The panel flasher's steps, with esptool-js and the board replaced by a fake.
 * Copied from VXP's tests/js/flash_steps.spec.js. What matters most is in the
 * last block: every refusal happens before the one call that writes.
 *
 * Run: node --test tests/js/
 */

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { describe, it } from "node:test";

import {
  APP_OFFSET,
  DESCRIPTOR_BYTES,
  DESCRIPTOR_MAGIC,
  DESCRIPTOR_OFFSET,
  flashBoard,
  imageDescriptor,
  judgeRunning,
  md5Hex,
  readDescriptor,
  refuseChip,
  refuseImage,
  writeOptions,
} from "../../app/static/flash_steps.js";

const PANEL = { key: "crowpanel-adv7", project: "relay-display-app", chip: "ESP32-S3", flashMb: 16 };

function descriptor(project, version = "0.4.0") {
  const bytes = new Uint8Array(DESCRIPTOR_BYTES);
  new DataView(bytes.buffer).setUint32(0, DESCRIPTOR_MAGIC, true);
  bytes.set(new TextEncoder().encode(version), 0x10);
  bytes.set(new TextEncoder().encode(project), 0x30);
  return bytes;
}

function mergedImage(project) {
  const image = new Uint8Array(APP_OFFSET + 0x400).fill(0xff);
  image.set(descriptor(project), APP_OFFSET + DESCRIPTOR_OFFSET);
  return image;
}

const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");

function manifestFor(board, image) {
  return {
    version: "0.4.0",
    board: board.key,
    sha256: sha256(image),
    size: image.length,
    url: `/api/admin/panels/flash/${board.key}.bin`,
  };
}

describe("readDescriptor", () => {
  it("reads the version and the project", () => {
    assert.deepEqual(readDescriptor(descriptor("relay-display-app", "0.4.0")), {
      version: "0.4.0",
      project: "relay-display-app",
    });
  });

  it("is null for an empty slot, which reads as 0xFF", () => {
    assert.equal(readDescriptor(new Uint8Array(DESCRIPTOR_BYTES).fill(0xff)), null);
  });

  it("is null for too few bytes", () => {
    assert.equal(readDescriptor(new Uint8Array(4)), null);
  });

  it("finds the application's descriptor inside a merged image", () => {
    assert.equal(imageDescriptor(mergedImage(PANEL.project))?.project, PANEL.project);
  });
});

describe("refuseImage", () => {
  const image = mergedImage(PANEL.project);

  it("takes the chosen board's own image", () => {
    assert.equal(refuseImage(PANEL, manifestFor(PANEL, image), image, sha256(image)), null);
  });

  it("refuses a manifest for another board", () => {
    const other = { ...PANEL, key: "waveshare-p4-10.1" };
    assert.equal(refuseImage(other, manifestFor(PANEL, image), image, sha256(image)), "manifest-wrong");
  });

  it("refuses an image whose size is not the manifest's", () => {
    const short = image.subarray(0, image.length - 1);
    assert.equal(refuseImage(PANEL, manifestFor(PANEL, image), short, sha256(short)), "image-size");
  });

  it("refuses an image whose SHA-256 is not the manifest's", () => {
    const tampered = image.slice();
    tampered[APP_OFFSET + 0x200] ^= 0x01;
    assert.equal(refuseImage(PANEL, manifestFor(PANEL, image), tampered, sha256(tampered)), "image-sha");
  });

  it("refuses an image that is not a Relay panel's", () => {
    const vxp = mergedImage("vxp-compact-display-app");
    assert.equal(refuseImage(PANEL, manifestFor(PANEL, vxp), vxp, sha256(vxp)), "image-project");
  });
});

describe("refuseChip", () => {
  it("takes an ESP32-S3 with 16 MB", () => {
    assert.equal(refuseChip(PANEL, "ESP32-S3", "16MB"), null);
  });

  it("refuses another chip", () => {
    assert.equal(refuseChip(PANEL, "ESP32-P4", "16MB"), "wrong-chip");
  });

  it("refuses another flash size, and one it could not read", () => {
    assert.equal(refuseChip(PANEL, "ESP32-S3", "4MB"), "wrong-size");
    assert.equal(refuseChip(PANEL, "ESP32-S3", undefined), "wrong-size");
  });
});

describe("judgeRunning", () => {
  it("knows a Relay panel's firmware", () => {
    assert.equal(judgeRunning(PANEL, { project: PANEL.project }), "same");
  });

  it("asks about a blank board or another vendor's firmware", () => {
    assert.equal(judgeRunning(PANEL, null), "unknown");
    assert.equal(judgeRunning(PANEL, { project: "vxp-compact-display-app" }), "unknown");
  });
});

describe("writeOptions", () => {
  it("writes one file at 0x0, keeps the flash settings, and never erases", () => {
    const options = writeOptions(new Uint8Array(4), md5Hex, () => {});
    assert.equal(options.fileArray.length, 1);
    assert.equal(options.fileArray[0].address, 0);
    assert.equal(options.eraseAll, false);
    assert.deepEqual([options.flashMode, options.flashFreq, options.flashSize], ["keep", "keep", "keep"]);
    assert.equal(options.calculateMD5Hash, md5Hex);
  });
});

describe("md5Hex", () => {
  it("matches node's MD5 across block boundaries", () => {
    for (const length of [0, 1, 55, 56, 63, 64, 65, 1000, 70000]) {
      const data = new Uint8Array(length).map((_, i) => (i * 31 + 7) & 0xff);
      assert.equal(md5Hex(data), createHash("md5").update(data).digest("hex"));
    }
  });
});

describe("flashBoard", () => {
  function world(overrides = {}) {
    const image = mergedImage(PANEL.project);
    const calls = [];
    const io = {
      steps: [],
      step(name) {
        this.steps.push(name);
      },
      fetchManifest: async () => manifestFor(PANEL, image),
      fetchImage: async () => image,
      sha256: async (bytes) => sha256(bytes),
      connect: async () => ({ name: "ESP32-S3", flashSize: "16MB" }),
      readFlash: async () => descriptor(PANEL.project),
      confirmNewBoard: async () => {
        calls.push("confirm");
        return true;
      },
      write: async () => {
        calls.push("write");
      },
      ...overrides,
    };
    return { io, calls };
  }

  it("writes, and is done once the write is checked", async () => {
    const { io, calls } = world();
    assert.deepEqual(await flashBoard(PANEL, io), { code: "done", version: "0.4.0" });
    assert.deepEqual(calls, ["write"]);
    assert.deepEqual(io.steps, ["image", "connect", "check", "write"]);
  });

  const blank = async () => new Uint8Array(DESCRIPTOR_BYTES).fill(0xff);
  const refusals = [
    ["image-missing", { fetchImage: async () => Promise.reject(new Error("404")) }],
    ["image-sha", { sha256: async () => "0".repeat(64) }],
    ["connect-failed", { connect: async () => Promise.reject(new Error("no port")) }],
    ["wrong-chip", { connect: async () => ({ name: "ESP32-P4", flashSize: "16MB" }) }],
    ["wrong-size", { connect: async () => ({ name: "ESP32-S3", flashSize: "8MB" }) }],
    ["read-failed", { readFlash: async () => Promise.reject(new Error("timeout")) }],
    ["cancelled", { readFlash: blank, confirmNewBoard: async () => false }],
  ];

  for (const [code, overrides] of refusals) {
    it(`refuses with ${code} before anything is written`, async () => {
      const { io, calls } = world(overrides);
      assert.equal((await flashBoard(PANEL, io)).code, code);
      assert.ok(!calls.includes("write"));
    });
  }

  it("asks before writing a blank board, and writes when the person agrees", async () => {
    const { io, calls } = world({ readFlash: blank });
    assert.equal((await flashBoard(PANEL, io)).code, "done");
    assert.deepEqual(calls, ["confirm", "write"]);
  });

  it("does not ask about a board already running a Relay panel's firmware", async () => {
    const { io, calls } = world();
    await flashBoard(PANEL, io);
    assert.ok(!calls.includes("confirm"));
  });

  it("reports a write that failed", async () => {
    const { io } = world({ write: async () => Promise.reject(new Error("md5")) });
    assert.equal((await flashBoard(PANEL, io)).code, "write-failed");
  });
});
