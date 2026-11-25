const summary = document.querySelector("#summary");

function showTimeline(timeline, other) {
  const cuts = document.querySelector(`#${timeline.id} ol`);
  const rangesInOther = new Set(other.cuts.map(({ asset, start, end }) => `${asset}:${start}:${end}`));
  for (const cut of timeline.cuts) {
    const row = document.createElement("li");
    const title = document.createElement("strong");
    title.classList.add("asset", cut.asset);
    title.textContent = cut.asset;
    const time = document.createElement("span");
    time.className = "time";
    time.textContent = `${(cut.timeline_start / timeline.fps).toFixed(2)}–${(cut.timeline_end / timeline.fps).toFixed(2)} s`;
    const range = document.createElement("span");
    range.className = "range";
    range.textContent = `Source frames ${cut.start}–${cut.end}`;
    if (!rangesInOther.has(`${cut.asset}:${cut.start}:${cut.end}`)) {
      range.classList.add("changed");
      range.textContent += " · different trim";
    }
    row.append(title, time, range);
    cuts.append(row);
  }
}

async function loadComparison() {
  try {
    const response = await fetch("/comparison.json");
    const comparison = await response.json();
    if (!response.ok) throw new Error(comparison.error);
    const { left, right } = comparison;
    showTimeline(left, right);
    showTimeline(right, left);
    const count = new Set(left.cuts.map(cut => cut.asset)).size;
    summary.textContent = comparison.same_assets && comparison.order_changed
      ? `Same ${count} source clips, different cut order.`
      : "Compare the source frames and cut order in the two exports.";
  } catch (error) {
    summary.textContent = `Preview unavailable. ${error.message}`;
  }
}

loadComparison();
