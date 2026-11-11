/*
 * The panel flasher's page: Web Serial, esptool-js and the page. Copied from
 * VXP's board flasher (app/static/flasher.js there, #1546).
 *
 * `flash_steps.js` decides the order and every refusal. This file gives it the
 * world: the image Relay holds, a port the person picked in the browser's own
 * chooser, esptool-js on that port, and the page. It also uploads the merged
 * image (`relay-display.bin`) the page writes.
 *
 * Every sentence the page can show is in the HTML the server sent, and this
 * file only chooses which one is visible and fills in a version, a chip or a
 * percentage. The one text it writes is Relay's own sentence when it refuses an
 * upload.
 *
 * **It never erases.**
 */

import { ESPLoader, Transport } from "./vendor/esptool-js/esptool.js";
import { flashBoard, md5Hex, writeOptions } from "./flash_steps.js";

// 115200 for the write, the ROM's own rate. VXP writes at 921600, but the
// CrowPanel's USB port is a CH340 bridge, and on the bench it dropped off a Mac
// at the baud change already at 460800 ("Device not configured"). A write at
// the ROM's own rate never changes the baud: slower (about two minutes for the
// whole image), and it finishes.
const FLASH_BAUD = 115200;
const ROM_BAUD = 115200;

function show(root, selector, on) {
  for (const el of root.querySelectorAll(selector)) el.hidden = !on;
}

function fill(root, slot, text) {
  for (const el of root.querySelectorAll(`[data-slot="${slot}"]`)) el.textContent = text;
}

async function sha256Hex(bytes) {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return Array.from(digest, (b) => b.toString(16).padStart(2, "0")).join("");
}

async function fetchOk(url) {
  const response = await fetch(url, { cache: "no-store", credentials: "same-origin" });
  if (!response.ok) throw new Error(`${url}: ${response.status}`);
  return response;
}

function startFlasher(root) {
  const board = {
    key: root.dataset.board,
    project: root.dataset.project,
    chip: root.dataset.chip,
    flashMb: Number(root.dataset.flashMb),
  };
  const button = root.querySelector("[data-flash-start]");
  const progress = root.querySelector("[data-flash-progress]");
  const confirmBox = root.querySelector("[data-flash-confirm]");

  if (!("serial" in navigator)) return;
  show(root, "[data-flash-unsupported]", false);
  if (!button) return;
  button.hidden = false;

  button.addEventListener("click", async () => {
    let port;
    try {
      // In the click itself: the chooser needs the gesture.
      port = await navigator.serial.requestPort();
    } catch {
      return;
    }
    button.disabled = true;
    show(root, "[data-result]", false);
    show(root, "[data-flash-steps]", true);
    const transport = new Transport(port, false);
    let loader = null;

    const io = {
      step(name) {
        for (const li of root.querySelectorAll("[data-step]")) {
          const on = li.dataset.step === name;
          li.classList.toggle("on", on);
          if (on) li.setAttribute("aria-current", "step");
          else li.removeAttribute("aria-current");
        }
      },
      fetchManifest: async (url) => (await fetchOk(url)).json(),
      fetchImage: async (url) => new Uint8Array(await (await fetchOk(url)).arrayBuffer()),
      sha256: sha256Hex,
      async connect() {
        loader = new ESPLoader({ transport, baudrate: FLASH_BAUD, romBaudrate: ROM_BAUD });
        await loader.main();
        const chip = { name: loader.chip.CHIP_NAME, flashSize: await loader.detectFlashSize() };
        fill(root, "chip", `${chip.name}, ${chip.flashSize || "unknown flash"}`);
        return chip;
      },
      readFlash: (addr, size) => loader.readFlash(addr, size),
      confirmNewBoard() {
        return new Promise((resolve) => {
          confirmBox.hidden = false;
          const yes = confirmBox.querySelector("[data-flash-yes]");
          const no = confirmBox.querySelector("[data-flash-no]");
          const done = (answer) => {
            confirmBox.hidden = true;
            yes.removeEventListener("click", onYes);
            no.removeEventListener("click", onNo);
            resolve(answer);
          };
          const onYes = () => done(true);
          const onNo = () => done(false);
          yes.addEventListener("click", onYes);
          no.addEventListener("click", onNo);
          yes.focus();
        });
      },
      async write(image) {
        progress.hidden = false;
        progress.value = 0;
        await loader.writeFlash(
          writeOptions(image, md5Hex, (written, total) => {
            progress.value = total ? Math.round((written / total) * 100) : 0;
            fill(root, "percent", `${progress.value}%`);
          }),
        );
      },
    };

    let result;
    try {
      result = await flashBoard(board, io);
    } catch {
      result = { code: "write-failed" };
    }
    try {
      await transport.disconnect();
    } catch {
      // Already closed.
    }
    if (result.version) fill(root, "version", result.version);
    io.step("");
    progress.hidden = true;
    const shown = root.querySelector(`[data-result="${result.code}"]`);
    if (shown) {
      shown.hidden = false;
      shown.focus();
    }
    button.disabled = false;
  });
}

/** The merged image's upload and removal. A success reloads the page, which the server draws again. */
function startUpload(root) {
  const board = root.dataset.board;
  const file = root.querySelector("[data-upload-file]");
  const error = root.querySelector("[data-upload-error]");
  const fail = (text) => {
    error.textContent = text;
    error.hidden = false;
  };
  const send = async (method, body, filename) => {
    error.hidden = true;
    let url = `/api/admin/panels/flash?board=${encodeURIComponent(board)}`;
    if (filename) url += `&filename=${encodeURIComponent(filename)}`;
    const response = await fetch(url, {
      method,
      body,
      cache: "no-store",
      credentials: "same-origin",
      headers: body ? { "Content-Type": "application/octet-stream" } : {},
    });
    const reply = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(reply.detail || `HTTP ${response.status}`);
    window.location.reload();
  };
  root.querySelector("[data-upload]")?.addEventListener("click", async () => {
    const chosen = file.files[0];
    if (!chosen) return fail("Choose relay-display.bin first.");
    try {
      await send("POST", chosen, chosen.name);
    } catch (e) {
      fail(e.message);
    }
  });
  root.querySelector("[data-upload-remove]")?.addEventListener("click", async () => {
    try {
      await send("DELETE");
    } catch (e) {
      fail(e.message);
    }
  });
}

for (const root of document.querySelectorAll("[data-flasher]")) startFlasher(root);
for (const root of document.querySelectorAll("[data-flash-upload]")) startUpload(root);
