const state = { runId: null, colors: new Map() };
const palette = ["#0f766e", "#2563eb", "#d97706", "#9333ea", "#dc2626", "#0891b2", "#65a30d", "#db2777"];
const byId = (id) => document.getElementById(id);

async function api(path) { const response = await fetch(path); if (!response.ok) throw new Error(await response.text()); return response.json(); }
function color(target) { if (!state.colors.has(target)) state.colors.set(target, palette[state.colors.size % palette.length]); return state.colors.get(target); }
function option(value, label = value) { const node = document.createElement("option"); node.value = value; node.textContent = label; return node; }

async function loadRuns(search = "") {
  const runs = await api(`/api/pcr/runs?search=${encodeURIComponent(search)}`);
  const select = byId("run-select"); const current = select.value; select.replaceChildren(option("", "Select a PCR run"));
  runs.forEach((run) => select.append(option(run.pcr_run_id, `${run.run_name} · ${run.run_type || "unclassified"}`)));
  if (current && [...select.options].some((item) => item.value === current)) select.value = current;
}

async function selectRun() {
  state.runId = Number(byId("run-select").value) || null;
  if (!state.runId) return;
  const run = byId("run-select").selectedOptions[0].textContent;
  byId("run-summary").textContent = run;
  const filters = await api(`/api/pcr/runs/${state.runId}/filters`);
  ["sample-filter", "target-filter"].forEach((id, index) => { const select = byId(id); const label = index ? "All targets" : "All samples"; select.replaceChildren(option("", label)); (index ? filters.targets : filters.samples).forEach((value) => select.append(option(value))); });
  await Promise.all([loadQc(), loadCurves()]);
}

async function loadSampleRuns(event) {
  event.preventDefault();
  const sampleNumber = byId("sample-search").value.trim();
  const body = byId("sample-runs-body");
  if (!sampleNumber) { body.innerHTML = '<tr><td colspan="5" class="muted">Enter an exact sample number.</td></tr>'; return; }
  body.innerHTML = '<tr><td colspan="5" class="muted">Searching…</td></tr>';
  try {
    const [runs, results] = await Promise.all([
      api(`/api/pcr/samples/${encodeURIComponent(sampleNumber)}/runs`),
      api(`/api/pcr/samples/${encodeURIComponent(sampleNumber)}/results`),
    ]);
    body.replaceChildren();
    if (!runs.length) { body.innerHTML = `<tr><td colspan="5" class="muted">No current-derivation runs for sample ${sampleNumber}.</td></tr>`; return; }
    runs.forEach((run) => {
      const row = document.createElement("tr");
      const open = document.createElement("button"); open.type = "button"; open.textContent = "Open";
      open.addEventListener("click", async () => {
        const select = byId("run-select");
        if (![...select.options].some((item) => Number(item.value) === run.pcr_run_id)) select.append(option(run.pcr_run_id, `${run.run_name} · ${run.run_type || "unclassified"}`));
        select.value = String(run.pcr_run_id); await selectRun(); window.scrollTo({ top: 0, behavior: "smooth" });
      });
      [run.run_name, run.run_started || "—", run.run_class || "—", run.run_type || "—"].forEach((value) => { const cell = document.createElement("td"); cell.textContent = value; row.append(cell); });
      const action = document.createElement("td"); action.append(open); row.append(action); body.append(row);
    });
    renderSampleResults(results);
  } catch (error) { body.innerHTML = `<tr><td colspan="5" class="muted">Search error: ${error.message}</td></tr>`; renderSampleResults([], error.message); }
}

function renderSampleResults(results, error = null) {
  const body = byId("sample-results-body"); const summary = byId("sample-results-summary"); body.replaceChildren();
  if (error) { summary.textContent = "Could not load results."; body.innerHTML = `<tr><td colspan="9" class="muted">${error}</td></tr>`; return; }
  summary.textContent = results.length ? `${results.length} interpreted target records in the current derivation.` : "No interpreted target records found.";
  if (!results.length) { body.innerHTML = '<tr><td colspan="9" class="muted">No interpreted target records found.</td></tr>'; return; }
  results.forEach((item) => {
    const peaks = item.peaks.map((peak) => `${Number(peak.temperature).toFixed(2)} °C (${Number(peak.deriv_rfu).toFixed(0)})`).join(", ") || "—";
    const row = document.createElement("tr");
    const promoted = item.is_promoted ? `yes · ${item.promoted_run_type}` : "—";
    [item.run_name, item.well, item.canonical_target, item.call, promoted, item.cq ?? "—", item.peak_temperature == null ? "—" : `${Number(item.peak_temperature).toFixed(2)} °C`, peaks, item.target_qc_valid == null ? "—" : item.target_qc_valid ? "valid" : "invalid"].forEach((value) => { const cell = document.createElement("td"); cell.textContent = value; row.append(cell); });
    body.append(row);
  });
}

async function loadQc() {
  const body = byId("qc-table-body"); if (!state.runId) return;
  const records = await api(`/api/pcr/qc?run_id=${state.runId}`); body.replaceChildren();
  if (!records.length) { body.innerHTML = '<tr><td colspan="5" class="muted">No current QC for this run.</td></tr>'; return; }
  records.forEach((item) => { const row = document.createElement("tr"); row.dataset.target = item.canonical_target; const issues = [...item.details.failures, ...item.warnings].join(", ") || "—"; row.innerHTML = `<td>${item.canonical_target}</td><td><span class="status status-${item.qc_status.toLowerCase()}">${item.qc_status}</span></td><td>${item.positive_controls}</td><td>${item.negative_controls}</td><td>${issues}</td>`; row.addEventListener("click", () => loadEvidence(item.canonical_target, row)); body.append(row); });
}

async function loadEvidence(target, row) {
  document.querySelectorAll("#qc-table-body tr").forEach((item) => item.classList.remove("selected")); row.classList.add("selected"); byId("evidence-title").textContent = target;
  const records = await api(`/api/pcr/qc/${state.runId}/${encodeURIComponent(target)}/evidence`); const content = byId("evidence-content"); content.replaceChildren();
  if (!records.length) { content.textContent = "No control evidence."; return; }
  records.forEach((item) => { const card = byId("evidence-card-template").content.cloneNode(true); card.querySelector("strong").textContent = `${item.well_role} · ${item.well}`; card.querySelector("span").textContent = `${item.sample_name || "—"} · ${item.source_target}`; const values = [["Call", item.call], ["Cq", item.cq ?? "—"], ["Peak", item.peak_temperature ? `${item.peak_temperature} °C` : "—"], ["Error", item.error || "—"]]; card.querySelector("dl").innerHTML = values.map(([key, value]) => `<dt>${key}</dt><dd>${value}</dd>`).join(""); content.append(card); });
}

async function loadReviewQueue() {
  const body = byId("review-table-body"); body.innerHTML = '<tr><td colspan="4" class="muted">Loading review queue…</td></tr>';
  try {
    const items = await api("/api/pcr/review-queue"); body.replaceChildren();
    if (!items.length) { body.innerHTML = '<tr><td colspan="4" class="muted">No invalid or review-needed items.</td></tr>'; return; }
    items.forEach((item) => { const row = document.createElement("tr"); const details = item.details_json || "—"; [item.item_type, item.item_id, item.reason, details].forEach((value) => { const cell = document.createElement("td"); cell.textContent = value; row.append(cell); }); body.append(row); });
  } catch (error) { body.innerHTML = `<tr><td colspan="4" class="muted">Could not load review queue: ${error.message}</td></tr>`; }
}

async function loadCurves() {
  if (!state.runId) return; const kind = byId("curve-kind").value; const sample = byId("sample-filter").value; const target = byId("target-filter").value;
  const query = new URLSearchParams({ kind }); if (sample) query.set("sample", sample); if (target) query.set("target", target);
  const data = await api(`/api/pcr/runs/${state.runId}/curves?${query}`); drawChart(data.series, kind);
}

function drawChart(series, kind) {
  const svg = byId("curve-chart"); const empty = byId("chart-empty"); const legend = byId("chart-legend"); svg.replaceChildren(); legend.replaceChildren(); if (!series.length) { empty.hidden = false; return; } empty.hidden = true;
  const points = series.flatMap((item) => item.points); const x = points.map((item) => item[0]); const y = points.map((item) => item[1]); const [minX, maxX] = [Math.min(...x), Math.max(...x)]; const [minY, maxY] = [Math.min(...y), Math.max(...y)]; const left = 66, right = 24, top = 28, bottom = 48, width = 960 - left - right, height = 460 - top - bottom; const sx = (value) => left + ((value - minX) / (maxX - minX || 1)) * width; const sy = (value) => top + height - ((value - minY) / (maxY - minY || 1)) * height;
  const element = (name, attrs) => { const node = document.createElementNS("http://www.w3.org/2000/svg", name); Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value)); svg.append(node); return node; };
  for (let step = 0; step <= 4; step += 1) { const yy = top + (height / 4) * step; element("line", { x1:left, y1:yy, x2:left + width, y2:yy, stroke:"#d9e1e8" }); element("text", { x:left - 8, y:yy + 4, "text-anchor":"end", fill:"#64748b", "font-size":"11" }).textContent = (maxY - ((maxY - minY) * step / 4)).toFixed(0); }
  element("line", { x1:left, y1:top + height, x2:left + width, y2:top + height, stroke:"#64748b" }); element("line", { x1:left, y1:top, x2:left, y2:top + height, stroke:"#64748b" });
  element("text", { x:480, y:448, "text-anchor":"middle", fill:"#475569", "font-size":"13" }).textContent = kind === "amplification" ? "PCR cycle" : "Temperature (°C)";
  element("text", { x:18, y:220, transform:"rotate(-90 18 220)", "text-anchor":"middle", fill:"#475569", "font-size":"13" }).textContent = kind === "amplification" ? "RFU" : "Derivative RFU";
  const targets = [...new Set(series.map((item) => item.target))]; targets.forEach((targetName) => { const item = document.createElement("span"); item.className = "legend-item"; item.innerHTML = `<i class="legend-dot" style="background:${color(targetName)}"></i>${targetName}`; legend.append(item); });
  series.forEach((item) => { const path = item.points.map(([px, py], index) => `${index ? "L" : "M"}${sx(px)},${sy(py)}`).join(" "); const node = element("path", { d:path, fill:"none", stroke:color(item.target), "stroke-width":"1.5", opacity:"0.72" }); node.append(document.createElementNS("http://www.w3.org/2000/svg", "title")).textContent = `${item.well} · ${item.sample || "no sample"} · ${item.target}`; });
}

document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => { document.querySelectorAll(".tab,.workspace").forEach((item) => item.classList.remove("active")); tab.classList.add("active"); byId(tab.dataset.panel).classList.add("active"); if (tab.dataset.panel === "review") loadReviewQueue(); }));
byId("run-search").addEventListener("input", (event) => loadRuns(event.target.value)); byId("run-select").addEventListener("change", selectRun); ["curve-kind", "sample-filter", "target-filter"].forEach((id) => byId(id).addEventListener("change", loadCurves));
byId("sample-search-form").addEventListener("submit", loadSampleRuns);
byId("refresh-review").addEventListener("click", loadReviewQueue);
loadRuns().then(() => { byId("connection-status").textContent = "Read-only API connected"; }).catch((error) => { byId("connection-status").textContent = `Connection error: ${error.message}`; });
