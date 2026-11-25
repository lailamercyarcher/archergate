const $ = selector => document.querySelector(selector);
const assets = new Map();
let edits = [];
let pendingPaths = [];
let busy = 0;
const invoke = window.__TAURI__?.core?.invoke;

async function api(path, value, xml = false) {
  if (value !== undefined) busy++;
  try {
    const response = await fetch(path, value === undefined ? {} : {
      method: "POST", headers: {"Content-Type": "application/json", "X-ArcherGate-Workspace": "1"},
      body: JSON.stringify(value),
    });
    if (!response.ok) {
      const result = await response.json();
      throw new Error(result.error || `Request failed (${response.status})`);
    }
    return xml ? response.blob() : response.json();
  } finally {
    if (value !== undefined) busy--;
  }
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
  view.append(button("Start edit from source", () => makeDraft(asset)));
  if (asset.source.streams.audio && (await api("/api/speech")).installed) {
    view.append(button("Transcribe English", async () => {
      $("#status").textContent = "Analyzing speech locally…";
      await api("/api/transcribe", {asset: id});
      await showAsset(id, player.currentTime);
    }));
  }
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

function makeDraft(asset) {
  const candidate = asset.source.streams.video?.avg_frame_rate;
  const fraction = candidate?.match(/^([1-9][0-9]*)\/(1|1001)$/);
  const fps = fraction && number(candidate) <= 240 ? candidate : "24/1";
  const [frames, denominator] = fps.split("/");
  const rate = number(fps);
  const tracks = Object.entries(asset.source.streams).flatMap(([kind, stream]) => {
    const at = Math.max(0, Math.ceil(number(stream.start) * rate - 1e-7));
    const end = Math.floor((number(stream.start) + number(stream.duration)) * rate + 1e-7);
    return end > at ? [{id: kind, kind, clips: [{
      id: `source-${kind}`, asset: asset.id, at, source_start: at, duration: end - at, sync: "source",
    }]}] : [];
  });
  if (!tracks.length) throw new Error("The source is shorter than one timeline frame.");
  $("#manifest").value = JSON.stringify({
    version: 2, id: `edit-${Date.now()}`, title: asset.name, timebase: `${denominator}/${frames}`, fps, tracks,
  }, null, 2);
  $("#review").replaceChildren();
  $("#edit-manifest").open = true;
  $("#manifest").scrollIntoView({block: "center"});
  $("#manifest").focus();
}

async function showSequence(names, editorial = null) {
  if (names.some(name => !name)) return;
  const container = $("#sequence-preview");
  $("#status").textContent = "Rendering sequence preview locally…";
  const keys = [];
  for (const name of names) keys.push((await api("/api/preview", {name})).key);
  const player = node("video");
  player.controls = true;
  player.preload = "auto";
  player.setAttribute("aria-label", "Composed sequence preview");
  const controls = node("div", undefined, "row");
  const download = node("a", "Download rendered MP4");
  let selected = 0;
  let cue = 0;
  let resume = false;
  let generation = 0;
  const activate = (index, preserve = true) => {
    if (preserve && player.readyState >= 1) {
      cue = player.currentTime;
      resume = !player.paused;
    }
    const current = ++generation;
    selected = index;
    if (preserve) player.addEventListener("loadedmetadata", () => {
      if (current !== generation) return;
      player.currentTime = Math.min(cue, player.duration);
      if (resume) player.play().catch(() => {});
    }, {once: true});
    player.src = `/api/preview/${keys[index]}`;
    download.href = player.src;
    download.download = `${names[index]}-preview.mp4`;
    for (let i = 0; i < controls.children.length; i++) controls.children[i].setAttribute("aria-pressed", String(i === selected));
  };
  if (keys.length > 1) {
    controls.append(...["A", "B"].map((name, index) =>
      button(`Show ${name}`, () => { if (index !== selected) activate(index); })));
  }
  activate(0, false);
  const decisions = node("div", undefined, "row");
  decisions.append(download);
  if (keys.length > 1) {
    async function recordDecision(decision) {
      if (names[0] === names[1]) throw new Error("Choose a different revision to compare.");
      const title = edits.find(edit => edit.id === names[1])?.title || names[1];
      const description = editorial ? `${editorial.direction}\n\n${editorial.rationale}`
        : `Revision ${names[1]} compared against ${names[0]}.`;
      await api("/api/memory", {kind: "suggestion", decision, title, description});
      if (decision === "rejected" && selected === 1) activate(0);
      await refreshMemory();
      $("#status").textContent = decision === "accepted" ? "Revision accepted." : "Revision rejected.";
    }
    decisions.append(button("Accept B", () => recordDecision("accepted")),
      button("Reject B", () => recordDecision("rejected")));
    if (invoke) decisions.append(button("Refine B", () => {
      $("#edit").value = names[1];
      selectEdit();
      $("#creative-direction").open = true;
      $("#creative-form").elements.direction.focus();
    }));
  }
  container.replaceChildren(controls, player, decisions);
  $("#status").textContent = "";
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
  $("#preview-edit").disabled = !edit;
  $("#preview-compare").disabled = edits.length < 2;
  $("#sequence-preview").replaceChildren();
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
    const values = fields(form);
    const paths = pendingPaths.length ? pendingPaths : [values.path];
    let asset;
    while (paths.length) {
      asset = await api("/api/import", {...values, path: paths[0]});
      paths.shift();
      if (pendingPaths.length) form.elements.path.value = pendingPaths[0] || "";
    }
    form.reset();
    await refreshAssets();
    await showAsset(asset.id);
    $("#status").textContent = "Media imported.";
  } finally { submit.disabled = false; }
}));
$("#preview-edit").addEventListener("click", action(() => showSequence([$("#edit").value])));
$("#preview-compare").addEventListener("click", action(() => showSequence([$("#edit").value, $("#alternative").value])));
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

if (invoke) action(async () => {
  window.addEventListener("archer-close", action(async () => {
    if (busy) throw new Error("Wait for the active media job to finish before closing.");
    await invoke("close_workspace");
  }));
  $("#desktop-controls").hidden = false;
  $("#edit-manifest").open = false;
  $("#pick-media").hidden = false;
  $("#speech-tools").hidden = false;
  $("#creative-direction").hidden = false;
  $("#creative-form").addEventListener("submit", action(async event => {
    const base = $("#edit").value;
    if (!base) throw new Error("Select a saved edit before requesting a revision.");
    const form = event.target;
    const consent = Object.fromEntries(["send", "frames", "dialogue", "memory"].map(key => [key, form.elements[key].checked]));
    const {direction, key, model} = fields(form);
    form.elements.key.value = "";
    form.elements.send.checked = false;
    const submit = form.querySelector("button");
    submit.disabled = true;
    $("#status").textContent = "Requesting an editorial proposal from Claude…";
    try {
      const result = await api("/api/creative-revision", {base, direction, key, model, consent});
      await refreshEdits(base);
      $("#alternative").value = result.id;
      await showSequence([base, result.id], {direction, rationale: result.rationale});
      const explanation = node("details");
      explanation.append(node("summary", "Editorial rationale"), node("p", result.rationale));
      $("#sequence-preview").append(explanation);
      $("#status").textContent = result.cached ? "Revision rendered from a previously approved response." : "Revision ready.";
    } finally {
      submit.disabled = false;
    }
  }));
  $("#workspace-path").textContent = await invoke("workspace");
  function selected(paths) {
    if (!paths.length) return;
    pendingPaths = paths;
    $("#import").elements.path.value = paths[0];
    $("#status").textContent = `${paths.length} file${paths.length === 1 ? "" : "s"} selected.`;
  }
  $("#import").elements.path.addEventListener("input", () => { pendingPaths = []; });
  $("#pick-media").addEventListener("click", action(async () => selected(await invoke("pick_media"))));
  $("#choose-workspace").addEventListener("click", action(async () => {
    if (busy) throw new Error("Wait for the active media job to finish.");
    await invoke("choose_workspace");
  }));
  window.addEventListener("archer-drop", event => {
    if (!busy && Array.isArray(event.detail) && event.detail.length) {
      selected(event.detail);
      $("#import").scrollIntoView({block: "center"});
    }
  });
  async function refreshSpeech() {
    const {installed} = await api("/api/speech");
    $("#speech-download").hidden = installed;
    $("#speech-status").textContent = installed ? "English transcription ready" : "English transcription unavailable";
  }
  $("#speech-download").addEventListener("click", action(async () => {
    const control = $("#speech-download");
    control.disabled = true;
    $("#speech-status").textContent = "Downloading the English model (~74 MB)…";
    try { await api("/api/speech/install", {}); await refreshSpeech(); }
    catch (error) { $("#speech-status").textContent = "English transcription unavailable"; throw error; }
    finally { control.disabled = false; }
  }));
  await refreshSpeech();
})();
