const $ = (selector) => document.querySelector(selector);
const NODE_IDS = ["adapter", "gateway", "internet", "dns", "web"];
const STATE_LABEL = { pass: "Passed", warn: "Warning", fail: "Failed", skipped: "Skipped" };

let busy = false;

function node(id) {
  return document.querySelector(`.node[data-step="${id}"]`);
}

/* ---------- path ---------- */
function setPending() {
  NODE_IDS.forEach((id) => {
    const li = node(id);
    li.dataset.status = "pending";
    li.dataset.link = "idle";
    li.querySelector(".sub").textContent = "Checking...";
  });
}

function renderPath(steps) {
  const byId = Object.fromEntries(steps.map((s) => [s.id, s]));
  const firstFail = NODE_IDS.findIndex((id) => byId[id].status === "fail");

  NODE_IDS.forEach((id, i) => {
    const step = byId[id];
    const li = node(id);
    li.dataset.status = step.status;
    li.querySelector(".sub").textContent =
      step.short || (step.status === "skipped" ? "Skipped" : "");

    // Line to the next node: green until the break, dashed red into it, grey after.
    let link = "idle";
    if (i < NODE_IDS.length - 1) {
      if (firstFail === -1) link = byId[NODE_IDS[i + 1]].status === "skipped" ? "idle" : "ok";
      else if (i + 1 < firstFail) link = "ok";
      else if (i + 1 === firstFail) link = "broken";
    }
    li.dataset.link = link;
  });
}

/* ---------- verdict ---------- */
function renderVerdict(result) {
  const v = result.verdict;
  $("#verdict").hidden = false;
  $("#verdict").dataset.severity = v.severity;
  $("#v-intro").textContent = result.intro || "";
  $("#v-title").textContent = v.title;
  $("#v-cause").textContent = v.cause;

  const note = $("#v-note");
  note.hidden = !result.note;
  note.textContent = result.note || "";

  const list = $("#v-fix-list");
  list.replaceChildren();
  $("#v-fixes").hidden = v.fixes.length === 0;

  v.fixes.forEach((fix) => {
    const li = document.createElement("li");
    li.append(fix.text);
    if (fix.command) {
      const row = document.createElement("div");
      row.className = "cmd";
      const code = document.createElement("code");
      code.textContent = fix.command;
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "copy";
      copy.textContent = "Copy command";
      copy.addEventListener("click", () => copyText(fix.command, copy));
      row.append(code, copy);
      li.append(row);
    }
    list.append(li);
  });
}

async function copyText(text, button) {
  try {
    await navigator.clipboard.writeText(text);
    button.textContent = "Copied";
  } catch {
    button.textContent = "Select and copy";
  }
  setTimeout(() => (button.textContent = "Copy command"), 1800);
}

/* ---------- step list ---------- */
function renderSteps(steps) {
  const list = $("#step-list");
  list.replaceChildren();
  steps.forEach((s) => {
    const li = document.createElement("li");

    const row = document.createElement("div");
    row.className = "row";
    const name = document.createElement("span");
    name.className = "name";
    name.append(s.title);
    const state = document.createElement("span");
    state.className = "state";
    state.dataset.s = s.status;
    state.textContent = STATE_LABEL[s.status];
    name.append(state);
    const layer = document.createElement("span");
    layer.className = "layer";
    layer.textContent = s.layer;
    row.append(name, layer);

    const detail = document.createElement("p");
    detail.className = "detail";
    detail.textContent = s.detail;

    li.append(row, detail);
    list.append(li);
  });
}

/* ---------- history ---------- */
async function loadHistory() {
  try {
    const rows = await (await fetch("/api/history")).json();
    const body = $("#history-body");
    if (!rows.length) return;
    body.replaceChildren();
    rows.forEach((r) => {
      const tr = document.createElement("tr");
      const cells = [
        r.timestamp,
        r.result,
        r.failed_step || "None",
        r.latency == null ? "No reply" : `${r.latency} ms`,
        r.packet_loss == null ? "-" : `${r.packet_loss}%`,
      ];
      cells.forEach((text, i) => {
        const td = document.createElement("td");
        td.textContent = text;
        if (i === 1) td.className = `sev-${r.severity}`;
        tr.append(td);
      });
      body.append(tr);
    });
  } catch {
    /* history is optional; ignore errors */
  }
}

/* ---------- run a diagnosis ---------- */
async function runDiagnosis(message) {
  if (busy) return;
  busy = true;
  const button = $("#run");
  button.disabled = true;
  button.textContent = "Checking...";
  setPending();

  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    if (!response.ok) throw new Error("Server error");
    const result = await response.json();
    renderPath(result.steps);
    renderVerdict(result);
    renderSteps(result.steps);
  } catch {
    NODE_IDS.forEach((id) => {
      node(id).dataset.status = "idle";
      node(id).querySelector(".sub").textContent = "Not checked yet";
    });
    renderVerdict({
      intro: "",
      verdict: {
        severity: "fail",
        title: "Cannot reach the troubleshooter server",
        cause: "The page loaded, but the backend did not answer. Start it with: python backend\\app.py",
        fixes: [],
      },
    });
  } finally {
    busy = false;
    button.disabled = false;
    button.textContent = "Run diagnosis";
    loadHistory();
  }
}

/* ---------- traceroute ---------- */
async function runTrace() {
  const button = $("#trace-btn");
  const out = $("#trace-out");
  button.disabled = true;
  button.textContent = "Tracing...";
  out.hidden = false;
  out.textContent = "Tracing the route. This can take up to a minute.";
  try {
    const data = await (await fetch("/api/traceroute")).json();
    out.textContent = data.lines.length
      ? data.lines.join("\n")
      : "The trace returned no output. Check that tracert (Windows) or traceroute (Linux) is installed.";
  } catch {
    out.textContent = "Could not reach the server.";
  } finally {
    button.disabled = false;
    button.textContent = "Trace route";
  }
}

/* ---------- events ---------- */
$("#ask").addEventListener("submit", (e) => {
  e.preventDefault();
  runDiagnosis($("#msg").value.trim());
});

document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    $("#msg").value = chip.textContent;
    runDiagnosis(chip.textContent);
  });
});

$("#trace-btn").addEventListener("click", runTrace);

loadHistory();
