const $ = selector => document.querySelector(selector);
const assets = new Map();
let edits = [];

async function api(path, value, xml = false) {
  const response = await fetch(path, value === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json", "X-ArcherGate-Workspace": "1"},
    body: JSON.stringify(value),
  });
  if (!response.ok) {
    const result = await response.json();
    throw new Error(result.error || `Request failed (${response.status})`);
  }
  return xml ? response.blob() : response.json();
}

function action(handler) {
  return async event => {
    event?.preventDefault();
    $("#status").textContent = "";
    try { await handler(event); }
    catch (error) { $("#status").textContent = error.message; }
  };
}

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function button(text, handler) {
  const result = node("button", text);
  result.type = "button";
  result.addEventListener("click", action(handler));
  return result;
}

function number(value) {
  const [numerator, denominator = 1] = String(value).split("/");
  return Number(numerator) / Number(denominator);
}

const seconds = value => `${Number(value).toFixed(2)} s`;
const fields = form => Object.fromEntries(new FormData(form));

async function refreshAssets(query = "") {
  const rows = await api(`/api/assets?q=${encodeURIComponent(query)}`);
  for (const row of rows) assets.set(row.id, row);
  $("#assets").replaceChildren(...rows.map(row => {
    const li = node("li");
    li.append(button(row.name, () => showAsset(row.id)));
    return li;
  }));
  const select = $("#memory [name=asset]");
  const selected = select.value;
  select.replaceChildren(new Option("No linked asset", ""), ...[...assets.values()].map(row => new Option(row.name, row.id)));
  select.value = selected;
}

async function showAsset(id, position) {
  const asset = await api(`/api/asset/${id}`);
  const view = $("#asset-details");
  const player = node(asset.source.streams.video ? "video" : "audio");
  player.controls = true;
  player.preload = "auto";
  player.src = `/api/asset/${id}/proxy`;
  if (asset.source.streams.video) player.poster = `/api/asset/${id}/thumbnail`;
  player.addEventListener("error", () => { $("#status").textContent = "The browser could not play this proxy."; });
  const seek = time => {
    if (player.readyState) player.currentTime = number(time);
    else player.addEventListener("loadedmetadata", () => { player.currentTime = number(time); }, {once: true});
  };
  if (position !== undefined) seek(position);
  view.replaceChildren(node("h3", asset.name), player,
    node("p", `${asset.provenance} · ${asset.rights}`, "details"),
    node("p", `SHA-256 ${asset.sha256}`, "details"));
  const wave = asset.waveform;
  if (wave.state === "complete") {
    const canvas = node("canvas");
    canvas.width = 640;
    canvas.height = 60;
    canvas.setAttribute("aria-label", "Audio peak waveform");
    canvas.setAttribute("role", "img");
    view.append(node("h4", "Waveform"), canvas);
    const context = canvas.getContext("2d");
    context.strokeStyle = "#e5a774";
    context.beginPath();
    for (let x = 0; x < canvas.width; x++) {
      const start = Math.floor(x * wave.peaks.length / canvas.width);
      const end = Math.max(start + 1, Math.ceil((x + 1) * wave.peaks.length / canvas.width));
      const height = 28 * Math.max(0, ...wave.peaks.slice(start, end));
      context.moveTo(x, 30 - height);
      context.lineTo(x, 30 + height);
    }
    context.stroke();
  }
  for (const [title, analysis] of [["Likely shot boundaries", asset.shots], ["English transcript", asset.transcript]]) {
    view.append(node("h4", title));
    if (analysis.state !== "complete") {
      view.append(node("p", `${analysis.state} · ${analysis.reason}`, "details"));
      continue;
    }
    const entries = analysis.candidates || analysis.segments;
    if (!entries.length) view.append(node("p", "Analysis completed with no results.", "details"));
    for (const row of entries) {
      const time = row.time ?? row.start;
      view.append(button(`${seconds(number(time))} · ${row.text ?? `Scene score ${row.score.toFixed(2)}`}`, () => seek(time)));
    }
  }
}

function showTimeline(edit, changed = new Set()) {
  const view = node("article");
  view.append(node("h3", edit.title));
  const base = number(edit.timebase);
  for (const track of edit.tracks) {
    const lane = node("div", undefined, "track");
    lane.append(node("h4", `${track.id} · ${track.kind}`));
    let last = 0;
    for (const clip of [...track.clips].sort((a, b) => a.at - b.at)) {
      if (clip.at > last) lane.append(node("p", `Gap ${seconds(last * base)} to ${seconds(clip.at * base)}`, "details"));
      const name = assets.get(clip.asset)?.name || clip.asset;
      const end = clip.at + clip.duration;
      const cue = button(`${seconds(clip.at * base)} → ${seconds(end * base)} · ${name} · source ${seconds(clip.source_start * base)}`,
        async () => {
          await showAsset(clip.asset, clip.source_start * base);
          $("#asset-details").scrollIntoView();
        });
      if (changed.has(clip.id)) cue.classList.add("accent");
      lane.append(cue);
      last = end;
    }
    view.append(lane);
  }
  return view;
}

function selectEdit() {
  const edit = edits.find(row => row.id === $("#edit").value);
  $("#edit-details").replaceChildren(...(edit ? [showTimeline(edit)] : []));
  $("#manifest").value = edit ? JSON.stringify(edit, null, 2) : "";
  $("#export").disabled = !edit;
  $("#compare").disabled = edits.length < 2;
  $("#review").replaceChildren();
}

async function refreshEdits(selected) {
  edits = await api("/api/timelines");
  for (const id of ["#edit", "#alternative"]) {
    $(id).replaceChildren(...edits.map(edit => new Option(edit.title, edit.id)));
  }
  if (selected) $("#edit").value = selected;
  $("#alternative").selectedIndex = Math.min(1, edits.length - 1);
  selectEdit();
}

async function refreshMemory() {
  $("#records").replaceChildren(...(await api("/api/memory")).map(record => {
    const item = node("li");
    item.append(button(`${record.kind} · ${record.title}`, () => {
      const form = $("#memory");
      for (const input of form.elements) if (input.name) input.value = record[input.name] || "";
      form.elements.title.focus();
    }), node("p", record.description, "details"));
    if (record.timestamp) item.append(node("p", `Cue ${seconds(number(record.timestamp))}`, "details"));
    if (record.asset) item.append(button("Open linked footage", () => showAsset(record.asset, record.timestamp || undefined)));
    if (record.history.length) item.append(node("p", record.history.map(row => row.decision + (row.revision ? ` · ${row.revision}` : "")).join(" → "), "details"));
    return item;
  }));
}

$("#import").addEventListener("submit", action(async event => {
  const form = event.target;
  const submit = form.querySelector("button");
  submit.disabled = true;
  $("#status").textContent = "Importing and analyzing media locally…";
  try {
    const asset = await api("/api/import", fields(form));
    form.reset();
    await refreshAssets();
    await showAsset(asset.id);
    $("#status").textContent = "Media imported.";
  } finally { submit.disabled = false; }
}));
$("#search").addEventListener("submit", action(event => refreshAssets(fields(event.target).q)));
$("#edit").addEventListener("change", selectEdit);
$("#compare").addEventListener("click", action(async () => {
  const data = await api("/api/compare", {left: $("#edit").value, right: $("#alternative").value});
  const changed = new Set(data.changes.map(row => row.clip));
  $("#edit-details").replaceChildren(showTimeline(data.left, changed), showTimeline(data.right, changed));
  $("#status").textContent = `${changed.size} changed clip${changed.size === 1 ? "" : "s"}.`;
}));
$("#inspect").addEventListener("click", action(async () => {
  const data = await api("/api/inspect", JSON.parse($("#manifest").value));
  $("#review").replaceChildren(
    ...data.split_edits.map(row => node("li", `${row.kinds.join(", ")} · ${row.video} / ${row.audio}`)),
    ...data.issues.map(row => node("li", `${row.level} · ${row.clip} · ${row.explanation}`)),
  );
  $("#status").textContent = data.valid ? "The proposed edit is valid." : "The proposed edit has errors.";
}));
$("#save-edit").addEventListener("click", action(async () => {
  const result = await api("/api/timelines", JSON.parse($("#manifest").value));
  await refreshEdits(result.id);
  $("#status").textContent = "New edit saved.";
}));
$("#export").addEventListener("click", action(async () => {
  const name = $("#edit").value;
  const data = await api("/api/export", {name}, true);
  const url = URL.createObjectURL(data);
  const link = node("a");
  link.href = url;
  link.download = `${name}.xml`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  $("#status").textContent = "Editable XML exported.";
}));
$("#memory").addEventListener("submit", action(async event => {
  await api("/api/memory", fields(event.target));
  event.target.reset();
  await refreshMemory();
  $("#status").textContent = "Creative record saved.";
}));

action(async () => {
  await refreshAssets();
  await Promise.all([refreshEdits(), refreshMemory()]);
})();
