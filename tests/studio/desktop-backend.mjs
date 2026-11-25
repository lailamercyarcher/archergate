import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { once } from "node:events";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createInterface } from "node:readline";

const token = randomBytes(32).toString("hex");
const workspace = mkdtempSync(join(tmpdir(), "archergate-desktop-"));
const child = spawn(".venv/bin/python",
  ["-m", "archergate.studio", "--workspace", workspace, "serve", "--port", "0", "--desktop"],
  {env: {...process.env, ARCHERGATE_SESSION_TOKEN: token}, stdio: ["pipe", "pipe", "inherit"]});
try {
  const output = createInterface({input: child.stdout});
  const [line] = await once(output, "line", {signal: AbortSignal.timeout(10_000)});
  const origin = line.match(/http:\/\/127\.0\.0\.1:[0-9]+/)?.[0];
  assert.ok(origin);
  assert.equal((await fetch(origin + "/api/assets")).status, 403);
  assert.equal((await fetch(origin + "/desktop-session/not-the-key")).status, 403);
  const bootstrap = await fetch(origin + "/desktop-session/" + token, {redirect: "manual"});
  assert.equal(bootstrap.status, 303);
  assert.equal(bootstrap.headers.get("location"), "/");
  const cookie = bootstrap.headers.get("set-cookie");
  assert.match(cookie, /HttpOnly; SameSite=Strict/);
  const authorized = await fetch(origin + "/api/assets", {headers: {Cookie: cookie.split(";")[0]}});
  assert.equal(authorized.status, 200);
  assert.deepEqual(await authorized.json(), []);
  const exited = once(child, "exit", {signal: AbortSignal.timeout(5000)});
  child.stdin.end();
  assert.deepEqual(await exited, [0, null]);
  console.log("Desktop backend denied unrelated requests and shut down when the parent pipe closed");
} finally {
  if (child.exitCode === null && child.signalCode === null) {
    child.kill();
    await once(child, "exit");
  }
  rmSync(workspace, {recursive: true, force: true});
}
