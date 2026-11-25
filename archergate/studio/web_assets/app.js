const $ = selector => document.querySelector(selector);
const assets = new Map();
let edits = [];
let pendingPaths = [];
let busy = 0;
const invoke = window.__TAURI__?.core?.invoke;
const initialPreview = $("#preview-empty").cloneNode(true);

function navigateTo(target) {
  const view = ["timelines", "media", "direction"].includes(target) ? target : "timelines";
  for (const section of document.querySelectorAll(".app-view")) {
    const active = section.id === view;
    section.hidden = !active;
    section.classList.toggle("is-active", active);
  }
  for (const link of document.querySelectorAll("nav [data-view]")) {
    if (link.dataset.view === view) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  if (location.hash !== `#${view}`) history.replaceState(null, "", `#${view}`);
}
function renderEmptyPreview(edit) {
  const preview = initialPreview.cloneNode(true);
  if (edit) {
    preview.querySelector("p").textContent = "sequence ready";
    const control = preview.querySelector("button");
    control.textContent = "play sequence";
    control.id = "empty-watch";
  } else if (assets.size) {
    preview.querySelector("p").textContent = "no sequence";
    preview.querySelector("button").textContent = "select footage";
    preview.querySelector("button").id = "empty-materials";
  }
  $("#sequence-preview").replaceChildren(preview);
  $("#preview-indicator").textContent = edit ? "ready for preview" : "no active sequence";
  $("#monitor-timecode").textContent = "--:--";
}

for (const link of document.querySelectorAll("nav [data-view]")) {
  link.addEventListener("click", event => {
    event.preventDefault(); navigateTo(link.dataset.view);
  });
}
document.addEventListener("click", event => {
  if (event.target.closest("#empty-import")) { navigateTo("media"); $("#open-import").click(); }
  if (event.target.closest("#empty-materials")) navigateTo("media");
  if (event.target.closest("#empty-watch")) $("#preview-edit").click();
});
window.addEventListener("hashchange", () => navigateTo(location.hash.slice(1)));
navigateTo(location.hash.slice(1));

const provider = $("#provider-dialog");
$("#request-revision").addEventListener("click", action(() => {
  $("#provider-error").textContent = "";
  if (!$("#edit").value) throw new Error("Start an edit from Materials before giving a direction.");
  if (!invoke) throw new Error("Creative revisions require the ArcherGate desktop application.");
  provider.showModal();
  provider.querySelector('[name="key"]').focus();
}));
$("#open-provider-settings").addEventListener("click", () => provider.showModal());
$("#close-provider").addEventListener("click", () => provider.close());
$("#cancel-provider").addEventListener("click", () => provider.close());

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
    catch (error) { $("#status").textContent = error.message; if ($("#provider-dialog")?.open) $("#provider-error").textContent = error.message; }
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
  $("#asset-count").textContent = `${rows.length} ${rows.length === 1 ? "source" : "sources"}`;
  document.body.classList.toggle("no-assets", rows.length === 0);
  for (const row of rows) assets.set(row.id, row);
  $("#assets").replaceChildren(...rows.map(row => {
    const li = node("li");
    const control = button(undefined, () => showAsset(row.id));
    control.setAttribute("aria-label", row.name);
    const thumbnail = node("span", undefined, "asset-thumb");
    if (row.source?.streams?.video) {
      const image = node("img");
      image.loading = "lazy";
      image.alt = "";
      image.src = `/api/asset/${row.id}/thumbnail`;
      thumbnail.append(image);
    } else thumbnail.append(node("span", "♪", "asset-audio"));
    control.append(thumbnail, node("span", row.name, "asset-name"),
      node("span", row.source?.streams?.video ? "video" : "audio", "asset-label"));
    li.append(control);
    return li;
  }));
  if (!rows.length) $("#assets").append(node("li", query ? "no matching clips" : "no footage imported", "collection-empty"));
  const select = $("#memory [name=asset]");
  const selected = select.value;
  select.replaceChildren(new Option("no linked footage", ""), ...[...assets.values()].map(row => new Option(row.name, row.id)));
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
  for (const item of document.querySelectorAll("#assets button")) item.removeAttribute("aria-current");
  [...document.querySelectorAll("#assets button")].find(el => el.getAttribute("aria-label") === asset.name)?.setAttribute("aria-current", "true");
  view.replaceChildren(node("h3", asset.name), player,
    node("p", `${asset.provenance} · ${asset.rights}`, "details"),
    node("p", `SHA-256 ${asset.sha256}`, "details"));
  view.append(button("start sequence", () => makeDraft(asset)));
  if (asset.source.streams.audio && (await api("/api/speech")).installed) {
    view.append(button("transcribe english", async () => {
      $("#status").textContent = "transcribing locally…";
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
    view.append(node("h4", "waveform"), canvas);
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
  for (const [title, analysis] of [["shot boundaries", asset.shots], ["english transcript", asset.transcript]]) {
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

async function makeDraft(asset) {
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
  const draft = {
    version: 2, id: `edit-${Date.now()}`, title: asset.name, timebase: `${denominator}/${frames}`, fps, tracks,
  };
  // Validate and save through the real timeline service, never fabricate a preview.
  const saved = await api("/api/timelines", draft);
  await refreshEdits(saved.id);
  navigateTo("timelines");
  await showSequence([saved.id]);
  $("#status").textContent = "Sequence ready.";
}

async function showSequence(names, editorial = null) {
  if (names.some(name => !name)) return;
  const container = $("#sequence-preview");
  $("#status").textContent = "rendering preview…";
  const keys = [];
  for (const name of names) keys.push((await api("/api/preview", {name})).key);
  const player = node("video");
  player.controls = true;
  player.preload = "auto";
  const timecode = $("#monitor-timecode");
  const clock = value => {
    if (!Number.isFinite(value)) return "--:--";
    const seconds = Math.max(0, Math.floor(value));
    return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
  };
  function updateClock() { timecode.textContent = `${clock(player.currentTime)} / ${clock(player.duration)}`; }
  player.addEventListener("timeupdate", updateClock);
  player.addEventListener("loadedmetadata", updateClock);
  player.setAttribute("aria-label", "Composed sequence preview");
  const controls = node("div", undefined, "row");
  const download = node("a", "download mp4");
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
      button(`show ${name.toLowerCase()}`, () => { if (index !== selected) activate(index); })));
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
      $("#status").textContent = decision === "accepted" ? "revision accepted" : "revision rejected";
    }
    decisions.append(button("accept b", () => recordDecision("accepted")),
      button("reject b", () => recordDecision("rejected")));
    if (invoke) decisions.append(button("refine b", () => {
      $("#edit").value = names[1];
      selectEdit();
      navigateTo("timelines");
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
    const length = Math.max(1, ...edit.tracks.flatMap(row => row.clips.map(clip => clip.at + clip.duration)));
    const strip = node("div", undefined, `timeline-strip ${track.kind}`);
    strip.setAttribute("aria-label", `${track.kind} segments`);
    for (const clip of track.clips) {
      const mark = button("", async () => { navigateTo("media"); await showAsset(clip.asset, clip.source_start * base); });
      mark.className = "timeline-segment";
      mark.style.left = `${clip.at * 100 / length}%`;
      mark.style.width = `${clip.duration * 100 / length}%`;
      mark.setAttribute("aria-label", `${clip.id}, ${seconds(clip.duration * base)}`);
      mark.title = assets.get(clip.asset)?.name || clip.asset;
      strip.append(mark);
    }
    lane.append(strip);
    let last = 0;
    for (const clip of [...track.clips].sort((a, b) => a.at - b.at)) {
      if (clip.at > last) lane.append(node("p", `Gap ${seconds(last * base)} to ${seconds(clip.at * base)}`, "details"));
      const name = assets.get(clip.asset)?.name || clip.asset;
      const end = clip.at + clip.duration;
      const cue = button(`${seconds(clip.at * base)} → ${seconds(end * base)} · ${name} · source ${seconds(clip.source_start * base)}`,
        async () => {
          navigateTo("media");
          await showAsset(clip.asset, clip.source_start * base);
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
  renderEmptyPreview(edit);
  document.body.classList.toggle("no-sequence", !edit);
  if (!edit) $("#edit-details").append(node("p", "No clips", "timeline-empty"));
  for (const id of ["#preview-edit", "#export"]) $(id).hidden = !edit;
  for (const id of ["#preview-compare", "#compare"]) $(id).hidden = edits.length < 2;
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

let memoryRows = [];
let selectedMemoryId = null;
const memoryDialog = $("#memory-dialog");
const importDialog = $("#import-dialog");

function memoryRequirements() {
  const form = $("#memory");
  const kind = form.elements.kind.value;
  const timed = kind === "scene" || kind === "annotation";
  const linked = kind === "footage" || kind === "annotation";
  const decided = kind === "suggestion";
  form.elements.timestamp.required = timed;
  form.elements.asset.required = linked;
  form.elements.decision.required = decided;
  $("#memory-decision-fields").hidden = !decided;
  if (timed || linked) $("#reference-more").open = true;
}

function openMemoryEditor(record = null) {
  const form = $("#memory");
  form.reset();
  form.elements.kind.disabled = false;
  form.elements.id.value = "";
  $("#memory-dialog-title").textContent = record ? "edit note" : "new note";
  if (record) {
    for (const input of form.elements) {
      if (input.name && Object.hasOwn(record, input.name)) input.value = record[input.name] ?? "";
    }
    form.elements.kind.disabled = true;
  }
  $("#reference-more").open = !!(record && (record.reference || record.timestamp || record.asset));
  memoryRequirements();
  if (!memoryDialog.open) memoryDialog.showModal();
  form.elements.title.focus();
}

function readMemory(record) {
  selectedMemoryId = record.id;
  const reader = $("#reference-reader");
  const heading = node("div", undefined, "reader-header");
  const titleGroup = node("div");
  titleGroup.append(node("p", record.kind === "suggestion" ? "decision" : record.kind, "reader-kind"),
                    node("h3", record.title, "reader-title"));
  const edit = button("edit note", () => openMemoryEditor(record));
  edit.className = "quiet-button";
  heading.append(titleGroup, edit);
  const body = node("div", record.description, "reader-content");
  const metadata = node("dl", undefined, "reader-metadata");
  const put = (label, value) => {
    if (value === undefined || value === null || value === "") return;
    metadata.append(node("dt", label), node("dd", String(value)));
  };
  put("reference", record.reference);
  put("time", record.timestamp ? `${seconds(number(record.timestamp))}` : "");
  put("decision", record.decision);
  put("modification", record.revision);
  if (record.history?.length) put("history", record.history.map(row => row.decision + (row.revision ? ` · ${row.revision}` : "")).join(" → "));
  if (record.asset) {
    const source = assets.get(record.asset);
    const dd = node("dd");
    const open = button(source?.name || "open footage", async () => {
      navigateTo("media");
      await showAsset(record.asset, record.timestamp || undefined);
    });
    open.className = "quiet-button";
    dd.append(open);
    metadata.append(node("dt", "source"), dd);
  }
  reader.replaceChildren(heading);
  if (record.asset && assets.get(record.asset)?.source?.streams?.video) {
    const still = node("img", undefined, "reader-frame");
    still.alt = `source preview: ${assets.get(record.asset).name}`;
    still.src = `/api/asset/${record.asset}/thumbnail`;
    reader.append(still);
  }
  reader.append(body);
  if (metadata.children.length) reader.append(metadata);
  paintMemory();
}

function paintMemory() {
  const query = $("#memory-query").value.trim().toLocaleLowerCase();
  const kind = $("#memory-filter").value;
  const rows = memoryRows.filter(row => (!kind || row.kind === kind) &&
    (!query || `${row.title} ${row.kind} ${row.description} ${row.reference}`.toLocaleLowerCase().includes(query)));
  $("#record-count").textContent = `${rows.length} ${rows.length === 1 ? "note" : "notes"}`;
  $("#records-empty").hidden = rows.length !== 0;
  $("#records-empty").querySelector("span").textContent = memoryRows.length ? "no matching notes" : "no notes yet";
  $("#records").replaceChildren(...rows.map(record => {
    const li = node("li");
    const control = button(undefined, () => readMemory(record));
    control.setAttribute("aria-label", `${record.kind} · ${record.title}`);
    if (record.id === selectedMemoryId) control.setAttribute("aria-current", "true");
    control.append(node("span", record.title, "record-title"),
                   node("span", record.kind === "suggestion" ? "decision" : record.kind, "record-kind"),
                   node("span", record.description, "record-summary"));
    li.append(control);
    return li;
  }));
}

async function refreshMemory(selected) {
  memoryRows = await api("/api/memory");
  if (selected) selectedMemoryId = selected;
  const current = memoryRows.find(row => row.id === selectedMemoryId) || memoryRows[0];
  if (current) readMemory(current);
  else {
    selectedMemoryId = null;
    $("#reference-reader").replaceChildren(node("div", "", "reference-empty"));
    paintMemory();
  }
}

$("#memory-query").addEventListener("input", paintMemory);
$("#memory-filter").addEventListener("change", paintMemory);
$("#new-reference").addEventListener("click", () => openMemoryEditor());
$("#empty-new-reference").addEventListener("click", () => openMemoryEditor());
$("#close-memory").addEventListener("click", () => memoryDialog.close());
$("#memory [name=kind]").addEventListener("change", memoryRequirements);
$("#memory").addEventListener("reset", () => queueMicrotask(memoryRequirements));
$("#memory").addEventListener("keydown", event => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault(); $("#memory").requestSubmit();
  }
});
$("#open-import").addEventListener("click", () => {
  if (!importDialog.open) importDialog.showModal();
  if (invoke) $("#pick-media").click();
});
$("#close-import").addEventListener("click", () => importDialog.close());
$("#library-query").addEventListener("input", action(event => refreshAssets(event.target.value)));
document.addEventListener("keydown", event => {
  if (event.ctrlKey && !event.altKey && !event.shiftKey && ["1", "2", "3"].includes(event.key)) {
    event.preventDefault(); navigateTo(({"1":"timelines", "2":"media", "3":"direction"})[event.key]);
  }
});

$("#import").addEventListener("submit", action(async event => {
  const form = event.target;
  const submit = form.querySelector('button[type="submit"]');
  submit.disabled = true;
  $("#status").textContent = "Importing and analyzing media locally…";
  try {
    const values = fields(form);
    const paths = pendingPaths.length ? [...pendingPaths] : [values.path];
    if (!paths[0]) throw new Error("Choose a media file before importing.");
    let asset;
    while (paths.length) {
      const current = paths[0];
      try { asset = await api("/api/import", {...values, path: current}); }
      catch (error) {
        pendingPaths = [...paths];
        throw new Error(`Could not import ${current.split(/[\\/]/).pop()}: ${error.message}. Check permissions, format and available workspace storage.`);
      }
      paths.shift();
      pendingPaths = [...paths];
      $("#pending-files").textContent = paths.length ? `${paths.length} file(s) remain to import` : "";
      form.elements.path.value = paths[0] || "";
    }
    form.reset();
    importDialog.close();
    document.body.classList.remove("has-pending-import");
    await refreshAssets();
    await showAsset(asset.id);
    $("#status").textContent = "media imported and available for playback";
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
  $("#status").textContent = "new edit saved";
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
  $("#status").textContent = "xml exported";
}));
$("#memory").addEventListener("submit", action(async event => {
  const saved = await api("/api/memory", {...fields(event.target), kind: event.target.elements.kind.value});
  memoryDialog.close();
  event.target.reset();
  await refreshMemory(saved.id);
  $("#status").textContent = "note saved";
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
  document.body.classList.add("is-desktop");
  $("#import").elements.path.required = false;
  $("#import").elements.path.readOnly = true;
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
    const submit = $("#submit-provider");
    submit.disabled = true;
    $("#status").textContent = "requesting revision…";
    try {
      const result = await api("/api/creative-revision", {base, direction, key, model, consent});
      await refreshEdits(base);
      $("#alternative").value = result.id;
      await showSequence([base, result.id], {direction, rationale: result.rationale});
      const explanation = node("details");
      explanation.append(node("summary", "editorial rationale"), node("p", result.rationale));
      $("#sequence-preview").append(explanation);
      provider.close();
      $("#status").textContent = result.cached ? "Revision rendered from a previously approved response." : "revision ready";
    } finally {
      submit.disabled = false;
    }
  }));
  $("#workspace-path").textContent = await invoke("workspace");
  function selected(paths) {
    if (!paths.length) return;
    pendingPaths = [...paths];
    if (!importDialog.open) importDialog.showModal();
    document.body.classList.add("has-pending-import");
    $("#import").elements.path.value = paths[0];
    $("#pending-files").textContent = `${paths.length} file${paths.length === 1 ? "" : "s"} ready · ${paths.map(path => path.split(/[\\/]/).pop()).join(" · ")}`;
    navigateTo("media");
    $("#status").textContent = `${paths.length} file${paths.length === 1 ? "" : "s"} selected.`;
  }
  $("#import").elements.path.addEventListener("input", () => { pendingPaths = []; $("#pending-files").textContent = ""; });
  $("#pick-media").addEventListener("click", action(async () => selected(await invoke("pick_media"))));
  $("#choose-workspace").addEventListener("click", action(async () => {
    if (busy) throw new Error("Wait for the active media job to finish.");
    await invoke("choose_workspace");
  }));
  window.addEventListener("archer-drop", event => {
    if (!busy && Array.isArray(event.detail) && event.detail.length) {
      selected(event.detail);
      $("#import-dropzone").classList.add("import-received");
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
