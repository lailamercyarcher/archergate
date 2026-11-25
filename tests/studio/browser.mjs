import assert from "node:assert/strict";
import { execFileSync, spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createInterface } from "node:readline";
import { chromium } from "playwright";

const python = process.env.ARCHERGATE_TEST_PYTHON || ".venv/bin/python";
const workspace = mkdtempSync(join(tmpdir(), "archergate-studio-"));
let processServer, browser;
try {
  const cli = ["-m", "archergate.studio", "--workspace", workspace];
  execFileSync(python, [...cli, "demo"], {timeout: 60_000});
  processServer = spawn(python, [...cli, "serve", "--port", "0"]);
  const lines = createInterface({input: processServer.stdout});
  const [line] = await once(lines, "line", {signal: AbortSignal.timeout(15_000)});
  lines.close();
  const url = line.match(/http:\/\/127\.0\.0\.1:[0-9]+/)?.[0];
  assert.ok(url, line);
  const libs = resolve(".tools/browser-libs");
  const env = process.platform === "linux" ? {
    ...process.env, LD_LIBRARY_PATH: join(libs, "usr/lib/x86_64-linux-gnu"), XKB_CONFIG_ROOT: join(libs, "usr/share/X11/xkb"),
  } : process.env;
  browser = await chromium.launch({env});
  const page = await browser.newPage({acceptDownloads: true});
  page.setDefaultTimeout(10_000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  assert.equal((await page.goto(url)).status(), 200);
  await page.getByRole("button", {name: "Synthetic Interview.mp4", exact: true}).click();
  await page.waitForFunction(() => document.querySelector("video")?.readyState >= 2);
  await page.locator("video").evaluate(video => { video.muted = true; return video.play(); });
  await page.waitForFunction(() => document.querySelector("video").getVideoPlaybackQuality().totalVideoFrames >= 3);
  await page.locator("video").evaluate(video => video.pause());
  await page.getByRole("button", {name: /1\.00 s · Scene score/}).click();
  await page.waitForFunction(() => {
    const video = document.querySelector("video");
    return !video.seeking && video.currentTime === 1 && video.readyState >= 2;
  });
  await page.getByRole("button", {name: "Synthetic Music.mp3", exact: true}).click();
  await page.waitForFunction(() => document.querySelector("audio")?.readyState >= 2);
  await page.locator("audio").evaluate(audio => { audio.muted = true; return audio.play(); });
  await page.waitForFunction(() => document.querySelector("audio").currentTime > 0.15);
  await page.getByRole("button", {name: "Compare edits"}).click();
  await page.getByRole("status").getByText("1 changed clip.").waitFor();
  assert.equal(await page.locator("#edit-details article").count(), 2);
  await page.getByRole("button", {name: "Render A/B"}).click();
  const rendered = page.locator("#sequence-preview video");
  await page.waitForFunction(() => document.querySelector("#sequence-preview video")?.readyState >= 2);
  await rendered.evaluate(video => { video.currentTime = 1.25; });
  await page.waitForFunction(() => !document.querySelector("#sequence-preview video").seeking);
  const sourceA = await rendered.getAttribute("src");
  await page.getByRole("button", {name: "Show B"}).click();
  await page.waitForFunction(() => {
    const video = document.querySelector("#sequence-preview video");
    return video.readyState >= 2 && !video.seeking && Math.abs(video.currentTime - 1.25) < 0.04;
  });
  assert.notEqual(await rendered.getAttribute("src"), sourceA);
  await page.getByRole("button", {name: "Show A"}).click();
  assert.equal(await rendered.getAttribute("src"), sourceA);
  await page.getByRole("button", {name: "Show B"}).click();
  await page.getByRole("button", {name: "Reject B"}).click();
  await page.getByText("Revision rejected.", {exact: true}).waitFor();
  assert.equal(await rendered.getAttribute("src"), sourceA);
  await page.getByRole("button", {name: "Review proposed edit"}).click();
  await page.locator("#review").getByText(/J-cut/).waitFor();
  const download = page.waitForEvent("download");
  await page.getByRole("button", {name: "Export Premiere XML"}).click();
  const downloaded = await download;
  assert.equal(downloaded.suggestedFilename(), "interview-j.xml");
  const xml = readFileSync(await downloaded.path(), "utf8");
  assert.match(xml, /<xmeml version="5">/);
  assert.match(xml, /Synthetic%20Dialogue\.wav/);
  await page.getByLabel("Category").selectOption("suggestion");
  await page.getByLabel("Title", {exact: true}).fill("Keep the pauses");
  await page.getByLabel("Creative direction or notes").fill("Carry room tone before the picture cut.");
  await page.getByLabel("Suggestion decision").selectOption("accepted");
  await page.getByRole("button", {name: "Save record"}).click();
  await page.getByRole("button", {name: "suggestion · Keep the pauses"}).waitFor();
  await page.reload();
  await page.getByRole("button", {name: "suggestion · Keep the pauses"}).click();
  assert.equal(await page.getByLabel("Suggestion decision").inputValue(), "accepted");
  await page.getByLabel("Suggestion decision").selectOption("modified");
  await page.getByLabel("Modified suggestion").fill("Hold an extra breath.");
  await page.getByRole("button", {name: "Save record"}).click();
  await page.getByText("accepted → modified · Hold an extra breath.").waitFor();
  await page.getByLabel("Local file path").fill(join(workspace, "synthetic", "Synthetic Dialogue.wav"));
  await page.getByLabel("Provenance", {exact: true}).fill("Synthetic browser import");
  await page.getByLabel("Rights note").fill("Locally generated");
  await page.getByRole("button", {name: "Import media"}).click();
  try {
    // Speech inference can share a CPU with the Windows build.
    await page.getByText("Media imported.", {exact: true}).waitFor({timeout: 60_000});
  } catch (error) {
    throw new Error(`Browser import ended at: ${await page.locator("#status").textContent()}`, {cause: error});
  }
  const speechConfigured = ["ARCHERGATE_TEST_SPEECH_WAV", "ARCHERGATE_WHISPER", "ARCHERGATE_WHISPER_MODEL"]
    .every(key => process.env[key]);
  if (speechConfigured) {
    await page.getByRole("button", {name: /s · .*documentary/i}).first().click();
    await page.waitForFunction(() => document.querySelector("audio")?.readyState >= 2);
  } else {
    await page.getByText(/unavailable · Configure a local whisper.cpp/).waitFor();
  }
  const denied = await fetch(url + "/api/memory", {method: "POST", body: "{}"});
  assert.equal(denied.status, 403);
  assert.equal((await fetch(url + "/studio.sqlite3")).status, 404);
  assert.deepEqual(errors, []);
  console.log(`Chrome ${browser.version()}: video playback, shot seeking, audio playback, comparison, XML download and creative records verified`);
} finally {
  if (browser) await browser.close();
  if (processServer && processServer.exitCode === null && processServer.signalCode === null) {
    processServer.kill();
    await once(processServer, "exit");
  }
  rmSync(workspace, {recursive: true, force: true});
}
