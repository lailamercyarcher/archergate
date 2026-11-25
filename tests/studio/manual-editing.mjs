import assert from "node:assert/strict";
import {execFileSync, spawn} from "node:child_process";
import {createHash, randomBytes} from "node:crypto";
import {copyFileSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from "node:fs";
import {once} from "node:events";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {createInterface} from "node:readline";
import {chromium} from "playwright";

const python = process.env.ARCHERGATE_TEST_PYTHON || ".venv/bin/python";
const root = mkdtempSync(join(tmpdir(), "archergate-manual-edit-"));
const workspace = join(root, "workspace");
const fixtureFolder = join(root, "fixtures");
const generated = JSON.parse(execFileSync(python, ["-c", `
import json, sys
from pathlib import Path
from archergate.studio.fixtures import make_sources
print(json.dumps([str(path) for path in make_sources(Path(sys.argv[1]))]))
`, fixtureFolder], {encoding: "utf8"}));
const videoPath = join(root, "Scene lumière.mov");
const audioPath = join(root, "Musique café.mp3");
execFileSync(python, ["-c", `
import sys
from pathlib import Path
from archergate.studio.library import ffmpeg
ffmpeg("-i", Path(sys.argv[1]), "-i", Path(sys.argv[2]), "-map", "0:v:0", "-map", "1:a:0",
       "-c:v", "copy", "-c:a", "pcm_s16le", "-shortest", Path(sys.argv[3]))
`, generated[0], generated[2], videoPath]);
copyFileSync(generated[3], audioPath);
const hashes = [videoPath, audioPath].map(hashFile);
const malformedPath = join(root, "broken video.mp4");
const unsupportedPath = join(root, "notes.txt");
writeFileSync(malformedPath, "not a media stream");
writeFileSync(unsupportedPath, "unsupported format");
const token = randomBytes(32).toString("hex");
const server = spawn(python, ["-m", "archergate.studio", "--workspace", workspace, "serve", "--port", "0", "--desktop"],
  {env: {...process.env, ARCHERGATE_SESSION_TOKEN: token}, stdio: ["pipe", "pipe", "inherit"]});
let browser;

function hashFile(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function decodeRendered(path, frameBeforeTrim, trimmedFrame) {
  const code = `
import json, struct, sys
from pathlib import Path
from archergate.studio.library import ffmpeg, input_args, probe
path = Path(sys.argv[1])
frame_before_trim, trimmed_frame = map(int, sys.argv[2:4])
def frame(index):
    raw = ffmpeg(*input_args(path), "-vf", rf"select=eq(n\\,{index}),scale=1:1,format=gray",
                 "-fps_mode", "vfr", "-frames:v", "1", "-f", "rawvideo", "-")
    return raw[0] if raw else None
def tone_frequency(start):
    raw = ffmpeg("-ss", str(start), *input_args(path), "-t", "0.5", "-vn",
                 "-ac", "1", "-ar", "48000", "-f", "s16le", "-")
    samples = [sample for (sample,) in struct.iter_unpack("<h", raw)]
    crossings = sum((left < 0) != (right < 0) for left, right in zip(samples, samples[1:]))
    return crossings / (2 * (len(samples) - 1) / 48000)
ffmpeg(*input_args(path), "-map", "0", "-f", "null", "-")
print(json.dumps({"duration": probe(path)["duration"],
                 "frame_before_trim": frame(frame_before_trim), "trimmed_frame": frame(trimmed_frame),
                  "first_tone_hz": tone_frequency(0.25), "inserted_tone_hz": tone_frequency(3.25)}))
`;
 return JSON.parse(execFileSync(python, ["-c", code, path, String(frameBeforeTrim), String(trimmedFrame)], {encoding: "utf8"}));
}

try {
  const lines = createInterface({input: server.stdout});
  const [line] = await once(lines, "line", {signal: AbortSignal.timeout(15_000)});
  lines.close();
  const origin = line.match(/http:\/\/127\.0\.0\.1:[0-9]+/)?.[0];
  assert.ok(origin, line);
  const libs = join(process.cwd(), ".tools/browser-libs");
  const env = process.platform === "linux" ? {
    ...process.env, LD_LIBRARY_PATH: join(libs, "usr/lib/x86_64-linux-gnu"),
    XKB_CONFIG_ROOT: join(libs, "usr/share/X11/xkb"),
  } : process.env;
  browser = await chromium.launch({env});
  const page = await browser.newPage({acceptDownloads: true});
  page.setDefaultTimeout(20_000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(({workspacePath, selectedPaths}) => {
    window.__AG_TEST_PATHS = [...selectedPaths];
    window.__TAURI__ = {core: {invoke: async command => {
      if (command === "workspace") return workspacePath;
      if (command === "pick_media") return window.__AG_TEST_PATHS.splice(0);
      if (command === "choose_workspace") return null;
      throw new Error(`Unexpected desktop bridge command: ${command}`);
    }}};
  }, {workspacePath: workspace, selectedPaths: [videoPath, audioPath]});
  const pageResponse = await page.goto(`${origin}/desktop-session/${token}`);
  assert.equal(pageResponse.status(), 200);
  await page.locator("#desktop-controls:not([hidden])").waitFor();
  await page.evaluate(async () => { await document.fonts.ready; return true; });
  assert.equal(await page.evaluate(() => document.fonts.check("17px Plex")), true);
  assert.deepEqual(await page.evaluate(async fonts =>
    Promise.all(fonts.map(async font => (await fetch(`/${font}`)).status)),
  ["IBMPlexSans-Regular.woff2", "IBMPlexSans-SemiBold.woff2"]), [200, 200]);

  await page.getByRole("navigation", {name: "workspace"}).getByRole("link", {name: "materials"}).click();
  await page.getByRole("button", {name: "+ import"}).click();
  await page.getByRole("status").getByText("2 files selected.", {exact: true}).waitFor();
  await page.locator("#import [name=provenance]").fill("Synthetic manual-edit test");
  await page.locator("#import [name=rights]").fill("Generated locally for testing");
  await page.getByRole("button", {name: "import selected footage"}).click();
  await page.getByRole("status").getByText("media imported and available for playback", {exact: true}).waitFor({timeout: 60_000});
  await page.getByRole("button", {name: "Scene lumière.mov", exact: true}).click();
  await page.waitForFunction(() => document.querySelector("#asset-details video")?.readyState >= 2);
  await page.getByRole("button", {name: "start sequence"}).click();
  await page.getByRole("status").getByText("Sequence ready.", {exact: true}).waitFor({timeout: 60_000});
  assert.equal(await page.locator("#destination-track").inputValue(), "video");
  assert.equal(await page.locator("#destination-track option").count(), 2);
  const baseId = await page.locator("#edit").inputValue();
  assert.equal(await page.locator("#playhead-tick").inputValue(), "72");
  assert.equal(await page.locator("#playhead-timecode").textContent(), "00:00:03:00 · 3.00 s");

  await page.getByRole("navigation", {name: "workspace"}).getByRole("link", {name: "materials"}).click();
  await page.getByRole("button", {name: "Scene lumière.mov", exact: true}).click();
  await page.getByRole("heading", {name: "Scene lumière.mov", exact: true}).waitFor();
  await page.waitForFunction(() => document.querySelector("#asset-details video")?.readyState >= 2);
  const sourceVideo = page.locator("#asset-details video");
  const sourceFrameCount = await page.evaluate(async () => {
    const records = await (await fetch("/api/assets")).json();
    const record = records.find(asset => asset.name === "Scene lumière.mov");
    const asset = await (await fetch(`/api/asset/${record.id}`)).json();
    const stream = asset.source.streams.video;
    const ratio = value => {
      const [numerator, denominator = "1"] = String(value).split("/");
      return [BigInt(numerator), BigInt(denominator)];
    };
    const [startNumerator, startDenominator] = ratio(stream.start);
    const [durationNumerator, durationDenominator] = ratio(stream.duration);
    const [fpsNumerator, fpsDenominator] = ratio(stream.avg_frame_rate);
    const floorDiv = (numerator, denominator) => numerator / denominator;
    const ceilDiv = (numerator, denominator) => (numerator + denominator - 1n) / denominator;
    const startFrame = ceilDiv(startNumerator * fpsNumerator, startDenominator * fpsDenominator);
    const endFrame = floorDiv(
      (startNumerator * durationDenominator + durationNumerator * startDenominator) * fpsNumerator,
      startDenominator * durationDenominator * fpsDenominator,
    );
    return Number(endFrame - startFrame);
  });
  assert.ok(sourceFrameCount > 24, `expected a source longer than one second, got ${sourceFrameCount} frames`);
  const frameBeforeTrim = sourceFrameCount - 2;
  const trimmedFrame = sourceFrameCount - 1;
  await seek(sourceVideo, 0.5);
  const sourceVideoTime = await sourceVideo.evaluate(video => video.currentTime);
  assert.ok(Math.abs(sourceVideoTime - 0.5) < 0.01, `source seek expected 0.5s, got ${sourceVideoTime}s`);
  await page.getByRole("button", {name: "mark in at playhead"}).click();
  const inText = await page.locator(".source-range p").textContent();
  assert.match(inText, /In 00:00:00:12/);
  await seek(sourceVideo, 1.5);
  await page.getByRole("button", {name: "mark out at playhead"}).click();
  const rangeText = await page.locator(".source-range p").textContent();
  assert.match(rangeText, /In 00:00:00:12 · Out 00:00:01:12 · Duration 00:00:01:00 \(24 ticks\)/);
  await page.getByRole("button", {name: "insert selected range into sequence"}).click();
  await page.getByRole("status").getByText(/Inserted 24 ticks at tick 72\./).waitFor({timeout: 60_000});
  const pictureRevision = await page.locator("#edit").inputValue();
  assert.notEqual(pictureRevision, baseId);
  assert.equal(await page.locator(".clip-entry").count(), 3);

  await page.getByRole("navigation", {name: "workspace"}).getByRole("link", {name: "work"}).click();
  await page.locator("#destination-track").selectOption("audio");
  await page.locator("#playhead-tick").fill("72");
  await page.getByRole("navigation", {name: "workspace"}).getByRole("link", {name: "materials"}).click();
  await page.getByRole("button", {name: "Musique café.mp3", exact: true}).click();
  const sourceAudio = page.locator("#asset-details audio");
  await page.waitForFunction(() => document.querySelector("#asset-details audio")?.readyState >= 2);
  await seek(sourceAudio, 0.5);
  await page.getByRole("button", {name: "mark in at playhead"}).click();
  await seek(sourceAudio, 1.5);
  await page.getByRole("button", {name: "mark out at playhead"}).click();
  await page.getByRole("button", {name: "insert selected range into sequence"}).click();
  await page.getByRole("status").getByText(/Inserted 24 ticks at tick 72\./).waitFor({timeout: 60_000});
  const audioRevision = await page.locator("#edit").inputValue();
  assert.equal(await page.locator(".clip-entry").count(), 4);

  await page.locator(".clip-entry").first().getByRole("button", {name: "trim out 1 frame"}).click();
  await page.getByRole("status").getByText(/Trimmed out-point by one frame\./).waitFor({timeout: 60_000});
  const trimmedRevision = await page.locator("#edit").inputValue();
  assert.notEqual(trimmedRevision, audioRevision);
  const rendered = page.locator("#sequence-preview video");
  await page.waitForFunction(() => document.querySelector("#sequence-preview video")?.readyState >= 2);
  await rendered.evaluate(video => { video.muted = true; return video.play(); });
  await page.waitForFunction(() => document.querySelector("#sequence-preview video").getVideoPlaybackQuality().totalVideoFrames >= 3);
  await rendered.evaluate(video => video.pause());

  const previewPath = join(workspace, "previews", `${(await rendered.getAttribute("src")).split("/").pop()}.mp4`);
  const decoded = decodeRendered(previewPath, frameBeforeTrim, trimmedFrame);
  assert.equal(Number(decoded.duration), 4);
  assert.ok(decoded.frame_before_trim > 210, `frame before trimmed gap should be white, got ${decoded.frame_before_trim}`);
  assert.ok(decoded.trimmed_frame < 40, `trimmed frame should be black, got ${decoded.trimmed_frame}`);
  assert.ok(Math.abs(decoded.first_tone_hz - 330) < 8, `base audiovisual source should be 330 Hz, got ${decoded.first_tone_hz}`);
  assert.ok(Math.abs(decoded.inserted_tone_hz - 550) < 8, `inserted source should be 550 Hz, got ${decoded.inserted_tone_hz}`);

  const mp4Download = page.waitForEvent("download");
  await page.locator("#sequence-preview a").getByText("download mp4").click();
  const mp4 = await mp4Download;
  assert.match(mp4.suggestedFilename(), /preview\.mp4$/);
  const downloadedMp4 = join(root, "exported-cut.mp4");
  await mp4.saveAs(downloadedMp4);
  assert.deepEqual(decodeRendered(downloadedMp4, frameBeforeTrim, trimmedFrame), decoded);
  const xmlDownload = page.waitForEvent("download");
  await page.getByRole("button", {name: "export xml"}).click();
  const xml = await xmlDownload;
  const xmlText = readFileSync(await xml.path(), "utf8");
  assert.match(xmlText, /<xmeml version="5">/);
  assert.match(xmlText, /Scene%20lumi%C3%A8re\.mov/);
  assert.match(xmlText, /Musique%20caf%C3%A9\.mp3/);

  await page.getByRole("button", {name: "revert to parent revision"}).click();
  await page.getByRole("status").getByText(`Reverted to saved parent revision ${audioRevision}.`, {exact: true}).waitFor({timeout: 60_000});
  assert.equal(await page.locator("#edit").inputValue(), audioRevision);
  await page.reload();
  await page.waitForFunction(id => document.querySelector("#edit").value === id, audioRevision);
  await page.getByRole("button", {name: "revert to parent revision"}).waitFor();
  await page.getByRole("button", {name: "render & watch"}).click();
  await page.waitForFunction(() => document.querySelector("#sequence-preview video")?.readyState >= 2, null, {timeout: 60_000});
  assert.deepEqual([videoPath, audioPath].map(hashFile), hashes);
  const registeredHashes = await page.evaluate(async () => {
    const records = await (await fetch("/api/assets")).json();
    return Promise.all(records.map(async record => {
      const asset = await (await fetch(`/api/asset/${record.id}`)).json();
      return [asset.name, asset.sha256];
    }));
  });
  assert.equal(registeredHashes.find(([name]) => name === "Scene lumière.mov")[1], hashes[0]);
  assert.equal(registeredHashes.find(([name]) => name === "Musique café.mp3")[1], hashes[1]);

  await page.getByRole("navigation", {name: "workspace"}).getByRole("link", {name: "materials"}).click();
  const assetCount = await page.locator("#assets li").count();
  await page.evaluate(paths => window.__AG_TEST_PATHS.push(...paths), [malformedPath]);
  await page.getByRole("button", {name: "+ import"}).click();
  await page.locator("#import [name=provenance]").fill("Invalid media test");
  await page.locator("#import [name=rights]").fill("Generated locally for testing");
  await page.getByRole("button", {name: "import selected footage"}).click();
  await page.getByRole("status").getByText(/Could not import broken video\.mp4/).waitFor();
  await page.getByRole("button", {name: "close"}).click();
  await page.evaluate(paths => window.__AG_TEST_PATHS.push(...paths), [unsupportedPath]);
  await page.getByRole("button", {name: "+ import"}).click();
  await page.locator("#import [name=provenance]").fill("Invalid media test");
  await page.locator("#import [name=rights]").fill("Generated locally for testing");
  await page.getByRole("button", {name: "import selected footage"}).click();
  await page.getByRole("status").getByText(/Could not import notes\.txt/).waitFor();
  await page.getByRole("button", {name: "close"}).click();
  await page.waitForFunction(count => document.querySelectorAll("#assets li").length === count, assetCount);
  assert.deepEqual(errors, []);
  console.log("Desktop-bridge UI: multi-file import, Unicode paths, source marking, picture/audio insert, one-frame trim, decoded MP4/XML export, parent revert and reload verified");
} finally {
  if (browser) await browser.close();
  if (server.exitCode === null && server.signalCode === null) {
    server.stdin.end();
    await once(server, "exit");
  }
  rmSync(root, {recursive: true, force: true});
}

async function seek(player, time) {
  await player.evaluate(async (element, target) => {
    if (element.readyState < 2) await new Promise(resolve => element.addEventListener("loadeddata", resolve, {once: true}));
    element.pause();
    if (Math.abs(element.currentTime - target) < 0.001) return;
    await new Promise((resolve, reject) => {
      let done = false;
      const timer = setTimeout(() => {
        done = true;
        reject(new Error(`Seek did not reach ${target}s; current time is ${element.currentTime}s.`));
      }, 5_000);
      const check = () => {
        if (done) return;
        if (element.error) {
          done = true;
          clearTimeout(timer);
          reject(new Error("Source proxy failed while seeking."));
        } else if (!element.seeking && Math.abs(element.currentTime - target) < 0.01) {
          done = true;
          clearTimeout(timer);
          resolve();
        } else {
          requestAnimationFrame(check);
        }
      };
      element.addEventListener("seeked", check, {once: true});
      element.currentTime = target;
    });
  }, time);
}
