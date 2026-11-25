import assert from "node:assert/strict";
import { execFileSync, spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createInterface } from "node:readline";
import { chromium } from "playwright";

const python = ".venv/bin/python";
const workspace = mkdtempSync(join(tmpdir(), "archergate-browser-"));
let server;
let browser;

try {
  execFileSync(python, ["-m", "archergate", "--workspace", workspace, "demo"], { timeout: 90_000 });
  server = spawn(python, ["-m", "archergate", "--workspace", workspace, "serve", "--port", "0"], {
    stdio: ["ignore", "pipe", "inherit"],
  });
  const lines = createInterface({ input: server.stdout });
  const controller = new AbortController();
  const startup = setTimeout(() => controller.abort(), 15_000);
  let address;
  try {
    const [ready] = await once(lines, "line", { signal: controller.signal });
    address = ready.match(/http:\/\/127\.0\.0\.1:\d+$/)?.[0];
    assert.ok(address, ready);
  } finally {
    clearTimeout(startup);
    lines.close();
  }

  for (const edit of ["edit-a", "edit-b"]) {
    const videoUrl = `${address}/video/${edit}.mp4`;
    const original = readFileSync(join(workspace, "renders", `${edit}.mp4`));
    const head = await fetch(videoUrl, { method: "HEAD" });
    assert.equal(head.status, 200);
    assert.equal(head.headers.get("content-type"), "video/mp4");
    assert.equal(head.headers.get("accept-ranges"), "bytes");
    assert.equal(Number(head.headers.get("content-length")), original.length);
    assert.equal((await head.arrayBuffer()).byteLength, 0);
    for (const [range, start, end] of [
      ["bytes=9-29", 9, 29],
      ["bytes=-8", original.length - 8, original.length - 1],
      [`bytes=${original.length - 6}-`, original.length - 6, original.length - 1],
    ]) {
      const response = await fetch(videoUrl, { headers: { Range: range } });
      assert.equal(response.status, 206);
      assert.equal(response.headers.get("content-range"), `bytes ${start}-${end}/${original.length}`);
      assert.deepEqual(Buffer.from(await response.arrayBuffer()), original.subarray(start, end + 1));
    }
    const invalid = await fetch(videoUrl, { headers: { Range: "bytes=999999999-" } });
    assert.equal(invalid.status, 416);
    assert.equal(invalid.headers.get("content-range"), `bytes */${original.length}`);
  }
  for (const route of ["/registry.json", "/sources/signal.mp4", "/video/%2e%2e%2fregistry.json"]) {
    assert.equal((await fetch(address + route)).status, 404);
  }

  const localLibs = resolve(".tools/browser-libs");
  browser = await chromium.launch({
    env: {
      ...process.env,
      LD_LIBRARY_PATH: join(localLibs, "usr/lib/x86_64-linux-gnu"),
      XKB_CONFIG_ROOT: join(localLibs, "usr/share/X11/xkb"),
    },
  });
  const page = await browser.newPage();
  page.setDefaultTimeout(10_000);
  const pageErrors = [];
  page.on("pageerror", error => pageErrors.push(error.message));
  assert.equal((await page.goto(address)).status(), 200);
  await page.getByText("Same 3 source clips, different cut order.").waitFor();
  assert.deepEqual(await page.locator("#edit-a strong").allTextContents(), ["signal", "transit", "echo"]);
  assert.deepEqual(await page.locator("#edit-b strong").allTextContents(), ["echo", "signal", "transit"]);
  assert.equal(await page.getByText("different trim").count(), 4);

  for (const [edit, first, last] of [["edit-a", 0, 2], ["edit-b", 2, 1]]) {
    const selector = `#${edit} video`;
    await page.waitForFunction(selector => document.querySelector(selector).readyState >= 2, selector);
    const video = page.locator(selector);
    const pixel = element => {
      const canvas = document.createElement("canvas");
      const context = canvas.getContext("2d");
      context.drawImage(element, 0, 0);
      const rgb = Array.from(context.getImageData(10, 10, 1, 1).data).slice(0, 3);
      return rgb.indexOf(Math.max(...rgb));
    };
    assert.equal(await video.evaluate(pixel), first);
    await video.evaluate(element => {
      element.muted = true;
      return element.play();
    });
    await page.waitForFunction(selector => {
      const video = document.querySelector(selector);
      return video.currentTime > 0.25 && video.getVideoPlaybackQuality().totalVideoFrames >= 3;
    }, selector);
    await video.evaluate(element => {
      element.pause();
      element.currentTime = 4.5;
    });
    await page.waitForFunction(selector => {
      const video = document.querySelector(selector);
      return !video.seeking && video.currentTime === 4.5 && video.readyState >= 2;
    }, selector);
    assert.equal(await video.evaluate(pixel), last);
    const properties = await video.evaluate(element => ({
      duration: element.duration, width: element.videoWidth, height: element.videoHeight, error: element.error,
    }));
    assert.deepEqual(properties, { duration: 6, width: 640, height: 360, error: null });
    console.log(`Chrome ${browser.version()} played and sought ${edit}.mp4 (decoded frames confirmed)`);
  }
  assert.deepEqual(pageErrors, []);
  console.log("HTTP range bytes, range errors and private file routes verified");
} finally {
  try {
    if (browser) await browser.close();
    if (server && server.exitCode === null && server.signalCode === null) {
      server.kill();
      await once(server, "exit");
    }
  } finally {
    rmSync(workspace, { recursive: true, force: true });
  }
}
