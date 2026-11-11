/*
 * The panel flasher's steps, without the browser. Copied from VXP's board
 * flasher (app/static/flash_steps.js there, #1546) and cut down to Relay's
 * panels.
 *
 * `/admin/flash` writes a panel's whole image at 0x0 over Web Serial with
 * esptool-js. This file is the order of that job and every check that can
 * refuse it. `flasher.js` gives it a port, esptool-js and the page; the tests
 * in `tests/js/flash_steps.test.mjs` give it fakes, so every refusal is proved
 * to happen before the one call that writes.
 *
 * The result is a code and never a sentence. Every sentence the page can show
 * is in the HTML the server sent, and `flasher.js` only chooses which one is
 * visible.
 *
 * **It never erases.** There is no erase call in this file or in flasher.js.
 * `nvs` sits above both application slots (firmware/relay-display/
 * partitions.csv), so the merged image cannot reach it, and a panel keeps its
 * Wi-Fi, its ID and its settings. A factory reset is the panel's own, not this
 * page's.
 *
 * What differs from VXP: every Relay board's firmware has the same project
 * name (`relay-display-app`), and what tells the boards apart is their chip,
 * so a panel running another board's firmware is already refused at the chip.
 */

/** Where the first application slot starts, and where a merged image carries the application. */
export const APP_OFFSET = 0x10000;

/**
 * The application descriptor's place inside an application image: after the
 * 24-byte image header and the first segment's 8-byte header.
 */
export const DESCRIPTOR_OFFSET = 0x20;

/** `ESP_APP_DESC_MAGIC_WORD`, little-endian in the image. */
export const DESCRIPTOR_MAGIC = 0xabcd5432;

/** How much of the descriptor this reads: magic, secure version, reserved, version and project. */
export const DESCRIPTOR_BYTES = 0x50;

/**
 * A NUL-terminated string field of the descriptor, as text.
 *
 * @param {Uint8Array} bytes
 * @param {number} start
 * @param {number} length
 * @returns {string}
 */
function cString(bytes, start, length) {
  let text = "";
  for (let i = start; i < start + length && i < bytes.length; i += 1) {
    const code = bytes[i];
    if (code === 0) break;
    text += String.fromCharCode(code);
  }
  return text;
}

/**
 * The version and project an application descriptor names, or null when the
 * bytes are not one (an empty slot reads as 0xFF, and another vendor's image
 * may carry no descriptor at all).
 *
 * @param {Uint8Array} bytes The descriptor, from its magic word on.
 * @returns {{version: string, project: string} | null}
 */
export function readDescriptor(bytes) {
  if (!bytes || bytes.length < DESCRIPTOR_BYTES) return null;
  const magic = (bytes[0] | (bytes[1] << 8) | (bytes[2] << 16) | (bytes[3] << 24)) >>> 0;
  if (magic !== DESCRIPTOR_MAGIC) return null;
  return { version: cString(bytes, 0x10, 32), project: cString(bytes, 0x30, 32) };
}

/**
 * The descriptor of the application inside a merged image.
 *
 * @param {Uint8Array} image
 * @returns {{version: string, project: string} | null}
 */
export function imageDescriptor(image) {
  const start = APP_OFFSET + DESCRIPTOR_OFFSET;
  return readDescriptor(image.subarray(start, start + DESCRIPTOR_BYTES));
}

/**
 * Why the manifest and the image the server sent are not the chosen board's,
 * or null when they are. Checked before the board is touched.
 *
 * @param {{key: string, project: string}} board
 * @param {any} manifest
 * @param {Uint8Array} image
 * @param {string} sha256 The image's SHA-256, lowercase hex.
 * @returns {string | null}
 */
export function refuseImage(board, manifest, image, sha256) {
  if (
    !manifest ||
    manifest.board !== board.key ||
    manifest.url !== `/api/admin/panels/flash/${board.key}.bin`
  ) {
    return "manifest-wrong";
  }
  if (image.length !== manifest.size) return "image-size";
  if (sha256 !== String(manifest.sha256).toLowerCase()) return "image-sha";
  const descriptor = imageDescriptor(image);
  if (!descriptor || descriptor.project !== board.project) return "image-project";
  return null;
}

/**
 * Why the connected chip is not one this board's image may go on, or null.
 *
 * @param {{chip: string, flashMb: number}} board
 * @param {string} chipName esptool-js's `chip.CHIP_NAME`.
 * @param {string | undefined} flashSize esptool-js's `detectFlashSize()`, as "16MB".
 * @returns {string | null}
 */
export function refuseChip(board, chipName, flashSize) {
  if (chipName !== board.chip) return "wrong-chip";
  if (flashSize !== `${board.flashMb}MB`) return "wrong-size";
  return null;
}

/**
 * What the board already runs. A Relay panel's firmware is "same" (a
 * reinstall or a recovery, written without asking); a blank board or another
 * vendor's firmware is a new board, which is what this page is mostly for, so
 * it asks first.
 *
 * @param {{project: string}} board
 * @param {{project: string} | null} running
 * @returns {"same" | "unknown"}
 */
export function judgeRunning(board, running) {
  return running && running.project === board.project ? "same" : "unknown";
}

/**
 * The options for the one write. Fixed rather than chosen: one file at 0x0,
 * the flash settings the image already carries, and never an erase.
 *
 * @param {Uint8Array} image
 * @param {(data: Uint8Array) => string} md5
 * @param {(written: number, total: number) => void} progress
 */
export function writeOptions(image, md5, progress) {
  return {
    fileArray: [{ data: image, address: 0x0 }],
    flashMode: "keep",
    flashFreq: "keep",
    flashSize: "keep",
    eraseAll: false,
    compress: true,
    reportProgress: (_index, written, total) => progress(written, total),
    calculateMD5Hash: md5,
  };
}

/**
 * The whole job, in order. `io` is the world: the server, the board and the
 * person. Returns `{code, ...}`; `code` is "done" once the write is checked,
 * and the name of the refusal or failure otherwise.
 *
 * Every refusal comes before `io.write`, which is the only call that changes
 * the board.
 *
 * @param {{key: string, project: string, chip: string, flashMb: number}} board
 * @param {any} io
 */
export async function flashBoard(board, io) {
  io.step("image");
  let manifest;
  let image;
  try {
    manifest = await io.fetchManifest(`/api/admin/panels/flash/${board.key}.json`);
    image = await io.fetchImage(`/api/admin/panels/flash/${board.key}.bin`);
  } catch {
    return { code: "image-missing" };
  }
  const refusedImage = refuseImage(board, manifest, image, await io.sha256(image));
  if (refusedImage) return { code: refusedImage };

  io.step("connect");
  let chip;
  try {
    chip = await io.connect();
  } catch {
    return { code: "connect-failed" };
  }
  const refusedChip = refuseChip(board, chip.name, chip.flashSize);
  if (refusedChip) return { code: refusedChip, chip };

  io.step("check");
  let running = null;
  try {
    running = readDescriptor(
      await io.readFlash(APP_OFFSET + DESCRIPTOR_OFFSET, DESCRIPTOR_BYTES),
    );
  } catch {
    return { code: "read-failed" };
  }
  if (judgeRunning(board, running) === "unknown" && !(await io.confirmNewBoard())) {
    return { code: "cancelled" };
  }

  io.step("write");
  try {
    await io.write(image);
  } catch {
    return { code: "write-failed" };
  }

  // The write is checked (esptool-js compares the MD5 the chip computes), so the job ends here. The page
  // does not reset the panel or wait for it to start: on the CrowPanel the reset line from USB does not
  // restart it, and its boot log did not reach the page (bench, 2026-11-11). The person presses RESET.
  return { code: "done", version: manifest.version };
}

/* MD5, which esptool-js asks for to verify the write and which Web Crypto does not offer. */

const MD5_SHIFTS = [
  7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14,
  20, 5, 9, 14, 20, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 6, 10, 15, 21, 6,
  10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
];

const MD5_K = Array.from({ length: 64 }, (_, i) =>
  Math.floor(Math.abs(Math.sin(i + 1)) * 2 ** 32) >>> 0,
);

/**
 * The MD5 of the bytes, as lowercase hex.
 *
 * @param {Uint8Array} data
 * @returns {string}
 */
export function md5Hex(data) {
  const length = data.length;
  const padded = new Uint8Array((((length + 8) >> 6) + 1) << 6);
  padded.set(data);
  padded[length] = 0x80;
  const view = new DataView(padded.buffer);
  view.setUint32(padded.length - 8, (length * 8) >>> 0, true);
  view.setUint32(padded.length - 4, Math.floor(length / 0x20000000), true);

  let a0 = 0x67452301;
  let b0 = 0xefcdab89;
  let c0 = 0x98badcfe;
  let d0 = 0x10325476;
  const m = new Uint32Array(16);
  for (let offset = 0; offset < padded.length; offset += 64) {
    for (let j = 0; j < 16; j += 1) m[j] = view.getUint32(offset + j * 4, true);
    let a = a0;
    let b = b0;
    let c = c0;
    let d = d0;
    for (let i = 0; i < 64; i += 1) {
      let f;
      let g;
      if (i < 16) {
        f = (b & c) | (~b & d);
        g = i;
      } else if (i < 32) {
        f = (d & b) | (~d & c);
        g = (5 * i + 1) % 16;
      } else if (i < 48) {
        f = b ^ c ^ d;
        g = (3 * i + 5) % 16;
      } else {
        f = c ^ (b | ~d);
        g = (7 * i) % 16;
      }
      const sum = (a + f + MD5_K[i] + m[g]) >>> 0;
      a = d;
      d = c;
      c = b;
      b = (b + ((sum << MD5_SHIFTS[i]) | (sum >>> (32 - MD5_SHIFTS[i])))) >>> 0;
    }
    a0 = (a0 + a) >>> 0;
    b0 = (b0 + b) >>> 0;
    c0 = (c0 + c) >>> 0;
    d0 = (d0 + d) >>> 0;
  }
  let hex = "";
  for (const word of [a0, b0, c0, d0]) {
    for (let k = 0; k < 4; k += 1) hex += ((word >>> (k * 8)) & 0xff).toString(16).padStart(2, "0");
  }
  return hex;
}
